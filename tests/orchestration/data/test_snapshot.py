# -*- coding: utf-8 -*-
"""Phase 3 · Task 5 — routing / snapshot / cache ABI + ``build_data_context`` tests.

Covers brief items 7–10: builders reject secrets / unknown methods / drifted
digests / unordered declarations; a future revision never changes an older PIT
manifest digest and an online capture root never mutates; every semantic
cache-key dimension is sensitive while locator relocation is invariant; and a
PIT_REPLAY cache miss can continue only to an explicitly frozen PIT_STORE.

Run: ``pytest tests/orchestration/data/test_snapshot.py -v``
"""
from __future__ import annotations

from datetime import date, datetime, timezone

import pytest
from pydantic import ValidationError

from guanlan_v2.orchestration.data.calendar import (
    TradingCalendarResolver,
    build_trading_calendar,
)
from guanlan_v2.orchestration.data.catalog import phase3_data_surface
from guanlan_v2.orchestration.data.errors import (
    CacheIntegrityError,
    LiveFallbackRefused,
    RoutingConfigurationError,
    SnapshotMismatchError,
)
from guanlan_v2.orchestration.data.pit import FreshnessPolicy
from guanlan_v2.orchestration.data.result import PitAudit, SourceAttempt
from guanlan_v2.orchestration.data.schema_registry import build_phase3_registry
from guanlan_v2.orchestration.data.snapshot import (
    DataCache,
    DataCacheEntry,
    DataCacheKey,
    DataRoutingSnapshot,
    DataSnapshotEntry,
    DataSnapshotManifest,
    DataSourceConfigSnapshot,
    build_data_cache_entry,
    build_data_cache_key,
    build_data_context,
    build_data_routing_snapshot,
    build_data_snapshot_manifest,
    build_data_source_config_snapshot,
    cache_miss_continuation,
)
from guanlan_v2.orchestration.data.source import (
    DataPolicyResolver,
    DataSourceRegistrySnapshot,
    ResolvedMethodRoute,
    RouteEntry,
    VerifiedDataCacheHit,
    build_data_result,
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
    phase2_runtime_registry,
)

UTC = timezone.utc
AS_OF = datetime(2026, 7, 16, 7, 0, tzinfo=UTC)


class FixedClock:
    def __init__(self, at: datetime) -> None:
        self._at = at

    def now(self) -> datetime:
        return self._at


@pytest.fixture(scope="module")
def registry():
    ph2 = phase2_runtime_registry(PHASE2_BASE_REGISTRY_DIGEST)
    return build_phase3_registry(ph2.registry_digest)


@pytest.fixture(scope="module")
def surface():
    return phase3_data_surface()


@pytest.fixture(scope="module")
def source_registry(surface):
    return DataSourceRegistrySnapshot.build(
        registry_version="test-v1",
        method_specs=surface.method_specs,
        source_descriptors=(surface.source_descriptor,),
        default_routes=tuple(
            ResolvedMethodRoute(
                method_ref=s.method_ref,
                entries=(RouteEntry(source_ref=surface.source_ref,
                                    capability_ref=s.capability_ref),),
                route_policy_ref=ContentRef(id="policy.route.default", version="1",
                                            content_digest="d" * 64),
            )
            for s in surface.method_specs
        ),
        freshness_policies=(surface.freshness_policy,),
    )


@pytest.fixture(scope="module")
def config():
    return build_data_source_config_snapshot(
        config_version="cfg-1",
        method_selections={"ohlcv": "guanlan.datafeed"},
        source_options={"guanlan.datafeed": {"host_pool": "primary", "retries": 2}},
    )


@pytest.fixture(scope="module")
def routing(source_registry, config, registry):
    return build_data_routing_snapshot(
        audit_id="route-audit-1",
        source_registry=source_registry,
        schema_registry_digest=registry.registry_digest,
        source_config=config,
        method_routes=source_registry.default_routes,
    )


def _entry(*, dataset="prices-daily", method="ohlcv", revision=None,
           digest="1" * 64) -> DataSnapshotEntry:
    return DataSnapshotEntry(
        dataset_id=dataset, method_id=method, source_id="guanlan.datafeed",
        revision_id=revision, payload_schema_ref=SchemaRef(name="OHLCVRows", version="1"),
        content_digest=digest, max_available_at=datetime(2026, 7, 15, tzinfo=UTC),
    )


