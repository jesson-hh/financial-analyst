# -*- coding: utf-8 -*-
"""Phase 3 · Task 9 — the proposal-only mutation boundary.

Workers submit PROPOSALS only; the existing reviewed writers remain the sole
accepted-mutation paths (Global Constraint line 24):

* agent pending files are written by the EXISTING
  ``financial_analyst.dream.proposal_writer`` (its Task-9 safe pending API);
* agent exact application is owned by the EXISTING
  ``financial_analyst.memory_ops`` (its Task-9 decision-backed
  ``AgentMemoryFileCoordinator``) — this module passes a primitive immutable
  command and projects the public semantic receipt from the verified durable
  owner result;
* console pending proposals journal through the EXISTING public
  ``ConsoleStore.append_event`` interface, and console application DELEGATES to
  the EXISTING reviewed writer ``guanlan_v2.console.tools.memory_write_impl``.

# DEFERRED(console-lifecycle): the console-INTERNAL half of the brief —
# ConsoleRootCoordinator, ``normalize_console_memory_write``-enforcing exact
# writer with apply-start/applied journal events + provenance pins, marker-aware
# curator recovery and verifier-injected console API routes — is deferred to the
# named coordination-window follow-up (the concurrent session owns
# ``guanlan_v2/console/*``; coordinator Decision 1, 2026-07-17). Until it lands,
# a console approval applies through ``memory_write_impl`` WITHOUT the exact
# marker, so conservative capture keeps the applied line PENDING — honest,
# fail-safe, and never a second accepted-write path.

Exactly-once: ``submit_proposal`` is idempotent (same key + same semantic
request returns the persisted refs; drift conflicts) via the registered
``MemoryProposalPreparation@1`` + the sealed ``memory.proposal_preparation.v1``
result cell. ``MemoryDecisionRepository.decide_once`` keys the terminal
decision by proposal content identity — one terminal Decision per proposal,
persisted BEFORE any owner side effect.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Protocol, runtime_checkable

from guanlan_v2.orchestration.digest import content_digest
from guanlan_v2.orchestration.memory.models import (
    AgentMemoryProposalTarget,
    AgentPendingLocator,
    AuthenticatedAdminPrincipal,
    ConsoleMemoryProposalTarget,
    ConsolePendingEventLocator,
    MemoryAuthorityError,
    MemoryConflictError,
    MemoryContractError,
    MemoryFacadeDescriptor,
    MemoryOwnerApplySemanticReceipt,
    MemoryProposal,
    MemoryProposalDecision,
    MemoryProposalPreparation,
    MemoryProposalReceipt,
    MemoryProposalRequest,
    _target_logical_identity,
    build_apply_marker_id,
    build_memory_proposal,
    normalize_console_memory_write,
)
from guanlan_v2.orchestration.memory.store import _key_digest, _MemoryRepositoryBase
from guanlan_v2.orchestration.refs import SchemaRef, TypedPayloadRef
from guanlan_v2.orchestration.runtime_clock import clock_now

__all__ = [
    "AdminReviewVerifier",
    "AppliedEvidenceBundle",
    "MemoryProposalSubmissionRepository",
    "MemoryDecisionRepository",
    "MemoryProposalService",
    "build_agent_marker_line",
    "sha256_unit_digest",
]

_SR = lambda name: SchemaRef(name=name, version="1")  # noqa: E731


@runtime_checkable
class AdminReviewVerifier(Protocol):
    """Fail-closed injected admin authentication — never a body field."""

    def verify(self, credential: Any) -> AuthenticatedAdminPrincipal:
        ...


def sha256_unit_digest(text: str) -> str:
    """The owner-side complete-unit digest (CRLF-folded sha256 hex)."""
    import hashlib

    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def build_agent_marker_line(
    *, marker_id: str, request_digest: str, payload_digest: str
) -> str:
    """The one closed ASCII marker grammar (first line of an applied unit)."""
    return (
        f"<!-- guanlan-memory-apply-v1 marker_id={marker_id} "
        f"request_digest={request_digest} payload_digest={payload_digest} -->"
    )


# --------------------------------------------------------------------------- #
# repositories (namespace family: proposal)                                    #
# --------------------------------------------------------------------------- #
class MemoryProposalSubmissionRepository(_MemoryRepositoryBase):
    """Exactly-once proposal preparation over ``memory.proposal_preparation.v1``."""

    OWNER = "proposal"
    OWNED = frozenset({"memory.proposal_preparation.v1"})

    def load_result(self, preparation_key: str) -> TypedPayloadRef | None:
        return self._cell("memory.proposal_preparation.v1",
                          _key_digest("memory.proposal.preparation", preparation_key))

    def prepare_once(
        self,
        preparation_key: str,
        build: Callable[[], MemoryProposalPreparation],
    ) -> tuple[MemoryProposalPreparation, TypedPayloadRef]:
        """Load-or-freeze the typed preparation BEFORE any clock/store read on
        the retry path; same-key semantic drift conflicts."""
        existing = self.load_result(preparation_key)
        if existing is not None:
            prep = self._resolve(existing, "MemoryProposalPreparation")
            if prep.preparation_key != preparation_key:
                raise MemoryConflictError("recovered preparation binds a different key")
            return prep, existing
        prep = build()  # clock/store reads happen exactly once, on the absent path
        if prep.preparation_key != preparation_key:
            raise MemoryConflictError("built preparation does not bind the key")
        ref = self._put_and_cas(
            batch_key=f"mem-prop-prep:{preparation_key}",
            schema="MemoryProposalPreparation",
            payload=prep,
            put_key=f"mem-prop-prep-put:{preparation_key}",
            cas=(("memory.proposal_preparation.v1",
                  _key_digest("memory.proposal.preparation", preparation_key), None),),
        )
        return prep, ref


class MemoryDecisionRepository(_MemoryRepositoryBase):
    """One repository-enforced terminal Decision per proposal content identity."""

    OWNER = "proposal"
    OWNED = frozenset({"memory.proposal_preparation.v1"})

    def load(self, proposal_ref: TypedPayloadRef) -> TypedPayloadRef | None:
        return self._cell(
            "memory.proposal_preparation.v1",
            _key_digest("memory.proposal.decision",
                        proposal_ref.payload_ref.content_digest))

    def decide_once(
        self,
        *,
        proposal: MemoryProposal,
        proposal_ref: TypedPayloadRef,
        receipt_ref: TypedPayloadRef,
        decision: str,
        principal: AuthenticatedAdminPrincipal,
        reason: str,
        decided_at: datetime,
    ) -> TypedPayloadRef:
        """Atomically persist/recover THE terminal decision for one proposal.

        Keyed by proposal content identity, not a caller idempotency key: the
        same proposal + identical semantics returns the one stored Decision ref
        even under a different retry; any changed actor/reason/digest or an
        ``approved``↔``rejected`` race conflicts. Persistence precedes every
        owner side effect.
        """
        candidate = MemoryProposalDecision(
            proposal_ref=proposal_ref,
            receipt_ref=receipt_ref,
            expected_before_target_store_digest=proposal.expected_before_target_store_digest,
            intended_after_target_store_digest=proposal.intended_after_target_store_digest,
            expected_before_container_digest=proposal.expected_before_container_digest,
            intended_after_container_digest=proposal.intended_after_container_digest,
            decision=decision,
            actor=principal.actor,
            reason=reason,
            decided_at=decided_at,
        )
        existing = self.load(proposal_ref)
        if existing is not None:
            stored: MemoryProposalDecision = self._resolve(existing, "MemoryProposalDecision")
            if (stored.decision != candidate.decision
                    or stored.actor != candidate.actor
                    or stored.reason != candidate.reason
                    or stored.expected_before_target_store_digest
                    != candidate.expected_before_target_store_digest
                    or stored.intended_after_target_store_digest
                    != candidate.intended_after_target_store_digest):
                raise MemoryConflictError(
                    "a semantically different terminal decision already exists for "
                    "this proposal (approve-vs-reject races conflict)"
                )
            return existing
        key = proposal_ref.payload_ref.content_digest
        return self._put_and_cas(
            batch_key=f"mem-prop-decision:{key}",
            schema="MemoryProposalDecision",
            payload=candidate,
            put_key=f"mem-prop-decision-put:{key}",
            cas=(("memory.proposal_preparation.v1",
                  _key_digest("memory.proposal.decision", key), None),),
        )


# --------------------------------------------------------------------------- #
# applied-evidence bundle (capture-side approval hook)                          #
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class AppliedEvidenceBundle:
    """The refs one completed exact apply pins for later CAPTURE approval.

    Capture builds the final ``AgentAppliedEvidence`` from this bundle plus the
    CURRENT source-state entry, and approves only when the entry's marker and
    store digest still equal the owner receipt's actual values (a later direct
    edit — new epoch — can never reuse it)."""

    marker_id: str
    kind: str  # "agent" | "console"
    proposal_ref: TypedPayloadRef
    receipt_ref: TypedPayloadRef
    decision_ref: TypedPayloadRef
    owner_receipt_ref: TypedPayloadRef
    actual_after_target_store_digest: str
    journal_session_id: str | None = None
    journal_event_id: int | None = None


# --------------------------------------------------------------------------- #
# the proposal service                                                          #
# --------------------------------------------------------------------------- #
class MemoryProposalService:
    """Idempotent submission + admin decision + owner-delegated application."""

    def __init__(
        self,
        *,
        stores: Any,
        registry_digest: str,
        facade: MemoryFacadeDescriptor,
        clock: Any,
        agent_root: Any = None,
        lease_factory: Any = None,
        console_root: Any = None,
        admin_verifier: AdminReviewVerifier | None = None,
        console_writer: Callable[..., dict] | None = None,
        console_journal: Callable[..., int] | None = None,
    ) -> None:
        self._stores = stores
        self._registry_digest = registry_digest
        self._facade = facade
        self._clock = clock
        self._agent_root = agent_root
        self._lease_factory = lease_factory
        self._console_root = console_root
        self._verifier = admin_verifier
        self._console_writer = console_writer
        self._console_journal = console_journal
        self._submission_repo = MemoryProposalSubmissionRepository(
            stores, registry_digest=registry_digest)
        self._decision_repo = MemoryDecisionRepository(
            stores, registry_digest=registry_digest)
        #: marker_id -> AppliedEvidenceBundle (rebuilt from durable refs on demand).
        self._applied: dict[str, AppliedEvidenceBundle] = {}

    # -- plumbing ------------------------------------------------------------- #
    def _put(self, schema: str, payload: Any, key: str) -> TypedPayloadRef:
        pref = self._stores.payloads.put(
            _SR(schema), payload, registry_digest=self._registry_digest,
            namespace="main", idempotency_key=key)
        return TypedPayloadRef(schema_ref=_SR(schema), payload_ref=pref)

    def _resolve(self, ref: TypedPayloadRef, schema: str) -> Any:
        if ref.schema_ref.name != schema or ref.schema_ref.version != "1":
            raise MemoryContractError(f"typed ref does not resolve {schema}@1")
        return self._stores.payloads.get(ref.payload_ref, expected_schema_ref=ref.schema_ref)

    def applied_evidence_resolver(self) -> Callable[[str], AppliedEvidenceBundle | None]:
        """The capture-side hook: marker id → verified applied-evidence bundle."""
        return self._applied.get

    # -- submission (worker-safe; proposal only) -------------------------------- #
    def submit_proposal(
        self,
        request: MemoryProposalRequest,
        *,
        worker: Any,
        plan: Any,
        run_id: str,
        context: Any,
        idempotency_key: str,
    ) -> tuple[MemoryProposal, TypedPayloadRef, MemoryProposalReceipt, TypedPayloadRef]:
        """Request → grant gate → Preparation → Proposal → pending side effect →
        Receipt; always ``pending_external_review``; never readable in the
        current snapshot; idempotent under the same key."""
        # (1) persist the validated Request once (content-keyed put).
        request_ref = self._put(
            "MemoryProposalRequest", request,
            key=f"mem-prop-req:{request.semantic_digest()}")
        # (2) capability + grant BEFORE any preparation/pending side effect.
        self._verify_grant(request, worker=worker, context=context)
        # (3) atomically persist/recover the typed preparation.
        preparation, preparation_ref = self._submission_repo.prepare_once(
            idempotency_key,
            lambda: self._build_preparation(
                request, request_ref, worker=worker, plan=plan, run_id=run_id,
                context=context, preparation_key=idempotency_key))
        if preparation.request_digest != request.semantic_digest():
            raise MemoryConflictError(
                "idempotency key reused with a semantically different request")
        # (4) service-build/persist the enriched Proposal once.
        proposal = build_memory_proposal(
            request, preparation=preparation, preparation_ref=preparation_ref,
            worker=worker, plan=plan, run_id=run_id, context=context,
            facade=self._facade)
        proposal_ref = self._put(
            "MemoryProposal", proposal, key=f"mem-prop:{proposal.proposal_id}")
        # (5) pending-store side effect through the EXISTING reviewed writers.
        locator, pending_digest = self._pending_side_effect(proposal)
        # (6) Receipt once, binding the Proposal ref.
        receipt = MemoryProposalReceipt(
            proposal_ref=proposal_ref,
            pending_store_content_digest=pending_digest,
            pending_locator=locator)
        receipt_ref = self._put(
            "MemoryProposalReceipt", receipt,
            key=f"mem-prop-receipt:{proposal.proposal_id}")
        return proposal, proposal_ref, receipt, receipt_ref

    def _verify_grant(self, request: MemoryProposalRequest, *, worker: Any, context: Any) -> None:
        cap = self._facade.proposal_capability_ref
        if not any(c.id == cap.id and c.version == cap.version
                   and c.content_digest == cap.content_digest
                   for c in worker.capability_allowlist):
            raise MemoryAuthorityError(
                f"worker {worker.id!r} does not hold the exact memory.propose capability")
        grant = next(
            (g for g in self._facade.proposal_grants if g.worker_id == worker.id), None)
        if grant is None:
            raise MemoryAuthorityError(f"worker {worker.id!r} has no proposal grant row")
        target = request.target
        if isinstance(target, AgentMemoryProposalTarget):
            if target.target_agent not in grant.allowed_agent_owners:
                raise MemoryAuthorityError(
                    f"worker {worker.id!r} is not granted agent owner "
                    f"{target.target_agent!r}")
        else:
            if target.scope not in grant.allowed_console_scopes:
                raise MemoryAuthorityError(
                    f"worker {worker.id!r} is not granted console scope {target.scope!r}")
            if context.memory_session_id is None:
                raise MemoryAuthorityError(
                    "a console proposal target requires a context with a memory session")

    def _build_preparation(
        self, request: MemoryProposalRequest, request_ref: TypedPayloadRef,
        *, worker: Any, plan: Any, run_id: str, context: Any, preparation_key: str,
    ) -> MemoryProposalPreparation:
        target = request.target
        proposed_at = clock_now(self._clock)
        effective_date = proposed_at.date().isoformat()
        proposal_id = "prop." + content_digest(
            [request.semantic_digest(), preparation_key])[:32]
        if isinstance(target, AgentMemoryProposalTarget):
            payload_text = target.lesson_md if target.lesson_md.endswith("\n") else target.lesson_md + "\n"
            payload_digest = sha256_unit_digest(payload_text)
            marker_id = build_apply_marker_id(
                request=request, proposal_id=proposal_id,
                intended_target_payload_digest=payload_digest)
            marker_line = build_agent_marker_line(
                marker_id=marker_id, request_digest=request.semantic_digest(),
                payload_digest=payload_digest)
            intended_after = sha256_unit_digest(marker_line + "\n" + payload_text)
            expected_before: str = "absent"  # v1 exact apply creates the lesson file
            container_before = container_after = None
        else:
            normalized = normalize_console_memory_write(
                scope=target.scope, key=target.key, text=target.text)
            payload_digest = sha256_unit_digest(normalized["text"])
            marker_id = build_apply_marker_id(
                request=request, proposal_id=proposal_id,
                intended_target_payload_digest=payload_digest)
            marker_line = build_agent_marker_line(
                marker_id=marker_id, request_digest=request.semantic_digest(),
                payload_digest=payload_digest)
            expected_before = "absent"  # the exact target record does not exist yet
            container_text = self._read_console_container()
            container_before = (
                sha256_unit_digest(container_text) if container_text is not None else "absent")
            intended_container = (
                ((container_text or "").rstrip("\n") + "\n" if container_text else "")
                + marker_line + "\n" + normalized["text"] + "\n")
            container_after = sha256_unit_digest(intended_container)
            intended_after = sha256_unit_digest(marker_line + "\n" + normalized["text"])
        return MemoryProposalPreparation(
            preparation_key=preparation_key,
            request_ref=request_ref,
            request_digest=request.semantic_digest(),
            proposer_worker_id=worker.id,
            run_id=run_id,
            plan_digest=plan.plan_digest,
            memory_session_id=context.memory_session_id,
            proposal_id=proposal_id,
            proposed_at=proposed_at,
            effective_date=effective_date,
            target_kind=target.target_kind,
            target_identity_digest=content_digest(_target_logical_identity(target)),
            marker_id=marker_id,
            intended_target_payload_digest=payload_digest,
            expected_before_target_store_digest=expected_before,
            intended_after_target_store_digest=intended_after,
            expected_before_container_digest=container_before,
            intended_after_container_digest=container_after,
        )

    def _read_console_container(self) -> str | None:
        if self._console_root is None:
            return None
        from pathlib import Path

        p = Path(self._console_root) / "memory.md"
        return p.read_text(encoding="utf-8") if p.exists() else None

    def _pending_side_effect(self, proposal: MemoryProposal):
        target = proposal.target
        if isinstance(target, AgentMemoryProposalTarget):
            # the EXISTING reviewed pending writer (safe API; legacy preserved).
            from financial_analyst.dream import proposal_writer

            content = (
                f"---\ntopic: {target.topic_slug}\ntitle: {target.title}\n"
                f"target_agent: {target.target_agent}\n"
                f"confidence: {target.confidence}\n"
                f"proposal_id: {proposal.proposal_id}\n---\n\n{target.lesson_md}\n")
            out = proposal_writer.write_pending_proposal(
                memory_root=self._agent_root,
                target_agent=target.target_agent,
                topic_slug=target.topic_slug,
                proposal_id=proposal.proposal_id.replace("prop.", "prop-"),
                effective_date=proposal.effective_date,
                content=content)
            if "error" in out:
                raise MemoryConflictError(f"pending writer refused: {out['error']}")
            locator = AgentPendingLocator(
                target_agent=target.target_agent,
                proposal_id=proposal.proposal_id,
                relative_locator=out["relative_locator"])
            return locator, out["content_digest"]
        # console: journal a pending event through the EXISTING public journal.
        journal = self._console_journal
        if journal is None:
            journal = self._default_console_journal
        event_id = journal(
            session_id=proposal.memory_session_id,
            proposal_id=proposal.proposal_id,
            marker_id=proposal.marker_id,
            scope=target.scope, key=target.key, text=target.text)
        locator = ConsolePendingEventLocator(
            journal_session_id=proposal.memory_session_id, event_id=event_id)
        return locator, sha256_unit_digest(target.text)

    def _default_console_journal(self, *, session_id: str, proposal_id: str,
                                 marker_id: str, scope: str, key: str, text: str) -> int:
        # DEFERRED(console-lifecycle): plain journal event through the EXISTING
        # public ConsoleStore interface; the structured idempotent
        # `console_memory_proposed` lifecycle event + pin awaits the console
        # coordination window.
        from guanlan_v2.console.store import ConsoleStore

        store = ConsoleStore(root=self._console_root)
        ev = store.append_event(
            session_id, "memory_proposal", proposal_id=proposal_id,
            marker_id=marker_id, scope=scope, key=key, text=text,
            status="pending_external_review")
        return ev["id"]

    # -- admin decision (verifier-gated; workers cannot reach this) -------------- #
    def decide(
        self,
        proposal_ref: TypedPayloadRef,
        receipt_ref: TypedPayloadRef,
        *,
        decision: str,
        credential: Any,
        reason: str,
    ) -> TypedPayloadRef:
        if self._verifier is None:
            raise MemoryAuthorityError(
                "memory review is disabled: no AdminReviewVerifier is injected "
                "(fail closed; a body-supplied actor is never accepted)")
        principal = self._verifier.verify(credential)
        proposal: MemoryProposal = self._resolve(proposal_ref, "MemoryProposal")
        receipt: MemoryProposalReceipt = self._resolve(receipt_ref, "MemoryProposalReceipt")
        if receipt.proposal_ref.payload_ref.content_digest != proposal_ref.payload_ref.content_digest:
            raise MemoryConflictError("receipt does not bind the given proposal")
        return self._decision_repo.decide_once(
            proposal=proposal, proposal_ref=proposal_ref, receipt_ref=receipt_ref,
            decision=decision, principal=principal, reason=reason,
            decided_at=clock_now(self._clock))

    # -- application (delegation to the existing owners) -------------------------- #
    def apply_agent_decision(self, decision_ref: TypedPayloadRef) -> TypedPayloadRef:
        """Approved agent decision → EXISTING owner coordinator → public receipt.

        The owner receives only a primitive immutable command; after its durable
        ``OwnerApplyResult`` is verified byte-for-byte, the adapter idempotently
        projects the registered ``MemoryOwnerApplySemanticReceipt`` into main.
        Retry recovers the same owner result and completes a missing projection.
        """
        decision: MemoryProposalDecision = self._resolve(decision_ref, "MemoryProposalDecision")
        if decision.decision != "approved":
            raise MemoryConflictError("only an approved decision can be applied")
        proposal: MemoryProposal = self._resolve(decision.proposal_ref, "MemoryProposal")
        target = proposal.target
        if not isinstance(target, AgentMemoryProposalTarget):
            raise MemoryContractError("apply_agent_decision requires an agent target")
        from financial_analyst.memory_ops import AgentMemoryFileCoordinator, OwnerApplyCommand

        payload_text = target.lesson_md if target.lesson_md.endswith("\n") else target.lesson_md + "\n"
        marker_line = build_agent_marker_line(
            marker_id=proposal.marker_id,
            request_digest=self._resolve(
                proposal.request_ref, "MemoryProposalRequest").semantic_digest(),
            payload_digest=proposal.intended_target_payload_digest)
        command = OwnerApplyCommand(
            operation_id=f"apply:{proposal.proposal_id}",
            target_agent=target.target_agent,
            slug=target.topic_slug,
            marker_line=marker_line,
            payload_text=payload_text,
            expected_before_target_store_digest=proposal.expected_before_target_store_digest,
            intended_after_target_store_digest=proposal.intended_after_target_store_digest)
        coordinator = AgentMemoryFileCoordinator(
            self._agent_root, lease_factory=self._lease_factory)
        result = coordinator.apply_exact(command)
        # verify the recovered owner result byte-for-byte before projecting.
        if (result.state != "applied"
                or result.marker_line != marker_line
                or result.intended_after_target_store_digest
                != proposal.intended_after_target_store_digest
                or result.actual_after_target_store_digest
                != proposal.intended_after_target_store_digest):
            raise MemoryConflictError("owner apply result does not verify against the decision")
        receipt = MemoryOwnerApplySemanticReceipt(
            receipt_kind="agent",
            operation=("create" if proposal.expected_before_target_store_digest == "absent"
                       else "update"),
            apply_operation_id=command.operation_id.replace(":", "."),
            decision_ref=decision_ref,
            proposal_ref=decision.proposal_ref,
            source_id="agent.own",
            locator=f"{target.target_agent}/{target.topic_slug}.md",
            marker_id=proposal.marker_id,
            expected_before_target_store_digest=result.expected_before_target_store_digest,
            actual_before_target_store_digest=result.actual_before_target_store_digest,
            intended_after_target_store_digest=result.intended_after_target_store_digest,
            actual_after_target_store_digest=result.actual_after_target_store_digest)
        receipt_ref = self._put(
            "MemoryOwnerApplySemanticReceipt", receipt,
            key=f"mem-owner-receipt:{command.operation_id}")
        self._applied[proposal.marker_id] = AppliedEvidenceBundle(
            marker_id=proposal.marker_id, kind="agent",
            proposal_ref=decision.proposal_ref,
            receipt_ref=decision.receipt_ref,
            decision_ref=decision_ref,
            owner_receipt_ref=receipt_ref,
            actual_after_target_store_digest=result.actual_after_target_store_digest)
        return receipt_ref

    def apply_console_decision(self, decision_ref: TypedPayloadRef) -> dict:
        """Approved console decision → the EXISTING reviewed console writer.

        # DEFERRED(console-lifecycle): this delegation calls
        # ``guanlan_v2.console.tools.memory_write_impl`` (the sole existing
        # accepted console writer) WITHOUT the exact apply-start/applied journal
        # lifecycle, container CAS enforcement or in-container marker — those
        # are console-internal and await the coordination-window follow-up.
        # Consequence (honest, fail-safe): the applied line carries no marker,
        # so conservative capture keeps it PENDING; approved-console visibility
        # arrives only with the deferred exact writer.
        """
        decision: MemoryProposalDecision = self._resolve(decision_ref, "MemoryProposalDecision")
        if decision.decision != "approved":
            raise MemoryConflictError("only an approved decision can be applied")
        proposal: MemoryProposal = self._resolve(decision.proposal_ref, "MemoryProposal")
        target = proposal.target
        if not isinstance(target, ConsoleMemoryProposalTarget):
            raise MemoryContractError("apply_console_decision requires a console target")
        writer = self._console_writer
        if writer is None:
            # lazy import of the EXISTING reviewed writer — read-only module use.
            from guanlan_v2.console.tools import memory_write_impl as writer  # type: ignore
        out = writer(text=target.text, scope=target.scope, key=target.key)
        if not isinstance(out, dict) or out.get("ok") is False:
            raise MemoryConflictError(f"console writer refused the delegated apply: {out}")
        return out
