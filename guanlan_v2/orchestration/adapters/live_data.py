# -*- coding: utf-8 -*-
"""Phase 9 · Task 3 — the ONLINE live_client adapter + the start-frozen as_of.

This module is the ONLINE mirror of Task 2's PIT_REPLAY backbone
(:mod:`guanlan_v2.orchestration.adapters.replay_data`): where the replay adapter
reads the engine pit_store at a frozen historical decision point, this one reads
the LIVE 现拉 门户 (``guanlan_v2.datafeed.live_client``) at the run's **start-frozen**
instant. Both compose with the SAME reviewed Phase-3 routing / dispatch / PitGuard
machinery — the only structural differences are the mode/backend on the descriptor
and the manifest kind. It owns exactly three things — one source and two builders:

* :class:`LiveClientSource` — the ONLINE raw adapter behind the Phase-3
  :class:`~guanlan_v2.orchestration.data.source.DataSource` protocol. It binds the
  ``live_client`` facade (``probe`` / ``catalog`` / ``resolve_source`` /
  ``known_sources``; tests inject fakes) behind the **existing** Phase-3 method ids
  (``verified_snapshot`` for a per-code realtime quote, ``news`` for
  announcement/news-style text feeds, ``ohlcv`` for the market-wide tape snapshot —
  **no new** ``DataMethodSpec`` id is minted). Each probe-envelope row becomes a
  :class:`~guanlan_v2.orchestration.data.pit.RawRowCandidate` carrying **the data's
  own availability**: a *live* candidate is stamped with the envelope's ``pulled_at``
  (当时可知时间 = when we pulled it live), while a ``verified_snapshot`` row that
  carries its own **settled** datum yields a *settled projection* stamped at that
  datum's own settlement instant — the session attributed from the row's own
  **venue quote timestamp** plus the **committed trading calendar** (never from an
  ``as_of`` comparison, never from a guessed date; see
  :data:`_SETTLED_CLOSE_LOCAL_TIME` and
  :meth:`LiveClientSource._settled_candidate` — the CONTROLLER RULING behind the
  L2-b Task-9 gate and its ADDENDUM). The calendar is an OPTIONAL constructor
  dependency: without one the projection simply never fires. A missing
  ``pulled_at`` yields ``available_at=None`` and the single PIT authority
  (``PitGuard.check_raw``, invoked by the reviewed Phase-3 router at ``data/registry.py``
  dispatch step 5c) refuses it as :class:`MissingAvailabilityRefused` — the adapter
  never fabricates an ``available_at``. Because ``as_of`` is the **start-frozen** run
  instant, a row whose live ``pulled_at`` is *after* run start (later than ``as_of``)
  is refused by the guard as :class:`FutureDataRefused` — the freeze is real, not
  decorative; an intraday tick under a session-midnight ``as_of`` therefore stays
  typed-refused, and only genuinely settled data passes.
  No method takes a caller ``as_of`` / ``strict`` override; the boundary
  is the request's frozen as-of (the ``DataContext``) only, and no wall clock is
  read on the fetch path (``fetched_at`` is the frozen ``request.as_of``).
* :func:`build_online_live_descriptor` — an ``ONLINE``-only / ``LIVE``-backend
  :class:`~guanlan_v2.orchestration.data.source.DataSourceDescriptor` bound to the
  **existing** ``verified_snapshot`` / ``news`` / ``ohlcv`` method refs. Its
  ``supported_modes==(ONLINE,)`` is the structural mode-exclusion mechanism (the
  mirror image of Task 2's ``(PIT_REPLAY,)``): a ``PIT_REPLAY`` routing snapshot can
  therefore never cross-select this live source — a current snapshot can never
  impersonate history, and history can never leak a live source.
* :func:`build_online_capture_manifest` — a ``manifest_kind="online_capture_root"``
  snapshot manifest freezing the run-start boundary + routing; results append under
  the root without mutating its digest (per the Phase-3 manifest contract).
* :func:`build_online_data_context` — reads ``clock.now()`` **exactly once** (via the
  ONE Phase-3 :func:`~guanlan_v2.orchestration.data.snapshot.build_data_context`,
  which reads the clock once and freezes ``as_of`` into the returned context) and
  delegates with ``mode=ONLINE`` / ``backend=LIVE``. The returned ``DataContext`` is
  the frozen ``as_of`` for the whole run; ONLINE omits ``vintage_manifest_digest``
  (``None``) while carrying the capture-root content digest.

Honest-degradation red lines (user honesty red lines):

* **No fabricated rows, no silent vendor swap.** An ``ok:False`` (caller/mechanical)
  envelope raises a raise-bucket :class:`RoutingConfigurationError`; ``status:error``
  raises :class:`SourceBrokenError` (surfaces loud, never advances the frozen chain);
  ``status:planned`` raises :class:`NotConfiguredError` (the vendor probe is not
  implemented/available — the ONLY, per Phase-3, error that advances the frozen chain
  to the next configured vendor, or exhausts an optional method to an honest
  ``UNAVAILABLE`` result). None of these fabricates a row or silently substitutes a
  different vendor's answer.
* **Red line §10 — the live adapter registers NO order/signal write capability.**
  Every bound method spec is ``read_only=True`` by its own frozen contract, and this
  source exposes only reads (``fetch`` + pure accessors); there is no order/signal
  write method anywhere.
* **枚举钉旧 gotcha — no hardcoded source count.** The live source list is derived
  from the facade's ``known_sources()`` at call time; no integer literal pins a
  source-registry count anywhere in this module.

This module defines **no** new registered contract model: the source is a plain
service object and the builders return existing Phase-1/Phase-3 models. The
descriptor helper returns an existing :class:`DataSourceDescriptor` bound to the
existing ``verified_snapshot`` / ``news`` / ``ohlcv`` method refs (Task 9 owns any
cumulative-registry classification).
"""
from __future__ import annotations

