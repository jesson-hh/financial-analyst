# -*- coding: utf-8 -*-
"""L2-b Task 3 — ``ProductionDataWorldResolver`` + ``ProductionDataProvider``
(plan: docs/superpowers/plans/2026-07-31-orchestration-L2b-data-runtime.md,
Task 3; gate corrections: .superpowers/sdd/task-L2b-0-handoff-gate-report.md
D-A/D-C/D-D).

The provider that replaces the worldless stopgap. Pinned here:

* the per-run resolver ``world_for``: admitted-snapshot read → three loud typed
  digest checks (a pilot-era context refuses with the re-run-Lane-0 remedy) →
  the manifest via the D-D payload-store channel (present/absent arms; absent
  is a typed refusal, never a rebuilt stand-in) → the full ``DataRuntimeWorld``
  with the gate D-C frozen clock at exactly ``ctx.as_of``;
* ``ProductionDataProvider``: prepare delegated bit-for-bit (never a third
  copy); ``open_execution`` partitions on ROW COUNT ONLY — zero rows freeze the
  catalog-licensed EMPTY, rows delegate the WHOLE session to the real
  ``DataRuntimeBridgeProvider`` (zero re-implementation, source-text pinned)
  via its own ``data_runtime_provider_factory`` (L2-b Task 4's conscious flip
  of the source pin — the single-symbol fact-F enumeration);
* (L2-b Task 4) the resolver's early manifest-locator refusal and the ONE
  hoisted data-aware refusal audit sink per binding set;
* (L2-b Task 5, the L1<->L2-b integration seam) the run-subject source: the
  provider carries ``subject_params`` into the delegated session, and
  ``production_data_provider_factory`` is the ONE construction recipe the
  per-run ``assembly._SubjectScopedFactories`` view overrides with. Pinned in
  BOTH directions — a subject-bound view READS the sealed ``dec.pm`` shape for
  real, and the same shape WITHOUT a view refuses at the runner seam (the
  permanent process-level posture: only a per-run view may bind a subject, so
  this pin stopped being order-conditional and became an invariant);
* the synthetic live-row END-TO-END read through the delegated real session
  over an injected fake probe (fabricated catalog in the
  ``test_runtime_integration.py`` idiom — faithful adaptation recorded): one
  ``ToolCallRecord``, PIT-audited result, rendered untrusted block for an LLM
  kind — the delegation seam carries the whole Phase-3 machinery;
* PIT honesty at the seam (``FutureDataRefused`` / ``MissingAvailabilityRefused``
  — the guard, not the adapter, refuses) and the degradation matrix
  (optional-UNAVAILABLE vs core-raise, both against the real dispatch).

Harness provenance (recorded per the task brief): the reduced-preset fixtures
are faithful copies of ``tests/orchestration/test_pm_two_bridges.py``'s module
fixtures (env/bundle/view/reduced + the registered-factories recipe, adapted
to register THIS task's production recipe); the synthetic-catalog builder is a
faithful, trimmed adaptation of
``tests/orchestration/data/test_runtime_integration.py::build_data_env``
(admission service dropped — plan identity is carried by
``compute_candidate_plan_digest`` over the validated draft; support comes from
the real ``check_runtime_support``).

Zero network (every probe is an injected fake through the existing
``adapters/live_data.py`` ports), zero LLM, zero ``var/`` writes.

Run: ``python -m pytest tests/orchestration/test_production_data_provider.py -q``
"""
from __future__ import annotations

import ast
import inspect
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

import guanlan_v2.orchestration.adapters.data_world as DW
import guanlan_v2.orchestration.bootstrap as bs
import guanlan_v2.orchestration.data.runtime as RT
import guanlan_v2.orchestration.memory.runtime as MR
import guanlan_v2.orchestration.worker as W
from guanlan_v2.orchestration import presets as P
from guanlan_v2.orchestration.adapters import chain
from guanlan_v2.orchestration.adapters.data_world import (
    ProductionDataProvider,
    ProductionDataWorldResolver,
    ProductionNoCache,
    production_data_provider_factory,
    production_data_recipe,
    register_production_data_provider,
)
from guanlan_v2.orchestration.adapters.live_data import (
    LiveClientSource,
    build_online_capture_manifest,
    build_online_data_context,
)
from guanlan_v2.orchestration.catalog import (
    CapabilityManifestEntry,
    ContentManifestEntry,
    EvidencePolicy,
    ExecutionSpec,
    OutputBinding,
    WorkerSpec,
    build_catalog_snapshot,
)
from guanlan_v2.orchestration.catalog_runtime import (
    BridgeCatalogView,
    CatalogRuntime,
    InMemoryMaterialSource,
    TrustedFactoryRegistry,
    build_text_material,
    serialize_bridge_descriptor,
)
from guanlan_v2.orchestration.context import ContextSnapshot, build_empty_memory_binding
from guanlan_v2.orchestration.data.catalog import (
    DATA_BRIDGE_ID,
    DataBridgePrefetchBinding,
    DataBridgeSupportAnalyzer,
    DataPrefetchOperation,
    ParamBinding,
    data_capability_refs,
    phase3_data_surface,
    serialize_prefetch_binding,
)
from guanlan_v2.orchestration.data.errors import (
    FutureDataRefused,
    MissingAvailabilityRefused,
    NotConfiguredError,
)
from guanlan_v2.orchestration.data.pit import DataFetchRefusalDetails
from guanlan_v2.orchestration.data.schema_registry import PHASE3_PUBLIC_MODELS
from guanlan_v2.orchestration.enums import (
    ApprovalPolicy,
    DataMode,
    DataStatus,
    ExecutionKind,
    PlanSource,
    Tier,
    ToolCallRequirement,
)
from guanlan_v2.orchestration.eventstore import (
    EventRefusalAuditSink,
    RuntimeStores,
    SchemaRegistryResolver,
)
from guanlan_v2.orchestration.memory.catalog import phase3_memory_surface
from guanlan_v2.orchestration.pipeline.assembly import (
    PRODUCTION_PRESETS_DIR,
    build_production_catalog_runtime,
    load_phase10_preset_registry,
    production_bridge_view,
)
from guanlan_v2.orchestration.pipeline.contracts import RunSubject
from guanlan_v2.orchestration.pipeline.deep_decide import (
    REDUCED_DEEP_DECIDE_PRESET_ID,
    materialize_deep_decide_draft,
)
from guanlan_v2.orchestration.refs import PayloadRef, SchemaRef, TypedPayloadRef
from guanlan_v2.orchestration.runtime_contracts import (
    Phase2RuntimeRegistry,
    default_audit_detail_registry,
    phase2_public_models,
    phase2_runtime_registry,
)
from guanlan_v2.orchestration.runtime_support import (
    STATIC_RUNTIME_PROFILE_V2,
    check_runtime_support,
)
from guanlan_v2.orchestration.spec import (
    OrchestrationRequest,
    PlanDraft,
    PlanNode,
    compute_candidate_plan_digest,
    validate_plan_draft,
)
from tests.orchestration.test_runtime_support import OtherOut, SR_OUT

UTC = timezone.utc
#: e2e frozen instant: 2026-07-16 07:00Z == 15:00 Asia/Shanghai (A-share close).
AS_OF = datetime(2026, 7, 16, 7, 0, tzinfo=UTC)
#: the reduced-harness instant (the pm-two-bridges fixture instant).
NOW = datetime(2026, 7, 24, 7, 0, tzinfo=UTC)
PHASE8_REGISTRY_DIGEST = (
    "d719e19bc8c64f56324ee36ca0d3aa039e5eac1c9488d80babe6ddce81e5e089"
)
MANIFEST_SR = SchemaRef(name="DataSnapshotManifest", version="1")
CTX_SR = SchemaRef(name="ContextSnapshot", version="1")

#: the L1-deleted helper names, built by concatenation with the split INSIDE
#: the stems (the Task-0 gate's lesson: L1's tombstone pin scans contiguous
#: stems, so this file's own source must never carry them contiguously).
_L1_DELETED_HELPER_NEEDLES = (
    "_row_is_structur" + "ally_dead",
    "structur" + "ally_dead_row_fact",
)

_REPO_ROOT = Path(__file__).resolve().parents[2]


# --------------------------------------------------------------------------- #
# small drivers (faithful copies of the pm-two-bridges / gate idioms)          #
# --------------------------------------------------------------------------- #
class _FixedClock:
    def __init__(self, now: datetime) -> None:
        self._now = now

    def now(self) -> datetime:
        return self._now


class _ForbiddenEvidenceWriter:
    """A path that must never persist evidence — any touch is RED."""

    def put(self, **kwargs):  # pragma: no cover - failing is the assertion
        raise AssertionError("evidence write attempted on a no-write path")

    def record_existing(self, **kwargs):  # pragma: no cover
        raise AssertionError("evidence write attempted on a no-write path")


class _PoisonedStores:
    """The rowless memory path must never touch the stores."""

    def __getattr__(self, name):  # pragma: no cover - failing is the assertion
        raise AssertionError(f"stores.{name} touched by a rowless path")


class _PoisonedResolver:
    """A world resolver that must never be consulted (row-count partition)."""

    def world_for(self, request):  # pragma: no cover - failing is the assertion
        raise AssertionError(
            "world_for consulted on a path that must partition on row count only")


class _ProbeStub:
    """A fake ``live_client.probe`` returning a controlled envelope."""

    def __init__(self, env) -> None:
        self.env = env
        self.calls: list = []

    def __call__(self, source, code="", date="", limit=20, timeout=90):
        self.calls.append({"source": source, "code": code, "date": date,
                           "limit": limit})
        return self.env


