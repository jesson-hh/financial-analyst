"""Durable discovery is namespace-safe and never claims resumable checkpoints."""
import json

import pytest

from guanlan_v2.workflow.run_store import WorkflowRunStore


RID = "wr_1234567890abcdef12345678"
WS = "rw_0123456789ab"
GRAPH = {
    "nodes": [{"id": "source", "type": "source"},
              {"id": "publish", "type": "publish"}],
    "edges": [],
}


def _create(store, **kwargs):
    return store.create(graph=GRAPH, overrides={}, run_id=RID, **kwargs)


@pytest.mark.parametrize("metadata", [
    {"scope": "workspace", "workspace_id": None},
    {"scope": "workspace"},
    {"scope": "global", "workspace_id": WS},
    {"scope": "unknown", "workspace_id": None},
    {"scope": "workspace", "workspace_id": "../../private"},
    {"scope": "workspace", "workspace_id": {"id": WS}},
])
def test_malformed_owner_metadata_never_leaks_into_any_namespace(tmp_path, metadata):
    store = WorkflowRunStore(tmp_path)
    record = _create(store)
    record.pop("scope", None)
    record.pop("workspace_id", None)
    record.update(metadata)
    (tmp_path / f"{RID}.json").write_text(json.dumps(record), encoding="utf-8")

    assert store.get(RID) is None
    assert store.get(RID, workspace_id=WS) is None
    assert store.list_runs() == []
    assert store.list_runs(workspace_id=WS) == []


def test_legacy_global_without_namespace_metadata_is_still_discoverable(tmp_path):
    store = WorkflowRunStore(tmp_path)
    record = _create(store)
    record.pop("scope")
    record.pop("workspace_id")
    (tmp_path / f"{RID}.json").write_text(json.dumps(record), encoding="utf-8")

    assert store.get(RID)["id"] == RID
    assert store.get(RID, workspace_id=WS) is None


def test_reconnect_is_not_resume_and_public_contract_does_not_expose_worker(tmp_path):
    store = WorkflowRunStore(tmp_path)
    record = _create(store, workspace_id=WS)
    public = store.public(record)

    assert "worker_instance" not in public
    assert public["recovery"]["mode"] == "reconnect"
    assert public["recovery"]["can_reconnect"] is True
    assert public["recovery"]["can_resume"] is False
    assert public["recovery"]["requires_new_run"] is False
    assert store.list_runs(workspace_id=WS)[0]["recovery"] == public["recovery"]


def test_restart_preserves_graph_and_done_nodes_but_interrupts_inflight_node(tmp_path):
    original = WorkflowRunStore(tmp_path)
    _create(original)
    original.update(RID, status="running")
    original.update_node(RID, "source", "source", "done")
    original.update_node(RID, "publish", "publish", "running")
    restarted = WorkflowRunStore(tmp_path)

    row = restarted.list_runs()[0]
    record = restarted.get(RID)

    assert row["status"] == "failed"  # backward-compatible terminal spelling
    assert row["failure_kind"] == "orphaned"
    assert row["interrupted"] is True
    assert record["node_state"]["source"]["state"] == "done"
    assert record["node_state"]["publish"]["state"] == "interrupted"
    assert record["graph"] == GRAPH and record["result"] is None
    assert row["recovery"]["mode"] == "restore_graph"
    assert row["recovery"]["can_resume"] is False
    assert row["recovery"]["can_reconnect"] is False
    assert row["recovery"]["requires_new_run"] is True
    assert "副作用" in row["recovery"]["reason"]
    first = (tmp_path / f"{RID}.json").read_bytes()
    restarted.list_runs()
    restarted.get(RID)
    assert (tmp_path / f"{RID}.json").read_bytes() == first


def test_completed_result_and_graph_are_available_after_reopening_store(tmp_path):
    original = WorkflowRunStore(tmp_path)
    _create(original)
    outcome = {"ok": True, "terminal": {"kind": "analysis", "payload": {"ic": .03}},
               "all_terminals": [{"kind": "report", "payload": {"asset_id": "audit-1"}}]}
    original.update(RID, status="completed", result=outcome)
    restarted = WorkflowRunStore(tmp_path)

    summary = restarted.list_runs()[0]
    restored = restarted.public(restarted.get(RID))
    assert "result" not in summary and "graph" not in summary
    assert restored["result"] == outcome
    assert restored["graph"] == GRAPH
    assert summary["recovery"]["mode"] == "read_result"
    assert summary["recovery"]["can_read_result"] is True
    assert summary["recovery"]["can_restore_graph"] is True
    assert summary["recovery"]["can_resume"] is False


def test_other_workspace_discovery_does_not_mark_private_worker_orphaned(tmp_path):
    original = WorkflowRunStore(tmp_path)
    _create(original, workspace_id=WS)
    restarted = WorkflowRunStore(tmp_path)
    before = (tmp_path / f"{RID}.json").read_bytes()

    assert restarted.list_runs() == []
    assert restarted.get(RID) is None
    assert (tmp_path / f"{RID}.json").read_bytes() == before


@pytest.mark.parametrize("stored_id", ["wr_aaaaaaaaaaaaaaaaaaaaaaaa", None, "../private"])
def test_corrupt_record_identity_is_not_discoverable(tmp_path, stored_id):
    store = WorkflowRunStore(tmp_path)
    record = _create(store)
    record["id"] = stored_id
    (tmp_path / f"{RID}.json").write_text(json.dumps(record), encoding="utf-8")

    assert store.get(RID) is None
    assert store.list_runs() == []


def test_bad_timestamp_does_not_hide_other_history(tmp_path):
    store = WorkflowRunStore(tmp_path)
    record = _create(store)
    record["updated_at"] = "corrupt-runtime-date"
    (tmp_path / f"{RID}.json").write_text(json.dumps(record), encoding="utf-8")

    assert [row["id"] for row in store.list_runs()] == [RID]


def test_failed_atomic_replace_preserves_previous_record_and_cleans_temp(tmp_path, monkeypatch):
    store = WorkflowRunStore(tmp_path)
    _create(store)
    previous = (tmp_path / f"{RID}.json").read_bytes()

    def fail_replace(*_args, **_kwargs):
        raise OSError("simulated atomic replace failure")

    monkeypatch.setattr("pathlib.Path.replace", fail_replace)
    with pytest.raises(OSError, match="simulated atomic"):
        store.update(RID, status="running")
    assert (tmp_path / f"{RID}.json").read_bytes() == previous
    assert sorted(path.name for path in tmp_path.iterdir()) == [f"{RID}.json"]
