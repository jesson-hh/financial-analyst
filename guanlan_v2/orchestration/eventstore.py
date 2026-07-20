"""Phase 2 · Task 2 — registry-validated payload store + append-only event store.

The production storage the rest of Phase 2 builds on. Everything here is an
in-memory *behavioral stand-in* for later durable storage — a lock plus
copy-on-write state — but every accepted transition is append-only and replayable,
and a failure never mutates an accepted record.

Components
----------
* :class:`SchemaRegistryResolver` — service-owned read-only map from a reviewed
  **sealed** registry digest to that one registry snapshot. A run resolves the
  exact digest bound by its Plan; there is deliberately no mutable "latest".
* :class:`PayloadStore` — registry-validated typed payload persistence.
* :class:`EventStore` — append-only, store-assigned ``journal_seq`` / ``visible_seq``,
  builds only Phase 1-valid immutable :class:`RunEvent`s, dual cursors + replay.
* :class:`EventRefusalAuditSink` — the audit-only owner of refusal-detail
  validation + record creation (never a ``RunEvent``, never on the public stream).
* :class:`RuntimeBudgetEventSink` — the production
  :class:`~guanlan_v2.orchestration.budget.BudgetEventSink` over the shared backend.
* :class:`RuntimeStateCellStore` — sealed, namespace-scoped exact-typed-ref cells.
* :class:`RuntimeUnitOfWork` — atomically commits a closed batch of payload puts,
  event appends, ``BudgetTransitionCommand``s and state-cell CAS commands, or none,
  substituting staged-ref sentinels in topological order.

All of the above are thin views over **one** shared copy-on-write backend + lock
(:class:`RuntimeStores`), so a unit of work can commit across them atomically.

``IdempotencyConflict`` is **not** redefined here — it is imported (and re-exported)
from :mod:`guanlan_v2.orchestration.budget` so the ledger and store agree on one
persist-then-publish idempotency type.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any

from guanlan_v2.orchestration.budget import (
    BudgetEvent,
    BudgetTransitionCommand,
    IdempotencyConflict,
    fold_budget_events,
    validate_budget_command,
)
from guanlan_v2.orchestration.context import RunBudget
from guanlan_v2.orchestration.digest import (
    DigestHex,
    DigestModel,
    NonEmptyStr,
    content_digest,
)
from guanlan_v2.orchestration.events import (
    PUBLIC_EVENT_PARTITIONS,
    EventCursor,
    EventPartition,
    EventType,
    RunEvent,
)
from guanlan_v2.orchestration.refs import (
    PUBLIC_PAYLOAD_NAMESPACES,
    PayloadNamespace,
    PayloadRef,
    SchemaRef,
    TypedPayloadRef,
)
from guanlan_v2.orchestration.runtime_clock import AuthoritativeClock, clock_now
from guanlan_v2.orchestration.runtime_contracts import (
    AuditDetailRegistry,
    EventRefusalRecord,
    NamedEvidenceDigest,
)
from guanlan_v2.orchestration.schema_registry import SchemaRegistry
from pydantic import BaseModel, ConfigDict, model_validator

__all__ = [
    # errors
    "EventStoreError",
    "UnknownRegistryDigest",
    "RegistryDigestConflict",
    "ContentDigestMismatch",
    "NamespaceViolation",
    "StateCellError",
    "UnitOfWorkError",
    "StagedReferenceError",
    "CasPreconditionFailed",
    "IdempotencyConflict",  # re-exported from budget (single type)
    # resolver + stores
    "SchemaRegistryResolver",
    "PayloadStore",
    "EventStore",
    "EventAppendRequest",
    "EventRefusalAuditSink",
    "RuntimeStateCellStore",
    "RuntimeBudgetEventSink",
    "RuntimeUnitOfWork",
    "RuntimeStores",
    # staged sentinels + commands
    "StagedPayloadKey",
    "StagedPayloadRef",
    "StagedTypedPayloadRef",
    "PayloadPutCommand",
    "EventAppendCommand",
    "StateCellCompareAndSwapCommand",
    "RuntimeBatch",
    "UnitOfWorkResult",
]


# --------------------------------------------------------------------------- #
# Errors                                                                      #
# --------------------------------------------------------------------------- #
class EventStoreError(Exception):
    """Base for every payload/event/cell store failure."""


class UnknownRegistryDigest(EventStoreError):
    """A registry digest was never registered in the resolver."""


class RegistryDigestConflict(EventStoreError):
    """A registry digest was re-registered with a different sealed manifest."""


class ContentDigestMismatch(EventStoreError):
    """A ref's declared content digest disagrees with the stored payload."""


class NamespaceViolation(EventStoreError):
    """A main/public event referencing a non-public payload (or vice versa)."""


class StateCellError(EventStoreError):
    """An unknown/unregistered state-cell namespace."""


class UnitOfWorkError(EventStoreError):
    """A unit-of-work batch failed prevalidation (nothing was published)."""


class StagedReferenceError(UnitOfWorkError):
    """An unknown / duplicate / forward-cyclic / mis-declared staged reference."""


class CasPreconditionFailed(UnitOfWorkError):
    """A state-cell compare-and-swap expected value did not match."""


# --------------------------------------------------------------------------- #
# Strict runtime base (NOT a ContractModel — mirrors the Task 1 idiom)         #
# --------------------------------------------------------------------------- #
class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


