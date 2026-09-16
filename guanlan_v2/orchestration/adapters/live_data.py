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
  announcement/news-style text feeds, ``ohlcv`` for per-instrument TDX daily bars —
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
  dependency: without one the projection simply never fires. A ``news`` row whose
  own **publication** instant is PIT-CERTIFIED by the vendor's temporal contract is
  the same rule read on the text side: it is stamped at that instant
  (:func:`_attested_publication` — gated on ``pit_certified`` + ``time_source``,
  never on the constant ``visible_ts_reason``), and a row whose own certified
  publication postdates the frozen ``as_of`` is EXCLUDED rather than handed to the
  all-or-nothing guard, where it would refuse the whole batch of long-public rows
  (:meth:`LiveClientSource._news_candidates` — the live ``deep-78abf946944b9984``
  regression). The exclusion only ever excludes, and never silently: no
  availability is fabricated or backdated, an UNCERTIFIED news row keeps
  ``pulled_at`` and is forwarded unchanged for the guard to judge, every exclusion
  is counted onto the module logger, and the other two routes perform no
  time comparison at all. A missing
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

* **No fabricated rows, no silent vendor swap.** The accepted data states are the
  exact closed set ``ok/empty/not_available``; only the exact transient states
  ``provider_busy/rate_limited`` raise :class:`RateLimitError`. Every unknown,
  legacy or structurally inconsistent state raises :class:`SourceBrokenError`
  (surfaces loud, never advances the frozen chain). None of these fabricates a row
  or silently substitutes a different vendor's answer.
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

import logging
import math
import threading
from collections import OrderedDict
from datetime import date as _date, datetime, time as _time, timedelta, timezone
from typing import Any, Callable, Mapping
from zoneinfo import ZoneInfo

