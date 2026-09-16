# -*- coding: utf-8 -*-
"""Closed rolling_max screening contract, tested on synthetic observations."""
from __future__ import annotations

import pandas as pd
import pytest

from financial_analyst.factors.zoo.panel import PanelData
from guanlan_v2.screen import data_screen as ds


def _high10():
    return {"type": "rolling_max", "field": "high", "window": 10}


def _pullback_rule():
    return {
        "id": "pullback", "type": "range",
        "value": {"type": "bias", "field": "close", "baseline": _high10()},
        "min": -1, "max": -0.01, "inclusive": True,
    }


def _request(rules=None, **extra):
    return {
        "universe": "csi300", "as_of": "2026-01-16",
        "selector": {"conditions": rules or [_pullback_rule()]},
        "filters": {"exclude_st": False, "require_asof_trade": True,
                    "min_avg_amount_yuan": 0, "amount_window": 1,
                    "amount_min_observations": 1},
        **extra,
    }


def _frame(highs, closes=None):
    return pd.DataFrame({
        "high": highs, "close": closes if closes is not None else [99.0] * len(highs),
    }, index=pd.bdate_range("2026-01-05", periods=len(highs)))


def _panel(highs_by_code, closes_by_code=None):
    frames = []
    for code, highs in highs_by_code.items():
        closes = None if closes_by_code is None else closes_by_code[code]
        frame = _frame(highs, closes)
        frame.index.name = "datetime"
        frame["open"] = frame["close"]
        frame["low"] = frame["close"] * 0.99
        frame["volume"] = 1_000_000.0
        frame["amount"] = 100_000_000.0
        frame["code"] = code
        frames.append(frame.reset_index().set_index(["datetime", "code"]))
    return PanelData(pd.concat(frames))


def _evaluate(panel, request=None):
    spec = ds.compile_data_screen_spec(request or _request())
    codes = list(panel.codes())
    return ds.evaluate_data_screen(
        panel, spec,
        universe_meta={"id": "synthetic", "n_resolved": len(codes), "strict_pit": True},
        metadata={code: {"name": "synthetic"} for code in codes},
        metadata_provenance={"available": True, "strict_pit": True},
        load_meta={"amount_unit": "yuan", "verified": True},
    )


def test_rolling_max_normalises_inside_bias_compare_both_sides_and_score():
    compare = {"id": "compare", "type": "compare", "left": _high10(),
               "op": ">=", "right": _high10()}
    spec = ds.compile_data_screen_spec(_request(
        [_pullback_rule(), compare],
        score={"type": "series", "value": _high10(), "direction": "asc"},
    ))
    assert spec["rules"][0]["value"]["baseline"] == _high10()
    assert spec["rules"][1]["left"] == spec["rules"][1]["right"] == _high10()
    assert spec["score"]["value"] == _high10()
    assert ds._required_bars(spec) == 10


@pytest.mark.parametrize("field", ["open", "high", "low", "close", "volume", "amount", "returns"])
@pytest.mark.parametrize("window", [2, 10, 250, 10.0])
def test_rolling_max_accepts_only_existing_fields_and_bounded_integer_windows(field, window):
    result = ds._normalise_series_expr({"type": "rolling_max", "field": field, "window": window})
    assert result == {"type": "rolling_max", "field": field, "window": int(window)}
    assert type(result["window"]) is int


@pytest.mark.parametrize("change", [
    {"window": 0}, {"window": 1}, {"window": -10}, {"window": 251},
    {"window": True}, {"window": "10"}, {"window": None}, {"window": 2.5},
    {"window": float("nan")}, {"window": float("inf")},
    {"field": "future_close"}, {"field": "__import__('os')"}, {"field": None},
    {"field": {"type": "field", "field": "high"}},
    {"min_periods": 1}, {"center": True}, {"shift": -1},
    {"source": {"type": "field", "field": "high"}}, {"expr": "high.max()"},
])
def test_rolling_max_rejects_bad_inputs_and_semantic_overrides(change):
    with pytest.raises(ds.DataScreenSpecError):
        ds._normalise_series_expr({**_high10(), **change})


@pytest.mark.parametrize("missing", ["field", "window"])
def test_rolling_max_requires_field_and_window(missing):
    raw = _high10()
    del raw[missing]
    with pytest.raises(ds.DataScreenSpecError):
        ds._normalise_series_expr(raw)


def test_rolling_max_waits_for_full_window_and_drops_expired_high():
    # 200 is outside the last ten observations at the final bar.
    series = ds._resolve_series(_frame([200.0] + [100.0] * 10), _high10())
    assert series.iloc[:9].isna().all()
    assert series.iloc[9] == 200.0
    assert series.iloc[10] == 100.0


def test_rolling_max_missing_values_are_not_filled_or_skipped_to_older_bars():
    series = ds._resolve_series(_frame([200.0, None] + [100.0] * 10), _high10())
    assert series.iloc[:11].isna().all()
    assert series.iloc[11] == 100.0
    absent_high = ds._resolve_series(_frame([100.0] * 10).drop(columns="high"), _high10())
    assert absent_high.isna().all()


