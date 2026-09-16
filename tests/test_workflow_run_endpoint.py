# -*- coding: utf-8 -*-
"""Workflow run endpoints (executor stubs, no market engine)."""
import threading
import time

from fastapi import FastAPI
from fastapi.testclient import TestClient

import guanlan_v2.workflow.api as wapi
import guanlan_v2.workflow.executor as wex
from guanlan_v2.workflow.run_store import WorkflowRunStore


def _client(run_store=None, workspace_store=None):
    app = FastAPI()
    app.include_router(wapi.build_workflow_router(
        run_store=run_store, workspace_store=workspace_store,
    ))
    return TestClient(app)


GRAPH = {
    "nodes": [{"id": "f", "type": "formula", "params": {"expr": "rank(close)"}}],
    "edges": [],
}


def _wait_status(client, run_id, wanted, params=None):
    deadline = time.time() + 3
    last = None
    while time.time() < deadline:
        last = client.get(f"/workflow/run/status/{run_id}", params=params or {}).json()
        if last.get("status") in wanted:
            return last
        time.sleep(0.01)
    raise AssertionError(last)


def test_run_empty_graph_honest():
    j = _client().post("/workflow/run", json={"graph": {}}).json()
    assert j["ok"] is False and j["reason"]


def test_run_ok_passes_overrides(monkeypatch):
    seen = {}

    def fake(graph, overrides=None, on_node=None, allow_diagnostic_result=False):
        assert allow_diagnostic_result is True
        seen.update(overrides or {})
        return {"ok": True, "metrics": {"rank_ic": 0.02}, "terminal": {"kind": "analysis"}}

    monkeypatch.setattr(wex, "run_graph", fake)
    j = _client().post("/workflow/run", json={
        "graph": {"nodes": [{"id": "f", "type": "formula", "params": {"expr": "rank(close)"}}],
                  "edges": []},
        "universe": "csi300_active", "freq": "month", "oos_frac": 0.3}).json()
    assert j["ok"] is True and seen["universe"] == "csi300_active" and seen["oos_frac"] == 0.3


def test_run_executor_exception_wrapped(monkeypatch):
    def boom(graph, overrides=None, on_node=None, allow_diagnostic_result=False):
        raise RuntimeError("x")

    monkeypatch.setattr(wex, "run_graph", boom)
    j = _client().post("/workflow/run", json={
        "graph": {"nodes": [{"id": "f", "type": "formula", "params": {}}], "edges": []}}).json()
    assert j["ok"] is False and "RuntimeError" in j["reason"]


def test_durable_run_survives_request_and_checkpoints_nodes(tmp_path, monkeypatch):
    store = WorkflowRunStore(tmp_path / "runs")
    release = threading.Event()
    node_started = threading.Event()

    def fake(graph, overrides=None, on_node=None, allow_diagnostic_result=False):
        assert allow_diagnostic_result is True
        on_node("f", "formula", "running")
        # The durable runner publishes run=running before its first node.
        # Synchronize on the persisted checkpoint, not the earlier run status.
        node_started.set()
        release.wait(timeout=5)
        on_node("f", "formula", "done")
        return {
            "ok": True,
            "terminal": {"kind": "analysis", "node_id": "f",
                         "payload": {"ic": {"rank_ic_mean": 0.03}}},
            "warnings": [],
            "node_errors": [],
        }

    monkeypatch.setattr(wex, "run_graph", fake)
    c = _client(run_store=store)
    run_id = "wr_1234567890abcdef12345678"
    started = c.post("/workflow/run/start", json={
        "run_id": run_id, "name": "可恢复回测", "graph": GRAPH,
        "universe": "csi300_active",
    }).json()
    assert started["ok"] is True
    assert node_started.wait(timeout=3), "executor did not persist its first node checkpoint"
    running = _wait_status(c, run_id, {"running"})
    assert running["node_state"]["f"]["state"] == "running"
    assert running["overrides"]["universe"] == "csi300_active"

    release.set()
    done = _wait_status(c, run_id, {"completed"})
    assert done["result"]["terminal"]["payload"]["ic"]["rank_ic_mean"] == 0.03
    assert done["node_state"]["f"]["state"] == "done"
    assert (tmp_path / "runs" / f"{run_id}.json").exists()


def test_durable_start_is_idempotent(tmp_path, monkeypatch):
    store = WorkflowRunStore(tmp_path / "runs")
    calls = []
    release = threading.Event()

    def fake(graph, overrides=None, on_node=None, allow_diagnostic_result=False):
        calls.append(1)
        release.wait(timeout=2)
        return {"ok": True, "terminal": {"kind": "analysis", "payload": {"ic": {}}}}

    monkeypatch.setattr(wex, "run_graph", fake)
    c = _client(run_store=store)
    payload = {"run_id": "wr_aaaaaaaaaaaaaaaaaaaaaaaa", "graph": GRAPH}
    assert c.post("/workflow/run/start", json=payload).json()["ok"]
    assert c.post("/workflow/run/start", json=payload).json()["ok"]
    deadline = time.time() + 1
    while not calls and time.time() < deadline:
        time.sleep(0.01)
    assert len(calls) == 1
    release.set()


def test_durable_run_is_workspace_scoped(tmp_path, monkeypatch):
    class Workspaces:
        def require(self, workspace_id, *, active_only=True):
            assert workspace_id == "rw_0123456789ab"
            return {"id": workspace_id}

    monkeypatch.setattr(wex, "run_graph", lambda *a, **k: {
        "ok": True, "terminal": {"kind": "analysis", "payload": {"ic": {}}},
    })
    c = _client(
        run_store=WorkflowRunStore(tmp_path / "runs"),
        workspace_store=Workspaces(),
    )
    run_id = "wr_bbbbbbbbbbbbbbbbbbbbbbbb"
    assert c.post("/workflow/run/start", json={
        "run_id": run_id, "graph": GRAPH, "workspace_id": "rw_0123456789ab",
    }).json()["ok"]
    assert c.get(f"/workflow/run/status/{run_id}").json()["ok"] is False
    assert c.get(f"/workflow/run/status/{run_id}", params={
        "workspace_id": "rw_abcdefabcdef",
    }).json()["ok"] is False
    own = _wait_status(c, run_id, {"completed"}, {"workspace_id": "rw_0123456789ab"})
    assert own["scope"] == "workspace"


def test_orphaned_active_run_fails_honestly_after_restart(tmp_path):
    root = tmp_path / "runs"
    first = WorkflowRunStore(root)
    rec = first.create(graph=GRAPH, overrides={}, run_id="wr_cccccccccccccccccccccccc")
    first.update(rec["id"], status="running")
    second = WorkflowRunStore(root)
    restored = second.get(rec["id"])
    assert restored["status"] == "failed"
    assert "服务已重启" in restored["reason"]


def test_norm_codes_bj_920_segment():
    """#5 自选股池归一:北交所 920 新号段裸六位 → BJ 前缀;4/8 照旧;其余不动。"""
    assert wapi._norm_codes(["920807"]) == ["BJ920807"]
    assert wapi._norm_codes(["920807.BJ", "bj920807"]) == ["BJ920807"]  # 去重保序
    assert wapi._norm_codes(["830799", "430047"]) == ["BJ830799", "BJ430047"]
    assert wapi._norm_codes(["600519", "000630"]) == ["SH600519", "SZ000630"]
