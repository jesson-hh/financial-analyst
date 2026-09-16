# -*- coding: utf-8 -*-
"""Workflow integration contract for orthogonalization and overfit auditing."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

import guanlan_v2.workflow.api as wapi
import guanlan_v2.workflow.executor as wex


def test_runtime_panel_snapshot_binds_values_and_is_order_stable():
    dates = pd.date_range("2026-01-01", periods=2, freq="D")
    index = pd.MultiIndex.from_product(
        [dates, ["SH600001", "SH600002"]], names=["datetime", "code"],
    )
    frame = pd.DataFrame({
        "close": [10.0, 11.0, 10.5, 11.5],
        "volume": [100.0, 90.0, 120.0, 80.0],
    }, index=index)
    first = wapi._panel_content_snapshot(SimpleNamespace(df=frame))
    reordered = wapi._panel_content_snapshot(SimpleNamespace(
        df=frame.iloc[::-1].loc[:, ["volume", "close"]],
    ))
    changed_frame = frame.copy()
    changed_frame.iloc[0, 0] += 0.01
    changed = wapi._panel_content_snapshot(SimpleNamespace(df=changed_frame))

    assert first["authority"] == "runtime_panel_content"
    assert first["strict_pit"] is False
    assert first["digest"] == reordered["digest"]
    assert first["digest"] != changed["digest"]

    # Duplicate vendor display labels must not expand through label selection.
    duplicate = pd.DataFrame(
        np.arange(8, dtype=float).reshape(4, 2), index=index,
        columns=["vendor_field", "vendor_field"],
    )
    duplicate_snapshot = wapi._panel_content_snapshot(SimpleNamespace(df=duplicate))
    assert duplicate_snapshot["n_columns"] == 2
    assert len(duplicate_snapshot["dtypes"]) == 2


def test_orthogonalization_adapter_materializes_replayable_spec():
    dates = pd.date_range("2026-01-01", periods=3, freq="D")
    codes = [f"SH600{i:03d}" for i in range(12)]
    index = pd.MultiIndex.from_product([dates, codes], names=["datetime", "code"])
    code_pos = np.tile(np.arange(len(codes), dtype=float), len(dates))
    industry = pd.Series(
        np.tile(["银行"] * 6 + ["电子"] * 6, len(dates)), index=index, name="industry"
    )
    market_cap = pd.Series(np.exp(8.0 + code_pos / 20.0), index=index)
    # Preserve a small non-control component so residual output has visible variance.
    factor = pd.Series(
        1.7 * np.log(market_cap.to_numpy())
        + (industry.to_numpy() == "电子") * 0.8
        + np.sin(code_pos),
        index=index,
        name="candidate",
    )
    panel = SimpleNamespace(
        df=pd.DataFrame({"total_mv": market_cap}, index=index),
        industry=industry,
    )

    residual, info = wapi._orthogonalize_from_spec(factor, panel, {
        "industry": True,
        "size": "total_mv",
        "styles": [],
        "existing_factors": [],
        "min_obs": 8,
        "max_condition_number": 1.0e8,
    })

    assert info["applied"] is True
    assert info["summary"]["n_ok_dates"] == len(dates)
    assert info["artifact"]["schema"] == "guanlan.factor_spec.v1"
    assert len(info["artifact"]["transform_digest"]) == 64
    json.dumps(info, ensure_ascii=False)
    assert residual.notna().sum() == len(index)
    # OLS residuals are orthogonal to size and have zero group means each date.
    for day in dates:
        r = residual.xs(day)
        mv = np.log(market_cap.xs(day))
        assert abs(float(r.corr(mv))) < 1.0e-10
        daily_industry = industry.xs(day)
        assert max(abs(float(r[daily_industry == group].mean())) for group in ("银行", "电子")) < 1.0e-10


def test_orthogonalization_adapter_rejects_no_controls_and_preserves_dsl_commas():
    dates = pd.date_range("2026-01-01", periods=2, freq="D")
    codes = [f"SH600{i:03d}" for i in range(12)]
    index = pd.MultiIndex.from_product([dates, codes], names=["datetime", "code"])
    factor = pd.Series(np.linspace(-1.0, 1.0, len(index)), index=index)
    from financial_analyst.factors.zoo.panel import PanelData
    close = np.linspace(10.0, 20.0, len(index))
    panel = PanelData(pd.DataFrame({
        "open": close, "high": close + 0.1, "low": close - 0.1,
        "close": close, "volume": np.linspace(100.0, 200.0, len(index)),
    }, index=index))
    with pytest.raises(ValueError, match="至少需要一个"):
        wapi._orthogonalize_from_spec(factor, panel, {
            "industry": "否", "size": "不控制", "styles": "", "old_factors": "",
        })

    _, info = wapi._orthogonalize_from_spec(factor, panel, {
        "industry": "否", "size": "不控制", "styles": "",
        "old_factors": "correlation(close,volume,20)", "min_obs": 8,
    })
    assert info["artifact"]["transform"]["existing_factors"] == [
        "correlation(close,volume,20)"
    ]
    resolved = info["artifact"]["transform"]["resolved_existing_factors"]
    assert resolved[0]["kind"] == "dsl" and len(resolved[0]["definition_digest"]) == 64


def test_sparse_candidate_uses_full_panel_for_rolling_style_controls():
    dates = pd.date_range("2025-01-01", periods=80, freq="B")
    codes = [f"SH600{i:03d}" for i in range(25)]
    index = pd.MultiIndex.from_product([dates, codes], names=["datetime", "code"])
    day = np.repeat(np.arange(len(dates), dtype=float), len(codes))
    code = np.tile(np.arange(len(codes), dtype=float), len(dates))
    market = 0.002 * np.sin(day / 4.0) + 0.0005 * np.cos(day / 9.0)
    returns = market * (0.5 + code / 25.0) + 0.001 * np.sin(day + code)
    panel = SimpleNamespace(
        df=pd.DataFrame({"returns": returns}, index=index),
        industry=pd.Series("未知", index=index),
    )
    factor = pd.Series(np.nan, index=index, name="sparse")
    factor.loc[(dates[-1], slice(None))] = np.sin(np.arange(len(codes)))

    residual, info = wapi._orthogonalize_from_spec(factor, panel, {
        "industry": "否", "size": "不控制", "styles": ["beta"],
        "min_obs": 20,
    })

    assert info["applied"] is True
    assert info["summary"]["n_ok_dates"] == 1
    assert residual.xs(dates[-1]).notna().sum() == len(codes)


def test_orthogonalization_digest_binds_registered_factor_definition():
    from financial_analyst.factors.zoo.panel import PanelData
    from financial_analyst.factors.zoo.registry import AlphaSpec, register, unregister

    dates = pd.to_datetime(["2026-02-02"])
    codes = [f"SH601{i:03d}" for i in range(12)]
    index = pd.MultiIndex.from_product([dates, codes], names=["datetime", "code"])
    close = np.linspace(10.0, 21.0, len(index))
    volume = np.linspace(220.0, 100.0, len(index)) ** 1.01
    panel = PanelData(pd.DataFrame({
        "open": close, "high": close + 0.2, "low": close - 0.2,
        "close": close, "volume": volume,
    }, index=index))
    factor = pd.Series(np.sin(np.arange(len(index))), index=index)
    name = "__workflow_replay_definition_test__"

    def control_close(p):
        return p.close

    def control_volume(p):
        return p.volume

    spec = {"industry": False, "size": "none", "existing_factors": [name], "min_obs": 8}
    unregister(name)
    try:
        register(AlphaSpec(name, "test", "", "close", control_close))
        _, first = wapi._orthogonalize_from_spec(factor, panel, spec)
        unregister(name)
        register(AlphaSpec(name, "test", "", "volume", control_volume))
        _, second = wapi._orthogonalize_from_spec(factor, panel, spec)
    finally:
        unregister(name)

    assert first["artifact"]["transform_digest"] != second["artifact"]["transform_digest"]
    resolved = second["artifact"]["transform"]["resolved_existing_factors"][0]
    assert resolved["formula_text"] == "volume"
    assert len(resolved["implementation_digest"]) == 64


def test_durable_graph_preserves_orthogonalization_and_prioritizes_audit(monkeypatch):
    graph = {
        "nodes": [
            {"id": "s", "type": "source", "x": -1, "params": {
                "scope": "自选", "codes": "600519,000858",
                "start": "2023-01-01", "end": "2026-01-31",
            }},
            {"id": "f", "type": "formula", "x": 0, "params": {"expr": "rank(pb)"}},
            {"id": "o", "type": "orthogonalize", "x": 1, "params": {
                "industry": "是", "size": "总市值", "styles": "beta,volatility",
                "old_factors": "rank(roe)", "min_obs": 20,
            }},
            {"id": "b", "type": "backtest", "x": 2, "params": {
                "start": "2024-01-01", "end": "2025-12-31",
                "rebalance": "month", "combine": "icir",
            }},
            {"id": "a", "type": "overfit", "x": 3, "params": {"confidence": "0.95"}},
        ],
        "edges": [
            {"from": ["s", "data"], "to": ["o", "src"]},
            {"from": ["f", "out"], "to": ["o", "signal"]},
            {"from": ["o", "factor"], "to": ["b", "factor"]},
            {"from": ["b", "result"], "to": ["a", "result"]},
        ],
    }
    ok, errors = wapi.validate_graph(graph)
    assert ok is True and errors == []
    seen = {}

    def fake_backtest(body):
        seen["orthogonalization"] = body.orthogonalization
        seen["backtest"] = body
        nav = [[f"2025-{month:02d}-28", 1.0 + month * 0.01] for month in range(1, 13)]
        return JSONResponse({
            "ok": True, "status": "ok", "method": "backtest_vector",
            "portfolio": {"nav_series": nav, "sharpe": 1.2, "ann_return": 0.15},
            "headline_ic": {"rank_ic": 0.03},
            "backtest": {"rebalance": "month"},
            "orthogonalization": {
                "requested": True, "applied": True,
                "summary": {"output_coverage": 0.98},
            },
            "warnings": ["回测窗口较短"],
        })

    def fake_audit(body, **runtime_binding):
        seen["audit_nav"] = body.nav_series
        seen["n_trials"] = body.n_trials
        seen["runtime_binding"] = runtime_binding
        return JSONResponse({
            "ok": True, "status": "INCOMPLETE", "passes_gate": False,
            "metrics": {}, "evidence": {},
            "reasons": ["trial_returns is missing"], "warnings": ["审计家族不完整"],
        })

    monkeypatch.setattr(wex, "_call_backtest", fake_backtest)
    monkeypatch.setattr(wex, "_call_overfit", fake_audit)
    out = wex.run_graph(graph, overrides={"universe": "csi_fast", "freq": "month"})

    assert out["ok"] is True
    assert out["terminal"]["kind"] == "overfit"
    assert out["terminal"]["payload"]["status"] == "INCOMPLETE"
    assert out["terminal"]["payload"]["passes_gate"] is False
    assert out["terminal"]["payload"]["orthogonalization"]["summary"]["output_coverage"] == 0.98
    assert out["terminal"]["payload"]["warnings"] == ["回测窗口较短", "审计家族不完整"]
    assert out["metrics"]["sharpe"] == 1.2
    assert seen["orthogonalization"]["styles"] == ["beta", "volatility"]
    assert seen["orthogonalization"]["existing_factors"] == ["rank(roe)"]
    assert seen["backtest"].codes == ["600519", "000858"]
    assert seen["backtest"].start == "2024-01-01"
    assert seen["backtest"].end == "2025-12-31"
    assert seen["backtest"].combine == "icir"
    assert seen["audit_nav"] and seen["n_trials"] is None
    assert "_runtime_candidate_hash" in seen["runtime_binding"]
    assert "_runtime_data_snapshot" in seen["runtime_binding"]


def test_overfit_endpoint_never_passes_without_complete_trial_family():
    app = FastAPI()
    app.include_router(wapi.build_workflow_router())
    client = TestClient(app)
    nav = [[f"2024-{month:02d}-01", 1.0 + month * 0.01 + (month % 2) * 0.002]
           for month in range(1, 13)]
    response = client.post("/workflow/overfit-audit", json={"nav_series": nav}).json()

    assert response["ok"] is True
    assert response["status"] == "INCOMPLETE"
    assert response["passes_gate"] is False
    assert any("trial_returns" in reason for reason in response["reasons"])
    assert any("n_trials" in reason for reason in response["reasons"])


def test_public_overfit_endpoint_cannot_self_attest_a_statistical_pass():
    rng = np.random.default_rng(8128)
    periods = 160
    common = rng.normal(0.0, 0.003, periods)
    selected = 0.018 + common + rng.normal(0.0, 0.002, periods)
    trials = np.column_stack([
        selected,
        0.007 + common + rng.normal(0.0, 0.004, periods),
        0.002 + common + rng.normal(0.0, 0.006, periods),
        -0.003 + common + rng.normal(0.0, 0.007, periods),
    ])
    phase = np.sin(np.arange(periods, dtype="float64") / 7.0) * 0.001
    selection_scores = np.column_stack([
        0.08 + phase, 0.06 + phase * 1.1,
        0.04 + phase * 1.2, 0.025 + phase * 1.3,
    ])
    dates = pd.date_range("2012-01-31", periods=periods, freq="ME").strftime("%Y-%m-%d").tolist()
    app = FastAPI()
    app.include_router(wapi.build_workflow_router())
    response = TestClient(app).post("/workflow/overfit-audit", json={
        "return_series": [[date, float(value)] for date, value in zip(dates, selected)],
        "trial_returns": trials.tolist(), "trial_dates": dates,
        "selection_score_matrix": selection_scores.tolist(),
        "selection_dates": dates,
        "eligible_column_indices": [0, 1, 2, 3],
        "selected_column_index": 0,
        "selection_objective": "mean_rank_ic",
        "selection_score_source": "backtest.ic.rank_ic_series",
        "selection_eligibility_scope": "full_validation_base_gate",
        "selection_rule_digest": "a" * 64,
        "n_trials": 4, "family_id": "family-1", "selected_trial_id": "trial-1",
    }).json()

    assert response["statistical_status"] == "PASS"
    assert response["status"] == "INCOMPLETE"
    assert response["passes_gate"] is False
    assert response["evidence_verified"] is False
    assert response["evidence_authority"] == "caller_supplied"
    assert any("TrialLedger" in reason for reason in response["reasons"])


def test_workflow_ui_wires_quant_nodes_and_three_state_drawer():
    source = (Path(__file__).resolve().parents[1] / "ui" / "factor" / "workflow.jsx").read_text(encoding="utf-8")
    assert "orthogonalize: { title: '因子正交化'" in source
    assert "overfit: { title: '过拟合审计'" in source
    assert "orthogonalization: f._orthogonalization || spec.orthogonalization || null" in source
    assert "ctx.post('/workflow/overfit-audit'" in source
    assert "payload.__dt === 'overfit_audit'" in source
    assert "result.__dt === 'overfit_audit'" in source
    assert "INCOMPLETE 不等于失败" in source
    assert "caller_supplied: '调用方诊断输入 · 不可解锁 PASS'" in source
    assert "result.policy_authority === 'server_governor_policy'" in source
    assert "server_trial_ledger_unbound" in source
    assert "snapshotAuthority === 'runtime_panel_content'" in source
    assert "snapshotStrictPit === true ? '是' : (snapshotStrictPit === false ? '否' : '—')" in source
    assert "ev.selection_objective === 'mean_rank_ic'" in source
    assert "基础门合格集内平均 RankIC（条件式 PBO）" in source
    assert "全验证基础联合门 shortlist" in source
    assert "收益矩阵 ·" in source and "选优矩阵 ·" in source
    assert "status === 'PASS' && result.evidence_verified === true" in source
    assert "仅 PASS 且 evidence_verified=true 时可存因子或经验卡" in source
    assert "上游正交化 ·" in source
    assert "const auditWarnings = Array.from(new Set(" in source
    assert "{auditWarnings.join(' · ')}" in source
    assert "SPECS.orthogonalize.inputs.push({ id: 'src'" in source
    assert "wantsOrthogonalize" in source and "wantsAudit" in source
    assert "auditGate && auditGate.declared" in source
    assert "factor_spec: factorSpec" in source
    assert "结构化因子已保存并注册 · 可按名称完整重放" in source
    assert "结构化因子已落盘，但运行时注册失败" in source
    assert "f.structured && f.registered" in source
    assert "结构化·未证实注册" in source
    assert "已绑定可重放结构化因子" in source
    assert "if (!res || !res.id) throw new Error('经验卡服务未返回卡片 id')" in source
    assert "audit_context_id: params.audit_context_id" in source
    assert "已绑定服务端 TrialLedger；置信度、PBO 与分块由 Governor 固定策略接管" in source
    assert "暂不能存入普通因子库" not in source
    assert "src 口只能连接「数据源」" in source


def test_workflow_ui_factor_refs_are_exact_and_data_types_share_one_policy():
    source = (Path(__file__).resolve().parents[1] / "ui" / "factor" / "workflow.jsx").read_text(encoding="utf-8")
    assert "function dtCompatible(sourceDt, targetDt, targetType, sourceType, targetPort)" in source
    assert "if (targetPort === 'src') return sourceType === 'source'" in source
    assert "if (sourceType === 'source') return false" in source
    assert "数据源 data 口只能连接下游的上下文 src 口" in source
    assert "sNode && sNode.type === 'source' && portId === 'data' && tp !== 'src'" in source
    assert "sourceType === 'formula' || sourceType === 'factorlib'" in source
    assert "targetDt === 'factor' && sourceType === 'feature'" in source
    assert "sourceDt === 'series' && targetDt === 'factor'" in source
    assert "targetType === 'backtest' || targetType === 'portfolio'" in source
    assert source.count("dtCompatible(") >= 3
    assert "const expectedSpec = (params.factor_spec && params.factor_spec.schema === 'guanlan.factor_spec.v1')" in source
    assert "const expectedDigest = String(params.factor_spec_digest || '').trim()" in source
    assert "已缺失，拒绝用缓存基础公式降级运行" in source
    assert "定义摘要已变化，拒绝执行非原始版本" in source
    assert "onParam('factor_spec', f.factor_spec || null)" in source
    assert "onParam('factor_spec_digest', f.factor_spec_digest || '')" in source
    assert "factor_spec: r.factor_spec || f.factor_spec || null" in source
    assert "factor_spec_digest: r.factor_spec_digest || f.factor_spec_digest || null" in source
    assert "当前运行面板的静态行业映射" in source
    assert "未提供历史行业 vintage，严格 PIT=false 会在审计证据显形" in source
    assert "使用每个交易日可见的行业标签" not in source


def test_executor_preserves_zero_pbo_threshold_and_audit_is_a_hard_research_gate(monkeypatch):
    captured = {}

    def fake_audit(body, **_runtime_binding):
        captured["pbo_max"] = body.pbo_max
        return JSONResponse({
            "ok": True, "status": "INCOMPLETE", "passes_gate": False,
            "metrics": {}, "evidence": {}, "reasons": ["ledger missing"],
        })

    monkeypatch.setattr(wex, "_call_overfit", fake_audit)
    result = wex._exec_overfit({"result": {
        "portfolio": {"return_series": [[f"2025-{m:02d}-01", 0.01] for m in range(1, 13)]},
        "backtest": {"rebalance": "month"},
    }}, {"pbo_max": 0}, {"universe": "csi_fast"})["audit"]
    assert captured["pbo_max"] == 0.0
    metrics = wex.metrics_of_terminal(result)
    assert metrics["audit_status"] == "INCOMPLETE"
    assert metrics["audit_passes_gate"] is False

    from guanlan_v2.research.loop import _gate
    base = {"rank_ic": 0.05, "sharpe": 1.2, "oos_verdict": "robust"}
    assert _gate({**base, "audit_status": "PASS", "audit_passes_gate": True}, 0.02)["passed"] is True
    assert _gate({**base, "audit_status": "FAIL", "audit_passes_gate": False}, 0.02)["passed"] is False
    assert _gate({**base, "audit_status": "INCOMPLETE", "audit_passes_gate": False}, 0.02)["passed"] is False


def test_strict_return_series_keeps_first_realised_period_and_rejects_reordering():
    assert wapi._jsonable(pd.Timestamp("2026-01-31")) == "2026-01-31T00:00:00"
    ns_time = np.datetime64("2026-08-22T14:34:56.123456789", "ns")
    assert wapi._jsonable(ns_time) == "2026-08-22T14:34:56.123456789"
    assert wapi._jsonable(np.datetime64("NaT", "ns")) is None
    values, dates = wapi._strict_dated_values(
        [["2026-01-31", 0.01], ["2026-02-28", -0.02]],
        name="return_series", positive=False,
    )
    assert values == [0.01, -0.02]
    assert dates == ["2026-01-31", "2026-02-28"]
    with pytest.raises(ValueError, match="严格递增"):
        wapi._strict_dated_values(
            [["2026-02-28", 0.01], ["2026-01-31", -0.02]],
            name="return_series", positive=False,
        )
    with pytest.raises((TypeError, ValueError)):
        wapi._strict_dated_values(
            [["2026-01-31", None]], name="return_series", positive=False,
        )


def test_catalog_matches_new_context_and_replayable_backtest_params():
    assert ["src", "series"] in wapi._CATALOG["orthogonalize"]["in"]
    for key in ("start", "end", "rebalance", "commission", "stamp_tax", "slippage"):
        assert key in wapi._CATALOG["backtest"]["params"]
    assert "上述 27 类" in wapi.SYSTEM_PROMPT
    assert wex._split_factor_controls("correlation(close,volume,20)") == [
        "correlation(close,volume,20)"
    ]


def test_autowire_source_connects_each_missing_context_branch_including_orthogonalize():
    graph = {
        "nodes": [
            {"id": "s", "type": "source", "params": {}},
            {"id": "f", "type": "formula", "params": {"expr": "rank(pb)"}},
            {"id": "o", "type": "orthogonalize", "params": {}},
            {"id": "ev", "type": "event", "params": {}},
        ],
        "edges": [
            {"from": ["f", "out"], "to": ["o", "signal"]},
            {"from": ["f", "out"], "to": ["ev", "trigger"]},
            {"from": ["s", "data"], "to": ["ev", "src"]},
        ],
    }

    wapi._autowire_source(graph)
    wapi._autowire_source(graph)  # self-healing is idempotent

    orth_edges = [e for e in graph["edges"] if e["to"] == ["o", "src"]]
    event_edges = [e for e in graph["edges"] if e["to"] == ["ev", "src"]]
    assert orth_edges == [{"from": ["s", "data"], "to": ["o", "src"]}]
    assert event_edges == [{"from": ["s", "data"], "to": ["ev", "src"]}]


def test_autowire_respects_any_existing_source_in_multi_source_graph():
    graph = {
        "nodes": [
            {"id": "s1", "type": "source", "params": {}},
            {"id": "s2", "type": "source", "params": {}},
            {"id": "f", "type": "formula", "params": {"expr": "rank(pb)"}},
            {"id": "o", "type": "orthogonalize", "params": {}},
            {"id": "bt", "type": "backtest", "params": {}},
        ],
        "edges": [
            {"from": ["f", "out"], "to": ["o", "signal"]},
            {"from": ["s2", "data"], "to": ["o", "src"]},
            {"from": ["o", "factor"], "to": ["bt", "factor"]},
        ],
    }

    assert wapi.validate_graph(graph) == (True, [])
    wapi._autowire_source(graph)
    assert [e for e in graph["edges"] if e["to"] == ["o", "src"]] == [
        {"from": ["s2", "data"], "to": ["o", "src"]}
    ]


def test_executor_accepts_list_style_controls_without_string_corruption():
    out = wex._exec_orthogonalize(
        {"signal": {"expr": "rank(pb)"}},
        {"industry": "否", "size": "不控制", "styles": ["beta", "volatility"]},
        {"universe": "csi_fast"},
    )["factor"]
    assert out["_orthogonalization"]["styles"] == ["beta", "volatility"]


def test_declared_audit_failure_cannot_fall_back_to_pretty_backtest(monkeypatch):
    graph = {
        "nodes": [
            {"id": "f", "type": "formula", "params": {"expr": "rank(pb)"}},
            {"id": "bt", "type": "backtest", "params": {}},
            {"id": "audit", "type": "overfit", "params": {}},
        ],
        "edges": [
            {"from": ["f", "out"], "to": ["bt", "factor"]},
            {"from": ["bt", "result"], "to": ["audit", "result"]},
        ],
    }
    monkeypatch.setattr(wex, "_call_backtest", lambda body: JSONResponse({
        "ok": True,
        "portfolio": {"sharpe": 2.1, "ann_return": 0.3,
                      "return_series": [["2026-01-31", 0.02], ["2026-02-28", 0.01]]},
        "headline_ic": {"rank_ic": 0.08},
        "oos": {"verdict": "robust"},
    }))
    monkeypatch.setattr(wex, "_call_overfit", lambda body, **_runtime_binding: JSONResponse({
        "ok": False, "reason": "TrialLedger unavailable",
    }))

    out = wex.run_graph(graph, overrides={"universe": "csi_fast", "freq": "month"})

    assert out["ok"] is True and out["terminal"]["kind"] == "overfit"
    assert out["terminal"]["synthetic"] is True
    assert out["terminal"]["payload"]["__dt"] == "overfit_audit"
    assert out["terminal"]["payload"]["status"] == "INCOMPLETE"
    assert out["metrics"]["rank_ic"] == 0.08
    assert out["metrics"]["audit_declared"] is True
    assert out["metrics"]["audit_status"] == "INCOMPLETE"
    assert out["metrics"]["audit_passes_gate"] is False
    assert out["audit"]["execution_error"] is True
    assert out["audit"]["nodes"][0]["reason"].endswith("TrialLedger unavailable")
    assert any("合成 INCOMPLETE" in warning for warning in out["warnings"])
    from guanlan_v2.research.loop import _gate
    assert _gate(out["metrics"], 0.02)["passed"] is False


def test_model_terminal_cannot_use_unbound_feature_backtest_audit_pass(monkeypatch):
    graph = {
        "nodes": [
            {"id": "f", "type": "formula", "params": {"expr": "rank(pb)"}},
            {"id": "analysis", "type": "analysis", "params": {}},
            {"id": "model_marker", "type": "lgbm", "params": {}},
            {"id": "bt", "type": "backtest", "params": {}},
            {"id": "audit", "type": "overfit", "params": {}},
        ],
        "edges": [
            {"from": ["f", "out"], "to": ["analysis", "factor"]},
            {"from": ["f", "out"], "to": ["bt", "factor"]},
            {"from": ["bt", "result"], "to": ["audit", "result"]},
        ],
    }
    report = {
        "ok": True, "_kind": "lightgbm",
        "portfolio": {"sharpe": 1.6, "ann_return": 0.2},
        "headline_ic": {"rank_ic": 0.06}, "oos": {"verdict": "robust"},
    }
    monkeypatch.setattr(wex, "_call_report2", lambda body: JSONResponse(report))
    monkeypatch.setattr(wex, "_call_backtest", lambda body: JSONResponse({
        "ok": True, "portfolio": {"sharpe": 1.9,
        "return_series": [["2026-01-31", 0.01], ["2026-02-28", 0.02]]},
        "headline_ic": {"rank_ic": 0.09}, "oos": {"verdict": "robust"},
    }))
    monkeypatch.setattr(wex, "_call_overfit", lambda body, **_runtime_binding: JSONResponse({
        "ok": True, "status": "PASS", "passes_gate": True,
        "metrics": {}, "evidence": {}, "reasons": [],
    }))

    out = wex.run_graph(
        graph, overrides={"universe": "csi_fast", "freq": "month"},
        prefer_model_terminal=True,
    )

    assert out["terminal"]["kind"] == "analysis"
    assert out["metrics"]["rank_ic"] == 0.06
    assert out["metrics"]["audit_status"] == "INCOMPLETE"
    assert out["metrics"]["audit_passes_gate"] is False
    assert out["audit"]["model_binding_required"] is True
    assert out["audit"]["model_binding_verified"] is False
    assert any("selection_binding" in warning for warning in out["warnings"])
    from guanlan_v2.research.loop import _gate
    assert _gate(out["metrics"], 0.02)["passed"] is False


def test_src_port_rejects_formula_context_and_autowire_adds_real_source():
    graph = {
        "nodes": [
            {"id": "s", "type": "source", "params": {}},
            {"id": "f", "type": "formula", "params": {"expr": "rank(pb)"}},
            {"id": "o", "type": "orthogonalize", "params": {}},
            {"id": "bt", "type": "backtest", "params": {}},
        ],
        "edges": [
            {"from": ["f", "out"], "to": ["o", "signal"]},
            {"from": ["f", "out"], "to": ["o", "src"]},
            {"from": ["o", "factor"], "to": ["bt", "factor"]},
        ],
    }

    ok_before, errors_before = wapi.validate_graph(graph)
    assert ok_before is False
    assert any("o.src" in error and "source.data" in error for error in errors_before)
    executed = wex.run_graph(graph)
    assert executed["ok"] is False and "source.data" in executed["reason"]

    wapi._autowire_source(graph)
    assert {"from": ["s", "data"], "to": ["o", "src"]} in graph["edges"]
    ok_after, errors_after = wapi.validate_graph(graph)
    assert ok_after is False
    assert any("o.src" in error and "多条边" in error for error in errors_after)


def test_multiple_audits_project_aggregate_failure_into_visible_terminal(monkeypatch):
    graph = {
        "nodes": [
            {"id": "f", "type": "formula", "params": {"expr": "rank(pb)"}},
            {"id": "bt", "type": "backtest", "params": {}},
            {"id": "a_fail", "type": "overfit", "x": 2, "params": {}},
            {"id": "a_pass", "type": "overfit", "x": 3, "params": {}},
        ],
        "edges": [
            {"from": ["f", "out"], "to": ["bt", "factor"]},
            {"from": ["bt", "result"], "to": ["a_fail", "result"]},
            {"from": ["bt", "result"], "to": ["a_pass", "result"]},
        ],
    }
    monkeypatch.setattr(wex, "_call_backtest", lambda body: JSONResponse({
        "ok": True, "portfolio": {"sharpe": 1.4,
        "return_series": [["2026-01-31", 0.01], ["2026-02-28", 0.02]]},
        "headline_ic": {"rank_ic": 0.04}, "oos": {"verdict": "robust"},
    }))
    calls = iter([
        {"ok": True, "status": "FAIL", "passes_gate": False,
         "metrics": {}, "failed_checks": ["PBO too high"]},
        {"ok": True, "status": "PASS", "passes_gate": True,
         "metrics": {}, "reasons": []},
    ])
    monkeypatch.setattr(
        wex, "_call_overfit",
        lambda body, **_runtime_binding: JSONResponse(next(calls)),
    )

    out = wex.run_graph(graph, overrides={"universe": "csi_fast", "freq": "month"})

    valid, validation_errors = wapi.validate_graph(graph)
    assert valid is False and any("最多一个 overfit" in e for e in validation_errors)
    assert out["terminal"]["node_id"] == "a_pass"  # provenance remains the last terminal
    assert out["terminal"]["payload"]["status"] == "FAIL"
    assert out["terminal"]["payload"]["passes_gate"] is False
    assert "PBO too high" in out["terminal"]["payload"]["failed_checks"]
    assert out["terminal"]["payload"]["metrics"] == {}
    assert out["audit"]["configuration_error"] is True
    assert out["terminal"]["payload"]["audit_aggregate"]["nodes"] == out["audit"]["nodes"]
    assert out["metrics"]["audit_status"] == "FAIL"


def test_critique_prompt_preserves_audit_gate_metrics(monkeypatch):
    seen = {}

    async def fake_complete(system, user, temperature=0.2):
        seen["user"] = user
        return json.dumps({
            "diagnosis": "保留审计并改因子",
            "nodes": [
                {"id": "s", "type": "source", "params": {"universe": "csi_fast"}},
                {"id": "f", "type": "formula", "params": {"expr": "rank(roe)"}},
                {"id": "t", "type": "tsic", "params": {"fwd_days": 10}},
            ],
            "edges": [{"from": ["f", "out"], "to": ["t", "factor"]}],
        }, ensure_ascii=False)

    monkeypatch.setattr(wapi, "_llm_complete", fake_complete)
    app = FastAPI()
    app.include_router(wapi.build_workflow_router())
    response = TestClient(app).post("/workflow/critique", json={
        "goal": "改进但保留审计",
        "metrics": {
            "rank_ic": 0.05, "sharpe": 1.2, "oos_verdict": "robust",
            "audit_declared": True, "audit_status": "INCOMPLETE",
            "audit_passes_gate": False, "audit_execution_error": True,
        },
        "graph": {"nodes": [], "edges": []},
    }).json()

    assert response["ok"] is True
    assert '"audit_declared": true' in seen["user"]
    assert '"audit_status": "INCOMPLETE"' in seen["user"]
    assert '"audit_passes_gate": false' in seen["user"]
    assert '"audit_execution_error": true' in seen["user"]
