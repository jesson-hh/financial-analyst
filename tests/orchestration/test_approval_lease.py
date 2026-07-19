# -*- coding: utf-8 -*-
"""Phase 7 - Task 7b: ApprovalLease bounded standing approvals for attested presets.

Covers the lease surface that extends Task 7's ``guanlan_v2.orchestration.approval``
module + journal *additively*:

* :class:`ApprovalLease` — a service-sealed, digest-bound standing approval whose
  ``lease_id`` is the content digest over its own issue content (never
  caller-chosen), carrying the preset/catalog/registry digests it binds, a valid
  window and admission + budget envelopes.
* ``PlanApprovalCoordinator.issue_lease`` — verifier-gated (fail closed), the
  preset must resolve in the sealed registry and the lease digests must equal the
  current chain digests; idempotent by issue-content digest.
* ``list_leases`` — active + terminal (expired / exhausted / revoked) with folded
  balances and a terminal reason.
* ``revoke_lease`` — verifier-gated, immediate, durable, idempotent.
* ``register_and_try_lease`` — registers the pending card (Task 7 semantics), then
  admits *only* a preset-provenanced candidate whose ``(preset_id,
  preset_record_digest, catalog_digest, registry_digest)`` match an active lease
  within its window and envelopes; ``lease_consumed`` is journaled FIRST, then a
  real ``PlanApproval`` (actor ``lease:<id>``) through the Task 7 decide internals;
  any failing condition falls back to ``pending_human`` (never an error, never a
  silent admit). DYNAMIC cards structurally never match.

The doubles mirror Task 7's fakes (a call-recording admission with durable
idempotency, a fail-closed verifier). A temp-path journal is used throughout.

Run: ``python -m pytest tests/orchestration/test_approval_lease.py -v``
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from guanlan_v2.orchestration.enums import ApprovalDecision, ApprovalPolicy, PlanSource
from guanlan_v2.orchestration.memory.models import AuthenticatedAdminPrincipal
from guanlan_v2.orchestration.plan_diff import (
    PLAN_DIFF_SCHEMA_REF,
    PendingPlanApproval,
    PlanDiff,
    PlanDiffEntry,
)
from guanlan_v2.orchestration.plan_presets import PlanPresetError
from guanlan_v2.orchestration.refs import PayloadRef, TypedPayloadRef

from guanlan_v2.orchestration.approval import (
    ApprovalAuthorityError,
    ApprovalJournalRow,
    ApprovalLease,
    ApprovalLeaseError,
    LeaseAdmissionOutcome,
    PlanApprovalCoordinator,
)

UTC = timezone.utc
NOW = datetime(2026, 7, 20, 2, 0, tzinfo=UTC)
VALID_FROM = datetime(2026, 7, 20, 0, 0, tzinfo=UTC)
VALID_UNTIL = datetime(2026, 7, 21, 0, 0, tzinfo=UTC)
AFTER_UNTIL = datetime(2026, 7, 22, 0, 0, tzinfo=UTC)

CAND_A = "a" * 64
CAND_D = "d" * 64
CAND_E = "e" * 64
PRESET_ID = "daily-brief"
PRESET_REC_DIG = "b" * 64
CAT_DIG = "1" * 64
REG_DIG = "2" * 64
REQ_DIGEST = "c" * 64
GOOD = "good-cred"


# --------------------------------------------------------------------------- #
# Doubles (mirror the Task 7 store test fakes)                                 #
# --------------------------------------------------------------------------- #
class _FixedClock:
    def now(self) -> datetime:
        return NOW


class _VerifyDenied(Exception):
    """The fake verifier's refusal for an unverifiable credential."""


class _FakeVerifier:
    def __init__(self, actor: str = "admin-1", *, good: str = GOOD) -> None:
        self._actor = actor
        self._good = good
        self.calls: list[object] = []

    def verify(self, credential) -> AuthenticatedAdminPrincipal:
        self.calls.append(credential)
        if credential != self._good:
            raise _VerifyDenied(f"unverifiable credential {credential!r}")
        return AuthenticatedAdminPrincipal(actor=self._actor, verified_by="test-verifier")