import math
from datetime import date as _date, datetime, time as _time, timedelta, timezone
from typing import Any, Callable, Mapping
from zoneinfo import ZoneInfo

from guanlan_v2.orchestration.context import DataContext
from guanlan_v2.orchestration.data.calendar import TradingCalendar
from guanlan_v2.orchestration.data.errors import (
    NotConfiguredError,
    RoutingConfigurationError,
    SourceBrokenError,
)
from guanlan_v2.orchestration.data.pit import RawRowCandidate
from guanlan_v2.orchestration.data.snapshot import (
    DataRoutingSnapshot,
    DataSnapshotEntry,
    DataSnapshotManifest,
    DataSourceConfigSnapshot,
    build_data_context,
    build_data_snapshot_manifest,
)
from guanlan_v2.orchestration.data.source import (
    DataInvocationScope,
    DataMethodSpec,
    DataRequest,
    DataSourceDescriptor,
    DataSourceRegistrySnapshot,
    RawFetch,
    build_raw_fetch,
)
from guanlan_v2.orchestration.enums import DataBackend, DataMode
from guanlan_v2.orchestration.refs import ContentRef, SchemaRef
from guanlan_v2.orchestration.runtime_clock import AuthoritativeClock

__all__ = [
    "LiveClientSource",
    "build_online_live_descriptor",
    "build_online_capture_manifest",
    "build_online_data_context",
    "ONLINE_SOURCE_ID",
    "ONLINE_SOURCE_VERSION",
]

#: the reviewed logical id + version of the ONLINE live source.
ONLINE_SOURCE_ID = "guanlan.online_live"
ONLINE_SOURCE_VERSION = "1"

_VERIFIED_SNAPSHOT_METHOD_ID = "verified_snapshot"
_NEWS_METHOD_ID = "news"
_OHLCV_METHOD_ID = "ohlcv"
#: the existing Phase-3 method-id families this adapter binds (no new id is minted):
#: per-code realtime quote → ``verified_snapshot``; announcement/news-style text
#: feeds → ``news``; market-wide tape snapshot → ``ohlcv``.
_SUPPORTED_METHOD_IDS = frozenset(
    {_VERIFIED_SNAPSHOT_METHOD_ID, _NEWS_METHOD_ID, _OHLCV_METHOD_ID}
)

#: the reviewed method-id → ``live_client`` facade source-id binding. This is a
#: fixed *routing* map (which live source serves each method), NOT a source-registry
#: enumeration — the live source LIST is derived from ``known_sources()`` at call
#: time (see :meth:`LiveClientSource.live_source_ids`), never a pinned count.
_METHOD_LIVE_SOURCE: dict[str, str] = {
    _VERIFIED_SNAPSHOT_METHOD_ID: "tencent_realtime_quote",   # per-code realtime quote
    _NEWS_METHOD_ID: "eastmoney_stock_news",                  # announcement/news feeds
    _OHLCV_METHOD_ID: "eastmoney_market_fund_flow",           # market-wide tape snapshot
}

_CONFIG_SCHEMA_REF = SchemaRef(name="DataSourceConfigSnapshot", version="1")

#: the ``live_client`` facade records ``pulled_at`` as a naive system-local wall time
#: (the deployment is the A-share Beijing domain), so a naive ``pulled_at`` is
#: localized under this zone and normalized to UTC — never fabricated.
_EXCHANGE_TZ = ZoneInfo("Asia/Shanghai")
#: item keys a news/announcement row's headline may live under (best-effort, honest
#: projection of the live row — never a fabricated headline).
_NEWS_TITLE_KEYS = ("title", "headline", "text", "name", "summary", "content")
_CLIP = 400

# --------------------------------------------------------------------------- #
# the SETTLED-projection rule (CONTROLLER RULING — the L2-b Task-9 gate,       #
# with its ADDENDUM: calendar-attributed session, venue-side anchor)           #
# --------------------------------------------------------------------------- #
#: **NAMED DOMAIN RULE, not a fabrication.** An A-share trading session's CLOSE price
#: is exchange-settled and publicly disseminated at the session close, **15:00
#: Asia/Shanghai**. A settled datum's own availability instant is therefore its own
#: session date at this local time, normalized to UTC.
#:
#: This encodes the CONTROLLER RULING recorded in
#: ``.superpowers/sdd/progress-orchestration.md`` (Task-7 concern 1 / the Task-9
#: gate): *"THE FIX IS STAMPING SEMANTICS: verified_snapshot/settled data must carry
#: available_at = the data's OWN availability time (bar settlement/publication), not
#: pulled_at; data genuinely available before the midnight boundary then passes
#: PitGuard honestly. Intraday ticks under a midnight context stay typed-refused —
#: that IS honest PIT behavior."* The ruling keeps gate D-C intact (the run's
#: ``ctx.as_of`` stays the session-midnight instant the admitted context describes),
#: so the ONLY honest passer under that boundary is settled data.
_SETTLED_CLOSE_LOCAL_TIME = _time(hour=15, minute=0)