def _manifest(routing, registry, *, kind="pit_frozen", mode=DataMode.PIT_REPLAY,
              entries=(), snapshot_id="snap-1", as_of=AS_OF) -> DataSnapshotManifest:
    return build_data_snapshot_manifest(
        data_snapshot_id=snapshot_id, manifest_kind=kind, as_of=as_of, mode=mode,
        timezone="Asia/Shanghai", calendar_id="cn_a_share",
        routing_snapshot_digest=routing.routing_digest,
        schema_registry_digest=registry.registry_digest, entries=tuple(entries),
    )


# --------------------------------------------------------------------------- #
# item 7 — builders reject secrets / unknown / drift / disorder                #
# --------------------------------------------------------------------------- #
def test_config_snapshot_rejects_credential_keys():
    with pytest.raises(ValueError, match="credential-looking key"):
        build_data_source_config_snapshot(
            config_version="cfg-1", method_selections={},
            source_options={"vendor": {"api_key": "sk-123"}})
    with pytest.raises(ValueError, match="credential-looking key"):
        build_data_source_config_snapshot(
            config_version="cfg-1", method_selections={},
            source_options={"vendor": {"nested": [{"Password": "x"}]}})


def test_config_snapshot_holds_digests_not_option_values(config):
    assert "host_pool" not in str(config.model_dump())
    fields = {n: getattr(config, n) for n in type(config).model_fields
              if n != "source_config_digest"}
    with pytest.raises(ValidationError):
        DataSourceConfigSnapshot(**fields, source_config_digest="0" * 64)


def test_routing_rejects_unknown_method(source_registry, config, registry):
    bogus = ResolvedMethodRoute(
        method_ref=ContentRef(id="not.a.method", version="1", content_digest="a" * 64),
        entries=(RouteEntry(
            source_ref=ContentRef(id="guanlan.datafeed", version="1", content_digest="b" * 64),
            capability_ref=source_registry.method_specs[0].capability_ref),),
        route_policy_ref=ContentRef(id="policy.route.default", version="1",
                                    content_digest="d" * 64))
    with pytest.raises(RoutingConfigurationError):
        build_data_routing_snapshot(
            audit_id="x", source_registry=source_registry,
            schema_registry_digest=registry.registry_digest,
            source_config=config, method_routes=(bogus,))


def test_routing_snapshot_is_the_sole_chain_producer(routing):
    # chains derive from the EXPLICIT ordered route entries.
    assert routing.resolved_vendor_chains["ohlcv"] == ("guanlan.datafeed",)
    tampered = {n: getattr(routing, n) for n in type(routing).model_fields
                if n != "routing_digest"}
    tampered["resolved_vendor_chains"] = {**routing.resolved_vendor_chains,
                                          "ohlcv": ("someone.else",)}
    with pytest.raises(ValidationError, match="derived from the ordered method routes"):
        DataRoutingSnapshot(**tampered, routing_digest=routing.routing_digest)


def test_routing_audit_id_is_audit_only(source_registry, config, registry):
    a = build_data_routing_snapshot(
        audit_id="audit-a", source_registry=source_registry,
        schema_registry_digest=registry.registry_digest, source_config=config,
        method_routes=source_registry.default_routes)
    b = build_data_routing_snapshot(
        audit_id="audit-b", source_registry=source_registry,
        schema_registry_digest=registry.registry_digest, source_config=config,
        method_routes=source_registry.default_routes)
    assert a.routing_digest == b.routing_digest


def test_source_registry_rejects_duplicates_and_disorder(surface):
    with pytest.raises(ValidationError, match="unique"):
        DataSourceRegistrySnapshot.build(
            registry_version="v", method_specs=surface.method_specs * 2,
            source_descriptors=(surface.source_descriptor,), default_routes=())
    ordered = tuple(sorted(surface.method_specs, key=lambda s: s.method_id))
    good = DataSourceRegistrySnapshot.build(
        registry_version="v", method_specs=ordered,
        source_descriptors=(surface.source_descriptor,), default_routes=())
    with pytest.raises(ValidationError, match="sorted"):
        DataSourceRegistrySnapshot(
            registry_version="v", method_specs=tuple(reversed(ordered)),
            source_descriptors=(surface.source_descriptor,), default_routes=(),
            source_registry_digest=good.source_registry_digest)


