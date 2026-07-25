# -*- coding: utf-8 -*-
"""Phase 9 · Task 3 — the live_client ONLINE adapter + the start-frozen as_of.

The ONLINE mirror of Task 2's PIT_REPLAY backbone. Both compose with the SAME
reviewed Phase-3 routing / dispatch / PitGuard machinery; this one reads the LIVE
现拉 门户 (``guanlan_v2.datafeed.live_client``) at the run's start-frozen instant
instead of the engine pit_store at a frozen historical point.

Design (recorded in the report's correction clauses): ``LiveClientSource`` is a
pure Phase-3 ``DataSource`` (``fetch(request, *, scope) -> RawFetch``) that wraps
every probe-envelope row into a ``RawRowCandidate`` with ``available_at = <envelope
pulled_at>``, WITHOUT pre-filtering by time. The single PIT authority is
``PitGuard.check_raw`` — exactly where the reviewed Phase-3 router invokes it
(``data/registry.py`` dispatch step 5c). Because ``as_of`` is the start-frozen run
instant, a row whose live ``pulled_at`` is AFTER run start is refused as
``FutureDataRefused`` (the freeze is real, not decorative); a missing ``pulled_at``
is refused as ``MissingAvailabilityRefused`` (no fabricated availability). The
honesty contract of the facade (caller/mechanical error → ok:False; upstream
planned/error → ok:True + visible status) is mapped onto the Phase-3 error taxonomy
and proven both at the adapter boundary and end-to-end through the REAL ``dispatch``.

Tests inject a fake probe (and, where needed, a fake resolve_source); no real
vendor is ever hit.

Run: ``python -m pytest tests/orchestration/test_adapters_live_data.py -v``
"""
from __future__ import annotations

import inspect
import re
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from guanlan_v2.orchestration.adapters import live_data
from guanlan_v2.orchestration.adapters.live_data import (
    ONLINE_SOURCE_ID,
    LiveClientSource,
    build_online_capture_manifest,
    build_online_data_context,
    build_online_live_descriptor,
)
from guanlan_v2.orchestration.adapters.replay_data import (
    PIT_REPLAY_SOURCE_ID,
    build_pit_replay_descriptor,
    pit_replay_source_refs,
)
from guanlan_v2.orchestration.context import DataContext
from guanlan_v2.orchestration.data.calendar import build_trading_calendar
from guanlan_v2.orchestration.data.catalog import phase3_data_surface
from guanlan_v2.orchestration.data.errors import (
    FutureDataRefused,
    MissingAvailabilityRefused,
    NotConfiguredError,
    RoutingConfigurationError,
    SourceBrokenError,
)
from guanlan_v2.orchestration.data.pit import PitGuard
from guanlan_v2.orchestration.data.registry import DataSourceRegistry, dispatch
from guanlan_v2.orchestration.data.schema_registry import build_phase3_registry
from guanlan_v2.orchestration.data.snapshot import (
    DataSnapshotManifest,
    build_data_source_config_snapshot,
)
from guanlan_v2.orchestration.data.source import (
    DataInvocationScope,
    DataSourceDescriptor,
    RawFetch,
    ResolvedDataMethodPolicy,
    ResolvedMethodRoute,
    RouteEntry,
    build_data_request,
    build_raw_fetch,
)
from guanlan_v2.orchestration.digest import content_digest
from guanlan_v2.orchestration.enums import DataBackend, DataMode, DataStatus
from guanlan_v2.orchestration.refs import ContentRef, SchemaRef
from guanlan_v2.orchestration.runtime_contracts import (
    PHASE2_BASE_REGISTRY_DIGEST,
    ExecutionEvidenceOrdinalToken,
    phase2_runtime_registry,
)

UTC = timezone.utc
_SH = ZoneInfo("Asia/Shanghai")
#: 2026-07-16 07:00Z == 2026-07-16 15:00 Asia/Shanghai (the A-share close) — the
#: run's start-frozen as_of.
AS_OF = datetime(2026, 7, 16, 7, 0, tzinfo=UTC)
_HANDLER = ContentRef(id="data.source.handler", version="1", content_digest="a" * 64)


