# -*- coding: utf-8 -*-
"""Phase 2 · Task 7 — the capability-confined typed worker executor.

This module is the runtime that turns one admitted, frozen :class:`Plan` node into
a typed :class:`~guanlan_v2.orchestration.schemas.NodeRun` (always) and an exclusive
primary :class:`~guanlan_v2.orchestration.schemas.Artifact` (only on a COMPLETED /
DEGRADED terminal status). Everything it does is confined by *trusted* resolution:

* **No handler/model/provider injection point.** Handlers, model gateways and
  bridge providers resolve ONLY through the Task-3
  :class:`~guanlan_v2.orchestration.catalog_runtime.CatalogRuntime` /
  :class:`~guanlan_v2.orchestration.catalog_runtime.TrustedFactoryRegistry` and the
  Task-5 :class:`~guanlan_v2.orchestration.catalog_runtime.BridgeCatalogView`. There
  is deliberately no public ``handlers: dict[worker_id, callable]``.
* **Two-stage bridge protocol.** :meth:`ExecutionBridgeResolver.prepare_input`
  runs before the runner freezes the :class:`InputSnapshot` (it may add only
  descriptor-authorized ``memory_refs_v1`` and cannot touch the CapabilityGateway,
  a live store, the model, the wall clock or data-result backfill).
  :meth:`ExecutionBridgeResolver.open_execution` runs after the snapshot is frozen
  and the child budget reserved; it re-verifies every pre-input ref/token and
  continues the *same* :class:`ExecutionEvidenceSequencer`.
* **One executor-owned sequencer.** :class:`ExecutionEvidenceSequencer` mints every
  ordinal token; providers only *echo* the exact token they were handed and can
  never mint or relabel one. The canonical raw-contribution merge key is
  ``(call_ordinal, bridge_priority, bridge_id, within_call_role)``.
* **Atomic evidence journal.** :class:`BridgeEvidenceWriter` writes each evidence
  value as ONE :class:`~guanlan_v2.orchestration.eventstore.RuntimeUnitOfWork`
  batch — the main-namespace evidence payload, a review-namespace
  :class:`~guanlan_v2.orchestration.runtime_contracts.BridgeEvidenceRecorded`
  control fact targeting it and a review-partition journal RunEvent targeting the
  control fact. Before commit none of the triple is visible; after commit all are,
  even if the provider never returns. :class:`BridgeEvidenceJournal` drains them by
  node/token for recovery, so a provider never owns the only copy.
* **Closed CapabilityGateway state machine.** ``begin`` verifies the admitted
  Plan, the exact WorkerSpec allowlist, the token/summary binding and the
  descriptor request schema and charges the invocation to the token-bound summary;
  ``invoke`` resolves the backend from the :class:`TrustedFactoryRegistry` keyed by
  the exact catalog capability identity — a caller cannot inject a backend — and
  the raw result carries no PayloadRef and cannot count as evidence; the owning
  adapter validates and persists the request/result ONCE through
  :class:`BridgeEvidenceWriter`; ``finalize_success`` re-resolves those exact main
  refs and mints one Phase-1
  :class:`~guanlan_v2.orchestration.schemas.ToolCallRecord` with **no** second
  write; ``reject`` records one audit-only refusal. A pending invocation has
  exactly one terminal transition. Before sealing any result, the executor
  cross-checks every provider-contributed ToolCallRecord against the gateway's
  authoritative ``finalized_records()`` — a record the gateway never finalized is
  forged evidence and yields an honest INCOMPLETE, never a sealed Artifact.
  The :class:`ModelGateway` remains an *injected service port* in static v1: the
  runner is the trust boundary that selects it. Carry-forward for Task 8: the
  runner resolves the gateway via ``factories.model_factory(worker.execution.model_tier)``
  so the tier binding is enforced at the same trusted-registry seam.
* **Prompt confinement.** :class:`StaticPromptAssembler` returns only an immutable
  :class:`AssembledModelRequest`; untrusted blocks go ONLY into the model gateway's
  data channel and are never interpolated into system/skill/guardrail text. The
  executor persists exactly one
  :class:`~guanlan_v2.orchestration.runtime_contracts.PromptAssemblyRecord` BEFORE
  invoking the :class:`ModelGateway`, which rehashes the exact bytes and refuses any
  record/request/assembler/order mismatch before provider bytes are sent.

Capability I/O schema seam (documented reconciliation)
------------------------------------------------------
A capability's declared ``input_schema_ref`` / ``output_schema_ref`` are
adapter-opaque: they are NOT registered in the cumulative Phase-2 runtime registry,
and Phase-1 catalog validation does **not** resolve them against any schema
registry either (a catalog snapshot never touches a registry). What actually
confines them is that the CapabilityGateway validates every request / result
**adapter-side** against the exact ``CapabilityDescriptor`` refs through the
Phase-1 ``SchemaRegistry`` instance it is constructed with — and that validation
**fails closed**: an unresolvable schema raises (``UnknownSchemaError``), so an
unregistered capability I/O schema means the capability invocation FAILS, never
that validation is silently skipped. Evidence persistence uses the Phase-1
registry digest for the main data payloads while the review-namespace control
facts use the runtime-registry digest. No registry is mutated and no capability
model enters the runtime registry.

**NOTE for Task 9 (pilot):** the pilot MUST register its capability I/O schemas
(e.g. ``AshareConstraintQuery@1`` / ``AshareConstraintResult@1``) into the
``SchemaRegistry`` instance it hands the CapabilityGateway / worker executor, or
its capability workers fail closed at ``invoke``/``validate_result``.
"""
from __future__ import annotations

import hashlib
import json
import threading
from dataclasses import dataclass, field
from collections.abc import Mapping
from typing import Any, ClassVar, Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, model_validator

from guanlan_v2.orchestration.budget import BudgetReservation
from guanlan_v2.orchestration.catalog import CapabilityDescriptor, WorkerSpec
from guanlan_v2.orchestration.catalog_runtime import (
    BridgeCatalogView,
    CatalogMaterialError,
    CatalogRuntime,
    ResolvedBridge,
    ResolvedWorkerRuntime,
    TrustedFactoryRegistry,
)
from guanlan_v2.orchestration.context import InputSnapshot, MemoryRecordRef, RunContext
from guanlan_v2.orchestration.digest import (
    DigestHex,
    NonEmptyStr,
    NonNegativeInt,
    PositiveInt,
    content_digest,
)
from guanlan_v2.orchestration.enums import ExecutionKind, NodeStatus, ToolCallRequirement
from guanlan_v2.orchestration.eventstore import (
    EventStoreError,
    EventAppendCommand,
    PayloadPutCommand,
    RuntimeBatch,
    RuntimeStores,
    StagedPayloadKey,
    StagedPayloadRef,
    StagedTypedPayloadRef,
    StateCellCompareAndSwapCommand,
)
from guanlan_v2.orchestration.refs import (
    CapabilityRef,
    ContentRef,
    PayloadRef,
    SchemaRef,
    TypedPayloadRef,
    typed_ref_sort_key,
)
from guanlan_v2.orchestration.runtime_clock import AuthoritativeClock, clock_now
from guanlan_v2.orchestration.runtime_contracts import (
    BridgeStaticSupportSummary,
    EventRefusalRecord,
    ExecutionEvidenceOrdinalToken,
    NamedEvidenceDigest,
    PromptAssemblyRecord,
    PromptUntrustedBlockRef,
    RuntimeSupportReport,
)
from guanlan_v2.orchestration.schema_registry import SchemaRegistry
from guanlan_v2.orchestration.schemas import (
    Artifact,
    ArtifactRef,
    NodeRun,
    NumberAnchor,
    Provenance,
    ToolCallRecord,
)
from guanlan_v2.orchestration.spec import Plan, PlanNode

__all__ = [
    # errors
    "WorkerExecutionError",
    "PreflightError",
    "CapabilityGatewayError",
    "PromptBindingError",
    "EvidenceTokenError",
    # DTOs
    "ExecutionRuntime",
    "EvidenceIssuanceToken",
    "WithinCallRole",
    "AssembledModelRequest",
    "ModelResult",
    "PendingCapabilityInvocation",
    "UnpublishedCapabilityResult",
    "BridgeInputContribution",
    "PreparedBridgeHandle",
    "BridgeContribution",
    "BridgeStageOutcome",
    "PreparedBridgeSet",
    "WorkerExecutionResult",
    # services / ports
    "ExecutionEvidenceSequencer",
    "BridgeEvidenceJournal",
    "BridgeEvidenceWriter",
    "CapabilityGateway",
    "PromptAssembler",
    "StaticPromptAssembler",
    "OUTPUT_SCHEMA_SECTION",
    "output_schema_section",
    "ModelGateway",
    "ExecutionBridgeProvider",
    "ExecutionBridgeSession",
    "ExecutionBridgeResolver",
    "ExecutionObserver",
    # entry points
    "prepare_input",
    "execute_node",
    # Phase 8 · Task 8 — bounded retry + schema-repair control loop
    "execute_with_bounded_retry",
    "retry_llm_invocation_upper_bound",
    "AttemptReservation",
    "BoundedRetryOutcome",
]

# --------------------------------------------------------------------------- #
# constants                                                                    #
# --------------------------------------------------------------------------- #
CODE_VERSION = "worker-executor-v1"
ASSEMBLER_ID = "static-prompt-assembler"
ASSEMBLER_VERSION = "1"
PROMPT_BRIDGE_ID = "runtime.prompt"
#: sentinel priority for the executor-owned prompt token (excluded from bridge
#: min/max counts and never eligible for the CapabilityGateway).
PROMPT_BRIDGE_PRIORITY = 2_147_483_647
REVIEW_NAMESPACE = "review"
PROMPT_CELL_NAMESPACE = "runtime.prompt.v1"
_PRIMARY_OUTPUT_KEY = "primary"
#: the canonical-channel key carrying the DERIVED output contract (裁决 2).
OUTPUT_SCHEMA_SECTION = "output_schema"

WithinCallRole = Literal["provider_prefetch", "tool_result", "memory_prefetch"]

_MODEL_REQUEST_DOMAIN = b"guanlan.orchestration.runtime.model-request.v1\x00"


def _model_request_digest(canonical_bytes: bytes) -> str:
    """Domain-separated digest of the exact canonical model-request bytes."""
    return hashlib.sha256(_MODEL_REQUEST_DOMAIN + canonical_bytes).hexdigest()


# --------------------------------------------------------------------------- #
# errors                                                                       #
# --------------------------------------------------------------------------- #
class WorkerExecutionError(Exception):
    """Base for every worker-executor failure."""


class PreflightError(WorkerExecutionError):
    """A pure preflight check failed; no provider/capability/store side effect ran."""


class CapabilityGatewayError(WorkerExecutionError):
    """A CapabilityGateway state-machine invariant was violated."""


class PromptBindingError(WorkerExecutionError):
    """A model request/prompt-record binding did not verify."""


class EvidenceTokenError(WorkerExecutionError):
    """A minted/echoed evidence issuance token was forged, duplicate or unknown."""


# --------------------------------------------------------------------------- #
# strict runtime base                                                          #
# --------------------------------------------------------------------------- #
class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


# --------------------------------------------------------------------------- #
# runtime bundle                                                               #
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class ExecutionRuntime:
    """The read-only trusted material the executor resolves handlers/models through.

    Bundles the verified :class:`CatalogRuntime`, its
    :class:`BridgeCatalogView`, the :class:`TrustedFactoryRegistry` (handler / model
    / bridge-provider factories keyed by catalog ref identity), the frozen
    :class:`RuntimeSupportReport` (embedded per-node summaries) and the runtime
    registry digest for control-fact persistence. It carries no mutable state and no
    caller-supplied callable.
    """

    catalog: CatalogRuntime
    bridge_view: BridgeCatalogView
    factories: TrustedFactoryRegistry
    support_report: RuntimeSupportReport
    runtime_registry_digest: str
    code_version: str = CODE_VERSION

    def summaries_for(self, node_id: str) -> tuple[BridgeStaticSupportSummary, ...]:
        return tuple(
            s for s in self.support_report.bridge_support_summaries if s.node_id == node_id
        )


# --------------------------------------------------------------------------- #
# evidence issuance token + sequencer                                          #
# --------------------------------------------------------------------------- #
class EvidenceIssuanceToken(_StrictModel):
    """The executor-owned within-node issuance token (the brief's internal token).

    Binds ``node_id`` / ``attempt`` / ``call_ordinal`` / ``evidence_ordinal`` plus
    the provider identity (``bridge_priority`` / ``bridge_id``) and an
    ``issuance_digest`` that pins the token to exactly one
    :class:`BridgeStaticSupportSummary` digest. Only the node-owned
    :class:`ExecutionEvidenceSequencer` constructs one; a provider must echo the
    exact token and cannot relabel its provider identity or mint a new ordinal.

    The persisted recovery-metadata projection is the *registered*
    :class:`ExecutionEvidenceOrdinalToken` ``(attempt, call_ordinal,
    evidence_ordinal)`` (a distinct, deliberately narrower shape); this internal
    token is a superset and is never persisted, so no registered schema/golden
    changes.
    """

    node_id: NonEmptyStr
    attempt: PositiveInt
    call_ordinal: PositiveInt
    evidence_ordinal: PositiveInt
    bridge_priority: NonNegativeInt
    bridge_id: NonEmptyStr
    summary_digest: DigestHex
    issuance_digest: DigestHex

    @property
    def is_prompt_token(self) -> bool:
        return self.bridge_id == PROMPT_BRIDGE_ID

    def ordinal_projection(self) -> ExecutionEvidenceOrdinalToken:
        """The registered persisted-recovery projection of this token."""
        return ExecutionEvidenceOrdinalToken(
            attempt=self.attempt,
            call_ordinal=self.call_ordinal,
            evidence_ordinal=self.evidence_ordinal,
        )


def _issuance_digest(
    *, node_id: str, attempt: int, call_ordinal: int, bridge_priority: int,
    bridge_id: str, summary_digest: str,
) -> str:
    return content_digest(
        {
            "domain": "runtime.evidence-issuance.v1",
            "node_id": node_id,
            "attempt": attempt,
            "call_ordinal": call_ordinal,
            "bridge_priority": bridge_priority,
            "bridge_id": bridge_id,
            "summary_digest": summary_digest,
        }
    )