class _FakeRunEvent:
    def __init__(self, *, event_id, candidate_id, decision, actor, idempotency_key):
        self.event_id = event_id
        self.candidate_id = candidate_id
        self.decision = decision
        self.actor = actor
        self.idempotency_key = idempotency_key


class _AdmissionBoom(Exception):
    """Injected admission failure."""


class _FakeAdmission:
    """Records ``record_approval`` effects with durable idempotency by key."""

    def __init__(self, *, fail_once: bool = False) -> None:
        self.calls: list[tuple] = []
        self._events: dict[str, _FakeRunEvent] = {}
        self._failed: set[str] = set()
        self.fail_once = fail_once

    def record_approval(self, candidate_id, approval_input, *, authenticated_actor,
                        idempotency_key):
        if idempotency_key in self._events:
            return self._events[idempotency_key]
        if self.fail_once and idempotency_key not in self._failed:
            self._failed.add(idempotency_key)
            raise _AdmissionBoom("injected admission failure")
        self.calls.append(
            (candidate_id, approval_input, authenticated_actor, idempotency_key))
        ev = _FakeRunEvent(
            event_id=f"ev-{len(self._events) + 1}", candidate_id=candidate_id,
            decision=approval_input.decision, actor=authenticated_actor,
            idempotency_key=idempotency_key)
        self._events[idempotency_key] = ev
        return ev


class _FakePresetRecord:
    def __init__(self, digest: str) -> None:
        self._digest = digest

    def semantic_digest(self) -> str:
        return self._digest


class _FakePresetRegistry:
    """Mirrors the sealed ``PlanPresetRegistry.get`` selection contract."""

    def __init__(self, presets: dict[str, str]) -> None:
        self._presets = dict(presets)

    def get(self, preset_id: str) -> _FakePresetRecord:
        if preset_id not in self._presets:
            raise PlanPresetError(f"no plan preset registered for {preset_id!r}")
        return _FakePresetRecord(self._presets[preset_id])


# --------------------------------------------------------------------------- #
# builders                                                                     #
# --------------------------------------------------------------------------- #
def _pending(request_id="r-1", candidate=CAND_A, *, source=PlanSource.PRESET_FALLBACK,
             goal="g", preset_id=PRESET_ID, preset_record_digest=PRESET_REC_DIG,
             budget_llm=0, worker_ids=("text.sentiment",)) -> PendingPlanApproval:
    diff = PlanDiff(
        baseline_kind="none", request_digest=REQ_DIGEST,
        candidate_plan_digest=candidate,
        entries=(PlanDiffEntry(pointer="goal", change="added",
                               baseline_json=None, candidate_json='"v"'),))
    dd = diff.semantic_digest()
    ref = TypedPayloadRef(
        schema_ref=PLAN_DIFF_SCHEMA_REF,
        payload_ref=PayloadRef(namespace="main", object_id="o-" + candidate[:6],
                               content_digest=dd))
    return PendingPlanApproval(
        request_id=request_id, candidate_plan_digest=candidate, goal=goal,
        source=source, approval_policy=ApprovalPolicy.REQUIRED, node_count=1,
        worker_ids=worker_ids, budget_request_llm_invocations=budget_llm,
        plan_diff_ref=ref, rendered_md="body", rendered_from_diff_digest=dd,
        candidate_id="cand-" + candidate[:6], requested_at=NOW,
        preset_id=preset_id, preset_record_digest=preset_record_digest)


def _dynamic_pending(request_id="r-dyn", candidate=CAND_D) -> PendingPlanApproval:
    return _pending(request_id=request_id, candidate=candidate,
                    source=PlanSource.DYNAMIC, preset_id=None,
                    preset_record_digest=None)


def _read_lines(path) -> list[str]:
    if not path.exists():
        return []
    raw = path.read_bytes().decode("utf-8")
    parts = raw.split("\n")
    if parts and parts[-1] == "":
        parts = parts[:-1]
    return parts


