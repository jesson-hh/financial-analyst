"""Independent comparison audit tests; no generated var artifacts required."""
from __future__ import annotations
import copy
import hashlib
import json

import numpy as np
import pandas as pd
import pytest

from scripts import analyze_strategy_comparison as analysis
from guanlan_v2.workflow.daily_simulator import simulate_daily_portfolio
from guanlan_v2.workflow.history_price_quality import ADJUSTMENT_SCHEMA, history_price_fingerprint


def fixture():
    dates = pd.bdate_range("2024-12-02", periods=35)
    frame = pd.DataFrame({"datetime": dates, "instrument": "SH600001",
        "open": 100. + np.arange(35) * .2, "close": 100.1 + np.arange(35) * .2,
        "high": 101. + np.arange(35) * .2, "low": 99. + np.arange(35) * .2,
        "amount": 1_000_000., "volume": 10_000.}).set_index(["datetime", "instrument"])
    signals = pd.DataFrame({"eligible": True, "score": 1.}, index=frame.index)
    signals.loc[signals.index.get_level_values(0) >= dates[27], "eligible"] = False
    evaluation = dates[22:]
    params = {"topn": 1, "rebalance": "day", "weight_mode": "fixed_slots", "initial_cash": 1000.,
              "commission": .0003, "sell_tax": .0005, "slippage_bps": 5}
    payload = simulate_daily_portfolio(frame, signals, evaluation, top_n=1, initial_cash=1000.,
                                      signal_valid=pd.Series(True, index=evaluation))
    payload.update(history_artifact={"id": "hs_fixture", "digest": "fixture"},
                   backtest={"topn": 1}, screen_diagnostics={"invalid_calendar_dates": []})
    source = {"start": str(dates[0].date()), "end": str(dates[-1].date()),
              "universe": "csi800", "research_freq": "day"}
    sample = {"eval_start": str(evaluation[0].date()), "eval_end": str(evaluation[-1].date())}
    periods = [{"id": name, "start": str(evaluation[a].date()), "end": str(evaluation[b].date())}
               for name, a, b in [("development", 0, 3), ("validation", 4, 7), ("recent_descriptive", 8, 12)]]
    plan = {"common": {"source_params": source, "sample": sample, "periods": periods,
                       "daily_backtest_params": params},
            "variants": [{"id": f"v{i}", "name": f"variant{i}", "history_screen_params": {"branches": [{"test": i}]}}
                         for i in range(6)]}
    records, receipts = {}, []
    for variant in plan["variants"]:
        vid = variant["id"]
        records[vid] = {"id": f"wr_{vid}", "status": "completed", "workspace_id": "rw_general",
            "graph": {"nodes": [
                {"type": "source", "params": copy.deepcopy(source)},
                {"type": "history_screen", "params": {**variant["history_screen_params"], "label": variant["name"], "evaluation_start": sample["eval_start"]}},
                {"type": "daily_backtest", "params": copy.deepcopy(params)}]},
            "result": {"terminal": {"payload": copy.deepcopy(payload)}}}
        records[vid]["result"]["terminal"]["payload"]["history_artifact"]["id"] = "hs_" + vid
        receipts.append({"variant_id": vid, "run_id": f"wr_{vid}", "status": "completed", "workspace_id": "rw_general"})
    meta = {"calendar": [str(d.date()) for d in evaluation], "files": {"panel.parquet": "samepanel", "signals.parquet": "signal"},
            "evidence": {"context": source, "load": {"amount_unit": "yuan", "verified": True},
                         "universe": {"n_resolved": 1}, "metadata": {"strict_pit": False}}}
    meta["evidence"]["load"]["price_adjustment"] = {
        "schema": ADJUSTMENT_SCHEMA, "status": "verified", "method": "causal_cumulative_factor",
        "future_normalization": False, "price_fields": ["open", "high", "low", "close"],
        "volume_policy": "raw_share", "amount_policy": "raw_yuan", "source": "synthetic test only",
        "raw_price_sha256": "a" * 64, "factor_source_sha256": "b" * 64,
        **history_price_fingerprint(frame, dates),
        "factor_coverage": {"n_required": len(frame), "n_verified": len(frame), "n_missing": 0}}
    return payload, plan, records, receipts, frame, signals, meta, dates


