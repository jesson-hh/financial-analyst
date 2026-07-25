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

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
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
    # --- Task 2b: per-feed archive coverage floors + feasible-window honesty ---
    "ARCHIVE_FEED_IDS",
    "ArchiveFeedFloor",
    "archive_feed_floor_from_read",
    "macro_feed_floor_from_snapshots",
    "ArchiveFeedVerdict",
    "ReplayFeasibleWindow",
    "derive_replay_feasible_window",
    "pit_replay_source_refs",
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

# --------------------------------------------------------------------------- #
# Per-feed snapshot-archive coverage floors (Task 2b)                          #
# --------------------------------------------------------------------------- #
# The Lane-0 market-factor battery reads three snapshot-archive feeds through
# ``guanlan_v2.datafeed.snapshot_archive.read_archive(kind, ...)`` — the delivered
# ``KINDS`` (甲 commit 2d2aba8): ``market_tape`` (炸板率/连板梯队/晋级率/北向),
# ``fundflow_concept`` (rot.* — compute deferred), ``fundflow_industry`` (主力分位)
# — plus the SEPARATE macro-pulse feed ``macro`` (``guanlan_v2.macro.pulse._read_snapshots``,
# ``var/macro_pulse/snapshots.jsonl``) that supplies ``temp.astock``'s history. These
# four are the feeds whose historical series only exist from an archive floor forward;
# ``ARCHIVE_FEED_IDS`` is drift-guarded against ``snapshot_archive.KINDS`` in the tests.
_ARCHIVE_FEED_MARKET_TAPE = "market_tape"
_ARCHIVE_FEED_FUNDFLOW_CONCEPT = "fundflow_concept"
_ARCHIVE_FEED_FUNDFLOW_INDUSTRY = "fundflow_industry"
_ARCHIVE_FEED_MACRO = "macro"
#: the canonical Lane-0 archive feed ids (three snapshot_archive KINDS + macro-pulse).
ARCHIVE_FEED_IDS: tuple[str, ...] = (
    _ARCHIVE_FEED_MARKET_TAPE,
    _ARCHIVE_FEED_FUNDFLOW_CONCEPT,
    _ARCHIVE_FEED_FUNDFLOW_INDUSTRY,
    _ARCHIVE_FEED_MACRO,
)

#: the digest-bearing archive-floor entry's payload schema ref (like ``PitStoreMeta``).
_ARCHIVE_FLOOR_SCHEMA_REF = SchemaRef(name="ArchiveCoverageFloor", version="1")
#: the badge prefix a pre-floor feed carries (``archive_pre_floor:<feed_id>``).
_ARCHIVE_PRE_FLOOR_BADGE = "archive_pre_floor"
#: the shared MarketFactorValue.status vocabulary (①§2 / AMEND-1) at feed granularity.
_STATUS_OK = "OK"
_STATUS_UNAVAILABLE = "UNAVAILABLE"


def _iso_from_yyyymmdd(value: Any) -> str | None:
    """``'20260716'`` / ``'2026-07-16'`` / ``'2026-07-16T…'`` → ``'2026-07-16'``.

    Mirrors ``market.factors._iso_from_yyyymmdd`` so the archive read surface's
    ``YYYYMMDD`` ``first_date`` normalizes to the ISO date the floor comparison uses.
    An unparseable / short value yields ``None`` (honest: no floor).
    """
    digits = "".join(ch for ch in str(value or "") if ch.isdigit())
    if len(digits) < 8:
        return None
    return f"{digits[0:4]}-{digits[4:6]}-{digits[6:8]}"


def _require_iso_date(value: str) -> str:
    """Reject anything that is not a real ISO ``YYYY-MM-DD`` calendar date."""
    try:
        datetime.strptime(value, "%Y-%m-%d")
    except ValueError:
        raise ValueError(f"floor_date must be a valid ISO YYYY-MM-DD date, got {value!r}")
    return value


@dataclass(frozen=True)
class ArchiveFeedFloor:
    """One Lane-0 feed's archive coverage floor (input to the replay manifest).

    ``feed_id`` is one of :data:`ARCHIVE_FEED_IDS` (bound to the delivered
    ``snapshot_archive.KINDS`` + the macro-pulse feed); ``floor_date`` is the feed's
    archive coverage floor as an ISO ``YYYY-MM-DD`` date — the earliest archived
    trading day (``read_archive(...).first_date``, normalized) — or ``None`` when the
    archive is empty (no coverage at all). ``archive_root`` is the physical archive
    root/locator and is **audit-only** — it never enters any semantic digest (it folds
    into the manifest's audit-only ``data_snapshot_id``). Per ruling R13 an archive is
    consumed by feed id + floor date only; the replay feasible window simply starts at
    each feed's floor (never a hard Phase-9 precondition).
    """

    feed_id: str
    floor_date: str | None
    archive_root: str

    def __post_init__(self) -> None:
        if self.feed_id not in ARCHIVE_FEED_IDS:
            raise ValueError(
                f"feed_id {self.feed_id!r} is not a delivered archive feed "
                f"{ARCHIVE_FEED_IDS}"
            )
        if self.floor_date is not None:
            _require_iso_date(self.floor_date)
        if not str(self.archive_root):
            raise ValueError("archive_root (audit-only physical locator) must be non-empty")


