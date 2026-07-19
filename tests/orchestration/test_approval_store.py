# -*- coding: utf-8 -*-
"""Phase 7 - Task 7: durable digest-bound plan-approval journal + coordinator.

Covers ``guanlan_v2.orchestration.approval``:

* :class:`ApprovalJournalRow` — the append-only row whose ``row_digest`` binds
  ``(row_kind, seq, payload)`` canonical JSON;
* :class:`PlanApprovalCoordinator` — ``register_pending`` (idempotent by
  ``(request_id, candidate_plan_digest)``), ``list_pending``, ``decide``
  (verifier fail-closed, journal-append-FIRST persist-then-publish, deterministic
  admission idempotency key, exactly-one terminal decision), ``load_decision`` and
  the crash-recovery ``replay`` classmethod;
* :func:`admit_after_approval` thin pass-through to ``freeze_and_admit_candidate``;
* journal file semantics: fsync-per-append, torn-tail tolerance vs mid-file /
  digest-mismatch hard failure, and rendered-md digest re-verification on fold.

The admission dependency is a **fake** that records ``record_approval`` calls and
models durable idempotency by the admission idempotency key (a real admission
service dedupes against its own persisted event store). A temp-path journal is
used throughout — no ``var/`` is ever written.

Run: ``python -m pytest tests/orchestration/test_approval_store.py -v``
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from guanlan_v2.orchestration.enums import ApprovalDecision, ApprovalPolicy, PlanSource
from guanlan_v2.orchestration.digest import content_digest
from guanlan_v2.orchestration.memory.models import AuthenticatedAdminPrincipal
from guanlan_v2.orchestration.plan_diff import (
    PLAN_DIFF_SCHEMA_REF,
    PendingPlanApproval,
    PlanDiff,
    PlanDiffEntry,
)
from guanlan_v2.orchestration.refs import PayloadRef, TypedPayloadRef

from guanlan_v2.orchestration.approval import (
    ApprovalAuthorityError,
    ApprovalDecisionConflict,
    ApprovalJournalRow,
    ApprovalStoreCorrupt,
    PlanApprovalCoordinator,
    UnknownPendingCandidate,
    admit_after_approval,
)

UTC = timezone.utc
NOW = datetime(2026, 7, 20, 2, 0, tzinfo=UTC)
CAND_A = "a" * 64
CAND_D = "d" * 64
DIG_B = "b" * 64
REQ_DIGEST = "c" * 64
GOOD = "good-cred"


# --------------------------------------------------------------------------- #
# Doubles                                                                      #
# --------------------------------------------------------------------------- #
class _FixedClock:
    def now(self) -> datetime:
        return NOW


class _VerifyDenied(Exception):
    """The fake verifier's refusal for an unverifiable credential."""


class _FakeVerifier:
    """Mirrors Phase 3 ``AdminReviewVerifier``: fail-closed ``verify`` that raises
    for a bad credential and returns an ``AuthenticatedAdminPrincipal`` otherwise."""

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
    """A stand-in for the Phase-1 ``RunEvent`` ``record_approval`` returns."""

    def __init__(self, *, event_id: str, candidate_id: str, decision: ApprovalDecision,
                 actor: str, idempotency_key: str) -> None:
        self.event_id = event_id
        self.candidate_id = candidate_id
        self.decision = decision
        self.actor = actor
        self.idempotency_key = idempotency_key


class _AdmissionBoom(Exception):
    """Injected admission failure (a stand-in for a transient store failure)."""


