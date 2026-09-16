"""Legacy intent preservation and evidence-gated local recovery writes."""
from copy import deepcopy
import hashlib
import json

import pytest

from guanlan_v2.workflow.api import validate_graph
from guanlan_v2.workflow.migrations import legacy_recovery_info, migrate_graph
from scripts.recover_legacy_workflows import recover_root


def _board():
    return {
        "nodes": [
            {"id": "m", "type": "model", "params": {"model_id": "m_old", "decision": "REJECT", "features": ["rank(close)"]}},
            {"id": "v", "type": "validate", "params": {"model_id": "m_old", "tier": "strict", "purge": 5, "embargo": 5}},
            {"id": "f", "type": "factorlib", "params": {"expr": "rank(close)"}},
        ],
        "edges": [{"from": ["m", "out"], "to": ["v", "model"]}],
    }


def _safe_graph():
    return {
        "nodes": [
            {"id": "s", "type": "source", "params": {"universe": "csi800", "start": "2023-01-01", "end": "2026-06-18"}},
            {"id": "f", "type": "formula", "params": {"expr": "rank(close)"}},
            {"id": "fe", "type": "feature", "params": {"tag": "IC"}},
            {"id": "a", "type": "analysis", "params": {}},
        ],
        "edges": [{"from": ["s", "data"], "to": ["fe", "src"]},
                  {"from": ["f", "out"], "to": ["fe", "feat"]},
                  {"from": ["fe", "fe"], "to": ["a", "factor"]}],
    }


def _save(root, wid, graph):
    record = {"id": wid, "name": "original", "ts": 42, "graph": graph,
              "scope": "workspace", "workspace_id": "rw_general", "user_extra": {"do_not_remove": True}}
    raw = json.dumps(record, ensure_ascii=False, indent=2).encode("utf-8")
    (root / f"{wid}.json").write_bytes(raw)
    return raw


def test_model_board_is_classified_not_rebuilt_or_relabelled():
    graph = _board()
    original = deepcopy(graph)
    migrated, report = migrate_graph(graph)
    recovery = report["legacy_recovery"]
    assert recovery["classification"] == "historical_model_board"
    assert not recovery["auto_repairable"] and not recovery["execution_supported"]
    assert recovery["historical_decisions"] == [{"node_id": "m", "decision": "REJECT"}]
    assert {ref["registry_resolution"] for ref in recovery["model_references"]} == {"not_checked"}
    assert not report["changed"] and not report["runnable_candidate"]
    assert migrated == graph
    assert graph == original


def test_conflicting_model_validation_ids_are_not_auto_selected():
    graph = _board()
    graph["nodes"][1]["params"]["model_id"] = "m_different"
    info = legacy_recovery_info(graph)
    assert info["classification"] == "legacy_rebuild_required"
    assert "model_validation_reference_mismatch" in {b["code"] for b in info["blockers"]}


def test_mixed_graph_cannot_masquerade_as_a_historical_board():
    graph = _board()
    graph["edges"].append({"from": ["f", "out"], "to": ["m", "fe"]})
    assert legacy_recovery_info(graph)["classification"] == "legacy_rebuild_required"


def test_screen_does_not_become_data_screen_without_explicit_spec():
    graph = {"nodes": [{"id": "screen", "type": "screen", "params": {"method": "screen/run verification"}}], "edges": []}
    info = legacy_recovery_info(graph)
    assert info["classification"] == "legacy_rebuild_required"
    assert "legacy_screen_requires_explicit_spec" in {b["code"] for b in info["blockers"]}
    assert migrate_graph(graph)[0]["nodes"] == graph["nodes"]


def test_legacy_mixed_board_preserves_even_recognizable_branch_and_schema():
    graph = _safe_graph()
    graph["nodes"].append({"id": "old", "type": "screen", "params": {"method": "manual review"}})
    graph["schema_version"] = 1
    graph["custom_note"] = "Do not silently reinterpret only half of this graph"
    migrated, report = migrate_graph(graph)
    assert migrated == graph
    assert not report["changed"] and report["changes"] == []
    assert report["legacy_recovery"]["original_graph_preserved"]


@pytest.mark.parametrize("graph", [None, {}, {"nodes": []}, _safe_graph()])
def test_nonlegacy_graph_not_classified_as_legacy(graph):
    assert legacy_recovery_info(graph) is None


