# -*- coding: utf-8 -*-
"""R3 + R18 — the launcher: the first production path that actually RUNS the framework.

Offline and deterministic. Every assertion below is against the REAL production
surfaces (``ProductionReplayPlanCoordinator``, ``run_interval_replay``,
``build_dual_curves``, ``hand_off_dual_curves_to_feedback``, ``persist_replay_state``,
the real Phase-2 ``PlanAdmissionService``) — the only injected doubles are the two
seams the repo genuinely has no production producer for (a plan EXECUTOR and a sink
artifact resolver), and those are the launcher's declared boundary.
"""
from __future__ import annotations

import ast
import asyncio
import dataclasses
import threading
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from guanlan_v2.orchestration.adapters import launcher as lc
from guanlan_v2.orchestration.adapters.launcher import (
    LaunchRefused,
    MultiPointPlanExecutionRefused,
    PlanExecutionUnwired,
    ReplayLaunchOutcome,
    build_admitted_plan_runner,
    launch_interval_replay,
    refuse_if_event_loop_running,
    run_coroutine_blocking,
)
from guanlan_v2.orchestration.adapters.luozi import (
    ReplayIntentLedger,
    ReplayRuntimeBindings,
)
from guanlan_v2.orchestration.enums import ApprovalDecision, ApprovalPolicy
from guanlan_v2.orchestration.spec import compute_candidate_plan_digest

from tests.orchestration.test_adapters_api import _RecordingApproval
from tests.orchestration.test_dual_curves import _FakePool
from tests.orchestration.test_luozi_replay import _bindings
from tests.orchestration.test_luozi_wakeup import _SpyLedger
from tests.orchestration.test_phase9_e2e import (
    _E2ePlanRunner,
    _coordinator,
    _request,
    _world,
)


# =========================================================================== #
# helpers                                                                      #
# =========================================================================== #
class _FakeCards:
    """The R22 :class:`ReplayApprovalCards` surface the launcher consumes."""

    def __init__(self, *, bootstrap, main, awaiting=(), decided=None):
        self._digests = {"bootstrap": bootstrap, "main": main}
        self._awaiting = tuple(awaiting)
        self.already_decided = dict(decided or {})
        self.request_id = "req-launch"
        self.plan_candidate_digest = "p" * 64

    def awaiting_human(self):
        return self._awaiting

    def candidate_plan_digest(self, lane):
        return self._digests[lane]

    def coordinator_kwargs(self):
        return {"bootstrap_candidate_plan_digest": self._digests["bootstrap"],
                "main_candidate_plan_digest": self._digests["main"]}


def _launch_world(*, request=None):
    """A real 3-point world + a real coordinator over the frozen PIT fixtures."""
    world = _world()
    request = request if request is not None else _request(world.schedule)
    base, _sink = _bindings(world.schedule, world.calendar)
    seam = _E2ePlanRunner()
    coordinator = _coordinator(
        world, plan_runner=seam, approval=_RecordingApproval(),
        budget=base.budget, request=request)
    bindings = ReplayRuntimeBindings(
        admission=coordinator, budget=base.budget, run_budget=base.run_budget,
        schedule_registry=base.schedule_registry, calendar=world.calendar,
        clock_factory=base.clock_factory, seats_budget_seam=base.seats_budget_seam,
        intent_ledger=ReplayIntentLedger())
    return SimpleNamespace(world=world, request=request, bindings=bindings,
                           coordinator=coordinator, seam=seam, base=base)


_RUN_HEAD = {"run_id": "r", "code": "SH600000", "strategy_id": "seat-launch", "tf": "D"}


def _cards_for(env, **kw):
    """Cards naming the digests the coordinator actually gates on (R22 §1b)."""
    return _FakeCards(bootstrap=env.coordinator._bootstrap_candidate,
                      main=env.coordinator._main_candidate, **kw)


