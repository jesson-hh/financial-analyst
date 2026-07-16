"""Run context, budget and immutable snapshot contracts.

This module freezes the *value* layer that a run reasons over — the point-in-time
clock, the immutable :class:`DataContext`, the frozen :class:`ContextSnapshot` /
:class:`InputSnapshot` a Plan and its workers see, the run-level
:class:`RunContext`, and the :class:`RunBudget` / :class:`BudgetReservation`
accounting values. Every model is a strict, frozen, versioned
:class:`~guanlan_v2.orchestration.digest.DigestModel`; **no runtime behavior**
(no ``PitGuard``, no ``BudgetLedger``, no persistence, no reservation mutation)
lives here — only models plus *pure* builders / validators. Later phases
construct guards, ledgers and services *from* these frozen values.

Reviewed identity partitions
----------------------------
* **audit** (excluded from the semantic digest): storage-assigned locators
  (``data_snapshot_id``, ``memory_snapshot_id``, ``snapshot_id``,
  ``reservation_id``, ``cancellation_token_id``, ``context_snapshot_id``) and
  every builder / freeze / settle **wall-clock** (``built_at`` / ``created_at`` /
  ``reserved_at`` / ``settled_at``). Relocating byte-identical content under a
  new id — or re-freezing a minute later — leaves the semantic identity stable.
* **semantic** (the content identity): the clock as-of / calendar / mode /
  backend / strictness, the resolved vendor chains, every
  source-config / registry / routing / snapshot-content / vintage digest, the
  memory snapshot / past-context hashes, the exact selected memory record
  revisions, the ledger identity and the exact reserved / actual amounts.

Self-sealed snapshots
---------------------
:class:`ContextSnapshot`, :class:`InputSnapshot`, :class:`EmptyMemorySnapshot`
and :class:`EmptyMemorySelection` seal their own ``content_digest`` (in
``SELF_DIGEST_FIELDS``); use the pure ``.build`` classmethods (or
:func:`build_empty_memory_binding`). A persisted record whose declared digest
does not match its recomputed canonical digest is rejected on load.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, ClassVar, Literal, NamedTuple

from pydantic import model_validator

from guanlan_v2.orchestration.digest import (
    DigestHex,
    DigestModel,
    NonEmptyStr,
    NonNegativeInt,
    PositiveInt,
    UtcDateTime,
    content_digest,
)
from guanlan_v2.orchestration.enums import DataBackend, DataMode
from guanlan_v2.orchestration.refs import (
    CapabilityRef,
    ContentRef,
    LogicalId,
    PayloadRef,
    SchemaRef,
    TypedPayloadRef,
    validate_typed_ref_tuple,
)
from guanlan_v2.orchestration.schemas import ArtifactRef

__all__ = [
    "ClockSpec",
    "DataContext",
    "MemoryRecordRef",
    "EmptyMemorySnapshot",
    "EmptyMemorySelection",
    "EmptyMemoryBinding",
    "build_empty_memory_binding",
    "verify_memory_record_ref",
    "check_memory_session_scope",
    "ContextSnapshot",
    "ContextRuntimeRequirements",
    "compute_context_subject_digest",
    "verify_context_runtime_requirements",
    "InputArtifactBinding",
    "InputSnapshot",
    "RunBudget",
    "BudgetReservation",
    "RunContext",
    "ReservationStatus",
    "BudgetScopeType",
]

#: Closed reservation lifecycle vocabulary.
ReservationStatus = Literal["reserved", "settled", "released"]
#: Closed budget scope vocabulary — every scope carries the same ledger-identity
#: contract; a run has a single ledger covering all of them.
BudgetScopeType = Literal[
    "bootstrap", "planner", "plan", "node", "schema_repair", "retry"
]

#: Well-formed but deliberately-wrong digest used by the ``build`` classmethods
#: when field values are malformed: it forces the validating constructor to
#: surface the real ``ValidationError`` and can never seal a valid record.
_DIGEST_PLACEHOLDER = "0" * 64


def _require_aware_utc(v: datetime) -> datetime:
    """Reject a naive datetime; normalize an aware one to UTC.

    Mirrors :data:`guanlan_v2.orchestration.digest.UtcDateTime` for the pure
    helper :func:`verify_memory_record_ref`, whose ``available_at`` argument is a
    plain ``datetime`` (not a validated model field).
    """
    if v.tzinfo is None or v.tzinfo.utcoffset(v) is None:
        raise ValueError("naive datetime is not allowed; a tz-aware value is required")
    return v.astimezone(timezone.utc)


# --------------------------------------------------------------------------- #
# Clock + data context                                                        #
# --------------------------------------------------------------------------- #
class ClockSpec(DigestModel):
    """The frozen point-in-time a run reasons over.

    ``as_of`` is the reviewed instant (tz-aware, canonicalized to UTC — naive
    rejected); ``timezone`` records the intended reasoning zone (e.g.
    ``Asia/Shanghai``) and ``calendar_id`` the trading calendar. Every field is
    semantic content — a clock has no random id or wall-clock of its own.
    """

    schema_version: Literal["1"] = "1"
    as_of: UtcDateTime
    timezone: NonEmptyStr
    calendar_id: NonEmptyStr
    clock_version: NonEmptyStr = "1"


class DataContext(DigestModel):
    """Immutable, frozen description of the data universe a run may read.

    Contains **no** ``PitGuard``, source handler, credential, cache path or
    mutable registry object — later phases construct those from this value. The
    top-level ``as_of`` / ``calendar_id`` must equal ``clock.as_of`` /
    ``clock.calendar_id`` (same instant, same calendar). ``data_snapshot_id`` is a
    storage-only audit locator and ``built_at`` is the builder wall-clock; both
    are excluded from the semantic projection, so relocating identical content
    under a new id — or re-freezing later — leaves the semantic identity stable.
    Everything else (mode / backend / strictness, resolved vendor chains and each
    config / registry / routing / snapshot-content / vintage digest) is semantic.

    Mode matrix: both ``ONLINE`` and ``PIT_REPLAY`` carry a real snapshot locator
    and content digest. ``PIT_REPLAY`` additionally requires ``strict_pit=True``,
    a non-``LIVE`` backend and a vintage manifest digest; ``ONLINE`` may freeze a
    capture-root content digest without a vintage or live-isolation claim.
    """

    schema_version: Literal["1"] = "1"
    as_of: UtcDateTime
    clock: ClockSpec
    mode: DataMode
    backend: DataBackend
    strict_pit: bool
    calendar_id: NonEmptyStr

    resolved_vendor_chains: dict[LogicalId, tuple[LogicalId, ...]]
    source_config_digest: DigestHex
    source_registry_digest: DigestHex
    routing_snapshot_digest: DigestHex

    data_snapshot_id: NonEmptyStr
    data_snapshot_content_digest: DigestHex
    vintage_manifest_digest: DigestHex | None = None

    built_at: UtcDateTime

    #: storage locator + builder wall-clock are audit-only.
    SEMANTIC_EXCLUDE: ClassVar[frozenset[str]] = frozenset(
        {"data_snapshot_id", "built_at"}
    )

    @model_validator(mode="after")
    def _coherent(self) -> "DataContext":
        if self.as_of != self.clock.as_of:
            raise ValueError(
                "DataContext.as_of and clock.as_of must represent the same instant"
            )
        if self.calendar_id != self.clock.calendar_id:
            raise ValueError("DataContext.calendar_id must match clock.calendar_id")
        if self.mode is DataMode.PIT_REPLAY:
            if not self.strict_pit:
                raise ValueError("PIT_REPLAY requires strict_pit=True")
            if self.backend is DataBackend.LIVE:
                raise ValueError("PIT_REPLAY requires a non-LIVE backend")
            if self.vintage_manifest_digest is None:
                raise ValueError("PIT_REPLAY requires a vintage_manifest_digest")
        return self


# --------------------------------------------------------------------------- #
# Memory references + empty-memory Phase 1 compatibility facts                #
# --------------------------------------------------------------------------- #
class MemoryRecordRef(DigestModel):
    """A semantic reference to one exact accepted memory revision.

    ``record_id`` + ``revision_id`` name the revision, ``available_at`` is the
    tz-aware time it became knowable (naive rejected) and ``content_digest`` is
    the digest of the referenced record. It carries no filesystem path, mutable
    score, :class:`PayloadRef`, review writer or storage handle; later phases map
    it to stored evidence without extending this ABI.
    """

    schema_version: Literal["1"] = "1"
    record_id: LogicalId
    revision_id: NonEmptyStr
    available_at: UtcDateTime
    content_digest: DigestHex


class EmptyMemorySnapshot(DigestModel):
    """A strict, immutable Phase 1 compatibility fact: a provably empty snapshot.

    ``records`` is always the empty tuple and ``content_digest`` self-seals the
    canonical empty-snapshot identity. Built only by
    :func:`build_empty_memory_binding`; a persisted copy with a non-empty record
    tuple or a mismatched declared digest is rejected on load.
    """

    schema_version: Literal["1"] = "1"
    records: tuple[MemoryRecordRef, ...] = ()
    content_digest: DigestHex

    SELF_DIGEST_FIELDS: ClassVar[frozenset[str]] = frozenset({"content_digest"})

    @model_validator(mode="after")
    def _verify(self) -> "EmptyMemorySnapshot":
        if self.records:
            raise ValueError("EmptyMemorySnapshot must have an empty record tuple")
        if self.content_digest != self.semantic_digest():
            raise ValueError("declared content_digest does not match canonical digest")
        return self


class EmptyMemorySelection(DigestModel):
    """A strict, immutable Phase 1 compatibility fact: an empty selection.

    ``snapshot_digest`` binds the empty snapshot it was selected from and
    ``content_digest`` self-seals the canonical empty-selection identity (the
    ``past_context_hash`` of a no-memory context). Built only by
    :func:`build_empty_memory_binding`.
    """

    schema_version: Literal["1"] = "1"
    records: tuple[MemoryRecordRef, ...] = ()
    snapshot_digest: DigestHex
    content_digest: DigestHex

    SELF_DIGEST_FIELDS: ClassVar[frozenset[str]] = frozenset({"content_digest"})

    @model_validator(mode="after")
    def _verify(self) -> "EmptyMemorySelection":
        if self.records:
            raise ValueError("EmptyMemorySelection must have an empty record tuple")
        if self.content_digest != self.semantic_digest():
            raise ValueError("declared content_digest does not match canonical digest")
        return self


#: canonical registered schema identities of the two Phase-1 empty-memory facts.
_EMPTY_MEMORY_SNAPSHOT_SCHEMA_REF = SchemaRef(name="EmptyMemorySnapshot", version="1")
_EMPTY_MEMORY_SELECTION_SCHEMA_REF = SchemaRef(name="EmptyMemorySelection", version="1")
#: reviewed, deterministic dereference locators for the persisted empty facts.
#: ``object_id`` is audit-only, so a fixed canonical value never affects any
#: semantic digest; it only names *where* a no-memory runtime persisted the fact.
_EMPTY_MEMORY_SNAPSHOT_OBJECT_ID = "empty-memory-snapshot-main"
_EMPTY_MEMORY_SELECTION_OBJECT_ID = "empty-memory-selection-main"
#: registered schema key a present ``runtime_requirements_ref`` must resolve.
_RUNTIME_REQUIREMENTS_SCHEMA_NAME = "ContextRuntimeRequirements"
_RUNTIME_REQUIREMENTS_SCHEMA_VERSION = "1"


class EmptyMemoryBinding(NamedTuple):
    """The output of :func:`build_empty_memory_binding` — models + canonical hashes.

    A no-memory runtime persists ``snapshot`` and ``selection`` in the ``main``
    namespace and uses ``snapshot_hash`` / ``past_context_hash`` as a
    :class:`ContextSnapshot`'s ``memory_snapshot_hash`` / ``past_context_hash``.
    ``memory_snapshot_ref`` / ``memory_selection_ref`` are the exact
    :class:`TypedPayloadRef`s wrapping each canonical fact (its
    ``EmptyMemorySnapshot@1`` / ``EmptyMemorySelection@1`` schema identity plus its
    main-namespace content digest) so the no-memory runtime can populate the new
    typed :class:`ContextSnapshot` fields directly. All hashes are canonical
    content digests — never random placeholders.
    """

    snapshot: EmptyMemorySnapshot
    selection: EmptyMemorySelection
    snapshot_hash: DigestHex
    past_context_hash: DigestHex
    memory_snapshot_ref: TypedPayloadRef
    memory_selection_ref: TypedPayloadRef


def build_empty_memory_binding() -> EmptyMemoryBinding:
    """Deterministically build the two canonical empty-memory models + digests.

    This is the *sole* pure builder for the pre-Phase-3 no-memory universe. It
    seals the empty snapshot, then the empty selection (which binds the snapshot
    digest), wraps each as the exact main-namespace :class:`TypedPayloadRef`, and
    returns both models, their canonical content digests and refs for a runtime to
    persist. Blank / random locator-hash pairs are never produced.
    """
    snap_digest = EmptyMemorySnapshot.digest_of_fields(projection="semantic", records=())
    snapshot = EmptyMemorySnapshot(records=(), content_digest=snap_digest)
    sel_digest = EmptyMemorySelection.digest_of_fields(
        projection="semantic", records=(), snapshot_digest=snapshot.content_digest
    )
    selection = EmptyMemorySelection(
        records=(), snapshot_digest=snapshot.content_digest, content_digest=sel_digest
    )
    memory_snapshot_ref = TypedPayloadRef(
        schema_ref=_EMPTY_MEMORY_SNAPSHOT_SCHEMA_REF,
        payload_ref=PayloadRef(
            namespace="main",
            object_id=_EMPTY_MEMORY_SNAPSHOT_OBJECT_ID,
            content_digest=snapshot.content_digest,
        ),
    )
    memory_selection_ref = TypedPayloadRef(
        schema_ref=_EMPTY_MEMORY_SELECTION_SCHEMA_REF,
        payload_ref=PayloadRef(
            namespace="main",
            object_id=_EMPTY_MEMORY_SELECTION_OBJECT_ID,
            content_digest=selection.content_digest,
        ),
    )
    return EmptyMemoryBinding(
        snapshot=snapshot,
        selection=selection,
        snapshot_hash=snapshot.content_digest,
        past_context_hash=selection.content_digest,
        memory_snapshot_ref=memory_snapshot_ref,
        memory_selection_ref=memory_selection_ref,
    )


#: the canonical empty-memory content digests, keyed by :func:`build_empty_memory_binding`.
#: A ``ContextSnapshot`` whose ``(memory_snapshot_hash, past_context_hash)`` equal
#: this exact pair is the pre-Phase-3 no-memory universe and carries no runtime
#: requirements ref; any other pair is a real (Phase 3) memory binding and must.
_EMPTY_MEMORY_BINDING = build_empty_memory_binding()
_CANONICAL_EMPTY_MEMORY_SNAPSHOT_HASH: DigestHex = _EMPTY_MEMORY_BINDING.snapshot_hash
_CANONICAL_EMPTY_PAST_CONTEXT_HASH: DigestHex = _EMPTY_MEMORY_BINDING.past_context_hash


def verify_memory_record_ref(
    ref: MemoryRecordRef,
    *,
    record_id: str,
    revision_id: str,
    available_at: datetime,
    content_digest: str,
) -> None:
    """Pure Phase 1 identity checker for one selected memory payload.

    A later memory facade must call this for each payload it maps to a
    :class:`MemoryRecordRef`. Missing, naive or mismatched availability is *not*
    repaired here — it raises. ``available_at`` is compared as an instant (a
    tz-aware value in any offset that names the same instant matches).
    """
    if ref.record_id != record_id:
        raise ValueError(
            f"record_id mismatch: ref {ref.record_id!r} != payload {record_id!r}"
        )
    if ref.revision_id != revision_id:
        raise ValueError(
            f"revision_id mismatch: ref {ref.revision_id!r} != payload {revision_id!r}"
        )
    aware = _require_aware_utc(available_at)  # raises on naive
    if ref.available_at != aware:
        raise ValueError("available_at mismatch between ref and payload")
    if ref.content_digest != content_digest:
        raise ValueError("content_digest mismatch between ref and payload")


def check_memory_session_scope(
    *, authority_session_id: str | None, requested_session_id: str | None
) -> None:
    """Reject widening a memory session scope beyond its authenticated grant.

    ``authority_session_id`` is the scope granted by the authenticated
    request/session authority (``None`` = no session scope). A requested scope
    (from a Phase 3 context selection or any node-specific selection) is valid
    only if it is the exact granted scope or a stricter non-session subset
    (``None``). A caller / worker / model that requests any *other* session id —
    or requests a session when none was granted — is widening and is rejected.
    """
    if requested_session_id is None:
        return  # stricter (or equal) non-session subset is always allowed
    if authority_session_id is None:
        raise ValueError(
            "memory session widening rejected: no session scope was granted, "
            f"cannot request {requested_session_id!r}"
        )
    if requested_session_id != authority_session_id:
        raise ValueError(
            "memory session widening rejected: requested "
            f"{requested_session_id!r} is not the granted scope "
            f"{authority_session_id!r}"
        )


# --------------------------------------------------------------------------- #
# Frozen snapshots                                                            #
# --------------------------------------------------------------------------- #
class ContextSnapshot(DigestModel):
    """The frozen data + memory universe a Plan (and its workers) construct over.

    Embeds the exact :class:`DataContext` (so any source-registry / route /
    chain / config / snapshot-content / vintage change flows into this
    snapshot's semantic digest, and therefore into the candidate Plan digest),
    plus the memory binding as *typed* references. ``snapshot_id`` /
    ``memory_snapshot_id`` are audit dereference locators and ``built_at`` is the
    freeze wall-clock — all audit — while ``memory_snapshot_hash`` (the complete
    frozen visible-memory universe), ``past_context_hash`` (the
    reviewed/query-specific selection) and ``memory_session_id`` are semantic.

    ``memory_snapshot_ref`` / ``memory_selection_ref`` are required main-namespace
    :class:`TypedPayloadRef`s: each carries the exact registered SchemaRef of the
    persisted fact plus its namespace/content digest, so a Phase 3 non-empty
    schema replays without guessing a schema from a bare hash. Their content
    digests must equal ``memory_snapshot_hash`` / ``past_context_hash``. Each
    nested ``payload_ref.object_id`` is audit-only, so relocating byte-identical
    memory evidence cannot change ``content_digest``.

    ``runtime_requirements_ref`` is ``None`` **iff** the memory binding is the
    canonical empty pair produced by :func:`build_empty_memory_binding`; any other
    (non-empty / Phase 3) binding must supply a main typed ref to a
    ``ContextRuntimeRequirements@1`` fact whose ``context_subject_digest``
    cross-matches this snapshot (Phase 1 proves shape/digest equality only; Phase 2
    admission resolves and enforces the required authority). ``memory_session_id``
    comes only from an authenticated request/session authority (``None`` before the
    Phase 3 facade). Self-seals ``content_digest`` via :meth:`build`.
    """

    schema_version: Literal["1"] = "1"
    snapshot_id: NonEmptyStr
    data_context: DataContext
    memory_snapshot_id: NonEmptyStr
    memory_snapshot_hash: DigestHex
    past_context_hash: DigestHex
    memory_snapshot_ref: TypedPayloadRef
    memory_selection_ref: TypedPayloadRef
    runtime_requirements_ref: TypedPayloadRef | None = None
    memory_session_id: LogicalId | None = None
    built_at: UtcDateTime
    content_digest: DigestHex

    SEMANTIC_EXCLUDE: ClassVar[frozenset[str]] = frozenset(
        {"snapshot_id", "memory_snapshot_id", "built_at"}
    )
    SELF_DIGEST_FIELDS: ClassVar[frozenset[str]] = frozenset({"content_digest"})

    @model_validator(mode="after")
    def _verify(self) -> "ContextSnapshot":
        if self.memory_snapshot_ref.payload_ref.namespace != "main":
            raise ValueError("memory_snapshot_ref must reference a main-namespace payload")
        if self.memory_snapshot_ref.payload_ref.content_digest != self.memory_snapshot_hash:
            raise ValueError(
                "memory_snapshot_ref.payload_ref.content_digest must equal memory_snapshot_hash"
            )
        if self.memory_selection_ref.payload_ref.namespace != "main":
            raise ValueError("memory_selection_ref must reference a main-namespace payload")
        if self.memory_selection_ref.payload_ref.content_digest != self.past_context_hash:
            raise ValueError(
                "memory_selection_ref.payload_ref.content_digest must equal past_context_hash"
            )
        is_empty_pair = (
            self.memory_snapshot_hash == _CANONICAL_EMPTY_MEMORY_SNAPSHOT_HASH
            and self.past_context_hash == _CANONICAL_EMPTY_PAST_CONTEXT_HASH
        )
        if is_empty_pair:
            if self.runtime_requirements_ref is not None:
                raise ValueError(
                    "the canonical empty-memory pair must have runtime_requirements_ref=None"
                )
        else:
            if self.runtime_requirements_ref is None:
                raise ValueError(
                    "a non-empty memory binding requires a runtime_requirements_ref"
                )
        rr = self.runtime_requirements_ref
        if rr is not None:
            if rr.payload_ref.namespace != "main":
                raise ValueError(
                    "runtime_requirements_ref must reference a main-namespace payload"
                )
            if (
                rr.schema_ref.name != _RUNTIME_REQUIREMENTS_SCHEMA_NAME
                or rr.schema_ref.version != _RUNTIME_REQUIREMENTS_SCHEMA_VERSION
            ):
                raise ValueError(
                    "runtime_requirements_ref must resolve ContextRuntimeRequirements@1"
                )
        if self.content_digest != self.semantic_digest():
            raise ValueError("declared content_digest does not match canonical digest")
        return self

    @classmethod
    def build(cls, **fields: Any) -> "ContextSnapshot":
        """Seal a context snapshot: compute ``content_digest`` from the fields."""
        try:
            digest = cls.digest_of_fields(projection="semantic", **fields)
        except (ValueError, TypeError, AttributeError, KeyError):
            digest = _DIGEST_PLACEHOLDER
        return cls(**fields, content_digest=digest)


class ContextRuntimeRequirements(DigestModel):
    """The runtime authority a :class:`ContextSnapshot` binds — a generic strict fact.

    Binds a builder-computed ``context_subject_digest`` (the DataContext content
    plus the memory snapshot/selection typed semantic projections and the memory
    session scope — see :func:`compute_context_subject_digest`) to the exact
    ``required_schema_registry_digest`` / ``required_catalog_digest`` and the
    canonical required runtime material :class:`ContentRef`s, required capability
    :class:`CapabilityRef`s and required bridge ids the run must have available.
    It contains **no** provider object or path: it names *what* authority is
    required, not *how* to obtain it. ``requirements_digest`` self-seals the whole
    fact.

    Phase 1 proves shape/digest equality only (see
    :func:`verify_context_runtime_requirements`); a Phase 2 admission service
    resolves and enforces the required registry/catalog/material/capability/bridge
    authority before any budget reservation.
    """

    schema_version: Literal["1"] = "1"
    context_subject_digest: DigestHex
    required_schema_registry_digest: DigestHex
    required_catalog_digest: DigestHex
    required_runtime_material_refs: tuple[ContentRef, ...] = ()
    required_capability_refs: tuple[CapabilityRef, ...] = ()
    required_bridge_ids: tuple[LogicalId, ...] = ()
    requirements_digest: DigestHex

    SELF_DIGEST_FIELDS: ClassVar[frozenset[str]] = frozenset({"requirements_digest"})

    @model_validator(mode="after")
    def _verify(self) -> "ContextRuntimeRequirements":
        mat_keys = [
            (r.id, r.version, r.content_digest) for r in self.required_runtime_material_refs
        ]
        if mat_keys != sorted(mat_keys):
            raise ValueError(
                "required_runtime_material_refs must be canonically ordered by "
                "(id, version, content_digest)"
            )
        if len(set(mat_keys)) != len(mat_keys):
            raise ValueError("required_runtime_material_refs must be duplicate-free")
        cap_keys = [
            (r.id, r.version, r.content_digest) for r in self.required_capability_refs
        ]
        if cap_keys != sorted(cap_keys):
            raise ValueError(
                "required_capability_refs must be canonically ordered by "
                "(id, version, content_digest)"
            )
        if len(set(cap_keys)) != len(cap_keys):
            raise ValueError("required_capability_refs must be duplicate-free")
        bridge = list(self.required_bridge_ids)
        if bridge != sorted(bridge):
            raise ValueError("required_bridge_ids must be canonically sorted")
        if len(set(bridge)) != len(bridge):
            raise ValueError("required_bridge_ids must be duplicate-free")
        if self.requirements_digest != self.semantic_digest():
            raise ValueError("declared requirements_digest does not match canonical digest")
        return self

    @classmethod
    def build(cls, **fields: Any) -> "ContextRuntimeRequirements":
        """Seal a requirements fact: compute ``requirements_digest`` from the fields."""
        try:
            digest = cls.digest_of_fields(projection="semantic", **fields)
        except (ValueError, TypeError, AttributeError, KeyError):
            digest = _DIGEST_PLACEHOLDER
        return cls(**fields, requirements_digest=digest)


def compute_context_subject_digest(
    *,
    data_context: DataContext,
    memory_snapshot_ref: TypedPayloadRef,
    memory_selection_ref: TypedPayloadRef,
    memory_session_id: str | None,
) -> DigestHex:
    """The canonical *context subject* digest a :class:`ContextRuntimeRequirements` binds.

    Combines the :class:`DataContext` **content** (its semantic projection) with
    the memory snapshot/selection **typed** semantic projections (exact SchemaRef
    + namespace/content, never the audit object id) and the memory session scope.
    Pure and deterministic so a Phase 2 admission service can recompute it from a
    resolved :class:`ContextSnapshot` and cross-match a resolved requirements fact.
    """
    return content_digest(
        {
            "data_context": data_context,
            "memory_snapshot_ref": memory_snapshot_ref,
            "memory_selection_ref": memory_selection_ref,
            "memory_session_id": memory_session_id,
        }
    )


def verify_context_runtime_requirements(
    snapshot: ContextSnapshot, requirements: ContextRuntimeRequirements
) -> None:
    """Pure Phase 1 shape/digest cross-check binding ``requirements`` to ``snapshot``.

    Proves, and *only* proves: the snapshot carries a ``runtime_requirements_ref``
    that resolves ``ContextRuntimeRequirements@1`` and whose referenced content
    digest equals ``requirements.requirements_digest``; and the recomputed context
    subject digest equals ``requirements.context_subject_digest``. It does **not**
    resolve the required registry/catalog/material/capability/bridge authority —
    that is Phase 2 admission's duty (docline 842). Raises :class:`ValueError` on
    any mismatch.
    """
    ref = snapshot.runtime_requirements_ref
    if ref is None:
        raise ValueError(
            "snapshot carries no runtime_requirements_ref (canonical empty-memory "
            "pair); there is nothing to verify"
        )
    if (
        ref.schema_ref.name != _RUNTIME_REQUIREMENTS_SCHEMA_NAME
        or ref.schema_ref.version != _RUNTIME_REQUIREMENTS_SCHEMA_VERSION
    ):
        raise ValueError("runtime_requirements_ref must resolve ContextRuntimeRequirements@1")
    if ref.payload_ref.content_digest != requirements.requirements_digest:
        raise ValueError(
            "runtime_requirements_ref content digest must equal the requirements digest"
        )
    expected = compute_context_subject_digest(
        data_context=snapshot.data_context,
        memory_snapshot_ref=snapshot.memory_snapshot_ref,
        memory_selection_ref=snapshot.memory_selection_ref,
        memory_session_id=snapshot.memory_session_id,
    )
    if requirements.context_subject_digest != expected:
        raise ValueError(
            "requirements context_subject_digest does not cross-match the ContextSnapshot"
        )


class InputArtifactBinding(DigestModel):
    """One Worker input's satisfied artifact binding on a per-node input snapshot.

    ``input_name`` names the exact Worker input; ``cardinality`` is its declared
    ``one`` | ``many`` shape; ``artifact_refs`` are the full :class:`ArtifactRef`s
    that satisfy it (producer/node/output/slot/schema/content identities intact —
    no anonymous payload id). ``many`` preserves the frozen Plan dependency
    declaration order. A ``one`` binding carries at most one ref at the model
    level; the *exactly one* bound is enforced by :class:`InputSnapshot` when
    ``readiness == "ready"`` (a ``terminal_partial`` snapshot may leave a ``one``
    binding empty). Structural only in Phase 1: equality with the admitted
    Plan/catalog bindings is a Phase 2 admission cross-check.
    """

    schema_version: Literal["1"] = "1"
    input_name: NonEmptyStr
    cardinality: Literal["one", "many"]
    artifact_refs: tuple[ArtifactRef, ...] = ()

    @model_validator(mode="after")
    def _verify(self) -> "InputArtifactBinding":
        if self.cardinality == "one" and len(self.artifact_refs) > 1:
            raise ValueError(
                f"a 'one' cardinality binding {self.input_name!r} cannot carry more "
                "than one artifact ref"
            )
        return self


class InputSnapshot(DigestModel):
    """The frozen, per-node read-set one node receives before it executes.

    Identity / correlation. ``snapshot_id`` / ``run_id`` / ``plan_id`` and
    ``attempt`` are audit/correlation identity (cross-checked by builders, excluded
    from the semantic projection); ``plan_digest``, ``node_id`` and the
    non-negative ``layer_index`` are semantic.

    Bindings. ``context_snapshot_ref`` is the exact main :class:`TypedPayloadRef`
    resolving ``ContextSnapshot@1`` this input derives from. ``artifact_inputs`` is
    the ordered tuple of :class:`InputArtifactBinding`s in the selected WorkerSpec
    input declaration order (order is semantic). ``data_result_refs`` is the
    canonical, duplicate-free, main-only typed DataResult tuple (ordered by the
    typed semantic projection; a relocated object is one semantic ref, and
    during-node results never mutate this pre-node tuple). ``memory_record_refs``
    is canonically ordered by ``(record_id, revision_id, content_digest)``,
    duplicate-free, with no ``(record_id, revision_id)`` appearing twice (a shared
    pair with different availability/content is a *conflict*, not two records).

    Readiness. ``ready`` requires no missing input and every ``one`` binding to
    carry exactly one ref. ``terminal_partial`` is non-executable (a gateway must
    reject it), records the exact unsatisfied ``missing_input_names`` (canonically
    sorted, duplicate-free) and only the bindings available at the terminal
    boundary — it exists so a BLOCKED/SKIPPED/early terminal NodeRun can still bind
    a real immutable snapshot. Self-seals ``content_digest`` via :meth:`build`.
    """

    schema_version: Literal["1"] = "1"
    snapshot_id: NonEmptyStr
    run_id: NonEmptyStr
    plan_id: NonEmptyStr
    plan_digest: DigestHex
    node_id: NonEmptyStr
    layer_index: NonNegativeInt
    attempt: PositiveInt
    context_snapshot_ref: TypedPayloadRef
    artifact_inputs: tuple[InputArtifactBinding, ...] = ()
    data_result_refs: tuple[TypedPayloadRef, ...] = ()
    memory_record_refs: tuple[MemoryRecordRef, ...] = ()
    readiness: Literal["ready", "terminal_partial"]
    missing_input_names: tuple[NonEmptyStr, ...] = ()
    built_at: UtcDateTime
    content_digest: DigestHex

    SEMANTIC_EXCLUDE: ClassVar[frozenset[str]] = frozenset(
        {"snapshot_id", "run_id", "plan_id", "attempt", "built_at"}
    )
    SELF_DIGEST_FIELDS: ClassVar[frozenset[str]] = frozenset({"content_digest"})

    @model_validator(mode="after")
    def _verify(self) -> "InputSnapshot":
        if self.context_snapshot_ref.payload_ref.namespace != "main":
            raise ValueError("context_snapshot_ref must reference a main-namespace payload")
        if (
            self.context_snapshot_ref.schema_ref.name != "ContextSnapshot"
            or self.context_snapshot_ref.schema_ref.version != "1"
        ):
            raise ValueError("context_snapshot_ref must resolve ContextSnapshot@1")

        validate_typed_ref_tuple(
            self.data_result_refs, require_main=True, field_name="data_result_refs"
        )

        input_names = [b.input_name for b in self.artifact_inputs]
        if len(set(input_names)) != len(input_names):
            raise ValueError("artifact_inputs must not repeat an input_name")

        refs = self.memory_record_refs
        keys = [(r.record_id, r.revision_id, r.content_digest) for r in refs]
        if keys != sorted(keys):
            raise ValueError(
                "memory_record_refs must be canonically ordered by "
                "(record_id, revision_id, content_digest)"
            )
        seen: dict[tuple[str, str], MemoryRecordRef] = {}
        for r in refs:
            pair = (r.record_id, r.revision_id)
            prev = seen.get(pair)
            if prev is not None:
                if (prev.content_digest == r.content_digest
                        and prev.available_at == r.available_at):
                    raise ValueError(
                        f"duplicate memory record ref for {pair!r}"
                    )
                raise ValueError(
                    f"conflicting memory record refs for {pair!r}: same "
                    "(record_id, revision_id) with different availability/content"
                )
            seen[pair] = r

        missing = list(self.missing_input_names)
        if missing != sorted(missing):
            raise ValueError("missing_input_names must be canonically sorted")
        if len(set(missing)) != len(missing):
            raise ValueError("missing_input_names must be duplicate-free")

        if self.readiness == "ready":
            if self.missing_input_names:
                raise ValueError(
                    "readiness='ready' requires an empty missing_input_names tuple"
                )
            for binding in self.artifact_inputs:
                if binding.cardinality == "one" and len(binding.artifact_refs) != 1:
                    raise ValueError(
                        f"readiness='ready' requires the 'one' binding "
                        f"{binding.input_name!r} to carry exactly one artifact ref"
                    )
        else:  # terminal_partial
            if not self.missing_input_names:
                raise ValueError(
                    "readiness='terminal_partial' requires at least one missing input name"
                )

        if self.content_digest != self.semantic_digest():
            raise ValueError("declared content_digest does not match canonical digest")
        return self

    @classmethod
    def build(cls, **fields: Any) -> "InputSnapshot":
        """Seal an input snapshot: compute ``content_digest`` from the fields."""
        try:
            digest = cls.digest_of_fields(projection="semantic", **fields)
        except (ValueError, TypeError, AttributeError, KeyError):
            digest = _DIGEST_PLACEHOLDER
        return cls(**fields, content_digest=digest)


# --------------------------------------------------------------------------- #
# Budget — maxima value + reservation value (no ledger behavior)              #
# --------------------------------------------------------------------------- #
class RunBudget(DigestModel):
    """A run's single budget: maxima plus immutable running reserved totals.

    Covers every scope of one run (bootstrap / planner / main / repair / retry)
    under one ``ledger_id``. Maxima and reserved totals are strict non-negative
    ints (``bool`` rejected); ``max_concurrency`` is a ``PositiveInt`` (a run
    cannot admit zero concurrent slots). The reviewed cap invariant: running
    reserved totals may not exceed their maxima. No ledger / mutation behavior is
    implemented — this is an immutable snapshot value.
    """

    schema_version: Literal["1"] = "1"
    ledger_id: NonEmptyStr
    max_tokens: NonNegativeInt
    max_llm_invocations: NonNegativeInt
    max_concurrency: PositiveInt
    reserved_tokens: NonNegativeInt = 0
    reserved_llm_invocations: NonNegativeInt = 0
    reserved_concurrency: NonNegativeInt = 0

    @model_validator(mode="after")
    def _within_maxima(self) -> "RunBudget":
        if self.reserved_tokens > self.max_tokens:
            raise ValueError("reserved_tokens cannot exceed max_tokens")
        if self.reserved_llm_invocations > self.max_llm_invocations:
            raise ValueError("reserved_llm_invocations cannot exceed max_llm_invocations")
        if self.reserved_concurrency > self.max_concurrency:
            raise ValueError("reserved_concurrency cannot exceed max_concurrency")
        return self


class BudgetReservation(DigestModel):
    """One atomic allocation drawn from a run's budget.

    Binds ``request_id`` + ``candidate_plan_digest`` + ledger identity
    (``ledger_id`` and the ``scope_type`` / ``scope_id`` it was drawn for) and the
    *exact* requested token / invocation / concurrency amounts — all semantic.
    ``reserved_concurrency`` is a ``PositiveInt`` (a reservation for zero slots is
    meaningless). Actual use may not exceed the reservation (Phase 1). The random
    ``reservation_id`` and the ``reserved_at`` / ``settled_at`` wall-clock are
    audit-only. Status matrix: ``reserved`` carries no settled time;
    ``settled`` / ``released`` require one. No ledger mutation behavior is
    implemented — settling / releasing produces a *new* value elsewhere.
    """

    schema_version: Literal["1"] = "1"
    reservation_id: NonEmptyStr
    ledger_id: NonEmptyStr
    run_id: NonEmptyStr
    request_id: NonEmptyStr
    candidate_plan_digest: DigestHex
    scope_type: BudgetScopeType
    scope_id: NonEmptyStr

    reserved_tokens: NonNegativeInt
    reserved_llm_invocations: NonNegativeInt
    reserved_concurrency: PositiveInt
    actual_tokens: NonNegativeInt = 0
    actual_llm_invocations: NonNegativeInt = 0
    actual_concurrency: NonNegativeInt = 0

    status: ReservationStatus
    reserved_at: UtcDateTime
    settled_at: UtcDateTime | None = None

    SEMANTIC_EXCLUDE: ClassVar[frozenset[str]] = frozenset(
        {"reservation_id", "reserved_at", "settled_at"}
    )

    @model_validator(mode="after")
    def _coherent(self) -> "BudgetReservation":
        if self.actual_tokens > self.reserved_tokens:
            raise ValueError("actual_tokens cannot exceed reserved_tokens")
        if self.actual_llm_invocations > self.reserved_llm_invocations:
            raise ValueError(
                "actual_llm_invocations cannot exceed reserved_llm_invocations"
            )
        if self.actual_concurrency > self.reserved_concurrency:
            raise ValueError("actual_concurrency cannot exceed reserved_concurrency")
        if self.status == "reserved":
            if self.settled_at is not None:
                raise ValueError("status='reserved' must not carry a settled_at time")
        else:  # settled / released
            if self.settled_at is None:
                raise ValueError(f"status={self.status!r} requires a settled_at time")
        return self


# --------------------------------------------------------------------------- #
# Run context                                                                 #
# --------------------------------------------------------------------------- #
class RunContext(DigestModel):
    """The frozen run-level binding of data, memory, budget and identity.

    Embeds the immutable :class:`DataContext` (which carries the clock/as-of) and
    the :class:`RunBudget`, binds the frozen memory universe via
    ``memory_snapshot_hash`` (semantic) and references the committed
    :class:`ContextSnapshot` by ``context_snapshot_id`` (``None`` before bootstrap
    commits; a main-plan RunContext references one). ``context_snapshot_id`` and
    ``cancellation_token_id`` are audit dereference locators and ``created_at`` is
    the builder wall-clock; ``replays_run_id`` (when re-running a prior run) is
    semantic content.
    """

    schema_version: Literal["1"] = "1"
    run_id: NonEmptyStr
    data: DataContext
    context_snapshot_id: NonEmptyStr | None = None
    memory_snapshot_hash: DigestHex
    budget: RunBudget
    cancellation_token_id: NonEmptyStr
    replays_run_id: NonEmptyStr | None = None
    created_at: UtcDateTime | None = None

    SEMANTIC_EXCLUDE: ClassVar[frozenset[str]] = frozenset(
        {"context_snapshot_id", "cancellation_token_id", "created_at"}
    )
