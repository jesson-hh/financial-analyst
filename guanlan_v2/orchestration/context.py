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
)
from guanlan_v2.orchestration.enums import DataBackend, DataMode
from guanlan_v2.orchestration.refs import LogicalId, PayloadRef

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


class EmptyMemoryBinding(NamedTuple):
    """The output of :func:`build_empty_memory_binding` — models + canonical hashes.

    A no-memory runtime persists ``snapshot`` and ``selection`` in the ``main``
    namespace and uses ``snapshot_hash`` / ``past_context_hash`` as a
    :class:`ContextSnapshot`'s ``memory_snapshot_hash`` / ``past_context_hash``.
    The hashes are canonical content digests — never random placeholders.
    """

    snapshot: EmptyMemorySnapshot
    selection: EmptyMemorySelection
    snapshot_hash: DigestHex
    past_context_hash: DigestHex


def build_empty_memory_binding() -> EmptyMemoryBinding:
    """Deterministically build the two canonical empty-memory models + digests.

    This is the *sole* pure builder for the pre-Phase-3 no-memory universe. It
    seals the empty snapshot, then the empty selection (which binds the snapshot
    digest), and returns both with their canonical content digests for a runtime
    to persist. Blank / random locator-hash pairs are never produced.
    """
    snap_digest = EmptyMemorySnapshot.digest_of_fields(projection="semantic", records=())
    snapshot = EmptyMemorySnapshot(records=(), content_digest=snap_digest)
    sel_digest = EmptyMemorySelection.digest_of_fields(
        projection="semantic", records=(), snapshot_digest=snapshot.content_digest
    )
    selection = EmptyMemorySelection(
        records=(), snapshot_digest=snapshot.content_digest, content_digest=sel_digest
    )
    return EmptyMemoryBinding(
        snapshot=snapshot,
        selection=selection,
        snapshot_hash=snapshot.content_digest,
        past_context_hash=selection.content_digest,
    )


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
    plus the memory binding. ``snapshot_id`` / ``memory_snapshot_id`` are audit
    dereference locators and ``built_at`` is the freeze wall-clock — all audit —
    while ``memory_snapshot_hash`` (the complete frozen visible-memory universe),
    ``past_context_hash`` (the reviewed/query-specific selection) and
    ``memory_session_id`` are semantic. ``memory_selection_ref`` must live in the
    ``main`` namespace with ``content_digest == past_context_hash``;
    ``memory_session_id`` comes only from an authenticated request/session
    authority (``None`` before the Phase 3 facade). Self-seals ``content_digest``
    via :meth:`build`; relocating identical memory evidence (a new
    ``memory_snapshot_id`` or ``memory_selection_ref.object_id``) cannot change it.
    """

    schema_version: Literal["1"] = "1"
    snapshot_id: NonEmptyStr
    data_context: DataContext
    memory_snapshot_id: NonEmptyStr
    memory_snapshot_hash: DigestHex
    past_context_hash: DigestHex
    memory_selection_ref: PayloadRef
    memory_session_id: LogicalId | None = None
    built_at: UtcDateTime
    content_digest: DigestHex

    SEMANTIC_EXCLUDE: ClassVar[frozenset[str]] = frozenset(
        {"snapshot_id", "memory_snapshot_id", "built_at"}
    )
    SELF_DIGEST_FIELDS: ClassVar[frozenset[str]] = frozenset({"content_digest"})

    @model_validator(mode="after")
    def _verify(self) -> "ContextSnapshot":
        if self.memory_selection_ref.namespace != "main":
            raise ValueError("memory_selection_ref must use namespace='main'")
        if self.memory_selection_ref.content_digest != self.past_context_hash:
            raise ValueError(
                "memory_selection_ref.content_digest must equal past_context_hash"
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


class InputSnapshot(DigestModel):
    """The frozen, per-layer read-set one node receives.

    ``memory_record_refs`` is an immutable tuple canonically ordered by
    ``(record_id, revision_id, content_digest)``, duplicate-free, with no
    ``(record_id, revision_id)`` appearing twice — two refs sharing a
    ``(record_id, revision_id)`` but differing in availability/content are a
    *conflict*, not two records. The full refs (including ``available_at``) enter
    the semantic projection, so a node can never be handed a later/live record by
    mutating an already-frozen snapshot. ``snapshot_id`` and ``built_at`` are
    audit; ``context_snapshot_hash`` binds the :class:`ContextSnapshot` this input
    derives from. Self-seals ``content_digest`` via :meth:`build`.
    """

    schema_version: Literal["1"] = "1"
    snapshot_id: NonEmptyStr
    context_snapshot_hash: DigestHex
    memory_record_refs: tuple[MemoryRecordRef, ...] = ()
    built_at: UtcDateTime
    content_digest: DigestHex

    SEMANTIC_EXCLUDE: ClassVar[frozenset[str]] = frozenset({"snapshot_id", "built_at"})
    SELF_DIGEST_FIELDS: ClassVar[frozenset[str]] = frozenset({"content_digest"})

    @model_validator(mode="after")
    def _verify(self) -> "InputSnapshot":
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
