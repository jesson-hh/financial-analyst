# -*- coding: utf-8 -*-
"""Phase 3 · Task 7 — the frozen ``DataReader`` facade + invocation-scoped
``DataEvidenceCollector``.

The reader is the *only* typed entry into the data plane an application node sees:
every method selects its exact ``DataMethodSpec``, validates its concrete params
model, builds a ``DataRequest`` from the frozen context, delegates to the Task-6
``dispatch`` and returns only its named concrete result. Callers can never supply
plan/node/worker identity, a guard, mutable config, a source chain, a freshness
map, mode, a strict flag, a wall clock or an alternate calendar. The collector
retains the already-persisted request/result refs + ToolCallRecord, rejects
foreign / duplicate / conflicting executor-issued tokens, and emits its three
tuples deterministically regardless of completion order.

The router is exercised against the same strict fakes Task 6 uses (a fake
CapabilityGateway / evidence writer / cache); a real ``EventRefusalAuditSink``
proves refusal audit persistence.

Run: ``pytest tests/orchestration/data/test_reader.py -v``
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from guanlan_v2.orchestration.data.calendar import build_trading_calendar
from guanlan_v2.orchestration.data.catalog import phase3_data_surface
from guanlan_v2.orchestration.data.pit import DataFetchRefusalDetails, RawRowCandidate
from guanlan_v2.orchestration.data.reader import (
    ConflictingEvidenceError,
    DataEvidenceCollector,
    DataReader,
    DataReaderFrozenSetError,
    DuplicateEvidenceError,
    ForeignEvidenceError,
)
from guanlan_v2.orchestration.data.registry import DataSourceRegistry
from guanlan_v2.orchestration.data.schema_registry import build_phase3_registry
from guanlan_v2.orchestration.data.snapshot import (
    build_data_context,
    build_data_snapshot_manifest,
    build_data_source_config_snapshot,
)
from guanlan_v2.orchestration.data.source import (
    DataInvocationScope,
    DataReadOutcome,
    DataSourceDescriptor,
    FundamentalDataResult,
    IndicatorDataResult,
    InstrumentSeriesParams,
    InstrumentUniverseParams,
    NewsDataResult,
    OHLCVDataResult,
    OHLCVRecord,
    ResolvedDataMethodPolicy,
    ResolvedMethodRoute,
    RouteEntry,
    SignalDataResult,
    VerifiedSnapshotDataResult,
    build_raw_fetch,
)
from guanlan_v2.orchestration.digest import content_digest
from guanlan_v2.orchestration.enums import DataBackend, DataMode, DataStatus
from guanlan_v2.orchestration.eventstore import EventRefusalAuditSink
from guanlan_v2.orchestration.refs import (
    CapabilityRef,
    ContentRef,
    PayloadRef,
    SchemaRef,
    TypedPayloadRef,
    typed_ref_sort_key,
)
from guanlan_v2.orchestration.runtime_contracts import (
    PHASE2_BASE_REGISTRY_DIGEST,
    AuditDetailRegistry,
    ExecutionEvidenceOrdinalToken,
    GenericRefusalDetails,
    NamedEvidenceDigest,
    phase2_runtime_registry,
)
from guanlan_v2.orchestration.schemas import ToolCallRecord

UTC = timezone.utc
AS_OF = datetime(2026, 7, 16, 7, 0, tzinfo=UTC)
_PRIMARY = "primary.vendor"
_BACKUP = "backup.vendor"
_HANDLER = ContentRef(id="data.source.handler", version="1", content_digest="a" * 64)

_SERIES_PARAMS = InstrumentSeriesParams(
    symbol={"code": "600519", "exchange": "SH", "board": "main"},
    start="2026-07-01T00:00:00+00:00", end="2026-07-15T00:00:00+00:00")
_UNIVERSE_PARAMS = InstrumentUniverseParams(as_of="2026-07-16T07:00:00+00:00")


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
    material_id="cal.cn.2026", material_version="1")


@pytest.fixture(scope="module")
def schema_registry():
    ph2 = phase2_runtime_registry(PHASE2_BASE_REGISTRY_DIGEST)
    return build_phase3_registry(ph2.registry_digest)


# --------------------------------------------------------------------------- #
# a two-source golden registry (primary -> backup for every method)            #
# --------------------------------------------------------------------------- #
def _descriptor(source_id, surface) -> DataSourceDescriptor:
    return DataSourceDescriptor.build(
        source_id=source_id, source_version="1",
        method_refs=tuple(s.method_ref for s in surface.method_specs),
        method_capability_refs=tuple(
            sorted(surface.capability_refs.values(), key=lambda c: (c.id, c.version))),
        supported_modes=(DataMode.ONLINE, DataMode.PIT_REPLAY),
        supported_backends=(DataBackend.CACHE, DataBackend.LIVE, DataBackend.PIT_STORE),
        handler_ref=_HANDLER,
        source_config_schema_ref=SchemaRef(name="DataSourceConfigSnapshot", version="1"))


def _source_ref(desc) -> ContentRef:
    return ContentRef(id=desc.source_id, version=desc.source_version,
                      content_digest=desc.descriptor_digest)


def _build_registry() -> DataSourceRegistry:
    surface = phase3_data_surface()
    primary, backup = _descriptor(_PRIMARY, surface), _descriptor(_BACKUP, surface)
    p_ref, b_ref = _source_ref(primary), _source_ref(backup)
    reg = DataSourceRegistry(registry_version="phase3-source-v1")
    for spec in surface.method_specs:
        reg.register_method(spec)
    reg.register_descriptor(primary).register_descriptor(backup)
    for spec in surface.method_specs:
        reg.register_route(ResolvedMethodRoute(
            method_ref=spec.method_ref,
            entries=(RouteEntry(source_ref=p_ref, capability_ref=spec.capability_ref),
                     RouteEntry(source_ref=b_ref, capability_ref=spec.capability_ref)),
            route_policy_ref=ContentRef(id="policy.route.default", version="1",
                                        content_digest="d" * 64)))
    reg.register_freshness(surface.freshness_policy)
    return reg.seal()


def _token(evidence: int) -> ExecutionEvidenceOrdinalToken:
    return ExecutionEvidenceOrdinalToken(attempt=1, call_ordinal=1, evidence_ordinal=evidence)


# --------------------------------------------------------------------------- #
# fakes (mirror Task 6's strict router collaborators)                          #
# --------------------------------------------------------------------------- #
def _audit_sink() -> EventRefusalAuditSink:
    reg = AuditDetailRegistry()
    reg.register(GenericRefusalDetails)
    reg.register(NamedEvidenceDigest)
    reg.register(DataFetchRefusalDetails)
    reg.seal()
    return EventRefusalAuditSink(detail_registry=reg, clock=FixedClock(AS_OF))


class _Pending:
    def __init__(self, token, source_ref, capability_ref, idem):
        self.token = token
        self.source_ref = source_ref
        self.capability_ref = capability_ref
        self.idem = idem


class FakeGateway:
    def __init__(self, *, sources, sink):
        self._sources = sources
        self._sink = sink
        self.begun: list[str] = []
        self.records: list[ToolCallRecord] = []

    def begin(self, *, attempt_token, source_ref, capability_ref, request_schema_ref,
              result_schema_ref, idempotency_key):
        self.begun.append(source_ref.id)
        return _Pending(attempt_token, source_ref, capability_ref, idempotency_key)

    def invoke(self, pending, request, *, scope):
        return self._sources[pending.source_ref.id](request, scope)

    def finalize_success(self, pending, *, request_ref, result_ref, request_digest, result_digest):
        record = ToolCallRecord(
            call_ordinal=pending.token.call_ordinal, tool_ref=pending.capability_ref,
            request_ref=request_ref, result_ref=result_ref,
            call_id=f"pm:call-{pending.token.call_ordinal}", started_at=AS_OF, finished_at=AS_OF)
        self.records.append(record)
        return record

    def reject(self, pending, *, detail_schema_ref, detail_payload, reason_code, idempotency_key):
        return self._sink.record(
            detail_schema_ref=detail_schema_ref, detail_payload=detail_payload,
            reason_code=reason_code, idempotency_key=idempotency_key,
            attempted_capability_ref=pending.capability_ref)


def _digest_of(payload) -> str:
    for attr in ("content_digest", "request_digest"):
        v = getattr(payload, attr, None)
        if v is not None:
            return v
    return content_digest(payload)


class FakeEvidenceWriter:
    def __init__(self):
        self._seq = 0

    def put(self, *, token, role, schema_ref, payload, idempotency_key):
        self._seq += 1
        return TypedPayloadRef(
            schema_ref=schema_ref,
            payload_ref=PayloadRef(namespace="main", object_id=f"obj-{self._seq}",
                                   content_digest=_digest_of(payload)))

    def record_existing(self, *, token, role, typed_ref, idempotency_key):
        return typed_ref


class FakeCache:
    def get_verified(self, key, *, ctx, manifest, registry, payload_store):
        return None


# --------------------------------------------------------------------------- #
# a fully-built frozen world + reader for one method                           #
# --------------------------------------------------------------------------- #
class _World:
    def __init__(self, schema_registry, *, method_id="ohlcv", sources=None):
        self.surface = phase3_data_surface()
        self.registry = _build_registry()
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
        self.route = self.registry.default_route(method_id)
        self.scope = DataInvocationScope(
            plan_digest="a" * 64, node_id="pm", worker_id="dec.pm",
            operation_token=_token(1), attempt_tokens=(_token(2), _token(3)),
            frozen_route=self.route, invocation_mode="cache_or_invoke",
            catalog_digest="b" * 64, schema_registry_digest=schema_registry.registry_digest)
        self.policy_resolver = _PolicyResolver(self.spec, self.surface.freshness_policy)
        self.collector = DataEvidenceCollector(
            issued_tokens=(self.scope.operation_token, *self.scope.attempt_tokens))
        self.sink = _audit_sink()
        self.gateway = FakeGateway(sources=sources or {}, sink=self.sink)

    def source_ref(self, source_id) -> ContentRef:
        for entry in self.route.entries:
            if entry.source_ref.id == source_id:
                return entry.source_ref
        raise KeyError(source_id)

    def reader(self, **over) -> DataReader:
        fields = dict(
            ctx=self.ctx, routing=self.routing, manifest=self.manifest, registry=self.registry,
            schema_registry=self.schema_registry, source_config=self.config, scope=self.scope,
            collector=self.collector, policy_resolver=self.policy_resolver,
            clock=FixedClock(AS_OF), calendar=CALENDAR, gateway=self.gateway,
            evidence_writer=FakeEvidenceWriter(), payload_reader=None,
            refusal_audit_sink=self.sink, cache=FakeCache())
        fields.update(over)
        return DataReader(**fields)


class _PolicyResolver:
    """A minimal resolver returning one bound policy bundle for the world's method."""

    def __init__(self, spec, freshness_policy):
        self._spec = spec
        self._fp = freshness_policy

    def resolve_method(self, method_spec, *, ctx) -> ResolvedDataMethodPolicy:
        return ResolvedDataMethodPolicy.build(
            method_ref=method_spec.method_ref, freshness_policy=self._fp, limit_policy=None,
            calendar_id="cn_a_share", calendar_material_ref=CALENDAR.material_ref)


