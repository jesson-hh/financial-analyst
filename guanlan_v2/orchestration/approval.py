# -*- coding: utf-8 -*-
"""Phase 7 - Task 7: durable, digest-bound plan-approval journal + coordinator.

The dynamic Planner (Task 1) emits a Phase 1 :class:`~guanlan_v2.orchestration.spec.PlanDraft`
candidate that a human must approve before it can freeze. Task 6 froze the
reviewer-facing card (:class:`~guanlan_v2.orchestration.plan_diff.PendingPlanApproval`);
this module owns the *carrier* that makes a human decision **survive process
death** and **bind the exact candidate digest** — the two properties the console
confirm gate's in-memory 600 s futures deliberately do not have.

What this module owns
---------------------
* :class:`PlanApprovalCoordinator` — an append-only journal
  (``var/orchestration/plan_approvals.jsonl``) of ``pending`` and ``decision``
  rows plus the in-memory fold rebuilt from it. ``register_pending`` records a
  card (fsync before return, idempotent by ``(request_id, candidate_plan_digest)``);
  ``decide`` verifies the actor **fail-closed**, appends the ``decision`` row
  **first** (durability before publication), then calls the Phase 2
  :meth:`~guanlan_v2.orchestration.admission.PlanAdmissionService.record_approval`
  with a *deterministic* idempotency key, then emits the console event; a
  console-emit failure never rolls back a durable decision. ``replay`` folds the
  journal and re-submits any decision's admission call idempotently, so a crash
  between the append and ``record_approval`` recovers to exactly one terminal
  decision.
* :class:`ApprovalJournalRow` — the storage row. Its ``row_digest`` seals
  ``(row_kind, seq, payload)`` over the house canonical JSON, so any byte tamper —
  a re-ordered row, a mutated payload — is caught on fold. A torn (non-JSON)
  *final* line (an interrupted append) is dropped with a warning; any *earlier*
  malformed row, a digest mismatch, a broken seq chain, or a card whose stored
  diff-binding no longer holds is a hard :class:`ApprovalStoreCorrupt` failure —
  no silent skip, no partial trust.
* :func:`admit_after_approval` — a thin, reviewed pass-through to
  :meth:`~guanlan_v2.orchestration.admission.PlanAdmissionService.freeze_and_admit_candidate`
  (one reviewed call site for the console handler; no wrapper semantics).

The six required invariants
---------------------------
1. **survives process death** — the durable journal + idempotent admission
   resubmission recover to one terminal decision at every crash cut;
2. **digest-bound** — a decision names the exact pre-freeze candidate digest of a
   registered card; a re-validated draft with a new digest needs a new card;
3. **writes nothing into a Plan** — approval identity lives only in the journal +
   the admission ``RunEvent``s;
4. **no auto-resolve** — a pending card has no timeout and never self-resolves;
5. **persist-then-publish** — journal append << admission event << console emit;
6. **fail-closed verifier** — ``verifier=None`` or an unverifiable actor means no
   decision path exists (mirrors Phase 3's ``MemoryProposalService.decide``).

Task 7b (``ApprovalLease``) extends this SAME module and journal additively: the
:class:`ApprovalJournalRow` ``row_kind`` is a :data:`typing.Literal` that 7b widens
with ``lease_issued`` / ``lease_consumed`` / ``lease_revoked`` and the fold loop
dispatches on it, so a new row kind is a pure addition (see
:meth:`PlanApprovalCoordinator._fold`).
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Literal

from pydantic import ValidationError

from guanlan_v2.orchestration.admission import ApprovalSubmission
from guanlan_v2.orchestration.digest import (
    ContractModel,
    DigestHex,
    NonEmptyStr,
    PositiveInt,
    content_digest,
)
from guanlan_v2.orchestration.enums import ApprovalDecision
from guanlan_v2.orchestration.events import PlanApproval
from guanlan_v2.orchestration.plan_diff import PendingPlanApproval
from guanlan_v2.orchestration.runtime_clock import AuthoritativeClock, clock_now

if TYPE_CHECKING:  # annotation-only imports (kept off the runtime import graph)
    from guanlan_v2.orchestration.admission import PlanAdmissionService
    from guanlan_v2.orchestration.events import RunEvent
    from guanlan_v2.orchestration.runtime_contracts import PlanAdmitted
    from guanlan_v2.orchestration.spec import Plan

__all__ = [
    "ApprovalDecisionConflict",
    "UnknownPendingCandidate",
    "ApprovalStoreCorrupt",
    "ApprovalAuthorityError",
    "ApprovalJournalRow",
    "PlanApprovalCoordinator",
    "admit_after_approval",
]

_LOG = logging.getLogger(__name__)

#: the closed row-kind vocabulary of Task 7. Task 7b widens this Literal additively
#: (``lease_issued`` / ``lease_consumed`` / ``lease_revoked``) — the fold loop
#: dispatches on ``row_kind`` so an unknown-to-Task-7 kind is simply new behaviour,
#: never a rewrite of these two.
ApprovalRowKind = Literal["pending", "decision"]


# --------------------------------------------------------------------------- #
# Errors                                                                       #
# --------------------------------------------------------------------------- #
class ApprovalDecisionConflict(Exception):
    """A second, semantically different registration/decision for one candidate.

    Raised when a re-``register_pending`` supplies different card content for an
    existing ``(request_id, candidate_plan_digest)``, or when ``decide`` is asked
    to record a *different* terminal decision than the one already on file.
    """


class UnknownPendingCandidate(KeyError):
    """``decide`` named a ``(request_id, candidate_plan_digest)`` with no pending card."""


class ApprovalStoreCorrupt(Exception):
    """The journal is unreadable beyond a tolerated torn final line.

    An earlier malformed row, a ``row_digest`` mismatch, a broken seq chain, a
    duplicate/contradictory terminal decision, or a stored card whose diff-binding
    no longer holds — none are silently skipped.
    """


class ApprovalAuthorityError(Exception):
    """No decision path exists: ``verifier=None`` (fail closed).

    Mirrors Phase 3's ``MemoryProposalService.decide`` refusal — a coordinator
    built without a verifier can never record a decision; an *unverifiable* actor
    is refused by the verifier's own ``verify`` raising.
    """


# --------------------------------------------------------------------------- #
# ApprovalJournalRow — the digest-bound storage row                            #
# --------------------------------------------------------------------------- #
class ApprovalJournalRow(ContractModel):
    """One append-only journal row: a typed payload sealed by its ``row_digest``.

    ``payload`` is the JSON-native dump of the row's typed model (a
    :class:`~guanlan_v2.orchestration.plan_diff.PendingPlanApproval` for a
    ``pending`` row, a :class:`~guanlan_v2.orchestration.events.PlanApproval` for a
    ``decision`` row). ``row_digest`` is the house ``content_digest`` over the
    canonical JSON of ``(row_kind, seq, payload)``, so a row is self-verifying:
    reordering, a mutated payload or a rewritten seq all break it.

    This is an internal storage row, not a registered contract — it never enters a
    schema registry (Task 9 registers the *public* Phase-7 contracts, not this).
    """

    schema_version: Literal["1"] = "1"
    row_kind: ApprovalRowKind
    seq: PositiveInt
    payload: dict[str, Any]
    row_digest: DigestHex

    @staticmethod
    def compute_digest(*, row_kind: str, seq: int, payload: dict[str, Any]) -> str:
        """The canonical ``content_digest`` over ``(row_kind, seq, payload)``."""
        return content_digest({"row_kind": row_kind, "seq": seq, "payload": payload})

    @classmethod
    def build(cls, *, row_kind: str, seq: int, payload: dict[str, Any]) -> "ApprovalJournalRow":
        """Seal a row: compute its ``row_digest`` from the field values."""
        return cls(
            row_kind=row_kind, seq=seq, payload=payload,
            row_digest=cls.compute_digest(row_kind=row_kind, seq=seq, payload=payload))

    def verify(self) -> None:
        """Raise :class:`ApprovalStoreCorrupt` unless ``row_digest`` recomputes."""
        expected = self.compute_digest(
            row_kind=self.row_kind, seq=self.seq, payload=self.payload)
        if self.row_digest != expected:
            raise ApprovalStoreCorrupt(
                f"journal row seq={self.seq} row_digest does not recompute "
                f"(declared {self.row_digest!r}, computed {expected!r})")


# --------------------------------------------------------------------------- #
# serialization helpers                                                        #
# --------------------------------------------------------------------------- #
def _to_payload(model: Any) -> dict[str, Any]:
    """The JSON-native dump of a typed model (round-trips through canonical JSON)."""
    return json.loads(model.model_dump_json())


def _admission_idempotency_key(
    request_id: str, candidate_plan_digest: str, decision: ApprovalDecision
) -> str:
    """The deterministic admission idempotency key for one terminal decision.

    Derived purely from ``(request_id, candidate_plan_digest, decision)`` via the
    house canonical digest, so replay reproduces the exact key without any caller
    token — the property that makes admission resubmission idempotent across a
    process restart.
    """
    return "plan-approval:" + content_digest(
        [request_id, candidate_plan_digest, decision.value])


# --------------------------------------------------------------------------- #
# PlanApprovalCoordinator                                                      #
# --------------------------------------------------------------------------- #
class PlanApprovalCoordinator:
    """Durable coordinator over the plan-approval journal (single journal file)."""

    def __init__(
        self,
        journal_path: Path,
        *,
        admission: "PlanAdmissionService",
        clock: AuthoritativeClock,
        verifier: Any,
        console_emit: Callable[[str, dict], None] | None = None,
    ) -> None:
        self._path = Path(journal_path)
        self._admission = admission
        self._clock = clock
        self._verifier = verifier
        self._console_emit = console_emit
        # in-memory fold, rebuilt from the journal on every construction.
        self._seq = 0
        self._pending: dict[tuple[str, str], PendingPlanApproval] = {}
        self._decisions: dict[tuple[str, str], PlanApproval] = {}
        # decision -> RunEvent, populated only once an admission call completes.
        self._events: dict[tuple[str, str], "RunEvent"] = {}
        self._fold()

    # ------------------------------------------------------------------ #
    # public API                                                         #
    # ------------------------------------------------------------------ #
    def register_pending(
        self, pending: PendingPlanApproval, *, idempotency_key: NonEmptyStr
    ) -> PendingPlanApproval:
        """Record a pending card (fsync before return); idempotent by identity.

        Identity is ``(request_id, candidate_plan_digest)``: an identical
        re-register (same semantic digest) returns the stored card and appends no
        row; different semantic content for the same identity raises
        :class:`ApprovalDecisionConflict`.
        """
        key = (pending.request_id, pending.candidate_plan_digest)
        if key in self._decisions:
            raise ApprovalDecisionConflict(
                "candidate already has a terminal decision; a pending card cannot "
                f"be re-registered for request {pending.request_id!r} / candidate "
                f"{pending.candidate_plan_digest}")
        stored = self._pending.get(key)
        if stored is not None:
            if stored.semantic_digest() != pending.semantic_digest():
                raise ApprovalDecisionConflict(
                    "a semantically different pending card already exists for "
                    f"request {pending.request_id!r} / candidate "
                    f"{pending.candidate_plan_digest}")
            return stored
        self._append("pending", _to_payload(pending))
        self._pending[key] = pending
        self._emit("plan_approval_request", {
            "request_id": pending.request_id,
            "candidate_plan_digest": pending.candidate_plan_digest,
            "goal": pending.goal,
            "source": pending.source.value,
            "idempotency_key": idempotency_key,
        })
        return pending

    def list_pending(self) -> tuple[PendingPlanApproval, ...]:
        """The undecided cards, in journal (registration) order."""
        return tuple(self._pending.values())

    def decide(
        self,
        *,
        request_id: NonEmptyStr,
        candidate_plan_digest: DigestHex,
        decision: ApprovalDecision,
        actor: Any,
        reason: str | None,
        idempotency_key: NonEmptyStr,
    ) -> tuple[PlanApproval, "RunEvent"]:
        """Record the one terminal decision for a pending candidate.

        Fail-closed: with no verifier there is no decision path; an unverifiable
        ``actor`` is refused by the verifier before anything is persisted. On a
        fresh decision the ``decision`` row is appended (fsync) **before**
        ``admission.record_approval`` (persist-then-publish), then the console
        event is emitted. Exactly one terminal decision per candidate: an identical
        re-decide returns the stored pair (self-healing a missing admission call);
        a differing decision raises :class:`ApprovalDecisionConflict`.
        """
        # (6) fail-closed: a coordinator without a verifier cannot decide at all.
        if self._verifier is None:
            raise ApprovalAuthorityError(
                "plan approval is disabled: no verifier is injected (fail closed; "
                "a caller-supplied actor is never accepted as authority)")
        # authenticate the actor BEFORE any state read/write or state disclosure;
        # an unverifiable actor's verify() raises and nothing is persisted.
        principal = self._verifier.verify(actor)
        verified_id = principal.actor

        key = (request_id, candidate_plan_digest)
        existing = self._decisions.get(key)
        if existing is not None:
            if existing.decision is not decision:
                raise ApprovalDecisionConflict(
                    "a different terminal decision already exists for request "
                    f"{request_id!r} / candidate {candidate_plan_digest} "
                    f"(stored {existing.decision.value}, requested {decision.value})")
            # idempotent replay: return the stored pair, healing a missing event.
            return existing, self._ensure_event(existing)

        if key not in self._pending:
            raise UnknownPendingCandidate(
                f"no pending card for request {request_id!r} / candidate "
                f"{candidate_plan_digest}")

        approval = PlanApproval(
            request_id=request_id,
            candidate_plan_digest=candidate_plan_digest,
            decision=decision,
            actor_id=verified_id,
            decided_at=clock_now(self._clock),
            reason=reason,
        )
        # (5) persist-then-publish: the durable decision row is written + fsynced
        # BEFORE the admission event and the console emit.
        self._append("decision", _to_payload(approval))
        self._decisions[key] = approval
        self._pending.pop(key, None)
        event = self._ensure_event(approval)  # admission.record_approval
        self._emit("plan_approval_resolved", {
            "request_id": request_id,
            "candidate_plan_digest": candidate_plan_digest,
            "decision": decision.value,
            "actor_id": verified_id,
            "approval_event_id": getattr(event, "event_id", None),
            "idempotency_key": idempotency_key,
        })
        return approval, event

    def load_decision(
        self, request_id: NonEmptyStr, candidate_plan_digest: DigestHex
    ) -> PlanApproval | None:
        """The durable terminal decision for a candidate, or ``None`` if undecided."""
        return self._decisions.get((request_id, candidate_plan_digest))

    @classmethod
    def replay(
        cls,
        journal_path: Path,
        *,
        admission: "PlanAdmissionService",
        clock: AuthoritativeClock,
        verifier: Any,
        console_emit: Callable[[str, dict], None] | None = None,
    ) -> "PlanApprovalCoordinator":
        """Rebuild a coordinator from the journal and complete missing admissions.

        Construction already folds the journal (torn-tail tolerant, corruption
        hard-failing, diff-binding re-verified). Replay then re-submits each
        terminal decision's ``record_approval`` idempotently, so a decision row
        written before its admission call completed (a crash cut) recovers to one
        terminal admission effect.
        """
        coord = cls(journal_path, admission=admission, clock=clock,
                    verifier=verifier, console_emit=console_emit)
        for key, approval in coord._decisions.items():
            coord._events[key] = coord._ensure_event(approval)
        return coord

    # ------------------------------------------------------------------ #
    # internals                                                          #
    # ------------------------------------------------------------------ #
    def _ensure_event(self, approval: PlanApproval) -> "RunEvent":
        """Idempotently obtain the admission ``RunEvent`` for a terminal decision.

        ``record_approval`` is idempotent by its idempotency key (a real admission
        dedupes against its persisted store), so calling this repeatedly — on a
        same-instance retry or on replay — yields the one terminal effect.
        """
        key = (approval.request_id, approval.candidate_plan_digest)
        cached = self._events.get(key)
        if cached is not None:
            return cached
        submission = ApprovalSubmission(
            request_id=approval.request_id,
            candidate_plan_digest=approval.candidate_plan_digest,
            decision=approval.decision)
        event = self._admission.record_approval(
            approval.candidate_plan_digest, submission,
            authenticated_actor=approval.actor_id,
            idempotency_key=_admission_idempotency_key(
                approval.request_id, approval.candidate_plan_digest, approval.decision))
        self._events[key] = event
        return event

    def _emit(self, event_name: str, payload: dict) -> None:
        """Best-effort console emit; a failure is logged and swallowed (never a
        rollback of the durable, already-published decision)."""
        if self._console_emit is None:
            return
        try:
            self._console_emit(event_name, payload)
        except Exception:  # noqa: BLE001 - console emit is downstream of durability
            _LOG.warning(
                "console_emit(%s) failed; the durable decision is unaffected",
                event_name, exc_info=True)

    # ------------------------------------------------------------------ #
    # journal I/O                                                         #
    # ------------------------------------------------------------------ #
    def _append(self, row_kind: str, payload: dict[str, Any]) -> None:
        """Append one sealed row and fsync it before returning (durability point)."""
        self._seq += 1
        row = ApprovalJournalRow.build(row_kind=row_kind, seq=self._seq, payload=payload)
        line = (row.model_dump_json() + "\n").encode("utf-8")
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with open(self._path, "ab") as f:
            f.write(line)
            f.flush()
            os.fsync(f.fileno())

    def _fold(self) -> None:
        """Rebuild the in-memory state from the journal, fail-closed on corruption.

        Torn (non-JSON) *final* line -> dropped with a warning. Any earlier
        non-JSON line, a ``row_digest`` mismatch, a broken seq chain, an
        unreconstructable payload, a broken card diff-binding, or a duplicate
        contradictory decision -> :class:`ApprovalStoreCorrupt`.
        """
        if not self._path.exists():
            return
        raw = self._path.read_bytes().decode("utf-8")
        if not raw:
            return
        lines = raw.split("\n")
        if lines and lines[-1] == "":
            lines = lines[:-1]  # a clean trailing newline, not a torn tail
        n = len(lines)
        expected_seq = 0
        for idx, line in enumerate(lines):
            is_last = idx == n - 1
            try:
                json.loads(line)
            except json.JSONDecodeError:
                if is_last:
                    _LOG.warning(
                        "dropping torn final journal line in %s (interrupted append)",
                        self._path)
                    break
                raise ApprovalStoreCorrupt(
                    f"malformed (non-JSON) journal line at position {idx} in {self._path}")
            try:
                row = ApprovalJournalRow.model_validate_json(line)
            except ValidationError as exc:
                raise ApprovalStoreCorrupt(
                    f"journal row at position {idx} is not a valid ApprovalJournalRow: {exc}"
                ) from exc
            row.verify()  # row_digest recomputes or ApprovalStoreCorrupt
            expected_seq += 1
            if row.seq != expected_seq:
                raise ApprovalStoreCorrupt(
                    f"journal seq chain broken at position {idx}: expected "
                    f"{expected_seq}, found {row.seq}")
            self._apply_row(row)
            self._seq = row.seq

    def _apply_row(self, row: ApprovalJournalRow) -> None:
        """Fold one verified row into the in-memory state (Task-7b extends here)."""
        if row.row_kind == "pending":
            pending = self._reconstruct_pending(row)
            key = (pending.request_id, pending.candidate_plan_digest)
            # a decision already folded for this key wins (still-decided stays out
            # of _pending); otherwise the card is undecided.
            if key not in self._decisions:
                self._pending[key] = pending
        elif row.row_kind == "decision":
            approval = self._reconstruct_decision(row)
            key = (approval.request_id, approval.candidate_plan_digest)
            prior = self._decisions.get(key)
            if prior is not None and prior.decision is not approval.decision:
                raise ApprovalStoreCorrupt(
                    "the journal holds two contradictory terminal decisions for "
                    f"request {approval.request_id!r} / candidate "
                    f"{approval.candidate_plan_digest}")
            self._decisions[key] = approval
            self._pending.pop(key, None)
        else:  # pragma: no cover - Task 7 vocabulary is closed to pending/decision
            raise ApprovalStoreCorrupt(f"unknown journal row_kind {row.row_kind!r}")

    def _reconstruct_pending(self, row: ApprovalJournalRow) -> PendingPlanApproval:
        try:
            pending = PendingPlanApproval.model_validate_json(json.dumps(row.payload))
        except ValidationError as exc:
            raise ApprovalStoreCorrupt(
                f"pending row seq={row.seq} payload is not a valid "
                f"PendingPlanApproval: {exc}") from exc
        # Re-verify the card's rendered-md diff-binding on fold. The model
        # validator already enforces this equality on reconstruction (so a
        # divergent card fails above); the storage boundary re-asserts it
        # independently as defense-in-depth, catching any future relaxation.
        if pending.rendered_from_diff_digest != pending.plan_diff_ref.payload_ref.content_digest:
            raise ApprovalStoreCorrupt(
                f"pending card seq={row.seq} rendered_from_diff_digest does not bind "
                "its plan_diff_ref payload digest (tampered card)")
        return pending

    def _reconstruct_decision(self, row: ApprovalJournalRow) -> PlanApproval:
        try:
            return PlanApproval.model_validate_json(json.dumps(row.payload))
        except ValidationError as exc:
            raise ApprovalStoreCorrupt(
                f"decision row seq={row.seq} payload is not a valid "
                f"PlanApproval: {exc}") from exc


# --------------------------------------------------------------------------- #
# admit_after_approval — thin pass-through                                     #
# --------------------------------------------------------------------------- #
def admit_after_approval(
    *,
    admission: "PlanAdmissionService",
    candidate_id: NonEmptyStr,
    reservation_id: NonEmptyStr,
    approval_event_id: NonEmptyStr,
    idempotency_key: NonEmptyStr,
) -> "tuple[Plan, PlanAdmitted]":
    """Pass through to ``freeze_and_admit_candidate`` (no wrapper semantics).

    Exists so the console handler and the Task 7b lease-admission path share one
    reviewed freeze/admit call site; the freeze/admit state machine and its
    already-admitted short-circuit stay entirely in the Phase 2 admission service.
    """
    return admission.freeze_and_admit_candidate(
        candidate_id, reservation_id=reservation_id,
        approval_event_id=approval_event_id, idempotency_key=idempotency_key)
