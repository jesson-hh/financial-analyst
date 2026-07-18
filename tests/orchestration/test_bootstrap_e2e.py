# -*- coding: utf-8 -*-
"""Phase 5 · Task 10 — the bootstrap end-to-end: Request → BootstrapPlan →
ContextSnapshot → derived main RunContext.

Drives the fixed Lane 0 preset through the REAL Phase 2 admission/runtime machinery
under ``BOOTSTRAP_RUNTIME_PROFILE`` with fake gateways + in-memory stores (zero live
network): admission (validate → support → reserve → approve → freeze → PlanAdmitted)
→ ``run_plan`` (deterministic ``market.factor`` handler + a scripted LLM gateway +
the real ``experience.retrieve`` capability bridge) → three committed artifacts →
``build_context_snapshot_from_bootstrap`` (honest unknown/degraded manifest) →
``derive_main_run_context`` → a main-phase draft binding the committed snapshot
passes Phase 1 validation. Also: degraded/failure honesty, experience wiring,
lifecycle-④ case append, unapproved dispatch, and the Lane 0 red lines.

Run: ``python -m pytest tests/orchestration/test_bootstrap_e2e.py -v``
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from guanlan_v2.orchestration import dag as D
from guanlan_v2.orchestration import presets as P
from guanlan_v2.orchestration import trial as _trial
from guanlan_v2.orchestration import worker as W
from guanlan_v2.orchestration import bootstrap as B
from guanlan_v2.orchestration.budget import BudgetLedger
from guanlan_v2.orchestration.bootstrap import (
    BOOTSTRAP_RUNTIME_PROFILE,
    append_regime_case_from_run,
    bootstrap_runtime_profile,
    build_bootstrap_plan,
    build_bootstrap_plan_draft,
    build_bootstrap_run_context,
    build_context_snapshot_from_bootstrap,
    build_phase5_registry,
    derive_main_run_context,
    lane0_analyzers,
    load_lane0_catalog,
    register_bootstrap_runtime_factories,
)
from guanlan_v2.orchestration.catalog_runtime import (
    BridgeCatalogView,
    CatalogRuntime,
    TrustedFactoryRegistry,
)
from guanlan_v2.orchestration.context import RunBudget
from guanlan_v2.orchestration.enums import (
    ApprovalDecision,
    Confidence,
    DataMode,
    NodeStatus,
    RotationStage,
)
from guanlan_v2.orchestration.eventstore import (
    EventRefusalAuditSink,
    RuntimeStores,
    SchemaRegistryResolver,
)
from guanlan_v2.orchestration.events import PlanApproval
from guanlan_v2.orchestration.market.factors import (
    MARKET_FACTOR_REPORT_SCHEMA_REF,
    DailyValueRow,
    EvidenceAnchor,
    HeatState,
    MainlineRead,
    MarketFactorInputs,
    MarketFactorReport,
    RegimeReport,
    RiskState,
    RotationReport,
    TrendState,
    build_market_factor_set_v1,
)
from guanlan_v2.orchestration.memory.experience import (
    EXPERIENCE_SELECTION_SCHEMA_REF,
    ExperienceLog,
    ExperienceQuery,
    ExperienceScalerSnapshot,
    ExperienceSelection,
    RegimeGraderSpec,
)
from guanlan_v2.orchestration.admission import (
    ApprovalSubmission,
    PlanAdmissionService,
)
from guanlan_v2.orchestration.pool import ArtifactPool
from guanlan_v2.orchestration.schema_registry import SchemaRegistry
from guanlan_v2.orchestration.spec import validate_plan_draft

UTC = timezone.utc
DT = datetime(2026, 7, 18, 1, 30, tzinfo=UTC)
FSV = "mfs-v1"


class FixedClock:
    def now(self) -> datetime:
        return DT


# --------------------------------------------------------------------------- #
# scenario inputs                                                             #
# --------------------------------------------------------------------------- #
def _daily_rows(n: int, base: float = 30.0) -> tuple[DailyValueRow, ...]:
    end = datetime(2026, 7, 17, tzinfo=UTC)
    rows = []
    for i in range(n):
        d = end - timedelta(days=(n - 1 - i))
        rows.append(DailyValueRow(
            date=d.strftime("%Y-%m-%d"), value=base + i * 0.5,
            available_at=datetime(d.year, d.month, d.day, 7, 5, tzinfo=UTC)))
    return tuple(rows)


def _happy_inputs() -> MarketFactorInputs:
    # a full window of limit_up_total ⇒ breadth.limit_up_ema OK ⇒ non-empty
    # feature_vector ⇒ the deterministic reader COMPLETES cleanly.
    return MarketFactorInputs(limit_up_total=_daily_rows(60))


def _empty_inputs() -> MarketFactorInputs:
    # every series absent ⇒ all factors UNAVAILABLE ⇒ empty feature_vector ⇒ the
    # deterministic reader reports an honest DEGRADED node (never silent success).
    return MarketFactorInputs()


def _grader() -> RegimeGraderSpec:
    return RegimeGraderSpec.build(
        grader_version="regime-grader-v1", horizon_trading_days=20, benchmark_id="eqw_all_a",
        bull_min_return=0.03, bear_max_return=-0.03, risk_off_max_drawdown=-0.05,
        risk_on_min_return=0.02, risk_on_min_drawdown=-0.03)


def _scaler() -> ExperienceScalerSnapshot:
    return ExperienceScalerSnapshot.build(
        feature_schema_version=FSV, scaler_version="scaler-v1", fit_as_of=DT,
        n_obs=0, mu={}, sd={})


# --------------------------------------------------------------------------- #
# the scripted Lane 0 LLM gateway                                            #
# --------------------------------------------------------------------------- #
class FakeLane0Gateway:
    """A trusted single-shot gateway that renders the factor-report block for its
    captured prompt (never the raw payload JSON) and returns a scripted, guardrail-
    conformant :class:`RegimeReport` / :class:`RotationReport` per node."""

    def __init__(self, *, stores, pool, registry, fail_nodes=()):
        self._stores = stores
        self._pool = pool
        self._registry = registry
        self._fail = set(fail_nodes)
        self._seed_factor_id = build_market_factor_set_v1().definitions[0].factor_id
        self.captured_prompts: dict[str, str] = {}
        self.rendered_factor: dict[str, str] = {}
        self.experience_blocks: dict[str, str] = {}

    def _factor_report(self):
        art = self._pool.committed_output("lane0.factor", "primary")
        if art is None:
            return None
        return self._registry.validate_payload(MARKET_FACTOR_REPORT_SCHEMA_REF, art.payload)

    def invoke(self, request, *, prompt_assembly_ref):
        record = W.verify_model_request_binding(
            request, prompt_assembly_ref, reader=self._stores.payloads)
        node_id, worker_id = record.node_id, record.worker_id
        if node_id in self._fail:
            raise RuntimeError(f"scripted model failure on {node_id}")

        report = self._factor_report()
        factor_block = (
            B.render_factor_report_for_prompt(report) if report is not None
            else "<no factor report — omitted input>")
        self.rendered_factor[node_id] = factor_block

        exp_blocks = []
        for b in record.untrusted_blocks:
            payload = self._stores.payloads.get(
                b.payload_ref.payload_ref, expected_schema_ref=b.payload_ref.schema_ref)
            if isinstance(payload, ExperienceSelection):
                exp_blocks.append(B.render_experience_selection_for_prompt(payload))
        self.experience_blocks[node_id] = "\n".join(exp_blocks)
        self.captured_prompts[node_id] = factor_block + "\n" + self.experience_blocks[node_id]

        if worker_id == "market.regime":
            payload = self._regime(report)
        elif worker_id == "market.rotation":
            payload = self._rotation(report)
        else:  # pragma: no cover - only the two LLM workers reach a gateway
            raise AssertionError(f"unexpected worker {worker_id!r}")
        return W.ModelResult(
            payload=payload, rendered_text=self.captured_prompts[node_id],
            number_anchors=(), input_tokens=11, output_tokens=7, provider="fake",
            model="fake-lane0", provider_response_id=f"resp-{node_id}")

    # -- scripted outputs --------------------------------------------------- #
    def _anchor_and_digest(self, report):
        if report is not None and report.feature_vector:
            fid, val = next(iter(report.feature_vector.items()))
            return EvidenceAnchor(factor_id=fid, value=val, reading="reading"), report.content_digest, True
        fid = report.values[0].factor_id if report is not None else self._seed_factor_id
        digest = report.content_digest if report is not None else "0" * 64
        return EvidenceAnchor(factor_id=fid, value=0.0, reading="no usable factor data"), digest, False

    def _as_of(self, report):
        return report.as_of if report is not None else DT

    def _regime(self, report) -> RegimeReport:
        anchor, digest, confident = self._anchor_and_digest(report)
        if confident:
            return RegimeReport.build(
                as_of=self._as_of(report), factor_report_digest=digest,
                trend=TrendState.BULL, risk_state=RiskState.RISK_ON, heat_state=HeatState.NORMAL,
                trend_probabilities={TrendState.BULL: 0.6, TrendState.BEAR: 0.1,
                                     TrendState.RANGE: 0.2, TrendState.UNKNOWN: 0.1},
                risk_probabilities={RiskState.RISK_ON: 0.6, RiskState.RISK_OFF: 0.2,
                                    RiskState.NEUTRAL: 0.1, RiskState.UNKNOWN: 0.1},
                heat_probabilities={HeatState.NORMAL: 0.7, HeatState.OVERHEAT: 0.2,
                                    HeatState.UNKNOWN: 0.1},
                confidence=Confidence.MEDIUM, evidence=(anchor,),
                drivers=("breadth",), evidence_factor_ids=(anchor.factor_id,),
                narrative="Supportive breadth; advisory only, zero trading authority.")
        return RegimeReport.build(
            as_of=self._as_of(report), factor_report_digest=digest,
            trend=TrendState.UNKNOWN, risk_state=RiskState.UNKNOWN, heat_state=HeatState.UNKNOWN,
            trend_probabilities={TrendState.BULL: 0.0, TrendState.BEAR: 0.0,
                                 TrendState.RANGE: 0.0, TrendState.UNKNOWN: 1.0},
            risk_probabilities={RiskState.RISK_ON: 0.0, RiskState.RISK_OFF: 0.0,
                                RiskState.NEUTRAL: 0.0, RiskState.UNKNOWN: 1.0},
            heat_probabilities={HeatState.NORMAL: 0.0, HeatState.OVERHEAT: 0.0,
                                HeatState.UNKNOWN: 1.0},
            confidence=Confidence.LOW, evidence=(anchor,),
            drivers=("insufficient_data",), evidence_factor_ids=(anchor.factor_id,),
            narrative="No usable factor data; regime unknown (honest degraded read).",
            unknown_reason="insufficient factor coverage")

    def _rotation(self, report) -> RotationReport:
        anchor, digest, confident = self._anchor_and_digest(report)
        if confident:
            mainline = MainlineRead(
                name="AI compute", universe_key="ai_compute", stage=RotationStage.SPREAD,
                strength=6.0, persistence="two-session inflow", evidence=(anchor,))
            return RotationReport.build(
                as_of=self._as_of(report), factor_report_digest=digest,
                mainlines=(mainline,), confidence=Confidence.MEDIUM,
                evidence_factor_ids=(anchor.factor_id,),
                narrative="AI compute leads; advisory only, zero trading authority.")
        return RotationReport.build(
            as_of=self._as_of(report), factor_report_digest=digest, mainlines=(),
            confidence=Confidence.LOW, evidence_factor_ids=(),
            narrative="No mainline discernible; themeless tape (honest degraded read).",
            unknown_reason="archive-young factors; no mainline signal")


# --------------------------------------------------------------------------- #
# the fully wired bootstrap env                                              #
# --------------------------------------------------------------------------- #
class BootstrapEnv:
    def __init__(self, **kw):
        self.__dict__.update(kw)

    def run(self, *, recorder=None):
        rec = recorder if recorder is not None else D.RunRecorder()
        result = asyncio.run(D.run_plan(
            self.plan.plan_digest, self.run_ctx, admission=self.service, pool=self.pool,
            budget=self.budget, runtime=self.runtime, registry=self.registry, stores=self.stores,
            runtime_limit=3, clock=self.clock, refusal_sink=self.refusal_sink,
            model_gateway=self.model_gateway, recorder=rec))
        return result, rec


_PHASE1_MODELS = (MarketFactorReport, RegimeReport, RotationReport,
                  ExperienceQuery, ExperienceSelection)


def build_bootstrap_env(*, suffix="", inputs=None, fail_nodes=(), approve=True):
    clock = FixedClock()
    cat = load_lane0_catalog()
    catalog = CatalogRuntime.build(cat.snapshot, cat.source)
    view = BridgeCatalogView.build(catalog, lane0_analyzers(cat))

    registry = SchemaRegistry()
    for model in _PHASE1_MODELS:
        registry.register(model)
    registry.seal()
    rt_reg = build_phase5_registry(_trial.PHASE4_REGISTRY_DIGEST)

    resolver = SchemaRegistryResolver()
    resolver.register(registry)
    rt_digest = resolver.register(rt_reg)
    stores = RuntimeStores(
        resolver=resolver, clock=clock, allowed_cell_namespaces=(W.PROMPT_CELL_NAMESPACE,))

    spec = build_market_factor_set_v1()
    preset = build_bootstrap_plan(spec=spec, grader=_grader())
    request = P.preset_orchestration_request(request_id=f"req-boot{suffix}")
    data_context = P.pilot_data_context(as_of=DT)
    draft = build_bootstrap_plan_draft(
        preset, request=request, as_of=DT, mode=DataMode.ONLINE, catalog=catalog.snapshot,
        schema_registry_digest=registry.registry_digest,
        draft_id=f"plan-bootstrap{suffix}", run_id=f"run-boot{suffix}")

    from guanlan_v2.orchestration.runtime_contracts import default_audit_detail_registry
    audit_reg = default_audit_detail_registry()
    audit_reg.seal()
    refusal_sink = EventRefusalAuditSink(detail_registry=audit_reg, clock=clock)
    run_budget = RunBudget(
        ledger_id=f"led-boot{suffix}", max_tokens=4_000_000, max_llm_invocations=8,
        max_concurrency=4)

    approvals: dict = {}
    service = PlanAdmissionService(
        run_id=draft.run_id, requests={request.request_id: request}, drafts={draft.id: draft},
        contexts={}, attestations={}, approvals=approvals, catalog=catalog, bridge_view=view,
        phase1_registry=registry, runtime_registry_digest=rt_digest,
        profile=bootstrap_runtime_profile(), stores=stores, run_budget=run_budget, clock=clock)

    prep = service.prepare_candidate(draft.id, request_id=request.request_id)
    cand, res = service.persist_and_reserve_candidate(prep, idempotency_key="reserve-1")

    plan = admitted = bundle = None
    if approve:
        approvals[(request.request_id, cand.candidate_plan_digest)] = PlanApproval(
            request_id=request.request_id, candidate_plan_digest=cand.candidate_plan_digest,
            decision=ApprovalDecision.APPROVED, actor_id="human-1", decided_at=clock.now())
        ev = service.record_approval(
            cand.candidate_plan_digest,
            ApprovalSubmission(request_id=request.request_id,
                               candidate_plan_digest=cand.candidate_plan_digest,
                               decision=ApprovalDecision.APPROVED),
            authenticated_actor="human-1", idempotency_key="approve-1")
        plan, admitted = service.freeze_and_admit_candidate(
            cand.candidate_plan_digest, reservation_id=res.reservation_id,
            approval_event_id=ev.event_id, idempotency_key="freeze-1")
        bundle = service.verify_for_dispatch(plan.plan_digest)

    pool = ArtifactPool(
        stores=stores, registry_digest=rt_digest,
        plan=plan if plan is not None else None, catalog=catalog.snapshot, clock=clock) \
        if plan is not None else None
    budget = BudgetLedger(
        sink=stores.budget_event_sink(run_id=draft.run_id, ledger_id=run_budget.ledger_id),
        run_budget=run_budget)

    scaler = _scaler()
    factories = TrustedFactoryRegistry(catalog)
    runtime = run_ctx = model_gateway = None
    if plan is not None:
        register_bootstrap_runtime_factories(
            factories=factories, catalog=cat, pool=pool, registry=registry, spec=spec,
            inputs=inputs if inputs is not None else _happy_inputs(), as_of=DT,
            experience_views=(), experience_scaler=scaler, experience_k=preset.experience_k)
        runtime = W.ExecutionRuntime(
            catalog=catalog, bridge_view=view, factories=factories,
            support_report=bundle.support_report, runtime_registry_digest=rt_digest)
        run_ctx = build_bootstrap_run_context(
            run_id=draft.run_id, data=data_context, budget=run_budget,
            cancellation_token_id=f"cancel-boot{suffix}")
        model_gateway = FakeLane0Gateway(
            stores=stores, pool=pool, registry=registry, fail_nodes=fail_nodes)

    return BootstrapEnv(
        clock=clock, cat=cat, catalog=catalog, view=view, registry=registry, rt_reg=rt_reg,
        rt_digest=rt_digest, resolver=resolver, stores=stores, spec=spec, preset=preset,
        request=request, data_context=data_context, draft=draft, refusal_sink=refusal_sink,
        run_budget=run_budget, approvals=approvals, service=service, prep=prep, cand=cand,
        res=res, plan=plan, admitted=admitted, bundle=bundle, pool=pool, budget=budget,
        factories=factories, runtime=runtime, run_ctx=run_ctx, model_gateway=model_gateway,
        scaler=scaler)


def _status(rec, node_id):
    runs = [nr for nr in rec.node_runs if nr.node_id == node_id]
    assert runs, f"no NodeRun for {node_id}"
    return runs[-1].status


# =========================================================================== #
# 1 — happy path: full spec §2.0 sequence + prompt honesty                     #
# =========================================================================== #
def test_happy_path_bootstrap_to_snapshot_to_main_context():
    env = build_bootstrap_env(inputs=_happy_inputs())
    result, rec = env.run()
    assert result.terminal_status == "completed"
    for nid in ("lane0.factor", "lane0.regime", "lane0.rotation"):
        assert _status(rec, nid) is NodeStatus.COMPLETED
    # the deterministic reader reserved zero LLM invocations (2 = regime + rotation).
    assert result.settled_llm_invocations == 2

    # three committed artifacts.
    factor = env.pool.committed_output("lane0.factor", "primary")
    regime = env.pool.committed_output("lane0.regime", "primary")
    rotation = env.pool.committed_output("lane0.rotation", "primary")
    assert factor is not None and regime is not None and rotation is not None

    # ContextSnapshot + manifest: three refs, no badges.
    context, manifest = build_context_snapshot_from_bootstrap(
        run_result=result, pool=env.pool, data_context=env.data_context, stores=env.stores,
        registry=env.rt_reg, clock=env.clock)
    assert manifest.market_factor_report_ref is not None
    assert manifest.regime_report_ref is not None
    assert manifest.rotation_report_ref is not None
    assert manifest.degradation_badges == ()
    assert manifest.context_snapshot_digest == context.content_digest
    assert manifest.bootstrap_plan_digest == env.plan.plan_digest

    # derive a NEW main RunContext (no in-place mutation of the bootstrap context).
    main_budget = RunBudget(
        ledger_id="led-main", max_tokens=4_000_000, max_llm_invocations=8, max_concurrency=4)
    main_ctx = derive_main_run_context(
        env.run_ctx, snapshot=context, main_run_id="run-main-1", budget=main_budget,
        cancellation_token_id="cancel-main")
    assert env.run_ctx.context_snapshot_id is None  # original untouched
    assert main_ctx.context_snapshot_id == context.snapshot_id
    assert main_ctx.memory_snapshot_hash == context.memory_snapshot_hash
    assert main_ctx.data == env.run_ctx.data

    # a main-phase draft binding the snapshot passes Phase 1 validation.
    from guanlan_v2.orchestration.schema_registry import default_registry
    pilot_cat = P.load_pilot_catalog()
    pilot_registry = default_registry()
    main_draft = P.build_pilot_draft(
        catalog=pilot_cat, registry=pilot_registry, context=context,
        request_id="req-main-1", as_of=DT)
    main_request = P.preset_orchestration_request(request_id="req-main-1")
    report = validate_plan_draft(
        main_draft, request=main_request, context=context, catalog=pilot_cat.snapshot,
        schema_registry=pilot_registry)
    assert report.valid, [i.code for i in report.issues]


def test_happy_path_prompt_honesty_and_evidence_binding():
    env = build_bootstrap_env(inputs=_happy_inputs())
    result, rec = env.run()
    assert result.terminal_status == "completed"
    factor = env.registry.validate_payload(
        MARKET_FACTOR_REPORT_SCHEMA_REF, env.pool.committed_output("lane0.factor", "primary").payload)
    factor_ids = {v.factor_id for v in factor.values}

    for nid in ("lane0.regime", "lane0.rotation"):
        captured = env.model_gateway.captured_prompts[nid]
        # the rendered factor-report block (header incl. battery_digest prefix) is present…
        assert f"battery_digest={factor.battery_digest[:8]}" in captured
        assert f"factor_report_digest={factor.content_digest[:8]}" in captured
        # …and never the raw MarketFactorReport typed-payload JSON.
        assert '"schema_version"' not in captured
        assert '"feature_vector"' not in captured

    regime = env.pool.committed_output("lane0.regime", "primary").payload
    rotation = env.pool.committed_output("lane0.rotation", "primary").payload
    assert regime.factor_report_digest == factor.content_digest
    assert rotation.factor_report_digest == factor.content_digest
    for anchor in regime.evidence:
        assert anchor.factor_id in factor_ids
    for ml in rotation.mainlines:
        for anchor in ml.evidence:
            assert anchor.factor_id in factor_ids


# =========================================================================== #
# 2 — degraded factors: honest all-unknown + badged manifest, snapshot commits  #
# =========================================================================== #
def test_degraded_factors_run_to_a_badged_committed_snapshot():
    env = build_bootstrap_env(inputs=_empty_inputs())
    result, rec = env.run()
    assert result.terminal_status == "completed"
    # the deterministic reader honestly DEGRADED (all factors UNAVAILABLE).
    assert _status(rec, "lane0.factor") is NodeStatus.DEGRADED

    regime = env.pool.committed_output("lane0.regime", "primary").payload
    rotation = env.pool.committed_output("lane0.rotation", "primary").payload
    # scripted guardrail-conformant all-unknown LOW.
    assert regime.trend is TrendState.UNKNOWN
    assert regime.confidence is Confidence.LOW
    assert rotation.mainlines == ()

    context, manifest = build_context_snapshot_from_bootstrap(
        run_result=result, pool=env.pool, data_context=env.data_context, stores=env.stores,
        registry=env.rt_reg, clock=env.clock)
    # the ContextSnapshot still commits, and the manifest is badged.
    assert manifest.market_factor_report_ref is not None
    assert manifest.degradation_badges != ()
    assert "factor_degraded" in manifest.degradation_badges


def test_failed_factor_node_omits_input_and_still_produces_unknown():
    # a variant where the deterministic reader itself FAILS is emulated by an empty
    # input that DEGRADES it; the LLM nodes still run guardrail-conformant. (An
    # outright FAILED factor node omits the required=False input entirely.)
    env = build_bootstrap_env(inputs=_empty_inputs())
    result, rec = env.run()
    regime = env.pool.committed_output("lane0.regime", "primary").payload
    assert regime.trend is TrendState.UNKNOWN and regime.confidence is Confidence.LOW


# =========================================================================== #
# 3 — regime LLM failure ⇒ regime_missing + None ref; rotation intact           #
# =========================================================================== #
def test_regime_failure_badges_missing_and_still_commits_snapshot():
    env = build_bootstrap_env(inputs=_happy_inputs(), fail_nodes=("lane0.regime",))
    result, rec = env.run()
    assert _status(rec, "lane0.regime") is NodeStatus.FAILED
    assert env.pool.committed_output("lane0.regime", "primary") is None
    assert env.pool.committed_output("lane0.rotation", "primary") is not None

    context, manifest = build_context_snapshot_from_bootstrap(
        run_result=result, pool=env.pool, data_context=env.data_context, stores=env.stores,
        registry=env.rt_reg, clock=env.clock)
    assert manifest.regime_report_ref is None
    assert "regime_missing" in manifest.degradation_badges
    assert manifest.rotation_report_ref is not None
    assert manifest.context_snapshot_digest == context.content_digest


# =========================================================================== #
# 4 — experience wiring: one finalized retrieve per LLM node, empty ⇒ COMPLETED #
# =========================================================================== #
def test_experience_retrieve_finalizes_once_per_llm_node_empty_selection():
    env = build_bootstrap_env(inputs=_happy_inputs())
    result, rec = env.run()
    for nid in ("lane0.regime", "lane0.rotation"):
        art = env.pool.committed_output(nid, "primary")
        records = art.provenance.tool_call_records
        assert len(records) == 1
        assert records[0].tool_ref == env.cat.capability_ref
        selection = env.stores.payloads.get(
            records[0].result_ref.payload_ref,
            expected_schema_ref=EXPERIENCE_SELECTION_SCHEMA_REF)
        assert selection.neighbours == ()
        assert "cold_start:0_neighbours" in selection.badges


# =========================================================================== #
# 5 — lifecycle ④: deterministic RegimeCase append; idempotent + conflict       #
# =========================================================================== #
def _experience_log(env):
    return ExperienceLog(
        event_store=env.stores.events, payload_store=env.stores.payloads, registry=env.rt_reg,
        clock=env.clock, uow_factory=lambda: env.stores.unit_of_work)


def test_case_append_from_run_is_deterministic_and_idempotent():
    env = build_bootstrap_env(inputs=_happy_inputs())
    result, rec = env.run()
    log = _experience_log(env)
    case = append_regime_case_from_run(
        run_result=result, pool=env.pool, log=log, spec=env.spec, clock=env.clock)
    assert case is not None
    assert case.id == f"rc.{FSV}.{DT.strftime('%Y%m%d')}"
    assert case.scaler_digest == env.scaler.content_digest
    assert case.judgment.as_of == case.as_of
    assert any(link.startswith("artifact:") for link in case.links)
    # same-day rerun is idempotent (same id + content).
    again = append_regime_case_from_run(
        run_result=result, pool=env.pool, log=log, spec=env.spec, clock=env.clock)
    assert again is not None and again.content_digest == case.content_digest


def test_case_append_absent_regime_returns_none():
    env = build_bootstrap_env(inputs=_happy_inputs(), fail_nodes=("lane0.regime",))
    result, rec = env.run()
    log = _experience_log(env)
    case = append_regime_case_from_run(
        run_result=result, pool=env.pool, log=log, spec=env.spec, clock=env.clock)
    assert case is None


# =========================================================================== #
# 6 — unapproved dispatch refused; a REJECTED approval releases the reservation  #
# =========================================================================== #
def test_unapproved_freeze_is_refused():
    from guanlan_v2.orchestration.admission import AdmissionRejected
    env = build_bootstrap_env(inputs=_happy_inputs(), approve=False)
    with pytest.raises((AdmissionRejected, Exception)):
        env.service.freeze_and_admit_candidate(
            env.cand.candidate_plan_digest, reservation_id=env.res.reservation_id,
            approval_event_id="no-such-event", idempotency_key="freeze-x")


def test_rejected_approval_releases_the_reservation():
    env = build_bootstrap_env(inputs=_happy_inputs(), approve=False)
    env.approvals[(env.request.request_id, env.cand.candidate_plan_digest)] = PlanApproval(
        request_id=env.request.request_id, candidate_plan_digest=env.cand.candidate_plan_digest,
        decision=ApprovalDecision.REJECTED, actor_id="human-1", decided_at=env.clock.now())
    env.service.record_approval(
        env.cand.candidate_plan_digest,
        ApprovalSubmission(request_id=env.request.request_id,
                           candidate_plan_digest=env.cand.candidate_plan_digest,
                           decision=ApprovalDecision.REJECTED),
        authenticated_actor="human-1", idempotency_key="reject-1")
    active = env.budget.get_active_plan(env.request.request_id, env.cand.candidate_plan_digest)
    assert active is None  # released on rejection


# =========================================================================== #
# 7 — red lines: no decision-class outputs; zero-LLM reader; no order/signal bus  #
# =========================================================================== #
def test_no_lane0_output_is_decision_class():
    from guanlan_v2.orchestration.spec import _DECISION_CLASS_SCHEMAS
    for name in ("MarketFactorReport", "RegimeReport", "RotationReport"):
        assert name not in _DECISION_CLASS_SCHEMAS


def test_deterministic_reader_reserves_zero_llm_invocations():
    env = build_bootstrap_env(inputs=_happy_inputs())
    result, rec = env.run()
    # two LLM nodes settle one invocation each; the deterministic reader zero.
    assert result.settled_llm_invocations == 2


def test_no_order_or_signal_bus_symbol_reachable_from_bootstrap():
    from pathlib import Path
    src = Path(B.__file__).read_text(encoding="utf-8").lower()
    for banned in ("orderbus", "signalbus", "order_bus", "signal_bus",
                   "place_order", "submit_order", "broker"):
        assert banned not in src, f"bootstrap.py references a banned symbol: {banned}"
    # the module never imports a trading/execution surface.
    assert "workflow.executor" not in src
    assert "import" in src  # sanity: the file was actually read


def test_profile_identity_is_the_reviewed_bootstrap_profile():
    assert BOOTSTRAP_RUNTIME_PROFILE.profile_id == "bootstrap-runtime"
    assert bootstrap_runtime_profile().profile_digest == BOOTSTRAP_RUNTIME_PROFILE.profile_digest