# --------------------------------------------------------------------------- #
# Staged sentinels (internal service-only batch-local symbolic identities)     #
# --------------------------------------------------------------------------- #
class StagedPayloadKey(_StrictModel):
    """A deterministic batch-local symbolic identity — never a caller object id."""

    key: NonEmptyStr


class StagedPayloadRef(_StrictModel):
    """A sentinel standing in for an earlier same-batch payload's real PayloadRef.

    Declares the target's schema/namespace so the unit of work can prove a nested
    control ref points at a real earlier dependency with the exact schema/namespace,
    without a callback or a fabricated locator.
    """

    staged_key: StagedPayloadKey
    schema_ref: SchemaRef
    namespace: PayloadNamespace


class StagedTypedPayloadRef(_StrictModel):
    """A sentinel standing in for an earlier same-batch payload's TypedPayloadRef."""

    staged_key: StagedPayloadKey
    schema_ref: SchemaRef
    namespace: PayloadNamespace


# --------------------------------------------------------------------------- #
# Closed commands + batch DTOs                                                 #
# --------------------------------------------------------------------------- #
class PayloadPutCommand(_StrictModel):
    """Declare one registry-validated payload put with a batch-local staged key.

    ``payload_template`` is a closed immutable mapping whose values are primitives,
    Phase 1 model instances or :class:`StagedPayloadRef` / :class:`StagedTypedPayloadRef`
    sentinels targeting an *earlier* dependency in the same acyclic batch.
    """

    staged_key: StagedPayloadKey
    schema_ref: SchemaRef
    namespace: PayloadNamespace
    payload_template: dict[str, Any]
    registry_digest: DigestHex
    idempotency_key: NonEmptyStr


class EventAppendCommand(_StrictModel):
    """Declare one event append whose payload is an existing ref or a staged key."""

    run_id: NonEmptyStr
    partition: EventPartition
    event_type: NonEmptyStr
    payload_schema_ref: SchemaRef
    payload_target: PayloadRef | StagedPayloadKey
    registry_digest: DigestHex
    idempotency_key: NonEmptyStr
    plan_digest: DigestHex | None = None
    causation_id: NonEmptyStr | None = None
    correlation_id: NonEmptyStr | None = None


class StateCellCompareAndSwapCommand(_StrictModel):
    """A pure equality CAS over one exact typed-ref recovery cell.

    There is no callback, merge, delete, arbitrary object value or caller-selected
    locator: it validates ``expected_value`` (a full :class:`TypedPayloadRef` or
    ``None``) and swaps in ``new_target`` (a concrete typed ref, or a staged one
    resolved by the unit of work).
    """

    cell_namespace: NonEmptyStr
    cell_key_digest: DigestHex
    expected_value: TypedPayloadRef | None = None
    new_target: TypedPayloadRef | StagedTypedPayloadRef


class EventAppendRequest(_StrictModel):
    """The stable runtime-port DTO for a single event append.

    Carries only the fields known *before* persistence: the store assigns
    ``journal_seq`` / ``visible_seq`` / ``occurred_at`` / ``event_id`` — a caller
    can never select a sequence, a cursor or a timestamp.
    """

    run_id: NonEmptyStr
    partition: EventPartition
    event_type: NonEmptyStr
    payload_schema_ref: SchemaRef
    payload_ref: PayloadRef
    registry_digest: DigestHex
    idempotency_key: NonEmptyStr
    plan_digest: DigestHex | None = None
    causation_id: NonEmptyStr | None = None
    correlation_id: NonEmptyStr | None = None


class RuntimeBatch(_StrictModel):
    """A closed, atomic unit-of-work command tuple (all-or-none).

    A batch deliberately carries **no** :class:`RunBudget`: budget maxima are
    service authority bound once per run via
    :meth:`RuntimeStores.bind_run_budget`, so two batches for the same run can
    never validate against inconsistent ceilings. ``budget_run_id`` only selects
    the already-bound authority.
    """

    idempotency_key: NonEmptyStr
    payload_puts: tuple[PayloadPutCommand, ...] = ()
    event_appends: tuple[EventAppendCommand, ...] = ()
    budget_commands: tuple[BudgetTransitionCommand, ...] = ()
    cell_cas: tuple[StateCellCompareAndSwapCommand, ...] = ()
    # budget context (required only when budget_commands is non-empty).
    budget_run_id: NonEmptyStr | None = None

    @model_validator(mode="after")
    def _budget_context_present(self) -> "RuntimeBatch":
        if self.budget_commands and self.budget_run_id is None:
            raise ValueError(
                "budget_commands require budget_run_id (the RunBudget itself is "
                "bound service-side via RuntimeStores.bind_run_budget)"
            )
        return self


@dataclass(frozen=True)
class UnitOfWorkResult:
    """The committed whole-batch result: staged→final maps plus resulting facts."""

    payload_refs: dict[str, PayloadRef]
    typed_refs: dict[str, TypedPayloadRef]
    events: tuple[RunEvent, ...]
    budget_events: tuple[BudgetEvent, ...]
    cells: dict[tuple[str, str], TypedPayloadRef]

    def payload_ref(self, staged_key: str) -> PayloadRef:
        return self.payload_refs[staged_key]

    def staged_typed_ref(self, staged_key: str) -> TypedPayloadRef:
        return self.typed_refs[staged_key]

    def cell(self, namespace: str, key_digest: str) -> TypedPayloadRef | None:
        return self.cells.get((namespace, key_digest))