def test_current_bar_high_is_included_without_an_implicit_one_bar_lag():
    frame = _frame([100.0] * 9 + [110.0], [99.0] * 9 + [100.0])
    expr = _pullback_rule()["value"]
    assert ds._resolve_series(frame, expr).iloc[-1] == pytest.approx(100.0 / 110.0 - 1)
    passed, evidence = ds._rule_result(frame, ds._normalise_rule(_pullback_rule(), 0))
    assert passed is True
    assert evidence["value"] == pytest.approx(100.0 / 110.0 - 1)


def test_exact_one_percent_pullback_passes_and_a_smaller_pullback_fails():
    panel = _panel(
        {"SH600001": [100.0] * 10, "SH600002": [100.0] * 10},
        {"SH600001": [99.0] * 10, "SH600002": [99.00001] * 10},
    )
    result = _evaluate(panel)
    assert [row["code"] for row in result["candidates"]] == ["SH600001"]
    assert result["candidates"][0]["evidence"]["pullback"]["value"] == pytest.approx(-0.01)
    assert result["rejection_counts"]["rule:pullback"] == 1


def test_incomplete_ten_bar_window_cannot_produce_a_passing_rule():
    passed, evidence = ds._rule_result(
        _frame([100.0] * 9), ds._normalise_rule(_pullback_rule(), 0),
    )
    assert passed is False
    assert evidence["value"] is None


def test_zero_baseline_is_not_a_passing_finite_bias():
    passed, evidence = ds._rule_result(
        _frame([0.0] * 10), ds._normalise_rule(_pullback_rule(), 0),
    )
    assert passed is False
    assert evidence["value"] is None


def test_returns_rolling_max_requires_and_hashes_the_hidden_prior_close():
    expr = {"type": "rolling_max", "field": "returns", "window": 10}
    rule = {"id": "max_return", "type": "range", "value": expr,
            "min": -1, "max": 10}
    spec = ds.compile_data_screen_spec(_request([rule]))
    assert ds._required_bars(spec) == 11
    frame = _frame([100.0] * 11, [50.0] + [100.0] * 10)
    series = ds._resolve_series(frame, expr)
    assert series.iloc[:10].isna().all()
    assert series.iloc[-1] == 1.0
    request = _request([rule], as_of="2026-01-19")
    before = _evaluate(_panel({"SH600001": [100.0] * 11},
                             {"SH600001": [50.0] + [100.0] * 10}), request)
    after = _evaluate(_panel({"SH600001": [100.0] * 11},
                            {"SH600001": [100.0] * 11}), request)
    assert before["data_snapshot"]["digest"] != after["data_snapshot"]["digest"]
    assert before["candidates"][0]["evidence"]["max_return"]["value"] == 1.0
    assert after["candidates"][0]["evidence"]["max_return"]["value"] == 0.0


@pytest.mark.parametrize("placement,expected", [("range", 10), ("strict_rise", 13),
                                                ("max_consecutive", 14), ("score", 10)])
def test_warmup_propagates_through_every_supported_consumer(placement, expected):
    if placement == "range":
        request = _request()
    elif placement == "strict_rise":
        request = _request([{"id": "rise", "type": "strict_rise", "series": _high10(),
                             "transitions": 3}])
    elif placement == "max_consecutive":
        request = _request([{"id": "streak", "type": "max_consecutive", "lookback": 5,
                             "max_count": 4, "predicate": {"type": "compare",
                             "left": {"type": "field", "field": "close"}, "op": "<",
                             "right": _high10()}}])
    else:
        request = _request([{"id": "close", "type": "range",
                             "value": {"type": "field", "field": "close"}, "min": 0, "max": 1e5}],
                           score={"type": "series", "value": _high10()})
    assert ds._required_bars(ds.compile_data_screen_spec(request)) == expected


def test_rolling_max_is_prefix_causal_and_future_bars_do_not_change_asof_results():
    prefix = _frame([100.0] * 10)
    future = _frame([100.0] * 10 + [99999.0, 0.001], [99.0] * 10 + [80000.0, 0.0009])
    pd.testing.assert_series_equal(
        ds._resolve_series(prefix, _high10()),
        ds._resolve_series(future, _high10()).iloc[:10],
    )
    before = _evaluate(_panel({"SH600001": [100.0] * 10}))
    after = _evaluate(_panel({"SH600001": [100.0] * 10 + [99999.0, 0.001]},
                            {"SH600001": [99.0] * 10 + [80000.0, 0.0009]}))
    assert before["candidates"] == after["candidates"]
    assert before["data_snapshot"]["digest"] == after["data_snapshot"]["digest"]


def test_rolling_mean_behavior_is_unchanged():
    frame = _frame([100.0, 20.0, 30.0, 40.0])
    mean = ds._resolve_series(frame, {"type": "rolling_mean", "field": "high", "window": 2})
    expected = frame["high"].rolling(window=2, min_periods=2).mean()
    pd.testing.assert_series_equal(mean, expected)
