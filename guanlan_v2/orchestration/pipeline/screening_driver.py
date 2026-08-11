# -*- coding: utf-8 -*-
"""L3 · the A-line screening EXECUTOR — the downstream driver the door names.

``pipeline/api.py`` opens the screening door and STOPS at admission ("even a
lease-admitted candidate awaits the downstream driver", api.py's STOP-LINE
comment), and ``screening.admit_screening_batch`` stops in the same place by its
own docstring ("it never freezes a plan, never dispatches"). Until this module
landed, NOTHING stood on the other side of that sentence: the 2026-08-10 live
campaign admitted two lanes under a real screening ``ApprovalLease`` and then
executed neither — a consumed human authorization with zero execution, the
exact burned-lease shape ``live_decide``'s binding block records for the deep
lane. This module IS the missing side.

**Where the shape comes from.** Nothing here is invented: the per-lane sequence
is the reviewed Phase-10 e2e A-chain (``test_phase10_e2e``'s step-5 loop), and
the post-admission idiom is ``live_decide``'s deep lane, verbatim in order:
``register_and_try_lease`` → (``lease_admitted`` only) → ``admit_after_approval``
→ ``build_production_plan_runner`` through the injected factory seam, with the
lane's own ``SubjectPromptAssembler`` and its materialization-stamped
``subject_params``.

**The three facts that force the plumbing** (each measured at source, none
stylistic):

1. ``PlanAdmissionService.freeze_and_admit_candidate`` requires the RESERVING
   instance's in-memory candidate state, and ``record_approval`` likewise — so
   the whole batch cannot share one service if its lanes are to execute.
2. ``dag.run_plan`` refuses a ``RunContext`` whose ``run_id`` is not the
   admitted plan's, and the execution ``BudgetLedger`` folds budget events by
   ``(run_id, ledger_id)`` — so a lane's admission service must be created under
   THAT lane's ``draft.run_id``, or the plan reservation is invisible at the
   first layer boundary ("the active plan reservation is gone").
3. ``admit_screening_batch`` discards the ``reservation`` its
   ``persist_and_reserve_candidate`` returned, and the freeze needs its id — so
   the executor REPLAYS that unit of work under the SAME idempotency key
   (:func:`~guanlan_v2.orchestration.pipeline.screening.
   screening_reserve_idempotency_key`). Whole-batch idempotency returns the
   original result, so the replay recovers the id and mints nothing.

(1) + (2) are why :class:`LaneAdmissionDispatcher` exists — the reviewed
"dumb per-candidate dispatcher" the Phase-10 e2e documented and this module
promotes to production: it forwards by identity and never decides.

**What this driver will not do.** It executes ``lease_admitted`` lanes and
nothing else — a ``pending_human`` lane is a card waiting for a human and is
never run, never freed, never re-carded. It mints no approval, issues no lease,
and never touches the lease channel: by the time control reaches here the
authorization was already spent by ``register_and_try_lease``.

**A failing lane never takes the batch down.** Lanes are executed sequentially
(the provider seam is serialized anyway) and each lane's failure is caught,
logged with its traceback and recorded as that lane's honest ``failed`` outcome;
the remaining lanes still run. That is not swallowing: nothing claims the lane
succeeded, the reason travels on the outcome, into the router's durable request
record and out onto the wire.

**The product tail** is the reviewed one, unchanged: ``build_recommendation_slate``
folds only lanes with a committed ``ResearchPlan@1``, ``render_recommendation_md``
renders it banner-first and ``land_recommendation`` files it through the injected
archive callable, idempotent per batch by its day-scoped id. A batch that
completed nothing lands NOTHING — an empty document filed under the day's id
would overwrite a real one with a lie of omission.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Sequence

from guanlan_v2.orchestration.adapters.launcher import LaneExecutionBinding
from guanlan_v2.orchestration.approval import admit_after_approval
from guanlan_v2.orchestration.context import RunBudget, RunContext
from guanlan_v2.orchestration.pipeline.contracts import RecommendationSlate, RunSubject
from guanlan_v2.orchestration.pipeline.live_decide import SubjectPromptAssembler
from guanlan_v2.orchestration.pipeline.screening import (
    RECOMMENDATION_ADVISORY_BANNER,
    MaterializedScreeningBatch,
    MaterializedScreeningLane,
    ScreeningError,
    build_recommendation_slate,
    land_recommendation,
    recommendation_archive_id,
    render_recommendation_md,
    screening_reserve_idempotency_key,
)

__all__ = [
    # lane / batch vocabularies
    "LANE_COMPLETED",
    "LANE_FAILED",
    "LANE_PENDING_HUMAN",
    "BATCH_AWAITING_APPROVAL",
    "BATCH_COMPLETED",
    "BATCH_FAILED",
    "BATCH_PARTIAL",
    "LEASE_ADMITTED",
    # errors
    "SubjectBondRefused",
    # carriers
    "LaneExecution",
    "ScreeningExecutionReport",
    # surface
    "LaneAdmissionDispatcher",
    "build_lane_admission",
    "execute_screening_batch",
    "slate_projection",
    "production_archive_put",
]

_LOG = logging.getLogger("guanlan.orchestration.pipeline.screening_driver")

#: the ONE outcome value that authorizes execution (the coordinator's own
#: vocabulary — ``pending_human`` is the only other, and it never executes).
LEASE_ADMITTED = "lease_admitted"

#: per-lane execution vocabulary (closed; never invented per call site).
LANE_COMPLETED = "completed"
LANE_FAILED = "failed"
LANE_PENDING_HUMAN = "pending_human"

#: batch-level status vocabulary, folded from the lane outcomes (see
#: :func:`_batch_status` for the exact rule — never a mood word).
BATCH_AWAITING_APPROVAL = "awaiting_approval"
BATCH_COMPLETED = "completed"
BATCH_PARTIAL = "partial"
BATCH_FAILED = "failed"

#: the archive artifact envelope this driver files, mirroring the console
#: ``_archive_research`` shape (console/api.py:354) field for field.
_ARCHIVE_TITLE = "观澜 · 编排选股推荐"
_ARCHIVE_KIND = "选股"
_ARCHIVE_FROM = "编排 · A 线选股(screening_driver)"


class SubjectBondRefused(ScreeningError):
    """The lane's committed subject ref does not bind the subject we rebuilt."""


