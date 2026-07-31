# -*- coding: utf-8 -*-
"""Phase 10 · Task 12 — whole-pipeline e2e + red-line regression (the FINAL gate).

Two chain e2es on in-memory stores + scripted gateways (zero network, zero LLM,
injected clocks throughout), the Phase-10 red-line sweep, and the three
review-inherited pins (Task-11 minor #1 late-failure loudness, Task-11 minor #5
production factory call shapes, Task-7 roll-up deep-failure retry bound).

**A-chain (帷幄选股)** — fixture ranking → ``cand.v4`` slate →
``build_screening_batch`` → Phase-1 validation inside the REAL
``PlanAdmissionService`` → the Phase-7 lease channel with ONE human act (the
screening ``ApprovalLease`` whose envelope mirrors the whole-picture cost
preview) → per-lane ``admit_after_approval`` → the Task-0b production plan
runner over the REAL ``dag.run_plan`` (scripted gateways; per-code sealed lane
preset; ONE shared ``RuntimeStores`` for every lane — the Task-8b de-forced
shape) → ``build_recommendation_slate`` → banner-first md → archive landing spy.

    The batch admission rides ONE long-lived coordinator over a per-candidate
    admission dispatcher — the honest resolution the Task-8 replay-evidence
    suite recorded for Task 12 (``record_approval`` requires the RESERVING
    service instance, and per-lane budget visibility at execution requires
    ``plan.run_id == admission run_id``); the dispatcher forwards, never decides.

    Degraded-lane honesty comes in ALL its reachable shapes: lane 2's
    ``text.sentiment`` fails, leaving the BLOCK-fed terminal without its
    required input → no committed plan → ``degraded_lanes`` (and a
    ``LauncherError`` refusal, never a silent artifact); lane 0's verdict
    model returns ``degraded=True`` → the REAL ``run_plan`` commits a real
    DEGRADED terminal and the product carries its
    ``lane_terminal_degraded:<idx>`` badge. A DEGRADED terminal is unreachable
    via dependency policy and bridges alone (the terminal's only plan-fed
    input is BLOCK-required — a weakening FAILS the node — and the trimmed
    execution catalog is bridge-free); the model-result degraded channel
    (worker.py: ``ModelResult.degraded`` ⇒ ``NodeStatus.DEGRADED``) IS the
    production path, and it is exercised through the real runner below. The
    Task-4 producer-shape history writer keeps a supplementary badge-shape
    arm on the same batch identity.

**B-chain (落子买卖点)** — scripted fast decide → direction-flip escalation →
sealed deep preset → ``register_and_try_lease`` under an active one-admission
lease → ONE orchestrated seats row (``source:"orchestrated"``) +
``note_llm_use`` exactly once → the SAME bindings' next escalation (lease
exhausted) returns the fast result with ``deep_outcome:"no_lease"`` and a
registered pending card. Both escalations ride ONE shared ``RuntimeStores``.

Run: ``python -m pytest tests/orchestration/test_phase10_e2e.py -v``
"""
from __future__ import annotations

import ast
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

import guanlan_v2.orchestration.bootstrap as bs
import guanlan_v2.orchestration.worker as W
from guanlan_v2.orchestration import lane_payloads as lp
from guanlan_v2.orchestration import presets as P
from guanlan_v2.orchestration.adapters.launcher import (
    LaneExecutionBinding,
    LauncherError,
)
from guanlan_v2.orchestration.admission import PlanAdmissionService
from guanlan_v2.orchestration.approval import (
    ApprovalJournalRow,
    PlanApprovalCoordinator,
    admit_after_approval,
)
from guanlan_v2.orchestration.budget import BudgetLedger
from guanlan_v2.orchestration.catalog_runtime import TrustedFactoryRegistry
from guanlan_v2.orchestration.context import RunBudget, RunContext
from guanlan_v2.orchestration.data.symbols import normalize_symbol
from guanlan_v2.orchestration.digest import content_digest
from guanlan_v2.orchestration.enums import (
    ApprovalDecision,
    ApprovalPolicy,
    DependencyPolicy,
    ExecutionKind,
    NodeStatus,
    PlanSource,
    PortfolioRating,
)
from guanlan_v2.orchestration.events import EventType
from guanlan_v2.orchestration.eventstore import RuntimeStores, SchemaRegistryResolver
from guanlan_v2.orchestration.plan_diff import (
    PLAN_DIFF_SCHEMA_REF,
    build_pending_plan_approval,
    build_plan_diff,
)
from guanlan_v2.orchestration.refs import PayloadRef, SchemaRef, TypedPayloadRef
from guanlan_v2.orchestration.runtime_support import (
    STATIC_RUNTIME_PROFILE_V2,
    check_runtime_support,
)
from guanlan_v2.orchestration.schemas import (
    Confidence,
    ResearchPlan,
    SentimentBand,
    SentimentReport,
)
from guanlan_v2.orchestration.spec import (
    OrchestrationRequest,
    PlanDraft,
    PlanNode,
    compute_candidate_plan_digest,
    validate_plan_draft,
)

from guanlan_v2.orchestration.pipeline import api as api_mod
from guanlan_v2.orchestration.pipeline import escalation as escalation_mod
from guanlan_v2.orchestration.pipeline import live_decide as live_decide_mod
from guanlan_v2.orchestration.pipeline.assembly import (
    PRODUCTION_PRESETS_DIR,
    ProductionCatalogRuntime,
    build_production_plan_runner,
    load_phase10_preset_registry,
    production_bridge_view,
)
from guanlan_v2.orchestration.pipeline.candidates import (
    CandidatePorts,
    CandV4Params,
    RankingArtifact,
    RankingRow,
    build_v4_slate,
    compute_ranking_artifact_digest,
    session_date_to_utc,
)
from guanlan_v2.orchestration.pipeline.contracts import RunSubject
from guanlan_v2.orchestration.pipeline.live_decide import (
    SubjectPromptAssembler,
    make_orchestrated_decide,
)
from guanlan_v2.orchestration.pipeline.screening import (
    LANE_TERMINAL_DEGRADED_BADGE_PREFIX,
    RECOMMENDATION_ADVISORY_BANNER,
    SCREENING_LANE_PRESET_ID,
    SCREENING_LANE_TERMINAL_NODE_ID,
    BaseRequestRefused,
    build_recommendation_slate,
    build_screening_batch,
    land_recommendation,
    recommendation_archive_id,
    render_recommendation_md,
)

# ---- reviewed sibling harnesses (the Task-8 replay-evidence precedent) ------ #
# Imported, never re-derived, so this suite cannot drift from the per-task
# suites on what "the sealed catalog / the trimmed execution stand-in" means.
from tests.orchestration.test_pipeline_live_decide import (  # noqa: F401
    GOOD_CRED,
    _FixedClock,
    _Verifier,
    _OPT_IN_STRAT,
    _bindings,
    _build_env,
    _decide_rows,
    _fast_result,
    _make_fast,
    _order_rows,
    _payload,
    _seed_flip,
    heavy,
)
from tests.orchestration.test_pipeline_screening import (  # noqa: F401
    _RunHistory,
    _fresh_run_store,
    env,
    phase9_runtime,
    trimmed_catalog,
)

UTC = timezone.utc
#: the A-chain frozen clock (2026-07-24 07:00Z == 15:00 +08:00 session date),
#: the same instant the screening suite's ``env`` context is dated at.
SCREEN_NOW = datetime(2026, 7, 24, 7, 0, tzinfo=UTC)
#: the three fixture codes, in ranking order.
CODES = ("600519", "000001", "300750")
#: per-lane scripted verdicts — lane 0 gets the WORSE grade on purpose so the
#: recommendation sort has to reorder the slate, and its verdict model comes
#: back ``degraded=True`` (the production degraded-terminal channel); lane 2's
#: sentiment fails.
LANE_RATINGS = {0: PortfolioRating.HOLD, 1: PortfolioRating.BUY}
LANE_RATIONALE = {0: "旺季验证前观望。", 1: "订单能见度延伸,产能兑现。"}

#: the nine pipeline modules (the Phase-10 chain's own roster; the sweep below
#: walks each one).
_PIPELINE_DIR = Path("guanlan_v2/orchestration/pipeline")
_PIPELINE_MODULES = (
    "api", "assembly", "candidates", "chain", "contracts", "deep_decide",
    "escalation", "live_decide", "screening",
)