# --------------------------------------------------------------------------- #
# Copy-on-write backend + shared holder                                       #
# --------------------------------------------------------------------------- #
@dataclass
class _StoredPayload:
    namespace: str
    schema_key: str
    registry_digest: str
    content_digest: str
    model: Any  # the validated Phase 1 payload model


@dataclass
class _StoredBatch:
    batch: "RuntimeBatch"
    result: UnitOfWorkResult


@dataclass
class _Backend:
    """One immutable-by-convention snapshot of all runtime storage.

    Never mutated in place: a transition clones it, applies changes to the clone
    and publishes the clone under the shared lock, so a crash before publish
    exposes none of the batch.
    """

    payloads: dict[str, _StoredPayload] = field(default_factory=dict)
    payload_idem: dict[str, str] = field(default_factory=dict)
    payload_seq: int = 0
    events: tuple[RunEvent, ...] = ()
    event_by_key: dict[str, RunEvent] = field(default_factory=dict)
    journal_counters: dict[tuple[str, str], int] = field(default_factory=dict)
    visible_counters: dict[tuple[str, str], int] = field(default_factory=dict)
    budget_events: tuple[BudgetEvent, ...] = ()
    budget_seq: int = 0
    reservation_seq: int = 0
    cells: dict[tuple[str, str], TypedPayloadRef] = field(default_factory=dict)
    batch_idem: dict[str, _StoredBatch] = field(default_factory=dict)

    def clone(self) -> "_Backend":
        return _Backend(
            payloads=dict(self.payloads),
            payload_idem=dict(self.payload_idem),
            payload_seq=self.payload_seq,
            events=self.events,
            event_by_key=dict(self.event_by_key),
            journal_counters=dict(self.journal_counters),
            visible_counters=dict(self.visible_counters),
            budget_events=self.budget_events,
            budget_seq=self.budget_seq,
            reservation_seq=self.reservation_seq,
            cells=dict(self.cells),
            batch_idem=dict(self.batch_idem),
        )


class _Shared:
    """The single copy-on-write backend + lock every store view shares."""

    def __init__(self) -> None:
        self.backend = _Backend()
        self.lock = threading.RLock()


# --------------------------------------------------------------------------- #
# SchemaRegistryResolver                                                       #
# --------------------------------------------------------------------------- #
class SchemaRegistryResolver:
    """Read-only map from a reviewed sealed registry digest → that registry.

    Registration requires a **sealed** registry and keys it by its exact
    ``registry_digest``; re-registering the same digest is idempotent, a different
    manifest under the same digest is impossible (the digest *is* the manifest) but
    a mismatched claimed identity fails. There is deliberately no ``latest``.
    """

    def __init__(self) -> None:
        self._by_digest: dict[str, SchemaRegistry] = {}

    def register(self, registry: SchemaRegistry) -> DigestHex:
        if not registry.sealed:
            raise EventStoreError("only a sealed SchemaRegistry may be registered in the resolver")
        digest = registry.registry_digest
        existing = self._by_digest.get(digest)
        if existing is not None:
            if existing.registry_digest != digest:  # pragma: no cover - defensive
                raise RegistryDigestConflict(f"registry digest {digest!r} conflict")
            return digest  # idempotent
        self._by_digest[digest] = registry
        return digest

    def resolve(self, digest: str) -> SchemaRegistry:
        registry = self._by_digest.get(digest)
        if registry is None:
            raise UnknownRegistryDigest(f"no sealed registry registered for digest {digest!r}")
        return registry


# --------------------------------------------------------------------------- #
# Shared low-level operations (used by the stores AND the unit of work)         #
# --------------------------------------------------------------------------- #
def _payload_content_digest(model: Any) -> str:
    """Canonical stored-content digest for a registry-validated payload model.

    A Phase-1 :class:`DigestModel` digests through its own canonical semantic
    projection (``content_digest(model)``). A strict *non*-DigestModel runtime
    fact (a Phase-2 ``_StrictModel`` control payload such as RuntimeSupportReport,
    AdmissionCandidate, PlanAdmitted or PromptAssemblyRecord) digests over its
    canonical JSON dump — the exact reviewed path the Task-2
    :class:`EventRefusalAuditSink` already uses for its strict detail payloads.
    Registry validation has already run by the time this is called, so ``model``
    is always the exact registered schema's instance.
    """
    if isinstance(model, DigestModel):
        return content_digest(model)
    return content_digest(model.model_dump(mode="json"))


def _apply_payload_put(
    wb: _Backend,
    resolver: SchemaRegistryResolver,
    *,
    schema_ref: SchemaRef,
    payload: Any,
    registry_digest: str,
    namespace: str,
    idempotency_key: str,
) -> PayloadRef:
    registry = resolver.resolve(registry_digest)  # UnknownRegistryDigest
    model = registry.validate_payload(schema_ref, payload)  # ValidationError
    cdigest = _payload_content_digest(model)
    existing_id = wb.payload_idem.get(idempotency_key)
    if existing_id is not None:
        stored = wb.payloads[existing_id]
        if (
            stored.content_digest != cdigest
            or stored.namespace != namespace
            or stored.schema_key != schema_ref.key
        ):
            raise IdempotencyConflict(
                f"payload idempotency key {idempotency_key!r} reused with different content"
            )
        return PayloadRef(namespace=stored.namespace, object_id=existing_id, content_digest=cdigest)
    wb.payload_seq += 1
    object_id = f"payload-{wb.payload_seq}"
    wb.payloads[object_id] = _StoredPayload(
        namespace=namespace, schema_key=schema_ref.key, registry_digest=registry_digest,
        content_digest=cdigest, model=model,
    )
    wb.payload_idem[idempotency_key] = object_id
    return PayloadRef(namespace=namespace, object_id=object_id, content_digest=cdigest)