from guanlan_v2.orchestration.adapters.live_binding import (
    ReviewedLiveBinding,
    ReviewedLiveBindingSnapshot,
    reviewed_live_binding_snapshot,
    verify_code_approved_live_binding_snapshot,
)
from guanlan_v2.orchestration.context import DataContext
from guanlan_v2.orchestration.data.calendar import TradingCalendar
from guanlan_v2.orchestration.data.errors import (
    RateLimitError,
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
from guanlan_v2.orchestration.digest import content_digest
from guanlan_v2.orchestration.refs import ContentRef, PayloadRef, SchemaRef
from guanlan_v2.orchestration.runtime_clock import AuthoritativeClock

__all__ = [
    "LiveClientSource",
    "build_online_live_descriptor",
    "build_online_capture_manifest",
    "build_online_data_context",
    "build_precaptured_indicator_fetch",
    "bind_precaptured_indicator_store_lookup",
    "register_precaptured_indicator_fetch",
    "ONLINE_SOURCE_ID",
    "ONLINE_SOURCE_VERSION",
    "POST_CLOSE_RETAINED_BOOK_REVISION_PREFIX",
]

_LOG = logging.getLogger(__name__)

#: the reviewed logical id + version of the ONLINE live source.
ONLINE_SOURCE_ID = "guanlan.online_live"
ONLINE_SOURCE_VERSION = "1"

_VERIFIED_SNAPSHOT_METHOD_ID = "verified_snapshot"
_NEWS_METHOD_ID = "news"
_OHLCV_METHOD_ID = "ohlcv"
_INDICATORS_METHOD_ID = "indicators"
#: The executable supported set is derived from the code-approved binding
#: snapshot, never from the observed stocks manifest.  No method/schema/capability
#: is minted here; every id is still an existing Phase-3 method.
_DEFAULT_BINDING_SNAPSHOT = reviewed_live_binding_snapshot()
_SUPPORTED_METHOD_IDS = frozenset(
    binding.method_id for binding in _DEFAULT_BINDING_SNAPSHOT.bindings
)

#: Price-action's reviewed minimum and one conservative live fetch window.  The
#: source excludes an as-yet-unclosed current-day bar before the PIT guard, so a
#: request for exactly twenty vendor rows could otherwise strand the consumer at
#: nineteen.  120 remains below the facade's hard 300-row cap.
_OHLCV_MIN_BARS = 20
_OHLCV_PROBE_LIMIT = 120

#: A deep run freezes its ONLINE context *after* capturing the provider-timed
#: five-level book.  The runtime adapter must then replay that exact persisted
#: ``RawFetch``; probing again would create a different, post-freeze observation.
#: Bindings are process-local locators into the durable payload store (the raw
#: value itself is never cached here), bounded to avoid retaining old store
#: closures indefinitely.  A missing binding fails closed in ``fetch``.
_DEEP_CAPTURE_PREFIX = "deep-capture-"
_PRECAPTURED_INDICATOR_MAX = 128
_PRECAPTURED_INDICATOR_LOCK = threading.RLock()
_PRECAPTURED_INDICATOR_FETCHES: OrderedDict[
    str, tuple[PayloadRef, Callable[[PayloadRef], Any]]
] = OrderedDict()
_PRECAPTURED_INDICATOR_DURABLE_LOOKUP: Callable[
    [str], tuple[PayloadRef, Callable[[PayloadRef], Any]] | None
] | None = None
_RAW_FETCH_SCHEMA_REF = SchemaRef(name="RawFetch", version="1")

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

#: TDX can retain the exchange's final five-level book for a short, provider-
#: stamped close-processing interval after 15:00.  It is no longer a live book,
#: but it is still an exact same-session observation whose own availability is
#: the provider ``servertime``.  Accept only this narrow interval; later stamps,
#: lunch/pre-open snapshots and next-session guesses remain refused.
_TDX_RETAINED_CLOSE_BOOK_END = _time(hour=15, minute=30)

#: Exact provenance spelling emitted by the live-client TDX item shaper when
#: ``publish_ts`` came from the provider's own quote timestamp.  The non-session
#: retained-book path requires this value at both the unified item and native row;
#: a fetch-time fallback can never date a prior-session book.
_TDX_PROVIDER_QUOTE_TIME_SOURCE = "provider.quote_time"

#: ``revision_id`` transport classifier for an exact provider-timed book that
#: TDX retained during close processing.  The provider timestamp is appended to
#: the prefix, preserving revision identity while letting deterministic
#: consumers distinguish the observation from a genuinely live book without a
#: schema change.  Ordinary in-session books keep ``revision_id=None``.
POST_CLOSE_RETAINED_BOOK_REVISION_PREFIX = "post_close_retained_book@"

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

#: Item keys carrying a NEWS row's own **publication instant** — the announcement's
#: 当时可知时间, i.e. the moment the datum became public. The unified stocks item
#: shaper stamps both (``src/data/live_text_sources.py``). First readable spelling
#: wins; nothing is derived.
#:
#: Deliberately a SEPARATE name from :data:`_ITEM_QUOTE_TIME_KEYS`, which currently
#: spells the same two keys: there they are read as a QUOTE anchor and consumed as a
#: session DATE; here they are read as a PUBLICATION instant. Two rules, two
#: consumers — collapsing them would couple the quote calendar attribution to the
#: news availability rule.
_NEWS_PUBLICATION_KEYS = ("publish_ts", "visible_ts")

#: The item's own PIT certification flag — the ONLY composite provenance statement
#: the vendor makes about its timestamps, and the primary gate on the news route.
#:
#: ``G:\stocks\src\data\temporal_contract.py::timestamp_is_pit_eligible`` (:122-143)
#: sets it False for exactly the cases that must not be trusted: an unparseable /
#: missing publish, visible or first-seen instant; ``time_precision == "unknown"``;
#: a ``time_source`` that starts with ``fetch_fallback``; or a publish/visible
#: instant later than first-seen. That last set is what makes it strictly stronger
#: than the ``time_source`` gate alone — see :func:`_attested_publication`.
_PIT_CERTIFIED_KEY = "pit_certified"

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


def _quote_anchor_date(view: Mapping[str, Any], item: Mapping[str, Any]) -> _date | None:
    """The row's own **venue quote date** — the settled-attribution anchor.

    Two bound sources, in precedence order (both venue-side facts, never the
    pull instant and never ``as_of``):

    1. the **vendor row's** own quote-timestamp key (:data:`_QUOTE_TIME_KEYS`),
       looked up in ``view`` (the vendor's nested ``raw`` row overlaid on the
       item) — these keys ARE the vendor's, so the vendor row rightly wins;
    2. the **unified item's** ``publish_ts`` / ``visible_ts`` — but ONLY when the
       item's own ``time_source`` proves it is a provider time. The stocks item
       shaper falls back to the FETCH instant with ``time_source="fetch_fallback"``
       whenever the vendor row carries no time of its own, and a fetch instant is
       not a quote time.

    **Trust direction (review M2):** the gate AND the item-level keys it unlocks
    are read from ``item`` — the shaper's own attestation — never from the merged
    ``view``. ``view`` is the item overlaid by the UNTRUSTED vendor row (raw
    wins), so reading the gate there would let a vendor row that carries its own
    ``time_source`` key overrule the shaper's honest ``fetch_fallback``
    attestation and unlock the very item-level anchor the gate exists to block —
    a trust inversion in the module's one defensive check.

    ``None`` when neither is readable — the caller then emits **no** settled
    candidate at all.
    """
    for key in _QUOTE_TIME_KEYS:
        anchor = _as_session_date(view.get(key))
        if anchor is not None:
            return anchor
    src = item.get("time_source")
    if isinstance(src, str) and src.strip() and not src.strip().lower().startswith(
            _UNPROVEN_TIME_SOURCES):
        for key in _ITEM_QUOTE_TIME_KEYS:
            anchor = _as_session_date(item.get(key))
            if anchor is not None:
                return anchor
    return None


def _attested_publication(item: Any) -> datetime | None:
    """A news row's OWN publication instant (aware UTC), or ``None`` when unattested.

    **The row's own availability, never a fabrication.** A news/announcement datum
    becomes knowable when it is PUBLISHED, and the unified stocks item carries that
    instant together with the shaper's own provenance statement. This is the
    news-side reading of the CONTROLLER RULING the settled projection implements for
    quotes: *available_at = the data's OWN availability time (bar
    settlement/publication), not pulled_at*.

    **The gate is provenance, NOT ``visible_ts_reason`` (review Important 1).** The
    first cut of this function gated on ``visible_ts_reason``, which does not
    survive contact with the vendor: on the bound news route that value is a
    CONSTANT. ``live_text_sources.py::_visible_ts`` (:331-336) returns
    ``"date_only_eod"`` only for ``source_type in {"announcement",
    "research_report"}``, and ``eastmoney_stock_news`` rows are stamped
    ``"market_news"``, so every row reads ``"exact_publish_time"`` — including the
    dangerous one: ``_publish_or_now`` (:324-328) substitutes ``now()`` whenever the
    vendor's date is missing or unparseable, so a date-less row arrives with a
    FETCH-instant ``publish_ts`` wearing the "exact" label. Under the old gate that
    row was "attested", found future, and SILENTLY EXCLUDED — breaking this module's
    own charter sentence that a row is never dropped on anything but its own
    attestation. ``visible_ts_reason`` is a *normalization* label (which rule
    produced ``visible_ts``), never a provenance claim, so it is no longer read here
    at all — and ``date_only_eod`` is a legitimately conservative instant that must
    keep passing.

    Two provenance gates, BOTH required, both read from the ITEM — the shaper's own
    statement — exactly as :func:`_quote_anchor_date` reads ``time_source`` from the
    item and never from a vendor-overlaid view:

    1. ``pit_certified is True`` (:data:`_PIT_CERTIFIED_KEY`) — the vendor's own
       composite certification. It is strictly the stronger of the two: it also
       fails a row whose raw date was present but UNPARSEABLE, where
       ``_publish_or_now`` substituted ``now()`` while ``time_source`` still reads
       ``"eastmoney.stock_search.date"`` (the residual hole a ``time_source``-only
       gate leaves open) — because ``infer_time_precision`` returns ``"unknown"``
       for such a value. Identity check, not truthiness: a ``1`` or ``"yes"`` is
       not a certification.
    2. ``time_source`` not in :data:`_UNPROVEN_TIME_SOURCES` — the same gate the
       quote anchor uses, kept as an independent second lock so the rule still
       holds if a future shaper emits ``pit_certified`` more liberally.

    Either gate failing yields ``None``: the row proves nothing about its own
    publication, so the caller keeps the envelope's ``pulled_at``, forwards the row
    UNFILTERED, and the PitGuard decides — unchanged, loud, and visible.
    """
    if not isinstance(item, Mapping):
        return None
    if item.get(_PIT_CERTIFIED_KEY) is not True:
        return None
    source = str(item.get("time_source") or "").strip().lower()
    if not source or source.startswith(_UNPROVEN_TIME_SOURCES):
        return None
    for key in _NEWS_PUBLICATION_KEYS:
        stamped = _online_availability(item.get(key))
        if stamped is not None:
            return stamped
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
    """The row's own settled close — a **strictly positive** finite number, or ``None``.

    ``> 0`` is not a tidiness rule, it is the admissibility rule (review I1). The
    real stocks handler parses every vendor field through a ``_num`` helper that
    **returns 0.0 on any parse failure** (``G:\\stocks\\src\\data\\live_text_sources.py``),
    and a suspended / not-yet-listed instrument quotes zeros outright — so a
    ``0.0`` under ``prev_close`` is a *sentinel for "unreadable"*, not an
    observation, and no A-share instrument ever settles at or below zero. Before
    the settled projection existed such a row could never be admitted (a live
    stamp is always after the frozen boundary); with the projection live, a
    non-positive close would complete a REAL read carrying a price of zero.

    A non-finite or non-positive value under one key falls through to the next
    bound alias of the same 昨收 datum (they are spellings of one number, and an
    unreadable spelling is simply not the readable one); when none is readable
    the caller emits **no** settled candidate at all and the live candidate's
    honest PitGuard refusal stands. Nothing is substituted, clamped or guessed.
    """
    for key in _SETTLED_CLOSE_KEYS:
        v = view.get(key)
        if isinstance(v, bool) or v is None:
            continue
        if isinstance(v, (int, float)):
            f = float(v)
            if math.isfinite(f) and f > 0.0:
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


def _latest_completed_session(
    calendar: TradingCalendar, as_of: datetime
) -> _date | None:
    """Return the exact latest A-share session completed by ``as_of``.

    The answer is derived only from the frozen cutoff, the reviewed 15:00 close
    rule and committed calendar bytes.  Before/during a trading session the
    current daily bar is not complete, so the prior session is expected; at or
    after close the current session is expected.  On a non-session day the most
    recent prior session remains current.  ``None`` means the committed material
    cannot prove an answer; callers must fail closed rather than extrapolate.
    """
    if as_of.tzinfo is None or as_of.utcoffset() is None:
        return None
    coverage = calendar.coverage
    if coverage is None:
        return None
    lo, hi = coverage
    local = as_of.astimezone(_EXCHANGE_TZ)
    local_day = local.date()
    if local_day < lo or local_day > hi:
        return None
    if (
        calendar.is_session(local_day)
        and local.time() >= _SETTLED_CLOSE_LOCAL_TIME
    ):
        return local_day
    day = local_day - timedelta(days=1)
    while day >= lo:
        if calendar.is_session(day):
            return day
        day -= timedelta(days=1)
    return None


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


def _ohlcv_bar_instant(view: Mapping[str, Any]) -> datetime:
    """Return a TDX daily bar's own A-share close instant in aware UTC."""
    raw = view.get("datetime")
    if raw is None:
        raw = view.get("date")
    parsed: datetime | _date | None = None
    if isinstance(raw, datetime):
        parsed = raw
    elif isinstance(raw, _date):
        parsed = raw
    elif isinstance(raw, str):
        value = raw.strip().replace("/", "-")
        if len(value) == 8 and value.isdigit():
            value = f"{value[:4]}-{value[4:6]}-{value[6:]}"
        try:
            parsed = datetime.fromisoformat(value.replace(" ", "T"))
        except ValueError:
            try:
                parsed = _date.fromisoformat(value)
            except ValueError:
                parsed = None
    if parsed is None:
        raise SourceBrokenError(
            "tdx_kline row has no parseable row-owned datetime/date; refusing "
            "to stamp OHLCV from the envelope pull time")
    if isinstance(parsed, datetime):
        local_day = (
            parsed.replace(tzinfo=_EXCHANGE_TZ)
            if parsed.tzinfo is None else parsed.astimezone(_EXCHANGE_TZ)
        ).date()
    else:
        local_day = parsed
    return _settled_instant(local_day)


def _ohlcv_number(view: Mapping[str, Any], key: str) -> float:
    value = view.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SourceBrokenError(
            f"tdx_kline row field {key!r} must be a numeric vendor value")
    number = float(value)
    if not math.isfinite(number) or number <= 0.0:
        raise SourceBrokenError(
            f"tdx_kline row field {key!r} must be finite and positive")
    return number


def _ohlcv_volume(view: Mapping[str, Any]) -> int:
    """Map the measured TDX ``vol`` float only when it is an exact count."""
    value = view.get("vol")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SourceBrokenError("tdx_kline row field 'vol' must be numeric")
    number = float(value)
    if not math.isfinite(number) or number < 0.0 or not number.is_integer():
        raise SourceBrokenError(
            "tdx_kline row field 'vol' must be finite, non-negative and integral")
    return int(number)


def _six_digits(value: Any) -> str:
    digits = "".join(ch for ch in str(value or "") if ch.isdigit())
    return digits[-6:] if len(digits) >= 6 else ""


def _require_verified_snapshot_identity(
    request: DataRequest,
    item: Mapping[str, Any],
    view: Mapping[str, Any],
) -> None:
    """Prove a quote row belongs to the one requested instrument.

    The settled projection reuses only ``last_close``/``name`` from the vendor
    row and reattaches the request's typed ``Symbol``.  That is safe only after
    every explicit row identity agrees with the request; otherwise a stale
    cache row for another security could be relabelled as the requested one.
    """
    symbol = _first_symbol(request.params)
    requested_code = _six_digits((symbol or {}).get("code"))
    exchange = str((symbol or {}).get("exchange") or "").upper()
    if symbol is None or not requested_code or exchange not in {"SH", "SZ"}:
        raise RoutingConfigurationError(
            "verified_snapshot request has no valid per-instrument identity")

    explicit_codes: list[str] = []
    for key in ("code", "symbol", "stock_code"):
        if key not in view:
            continue
        code = _six_digits(view.get(key))
        if not code:
            raise SourceBrokenError(
                f"verified_snapshot row {key!r} is not a readable six-digit code")
        explicit_codes.append(code)
    if "stock_codes" in item:
        stock_codes = item.get("stock_codes")
        if not isinstance(stock_codes, (list, tuple)) or not stock_codes:
            raise SourceBrokenError(
                "verified_snapshot item stock_codes must be a non-empty sequence")
        for value in stock_codes:
            code = _six_digits(value)
            if not code:
                raise SourceBrokenError(
                    "verified_snapshot item stock_codes contains an unreadable code")
            explicit_codes.append(code)
    if not explicit_codes or set(explicit_codes) != {requested_code}:
        raise SourceBrokenError(
            "verified_snapshot row identity does not exactly match requested "
            f"symbol {exchange}{requested_code}")

    prefixes: set[str] = set()
    for key in ("symbol", "stock_code"):
        raw = view.get(key)
        if not isinstance(raw, str):
            continue
        normalized = raw.strip().upper()
        if normalized.startswith("SH"):
            prefixes.add("SH")
        elif normalized.startswith("SZ"):
            prefixes.add("SZ")
    if prefixes and prefixes != {exchange}:
        raise SourceBrokenError(
            "verified_snapshot row exchange prefix does not exactly match "
            f"requested exchange {exchange}")
    if "market" in view:
        market = view.get("market")
        expected_market = {"SZ": 0, "SH": 1}[exchange]
        if type(market) is not int or market != expected_market:
            raise SourceBrokenError(
                "verified_snapshot row market does not exactly match requested "
                f"exchange {exchange}")


def _ohlcv_candidate(request: DataRequest, item: Any) -> RawRowCandidate:
    """Build one identity-checked OHLCV candidate from the native TDX shape."""
    if not isinstance(item, Mapping):
        raise SourceBrokenError(
            f"tdx_kline item must be a mapping, got {type(item).__name__}")
    view = _vendor_row_view(item)
    freq = str(view.get("freq") or "").strip().lower()
    if freq not in {"day", "daily", "1d", "d"}:
        raise SourceBrokenError(
            f"tdx_kline row is not an attested daily bar (freq={freq!r})")
    symbol = _first_symbol(request.params)
    requested_code = _six_digits((symbol or {}).get("code"))
    row_codes = {
        code for code in (
            _six_digits(view.get("code")),
            _six_digits(view.get("stock_code")),
        ) if code
    }
    if symbol is None or not requested_code:
        raise RoutingConfigurationError(
            "ohlcv request has no valid per-instrument symbol identity")
    if not row_codes or row_codes != {requested_code}:
        raise SourceBrokenError(
            f"tdx_kline row identity {sorted(row_codes)!r} does not exactly match "
            f"requested symbol {requested_code!r}")

    open_ = _ohlcv_number(view, "open")
    high = _ohlcv_number(view, "high")
    low = _ohlcv_number(view, "low")
    close = _ohlcv_number(view, "close")
    if high < max(open_, low, close) or low > min(open_, high, close):
        raise SourceBrokenError("tdx_kline row has internally inconsistent OHLC bounds")
    instant = _ohlcv_bar_instant(view)
    return RawRowCandidate.build(
        raw_payload={
            "symbol": symbol,
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": _ohlcv_volume(view),
        },
        effective_at=instant,
        available_at=instant,
        ingested_at=request.as_of,
    )


def _finite_book_number(
    view: Mapping[str, Any], key: str, *, positive: bool
) -> float:
    """Read one measured TDX book scalar without coercion or imputation."""
    value = view.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SourceBrokenError(
            f"tdx_orderbook row field {key!r} must be a numeric vendor value")
    number = float(value)
    if not math.isfinite(number) or (number <= 0.0 if positive else number < 0.0):
        qualifier = "positive" if positive else "non-negative"
        raise SourceBrokenError(
            f"tdx_orderbook row field {key!r} must be finite and {qualifier}")
    return number


def _tdx_provider_local_datetime(value: Any, *, field: str) -> datetime:
    """Parse one provider-owned TDX timestamp into Asia/Shanghai.

    TDX currently emits naive local ISO timestamps.  An explicit offset is also
    accepted and normalized, but a date-only/unparseable value is not enough to
    establish the retained book's exact availability instant.
    """
    if not isinstance(value, str) or not value.strip():
        raise SourceBrokenError(
            f"tdx_realtime_quote stale book has no provider {field}")
    try:
        parsed = datetime.fromisoformat(value.strip().replace(" ", "T"))
    except ValueError as exc:
        raise SourceBrokenError(
            f"tdx_realtime_quote stale book {field} is not an ISO timestamp"
        ) from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=_EXCHANGE_TZ)
    else:
        parsed = parsed.astimezone(_EXCHANGE_TZ)
    if parsed.timetz().replace(tzinfo=None) == _time(0, 0):
        raise SourceBrokenError(
            f"tdx_realtime_quote stale book {field} is date-only, not an exact "
            "provider quote timestamp"
        )
    return parsed


