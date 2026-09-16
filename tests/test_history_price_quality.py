"""Replay-only quality gate; no real datasets, source edits or exclusion."""
from __future__ import annotations
import copy
import json

import numpy as np
import pandas as pd
import pytest

from guanlan_v2.workflow.history_price_quality import (
    ADJUSTMENT_SCHEMA, HistoryPriceQualityError, assess_history_price_quality,
    history_price_fingerprint, require_history_price_quality,
)


def panel():
    dates = pd.bdate_range("2025-01-02", periods=5)
    frame = pd.DataFrame({"datetime": dates, "instrument": "SH600001",
        "open": [10., 10.1, 10.2, 10.3, 10.4], "high": 11., "low": 9., "close": 10.5,
        "volume": 100000., "amount": 1_000_000.}).set_index(["datetime", "instrument"])
    return frame, dates


def fixture_certificate(frame, calendar):
    # Test-only trusted adapter example; this does not certify real raw data.
    required = int((np.isfinite(frame[["open", "high", "low", "close"]]) &
                    frame[["open", "high", "low", "close"]].gt(0)).any(axis=1).sum())
    return {"schema": ADJUSTMENT_SCHEMA, "status": "verified", "method": "causal_cumulative_factor",
            "future_normalization": False, "price_fields": ["open", "high", "low", "close"],
            "volume_policy": "raw_share", "amount_policy": "raw_yuan", "source": "synthetic test adapter",
            "raw_price_sha256": "a" * 64, "factor_source_sha256": "b" * 64,
            **history_price_fingerprint(frame, calendar),
            "factor_coverage": {"n_required": required, "n_verified": required, "n_missing": 0}}


def test_untrusted_raw_or_legacy_artifact_fails_closed_even_when_shape_is_clean():
    frame, calendar = panel()
    report = assess_history_price_quality(frame, calendar)
    assert not report["performance_ready"] and report["diagnostics_only"]
    assert [x["code"] for x in report["hard_blockers"]] == ["uncertified_price_adjustment"]
    with pytest.raises(HistoryPriceQualityError) as caught:
        require_history_price_quality(frame, calendar)
    assert caught.value.diagnostics == report


def test_complete_trusted_content_bound_certificate_passes_without_source_mutation():
    frame, calendar = panel()
    before = frame.copy(deep=True)
    report = require_history_price_quality(frame, calendar,
        adjustment_provenance=fixture_certificate(frame, calendar))
    assert report["performance_ready"] and not report["diagnostics_only"]
    pd.testing.assert_frame_equal(frame, before)
    json.dumps(report, allow_nan=False)


@pytest.mark.parametrize("defect", ["high_below_low", "close_above_high", "open_below_low", "inf", "zero", "negative", "nonnumeric"])
def test_impossible_or_invalid_provided_prices_are_hard_blockers(defect):
    frame, calendar = panel()
    if defect == "high_below_low": frame.loc[frame.index[0], "high"] = 8.
    elif defect == "close_above_high": frame.loc[frame.index[0], "close"] = 12.
    elif defect == "open_below_low": frame.loc[frame.index[0], "open"] = 8.
    elif defect == "inf": frame.loc[frame.index[0], "close"] = np.inf
    elif defect == "zero": frame.loc[frame.index[0], "close"] = 0.
    elif defect == "negative": frame.loc[frame.index[0], "close"] = -1.
    else:
        frame["close"] = frame["close"].astype(object)
        frame.loc[frame.index[0], "close"] = "untrusted code is not evaluated"
    report = assess_history_price_quality(frame, calendar)
    assert len(report["hard_blockers"]) >= 2
    json.dumps(report, allow_nan=False)


def test_missing_rows_and_nan_prices_are_explicit_not_dropped_or_filled():
    frame, calendar = panel()
    frame = frame.drop(frame.index[1])
    frame.loc[frame.index[1], "close"] = np.nan
    before = frame.copy()
    report = assess_history_price_quality(frame, calendar,
        adjustment_provenance=fixture_certificate(frame, calendar))
    assert report["performance_ready"]
    assert report["counts"]["missing_calendar_stock_rows"] == 1
    assert report["counts"]["partial_OHLC_missing_rows"] == 1
    assert any(w["code"] == "missing_prices" for w in report["warnings"])
    pd.testing.assert_frame_equal(before, frame)


