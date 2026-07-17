# -*- coding: utf-8 -*-
"""Phase 3 · Task 6 — the sealed ``DataSourceRegistry`` + narrow-fallback ``dispatch``.

Written test-first. Locks the routing heart of the data plane: the error taxonomy's
three disjoint buckets become behavior in :func:`dispatch`, and the sealed registry
freezes to one immutable snapshot/manifest whose digests are registration-order
independent.

The router is exercised against strict fakes (a fake ``CapabilityGateway`` / evidence
writer / cache) exactly as the brief prescribes; a *real*
:class:`~guanlan_v2.orchestration.eventstore.EventRefusalAuditSink` (over a test
audit-detail registry) proves every refusal path persists an idempotent, typed audit
before re-raising.

Run: ``pytest tests/orchestration/data/test_registry.py -v``
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from guanlan_v2.orchestration.data.calendar import build_trading_calendar
from guanlan_v2.orchestration.data.catalog import phase3_data_surface
from guanlan_v2.orchestration.data.errors import (
    DataIntegrityError,
    FutureDataRefused,
    MissingAvailabilityRefused,
    NoDataError,
    NotConfiguredError,
    RateLimitError,
    RoutingConfigurationError,
    SnapshotMismatchError,
    SourceBrokenError,
    StaleDataError,
)
from guanlan_v2.orchestration.data.pit import (
    DataFetchRefusalDetails,
    RawRowCandidate,
)
from guanlan_v2.orchestration.data.registry import (
    DataSourceRegistry,
    RegistryConflictError,
    SealedRegistryError,
    dispatch,
)
from guanlan_v2.orchestration.data.schema_registry import build_phase3_registry
from guanlan_v2.orchestration.data.snapshot import (
    build_data_context,
    build_data_snapshot_manifest,
    build_data_source_config_snapshot,
)
from guanlan_v2.orchestration.data.source import (
    DataInvocationScope,
    DataSourceDescriptor,
    ResolvedDataMethodPolicy,
    ResolvedMethodRoute,
    RouteEntry,
    VerifiedDataCacheHit,
    build_data_request,
    build_raw_fetch,
)
from guanlan_v2.orchestration.digest import content_digest
from guanlan_v2.orchestration.enums import DataBackend, DataMode, DataStatus
from guanlan_v2.orchestration.refs import (
    ContentRef,
    PayloadRef,
    SchemaRef,
    TypedPayloadRef,
)
from guanlan_v2.orchestration.runtime_contracts import (
    PHASE2_BASE_REGISTRY_DIGEST,
    AuditDetailRegistry,
    ExecutionEvidenceOrdinalToken,
    GenericRefusalDetails,
    NamedEvidenceDigest,
    phase2_runtime_registry,
)
from guanlan_v2.orchestration.eventstore import EventRefusalAuditSink
from guanlan_v2.orchestration.schemas import ToolCallRecord

UTC = timezone.utc
AS_OF = datetime(2026, 7, 16, 7, 0, tzinfo=UTC)
GOLDEN_PATH = Path(__file__).resolve().parent.parent / "golden" / "data_source_manifest_v1.json"
FALLBACK_USED = "FALLBACK_USED"

_SERIES_PARAMS = {
    "symbol": {"code": "600519", "exchange": "SH", "board": "main"},
    "start": "2026-07-01T00:00:00+00:00",
    "end": "2026-07-15T00:00:00+00:00",
}


# --------------------------------------------------------------------------- #
# fixed clock + trading calendar                                              #
# --------------------------------------------------------------------------- #
class FixedClock:
    def __init__(self, now: datetime) -> None:
        self._now = now

    def now(self) -> datetime:
        return self._now


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


# --------------------------------------------------------------------------- #
# schema registry + the two-source golden registry builder                    #
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="module")
def schema_registry():
    ph2 = phase2_runtime_registry(PHASE2_BASE_REGISTRY_DIGEST)
    return build_phase3_registry(ph2.registry_digest)


_PRIMARY = "primary.vendor"
_BACKUP = "backup.vendor"
_HANDLER = ContentRef(id="data.source.handler", version="1", content_digest="a" * 64)


def _descriptor(source_id: str, surface) -> DataSourceDescriptor:
    return DataSourceDescriptor.build(
        source_id=source_id, source_version="1",
        method_refs=tuple(s.method_ref for s in surface.method_specs),
        method_capability_refs=tuple(
            sorted(surface.capability_refs.values(), key=lambda c: (c.id, c.version))),
        supported_modes=(DataMode.ONLINE, DataMode.PIT_REPLAY),
        supported_backends=(DataBackend.CACHE, DataBackend.LIVE, DataBackend.PIT_STORE),
        handler_ref=_HANDLER,
        source_config_schema_ref=SchemaRef(name="DataSourceConfigSnapshot", version="1"),
    )


def _source_ref(desc: DataSourceDescriptor) -> ContentRef:
    return ContentRef(id=desc.source_id, version=desc.source_version,
                      content_digest=desc.descriptor_digest)


def build_golden_registry(*, reverse: bool = False) -> DataSourceRegistry:
    """The canonical two-source registry (primary→backup fallback for every method).

    Registration order is varied by ``reverse`` to prove order-independence.
    """
    surface = phase3_data_surface()
    primary = _descriptor(_PRIMARY, surface)
    backup = _descriptor(_BACKUP, surface)
    p_ref, b_ref = _source_ref(primary), _source_ref(backup)
    reg = DataSourceRegistry(registry_version="phase3-source-v1")
    specs = list(surface.method_specs)
    descriptors = [primary, backup]
    if reverse:
        specs = list(reversed(specs))
        descriptors = list(reversed(descriptors))
    for spec in specs:
        reg.register_method(spec)
    for desc in descriptors:
        reg.register_descriptor(desc)
    routes = []
    for spec in surface.method_specs:
        routes.append(ResolvedMethodRoute(
            method_ref=spec.method_ref,
            entries=(
                RouteEntry(source_ref=p_ref, capability_ref=spec.capability_ref),
                RouteEntry(source_ref=b_ref, capability_ref=spec.capability_ref),
            ),
            route_policy_ref=ContentRef(id="policy.route.default", version="1",
                                        content_digest="d" * 64),
        ))
    if reverse:
        routes = list(reversed(routes))
    for route in routes:
        reg.register_route(route)
    reg.register_freshness(surface.freshness_policy)
    return reg.seal()


# --------------------------------------------------------------------------- #
# frozen-set fixtures (config / routing / manifest / context / request)        #
# --------------------------------------------------------------------------- #
class _World:
    """A fully-built, coherent frozen set for one method + its invocation scope."""

    def __init__(self, schema_registry, *, method_id="ohlcv", mode="cache_or_invoke"):
        self.surface = phase3_data_surface()
        self.registry = build_golden_registry()
        self.snapshot = self.registry.snapshot()
        self.spec = self.registry.method_spec(method_id)
        self.schema_registry = schema_registry
        self.config = build_data_source_config_snapshot(
            config_version="cfg-1", method_selections={}, source_options={})
        self.routing = self.registry.build_routing_snapshot(
            audit_id="route-1", schema_registry_digest=schema_registry.registry_digest,
            source_config=self.config)
        self.manifest = build_data_snapshot_manifest(
            data_snapshot_id="snap-1", manifest_kind="online_capture_root", as_of=AS_OF,
            mode=DataMode.ONLINE, timezone="Asia/Shanghai", calendar_id="cn_a_share",
            routing_snapshot_digest=self.routing.routing_digest,
            schema_registry_digest=schema_registry.registry_digest, entries=())
        self.ctx = build_data_context(
            FixedClock(AS_OF), mode=DataMode.ONLINE, backend=DataBackend.LIVE,
            source_config=self.config, source_registry=self.snapshot, routing=self.routing,
            manifest=self.manifest)
        params = dict(_SERIES_PARAMS) if method_id in ("ohlcv", "indicators") else {
            "as_of": "2026-07-16T07:00:00+00:00"}
        self.req = build_data_request(
            self.ctx, method_spec=self.spec, params=params, registry=schema_registry,
            request_id="req-1")
        self.route = self.registry.default_route(method_id)
        self.scope = DataInvocationScope(
            plan_digest="a" * 64, node_id="pm", worker_id="dec.pm",
            operation_token=_token(1), attempt_tokens=(_token(2), _token(3)),
            frozen_route=self.route, invocation_mode=mode,
            catalog_digest="b" * 64, schema_registry_digest=schema_registry.registry_digest)
        self.policy = ResolvedDataMethodPolicy.build(
            method_ref=self.spec.method_ref, freshness_policy=self.surface.freshness_policy,
            limit_policy=None, calendar_id="cn_a_share",
            calendar_material_ref=CALENDAR.material_ref)

    def source_ref(self, source_id: str) -> ContentRef:
        for entry in self.route.entries:
            if entry.source_ref.id == source_id:
                return entry.source_ref
        raise KeyError(source_id)


def _token(evidence: int) -> ExecutionEvidenceOrdinalToken:
    return ExecutionEvidenceOrdinalToken(attempt=1, call_ordinal=1, evidence_ordinal=evidence)


# --------------------------------------------------------------------------- #
# candidates + raw fetches                                                     #
# --------------------------------------------------------------------------- #
def _cand(*, available_at: datetime | None, close=1.5) -> RawRowCandidate:
    return RawRowCandidate.build(
        raw_payload={
            "symbol": {"code": "600519", "exchange": "SH", "board": "main"},
            "open": 1.0, "high": 2.0, "low": 0.5, "close": close, "volume": 100,
        },
        effective_at=datetime(2026, 7, 1, tzinfo=UTC),
        available_at=available_at,
        ingested_at=datetime(2026, 7, 2, tzinfo=UTC),
    )


def _raw(world: _World, source_id: str, candidates) -> object:
    src = world.source_ref(source_id)
    return build_raw_fetch(
        request_digest=world.req.request_digest, source_ref=src,
        capability_ref=world.spec.capability_ref, candidates=tuple(candidates),
        subsource="main", fetched_at=AS_OF, provider_request_id="p-1")


# --------------------------------------------------------------------------- #
# fakes: audit sink, gateway, evidence writer, cache                          #
# --------------------------------------------------------------------------- #
def _audit_sink() -> EventRefusalAuditSink:
    reg = AuditDetailRegistry()
    reg.register(GenericRefusalDetails)
    reg.register(NamedEvidenceDigest)
    reg.register(DataFetchRefusalDetails)  # test-local; never a golden/main registry
    reg.seal()
    return EventRefusalAuditSink(detail_registry=reg, clock=FixedClock(AS_OF))


class _Pending:
    def __init__(self, token, source_ref, capability_ref, result_schema_ref, idem):
        self.token = token
        self.source_ref = source_ref
        self.capability_ref = capability_ref
        self.result_schema_ref = result_schema_ref
        self.idem = idem


class FakeGateway:
    """A strict fake ``CapabilityGateway`` — the router never calls a handler directly."""

    def __init__(self, *, sources: dict, not_configured=(), sink=None):
        self._sources = sources
        self._not_configured = set(not_configured)
        self._sink = sink or _audit_sink()
        self.begun: list[str] = []
        self.terminal: dict[str, str] = {}
        self.records: list[ToolCallRecord] = []
        self.rejects: list[tuple] = []
        self._pending: dict[str, _Pending] = {}

    def begin(self, *, attempt_token, source_ref, capability_ref, request_schema_ref,
              result_schema_ref, idempotency_key):
        self.begun.append(source_ref.id)
        if source_ref.id in self._not_configured:
            self._sink.record(
                detail_schema_ref=SchemaRef(name="GenericRefusalDetails", version="1"),
                detail_payload=GenericRefusalDetails(summary=f"not_configured {source_ref.id}",
                                                     category="capability_gateway"),
                reason_code="not_configured", idempotency_key=f"{idempotency_key}:begin",
                attempted_capability_ref=capability_ref)
            raise NotConfiguredError(f"{source_ref.id} is not configured")
        pending = _Pending(attempt_token, source_ref, capability_ref, result_schema_ref,
                           idempotency_key)
        self._pending[idempotency_key] = pending
        return pending

    def invoke(self, pending, request, *, scope):
        backend = self._sources[pending.source_ref.id]
        return backend(request, scope)

    def finalize_success(self, pending, *, request_ref, result_ref, request_digest,
                         result_digest):
        assert request_ref.payload_ref.namespace == "main"
        assert result_ref.payload_ref.namespace == "main"
        assert result_ref.payload_ref.content_digest == result_digest
        if self.terminal.get(pending.idem) == "reject":
            raise AssertionError("finalize_success after reject")
        self.terminal[pending.idem] = "success"
        record = ToolCallRecord(
            call_ordinal=pending.token.call_ordinal, tool_ref=pending.capability_ref,
            request_ref=request_ref, result_ref=result_ref,
            call_id=f"pm:call-{pending.token.call_ordinal}", started_at=AS_OF, finished_at=AS_OF)
        self.records.append(record)
        return record

    def reject(self, pending, *, detail_schema_ref, detail_payload, reason_code,
               idempotency_key):
        if self.terminal.get(pending.idem) == "success":
            raise AssertionError("reject after finalize_success")
        rec = self._sink.record(
            detail_schema_ref=detail_schema_ref, detail_payload=detail_payload,
            reason_code=reason_code, idempotency_key=idempotency_key,
            attempted_capability_ref=pending.capability_ref)
        self.terminal[pending.idem] = "reject"
        self.rejects.append((reason_code, pending.source_ref.id))
        return rec

    @property
    def sink_records(self):
        return self._sink.records()


def _digest_of(payload) -> str:
    cd = getattr(payload, "content_digest", None)
    if cd is not None:
        return cd
    rd = getattr(payload, "request_digest", None)
    if rd is not None:
        return rd
    return content_digest(payload)


class FakeEvidenceWriter:
    """Fixed-main evidence writer fake — returns real main-namespace typed refs."""

    def __init__(self):
        self.puts: list[tuple] = []
        self.existing: list[tuple] = []
        self._seq = 0

    def put(self, *, token, role, schema_ref, payload, idempotency_key):
        self._seq += 1
        ref = TypedPayloadRef(
            schema_ref=schema_ref,
            payload_ref=PayloadRef(namespace="main", object_id=f"obj-{self._seq}",
                                   content_digest=_digest_of(payload)))
        self.puts.append((role, schema_ref.key, ref.payload_ref.content_digest, token))
        return ref

    def record_existing(self, *, token, role, typed_ref, idempotency_key):
        self.existing.append((role, typed_ref, token))
        return typed_ref

    def role_count(self, role: str) -> int:
        return sum(1 for r, *_ in self.puts if r == role)


class FakeCache:
    def __init__(self, hit: VerifiedDataCacheHit | None = None):
        self._hit = hit
        self.calls = 0

    def get_verified(self, key, *, ctx, manifest, registry, payload_store):
        self.calls += 1
        return self._hit


def _run(world: _World, gateway, *, cache=None, calendar=CALENDAR, sink=None):
    return dispatch(
        world.req, invocation_scope=world.scope, ctx=world.ctx, routing=world.routing,
        manifest=world.manifest, registry=world.registry,
        schema_registry=world.schema_registry, resolved_policy=world.policy,
        gateway=gateway, evidence_writer=FakeEvidenceWriter(),
        payload_reader=None, refusal_audit_sink=sink or gateway._sink,
        cache=cache or FakeCache(), clock=FixedClock(AS_OF), calendar=calendar,
    )


# =========================================================================== #
# A. sealed registry: idempotency / conflict / post-seal / order independence  #
# =========================================================================== #
def test_seal_freezes_and_reverse_order_gives_same_digest(schema_registry):
    forward = build_golden_registry()
    reverse = build_golden_registry(reverse=True)
    assert forward.snapshot().source_registry_digest == reverse.snapshot().source_registry_digest
    assert forward.manifest() == reverse.manifest()
    # the explicit default route is identical regardless of registration order.
    assert forward.default_route("ohlcv").entries == reverse.default_route("ohlcv").entries


def test_identical_redeclaration_is_idempotent():
    surface = phase3_data_surface()
    reg = DataSourceRegistry(registry_version="v")
    spec = surface.spec_by_method["ohlcv"]
    reg.register_method(spec).register_method(spec)  # idempotent
    desc = _descriptor(_PRIMARY, surface)
    reg.register_descriptor(desc).register_descriptor(desc)
    reg.seal()
    assert len(reg.snapshot().method_specs) == 1
    assert len(reg.snapshot().source_descriptors) == 1


def test_conflicting_redeclaration_fails():
    surface = phase3_data_surface()
    reg = DataSourceRegistry(registry_version="v")
    spec = surface.spec_by_method["ohlcv"]
    reg.register_method(spec)
    conflicting = surface.spec_by_method["ohlcv"].model_copy(
        update={"spec_digest": "0" * 64})
    with pytest.raises(RegistryConflictError):
        reg.register_method(conflicting)


def test_every_mutation_after_seal_fails():
    reg = build_golden_registry()
    surface = phase3_data_surface()
    with pytest.raises(SealedRegistryError):
        reg.register_method(surface.spec_by_method["ohlcv"])
    with pytest.raises(SealedRegistryError):
        reg.register_descriptor(_descriptor("x.vendor", surface))
    with pytest.raises(SealedRegistryError):
        reg.register_route(reg.default_route("ohlcv"))


def test_unknown_route_source_is_a_freeze_time_error():
    surface = phase3_data_surface()
    reg = DataSourceRegistry(registry_version="v")
    spec = surface.spec_by_method["ohlcv"]
    reg.register_method(spec)
    reg.register_descriptor(_descriptor(_PRIMARY, surface))
    reg.register_route(ResolvedMethodRoute(
        method_ref=spec.method_ref,
        entries=(RouteEntry(
            source_ref=ContentRef(id="ghost.vendor", version="1", content_digest="9" * 64),
            capability_ref=spec.capability_ref),),
        route_policy_ref=ContentRef(id="policy.route.default", version="1",
                                    content_digest="d" * 64)))
    with pytest.raises(RoutingConfigurationError):
        reg.seal()


def test_snapshot_before_seal_fails():
    reg = DataSourceRegistry(registry_version="v")
    with pytest.raises(SealedRegistryError):
        reg.snapshot()


# =========================================================================== #
# B. bucket 1 — narrow fallback (RateLimit / NotConfigured advance)            #
# =========================================================================== #
def test_first_source_success(schema_registry):
    world = _World(schema_registry)
    gw = FakeGateway(sources={_PRIMARY: lambda r, s: _raw(world, _PRIMARY, [_cand(
        available_at=AS_OF - timedelta(hours=1))])})
    writer = FakeEvidenceWriter()
    outcome = dispatch(
        world.req, invocation_scope=world.scope, ctx=world.ctx, routing=world.routing,
        manifest=world.manifest, registry=world.registry, schema_registry=schema_registry,
        resolved_policy=world.policy, gateway=gw, evidence_writer=writer, payload_reader=None,
        refusal_audit_sink=gw._sink, cache=FakeCache(), clock=FixedClock(AS_OF),
        calendar=CALENDAR)
    assert outcome.result.status is DataStatus.OK
    assert outcome.tool_call_record is not None
    assert outcome.result_token == world.scope.attempt_tokens[0]
    assert gw.begun == [_PRIMARY]
    assert writer.role_count("request") == 1
    assert writer.role_count("result") == 1
    assert len(gw.records) == 1
    # the winning result carries the full (single-attempt) history.
    assert len(outcome.result.attempts) == 1
    assert outcome.result.attempts[0].vendor == _PRIMARY
    assert FALLBACK_USED not in outcome.result.badges


def test_rate_limit_falls_through(schema_registry):
    world = _World(schema_registry)

    def primary(r, s):
        raise RateLimitError("throttled")

    gw = FakeGateway(sources={
        _PRIMARY: primary,
        _BACKUP: lambda r, s: _raw(world, _BACKUP, [_cand(available_at=AS_OF - timedelta(hours=1))]),
    })
    outcome = _run(world, gw)
    assert outcome.result.status is DataStatus.OK
    assert gw.begun == [_PRIMARY, _BACKUP]  # attempt order preserved
    assert outcome.result_token == world.scope.attempt_tokens[1]  # second token wins
    assert gw.records[0].result_ref == outcome.result_ref
    assert FALLBACK_USED in outcome.result.badges
    assert [a.outcome for a in outcome.result.attempts] == ["rate_limited", "success"]
    assert gw.rejects == [("rate_limited", _PRIMARY)]


def test_not_configured_falls_through(schema_registry):
    world = _World(schema_registry)
    gw = FakeGateway(
        sources={_BACKUP: lambda r, s: _raw(world, _BACKUP, [_cand(
            available_at=AS_OF - timedelta(hours=1))])},
        not_configured={_PRIMARY})
    outcome = _run(world, gw)
    assert outcome.result.status is DataStatus.OK
    assert gw.begun == [_PRIMARY, _BACKUP]
    assert outcome.result_token == world.scope.attempt_tokens[1]
    assert [a.outcome for a in outcome.result.attempts] == ["not_configured", "success"]
    assert outcome.result.attempts[0].configured is False


# =========================================================================== #
# C. bucket 2 — typed terminals (NO_DATA / STALE stop the chain)               #
# =========================================================================== #
def test_no_data_stops_chain(schema_registry):
    world = _World(schema_registry)
    gw = FakeGateway(sources={
        _PRIMARY: lambda r, s: _raw(world, _PRIMARY, []),  # empty → NO_DATA
        _BACKUP: lambda r, s: pytest.fail("must not fall back on NO_DATA"),
    })
    outcome = _run(world, gw)
    assert outcome.result.status is DataStatus.NO_DATA
    assert outcome.result.data is None
    assert gw.begun == [_PRIMARY]  # no second vendor
    assert outcome.tool_call_record is not None  # a real "nothing here" answer


def test_stale_stops_chain(schema_registry):
    world = _World(schema_registry)
    gw = FakeGateway(sources={
        _PRIMARY: lambda r, s: _raw(world, _PRIMARY, [_cand(
            available_at=AS_OF - timedelta(days=2))]),  # older than 86400s policy
        _BACKUP: lambda r, s: pytest.fail("must not fall back on STALE"),
    })
    outcome = _run(world, gw)
    assert outcome.result.status is DataStatus.STALE
    assert outcome.result.data is None
    assert gw.begun == [_PRIMARY]


def test_adapter_raised_terminal_is_a_broken_source(schema_registry):
    world = _World(schema_registry)

    def primary(r, s):
        raise NoDataError(symbol="600519", canonical="600519.SH", detail="delisted")

    gw = FakeGateway(sources={_PRIMARY: primary,
                              _BACKUP: lambda r, s: pytest.fail("no fallback on broken source")})
    with pytest.raises(SourceBrokenError):
        _run(world, gw)
    assert gw.begun == [_PRIMARY]
    assert gw.rejects == [("broken_source", _PRIMARY)]  # rejected, never a fake audit
    assert gw.records == []


# =========================================================================== #
# D. bucket 3 — raise loud (PIT breach + broken primary)                       #
# =========================================================================== #
def test_future_refused_raises_not_falls_through(schema_registry):
    world = _World(schema_registry)
    gw = FakeGateway(sources={
        _PRIMARY: lambda r, s: _raw(world, _PRIMARY, [_cand(
            available_at=AS_OF + timedelta(hours=1))]),  # a future row
        _BACKUP: lambda r, s: pytest.fail("a PIT breach must never fall back"),
    })
    before = len(gw.sink_records)
    with pytest.raises(FutureDataRefused):
        _run(world, gw)
    assert gw.begun == [_PRIMARY]  # no fallback
    assert gw.records == []  # no success ToolCallRecord
    # exactly one refusal audit exists before the exception (the guard's reject).
    audits = gw.sink_records[before:]
    assert len(audits) == 1
    assert audits[0].reason_code == "future_data"
    assert gw.rejects == [("future_data", _PRIMARY)]


def test_missing_availability_raises_not_falls_through(schema_registry):
    world = _World(schema_registry)
    gw = FakeGateway(sources={
        _PRIMARY: lambda r, s: _raw(world, _PRIMARY, [_cand(available_at=None)]),
        _BACKUP: lambda r, s: pytest.fail("a PIT breach must never fall back"),
    })
    with pytest.raises(MissingAvailabilityRefused):
        _run(world, gw)
    assert gw.begun == [_PRIMARY]
    assert gw.rejects == [("missing_availability", _PRIMARY)]


def test_core_broken_primary_raises(schema_registry):
    world = _World(schema_registry)

    def primary(r, s):
        raise DataIntegrityError("primary is structurally broken")

    gw = FakeGateway(sources={_PRIMARY: primary,
                              _BACKUP: lambda r, s: pytest.fail("integrity error never falls back")})
    before = len(gw.sink_records)
    with pytest.raises(DataIntegrityError):
        _run(world, gw)
    assert gw.begun == [_PRIMARY]
    assert gw.rejects == [("source_error", _PRIMARY)]
    assert len(gw.sink_records[before:]) == 1  # failure audit retained


# =========================================================================== #
# E. exhaustion semantics (optional → UNAVAILABLE ; core → raise-first)         #
# =========================================================================== #
def test_optional_category_exhaustion_is_unavailable(schema_registry):
    world = _World(schema_registry, method_id="indicators")  # DataMethodSpec.optional=True

    def throttled(r, s):
        raise RateLimitError("throttled")

    gw = FakeGateway(sources={_PRIMARY: throttled, _BACKUP: throttled})
    outcome = _run(world, gw)
    assert outcome.result.status is DataStatus.UNAVAILABLE
    assert outcome.tool_call_record is None  # no fabricated success record
    assert outcome.result_token == world.scope.operation_token
    assert gw.begun == [_PRIMARY, _BACKUP]
    assert [a.outcome for a in outcome.result.attempts] == ["rate_limited", "rate_limited"]


def test_core_exhausted_raises_first_retriable(schema_registry):
    world = _World(schema_registry, method_id="ohlcv")  # core (optional=False)

    def throttled(r, s):
        raise RateLimitError("throttled")

    gw = FakeGateway(sources={_PRIMARY: throttled, _BACKUP: throttled})
    before = len(gw.sink_records)
    with pytest.raises(RateLimitError):
        _run(world, gw)
    assert gw.begun == [_PRIMARY, _BACKUP]
    # every failed-attempt refusal persisted before the raise.
    assert len(gw.sink_records[before:]) == 2
    assert gw.rejects == [("rate_limited", _PRIMARY), ("rate_limited", _BACKUP)]


# =========================================================================== #
# F. frozen-set + guard-construction audits (obligation 1)                      #
# =========================================================================== #
def test_manifest_mismatch_audits_once_and_invokes_no_source(schema_registry):
    world = _World(schema_registry)
    # a genuinely mismatched manifest: extra entries change the content digest, so
    # ctx.data_snapshot_content_digest no longer equals manifest.content_digest.
    from guanlan_v2.orchestration.data.snapshot import DataSnapshotEntry
    mismatched = build_data_snapshot_manifest(
        data_snapshot_id="snap-1", manifest_kind="online_capture_root", as_of=AS_OF,
        mode=DataMode.ONLINE, timezone="Asia/Shanghai", calendar_id="cn_a_share",
        routing_snapshot_digest=world.routing.routing_digest,
        schema_registry_digest=schema_registry.registry_digest,
        entries=(DataSnapshotEntry(
            dataset_id="prices", method_id="ohlcv", source_id=_PRIMARY,
            payload_schema_ref=SchemaRef(name="OHLCVRows", version="1"),
            content_digest="1" * 64, max_available_at=AS_OF),))
    gw = FakeGateway(sources={_PRIMARY: lambda r, s: pytest.fail("no source on mismatch")})
    sink = _audit_sink()
    before = len(sink.records())
    with pytest.raises(SnapshotMismatchError):
        dispatch(
            world.req, invocation_scope=world.scope, ctx=world.ctx, routing=world.routing,
            manifest=mismatched, registry=world.registry, schema_registry=schema_registry,
            resolved_policy=world.policy, gateway=gw, evidence_writer=FakeEvidenceWriter(),
            payload_reader=None, refusal_audit_sink=sink, cache=FakeCache(),
            clock=FixedClock(AS_OF), calendar=CALENDAR)
    assert gw.begun == []  # invoked no source
    assert len(sink.records()[before:]) == 1
    assert sink.records()[before:][0].reason_code == "frozen_set_mismatch"


def test_guard_construction_refusal_is_audited_before_raise(schema_registry):
    """Obligation 1: a construction refusal (wrong calendar) audits before re-raising."""
    world = _World(schema_registry)
    wrong_cal = build_trading_calendar(
        calendar_id="XSHE", sessions=_weekdays_2026(), material_id="cal.xshe", material_version="1")
    gw = FakeGateway(sources={_PRIMARY: lambda r, s: pytest.fail("no source before guard")})
    sink = _audit_sink()
    before = len(sink.records())
    with pytest.raises(RoutingConfigurationError):
        _run(world, gw, calendar=wrong_cal, sink=sink)
    assert gw.begun == []
    audits = sink.records()[before:]
    assert len(audits) == 1
    assert audits[0].reason_code == "guard_construction_refused"


# =========================================================================== #
# G. cache hit (cache_or_invoke) — record_existing at the operation token       #
# =========================================================================== #
def test_verified_cache_hit_records_existing_no_tool_call(schema_registry):
    world = _World(schema_registry)
    # build a cached OHLCV result + its typed ref.
    from guanlan_v2.orchestration.data.source import OHLCVDataResult, OHLCVRecord, OHLCVRows
    from guanlan_v2.orchestration.data.result import PitAudit, SourceAttempt
    batch = OHLCVRows.build([OHLCVRecord.from_candidate(
        _cand(available_at=AS_OF - timedelta(hours=1)))])
    from guanlan_v2.orchestration.data.source import build_data_result
    cached = build_data_result(
        world.spec, registry=schema_registry, id="cached-1", request_digest=world.req.request_digest,
        status=DataStatus.OK, data=batch, resolved_vendor_chain=(_PRIMARY,),
        source_config_digest=world.ctx.source_config_digest, fetched_at=AS_OF,
        attempts=(SourceAttempt(vendor=_PRIMARY, configured=True, outcome="success",
                                started_at=AS_OF, finished_at=AS_OF),),
        pit_audit=PitAudit(mode=DataMode.ONLINE, as_of=AS_OF, rows_seen=1, rows_returned=1,
                           future_rows=0, missing_available_at_rows=0, guard_result="passed"))
    ref = TypedPayloadRef(
        schema_ref=SchemaRef(name="OHLCVDataResult", version="1"),
        payload_ref=PayloadRef(namespace="main", object_id="obj-cache",
                               content_digest=cached.content_digest))
    hit = VerifiedDataCacheHit(result=cached, result_ref=ref)
    gw = FakeGateway(sources={_PRIMARY: lambda r, s: pytest.fail("cache hit must not invoke")})
    writer = FakeEvidenceWriter()
    outcome = dispatch(
        world.req, invocation_scope=world.scope, ctx=world.ctx, routing=world.routing,
        manifest=world.manifest, registry=world.registry, schema_registry=schema_registry,
        resolved_policy=world.policy, gateway=gw, evidence_writer=writer, payload_reader=None,
        refusal_audit_sink=gw._sink, cache=FakeCache(hit=hit), clock=FixedClock(AS_OF),
        calendar=CALENDAR)
    assert outcome.result is cached
    assert outcome.result_token == world.scope.operation_token
    assert outcome.tool_call_record is None
    assert gw.begun == []
    assert writer.existing and writer.existing[0][0] == "result"
    assert gw.records == []


def test_always_invoke_bypasses_the_cache(schema_registry):
    world = _World(schema_registry, mode="always_invoke")
    cache = FakeCache(hit="poison")  # would explode if consulted
    gw = FakeGateway(sources={_PRIMARY: lambda r, s: _raw(world, _PRIMARY, [_cand(
        available_at=AS_OF - timedelta(hours=1))])})
    outcome = _run(world, gw, cache=cache)
    assert outcome.result.status is DataStatus.OK
    assert cache.calls == 0  # always_invoke never consults the cache
    assert gw.begun == [_PRIMARY]


# =========================================================================== #
# H. method vs source identity honesty (obligation 2)                          #
# =========================================================================== #
def test_attempts_record_method_and_source_distinctly(schema_registry):
    world = _World(schema_registry)
    gw = FakeGateway(sources={_PRIMARY: lambda r, s: _raw(world, _PRIMARY, [_cand(
        available_at=AS_OF - timedelta(hours=1))])})
    outcome = _run(world, gw)
    # the attempt's vendor is the SOURCE id, distinct from the method id.
    assert outcome.result.attempts[0].vendor == _PRIMARY
    assert outcome.result.method == "ohlcv"
    assert outcome.result.method != outcome.result.attempts[0].vendor


# =========================================================================== #
# I. golden manifest                                                           #
# =========================================================================== #
def test_manifest_matches_frozen_golden():
    assert GOLDEN_PATH.exists(), f"missing golden: {GOLDEN_PATH}"
    doc = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))
    manifest = build_golden_registry().manifest()
    assert manifest == doc
    assert doc["source_registry_digest"] != "0" * 64
    # reverse registration order reproduces the identical frozen manifest.
    assert build_golden_registry(reverse=True).manifest() == doc
