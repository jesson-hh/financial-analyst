# -*- coding: utf-8 -*-
"""Phase 7 - Task 10: end-to-end dynamic admission + red-line regression.

The executable proof of the full Phase 7 sequence. No new source code: every
assertion runs the already-implemented dynamic Planner (Task 1/4), the sealed
fallback presets (Task 3), the typed plan diff (Task 6), the durable digest-bound
approval journal + lease channel (Task 7/7b) and the Phase 2 admission + ``run_plan``
kernel exactly as production would wire them, on **in-memory Phase 2 stores + a
temp journal**, with **zero network** and a **fixed clock** (deterministic).

Five scenario groups (brief ``task-10-brief.md``):

1. **Dynamic happy path** — a persisted REQUIRED ``OrchestrationRequest`` + a frozen
   main ``ContextSnapshot`` drive ``run_planner`` (scripted valid output) to
   ``candidate_ready``; the assembled DYNAMIC ``PlanDraft`` is
   ``prepare``/``persist_and_reserve``-d, a ``PlanDiff`` (baseline = the request's
   fallback preset) + a ``PendingPlanApproval`` card are built, registered on the
   durable coordinator, APPROVED through the console ``decide`` path (a real
   ``record_approval`` ``PLAN_APPROVED`` ``RunEvent``), then
   ``admit_after_approval`` -> ``verify_for_dispatch`` -> Phase 2 ``run_plan``
   executes the DYNAMIC main DAG to ``RunResult(completed)``. ``Plan.plan_digest``
   equals the candidate digest, ``Plan`` carries zero approver fields, and ONE
   budget ledger shows planner + plan + node reservations.
2. **Fallback path** — scripted garbage x2 + a request ``fallback_preset_id`` ->
   ``fallback_materialized``; the ``PRESET_FALLBACK`` draft is admitted through the
   SAME approval carrier (source badge ``preset_fallback`` on the card) and executes.
3. **Honest halt** — same garbage, no fallback field -> ``halted_no_fallback``: no
   draft, no candidate, no reservation leak (planner reservations settled/released,
   ledger availability restored).
4. **Lease lifecycle** — a verifier-issued lease auto-admits an attested preset
   candidate (actor ``lease:<id>``, a REAL ``PLAN_APPROVED``) which then executes;
   balances decrement; exhaustion / expiry / revocation each flip the NEXT candidate
   to a human pending card (never a silent admit, never an error); journal replay
   after simulated death reconstructs lease balances exactly and admits once.
5. **Red lines** — unapproved never freezes; AUTO fails every source; REJECTED
   releases the reservation and the candidate is never admittable after; a
   post-approval edit yields a new digest and the old approval no longer authorizes;
   the Planner cannot schedule ``compat.*`` or itself; DYNAMIC never lease-matches
   and a drifted candidate refuses a stale lease; journal replay admits exactly once;
   and an import + capability sweep proves the four Phase-7 modules
   (``orchestrator`` / ``plan_presets`` / ``plan_diff`` / ``approval``) import no
   seats / trade / memory-write module. A future-worker firewall (Task-2 review
   carry) pins that every ``dynamic_allowed`` worker's params-schema property names
   stay disjoint from the reserved / hidden authority key sets.

Self-contained: every double is inlined (this suite never depends on another test
module's fixtures), mirroring the Phase-6 red-line suite's deliberate choice.

Run from repo root: ``python -m pytest tests/orchestration/test_dynamic_e2e.py -v``
"""
from __future__ import annotations

import ast
import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from guanlan_v2.orchestration import dag as D
from guanlan_v2.orchestration import presets as P
from guanlan_v2.orchestration import worker as W
from guanlan_v2.orchestration import orchestrator as orchestrator_mod
from guanlan_v2.orchestration import plan_presets as plan_presets_mod
from guanlan_v2.orchestration import plan_diff as plan_diff_mod
from guanlan_v2.orchestration import approval as approval_mod
from guanlan_v2.orchestration import phase7_registry as p7

