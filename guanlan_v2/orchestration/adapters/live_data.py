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
  :class:`~guanlan_v2.orchestration.data.pit.RawRowCandidate` whose ``available_at``
  is the envelope's ``pulled_at`` (当时可知时间 = when we pulled it live); a missing
  ``pulled_at`` yields ``available_at=None`` and the single PIT authority
  (``PitGuard.check_raw``, invoked by the reviewed Phase-3 router at ``data/registry.py``
  dispatch step 5c) refuses it as :class:`MissingAvailabilityRefused` — the adapter
  never fabricates an ``available_at``. Because ``as_of`` is the **start-frozen** run
  instant, a row whose live ``pulled_at`` is *after* run start (later than ``as_of``)
  is refused by the guard as :class:`FutureDataRefused` — the freeze is real, not
  decorative. No method takes a caller ``as_of`` / ``strict`` override; the boundary
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
from datetime import datetime, timezone
from typing import Any, Callable, Mapping
from zoneinfo import ZoneInfo

from guanlan_v2.orchestration.context import DataContext
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

    __slots__ = ("_probe_fn", "_catalog_fn", "_resolve_fn", "_known_fn")

    def __init__(
        self,
        *,
        probe_fn: Callable[..., Mapping[str, Any]] | None = None,
        catalog_fn: Callable[..., Mapping[str, Any]] | None = None,
        resolve_source_fn: Callable[[str], str] | None = None,
        known_sources_fn: Callable[[], Any] | None = None,
    ) -> None:
        # The brief's __init__ lists probe_fn/catalog_fn; resolve_source_fn/
        # known_sources_fn are added (defaulting to the facade) so invariant 4
        # (unknown source fails loud) and the 枚举钉旧 gotcha guard can be exercised
        # against injected fakes without touching a real vendor (recorded in report).
        self._probe_fn = probe_fn
        self._catalog_fn = catalog_fn
        self._resolve_fn = resolve_source_fn
        self._known_fn = known_sources_fn

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

        # -- ok/data-bearing: wrap each row (available_at = envelope pulled_at) -- #
        avail = _online_availability(envelope.get("pulled_at"))
        items = envelope.get("items") or ()
        candidates = tuple(
            self._candidate(request, method_id, item, avail) for item in items
        )
        entry = scope.frozen_route.entries[0]
        # fetched_at is the FROZEN as-of (audit-only) — no wall-clock read exists here.
        return build_raw_fetch(
            request_digest=request.request_digest, source_ref=entry.source_ref,
            capability_ref=entry.capability_ref, candidates=candidates,
            subsource=canonical, fetched_at=request.as_of, provider_request_id=None,
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