# --------------------------------------------------------------------------- #
# calendar + schema registry                                                  #
# --------------------------------------------------------------------------- #
def _weekdays_2026() -> tuple[date, ...]:
    out: list[date] = []
    d = date(2026, 1, 1)
    while d <= date(2026, 12, 31):
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return tuple(out)


CALENDAR = build_trading_calendar(
    calendar_id="cn_a_share", sessions=_weekdays_2026(),
    material_id="cal.cn.2026", material_version="1",
)


@pytest.fixture(scope="module")
def schema_registry():
    ph2 = phase2_runtime_registry(PHASE2_BASE_REGISTRY_DIGEST)
    return build_phase3_registry(ph2.registry_digest)


# --------------------------------------------------------------------------- #
# fake clocks, probe stub, params                                             #
# --------------------------------------------------------------------------- #
class _FrozenClock:
    """A frozen authoritative clock — ``now()`` always returns the same instant."""

    def __init__(self, as_of: datetime) -> None:
        self._t = as_of

    def now(self) -> datetime:
        return self._t


class _AdvancingClock:
    """An advancing clock that steps forward on every read (proves the freeze)."""

    def __init__(self, start: datetime, step: timedelta = timedelta(seconds=1)) -> None:
        self._t = start
        self._step = step
        self.reads = 0

    def now(self) -> datetime:
        self.reads += 1
        t = self._t
        self._t = self._t + self._step
        return t


def _pulled(dt: datetime) -> str:
    """A naive Beijing-local ``pulled_at`` string whose UTC equals ``dt``."""
    return dt.astimezone(_SH).strftime("%Y-%m-%dT%H:%M:%S")


def _ok_env(*, items, pulled_at=None, status="ok"):
    return {"ok": True, "status": status, "items": list(items), "n": len(items),
            "error": "", "note": "", "pulled_at": pulled_at}


class _ProbeStub:
    """A fake ``live_client.probe`` returning a controlled envelope (no real vendor)."""

    def __init__(self, env):
        self.env = env
        self.calls: list = []

    def __call__(self, source, code="", date="", limit=20, timeout=90):
        self.calls.append({"source": source, "code": code, "date": date, "limit": limit})
        return self.env


_NEWS_PARAMS = {
    "symbols": ({"code": "600519", "exchange": "SH", "board": "main"},),
    "as_of": AS_OF.isoformat(),
}
_VERIFIED_SNAPSHOT_PARAMS = dict(_NEWS_PARAMS)
_OHLCV_PARAMS = {
    "symbol": {"code": "600519", "exchange": "SH", "board": "main"},
    "start": "2026-07-16T00:00:00+00:00", "end": "2026-07-16T00:00:00+00:00",
}


class _SpyRecorder:
    def __init__(self):
        self.records: list = []

    def record_data_refusal(self, details, evidence, *, idempotency_key):
        self.records.append((details.reason_code, idempotency_key))


