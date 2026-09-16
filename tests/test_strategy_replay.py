import copy
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from guanlan_v2.workflow.strategy_replay import HistoryArtifactStore, run_history_screen, run_daily_backtest, validate_history_params
from guanlan_v2.workflow.history_price_quality import (
    ADJUSTMENT_SCHEMA, HistoryPriceQualityError, history_price_fingerprint,
)


def synthetic_certificate(frame, dates):
    fields = ["open", "high", "low", "close"]
    required = int((np.isfinite(frame[fields]) & frame[fields].gt(0)).any(axis=1).sum())
    return {"schema": ADJUSTMENT_SCHEMA, "status": "verified", "method": "causal_cumulative_factor",
            "future_normalization": False, "price_fields": fields,
            "volume_policy": "raw_share", "amount_policy": "raw_yuan", "source": "synthetic trusted test loader",
            "raw_price_sha256": "a" * 64, "factor_source_sha256": "b" * 64,
            **history_price_fingerprint(frame, dates),
            "factor_coverage": {"n_required": required, "n_verified": required, "n_missing": 0}}


def inputs():
    dates = pd.bdate_range("2025-01-01", periods=80)
    frame = pd.DataFrame({"datetime": dates, "code": "SH600001", "open": 100 + np.arange(80),
        "close": 101 + np.arange(80), "high": 102 + np.arange(80), "low": 99 + np.arange(80),
        "volume": 1e6, "amount": 1e8}).set_index(["datetime", "code"])
    request = {"universe": "csi800", "top_n": 200, "selector": {"conditions": [
        {"type": "range", "id": "price", "value": {"type": "field", "field": "close"}, "min": 1, "max": 1000}]},
        "filters": {"exclude_st": False, "amount_window": 20, "amount_min_observations": 20, "min_avg_amount_yuan": 5e7}}
    params = {"branches": [request], "evaluation_start": str(dates[55].date())}
    context = {"universe": "csi800", "start": str(dates[0].date()), "end": str(dates[-1].date()), "research_freq": "day"}
    loaded = (frame, dates, {"n_resolved": 1, "strict_pit": False}, {}, {},
              {"amount_unit": "yuan", "verified": True, "price_adjustment": synthetic_certificate(frame, dates)})
    return params, context, loaded


def test_history_to_cash_ledger_persists_and_verifies(tmp_path):
    params, context, loaded = inputs()
    store = HistoryArtifactStore(tmp_path)
    result = run_history_screen(params, context, loaded=loaded, store=store, owner="rw_general")
    assert result["n_dates"] == 25
    assert result["latest_candidates"][0]["code"] == "SH600001"
    simulated = run_daily_backtest(result, {"topn": 10}, store=store, owner="rw_general")
    assert simulated["method"] == "daily_screen_backtest"
    assert len(simulated["portfolio"]["nav_series"]) == 25
    assert simulated["daily"][0]["cash"] == 1_000_000
    assert simulated["summary"]["average_cash_weight"] > .85
    assert simulated["summary"]["total_return"] > 0
    assert result["price_quality"]["performance_ready"]
    assert simulated["price_quality"]["performance_ready"]
    json.dumps(simulated, allow_nan=False)
    with pytest.raises((ValueError, FileNotFoundError)):
        store.load(result["history_artifact"], owner=None)
    with pytest.raises((ValueError, FileNotFoundError)):
        store.load(result["history_artifact"], owner="rw_aaaaaaaaaaaa")
    file = tmp_path / "rw_general" / result["history_artifact"]["id"] / "signals.parquet"
    with file.open("ab") as stream:
        stream.write(b"corrupt")
    with pytest.raises(ValueError, match="被修改"):
        run_daily_backtest(result, {}, store=store, owner="rw_general")


@pytest.mark.parametrize("id", ["../x", "hs_", "hs_" + "g" * 64, "G:/data"])
def test_artifact_path_traversal_rejected(tmp_path, id):
    with pytest.raises(ValueError):
        HistoryArtifactStore(tmp_path).load({"id": id})


