# -*- coding: utf-8 -*-
"""Fixed-calendar replay parity and causality, using only synthetic quotes."""
from __future__ import annotations

import itertools

import numpy as np
import pandas as pd
import pytest

from guanlan_v2.screen import data_screen as ds
from guanlan_v2.screen import historical_screen as hs


def _field(name="close"):
    return {"type": "field", "field": name}


def _ma(window):
    return {"type": "rolling_mean", "field": "close", "window": window}


def _panel(n=90, codes=("SH600001", "SH600002")):
    dates = pd.bdate_range("2025-01-02", periods=n)
    frames = []
    for i, code in enumerate(codes):
        close = 100.0 + np.arange(n) * 0.15 + i
        frame = pd.DataFrame({"open": close * .998, "high": close * 1.015,
                              "low": close * .995, "close": close,
                              "volume": 1_000_000., "amount": 120_000_000.}, index=dates)
        frame["code"] = code
        frames.append(frame.set_index("code", append=True))
    panel = pd.concat(frames).sort_index()
    panel.index.names = ["datetime", "code"]
    return panel, dates


def _request(rules=None, **overrides):
    return {
        "universe": "csi800", "top_n": 200,
        "selector": {"conditions": rules or [{"type": "range", "id": "price",
                                              "value": _field(), "min": 1, "max": 1000}]},
        "filters": {"exclude_st": False, "require_asof_trade": True,
                    "min_avg_amount_yuan": 50_000_000, "amount_window": 20,
                    "amount_min_observations": 20},
        **overrides,
    }


def _trend(window):
    rules = [{"type": "compare", "id": f"order_{a}_{b}", "left": _ma(a),
              "op": ">", "right": _ma(b)} for a, b in [(5, 10), (10, 20), (20, 50)]]
    rules += [{"type": "strict_rise", "id": f"rise_{n}", "series": _ma(n),
               "transitions": 3} for n in (5, 10, 20, 50)]
    rules += [{"type": "compare", "id": "above50", "left": _field(),
               "op": ">=", "right": _ma(50)},
              {"type": "range", "id": "pullback", "value": {"type": "bias", "field": "close",
               "baseline": {"type": "rolling_max", "field": "high", "window": 10}},
               "min": -1, "max": -.01},
              {"type": "range", "id": "proximity", "value": {"type": "bias", "field": "close",
               "baseline": _ma(window)}, "min": -.03, "max": .03}]
    return _request(rules)


def _kwargs(panel):
    codes = panel.index.get_level_values("code").unique()
    return {"load_meta": {"amount_unit": "yuan", "verified": True},
            "universe_meta": {"id": "synthetic", "n_resolved": len(codes), "strict_pit": True},
            "metadata": {code: {"name": "Synthetic"} for code in codes},
            "metadata_provenance": {"available": True, "strict_pit": True}}


def test_complete_panel_last_day_matches_existing_strict_trend_branches():
    panel, dates = _panel()
    requests = [_trend(10), _trend(20)]
    result = hs.evaluate_historical_screen(panel, requests, calendar=dates, **_kwargs(panel))
    union = set()
    for i, request in enumerate(requests, 1):
        latest = ds.evaluate_data_screen(panel, ds.compile_data_screen_spec({
            **request, "as_of": dates[-1].date().isoformat(),
        }), **_kwargs(panel))
        expected = {row["code"] for row in latest["candidates"]}
        assert expected  # Prevent vacuous all-empty parity.
        assert set(result.branch_eligibility[f"branch_{i}"].columns[
            result.branch_eligibility[f"branch_{i}"].iloc[-1]]) == expected
        union |= expected
    assert set(result.eligibility.columns[result.eligibility.iloc[-1]]) == union
    assert result.eligibility.iloc[:52].sum().sum() == 0
    assert result.diagnostics["required_bars"] == 53


def test_prefix_invariance_including_coverage_and_named_score():
    panel, dates = _panel(90)
    prefix = panel.loc[panel.index.get_level_values("datetime") <= dates[69]]
    earlier = hs.evaluate_historical_screen(prefix, [_trend(10), _trend(20)], calendar=dates[:70],
                                           rank="nearest_ma10_ma20", **_kwargs(panel))
    panel.loc[panel.index.get_level_values("datetime") > dates[69], "close"] = 1_000_000.
    later = hs.evaluate_historical_screen(panel, [_trend(10), _trend(20)], calendar=dates,
                                         rank="nearest_ma10_ma20", **_kwargs(panel))
    pd.testing.assert_frame_equal(earlier.eligibility, later.eligibility.iloc[:70])
    pd.testing.assert_frame_equal(earlier.scores, later.scores.iloc[:70])
    pd.testing.assert_series_equal(earlier.calendar_valid, later.calendar_valid.iloc[:70])


