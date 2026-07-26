# -*- coding: utf-8 -*-
"""R3 + R18 — the launcher: the production path that actually RUNS the framework.

The hole this closes
--------------------
Nine phases of orchestration landed and merged, and none of it ever ran. Nothing in
``guanlan_v2/`` called :func:`~guanlan_v2.orchestration.adapters.luozi.run_interval_replay`,
:func:`~guanlan_v2.orchestration.adapters.luozi.build_dual_curves` or
:func:`~guanlan_v2.orchestration.adapters.luozi.hand_off_dual_curves_to_feedback`;
``ProductionReplayPlanCoordinator`` was constructed only in tests; and its
``plan_runner`` — the seam that admits and executes a plan — had no production
binding at all (residuals **R3** and **R18**). This module is the missing caller.

It does exactly two things, and refuses everything else:

* :func:`launch_interval_replay` **sequences and gates** one shadow-replay interval:
  approval gate → driver → dual curves → maturity handoff → durable state →
  seats-compat rows. Every stage it cannot reach is *named* in the returned
  :class:`ReplayLaunchOutcome`, never silently skipped and never faked.
* :func:`build_admitted_plan_runner` is **R18's production ``plan_runner``**: the
  ``freeze_and_admit_candidate`` → ``dag.run_plan`` chain behind the coordinator's
  injected seam, with the async→sync bridge and the digest/identity guards.

Red lines, all structural rather than documentary
-------------------------------------------------
* **It never approves anything.** No ``decide``, no lease consume, no
  ``record_approval``. A lane still awaiting a human returns an
  ``awaiting_approval`` outcome with the driver untouched; a rejected lane refuses.
  ``AUTO`` is refused at the door (the driver and the coordinator refuse it again).
* **It never writes an order or a signal.** A shadow replay stages intents into a
  run-scoped ledger; nothing here reaches a broker, ``seats`` write path or the
  factor library.
* **It never runs on an event loop thread.** ``run_interval_replay`` is sync and
  ``dag.run_plan`` is async, so the bridge must own a thread with no running loop.
  A coroutine that blocked the loop is what killed the 9999 process once already,
  so :func:`refuse_if_event_loop_running` makes it a loud refusal instead of a
  hang. Callers on the server's event loop must go through a worker thread.
* **Degradation is always badged** — the driver's per-point badges are folded into
  the curve report and re-surfaced on the outcome.

The Option-1 ruling, and what it does and does not buy
------------------------------------------------------
The coordinator holds ONE ``_bootstrap_candidate`` / ``_main_candidate`` for a whole
interval and hands that constant digest to ``plan_runner`` at every decision point,
while ``_verify_bootstrap_return`` expects a **per-point** ``ContextSnapshot``. The
ruling is Option 1: **the approved plan is interval-level**, and a point's PIT
context is an *input* to the approved graph rather than part of its identity. That
is buildable, and one fact makes it so — ``spec._EXECUTABLE_FIELDS`` (the exact
field set ``compute_candidate_plan_digest`` binds) **excludes** ``id``, ``run_id``
and the ``context_snapshot_ref`` locator, so a per-point run identity does not move
the lane digest. What *does* move it is ``as_of`` and the ContextSnapshot
**content** digest — which is precisely why the approved plan's ``as_of`` and
context must be sealed at the interval, not at a point.

**What Option 1 does NOT buy — the collision this module discovered and refuses to
paper over.** Option 1 describes an interval-shaped graph run **once**. The sealed
``ReplayPlanCoordinator`` port calls ``plan_runner`` **once per point per lane**, so
this module runs ONE admitted plan N times — and that is impossible. **The blocker is
NOT the approval.** Since ``_EXECUTABLE_FIELDS`` excludes ``run_id``, N per-point
drafts differing only in ``run_id``/``id`` share ONE candidate digest; there is no
"family" to authorize, and ``PlanApproval.authorizes_freeze`` (``events.py:451-455``)
binds only ``(request_id, candidate_plan_digest)`` — it is run-agnostic and already
authorizes freezing every one of them. What actually blocks re-execution is **four
digest-keyed singletons**:

1. the run-scoped admitted-event short-circuit (``admission.py:894-898``) — one
   admitted ``Plan`` per candidate digest, so one ``run_id``;
2. the single-slot active-plan reservation index (``budget.py:969-974``);
3. the Plan-scoped :class:`ArtifactPool` (``pool.py:291-293``), whose ``stage()``
   raises ``LateStageError`` on an already-committed output key
   (``pool.py:339-340``) — re-execution dies here *before* resume gets a say;
4. ``dag.run_plan``'s resume-by-``run_id`` (``dag.py:510`` + ``:538`` +
   the ``layer_index in committed_layers`` branch), which would otherwise serve
   point 2 the artifacts point 1 committed — a borrowed-snapshot PIT leak.

:class:`MultiPointPlanExecutionRefused` makes it a refusal at the seam.

**Standing decision (coordinator's ruling, recorded here, NOT implemented):
reshape the plan graph to interval scope and call ``plan_runner`` ONCE per
interval.** The per-point ``ContextSnapshot``s then come from per-point *nodes inside
one interval graph*, committed by node execution exactly as Option 1 says, with the
seam returning the slice for the requested point. That fights none of the four
singletons. The rejected alternative — per-point admission scopes — is
contract-legal but mints N approval events and N run budgets and breaks the
coordinator's one-interval-reservation parentage. (A) needs a production
interval-shaped plan graph to exist, which is the same missing piece as "no
production graph emits ``PortfolioTargetProposal@1``".

The per-point freeze decision
-----------------------------
**The runner DOES call ``freeze_and_admit_candidate`` at every point it reaches**,
against the ONE interval lane digest — and the honest scope of that has to be stated
precisely, because two easy overstatements are both false here.

* **It is genuinely idempotent.** The already-admitted short-circuit is the FIRST
  statement in the function (``admission.py:553-556``) and returns a pure read before
  any ``RuntimeBatch``: no double reservation, no duplicate ``PlanFrozen`` event, no
  second admitted payload, no mutation of any kind.
* **It does NOT re-verify anything after the first point.** That same short-circuit
  returns *before* the reservation load, before ``_load_approval_event``, before
  ``authorizes_freeze`` and before ``_detect_drift``. Every genuine re-verification
  lives in ``verify_for_dispatch`` inside ``dag.run_plan``.
* **And today no second point is ever reached**, because the
  :class:`MultiPointPlanExecutionRefused` guard above sits *before* the freeze call.
  So the per-point cadence is the right shape for the day the interval-scope reshape
  lands — it is not currently buying re-verification, and claiming otherwise would
  describe a path that cannot execute.

What the per-point call DOES buy today: the freeze sits inside the same seam the
coordinator's approval gate guards, adjacent in the code a reviewer reads, and a
digest the admission service never prepared is refused ``unknown_candidate`` —
loud, terminal, never a silent unapproved run. A re-verification failure propagates
**uncaught** out of :func:`launch_interval_replay`: it raises, it never continues.
"""
from __future__ import annotations