def _rows(path) -> list[ApprovalJournalRow]:
    return [ApprovalJournalRow.model_validate_json(line) for line in _read_lines(path)]


def _kinds(path) -> list[str]:
    return [r.row_kind for r in _rows(path)]


def _coord(tmp_path, *, admission=None, verifier=None, console_emit=None,
           preset_registry=None, catalog_digest=CAT_DIG, registry_digest=REG_DIG,
           name="plan_approvals.jsonl", wire_presets=True):
    if preset_registry is None and wire_presets:
        preset_registry = _FakePresetRegistry({PRESET_ID: PRESET_REC_DIG})
    return PlanApprovalCoordinator(
        tmp_path / name,
        admission=admission if admission is not None else _FakeAdmission(),
        clock=_FixedClock(),
        verifier=verifier if verifier is not None else _FakeVerifier(),
        console_emit=console_emit,
        preset_registry=preset_registry,
        catalog_digest=catalog_digest,
        registry_digest=registry_digest)


def _issue(coord, *, purpose="daily lane-0", preset_id=PRESET_ID,
           preset_record_digest=PRESET_REC_DIG, catalog_digest=CAT_DIG,
           registry_digest=REG_DIG, valid_from=VALID_FROM, valid_until=VALID_UNTIL,
           max_admissions=5, budget_cap=100, actor=GOOD, reason="standing daily run"):
    return coord.issue_lease(
        purpose=purpose, preset_id=preset_id,
        preset_record_digest=preset_record_digest, catalog_digest=catalog_digest,
        registry_digest=registry_digest, valid_from=valid_from,
        valid_until=valid_until, max_admissions=max_admissions,
        budget_cap_llm_invocations=budget_cap, actor=actor, reason=reason)


def _try(coord, pending, *, idempotency_key="i1", now=NOW,
         candidate_catalog_digest=CAT_DIG, candidate_registry_digest=REG_DIG):
    return coord.register_and_try_lease(
        pending, idempotency_key=idempotency_key, now=now,
        candidate_catalog_digest=candidate_catalog_digest,
        candidate_registry_digest=candidate_registry_digest)


# =========================================================================== #
# ApprovalLease — the sealed, digest-bound model                               #
# =========================================================================== #
def test_lease_id_binds_issue_content_and_excludes_wallclock():
    lease = ApprovalLease.issue(
        purpose="p", preset_id=PRESET_ID, preset_record_digest=PRESET_REC_DIG,
        catalog_digest=CAT_DIG, registry_digest=REG_DIG, valid_from=VALID_FROM,
        valid_until=VALID_UNTIL, max_admissions=3, budget_cap_llm_invocations=10,
        issued_by="admin-1", issued_at=NOW, reason="r")
    # lease_id is the semantic digest over the issue content (never chosen).
    assert lease.lease_id == lease.semantic_digest()
    # issued_at is audit-only: a different stamp yields the SAME lease_id.
    later = ApprovalLease.issue(
        purpose="p", preset_id=PRESET_ID, preset_record_digest=PRESET_REC_DIG,
        catalog_digest=CAT_DIG, registry_digest=REG_DIG, valid_from=VALID_FROM,
        valid_until=VALID_UNTIL, max_admissions=3, budget_cap_llm_invocations=10,
        issued_by="admin-1", issued_at=AFTER_UNTIL, reason="r")
    assert later.lease_id == lease.lease_id
    # a different envelope yields a different lease_id.
    other = ApprovalLease.issue(
        purpose="p", preset_id=PRESET_ID, preset_record_digest=PRESET_REC_DIG,
        catalog_digest=CAT_DIG, registry_digest=REG_DIG, valid_from=VALID_FROM,
        valid_until=VALID_UNTIL, max_admissions=4, budget_cap_llm_invocations=10,
        issued_by="admin-1", issued_at=NOW, reason="r")
    assert other.lease_id != lease.lease_id


