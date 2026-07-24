# -*- coding: utf-8 -*-
"""Phase 9 · Task 2 — strict raw PitReader adapter + PIT_REPLAY DataContext.

Locks the PIT time-machine backbone every interval-replay decision point reads
through (Task 4). The strictness posture is REFUSE, never filter: a future row
reaching the adapter is a bug upstream/in the store, and the pipeline raises
loudly (``FutureDataRefused``) — it never silently drops rows or degrades.

Design (recorded in the report's C4 corrections): ``PitReaderRawSource`` is a pure
Phase-3 ``DataSource`` (``fetch(request, *, scope) -> RawFetch``) that wraps every
reader row into a ``RawRowCandidate`` WITHOUT pre-filtering by time. The single PIT
authority is ``PitGuard.check_raw`` — exactly where the reviewed Phase-3 router
invokes it (``data/registry.py`` dispatch step 5c). The row-level tests reproduce
that step faithfully by building the REAL guard from the REAL replay ``DataContext``
and calling ``check_raw`` on the adapter's candidates; the no-fallback invariant is
proven end-to-end through the REAL ``dispatch`` router (a route with a spy behind
the refusing source).

Run: ``python -m pytest tests/orchestration/test_adapters_replay_data.py -v``
"""
from __future__ import annotations

import inspect
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from guanlan_v2.orchestration.adapters.contracts import ReplayDecisionPoint
from guanlan_v2.orchestration.adapters.replay_data import (
    PitReaderRawSource,
    ReplayPointClock,
    build_pit_replay_descriptor,
    build_replay_data_context,
    build_replay_manifest,
)
from guanlan_v2.orchestration.context import DataContext
from guanlan_v2.orchestration.data.calendar import build_trading_calendar
from guanlan_v2.orchestration.data.catalog import phase3_data_surface
from guanlan_v2.orchestration.data.errors import (
    FutureDataRefused,
    MissingAvailabilityRefused,
)
from guanlan_v2.orchestration.data.pit import PitGuard, RawRowCandidate
from guanlan_v2.orchestration.data.registry import DataSourceRegistry, dispatch
from guanlan_v2.orchestration.data.schema_registry import build_phase3_registry
from guanlan_v2.orchestration.data.snapshot import (
    DataSnapshotEntry,
    DataSnapshotManifest,
    build_data_source_config_snapshot,
)
from guanlan_v2.orchestration.data.source import (
    DataInvocationScope,
    DataSourceDescriptor,
    NewsRecord,
    NewsRows,
    RawFetch,
    ResolvedDataMethodPolicy,
    ResolvedMethodRoute,
    RouteEntry,
    build_data_request,
    build_data_result,
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
#: 2026-07-16 07:00Z == 2026-07-16 15:00 Asia/Shanghai (a live trading session).
AS_OF = datetime(2026, 7, 16, 7, 0, tzinfo=UTC)
_HANDLER = ContentRef(id="data.source.handler", version="1", content_digest="a" * 64)
_STORE_META = {
    "news_coverage_floor": "2020-05-20",
    "cal_start": "2015-01-05",
    "cal_end": "2026-12-31",
    "data_end": "2026-07-15",
}


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
# fixtures: a fake PitReader, decision point, tokens                          #
# --------------------------------------------------------------------------- #
def _news(ts, *, title="headline", code=None, url=None):
    return SimpleNamespace(ts=ts, code=code, title=title, body="body",
                           source="src", url=url, date="")


def _event(ann_date, *, summary="event", code="SH600519", visible_ts=None, type_="disclosure"):
    fields = {"visible_ts": visible_ts} if visible_ts else {}
    return SimpleNamespace(ann_date=ann_date, code=code, type=type_,
                           summary=summary, fields=fields, source="src")


def _policy(ts, *, title="policy", code=None, url=None):
    return SimpleNamespace(ts=ts, pub_date="", title=title, summary="sum",
                           code=code, source="src", url=url, level="gov")


def _bar(time, *, open_=1.0, high=2.0, low=0.5, close=1.5, volume=100):
    return {"time": time, "open": open_, "high": high, "low": low,
            "close": close, "volume": volume}


def _parse_local(s: str) -> datetime:
    return datetime.fromisoformat(str(s).replace(" ", "T"))


class _FakeReader:
    """A fake ``PitReader`` returning controlled visible rows (never real I/O).

    ``enforce_boundary`` mirrors the real reader's PIT truncation (news/policy
    ``ts`` <= boundary; events ``ann_date`` <= date), so the digest-stability test
    can prove that appending strictly-future store rows leaves the visible set
    (and therefore the result digest) untouched. The refusal tests use a *leaky*
    reader (``enforce_boundary=False``) to prove the adapter forwards a future row
    to the guard rather than silently dropping it.
    """

    def __init__(self, *, news=(), events=(), policy=(), intraday=(), day=(),
                 enforce_boundary=False):
        self._news = list(news)
        self._events = list(events)
        self._policy = list(policy)
        self._intraday = list(intraday)
        self._day = list(day)
        self._enforce = enforce_boundary
        self.calls = 0

    def add_future_news(self, item):
        self._news.append(item)

    def get_visible_info(self, date, codes=None, as_of="09:25", lookback_days=1,
                         include=("news", "events", "policy")):
        self.calls += 1
        news, events, policy = self._news, self._events, self._policy
        if self._enforce:
            boundary = _parse_local(f"{date} {as_of}")
            news = [n for n in news if _parse_local(n.ts) <= boundary]
            policy = [p for p in policy if _parse_local(p.ts) <= boundary]
            events = [e for e in events if str(e.ann_date) <= str(date)]
        return SimpleNamespace(
            news=list(news) if "news" in include else [],
            events=list(events) if "events" in include else [],
            policy=list(policy) if "policy" in include else [],
        )

    def fetch_bars_intraday(self, code, date, freq="5min"):
        return list(self._intraday)

    def fetch_quote_leq_prev(self, code, n_days_back=30, freq="day", as_of_date=None):
        return list(self._day)


def _decision_point(as_of: datetime = AS_OF) -> ReplayDecisionPoint:
    return ReplayDecisionPoint(
        schedule_ref=ContentRef(id="sched.replay", version="1", content_digest="c" * 64),
        schedule_digest="d" * 64, point_ordinal=1, scheduled_for=as_of,
        cutoff_at=as_of - timedelta(minutes=1), decision_as_of=as_of,
        eligible_execution_at=as_of + timedelta(minutes=1),
        execution_price_field="close", bar_frequency="1d",
    )


def _token(evidence: int) -> ExecutionEvidenceOrdinalToken:
    return ExecutionEvidenceOrdinalToken(attempt=1, call_ordinal=1, evidence_ordinal=evidence)


_NEWS_PARAMS = {
    "symbols": ({"code": "600519", "exchange": "SH", "board": "main"},),
    "as_of": AS_OF.isoformat(),
}
_OHLCV_INTRADAY_PARAMS = {
    "symbol": {"code": "600519", "exchange": "SH", "board": "main"},
    "start": "2026-07-16T00:00:00+00:00", "end": "2026-07-16T00:00:00+00:00",
    "field_id": "5min",
}
_OHLCV_DAILY_PARAMS = {
    "symbol": {"code": "600519", "exchange": "SH", "board": "main"},
    "start": "2026-07-01T00:00:00+00:00", "end": "2026-07-15T00:00:00+00:00",
}


class _SpyRecorder:
    def __init__(self):
        self.records: list = []

    def record_data_refusal(self, details, evidence, *, idempotency_key):
        self.records.append((details.reason_code, idempotency_key))


# --------------------------------------------------------------------------- #
# a coherent PIT_REPLAY frozen world (source registry + routing + manifest +   #
# context) built from THIS task's builders + one adapter over a fake reader     #
# --------------------------------------------------------------------------- #
class _ReplayWorld:
    def __init__(self, schema_registry, *, method_id, params, fake_reader,
                 spy_source=False, data_snapshot_id="pit-root-A"):
        surface = phase3_data_surface()
        self.spec = surface.spec_by_method[method_id]
        pit_desc = build_pit_replay_descriptor(
            method_specs=surface.method_specs, handler_ref=_HANDLER)
        self.src_ref = ContentRef(id=pit_desc.source_id, version=pit_desc.source_version,
                                  content_digest=pit_desc.descriptor_digest)
        reg = DataSourceRegistry(registry_version="p9-replay-v1")
        for s in surface.method_specs:
            reg.register_method(s)
        reg.register_descriptor(pit_desc)
        entries = [RouteEntry(source_ref=self.src_ref, capability_ref=self.spec.capability_ref)]
        self.spy_ref = None
        if spy_source:
            spy_desc = DataSourceDescriptor.build(
                source_id="backup.spy", source_version="1",
                method_refs=tuple(s.method_ref for s in surface.method_specs),
                method_capability_refs=pit_desc.method_capability_refs,
                supported_modes=(DataMode.PIT_REPLAY,),
                supported_backends=(DataBackend.PIT_STORE,),
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
        self.decision_point = _decision_point(AS_OF)
        self.manifest = build_replay_manifest(
            data_snapshot_id=data_snapshot_id, store_meta=_STORE_META, as_of=AS_OF,
            timezone="Asia/Shanghai", calendar_id="cn_a_share",
            routing_snapshot_digest=self.routing.routing_digest,
            schema_registry_digest=schema_registry.registry_digest)
        self.ctx = build_replay_data_context(
            decision_point=self.decision_point, source_config=self.config,
            source_registry=self.snapshot, routing=self.routing, manifest=self.manifest)
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
        self.adapter = PitReaderRawSource(reader_factory=lambda: fake_reader)
        self.policy = ResolvedDataMethodPolicy.build(
            method_ref=self.spec.method_ref, freshness_policy=surface.freshness_policy,
            limit_policy=None, calendar_id="cn_a_share",
            calendar_material_ref=CALENDAR.material_ref)

    def guard(self, recorder=None) -> PitGuard:
        return PitGuard.from_context(
            self.ctx, clock=ReplayPointClock(as_of=self.decision_point.decision_as_of),
            calendar=CALENDAR, refusal_recorder=recorder or _SpyRecorder())

    def fetch(self) -> RawFetch:
        return self.adapter.fetch(self.req, scope=self.scope)

    def check(self, guard=None, freshness=None):
        raw = self.fetch()
        g = guard or self.guard()
        return g.check_raw(
            raw.candidates, request_digest=self.req.request_digest,
            request_context_digest=content_digest(self.ctx), source_ref=self.src_ref,
            freshness=freshness)


# =========================================================================== #
# 1. ReplayPointClock                                                          #
# =========================================================================== #
def test_replay_clock_frozen_and_aware():
    with pytest.raises(ValueError):
        ReplayPointClock(as_of=datetime(2026, 7, 16, 15, 0))  # naive rejected

    clock = ReplayPointClock(as_of=AS_OF)
    first, second = clock.now(), clock.now()
    assert first == second == AS_OF
    assert first.tzinfo is not None and first.utcoffset() == timedelta(0)


# =========================================================================== #
# 2. future news row -> FutureDataRefused (no partial result escapes)          #
# =========================================================================== #
def test_future_news_row_refused(schema_registry):
    future_ts = (AS_OF + timedelta(minutes=1)).astimezone(_SH).strftime("%Y-%m-%d %H:%M:%S")
    reader = _FakeReader(news=[_news(future_ts, title="leaked from the future")])
    world = _ReplayWorld(schema_registry, method_id="news", params=_NEWS_PARAMS,
                         fake_reader=reader)
    spy = _SpyRecorder()
    with pytest.raises(FutureDataRefused) as exc:
        world.check(guard=world.guard(spy))
    assert exc.value.future_rows == 1
    assert spy.records and spy.records[0][0] == "future_data"


# =========================================================================== #
# 3. future intraday bar -> FutureDataRefused (NOT silently filtered)          #
# =========================================================================== #
def test_future_bar_refused_not_filtered(schema_registry):
    future_close = (AS_OF + timedelta(minutes=1)).astimezone(_SH).strftime("%Y-%m-%d %H:%M:%S")
    reader = _FakeReader(intraday=[_bar(future_close)])
    world = _ReplayWorld(schema_registry, method_id="ohlcv",
                         params=_OHLCV_INTRADAY_PARAMS, fake_reader=reader)
    # the fetch must NOT drop the bar — one candidate reaches the guard.
    assert len(world.fetch().candidates) == 1
    with pytest.raises(FutureDataRefused) as exc:
        world.check()
    assert exc.value.future_rows == 1


# =========================================================================== #
# 4. missing / naive available_at -> MissingAvailabilityRefused                #
# =========================================================================== #
def test_missing_available_at_refused(schema_registry):
    # (a) a row with no timestamp at all -> available_at is None.
    world_none = _ReplayWorld(schema_registry, method_id="news", params=_NEWS_PARAMS,
                              fake_reader=_FakeReader(news=[_news(None)]))
    cands = world_none.fetch().candidates
    assert len(cands) == 1 and cands[0].available_at is None
    with pytest.raises(MissingAvailabilityRefused):
        world_none.check()

    # (b) a naive available_at can never even be canonically digested into a
    # RawFetch, so the adapter never emits one (it localizes every wall-time); the
    # PitGuard's naive-refusal is the defense-in-depth backstop, proven here on a
    # directly-constructed naive candidate exactly as the router's guard would see it.
    world = _ReplayWorld(schema_registry, method_id="news", params=_NEWS_PARAMS,
                         fake_reader=_FakeReader())
    naive_cand = RawRowCandidate.build(
        raw_payload={"headline": "naive"}, effective_at=None,
        available_at=datetime(2026, 7, 16, 9, 31), ingested_at=AS_OF)
    assert naive_cand.available_at is not None and naive_cand.available_at.tzinfo is None
    with pytest.raises(MissingAvailabilityRefused):
        world.guard().check_raw(
            (naive_cand,), request_digest=world.req.request_digest,
            request_context_digest=content_digest(world.ctx), source_ref=world.src_ref,
            freshness=None)


# =========================================================================== #
# 5. a PIT refusal never falls back to a second vendor (real dispatch)          #
# =========================================================================== #
class _SpySource:
    def __init__(self):
        self.calls = 0

    def __call__(self, request, *, scope):
        self.calls += 1  # pragma: no cover - must never be reached
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
        raise AssertionError("a PIT refusal must not finalize a success")

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
    return EventRefusalAuditSink(detail_registry=reg, clock=ReplayPointClock(as_of=AS_OF))


def test_refusal_never_falls_back(schema_registry):
    future_ts = (AS_OF + timedelta(minutes=1)).astimezone(_SH).strftime("%Y-%m-%d %H:%M:%S")
    reader = _FakeReader(news=[_news(future_ts)])
    world = _ReplayWorld(schema_registry, method_id="news", params=_NEWS_PARAMS,
                         fake_reader=reader, spy_source=True)
    spy = _SpySource()
    sink = _audit_sink()
    gateway = _FallbackGateway(
        sources={world.src_ref.id: world.adapter.fetch, world.spy_ref.id: spy}, sink=sink)
    with pytest.raises(FutureDataRefused):
        dispatch(
            world.req, invocation_scope=world.scope, ctx=world.ctx, routing=world.routing,
            manifest=world.manifest, registry=world.registry,
            schema_registry=schema_registry, resolved_policy=world.policy,
            gateway=gateway, evidence_writer=_FakeWriter(), payload_reader=None,
            refusal_audit_sink=sink, cache=_NullCache(),
            clock=ReplayPointClock(as_of=AS_OF), calendar=CALENDAR)
    assert spy.calls == 0  # the second vendor was never invoked
    assert any(rc == "future_data" for rc, _ in gateway.rejects)


# =========================================================================== #
# 6. old-date digest stability under strictly-future store rows                #
# =========================================================================== #
def _news_result(world, passed, audit, schema_registry):
    records = tuple(NewsRecord.from_candidate(c) for c in passed)
    batch = NewsRows.build(records)
    return build_data_result(
        world.spec, registry=schema_registry, id="data-x",
        request_digest=world.req.request_digest, status=DataStatus.OK, data=batch,
        resolved_vendor_chain=(world.src_ref.id,),
        source_config_digest=world.ctx.source_config_digest, fetched_at=AS_OF,
        attempts=(), pit_audit=audit)


def test_old_date_digest_stable_under_future_rows(schema_registry):
    reader = _FakeReader(
        news=[_news("2026-07-16 09:31:00", title="visible macro"),
              _news("2026-07-16 10:05:00", title="also visible")],
        enforce_boundary=True)
    world = _ReplayWorld(schema_registry, method_id="news", params=_NEWS_PARAMS,
                         fake_reader=reader)
    passed1, audit1 = world.check()
    digest1 = _news_result(world, passed1, audit1, schema_registry).content_digest

    # append strictly-future rows to the store; the enforcing reader hides them.
    reader.add_future_news(_news("2026-07-16 15:30:00", title="tomorrow's news"))
    reader.add_future_news(_news("2026-07-17 09:31:00", title="next session"))
    passed2, audit2 = world.check()
    digest2 = _news_result(world, passed2, audit2, schema_registry).content_digest

    assert digest1 == digest2
    assert len(passed1) == len(passed2) == 2


# =========================================================================== #
# 7. event ann_date end-of-availability convention (matches news_marks.py)      #
# =========================================================================== #
def test_event_ann_date_availability_convention(schema_registry):
    today = AS_OF.astimezone(_SH).date().isoformat()          # 2026-07-16
    tomorrow = (AS_OF.astimezone(_SH).date() + timedelta(days=1)).isoformat()

    visible = _ReplayWorld(
        schema_registry, method_id="news", params=_NEWS_PARAMS,
        fake_reader=_FakeReader(events=[_event(today, code=None)]))
    passed, audit = visible.check()
    assert len(passed) == 1 and audit.guard_result == "passed"

    future = _ReplayWorld(
        schema_registry, method_id="news", params=_NEWS_PARAMS,
        fake_reader=_FakeReader(events=[_event(tomorrow, code=None)]))
    with pytest.raises(FutureDataRefused):
        future.check()


# =========================================================================== #
# 8. build_replay_data_context coherence                                       #
# =========================================================================== #
def test_context_builder_coherence(schema_registry):
    world = _ReplayWorld(schema_registry, method_id="news", params=_NEWS_PARAMS,
                         fake_reader=_FakeReader())
    ctx = world.ctx
    assert isinstance(ctx, DataContext)
    clock = ReplayPointClock(as_of=world.decision_point.decision_as_of)
    assert ctx.as_of == clock.now() == world.decision_point.decision_as_of
    assert ctx.calendar_id == ctx.clock.calendar_id == "cn_a_share"
    assert ctx.mode is DataMode.PIT_REPLAY
    assert ctx.backend is DataBackend.PIT_STORE
    assert ctx.strict_pit is True
    assert ctx.vintage_manifest_digest is not None
    assert ctx.vintage_manifest_digest == world.manifest.vintage_manifest_digest


# =========================================================================== #
# 9. no method accepts a caller time/strict override                           #
# =========================================================================== #
def test_no_caller_time_override():
    banned = {"as_of", "strict", "strict_pit", "now"}
    source = PitReaderRawSource(reader_factory=lambda: _FakeReader())
    for name, member in inspect.getmembers(source, predicate=callable):
        if name.startswith("__"):
            continue
        params = set(inspect.signature(member).parameters)
        assert not (params & banned), f"{name} exposes a caller time override: {params & banned}"
    # the module builders likewise take no caller as_of/strict override on the
    # source itself (time authority is the decision point / context only).
    for fn in (ReplayPointClock.__init__,):
        assert "strict" not in inspect.signature(fn).parameters


# =========================================================================== #
# 10. manifest semantic/audit split (physical root is audit-only)              #
# =========================================================================== #
def test_manifest_semantic_audit_split(schema_registry):
    common = dict(
        store_meta=_STORE_META, as_of=AS_OF, timezone="Asia/Shanghai",
        calendar_id="cn_a_share", routing_snapshot_digest="e" * 64,
        schema_registry_digest="f" * 64)
    m1 = build_replay_manifest(data_snapshot_id="pit-store-root-A", **common)
    m2 = build_replay_manifest(data_snapshot_id="pit-store-root-B", **common)
    assert isinstance(m1, DataSnapshotManifest)
    assert m1.manifest_kind == "pit_frozen"
    assert m1.mode is DataMode.PIT_REPLAY
    # semantic identity is stable across a physical-root relocation ...
    assert m1.content_digest == m2.content_digest
    assert m1.semantic_digest() == m2.semantic_digest()
    # ... but audit (dereference) identity differs.
    assert m1.audit_digest_value() != m2.audit_digest_value()
    assert m1.data_snapshot_id != m2.data_snapshot_id

    # a coverage-floor change moves the SEMANTIC digest (facts are digest-bearing).
    m3 = build_replay_manifest(
        data_snapshot_id="pit-store-root-A",
        store_meta={**_STORE_META, "news_coverage_floor": "2019-01-02"},
        as_of=AS_OF, timezone="Asia/Shanghai", calendar_id="cn_a_share",
        routing_snapshot_digest="e" * 64, schema_registry_digest="f" * 64)
    assert m3.content_digest != m1.content_digest


# =========================================================================== #
# 11. the registered descriptor is PIT_REPLAY-only over existing method ids     #
# =========================================================================== #
def test_source_descriptor_pit_only():
    surface = phase3_data_surface()
    desc = build_pit_replay_descriptor(method_specs=surface.method_specs, handler_ref=_HANDLER)
    assert isinstance(desc, DataSourceDescriptor)
    assert desc.supported_modes == (DataMode.PIT_REPLAY,)
    assert desc.supported_backends == (DataBackend.PIT_STORE,)

    bound = {s.method_id for s in surface.method_specs if s.method_ref in desc.method_refs}
    assert bound == {"ohlcv", "news"}
    # every bound method ref is an EXISTING Phase-3 method id (no new id minted).
    existing = {s.method_ref for s in surface.method_specs}
    assert set(desc.method_refs) <= existing
    # every bound method spec is read-only.
    for spec in surface.method_specs:
        if spec.method_id in bound:
            assert spec.read_only is True


# =========================================================================== #
# extra: daily-bar visibility (the ohlcv daily surface is exercised too)        #
# =========================================================================== #
def test_daily_bar_visible(schema_registry):
    reader = _FakeReader(day=[_bar("2026-07-15", close=1750.0)])
    world = _ReplayWorld(schema_registry, method_id="ohlcv",
                         params=_OHLCV_DAILY_PARAMS, fake_reader=reader)
    cands = world.fetch().candidates
    assert len(cands) == 1
    assert cands[0].available_at is not None and cands[0].available_at <= AS_OF
    passed, audit = world.check()
    assert len(passed) == 1 and audit.guard_result == "passed"


# =========================================================================== #
# extra: the default (no-injection) factory lazily targets the engine PitReader #
# =========================================================================== #
def test_default_factory_wires_engine_pit_reader():
    # structural: the default source declares NO reader and never touches disk at
    # construction; the lazy import target is the engine PitReader (never loaded
    # here — asserting the wiring, not real data).
    src = PitReaderRawSource()
    assert src._reader is None  # nothing loaded eagerly
    source_code = inspect.getsource(PitReaderRawSource)
    assert "financial_analyst.backtest.pit_reader" in source_code
    assert "import PitReader" in source_code
