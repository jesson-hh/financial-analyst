# -*- coding: utf-8 -*-
"""L3 Task 5 Step 4 — the D-2 outage consequence, pinned EXECUTABLE.

Ruling D-2 (ledger §9, Option A) accepted that the two granted evidence
readers' rows are ``always_invoke`` + ``success_requires_finalized_call=True``
(``row_min_finalized=1``), and therefore that **a data outage becomes a hard
node failure** for a reader whose entire value is the data.  The plan text said
that failure lands in ONE vocabulary; Task 1's measurement (ledger §8.2)
CORRECTED it — it lands in TWO, split by the method spec's ``optional`` bit:

============================  ==========  =====================================
method (row)                  optional    node outcome on outage
============================  ==========  =====================================
``verified_snapshot`` (core)  ``False``   route exhaustion RAISES the first
                                          retryable error, it escapes the
                                          bridge stage, and the executor maps
                                          it to ``FAILED`` /
                                          ``bridge_execution_error``
``news``            (optional) ``True``   route exhaustion builds an honest
                                          named ``UNAVAILABLE`` result with NO
                                          finalized ``ToolCallRecord``; the
                                          bridge COMPLETES and the node fails
                                          the tool-call discipline check:
                                          ``INCOMPLETE`` /
                                          ``tool_discipline_unmet``
============================  ==========  =====================================

Both are HARD failures — neither pretends to have read anything — which is the
substance of D-2 Option A.  The ledger's instruction to Task 5 is that **both
paths and both presets** are pinned, because pinning one leaves the other half
of the degradation path with no evidence at all.

The four cells this file owns (2 vocabularies x 2 presets):

1. full deep preset x CORE   — ``pv.technical`` / ``verified_snapshot``
2. full deep preset x OPTIONAL — ``text.news`` / ``news``
3. screening lane   x OPTIONAL — ``text.news`` / ``news`` (the same worker on
   the A-line; ledger §11.2 records that every screening run now forces a real
   news read, so this is the lane's live outage shape)
4. screening lane   x CORE   — **structurally unreachable, and pinned as
   exactly that**: the lane's nine workers carry exactly ONE reviewed prefetch
   row (``text.news`` / ``news``) and that method is ``optional=True``, so no
   scheduled seat on this lane can produce the core vocabulary.  The pin is
   falsifiable, not a shrug: the day a core-method row enters the screening
   lane it reddens and whoever put it there must drive cell 4 for real.

**Graph-level consequence** (``TestTheTrunkSurvivesTheOutage``): the two aux
readers reach the debate ONLY through ``DEGRADE`` dependencies and are absent
from the decision spine ``sentiment -> research-mgr -> pm -> trader``, so an
aux node that ends FAILED/INCOMPLETE cannot block the trunk.  That is the
executable form of "degrade badge present, trunk completes, run COMPLETED",
and it is what the mutation ``degrade -> REQUIRED`` reddens.  The end-to-end
observation of a COMPLETED ten-node run under a live outage is Task 7's
(the plan assigns the real run there); this file does not claim it.

Everything runs against the REAL sealed Phase-9 catalog, the REAL production
assembly, the REAL per-run subject projection and the REAL
``worker.execute_node``.  The outage is injected at the ONE production door
(``adapters/data_world._BACKEND``) as a probe that answers ``status: planned``
— the idiom ``test_production_data_provider.py::TestDegradationMatrix`` already
uses.  Zero network, zero LLM (both vocabularies are decided BEFORE the model
call at ``worker.py`` step 4b), zero ``var/`` writes.

Run: ``python -m pytest tests/orchestration/test_l3_d2_outage.py -q``
"""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