def _cand(*, available_at, close=1.5) -> RawRowCandidate:
    return RawRowCandidate.build(
        raw_payload={"symbol": {"code": "600519", "exchange": "SH", "board": "main"},
                     "open": 1.0, "high": 2.0, "low": 0.5, "close": close, "volume": 100},
        effective_at=datetime(2026, 7, 1, tzinfo=UTC), available_at=available_at,
        ingested_at=datetime(2026, 7, 2, tzinfo=UTC))


def _snap_cand(*, available_at) -> RawRowCandidate:
    return RawRowCandidate.build(
        raw_payload={"symbol": {"code": "600519", "exchange": "SH", "board": "main"},
                     "last_price": 1.5, "prev_close": 1.4},
        effective_at=None, available_at=available_at,
        ingested_at=datetime(2026, 7, 2, tzinfo=UTC))


def _raw_for(world, source_id, candidates):
    """Bind the raw fetch to the actual request digest at invoke time."""
    def _fn(request, scope):
        return build_raw_fetch(
            request_digest=request.request_digest, source_ref=world.source_ref(source_id),
            capability_ref=world.spec.capability_ref, candidates=tuple(candidates),
            subsource="main", fetched_at=AS_OF, provider_request_id="p-1")
    return _fn


# =========================================================================== #
# 1. get_ohlcv OK: typed result + collector retains refs + ToolCallRecord       #
# =========================================================================== #
def test_get_ohlcv_ok(schema_registry):
    world = _World(schema_registry, method_id="ohlcv")
    world.gateway._sources = {
        _PRIMARY: _raw_for(world, _PRIMARY, [_cand(available_at=AS_OF - timedelta(hours=1))])}
    reader = world.reader()
    result = reader.get_ohlcv(_SERIES_PARAMS)
    assert isinstance(result, OHLCVDataResult)
    assert result.status is DataStatus.OK
    assert result.data is not None and all(isinstance(r, OHLCVRecord) for r in result.data.rows)
    # the collector retained the finalized ToolCallRecord + typed request/result refs.
    assert len(world.collector.tool_call_records()) == 1
    assert len(world.collector.data_result_refs()) == 1
    assert len(world.collector.request_refs()) == 1
    rec = world.collector.tool_call_records()[0]
    assert rec.result_ref in world.collector.data_result_refs()