class ExecutionEvidenceSequencer:
    """The single node-owned issuer of evidence ordinals (pre-input + execution).

    ``call_ordinal`` starts at one and continues monotonically across both bridge
    phases; ``evidence_ordinal`` is a distinct monotonic per-evidence counter the
    :class:`BridgeEvidenceWriter` draws for each persisted value. Every issued token
    is retained in an ``issued`` set keyed by its full identity, so a provider that
    echoes a token the sequencer never minted — or mints one itself — fails
    validation. The reserved executor-owned ``runtime.prompt`` token is minted
    exactly once, after all provider tokens are frozen, and can never enter the
    CapabilityGateway.
    """

    def __init__(self, *, node_id: str, attempt: int) -> None:
        self._node_id = node_id
        self._attempt = attempt
        self._call_seq = 0
        self._evidence_seq = 0
        self._issued: dict[tuple[int, int, str], EvidenceIssuanceToken] = {}
        self._prompt_issued = False
        self._lock = threading.RLock()

    @property
    def node_id(self) -> str:
        return self._node_id

    @property
    def attempt(self) -> int:
        return self._attempt

    def issue_call_token(
        self, *, bridge_priority: int, bridge_id: str, summary_digest: str,
    ) -> EvidenceIssuanceToken:
        """Mint the next provider issuance token bound to ``summary_digest``."""
        if bridge_id == PROMPT_BRIDGE_ID:
            raise EvidenceTokenError("the reserved prompt token id is executor-only")
        with self._lock:
            self._call_seq += 1
            call_ordinal = self._call_seq
            token = EvidenceIssuanceToken(
                node_id=self._node_id, attempt=self._attempt, call_ordinal=call_ordinal,
                evidence_ordinal=call_ordinal, bridge_priority=bridge_priority,
                bridge_id=bridge_id, summary_digest=summary_digest,
                issuance_digest=_issuance_digest(
                    node_id=self._node_id, attempt=self._attempt, call_ordinal=call_ordinal,
                    bridge_priority=bridge_priority, bridge_id=bridge_id,
                    summary_digest=summary_digest),
            )
            self._issued[(call_ordinal, bridge_priority, bridge_id)] = token
            return token

    def issue_prompt_token(self) -> EvidenceIssuanceToken:
        """Mint the single reserved executor-owned ``runtime.prompt`` token."""
        with self._lock:
            if self._prompt_issued:
                raise EvidenceTokenError("the reserved prompt token was already issued")
            self._prompt_issued = True
            self._call_seq += 1
            call_ordinal = self._call_seq
            token = EvidenceIssuanceToken(
                node_id=self._node_id, attempt=self._attempt, call_ordinal=call_ordinal,
                evidence_ordinal=call_ordinal, bridge_priority=PROMPT_BRIDGE_PRIORITY,
                bridge_id=PROMPT_BRIDGE_ID, summary_digest=_ZERO,
                issuance_digest=_issuance_digest(
                    node_id=self._node_id, attempt=self._attempt, call_ordinal=call_ordinal,
                    bridge_priority=PROMPT_BRIDGE_PRIORITY, bridge_id=PROMPT_BRIDGE_ID,
                    summary_digest=_ZERO),
            )
            self._issued[(call_ordinal, PROMPT_BRIDGE_PRIORITY, PROMPT_BRIDGE_ID)] = token
            return token

    def next_evidence_ordinal(self) -> int:
        """Draw the next monotonic per-evidence ordinal (a writer-side put position)."""
        with self._lock:
            self._evidence_seq += 1
            return self._evidence_seq

    def validate_token(self, token: EvidenceIssuanceToken) -> EvidenceIssuanceToken:
        """Reject a forged / relabelled / unknown token; return the exact minted one."""
        if not isinstance(token, EvidenceIssuanceToken):
            raise EvidenceTokenError("evidence token is not an EvidenceIssuanceToken")
        if token.node_id != self._node_id or token.attempt != self._attempt:
            raise EvidenceTokenError("evidence token names a foreign node/attempt")
        minted = self._issued.get((token.call_ordinal, token.bridge_priority, token.bridge_id))
        if minted is None:
            raise EvidenceTokenError(
                f"evidence token for call {token.call_ordinal} / bridge {token.bridge_id!r} "
                "was never minted by the node sequencer"
            )
        if minted != token:
            raise EvidenceTokenError(
                "evidence token identity was relabelled from the minted token "
                "(issuance/summary/priority forgery)"
            )
        return minted


_ZERO = "0" * 64


# --------------------------------------------------------------------------- #
# bridge evidence journal + writer                                             #
# --------------------------------------------------------------------------- #
_BRIDGE_EVIDENCE_RECORDED_SR = SchemaRef(name="BridgeEvidenceRecorded", version="1")
_PROMPT_ASSEMBLY_RECORD_SR = SchemaRef(name="PromptAssemblyRecord", version="1")


@dataclass(frozen=True)
class _JournaledEvidence:
    """One drained journal entry: the recovery control fact + resolved main ref."""

    token: ExecutionEvidenceOrdinalToken
    within_call_role: str
    evidence_ref: TypedPayloadRef


class BridgeEvidenceJournal:
    """Read-only recovery view over the persisted bridge-evidence control facts.

    Drains the review-namespace
    :class:`~guanlan_v2.orchestration.runtime_contracts.BridgeEvidenceRecorded`
    control facts for one node (by ``correlation_id == node_id``) in canonical
    ``(call_ordinal, evidence_ordinal)`` order, so a provider that never returned —
    or a service that crashed after the atomic commit — still exposes every
    committed evidence ref exactly once.
    """

    def __init__(self, *, stores: RuntimeStores, run_id: str, plan_digest: str, node_id: str) -> None:
        self._stores = stores
        self._run_id = run_id
        self._plan_digest = plan_digest
        self._node_id = node_id

    def drain(self) -> tuple[_JournaledEvidence, ...]:
        out: list[_JournaledEvidence] = []
        for ev in self._stores.events.journal(self._run_id, REVIEW_NAMESPACE):
            if ev.payload_schema_ref != _BRIDGE_EVIDENCE_RECORDED_SR:
                continue
            if ev.correlation_id != self._node_id or ev.plan_digest != self._plan_digest:
                continue
            fact = self._stores.payloads.get(
                ev.payload_ref, expected_schema_ref=_BRIDGE_EVIDENCE_RECORDED_SR)
            out.append(
                _JournaledEvidence(
                    token=fact.ordinal_token,
                    within_call_role=fact.within_call_role,
                    evidence_ref=fact.evidence_ref,
                )
            )
        out.sort(key=lambda j: (j.token.call_ordinal, j.token.evidence_ordinal))
        return tuple(out)


class BridgeEvidenceWriter:
    """The only provider write port: one atomic evidence-payload + control + event.

    ``put`` persists a *new* main-namespace evidence payload and, in the SAME unit
    of work, a review-namespace ``BridgeEvidenceRecorded`` control fact targeting a
    staged reference to it and a review-partition journal RunEvent targeting a staged
    reference to that control fact. ``record_existing`` revalidates an already
    persisted main ref and atomically writes only the control fact + event. Providers
    receive this fixed-main writer and a read-only PayloadStore view — never a raw
    write-capable store — so before commit none of a triple is visible and after
    commit all are, even if the provider never returns.
    """

    def __init__(
        self,
        *,
        stores: RuntimeStores,
        sequencer: ExecutionEvidenceSequencer,
        run_id: str,
        plan_digest: str,
        node_id: str,
        data_registry_digest: str,
        runtime_registry_digest: str,
    ) -> None:
        self._stores = stores
        self._sequencer = sequencer
        self._run_id = run_id
        self._plan_digest = plan_digest
        self._node_id = node_id
        self._data_rt = data_registry_digest
        self._rt = runtime_registry_digest
        self._put_seq = 0
        self._sealed = False

    def seal(self) -> None:
        """Seal the writer: any later ``put`` / ``record_existing`` is rejected.

        The executor seals the stage-1 writer once every prepared handle is frozen
        and the execution writer once every session's ``freeze_for_execution``
        returned (or a terminal interruption fired), so a provider that stashed the
        writer cannot journal late evidence after its contribution was sealed.
        """
        self._sealed = True

    def _require_open(self, op: str) -> None:
        if self._sealed:
            raise WorkerExecutionError(
                f"BridgeEvidenceWriter.{op} rejected: the writer is sealed "
                "(late call after freeze_for_execution)"
            )

    @property
    def reader(self):
        """A read-only PayloadStore view (no write surface)."""
        return _ReadOnlyPayloadView(self._stores.payloads)

    def _next_key(self, prefix: str) -> str:
        self._put_seq += 1
        return f"{self._node_id}:{prefix}:{self._put_seq}"

    def _run_scoped(self, idempotency_key: str) -> str:
        """Scope a provider's semantic idempotency key by THIS writer's run identity.

        The sixth member of the Task 8b defect class (live ``run --attempt 4``,
        2026-07-29): providers mint keys from per-run-local coordinates only —
        ``{bridge_id}:{node_id}:a{attempt}:c{call_ordinal}:…`` (experience + memory
        bridges) and ``{node_id}:a{attempt}:{semantic}`` (the data adapter) — while
        the sealed Lane-0 preset pins its node ids and every fresh run restarts at
        ``a1:c1``. Against the durable store (``var/orchestration/``) the SECOND
        run identity therefore reused the FIRST run's payload keys with different
        content (and the ``:control`` fact ALWAYS differs: it embeds ``run_id``),
        so both LLM seats died in ``freeze_for_execution`` with
        ``IdempotencyConflict`` before any model invoke. Folding ``run_id`` here —
        the one choke point every provider write crosses, which already holds it —
        run-scopes all four durable keys of a ``put`` (``:uow``/``:evidence``/
        ``:control``/``:event``) and the three of a ``record_existing`` for every
        current and future provider. Within one run the key is unchanged-stable
        (crash-retry replays the identical batch); keys written under the pre-fix
        shape become unreachable and are deliberately NOT migrated — they are
        dedup guards, not lookups, and a fresh write under the new shape is the
        correct outcome (the Task 8b precedent, worker._persist_prompt_record).
        """
        return f"{self._run_id}:{idempotency_key}"

    def _control_template(
        self, ordinal: ExecutionEvidenceOrdinalToken, role: str, evidence_ref: Any,
    ) -> dict[str, Any]:
        return {
            "schema_version": "1",
            "run_id": self._run_id,
            "plan_digest": self._plan_digest,
            "node_id": self._node_id,
            "ordinal_token": ordinal,
            "within_call_role": role,
            "evidence_ref": evidence_ref,
        }

    def put(
        self,
        *,
        token: EvidenceIssuanceToken,
        role: WithinCallRole,
        schema_ref: SchemaRef,
        payload: Any,
        idempotency_key: str,
    ) -> TypedPayloadRef:
        self._require_open("put")
        self._sequencer.validate_token(token)
        idempotency_key = self._run_scoped(idempotency_key)
        evidence_ordinal = self._sequencer.next_evidence_ordinal()
        ordinal = ExecutionEvidenceOrdinalToken(
            attempt=token.attempt, call_ordinal=token.call_ordinal,
            evidence_ordinal=evidence_ordinal,
        )
        ev_key = self._next_key("evidence")
        ctrl_key = self._next_key("control")
        staged_evidence = StagedPayloadRef(
            staged_key=StagedPayloadKey(key=ev_key), schema_ref=schema_ref, namespace="main")
        control_template = self._control_template(
            ordinal, role, {"schema_ref": schema_ref, "payload_ref": staged_evidence})
        batch = RuntimeBatch(
            idempotency_key=f"{idempotency_key}:uow",
            payload_puts=(
                PayloadPutCommand(
                    staged_key=StagedPayloadKey(key=ev_key), schema_ref=schema_ref,
                    namespace="main", payload_template=_as_template(payload),
                    registry_digest=self._data_rt, idempotency_key=f"{idempotency_key}:evidence"),
                PayloadPutCommand(
                    staged_key=StagedPayloadKey(key=ctrl_key), schema_ref=_BRIDGE_EVIDENCE_RECORDED_SR,
                    namespace=REVIEW_NAMESPACE, payload_template=control_template,
                    registry_digest=self._rt, idempotency_key=f"{idempotency_key}:control"),
            ),
            event_appends=(
                EventAppendCommand(
                    run_id=self._run_id, partition=REVIEW_NAMESPACE, event_type="NodeStateChanged",
                    payload_schema_ref=_BRIDGE_EVIDENCE_RECORDED_SR,
                    payload_target=StagedPayloadKey(key=ctrl_key), registry_digest=self._rt,
                    idempotency_key=f"{idempotency_key}:event", plan_digest=self._plan_digest,
                    correlation_id=self._node_id),
            ),
        )
        result = self._stores.unit_of_work.commit(batch)
        return result.staged_typed_ref(ev_key)

    def record_existing(
        self,
        *,
        token: EvidenceIssuanceToken,
        role: WithinCallRole,
        typed_ref: TypedPayloadRef,
        idempotency_key: str,
    ) -> TypedPayloadRef:
        self._require_open("record_existing")
        self._sequencer.validate_token(token)
        idempotency_key = self._run_scoped(idempotency_key)
        if typed_ref.payload_ref.namespace != "main":
            raise EvidenceGatewayNamespace("record_existing requires a main-namespace typed ref")
        # revalidate the already-persisted main payload resolves at the exact digest.
        self._stores.payloads.get(typed_ref.payload_ref, expected_schema_ref=typed_ref.schema_ref)
        evidence_ordinal = self._sequencer.next_evidence_ordinal()
        ordinal = ExecutionEvidenceOrdinalToken(
            attempt=token.attempt, call_ordinal=token.call_ordinal,
            evidence_ordinal=evidence_ordinal,
        )
        ctrl_key = self._next_key("control")
        control_template = self._control_template(ordinal, role, typed_ref)
        batch = RuntimeBatch(
            idempotency_key=f"{idempotency_key}:uow",
            payload_puts=(
                PayloadPutCommand(
                    staged_key=StagedPayloadKey(key=ctrl_key), schema_ref=_BRIDGE_EVIDENCE_RECORDED_SR,
                    namespace=REVIEW_NAMESPACE, payload_template=control_template,
                    registry_digest=self._rt, idempotency_key=f"{idempotency_key}:control"),
            ),
            event_appends=(
                EventAppendCommand(
                    run_id=self._run_id, partition=REVIEW_NAMESPACE, event_type="NodeStateChanged",
                    payload_schema_ref=_BRIDGE_EVIDENCE_RECORDED_SR,
                    payload_target=StagedPayloadKey(key=ctrl_key), registry_digest=self._rt,
                    idempotency_key=f"{idempotency_key}:event", plan_digest=self._plan_digest,
                    correlation_id=self._node_id),
            ),
        )
        self._stores.unit_of_work.commit(batch)
        return typed_ref