def _data_audit_sink(clock) -> EventRefusalAuditSink:
    """The ONE authoritative refusal sink over a data-aware detail registry."""
    reg = default_audit_detail_registry()
    reg.register(DataFetchRefusalDetails)
    reg.seal()
    return EventRefusalAuditSink(detail_registry=reg, clock=clock)


def _sealed_base_detail_registry():
    """The BARE reviewed base registry — no Phase-3 data detail (the shape a
    data refusal cannot be recorded against)."""
    reg = default_audit_detail_registry()
    reg.seal()
    return reg


def _subject_ref(code: str = "833509") -> TypedPayloadRef:
    subject = RunSubject(code=code, as_of=NOW)
    return TypedPayloadRef(
        payload_ref=PayloadRef(
            namespace="main", object_id=f"subject-{code}",
            content_digest=subject.semantic_digest()),
        schema_ref=SchemaRef(name="RunSubject", version="1"))


def _summary_for(report, node_id: str, bridge_id: str):
    mine = [s for s in report.bridge_support_summaries
            if s.bridge_id == bridge_id and s.node_id == node_id]
    assert len(mine) == 1
    return mine[0]


def _recipe_capture(recipe, *, as_of: datetime, snapshot_id: str):
    """A recipe-built (manifest, DataContext) pair — the Lane-0 producer shape
    Task 7 will own, built here directly from the Task-1/9 reviewed builders."""
    manifest = build_online_capture_manifest(
        data_snapshot_id=snapshot_id, as_of=as_of, timezone="Asia/Shanghai",
        calendar_id="cn_a_share",
        routing_snapshot_digest=recipe.routing_snapshot_digest,
        schema_registry_digest=recipe.routing.schema_registry_digest)
    dc = build_online_data_context(
        clock=_FixedClock(as_of), source_config=recipe.source_config,
        source_registry=recipe.registry.snapshot(), routing=recipe.routing,
        manifest=manifest)
    return manifest, dc


# =========================================================================== #
# module fixtures                                                              #
# =========================================================================== #
@pytest.fixture(scope="module")
def reg9():
    return chain.build_phase9_registry(PHASE8_REGISTRY_DIGEST)


@pytest.fixture(scope="module")
def recipe():
    return production_data_recipe()


@pytest.fixture(scope="module")
def snap():
    return chain.phase9_catalog_snapshot()


@pytest.fixture(scope="module")
def bundle(snap):
    return build_production_catalog_runtime(snap)


@pytest.fixture(scope="module")
def view(bundle):
    return production_bridge_view(bundle.runtime)


# =========================================================================== #
# 1. the per-run world resolver (world_for) — unit arms                        #
# =========================================================================== #
def _fresh_stores(reg9, *, now: datetime = AS_OF) -> RuntimeStores:
    resolver = SchemaRegistryResolver()
    resolver.register(reg9)
    return RuntimeStores(resolver=resolver, clock=_FixedClock(now))


def _resolver_under_test(stores, recipe, *, catalog_runtime=None,
                         clock: datetime = AS_OF) -> ProductionDataWorldResolver:
    return ProductionDataWorldResolver(
        stores=stores, recipe=recipe, schema_resolver=stores.resolver,
        clock=_FixedClock(clock),
        catalog_runtime=catalog_runtime if catalog_runtime is not None else object())


def _world_request(dc, *, registry_digest: str):
    """A fabricated open request carrying the admitted snapshot's DataContext
    through EXACTLY the resolution ``_verify_context_binding`` performs."""
    snapshot = SimpleNamespace(data_context=dc)

    class _Reader:
        def get(self, payload_ref, *, expected_schema_ref):
            assert payload_ref == "p-ctx" and expected_schema_ref == "s-ctx"
            return snapshot

    return SimpleNamespace(
        input_snapshot=SimpleNamespace(
            context_snapshot_ref=SimpleNamespace(
                payload_ref="p-ctx", schema_ref="s-ctx")),
        reader=_Reader(),
        plan=SimpleNamespace(schema_registry_digest=registry_digest))


class TestWorldForDigestChecks:
    def test_a_pilot_context_refuses_with_the_rerun_lane0_remedy(
            self, reg9, recipe):
        """The pre-L2-b pilot placeholder digests ("a"/"b"/"c" * 64) fail the
        FIRST digest check loudly, naming the failing pair AND the remedy."""
        stores = _fresh_stores(reg9)
        resolver = _resolver_under_test(stores, recipe)
        dc = P.pilot_data_context(as_of=AS_OF)
        with pytest.raises(RT.DataRuntimeError) as exc:
            resolver.world_for(
                _world_request(dc, registry_digest=reg9.registry_digest))
        msg = str(exc.value)
        assert "source_registry_digest" in msg
        assert "b" * 64 in msg  # the pilot placeholder, named
        assert recipe.source_registry_digest in msg  # the recipe side, named
        assert "predates the production data world" in msg
        assert "re-run Lane 0" in msg

    @pytest.mark.parametrize("field", [
        "source_registry_digest", "routing_snapshot_digest",
        "source_config_digest"])
    def test_each_digest_field_is_verified_individually(
            self, reg9, recipe, field):
        """ONE drifted field on an otherwise recipe-true context refuses,
        naming exactly that pair (the three checks are independent)."""
        stores = _fresh_stores(reg9)
        resolver = _resolver_under_test(stores, recipe)
        _manifest, dc = _recipe_capture(
            recipe, as_of=AS_OF, snapshot_id=f"l2b3-drift-{field}")
        drifted = dc.model_copy(update={field: "e" * 64})
        with pytest.raises(RT.DataRuntimeError) as exc:
            resolver.world_for(
                _world_request(drifted, registry_digest=reg9.registry_digest))
        msg = str(exc.value)
        assert field in msg
        assert "e" * 64 in msg
        assert "re-run Lane 0" in msg


class TestWorldForManifestChannel:
    def test_present_arm_returns_the_full_world(self, reg9, recipe):
        """The D-D payload-store channel, present arm: the persisted manifest
        loads back by ``ctx.data_snapshot_content_digest`` and the returned
        world is the recipe's frozen half + the run's admitted ctx + the gate
        D-C frozen clock + the honest no-cache + the ONE production backend."""
        stores = _fresh_stores(reg9)
        sentinel_runtime = object()
        resolver = _resolver_under_test(
            stores, recipe, catalog_runtime=sentinel_runtime)
        manifest, dc = _recipe_capture(
            recipe, as_of=AS_OF, snapshot_id="l2b3-present-arm")
        stores.payloads.put(
            MANIFEST_SR, manifest, registry_digest=reg9.registry_digest,
            namespace="main", idempotency_key="l2b3:present:manifest")
        world = resolver.world_for(
            _world_request(dc, registry_digest=reg9.registry_digest))
        assert isinstance(world, RT.DataRuntimeWorld)
        assert world.ctx == dc
        assert world.source_registry is recipe.registry
        assert world.routing is recipe.routing
        assert world.source_config is recipe.source_config
        assert world.policy_resolver is recipe.policy_resolver
        assert world.calendar is recipe.calendar
        assert world.manifest.content_digest == dc.data_snapshot_content_digest
        assert world.manifest.content_digest == manifest.content_digest
        # gate D-C: a FROZEN clock reading exactly the admitted ctx.as_of —
        # twice (frozen, never advancing), in ONLINE mode too.
        assert world.clock.now() == dc.as_of
        assert world.clock.now() == dc.as_of
        # honest no-cache + the ONE thread-confined backend singleton.
        assert isinstance(world.cache, ProductionNoCache)
        assert world.cache.get_verified(
            None, ctx=None, manifest=None, registry=None,
            payload_store=None) is None
        assert world.backend is DW.production_data_backend()
        assert world.catalog_runtime is sentinel_runtime
        assert world.schema_resolver is stores.resolver

    def test_absent_arm_refuses_and_never_rebuilds_a_stand_in(
            self, reg9, recipe):
        """The D-D absent arm: no persisted manifest ⇒ typed refusal naming
        the producer gap — never a rebuilt stand-in (a rebuilt manifest could
        not honestly carry the run-start boundary) and zero store writes."""
        stores = _fresh_stores(reg9)
        resolver = _resolver_under_test(stores, recipe)
        _manifest, dc = _recipe_capture(
            recipe, as_of=AS_OF, snapshot_id="l2b3-absent-arm")
        before = stores.payloads_object_count()
        with pytest.raises(RT.DataRuntimeError) as exc:
            resolver.world_for(
                _world_request(dc, registry_digest=reg9.registry_digest))
        msg = str(exc.value)
        assert "manifest not persisted" in msg
        assert "did not run the L2-b recipe" in msg
        assert dc.data_snapshot_content_digest in msg
        assert stores.payloads_object_count() == before  # nothing fabricated

    def test_the_channel_is_keyed_by_the_admitted_plans_registry_digest(
            self, reg9, recipe):
        """Run-scoped key discipline (the D-D ruling): the lookup binds the
        ADMITTED plan's schema-registry digest — a manifest persisted under a
        foreign registry digest is not this run's chain and refuses."""
        stores = _fresh_stores(reg9)
        resolver = _resolver_under_test(stores, recipe)
        manifest, dc = _recipe_capture(
            recipe, as_of=AS_OF, snapshot_id="l2b3-foreign-registry")
        stores.payloads.put(
            MANIFEST_SR, manifest, registry_digest=reg9.registry_digest,
            namespace="main", idempotency_key="l2b3:foreign:manifest")
        with pytest.raises(RT.DataRuntimeError, match="manifest not persisted"):
            resolver.world_for(_world_request(dc, registry_digest="9" * 64))

    def test_a_manifest_whose_snapshot_id_disagrees_refuses_early(
            self, reg9, recipe):
        """L2-b Task 4 (Task-3 review Minor 1): ``data_snapshot_id`` is
        AUDIT-ONLY — excluded from both the manifest's and the context's
        semantic projection — so a manifest whose locator disagrees with the
        admitted context's still matches the content-digest lookup and gets
        bound. Downstream the disagreement DOES surface (``data/reader.py``'s
        frozen-world proof and ``registry.py``'s per-request frozen-set check),
        but only after the session is opened and only as a generic
        "not one frozen set" message with no remedy.

        The resolver now refuses it EARLY, in the same typed loud family as the
        three digest checks and with the same re-run-Lane-0 remedy — before any
        session, read, gateway begin or lease."""
        stores = _fresh_stores(reg9)
        resolver = _resolver_under_test(stores, recipe)
        manifest, dc = _recipe_capture(
            recipe, as_of=AS_OF, snapshot_id="l2b4-locator-a")
        # the audit locator is semantically excluded, so relocating it leaves
        # content_digest — hence the resolver's lookup key — untouched.
        relocated = manifest.model_copy(update={"data_snapshot_id": "l2b4-other"})
        assert relocated.content_digest == manifest.content_digest
        assert relocated.data_snapshot_id != dc.data_snapshot_id
        stores.payloads.put(
            MANIFEST_SR, relocated, registry_digest=reg9.registry_digest,
            namespace="main", idempotency_key="l2b4:locator:manifest")
        with pytest.raises(RT.DataRuntimeError) as exc:
            resolver.world_for(
                _world_request(dc, registry_digest=reg9.registry_digest))
        msg = str(exc.value)
        assert "data_snapshot_id" in msg
        assert "l2b4-other" in msg and "l2b4-locator-a" in msg
        assert "re-run Lane 0" in msg


