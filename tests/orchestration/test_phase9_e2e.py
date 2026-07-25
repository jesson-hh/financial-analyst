# -*- coding: utf-8 -*-
"""Phase 9 · Task 12 — the whole-framework end-to-end suite.

Offline, fixture-driven and deterministic: no live server, no network, no vendor
credential, no ``G:\\stocks`` dependency. Five end-to-end bullets from spec §11
影子镜像/e2e, driven through the REAL contracts and the REAL production adapters:

* :func:`test_luozi_interval_e2e` — a 3-point daily schedule over fixture bars →
  per-point Bootstrap + MainPlan with per-point PIT snapshots → shadow intents each
  referencing a real committed Proposal Artifact and the schedule digest → dual curves
  under ONE ``ShadowExecutionConfig`` attestation → the Phase-4 feedback handoff →
  seats-compat rows round-tripping through the EXISTING seats handlers. Plus the two R2
  additions (per-point ``factor_report_digest`` binding, machine-re-verified
  ``EvidenceAnchor``s) and the Task-10-review chartered carries (a)/(b)/(c)/(f)/(g).
* :func:`test_eligible_time_uniqueness` — one eligible instant per ``(1d, policy)`` pair.
* :func:`test_recovery` — kill/replay between every pair of UoW boundaries.
* :func:`test_weiwo_e2e` — stub live vendor → frozen ``as_of`` → optimizer via the
  ``run_graph`` binding → draft-only factorlib landing.
* :func:`test_maturity_chain` — immature ⇒ ``WAITING_FOR_MATURITY`` ⇒ a fresh-process
  scheduler wakeup ⇒ matured batch ⇒ feedback exactly once.

CORRECTION-CLAUSE NOTES (brief wording → reviewed source; source wins — full prose in
``.superpowers/sdd/p9-task-12-report.md``):

* N12-1 — the Task-5 handoff is ``hand_off_dual_curves_to_feedback`` and the Task-6
  maturity consumer is ``mature_shadow_replay`` (never the brief's
  ``submit_dual_curves_to_evaluator`` / ``wakeup_shadow_replay``): both brief names trip
  SEALED Phase-6 lexical red lines (p9-task-5-report N5-2, p9-task-6-report N6-1).
* N12-2 — **there is no named "Phase 5 machine re-verifier" for ``EvidenceAnchor``.**
  ``RegimeReport``/``RotationReport`` re-verify their OWN axis maps / evidence
  non-emptiness / ``evidence_factor_ids`` consistency in their model validators, and
  ``RegimeReport``'s own docstring states that RE-RESOLVING an anchor against the bound
  factor report is "a downstream responsibility". The reviewed Phase-5 e2e performs that
  resolution INLINE (``test_bootstrap_e2e.py::test_happy_path_prompt_honesty_and_
  evidence_binding``). This suite therefore CONSUMES the Phase-5 validators (by
  constructing real reports through ``seed_judgment_proxy`` / ``RotationReport.build``)
  and calls ``adapters.api.verify_point_lane0_evidence`` — the Task-12 home for the
  cross-point binding refusal — rather than re-implementing any structural Lane-0
  property (ruling R14).

Run: ``python -m pytest tests/orchestration/test_phase9_e2e.py -v``
"""
from __future__ import annotations

import asyncio
import dataclasses
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from financial_analyst.backtest.costs import CostModel

from guanlan_v2.orchestration import shadow as shadow_mod
from guanlan_v2.orchestration.adapters import luozi as lz
from guanlan_v2.orchestration.adapters.api import (
    PointEvidenceBindingRefused,
    ProductionReplayPlanCoordinator,
    SEATS_DIRECTIONS,
    derive_replay_plan_candidate_digest,
    fold_degraded_points_into_report_badges,
    persist_replay_run_compat_async,
    seats_rows_from_committed_decisions,
    verify_point_lane0_evidence,
)
from guanlan_v2.orchestration.adapters.contracts import ShadowExecutionConfig
from guanlan_v2.orchestration.adapters.luozi import (
    ReplayIntentLedger,
    ReplayRuntimeBindings,
    ReplayStateStore,
    build_dual_curves,
    derive_deterministic_targets,
    hand_off_dual_curves_to_feedback,
    mature_shadow_replay,
    persist_replay_state,
    resolve_decision_points,
    run_interval_replay,
    REPLAY_STATE_CELL_NAMESPACES,
)
from guanlan_v2.orchestration.context import ClockSpec
from guanlan_v2.orchestration.data.symbols import normalize_symbol
from guanlan_v2.orchestration.digest import content_digest
from guanlan_v2.orchestration.enums import (
    ApprovalDecision,
    ApprovalPolicy,
    Confidence,
    ExperimentStatus,
    RotationStage,
)
from guanlan_v2.orchestration.market.factors import (
    EvidenceAnchor,
    MainlineRead,
    RotationReport,
)
from guanlan_v2.orchestration.memory.experience import seed_judgment_proxy
from guanlan_v2.orchestration.refs import SchemaRef
from guanlan_v2.orchestration.runtime_clock import SystemClock
from guanlan_v2.orchestration.shadow import (
    SHADOW_MATCHING_ENGINE_VERSION,
    UnsupportedBarFrequencyError,
    compute_cutoff_at,
    compute_eligible_execution_at,
    compute_scheduled_for,
)
from guanlan_v2.seats import api as seats_api

# real, reviewed harnesses reused from the sibling Phase-9 suites.
from tests.orchestration.test_adapters_api import (
    _RecordingApproval,
    _RecordingMemory,
    _coordinator_world,
    _floors,
    _tmp_seats,
)
from tests.orchestration.test_dual_curves import (
    _FakePool,
    _MemLoader,
    _MemReader,
    _SteppingClock,
    _real_ledger,
)
from tests.orchestration.test_luozi_replay import (
    _bindings,
    _calendar,
    _request,
    _schedule,
    _utc,
)
from tests.orchestration.test_luozi_wakeup import _SpyLedger

UTC = timezone.utc
TZ = "Asia/Shanghai"
CAL_ID = "cn_a_share"
CODE = "600000"
EXCHANGE = "SH"

#: the fixture bar frame both curve lanes consume (a seed day + a rising week).
_BARS = [
    ("2026-07-03", 9.9, 10.0, 9.7, 10.0, 1e6),
    ("2026-07-06", 10.0, 10.4, 9.8, 10.0, 1e6),
    ("2026-07-07", 10.0, 10.4, 9.8, 10.2, 1e6),
    ("2026-07-08", 10.2, 11.0, 10.1, 10.8, 1e6),
    ("2026-07-09", 10.8, 11.4, 10.7, 11.0, 1e6),
]
_SESSIONS = ["2026-07-03", "2026-07-06", "2026-07-07", "2026-07-08", "2026-07-09"]
_SOURCED = content_digest({"factor_report": "market.factors", "e2e": "phase9"})


# =========================================================================== #
# one coherent world: schedule + calendar + runner + attested execution config #
# =========================================================================== #
def _sym():
    return normalize_symbol(CODE)


def _runner(*, schedule, calendar, reader=None, init_cash=1_000_000.0):
    return lz.ShadowBacktestRunner(
        reader=reader if reader is not None else _MemReader(_SESSIONS),
        loader=_MemLoader({f"{EXCHANGE}{CODE}": list(_BARS)}),
        schedule=schedule,
        schedule_ref=shadow_mod.ContentRef(
            id=schedule.id, version=schedule.version,
            content_digest=schedule.content_digest),
        calendar=calendar,
        cost_model=CostModel(),
        init_cash=init_cash,
        clock=SystemClock(),
    )