def test_manifest_rejects_disorder_and_duplicates(routing, registry):
    e1, e2 = _entry(dataset="a-set"), _entry(dataset="b-set")
    good = _manifest(routing, registry, entries=(e1, e2))
    with pytest.raises(ValidationError, match="sorted"):
        DataSnapshotManifest(
            **{n: getattr(good, n) for n in type(good).model_fields
               if n not in ("entries",)} | {"entries": (e2, e1)})
    with pytest.raises(ValidationError, match="duplicate"):
        _manifest(routing, registry, entries=(e1, e1))


def test_pit_replay_manifest_must_be_pit_frozen(routing, registry):
    with pytest.raises(ValidationError, match="pit_frozen"):
        _manifest(routing, registry, kind="online_capture_root", mode=DataMode.PIT_REPLAY)


# --------------------------------------------------------------------------- #
# build_data_context — exact digest/as-of/calendar consistency                 #
# --------------------------------------------------------------------------- #
def test_build_data_context_pit_replay_happy_path(config, source_registry, routing, registry):
    manifest = _manifest(routing, registry, entries=(_entry(),))
    ctx = build_data_context(
        FixedClock(AS_OF), mode=DataMode.PIT_REPLAY, backend=DataBackend.PIT_STORE,
        source_config=config, source_registry=source_registry, routing=routing,
        manifest=manifest)
    assert ctx.strict_pit is True
    assert ctx.vintage_manifest_digest == manifest.vintage_manifest_digest
    assert ctx.data_snapshot_content_digest == manifest.content_digest
    assert ctx.resolved_vendor_chains == dict(routing.resolved_vendor_chains)
    assert ctx.source_config_digest == config.source_config_digest
    assert ctx.source_registry_digest == source_registry.source_registry_digest
    assert ctx.routing_snapshot_digest == routing.routing_digest
    assert ctx.calendar_id == "cn_a_share"


def test_build_data_context_online_uses_capture_root(config, source_registry, routing, registry):
    manifest = _manifest(routing, registry, kind="online_capture_root", mode=DataMode.ONLINE)
    ctx = build_data_context(
        FixedClock(AS_OF), mode=DataMode.ONLINE, backend=DataBackend.LIVE,
        source_config=config, source_registry=source_registry, routing=routing,
        manifest=manifest)
    assert ctx.strict_pit is False and ctx.vintage_manifest_digest is None
    # a pit_frozen manifest is NOT an online capture root.
    frozen = _manifest(routing, registry, kind="pit_frozen", mode=DataMode.ONLINE)
    with pytest.raises(SnapshotMismatchError, match="online_capture_root"):
        build_data_context(
            FixedClock(AS_OF), mode=DataMode.ONLINE, backend=DataBackend.LIVE,
            source_config=config, source_registry=source_registry, routing=routing,
            manifest=frozen)


def test_build_data_context_rejects_drift(config, source_registry, routing, registry):
    manifest = _manifest(routing, registry)
    with pytest.raises(SnapshotMismatchError, match="as_of"):
        build_data_context(
            FixedClock(datetime(2026, 7, 17, tzinfo=UTC)), mode=DataMode.PIT_REPLAY,
            backend=DataBackend.PIT_STORE, source_config=config,
            source_registry=source_registry, routing=routing, manifest=manifest)
    with pytest.raises(LiveFallbackRefused):
        build_data_context(
            FixedClock(AS_OF), mode=DataMode.PIT_REPLAY, backend=DataBackend.LIVE,
            source_config=config, source_registry=source_registry, routing=routing,
            manifest=manifest)
    other_config = build_data_source_config_snapshot(
        config_version="cfg-2", method_selections={}, source_options={})
    with pytest.raises(SnapshotMismatchError, match="source_config"):
        build_data_context(
            FixedClock(AS_OF), mode=DataMode.PIT_REPLAY, backend=DataBackend.PIT_STORE,
            source_config=other_config, source_registry=source_registry,
            routing=routing, manifest=manifest)