#: independent verbatim copy of the Task-5 frozen escalation-constants digest
#: (the red-line re-pin; the Task-5 suite carries the vocabulary itself).
_ESCALATION_DIGEST_GOLDEN = (
    "b0a10407144d48cbbd43840e5615a51f59fa57ddef301b2c6d5af7c6b302392d"
)

#: plan-side/admission evidence schemas the code-free sweep covers (worker
#: OUTPUT artifacts excluded — scripted payloads legitimately carry symbols;
#: ``RunSubject@1`` is the ONE sanctioned carrier).
_PLAN_SIDE_SCHEMAS = (
    "Plan@1", "AdmissionCandidate@1", "PlanValidationReport@1",
    "RuntimeSupportReport@1", "PlanAdmitted@1", "PlanApproval@1",
    "PromptAssemblyRecord@1",
)


# =========================================================================== #
# A-chain doubles                                                              #
# =========================================================================== #
class _ReadOnlyRankingSurface:
    """The fixture ranking read surface — deliberately exposes ONLY ``read_ranking``.

    Any other attribute access (a write verb, a mutation helper) raises
    ``AttributeError`` at the call site, so "the ranking surface is read-only"
    is enforced by the object shape, not merely asserted about the source.
    """

    def __init__(self, artifact: RankingArtifact) -> None:
        self._artifact = artifact
        self.reads = 0

    def read_ranking(self, *, variant_id, as_of_hint) -> RankingArtifact:
        assert variant_id is None  # cand.v4 reads the production artifact only
        self.reads += 1
        return self._artifact


def _fixture_ranking() -> RankingArtifact:
    rows = tuple(
        RankingRow(code=code, rank=i + 1, score=9.0 - i)
        for i, code in enumerate(CODES))
    return RankingArtifact(
        as_of=session_date_to_utc("2026-07-24"),
        artifact_digest=compute_ranking_artifact_digest(
            rows, model_id=None, as_of_date="2026-07-24"),
        rows=rows)


def _screen_llm_payload(worker_id: str, *, code: str, rating: PortfolioRating,
                        rationale: str):
    sym = normalize_symbol(code)
    if worker_id == "text.news":
        return lp.NewsDigestReport(as_of=SCREEN_NOW, scope="market", items=(),
                                   coverage_note="scripted: feeds quiet")
    if worker_id == "text.research_report":
        return lp.ResearchReportExtract(
            symbol=sym, as_of=SCREEN_NOW, source_report_label="scripted-report",
            report_age_days=3, staleness_downweight=0.8, claims=())
    if worker_id == "text.sentiment":
        return SentimentReport(
            overall_band=SentimentBand.NEUTRAL, overall_score=5.0,
            confidence=Confidence.MEDIUM, narrative="Balanced flow, mild caution.")
    if worker_id == "dec.bull":
        return lp.BullCase(symbol=sym, as_of=SCREEN_NOW,
                           thesis_bullets=("scripted bull",))
    if worker_id == "dec.bear":
        return lp.BearCase(symbol=sym, as_of=SCREEN_NOW,
                           thesis_bullets=("scripted bear",))
    if worker_id == "dec.research_mgr":
        return ResearchPlan(recommendation=rating, rationale=rationale,
                            strategic_actions=("分批建仓", "跌破前低离场"))
    raise AssertionError(f"unexpected LLM worker {worker_id!r}")


#: the scripted model-side degradation reason lane 0's verdict carries — the
#: production degraded channel (worker.py: ``ModelResult.degraded`` →
#: ``NodeStatus.DEGRADED`` on the committed terminal).
DEGRADED_VERDICT_REASON = "scripted: 证据面收窄,研判在缺证下成verdict"


class _ScreenGateway:
    """Scripted trusted single-shot gateway for the screening lane workers."""

    def __init__(self, payload_reader, *, code, rating, rationale, fail, counter,
                 degrade=frozenset()):
        self._reader = payload_reader
        self._code = code
        self._rating = rating
        self._rationale = rationale
        self._fail = fail
        self._degrade = degrade
        self._counter = counter
        self.records: list = []

    def invoke(self, request, *, prompt_assembly_ref):
        record = W.verify_model_request_binding(
            request, prompt_assembly_ref, reader=self._reader)
        self.records.append(record)
        self._counter.append((self._code, record.worker_id))
        if record.worker_id in self._fail:
            return W.ModelResult(payload={"schema_version": "1"}, rendered_text="bad",
                                 input_tokens=3, output_tokens=1,
                                 provider="scripted", model="scripted")
        degraded = record.worker_id in self._degrade
        return W.ModelResult(
            payload=_screen_llm_payload(
                record.worker_id, code=self._code, rating=self._rating,
                rationale=self._rationale),
            rendered_text=f"scripted:{record.worker_id}", input_tokens=7,
            output_tokens=5, provider="scripted", model="scripted",
            degraded=degraded,
            degradation_reasons=((DEGRADED_VERDICT_REASON,) if degraded else ()))


class _ScreenGatewayFactory:
    def __init__(self, *, code, rating, rationale, fail, counter,
                 degrade=frozenset()):
        self._kw = dict(code=code, rating=rating, rationale=rationale, fail=fail,
                        counter=counter, degrade=degrade)
        self.gateways: list[_ScreenGateway] = []

    def __call__(self, *, payload_reader, catalog_runtime):
        gw = _ScreenGateway(payload_reader, **self._kw)
        self.gateways.append(gw)
        return gw


def _det_handler_factory(payload_builder):
    def factory(worker, resolved):
        def handler(*, node, input_snapshot, contributions, data_result_refs):
            return W.ModelResult(payload=payload_builder(), rendered_text="det",
                                 input_tokens=0, output_tokens=1)
        return handler
    return factory


def _pa_payload(code: str):
    keys = lp.PA_FEATURE_SET_KEYS["pa-15key-v1"]
    return lp.PriceActionFeatureReport(
        symbol=normalize_symbol(code), as_of=SCREEN_NOW,
        feature_set_version="pa-15key-v1",
        features={k: float(i) for i, k in enumerate(keys)})


class _LaneAdmissionDispatcher:
    """Per-candidate admission routing under ONE long-lived coordinator.

    The Task-8 replay-evidence wiring fact, applied to the screening batch:
    ``PlanAdmissionService.record_approval`` requires the RESERVING instance and
    lane execution requires ``plan.run_id == admission run_id`` (budget events
    are keyed by run identity) — so each lane owns a service and this dispatcher
    forwards by draft/candidate identity. It forwards; it never decides.
    """

    def __init__(self) -> None:
        self._by_draft: dict[str, PlanAdmissionService] = {}
        self._by_digest: dict[str, PlanAdmissionService] = {}
        self._approvals: dict[str, dict] = {}

    def bind(self, *, draft_id, digest, service, approvals) -> None:
        self._by_draft[draft_id] = service
        self._by_digest[digest] = service
        self._approvals[digest] = approvals

    def prepare_candidate(self, draft_id, *, request_id):
        return self._by_draft[draft_id].prepare_candidate(
            draft_id, request_id=request_id)

    def persist_and_reserve_candidate(self, prep, *, idempotency_key):
        return self._by_draft[prep.draft.id].persist_and_reserve_candidate(
            prep, idempotency_key=idempotency_key)

    def record_approval(self, candidate_id, approval_input, *,
                        authenticated_actor, idempotency_key):
        return self._by_digest[candidate_id].record_approval(
            candidate_id, approval_input,
            authenticated_actor=authenticated_actor,
            idempotency_key=idempotency_key)

    def approvals_sink(self, approval) -> None:
        self._approvals[approval.candidate_plan_digest][
            (approval.request_id, approval.candidate_plan_digest)] = approval


class _ArchiveSpy:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str]] = []

    def __call__(self, artifact_id, artifact_type, markdown) -> None:
        self.calls.append((artifact_id, artifact_type, markdown))


class _Point:
    point_ordinal = 1


def _journal_kinds(path: Path) -> list[str]:
    if not path.exists():
        return []
    return [ApprovalJournalRow.model_validate_json(ln).row_kind
            for ln in path.read_bytes().decode("utf-8").split("\n") if ln]