# --------------------------------------------------------------------------- #
# a coherent ONLINE frozen world (source registry + routing + capture manifest #
# + context) built from THIS task's builders + one live adapter over a stub    #
# --------------------------------------------------------------------------- #
class _OnlineWorld:
    def __init__(self, schema_registry, *, method_id, params, probe_stub, clock,
                 spy_source=False, resolve_source_fn=None, data_snapshot_id="online-root-A"):
        surface = phase3_data_surface()
        self.spec = surface.spec_by_method[method_id]
        online_desc = build_online_live_descriptor(
            method_specs=surface.method_specs, handler_ref=_HANDLER)
        self.src_ref = ContentRef(id=online_desc.source_id, version=online_desc.source_version,
                                  content_digest=online_desc.descriptor_digest)
        reg = DataSourceRegistry(registry_version="p9-online-v1")
        for s in surface.method_specs:
            reg.register_method(s)
        reg.register_descriptor(online_desc)
        entries = [RouteEntry(source_ref=self.src_ref, capability_ref=self.spec.capability_ref)]
        self.spy_ref = None
        if spy_source:
            spy_desc = DataSourceDescriptor.build(
                source_id="backup.spy", source_version="1",
                method_refs=tuple(s.method_ref for s in surface.method_specs),
                method_capability_refs=online_desc.method_capability_refs,
                supported_modes=(DataMode.ONLINE,),
                supported_backends=(DataBackend.LIVE,),
                handler_ref=_HANDLER,
                source_config_schema_ref=SchemaRef(name="DataSourceConfigSnapshot", version="1"))
            self.spy_ref = ContentRef(id=spy_desc.source_id, version="1",
                                      content_digest=spy_desc.descriptor_digest)
            reg.register_descriptor(spy_desc)
            entries.append(RouteEntry(source_ref=self.spy_ref, capability_ref=self.spec.capability_ref))
        reg.register_route(ResolvedMethodRoute(
            method_ref=self.spec.method_ref, entries=tuple(entries),
            route_policy_ref=ContentRef(id="policy.route.default", version="1",
                                        content_digest="d" * 64)))
        reg.register_freshness(surface.freshness_policy)
        reg.seal()
        self.registry = reg
        self.snapshot = reg.snapshot()
        self.config = build_data_source_config_snapshot(
            config_version="cfg-1", method_selections={}, source_options={})
        self.routing = reg.build_routing_snapshot(
            audit_id="route-1", schema_registry_digest=schema_registry.registry_digest,
            source_config=self.config)
        self.manifest = build_online_capture_manifest(
            data_snapshot_id=data_snapshot_id, as_of=AS_OF, timezone="Asia/Shanghai",
            calendar_id="cn_a_share", routing_snapshot_digest=self.routing.routing_digest,
            schema_registry_digest=schema_registry.registry_digest)
        self.clock = clock
        self.ctx = build_online_data_context(
            clock=clock, source_config=self.config, source_registry=self.snapshot,
            routing=self.routing, manifest=self.manifest)
        self.req = build_data_request(
            self.ctx, method_spec=self.spec, params=params, registry=schema_registry,
            request_id="req-1")
        self.route = reg.default_route(method_id)
        self.scope = DataInvocationScope(
            plan_digest="a" * 64, node_id="pm", worker_id="dec.pm",
            operation_token=_token(1),
            attempt_tokens=tuple(_token(2 + i) for i in range(len(entries))),
            frozen_route=self.route, invocation_mode="always_invoke",
            catalog_digest="b" * 64, schema_registry_digest=schema_registry.registry_digest)
        self.probe_stub = probe_stub
        self.adapter = LiveClientSource(probe_fn=probe_stub, resolve_source_fn=resolve_source_fn)
        self.policy = ResolvedDataMethodPolicy.build(
            method_ref=self.spec.method_ref, freshness_policy=surface.freshness_policy,
            limit_policy=None, calendar_id="cn_a_share",
            calendar_material_ref=CALENDAR.material_ref)

    def guard(self, recorder=None) -> PitGuard:
        return PitGuard.from_context(
            self.ctx, clock=_FrozenClock(AS_OF), calendar=CALENDAR,
            refusal_recorder=recorder or _SpyRecorder())

    def fetch(self) -> RawFetch:
        return self.adapter.fetch(self.req, scope=self.scope)

    def check(self, guard=None, freshness=None):
        raw = self.fetch()
        g = guard or self.guard()
        return g.check_raw(
            raw.candidates, request_digest=self.req.request_digest,
            request_context_digest=content_digest(self.ctx), source_ref=self.src_ref,
            freshness=freshness)


def _token(evidence: int) -> ExecutionEvidenceOrdinalToken:
    return ExecutionEvidenceOrdinalToken(attempt=1, call_ordinal=1, evidence_ordinal=evidence)