class _FakeAdmission:
    """Records ``record_approval`` effects with durable idempotency by key.

    ``calls`` lists exactly-once *effects* (a repeat key returns the cached event
    and never re-records), so ``len(calls) == 1`` proves a single terminal
    admission effect even across replay resubmission. ``fail_once`` makes the first
    call for each fresh key raise (modelling a crash between journal append and a
    completed ``record_approval``); the retry succeeds.
    """

    def __init__(self, *, fail_once: bool = False) -> None:
        self.calls: list[tuple] = []
        self.freeze_calls: list[tuple] = []
        self._events: dict[str, _FakeRunEvent] = {}
        self._failed: set[str] = set()
        self.fail_once = fail_once

    def record_approval(self, candidate_id, approval_input, *, authenticated_actor,
                        idempotency_key):
        if idempotency_key in self._events:  # durable idempotency
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

    def freeze_and_admit_candidate(self, candidate_id, *, reservation_id,
                                   approval_event_id, idempotency_key):
        self.freeze_calls.append(
            (candidate_id, reservation_id, approval_event_id, idempotency_key))
        return (f"plan-{candidate_id[:6]}", f"admitted-{candidate_id[:6]}")


# --------------------------------------------------------------------------- #
# Synthetic pending-card builder (no pilot catalog needed)                     #
# --------------------------------------------------------------------------- #
def _pending(request_id: str = "r-1", candidate: str = CAND_A, *,
             source: PlanSource = PlanSource.DYNAMIC, goal: str = "g",
             preset_id=None, preset_record_digest=None,
             worker_ids=("text.sentiment",)) -> PendingPlanApproval:
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
        worker_ids=worker_ids, plan_diff_ref=ref, rendered_md="body",
        rendered_from_diff_digest=dd, candidate_id="cand-" + candidate[:6],
        requested_at=NOW, preset_id=preset_id,
        preset_record_digest=preset_record_digest)


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


def _coord(tmp_path, *, admission=None, verifier=None, console_emit=None,
           name="plan_approvals.jsonl"):
    return PlanApprovalCoordinator(
        tmp_path / name,
        admission=admission if admission is not None else _FakeAdmission(),
        clock=_FixedClock(),
        verifier=verifier if verifier is not None else _FakeVerifier(),
        console_emit=console_emit)


# =========================================================================== #
# ApprovalJournalRow — the digest-bound row model                              #
# =========================================================================== #
def test_journal_row_digest_binds_kind_seq_payload():
    payload = {"x": 1, "y": "z"}
    row = ApprovalJournalRow.build(row_kind="pending", seq=1, payload=payload)
    assert row.row_kind == "pending" and row.seq == 1 and row.payload == payload
    assert row.row_digest == content_digest(
        {"row_kind": "pending", "seq": 1, "payload": payload})
    # verify() accepts an honest row and rejects any tamper.
    row.verify()
    tampered = row.model_copy(update={"payload": {"x": 2, "y": "z"}})
    with pytest.raises(ApprovalStoreCorrupt):
        tampered.verify()


# =========================================================================== #
# register_pending / list_pending                                             #
# =========================================================================== #
def test_register_pending_appends_row_and_lists(tmp_path):
    coord = _coord(tmp_path)
    returned = coord.register_pending(_pending(), idempotency_key="k1")
    assert returned == _pending()
    assert coord.list_pending() == (_pending(),)
    rows = _rows(tmp_path / "plan_approvals.jsonl")
    assert len(rows) == 1 and rows[0].row_kind == "pending" and rows[0].seq == 1


def test_register_pending_is_idempotent_on_identical_content(tmp_path):
    coord = _coord(tmp_path)
    coord.register_pending(_pending(), idempotency_key="k1")
    # a re-register with identical semantic content returns the stored card and
    # appends NO second row.
    again = coord.register_pending(_pending(), idempotency_key="k1")
    assert again == _pending()
    assert len(_read_lines(tmp_path / "plan_approvals.jsonl")) == 1
    assert coord.list_pending() == (_pending(),)


def test_register_pending_conflict_on_different_content(tmp_path):
    coord = _coord(tmp_path)
    coord.register_pending(_pending(goal="first goal"), idempotency_key="k1")
    with pytest.raises(ApprovalDecisionConflict):
        coord.register_pending(_pending(goal="a DIFFERENT goal"), idempotency_key="k1")
    # nothing extra persisted.
    assert len(_read_lines(tmp_path / "plan_approvals.jsonl")) == 1