def _launch(env, *, cards=None, coordinator=None, maturity_delta=timedelta(days=-1),
            **kw):
    report_pool = _FakePool()
    return launch_interval_replay(
        request=env.request,
        schedule=env.world.schedule,
        execution_config=env.world.config,
        interval_start=env.world.start,
        interval_end=env.world.end,
        cards=cards if cards is not None else _cards_for(env),
        coordinator=coordinator if coordinator is not None else env.coordinator,
        bindings=env.bindings,
        shadow_runner=env.world.runner,
        artifact_pool=report_pool,
        trial_ledger=_SpyLedger(),
        maturity_now=env.world.end + maturity_delta,
        decision_artifacts=lambda: tuple(
            env.seam.artifacts[o] for o in sorted(env.seam.artifacts)),
        seats_run_head=_RUN_HEAD,
        **kw)


# =========================================================================== #
# 1. the 9999 red line, encoded as a guard                                     #
# =========================================================================== #
def test_refuse_if_event_loop_running_refuses_inside_a_coroutine():
    """`run_interval_replay` is sync and `dag.run_plan` is async: a launch that ran
    on the event loop thread would block it — that is what killed 9999 before."""
    async def _inside():
        with pytest.raises(LaunchRefused) as excinfo:
            refuse_if_event_loop_running("launch")
        return str(excinfo.value)

    message = asyncio.run(_inside())
    assert "event loop" in message
    # and on a bare thread it is a no-op
    refuse_if_event_loop_running("launch")


def test_run_coroutine_blocking_runs_on_a_loop_free_thread():
    async def _coro():
        return asyncio.get_running_loop() is not None

    assert run_coroutine_blocking(_coro) is True

    async def _outer():
        with pytest.raises(LaunchRefused):
            run_coroutine_blocking(_coro)

    asyncio.run(_outer())


def test_a_launch_from_a_coroutine_is_refused_before_anything_runs():
    env = _launch_world()

    async def _inside():
        with pytest.raises(LaunchRefused):
            _launch(env)

    asyncio.run(_inside())
    assert env.seam.calls == []


# =========================================================================== #
# 2. the approval gates — nothing runs unapproved                              #
# =========================================================================== #
def test_a_lane_still_awaiting_a_human_runs_nothing():
    env = _launch_world()
    outcome = _launch(env, cards=_cards_for(env, awaiting=("bootstrap", "main")))
    assert isinstance(outcome, ReplayLaunchOutcome)
    assert outcome.status == "awaiting_approval"
    assert outcome.awaiting_human == ("bootstrap", "main")
    assert outcome.completed_points == 0
    assert env.seam.calls == []          # the driver never started


def test_a_rejected_lane_refuses_the_launch():
    env = _launch_world()
    cards = _cards_for(env, decided={"bootstrap": "approved", "main": "rejected"})
    with pytest.raises(LaunchRefused) as excinfo:
        _launch(env, cards=cards)
    assert "REJECTED" in str(excinfo.value)
    assert env.seam.calls == []


def test_auto_approval_is_refused_at_the_launcher_door():
    world = _world()
    request = _request(world.schedule).model_copy(
        update={"approval_policy": ApprovalPolicy.AUTO})
    env = _launch_world(request=request)
    with pytest.raises(LaunchRefused) as excinfo:
        _launch(env)
    assert "AUTO" in str(excinfo.value)
    assert env.seam.calls == []


# =========================================================================== #
# 3. the happy path — the whole framework, once                                #
# =========================================================================== #
def test_the_launcher_runs_the_whole_framework_once():
    env = _launch_world()
    outcome = _launch(env)
    assert outcome.status == "parked"                 # immature ⇒ WAITING_FOR_MATURITY
    assert outcome.completed_points == 3 and outcome.total_points == 3
    assert outcome.awaiting_human == ()
    # the driver really walked both lanes of every point through the coordinator
    assert [c["lane"] for c in env.seam.calls] == [
        "bootstrap", "llm", "bootstrap", "llm", "bootstrap", "llm"]
    # real PIT contexts: every lane call is frozen at ITS point's instant
    by_point = {p.point_ordinal: p for p in env.world.points}
    for call in env.seam.calls:
        assert call["as_of"] == by_point[call["ordinal"]].decision_as_of
    # dual curves under ONE attestation + the maturity handoff
    attestation = env.world.config.semantic_digest()
    assert outcome.execution_config_digest == attestation
    assert outcome.curve_report_ref is not None
    assert outcome.intent_count == 3
    # the seats-compat rows round-trip
    assert outcome.seats_row_count == 3