def archive_feed_floor_from_read(
    feed_id: str, read_result: Mapping[str, Any], *, archive_root: str
) -> ArchiveFeedFloor:
    """Build an :class:`ArchiveFeedFloor` from a ``snapshot_archive.read_archive`` result.

    ``read_result`` is the frozen ``{kind, rows, first_date}`` supply contract; its
    ``first_date`` is a ``YYYYMMDD`` string (or ``None`` on an empty archive), which is
    normalized to the ISO floor date. This is the sole binding to the delivered read
    surface — the caller (Task 4) reads the archive once per feed and passes the result
    here; the physical ``archive_root`` stays audit-only.
    """
    floor = _iso_from_yyyymmdd(read_result.get("first_date"))
    return ArchiveFeedFloor(feed_id=feed_id, floor_date=floor, archive_root=archive_root)


def macro_feed_floor_from_snapshots(
    *, snapshots_path: str | Path | None = None
) -> ArchiveFeedFloor:
    """Build the ``macro`` feed's :class:`ArchiveFeedFloor` from the macro-pulse surface.

    The macro (``temp.astock``) history does **NOT** live in
    ``snapshot_archive.read_archive`` — that serves only the three ``KINDS``. It lives
    in the DELIVERED macro-pulse append-only jsonl (``guanlan_v2.macro.pulse``,
    ``var/macro_pulse/snapshots.jsonl``): one row per successful pull, each stamped with
    a Beijing-local ``ts``. The feed's coverage floor is the **earliest** snapshot's
    calendar date (append-only ⇒ the earliest date is stable as newer rows accrete).
    The macro-pulse module exposes no feed-id constant, so this binds to its read
    surface directly — the private ``_read_snapshots`` / ``_SNAP_DEFAULT`` (the same
    surface ``market.factors._load_astock_temp_rows`` reads); a real-fixture test guards
    against drift. Lazily imported (import purity, like the engine ``PitReader``). An
    empty / missing store ⇒ ``floor_date=None`` ⇒ honest UNAVAILABLE (never fabricated).
    """
    from guanlan_v2.macro.pulse import _SNAP_DEFAULT, _read_snapshots  # private read surface (bound intentionally)

    path = Path(snapshots_path) if snapshots_path is not None else _SNAP_DEFAULT
    dates: list[str] = []
    for row in _read_snapshots(path):
        day = str(row.get("ts") or "")[:10]  # ts = "YYYY-MM-DDTHH:MM:SS" (macro _iso)
        try:
            _require_iso_date(day)
        except ValueError:
            continue
        dates.append(day)
    floor = min(dates) if dates else None
    return ArchiveFeedFloor(feed_id=_ARCHIVE_FEED_MACRO, floor_date=floor, archive_root=str(path))


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


def _feed_floor_entry(floor: ArchiveFeedFloor, *, as_of: datetime) -> DataSnapshotEntry:
    """Bind ONE feed's archive coverage floor into a digest-bearing entry.

    Only the semantic facts (``feed_id`` + ``floor_date``) enter the entry's
    ``content_digest`` — a floor-date change moves the manifest's semantic digest,
    exactly like the pit_store ``_meta.json`` facts. The physical ``archive_root``
    never enters here (it is audit-only, folded into the manifest ``data_snapshot_id``).
    """
    facts = {"feed_id": floor.feed_id, "floor_date": floor.floor_date}
    return DataSnapshotEntry(
        dataset_id=f"snapshot_archive.{floor.feed_id}",
        method_id="archive.coverage_floor", source_id="snapshot.archive",
        revision_id=None, payload_schema_ref=_ARCHIVE_FLOOR_SCHEMA_REF,
        content_digest=content_digest(facts), max_available_at=as_of,
    )