def test_missing_fixed_calendar_observation_is_not_compressed_out_of_window():
    panel, dates = _panel(8, codes=("SH600001",))
    panel = panel.drop(index=(dates[5], "SH600001"))
    request = _request([{"type": "strict_rise", "id": "rise", "series": _ma(2), "transitions": 1}],
                       filters={"exclude_st": False, "min_avg_amount_yuan": 0,
                                "amount_window": 1, "amount_min_observations": 1})
    result = hs.evaluate_historical_screen(panel, request, calendar=dates, **_kwargs(panel))
    assert bool(result.eligibility.loc[dates[4], "SH600001"])
    assert not result.eligibility.loc[dates[5]:, "SH600001"].any()
    assert not result.calendar_valid.loc[dates[5]]
    assert result.diagnostics["invalid_calendar_dates"] == [dates[5].date().isoformat()]
    assert result.diagnostics["missing_rows_by_code"] == {"SH600001": [dates[5].date().isoformat()]}
    assert result.diagnostics["invalid_bars_by_code"] == result.diagnostics["missing_rows_by_code"]


def test_strict_rise_requires_every_adjacent_increase_not_endpoint_only():
    panel, dates = _panel(6, codes=("SH600001",))
    panel["close"] = [1., 2., 3., 2.9, 4., 5.]
    request = _request([{"type": "strict_rise", "id": "rise", "series": _field(), "transitions": 3}],
                       filters={"exclude_st": False, "amount_window": 1,
                                "amount_min_observations": 1, "min_avg_amount_yuan": 0})
    result = hs.evaluate_historical_screen(panel, request, calendar=dates, **_kwargs(panel))
    assert not result.eligibility.iloc[-1, 0]
    assert panel["close"].iloc[-1] > panel["close"].iloc[-4]


@pytest.mark.parametrize("maximum", range(6))
def test_vectorized_max_consecutive_matches_scalar_for_every_binary_pattern(maximum):
    for bits in itertools.product((False, True), repeat=7):
        frame = pd.DataFrame({"close": [0.5 if bit else 2. for bit in bits], "open": 1.})
        rule = ds._normalise_rule({"type": "max_consecutive", "lookback": 5,
                                  "max_count": maximum, "predicate": {"type": "compare",
                                  "left": _field(), "right": _field("open"), "op": "<"}}, 0)
        resolve = lambda expr: ds._resolve_series(frame, expr)
        actual = hs._rule_mask(rule, resolve)
        for i in range(len(frame)):
            assert bool(actual.iloc[i]) == ds._rule_result(frame.iloc[:i + 1], rule)[0]


def test_or_deduplicates_and_does_not_apply_request_top_n():
    panel, dates = _panel()
    request = _request(top_n=1)
    result = hs.evaluate_historical_screen(panel, [request, request], calendar=dates, **_kwargs(panel))
    assert result.eligibility.iloc[-1].sum() == 2
    assert result.diagnostics["matched_by_date"][dates[-1].date().isoformat()] == 2
    assert result.diagnostics["top_n_applied"] is False


def test_named_score_requires_both_mas_and_is_exact_negative_nearest_bias():
    panel, dates = _panel()
    result = hs.evaluate_historical_screen(panel, _request(), calendar=dates,
                                          rank="nearest_ma10_ma20", **_kwargs(panel))
    close = panel.xs("SH600001", level="code")["close"]
    expected = -min(abs(close.iloc[-1] / close.tail(10).mean() - 1),
                    abs(close.iloc[-1] / close.tail(20).mean() - 1))
    assert result.scores.iloc[:19].isna().all().all()
    assert result.scores.loc[dates[-1], "SH600001"] == pytest.approx(expected)
    assert result.diagnostics["score"]["direction"] == "desc"


def test_preset_score_and_all_rule_types_equal_scalar_prefix_results():
    panel, dates = _panel(35, codes=("SH600001",))
    request = _request(selector={"preset": "ma_trend", "params": {}},
                       filters={"exclude_st": False, "min_avg_amount_yuan": 0,
                                "amount_window": 1, "amount_min_observations": 1})
    result = hs.evaluate_historical_screen(panel, request, calendar=dates, **_kwargs(panel))
    frame = panel.droplevel("code")
    spec = ds.compile_data_screen_spec(request)
    for i in range(len(frame)):
        prefix = frame.iloc[:i + 1]
        assert bool(result.eligibility.iloc[i, 0]) == all(ds._rule_result(prefix, rule)[0] for rule in spec["rules"])
        score, _ = ds._score_result(prefix, spec["score"])
        if score is None:
            assert pd.isna(result.scores.iloc[i, 0])
        else:
            assert result.scores.iloc[i, 0] == pytest.approx(score)