# =========================================================================== #
# 2. every facade method binds its exact params + result SchemaRefs             #
# =========================================================================== #
@pytest.mark.parametrize(
    "method_id, call, params, result_cls",
    [
        ("ohlcv", "get_ohlcv", _SERIES_PARAMS, OHLCVDataResult),
        ("indicators", "get_indicators", _SERIES_PARAMS, IndicatorDataResult),
        ("verified_snapshot", "get_verified_snapshot", _UNIVERSE_PARAMS, VerifiedSnapshotDataResult),
        ("fundamentals", "get_fundamentals", _SERIES_PARAMS, FundamentalDataResult),
        ("news", "get_news", _UNIVERSE_PARAMS, NewsDataResult),
    ],
)
def test_every_method_returns_its_named_envelope(schema_registry, method_id, call, params, result_cls):
    world = _World(schema_registry, method_id=method_id)
    # each method resolves to a NO_DATA terminal (empty candidates) -> a real typed answer.
    world.gateway._sources = {_PRIMARY: _raw_for(world, _PRIMARY, [])}
    reader = world.reader()
    result = getattr(reader, call)(params)
    assert isinstance(result, result_cls)
    assert result.method == method_id
    assert result.status is DataStatus.NO_DATA


def test_get_verified_snapshot_ok(schema_registry):
    world = _World(schema_registry, method_id="verified_snapshot")
    world.gateway._sources = {
        _PRIMARY: _raw_for(world, _PRIMARY, [_snap_cand(available_at=AS_OF - timedelta(hours=1))])}
    result = world.reader().get_verified_snapshot(_UNIVERSE_PARAMS)
    assert isinstance(result, VerifiedSnapshotDataResult)
    assert result.status is DataStatus.OK