def test_independent_cash_quantity_and_mark_reconstruction_passes():
    payload, *_ = fixture()
    assert analysis.audit_ledger(payload)["passed"]


@pytest.mark.parametrize("defect", ["cash", "quantity", "mark", "normalized", "trade_price", "cost", "summary", "nan"])
def test_independent_audit_rejects_corrupted_evidence(defect):
    payload, *_ = fixture()
    row = next(r for r in payload["daily"] if r["holdings"])
    if defect == "cash":
        row["cash"] += 1
    elif defect == "quantity":
        row["holdings"][0]["quantity"] += 1
    elif defect == "mark":
        row["holdings"][0]["mark"] += 1
    elif defect == "normalized":
        row["normalized_nav"] += .01
    elif defect == "trade_price":
        payload["trades"][0]["execution_price"] += 1
    elif defect == "cost":
        row["costs_paid"] += 1
    elif defect == "summary":
        payload["summary"]["total_return"] += .01
    else:
        payload["diagnostics"]["unrelated_nan"] = float("nan")
    with pytest.raises(ValueError):
        analysis.audit_ledger(payload)


def test_period_return_uses_prior_close_equity_not_segment_reset_or_stored_return():
    payload, *_ = fixture()
    rows = payload["daily"]
    first, last = rows[2]["date"], rows[6]["date"]
    expected = rows[6]["nav"] / rows[1]["nav"] - 1
    for row in rows:
        row["return"] = 999.  # period calculation must independently ignore this field.
    metric = analysis.period_metrics(payload, first, last)
    assert metric["net_total_return"] == pytest.approx(expected)
    assert metric["starting_equity_before_first_day"] == rows[1]["nav"]
    assert metric["boundary_policy"].startswith("continuous account")


def test_all_calendar_days_and_exit_day_stay_in_denominators():
    payload, *_ = fixture()
    metric = analysis.period_metrics(payload, payload["daily"][0]["date"], payload["daily"][-1]["date"])
    assert metric["n_days"] == len(payload["daily"])
    assert metric["mean_candidate_count"] == pytest.approx(np.mean([r["eligible_count"] for r in payload["candidates"]]))
    exit_row = next(row for i, row in enumerate(payload["daily"]) if i and not row["holdings_count"] and payload["daily"][i - 1]["holdings_count"])
    exit_metric = analysis.period_metrics(payload, exit_row["date"], exit_row["date"])
    assert exit_metric["noncash_days"] == 1
    assert exit_metric["cash_day_fraction"] == 0
    assert exit_metric["close_cash_day_fraction"] == 1


def test_phase_and_yearly_compounding_reconcile_with_boundary_holdings():
    payload, plan, *_ = fixture()
    periods = {p["id"]: analysis.period_metrics(payload, p["start"], p["end"]) for p in plan["common"]["periods"]}
    periods["full"] = analysis.period_metrics(payload, plan["common"]["sample"]["eval_start"], plan["common"]["sample"]["eval_end"])
    result = analysis.yearly_and_phase_consistency(payload, plan, periods)
    assert result["compound_reconciled"]
    assert set(result["yearly"]) == {"2025"}  # All13 evaluation days are in2025.
    periods["validation"]["net_total_return"] += .1
    with pytest.raises(ValueError, match="reconcile"):
        analysis.yearly_and_phase_consistency(payload, plan, periods)


def test_prior20_capacity_uses_fixed_calendar_and_never_current_amount():
    payload, _, _, _, panel, _, _, dates = fixture()
    original = analysis.capacity_proxy(payload, panel, dates)
    assert original["n_trades_with_proxy"] > 0
    first_trade_date = pd.Timestamp(payload["trades"][0]["date"])
    one = {**payload, "trades": [payload["trades"][0]], "daily": []}
    expected = analysis.capacity_proxy(one, panel, dates)["max_participation_proxy"]
    changed = panel.copy()
    changed.loc[(first_trade_date, "SH600001"), "amount"] = 1e12
    assert analysis.capacity_proxy(one, changed, dates)["max_participation_proxy"] == expected
    # Entire market date missing from panel, not merely a symbol's NaN row.
    missing = panel.drop(index=(dates[10], "SH600001"))
    result = analysis.capacity_proxy(one, missing, dates)
    assert result["n_trades_without_proxy"] == 1
    assert result["max_participation_proxy"] is None