import guanlan_v2.orchestration.adapters.data_world as DW
import guanlan_v2.orchestration.bootstrap as bs
import guanlan_v2.orchestration.memory.runtime as MR
import guanlan_v2.orchestration.worker as W
from guanlan_v2.orchestration import presets as P
from guanlan_v2.orchestration.adapters import chain
from guanlan_v2.orchestration.adapters.data_world import (
    production_data_provider_factory,
    production_data_recipe,
    register_production_data_provider,
)
from guanlan_v2.orchestration.adapters.live_data import (
    LiveClientSource,
    build_online_capture_manifest,
    build_online_data_context,
)
from guanlan_v2.orchestration.admission import (
    ApprovalSubmission,
    PlanAdmissionService,
)
from guanlan_v2.orchestration.approval import admit_after_approval
from guanlan_v2.orchestration.budget import BudgetLedger
from guanlan_v2.orchestration.catalog_runtime import TrustedFactoryRegistry
from guanlan_v2.orchestration.context import InputSnapshot, RunBudget, RunContext
from guanlan_v2.orchestration.data.catalog import phase3_data_surface
from guanlan_v2.orchestration.enums import (
    ApprovalDecision,
    ApprovalPolicy,
    DependencyPolicy,
    NodeStatus,
)
from guanlan_v2.orchestration.events import PlanApproval
from guanlan_v2.orchestration.eventstore import (
    RuntimeStores,
    SchemaRegistryResolver,
)
from guanlan_v2.orchestration.pipeline import assembly as ASM
from guanlan_v2.orchestration.pipeline.assembly import (
    PRODUCTION_PRESETS_DIR,
    build_production_catalog_runtime,
    load_phase10_preset_registry,
    production_bridge_view,
)
from guanlan_v2.orchestration.pipeline.contracts import (
    CandidateEntry,
    CandidateSlate,
    RunSubject,
)
from guanlan_v2.orchestration.pipeline.deep_decide import (
    DEEP_DECIDE_PRESET_ID,
    materialize_deep_decide_draft,
)
from guanlan_v2.orchestration.pipeline.screening import (
    SCREENING_LANE_PRESET_ID,
    SCREENING_LANE_WORKER_IDS,
    build_screening_batch,
)
from guanlan_v2.orchestration.refs import PayloadRef, SchemaRef, TypedPayloadRef
from guanlan_v2.orchestration.runtime_support import STATIC_RUNTIME_PROFILE_V2
from guanlan_v2.orchestration.spec import OrchestrationRequest, validate_plan_draft

UTC = timezone.utc
#: the frozen instant: 2026-07-16 07:00Z == 15:00 Asia/Shanghai (A-share close).
AS_OF = datetime(2026, 7, 16, 7, 0, tzinfo=UTC)
PHASE8_REGISTRY_DIGEST = (
    "201e7f2cccdf56369e6c8244982eb0647172a7be7d2b8060f8eebb75de6ca650"
)
CTX_SR = SchemaRef(name="ContextSnapshot", version="1")
MANIFEST_SR = SchemaRef(name="DataSnapshotManifest", version="1")
SUBJECT_SR = SchemaRef(name="RunSubject", version="1")
SLATE_SR = SchemaRef(name="CandidateSlate", version="1")

#: the campaign subject code (五粮液).
CODE = "000858"

#: the two granted (worker, method) pairs whose outage this file pins, and the
#: vocabulary each one lands in.  Derived from the method spec's ``optional``
#: bit in :class:`TestTheVocabularySplitIsTheMethodSpec`, never assumed.
CORE_PAIR = ("pv.technical", "verified_snapshot")
OPTIONAL_PAIR = ("text.news", "news")


class _FixedClock:
    def __init__(self, now: datetime = AS_OF) -> None:
        self._now = now

    def now(self) -> datetime:
        return self._now


class _PoisonedStores:
    """The rowless memory path must never touch the stores."""

    def __getattr__(self, name):  # pragma: no cover - failing is the assertion
        raise AssertionError(f"stores.{name} touched by the rowless memory path")


class _OutageProbe:
    """A ``live_client.probe`` that answers ``status: planned`` for every call.

    The adapter turns that into ``NotConfiguredError`` per attempt, the frozen
    single-entry route exhausts, and ``data/registry.py::_exhaustion`` decides
    the vocabulary on the method spec's ``optional`` bit.  This is a real
    outage shape (a source that answers but serves nothing), not a monkeypatched
    exception at the seam under test.
    """

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def __call__(self, source, code="", date="", limit=20, timeout=90):
        self.calls.append({"source": source, "code": code, "date": date})
        return {"ok": True, "status": "planned", "n": 0, "items": [],
                "error": "", "note": "probe not implemented"}