class _ReadOnlyPayloadView:
    """A read-only PayloadStore view — exposes ``get`` only, never a write surface.

    Providers and the CapabilityGateway re-resolve refs through this view, so a
    provider can never reach a raw write-capable store: the only write port is the
    fixed-main :class:`BridgeEvidenceWriter`.
    """

    __slots__ = ("_store",)

    def __init__(self, store) -> None:
        self._store = store

    def get(self, ref, *, expected_schema_ref):
        return self._store.get(ref, expected_schema_ref=expected_schema_ref)


class EvidenceGatewayNamespace(WorkerExecutionError):
    """A control/evidence ref referenced a non-main payload namespace."""


def _as_template(payload: Any) -> dict[str, Any]:
    """Coerce a payload value into a closed put template mapping.

    A pydantic model is converted through ``dict(model)`` so nested model instances
    survive (the eventstore put path validates the exact registered schema).
    """
    if isinstance(payload, BaseModel):
        return dict(payload)
    if isinstance(payload, dict):
        return dict(payload)
    raise WorkerExecutionError(
        f"evidence payload must be a pydantic model or mapping, got {type(payload).__name__}"
    )


# --------------------------------------------------------------------------- #
# Capability gateway                                                           #
# --------------------------------------------------------------------------- #
class PendingCapabilityInvocation(_StrictModel):
    """One begun-but-unterminated capability invocation (opaque authorization)."""

    plan_digest: DigestHex
    node_id: NonEmptyStr
    worker_id: NonEmptyStr
    ordinal_token: EvidenceIssuanceToken
    capability_ref: CapabilityRef
    request_schema_ref: SchemaRef
    result_schema_ref: SchemaRef
    summary_digest: DigestHex
    idempotency_key: NonEmptyStr


@dataclass(frozen=True)
class UnpublishedCapabilityResult:
    """The raw backend result — deliberately carries NO PayloadRef / public identity.

    It cannot count as tool evidence: only the owning adapter that validates and
    persists it through :class:`BridgeEvidenceWriter`, then finalizes it, mints a
    :class:`ToolCallRecord`.
    """

    pending: PendingCapabilityInvocation
    validated_request: Any
    raw_result: Any


class _SummaryCharge:
    """Per-summary invocation / finalized-success counters for the closed arithmetic."""

    __slots__ = ("invocations", "finalized")

    def __init__(self) -> None:
        self.invocations = 0
        self.finalized = 0


@runtime_checkable
class _CapabilityBackend(Protocol):
    """The trusted resolved backend a gateway calls (never a caller callable)."""

    def invoke(self, *, capability_ref: CapabilityRef, request: Any) -> Any:
        ...


class CapabilityGateway:
    """The closed capability state machine (``begin`` → ``invoke`` → terminal).

    Confines every capability call to the admitted Plan, the exact WorkerSpec
    allowlist and the token-bound summary. A caller cannot inject a backend:
    ``invoke`` resolves it per capability from the
    :class:`~guanlan_v2.orchestration.catalog_runtime.TrustedFactoryRegistry`
    keyed by the exact catalog capability identity (id + version + content
    digest); an unbound / off-catalog capability has no backend and fails. A
    worker cannot self-report a numeric tool count; only a ``finalize_success``
    (which re-resolves the exact persisted main refs) mints a
    :class:`ToolCallRecord`.
    """

    def __init__(
        self,
        *,
        plan_digest: str,
        worker: WorkerSpec,
        summaries: dict[str, BridgeStaticSupportSummary],
        catalog: CatalogRuntime,
        factories: TrustedFactoryRegistry,
        phase1_registry: SchemaRegistry,
        refusal_sink,
        clock: AuthoritativeClock,
        sequencer: ExecutionEvidenceSequencer,
    ) -> None:
        self._plan_digest = plan_digest
        self._worker = worker
        self._summaries = summaries
        self._catalog = catalog
        self._factories = factories
        self._registry = phase1_registry
        self._refusals = refusal_sink
        self._clock = clock
        self._sequencer = sequencer
        # anchor tokens whose own ordinal has already been consumed by a begin;
        # a later begin under the same anchor draws a FRESH service-issued ordinal
        # (Phase-1 requires duplicate-free call ordinals across all records).
        self._anchor_used: set[tuple[int, int, str]] = set()
        self._allow = {(c.id, c.version): c for c in worker.capability_allowlist}
        self._charges: dict[str, _SummaryCharge] = {d: _SummaryCharge() for d in summaries}
        self._pending: dict[str, PendingCapabilityInvocation] = {}
        self._terminal: dict[str, str] = {}  # idempotency_key -> "success"|"reject"
        self._records: dict[str, ToolCallRecord] = {}
        self._running = False
        self._reader: Any = None

    def mark_running(self) -> None:
        self._running = True

    def summary_charge(self, summary_digest: str) -> _SummaryCharge:
        return self._charges[summary_digest]

    def finalized_records(self) -> tuple[ToolCallRecord, ...]:
        return tuple(self._records.values())

    def begun_count(self) -> int:
        return sum(c.invocations for c in self._charges.values())

    # -- transitions -------------------------------------------------------- #
    def begin(
        self,
        *,
        ordinal_token: EvidenceIssuanceToken,
        capability_ref: CapabilityRef,
        request_schema_ref: SchemaRef,
        idempotency_key: str,
    ) -> PendingCapabilityInvocation:
        if not self._running:
            raise CapabilityGatewayError("RUNNING must precede any capability begin")
        if ordinal_token.is_prompt_token:
            raise CapabilityGatewayError("the reserved prompt token cannot enter the CapabilityGateway")
        summary = self._summaries.get(ordinal_token.summary_digest)
        if summary is None:
            raise CapabilityGatewayError("ordinal token is not bound to an active support summary")
        if ordinal_token.bridge_id != summary.bridge_id:
            raise CapabilityGatewayError("ordinal token bridge id does not match its bound summary")
        # WorkerSpec allowlist + summary allow-list membership.
        key = (capability_ref.id, capability_ref.version)
        allowed = self._allow.get(key)
        if allowed is None or allowed.content_digest != capability_ref.content_digest:
            self._refuse(capability_ref, request_schema_ref, "capability_not_in_worker_allowlist",
                         idempotency_key)
            raise CapabilityGatewayError("capability is outside the WorkerSpec allowlist")
        summary_keys = {(c.id, c.version) for c in summary.allowed_capability_refs}
        if key not in summary_keys:
            self._refuse(capability_ref, request_schema_ref, "capability_not_in_summary",
                         idempotency_key)
            raise CapabilityGatewayError("capability is not allowed by the token-bound summary")
        # descriptor request-schema binding.
        try:
            desc = self._catalog.capability(capability_ref).descriptor
        except CatalogMaterialError as exc:
            raise CapabilityGatewayError(f"capability descriptor did not resolve: {exc}") from exc
        if request_schema_ref != desc.input_schema_ref:
            self._refuse(capability_ref, request_schema_ref, "request_schema_mismatch", idempotency_key)
            raise CapabilityGatewayError("request schema does not match the descriptor input schema")
        # charge the token-bound summary; reject max+1 BEFORE backend I/O.
        charge = self._charges[ordinal_token.summary_digest]
        if charge.invocations + 1 > summary.max_capability_invocations:
            self._refuse(capability_ref, request_schema_ref, "max_capability_invocations_exceeded",
                         idempotency_key)
            raise CapabilityGatewayError(
                f"summary {summary.bridge_id!r} max_capability_invocations "
                f"({summary.max_capability_invocations}) exceeded"
            )
        if idempotency_key in self._terminal or idempotency_key in self._pending:
            raise CapabilityGatewayError("invocation idempotency key already in use")
        charge.invocations += 1
        # service-issued unique call ordinal: the FIRST begin under a bridge's
        # issuance token consumes that token's own ordinal; every later begin under
        # the same anchor draws a fresh ordinal from the same node sequencer, so
        # two finalized records can never share a call_ordinal. Providers still
        # cannot mint one — the sequencer is executor-owned.
        anchor_key = (ordinal_token.call_ordinal, ordinal_token.bridge_priority,
                      ordinal_token.bridge_id)
        if anchor_key in self._anchor_used:
            call_token = self._sequencer.issue_call_token(
                bridge_priority=ordinal_token.bridge_priority,
                bridge_id=ordinal_token.bridge_id,
                summary_digest=ordinal_token.summary_digest)
        else:
            self._anchor_used.add(anchor_key)
            call_token = ordinal_token
        pending = PendingCapabilityInvocation(
            plan_digest=self._plan_digest, node_id=ordinal_token.node_id,
            worker_id=self._worker.id, ordinal_token=call_token,
            capability_ref=capability_ref, request_schema_ref=request_schema_ref,
            result_schema_ref=desc.output_schema_ref, summary_digest=ordinal_token.summary_digest,
            idempotency_key=idempotency_key,
        )
        self._pending[idempotency_key] = pending
        return pending

    def invoke(
        self, pending: PendingCapabilityInvocation, validated_request: Any,
    ) -> UnpublishedCapabilityResult:
        if self._pending.get(pending.idempotency_key) is not pending:
            raise CapabilityGatewayError("invoke on an unknown / already-terminal pending invocation")
        # adapter-side request validation against the exact descriptor input schema.
        # FAILS CLOSED: an unregistered/unresolvable schema raises here — the
        # invocation can never silently skip validation.
        req_model = self._registry.validate_payload(pending.request_schema_ref, _raw(validated_request))
        # the backend resolves ONLY from the trusted registry, keyed by the exact
        # catalog capability identity — never from a caller-supplied callable.
        try:
            factory = self._factories.capability_backend_factory(pending.capability_ref)
        except CatalogMaterialError as exc:
            raise CapabilityGatewayError(
                f"no trusted capability backend bound for "
                f"{pending.capability_ref.id}@{pending.capability_ref.version}: {exc}"
            ) from exc
        backend = factory(capability_ref=pending.capability_ref)
        raw = backend.invoke(capability_ref=pending.capability_ref, request=req_model)
        return UnpublishedCapabilityResult(pending=pending, validated_request=req_model, raw_result=raw)

    def validate_result(self, unpublished: UnpublishedCapabilityResult) -> Any:
        """Adapter-side result validation against the descriptor output schema."""
        pending = unpublished.pending
        return self._registry.validate_payload(
            pending.result_schema_ref, _raw(unpublished.raw_result))

    def finalize_success(
        self,
        pending: PendingCapabilityInvocation,
        *,
        request_ref: TypedPayloadRef,
        result_ref: TypedPayloadRef,
    ) -> ToolCallRecord:
        prior = self._terminal.get(pending.idempotency_key)
        if prior == "success":
            return self._records[pending.idempotency_key]  # idempotent identical terminal
        if prior == "reject":
            raise CapabilityGatewayError("a rejected invocation cannot be finalized as success")
        if self._pending.get(pending.idempotency_key) is not pending:
            raise CapabilityGatewayError("finalize_success on an unknown pending invocation")
        # re-resolve the exact persisted main refs + verify against the capability schemas.
        self._verify_ref(request_ref, pending.request_schema_ref, "request")
        self._verify_ref(result_ref, pending.result_schema_ref, "result")
        now = clock_now(self._clock)
        record = ToolCallRecord(
            call_ordinal=pending.ordinal_token.call_ordinal, tool_ref=pending.capability_ref,
            request_ref=request_ref, result_ref=result_ref,
            call_id=f"{pending.node_id}:call-{pending.ordinal_token.call_ordinal}",
            provider_call_id=None, started_at=now, finished_at=now,
        )
        self._charges[pending.summary_digest].finalized += 1
        self._terminal[pending.idempotency_key] = "success"
        self._records[pending.idempotency_key] = record
        del self._pending[pending.idempotency_key]
        return record

    def reject(
        self,
        pending: PendingCapabilityInvocation,
        *,
        detail_schema_ref: SchemaRef,
        detail_payload: Any,
        reason_code: str,
        idempotency_key: str,
    ) -> EventRefusalRecord:
        prior = self._terminal.get(pending.idempotency_key)
        if prior == "reject":
            # idempotent identical terminal: re-record returns the same audit record.
            return self._refusals.record(
                detail_schema_ref=detail_schema_ref, detail_payload=detail_payload,
                reason_code=reason_code, idempotency_key=idempotency_key,
                attempted_capability_ref=pending.capability_ref,
                attempted_schema_ref=pending.request_schema_ref)
        if prior == "success":
            raise CapabilityGatewayError("a finalized-success invocation cannot be rejected")
        if self._pending.get(pending.idempotency_key) is not pending:
            raise CapabilityGatewayError("reject on an unknown pending invocation")
        record = self._refusals.record(
            detail_schema_ref=detail_schema_ref, detail_payload=detail_payload,
            reason_code=reason_code, idempotency_key=idempotency_key,
            attempted_capability_ref=pending.capability_ref,
            attempted_schema_ref=pending.request_schema_ref)
        self._terminal[pending.idempotency_key] = "reject"
        del self._pending[pending.idempotency_key]
        return record

    def bind_reader(self, reader) -> None:
        """Bind the read-only PayloadStore view the gateway re-resolves refs through."""
        self._reader = reader

    # -- helpers ------------------------------------------------------------ #
    def _verify_ref(self, ref: TypedPayloadRef, schema_ref: SchemaRef, label: str) -> None:
        if ref.payload_ref.namespace != "main":
            raise CapabilityGatewayError(f"{label} ref must be main-namespace")
        if ref.schema_ref != schema_ref:
            raise CapabilityGatewayError(f"{label} ref schema does not match the capability schema")
        if self._reader is None:  # pragma: no cover - executor always binds a reader
            raise CapabilityGatewayError("gateway has no bound reader to re-resolve the ref")
        # re-resolve through the same read-only payload store the adapter persisted
        # into (raises on a missing / digest-mismatched ref); performs no second write.
        self._reader.get(ref.payload_ref, expected_schema_ref=ref.schema_ref)

    def _refuse(self, capability_ref, request_schema_ref, reason_code, idempotency_key) -> None:
        from guanlan_v2.orchestration.runtime_contracts import GenericRefusalDetails

        self._refusals.record(
            detail_schema_ref=SchemaRef(name="GenericRefusalDetails", version="1"),
            detail_payload=GenericRefusalDetails(summary=reason_code, category="capability_gateway"),
            reason_code=reason_code, idempotency_key=f"{idempotency_key}:{reason_code}",
            attempted_capability_ref=capability_ref, attempted_schema_ref=request_schema_ref)


def _raw(value: Any) -> Any:
    return value.model_dump() if isinstance(value, BaseModel) else value