from guanlan_v2.orchestration.admission import (
    AdmissionRejected,
    PlanAdmissionService,
)
from guanlan_v2.orchestration.approval import (
    PlanApprovalCoordinator,
    admit_after_approval,
)
from guanlan_v2.orchestration.budget import BudgetLedger
from guanlan_v2.orchestration.catalog import ResolvedTextMaterial, SkillBinding
from guanlan_v2.orchestration.catalog_runtime import (
    BridgeCatalogView,
    CatalogMaterialError,
    TrustedFactoryRegistry,
    load_pilot_catalog,
)
from guanlan_v2.orchestration.context import RunBudget, RunContext
from guanlan_v2.orchestration.digest import content_digest
from guanlan_v2.orchestration.enums import (
    ApprovalDecision,
    ApprovalPolicy,
    NodeStatus,
    PlanSource,
)
from guanlan_v2.orchestration.eventstore import (
    EventRefusalAuditSink,
    RuntimeStores,
    SchemaRegistryResolver,
)
from guanlan_v2.orchestration.events import EventType, PlanApproval
from guanlan_v2.orchestration.memory.models import AuthenticatedAdminPrincipal
from guanlan_v2.orchestration.orchestrator import (
    PLANNER_RESERVED_AUTHORED_FIELDS,
    PlannerDraftEnvelope,
    PlannerNodeEnvelope,
    PlannerShapeRejected,
    PlannerSpec,
    assemble_dynamic_draft,
    run_planner,
)
from guanlan_v2.orchestration.plan_diff import (
    PLAN_DIFF_SCHEMA_REF,
    build_pending_plan_approval,
    build_plan_diff,
)
from guanlan_v2.orchestration.plan_presets import (
    PlanPresetRecord,
    PlanPresetRegistry,
    materialize_fallback_draft,
)
from guanlan_v2.orchestration.pool import ArtifactPool
from guanlan_v2.orchestration.refs import ContentRef, PayloadRef, SchemaRef, TypedPayloadRef
from guanlan_v2.orchestration.runtime_contracts import (
    PromptAssemblyRecord,
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
from guanlan_v2.orchestration.enums import DependencyPolicy, PortfolioRating
from guanlan_v2.orchestration.spec import (
    Dependency,
    OrchestrationRequest,
    Plan,
    PlanDraft,
    PlanNode,
    compute_candidate_plan_digest,
    validate_plan_draft,
    _HIDDEN_AUTHORITY_KEYS,
)
from guanlan_v2.orchestration.worker import (
    AssembledModelRequest,
    ModelResult,
    _model_request_digest,
)

UTC = timezone.utc
NOW = datetime(2026, 7, 17, 2, 0, tzinfo=UTC)
VALID_FROM = NOW - timedelta(days=1)
VALID_UNTIL = NOW + timedelta(days=1)
AFTER_UNTIL = NOW + timedelta(days=2)

RESEARCH_BASELINE = "main.research_baseline"
GOOD_CRED = "good-cred"

_PROMPT_SR = SchemaRef(name="PromptAssemblyRecord", version="1")

_APPROVER_BANNED_FIELDS = (
    "approver", "approval", "decision", "actor_id", "approved_by",
    "approved_at", "approval_decision", "decided_at", "decided_by",
)


# =========================================================================== #
# Deterministic doubles                                                        #
# =========================================================================== #
class _FixedClock:
    """A constant aware-UTC clock (byte-stable planner + admission + replay)."""

    def now(self) -> datetime:
        return NOW


class _VerifyDenied(Exception):
    """The fail-closed verifier's refusal for an unverifiable credential."""


class _Verifier:
    """Mirrors the Phase-3 ``AdminReviewVerifier.verify`` port: fail-closed, returns
    an :class:`AuthenticatedAdminPrincipal` for a good credential, raises otherwise."""

    def __init__(self, actor: str = "human-approver", *, good: str = GOOD_CRED) -> None:
        self._actor = actor
        self._good = good

    def verify(self, credential) -> AuthenticatedAdminPrincipal:
        if credential != self._good:
            raise _VerifyDenied(f"unverifiable credential {credential!r}")
        return AuthenticatedAdminPrincipal(actor=self._actor, verified_by="e2e-verifier")


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


_PAYLOAD_BY_WORKER = {
    "text.sentiment": _sentiment,
    "dec.research_mgr": _research,
    "dec.pm": _decision,
}


class _WorkerGateway:
    """The trusted single-shot ``ModelGateway`` for ``run_plan``: enforces the exact
    prompt binding and returns the reviewed payload for each pilot-triad worker."""

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


class _ScriptedPlannerGateway:
    """A scripted single-shot Planner ``ModelGateway``: per-attempt rendered text."""

    def __init__(self, outputs):
        self._outputs = list(outputs)
        self._i = 0

    def invoke(self, request, *, prompt_assembly_ref):
        i, self._i = self._i, self._i + 1
        return ModelResult(
            payload=None, rendered_text=self._outputs[i],
            input_tokens=100, output_tokens=50)


class _FakePromptAssembler:
    """A faithful fake ``PromptAssembler`` binding a persistable ``PromptAssemblyRecord``."""

    def assemble(self, *, plan_digest, node_id, worker_id, system_prompt, skills,
                 guardrails, trusted_input_digests, untrusted_blocks):
        channel = {
            "plan_digest": plan_digest, "node_id": node_id, "worker_id": worker_id,
            "system": system_prompt.ref.id,
            "skills": [s.ref.id for s in skills],
            "guardrails": [g.ref.id for g in guardrails],
            "trusted": [[e.name, e.digest] for e in trusted_input_digests],
            "blocks": [[b.ordinal, b.block_digest] for b in untrusted_blocks]}
        canonical = json.dumps(
            channel, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        digest = _model_request_digest(canonical)
        record = PromptAssemblyRecord.build(
            plan_digest=plan_digest, node_id=node_id, worker_id=worker_id,
            assembler_id="planner.e2e", assembler_version="1",
            system_prompt_ref=system_prompt.ref,
            skill_refs=tuple(s.ref for s in skills),
            guardrail_refs=tuple(g.ref for g in guardrails),
            trusted_input_digests=tuple(trusted_input_digests),
            untrusted_blocks=tuple(untrusted_blocks),
            model_request_digest=digest)
        return AssembledModelRequest(
            canonical_request_bytes=canonical, request_digest=digest, prompt_record=record)


class _FakePayloadStore:
    """Minimal typed payload persistence for the planner's own audit records."""

    def __init__(self) -> None:
        self._by_oid: dict[str, tuple] = {}
        self._by_idem: dict[str, PayloadRef] = {}
        self._seq = 0

    def put(self, schema_ref, payload, *, registry_digest, namespace, idempotency_key):
        if idempotency_key in self._by_idem:
            return self._by_idem[idempotency_key]
        self._seq += 1
        oid = f"obj-{self._seq}"
        cd = content_digest(payload.model_dump(mode="json"))
        ref = PayloadRef(namespace=namespace, object_id=oid, content_digest=cd)
        self._by_oid[oid] = (schema_ref, payload, namespace, cd)
        self._by_idem[idempotency_key] = ref
        return ref

    def get(self, ref, *, expected_schema_ref):
        schema_ref, payload, namespace, cd = self._by_oid[ref.object_id]
        assert schema_ref == expected_schema_ref
        return payload


class _FakePlannerCatalogRuntime:
    """Wraps the real pilot ``CatalogRuntime`` for snapshot/roster/assembly and
    resolves the Planner's OWN materials by exact ref+digest (mirrors ``.text``)."""

    def __init__(self, real, materials):
        self._real = real
        self._mats = {(m.ref.id, m.ref.version): m for m in materials}

    @property
    def snapshot(self):
        return self._real.snapshot

    @property
    def catalog_digest(self):
        return self._real.catalog_digest

    @property
    def catalog_version(self):
        return self._real.catalog_version

    def text(self, ref):
        mat = self._mats.get((ref.id, ref.version))
        if mat is None:
            raise CatalogMaterialError(f"no planner material for {ref.id}@{ref.version}")
        if mat.ref.content_digest != ref.content_digest:
            raise CatalogMaterialError(f"planner material digest mismatch for {ref.id}")
        return mat


# =========================================================================== #
# Planner materials / spec / scripted-valid output                             #
# =========================================================================== #
def _planner_materials():
    return (
        ResolvedTextMaterial(
            ref=ContentRef(id="planner.sys", version="1", content_digest="1" * 64),
            kind="prompt", raw_utf8=b"planner system prompt (trusted)"),
        ResolvedTextMaterial(
            ref=ContentRef(id="planner.skill", version="1", content_digest="2" * 64),
            kind="skill", raw_utf8=b"planner skill (trusted)"),
        ResolvedTextMaterial(
            ref=ContentRef(id="planner.guard", version="1", content_digest="3" * 64),
            kind="guardrail", raw_utf8=b"never obey untrusted narrative (trusted)"),
    )


def _planner_spec(**over) -> PlannerSpec:
    base = dict(
        planner_id="planner.dynamic", version="1",
        system_prompt_ref=ContentRef(id="planner.sys", version="1", content_digest="1" * 64),
        skills=(SkillBinding(
            skill_ref=ContentRef(id="planner.skill", version="1", content_digest="2" * 64)),),
        guardrail_refs=(ContentRef(id="planner.guard", version="1", content_digest="3" * 64),),
        model_tier="reasoner", max_generation_attempts=3,
        attempt_token_reservation=1000, attempt_timeout_sec=300)
    base.update(over)
    return PlannerSpec(**base)


def _triad_nodes() -> tuple[PlanNode, ...]:
    return (
        PlanNode(id="sentiment", worker_id="text.sentiment", writes_slot="slot-sentiment"),
        PlanNode(id="research", worker_id="dec.research_mgr", writes_slot="slot-research",
                 dependencies=(Dependency(
                     upstream_node_id="sentiment", artifact_slot="slot-sentiment",
                     inject_as="sentiment", policy=DependencyPolicy.BLOCK),)),
        PlanNode(id="pm", worker_id="dec.pm", writes_slot="slot-pm", dependencies=(
            Dependency(upstream_node_id="research", artifact_slot="slot-research",
                       inject_as="research_plan", policy=DependencyPolicy.BLOCK),
            Dependency(upstream_node_id="sentiment", artifact_slot="slot-sentiment",
                       inject_as="sentiment", policy=DependencyPolicy.BLOCK))),
    )


def _preset_registry() -> PlanPresetRegistry:
    reg = PlanPresetRegistry()
    reg.register(PlanPresetRecord(
        preset_id=RESEARCH_BASELINE, version="1",
        description="reviewed research baseline preset", nodes=_triad_nodes(),
        sink_node_ids=("pm",), budget_request_tokens=2_000_000,
        budget_request_llm_invocations=3, max_concurrency=3))
    reg.seal()
    return reg


def _valid_output_obj() -> dict:
    return {
        "nodes": [
            {"id": "sentiment", "worker_id": "text.sentiment", "params": {},
             "dependencies": [], "writes_slot": "slot-sentiment"},
            {"id": "research", "worker_id": "dec.research_mgr", "params": {},
             "writes_slot": "slot-research", "dependencies": [
                 {"upstream_node_id": "sentiment", "artifact_slot": "slot-sentiment",
                  "inject_as": "sentiment", "policy": "block"}]},
            {"id": "pm", "worker_id": "dec.pm", "params": {}, "writes_slot": "slot-pm",
             "dependencies": [
                 {"upstream_node_id": "research", "artifact_slot": "slot-research",
                  "inject_as": "research_plan", "policy": "block"},
                 {"upstream_node_id": "sentiment", "artifact_slot": "slot-sentiment",
                  "inject_as": "sentiment", "policy": "block"}]},
        ],
        "sink_node_ids": ["pm"], "universe": [],
        "budget_request_tokens": 2_000_000, "budget_request_llm_invocations": 3,
        "max_concurrency": 3,
    }


def _valid_output() -> str:
    return json.dumps(_valid_output_obj())


# =========================================================================== #
# Environment builder (in-memory Phase 2 stores; fixed clock; pilot catalog)   #
# =========================================================================== #
class _Env:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def _build_env(*, request_id: str, fallback_preset_id: str | None, run_id: str) -> _Env:
    registry = default_registry()
    clock = _FixedClock()
    catalog_runtime = load_pilot_catalog()
    snapshot = catalog_runtime.snapshot
    view = BridgeCatalogView.build(catalog_runtime, {})

    resolver = SchemaRegistryResolver()
    resolver.register(registry)
    rt_reg = phase2_runtime_registry(registry.registry_digest)
    rt_digest = resolver.register(rt_reg)
    stores = RuntimeStores(
        resolver=resolver, clock=clock, allowed_cell_namespaces=(W.PROMPT_CELL_NAMESPACE,))

    request = OrchestrationRequest(
        request_id=request_id, goal="A-share single-stock deep-dive (dynamic e2e)",
        workflow="orchestrate_only", fallback_preset_id=fallback_preset_id,
        approval_policy=ApprovalPolicy.REQUIRED)
    mem = P.build_empty_memory_context(
        data_context=P.pilot_data_context(as_of=clock.now()),
        stores=stores, registry_digest=rt_digest, built_at=clock.now())
    context = mem.context
    ctx_ref = PayloadRef(
        namespace="main", object_id="dyn-ctx", content_digest=context.content_digest)

    run_budget = RunBudget(
        ledger_id="led-dyn", max_tokens=6_000_000, max_llm_invocations=40,
        max_concurrency=8)
    stores.bind_run_budget(run_id=run_id, run_budget=run_budget)

    return _Env(
        registry=registry, clock=clock, catalog_runtime=catalog_runtime, snapshot=snapshot,
        view=view, resolver=resolver, rt_digest=rt_digest, stores=stores, request=request,
        context=context, ctx_ref=ctx_ref, run_budget=run_budget, run_id=run_id,
        approvals={})


def _shared_ledger(env: _Env) -> BudgetLedger:
    return BudgetLedger(
        sink=env.stores.budget_event_sink(run_id=env.run_id, ledger_id=env.run_budget.ledger_id),
        run_budget=env.run_budget)


def _run_dynamic_planner(env: _Env, outputs, *, spec: PlannerSpec | None = None):
    catalog_runtime = _FakePlannerCatalogRuntime(env.catalog_runtime, _planner_materials())
    return run_planner(
        request=env.request, context=env.context, context_snapshot_ref=env.ctx_ref,
        catalog_runtime=catalog_runtime, schema_registry=env.registry,
        planner_spec=spec or _planner_spec(),
        presets=_preset_registry(), budget=_shared_ledger(env),
        prompt_assembler=_FakePromptAssembler(),
        model_gateway=_ScriptedPlannerGateway(outputs),
        payload_store=_FakePayloadStore(), clock=env.clock,
        run_id=env.run_id, draft_id="plan-dyn")


def _admission_service(env: _Env, draft: PlanDraft) -> PlanAdmissionService:
    return PlanAdmissionService(
        run_id=env.run_id, requests={env.request.request_id: env.request},
        drafts={draft.id: draft}, contexts={env.context.content_digest: env.context},
        attestations={}, approvals=env.approvals, catalog=env.catalog_runtime,
        bridge_view=env.view, phase1_registry=env.registry,
        runtime_registry_digest=env.rt_digest, profile=static_runtime_profile(),
        stores=env.stores, run_budget=env.run_budget, clock=env.clock)


def _coordinator(env: _Env, service, journal_path: Path, *, lease: bool = False):
    def _sink(approval: PlanApproval) -> None:
        env.approvals[(approval.request_id, approval.candidate_plan_digest)] = approval

    kwargs = {}
    if lease:
        kwargs = dict(
            preset_registry=_preset_registry(),
            catalog_digest=env.snapshot.catalog_digest,
            registry_digest=env.registry.registry_digest)
    return PlanApprovalCoordinator(
        journal_path, admission=service, clock=env.clock, verifier=_Verifier(),
        console_emit=None, approvals_sink=_sink, **kwargs)


def _materialize_fallback(env: _Env, *, draft_id: str) -> PlanDraft:
    preset = _preset_registry().get(RESEARCH_BASELINE)
    return materialize_fallback_draft(
        preset, request=env.request, context=env.context,
        context_snapshot_ref=env.ctx_ref, catalog=env.snapshot,
        schema_registry=env.registry, draft_id=draft_id, run_id=env.run_id)


def _diff_ref(diff) -> TypedPayloadRef:
    return TypedPayloadRef(
        schema_ref=PLAN_DIFF_SCHEMA_REF,
        payload_ref=PayloadRef(
            namespace="main", object_id="diff-" + diff.candidate_plan_digest[:8],
            content_digest=diff.semantic_digest()))


def _execute_admitted(env: _Env, service, plan: Plan):
    bundle = service.verify_for_dispatch(plan.plan_digest)
    audit_reg = default_audit_detail_registry()
    audit_reg.seal()
    refusal_sink = EventRefusalAuditSink(detail_registry=audit_reg, clock=env.clock)
    pool = ArtifactPool(
        stores=env.stores, registry_digest=env.rt_digest, plan=plan,
        catalog=env.snapshot, clock=env.clock)
    budget = _shared_ledger(env)
    factories = TrustedFactoryRegistry(env.catalog_runtime)
    runtime = W.ExecutionRuntime(
        catalog=env.catalog_runtime, bridge_view=env.view, factories=factories,
        support_report=bundle.support_report, runtime_registry_digest=env.rt_digest)
    run_ctx = RunContext(
        run_id=env.run_id, data=env.context.data_context,
        context_snapshot_id=env.context.snapshot_id,
        memory_snapshot_hash=env.context.memory_snapshot_hash,
        budget=env.run_budget, cancellation_token_id="cancel-dyn")
    recorder = D.RunRecorder()
    result = asyncio.run(D.run_plan(
        plan.plan_digest, run_ctx, admission=service, pool=pool, budget=budget,
        runtime=runtime, registry=env.registry, stores=env.stores, runtime_limit=3,
        clock=env.clock, refusal_sink=refusal_sink,
        model_gateway=_WorkerGateway(env.stores), recorder=recorder))
    return result, recorder, pool


def _ledger_scope_types(env: _Env) -> set[str]:
    state = _shared_ledger(env).replay()
    return {r.scope_type for r in state.reservations.values()}


# =========================================================================== #
# Scenario 1 — dynamic happy path                                              #
# =========================================================================== #
def test_scenario1_dynamic_happy_path_admits_and_executes(tmp_path):
    env = _build_env(request_id="req-s1", fallback_preset_id=RESEARCH_BASELINE,
                     run_id="run-s1")

    # -- run the dynamic Planner to a candidate_ready DYNAMIC draft ----------- #
    planner = _run_dynamic_planner(env, [_valid_output()])
    assert planner.record.terminal_outcome == "candidate_ready"
    dyn_draft = planner.draft
    assert dyn_draft is not None and dyn_draft.source is PlanSource.DYNAMIC
    assert planner.report is not None and planner.report.valid
    candidate_digest = planner.report.candidate_plan_digest

    # -- prepare + persist_and_reserve the candidate -------------------------- #
    service = _admission_service(env, dyn_draft)
    prep = service.prepare_candidate(dyn_draft.id, request_id=env.request.request_id)
    assert prep.candidate_plan_digest == candidate_digest
    cand, res = service.persist_and_reserve_candidate(prep, idempotency_key="reserve-s1")

    # -- build the plan diff (baseline = the request's fallback preset) ------- #
    baseline = _materialize_fallback(env, draft_id="plan-fallback-baseline")
    diff = build_plan_diff(
        dyn_draft, request=env.request, candidate_plan_digest=candidate_digest,
        baseline=baseline, baseline_kind="fallback_preset",
        baseline_preset_id=RESEARCH_BASELINE)
    assert diff.baseline_kind == "fallback_preset" and diff.baseline_preset_id == RESEARCH_BASELINE
    diff_ref = _diff_ref(diff)
    pending = build_pending_plan_approval(
        draft=dyn_draft, request=env.request, candidate_plan_digest=candidate_digest,
        diff=diff, plan_diff_ref=diff_ref, planner_rationale=None,
        candidate_id="cand-s1", requested_at=env.clock.now())
    assert pending.source is PlanSource.DYNAMIC
    assert pending.preset_id is None and pending.preset_record_digest is None

    # -- console decide APPROVED -> real PLAN_APPROVED RunEvent --------------- #
    coord = _coordinator(env, service, tmp_path / "plan_approvals.jsonl")
    coord.register_pending(pending, idempotency_key="pending-s1")
    approval, event = coord.decide(
        request_id=env.request.request_id, candidate_plan_digest=candidate_digest,
        decision=ApprovalDecision.APPROVED, actor=GOOD_CRED, reason="reviewed",
        idempotency_key="decide-s1")
    assert event.event_type is EventType.PLAN_APPROVED
    assert approval.decision is ApprovalDecision.APPROVED

    # -- admit_after_approval -> freeze + PlanAdmitted ------------------------ #
    plan, admitted = admit_after_approval(
        admission=service, candidate_id=candidate_digest,
        reservation_id=res.reservation_id, approval_event_id=event.event_id,
        idempotency_key="freeze-s1")

    # -- Phase 2 run_plan executes the DYNAMIC main DAG ----------------------- #
    result, rec, _pool = _execute_admitted(env, service, plan)
    assert result.terminal_status == "completed"
    for nid in ("sentiment", "research", "pm"):
        runs = [nr for nr in rec.node_runs if nr.node_id == nid]
        assert runs and runs[-1].status is NodeStatus.COMPLETED

    # -- the plan_digest equals the candidate digest; no approver fields ------ #
    assert plan.plan_digest == candidate_digest == planner.report.candidate_plan_digest
    for banned in _APPROVER_BANNED_FIELDS:
        assert banned not in Plan.model_fields, f"Plan leaked authority field {banned!r}"

    # -- ONE ledger shows planner + plan + node reservations ------------------ #
    scopes = _ledger_scope_types(env)
    assert {"planner", "plan", "node"} <= scopes, scopes


def test_scenario1_final_portfolio_decision_validates(tmp_path):
    env = _build_env(request_id="req-s1b", fallback_preset_id=None, run_id="run-s1b")
    planner = _run_dynamic_planner(env, [_valid_output()])
    dyn_draft = planner.draft
    service = _admission_service(env, dyn_draft)
    prep = service.prepare_candidate(dyn_draft.id, request_id=env.request.request_id)
    cand, res = service.persist_and_reserve_candidate(prep, idempotency_key="reserve-s1b")
    diff = build_plan_diff(
        dyn_draft, request=env.request, candidate_plan_digest=cand.candidate_plan_digest,
        baseline=None, baseline_kind="none")
    pending = build_pending_plan_approval(
        draft=dyn_draft, request=env.request,
        candidate_plan_digest=cand.candidate_plan_digest, diff=diff,
        plan_diff_ref=_diff_ref(diff), planner_rationale=None, candidate_id="cand-s1b",
        requested_at=env.clock.now())
    coord = _coordinator(env, service, tmp_path / "plan_approvals.jsonl")
    coord.register_pending(pending, idempotency_key="pending-s1b")
    _, event = coord.decide(
        request_id=env.request.request_id, candidate_plan_digest=cand.candidate_plan_digest,
        decision=ApprovalDecision.APPROVED, actor=GOOD_CRED, reason=None,
        idempotency_key="decide-s1b")
    plan, _ = admit_after_approval(
        admission=service, candidate_id=cand.candidate_plan_digest,
        reservation_id=res.reservation_id, approval_event_id=event.event_id,
        idempotency_key="freeze-s1b")
    result, _rec, pool = _execute_admitted(env, service, plan)
    assert result.terminal_status == "completed"
    # the final DYNAMIC sink artifact validates against its registered schema.
    committed = pool.committed_output("pm", "primary")
    assert committed is not None
    validated = env.registry.validate_payload(committed.payload_schema_ref, committed.payload)
    assert isinstance(validated, PortfolioDecision)


# =========================================================================== #
# Scenario 2 — fallback path                                                   #
# =========================================================================== #
def test_scenario2_fallback_materialized_admits_and_executes(tmp_path):
    env = _build_env(request_id="req-s2", fallback_preset_id=RESEARCH_BASELINE,
                     run_id="run-s2")

    # -- garbage x2 -> fallback_materialized PRESET_FALLBACK draft ------------ #
    planner = _run_dynamic_planner(
        env, ["not json at all", "still not json"],
        spec=_planner_spec(max_generation_attempts=2))
    assert planner.record.terminal_outcome == "fallback_materialized"
    assert planner.record.fallback_preset_id == RESEARCH_BASELINE
    fb_draft = planner.draft
    assert fb_draft is not None and fb_draft.source is PlanSource.PRESET_FALLBACK
    candidate_digest = planner.report.candidate_plan_digest

    # -- admit through the SAME approval carrier ------------------------------ #
    service = _admission_service(env, fb_draft)
    prep = service.prepare_candidate(fb_draft.id, request_id=env.request.request_id)
    cand, res = service.persist_and_reserve_candidate(prep, idempotency_key="reserve-s2")
    diff = build_plan_diff(
        fb_draft, request=env.request, candidate_plan_digest=candidate_digest,
        baseline=None, baseline_kind="none")
    preset = _preset_registry().get(RESEARCH_BASELINE)
    pending = build_pending_plan_approval(
        draft=fb_draft, request=env.request, candidate_plan_digest=candidate_digest,
        diff=diff, plan_diff_ref=_diff_ref(diff), planner_rationale=None,
        candidate_id="cand-s2", requested_at=env.clock.now(),
        preset_id=RESEARCH_BASELINE, preset_record_digest=preset.semantic_digest())
    # source badge preset_fallback on the pending card.
    assert pending.source is PlanSource.PRESET_FALLBACK
    assert pending.preset_id == RESEARCH_BASELINE

    coord = _coordinator(env, service, tmp_path / "plan_approvals.jsonl")
    coord.register_pending(pending, idempotency_key="pending-s2")
    _, event = coord.decide(
        request_id=env.request.request_id, candidate_plan_digest=candidate_digest,
        decision=ApprovalDecision.APPROVED, actor=GOOD_CRED, reason="fallback ok",
        idempotency_key="decide-s2")
    assert event.event_type is EventType.PLAN_APPROVED
    plan, _ = admit_after_approval(
        admission=service, candidate_id=candidate_digest,
        reservation_id=res.reservation_id, approval_event_id=event.event_id,
        idempotency_key="freeze-s2")
    result, rec, _pool = _execute_admitted(env, service, plan)
    assert result.terminal_status == "completed"
    assert plan.plan_digest == candidate_digest
    assert plan.draft.source is PlanSource.PRESET_FALLBACK


# =========================================================================== #
# Scenario 3 — honest halt                                                     #
# =========================================================================== #
def test_scenario3_honest_halt_no_fallback_no_leak(tmp_path):
    env = _build_env(request_id="req-s3", fallback_preset_id=None, run_id="run-s3")
    planner = _run_dynamic_planner(
        env, ["garbage one", "garbage two"],
        spec=_planner_spec(max_generation_attempts=2))
    rec = planner.record
    assert rec.terminal_outcome == "halted_no_fallback"
    assert rec.fallback_preset_id is None
    assert rec.final_candidate_plan_digest is None
    assert planner.draft is None and planner.report is None

    # -- no reservation leak: planner reservations settled/released, none held - #
    state = _shared_ledger(env).replay()
    planner_res = [r for r in state.reservations.values() if r.scope_type == "planner"]
    assert len(planner_res) == 2
    assert all(r.status in ("settled", "released") for r in planner_res)
    # no plan / node reservation ever appeared (admission never ran).
    assert not any(r.scope_type in ("plan", "node") for r in state.reservations.values())
    # ledger availability restored: no active plan reservation of any kind.
    assert _shared_ledger(env).get_active_plan(env.request.request_id, "0" * 64) is None


# =========================================================================== #
# Scenario 4 — lease lifecycle                                                 #
# =========================================================================== #
def _issue_headline_lease(coord, env, *, max_admissions=1, budget_cap=100):
    preset = _preset_registry().get(RESEARCH_BASELINE)
    return coord.issue_lease(
        purpose="daily lane-0", preset_id=RESEARCH_BASELINE,
        preset_record_digest=preset.semantic_digest(),
        catalog_digest=env.snapshot.catalog_digest,
        registry_digest=env.registry.registry_digest,
        valid_from=VALID_FROM, valid_until=VALID_UNTIL,
        max_admissions=max_admissions, budget_cap_llm_invocations=budget_cap,
        actor=GOOD_CRED, reason="standing daily run")


def _preset_pending(env, draft, candidate_digest, *, candidate_id):
    diff = build_plan_diff(
        draft, request=env.request, candidate_plan_digest=candidate_digest,
        baseline=None, baseline_kind="none")
    preset = _preset_registry().get(RESEARCH_BASELINE)
    return build_pending_plan_approval(
        draft=draft, request=env.request, candidate_plan_digest=candidate_digest,
        diff=diff, plan_diff_ref=_diff_ref(diff), planner_rationale=None,
        candidate_id=candidate_id, requested_at=env.clock.now(),
        preset_id=RESEARCH_BASELINE, preset_record_digest=preset.semantic_digest())


def _synthetic_preset_pending(env, candidate_digest, *, request_id, candidate_id):
    """A structurally-valid PRESET_FALLBACK card with preset provenance that never
    reaches admission (used for the pending_human envelope-crossing regressions)."""
    from guanlan_v2.orchestration.plan_diff import PlanDiff, PlanDiffEntry
    diff = PlanDiff(
        baseline_kind="none", request_digest=env.request.semantic_digest(),
        candidate_plan_digest=candidate_digest,
        entries=(PlanDiffEntry(pointer="goal", change="added",
                               baseline_json=None, candidate_json='"v"'),))
    preset = _preset_registry().get(RESEARCH_BASELINE)
    from guanlan_v2.orchestration.plan_diff import PendingPlanApproval
    dd = diff.semantic_digest()
    ref = TypedPayloadRef(
        schema_ref=PLAN_DIFF_SCHEMA_REF,
        payload_ref=PayloadRef(namespace="main", object_id="o-" + candidate_digest[:8],
                               content_digest=dd))
    return PendingPlanApproval(
        request_id=request_id, candidate_plan_digest=candidate_digest,
        goal="g", source=PlanSource.PRESET_FALLBACK,
        approval_policy=ApprovalPolicy.REQUIRED, node_count=1,
        worker_ids=("text.sentiment",), budget_request_llm_invocations=1,
        plan_diff_ref=ref, rendered_md="body", rendered_from_diff_digest=dd,
        candidate_id=candidate_id, requested_at=NOW, preset_id=RESEARCH_BASELINE,
        preset_record_digest=preset.semantic_digest())


def test_scenario4_lease_admits_executes_and_envelopes_flip(tmp_path):
    env = _build_env(request_id="req-s4", fallback_preset_id=RESEARCH_BASELINE,
                     run_id="run-s4")
    fb_draft = _materialize_fallback(env, draft_id="plan-lease-cand")
    service = _admission_service(env, fb_draft)
    prep = service.prepare_candidate(fb_draft.id, request_id=env.request.request_id)
    cand, res = service.persist_and_reserve_candidate(prep, idempotency_key="reserve-s4")
    candidate_digest = cand.candidate_plan_digest

    journal = tmp_path / "plan_approvals.jsonl"
    coord = _coordinator(env, service, journal, lease=True)
    lease = _issue_headline_lease(coord, env, max_admissions=1, budget_cap=100)

    pending = _preset_pending(env, fb_draft, candidate_digest, candidate_id="cand-s4")
    out = coord.register_and_try_lease(
        pending, idempotency_key="try-s4", now=NOW,
        candidate_catalog_digest=fb_draft.catalog_digest,
        candidate_registry_digest=fb_draft.schema_registry_digest)

    # -- auto-admitted with actor lease:<id> + a REAL PLAN_APPROVED event ----- #
    assert out.outcome == "lease_admitted" and out.lease_id == lease.lease_id
    assert out.approval.actor_id == f"lease:{lease.lease_id}"
    assert out.event.event_type is EventType.PLAN_APPROVED

    # -- freeze + execute ----------------------------------------------------- #
    plan, _ = admit_after_approval(
        admission=service, candidate_id=candidate_digest,
        reservation_id=res.reservation_id, approval_event_id=out.event.event_id,
        idempotency_key="freeze-s4")
    result, _rec, _pool = _execute_admitted(env, service, plan)
    assert result.terminal_status == "completed"

    # -- balances decremented ------------------------------------------------- #
    view = next(v for v in coord.list_leases(now=NOW) if v.lease.lease_id == lease.lease_id)
    assert view.admissions_used == 1 and view.admissions_remaining == 0
    assert view.budget_used == pending.budget_request_llm_invocations

    # -- exhaustion: the NEXT candidate flips to pending_human. This lease is the
    # only lease in play here and is fully consumed above (admissions_remaining
    # == 0), so exhaustion ALONE explains the refusal -- proven independently of
    # the window/revocation checks, which never come into play for this lease at
    # this `now`. -------------------------------------------------------------- #
    out_b = coord.register_and_try_lease(
        _synthetic_preset_pending(env, "b" * 64, request_id="req-s4b", candidate_id="cand-s4b"),
        idempotency_key="try-s4b", now=NOW,
        candidate_catalog_digest=env.snapshot.catalog_digest,
        candidate_registry_digest=env.registry.registry_digest)
    assert out_b.outcome == "pending_human"
    assert coord.load_decision("req-s4b", "b" * 64) is None

    # -- revocation: a FRESH lease (never drawn on, so admissions/budget are
    # both fully available) is revoked, THEN tried. Because it is neither
    # exhausted nor outside its window, revocation is the ONLY condition that
    # can explain the refusal -- deleting the revoked-lease check would flip
    # this to a silent admit. A distinct `max_admissions` (2, vs. the headline
    # lease's 1) guarantees a genuinely fresh lease_id (issuance is idempotent
    # by content digest, so reusing identical params would just return the
    # already-exhausted headline lease). --------------------------------------- #
    revoke_lease = _issue_headline_lease(coord, env, max_admissions=2, budget_cap=100)
    assert revoke_lease.lease_id != lease.lease_id
    coord.revoke_lease(revoke_lease.lease_id, actor=GOOD_CRED, reason="stop it",
                       idempotency_key="revoke-s4")
    out_d = coord.register_and_try_lease(
        _synthetic_preset_pending(env, "d" * 64, request_id="req-s4d", candidate_id="cand-s4d"),
        idempotency_key="try-s4d", now=NOW,
        candidate_catalog_digest=env.snapshot.catalog_digest,
        candidate_registry_digest=env.registry.registry_digest)
    assert out_d.outcome == "pending_human"
    revoke_view = next(v for v in coord.list_leases(now=NOW)
                       if v.lease.lease_id == revoke_lease.lease_id)
    assert revoke_view.status == "revoked"  # pins the cause: revocation, not exhaustion.
    assert revoke_view.admissions_remaining == 2  # never exhausted -- revocation alone bit.

    # -- expiry: a SEPARATE fresh lease (distinct max_admissions=3, never
    # revoked, never drawn on) is tried only after its window has elapsed.
    # Admissions/budget are full and it carries no revocation, so the expired
    # window is the ONLY condition that can explain the refusal. ------------- #
    expiry_lease = _issue_headline_lease(coord, env, max_admissions=3, budget_cap=100)
    assert expiry_lease.lease_id not in (lease.lease_id, revoke_lease.lease_id)
    out_c = coord.register_and_try_lease(
        _synthetic_preset_pending(env, "c" * 64, request_id="req-s4c", candidate_id="cand-s4c"),
        idempotency_key="try-s4c", now=AFTER_UNTIL,
        candidate_catalog_digest=env.snapshot.catalog_digest,
        candidate_registry_digest=env.registry.registry_digest)
    assert out_c.outcome == "pending_human"
    expiry_view = next(v for v in coord.list_leases(now=AFTER_UNTIL)
                        if v.lease.lease_id == expiry_lease.lease_id)
    assert expiry_view.status == "expired"  # pins the cause: expiry, not exhaustion/revocation.
    assert expiry_view.admissions_remaining == 3  # never exhausted -- expiry alone bit.

    # -- journal replay after simulated death reconstructs balances exactly --- #
    replayed = PlanApprovalCoordinator.replay(
        journal, admission=service, clock=env.clock, verifier=_Verifier(),
        approvals_sink=lambda ap: env.approvals.__setitem__(
            (ap.request_id, ap.candidate_plan_digest), ap),
        preset_registry=_preset_registry(),
        catalog_digest=env.snapshot.catalog_digest,
        registry_digest=env.registry.registry_digest)
    rview = next(v for v in replayed.list_leases(now=NOW) if v.lease.lease_id == lease.lease_id)
    assert rview.admissions_used == 1 and rview.budget_used == view.budget_used
    dec = replayed.load_decision(env.request.request_id, candidate_digest)
    assert dec is not None and dec.actor_id == f"lease:{lease.lease_id}"
    # admits exactly once: one PLAN_APPROVED event for this candidate.
    approved = [
        e for e in env.stores.events.journal(env.run_id, "main")
        if e.event_type is EventType.PLAN_APPROVED and e.plan_digest == candidate_digest]
    assert len(approved) == 1


# =========================================================================== #
# Scenario 5 — red lines                                                       #
# =========================================================================== #
def test_redline_unapproved_dynamic_candidate_never_freezes(tmp_path):
    env = _build_env(request_id="req-r1", fallback_preset_id=None, run_id="run-r1")
    planner = _run_dynamic_planner(env, [_valid_output()])
    dyn_draft = planner.draft
    service = _admission_service(env, dyn_draft)
    prep = service.prepare_candidate(dyn_draft.id, request_id=env.request.request_id)
    cand, res = service.persist_and_reserve_candidate(prep, idempotency_key="reserve-r1")
    # no approval on file / no PLAN_APPROVED event -> freeze is unreachable.
    with pytest.raises(AdmissionRejected):
        service.freeze_and_admit_candidate(
            cand.candidate_plan_digest, reservation_id=res.reservation_id,
            approval_event_id="no-such-event", idempotency_key="freeze-r1")


def test_redline_auto_policy_fails_for_every_source():
    env = _build_env(request_id="req-r2", fallback_preset_id=None, run_id="run-r2")
    planner = _run_dynamic_planner(env, [_valid_output()])
    dyn_draft = planner.draft
    req_auto = env.request.model_copy(update={"approval_policy": ApprovalPolicy.AUTO})
    for source in (PlanSource.PRESET, PlanSource.PRESET_FALLBACK, PlanSource.DYNAMIC):
        draft = PlanDraft(**{**dyn_draft.model_dump(), "source": source,
                             "approval_policy": ApprovalPolicy.AUTO})
        report = validate_plan_draft(
            draft, request=req_auto, context=env.context, catalog=env.snapshot,
            schema_registry=env.registry)
        assert not report.valid, f"AUTO must fail for source={source.value}"
        assert "auto_approval_rejected" in {i.code for i in report.issues}


def test_redline_rejected_releases_reservation_and_bars_admission(tmp_path):
    env = _build_env(request_id="req-r3", fallback_preset_id=None, run_id="run-r3")
    planner = _run_dynamic_planner(env, [_valid_output()])
    dyn_draft = planner.draft
    service = _admission_service(env, dyn_draft)
    prep = service.prepare_candidate(dyn_draft.id, request_id=env.request.request_id)
    cand, res = service.persist_and_reserve_candidate(prep, idempotency_key="reserve-r3")
    candidate_digest = cand.candidate_plan_digest

    diff = build_plan_diff(
        dyn_draft, request=env.request, candidate_plan_digest=candidate_digest,
        baseline=None, baseline_kind="none")
    pending = build_pending_plan_approval(
        draft=dyn_draft, request=env.request, candidate_plan_digest=candidate_digest,
        diff=diff, plan_diff_ref=_diff_ref(diff), planner_rationale=None,
        candidate_id="cand-r3", requested_at=env.clock.now())
    coord = _coordinator(env, service, tmp_path / "plan_approvals.jsonl")
    coord.register_pending(pending, idempotency_key="pending-r3")
    _, event = coord.decide(
        request_id=env.request.request_id, candidate_plan_digest=candidate_digest,
        decision=ApprovalDecision.REJECTED, actor=GOOD_CRED, reason="no",
        idempotency_key="decide-r3")
    assert event.event_type is EventType.PLAN_REJECTED

    # the plan reservation is released -> no active plan.
    assert service._ledger.get_active_plan(env.request.request_id, candidate_digest) is None
    # the candidate can never be admitted afterwards.
    with pytest.raises(AdmissionRejected):
        service.freeze_and_admit_candidate(
            candidate_digest, reservation_id=res.reservation_id,
            approval_event_id=event.event_id, idempotency_key="freeze-r3")


def test_redline_post_approval_edit_invalidates_old_approval():
    env = _build_env(request_id="req-r4", fallback_preset_id=None, run_id="run-r4")
    planner = _run_dynamic_planner(env, [_valid_output()])
    dyn_draft = planner.draft
    original = compute_candidate_plan_digest(
        request=env.request, draft=dyn_draft,
        context_content_digest=env.context.content_digest)
    # a human approves the ORIGINAL candidate digest.
    approval = PlanApproval(
        request_id=env.request.request_id, candidate_plan_digest=original,
        decision=ApprovalDecision.APPROVED, actor_id="human-approver",
        decided_at=NOW)
    assert approval.authorizes_freeze(
        request_id=env.request.request_id, candidate_plan_digest=original)

    # editing any executable field (the goal) yields a NEW digest.
    edited_draft = PlanDraft(**{**dyn_draft.model_dump(), "goal": "an edited goal"})
    edited = compute_candidate_plan_digest(
        request=env.request, draft=edited_draft,
        context_content_digest=env.context.content_digest)
    assert edited != original
    # the old approval no longer authorizes the edited candidate (re-approval required).
    assert not approval.authorizes_freeze(
        request_id=env.request.request_id, candidate_plan_digest=edited)


def test_redline_planner_cannot_schedule_compat_or_itself():
    env = _build_env(request_id="req-r5", fallback_preset_id=None, run_id="run-r5")
    p7_snapshot = p7.phase7_catalog_snapshot()
    worker_ids = {w.id for w in p7_snapshot.workers}
    # the Planner is not a catalog worker id.
    assert "orchestrator.planner" not in worker_ids
    # there IS at least one compat.* (static_legacy_only) worker to try to select.
    compat_ids = [w.id for w in p7_snapshot.workers if w.id.startswith("compat.")]
    assert compat_ids

    def _envelope(worker_id):
        return PlannerDraftEnvelope(
            nodes=(PlannerNodeEnvelope(id="n1", worker_id=worker_id, writes_slot="s1"),),
            sink_node_ids=("n1",), budget_request_tokens=0,
            budget_request_llm_invocations=0)

    # selecting a compat.* worker -> planner_worker_not_dynamic.
    with pytest.raises(PlannerShapeRejected) as ei_compat:
        assemble_dynamic_draft(
            _envelope(compat_ids[0]), request=env.request, context=env.context,
            context_snapshot_ref=env.ctx_ref, catalog=p7_snapshot,
            schema_registry=env.registry, draft_id="d-compat", run_id=env.run_id)
    assert "planner_worker_not_dynamic" in ei_compat.value.issue_codes

    # selecting the Planner itself -> planner_unknown_worker (not a catalog worker).
    with pytest.raises(PlannerShapeRejected) as ei_self:
        assemble_dynamic_draft(
            _envelope("orchestrator.planner"), request=env.request, context=env.context,
            context_snapshot_ref=env.ctx_ref, catalog=p7_snapshot,
            schema_registry=env.registry, draft_id="d-self", run_id=env.run_id)
    assert "planner_unknown_worker" in ei_self.value.issue_codes


def test_redline_dynamic_never_lease_matches_and_drift_refused(tmp_path):
    env = _build_env(request_id="req-r6", fallback_preset_id=RESEARCH_BASELINE,
                     run_id="run-r6")
    fb_draft = _materialize_fallback(env, draft_id="plan-r6")
    service = _admission_service(env, fb_draft)
    journal = tmp_path / "plan_approvals.jsonl"
    coord = _coordinator(env, service, journal, lease=True)
    _issue_headline_lease(coord, env, max_admissions=5, budget_cap=100)

    # a DYNAMIC card carries no preset provenance -> structurally never matches.
    from guanlan_v2.orchestration.plan_diff import PlanDiff, PlanDiffEntry, PendingPlanApproval
    diff = PlanDiff(
        baseline_kind="none", request_digest=env.request.semantic_digest(),
        candidate_plan_digest="a" * 64,
        entries=(PlanDiffEntry(pointer="goal", change="added",
                               baseline_json=None, candidate_json='"v"'),))
    dd = diff.semantic_digest()
    dyn_card = PendingPlanApproval(
        request_id="req-dyn", candidate_plan_digest="a" * 64, goal="g",
        source=PlanSource.DYNAMIC, approval_policy=ApprovalPolicy.REQUIRED, node_count=1,
        worker_ids=("text.sentiment",), plan_diff_ref=TypedPayloadRef(
            schema_ref=PLAN_DIFF_SCHEMA_REF,
            payload_ref=PayloadRef(namespace="main", object_id="o-dyn", content_digest=dd)),
        rendered_md="body", rendered_from_diff_digest=dd, candidate_id="cand-dyn",
        requested_at=NOW)
    out_dyn = coord.register_and_try_lease(
        dyn_card, idempotency_key="try-dyn", now=NOW,
        candidate_catalog_digest=env.snapshot.catalog_digest,
        candidate_registry_digest=env.registry.registry_digest)
    assert out_dyn.outcome == "pending_human"

    # a preset candidate built against a DRIFTED catalog digest refuses the lease.
    out_drift = coord.register_and_try_lease(
        _synthetic_preset_pending(env, "e" * 64, request_id="req-drift", candidate_id="cand-drift"),
        idempotency_key="try-drift", now=NOW,
        candidate_catalog_digest="0" * 64,
        candidate_registry_digest=env.registry.registry_digest)
    assert out_drift.outcome == "pending_human"


def test_redline_journal_replay_admits_exactly_once(tmp_path):
    env = _build_env(request_id="req-r7", fallback_preset_id=None, run_id="run-r7")
    planner = _run_dynamic_planner(env, [_valid_output()])
    dyn_draft = planner.draft
    service = _admission_service(env, dyn_draft)
    prep = service.prepare_candidate(dyn_draft.id, request_id=env.request.request_id)
    cand, res = service.persist_and_reserve_candidate(prep, idempotency_key="reserve-r7")
    candidate_digest = cand.candidate_plan_digest
    diff = build_plan_diff(
        dyn_draft, request=env.request, candidate_plan_digest=candidate_digest,
        baseline=None, baseline_kind="none")
    pending = build_pending_plan_approval(
        draft=dyn_draft, request=env.request, candidate_plan_digest=candidate_digest,
        diff=diff, plan_diff_ref=_diff_ref(diff), planner_rationale=None,
        candidate_id="cand-r7", requested_at=env.clock.now())
    journal = tmp_path / "plan_approvals.jsonl"
    coord = _coordinator(env, service, journal)
    coord.register_pending(pending, idempotency_key="pending-r7")
    coord.decide(
        request_id=env.request.request_id, candidate_plan_digest=candidate_digest,
        decision=ApprovalDecision.APPROVED, actor=GOOD_CRED, reason=None,
        idempotency_key="decide-r7")

    def _approved_count():
        return len([
            e for e in env.stores.events.journal(env.run_id, "main")
            if e.event_type is EventType.PLAN_APPROVED and e.plan_digest == candidate_digest])

    assert _approved_count() == 1
    # replay resubmits record_approval idempotently -> still exactly one.
    PlanApprovalCoordinator.replay(
        journal, admission=service, clock=env.clock, verifier=_Verifier(),
        approvals_sink=lambda ap: env.approvals.__setitem__(
            (ap.request_id, ap.candidate_plan_digest), ap))
    assert _approved_count() == 1


# --------------------------------------------------------------------------- #
# Red line — import + capability sweep over the four Phase-7 modules            #
# --------------------------------------------------------------------------- #
_PHASE7_MODULES = (orchestrator_mod, plan_presets_mod, plan_diff_mod, approval_mod)

#: module-path substrings a Phase-7 planning/approval module must never import.
_BANNED_IMPORT_SUBSTRINGS = (
    "guanlan_v2.seats",
    "guanlan_v2.trade",
    ".order",
    ".broker",
    ".execution_engine",
)


def _import_edges(module):
    src = Path(module.__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)
    edges: list[tuple[str | None, tuple[str, ...]]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            edges.append((None, tuple(a.name for a in node.names)))
        elif isinstance(node, ast.ImportFrom):
            edges.append((node.module, tuple(a.name for a in node.names)))
    return edges


def _is_memory_write_module(mod: str) -> bool:
    """A memory-WRITE surface. ``memory.proposals`` / ``memory.models`` are the
    read/auth verifier ports (legitimate); ``memory.experience`` and any *write*
    module are banned. None of the four modules import memory at all today, so this
    is a forward-looking firewall."""
    low = mod.lower()
    if ".memory" not in low:
        return False
    return "experience" in low or "write" in low


@pytest.mark.parametrize("module", _PHASE7_MODULES, ids=lambda m: m.__name__.split(".")[-1])
def test_redline_phase7_modules_import_no_seats_trade_or_memory_write(module):
    for imported_module, names in _import_edges(module):
        if imported_module is not None:
            for banned in _BANNED_IMPORT_SUBSTRINGS:
                assert banned not in imported_module, (
                    f"{module.__name__} imports {imported_module!r} (banned {banned!r})")
            assert not _is_memory_write_module(imported_module), (
                f"{module.__name__} imports a memory-write surface {imported_module!r}")
        for name in names:
            for banned in ("guanlan_v2.seats", "guanlan_v2.trade"):
                assert banned not in name, f"{module.__name__} imports {name!r}"


def test_redline_phase7_catalog_has_no_order_or_signal_write_capability():
    # the capability sweep: no order/signal-write capability exists to grant a Planner
    # -selected worker, and no worker carries live/executable decision authority.
    snap = p7.phase7_catalog_snapshot()
    banned_kind_tokens = ("order", "trade", "execut", "signal_write", "actuat", "broker")
    for cap in snap.capability_manifest:
        kind = str(getattr(cap, "capability_kind", "")).lower()
        assert kind in ("data_adapter", "tool"), kind
        for tok in banned_kind_tokens:
            assert tok not in kind, f"capability kind {kind!r} looks order/signal-write"
    for w in snap.workers:
        authority = str(getattr(w, "decision_authority", "none")).lower()
        assert authority in ("none", "advisory_only"), (w.id, authority)


# --------------------------------------------------------------------------- #
# Carry (Task-2 review) — future-worker params-schema authority firewall        #
# --------------------------------------------------------------------------- #
def test_carry_future_worker_params_schema_disjoint_from_authority_keys():
    """Every ``dynamic_allowed`` worker's params-schema property names (resolved
    where a ``params_schema_ref`` exists) must stay disjoint from the reserved
    authored + hidden authority key sets. No current worker declares a params schema,
    so this is a forward-looking firewall: a future worker whose params schema reuses
    an authority field name would go red here."""
    banned = set(PLANNER_RESERVED_AUTHORED_FIELDS) | set(_HIDDEN_AUTHORITY_KEYS)
    assert banned, "the authority key sets must be non-empty for this firewall to bite"

    registry = default_registry()
    resolver = SchemaRegistryResolver()
    resolver.register(registry)
    rt = phase2_runtime_registry(registry.registry_digest)
    resolver.register(rt)

    checked_workers = 0
    checked_schemas = 0
    for snap in (load_pilot_catalog().snapshot, p7.phase7_catalog_snapshot()):
        for w in snap.workers:
            if w.selection_scope != "dynamic_allowed":
                continue
            checked_workers += 1
            ref = w.params_schema_ref
            if ref is None:
                continue
            model = None
            for reg in (registry, rt):
                try:
                    model = reg.resolve(ref)
                    break
                except Exception:
                    continue
            assert model is not None, f"unresolvable params_schema_ref for {w.id}"
            props = set(model.model_fields)
            checked_schemas += 1
            assert props.isdisjoint(banned), (
                f"worker {w.id} params schema reuses an authority key: "
                f"{sorted(props & banned)}")
    assert checked_workers >= 3  # the firewall really iterated the dynamic roster
    assert checked_schemas == 0  # no dynamic worker declares a params schema today; flips when one appears
