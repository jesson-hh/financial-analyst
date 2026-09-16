"""Diagnostic execution completes without fabricating research gate metrics."""
from copy import deepcopy
import time
import pytest

from fastapi import FastAPI
from fastapi.testclient import TestClient

from guanlan_v2.workflow import api, executor
from guanlan_v2.workflow.run_store import WorkflowRunStore


GRAPH = {
    "nodes": [
        {"id": "src", "type": "source", "params": {"scope": "single", "code": "SH600667",
          "start": "2026-06-12", "end": "2026-09-08", "research_freq": "day"}},
        {"id": "ret", "type": "formula", "params": {"expr": "returns"}},
        {"id": "summary", "type": "relstat", "params": {}},
    ],
    "edges": [{"from": ["src", "data"], "to": ["summary", "src"]},
              {"from": ["ret", "out"], "to": ["summary", "factor"]}],
}
PAYLOAD = {"ok": True, "method": "relstat", "start": "2026-06-12", "end": "2026-09-08",
           "sample_start": "2026-06-15", "sample_end": "2026-09-08", "requested_codes": ["SH600667"],
           "summary": {"n_codes": 1, "avg_n": 60, "mean_level": .01},
           "codes_relstat": [{"code": "SH600667", "n": 60, "level": .01, "vol": .03}],
           "metrics": {"rank_ic": .99}, "expr_or_name": "returns"}


def _stub(monkeypatch):
    def relstat(body):
        assert body.codes == ["SH600667"]
        assert body.start == "2026-06-12" and body.end == "2026-09-08"
        assert body.expr_or_name == "returns"
        return deepcopy(PAYLOAD)
    monkeypatch.setattr(executor, "_call_relstat", relstat)


def test_opt_in_diagnostic_does_not_relax_research_gate(monkeypatch):
    _stub(monkeypatch)
    out = executor.run_graph(GRAPH, allow_diagnostic_result=True)
    assert out["ok"] is True and out["terminal"]["kind"] == "relstat", out
    assert out["result_kind"] == "diagnostic" and out["gate_eligible"] is False
    assert out["metrics"] is None and not out["node_errors"]
    assert out["terminal"]["payload"]["summary"]["avg_n"] == 60
    # Default governs research loops; even a mistaken opt-in with model
    # preference cannot turn a diagnostic into strategy evidence.
    for kwargs in ({}, {"allow_diagnostic_result": True, "prefer_model_terminal": True}):
        closed = executor.run_graph(GRAPH, **kwargs)
        assert closed["ok"] is False and closed["metrics"] is None


def test_diagnostic_never_masks_failed_declared_primary(monkeypatch):
    _stub(monkeypatch)
    graph = deepcopy(GRAPH)
    graph["nodes"].append({"id": "bt", "type": "backtest", "params": {}})
    out = executor.run_graph(graph, allow_diagnostic_result=True)
    assert out["ok"] is False and out["terminal"] is None
    assert out["node_errors"][0]["nid"] == "bt"


def test_missing_data_remains_failure(monkeypatch):
    monkeypatch.setattr(executor, "_call_relstat", lambda body: {"ok": False, "reason": "empty data"})
    out = executor.run_graph(GRAPH, allow_diagnostic_result=True)
    assert out["ok"] is False and "empty data" in out["reason"]
    assert out["metrics"] is None


def test_http_sync_and_durable_return_readable_diagnostic(tmp_path, monkeypatch):
    _stub(monkeypatch)
    app = FastAPI()
    app.include_router(api.build_workflow_router(run_store=WorkflowRunStore(tmp_path / "runs")))
    client = TestClient(app)
    sync = client.post("/workflow/run", json={"graph": GRAPH}).json()
    assert sync["ok"] and sync["gate_eligible"] is False and sync["metrics"] is None, sync
    queued = client.post("/workflow/run/start", json={"graph": GRAPH}).json()
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        result = client.get("/workflow/run/status/" + queued["id"]).json()
        if result["status"] not in {"queued", "running"}:
            break
        time.sleep(.01)
    assert result["status"] == "completed"
    assert result["result"]["terminal"]["payload"]["summary"]["avg_n"] == 60
    from guanlan_v2.console.workflow_tools import _result_summary
    agent = _result_summary(result["result"])
    assert agent["gate_eligible"] is False
    assert agent["terminals"][0]["codes_relstat"][0]["code"] == "SH600667"
    assert agent["terminals"][0]["sample_end"] == "2026-09-08"
    assert agent["terminals"][0]["summary"]["avg_n"] == 60