def _append_event(
    wb: _Backend,
    resolver: SchemaRegistryResolver,
    clock: AuthoritativeClock,
    request: EventAppendRequest,
) -> RunEvent:
    # 1) the referenced payload must exist with the exact namespace + content digest.
    stored = wb.payloads.get(request.payload_ref.object_id)
    if stored is None:
        raise EventStoreError("append references a payload ref that does not exist")
    if stored.namespace != request.payload_ref.namespace:
        raise EventStoreError("append payload ref namespace does not match the stored payload")
    if stored.content_digest != request.payload_ref.content_digest:
        raise EventStoreError("append payload ref content digest does not match the stored payload")
    # 2) the schema must resolve under the request's exact registry digest AND
    #    match the schema the stored payload was actually validated under — an
    #    event cannot re-declare a payload as a different type.
    registry = resolver.resolve(request.registry_digest)
    registry.resolve(request.payload_schema_ref)  # UnknownSchemaError
    if stored.schema_key != request.payload_schema_ref.key:
        raise EventStoreError(
            f"append declares payload schema {request.payload_schema_ref.key!r} but the "
            f"stored payload was validated as {stored.schema_key!r}"
        )
    if stored.registry_digest != request.registry_digest:
        raise EventStoreError(
            "append declares a registry digest different from the one its payload "
            "was validated/stored under"
        )
    # 3) recheck the main/public namespace boundary before persistence.
    partition_public = request.partition in PUBLIC_EVENT_PARTITIONS
    payload_public = request.payload_ref.namespace in PUBLIC_PAYLOAD_NAMESPACES
    if partition_public != payload_public:
        raise NamespaceViolation(
            "a main/public event cannot reference a sealed/review/audit payload "
            "(or vice versa)"
        )
    event_type = EventType(request.event_type)
    key = (request.run_id, request.partition)
    journal_seq = wb.journal_counters.get(key, 0) + 1
    if partition_public and event_type is not EventType.ARTIFACT_STAGED:
        visible_seq: int | None = wb.visible_counters.get(key, 0) + 1
    else:
        visible_seq = None
    occurred_at = clock_now(clock)  # rejects a naive-returning clock
    event = RunEvent.build(
        event_id=f"event-{request.run_id}-{request.partition}-{journal_seq}",
        journal_seq=journal_seq, visible_seq=visible_seq, occurred_at=occurred_at,
        run_id=request.run_id, partition=request.partition, event_type=event_type,
        plan_digest=request.plan_digest, idempotency_key=request.idempotency_key,
        payload_schema_ref=request.payload_schema_ref, payload_ref=request.payload_ref,
        causation_id=request.causation_id, correlation_id=request.correlation_id,
    )
    existing = wb.event_by_key.get(request.idempotency_key)
    if existing is not None:
        if existing.content_digest != event.content_digest:
            raise IdempotencyConflict(
                f"event idempotency key {request.idempotency_key!r} reused with a different event"
            )
        return existing
    wb.events = wb.events + (event,)
    wb.event_by_key[request.idempotency_key] = event
    wb.journal_counters[key] = journal_seq
    if visible_seq is not None:
        wb.visible_counters[key] = visible_seq
    return event


def _budget_same_semantics(a: BudgetTransitionCommand, b: BudgetTransitionCommand) -> bool:
    return a.operation == b.operation and a.semantic_args == b.semantic_args


def _find_budget_event(wb: _Backend, run_id: str, ledger_id: str, key: str) -> BudgetEvent | None:
    for ev in wb.budget_events:
        if ev.run_id == run_id and ev.ledger_id == ledger_id and ev.command.idempotency_key == key:
            return ev
    return None


def _append_budget_event(
    wb: _Backend,
    command: BudgetTransitionCommand,
    *,
    run_id: str,
    ledger_id: str,
    occurred_at: Any,
) -> BudgetEvent:
    wb.budget_seq += 1
    if command.operation in (
        "reserve_plan", "reserve_node", "reserve_planner",
        "reserve_retry", "reserve_schema_repair",
    ):
        wb.reservation_seq += 1
        reservation_id = f"res-{wb.reservation_seq}"
    else:
        reservation_id = command.semantic_args.reservation_id
    event = BudgetEvent(
        seq=wb.budget_seq, event_id=f"be-{wb.budget_seq}", run_id=run_id, ledger_id=ledger_id,
        reservation_id=reservation_id, occurred_at=occurred_at, command=command,
    )
    wb.budget_events = wb.budget_events + (event,)
    return event


