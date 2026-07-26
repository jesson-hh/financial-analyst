# -*- coding: utf-8 -*-
"""R22 — the pending approval cards the replay coordinator actually demands.

Before this suite existed, ``PlanApprovalCoordinator.register_pending`` had **zero**
production callers, so no human-approvable card existed for either lane digest
``ProductionReplayPlanCoordinator._require_approval`` looks up: every shadow-replay
run refused at its first decision point with ``ReplayCoordinatorApprovalRefused``
even though a human had genuinely approved the card ``/replay/start`` publishes
(a THIRD, unrelated digest).

What is proven here, all against real components:

* the digest arithmetic — the coordinator's *default* lane digests are reproduced
  exactly by :func:`coordinator_default_lane_candidate_digests`, and are then shown
  to be **undecidable** on the production path (a real ``PlanAdmissionService``
  refuses ``record_approval`` for a digest that is not one of its prepared
  candidates), which is why the sealer binds the REAL admission candidate digests
  and hands them back as the coordinator's constructor overrides;
* the card binds the plan — a ``candidate_plan_digest`` that does not recompute
  from ``(request, draft, context)`` through the Phase-1
  ``compute_candidate_plan_digest`` is refused, so an approval can never authorize
  a plan the human did not see;
* the ``PlanDiff@1`` payload is really committed into a real payload store and the
  card's ``rendered_md`` re-derives from the resolved payload;
* **the R22 acceptance** — one human moment (two ``register_pending`` cards, two
  ``decide`` calls by a real allowlisted operator through the real
  ``ConfigOperatorVerifier``) drives a real three-point ``run_interval_replay`` in
  which BOTH lanes pass ``_require_approval``; the same run without the cards
  still raises the exact refusal that blocks today.

Run: ``python -m pytest tests/orchestration/test_replay_approval_cards.py -v``
"""
from __future__ import annotations

import dataclasses
import inspect
import json
from types import SimpleNamespace

import pytest

from guanlan_v2.orchestration.adapters.api import (
    ProductionReplayPlanCoordinator,
    ReplayCoordinatorApprovalRefused,
    build_plan_approval_coordinator,
    derive_replay_plan_candidate_digest,
    derive_replay_start_candidate_digest,
)
from guanlan_v2.orchestration.adapters.identity import (
    ConfigOperatorVerifier,
    DEFAULT_OPERATOR_ALLOWLIST_PATH,
    OperatorAllowlistError,
    declared_operator_actor,
)
from guanlan_v2.orchestration.adapters.luozi import (
    ReplayIntentLedger,
    ReplayRuntimeBindings,
    run_interval_replay,
)
from guanlan_v2.orchestration.admission import AdmissionRejected, PlanAdmissionService
from guanlan_v2.orchestration.approval import ApprovalDecisionConflict
from guanlan_v2.orchestration.digest import content_digest
from guanlan_v2.orchestration.enums import ApprovalDecision, ApprovalPolicy, PlanSource
from guanlan_v2.orchestration.plan_diff import (
    PLAN_DIFF_SCHEMA_REF,
    PlanDiff,
    render_plan_diff_md,
)
from guanlan_v2.orchestration.runtime_contracts import static_runtime_profile

from guanlan_v2.orchestration.adapters.replay_cards import (
    REPLAY_LANES,
    ReplayApprovalCards,
    ReplayCardRefused,
    ReplayLanePlan,
    ReplayLaneUnknown,
    build_replay_lane_card,
    coordinator_default_lane_candidate_digests,
    register_replay_approval_cards,
)

from tests.orchestration import test_dynamic_e2e as e2e
from tests.orchestration.test_adapters_api import (
    _RecordingMemory,
    _RecordingRunner,
    _coordinator_world,
    _floors,
)
from guanlan_v2.orchestration.spec import OrchestrationRequest

from tests.orchestration.test_luozi_replay import (
    _bindings,
    _exec_config,
    _proposal_artifact,
    _schedule_ref,
    _three_point_env,
)

TZ = "Asia/Shanghai"
#: the driver hardcodes ``replay-run.{request_id}``; the reused budget-sink harness
#: is stamped for ``req-replay``, so every env here uses that request id.
REQUEST_ID = "req-replay"


# =========================================================================== #
# a real world: real admission service + two real, card-eligible lane drafts   #
# =========================================================================== #
class _World:
    """The e2e env plus the two prepared+reserved lane candidates.

    ``bootstrap`` is the reviewed ``PRESET_FALLBACK`` research-baseline plan and
    ``main`` the dynamic Planner's ``DYNAMIC`` candidate: the only two *real*,
    card-eligible drafts this repository can produce today. Which concrete plan a
    lane runs is the launcher's binding (R3/R18) — what is proven here is the
    mechanism, with nothing synthesized.
    """

    def __init__(self, env, service, plans, phase7_digest):
        self.env = env
        self.service = service
        self.plans = plans
        self.phase7_digest = phase7_digest

    @property
    def request(self):
        return self.env.request

    @property
    def payloads(self):
        return self.env.stores.payloads


def _phase7_digest(env) -> str:
    """Register the Phase-7 cumulative registry so ``PlanDiff@1`` resolves in the store."""
    import guanlan_v2.orchestration.phase7_registry as p7

    return env.resolver.register(p7.build_phase7_registry(p7.PHASE7_BASE_REGISTRY_DIGEST))


