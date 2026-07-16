# -*- coding: utf-8 -*-
"""Phase 2 · Task 9.1 — the reviewed three-final-worker pilot end-to-end.

Locks the full static-runtime admission→dispatch path on the *real* Task 3 pilot
catalog (``text.sentiment -> dec.research_mgr -> dec.pm`` producing
``SentimentReport -> ResearchPlan -> PortfolioDecision``) with a trusted fake
single-shot :class:`ModelGateway`. The nine reviewed path steps are each
exercised:

1. build/verify the material-aware final-worker catalog snapshot
   (:func:`load_pilot_catalog`);
2. a strict PRESET :class:`OrchestrationRequest` + canonical empty-memory
   :class:`ContextSnapshot` (persisted EmptyMemory* facts, exact main
   ``TypedPayloadRef``s, ``memory_session_id=None``, ``runtime_requirements_ref=None``)
   + a PlanDraft with exact registry/catalog/budget bindings;
3. Phase-1 validation; 4. RuntimeSupportReport; 5. plan reservation;
6. REQUIRED authenticated approval; 7. Phase-1 freeze + PlanAdmitted;
8. bounded execution (typed InputSnapshots/NodeRuns/Artifacts/LayerCommits +
   provenance); 9. replay + final PortfolioDecision validation.

Capability-schema decision: ``dec.pm`` declares ``tool_calls=optional`` and no
execution bridge activates for it (the pilot catalog carries no bridge-marker
guardrails), so the fake gateway returns a ``PortfolioDecision`` directly without
any capability tool call and ``cap.ashare_constraint_check`` (whose
``AshareConstraintQuery@1``/``AshareConstraintResult@1`` schemas are registered
nowhere) is never resolved. The runtime golden is therefore untouched.

Run: ``python -m pytest tests/orchestration/test_pilot_runtime.py -v``
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest

from guanlan_v2.orchestration import dag as D
from guanlan_v2.orchestration import presets as P
from guanlan_v2.orchestration import worker as W
from guanlan_v2.orchestration.budget import BudgetLedger
from guanlan_v2.orchestration.catalog_runtime import (
    BridgeCatalogView,
    CatalogMaterialError,
    CatalogRuntime,
    InMemoryMaterialSource,
    TrustedFactoryRegistry,
    load_pilot_catalog,
)
from guanlan_v2.orchestration.context import RunBudget, RunContext
from guanlan_v2.orchestration.enums import (
    ApprovalDecision,
    ApprovalPolicy,
    DataMode,
    NodeStatus,
    PlanSource,
)
from guanlan_v2.orchestration.eventstore import (
    EventRefusalAuditSink,
    RuntimeStores,
    SchemaRegistryResolver,
)
from guanlan_v2.orchestration.events import PlanApproval
from guanlan_v2.orchestration.pool import ArtifactPool
from guanlan_v2.orchestration.refs import PayloadRef, SchemaRef
from guanlan_v2.orchestration.runtime_contracts import (
    RunResult,
    default_audit_detail_registry,
    phase2_runtime_registry,
    static_runtime_profile,
)
from guanlan_v2.orchestration.schema_registry import default_registry
from guanlan_v2.orchestration.schemas import (
    Confidence,
    PortfolioDecision,
    ResearchPlan,
    SentimentBand,
    SentimentReport,
)
from guanlan_v2.orchestration.enums import PortfolioRating
from guanlan_v2.orchestration.admission import (
    AdmissionRejected,
    ApprovalSubmission,
    PlanAdmissionService,
    check_report_binding,
)

UTC = timezone.utc

_SR_SENTIMENT = SchemaRef(name="SentimentReport", version="1")
_SR_RESEARCH = SchemaRef(name="ResearchPlan", version="1")
_SR_DECISION = SchemaRef(name="PortfolioDecision", version="1")


class FixedClock:
    """A deterministic aware-UTC clock (constant instant → byte-stable replay)."""

    def now(self) -> datetime:
        return datetime(2026, 7, 17, 2, 0, tzinfo=UTC)


def _sentiment() -> SentimentReport:
    return SentimentReport(
        overall_band=SentimentBand.NEUTRAL, overall_score=5.0,
        confidence=Confidence.MEDIUM, narrative="Balanced flow with mild caution.")


def _research() -> ResearchPlan:
    return ResearchPlan(
        recommendation=PortfolioRating.HOLD, rationale="Hold pending confirmation.",
        strategic_actions=("Monitor breadth", "Re-check on catalyst"))


def _decision() -> PortfolioDecision:
    return PortfolioDecision(
        rating=PortfolioRating.HOLD, executive_summary="Advisory hold.",
        investment_thesis="Risk/reward balanced; no execution authority.")


#: reviewed per-worker payloads the trusted fake single-shot gateway returns.
_PAYLOAD_BY_WORKER = {
    "text.sentiment": _sentiment,
    "dec.research_mgr": _research,
    "dec.pm": _decision,
}


class FakePilotModelGateway:
    """A trusted single-shot gateway that enforces the exact prompt binding and
    returns the reviewed payload for each pilot worker (never a tool loop)."""

    def __init__(self, stores):
        self._stores = stores

    def invoke(self, request, *, prompt_assembly_ref):
        record = W.verify_model_request_binding(
            request, prompt_assembly_ref, reader=self._stores.payloads)
        factory = _PAYLOAD_BY_WORKER.get(record.worker_id)
        if factory is None:  # pragma: no cover - defensive
            raise AssertionError(f"unexpected pilot worker {record.worker_id!r}")
        return W.ModelResult(
            payload=factory(), rendered_text=f"rendered:{record.worker_id}",
            number_anchors=(), input_tokens=13, output_tokens=9, provider="fake",
            model="fake-pilot", provider_response_id=f"resp-{record.node_id}")


class PilotEnv:
    """The fully wired pilot admission→dispatch environment."""

    def __init__(self, **kw):
        self.__dict__.update(kw)

    def run(self, *, runtime_limit=3, recorder=None, model_gateway="__default__",
            runtime=None):
        rec = recorder if recorder is not None else D.RunRecorder()
        mg = self.model_gateway if model_gateway == "__default__" else model_gateway
        result = asyncio.run(D.run_plan(
            self.plan.plan_digest, self.run_ctx, admission=self.service, pool=self.pool,
            budget=self.budget, runtime=runtime or self.runtime, registry=self.registry,
            stores=self.stores, runtime_limit=runtime_limit, clock=self.clock,
            refusal_sink=self.refusal_sink, model_gateway=mg, recorder=rec))
        return result, rec


def build_pilot_env(*, suffix: str = "") -> PilotEnv:
    registry = default_registry()
    clock = FixedClock()
    req_id = f"req-pilot{suffix}"

    # -- step 1: material-aware final-worker catalog snapshot -------------- #
    catalog_runtime = load_pilot_catalog()
    snapshot = catalog_runtime.snapshot
    view = BridgeCatalogView.build(catalog_runtime, {})

    resolver = SchemaRegistryResolver()
    resolver.register(registry)
    rt_reg = phase2_runtime_registry(registry.registry_digest)
    rt_digest = resolver.register(rt_reg)
    stores = RuntimeStores(
        resolver=resolver, clock=clock, allowed_cell_namespaces=(W.PROMPT_CELL_NAMESPACE,))

    # -- step 2: PRESET request + canonical empty-memory context + draft --- #
    request = P.preset_orchestration_request(request_id=req_id)
    mem = P.build_empty_memory_context(
        data_context=P.pilot_data_context(as_of=clock.now()),
        stores=stores, registry_digest=rt_digest, built_at=clock.now())
    context, binding = mem.context, mem.binding
    draft = P.build_pilot_draft(
        catalog=catalog_runtime, registry=registry, context=context,
        request_id=request.request_id, run_id=f"run-pilot{suffix}", as_of=clock.now())

    audit_reg = default_audit_detail_registry()
    audit_reg.seal()
    refusal_sink = EventRefusalAuditSink(detail_registry=audit_reg, clock=clock)
    run_budget = RunBudget(
        ledger_id="led-pilot", max_tokens=4_000_000, max_llm_invocations=16,
        max_concurrency=8)

    factories = TrustedFactoryRegistry(catalog_runtime)
    model_gateway = FakePilotModelGateway(stores)

    approvals: dict = {}
    service = PlanAdmissionService(
        run_id=draft.run_id, requests={request.request_id: request}, drafts={draft.id: draft},
        contexts={context.content_digest: context}, attestations={}, approvals=approvals,
        catalog=catalog_runtime, bridge_view=view, phase1_registry=registry,
        runtime_registry_digest=rt_digest, profile=static_runtime_profile(), stores=stores,
        run_budget=run_budget, clock=clock)

    # -- steps 3-4: validation + runtime support (inside prepare) --------- #
    prep = service.prepare_candidate(draft.id, request_id=request.request_id)
    # -- step 5: reservation ---------------------------------------------- #
    cand, res = service.persist_and_reserve_candidate(prep, idempotency_key="reserve-1")
    # -- step 6: REQUIRED authenticated approval -------------------------- #
    approvals[(request.request_id, cand.candidate_plan_digest)] = PlanApproval(
        request_id=request.request_id, candidate_plan_digest=cand.candidate_plan_digest,
        decision=ApprovalDecision.APPROVED, actor_id="human-1", decided_at=clock.now())
    ev = service.record_approval(
        cand.candidate_plan_digest,
        ApprovalSubmission(request_id=request.request_id,
                           candidate_plan_digest=cand.candidate_plan_digest,
                           decision=ApprovalDecision.APPROVED),
        authenticated_actor="human-1", idempotency_key="approve-1")
    # -- step 7: freeze + PlanAdmitted ------------------------------------ #
    plan, admitted = service.freeze_and_admit_candidate(
        cand.candidate_plan_digest, reservation_id=res.reservation_id,
        approval_event_id=ev.event_id, idempotency_key="freeze-1")
    bundle = service.verify_for_dispatch(plan.plan_digest)

    pool = ArtifactPool(stores=stores, registry_digest=rt_digest, plan=plan, catalog=snapshot,
                        clock=clock)
    budget = BudgetLedger(
        sink=stores.budget_event_sink(run_id=draft.run_id, ledger_id=run_budget.ledger_id),
        run_budget=run_budget)
    runtime = W.ExecutionRuntime(
        catalog=catalog_runtime, bridge_view=view, factories=factories,
        support_report=bundle.support_report, runtime_registry_digest=rt_digest)
    run_ctx = RunContext(
        run_id=draft.run_id, data=context.data_context, context_snapshot_id=context.snapshot_id,
        memory_snapshot_hash=context.memory_snapshot_hash, budget=run_budget,
        cancellation_token_id="cancel-pilot")

    return PilotEnv(
        registry=registry, clock=clock, catalog_runtime=catalog_runtime, snapshot=snapshot,
        view=view, resolver=resolver, rt_digest=rt_digest, stores=stores, request=request,
        context=context, binding=binding, draft=draft, refusal_sink=refusal_sink,
        run_budget=run_budget, factories=factories, model_gateway=model_gateway,
        approvals=approvals, service=service, prep=prep, cand=cand, res=res, approval_event=ev,
        plan=plan, admitted=admitted, bundle=bundle, pool=pool, budget=budget, runtime=runtime,
        run_ctx=run_ctx, mem=mem)


def _status(rec, node_id):
    runs = [nr for nr in rec.node_runs if nr.node_id == node_id]
    assert runs, f"no NodeRun recorded for {node_id}"
    return runs[-1].status


# =========================================================================== #
# 1. canonical empty-memory context (step 2 fidelity)                          #
# =========================================================================== #
def test_preset_context_is_canonical_empty_memory_and_persists_facts():
    env = build_pilot_env()
    ctx, binding = env.context, env.binding
    # exact typed refs (not plain PayloadRefs), nested content digests as the hashes
    assert ctx.memory_snapshot_ref == binding.memory_snapshot_ref
    assert ctx.memory_selection_ref == binding.memory_selection_ref
    assert ctx.memory_snapshot_hash == binding.snapshot_hash
    assert ctx.past_context_hash == binding.past_context_hash
    # canonical empty pair → no requirements ref; no caller session scope
    assert ctx.runtime_requirements_ref is None
    assert ctx.memory_session_id is None
    # both facts were persisted and resolve back to the canonical models
    snap = env.stores.payloads.get(
        env.mem.snapshot_store_ref, expected_schema_ref=P.EMPTY_MEMORY_SNAPSHOT_SR)
    sel = env.stores.payloads.get(
        env.mem.selection_store_ref, expected_schema_ref=P.EMPTY_MEMORY_SELECTION_SR)
    assert snap == binding.snapshot
    assert sel == binding.selection
    # the context binds the exact canonical typed refs (not plain PayloadRefs).
    assert ctx.memory_snapshot_ref == P.EMPTY_MEMORY_SNAPSHOT_REF
    assert ctx.memory_selection_ref == P.EMPTY_MEMORY_SELECTION_REF


# =========================================================================== #
# 2. full happy path — all nine steps, typed provenance                        #
# =========================================================================== #
def test_pilot_full_path_completes_with_final_portfolio_decision():
    env = build_pilot_env()
    # steps 3-7 already exercised in build_pilot_env; assert the persisted witnesses.
    assert env.draft.source is PlanSource.PRESET
    assert env.prep.phase1_report.valid
    assert env.prep.support_report.supported
    assert env.bundle.plan.plan_digest == env.plan.plan_digest
    assert env.bundle.approval.decision is ApprovalDecision.APPROVED

    # step 8: bounded execution
    result, rec = env.run()
    assert isinstance(result, RunResult)
    assert result.terminal_status == "completed"
    for nid in ("sentiment", "research", "pm"):
        assert _status(rec, nid) is NodeStatus.COMPLETED
    # three LLM invocations settled (one per node)
    assert result.settled_llm_invocations == 3

    # typed InputSnapshots: research bound the sentiment artifact; pm bound both.
    r_snap = rec.input_snapshots["research"]
    r_one = [x for x in r_snap.artifact_inputs if x.input_name == "sentiment"][0]
    assert [r.producer_node_id for r in r_one.artifact_refs] == ["sentiment"]
    pm_snap = rec.input_snapshots["pm"]
    pm_names = {x.input_name for x in pm_snap.artifact_inputs}
    assert {"research_plan", "sentiment"} <= pm_names

    # LayerCommits: three layers (sentiment → research → pm), real NodeRun ids.
    assert len(rec.layer_commits) == 3
    pm_run = [nr for nr in rec.node_runs if nr.node_id == "pm"][-1]
    assert pm_run.node_run_id in rec.layer_commits[-1].node_run_ids

    # step 9: final PortfolioDecision validates against its registered schema.
    art = env.pool.committed_output("pm", "primary")
    assert art is not None
    assert art.payload_schema_ref == _SR_DECISION
    validated = env.registry.validate_payload(art.payload_schema_ref, art.payload)
    assert isinstance(validated, PortfolioDecision)
    assert validated.rating is PortfolioRating.HOLD
    # provenance: the sealed decision artifact carries a prompt evidence ref.
    assert art.provenance.execution_evidence_refs != () or art.provenance.data_result_refs == ()


def test_pilot_does_not_exercise_the_ashare_capability():
    # dec.pm carries cap.ashare_constraint_check but tool_calls=optional and no
    # bridge activates → no ToolCallRecord, no capability schema resolution.
    env = build_pilot_env()
    result, rec = env.run()
    assert result.terminal_status == "completed"
    pm_run = [nr for nr in rec.node_runs if nr.node_id == "pm"][-1]
    assert pm_run.tool_call_records == ()


# =========================================================================== #
# 3. replay: a second run is idempotent and equal                              #
# =========================================================================== #
def test_pilot_replay_is_idempotent_and_equal():
    env = build_pilot_env()
    r1, _ = env.run()
    r2, _ = env.run()
    assert r1.terminal_status == "completed"
    assert r2 == r1  # full RunResult equality incl. settled totals


# =========================================================================== #
# 4. fail on prompt/skill/capability byte drift                                #
# =========================================================================== #
def test_pilot_fails_on_prompt_byte_drift():
    catalog_runtime = load_pilot_catalog()
    snapshot = catalog_runtime.snapshot
    # rebuild a source with one prompt's bytes tampered → digest recompute fails.
    text = {}
    for entry in snapshot.content_manifest:
        mat = catalog_runtime.text(entry.ref)
        raw = mat.raw_utf8
        if entry.ref.id == "prompt.sentiment":
            raw = raw + b"\ntampered"
        text[(entry.ref.id, entry.ref.version)] = raw
    for entry in snapshot.skill_manifest:
        text[(entry.ref.id, entry.ref.version)] = catalog_runtime.text(entry.ref).raw_utf8
    drifted = InMemoryMaterialSource(text=text, capabilities={})
    with pytest.raises(CatalogMaterialError):
        CatalogRuntime.build(snapshot, drifted)


def test_pilot_fails_on_skill_byte_drift():
    catalog_runtime = load_pilot_catalog()
    snapshot = catalog_runtime.snapshot
    text = {}
    for entry in snapshot.content_manifest:
        text[(entry.ref.id, entry.ref.version)] = catalog_runtime.text(entry.ref).raw_utf8
    for entry in snapshot.skill_manifest:
        raw = catalog_runtime.text(entry.ref).raw_utf8
        if entry.ref.id == "skill.sentiment":
            raw = raw + b"\n- extra bullet"
        text[(entry.ref.id, entry.ref.version)] = raw
    drifted = InMemoryMaterialSource(text=text, capabilities={})
    with pytest.raises(CatalogMaterialError):
        CatalogRuntime.build(snapshot, drifted)


# =========================================================================== #
# 5. reject skill/tool override attempts (off-catalog factory binding)         #
# =========================================================================== #
def test_pilot_rejects_off_catalog_capability_backend_binding():
    from guanlan_v2.orchestration.refs import CapabilityRef
    env = build_pilot_env()
    forged = CapabilityRef(id="cap.ashare_constraint_check", version="1",
                           content_digest="f" * 64)  # forged digest
    with pytest.raises(CatalogMaterialError):
        env.factories.register_capability_backend(forged, lambda **kw: None)


def test_pilot_resolve_worker_exposes_only_declared_bindings():
    # a caller cannot inject an extra skill/tool: resolve_worker returns exactly the
    # WorkerSpec's declared refs.
    env = build_pilot_env()
    resolved = env.catalog_runtime.resolve_worker("dec.pm")
    assert tuple(s.ref.id for s in resolved.skills) == ("skill.pm",)
    assert tuple(c.ref.id for c in resolved.capabilities) == ("cap.ashare_constraint_check",)


# =========================================================================== #
# 6. reject approval identity change                                           #
# =========================================================================== #
def test_pilot_rejects_wrong_actor_approval():
    env = build_pilot_env()
    # a fresh candidate under a fresh service so we control the approval step.
    with pytest.raises(AdmissionRejected):
        env.service.record_approval(
            env.cand.candidate_plan_digest,
            ApprovalSubmission(request_id=env.request.request_id,
                               candidate_plan_digest=env.cand.candidate_plan_digest,
                               decision=ApprovalDecision.APPROVED),
            authenticated_actor="impostor", idempotency_key="approve-bad")


# =========================================================================== #
# 7. reject reservation identity change (release → dispatch fails)             #
# =========================================================================== #
def test_pilot_dispatch_rejected_on_reservation_drift():
    env = build_pilot_env()
    active = env.budget.get_active_plan(env.request.request_id, env.plan.plan_digest)
    env.budget.release(active.reservation_id, reason="test-drift", idempotency_key="drift-rel")
    with pytest.raises(Exception):
        env.run()


# =========================================================================== #
# 8. reject report identity change (report bound to a different candidate)      #
# =========================================================================== #
def test_pilot_rejects_report_for_a_different_candidate():
    env = build_pilot_env()
    with pytest.raises(AdmissionRejected):
        check_report_binding(
            "0" * 64, env.prep.phase1_report, env.prep.support_report)


def test_pilot_rejects_mismatched_support_report_at_runtime():
    env = build_pilot_env()
    other = build_pilot_env(suffix="-other")  # a genuinely different plan/report
    bad_runtime = W.ExecutionRuntime(
        catalog=env.catalog_runtime, bridge_view=env.view, factories=env.factories,
        support_report=other.bundle.support_report, runtime_registry_digest=env.rt_digest)
    with pytest.raises(Exception):
        env.run(runtime=bad_runtime)