# --------------------------------------------------------------------------- #
# PayloadStore                                                                 #
# --------------------------------------------------------------------------- #
class PayloadStore:
    """Registry-validated in-memory typed payload persistence."""

    def __init__(self, shared: _Shared, resolver: SchemaRegistryResolver) -> None:
        self._shared = shared
        self._resolver = resolver

    def put(
        self,
        schema_ref: SchemaRef,
        payload: Any,
        *,
        registry_digest: str,
        namespace: str,
        idempotency_key: str,
    ) -> PayloadRef:
        with self._shared.lock:
            wb = self._shared.backend.clone()
            ref = _apply_payload_put(
                wb, self._resolver, schema_ref=schema_ref, payload=payload,
                registry_digest=registry_digest, namespace=namespace,
                idempotency_key=idempotency_key,
            )
            self._shared.backend = wb
            return ref

    def get(self, ref: PayloadRef, *, expected_schema_ref: SchemaRef) -> Any:
        backend = self._shared.backend
        stored = backend.payloads.get(ref.object_id)
        if stored is None:
            raise EventStoreError(f"no payload stored for object id {ref.object_id!r}")
        if stored.namespace != ref.namespace:
            raise EventStoreError("payload ref namespace does not match the stored payload")
        if stored.content_digest != ref.content_digest:
            raise ContentDigestMismatch("payload ref content digest does not match the stored payload")
        if stored.schema_key != expected_schema_ref.key:
            raise EventStoreError(
                f"stored payload schema {stored.schema_key!r} does not match expected "
                f"{expected_schema_ref.key!r}"
            )
        # re-verify through the exact digesting path used at put time, so a
        # tampered stored model (not just a wrong ref) is rejected on read.
        if _payload_content_digest(stored.model) != stored.content_digest:
            raise ContentDigestMismatch(
                "stored payload content does not recompute to its declared digest (tamper)"
            )
        return stored.model


# --------------------------------------------------------------------------- #
# EventStore                                                                   #
# --------------------------------------------------------------------------- #
class EventStore:
    """Append-only event store with store-assigned dual cursors + replay."""

    def __init__(self, shared: _Shared, resolver: SchemaRegistryResolver, clock: AuthoritativeClock) -> None:
        self._shared = shared
        self._resolver = resolver
        self._clock = clock

    def append(self, request: EventAppendRequest) -> RunEvent:
        with self._shared.lock:
            wb = self._shared.backend.clone()
            event = _append_event(wb, self._resolver, self._clock, request)
            self._shared.backend = wb
            return event

    def journal(self, run_id: str, partition: str) -> tuple[RunEvent, ...]:
        return tuple(
            e for e in self._shared.backend.events
            if e.run_id == run_id and e.partition == partition
        )

    def visible(self, run_id: str, partition: str, after: int = 0) -> tuple[RunEvent, ...]:
        return tuple(
            e for e in self._shared.backend.events
            if e.run_id == run_id and e.partition == partition
            and e.visible_seq is not None and e.visible_seq > after
        )

    def cursor(self, run_id: str, partition: str) -> EventCursor | None:
        visible = self.visible(run_id, partition)
        if not visible:
            return None
        return EventCursor(run_id=run_id, partition=partition, visible_seq=visible[-1].visible_seq)

    def replay(self) -> "EventStore":
        """Rebuild a fresh EventStore purely from the persisted RunEvents.

        Journal/visible counters and the idempotency index are recomputed from the
        stored events (not copied from cached maps), proving replay reproduces the
        same journal, cursors and visible view.
        """
        src = self._shared.backend
        nb = _Backend(
            payloads=dict(src.payloads),
            payload_idem=dict(src.payload_idem),
            payload_seq=src.payload_seq,
        )
        for event in src.events:  # stored append order == per-partition journal order
            key = (event.run_id, event.partition)
            nb.events = nb.events + (event,)
            nb.event_by_key[event.idempotency_key] = event
            nb.journal_counters[key] = max(nb.journal_counters.get(key, 0), event.journal_seq)
            if event.visible_seq is not None:
                nb.visible_counters[key] = max(nb.visible_counters.get(key, 0), event.visible_seq)
        shared = _Shared()
        shared.backend = nb
        return EventStore(shared, self._resolver, self._clock)


# --------------------------------------------------------------------------- #
# RuntimeBudgetEventSink (production BudgetEventSink over the shared backend)   #
# --------------------------------------------------------------------------- #
class RuntimeBudgetEventSink:
    """The production append-only budget-event sink for one run/ledger.

    Implements the Task 1 :class:`~guanlan_v2.orchestration.budget.BudgetEventSink`
    protocol over the shared backend, so a :class:`BudgetLedger` drives the *exact
    same* closed ``BudgetTransitionCommand``s against production storage.
    """

    def __init__(self, shared: _Shared, clock: AuthoritativeClock, *, run_id: str, ledger_id: str) -> None:
        self._shared = shared
        self._clock = clock
        self._run_id = run_id
        self._ledger_id = ledger_id

    def budget_events(self) -> tuple[BudgetEvent, ...]:
        return tuple(
            e for e in self._shared.backend.budget_events
            if e.run_id == self._run_id and e.ledger_id == self._ledger_id
        )

    def find_by_idempotency_key(self, key: str) -> BudgetEvent | None:
        return _find_budget_event(self._shared.backend, self._run_id, self._ledger_id, key)

    def append(self, command: BudgetTransitionCommand) -> BudgetEvent:
        with self._shared.lock:
            wb = self._shared.backend.clone()
            existing = _find_budget_event(wb, self._run_id, self._ledger_id, command.idempotency_key)
            if existing is not None:
                if not _budget_same_semantics(existing.command, command):
                    raise IdempotencyConflict(
                        f"budget idempotency key {command.idempotency_key!r} reused with different content"
                    )
                return existing
            occurred_at = clock_now(self._clock)
            event = _append_budget_event(
                wb, command, run_id=self._run_id, ledger_id=self._ledger_id, occurred_at=occurred_at,
            )
            self._shared.backend = wb
            return event