def _exec_config(runner):
    """The ONE ``ShadowExecutionConfig`` both lanes are attested against."""
    schedule = runner._schedule
    return ShadowExecutionConfig(
        universe=(_sym(),),
        init_cash=runner._init_cash,
        data_snapshot_content_digest="a" * 64,
        vintage_manifest_digest="b" * 64,
        calendar_id=runner._calendar.calendar_id,
        cost_model_digest=content_digest(dataclasses.asdict(runner._cost_model)),
        matching_engine_version=SHADOW_MATCHING_ENGINE_VERSION,
        clock=ClockSpec(as_of=_utc("2026-07-06", 14, 0), timezone=schedule.timezone,
                        calendar_id=runner._calendar.calendar_id),
        schedule_digest=schedule.content_digest,
        intrabar_exit_priority=schedule.intrabar_exit_priority,
    )


def _world():
    """schedule + calendar + the three decision points + the attested runner/config."""
    schedule = _schedule(kind="daily")
    calendar = _calendar(_SESSIONS)
    start, end = _utc("2026-07-06", 0, 0), _utc("2026-07-08", 23, 59)
    runner = _runner(schedule=schedule, calendar=calendar)
    points = resolve_decision_points(
        schedule, calendar=calendar, interval_start=start, interval_end=end)
    return SimpleNamespace(
        schedule=schedule, calendar=calendar, start=start, end=end,
        runner=runner, config=_exec_config(runner), points=points)


# --------------------------------------------------------------------------- #
# per-point Lane 0 evidence (REAL Phase-5 contracts, R2 addition)              #
# --------------------------------------------------------------------------- #
def _factor_report_at(as_of: datetime, *, temp: float = 60.0):
    """A REAL ``MarketFactorReport`` at ``as_of`` (its digest moves with the instant)."""
    from tests.orchestration.test_experience_seed import _pin_report_at

    return _pin_report_at(as_of, temp=temp)


def _lane0_reports_for(point) -> dict:
    """The point's REAL factor report + regime/rotation reads bound to THAT report.

    ``seed_judgment_proxy`` is the reviewed Phase-5 producer: it derives the regime read
    (and its ``EvidenceAnchor``s) from the supplied report and binds
    ``factor_report_digest = report.content_digest``. The rotation read is built through
    the sealed ``RotationReport.build`` validator over anchors the SAME report carries.
    """
    report = _factor_report_at(point.decision_as_of)
    regime = seed_judgment_proxy(report)
    anchor_id = report.values[0].factor_id
    rotation = RotationReport.build(
        as_of=report.as_of,
        factor_report_digest=report.content_digest,
        mainlines=(
            MainlineRead(
                name="A股温度", universe_key="theme.market_temp",
                stage=RotationStage.SPREAD, strength=6.0,
                persistence="3 consecutive sessions",
                evidence=(EvidenceAnchor(
                    factor_id=anchor_id, value=float(report.values[0].value),
                    reading=f"{anchor_id} at the decision instant"),),
            ),
        ),
        confidence=Confidence.MEDIUM,
        conflicts=(), analog_case_ids=(),
        narrative="One mainline read from the point's own factor report.",
        evidence_factor_ids=(anchor_id,),
        unknown_reason=None,
    )
    return {"factor_report": report, "regime": regime, "rotation": rotation}


# --------------------------------------------------------------------------- #
# the plan-execution seam (recorded, real-contract returns)                    #
# --------------------------------------------------------------------------- #
def _proposal_payload(point):
    return {
        "positions": ({"symbol": {"code": CODE, "exchange": EXCHANGE, "board": "main"},
                       "target_weight": 0.5},),
        "cash_weight": 0.5,
        "rationale": f"shadow advisory only; point {point.point_ordinal}",
        "confidence": Confidence.MEDIUM.value,
        "decision_as_of": point.decision_as_of,
    }


class _E2ePlanRunner:
    """The per-point plan-execution seam: bootstrap commits a ContextSnapshot, the LLM
    lane returns a committed Proposal Artifact. Records the digest it was handed."""

    def __init__(self):
        self.calls: list[dict] = []
        self.artifacts: dict[int, SimpleNamespace] = {}

    def __call__(self, *, lane, point, approval, data_context, memory_binding,
                 candidate_plan_digest):
        self.calls.append({
            "lane": lane, "ordinal": point.point_ordinal,
            "candidate_plan_digest": candidate_plan_digest,
            "approval_digest": approval.candidate_plan_digest,
            "as_of": data_context.as_of,
        })
        if lane == "bootstrap":
            # the BootstrapPlan's committed ContextSnapshot — its ``as_of`` IS the
            # frozen per-point instant (carry (b): the coordinator verifies it).
            return SimpleNamespace(
                context_snapshot=SimpleNamespace(
                    as_of=data_context.as_of,
                    content_digest=content_digest(
                        {"ctx": data_context.as_of.isoformat()})))
        proposal = shadow_mod.PortfolioTargetProposal(
            positions=(shadow_mod.TargetPosition(symbol=_sym(), target_weight=0.5),),
            cash_weight=0.5,
            rationale=f"shadow advisory only; point {point.point_ordinal}",
            confidence=Confidence.MEDIUM,
        )
        artifact = SimpleNamespace(
            payload_schema_ref=SchemaRef(name="PortfolioTargetProposal", version="1"),
            payload=proposal.model_dump(),
            artifact_id=f"artifact.proposal.{point.point_ordinal}",
            content_digest=proposal.semantic_digest(),
            decision_as_of=point.decision_as_of,
            rationale=proposal.rationale,
        )
        self.artifacts[point.point_ordinal] = artifact
        return artifact


def _coordinator(world, *, plan_runner, approval=None, lane0_reports=None,
                 budget=None, request=None, feed_floors=None):
    live, store_meta, schema_registry = _coordinator_world()
    return ProductionReplayPlanCoordinator(
        request=request, schedule=world.schedule, execution_config=world.config,
        budget=budget,
        source_config=live.config, source_registry=live.snapshot, routing=live.routing,
        store_meta=store_meta, data_snapshot_id="pit-root-e2e",
        calendar_id=CAL_ID, timezone=TZ,
        routing_snapshot_digest=live.routing.routing_digest,
        schema_registry_digest=schema_registry.registry_digest,
        feed_floors=_floors() if feed_floors is None else feed_floors,
        memory_service=_RecordingMemory(),
        memory_authority=SimpleNamespace(memory_session_id="sess-e2e"),
        prior_context_ref=SimpleNamespace(schema_ref=None, payload_ref=None),
        approval=approval or _RecordingApproval(),
        plan_runner=plan_runner,
        factor_scores=lambda point: {CODE: 0.5},
        universe=(_sym(),),
        provenance_digest=_SOURCED,
        lane0_reports=lane0_reports,
    )