#: Row keys that carry the **settled previous-session CLOSE**, bound at source:
#: ``prev_close`` is the key the ``verified_snapshot`` record family consumes and the
#: key the reviewed adapter fixtures carry; ``last_close`` is the key the real
#: ``tencent_realtime_quote`` handler emits (stocks
#: ``src/data/live_text_sources.py::tencent_quote``); ``pre_close`` is the common
#: alias of the same 昨收 datum. First match wins; nothing is derived or averaged.
_SETTLED_CLOSE_KEYS = ("prev_close", "last_close", "pre_close")

#: Row keys carrying the vendor row's own **QUOTE TIMESTAMP** — the venue's
#: last-trade time, i.e. the ANCHOR the settled session is attributed from (the
#: RULING ADDENDUM; see :meth:`LiveClientSource._settled_candidate` for the
#: two-regime proof). This deliberately ACCEPTS a bare ``date``/``time``: on a
#: realtime-quote row that is the venue's CURRENT trading date, which is exactly
#: the anchor — it is never taken as the settled session (the settled session is
#: the committed calendar's session strictly BEFORE it).
_QUOTE_TIME_KEYS = (
    "quote_time", "quote_ts", "tick_time", "ticktime", "trade_time",
    "datetime", "date", "time",
)

#: Item-level (unified stocks ``LiveSourceItem``) timestamp keys, accepted as the
#: anchor ONLY when the item's own ``time_source`` proves the value is a PROVIDER
#: time. The item shaper stamps ``time_source="fetch_fallback"`` whenever the
#: vendor row carried no time of its own (stocks
#: ``src/data/live_sources.py::_item_from_dict``) — that value is the FETCH
#: instant, not a venue quote time, and stamping a settled session from it would
#: be exactly the pull-instant lie the ruling forbids.
_ITEM_QUOTE_TIME_KEYS = ("publish_ts", "visible_ts")

#: ``time_source`` prefixes that prove the item's timestamp is NOT venue-side.
_UNPROVEN_TIME_SOURCES = ("fetch_fallback", "unknown")

#: Row keys carrying the instrument's settled display name (static reference data).
_SETTLED_NAME_KEYS = ("name", "sec_name", "short_name")