# =========================================================================== #
# Scenario 1 — the A-chain, end to end, once (module-scoped)                    #
# =========================================================================== #
@pytest.fixture(scope="module")
def a_chain(env, trimmed_catalog, tmp_path_factory):
    """ranking → slate → batch → ONE-lease admission → run_plan × 3 → product."""
    snapshot, runtime, view = trimmed_catalog
    registry = env["registry"]
    rd = registry.registry_digest
    clock = _FixedClock(SCREEN_NOW)
    journal = tmp_path_factory.mktemp("phase10-e2e-a") / "plan_approvals.jsonl"

    # ── 1. fixture ranking → cand.v4 slate (read-only surface) ─────────────── #
    reader = _ReadOnlyRankingSurface(_fixture_ranking())
    ranking_digest_before = reader._artifact.semantic_digest()
    slate = build_v4_slate(
        params=CandV4Params.from_mapping({"top_n": 3}),
        ports=CandidatePorts(as_of=SCREEN_NOW, ranking_reader=reader))

    # ── 2. ONE shared RuntimeStores for the WHOLE chain (Task-8b shape) ────── #
    resolver = SchemaRegistryResolver()
    resolver.register(registry)
    stores = RuntimeStores(resolver=resolver, clock=clock,
                           allowed_cell_namespaces=(W.PROMPT_CELL_NAMESPACE,))
    context = P.build_empty_memory_context(
        data_context=P.pilot_data_context(as_of=SCREEN_NOW), stores=stores,
        registry_digest=rd, built_at=SCREEN_NOW).context
    ctx_ref = PayloadRef(namespace="main", object_id="ctx-e2e-a",
                         content_digest=context.content_digest)

    # real committed inputs: the slate + one RunSubject@1 per code.
    slate_ref = api_mod._commit_candidate_slate(stores, slate)
    committer = api_mod.build_stores_subject_committer(stores)

    base_request = OrchestrationRequest(
        request_id="e2e-screen-req", goal="观澜 · e2e 选股批量研判",
        workflow="orchestrate_only",
        fallback_preset_id=SCREENING_LANE_PRESET_ID,
        approval_policy=ApprovalPolicy.REQUIRED)
    presets = load_phase10_preset_registry(PRODUCTION_PRESETS_DIR)
    build = build_screening_batch(
        slate, preset_registry=presets, clock=clock, base_request=base_request,
        slate_ref=slate_ref, subject_committer=committer, context=context,
        context_snapshot_ref=ctx_ref, catalog=snapshot, schema_registry=registry)
    batch = build.batch
    preview = batch.cost_preview

    # ── 3. per-lane REAL admission services under ONE coordinator ──────────── #
    dispatcher = _LaneAdmissionDispatcher()
    services: list[PlanAdmissionService] = []
    budgets: list[RunBudget] = []
    for lane in build.lanes:
        run_budget = RunBudget(
            ledger_id=f"led-{lane.draft.run_id}",
            max_tokens=lane.draft.budget_request_tokens,
            max_llm_invocations=lane.draft.budget_request_llm_invocations,
            max_concurrency=lane.draft.max_concurrency)
        approvals: dict = {}
        service = PlanAdmissionService(
            run_id=lane.draft.run_id,
            requests={lane.request.request_id: lane.request},
            drafts={lane.draft.id: lane.draft},
            contexts={context.content_digest: context}, attestations={},
            approvals=approvals, catalog=runtime, bridge_view=view,
            phase1_registry=registry, runtime_registry_digest=rd,
            profile=STATIC_RUNTIME_PROFILE_V2, stores=stores,
            run_budget=run_budget, clock=clock)
        dispatcher.bind(draft_id=lane.draft.id, digest=lane.candidate_plan_digest,
                        service=service, approvals=approvals)
        services.append(service)
        budgets.append(run_budget)

    coordinator = PlanApprovalCoordinator(
        journal, admission=dispatcher, clock=clock, verifier=_Verifier(),
        console_emit=None, approvals_sink=dispatcher.approvals_sink,
        preset_registry=presets, catalog_digest=snapshot.catalog_digest,
        registry_digest=rd)

    # ── 4. THE one human act: the lease whose envelope IS the cost preview ─── #
    lease = coordinator.issue_lease(
        purpose="e2e 选股批量 · whole-picture screening lease",
        preset_id=SCREENING_LANE_PRESET_ID,
        preset_record_digest=batch.preset_record_digest,
        catalog_digest=snapshot.catalog_digest, registry_digest=rd,
        valid_from=SCREEN_NOW - timedelta(days=1),
        valid_until=SCREEN_NOW + timedelta(days=1),
        max_admissions=preview.n_codes,
        budget_cap_llm_invocations=preview.total_budget_llm_invocations,
        actor=GOOD_CRED,
        reason=f"已审阅整体成本预览 digest={preview.semantic_digest()};一次审批覆盖整批")

    # the caller-carried whole-picture pre-flight ledger (the Task-3 ABI).
    batch_budget = RunBudget(
        ledger_id="led-e2e-batch", max_tokens=preview.total_budget_tokens,
        max_llm_invocations=preview.total_budget_llm_invocations,
        max_concurrency=preview.n_codes * preview.per_lane_max_concurrency)
    ledger = BudgetLedger(
        sink=stores.budget_event_sink(run_id="e2e-batch-preflight",
                                      ledger_id=batch_budget.ledger_id),
        run_budget=batch_budget)

    from guanlan_v2.orchestration.pipeline.screening import admit_screening_batch
    outcomes = admit_screening_batch(
        batch, coordinator=coordinator, admission=dispatcher, now=SCREEN_NOW,
        payloads=stores.payloads, registry_digest=rd, budget=ledger)

    # ── 5. run every lane through the production runner (real dag.run_plan) ── #
    by_id = {w.id: w for w in snapshot.workers}
    llm_counter: list[tuple[str, str]] = []
    preps, plans, artifacts, gateway_factories = [], {}, {}, {}
    lane2_error = None
    for i, lane in enumerate(build.lanes):
        service = services[i]
        prep = service.prepare_candidate(lane.draft.id,
                                         request_id=lane.request.request_id)
        preps.append(prep)
        _candidate, reservation = service.persist_and_reserve_candidate(
            prep, idempotency_key=f"screening-reserve:{batch.batch_id}:{lane.code}")
        plan, _admitted = admit_after_approval(
            admission=service, candidate_id=lane.candidate_plan_digest,
            reservation_id=reservation.reservation_id,
            approval_event_id=outcomes[i].event.event_id,
            idempotency_key=f"e2e-freeze:{lane.code}")
        plans[i] = plan

        factories = TrustedFactoryRegistry(runtime)
        factories.register_handler(
            by_id["quant.factor"].execution.handler_ref,
            _det_handler_factory(lambda: lp.FactorICReport(as_of=SCREEN_NOW,
                                                           rows=())))
        factories.register_handler(
            by_id["quant.backtest"].execution.handler_ref,
            _det_handler_factory(lambda: lp.BacktestEvidenceReport(
                as_of=SCREEN_NOW, subject="scripted-universe", vintage_ic=None,
                oos_verdict=None, pbo=None,
                caveats=("scripted: evidence stood down",))))
        factories.register_handler(
            by_id["pv.price_action"].execution.handler_ref,
            _det_handler_factory(lambda code=lane.code: _pa_payload(code)))
        bundle = ProductionCatalogRuntime(runtime=runtime, factories=factories)
        gateway_factory = _ScreenGatewayFactory(
            code=lane.code, rating=LANE_RATINGS.get(i, PortfolioRating.HOLD),
            rationale=LANE_RATIONALE.get(i, "scripted"),
            fail=(frozenset({"text.sentiment"}) if i == 2 else frozenset()),
            # lane 0: the PRODUCTION degradation channel — the verdict model
            # returns ``degraded=True`` ⇒ the real run_plan commits a real
            # DEGRADED terminal (worker.py's model-result degraded channel).
            degrade=(frozenset({"dec.research_mgr"}) if i == 0 else frozenset()),
            counter=llm_counter)
        gateway_factories[i] = gateway_factory

        subject = RunSubject(code=lane.code, as_of=slate.as_of)
        assembler = SubjectPromptAssembler(subject=subject,
                                           subject_ref=lane.subject_ref)
        binding = LaneExecutionBinding(
            lane="main", candidate_plan_digest=lane.candidate_plan_digest,
            reservation_id=reservation.reservation_id,
            approval_event_id=outcomes[i].event.event_id)
        run_budget = budgets[i]

        def _run_context_factory(*, lane, point, plan, data_context,
                                 memory_binding, _rb=run_budget,
                                 _code=build.lanes[i].code):
            return RunContext(
                run_id=plan.run_id, data=data_context,
                context_snapshot_id=context.snapshot_id,
                memory_snapshot_hash=context.memory_snapshot_hash,
                budget=_rb, cancellation_token_id=f"cancel-{_code}")

        runner = build_production_plan_runner(
            stores=stores, catalog_snapshot=snapshot, registry=registry,
            gateway_factory=gateway_factory, admission=service,
            lane_bindings={"main": binding},
            run_context_factory=_run_context_factory,
            request_id=lane.request.request_id, clock=clock,
            runtime_registry_digest=rd, runtime_limit=4, catalog=bundle,
            prompt_assembler=assembler,
            # L1 Task 4 (plan R4): the screening lane's ONLY runner invocation
            # today — the lane's materialization-stamped projection rides the
            # per-run seam (vacuous behaviorally: zero screening workers carry
            # data prefetch rows; source-text-pinned in test_pipeline_screening).
            subject_params=lane.subject_params)
        try:
            artifacts[i] = runner(
                lane="main", point=_Point(), approval=None,
                data_context=context.data_context, memory_binding=None,
                candidate_plan_digest=lane.candidate_plan_digest)
        except LauncherError as exc:
            lane2_error = exc
            assert i == 2, f"only the sentiment-failing lane may refuse; lane {i}"

    # ── 6. assemble → render → land ────────────────────────────────────────── #
    rec_slate = build_recommendation_slate(batch, stores=stores)
    md = render_recommendation_md(rec_slate, stores=stores)
    spy = _ArchiveSpy()
    land_recommendation(rec_slate, stores=stores, archive_put=spy)
    land_recommendation(rec_slate, stores=stores, archive_put=spy)  # idempotent

    return SimpleNamespace(
        reader=reader, ranking_digest_before=ranking_digest_before, slate=slate,
        stores=stores, context=context, build=build, batch=batch,
        preview=preview, services=services, dispatcher=dispatcher,
        coordinator=coordinator, lease=lease, outcomes=outcomes, preps=preps,
        plans=plans, artifacts=artifacts, lane2_error=lane2_error,
        rec_slate=rec_slate, md=md, spy=spy, journal=journal,
        llm_counter=llm_counter, gateway_factories=gateway_factories,
        registry=registry, snapshot=snapshot)