# =========================================================================== #
# 1. the 落子 interval e2e                                                      #
# =========================================================================== #
def test_luozi_interval_e2e(monkeypatch, tmp_path):
    world = _world()
    request = _request(world.schedule)
    base, sink = _bindings(world.schedule, world.calendar)
    approval = _RecordingApproval()
    runner_seam = _E2ePlanRunner()
    coordinator = _coordinator(
        world, plan_runner=runner_seam, approval=approval,
        lane0_reports=_lane0_reports_for, budget=base.budget, request=request)
    bindings = ReplayRuntimeBindings(
        admission=coordinator, budget=base.budget, run_budget=base.run_budget,
        schedule_registry=base.schedule_registry, calendar=world.calendar,
        clock_factory=base.clock_factory, seats_budget_seam=base.seats_budget_seam,
        intent_ledger=ReplayIntentLedger())

    state = run_interval_replay(
        request=request, schedule=world.schedule, execution_config=world.config,
        interval_start=world.start, interval_end=world.end, bindings=bindings)

    # ── per-point PIT snapshots: every plan's context is frozen at ITS instant ──
    assert state.completed_points == 3 and state.total_points == 3
    by_point = {p.point_ordinal: p for p in world.points}
    for call in runner_seam.calls:
        assert call["as_of"] == by_point[call["ordinal"]].decision_as_of, call

    # ── carry (a): NO digest substitution inside the seam ──────────────────── #
    # the digest plan_runner was handed is EXACTLY the digest the coordinator's
    # approval gate approved, for every lane of every point.
    for call in runner_seam.calls:
        assert call["candidate_plan_digest"] == call["approval_digest"], call
    approved = [digest for _rid, digest in approval.calls]
    assert [c["candidate_plan_digest"] for c in runner_seam.calls] == approved
    assert len(approved) == 6                       # bootstrap + llm, per point

    # ── carry (c): the interval holds EXACTLY ONE plan reservation ─────────── #
    ledger_state = base.budget.replay()
    plans = [r for r in ledger_state.reservations.values() if r.scope_type == "plan"]
    assert len(plans) == 1
    derived = derive_replay_plan_candidate_digest(
        request_id=request.request_id, schedule_digest=world.schedule.content_digest,
        execution_config_digest=world.config.semantic_digest())
    assert base.budget.get_active_plan(
        request.request_id, derived).reservation_id == plans[0].reservation_id
    # the reconciliation rule: the per-point admission path reserves NODES ONLY.
    # `persist_and_reserve_candidate` mints its own plan reservation, so a production
    # plan_runner must never call it inside the interval. LOAD-BEARING: the parentage
    # walk below over the REAL ledger. SUPPLEMENT (corroborating): the coordinator
    # module does not name that call at all.
    from guanlan_v2.orchestration.adapters import api as adapters_api

    coordinator_src = Path(adapters_api.__file__).read_text(encoding="utf-8")
    assert "persist_and_reserve_candidate(" not in coordinator_src
    for reservation in ledger_state.reservations.values():
        if reservation.scope_type == "node":
            assert ledger_state.parent_of[
                reservation.reservation_id] == plans[0].reservation_id

    # ── every intent references a REAL committed Proposal Artifact + the schedule ──
    intents = bindings.intent_ledger.intents
    assert len(intents) == 3
    for intent in intents:
        artifact = runner_seam.artifacts[
            [p.point_ordinal for p in world.points
             if p.decision_as_of == intent.decision_as_of][0]]
        assert intent.proposal_artifact_id == artifact.artifact_id
        assert intent.proposal_digest == artifact.content_digest
        assert intent.decision_schedule_digest == world.schedule.content_digest
        assert intent.origin == "LLM" and intent.execution_scope == "SHADOW_ONLY"

    # ── R2 #1: each point's Lane-0 reports bind THAT point's factor report ──── #
    reports = {p.point_ordinal: _lane0_reports_for(p) for p in world.points}
    for ordinal, bundle in reports.items():
        verify_point_lane0_evidence(**bundle)         # same-point pairing accepted
        assert bundle["regime"].factor_report_digest == (
            bundle["factor_report"].content_digest)
    # a CROSS-point digest reference is refused (the classic silent leak).
    assert reports[1]["factor_report"].content_digest != (
        reports[2]["factor_report"].content_digest)
    with pytest.raises(PointEvidenceBindingRefused):
        verify_point_lane0_evidence(
            factor_report=reports[1]["factor_report"], regime=reports[2]["regime"])
    with pytest.raises(PointEvidenceBindingRefused):
        verify_point_lane0_evidence(
            factor_report=reports[1]["factor_report"],
            regime=reports[1]["regime"], rotation=reports[3]["rotation"])

    # ── R2 #2: every EvidenceAnchor re-resolves against the bound report ────── #
    for bundle in reports.values():
        factor_ids = {v.factor_id for v in bundle["factor_report"].values}
        anchors = list(bundle["regime"].evidence)
        for mainline in bundle["rotation"].mainlines:
            anchors.extend(mainline.evidence)
        assert anchors
        for anchor in anchors:
            assert anchor.factor_id in factor_ids
        # an anchor naming a factor the report does not carry is refused.
        forged = bundle["regime"].model_copy(update={
            "evidence": (EvidenceAnchor(factor_id="not.a.real.factor", value=1.0,
                                        reading="forged"),)})
        with pytest.raises(PointEvidenceBindingRefused):
            verify_point_lane0_evidence(
                factor_report=bundle["factor_report"], regime=forged)

    # ── dual curves under ONE attestation ──────────────────────────────────── #
    targets = tuple(
        derive_deterministic_targets(
            point, factor_scores={CODE: 0.5}, universe=(_sym(),),
            provenance_digest=_SOURCED)
        for point in world.points)
    report = build_dual_curves(
        execution_config=world.config, points=world.points,
        deterministic_target_sets=targets, intents=intents,
        shadow_runner=world.runner)
    attestation = world.config.semantic_digest()
    assert report.llm_shadow.execution_config_digest == attestation
    assert report.deterministic.execution_config_digest == attestation
    assert report.deterministic.applied_intent_digests == ()     # envelope-free lane
    assert report.not_causal_attribution is True
    assert report.decision_point_count == 3

    # ── carry (g): the ledger's degraded points reach the report badges ─────── #
    degraded = {2: ("archive_pre_floor:macro",)}
    folded = fold_degraded_points_into_report_badges(report, degraded)
    assert "archive_pre_floor:macro" in folded.badges
    assert "point2:archive_pre_floor:macro" in folded.badges
    assert fold_degraded_points_into_report_badges(report, {}) is report

    # ── the Phase-4 feedback handoff (immature ⇒ parked, ledger untouched) ──── #
    spy = _SpyLedger()
    pool = _FakePool()
    parked = hand_off_dual_curves_to_feedback(
        folded, run_id="replay-run.e2e", pool=pool, ledger=spy,
        maturity_now=report.interval_end - timedelta(days=1))
    assert parked.status == ExperimentStatus.WAITING_FOR_MATURITY
    assert parked.curve_report_ref is not None and spy.touched == []
    assert pool.staged and pool.committed

    # ── carry (f): committed artifacts → seats rows → the EXISTING handlers ─── #
    _tmp_seats(monkeypatch, tmp_path)
    run_head = {
        "run_id": state.run_id, "code": f"{EXCHANGE}{CODE}", "name": "浦发银行",
        "strategy_id": "seat-e2e", "strategy_name": "回放席位", "tf": "D",
        "start_date": "2026-07-06", "end_date": "2026-07-08",
        "n_buy": 1, "n_sell": 0, "n_watch": 2, "n_err": 0,
        "model": "orchestration/dec.trader",
    }
    committed = tuple(runner_seam.artifacts[o] for o in sorted(runner_seam.artifacts))
    rows = seats_rows_from_committed_decisions(committed, run_head=run_head)
    assert len(rows) == 3
    for row in rows:
        assert row["direction"] in SEATS_DIRECTIONS
        assert row["strategy_id"] == run_head["strategy_id"]   # RunPicker filters on it
        assert row["rationale"]
    # the direction is the BOOK DELTA: the run opens flat, so point 1 (0 → 0.5) is 买入
    # and the two identical follow-on books are 观望 (see test_seats_direction_matrix
    # for the 卖出 leg — an exit is a fall, which a book-absolute reading cannot see).
    assert [r["direction"] for r in rows] == [
        SEATS_DIRECTIONS[0], SEATS_DIRECTIONS[2], SEATS_DIRECTIONS[2]]

    # the async caller path goes through asyncio.to_thread (never blocks the loop).
    asyncio.run(persist_replay_run_compat_async(
        state, decisions=rows, run_head=run_head))

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    app = FastAPI()
    app.include_router(seats_api.build_seats_router())
    seats = TestClient(app)
    runs = seats.get("/seats/runs", params={"code": CODE}).json()
    assert runs["ok"] is True and runs["total"] == 1
    assert runs["runs"][0]["run_id"] == state.run_id
    assert runs["runs"][0]["strategy_id"] == "seat-e2e"
    assert runs["runs"][0]["source"] == "orchestrated"
    decisions = seats.get("/seats/decisions",
                          params={"run_id": state.run_id}).json()
    assert decisions["ok"] is True and decisions["total"] == 3
    for row in decisions["decisions"]:
        assert row["source"] == "orchestrated" and row["kind"] == "decide"
        assert row["direction"] in SEATS_DIRECTIONS
        assert "idx" not in row                    # never a frontend display coordinate