def _world(tmp_path, *, run_id="run-r22") -> _World:
    env = e2e._build_env(request_id=REQUEST_ID,
                         fallback_preset_id=e2e.RESEARCH_BASELINE, run_id=run_id)
    # the SAME request must serve the admission service and the replay driver, so it
    # binds the registered decision schedule the shadow envelope requires. Swapped
    # before any draft is materialized, so every candidate digest binds this request.
    sch, _cal, _s, _e = _three_point_env()
    env.request = OrchestrationRequest(
        request_id=REQUEST_ID, goal=env.request.goal, workflow="orchestrate_only",
        fallback_preset_id=e2e.RESEARCH_BASELINE,
        approval_policy=ApprovalPolicy.REQUIRED,
        decision_schedule_ref=_schedule_ref(sch))
    fallback = e2e._materialize_fallback(env, draft_id="plan-r22-fallback")
    planner = e2e._run_dynamic_planner(env, [e2e._valid_output()])
    dynamic = planner.draft
    assert dynamic is not None and dynamic.source is PlanSource.DYNAMIC

    service = PlanAdmissionService(
        run_id=env.run_id, requests={env.request.request_id: env.request},
        drafts={fallback.id: fallback, dynamic.id: dynamic},
        contexts={env.context.content_digest: env.context},
        attestations={}, approvals=env.approvals, catalog=env.catalog_runtime,
        bridge_view=env.view, phase1_registry=env.registry,
        runtime_registry_digest=env.rt_digest, profile=static_runtime_profile(),
        stores=env.stores, run_budget=env.run_budget, clock=env.clock)

    preset = e2e._preset_registry().get(e2e.RESEARCH_BASELINE)
    plans = {}
    for lane, draft, extra in (
        ("bootstrap", fallback, dict(preset_id=e2e.RESEARCH_BASELINE,
                                     preset_record_digest=preset.semantic_digest())),
        ("main", dynamic, {}),
    ):
        prep = service.prepare_candidate(draft.id, request_id=env.request.request_id)
        service.persist_and_reserve_candidate(prep, idempotency_key=f"reserve-{lane}")
        plans[lane] = ReplayLanePlan(
            lane=lane, draft=draft,
            candidate_plan_digest=prep.candidate_plan_digest,
            context_content_digest=prep.context_content_digest, **extra)
    assert plans["bootstrap"].candidate_plan_digest != plans["main"].candidate_plan_digest
    return _World(env, service, plans, _phase7_digest(env))


def _coord(world, tmp_path, *, verifier=None, name="plan_approvals.jsonl"):
    """The REAL Phase-7 coordinator, built through the production builder."""
    return build_plan_approval_coordinator(
        admission=world.service, clock=world.env.clock,
        verifier=verifier if verifier is not None else ConfigOperatorVerifier(),
        approvals=world.env.approvals, journal_path=tmp_path / name)


def _register(world, coord):
    sch, _cal, _s, _e = _three_point_env()
    return register_replay_approval_cards(
        coordinator=coord, request=world.request, schedule=sch,
        execution_config=_exec_config(sch), lane_plans=world.plans,
        payloads=world.payloads, registry_digest=world.phase7_digest)


def _decide_all(coord, cards, *, decision=ApprovalDecision.APPROVED):
    actor = declared_operator_actor()
    out = {}
    for lane in REPLAY_LANES:
        digest = cards.candidate_plan_digest(lane)
        out[lane] = coord.decide(
            request_id=cards.request_id, candidate_plan_digest=digest,
            decision=decision, actor=actor, reason=f"reviewed the {lane} lane",
            idempotency_key=f"decide-{lane}")
    return out


def _replay_coordinator(world, *, approval, overrides=None):
    """A REAL ``ProductionReplayPlanCoordinator`` over the frozen Task-2 PIT world."""
    from guanlan_v2.orchestration.data.symbols import normalize_symbol

    live, store_meta, schema_registry = _coordinator_world()
    sch, cal, start, end = _three_point_env()
    binds, _sink = _bindings(sch, cal)
    runner = _RecordingRunner(_proposal_artifact)
    coord = ProductionReplayPlanCoordinator(
        request=world.request, schedule=sch, execution_config=_exec_config(sch),
        budget=binds.budget,
        source_config=live.config, source_registry=live.snapshot, routing=live.routing,
        store_meta=store_meta, data_snapshot_id="pit-root-r22",
        calendar_id="cn_a_share", timezone=TZ,
        routing_snapshot_digest=live.routing.routing_digest,
        schema_registry_digest=schema_registry.registry_digest,
        feed_floors=_floors(),
        memory_service=_RecordingMemory(),
        memory_authority=SimpleNamespace(memory_session_id="sess-r22"),
        prior_context_ref=SimpleNamespace(schema_ref=None, payload_ref=None),
        approval=approval,
        plan_runner=runner,
        factor_scores=lambda point: {"600519": 0.4},
        universe=(normalize_symbol("600519"),),
        provenance_digest="f" * 64,
        **(overrides or {}))
    bindings = ReplayRuntimeBindings(
        admission=coord, budget=binds.budget, run_budget=binds.run_budget,
        schedule_registry=binds.schedule_registry, calendar=cal,
        clock_factory=binds.clock_factory, seats_budget_seam=binds.seats_budget_seam,
        intent_ledger=ReplayIntentLedger())
    return coord, bindings, (sch, start, end), runner