class TestAChainE2E:
    def test_the_slate_is_the_fixture_rankings_top_n(self, a_chain):
        slate = a_chain.slate
        assert slate.source_kind == "v4"
        assert tuple(e.code for e in slate.entries) == CODES
        assert [e.rank for e in slate.entries] == [1, 2, 3]
        # same +08:00 session as the run clock ⇒ no staleness badge to carry.
        assert slate.badges == ()

    def test_one_human_act_covers_the_whole_batch_via_the_lease(self, a_chain):
        """The whole-picture approval: ONE ``issue_lease`` (envelope == the cost
        preview) admits all N lanes; every minted approval names that lease and
        no human ``decide`` ever ran."""
        outcomes, lease, preview = a_chain.outcomes, a_chain.lease, a_chain.preview
        assert [o.outcome for o in outcomes] == ["lease_admitted"] * 3
        assert {o.lease_id for o in outcomes} == {lease.lease_id}
        assert lease.max_admissions == preview.n_codes == 3
        assert (lease.budget_cap_llm_invocations
                == preview.total_budget_llm_invocations == 18)
        for outcome, entry in zip(outcomes, a_chain.batch.entries):
            assert outcome.approval.decision is ApprovalDecision.APPROVED
            assert outcome.approval.actor_id == f"lease:{lease.lease_id}"
            assert outcome.approval.candidate_plan_digest == (
                entry.candidate_plan_digest)
        # journal shape: one lease issuance, then per lane pending → consume →
        # decision. NO human decision row exists anywhere.
        assert _journal_kinds(a_chain.journal) == (
            ["lease_issued"] + ["pending", "lease_consumed", "decision"] * 3)

    def test_phase1_validation_really_ran_inside_admission(self, a_chain):
        for prep, entry in zip(a_chain.preps, a_chain.batch.entries):
            assert prep.phase1_report.valid is True, [
                (i.code, i.node_id) for i in prep.phase1_report.issues]
            assert prep.support_report.supported is True
            assert prep.candidate_plan_digest == entry.candidate_plan_digest

    def test_the_two_verdict_lanes_ran_to_committed_research_plans(self, a_chain):
        for i in (0, 1):
            lane = a_chain.build.lanes[i]
            kinds = [e.event_type
                     for e in a_chain.stores.events.journal(
                         lane.draft.run_id, "main")]
            assert EventType.PLAN_FROZEN in kinds
            assert EventType.RUN_COMPLETED in kinds
            artifact = a_chain.artifacts[i]
            assert artifact.payload_schema_ref == SchemaRef(
                name="ResearchPlan", version="1")
            assert artifact.run_id == lane.draft.run_id
            assert a_chain.plans[i].plan_digest == lane.candidate_plan_digest

    def test_the_failing_lane_refuses_loudly_and_commits_no_verdict(self, a_chain):
        """Lane 2's ``text.sentiment`` fails ⇒ the BLOCK-fed terminal cannot run
        ⇒ the runner REFUSES (typed) instead of synthesizing an artifact, and the
        journal records the honest failure."""
        assert isinstance(a_chain.lane2_error, LauncherError)
        assert 2 not in a_chain.artifacts
        lane = a_chain.build.lanes[2]
        kinds = [e.event_type
                 for e in a_chain.stores.events.journal(lane.draft.run_id, "main")]
        assert EventType.PLAN_FROZEN in kinds  # the run genuinely started
        assert EventType.RUN_COMPLETED not in kinds

    def test_the_recommendation_copies_ratings_verbatim_and_sorts(self, a_chain):
        rec = a_chain.rec_slate
        assert {e.code for e in rec.entries} == {CODES[0], CODES[1]}
        assert rec.degraded_lanes == (2,)
        # grade order: the Buy lane (slate rank 2) leads; lane_index survives.
        assert [e.rating for e in rec.entries] == ["Buy", "Hold"]
        assert [e.lane_index for e in rec.entries] == [1, 0]
        # verbatim from the committed plan payloads this run really produced:
        for entry in rec.entries:
            plan = a_chain.stores.payloads.get(
                entry.research_plan_ref.payload_ref,
                expected_schema_ref=entry.research_plan_ref.schema_ref)
            assert entry.rating == plan.recommendation.value
            assert plan.rationale == LANE_RATIONALE[entry.lane_index]

    def test_the_md_is_banner_first_and_names_the_degraded_lane(self, a_chain):
        assert a_chain.md.splitlines()[0] == RECOMMENDATION_ADVISORY_BANNER
        assert "lane_index 2" in a_chain.md
        assert a_chain.batch.batch_id in a_chain.md
        for lane_index in LANE_RATINGS:
            assert LANE_RATIONALE[lane_index] in a_chain.md

    def test_landing_is_idempotent_per_batch_and_day_scoped(self, a_chain):
        calls = a_chain.spy.calls
        assert len(calls) == 2 and calls[0] == calls[1]
        artifact_id, artifact_type, markdown = calls[0]
        assert artifact_id == "rs_orch_screen_20260724"
        assert artifact_id == recommendation_archive_id(a_chain.rec_slate)
        assert artifact_type == "research"
        assert markdown == a_chain.md

    def test_no_v4_artifact_write(self, a_chain):
        """Read-only ranking surface: exactly one read, byte-identical artifact,
        and the surface object never even EXPOSES a write verb (any non-read
        attribute access would have raised at the call site)."""
        assert a_chain.reader.reads == 1
        assert (a_chain.reader._artifact.semantic_digest()
                == a_chain.ranking_digest_before)
        public = {n for n in dir(_ReadOnlyRankingSurface)
                  if not n.startswith("_")}
        assert public == {"read_ranking"}

    def test_all_three_lane_runs_shared_one_runtime_stores(self, a_chain):
        """The Task-8b de-forced shape, exercised across THREE runs of ONE
        sealed preset (pinned node ids) on ONE store: per-run distinct
        run-scoped prompt recovery cells, no cross-run recovery."""
        node_id = "sentiment"  # an LLM node id every lane's plan pins
        refs = {}
        for i in (0, 1):
            run_id = a_chain.build.lanes[i].draft.run_id
            key = content_digest({"run_id": run_id, "node_id": node_id,
                                  "attempt": 1, "kind": "runtime.prompt"})
            ref = a_chain.stores.cells.load(W.PROMPT_CELL_NAMESPACE, key)
            assert ref is not None, run_id
            refs[i] = ref.payload_ref.content_digest
        assert refs[0] != refs[1]

    def test_the_subject_is_the_only_code_carrier_in_plan_side_evidence(
            self, a_chain):
        stored_by_schema: dict[str, list] = {}
        for stored in dict(a_chain.stores._shared.backend.payloads).values():
            stored_by_schema.setdefault(stored.schema_key, []).append(stored)
        assert stored_by_schema.get("Plan@1")
        assert len(stored_by_schema.get("RunSubject@1", ())) == 3
        # the reviewed screening rule (screening.py module docstring): the code
        # lives in the run IDENTITY (request_id / draft_id / run_id) so a human
        # can tell N lanes apart — and in NO executable plan field. The sweep
        # therefore strips the sanctioned identity strings first; any six-digit
        # run that SURVIVES is a leak into an executable/evidence field.
        identity_strings = sorted(
            {s for lane in a_chain.build.lanes
             for s in (lane.draft.id, lane.draft.run_id,
                       lane.request.request_id)},
            key=len, reverse=True)
        digest_re = re.compile(r"[0-9a-f]{32,}")
        code_re = re.compile(r"(?<!\d)\d{6}(?!\d)")
        for schema_key in _PLAN_SIDE_SCHEMAS:
            for stored in stored_by_schema.get(schema_key, ()):
                blob = stored.model.model_dump_json()
                for identity in identity_strings:
                    blob = blob.replace(identity, "<lane-identity>")
                blob = digest_re.sub('""', blob)
                assert code_re.search(blob) is None, (schema_key, blob)
        # …and every prompt record cites its lane's committed subject (E2b).
        for i, factory in a_chain.gateway_factories.items():
            lane = a_chain.build.lanes[i]
            for gw in factory.gateways:
                for record in gw.records:
                    named = [e for e in record.trusted_input_digests
                             if e.name == live_decide_mod.SUBJECT_TRUSTED_INPUT_NAME]
                    assert [e.digest for e in named] == [
                        lane.subject_ref.payload_ref.content_digest]

    def test_a_real_degraded_terminal_from_run_plan_badges_the_product(
            self, a_chain):
        """The PRODUCTION degraded channel, end to end: lane 0's verdict model
        returned ``degraded=True`` (worker.py: ``ModelResult.degraded`` ⇒
        ``NodeStatus.DEGRADED``), the REAL ``dag.run_plan`` committed the
        DEGRADED terminal, and ``build_recommendation_slate`` carries the
        ``lane_terminal_degraded:0`` badge onto the product and into the
        rendered md — banner still first. The badge is DISTINCT from
        ``degraded_lanes`` (no plan at all): lane 0's verdict is real, present
        and verbatim, just honestly marked."""
        # the terminal NodeRun the real run committed is DEGRADED with the
        # scripted model-side reason (not a history-writer artifact).
        run_id = a_chain.build.lanes[0].draft.run_id
        node_run_ref = SchemaRef(name="NodeRun", version="1")
        terminal_records = [
            a_chain.stores.payloads.get(e.payload_ref,
                                        expected_schema_ref=node_run_ref)
            for e in a_chain.stores.events.journal(run_id, "main")
            if e.event_type is EventType.NODE_STATE_CHANGED
            and e.payload_schema_ref == node_run_ref]
        terminal = [r for r in terminal_records
                    if r.node_id == SCREENING_LANE_TERMINAL_NODE_ID]
        assert terminal, "the terminal node left no durable NodeRun"
        assert terminal[-1].status is NodeStatus.DEGRADED
        assert DEGRADED_VERDICT_REASON in (terminal[-1].reason or "")
        # …and the badge flows through the assembler onto the product:
        badge = f"{LANE_TERMINAL_DEGRADED_BADGE_PREFIX}0"
        assert badge in a_chain.rec_slate.badges
        # per-lane discrimination: the clean COMPLETED lane is NOT badged
        # (a blanket-badging assembler bug would redden here).
        assert f"{LANE_TERMINAL_DEGRADED_BADGE_PREFIX}1" not in a_chain.rec_slate.badges
        assert a_chain.rec_slate.degraded_lanes == (2,)  # badge ≠ no-plan
        assert badge in a_chain.md
        assert a_chain.md.splitlines()[0] == RECOMMENDATION_ADVISORY_BANNER

    def test_the_lane_terminal_degraded_badge_shape_via_the_history_writer(
            self, a_chain, env):
        """Supplementary SHAPE arm through the Task-4 reviewed producer-shape
        history writer, kept for the composition the live chain does not
        produce: ONLY lane 0 committed ⇒ ``entries == [0]`` beside
        ``degraded_lanes == (1, 2)``. The production channel itself — a real
        ``run_plan``-committed DEGRADED terminal — is exercised by the test
        above; a DEGRADED terminal is unreachable via dependency policy and
        bridges alone, and the model-result degraded channel IS the
        production path.
        """
        stores_b, writer = _fresh_run_store(env)
        writer.commit_terminal_plan(
            a_chain.build.lanes[0].draft.run_id,
            ResearchPlan(recommendation=PortfolioRating.BUY,
                         rationale="缺一路证据仍成verdict"),
            status=NodeStatus.DEGRADED)
        rec = build_recommendation_slate(a_chain.batch, stores=stores_b)
        assert [e.lane_index for e in rec.entries] == [0]
        assert f"{LANE_TERMINAL_DEGRADED_BADGE_PREFIX}0" in rec.badges
        assert rec.degraded_lanes == (1, 2)
        assert f"{LANE_TERMINAL_DEGRADED_BADGE_PREFIX}0" in (
            render_recommendation_md(rec, stores=stores_b))

    def test_execution_spent_only_scripted_llm_calls(self, a_chain):
        """6 LLM nodes per verdict lane; the failing lane stops at its dead
        sentiment node (news/research-report ran, the debate + judge never
        did). Every invocation is a screening worker."""
        by_lane: dict[str, list[str]] = {}
        for code, worker_id in a_chain.llm_counter:
            by_lane.setdefault(code, []).append(worker_id)
        assert sorted(by_lane[CODES[0]]) == sorted(by_lane[CODES[1]])
        assert len(by_lane[CODES[0]]) == 6
        # the failing lane: sentiment died, so the debate seats ran DEGRADED
        # (their sentiment dependency is DEGRADE) but the BLOCK-fed judge —
        # the verdict — never invoked a model.
        assert set(by_lane[CODES[2]]) <= {
            "text.news", "text.research_report", "text.sentiment",
            "dec.bull", "dec.bear"}
        assert "dec.research_mgr" not in by_lane[CODES[2]]