# =========================================================================== #
# 1. as_of is frozen at build; the freeze is ENFORCED, not decorative          #
# =========================================================================== #
def test_as_of_frozen_at_build(schema_registry):
    stub = _ProbeStub(_ok_env(items=[]))
    clock = _AdvancingClock(AS_OF)
    world = _OnlineWorld(schema_registry, method_id="news", params=_NEWS_PARAMS,
                         probe_stub=stub, clock=clock)
    # build_online_data_context read the advancing clock EXACTLY once.
    assert clock.reads == 1
    assert world.ctx.as_of == AS_OF
    # advancing the clock twice afterwards never changes the frozen context as_of.
    clock.now(); clock.now()
    assert world.ctx.as_of == AS_OF
    assert clock.reads == 3

    # FREEZE ENFORCED: a row whose live pulled_at is AFTER run start is refused.
    future_pulled = _pulled(AS_OF + timedelta(minutes=5))
    stub.env = _ok_env(items=[{"title": "late tick"}], pulled_at=future_pulled)
    with pytest.raises(FutureDataRefused) as exc:
        world.check()
    assert exc.value.future_rows == 1

    # a row pulled AT/BEFORE run start passes — the freeze is not "refuse everything".
    stub.env = _ok_env(items=[{"title": "on time"}], pulled_at=_pulled(AS_OF))
    passed, audit = world.check()
    assert len(passed) == 1 and audit.guard_result == "passed"


# =========================================================================== #
# 2. an ok:False (caller/mechanical) envelope -> a typed error, no fabrication  #
# =========================================================================== #
def test_probe_ok_false_is_typed_error(schema_registry):
    stub = _ProbeStub({"ok": False, "note": "bad code", "items": []})
    world = _OnlineWorld(schema_registry, method_id="news", params=_NEWS_PARAMS,
                         probe_stub=stub, clock=_FrozenClock(AS_OF))
    with pytest.raises(RoutingConfigurationError):
        world.fetch()  # a typed caller error; no DataResult / row fabricated


# =========================================================================== #
# 3. an ok:True status:planned envelope -> honest UNAVAILABLE (zero rows)       #
# =========================================================================== #
def test_probe_planned_maps_unavailable(schema_registry):
    stub = _ProbeStub(_ok_env(items=[], pulled_at=_pulled(AS_OF), status="planned"))
    world = _OnlineWorld(schema_registry, method_id="news", params=_NEWS_PARAMS,
                         probe_stub=stub, clock=_FrozenClock(AS_OF))
    # (a) at the adapter boundary: planned = the vendor probe is not implemented/
    # available -> NotConfiguredError (the single taxonomy error that advances the
    # frozen chain; never a fabricated row).
    with pytest.raises(NotConfiguredError):
        world.fetch()

    # (b) end-to-end through the REAL dispatch: a single-vendor OPTIONAL method
    # (news) exhausts to an honest UNAVAILABLE DataResult, zero rows, visible reason.
    sink = _audit_sink()
    gateway = _FallbackGateway(sources={world.src_ref.id: world.adapter.fetch}, sink=sink)
    outcome = dispatch(
        world.req, invocation_scope=world.scope, ctx=world.ctx, routing=world.routing,
        manifest=world.manifest, registry=world.registry, schema_registry=schema_registry,
        resolved_policy=world.policy, gateway=gateway, evidence_writer=_FakeWriter(),
        payload_reader=None, refusal_audit_sink=sink, cache=_NullCache(),
        clock=_FrozenClock(AS_OF), calendar=CALENDAR)
    assert outcome.result.status is DataStatus.UNAVAILABLE
    assert outcome.result.data is None                       # zero rows, no fabrication
    assert any(a.fallback_reason == "not_configured" for a in outcome.result.attempts)


# =========================================================================== #
# 4. an ok:True status:error surfaces loud and never swaps to the next vendor   #
# =========================================================================== #
def test_upstream_error_visible_not_swapped(schema_registry):
    stub = _ProbeStub(_ok_env(items=[], pulled_at=_pulled(AS_OF), status="error"))
    stub.env["error"] = "upstream 500"
    world = _OnlineWorld(schema_registry, method_id="news", params=_NEWS_PARAMS,
                         probe_stub=stub, clock=_FrozenClock(AS_OF), spy_source=True)
    spy = _SpySource()
    sink = _audit_sink()
    gateway = _FallbackGateway(
        sources={world.src_ref.id: world.adapter.fetch, world.spy_ref.id: spy}, sink=sink)
    with pytest.raises(SourceBrokenError):
        dispatch(
            world.req, invocation_scope=world.scope, ctx=world.ctx, routing=world.routing,
            manifest=world.manifest, registry=world.registry, schema_registry=schema_registry,
            resolved_policy=world.policy, gateway=gateway, evidence_writer=_FakeWriter(),
            payload_reader=None, refusal_audit_sink=sink, cache=_NullCache(),
            clock=_FrozenClock(AS_OF), calendar=CALENDAR)
    assert spy.calls == 0                                    # backup never invoked (no swap)
    assert any(rc == "source_error" for rc, _ in gateway.rejects)