# =========================================================================== #
# 3. unknown / arbitrary signal method ref is rejected                          #
# =========================================================================== #
def test_get_signal_accepts_the_approved_signal_ref(schema_registry):
    world = _World(schema_registry, method_id="signals")
    world.gateway._sources = {_PRIMARY: _raw_for(world, _PRIMARY, [])}
    result = world.reader().get_signal(world.spec.method_ref, _SERIES_PARAMS)
    assert isinstance(result, SignalDataResult)


def test_get_signal_rejects_a_foreign_method_ref(schema_registry):
    world = _World(schema_registry, method_id="signals")
    ohlcv_ref = world.registry.method_spec("ohlcv").method_ref  # not the signals spec
    with pytest.raises(ValueError, match="signal"):
        world.reader().get_signal(ohlcv_ref, _SERIES_PARAMS)


def test_get_signal_rejects_a_forged_signal_ref(schema_registry):
    world = _World(schema_registry, method_id="signals")
    forged = ContentRef(id="signals", version="1", content_digest="0" * 64)  # wrong digest
    with pytest.raises(ValueError):
        world.reader().get_signal(forged, _SERIES_PARAMS)


# =========================================================================== #
# 4. constructor rejects a mismatched frozen set                                #
# =========================================================================== #
def test_constructor_rejects_mismatched_manifest(schema_registry):
    world = _World(schema_registry)
    other = build_data_snapshot_manifest(
        data_snapshot_id="snap-2", manifest_kind="online_capture_root", as_of=AS_OF,
        mode=DataMode.ONLINE, timezone="Asia/Shanghai", calendar_id="cn_a_share",
        routing_snapshot_digest=world.routing.routing_digest,
        schema_registry_digest=schema_registry.registry_digest, entries=())
    with pytest.raises(DataReaderFrozenSetError):
        world.reader(manifest=other)