def _immediately_previous_committed_session(
    calendar: TradingCalendar, capture_day: _date
) -> _date | None:
    """Return the last committed session before one covered non-session day.

    A date outside the immutable material's covered span cannot be extrapolated:
    returning ``None`` is the fail-closed answer rather than guessing a holiday or
    next-year session schedule.
    """
    coverage = calendar.coverage
    if coverage is None:
        return None
    lo, hi = coverage
    if capture_day < lo or capture_day > hi:
        return None
    cursor = capture_day - timedelta(days=1)
    while cursor >= lo:
        if calendar.is_session(cursor):
            return cursor
        cursor -= timedelta(days=1)
    return None


def _stale_indicator_book_instant(
    request: DataRequest,
    item: Mapping[str, Any],
    view: Mapping[str, Any],
    calendar: TradingCalendar,
    ingested_at: datetime | None,
) -> datetime:
    """Prove one non-session TDX stale row is the prior session's retained book.

    The proof deliberately joins both provenance layers in the real facade shape:
    the unified item certifies ``publish_ts`` as ``provider.quote_time`` and the
    nested native row supplies matching ``quote_date`` / ``quote_time`` /
    ``servertime``.  Neither the envelope pull date nor the current wall clock is
    allowed to manufacture the book's Friday timestamp.
    """
    if item.get(_PIT_CERTIFIED_KEY) is not True:
        raise SourceBrokenError(
            "tdx_realtime_quote stale book is not PIT-certified by the item shaper"
        )
    if item.get("time_source") != _TDX_PROVIDER_QUOTE_TIME_SOURCE:
        raise SourceBrokenError(
            "tdx_realtime_quote stale book item time_source is not exact "
            "provider.quote_time"
        )
    if item.get("source_timezone") != str(_EXCHANGE_TZ):
        raise SourceBrokenError(
            "tdx_realtime_quote stale book does not attest Asia/Shanghai as its "
            "source timezone"
        )
    raw = item.get("raw")
    if not isinstance(raw, Mapping):
        raise SourceBrokenError(
            "tdx_realtime_quote stale book has no native provider row")
    if raw.get("time_source") != _TDX_PROVIDER_QUOTE_TIME_SOURCE:
        raise SourceBrokenError(
            "tdx_realtime_quote stale native row time_source is not exact "
            "provider.quote_time"
        )
    if raw.get("is_stale") is not True:
        raise SourceBrokenError(
            "tdx_realtime_quote status=stale row does not carry is_stale=true")

    published = _tdx_provider_local_datetime(
        item.get("publish_ts"), field="item publish_ts")
    quoted = _tdx_provider_local_datetime(
        raw.get("quote_time"), field="native quote_time")
    if published != quoted:
        raise SourceBrokenError(
            "tdx_realtime_quote stale book item/native provider timestamps disagree"
        )
    quote_day = _as_session_date(raw.get("quote_date"))
    if quote_day is None or quote_day != quoted.date():
        raise SourceBrokenError(
            "tdx_realtime_quote stale book quote_date does not match provider "
            "quote_time"
        )

    raw_server_time = view.get("servertime")
    if not isinstance(raw_server_time, str) or not raw_server_time.strip():
        raise SourceBrokenError(
            "tdx_realtime_quote stale book has no provider-owned servertime")
    try:
        server_time = _time.fromisoformat(raw_server_time.strip())
    except ValueError as exc:
        raise SourceBrokenError(
            "tdx_realtime_quote stale book servertime is not an ISO local time"
        ) from exc
    if server_time.tzinfo is not None:
        raise SourceBrokenError(
            "tdx_realtime_quote stale book servertime must be a provider local "
            "wall time"
        )
    if quoted.timetz().replace(tzinfo=None) != server_time:
        raise SourceBrokenError(
            "tdx_realtime_quote stale book quote_time and servertime disagree"
        )
    if not (_SETTLED_CLOSE_LOCAL_TIME < server_time <= _TDX_RETAINED_CLOSE_BOOK_END):
        raise SourceBrokenError(
            f"tdx_realtime_quote stale book servertime {raw_server_time!r} is "
            "outside the retained-close-book window"
        )

    if ingested_at is None:
        raise SourceBrokenError(
            "tdx_realtime_quote stale envelope has no capture-completion timestamp"
        )
    if ingested_at != request.as_of:
        raise SourceBrokenError(
            "tdx_realtime_quote stale book request.as_of is not the actual "
            "capture-completion instant"
        )
    local_capture = request.as_of.astimezone(_EXCHANGE_TZ)
    capture_day = local_capture.date()
    if calendar.is_session(capture_day):
        raise SourceBrokenError(
            "tdx_realtime_quote stale retained book is allowed only on a "
            "non-session capture date"
        )
    previous = _immediately_previous_committed_session(calendar, capture_day)
    if previous is None:
        raise SourceBrokenError(
            "tdx_realtime_quote stale retained book capture date is outside the "
            "committed calendar coverage"
        )
    if quote_day != previous:
        raise SourceBrokenError(
            f"tdx_realtime_quote stale book date {quote_day.isoformat()} is not "
            "the immediately preceding committed trading session "
            f"{previous.isoformat()}"
        )
    if quoted > local_capture:
        raise SourceBrokenError(
            "tdx_realtime_quote stale book provider timestamp postdates capture"
        )
    return quoted