def test_snapshot_candidates_cannot_masquerade_as_history(tmp_path):
    with pytest.raises(ValueError, match="history_screen"):
        run_daily_backtest({"__dt": "data_screen", "codes": ["SH600001"]}, {}, store=HistoryArtifactStore(tmp_path))


@pytest.mark.parametrize("mutate", [
    lambda p, c: p.update(ranking="eval(x)"),
    lambda p, c: p.update(unknown=True),
    lambda p, c: p.update(branches=p["branches"] * 9),
    lambda p, c: c.update(research_freq="week"),
    lambda p, c: c.update(start="2026-01-01"),
    lambda p, c: c.update(universe="all_a"),
    lambda p, c: p["branches"][0].update(universe="csi300"),
])
def test_history_contract_fail_closed(mutate):
    params, context, _ = inputs()
    mutate(params, context)
    with pytest.raises(ValueError):
        validate_history_params(params, context)


@pytest.mark.parametrize("change", [
    {"as_of": "2020-01-01"}, {"end": "2020-01-01"}, {"freq": "minute"},
    {"score": {"type": "series", "value": {"type": "field", "field": "close"}}},
])
def test_branch_source_overrides_are_rejected_instead_of_silently_rewritten(change):
    params, context, _ = inputs()
    params["branches"][0].update(change)
    with pytest.raises(ValueError):
        validate_history_params(params, context)


def test_or_filter_mismatch_fails_during_validation_before_any_load():
    params, context, _ = inputs()
    second = copy.deepcopy(params["branches"][0])
    second["filters"]["amount_window"] = 21
    params["branches"].append(second)
    with pytest.raises(ValueError, match="filters"):
        validate_history_params(params, context)


def test_preset_implicit_score_conflict_fails_before_loading():
    params, context, _ = inputs()
    params["branches"][0]["selector"] = {"preset": "ma_trend", "params": {}}
    with pytest.raises(ValueError, match="隐含 score"):
        validate_history_params(params, context)


def test_injected_and_exact_source_panel_have_identical_warmup_and_artifact(tmp_path):
    params, context, loaded = inputs()
    panel, calendar, *meta = loaded
    context["start"] = str(calendar[30].date())
    clipped = (panel.loc[panel.index.get_level_values(0) >= calendar[30]], calendar[30:], *meta)
    # Trusted adapter certifies the actual source interval, not the reusable
    # superset. Both injected paths are clipped to these exact bytes/dates.
    meta[-1]["price_adjustment"] = synthetic_certificate(clipped[0], clipped[1])
    store = HistoryArtifactStore(tmp_path)
    broad = run_history_screen(params, context, loaded=loaded, store=store)
    exact = run_history_screen(params, context, loaded=clipped, store=store)
    assert broad["history_artifact"] == exact["history_artifact"]
    stored_panel, signals, _ = store.load(broad["history_artifact"])
    assert stored_panel.index.get_level_values(0).min() == calendar[30]
    assert not signals.iloc[:19]["eligible"].any()


def test_injected_universe_mismatch_and_extra_explicit_code_fail(tmp_path):
    params, context, loaded = inputs()
    replaced = list(loaded)
    replaced[2] = {**loaded[2], "id": "csi300"}
    with pytest.raises(ValueError, match="股票池"):
        run_history_screen(params, context, loaded=tuple(replaced), store=HistoryArtifactStore(tmp_path))
    context["codes"] = ["SH600002"]
    params["branches"][0]["codes"] = context["codes"]
    with pytest.raises(ValueError, match="source.codes"):
        run_history_screen(params, context, loaded=loaded, store=HistoryArtifactStore(tmp_path))