class _RefusalSpy:
    """The refusal sink the gateway writes each failed-attempt refusal to.

    Real, not ``None``: with no sink the gateway raises ``AttributeError`` inside
    the bridge stage and the executor faithfully reports THAT as the
    ``bridge_execution_error`` — a harness bug wearing the outage's clothes.
    Measured during this task; keeping the spy is what makes ``error_type``
    load-bearing evidence about the data source rather than about the test.
    """

    def __init__(self) -> None:
        self.records: list[dict] = []

    def record(self, **kwargs) -> None:
        self.records.append(kwargs)


class _SubjectCommitter:
    """The injected subject-commit port (the screening-test double).

    ``RunSubject@1`` is not registered in the sealed Phase-9 registry, so a real
    ``stores.payloads.put`` cannot commit one; the ref this returns genuinely
    binds the subject's content digest.
    """

    def __call__(self, subject: RunSubject) -> TypedPayloadRef:
        return TypedPayloadRef(
            schema_ref=SUBJECT_SR,
            payload_ref=PayloadRef(
                namespace="main", object_id=f"subject-{subject.code}",
                content_digest=subject.semantic_digest()))


def _subject_ref(code: str = CODE) -> TypedPayloadRef:
    return _SubjectCommitter()(RunSubject(code=code, as_of=AS_OF))


def _recipe_capture(recipe, *, snapshot_id: str):
    """A recipe-built ``(manifest, DataContext)`` pair — the Lane-0 producer
    shape, built from the reviewed builders (the L2-b Task-3 harness idiom)."""
    manifest = build_online_capture_manifest(
        data_snapshot_id=snapshot_id, as_of=AS_OF, timezone="Asia/Shanghai",
        calendar_id="cn_a_share",
        routing_snapshot_digest=recipe.routing_snapshot_digest,
        schema_registry_digest=recipe.routing.schema_registry_digest)
    dc = build_online_data_context(
        clock=_FixedClock(), source_config=recipe.source_config,
        source_registry=recipe.registry.snapshot(), routing=recipe.routing,
        manifest=manifest)
    return manifest, dc


# =========================================================================== #
# module fixtures — the REAL sealed chain through the REAL assembly             #
# =========================================================================== #
@pytest.fixture(scope="module")
def reg9():
    return chain.build_phase9_registry(PHASE8_REGISTRY_DIGEST)


@pytest.fixture(scope="module")
def snap():
    return chain.phase9_catalog_snapshot()


@pytest.fixture(scope="module")
def recipe():
    return production_data_recipe()


@pytest.fixture(scope="module")
def bundle(snap):
    return build_production_catalog_runtime(snap)


@pytest.fixture(scope="module")
def view(bundle):
    return production_bridge_view(bundle.runtime)


@pytest.fixture(scope="module")
def surface():
    return phase3_data_surface()


def _fresh_stores(reg9):
    resolver = SchemaRegistryResolver()
    resolver.register(reg9)
    return RuntimeStores(
        resolver=resolver, clock=_FixedClock(),
        allowed_cell_namespaces=(W.PROMPT_CELL_NAMESPACE,))


def _persisted_context(reg9, recipe, *, run_tag: str):
    """A committed ContextSnapshot over a RECIPE-built DataContext + its
    per-run manifest, both persisted — exactly what
    ``ProductionDataWorldResolver.world_for`` reads.  A pilot-era context would
    be refused there, so this is the only honest way to reach the outage."""
    stores = _fresh_stores(reg9)
    manifest, dc = _recipe_capture(recipe, snapshot_id=f"l3d2-{run_tag}")
    context = P.build_empty_memory_context(
        data_context=dc, stores=stores,
        registry_digest=reg9.registry_digest, built_at=AS_OF).context
    ctx_ref = stores.payloads.put(
        CTX_SR, context, registry_digest=reg9.registry_digest,
        namespace="main", idempotency_key=f"l3d2:{run_tag}:ctx")
    stores.payloads.put(
        MANIFEST_SR, manifest, registry_digest=reg9.registry_digest,
        namespace="main", idempotency_key=f"l3d2:{run_tag}:manifest")
    return SimpleNamespace(
        stores=stores, context=context, ctx_ref=ctx_ref,
        ctx_typed=TypedPayloadRef(schema_ref=CTX_SR, payload_ref=ctx_ref))