def _rows(path):
    return [json.loads(line) for line in
            path.read_text(encoding="utf-8").splitlines() if line.strip()]


# =========================================================================== #
# 1. the digest arithmetic — and why the coordinator's DEFAULTS are a dead end #
# =========================================================================== #
def test_default_lane_digests_reproduce_the_coordinators_own_defaults(tmp_path):
    """The helper must be byte-identical to what the sealed coordinator computes."""
    world = _world(tmp_path)
    coord, _bindings_, _env, _runner = _replay_coordinator(
        world, approval=SimpleNamespace(load_decision=lambda *a: None))
    sch, _cal, _s, _e = _three_point_env()
    plan_digest = derive_replay_plan_candidate_digest(
        request_id=world.request.request_id, schedule_digest=sch.content_digest,
        execution_config_digest=_exec_config(sch).semantic_digest())
    defaults = coordinator_default_lane_candidate_digests(
        plan_candidate_digest=plan_digest)
    assert defaults["bootstrap"] == coord._bootstrap_candidate
    assert defaults["main"] == coord._main_candidate
    assert set(defaults) == set(REPLAY_LANES)


def test_the_coordinator_default_digests_can_never_be_decided(tmp_path):
    """WHY the sealer binds real admission candidates: the synthetic default lane
    digest is not a prepared candidate, so a real ``PlanAdmissionService`` refuses
    the ``record_approval`` every ``decide`` performs — the default is fail-closed
    by construction and can never carry a human decision."""
    world = _world(tmp_path)
    sch, _cal, _s, _e = _three_point_env()
    plan_digest = derive_replay_plan_candidate_digest(
        request_id=world.request.request_id, schedule_digest=sch.content_digest,
        execution_config_digest=_exec_config(sch).semantic_digest())
    default = coordinator_default_lane_candidate_digests(
        plan_candidate_digest=plan_digest)["bootstrap"]
    from guanlan_v2.orchestration.admission import ApprovalSubmission

    with pytest.raises(AdmissionRejected) as exc:
        world.service.record_approval(
            default,
            ApprovalSubmission(request_id=world.request.request_id,
                               candidate_plan_digest=default,
                               decision=ApprovalDecision.APPROVED),
            authenticated_actor=declared_operator_actor(), idempotency_key="x")
    assert exc.value.code == "unknown_candidate"


# =========================================================================== #
# 2. the card binds the plan the digest was minted from                        #
# =========================================================================== #
def test_the_card_is_derived_from_the_real_draft(tmp_path):
    world = _world(tmp_path)
    plan = world.plans["main"]
    card = build_replay_lane_card(
        plan=plan, request=world.request, payloads=world.payloads,
        registry_digest=world.phase7_digest, requested_at=world.env.clock.now())
    assert card.request_id == world.request.request_id
    assert card.candidate_plan_digest == plan.candidate_plan_digest
    assert card.approval_policy is ApprovalPolicy.REQUIRED
    assert card.source is plan.draft.source
    assert card.node_count == len(plan.draft.nodes)
    assert card.worker_ids == tuple(sorted({n.worker_id for n in plan.draft.nodes}))
    assert card.budget_request_tokens == plan.draft.budget_request_tokens
    assert card.goal == world.request.goal


def test_a_digest_that_does_not_bind_the_draft_is_refused(tmp_path):
    """The one property that makes the approval mean something: the card can only
    be minted under the digest the Phase-1 rule derives from THIS draft."""
    world = _world(tmp_path)
    plan = world.plans["main"]
    forged = ReplayLanePlan(
        lane="main", draft=plan.draft, candidate_plan_digest="9" * 64,
        context_content_digest=plan.context_content_digest)
    with pytest.raises(ReplayCardRefused) as exc:
        build_replay_lane_card(
            plan=forged, request=world.request, payloads=world.payloads,
            registry_digest=world.phase7_digest, requested_at=world.env.clock.now())
    assert "recompute" in str(exc.value)

    # …and swapping the two lanes' digests is refused for the same reason.
    swapped = ReplayLanePlan(
        lane="main", draft=plan.draft,
        candidate_plan_digest=world.plans["bootstrap"].candidate_plan_digest,
        context_content_digest=plan.context_content_digest)
    with pytest.raises(ReplayCardRefused):
        build_replay_lane_card(
            plan=swapped, request=world.request, payloads=world.payloads,
            registry_digest=world.phase7_digest, requested_at=world.env.clock.now())


def test_the_plan_diff_payload_is_really_committed_and_rebinds(tmp_path):
    world = _world(tmp_path)
    plan = world.plans["bootstrap"]
    card = build_replay_lane_card(
        plan=plan, request=world.request, payloads=world.payloads,
        registry_digest=world.phase7_digest, requested_at=world.env.clock.now())
    assert card.plan_diff_ref.schema_ref == PLAN_DIFF_SCHEMA_REF
    resolved = world.payloads.get(card.plan_diff_ref.payload_ref,
                                  expected_schema_ref=PLAN_DIFF_SCHEMA_REF)
    assert isinstance(resolved, PlanDiff)
    assert resolved.candidate_plan_digest == plan.candidate_plan_digest
    assert resolved.semantic_digest() == card.rendered_from_diff_digest
    # the reviewer text re-derives from the STORED payload — a tampered rendering
    # could not survive this.
    assert render_plan_diff_md(resolved) == card.rendered_md


