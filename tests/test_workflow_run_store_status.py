# -*- coding: utf-8 -*-
"""Focused status/list semantics for the durable workflow run store."""
import time

from fastapi import FastAPI
from fastapi.testclient import TestClient

import guanlan_v2.workflow.api as workflow_api
import guanlan_v2.workflow.executor as workflow_executor
from guanlan_v2.workflow.run_store import WorkflowRunStore


GRAPH = {
    "nodes": [{"id": "f", "type": "formula", "params": {"expr": "close"}}],
    "edges": [],
}


def _create(store, run_id, *, workspace_id=None):
    return store.create(
        run_id=run_id,
        graph=GRAPH,
        overrides={},
        workspace_id=workspace_id,
    )


def test_completed_result_with_node_errors_gets_an_honest_terminal_status(tmp_path):
    store = WorkflowRunStore(tmp_path / "runs")
    run_id = "wr_111111111111111111111111"
    _create(store, run_id)

    stored = store.update(run_id, status="completed", result={
        "ok": True,
        "terminal": {"kind": "backtest"},
        "node_errors": [{"nid": "analysis", "error": "missing factor"}],
        "warnings": [],
    })

    assert stored["status"] == "completed_with_errors"
    reopened = WorkflowRunStore(tmp_path / "runs").get(run_id)
    assert reopened["status"] == "completed_with_errors"


def test_clean_completed_result_keeps_completed_status(tmp_path):
    store = WorkflowRunStore(tmp_path / "runs")
    run_id = "wr_222222222222222222222222"
    _create(store, run_id)

    stored = store.update(run_id, status="completed", result={
        "ok": True,
        "terminal": {"kind": "analysis"},
        "node_errors": [],
    })

    assert stored["status"] == "completed"


def test_existing_status_endpoint_exposes_completed_with_errors(tmp_path, monkeypatch):
    monkeypatch.setattr(workflow_executor, "run_graph", lambda *args, **kwargs: {
        "ok": True,
        "terminal": {"kind": "backtest", "payload": {"ok": True}},
        "node_errors": [{"nid": "analysis", "error": "missing factor"}],
        "warnings": [],
    })
    app = FastAPI()
    app.include_router(workflow_api.build_workflow_router(
        run_store=WorkflowRunStore(tmp_path / "runs"),
    ))
    client = TestClient(app)
    run_id = "wr_666666666666666666666666"
    assert client.post("/workflow/run/start", json={
        "run_id": run_id,
        "graph": GRAPH,
    }).json()["ok"] is True

    deadline = time.time() + 3
    status = None
    while time.time() < deadline:
        status = client.get(f"/workflow/run/status/{run_id}").json()
        if status.get("status") not in {"queued", "running"}:
            break
        time.sleep(0.01)

    assert status["status"] == "completed_with_errors"
    assert status["result"]["terminal"]["kind"] == "backtest"
    assert status["result"]["node_errors"]


def test_restart_orphan_keeps_failed_compatibility_and_explicit_evidence(tmp_path):
    root = tmp_path / "runs"
    first = WorkflowRunStore(root)
    run_id = "wr_333333333333333333333333"
    _create(first, run_id)
    first.update(run_id, status="running")

    orphan = WorkflowRunStore(root).get(run_id)

    assert orphan["status"] == "failed"
    assert orphan["failure_kind"] == "orphaned"
    assert orphan["orphaned"] is True
    assert orphan["orphaned_at"] == orphan["ended_at"]


def test_run_list_is_scoped_bounded_and_keeps_heavy_payloads_out(tmp_path):
    store = WorkflowRunStore(tmp_path / "runs")
    global_id = "wr_444444444444444444444444"
    workspace_id = "wr_555555555555555555555555"
    _create(store, global_id)
    _create(store, workspace_id, workspace_id="rw_0123456789ab")
    store.update(global_id, status="completed", result={
        "ok": True,
        "terminal": {"kind": "backtest", "payload": {"large": [1, 2, 3]}},
        "node_errors": [{"nid": "analysis", "error": "degraded"}],
        "warnings": ["short sample"],
    })

    rows = store.list_runs(limit=1)

    assert [row["id"] for row in rows] == [global_id]
    assert rows[0]["status"] == "completed_with_errors"
    assert rows[0]["has_result"] is True
    assert rows[0]["terminal_kind"] == "backtest"
    assert rows[0]["node_error_count"] == 1
    assert rows[0]["warning_count"] == 1
    assert "graph" not in rows[0] and "result" not in rows[0]
    assert store.list_runs(workspace_id="rw_0123456789ab")[0]["id"] == workspace_id