def _admit(base, *, snap, bundle, view, reg9, request, draft, tag: str):
    """Drive one draft through the REAL admission pipeline to an admitted Plan."""
    approvals: dict = {}
    run_budget = RunBudget(ledger_id=f"led-{tag}", max_tokens=32_000_000,
                           max_llm_invocations=128, max_concurrency=16)
    service = PlanAdmissionService(
        run_id=draft.run_id, requests={request.request_id: request},
        drafts={draft.id: draft},
        contexts={base.context.content_digest: base.context},
        attestations={}, approvals=approvals, catalog=bundle.runtime,
        bridge_view=view, phase1_registry=reg9,
        runtime_registry_digest=reg9.registry_digest,
        profile=STATIC_RUNTIME_PROFILE_V2, stores=base.stores,
        run_budget=run_budget, clock=_FixedClock())
    prep = service.prepare_candidate(draft.id, request_id=request.request_id)
    assert prep.support_report.supported, [
        (i.code, i.node_id) for i in prep.support_report.issues]
    cand, res = service.persist_and_reserve_candidate(
        prep, idempotency_key=f"{tag}-reserve")
    cd = cand.candidate_plan_digest
    approvals[(request.request_id, cd)] = PlanApproval(
        request_id=request.request_id, candidate_plan_digest=cd,
        decision=ApprovalDecision.APPROVED, actor_id="human-l3d2",
        decided_at=AS_OF)
    ev = service.record_approval(
        cd, ApprovalSubmission(
            request_id=request.request_id, candidate_plan_digest=cd,
            decision=ApprovalDecision.APPROVED),
        authenticated_actor="human-l3d2", idempotency_key=f"{tag}-approve")
    plan, _ = admit_after_approval(
        admission=service, candidate_id=cd, reservation_id=res.reservation_id,
        approval_event_id=ev.event_id, idempotency_key=f"{tag}-freeze")
    return SimpleNamespace(plan=plan, reservation=res, run_budget=run_budget,
                           dispatch=service.verify_for_dispatch(plan.plan_digest))


# --------------------------------------------------------------------------- #
# the two lanes                                                                 #
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="module")
def deep_env(reg9, snap, bundle, view, recipe):
    """The FULL ten-node deep preset, admitted for real over a recipe context."""
    base = _persisted_context(reg9, recipe, run_tag="deep")
    request = OrchestrationRequest(
        request_id="req-l3d2-deep", goal="观澜 · 落子深度研判 (D-2 断供)",
        workflow="orchestrate_only", fallback_preset_id=None,
        approval_policy=ApprovalPolicy.REQUIRED)
    materialized = materialize_deep_decide_draft(
        request=request,
        preset_registry=load_phase10_preset_registry(PRODUCTION_PRESETS_DIR),
        preset_id=DEEP_DECIDE_PRESET_ID, context_snapshot_ref=base.ctx_ref,
        subject_ref=_subject_ref(), subject=RunSubject(code=CODE, as_of=AS_OF),
        clock=_FixedClock(), context=base.context,
        catalog=snap, schema_registry=reg9, draft_id="draft-l3d2-deep",
        run_id="run-l3d2-deep")
    phase1 = validate_plan_draft(
        materialized.draft, request=request, context=base.context,
        catalog=snap, schema_registry=reg9)
    assert phase1.valid is True, [(i.code, i.node_id) for i in phase1.issues]
    admitted = _admit(base, snap=snap, bundle=bundle, view=view, reg9=reg9,
                      request=request, draft=materialized.draft, tag="l3d2deep")
    return SimpleNamespace(
        base=base, request=request, draft=materialized.draft,
        materialized=materialized, admitted=admitted,
        subject_params=materialized.subject_params, snapshot=snap,
        registry=reg9, lane="deep")