def test_committing_the_same_card_twice_reuses_one_payload(tmp_path):
    world = _world(tmp_path)
    plan = world.plans["main"]
    kw = dict(plan=plan, request=world.request, payloads=world.payloads,
              registry_digest=world.phase7_digest,
              requested_at=world.env.clock.now())
    first = build_replay_lane_card(**kw)
    second = build_replay_lane_card(**kw)
    assert first.plan_diff_ref == second.plan_diff_ref
    assert first.semantic_digest() == second.semantic_digest()


# =========================================================================== #
# 3. the refusals                                                              #
# =========================================================================== #
def test_a_preset_sourced_draft_is_refused_with_the_structural_reason(tmp_path):
    """Phase-7's card model accepts only DYNAMIC / PRESET_FALLBACK. The Phase-5
    Lane-0 bootstrap draft is ``PlanSource.PRESET``, so it cannot be carded — the
    sealer says so instead of relabelling a preset as a fallback."""
    world = _world(tmp_path)
    plan = world.plans["main"]
    preset_draft = plan.draft.model_copy(update={"source": PlanSource.PRESET})
    refused = ReplayLanePlan(
        lane="main", draft=preset_draft,
        candidate_plan_digest=plan.candidate_plan_digest,
        context_content_digest=plan.context_content_digest)
    with pytest.raises(ReplayCardRefused) as exc:
        build_replay_lane_card(
            plan=refused, request=world.request, payloads=world.payloads,
            registry_digest=world.phase7_digest, requested_at=world.env.clock.now())
    assert "preset" in str(exc.value).lower()


def test_an_auto_policy_draft_is_refused(tmp_path):
    world = _world(tmp_path)
    plan = world.plans["main"]
    auto = plan.draft.model_copy(update={"approval_policy": ApprovalPolicy.AUTO})
    refused = ReplayLanePlan(
        lane="main", draft=auto, candidate_plan_digest=plan.candidate_plan_digest,
        context_content_digest=plan.context_content_digest)
    with pytest.raises(ReplayCardRefused) as exc:
        build_replay_lane_card(
            plan=refused, request=world.request, payloads=world.payloads,
            registry_digest=world.phase7_digest, requested_at=world.env.clock.now())
    assert "auto" in str(exc.value).lower()


def test_a_draft_from_another_request_is_refused(tmp_path):
    world = _world(tmp_path)
    plan = world.plans["main"]
    other = plan.draft.model_copy(update={"request_id": "req-somebody-else"})
    refused = ReplayLanePlan(
        lane="main", draft=other, candidate_plan_digest=plan.candidate_plan_digest,
        context_content_digest=plan.context_content_digest)
    with pytest.raises(ReplayCardRefused):
        build_replay_lane_card(
            plan=refused, request=world.request, payloads=world.payloads,
            registry_digest=world.phase7_digest, requested_at=world.env.clock.now())


def test_an_unknown_lane_and_a_missing_lane_are_refused(tmp_path):
    world = _world(tmp_path)
    coord = _coord(world, tmp_path)
    sch, _cal, _s, _e = _three_point_env()
    bad = dict(world.plans)
    bad["shadow"] = world.plans["main"]
    with pytest.raises(ReplayLaneUnknown):
        register_replay_approval_cards(
            coordinator=coord, request=world.request, schedule=sch,
            execution_config=_exec_config(sch), lane_plans=bad,
            payloads=world.payloads, registry_digest=world.phase7_digest)
    with pytest.raises(ReplayLaneUnknown):
        register_replay_approval_cards(
            coordinator=coord, request=world.request, schedule=sch,
            execution_config=_exec_config(sch),
            lane_plans={"main": world.plans["main"]},
            payloads=world.payloads, registry_digest=world.phase7_digest)


def test_a_refused_second_lane_leaves_no_half_registered_card(tmp_path):
    """BUILD-time atomicity: if either lane fails a card precondition, NEITHER lane
    is registered (all lanes are built before any is registered). A lone pending card
    would let a human decide one lane and believe the run was authorized. The
    register-time half of this is covered by
    ``test_a_drifted_pending_card_refuses_before_any_lane_is_registered``; a mid-loop
    I/O failure is NOT covered and is documented as such on the function."""
    world = _world(tmp_path)
    coord = _coord(world, tmp_path)
    sch, _cal, _s, _e = _three_point_env()
    broken = dict(world.plans)
    broken["main"] = dataclasses.replace(
        world.plans["main"], candidate_plan_digest="7" * 64)
    with pytest.raises(ReplayCardRefused):
        register_replay_approval_cards(
            coordinator=coord, request=world.request, schedule=sch,
            execution_config=_exec_config(sch), lane_plans=broken,
            payloads=world.payloads, registry_digest=world.phase7_digest)
    assert coord.list_pending() == ()
    journal = tmp_path / "plan_approvals.jsonl"
    assert not journal.exists() or not journal.read_bytes()


def test_two_lanes_naming_the_same_candidate_are_refused(tmp_path):
    """Card identity is ``(request_id, candidate_plan_digest)``: two lanes sharing a
    digest would collapse into ONE card, so a single decision would authorize both
    lanes while the reviewer believed they were deciding one."""
    world = _world(tmp_path)
    coord = _coord(world, tmp_path)
    sch, _cal, _s, _e = _three_point_env()
    same = world.plans["main"]
    collapsed = {
        "bootstrap": dataclasses.replace(same, lane="bootstrap"),
        "main": same,
    }
    with pytest.raises(ReplayCardRefused) as exc:
        register_replay_approval_cards(
            coordinator=coord, request=world.request, schedule=sch,
            execution_config=_exec_config(sch), lane_plans=collapsed,
            payloads=world.payloads, registry_digest=world.phase7_digest)
    assert "SAME candidate digest" in str(exc.value)
    assert coord.list_pending() == ()


