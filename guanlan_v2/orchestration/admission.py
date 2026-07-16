# -*- coding: utf-8 -*-
"""Phase 2 · Task 6 — service-owned Plan admission and freeze.

The authorization heart of the static runtime: the fixed
``validate -> runtime-support -> persist-both-reports -> atomic same-digest
reservation -> same-digest REQUIRED approval -> Phase-1 freeze -> append
PlanAdmitted -> dispatch`` state machine. Every authority — the plan budget
reservation, the ``PlanApproval`` and the ``StaticLegacyPlanAttestation`` — is
**loaded** from a service-owned store; a caller submits only *identifiers* and an
authenticated approval decision and can never inject a constructed record as
authority.

Load-bearing design
-------------------
* **Consume, never fork.** Admission calls the Phase-1
  :func:`~guanlan_v2.orchestration.spec.validate_plan_draft` /
  :func:`~guanlan_v2.orchestration.spec.compute_candidate_plan_digest` /
  :func:`~guanlan_v2.orchestration.spec.freeze_plan` and the pure Task-5
  :func:`~guanlan_v2.orchestration.runtime_support.check_runtime_support`; there is
  no second plan digest or alternate freeze. ``Plan.plan_digest`` *equals* the
  validated ``candidate_plan_digest``.
* **Report<->draft binding seam.** ``check_runtime_support`` takes no request and
  trusts ``phase1_report.candidate_plan_digest``. This service holds the
  request/draft, recomputes the candidate digest and requires it to equal *both*
  the Phase-1 report's and the support report's bound digest before any
  persistence or reservation (:func:`check_report_binding`).
* **Fixed order, atomic boundaries.** Both reports persist as typed payloads
  **before** any budget event. When supported, the ``AdmissionCandidate`` payload,
  its ``AdmissionCandidatePrepared`` RunEvent and the plan-budget reservation are
  ONE :class:`~guanlan_v2.orchestration.eventstore.RuntimeUnitOfWork` batch; the
  frozen ``Plan`` payload, the ``PlanAdmitted`` control payload and its public
  RunEvent are a SECOND. A REJECTED approval / a drift invalidation each carry
  their payload + RunEvent + reservation release in one all-or-none batch.
* **AUTO always fails; PRESET provenance grants nothing.** ``ApprovalPolicy.AUTO``
  is rejected by Phase 1 for every source (so an AUTO draft is never even
  supported), and a supported PRESET candidate still needs a same-digest REQUIRED
  ``PlanApproval`` on file before it can freeze.

Event-type mapping (reviewed)
-----------------------------
The Phase-1 :class:`~guanlan_v2.orchestration.events.EventType` vocabulary is
frozen and carries no admission-specific members, so admission RunEvents reuse the
closest existing types: candidate-prepared -> ``PlanDrafted``; approve/reject ->
``PlanApproved`` / ``PlanRejected``; drift invalidation -> ``PlanRejected`` (over
an ``AdmissionInvalidated`` payload); admitted -> ``PlanFrozen`` (the public
admission/visibility boundary), whose payload is the persisted ``PlanAdmitted``
witness (a main-namespace payload on a main-partition event, per the ``events.py``
namespace matrix); the witness's nested ``plan_payload_ref`` resolves the frozen
``Plan`` payload committed in the same unit of work.

Witness persistence (reviewer-prescribed unlock)
------------------------------------------------
The Task 2 :class:`~guanlan_v2.orchestration.eventstore.PayloadStore` digests a
strict *non*-DigestModel runtime fact over its canonical JSON dump (the audit-sink
precedent — see ``eventstore._payload_content_digest``), so the four ``_StrictModel``
admission witnesses persist as registry-validated typed payloads exactly as the
brief states: both reports before any budget event (step 4); the
``AdmissionCandidate`` payload inside the candidate+event+reservation unit of work
(step 5); the ``AdmissionInvalidated`` payload inside the invalidation+release unit
of work (step 8); and the ``PlanAdmitted`` payload inside the Plan+admitted-event
unit of work (step 10). ``load_admitted`` is a pure store read of the persisted
witness, and ``verify_for_dispatch`` compares current recomputations **against the
persisted witness** — real drift detection, not recomputed-vs-recomputed.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from guanlan_v2.orchestration.budget import (
    BudgetLedger,
    BudgetTransitionCommand,
    ReleaseArgs,
    ReservePlanArgs,
)
from guanlan_v2.orchestration.catalog_runtime import (
    BridgeCatalogView,
    CatalogRuntime,
)
from guanlan_v2.orchestration.context import (
    ContextRuntimeRequirements,
    ContextSnapshot,
    RunBudget,
    verify_context_runtime_requirements,
)
from guanlan_v2.orchestration.enums import ApprovalDecision
from guanlan_v2.orchestration.events import EventType, PlanApproval, RunEvent
from guanlan_v2.orchestration.eventstore import (
    EventAppendCommand,
    EventStoreError,
    PayloadPutCommand,
    RuntimeBatch,
    RuntimeStores,
    StagedPayloadKey,
    StagedPayloadRef,
)
from guanlan_v2.orchestration.refs import PayloadRef, SchemaRef
from guanlan_v2.orchestration.runtime_clock import AuthoritativeClock
from guanlan_v2.orchestration.runtime_contracts import (
    AdmissionCandidate,
    AdmissionInvalidated,
    NamedEvidenceDigest,
    PlanAdmitted,
    ResolvedContextRuntimeRequirements,
    RuntimeSupportReport,
    StaticRuntimeProfile,
)
from guanlan_v2.orchestration.runtime_support import check_runtime_support
from guanlan_v2.orchestration.schema_registry import SchemaRegistry
from guanlan_v2.orchestration.spec import (
    OrchestrationRequest,
    Plan,
    PlanDraft,
    PlanValidationReport,
    StaticLegacyPlanAttestation,
    compute_candidate_plan_digest,
    freeze_plan,
    validate_plan_draft,
)

__all__ = [
    "AdmissionError",
    "AdmissionRejected",
    "ApprovalSubmission",
    "PreparedAdmissionCandidate",
    "AdmissionDispatchBundle",
    "PlanAdmissionService",
    "check_report_binding",
]


# --------------------------------------------------------------------------- #
# Schema refs of the persisted control payloads (cumulative Phase-2 registry)   #
# --------------------------------------------------------------------------- #
_PLAN_VALIDATION_REPORT_SR = SchemaRef(name="PlanValidationReport", version="1")
_RUNTIME_SUPPORT_REPORT_SR = SchemaRef(name="RuntimeSupportReport", version="1")
_ADMISSION_CANDIDATE_SR = SchemaRef(name="AdmissionCandidate", version="1")
_ADMISSION_INVALIDATED_SR = SchemaRef(name="AdmissionInvalidated", version="1")
_PLAN_ADMITTED_SR = SchemaRef(name="PlanAdmitted", version="1")
_PLAN_APPROVAL_SR = SchemaRef(name="PlanApproval", version="1")
_PLAN_SR = SchemaRef(name="Plan", version="1")

#: reviewed EventType mapping onto the frozen Phase-1 vocabulary (see module doc).
_CANDIDATE_PREPARED_EVENT = EventType.PLAN_DRAFTED
_APPROVED_EVENT = EventType.PLAN_APPROVED
_REJECTED_EVENT = EventType.PLAN_REJECTED
_INVALIDATED_EVENT = EventType.PLAN_REJECTED
_ADMITTED_EVENT = EventType.PLAN_FROZEN


# --------------------------------------------------------------------------- #
# Errors + caller-facing DTOs                                                 #
# --------------------------------------------------------------------------- #
class AdmissionError(Exception):
    """Base for every admission-service failure."""


class AdmissionRejected(AdmissionError):
    """A candidate cannot be admitted (invalid / unsupported / missing-or-forged
    authority / drift). Carries an optional machine ``code`` and — for an
    unsupported candidate — the diagnostic :class:`RuntimeSupportReport`."""

    def __init__(self, message: str, *, code: str = "admission_rejected",
                 support_report: RuntimeSupportReport | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.support_report = support_report


@dataclass(frozen=True)
class ApprovalSubmission:
    """The authenticated approval *decision* a caller records — identifiers only.

    It is deliberately **not** a :class:`PlanApproval`: the authoritative approval
    record is loaded from the service-owned approvals store and cross-checked
    against this submission, so a caller can never submit a constructed authority.
    """

    request_id: str
    candidate_plan_digest: str
    decision: ApprovalDecision


@dataclass(frozen=True)
class PreparedAdmissionCandidate:
    """A fully validated, support-checked candidate — *not* persisted authority.

    Carries the resolved candidate contents and the two report models but is
    neither loadable admission authority nor approval/reservation evidence: only
    :meth:`PlanAdmissionService.persist_and_reserve_candidate` turns it into a
    persisted :class:`AdmissionCandidate` + a live reservation.
    """

    run_id: str
    draft_id: str
    request: OrchestrationRequest
    draft: PlanDraft
    context: ContextSnapshot | None
    resolved_requirements: ResolvedContextRuntimeRequirements | None
    legacy_attestation: StaticLegacyPlanAttestation | None
    phase1_report: PlanValidationReport
    support_report: RuntimeSupportReport
    candidate_plan_digest: str
    context_content_digest: str | None


@dataclass(frozen=True)
class AdmissionDispatchBundle:
    """The verified authoritative bundle :meth:`verify_for_dispatch` returns."""

    plan: Plan
    plan_admitted: PlanAdmitted
    reservation: Any  # BudgetReservation
    phase1_report: PlanValidationReport
    support_report: RuntimeSupportReport
    approval: PlanApproval
    resolved_requirements: ResolvedContextRuntimeRequirements | None


# --------------------------------------------------------------------------- #
# Report<->draft binding seam (Task 5 review constraint 1)                     #
# --------------------------------------------------------------------------- #
def check_report_binding(
    candidate_plan_digest: str,
    phase1_report: PlanValidationReport,
    support_report: RuntimeSupportReport,
) -> str:
    """Require both reports to bind the *exact* recomputed candidate digest.

    ``check_runtime_support`` takes no request and trusts the digest bound in the
    Phase-1 report; this seam refuses a valid report generated for a *different*
    draft (report-for-draft-B + draft-A) before any persistence or reservation.
    Returns ``candidate_plan_digest`` on success; raises :class:`AdmissionRejected`
    otherwise.
    """
    if phase1_report.candidate_plan_digest != candidate_plan_digest:
        raise AdmissionRejected(
            "Phase-1 validation report is bound to a different candidate plan digest "
            "than the draft under admission (report<->draft binding violation)",
            code="report_binding_mismatch",
        )
    if support_report.candidate_plan_digest != candidate_plan_digest:
        raise AdmissionRejected(
            "runtime-support report is bound to a different candidate plan digest "
            "than the draft under admission (report<->draft binding violation)",
            code="report_binding_mismatch",
        )
    return candidate_plan_digest


# --------------------------------------------------------------------------- #
# Internal live candidate state (service-owned; rebuilt from stores on replay)  #
# --------------------------------------------------------------------------- #
@dataclass
class _CandidateState:
    prep: PreparedAdmissionCandidate
    candidate: AdmissionCandidate
    reservation_id: str
    approval_event_id: str | None = None


# --------------------------------------------------------------------------- #
# The service                                                                 #
# --------------------------------------------------------------------------- #
class PlanAdmissionService:
    """Service-owned Plan admission coordinator (single run).

    All authoritative inputs (request / draft / context / catalog / registry /
    legacy attestation / approval) are supplied as service-owned stores; the public
    transitions receive identifiers and an authenticated :class:`ApprovalSubmission`
    only.
    """

    def __init__(
        self,
        *,
        run_id: str,
        requests: Mapping[str, OrchestrationRequest],
        drafts: Mapping[str, PlanDraft],
        contexts: Mapping[str, ContextSnapshot],
        attestations: Mapping[str, StaticLegacyPlanAttestation],
        approvals: Mapping[tuple[str, str], PlanApproval],
        catalog: CatalogRuntime,
        bridge_view: BridgeCatalogView,
        phase1_registry: SchemaRegistry,
        runtime_registry_digest: str,
        profile: StaticRuntimeProfile,
        stores: RuntimeStores,
        run_budget: RunBudget,
        clock: AuthoritativeClock,
    ) -> None:
        # authoritative stores are held by reference: a service-owned store can be
        # updated out of band (an input can drift after preparation), and admission
        # re-loads from it at freeze / dispatch.
        self._run_id = run_id
        self._requests = requests
        self._drafts = drafts
        self._contexts = contexts
        self._attestations = attestations
        self._approvals = approvals
        self._catalog = catalog
        self._bridge_view = bridge_view
        self._phase1_registry = phase1_registry
        self._rt_digest = runtime_registry_digest
        self._profile = profile
        self._stores = stores
        self._run_budget = run_budget
        self._clock = clock
        # the cumulative runtime registry must resolve every payload this service
        # persists (both reports, all four control witnesses, approval and Plan).
        rt = stores.resolver.resolve(runtime_registry_digest)
        for sr in (_PLAN_VALIDATION_REPORT_SR, _RUNTIME_SUPPORT_REPORT_SR,
                   _ADMISSION_CANDIDATE_SR, _ADMISSION_INVALIDATED_SR,
                   _PLAN_ADMITTED_SR, _PLAN_APPROVAL_SR, _PLAN_SR):
            rt.resolve(sr)
        # first-bind the maxima-only run-budget authority (idempotent rebind).
        stores.bind_run_budget(run_id=run_id, run_budget=run_budget)
        self._ledger = BudgetLedger(
            sink=stores.budget_event_sink(run_id=run_id, ledger_id=run_budget.ledger_id),
            run_budget=run_budget,
        )
        self._candidates: dict[str, _CandidateState] = {}

    # ------------------------------------------------------------------ #
    # Step 1-3 — prepare (pure: validate + support, no persistence)      #
    # ------------------------------------------------------------------ #
    def prepare_candidate(self, draft_id: str, *, request_id: str) -> PreparedAdmissionCandidate:
        draft = self._drafts.get(draft_id)
        if draft is None:
            raise AdmissionRejected(f"unknown draft {draft_id!r}", code="unknown_draft")
        request = self._requests.get(request_id)
        if request is None:
            raise AdmissionRejected(f"unknown request {request_id!r}", code="unknown_request")
        if draft.request_id != request_id:
            raise AdmissionRejected(
                "draft.request_id does not match the supplied request id", code="request_mismatch")

        # -- load + verify the ContextSnapshot -------------------------- #
        context = self._load_context(draft)

        # -- exact catalog / registry identity -------------------------- #
        if draft.catalog_digest != self._catalog.catalog_digest:
            raise AdmissionRejected("draft.catalog_digest does not match the admitted catalog",
                                    code="catalog_drift")
        if draft.schema_registry_digest != self._phase1_registry.registry_digest:
            raise AdmissionRejected("draft.schema_registry_digest does not match the admitted registry",
                                    code="registry_drift")

        context_content_digest = context.content_digest if context is not None else None
        candidate = compute_candidate_plan_digest(
            request=request, draft=draft, context_content_digest=context_content_digest)

        # -- resolve the ContextRuntimeRequirements typed ref ----------- #
        rcr = self._resolve_requirements(context)

        # -- one service-owned legacy attestation (never caller-carried) - #
        attestation = self._attestations.get(candidate)

        # -- Phase-1 validation + pure runtime support ------------------ #
        phase1 = validate_plan_draft(
            draft, request=request, context=context, catalog=self._catalog.snapshot,
            schema_registry=self._phase1_registry, legacy_attestation=attestation)
        support = check_runtime_support(
            draft, phase1_report=phase1, context=context, context_requirements=rcr,
            catalog=self._catalog, bridge_view=self._bridge_view,
            schema_registry=self._phase1_registry, profile=self._profile)

        # -- the report<->draft binding seam (before ANY persistence) --- #
        check_report_binding(candidate, phase1, support)

        return PreparedAdmissionCandidate(
            run_id=self._run_id, draft_id=draft_id, request=request, draft=draft,
            context=context, resolved_requirements=rcr, legacy_attestation=attestation,
            phase1_report=phase1, support_report=support, candidate_plan_digest=candidate,
            context_content_digest=context_content_digest)

    # ------------------------------------------------------------------ #
    # Step 4-5 — persist both reports, then candidate + event + reserve   #
    # ------------------------------------------------------------------ #
    def persist_and_reserve_candidate(
        self, preparation: PreparedAdmissionCandidate, *, idempotency_key: str,
    ):
        candidate = preparation.candidate_plan_digest

        # defensive seam re-check: recompute the candidate digest from the carried
        # request/draft/context and require both reports to bind it — a caller who
        # assembled a forged PreparedAdmissionCandidate (it is a plain dataclass)
        # is refused before any persistence or reservation.
        recomputed = compute_candidate_plan_digest(
            request=preparation.request, draft=preparation.draft,
            context_content_digest=preparation.context_content_digest)
        if recomputed != candidate:
            raise AdmissionRejected(
                "PreparedAdmissionCandidate.candidate_plan_digest does not recompute from "
                "its carried request/draft/context", code="report_binding_mismatch")
        check_report_binding(recomputed, preparation.phase1_report, preparation.support_report)

        # -- (4) BOTH reports persist as typed payloads BEFORE any budget event
        self._stores.payloads.put(
            _PLAN_VALIDATION_REPORT_SR, preparation.phase1_report,
            registry_digest=self._rt_digest, namespace="main",
            idempotency_key=f"phase1-report:{candidate}")
        self._stores.payloads.put(
            _RUNTIME_SUPPORT_REPORT_SR, preparation.support_report,
            registry_digest=self._rt_digest, namespace="main",
            idempotency_key=f"support-report:{candidate}")

        if not preparation.support_report.supported:
            raise AdmissionRejected(
                "candidate is not runtime-supported; no AdmissionCandidate or reservation "
                "is created", code="unsupported",
                support_report=preparation.support_report)

        candidate_model = self._build_candidate(preparation)

        # -- (5) AdmissionCandidate payload + prepared event + reservation
        #        as ONE all-or-none RuntimeUnitOfWork batch.
        batch = RuntimeBatch(
            idempotency_key=idempotency_key,
            payload_puts=(
                PayloadPutCommand(
                    staged_key=StagedPayloadKey(key="candidate"),
                    schema_ref=_ADMISSION_CANDIDATE_SR, namespace="main",
                    payload_template=dict(candidate_model),
                    registry_digest=self._rt_digest,
                    idempotency_key=f"{idempotency_key}:candidate-payload"),
            ),
            event_appends=(
                EventAppendCommand(
                    run_id=self._run_id, partition="main",
                    event_type=_CANDIDATE_PREPARED_EVENT.value,
                    payload_schema_ref=_ADMISSION_CANDIDATE_SR,
                    payload_target=StagedPayloadKey(key="candidate"),
                    registry_digest=self._rt_digest,
                    idempotency_key=f"{idempotency_key}:candidate-event",
                    plan_digest=candidate),
            ),
            budget_commands=(
                BudgetTransitionCommand(
                    operation="reserve_plan",
                    semantic_args=ReservePlanArgs(
                        request_id=preparation.request.request_id,
                        candidate_plan_digest=candidate,
                        reserved_tokens=preparation.draft.budget_request_tokens,
                        reserved_llm_invocations=preparation.draft.budget_request_llm_invocations,
                        reserved_concurrency=preparation.draft.max_concurrency),
                    idempotency_key=f"{idempotency_key}:reserve"),
            ),
            budget_run_id=self._run_id,
        )
        result = self._stores.unit_of_work.commit(batch)
        reservation_id = result.budget_events[0].reservation_id
        reservation = self._ledger.get(reservation_id)
        if reservation is None:  # pragma: no cover - defensive
            raise AdmissionError("reservation vanished after commit")
        self._candidates[candidate] = _CandidateState(
            prep=preparation, candidate=candidate_model, reservation_id=reservation_id)
        return candidate_model, reservation

    # ------------------------------------------------------------------ #
    # Step 6-7 — approval (loaded authority; REJECTED releases)          #
    # ------------------------------------------------------------------ #
    def record_approval(
        self, candidate_id: str, approval_input: ApprovalSubmission, *,
        authenticated_actor: str, idempotency_key: str,
    ) -> RunEvent:
        if not isinstance(approval_input, ApprovalSubmission):
            raise AdmissionRejected(
                "record_approval accepts only an authenticated ApprovalSubmission; a "
                "caller-constructed authority record is not authority",
                code="caller_carried_authority")
        if not authenticated_actor or not authenticated_actor.strip():
            raise AdmissionRejected("an authenticated actor is required to record approval",
                                    code="unauthenticated")
        state = self._candidates.get(candidate_id)
        if state is None:
            raise AdmissionRejected("no reserved candidate for this digest", code="unknown_candidate")
        if approval_input.candidate_plan_digest != candidate_id:
            raise AdmissionRejected("approval submission names a different candidate digest",
                                    code="wrong_candidate")
        request_id = state.prep.request.request_id
        if approval_input.request_id != request_id:
            raise AdmissionRejected("approval submission names a different request id",
                                    code="wrong_request")

        # -- load the authoritative PlanApproval (never caller-carried) - #
        approval = self._approvals.get((request_id, candidate_id))
        if approval is None:
            raise AdmissionRejected("no PlanApproval is on file for this request/candidate",
                                    code="missing_approval")
        if approval.actor_id != authenticated_actor:
            raise AdmissionRejected("the authenticated actor does not own the loaded approval",
                                    code="wrong_actor")
        if approval.request_id != request_id or approval.candidate_plan_digest != candidate_id:
            raise AdmissionRejected("the loaded approval does not bind this request/candidate",
                                    code="approval_binding_mismatch")
        if approval.decision != approval_input.decision:
            raise AdmissionRejected("the submitted decision does not match the loaded approval",
                                    code="decision_mismatch")

        approved = approval.decision is ApprovalDecision.APPROVED
        event_type = _APPROVED_EVENT if approved else _REJECTED_EVENT
        budget_commands: tuple[BudgetTransitionCommand, ...] = ()
        budget_run_id = None
        if not approved:
            # a REJECTED decision releases the plan reservation in the SAME batch.
            budget_commands = (
                BudgetTransitionCommand(
                    operation="release",
                    semantic_args=ReleaseArgs(reservation_id=state.reservation_id,
                                              reason="approval_rejected"),
                    idempotency_key=f"{idempotency_key}:release"),
            )
            budget_run_id = self._run_id

        batch = RuntimeBatch(
            idempotency_key=idempotency_key,
            payload_puts=(
                PayloadPutCommand(
                    staged_key=StagedPayloadKey(key="approval"),
                    schema_ref=_PLAN_APPROVAL_SR, namespace="main",
                    payload_template=dict(approval),
                    registry_digest=self._rt_digest,
                    idempotency_key=f"{idempotency_key}:approval-payload"),
            ),
            event_appends=(
                EventAppendCommand(
                    run_id=self._run_id, partition="main", event_type=event_type.value,
                    payload_schema_ref=_PLAN_APPROVAL_SR,
                    payload_target=StagedPayloadKey(key="approval"),
                    registry_digest=self._rt_digest,
                    idempotency_key=f"{idempotency_key}:approval-event",
                    plan_digest=candidate_id),
            ),
            budget_commands=budget_commands,
            budget_run_id=budget_run_id,
        )
        result = self._stores.unit_of_work.commit(batch)
        event = result.events[0]
        if approved:
            state.approval_event_id = event.event_id
        return event

    # ------------------------------------------------------------------ #
    # Step 8-10 — reload + rerun, freeze, Plan+PlanAdmitted UoW          #
    # ------------------------------------------------------------------ #
    def freeze_and_admit_candidate(
        self, candidate_id: str, *, reservation_id: str, approval_event_id: str,
        idempotency_key: str,
    ):
        # already-admitted short-circuit: a retry — even under a FRESH idempotency
        # key — returns the existing Plan + persisted witness and can never mint a
        # duplicate PlanFrozen event or a second admitted payload.
        existing = self._maybe_admitted_event(candidate_id)
        if existing is not None:
            return self.load_admitted(candidate_id)

        state = self._candidates.get(candidate_id)
        if state is None:
            raise AdmissionRejected("no reserved candidate for this digest", code="unknown_candidate")

        # -- load the active plan reservation (authority, not caller) --- #
        reservation = self._ledger.get(reservation_id)
        if reservation is None or reservation.reservation_id != state.reservation_id:
            raise AdmissionRejected("reservation_id does not name this candidate's plan reservation",
                                    code="unknown_reservation")
        active = self._ledger.get_active_plan(state.prep.request.request_id, candidate_id)
        if active is None or active.reservation_id != reservation_id:
            raise AdmissionRejected("the plan reservation is not active (rejected/released/settled)",
                                    code="reservation_not_active")

        # -- load the APPROVED PlanApproval authority from its event ---- #
        approval, approval_event = self._load_approval_event(approval_event_id, candidate_id)
        if not approval.authorizes_freeze(request_id=state.prep.request.request_id,
                                          candidate_plan_digest=candidate_id):
            raise AdmissionRejected("the approval event does not authorize this freeze",
                                    code="not_authorized")

        # -- (8) reload all authoritative inputs + rerun; require equality  #
        draft = self._drafts.get(state.prep.draft_id)
        request = self._requests.get(state.prep.request.request_id)
        if draft is None or request is None:
            raise AdmissionRejected("an authoritative input vanished before freeze", code="input_missing")
        context = self._load_context(draft, soft=True)
        rcr = self._resolve_requirements(context, soft=True)
        attestation = self._attestations.get(candidate_id)

        drifted = self._detect_drift(state, draft, request, context, rcr, attestation)
        if drifted:
            self._invalidate(state, draft, request, context, rcr, attestation,
                             reservation_id, drifted, idempotency_key)
            raise AdmissionRejected(
                "an authoritative input drifted after preparation; the candidate is "
                "invalidated and its reservation released", code="drift_invalidated")

        # -- (9) Phase-1 freeze (never recompute the projection locally) - #
        plan = freeze_plan(
            draft, request=request, context=context, catalog=self._catalog.snapshot,
            schema_registry=self._phase1_registry, legacy_attestation=attestation,
            report=state.prep.phase1_report, reservation=reservation, approval=approval,
            frozen_at=self._clock.now())

        # -- (10) frozen Plan payload + PlanAdmitted witness payload + the public
        #        PlanFrozen event as ONE all-or-none RuntimeUnitOfWork batch. The
        #        event's payload IS the persisted PlanAdmitted witness (main
        #        namespace on a main partition per the events.py matrix); the
        #        witness's nested plan_payload_ref is a staged sentinel the unit
        #        of work resolves to the frozen Plan payload in the same batch.
        admitted_template = self._build_admitted_template(
            state, plan, reservation, approval, approval_event_id)
        batch = RuntimeBatch(
            idempotency_key=idempotency_key,
            payload_puts=(
                PayloadPutCommand(
                    staged_key=StagedPayloadKey(key="plan"),
                    schema_ref=_PLAN_SR, namespace="main",
                    payload_template=dict(plan),
                    registry_digest=self._rt_digest,
                    idempotency_key=f"{idempotency_key}:plan-payload"),
                PayloadPutCommand(
                    staged_key=StagedPayloadKey(key="admitted"),
                    schema_ref=_PLAN_ADMITTED_SR, namespace="main",
                    payload_template=admitted_template,
                    registry_digest=self._rt_digest,
                    idempotency_key=f"{idempotency_key}:admitted-payload"),
            ),
            event_appends=(
                EventAppendCommand(
                    run_id=self._run_id, partition="main", event_type=_ADMITTED_EVENT.value,
                    payload_schema_ref=_PLAN_ADMITTED_SR,
                    payload_target=StagedPayloadKey(key="admitted"),
                    registry_digest=self._rt_digest,
                    idempotency_key=f"{idempotency_key}:admitted-event",
                    plan_digest=plan.plan_digest,
                    causation_id=approval_event_id,
                    correlation_id=reservation.reservation_id),
            ),
        )
        result = self._stores.unit_of_work.commit(batch)
        admitted = self._stores.payloads.get(
            result.payload_ref("admitted"), expected_schema_ref=_PLAN_ADMITTED_SR)
        return plan, admitted

    # ------------------------------------------------------------------ #
    # Step 11a — load an admitted plan (pure read of the persisted witness) #
    # ------------------------------------------------------------------ #
    def load_admitted(self, plan_digest: str):
        event = self._find_admitted_event(plan_digest)
        admitted = self._stores.payloads.get(event.payload_ref, expected_schema_ref=_PLAN_ADMITTED_SR)
        if not isinstance(admitted, PlanAdmitted):  # pragma: no cover - schema guarded
            raise AdmissionError("admitted event does not reference a PlanAdmitted payload")
        if admitted.plan_digest != plan_digest:  # pragma: no cover - event bound by plan_digest
            raise AdmissionError("persisted PlanAdmitted witness does not bind this plan digest")
        plan = self._stores.payloads.get(admitted.plan_payload_ref, expected_schema_ref=_PLAN_SR)
        return plan, admitted

    # ------------------------------------------------------------------ #
    # Step 11 — verify for dispatch (recompute + verify everything)      #
    # ------------------------------------------------------------------ #
    def verify_for_dispatch(self, plan_digest: str) -> AdmissionDispatchBundle:
        plan, admitted = self.load_admitted(plan_digest)

        # -- the frozen Plan recomputes its own candidate/plan digest --- #
        if plan.recompute_plan_digest() != plan.plan_digest or plan.plan_digest != plan_digest:
            raise AdmissionRejected("persisted Plan digest does not recompute", code="plan_digest_drift")
        if admitted.plan_digest != plan_digest or admitted.candidate_plan_digest != plan_digest:
            raise AdmissionRejected("PlanAdmitted plan/candidate digest mismatch", code="admitted_drift")

        draft = plan.draft
        request = self._requests.get(plan.request_id)
        if request is None:
            raise AdmissionRejected("admitted request is no longer authoritative", code="request_missing")
        context = self._load_context(draft, soft=True)
        rcr = self._resolve_requirements(context, soft=True)
        attestation = self._attestations.get(plan_digest)

        # -- catalog / registry closure -------------------------------- #
        if admitted.catalog_digest != self._catalog.catalog_digest:
            raise AdmissionRejected("catalog digest drift at dispatch", code="catalog_drift")
        if admitted.schema_registry_digest != self._phase1_registry.registry_digest:
            raise AdmissionRejected("registry digest drift at dispatch", code="registry_drift")
        if admitted.runtime_profile_digest != self._profile.profile_digest:
            raise AdmissionRejected("runtime profile drift at dispatch", code="profile_drift")

        # -- rerun validation + support; require the bound digests ------ #
        phase1 = validate_plan_draft(
            draft, request=request, context=context, catalog=self._catalog.snapshot,
            schema_registry=self._phase1_registry, legacy_attestation=attestation)
        if phase1.semantic_digest() != admitted.phase1_report_digest:
            raise AdmissionRejected("Phase-1 report drift at dispatch", code="phase1_report_drift")
        support = check_runtime_support(
            draft, phase1_report=phase1, context=context, context_requirements=rcr,
            catalog=self._catalog, bridge_view=self._bridge_view,
            schema_registry=self._phase1_registry, profile=self._profile)
        if not support.supported or support.report_digest != admitted.support_report_digest:
            raise AdmissionRejected("runtime-support drift at dispatch", code="support_drift")

        # -- ContextRuntimeRequirements closure ------------------------- #
        if (admitted.context_requirements_ref is None) != (rcr is None):
            raise AdmissionRejected("context requirements presence drift at dispatch",
                                    code="requirements_drift")
        if rcr is not None and (
            admitted.context_requirements_ref != rcr.typed_ref
            or admitted.context_requirements_digest != rcr.fact.requirements_digest
        ):
            raise AdmissionRejected("context requirements drift at dispatch", code="requirements_drift")

        # -- the active plan reservation binds the same semantic digest - #
        active = self._ledger.get_active_plan(request.request_id, plan_digest)
        if active is None or active.reservation_id != admitted.reservation_id:
            raise AdmissionRejected("no active plan reservation at dispatch", code="reservation_not_active")
        if active.semantic_digest() != admitted.reservation_semantic_digest:
            raise AdmissionRejected("reservation semantic digest drift at dispatch",
                                    code="reservation_drift")

        # -- the approval authority still binds ------------------------- #
        approval, _ = self._load_approval_event(admitted.approval_event_id, plan_digest)
        if not approval.authorizes_freeze(request_id=request.request_id, candidate_plan_digest=plan_digest):
            raise AdmissionRejected("approval no longer authorizes dispatch", code="approval_drift")
        if approval.semantic_digest() != admitted.approval_digest:
            raise AdmissionRejected("approval semantic digest drift at dispatch", code="approval_drift")

        return AdmissionDispatchBundle(
            plan=plan, plan_admitted=admitted, reservation=active, phase1_report=phase1,
            support_report=support, approval=approval, resolved_requirements=rcr)

    # ================================================================== #
    # helpers                                                            #
    # ================================================================== #
    def _load_context(self, draft: PlanDraft, *, soft: bool = False) -> ContextSnapshot | None:
        ref = draft.context_snapshot_ref
        if ref is None:
            return None
        if ref.namespace != "main":
            raise AdmissionRejected("context_snapshot_ref must be main-namespace", code="context_namespace")
        ctx = self._contexts.get(ref.content_digest)
        if ctx is None:
            if soft:
                raise AdmissionRejected("the admitted context is no longer authoritative",
                                        code="context_missing")
            raise AdmissionRejected("no ContextSnapshot bound to the draft's context ref",
                                    code="context_missing")
        if ctx.content_digest != ref.content_digest:  # pragma: no cover - dict keyed by digest
            raise AdmissionRejected("context content digest mismatch", code="context_drift")
        return ctx

    def _resolve_requirements(
        self, context: ContextSnapshot | None, *, soft: bool = False
    ) -> ResolvedContextRuntimeRequirements | None:
        if context is None or context.runtime_requirements_ref is None:
            return None
        typed_ref = context.runtime_requirements_ref
        try:
            fact = self._stores.payloads.get(typed_ref.payload_ref, expected_schema_ref=typed_ref.schema_ref)
        except EventStoreError as exc:
            raise AdmissionRejected(
                f"could not resolve the ContextRuntimeRequirements typed ref: {exc}",
                code="requirements_unresolved") from exc
        if not isinstance(fact, ContextRuntimeRequirements):  # pragma: no cover - schema guarded
            raise AdmissionRejected("resolved requirements payload is not ContextRuntimeRequirements",
                                    code="requirements_unresolved")
        try:
            rcr = ResolvedContextRuntimeRequirements(typed_ref=typed_ref, fact=fact)
            verify_context_runtime_requirements(context, fact)
        except ValueError as exc:
            raise AdmissionRejected(f"resolved requirements do not bind the context: {exc}",
                                    code="requirements_subject_mismatch") from exc
        return rcr

    def _build_candidate(self, prep: PreparedAdmissionCandidate) -> AdmissionCandidate:
        rcr = prep.resolved_requirements
        return AdmissionCandidate(
            run_id=self._run_id, request_id=prep.request.request_id,
            candidate_plan_digest=prep.candidate_plan_digest,
            support_report_digest=prep.support_report.report_digest,
            phase1_report_digest=prep.phase1_report.semantic_digest(),
            runtime_profile_digest=self._profile.profile_digest,
            catalog_digest=self._catalog.catalog_digest,
            schema_registry_digest=self._phase1_registry.registry_digest,
            context_content_digest=prep.context_content_digest,
            context_requirements_ref=(rcr.typed_ref if rcr is not None else None),
            context_requirements_digest=(rcr.fact.requirements_digest if rcr is not None else None),
            legacy_attestation_digest=(
                prep.legacy_attestation.semantic_digest() if prep.legacy_attestation is not None else None),
        )

    def _build_admitted_template(
        self, state: _CandidateState, plan: Plan, reservation,
        approval: PlanApproval, approval_event_id: str,
    ) -> dict[str, Any]:
        """The PlanAdmitted witness template; ``plan_payload_ref`` is a staged
        sentinel the unit of work resolves to the same-batch frozen Plan payload."""
        prep = state.prep
        rcr = prep.resolved_requirements
        return {
            "schema_version": "1",
            "run_id": self._run_id,
            "request_id": prep.request.request_id,
            "plan_digest": plan.plan_digest,
            "candidate_plan_digest": prep.candidate_plan_digest,
            "phase1_report_digest": prep.phase1_report.semantic_digest(),
            "support_report_digest": prep.support_report.report_digest,
            "runtime_profile_digest": self._profile.profile_digest,
            "catalog_digest": self._catalog.catalog_digest,
            "schema_registry_digest": self._phase1_registry.registry_digest,
            "context_content_digest": prep.context_content_digest,
            "context_requirements_ref": (rcr.typed_ref if rcr is not None else None),
            "context_requirements_digest": (rcr.fact.requirements_digest if rcr is not None else None),
            "reservation_id": reservation.reservation_id,
            "reservation_semantic_digest": reservation.semantic_digest(),
            "approval_event_id": approval_event_id,
            "approval_digest": approval.semantic_digest(),
            "legacy_attestation_digest": (
                prep.legacy_attestation.semantic_digest() if prep.legacy_attestation is not None else None),
            "plan_payload_ref": StagedPayloadRef(
                staged_key=StagedPayloadKey(key="plan"), schema_ref=_PLAN_SR, namespace="main"),
        }

    def _detect_drift(self, state, draft, request, context, rcr, attestation) -> tuple[NamedEvidenceDigest, ...]:
        """Recompute Phase-1 + support over the reloaded inputs and return the exact
        drifted authoritative-input evidence (empty when nothing drifted)."""
        drifted: dict[str, str] = {}
        phase1 = validate_plan_draft(
            draft, request=request, context=context, catalog=self._catalog.snapshot,
            schema_registry=self._phase1_registry, legacy_attestation=attestation)
        if phase1 != state.prep.phase1_report:
            drifted["phase1_report"] = phase1.semantic_digest()
        support = check_runtime_support(
            draft, phase1_report=phase1, context=context, context_requirements=rcr,
            catalog=self._catalog, bridge_view=self._bridge_view,
            schema_registry=self._phase1_registry, profile=self._profile)
        if support != state.prep.support_report:
            drifted["support_report"] = support.report_digest
        return tuple(
            NamedEvidenceDigest(name=name, digest=digest)
            for name, digest in sorted(drifted.items()))

    def _invalidate(self, state, draft, request, context, rcr, attestation, reservation_id,
                    drifted, idempotency_key: str) -> None:
        """The only drift-after-preparation recovery path: persist the
        ``AdmissionInvalidated`` witness, append its RunEvent and release the
        reservation as ONE all-or-none unit of work."""
        invalidated = AdmissionInvalidated(
            run_id=self._run_id, candidate_plan_digest=state.prep.candidate_plan_digest,
            reservation_id=reservation_id, drifted_inputs=drifted,
            reason_code="authoritative_input_drift")
        batch = RuntimeBatch(
            idempotency_key=f"{idempotency_key}:invalidate",
            payload_puts=(
                PayloadPutCommand(
                    staged_key=StagedPayloadKey(key="invalidated"),
                    schema_ref=_ADMISSION_INVALIDATED_SR, namespace="main",
                    payload_template=dict(invalidated),
                    registry_digest=self._rt_digest,
                    idempotency_key=f"{idempotency_key}:invalidated-payload"),
            ),
            event_appends=(
                EventAppendCommand(
                    run_id=self._run_id, partition="main", event_type=_INVALIDATED_EVENT.value,
                    payload_schema_ref=_ADMISSION_INVALIDATED_SR,
                    payload_target=StagedPayloadKey(key="invalidated"),
                    registry_digest=self._rt_digest,
                    idempotency_key=f"{idempotency_key}:invalidated-event",
                    plan_digest=state.prep.candidate_plan_digest,
                    correlation_id=reservation_id),
            ),
            budget_commands=(
                BudgetTransitionCommand(
                    operation="release",
                    semantic_args=ReleaseArgs(reservation_id=reservation_id, reason="drift_invalidated"),
                    idempotency_key=f"{idempotency_key}:invalidate-release"),
            ),
            budget_run_id=self._run_id,
        )
        self._stores.unit_of_work.commit(batch)

    def _load_approval_event(self, approval_event_id: str, candidate_id: str):
        event = None
        for ev in self._stores.events.journal(self._run_id, "main"):
            if ev.event_id == approval_event_id:
                event = ev
                break
        if event is None:
            raise AdmissionRejected("no approval event with this id", code="unknown_approval_event")
        if event.event_type is not _APPROVED_EVENT:
            raise AdmissionRejected("the approval event is not an APPROVED PlanApproval event",
                                    code="approval_not_approved")
        approval = self._stores.payloads.get(event.payload_ref, expected_schema_ref=_PLAN_APPROVAL_SR)
        if not isinstance(approval, PlanApproval):  # pragma: no cover - schema guarded
            raise AdmissionRejected("approval event does not reference a PlanApproval payload",
                                    code="approval_payload_mismatch")
        return approval, event

    def _maybe_admitted_event(self, plan_digest: str):
        for ev in self._stores.events.journal(self._run_id, "main"):
            if ev.event_type is _ADMITTED_EVENT and ev.plan_digest == plan_digest:
                return ev
        return None

    def _find_admitted_event(self, plan_digest: str):
        event = self._maybe_admitted_event(plan_digest)
        if event is None:
            raise AdmissionError(f"no admitted plan for digest {plan_digest!r}")
        return event
