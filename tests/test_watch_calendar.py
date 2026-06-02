"""Tests for watch/calendar.py — holiday-aware A 股 trading-day check.

In-range membership = real holiday awareness; out-of-range / empty = weekday
fallback. 2026-06-02 is a Tuesday; 2026-06-06 a Saturday.
"""
from __future__ import annotations

import pandas as pd

from financial_analyst.watch.calendar import (
    is_a_share_trading_day,
    make_market_open_check,
)


def test_empty_calendar_weekday_fallback():
    assert is_a_share_trading_day("2026-06-02", trading_days=set()) is True   # Tue
    assert is_a_share_trading_day("2026-06-06", trading_days=set()) is False  # Sat


def test_holiday_inside_coverage_is_false():
    # coverage 06-01..06-05; 06-03 is a (hypothetical) holiday → absent from set.
    days = {"2026-06-01", "2026-06-02", "2026-06-04", "2026-06-05"}
    assert is_a_share_trading_day("2026-06-03", days) is False   # weekday but holiday
    assert is_a_share_trading_day("2026-06-02", days) is True


def test_makeup_weekend_workday_inside_coverage_is_true():
    # a Saturday present in the calendar (调休补班) → True despite being a weekend.
    days = {"2026-06-05", "2026-06-06", "2026-06-08"}   # 06-06 = Sat, present
    assert is_a_share_trading_day("2026-06-06", days) is True


def test_future_beyond_coverage_weekday_fallback():
    days = {"2026-06-01", "2026-06-02"}                 # max coverage = 06-02
    assert is_a_share_trading_day("2026-06-09", days) is True    # Tue beyond coverage
    assert is_a_share_trading_day("2026-06-13", days) is False   # Sat beyond coverage


def test_before_coverage_weekday_fallback():
    days = {"2026-06-08", "2026-06-09"}                 # min coverage = 06-08
    assert is_a_share_trading_day("2026-06-02", days) is True    # Tue before coverage


def test_make_market_open_check_returns_callable_no_raise():
    chk = make_market_open_check()
    assert callable(chk)
    # smoke: must not raise regardless of whether a real calendar is present.
    assert chk(pd.Timestamp("2026-06-06 10:00")) in (True, False)
    assert chk(pd.Timestamp("2026-06-02 10:00")) in (True, False)