# =========================================================================== #
# Scenario 2 — the B-chain, two escalations on ONE env (lease of ONE)           #
# =========================================================================== #
@pytest.fixture()
def b_chain(tmp_path, heavy):
    env_ = _build_env(tmp_path, heavy, lease_max_admissions=1)
    # flip-only escalation (no opt-in strat): the charter's trigger, and the
    # re-tick below re-fires the SAME identity by re-seeding the flip.
    bindings = _bindings(env_, heavy)

    _seed_flip(env_, "600519", prev_direction="卖出")
    decide_a = make_orchestrated_decide(
        fast_decide=_make_fast(env_, _fast_result("600519", direction="买入")),
        bindings=bindings)
    first = decide_a(_payload("600519"))

    _seed_flip(env_, "300750", prev_direction="卖出")
    fast_b = _fast_result("300750", direction="买入")
    decide_b = make_orchestrated_decide(
        fast_decide=_make_fast(env_, fast_b), bindings=bindings)
    second = decide_b(_payload("300750"))

    return SimpleNamespace(env=env_, heavy=heavy, first=first, second=second,
                           fast_b=fast_b, decide_a=decide_a)


class TestBChainE2E:
    def test_the_first_escalation_completes_through_the_lease(self, b_chain):
        first = b_chain.first
        assert first["ok"] is True
        assert first["deep_attempted"] is True
        assert first["deep_outcome"] == "completed"
        assert first["source"] == "orchestrated"
        kinds = [e.event_type
                 for e in b_chain.env.stores.events.journal(first["run_id"], "main")]
        assert EventType.PLAN_FROZEN in kinds
        assert EventType.RUN_COMPLETED in kinds

    def test_exactly_one_orchestrated_row_and_one_llm_note(self, b_chain):
        rows = _decide_rows(b_chain.env)
        assert len(rows) == 1
        assert rows[0]["source"] == "orchestrated"
        assert rows[0]["run_id"] == b_chain.first["run_id"]
        assert rows[0]["code"] == "600519"
        assert b_chain.env.noted == [8]  # note_llm_use exactly once, folded spend

    def test_lease_exhaustion_returns_fast_with_no_lease(self, b_chain):
        """The one-admission lease is spent; the SECOND escalation still runs
        the fast chain untouched and reports the honest ``no_lease``."""
        second = b_chain.second
        assert second.get("deep_attempted") is True
        assert second.get("deep_outcome") == "no_lease"
        for key, value in b_chain.fast_b.items():
            assert second[key] == value
        # no second orchestrated row, no second note.
        assert len(_decide_rows(b_chain.env)) == 1
        assert b_chain.env.noted == [8]
        # the candidate stayed a REAL pending card for a human.
        reader = PlanApprovalCoordinator(
            b_chain.env.journal, admission=SimpleNamespace(),
            clock=b_chain.env.clock, verifier=None, console_emit=None)
        pending = reader.list_pending()
        assert len(pending) == 1
        assert pending[0].source is PlanSource.PRESET_FALLBACK

    def test_both_escalations_rode_one_shared_runtime_stores(self, b_chain):
        """The Task-8b posture on the live lane: one store, two subjects, the
        completed run's events beside the refused candidate's evidence."""
        stored = dict(b_chain.env.stores._shared.backend.payloads).values()
        subjects = [s for s in stored if s.schema_key == "RunSubject@1"]
        codes = {json_blob for s in subjects
                 for json_blob in re.findall(r'"(\d{6})"',
                                             s.model.model_dump_json())}
        assert codes == {"600519", "300750"}

    def test_a_re_tick_replays_the_receipt_instead_of_re_running(self, b_chain):
        # the same escalation identity re-fires (a fresh 卖出 row re-arms the
        # flip; same code, same +08:00 session, same trigger-kind set) — and
        # the prior orchestrated row in the pre-captured tail IS the receipt.
        _seed_flip(b_chain.env, "600519", prev_direction="卖出")
        again = b_chain.decide_a(_payload("600519"))
        assert again.get("deep_outcome") == "completed"
        assert again.get("deep_replayed") is True
        assert again.get("run_id") == b_chain.first["run_id"]
        assert len(_decide_rows(b_chain.env)) == 1
        assert b_chain.env.noted == [8]