@pytest.fixture(scope="module")
def screening_env(reg9, snap, bundle, view, recipe):
    """ONE screening lane (the A-line), admitted for real over a recipe context."""
    base = _persisted_context(reg9, recipe, run_tag="screen")
    slate = CandidateSlate(
        source_kind="v4", as_of=AS_OF, top_n=5,
        entries=(CandidateEntry(code=CODE, rank=1, score=0.9),))
    slate_ref = TypedPayloadRef(
        schema_ref=SLATE_SR,
        payload_ref=PayloadRef(namespace="main", object_id="slate-l3d2",
                               content_digest=slate.semantic_digest()))
    base_request = OrchestrationRequest(
        request_id="req-l3d2-screen", goal="观澜 · 选股批量研判 (D-2 断供)",
        workflow="orchestrate_only",
        fallback_preset_id=SCREENING_LANE_PRESET_ID,
        approval_policy=ApprovalPolicy.REQUIRED)
    built = build_screening_batch(
        slate, preset_registry=load_phase10_preset_registry(PRODUCTION_PRESETS_DIR),
        clock=_FixedClock(), base_request=base_request, slate_ref=slate_ref,
        subject_committer=_SubjectCommitter(), context=base.context,
        context_snapshot_ref=base.ctx_ref, catalog=snap, schema_registry=reg9)
    lane = built.lanes[0]
    phase1 = validate_plan_draft(
        lane.draft, request=lane.request, context=base.context, catalog=snap,
        schema_registry=reg9)
    assert phase1.valid is True, [(i.code, i.node_id) for i in phase1.issues]
    admitted = _admit(base, snap=snap, bundle=bundle, view=view, reg9=reg9,
                      request=lane.request, draft=lane.draft, tag="l3d2screen")
    return SimpleNamespace(
        base=base, request=lane.request, draft=lane.draft, admitted=admitted,
        subject_params=lane.subject_params, snapshot=snap, registry=reg9,
        lane="screening")


# --------------------------------------------------------------------------- #
# the outage drive                                                              #
# --------------------------------------------------------------------------- #
def _outage_factories(env, bundle, surface, probe, monkeypatch):
    """The three reviewed provider recipes, with the ONE production data door
    swapped for an outage backend and the per-run SUBJECT view on top.

    The subject view is not optional decoration: the granted rows bind their
    params through ``node_param`` pointers served out-of-band by the L1
    projection, so an unscoped provider refuses at the runner seam before any
    read is even attempted — the outage would never be reached.
    """
    outage_backend = DW.ThreadConfinedDataBackend(
        {surface.source_ref.id: LiveClientSource(
            probe_fn=probe, resolve_source_fn=lambda s: s)})
    monkeypatch.setattr(DW, "_BACKEND", outage_backend)
    assert DW.production_data_backend() is outage_backend

    factories = TrustedFactoryRegistry(bundle.runtime)
    bs.register_lane0_experience_factories(
        factories=factories, catalog=bs.load_lane0_catalog(), pool=None,
        registry=env.registry, experience_views=(), experience_scaler=None,
        as_of=AS_OF)
    MR.register_phase3_memory_provider_factory(
        factories=factories, stores=_PoisonedStores())
    register_production_data_provider(
        factories=factories, stores=env.base.stores,
        schema_resolver=env.base.stores.resolver, clock=_FixedClock(),
        catalog_runtime=bundle.runtime)
    assert env.subject_params is not None, (
        "the lane did not stamp subject params; the L1 projection is the only "
        "source the granted rows' node_param pointers have")
    return ASM._SubjectScopedFactories(
        factories, provider_ref=surface.provider_ref,
        factory=production_data_provider_factory(
            env.subject_params, stores=env.base.stores,
            schema_resolver=env.base.stores.resolver, clock=_FixedClock(),
            catalog_runtime=bundle.runtime))