def test_register_pending_emits_console_request(tmp_path):
    seen = []
    coord = _coord(tmp_path, console_emit=lambda n, p: seen.append((n, p)))
    coord.register_pending(_pending(), idempotency_key="k1")
    assert seen and seen[0][0] == "plan_approval_request"
    assert seen[0][1]["candidate_plan_digest"] == CAND_A


# =========================================================================== #
# decide — happy paths (APPROVED and REJECTED)                                #
# =========================================================================== #
@pytest.mark.parametrize("decision", [ApprovalDecision.APPROVED, ApprovalDecision.REJECTED])
def test_decide_happy(tmp_path, decision):
    admission = _FakeAdmission()
    seen = []
    coord = _coord(tmp_path, admission=admission,
                   console_emit=lambda n, p: seen.append((n, p)))
    coord.register_pending(_pending(), idempotency_key="k1")

    approval, event = coord.decide(
        request_id="r-1", candidate_plan_digest=CAND_A, decision=decision,
        actor=GOOD, reason="reviewed", idempotency_key="d1")

    # a real Phase-1 PlanApproval with the VERIFIED actor id (not the raw credential).
    assert approval.request_id == "r-1" and approval.candidate_plan_digest == CAND_A
    assert approval.decision is decision and approval.actor_id == "admin-1"
    assert approval.decided_at == NOW and approval.reason == "reviewed"
    assert event is admission._events[event.idempotency_key]

    # the admission received an ApprovalSubmission (NOT a PlanApproval) with the
    # verified actor + a deterministic idempotency key.
    assert len(admission.calls) == 1
    cand_id, submission, actor, idem = admission.calls[0]
    assert cand_id == CAND_A and actor == "admin-1"
    assert submission.request_id == "r-1" and submission.candidate_plan_digest == CAND_A
    assert submission.decision is decision
    assert not isinstance(submission, PendingPlanApproval)

    # journal now has pending + decision rows; the card is no longer pending; the
    # durable decision is readable.
    rows = _rows(tmp_path / "plan_approvals.jsonl")
    assert [r.row_kind for r in rows] == ["pending", "decision"]
    assert coord.list_pending() == ()
    assert coord.load_decision("r-1", CAND_A) == approval
    # console resolved event emitted last.
    assert seen[-1][0] == "plan_approval_resolved"
    assert seen[-1][1]["decision"] == decision.value


def test_decide_actor_id_is_the_verified_id_not_the_credential(tmp_path):
    coord = _coord(tmp_path, verifier=_FakeVerifier(actor="alice"))
    coord.register_pending(_pending(), idempotency_key="k1")
    approval, _ = coord.decide(
        request_id="r-1", candidate_plan_digest=CAND_A,
        decision=ApprovalDecision.APPROVED, actor=GOOD, reason=None,
        idempotency_key="d1")
    assert approval.actor_id == "alice"  # the verifier's principal, never GOOD


# =========================================================================== #
# decide — idempotency + conflict + unknown                                   #
# =========================================================================== #
def test_decide_idempotent_same_decision_returns_stored_pair(tmp_path):
    admission = _FakeAdmission()
    coord = _coord(tmp_path, admission=admission)
    coord.register_pending(_pending(), idempotency_key="k1")
    a1, e1 = coord.decide(request_id="r-1", candidate_plan_digest=CAND_A,
                          decision=ApprovalDecision.APPROVED, actor=GOOD,
                          reason="x", idempotency_key="d1")
    a2, e2 = coord.decide(request_id="r-1", candidate_plan_digest=CAND_A,
                          decision=ApprovalDecision.APPROVED, actor=GOOD,
                          reason="y", idempotency_key="d2")
    assert a2 == a1 and e2 is e1
    # exactly one terminal decision row + one admission effect.
    assert [r.row_kind for r in _rows(tmp_path / "plan_approvals.jsonl")] == \
        ["pending", "decision"]
    assert len(admission.calls) == 1