def _indicator_candidates(
    request: DataRequest,
    items: Any,
    calendar: TradingCalendar | None,
    ingested_at: datetime | None,
    *,
    stale_envelope: bool = False,
) -> tuple[RawRowCandidate, ...]:
    """Project one exact-code, row-time-bound TDX book into two indicators.

    The projection is deliberately narrow: no raw book row is exposed to a
    worker and no missing level is treated as zero.  A complete five-level book
    yields the L1 spread in basis points and the five-level bid/ask volume
    imbalance.  Anything else fails loudly, so a partial/foreign book can only
    degrade the microstructure seat, never look like measured evidence.
    """
    rows = tuple(items or ())
    if len(rows) != 1 or not isinstance(rows[0], Mapping):
        raise SourceBrokenError(
            "tdx_orderbook must return exactly one mapping row for one instrument")
    view = _vendor_row_view(rows[0])
    symbol = _first_symbol(request.params)
    requested_code = _six_digits((symbol or {}).get("code"))
    row_codes = {
        code for code in (
            _six_digits(view.get("code")),
            _six_digits(view.get("stock_code")),
        ) if code
    }
    if symbol is None or not requested_code:
        raise RoutingConfigurationError(
            "indicators request has no valid per-instrument symbol identity")
    if not row_codes or row_codes != {requested_code}:
        raise SourceBrokenError(
            f"tdx_realtime_quote row identity {sorted(row_codes)!r} does not exactly "
            f"match requested symbol {requested_code!r}")
    exchange = str((symbol or {}).get("exchange") or "").upper()
    expected_market = {"SZ": 0, "SH": 1}.get(exchange)
    row_market = view.get("market")
    row_stock_code = str(view.get("stock_code") or "").upper()
    if expected_market is None or isinstance(row_market, bool) or row_market != expected_market:
        raise SourceBrokenError(
            f"tdx_realtime_quote market {row_market!r} does not match requested "
            f"exchange {exchange!r}")
    if row_stock_code and not row_stock_code.startswith(exchange):
        raise SourceBrokenError(
            f"tdx_realtime_quote stock_code {row_stock_code!r} does not match "
            f"requested exchange {exchange!r}")

    # ``tdx_orderbook`` has no provider-owned row time and therefore cannot
    # prove PIT. ``tdx_realtime_quote`` carries ``servertime`` alongside the
    # same five levels.  An ordinary envelope keeps the reviewed same-session
    # 10-second/retained-close rules below.  A stale envelope takes a separate,
    # stricter non-session proof that binds the full provider Friday timestamp;
    # the two regimes never infer one another.
    if calendar is None:
        raise RoutingConfigurationError(
            "timed TDX book projection requires the committed trading calendar")
    if stale_envelope:
        local_instant = _stale_indicator_book_instant(
            request, rows[0], view, calendar, ingested_at)
        retained_close_book = True
    else:
        # This is the pre-existing same-session path.  Keep its time source and
        # ten-second live rule intact; a non-session stale row cannot reach it.
        # The native row may carry stronger, explicit timing facts.  They must
        # agree with the non-stale envelope and admitted session before the
        # servertime is combined with request.as_of's date; otherwise that
        # combination would silently relabel a Friday row as today's live book.
        local_cutoff = request.as_of.astimezone(_EXCHANGE_TZ)
        session_day = local_cutoff.date()
        native = rows[0].get("raw")
        native = native if isinstance(native, Mapping) else rows[0]
        identity_layers = (("item", rows[0]),)
        if native is not rows[0]:
            identity_layers += (("native", native),)
        for layer_name, layer in identity_layers:
            if "is_stale" not in layer:
                continue
            layer_stale = layer.get("is_stale")
            if type(layer_stale) is not bool:
                raise SourceBrokenError(
                    f"tdx_realtime_quote {layer_name} is_stale must be a boolean "
                    "when present")
            if layer_stale is True:
                raise SourceBrokenError(
                    "tdx_realtime_quote non-stale envelope contradicts "
                    f"{layer_name} is_stale=true")
        for field in ("quote_date", "quote_time"):
            if field not in native:
                continue
            provider_day = _as_session_date(native.get(field))
            if provider_day is None:
                raise SourceBrokenError(
                    f"tdx_realtime_quote native {field} is not a readable "
                    "provider session date")
            if provider_day != session_day:
                raise SourceBrokenError(
                    f"tdx_realtime_quote native {field} date "
                    f"{provider_day.isoformat()} does not match admitted "
                    f"session {session_day.isoformat()}")
        if not calendar.is_session(session_day):
            raise SourceBrokenError(
                f"tdx_realtime_quote book date {session_day.isoformat()} is not a "
                "committed exchange session")
        raw_server_time = view.get("servertime")
        if not isinstance(raw_server_time, str) or not raw_server_time.strip():
            raise SourceBrokenError(
                "tdx_realtime_quote book has no provider-owned servertime")
        try:
            server_time = _time.fromisoformat(raw_server_time.strip())
        except ValueError as exc:
            raise SourceBrokenError(
                "tdx_realtime_quote servertime is not an ISO local time") from exc
        if server_time.tzinfo is not None:
            raise SourceBrokenError(
                "tdx_realtime_quote servertime must be a provider local wall time")
        if "quote_time" in native:
            provider_quote_time = _tdx_provider_local_datetime(
                native.get("quote_time"), field="native quote_time")
            if provider_quote_time.timetz().replace(tzinfo=None) != server_time:
                raise SourceBrokenError(
                    "tdx_realtime_quote native quote_time and servertime disagree")
        local_instant = datetime.combine(
            session_day, server_time, tzinfo=_EXCHANGE_TZ)
        local_t = local_instant.timetz().replace(tzinfo=None)
        if local_instant > local_cutoff:
            raise SourceBrokenError(
                "tdx_realtime_quote servertime postdates the frozen evidence cutoff")
        in_session = (
            _time(9, 30) <= local_t <= _time(11, 30)
            or _time(13, 0) <= local_t <= _time(15, 0)
        )
        retained_close_book = (
            _SETTLED_CLOSE_LOCAL_TIME < local_t <= _TDX_RETAINED_CLOSE_BOOK_END
            and local_cutoff.timetz().replace(tzinfo=None) > _SETTLED_CLOSE_LOCAL_TIME
        )
        if not in_session and not retained_close_book:
            raise SourceBrokenError(
                f"tdx_realtime_quote servertime {raw_server_time!r} is outside the "
                "reviewed live/retained-close-book windows")
        if in_session and (local_cutoff - local_instant).total_seconds() > 10.0:
            raise SourceBrokenError(
                "tdx_realtime_quote five-level book is more than 10 seconds stale")
        if ingested_at is None:
            raise SourceBrokenError(
                "tdx_realtime_quote envelope has no capture-completion timestamp")
        local_ingested = ingested_at.astimezone(_EXCHANGE_TZ)
        if retained_close_book and local_ingested.date() != session_day:
            raise SourceBrokenError(
                "tdx_realtime_quote retained close book was not captured on the "
                "admitted exchange-session date")
        if retained_close_book and local_ingested < local_instant:
            raise SourceBrokenError(
                "tdx_realtime_quote retained close book capture predates provider "
                "servertime")
    available_at = local_instant.astimezone(timezone.utc)

    bids = tuple(
        _finite_book_number(view, f"bid{i}", positive=True)
        for i in range(1, 6)
    )
    asks = tuple(
        _finite_book_number(view, f"ask{i}", positive=True)
        for i in range(1, 6)
    )
    bid_volumes = tuple(
        _finite_book_number(view, f"bid_vol{i}", positive=False)
        for i in range(1, 6)
    )
    ask_volumes = tuple(
        _finite_book_number(view, f"ask_vol{i}", positive=False)
        for i in range(1, 6)
    )
    if any(left < right for left, right in zip(bids, bids[1:])):
        raise SourceBrokenError(
            "tdx_realtime_quote bid prices are not monotonically descending")
    if any(left > right for left, right in zip(asks, asks[1:])):
        raise SourceBrokenError(
            "tdx_realtime_quote ask prices are not monotonically ascending")
    if asks[0] < bids[0]:
        raise SourceBrokenError(
            "tdx_orderbook best ask is below best bid; crossed book refused")
    mid = (asks[0] + bids[0]) / 2.0
    spread_bp = (asks[0] - bids[0]) / mid * 10_000.0
    bid_total = sum(bid_volumes)
    ask_total = sum(ask_volumes)
    depth_total = bid_total + ask_total
    if depth_total <= 0.0:
        raise SourceBrokenError(
            "tdx_orderbook has zero total displayed depth; imbalance is undefined")
    imbalance = (bid_total - ask_total) / depth_total

    common = {
        "effective_at": available_at,
        "available_at": available_at,
        "ingested_at": ingested_at,
        "revision_id": (
            POST_CLOSE_RETAINED_BOOK_REVISION_PREFIX + local_instant.isoformat()
            if retained_close_book else None
        ),
    }
    return tuple(
        RawRowCandidate.build(
            raw_payload={
                "symbol": symbol,
                "indicator_id": indicator_id,
                "value": value,
            },
            **common,
        )
        for indicator_id, value in (
            ("l1_spread_bp", spread_bp),
            ("bid_ask_imbalance", imbalance),
        )
    )


