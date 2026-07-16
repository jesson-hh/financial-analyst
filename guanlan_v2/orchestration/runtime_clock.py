"""The authoritative-clock runtime port (Phase 2 · Task 1).

Phase 1 froze *values*; Phase 2 adds runtime services around them. Those services
must never read the wall clock directly (``datetime.now()`` scattered through the
kernel is untestable and lets a naive datetime leak into a digest). Instead every
runtime service accepts an :class:`AuthoritativeClock` port and reads time through
:func:`clock_now`, which rejects a *naive* datetime returned by a misbehaving clock
and normalizes any aware datetime to UTC — the same discipline
:data:`guanlan_v2.orchestration.digest.UtcDateTime` enforces on model fields.

This module owns **no** contract model (nothing here is a
:class:`~guanlan_v2.orchestration.digest.ContractModel`); it is a pure runtime
port plus the single production clock. It is deliberately *not* exported from the
Phase 1 ``__init__`` public surface and registers no schema.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Protocol, runtime_checkable

__all__ = [
    "AuthoritativeClock",
    "SystemClock",
    "ensure_aware_utc",
    "clock_now",
]


def ensure_aware_utc(value: datetime) -> datetime:
    """Return ``value`` normalized to UTC; reject a naive datetime.

    Mirrors :data:`guanlan_v2.orchestration.digest.UtcDateTime` for a plain
    ``datetime`` that is not (yet) a validated model field — a clock reading.
    """
    if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
        raise ValueError("naive datetime is not allowed; a tz-aware value is required")
    return value.astimezone(timezone.utc)


@runtime_checkable
class AuthoritativeClock(Protocol):
    """The single time source a runtime service reads.

    Structural (``runtime_checkable``) so any object exposing ``now() -> datetime``
    satisfies it — production :class:`SystemClock`, or a deterministic fixed /
    advancing clock in tests. Implementations should return an aware instant;
    :func:`clock_now` enforces that at the read boundary.
    """

    def now(self) -> datetime:
        """Return the current instant as an aware datetime."""
        ...


class SystemClock:
    """Production clock — the single place a runtime reads the wall clock.

    Returns an aware-UTC instant. Runtime services take an
    :class:`AuthoritativeClock` and never call :func:`datetime.now` themselves, so
    swapping this for a deterministic clock in a test makes every timestamp
    reproducible.
    """

    def now(self) -> datetime:
        return datetime.now(timezone.utc)


def clock_now(clock: AuthoritativeClock) -> datetime:
    """Read an authoritative timestamp, rejecting a naive value and normalizing UTC.

    The one function every runtime service uses to obtain "now": it defends the
    kernel against a clock that hands back a naive datetime (which would later be
    rejected — noisily and far from the cause — by a :class:`UtcDateTime` field).
    """
    return ensure_aware_utc(clock.now())