@pytest.mark.parametrize("defect", ["missing_strategy", "duplicate_receipt", "not_completed", "same_run", "wrong_dates", "phase_gap", "wrong_rule", "wrong_cost", "nonfinite"])
def test_all_six_complete_same_periods_and_frozen_graphs_required(defect):
    _, plan, records, receipts, *_ = fixture()
    if defect == "missing_strategy":
        records.pop("v5")
    elif defect == "duplicate_receipt":
        receipts[-1] = copy.deepcopy(receipts[0])
    elif defect == "not_completed":
        records["v0"]["status"] = "completed_with_errors"
    elif defect == "same_run":
        receipts[1]["run_id"] = receipts[0]["run_id"]
    elif defect == "wrong_dates":
        plan["common"]["sample"]["eval_end"] = "2025-02-01"
    elif defect == "phase_gap":
        plan["common"]["periods"][1]["start"] = "2025-12-31"
    elif defect == "wrong_rule":
        records["v0"]["graph"]["nodes"][1]["params"]["label"] = "changed"
    elif defect == "wrong_cost":
        records["v0"]["graph"]["nodes"][2]["params"]["commission"] = 0
    else:
        records["v0"]["result"]["terminal"]["payload"]["summary"]["sharpe"] = float("inf")
    with pytest.raises(ValueError):
        analysis.validate_comparison_inputs(plan, receipts, records)


def test_six_fixture_records_and_artifact_sources_validate():
    payload, plan, records, receipts, panel, signals, meta, _ = fixture()
    assert len(analysis.validate_comparison_inputs(plan, receipts, records)) == len(payload["daily"])
    assert len(analysis.validate_artifact_source(plan, payload, panel, signals, meta)) == 35


@pytest.mark.parametrize("defect", ["future_source", "wrong_context", "invalid_code", "unknown_units", "count_mismatch", "calendar_gap"])
def test_actual_source_bounds_and_calendar_verified(defect):
    payload, plan, _, _, panel, signals, meta, _ = fixture()
    if defect == "future_source":
        plan["common"]["source_params"]["end"] = "2025-01-01"
    elif defect == "wrong_context":
        meta["evidence"]["context"] = {**meta["evidence"]["context"], "universe": "csi300"}
    elif defect == "invalid_code":
        panel = panel.rename(index={"SH600001": "unsafe"}, level=1)
    elif defect == "unknown_units":
        meta["evidence"]["load"]["verified"] = False
    elif defect == "count_mismatch":
        meta["evidence"]["universe"]["n_resolved"] = 0
    else:
        meta["calendar"] = meta["calendar"][1:]
    with pytest.raises(ValueError):
        analysis.validate_artifact_source(plan, payload, panel, signals, meta)


def test_cost_counterfactual_can_change_cash_but_not_signals_or_artifact():
    payload, _, _, _, panel, signals, meta, _ = fixture()
    counter = simulate_daily_portfolio(panel, signals, pd.DatetimeIndex(meta["calendar"]), top_n=1,
        initial_cash=1000., commission=0, stamp_tax=0, slippage_bps=0,
        signal_valid=pd.Series(True, index=pd.DatetimeIndex(meta["calendar"])))
    counter["history_artifact"] = payload["history_artifact"]
    analysis.validate_counterfactual(payload, counter)
    assert counter["summary"]["total_return"] != payload["summary"]["total_return"]
    counter["candidates"][0]["selected"] = []
    with pytest.raises(ValueError, match="selections"):
        analysis.validate_counterfactual(payload, counter)


def test_price_profile_flags_without_excluding_suspicious_quotes():
    _, _, _, _, panel, _, _, dates = fixture()
    panel.loc[(dates[5], "SH600001"), "open"] *= 10
    profile = analysis.source_price_profile(panel, dates)
    assert profile["overnight_ratio_outside_075_125"] == 1
    assert len(panel) == 35
    json.dumps(profile, allow_nan=False)