def _committed_book(ordinal: int, weight: float | None):
    """A committed proposal artifact whose book holds ``weight`` (``None`` ⇒ all cash)."""
    positions = ()
    if weight is not None:
        positions = ({"symbol": {"code": CODE, "exchange": EXCHANGE, "board": "main"},
                      "target_weight": weight},)
    return SimpleNamespace(
        payload={"positions": positions,
                 "cash_weight": 1.0 - (weight or 0.0),
                 "rationale": f"point {ordinal}"},
        artifact_id=f"artifact.book.{ordinal}",
        decision_as_of=_utc("2026-07-06", 14, 0) + timedelta(days=ordinal - 1),
    )


def _committed_multi(ordinal: int, book: dict[str, float]):
    """A committed proposal artifact holding a MULTI-symbol target book."""
    positions = tuple(
        {"symbol": {"code": code, "exchange": EXCHANGE, "board": "main"},
         "target_weight": weight}
        for code, weight in sorted(book.items()))
    return SimpleNamespace(
        payload={"positions": positions,
                 "cash_weight": max(0.0, 1.0 - sum(book.values())),
                 "rationale": f"rotation point {ordinal}"},
        artifact_id=f"artifact.multi.{ordinal}",
        decision_as_of=_utc("2026-07-06", 14, 0) + timedelta(days=ordinal - 1),
    )


def test_seats_direction_matrix_covers_every_reachable_direction():
    """carry (f): all THREE seats directions are reachable — an exit renders 卖出.

    The mapping is delta-based precisely because the shadow contract's target book is
    long-only: a book-absolute reading could only ever produce 买入 / 观望, so the
    existing seats UI would never show a sell for an orchestrated run.
    """
    run_head = {"run_id": "r", "code": f"{EXCHANGE}{CODE}", "strategy_id": "seat-m",
                "tf": "D"}
    # open (0 → 0.5) ⇒ 买入 · hold (0.5 → 0.5) ⇒ 观望 · exit (0.5 → all cash) ⇒ 卖出
    rows = seats_rows_from_committed_decisions(
        (_committed_book(1, 0.5), _committed_book(2, 0.5), _committed_book(3, None)),
        run_head=run_head)
    assert [r["direction"] for r in rows] == [
        SEATS_DIRECTIONS[0], SEATS_DIRECTIONS[2], SEATS_DIRECTIONS[1]]

    # a TRIM below τ is 观望; a trim beyond τ is 卖出 (the same dead zone as the curves).
    trims = seats_rows_from_committed_decisions(
        (_committed_book(1, 0.5), _committed_book(2, 0.40), _committed_book(3, 0.10)),
        run_head=run_head)
    assert [r["direction"] for r in trims] == [
        SEATS_DIRECTIONS[0], SEATS_DIRECTIONS[2], SEATS_DIRECTIONS[1]]

    # an explicit opening book means the run does NOT start flat: holding it is 观望.
    held = seats_rows_from_committed_decisions(
        (_committed_book(1, 0.5),), run_head=run_head, opening_book={CODE: 0.5})
    assert [r["direction"] for r in held] == [SEATS_DIRECTIONS[2]]
    exited = seats_rows_from_committed_decisions(
        (_committed_book(1, None),), run_head=run_head, opening_book={CODE: 0.5})
    assert [r["direction"] for r in exited] == [SEATS_DIRECTIONS[1]]

    # every produced direction is in the closed vocabulary, and only those three exist.
    assert SEATS_DIRECTIONS == ("买入", "卖出", "观望")
    # an unidentifiable position is refused, never mapped to a default direction.
    with pytest.raises(ValueError):
        seats_rows_from_committed_decisions(
            (SimpleNamespace(
                payload={"positions": ({"symbol": {}, "target_weight": 0.5},),
                         "cash_weight": 0.5, "rationale": "r"},
                decision_as_of=_utc("2026-07-06", 14, 0)),),
            run_head=run_head)


def test_seats_direction_multi_symbol_rotation_and_tie_break():
    """The union over BOTH books (rotation) and the documented rise-wins tie-break.

    Every single-symbol case leaves ``set(current) | set(previous)`` degenerate, so the
    union — the thing that makes a rotation legible at all — and the tie-break branch
    would never execute. Both run here.
    """
    run_head = {"run_id": "r", "code": f"{EXCHANGE}{CODE}", "strategy_id": "seat-rot",
                "tf": "D"}
    rows = seats_rows_from_committed_decisions(
        (_committed_multi(1, {"600000": 0.6}),
         # a FULL rotation: 600000 leaves the book entirely (-0.6) while 600519 enters
         # (+0.6). Only the union over both books sees the leg that left; the documented
         # rise-wins tie-break then reports the point as 买入.
         _committed_multi(2, {"600519": 0.6}),
         # a pure TRIM of the symbol that is no longer in the previous book's peer set:
         # nothing rises, one leg falls beyond τ ⇒ 卖出.
         _committed_multi(3, {"600519": 0.2}),
         # a rebalance INSIDE the dead zone on every leg ⇒ 观望.
         _committed_multi(4, {"600519": 0.3})),
        run_head=run_head)
    assert [r["direction"] for r in rows] == [
        SEATS_DIRECTIONS[0], SEATS_DIRECTIONS[0], SEATS_DIRECTIONS[1],
        SEATS_DIRECTIONS[2]]

    # the UNION in isolation — the discriminating case. A leg is DROPPED from the book
    # entirely (not written as 0.0) while the surviving leg does not move: without
    # `set(current) | set(previous)` the only delta computed would be the survivor's 0.0
    # and the point would read 观望. It must read 卖出.
    dropped = seats_rows_from_committed_decisions(
        (_committed_multi(1, {"600000": 0.6, "600519": 0.2}),
         _committed_multi(2, {"600519": 0.2})),
        run_head=run_head)
    assert [r["direction"] for r in dropped] == [
        SEATS_DIRECTIONS[0], SEATS_DIRECTIONS[1]]

    # the tie-break in isolation: one leg rises beyond τ and another falls beyond τ on
    # the SAME point — the row is 买入 (one row per point; the entry is the dominant
    # intent).
    tie = seats_rows_from_committed_decisions(
        (_committed_multi(1, {"600000": 0.5, "600519": 0.5}),
         _committed_multi(2, {"600000": 0.0, "600519": 1.0})),
        run_head=run_head)
    assert [r["direction"] for r in tie] == [SEATS_DIRECTIONS[0], SEATS_DIRECTIONS[0]]
    fall_only = seats_rows_from_committed_decisions(
        (_committed_multi(1, {"600000": 0.5, "600519": 0.5}),
         _committed_multi(2, {"600000": 0.0, "600519": 0.5})),
        run_head=run_head)
    assert [r["direction"] for r in fall_only] == [
        SEATS_DIRECTIONS[0], SEATS_DIRECTIONS[1]]


