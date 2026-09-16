# -*- coding: utf-8 -*-
"""Workflow run endpoints (executor stubs, no market engine)."""
import threading
import time

import pytest
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


def _wait_status(client, run_id, wanted, params=None, node_id=None):
    deadline = time.time() + 3
    last = None
    while time.time() < deadline:
        last = client.get(f"/workflow/run/status/{run_id}", params=params or {}).json()
        if last.get("status") in wanted and (node_id is None or node_id in last.get("node_state", {})):
            return last
        time.sleep(0.01)
    raise AssertionError(last)


def test_run_empty_graph_honest():
    j = _client().post("/workflow/run", json={"graph": {}}).json()
    assert j["ok"] is False and j["reason"]


def test_run_ok_passes_overrides(monkeypatch):
    seen = {}

    def fake(graph, overrides=None, on_node=None):
        seen.update(overrides or {})
        return {"ok": True, "metrics": {"rank_ic": 0.02}, "terminal": {"kind": "analysis"}}

    monkeypatch.setattr(wex, "run_graph", fake)
    j = _client().post("/workflow/run", json={
        "graph": {"nodes": [{"id": "f", "type": "formula", "params": {"expr": "rank(close)"}}],
                  "edges": []},
        "universe": "csi300_active", "freq": "month", "oos_frac": 0.3}).json()
    assert j["ok"] is True and seen["universe"] == "csi300_active" and seen["oos_frac"] == 0.3


def test_run_executor_exception_wrapped(monkeypatch):
    def boom(graph, overrides=None, on_node=None):
        raise RuntimeError("x")

    monkeypatch.setattr(wex, "run_graph", boom)
    j = _client().post("/workflow/run", json={
        "graph": {"nodes": [{"id": "f", "type": "formula", "params": {}}], "edges": []}}).json()
    assert j["ok"] is False and "RuntimeError" in j["reason"]


def test_durable_run_survives_request_and_checkpoints_nodes(tmp_path, monkeypatch):
    store = WorkflowRunStore(tmp_path / "runs")
    release = threading.Event()

    def fake(graph, overrides=None, on_node=None):
        on_node("f", "formula", "running")
        release.wait(timeout=2)
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
    running = _wait_status(c, run_id, {"running"}, node_id="f")
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

    def fake(graph, overrides=None, on_node=None):
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


def _data_screen_graph(source_params=None):
    return {
        "nodes": [
            {"id": "src", "type": "source",
             "params": source_params or {"universe": "all_a"}},
            {"id": "screen", "type": "data_screen",
             "params": {"preset": "ma_trend"}},
        ],
        "edges": [{"from": ["src", "data"], "to": ["screen", "src"]}],
    }


def test_run_endpoints_enforce_data_screen_wiring_but_preserve_legacy_graphs(
        tmp_path, monkeypatch):
    calls = []

    def fake(graph, overrides=None, on_node=None):
        calls.append(graph)
        return {"ok": True, "terminal": {"kind": "data_screen", "payload": {}}}

    monkeypatch.setattr(wex, "run_graph", fake)
    client = _client(run_store=WorkflowRunStore(tmp_path / "runs"))

    # Historical endpoint behaviour remains permissive for ordinary saved
    # canvases; the new authority is scoped to data_screen/all_a graphs.
    legacy = client.post("/workflow/run", json={"graph": GRAPH}).json()
    assert legacy["ok"] is True and len(calls) == 1

    bare = {"nodes": [{"id": "screen", "type": "data_screen", "params": {}}],
            "edges": []}
    rejected = client.post("/workflow/run", json={"graph": bare}).json()
    assert rejected["ok"] is False and "source.data" in rejected["reason"]
    assert len(calls) == 1

    explicit_codes = client.post("/workflow/run", json={
        "graph": _data_screen_graph({
            "universe": "自动", "codes": ["SH600001", "SZ000002"],
        }),
    }).json()
    assert explicit_codes["ok"] is True and len(calls) == 2

    invalid_all_a = {
        "nodes": [
            {"id": "src", "type": "source", "params": {"universe": "all_a"}},
            {"id": "feature", "type": "feature", "params": {}},
            {"id": "formula", "type": "formula", "params": {"expr": "close"}},
            {"id": "analysis", "type": "analysis", "params": {}},
        ],
        "edges": [
            {"from": ["src", "data"], "to": ["feature", "src"]},
            {"from": ["formula", "out"], "to": ["analysis", "factor"]},
        ],
    }
    rejected = client.post("/workflow/run/start", json={
        "graph": invalid_all_a,
    }).json()
    assert rejected["ok"] is False and "data_screen.src" in rejected["reason"]


def test_run_rejects_universe_override_that_cannot_truthfully_reach_screen(monkeypatch):
    monkeypatch.setattr(wex, "run_graph", lambda *args, **kwargs: pytest.fail(
        "executor must not run after an invalid universe override"
    ))
    client = _client()
    for path in ("/workflow/run", "/workflow/run/start"):
        rejected = client.post(path, json={
            "graph": _data_screen_graph(), "universe": "all_a",
        }).json()
        assert rejected["ok"] is False
        assert "source" in rejected["reason"] and "universe" in rejected["reason"]

    ordinary_all_a = client.post("/workflow/run", json={
        "graph": GRAPH, "universe": "all_a",
    }).json()
    assert ordinary_all_a["ok"] is False and "普通因子/回测" in ordinary_all_a["reason"]


def test_durable_parallel_terminals_are_persisted_intact(tmp_path, monkeypatch):
    store = WorkflowRunStore(tmp_path / "runs")
    outcome = {
        "ok": True,
        "terminal": {"kind": "analysis", "node_id": "analysis",
                     "payload": {"ic": {"rank_ic_mean": 0.02}}},
        "all_terminals": [
            {"kind": "data_screen", "node_id": "screen",
             "payload": {"run_id": "ds_parallel", "as_of": "2026-08-21",
                         "candidates": [{"code": "SH600001"}]}},
            {"kind": "analysis", "node_id": "analysis",
             "payload": {"ic": {"rank_ic_mean": 0.02}}},
        ],
        "warnings": [], "node_errors": [],
    }
    monkeypatch.setattr(wex, "run_graph", lambda *args, **kwargs: outcome)
    client = _client(run_store=store)
    run_id = "wr_dddddddddddddddddddddddd"
    started = client.post("/workflow/run/start", json={
        "run_id": run_id, "graph": GRAPH,
    }).json()
    assert started["ok"] is True

    done = _wait_status(client, run_id, {"completed"})
    assert done["result"]["terminal"]["kind"] == "analysis"
    assert done["result"]["all_terminals"][0]["payload"]["run_id"] == "ds_parallel"
    restored = WorkflowRunStore(tmp_path / "runs").get(run_id)
    assert restored["result"]["all_terminals"][0]["payload"]["candidates"] == [
        {"code": "SH600001"},
    ]
