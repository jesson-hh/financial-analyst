"""The read-only trading-calendar port — exact versioned material, no wall clock.

A trading calendar answers two deterministic questions — *is this date a trading
session?* and *how many sessions fall in ``[start, end]``?* — and it answers them
purely from one **immutable material**, never from the wall clock or a mutable
global holiday list. That property is what lets a PIT-safe layer count listing
sessions (Task 3's :func:`~guanlan_v2.orchestration.data.symbols.resolve_limit_rule`)
or evaluate a session-based freshness policy (Task 4's ``PitGuard``) without any
hidden "today".

Three pieces:

* :class:`TradingCalendarMaterial` — the frozen, digestible session set (ISO
  ``YYYY-MM-DD`` strings, canonically ascending and duplicate-free). Its content
  digest *is* the calendar's identity.
* :class:`TradingCalendar` (``Protocol``) + :class:`ImmutableTradingCalendar` —
  the read-only port and its concrete, self-verifying implementation.
* :class:`TradingCalendarResolver` — a service-owned mapping from a versioned
  calendar :class:`~guanlan_v2.orchestration.refs.ContentRef` to a trusted
  implementation. Resolution verifies the material digest **and** the exact
  ``calendar_id``; a missing registration or any digest / id drift is loud
  (:class:`~guanlan_v2.orchestration.data.errors.RoutingConfigurationError` /
  :class:`~guanlan_v2.orchestration.data.errors.SnapshotMismatchError`), never
  silently repaired. Tests drive an immutable fake behind this same port.
"""
from __future__ import annotations

from collections.abc import Iterable
from datetime import date, datetime
from typing import Literal, Protocol, runtime_checkable

from pydantic import model_validator

from guanlan_v2.orchestration.data.errors import (
    RoutingConfigurationError,
    SnapshotMismatchError,
)
from guanlan_v2.orchestration.digest import (
    DigestModel,
    NonEmptyStr,
    content_digest,
)
from guanlan_v2.orchestration.refs import ContentRef

__all__ = [
    "TradingCalendar",
    "TradingCalendarMaterial",
    "ImmutableTradingCalendar",
    "TradingCalendarResolver",
    "build_trading_calendar",
]

_ISO_DATE_LEN = len("YYYY-MM-DD")


def _require_pure_date(value: object, *, arg: str) -> date:
    """Return ``value`` iff it is a plain :class:`datetime.date` (not a datetime).

    ``datetime`` subclasses ``date``; a datetime would silently miss the session
    set (which is keyed by pure dates), so it is rejected rather than coerced.
    """
    if not isinstance(value, date) or isinstance(value, datetime):
        raise TypeError(f"{arg} must be a datetime.date, got {type(value).__name__}")
    return value


class TradingCalendarMaterial(DigestModel):
    """The immutable trading-session material a calendar resolves over.

    ``sessions`` is the canonical, ascending, duplicate-free tuple of session
    dates in ISO ``YYYY-MM-DD`` form. They are stored as strings so the material
    is a pure canonical-JSON fact — ``datetime.date`` is not a canonical-JSON type
    — and the model's content digest is the calendar's content identity.
    """

    schema_version: Literal["1"] = "1"
    calendar_id: NonEmptyStr
    sessions: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _check(self) -> "TradingCalendarMaterial":
        for s in self.sessions:
            if len(s) != _ISO_DATE_LEN:
                raise ValueError(f"session {s!r} is not an ISO YYYY-MM-DD date")
            try:
                date.fromisoformat(s)
            except ValueError as exc:  # pragma: no cover - defensive
                raise ValueError(f"session {s!r} is not a valid date: {exc}") from exc
        if list(self.sessions) != sorted(self.sessions):
            raise ValueError("sessions must be canonically ascending")
        if len(set(self.sessions)) != len(self.sessions):
            raise ValueError("sessions must be duplicate-free")
        return self


@runtime_checkable
class TradingCalendar(Protocol):
    """Read-only trading calendar over one immutable material.

    Every answer is a pure function of the frozen ``material_ref`` material; a
    calendar never reads the wall clock or a mutable global holiday list.
    """

    @property
    def calendar_id(self) -> str: ...

    @property
    def material_ref(self) -> ContentRef: ...

    @property
    def coverage(self) -> tuple[date, date] | None:
        """The material's covered span ``(first_session, last_session)``.

        ``None`` for an empty material. A consumer counting sessions over a range
        MUST first assert the range lies inside this span — a date outside the
        material is *uncovered*, not "zero sessions", and counting across it would
        silently undercount.
        """
        ...