def test_lease_rejects_a_tampered_lease_id():
    lease = ApprovalLease.issue(
        purpose="p", preset_id=PRESET_ID, preset_record_digest=PRESET_REC_DIG,
        catalog_digest=CAT_DIG, registry_digest=REG_DIG, valid_from=VALID_FROM,
        valid_until=VALID_UNTIL, max_admissions=3, budget_cap_llm_invocations=10,
        issued_by="admin-1", issued_at=NOW, reason="r")
    data = lease.model_dump()
    data["lease_id"] = "f" * 64  # a well-formed but wrong digest
    with pytest.raises(Exception):
        ApprovalLease.model_validate(data)


def test_lease_rejects_inverted_window():
    with pytest.raises(Exception):
        ApprovalLease.issue(
            purpose="p", preset_id=PRESET_ID, preset_record_digest=PRESET_REC_DIG,
            catalog_digest=CAT_DIG, registry_digest=REG_DIG, valid_from=VALID_UNTIL,
            valid_until=VALID_FROM, max_admissions=3, budget_cap_llm_invocations=10,
            issued_by="admin-1", issued_at=NOW, reason="r")


# =========================================================================== #
# issue_lease                                                                  #
# =========================================================================== #
def test_issue_lease_happy(tmp_path):
    seen = []
    coord = _coord(tmp_path, console_emit=lambda n, p: seen.append((n, p)))
    lease = _issue(coord)
    assert lease.issued_by == "admin-1"  # the VERIFIED actor, not GOOD
    assert lease.preset_id == PRESET_ID and lease.catalog_digest == CAT_DIG
    assert lease.lease_id == lease.semantic_digest()
    # journal has exactly the lease_issued row; console emitted.
    assert _kinds(tmp_path / "plan_approvals.jsonl") == ["lease_issued"]
    assert seen and seen[-1][0] == "plan_lease_issued"
    assert seen[-1][1]["lease_id"] == lease.lease_id


def test_issue_lease_idempotent_by_content(tmp_path):
    coord = _coord(tmp_path)
    a = _issue(coord)
    b = _issue(coord)  # identical issue content
    assert a.lease_id == b.lease_id
    assert _kinds(tmp_path / "plan_approvals.jsonl") == ["lease_issued"]  # one row


def test_issue_lease_unverified_refusal_persists_nothing(tmp_path):
    coord = _coord(tmp_path)
    with pytest.raises(_VerifyDenied):
        _issue(coord, actor="bad-cred")
    assert _read_lines(tmp_path / "plan_approvals.jsonl") == []


def test_issue_lease_verifier_none_has_no_path(tmp_path):
    coord = PlanApprovalCoordinator(
        tmp_path / "plan_approvals.jsonl", admission=_FakeAdmission(),
        clock=_FixedClock(), verifier=None,
        preset_registry=_FakePresetRegistry({PRESET_ID: PRESET_REC_DIG}),
        catalog_digest=CAT_DIG, registry_digest=REG_DIG)
    with pytest.raises(ApprovalAuthorityError):
        _issue(coord)
    assert _read_lines(tmp_path / "plan_approvals.jsonl") == []


def test_issue_lease_unknown_preset_refusal(tmp_path):
    coord = _coord(tmp_path)
    with pytest.raises(ApprovalLeaseError):
        _issue(coord, preset_id="never-registered")
    assert _read_lines(tmp_path / "plan_approvals.jsonl") == []


@pytest.mark.parametrize("field,bad", [
    ("preset_record_digest", "9" * 64),
    ("catalog_digest", "8" * 64),
    ("registry_digest", "7" * 64),
])
def test_issue_lease_chain_digest_drift_refusal(tmp_path, field, bad):
    coord = _coord(tmp_path)
    with pytest.raises(ApprovalLeaseError):
        _issue(coord, **{field: bad})
    assert _read_lines(tmp_path / "plan_approvals.jsonl") == []