class _ScreeningPoint:
    """The single decision point of one screening lane (the runner's point seam)."""

    point_ordinal = 1


# =========================================================================== #
# carriers (plain frozen dataclasses — the ContractModel firewall stays inert)  #
# =========================================================================== #
@dataclass(frozen=True)
class LaneExecution:
    """One lane's honest execution outcome — completed, failed, or never run."""

    code: str
    lane_index: int
    run_id: str
    candidate_plan_digest: str
    status: Literal["completed", "failed", "pending_human"]
    reason: str | None = None

    def as_row(self) -> dict[str, Any]:
        """The JSON row the router records and serves (no field invented)."""
        row: dict[str, Any] = {
            "code": self.code, "lane_index": self.lane_index,
            "run_id": self.run_id, "status": self.status,
            "candidate_plan_digest": self.candidate_plan_digest,
        }
        if self.reason is not None:
            row["reason"] = self.reason
        return row


@dataclass(frozen=True)
class ScreeningExecutionReport:
    """What the drive really did: per lane, then the product it could build."""

    status: Literal["awaiting_approval", "completed", "partial", "failed"]
    lanes: tuple[LaneExecution, ...]
    slate: RecommendationSlate | None = None
    archive_id: str | None = None
    landed: bool = False
    landing_error: str | None = None
    projection: dict[str, Any] | None = None

    def count(self, status: str) -> int:
        return sum(1 for lane in self.lanes if lane.status == status)

    def as_row(self) -> dict[str, Any]:
        row: dict[str, Any] = {
            "status": self.status,
            "lanes": [lane.as_row() for lane in self.lanes],
            "completed": self.count(LANE_COMPLETED),
            "failed": self.count(LANE_FAILED),
            "pending_human": self.count(LANE_PENDING_HUMAN),
        }
        if self.archive_id is not None:
            row["archive_id"] = self.archive_id
            row["landed"] = self.landed
        if self.landing_error is not None:
            row["landing_error"] = self.landing_error
        if self.projection is not None:
            row["slate"] = self.projection
        return row