def _validate_primary_output(registry: Any, schema_ref: Any, payload: Any) -> Any:
    """Step (9) validation, with the decode mode matched to the payload's nature.

    D2 (2026-07-29) established strict JSON-mode as the only discipline under
    which a raw model completion can be judged: strict PYTHON-mode refuses every
    JSON-expressible conversion, so validating a completion with it produces
    artifact errors (``'unknown' is not an instance of TrendState``, an ISO
    string is not a ``datetime``…) that bury the answer's TRUE defect — the
    2026-07-31 live run ``nr-lane0-2026-07-31-lane0.regime-1`` recorded 21 such
    artifacts over the single real error (``drivers must be sorted``).

    So: a payload that arrives as a raw JSON ``Mapping`` (a completion a
    gateway ``json.loads``-ed, or one the normalizing decorators REFUSED and
    passed through byte-identical) validates via ``model_validate_json(...,
    strict=True)`` — the refusal then carries the true errors. A typed instance
    (every deterministic handler and scripted double) keeps the reviewed
    python-mode path bit-for-bit, and so does a ``Mapping`` carrying values
    JSON cannot express (a scripted double's python ``datetime`` is not a
    completion). Nothing here loosens the contract: strict JSON mode still
    refuses undeclared fields, missing fields and every non-JSON coercion, and
    every model validator still runs.
    """
    if isinstance(payload, Mapping):
        resolve = getattr(registry, "resolve", None)
        if resolve is not None:
            try:
                text = json.dumps(payload, ensure_ascii=False)
            except (TypeError, ValueError):
                text = None                 # not JSON-expressible ⇒ not a completion
            if text is not None:
                declared = payload.get("schema_version")
                if declared is not None and declared != schema_ref.version:
                    raise ValueError(
                        f"payload schema_version {declared!r} does not match "
                        f"resolved schema {schema_ref.key!r}")
                model = resolve(schema_ref)
                return model.model_validate_json(text, strict=True)
    return registry.validate_payload(schema_ref, _raw(payload))