class TestTheHoistedRefusalAuditSink:
    """L2-b Task 4 (Task-3 review Minor 4): ONE data-aware refusal audit sink
    per BINDING SET, hoisted through the existing ``refusal_audit_sink_factory``
    seam — the ``ExperienceRetrievalBackend`` idiom (build once outside the
    factory, hand the same instance out on every call).

    Before the hoist the default factory built a FRESH sink inside every
    ``world_for`` call, so the records it accumulated were unreachable: nothing
    outside the world ever held the object."""

    def test_the_seam_hands_every_world_the_same_hoisted_sink(
            self, reg9, recipe):
        stores = _fresh_stores(reg9)
        one = DW.production_data_refusal_audit_sink(_FixedClock(AS_OF))
        built: list = []

        def hoisted():
            built.append(one)
            return one

        resolver = ProductionDataWorldResolver(
            stores=stores, recipe=recipe, schema_resolver=stores.resolver,
            clock=_FixedClock(AS_OF), catalog_runtime=object(),
            refusal_audit_sink_factory=hoisted)
        manifest, dc = _recipe_capture(
            recipe, as_of=AS_OF, snapshot_id="l2b4-sink")
        stores.payloads.put(
            MANIFEST_SR, manifest, registry_digest=reg9.registry_digest,
            namespace="main", idempotency_key="l2b4:sink:manifest")
        request = _world_request(dc, registry_digest=reg9.registry_digest)
        worlds = [resolver.world_for(request), resolver.world_for(request)]
        assert worlds[0] is not worlds[1]  # two genuinely distinct worlds…
        for world in worlds:
            assert world.refusal_audit_sink is one  # …ONE sink
        assert len(built) == 2  # the seam is consulted per world

    def test_the_hoisted_sink_recipe_is_data_aware(self):
        """The one recipe both the default and the hoist use: the base audit
        details PLUS ``DataFetchRefusalDetails``, sealed. A sink built from the
        bare ``default_audit_detail_registry`` cannot validate a data refusal at
        all (``UnknownAuditDetailSchema``), which is why the production
        capability gateway must share THIS sink, not its own: a PitGuard
        candidate-stage refusal reaches the gateway as a
        ``DataFetchRefusalDetails@1`` payload (``data/registry.py``'s
        ``_DispatchGuardRecorder`` → ``CapabilityGateway.reject``)."""
        from guanlan_v2.orchestration.runtime_contracts import (
            UnknownAuditDetailSchema,
        )

        sink = DW.production_data_refusal_audit_sink(_FixedClock(AS_OF))
        detail = DataFetchRefusalDetails.build(
            reason_code="future_data", stage="candidate_check",
            request_digest="1" * 64, request_context_digest="2" * 64,
            method="ohlcv", routing_snapshot_digest="3" * 64,
            data_snapshot_content_digest="4" * 64,
            candidate_metadata_digest="5" * 64, pit_audit_digest="6" * 64)
        record = sink.record(
            detail_schema_ref=SchemaRef(name="DataFetchRefusalDetails", version="1"),
            detail_payload=detail, reason_code="future_data",
            idempotency_key="probe:1")
        assert record.reason_code == "future_data"
        assert len(sink.records()) == 1

        bare = EventRefusalAuditSink(
            detail_registry=_sealed_base_detail_registry(),
            clock=_FixedClock(AS_OF))
        with pytest.raises(UnknownAuditDetailSchema):
            bare.record(
                detail_schema_ref=SchemaRef(
                    name="DataFetchRefusalDetails", version="1"),
                detail_payload=detail, reason_code="future_data",
                idempotency_key="probe:2")


# =========================================================================== #
# 2. prepare is DELEGATED — bit-for-bit the real provider's, never a copy      #
# =========================================================================== #
class TestPrepareDelegation:
    def _pair(self):
        bridge = SimpleNamespace(bridge_id="data.runtime", priority=100)
        summary = SimpleNamespace(summary_digest="s" * 64)
        mine = ProductionDataProvider(
            bridge=bridge, summary=summary, resolver=_PoisonedResolver())
        real = RT.DataRuntimeBridgeProvider(
            bridge=bridge, summary=summary, world=None)
        return bridge, summary, mine, real

    def test_prepare_mirrors_the_real_provider_bit_for_bit(self):
        """The pm-two-bridges prepare-mirroring pin, kept: the SAME token in
        yields the SAME BridgeStageOutcome value (empty I/O-free prepare)."""
        _bridge, summary, mine, real = self._pair()
        token = SimpleNamespace(bridge_id="data.runtime",
                                summary_digest=summary.summary_digest)
        req = SimpleNamespace(token=token,
                              evidence_writer=_ForbiddenEvidenceWriter())
        assert mine.prepare_input(req) == real.prepare_input(req)

    def test_prepare_token_guards_are_the_inherited_ones(self):
        _bridge, summary, mine, _real = self._pair()
        foreign = SimpleNamespace(bridge_id="memory.runtime",
                                  summary_digest=summary.summary_digest)
        with pytest.raises(RT.DataRuntimeError, match="different bridge"):
            mine.prepare_input(SimpleNamespace(
                token=foreign, evidence_writer=_ForbiddenEvidenceWriter()))
        unbound = SimpleNamespace(bridge_id="data.runtime",
                                  summary_digest="x" * 64)
        with pytest.raises(RT.DataRuntimeError, match="summary"):
            mine.prepare_input(SimpleNamespace(
                token=unbound, evidence_writer=_ForbiddenEvidenceWriter()))

    def test_delegation_is_source_pinned_never_a_third_copy(self):
        """Anti-copy-paste source pins: prepare forwards to the REAL
        provider's prepare; the rows-present arm builds the REAL provider
        through its own reviewed factory and delegates the WHOLE session; and
        no Phase-3 session machinery is re-implemented in the module.

        CONSCIOUS FLIP (L2-b Task 4). Pre-flip the open-arm pin was
        ``assert "DataRuntimeBridgeProvider(" in open_src`` — a direct class
        construction. Task 4 re-routed it through
        ``data_runtime_provider_factory(world)`` so that the fact-F AST pins
        (``test_data_catalog.py`` + ``test_l2b_handoff.py``) can enumerate the
        production caller by the SINGLE factory symbol they were built around,
        instead of degrading into a class-name scan. The delegation target is
        unchanged — the factory's whole body is
        ``DataRuntimeBridgeProvider(bridge=…, summary=…, world=world)``.

        STRENGTHENED (Task-4 review, M-4): the thin-constructor arm used to pin
        only that the return statement was PRESENT, so a factory that grew a
        side effect or a conditional ahead of it still passed while the promise
        above was broken. It now pins, in the sibling fact-F pins' AST idiom,
        that the returned closure's body IS that ONE ``ast.Return`` — nothing
        before it, nothing after it, and the constructed class and its
        pass-through keywords enumerated.

        CONSCIOUS FLIP (L2-b Task 5 — forced by the integration seam; the
        Task-4 review's M-4 pin is STRENGTHENED here, never weakened).

        | | delegation call text | the factory's enumerated keywords |
        |---|---|---|
        | Task 4 | ``data_runtime_provider_factory(world)(`` | ``[bridge, summary, world]`` |
        | here | ``data_runtime_provider_factory(world, subject_params=self._subject_params)(`` | ``[bridge, summary, world, subject_params]`` |

        Why the OUTER call grew the argument rather than the inner one: the
        inner ``factory`` IS the provider-handler protocol the
        ``ExecutionBridgeResolver`` calls (``factory(*, bridge, summary)``),
        and widening THAT signature would have made the run-subject look like
        something a resolver could pass. Currying it with the world keeps the
        protocol exact and keeps every keyword below a pure pass-through.

        Both halves stay bidirectional: the pre-flip call text must be ABSENT
        (a revert, or Task 5's mutation m1 dropping the kwarg at this one
        line, reddens here), and the fourth keyword must be its own
        pass-through (a hard-coded ``subject_params=None`` reddens too — that
        is the campaign's half-wired-kwarg defect class one call deeper)."""
        prepare_src = inspect.getsource(ProductionDataProvider.prepare_input)
        assert "DataRuntimeBridgeProvider.prepare_input(self, request)" in prepare_src
        open_src = inspect.getsource(ProductionDataProvider.open_execution)
        assert ("data_runtime_provider_factory("
                "world, subject_params=self._subject_params)(") in open_src
        assert "data_runtime_provider_factory(world)(" not in open_src
        assert ".open_execution(request)" in open_src
        # …and the factory really is the thin constructor (so "delegated
        # through it" is not a longer road to a different provider).
        factory_src = inspect.getsource(RT.data_runtime_provider_factory)
        # whitespace-squashed so the arm survives the line wrap the fourth
        # keyword forced, while staying EXACT about tokens and their order.
        squashed = "".join(factory_src.split())
        assert ("returnDataRuntimeBridgeProvider(bridge=bridge,summary=summary,"
                "world=world,subject_params=subject_params)") in squashed
        outer = ast.parse(factory_src).body[0]
        assert isinstance(outer, ast.FunctionDef)
        inner = [n for n in outer.body if isinstance(n, ast.FunctionDef)]
        assert [n.name for n in inner] == ["factory"], (
            "data_runtime_provider_factory must close over exactly ONE "
            "provider-handler factory")
        # the inner factory IS the untouched provider-handler protocol.
        inner_args = inner[0].args
        assert inner_args.args == [] and inner_args.posonlyargs == []
        assert [a.arg for a in inner_args.kwonlyargs] == ["bridge", "summary"]
        body = inner[0].body
        assert len(body) == 1 and isinstance(body[0], ast.Return), (
            "the factory body must be the SINGLE return — a side effect, a "
            "guard or a conditional ahead of it makes 'delegated through the "
            "factory' a longer road to a possibly different provider")
        call = body[0].value
        assert isinstance(call, ast.Call)
        assert isinstance(call.func, ast.Name)
        assert call.func.id == "DataRuntimeBridgeProvider"
        assert call.args == []
        assert [(kw.arg, getattr(kw.value, "id", None)) for kw in call.keywords] == [
            ("bridge", "bridge"), ("summary", "summary"), ("world", "world"),
            ("subject_params", "subject_params")], (
            "the four arguments must be the delegation's own pass-throughs, "
            "unmodified and in the reviewed order")
        # the Task-3 surface itself never touches the session machinery (the
        # Task-2 backend docstring may NAME the gateway adapter in prose, so
        # the scan scope is exactly the Task-3 classes, code and doc alike).
        task3_src = "\n".join(
            inspect.getsource(obj) for obj in (
                ProductionDataProvider, ProductionDataWorldResolver,
                ProductionNoCache, DW._RowlessLicensedEmptyDataSession))
        for machinery in ("DataReader(", "issue_call_token",
                          "_DataGatewayAdapter", "_DataTokenMap",
                          "render_for_prompt(", "_frozen_route_for"):
            assert machinery not in task3_src, (
                f"Phase-3 session machinery {machinery!r} re-implemented or "
                "re-driven inside the Task-3 surface -- delegation only")