def test_large_real_gap_warns_without_exclusion_when_adjustment_is_certified():
    frame, calendar = panel()
    frame.loc[frame.index[2], ["open", "high", "low", "close"]] = [16., 18., 15., 17.]
    report = assess_history_price_quality(frame, calendar,
        adjustment_provenance=fixture_certificate(frame, calendar))
    assert report["performance_ready"]
    assert report["counts"]["overnight_move_warning_rows"] == 2
    assert len(frame) == 5


@pytest.mark.parametrize("mutation", [
    lambda c: c.update(status="claimed"), lambda c: c.update(method="raw_prices"),
    lambda c: c.update(future_normalization=True), lambda c: c.update(future_normalization=0),
    lambda c: c.update(adjusted_price_sha256="f" * 64), lambda c: c.update(calendar_sha256="f" * 64),
    lambda c: c.update(factor_source_sha256="not-a-hash"),
    lambda c: c["factor_coverage"].update(n_missing=1),
    lambda c: c["factor_coverage"].update(n_verified=4),
    lambda c: c.update(price_fields=["close"]),
])
def test_incomplete_or_content_mismatched_certificate_is_rejected(mutation):
    frame, calendar = panel()
    certificate = fixture_certificate(frame, calendar)
    mutation(certificate)
    report = assess_history_price_quality(frame, calendar, adjustment_provenance=certificate)
    assert not report["performance_ready"]
    assert report["adjustment_certificate"]["failed_fields"]


def test_old_certificate_cannot_validate_changed_price_or_changed_calendar():
    frame, calendar = panel()
    certificate = fixture_certificate(frame, calendar)
    frame.loc[frame.index[-1], "open"] += .01
    with pytest.raises(HistoryPriceQualityError):
        require_history_price_quality(frame, calendar, adjustment_provenance=certificate)
    frame, calendar = panel()
    with pytest.raises(HistoryPriceQualityError):
        require_history_price_quality(frame, calendar.append(pd.DatetimeIndex([calendar[-1] + pd.Timedelta(days=3)])),
                                      adjustment_provenance=certificate)


@pytest.mark.parametrize("defect", ["duplicate_panel", "duplicate_calendar", "intraday", "outside", "unknown_code"])
def test_structural_ambiguity_is_a_json_safe_hard_blocker(defect):
    frame, calendar = panel()
    if defect == "duplicate_panel": frame = pd.concat([frame, frame.iloc[:1]])
    elif defect == "duplicate_calendar": calendar = calendar.insert(0, calendar[0])
    elif defect == "intraday": calendar = calendar + pd.Timedelta(minutes=1)
    elif defect == "outside": calendar = calendar[1:]
    else: frame = frame.rename(index={"SH600001": "../bad"}, level=1)
    report = assess_history_price_quality(frame, calendar)
    assert not report["performance_ready"]
    json.dumps(report, allow_nan=False)


def test_example_count_is_bounded_and_explicit_missing_gap_not_compressed():
    frame, calendar = panel()
    frame["high"] = 1.
    report = assess_history_price_quality(frame, calendar, max_examples=2)
    assert report["counts"]["OHLC_envelope_bad_rows"] == 5
    assert len(report["examples"]["impossible_OHLC"]) == 2
    with pytest.raises(HistoryPriceQualityError, match="5行"):
        require_history_price_quality(frame, calendar)
    frame, calendar = panel()
    frame = frame.drop(frame.index[1])
    frame.loc[frame.index[1], ["open", "high", "low", "close"]] = [30., 31., 29., 30.]
    report = assess_history_price_quality(frame, calendar)
    # No fictitious 3x gap across missing Jan3; only next known Jan7 decline warns.
    assert report["counts"]["overnight_move_warning_rows"] == 1


def test_empty_price_observations_cannot_masquerade_as_cash_strategy():
    frame, calendar = panel()
    frame.loc[:, ["open", "high", "low", "close"]] = np.nan
    report = assess_history_price_quality(frame, calendar,
        adjustment_provenance=fixture_certificate(frame, calendar))
    assert not report["performance_ready"]
    assert "no_observed_valid_prices" in {x["code"] for x in report["hard_blockers"]}


def test_untrusted_certificate_fields_cannot_inject_non_json_diagnostics():
    frame, calendar = panel()
    report = assess_history_price_quality(frame, calendar,
        adjustment_provenance={"source": {"nan": float("nan")}, "method": object()})
    assert not report["performance_ready"]
    json.dumps(report, allow_nan=False)