def test_dry_run_records_backup_evidence_and_changes_nothing(tmp_path):
    raw = _save(tmp_path, "wboard", _board())
    backup = tmp_path / "_migration_backups" / "20260829T050524Z"
    backup.mkdir(parents=True)
    (backup / "wboard.json").write_bytes(raw)
    initial_files = sorted(str(p) for p in tmp_path.rglob("*"))
    result = recover_root(tmp_path)
    assert result["source_files_unchanged"] and result["changed_records"] == 0
    assert result["manual_rebuild_required"] == 1 and result["safe_repair_candidates"] == 0
    assert result["items"][0]["backups"][0]["graph_equal_to_current"]
    assert initial_files == sorted(str(p) for p in tmp_path.rglob("*"))
    assert (tmp_path / "wboard.json").read_bytes() == raw


def test_legacy_apply_still_preserves_original_bytes_and_writes_no_metadata(tmp_path):
    raw = _save(tmp_path, "wboard", _board())
    dry = recover_root(tmp_path)
    result = recover_root(tmp_path, apply=True, expected_hashes=dry["source_file_sha256"])
    assert result["changed_records"] == 0 and result["source_files_unchanged"]
    assert (tmp_path / "wboard.json").read_bytes() == raw
    assert not (tmp_path / "_recovery_backups").exists()


def test_safe_repair_requires_manifest_backups_verbatim_and_is_idempotent(tmp_path):
    raw = _save(tmp_path, "wsafe", _safe_graph())
    dry = recover_root(tmp_path)
    assert dry["safe_repair_candidates"] == 1
    assert not dry["items"][0]["before_ok"] and dry["items"][0]["after_ok"]
    with pytest.raises(ValueError, match="prior dry-run"):
        recover_root(tmp_path, apply=True)
    done = recover_root(tmp_path, apply=True, expected_hashes=dry["source_file_sha256"])
    assert done["changed_records"] == 1
    backup = tmp_path / "_recovery_backups" / hashlib.sha256(raw).hexdigest() / "wsafe.json"
    assert backup.read_bytes() == raw
    repaired = json.loads((tmp_path / "wsafe.json").read_bytes())
    assert validate_graph(repaired["graph"], require_complete=True)[0]
    assert repaired["user_extra"] == {"do_not_remove": True}
    assert repaired["ts"] == 42 and repaired["workspace_id"] == "rw_general"
    assert next(n for n in repaired["graph"]["nodes"] if n["id"] == "a")["params"]["context_source_id"] == "s"
    after_bytes = (tmp_path / "wsafe.json").read_bytes()
    all_files = sorted(str(p) for p in tmp_path.rglob("*"))
    again = recover_root(tmp_path, apply=True, expected_hashes=dry["source_file_sha256"])
    assert again["changed_records"] == 0 and again["source_files_unchanged"]
    assert (tmp_path / "wsafe.json").read_bytes() == after_bytes
    assert all_files == sorted(str(p) for p in tmp_path.rglob("*"))


def test_stale_manifest_preflight_prevents_any_graph_write(tmp_path):
    first = _save(tmp_path, "wa", _safe_graph())
    _save(tmp_path, "wz", _safe_graph())
    dry = recover_root(tmp_path)
    path = tmp_path / "wz.json"
    path.write_bytes(path.read_bytes() + b"\n")
    with pytest.raises(ValueError, match="Source changed"):
        recover_root(tmp_path, apply=True, expected_hashes=dry["source_file_sha256"])
    assert (tmp_path / "wa.json").read_bytes() == first
    assert not (tmp_path / "_recovery_backups").exists()


def test_invalid_record_id_cannot_escape_recovery_root(tmp_path):
    with pytest.raises(ValueError, match="Invalid workflow ID"):
        recover_root(tmp_path, ids=["../outside"])


def test_model_recipe_and_snapshot_not_claimed_to_be_original_estimator(tmp_path):
    workflows = tmp_path / "workflows"
    workflows.mkdir()
    _save(workflows, "wboard", _board())
    model_dir = tmp_path / "models" / "m_old"
    model_dir.mkdir(parents=True)
    (model_dir / "meta.json").write_text(json.dumps({"recipe": {"features": ["rank(close)"]}}), encoding="utf-8")
    row = recover_root(workflows, model_root=model_dir.parent)["items"][0]["model_evidence"][0]
    assert row["status"] == "metadata_present" and row["recipe_available"]
    assert not row["estimator_artifact_declared"]
    assert "do not prove" in row["warning"]