def test_relstat_summary_truncates_rows_honestly():
    from guanlan_v2.console.workflow_tools import _result_summary
    payload = deepcopy(PAYLOAD)
    payload["codes_relstat"] = payload["codes_relstat"] * 31
    result = _result_summary({"ok": True, "terminal": {"kind": "relstat", "payload": payload}})
    terminal = result["terminals"][0]
    assert terminal["diagnostic_rows_total"] == 31
    assert terminal["diagnostic_rows_truncated"] is True
    assert len(terminal["codes_relstat"]) == 30


@pytest.mark.parametrize("code,codes", [("", None), ("not-a-stock", None),
                                      ("SH600667", ["SH600667", "SH600519"])])
def test_single_stock_scope_never_widens(code, codes):
    graph = deepcopy(GRAPH)
    graph["nodes"][0]["params"].update({"code": code, "codes": codes})
    ok, errors = api.validate_graph(graph, require_complete=True)
    assert ok is False and any("拒绝回落" in error for error in errors)
    out = executor.run_graph(graph, allow_diagnostic_result=True)
    assert out["ok"] is False and out["terminal"] is None


def test_single_stock_conflict_fails_but_equivalent_aliases_match():
    from guanlan_v2.workflow.source_scope import source_codes
    with pytest.raises(ValueError, match="冲突"):
        source_codes({"scope": "single", "code": "600667", "codes": "SH600519"})
    assert source_codes({"scope": "个股", "code": "600667", "codes": "600667.SH"}) == ["600667.SH"]


def test_relstat_actual_dates_are_not_requested_boundaries(monkeypatch):
    import json
    import numpy as np
    import pandas as pd
    from financial_analyst.data import loader_factory
    from financial_analyst.data import universe as universe_module
    from financial_analyst.factors.zoo import panel_cache, registry
    dates = pd.bdate_range("2026-07-01", periods=25)
    series = pd.Series(np.arange(25, dtype=float), index=pd.MultiIndex.from_product(
        [dates, ["SH600667"]], names=["datetime", "code"]))
    series.iloc[0] = np.inf
    class Panel:
        def codes(self):
            return ["SH600667"]
    class Factor:
        def compute(self, panel):
            return series
    monkeypatch.setattr(loader_factory, "get_default_loader", lambda: object())
    monkeypatch.setattr(panel_cache, "load_panel_cached", lambda *a, **k: Panel())
    monkeypatch.setattr(registry, "get", lambda name: Factor())
    monkeypatch.setattr(api, "_inject_market_refs", lambda panel, *a: (panel, []))
    def forbidden_pool_resolution(*args, **kwargs):
        raise AssertionError("explicit codes must not resolve or intersect a default pool")
    monkeypatch.setattr(universe_module, "resolve_universe_codes", forbidden_pool_resolution)
    response = api._factor_relstat(api.FactorRelstatIn(codes=["SH600667"], expr_or_name="returns",
        start="2026-06-12", end="2026-09-08"))
    result = json.loads(response.body)
    assert result["ok"] is True and result["summary"]["n_codes"] == 1
    assert result["sample_start"] == "2026-07-02"
    assert result["sample_end"] == dates[-1].date().isoformat()
    assert result["codes_relstat"][0]["n"] == 24
    assert result["start"] == "2026-06-12" and result["end"] == "2026-09-08"
    assert result["universe"] == "custom" and result["_universe"] == "SH600667"
    assert result["selection_mode"] == "explicit_codes"
    assert result["configured_universe"] == "csi_fast"


def test_relstat_ui_marks_diagnostic_and_actual_sample_dates():
    from pathlib import Path
    source = (Path(__file__).resolve().parents[1] / "ui/factor/workflow.jsx").read_text(encoding="utf-8")
    assert "诊断完成 · 描述统计 / 关系稳定度" in source
    assert "仅描述统计，不是回测或策略晋级通过。" in source
    assert "result.sample_start" in source and "result.sample_end" in source