# =========================================================================== #
# Scenario 6 — deep-failure retry bound (document-and-pin, Task-7 roll-up)      #
# =========================================================================== #
class TestDeepFailureRetryBound:
    def test_persistent_deep_failure_is_bounded_and_never_blocks_fast(
            self, tmp_path, heavy):
        """No receipt ⇒ every tick re-escalates; pinned AS OBSERVED (not fixed).

        Tick 1 really executes the deep run (``dec.pm`` fails; the other LLM
        nodes' spend settles and is noted). Ticks 2+ re-derive the SAME run id
        and re-enter the runner — where the composed runner's per-plan
        ``ArtifactPool.replay()`` resume precondition REFUSES ("committed
        artifact ... has no persisted envelope": Task 7's recorded fact #4,
        kernel re-execution does not cleanly resume through the composed
        runner) BEFORE any model call. So the CURRENT honest bound is:

        * the retry storm costs ZERO further LLM invocations after tick 1
          (the loud typed resume refusal precedes the gateway);
        * every tick returns the fast result with an honest ``failed`` —
          the fast lane is never blocked and never silenced;
        * RESIDUE (documented, deliberately not fixed here — the policy fix
          rides the post-P10 plate with the durable failure receipt): each
          re-tick re-folds the run's settled ledger and RE-NOTES tick 1's
          spend against the watcher's 24/day pool — note-once is per wrapper
          call, not per run, so a persistent same-identity failure over-counts
          the external-LLM pool by one fold per tick.
        """
        env_ = _build_env(tmp_path, heavy, fail=frozenset({"dec.pm"}))
        # opt-in only: every tick re-derives the SAME escalation identity
        # (code + session + {opt_in}), so the storm is the same-run retry the
        # Task-7 roll-up describes, not a parade of fresh identities.
        fast_result = _fast_result("300750")
        decide = make_orchestrated_decide(
            fast_decide=_make_fast(env_, fast_result),
            bindings=_bindings(env_, heavy,
                               strat_fn=lambda sid: dict(_OPT_IN_STRAT)))

        first = decide(_payload("300750"))
        invocations_after_tick1 = sum(
            len(g.records) for g in env_.gateway_factory.gateways)
        later = [decide(_payload("300750")) for _ in range(3)]

        for out in [first] + later:
            assert out.get("deep_attempted") is True
            assert out.get("deep_outcome") == "failed"
            for key, value in fast_result.items():
                assert out[key] == value
        # the fast path really ran on EVERY tick (never starved into silence).
        assert env_.calls.count("fast") == 4
        # no orchestrated row, no advisory order — a failed run lands nothing.
        assert _decide_rows(env_) == [] and _order_rows(env_) == []
        # the bound: ALL model spend happened on tick 1; the re-ticks' loud
        # resume refusal precedes any gateway call.
        total_invocations = sum(
            len(g.records) for g in env_.gateway_factory.gateways)
        assert 0 < invocations_after_tick1 <= 9  # 8 LLM nodes (+ bounded repair)
        assert total_invocations == invocations_after_tick1
        # the documented residue: one identical re-note per tick (the settled
        # fold of tick 1's spend), never a growing figure. If this reddens with
        # len == 1, the note became per-run — retire the residue note.
        assert len(env_.noted) == 4
        assert len(set(env_.noted)) == 1 and 0 < env_.noted[0] <= 8


# =========================================================================== #
# Scenario 4 — late-failure loudness (Task-11 review minor #1, pinned)          #
# =========================================================================== #
def _late_failure_draft(env) -> tuple[OrchestrationRequest, PlanDraft]:
    """A DYNAMIC planner-shaped plan: ``text.sentiment → dec.research_mgr`` —
    deliberately WITHOUT ``pv.technical``/``text.news`` (the two honest support
    refusals), so admission support passes while the experience-bridge rowless
    posture rides into execution."""
    request = OrchestrationRequest(
        request_id="e2e-late-req", goal="观澜 · e2e late-failure probe",
        workflow="orchestrate_only", fallback_preset_id=None,
        approval_policy=ApprovalPolicy.REQUIRED)
    context = env["context"]
    nodes = (
        PlanNode.model_validate({
            "id": "sentiment", "worker_id": "text.sentiment",
            "writes_slot": "slot.sentiment"}),
        PlanNode.model_validate({
            "id": "research-mgr", "worker_id": "dec.research_mgr",
            "writes_slot": "slot.research_plan",
            "dependencies": ({
                "upstream_node_id": "sentiment",
                "artifact_slot": "slot.sentiment",
                "inject_as": "sentiment",
                "policy": DependencyPolicy.BLOCK},)}),
    )
    draft = PlanDraft(
        id="plan-e2e-late", run_id="run-e2e-late",
        request_id=request.request_id, phase="main", source=PlanSource.DYNAMIC,
        goal=request.goal, as_of=context.data_context.as_of,
        mode=context.data_context.mode, context_snapshot_ref=env["ctx_ref"],
        universe=(), nodes=nodes, sink_node_ids=("research-mgr",), debates=(),
        gates=(), reducers=(), catalog_version=env["snapshot"].catalog_version,
        catalog_digest=env["snapshot"].catalog_digest,
        schema_registry_digest=env["registry"].registry_digest,
        approval_policy=ApprovalPolicy.REQUIRED,
        budget_request_tokens=2_000_000, budget_request_llm_invocations=2,
        max_concurrency=2, stop_condition_refs=(), legacy_source_schema=None,
        legacy_source_config_digest=None, legacy_mapping_digest=None)
    return request, draft


class _RefusalSpy:
    def __init__(self) -> None:
        self.records: list[dict] = []

    def record(self, **kwargs) -> None:
        self.records.append(kwargs)


class _ForbiddenEvidenceWriter:
    """No evidence write may precede the gateway refusal — any put is a RED."""

    def put(self, **kwargs):
        raise AssertionError("evidence write attempted before the gateway "
                             "refused the zero-bound capability call")

    def record_existing(self, **kwargs):
        raise AssertionError("evidence write attempted before the gateway "
                             "refused the zero-bound capability call")