def test_an_execution_config_bound_to_another_schedule_is_refused(tmp_path):
    world = _world(tmp_path)
    coord = _coord(world, tmp_path)
    sch, _cal, _s, _e = _three_point_env()
    drifted = _exec_config(sch).model_copy(update={"schedule_digest": "e" * 64})
    with pytest.raises(ReplayCardRefused) as exc:
        register_replay_approval_cards(
            coordinator=coord, request=world.request, schedule=sch,
            execution_config=drifted, lane_plans=world.plans,
            payloads=world.payloads, registry_digest=world.phase7_digest)
    assert "schedule_digest" in str(exc.value)
    assert coord.list_pending() == ()


def test_a_lane_plan_whose_lane_field_disagrees_is_refused(tmp_path):
    world = _world(tmp_path)
    coord = _coord(world, tmp_path)
    sch, _cal, _s, _e = _three_point_env()
    crossed = {"bootstrap": world.plans["main"], "main": world.plans["bootstrap"]}
    with pytest.raises(ReplayCardRefused):
        register_replay_approval_cards(
            coordinator=coord, request=world.request, schedule=sch,
            execution_config=_exec_config(sch), lane_plans=crossed,
            payloads=world.payloads, registry_digest=world.phase7_digest)


# =========================================================================== #
# 4. registration on the REAL coordinator                                      #
# =========================================================================== #
def test_registration_puts_two_pending_cards_on_the_real_journal(tmp_path):
    world = _world(tmp_path)
    coord = _coord(world, tmp_path)
    cards = _register(world, coord)
    assert isinstance(cards, ReplayApprovalCards)
    assert set(cards.cards) == set(REPLAY_LANES)
    pending = coord.list_pending()
    assert {c.candidate_plan_digest for c in pending} == {
        cards.candidate_plan_digest(lane) for lane in REPLAY_LANES}
    rows = _rows(tmp_path / "plan_approvals.jsonl")
    assert [r["row_kind"] for r in rows] == ["pending", "pending"]
    # the returned kwargs are exactly the coordinator's two override parameters.
    kw = cards.coordinator_kwargs()
    assert set(kw) == {"bootstrap_candidate_plan_digest", "main_candidate_plan_digest"}
    sig = inspect.signature(ProductionReplayPlanCoordinator.__init__).parameters
    assert set(kw) <= set(sig)


def test_registration_is_idempotent(tmp_path):
    world = _world(tmp_path)
    coord = _coord(world, tmp_path)
    first = _register(world, coord)
    before = (tmp_path / "plan_approvals.jsonl").read_bytes()
    second = _register(world, coord)
    assert (tmp_path / "plan_approvals.jsonl").read_bytes() == before
    for lane in REPLAY_LANES:
        assert first.candidate_plan_digest(lane) == second.candidate_plan_digest(lane)
        assert first.cards[lane].semantic_digest() == second.cards[lane].semantic_digest()


def test_re_registering_after_a_decision_does_not_crash_and_names_the_lane(tmp_path):
    """The launcher calls this on every start and after every restart. Once a human
    has decided a lane, ``register_pending`` would raise ``ApprovalDecisionConflict``
    — i.e. it would crash precisely when someone had done the right thing. The
    decided lane is skipped, named in ``already_decided``, and the returned digests
    are unchanged, so a restart re-establishes the run instead of dying."""
    world = _world(tmp_path)
    coord = _coord(world, tmp_path)
    first = _register(world, coord)
    _decide_all(coord, first)
    journal = tmp_path / "plan_approvals.jsonl"
    before = journal.read_bytes()

    # the hazard being avoided, pinned directly: re-registering a decided card is a
    # hard conflict, so a naive re-call would crash the launcher on restart.
    with pytest.raises(ApprovalDecisionConflict):
        coord.register_pending(first.cards["bootstrap"], idempotency_key="naive")

    again = _register(world, coord)                      # the restart call
    assert journal.read_bytes() == before, "a decided lane must append nothing"
    assert dict(again.already_decided) == {
        "bootstrap": "approved", "main": "approved"}
    assert again.awaiting_human() == ()
    for lane in REPLAY_LANES:
        assert again.candidate_plan_digest(lane) == first.candidate_plan_digest(lane)
    # …and the re-established identities still authorize a real run.
    _replay, bindings, (sch, start, end), _runner = _replay_coordinator(
        world, approval=coord, overrides=again.coordinator_kwargs())
    state = run_interval_replay(
        request=world.request, schedule=sch, execution_config=_exec_config(sch),
        interval_start=start, interval_end=end, bindings=bindings)
    assert state.completed_points == 3