# =========================================================================== #
# 3. zero rows — the catalog-licensed EMPTY freeze (row-count partition only)  #
# =========================================================================== #
class TestRowlessLicensedEmpty:
    def _provider(self):
        surf = phase3_data_surface()
        bridge = SimpleNamespace(
            bridge_id=DATA_BRIDGE_ID, priority=100,
            config_bytes=serialize_prefetch_binding(surf.prefetch_binding))
        summary = SimpleNamespace(summary_digest="s" * 64)
        return bridge, summary, ProductionDataProvider(
            bridge=bridge, summary=summary, resolver=_PoisonedResolver())

    @pytest.mark.parametrize("kind", [ExecutionKind.DETERMINISTIC,
                                      ExecutionKind.LLM])
    @pytest.mark.parametrize("wid", ["pv.price_action", "pv.microstructure"])
    def test_a_rowless_worker_freezes_the_licensed_empty(self, wid, kind):
        """The sealed binding grants the pv aux workers NO row: the analyzer
        summed bounds over zero rows (0/0), so the EMPTY completed contribution
        is exactly what the sealed summary licenses — no refusal, no world
        resolution (poisoned resolver proves the partition is row-count ONLY),
        no store/reader/gateway touch."""
        bridge, summary, provider = self._provider()
        req = SimpleNamespace(
            plan=None, node=SimpleNamespace(id="n-aux", params={}),
            worker=SimpleNamespace(id=wid), bridge=bridge, summary=summary,
            handle=None, input_snapshot=None, capability_gateway=None,
            evidence_writer=_ForbiddenEvidenceWriter(), reader=None,
            sequencer=None)
        session = provider.open_execution(req)
        outcome = session.freeze_for_execution(kind=kind)
        assert outcome.status == "completed"
        c = outcome.frozen_contribution
        assert c.bridge_id == DATA_BRIDGE_ID
        assert c.bridge_priority == 100
        assert c.summary_digest == summary.summary_digest
        assert c.tool_call_records == ()
        assert c.data_result_refs == ()
        assert c.direct_evidence_refs == ()
        assert c.untrusted_blocks == ()

    def test_a_foreign_bridge_config_is_still_refused(self):
        """The structural bridge-identity check (the real session's ``_rows``
        mirror) stays loud — a foreign binding is never partitioned over."""
        surf = phase3_data_surface()
        _bridge, summary, provider = self._provider()
        foreign = SimpleNamespace(
            bridge_id="other.bridge", priority=100,
            config_bytes=serialize_prefetch_binding(
                DataBridgePrefetchBinding.build(
                    bridge_id="other.bridge", bridge_version="1",
                    operations=(surf.prefetch_binding.operations[0],))))
        req = SimpleNamespace(
            plan=None, node=SimpleNamespace(id="n", params={}),
            worker=SimpleNamespace(id="pv.price_action"), bridge=foreign,
            summary=summary, handle=None, input_snapshot=None,
            capability_gateway=None,
            evidence_writer=_ForbiddenEvidenceWriter(), reader=None,
            sequencer=None)
        with pytest.raises(RT.DataRuntimeError, match="different bridge"):
            provider.open_execution(req)


# =========================================================================== #
# 4. the reduced-preset harness (pm-two-bridges idiom, faithfully copied)      #
# =========================================================================== #
def _build_reduced_env(reg9, snap, bundle, view, *, dc, manifest,
                       run_tag: str):
    """One reduced-preset environment over a chosen committed DataContext.

    Faithful adaptation of the pm-two-bridges module fixtures: same preset,
    same validation + support path, but the ContextSnapshot is PERSISTED for
    real (the resolver reads it through ``request.reader``) and the per-run
    manifest — when the producer ran the recipe — is persisted through the
    D-D payload-store channel.
    """
    resolver = SchemaRegistryResolver()
    resolver.register(reg9)
    stores = RuntimeStores(
        resolver=resolver, clock=_FixedClock(NOW),
        allowed_cell_namespaces=(W.PROMPT_CELL_NAMESPACE,))
    mem = P.build_empty_memory_context(
        data_context=dc, stores=stores,
        registry_digest=reg9.registry_digest, built_at=NOW)
    context = mem.context
    ctx_payload_ref = stores.payloads.put(
        CTX_SR, context, registry_digest=reg9.registry_digest,
        namespace="main", idempotency_key=f"l2b3:{run_tag}:ctx")
    ctx_typed = TypedPayloadRef(schema_ref=CTX_SR, payload_ref=ctx_payload_ref)
    if manifest is not None:
        stores.payloads.put(
            MANIFEST_SR, manifest, registry_digest=reg9.registry_digest,
            namespace="main", idempotency_key=f"l2b3:{run_tag}:manifest")
    request = OrchestrationRequest(
        request_id=f"req-l2b3-{run_tag}", goal="观澜 · L2-b 生产数据供给",
        workflow="orchestrate_only", fallback_preset_id=None,
        approval_policy=ApprovalPolicy.REQUIRED)
    presets = load_phase10_preset_registry(PRODUCTION_PRESETS_DIR)
    materialized = materialize_deep_decide_draft(
        request=request, preset_registry=presets,
        preset_id=REDUCED_DEEP_DECIDE_PRESET_ID,
        context_snapshot_ref=ctx_payload_ref, subject_ref=_subject_ref(),
        clock=_FixedClock(NOW), context=context, catalog=snap,
        schema_registry=reg9, draft_id=f"draft-l2b3-{run_tag}",
        run_id=f"run-l2b3-{run_tag}")
    draft = materialized.draft
    phase1 = validate_plan_draft(
        draft, request=request, context=context, catalog=snap,
        schema_registry=reg9)
    assert phase1.valid is True, [(i.code, i.node_id) for i in phase1.issues]
    report = check_runtime_support(
        draft, phase1_report=phase1, context=context,
        context_requirements=None, catalog=bundle.runtime, bridge_view=view,
        schema_registry=reg9, profile=STATIC_RUNTIME_PROFILE_V2)
    assert report.supported is True, [(i.code, i.node_id) for i in report.issues]
    plan_ns = SimpleNamespace(
        plan_digest=compute_candidate_plan_digest(
            request=request, draft=draft,
            context_content_digest=context.content_digest),
        catalog_digest=snap.catalog_digest,
        schema_registry_digest=reg9.registry_digest)
    return SimpleNamespace(
        stores=stores, context=context, ctx_typed=ctx_typed, draft=draft,
        report=report, request=request, plan=plan_ns, registry=reg9,
        snapshot=snap)


@pytest.fixture(scope="module")
def env_pilot(reg9, snap, bundle, view):
    """The reduced env over the PILOT context (pre-L2-b digests)."""
    return _build_reduced_env(
        reg9, snap, bundle, view, dc=P.pilot_data_context(as_of=NOW),
        manifest=None, run_tag="pilot")