class TestLateFailureLoudness:
    """CONSCIOUSLY FLIPPED 2026-07-31 (controller ruling — the deep decision
    trunk; was: Task-11 review minor #1's late-failure pin).

    The original pin recorded the pre-ruling residue: a DYNAMIC plan scheduling
    ``dec.research_mgr`` passed admission support with the rowless
    zero-contribution experience summary, and then died LATE because the Lane-0
    experience provider unconditionally invoked ``experience.retrieve`` on LLM
    freeze into the gateway's closed allowlist door (``CapabilityGatewayError``)
    — loud and typed, but a dead decision trunk (live run
    ``deep-8eb9afef6e9a5e48`` died of exactly this once the provider factory
    was bound). The controller then RULED the C3 discrimination extended to the
    experience provider half, mirroring ``memory/runtime.py``: a worker whose
    allowlist does NOT hold the experience capability freezes with an EMPTY,
    attributable contribution (the zero-bounds summary's digest rides it) —
    zero invocations, zero evidence, no fabricated case row, no empty-sentinel
    block. So the non-granted case now COMPLETES prepared-empty, and this class
    pins the ruled shape. The loud branches stay loud and stay pinned
    elsewhere: allowlisted-but-rowless keeps its ``CatalogError`` at the
    analyzer (``test_pipeline_deep_preset.py::TestFullPhase9BridgeView::
    test_an_allowlisted_worker_without_a_row_still_raises_loudly``), and a
    GRANTED worker still drives the gateway bit-for-bit
    (``test_experience_provider_discrimination.py::TestGrantedPathUnchanged``,
    ``test_bootstrap_e2e.py`` §4).
    """

    @pytest.fixture(scope="class")
    def late(self, env, phase9_runtime):
        request, draft = _late_failure_draft(env)
        view = production_bridge_view(phase9_runtime)
        phase1 = validate_plan_draft(
            draft, request=request, context=env["context"],
            catalog=env["snapshot"], schema_registry=env["registry"])
        assert phase1.valid is True, [(i.code, i.node_id) for i in phase1.issues]
        report = check_runtime_support(
            draft, phase1_report=phase1, context=env["context"],
            context_requirements=None, catalog=phase9_runtime, bridge_view=view,
            schema_registry=env["registry"], profile=STATIC_RUNTIME_PROFILE_V2)
        return SimpleNamespace(request=request, draft=draft, view=view,
                               report=report)

    def test_admission_support_passes_the_rowless_dynamic_plan(self, late):
        """The EARLY half: nothing at admission refuses this plan — the ruled
        empty-freeze below is genuinely the execution-time answer."""
        assert late.report.supported is True, [
            (i.code, i.node_id) for i in late.report.issues]
        mine = [s for s in late.report.bridge_support_summaries
                if s.bridge_id == "experience.bridge"
                and s.node_id == "research-mgr"]
        assert len(mine) == 1
        summary = mine[0]
        # the rowless zero-contribution posture that makes the failure late:
        assert summary.allowed_capability_refs == ()
        assert summary.max_capability_invocations == 0
        assert summary.min_finalized_tool_calls_on_success == 0

    def test_the_execution_time_answer_is_the_ruled_empty_contribution(
            self, late, env, phase9_runtime):
        """FLIPPED 2026-07-31 per the controller ruling (class docstring): the
        non-granted freeze now COMPLETES with the empty attributable
        contribution instead of dying at the gateway's allowlist door."""
        summary = next(
            s for s in late.report.bridge_support_summaries
            if s.bridge_id == "experience.bridge"
            and s.node_id == "research-mgr")
        rb = late.view.resolve("experience.bridge")
        rm_spec = {w.id: w for w in env["snapshot"].workers}["dec.research_mgr"]
        # the discriminator's premise, asserted not assumed: no reviewed grant.
        cap_ref, _mat = bs.experience_retrieve_capability()
        assert all((c.id, c.version) != (cap_ref.id, cap_ref.version)
                   for c in rm_spec.capability_allowlist)

        sequencer = W.ExecutionEvidenceSequencer(node_id="research-mgr", attempt=1)
        refusals = _RefusalSpy()
        gateway = W.CapabilityGateway(
            plan_digest=compute_candidate_plan_digest(
                request=late.request, draft=late.draft,
                context_content_digest=env["context"].content_digest),
            worker=rm_spec, summaries={summary.summary_digest: summary},
            catalog=phase9_runtime, factories=TrustedFactoryRegistry(phase9_runtime),
            phase1_registry=env["registry"], refusal_sink=refusals,
            clock=_FixedClock(SCREEN_NOW), sequencer=sequencer)
        gateway.mark_running()
        token = sequencer.issue_call_token(
            bridge_priority=rb.priority, bridge_id=rb.bridge_id,
            summary_digest=summary.summary_digest)

        lane0 = bs.load_lane0_catalog()
        provider = bs.make_lane0_experience_bridge_factory(
            pool=None, registry=env["registry"],
            capability_ref=lane0.capability_ref, views=None,
            scaler=SimpleNamespace(feature_schema_version="e2e-probe-v1"),
            k=1, as_of=SCREEN_NOW)(bridge=rb, summary=summary)
        prepared = provider.prepare_input(SimpleNamespace(token=token))
        assert prepared.status == "prepared"

        research_node = next(n for n in late.draft.nodes if n.id == "research-mgr")
        session = provider.open_execution(SimpleNamespace(
            plan=None, node=research_node, worker=rm_spec, bridge=rb,
            summary=summary, handle=prepared.prepared_handle,
            input_snapshot=SimpleNamespace(artifact_inputs=()),
            capability_gateway=gateway,
            evidence_writer=_ForbiddenEvidenceWriter(), reader=None,
            sequencer=sequencer))
        # the RULED shape: prepared-empty, completes — an EMPTY contribution
        # attributable through the zero-bounds summary's digest.
        outcome = session.freeze_for_execution(kind=ExecutionKind.LLM)
        assert outcome.status == "completed"
        contribution = outcome.frozen_contribution
        assert contribution.summary_digest == summary.summary_digest
        assert contribution.tool_call_records == ()
        assert contribution.direct_evidence_refs == ()
        assert contribution.untrusted_blocks == ()
        # nothing fabricated and nothing attempted: the gateway was never
        # touched — zero begins, zero ToolCallRecords, zero refusal records,
        # zero evidence writes (the writer raises on any put).
        assert gateway.begun_count() == 0
        assert gateway.finalized_records() == ()
        assert refusals.records == []


# =========================================================================== #
# Scenario 5 — production factory call shapes (Task-11 review minor #5)         #
# =========================================================================== #
class TestProductionFactoryCallShapes:
    def test_the_router_call_shapes_return_real_service_instances(
            self, monkeypatch, tmp_path, env):
        """Close the callability-only gap: call ``deps.admission_factory`` /
        ``deps.coordinator_factory`` with EXACTLY the router's real kwargs and
        assert real ``PlanAdmissionService`` / ``PlanApprovalCoordinator``
        instances come back."""
        from guanlan_v2 import orch_store_status as status_mod
        from guanlan_v2.orchestration.adapters import durable as durable_mod
        from guanlan_v2.orchestration.adapters.durable import (
            build_durable_runtime_stores,
        )

        stores = build_durable_runtime_stores(tmp_path / "orch")
        monkeypatch.setattr(durable_mod, "process_durable_stores", lambda: stores)
        monkeypatch.setattr(status_mod, "orchestration_store_bound", lambda: True)
        deps = api_mod.build_production_pipeline_deps()

        request, draft = _late_failure_draft(env)
        run_budget = RunBudget(
            ledger_id="led-cs-1", max_tokens=draft.budget_request_tokens,
            max_llm_invocations=draft.budget_request_llm_invocations,
            max_concurrency=draft.max_concurrency)
        approvals: dict = {}
        # the router's exact kwargs (api.py `_single_plan_admit` /
        # `_start_source_kind`): run_id=, requests=, drafts=, context=,
        # approvals=, run_budget=.
        service = deps.admission_factory(
            run_id="cs-run-1", requests={request.request_id: request},
            drafts={draft.id: draft}, context=env["context"],
            approvals=approvals, run_budget=run_budget)
        assert isinstance(service, PlanAdmissionService)

        coordinator = deps.coordinator_factory(
            admission=service, approvals_sink=lambda ap: None)
        assert isinstance(coordinator, PlanApprovalCoordinator)
        assert callable(deps.planner_runner)


