# -*- coding: utf-8 -*-
"""Phase 2 · Task 1 — the ``AuthoritativeClock`` runtime port (``runtime_clock.py``).

Written test-first (RED until ``runtime_clock.py`` exists). Locks the reviewed
clock contract that every Phase 2 runtime service consumes:

* ``AuthoritativeClock`` is a structural (``runtime_checkable``) ``Protocol`` with a
  single ``now() -> datetime`` method; a deterministic fixed/advancing test clock
  satisfies it, an object without ``now`` does not;
* runtime services never call :func:`datetime.now` directly — they read time via a
  clock and through :func:`clock_now`, which rejects a *naive* datetime returned by
  a misbehaving clock and normalizes any aware datetime to UTC;
* :class:`SystemClock` is the single production place a wall-clock is read, and it
  returns an aware-UTC instant.

Run from repo root: ``python -m pytest tests/orchestration/test_runtime_clock.py -v``
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from guanlan_v2.orchestration.runtime_clock import (
    AuthoritativeClock,
    SystemClock,
    clock_now,
    ensure_aware_utc,
)

UTC = timezone.utc
SHANGHAI = timezone(timedelta(hours=8))


# --------------------------------------------------------------------------- #
# deterministic test clocks (the "fixed/advancing implementation" the brief asks
# every runtime test to use in place of a wall clock)                           #
# --------------------------------------------------------------------------- #
class FixedClock:
    """Returns the same aware instant every call."""

    def __init__(self, at: datetime) -> None:
        self._at = at

    def now(self) -> datetime:
        return self._at


class AdvancingClock:
    """Returns a strictly increasing instant, one ``step`` per call."""

    def __init__(self, start: datetime, step: timedelta = timedelta(seconds=1)) -> None:
        self._next = start
        self._step = step

    def now(self) -> datetime:
        cur = self._next
        self._next = cur + self._step
        return cur


class NaiveClock:
    """A misbehaving clock returning a *naive* datetime (no tzinfo)."""

    def now(self) -> datetime:
        return datetime(2026, 7, 16, 9, 0, 0)


class NotAClock:
    """Has no ``now`` method — must not satisfy the protocol."""


# --------------------------------------------------------------------------- #
# protocol shape                                                              #
# --------------------------------------------------------------------------- #
def test_authoritative_clock_is_runtime_checkable_protocol():
    assert isinstance(FixedClock(datetime(2026, 7, 16, tzinfo=UTC)), AuthoritativeClock)
    assert isinstance(SystemClock(), AuthoritativeClock)
    assert not isinstance(NotAClock(), AuthoritativeClock)


# --------------------------------------------------------------------------- #
# ensure_aware_utc: naive rejection + UTC normalization                       #
# --------------------------------------------------------------------------- #
def test_ensure_aware_utc_rejects_naive():
    with pytest.raises(ValueError):
        ensure_aware_utc(datetime(2026, 7, 16, 9, 0, 0))


def test_ensure_aware_utc_normalizes_to_utc():
    at = datetime(2026, 7, 16, 17, 0, 0, tzinfo=SHANGHAI)  # 09:00 UTC
    out = ensure_aware_utc(at)
    assert out.tzinfo == UTC
    assert out == datetime(2026, 7, 16, 9, 0, 0, tzinfo=UTC)


# --------------------------------------------------------------------------- #
# clock_now: reads through the port, rejecting naive, normalizing to UTC       #
# --------------------------------------------------------------------------- #
def test_clock_now_returns_aware_utc_from_a_fixed_clock():
    clock = FixedClock(datetime(2026, 7, 16, 9, 30, tzinfo=UTC))
    out = clock_now(clock)
    assert out == datetime(2026, 7, 16, 9, 30, tzinfo=UTC)
    assert out.tzinfo == UTC


def test_clock_now_normalizes_a_non_utc_clock_to_utc():
    clock = FixedClock(datetime(2026, 7, 16, 17, 30, tzinfo=SHANGHAI))
    out = clock_now(clock)
    assert out == datetime(2026, 7, 16, 9, 30, tzinfo=UTC)
    assert out.tzinfo == UTC


def test_clock_now_rejects_a_naive_returning_clock():
    with pytest.raises(ValueError):
        clock_now(NaiveClock())


def test_clock_now_is_monotonic_over_an_advancing_clock():
    clock = AdvancingClock(datetime(2026, 7, 16, 9, 0, tzinfo=UTC))
    seen = [clock_now(clock) for _ in range(5)]
    assert seen == sorted(seen)
    assert len(set(seen)) == 5  # strictly increasing


# --------------------------------------------------------------------------- #
# SystemClock: the single production wall-clock read                          #
# --------------------------------------------------------------------------- #
def test_system_clock_returns_aware_utc():
    now = SystemClock().now()
    assert now.tzinfo is not None and now.utcoffset() == timedelta(0)
    # clock_now accepts it unchanged (already aware UTC).
    assert clock_now(SystemClock()).tzinfo == UTC
