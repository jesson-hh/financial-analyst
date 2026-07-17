# -*- coding: utf-8 -*-
"""Phase 3 · Task 9 — exactly-once memory repositories + PIT-safe capture,
visibility-before-ranking selection and the pre-admission preparation service.

Everything here is a NARROW adapter over the injected Phase-2
``RuntimeStores`` (``RuntimeUnitOfWork`` / ``RuntimeStateCellStore`` /
``PayloadStore``): no repository owns an independent lock, transaction manager
or backend, and each claims only its reviewed subset of the already-sealed
seven-name namespace union :data:`PHASE3_MEMORY_STATE_CELL_NAMESPACES`.

The closed retrieval order (Global Constraint line 24 — memory PIT before
relevance) lives in :func:`select_memory`:

1. resolve/verify the exact frozen snapshot records;
2. build the VISIBLE set (``available_at <= as_of``, validity window,
   ``review_state=approved``, role/own/shared/borrowed grants, exact session);
3. only then take mandatory records in deterministic ID order, rank the
   remaining visible records and apply ``top_k``;
4. zero-hit fallback inspects only that same visible set — never
   ``AgentMemory.load_all()`` or an unfiltered FTS result.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Mapping

from guanlan_v2.orchestration.context import (
    ContextRuntimeRequirements,
    ContextSnapshot,
    DataContext,
    MemoryRecordRef,
    check_memory_session_scope,
    compute_context_subject_digest,
    verify_context_runtime_requirements,
)
from guanlan_v2.orchestration.digest import content_digest
from guanlan_v2.orchestration.eventstore import (
    IdempotencyConflict,
    PayloadPutCommand,
    RuntimeBatch,
    RuntimeStores,
    StagedPayloadKey,
    StagedTypedPayloadRef,
    StateCellCompareAndSwapCommand,
)
from guanlan_v2.orchestration.memory.adapters import (
    AgentMemoryAdapter,
    ConsoleMemoryAdapter,
    RawMemoryRow,
    RawSourceUnit,
)
from guanlan_v2.orchestration.memory.models import (
    LegacyCutoverEvidence,
    MemoryAuthorityError,
    MemoryConflictError,
    MemoryContextAuthority,
    MemoryContractError,
    MemoryCutoverAttestation,
    MemoryCutoverManifest,
    MemoryCutoverManifestEntry,
    MemoryCutoverPreparation,
    MemoryCutoverPreparationEntry,
    MemoryCutoverSourcePayload,
    MemoryPitError,
    MemoryQuery,
    MemoryRecord,
    MemoryReplayBinding,
    MemoryReviewEvidence,
    MemorySelection,
    MemorySelectionEntry,
    MemorySnapshot,
    MemorySnapshotCapturePreparation,
    MemorySnapshotEntry,
    MemorySourceStateEntry,
    MemorySourceStateSnapshot,
    PHASE3_MEMORY_STATE_CELL_NAMESPACES,
    ResolvedMemoryPolicy,
    build_context_memory_query,
    build_memory_record,
    derive_cutover_put_idempotency_key,
    derive_source_revision_token,
)
from guanlan_v2.orchestration.refs import (
    CapabilityRef,
    ContentRef,
    PayloadRef,
    SchemaRef,
    TypedPayloadRef,
)

__all__ = [
    "MEMORY_STATE_CELL_OWNERS",
    "validate_memory_namespace_wiring",
    "MemorySourceStateHeadRepository",
    "MemorySnapshotHeadRepository",
    "MemorySnapshotCaptureRepository",
    "MemoryCutoverRepository",
    "MemoryCutoverCoordinator",
    "compute_root_state_digest",
    "compute_source_state_candidate",
    "select_memory",
    "MemoryContextBinding",
    "MemoryContextPreparationService",
]

_SR = lambda name: SchemaRef(name=name, version="1")  # noqa: E731


def _typed(schema: str, ref: PayloadRef) -> TypedPayloadRef:
    return TypedPayloadRef(schema_ref=_SR(schema), payload_ref=ref)


#: the reviewed owner map — exactly one repository owner per namespace.
MEMORY_STATE_CELL_OWNERS: Mapping[str, str] = {
    "memory.cutover_preparation.v1": "cutover",
    "memory.proposal_preparation.v1": "proposal",
    "memory.snapshot_head.v1": "snapshot",
    "memory.snapshot_operation.v1": "snapshot",
    "memory.snapshot_preparation.v1": "snapshot",
    "memory.source_head.v1": "source",
    "memory.source_operation.v1": "source",
}


def validate_memory_namespace_wiring(allowed_namespaces: frozenset[str]) -> None:
    """Startup gate: the sealed store must contain the EXACT seven-name union.

    Missing namespaces fail startup; the owner map must cover exactly the union
    with one owner per name. (The store may additionally carry non-memory
    namespaces such as the Phase-2 prompt cell — those are not this gate's.)
    """
    union = set(PHASE3_MEMORY_STATE_CELL_NAMESPACES)
    missing = union - set(allowed_namespaces)
    if missing:
        raise MemoryContractError(
            f"memory state-cell namespaces missing from the sealed store: {sorted(missing)}"
        )
    if set(MEMORY_STATE_CELL_OWNERS) != union:
        raise MemoryContractError("the reviewed owner map must cover exactly the namespace union")
    extra_memory = {
        ns for ns in allowed_namespaces
        if ns.startswith("memory.") and ns not in union
    }
    if extra_memory:
        raise MemoryContractError(
            f"unreviewed extra memory state-cell namespace(s): {sorted(extra_memory)}"
        )


class _MemoryRepositoryBase:
    """Shared narrow-adapter plumbing: owned-subset validation + typed reads."""

    OWNER: str = ""
    OWNED: frozenset[str] = frozenset()

    def __init__(self, stores: RuntimeStores, *, registry_digest: str) -> None:
        validate_memory_namespace_wiring(stores.cells.allowed_namespaces)
        for ns in self.OWNED:
            if MEMORY_STATE_CELL_OWNERS.get(ns) != self.OWNER:
                raise MemoryContractError(
                    f"repository {type(self).__name__} claims namespace {ns!r} it does not own"
                )
        self._stores = stores
        self._registry_digest = registry_digest

    # -- reads ------------------------------------------------------------- #
    def _cell(self, namespace: str, key_digest: str) -> TypedPayloadRef | None:
        if namespace not in self.OWNED:
            raise MemoryContractError(
                f"{type(self).__name__} may not read foreign namespace {namespace!r}"
            )
        return self._stores.cells.load(namespace, key_digest)

    def _resolve(self, ref: TypedPayloadRef, schema: str) -> Any:
        if ref.schema_ref.name != schema or ref.schema_ref.version != "1":
            raise MemoryContractError(f"typed ref does not resolve {schema}@1")
        return self._stores.payloads.get(ref.payload_ref, expected_schema_ref=ref.schema_ref)

    # -- one-batch put + CAS helpers ---------------------------------------- #
    def _put_and_cas(
        self,
        *,
        batch_key: str,
        schema: str,
        payload: Any,
        put_key: str,
        cas: tuple[tuple[str, str, TypedPayloadRef | None], ...],
    ) -> TypedPayloadRef:
        """One atomic UoW: put ``payload`` + CAS every named cell to its staged ref."""
        staged = StagedPayloadKey(key="p0")
        commands = tuple(
            StateCellCompareAndSwapCommand(
                cell_namespace=ns, cell_key_digest=key, expected_value=expected,
                new_target=StagedTypedPayloadRef(
                    staged_key=staged, schema_ref=_SR(schema), namespace="main"),
            )
            for ns, key, expected in cas
        )
        # a one-level template whose values are the actual field objects (nested
        # models stay models so strict validation accepts them verbatim).
        template = {name: getattr(payload, name) for name in type(payload).model_fields}
        result = self._stores.unit_of_work.commit(RuntimeBatch(
            idempotency_key=batch_key,
            payload_puts=(PayloadPutCommand(
                staged_key=staged, schema_ref=_SR(schema), namespace="main",
                payload_template=template,
                registry_digest=self._registry_digest, idempotency_key=put_key),),
            cell_cas=commands,
        ))
        return result.staged_typed_ref("p0")


# --------------------------------------------------------------------------- #
# source-state head repository                                                 #
# --------------------------------------------------------------------------- #
def _key_digest(*parts: Any) -> str:
    return content_digest(list(parts))


class MemorySourceStateHeadRepository(_MemoryRepositoryBase):
    """Policy/session-independent source-state head, keyed by root binding."""

    OWNER = "source"
    OWNED = frozenset({"memory.source_head.v1", "memory.source_operation.v1"})

    def head_ref(self, root_binding_digest: str) -> TypedPayloadRef | None:
        return self._cell("memory.source_head.v1",
                          _key_digest("memory.source_head", root_binding_digest))

    def load(self, root_binding_digest: str) -> MemorySourceStateSnapshot | None:
        ref = self.head_ref(root_binding_digest)
        if ref is None:
            return None
        return self._resolve(ref, "MemorySourceStateSnapshot")

    def load_result(self, stable_scan_operation_key: str) -> TypedPayloadRef | None:
        return self._cell("memory.source_operation.v1",
                          _key_digest("memory.source_operation", stable_scan_operation_key))

    def advance_once(
        self, stable_scan_operation_key: str, candidate: MemorySourceStateSnapshot,
    ) -> TypedPayloadRef:
        """Atomically bind typed state + head CAS + operation-result CAS.

        Retry of the SAME completed operation returns the original ref even
        after a later operation advanced the head; same-key drift conflicts.
        """
        existing = self.load_result(stable_scan_operation_key)
        if existing is not None:
            # verify the recovered typed state (drift under the same key conflicts).
            stored = self._resolve(existing, "MemorySourceStateSnapshot")
            if stored.content_digest != candidate.content_digest:
                raise MemoryConflictError(
                    "stable-scan operation key reused with a semantically different state"
                )
            return existing
        expected_head = self.head_ref(candidate.root_binding_digest)
        if expected_head is not None:
            if expected_head.payload_ref.content_digest != candidate.previous_source_state_hash:
                raise MemoryConflictError(
                    "source-state candidate does not bind the current head as predecessor"
                )
        elif candidate.previous_source_state_ref is not None:
            raise MemoryConflictError("a genesis head cannot bind a predecessor")
        return self._put_and_cas(
            batch_key=f"mem-source-state:{stable_scan_operation_key}",
            schema="MemorySourceStateSnapshot",
            payload=candidate,
            put_key=f"mem-source-state-put:{stable_scan_operation_key}",
            cas=(
                ("memory.source_head.v1",
                 _key_digest("memory.source_head", candidate.root_binding_digest),
                 expected_head),
                ("memory.source_operation.v1",
                 _key_digest("memory.source_operation", stable_scan_operation_key),
                 None),
            ),
        )


# --------------------------------------------------------------------------- #
# snapshot head + exactly-once capture repository                               #
# --------------------------------------------------------------------------- #
class MemorySnapshotHeadRepository(_MemoryRepositoryBase):
    """Scoped snapshot head keyed ONLY by (scope_binding_digest, session)."""

    OWNER = "snapshot"
    OWNED = frozenset({
        "memory.snapshot_head.v1", "memory.snapshot_operation.v1",
        "memory.snapshot_preparation.v1",
    })

    @staticmethod
    def head_key(scope_binding_digest: str, memory_session_id: str | None) -> str:
        return _key_digest("memory.snapshot_head", scope_binding_digest, memory_session_id)

    def head_ref(self, scope_binding_digest: str, memory_session_id: str | None) -> TypedPayloadRef | None:
        return self._cell("memory.snapshot_head.v1",
                          self.head_key(scope_binding_digest, memory_session_id))

    def load(self, scope_binding_digest: str, memory_session_id: str | None) -> MemorySnapshot | None:
        ref = self.head_ref(scope_binding_digest, memory_session_id)
        if ref is None:
            return None
        return self._resolve(ref, "MemorySnapshot")


class MemorySnapshotCaptureRepository(MemorySnapshotHeadRepository):
    """Exactly-once ONLINE capture: registered preparation + snapshot commit."""

    def load_preparation(self, capture_operation_key: str) -> TypedPayloadRef | None:
        return self._cell("memory.snapshot_preparation.v1",
                          _key_digest("memory.snapshot_preparation", capture_operation_key))

    def prepare_once(
        self,
        capture_operation_key: str,
        build: Callable[[], MemorySnapshotCapturePreparation],
    ) -> tuple[MemorySnapshotCapturePreparation, TypedPayloadRef]:
        """Load-or-freeze the capture preparation, atomically, before any clock
        or newer-head read on the retry path."""
        existing = self.load_preparation(capture_operation_key)
        if existing is not None:
            prep = self._resolve(existing, "MemorySnapshotCapturePreparation")
            if prep.capture_operation_key != capture_operation_key:
                raise MemoryConflictError("recovered preparation binds a different operation key")
            return prep, existing
        prep = build()  # reads clock/head exactly once, only on the absent path
        if prep.capture_operation_key != capture_operation_key:
            raise MemoryConflictError("built preparation does not bind the operation key")
        ref = self._put_and_cas(
            batch_key=f"mem-snap-prep:{capture_operation_key}",
            schema="MemorySnapshotCapturePreparation",
            payload=prep,
            put_key=f"mem-snap-prep-put:{capture_operation_key}",
            cas=(("memory.snapshot_preparation.v1",
                  _key_digest("memory.snapshot_preparation", capture_operation_key),
                  None),),
        )
        return prep, ref

    def load_result(self, capture_operation_key: str) -> TypedPayloadRef | None:
        return self._cell("memory.snapshot_operation.v1",
                          _key_digest("memory.snapshot_operation", capture_operation_key))

    def capture_once(
        self,
        preparation: MemorySnapshotCapturePreparation,
        preparation_ref: TypedPayloadRef,
        snapshot: MemorySnapshot,
    ) -> TypedPayloadRef:
        """Atomically persist the snapshot + advance the scope head + record the
        operation result. Retry returns the original snapshot ref."""
        key = preparation.capture_operation_key
        existing = self.load_result(key)
        if existing is not None:
            stored = self._resolve(existing, "MemorySnapshot")
            if stored.content_digest != snapshot.content_digest:
                raise MemoryConflictError(
                    "capture operation key reused with a semantically different snapshot"
                )
            return existing
        if preparation_ref.payload_ref.content_digest != preparation.semantic_digest():
            raise MemoryConflictError("preparation_ref does not bind the given preparation")
        # the snapshot must copy the preparation-frozen identity exactly.
        if (snapshot.scope_binding_digest != preparation.scope_binding_digest
                or snapshot.memory_session_id != preparation.memory_session_id
                or snapshot.policy_digest != preparation.policy_digest
                or snapshot.source_state_ref.payload_ref.content_digest
                != preparation.source_state_hash
                or snapshot.previous_snapshot_hash != preparation.previous_snapshot_hash
                or snapshot.as_of != preparation.as_of
                or snapshot.capture_completed_at != preparation.capture_time):
            raise MemoryConflictError("snapshot drifted from its frozen capture preparation")
        expected_head = self.head_ref(
            preparation.scope_binding_digest, preparation.memory_session_id)
        expected_hash = (
            expected_head.payload_ref.content_digest if expected_head is not None else None
        )
        if expected_hash != preparation.previous_snapshot_hash:
            raise MemoryConflictError(
                "the scope head moved since this preparation froze its predecessor; "
                "a stale distinct operation never silently chooses a newer predecessor"
            )
        return self._put_and_cas(
            batch_key=f"mem-snap-capture:{key}",
            schema="MemorySnapshot",
            payload=snapshot,
            put_key=f"mem-snap-capture-put:{key}",
            cas=(
                ("memory.snapshot_head.v1",
                 self.head_key(preparation.scope_binding_digest, preparation.memory_session_id),
                 expected_head),
                ("memory.snapshot_operation.v1",
                 _key_digest("memory.snapshot_operation", key),
                 None),
            ),
        )


# --------------------------------------------------------------------------- #
# cutover repository + coordinator                                              #
# --------------------------------------------------------------------------- #
def compute_root_state_digest(units: tuple[RawSourceUnit, ...]) -> str:
    """The policy-independent digest of one stable scan (sorted logical units)."""
    return content_digest([
        [u.source_id, u.locator, u.store_content_digest]
        for u in sorted(units, key=lambda u: (u.source_id, u.locator))
    ])


class MemoryCutoverRepository(_MemoryRepositoryBase):
    """The one global legacy-adoption repository (narrow Phase-2 adapter)."""

    OWNER = "cutover"
    OWNED = frozenset({"memory.cutover_preparation.v1"})

    _PREP = "memory.cutover.preparation"
    _ATTEST = "memory.cutover.attestation"

    def load_preparation(self, admin_operation_key: str) -> TypedPayloadRef | None:
        return self._cell("memory.cutover_preparation.v1",
                          _key_digest(self._PREP, admin_operation_key))

    def prepare_once(
        self, admin_operation_key: str, preparation: MemoryCutoverPreparation,
    ) -> TypedPayloadRef:
        existing = self.load_preparation(admin_operation_key)
        if existing is not None:
            stored = self._resolve(existing, "MemoryCutoverPreparation")
            if stored.semantic_digest() != preparation.semantic_digest():
                raise MemoryConflictError(
                    "cutover admin-operation key reused with semantically different sources"
                )
            return existing
        if preparation.admin_operation_key != admin_operation_key:
            raise MemoryConflictError("preparation does not bind the admin operation key")
        return self._put_and_cas(
            batch_key=f"mem-cutover-prep:{admin_operation_key}",
            schema="MemoryCutoverPreparation",
            payload=preparation,
            put_key=f"mem-cutover-prep-put:{admin_operation_key}",
            cas=(("memory.cutover_preparation.v1",
                  _key_digest(self._PREP, admin_operation_key), None),),
        )

    def load(self) -> TypedPayloadRef | None:
        """The one global attestation ref (None until initialized)."""
        return self._cell("memory.cutover_preparation.v1", _key_digest(self._ATTEST))

    def initialize_once(
        self,
        manifest_ref: TypedPayloadRef,
        admin_actor: str,
        validated_root_state_digest: str,
        *,
        cutover_policy_digest: str,
        attested_at: datetime,
    ) -> TypedPayloadRef:
        existing = self.load()
        if existing is not None:
            stored: MemoryCutoverAttestation = self._resolve(existing, "MemoryCutoverAttestation")
            if (stored.manifest_ref.payload_ref.content_digest
                    != manifest_ref.payload_ref.content_digest
                    or stored.root_state_digest != validated_root_state_digest):
                raise MemoryConflictError(
                    "a different cutover attestation already exists; restart/new "
                    "session cannot initialize another baseline"
                )
            return existing
        attestation = MemoryCutoverAttestation(
            manifest_ref=manifest_ref,
            cutover_policy_digest=cutover_policy_digest,
            root_state_digest=validated_root_state_digest,
            admin_actor=admin_actor,
            attested_at=attested_at,
        )
        return self._put_and_cas(
            batch_key="mem-cutover-attest",
            schema="MemoryCutoverAttestation",
            payload=attestation,
            put_key="mem-cutover-attest-put",
            cas=(("memory.cutover_preparation.v1", _key_digest(self._ATTEST), None),),
        )


@dataclass(frozen=True)
class CutoverResult:
    """The frozen outputs of one completed legacy adoption."""

    preparation_ref: TypedPayloadRef
    manifest_ref: TypedPayloadRef
    attestation_ref: TypedPayloadRef
    genesis_source_state_ref: TypedPayloadRef
    root_binding_digest: str
    attested_at: datetime


class MemoryCutoverCoordinator:
    """Owns the ONE global process-shared cutover lease + reviewed root leases.

    The non-cyclic crash-recoverable order: stable admin key → load/verify or
    freeze the preparation → idempotent source-payload puts FROM the preparation
    → manifest → global-cutover lease → root leases in canonical logical-root
    order → equality-only re-scan → attestation → genesis source state →
    release. Exposes no mutation/refresh operation.
    """

    def __init__(
        self,
        *,
        repository: MemoryCutoverRepository,
        source_repository: MemorySourceStateHeadRepository,
        stores: RuntimeStores,
        registry_digest: str,
        lease_factory: Any,
        global_lease_root: Any,
        logical_roots: tuple[tuple[str, Any], ...],
    ) -> None:
        self._repo = repository
        self._source_repo = source_repository
        self._stores = stores
        self._registry_digest = registry_digest
        self._leases = lease_factory
        self._global_root = global_lease_root
        self._logical_roots = tuple(sorted(logical_roots, key=lambda t: t[0]))

    def perform_cutover(
        self,
        *,
        admin_operation_key: str,
        admin_actor: str,
        scan: Callable[[], tuple[RawSourceUnit, ...]],
        cutover_policy_digest: str,
        clock_now: Callable[[], datetime],
    ) -> CutoverResult:
        # -- (1) load-or-freeze the preparation (before any new initial scan) - #
        existing_prep_ref = self._repo.load_preparation(admin_operation_key)
        if existing_prep_ref is not None:
            preparation = self._repo._resolve(existing_prep_ref, "MemoryCutoverPreparation")
            prep_ref = existing_prep_ref
        else:
            units = tuple(sorted(scan(), key=lambda u: (u.source_id, u.locator)))
            if not units:
                raise MemoryContractError(
                    "an empty baseline cannot be adopted (no reviewed source unit)"
                )
            operation_id = "cutover." + content_digest([admin_operation_key])[:32]
            entries = tuple(
                MemoryCutoverPreparationEntry(
                    source_id=u.source_id, locator=u.locator,
                    store_content_digest=u.store_content_digest,
                    source_revision_token=derive_source_revision_token(
                        cutover_operation_id=operation_id, source_id=u.source_id,
                        locator=u.locator, store_content_digest=u.store_content_digest),
                    put_idempotency_key=derive_cutover_put_idempotency_key(
                        cutover_operation_id=operation_id, source_id=u.source_id,
                        locator=u.locator, store_content_digest=u.store_content_digest),
                )
                for u in units
            )
            preparation = MemoryCutoverPreparation(
                admin_operation_key=admin_operation_key,
                cutover_operation_id=operation_id, entries=entries)
            prep_ref = self._repo.prepare_once(admin_operation_key, preparation)

        # -- (2) idempotent source-payload puts FROM the frozen preparation --- #
        current_by_key = {
            (u.source_id, u.locator): u
            for u in scan()
        }
        payload_refs: dict[tuple[str, str], TypedPayloadRef] = {}
        for entry in preparation.entries:
            unit = current_by_key.get((entry.source_id, entry.locator))
            if unit is None or unit.store_content_digest != entry.store_content_digest:
                raise MemoryConflictError(
                    f"source ({entry.source_id},{entry.locator}) drifted from the frozen "
                    "preparation; the baseline is never refreshed or re-cut"
                )
            payload = MemoryCutoverSourcePayload(
                cutover_operation_id=preparation.cutover_operation_id,
                source_id=entry.source_id, locator=entry.locator,
                normalized_text=unit.text,
                store_content_digest=entry.store_content_digest,
                source_revision_token=entry.source_revision_token)
            pref = self._stores.payloads.put(
                _SR("MemoryCutoverSourcePayload"), payload,
                registry_digest=self._registry_digest, namespace="main",
                idempotency_key=entry.put_idempotency_key)
            payload_refs[(entry.source_id, entry.locator)] = _typed(
                "MemoryCutoverSourcePayload", pref)

        # -- (3) manifest ------------------------------------------------------ #
        manifest = MemoryCutoverManifest.build(
            cutover_operation_id=preparation.cutover_operation_id,
            entries=tuple(
                MemoryCutoverManifestEntry(
                    source_id=e.source_id, locator=e.locator,
                    store_content_digest=e.store_content_digest,
                    payload_ref=payload_refs[(e.source_id, e.locator)])
                for e in preparation.entries
            ))
        manifest_pref = self._stores.payloads.put(
            _SR("MemoryCutoverManifest"), manifest,
            registry_digest=self._registry_digest, namespace="main",
            idempotency_key=f"mem-cutover-manifest:{preparation.cutover_operation_id}")
        manifest_ref = _typed("MemoryCutoverManifest", manifest_pref)

        # -- (4) global lease → root leases → EQUALITY-ONLY validation scan ---- #
        global_lease = self._leases.lease_for(
            self._global_root, owner="memory-cutover", operation="global-cutover")
        global_lease.acquire()
        root_leases: tuple = ()
        try:
            root_leases = self._leases.acquire_ordered(
                self._logical_roots, owner="memory-cutover", operation="cutover")
            validation_units = tuple(sorted(scan(), key=lambda u: (u.source_id, u.locator)))
            expected = {
                (e.source_id, e.locator): e.store_content_digest
                for e in preparation.entries
            }
            actual = {
                (u.source_id, u.locator): u.store_content_digest
                for u in validation_units
            }
            if actual != expected:
                raise MemoryConflictError(
                    "equality validation scan does not equal the frozen preparation/"
                    "manifest (missing/extra/drifted locators); the preparation is "
                    "never refreshed and no replacement baseline is cut"
                )
            root_state_digest = compute_root_state_digest(validation_units)

            # -- (5) attestation (leases still held) --------------------------- #
            attested_at = clock_now()
            attestation_ref = self._repo.initialize_once(
                manifest_ref, admin_actor, root_state_digest,
                cutover_policy_digest=cutover_policy_digest, attested_at=attested_at)
            attestation = self._repo._resolve(attestation_ref, "MemoryCutoverAttestation")
            if attestation.root_state_digest != root_state_digest:
                raise MemoryConflictError(
                    "the current root-state digest no longer equals the attested "
                    "digest; ONLINE remains blocked"
                )

            # -- (6) genesis source-state (policy-independent) ------------------ #
            genesis_entries = tuple(
                MemorySourceStateEntry(
                    source_id=u.source_id, locator=u.locator, presence="present",
                    store_content_digest=u.store_content_digest,
                    continuity_epoch=0)
                for u in validation_units
            )
            root_binding_digest = content_digest(
                ["guanlan.memory.root-binding.v1",
                 [rid for rid, _ in self._logical_roots]])
            genesis = MemorySourceStateSnapshot.build(
                root_binding_digest=root_binding_digest,
                captured_at=attestation.attested_at,
                entries=genesis_entries,
                genesis_manifest_ref=manifest_ref,
                genesis_attestation_ref=attestation_ref)
            genesis_ref = self._source_repo.advance_once(
                f"cutover-genesis:{preparation.cutover_operation_id}", genesis)
        finally:
            for lease in reversed(root_leases):
                try:
                    lease.release()
                except Exception:  # noqa: BLE001 - release best effort on unwind
                    pass
            global_lease.release()

        return CutoverResult(
            preparation_ref=prep_ref, manifest_ref=manifest_ref,
            attestation_ref=attestation_ref, genesis_source_state_ref=genesis_ref,
            root_binding_digest=root_binding_digest,
            attested_at=attestation.attested_at)


# --------------------------------------------------------------------------- #
# conservative source-state candidate derivation                               #
# --------------------------------------------------------------------------- #
def compute_source_state_candidate(
    units: tuple[RawSourceUnit, ...],
    previous: MemorySourceStateSnapshot,
    previous_ref: TypedPayloadRef,
    *,
    captured_at: datetime,
) -> MemorySourceStateSnapshot | None:
    """Derive the successor source state, or ``None`` when nothing changed.

    Each content/marker change, missing or reappearing locator advances its
    epoch — even ``A → B → A`` and ``A → absent → A``; unchanged continuously
    present bytes/marker retain it. New locators start at epoch 0.
    """
    prev_by_key = {(e.source_id, e.locator): e for e in previous.entries}
    cur_by_key = {(u.source_id, u.locator): u for u in units}
    entries: list[MemorySourceStateEntry] = []
    changed = False
    for key in sorted(set(prev_by_key) | set(cur_by_key)):
        prev = prev_by_key.get(key)
        cur = cur_by_key.get(key)
        if cur is not None:
            marker = cur.apply_marker_id
            if prev is None:
                entries.append(MemorySourceStateEntry(
                    source_id=key[0], locator=key[1], presence="present",
                    store_content_digest=cur.store_content_digest,
                    apply_marker_id=marker, continuity_epoch=0))
                changed = True
            elif (prev.presence == "present"
                  and prev.store_content_digest == cur.store_content_digest
                  and prev.apply_marker_id == marker):
                entries.append(prev)  # unchanged: retain the epoch
            else:
                entries.append(MemorySourceStateEntry(
                    source_id=key[0], locator=key[1], presence="present",
                    store_content_digest=cur.store_content_digest,
                    apply_marker_id=marker,
                    continuity_epoch=prev.continuity_epoch + 1))
                changed = True
        else:
            assert prev is not None
            if prev.presence == "absent":
                entries.append(prev)  # continuously absent: retain
            else:
                entries.append(MemorySourceStateEntry(
                    source_id=key[0], locator=key[1], presence="absent",
                    continuity_epoch=prev.continuity_epoch + 1))
                changed = True
    if not changed:
        return None
    return MemorySourceStateSnapshot.build(
        root_binding_digest=previous.root_binding_digest,
        captured_at=captured_at,
        entries=tuple(entries),
        previous_source_state_ref=previous_ref,
        previous_source_state_hash=previous.content_digest)


# --------------------------------------------------------------------------- #
# visibility-before-ranking selection                                          #
# --------------------------------------------------------------------------- #
def _tokenize(text: str) -> set[str]:
    return {t for t in re.split(r"[^0-9a-zA-Z一-鿿]+", text.lower()) if t}


def _relevance(query_text: str, record_text: str) -> int:
    """Deterministic pure relevance: distinct query tokens present in the text."""
    q = _tokenize(query_text)
    if not q:
        return 0
    body = record_text.lower()
    return sum(1 for tok in q if tok in body)


def select_memory(
    query: MemoryQuery,
    records: tuple[tuple[MemoryRecord, MemoryRecordRef], ...],
    *,
    as_of: datetime,
    policy: ResolvedMemoryPolicy,
) -> tuple[MemorySelectionEntry, ...]:
    """The closed retrieval order — PIT/visibility strictly before relevance."""
    policy.require_digest(query.policy_digest)
    p = policy.policy

    # -- step 2: the visible set (storage/facade-space filtering) ------------ #
    visible: list[tuple[MemoryRecord, MemoryRecordRef]] = []
    for record, ref in records:
        if record.content_digest != ref.content_digest:
            raise MemoryPitError(
                f"record {record.record_id} does not match its frozen ref (tamper)"
            )
        if record.available_at > as_of:
            continue
        if record.valid_from > as_of:
            continue
        if record.valid_to is not None and not (as_of < record.valid_to):
            continue
        if record.review_state != "approved":
            continue
        if record.kind not in query.allowed_kinds:
            continue
        if record.scope not in query.allowed_scopes:
            continue
        if record.scope == "agent_own":
            if query.role != "worker":
                continue
            if record.owner_id != query.reader_id and record.owner_id not in query.borrowed_owners:
                continue
        if record.scope == "console_session":
            if query.memory_session_id is None:
                continue
            if record.session_id != query.memory_session_id:
                continue
        if len(record.text.encode("utf-8")) > p.max_record_bytes:
            raise MemoryContractError(
                f"record {record.record_id} exceeds the per-record byte bound; "
                "fail closed rather than truncate"
            )
        visible.append((record, ref))

    # -- step 3: mandatory in deterministic ID order, then rank + top_k ------- #
    mandatory = sorted(
        ((r, ref) for r, ref in visible if r.mandatory),
        key=lambda t: (t[1].record_id, t[1].revision_id))
    if len(mandatory) > p.max_mandatory:
        raise MemoryContractError(
            f"{len(mandatory)} mandatory records exceed the policy bound "
            f"{p.max_mandatory}; fail closed rather than drop a mandatory record"
        )
    rest = [(r, ref) for r, ref in visible if not r.mandatory]
    scored = [
        (_relevance(query.query_text, r.text), r, ref) for r, ref in rest
    ]
    if any(score > 0 for score, _r, _ref in scored):
        ranked = sorted(scored, key=lambda t: (
            -t[0], -t[1].importance, -t[1].available_at.timestamp(),
            t[2].record_id, t[2].revision_id))
    else:
        # zero-hit fallback: the SAME visible set, recency order — never a live
        # load_all()/unfiltered FTS result.
        ranked = sorted(scored, key=lambda t: (
            -t[1].available_at.timestamp(), t[2].record_id, t[2].revision_id))
    selected = ranked[: query.top_k]

    entries: list[MemorySelectionEntry] = []
    rank = 0
    for _record, ref in mandatory:
        entries.append(MemorySelectionEntry(rank=rank, record_ref=ref, mandatory=True))
        rank += 1
    for _score, _record, ref in selected:
        entries.append(MemorySelectionEntry(rank=rank, record_ref=ref, mandatory=False))
        rank += 1
    return tuple(entries)


# --------------------------------------------------------------------------- #
# MemoryContextPreparationService                                              #
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class MemoryContextBinding:
    """The Phase-1-builder inputs one prepared memory context supplies."""

    memory_snapshot_ref: TypedPayloadRef
    memory_snapshot_hash: str
    memory_selection_ref: TypedPayloadRef
    past_context_hash: str
    runtime_requirements_ref: TypedPayloadRef
    memory_session_id: str | None
    snapshot: MemorySnapshot
    selection: MemorySelection
    records: tuple[tuple[MemoryRecord, MemoryRecordRef], ...]
    selected_record_refs: tuple[MemoryRecordRef, ...]


class MemoryContextPreparationService:
    """Pre-admission ONLINE capture / PIT_REPLAY projection (closed mode matrix)."""

    def __init__(
        self,
        *,
        stores: RuntimeStores,
        registry_digest: str,
        policy: ResolvedMemoryPolicy,
        agent_adapter: AgentMemoryAdapter,
        console_adapter_factory: Callable[[str | None], ConsoleMemoryAdapter],
        clock: Any,
        cutover: CutoverResult,
        required_schema_registry_digest: str,
        required_catalog_digest: str,
        required_runtime_material_refs: tuple[ContentRef, ...] = (),
        required_capability_refs: tuple[CapabilityRef, ...] = (),
        required_bridge_ids: tuple[str, ...] = ("memory.runtime",),
    ) -> None:
        validate_memory_namespace_wiring(stores.cells.allowed_namespaces)
        self._stores = stores
        self._registry_digest = registry_digest
        self._policy = policy
        self._agent = agent_adapter
        self._console_factory = console_adapter_factory
        self._clock = clock
        self._cutover = cutover
        self._source_repo = MemorySourceStateHeadRepository(
            stores, registry_digest=registry_digest)
        self._capture_repo = MemorySnapshotCaptureRepository(
            stores, registry_digest=registry_digest)
        self._required_registry = required_schema_registry_digest
        self._required_catalog = required_catalog_digest
        self._required_materials = tuple(sorted(
            required_runtime_material_refs, key=lambda r: (r.id, r.version, r.content_digest)))
        self._required_capabilities = tuple(sorted(
            required_capability_refs, key=lambda r: (r.id, r.version, r.content_digest)))
        self._required_bridge_ids = tuple(sorted(required_bridge_ids))
        # legacy-eligibility map from the genesis state.
        genesis = self._resolve_typed(
            cutover.genesis_source_state_ref, "MemorySourceStateSnapshot")
        self._genesis_epochs = {
            (e.source_id, e.locator): e.continuity_epoch for e in genesis.entries
        }
        manifest = self._resolve_typed(cutover.manifest_ref, "MemoryCutoverManifest")
        self._baseline_payload_refs = {
            (e.source_id, e.locator): e.payload_ref for e in manifest.entries
        }
        self._attestation = self._resolve_typed(
            cutover.attestation_ref, "MemoryCutoverAttestation")

    # -- helpers ---------------------------------------------------------------- #
    def _resolve_typed(self, ref: TypedPayloadRef, schema: str) -> Any:
        if ref.schema_ref.name != schema or ref.schema_ref.version != "1":
            raise MemoryContractError(f"typed ref does not resolve {schema}@1")
        return self._stores.payloads.get(ref.payload_ref, expected_schema_ref=ref.schema_ref)

    def _now(self) -> datetime:
        from guanlan_v2.orchestration.runtime_clock import clock_now

        return clock_now(self._clock)

    def _scan(self, session_id: str | None) -> tuple[tuple[RawSourceUnit, ...], tuple[RawMemoryRow, ...]]:
        console = self._console_factory(session_id)
        units = tuple(sorted(
            self._agent.scan_units() + console.scan_units(),
            key=lambda u: (u.source_id, u.locator)))
        rows = self._agent.rows() + console.rows()
        return units, rows

    def scope_binding_digest(self) -> str:
        return content_digest(
            ["guanlan.memory.scope.v1", self._cutover.root_binding_digest])

    # -- capture ------------------------------------------------------------------ #
    def capture_snapshot(
        self, *, as_of: datetime, memory_session_id: str | None, operation_key: str,
    ) -> tuple[MemorySnapshot, TypedPayloadRef, tuple[tuple[MemoryRecord, MemoryRecordRef], ...]]:
        # (1) stable scan + source-state advance (policy-independent).
        units, rows = self._scan(memory_session_id)
        head_state = self._source_repo.load(self._cutover.root_binding_digest)
        head_ref = self._source_repo.head_ref(self._cutover.root_binding_digest)
        if head_state is None or head_ref is None:
            raise MemoryContractError(
                "missing source-state genesis; ONLINE preparation is blocked until "
                "the reviewed cutover evidence is restored"
            )
        candidate = compute_source_state_candidate(
            units, head_state, head_ref, captured_at=self._now())
        if candidate is not None:
            state_ref = self._source_repo.advance_once(
                f"scan:{operation_key}", candidate)
            state = candidate
        else:
            state_ref, state = head_ref, head_state

        # (2) freeze the capture preparation (clock/head read exactly once).
        scope_digest = self.scope_binding_digest()

        def _build() -> MemorySnapshotCapturePreparation:
            prev_ref = self._capture_repo.head_ref(scope_digest, memory_session_id)
            prev_hash = (
                prev_ref.payload_ref.content_digest if prev_ref is not None else None
            )
            return MemorySnapshotCapturePreparation(
                capture_operation_key=operation_key,
                capture_operation_id="capture." + content_digest([operation_key])[:32],
                capture_time=self._now(),
                as_of=as_of,
                scope_binding_digest=scope_digest,
                memory_session_id=memory_session_id,
                policy_digest=self._policy.policy.policy_digest,
                source_state_ref=state_ref,
                source_state_hash=state.content_digest,
                previous_snapshot_ref=prev_ref,
                previous_snapshot_hash=prev_hash,
            )

        preparation, preparation_ref = self._capture_repo.prepare_once(operation_key, _build)

        # (3) build records from the FROZEN source state / predecessor / time.
        prev_records: dict[tuple[str, str], tuple[MemoryRecord, MemoryRecordRef]] = {}
        if preparation.previous_snapshot_ref is not None:
            prev_snap = self._resolve_typed(preparation.previous_snapshot_ref, "MemorySnapshot")
            for entry in prev_snap.entries:
                rec = self._resolve_typed(entry.record_payload_ref, "MemoryRecord")
                prev_records[(rec.record_id, rec.revision_id)] = (rec, entry.record_ref)

        records = self._build_records(
            rows=rows, state=state, capture_time=preparation.capture_time,
            prev_records=prev_records)

        # (4) persist records + snapshot exactly once, advance the scope head.
        record_pairs: list[tuple[MemoryRecord, MemoryRecordRef, TypedPayloadRef]] = []
        for record, ref in records:
            pref = self._stores.payloads.put(
                _SR("MemoryRecord"), record,
                registry_digest=self._registry_digest, namespace="main",
                idempotency_key=f"mem-record:{record.record_id}:{record.revision_id}")
            record_pairs.append((record, ref, _typed("MemoryRecord", pref)))
        entries = tuple(sorted(
            (MemorySnapshotEntry(record_ref=ref, record_payload_ref=typed)
             for _record, ref, typed in record_pairs),
            key=lambda e: (e.record_ref.record_id, e.record_ref.revision_id,
                           e.record_ref.content_digest)))
        snapshot = MemorySnapshot.build(
            as_of=preparation.as_of,
            capture_completed_at=preparation.capture_time,
            memory_session_id=memory_session_id,
            scope_binding_digest=scope_digest,
            cutover_attestation_ref=self._cutover.attestation_ref,
            source_state_ref=preparation.source_state_ref,
            previous_snapshot_hash=preparation.previous_snapshot_hash,
            previous_snapshot_ref=preparation.previous_snapshot_ref,
            policy_digest=preparation.policy_digest,
            entries=entries)
        snapshot_ref = self._capture_repo.capture_once(preparation, preparation_ref, snapshot)
        return snapshot, snapshot_ref, records

    def _build_records(
        self,
        *,
        rows: tuple[RawMemoryRow, ...],
        state: MemorySourceStateSnapshot,
        capture_time: datetime,
        prev_records: dict[tuple[str, str], tuple[MemoryRecord, MemoryRecordRef]],
    ) -> tuple[tuple[MemoryRecord, MemoryRecordRef], ...]:
        p = self._policy.policy
        out: list[tuple[MemoryRecord, MemoryRecordRef]] = []
        seen_ids: set[str] = set()
        for row in rows:
            mapping = p.mapping_for(row.source_id)
            entry = state.entry_for(row.source_id, row.locator)
            if entry.presence != "present":
                continue  # a vanished source contributes no record to THIS snapshot
            genesis_epoch = self._genesis_epochs.get((row.source_id, row.locator))
            legacy_ok = (
                genesis_epoch is not None and entry.continuity_epoch == genesis_epoch
            )
            if legacy_ok:
                evidence = MemoryReviewEvidence(evidence=LegacyCutoverEvidence(
                    attestation_ref=self._cutover.attestation_ref,
                    source_payload_ref=self._baseline_payload_refs[
                        (row.source_id, row.locator)],
                    genesis_continuity_epoch=genesis_epoch))
                ev_pref = self._stores.payloads.put(
                    _SR("MemoryReviewEvidence"), evidence,
                    registry_digest=self._registry_digest, namespace="main",
                    idempotency_key=f"mem-evidence:{evidence.semantic_digest()}")
                review_state, review_basis = "approved", "legacy_cutover"
                ev_ref: TypedPayloadRef | None = _typed("MemoryReviewEvidence", ev_pref)
                ev_model: MemoryReviewEvidence | None = evidence
                base_time = self._attestation.attested_at
            else:
                # conservative: a post-baseline direct write stays PENDING until
                # exact Decision-backed applied evidence exists (Stage C).
                review_state, review_basis, ev_ref, ev_model = "pending", None, None, None
                base_time = capture_time
            record, ref = build_memory_record(
                source_entry=entry,
                owner_id=row.owner_id,
                scope=row.scope,
                session_id=row.session_id,
                kind=mapping.kind,
                text=row.text,
                created_at=base_time,
                valid_from=base_time,
                valid_to=None,
                available_at=base_time,
                review_state=review_state,
                review_basis=review_basis,
                review_evidence=ev_model,
                review_evidence_ref=ev_ref,
                importance=mapping.importance,
                mandatory=bool(mapping.mandatory or row.mandatory_hint),
                identity=row.identity,
            )
            if record.record_id in seen_ids:
                continue  # curator-transition duplicate (main + archive): keep first
            seen_ids.add(record.record_id)
            prev = prev_records.get((record.record_id, record.revision_id))
            if prev is not None:
                out.append(prev)  # byte-stable revision reuse (original availability)
                continue
            # a NEW revision (new/changed content, epoch or evidence) keeps its
            # capture-completed availability — never backdated from mtime/Git/prose.
            out.append((record, ref))
        return tuple(out)

    # -- ONLINE ------------------------------------------------------------------- #
    def prepare_online(
        self,
        data_context: DataContext,
        authority: MemoryContextAuthority,
        *,
        query_text: str,
        top_k: int,
        operation_key: str,
    ) -> MemoryContextBinding:
        snapshot, snapshot_ref, records = self.capture_snapshot(
            as_of=data_context.as_of,
            memory_session_id=authority.memory_session_id,
            operation_key=operation_key)
        query = build_context_memory_query(
            query_text, top_k, authority=authority, policy=self._policy)
        q_pref = self._stores.payloads.put(
            _SR("MemoryQuery"), query,
            registry_digest=self._registry_digest, namespace="main",
            idempotency_key=f"mem-query:{operation_key}")
        query_ref = _typed("MemoryQuery", q_pref)
        entries = select_memory(
            query, records, as_of=data_context.as_of, policy=self._policy)
        selection = MemorySelection.build(
            snapshot_digest=snapshot.content_digest,
            query_ref=query_ref,
            query_digest=q_pref.content_digest,
            entries=entries)
        s_pref = self._stores.payloads.put(
            _SR("MemorySelection"), selection,
            registry_digest=self._registry_digest, namespace="main",
            idempotency_key=f"mem-selection:{operation_key}")
        selection_ref = _typed("MemorySelection", s_pref)
        requirements = ContextRuntimeRequirements.build(
            context_subject_digest=compute_context_subject_digest(
                data_context=data_context,
                memory_snapshot_ref=snapshot_ref,
                memory_selection_ref=selection_ref,
                memory_session_id=authority.memory_session_id),
            required_schema_registry_digest=self._required_registry,
            required_catalog_digest=self._required_catalog,
            required_runtime_material_refs=self._required_materials,
            required_capability_refs=self._required_capabilities,
            required_bridge_ids=self._required_bridge_ids)
        r_pref = self._stores.payloads.put(
            _SR("ContextRuntimeRequirements"), requirements,
            registry_digest=self._registry_digest, namespace="main",
            idempotency_key=f"mem-requirements:{operation_key}")
        return MemoryContextBinding(
            memory_snapshot_ref=snapshot_ref,
            memory_snapshot_hash=snapshot_ref.payload_ref.content_digest,
            memory_selection_ref=selection_ref,
            past_context_hash=selection_ref.payload_ref.content_digest,
            runtime_requirements_ref=_typed("ContextRuntimeRequirements", r_pref),
            memory_session_id=authority.memory_session_id,
            snapshot=snapshot,
            selection=selection,
            records=records,
            selected_record_refs=tuple(e.record_ref for e in selection.entries))

    # -- PIT_REPLAY ---------------------------------------------------------------- #
    def prepare_pit_replay(
        self,
        data_context: DataContext,
        authority: MemoryContextAuthority,
        *,
        prior_context_ref: TypedPayloadRef,
    ) -> MemoryReplayBinding:
        """Project a previously persisted binding; NO capture, clock or live store.

        The ref alone is never authority: the resolved snapshot's session/scopes
        must match the authenticated authority, and every typed ref must resolve
        its exact schema/content.
        """
        if (prior_context_ref.schema_ref.name != "ContextSnapshot"
                or prior_context_ref.schema_ref.version != "1"
                or prior_context_ref.payload_ref.namespace != "main"):
            raise MemoryAuthorityError("prior_context_ref must resolve main ContextSnapshot@1")
        try:
            ctx: ContextSnapshot = self._stores.payloads.get(
                prior_context_ref.payload_ref,
                expected_schema_ref=prior_context_ref.schema_ref)
        except Exception as exc:
            raise MemoryAuthorityError(
                f"prior ContextSnapshot does not resolve: {exc}"
            ) from exc
        # data-context / as-of identity.
        if ctx.data_context.semantic_digest() != data_context.semantic_digest():
            raise MemoryAuthorityError(
                "PIT_REPLAY data context does not equal the persisted binding"
            )
        if ctx.data_context.as_of != data_context.as_of:
            raise MemoryAuthorityError("PIT_REPLAY as_of drifted from the persisted binding")
        # session authority (None↔session and leaked foreign session fail).
        try:
            check_memory_session_scope(
                authority_session_id=authority.memory_session_id,
                requested_session_id=ctx.memory_session_id)
        except ValueError as exc:
            raise MemoryAuthorityError(str(exc)) from exc
        if ctx.memory_session_id != authority.memory_session_id:
            raise MemoryAuthorityError(
                "PIT_REPLAY authority session does not equal the persisted binding session"
            )
        # exact typed snapshot / selection / requirements closure.
        snapshot: MemorySnapshot = self._resolve_typed(ctx.memory_snapshot_ref, "MemorySnapshot")
        if snapshot.policy_digest != self._policy.policy.policy_digest:
            raise MemoryAuthorityError("persisted snapshot binds a different policy")
        selection: MemorySelection = self._resolve_typed(
            ctx.memory_selection_ref, "MemorySelection")
        if selection.snapshot_digest != snapshot.content_digest:
            raise MemoryAuthorityError("persisted selection does not bind the snapshot")
        self._resolve_typed(selection.query_ref, "MemoryQuery")
        if ctx.runtime_requirements_ref is None:
            raise MemoryAuthorityError("a memory-bound context requires runtime requirements")
        requirements: ContextRuntimeRequirements = self._resolve_typed(
            ctx.runtime_requirements_ref, "ContextRuntimeRequirements")
        verify_context_runtime_requirements(ctx, requirements)
        if (requirements.required_schema_registry_digest != self._required_registry
                or requirements.required_catalog_digest != self._required_catalog):
            raise MemoryAuthorityError(
                "persisted runtime requirements bind a different registry/catalog"
            )
        # lineage: source state + predecessor resolve exactly.
        self._resolve_typed(snapshot.source_state_ref, "MemorySourceStateSnapshot")
        if snapshot.previous_snapshot_ref is not None:
            self._resolve_typed(snapshot.previous_snapshot_ref, "MemorySnapshot")
        return MemoryReplayBinding(
            context_snapshot_ref=prior_context_ref,
            memory_snapshot_ref=ctx.memory_snapshot_ref,
            memory_snapshot_hash=ctx.memory_snapshot_hash,
            memory_selection_ref=ctx.memory_selection_ref,
            past_context_hash=ctx.past_context_hash,
            runtime_requirements_ref=ctx.runtime_requirements_ref,
            memory_session_id=ctx.memory_session_id,
            source_state_ref=snapshot.source_state_ref)


# re-exported for tests: IdempotencyConflict is the shared Phase-2 conflict type.
_ = IdempotencyConflict
