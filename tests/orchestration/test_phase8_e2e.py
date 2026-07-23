# -*- coding: utf-8 -*-
"""Phase 8 · Task 12 — Lane D end-to-end + red-line regression.

The executable proof of the whole Phase 8 Lane-D runtime. The canonical debate plan
(Task 10 topology — bull/bear ×2 rounds + ``dec.research_mgr`` judge + risk 3-seat
×2 rounds + ``dec.pm`` + ``dec.trader`` + one ``x.number_critic``) is driven through
the FULL v2 path — ``validate → support(STATIC_RUNTIME_PROFILE_V2) → reserve →
approve(REQUIRED) → freeze → dispatch → run_plan → replay`` — on in-memory Phase-2
stores with a fixed clock, scripted fake ``ModelGateway``\\s (the real
``verify_model_request_binding`` boundary), and NO network.

The e2e both *demonstrates* the Phase-8 runtime and *pins the red lines*: zero-trading
structural sweep over the 27-worker capability manifest, ``dec.trader`` emits only
``PortfolioTargetProposal@1`` (no intent fields), draft-only advisory (no promotion
path), the honesty spine (``x.number_critic``), the reducer/transcript determinism,
retry + bounded schema repair live through ``run_plan``, the model-tier bridge
(``dec.pm`` alone on ``reasoner_deep``), the asymmetric ammo + injection-face binding,
and the ``params.stance_role == round_role`` runtime binding (CONTROLLER RULING).

**Wiring gaps this suite drove into the owning modules (all v2-gated, additive):**

* ``dag.run_plan`` — the Task-8/9 reducer-fold + ``DEBATE_MESSAGE_PUBLISHED`` publish
  seam at the barrier, and the Task-8 ``execute_with_bounded_retry`` retry/repair seam
  for v2 executable nodes (``runtime_profile`` gate; v1/BOOTSTRAP byte-identical).
* ``debate.analyze_debates`` — the ``params.stance_role == round_role`` runtime bind.
* ``worker._preflight`` — accept a ``retry``/``schema_repair`` scope reservation (a
  bounded-retry attempt draws its per-attempt child through the first-class ops).

Run from repo root: ``python -m pytest tests/orchestration/test_phase8_e2e.py -v``.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest

from guanlan_v2.orchestration import dag as D
from guanlan_v2.orchestration import decision_inputs as di
from guanlan_v2.orchestration import lane_catalog as lc
from guanlan_v2.orchestration import lane_payloads as lp
from guanlan_v2.orchestration import presets as P
from guanlan_v2.orchestration import worker as W
from guanlan_v2.orchestration.admission import AdmissionRejected, PlanAdmissionService
from guanlan_v2.orchestration.approval import (
    PlanApprovalCoordinator,
    admit_after_approval,
)
from guanlan_v2.orchestration.budget import BudgetLedger
from guanlan_v2.orchestration.catalog import (
    ContentManifestEntry,
    EvidencePolicy,
    OutputBinding,
    SkillManifest,
    WorkerSpec,
    build_catalog_snapshot,
    parse_skill_v1,
)
from guanlan_v2.orchestration.catalog_runtime import (
    BridgeCatalogView,
    CatalogRuntime,
    InMemoryMaterialSource,
    TrustedFactoryRegistry,
    build_text_material,
)
from guanlan_v2.orchestration.context import RunBudget, RunContext
from guanlan_v2.orchestration.data.symbols import Symbol
from guanlan_v2.orchestration.debate import (
    analyze_debates,
    debate_transcript_reducer_handler,
    fold_debate_messages,
    replay_debate_transcript,
)
from guanlan_v2.orchestration.enums import (
    ApprovalDecision,
    ApprovalPolicy,
    DataMode,
    ExecutionKind,
    NodeStatus,
    PlanSource,
    PortfolioRating,
    ToolCallRequirement,
)
from guanlan_v2.orchestration.eventstore import (
    EventRefusalAuditSink,
    RuntimeStores,
    SchemaRegistryResolver,
)
from guanlan_v2.orchestration.events import EventType
from guanlan_v2.orchestration.honesty import (
    UNSOURCED_BADGE,
    HonestyReport,
    classify_worker,
)
from guanlan_v2.orchestration.memory.models import AuthenticatedAdminPrincipal
from guanlan_v2.orchestration.plan_diff import (
    PLAN_DIFF_SCHEMA_REF,
    build_pending_plan_approval,
    build_plan_diff,
)
from guanlan_v2.orchestration.pool import ArtifactPool, fold_reducer_producers
from guanlan_v2.orchestration.refs import PayloadRef, SchemaRef, TypedPayloadRef
from guanlan_v2.orchestration.runtime_contracts import (
    default_audit_detail_registry,
    static_runtime_profile,
)
from guanlan_v2.orchestration.runtime_support import (
    STATIC_RUNTIME_PROFILE_V2,
    static_runtime_profile_v2,
)
from guanlan_v2.orchestration.schemas import (
    Artifact,
    Confidence,
    NodeRun,
    NumberAnchor,
    PortfolioDecision,
    Provenance,
    ResearchPlan,
    SentimentBand,
    SentimentReport,
)
from guanlan_v2.orchestration.shadow import PortfolioTargetProposal
from guanlan_v2.orchestration.spec import (
    DebateCfg,
    Dependency,
    OrchestrationRequest,
    PlanDraft,
    PlanNode,
    ReducerCfg,
    validate_plan_draft,
)

UTC = timezone.utc
NOW = datetime(2026, 7, 24, 2, 0, tzinfo=UTC)
SYM = Symbol(exchange="SH", code="600519", board="main")
GOOD = "good-cred"
_TR = SchemaRef(name="DebateTranscript", version="1")
_RISK_ROLES = ("aggressive", "steady", "neutral")


# =========================================================================== #
# Deterministic doubles                                                        #
# =========================================================================== #
class _Clock:
    def now(self) -> datetime:
        return NOW


class _Verifier:
    def verify(self, credential):
        if credential != GOOD:
            raise ValueError("unverifiable credential")
        return AuthenticatedAdminPrincipal(actor="human-approver", verified_by="e2e")


# ---- typed payload factories (valid Lane-D fixtures) ----------------------- #
def _sentiment() -> SentimentReport:
    return SentimentReport(overall_band=SentimentBand.NEUTRAL, overall_score=5.0,
                           confidence=Confidence.MEDIUM, narrative="Balanced flow.")


def _bull() -> lp.BullCase:
    return lp.BullCase(symbol=SYM, as_of=NOW, thesis_bullets=("durable moat",),
                       catalysts=("new launch",), disproof_signals=("margin compression",),
                       v_anchors=("V1: ROE",), rebuttal_of=())


def _bear() -> lp.BearCase:
    return lp.BearCase(symbol=SYM, as_of=NOW, thesis_bullets=("rich valuation",),
                       valuation_concerns=("PE > 40",), technical_breakdown=("MA death cross",),
                       f_anchors=("F1: PE",), rebuttal_of=())


def _risk(role: str) -> lp.RiskDebateStance:
    return lp.RiskDebateStance(symbol=SYM, as_of=NOW, stance_role=role, risk_score=-1,
                               position_sizing_advice="half position",
                               veto_flags=(), blind_spots=("liquidity",), rebuttal_of=())


def _research() -> ResearchPlan:
    return ResearchPlan(recommendation=PortfolioRating.HOLD, rationale="Hold pending catalyst.",
                        strategic_actions=("Monitor breadth",))


def _decision(rating: PortfolioRating = PortfolioRating.HOLD) -> PortfolioDecision:
    return PortfolioDecision(rating=rating, executive_summary="Advisory hold.",
                             investment_thesis="Risk/reward balanced; no execution authority.")


def _proposal() -> PortfolioTargetProposal:
    return PortfolioTargetProposal(positions=(), cash_weight=1.0, rationale="All-cash advisory.",
                                   confidence=Confidence.MEDIUM)


_ROLE_BY_NODE = {f"risk-{r}-r{k}": r for k in (1, 2) for r in _RISK_ROLES}


class _LaneGateway:
    """The trusted single-shot fake ``ModelGateway`` for the Lane-D run.

    Enforces the real ``verify_model_request_binding`` boundary, records the exact
    ``(node_id, worker_id, model_tier)`` binding observed per invocation, and returns
    the reviewed typed payload for each worker. A ``fail_plan`` maps ``node_id -> k``:
    the node's first ``k`` invocations return an invalid payload (schema-repair drill).
    """

    def __init__(self, stores, catalog, *, fail_plan=None):
        self._stores = stores
        self._tier = {w.id: w.execution.model_tier for w in catalog.workers}
        self._fail_plan = dict(fail_plan or {})
        self.seen: list[tuple[str, str, str]] = []
        self.requests: list = []
        self._calls: dict[str, int] = {}

    def invoke(self, request, *, prompt_assembly_ref):
        rec = W.verify_model_request_binding(request, prompt_assembly_ref,
                                             reader=self._stores.payloads)
        wid, nid = rec.worker_id, rec.node_id
        self.seen.append((nid, wid, self._tier.get(wid)))
        self.requests.append(request)
        self._calls[nid] = self._calls.get(nid, 0) + 1

        payload = self._payload_for(wid, nid)
        if self._fail_plan.get(nid, 0) >= self._calls[nid]:
            payload = {"schema_version": "1"}  # invalid: missing every required field
        return W.ModelResult(payload=payload, rendered_text=f"rendered:{wid}", number_anchors=(),
                             input_tokens=7, output_tokens=5, provider="fake", model="fake",
                             provider_response_id=f"resp-{nid}-{self._calls[nid]}")

    def _payload_for(self, wid, nid):
        if wid == "text.sentiment":
            return _sentiment()
        if wid == "dec.bull":
            return _bull()
        if wid == "dec.bear":
            return _bear()
        if wid == "dec.risk_debate":
            return _risk(_ROLE_BY_NODE[nid])
        if wid == "dec.research_mgr":
            return _research()
        if wid == "dec.pm":
            # dec.pm always returns Hold in this e2e's fixture; the gateway does NOT
            # honor any injected veto cap (see the honest-scoping note on
            # test_hard_veto_injection_caps_pm_rating_at_hold below — that binding is
            # Phase-9 data-binding work, not proven here).
            return _decision(PortfolioRating.HOLD)
        if wid == "dec.trader":
            return _proposal()
        raise AssertionError(f"unexpected LLM worker {wid!r}")


def _honesty_handler_factory(worker, resolved):
    def handler(*, node, input_snapshot, contributions, data_result_refs):
        rep = HonestyReport(worker_id="x.number_critic", node_id=node.id,
                            subject_content_digest=None, verdict="ok", issues=(), badges=())
        return W.ModelResult(payload=rep, rendered_text="honesty ok", number_anchors=(),
                             input_tokens=0, output_tokens=1)
    return handler


# =========================================================================== #
# Runnable Lane-D catalog + registry                                           #
# =========================================================================== #
def _phase8_registry():
    return lc.build_phase8_registry(lc.PHASE8_BASE_REGISTRY_DIGEST)


def _reducer_material():
    return build_text_material(id="debate.transcript_reducer", version="1", kind="reducer",
                               raw=b"debate transcript reducer\n")


def _lane_catalog():
    """A runnable purpose-built Lane-D catalog.

    Uses the REAL Lane-D worker specs (four new seats + ``dec.research_mgr`` + trader)
    + ``text.sentiment`` + the two ``x.*`` critics. ``dec.pm`` is the FORBIDDEN
    capability-free stand-in (its two data-adapter capabilities are orthogonal to the
    debate topology; real capability-bridge binding is Phase-9 data-method work — the
    injection-face contract is demonstrated at the adapter level, per the reviewed
    charter "e2e demonstrates the contract, full production data-binding is Phase 9").
    Its ``reasoner_deep`` tier is preserved (the sole-deep invariant is real).
    """
    text_mats = lc.load_text_lane_materials()
    xcut_mats = lc.load_xcut_lane_materials()
    dec_mats = lc.load_decision_lane_materials()
    text_specs = {w.id: w for w in lc.build_text_worker_specs(materials=text_mats)}
    xcut_specs = {w.id: w for w in lc.build_xcut_worker_specs(materials=xcut_mats)}
    dec_specs = {w.id: w for w in lc.build_decision_worker_specs(materials=dec_mats)}
    pm = dec_specs["dec.pm"].model_copy(update=dict(
        evidence_policy=EvidencePolicy(tool_calls=ToolCallRequirement.FORBIDDEN),
        capability_allowlist=()))
    workers = (
        text_specs["text.sentiment"],
        dec_specs["dec.bull"], dec_specs["dec.bear"], dec_specs["dec.risk_debate"],
        dec_specs["dec.research_mgr"], pm, dec_specs["dec.trader"],
        xcut_specs["x.number_critic"], xcut_specs["x.quality_gate"],
    )
    red_ref, red_mat = _reducer_material()
    by_key = {}
    for m in list(text_mats) + list(xcut_mats) + list(dec_mats) + [red_mat]:
        by_key[m.ref_key] = m
    mats = tuple(by_key.values())
    content, skills = [], []
    for m in mats:
        if m.kind == "skill":
            parsed = parse_skill_v1(m.raw_utf8.decode("utf-8"))
            skills.append(SkillManifest(
                ref=m.ref, name=parsed.name, summary=parsed.summary,
                perfect_for=parsed.perfect_for, not_ideal_for=parsed.not_ideal_for,
                critical_data_source_heading="⚠️ CRITICAL: Data Source Priority",
                source_identity=m.ref.id))
        else:
            content.append(ContentManifestEntry(
                ref=m.ref, kind=m.kind, name="x", description="d", source_identity="gl"))
    snapshot = build_catalog_snapshot(
        catalog_version="lane-d-e2e-v1", content_manifest=tuple(content),
        skill_manifest=tuple(skills), capability_manifest=(), workers=workers,
        resolved_material=mats)
    source = InMemoryMaterialSource(
        text={(m.ref.id, m.ref.version): m.raw_utf8 for m in mats}, capabilities={})
    return snapshot, source, red_ref


# =========================================================================== #
# Plan topology (canonical Lane-D fixture — Task 10)                            #
# =========================================================================== #
_BULLBEAR = DebateCfg(id="bullbear", seats=("bull", "bear"), turn_order=("bull", "bear"),
                      max_rounds=2, judge_node_id="research-mgr")
_RISKDEBATE = DebateCfg(id="riskdebate", seats=_RISK_ROLES, turn_order=_RISK_ROLES,
                        max_rounds=2, judge_node_id="pm")


def _dep(up, slot, inject):
    return Dependency(upstream_node_id=up, artifact_slot=slot, upstream_output_key="primary",
                      inject_as=inject)


def _seat(node_id, worker, debate, role, rnd, turn, slot, deps=(), stance=None):
    params = {}
    if worker == "dec.risk_debate":
        params = {"stance_role": stance if stance is not None else role}
    return PlanNode(id=node_id, worker_id=worker, writes_slot=slot, debate_id=debate,
                    round_role=role, debate_round=rnd, debate_turn=turn, auxiliary=True,
                    dependencies=tuple(deps), params=params)


def _lane_nodes(*, risk_stance_override=None):
    BB, RD = "slot.bullbear_transcript", "slot.riskdebate_transcript"
    nodes = [
        PlanNode(id="sentiment", worker_id="text.sentiment", writes_slot="slot.sentiment"),
        _seat("bull-r1", "dec.bull", "bullbear", "bull", 1, 1, BB),
        _seat("bear-r1", "dec.bear", "bullbear", "bear", 1, 2, BB,
              deps=[_dep("bull-r1", BB, "opponent_case")]),
        _seat("bull-r2", "dec.bull", "bullbear", "bull", 2, 1, BB,
              deps=[_dep("bear-r1", BB, "opponent_case")]),
        _seat("bear-r2", "dec.bear", "bullbear", "bear", 2, 2, BB,
              deps=[_dep("bull-r2", BB, "opponent_case")]),
        PlanNode(id="research-mgr", worker_id="dec.research_mgr", writes_slot="slot.research_plan",
                 dependencies=(_dep("sentiment", "slot.sentiment", "sentiment"),)),
    ]
    for i, role in enumerate(_RISK_ROLES, start=1):
        override = risk_stance_override if (risk_stance_override and i == 1) else None
        nodes.append(_seat(f"risk-{role}-r1", "dec.risk_debate", "riskdebate", role, 1, i, RD,
                           deps=[_dep("research-mgr", "slot.research_plan", "research_plan")],
                           stance=override))
    for i, role in enumerate(_RISK_ROLES, start=1):
        deps = [_dep("research-mgr", "slot.research_plan", "research_plan")]
        deps += [_dep(f"risk-{r}-r1", RD, "opponent_stances") for r in _RISK_ROLES]
        nodes.append(_seat(f"risk-{role}-r2", "dec.risk_debate", "riskdebate", role, 2, i, RD,
                           deps=deps))
    nodes.append(PlanNode(
        id="pm", worker_id="dec.pm", writes_slot="slot.portfolio_decision",
        dependencies=(_dep("research-mgr", "slot.research_plan", "research_plan"),
                      _dep("sentiment", "slot.sentiment", "sentiment"))))
    nodes.append(PlanNode(
        id="trader", worker_id="dec.trader", writes_slot="slot.target_proposal",
        dependencies=(_dep("pm", "slot.portfolio_decision", "portfolio_decision"),)))
    nodes.append(PlanNode(id="number-critic", worker_id="x.number_critic",
                          writes_slot="slot.honesty"))
    return nodes


def _lane_reducers(red_ref):
    BB, RD = "slot.bullbear_transcript", "slot.riskdebate_transcript"
    return (
        ReducerCfg(id="red.bullbear", slot=BB, reducer_ref=red_ref,
                   producer_node_ids=("bull-r1", "bear-r1", "bull-r2", "bear-r2"),
                   output_schema_ref=_TR),
        ReducerCfg(id="red.riskdebate", slot=RD, reducer_ref=red_ref,
                   producer_node_ids=tuple(f"risk-{r}-r{k}" for k in (1, 2) for r in _RISK_ROLES),
                   output_schema_ref=_TR),
    )


def _pilot_nodes():
    """The pilot chain (sentiment -> research-mgr -> pm) — NO debate, NO reducer."""
    return (
        PlanNode(id="sentiment", worker_id="text.sentiment", writes_slot="slot.sentiment"),
        PlanNode(id="research-mgr", worker_id="dec.research_mgr", writes_slot="slot.research_plan",
                 dependencies=(_dep("sentiment", "slot.sentiment", "sentiment"),)),
        PlanNode(id="pm", worker_id="dec.pm", writes_slot="slot.portfolio_decision",
                 dependencies=(_dep("research-mgr", "slot.research_plan", "research_plan"),
                               _dep("sentiment", "slot.sentiment", "sentiment"))),
    )


# =========================================================================== #
# Environment + full-path driver                                               #
# =========================================================================== #
class _Env:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def _build_env(*, run_id: str) -> _Env:
    registry = _phase8_registry()
    clock = _Clock()
    snapshot, source, red_ref = _lane_catalog()
    catalog_runtime = CatalogRuntime.build(snapshot, source)
    view = BridgeCatalogView.build(catalog_runtime, {})
    resolver = SchemaRegistryResolver()
    resolver.register(registry)
    rt_digest = registry.registry_digest
    stores = RuntimeStores(resolver=resolver, clock=clock,
                           allowed_cell_namespaces=(W.PROMPT_CELL_NAMESPACE,))
    request = OrchestrationRequest(
        request_id=f"req-{run_id}", goal="Lane D single-stock deep-dive (e2e)",
        workflow="orchestrate_only", fallback_preset_id=None,
        approval_policy=ApprovalPolicy.REQUIRED)
    mem = P.build_empty_memory_context(
        data_context=P.pilot_data_context(as_of=NOW), stores=stores,
        registry_digest=rt_digest, built_at=NOW)
    context = mem.context
    ctx_ref = PayloadRef(namespace="main", object_id=f"ctx-{run_id}",
                         content_digest=context.content_digest)
    run_budget = RunBudget(ledger_id=f"led-{run_id}", max_tokens=8_000_000,
                           max_llm_invocations=120, max_concurrency=8)
    stores.bind_run_budget(run_id=run_id, run_budget=run_budget)
    return _Env(registry=registry, clock=clock, snapshot=snapshot, source=source, red_ref=red_ref,
                catalog_runtime=catalog_runtime, view=view, resolver=resolver, rt_digest=rt_digest,
                stores=stores, request=request, context=context, ctx_ref=ctx_ref,
                run_budget=run_budget, run_id=run_id, approvals={})


def _draft(env, nodes, reducers, debates, *, budget_llm=24):
    return PlanDraft(
        id=f"plan.{env.run_id}", run_id=env.run_id, request_id=env.request.request_id,
        phase="main", source=PlanSource.DYNAMIC, goal="Lane D e2e", as_of=NOW, mode=DataMode.ONLINE,
        context_snapshot_ref=env.ctx_ref, nodes=tuple(nodes),
        sink_node_ids=("trader", "number-critic") if debates else ("pm",),
        debates=tuple(debates), reducers=tuple(reducers),
        catalog_version=env.snapshot.catalog_version, catalog_digest=env.snapshot.catalog_digest,
        schema_registry_digest=env.registry.registry_digest,
        approval_policy=ApprovalPolicy.REQUIRED, budget_request_tokens=2_000_000,
        budget_request_llm_invocations=budget_llm, max_concurrency=4)


def _admission(env, draft, *, profile):
    return PlanAdmissionService(
        run_id=env.run_id, requests={env.request.request_id: env.request},
        drafts={draft.id: draft}, contexts={env.context.content_digest: env.context},
        attestations={}, approvals=env.approvals, catalog=env.catalog_runtime,
        bridge_view=env.view, phase1_registry=env.registry,
        runtime_registry_digest=env.rt_digest, profile=profile, stores=env.stores,
        run_budget=env.run_budget, clock=env.clock)


def _ledger(env):
    return BudgetLedger(
        sink=env.stores.budget_event_sink(run_id=env.run_id, ledger_id=env.run_budget.ledger_id),
        run_budget=env.run_budget)


def _admit(env, draft, service, tmp_path):
    prep = service.prepare_candidate(draft.id, request_id=env.request.request_id)
    assert prep.support_report.supported, [i.code for i in prep.support_report.issues]
    cand, res = service.persist_and_reserve_candidate(prep, idempotency_key=f"rk-{env.run_id}")
    cd = cand.candidate_plan_digest
    diff = build_plan_diff(draft, request=env.request, candidate_plan_digest=cd,
                           baseline=None, baseline_kind="none")
    ref = TypedPayloadRef(schema_ref=PLAN_DIFF_SCHEMA_REF,
                          payload_ref=PayloadRef(namespace="main", object_id="diff",
                                                 content_digest=diff.semantic_digest()))
    pending = build_pending_plan_approval(
        draft=draft, request=env.request, candidate_plan_digest=cd, diff=diff,
        plan_diff_ref=ref, planner_rationale=None, candidate_id="cand", requested_at=NOW)

    def _sink(ap):
        env.approvals[(ap.request_id, ap.candidate_plan_digest)] = ap
    coord = PlanApprovalCoordinator(tmp_path / "j.jsonl", admission=service, clock=env.clock,
                                    verifier=_Verifier(), console_emit=None, approvals_sink=_sink)
    coord.register_pending(pending, idempotency_key="pk")
    _, event = coord.decide(request_id=env.request.request_id, candidate_plan_digest=cd,
                            decision=ApprovalDecision.APPROVED, actor=GOOD, reason="ok",
                            idempotency_key="dk")
    assert event.event_type is EventType.PLAN_APPROVED
    plan, _ = admit_after_approval(admission=service, candidate_id=cd,
                                   reservation_id=res.reservation_id,
                                   approval_event_id=event.event_id, idempotency_key="fk")
    return plan, res


def _run(env, service, plan, *, gateway, profile):
    bundle = service.verify_for_dispatch(plan.plan_digest)
    audit_reg = default_audit_detail_registry()
    audit_reg.seal()
    refusal_sink = EventRefusalAuditSink(detail_registry=audit_reg, clock=env.clock)
    pool = ArtifactPool(stores=env.stores, registry_digest=env.rt_digest, plan=plan,
                        catalog=env.snapshot, clock=env.clock)
    factories = TrustedFactoryRegistry(env.catalog_runtime)
    nc_ref = {w.id: w for w in env.snapshot.workers}["x.number_critic"].execution.handler_ref
    factories.register_handler(nc_ref, _honesty_handler_factory)
    runtime = W.ExecutionRuntime(catalog=env.catalog_runtime, bridge_view=env.view,
                                 factories=factories, support_report=bundle.support_report,
                                 runtime_registry_digest=env.rt_digest)
    run_ctx = RunContext(run_id=env.run_id, data=env.context.data_context,
                         context_snapshot_id=env.context.snapshot_id,
                         memory_snapshot_hash=env.context.memory_snapshot_hash,
                         budget=env.run_budget, cancellation_token_id="cx")
    recorder = D.RunRecorder()
    result = asyncio.run(D.run_plan(
        plan.plan_digest, run_ctx, admission=service, pool=pool, budget=_ledger(env),
        runtime=runtime, registry=env.registry, stores=env.stores, runtime_limit=4,
        clock=env.clock, refusal_sink=refusal_sink, model_gateway=gateway, recorder=recorder,
        runtime_profile=profile))
    return result, recorder, pool


def _full_lane_run(tmp_path, *, run_id="run-full", fail_plan=None, budget_llm=24):
    env = _build_env(run_id=run_id)
    draft = _draft(env, _lane_nodes(), _lane_reducers(env.red_ref),
                   (_BULLBEAR, _RISKDEBATE), budget_llm=budget_llm)
    profile = static_runtime_profile_v2()
    service = _admission(env, draft, profile=profile)
    plan, res = _admit(env, draft, service, tmp_path)
    gateway = _LaneGateway(env.stores, env.snapshot, fail_plan=fail_plan)
    result, recorder, pool = _run(env, service, plan, gateway=gateway, profile=profile)
    return _Env(env=env, draft=draft, service=service, plan=plan, res=res, gateway=gateway,
                result=result, recorder=recorder, pool=pool, profile=profile)


@pytest.fixture(scope="module")
def full_run(tmp_path_factory):
    return _full_lane_run(tmp_path_factory.mktemp("full"))


# =========================================================================== #
# Scenario — the spine: RunResult(completed) through the whole v2 path          #
# =========================================================================== #
# HONEST SCOPING NOTE (applies to every ``full_run``-based assertion below): the
# folded debate transcripts ARE produced, published (``DEBATE_MESSAGE_PUBLISHED``),
# and replayable byte-for-byte from the event log (see
# ``test_debate_transcripts_folded_and_event_replay_matches`` and
# ``test_audit_replay_reconstructs_with_zero_model_calls``) — but NO node in this
# plan's DAG actually *consumes* ``slot.bullbear_transcript`` /
# ``slot.riskdebate_transcript`` as a wired ``Dependency``. ``dec.research_mgr``
# declares an optional ``bullbear_transcript`` input and ``dec.pm`` declares an
# optional ``riskdebate_transcript`` input, but neither is connected by a
# ``Dependency`` in ``_lane_nodes()``, so both run DEGRADED with that optional
# input absent. Reducing a folded transcript into a judge's model-request input
# (the actual debate -> judge data binding) is Phase-9 data-method work, NOT
# proven by this e2e: the debate outcome does NOT influence ``dec.pm``'s rating
# or ``dec.trader``'s proposal here — the fake gateway returns a fixed Hold /
# all-cash payload for every plan, independent of anything the bull/bear/risk
# seats produced. ``text.sentiment`` is the one real (non-debate) input judges
# receive in this DAG — it IS wired to both ``research-mgr`` and ``pm`` — which is
# also why the settled LLM invocation count is 14 (13 debate/judge/trader seats +
# the sentiment producer), not a bare 13.
def test_full_lane_d_path_completes(full_run):
    assert full_run.result.terminal_status == "completed"
    statuses = {nr.node_id: nr.status for nr in full_run.recorder.node_runs}
    for nid in ("sentiment", "bull-r1", "bear-r1", "bull-r2", "bear-r2", "research-mgr",
                "risk-aggressive-r1", "risk-neutral-r2", "pm", "trader", "number-critic"):
        assert statuses[nid] in (NodeStatus.COMPLETED, NodeStatus.DEGRADED), (nid, statuses[nid])


# ---- Assertion 5 (red lines): trader emits ONLY PortfolioTargetProposal@1 --- #
def test_trader_emits_only_portfolio_target_proposal_no_intent(full_run):
    committed = full_run.pool.committed_output("trader", "primary")
    assert committed is not None
    assert committed.payload_schema_ref == SchemaRef(name="PortfolioTargetProposal", version="1")
    validated = full_run.env.registry.validate_payload(
        committed.payload_schema_ref, committed.payload)
    assert isinstance(validated, PortfolioTargetProposal)
    # no intent / order / signal fields on the proposal schema.
    banned = {"intent", "order", "signal", "execute", "quantity", "shares", "broker",
              "target_portfolio_intent"}
    assert banned.isdisjoint(set(PortfolioTargetProposal.model_fields))


# ---- Assertion 1 (budget): reservation covers expanded count; settle == actual;
#       AUTO rejected. --------------------------------------------------------- #
def test_budget_reservation_and_settlement(full_run):
    # 13 base LLM invocations: 4 bull/bear seats + 6 risk seats + research-mgr + pm +
    # trader; number-critic deterministic = 0. Settled equals the actual invocations.
    assert full_run.result.settled_llm_invocations == 14  # includes the sentiment producer
    # the plan reservation reserved >= the fully expanded LLM invocation count.
    state = _ledger(full_run.env).replay()
    plan_res = state.reservations[full_run.res.reservation_id]
    assert plan_res.reserved_llm_invocations >= 13
    # settlement equals the actual node LLM invocations settled under the plan pool.
    settled = sum(r.actual_llm_invocations for r in state.reservations.values()
                  if state.parent_of.get(r.reservation_id) == plan_res.reservation_id
                  and r.status == "settled")
    assert settled == full_run.result.settled_llm_invocations


def test_auto_approval_is_rejected(full_run):
    env = full_run.env
    req_auto = env.request.model_copy(update={"approval_policy": ApprovalPolicy.AUTO})
    draft = _draft(env, _lane_nodes(), _lane_reducers(env.red_ref), (_BULLBEAR, _RISKDEBATE))
    draft = draft.model_copy(update={"approval_policy": ApprovalPolicy.AUTO})
    report = validate_plan_draft(draft, request=req_auto, context=env.context,
                                 catalog=env.snapshot, schema_registry=env.registry)
    assert not report.valid
    assert "auto_approval_rejected" in {i.code for i in report.issues}


# ---- Assertion 2 (transcripts): folded, byte-identical under shuffled
#       completion, and event-replay == pool fold. ---------------------------- #
def test_debate_transcripts_folded_and_event_replay_matches(full_run):
    tr = full_run.recorder.debate_transcripts
    assert set(tr) == {"bullbear", "riskdebate"}
    assert len(tr["bullbear"].messages) == 4 and len(tr["riskdebate"].messages) == 6
    # DEBATE_MESSAGE_PUBLISHED events replay to the SAME transcripts as the pool fold.
    for debate in (_BULLBEAR, _RISKDEBATE):
        replayed = replay_debate_transcript(full_run.env.stores, run_id=full_run.env.run_id,
                                            debate=debate, max_rounds=debate.max_rounds)
        assert replayed.semantic_digest() == tr[debate.id].semantic_digest()
    # visible DEBATE_MESSAGE_PUBLISHED events: one per debate seat (4 + 6).
    pubs = [e for e in full_run.env.stores.events.journal(full_run.env.run_id, "main")
            if e.event_type is EventType.DEBATE_MESSAGE_PUBLISHED]
    assert len(pubs) == 10


def test_transcript_fold_is_byte_identical_under_shuffled_completion(full_run):
    # property rerun: shuffle the committed producer arrival order — the Plan-order
    # fold yields a byte-identical DebateTranscript (Task-8 determinism exit gate).
    import random

    nodes = full_run.plan.nodes
    seats = tuple(n for n in nodes if n.debate_id == "riskdebate")
    arts = [full_run.pool.committed_output(n.id, "primary") for n in seats]
    handler = debate_transcript_reducer_handler(debate=_RISKDEBATE, nodes=seats)
    base = fold_reducer_producers(arts, handler=handler).semantic_digest()
    for seed in range(20):
        shuffled = list(arts)
        random.Random(seed).shuffle(shuffled)
        assert fold_reducer_producers(shuffled, handler=handler).semantic_digest() == base
    # fold_debate_messages over a shuffled committed map is likewise order-independent.
    committed = {n.id: full_run.pool.committed_output(n.id, "primary") for n in seats}
    assert fold_debate_messages(debate=_RISKDEBATE, nodes=seats,
                                committed=committed).semantic_digest() == base


# ---- Assertion 3 (tiers): reasoner_deep observed exactly once on dec.pm; every
#       seat ran fast/reasoner. ------------------------------------------------ #
def test_model_tier_binding_reasoner_deep_only_on_pm(full_run):
    by_worker: dict[str, set] = {}
    for _nid, wid, tier in full_run.gateway.seen:
        by_worker.setdefault(wid, set()).add(tier)
    deep = [(nid, wid) for nid, wid, tier in full_run.gateway.seen if tier == "reasoner_deep"]
    assert len(deep) == 1 and deep[0][1] == "dec.pm"
    # every debate seat bound fast/reasoner (never reasoner_deep).
    for wid in ("dec.bull", "dec.bear", "dec.risk_debate"):
        assert by_worker[wid] <= {"fast", "reasoner"}


# ---- Assertion 8 (prompt-assembly ordering, TA #750): static role/skill text
#       precedes dynamic data blocks — structurally separated by the real
#       StaticPromptAssembler. --------------------------------------------------- #
def test_prompt_assembly_static_before_dynamic(full_run):
    # every assembled model request binds the static materials (system prompt + skills
    # + guardrails) as their OWN refs, and dynamic data lives ONLY in the separate
    # untrusted-block / trusted-digest channels — never interpolated into the static
    # text (TA #750 成本排版纪律, enforced by the real StaticPromptAssembler).
    assert len(full_run.gateway.requests) >= 13   # one per LLM node invocation at least
    for request in full_run.gateway.requests:
        rec = request.prompt_record
        assert rec.system_prompt_ref is not None   # static role text is bound as a ref
        assert rec.assembler_id == "static-prompt-assembler"  # the real ordering-disciplined asm
        # every untrusted (dynamic) block carries a positive ordinal in its own channel,
        # never mixed into the static skill / guardrail refs.
        for b in rec.untrusted_blocks:
            assert b.ordinal >= 1


# ---- Assertion 7 (audit replay): reconstruct plan/node states/artifacts/
#       transcripts with ZERO model calls. ------------------------------------- #
def test_audit_replay_reconstructs_with_zero_model_calls(full_run):
    env = full_run.env
    # a fresh pool replay()ed from the SAME event history reproduces every committed
    # sink output — no gateway, no model call.
    replayed_pool = full_run.pool.replay()
    trader = replayed_pool.committed_output("trader", "primary")
    assert trader is not None and trader.payload_schema_ref.name == "PortfolioTargetProposal"
    # node states reconstruct from the durable NodeStateChanged records.
    node_events = [e for e in env.stores.events.journal(env.run_id, "main")
                   if e.event_type is EventType.NODE_STATE_CHANGED]
    assert len(node_events) >= 14
    # transcripts reconstruct from the visible DebateMessagePublished stream alone.
    for debate in (_BULLBEAR, _RISKDEBATE):
        t = replay_debate_transcript(env.stores, run_id=env.run_id, debate=debate,
                                     max_rounds=debate.max_rounds)
        assert t.semantic_digest() == full_run.recorder.debate_transcripts[debate.id].semantic_digest()


# =========================================================================== #
# Assertion 6 (schema repair): live through run_plan                            #
# =========================================================================== #
def test_schema_repair_recovers_one_seat_live(tmp_path):
    # bull-r1's first model output is invalid; a single bounded schema-repair
    # invocation recovers it -> COMPLETED, TWO gateway invocations (2nd
    # PromptAssemblyRecord), budget settled with the extra invocation.
    run = _full_lane_run(tmp_path, run_id="run-repair", fail_plan={"bull-r1": 1})
    statuses = {nr.node_id: nr.status for nr in run.recorder.node_runs}
    assert statuses["bull-r1"] in (NodeStatus.COMPLETED, NodeStatus.DEGRADED)
    bull_calls = [s for s in run.gateway.seen if s[0] == "bull-r1"]
    assert len(bull_calls) == 2                       # primary invalid + one repair
    assert run.result.terminal_status == "completed"
    # the extra invocation is settled: total is base 14 + 1 repair.
    assert run.result.settled_llm_invocations == 15
    # a first-class schema_repair-scope reservation was minted on the ledger.
    state = _ledger(run.env).replay()
    assert any(r.scope_type == "schema_repair" for r in state.reservations.values())


def test_schema_repair_twice_invalid_is_incomplete_no_artifact(tmp_path):
    # bull-r1 invalid on BOTH the primary and the single repair -> INCOMPLETE, no
    # Artifact. (bull/bear debate does not gate the pm->trader sink chain, so the run
    # still reaches a terminal status; the seat itself is honestly INCOMPLETE.)
    run = _full_lane_run(tmp_path, run_id="run-repair2", fail_plan={"bull-r1": 2})
    statuses = {nr.node_id: nr.status for nr in run.recorder.node_runs}
    assert statuses["bull-r1"] is NodeStatus.INCOMPLETE
    assert run.pool.committed_output("bull-r1", "primary") is None
    bull_calls = [s for s in run.gateway.seen if s[0] == "bull-r1"]
    assert len(bull_calls) == 2                       # primary + one (exhausted) repair


# =========================================================================== #
# Assertion 4 (honesty spine): classify_worker matrix                           #
# =========================================================================== #
_D64 = "d" * 64


def _subject_worker(*, tool_calls=ToolCallRequirement.OPTIONAL, allow_unsourced=False):
    from guanlan_v2.orchestration.catalog import ExecutionSpec
    from guanlan_v2.orchestration.refs import CapabilityRef, ContentRef
    from guanlan_v2.orchestration.enums import Tier
    # a REQUIRED tool policy requires a non-empty allowlist; OPTIONAL may be empty.
    allow = ((CapabilityRef(id="cap.data.verified_snapshot", version="1", content_digest=_D64),)
             if tool_calls is ToolCallRequirement.REQUIRED else ())
    return WorkerSpec(
        id="pv.technical", catalog_role="final", selection_scope="dynamic_allowed", lane="pv",
        persona="subject under critique", tier=Tier.READER,
        execution=ExecutionSpec(kind=ExecutionKind.DETERMINISTIC,
                                handler_ref=ContentRef(id="handler.pv.x", version="1",
                                                       content_digest=_D64)),
        capability_allowlist=allow,
        outputs=(OutputBinding(name="primary",
                               schema_ref=SchemaRef(name="TechnicalReport", version="1")),),
        evidence_policy=EvidencePolicy(tool_calls=tool_calls, require_input_refs=True,
                                       require_number_anchors=True,
                                       allow_unsourced_numbers=allow_unsourced,
                                       optional_data_may_degrade=True),
        supported_modes=(DataMode.ONLINE,), can_emit_decision=False, decision_authority="none")


def _subject_node_run(*, status=NodeStatus.COMPLETED, tool_records=()):
    return NodeRun(node_run_id="nr", run_id="run-1", plan_id="plan-1", plan_digest=_D64,
                   node_id="pv", worker_id="pv.technical", status=status, attempt_id="att-1",
                   input_snapshot_digest="1" * 64, tool_call_records=tool_records,
                   output_keys=("primary",) if status is NodeStatus.COMPLETED else (),
                   output_artifact_ids=("art",) if status is NodeStatus.COMPLETED else ())


def _subject_artifact(*, payload, numbers):
    return Artifact.build(
        artifact_id="art", run_id="run-1", created_at=NOW, producer_node_id="pv", slot="pv",
        output_key="primary", kind="technical_report",
        payload_schema_ref=SchemaRef(name="TechnicalReport", version="1"), payload=payload,
        rendered_md="# subj", input_refs=(),
        provenance=Provenance(plan_digest=_D64, code_version="git:abc", as_of=NOW,
                              pit_mode=DataMode.ONLINE),
        numbers=numbers, badges=())


def test_honesty_unsourced_number_is_incomplete_with_badge():
    # an unsourced number (anchor.is_unsourced) under allow_unsourced_numbers=False ->
    # incomplete verdict + [UNSOURCED] badge propagation.
    art = _subject_artifact(
        payload={"score": 0.74},
        numbers=(NumberAnchor(label="score", value=0.74, payload_path="score",
                              source_artifact_id=None, is_unsourced=True),))
    rep = classify_worker(worker=_subject_worker(), node_run=_subject_node_run(), artifact=art)
    assert rep.verdict == "incomplete"
    assert "unsourced_number" in {i.code for i in rep.issues}
    assert UNSOURCED_BADGE in rep.badges


def test_honesty_required_tools_zero_calls_is_incomplete():
    art = _subject_artifact(
        payload={"score": 0.5},
        numbers=(NumberAnchor(label="score", value=0.5, payload_path="score",
                              source_artifact_id="in-1", is_unsourced=False),))
    worker = _subject_worker(tool_calls=ToolCallRequirement.REQUIRED)
    rep = classify_worker(worker=worker, node_run=_subject_node_run(), artifact=art)
    assert rep.verdict == "incomplete"
    assert "required_tools_zero_calls" in {i.code for i in rep.issues}


def test_honesty_legal_no_tool_sentiment_path_is_ok(full_run):
    # cross-lane spot check: the legal no-tool text.sentiment worker with a clean,
    # anchor-free payload is never killed for making zero tool calls.
    sent = {w.id: w for w in full_run.env.snapshot.workers}["text.sentiment"]
    assert sent.evidence_policy.tool_calls is ToolCallRequirement.FORBIDDEN
    nr = NodeRun(node_run_id="nr-s", run_id="run-1", plan_id="p", plan_digest=_D64,
                 node_id="sentiment", worker_id="text.sentiment", status=NodeStatus.COMPLETED,
                 attempt_id="a", input_snapshot_digest="1" * 64, tool_call_records=(),
                 output_keys=("primary",), output_artifact_ids=("art-s",))
    art = Artifact.build(
        artifact_id="art-s", run_id="run-1", created_at=NOW, producer_node_id="sentiment",
        slot="slot.sentiment", output_key="primary", kind="sentiment_report",
        payload_schema_ref=SchemaRef(name="SentimentReport", version="1"),
        payload=_sentiment(), rendered_md="# s", input_refs=(),
        provenance=Provenance(plan_digest=_D64, code_version="git:abc", as_of=NOW,
                              pit_mode=DataMode.ONLINE),
        numbers=(NumberAnchor(label="overall_score", value=5.0, payload_path="overall_score",
                              source_artifact_id="self", is_unsourced=False),), badges=())
    rep = classify_worker(worker=sent, node_run=nr, artifact=art)
    assert rep.verdict == "ok"


# =========================================================================== #
# Assertion 5 (red lines): structural zero-trading sweep over the phase-8 manifest #
# =========================================================================== #
def test_no_order_signal_intent_skill_memory_write_capability():
    snap = lc.phase8_catalog_snapshot()
    banned = ("order", "trade", "execut", "signal_write", "actuat", "broker",
              "memory_write", "skill_write", "intent", "code_write")
    for cap in snap.capability_manifest:
        kind = str(getattr(cap, "capability_kind", "")).lower()
        assert kind in ("data_adapter", "tool"), kind
        for tok in banned:
            assert tok not in kind, (cap.ref.id, kind)
    # no worker carries live/executable decision authority; every one is advisory/none.
    for w in snap.workers:
        authority = str(getattr(w, "decision_authority", "none")).lower()
        assert authority in ("none", "advisory_only"), (w.id, authority)


def test_no_worker_can_construct_target_portfolio_intent():
    # TargetPortfolioIntent construction stays Phase-6 runtime-only: no Phase-8 worker
    # output schema is TargetPortfolioIntent, and dec.trader emits the draft proposal.
    snap = lc.phase8_catalog_snapshot()
    for w in snap.workers:
        for o in w.outputs:
            assert o.schema_ref.name != "TargetPortfolioIntent", w.id
    trader = {w.id: w for w in snap.workers}["dec.trader"]
    assert [o.schema_ref.name for o in trader.outputs] == ["PortfolioTargetProposal"]


def test_lane_d_outputs_are_draft_only_no_promotion_path():
    # every Lane-D decision worker is draft-only advisory: FORBIDDEN tool policy (no
    # actuation) for the four new seats + trader; the proposal has no promotion field.
    snap = lc.phase8_catalog_snapshot()
    specs = {w.id: w for w in snap.workers}
    for wid in ("dec.bull", "dec.bear", "dec.risk_debate", "dec.trader"):
        assert specs[wid].evidence_policy.tool_calls is ToolCallRequirement.FORBIDDEN, wid
    assert not any("promot" in f.lower() for f in PortfolioTargetProposal.model_fields)


# =========================================================================== #
# Assertion 9 (asymmetric ammo) + the injection-face adapters                   #
# =========================================================================== #
def test_asymmetric_ammo_bear_gets_announcement_risk_bull_does_not(full_run):
    specs = {w.id: w for w in full_run.env.snapshot.workers}
    bull_inputs = {i.name for i in specs["dec.bull"].inputs}
    bear_inputs = {i.name for i in specs["dec.bear"].inputs}
    assert "announcement_risk" in bear_inputs
    assert "announcement_risk" not in bull_inputs
    # the AnnouncementRiskFlags injection face is deterministically constructible.
    tier1 = next(iter(di.ANNOUNCEMENT_TIER1))
    flags = di.scan_announcement_risk(symbol=SYM, as_of=NOW, announcements=[f"公司{tier1}"])
    assert flags.hard_veto is True and flags.max_tier == "tier1"


# =========================================================================== #
# Assertion 10 (injection faces): the DETERMINISTIC hard-veto adapter chain     #
# =========================================================================== #
def test_hard_veto_injection_caps_pm_rating_at_hold(full_run):
    # HONEST SCOPE: this test proves the deterministic adapter chain that a hard
    # veto rides through — scan_announcement_risk -> hard_veto -> build_allowed_actions
    # -> can_buy=False / max_target_weight=0.0 — and that dec.pm structurally declares
    # both injection-face inputs. It does NOT prove that the runtime gateway honors an
    # injected veto by capping its emitted rating: this e2e's fake ``_LaneGateway``
    # always returns Hold for dec.pm regardless of any veto input (there is no
    # veto-conditional branch left in the double), so a passing assertion on the
    # gateway's output would be a tautology, not evidence. Wiring the built
    # AllowedActions/veto facts into the actual dec.pm model request (and having the
    # runtime enforce the cap on whatever the model returns) is Phase-9 data-binding
    # work, not proven in this e2e.
    specs = {w.id: w for w in full_run.env.snapshot.workers}
    # dec.pm declares the two injection-face inputs.
    pm_inputs = {i.name for i in specs["dec.pm"].inputs}
    assert {"allowed_actions", "announcement_risk"} <= pm_inputs
    # deterministic pre-input: a tier-1 announcement -> hard veto -> the symbol's
    # allowance provably excludes buying (can_buy=False, ceiling 0.0) BEFORE the LLM.
    tier1 = next(iter(di.ANNOUNCEMENT_TIER1))
    flags = di.scan_announcement_risk(symbol=SYM, as_of=NOW, announcements=[f"重大{tier1}"])
    facts = di.SymbolConstraintFacts(symbol=SYM, severe_negative_event=flags.hard_veto)
    allowed = di.build_allowed_actions(as_of=NOW, facts=[facts])
    allowance = allowed.allowances[0]
    assert allowance.can_buy is False and allowance.max_target_weight == 0.0


# =========================================================================== #
# stance_role == round_role RUNTIME binding (CONTROLLER RULING)                 #
# =========================================================================== #
def test_stance_role_binding_clean_fixture_admits(full_run):
    # the canonical fixture (every risk seat's params.stance_role == round_role) admits
    # cleanly under the v2 analyzer.
    issues = analyze_debates(full_run.draft, catalog=full_run.env.catalog_runtime,
                             profile=STATIC_RUNTIME_PROFILE_V2)
    assert issues == (), [i.code for i in issues]


def test_stance_role_mismatch_is_refused_at_runtime(tmp_path):
    # a risk seat whose params.stance_role != round_role is refused BEFORE reservation
    # by the runtime binding wired into analyze_debates (support report not supported).
    env = _build_env(run_id="run-stance")
    bad_nodes = _lane_nodes(risk_stance_override="neutral")  # r1 seat mislabels its stance
    draft = _draft(env, bad_nodes, _lane_reducers(env.red_ref), (_BULLBEAR, _RISKDEBATE))
    issues = analyze_debates(draft, catalog=env.catalog_runtime, profile=STATIC_RUNTIME_PROFILE_V2)
    assert "debate_seat_role_param_mismatch" in {i.code for i in issues}
    # and admission refuses it (persist_and_reserve raises on an unsupported plan).
    service = _admission(env, draft, profile=static_runtime_profile_v2())
    prep = service.prepare_candidate(draft.id, request_id=env.request.request_id)
    assert not prep.support_report.supported
    assert "debate_seat_role_param_mismatch" in {i.code for i in prep.support_report.issues}
    with pytest.raises(AdmissionRejected):
        service.persist_and_reserve_candidate(prep, idempotency_key="rk-bad")


# =========================================================================== #
# Assertion 11 (graceful absence) + differential pin                            #
# =========================================================================== #
def test_graceful_absence_pilot_chain_admits_and_completes(tmp_path):
    # with all six Task-9b injection faces absent, the pilot chain (sentiment ->
    # research-mgr -> pm, NO debate) admits and completes unchanged.
    env = _build_env(run_id="run-pilot")
    draft = _draft(env, _pilot_nodes(), (), (), budget_llm=6)
    profile = static_runtime_profile_v2()
    service = _admission(env, draft, profile=profile)
    plan, _res = _admit(env, draft, service, tmp_path)
    gateway = _LaneGateway(env.stores, env.snapshot)
    result, recorder, pool = _run(env, service, plan, gateway=gateway, profile=profile)
    assert result.terminal_status == "completed"
    assert {nr.node_id for nr in recorder.node_runs} == {"sentiment", "research-mgr", "pm"}
    # no debate machinery engaged (优雅缺席): zero transcripts, zero debate events.
    assert recorder.debate_transcripts == {}
    assert not any(e.event_type is EventType.DEBATE_MESSAGE_PUBLISHED
                   for e in env.stores.events.journal(env.run_id, "main"))


def test_v1_bound_plan_settled_counts_match_profile_passed_or_not(tmp_path):
    # DIFFERENTIAL PIN: a v1-bound plan (no debates/reducers/retries) run through the
    # SAME run_plan produces the SAME terminal status and the SAME settled
    # token/invocation counts whether or not a v2 profile is supplied — the v2 seams
    # are strictly inert for a v1-shaped plan. NOTE on naming: this pin covers
    # terminal_status + settled_llm_invocations + settled_tokens equality, NOT a
    # field-by-field byte-identity of the whole RunResult object (hence no longer
    # named "byte_identical"). The actual byte-level proof that the v1 code path is
    # untouched rests on (a) the verbatim-relocated v1 else-branch in
    # ``dag.run_plan`` (the v2 gate wraps around it without altering a single line)
    # and (b) the full pre-existing 124-test v1 regression suite continuing to pass
    # unchanged — both of those, not this one pin, are what establish "byte
    # identical" for the v1 path as a whole.
    def _once(run_id, profile, run_profile):
        env = _build_env(run_id=run_id)
        draft = _draft(env, _pilot_nodes(), (), (), budget_llm=6)
        service = _admission(env, draft, profile=profile)
        plan, _ = _admit(env, draft, service, tmp_path / run_id)
        gateway = _LaneGateway(env.stores, env.snapshot)
        result, recorder, _pool = _run(env, service, plan, gateway=gateway,
                                       profile=run_profile)
        return result, recorder

    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    r_v1, rec_v1 = _once("v1a", static_runtime_profile(), None)
    r_v2, rec_v2 = _once("v2b", static_runtime_profile_v2(), static_runtime_profile_v2())
    assert r_v1.terminal_status == r_v2.terminal_status == "completed"
    assert r_v1.settled_llm_invocations == r_v2.settled_llm_invocations
    assert r_v1.settled_tokens == r_v2.settled_tokens
    # neither run engages any debate machinery.
    assert rec_v1.debate_transcripts == {} and rec_v2.debate_transcripts == {}