def test_issue_lease_unwired_preset_registry_refuses(tmp_path):
    coord = _coord(tmp_path, wire_presets=False)  # no preset registry injected
    with pytest.raises(ApprovalLeaseError):
        _issue(coord)
    assert _read_lines(tmp_path / "plan_approvals.jsonl") == []


# =========================================================================== #
# register_and_try_lease — admit happy                                        #
# =========================================================================== #
def test_register_and_try_lease_admits_happy(tmp_path):
    admission = _FakeAdmission()
    seen = []
    coord = _coord(tmp_path, admission=admission,
                   console_emit=lambda n, p: seen.append((n, p)))
    lease = _issue(coord, budget_cap=100)
    out = _try(coord, _pending(budget_llm=7))

    assert isinstance(out, LeaseAdmissionOutcome)
    assert out.outcome == "lease_admitted" and out.lease_id == lease.lease_id
    # a REAL PlanApproval was recorded with the lease actor id.
    assert out.approval.decision is ApprovalDecision.APPROVED
    assert out.approval.actor_id == f"lease:{lease.lease_id}"
    assert coord.load_decision("r-1", CAND_A) == out.approval
    # the admission recorded exactly one effect, with the lease actor.
    assert len(admission.calls) == 1
    cand_id, submission, actor, _idem = admission.calls[0]
    assert cand_id == CAND_A and actor == f"lease:{lease.lease_id}"
    assert submission.decision is ApprovalDecision.APPROVED
    # journal: lease_issued, pending, lease_consumed BEFORE decision.
    assert _kinds(tmp_path / "plan_approvals.jsonl") == [
        "lease_issued", "pending", "lease_consumed", "decision"]
    # console emitted the lease admission last.
    assert seen[-1][0] == "plan_lease_admitted"
    assert seen[-1][1]["lease_id"] == lease.lease_id


def test_admit_decrements_admissions_and_budget(tmp_path):
    coord = _coord(tmp_path)
    lease = _issue(coord, max_admissions=5, budget_cap=100)
    _try(coord, _pending(budget_llm=30))
    view = _lease_view(coord, lease.lease_id, now=NOW)
    assert view.status == "active"
    assert view.admissions_used == 1 and view.admissions_remaining == 4
    assert view.budget_used == 30 and view.budget_remaining == 70


# =========================================================================== #
# envelope crossings each fall back to pending_human                          #
# =========================================================================== #
def test_expiry_falls_back_to_pending_human(tmp_path):
    coord = _coord(tmp_path)
    _issue(coord)
    out = _try(coord, _pending(), now=AFTER_UNTIL)  # after valid_until
    assert out.outcome == "pending_human" and out.approval is None
    # no consume / decision rows; the card stays pending.
    assert _kinds(tmp_path / "plan_approvals.jsonl") == ["lease_issued", "pending"]
    assert coord.load_decision("r-1", CAND_A) is None
    assert coord.list_pending() and coord.list_pending()[0].candidate_plan_digest == CAND_A


def test_before_window_falls_back_to_pending_human(tmp_path):
    coord = _coord(tmp_path)
    _issue(coord)
    before = VALID_FROM - timedelta(hours=1)
    out = _try(coord, _pending(), now=before)
    assert out.outcome == "pending_human"
    assert _kinds(tmp_path / "plan_approvals.jsonl") == ["lease_issued", "pending"]


def test_admission_count_exhaustion_falls_back(tmp_path):
    coord = _coord(tmp_path)
    _issue(coord, max_admissions=1)
    a = _try(coord, _pending(request_id="r-1", candidate=CAND_A), idempotency_key="i1")
    assert a.outcome == "lease_admitted"
    b = _try(coord, _pending(request_id="r-2", candidate=CAND_E), idempotency_key="i2")
    assert b.outcome == "pending_human"
    # the second candidate was registered but never consumed / decided.
    assert coord.load_decision("r-2", CAND_E) is None
    assert "lease_consumed" not in _kinds(tmp_path / "plan_approvals.jsonl")[3:]