# =========================================================================== #
# the per-candidate admission dispatcher (promoted from the reviewed e2e)       #
# =========================================================================== #
class LaneAdmissionDispatcher:
    """Per-candidate admission routing under ONE coordinator. It never decides.

    Promoted verbatim from the Phase-10 e2e's ``_LaneAdmissionDispatcher`` (the
    Task-8-documented resolution), for the two source facts the module docstring
    records: ``record_approval`` requires the reserving instance, and lane
    execution requires ``plan.run_id == admission run_id``. Every method
    forwards by identity; the class holds no policy of its own.

    The executor additionally reads :meth:`service_for` / :meth:`run_budget_for`,
    because the freeze, ``verify_for_dispatch`` and the run's budget authority
    are all the LANE's, not the batch's.
    """

    def __init__(self) -> None:
        self._by_draft: dict[str, Any] = {}
        self._by_digest: dict[str, Any] = {}
        self._approvals: dict[str, dict] = {}
        self._budgets: dict[str, RunBudget] = {}

    # -- binding -------------------------------------------------------- #
    def bind(self, *, draft_id: str, digest: str, service: Any,
             approvals: dict, run_budget: RunBudget) -> None:
        self._by_draft[draft_id] = service
        self._by_digest[digest] = service
        self._approvals[digest] = approvals
        self._budgets[digest] = run_budget

    def service_for(self, digest: str) -> Any:
        service = self._by_digest.get(digest)
        if service is None:
            raise KeyError(
                f"no admission service is bound for candidate {digest!r}; "
                "refusing to execute a lane this dispatcher never reserved")
        return service

    def run_budget_for(self, digest: str) -> RunBudget:
        budget = self._budgets.get(digest)
        if budget is None:
            raise KeyError(
                f"no run budget is bound for candidate {digest!r}")
        return budget

    # -- the forwarded admission surface -------------------------------- #
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


def build_lane_admission(
    build: MaterializedScreeningBatch,
    *,
    admission_factory,
    stores: Any,
    context: Any,
) -> LaneAdmissionDispatcher:
    """One REAL admission service per lane, bound into one dispatcher.

    Each lane's service is created under the lane's OWN ``draft.run_id`` with
    the lane's own maxima-only :class:`RunBudget` (``led-<run id>``), which is
    what makes the lane executable at all (module docstring, fact 2). The
    caller's ``admission_factory`` is the router's injected seam, called with
    singleton request/draft maps — the same ABI the batch call used.

    Binding a run budget is not a reservation: it declares the run's maxima so
    two unit-of-work batches can never validate against inconsistent ones.
    Nothing here reserves, cards, approves or executes.
    """
    dispatcher = LaneAdmissionDispatcher()
    for lane in build.lanes:
        run_budget = RunBudget(
            ledger_id=f"led-{lane.draft.run_id}",
            max_tokens=lane.draft.budget_request_tokens,
            max_llm_invocations=lane.draft.budget_request_llm_invocations,
            max_concurrency=lane.draft.max_concurrency)
        stores.bind_run_budget(run_id=lane.draft.run_id, run_budget=run_budget)
        approvals: dict = {}
        service = admission_factory(
            run_id=lane.draft.run_id,
            requests={lane.request.request_id: lane.request},
            drafts={lane.draft.id: lane.draft},
            context=context, approvals=approvals, run_budget=run_budget)
        dispatcher.bind(draft_id=lane.draft.id,
                        digest=lane.candidate_plan_digest,
                        service=service, approvals=approvals,
                        run_budget=run_budget)
    return dispatcher


# =========================================================================== #
# the executor                                                                 #
# =========================================================================== #
def _run_subject_of(lane: MaterializedScreeningLane, *, as_of) -> RunSubject:
    """Rebuild the lane's committed subject and PROVE it is that subject.

    ``MaterializedScreeningLane`` carries the committed ref, not the object, so
    the executor reconstructs ``RunSubject(code, batch.as_of)`` — the exact
    recipe ``build_screening_batch`` used — and then re-checks the digest bond
    the builder checked. Without that check a drifted ``as_of`` would hand the
    prompt assembler a subject the committed artifact never described, and every
    ``PromptAssemblyRecord`` would cite a digest for text it did not produce.
    """
    subject = RunSubject(code=lane.code, as_of=as_of)
    if lane.subject_ref.payload_ref.content_digest != subject.semantic_digest():
        raise SubjectBondRefused(
            f"lane {lane.lane_index} ({lane.code}): the committed subject ref "
            "does not bind the subject rebuilt from the batch's as_of; refusing "
            "to execute under a subject the committed artifact never described")
    return subject