def _drive_under_outage(env, bundle, view, surface, worker_id, monkeypatch):
    """Drive ONE node of ``env``'s admitted plan through the REAL
    ``worker.execute_node`` while its data source is out.

    ``model_gateway=None`` is deliberate and load-bearing: both outage
    vocabularies are decided at ``worker.py`` steps (4)/(6), BEFORE the single
    model call at step (4b).  If either arm ever started reaching the model, this
    drive would raise instead of returning a NodeRun — the harness cannot
    silently start testing an LLM path.
    """
    probe = _OutageProbe()
    factories = _outage_factories(env, bundle, surface, probe, monkeypatch)
    plan = env.admitted.plan
    node = next(n for n in plan.nodes if n.worker_id == worker_id)
    worker = {w.id: w for w in env.snapshot.workers}[worker_id]
    runtime = W.ExecutionRuntime(
        catalog=bundle.runtime, bridge_view=view, factories=factories,
        support_report=env.admitted.dispatch.support_report,
        runtime_registry_digest=env.registry.registry_digest)
    sequencer = W.ExecutionEvidenceSequencer(node_id=node.id, attempt=1)
    resolver = W.ExecutionBridgeResolver(
        runtime=runtime, node=node, worker=worker, sequencer=sequencer)
    assert "data.runtime" in resolver.required_bridge_ids
    writer = W.BridgeEvidenceWriter(
        stores=env.base.stores, sequencer=sequencer, run_id=plan.run_id,
        plan_digest=plan.plan_digest, node_id=node.id,
        data_registry_digest=env.registry.registry_digest,
        runtime_registry_digest=env.registry.registry_digest)
    prepared = resolver.prepare_input(
        plan=plan, context_snapshot_ref=env.base.ctx_typed,
        evidence_writer=writer)
    summaries = [s for s in env.admitted.dispatch.support_report
                 .bridge_support_summaries if s.node_id == node.id]
    refusals = _RefusalSpy()
    gateway = W.CapabilityGateway(
        plan_digest=plan.plan_digest, worker=worker,
        summaries={s.summary_digest: s for s in summaries},
        catalog=bundle.runtime, factories=factories,
        phase1_registry=env.registry, refusal_sink=refusals,
        clock=_FixedClock(), sequencer=sequencer)
    gateway.bind_reader(env.base.stores.payloads)
    input_snapshot = InputSnapshot.build(
        snapshot_id=f"isnap-{env.lane}-{node.id}", run_id=plan.run_id,
        plan_id=plan.plan_id, plan_digest=plan.plan_digest, node_id=node.id,
        layer_index=0, attempt=1, context_snapshot_ref=env.base.ctx_typed,
        artifact_inputs=(), data_result_refs=(), memory_record_refs=(),
        readiness="ready", built_at=AS_OF)
    ledger = BudgetLedger(
        sink=env.base.stores.budget_event_sink(
            run_id=plan.run_id, ledger_id=env.admitted.run_budget.ledger_id),
        run_budget=env.admitted.run_budget)
    node_res = ledger.reserve_node(
        plan_reservation_id=env.admitted.reservation.reservation_id,
        node_id=node.id, attempt=1, tokens=1000, llm_invocations=0,
        concurrency=1, idempotency_key=f"l3d2-{env.lane}-{node.id}")
    run_ctx = RunContext(
        run_id=plan.run_id, data=env.base.context.data_context,
        context_snapshot_id=env.base.context.snapshot_id,
        memory_snapshot_hash=env.base.context.memory_snapshot_hash,
        budget=env.admitted.run_budget,
        cancellation_token_id=f"cancel-l3d2-{env.lane}")
    node_run, artifact = W.execute_node(
        plan, node, runtime=runtime, prepared_bridges=prepared,
        input_snapshot=input_snapshot, ctx=run_ctx, node_reservation=node_res,
        bridge_resolver=resolver, model_gateway=None,
        capability_gateway=gateway, registry=env.registry,
        stores=env.base.stores, clock=_FixedClock(), attempt=1)
    return SimpleNamespace(node_run=node_run, artifact=artifact, node=node,
                           probe=probe, gateway=gateway, refusals=refusals)


# =========================================================================== #
# 0. the split itself — measured from the method specs, never assumed           #
# =========================================================================== #
class TestTheVocabularySplitIsTheMethodSpec:
    def test_the_two_granted_methods_sit_on_opposite_sides_of_optional(
            self, surface):
        assert surface.spec_by_method[CORE_PAIR[1]].optional is False
        assert surface.spec_by_method[OPTIONAL_PAIR[1]].optional is True

    def test_every_granted_row_is_the_d2_shape(self, surface):
        """Ruling D-2 Option A on the wire: every row added this phase is
        ``always_invoke`` + ``success_requires_finalized_call`` (min=1), which is
        precisely why an outage cannot end in a claimed success."""
        rows = {(r.worker_id, r.method_ref.id): r
                for r in surface.prefetch_binding.operations}
        for pair in (CORE_PAIR, OPTIONAL_PAIR):
            row = rows[pair]
            assert row.invocation_mode == "always_invoke"
            assert row.success_requires_finalized_call is True
            assert row.row_min_finalized == 1