def test_budget_exhaustion_falls_back(tmp_path):
    coord = _coord(tmp_path)
    _issue(coord, budget_cap=10)
    # a request for 11 exceeds the whole cap.
    out = _try(coord, _pending(budget_llm=11))
    assert out.outcome == "pending_human"
    assert coord.load_decision("r-1", CAND_A) is None


def test_partial_budget_then_overflow_falls_back(tmp_path):
    coord = _coord(tmp_path)
    lease = _issue(coord, max_admissions=5, budget_cap=10)
    a = _try(coord, _pending(request_id="r-1", candidate=CAND_A, budget_llm=8),
             idempotency_key="i1")
    assert a.outcome == "lease_admitted"
    # 8 of 10 spent; a request for 3 overflows the remaining 2.
    b = _try(coord, _pending(request_id="r-2", candidate=CAND_E, budget_llm=3),
             idempotency_key="i2")
    assert b.outcome == "pending_human"
    view = _lease_view(coord, lease.lease_id, now=NOW)
    assert view.budget_used == 8 and view.budget_remaining == 2


# =========================================================================== #
# structural refusals: DYNAMIC + candidate drift                              #
# =========================================================================== #
def test_dynamic_card_never_matches(tmp_path):
    coord = _coord(tmp_path)
    _issue(coord)
    out = _try(coord, _dynamic_pending())  # no preset provenance
    assert out.outcome == "pending_human"
    assert coord.load_decision("r-dyn", CAND_D) is None


def test_candidate_catalog_drift_refused(tmp_path):
    coord = _coord(tmp_path)
    _issue(coord, catalog_digest=CAT_DIG)
    # the candidate was built against a DRIFTED catalog digest.
    out = _try(coord, _pending(), candidate_catalog_digest="0" * 64)
    assert out.outcome == "pending_human"
    assert coord.load_decision("r-1", CAND_A) is None


def test_candidate_preset_record_drift_refused(tmp_path):
    coord = _coord(tmp_path)
    _issue(coord, preset_record_digest=PRESET_REC_DIG)
    # the candidate card carries a different preset_record_digest.
    out = _try(coord, _pending(preset_record_digest="0" * 64))
    assert out.outcome == "pending_human"


def test_candidate_registry_drift_refused(tmp_path):
    coord = _coord(tmp_path)
    _issue(coord, registry_digest=REG_DIG)
    out = _try(coord, _pending(), candidate_registry_digest="0" * 64)
    assert out.outcome == "pending_human"


# =========================================================================== #
# revoke_lease                                                                #
# =========================================================================== #
def test_revoke_then_try_falls_back(tmp_path):
    coord = _coord(tmp_path)
    lease = _issue(coord)
    coord.revoke_lease(lease.lease_id, actor=GOOD, reason="stop it",
                       idempotency_key="rev-1")
    out = _try(coord, _pending())
    assert out.outcome == "pending_human"
    assert coord.load_decision("r-1", CAND_A) is None


def test_revoke_is_immediate_durable_and_idempotent(tmp_path):
    coord = _coord(tmp_path)
    lease = _issue(coord)
    rev1 = coord.revoke_lease(lease.lease_id, actor=GOOD, reason="stop",
                              idempotency_key="rev-1")
    rev2 = coord.revoke_lease(lease.lease_id, actor=GOOD, reason="stop again",
                              idempotency_key="rev-2")
    # idempotent replay returns the STORED revocation (first reason wins).
    assert rev2.lease_id == rev1.lease_id and rev2.reason == rev1.reason
    # exactly one lease_revoked row.
    assert _kinds(tmp_path / "plan_approvals.jsonl").count("lease_revoked") == 1
    view = _lease_view(coord, lease.lease_id, now=NOW)
    assert view.status == "revoked" and "stop" in (view.terminal_reason or "")