def test_seats_rows_refuse_out_of_order_decisions():
    """Point order is LOAD-BEARING for a delta reading — a shuffle is REFUSED.

    Under the old book-absolute rule order was irrelevant. It is not any more: the same
    three artifacts shuffled would silently render 观望/买入/观望 instead of
    买入/观望/卖出. The mapping refuses rather than emitting wrong user-facing rows.
    """
    run_head = {"run_id": "r", "code": f"{EXCHANGE}{CODE}", "strategy_id": "seat-o",
                "tf": "D"}
    ordered = (_committed_book(1, 0.5), _committed_book(2, 0.5), _committed_book(3, None))
    assert [r["direction"] for r in seats_rows_from_committed_decisions(
        ordered, run_head=run_head)] == [
        SEATS_DIRECTIONS[0], SEATS_DIRECTIONS[2], SEATS_DIRECTIONS[1]]

    for shuffled in ((ordered[1], ordered[0], ordered[2]),      # a swap
                     (ordered[2], ordered[1], ordered[0]),      # fully reversed
                     (ordered[0], ordered[0])):                 # a repeated instant
        with pytest.raises(ValueError, match="strictly increasing"):
            seats_rows_from_committed_decisions(shuffled, run_head=run_head)


def test_persist_compat_async_uses_a_worker_thread():
    """carry (f): the compat append is blocking file IO — it MUST run off the loop."""
    import inspect
    import threading

    # LOAD-BEARING: the append really runs on a DIFFERENT thread than the event loop.
    seen: list[int] = []
    calls: list[tuple] = []

    async def _drive():
        loop_thread = threading.get_ident()
        import guanlan_v2.orchestration.adapters.api as mod

        real = mod.persist_replay_run_compat
        try:
            mod.persist_replay_run_compat = lambda *a, **kw: (
                seen.append(threading.get_ident()), calls.append((a, kw)))[0]
            await persist_replay_run_compat_async(
                object(), decisions=(), run_head={"run_id": "r", "code": "600000"})
        finally:
            mod.persist_replay_run_compat = real
        return loop_thread

    loop_thread = asyncio.run(_drive())
    assert calls, "the async wrapper must actually call the sync append"
    assert seen and seen[0] != loop_thread, "the blocking append ran ON the event loop"
    # SUPPLEMENT (corroborating, NOT load-bearing).
    assert "asyncio.to_thread" in inspect.getsource(persist_replay_run_compat_async)
    assert inspect.iscoroutinefunction(persist_replay_run_compat_async)


def test_bootstrap_snapshot_mismatch_is_refused():
    """carry (b): a Bootstrap plan that commits a snapshot at another instant refuses."""
    world = _world()
    request = _request(world.schedule)
    base, _ = _bindings(world.schedule, world.calendar)

    class _DriftingRunner(_E2ePlanRunner):
        def __call__(self, *, lane, point, approval, data_context, memory_binding,
                     candidate_plan_digest):
            if lane == "bootstrap":
                return SimpleNamespace(context_snapshot=SimpleNamespace(
                    as_of=data_context.as_of + timedelta(days=1)))
            return super().__call__(
                lane=lane, point=point, approval=approval, data_context=data_context,
                memory_binding=memory_binding,
                candidate_plan_digest=candidate_plan_digest)

    coordinator = _coordinator(
        world, plan_runner=_DriftingRunner(), budget=base.budget, request=request)
    bindings = ReplayRuntimeBindings(
        admission=coordinator, budget=base.budget, run_budget=base.run_budget,
        schedule_registry=base.schedule_registry, calendar=world.calendar,
        clock_factory=base.clock_factory, seats_budget_seam=base.seats_budget_seam,
        intent_ledger=ReplayIntentLedger())
    with pytest.raises(shadow_mod.ShadowContractError):
        run_interval_replay(
            request=request, schedule=world.schedule, execution_config=world.config,
            interval_start=world.start, interval_end=world.end, bindings=bindings)


# =========================================================================== #
# 2. the eligible execution instant is uniquely determined                     #
# =========================================================================== #
@pytest.mark.parametrize("policy,price_field", [
    ("next_open", "open"),
    ("next_bar_close", "close"),
])
def test_eligible_time_uniqueness(policy, price_field):
    calendar = _calendar(_SESSIONS)
    schedule = _schedule(kind="daily", policy=policy, price=price_field)
    start, end = _utc("2026-07-06", 0, 0), _utc("2026-07-08", 23, 59)
    points = resolve_decision_points(
        schedule, calendar=calendar, interval_start=start, interval_end=end)
    assert len(points) == 3

    seen: set[datetime] = set()
    for point in points:
        session = point.scheduled_for.astimezone(ZoneInfo(TZ)).date().isoformat()
        # the three instants come ONLY from the Phase-6 computations (no re-derivation).
        assert point.scheduled_for == compute_scheduled_for(
            schedule, session_date=session, calendar=calendar)
        assert point.cutoff_at == compute_cutoff_at(schedule, session_date=session)
        eligible = compute_eligible_execution_at(
            schedule, scheduled_for=point.scheduled_for, calendar=calendar)
        assert point.eligible_execution_at == eligible
        # calling it again is byte-identical — the instant is a FUNCTION of the point.
        assert eligible == compute_eligible_execution_at(
            schedule, scheduled_for=point.scheduled_for, calendar=calendar)
        # the ruled time model holds, and no two points share an eligible instant.
        assert point.cutoff_at <= point.decision_as_of < point.eligible_execution_at
        assert point.eligible_execution_at not in seen
        seen.add(point.eligible_execution_at)
        # the policy/price-field pairing holds on every point.
        assert point.execution_price_field == price_field
        assert point.bar_frequency == "1d"

    # a non-1d bar frequency is refused through the consumed Phase-6 error.
    with pytest.raises(UnsupportedBarFrequencyError):
        resolve_decision_points(
            _schedule(kind="daily", policy=policy, price=price_field,
                      bar_frequency="30m"),
            calendar=calendar, interval_start=start, interval_end=end)