# --------------------------------------------------------------------------- #
# item 8 — vintage immutability                                                #
# --------------------------------------------------------------------------- #
def test_future_revision_never_changes_an_older_manifest(routing, registry):
    older = _manifest(routing, registry, entries=(_entry(revision="r1"),))
    frozen_digest = older.content_digest
    newer = _manifest(routing, registry,
                      entries=(_entry(revision="r1"), _entry(revision="r2", digest="2" * 64)))
    assert newer.content_digest != frozen_digest
    # the older manifest re-validates bit-for-bit — nothing mutated it.
    assert DataSnapshotManifest(**older.model_dump()).content_digest == frozen_digest


def test_online_capture_root_is_append_stable(routing, registry, surface):
    root = _manifest(routing, registry, kind="online_capture_root", mode=DataMode.ONLINE)
    frozen_digest = root.content_digest
    # recording a result later happens UNDER the root (per-result evidence),
    # never inside it: the root model carries no result field to mutate.
    assert not any("result" in name for name in type(root).model_fields)
    assert root.content_digest == frozen_digest


def test_manifest_locator_is_audit_only(routing, registry):
    a = _manifest(routing, registry, snapshot_id="snap-a")
    b = _manifest(routing, registry, snapshot_id="snap-b")
    assert a.content_digest == b.content_digest
    assert a.audit_digest_value() != b.audit_digest_value()


# --------------------------------------------------------------------------- #
# item 9 — cache key semantics                                                 #
# --------------------------------------------------------------------------- #
def _key(**over) -> DataCacheKey:
    fields = dict(
        method_ref=ContentRef(id="ohlcv", version="1", content_digest="a" * 64),
        source_ref=ContentRef(id="guanlan.datafeed", version="1", content_digest="b" * 64),
        result_schema_ref=SchemaRef(name="OHLCVDataResult", version="1"),
        params_digest="c" * 64,
        as_of=AS_OF,
        mode=DataMode.PIT_REPLAY,
        backend=DataBackend.PIT_STORE,
        data_snapshot_content_digest="d" * 64,
        vintage_manifest_digest="e" * 64,
        source_config_digest="f" * 64,
        routing_snapshot_digest="1" * 64,
        source_registry_digest="2" * 64,
        schema_registry_digest="3" * 64,
    )
    fields.update(over)
    return build_data_cache_key(**fields)


@pytest.mark.parametrize(
    "over",
    [
        {"params_digest": "9" * 64},
        {"as_of": datetime(2026, 7, 17, tzinfo=UTC)},
        {"mode": DataMode.ONLINE},
        {"backend": DataBackend.CACHE},
        {"data_snapshot_content_digest": "9" * 64},
        {"vintage_manifest_digest": None},
        {"source_config_digest": "9" * 64},
        {"routing_snapshot_digest": "9" * 64},
        {"source_registry_digest": "9" * 64},
        {"schema_registry_digest": "9" * 64},
        {"result_schema_ref": SchemaRef(name="NewsDataResult", version="1")},
        {"method_ref": ContentRef(id="news", version="1", content_digest="a" * 64)},
        {"source_ref": ContentRef(id="other.vendor", version="1", content_digest="b" * 64)},
    ],
)
def test_every_semantic_cache_dimension_is_sensitive(over):
    assert _key(**over).key_digest != _key().key_digest


def test_cache_key_never_uses_the_snapshot_locator():
    assert "data_snapshot_id" not in DataCacheKey.model_fields


def test_cache_entry_object_id_relocation_is_semantically_invariant():
    key = _key()

    def entry(object_id: str, audit: str) -> DataCacheEntry:
        return build_data_cache_entry(
            key=key,
            result_ref=TypedPayloadRef(
                schema_ref=SchemaRef(name="OHLCVDataResult", version="1"),
                payload_ref=PayloadRef(namespace="main", object_id=object_id,
                                       content_digest="4" * 64)),
            result_content_digest="4" * 64,
            result_audit_digest=audit,
            pit_audit_digest="5" * 64,
        )

    a = entry("obj-1", "6" * 64)
    b = entry("obj-relocated", "7" * 64)  # audit digest + locator moved only
    assert a.entry_digest == b.entry_digest
    # but the semantic result/PIT identity IS sensitive.
    c = build_data_cache_entry(
        key=key,
        result_ref=TypedPayloadRef(
            schema_ref=SchemaRef(name="OHLCVDataResult", version="1"),
            payload_ref=PayloadRef(namespace="main", object_id="obj-1",
                                   content_digest="9" * 64)),
        result_content_digest="9" * 64, result_audit_digest="6" * 64,
        pit_audit_digest="5" * 64)
    assert c.entry_digest != a.entry_digest