def _validate_live_envelope(
    envelope: Any,
    *,
    binding: ReviewedLiveBinding,
    canonical: str,
    method_id: str,
) -> Mapping[str, Any]:
    """Apply the adapter's one honest envelope classification.

    Pre-capture and ordinary reads share this exact classifier so a run cannot
    admit an envelope during capture that the normal adapter would have refused.
    The run-injected binding is checked again here: resolver validation before
    the probe and envelope validation after it are separate tripwires.
    """
    if binding.method_id != method_id or binding.source_id != canonical:
        raise RoutingConfigurationError(
            "live envelope validation binding does not match the exact "
            f"method/source request ({method_id!r}, {canonical!r})"
        )
    if (
        binding.requires_exact_resolver_match is not True
        or binding.requires_source_echo is not True
        or binding.requires_strict_provenance is not True
        or binding.requires_network_used is not True
        or binding.requires_closed_circuit is not True
        or binding.allows_provider_fallback is not False
    ):
        raise RoutingConfigurationError(
            "live envelope validation received a binding with unsafe semantics"
        )
    if not isinstance(envelope, Mapping):
        raise SourceBrokenError(
            f"live probe for source={canonical} method={method_id} returned a "
            f"non-mapping envelope {type(envelope).__name__}; the result is void"
        )

    def _broken(detail: str) -> None:
        raise SourceBrokenError(
            f"live source {canonical!r} returned an invalid envelope "
            f"(method={method_id}): {detail}"
        )

    # Status is a closed, exact state machine.  In particular, neither a legacy
    # ``planned``/``error`` nor a new/unknown spelling can widen the router's
    # narrow fallback taxonomy.  ``stale`` is NOT a general data status: the two
    # method-scoped exceptions are (a) a verified snapshot reducible to its own
    # prior-session settlement and (b) an indicators row provable as the
    # immediately-prior session's retained close book.  Both require the exact
    # ``stale_data`` attestation and are reduced later; no stale live value is
    # admitted merely because its envelope passed this structural gate.
    status = envelope.get("status")
    data_statuses = frozenset({"ok", "empty", "not_available"})
    transient_statuses = frozenset({"provider_busy", "rate_limited"})
    stale_settled_snapshot = (
        status == "stale" and method_id == _VERIFIED_SNAPSHOT_METHOD_ID
    )
    stale_retained_indicators = (
        status == "stale" and method_id == _INDICATORS_METHOD_ID
    )
    stale_projectable = stale_settled_snapshot or stale_retained_indicators
    if (
        type(status) is not str
        or (
            status not in data_statuses | transient_statuses
            and not stale_projectable
        )
    ):
        _broken(f"unsupported exact status {status!r}")

    items = envelope.get("items")
    if not isinstance(items, list):
        _broken("items must be a list")
    if status in {"empty", "not_available"} and items:
        _broken(f"status={status} requires an empty items list")
    if stale_projectable:
        if envelope.get("error_kind") != "stale_data":
            _broken(
                f"status=stale for {method_id} requires exact "
                "error_kind='stale_data'"
            )
        if not items:
            _broken(
                f"status=stale for {method_id} requires a non-empty "
                "items list"
            )
    if envelope.get("ok") is not True:
        _broken(f"status={status} requires ok to be the boolean true")
    if "schema_version" not in envelope:
        _broken("schema_version is missing")
    if envelope.get("schema_version") != binding.envelope_schema_version:
        _broken(
            f"schema_version must exactly equal "
            f"{binding.envelope_schema_version!r}"
        )

    # Both production and injected probes are strict.  ``source_id`` is accepted
    # only as an additional echo and can never contradict the required ``source``.
    if "source" not in envelope:
        _broken("source echo is missing")
    source_echo = envelope.get("source")
    if not isinstance(source_echo, str) or source_echo != canonical:
        _broken(
            f"source echo {source_echo!r} does not exactly match "
            f"{canonical!r}"
        )
    if "source_id" in envelope:
        source_id_echo = envelope.get("source_id")
        if not isinstance(source_id_echo, str) or source_id_echo != canonical:
            _broken(
                f"source_id echo {source_id_echo!r} does not exactly match "
                f"{canonical!r}"
            )

    expected_provider = binding.provider_id
    for field in ("provider", "actual_provider"):
        if field not in envelope:
            _broken(f"{field} is missing")
        value = envelope.get(field)
        if not isinstance(value, str) or value != expected_provider:
            _broken(
                f"{field} {value!r} does not match reviewed provider "
                f"{expected_provider!r}"
            )

    if "fallback_level" not in envelope:
        _broken("fallback_level is missing")
    fallback_level = envelope.get("fallback_level")
    # ``bool`` is an ``int`` subclass and strings are coercible, but neither
    # is an honest integer-zero attestation.
    if type(fallback_level) is not int or fallback_level != 0:
        _broken(
            f"fallback_level must be the integer 0, got "
            f"{fallback_level!r}"
        )

    if "attempted_sources" not in envelope:
        _broken("attempted_sources is missing")
    attempted_sources = envelope.get("attempted_sources")
    if not isinstance(attempted_sources, list) or attempted_sources:
        _broken(
            "attempted_sources must be an empty list; internal provider "
            "fallback is not attested"
        )

    if "cache_hit" not in envelope:
        _broken("cache_hit is missing")
    if envelope.get("cache_hit") is not False:
        _broken("cache_hit must be the boolean false")
    if "network_used" not in envelope:
        _broken("network_used is missing")
    if envelope.get("network_used") is not True:
        _broken("network_used must be the boolean true")
    if "circuit_state" not in envelope:
        _broken("circuit_state is missing")
    if envelope.get("circuit_state") != "closed":
        _broken("circuit_state must exactly equal 'closed'")

    if status in transient_statuses:
        raise RateLimitError(
            f"live source {canonical!r} is temporarily unavailable "
            f"(status={status}; method={method_id})"
        )
    return envelope