# =========================================================================== #
# 3. recovery — kill/replay between every pair of UoW boundaries               #
# =========================================================================== #
def test_recovery():
    """A crash between (state put / event append / head CAS) and the maturity wakeup
    applies each target exactly ONCE, while distinct order kinds / trigger bars /
    multi-fills survive intact."""
    from guanlan_v2.orchestration.eventstore import RuntimeUnitOfWork
    from tests.orchestration.test_dual_curves import (
        _alpha_frames, _points as _dc_points, _report_over, _runner as _dc_runner,
    )
    from tests.orchestration.test_luozi_wakeup import (
        _CrashBeforePublishUoW,
        _InjectedCrash,
        _env,
        _parked_state,
    )

    env = _env()
    curve_runner = _dc_runner(_alpha_frames())
    curve_points = _dc_points(curve_runner)
    report = _report_over(curve_runner, curve_points)
    ladder = lz._replay_maturity_ladder(report)
    curve_ref = env.put_report(report, run="replay-run.recovery")
    state = _parked_state(report, curve_ref, resume_after=ladder[0],
                          exp="replay.recovery", run="replay-run.recovery")

    # ── boundary 1: crash AFTER the batch is applied to the clone, BEFORE publish ──
    crash_uow = _CrashBeforePublishUoW(
        env.stores._shared, env.stores._resolver, env.stores._clock,
        allowed_cell_namespaces=env.stores._allowed_cell_namespaces,
        run_budgets=env.stores._run_budgets)
    crashing = ReplayStateStore(
        payload_store=env.stores.payloads, state_cells=env.stores.cells,
        registry=env.registry, clock=env.clock, uow_factory=lambda: crash_uow)
    before = env.stores.payloads_object_count()
    with pytest.raises(_InjectedCrash):
        persist_replay_state(state, stores=crashing, idempotency_key="recover")
    assert env.stores.payloads_object_count() == before        # nothing leaked
    assert crashing.load_head("replay.recovery") is None

    # ── the retry lands the WHOLE batch atomically, and a duplicate is a no-op ──
    persist_replay_state(state, stores=crashing, idempotency_key="recover")
    head = crashing.load_head("replay.recovery")
    assert head is not None and head.semantic_digest() == state.semantic_digest()
    after_one = env.stores.payloads_object_count()
    persist_replay_state(state, stores=crashing, idempotency_key="recover")
    assert env.stores.payloads_object_count() == after_one     # applied ONCE

    # ── boundary 2: duplicate wakeup delivery ⇒ the stored receipt, effects once ──
    ledger = _real_ledger(_SteppingClock(report.interval_end + timedelta(days=7)))
    bindings = ReplayRuntimeBindings(
        admission=SimpleNamespace(), budget=None, run_budget=None,
        schedule_registry=None, calendar=None,
        clock_factory=lambda point: SystemClock(),
        pool=_FakePool(), trial_ledger=ledger, replay_state_store=env.store)
    persist_replay_state(
        _parked_state(report, curve_ref, resume_after=ladder[0], exp="replay.dup",
                      run="replay-run.dup"),
        stores=env.store, idempotency_key="dup-park")
    parked = env.store.load_head("replay.dup")
    now = report.interval_end + timedelta(days=7)
    first = mature_shadow_replay(parked.wakeup_key, bindings=bindings, now=now)
    second = mature_shadow_replay(parked.wakeup_key, bindings=bindings, now=now)
    assert first.semantic_digest() == second.semantic_digest()
    from guanlan_v2.orchestration.adapters.luozi import _feedback_study_and_window

    study, _window = _feedback_study_and_window(report, "replay-run.dup")
    assert ledger.effective_trial_stats(study)["holdout_windows_registered"] == 1

    # ── distinct order kinds / trigger bars / multi-fills survive the replay ──── #
    from tests.orchestration.test_dual_curves import _intent_for, _pos

    fresh = _dc_runner(_alpha_frames())
    point = _dc_points(fresh, first="2026-07-20", last="2026-07-20")[0]
    intent = _intent_for(point, [_pos(0.5, stop_loss_pct=0.02)], 0.5)
    once = fresh.run((intent,), start="2026-07-20", end="2026-07-24")
    twice = _dc_runner(_alpha_frames()).run(
        (intent, intent), start="2026-07-20", end="2026-07-24")
    assert [o.order_id for o in once.orders] == [o.order_id for o in twice.orders]
    kinds = {o.order_kind for o in once.orders}
    assert {"target_buy", "stop_loss"} <= kinds       # distinct kinds NOT collapsed
    assert len({o.order_id for o in once.orders}) == len(once.orders)
    assert len({(o.order_kind, getattr(o, "trade_date", None)) for o in once.orders}) >= 2
    assert [n for _d, n in once.nav_history] == [n for _d, n in twice.nav_history]
    assert isinstance(crash_uow, RuntimeUnitOfWork)


# =========================================================================== #
# 4. the 帷幄 ONLINE e2e                                                        #
# =========================================================================== #
def test_weiwo_e2e(tmp_path):
    from guanlan_v2.factorlib.store import LibraryFactorStore
    from guanlan_v2.orchestration.adapters.weiwo import run_weiwo_research
    from guanlan_v2.orchestration.spec import OrchestrationRequest
    from tests.orchestration.test_weiwo_adapter import (
        AS_OF as WEIWO_AS_OF,
        _AdvancingClock,
        _FakeApproval,
        _FakeOptimizer,
        _FakePlanner,
        _FakeRejectionSink,
        _ProbeStub,
        _World,
        _bindings as _weiwo_bindings,
        _candidate,
        _ok_env,
    )
    from tests.orchestration.test_redline_regression import _weiwo_schema_registry

    clock = _AdvancingClock(WEIWO_AS_OF)          # advancing ⇒ proves the as_of freeze
    world = _World(_weiwo_schema_registry(), clock=clock,
                   probe_stub=_ProbeStub(_ok_env(items=[])))
    mined = tmp_path / "factorlib"
    mined.mkdir(parents=True, exist_ok=True)
    store = LibraryFactorStore(mined_dir=mined)
    passing = _candidate(name="lib_e2e_pass")
    failing = _candidate(name="lib_e2e_fail")
    rejections = _FakeRejectionSink()
    optimizer = _FakeOptimizer(passing=(passing,), failing=(failing,), invoke_eval=True)
    receipt = run_weiwo_research(
        OrchestrationRequest(
            request_id="req-weiwo-e2e", goal="研究:动量与换手因子",
            workflow="orchestrate_and_optimize", approval_policy=ApprovalPolicy.REQUIRED),
        bindings=_weiwo_bindings(
            world, planner=_FakePlanner(passing),
            approval=_FakeApproval(ApprovalDecision.APPROVED), optimizer=optimizer,
            rejection_sink=rejections, factor_store=store))

    # ── the optimizer really drove the run_graph binding (口径-identical metrics) ──
    assert optimizer.eval_result is not None
    assert optimizer.eval_result.source == "run_graph"

    # ── every product landed DRAFT-ONLY ────────────────────────────────────── #
    assert receipt.stop_reason == "completed"
    assert receipt.draft_ids == ("lib_e2e_pass",)
    landed = sorted(mined.rglob("*.json"))
    assert landed, "the passing candidate must have landed in the temp factorlib"
    names = set()
    for path in landed:
        records = json.loads(path.read_text(encoding="utf-8"))
        for record in (records if isinstance(records, list) else [records]):
            names.add(str(record.get("name") or ""))
            assert str(record.get("status") or "") == "draft", record.get("status")
            assert str(record.get("source") or "") == "orchestration_weiwo"
    assert "lib_e2e_pass" in names

    # ── the rejected candidate left NO factorlib trace and no invented status ── #
    assert "lib_e2e_fail" not in names
    assert not any("lib_e2e_fail" in p.name for p in landed)
    assert [reason for _c, reason in rejections.records] == ["gate_failed"]

    # ── as_of stayed frozen even though the clock advanced during the run ───── #
    assert clock.reads >= 1
    assert receipt.context_snapshot_digest