def test_a_mixed_decided_and_pending_state_registers_only_the_pending_lane(tmp_path):
    """The half-decided state the reviewer named: one lane decided, one not. The
    pending lane is (idempotently) registered, the decided one is reported."""
    world = _world(tmp_path)
    coord = _coord(world, tmp_path)
    cards = _register(world, coord)
    coord.decide(
        request_id=cards.request_id,
        candidate_plan_digest=cards.candidate_plan_digest("bootstrap"),
        decision=ApprovalDecision.APPROVED, actor=declared_operator_actor(),
        reason="only the bootstrap lane so far", idempotency_key="decide-partial")

    again = _register(world, coord)
    assert dict(again.already_decided) == {"bootstrap": "approved"}
    assert again.awaiting_human() == ("main",)
    assert [c.candidate_plan_digest for c in coord.list_pending()] == [
        cards.candidate_plan_digest("main")]


def test_a_drifted_pending_card_refuses_before_any_lane_is_registered(tmp_path):
    """The register-time half-write the build-time guard does NOT cover: a
    semantically different card already pending for the second lane. The pre-flight
    catches it, so the first lane is not written either."""
    world = _world(tmp_path)
    coord = _coord(world, tmp_path)
    sch, _cal, _s, _e = _three_point_env()
    # seed ONLY the main lane, with a different (non-digest-bound) framing.
    drifted_plan = dataclasses.replace(
        world.plans["main"], planner_rationale="an earlier, different rationale")
    coord.register_pending(
        build_replay_lane_card(
            plan=drifted_plan, request=world.request, payloads=world.payloads,
            registry_digest=world.phase7_digest, requested_at=world.env.clock.now()),
        idempotency_key="seed-main")
    before = (tmp_path / "plan_approvals.jsonl").read_bytes()

    with pytest.raises(ReplayCardRefused) as exc:
        register_replay_approval_cards(
            coordinator=coord, request=world.request, schedule=sch,
            execution_config=_exec_config(sch), lane_plans=world.plans,
            payloads=world.payloads, registry_digest=world.phase7_digest)
    assert "semantically different" in str(exc.value)
    assert (tmp_path / "plan_approvals.jsonl").read_bytes() == before
    # the bootstrap lane was NOT half-written.
    assert [c.candidate_plan_digest for c in coord.list_pending()] == [
        world.plans["main"].candidate_plan_digest]


def test_a_request_bound_to_another_schedule_is_refused(tmp_path):
    """The 口径-drift door must also check the REQUEST's schedule ref, not only the
    execution config's — otherwise a request bound to schedule A cards cleanly
    against schedule B whose config simply names B."""
    world = _world(tmp_path)
    coord = _coord(world, tmp_path)
    sch, _cal, _s, _e = _three_point_env()
    other = sch.model_copy(update={"id": "other.schedule"})
    with pytest.raises(ReplayCardRefused) as exc:
        register_replay_approval_cards(
            coordinator=coord, request=world.request, schedule=other,
            execution_config=_exec_config(other), lane_plans=world.plans,
            payloads=world.payloads, registry_digest=world.phase7_digest)
    assert "the request binds schedule" in str(exc.value)
    assert coord.list_pending() == ()


def test_a_request_with_no_schedule_ref_is_refused(tmp_path):
    world = _world(tmp_path)
    coord = _coord(world, tmp_path)
    sch, _cal, _s, _e = _three_point_env()
    unbound = OrchestrationRequest(
        request_id=REQUEST_ID, goal=world.request.goal, workflow="orchestrate_only",
        fallback_preset_id=e2e.RESEARCH_BASELINE,
        approval_policy=ApprovalPolicy.REQUIRED)
    with pytest.raises(ReplayCardRefused) as exc:
        register_replay_approval_cards(
            coordinator=coord, request=unbound, schedule=sch,
            execution_config=_exec_config(sch), lane_plans=world.plans,
            payloads=world.payloads, registry_digest=world.phase7_digest)
    assert "decision_schedule_ref" in str(exc.value)
    assert coord.list_pending() == ()


def test_a_semantically_different_recard_conflicts(tmp_path):
    """A re-registration under the same identity with different content is a hard
    conflict, never a silent overwrite of what the human is looking at."""
    world = _world(tmp_path)
    coord = _coord(world, tmp_path)
    _register(world, coord)
    plan = world.plans["main"]
    mutated = ReplayLanePlan(
        lane="main", draft=plan.draft,
        candidate_plan_digest=plan.candidate_plan_digest,
        context_content_digest=plan.context_content_digest,
        planner_rationale="a different rationale for the same candidate")
    card = build_replay_lane_card(
        plan=mutated, request=world.request, payloads=world.payloads,
        registry_digest=world.phase7_digest, requested_at=world.env.clock.now())
    with pytest.raises(ApprovalDecisionConflict):
        coord.register_pending(card, idempotency_key="k")