def build_precaptured_indicator_fetch(
    request: DataRequest,
    *,
    envelope: Mapping[str, Any],
    canonical_source: str,
    source_ref: ContentRef,
    capability_ref: Any,
    calendar: TradingCalendar,
    captured_at: datetime,
    binding_snapshot: ReviewedLiveBindingSnapshot | None = None,
) -> RawFetch:
    """Seal one provider-timed book captured before the run context is exposed.

    ``captured_at`` is the authoritative completion instant used to freeze the
    run's ``DataContext``.  The row-owned ``servertime`` remains the evidence
    availability; the completion instant is only ingestion/audit provenance.
    """
    if request.method_spec_ref.id != _INDICATORS_METHOD_ID:
        raise RoutingConfigurationError(
            "pre-captured indicator fetch requires the reviewed indicators method")
    if captured_at.tzinfo is None or captured_at.utcoffset() is None:
        raise RoutingConfigurationError(
            "pre-captured indicator fetch needs an aware completion instant")
    bindings = (
        reviewed_live_binding_snapshot()
        if binding_snapshot is None
        else binding_snapshot
    )
    verify_code_approved_live_binding_snapshot(bindings)
    binding = bindings.binding_for_method(_INDICATORS_METHOD_ID)
    if request.method_spec_ref.content_digest != binding.method_spec_digest:
        raise RoutingConfigurationError(
            "pre-captured indicator request method spec is not the approved binding"
        )
    checked = _validate_live_envelope(
        envelope, binding=binding, canonical=canonical_source,
        method_id=_INDICATORS_METHOD_ID,
    )
    candidates = _indicator_candidates(
        request,
        checked.get("items") or (),
        calendar,
        captured_at,
        stale_envelope=checked.get("status") == "stale",
    )
    return build_raw_fetch(
        request_digest=request.request_digest,
        source_ref=source_ref,
        capability_ref=capability_ref,
        candidates=candidates,
        subsource=canonical_source,
        fetched_at=captured_at,
        provider_request_id=None,
    )


def register_precaptured_indicator_fetch(
    *,
    request_digest: str,
    raw_fetch_ref: PayloadRef,
    loader: Callable[[PayloadRef], Any],
) -> None:
    """Bind a deep request to its already-persisted exact ``RawFetch``.

    The registry retains only a typed locator and loader.  Registration proves
    the payload exists and matches the request before the ContextSnapshot can be
    published.  A later adapter read loads the persisted bytes again and refuses
    any drift; it never performs a second vendor probe.
    """
    raw = loader(raw_fetch_ref)
    if not isinstance(raw, RawFetch):
        raise SourceBrokenError(
            "pre-captured indicator payload does not resolve RawFetch@1")
    if raw.request_digest != request_digest:
        raise SourceBrokenError(
            "pre-captured indicator RawFetch names a different request digest")
    if content_digest(raw) != raw_fetch_ref.content_digest:
        raise SourceBrokenError(
            "pre-captured indicator payload ref does not bind the RawFetch content")
    with _PRECAPTURED_INDICATOR_LOCK:
        prior = _PRECAPTURED_INDICATOR_FETCHES.get(request_digest)
        if prior is not None and prior[0].content_digest != raw_fetch_ref.content_digest:
            raise SourceBrokenError(
                "one indicator request digest cannot be rebound to different capture bytes")
        _PRECAPTURED_INDICATOR_FETCHES[request_digest] = (raw_fetch_ref, loader)
        _PRECAPTURED_INDICATOR_FETCHES.move_to_end(request_digest)
        while len(_PRECAPTURED_INDICATOR_FETCHES) > _PRECAPTURED_INDICATOR_MAX:
            _PRECAPTURED_INDICATOR_FETCHES.popitem(last=False)


def bind_precaptured_indicator_store_lookup(
    lookup: Callable[
        [str], tuple[PayloadRef, Callable[[PayloadRef], Any]] | None
    ],
) -> None:
    """Install the process-start recovery index over persisted RawFetch@1 rows.

    The in-memory LRU is only a latency aid.  A restarted production process
    rebinds this resolver from its durable ``RuntimeStores`` and can rebuild the
    request-digest -> typed-ref association on demand before any adapter read.
    """
    if not callable(lookup):
        raise TypeError("pre-captured indicator store lookup must be callable")
    global _PRECAPTURED_INDICATOR_DURABLE_LOOKUP
    with _PRECAPTURED_INDICATOR_LOCK:
        # A new production binding represents a process/store recovery boundary.
        # Drop latency-only loaders so no prior RuntimeStores instance can leak
        # into the newly bound process view; durable rows are rediscovered below.
        _PRECAPTURED_INDICATOR_FETCHES.clear()
        _PRECAPTURED_INDICATOR_DURABLE_LOOKUP = lookup


def _load_precaptured_indicator_fetch(request: DataRequest) -> RawFetch:
    with _PRECAPTURED_INDICATOR_LOCK:
        bound = _PRECAPTURED_INDICATOR_FETCHES.get(request.request_digest)
        if bound is not None:
            _PRECAPTURED_INDICATOR_FETCHES.move_to_end(request.request_digest)
        durable_lookup = _PRECAPTURED_INDICATOR_DURABLE_LOOKUP
    if bound is None and durable_lookup is not None:
        bound = durable_lookup(request.request_digest)
        if bound is not None:
            raw_fetch_ref, loader = bound
            register_precaptured_indicator_fetch(
                request_digest=request.request_digest,
                raw_fetch_ref=raw_fetch_ref,
                loader=loader,
            )
            with _PRECAPTURED_INDICATOR_LOCK:
                bound = _PRECAPTURED_INDICATOR_FETCHES.get(
                    request.request_digest)
    if bound is None:
        raise SourceBrokenError(
            "deep indicator evidence has no persisted pre-capture binding; "
            "refusing a post-freeze live probe")
    raw_fetch_ref, loader = bound
    raw = loader(raw_fetch_ref)
    if not isinstance(raw, RawFetch):
        raise SourceBrokenError(
            "persisted deep indicator evidence no longer resolves RawFetch@1")
    if raw.request_digest != request.request_digest:
        raise SourceBrokenError(
            "persisted deep indicator evidence names a different request")
    if content_digest(raw) != raw_fetch_ref.content_digest:
        raise SourceBrokenError(
            "persisted deep indicator evidence digest drifted after capture")
    return raw