# --------------------------------------------------------------------------- #
# RuntimeStateCellStore                                                        #
# --------------------------------------------------------------------------- #
class RuntimeStateCellStore:
    """Sealed, namespace-scoped exact-typed-ref recovery cells (read + CAS via UoW).

    Cells hold only exact :class:`TypedPayloadRef`s, keyed by an immutable
    startup-reviewed namespace plus an opaque semantic key digest. ``load`` is
    read-only; a CAS runs only inside a :class:`RuntimeUnitOfWork`.
    """

    def __init__(self, shared: _Shared, *, allowed_namespaces: frozenset[str]) -> None:
        self._shared = shared
        self._allowed = frozenset(allowed_namespaces)

    @property
    def allowed_namespaces(self) -> frozenset[str]:
        return self._allowed

    def load(self, namespace: str, key_digest: str) -> TypedPayloadRef | None:
        if namespace not in self._allowed:
            raise StateCellError(f"unknown state-cell namespace {namespace!r}")
        return self._shared.backend.cells.get((namespace, key_digest))


# --------------------------------------------------------------------------- #
# Staged-ref substitution helpers                                             #
# --------------------------------------------------------------------------- #
def _iter_staged_refs(value: Any):
    """Yield every :class:`StagedPayloadRef` reachable inside a payload template."""
    if isinstance(value, StagedPayloadRef):
        yield value
    elif isinstance(value, dict):
        for v in value.values():
            yield from _iter_staged_refs(v)
    elif isinstance(value, (list, tuple)):
        for v in value:
            yield from _iter_staged_refs(v)