def test_cache_entry_rejects_detached_schema_or_namespace():
    key = _key()
    with pytest.raises(ValidationError, match="schema"):
        build_data_cache_entry(
            key=key,
            result_ref=TypedPayloadRef(
                schema_ref=SchemaRef(name="NewsDataResult", version="1"),
                payload_ref=PayloadRef(namespace="main", object_id="o",
                                       content_digest="4" * 64)),
            result_content_digest="4" * 64, result_audit_digest="6" * 64,
            pit_audit_digest="5" * 64)
    with pytest.raises(ValidationError, match="main"):
        build_data_cache_entry(
            key=key,
            result_ref=TypedPayloadRef(
                schema_ref=SchemaRef(name="OHLCVDataResult", version="1"),
                payload_ref=PayloadRef(namespace="audit", object_id="o",
                                       content_digest="4" * 64)),
            result_content_digest="4" * 64, result_audit_digest="6" * 64,
            pit_audit_digest="5" * 64)


# --------------------------------------------------------------------------- #
# item 10 — verified cache conformance fake + replay continuation              #
# --------------------------------------------------------------------------- #
class InMemoryVerifiedCache:
    """The in-memory conformance fake proving the :class:`DataCache` ABI."""

    def __init__(self) -> None:
        self._store: dict[str, tuple[DataCacheEntry, object]] = {}

    def put_verified(self, key, result_ref, *, result, pit_audit) -> None:
        entry = build_data_cache_entry(
            key=key, result_ref=result_ref,
            result_content_digest=result.content_digest,
            result_audit_digest=result.audit_digest,
            pit_audit_digest=content_digest(pit_audit))
        self._store[key.key_digest] = (entry, result)

    def get_verified(self, key, *, ctx, manifest, registry, payload_store):
        hit = self._store.get(key.key_digest)
        if hit is None:
            return None
        entry, result = hit
        # re-run every bound digest + context/PIT compatibility check.
        if key.data_snapshot_content_digest != manifest.content_digest:
            raise CacheIntegrityError("cache key snapshot-content digest mismatch")
        if key.mode is not ctx.mode or key.backend is not ctx.backend:
            raise CacheIntegrityError("cache key mode/backend does not match the context")
        if key.vintage_manifest_digest != ctx.vintage_manifest_digest:
            raise CacheIntegrityError("cache key vintage digest mismatch")
        if entry.result_content_digest != result.content_digest:
            raise CacheIntegrityError("stored result content digest mismatch")
        if type(result).__name__ != entry.result_ref.schema_ref.name:
            raise CacheIntegrityError("stored result schema mismatch")
        registry.resolve(entry.result_ref.schema_ref)
        return VerifiedDataCacheHit(result=result, result_ref=entry.result_ref)