import asyncio
import dataclasses
from typing import Any, Callable, Mapping, Sequence

__all__ = [
    "LAUNCH_LANES",
    "LauncherError",
    "LaunchRefused",
    "PlanExecutionUnwired",
    "MultiPointPlanExecutionRefused",
    "LaneExecutionBinding",
    "ReplayLaunchOutcome",
    "refuse_if_event_loop_running",
    "run_coroutine_blocking",
    "pool_sink_artifact_resolver",
    "build_dag_plan_executor",
    "build_admitted_plan_runner",
    "launch_interval_replay",
]

#: the two lanes the replay coordinator gates, in the order it gates them.
LAUNCH_LANES: tuple[str, ...] = ("bootstrap", "main")

#: the coordinator's ``plan_runner`` lane names, mapped onto the approval lanes.
#: ``bootstrap_context`` passes ``lane="bootstrap"``; ``llm_proposal`` passes
#: ``lane="llm"`` while its card/approval lane is ``"main"``.
_RUNNER_LANE_TO_APPROVAL_LANE: Mapping[str, str] = {
    "bootstrap": "bootstrap", "llm": "main", "main": "main"}

#: every worker in this repo declares exactly one output binding named ``primary``.
_PRIMARY_OUTPUT = "primary"

#: the only ``RunResult.terminal_status`` a lane artifact may be taken from.
_USABLE_TERMINAL_STATUSES = ("completed", "degraded")


class LauncherError(Exception):
    """Base: the launcher refused to start, continue or complete a run."""