def test_the_outcome_is_json_safe_and_names_every_identity():
    import json

    env = _launch_world()
    payload = _launch(env).as_dict()
    json.dumps(payload)                                     # never raises
    assert payload["status"] == "parked"
    assert payload["lane_candidate_digests"]["bootstrap"] == (
        env.coordinator._bootstrap_candidate)
    assert payload["lane_candidate_digests"]["main"] == env.coordinator._main_candidate


def test_a_mature_interval_completes_and_reaches_the_feedback_ledger():
    env = _launch_world()
    ledger = _SpyLedger()
    outcome = launch_interval_replay(
        request=env.request, schedule=env.world.schedule,
        execution_config=env.world.config, interval_start=env.world.start,
        interval_end=env.world.end, cards=_cards_for(env),
        coordinator=env.coordinator, bindings=env.bindings,
        shadow_runner=env.world.runner, artifact_pool=_FakePool(),
        trial_ledger=ledger, maturity_now=env.world.end + timedelta(days=30),
        decision_artifacts=lambda: tuple(
            env.seam.artifacts[o] for o in sorted(env.seam.artifacts)),
        seats_run_head=_RUN_HEAD)
    assert outcome.status == "completed"
    assert ledger.touched                        # the matured window really registered


def test_a_coordinator_built_without_the_card_digests_is_refused_before_the_driver():
    """R22's named trap: forgetting ``coordinator_kwargs()`` leaves the coordinator on
    its synthetic defaults, which no human can ever have approved. Refused up front
    rather than at the first decision point, after budget was reserved."""
    env = _launch_world()
    drifted = _FakeCards(bootstrap="b" * 64, main=env.coordinator._main_candidate)
    with pytest.raises(LaunchRefused) as excinfo:
        _launch(env, cards=drifted)
    assert "coordinator_kwargs" in str(excinfo.value)
    assert env.seam.calls == []


def test_a_cards_object_that_cannot_name_a_lane_digest_refuses_the_launch():
    """Minor 6: the guard used to swallow this and drop the lane — a silent fail-open
    that disarmed the R22 check AND blanked the outcome's own digest record."""
    env = _launch_world()

    class _Broken(_FakeCards):
        def candidate_plan_digest(self, lane):
            if lane == "main":
                raise RuntimeError("no card for this lane")
            return super().candidate_plan_digest(lane)

    broken = _Broken(bootstrap=env.coordinator._bootstrap_candidate,
                     main=env.coordinator._main_candidate)
    with pytest.raises(LaunchRefused) as excinfo:
        _launch(env, cards=broken)
    assert "main" in str(excinfo.value)
    assert env.seam.calls == []


def test_a_coordinator_that_hides_its_lane_digests_says_the_guard_did_not_run():
    """Minor 6: the other fail-open. A double with no ``_bootstrap_candidate`` no
    longer passes silently — the outcome NAMES the guard as not having run."""
    env = _launch_world()

    class _Opaque:
        def __init__(self, inner):
            self._inner = inner

        def __getattr__(self, name):
            if name in ("_bootstrap_candidate", "_main_candidate"):
                raise AttributeError(name)
            return getattr(self._inner, name)

    outcome = _launch(env, coordinator=_Opaque(env.coordinator))
    joined = " | ".join(outcome.notes)
    assert "bootstrap lane override could NOT be verified" in joined
    assert "main lane override could NOT be verified" in joined


def test_every_stage_the_launch_cannot_reach_is_named_on_the_outcome():
    """No stage is ever silently absent: an unbound input becomes a NOTE."""
    env = _launch_world()
    outcome = launch_interval_replay(
        request=env.request, schedule=env.world.schedule,
        execution_config=env.world.config, interval_start=env.world.start,
        interval_end=env.world.end, cards=_cards_for(env),
        coordinator=env.coordinator, bindings=env.bindings)
    assert outcome.status == "ran"
    assert outcome.completed_points == 3
    assert outcome.curve_report_ref is None and outcome.state_payload_ref is None
    joined = " | ".join(outcome.notes)
    assert "no shadow_runner bound" in joined
    assert "no ReplayStateStore bound" in joined
    assert "seats-compat rows were NOT produced" in joined