# =========================================================================== #
# 5. a data row with no envelope pulled_at -> MissingAvailabilityRefused        #
# =========================================================================== #
def test_missing_pulled_at_refused(schema_registry):
    # ok:True with a real row but NO pulled_at -> available_at=None -> the guard
    # refuses (ONLINE records availability honestly; never a fabricated available_at).
    stub = _ProbeStub({"ok": True, "status": "ok", "items": [{"title": "live headline"}],
                       "n": 1, "error": "", "note": ""})  # no pulled_at key
    world = _OnlineWorld(schema_registry, method_id="news", params=_NEWS_PARAMS,
                         probe_stub=stub, clock=_FrozenClock(AS_OF))
    cands = world.fetch().candidates
    assert len(cands) == 1 and cands[0].available_at is None
    with pytest.raises(MissingAvailabilityRefused):
        world.check()


# =========================================================================== #
# 6. no order/signal write method; every bound method ref is read-only+existing #
# =========================================================================== #
def test_no_write_methods(schema_registry):
    source = LiveClientSource()
    # (a) no method name suggests an order/signal write actuator (red line §10).
    banned = ("order", "signal", "trade", "submit", "write", "execute",
              "buy", "sell", "place", "cancel")
    for name, _member in inspect.getmembers(source, predicate=callable):
        if name.startswith("__"):
            continue
        low = name.lower()
        assert not any(b in low for b in banned), \
            f"LiveClientSource exposes a write-shaped method: {name}"

    # (b) the descriptor binds ONLY existing, read-only Phase-3 method refs (no new id).
    surface = phase3_data_surface()
    desc = build_online_live_descriptor(method_specs=surface.method_specs, handler_ref=_HANDLER)
    existing = {s.method_ref for s in surface.method_specs}
    assert set(desc.method_refs) <= existing              # no new DataMethodSpec id minted
    bound = {s.method_id for s in surface.method_specs if s.method_ref in desc.method_refs}
    assert bound == {"verified_snapshot", "news", "ohlcv"}
    assert "signals" not in bound                          # no signal method is bound
    for s in surface.method_specs:
        if s.method_id in bound:
            assert s.read_only is True                     # every bound method read-only


# =========================================================================== #
# 7. an unknown source fails loud; a raising resolve_source is never swallowed  #
# =========================================================================== #
def test_unknown_source_fails_loud(schema_registry):
    # (a) a resolve_source that RAISES propagates unmasked (no silent substitute).
    def _raising(source):
        raise RuntimeError(f"resolve blew up for {source!r}")

    world = _OnlineWorld(schema_registry, method_id="news", params=_NEWS_PARAMS,
                         probe_stub=_ProbeStub(_ok_env(items=[])), clock=_FrozenClock(AS_OF),
                         resolve_source_fn=_raising)
    with pytest.raises(RuntimeError):
        world.fetch()
    assert world.probe_stub.calls == []                    # never reached the probe

    # (b) an unknown source id ('' from resolve_source) fails loud, not silently.
    world2 = _OnlineWorld(schema_registry, method_id="news", params=_NEWS_PARAMS,
                          probe_stub=_ProbeStub(_ok_env(items=[])), clock=_FrozenClock(AS_OF),
                          resolve_source_fn=lambda s: "")
    with pytest.raises(RoutingConfigurationError):
        world2.fetch()
    assert world2.probe_stub.calls == []


# =========================================================================== #
# 8. no hardcoded source count anywhere (枚举钉旧31 gotcha)                      #
# =========================================================================== #
def test_no_hardcoded_source_count():
    from guanlan_v2.datafeed.live_client import known_sources

    src = inspect.getsource(live_data)
    # the source list is resolved DYNAMICALLY from the facade at call time.
    assert "resolve_source" in src and "known_sources" in src
    # no comparison pins a source-list length to an integer literal.
    assert re.search(r"len\([^)]*known_sources[^)]*\)\s*[=!<>]=\s*\d", src) is None
    assert re.search(r"len\([^)]*_known\(\)[^)]*\)\s*[=!<>]=\s*\d", src) is None
    # the adapter derives its bound live-source list from known_sources() at call time.
    ids = LiveClientSource().live_source_ids()
    assert set(ids) <= set(known_sources())