def test_revoke_lease_verifier_gated(tmp_path):
    coord = _coord(tmp_path)
    lease = _issue(coord)
    with pytest.raises(_VerifyDenied):
        coord.revoke_lease(lease.lease_id, actor="bad-cred", reason="x",
                           idempotency_key="rev-1")
    assert "lease_revoked" not in _kinds(tmp_path / "plan_approvals.jsonl")


def test_revoke_unknown_lease_errors(tmp_path):
    coord = _coord(tmp_path)
    with pytest.raises(ApprovalLeaseError):
        coord.revoke_lease("f" * 64, actor=GOOD, reason="x", idempotency_key="rev-1")


# =========================================================================== #
# list_leases — active + terminal with folded balances                        #
# =========================================================================== #
def test_list_leases_reports_all_states(tmp_path):
    # active + expired come from one lease viewed at two instants (viewing after the
    # window turns it terminal without any state change).
    coord = _coord(tmp_path)
    active = _issue(coord, purpose="active", max_admissions=5, budget_cap=100)
    assert _lease_view(coord, active.lease_id, now=NOW).status == "active"
    v_expired = _lease_view(coord, active.lease_id, now=AFTER_UNTIL)
    assert v_expired.status == "expired" and v_expired.terminal_reason

    # exhausted: a one-admission lease consumed once (own coordinator so it is the
    # only matching lease).
    coord2 = _coord(tmp_path, name="j2.jsonl")
    exhausted = _issue(coord2, purpose="exhaust", max_admissions=1, budget_cap=100)
    _try(coord2, _pending(request_id="r-x", candidate=CAND_E), idempotency_key="ix")
    v_exhausted = _lease_view(coord2, exhausted.lease_id, now=NOW)
    assert v_exhausted.status == "exhausted"
    assert "max_admissions" in (v_exhausted.terminal_reason or "")

    # revoked
    coord3 = _coord(tmp_path, name="j3.jsonl")
    revoked = _issue(coord3, purpose="to-revoke", reason="revoke-me")
    coord3.revoke_lease(revoked.lease_id, actor=GOOD, reason="done",
                        idempotency_key="rv")
    v_revoked = _lease_view(coord3, revoked.lease_id, now=NOW)
    assert v_revoked.status == "revoked" and "done" in (v_revoked.terminal_reason or "")


# =========================================================================== #
# idempotency: double delivery admits once                                    #
# =========================================================================== #
def test_double_delivery_of_one_key_admits_once(tmp_path):
    admission = _FakeAdmission()
    coord = _coord(tmp_path, admission=admission)
    lease = _issue(coord, max_admissions=5, budget_cap=100)
    a = _try(coord, _pending(budget_llm=5), idempotency_key="i1")
    b = _try(coord, _pending(budget_llm=5), idempotency_key="i1")  # replay
    assert a.outcome == "lease_admitted" and b.outcome == "lease_admitted"
    assert b.lease_id == a.lease_id and b.approval == a.approval
    # exactly one consume + one decision + one admission effect.
    assert _kinds(tmp_path / "plan_approvals.jsonl").count("lease_consumed") == 1
    assert _kinds(tmp_path / "plan_approvals.jsonl").count("decision") == 1
    assert len(admission.calls) == 1
    assert _lease_view(coord, lease.lease_id, now=NOW).admissions_used == 1


