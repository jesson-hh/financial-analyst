# -*- coding: utf-8 -*-
"""Phase 9 · Task 2 — the strict raw PitReader adapter + the PIT_REPLAY DataContext.

This module is the point-in-time time-machine backbone the interval replay (Task 4)
reads through: at every historical decision point Bootstrap reruns, and every data
read at that point must be point-in-time honest. It owns exactly four things — one
clock, one raw source, and two builders:

* :class:`ReplayPointClock` — the frozen :class:`~guanlan_v2.orchestration.runtime_clock.AuthoritativeClock`
  for a replay point: the point's ``decision_as_of`` **is** the run clock. There is
  **no wall-clock read anywhere on the replay path** — ``now()`` returns the frozen
  aware-UTC ``as_of`` forever, and a naive ``as_of`` is rejected at construction.
* :class:`PitReaderRawSource` — the strict raw adapter behind the Phase-3
  :class:`~guanlan_v2.orchestration.data.source.DataSource` protocol. It binds the
  engine ``financial_analyst.backtest.pit_reader.PitReader`` (lazy-imported exactly
  like :func:`guanlan_v2.seats.news_marks._get_reader`; tests inject a fake) behind
  the **existing** Phase-3 method ids (``ohlcv`` for daily + intraday bars, ``news``
  for visible news/events/policy — no new ``DataMethodSpec`` id is minted). It wraps
  **every** returned row into a :class:`~guanlan_v2.orchestration.data.pit.RawRowCandidate`
  with an explicit ``available_at`` and **never pre-filters by time** — a future row
  the store leaks is forwarded, so the single PIT authority (``PitGuard.check_raw``,
  invoked by the reviewed Phase-3 router, ``data/registry.py`` dispatch step 5c)
  refuses it loudly. No method accepts a caller ``as_of`` / ``strict`` override; the
  boundary is the request's frozen as-of (the ``DataContext``) only.
* :func:`build_replay_manifest` — a ``manifest_kind="pit_frozen"`` snapshot manifest
  binding the pit_store ``_meta.json`` facts (``news_coverage_floor`` / ``cal_start`` /
  ``cal_end`` / ``data_end``) into a digest-bearing entry; the physical store root is
  the audit-only ``data_snapshot_id``, never semantic.
* :func:`build_replay_data_context` — constructs a :class:`ReplayPointClock` from the
  decision point and delegates to the ONE Phase-3 :func:`~guanlan_v2.orchestration.data.snapshot.build_data_context`
  with ``mode=PIT_REPLAY`` / ``backend=PIT_STORE``; the Phase-1 ``DataContext``
  coherence validator then enforces ``strict_pit=True`` + non-LIVE backend + a
  present vintage-manifest digest.

Red lines (user honesty red lines):

* **REFUSE, never filter.** The adapter never silently drops a future row and never
  degrades to a truncated OK result — it forwards every row to the PitGuard.
* **No silent fallback.** A refusal is a raise-bucket error (``FutureDataRefused`` /
  ``MissingAvailabilityRefused``); the router never advances to another vendor.
* **No caller time authority.** No method takes an ``as_of`` / ``strict`` / ``now``
  override; the frozen ``ReplayPointClock`` / ``DataContext`` is the only clock.

This module defines **no** new registered contract model: the clock and source are
plain service objects and the builders return existing Phase-1/Phase-3 models. The
descriptor helper (:func:`build_pit_replay_descriptor`) returns an existing
:class:`~guanlan_v2.orchestration.data.source.DataSourceDescriptor` bound to the
existing ``ohlcv`` / ``news`` method refs (Task 9 owns any registry classification).
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable, Iterable, Iterator, Mapping
from zoneinfo import ZoneInfo

from guanlan_v2.orchestration.adapters.contracts import ReplayDecisionPoint
from guanlan_v2.orchestration.context import DataContext
from guanlan_v2.orchestration.data.errors import RoutingConfigurationError
from guanlan_v2.orchestration.data.pit import RawRowCandidate
from guanlan_v2.orchestration.data.snapshot import (
    DataSnapshotEntry,
    DataSnapshotManifest,
    DataSourceConfigSnapshot,
    DataRoutingSnapshot,
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
from guanlan_v2.orchestration.digest import content_digest
from guanlan_v2.orchestration.enums import DataBackend, DataMode
from guanlan_v2.orchestration.refs import ContentRef, SchemaRef
from guanlan_v2.orchestration.runtime_clock import ensure_aware_utc

__all__ = [
    "ReplayPointClock",
    "PitReaderRawSource",
    "build_pit_replay_descriptor",
    "build_replay_manifest",
    "build_replay_data_context",
    "PIT_REPLAY_SOURCE_ID",
]

#: the A-share pit_store trading timezone — the reviewed exchange tz whose local
#: wall-time stamps (``ts`` / ``ann_date``) the store records; bar-close and
#: news/event availability are localized under it, exactly as the pit_store's own
#: naive-local comparison (``financial_analyst.backtest.pit_reader``) does.
_EXCHANGE_TZ = ZoneInfo("Asia/Shanghai")
#: the A-share regular-session close (local wall time) — a daily bar becomes knowable
#: at its date's market close.
_MARKET_CLOSE = "15:00:00"
#: the reviewed default news-scan window (trading-day lookback), mirroring
#: ``guanlan_v2.seats.news_marks`` (the news params carry no window field).
_NEWS_LOOKBACK_DAYS = 250

_OHLCV_METHOD_ID = "ohlcv"
_NEWS_METHOD_ID = "news"
#: the existing Phase-3 method-id families this adapter binds (no new id is minted):
#: daily + intraday bars → ``ohlcv``; visible news/events/policy → ``news``.
_SUPPORTED_METHOD_IDS = frozenset({_OHLCV_METHOD_ID, _NEWS_METHOD_ID})
#: ``field_id`` values that select the intraday bar surface of the ``ohlcv`` method.
_INTRADAY_FREQS = frozenset(
    {"1min", "5min", "15min", "30min", "60min", "1m", "5m", "15m", "30m", "60m"}
)

#: the reviewed logical id of the PIT_REPLAY raw source.
PIT_REPLAY_SOURCE_ID = "guanlan.pit_replay"
_CONFIG_SCHEMA_REF = SchemaRef(name="DataSourceConfigSnapshot", version="1")


def _is_aware(value: datetime) -> bool:
    return value.tzinfo is not None and value.tzinfo.utcoffset(value) is not None


def _availability(value: Any) -> datetime | None:
    """Derive an aware-UTC ``available_at`` from a store wall-time — never fabricate.

    The pit_store records local wall-time under the exchange tz (naive strings, e.g.
    ``"2026-07-16 09:31:00"``, or the ``ann_date`` at start-of-day); a naive value is
    localized to :data:`_EXCHANGE_TZ` and normalized to UTC, an already-aware value is
    normalized to UTC. A missing / empty / unparseable value yields ``None`` — the
    row is still forwarded (never dropped), and the PitGuard refuses it as
    ``MissingAvailabilityRefused``. The adapter therefore never emits a *naive*
    ``available_at`` (a naive instant is not canonically digestible and could never
    prove point-in-time anyway); the PitGuard's naive-refusal remains the
    defense-in-depth backstop for any directly-constructed candidate.
    """
    if value is None:
        return None
    if isinstance(value, str):
        s = value.strip().replace(" ", "T")
        if not s:
            return None
        try:
            parsed = datetime.fromisoformat(s)
        except ValueError:
            return None
    elif isinstance(value, datetime):
        parsed = value
    else:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=_EXCHANGE_TZ)  # localize a naive exchange wall-time
    return parsed.astimezone(timezone.utc)


def _pit_code(symbol: Mapping[str, Any] | None) -> str | None:
    if not symbol:
        return None
    exchange = str(symbol.get("exchange") or "").upper()
    code = str(symbol.get("code") or "")
    return f"{exchange}{code}" if exchange else (code or None)


def _symbol_dict(pit_code: Any) -> dict[str, Any] | None:
    """Best-effort ``exchange+code`` → ``{code, exchange, board}`` (macro → ``None``)."""
    if not pit_code:
        return None
    s = str(pit_code).strip().upper()
    if len(s) > 2 and s[:2].isalpha() and s[2:].isdigit():
        return {"code": s[2:], "exchange": s[:2], "board": "main"}
    return None


def _iter_bars(bars: Any) -> Iterator[tuple[Any, Mapping[str, Any]]]:
    """Iterate ``(close_time, ohlcv_row)`` from a DataFrame or a mapping iterable."""
    if hasattr(bars, "iterrows"):  # a pandas DataFrame (the engine PitReader path)
        for idx, series in bars.iterrows():
            yield str(idx), {c: series[c] for c in ("open", "high", "low", "close", "volume")}
        return
    for row in bars:
        yield row.get("time"), row


# --------------------------------------------------------------------------- #
# ReplayPointClock                                                            #
# --------------------------------------------------------------------------- #
class ReplayPointClock:
    """The frozen replay clock — the decision point's ``as_of`` IS the run clock.

    Implements the Phase-2 :class:`~guanlan_v2.orchestration.runtime_clock.AuthoritativeClock`
    protocol (``now() -> datetime``). It reads no wall clock: every ``now()`` returns
    the exact aware-UTC ``as_of`` the point was frozen at. A naive ``as_of`` is
    rejected at construction (there is no un-provable point-in-time on the replay
    path).
    """

    __slots__ = ("_as_of",)

    def __init__(self, *, as_of: datetime) -> None:
        self._as_of = ensure_aware_utc(as_of)  # rejects a naive value; normalizes to UTC

    def now(self) -> datetime:
        return self._as_of

    @property
    def as_of(self) -> datetime:
        return self._as_of


# --------------------------------------------------------------------------- #
# PitReaderRawSource                                                          #
# --------------------------------------------------------------------------- #
class PitReaderRawSource:
    """The strict raw PIT_REPLAY adapter (a Phase-3 ``DataSource``).

    Reads the engine pit_store through the lazily-imported ``PitReader`` and wraps
    every row into a :class:`RawRowCandidate` with an explicit ``available_at`` —
    **without** pre-filtering by time. The single PIT authority is the reviewed
    Phase-3 router's ``PitGuard.check_raw`` (``data/registry.py`` dispatch step 5c),
    which refuses any future / missing-availability row loudly; this adapter never
    silently drops a row and never degrades to a truncated result. No method accepts
    a caller ``as_of`` / ``strict`` override — the boundary is ``request.as_of`` (the
    frozen ``DataContext`` as-of) only, and no wall clock is ever read.
    """

    __slots__ = ("_reader_factory", "_reader")

    def __init__(self, *, reader_factory: Callable[[], Any] | None = None) -> None:
        self._reader_factory = reader_factory
        self._reader: Any = None

    def _get_reader(self) -> Any:
        if self._reader is None:
            factory = self._reader_factory
            if factory is None:
                from financial_analyst.backtest.pit_reader import PitReader
                factory = PitReader
            self._reader = factory()
        return self._reader

    # -- the DataSource protocol surface ------------------------------------ #
    def fetch(self, request: DataRequest, *, scope: DataInvocationScope) -> RawFetch:
        method_id = request.method_spec_ref.id
        if method_id == _OHLCV_METHOD_ID:
            candidates = self._read_ohlcv(request)
        elif method_id == _NEWS_METHOD_ID:
            candidates = self._read_news(request)
        else:
            raise RoutingConfigurationError(
                f"PitReaderRawSource serves only {sorted(_SUPPORTED_METHOD_IDS)}; "
                f"method {method_id!r} is not bound to this source"
            )
        entry = scope.frozen_route.entries[0]
        # fetched_at is the FROZEN as-of (audit-only) — no wall-clock read exists here.
        return build_raw_fetch(
            request_digest=request.request_digest, source_ref=entry.source_ref,
            capability_ref=entry.capability_ref, candidates=tuple(candidates),
            subsource="pit_store", fetched_at=request.as_of, provider_request_id=None,
        )

    # -- bars (daily + intraday) -> the ``ohlcv`` method -------------------- #
    def _read_ohlcv(self, request: DataRequest) -> tuple[RawRowCandidate, ...]:
        reader = self._get_reader()
        params = request.params
        symbol = params.get("symbol") or {}
        pit_code = _pit_code(symbol)
        local = request.as_of.astimezone(_EXCHANGE_TZ)
        day = local.strftime("%Y-%m-%d")
        field_id = params.get("field_id")
        out: list[RawRowCandidate] = []
        if field_id and str(field_id).lower() in _INTRADAY_FREQS:
            bars = reader.fetch_bars_intraday(pit_code, day, str(field_id))
            for close_time, row in _iter_bars(bars):
                out.append(self._bar_candidate(request, symbol, close_time, row, daily=False))
        else:
            bars = reader.fetch_quote_leq_prev(pit_code, as_of_date=day)
            for bar_time, row in _iter_bars(bars):
                out.append(self._bar_candidate(request, symbol, bar_time, row, daily=True))
        return tuple(out)

    def _bar_candidate(self, request: DataRequest, symbol: Mapping[str, Any],
                       bar_time: Any, row: Mapping[str, Any], *, daily: bool) -> RawRowCandidate:
        if daily:
            avail = _availability(f"{str(bar_time)[:10]}T{_MARKET_CLOSE}")
        else:
            avail = _availability(bar_time)
        payload = {
            "symbol": dict(symbol),
            "open": float(row["open"]), "high": float(row["high"]),
            "low": float(row["low"]), "close": float(row["close"]),
            "volume": int(row["volume"]),
        }
        return _candidate(request, payload, avail)

    # -- visible news + events + policy -> the ``news`` method -------------- #
    def _read_news(self, request: DataRequest) -> tuple[RawRowCandidate, ...]:
        reader = self._get_reader()
        local = request.as_of.astimezone(_EXCHANGE_TZ)
        day = local.strftime("%Y-%m-%d")
        tm = local.strftime("%H:%M:%S")
        symbols = request.params.get("symbols") or ()
        codes = [c for c in (_pit_code(s) for s in symbols) if c]
        vi = reader.get_visible_info(
            day, codes=codes or None, as_of=tm, lookback_days=_NEWS_LOOKBACK_DAYS,
            include=("news", "events", "policy"),
        )
        out: list[RawRowCandidate] = []
        for it in getattr(vi, "news", ()) or ():
            out.append(_candidate(
                request,
                _news_payload(getattr(it, "title", "") or "", getattr(it, "url", None),
                              getattr(it, "code", None)),
                _availability(getattr(it, "ts", None)),
            ))
        for it in getattr(vi, "events", ()) or ():
            fields = getattr(it, "fields", None) or {}
            # ann_date end-of-availability convention (identical to news_marks.py):
            # visible_ts if present, else the ann_date at start-of-day.
            visible_ts = fields.get("visible_ts") or (str(getattr(it, "ann_date", "")) + "T00:00:00")
            headline = (getattr(it, "summary", "") or getattr(it, "type", "")) or ""
            out.append(_candidate(
                request, _news_payload(headline, None, getattr(it, "code", None)),
                _availability(visible_ts),
            ))
        for it in getattr(vi, "policy", ()) or ():
            out.append(_candidate(
                request,
                _news_payload(getattr(it, "title", "") or "", getattr(it, "url", None),
                              getattr(it, "code", None)),
                _availability(getattr(it, "ts", None)),
            ))
        return tuple(out)


def _news_payload(headline: str, url: Any, pit_code: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {"headline": headline}
    if url is not None:
        payload["url"] = url
    symbol = _symbol_dict(pit_code)
    if symbol is not None:
        payload["symbol"] = symbol
    return payload


def _candidate(request: DataRequest, payload: Mapping[str, Any],
               avail: datetime | None) -> RawRowCandidate:
    """Wrap one raw row — forwarded whatever its availability proves (never dropped).

    ``avail`` is an aware-UTC instant or ``None`` (a missing timestamp): the row is
    forwarded either way, and the PitGuard is the single authority that refuses a
    future / missing availability.
    """
    return RawRowCandidate.build(
        raw_payload=dict(payload), effective_at=avail, available_at=avail,
        ingested_at=request.as_of,
    )


# --------------------------------------------------------------------------- #
# build_pit_replay_descriptor                                                 #
# --------------------------------------------------------------------------- #
def build_pit_replay_descriptor(
    *, method_specs: Iterable[DataMethodSpec], handler_ref: ContentRef
) -> DataSourceDescriptor:
    """Build the reviewed PIT_REPLAY-only :class:`DataSourceDescriptor`.

    Binds the **existing** ``ohlcv`` / ``news`` method refs (no new id is minted);
    supported modes are exactly ``(PIT_REPLAY,)`` and the backend exactly
    ``(PIT_STORE,)`` — ``DataMode`` routing can therefore never select this source
    for an ONLINE read, so a current snapshot can never impersonate history. Every
    bound method spec is ``read_only=True`` by its own contract.
    """
    bound = tuple(s for s in method_specs if s.method_id in _SUPPORTED_METHOD_IDS)
    have = {s.method_id for s in bound}
    if have != set(_SUPPORTED_METHOD_IDS):
        missing = sorted(set(_SUPPORTED_METHOD_IDS) - have)
        raise ValueError(
            f"PIT_REPLAY descriptor requires the existing {missing} method spec(s) "
            "in method_specs (no new DataMethodSpec id may be minted)"
        )
    caps = tuple(sorted({s.capability_ref for s in bound}, key=lambda c: (c.id, c.version)))
    return DataSourceDescriptor.build(
        source_id=PIT_REPLAY_SOURCE_ID, source_version="1",
        method_refs=tuple(s.method_ref for s in bound),
        method_capability_refs=caps,
        supported_modes=(DataMode.PIT_REPLAY,),
        supported_backends=(DataBackend.PIT_STORE,),
        handler_ref=handler_ref,
        source_config_schema_ref=_CONFIG_SCHEMA_REF,
    )


# --------------------------------------------------------------------------- #
# build_replay_manifest                                                       #
# --------------------------------------------------------------------------- #
def _store_meta_entry(store_meta: Mapping[str, Any], *, as_of: datetime) -> DataSnapshotEntry:
    """Bind the pit_store ``_meta.json`` facts into ONE digest-bearing entry.

    ``news_coverage_floor`` / ``cal_start`` / ``cal_end`` / ``data_end`` are semantic
    (a floor-date change moves the manifest's semantic digest). The physical store
    root never enters here — it is the manifest's audit-only ``data_snapshot_id``.
    """
    facts = {k: store_meta.get(k) for k in ("news_coverage_floor", "cal_start", "cal_end", "data_end")}
    return DataSnapshotEntry(
        dataset_id="pit_store.meta", method_id="store.meta", source_id="pit.store",
        revision_id=None, payload_schema_ref=SchemaRef(name="PitStoreMeta", version="1"),
        content_digest=content_digest(facts), max_available_at=as_of,
    )


def build_replay_manifest(
    *,
    data_snapshot_id: str,
    store_meta: Mapping[str, Any],
    as_of: datetime,
    timezone: str,
    calendar_id: str,
    routing_snapshot_digest: str,
    schema_registry_digest: str,
    entries: tuple[DataSnapshotEntry, ...] = (),
) -> DataSnapshotManifest:
    """Seal a complete, immutable ``pit_frozen`` PIT_REPLAY snapshot manifest.

    ``data_snapshot_id`` is the physical pit_store root/locator — **audit-only**
    (excluded from the manifest's semantic digest); the pit_store ``_meta.json``
    facts in ``store_meta`` are bound into a digest-bearing entry (semantic). The
    ``as_of`` must equal the decision point's ``decision_as_of`` (the caller's
    :func:`build_replay_data_context` re-asserts this through ``build_data_context``).

    Note: the brief's illustrative ``(store_meta, entries)`` signature could not
    supply the coherence coordinates ``build_data_context`` requires; this reviewed
    signature adds them explicitly (recorded in the task report).
    """
    ordered = tuple(entries) + (_store_meta_entry(store_meta, as_of=as_of),)
    return build_data_snapshot_manifest(
        data_snapshot_id=data_snapshot_id, manifest_kind="pit_frozen", as_of=as_of,
        mode=DataMode.PIT_REPLAY, timezone=timezone, calendar_id=calendar_id,
        routing_snapshot_digest=routing_snapshot_digest,
        schema_registry_digest=schema_registry_digest, entries=ordered,
    )


# --------------------------------------------------------------------------- #
# build_replay_data_context                                                   #
# --------------------------------------------------------------------------- #
def build_replay_data_context(
    *,
    decision_point: ReplayDecisionPoint,
    source_config: DataSourceConfigSnapshot,
    source_registry: DataSourceRegistrySnapshot,
    routing: DataRoutingSnapshot,
    manifest: DataSnapshotManifest,
) -> DataContext:
    """Build the PIT_REPLAY :class:`DataContext` for one decision point.

    Constructs a :class:`ReplayPointClock` from ``decision_point.decision_as_of`` and
    delegates to the ONE Phase-3 :func:`build_data_context` with ``mode=PIT_REPLAY`` /
    ``backend=PIT_STORE``. The Phase-1 ``DataContext`` coherence validator then
    enforces ``strict_pit=True`` + a non-LIVE backend + a present vintage-manifest
    digest — a PIT_REPLAY context without a frozen vintage is impossible by
    construction, and its ``as_of`` equals the decision point's ``decision_as_of``.
    """
    clock = ReplayPointClock(as_of=decision_point.decision_as_of)
    return build_data_context(
        clock, mode=DataMode.PIT_REPLAY, backend=DataBackend.PIT_STORE,
        source_config=source_config, source_registry=source_registry,
        routing=routing, manifest=manifest,
    )