# =========================================================================== #
# 4. R18 — the production plan runner (admission half)                         #
# =========================================================================== #
class _Admission:
    """Records the freeze chain; mirrors PlanAdmissionService's short-circuit."""

    def __init__(self):
        self.freezes: list[tuple[str, str]] = []
        self._plans: dict[str, SimpleNamespace] = {}

    def freeze_and_admit_candidate(self, candidate_id, *, reservation_id,
                                   approval_event_id, idempotency_key):
        self.freezes.append((candidate_id, idempotency_key))
        plan = self._plans.get(candidate_id)
        if plan is None:
            plan = SimpleNamespace(plan_digest=candidate_id,
                                   run_id=f"run.{candidate_id[:6]}")
            self._plans[candidate_id] = plan
        return plan, SimpleNamespace(plan_digest=candidate_id)


def _lane_bindings():
    return {
        "bootstrap": lc.LaneExecutionBinding(
            lane="bootstrap", candidate_plan_digest="b" * 64,
            reservation_id="res-b", approval_event_id="ev-b"),
        "main": lc.LaneExecutionBinding(
            lane="main", candidate_plan_digest="m" * 64,
            reservation_id="res-m", approval_event_id="ev-m"),
    }


def _point(ordinal):
    return SimpleNamespace(point_ordinal=ordinal)


def test_the_plan_runner_freezes_the_exact_digest_the_gate_approved():
    admission = _Admission()
    seen = []
    runner = build_admitted_plan_runner(
        admission=admission, lane_bindings=_lane_bindings(),
        run_context_factory=lambda **kw: SimpleNamespace(**kw),
        plan_executor=lambda **kw: seen.append(kw) or SimpleNamespace(
            terminal_status="completed", run_id="r", plan_digest=kw["plan"].plan_digest),
        sink_artifact_resolver=lambda **kw: SimpleNamespace(lane=kw["lane"]),
        request_id="req-1")
    runner(lane="bootstrap", point=_point(1), approval=SimpleNamespace(),
           data_context=SimpleNamespace(as_of=None), memory_binding=None,
           candidate_plan_digest="b" * 64)
    assert admission.freezes == [("b" * 64, "replay-freeze:req-1:bootstrap")]
    assert seen and seen[0]["plan"].plan_digest == "b" * 64


def test_the_plan_runner_refuses_a_digest_no_lane_binding_names():
    """No digest substitution: an unbound digest is refused, never freeze-attempted."""
    admission = _Admission()
    runner = build_admitted_plan_runner(
        admission=admission, lane_bindings=_lane_bindings(),
        run_context_factory=lambda **kw: SimpleNamespace(),
        plan_executor=lambda **kw: SimpleNamespace(terminal_status="completed"),
        sink_artifact_resolver=lambda **kw: None, request_id="req-1")
    with pytest.raises(LaunchRefused):
        runner(lane="bootstrap", point=_point(1), approval=SimpleNamespace(),
               data_context=SimpleNamespace(as_of=None), memory_binding=None,
               candidate_plan_digest="f" * 64)
    assert admission.freezes == []


def test_a_second_decision_point_is_refused_not_silently_served_stale():
    """THE structural finding: one approved candidate digest ⇒ one admitted Plan ⇒
    one ``run_id`` ⇒ ``dag.run_plan`` executes ONCE. Serving point 2 the artifacts
    point 1 committed would be a borrowed-snapshot PIT leak, so it is refused."""
    admission = _Admission()
    runner = build_admitted_plan_runner(
        admission=admission, lane_bindings=_lane_bindings(),
        run_context_factory=lambda **kw: SimpleNamespace(),
        plan_executor=lambda **kw: SimpleNamespace(terminal_status="completed"),
        sink_artifact_resolver=lambda **kw: SimpleNamespace(),
        request_id="req-1")
    kw = dict(lane="bootstrap", approval=SimpleNamespace(),
              data_context=SimpleNamespace(as_of=None), memory_binding=None,
              candidate_plan_digest="b" * 64)
    runner(point=_point(1), **kw)
    with pytest.raises(MultiPointPlanExecutionRefused) as excinfo:
        runner(point=_point(2), **kw)
    assert "run_id" in str(excinfo.value)
    # the SAME point re-asked is idempotent, not a refusal (a retry is legitimate)
    runner(point=_point(1), **kw)