def _pit_result(registry, surface):
    from guanlan_v2.orchestration.data.pit import RawRowCandidate
    from guanlan_v2.orchestration.data.source import OHLCVRecord, OHLCVRows

    cand = RawRowCandidate.build(
        raw_payload={"symbol": {"code": "600519", "exchange": "SH", "board": "main"},
                     "open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5, "volume": 100},
        effective_at=datetime(2026, 7, 1, tzinfo=UTC),
        available_at=datetime(2026, 7, 2, tzinfo=UTC),
        ingested_at=datetime(2026, 7, 2, tzinfo=UTC))
    batch = OHLCVRows.build([OHLCVRecord.from_candidate(cand)])
    return build_data_result(
        surface.spec_by_method["ohlcv"], registry=registry, id="res-1",
        request_digest="a" * 64, status=DataStatus.OK, data=batch,
        resolved_vendor_chain=("guanlan.datafeed",), source_config_digest="a" * 64,
        fetched_at=AS_OF,
        attempts=(SourceAttempt(vendor="guanlan.datafeed", configured=True,
                                outcome="success", started_at=AS_OF, finished_at=AS_OF),),
        pit_audit=PitAudit(mode=DataMode.PIT_REPLAY, as_of=AS_OF, rows_seen=1,
                           rows_returned=1, future_rows=0,
                           missing_available_at_rows=0, guard_result="passed"))


def test_verified_cache_hit_preserves_the_typed_identity(
        registry, surface, config, source_registry, routing):
    cache = InMemoryVerifiedCache()
    assert isinstance(cache, DataCache)  # ABI conformance
    manifest = _manifest(routing, registry, entries=(_entry(),))
    ctx = build_data_context(
        FixedClock(AS_OF), mode=DataMode.PIT_REPLAY, backend=DataBackend.PIT_STORE,
        source_config=config, source_registry=source_registry, routing=routing,
        manifest=manifest)
    result = _pit_result(registry, surface)
    key = _key(data_snapshot_content_digest=manifest.content_digest,
               vintage_manifest_digest=manifest.vintage_manifest_digest)
    ref = TypedPayloadRef(
        schema_ref=SchemaRef(name="OHLCVDataResult", version="1"),
        payload_ref=PayloadRef(namespace="main", object_id="obj-1",
                               content_digest=result.content_digest))
    cache.put_verified(key, ref, result=result, pit_audit=result.pit_audit)
    hit = cache.get_verified(key, ctx=ctx, manifest=manifest, registry=registry,
                             payload_store=None)
    assert isinstance(hit, VerifiedDataCacheHit)
    assert hit.result is result and hit.result_ref == ref


def test_cache_hit_with_mismatched_snapshot_digest_is_rejected(
        registry, surface, config, source_registry, routing):
    cache = InMemoryVerifiedCache()
    manifest = _manifest(routing, registry, entries=(_entry(),))
    other = _manifest(routing, registry, entries=(_entry(), _entry(dataset="z-set")))
    ctx = build_data_context(
        FixedClock(AS_OF), mode=DataMode.PIT_REPLAY, backend=DataBackend.PIT_STORE,
        source_config=config, source_registry=source_registry, routing=routing,
        manifest=manifest)
    result = _pit_result(registry, surface)
    key = _key(data_snapshot_content_digest=manifest.content_digest,
               vintage_manifest_digest=manifest.vintage_manifest_digest)
    ref = TypedPayloadRef(
        schema_ref=SchemaRef(name="OHLCVDataResult", version="1"),
        payload_ref=PayloadRef(namespace="main", object_id="obj-1",
                               content_digest=result.content_digest))
    cache.put_verified(key, ref, result=result, pit_audit=result.pit_audit)
    with pytest.raises(CacheIntegrityError):
        cache.get_verified(key, ctx=ctx, manifest=other, registry=registry,
                           payload_store=None)


def test_pit_replay_miss_reaches_only_a_frozen_pit_store(
        config, source_registry, routing, registry):
    manifest = _manifest(routing, registry)
    replay_ctx = build_data_context(
        FixedClock(AS_OF), mode=DataMode.PIT_REPLAY, backend=DataBackend.PIT_STORE,
        source_config=config, source_registry=source_registry, routing=routing,
        manifest=manifest)
    assert cache_miss_continuation(replay_ctx) == "pit_store"

    cache_ctx = build_data_context(
        FixedClock(AS_OF), mode=DataMode.PIT_REPLAY, backend=DataBackend.CACHE,
        source_config=config, source_registry=source_registry, routing=routing,
        manifest=manifest)
    assert cache_miss_continuation(cache_ctx) == "stop"  # no continuation

    # LIVE replay is structurally impossible via the builder; the continuation
    # guard refuses even a mis-built (validator-bypassing) context.
    from guanlan_v2.orchestration.context import DataContext

    misbuilt = DataContext.model_construct(
        **{**replay_ctx.__dict__, "backend": DataBackend.LIVE})
    with pytest.raises(LiveFallbackRefused):
        cache_miss_continuation(misbuilt)


def test_online_cache_backend_has_no_continuation(
        config, source_registry, routing, registry):
    manifest = _manifest(routing, registry, kind="online_capture_root", mode=DataMode.ONLINE)
    ctx = build_data_context(
        FixedClock(AS_OF), mode=DataMode.ONLINE, backend=DataBackend.CACHE,
        source_config=config, source_registry=source_registry, routing=routing,
        manifest=manifest)
    assert cache_miss_continuation(ctx) == "stop"


# --------------------------------------------------------------------------- #
# item 7 (policy material) — DataPolicyResolver over the sealed snapshot        #
# --------------------------------------------------------------------------- #
def _calendar():
    return build_trading_calendar(
        calendar_id="cn_a_share",
        sessions=[date(2026, 7, d) for d in (13, 14, 15, 16)],
        material_id="calendar.cn.a", material_version="1")


def _session_policy(cal) -> FreshnessPolicy:
    return FreshnessPolicy.build(
        policy_id="policy.freshness.session", policy_version="1",
        method_or_category="prices", max_trading_sessions=2,
        calendar_id=cal.calendar_id, calendar_material_ref=cal.material_ref)


def test_policy_resolver_binds_the_exact_session_calendar(surface, config, source_registry,
                                                          routing, registry):
    cal = _calendar()
    policy = _session_policy(cal)
    spec = surface.spec_by_method["ohlcv"].model_copy(deep=True)
    # a spec naming the session policy by exact versioned ref
    from guanlan_v2.orchestration.data.source import DataMethodSpec

    fields = {n: getattr(spec, n) for n in type(spec).model_fields if n != "spec_digest"}
    fields["freshness_policy_ref"] = ContentRef(
        id=policy.policy_id, version=policy.policy_version,
        content_digest=policy.policy_digest)
    spec = DataMethodSpec.build(**fields)

    snapshot = DataSourceRegistrySnapshot.build(
        registry_version="v", method_specs=(spec,),
        source_descriptors=(surface.source_descriptor,), default_routes=(),
        freshness_policies=(policy,))
    resolver = DataPolicyResolver(snapshot,
                                  calendar_resolver=TradingCalendarResolver([cal]))
    manifest = _manifest(routing, registry, entries=(_entry(),))
    ctx = build_data_context(
        FixedClock(AS_OF), mode=DataMode.PIT_REPLAY, backend=DataBackend.PIT_STORE,
        source_config=config, source_registry=source_registry, routing=routing,
        manifest=manifest)
    bundle = resolver.resolve_method(spec, ctx=ctx)
    assert bundle.freshness_policy == policy
    assert bundle.calendar_id == "cn_a_share"
    assert bundle.calendar_material_ref == cal.material_ref
    assert bundle.policy_bundle_digest  # sealed


def test_policy_resolver_fails_loud_on_missing_or_drifted_material(surface, config,
                                                                   source_registry,
                                                                   routing, registry):
    cal = _calendar()
    policy = _session_policy(cal)
    from guanlan_v2.orchestration.data.source import DataMethodSpec

    base = surface.spec_by_method["ohlcv"]
    fields = {n: getattr(base, n) for n in type(base).model_fields if n != "spec_digest"}
    fields["freshness_policy_ref"] = ContentRef(
        id=policy.policy_id, version=policy.policy_version,
        content_digest="9" * 64)  # drifted material digest
    drifted_spec = DataMethodSpec.build(**fields)

    snapshot = DataSourceRegistrySnapshot.build(
        registry_version="v", method_specs=(drifted_spec,),
        source_descriptors=(surface.source_descriptor,), default_routes=(),
        freshness_policies=(policy,))
    resolver = DataPolicyResolver(snapshot,
                                  calendar_resolver=TradingCalendarResolver([cal]))
    manifest = _manifest(routing, registry, entries=(_entry(),))
    ctx = build_data_context(
        FixedClock(AS_OF), mode=DataMode.PIT_REPLAY, backend=DataBackend.PIT_STORE,
        source_config=config, source_registry=source_registry, routing=routing,
        manifest=manifest)
    with pytest.raises(ValueError, match="drift"):
        resolver.resolve_method(drifted_spec, ctx=ctx)

    # missing policy material fails at resolution, never a module-global fallback.
    empty = DataSourceRegistrySnapshot.build(
        registry_version="v", method_specs=(drifted_spec,),
        source_descriptors=(surface.source_descriptor,), default_routes=(),
        freshness_policies=())
    missing_resolver = DataPolicyResolver(empty,
                                          calendar_resolver=TradingCalendarResolver([cal]))
    with pytest.raises(ValueError, match="no freshness policy"):
        missing_resolver.resolve_method(drifted_spec, ctx=ctx)