def test_decide_conflict_on_different_decision(tmp_path):
    coord = _coord(tmp_path)
    coord.register_pending(_pending(), idempotency_key="k1")
    coord.decide(request_id="r-1", candidate_plan_digest=CAND_A,
                 decision=ApprovalDecision.APPROVED, actor=GOOD, reason=None,
                 idempotency_key="d1")
    with pytest.raises(ApprovalDecisionConflict):
        coord.decide(request_id="r-1", candidate_plan_digest=CAND_A,
                     decision=ApprovalDecision.REJECTED, actor=GOOD, reason=None,
                     idempotency_key="d2")
    # the original terminal decision is untouched; no extra decision row.
    assert coord.load_decision("r-1", CAND_A).decision is ApprovalDecision.APPROVED
    assert [r.row_kind for r in _rows(tmp_path / "plan_approvals.jsonl")] == \
        ["pending", "decision"]


def test_decide_unknown_candidate(tmp_path):
    coord = _coord(tmp_path)
    # no pending card registered for this digest.
    with pytest.raises(UnknownPendingCandidate):
        coord.decide(request_id="r-1", candidate_plan_digest=CAND_A,
                     decision=ApprovalDecision.APPROVED, actor=GOOD, reason=None,
                     idempotency_key="d1")
    assert _read_lines(tmp_path / "plan_approvals.jsonl") == []  # nothing persisted


# =========================================================================== #
# decide — fail-closed verifier                                               #
# =========================================================================== #
def test_decide_unverified_actor_refusal_persists_nothing(tmp_path):
    admission = _FakeAdmission()
    coord = _coord(tmp_path, admission=admission)
    coord.register_pending(_pending(), idempotency_key="k1")
    with pytest.raises(_VerifyDenied):
        coord.decide(request_id="r-1", candidate_plan_digest=CAND_A,
                     decision=ApprovalDecision.APPROVED, actor="bad-cred",
                     reason=None, idempotency_key="d1")
    # no decision row; the card is still pending; admission never called.
    assert [r.row_kind for r in _rows(tmp_path / "plan_approvals.jsonl")] == ["pending"]
    assert coord.list_pending() == (_pending(),)
    assert coord.load_decision("r-1", CAND_A) is None
    assert admission.calls == []


def test_decide_verifier_none_has_no_decision_path(tmp_path):
    admission = _FakeAdmission()
    coord = PlanApprovalCoordinator(
        tmp_path / "plan_approvals.jsonl", admission=admission,
        clock=_FixedClock(), verifier=None)
    coord.register_pending(_pending(), idempotency_key="k1")
    with pytest.raises(ApprovalAuthorityError):
        coord.decide(request_id="r-1", candidate_plan_digest=CAND_A,
                     decision=ApprovalDecision.APPROVED, actor=GOOD, reason=None,
                     idempotency_key="d1")
    assert [r.row_kind for r in _rows(tmp_path / "plan_approvals.jsonl")] == ["pending"]
    assert admission.calls == []


# =========================================================================== #
# persist-then-publish ordering + contained console failure                    #
# =========================================================================== #
def test_persist_then_publish_ordering(tmp_path):
    order = []
    journal = tmp_path / "plan_approvals.jsonl"

    class _OrderAdmission(_FakeAdmission):
        def record_approval(self, *a, **k):
            # the decision row must already be durable when admission is called.
            order.append(("admission", len(_read_lines(journal))))
            return super().record_approval(*a, **k)

    admission = _OrderAdmission()
    coord = PlanApprovalCoordinator(
        journal, admission=admission, clock=_FixedClock(),
        verifier=_FakeVerifier(),
        console_emit=lambda n, p: order.append(("console", n)))
    coord.register_pending(_pending(), idempotency_key="k1")
    order.clear()
    coord.decide(request_id="r-1", candidate_plan_digest=CAND_A,
                 decision=ApprovalDecision.APPROVED, actor=GOOD, reason=None,
                 idempotency_key="d1")
    # journal append (2 rows) strictly precedes admission, which precedes console.
    assert order == [("admission", 2), ("console", "plan_approval_resolved")]