def _substitute_staged(value: Any, allocated: dict[str, PayloadRef]) -> Any:
    """Return ``value`` with every staged ref replaced by its allocated PayloadRef."""
    if isinstance(value, StagedPayloadRef):
        return allocated[value.staged_key.key]
    if isinstance(value, dict):
        return {k: _substitute_staged(v, allocated) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return type(value)(_substitute_staged(v, allocated) for v in value)
    return value


def _typed_ref_equal(a: TypedPayloadRef | None, b: TypedPayloadRef | None) -> bool:
    if a is None or b is None:
        return a is None and b is None
    return a.semantic_digest() == b.semantic_digest()


# --------------------------------------------------------------------------- #
# RuntimeUnitOfWork                                                            #
# --------------------------------------------------------------------------- #
class RuntimeUnitOfWork:
    """Atomically commit a closed batch of payload/event/budget/CAS commands, or none.

    ``run_budgets`` is the service-owned run → :class:`RunBudget` authority map
    shared with :class:`RuntimeStores` (bound once per run via
    :meth:`RuntimeStores.bind_run_budget`); a batch selects it by ``budget_run_id``
    and can never carry its own maxima.
    """

    def __init__(
        self,
        shared: _Shared,
        resolver: SchemaRegistryResolver,
        clock: AuthoritativeClock,
        *,
        allowed_cell_namespaces: frozenset[str],
        run_budgets: dict[str, RunBudget],
    ) -> None:
        self._shared = shared
        self._resolver = resolver
        self._clock = clock
        self._allowed_cell_namespaces = frozenset(allowed_cell_namespaces)
        self._run_budgets = run_budgets

    def commit(self, batch: RuntimeBatch) -> UnitOfWorkResult:
        with self._shared.lock:
            prev = self._shared.backend.batch_idem.get(batch.idempotency_key)
            if prev is not None:
                # whole-batch idempotency: return the original result even if a later
                # transaction has since advanced a cell.
                if prev.batch != batch:
                    raise IdempotencyConflict(
                        f"batch idempotency key {batch.idempotency_key!r} reused with different content"
                    )
                return prev.result
            wb = self._shared.backend.clone()
            result = self._apply_batch(wb, batch)
            wb.batch_idem[batch.idempotency_key] = _StoredBatch(batch=batch, result=result)
            self._shared.backend = wb
            return result

    def _apply_batch(self, wb: _Backend, batch: RuntimeBatch) -> UnitOfWorkResult:
        # -- A. structural validation of the staged payload-put DAG ---------- #
        staged_meta: dict[str, tuple[SchemaRef, str]] = {}
        for put in batch.payload_puts:
            k = put.staged_key.key
            if k in staged_meta:
                raise StagedReferenceError(f"duplicate staged key {k!r} in batch")
            for sref in _iter_staged_refs(put.payload_template):
                dep = sref.staged_key.key
                if dep not in staged_meta:  # unknown / forward / cyclic reference
                    raise StagedReferenceError(
                        f"staged ref {dep!r} does not target an earlier dependency in the batch"
                    )
                dep_schema, dep_namespace = staged_meta[dep]
                if sref.schema_ref.key != dep_schema.key or sref.namespace != dep_namespace:
                    raise StagedReferenceError(
                        f"staged ref {dep!r} declares schema/namespace that differs from the target"
                    )
            staged_meta[k] = (put.schema_ref, put.namespace)

        # -- B. allocate payloads in topological (declared) order ------------ #
        allocated: dict[str, PayloadRef] = {}
        typed_refs: dict[str, TypedPayloadRef] = {}
        for put in batch.payload_puts:
            template = _substitute_staged(put.payload_template, allocated)
            ref = _apply_payload_put(
                wb, self._resolver, schema_ref=put.schema_ref, payload=template,
                registry_digest=put.registry_digest, namespace=put.namespace,
                idempotency_key=put.idempotency_key,
            )
            allocated[put.staged_key.key] = ref
            typed_refs[put.staged_key.key] = TypedPayloadRef(schema_ref=put.schema_ref, payload_ref=ref)

        # -- C. budget transitions (fold-validate-append each in order) ------ #
        budget_events: list[BudgetEvent] = []
        if batch.budget_commands:
            run_id = batch.budget_run_id
            assert run_id is not None  # RuntimeBatch validator
            run_budget = self._run_budgets.get(run_id)
            if run_budget is None:
                raise UnitOfWorkError(
                    f"no RunBudget is bound for run {run_id!r}; bind the service "
                    "authority via RuntimeStores.bind_run_budget before committing "
                    "budget commands"
                )
            ledger_id = run_budget.ledger_id
            for command in batch.budget_commands:
                existing = _find_budget_event(wb, run_id, ledger_id, command.idempotency_key)
                if existing is not None:
                    if not _budget_same_semantics(existing.command, command):
                        raise IdempotencyConflict(
                            f"budget idempotency key {command.idempotency_key!r} reused with different content"
                        )
                    budget_events.append(existing)
                    continue
                state = fold_budget_events(
                    tuple(e for e in wb.budget_events if e.run_id == run_id and e.ledger_id == ledger_id)
                )
                validate_budget_command(command, state, run_budget=run_budget)  # BudgetExceeded / Invalid
                occurred_at = clock_now(self._clock)
                budget_events.append(
                    _append_budget_event(wb, command, run_id=run_id, ledger_id=ledger_id, occurred_at=occurred_at)
                )

        # -- D. event appends (resolve staged payload targets) --------------- #
        events: list[RunEvent] = []
        for cmd in batch.event_appends:
            target = cmd.payload_target
            if isinstance(target, StagedPayloadKey):
                ref = allocated.get(target.key)
                if ref is None:
                    raise StagedReferenceError(f"event target references unknown staged key {target.key!r}")
            else:
                ref = target
            request = EventAppendRequest(
                run_id=cmd.run_id, partition=cmd.partition, event_type=cmd.event_type,
                payload_schema_ref=cmd.payload_schema_ref, payload_ref=ref,
                registry_digest=cmd.registry_digest, idempotency_key=cmd.idempotency_key,
                plan_digest=cmd.plan_digest, causation_id=cmd.causation_id,
                correlation_id=cmd.correlation_id,
            )
            events.append(_append_event(wb, self._resolver, self._clock, request))

        # -- E. state-cell compare-and-swap ---------------------------------- #
        result_cells: dict[tuple[str, str], TypedPayloadRef] = {}
        for cas in batch.cell_cas:
            if cas.cell_namespace not in self._allowed_cell_namespaces:
                raise StateCellError(f"unknown state-cell namespace {cas.cell_namespace!r}")
            key = (cas.cell_namespace, cas.cell_key_digest)
            current = wb.cells.get(key)
            if not _typed_ref_equal(current, cas.expected_value):
                raise CasPreconditionFailed(
                    f"state-cell {key!r} expected value does not match the current value"
                )
            target = cas.new_target
            if isinstance(target, StagedTypedPayloadRef):
                ref = allocated.get(target.staged_key.key)
                if ref is None:
                    raise StagedReferenceError(
                        f"CAS new_target references unknown staged key {target.staged_key.key!r}"
                    )
                dep_schema, dep_namespace = staged_meta[target.staged_key.key]
                if target.schema_ref.key != dep_schema.key or target.namespace != dep_namespace:
                    raise StagedReferenceError("CAS staged target schema/namespace mismatch")
                resolved = TypedPayloadRef(schema_ref=target.schema_ref, payload_ref=ref)
            else:
                resolved = target
            wb.cells[key] = resolved
            result_cells[key] = resolved

        return UnitOfWorkResult(
            payload_refs=allocated,
            typed_refs=typed_refs,
            events=tuple(events),
            budget_events=tuple(budget_events),
            cells=result_cells,
        )


# --------------------------------------------------------------------------- #
# EventRefusalAuditSink (audit-only; separate from the main payload/event path) #
# --------------------------------------------------------------------------- #
class EventRefusalAuditSink:
    """The single owner of refusal-detail validation, audit persistence + records.

    A refusal is never a :class:`RunEvent`: it receives no journal/visible
    sequence, is persisted only in the ``audit`` namespace, and cannot be read
    through the main/public EventStore APIs. The raw rejected payload/secret is
    never stored — only a validated *safe* detail and its digest.
    """

    def __init__(self, *, detail_registry: AuditDetailRegistry, clock: AuthoritativeClock) -> None:
        self._registry = detail_registry
        self._clock = clock
        self._lock = threading.RLock()  # serializes the idempotency probe + append
        self._records: list[EventRefusalRecord] = []
        self._by_key: dict[str, EventRefusalRecord] = {}
        self._details: dict[str, Any] = {}  # object_id → validated safe detail
        self._obj_seq = 0
        self._rec_seq = 0

    def records(self) -> tuple[EventRefusalRecord, ...]:
        return tuple(self._records)

    def record(
        self,
        *,
        detail_schema_ref: SchemaRef,
        detail_payload: Any,
        reason_code: str,
        idempotency_key: str,
        attempted_capability_ref: Any = None,
        attempted_schema_ref: SchemaRef | None = None,
        attempted_namespace: str | None = None,
        evidence_digests: tuple[NamedEvidenceDigest, ...] = (),
    ) -> EventRefusalRecord:
        # validate the detail against the audit-detail registry (raises on bad/unknown)
        detail = self._registry.validate(detail_schema_ref, detail_payload)
        detail_digest = content_digest(detail.model_dump(mode="json"))
        occurred_at = clock_now(self._clock)

        # idempotency by semantic content — probe with an audit-only placeholder id.
        probe_ref = PayloadRef(namespace="audit", object_id="probe", content_digest=detail_digest)
        candidate = EventRefusalRecord.build(
            record_id="probe", occurred_at=occurred_at, reason_code=reason_code,
            detail_schema_ref=detail_schema_ref, detail_payload_ref=probe_ref,
            idempotency_key=idempotency_key, attempted_capability_ref=attempted_capability_ref,
            attempted_schema_ref=attempted_schema_ref, attempted_namespace=attempted_namespace,
            evidence_digests=tuple(evidence_digests),
        )
        with self._lock:  # probe + append must be one step under concurrency
            existing = self._by_key.get(idempotency_key)
            if existing is not None:
                if existing.record_digest != candidate.record_digest:
                    raise IdempotencyConflict(
                        f"refusal idempotency key {idempotency_key!r} reused with different content"
                    )
                return existing

            # persist the safe detail in the audit namespace + seal the final record.
            self._obj_seq += 1
            object_id = f"audit-detail-{self._obj_seq}"
            detail_ref = PayloadRef(namespace="audit", object_id=object_id, content_digest=detail_digest)
            self._rec_seq += 1
            record = EventRefusalRecord.build(
                record_id=f"refusal-{self._rec_seq}", occurred_at=occurred_at, reason_code=reason_code,
                detail_schema_ref=detail_schema_ref, detail_payload_ref=detail_ref,
                idempotency_key=idempotency_key, attempted_capability_ref=attempted_capability_ref,
                attempted_schema_ref=attempted_schema_ref, attempted_namespace=attempted_namespace,
                evidence_digests=tuple(evidence_digests),
            )
            self._details[object_id] = detail
            self._records.append(record)
            self._by_key[idempotency_key] = record
            return record


# --------------------------------------------------------------------------- #
# RuntimeStores — the shared-backend wiring                                    #
# --------------------------------------------------------------------------- #
class RuntimeStores:
    """Wire the PayloadStore / EventStore / cell store / unit of work over ONE
    shared copy-on-write backend + lock, plus per-run budget-event sinks."""

    def __init__(
        self,
        *,
        resolver: SchemaRegistryResolver,
        clock: AuthoritativeClock,
        allowed_cell_namespaces: tuple[str, ...] = (),
    ) -> None:
        self._shared = _Shared()
        self._resolver = resolver
        self._clock = clock
        self._allowed_cell_namespaces = frozenset(allowed_cell_namespaces)
        self._run_budgets: dict[str, RunBudget] = {}
        self.payloads = PayloadStore(self._shared, resolver)
        self.events = EventStore(self._shared, resolver, clock)
        self.cells = RuntimeStateCellStore(self._shared, allowed_namespaces=self._allowed_cell_namespaces)
        self.unit_of_work = RuntimeUnitOfWork(
            self._shared, resolver, clock,
            allowed_cell_namespaces=self._allowed_cell_namespaces,
            run_budgets=self._run_budgets,
        )

    @property
    def resolver(self) -> SchemaRegistryResolver:
        return self._resolver

    def bind_run_budget(self, *, run_id: str, run_budget: RunBudget) -> None:
        """Bind the single :class:`RunBudget` authority for ``run_id``.

        Service-owned, first-bind-wins: an identical rebind is idempotent; a
        different RunBudget for the same run is rejected, so two unit-of-work
        batches can never validate against inconsistent maxima for one run.
        Mirrors the Task 1 ``BudgetLedger`` constructor guard: the event fold owns
        all holds, so the RunBudget must be maxima-only.
        """
        if (
            run_budget.reserved_tokens != 0
            or run_budget.reserved_llm_invocations != 0
            or run_budget.reserved_concurrency != 0
        ):
            raise EventStoreError(
                "bind_run_budget requires a maxima-only RunBudget (reserved_* == 0); "
                "the event fold owns all holds"
            )
        with self._shared.lock:
            existing = self._run_budgets.get(run_id)
            if existing is not None:
                if existing != run_budget:
                    raise EventStoreError(
                        f"run {run_id!r} already has a bound RunBudget with different "
                        "maxima/ledger; budget authority is bound once per run"
                    )
                return  # idempotent identical rebind
            self._run_budgets[run_id] = run_budget

    def budget_event_sink(self, *, run_id: str, ledger_id: str) -> RuntimeBudgetEventSink:
        return RuntimeBudgetEventSink(self._shared, self._clock, run_id=run_id, ledger_id=ledger_id)

    def payloads_object_count(self) -> int:
        return len(self._shared.backend.payloads)