# =========================================================================== #
# 1+2. the FULL deep preset — both vocabularies                                 #
# =========================================================================== #
class TestTheFullDeepPresetUnderOutage:
    def test_core_verified_snapshot_outage_fails_the_node_loudly(
            self, deep_env, bundle, view, surface, monkeypatch):
        """Cell 1. ``pv.technical`` reads a CORE method: route exhaustion RAISES,
        the exception escapes the bridge stage and the executor seals
        ``FAILED`` / ``bridge_execution_error`` naming the adapter's own error
        type.  Nothing is fabricated and nothing claims success."""
        out = _drive_under_outage(
            deep_env, bundle, view, surface, CORE_PAIR[0], monkeypatch)
        run = out.node_run
        assert run.status is NodeStatus.FAILED
        assert run.reason_code == "bridge_execution_error"
        assert run.error_type == "NotConfiguredError", run.reason
        assert out.artifact is None
        assert out.probe.calls, "the outage was never actually reached"

    def test_optional_news_outage_leaves_the_node_incomplete_on_discipline(
            self, deep_env, bundle, view, surface, monkeypatch):
        """Cell 2. ``text.news`` reads an OPTIONAL method: exhaustion builds an
        honest named UNAVAILABLE with NO finalized ToolCallRecord, the bridge
        COMPLETES, and the node dies at the tool-call discipline check its own
        ``always_invoke`` row set to 1."""
        out = _drive_under_outage(
            deep_env, bundle, view, surface, OPTIONAL_PAIR[0], monkeypatch)
        run = out.node_run
        assert run.status is NodeStatus.INCOMPLETE
        assert run.reason_code == "tool_discipline_unmet"
        assert "finalized 0" in run.reason and "minimum 1" in run.reason
        assert out.artifact is None
        assert out.gateway.finalized_records() == ()
        assert out.probe.calls, "the outage was never actually reached"

    def test_the_two_vocabularies_are_genuinely_different(
            self, deep_env, bundle, view, surface, monkeypatch):
        """The correction to the plan text, asserted as a difference rather than
        as two independent facts: the SAME outage, the SAME preset, two
        different terminal vocabularies — so pinning one would have left the
        other unproven."""
        core = _drive_under_outage(
            deep_env, bundle, view, surface, CORE_PAIR[0], monkeypatch)
        opt = _drive_under_outage(
            deep_env, bundle, view, surface, OPTIONAL_PAIR[0], monkeypatch)
        assert core.node_run.status is not opt.node_run.status
        assert core.node_run.reason_code != opt.node_run.reason_code
        assert {core.node_run.reason_code, opt.node_run.reason_code} == {
            "bridge_execution_error", "tool_discipline_unmet"}


# =========================================================================== #
# 3+4. the SCREENING lane — the optional arm live, the core arm unreachable      #
# =========================================================================== #
class TestTheScreeningLaneUnderOutage:
    def test_optional_news_outage_is_the_same_on_the_a_line(
            self, screening_env, bundle, view, surface, monkeypatch):
        """Cell 3. ``text.news`` is one of the screening lane's nine workers and
        its row is ``always_invoke``, so EVERY screening run forces a real news
        read (ledger §11.2).  Its outage lands in the same honest vocabulary on
        this lane as on the deep lane — pinned here rather than implied."""
        out = _drive_under_outage(
            screening_env, bundle, view, surface, OPTIONAL_PAIR[0], monkeypatch)
        run = out.node_run
        assert run.status is NodeStatus.INCOMPLETE
        assert run.reason_code == "tool_discipline_unmet"
        assert "finalized 0" in run.reason and "minimum 1" in run.reason
        assert out.gateway.finalized_records() == ()
        assert out.probe.calls, "the outage was never actually reached"

    def test_the_core_vocabulary_has_no_carrier_on_this_lane(self, surface):
        """Cell 4, pinned as the structural fact it is rather than skipped.

        The screening lane schedules exactly ONE worker carrying a reviewed
        prefetch row, and that row's method is OPTIONAL — so no scheduled seat
        on this lane can reach the core-raise vocabulary.  The day a core-method
        row enters this lane (a ``dec.pm`` / ``pv.technical`` seat, or a core
        grant for an existing one) this pin reddens and cell 4 must be driven
        for real instead of reasoned about."""
        rows = {r.worker_id: r.method_ref.id
                for r in surface.prefetch_binding.operations}
        on_lane = {wid: mid for wid, mid in rows.items()
                   if wid in set(SCREENING_LANE_WORKER_IDS)}
        assert on_lane == {OPTIONAL_PAIR[0]: OPTIONAL_PAIR[1]}
        assert all(surface.spec_by_method[mid].optional is True
                   for mid in on_lane.values()), (
            "a CORE-method row entered the screening lane: the core outage "
            "vocabulary is now reachable here and must be pinned for real")


