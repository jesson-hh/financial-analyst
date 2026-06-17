"""watch/calendar.py — A 股交易日判定 (holiday-aware, qlib day 日历为准).

``WatchLoop.is_market_open`` defaults to a weekday + session-window check, which
treats 节假日 as open — the loop would burn snapshot/LLM calls on a holiday. This
module supplies a holiday-aware ``is_trading_day`` callable backed by the qlib
**day calendar** (``{provider_uri}/calendars/day.txt`` — the project's source of
truth for real trading days, weekends already filtered).

Safety: the calendar has finite coverage ``[min, max]``. For a date INSIDE that
range we trust membership (real holiday awareness, incl. 调休补班 weekend
workdays). For a date BEYOND ``max`` (the calendar hasn't been extended to today
yet) we **fall back to a weekday check** — otherwise every future trading day
would read as closed and the loop would never run. Empty/unreadable calendar →
weekday-only (status quo). Never raises.
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Optional, Set

import pandas as pd

log = logging.getLogger(__name__)

_DAYS_CACHE: Optional[Set[str]] = None


def _load_trading_days() -> Set[str]:
    """Load the qlib day calendar as a set of 'YYYY-MM-DD'. Empty on any failure."""
    try:
        from financial_analyst.data.paths import get_data_paths
        from financial_analyst.data.bin_writer import load_calendar
        root = str(get_data_paths().qlib_day)
        days = load_calendar(root, "day")
        return {str(d)[:10] for d in days if d}
    except Exception as exc:  # noqa: BLE001
        log.debug("watch.calendar: trading-day load failed (%s); weekday fallback", exc)
        return set()


def _trading_days() -> Set[str]:
    """Lazily loaded, process-cached trading-day set (no import-time IO)."""
    global _DAYS_CACHE
    if _DAYS_CACHE is None:
        _DAYS_CACHE = _load_trading_days()
    return _DAYS_CACHE


def _is_weekday(ts: pd.Timestamp) -> bool:
    return ts.weekday() < 5


def is_a_share_trading_day(d: Any, trading_days: Optional[Set[str]] = None) -> bool:
    """``True`` iff ``d`` is an A 股 trading day.

    Membership inside the calendar's covered range (real holiday awareness);
    weekday fallback outside it or when the set is empty. ``d`` is anything
    :class:`pandas.Timestamp` accepts.
    """
    ts = pd.Timestamp(d)
    days = _trading_days() if trading_days is None else trading_days
    if not days:
        return _is_weekday(ts)
    day = ts.strftime("%Y-%m-%d")
    if day < min(days) or day > max(days):     # outside coverage → weekday
        return _is_weekday(ts)
    return day in days


def make_market_open_check() -> Callable[[Any], bool]:
    """Return a holiday-aware ``is_trading_day(ts)`` for ``WatchLoop``.

    Captures the loaded trading-day set once; the loop combines it with its own
    session-window (time-of-day) gate. Falls back to weekday-only when no
    calendar is available.
    """
    days = _trading_days()

    def _check(ts: Any) -> bool:
        return is_a_share_trading_day(ts, days)

    return _check


__all__ = ["is_a_share_trading_day", "make_market_open_check"]