def test_console_emit_failure_never_rolls_back_decision(tmp_path):
    admission = _FakeAdmission()

    def _boom(name, payload):
        raise RuntimeError("console down")

    coord = _coord(tmp_path, admission=admission, console_emit=_boom)
    coord.register_pending(_pending(), idempotency_key="k1")  # emit boom swallowed
    approval, event = coord.decide(
        request_id="r-1", candidate_plan_digest=CAND_A,
        decision=ApprovalDecision.APPROVED, actor=GOOD, reason=None,
        idempotency_key="d1")
    # the durable decision + admission effect survive a console failure.
    assert coord.load_decision("r-1", CAND_A) == approval
    assert len(admission.calls) == 1
    assert [r.row_kind for r in _rows(tmp_path / "plan_approvals.jsonl")] == \
        ["pending", "decision"]


# =========================================================================== #
# crash-recovery trio (replay resubmits idempotently)                          #
# =========================================================================== #
def test_crash_after_append_before_record_approval_replay_resubmits(tmp_path):
    # pre-crash: admission raises on the first call, so decide() aborts AFTER the
    # decision row is durable but BEFORE record_approval completes.
    failing = _FakeAdmission(fail_once=True)
    coord = _coord(tmp_path, admission=failing)
    coord.register_pending(_pending(), idempotency_key="k1")
    with pytest.raises(_AdmissionBoom):
        coord.decide(request_id="r-1", candidate_plan_digest=CAND_A,
                     decision=ApprovalDecision.APPROVED, actor=GOOD, reason=None,
                     idempotency_key="d1")
    # the decision row is durable even though admission never recorded.
    assert [r.row_kind for r in _rows(tmp_path / "plan_approvals.jsonl")] == \
        ["pending", "decision"]
    assert failing.calls == []

    # restart: a fresh admission (its durable store had nothing) replays and the
    # missing admission call is resubmitted exactly once.
    fresh = _FakeAdmission()
    replayed = PlanApprovalCoordinator.replay(
        tmp_path / "plan_approvals.jsonl", admission=fresh, clock=_FixedClock(),
        verifier=_FakeVerifier())
    decision = replayed.load_decision("r-1", CAND_A)
    assert decision is not None and decision.decision is ApprovalDecision.APPROVED
    assert len(fresh.calls) == 1
    assert replayed.list_pending() == ()


def test_double_replay_never_double_consumes(tmp_path):
    # a decision durably recorded; the SAME admission instance models its own
    # durable store surviving the restart(s).
    admission = _FakeAdmission()
    coord = _coord(tmp_path, admission=admission)
    coord.register_pending(_pending(), idempotency_key="k1")
    coord.decide(request_id="r-1", candidate_plan_digest=CAND_A,
                 decision=ApprovalDecision.APPROVED, actor=GOOD, reason=None,
                 idempotency_key="d1")
    assert len(admission.calls) == 1

    for _ in range(2):
        again = PlanApprovalCoordinator.replay(
            tmp_path / "plan_approvals.jsonl", admission=admission,
            clock=_FixedClock(), verifier=_FakeVerifier())
        assert again.load_decision("r-1", CAND_A).decision is ApprovalDecision.APPROVED
    # replayed twice, still exactly one admission effect.
    assert len(admission.calls) == 1