def test_weiwo_production_binding_red_lines(tmp_path):
    """Task-7 review carry: the PRODUCTION 帷幄 binding enforces three obligations the
    optional dataclass fields cannot."""
    from guanlan_v2.orchestration.adapters.weiwo import (
        ProductionWeiwoApprovalGate,
        WeiwoApprovalPolicyRefused,
        WeiwoSnapshotBindingError,
        build_weiwo_production_bindings,
        resolve_weiwo_capability_binding,
        save_draft_to_factorlib,
    )
    from guanlan_v2.orchestration.data.catalog import phase3_data_surface
    from guanlan_v2.orchestration.spec import OrchestrationRequest
    from tests.orchestration.test_weiwo_adapter import (
        AS_OF as WEIWO_AS_OF,
        _FakeMemoryPreparer,
        _FakeOptimizer,
        _FakePlanner,
        _FakeRejectionSink,
        _FrozenClock,
        _ProbeStub,
        _World,
        _candidate,
        _ok_env,
    )
    from tests.orchestration.test_redline_regression import _weiwo_schema_registry

    world = _World(_weiwo_schema_registry(), clock=_FrozenClock(WEIWO_AS_OF),
                   probe_stub=_ProbeStub(_ok_env(items=[])))
    capability = resolve_weiwo_capability_binding(
        method_specs=phase3_data_surface().method_specs)
    common = dict(
        clock=world.clock, source_config=world.config, source_registry=world.snapshot,
        routing=world.routing, manifest=world.manifest,
        memory_preparer=_FakeMemoryPreparer(), planner=_FakePlanner(_candidate()),
        optimizer=_FakeOptimizer(), rejection_sink=_FakeRejectionSink(),
        capability_binding=capability)
    gate = ProductionWeiwoApprovalGate(lambda request, plan: ApprovalDecision.APPROVED)

    # ① the DataRequest as_of re-assertion is MANDATORY in the production binding.
    for dropped in ("schema_registry", "request_method_spec", "request_params"):
        anchor = dict(schema_registry=world.schema_registry,
                      request_method_spec=world.spec, request_params=world.params)
        anchor[dropped] = None
        with pytest.raises(WeiwoSnapshotBindingError):
            build_weiwo_production_bindings(approval=gate, **common, **anchor)

    bindings = build_weiwo_production_bindings(
        approval=gate, schema_registry=world.schema_registry,
        request_method_spec=world.spec, request_params=world.params, **common)
    # ③ factor_saver stays the guarded draft-only landing (never parameterised here).
    assert bindings.factor_saver is save_draft_to_factorlib

    # ② the production approval port NEVER auto-approves under AUTO.
    with pytest.raises(WeiwoApprovalPolicyRefused):
        gate.decide(
            OrchestrationRequest(request_id="req-auto", goal="g",
                                 workflow="orchestrate_and_optimize",
                                 approval_policy=ApprovalPolicy.AUTO),
            object())
    # …and a non-ApprovalDecision answer is refused, never coerced into "approved".
    with pytest.raises(WeiwoApprovalPolicyRefused):
        ProductionWeiwoApprovalGate(lambda request, plan: "yes").decide(
            OrchestrationRequest(request_id="req-str", goal="g",
                                 workflow="orchestrate_and_optimize",
                                 approval_policy=ApprovalPolicy.REQUIRED),
            object())
    # a bare callable can never be the production approval port.
    with pytest.raises(WeiwoApprovalPolicyRefused):
        build_weiwo_production_bindings(
            approval=lambda request, plan: ApprovalDecision.APPROVED,
            schema_registry=world.schema_registry, request_method_spec=world.spec,
            request_params=world.params, **common)


def test_replay_feed_floors_default_readers_are_bound(tmp_path, monkeypatch):
    """Task-10 review carry (d): the DEFAULT-reader branch of ``build_replay_feed_floors``
    binds the REAL ``snapshot_archive.read_archive`` and the REAL macro-pulse surface."""
    from guanlan_v2.datafeed import snapshot_archive as sa
    from guanlan_v2.orchestration.adapters.api import build_replay_feed_floors
    from guanlan_v2.orchestration.adapters import replay_data

    # ① the default archive reader IS the real one, resolved through the module's own
    #    root resolver so the env override a 9998 verification run uses is honoured.
    override = tmp_path / "archive-empty"
    monkeypatch.setenv("GUANLAN_SNAPSHOT_ARCHIVE_DIR", str(override))
    macro = tmp_path / "snapshots.jsonl"
    macro.write_text(json.dumps({"ts": "2026-01-20T15:00:00"}) + "\n", encoding="utf-8")

    seen: list[str] = []
    real_read = sa.read_archive
    monkeypatch.setattr(
        sa, "read_archive",
        lambda kind, *a, **kw: (seen.append(kind), real_read(kind, *a, **kw))[1])
    floors = build_replay_feed_floors(macro_snapshots_path=macro)   # NO read_archive arg
    assert seen == list(sa.KINDS), "the default branch must call the REAL read_archive"
    by_id = {f.feed_id: f for f in floors}
    assert set(by_id) == set(sa.KINDS) | {"macro"}
    # an empty archive is an honest missing floor — never a fabricated date.
    assert all(by_id[k].floor_date is None for k in sa.KINDS)
    assert all(by_id[k].archive_root == str(override) for k in sa.KINDS)
    assert by_id["macro"].floor_date == "2026-01-20"

    # ② the default macro path IS the real macro-pulse snapshot surface — proven by
    #    redirecting that surface's OWN default to a fixture and calling the builder with
    #    NO snapshots_path. The real `var/macro_pulse/snapshots.jsonl` is never read: it
    #    is rotated live by another process, so reading it would make this suite
    #    non-hermetic (and violate the fixtures-only invariant).
    from guanlan_v2.macro import pulse as macro_pulse

    fixture = tmp_path / "macro_default.jsonl"
    fixture.write_text(
        json.dumps({"ts": "2026-02-03T15:00:00"}) + "\n"
        + json.dumps({"ts": "2026-01-11T15:00:00"}) + "\n", encoding="utf-8")
    monkeypatch.setattr(macro_pulse, "_SNAP_DEFAULT", fixture)
    default_macro = replay_data.macro_feed_floor_from_snapshots()
    assert default_macro.feed_id == "macro"
    assert default_macro.floor_date == "2026-01-11"    # the EARLIEST snapshot's date
    assert str(default_macro.archive_root) == str(fixture)
    # and the module the builder binds really is the macro-pulse surface.
    assert "macro" in macro_pulse.__name__ and hasattr(macro_pulse, "_read_snapshots")