def _execute_lane(
    lane: MaterializedScreeningLane,
    outcome: Any,
    *,
    batch,
    dispatcher: LaneAdmissionDispatcher,
    context: Any,
    plan_runner_factory,
) -> Any:
    """Freeze the lane's admitted plan and run it. The deep-lane idiom, verbatim."""
    service = dispatcher.service_for(lane.candidate_plan_digest)
    run_budget = dispatcher.run_budget_for(lane.candidate_plan_digest)

    # -- recover the reservation the admission discarded (a REPLAY, never a
    #    second reservation: same unit of work, same idempotency key).
    prep = service.prepare_candidate(
        lane.draft.id, request_id=lane.request.request_id)
    _candidate, reservation = service.persist_and_reserve_candidate(
        prep, idempotency_key=screening_reserve_idempotency_key(
            batch.batch_id, lane.code))

    # -- the freeze, on the authority the lease already minted --------------- #
    admit_after_approval(
        admission=service, candidate_id=lane.candidate_plan_digest,
        reservation_id=reservation.reservation_id,
        approval_event_id=outcome.event.event_id,
        idempotency_key=f"screening-freeze:{batch.batch_id}:{lane.code}")

    subject = _run_subject_of(lane, as_of=batch.as_of)
    assembler = SubjectPromptAssembler(
        subject=subject, subject_ref=lane.subject_ref)
    binding = LaneExecutionBinding(
        lane="main", candidate_plan_digest=lane.candidate_plan_digest,
        reservation_id=reservation.reservation_id,
        approval_event_id=outcome.event.event_id)

    def _run_context_factory(*, lane, point, plan, data_context, memory_binding):
        # ``plan.run_id`` deliberately (dag.run_plan refuses a cross-run
        # RunContext); the budget is the LANE's, the one its reservation was
        # written against.
        return RunContext(
            run_id=plan.run_id, data=data_context,
            context_snapshot_id=context.snapshot_id,
            memory_snapshot_hash=context.memory_snapshot_hash,
            budget=run_budget,
            cancellation_token_id=f"cancel-{plan.run_id}")

    runner = plan_runner_factory(
        admission=service, lane_bindings={"main": binding},
        run_context_factory=_run_context_factory,
        request_id=lane.request.request_id, prompt_assembler=assembler,
        # L1 Task 4 (ONE-recipe provenance): the runner receives THE
        # materialization-stamped, digest-bonded projection — never a fresh
        # ``SubjectParams.project`` here, which would bypass the bond check.
        # No longer vacuous: ``text.news`` carries reviewed prefetch rows (L3
        # D-1), so this stamp feeds a real param binding on every lane.
        subject_params=lane.subject_params)
    return runner(
        lane="main", point=_ScreeningPoint(), approval=None,
        data_context=context.data_context, memory_binding=None,
        candidate_plan_digest=lane.candidate_plan_digest)


def _batch_status(lanes: Sequence[LaneExecution]) -> str:
    """The fold, stated rather than felt.

    * nothing was admitted ⇒ ``awaiting_approval`` (the door's own posture — the
      cards are registered and every lane waits for a human);
    * every admitted lane completed and none is still pending ⇒ ``completed``;
    * at least one completed, plus a failure or a still-pending card ⇒
      ``partial`` — a caller must never read a half-run batch as a whole one;
    * something was admitted and nothing completed ⇒ ``failed``.
    """
    completed = sum(1 for lane in lanes if lane.status == LANE_COMPLETED)
    failed = sum(1 for lane in lanes if lane.status == LANE_FAILED)
    pending = sum(1 for lane in lanes if lane.status == LANE_PENDING_HUMAN)
    if completed == 0 and failed == 0:
        return BATCH_AWAITING_APPROVAL
    if completed == 0:
        return BATCH_FAILED
    if failed == 0 and pending == 0:
        return BATCH_COMPLETED
    return BATCH_PARTIAL