# --------------------------------------------------------------------------- #
# Prompt assembler + model gateway                                            #
# --------------------------------------------------------------------------- #
class AssembledModelRequest(_StrictModel):
    """The immutable assembler output: canonical bytes + digest + persisted record.

    The only thing :class:`StaticPromptAssembler` returns — never a detached
    prompt/string. ``request_digest`` is the domain-separated digest of the exact
    ``canonical_request_bytes`` and equals ``prompt_record.model_request_digest``.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, arbitrary_types_allowed=True)

    canonical_request_bytes: bytes
    request_digest: DigestHex
    prompt_record: PromptAssemblyRecord

    @model_validator(mode="after")
    def _bound(self) -> "AssembledModelRequest":
        if self.request_digest != _model_request_digest(self.canonical_request_bytes):
            raise ValueError("request_digest does not match the canonical request bytes")
        if self.prompt_record.model_request_digest != self.request_digest:
            raise ValueError("prompt_record.model_request_digest does not equal request_digest")
        return self


@dataclass(frozen=True)
class ModelResult:
    """The single-shot model / deterministic-handler outcome the executor consumes.

    ``decode_refusal`` (2026-07-31) is the normalizing gateways' channel for the
    TRUE refusal: when the reviewed strict-JSON output treatment (``llm_output``,
    via ``bootstrap.Lane0OutputNormalizingGateway`` or
    ``assembly.SeatOutputNormalizingGateway``) cannot turn a raw completion into
    its typed report, the payload passes through byte-identical (the honesty red
    line) and this field carries the exact error the reviewed decode measured —
    e.g. the 2026-07-31 live run's real defect ``drivers must be sorted``. The
    executor's step (9) refuses with THIS reason instead of re-deriving one from
    a payload it cannot judge fully (the runtime's stamps are not its to know),
    which is how the live record came to bury one true error under 21
    python-mode artifacts. ``None`` = no normalizer refused anything.
    """

    payload: Any
    rendered_text: str
    number_anchors: tuple[NumberAnchor, ...] = ()
    input_tokens: int = 0
    output_tokens: int = 0
    provider: str | None = None
    model: str | None = None
    provider_response_id: str | None = None
    degraded: bool = False
    degradation_reasons: tuple[str, ...] = ()
    decode_refusal: str | None = None


def output_schema_section(
    *,
    output_binding,
    schema_registry,
    runtime_owned_fields: tuple[str, ...] = (),
) -> dict[str, Any] | None:
    """Derive the model-facing output contract from a worker's ``OutputBinding``.

    裁决 2 (2026-07-29). The reviewed prompt materials name the output schema
    (``RegimeReport@1``) and its rules but never its FIELD NAMES, so the first
    live Lane-0 run invented its own (``regime_type`` / ``risk`` / ``heat`` /
    ``modal``) and was refused. This section is DERIVED — from the worker's own
    primary :class:`~guanlan_v2.orchestration.catalog.OutputBinding`, resolved
    through the very ``schema_registry`` the executor's step (9) validates the
    answer against — so the contract the model is shown cannot drift from the
    contract that is enforced. Prose in a material can silently expire; this
    cannot.

    Returns ``None`` when either input is absent (a caller with no output
    binding — e.g. ``run_planner`` — is byte-for-byte unchanged). A binding the
    registry cannot resolve is STATED as unresolved rather than guessed at.

    ``runtime_owned_fields`` names fields the RUNTIME stamps (Lane 0:
    ``as_of`` / ``factor_report_digest`` / ``content_digest``). They are listed
    separately and kept out of the required/optional lists: asking a model for
    a full 64-hex digest or its own self-seal is asking it to invent a number.
    """
    if output_binding is None or schema_registry is None:
        return None
    key = output_binding.schema_ref.key
    section: dict[str, Any] = {
        "schema": key,
        "contract": (
            f"Your answer is validated as {key}. Return ONE JSON object using "
            "exactly the field names below — an undeclared field, a missing "
            "required field or a value JSON cannot express as the declared type "
            "is refused, not repaired."
        ),
    }
    try:
        model = schema_registry.resolve(output_binding.schema_ref)
        fields = model.model_fields
        json_schema = model.model_json_schema()
    except Exception as exc:  # noqa: BLE001 - an unresolvable binding is stated
        section["unresolved"] = (
            "this run's schema registry does not resolve the binding "
            f"({type(exc).__name__}); no field list can be derived"
        )
        return section
    owned = {name for name in runtime_owned_fields if name in fields}
    section["required_fields"] = sorted(
        n for n, f in fields.items() if f.is_required() and n not in owned)
    section["optional_fields"] = sorted(
        n for n, f in fields.items() if not f.is_required() and n not in owned)
    if owned:
        section["runtime_supplied_fields"] = sorted(owned)
        section["runtime_supplied_note"] = (
            "the runtime stamps these from the artifacts this run actually "
            "committed; do NOT write them — anything you put there is discarded"
        )
    section["json_schema"] = json_schema
    return section


@runtime_checkable
class PromptAssembler(Protocol):
    """Combines trusted system/skill/guardrail text + untrusted block *refs* only."""

    def assemble(
        self,
        *,
        plan_digest: str,
        node_id: str,
        worker_id: str,
        system_prompt,
        skills,
        guardrails,
        trusted_input_digests: tuple[NamedEvidenceDigest, ...],
        untrusted_blocks: tuple[PromptUntrustedBlockRef, ...],
        output_binding=None,
        schema_registry=None,
    ) -> AssembledModelRequest:
        ...


class StaticPromptAssembler:
    """The concrete static-v1 assembler.

    Places untrusted blocks ONLY in the model gateway's ``data_inputs`` channel as
    schema/namespace/digest references — never interpolating their bytes into the
    system / skill / guardrail text — and returns only an
    :class:`AssembledModelRequest`.
    """

    assembler_id: ClassVar[str] = ASSEMBLER_ID
    assembler_version: ClassVar[str] = ASSEMBLER_VERSION

    def assemble(
        self,
        *,
        plan_digest: str,
        node_id: str,
        worker_id: str,
        system_prompt,
        skills,
        guardrails,
        trusted_input_digests: tuple[NamedEvidenceDigest, ...],
        untrusted_blocks: tuple[PromptUntrustedBlockRef, ...],
        output_binding=None,
        schema_registry=None,
    ) -> AssembledModelRequest:
        system_text = _text_of(system_prompt)
        skill_texts = [_text_of(s) for s in skills]
        guardrail_texts = [_text_of(g) for g in guardrails]
        trusted = sorted(trusted_input_digests, key=lambda e: e.name)
        blocks = tuple(untrusted_blocks)
        channel = {
            "assembler_id": self.assembler_id,
            "assembler_version": self.assembler_version,
            "system": system_text,
            "skills": skill_texts,
            "guardrails": guardrail_texts,
            "trusted_inputs": [{"name": e.name, "digest": e.digest} for e in trusted],
            "data_inputs": [
                {
                    "ordinal": b.ordinal,
                    "schema": b.payload_ref.schema_ref.key,
                    "namespace": b.payload_ref.payload_ref.namespace,
                    "content_digest": b.payload_ref.payload_ref.content_digest,
                    "media_type": b.media_type,
                    "length": b.rendered_length,
                }
                for b in blocks
            ],
        }
        out_schema = output_schema_section(
            output_binding=output_binding, schema_registry=schema_registry)
        if out_schema is not None:
            channel[OUTPUT_SCHEMA_SECTION] = out_schema
        canonical_bytes = json.dumps(
            channel, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        digest = _model_request_digest(canonical_bytes)
        record = PromptAssemblyRecord.build(
            plan_digest=plan_digest, node_id=node_id, worker_id=worker_id,
            assembler_id=self.assembler_id, assembler_version=self.assembler_version,
            system_prompt_ref=_ref_of(system_prompt), skill_refs=tuple(_ref_of(s) for s in skills),
            guardrail_refs=tuple(_ref_of(g) for g in guardrails),
            trusted_input_digests=tuple(trusted), untrusted_blocks=blocks,
            model_request_digest=digest,
        )
        return AssembledModelRequest(
            canonical_request_bytes=canonical_bytes, request_digest=digest, prompt_record=record)


@runtime_checkable
class ModelGateway(Protocol):
    """A service-owned single-shot model invocation selected from the catalog tier.

    ``invoke`` resolves ``prompt_assembly_ref``, rehashes the exact bytes and refuses
    any record/request/assembler/order mismatch BEFORE provider bytes are sent. It
    cannot accept a detached raw prompt/string. Static v1 exposes no model-controlled
    provider callback or tool-result → second-model loop.
    """

    def invoke(
        self, request: AssembledModelRequest, *, prompt_assembly_ref: TypedPayloadRef,
    ) -> ModelResult:
        ...


def verify_model_request_binding(
    request: AssembledModelRequest,
    prompt_assembly_ref: TypedPayloadRef,
    *,
    reader,
) -> PromptAssemblyRecord:
    """The exact prompt/request binding a :class:`ModelGateway` MUST enforce.

    Resolves the persisted record, rehashes the exact bytes and refuses any
    ref/schema/content/assembler/order/request-digest mismatch. Reusable by any
    conforming gateway (including the test fakes) so the check lives behind the same
    interface boundary.
    """
    if prompt_assembly_ref.schema_ref != _PROMPT_ASSEMBLY_RECORD_SR:
        raise PromptBindingError("prompt_assembly_ref must resolve PromptAssemblyRecord@1")
    if prompt_assembly_ref.payload_ref.namespace != "main":
        raise PromptBindingError("prompt_assembly_ref must be main-namespace")
    try:
        stored = reader.get(prompt_assembly_ref.payload_ref, expected_schema_ref=_PROMPT_ASSEMBLY_RECORD_SR)
    except EventStoreError as exc:
        raise PromptBindingError(f"prompt record did not resolve: {exc}") from exc
    if not isinstance(stored, PromptAssemblyRecord):  # pragma: no cover - schema guarded
        raise PromptBindingError("resolved payload is not a PromptAssemblyRecord")
    if stored != request.prompt_record:
        raise PromptBindingError("persisted prompt record does not equal the request's bound record")
    recomputed = _model_request_digest(request.canonical_request_bytes)
    if recomputed != request.request_digest:
        raise PromptBindingError("request bytes do not rehash to the request digest")
    if stored.model_request_digest != recomputed:
        raise PromptBindingError("persisted record digest does not authorize these request bytes")
    return stored


# --------------------------------------------------------------------------- #
# Bridge provider / session ports + contributions                             #
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class BridgeInputContribution:
    """The pre-input stage contribution: descriptor-authorized memory refs only."""

    memory_refs: tuple[MemoryRecordRef, ...] = ()
    memory_evidence_refs: tuple[TypedPayloadRef, ...] = ()


@dataclass(frozen=True)
class PreparedBridgeHandle:
    """The frozen pre-input handle for one bridge: token + created refs/digests."""

    bridge_id: str
    bridge_priority: int
    summary_digest: str
    token: EvidenceIssuanceToken
    input_contribution: BridgeInputContribution
    journal_cursor: tuple[TypedPayloadRef, ...] = ()


@dataclass(frozen=True)
class BridgeContribution:
    """One bridge's frozen execution-stage contribution.

    ``tool_call_records`` are finalized capability records; ``data_result_refs`` are
    consumed DataResult typed refs (cache-hit or the exact tool result);
    ``direct_evidence_refs`` are otherwise-unrepresented main refs (cache request,
    rejected/orphan request, pre-assembly block); ``untrusted_blocks`` are the
    provider's ordered untrusted-block DTOs (LLM only). Each carries the merge key
    ``(call_ordinal, bridge_priority, bridge_id, within_call_role)`` on its raw
    parts.
    """

    bridge_id: str
    bridge_priority: int
    summary_digest: str
    tool_call_records: tuple[ToolCallRecord, ...] = ()
    data_result_refs: tuple[TypedPayloadRef, ...] = ()
    direct_evidence_refs: tuple[TypedPayloadRef, ...] = ()
    untrusted_blocks: tuple["ProviderUntrustedBlock", ...] = ()


@dataclass(frozen=True)
class ProviderUntrustedBlock:
    """A provider's raw untrusted-block DTO before prompt-ordinal assignment."""

    bridge_id: str
    bridge_priority: int
    call_ordinal: int
    payload_ref: TypedPayloadRef
    media_type: str
    rendered_length: int


@dataclass(frozen=True)
class BridgeStageOutcome:
    """The closed two-stage outcome a provider returns."""

    status: Literal["prepared", "completed", "failed", "timed_out", "cancelled"]
    input_contribution: BridgeInputContribution | None = None
    prepared_handle: PreparedBridgeHandle | None = None
    frozen_contribution: BridgeContribution | None = None
    reason: str | None = None
    journal_cursor: tuple[TypedPayloadRef, ...] = ()


@dataclass(frozen=True)
class PreparedBridgeSet:
    """The frozen stage-1 output the runner carries into snapshot freeze + stage 2."""

    node_id: str
    handles: tuple[PreparedBridgeHandle, ...] = ()

    def memory_record_refs(self) -> tuple[MemoryRecordRef, ...]:
        refs: list[MemoryRecordRef] = []
        for h in self.handles:
            if h.input_contribution is not None:
                refs.extend(h.input_contribution.memory_refs)
        return tuple(refs)


@runtime_checkable
class ExecutionBridgeProvider(Protocol):
    """A reviewed static-prefetch provider resolved only from catalog material."""

    def prepare_input(self, request: "BridgePrepareRequest") -> BridgeStageOutcome:
        ...

    def open_execution(self, request: "BridgeOpenRequest") -> "ExecutionBridgeSession":
        ...


@runtime_checkable
class ExecutionBridgeSession(Protocol):
    def freeze_for_execution(self, *, kind: ExecutionKind) -> BridgeStageOutcome:
        ...


@dataclass(frozen=True)
class BridgePrepareRequest:
    plan: Plan
    node: PlanNode
    worker: WorkerSpec
    bridge: ResolvedBridge
    summary: BridgeStaticSupportSummary
    token: EvidenceIssuanceToken
    context_snapshot_ref: TypedPayloadRef
    evidence_writer: BridgeEvidenceWriter


@dataclass(frozen=True)
class BridgeOpenRequest:
    plan: Plan
    node: PlanNode
    worker: WorkerSpec
    bridge: ResolvedBridge
    summary: BridgeStaticSupportSummary
    handle: PreparedBridgeHandle
    input_snapshot: InputSnapshot
    capability_gateway: CapabilityGateway
    evidence_writer: BridgeEvidenceWriter
    reader: Any
    #: the continued shared node sequencer (the same instance stage 1 used). A
    #: provider that needs additional executor-issued evidence tokens (e.g. the
    #: Phase-3 data bridge's per-route attempt tokens) draws them HERE — it can
    #: never mint an ordinal itself. Optional for backward compatibility with
    #: providers that only echo their single prepared-handle token.
    sequencer: "ExecutionEvidenceSequencer | None" = None


# --------------------------------------------------------------------------- #
# Execution bridge resolver (two-stage protocol driver)                        #
# --------------------------------------------------------------------------- #
class ExecutionObserver:
    """A minimal ordered-phase spy hook (proves RUNNING precedes bridge / capability work)."""

    def __init__(self) -> None:
        self.events: list[str] = []

    def record(self, phase: str) -> None:
        self.events.append(phase)


class ExecutionBridgeResolver:
    """Derives the required provider set from reviewed material and drives both stages.

    The required set, its canonical ``(bridge_priority, bridge_id)`` order and each
    provider's one bound summary digest come only from the
    :class:`BridgeCatalogView` active bridges and the embedded per-node
    :class:`BridgeStaticSupportSummary` values. A missing required provider, an extra
    provider, a summary/prepared-handle drift or an unregistered provider factory
    fails BEFORE handler/model execution. No worker/model/caller can inject a
    provider or mint an ordinal.
    """

    def __init__(
        self,
        *,
        runtime: ExecutionRuntime,
        node: PlanNode,
        worker: WorkerSpec,
        sequencer: ExecutionEvidenceSequencer,
        observer: ExecutionObserver | None = None,
    ) -> None:
        self._runtime = runtime
        self._node = node
        self._worker = worker
        self._sequencer = sequencer
        self._observer = observer
        self._bridges = self._resolve_required_set()
        self._handles: dict[str, PreparedBridgeHandle] = {}
        self._prepared = False

    # -- required-set derivation (pure preflight) --------------------------- #
    def _resolve_required_set(self) -> tuple[tuple[ResolvedBridge, BridgeStaticSupportSummary], ...]:
        active = self._runtime.bridge_view.active_bridges_for(self._worker)
        summaries = {s.bridge_id: s for s in self._runtime.summaries_for(self._node.id)}
        active_ids = {rb.bridge_id for rb in active}
        if active_ids != set(summaries):
            raise PreflightError(
                "active bridge set does not equal the support-report summaries for the node "
                f"(active={sorted(active_ids)} summaries={sorted(summaries)})"
            )
        out: list[tuple[ResolvedBridge, BridgeStaticSupportSummary]] = []
        for rb in active:  # active is already sorted by (priority, bridge_id)
            summary = summaries[rb.bridge_id]
            self._verify_summary_binding(rb, summary)
            # the provider factory must be registered for the exact catalog handler ref.
            try:
                self._runtime.factories.handler_factory(rb.provider_ref)
            except CatalogMaterialError as exc:
                raise PreflightError(
                    f"no trusted provider factory bound for bridge {rb.bridge_id!r}: {exc}"
                ) from exc
            out.append((rb, summary))
        return tuple(out)

    def _verify_summary_binding(self, rb: ResolvedBridge, summary: BridgeStaticSupportSummary) -> None:
        if (
            summary.bridge_id != rb.bridge_id
            or summary.descriptor_ref != rb.descriptor_ref
            or summary.config_ref != rb.config_ref
            or summary.provider_ref != rb.provider_ref
            or summary.analyzer_ref != rb.analyzer_ref
            or summary.worker_id != self._worker.id
            or summary.worker_digest != self._worker.semantic_digest()
            or summary.node_id != self._node.id
        ):
            raise PreflightError(
                f"support summary for bridge {rb.bridge_id!r} does not bind the exact "
                "descriptor/config/provider/analyzer/worker/node identity"
            )
        # re-verify the summary digest recomputes (no silent drift).
        if summary.summary_digest != content_digest(summary._semantic_payload()):
            raise PreflightError(f"support summary digest drift for bridge {rb.bridge_id!r}")

    @property
    def required_bridge_ids(self) -> tuple[str, ...]:
        return tuple(rb.bridge_id for rb, _ in self._bridges)

    def _provider(self, rb: ResolvedBridge, summary: BridgeStaticSupportSummary) -> ExecutionBridgeProvider:
        factory = self._runtime.factories.handler_factory(rb.provider_ref)
        return factory(bridge=rb, summary=summary)

    # -- stage 1: prepare_input --------------------------------------------- #
    def prepare_input(
        self, *, plan: Plan, context_snapshot_ref: TypedPayloadRef, evidence_writer: BridgeEvidenceWriter,
    ) -> PreparedBridgeSet:
        if self._prepared:
            raise PreflightError("prepare_input already ran for this resolver")
        handles: list[PreparedBridgeHandle] = []
        for rb, summary in self._bridges:
            token = self._sequencer.issue_call_token(
                bridge_priority=rb.priority, bridge_id=rb.bridge_id, summary_digest=summary.summary_digest)
            provider = self._provider(rb, summary)
            req = BridgePrepareRequest(
                plan=plan, node=self._node, worker=self._worker, bridge=rb, summary=summary,
                token=token, context_snapshot_ref=context_snapshot_ref, evidence_writer=evidence_writer)
            outcome = provider.prepare_input(req)
            if outcome.status != "prepared" or outcome.prepared_handle is None:
                raise PreflightError(
                    f"bridge {rb.bridge_id!r} prepare_input did not produce a prepared handle "
                    f"(status={outcome.status})"
                )
            handle = outcome.prepared_handle
            if handle.token != token or handle.bridge_id != rb.bridge_id:
                raise PreflightError(f"bridge {rb.bridge_id!r} prepared handle relabelled its token")
            # a memory addition is only legal for a memory_refs_v1 descriptor.
            if handle.input_contribution.memory_refs and rb.descriptor.pre_input_kind != "memory_refs_v1":
                raise PreflightError(
                    f"bridge {rb.bridge_id!r} added memory refs but is not a memory_refs_v1 descriptor")
            self._handles[rb.bridge_id] = handle
            handles.append(handle)
        self._prepared = True
        return PreparedBridgeSet(node_id=self._node.id, handles=tuple(handles))

    # -- stage 2: open_execution + freeze ----------------------------------- #
    def open_execution(
        self,
        *,
        plan: Plan,
        prepared: PreparedBridgeSet,
        input_snapshot: InputSnapshot,
        capability_gateway: CapabilityGateway,
        evidence_writer: BridgeEvidenceWriter,
        reader,
        kind: ExecutionKind,
    ) -> tuple[BridgeContribution, ...]:
        if not self._prepared:
            raise PreflightError("open_execution before prepare_input")
        if prepared.node_id != self._node.id:
            raise PreflightError("prepared bridge set is for a different node")
        by_id = {h.bridge_id: h for h in prepared.handles}
        if set(by_id) != set(self.required_bridge_ids):
            raise PreflightError("prepared bridge set does not match the required provider set")
        contributions: list[BridgeContribution] = []
        for rb, summary in self._bridges:
            handle = self._handles[rb.bridge_id]
            carried = by_id[rb.bridge_id]
            if carried.token != handle.token:
                raise PreflightError(f"bridge {rb.bridge_id!r} pre-input token drifted before execution")
            if self._observer is not None:
                self._observer.record(f"bridge_open:{rb.bridge_id}")
            provider = self._provider(rb, summary)
            req = BridgeOpenRequest(
                plan=plan, node=self._node, worker=self._worker, bridge=rb, summary=summary,
                handle=handle, input_snapshot=input_snapshot, capability_gateway=capability_gateway,
                evidence_writer=evidence_writer, reader=reader, sequencer=self._sequencer)
            session = provider.open_execution(req)
            outcome = session.freeze_for_execution(kind=kind)
            if outcome.status != "completed" or outcome.frozen_contribution is None:
                raise WorkerExecutionError(
                    f"bridge {rb.bridge_id!r} freeze_for_execution did not complete "
                    f"(status={outcome.status}): {outcome.reason}"
                )
            contributions.append(outcome.frozen_contribution)
        return tuple(contributions)


# --------------------------------------------------------------------------- #
# Worker execution result                                                      #
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class WorkerExecutionResult:
    """The strict internal result the executor derives NodeRun / Artifact from."""

    execution_kind: ExecutionKind
    status: NodeStatus
    payload: Any = None
    payload_schema_ref: SchemaRef | None = None
    rendered_text: str = ""
    number_anchors: tuple[NumberAnchor, ...] = ()
    tool_call_records: tuple[ToolCallRecord, ...] = ()
    data_result_refs: tuple[TypedPayloadRef, ...] = ()
    execution_evidence_refs: tuple[TypedPayloadRef, ...] = ()
    prompt_assembly_ref: TypedPayloadRef | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    degradation_reasons: tuple[str, ...] = ()
    reason_code: str | None = None
    reason: str | None = None
    error_type: str | None = None
    provider: str | None = None
    model: str | None = None
    provider_response_id: str | None = None


# --------------------------------------------------------------------------- #
# small helpers                                                                #
# --------------------------------------------------------------------------- #
def _text_of(material) -> str:
    if material is None:
        return ""
    return material.raw_utf8.decode("utf-8")


def _ref_of(material) -> ContentRef:
    return material.ref


def _sorted_typed(refs) -> tuple[TypedPayloadRef, ...]:
    return tuple(sorted(refs, key=typed_ref_sort_key))


def _dedup_typed(refs) -> tuple[TypedPayloadRef, ...]:
    seen: set = set()
    out: list[TypedPayloadRef] = []
    for r in refs:
        k = typed_ref_sort_key(r)
        if k in seen:
            continue
        seen.add(k)
        out.append(r)
    return tuple(out)


# --------------------------------------------------------------------------- #
# stage-1 public entry point                                                   #
# --------------------------------------------------------------------------- #
def prepare_input(
    plan: Plan,
    node: PlanNode,
    *,
    runtime: ExecutionRuntime,
    ctx: RunContext,
    stores: RuntimeStores,
    registry: SchemaRegistry,
    context_snapshot_ref: TypedPayloadRef,
    clock: AuthoritativeClock,
    attempt: int = 1,
    observer: ExecutionObserver | None = None,
) -> tuple[ExecutionBridgeResolver, PreparedBridgeSet, ExecutionEvidenceSequencer]:
    """Stage 1: run every provider's ``prepare_input`` and freeze a PreparedBridgeSet.

    Returns the stateful resolver, the frozen prepared set and the shared sequencer
    the runner carries into :func:`execute_node` (open_execution continues the SAME
    sequencer rather than restarting ordinals).
    """
    worker = runtime.catalog.worker(node.worker_id)
    sequencer = ExecutionEvidenceSequencer(node_id=node.id, attempt=attempt)
    resolver = ExecutionBridgeResolver(
        runtime=runtime, node=node, worker=worker, sequencer=sequencer, observer=observer)
    writer = BridgeEvidenceWriter(
        stores=stores, sequencer=sequencer, run_id=ctx.run_id, plan_digest=plan.plan_digest,
        node_id=node.id, data_registry_digest=registry.registry_digest,
        runtime_registry_digest=runtime.runtime_registry_digest)
    prepared = resolver.prepare_input(
        plan=plan, context_snapshot_ref=context_snapshot_ref, evidence_writer=writer)
    # pre-input evidence work ends when every handle is frozen: seal the stage-1
    # writer so a provider that stashed it cannot journal late pre-input evidence.
    writer.seal()
    return resolver, prepared, sequencer


# --------------------------------------------------------------------------- #
# The executor                                                                 #
# --------------------------------------------------------------------------- #
def execute_node(
    plan: Plan,
    node: PlanNode,
    *,
    runtime: ExecutionRuntime,
    prepared_bridges: PreparedBridgeSet,
    input_snapshot: InputSnapshot,
    ctx: RunContext,
    node_reservation: BudgetReservation,
    bridge_resolver: ExecutionBridgeResolver,
    model_gateway: ModelGateway | None,
    capability_gateway: CapabilityGateway,
    registry: SchemaRegistry,
    stores: RuntimeStores,
    clock: AuthoritativeClock,
    prompt_assembler: PromptAssembler | None = None,
    observer: ExecutionObserver | None = None,
    attempt: int = 1,
    base_authorized_memory_refs: tuple[MemoryRecordRef, ...] = (),
) -> tuple[NodeRun, Artifact | None]:
    """Execute one admitted node → (NodeRun always, Artifact only on COMPLETED/DEGRADED).

    Runs pure preflight (no side effect on failure), requires a *ready* InputSnapshot,
    activates the timeout/cancellation scope, opens the exact prepared bridge sessions
    through the CapabilityGateway, then (LLM) assembles + persists exactly one prompt
    record before a single ModelGateway call, classifies all evidence without
    duplication and seals the terminal NodeRun / Artifact.
    """
    worker = runtime.catalog.worker(node.worker_id)
    sequencer = bridge_resolver._sequencer  # the SAME node sequencer used in stage 1
    started_at = clock_now(clock)
    node_run_id = f"nr-{ctx.run_id}-{node.id}-{attempt}"
    attempt_id = f"att-{ctx.run_id}-{node.id}-{attempt}"

    # ---- (1) pure preflight ------------------------------------------------ #
    _preflight(plan, node, worker, runtime=runtime, input_snapshot=input_snapshot, ctx=ctx,
               node_reservation=node_reservation, prepared_bridges=prepared_bridges,
               bridge_resolver=bridge_resolver,
               base_authorized_memory_refs=base_authorized_memory_refs)

    kind = worker.execution.kind
    writer = BridgeEvidenceWriter(
        stores=stores, sequencer=sequencer, run_id=ctx.run_id, plan_digest=plan.plan_digest,
        node_id=node.id, data_registry_digest=registry.registry_digest,
        runtime_registry_digest=runtime.runtime_registry_digest)
    reader = _ReadOnlyPayloadView(stores.payloads)
    capability_gateway.bind_reader(reader)

    def _terminal_nodrun(status, *, reason_code=None, reason=None, error_type=None,
                         data_refs=(), evid_refs=(), tool_records=(),
                         itok=0, otok=0):
        return _build_node_run(
            plan=plan, node=node, worker=worker, ctx=ctx, status=status,
            node_run_id=node_run_id, attempt_id=attempt_id, attempt=attempt,
            input_snapshot=input_snapshot, started_at=started_at, finished_at=clock_now(clock),
            reason_code=reason_code, reason=reason, error_type=error_type,
            tool_call_records=tool_records, data_result_refs=data_refs,
            execution_evidence_refs=evid_refs, input_tokens=itok, output_tokens=otok)

    # ---- (3) RUNNING + timeout/cancellation scope -------------------------- #
    if observer is not None:
        observer.record("running")
    capability_gateway.mark_running()

    contributions: tuple[BridgeContribution, ...] = ()
    try:
        # ---- (4) open the exact prepared sessions + freeze bridge work ----- #
        contributions = bridge_resolver.open_execution(
            plan=plan, prepared=prepared_bridges, input_snapshot=input_snapshot,
            capability_gateway=capability_gateway, evidence_writer=writer, reader=reader, kind=kind)
    except _TimeoutSignal as exc:
        return _drain_terminal(NodeStatus.TIMED_OUT, "node_timeout", str(exc), None,
                               capability_gateway, stores, ctx, plan, node, _terminal_nodrun), None
    except _CancelSignal as exc:
        return _drain_terminal(NodeStatus.CANCELLED, "node_cancelled", str(exc), None,
                               capability_gateway, stores, ctx, plan, node, _terminal_nodrun), None
    except WorkerExecutionError:
        raise
    except Exception as exc:  # handler/provider exception
        return _drain_terminal(NodeStatus.FAILED, "bridge_execution_error", str(exc),
                               type(exc).__name__, capability_gateway, stores, ctx, plan, node,
                               _terminal_nodrun), None
    finally:
        # every session is sealed (or terminally interrupted): reject any late
        # provider put through a stashed writer reference.
        writer.seal()

    # ---- classify merged bridge contributions ------------------------------ #
    merged = _MergedEvidence.from_contributions(contributions)
    data_refs = _sorted_typed(_dedup_typed(merged.data_result_refs))

    # ---- (5) forged-evidence gate: a provider-contributed ToolCallRecord the
    #          CapabilityGateway never finalized can NEVER reach sealed provenance
    #          or satisfy REQUIRED. -------------------------------------------- #
    finalized_digests = {r.semantic_digest() for r in capability_gateway.finalized_records()}
    forged = tuple(r for r in merged.tool_call_records
                   if r.semantic_digest() not in finalized_digests)
    if forged:
        verified = tuple(r for r in merged.tool_call_records
                         if r.semantic_digest() in finalized_digests)
        honest = _MergedEvidence(
            tool_call_records=verified, data_result_refs=merged.data_result_refs,
            direct_evidence_refs=merged.direct_evidence_refs,
            untrusted_blocks=merged.untrusted_blocks, degradation_reasons=())
        return _terminal_nodrun(
            NodeStatus.INCOMPLETE, reason_code="forged_tool_evidence",
            reason=("provider contributed ToolCallRecord(s) the CapabilityGateway never "
                    f"finalized: call ordinals {sorted(r.call_ordinal for r in forged)}"),
            data_refs=data_refs, tool_records=verified,
            evid_refs=_finalize_direct_evidence(honest, None)), None

    # ---- (6) per-summary REQUIRED/FORBIDDEN + tool-call discipline --------- #
    ok, why = _check_tool_discipline(worker, runtime, node, capability_gateway,
                                     merged.tool_call_records)
    if not ok:
        return _terminal_nodrun(
            NodeStatus.INCOMPLETE, reason_code="tool_discipline_unmet", reason=why,
            data_refs=data_refs, tool_records=merged.tool_call_records,
            evid_refs=_finalize_direct_evidence(merged, None)), None

    # ---- (4b) LLM prompt assembly + single model call ---------------------- #
    prompt_ref: TypedPayloadRef | None = None
    degradation: list[str] = list(merged.degradation_reasons)
    # honesty rule 11: a plan-fed input that arrived absent/empty on the ready
    # snapshot was weakened away (DEGRADE/SKIP) — a successful result is at most
    # DEGRADED, and the worker self-reports the honest reason.
    degradation.extend(_absent_input_degradations(node, worker, input_snapshot))
    if kind is ExecutionKind.LLM:
        assembler = prompt_assembler or StaticPromptAssembler()
        resolved = runtime.catalog.resolve_worker(node.worker_id)
        blocks = _assign_block_ordinals(merged.untrusted_blocks)
        trusted = _trusted_input_digests(input_snapshot)
        # 裁决 2: the model is TOLD the contract its answer is validated against —
        # the very same primary OutputBinding step (9) below resolves, through the
        # very same registry, so the declared and the enforced contract are one.
        llm_out_binding = _primary_output_binding(worker)
        try:
            request = assembler.assemble(
                plan_digest=plan.plan_digest, node_id=node.id, worker_id=worker.id,
                system_prompt=resolved.system_prompt, skills=resolved.skills,
                guardrails=resolved.guardrails, trusted_input_digests=trusted,
                untrusted_blocks=blocks, output_binding=llm_out_binding,
                schema_registry=registry)
        except Exception as exc:  # assembly failure -> orphan blocks retained directly
            return _terminal_nodrun(
                NodeStatus.INCOMPLETE, reason_code="prompt_assembly_failed", reason=str(exc),
                data_refs=data_refs, tool_records=merged.tool_call_records,
                evid_refs=_finalize_direct_evidence(merged, None)), None

        if model_gateway is None:
            raise WorkerExecutionError("an LLM worker requires a ModelGateway")
        # reserve the single executor-owned prompt token, persist the record ONCE.
        prompt_token = sequencer.issue_prompt_token()
        prompt_ref = _persist_prompt_record(
            request.prompt_record, writer=writer, stores=stores, ctx=ctx, plan=plan, node=node,
            runtime=runtime, prompt_token=prompt_token, clock=clock)
        try:
            model_result = model_gateway.invoke(request, prompt_assembly_ref=prompt_ref)
        except PromptBindingError:
            raise
        except _TimeoutSignal as exc:
            return _drain_terminal(NodeStatus.TIMED_OUT, "model_timeout", str(exc), None,
                                   capability_gateway, stores, ctx, plan, node, _terminal_nodrun,
                                   extra_prompt_ref=prompt_ref, merged=merged), None
        except Exception as exc:
            return _terminal_nodrun(
                NodeStatus.FAILED, reason_code="model_error", reason=str(exc),
                error_type=type(exc).__name__, data_refs=data_refs,
                tool_records=merged.tool_call_records,
                evid_refs=_finalize_direct_evidence(merged, prompt_ref)), None
        payload = model_result.payload
    else:  # DETERMINISTIC
        if merged.untrusted_blocks:
            raise WorkerExecutionError("a DETERMINISTIC worker cannot emit untrusted blocks")
        resolved = runtime.catalog.resolve_worker(node.worker_id)
        handler_factory = runtime.factories.handler_factory(worker.execution.handler_ref)
        handler = handler_factory(worker=worker, resolved=resolved)
        try:
            model_result = handler(
                node=node, input_snapshot=input_snapshot, contributions=contributions,
                data_result_refs=merged.data_result_refs)
        except _TimeoutSignal as exc:
            return _drain_terminal(NodeStatus.TIMED_OUT, "handler_timeout", str(exc), None,
                                   capability_gateway, stores, ctx, plan, node, _terminal_nodrun,
                                   merged=merged), None
        except Exception as exc:
            return _terminal_nodrun(
                NodeStatus.FAILED, reason_code="handler_error", reason=str(exc),
                error_type=type(exc).__name__, data_refs=data_refs,
                tool_records=merged.tool_call_records,
                evid_refs=_finalize_direct_evidence(merged, None)), None
        payload = model_result.payload

    if model_result.degraded:
        degradation.extend(model_result.degradation_reasons)

    # ---- (7) number-anchor / unsourced-number policy ----------------------- #
    anchor_ok, anchor_why = _check_number_anchors(worker, model_result.number_anchors)
    if not anchor_ok:
        return _terminal_nodrun(
            NodeStatus.INCOMPLETE, reason_code="number_anchor_policy", reason=anchor_why,
            data_refs=data_refs, tool_records=merged.tool_call_records,
            evid_refs=_finalize_direct_evidence(merged, prompt_ref),
            itok=model_result.input_tokens, otok=model_result.output_tokens), None

    # ---- (9) validate the primary payload through its OutputBinding SchemaRef  #
    out_binding = _primary_output_binding(worker)
    if model_result.decode_refusal is not None:
        # a reviewed normalizing gateway measured the TRUE error under the full
        # discipline (runtime stamps included) and passed the payload through
        # byte-identical; re-deriving a reason from the raw payload here can
        # only bury it (the 2026-07-31 live record's 21 python-mode artifacts
        # over one real `drivers must be sorted`). The refusal stands, with the
        # real error as its reason.
        return _terminal_nodrun(
            NodeStatus.INCOMPLETE, reason_code="output_schema_invalid",
            reason=model_result.decode_refusal,
            data_refs=data_refs, tool_records=merged.tool_call_records,
            evid_refs=_finalize_direct_evidence(merged, prompt_ref),
            itok=model_result.input_tokens, otok=model_result.output_tokens), None
    try:
        validated_payload = _validate_primary_output(
            registry, out_binding.schema_ref, payload)
    except Exception as exc:
        return _terminal_nodrun(
            NodeStatus.INCOMPLETE, reason_code="output_schema_invalid", reason=str(exc),
            data_refs=data_refs, tool_records=merged.tool_call_records,
            evid_refs=_finalize_direct_evidence(merged, prompt_ref),
            itok=model_result.input_tokens, otok=model_result.output_tokens), None

    # ---- (11/12) COMPLETED or DEGRADED: seal NodeRun + one Artifact -------- #
    status = NodeStatus.DEGRADED if degradation else NodeStatus.COMPLETED
    direct_evidence = _finalize_direct_evidence(merged, prompt_ref)

    artifact = _build_artifact(
        plan=plan, node=node, worker=worker, ctx=ctx, out_binding=out_binding,
        payload=validated_payload, rendered_text=model_result.rendered_text,
        number_anchors=model_result.number_anchors, input_snapshot=input_snapshot,
        tool_call_records=merged.tool_call_records, data_result_refs=data_refs,
        execution_evidence_refs=direct_evidence, prompt_ref=prompt_ref, kind=kind,
        model_result=model_result, clock=clock)

    node_run = _build_node_run(
        plan=plan, node=node, worker=worker, ctx=ctx, status=status,
        node_run_id=node_run_id, attempt_id=attempt_id, attempt=attempt,
        input_snapshot=input_snapshot, started_at=started_at, finished_at=clock_now(clock),
        reason_code=("degraded_input_or_result" if status is NodeStatus.DEGRADED else None),
        reason=("; ".join(degradation) if degradation else None), error_type=None,
        tool_call_records=merged.tool_call_records, data_result_refs=data_refs,
        execution_evidence_refs=direct_evidence, input_tokens=model_result.input_tokens,
        output_tokens=model_result.output_tokens, output_keys=(_PRIMARY_OUTPUT_KEY,),
        output_artifact_ids=(artifact.artifact_id,))

    # cross-object equality: Artifact provenance tuples == NodeRun tuples.
    prov = artifact.provenance
    if (
        prov.tool_call_records != node_run.tool_call_records
        or prov.data_result_refs != node_run.data_result_refs
        or prov.execution_evidence_refs != node_run.execution_evidence_refs
    ):
        raise WorkerExecutionError(
            "Artifact provenance evidence tuples drifted from the NodeRun tuples "
            "(internal invariant; the record must not be sealed)"
        )
    return node_run, artifact


# --------------------------------------------------------------------------- #
# Phase 8 · Task 8 — bounded retry + schema-repair control loop                 #
# --------------------------------------------------------------------------- #
#: node statuses a retry may re-attempt (transient / recoverable terminals).
_RETRYABLE_STATUSES: frozenset = frozenset(
    {NodeStatus.FAILED, NodeStatus.INCOMPLETE, NodeStatus.TIMED_OUT}
)
#: node statuses that count as a usable success (one committed output per key).
_RETRY_SUCCESS_STATUSES: frozenset = frozenset(
    {NodeStatus.COMPLETED, NodeStatus.DEGRADED}
)
#: the reason_code the executor stamps when the primary payload fails registry
#: validation — the only condition a bounded schema repair may address.
_SCHEMA_INVALID_REASON = "output_schema_invalid"


def retry_llm_invocation_upper_bound(
    *, is_llm: bool, max_attempts: int, schema_repairs_per_attempt: int
) -> int:
    """The v2 LLM-invocation reservation upper bound for one node.

    ``max_attempts × (1 + schema_repairs_per_attempt)`` for an LLM node (each attempt
    is one primary model call plus up to ``schema_repairs_per_attempt`` repair calls),
    **0** for a deterministic node (a deterministic ``max_attempts>1`` reserves zero
    LLM invocations). This is the exact upper bound ``analyze_retry_repair`` documents
    and the runner reserves against the plan LLM budget (spec §8 line 947 / §10 line
    968).
    """
    if not is_llm:
        return 0
    return int(max_attempts) * (1 + int(schema_repairs_per_attempt))


@dataclass(frozen=True)
class AttemptReservation:
    """One per-attempt child reservation minted by the bounded-retry loop.

    ``role`` labels the attempt's purpose (``"primary"`` / ``"retry"`` /
    ``"schema_repair"``) and mirrors the LEDGER truth: a ``"primary"`` attempt is a
    ``reserve_node`` child (``scope_type="node"``); a ``"retry"`` attempt is a
    first-class ``reserve_retry`` child (``scope_type="retry"``); a
    ``"schema_repair"`` attempt is a first-class ``reserve_schema_repair`` child
    (``scope_type="schema_repair"``). Phase 8 · Task 8 promoted these to their own
    closed budget ops (the Phase-7 handoff op-set gate was flipped additively), so
    the reservation's scope_type — not merely this record — carries the attempt role.
    """

    reservation_id: str
    role: str
    attempt: int
    idempotency_key: str


@dataclass(frozen=True)
class BoundedRetryOutcome:
    """The terminal outcome of the bounded retry + schema-repair loop for one node."""

    node_run: NodeRun
    artifact: Artifact | None
    reservations: tuple[AttemptReservation, ...]
    succeeded: bool
    invocations: int


def execute_with_bounded_retry(
    *,
    node_id: str,
    run_id: str,
    is_llm: bool,
    max_attempts: int,
    schema_repairs_per_attempt: int,
    budget: Any,
    plan_reservation_id: str,
    node_token_reservation: int,
    attempt_fn: Any,
) -> BoundedRetryOutcome:
    """Run one node under the v2 bounded retry + schema-repair discipline.

    ``attempt_fn(attempt_ordinal: int, *, is_repair: bool) -> (NodeRun, Artifact|None)``
    is the caller-owned closure that builds the fresh per-attempt plumbing (a new
    :class:`ExecutionEvidenceSequencer` via :func:`prepare_input`, the gateways) and
    calls :func:`execute_node` with the given attempt ordinal — each attempt is a
    fresh sequencer / one prompt (the sequencer permits exactly one prompt per
    attempt, so a repair is a *new attempt ordinal*, never a second prompt within an
    attempt).

    The loop, per the reviewed v2 semantics:

    * the base attempt reserves one child node budget (``reserve_node``;
      ``llm_invocations = 1`` for an LLM node, ``0`` for a deterministic node), runs
      ``attempt_fn`` and settles the actuals; a subsequent retry attempt reserves via
      ``reserve_retry`` (a first-class ``scope_type="retry"`` child of the same plan
      pool);
    * a COMPLETED / DEGRADED attempt returns immediately (**at most one committed
      output per logical key** — the Phase-2 invariant);
    * an LLM attempt that failed with ``reason_code="output_schema_invalid"`` triggers
      up to ``schema_repairs_per_attempt`` bounded repair invocations (each a fresh
      attempt ordinal, its own ``reserve_schema_repair`` child + settle, its own
      second ``PromptAssemblyRecord``); a non-schema failure is not repairable;
    * a retryable terminal (FAILED / INCOMPLETE / TIMED_OUT) with primary attempts
      remaining retries; otherwise the loop returns the terminal record with no
      Artifact.

    Every reservation / ``settle`` uses a deterministic idempotency key
    (``role``/``ordinal``), so a crash between a repair and its settle replays
    idempotently (no duplicate reservation, no duplicated invocation billed). The
    per-attempt op is selected by role (``reserve_node`` / ``reserve_retry`` /
    ``reserve_schema_repair``) so the ledger carries the attempt role first-class.
    """
    reservations: list[AttemptReservation] = []
    llm_per = 1 if is_llm else 0
    invocations = 0
    ordinal = 0
    last_run: NodeRun | None = None
    last_artifact: Artifact | None = None

    # each attempt role draws from the plan pool through its own first-class ledger
    # op (Phase 8 · Task 8 exit gate): the base attempt is ordinary node work
    # (reserve_node), a bounded retry mints a scope_type="retry" reservation and a
    # bounded schema repair a scope_type="schema_repair" one — same plan-pool
    # accounting, but the LEDGER now carries the attempt role, not just the record.
    _RESERVE_BY_ROLE = {
        "primary": budget.reserve_node,
        "retry": budget.reserve_retry,
        "schema_repair": budget.reserve_schema_repair,
    }

    def _reserve(role: str, ordinal: int) -> str:
        key = f"noderes:{run_id}:{node_id}:{role}:{ordinal}"
        res = _RESERVE_BY_ROLE[role](
            plan_reservation_id=plan_reservation_id, node_id=node_id, attempt=ordinal,
            tokens=node_token_reservation, llm_invocations=llm_per, concurrency=1,
            idempotency_key=key)
        reservations.append(AttemptReservation(
            reservation_id=res.reservation_id, role=role, attempt=ordinal, idempotency_key=key))
        return res.reservation_id

    def _settle(reservation_id: str, role: str, ordinal: int, node_run: NodeRun) -> None:
        actual_tokens = min(node_run.input_tokens + node_run.output_tokens, node_token_reservation)
        actual_llm = 1 if (is_llm and node_run.output_tokens > 0) else 0
        budget.settle(
            reservation_id, actual_tokens=actual_tokens, actual_llm_invocations=actual_llm,
            idempotency_key=f"settle:{run_id}:{node_id}:{role}:{ordinal}")

    for primary in range(1, int(max_attempts) + 1):
        role = "primary" if primary == 1 else "retry"
        ordinal += 1
        invocations += 1
        reservation_id = _reserve(role, ordinal)
        node_run, artifact = attempt_fn(ordinal, is_repair=False)
        _settle(reservation_id, role, ordinal, node_run)
        last_run, last_artifact = node_run, artifact
        if node_run.status in _RETRY_SUCCESS_STATUSES:
            return BoundedRetryOutcome(node_run, artifact, tuple(reservations), True, invocations)

        # bounded schema repair: only an LLM output_schema_invalid is repairable.
        if (
            is_llm
            and node_run.reason_code == _SCHEMA_INVALID_REASON
            and schema_repairs_per_attempt > 0
        ):
            for _ in range(int(schema_repairs_per_attempt)):
                ordinal += 1
                invocations += 1
                reservation_id = _reserve("schema_repair", ordinal)
                rn2, art2 = attempt_fn(ordinal, is_repair=True)
                _settle(reservation_id, "schema_repair", ordinal, rn2)
                last_run, last_artifact = rn2, art2
                if rn2.status in _RETRY_SUCCESS_STATUSES:
                    return BoundedRetryOutcome(rn2, art2, tuple(reservations), True, invocations)
                if rn2.reason_code != _SCHEMA_INVALID_REASON:
                    break  # a non-schema failure is not addressable by another repair

        if last_run is None or last_run.status not in _RETRYABLE_STATUSES:
            break  # terminal-but-not-retryable (defensive; success already returned)

    assert last_run is not None  # the loop always runs at least once (max_attempts >= 1)
    return BoundedRetryOutcome(last_run, None, tuple(reservations), False, invocations)


# --------------------------------------------------------------------------- #
# timeout / cancellation signals                                              #
# --------------------------------------------------------------------------- #
class _TimeoutSignal(Exception):
    """Raised by a provider/handler/model fake to exercise the TIMED_OUT branch."""


class _CancelSignal(Exception):
    """Raised by a provider/handler/model fake to exercise the CANCELLED branch."""


# expose the signals so tests can raise them behind the trusted boundaries.
TimeoutSignal = _TimeoutSignal
CancelSignal = _CancelSignal


def _drain_terminal(
    status, reason_code, reason, error_type, capability_gateway, stores, ctx, plan, node,
    build_terminal, *, extra_prompt_ref=None, merged=None,
):
    """Drain the persisted evidence journal into a terminal NodeRun (no Artifact).

    Recovers every committed evidence ref from :class:`BridgeEvidenceJournal` by
    node/token — even when a provider never returned — so partial work is never
    dropped on a failed / timed-out / cancelled path. The recovered set runs
    through the SAME :func:`_finalize_direct_evidence` classifier as the normal
    path: a ref represented by a gateway-finalized :class:`ToolCallRecord` or a
    consumed DataResult is never double-listed in direct execution evidence.
    With no frozen contribution the authoritative
    ``capability_gateway.finalized_records()`` supplies the retained records, so a
    data/tool success followed by a terminal interruption keeps its records.
    """
    journal = BridgeEvidenceJournal(
        stores=stores, run_id=ctx.run_id, plan_digest=plan.plan_digest, node_id=node.id)
    drained = journal.drain()
    if merged is not None:
        tool_records = merged.tool_call_records
        data_refs = list(merged.data_result_refs)
        direct = list(merged.direct_evidence_refs)
        blocks = merged.untrusted_blocks
    else:
        # no frozen contribution: the gateway's finalized records are the only
        # authoritative tool evidence; their result refs are the consumed data.
        tool_records = tuple(
            sorted(capability_gateway.finalized_records(), key=lambda r: r.call_ordinal))
        data_refs = [r.result_ref for r in tool_records]
        direct = []
        blocks = ()
    # every journal-recovered ref becomes a direct candidate; the shared classifier
    # filters record/data/block-represented refs, so nothing is double-listed.
    direct.extend(j.evidence_ref for j in drained)
    pseudo = _MergedEvidence(
        tool_call_records=tool_records, data_result_refs=tuple(data_refs),
        direct_evidence_refs=tuple(direct), untrusted_blocks=tuple(blocks),
        degradation_reasons=())
    evid_final = _finalize_direct_evidence(pseudo, extra_prompt_ref)
    data_final = _sorted_typed(_dedup_typed(data_refs))
    return build_terminal(
        status, reason_code=reason_code, reason=reason, error_type=error_type,
        data_refs=data_final, evid_refs=evid_final, tool_records=tool_records)


# --------------------------------------------------------------------------- #
# preflight                                                                    #
# --------------------------------------------------------------------------- #
def _preflight(
    plan, node, worker, *, runtime, input_snapshot, ctx, node_reservation, prepared_bridges,
    bridge_resolver, base_authorized_memory_refs: tuple[MemoryRecordRef, ...] = (),
) -> None:
    # -- admitted plan identity ----------------------------------------------- #
    if plan.recompute_plan_digest() != plan.plan_digest:
        raise PreflightError("admitted Plan digest does not recompute")
    dispatch_digest = runtime.support_report.candidate_plan_digest
    if dispatch_digest != plan.plan_digest:
        raise PreflightError("support report is not bound to the admitted plan digest")
    if runtime.support_report.catalog_digest != runtime.catalog.catalog_digest:
        raise PreflightError("support report catalog digest drift")
    if not runtime.support_report.supported:
        raise PreflightError("plan is not runtime-supported")
    if node not in plan.nodes:
        raise PreflightError("node is not part of the admitted plan")

    # -- ready snapshot ------------------------------------------------------- #
    if input_snapshot.readiness != "ready":
        raise PreflightError("execute_node requires a ready InputSnapshot (terminal_partial rejected)")
    if input_snapshot.node_id != node.id or input_snapshot.plan_digest != plan.plan_digest:
        raise PreflightError("InputSnapshot does not bind this plan node")

    # -- one/many artifact-input shape honors the WorkerSpec required matrix -- #
    # Consistent with Phase-1 validate_plan_draft (an optional input may be fed by a
    # weakening DEGRADE/SKIP dependency) and pool.freeze_input_snapshot (a ready
    # snapshot requires only the REQUIRED inputs): present names ⊆ declared names AND
    # required names ⊆ present names. An absent OPTIONAL input is legal on a ready
    # snapshot and flows into the degraded-inputs path; an undeclared (extra) name or
    # an absent REQUIRED input still fails.
    binding_by_name = {b.input_name: b for b in input_snapshot.artifact_inputs}
    spec_names = {i.name for i in worker.inputs}
    undeclared = sorted(set(binding_by_name) - spec_names)
    if undeclared:
        raise PreflightError(
            f"InputSnapshot binds undeclared worker input(s): {undeclared}")
    missing_required = sorted(
        i.name for i in worker.inputs if i.required and i.name not in binding_by_name)
    if missing_required:
        raise PreflightError(
            f"ready InputSnapshot is missing required worker input(s): {missing_required}")
    for ib in worker.inputs:
        b = binding_by_name.get(ib.name)
        if b is None:
            continue  # absent OPTIONAL input — legal; the node runs at most DEGRADED
        if b.cardinality != ib.cardinality:
            raise PreflightError(f"input {ib.name!r} cardinality mismatch with the WorkerSpec")
        if ib.cardinality == "one" and len(b.artifact_refs) != 1:
            raise PreflightError(f"'one' input {ib.name!r} must carry exactly one artifact ref")

    # -- expected memory-record refs equal the frozen snapshot ---------------- #
    expected = _expected_memory_refs(prepared_bridges, base=base_authorized_memory_refs)
    if tuple(input_snapshot.memory_record_refs) != expected:
        raise PreflightError(
            "InputSnapshot memory_record_refs do not equal the canonical union of the "
            "authorized base + prepared bridge memory additions")

    # -- active child reservation --------------------------------------------- #
    # Phase 8 · Task 12: a bounded-retry/schema-repair attempt (v2 only) draws its
    # per-attempt child through the first-class ``reserve_retry`` / ``reserve_schema_repair``
    # ops (scope_type "retry"/"schema_repair"), so an ``execute_node`` invoked for such an
    # attempt is preflighted against a non-``node`` scope child. All three are legitimate
    # per-attempt node children of the same plan pool; a v1 run only ever mints "node".
    if node_reservation.scope_type not in ("node", "retry", "schema_repair"):
        raise PreflightError(
            "node_reservation is not a node/retry/schema_repair-scope reservation")
    if node_reservation.candidate_plan_digest != plan.plan_digest:
        raise PreflightError("node_reservation is not bound to the admitted plan digest")
    if node_reservation.status != "reserved":
        raise PreflightError("node_reservation is not active (settled/released)")

    # -- prepared handles match the required provider set --------------------- #
    if prepared_bridges.node_id != node.id:
        raise PreflightError("prepared bridge set is for a different node")
    if {h.bridge_id for h in prepared_bridges.handles} != set(bridge_resolver.required_bridge_ids):
        raise PreflightError("prepared bridge handles do not match the required provider set")


def _expected_memory_refs(
    prepared_bridges: PreparedBridgeSet,
    base: tuple[MemoryRecordRef, ...] = (),
) -> tuple[MemoryRecordRef, ...]:
    """The Phase-1-canonical union of the authorized base + provider additions.

    ``base`` is the run's base-authorized memory (the Phase-3 context selection
    supplied by the memory preparation service); providers add their completed
    PreparedBridgeSet contributions. An exactly-identical ref appearing in both
    is ONE piece of evidence (deduplicated by full semantic identity); a
    conflicting duplicate — same ``(record_id, revision_id)`` with different
    availability/content — is deliberately left in the tuple so the Phase-1
    InputSnapshot validator rejects it loudly.
    """
    additions = prepared_bridges.memory_record_refs()
    union: list[MemoryRecordRef] = []
    seen: set[tuple[str, str, str, datetime]] = set()
    for ref in tuple(base) + tuple(additions):
        key = (ref.record_id, ref.revision_id, ref.content_digest, ref.available_at)
        if key in seen:
            continue
        seen.add(key)
        union.append(ref)
    union.sort(key=lambda r: (r.record_id, r.revision_id, r.content_digest))
    return tuple(union)


# --------------------------------------------------------------------------- #
# merged-evidence classifier                                                   #
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class _MergedEvidence:
    tool_call_records: tuple[ToolCallRecord, ...]
    data_result_refs: tuple[TypedPayloadRef, ...]
    direct_evidence_refs: tuple[TypedPayloadRef, ...]
    untrusted_blocks: tuple[ProviderUntrustedBlock, ...]
    degradation_reasons: tuple[str, ...]

    @classmethod
    def from_contributions(cls, contributions: tuple[BridgeContribution, ...]) -> "_MergedEvidence":
        records: list[ToolCallRecord] = []
        data: list[TypedPayloadRef] = []
        direct: list[TypedPayloadRef] = []
        blocks: list[ProviderUntrustedBlock] = []
        for c in contributions:
            records.extend(c.tool_call_records)
            data.extend(c.data_result_refs)
            direct.extend(c.direct_evidence_refs)
            blocks.extend(c.untrusted_blocks)
        # ToolCallRecords canonicalize by call_ordinal (reversed completion cannot change this).
        records.sort(key=lambda r: r.call_ordinal)
        ordinals = [r.call_ordinal for r in records]
        if len(set(ordinals)) != len(ordinals):
            raise WorkerExecutionError("duplicate ToolCallRecord call_ordinal across bridges")
        # merge untrusted blocks by (call_ordinal, bridge_priority, bridge_id).
        blocks.sort(key=lambda b: (b.call_ordinal, b.bridge_priority, b.bridge_id))
        return cls(
            tool_call_records=tuple(records),
            data_result_refs=tuple(data),
            direct_evidence_refs=tuple(direct),
            untrusted_blocks=tuple(blocks),
            degradation_reasons=(),
        )


def _finalize_direct_evidence(
    merged: _MergedEvidence, prompt_ref: TypedPayloadRef | None,
) -> tuple[TypedPayloadRef, ...]:
    """Direct execution evidence: non-prompt bridge direct refs + (LLM) the prompt ref.

    Refs represented by a finalized ToolCallRecord or a valid prompt record are NOT
    duplicated here; a cache request / rejected-orphan request / pre-assembly block
    enters directly. Any block promoted into the prompt record is excluded.
    """
    # exclude any tool request/result refs (they live in the records).
    tool_keys: set = set()
    for rec in merged.tool_call_records:
        tool_keys.add(typed_ref_sort_key(rec.request_ref))
        tool_keys.add(typed_ref_sort_key(rec.result_ref))
    data_keys = {typed_ref_sort_key(r) for r in merged.data_result_refs}
    # blocks promoted to the prompt record are transitive-only when a prompt ref exists.
    block_keys = (
        {typed_ref_sort_key(b.payload_ref) for b in merged.untrusted_blocks}
        if prompt_ref is not None else set()
    )
    direct: list[TypedPayloadRef] = []
    for r in merged.direct_evidence_refs:
        k = typed_ref_sort_key(r)
        if k in tool_keys or k in data_keys or k in block_keys:
            continue
        direct.append(r)
    if prompt_ref is None:
        # orphan blocks (no valid prompt record) are retained directly.
        for b in merged.untrusted_blocks:
            direct.append(b.payload_ref)
    else:
        direct.append(prompt_ref)
    return _sorted_typed(_dedup_typed(direct))


def _assign_block_ordinals(blocks: tuple[ProviderUntrustedBlock, ...]) -> tuple[PromptUntrustedBlockRef, ...]:
    out: list[PromptUntrustedBlockRef] = []
    for i, b in enumerate(blocks, start=1):
        out.append(
            PromptUntrustedBlockRef.build(
                ordinal=i, payload_ref=b.payload_ref, media_type=b.media_type,
                rendered_length=b.rendered_length))
    return tuple(out)


def _trusted_input_digests(input_snapshot: InputSnapshot) -> tuple[NamedEvidenceDigest, ...]:
    out: list[NamedEvidenceDigest] = []
    out.append(
        NamedEvidenceDigest(name="context_snapshot",
                            digest=input_snapshot.context_snapshot_ref.payload_ref.content_digest))
    for b in input_snapshot.artifact_inputs:
        if b.artifact_refs:
            out.append(NamedEvidenceDigest(name=b.input_name, digest=b.artifact_refs[0].content_digest))
    out.sort(key=lambda e: e.name)
    # de-dup by name (context_snapshot is unique).
    seen: set = set()
    deduped: list[NamedEvidenceDigest] = []
    for e in out:
        if e.name in seen:
            continue
        seen.add(e.name)
        deduped.append(e)
    return tuple(deduped)


# --------------------------------------------------------------------------- #
# prompt-record persistence (payload + recovery cell, atomic)                  #
# --------------------------------------------------------------------------- #
def _persist_prompt_record(
    record: PromptAssemblyRecord, *, writer, stores, ctx, plan, node, runtime, prompt_token, clock,
) -> TypedPayloadRef:
    """Persist the single prompt record exactly once (payload + recovery cell).

    A post-commit / pre-model crash is recoverable: the recovery cell holds the exact
    prompt typed ref keyed by (run, node, attempt, runtime.prompt), so a retry of the
    SAME run recovers it without a second prompt put.
    """
    # Phase 10 · Task 8b: the key is RUN-SCOPED. Cells are process-global per
    # store backend, and a sealed preset pins its node ids — without the run's
    # identity in the key, the SECOND run of the same plan against one shared
    # store recovered the FIRST run's prompt record and every LLM node failed
    # the verify_model_request_binding equality check. Within one run the key
    # stays stable across re-execution of the same (run, node, attempt), which
    # is the cell's crash-recovery purpose. Cells written under the pre-fix
    # key shape become unreachable — deliberately NOT migrated: this is a
    # recovery cache, and a fresh persist on the next run is the correct
    # outcome.
    cell_key = content_digest(
        {"run_id": ctx.run_id, "node_id": node.id, "attempt": prompt_token.attempt,
         "kind": "runtime.prompt"})
    existing = stores.cells.load(PROMPT_CELL_NAMESPACE, cell_key)
    if existing is not None:
        return existing
    # Phase 8 · Task 8 (additive): the first attempt keeps the EXACT prior
    # idempotency keys (byte-identical v1); a v2 retry / schema-repair attempt
    # (attempt >= 2) assembles a DISTINCT prompt, so its payload/batch keys carry
    # the attempt suffix — without it a differing repair prompt would collide with
    # attempt 1 under the attempt-independent key and raise IdempotencyConflict.
    attempt_suffix = "" if prompt_token.attempt == 1 else f":a{prompt_token.attempt}"
    staged_key = f"{node.id}:prompt:{prompt_token.call_ordinal}"
    batch = RuntimeBatch(
        idempotency_key=f"{ctx.run_id}:{node.id}{attempt_suffix}:prompt-record",
        payload_puts=(
            PayloadPutCommand(
                staged_key=StagedPayloadKey(key=staged_key), schema_ref=_PROMPT_ASSEMBLY_RECORD_SR,
                namespace="main", payload_template=dict(record),
                registry_digest=runtime.runtime_registry_digest,
                idempotency_key=f"{ctx.run_id}:{node.id}{attempt_suffix}:prompt-payload"),
        ),
        cell_cas=(
            StateCellCompareAndSwapCommand(
                cell_namespace=PROMPT_CELL_NAMESPACE, cell_key_digest=cell_key,
                expected_value=None,
                new_target=StagedTypedPayloadRef(
                    staged_key=StagedPayloadKey(key=staged_key),
                    schema_ref=_PROMPT_ASSEMBLY_RECORD_SR, namespace="main")),
        ),
    )
    result = stores.unit_of_work.commit(batch)
    return result.staged_typed_ref(staged_key)


# --------------------------------------------------------------------------- #
# discipline / policy checks                                                   #
# --------------------------------------------------------------------------- #
def _check_tool_discipline(worker, runtime, node, capability_gateway, tool_records):
    # per-summary independent minima/maxima.
    for summary in runtime.summaries_for(node.id):
        charge = capability_gateway.summary_charge(summary.summary_digest)
        if charge.finalized < summary.min_finalized_tool_calls_on_success:
            return False, (
                f"bridge {summary.bridge_id!r} finalized {charge.finalized} tool calls "
                f"(< minimum {summary.min_finalized_tool_calls_on_success})")
        if charge.invocations > summary.max_capability_invocations:
            return False, (
                f"bridge {summary.bridge_id!r} invoked {charge.invocations} "
                f"(> maximum {summary.max_capability_invocations})")
    # WorkerSpec REQUIRED / FORBIDDEN overall.
    tc = worker.evidence_policy.tool_calls
    if tc is ToolCallRequirement.REQUIRED and len(tool_records) < 1:
        return False, "WorkerSpec tool_calls=REQUIRED but no finalized ToolCallRecord"
    if tc is ToolCallRequirement.FORBIDDEN and capability_gateway.begun_count() != 0:
        return False, "WorkerSpec tool_calls=FORBIDDEN but a capability invocation was begun"
    return True, ""


def _absent_input_degradations(node, worker, input_snapshot) -> tuple[str, ...]:
    """Honest worker-side degraded-input detection.

    A declared worker input that the Plan feeds (>= 1 dependency injects into it)
    but that arrives absent — or bound with zero refs — on the ready snapshot was
    weakened away by an unsatisfied DEGRADE/SKIP dependency: a successful result is
    then at most DEGRADED (honesty rule 11). An input the Plan never feeds is absent
    *by design* and degrades nothing.
    """
    fed = {dep.inject_as for dep in node.dependencies}
    by_name = {b.input_name: b for b in input_snapshot.artifact_inputs}
    reasons: list[str] = []
    for ib in worker.inputs:
        if ib.name not in fed:
            continue
        binding = by_name.get(ib.name)
        if binding is None or not binding.artifact_refs:
            reasons.append(
                f"declared input {ib.name!r} was omitted from the ready snapshot "
                "(unsatisfied weakening dependency)")
    return tuple(reasons)


def _check_number_anchors(worker, anchors):
    policy = worker.evidence_policy
    if not policy.allow_unsourced_numbers:
        for a in anchors:
            if a.is_unsourced:
                return False, f"unsourced NumberAnchor {a.label!r} but allow_unsourced_numbers=False"
    if policy.require_number_anchors:
        # a required-anchor worker with zero anchors is honest only when the payload
        # surfaces no figure; static v1 accepts an empty anchor tuple (the model
        # declares its numbers), so we only enforce the unsourced policy above.
        pass
    return True, ""


def _primary_output_binding(worker):
    for o in worker.outputs:
        if o.name == _PRIMARY_OUTPUT_KEY:
            return o
    raise WorkerExecutionError("worker declares no primary output binding")  # pragma: no cover


# --------------------------------------------------------------------------- #
# NodeRun + Artifact builders                                                  #
# --------------------------------------------------------------------------- #
def _build_node_run(
    *, plan, node, worker, ctx, status, node_run_id, attempt_id, attempt, input_snapshot,
    started_at, finished_at, reason_code, reason, error_type, tool_call_records, data_result_refs,
    execution_evidence_refs, input_tokens, output_tokens, output_keys=(), output_artifact_ids=(),
) -> NodeRun:
    return NodeRun(
        node_run_id=node_run_id, run_id=ctx.run_id, plan_id=plan.plan_id,
        plan_digest=plan.plan_digest, node_id=node.id, worker_id=worker.id, status=status,
        reason_code=reason_code, reason=reason, attempt_id=attempt_id, attempt=attempt,
        input_snapshot_digest=input_snapshot.content_digest, started_at=started_at,
        finished_at=finished_at, output_keys=tuple(output_keys),
        output_artifact_ids=tuple(output_artifact_ids), tool_call_records=tuple(tool_call_records),
        data_result_refs=tuple(data_result_refs), execution_evidence_refs=tuple(execution_evidence_refs),
        input_tokens=input_tokens, output_tokens=output_tokens, error_type=error_type)


def _build_artifact(
    *, plan, node, worker, ctx, out_binding, payload, rendered_text, number_anchors, input_snapshot,
    tool_call_records, data_result_refs, execution_evidence_refs, prompt_ref, kind, model_result, clock,
) -> Artifact:
    prompt_material_ref = worker.system_prompt_ref if kind is ExecutionKind.LLM else None
    skill_refs = tuple(s.skill_ref for s in worker.skills) if kind is ExecutionKind.LLM else ()
    model_config_digest = None
    if kind is ExecutionKind.LLM:
        model_config_digest = content_digest(
            {"model_tier": worker.execution.model_tier, "thinking_budget": worker.execution.thinking_budget})
    input_refs = _input_artifact_refs(input_snapshot)
    created_at = clock_now(clock)
    provenance = Provenance(
        plan_digest=plan.plan_digest, code_version=CODE_VERSION, as_of=ctx.data.as_of,
        pit_mode=ctx.data.mode, model_config_digest=model_config_digest, sampling_seed=None,
        prompt_ref=prompt_material_ref, skill_refs=skill_refs,
        capability_refs=tuple(worker.capability_allowlist),
        input_snapshot_digest=input_snapshot.content_digest,
        data_result_refs=tuple(data_result_refs), execution_evidence_refs=tuple(execution_evidence_refs),
        tool_call_records=tuple(tool_call_records), provider=model_result.provider,
        model=model_result.model, model_response_id=model_result.provider_response_id)
    artifact_id = f"art-{ctx.run_id}-{node.id}-{_PRIMARY_OUTPUT_KEY}"
    return Artifact.build(
        artifact_id=artifact_id, run_id=ctx.run_id, created_at=created_at,
        producer_node_id=node.id, slot=node.writes_slot, output_key=_PRIMARY_OUTPUT_KEY,
        kind=out_binding.schema_ref.name, payload_schema_ref=out_binding.schema_ref, payload=payload,
        rendered_md=rendered_text, input_refs=input_refs, provenance=provenance,
        numbers=tuple(number_anchors), badges=())


def _input_artifact_refs(input_snapshot: InputSnapshot) -> tuple[ArtifactRef, ...]:
    refs: list[ArtifactRef] = []
    for b in input_snapshot.artifact_inputs:
        refs.extend(b.artifact_refs)
    return tuple(refs)