def test_ordering_journal_row_survives_admission_raise_and_replay_completes(tmp_path):
    # this is the ordering invariant proved end to end: append precedes publish, so
    # a failed publish leaves a durable decision that replay completes.
    failing = _FakeAdmission(fail_once=True)
    coord = _coord(tmp_path, admission=failing)
    coord.register_pending(_pending(), idempotency_key="k1")
    with pytest.raises(_AdmissionBoom):
        coord.decide(request_id="r-1", candidate_plan_digest=CAND_A,
                     decision=ApprovalDecision.REJECTED, actor=GOOD, reason="no",
                     idempotency_key="d1")
    # same-instance retry heals: the decision row is reused, record_approval
    # resubmitted, one terminal decision + one effect.
    approval, event = coord.decide(
        request_id="r-1", candidate_plan_digest=CAND_A,
        decision=ApprovalDecision.REJECTED, actor=GOOD, reason="no",
        idempotency_key="d2")
    assert approval.decision is ApprovalDecision.REJECTED
    assert len(failing.calls) == 1
    assert [r.row_kind for r in _rows(tmp_path / "plan_approvals.jsonl")] == \
        ["pending", "decision"]


# =========================================================================== #
# replay reconstruction of mixed state                                        #
# =========================================================================== #
def test_replay_reconstructs_pending_and_decisions(tmp_path):
    admission = _FakeAdmission()
    coord = _coord(tmp_path, admission=admission)
    coord.register_pending(_pending(request_id="r-1", candidate=CAND_A),
                           idempotency_key="k1")
    coord.register_pending(_pending(request_id="r-2", candidate=CAND_D),
                           idempotency_key="k2")
    coord.decide(request_id="r-1", candidate_plan_digest=CAND_A,
                 decision=ApprovalDecision.APPROVED, actor=GOOD, reason=None,
                 idempotency_key="d1")

    replayed = PlanApprovalCoordinator.replay(
        tmp_path / "plan_approvals.jsonl", admission=admission,
        clock=_FixedClock(), verifier=_FakeVerifier())
    # the undecided r-2 card remains pending; the decided r-1 card is a decision.
    pend = replayed.list_pending()
    assert len(pend) == 1 and pend[0].candidate_plan_digest == CAND_D
    assert replayed.load_decision("r-1", CAND_A).decision is ApprovalDecision.APPROVED
    assert replayed.load_decision("r-2", CAND_D) is None


# =========================================================================== #
# journal integrity: torn tail vs mid-file / digest-mismatch hard fail          #
# =========================================================================== #
def _append_raw(path, text: str) -> None:
    with open(path, "ab") as f:
        f.write(text.encode("utf-8"))


def test_torn_final_line_is_tolerated(tmp_path):
    coord = _coord(tmp_path)
    coord.register_pending(_pending(), idempotency_key="k1")
    # a crash mid-append leaves a partial, non-JSON final line (no trailing newline).
    _append_raw(tmp_path / "plan_approvals.jsonl", '{"row_kind":"decisi')
    # construction folds cleanly, dropping the torn tail; the pending card survives.
    coord2 = _coord(tmp_path)
    assert coord2.list_pending() == (_pending(),)


def test_mid_file_corruption_is_hard_failure(tmp_path):
    coord = _coord(tmp_path)
    coord.register_pending(_pending(request_id="r-1", candidate=CAND_A),
                           idempotency_key="k1")
    coord.register_pending(_pending(request_id="r-2", candidate=CAND_D),
                           idempotency_key="k2")
    # corrupt the FIRST (non-final) line into non-JSON.
    lines = _read_lines(tmp_path / "plan_approvals.jsonl")
    lines[0] = "NOT JSON AT ALL"
    (tmp_path / "plan_approvals.jsonl").write_bytes(
        ("\n".join(lines) + "\n").encode("utf-8"))
    with pytest.raises(ApprovalStoreCorrupt):
        _coord(tmp_path)


def test_row_digest_mismatch_is_hard_failure(tmp_path):
    coord = _coord(tmp_path)
    coord.register_pending(_pending(), idempotency_key="k1")
    # tamper the payload but keep the stale row_digest -> digest mismatch.
    line = _read_lines(tmp_path / "plan_approvals.jsonl")[0]
    obj = json.loads(line)
    obj["payload"]["goal"] = "tampered goal"  # payload changed, row_digest stale
    (tmp_path / "plan_approvals.jsonl").write_bytes(
        (json.dumps(obj) + "\n").encode("utf-8"))
    with pytest.raises(ApprovalStoreCorrupt):
        _coord(tmp_path)