# =========================================================================== #
# 5. THE R22 ACCEPTANCE                                                        #
# =========================================================================== #
def test_r22_one_human_moment_authorizes_both_lanes_of_a_real_replay(tmp_path):
    """THE closure assertion. A real operator decides the two real cards through
    the real verifier and the real admission service; a real three-point
    ``run_interval_replay`` then completes with BOTH lanes passing
    ``_require_approval`` — the refusal that blocks today no longer fires, and it
    stopped firing because a human approved exactly what runs."""
    world = _world(tmp_path)
    coord = _coord(world, tmp_path)
    cards = _register(world, coord)

    # nothing is approved yet: the lane gate still refuses.
    replay_coord, bindings, (sch, start, end), runner = _replay_coordinator(
        world, approval=coord, overrides=cards.coordinator_kwargs())
    with pytest.raises(ReplayCoordinatorApprovalRefused):
        run_interval_replay(
            request=world.request, schedule=sch, execution_config=_exec_config(sch),
            interval_start=start, interval_end=end, bindings=bindings)

    # the one human moment — a real allowlisted operator, a real verifier.
    decided = _decide_all(coord, cards)
    from guanlan_v2.orchestration.events import EventType

    for lane in REPLAY_LANES:
        approval, event = decided[lane]
        assert approval.decision is ApprovalDecision.APPROVED
        assert approval.actor_id == declared_operator_actor()
        assert event.event_type is EventType.PLAN_APPROVED

    # …and now the whole three-point interval runs, both lanes gated by a REAL
    # PlanApprovalCoordinator lookup (never a stub).
    replay_coord, bindings, (sch, start, end), runner = _replay_coordinator(
        world, approval=coord, overrides=cards.coordinator_kwargs())
    state = run_interval_replay(
        request=world.request, schedule=sch, execution_config=_exec_config(sch),
        interval_start=start, interval_end=end, bindings=bindings)
    assert state.completed_points == 3 and state.total_points == 3
    assert bindings.admission._require_approval(
        cards.candidate_plan_digest("bootstrap"), lane="bootstrap").is_approved
    assert bindings.admission._require_approval(
        cards.candidate_plan_digest("main"), lane="main").is_approved
    # both lanes really executed, per point, in the driver's order.
    assert runner.lanes == [
        "bootstrap#1", "llm#1", "bootstrap#2", "llm#2", "bootstrap#3", "llm#3"]
    # …and the digest the coordinator handed the runner IS the decided one.
    assert replay_coord._bootstrap_candidate == cards.candidate_plan_digest("bootstrap")
    assert replay_coord._main_candidate == cards.candidate_plan_digest("main")


def test_without_the_cards_the_same_run_raises_the_refusal_that_blocks_today(tmp_path):
    """The control: the coordinator's own DEFAULT lane digests (no cards, no
    decisions) reproduce the exact failure R22 exists to remove."""
    world = _world(tmp_path)
    coord = _coord(world, tmp_path)
    _replay, bindings, (sch, start, end), _runner = _replay_coordinator(
        world, approval=coord)
    with pytest.raises(ReplayCoordinatorApprovalRefused) as exc:
        run_interval_replay(
            request=world.request, schedule=sch, execution_config=_exec_config(sch),
            interval_start=start, interval_end=end, bindings=bindings)
    assert "no terminal approval decision" in str(exc.value)


def test_forgetting_the_overrides_still_refuses_even_after_a_human_approved(tmp_path):
    """The trap this module's return value exists to prevent: cards registered and
    APPROVED, but the coordinator built without ``coordinator_kwargs()`` — it falls
    back to the synthetic defaults nobody can ever have decided and refuses. Loudly
    fail-closed, never a silent run under an unapproved identity."""
    world = _world(tmp_path)
    coord = _coord(world, tmp_path)
    cards = _register(world, coord)
    _decide_all(coord, cards)
    _replay, bindings, (sch, start, end), _runner = _replay_coordinator(
        world, approval=coord)                       # <-- overrides deliberately omitted
    with pytest.raises(ReplayCoordinatorApprovalRefused):
        run_interval_replay(
            request=world.request, schedule=sch, execution_config=_exec_config(sch),
            interval_start=start, interval_end=end, bindings=bindings)


def test_a_rejected_card_refuses_its_lane(tmp_path):
    world = _world(tmp_path)
    coord = _coord(world, tmp_path)
    cards = _register(world, coord)
    _decide_all(coord, cards, decision=ApprovalDecision.REJECTED)
    _replay, bindings, (sch, start, end), _runner = _replay_coordinator(
        world, approval=coord, overrides=cards.coordinator_kwargs())
    with pytest.raises(ReplayCoordinatorApprovalRefused) as exc:
        run_interval_replay(
            request=world.request, schedule=sch, execution_config=_exec_config(sch),
            interval_start=start, interval_end=end, bindings=bindings)
    assert "not APPROVED" in str(exc.value)


def test_the_decisions_survive_process_death(tmp_path):
    """A cold coordinator rebuilt from the journal alone still authorizes both
    lanes — the property that makes a human decision worth recording."""
    world = _world(tmp_path)
    coord = _coord(world, tmp_path)
    cards = _register(world, coord)
    _decide_all(coord, cards)
    del coord

    reborn = _coord(_world(tmp_path, run_id="run-r22-cold"), tmp_path)
    for lane in REPLAY_LANES:
        recovered = reborn.load_decision(
            cards.request_id, cards.candidate_plan_digest(lane))
        assert recovered is not None and recovered.is_approved
        assert recovered.actor_id == declared_operator_actor()


def test_the_start_candidate_digest_authorizes_neither_lane(tmp_path):
    """The fate of ``derive_replay_start_candidate_digest``: it is a request-level
    correlation id, NOT an approval identity. Approving it authorizes nothing —
    which is exactly the bug R22 removes, pinned so it cannot come back as a
    silent 'the human approved something' pass."""
    world = _world(tmp_path)
    sch, _cal, _s, _e = _three_point_env()
    start_digest = derive_replay_start_candidate_digest(
        request_id=world.request.request_id, schedule_digest=sch.content_digest,
        code="600519", start_date="2026-07-06", end_date="2026-07-08",
        strategy_id=None)
    coord = _coord(world, tmp_path)
    cards = _register(world, coord)
    for lane in REPLAY_LANES:
        assert start_digest != cards.candidate_plan_digest(lane)
    assert start_digest != cards.plan_candidate_digest
    # it is not even registrable: the sealer never mints a card for it, so a human
    # cannot decide it at all (fail-closed, no fabricated authority).
    assert coord.load_decision(world.request.request_id, start_digest) is None
    replay_coord, _bindings_, _env, _runner = _replay_coordinator(
        world, approval=coord, overrides=cards.coordinator_kwargs())
    with pytest.raises(ReplayCoordinatorApprovalRefused):
        replay_coord._require_approval(start_digest, lane="bootstrap")