# --------------------------------------------------------------------------- #
# availability + payload helpers (pure)                                        #
# --------------------------------------------------------------------------- #
def _online_availability(value: Any) -> datetime | None:
    """Derive an aware-UTC ``available_at`` from the envelope ``pulled_at`` — no fabrication.

    The facade stamps ``pulled_at`` as a naive local wall time (e.g.
    ``"2026-07-16T15:00:00"``); a naive value is localized to :data:`_EXCHANGE_TZ`
    and normalized to UTC, an already-aware value is normalized to UTC. A missing /
    empty / unparseable value yields ``None`` — the row is still forwarded, and the
    PitGuard refuses it as :class:`MissingAvailabilityRefused`. The adapter never
    emits a *naive* ``available_at`` (a naive instant is not canonically digestible
    and cannot prove point-in-time).
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        s = value.strip().replace(" ", "T")
        if not s:
            return None
        try:
            parsed = datetime.fromisoformat(s)
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=_EXCHANGE_TZ)  # localize a naive local wall-time
    return parsed.astimezone(timezone.utc)


def _clip(value: str) -> str:
    return value if len(value) <= _CLIP else value[:_CLIP] + "…"


def _first_str(item: Mapping[str, Any], keys: tuple[str, ...]) -> str | None:
    for k in keys:
        v = item.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return None


def _first_symbol(params: Mapping[str, Any]) -> dict[str, Any] | None:
    syms = params.get("symbols")
    if syms:
        first = syms[0]
        if isinstance(first, Mapping):
            return dict(first)
    sym = params.get("symbol")
    if isinstance(sym, Mapping):
        return dict(sym)
    return None


def _json_safe_row(item: Mapping[str, Any]) -> dict[str, Any]:
    """An honest JSON-safe projection of a live row (scalar fields only, clipped)."""
    out: dict[str, Any] = {}
    for k, v in item.items():
        key = str(k)
        if isinstance(v, bool) or v is None or isinstance(v, int):
            out[key] = v
        elif isinstance(v, float) and math.isfinite(v):
            out[key] = v
        elif isinstance(v, str):
            out[key] = _clip(v)
    return out


def _row_payload(method_id: str, item: Mapping[str, Any], params: Mapping[str, Any]) -> dict[str, Any]:
    """Wrap one live row into a canonical raw payload (never fabricated data).

    ``news`` yields a headline-bearing payload (the honest projection of the live
    row's own text); ``verified_snapshot`` / ``ohlcv`` carry the JSON-safe live-row
    projection plus the requested symbol. The concrete record-field mapping (last
    price / OHLCV) that :meth:`from_candidate` will consume is Task 7's fidelity
    concern — this task's PIT firewall keys only on ``available_at``.
    """
    row = _json_safe_row(item) if isinstance(item, Mapping) else {}
    if method_id == _NEWS_METHOD_ID:
        headline = _first_str(item, _NEWS_TITLE_KEYS) if isinstance(item, Mapping) else None
        payload: dict[str, Any] = {"headline": headline or _clip(str(row) or "(live item)")}
        url = _first_str(item, ("url", "link")) if isinstance(item, Mapping) else None
        if url:
            payload["url"] = url
        sym = _first_symbol(params)
        if sym is not None:
            payload["symbol"] = sym
        return payload
    sym = _first_symbol(params)
    if sym is not None:
        return {"symbol": sym, **row}
    return row


def _vendor_row_view(item: Mapping[str, Any]) -> dict[str, Any]:
    """The item's own fields overlaid with the vendor's nested ``raw`` sub-row.

    The ``live_client`` facade wraps every vendor row in the unified stocks item
    (``LiveSourceItem.as_dict``) whose ``raw`` key carries the vendor's OWN row; the
    facade's own consumer helper (``live_client.native_rows``) uses exactly this
    precedence (raw first, item fields as fallback). A flat vendor row (the shape the
    reviewed fixtures carry) passes through unchanged. Read-only projection: this
    view is used ONLY to look up settled fields, never to build the live payload.
    """
    view = {str(k): v for k, v in item.items() if k != "raw"}
    raw = item.get("raw")
    if isinstance(raw, Mapping):
        view.update({str(k): v for k, v in raw.items() if k != "raw"})
    return view


def _as_session_date(value: Any) -> _date | None:
    """Parse a row's own timestamp value down to its DATE — ``None`` otherwise.

    Accepts a ``date`` / ``datetime`` (its date part), an ISO ``YYYY-MM-DD``
    prefix (so ``"2026-07-16T10:30:00"`` and ``"2026-07-16 10:30:00"`` both read)
    or a compact ``YYYYMMDD`` prefix (so the vendor's ``"20260716103000"``
    stamp reads too). Anything else yields ``None``: a date that cannot be *read*
    is never guessed at.
    """
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, _date):
        return value
    if not isinstance(value, str):
        return None
    s = value.strip()
    if not s:
        return None
    try:
        return _date.fromisoformat(s[:10])
    except ValueError:
        pass
    if len(s) >= 8 and s[:8].isdigit():
        try:
            return _date(int(s[:4]), int(s[4:6]), int(s[6:8]))
        except ValueError:
            return None
    return None


def _quote_anchor_date(view: Mapping[str, Any]) -> _date | None:
    """The row's own **venue quote date** — the settled-attribution anchor.

    Two bound sources, in precedence order (both venue-side facts, never the
    pull instant and never ``as_of``):

    1. the vendor row's own quote-timestamp key (:data:`_QUOTE_TIME_KEYS`);
    2. the unified item's ``publish_ts`` / ``visible_ts`` — but ONLY when the
       item's own ``time_source`` proves it is a provider time. The stocks item
       shaper falls back to the FETCH instant with ``time_source="fetch_fallback"``
       whenever the vendor row carries no time of its own, and a fetch instant is
       not a quote time.

    ``None`` when neither is readable — the caller then emits **no** settled
    candidate at all.
    """
    for key in _QUOTE_TIME_KEYS:
        anchor = _as_session_date(view.get(key))
        if anchor is not None:
            return anchor
    src = view.get("time_source")
    if isinstance(src, str) and src.strip() and not src.strip().lower().startswith(
            _UNPROVEN_TIME_SOURCES):
        for key in _ITEM_QUOTE_TIME_KEYS:
            anchor = _as_session_date(view.get(key))
            if anchor is not None:
                return anchor
    return None


def _previous_session(calendar: TradingCalendar, anchor: _date) -> _date | None:
    """The committed calendar's latest session **strictly before** ``anchor``.

    Answered purely from the calendar's own immutable material — no wall clock,
    no ``as_of``, no extrapolation. Returns ``None`` (i.e. the caller emits no
    settled candidate) whenever the material cannot ANSWER:

    * empty material / ``anchor`` outside the covered span — counting past the
      material is the silent-undercount lie ``data/calendar.py``'s coverage
      contract exists to forbid;
    * ``anchor`` is not itself a session — a real venue quote timestamp always
      falls on a trading session, so a non-session anchor proves the value is
      not one (e.g. a weekend pull instant), and attributing from it would
      mis-stamp by a whole session;
    * no session precedes ``anchor`` inside the covered span.
    """
    coverage = calendar.coverage
    if coverage is None:
        return None
    lo, hi = coverage
    if anchor < lo or anchor > hi or not calendar.is_session(anchor):
        return None
    day = anchor - timedelta(days=1)
    while day >= lo:
        if calendar.is_session(day):
            return day
        day -= timedelta(days=1)
    return None


def _settled_close(view: Mapping[str, Any]) -> float | None:
    """The row's own settled close — a finite number under a bound key, or ``None``."""
    for key in _SETTLED_CLOSE_KEYS:
        v = view.get(key)
        if isinstance(v, bool) or v is None:
            continue
        if isinstance(v, (int, float)):
            f = float(v)
            if math.isfinite(f):
                return f
    return None


def _settled_instant(session: _date) -> datetime:
    """The settled availability instant of ``session``: its close, in aware UTC.

    :data:`_SETTLED_CLOSE_LOCAL_TIME` at :data:`_EXCHANGE_TZ`, normalized to UTC —
    the data's OWN availability time, derived from the row's own session date by the
    named domain rule, never from a wall clock and never from ``pulled_at``.
    """
    local = datetime.combine(session, _SETTLED_CLOSE_LOCAL_TIME, tzinfo=_EXCHANGE_TZ)
    return local.astimezone(timezone.utc)


def _settled_payload(
    view: Mapping[str, Any], session: _date, close: float, params: Mapping[str, Any]
) -> dict[str, Any]:
    """The settled projection's payload — settled fields ONLY.

    Carries the settled session's date, the settled close and the static reference
    fields (requested symbol identity + instrument name). **No intraday field ever
    enters** (current price, open/high/low, today's volume, bid/ask, today's change):
    claiming an intraday number was available at yesterday's close is exactly the lie
    the stamping rule exists to prevent.

    Both price slots the ``VerifiedSnapshotRecord`` family consumes (``last_price``
    and ``prev_close``, ``data/source.py:285-305``) carry the SAME single settled
    observation: at the settlement instant the session's last traded price IS its
    close. The projection reports one observed number in both slots and invents no
    second observation — a consumer computing an intraday change from a settled
    projection therefore reads 0.0, which is honest for a settled snapshot (the
    record model has no "unknown" slot).
    """
    payload: dict[str, Any] = {
        "last_price": close,
        "prev_close": close,
        "settled_session_date": session.isoformat(),
    }
    sym = _first_symbol(params)
    if sym is not None:
        payload["symbol"] = sym
    name = _first_str(view, _SETTLED_NAME_KEYS)
    if name:
        payload["name"] = name
    return payload


def _probe_args(method_id: str, params: Mapping[str, Any]) -> tuple[str, str, int]:
    """Derive ``(code, date, limit)`` for a facade probe from the validated params.

    A per-code method (``news`` / ``verified_snapshot``) carries the requested
    instrument as ``symbols[0]``; the market-wide tape (``ohlcv``) is a no-code
    whole-market snapshot. ``date`` stays current ("") — ONLINE reads the live
    boundary, never a historical date.
    """
    raw_limit = params.get("limit")
    limit = raw_limit if isinstance(raw_limit, int) and not isinstance(raw_limit, bool) else 20
    if method_id == _OHLCV_METHOD_ID:
        return ("", "", limit)  # market-wide tape snapshot — no per-code arg
    syms = params.get("symbols") or ()
    code = ""
    if syms:
        first = syms[0]
        if isinstance(first, Mapping):
            exchange = str(first.get("exchange") or "")
            base = str(first.get("code") or "")
            code = f"{exchange}{base}" if exchange else base
    return (code, "", limit)


# --------------------------------------------------------------------------- #
# LiveClientSource                                                            #
# --------------------------------------------------------------------------- #
class LiveClientSource:
    """The ONLINE live-data adapter (a Phase-3 ``DataSource``).

    Reads the ``live_client`` facade and wraps every probe-envelope row into a
    :class:`RawRowCandidate` with ``available_at = <envelope pulled_at>`` — **without**
    pre-filtering by time. The single PIT authority is the reviewed Phase-3 router's
    ``PitGuard.check_raw`` (``data/registry.py`` dispatch step 5c), which refuses any
    future (``pulled_at > as_of``) / missing-availability row loudly. No method takes
    a caller ``as_of`` / ``strict`` / ``now`` override — the boundary is
    ``request.as_of`` (the start-frozen ``DataContext`` as-of) only.

    Facade functions default to the live ``guanlan_v2.datafeed.live_client``
    surface, lazily imported at call time (import purity); tests inject fakes and
    hit no real vendor.
    """

    __slots__ = ("_probe_fn", "_catalog_fn", "_resolve_fn", "_known_fn", "_calendar")

    def __init__(
        self,
        *,
        probe_fn: Callable[..., Mapping[str, Any]] | None = None,
        catalog_fn: Callable[..., Mapping[str, Any]] | None = None,
        resolve_source_fn: Callable[[str], str] | None = None,
        known_sources_fn: Callable[[], Any] | None = None,
        calendar: TradingCalendar | None = None,
    ) -> None:
        # The brief's __init__ lists probe_fn/catalog_fn; resolve_source_fn/
        # known_sources_fn are added (defaulting to the facade) so invariant 4
        # (unknown source fails loud) and the 枚举钉旧 gotcha guard can be exercised
        # against injected fakes without touching a real vendor (recorded in report).
        #
        # ``calendar`` is the RULING ADDENDUM's committed session calendar: the
        # ONLY thing that can attribute a quote row's settled close to a session
        # (see :meth:`_settled_candidate`). It is OPTIONAL and defaults to
        # ``None`` — a calendar-less construction is legal and simply emits no
        # settled projection (no guessed attribution, no silent stand-in
        # calendar). Production binds the recipe's committed material at
        # ``adapters/data_world.py::production_data_adapters``; unit harnesses
        # that never exercise the projection keep constructing without one.
        self._probe_fn = probe_fn
        self._catalog_fn = catalog_fn
        self._resolve_fn = resolve_source_fn
        self._known_fn = known_sources_fn
        self._calendar = calendar

    # -- lazy facade binders (defaults; tests inject) ----------------------- #
    def _probe(self, source: str, *, code: str, date: str, limit: int) -> Mapping[str, Any]:
        fn = self._probe_fn
        if fn is None:
            from guanlan_v2.datafeed.live_client import probe as fn
        return fn(source, code=code, date=date, limit=limit)

    def _resolve(self, source: str) -> str:
        fn = self._resolve_fn
        if fn is None:
            from guanlan_v2.datafeed.live_client import resolve_source as fn
        return fn(source)

    def _known(self) -> list[str]:
        fn = self._known_fn
        if fn is None:
            from guanlan_v2.datafeed.live_client import known_sources as fn
        return list(fn())

    def catalog(self, **kwargs: Any) -> Mapping[str, Any]:
        """The live source catalog (facade passthrough) — dynamic, never cached here."""
        fn = self._catalog_fn
        if fn is None:
            from guanlan_v2.datafeed.live_client import catalog as fn
        return fn(**kwargs)

    def live_source_ids(self) -> tuple[str, ...]:
        """The bound live source ids currently resolvable via the facade (dynamic).

        Derived from ``known_sources()`` at call time (the 枚举钉旧 gotcha guard: the
        source list is never a pinned integer count), intersected with this adapter's
        reviewed method→source binding, ordered by method id for stability.
        """
        known = set(self._known())
        return tuple(
            _METHOD_LIVE_SOURCE[mid]
            for mid in sorted(_SUPPORTED_METHOD_IDS)
            if _METHOD_LIVE_SOURCE[mid] in known
        )

    # -- the DataSource protocol surface (read-only) ------------------------ #
    def fetch(self, request: DataRequest, *, scope: DataInvocationScope) -> RawFetch:
        method_id = request.method_spec_ref.id
        if method_id not in _SUPPORTED_METHOD_IDS:
            raise RoutingConfigurationError(
                f"LiveClientSource serves only {sorted(_SUPPORTED_METHOD_IDS)}; "
                f"method {method_id!r} is not bound to this source"
            )
        live_source = _METHOD_LIVE_SOURCE[method_id]
        # invariant 4: source ids are resolved through the facade's resolve_source;
        # an unknown source fails loud (no silent substitute). A resolver that raises
        # propagates unmasked.
        canonical = self._resolve(live_source)
        if not canonical:
            raise RoutingConfigurationError(
                f"live source {live_source!r} for method {method_id!r} is not "
                "resolvable via the live_client facade (unknown source); no silent "
                "substitute"
            )

        code, date, limit = _probe_args(method_id, request.params)
        envelope = self._probe(canonical, code=code, date=date, limit=limit)
        if not isinstance(envelope, Mapping):
            raise SourceBrokenError(
                f"live probe for source={canonical} method={method_id} returned a "
                f"non-mapping envelope {type(envelope).__name__}; the result is void"
            )

        # -- honest degradation red line (never fabricate, never silent swap) --- #
        if not envelope.get("ok", False):
            # ok:False = caller/mechanical fault → raise-bucket, loud, no advance.
            raise RoutingConfigurationError(
                f"live probe rejected the request (source={canonical} method={method_id}): "
                f"{envelope.get('note') or 'ok:false'}"
            )
        status = str(envelope.get("status") or "")
        if status == "planned":
            # source registered but its probe is not implemented/available = not
            # configured; the ONLY (with rate-limit) taxonomy error that advances the
            # frozen chain, then exhausts an optional method to an honest UNAVAILABLE.
            raise NotConfiguredError(
                f"live source {canonical!r} is registered but its probe is not "
                f"implemented (status=planned; method={method_id}): "
                f"{envelope.get('note') or ''}"
            )
        if status == "error":
            # upstream errored — surface loud, never a silent vendor swap.
            raise SourceBrokenError(
                f"live source {canonical!r} returned an upstream error "
                f"(status=error; method={method_id}): "
                f"{envelope.get('error') or envelope.get('note') or ''}"
            )

        # -- ok/data-bearing: wrap each row with ITS OWN availability ----------- #
        avail = _online_availability(envelope.get("pulled_at"))
        items = envelope.get("items") or ()
        candidates = tuple(
            self._row_candidate(request, method_id, item, avail) for item in items
        )
        entry = scope.frozen_route.entries[0]
        # fetched_at is the FROZEN as-of (audit-only) — no wall-clock read exists here.
        return build_raw_fetch(
            request_digest=request.request_digest, source_ref=entry.source_ref,
            capability_ref=entry.capability_ref, candidates=candidates,
            subsource=canonical, fetched_at=request.as_of, provider_request_id=None,
        )

    def _row_candidate(
        self, request: DataRequest, method_id: str, item: Any, avail: datetime | None
    ) -> RawRowCandidate:
        """One candidate per envelope row, stamped with THAT row's own availability.

        A ``verified_snapshot`` row that carries its own settled datum (a settled
        close + a venue quote timestamp the committed calendar can attribute to a
        preceding session) becomes the **settled projection**
        (:meth:`_settled_candidate`); every other row — every other method, any
        quote row with no readable settled datum or no venue anchor, and every row
        when this source was constructed without a calendar — becomes the
        unchanged **live** candidate stamped with the envelope's ``pulled_at``.

        **Why the settled projection SUPERSEDES that row's live candidate** (a
        recorded correction to the pre-fix brief's "in addition", verified at
        source): ``PitGuard.check_raw`` is ALL-OR-NOTHING, not per-row — a single
        future candidate raises :class:`FutureDataRefused` for the WHOLE batch
        (``data/pit.py:485-540``) and the reviewed dispatch re-raises it unchanged
        (``data/registry.py:704-708``). Emitting both candidates for the same row
        would therefore make EVERY intraday read refuse, and the ruling's own exit
        criterion (pm's row completes a real read over settled data) would be
        structurally unreachable. Nothing is lost by the supersede: ``as_of`` is
        frozen at run START and the probe necessarily happens after it, so a live
        tick's ``pulled_at`` can never precede the boundary anyway — the ruling's own
        premise. The adapter still performs **zero** time filtering/comparison
        against ``as_of``: it forwards exactly one candidate per row and never drops
        one; the single PIT authority remains ``PitGuard.check_raw``.
        """
        if method_id == _VERIFIED_SNAPSHOT_METHOD_ID and isinstance(item, Mapping):
            settled = self._settled_candidate(request, item)
            if settled is not None:
                return settled
        return self._candidate(request, method_id, item, avail)

    def _settled_candidate(
        self, request: DataRequest, item: Mapping[str, Any]
    ) -> RawRowCandidate | None:
        """The settled projection of one quote row, or ``None`` when there is none.

        **Session attribution comes from VENUE-SIDE facts + the COMMITTED
        CALENDAR** (the RULING ADDENDUM) — never from an ``as_of`` comparison and
        never from a guessed date:

        1. the ANCHOR is the row's own **quote timestamp** (the venue's last-trade
           time — :func:`_quote_anchor_date`);
        2. ``settled_session`` is the committed calendar's latest session
           **strictly before** the anchor's session (:func:`_previous_session`).

        **The two-regime proof — one rule answers both.** A realtime-quote row's
        ``last_close`` is, by the venue's own definition, the close of the session
        preceding the session the quote belongs to. So:

        * **intraday pull** — the venue is serving TODAY's snapshot, quote_time =
          today ``T`` (a session), and ``last_close`` is the close of the session
          before ``T``: the rule names exactly that session;
        * **weekend / holiday / pre-open pull** — the venue is still serving the
          LAST COMPLETED session's snapshot, so quote_time = that session ``F``
          (its close), and the row's ``last_close`` is the close of the session
          before ``F``: the rule names exactly that session too.

        Anchoring on the *pull* instant instead would break regime 2 (a Saturday
        pull would name Friday, whose close is in ``price``, not ``last_close``) —
        which is precisely why the anchor must be venue-side.

        ``available_at`` / ``effective_at`` = that settled session's date at
        :data:`_SETTLED_CLOSE_LOCAL_TIME` (:data:`_EXCHANGE_TZ`), normalized to
        UTC — the named domain rule that an A-share session's close is
        exchange-settled and public at 15:00 local (the CONTROLLER RULING this
        module implements). ``ingested_at`` stays the frozen ``request.as_of``
        (the unchanged idiom). The payload carries settled fields ONLY.

        Returns ``None`` — i.e. emits NO settled candidate — when this source was
        constructed without a calendar, when the row carries no readable settled
        close, when no venue quote timestamp is readable, or when the committed
        calendar cannot ANSWER which session precedes the anchor (outside its
        covered span, a non-session anchor, or no earlier covered session). The
        live candidate's honest PitGuard refusal then stands as the outcome.
        """
        calendar = self._calendar
        if calendar is None:
            return None
        view = _vendor_row_view(item)
        close = _settled_close(view)
        if close is None:
            return None
        anchor = _quote_anchor_date(view)
        if anchor is None:
            return None
        session = _previous_session(calendar, anchor)
        if session is None:
            return None
        instant = _settled_instant(session)
        return RawRowCandidate.build(
            raw_payload=_settled_payload(view, session, close, request.params),
            effective_at=instant, available_at=instant, ingested_at=request.as_of,
        )

    def _candidate(
        self, request: DataRequest, method_id: str, item: Any, avail: datetime | None
    ) -> RawRowCandidate:
        """Wrap one row — forwarded whatever its availability proves (never dropped).

        ``avail`` is the envelope's ``pulled_at`` (aware-UTC) or ``None`` (missing
        ``pulled_at``): the row is forwarded either way, and the PitGuard is the
        single authority that refuses a future / missing availability.
        """
        payload = _row_payload(method_id, item if isinstance(item, Mapping) else {}, request.params)
        return RawRowCandidate.build(
            raw_payload=payload, effective_at=avail, available_at=avail,
            ingested_at=request.as_of,
        )


# --------------------------------------------------------------------------- #
# build_online_live_descriptor                                                #
# --------------------------------------------------------------------------- #
def build_online_live_descriptor(
    *, method_specs: tuple[DataMethodSpec, ...], handler_ref: ContentRef
) -> DataSourceDescriptor:
    """Build the reviewed ONLINE-only :class:`DataSourceDescriptor`.

    Binds the **existing** ``verified_snapshot`` / ``news`` / ``ohlcv`` method refs
    (no new id is minted); supported modes are exactly ``(ONLINE,)`` and the backend
    exactly ``(LIVE,)`` — ``DataMode`` routing can therefore never select this source
    for a ``PIT_REPLAY`` read (the structural mode-exclusion mirror of Task 2's
    ``(PIT_REPLAY,)`` descriptor). Every bound method spec is ``read_only=True`` by
    its own frozen contract (red line §10: the live adapter 不注册订单/信号写工具).
    """
    bound = tuple(
        sorted(
            (s for s in method_specs if s.method_id in _SUPPORTED_METHOD_IDS),
            key=lambda s: s.method_id,
        )
    )
    have = {s.method_id for s in bound}
    if have != set(_SUPPORTED_METHOD_IDS):
        missing = sorted(set(_SUPPORTED_METHOD_IDS) - have)
        raise ValueError(
            f"ONLINE descriptor requires the existing {missing} method spec(s) in "
            "method_specs (no new DataMethodSpec id may be minted)"
        )
    caps = tuple(sorted({s.capability_ref for s in bound}, key=lambda c: (c.id, c.version)))
    return DataSourceDescriptor.build(
        source_id=ONLINE_SOURCE_ID, source_version=ONLINE_SOURCE_VERSION,
        method_refs=tuple(s.method_ref for s in bound),
        method_capability_refs=caps,
        supported_modes=(DataMode.ONLINE,),
        supported_backends=(DataBackend.LIVE,),
        handler_ref=handler_ref,
        source_config_schema_ref=_CONFIG_SCHEMA_REF,
    )


# --------------------------------------------------------------------------- #
# build_online_capture_manifest                                               #
# --------------------------------------------------------------------------- #
def build_online_capture_manifest(
    *,
    data_snapshot_id: str,
    as_of: datetime,
    timezone: str,
    calendar_id: str,
    routing_snapshot_digest: str,
    schema_registry_digest: str,
    entries: tuple[DataSnapshotEntry, ...] = (),
) -> DataSnapshotManifest:
    """Seal an ``online_capture_root`` snapshot manifest for one ONLINE run.

    ``data_snapshot_id`` is the physical capture root/locator — **audit-only**
    (excluded from the manifest's semantic digest). ``as_of`` must equal the run's
    start-frozen instant (the caller's :func:`build_online_data_context` re-asserts
    this through ``build_data_context``). Unlike Task 2's ``pit_frozen`` manifest an
    ``online_capture_root`` is not a complete vintage: it freezes the run-start
    boundary + routing, and subsequently recorded ``DataResult``\\ s append under the
    root without mutating its digest (per the Phase-3 manifest contract). ONLINE
    therefore carries a capture-root content digest while the derived ``DataContext``
    omits a vintage manifest digest.
    """
    return build_data_snapshot_manifest(
        data_snapshot_id=data_snapshot_id,
        manifest_kind="online_capture_root", as_of=as_of,
        mode=DataMode.ONLINE, timezone=timezone, calendar_id=calendar_id,
        routing_snapshot_digest=routing_snapshot_digest,
        schema_registry_digest=schema_registry_digest, entries=entries,
    )


# --------------------------------------------------------------------------- #
# build_online_data_context                                                   #
# --------------------------------------------------------------------------- #
def build_online_data_context(
    *,
    clock: AuthoritativeClock,
    source_config: DataSourceConfigSnapshot,
    source_registry: DataSourceRegistrySnapshot,
    routing: DataRoutingSnapshot,
    manifest: DataSnapshotManifest,
) -> DataContext:
    """Build the start-frozen ONLINE :class:`DataContext` for one run.

    Delegates to the ONE Phase-3 :func:`build_data_context` with ``mode=ONLINE`` /
    ``backend=LIVE``. ``build_data_context`` reads ``clock.now()`` **exactly once**
    (and builds the ``ClockSpec`` from that single reading), so the returned
    context's ``as_of`` is the run's start-frozen instant: a clock that advances
    afterwards never changes it. This function therefore performs **no** second clock
    read and builds no redundant ``ClockSpec`` — a second read would break the freeze
    for an advancing clock (recorded in the report as a C-clause correction against
    the brief's "reads clock.now() … builds ClockSpec" wording, which the reviewed
    ``build_data_context`` already owns). ONLINE omits ``vintage_manifest_digest``
    (``None``) while carrying the capture-root content digest; Phase-1 coherence is
    enforced by the ``DataContext`` validator + ``build_data_context``'s mode matrix.
    """
    return build_data_context(
        clock, mode=DataMode.ONLINE, backend=DataBackend.LIVE,
        source_config=source_config, source_registry=source_registry,
        routing=routing, manifest=manifest,
    )