class ImmutableTradingCalendar:
    """A concrete :class:`TradingCalendar` over a verified immutable material.

    On construction it re-derives the material digest and requires it to equal the
    supplied ``material_ref.content_digest``; a tampered material or a mislabeled
    ref is loud (:class:`SnapshotMismatchError`), so a resolved calendar can be
    trusted as exactly the material its ref names.
    """

    __slots__ = ("_material", "_material_ref", "_sessions", "_coverage")

    def __init__(
        self, *, material: TradingCalendarMaterial, material_ref: ContentRef
    ) -> None:
        if content_digest(material) != material_ref.content_digest:
            raise SnapshotMismatchError(
                "trading-calendar material digest does not match its material_ref "
                f"({material_ref.id}@{material_ref.version})"
            )
        self._material = material
        self._material_ref = material_ref
        self._sessions: frozenset[date] = frozenset(
            date.fromisoformat(s) for s in material.sessions
        )
        self._coverage: tuple[date, date] | None = (
            (min(self._sessions), max(self._sessions)) if self._sessions else None
        )

    @property
    def calendar_id(self) -> str:
        return self._material.calendar_id

    @property
    def material_ref(self) -> ContentRef:
        return self._material_ref

    @property
    def material(self) -> TradingCalendarMaterial:
        return self._material

    @property
    def coverage(self) -> tuple[date, date] | None:
        return self._coverage

    def is_session(self, day: date) -> bool:
        return _require_pure_date(day, arg="day") in self._sessions

    def sessions_between(self, start: date, end: date) -> int:
        start = _require_pure_date(start, arg="start")
        end = _require_pure_date(end, arg="end")
        if end < start:
            raise ValueError(f"end {end.isoformat()} must be >= start {start.isoformat()}")
        return sum(1 for d in self._sessions if start <= d <= end)


class TradingCalendarResolver:
    """Service-owned resolver from a versioned calendar ref to a trusted calendar.

    Registered with a fixed set of trusted :class:`ImmutableTradingCalendar`s,
    keyed by their material ``(id, version)``. :meth:`resolve` verifies the
    resolved material's digest against the requested ref **and** the exact
    ``calendar_id`` before returning it; a missing registration or any drift
    raises rather than guessing. The same port backs an immutable fake in tests.
    """

    __slots__ = ("_by_key",)

    def __init__(self, calendars: Iterable[ImmutableTradingCalendar]) -> None:
        by_key: dict[tuple[str, str], ImmutableTradingCalendar] = {}
        for cal in calendars:
            key = (cal.material_ref.id, cal.material_ref.version)
            if key in by_key:
                raise RoutingConfigurationError(
                    f"duplicate trading-calendar registration for {key[0]}@{key[1]}"
                )
            by_key[key] = cal
        self._by_key = by_key

    def resolve(self, material_ref: ContentRef, *, calendar_id: str) -> TradingCalendar:
        cal = self._by_key.get((material_ref.id, material_ref.version))
        if cal is None:
            raise RoutingConfigurationError(
                "no trading calendar registered for material "
                f"{material_ref.id}@{material_ref.version}"
            )
        if content_digest(cal.material) != material_ref.content_digest:
            raise SnapshotMismatchError(
                "trading-calendar material digest drift for "
                f"{material_ref.id}@{material_ref.version}"
            )
        if cal.calendar_id != calendar_id:
            raise SnapshotMismatchError(
                f"calendar_id mismatch: resolved {cal.calendar_id!r} != requested "
                f"{calendar_id!r} for {material_ref.id}@{material_ref.version}"
            )
        return cal


def build_trading_calendar(
    *,
    calendar_id: str,
    sessions: Iterable[date],
    material_id: str,
    material_version: str,
) -> ImmutableTradingCalendar:
    """Build a verified :class:`ImmutableTradingCalendar` from session dates.

    ``sessions`` are :class:`datetime.date` objects; they are normalized to the
    canonical ascending, duplicate-free ISO tuple, digested, and bound to the
    exact versioned :class:`ContentRef` ``material_id@material_version``.
    """
    iso = tuple(sorted({_require_pure_date(d, arg="session").isoformat() for d in sessions}))
    material = TradingCalendarMaterial(calendar_id=calendar_id, sessions=iso)
    ref = ContentRef(
        id=material_id,
        version=material_version,
        content_digest=content_digest(material),
    )
    return ImmutableTradingCalendar(material=material, material_ref=ref)