# =========================================================================== #
# 5. the graph consequence — the trunk cannot be blocked by an aux outage        #
# =========================================================================== #
class TestTheTrunkSurvivesTheOutage:
    """The other half of D-2 Option A's "bounded consequence" claim.

    A hard aux failure is only acceptable because the two readers are AUX: they
    reach the debate through ``DEGRADE`` dependencies and the decision spine
    never touches them.  This is what makes "the trunk completes / the run ends
    COMPLETED" true, and it is exactly what the mutation ``DEGRADE -> REQUIRED``
    destroys.  (The end-to-end COMPLETED observation under a live outage is
    Task 7's real run; this class pins the property that makes it possible.)
    """

    TRUNK = ("text.sentiment", "dec.research_mgr", "dec.pm", "dec.trader")

    def _record(self):
        return load_phase10_preset_registry(PRODUCTION_PRESETS_DIR).get(
            DEEP_DECIDE_PRESET_ID)

    def test_neither_granted_reader_is_on_the_decision_spine(self):
        record = self._record()
        by_id = {n.id: n for n in record.nodes}
        trunk_nodes = {n.id for n in record.nodes if n.worker_id in self.TRUNK}
        aux_nodes = {n.id for n in record.nodes
                     if n.worker_id in (CORE_PAIR[0], OPTIONAL_PAIR[0])}
        assert len(aux_nodes) == 2, sorted(aux_nodes)
        assert not (aux_nodes & trunk_nodes)
        # …and no trunk node depends on either of them, at any policy.
        for nid in trunk_nodes:
            depends = {d.upstream_node_id for d in by_id[nid].dependencies}
            assert not (depends & aux_nodes), (
                f"trunk node {nid!r} depends on an aux evidence node: an outage "
                "would stop being bounded")

    def test_every_dependency_on_a_granted_reader_is_degrade(self):
        """The load-bearing half: every edge OUT of the two aux readers is a
        ``DEGRADE`` dependency, so a FAILED/INCOMPLETE aux node weakens the
        debate instead of blocking the run.  Flip one to REQUIRED and this
        reddens — that is the mutation D-2's bounded-consequence claim rests
        on."""
        record = self._record()
        aux_nodes = {n.id for n in record.nodes
                     if n.worker_id in (CORE_PAIR[0], OPTIONAL_PAIR[0])}
        edges = [(n.id, d.upstream_node_id, d.policy) for n in record.nodes
                 for d in n.dependencies if d.upstream_node_id in aux_nodes]
        assert edges, "the aux readers feed nothing; the preset changed shape"
        offenders = [e for e in edges if e[2] is not DependencyPolicy.DEGRADE]
        assert offenders == [], (
            f"a non-DEGRADE dependency on an aux evidence node: {offenders}")

    def test_the_degrade_policy_pin_is_load_bearing(self):
        """Positive control for the mutation above: the same assertion run over
        a record whose aux edge was flipped to REQUIRED DOES fail, so a green
        result here is evidence rather than decoration."""
        record = self._record()
        aux_nodes = {n.id for n in record.nodes
                     if n.worker_id in (CORE_PAIR[0], OPTIONAL_PAIR[0])}
        victim = next(n for n in record.nodes
                      if any(d.upstream_node_id in aux_nodes for d in n.dependencies))
        mutated = victim.model_copy(update={
            "dependencies": tuple(
                d.model_copy(update={"policy": DependencyPolicy.BLOCK,
                                     "accept_statuses": frozenset(
                                         {NodeStatus.COMPLETED})})
                if d.upstream_node_id in aux_nodes else d
                for d in victim.dependencies)})
        edges = [(mutated.id, d.upstream_node_id, d.policy)
                 for d in mutated.dependencies
                 if d.upstream_node_id in aux_nodes]
        assert any(p is not DependencyPolicy.DEGRADE for _n, _d, p in edges)