def slate_projection(
    slate: RecommendationSlate,
    *,
    build: MaterializedScreeningBatch,
    archive_id: str,
    landed: bool,
) -> dict[str, Any]:
    """The wire projection of a landed slate — the ``/screening/latest`` shape.

    Built HERE, where the batch's own lanes are in hand, so the degraded lanes
    are NAMED from the materialized lane list rather than re-joined later
    (``degraded_lanes`` is index-only on the contract). Every value is copied
    from the slate; this function computes nothing.
    """
    code_by_lane = {lane.lane_index: lane.code for lane in build.lanes}
    return {
        "as_of": slate.as_of.isoformat(),
        "batch_id": slate.batch_id,
        "archive_id": archive_id,
        "advisory_banner": RECOMMENDATION_ADVISORY_BANNER,
        "entries": [
            {"code": entry.code, "lane_index": entry.lane_index,
             "rating": entry.rating,
             "research_plan_digest":
                 entry.research_plan_ref.payload_ref.content_digest}
            for entry in slate.entries
        ],
        "degraded": [
            {"lane_index": int(idx), "code": code_by_lane.get(int(idx))}
            for idx in slate.degraded_lanes
        ],
        "badges": list(slate.badges),
        "landed": bool(landed),
    }


def execute_screening_batch(
    build: MaterializedScreeningBatch,
    *,
    outcomes: Sequence[Any],
    dispatcher: LaneAdmissionDispatcher,
    stores: Any,
    context: Any,
    plan_runner_factory,
    archive_put=None,
) -> ScreeningExecutionReport:
    """Execute every LEASE-ADMITTED lane, then assemble → render → land.

    ``outcomes`` is ``admit_screening_batch``'s return, positionally paired with
    ``build.lanes`` (both are batch order); a length mismatch is a wiring fault
    and refuses rather than guessing which outcome belongs to which lane.

    Sequential by construction: the provider seam is serialized anyway, and a
    lane is a real-money LLM run — an operator reading the log must be able to
    say which lane spent what, in order.
    """
    if len(outcomes) != len(build.lanes):
        raise ScreeningError(
            f"{len(outcomes)} admission outcomes for {len(build.lanes)} lanes; "
            "the pairing is positional and refusing beats guessing")

    batch = build.batch
    executions: list[LaneExecution] = []
    for lane, outcome in zip(build.lanes, outcomes):
        if getattr(outcome, "outcome", None) != LEASE_ADMITTED:
            # a pending card is a human's to decide — never executed here, and
            # its reservation is deliberately NOT released (the Task-3 ruling:
            # the budget must be there if the human approves).
            executions.append(LaneExecution(
                code=lane.code, lane_index=lane.lane_index,
                run_id=lane.draft.run_id,
                candidate_plan_digest=lane.candidate_plan_digest,
                status=LANE_PENDING_HUMAN,
                reason="no lease admission; the card awaits a human decision"))
            continue
        try:
            _execute_lane(
                lane, outcome, batch=batch, dispatcher=dispatcher,
                context=context, plan_runner_factory=plan_runner_factory)
        except Exception as exc:  # noqa: BLE001 — one lane's failure is ITS
            # failure: recorded with its traceback and carried out on the wire,
            # never swallowed and never allowed to abort the sibling lanes.
            _LOG.warning("screening lane %s (%s) failed: %s", lane.lane_index,
                         lane.code, exc, exc_info=True)
            executions.append(LaneExecution(
                code=lane.code, lane_index=lane.lane_index,
                run_id=lane.draft.run_id,
                candidate_plan_digest=lane.candidate_plan_digest,
                status=LANE_FAILED, reason=f"{type(exc).__name__}: {exc}"))
            continue
        executions.append(LaneExecution(
            code=lane.code, lane_index=lane.lane_index,
            run_id=lane.draft.run_id,
            candidate_plan_digest=lane.candidate_plan_digest,
            status=LANE_COMPLETED))

    lanes = tuple(executions)
    status = _batch_status(lanes)
    if not any(lane.status == LANE_COMPLETED for lane in lanes):
        # nothing ran to a verdict ⇒ no product. The day-scoped archive id is an
        # UPSERT, so filing an empty document here would overwrite a real one.
        return ScreeningExecutionReport(status=status, lanes=lanes)

    slate = build_recommendation_slate(batch, stores=stores)
    if not slate.entries:
        _LOG.warning(
            "screening batch %s: %s lane(s) completed but none committed a "
            "ResearchPlan@1; nothing is landed", batch.batch_id,
            sum(1 for lane in lanes if lane.status == LANE_COMPLETED))
        return ScreeningExecutionReport(status=status, lanes=lanes, slate=slate)

    archive_id = recommendation_archive_id(slate)
    landed, landing_error = False, None
    if archive_put is None:
        landing_error = "archive_put_unwired"
        _LOG.warning("screening batch %s built a slate but no archive seam is "
                     "wired; it is NOT filed", batch.batch_id)
    else:
        try:
            land_recommendation(slate, stores=stores, archive_put=archive_put)
        except Exception as exc:  # noqa: BLE001 — the runs happened; the FILING
            # failed. ``land_recommendation`` propagates by design and this is
            # the caller half of that contract: the failure is recorded on the
            # report, logged with its traceback and served on the wire — it is
            # never converted into a success, and never allowed to delete the
            # receipt of runs that really occurred.
            landing_error = f"{type(exc).__name__}: {exc}"
            _LOG.warning("screening batch %s: the recommendation could not be "
                         "filed: %s", batch.batch_id, exc, exc_info=True)
        else:
            landed = True
    return ScreeningExecutionReport(
        status=status, lanes=lanes, slate=slate, archive_id=archive_id,
        landed=landed, landing_error=landing_error,
        projection=slate_projection(slate, build=build, archive_id=archive_id,
                                    landed=landed))


