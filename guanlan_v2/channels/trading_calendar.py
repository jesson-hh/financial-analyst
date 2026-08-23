"""Strict A-share trading-calendar provider for conversation automations.

There is intentionally no Monday-to-Friday fallback.  If the repository's
authoritative day calendar cannot be loaded, trading-day schedules are blocked
while other schedule kinds keep working.
"""
from __future__ import annotations

from datetime import date, datetime
from threading import Lock
from typing import Any, Callable, Iterable


class CalendarUnavailable(RuntimeError):
    """The authoritative A-share calendar could not be loaded safely."""


def _as_date(value: Any, *, field: str) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    to_python = getattr(value, "to_pydatetime", None)
    if callable(to_python):
        converted = to_python()
        if isinstance(converted, datetime):
            return converted.date()
        if isinstance(converted, date):
            return converted
    if isinstance(value, str):
        candidate = value.strip()
    else:
        candidate = str(value).strip()
    if not candidate:
        raise ValueError(f"{field} is not a date")
    # pandas/numpy timestamps stringify with a date prefix.  ISO parsing keeps
    # this adapter dependency-free and rejects locale-dependent values.
    try:
        return date.fromisoformat(candidate[:10])
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} is not an ISO date") from exc


def _default_loader_factory() -> Any:
    # Lazy import is essential: channel startup must remain available even when
    # the analysis engine or its configured data source is unavailable.
    from financial_analyst.data.loader_factory import get_default_loader

    return get_default_loader()


class AShareTradingCalendar:
    """Lazy, cached view of ``get_default_loader()._load_calendar('day')``."""

    def __init__(self, loader_factory: Callable[[], Any] | None = None) -> None:
        self._loader_factory = loader_factory or _default_loader_factory
        self._cached: frozenset[date] | None = None
        self._lock = Lock()

    def _load(self) -> frozenset[date]:
        cached = self._cached
        if cached is not None:
            return cached
        with self._lock:
            if self._cached is not None:
                return self._cached
            try:
                loader = self._loader_factory()
                load_calendar = getattr(loader, "_load_calendar")
                raw: Iterable[Any] = load_calendar("day")
                normalized = frozenset(
                    _as_date(value, field="calendar entry") for value in raw
                )
            except CalendarUnavailable:
                raise
            except Exception as exc:
                raise CalendarUnavailable("a_share_calendar_unavailable") from exc
            if not normalized:
                raise CalendarUnavailable("a_share_calendar_unavailable")
            self._cached = normalized
            return normalized

    def all_days(self) -> frozenset[date]:
        """Return the normalized cached calendar."""

        return self._load()

    def trading_days(self, start: date | datetime | str, end: date | datetime | str) \
            -> frozenset[date]:
        """Return authoritative sessions within an inclusive date range."""

        first = _as_date(start, field="start")
        last = _as_date(end, field="end")
        if last < first:
            raise ValueError("end must not be before start")
        return frozenset(day for day in self._load() if first <= day <= last)

    def is_trading_day(self, value: date | datetime | str) -> bool:
        """Test membership without substituting a weekday heuristic."""

        return _as_date(value, field="day") in self._load()


__all__ = ["AShareTradingCalendar", "CalendarUnavailable"]