def test_different_panel_snapshots_never_publish_partial_comparison(tmp_path, monkeypatch):
    _, plan, records, receipts, panel, signals, meta, _ = fixture()
    plan_bytes = json.dumps(plan).encode()
    (tmp_path / "experiment_plan.json").write_bytes(plan_bytes)
    (tmp_path / "workflow_receipts.json").write_text(json.dumps(receipts))
    (tmp_path / "runs").mkdir()
    for vid, record in records.items():
        (tmp_path / "runs" / (vid + ".json")).write_text(json.dumps(record))
    class FakeStore:
        def __init__(self, *_):
            pass
        def load(self, handle, **_):
            result = copy.deepcopy(meta)
            if handle["id"] == "hs_v5":
                result["files"]["panel.parquet"] = "different"
            return panel, signals, result
    from guanlan_v2.workflow import strategy_replay
    monkeypatch.setattr(strategy_replay, "HistoryArtifactStore", FakeStore)
    monkeypatch.setattr(analysis, "OUT", tmp_path)
    monkeypatch.setattr(analysis, "EXPECTED_PLAN_SHA256", hashlib.sha256(plan_bytes).hexdigest())
    with pytest.raises(ValueError, match="different source"):
        analysis.main()
    assert not (tmp_path / "comparison.json").exists()


@pytest.mark.parametrize("quality_ok", [True, False])
def test_full_six_variant_comparison_publishes_gross_and_shared_quality_fields(tmp_path, monkeypatch, quality_ok):
    _, plan, records, receipts, panel, signals, meta, _ = fixture()
    if not quality_ok:
        meta["evidence"]["load"].pop("price_adjustment")
    plan_bytes = json.dumps(plan).encode()
    (tmp_path / "experiment_plan.json").write_bytes(plan_bytes)
    (tmp_path / "workflow_receipts.json").write_text(json.dumps(receipts))
    (tmp_path / "runs").mkdir()
    for vid, record in records.items():
        (tmp_path / "runs" / (vid + ".json")).write_text(json.dumps(record))
    class FakeStore:
        def __init__(self, *_):
            pass
        def load(self, *_args, **_kwargs):
            return panel, signals, copy.deepcopy(meta)
    def counter(history, settings, **_):
        assert quality_ok, "Uncertified source must never enter cost sensitivity comparison"
        calendar = pd.DatetimeIndex(meta["calendar"])
        result = simulate_daily_portfolio(panel, signals, calendar,
            top_n=settings["topn"], initial_cash=settings["initial_cash"],
            commission=settings["commission"], stamp_tax=settings["sell_tax"],
            slippage_bps=settings["slippage_bps"], signal_valid=pd.Series(True, index=calendar))
        result["history_artifact"] = history["history_artifact"]
        return result
    from guanlan_v2.workflow import strategy_replay
    monkeypatch.setattr(strategy_replay, "HistoryArtifactStore", FakeStore)
    monkeypatch.setattr(strategy_replay, "run_daily_backtest", counter)
    monkeypatch.setattr(analysis, "OUT", tmp_path)
    monkeypatch.setattr(analysis, "EXPECTED_PLAN_SHA256", hashlib.sha256(plan_bytes).hexdigest())
    analysis.main()
    result = json.loads((tmp_path / "comparison.json").read_text(encoding="utf-8"))
    assert result["all_six_complete"] and result["shared_panel_verified"]
    assert len(result["strategies"]) == 6
    if not quality_ok:
        assert result["status"] == "diagnostics_only" and not result["performance_ready"]
        assert result["diagnostics"]["blocking_reasons"]
        assert all(set(row) == {"id", "name", "receipt", "status"} for row in result["strategies"])
        return
    for row in result["strategies"]:
        assert row["periods"]["full"]["gross_total_return"] == row["cost_sensitivity"]["zero_cost"]["full"]["net_total_return"]
        assert row["yearly_or_segment_consistency"]["compound_reconciled"]
        assert "has_stale_marks" in row["quality_flags"]
    json.dumps(result, allow_nan=False)
