import json

from guanlan_v2.workflow.store import WorkflowStore
from guanlan_v2.workflow.migrations import WORKFLOW_SCHEMA_VERSION


def _legacy_graph():
    return {
        "nodes": [
            {"id": "f", "type": "formula"},
            {"id": "fe", "type": "feature"},
            {"id": "a", "type": "analysis"},
        ],
        "edges": [
            {"from": ["f", "out"], "to": ["fe", "feat"]},
            {"from": ["fe", "fe"], "to": ["a", "factor"]},
        ],
    }


def test_save_migrates_known_legacy_edge_and_persists_version(tmp_path):
    store = WorkflowStore(tmp_path)
    rec = store.save("legacy", _legacy_graph())

    assert rec["schema_version"] == WORKFLOW_SCHEMA_VERSION
    assert rec["migration"]["changed"] is True
    assert {"from": ["f", "out"], "to": ["a", "factor"]} in rec["graph"]["edges"]
    on_disk = json.loads((tmp_path / f"{rec['id']}.json").read_text(encoding="utf-8"))
    assert on_disk["schema_version"] == WORKFLOW_SCHEMA_VERSION
    assert not list(tmp_path.glob("*.tmp"))


def test_list_exposes_legacy_version_without_mutating_old_record(tmp_path):
    old = {
        "id": "wlegacy", "name": "old", "ts": 1,
        "graph": {"nodes": [], "edges": []},
    }
    (tmp_path / "wlegacy.json").write_text(json.dumps(old), encoding="utf-8")

    items = WorkflowStore(tmp_path).list()

    assert items[0]["schema_version"] == 1
    assert items[0]["migration"] == {}