# =========================================================================== #
# crash-replay reconstructs balances exactly, never double-consumes           #
# =========================================================================== #
def test_crash_between_consume_and_decision_reconstructs(tmp_path, monkeypatch):
    admission = _FakeAdmission()
    coord = _coord(tmp_path, admission=admission)
    lease = _issue(coord, max_admissions=5, budget_cap=100)

    # kill AFTER lease_consumed is durable but BEFORE the decision row is written.
    def _boom(*a, **k):
        raise RuntimeError("killed mid-admission")
    monkeypatch.setattr(coord, "_record_terminal_decision", _boom)
    with pytest.raises(RuntimeError):
        _try(coord, _pending(budget_llm=9), idempotency_key="i1")
    kinds = _kinds(tmp_path / "plan_approvals.jsonl")
    assert "lease_consumed" in kinds and "decision" not in kinds

    # restart: replay folds the consume (balance reconstructed) with no decision.
    fresh = _FakeAdmission()
    replayed = PlanApprovalCoordinator.replay(
        tmp_path / "plan_approvals.jsonl", admission=fresh, clock=_FixedClock(),
        verifier=_FakeVerifier(),
        preset_registry=_FakePresetRegistry({PRESET_ID: PRESET_REC_DIG}),
        catalog_digest=CAT_DIG, registry_digest=REG_DIG)
    v = _lease_view(replayed, lease.lease_id, now=NOW)
    assert v.admissions_used == 1 and v.budget_used == 9
    assert replayed.load_decision("r-1", CAND_A) is None
    assert fresh.calls == []  # no admission effect yet

    # a retry heals the half-done admission without double-consuming.
    out = _try(replayed, _pending(budget_llm=9), idempotency_key="i1")
    assert out.outcome == "lease_admitted"
    assert out.approval.actor_id == f"lease:{lease.lease_id}"
    assert len(fresh.calls) == 1
    v2 = _lease_view(replayed, lease.lease_id, now=NOW)
    assert v2.admissions_used == 1 and v2.budget_used == 9  # still one consume


def test_crash_after_both_rows_replay_completes_admission(tmp_path):
    # admission raises the first time (crash AFTER both consume + decision rows are
    # durable, but before record_approval completes).
    failing = _FakeAdmission(fail_once=True)
    coord = _coord(tmp_path, admission=failing)
    lease = _issue(coord, max_admissions=5, budget_cap=100)
    with pytest.raises(_AdmissionBoom):
        _try(coord, _pending(budget_llm=4), idempotency_key="i1")
    assert _kinds(tmp_path / "plan_approvals.jsonl") == [
        "lease_issued", "pending", "lease_consumed", "decision"]
    assert failing.calls == []

    fresh = _FakeAdmission()
    replayed = PlanApprovalCoordinator.replay(
        tmp_path / "plan_approvals.jsonl", admission=fresh, clock=_FixedClock(),
        verifier=_FakeVerifier(),
        preset_registry=_FakePresetRegistry({PRESET_ID: PRESET_REC_DIG}),
        catalog_digest=CAT_DIG, registry_digest=REG_DIG)
    dec = replayed.load_decision("r-1", CAND_A)
    assert dec is not None and dec.actor_id == f"lease:{lease.lease_id}"
    assert len(fresh.calls) == 1
    assert _lease_view(replayed, lease.lease_id, now=NOW).admissions_used == 1


def test_replay_reconstructs_leases_and_balances_no_double(tmp_path):
    admission = _FakeAdmission()
    coord = _coord(tmp_path, admission=admission)
    lease = _issue(coord, max_admissions=5, budget_cap=100)
    _try(coord, _pending(request_id="r-1", candidate=CAND_A, budget_llm=10),
         idempotency_key="i1")
    _try(coord, _pending(request_id="r-2", candidate=CAND_E, budget_llm=20),
         idempotency_key="i2")

    for _ in range(2):
        replayed = PlanApprovalCoordinator.replay(
            tmp_path / "plan_approvals.jsonl", admission=admission,
            clock=_FixedClock(), verifier=_FakeVerifier(),
            preset_registry=_FakePresetRegistry({PRESET_ID: PRESET_REC_DIG}),
            catalog_digest=CAT_DIG, registry_digest=REG_DIG)
        v = _lease_view(replayed, lease.lease_id, now=NOW)
        assert v.admissions_used == 2 and v.budget_used == 30
    # replayed twice, still exactly two admission effects.
    assert len(admission.calls) == 2


# --------------------------------------------------------------------------- #
# helper: fetch one lease's folded balance view                               #
# --------------------------------------------------------------------------- #
def _lease_view(coord, lease_id, *, now):
    for v in coord.list_leases(now=now):
        if v.lease.lease_id == lease_id:
            return v
    raise AssertionError(f"lease {lease_id} not listed")