# =========================================================================== #
# Scenario 3 — red lines                                                        #
# =========================================================================== #
def _module_source(name: str) -> str:
    return (_PIPELINE_DIR / f"{name}.py").read_text(encoding="utf-8")


class TestRedLines:
    def test_auto_is_rejected_for_every_phase10_draft(self, env):
        """AUTO's three closed doors, all typed: the screening builder refuses
        it at the door; a deep/dynamic AUTO draft fails Phase-1 validation
        (``auto_approval_rejected``); and the pending human card is
        structurally unconstructible for it."""
        # (a) screening: refused before any materialization.
        from tests.orchestration.test_pipeline_screening import (
            _base_request,
            _build,
        )
        with pytest.raises(BaseRequestRefused, match="AUTO"):
            _build(env, base_request=_base_request(
                approval_policy=ApprovalPolicy.AUTO))
        # (b) a DYNAMIC draft carrying AUTO fails the unmodified Phase-1
        # validator for EVERY source (spec.py's blanket rule).
        request, draft = _late_failure_draft(env)
        auto_request = request.model_copy(
            update={"approval_policy": ApprovalPolicy.AUTO})
        auto_draft = draft.model_copy(
            update={"approval_policy": ApprovalPolicy.AUTO})
        report = validate_plan_draft(
            auto_draft, request=auto_request, context=env["context"],
            catalog=env["snapshot"], schema_registry=env["registry"])
        assert report.valid is False
        assert "auto_approval_rejected" in {i.code for i in report.issues}
        # (c) the card: unconstructible for a non-REQUIRED policy.
        digest = compute_candidate_plan_digest(
            request=request, draft=draft,
            context_content_digest=env["context"].content_digest)
        diff = build_plan_diff(draft, request=request,
                               candidate_plan_digest=digest, baseline=None,
                               baseline_kind="none")
        ref = TypedPayloadRef(
            schema_ref=PLAN_DIFF_SCHEMA_REF,
            payload_ref=PayloadRef(namespace="main", object_id="diff-auto",
                                   content_digest=diff.semantic_digest()))
        card = build_pending_plan_approval(
            draft=draft, request=request, candidate_plan_digest=digest,
            diff=diff, plan_diff_ref=ref, planner_rationale=None,
            candidate_id="auto-probe", requested_at=SCREEN_NOW)
        fields = card.model_dump()
        fields["approval_policy"] = ApprovalPolicy.AUTO
        # a tuple, not a list: strict mode rejects a list for a tuple field
        # BEFORE any of the card's own validators run (the Task-6 lesson).
        fields["worker_ids"] = tuple(fields["worker_ids"])
        from guanlan_v2.orchestration.plan_diff import PendingPlanApproval
        with pytest.raises(ValidationError, match="AUTO"):
            PendingPlanApproval.model_validate(fields)

    def test_dynamic_drafts_never_lease_admit(self, env, tmp_path):
        """The lease channel is preset-provenance-only: a DYNAMIC card under an
        ACTIVE screening lease stays ``pending_human`` and the lease is never
        drawn — the chat path keeps its per-plan human approval."""
        request, draft = _late_failure_draft(env)
        digest = compute_candidate_plan_digest(
            request=request, draft=draft,
            context_content_digest=env["context"].content_digest)
        diff = build_plan_diff(draft, request=request,
                               candidate_plan_digest=digest, baseline=None,
                               baseline_kind="none")
        card = build_pending_plan_approval(
            draft=draft, request=request, candidate_plan_digest=digest,
            diff=diff,
            plan_diff_ref=TypedPayloadRef(
                schema_ref=PLAN_DIFF_SCHEMA_REF,
                payload_ref=PayloadRef(namespace="main", object_id="diff-dyn",
                                       content_digest=diff.semantic_digest())),
            planner_rationale=None, candidate_id="dyn-e2e",
            requested_at=SCREEN_NOW)
        assert card.preset_id is None and card.preset_record_digest is None

        presets = load_phase10_preset_registry(PRODUCTION_PRESETS_DIR)
        admission_never = SimpleNamespace(record_approval=lambda *a, **k: (
            (_ for _ in ()).throw(AssertionError(
                "a DYNAMIC card must never reach record_approval through a lease"))))
        coordinator = PlanApprovalCoordinator(
            tmp_path / "journal.jsonl", admission=admission_never,
            clock=_FixedClock(SCREEN_NOW), verifier=_Verifier(),
            console_emit=None, preset_registry=presets,
            catalog_digest=env["snapshot"].catalog_digest,
            registry_digest=env["registry"].registry_digest)
        record = presets.get(SCREENING_LANE_PRESET_ID)
        lease = coordinator.issue_lease(
            purpose="red-line probe", preset_id=SCREENING_LANE_PRESET_ID,
            preset_record_digest=record.semantic_digest(),
            catalog_digest=env["snapshot"].catalog_digest,
            registry_digest=env["registry"].registry_digest,
            valid_from=SCREEN_NOW - timedelta(days=1),
            valid_until=SCREEN_NOW + timedelta(days=1),
            max_admissions=5, budget_cap_llm_invocations=100,
            actor=GOOD_CRED, reason="red-line probe lease")
        outcome = coordinator.register_and_try_lease(
            card, idempotency_key="dyn-e2e", now=SCREEN_NOW,
            candidate_catalog_digest=draft.catalog_digest,
            candidate_registry_digest=draft.schema_registry_digest)
        assert outcome.outcome == "pending_human"
        assert outcome.lease_id is None and outcome.approval is None
        view = {v.lease.lease_id: v
                for v in coordinator.list_leases(now=SCREEN_NOW)}[lease.lease_id]
        assert view.admissions_used == 0

    def test_import_sweep_no_http_client_and_no_out_of_lane_import(self):
        """No module in the pipeline package imports an HTTP client (any
        level) or reaches outside ``guanlan_v2.orchestration`` at module top
        level (fastapi/pydantic/stdlib excepted; seats/engine reads are lazy
        by the reviewed Task-7 rule)."""
        banned_clients = ("httpx", "requests", "aiohttp", "urllib", "socket")
        for name in _PIPELINE_MODULES:
            tree = ast.parse(_module_source(name))
            top_level: list[str] = []
            for node in tree.body:
                if isinstance(node, ast.Import):
                    top_level += [a.name for a in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module:
                    top_level.append(node.module)
            for module in top_level:
                if module.startswith("guanlan_v2"):
                    assert module.startswith("guanlan_v2.orchestration"), (
                        name, module)
                assert not module.startswith("financial_analyst"), (name, module)
            every: set[str] = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    every.update(a.name for a in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    every.add(node.module)
            for client in banned_clients:
                assert not any(m == client or m.startswith(client + ".")
                               for m in every), (name, client)

    def test_kind_trade_appears_nowhere_in_the_pipeline_package(self):
        for name in _PIPELINE_MODULES:
            source = _module_source(name)
            assert '"trade"' not in source and "'trade'" not in source, name
            kinds = set(re.findall(r"persist_decision\(\s*['\"](\w+)['\"]",
                                   source))
            assert kinds <= {"decide", "order"}, (name, kinds)

    def test_escalation_constants_digest_is_unchanged(self):
        assert (escalation_mod.ESCALATION_CONSTANTS_DIGEST
                == _ESCALATION_DIGEST_GOLDEN)

    def test_watcher_env_off_stays_bit_unchanged(self):
        """The Task-7 regression, re-confirmed at the seam: the watcher's
        ``decide_fn`` is a keyword-only ``None``-default addition and the
        server's deep wiring is env-gated with a lazy import."""
        import inspect

        from guanlan_v2.seats import watcher

        run_loop_param = inspect.signature(watcher.run_loop).parameters["decide_fn"]
        assert run_loop_param.default is None
        assert run_loop_param.kind is inspect.Parameter.KEYWORD_ONLY
        assert inspect.signature(watcher.tick).parameters["decide_fn"].default is None
        source = Path("guanlan_v2/server.py").read_text(encoding="utf-8")
        assert 'os.environ.get("GUANLAN_SEATS_DEEP") == "1"' in source
        assert re.search(r"run_loop\(\s*decide_fn=", source)
        gate = source.index("GUANLAN_SEATS_DEEP")
        assert source.index("live_decide", gate) > gate