@pytest.fixture(scope="module")
def env_recipe(reg9, snap, bundle, view, recipe):
    """The reduced env over a RECIPE-built context + persisted manifest."""
    manifest, dc = _recipe_capture(
        recipe, as_of=NOW, snapshot_id="l2b3-reduced-recipe")
    return _build_reduced_env(
        reg9, snap, bundle, view, dc=dc, manifest=manifest, run_tag="recipe")


def _registered_factories(env, bundle):
    """EXACTLY the three reviewed recipes the Task-4 seam will call, on a
    fresh registry over the same sealed runtime — with THIS task's production
    data registration in the data slot (pm-two-bridges recipe, adapted)."""
    factories = TrustedFactoryRegistry(bundle.runtime)
    bs.register_lane0_experience_factories(
        factories=factories, catalog=bs.load_lane0_catalog(), pool=None,
        registry=env.registry, experience_views=(),
        experience_scaler=None, as_of=NOW)
    MR.register_phase3_memory_provider_factory(
        factories=factories, stores=_PoisonedStores())
    register_production_data_provider(
        factories=factories, stores=env.stores,
        schema_resolver=env.stores.resolver, clock=_FixedClock(NOW),
        catalog_runtime=bundle.runtime)
    return factories


def _exec_runtime(bundle, view, report, factories):
    return W.ExecutionRuntime(
        catalog=bundle.runtime, bridge_view=view, factories=factories,
        support_report=report, runtime_registry_digest="r" * 64)


def _pm_drive(env, bundle, view, factories, *, kind=ExecutionKind.LLM):
    """Drive pm through the REAL resolver + a REAL gateway to open/freeze."""
    runtime = _exec_runtime(bundle, view, env.report, factories)
    node = next(n for n in env.draft.nodes if n.id == "pm")
    worker = {w.id: w for w in env.snapshot.workers}["dec.pm"]
    sequencer = W.ExecutionEvidenceSequencer(node_id="pm", attempt=1)
    resolver = W.ExecutionBridgeResolver(
        runtime=runtime, node=node, worker=worker, sequencer=sequencer)
    prepared = resolver.prepare_input(
        plan=env.plan, context_snapshot_ref=env.ctx_typed,
        evidence_writer=_ForbiddenEvidenceWriter())
    summaries = (
        _summary_for(env.report, "pm", "data.runtime"),
        _summary_for(env.report, "pm", "memory.runtime"))
    sink = _data_audit_sink(_FixedClock(NOW))
    gateway = W.CapabilityGateway(
        plan_digest=env.plan.plan_digest, worker=worker,
        summaries={s.summary_digest: s for s in summaries},
        catalog=bundle.runtime,
        factories=factories, phase1_registry=env.registry,
        refusal_sink=sink, clock=_FixedClock(NOW), sequencer=sequencer)
    gateway.bind_reader(env.stores.payloads)
    gateway.mark_running()

    def run():
        return resolver.open_execution(
            plan=env.plan, prepared=prepared,
            input_snapshot=SimpleNamespace(context_snapshot_ref=env.ctx_typed),
            capability_gateway=gateway,
            evidence_writer=_ForbiddenEvidenceWriter(),
            reader=env.stores.payloads, kind=kind)

    return run, gateway, sink


class TestRegistrationRecipe:
    def test_the_one_recipe_binds_provider_and_all_seven_backends(
            self, env_pilot, bundle):
        """``register_production_data_provider``: the provider factory lands
        under the exact sealed ``bridge.data_runtime.provider@1`` identity and
        every one of the seven data capability refs resolves — per the D-A
        ``lambda **kw`` shape — to the ONE ``production_data_backend()``."""
        factories = _registered_factories(env_pilot, bundle)
        provider = factories.handler_factory(
            phase3_data_surface().provider_ref)(
            bridge=SimpleNamespace(bridge_id="data.runtime", priority=100),
            summary=SimpleNamespace(summary_digest="s" * 64))
        assert isinstance(provider, ProductionDataProvider)
        caps = data_capability_refs()
        assert len(caps) == 7
        one = DW.production_data_backend()
        for _mid, cap_ref in sorted(caps.items()):
            factory = factories.capability_backend_factory(cap_ref)
            assert factory(capability_ref=cap_ref) is one
            assert factory(capability_ref=cap_ref) is one  # per-invocation

    def test_the_sibling_recipes_are_untouched(self, env_pilot, bundle):
        factories = _registered_factories(env_pilot, bundle)
        mem = factories.handler_factory(phase3_memory_surface().provider_ref)(
            bridge=SimpleNamespace(bridge_id="memory.runtime", priority=200),
            summary=SimpleNamespace(summary_digest="s" * 64))
        assert isinstance(mem, MR.MemoryRuntimeBridgeProvider)
        lane0 = bs.load_lane0_catalog()
        assert factories.handler_factory(
            lane0.refs["lane0.experience.provider"])
        assert factories.capability_backend_factory(lane0.capability_ref)


class TestPmThroughTheRealResolver:
    def test_pilot_context_refuses_before_any_gateway_begin(
            self, env_pilot, bundle, view):
        """The pre-flight half of the resolver at the REAL seam: pm's rows are
        present, so the world is resolved — and the pilot-era committed
        snapshot refuses with the re-run-Lane-0 remedy BEFORE any session,
        any read, any gateway begin, any evidence write."""
        factories = _registered_factories(env_pilot, bundle)
        run, gateway, sink = _pm_drive(env_pilot, bundle, view, factories)
        with pytest.raises(RT.DataRuntimeError) as exc:
            run()
        msg = str(exc.value)
        assert "predates the production data world" in msg
        assert "re-run Lane 0" in msg
        assert gateway.begun_count() == 0
        assert gateway.finalized_records() == ()
        assert sink.records() == ()

    def test_pm_rows_present_without_a_subject_refuse_inside_the_real_session(
            self, env_recipe, bundle, view):
        """THE SURVIVING SHAPE 3 (L2-b Task 5's conscious flip 3, negative
        half): the deleted worldless provider's ``subject projection not bound
        at the runner seam`` refusal lives on INSIDE the real path.

        Pre-flip this test was order-conditional
        (``_skip_unless_subjectless_provider``) because it described an
        intra-train intermediate — the provider had no subject source at all.
        Post-flip it describes a PERMANENT production invariant: the
        process-level registration is UNBOUND by design (only a per-run
        ``_SubjectScopedFactories`` view binds a projection), so a rows-present
        worker reached WITHOUT one must still refuse loudly rather than read
        with a guessed subject. The guard therefore stopped being conditional
        and the docstring stopped promising a rewrite.

        With a RECIPE-built committed context the digest checks PASS and the
        manifest loads, so this is the REAL delegated session refusing in the
        REAL ``_assemble_params`` — zero gateway begins, the refusal is pure."""
        factories = _registered_factories(env_recipe, bundle)
        run, gateway, sink = _pm_drive(env_recipe, bundle, view, factories)
        with pytest.raises(RT.DataRuntimeError) as exc:
            run()
        msg = str(exc.value)
        assert "not bound at the runner seam" in msg
        assert "subject_params=None" in msg
        # NOT the digest refusal (the recipe context passed verification) and
        # NOT the retired worldless shape-2 marker:
        assert "re-run Lane 0" not in msg
        assert "params resolved from the run subject projection" not in msg
        assert "no production DataRuntimeWorld is bound" not in msg
        assert gateway.begun_count() == 0
        assert gateway.finalized_records() == ()

    def test_the_pv_aux_nodes_freeze_empty_through_the_real_resolver(
            self, env_pilot, bundle, view):
        """The rowless arm at the REAL seam (RED today: the worldless
        incumbent refuses loudly): both pv aux workers complete their data
        bridge with the catalog-licensed EMPTY contribution — no
        ``bridge_execution_error`` on this path, and the pilot context is
        never even read (rowless partitions BEFORE world resolution)."""
        factories = _registered_factories(env_pilot, bundle)
        runtime = _exec_runtime(bundle, view, env_pilot.report, factories)
        for wid in ("pv.price_action", "pv.microstructure"):
            node = next(n for n in env_pilot.draft.nodes
                        if n.worker_id == wid)
            worker = {w.id: w for w in env_pilot.snapshot.workers}[wid]
            sequencer = W.ExecutionEvidenceSequencer(
                node_id=node.id, attempt=1)
            resolver = W.ExecutionBridgeResolver(
                runtime=runtime, node=node, worker=worker,
                sequencer=sequencer)
            assert resolver.required_bridge_ids == ("data.runtime",)
            prepared = resolver.prepare_input(
                plan=env_pilot.plan, context_snapshot_ref=env_pilot.ctx_typed,
                evidence_writer=_ForbiddenEvidenceWriter())
            contributions = resolver.open_execution(
                plan=env_pilot.plan, prepared=prepared, input_snapshot=None,
                capability_gateway=None,
                evidence_writer=_ForbiddenEvidenceWriter(), reader=None,
                kind=ExecutionKind.DETERMINISTIC)
            assert len(contributions) == 1
            c = contributions[0]
            assert c.bridge_id == "data.runtime"
            assert c.tool_call_records == ()
            assert c.data_result_refs == ()
            assert c.direct_evidence_refs == ()
            assert c.untrusted_blocks == ()


# =========================================================================== #
# 5. the synthetic live-row END-TO-END read (fabricated catalog idiom)         #
# =========================================================================== #
E2E_WORKER_ID = "dec.data"

