import threading
from fastapi import FastAPI
from fastapi.testclient import TestClient
from guanlan_v2.workflow.api import build_workflow_router
from guanlan_v2.workflow.run_store import WorkflowRunStore
from guanlan_v2.workflow import executor, strategy_replay


class Workspaces:
    def require(self, owner, active_only=True):
        if owner != "rw_general":
            raise ValueError("workspace not found")


def client(tmp_path, workspaces=None):
    app = FastAPI()
    app.include_router(build_workflow_router(workspace_store=workspaces,
        run_store=WorkflowRunStore(tmp_path / "runs")))
    return TestClient(app)


def test_standalone_endpoints_validate_owner_before_execution(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(strategy_replay, "run_history_screen", lambda p, c, owner=None: calls.append(owner) or {"ok": True})
    monkeypatch.setattr(strategy_replay, "run_daily_backtest", lambda h, p, owner=None: calls.append(owner) or {"ok": True})
    c = client(tmp_path, Workspaces())
    for route in ("/workflow/history-screen", "/backtest/daily-screen"):
        assert not c.post(route, json={"workspace_id": "../rw_general"}).json()["ok"]
        assert not c.post(route, json={"workspace_id": "rw_aaaaaaaaaaaa"}).json()["ok"]
        assert c.post(route, json={"workspace_id": "rw_general"}).json()["ok"]
    assert calls == ["rw_general", "rw_general"]


def test_durable_worker_injects_research_workspace_private_context(tmp_path, monkeypatch):
    done = threading.Event()
    seen = {}
    def run(graph, overrides=None, on_node=None, allow_diagnostic_result=False):
        seen.update(overrides)
        done.set()
        return {"ok": True, "terminal": {"kind": "daily_backtest", "payload": {}}, "node_errors": []}
    monkeypatch.setattr(executor, "run_graph", run)
    c = client(tmp_path, Workspaces())
    graph = {"nodes": [{"id": "s", "type": "source", "params": {"universe": "csi800"}},
        {"id": "h", "type": "history_screen", "params": {}},
        {"id": "b", "type": "daily_backtest", "params": {}}],
        "edges": [{"from": ["s", "data"], "to": ["h", "src"]}, {"from": ["h", "history"], "to": ["b", "history"]}]}
    response = c.post("/workflow/run/start", json={"graph": graph, "workspace_id": "rw_general"}).json()
    assert response["ok"]
    assert done.wait(2)
    assert seen["_workspace_id"] == "rw_general"
    assert seen["_workflow_run_id"].startswith("wr_")


def test_executor_rejects_ignored_universe_override():
    import pytest
    with pytest.raises(executor.NodeError, match="股票池覆盖"):
        executor._exec_history_screen({"src": {"universe": "csi800", "requested_universe": "csi800"}}, {},
                                      {"universe": "csi300"})