class LaunchRefused(LauncherError):
    """A red line refused the launch (never a partially-run, half-honest state)."""


class PlanExecutionUnwired(LauncherError):
    """The plan-execution backend is not bound — refused, never fabricated.

    Raised instead of returning a synthetic artifact when the ``plan_executor`` or
    the ``sink_artifact_resolver`` is absent. A replay that ran on invented lane
    artifacts would produce curves, a feedback handoff and seats rows that describe
    a decision nothing ever made.
    """


class MultiPointPlanExecutionRefused(LauncherError):
    """A second decision point asked the same admitted plan to execute again.

    See the module docstring: one approved candidate digest ⇒ one admitted ``Plan``
    ⇒ one ``run_id`` ⇒ ``dag.run_plan`` resumes rather than re-executes, so point 2
    would be served point 1's artifacts. Refused at the seam.
    """


# =========================================================================== #
# the event-loop red line                                                      #
# =========================================================================== #
def refuse_if_event_loop_running(what: str) -> None:
    """Refuse ``what`` when this thread is running an asyncio event loop.

    ``run_interval_replay`` is synchronous and ``dag.run_plan`` is a coroutine, so
    the launcher must own a thread with no running loop: blocking the server's loop
    on a multi-minute replay is the failure that took the 9999 process down before.
    A caller inside a route handler must dispatch through ``asyncio.to_thread`` (or
    a plain daemon thread) — this refusal names that remedy.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return
    raise LaunchRefused(
        f"{what} refuses to run on a thread with a running asyncio event loop: the "
        "replay driver is synchronous and the plan runner bridges an async "
        "dag.run_plan, so running here would block the loop for the whole interval "
        "(the failure mode that killed the 9999 server process). Dispatch through "
        "asyncio.to_thread or a dedicated worker thread.")


def run_coroutine_blocking(coroutine_factory: Callable[[], Any]) -> Any:
    """Run ``coroutine_factory()`` to completion on this (loop-free) thread.

    Deliberately NOT a "detect a loop and hop to a worker thread" helper: hopping
    would still block the calling loop on ``.result()``. The honest contract is that
    the whole launch owns a loop-free thread, so this refuses rather than papers
    over a caller that does not.

    **Loop-lifetime hazard, recorded where the bridge lives.** ``asyncio.run``
    creates a FRESH event loop per call and closes it on return, so anything the
    coroutine leaves behind that is bound to that loop is dead afterwards. Today
    nothing is: each ``dag.run_plan`` call is self-contained. But a future
    ``ModelGateway`` holding a long-lived ``httpx.AsyncClient`` (or any loop-bound
    connection pool) constructed on the first call would raise on the second — this
    repo has already been bitten once by httpx binding itself to a loop. Whoever
    binds such a gateway must construct it per call, or own a single long-lived loop
    on the launch thread instead of this per-call ``asyncio.run``.
    """
    refuse_if_event_loop_running("the async plan-execution bridge")
    return asyncio.run(coroutine_factory())


# =========================================================================== #
# R18 — the production plan runner                                             #
# =========================================================================== #
@dataclasses.dataclass(frozen=True)
class LaneExecutionBinding:
    """One lane's admitted-plan identity: what the human approved, made executable.

    ``candidate_plan_digest`` is the digest the R22 card was registered under and the
    coordinator was constructed with; ``reservation_id`` is the plan reservation the
    admission service minted for it (``persist_and_reserve_candidate``); and
    ``approval_event_id`` is the ``PLAN_APPROVED`` event the human's decision
    produced. All three are the *caller's own* admission artifacts — this module
    derives none of them.
    """

    lane: str
    candidate_plan_digest: str
    reservation_id: str
    approval_event_id: str


def pool_sink_artifact_resolver(pool: Any) -> Callable[..., Any]:
    """The real sink-artifact resolver: the plan's first sink node's ``primary``.

    ``RunResult`` carries five scalars and no artifacts, so the produced artifact is
    read back out of the :class:`~guanlan_v2.orchestration.pool.ArtifactPool` the run
    committed into — ``pool.committed_output(node_id, "primary")``, the same read the
    reviewed Phase-2 runtime suite performs. A sink that committed nothing returns
    ``None``, which the runner turns into a loud refusal rather than a silent
    ``None`` artifact.
    """

    def _resolve(*, plan: Any, run_result: Any, lane: str, point: Any) -> Any:
        sinks = tuple(getattr(plan, "sink_node_ids", ()) or ())
        if not sinks:
            raise LaunchRefused(
                f"the admitted plan for the {lane} lane declares no sink node, so it "
                "can produce no lane artifact")
        return pool.committed_output(sinks[0], _PRIMARY_OUTPUT)

    return _resolve


def build_dag_plan_executor(
    *,
    pool: Any,
    budget: Any,
    runtime: Any,
    registry: Any,
    stores: Any,
    runtime_limit: int,
    clock: Any,
    admission: Any,
    model_gateway: Any = None,
    prompt_assembler: Any = None,
    refusal_sink: Any = None,
    runtime_profile: Any = None,
    recorder_factory: Callable[[], Any] | None = None,
) -> Callable[..., Any]:
    """The production plan executor: ``dag.run_plan`` behind the sync bridge.

    Every argument is the caller's own runtime material — this module builds no
    catalog, no pool and no model gateway (there is no production assembler for any
    of them; see the launcher report). ``model_gateway=None`` is legal only for a
    graph with no LLM node: ``dag.run_plan`` does not refuse up front, it fails the
    LLM node with ``executor_setup_failed`` and returns ``terminal_status="failed"``,
    which the runner above refuses rather than reads an artifact from.

    See :func:`run_coroutine_blocking` for the loop-lifetime hazard a future
    loop-bound ``model_gateway`` would hit here: this executor calls ``asyncio.run``
    once per lane per point, and every call closes the loop it created.
    """
    from guanlan_v2.orchestration import dag as _dag

    def _execute(*, plan: Any, run_context: Any, lane: str, point: Any) -> Any:
        recorder = recorder_factory() if recorder_factory is not None else None
        return run_coroutine_blocking(lambda: _dag.run_plan(
            plan.plan_digest, run_context, admission=admission, pool=pool,
            budget=budget, runtime=runtime, registry=registry, stores=stores,
            runtime_limit=int(runtime_limit), clock=clock,
            refusal_sink=refusal_sink, model_gateway=model_gateway,
            prompt_assembler=prompt_assembler, recorder=recorder,
            runtime_profile=runtime_profile))

    return _execute


def build_admitted_plan_runner(
    *,
    admission: Any,
    lane_bindings: Mapping[str, LaneExecutionBinding],
    run_context_factory: Callable[..., Any],
    request_id: str,
    plan_executor: Callable[..., Any] | None = None,
    sink_artifact_resolver: Callable[..., Any] | None = None,
    freeze_idempotency_prefix: str = "replay-freeze",
) -> Callable[..., Any]:
    """R18 — the coordinator's ``plan_runner``, bound to the real admission chain.

    The returned callable matches the seam
    ``ProductionReplayPlanCoordinator`` calls:
    ``(lane, point, approval, data_context, memory_binding, candidate_plan_digest)``.
    Per call, in this order:

    ① the event-loop red line (nothing is frozen from a coroutine);
    ② the digest the coordinator's approval gate just approved is matched against a
       declared :class:`LaneExecutionBinding` — **an unbound digest is refused and
       never freeze-attempted**, so the seam can never substitute an identity;
    ③ ``freeze_and_admit_candidate`` under a lane-constant idempotency key (the
       per-point freeze decision — see the module docstring);
    ④ the single-execution guard (:class:`MultiPointPlanExecutionRefused`);
    ⑤ a per-point ``RunContext`` from ``run_context_factory`` — this is where the
       point's PIT ``DataContext`` and memory binding enter, i.e. Option 1's "the
       context is an input to the approved graph";
    ⑥ ``plan_executor`` (production: :func:`build_dag_plan_executor`), then the sink
       artifact via ``sink_artifact_resolver``.

    ``plan_executor`` / ``sink_artifact_resolver`` unbound ⇒
    :class:`PlanExecutionUnwired`. Never a synthesized artifact.
    """
    by_digest: dict[str, LaneExecutionBinding] = {}
    for lane, binding in lane_bindings.items():
        if binding.candidate_plan_digest in by_digest:
            raise LaunchRefused(
                "two lanes declare the same candidate_plan_digest "
                f"{binding.candidate_plan_digest!r}; one approval cannot stand for "
                "two distinct lane plans")
        by_digest[binding.candidate_plan_digest] = binding
    executed_at: dict[str, int] = {}

    def _runner(*, lane: str, point: Any, approval: Any, data_context: Any,
                memory_binding: Any, candidate_plan_digest: str) -> Any:
        refuse_if_event_loop_running(f"the {lane} lane plan runner")
        binding = by_digest.get(candidate_plan_digest)
        if binding is None:
            raise LaunchRefused(
                f"the {lane} lane was handed candidate {candidate_plan_digest!r}, "
                "which no declared lane binding names; the plan runner never freezes "
                "a digest the launcher did not admit and the human did not approve")
        approval_lane = _RUNNER_LANE_TO_APPROVAL_LANE.get(lane, lane)
        if binding.lane != approval_lane:
            raise LaunchRefused(
                f"candidate {candidate_plan_digest!r} is bound to the "
                f"{binding.lane!r} lane but was handed to the {lane!r} lane")
        if plan_executor is None or sink_artifact_resolver is None:
            missing = "plan_executor" if plan_executor is None else "sink_artifact_resolver"
            raise PlanExecutionUnwired(
                f"the {lane} lane cannot execute: {missing} is unbound. The launcher "
                "refuses to synthesize a lane artifact — a replay built on an invented "
                "proposal would produce curves, a feedback handoff and seats rows "
                "describing a decision nothing ever made.")

        ordinal = int(getattr(point, "point_ordinal", 0))
        first = executed_at.get(candidate_plan_digest)
        if first is not None and first != ordinal:
            raise MultiPointPlanExecutionRefused(
                f"decision point {ordinal} asked the {lane} lane to execute the plan "
                f"already run for point {first}. One approved candidate digest yields "
                "one admitted Plan, one Plan carries one run_id, and dag.run_plan "
                "RESUMES a run_id whose layers are committed instead of re-executing "
                "them — so this point would be served the artifacts point "
                f"{first} committed (a borrowed-snapshot PIT leak). A multi-point "
                "interval needs per-point plans, which needs an approval that "
                "authorizes a family of digests; neither exists yet.")

        plan, _admitted = admission.freeze_and_admit_candidate(
            candidate_plan_digest,
            reservation_id=binding.reservation_id,
            approval_event_id=binding.approval_event_id,
            idempotency_key=f"{freeze_idempotency_prefix}:{request_id}:{binding.lane}")

        run_context = run_context_factory(
            lane=lane, point=point, plan=plan, data_context=data_context,
            memory_binding=memory_binding)
        run_result = plan_executor(
            plan=plan, run_context=run_context, lane=lane, point=point)
        executed_at[candidate_plan_digest] = ordinal

        status = getattr(run_result, "terminal_status", None)
        if status is not None and status not in _USABLE_TERMINAL_STATUSES:
            raise LaunchRefused(
                f"the {lane} lane plan run terminated {status!r}; no lane artifact is "
                "taken from a run that did not complete (a failed LLM node yields "
                "'failed', never a usable proposal)")
        artifact = sink_artifact_resolver(
            plan=plan, run_result=run_result, lane=lane, point=point)
        if artifact is None:
            raise LaunchRefused(
                f"the {lane} lane plan run committed no sink artifact; the launcher "
                "refuses to continue with nothing rather than stage an empty decision")
        return artifact

    return _runner


# =========================================================================== #
# the outcome record                                                           #
# =========================================================================== #
@dataclasses.dataclass(frozen=True)
class ReplayLaunchOutcome:
    """The honest record of one launch — including everything it could NOT reach.

    ``status`` is the single field an operator reads:

    ``awaiting_approval``  a lane still needs a human; the driver never started.
    ``completed``          the interval ran and its realized window was mature, so
                           the Phase-4 feedback ledger was registered.
    ``parked``             the interval ran but its window is immature, so the run is
                           ``WAITING_FOR_MATURITY`` and the ledger was NOT touched.
    ``ran``                the driver completed but no curve report was produced (see
                           ``notes``) — never dressed up as a success.

    ``notes`` names every stage that was skipped and why. A stage is never silently
    absent.
    """

    request_id: str
    status: str
    awaiting_human: tuple[str, ...] = ()
    lane_candidate_digests: Mapping[str, str] = dataclasses.field(default_factory=dict)
    plan_candidate_digest: str | None = None
    execution_config_digest: str | None = None
    schedule_digest: str | None = None
    run_id: str | None = None
    experiment_id: str | None = None
    completed_points: int = 0
    total_points: int = 0
    intent_count: int = 0
    deterministic_target_count: int = 0
    degradation_badges: tuple[str, ...] = ()
    curve_report_ref: Any = None
    state_payload_ref: Any = None
    seats_row_count: int = 0
    notes: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        """A JSON-safe projection (every ref rendered as its string form)."""
        return {
            "request_id": self.request_id,
            "status": self.status,
            "awaiting_human": list(self.awaiting_human),
            "lane_candidate_digests": dict(self.lane_candidate_digests),
            "plan_candidate_digest": self.plan_candidate_digest,
            "execution_config_digest": self.execution_config_digest,
            "schedule_digest": self.schedule_digest,
            "run_id": self.run_id,
            "experiment_id": self.experiment_id,
            "completed_points": self.completed_points,
            "total_points": self.total_points,
            "intent_count": self.intent_count,
            "deterministic_target_count": self.deterministic_target_count,
            "degradation_badges": list(self.degradation_badges),
            "curve_report_ref": None if self.curve_report_ref is None
            else str(self.curve_report_ref),
            "state_payload_ref": None if self.state_payload_ref is None
            else str(self.state_payload_ref),
            "seats_row_count": self.seats_row_count,
            "notes": list(self.notes),
        }


# =========================================================================== #
# launch_interval_replay — the composition                                     #
# =========================================================================== #
def _lane_digests(cards: Any) -> tuple[dict[str, str], dict[str, str]]:
    """``(digests, unavailable)`` — a lane that cannot name its digest is NAMED.

    The earlier shape swallowed the exception and dropped the lane, which was a
    silent fail-open on a record whose entire purpose is honesty: a renamed or
    raising cards object would have produced an outcome missing a lane digest AND
    a disarmed :func:`_refuse_forgotten_overrides`. The reason string is carried so
    the caller can refuse (before a run) or note it (while awaiting a human).
    """
    digests: dict[str, str] = {}
    unavailable: dict[str, str] = {}
    for lane in LAUNCH_LANES:
        try:
            digests[lane] = cards.candidate_plan_digest(lane)
        except Exception as exc:  # noqa: BLE001 — reported by name, never dropped
            unavailable[lane] = f"{type(exc).__name__}: {exc}"
    return digests, unavailable


def _refuse_unapproved(request: Any, cards: Any) -> tuple[str, ...]:
    """Every approval red line, before anything is reserved or run."""
    from guanlan_v2.orchestration.enums import ApprovalPolicy

    if getattr(request, "approval_policy", None) is ApprovalPolicy.AUTO:
        raise LaunchRefused(
            "AUTO approval is refused at the launcher door: a shadow replay plan only "
            "ever runs under a REQUIRED human decision (the driver and the replay "
            "coordinator refuse it again, and a pending card is unconstructible for it)")
    rejected = sorted(
        lane for lane, verdict in dict(getattr(cards, "already_decided", {}) or {}).items()
        if str(verdict).lower() == "rejected")
    if rejected:
        raise LaunchRefused(
            f"lane(s) {rejected} carry a REJECTED decision; the interval is not "
            "launched (the coordinator would refuse the lane at its first decision "
            "point anyway, after reserving budget)")
    return tuple(cards.awaiting_human())


def _refuse_forgotten_overrides(
    coordinator: Any, digests: Mapping[str, str]
) -> tuple[str, ...]:
    """R22's trap: a coordinator built without the card digests can never be approved.

    Returns the notes for any lane it could **not** compare. The only fail-open left
    is a coordinator that does not expose the attribute at all (a test double) — and
    that case is now NAMED on the outcome instead of silently passing, because the
    guard's whole value is that a reader can tell whether it actually ran.

    Known coupling, deliberate: the two attributes are the sealed coordinator's
    private lane digests. A rename upstream would turn this guard into a note rather
    than into a false pass, which is why the note exists.
    """
    notes: list[str] = []
    for lane, attribute in (("bootstrap", "_bootstrap_candidate"),
                            ("main", "_main_candidate")):
        held = getattr(coordinator, attribute, None)
        wanted = digests.get(lane)
        if held is None:
            notes.append(
                f"the {lane} lane override could NOT be verified: this coordinator "
                f"exposes no {attribute!r}, so the R22 forgotten-overrides guard did "
                "not run for it")
            continue
        if wanted is None:
            raise LaunchRefused(
                f"the coordinator gates the {lane} lane on {held!r} but the cards "
                "cannot name that lane's approved digest, so nothing can confirm the "
                "human decided it; refusing rather than running against an "
                "unverifiable identity")
        if held != wanted:
            raise LaunchRefused(
                f"the coordinator's {lane} lane digest {held!r} is not the digest the "
                f"human decided ({wanted!r}); it was built without the registered "
                "cards' coordinator_kwargs(), so every point would refuse "
                "unapproved. Pass ReplayApprovalCards.coordinator_kwargs().")
    return tuple(notes)


def launch_interval_replay(
    *,
    request: Any,
    schedule: Any,
    execution_config: Any,
    interval_start: Any,
    interval_end: Any,
    cards: Any,
    coordinator: Any,
    bindings: Any,
    shadow_runner: Any = None,
    artifact_pool: Any = None,
    trial_ledger: Any = None,
    maturity_now: Any = None,
    replay_state_store: Any = None,
    decision_artifacts: Any = None,
    seats_run_head: Mapping[str, Any] | None = None,
    is_live_session: Callable[[Any], bool] | None = None,
) -> ReplayLaunchOutcome:
    """Run ONE shadow-replay interval end to end, honestly, and report what it reached.

    The stages, in order, each gated:

    ⓪ the event-loop red line and the approval red lines (``AUTO``, a rejected lane,
      a lane still awaiting a human — the last returns an ``awaiting_approval``
      outcome with **nothing** run);
    ① the driver — ``run_interval_replay`` over the real coordinator, which seals a
      per-point PIT ``DataContext`` at every decision point and gates every lane on
      the durable human decision. ``is_live_session`` is passed straight through: a
      real predicate is what makes the R17 watcher registration fire (and it only
      fires **in the watcher's own process** — run this in the server process);
    ② dual curves under ONE ``ShadowExecutionConfig`` attestation, with the driver's
      per-point degradation badges folded in;
    ③ the maturity-gated Phase-4 feedback handoff (immature ⇒ parked, ledger
      untouched);
    ④ the durable ``ShadowReplayRunState`` head, when a ``ReplayStateStore`` is bound;
    ⑤ the seats-compat rows, when the caller supplies the committed decision
      artifacts and a run head.

    Stages ②–⑤ each degrade to a NOTE on the outcome when their input is absent —
    never to a fabricated artifact and never to silence.
    """
    from guanlan_v2.orchestration.adapters.api import (
        fold_degraded_points_into_report_badges,
        seats_rows_from_committed_decisions,
    )
    from guanlan_v2.orchestration.adapters.luozi import (
        build_dual_curves,
        hand_off_dual_curves_to_feedback,
        persist_replay_state,
    )
    from guanlan_v2.orchestration.enums import ExperimentStatus

    refuse_if_event_loop_running("launch_interval_replay")
    request_id = str(getattr(request, "request_id", "req"))
    digests, unavailable = _lane_digests(cards)
    awaiting = _refuse_unapproved(request, cards)
    if awaiting:
        return ReplayLaunchOutcome(
            request_id=request_id, status="awaiting_approval", awaiting_human=awaiting,
            lane_candidate_digests=digests,
            plan_candidate_digest=getattr(cards, "plan_candidate_digest", None),
            execution_config_digest=execution_config.semantic_digest(),
            schedule_digest=getattr(schedule, "content_digest", None),
            notes=(f"awaiting a human decision on lane(s) {list(awaiting)}; the "
                   "interval was NOT started and no budget was reserved",)
            + tuple(f"lane {lane!r} could not name its approved digest ({reason})"
                    for lane, reason in sorted(unavailable.items())))
    if unavailable:
        raise LaunchRefused(
            "the cards cannot name the approved digest for lane(s) "
            f"{sorted(unavailable)} ({'; '.join(f'{k}: {v}' for k, v in sorted(unavailable.items()))}); "
            "an interval is never launched against a lane identity nothing can confirm")
    notes: list[str] = list(_refuse_forgotten_overrides(coordinator, digests))

    # ── ① the driver ─────────────────────────────────────────────────────── #
    from guanlan_v2.orchestration.adapters.luozi import run_interval_replay

    state = run_interval_replay(
        request=request, schedule=schedule, execution_config=execution_config,
        interval_start=interval_start, interval_end=interval_end, bindings=bindings,
        is_live_session=is_live_session)

    ledger = bindings.intent_ledger
    intents = tuple(ledger.intents)
    targets = tuple(ledger.deterministic_targets)
    degraded = dict(ledger.degraded_points)
    badges = tuple(sorted({b for row in degraded.values() for b in row}))
    status = "ran"
    curve_ref = None
    state_ref = None
    seats_rows = 0

    # ── ② dual curves ────────────────────────────────────────────────────── #
    report = None
    if shadow_runner is None:
        notes.append("no shadow_runner bound: dual curves NOT built")
    elif not intents:
        notes.append(
            "the LLM shadow lane produced no intent, so no curve was built "
            "(build_dual_curves requires at least one intent — a curve names the "
            "intent digests it applied)")
    elif not targets:
        notes.append("the deterministic lane produced no target set: dual curves NOT built")
    else:
        report = build_dual_curves(
            execution_config=execution_config,
            points=tuple(_resolve_points(schedule, bindings, interval_start, interval_end)),
            deterministic_target_sets=targets, intents=intents,
            shadow_runner=shadow_runner)
        report = fold_degraded_points_into_report_badges(report, degraded)
        badges = tuple(sorted(set(badges) | set(report.badges)))

    # ── ③ the maturity-gated feedback handoff ────────────────────────────── #
    if report is not None:
        if artifact_pool is None or trial_ledger is None or maturity_now is None:
            notes.append(
                "curves were built but the feedback handoff was NOT attempted "
                "(artifact_pool / trial_ledger / maturity_now unbound)")
        else:
            state = hand_off_dual_curves_to_feedback(
                report, run_id=state.run_id, pool=artifact_pool, ledger=trial_ledger,
                maturity_now=maturity_now)
            curve_ref = state.curve_report_ref
            status = ("parked" if state.status is ExperimentStatus.WAITING_FOR_MATURITY
                      else "completed")

    # ── ④ the durable head ───────────────────────────────────────────────── #
    if replay_state_store is None:
        notes.append(
            "no ReplayStateStore bound: the run state was NOT persisted and "
            "/orchestration/replay/state cannot serve this run")
    else:
        state_ref = persist_replay_state(
            state, stores=replay_state_store,
            idempotency_key=f"launch-replay:{state.run_id}:{state.status.value}")

    # ── ⑤ the seats-compat rows ──────────────────────────────────────────── #
    committed = decision_artifacts() if callable(decision_artifacts) else decision_artifacts
    if not committed or seats_run_head is None:
        notes.append(
            "no committed decision artifacts / run head supplied: the seats-compat "
            "rows were NOT produced")
    else:
        rows = seats_rows_from_committed_decisions(
            tuple(committed), run_head=dict(seats_run_head))
        seats_rows = len(rows)

    return ReplayLaunchOutcome(
        request_id=request_id, status=status, awaiting_human=(),
        lane_candidate_digests=digests,
        plan_candidate_digest=getattr(cards, "plan_candidate_digest", None),
        execution_config_digest=execution_config.semantic_digest(),
        schedule_digest=getattr(schedule, "content_digest", None),
        run_id=state.run_id, experiment_id=state.experiment_id,
        completed_points=int(state.completed_points),
        total_points=int(state.total_points),
        intent_count=len(intents), deterministic_target_count=len(targets),
        degradation_badges=badges, curve_report_ref=curve_ref,
        state_payload_ref=state_ref, seats_row_count=seats_rows,
        notes=tuple(notes))


def _resolve_points(schedule: Any, bindings: Any, interval_start: Any,
                    interval_end: Any) -> Sequence[Any]:
    """The same decision points the driver walked — resolved from the same inputs.

    Recomputed rather than captured: ``resolve_decision_points`` is pure over
    (schedule, calendar, interval), so this is the identical tuple by construction,
    and it keeps the launcher off the sealed driver's internals.
    """
    from guanlan_v2.orchestration.adapters.luozi import resolve_decision_points

    return resolve_decision_points(
        schedule, calendar=bindings.calendar, interval_start=interval_start,
        interval_end=interval_end)