def test_constructor_rejects_mismatched_schema_registry_digest(schema_registry):
    world = _World(schema_registry)
    bad_scope = DataInvocationScope(
        plan_digest="a" * 64, node_id="pm", worker_id="dec.pm",
        operation_token=_token(1), attempt_tokens=(_token(2), _token(3)),
        frozen_route=world.route, invocation_mode="cache_or_invoke",
        catalog_digest="b" * 64, schema_registry_digest="0" * 64)
    with pytest.raises(DataReaderFrozenSetError):
        world.reader(scope=bad_scope)


# =========================================================================== #
# 5. typed methods expose no caller cfg / guard / strict / mode / now overrides  #
# =========================================================================== #
def test_no_caller_control_knobs_on_typed_methods(schema_registry):
    import inspect

    world = _World(schema_registry)
    reader = world.reader()
    forbidden = {"cfg", "config", "guard", "strict", "strict_pit", "mode", "now",
                 "clock", "freshness", "calendar", "chain", "source_chain"}
    for name in ("get_ohlcv", "get_indicators", "get_verified_snapshot",
                 "get_fundamentals", "get_news", "get_signal"):
        params = set(inspect.signature(getattr(reader, name)).parameters)
        assert not (params & forbidden), f"{name} exposes a forbidden knob: {params & forbidden}"


# =========================================================================== #
# 12. collector: foreign / duplicate / conflicting + order-independent merge     #
# =========================================================================== #
def _outcome(op_token, result_token, *, result_digest="1" * 64, with_record=True) -> DataReadOutcome:
    result = _built_ohlcv_result(result_digest)
    request_ref = TypedPayloadRef(
        schema_ref=SchemaRef(name="DataRequest", version="1"),
        payload_ref=PayloadRef(namespace="main", object_id="req", content_digest="2" * 64))
    result_ref = TypedPayloadRef(
        schema_ref=SchemaRef(name="OHLCVDataResult", version="1"),
        payload_ref=PayloadRef(namespace="main", object_id="res",
                               content_digest=result.content_digest))
    record = None
    if with_record:
        record = ToolCallRecord(
            call_ordinal=result_token.call_ordinal,
            tool_ref=CapabilityRef(id="cap.data.ohlcv", version="1", content_digest="e" * 64),
            request_ref=request_ref, result_ref=result_ref, call_id="c",
            started_at=AS_OF, finished_at=AS_OF)
    return DataReadOutcome(
        operation_token=op_token, result_token=result_token, result=result,
        request_ref=request_ref, result_ref=result_ref, tool_call_record=record)


_PHASE3_REG = None