def test_tampered_card_digest_binding_is_hard_failure(tmp_path):
    coord = _coord(tmp_path)
    coord.register_pending(_pending(), idempotency_key="k1")
    # tamper rendered_from_diff_digest AND recompute row_digest so the row-level
    # digest passes: the card's own diff-binding re-verification must still reject.
    line = _read_lines(tmp_path / "plan_approvals.jsonl")[0]
    obj = json.loads(line)
    obj["payload"]["rendered_from_diff_digest"] = DIG_B  # now != plan_diff_ref digest
    obj["row_digest"] = content_digest(
        {"row_kind": obj["row_kind"], "seq": obj["seq"], "payload": obj["payload"]})
    (tmp_path / "plan_approvals.jsonl").write_bytes(
        (json.dumps(obj) + "\n").encode("utf-8"))
    with pytest.raises(ApprovalStoreCorrupt):
        _coord(tmp_path)


def test_out_of_order_seq_is_hard_failure(tmp_path):
    coord = _coord(tmp_path)
    coord.register_pending(_pending(request_id="r-1", candidate=CAND_A),
                           idempotency_key="k1")
    coord.register_pending(_pending(request_id="r-2", candidate=CAND_D),
                           idempotency_key="k2")
    # rewrite the second row with a re-sealed but wrong seq (skips 2 -> 5): the
    # row_digest recomputes fine, but the fold's seq continuity check rejects it.
    lines = _read_lines(tmp_path / "plan_approvals.jsonl")
    obj = json.loads(lines[1])
    obj["seq"] = 5
    obj["row_digest"] = content_digest(
        {"row_kind": obj["row_kind"], "seq": 5, "payload": obj["payload"]})
    lines[1] = json.dumps(obj)
    (tmp_path / "plan_approvals.jsonl").write_bytes(
        ("\n".join(lines) + "\n").encode("utf-8"))
    with pytest.raises(ApprovalStoreCorrupt):
        _coord(tmp_path)


# =========================================================================== #
# admit_after_approval — thin pass-through                                     #
# =========================================================================== #
def test_admit_after_approval_is_a_passthrough(tmp_path):
    admission = _FakeAdmission()
    out = admit_after_approval(
        admission=admission, candidate_id=CAND_A, reservation_id="resv-1",
        approval_event_id="ev-1", idempotency_key="adm-1")
    assert out == (f"plan-{CAND_A[:6]}", f"admitted-{CAND_A[:6]}")
    assert admission.freeze_calls == [(CAND_A, "resv-1", "ev-1", "adm-1")]


# =========================================================================== #
# load_decision                                                               #
# =========================================================================== #
def test_load_decision_none_when_undecided(tmp_path):
    coord = _coord(tmp_path)
    coord.register_pending(_pending(), idempotency_key="k1")
    assert coord.load_decision("r-1", CAND_A) is None


def test_deterministic_admission_idempotency_key_survives_replay(tmp_path):
    admission = _FakeAdmission()
    coord = _coord(tmp_path, admission=admission)
    coord.register_pending(_pending(), idempotency_key="k1")
    _, event = coord.decide(request_id="r-1", candidate_plan_digest=CAND_A,
                            decision=ApprovalDecision.APPROVED, actor=GOOD,
                            reason=None, idempotency_key="d1")
    live_key = event.idempotency_key
    # replay derives the SAME key deterministically (no double effect).
    replayed = PlanApprovalCoordinator.replay(
        tmp_path / "plan_approvals.jsonl", admission=admission,
        clock=_FixedClock(), verifier=_FakeVerifier())
    assert replayed.load_decision("r-1", CAND_A) is not None
    assert len(admission.calls) == 1
    assert admission.calls[0][3] == live_key