#: per-method synthetic shapes.  The e2e row uses ``ohlcv`` (series params —
#: strict-safe from JSON-normalized node params); ``news`` binds only
#: ``/as_of`` (``symbols`` defaults empty) — the optional degradation arm.
#:
#: ``verified_snapshot`` is the L2-b Task 5 arm and is shaped as the SEALED
#: ``dec.pm`` row is: ``params_schema=None`` (the worker can never legally
#: carry node params — Phase-1 refuses them with ``params_not_allowed``),
#: ``node_params={}``, and the two sealed ``node_param`` bindings verbatim.
#: ``InstrumentUniverseParams.symbols`` is a STRICT tuple of ``Symbol``, so
#: this row is structurally UNREACHABLE from node params — the exact catalog
#: honesty fact that forced the L1 subject projection. It resolves ONLY when a
#: run subject is bound, which is why it is the proof that the integration
#: seam is really closed (``subject_params=`` on the shape below).
_E2E_METHODS = {
    "ohlcv": SimpleNamespace(
        params_schema=SchemaRef(name="InstrumentSeriesParams", version="1"),
        node_params={
            "symbol": {"code": "600519", "exchange": "SH", "board": "main"},
            "start": "2026-07-01T00:00:00+00:00",
            "end": AS_OF.isoformat(),
        },
        bindings=(
            ParamBinding(target_pointer="/end", source_kind="node_param",
                         source_pointer="/end"),
            ParamBinding(target_pointer="/start", source_kind="node_param",
                         source_pointer="/start"),
            ParamBinding(target_pointer="/symbol", source_kind="node_param",
                         source_pointer="/symbol"),
        )),
    "news": SimpleNamespace(
        params_schema=SchemaRef(name="InstrumentUniverseParams", version="1"),
        node_params={"as_of": AS_OF.isoformat()},
        bindings=(
            ParamBinding(target_pointer="/as_of", source_kind="node_param",
                         source_pointer="/as_of"),
        )),
    "verified_snapshot": SimpleNamespace(
        params_schema=None,          # exactly dec.pm: no params schema at all
        node_params={},              # …so the node can carry nothing
        bindings=phase3_data_surface(
        ).prefetch_binding.operations[0].param_bindings),
}


def _synthetic_schema_registry() -> Phase2RuntimeRegistry:
    reg = Phase2RuntimeRegistry()
    for model in tuple(phase2_public_models()) + PHASE3_PUBLIC_MODELS + (OtherOut,):
        reg.register(model)
    reg.seal()
    return reg


def _snapshot_envelope(*, pulled_at="2026-07-16T14:59:00"):
    """A ``tencent_realtime_quote``-shaped envelope: the two fields
    ``VerifiedSnapshotRecord.from_candidate`` consumes, nothing fabricated
    beyond them."""
    return {"ok": True, "status": "ok", "n": 1, "error": "", "note": "",
            "pulled_at": pulled_at,
            "items": [{"name": "贵州茅台", "last_price": 1487.3,
                       "prev_close": 1470.0}]}


def _tape_envelope(*, pulled_at="2026-07-16T14:59:00"):
    env = {"ok": True, "status": "ok", "n": 1, "error": "", "note": "",
           "items": [{"name": "全市场", "open": 1480.0, "high": 1490.0,
                      "low": 1470.0, "close": 1487.3, "volume": 100}]}
    if pulled_at is not None:
        env["pulled_at"] = pulled_at
    return env


def _build_synthetic_env(monkeypatch, *, method_id: str, probe,
                         subject_params=None):
    """A fabricated catalog granting ONE live row to a worker, admitted for
    real, executed through THIS task's registration recipe over the REAL
    production recipe world — probe injected, zero network.

    Faithful trimmed adaptation of ``test_runtime_integration.build_data_env``
    (recorded in the module docstring): admission service dropped; validation
    + runtime support are the real ones.

    ``subject_params`` (L2-b Task 5) drives the run through the REAL per-run
    seam object: the registered factories are wrapped in
    ``assembly._SubjectScopedFactories`` over
    ``production_data_provider_factory(subject_params, …)`` — the exact
    construction ``build_production_plan_runner._plan_executor`` performs when
    a deep run's projection is bound. It is NOT a second registration: the
    process-level binding stays UNBOUND underneath, and the view overrides
    exactly the sealed provider ref.
    """
    recipe = production_data_recipe()
    schema_registry = _synthetic_schema_registry()
    manifest, dc = _recipe_capture(
        recipe, as_of=AS_OF, snapshot_id=f"l2b3-e2e-{method_id}")

    surface = phase3_data_surface()
    shape = _E2E_METHODS[method_id]
    spec = recipe.registry.method_spec(method_id)
    route = recipe.registry.default_route(method_id)
    row = DataPrefetchOperation(
        worker_id=E2E_WORKER_ID, method_ref=spec.method_ref,
        capability_ref=spec.capability_ref, frozen_route=route.entries,
        invocation_mode="cache_or_invoke",
        success_requires_finalized_call=False,
        param_bindings=shape.bindings)
    binding = DataBridgePrefetchBinding.build(
        bridge_id=DATA_BRIDGE_ID, bridge_version="1", operations=(row,))
    config_bytes = serialize_prefetch_binding(binding)

    from guanlan_v2.orchestration.runtime_contracts import (
        ExecutionBridgeDescriptor,
    )

    prompt_ref, prompt_mat = build_text_material(
        id="text.l2b3.prompt", version="1", kind="prompt",
        raw="You are the data worker.".encode("utf-8"))
    config_ref, config_mat = build_text_material(
        id="bridge.data_runtime.prefetch.l2b3", version="1", kind="guardrail",
        raw=config_bytes)
    granted_cap = spec.capability_ref
    descriptor = ExecutionBridgeDescriptor(
        bridge_id=DATA_BRIDGE_ID, bridge_version="1", priority=100,
        provider_handler_ref=surface.provider_ref, config_ref=config_ref,
        support_analyzer_ref=surface.analyzer_ref,
        config_schema_ref=SchemaRef(name="DataBridgePrefetchBinding",
                                    version="1"),
        activation_capability_refs=(granted_cap,),
        activation_read_categories=(),
        supported_execution_kinds=(ExecutionKind.DETERMINISTIC,
                                   ExecutionKind.LLM),
        pre_input_kind="none", lifecycle="static_prefetch_v1")
    d_ref, d_mat = build_text_material(
        id="bridge.data_runtime.descriptor.l2b3", version="1",
        kind="guardrail", raw=serialize_bridge_descriptor(descriptor))

    content = [
        ContentManifestEntry(ref=prompt_ref, kind="prompt", name="P",
                             description="d", source_identity="gl.l2b3"),
        ContentManifestEntry(ref=surface.renderer_ref, kind="handler",
                             name="Renderer", description="d",
                             source_identity="gl.l2b3"),
        ContentManifestEntry(ref=surface.provider_ref, kind="handler",
                             name="Provider", description="d",
                             source_identity="gl.l2b3"),
        ContentManifestEntry(ref=surface.analyzer_ref, kind="handler",
                             name="Analyzer", description="d",
                             source_identity="gl.l2b3"),
        ContentManifestEntry(ref=config_ref, kind="guardrail", name="Cfg",
                             description="d", source_identity="gl.l2b3"),
        ContentManifestEntry(ref=d_ref, kind="guardrail", name=DATA_BRIDGE_ID,
                             description="d", source_identity="gl.l2b3"),
    ]
    mats: list = [prompt_mat, surface.renderer_material,
                  surface.provider_material, surface.analyzer_material,
                  config_mat, d_mat]
    # ALL SEVEN sealed capability materials ride along (the registration
    # recipe registers a backend for each); only the granted one enters the
    # worker allowlist.
    cap_manifest = [
        CapabilityManifestEntry(ref=m.ref, capability_kind="data_adapter",
                                transport="in_process")
        for m in surface.capability_materials]
    mats.extend(surface.capability_materials)

    worker = WorkerSpec(
        id=E2E_WORKER_ID, catalog_role="final",
        selection_scope="dynamic_allowed", lane="quant", persona="analyst",
        tier=Tier.WRITER,
        execution=ExecutionSpec(kind=ExecutionKind.LLM, model_tier="reasoner"),
        system_prompt_ref=prompt_ref,
        params_schema_ref=shape.params_schema,
        capability_allowlist=(granted_cap,),
        read_categories=("context",), inputs=(),
        outputs=(OutputBinding(name="primary", schema_ref=SR_OUT),),
        evidence_policy=EvidencePolicy(tool_calls=ToolCallRequirement.OPTIONAL),
        supported_modes=(DataMode.ONLINE,), can_emit_decision=False,
        decision_authority="none")

    snapshot = build_catalog_snapshot(
        catalog_version="l2b3-e2e-v1", content_manifest=tuple(content),
        skill_manifest=(), capability_manifest=tuple(cap_manifest),
        workers=(worker,), resolved_material=tuple(mats))
    from guanlan_v2.orchestration.catalog import (
        ResolvedCapabilityMaterial,
        ResolvedTextMaterial,
    )
    source = InMemoryMaterialSource(
        text={(m.ref.id, m.ref.version): m.raw_utf8 for m in mats
              if isinstance(m, ResolvedTextMaterial)},
        capabilities={(m.ref.id, m.ref.version): m.descriptor for m in mats
                      if isinstance(m, ResolvedCapabilityMaterial)})
    catalog_runtime = CatalogRuntime.build(snapshot, source)
    analyzers = {(surface.analyzer_ref.id, surface.analyzer_ref.version,
                  surface.analyzer_ref.content_digest):
                 DataBridgeSupportAnalyzer()}
    view = BridgeCatalogView.build(catalog_runtime, analyzers)

    binding_mem = build_empty_memory_binding()
    context = ContextSnapshot.build(
        snapshot_id="ctx-l2b3", data_context=dc, memory_snapshot_id="ms-1",
        memory_snapshot_hash=binding_mem.snapshot_hash,
        past_context_hash=binding_mem.past_context_hash,
        memory_snapshot_ref=binding_mem.memory_snapshot_ref,
        memory_selection_ref=binding_mem.memory_selection_ref,
        runtime_requirements_ref=None, built_at=AS_OF)

    node = PlanNode(
        id="n1", worker_id=E2E_WORKER_ID, writes_slot="s1",
        params=dict(shape.node_params))
    draft = PlanDraft(
        id="plan.l2b3", run_id="run-l2b3", request_id="req-l2b3",
        phase="main", source=PlanSource.DYNAMIC, goal="g", as_of=AS_OF,
        mode=DataMode.ONLINE,
        context_snapshot_ref=PayloadRef(
            namespace="main", object_id="ctx-obj",
            content_digest=context.content_digest),
        nodes=(node,), sink_node_ids=("n1",),
        catalog_version=snapshot.catalog_version,
        catalog_digest=snapshot.catalog_digest,
        schema_registry_digest=schema_registry.registry_digest,
        approval_policy=ApprovalPolicy.REQUIRED,
        budget_request_tokens=500_000, budget_request_llm_invocations=100,
        max_concurrency=8)
    request = OrchestrationRequest(
        request_id="req-l2b3", goal="g", workflow="orchestrate_only",
        approval_policy=ApprovalPolicy.REQUIRED)
    phase1 = validate_plan_draft(
        draft, request=request, context=context, catalog=snapshot,
        schema_registry=schema_registry)
    assert phase1.valid is True, [(i.code, i.node_id) for i in phase1.issues]
    report = check_runtime_support(
        draft, phase1_report=phase1, context=context,
        context_requirements=None, catalog=catalog_runtime, bridge_view=view,
        schema_registry=schema_registry, profile=STATIC_RUNTIME_PROFILE_V2)
    assert report.supported is True, [(i.code, i.node_id)
                                      for i in report.issues]

    resolver = SchemaRegistryResolver()
    resolver.register(schema_registry)
    from guanlan_v2.orchestration.schema_registry import default_registry
    rt_reg = phase2_runtime_registry(default_registry().registry_digest)
    rt_digest = resolver.register(rt_reg)
    stores = RuntimeStores(
        resolver=resolver, clock=_FixedClock(AS_OF),
        allowed_cell_namespaces=(W.PROMPT_CELL_NAMESPACE,))
    ctx_payload_ref = stores.payloads.put(
        CTX_SR, context, registry_digest=rt_digest, namespace="main",
        idempotency_key="l2b3:e2e:ctx")
    ctx_typed = TypedPayloadRef(schema_ref=CTX_SR, payload_ref=ctx_payload_ref)
    stores.payloads.put(
        MANIFEST_SR, manifest,
        registry_digest=schema_registry.registry_digest, namespace="main",
        idempotency_key="l2b3:e2e:manifest")

    # the injected fake probe reaches the run through the REAL singleton door:
    # the ONE thread-confined backend, its adapter a REAL LiveClientSource
    # over the existing probe/resolve ports.
    fake_backend = DW.ThreadConfinedDataBackend(
        {surface.source_ref.id: LiveClientSource(
            probe_fn=probe, resolve_source_fn=lambda s: s)})
    monkeypatch.setattr(DW, "_BACKEND", fake_backend)
    assert DW.production_data_backend() is fake_backend

    base_factories = TrustedFactoryRegistry(catalog_runtime)
    register_production_data_provider(
        factories=base_factories, stores=stores, schema_resolver=resolver,
        clock=_FixedClock(AS_OF), catalog_runtime=catalog_runtime)
    if subject_params is None:
        factories = base_factories
    else:
        from guanlan_v2.orchestration.pipeline import assembly as _assembly

        factories = _assembly._SubjectScopedFactories(
            base_factories, provider_ref=surface.provider_ref,
            factory=production_data_provider_factory(
                subject_params, stores=stores, schema_resolver=resolver,
                clock=_FixedClock(AS_OF), catalog_runtime=catalog_runtime))

    plan_ns = SimpleNamespace(
        plan_digest=compute_candidate_plan_digest(
            request=request, draft=draft,
            context_content_digest=context.content_digest),
        catalog_digest=snapshot.catalog_digest,
        schema_registry_digest=schema_registry.registry_digest)

    runtime = W.ExecutionRuntime(
        catalog=catalog_runtime, bridge_view=view, factories=factories,
        support_report=report, runtime_registry_digest=rt_digest)
    sequencer = W.ExecutionEvidenceSequencer(node_id="n1", attempt=1)
    bridge_resolver = W.ExecutionBridgeResolver(
        runtime=runtime, node=node, worker=worker, sequencer=sequencer)
    writer = W.BridgeEvidenceWriter(
        stores=stores, sequencer=sequencer, run_id="run-l2b3",
        plan_digest=plan_ns.plan_digest, node_id="n1",
        data_registry_digest=schema_registry.registry_digest,
        runtime_registry_digest=rt_digest)
    sink = _data_audit_sink(_FixedClock(AS_OF))
    gateway = W.CapabilityGateway(
        plan_digest=plan_ns.plan_digest, worker=worker,
        summaries={s.summary_digest: s
                   for s in report.bridge_support_summaries},
        catalog=catalog_runtime, factories=factories,
        phase1_registry=schema_registry, refusal_sink=sink,
        clock=_FixedClock(AS_OF), sequencer=sequencer)
    gateway.bind_reader(stores.payloads)
    gateway.mark_running()

    def drive(*, kind=ExecutionKind.LLM):
        prepared = bridge_resolver.prepare_input(
            plan=plan_ns, context_snapshot_ref=ctx_typed,
            evidence_writer=writer)
        return bridge_resolver.open_execution(
            plan=plan_ns, prepared=prepared,
            input_snapshot=SimpleNamespace(context_snapshot_ref=ctx_typed),
            capability_gateway=gateway, evidence_writer=writer,
            reader=stores.payloads, kind=kind)

    return SimpleNamespace(
        drive=drive, stores=stores, gateway=gateway, sink=sink,
        method_spec=spec, manifest=manifest, dc=dc, probe=probe,
        factories=factories, base_factories=base_factories,
        provider_ref=surface.provider_ref)