def test_the_plan_runner_refuses_loudly_when_execution_is_unwired():
    admission = _Admission()
    runner = build_admitted_plan_runner(
        admission=admission, lane_bindings=_lane_bindings(),
        run_context_factory=lambda **kw: SimpleNamespace(),
        plan_executor=None, sink_artifact_resolver=None, request_id="req-1")
    with pytest.raises(PlanExecutionUnwired) as excinfo:
        runner(lane="bootstrap", point=_point(1), approval=SimpleNamespace(),
               data_context=SimpleNamespace(as_of=None), memory_binding=None,
               candidate_plan_digest="b" * 64)
    assert "plan_executor" in str(excinfo.value)


def test_the_plan_runner_never_runs_on_the_event_loop_thread():
    admission = _Admission()
    runner = build_admitted_plan_runner(
        admission=admission, lane_bindings=_lane_bindings(),
        run_context_factory=lambda **kw: SimpleNamespace(),
        plan_executor=lambda **kw: SimpleNamespace(terminal_status="completed"),
        sink_artifact_resolver=lambda **kw: SimpleNamespace(), request_id="req-1")

    async def _inside():
        with pytest.raises(LaunchRefused):
            runner(lane="bootstrap", point=_point(1), approval=SimpleNamespace(),
                   data_context=SimpleNamespace(as_of=None), memory_binding=None,
                   candidate_plan_digest="b" * 64)

    asyncio.run(_inside())
    assert admission.freezes == []


# =========================================================================== #
# 5. the Option-1 ruling, proven at source                                     #
# =========================================================================== #
def test_a_per_point_run_identity_does_not_move_the_candidate_digest():
    """Option 1's load-bearing fact: ``_EXECUTABLE_FIELDS`` excludes ``id`` and
    ``run_id``, so a per-point draft identity keeps the ONE interval lane digest —
    while a per-point ``as_of`` (or a per-point ContextSnapshot content) moves it,
    which is exactly why the plan's ``as_of`` and context must be interval-level."""
    from tests.orchestration.test_dynamic_e2e import _build_env, _materialize_fallback

    env = _build_env(request_id="req-opt1", fallback_preset_id="main.research_baseline",
                     run_id="run-opt1")
    draft = _materialize_fallback(env, draft_id="plan-opt1")
    request = env.request
    ctx_digest = env.context.content_digest
    base = compute_candidate_plan_digest(
        request=request, draft=draft, context_content_digest=ctx_digest)
    per_point = draft.model_copy(update={"id": "draft.point-2", "run_id": "run.point-2"})
    assert compute_candidate_plan_digest(
        request=request, draft=per_point, context_content_digest=ctx_digest) == base
    moved = draft.model_copy(update={"as_of": draft.as_of + timedelta(days=1)})
    assert compute_candidate_plan_digest(
        request=request, draft=moved, context_content_digest=ctx_digest) != base
    assert compute_candidate_plan_digest(
        request=request, draft=draft, context_content_digest="c" * 64) != base


# =========================================================================== #
# 6. red lines                                                                 #
# =========================================================================== #
def test_the_launcher_never_approves_and_never_writes_an_order():
    src = Path(lc.__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)
    banned = {"decide", "record_approval", "register_and_try_lease", "issue_lease",
              "admit_after_approval", "place_order", "submit_order"}
    called: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            fn = node.func
            name = getattr(fn, "attr", None) or getattr(fn, "id", None)
            if name:
                called.add(name)
    assert not (called & banned), sorted(called & banned)


def test_the_launcher_defines_no_public_contract_model():
    from guanlan_v2.orchestration.digest import ContractModel

    for name in dir(lc):
        obj = getattr(lc, name)
        if isinstance(obj, type) and issubclass(obj, ContractModel):
            assert obj.__module__ != lc.__name__, name
