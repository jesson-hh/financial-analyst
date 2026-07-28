# -*- coding: utf-8 -*-
"""POST /workflow/run 端点三态(executor 打桩,零引擎)。"""
from fastapi import FastAPI
from fastapi.testclient import TestClient

import guanlan_v2.workflow.api as wapi
import guanlan_v2.workflow.executor as wex


def _client():
    app = FastAPI()
    app.include_router(wapi.build_workflow_router())
    return TestClient(app)


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


def test_norm_codes_bj_920_segment():
    """#5 自选股池归一:北交所 920 新号段裸六位 → BJ 前缀;4/8 照旧;其余不动。"""
    assert wapi._norm_codes(["920807"]) == ["BJ920807"]
    assert wapi._norm_codes(["920807.BJ", "bj920807"]) == ["BJ920807"]  # 去重保序
    assert wapi._norm_codes(["830799", "430047"]) == ["BJ830799", "BJ430047"]
    assert wapi._norm_codes(["600519", "000630"]) == ["SH600519", "SZ000630"]