def test_short_history_eligible_but_unrankable_candidate_is_strict_json(tmp_path):
    params, context, loaded = inputs()
    panel, calendar, *meta = loaded
    panel, calendar = panel.iloc[:5], calendar[:5]
    context["end"] = str(calendar[-1].date())
    params["evaluation_start"] = context["start"]
    params["branches"][0]["filters"].update(amount_window=1, amount_min_observations=1)
    meta[-1]["price_adjustment"] = synthetic_certificate(panel, calendar)
    store = HistoryArtifactStore(tmp_path)
    result = run_history_screen(params, context, loaded=(panel, calendar, *meta), store=store)
    assert result["latest_candidates"] == [{"code": "SH600001", "score": None}]
    assert result["latest_unrankable_candidates"] == 1
    json.dumps(result, allow_nan=False)
    simulated = run_daily_backtest(result, {}, store=store)
    assert simulated["summary"]["trade_count"] == 0
    assert simulated["summary"]["average_cash_weight"] == 1
    json.dumps(simulated, allow_nan=False)


def test_immutable_old_run_remains_readable_after_source_data_changes(tmp_path):
    params, context, loaded = inputs()
    store = HistoryArtifactStore(tmp_path)
    first = run_history_screen(params, context, loaded=loaded, store=store)
    old_panel, old_signals, old_meta = store.load(first["history_artifact"])
    fields = ["open", "high", "low", "close"]
    loaded[0][fields] = loaded[0][fields] * .5
    loaded[-1]["price_adjustment"] = synthetic_certificate(loaded[0], loaded[1])
    second = run_history_screen(params, context, loaded=loaded, store=store)
    assert first["history_artifact"] != second["history_artifact"]
    again_panel, again_signals, again_meta = store.load(first["history_artifact"])
    pd.testing.assert_frame_equal(old_panel, again_panel)
    pd.testing.assert_frame_equal(old_signals, again_signals)
    assert old_meta == again_meta
    assert run_daily_backtest(first, {}, store=store)["history_artifact"] == first["history_artifact"]


def test_artifact_handle_schema_is_checked(tmp_path):
    params, context, loaded = inputs()
    store = HistoryArtifactStore(tmp_path)
    result = run_history_screen(params, context, loaded=loaded, store=store)
    handle = {**result["history_artifact"], "schema": "legacy-or-arbitrary"}
    with pytest.raises(ValueError, match="schema"):
        store.load(handle)


@pytest.mark.parametrize("change", ["numeric_validity", "short_validity", "duplicate_calendar", "nan_evidence"])
def test_artifact_save_validates_calendar_and_provenance_before_publication(tmp_path, change):
    params, context, loaded = inputs()
    store = HistoryArtifactStore(tmp_path)
    result = run_history_screen(params, context, loaded=loaded, store=store)
    panel, signals, meta = store.load(result["history_artifact"])
    calendar, valid, evidence = pd.DatetimeIndex(meta["calendar"]), meta["signal_valid"], meta["evidence"]
    if change == "numeric_validity":
        valid = [1] * len(calendar)
    elif change == "short_validity":
        valid = valid[:-1]
    elif change == "duplicate_calendar":
        calendar = calendar.insert(0, calendar[0])
        valid = [True] + valid
    else:
        evidence = {**evidence, "bad": float("nan")}
    before = set(tmp_path.rglob("meta.json"))
    with pytest.raises(ValueError):
        store.save(panel, signals, calendar=calendar, signal_valid=valid, evidence=evidence)
    assert set(tmp_path.rglob("meta.json")) == before


def test_namespace_lookup_does_not_create_new_owner_folder(tmp_path):
    params, context, loaded = inputs()
    store = HistoryArtifactStore(tmp_path)
    result = run_history_screen(params, context, loaded=loaded, store=store)
    with pytest.raises(FileNotFoundError):
        store.load(result["history_artifact"], owner="rw_general")
    assert not (tmp_path / "rw_general").exists()


def test_linked_ancestor_is_rejected_without_following_it(tmp_path, monkeypatch):
    from guanlan_v2.workflow import strategy_replay as replay
    monkeypatch.setattr(replay, "_is_link", lambda path: path == tmp_path)
    params, context, loaded = inputs()
    with pytest.raises(ValueError, match="链接"):
        run_history_screen(params, context, loaded=loaded, store=HistoryArtifactStore(tmp_path / "nested"))
    assert not (tmp_path / "nested").exists()