# =========================================================================== #
# the production archive seam                                                  #
# =========================================================================== #
def production_archive_put(artifact_id: str, artifact_type: str,
                           markdown: str) -> None:
    """File the rendered document into the GL archive shadow store.

    The envelope mirrors the console ``_archive_research`` idiom (console/api.py)
    field for field — same ``type``/``kind``/``status``/``path`` shape the GL bus
    already reads — and the id is the slate's own day-scoped one, so the write is
    an upsert exactly like ``POST /archive/put``'s.

    **The one deliberate deviation, recorded:** the console idiom posts through
    an in-process self-HTTP call. This driver runs INSIDE a ``/start`` request's
    worker thread, and a re-entrant self-HTTP from a served request is the house
    hazard that has killed 9999 before, so the transport here is the archive
    router's OWN file store — the same directory, read through the router module
    so a relocation can never fork into two archives.

    The markdown is written beside it under ``out/`` (the research-artifact
    precedent: the envelope carries a ``path``, not the document body), and both
    writes are keyed by the artifact id, so re-landing the same slate overwrites
    itself and can never mint a second entry.
    """
    from guanlan_v2.archive import api as _archive_api

    out_dir = Path(_archive_api.__file__).resolve().parents[2] / "out"
    out_dir.mkdir(parents=True, exist_ok=True)
    md_path = out_dir / f"{artifact_id}.md"
    md_path.write_text(markdown, encoding="utf-8")

    archive_dir = Path(_archive_api._DIR)  # noqa: SLF001 — ONE archive directory
    archive_dir.mkdir(parents=True, exist_ok=True)
    envelope = {
        "id": artifact_id, "type": artifact_type, "title": _ARCHIVE_TITLE,
        "kind": _ARCHIVE_KIND, "from": _ARCHIVE_FROM, "status": "raw",
        "path": str(md_path), "date": _archive_date_of(artifact_id),
        "refs": [], "ts": int(time.time() * 1000),
    }
    (archive_dir / f"{artifact_id}.json").write_text(
        json.dumps(envelope, ensure_ascii=False), encoding="utf-8")


def _archive_date_of(artifact_id: str) -> str:
    """The envelope's ``date``, read off the id's own day suffix (never today's).

    The id is day-scoped by the slate's ``as_of`` (the data's +08:00 session
    day), so stamping the wall clock here would date a PIT replay's artifact
    with the day it was re-filed. A suffix that is not eight digits yields an
    empty string — an honest blank, never a guessed day.
    """
    tail = artifact_id.rsplit("_", 1)[-1]
    if len(tail) != 8 or not tail.isdigit():
        return ""
    return f"{tail[:4]}-{tail[4:6]}-{tail[6:]}"