# =========================================================================== #
# 5. the maturity chain (immature → parked → scheduler wakeup → feedback once)  #
# =========================================================================== #
def test_maturity_chain(tmp_path, monkeypatch):
    """The chain across a PROCESS RESTART: the daily scheduler parks on day N and wakes
    on day N+1 in a fresh process, so the parked-head enumeration must be DURABLE."""
    from guanlan_v2.autonomy import playbooks
    from guanlan_v2.orchestration.adapters.contracts import (
        DualCurveReport, ReplayWakeupReceipt, ShadowReplayRunState,
    )
    from guanlan_v2.orchestration.adapters.durable import build_durable_runtime_stores
    from guanlan_v2.orchestration.eventstore import SchemaRegistryResolver
    from guanlan_v2.orchestration.refs import SchemaRef, TypedPayloadRef
    from guanlan_v2.orchestration.runtime_contracts import Phase2RuntimeRegistry
    from tests.orchestration.test_dual_curves import (
        _alpha_frames, _points as _dc_points, _report_over, _runner as _dc_runner,
    )
    from tests.orchestration.test_luozi_wakeup import _parked_state

    registry = Phase2RuntimeRegistry()
    for model in (ShadowReplayRunState, ReplayWakeupReceipt, DualCurveReport):
        registry.register(model)
    registry.seal()
    root = tmp_path / "orchestration"

    def _open_stores():
        resolver = SchemaRegistryResolver()
        digest = resolver.register(registry)
        stores = build_durable_runtime_stores(
            root, resolver=resolver, clock=SystemClock(),
            allowed_cell_namespaces=REPLAY_STATE_CELL_NAMESPACES)
        store = ReplayStateStore(
            payload_store=stores.payloads, state_cells=stores.cells,
            registry=registry, clock=SystemClock(),
            uow_factory=lambda: stores.unit_of_work,
            event_store=stores.events)              # the DURABLE parked-head index
        return stores, store, digest

    curve_runner = _dc_runner(_alpha_frames())
    report = _report_over(curve_runner, _dc_points(curve_runner))

    # ── day N: the interval is immature ⇒ the run parks (ledger untouched) ──── #
    spy = _SpyLedger()
    parked_state = hand_off_dual_curves_to_feedback(
        report, run_id="replay-run.chain", pool=_FakePool(), ledger=spy,
        maturity_now=report.interval_end - timedelta(days=1))
    assert parked_state.status == ExperimentStatus.WAITING_FOR_MATURITY
    assert spy.touched == []                       # no feedback before maturity

    stores_a, store_a, digest_a = _open_stores()
    ref = stores_a.payloads.put(
        SchemaRef(name="DualCurveReport", version="1"), report,
        registry_digest=digest_a, namespace="main", idempotency_key="chain-report")
    curve_ref = TypedPayloadRef(
        schema_ref=SchemaRef(name="DualCurveReport", version="1"), payload_ref=ref)
    ladder = lz._replay_maturity_ladder(report)
    head = _parked_state(report, curve_ref, resume_after=ladder[0],
                         exp="replay.chain", run="replay-run.chain")
    persist_replay_state(head, stores=store_a, idempotency_key="chain-park")
    assert [s.experiment_id for s in store_a.waiting_states()] == ["replay.chain"]

    # ── day N+1: a FRESH process — the in-process map is empty by construction ─ #
    stores_b, store_b, _digest_b = _open_stores()
    assert store_b._waiting == {}, "a fresh process starts with no in-memory index"
    recovered = store_b.waiting_states()
    assert [s.experiment_id for s in recovered] == ["replay.chain"], (
        "the parked head must be found by a DURABLE scan, not an in-memory map")
    assert recovered[0].wakeup_key == head.wakeup_key

    # ── the scheduler playbook drives the matured batch through the real consumer ─
    ledger = _real_ledger(_SteppingClock(report.interval_end + timedelta(days=7)))
    bindings = ReplayRuntimeBindings(
        admission=_NeverExecutes(), budget=None, run_budget=None,
        schedule_registry=None, calendar=None,
        clock_factory=lambda point: SystemClock(),
        pool=_FakePool(), trial_ledger=ledger, replay_state_store=store_b)
    now = report.interval_end + timedelta(days=7)
    playbooks.set_shadow_wakeup_context_provider(lambda: (store_b, bindings, now))
    try:
        out = playbooks.PLAYBOOKS["shadow_replay_wakeup"](SimpleNamespace())
    finally:
        playbooks.set_shadow_wakeup_context_provider(None)
    assert out["ok"] is True
    assert out["report"]["count"] == 1
    assert out["report"]["wakeups"][0]["experiment_id"] == "replay.chain"
    assert out["report"]["wakeups"][0]["outcome"] == "completed"

    # ── the evaluator feedback happened EXACTLY once (append-only maturity chain) ─
    from guanlan_v2.orchestration.adapters.luozi import _feedback_study_and_window

    study, _window = _feedback_study_and_window(report, "replay-run.chain")
    assert ledger.effective_trial_stats(study)["holdout_windows_registered"] == 1
    completed = store_b.load_head("replay.chain")
    assert completed.status == ExperimentStatus.COMPLETED
    assert store_b.waiting_states() == ()          # the head left the parked set

    # a second scheduler pass finds nothing to do and registers nothing new.
    playbooks.set_shadow_wakeup_context_provider(lambda: (store_b, bindings, now))
    try:
        again = playbooks.PLAYBOOKS["shadow_replay_wakeup"](SimpleNamespace())
    finally:
        playbooks.set_shadow_wakeup_context_provider(None)
    assert again["report"]["count"] == 0
    assert ledger.effective_trial_stats(study)["holdout_windows_registered"] == 1


class _NeverExecutes:
    """A coordinator spy: the maturity consumer must never re-execute a decision point."""

    def bootstrap_context(self, point):  # pragma: no cover - must never run
        raise AssertionError("the maturity wakeup must not admit/run a decision point")

    def llm_proposal(self, point, snapshot):  # pragma: no cover - must never run
        raise AssertionError("the maturity wakeup must not re-run the LLM lane")

    def deterministic_targets(self, point, snapshot):  # pragma: no cover
        raise AssertionError("the maturity wakeup must not re-run the deterministic lane")


# =========================================================================== #
# cross-suite aggregation — the exit-gate signal this suite belongs to          #
# =========================================================================== #
def test_this_suite_is_offline_and_is_the_retirement_precondition_signal():
    """Invariant 2: ``pytest tests/orchestration -v`` is the single green signal spec
    §12.9's retirement precondition refers to, and BOTH Task-12 suites are named by the
    frozen retirement gates."""
    from guanlan_v2.orchestration.adapters import retirement as rt

    selectors = {
        token
        for gate in rt.default_retirement_gates()
        for criterion in gate.criteria
        if criterion.evidence_kind == "pytest_suite"
        for token in criterion.evidence_selector.split()
    }
    assert "tests/orchestration/test_redline_regression.py" in selectors
    assert any(s.startswith("tests/orchestration/test_phase9_e2e.py") for s in selectors)

    # nothing in this module reaches a network endpoint or the live server.
    import ast
    from pathlib import Path

    tree = ast.parse(Path(__file__).read_text(encoding="utf-8"))
    banned = {"httpx", "requests", "aiohttp", "urllib", "socket", "akshare", "tushare"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name.split(".")[0] not in banned, alias.name
        elif isinstance(node, ast.ImportFrom):
            assert (node.module or "").split(".")[0] not in banned, node.module