def test_uncertified_new_history_fails_before_persisting_an_artifact(tmp_path):
    params, context, loaded = inputs()
    loaded[-1].pop("price_adjustment")
    with pytest.raises(HistoryPriceQualityError) as caught:
        run_history_screen(params, context, loaded=loaded, store=HistoryArtifactStore(tmp_path))
    assert "uncertified_price_adjustment" in {b["code"] for b in caught.value.diagnostics["hard_blockers"]}
    assert not list(tmp_path.rglob("meta.json"))


def test_legacy_artifact_without_certificate_remains_readable_but_cannot_simulate(tmp_path):
    params, context, loaded = inputs()
    store = HistoryArtifactStore(tmp_path)
    result = run_history_screen(params, context, loaded=loaded, store=store)
    panel, signals, meta = store.load(result["history_artifact"])
    meta["evidence"]["load"].pop("price_adjustment")
    # A cached older success flag is not a fresh source certificate.
    meta["evidence"]["price_quality"] = {"performance_ready": True}
    legacy = store.save(panel, signals, calendar=pd.DatetimeIndex(meta["calendar"]),
                        signal_valid=meta["signal_valid"], evidence=meta["evidence"])
    assert store.load(legacy)[2]["evidence"]["price_quality"]["performance_ready"]
    with pytest.raises(HistoryPriceQualityError):
        run_daily_backtest({"__dt": "screen_history", "history_artifact": legacy}, {}, store=store)
    # Valid old immutable artifact still works; diagnostic artifact wasn't overwritten.
    assert run_daily_backtest(result, {}, store=store)["price_quality"]["performance_ready"]


@pytest.mark.parametrize("location", ["params", "context", "branch"])
def test_user_params_or_source_context_cannot_supply_adjustment_certificate(tmp_path, location):
    params, context, loaded = inputs()
    certificate = loaded[-1].pop("price_adjustment")
    target = params if location == "params" else context if location == "context" else params["branches"][0]
    target["price_adjustment"] = certificate
    with pytest.raises(ValueError):
        run_history_screen(params, context, loaded=loaded, store=HistoryArtifactStore(tmp_path))
    assert not list(tmp_path.rglob("meta.json"))


def test_bad_source_prices_are_blocked_even_with_fresh_hash_bound_certificate(tmp_path):
    params, context, loaded = inputs()
    loaded[0].loc[loaded[0].index[-1], "high"] = 1
    loaded[-1]["price_adjustment"] = synthetic_certificate(loaded[0], loaded[1])
    with pytest.raises(HistoryPriceQualityError) as caught:
        run_history_screen(params, context, loaded=loaded, store=HistoryArtifactStore(tmp_path))
    assert "impossible_OHLC" in {b["code"] for b in caught.value.diagnostics["hard_blockers"]}


def test_frozen_graphs_authoring_and_ports():
    from guanlan_v2.workflow.authoring import validate_authoring
    params, context, _ = inputs()
    # Keep unit coverage self-contained; the CLI separately validates all six
    # frozen experiment graphs before executing that saved local plan.
    for branches in (params["branches"], params["branches"] * 2):
        graph = {"nodes": [
            {"id": "src", "type": "source", "params": context},
            {"id": "hs", "type": "history_screen", "params": {**params, "branches": branches}},
            {"id": "bt", "type": "daily_backtest", "params": {"topn": 10, "rebalance": "day"}}],
            "edges": [{"from": ["src", "data"], "to": ["hs", "src"]}, {"from": ["hs", "history"], "to": ["bt", "history"]}]}
        result = validate_authoring(graph)
        assert result["ok"], result
        wrong = copy.deepcopy(graph)
        wrong["nodes"][1]["type"] = "data_screen"
        wrong["edges"][1]["from"][1] = "candidates"
        assert not validate_authoring(wrong)["ok"]