def _compose_data_snapshot_id(
    data_snapshot_id: str, feed_floors: tuple[ArchiveFeedFloor, ...]
) -> str:
    """Fold the per-feed physical archive roots into the audit-only locator.

    ``data_snapshot_id`` is the manifest's single audit-only field (``SEMANTIC_EXCLUDE``),
    so appending the per-feed physical roots here keeps every physical root audit-only:
    relocating a feed's archive root changes the manifest's audit/dereference digest
    while leaving its semantic digest untouched. Empty ``feed_floors`` ⇒ the bare
    ``data_snapshot_id`` unchanged (Task 2's manifests stay byte-identical).
    """
    if not feed_floors:
        return data_snapshot_id
    roots = ";".join(
        f"{f.feed_id}={f.archive_root}" for f in sorted(feed_floors, key=lambda f: f.feed_id)
    )
    return f"{data_snapshot_id}::archives={roots}"


def build_replay_manifest(
    *,
    data_snapshot_id: str,
    store_meta: Mapping[str, Any],
    as_of: datetime,
    timezone: str,
    calendar_id: str,
    routing_snapshot_digest: str,
    schema_registry_digest: str,
    feed_floors: Iterable[ArchiveFeedFloor] = (),
    entries: tuple[DataSnapshotEntry, ...] = (),
) -> DataSnapshotManifest:
    """Seal a complete, immutable ``pit_frozen`` PIT_REPLAY snapshot manifest.

    ``data_snapshot_id`` is the physical pit_store root/locator — **audit-only**
    (excluded from the manifest's semantic digest); the pit_store ``_meta.json``
    facts in ``store_meta`` are bound into a digest-bearing entry (semantic). The
    ``as_of`` must equal the decision point's ``decision_as_of`` (the caller's
    :func:`build_replay_data_context` re-asserts this through ``build_data_context``).

    ``feed_floors`` (Task 2b) adds one **digest-bearing archive coverage-floor entry**
    per Lane-0 feed the factor battery reads historically — each binds the semantic
    ``feed_id`` + ``floor_date`` (a floor-date change moves the manifest's semantic
    digest, exactly like the store-meta facts). Every feed's physical archive root
    stays audit-only (folded into ``data_snapshot_id``); an empty ``feed_floors`` leaves
    Task 2's manifest byte-identical.

    Note: the brief's illustrative ``(store_meta, entries)`` signature could not
    supply the coherence coordinates ``build_data_context`` requires; this reviewed
    signature adds them explicitly (recorded in the task report).
    """
    floors = tuple(feed_floors)
    floor_entries = tuple(_feed_floor_entry(f, as_of=as_of) for f in floors)
    ordered = tuple(entries) + floor_entries + (_store_meta_entry(store_meta, as_of=as_of),)
    return build_data_snapshot_manifest(
        data_snapshot_id=_compose_data_snapshot_id(data_snapshot_id, floors),
        manifest_kind="pit_frozen", as_of=as_of,
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


# --------------------------------------------------------------------------- #
# The replay feasible-window fact (Task 2b)                                    #
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class ArchiveFeedVerdict:
    """One feed's coverage verdict at a replay decision point (feed granularity).

    Carries the shared ``MarketFactorValue.status`` vocabulary (①§2 / AMEND-1) at
    feed granularity: a decision point on or after the feed's ``floor_date`` is
    ``covered`` (``OK``); a point before the floor (or a feed with no floor at all)
    is ``UNAVAILABLE`` with a mandatory ``reason`` + ``badge`` — that feed's factors
    resolve UNAVAILABLE downstream (缺历史覆盖 → UNAVAILABLE, 绝不补零/绝不拿当前快照冒充历史).
    There is **no** numeric payload (no value/series) at feed granularity — there is
    nothing to zero-fill or fabricate; the per-factor UNAVAILABLE values are produced
    by the Phase-5 compute core, whose loaders read the same archive by feed id.
    """

    feed_id: str
    floor_date: str | None
    covered: bool
    status: str
    reason: str | None
    badge: str | None


@dataclass(frozen=True)
class ReplayFeasibleWindow:
    """The derived per-feed feasibility fact for one replay decision point.

    ``decision_date`` is the Asia/Shanghai session date of the point's ``as_of`` (the
    floors are trading-day dates, so the boundary is date-granular). ``verdicts`` are
    sorted by ``feed_id``. Bootstrap degrades honestly on any UNAVAILABLE feed and
    continues; the Task 4 driver records a degraded point (never a run failure), and
    the pre-floor point's :attr:`digest` is archive-growth stable (invariant 2).
    """

    as_of: datetime
    decision_date: str
    verdicts: tuple[ArchiveFeedVerdict, ...]

    @property
    def unavailable_feed_ids(self) -> tuple[str, ...]:
        """The feed ids whose factors are UNAVAILABLE at this point (sorted)."""
        return tuple(v.feed_id for v in self.verdicts if not v.covered)

    def _semantic(self) -> dict[str, Any]:
        # date-granular identity: the raw as_of instant never enters (floors are
        # dates), so an old point's verdict is byte-stable as the archive accretes.
        return {
            "decision_date": self.decision_date,
            "verdicts": [
                {
                    "feed_id": v.feed_id, "floor_date": v.floor_date,
                    "covered": v.covered, "status": v.status,
                    "reason": v.reason, "badge": v.badge,
                }
                for v in self.verdicts
            ],
        }

    @property
    def digest(self) -> str:
        """The feasible-window fact's content digest (date-granular; growth-stable)."""
        return content_digest(self._semantic())


def _feed_reason(feed_id: str, floor_date: str | None, decision_date: str) -> str:
    tail = (
        "its factors are UNAVAILABLE — no zero-fill, no synthetic backfill, no "
        "current-snapshot fallback (缺历史覆盖 → UNAVAILABLE, AMEND-1)"
    )
    if floor_date is None:
        return f"the {feed_id} snapshot archive has no coverage (empty); {tail}"
    return (
        f"decision point {decision_date} precedes the {feed_id} archive coverage "
        f"floor {floor_date}; {tail}"
    )


def derive_replay_feasible_window(
    *, as_of: datetime, feed_floors: Iterable[ArchiveFeedFloor]
) -> ReplayFeasibleWindow:
    """Derive the per-feed feasibility fact for one replay decision point.

    Pure and deterministic (no wall-clock read): ``as_of`` must be aware (normalized
    to UTC); its Asia/Shanghai session date is compared against each feed's ISO
    ``floor_date``. A point **on or after** the floor ⇒ the feed is covered (``OK``);
    a point **strictly before** the floor — or a feed whose archive is empty
    (``floor_date is None``) — ⇒ ``UNAVAILABLE`` with a reason + badge. The floor
    boundary is exact (one session before ⇒ UNAVAILABLE, on the floor ⇒ resolves).
    The manifest that :func:`build_replay_manifest` seals from the SAME ``feed_floors``
    is the digest-bearing PIT seal of these floors.
    """
    as_of = ensure_aware_utc(as_of)
    decision_date = as_of.astimezone(_EXCHANGE_TZ).date().isoformat()
    verdicts: list[ArchiveFeedVerdict] = []
    for floor in sorted(feed_floors, key=lambda f: f.feed_id):
        covered = floor.floor_date is not None and decision_date >= floor.floor_date
        if covered:
            verdicts.append(ArchiveFeedVerdict(
                feed_id=floor.feed_id, floor_date=floor.floor_date, covered=True,
                status=_STATUS_OK, reason=None, badge=None))
        else:
            verdicts.append(ArchiveFeedVerdict(
                feed_id=floor.feed_id, floor_date=floor.floor_date, covered=False,
                status=_STATUS_UNAVAILABLE,
                reason=_feed_reason(floor.feed_id, floor.floor_date, decision_date),
                badge=f"{_ARCHIVE_PRE_FLOOR_BADGE}:{floor.feed_id}"))
    return ReplayFeasibleWindow(
        as_of=as_of, decision_date=decision_date, verdicts=tuple(verdicts))


# --------------------------------------------------------------------------- #
# PIT_REPLAY source resolution (structural: never an ONLINE source)           #
# --------------------------------------------------------------------------- #
def pit_replay_source_refs(
    source_registry: DataSourceRegistrySnapshot, *, method_ref: ContentRef
) -> tuple[ContentRef, ...]:
    """The PIT_REPLAY-eligible source refs for a method, in sealed-registry order.

    Reuses the Task 2 descriptor's supported-modes mechanism: a descriptor is
    eligible only when it binds ``method_ref`` **and** declares ``PIT_REPLAY`` in its
    ``supported_modes``. An ONLINE-only descriptor (``supported_modes`` without
    PIT_REPLAY — the Task 3 live source) is therefore structurally excluded, so a
    PIT_REPLAY read can never resolve a current-snapshot ONLINE source: a current
    snapshot can never impersonate history. Returns the descriptors' self-consistent
    source refs (``id@version`` + descriptor digest).
    """
    out: list[ContentRef] = []
    for desc in source_registry.source_descriptors:
        if method_ref in desc.method_refs and DataMode.PIT_REPLAY in desc.supported_modes:
            out.append(ContentRef(
                id=desc.source_id, version=desc.source_version,
                content_digest=desc.descriptor_digest))
    return tuple(out)