# =========================================================================== #
# end-to-end dispatch collaborators (fakes; mirror the reviewed Task-6 ABI)     #
# =========================================================================== #
class _SpySource:
    def __init__(self):
        self.calls = 0

    def __call__(self, request, *, scope):  # pragma: no cover - must never be reached
        self.calls += 1
        return build_raw_fetch(
            request_digest=request.request_digest,
            source_ref=scope.frozen_route.entries[-1].source_ref,
            capability_ref=scope.frozen_route.entries[-1].capability_ref,
            candidates=(), subsource="spy", fetched_at=AS_OF, provider_request_id=None)


class _Pending:
    def __init__(self, token, source_ref, capability_ref, idem):
        self.token = token
        self.source_ref = source_ref
        self.capability_ref = capability_ref
        self.idem = idem


class _FallbackGateway:
    def __init__(self, sources, sink):
        self._sources = sources
        self._sink = sink
        self.rejects: list = []

    def begin(self, *, attempt_token, source_ref, capability_ref, request_schema_ref,
              result_schema_ref, idempotency_key):
        return _Pending(attempt_token, source_ref, capability_ref, idempotency_key)

    def invoke(self, pending, request, *, scope):
        return self._sources[pending.source_ref.id](request, scope=scope)

    def finalize_success(self, pending, *, request_ref, result_ref, request_digest,
                         result_digest):  # pragma: no cover
        raise AssertionError("a planned/error/refusal outcome must not finalize a success")

    def reject(self, pending, *, detail_schema_ref, detail_payload, reason_code,
               idempotency_key):
        self.rejects.append((reason_code, pending.source_ref.id))
        return self._sink.record(
            detail_schema_ref=detail_schema_ref, detail_payload=detail_payload,
            reason_code=reason_code, idempotency_key=idempotency_key,
            attempted_capability_ref=pending.capability_ref)


class _FakeWriter:
    def __init__(self):
        self._seq = 0

    def put(self, *, token, role, schema_ref, payload, idempotency_key):
        from guanlan_v2.orchestration.refs import PayloadRef, TypedPayloadRef
        self._seq += 1
        cd = getattr(payload, "content_digest", None) or getattr(
            payload, "request_digest", None) or content_digest(payload)
        return TypedPayloadRef(schema_ref=schema_ref, payload_ref=PayloadRef(
            namespace="main", object_id=f"obj-{self._seq}", content_digest=cd))

    def record_existing(self, *, token, role, typed_ref, idempotency_key):  # pragma: no cover
        return typed_ref


class _NullCache:
    def get_verified(self, key, *, ctx, manifest, registry, payload_store):  # pragma: no cover
        return None


def _audit_sink():
    from guanlan_v2.orchestration.eventstore import EventRefusalAuditSink
    from guanlan_v2.orchestration.data.pit import DataFetchRefusalDetails
    from guanlan_v2.orchestration.runtime_contracts import (
        AuditDetailRegistry, GenericRefusalDetails, NamedEvidenceDigest)
    reg = AuditDetailRegistry()
    reg.register(GenericRefusalDetails)
    reg.register(NamedEvidenceDigest)
    reg.register(DataFetchRefusalDetails)
    reg.seal()
    return EventRefusalAuditSink(detail_registry=reg, clock=_FrozenClock(AS_OF))