def test_start_trims_output_not_warmup_and_end_discards_future():
    panel, dates = _panel()
    result = hs.evaluate_historical_screen(panel, _trend(10), calendar=dates,
                                          start=str(dates[70].date()), end=str(dates[75].date()),
                                          **_kwargs(panel))
    assert result.eligibility.index.equals(dates[70:76].rename("datetime"))
    assert result.eligibility.all().all()


def test_static_metadata_is_explicit_and_strict_pit_fails_closed():
    panel, dates = _panel()
    kwargs = _kwargs(panel)
    kwargs["universe_meta"]["strict_pit"] = False
    kwargs["metadata_provenance"]["strict_pit"] = False
    request = _request()
    request["filters"]["exclude_st"] = True
    result = hs.evaluate_historical_screen(panel, request, calendar=dates, **kwargs)
    assert not result.diagnostics["strict_pit"]
    assert any("当前公司主表" in warning for warning in result.diagnostics["warnings"])
    assert any("幸存者偏差" in warning for warning in result.diagnostics["warnings"])
    request["filters"]["require_strict_metadata_pit"] = True
    with pytest.raises(ds.DataScreenSpecError, match="PIT"):
        hs.evaluate_historical_screen(panel, request, calendar=dates, **kwargs)


def test_fixed_declared_universe_coverage_not_future_observed_peak():
    panel, dates = _panel()
    kwargs = _kwargs(panel)
    kwargs["universe_meta"]["n_resolved"] = 3
    result = hs.evaluate_historical_screen(panel, _request(), calendar=dates, **kwargs)
    assert not result.calendar_valid.any()  # ceil(.8 × 3) = 3, only 2 loaded.
    assert not result.eligibility.any().any()
    assert result.diagnostics["coverage_minimum"] == 3


@pytest.mark.parametrize("problem", ["duplicates", "intraday", "missing_calendar", "bad_rank", "bad_units", "nine_branches"])
def test_rejects_ambiguous_or_unsafe_inputs(problem):
    panel, dates = _panel()
    request, extra = _request(), {}
    kwargs = _kwargs(panel)
    if problem == "duplicates":
        panel = pd.concat([panel, panel.iloc[:1]])
    elif problem == "intraday":
        dates = dates + pd.Timedelta(minutes=1)
    elif problem == "missing_calendar":
        dates = dates[1:]
    elif problem == "bad_rank":
        extra["rank"] = "__import__('os')"
    elif problem == "bad_units":
        kwargs["load_meta"]["amount_unit"] = "unknown"
    else:
        request = [request] * 9
    with pytest.raises(ds.DataScreenSpecError):
        hs.evaluate_historical_screen(panel, request, calendar=dates, **kwargs, **extra)


def test_branch_filter_mismatch_is_rejected_and_matrix_limit_is_bounded(monkeypatch):
    panel, dates = _panel()
    a, b = _trend(10), _trend(20)
    b["filters"]["min_avg_amount_yuan"] = 1
    with pytest.raises(ds.DataScreenSpecError, match="filters"):
        hs.evaluate_historical_screen(panel, [a, b], calendar=dates, **_kwargs(panel))
    monkeypatch.setattr(hs, "MAX_MATRIX_CELLS", 100)
    with pytest.raises(ds.DataScreenSpecError, match="上限"):
        hs.evaluate_historical_screen(panel, a, calendar=dates, **_kwargs(panel))


def test_nonfinite_quotes_cannot_pass_rules_or_score_and_no_zero_fills():
    panel, dates = _panel(25, codes=("SH600001",))
    panel.loc[(dates[-1], "SH600001"), "close"] = np.inf
    result = hs.evaluate_historical_screen(panel, _request(), calendar=dates,
                                          rank="nearest_ma10_ma20", **_kwargs(panel))
    assert not result.eligibility.iloc[-1, 0]
    assert pd.isna(result.scores.iloc[-1, 0])


def test_zero_turnover_remains_in_fixed_liquidity_window():
    panel, dates = _panel(22, codes=("SH600001",))
    panel.loc[(dates[5], "SH600001"), "amount"] = 0
    panel.loc[(dates[6], "SH600001"), "amount"] = np.nan
    result = hs.evaluate_historical_screen(panel, _request(), calendar=dates, **_kwargs(panel))
    assert not result.eligibility.iloc[-1, 0]  # Missing observation is still inside last20.


def test_conflicting_volume_alias_fails_closed():
    panel, dates = _panel()
    panel["vol"] = panel["volume"] / 100
    with pytest.raises(ds.DataScreenSpecError, match="冲突"):
        hs.evaluate_historical_screen(panel, _request(), calendar=dates, **_kwargs(panel))
