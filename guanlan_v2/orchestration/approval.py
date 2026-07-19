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
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, ClassVar, Literal

from pydantic import ValidationError, model_validator

from guanlan_v2.orchestration.admission import ApprovalSubmission
from guanlan_v2.orchestration.digest import (
    ContractModel,
    DigestHex,
    DigestModel,
    NonEmptyStr,
    NonNegativeInt,
    PositiveInt,
    UtcDateTime,
    content_digest,
)
from guanlan_v2.orchestration.enums import ApprovalDecision
from guanlan_v2.orchestration.events import PlanApproval
from guanlan_v2.orchestration.plan_diff import PendingPlanApproval
from guanlan_v2.orchestration.plan_presets import PlanPresetError
from guanlan_v2.orchestration.refs import LogicalId
from guanlan_v2.orchestration.runtime_clock import AuthoritativeClock, clock_now

if TYPE_CHECKING:  # annotation-only imports (kept off the runtime import graph)
    from guanlan_v2.orchestration.admission import PlanAdmissionService
    from guanlan_v2.orchestration.events import RunEvent
    from guanlan_v2.orchestration.plan_presets import PlanPresetRegistry
    from guanlan_v2.orchestration.runtime_contracts import PlanAdmitted
    from guanlan_v2.orchestration.spec import Plan

__all__ = [
    "ApprovalDecisionConflict",
    "UnknownPendingCandidate",
    "ApprovalStoreCorrupt",
    "ApprovalAuthorityError",
    "ApprovalLeaseError",
    "ApprovalJournalRow",
    "ApprovalLease",
    "LeaseRevocation",
    "LeaseBalanceView",
    "LeaseAdmissionOutcome",
    "PlanApprovalCoordinator",
    "admit_after_approval",
]

_LOG = logging.getLogger(__name__)

#: the actor-id prefix stamped on a lease-authorized :class:`PlanApproval`. A leased
#: decision signs ``lease:<lease_id>`` — the standing authorization, not a human —
#: so an admission can always be traced back to the lease that authorized it.
_LEASE_ACTOR_PREFIX = "lease:"

#: the row-kind vocabulary. Task 7 owned ``pending`` / ``decision``; Task 7b widens
#: this Literal additively (``lease_issued`` / ``lease_consumed`` / ``lease_revoked``)
#: — the fold loop dispatches on ``row_kind`` so a new kind is pure addition, never a
#: rewrite of the two Task 7 branches.
ApprovalRowKind = Literal[
    "pending", "decision", "lease_issued", "lease_consumed", "lease_revoked"
]


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