# =========================================================================== #
# 6. the actor material                                                        #
# =========================================================================== #
def test_declared_operator_actor_returns_the_shipped_declaration():
    from guanlan_v2.orchestration.adapters.identity import load_operator_allowlist

    declared = load_operator_allowlist(DEFAULT_OPERATOR_ALLOWLIST_PATH)
    assert declared_operator_actor() == declared[0]
    # …and it verifies against the shipped verifier, which is the whole point.
    assert ConfigOperatorVerifier().verify(declared_operator_actor()).actor == declared[0]


def test_declared_operator_actor_is_usable_as_console_actor_material():
    """``console/api.py::_resolve_actor`` calls a callable and passes a value
    through, so the FUNCTION (not its value) is what a router should be given —
    then the declaration in force at DECISION time is the one that is used, exactly
    like the verifier's own per-verify re-read."""
    assert callable(declared_operator_actor)
    resolved = declared_operator_actor() if callable(declared_operator_actor) \
        else declared_operator_actor
    assert isinstance(resolved, str) and resolved
    assert ConfigOperatorVerifier().verify(resolved).actor == resolved


def test_declared_operator_actor_refuses_an_ambiguous_declaration(tmp_path):
    """Two declared operators means the server cannot know WHO is at the console;
    guessing would stamp a durable approval with the wrong human."""
    path = tmp_path / "operators.json"
    path.write_text(json.dumps({
        "schema_version": "1",
        "operators": [{"actor_id": "human:ops"}, {"actor_id": "human:second"}],
    }), encoding="utf-8")
    with pytest.raises(OperatorAllowlistError) as exc:
        declared_operator_actor(path)
    assert "ambiguous" in str(exc.value).lower() or "one" in str(exc.value).lower()


def test_declared_operator_actor_refuses_a_missing_declaration(tmp_path):
    with pytest.raises(OperatorAllowlistError):
        declared_operator_actor(tmp_path / "absent.json")


# =========================================================================== #
# 7. housekeeping — the new module disturbs no sealed firewall                 #
# =========================================================================== #
def test_replay_cards_defines_no_public_contract_model():
    import guanlan_v2.orchestration.adapters.replay_cards as mod
    from guanlan_v2.orchestration.digest import ContractModel, DigestModel

    for obj in vars(mod).values():
        if inspect.isclass(obj) and issubclass(obj, ContractModel):
            assert (obj in (ContractModel, DigestModel)
                    or obj.__module__ != mod.__name__
                    or obj.__name__.startswith("_")), obj.__name__


def test_replay_cards_never_self_approves():
    """The sealer registers a PENDING card and nothing else: no ``decide``, no
    ``register_and_try_lease``, no ``AUTO``, no admission freeze."""
    import ast

    import guanlan_v2.orchestration.adapters.replay_cards as mod

    banned = ("decide", "register_and_try_lease", "freeze_and_admit_candidate",
              "record_approval", "admit_after_approval", "issue_lease")
    tree = ast.parse(inspect.getsource(mod))
    called = {
        node.func.attr for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    for name in banned:
        assert name not in called, f"the sealer must never call {name}()"
    assert "register_pending" in called

    # …and an attribute scan alone would miss ``getattr(coord, "decide")()``, so every
    # NON-docstring string literal is checked too: a banned name can be neither called
    # directly nor smuggled through a dynamic lookup.
    docstrings = {
        id(node.body[0].value) for node in ast.walk(tree)
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                             ast.AsyncFunctionDef))
        and node.body and isinstance(node.body[0], ast.Expr)
        and isinstance(node.body[0].value, ast.Constant)
        and isinstance(node.body[0].value.value, str)
    }
    literals = [
        node.value for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
        and id(node) not in docstrings
    ]
    for name in banned:
        assert name not in literals, (
            f"a bare {name!r} string literal could feed a dynamic lookup — the sealer "
            "registers a pending card and nothing else")
    src = inspect.getsource(mod)
    assert "ApprovalDecision.APPROVED" not in src
    assert "ApprovalPolicy.AUTO" not in src or "refus" in src.lower()


def test_the_content_digest_domains_match_the_sealed_coordinator():
    """The two domain strings are transcribed from the sealed coordinator; if that
    source ever changes, this fails instead of silently carding a dead digest."""
    import guanlan_v2.orchestration.adapters.api as api_mod

    src = inspect.getsource(api_mod.ProductionReplayPlanCoordinator)
    assert "shadow-replay-bootstrap-candidate-v1" in src
    assert "shadow-replay-main-candidate-v1" in src
    probe = content_digest({"domain": "shadow-replay-bootstrap-candidate-v1",
                            "plan_candidate_digest": "a" * 64})
    assert coordinator_default_lane_candidate_digests(
        plan_candidate_digest="a" * 64)["bootstrap"] == probe