def _built_ohlcv_result(digest_seed):
    reg = _module_registry()
    surface = phase3_data_surface()
    spec = surface.spec_by_method["ohlcv"]
    from guanlan_v2.orchestration.data.source import OHLCVRows, build_data_result
    from guanlan_v2.orchestration.data.result import PitAudit, SourceAttempt
    rows = OHLCVRows.build([OHLCVRecord.from_candidate(
        _cand(available_at=AS_OF - timedelta(hours=1), close=float(len(digest_seed))))])
    return build_data_result(
        spec, registry=reg, id="r", request_digest="a" * 64, status=DataStatus.OK, data=rows,
        resolved_vendor_chain=(_PRIMARY,), source_config_digest="a" * 64, fetched_at=AS_OF,
        attempts=(SourceAttempt(vendor=_PRIMARY, configured=True, outcome="success",
                                started_at=AS_OF, finished_at=AS_OF),),
        pit_audit=PitAudit(mode=DataMode.ONLINE, as_of=AS_OF, rows_seen=1, rows_returned=1,
                           future_rows=0, missing_available_at_rows=0, guard_result="passed"))


def _module_registry():
    global _PHASE3_REG
    if _PHASE3_REG is None:
        ph2 = phase2_runtime_registry(PHASE2_BASE_REGISTRY_DIGEST)
        _PHASE3_REG = build_phase3_registry(ph2.registry_digest)
    return _PHASE3_REG


def _tok(call, evidence):
    return ExecutionEvidenceOrdinalToken(attempt=1, call_ordinal=call, evidence_ordinal=evidence)


def test_collector_rejects_a_foreign_token():
    col = DataEvidenceCollector(issued_tokens=(_tok(1, 1), _tok(1, 2)))
    stranger = _outcome(_tok(9, 9), _tok(9, 9))
    with pytest.raises(ForeignEvidenceError):
        col.accept(stranger)


def test_collector_rejects_a_duplicate_operation_token():
    op, res = _tok(1, 1), _tok(1, 2)
    col = DataEvidenceCollector(issued_tokens=(op, res))
    col.accept(_outcome(op, res))
    with pytest.raises(DuplicateEvidenceError):
        col.accept(_outcome(op, res))


def test_collector_rejects_conflicting_refs_at_the_same_slot():
    op, res = _tok(1, 1), _tok(1, 2)
    col = DataEvidenceCollector(issued_tokens=(op, res, _tok(1, 3)))
    col.accept(_outcome(op, res, result_digest="1" * 64))
    # a different operation token but the SAME result-position token carrying a
    # different result ref is a conflict.
    with pytest.raises(ConflictingEvidenceError):
        col.accept(_outcome(_tok(1, 3), res, result_digest="7" * 64))


def test_collector_merge_is_order_independent():
    tokens = [(_tok(1, 10), _tok(1, 11)), (_tok(2, 20), _tok(2, 21)), (_tok(3, 30), _tok(3, 31))]
    issued = tuple(t for pair in tokens for t in pair)
    outcomes = [_outcome(op, res, result_digest=str(i) * (i + 3)) for i, (op, res) in enumerate(tokens)]

    forward = DataEvidenceCollector(issued_tokens=issued)
    for o in outcomes:
        forward.accept(o)
    reverse = DataEvidenceCollector(issued_tokens=issued)
    for o in reversed(outcomes):
        reverse.accept(o)

    assert [r.call_ordinal for r in forward.tool_call_records()] == \
           [r.call_ordinal for r in reverse.tool_call_records()]
    assert forward.data_result_refs() == reverse.data_result_refs()
    assert forward.request_refs() == reverse.request_refs()
    # tool calls are ordered by strictly increasing call_ordinal (Phase 1 canonicalizer)
    ordinals = [r.call_ordinal for r in forward.tool_call_records()]
    assert ordinals == sorted(ordinals)
    # data-result refs are canonically ordered by the typed semantic projection
    keys = [typed_ref_sort_key(r) for r in forward.data_result_refs()]
    assert keys == sorted(keys)


def test_collector_cache_hit_has_no_tool_call_record():
    op = _tok(1, 1)
    col = DataEvidenceCollector(issued_tokens=(op,))
    col.accept(_outcome(op, op, with_record=False))  # cache-hit shape: result_token == op
    assert col.tool_call_records() == ()
    assert len(col.data_result_refs()) == 1  # the cache result still appears as a data ref