class TestSyntheticLiveRowEndToEnd:
    def test_one_real_read_through_the_delegated_session(self, monkeypatch):
        """THE delegation-seam e2e: the rows-present arm hands the WHOLE
        session to the real ``DataRuntimeBridgeProvider`` over the per-run
        resolved world — and the whole Phase-3 machinery runs: one finalized
        ``ToolCallRecord``, a PIT-audited named ``OHLCVDataResult``, a
        rendered untrusted block for the LLM kind, one probe call under the
        canonical vendor id."""
        probe = _ProbeStub(_tape_envelope())
        env = _build_synthetic_env(monkeypatch, method_id="ohlcv", probe=probe)
        contributions = env.drive(kind=ExecutionKind.LLM)
        assert len(contributions) == 1
        c = contributions[0]
        assert c.bridge_id == DATA_BRIDGE_ID

        # one ToolCallRecord, minted only through finalize_success.
        assert len(c.tool_call_records) == 1
        record = c.tool_call_records[0]
        assert record.tool_ref.id == "cap.data.ohlcv"

        # the consumed named result IS the tool result (cross-match).
        assert len(c.data_result_refs) == 1
        result_ref = c.data_result_refs[0]
        assert result_ref == record.result_ref
        assert result_ref.schema_ref == SchemaRef(
            name="OHLCVDataResult", version="1")
        result = env.stores.payloads.get(
            result_ref.payload_ref, expected_schema_ref=result_ref.schema_ref)
        assert result.status is DataStatus.OK
        assert result.pit_audit.guard_result == "passed"
        assert result.resolved_vendor_chain == ("guanlan.datafeed",)
        assert result.data.rows[0].close == 1487.3
        assert result.data.rows[0].symbol.code == "600519"

        # the rendered untrusted block, persisted and typed.
        assert len(c.untrusted_blocks) == 1
        block_ref = c.untrusted_blocks[0].payload_ref
        block = env.stores.payloads.get(
            block_ref.payload_ref, expected_schema_ref=SchemaRef(
                name="RenderedDataBlock", version="1"))
        assert block.method == "ohlcv"
        assert "1487.3" in block.rendered_text

        # one probe call under the canonical vendor id (the market-wide tape
        # source; no per-code arg) — the reviewed adapter, not a stub of it.
        assert len(probe.calls) == 1
        assert probe.calls[0]["source"] == "eastmoney_market_fund_flow"
        assert probe.calls[0]["code"] == ""

    def test_the_read_params_echo_the_node_params(self, monkeypatch):
        """The delegated session's param assembly is the REAL one: the
        persisted ``DataRequest`` carries the node's own validated params."""
        probe = _ProbeStub(_tape_envelope())
        env = _build_synthetic_env(monkeypatch, method_id="ohlcv", probe=probe)
        contributions = env.drive(kind=ExecutionKind.LLM)
        record = contributions[0].tool_call_records[0]
        request = env.stores.payloads.get(
            record.request_ref.payload_ref,
            expected_schema_ref=record.request_ref.schema_ref)
        assert request.params["symbol"]["code"] == "600519"
        assert request.params["end"] == AS_OF.isoformat()

    def test_the_subject_bound_sealed_row_reads_for_real_through_the_per_run_view(
            self, monkeypatch):
        """**THE L2-b Task 5 pin — conscious flip 2, the end state both plans
        exist for.**

        The row here is shaped EXACTLY as the sealed ``dec.pm`` grant: a worker
        with ``params_schema_ref=None``, a node with NO params, and the two
        sealed ``node_param`` bindings (``/as_of <- /asof_date``,
        ``/symbols <- /code``). ``InstrumentUniverseParams.symbols`` is a
        STRICT tuple of ``Symbol``, so this row is structurally unreachable
        from node params — before this task it could only ever refuse:

        | | outcome for this exact row |
        |---|---|
        | L1 | worldless provider: PROVEN resolvable under a bound projection, then refused (``no production DataRuntimeWorld is bound``) |
        | L2-b Task 4 | world bound, but the per-run view still handed out the worldless provider AND the real session had no subject source ⇒ ``subject projection not bound at the runner seam`` |
        | here | READ — the per-run view hands out the subject-bound production provider, and the run subject IS the param source |

        Driven through the ACTUAL seam object (``_SubjectScopedFactories`` over
        ``production_data_provider_factory(sp, …)``) with the process-level
        registration left UNBOUND underneath, over the REAL production recipe
        world, against an injected fake probe (zero network). The echoes are
        EXACT — the subject's own code and as-of, not merely "some values" —
        at all three places the projection has to survive:

        1. the persisted ``DataRequest.params`` (post ``params_cls`` validation);
        2. the vendor door (``_probe_args`` builds ``exchange+code``, so the
           probe argument proves the Symbol identity the reviewed constructor
           derived — a wrong-market projection would show up here);
        3. the persisted, PIT-audited ``VerifiedSnapshotDataResult`` row.
        """
        sp = RT.SubjectParams.project(code="600519", as_of=AS_OF)
        probe = _ProbeStub(_snapshot_envelope())
        env = _build_synthetic_env(
            monkeypatch, method_id="verified_snapshot", probe=probe,
            subject_params=sp)

        # the seam object really is the per-run view over an UNBOUND base.
        base_provider = env.base_factories.handler_factory(env.provider_ref)(
            bridge=SimpleNamespace(bridge_id=DATA_BRIDGE_ID, priority=100),
            summary=SimpleNamespace(summary_digest="s" * 64))
        assert base_provider._subject_params is None

        contributions = env.drive(kind=ExecutionKind.LLM)
        assert len(contributions) == 1
        c = contributions[0]

        # (1) ONE finalized ToolCallRecord under the sealed capability.
        assert len(c.tool_call_records) == 1
        record = c.tool_call_records[0]
        assert record.tool_ref.id == "cap.data.verified_snapshot"
        request = env.stores.payloads.get(
            record.request_ref.payload_ref,
            expected_schema_ref=record.request_ref.schema_ref)
        assert [s["code"] for s in request.params["symbols"]] == ["600519"]
        assert [s["exchange"] for s in request.params["symbols"]] == ["SH"]
        assert request.params["as_of"] == sp.asof_value
        assert request.params["as_of"] == AS_OF.isoformat()

        # (2) the vendor door saw the subject's Symbol identity.
        assert len(probe.calls) == 1
        assert probe.calls[0]["source"] == "tencent_realtime_quote"
        assert probe.calls[0]["code"] == "SH600519"

        # (3) the persisted, PIT-audited named result.
        assert len(c.data_result_refs) == 1
        result_ref = c.data_result_refs[0]
        assert result_ref == record.result_ref
        assert result_ref.schema_ref == SchemaRef(
            name="VerifiedSnapshotDataResult", version="1")
        result = env.stores.payloads.get(
            result_ref.payload_ref, expected_schema_ref=result_ref.schema_ref)
        assert result.status is DataStatus.OK
        assert result.pit_audit.guard_result == "passed"
        assert result.resolved_vendor_chain == ("guanlan.datafeed",)
        assert result.data.rows[0].symbol.code == "600519"
        assert result.data.rows[0].last_price == 1487.3

        # the rendered untrusted block for the LLM kind.
        assert len(c.untrusted_blocks) == 1
        block = env.stores.payloads.get(
            c.untrusted_blocks[0].payload_ref.payload_ref,
            expected_schema_ref=SchemaRef(name="RenderedDataBlock", version="1"))
        assert block.method == "verified_snapshot"
        assert "1487.3" in block.rendered_text

    def test_without_the_subject_the_same_sealed_shape_refuses_at_the_seam(
            self, monkeypatch):
        """The negative half of the flip above, over the SAME fabricated
        catalog: drop the per-run view (process-level UNBOUND, everything else
        byte-identical) and the identical row refuses inside the real session
        naming the runner seam — zero probe calls, zero finalized records.

        This is what makes the positive pin mean something: the read is caused
        by the SUBJECT reaching the session, not by the fabricated catalog
        being permissive."""
        probe = _ProbeStub(_snapshot_envelope())
        env = _build_synthetic_env(
            monkeypatch, method_id="verified_snapshot", probe=probe)
        with pytest.raises(RT.DataRuntimeError) as exc:
            env.drive(kind=ExecutionKind.LLM)
        msg = str(exc.value)
        assert "not bound at the runner seam" in msg
        assert "subject_params=None" in msg
        assert probe.calls == []
        assert env.gateway.finalized_records() == ()