def _probe_args(method_id: str, params: Mapping[str, Any]) -> tuple[str, str, int]:
    """Derive ``(code, date, limit)`` for a facade probe from the validated params.

    ``news`` / ``verified_snapshot`` carry the requested instrument as
    ``symbols[0]``; ``ohlcv`` carries it as ``symbol`` and is routed to the
    per-instrument ``tdx_kline`` source. ``date`` stays current ("") — ONLINE
    reads the live boundary, never a caller-selected historical snapshot.
    """
    raw_limit = params.get("limit")
    limit = raw_limit if isinstance(raw_limit, int) and not isinstance(raw_limit, bool) else 20
    if method_id in {_OHLCV_METHOD_ID, _INDICATORS_METHOD_ID}:
        sym = params.get("symbol")
        code = str(sym.get("code") or "").strip() if isinstance(sym, Mapping) else ""
        if method_id == _OHLCV_METHOD_ID:
            return (code, "", max(limit, _OHLCV_PROBE_LIMIT))
        return (code, "", 1)
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
    :class:`RawRowCandidate` with ``available_at = <envelope pulled_at>``. The single
    PIT authority over everything served is the reviewed Phase-3 router's
    ``PitGuard.check_raw`` (``data/registry.py`` dispatch step 5c), which refuses any
    future (``pulled_at > as_of``) / missing-availability row loudly. The ONE
    source-side exclusion is a ``news`` row whose own PIT-CERTIFIED publication
    instant is later than the frozen boundary (:meth:`_news_candidates`) — the source
    never hands the guard a row the row itself certifies was not yet public, because
    the guard's refusal is all-or-nothing and would take the compliant rows with it.
    Every such exclusion is counted onto the module logger, never silent. No method
    takes a caller ``as_of`` / ``strict`` / ``now`` override — the boundary is
    ``request.as_of`` (the start-frozen ``DataContext`` as-of) only.

    Facade functions default to the live ``guanlan_v2.datafeed.live_client``
    surface, lazily imported at call time (import purity); tests inject fakes and
    hit no real vendor.
    """

    __slots__ = (
        "_probe_fn",
        "_catalog_fn",
        "_resolve_fn",
        "_known_fn",
        "_calendar",
        "_binding_snapshot",
    )

    def __init__(
        self,
        *,
        probe_fn: Callable[..., Mapping[str, Any]] | None = None,
        catalog_fn: Callable[..., Mapping[str, Any]] | None = None,
        resolve_source_fn: Callable[[str], str] | None = None,
        known_sources_fn: Callable[[], Any] | None = None,
        calendar: TradingCalendar | None = None,
        binding_snapshot: ReviewedLiveBindingSnapshot | None = None,
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
        self._binding_snapshot = (
            reviewed_live_binding_snapshot()
            if binding_snapshot is None
            else binding_snapshot
        )
        # Structural self-sealing is not authorization.  Even an injected probe
        # can run only under the exact code-owned production approval.
        verify_code_approved_live_binding_snapshot(self._binding_snapshot)

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
            binding.source_id
            for binding in sorted(
                self._binding_snapshot.bindings,
                key=lambda row: row.method_id,
            )
            if binding.source_id in known
        )

    @property
    def binding_snapshot(self) -> ReviewedLiveBindingSnapshot:
        """The exact code-approved physical binding frozen into this adapter."""
        return self._binding_snapshot

    def _resolve_binding(
        self, method_id: str,
    ) -> tuple[ReviewedLiveBinding, str]:
        """Resolve one signed source and reject aliases/substitutes before I/O."""
        binding = self._binding_snapshot.binding_for_method(method_id)
        canonical = self._resolve(binding.source_id)
        if not isinstance(canonical, str) or canonical != binding.source_id:
            raise RoutingConfigurationError(
                f"resolver result {canonical!r} does not exactly match the "
                f"run-approved source {binding.source_id!r} for method "
                f"{method_id!r}; no alias or silent substitute is allowed"
            )
        return binding, canonical

    def capture_indicator_book(self, *, code: str) -> tuple[str, Mapping[str, Any]]:
        """Pull one exact-code timed book before the deep context is frozen.

        Projection intentionally happens later, after the caller records the
        authoritative capture-completion instant and builds the request-bound
        ONLINE context.  Runtime then replays the persisted projection and never
        performs a second probe.
        """
        binding, canonical = self._resolve_binding(_INDICATORS_METHOD_ID)
        envelope = self._probe(canonical, code=code, date="", limit=1)
        return canonical, _validate_live_envelope(
            envelope,
            binding=binding,
            canonical=canonical,
            method_id=_INDICATORS_METHOD_ID,
        )

    # -- the DataSource protocol surface (read-only) ------------------------ #
    def fetch(self, request: DataRequest, *, scope: DataInvocationScope) -> RawFetch:
        method_id = request.method_spec_ref.id
        if method_id not in _SUPPORTED_METHOD_IDS:
            raise RoutingConfigurationError(
                f"LiveClientSource serves only {sorted(_SUPPORTED_METHOD_IDS)}; "
                f"method {method_id!r} is not bound to this source"
            )
        binding, canonical = self._resolve_binding(method_id)
        if request.method_spec_ref.content_digest != binding.method_spec_digest:
            raise RoutingConfigurationError(
                f"request method spec for {method_id!r} does not match the "
                "run-approved live binding; method/schema drift requires review"
            )

        entry = scope.frozen_route.entries[0]
        if (
            method_id == _INDICATORS_METHOD_ID
            and str(getattr(request, "data_snapshot_id", "")).startswith(
                _DEEP_CAPTURE_PREFIX)
        ):
            # The run's cutoff was frozen after this exact provider observation
            # completed.  Reload the persisted RawFetch; probing here would be a
            # different, post-freeze observation and is prohibited.
            raw = _load_precaptured_indicator_fetch(request)
            if raw.source_ref != entry.source_ref:
                raise SourceBrokenError(
                    "persisted deep indicator evidence names a different source")
            if raw.capability_ref != entry.capability_ref:
                raise SourceBrokenError(
                    "persisted deep indicator evidence names a different capability")
            if raw.subsource != canonical:
                raise SourceBrokenError(
                    "persisted deep indicator evidence names a different live subsource")
            return raw

        code, date, limit = _probe_args(method_id, request.params)
        envelope = self._probe(canonical, code=code, date=date, limit=limit)
        envelope = _validate_live_envelope(
            envelope,
            binding=binding,
            canonical=canonical,
            method_id=method_id,
        )

        # -- data-bearing: wrap each row with ITS OWN availability --------------- #
        items = envelope.get("items") or ()
        if envelope.get("status") == "stale":
            if method_id == _VERIFIED_SNAPSHOT_METHOD_ID:
                # A malformed stale quote must never fall through to an
                # envelope-pulled live candidate whose price looks current.
                candidates = self._stale_settled_candidates(request, items)
            elif method_id == _INDICATORS_METHOD_ID:
                candidates = _indicator_candidates(
                    request,
                    items,
                    self._calendar,
                    _online_availability(envelope.get("pulled_at")),
                    stale_envelope=True,
                )
            else:  # the envelope classifier makes this unreachable
                raise SourceBrokenError(
                    f"status=stale is not projectable for method {method_id!r}")
        else:
            avail = _online_availability(envelope.get("pulled_at"))
            if method_id == _NEWS_METHOD_ID:
                candidates = self._news_candidates(request, items, avail)
            elif method_id == _OHLCV_METHOD_ID:
                candidates = self._ohlcv_candidates(request, items)
            elif method_id == _INDICATORS_METHOD_ID:
                candidates = _indicator_candidates(
                    request, items, self._calendar, avail, stale_envelope=False)
            else:
                candidates = tuple(
                    self._row_candidate(request, method_id, item, avail)
                    for item in items
                )
        # fetched_at is the FROZEN as-of (audit-only) — no wall-clock read exists here.
        return build_raw_fetch(
            request_digest=request.request_digest, source_ref=entry.source_ref,
            capability_ref=entry.capability_ref, candidates=candidates,
            subsource=canonical, fetched_at=request.as_of, provider_request_id=None,
        )

    def _ohlcv_candidates(
        self, request: DataRequest, items: Any
    ) -> tuple[RawRowCandidate, ...]:
        """Map TDX daily bars through a dedicated strict PIT projection.

        This route deliberately never calls :func:`_row_payload`: a market-flow
        row and an OHLCV bar may share words such as ``open``/``close`` but are
        not interchangeable evidence.  Each accepted candidate is tied to the
        requested symbol, carries only the five bar fields, maps TDX ``vol`` to
        an exact non-negative integer ``volume``, and is stamped from the row's
        own daily-bar session at the A-share close.  An unclosed current-session
        bar is excluded (and counted) rather than letting the all-or-nothing PIT
        guard reject the historical rows beside it.
        """
        out: list[RawRowCandidate] = []
        excluded_future = 0
        for item in items:
            candidate = _ohlcv_candidate(request, item)
            if candidate.available_at is not None and candidate.available_at > request.as_of:
                excluded_future += 1
                continue
            out.append(candidate)
        if excluded_future:
            _LOG.warning(
                "%s: %d unclosed/future daily bar(s) excluded after row-time "
                "projection (frozen as_of %s; %d closed bar(s) remain)",
                _OHLCV_METHOD_ID, excluded_future, request.as_of.isoformat(), len(out),
            )
        if len(out) < _OHLCV_MIN_BARS:
            raise SourceBrokenError(
                f"live tdx_kline returned only {len(out)} closed, valid PIT bar(s) "
                f"for method={_OHLCV_METHOD_ID}; at least {_OHLCV_MIN_BARS} are "
                "required for price-action geometry"
            )
        if self._calendar is not None:
            expected = _latest_completed_session(self._calendar, request.as_of)
            if expected is None:
                raise SourceBrokenError(
                    "the committed trading calendar cannot prove the latest "
                    "completed OHLCV session for the frozen as_of"
                )
            latest = max(
                c.available_at for c in out if c.available_at is not None
            )
            actual = latest.astimezone(_EXCHANGE_TZ).date()
            if actual != expected:
                raise SourceBrokenError(
                    "live tdx_kline latest closed bar is not the exact latest "
                    "completed session: "
                    f"expected={expected.isoformat()} actual={actual.isoformat()}"
                )
        return tuple(out)

    def _news_candidates(
        self, request: DataRequest, items: Any, envelope_avail: datetime | None
    ) -> tuple[RawRowCandidate, ...]:
        """The ``news`` route: publication-stamped rows, minus the not-yet-public ones.

        **Live regression** — run ``deep-78abf946944b9984`` (2026-08-11 09:49 CST):
        the ``news`` node died with ``FutureDataRefused`` ("20 row(s) have
        available_at after the frozen as_of"). The read had genuinely happened and
        every row was real; they were refused only because this adapter stamped ALL
        of them with the envelope's ``pulled_at`` (the 09:49 pull instant) while the
        run's ``as_of`` is the session-midnight boundary. Measured against the real
        vendor the same morning, every ``eastmoney_stock_news`` row carries its own
        ``publish_ts`` with ``visible_ts_reason="exact_publish_time"`` — the 20
        refused rows had been public for weeks.

        Two source-side halves, neither of which touches the guard:

        1. a row that ATTESTS its own publication instant
           (:func:`_attested_publication`) is stamped with it — the datum's own
           availability, the CONTROLLER RULING's own rule, applied where the datum
           actually carries it. An UNATTESTED row keeps the envelope's ``pulled_at``
           exactly as before (so the freeze stays enforced and visible, and a
           missing ``pulled_at`` still reaches the guard as ``available_at=None``).
        2. a row whose OWN attested publication instant is **after** the frozen
           ``request.as_of`` is EXCLUDED. Serving a row the source already knows the
           guard must refuse is a source-side bug, and it is not a private bug:
           ``PitGuard.check_raw`` is ALL-OR-NOTHING (``data/pit.py:485-540``), so one
           announcement published this morning would refuse the WHOLE batch of
           long-public rows — precisely the live failure, one intraday headline away
           from recurring even with (1) in place.

        The exclusion **only excludes**: no ``available_at`` is fabricated, none is
        backdated, and a row is never re-stamped to make it pass. Nothing is dropped
        on a claim the source cannot read — only on the row's own PIT-certified
        provenance (:func:`_attested_publication`). Rows that are forwarded are still
        judged by the single PIT authority, and the adapter still performs no time
        comparison of any kind on the other routes (``verified_snapshot`` answers the
        ruling through its calendar-attributed settled projection; ``ohlcv`` is
        unchanged).

        **An exclusion is never silent (review Important 2).** Every excluded row is
        counted and the count is emitted as a WARNING on this module's logger, so an
        all-excluded read is distinguishable from "the vendor returned nothing" — the
        same honest-badge idiom :mod:`~guanlan_v2.orchestration.adapters.durable`
        uses for its torn-tail drop. The log is the carrier because both structural
        carriers are closed by red lines, measured not assumed: a field on
        :class:`RawFetch` moves the sealed phase-3 schema-registry digest
        (``RawFetch@1`` is one of the 76 entries behind
        ``tests/orchestration/golden/data_schema_manifest_v1.json``,
        registry ``9119fa17…``), and a marker key in ``raw_payload`` would land an
        UNBONDABLE claim in the trusted prompt header — the one thing
        ``data/registry.py::_settled_session_marker``'s reviewed §I2 rule forbids,
        since dispatch cannot re-derive a count for rows it never saw.
        """
        out: list[RawRowCandidate] = []
        excluded = 0
        for item in items:
            published = _attested_publication(item)
            if published is not None and published > request.as_of:
                excluded += 1  # the row itself says it was not yet public
                continue
            out.append(self._candidate(
                request, _NEWS_METHOD_ID, item,
                published if published is not None else envelope_avail))
        if excluded:
            _LOG.warning(
                "%s: %d row(s) excluded: attested publication postdates the frozen "
                "as_of %s (%d row(s) served)",
                _NEWS_METHOD_ID, excluded, request.as_of.isoformat(), len(out),
            )
        return tuple(out)

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

    def _stale_settled_candidates(
        self, request: DataRequest, items: Any
    ) -> tuple[RawRowCandidate, ...]:
        """Strictly reduce a stale verified snapshot to settled rows only.

        ``status=stale`` describes the provider's current quote, so its ``price``
        and envelope pull time are not admissible as current evidence.  Every row
        must independently prove a readable settled close plus venue/session PIT
        provenance through :meth:`_settled_candidate`.  One malformed row voids
        the entire envelope; there is no live-candidate fallback on this path.
        """
        rows = tuple(items or ())
        if not rows:
            raise SourceBrokenError(
                "stale verified_snapshot envelope has no rows to project as "
                "settled evidence"
            )
        projected: list[RawRowCandidate] = []
        for index, item in enumerate(rows):
            if not isinstance(item, Mapping):
                raise SourceBrokenError(
                    "stale verified_snapshot item "
                    f"{index} is not a mapping and cannot prove settled evidence"
                )
            settled = self._settled_candidate(request, item)
            if settled is None:
                raise SourceBrokenError(
                    "stale verified_snapshot item "
                    f"{index} has no usable settled evidence with a readable "
                    "close and venue/session PIT provenance"
                )
            projected.append(settled)
        return tuple(projected)

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
        close (a non-positive/non-finite value is NOT readable — see
        :func:`_settled_close`), when no venue quote timestamp is readable, or
        when the committed calendar cannot ANSWER which session precedes the
        anchor (outside its covered span, a non-session anchor, or no earlier
        covered session). The live candidate's honest PitGuard refusal then
        stands as the outcome.
        """
        calendar = self._calendar
        if calendar is None:
            return None
        view = _vendor_row_view(item)
        _require_verified_snapshot_identity(request, item, view)
        close = _settled_close(view)
        if close is None:
            return None
        # the anchor gate reads the ITEM's own attestation, never the merged view
        # (review M2 — the vendor row must not be able to overrule the shaper).
        anchor = _quote_anchor_date(view, item)
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
