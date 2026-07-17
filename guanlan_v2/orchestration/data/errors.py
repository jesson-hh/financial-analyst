"""The data error taxonomy — the multi-source router's whole control surface.

Every cross-vendor routing decision is an ``isinstance`` check against the
classes defined here, so the hierarchy *is* the contract. Three disjoint
outcomes, and nothing downstream may add a fourth:

1. **Advance the frozen chain (narrow fallback).** *Only* :class:`RateLimitError`
   and :class:`NotConfiguredError` let the dispatcher move on to the next vendor
   in the already-frozen chain. A vendor being throttled or unconfigured is the
   sole reason another vendor's answer may substitute for it.
2. **Terminate the chain, typed.** :class:`NoDataError` and
   :class:`StaleDataError` end the current chain with a typed ``NO_DATA`` /
   ``STALE`` result and *no* continue — an authoritative "nothing here" is an
   answer, not a reason to shop other vendors.
3. **Raise, loud.** :class:`FutureDataRefused`, :class:`MissingAvailabilityRefused`
   and every :class:`DataIntegrityError` propagate unmasked. A PIT breach or a
   structurally broken primary must never be silently papered over by a
   fallback's answer.

Because the router keys purely on type, the disjointness matters as much as the
descent: :class:`RateLimitError` and :class:`NotConfiguredError` are siblings
(neither is a subclass of the other), and none of the terminal/raise types is a
subclass of that fallback pair. :class:`DataIntegrityError` groups the
never-fallback structural failures under one catchable base so a supervising
layer can treat "the data plane itself is broken" distinctly from an ordinary
per-vendor miss. Every class descends from :class:`DataError`, so a broad
``except DataError`` remains a valid backstop.

These are plain exception classes (not pydantic contracts): they are raised and
caught in control flow, never persisted as facts. The structured fields a few of
them carry (``symbol`` / ``future_rows`` / ``latest_available_at`` …) are the
minimum an audit sink or a typed :class:`DataResult` needs to record *why* the
outcome happened without re-deriving it.
"""
from __future__ import annotations

from datetime import datetime


class DataError(Exception):
    """Root of the data taxonomy; a broad ``except DataError`` catches them all."""


class NoDataError(DataError):
    """Authoritative "no such data" — terminates the chain with ``NO_DATA``.

    Carries the queried ``symbol`` and its ``canonical`` (dotted) form plus a
    free-text ``detail`` (e.g. ``"delisted"``) so the typed result and audit
    record can state what was asked for without re-deriving it. This is a
    *typed terminal*, never a fallback trigger.
    """

    def __init__(self, *, symbol: str, canonical: str, detail: str = "") -> None:
        self.symbol = symbol
        self.canonical = canonical
        self.detail = detail
        super().__init__(f"no data for {symbol} ({canonical}): {detail}")


class StaleDataError(DataError):
    """Data older than the freshness policy allows — terminates the chain ``STALE``.

    ``latest_available_at`` is the newest ``available_at`` (当时可知时间) the
    source could offer, and ``pit_audit`` optionally carries the PIT evaluation
    that judged it stale. Like :class:`NoDataError`, this is a typed terminal and
    never advances the chain.
    """

    def __init__(
        self,
        detail: str,
        *,
        latest_available_at: datetime | None = None,
        pit_audit: object | None = None,
    ) -> None:
        self.detail = detail
        self.latest_available_at = latest_available_at
        self.pit_audit = pit_audit
        super().__init__(detail)


class RateLimitError(DataError):
    """Vendor throttled the request — one of the two narrow-fallback triggers."""


class NotConfiguredError(DataError, ValueError):
    """Vendor is not configured (missing key/endpoint) — the other fallback trigger.

    Also a :class:`ValueError` so callers that validate configuration eagerly can
    catch it as a plain value error before any routing is attempted.
    """


class FutureDataRefused(DataError):
    """A row's ``available_at`` is after the as-of instant — a PIT breach.

    ``future_rows`` is how many rows leaked from the future. This must **raise**
    (never fall back, never be silently filtered): a primary that hands back
    future-dated data is broken and must be loud.
    """

    def __init__(self, detail: str, *, future_rows: int) -> None:
        self.future_rows = future_rows
        super().__init__(detail)


class MissingAvailabilityRefused(DataError):
    """A required row has no ``available_at`` at all — PIT cannot be proven.

    Refused rather than assumed available; like :class:`FutureDataRefused` it
    raises and never triggers fallback.
    """


class DataIntegrityError(DataError):
    """Base for structural failures that must raise, never fall back.

    These describe the data plane itself being broken (a source, a routing
    config, a snapshot/cache identity, or a replay invariant), as opposed to an
    ordinary per-vendor miss. Grouping them lets a supervisor catch the whole
    never-fallback family with one ``except DataIntegrityError``.
    """


class SourceBrokenError(DataIntegrityError):
    """A source raised an unexpected/unclassifiable error — treat as broken, raise."""


class RoutingConfigurationError(DataIntegrityError, ValueError):
    """The resolved routing chain is itself invalid (unknown vendor, empty chain…).

    Also a :class:`ValueError` because it is a configuration/programming fault
    surfaced while building or resolving the chain, not a runtime data miss.
    """


class SnapshotMismatchError(DataIntegrityError):
    """A read's snapshot/manifest digests do not match the frozen expectation."""


class CacheIntegrityError(DataIntegrityError):
    """A cache entry failed its integrity check (key/digest/schema mismatch)."""


class LiveFallbackRefused(DataIntegrityError):
    """A frozen/replay read tried to fall through to LIVE — structurally forbidden.

    ``PIT_REPLAY`` may never advance to a live source; attempting it is an
    integrity fault, not a recoverable fallback.
    """