class TestPitHonestyAtTheSeam:
    def test_a_future_pulled_at_is_refused_by_the_guard(self, monkeypatch):
        """``pulled_at`` after the start-frozen ``ctx.as_of`` ⇒ the single PIT
        authority (PitGuard, never the adapter) ends the read
        ``FutureDataRefused`` — the adapter forwarded the row (probe called
        once), the guard refused it."""
        probe = _ProbeStub(_tape_envelope(pulled_at="2026-07-16T15:00:01"))
        env = _build_synthetic_env(monkeypatch, method_id="ohlcv", probe=probe)
        with pytest.raises(FutureDataRefused):
            env.drive(kind=ExecutionKind.LLM)
        assert len(probe.calls) == 1

    def test_a_missing_pulled_at_is_refused_as_missing_availability(
            self, monkeypatch):
        """No ``pulled_at`` ⇒ ``available_at=None`` is forwarded honestly and
        the guard refuses ``MissingAvailabilityRefused`` — never a fabricated
        availability."""
        probe = _ProbeStub(_tape_envelope(pulled_at=None))
        env = _build_synthetic_env(monkeypatch, method_id="ohlcv", probe=probe)
        with pytest.raises(MissingAvailabilityRefused):
            env.drive(kind=ExecutionKind.LLM)
        assert len(probe.calls) == 1


class TestDegradationMatrix:
    def test_core_method_outage_raises_after_chain_exhaustion(
            self, monkeypatch):
        """``ohlcv`` is a CORE method (``optional=False``, like
        ``verified_snapshot``): a ``status:planned`` probe advances the
        (single-entry) frozen chain via ``NotConfiguredError`` and exhaustion
        RAISES it — no fabricated row, no silent fallback, the refusal
        audited through the one sink.

        「控制器已裁」the production consequence is ACCEPTED for this phase:
        a ``verified_snapshot`` outage ⇒ pm node hard-fail ⇒ the deep run
        fails/refuses honestly (the fast chain stands) — cross-referenced
        into L3's D-2 ruling packet as the same-family precedent."""
        probe = _ProbeStub({"ok": True, "status": "planned", "n": 0,
                            "items": [], "error": "",
                            "note": "probe not implemented"})
        env = _build_synthetic_env(monkeypatch, method_id="ohlcv", probe=probe)
        with pytest.raises(NotConfiguredError, match="planned"):
            env.drive(kind=ExecutionKind.LLM)
        reasons = [r.reason_code for r in env.sink.records()]
        assert reasons.count("not_configured") == 1

    def test_optional_method_outage_exhausts_to_a_named_unavailable(
            self, monkeypatch):
        """``news`` is OPTIONAL: the same outage exhausts to an honest named
        ``UNAVAILABLE`` result — persisted, typed, zero fabricated success
        record — and the bridge completes."""
        probe = _ProbeStub({"ok": True, "status": "planned", "n": 0,
                            "items": [], "error": "",
                            "note": "probe not implemented"})
        env = _build_synthetic_env(monkeypatch, method_id="news", probe=probe)
        contributions = env.drive(kind=ExecutionKind.DETERMINISTIC)
        assert len(contributions) == 1
        c = contributions[0]
        assert c.tool_call_records == ()  # no fabricated success record
        assert len(c.data_result_refs) == 1
        result = env.stores.payloads.get(
            c.data_result_refs[0].payload_ref,
            expected_schema_ref=SchemaRef(name="NewsDataResult", version="1"))
        assert result.status is DataStatus.UNAVAILABLE
        assert result.data is None
        assert [a.outcome for a in result.attempts] == ["not_configured"]
        # the un-represented request ref surfaces as direct evidence.
        assert len(c.direct_evidence_refs) == 1
        assert c.direct_evidence_refs[0].schema_ref.name == "DataRequest"


# =========================================================================== #
# 6. lineage guards                                                            #
# =========================================================================== #
class TestLineageGuards:
    def test_the_l1_deleted_helpers_exist_nowhere_in_orchestration(self):
        """Their fate was decided ONCE, in L1 — the row-count partition never
        resurrects the deleted predicate/fact helpers (grep pin; the needles
        are concatenation-built with splits INSIDE the stems)."""
        pkg = _REPO_ROOT / "guanlan_v2" / "orchestration"
        hits: list[str] = []
        for path in sorted(pkg.rglob("*.py")):
            text = path.read_text(encoding="utf-8", errors="replace")
            for needle in _L1_DELETED_HELPER_NEEDLES:
                if needle in text:
                    hits.append(f"{path.relative_to(_REPO_ROOT)}: {needle}")
        assert hits == [], (
            f"an L1-deleted helper name was resurrected: {hits}")

    def test_task2_minor2_no_second_stage_call_site(self):
        """Task-2 review Minor #2 carry, CONFIRMED: this task adds NO second
        ``backend.stage`` call site — the delegated session drives the ONE
        existing gateway-adapter site, so the per-thread never-consumed guard
        needs no new test here."""
        module_src = inspect.getsource(DW)
        assert ".stage(" not in module_src