class ApprovalLeaseError(Exception):
    """A lease cannot be issued or revoked as asked (never a silent no-op).

    Raised by :meth:`PlanApprovalCoordinator.issue_lease` when the lease channel
    is unwired (no sealed preset registry / no current chain digests), when the
    named preset does not resolve in the sealed registry, or when the requested
    lease digests do not equal the current chain digests (a drifted attestation is
    refused at issue time, never bound). Raised by
    :meth:`PlanApprovalCoordinator.revoke_lease` when the lease id is unknown. This
    is distinct from :class:`ApprovalAuthorityError` (no verifier at all) and from
    the verifier's own refusal of an unverifiable actor.
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
# ApprovalLease — a sealed, digest-bound standing approval                     #
# --------------------------------------------------------------------------- #
class ApprovalLease(DigestModel):
    """A human-issued, bounded standing approval for one attested preset.

    A lease is the standing authorization that lets an unattended preset run
    (daily Lane 0, an interval replay) admit *without* a fresh per-instance human
    decision — but only within hard envelopes. It binds the exact preset the human
    reviewed (``preset_id`` + ``preset_record_digest``) **and** the exact
    chain it was reviewed against (``catalog_digest`` + ``registry_digest``), so a
    lease can never admit a candidate that drifted onto a newer catalog/registry or
    a re-reviewed preset — a stale lease simply stops matching.

    ``lease_id`` is the service-derived ``content_digest`` over the lease's own
    issue content (every field except the wall-clock ``issued_at`` and the
    self-referential ``lease_id``), never caller-chosen; two issue requests with
    identical content therefore collide on ``lease_id`` and the coordinator treats
    the second as an idempotent replay. ``issued_by`` is the *verified* actor id;
    the window ``valid_from < valid_until`` and both envelopes (``max_admissions``,
    ``budget_cap_llm_invocations``) are enforced at admission time by the
    coordinator, folded from the journal.

    Registered-to-be by Task 9 (``ApprovalLease@1``); it exists here unregistered.
    """

    schema_version: Literal["1"] = "1"
    lease_id: DigestHex
    purpose: NonEmptyStr
    preset_id: LogicalId
    preset_record_digest: DigestHex
    catalog_digest: DigestHex
    registry_digest: DigestHex
    valid_from: UtcDateTime
    valid_until: UtcDateTime
    max_admissions: PositiveInt
    budget_cap_llm_invocations: PositiveInt
    issued_by: NonEmptyStr
    issued_at: UtcDateTime
    reason: NonEmptyStr

    #: ``issued_at`` is audit wall-clock; ``lease_id`` is this object's own digest.
    #: Both are excluded from the semantic projection, so ``lease_id`` binds exactly
    #: the issue content and is reproducible from it.
    SEMANTIC_EXCLUDE: ClassVar[frozenset[str]] = frozenset({"issued_at"})
    SELF_DIGEST_FIELDS: ClassVar[frozenset[str]] = frozenset({"lease_id"})

    @model_validator(mode="after")
    def _verify(self) -> "ApprovalLease":
        if self.valid_until <= self.valid_from:
            raise ValueError("valid_until must be strictly after valid_from")
        expected = self.semantic_digest()
        if self.lease_id != expected:
            raise ValueError(
                "lease_id must equal the content digest of the issue content "
                f"(declared {self.lease_id!r}, computed {expected!r})")
        return self

    @classmethod
    def issue(
        cls,
        *,
        purpose: NonEmptyStr,
        preset_id: LogicalId,
        preset_record_digest: DigestHex,
        catalog_digest: DigestHex,
        registry_digest: DigestHex,
        valid_from: UtcDateTime,
        valid_until: UtcDateTime,
        max_admissions: PositiveInt,
        budget_cap_llm_invocations: PositiveInt,
        issued_by: NonEmptyStr,
        issued_at: UtcDateTime,
        reason: NonEmptyStr,
    ) -> "ApprovalLease":
        """Seal a lease: derive ``lease_id`` from the issue content, then construct.

        Uses :meth:`DigestModel.digest_of_fields` to compute the digest over every
        field except the self-referential ``lease_id`` and the audit ``issued_at``,
        breaking the self-reference cycle; the model validator re-verifies it.
        """
        fields = dict(
            purpose=purpose, preset_id=preset_id,
            preset_record_digest=preset_record_digest, catalog_digest=catalog_digest,
            registry_digest=registry_digest, valid_from=valid_from,
            valid_until=valid_until, max_admissions=max_admissions,
            budget_cap_llm_invocations=budget_cap_llm_invocations,
            issued_by=issued_by, issued_at=issued_at, reason=reason)
        lease_id = cls.digest_of_fields(projection="semantic", **fields)
        return cls(lease_id=lease_id, **fields)


class LeaseRevocation(ContractModel):
    """The durable record of a lease revocation (a ``lease_revoked`` row payload)."""

    schema_version: Literal["1"] = "1"
    lease_id: DigestHex
    actor_id: NonEmptyStr
    reason: NonEmptyStr
    revoked_at: UtcDateTime


class _LeaseConsumeRow(ContractModel):
    """The payload of a ``lease_consumed`` row: one admission drawn against a lease.

    ``consume_seq`` is 1-based per lease (the k-th consume against that lease id);
    the fold enforces monotonic continuity so a duplicated / re-ordered consume is
    caught. ``budget_consumed`` is the candidate's requested llm-invocation budget,
    summed across consumes to fold the remaining budget cap.
    """

    schema_version: Literal["1"] = "1"
    lease_id: DigestHex
    consume_seq: PositiveInt
    request_id: NonEmptyStr
    candidate_plan_digest: DigestHex
    budget_consumed: NonNegativeInt


@dataclass(frozen=True)
class LeaseBalanceView:
    """A lease plus its journal-folded balances and terminal status, at one instant.

    ``status`` is ``active`` unless the lease is (in precedence order) ``revoked``,
    ``expired`` (``now >= valid_until``) or ``exhausted`` (no admissions or no
    budget left). ``terminal_reason`` carries the human-readable cause and is
    ``None`` only when active. A terminal lease is *displayed*, never deleted.
    """

    lease: ApprovalLease
    status: Literal["active", "expired", "exhausted", "revoked"]
    terminal_reason: str | None
    admissions_used: int
    admissions_remaining: int
    budget_used: int
    budget_remaining: int
    revocation: LeaseRevocation | None = None


@dataclass(frozen=True)
class LeaseAdmissionOutcome:
    """The result of :meth:`PlanApprovalCoordinator.register_and_try_lease`.

    ``outcome`` is ``lease_admitted`` (a real digest-bound :class:`PlanApproval`
    and its admission ``RunEvent`` are attached) or ``pending_human`` (the card was
    registered and simply stays pending for a human — never an error, never a
    silent admit).
    """

    outcome: Literal["lease_admitted", "pending_human"]
    lease_id: str | None = None
    approval: PlanApproval | None = None
    event: "RunEvent | None" = None


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
        approvals_sink: "Callable[[PlanApproval], None] | None" = None,
        preset_registry: "PlanPresetRegistry | None" = None,
        catalog_digest: DigestHex | None = None,
        registry_digest: DigestHex | None = None,
    ) -> None:
        self._path = Path(journal_path)
        self._admission = admission
        self._clock = clock
        self._verifier = verifier
        self._console_emit = console_emit
        # Task 8 (console carrier) additive seam. The coordinator constructs the ONE
        # authoritative PlanApproval; the Phase-2 admission service (admission.py)
        # never trusts a caller-carried authority — its ``record_approval`` LOADS the
        # PlanApproval from its own service-owned ``_approvals`` store. This sink is
        # the reviewed bridge that deposits the freshly-constructed, verified approval
        # into that store *after* the durable decision row and *before* the
        # record_approval call (see ``_record_terminal_decision``), so the loaded
        # authority equals the durable decision. ``None`` (the Task 7/7b default) keeps
        # the coordinator's behaviour byte-identical.
        self._approvals_sink = approvals_sink
        # lease-issuance wiring (all optional; unwired -> honest issuance refusal,
        # mirroring the verifier=None fail-closed pattern). ``catalog_digest`` /
        # ``registry_digest`` are the CURRENT sealed chain digests a fresh lease must
        # bind; a mismatch at issue time is a drift refusal.
        self._preset_registry = preset_registry
        self._catalog_digest = catalog_digest
        self._registry_digest = registry_digest
        # in-memory fold, rebuilt from the journal on every construction.
        self._seq = 0
        self._pending: dict[tuple[str, str], PendingPlanApproval] = {}
        self._decisions: dict[tuple[str, str], PlanApproval] = {}
        # decision -> RunEvent, populated only once an admission call completes.
        self._events: dict[tuple[str, str], "RunEvent"] = {}
        # lease fold state, all rebuilt from the journal.
        self._leases: dict[str, ApprovalLease] = {}
        self._revocations: dict[str, LeaseRevocation] = {}
        # per-lease consume rows, in journal order (balances fold from these).
        self._consumes: dict[str, list[_LeaseConsumeRow]] = {}
        # (request_id, candidate_plan_digest) -> its one consume (a candidate is
        # consumed at most once; the guard that makes admission idempotent).
        self._consumed: dict[tuple[str, str], _LeaseConsumeRow] = {}
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

        approval, event, fresh = self._record_terminal_decision(
            request_id=request_id, candidate_plan_digest=candidate_plan_digest,
            decision=decision, actor_id=verified_id, reason=reason)
        if fresh:
            self._emit("plan_approval_resolved", {
                "request_id": request_id,
                "candidate_plan_digest": candidate_plan_digest,
                "decision": decision.value,
                "actor_id": verified_id,
                "approval_event_id": getattr(event, "event_id", None),
                "idempotency_key": idempotency_key,
            })
        return approval, event

    def _record_terminal_decision(
        self,
        *,
        request_id: str,
        candidate_plan_digest: str,
        decision: ApprovalDecision,
        actor_id: str,
        reason: str | None,
    ) -> tuple[PlanApproval, "RunEvent", bool]:
        """Record (or idempotently re-serve) the one terminal decision for a card.

        The authority-free core shared by the human :meth:`decide` path (which
        authenticates a verifier first, then stamps the verified actor) and the
        lease path (which stamps ``lease:<lease_id>`` — the standing authorization
        is the authority, so no verifier is consulted at consume time). Returns
        ``(approval, event, fresh)``: ``fresh`` is ``True`` only when a new decision
        row was written this call, so a caller emits its console event exactly once.

        Ordering is persist-then-publish, identical to Task 7: the ``decision`` row
        is appended + fsynced BEFORE ``record_approval``. An identical re-decide
        returns the stored pair (healing a missing admission event); a differing
        decision raises :class:`ApprovalDecisionConflict`; an unknown card raises
        :class:`UnknownPendingCandidate`.
        """
        key = (request_id, candidate_plan_digest)
        existing = self._decisions.get(key)
        if existing is not None:
            if existing.decision is not decision:
                raise ApprovalDecisionConflict(
                    "a different terminal decision already exists for request "
                    f"{request_id!r} / candidate {candidate_plan_digest} "
                    f"(stored {existing.decision.value}, requested {decision.value})")
            # idempotent replay: return the stored pair, healing a missing event.
            return existing, self._ensure_event(existing), False

        if key not in self._pending:
            raise UnknownPendingCandidate(
                f"no pending card for request {request_id!r} / candidate "
                f"{candidate_plan_digest}")

        approval = PlanApproval(
            request_id=request_id,
            candidate_plan_digest=candidate_plan_digest,
            decision=decision,
            actor_id=actor_id,
            decided_at=clock_now(self._clock),
            reason=reason,
        )
        # (5) persist-then-publish: the durable decision row is written + fsynced
        # BEFORE the admission event and the console emit.
        self._append("decision", _to_payload(approval))
        self._decisions[key] = approval
        self._pending.pop(key, None)
        # deposit the authoritative approval into the admission-owned store BEFORE
        # record_approval loads it (Task 8 seam; a no-op when no sink is wired). This
        # sits AFTER the durable decision row so persist-then-publish still holds:
        # a crash between the row and the sink recovers via replay's idempotent
        # record_approval resubmission (the sink is re-invoked on that path too).
        if self._approvals_sink is not None:
            self._approvals_sink(approval)
        event = self._ensure_event(approval)  # admission.record_approval
        return approval, event, True

    def load_decision(
        self, request_id: NonEmptyStr, candidate_plan_digest: DigestHex
    ) -> PlanApproval | None:
        """The durable terminal decision for a candidate, or ``None`` if undecided."""
        return self._decisions.get((request_id, candidate_plan_digest))

    # ------------------------------------------------------------------ #
    # lease channel (Task 7b) — bounded standing approvals               #
    # ------------------------------------------------------------------ #
    def issue_lease(
        self,
        *,
        purpose: NonEmptyStr,
        preset_id: LogicalId,
        preset_record_digest: DigestHex,
        catalog_digest: DigestHex,
        registry_digest: DigestHex,
        valid_from: UtcDateTime,
        valid_until: UtcDateTime,
        max_admissions: PositiveInt,
        budget_cap_llm_invocations: PositiveInt,
        actor: Any,
        reason: NonEmptyStr,
    ) -> ApprovalLease:
        """Issue a bounded standing approval for an attested preset (verified human).

        Fail-closed like :meth:`decide`: with no verifier there is no issuance path;
        an unverifiable ``actor`` is refused by the verifier before anything is
        persisted. The named ``preset_id`` must resolve in the sealed preset
        registry, and the requested ``preset_record_digest`` / ``catalog_digest`` /
        ``registry_digest`` must each equal the current chain digests — a drifted
        attestation is refused (:class:`ApprovalLeaseError`), never bound. On
        success the ``lease_issued`` row is appended + fsynced BEFORE the console
        emit. Idempotent by issue-content digest: an identical re-issue returns the
        stored lease and appends no row.
        """
        # (6) fail-closed: no verifier -> no issuance authority at all.
        if self._verifier is None:
            raise ApprovalAuthorityError(
                "lease issuance is disabled: no verifier is injected (fail closed)")
        principal = self._verifier.verify(actor)
        issued_by = principal.actor

        if (self._preset_registry is None or self._catalog_digest is None
                or self._registry_digest is None):
            raise ApprovalLeaseError(
                "lease issuance is unwired: a sealed preset registry and the current "
                "catalog/registry chain digests must be supplied to the coordinator")
        # the preset must resolve in the sealed registry (never a silent substitute).
        try:
            record = self._preset_registry.get(preset_id)
        except PlanPresetError as exc:
            raise ApprovalLeaseError(
                f"cannot issue a lease for unknown preset {preset_id!r}") from exc
        # the lease digests must equal the current chain digests (drift refusal).
        if preset_record_digest != record.semantic_digest():
            raise ApprovalLeaseError(
                f"preset_record_digest does not match the current record of preset "
                f"{preset_id!r} (a re-reviewed preset needs a fresh lease)")
        if catalog_digest != self._catalog_digest:
            raise ApprovalLeaseError(
                "catalog_digest does not match the current sealed catalog "
                "(a drifted chain needs a fresh lease)")
        if registry_digest != self._registry_digest:
            raise ApprovalLeaseError(
                "registry_digest does not match the current sealed registry "
                "(a drifted chain needs a fresh lease)")

        lease = ApprovalLease.issue(
            purpose=purpose, preset_id=preset_id,
            preset_record_digest=preset_record_digest, catalog_digest=catalog_digest,
            registry_digest=registry_digest, valid_from=valid_from,
            valid_until=valid_until, max_admissions=max_admissions,
            budget_cap_llm_invocations=budget_cap_llm_invocations,
            issued_by=issued_by, issued_at=clock_now(self._clock), reason=reason)

        stored = self._leases.get(lease.lease_id)
        if stored is not None:
            return stored  # idempotent by issue-content digest; no second row.
        self._append("lease_issued", _to_payload(lease))
        self._leases[lease.lease_id] = lease
        self._emit("plan_lease_issued", {
            "lease_id": lease.lease_id,
            "preset_id": lease.preset_id,
            "issued_by": issued_by,
            "valid_from": lease.valid_from.isoformat(),
            "valid_until": lease.valid_until.isoformat(),
            "max_admissions": lease.max_admissions,
            "budget_cap_llm_invocations": lease.budget_cap_llm_invocations,
        })
        return lease

    def list_leases(self, *, now: UtcDateTime) -> tuple[LeaseBalanceView, ...]:
        """Every lease (active + terminal) with journal-folded balances, at ``now``.

        A terminal lease (revoked / expired / exhausted) is displayed with its
        reason, never deleted. Ordered by ``lease_id`` for determinism.
        """
        views = [self._lease_view(lease, now=now)
                 for lease in self._leases.values()]
        views.sort(key=lambda v: v.lease.lease_id)
        return tuple(views)

    def revoke_lease(
        self,
        lease_id: DigestHex,
        *,
        actor: Any,
        reason: NonEmptyStr,
        idempotency_key: NonEmptyStr,
    ) -> LeaseRevocation:
        """Revoke a lease: verifier-gated, immediate, durable, idempotent.

        Fail-closed like :meth:`decide`. An unknown ``lease_id`` is an
        :class:`ApprovalLeaseError`. A first revocation appends a ``lease_revoked``
        row (fsync) then emits; an already-revoked lease returns the STORED
        revocation and appends no second row (idempotent replay).
        """
        if self._verifier is None:
            raise ApprovalAuthorityError(
                "lease revocation is disabled: no verifier is injected (fail closed)")
        principal = self._verifier.verify(actor)
        actor_id = principal.actor

        if lease_id not in self._leases:
            raise ApprovalLeaseError(f"cannot revoke unknown lease {lease_id!r}")
        stored = self._revocations.get(lease_id)
        if stored is not None:
            return stored  # idempotent: the first revocation stands.

        revocation = LeaseRevocation(
            lease_id=lease_id, actor_id=actor_id, reason=reason,
            revoked_at=clock_now(self._clock))
        self._append("lease_revoked", _to_payload(revocation))
        self._revocations[lease_id] = revocation
        self._emit("plan_lease_revoked", {
            "lease_id": lease_id, "actor_id": actor_id, "reason": reason})
        return revocation

    def register_and_try_lease(
        self,
        pending: PendingPlanApproval,
        *,
        idempotency_key: NonEmptyStr,
        now: UtcDateTime,
        candidate_catalog_digest: DigestHex,
        candidate_registry_digest: DigestHex,
    ) -> LeaseAdmissionOutcome:
        """Register the pending card, then admit it under a lease if one matches.

        The card is always registered (Task 7 :meth:`register_pending` semantics,
        unchanged). It is then admitted **iff** it has preset provenance and an
        active lease matches ``(preset_id, preset_record_digest, catalog_digest,
        registry_digest)`` exactly, ``valid_from <= now < valid_until``, admissions
        remain and the plan's requested budget fits the remaining cap. The caller
        supplies the candidate's own ``catalog_digest`` / ``registry_digest`` (they
        live on the underlying PlanDraft, not the card) so a candidate that drifted
        onto a newer chain refuses to match a stale lease.

        On a match: the ``lease_consumed`` row is appended FIRST, then a real
        APPROVED :class:`PlanApproval` (actor ``lease:<lease_id>``) is recorded
        through the shared decide internals, then ``plan_lease_admitted`` is
        emitted. Any failing condition -> ``pending_human`` (never an error, never a
        silent admit). Idempotent: a re-delivery whose candidate was already
        consumed admits once (returns the stored / healed decision), never
        double-consuming.
        """
        key = (pending.request_id, pending.candidate_plan_digest)

        # idempotency 1: the candidate already has a terminal decision.
        existing = self._decisions.get(key)
        if existing is not None:
            if existing.actor_id.startswith(_LEASE_ACTOR_PREFIX):
                return LeaseAdmissionOutcome(
                    outcome="lease_admitted",
                    lease_id=existing.actor_id[len(_LEASE_ACTOR_PREFIX):],
                    approval=existing, event=self._ensure_event(existing))
            # a human already decided this card; the lease yields to that decision.
            return LeaseAdmissionOutcome(outcome="pending_human")

        # register the card exactly as Task 7 does (idempotent by identity).
        self.register_pending(pending, idempotency_key=idempotency_key)

        # idempotency 2: the candidate was already consumed but not yet decided
        # (a crash between lease_consumed and the decision row) -> complete it
        # without a second consume.
        consumed = self._consumed.get(key)
        if consumed is not None:
            return self._admit_under_lease(pending, consumed.lease_id)

        # fresh: find an admissible lease (identity + window + envelopes).
        lease = self._find_admissible_lease(
            pending, now=now, candidate_catalog_digest=candidate_catalog_digest,
            candidate_registry_digest=candidate_registry_digest)
        if lease is None:
            return LeaseAdmissionOutcome(outcome="pending_human")

        # (2) append lease_consumed FIRST (durable envelope draw before the decision).
        consume = _LeaseConsumeRow(
            lease_id=lease.lease_id,
            consume_seq=len(self._consumes.get(lease.lease_id, ())) + 1,
            request_id=pending.request_id,
            candidate_plan_digest=pending.candidate_plan_digest,
            budget_consumed=pending.budget_request_llm_invocations)
        self._append("lease_consumed", _to_payload(consume))
        self._consumes.setdefault(lease.lease_id, []).append(consume)
        self._consumed[key] = consume
        return self._admit_under_lease(pending, lease.lease_id)

    def _admit_under_lease(
        self, pending: PendingPlanApproval, lease_id: str
    ) -> LeaseAdmissionOutcome:
        """Record the APPROVED leased decision and emit; idempotent on the decision.

        Stamps ``actor_id=lease:<lease_id>`` through the shared, verifier-free
        :meth:`_record_terminal_decision` (the lease IS the standing authority), so
        a leased admission is a real journal ``decision`` row + real
        ``record_approval`` + real event — never an admission bypass.
        """
        lease = self._leases.get(lease_id)
        approval, event, fresh = self._record_terminal_decision(
            request_id=pending.request_id,
            candidate_plan_digest=pending.candidate_plan_digest,
            decision=ApprovalDecision.APPROVED,
            actor_id=f"{_LEASE_ACTOR_PREFIX}{lease_id}",
            reason=lease.reason if lease is not None else None)
        if fresh:
            self._emit("plan_lease_admitted", {
                "lease_id": lease_id,
                "request_id": pending.request_id,
                "candidate_plan_digest": pending.candidate_plan_digest,
                "actor_id": approval.actor_id,
                "approval_event_id": getattr(event, "event_id", None),
            })
        return LeaseAdmissionOutcome(
            outcome="lease_admitted", lease_id=lease_id, approval=approval,
            event=event)

    def _find_admissible_lease(
        self,
        pending: PendingPlanApproval,
        *,
        now: UtcDateTime,
        candidate_catalog_digest: DigestHex,
        candidate_registry_digest: DigestHex,
    ) -> ApprovalLease | None:
        """The first active lease that matches identity + window + envelopes, or None.

        A candidate with no preset provenance (a DYNAMIC card) structurally never
        matches. A lease that is revoked, outside its window, out of admissions, or
        without enough remaining budget for this candidate is skipped — so any
        envelope crossing yields ``None`` and the caller falls back to a human.
        """
        # (1) DYNAMIC / non-preset candidates carry no provenance -> never match.
        if pending.preset_id is None or pending.preset_record_digest is None:
            return None
        for lease in sorted(self._leases.values(), key=lambda le: le.lease_id):
            if lease.lease_id in self._revocations:
                continue
            if not (lease.valid_from <= now < lease.valid_until):
                continue
            if (lease.preset_id != pending.preset_id
                    or lease.preset_record_digest != pending.preset_record_digest
                    or lease.catalog_digest != candidate_catalog_digest
                    or lease.registry_digest != candidate_registry_digest):
                continue
            used = self._consumes.get(lease.lease_id, [])
            if len(used) >= lease.max_admissions:
                continue
            budget_used = sum(c.budget_consumed for c in used)
            if pending.budget_request_llm_invocations > (
                    lease.budget_cap_llm_invocations - budget_used):
                continue
            return lease
        return None

    def _lease_view(self, lease: ApprovalLease, *, now: UtcDateTime) -> LeaseBalanceView:
        """Fold one lease's balances + terminal status at ``now``."""
        used_rows = self._consumes.get(lease.lease_id, [])
        admissions_used = len(used_rows)
        budget_used = sum(c.budget_consumed for c in used_rows)
        admissions_remaining = max(0, lease.max_admissions - admissions_used)
        budget_remaining = max(0, lease.budget_cap_llm_invocations - budget_used)

        revocation = self._revocations.get(lease.lease_id)
        status: Literal["active", "expired", "exhausted", "revoked"]
        reason: str | None
        if revocation is not None:
            status, reason = "revoked", f"revoked by {revocation.actor_id}: {revocation.reason}"
        elif now >= lease.valid_until:
            status, reason = "expired", "valid_until elapsed"
        elif admissions_remaining <= 0:
            status, reason = "exhausted", "max_admissions reached"
        elif budget_remaining <= 0:
            status, reason = "exhausted", "budget cap reached"
        else:
            status, reason = "active", None
        return LeaseBalanceView(
            lease=lease, status=status, terminal_reason=reason,
            admissions_used=admissions_used,
            admissions_remaining=admissions_remaining,
            budget_used=budget_used, budget_remaining=budget_remaining,
            revocation=revocation)

    @classmethod
    def replay(
        cls,
        journal_path: Path,
        *,
        admission: "PlanAdmissionService",
        clock: AuthoritativeClock,
        verifier: Any,
        console_emit: Callable[[str, dict], None] | None = None,
        approvals_sink: "Callable[[PlanApproval], None] | None" = None,
        preset_registry: "PlanPresetRegistry | None" = None,
        catalog_digest: DigestHex | None = None,
        registry_digest: DigestHex | None = None,
    ) -> "PlanApprovalCoordinator":
        """Rebuild a coordinator from the journal and complete missing admissions.

        Construction already folds the journal (torn-tail tolerant, corruption
        hard-failing, diff-binding re-verified, lease balances reconstructed).
        Replay then re-submits each terminal decision's ``record_approval``
        idempotently, so a decision row written before its admission call completed
        (a crash cut) recovers to one terminal admission effect. This includes
        leased decisions (whose actor is ``lease:<id>``): a lease admission is a
        real decision row, so it heals exactly like a human one — the lease only
        changed who signed. A candidate consumed but never decided (a crash between
        the ``lease_consumed`` row and its ``decision`` row) is left pending for the
        live :meth:`register_and_try_lease` retry to complete, which reuses the
        durable consume (no double-consume) — replay itself never writes a journal
        row.
        """
        coord = cls(journal_path, admission=admission, clock=clock,
                    verifier=verifier, console_emit=console_emit,
                    approvals_sink=approvals_sink, preset_registry=preset_registry,
                    catalog_digest=catalog_digest, registry_digest=registry_digest)
        for key, approval in coord._decisions.items():
            # re-deposit each folded approval into the admission store before its
            # idempotent record_approval resubmission, so recovery heals even when the
            # admission's own approvals store did not survive the restart.
            if coord._approvals_sink is not None:
                coord._approvals_sink(approval)
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
        elif row.row_kind == "lease_issued":
            lease = self._reconstruct_lease(row)
            prior = self._leases.get(lease.lease_id)
            # lease_id IS the content digest, so a same-id row is byte-identical
            # content; a mismatch means tamper.
            if prior is not None and prior.semantic_digest() != lease.semantic_digest():
                raise ApprovalStoreCorrupt(
                    f"lease_issued seq={row.seq} reuses lease_id {lease.lease_id} "
                    "with different content")
            self._leases[lease.lease_id] = lease
        elif row.row_kind == "lease_consumed":
            consume = self._reconstruct_consume(row)
            if consume.lease_id not in self._leases:
                raise ApprovalStoreCorrupt(
                    f"lease_consumed seq={row.seq} names unknown lease "
                    f"{consume.lease_id}")
            prior_seq = len(self._consumes.get(consume.lease_id, ()))
            if consume.consume_seq != prior_seq + 1:
                raise ApprovalStoreCorrupt(
                    f"lease_consumed seq={row.seq} has non-monotonic consume_seq "
                    f"(expected {prior_seq + 1}, found {consume.consume_seq})")
            ckey = (consume.request_id, consume.candidate_plan_digest)
            if ckey in self._consumed:
                raise ApprovalStoreCorrupt(
                    "the journal consumes one candidate twice for request "
                    f"{consume.request_id!r} / candidate {consume.candidate_plan_digest}")
            self._consumes.setdefault(consume.lease_id, []).append(consume)
            self._consumed[ckey] = consume
        elif row.row_kind == "lease_revoked":
            revocation = self._reconstruct_revocation(row)
            if revocation.lease_id not in self._leases:
                raise ApprovalStoreCorrupt(
                    f"lease_revoked seq={row.seq} names unknown lease "
                    f"{revocation.lease_id}")
            # the first revocation stands; a later row (idempotent replay never
            # writes one) keeps the earliest.
            self._revocations.setdefault(revocation.lease_id, revocation)
        else:  # pragma: no cover - the row_kind Literal is closed to the 5 kinds
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

    def _reconstruct_lease(self, row: ApprovalJournalRow) -> ApprovalLease:
        # ApprovalLease's own validator re-verifies lease_id == content digest, so
        # a tampered lease payload fails here.
        try:
            return ApprovalLease.model_validate_json(json.dumps(row.payload))
        except ValidationError as exc:
            raise ApprovalStoreCorrupt(
                f"lease_issued row seq={row.seq} payload is not a valid "
                f"ApprovalLease: {exc}") from exc

    def _reconstruct_consume(self, row: ApprovalJournalRow) -> _LeaseConsumeRow:
        try:
            return _LeaseConsumeRow.model_validate_json(json.dumps(row.payload))
        except ValidationError as exc:
            raise ApprovalStoreCorrupt(
                f"lease_consumed row seq={row.seq} payload is not a valid "
                f"consume record: {exc}") from exc

    def _reconstruct_revocation(self, row: ApprovalJournalRow) -> LeaseRevocation:
        try:
            return LeaseRevocation.model_validate_json(json.dumps(row.payload))
        except ValidationError as exc:
            raise ApprovalStoreCorrupt(
                f"lease_revoked row seq={row.seq} payload is not a valid "
                f"LeaseRevocation: {exc}") from exc


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