# =========================================================================== #
# extra: the ONLINE DataContext is coherent (ONLINE/LIVE, no vintage, frozen)   #
# =========================================================================== #
def test_online_context_builder_coherence(schema_registry):
    world = _OnlineWorld(schema_registry, method_id="news", params=_NEWS_PARAMS,
                         probe_stub=_ProbeStub(_ok_env(items=[])), clock=_FrozenClock(AS_OF))
    ctx = world.ctx
    assert isinstance(ctx, DataContext)
    assert ctx.as_of == AS_OF == ctx.clock.as_of
    assert ctx.calendar_id == ctx.clock.calendar_id == "cn_a_share"
    assert ctx.mode is DataMode.ONLINE
    assert ctx.backend is DataBackend.LIVE
    assert ctx.strict_pit is False
    # ONLINE omits a vintage manifest digest while carrying the capture-root digest.
    assert ctx.vintage_manifest_digest is None
    assert ctx.data_snapshot_content_digest == world.manifest.content_digest
    assert world.manifest.manifest_kind == "online_capture_root"
    assert world.manifest.mode is DataMode.ONLINE


# =========================================================================== #
# extra: the descriptor is ONLINE-only / LIVE-backend over existing method ids  #
# =========================================================================== #
def test_online_descriptor_online_only():
    surface = phase3_data_surface()
    desc = build_online_live_descriptor(method_specs=surface.method_specs, handler_ref=_HANDLER)
    assert isinstance(desc, DataSourceDescriptor)
    assert desc.source_id == ONLINE_SOURCE_ID
    assert desc.supported_modes == (DataMode.ONLINE,)
    assert desc.supported_backends == (DataBackend.LIVE,)
    bound = {s.method_id for s in surface.method_specs if s.method_ref in desc.method_refs}
    assert bound == {"verified_snapshot", "news", "ohlcv"}


# =========================================================================== #
# extra: a PIT_REPLAY routing snapshot can NEVER cross-select the ONLINE source #
# =========================================================================== #
def test_online_never_cross_selected_by_pit_replay():
    surface = phase3_data_surface()
    online_desc = build_online_live_descriptor(method_specs=surface.method_specs, handler_ref=_HANDLER)
    pit_desc = build_pit_replay_descriptor(method_specs=surface.method_specs, handler_ref=_HANDLER)
    assert DataMode.PIT_REPLAY not in online_desc.supported_modes

    reg = DataSourceRegistry(registry_version="p9-mode-exclusion")
    for s in surface.method_specs:
        reg.register_method(s)
    reg.register_descriptor(online_desc)
    reg.register_descriptor(pit_desc)
    reg.seal()
    snap = reg.snapshot()

    # both descriptors carry news/ohlcv, but a PIT_REPLAY read resolves ONLY the
    # PIT_REPLAY source — the ONLINE live source is structurally excluded.
    for method_id in ("news", "ohlcv"):
        mref = surface.spec_by_method[method_id].method_ref
        ids = {r.id for r in pit_replay_source_refs(snap, method_ref=mref)}
        assert ids == {PIT_REPLAY_SOURCE_ID}
        assert ONLINE_SOURCE_ID not in ids


# =========================================================================== #
# extra: no adapter method accepts a caller time / strict override             #
# =========================================================================== #
def test_no_caller_time_override():
    banned = {"as_of", "strict", "strict_pit", "now"}
    source = LiveClientSource()
    for name, member in inspect.getmembers(source, predicate=callable):
        if name.startswith("__"):
            continue
        params = set(inspect.signature(member).parameters)
        assert not (params & banned), f"{name} exposes a caller time override: {params & banned}"


# =========================================================================== #
# extra: the online capture manifest is audit/semantic split (root is audit)    #
# =========================================================================== #
def test_online_capture_manifest_kind_and_audit_split(schema_registry):
    common = dict(
        as_of=AS_OF, timezone="Asia/Shanghai", calendar_id="cn_a_share",
        routing_snapshot_digest="e" * 64, schema_registry_digest="f" * 64)
    m1 = build_online_capture_manifest(data_snapshot_id="online-root-A", **common)
    m2 = build_online_capture_manifest(data_snapshot_id="online-root-B", **common)
    assert isinstance(m1, DataSnapshotManifest)
    assert m1.manifest_kind == "online_capture_root"
    assert m1.mode is DataMode.ONLINE
    # semantic identity is stable across a physical capture-root relocation ...
    assert m1.content_digest == m2.content_digest
    # ... but audit (dereference) identity differs.
    assert m1.audit_digest_value() != m2.audit_digest_value()
    assert m1.data_snapshot_id != m2.data_snapshot_id
