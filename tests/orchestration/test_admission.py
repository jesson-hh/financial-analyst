# -*- coding: utf-8 -*-
"""Phase 2 · Task 6 — service-owned Plan admission + freeze.

Locks :class:`~guanlan_v2.orchestration.admission.PlanAdmissionService`, the
authorization heart of the static runtime — the fixed
``validate -> support -> persist-reports -> reserve -> approve -> freeze ->
PlanAdmitted -> dispatch`` state machine. Every authority (reservation, approval,
attestation) is **loaded** from a service-owned store; a caller can never submit a
constructed record as authority.

Run: ``python -m pytest tests/orchestration/test_admission.py -v``
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from guanlan_v2.orchestration.budget import BudgetLedger
from guanlan_v2.orchestration.context import RunBudget
from guanlan_v2.orchestration.enums import ApprovalDecision, ApprovalPolicy, PlanSource
from guanlan_v2.orchestration.events import EventType, PlanApproval
from guanlan_v2.orchestration.eventstore import (
    IdempotencyConflict,
    RuntimeStores,
    SchemaRegistryResolver,
)
from guanlan_v2.orchestration.refs import PayloadRef, SchemaRef, TypedPayloadRef
from guanlan_v2.orchestration.schema_registry import default_registry
from guanlan_v2.orchestration.runtime_contracts import (
    AdmissionCandidate,
    PlanAdmitted,
    phase2_runtime_registry,
    static_runtime_profile,
)
from guanlan_v2.orchestration.spec import (
    OrchestrationRequest,
    Plan,
    compute_candidate_plan_digest,
    validate_plan_draft,
)

from guanlan_v2.orchestration.admission import (
    AdmissionRejected,
    ApprovalSubmission,
    PlanAdmissionService,
    PreparedAdmissionCandidate,
    check_report_binding,
)

# reuse the fully-wired Task-5 scenario builder (catalog / bridge view / context).
from tests.orchestration.test_runtime_support import (
    build_scenario,
    _BridgeSpec,
    _context,
    _requirements_for,
)

UTC = timezone.utc


class FixedClock:
    def now(self) -> datetime:
        return datetime(2026, 7, 16, 1, 30, tzinfo=UTC)


class _Env:
    """A fully-wired admission scenario: service + handles to mutate for drift."""

    def __init__(self, sc, *, approvals=None, attestations=None, run_budget=None):
        self.sc = sc
        self.clock = FixedClock()
        self.resolver = SchemaRegistryResolver()
        self.resolver.register(sc.registry)  # the Phase-1 registry the draft binds
        rt_reg = phase2_runtime_registry(default_registry().registry_digest)
        self.rt_digest = self.resolver.register(rt_reg)
        self.stores = RuntimeStores(resolver=self.resolver, clock=self.clock)
        self.run_budget = run_budget or RunBudget(
            ledger_id="led-1", max_tokens=100_000, max_llm_invocations=100, max_concurrency=16
        )
        self.approvals = approvals if approvals is not None else {}
        self.attestations = attestations if attestations is not None else {}
        self.contexts = {sc.context.content_digest: sc.context} if sc.context is not None else {}
        self.service = self._make_service()

    def _make_service(self, catalog=None, bridge_view=None):
        return PlanAdmissionService(
            run_id=self.sc.draft.run_id,
            requests={self.sc.request.request_id: self.sc.request},
            drafts={self.sc.draft.id: self.sc.draft},
            contexts=self.contexts,
            attestations=self.attestations,
            approvals=self.approvals,
            catalog=catalog or self.sc.runtime,
            bridge_view=bridge_view or self.sc.view,
            phase1_registry=self.sc.registry,
            runtime_registry_digest=self.rt_digest,
            profile=self.sc.profile,
            stores=self.stores,
            run_budget=self.run_budget,
            clock=self.clock,
        )

    def ledger(self) -> BudgetLedger:
        sink = self.stores.budget_event_sink(
            run_id=self.sc.draft.run_id, ledger_id=self.run_budget.ledger_id
        )
        return BudgetLedger(sink=sink, run_budget=self.run_budget)

    def candidate_digest(self) -> str:
        ctx = self.sc.context
        return compute_candidate_plan_digest(
            request=self.sc.request, draft=self.sc.draft,
            context_content_digest=ctx.content_digest if ctx is not None else None,
        )

    def approve(self, *, actor="human-1", decision=ApprovalDecision.APPROVED, candidate=None):
        """Seat an authoritative PlanApproval in the service-owned approvals store."""
        cand = candidate or self.candidate_digest()
        approval = PlanApproval(
            request_id=self.sc.request.request_id, candidate_plan_digest=cand,
            decision=decision, actor_id=actor, decided_at=self.clock.now(),
        )
        self.approvals[(self.sc.request.request_id, cand)] = approval
        return approval

    def submission(self, *, decision=ApprovalDecision.APPROVED, candidate=None):
        return ApprovalSubmission(
            request_id=self.sc.request.request_id,
            candidate_plan_digest=candidate or self.candidate_digest(),
            decision=decision,
        )

    # -- convenience: run the full happy pipeline ------------------------- #
    def admit(self, *, actor="human-1"):
        prep = self.service.prepare_candidate(self.sc.draft.id, request_id=self.sc.request.request_id)
        cand, res = self.service.persist_and_reserve_candidate(prep, idempotency_key="reserve-1")
        self.approve(actor=actor)
        ev = self.service.record_approval(
            cand.candidate_plan_digest, self.submission(),
            authenticated_actor=actor, idempotency_key="approve-1",
        )
        plan, admitted = self.service.freeze_and_admit_candidate(
            cand.candidate_plan_digest, reservation_id=res.reservation_id,
            approval_event_id=ev.event_id, idempotency_key="freeze-1",
        )
        return prep, cand, res, ev, plan, admitted


def env(**kw) -> _Env:
    return _Env(build_scenario(**kw))


# =========================================================================== #
# 1. happy path — full admission authority chain                              #
# =========================================================================== #
def test_happy_path_admits_and_binds_full_authority_chain():
    e = env()
    prep, cand, res, ev, plan, admitted = e.admit()

    assert isinstance(prep, PreparedAdmissionCandidate)
    assert isinstance(cand, AdmissionCandidate)
    assert isinstance(plan, Plan)
    assert isinstance(admitted, PlanAdmitted)

    candidate = e.candidate_digest()
    # candidate == final plan digest (no second plan digest).
    assert plan.plan_digest == candidate
    assert admitted.plan_digest == candidate == admitted.candidate_plan_digest
    assert admitted.request_id == e.sc.request.request_id
    # both report digests bound.
    p1 = validate_plan_draft(e.sc.draft, request=e.sc.request, context=e.sc.context,
                             catalog=e.sc.snapshot, schema_registry=e.sc.registry)
    assert admitted.phase1_report_digest == p1.semantic_digest()
    assert admitted.support_report_digest == prep.support_report.report_digest
    assert cand.phase1_report_digest == p1.semantic_digest()
    assert cand.support_report_digest == prep.support_report.report_digest
    # profile / catalog / registry.
    assert admitted.runtime_profile_digest == e.sc.profile.profile_digest
    assert admitted.catalog_digest == e.sc.runtime.catalog_digest
    assert admitted.schema_registry_digest == e.sc.registry.registry_digest
    # reservation id + semantic digest.
    assert admitted.reservation_id == res.reservation_id
    assert admitted.reservation_semantic_digest == res.semantic_digest()
    # approval event id + digest.
    assert admitted.approval_event_id == ev.event_id
    approval = e.approvals[(e.sc.request.request_id, candidate)]
    assert admitted.approval_digest == approval.semantic_digest()
    # persisted frozen Plan payload ref -> resolves back to the Plan.
    assert admitted.plan_payload_ref.namespace == "main"
    stored_plan = e.stores.payloads.get(admitted.plan_payload_ref,
                                        expected_schema_ref=SchemaRef(name="Plan", version="1"))
    assert stored_plan.plan_digest == candidate
    # a public PlanFrozen event marks admission.
    frozen = [ev for ev in e.stores.events.journal(e.sc.draft.run_id, "main")
              if ev.event_type is EventType.PLAN_FROZEN]
    assert len(frozen) == 1 and frozen[0].visible_seq is not None


# =========================================================================== #
# 2. no reservation / budget event before BOTH reports persisted             #
# =========================================================================== #
def test_no_budget_event_before_both_reports_persisted():
    e = env()
    prep = e.service.prepare_candidate(e.sc.draft.id, request_id=e.sc.request.request_id)
    # prepare is pure: no payloads, no budget events yet.
    assert e.stores.payloads_object_count() == 0
    assert e.ledger()._state().reservations == {}

    cand, res = e.service.persist_and_reserve_candidate(prep, idempotency_key="k")
    # after persist+reserve: the report anchor is persisted AND exactly one plan
    # reservation exists (the report payload precedes the budget event).
    assert e.stores.payloads_object_count() == 1  # the persisted Phase-1 report anchor
    active = e.ledger().get_active_plan(e.sc.request.request_id, cand.candidate_plan_digest)
    assert active is not None and active.reservation_id == res.reservation_id


def test_unsupported_report_persists_reports_but_no_candidate_or_reservation():
    # phase1-valid but runtime-unsupported (max_attempts>1).
    e = env(node_over={"max_attempts": 3})
    prep = e.service.prepare_candidate(e.sc.draft.id, request_id=e.sc.request.request_id)
    assert prep.support_report.supported is False
    with pytest.raises(AdmissionRejected):
        e.service.persist_and_reserve_candidate(prep, idempotency_key="k")
    # the report anchor was persisted (before the stop) but no reservation / event.
    assert e.stores.payloads_object_count() == 1  # only the Phase-1 report anchor
    assert e.ledger()._state().reservations == {}
    assert e.stores.events.journal(e.sc.draft.run_id, "main") == ()


# =========================================================================== #
# 3. report<->draft binding seam (Task 5 review constraint 1)                 #
# =========================================================================== #
def test_report_for_a_different_draft_is_rejected():
    a = build_scenario()
    b = build_scenario()
    # draft B: materially different goal -> a different candidate digest.
    draft_b = b.draft.model_copy(update={"goal": "a materially different goal for B"})
    p1_b = validate_plan_draft(draft_b, request=b.request, context=b.context,
                               catalog=b.snapshot, schema_registry=b.registry)
    from guanlan_v2.orchestration.runtime_support import check_runtime_support
    support_b = check_runtime_support(draft_b, phase1_report=p1_b, context=b.context,
                                      context_requirements=None, catalog=b.runtime,
                                      bridge_view=b.view, schema_registry=b.registry, profile=b.profile)
    a_candidate = compute_candidate_plan_digest(
        request=a.request, draft=a.draft, context_content_digest=a.context.content_digest)
    assert a_candidate != p1_b.candidate_plan_digest
    # a valid report bound to draft B cannot admit draft A.
    with pytest.raises(AdmissionRejected):
        check_report_binding(a_candidate, p1_b, support_b)
    # the matching pair binds fine.
    p1_a = validate_plan_draft(a.draft, request=a.request, context=a.context,
                               catalog=a.snapshot, schema_registry=a.registry)
    support_a = check_runtime_support(a.draft, phase1_report=p1_a, context=a.context,
                                      context_requirements=None, catalog=a.runtime,
                                      bridge_view=a.view, schema_registry=a.registry, profile=a.profile)
    assert check_report_binding(a_candidate, p1_a, support_a) == a_candidate


# =========================================================================== #
# 4. AUTO rejected for every source; PRESET provenance grants no approval      #
# =========================================================================== #
def test_auto_approval_fails_and_cannot_ride_a_supported_report():
    sc = build_scenario(source=PlanSource.PRESET)
    auto_draft = sc.draft.model_copy(update={"approval_policy": ApprovalPolicy.AUTO})
    sc.draft = auto_draft  # swap in the AUTO draft
    e = _Env(sc)
    prep = e.service.prepare_candidate(auto_draft.id, request_id=sc.request.request_id)
    # AUTO makes Phase 1 invalid -> runtime support can never be True.
    assert prep.phase1_report.valid is False
    assert prep.support_report.supported is False
    with pytest.raises(AdmissionRejected):
        e.service.persist_and_reserve_candidate(prep, idempotency_key="k")
    assert e.ledger()._state().reservations == {}


def test_preset_provenance_alone_grants_no_approval():
    e = env(source=PlanSource.PRESET)
    prep = e.service.prepare_candidate(e.sc.draft.id, request_id=e.sc.request.request_id)
    assert prep.support_report.supported is True  # PRESET is supported...
    cand, res = e.service.persist_and_reserve_candidate(prep, idempotency_key="k")
    # ... but with no approval on file, freeze cannot proceed.
    with pytest.raises(AdmissionRejected):
        e.service.freeze_and_admit_candidate(
            cand.candidate_plan_digest, reservation_id=res.reservation_id,
            approval_event_id="event-run-1-main-999", idempotency_key="f")


# =========================================================================== #
# 5. approval failures (missing / wrong actor / rejected / wrong request)      #
# =========================================================================== #
def test_missing_approval_fails():
    e = env()
    prep = e.service.prepare_candidate(e.sc.draft.id, request_id=e.sc.request.request_id)
    cand, res = e.service.persist_and_reserve_candidate(prep, idempotency_key="k")
    # no approval seated in the store.
    with pytest.raises(AdmissionRejected):
        e.service.record_approval(cand.candidate_plan_digest, e.submission(),
                                  authenticated_actor="human-1", idempotency_key="a")


def test_unauthenticated_or_wrong_actor_approval_fails():
    e = env()
    prep = e.service.prepare_candidate(e.sc.draft.id, request_id=e.sc.request.request_id)
    cand, res = e.service.persist_and_reserve_candidate(prep, idempotency_key="k")
    e.approve(actor="human-1")
    # a different authenticated actor than the one on the loaded approval.
    with pytest.raises(AdmissionRejected):
        e.service.record_approval(cand.candidate_plan_digest, e.submission(),
                                  authenticated_actor="attacker", idempotency_key="a")
    # an empty/None authenticated actor is unauthenticated.
    with pytest.raises(AdmissionRejected):
        e.service.record_approval(cand.candidate_plan_digest, e.submission(),
                                  authenticated_actor="", idempotency_key="a2")


def test_wrong_candidate_or_request_approval_fails():
    e = env()
    prep = e.service.prepare_candidate(e.sc.draft.id, request_id=e.sc.request.request_id)
    cand, res = e.service.persist_and_reserve_candidate(prep, idempotency_key="k")
    e.approve()
    # submission naming the wrong candidate digest.
    with pytest.raises(AdmissionRejected):
        e.service.record_approval(
            cand.candidate_plan_digest,
            ApprovalSubmission(request_id=e.sc.request.request_id,
                               candidate_plan_digest="0" * 64, decision=ApprovalDecision.APPROVED),
            authenticated_actor="human-1", idempotency_key="a")


def test_rejected_approval_releases_reservation_and_blocks_freeze():
    e = env()
    prep = e.service.prepare_candidate(e.sc.draft.id, request_id=e.sc.request.request_id)
    cand, res = e.service.persist_and_reserve_candidate(prep, idempotency_key="k")
    e.approve(decision=ApprovalDecision.REJECTED)
    ev = e.service.record_approval(cand.candidate_plan_digest,
                                   e.submission(decision=ApprovalDecision.REJECTED),
                                   authenticated_actor="human-1", idempotency_key="a")
    assert ev.event_type is EventType.PLAN_REJECTED
    # the plan reservation is released (no active plan reservation remains).
    assert e.ledger().get_active_plan(e.sc.request.request_id, cand.candidate_plan_digest) is None
    released = e.ledger().get(res.reservation_id)
    assert released is not None and released.status == "released"
    # freeze on a rejected candidate fails.
    with pytest.raises(AdmissionRejected):
        e.service.freeze_and_admit_candidate(
            cand.candidate_plan_digest, reservation_id=res.reservation_id,
            approval_event_id=ev.event_id, idempotency_key="f")


# =========================================================================== #
# 6. caller-carried authority is rejected                                      #
# =========================================================================== #
def test_caller_carried_approval_record_is_rejected():
    e = env()
    prep = e.service.prepare_candidate(e.sc.draft.id, request_id=e.sc.request.request_id)
    cand, res = e.service.persist_and_reserve_candidate(prep, idempotency_key="k")
    # a caller passing a constructed PlanApproval instead of a bare ApprovalSubmission.
    forged = PlanApproval(request_id=e.sc.request.request_id,
                          candidate_plan_digest=cand.candidate_plan_digest,
                          decision=ApprovalDecision.APPROVED, actor_id="human-1",
                          decided_at=e.clock.now())
    with pytest.raises((AdmissionRejected, TypeError)):
        e.service.record_approval(cand.candidate_plan_digest, forged,
                                  authenticated_actor="human-1", idempotency_key="a")


def test_forged_reservation_id_is_rejected_at_freeze():
    e = env()
    prep = e.service.prepare_candidate(e.sc.draft.id, request_id=e.sc.request.request_id)
    cand, res = e.service.persist_and_reserve_candidate(prep, idempotency_key="k")
    ev = e.service.record_approval(cand.candidate_plan_digest, e.submission(),
                                   authenticated_actor="human-1", idempotency_key="a") \
        if e.approve() or True else None
    with pytest.raises(AdmissionRejected):
        e.service.freeze_and_admit_candidate(
            cand.candidate_plan_digest, reservation_id="res-does-not-exist",
            approval_event_id=ev.event_id, idempotency_key="f")


# =========================================================================== #
# 7. drift after preparation -> AdmissionInvalidated + release                 #
# =========================================================================== #
def test_draft_drift_after_reservation_invalidates_and_releases():
    e = env()
    prep = e.service.prepare_candidate(e.sc.draft.id, request_id=e.sc.request.request_id)
    cand, res = e.service.persist_and_reserve_candidate(prep, idempotency_key="k")
    ev = e.service.record_approval(cand.candidate_plan_digest, e.submission(),
                                   authenticated_actor="human-1", idempotency_key="a") \
        if e.approve() or True else None
    # an authoritative input (the draft) drifts after preparation.
    e.service._drafts[e.sc.draft.id] = e.sc.draft.model_copy(
        update={"goal": "a drifted goal after reservation"})
    with pytest.raises(AdmissionRejected):
        e.service.freeze_and_admit_candidate(
            cand.candidate_plan_digest, reservation_id=res.reservation_id,
            approval_event_id=ev.event_id, idempotency_key="f")
    # the reservation is released and no admission occurred.
    assert e.ledger().get_active_plan(e.sc.request.request_id, cand.candidate_plan_digest) is None
    assert e.ledger().get(res.reservation_id).status == "released"
    invalidated = [ev for ev in e.stores.events.journal(e.sc.draft.run_id, "main")
                   if ev.event_type is EventType.PLAN_FROZEN]
    assert invalidated == []  # no PlanFrozen / admission
    with pytest.raises(Exception):
        e.service.load_admitted(cand.candidate_plan_digest)


# =========================================================================== #
# 8. dispatch verification                                                     #
# =========================================================================== #
def test_verify_for_dispatch_returns_bundle_on_healthy_admission():
    e = env()
    _, cand, res, ev, plan, admitted = e.admit()
    bundle = e.service.verify_for_dispatch(plan.plan_digest)
    assert bundle.plan.plan_digest == plan.plan_digest
    assert bundle.plan_admitted == admitted
    assert bundle.reservation.reservation_id == res.reservation_id
    assert bundle.reservation.status == "reserved"


def test_verify_for_dispatch_fails_after_catalog_drift():
    e = env()
    _, cand, res, ev, plan, admitted = e.admit()
    other = build_scenario(bridges=[_BridgeSpec(bridge_id="bridge.other", priority=5,
                                                activation_caps=("cap.data",))])
    assert other.runtime.catalog_digest != e.sc.runtime.catalog_digest
    e.service._catalog = other.runtime
    e.service._bridge_view = other.view
    with pytest.raises(AdmissionRejected):
        e.service.verify_for_dispatch(plan.plan_digest)


def test_verify_for_dispatch_fails_after_reservation_release():
    e = env()
    _, cand, res, ev, plan, admitted = e.admit()
    # release the active plan reservation out of band.
    e.ledger().release(res.reservation_id, reason="drained", idempotency_key="rel-x")
    with pytest.raises(AdmissionRejected):
        e.service.verify_for_dispatch(plan.plan_digest)


# =========================================================================== #
# 9. idempotency at both boundaries                                            #
# =========================================================================== #
def test_identical_retries_reuse_the_same_batch():
    e = env()
    prep = e.service.prepare_candidate(e.sc.draft.id, request_id=e.sc.request.request_id)
    cand1, res1 = e.service.persist_and_reserve_candidate(prep, idempotency_key="reserve-1")
    cand2, res2 = e.service.persist_and_reserve_candidate(prep, idempotency_key="reserve-1")
    assert cand1 == cand2 and res1.reservation_id == res2.reservation_id
    e.approve()
    ev1 = e.service.record_approval(cand1.candidate_plan_digest, e.submission(),
                                    authenticated_actor="human-1", idempotency_key="approve-1")
    ev2 = e.service.record_approval(cand1.candidate_plan_digest, e.submission(),
                                    authenticated_actor="human-1", idempotency_key="approve-1")
    assert ev1.event_id == ev2.event_id
    plan1, adm1 = e.service.freeze_and_admit_candidate(
        cand1.candidate_plan_digest, reservation_id=res1.reservation_id,
        approval_event_id=ev1.event_id, idempotency_key="freeze-1")
    plan2, adm2 = e.service.freeze_and_admit_candidate(
        cand1.candidate_plan_digest, reservation_id=res1.reservation_id,
        approval_event_id=ev1.event_id, idempotency_key="freeze-1")
    assert plan1.plan_digest == plan2.plan_digest and adm1 == adm2


def test_conflicting_retry_same_key_raises():
    e = env()
    prep = e.service.prepare_candidate(e.sc.draft.id, request_id=e.sc.request.request_id)
    e.service.persist_and_reserve_candidate(prep, idempotency_key="reserve-1")
    # a materially different candidate (different goal -> different digest) under the
    # SAME reservation idempotency key conflicts on the shared batch key.
    draft2 = e.sc.draft.model_copy(update={"id": "plan.y", "goal": "a materially different goal"})
    e.service._drafts["plan.y"] = draft2
    prep2 = e.service.prepare_candidate("plan.y", request_id=e.sc.request.request_id)
    assert prep2.candidate_plan_digest != prep.candidate_plan_digest
    with pytest.raises((IdempotencyConflict, AdmissionRejected)):
        e.service.persist_and_reserve_candidate(prep2, idempotency_key="reserve-1")


# =========================================================================== #
# 10. replay: a fresh service over the SAME stores loads the admitted plan     #
# =========================================================================== #
def test_fresh_service_replays_admitted_plan_from_persisted_state():
    e = env()
    _, cand, res, ev, plan, admitted = e.admit()
    # a brand-new service instance over the SAME shared stores (a "restart").
    fresh = e._make_service()
    plan2, admitted2 = fresh.load_admitted(plan.plan_digest)
    assert plan2.plan_digest == plan.plan_digest
    assert admitted2 == admitted
    bundle = fresh.verify_for_dispatch(plan.plan_digest)
    assert bundle.reservation.reservation_id == res.reservation_id


# =========================================================================== #
# 11. ContextRuntimeRequirements closure (present)                            #
# =========================================================================== #
def test_present_context_requirements_closure_admits_and_binds():
    base = build_scenario()
    # build a requirements fact bound to the eventual non-empty context subject.
    tmp_ctx = _context(requirements=__import__(
        "guanlan_v2.orchestration.context", fromlist=["ContextRuntimeRequirements"]
    ).ContextRuntimeRequirements.build(
        context_subject_digest="0" * 64, required_schema_registry_digest="0" * 64,
        required_catalog_digest="0" * 64))
    reqs = _requirements_for(tmp_ctx, registry_digest=base.registry.registry_digest,
                             catalog_digest=base.runtime.catalog_digest,
                             bridge_ids=("bridge.data",))
    ctx2 = _context(requirements=reqs)
    e = _Env(base)
    # persist the CRR fact so admission can resolve it through the PayloadStore,
    # then rebuild the context so its ref object id matches the stored locator.
    from guanlan_v2.orchestration.refs import SchemaRef as _SR
    crr_ref = e.stores.payloads.put(
        _SR(name="ContextRuntimeRequirements", version="1"), reqs,
        registry_digest=e.rt_digest, namespace="main",
        idempotency_key="crr-1")
    ctx3 = _context(requirements=reqs, req_object_id=crr_ref.object_id)
    ref = PayloadRef(namespace="main", object_id="ctx-obj", content_digest=ctx3.content_digest)
    draft = base.draft.model_copy(update={"context_snapshot_ref": ref})
    e.service._drafts[draft.id] = draft
    e.service._contexts[ctx3.content_digest] = ctx3

    prep = e.service.prepare_candidate(draft.id, request_id=base.request.request_id)
    assert prep.support_report.supported is True, prep.support_report.issues
    cand, res = e.service.persist_and_reserve_candidate(prep, idempotency_key="k")
    assert cand.context_requirements_digest == reqs.requirements_digest
    assert cand.context_requirements_ref == ctx3.runtime_requirements_ref
    e.approve(candidate=cand.candidate_plan_digest)
    ev = e.service.record_approval(cand.candidate_plan_digest,
                                   ApprovalSubmission(request_id=base.request.request_id,
                                                      candidate_plan_digest=cand.candidate_plan_digest,
                                                      decision=ApprovalDecision.APPROVED),
                                   authenticated_actor="human-1", idempotency_key="a")
    plan, admitted = e.service.freeze_and_admit_candidate(
        cand.candidate_plan_digest, reservation_id=res.reservation_id,
        approval_event_id=ev.event_id, idempotency_key="f")
    assert admitted.context_requirements_ref == ctx3.runtime_requirements_ref
    assert admitted.context_requirements_digest == reqs.requirements_digest
