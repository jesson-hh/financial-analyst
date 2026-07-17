# -*- coding: utf-8 -*-
"""Phase 3 · Task 9 — strict public memory contracts + pure builders.

Every **registered** memory payload/fact schema lives in this one module (the
Phase-1 contract-completeness firewall discovers ``ContractModel`` subclasses
per defining module, so keeping the registered surface here keeps the reviewed
classification surface single). Internal carriers/ports use the non-``ContractModel``
:class:`_MemoryInternalModel` base (or plain classes) so they can never leak into
a schema registry or golden.

Phase 1 remains the sole owner of ``MemoryRecordRef`` / ``ContextSnapshot`` /
``ContextRuntimeRequirements`` / ``InputSnapshot``; this module imports that ABI
and never redefines it. PIT rule (Global Constraint line 24): records carry a
tz-aware ``available_at``; visibility filtering happens **before** any
ranking/top-k/fallback (see ``store.select_memory``); missing/naive availability
is a loud refusal, never a silent filter.
"""
from __future__ import annotations

import hashlib
import re
from datetime import datetime
from typing import Annotated, Any, ClassVar, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, model_validator

from guanlan_v2.orchestration.catalog import WorkerSpec
from guanlan_v2.orchestration.context import ContextSnapshot, MemoryRecordRef
from guanlan_v2.orchestration.digest import (
    DigestHex,
    DigestModel,
    FiniteFloat,
    NonEmptyStr,
    NonNegativeInt,
    PositiveInt,
    UtcDateTime,
    canonical_json,
    content_digest,
)
from guanlan_v2.orchestration.refs import (
    CapabilityRef,
    ContentRef,
    LogicalId,
    TypedPayloadRef,
)

__all__ = [
    # errors
    "MemoryContractError",
    "MemoryAuthorityError",
    "MemoryConflictError",
    "MemoryPitError",
    # literals / constants
    "MemoryKind",
    "MemoryReviewState",
    "MemoryScopeKind",
    "MemoryReviewBasis",
    "StoreDigestPrecondition",
    "MEMORY_BRIDGE_ID",
    "MEMORY_APPLY_MARKER_DOMAIN",
    "PHASE3_MEMORY_STATE_CELL_NAMESPACES",
    # policy
    "MemorySourcePolicyEntry",
    "MemoryBorrowGrant",
    "MemoryCapturePolicy",
    # cutover
    "MemoryCutoverSourcePayload",
    "MemoryCutoverManifestEntry",
    "MemoryCutoverManifest",
    "MemoryCutoverAttestation",
    "MemoryCutoverPreparationEntry",
    "MemoryCutoverPreparation",
    "derive_source_revision_token",
    "derive_cutover_put_idempotency_key",
    # source state
    "MemorySourceStateEntry",
    "MemorySourceStateSnapshot",
    # record / snapshot
    "MemoryRecord",
    "MemorySnapshotEntry",
    "MemorySnapshot",
    "MemorySnapshotCapturePreparation",
    "derive_memory_record_id",
    "derive_memory_revision_id",
    "build_memory_record",
    # review evidence
    "LegacyCutoverEvidence",
    "AgentAppliedEvidence",
    "ConsoleAppliedEvidence",
    "MemoryReviewEvidence",
    "MemoryOwnerApplySemanticReceipt",
    # query / selection
    "MemoryQuery",
    "MemorySelectionEntry",
    "MemorySelection",
    "build_context_memory_query",
    "build_worker_memory_query",
    # proposals
    "AgentMemoryProposalTarget",
    "ConsoleMemoryProposalTarget",
    "MemoryProposalTarget",
    "MemoryProposalRequest",
    "MemoryProposalGrant",
    "MemoryProposalPreparation",
    "MemoryProposal",
    "AgentPendingLocator",
    "ConsolePendingEventLocator",
    "MemoryProposalReceipt",
    "MemoryProposalDecision",
    "build_apply_marker_id",
    "build_memory_proposal",
    "normalize_console_memory_write",
    # facade / bridge / render
    "MemoryQueryProjection",
    "MemoryBridgePrefetchBinding",
    "MemoryFacadeDescriptor",
    "RenderedMemoryBlock",
    # internal carriers
    "MemoryContextAuthority",
    "AuthenticatedAdminPrincipal",
    "ResolvedMemoryPolicy",
    "MemoryReplayBinding",
    "MemoryReadOutcome",
    "PHASE3_MEMORY_INTERNAL_MODELS",
    "MEMORY_PUBLIC_MODELS",
]

_DIGEST_PLACEHOLDER = "0" * 64


# --------------------------------------------------------------------------- #
# Errors                                                                       #
# --------------------------------------------------------------------------- #
class MemoryContractError(ValueError):
    """A memory contract / builder invariant was violated.

    Subclasses :class:`ValueError` so a violation raised inside a pydantic
    validator surfaces uniformly as a ``ValidationError`` while direct builder
    calls still raise the specific memory error type.
    """


class MemoryAuthorityError(MemoryContractError):
    """A caller attempted to widen role/scope/kind/session/policy authority."""


class MemoryConflictError(MemoryContractError):
    """Same-key semantic drift or a competing exactly-once operation."""


class MemoryPitError(MemoryContractError):
    """A memory PIT invariant failed loud (naive/missing/future availability)."""


# --------------------------------------------------------------------------- #
# Closed literals + reviewed constants                                        #
# --------------------------------------------------------------------------- #
MemoryKind = Literal["semantic", "episodic", "procedural"]
MemoryReviewState = Literal["pending", "approved"]
#: closed storage scopes over the two real stores. Borrowing is a *read grant*
#: over another owner's ``agent_own`` scope, never a storage scope.
MemoryScopeKind = Literal["agent_shared", "agent_own", "console_global", "console_session"]
MemoryReviewBasis = Literal["legacy_cutover", "memory_ops_approval", "console_proposal_approval"]
#: a store CAS precondition: the exact complete-unit digest, or provably absent.
StoreDigestPrecondition = Union[Literal["absent"], DigestHex]

MEMORY_BRIDGE_ID = "memory.runtime"
MEMORY_APPLY_MARKER_DOMAIN = "guanlan.memory.apply-marker.v1"

#: the one canonical, lexicographically ordered startup namespace union. The
#: service constructs/seals the Phase-2 state-cell store once with exactly this
#: union before any repository is created (see ``store.MemoryStateCellOwnerMap``).
PHASE3_MEMORY_STATE_CELL_NAMESPACES: tuple[str, ...] = (
    "memory.cutover_preparation.v1",
    "memory.proposal_preparation.v1",
    "memory.snapshot_head.v1",
    "memory.snapshot_operation.v1",
    "memory.snapshot_preparation.v1",
    "memory.source_head.v1",
    "memory.source_operation.v1",
)

_RECORD_ID_RE = re.compile(r"^mem\.[0-9a-f]{64}$")
_MARKER_ID_RE = re.compile(r"^apply\.[0-9a-f]{64}$")


def _require_sorted_unique(values: tuple, label: str) -> None:
    vals = list(values)
    if vals != sorted(vals):
        raise ValueError(f"{label} must be canonically sorted")
    if len(set(vals)) != len(vals):
        raise ValueError(f"{label} must be duplicate-free")


def _sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------- #
# Internal-model base (never registered, invisible to the Phase-1 firewall)    #
# --------------------------------------------------------------------------- #
class _MemoryInternalModel(BaseModel):
    """Strict frozen base for internal carriers — deliberately NOT a ContractModel."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


# --------------------------------------------------------------------------- #
# MemoryCapturePolicy@1                                                        #
# --------------------------------------------------------------------------- #
class MemorySourcePolicyEntry(DigestModel):
    """One reviewed logical source → kind/scope/importance/mandatory mapping."""

    schema_version: Literal["1"] = "1"
    source_id: LogicalId
    kind: MemoryKind
    scope: MemoryScopeKind
    importance: FiniteFloat
    mandatory: bool = False


class MemoryBorrowGrant(DigestModel):
    """One borrower → borrowed-owner read grant (never a write/proposal grant)."""

    schema_version: Literal["1"] = "1"
    borrower_id: LogicalId
    owner_id: LogicalId

    @model_validator(mode="after")
    def _verify(self) -> "MemoryBorrowGrant":
        if self.borrower_id == self.owner_id:
            raise ValueError("a borrow grant cannot name the borrower as owner (own scope is implicit)")
        return self


class MemoryCapturePolicy(DigestModel):
    """The one combined reviewed capture/visibility/ranking policy (catalog material).

    A structurally valid caller policy has no authority; runtime accepts only the
    exact catalog-bound policy material (see :class:`ResolvedMemoryPolicy`).
    Mandatory records do not consume ``top_k_non_mandatory``; exceeding any
    mandatory/count/byte bound fails closed rather than truncating.
    """

    schema_version: Literal["1"] = "1"
    policy_id: LogicalId
    policy_version: NonEmptyStr
    source_mappings: tuple[MemorySourcePolicyEntry, ...]
    require_global_cutover: Literal[True] = True
    #: the reviewed console archive-tail window (existing behavior reads the
    #: archive's trailing characters; the policy freezes that size).
    archive_tail_chars: NonNegativeInt
    context_scopes: tuple[MemoryScopeKind, ...]
    worker_scopes: tuple[MemoryScopeKind, ...]
    borrow_grants: tuple[MemoryBorrowGrant, ...] = ()
    allowed_kinds: tuple[MemoryKind, ...]
    session_grant: Literal["same_session_only"] = "same_session_only"
    top_k_non_mandatory: PositiveInt
    max_mandatory: NonNegativeInt
    max_record_bytes: PositiveInt
    max_total_rendered_bytes: PositiveInt
    rank_policy: Literal["role_recency_importance_relevance_v1"] = (
        "role_recency_importance_relevance_v1"
    )
    tie_break: Literal["record_id_revision_id"] = "record_id_revision_id"
    policy_digest: DigestHex

    SELF_DIGEST_FIELDS: ClassVar[frozenset[str]] = frozenset({"policy_digest"})

    @model_validator(mode="after")
    def _verify(self) -> "MemoryCapturePolicy":
        src_ids = tuple(e.source_id for e in self.source_mappings)
        _require_sorted_unique(src_ids, "source_mappings (by source_id)")
        if not self.source_mappings:
            raise ValueError("source_mappings must be non-empty")
        _require_sorted_unique(self.context_scopes, "context_scopes")
        _require_sorted_unique(self.worker_scopes, "worker_scopes")
        _require_sorted_unique(self.allowed_kinds, "allowed_kinds")
        grant_keys = tuple((g.borrower_id, g.owner_id) for g in self.borrow_grants)
        _require_sorted_unique(grant_keys, "borrow_grants (by borrower, owner)")
        if self.policy_digest != self.semantic_digest():
            raise ValueError("declared policy_digest does not match canonical digest")
        return self

    @classmethod
    def build(cls, **fields: Any) -> "MemoryCapturePolicy":
        try:
            digest = cls.digest_of_fields(projection="semantic", **fields)
        except (ValueError, TypeError, AttributeError, KeyError):
            digest = _DIGEST_PLACEHOLDER
        return cls(**fields, policy_digest=digest)

    def mapping_for(self, source_id: str) -> MemorySourcePolicyEntry:
        for entry in self.source_mappings:
            if entry.source_id == source_id:
                return entry
        raise MemoryContractError(f"no reviewed policy mapping for source {source_id!r}")


# --------------------------------------------------------------------------- #
# Cutover — source payloads / manifest / attestation / preparation             #
# --------------------------------------------------------------------------- #
def derive_source_revision_token(
    *, cutover_operation_id: str, source_id: str, locator: str, store_content_digest: str
) -> DigestHex:
    """The deterministic baseline source revision token (closed derivation)."""
    return _sha256_hex(
        canonical_json(
            [
                "guanlan.memory.cutover-source-revision.v1",
                cutover_operation_id,
                source_id,
                locator,
                store_content_digest,
            ]
        )
    )


def derive_cutover_put_idempotency_key(
    *, cutover_operation_id: str, source_id: str, locator: str, store_content_digest: str
) -> str:
    """The PayloadStore idempotency key of one cutover source payload (same tuple)."""
    return "memcut:" + derive_source_revision_token(
        cutover_operation_id=cutover_operation_id,
        source_id=source_id,
        locator=locator,
        store_content_digest=store_content_digest,
    )


class MemoryCutoverSourcePayload(DigestModel):
    """One frozen normalized baseline source body (registered ``main`` payload)."""

    schema_version: Literal["1"] = "1"
    cutover_operation_id: LogicalId
    source_id: LogicalId
    locator: NonEmptyStr
    normalized_text: str
    store_content_digest: DigestHex
    source_revision_token: DigestHex

    @model_validator(mode="after")
    def _verify(self) -> "MemoryCutoverSourcePayload":
        expected = derive_source_revision_token(
            cutover_operation_id=self.cutover_operation_id,
            source_id=self.source_id,
            locator=self.locator,
            store_content_digest=self.store_content_digest,
        )
        if self.source_revision_token != expected:
            raise ValueError(
                "source_revision_token is not the closed derivation of "
                "(cutover_operation_id, source identity, store digest)"
            )
        return self


class MemoryCutoverManifestEntry(DigestModel):
    """One manifest row: the exact main source-payload ref + its store digest."""

    schema_version: Literal["1"] = "1"
    source_id: LogicalId
    locator: NonEmptyStr
    store_content_digest: DigestHex
    payload_ref: TypedPayloadRef

    @model_validator(mode="after")
    def _verify(self) -> "MemoryCutoverManifestEntry":
        if self.payload_ref.payload_ref.namespace != "main":
            raise ValueError("cutover manifest entry payload_ref must be main-namespace")
        if (self.payload_ref.schema_ref.name != "MemoryCutoverSourcePayload"
                or self.payload_ref.schema_ref.version != "1"):
            raise ValueError("cutover manifest entry must resolve MemoryCutoverSourcePayload@1")
        return self


class MemoryCutoverManifest(DigestModel):
    """The one global cutover manifest — deliberately contains NO final record
    revision/content digest, review-evidence digest, availability time or
    attestation digest (those are downstream facts)."""

    schema_version: Literal["1"] = "1"
    cutover_operation_id: LogicalId
    entries: tuple[MemoryCutoverManifestEntry, ...]
    content_digest: DigestHex

    SELF_DIGEST_FIELDS: ClassVar[frozenset[str]] = frozenset({"content_digest"})

    @model_validator(mode="after")
    def _verify(self) -> "MemoryCutoverManifest":
        keys = tuple((e.source_id, e.locator) for e in self.entries)
        _require_sorted_unique(keys, "cutover manifest entries (by source_id, locator)")
        if self.content_digest != self.semantic_digest():
            raise ValueError("declared content_digest does not match canonical digest")
        return self

    @classmethod
    def build(cls, **fields: Any) -> "MemoryCutoverManifest":
        try:
            digest = cls.digest_of_fields(projection="semantic", **fields)
        except (ValueError, TypeError, AttributeError, KeyError):
            digest = _DIGEST_PLACEHOLDER
        return cls(**fields, content_digest=digest)


class MemoryCutoverAttestation(DigestModel):
    """The authenticated one-time cutover attestation.

    ``attested_at`` is the authoritative cutover time — it *is* the baseline
    availability instant, hence semantic (never wall-clock audit noise).
    """

    schema_version: Literal["1"] = "1"
    manifest_ref: TypedPayloadRef
    cutover_policy_digest: DigestHex
    root_state_digest: DigestHex
    admin_actor: NonEmptyStr
    attested_at: UtcDateTime

    @model_validator(mode="after")
    def _verify(self) -> "MemoryCutoverAttestation":
        if self.manifest_ref.payload_ref.namespace != "main":
            raise ValueError("attestation manifest_ref must be main-namespace")
        if (self.manifest_ref.schema_ref.name != "MemoryCutoverManifest"
                or self.manifest_ref.schema_ref.version != "1"):
            raise ValueError("attestation manifest_ref must resolve MemoryCutoverManifest@1")
        return self


class MemoryCutoverPreparationEntry(DigestModel):
    """One normalized logical source row frozen by the cutover preparation."""

    schema_version: Literal["1"] = "1"
    source_id: LogicalId
    locator: NonEmptyStr
    store_content_digest: DigestHex
    source_revision_token: DigestHex
    put_idempotency_key: NonEmptyStr


class MemoryCutoverPreparation(DigestModel):
    """Registered ``main`` recovery-control payload — NOT accepted memory.

    Binds the authenticated admin-operation key, one service-derived operation ID
    and the complete canonically ordered source tuple / store digests / revision
    tokens / put-idempotency map.
    """

    schema_version: Literal["1"] = "1"
    admin_operation_key: NonEmptyStr
    cutover_operation_id: LogicalId
    entries: tuple[MemoryCutoverPreparationEntry, ...]

    @model_validator(mode="after")
    def _verify(self) -> "MemoryCutoverPreparation":
        keys = tuple((e.source_id, e.locator) for e in self.entries)
        _require_sorted_unique(keys, "cutover preparation entries (by source_id, locator)")
        for e in self.entries:
            expected = derive_source_revision_token(
                cutover_operation_id=self.cutover_operation_id,
                source_id=e.source_id, locator=e.locator,
                store_content_digest=e.store_content_digest,
            )
            if e.source_revision_token != expected:
                raise ValueError(
                    f"preparation entry ({e.source_id},{e.locator}) revision token drifted"
                )
            if e.put_idempotency_key != "memcut:" + expected:
                raise ValueError(
                    f"preparation entry ({e.source_id},{e.locator}) put key drifted"
                )
        return self


# --------------------------------------------------------------------------- #
# Source-state continuity                                                      #
# --------------------------------------------------------------------------- #
class MemorySourceStateEntry(DigestModel):
    """One logical source/locator present/absent fact + its continuity epoch."""

    schema_version: Literal["1"] = "1"
    source_id: LogicalId
    locator: NonEmptyStr
    presence: Literal["present", "absent"]
    store_content_digest: DigestHex | None = None
    apply_marker_id: LogicalId | None = None
    continuity_epoch: NonNegativeInt

    @model_validator(mode="after")
    def _verify(self) -> "MemorySourceStateEntry":
        if self.presence == "present":
            if self.store_content_digest is None:
                raise ValueError("a present source-state entry requires a store digest")
        else:
            if self.store_content_digest is not None or self.apply_marker_id is not None:
                raise ValueError("an absent source-state entry forbids digest/marker")
        if self.apply_marker_id is not None and not _MARKER_ID_RE.fullmatch(self.apply_marker_id):
            raise ValueError("apply_marker_id must be apply.<64 lowercase hex>")
        return self


class MemorySourceStateSnapshot(DigestModel):
    """A registered, policy/session-independent stable-scan continuity fact.

    Genesis (no predecessor) must bind the exact cutover manifest + attestation
    refs; later forms bind neither a replacement genesis nor a caller epoch.
    ``captured_at`` is scan wall-clock (audit); the continuity content is semantic.
    """

    schema_version: Literal["1"] = "1"
    root_binding_digest: DigestHex
    captured_at: UtcDateTime
    entries: tuple[MemorySourceStateEntry, ...]
    previous_source_state_ref: TypedPayloadRef | None = None
    previous_source_state_hash: DigestHex | None = None
    genesis_manifest_ref: TypedPayloadRef | None = None
    genesis_attestation_ref: TypedPayloadRef | None = None
    content_digest: DigestHex

    SEMANTIC_EXCLUDE: ClassVar[frozenset[str]] = frozenset({"captured_at"})
    SELF_DIGEST_FIELDS: ClassVar[frozenset[str]] = frozenset({"content_digest"})

    @model_validator(mode="after")
    def _verify(self) -> "MemorySourceStateSnapshot":
        keys = tuple((e.source_id, e.locator) for e in self.entries)
        _require_sorted_unique(keys, "source-state entries (by source_id, locator)")
        has_prev_ref = self.previous_source_state_ref is not None
        has_prev_hash = self.previous_source_state_hash is not None
        if has_prev_ref != has_prev_hash:
            raise ValueError("previous_source_state_ref and hash must be present together")
        if has_prev_ref:
            if self.genesis_manifest_ref is not None or self.genesis_attestation_ref is not None:
                raise ValueError("a non-genesis source state binds no genesis refs")
            prev = self.previous_source_state_ref
            if prev.payload_ref.namespace != "main":
                raise ValueError("previous_source_state_ref must be main-namespace")
            if (prev.schema_ref.name != "MemorySourceStateSnapshot"
                    or prev.schema_ref.version != "1"):
                raise ValueError("previous_source_state_ref must resolve MemorySourceStateSnapshot@1")
            if prev.payload_ref.content_digest != self.previous_source_state_hash:
                raise ValueError("previous_source_state_hash must equal the predecessor ref digest")
        else:
            if self.genesis_manifest_ref is None or self.genesis_attestation_ref is None:
                raise ValueError(
                    "the genesis source state must bind the exact cutover manifest + attestation refs"
                )
            gm, ga = self.genesis_manifest_ref, self.genesis_attestation_ref
            if (gm.schema_ref.name != "MemoryCutoverManifest" or gm.schema_ref.version != "1"
                    or gm.payload_ref.namespace != "main"):
                raise ValueError("genesis_manifest_ref must resolve main MemoryCutoverManifest@1")
            if (ga.schema_ref.name != "MemoryCutoverAttestation" or ga.schema_ref.version != "1"
                    or ga.payload_ref.namespace != "main"):
                raise ValueError("genesis_attestation_ref must resolve main MemoryCutoverAttestation@1")
        if self.content_digest != self.semantic_digest():
            raise ValueError("declared content_digest does not match canonical digest")
        return self

    @classmethod
    def build(cls, **fields: Any) -> "MemorySourceStateSnapshot":
        try:
            digest = cls.digest_of_fields(projection="semantic", **fields)
        except (ValueError, TypeError, AttributeError, KeyError):
            digest = _DIGEST_PLACEHOLDER
        return cls(**fields, content_digest=digest)

    def entry_for(self, source_id: str, locator: str) -> MemorySourceStateEntry:
        for e in self.entries:
            if e.source_id == source_id and e.locator == locator:
                return e
        raise MemoryContractError(
            f"no source-state entry for ({source_id!r}, {locator!r})"
        )


# --------------------------------------------------------------------------- #
# Review evidence + owner semantic receipt                                     #
# --------------------------------------------------------------------------- #
def _require_main_typed(ref: TypedPayloadRef, schema: str, label: str) -> None:
    if ref.payload_ref.namespace != "main":
        raise ValueError(f"{label} must be main-namespace")
    if ref.schema_ref.name != schema or ref.schema_ref.version != "1":
        raise ValueError(f"{label} must resolve {schema}@1")


class MemoryOwnerApplySemanticReceipt(DigestModel):
    """The registered semantic projection of one verified owner apply result.

    The closed target CAS matrix distinguishes intent from observation:
    ``create`` has expected/actual before == ``"absent"``; ``update`` has equal
    digest values; both require actual-after == intended-after. Console receipts
    additionally cross-match container digests. Physical root/path, audit
    ID/time, Git state and raw owner log fields live only in an optional audit
    projection and never enter this receipt.
    """

    schema_version: Literal["1"] = "1"
    receipt_kind: Literal["agent", "console"]
    operation: Literal["create", "update"]
    apply_operation_id: LogicalId
    decision_ref: TypedPayloadRef
    proposal_ref: TypedPayloadRef
    source_id: LogicalId
    locator: NonEmptyStr
    marker_id: LogicalId
    expected_before_target_store_digest: StoreDigestPrecondition
    actual_before_target_store_digest: StoreDigestPrecondition
    intended_after_target_store_digest: DigestHex
    actual_after_target_store_digest: DigestHex
    expected_before_container_digest: StoreDigestPrecondition | None = None
    actual_before_container_digest: StoreDigestPrecondition | None = None
    intended_after_container_digest: DigestHex | None = None
    actual_after_container_digest: DigestHex | None = None
    journal_session_id: NonEmptyStr | None = None
    journal_event_id: PositiveInt | None = None

    @model_validator(mode="after")
    def _verify(self) -> "MemoryOwnerApplySemanticReceipt":
        _require_main_typed(self.decision_ref, "MemoryProposalDecision", "decision_ref")
        _require_main_typed(self.proposal_ref, "MemoryProposal", "proposal_ref")
        if not _MARKER_ID_RE.fullmatch(self.marker_id):
            raise ValueError("marker_id must be apply.<64 lowercase hex>")
        if self.operation == "create":
            if self.expected_before_target_store_digest != "absent":
                raise ValueError("create requires expected_before_target_store_digest='absent'")
            if self.actual_before_target_store_digest != "absent":
                raise ValueError("create requires actual_before_target_store_digest='absent'")
        else:  # update
            if (self.expected_before_target_store_digest == "absent"
                    or self.actual_before_target_store_digest == "absent"):
                raise ValueError("update requires concrete before digests")
            if self.expected_before_target_store_digest != self.actual_before_target_store_digest:
                raise ValueError("update requires expected-before == actual-before target digest")
        if self.actual_after_target_store_digest != self.intended_after_target_store_digest:
            raise ValueError("actual-after must equal intended-after target digest")
        container = (
            self.expected_before_container_digest, self.actual_before_container_digest,
            self.intended_after_container_digest, self.actual_after_container_digest,
        )
        if self.receipt_kind == "console":
            if any(v is None for v in container):
                raise ValueError("a console receipt requires all four container digests")
            if self.expected_before_container_digest != self.actual_before_container_digest:
                raise ValueError("console requires expected-before == actual-before container digest")
            if self.actual_after_container_digest != self.intended_after_container_digest:
                raise ValueError("console requires actual-after == intended-after container digest")
            if self.journal_session_id is None or self.journal_event_id is None:
                raise ValueError("a console receipt requires (journal_session_id, event_id)")
        else:
            if any(v is not None for v in container):
                raise ValueError("an agent receipt forbids container digests")
            if self.journal_session_id is not None or self.journal_event_id is not None:
                raise ValueError("an agent receipt forbids journal identity")
        return self


class LegacyCutoverEvidence(DigestModel):
    """Valid only while the genesis source continuity remains unbroken."""

    schema_version: Literal["1"] = "1"
    evidence_kind: Literal["legacy_cutover"] = "legacy_cutover"
    attestation_ref: TypedPayloadRef
    source_payload_ref: TypedPayloadRef
    genesis_continuity_epoch: NonNegativeInt

    @model_validator(mode="after")
    def _verify(self) -> "LegacyCutoverEvidence":
        _require_main_typed(self.attestation_ref, "MemoryCutoverAttestation", "attestation_ref")
        _require_main_typed(
            self.source_payload_ref, "MemoryCutoverSourcePayload", "source_payload_ref"
        )
        return self


class AgentAppliedEvidence(DigestModel):
    """Decision-backed exact agent apply evidence (post-cutover)."""

    schema_version: Literal["1"] = "1"
    evidence_kind: Literal["agent_applied"] = "agent_applied"
    source_id: LogicalId
    locator: NonEmptyStr
    continuity_epoch: NonNegativeInt
    proposal_ref: TypedPayloadRef
    receipt_ref: TypedPayloadRef
    decision_ref: TypedPayloadRef
    owner_receipt_ref: TypedPayloadRef

    @model_validator(mode="after")
    def _verify(self) -> "AgentAppliedEvidence":
        _require_main_typed(self.proposal_ref, "MemoryProposal", "proposal_ref")
        _require_main_typed(self.receipt_ref, "MemoryProposalReceipt", "receipt_ref")
        _require_main_typed(self.decision_ref, "MemoryProposalDecision", "decision_ref")
        _require_main_typed(
            self.owner_receipt_ref, "MemoryOwnerApplySemanticReceipt", "owner_receipt_ref"
        )
        return self


class ConsoleAppliedEvidence(DigestModel):
    """Decision-backed exact console apply evidence (post-cutover)."""

    schema_version: Literal["1"] = "1"
    evidence_kind: Literal["console_applied"] = "console_applied"
    source_id: LogicalId
    locator: NonEmptyStr
    continuity_epoch: NonNegativeInt
    proposal_ref: TypedPayloadRef
    receipt_ref: TypedPayloadRef
    decision_ref: TypedPayloadRef
    owner_receipt_ref: TypedPayloadRef
    journal_session_id: NonEmptyStr
    journal_event_id: PositiveInt

    @model_validator(mode="after")
    def _verify(self) -> "ConsoleAppliedEvidence":
        _require_main_typed(self.proposal_ref, "MemoryProposal", "proposal_ref")
        _require_main_typed(self.receipt_ref, "MemoryProposalReceipt", "receipt_ref")
        _require_main_typed(self.decision_ref, "MemoryProposalDecision", "decision_ref")
        _require_main_typed(
            self.owner_receipt_ref, "MemoryOwnerApplySemanticReceipt", "owner_receipt_ref"
        )
        return self


class MemoryReviewEvidence(DigestModel):
    """The concrete registered evidence envelope (closed discriminated variants)."""

    schema_version: Literal["1"] = "1"
    evidence: Annotated[
        Union[LegacyCutoverEvidence, AgentAppliedEvidence, ConsoleAppliedEvidence],
        Field(discriminator="evidence_kind"),
    ]


_BASIS_FOR_EVIDENCE_KIND: dict[str, str] = {
    "legacy_cutover": "legacy_cutover",
    "agent_applied": "memory_ops_approval",
    "console_applied": "console_proposal_approval",
}


# --------------------------------------------------------------------------- #
# MemoryRecord@1 + identity derivations + builder                              #
# --------------------------------------------------------------------------- #
def derive_memory_record_id(**identity: Any) -> str:
    """Hash a canonical logical identity into ``mem.<lowercase-sha256>``.

    Logical record IDs never contain an absolute root or raw Unicode/path text.
    """
    if not identity:
        raise MemoryContractError("record identity must be non-empty")
    return "mem." + _sha256_hex(
        canonical_json({str(k): v for k, v in identity.items()})
    )


def derive_memory_revision_id(
    *,
    record_id: str,
    text_digest: str,
    continuity_epoch: int,
    review_state: str,
    review_basis: str | None,
    review_evidence_digest: str | None,
    classification_digest: str,
) -> str:
    """The deterministic revision identity — the complete immutable tuple.

    Any changed normalized content, continuity epoch, review state, review
    basis, exact review-evidence digest or policy classification (kind/scope/
    owner/session/importance/mandatory — a reviewed policy reclassification is
    a new capture-timed revision) is a NEW revision.
    """
    return _sha256_hex(
        canonical_json(
            [
                "guanlan.memory.revision.v1",
                record_id,
                text_digest,
                continuity_epoch,
                review_state,
                review_basis,
                review_evidence_digest,
                classification_digest,
            ]
        )
    )


class MemoryRecord(DigestModel):
    """One exact accepted/pending memory revision (registered ``main`` payload).

    ``pending`` requires basis/evidence absent; ``approved`` requires a matching
    positive basis + evidence ref. ``valid_to`` is exclusive. ``content_digest``
    self-seals the record; the Phase-1 :class:`MemoryRecordRef` for this record
    carries exactly this digest.
    """

    schema_version: Literal["1"] = "1"
    record_id: NonEmptyStr
    revision_id: NonEmptyStr
    source_id: LogicalId
    locator: NonEmptyStr
    source_continuity_epoch: NonNegativeInt
    owner_id: LogicalId
    scope: MemoryScopeKind
    session_id: LogicalId | None = None
    kind: MemoryKind
    text: NonEmptyStr
    created_at: UtcDateTime
    valid_from: UtcDateTime
    valid_to: UtcDateTime | None = None
    available_at: UtcDateTime
    review_state: MemoryReviewState
    review_basis: MemoryReviewBasis | None = None
    review_evidence_ref: TypedPayloadRef | None = None
    importance: FiniteFloat
    mandatory: bool = False
    content_digest: DigestHex

    SELF_DIGEST_FIELDS: ClassVar[frozenset[str]] = frozenset({"content_digest"})

    @model_validator(mode="after")
    def _verify(self) -> "MemoryRecord":
        if not _RECORD_ID_RE.fullmatch(self.record_id):
            raise ValueError("record_id must be mem.<64 lowercase hex>")
        if self.scope == "console_session":
            if self.session_id is None:
                raise ValueError("a console_session record requires a session_id")
        else:
            if self.session_id is not None:
                raise ValueError("only console_session records carry a session_id")
        if self.valid_to is not None and not (self.valid_from < self.valid_to):
            raise ValueError("valid_from must precede the exclusive valid_to")
        if self.review_state == "pending":
            if self.review_basis is not None or self.review_evidence_ref is not None:
                raise ValueError("a pending record forbids review basis/evidence")
        else:  # approved
            if self.review_basis is None or self.review_evidence_ref is None:
                raise ValueError("an approved record requires review basis + evidence ref")
            _require_main_typed(
                self.review_evidence_ref, "MemoryReviewEvidence", "review_evidence_ref"
            )
        if self.content_digest != self.semantic_digest():
            raise ValueError("declared content_digest does not match canonical digest")
        return self


def build_memory_record(
    *,
    source_entry: MemorySourceStateEntry,
    owner_id: str,
    scope: str,
    session_id: str | None,
    kind: str,
    text: str,
    created_at: datetime,
    valid_from: datetime,
    valid_to: datetime | None,
    available_at: datetime,
    review_state: str,
    review_basis: str | None,
    review_evidence: MemoryReviewEvidence | None,
    review_evidence_ref: TypedPayloadRef | None,
    importance: float,
    mandatory: bool,
    identity: dict[str, Any],
) -> tuple[MemoryRecord, MemoryRecordRef]:
    """Seal one record revision + emit/verify its matching Phase-1 ref.

    Verifies the continuity epoch against the bound source-state entry, requires
    the evidence basis to match the evidence variant, computes the revision only
    after evidence exists, and returns ``(record, ref)`` with
    ``ref.content_digest == record.content_digest`` (the record's own semantic
    digest — never the raw store digest or the PayloadRef digest of another
    object).
    """
    if source_entry.presence != "present":
        raise MemoryContractError("cannot build a record over an absent source-state entry")
    evidence_digest: str | None = None
    if review_state == "approved":
        if review_evidence is None or review_evidence_ref is None or review_basis is None:
            raise MemoryContractError("an approved record requires evidence + ref + basis")
        expected_basis = _BASIS_FOR_EVIDENCE_KIND[review_evidence.evidence.evidence_kind]
        if review_basis != expected_basis:
            raise MemoryContractError(
                f"review_basis {review_basis!r} does not match evidence kind "
                f"{review_evidence.evidence.evidence_kind!r}"
            )
        if review_evidence_ref.payload_ref.content_digest != review_evidence.semantic_digest():
            raise MemoryContractError("review_evidence_ref does not bind the given evidence")
        ev = review_evidence.evidence
        if not isinstance(ev, LegacyCutoverEvidence):
            if (ev.source_id, ev.locator) != (source_entry.source_id, source_entry.locator):
                raise MemoryContractError("evidence does not bind this logical source")
            if ev.continuity_epoch != source_entry.continuity_epoch:
                raise MemoryContractError(
                    "evidence continuity epoch does not match the bound source-state entry"
                )
        evidence_digest = review_evidence.semantic_digest()
    elif review_state == "pending":
        if review_evidence is not None or review_evidence_ref is not None or review_basis is not None:
            raise MemoryContractError("a pending record forbids review basis/evidence")
    else:
        raise MemoryContractError(f"unknown review_state {review_state!r}")

    record_id = derive_memory_record_id(**identity)
    revision_id = derive_memory_revision_id(
        record_id=record_id,
        text_digest=_sha256_hex(text),
        continuity_epoch=source_entry.continuity_epoch,
        review_state=review_state,
        review_basis=review_basis,
        review_evidence_digest=evidence_digest,
        classification_digest=content_digest(
            {
                "kind": kind,
                "scope": scope,
                "owner_id": owner_id,
                "session_id": session_id,
                "importance": importance,
                "mandatory": mandatory,
            }
        ),
    )
    fields: dict[str, Any] = dict(
        record_id=record_id,
        revision_id=revision_id,
        source_id=source_entry.source_id,
        locator=source_entry.locator,
        source_continuity_epoch=source_entry.continuity_epoch,
        owner_id=owner_id,
        scope=scope,
        session_id=session_id,
        kind=kind,
        text=text,
        created_at=created_at,
        valid_from=valid_from,
        valid_to=valid_to,
        available_at=available_at,
        review_state=review_state,
        review_basis=review_basis,
        review_evidence_ref=review_evidence_ref,
        importance=importance,
        mandatory=mandatory,
    )
    try:
        digest = MemoryRecord.digest_of_fields(projection="semantic", **fields)
    except (ValueError, TypeError, AttributeError, KeyError):
        digest = _DIGEST_PLACEHOLDER
    record = MemoryRecord(**fields, content_digest=digest)
    ref = MemoryRecordRef(
        record_id=record.record_id,
        revision_id=record.revision_id,
        available_at=record.available_at,
        content_digest=record.content_digest,
    )
    return record, ref


# --------------------------------------------------------------------------- #
# MemorySnapshotEntry@1 / MemorySnapshot@1 / capture preparation               #
# --------------------------------------------------------------------------- #
class MemorySnapshotEntry(DigestModel):
    """One snapshot row: the exact Phase-1 ref + the registered record's main ref."""

    schema_version: Literal["1"] = "1"
    record_ref: MemoryRecordRef
    record_payload_ref: TypedPayloadRef

    @model_validator(mode="after")
    def _verify(self) -> "MemorySnapshotEntry":
        _require_main_typed(self.record_payload_ref, "MemoryRecord", "record_payload_ref")
        if self.record_payload_ref.payload_ref.content_digest != self.record_ref.content_digest:
            raise ValueError(
                "record_payload_ref content digest must equal the MemoryRecordRef digest"
            )
        return self


class MemorySnapshot(DigestModel):
    """The complete frozen visible-memory universe of one scope at one as-of.

    The snapshot digest includes scope/cutover/source-state/previous/current-policy
    typed content identities and excludes capture wall-clock and payload object
    IDs. The current snapshot has no self-locator field; after persistence its
    complete ``TypedPayloadRef`` becomes ``ContextSnapshot.memory_snapshot_ref``.
    """

    schema_version: Literal["1"] = "1"
    as_of: UtcDateTime
    capture_completed_at: UtcDateTime
    memory_session_id: LogicalId | None = None
    scope_binding_digest: DigestHex
    cutover_attestation_ref: TypedPayloadRef
    source_state_ref: TypedPayloadRef
    previous_snapshot_hash: DigestHex | None = None
    previous_snapshot_ref: TypedPayloadRef | None = None
    policy_digest: DigestHex
    entries: tuple[MemorySnapshotEntry, ...] = ()
    content_digest: DigestHex

    SEMANTIC_EXCLUDE: ClassVar[frozenset[str]] = frozenset({"capture_completed_at"})
    SELF_DIGEST_FIELDS: ClassVar[frozenset[str]] = frozenset({"content_digest"})

    @model_validator(mode="after")
    def _verify(self) -> "MemorySnapshot":
        _require_main_typed(
            self.cutover_attestation_ref, "MemoryCutoverAttestation", "cutover_attestation_ref"
        )
        _require_main_typed(
            self.source_state_ref, "MemorySourceStateSnapshot", "source_state_ref"
        )
        if (self.previous_snapshot_ref is None) != (self.previous_snapshot_hash is None):
            raise ValueError("previous snapshot hash/ref are both absent only for a scope's first snapshot")
        if self.previous_snapshot_ref is not None:
            _require_main_typed(self.previous_snapshot_ref, "MemorySnapshot", "previous_snapshot_ref")
            if self.previous_snapshot_ref.payload_ref.content_digest != self.previous_snapshot_hash:
                raise ValueError("previous_snapshot_hash must equal the predecessor ref digest")
        keys = [
            (e.record_ref.record_id, e.record_ref.revision_id, e.record_ref.content_digest)
            for e in self.entries
        ]
        if keys != sorted(keys):
            raise ValueError(
                "snapshot entries must be canonically ordered by (record_id, revision_id, content_digest)"
            )
        if len(set(keys)) != len(keys):
            raise ValueError("snapshot entries must be duplicate-free")
        if self.content_digest != self.semantic_digest():
            raise ValueError("declared content_digest does not match canonical digest")
        return self

    @classmethod
    def build(cls, **fields: Any) -> "MemorySnapshot":
        try:
            digest = cls.digest_of_fields(projection="semantic", **fields)
        except (ValueError, TypeError, AttributeError, KeyError):
            digest = _DIGEST_PLACEHOLDER
        return cls(**fields, content_digest=digest)


class MemorySnapshotCapturePreparation(DigestModel):
    """Registered ``main`` recovery-control payload for exactly-once capture.

    Freezes the stable operation key/ID, authoritative capture time, scope/
    session/policy identity and the exact source-state + predecessor refs/hashes
    (or the closed initial form) BEFORE the snapshot payload exists.
    """

    schema_version: Literal["1"] = "1"
    capture_operation_key: NonEmptyStr
    capture_operation_id: LogicalId
    capture_time: UtcDateTime
    as_of: UtcDateTime
    scope_binding_digest: DigestHex
    memory_session_id: LogicalId | None = None
    policy_digest: DigestHex
    source_state_ref: TypedPayloadRef
    source_state_hash: DigestHex
    previous_snapshot_ref: TypedPayloadRef | None = None
    previous_snapshot_hash: DigestHex | None = None

    @model_validator(mode="after")
    def _verify(self) -> "MemorySnapshotCapturePreparation":
        _require_main_typed(self.source_state_ref, "MemorySourceStateSnapshot", "source_state_ref")
        if self.source_state_ref.payload_ref.content_digest != self.source_state_hash:
            raise ValueError("source_state_hash must equal the source_state_ref digest")
        if (self.previous_snapshot_ref is None) != (self.previous_snapshot_hash is None):
            raise ValueError("previous snapshot ref/hash must be present together (closed initial form)")
        if self.previous_snapshot_ref is not None:
            _require_main_typed(self.previous_snapshot_ref, "MemorySnapshot", "previous_snapshot_ref")
            if self.previous_snapshot_ref.payload_ref.content_digest != self.previous_snapshot_hash:
                raise ValueError("previous_snapshot_hash must equal the predecessor ref digest")
        return self


# --------------------------------------------------------------------------- #
# MemoryQuery@1 + authority-specific builders                                   #
# --------------------------------------------------------------------------- #
class MemoryQuery(DigestModel):
    """A frozen, service-derived memory query — never a caller-filled envelope."""

    schema_version: Literal["1"] = "1"
    query_text: NonEmptyStr
    role: Literal["context", "worker"]
    reader_id: LogicalId | None = None
    allowed_kinds: tuple[MemoryKind, ...]
    allowed_scopes: tuple[MemoryScopeKind, ...]
    borrowed_owners: tuple[LogicalId, ...] = ()
    memory_session_id: LogicalId | None = None
    top_k: PositiveInt
    policy_digest: DigestHex

    @model_validator(mode="after")
    def _verify(self) -> "MemoryQuery":
        _require_sorted_unique(self.allowed_kinds, "allowed_kinds")
        _require_sorted_unique(self.allowed_scopes, "allowed_scopes")
        _require_sorted_unique(self.borrowed_owners, "borrowed_owners")
        if self.role == "context":
            if self.reader_id is not None:
                raise ValueError("a context query carries no reader_id")
            if self.borrowed_owners:
                raise ValueError("a context query carries no borrowed owners")
        else:
            if self.reader_id is None:
                raise ValueError("a worker query requires the exact reader_id")
        return self


# --------------------------------------------------------------------------- #
# Internal carriers (non-ContractModel; never registered)                       #
# --------------------------------------------------------------------------- #
class MemoryContextAuthority(_MemoryInternalModel):
    """The authenticated context-level memory authority (service-created only)."""

    memory_session_id: str | None = None
    granted_scopes: tuple[MemoryScopeKind, ...]
    authenticated_by: str


class AuthenticatedAdminPrincipal(_MemoryInternalModel):
    """A fail-closed verifier-produced admin actor (never a body field)."""

    actor: str
    verified_by: str


class ResolvedMemoryPolicy(_MemoryInternalModel):
    """The exact catalog-bound policy material — the ONLY policy with authority."""

    policy: MemoryCapturePolicy
    material_ref: ContentRef

    def require_digest(self, policy_digest: str) -> None:
        if self.policy.policy_digest != policy_digest:
            raise MemoryAuthorityError(
                "policy digest does not match the catalog-bound policy material"
            )


class MemoryReplayBinding(_MemoryInternalModel):
    """The verified PIT_REPLAY projection of a prior persisted context binding."""

    context_snapshot_ref: TypedPayloadRef
    memory_snapshot_ref: TypedPayloadRef
    memory_snapshot_hash: str
    memory_selection_ref: TypedPayloadRef
    past_context_hash: str
    runtime_requirements_ref: TypedPayloadRef
    memory_session_id: str | None
    source_state_ref: TypedPayloadRef


class MemoryReadOutcome(_MemoryInternalModel):
    """One node-level memory read's frozen evidence handles (bridge-internal)."""

    ordinal_token: Any
    query_ref: TypedPayloadRef
    selection_ref: TypedPayloadRef
    rendered_block_ref: TypedPayloadRef | None = None
    selected_memory_refs: tuple[MemoryRecordRef, ...] = ()

    model_config = ConfigDict(extra="forbid", strict=False, frozen=True)


def build_context_memory_query(
    query_text: str,
    top_k: int,
    *,
    authority: MemoryContextAuthority,
    policy: ResolvedMemoryPolicy,
) -> MemoryQuery:
    """Build the context-role query: selection authority is ``authority ∩ policy``.

    Caller/model values may provide query text and a policy-bounded ``top_k``;
    they can never widen role, scope, kind, session or the policy maximum.
    """
    p = policy.policy
    if not isinstance(top_k, int) or isinstance(top_k, bool) or top_k < 1:
        raise MemoryContractError("top_k must be a strict positive int")
    if top_k > p.top_k_non_mandatory:
        raise MemoryAuthorityError(
            f"top_k {top_k} exceeds the policy maximum {p.top_k_non_mandatory}"
        )
    scopes = tuple(sorted(set(authority.granted_scopes) & set(p.context_scopes)))
    return MemoryQuery(
        query_text=query_text,
        role="context",
        reader_id=None,
        allowed_kinds=tuple(sorted(set(p.allowed_kinds))),
        allowed_scopes=scopes,
        borrowed_owners=(),
        memory_session_id=authority.memory_session_id,
        top_k=top_k,
        policy_digest=p.policy_digest,
    )


def build_worker_memory_query(
    query_text: str,
    top_k: int,
    *,
    worker: WorkerSpec,
    context: ContextSnapshot,
    snapshot: MemorySnapshot,
    policy: ResolvedMemoryPolicy,
) -> MemoryQuery:
    """Build the worker-role query over the already frozen context/session boundary.

    Requires ``"memory" in worker.read_categories``, verifies the snapshot binds
    the exact context session, derives own scope from ``worker.id``, permits
    borrowed scopes only when BOTH policy and ``worker.borrowed_from`` grant
    them, and can only retain or narrow the ContextSnapshot session scope.
    """
    p = policy.policy
    if "memory" not in worker.read_categories:
        raise MemoryAuthorityError(
            f"worker {worker.id!r} has no 'memory' read category"
        )
    if snapshot.memory_session_id != context.memory_session_id:
        raise MemoryAuthorityError(
            "snapshot.memory_session_id does not equal the frozen context session"
        )
    if not isinstance(top_k, int) or isinstance(top_k, bool) or top_k < 1:
        raise MemoryContractError("top_k must be a strict positive int")
    if top_k > p.top_k_non_mandatory:
        raise MemoryAuthorityError(
            f"top_k {top_k} exceeds the policy maximum {p.top_k_non_mandatory}"
        )
    borrowed = tuple(sorted(
        set(worker.borrowed_from)
        & {g.owner_id for g in p.borrow_grants if g.borrower_id == worker.id}
    ))
    return MemoryQuery(
        query_text=query_text,
        role="worker",
        reader_id=worker.id,
        allowed_kinds=tuple(sorted(set(p.allowed_kinds))),
        allowed_scopes=tuple(sorted(set(p.worker_scopes))),
        borrowed_owners=borrowed,
        memory_session_id=context.memory_session_id,
        top_k=top_k,
        policy_digest=p.policy_digest,
    )


# --------------------------------------------------------------------------- #
# MemorySelection@1                                                            #
# --------------------------------------------------------------------------- #
class MemorySelectionEntry(DigestModel):
    """One ranked selected record (rank order is semantic)."""

    schema_version: Literal["1"] = "1"
    rank: NonNegativeInt
    record_ref: MemoryRecordRef
    mandatory: bool = False


class MemorySelection(DigestModel):
    """The order-sensitive selection over one exact frozen snapshot.

    ``query_digest`` must equal the persisted query ref's payload content digest;
    query object-ID relocation is audit-only while query semantic drift changes
    this selection's digest. The selection digest is the run's
    ``past_context_hash``.
    """

    schema_version: Literal["1"] = "1"
    snapshot_digest: DigestHex
    query_ref: TypedPayloadRef
    query_digest: DigestHex
    entries: tuple[MemorySelectionEntry, ...] = ()
    content_digest: DigestHex

    SELF_DIGEST_FIELDS: ClassVar[frozenset[str]] = frozenset({"content_digest"})

    @model_validator(mode="after")
    def _verify(self) -> "MemorySelection":
        _require_main_typed(self.query_ref, "MemoryQuery", "query_ref")
        if self.query_ref.payload_ref.content_digest != self.query_digest:
            raise ValueError("query_digest must equal the persisted query ref content digest")
        ranks = [e.rank for e in self.entries]
        if ranks != list(range(len(ranks))):
            raise ValueError("selection entries must carry explicit ranks 0..n-1 in order")
        pairs = [(e.record_ref.record_id, e.record_ref.revision_id) for e in self.entries]
        if len(set(pairs)) != len(pairs):
            raise ValueError("selection entries must be duplicate-free by (record_id, revision_id)")
        if self.content_digest != self.semantic_digest():
            raise ValueError("declared content_digest does not match canonical digest")
        return self

    @classmethod
    def build(cls, **fields: Any) -> "MemorySelection":
        try:
            digest = cls.digest_of_fields(projection="semantic", **fields)
        except (ValueError, TypeError, AttributeError, KeyError):
            digest = _DIGEST_PLACEHOLDER
        return cls(**fields, content_digest=digest)


# --------------------------------------------------------------------------- #
# Proposal targets / request / grant / preparation / proposal                  #
# --------------------------------------------------------------------------- #
class AgentMemoryProposalTarget(DigestModel):
    """Agent-store proposal fields mirroring the existing dream proposal writer."""

    schema_version: Literal["1"] = "1"
    target_kind: Literal["agent"] = "agent"
    target_agent: LogicalId
    topic_slug: LogicalId
    title: NonEmptyStr
    confidence: FiniteFloat
    supporting_cases: tuple[NonEmptyStr, ...] = ()
    reasoning: NonEmptyStr
    lesson_md: NonEmptyStr


_CONSOLE_KEY_FORBIDDEN = re.compile(r"[()\[\]/\\\r\n:：]|\.\.")


class ConsoleMemoryProposalTarget(DigestModel):
    """Console-store proposal fields (scope/key/text; no journal/session authority)."""

    schema_version: Literal["1"] = "1"
    target_kind: Literal["console"] = "console"
    scope: Literal["session", "global"]
    key: str = ""
    text: NonEmptyStr

    @model_validator(mode="after")
    def _verify(self) -> "ConsoleMemoryProposalTarget":
        normalized = normalize_console_memory_write(scope=self.scope, key=self.key, text=self.text)
        if normalized["key"] != self.key or normalized["text"] != self.text:
            raise ValueError(
                "console target key/text must already be in exact normalized form "
                "(a lossy/colliding value is rejected, never silently rewritten)"
            )
        return self


MemoryProposalTarget = Annotated[
    Union[AgentMemoryProposalTarget, ConsoleMemoryProposalTarget],
    Field(discriminator="target_kind"),
]


def normalize_console_memory_write(*, scope: str, key: str, text: str) -> dict[str, str]:
    """The one pure normalizer shared by request, Proposal, Decision, writer,
    capture and tests (normalizer version ``1``).

    Rejects text over 280 characters, CR/LF, empty text, invalid scope and
    lossy/colliding keys (separators, traversal, brackets, colons). Returns the
    exact normalized ``{scope, key, text}`` mapping.
    """
    if scope not in ("session", "global"):
        raise MemoryContractError(f"invalid console memory scope {scope!r}")
    if not isinstance(text, str) or not text.strip():
        raise MemoryContractError("console memory text must be non-empty")
    if "\r" in text or "\n" in text:
        raise MemoryContractError("console memory text must be a single line (no CR/LF)")
    if len(text) > 280:
        raise MemoryContractError("console memory text exceeds the 280-character bound")
    if text != text.strip():
        raise MemoryContractError("console memory text must carry no leading/trailing blank")
    if not isinstance(key, str):
        raise MemoryContractError("console memory key must be a string")
    if key:
        if _CONSOLE_KEY_FORBIDDEN.search(key):
            raise MemoryContractError(
                "console memory key contains separators/traversal/reserved characters"
            )
        if key != key.strip():
            raise MemoryContractError("console memory key must carry no leading/trailing blank")
        if len(key) > 48:
            raise MemoryContractError("console memory key exceeds the 48-character bound")
    return {"scope": scope, "key": key, "text": text}


class MemoryProposalRequest(DigestModel):
    """The worker-safe registered capability input envelope.

    Carries the discriminated ``target`` only — never proposer/run/session/
    journal/expected-digest/actor/path authority.
    """

    schema_version: Literal["1"] = "1"
    target: MemoryProposalTarget


class MemoryProposalGrant(DigestModel):
    """One canonical catalog-bound least-privilege proposer row (no wildcard)."""

    schema_version: Literal["1"] = "1"
    worker_id: LogicalId
    allowed_agent_owners: tuple[LogicalId, ...] = ()
    allowed_console_scopes: tuple[Literal["session", "global"], ...] = ()

    @model_validator(mode="after")
    def _verify(self) -> "MemoryProposalGrant":
        _require_sorted_unique(self.allowed_agent_owners, "allowed_agent_owners")
        _require_sorted_unique(self.allowed_console_scopes, "allowed_console_scopes")
        if not self.allowed_agent_owners and not self.allowed_console_scopes:
            raise ValueError("an empty grant row is meaningless — omit the row instead")
        return self


def _target_logical_identity(target: AgentMemoryProposalTarget | ConsoleMemoryProposalTarget) -> dict[str, Any]:
    """The closed logical-target projection shared by marker/preparation/proposal."""
    if isinstance(target, AgentMemoryProposalTarget):
        return {
            "target_kind": "agent",
            "target_agent": target.target_agent,
            "topic_slug": target.topic_slug,
        }
    return {"target_kind": "console", "scope": target.scope, "key": target.key}


def build_apply_marker_id(
    *,
    request: MemoryProposalRequest,
    proposal_id: str,
    intended_target_payload_digest: str,
) -> str:
    """The closed apply-marker derivation: ``apply.<64 lowercase hex>``.

    Independent of Proposal/Receipt/Decision refs — it binds only the request's
    typed semantic projection, the stable proposal id, the logical target and
    the intended user-payload digest.
    """
    marker_hex = _sha256_hex(
        canonical_json(
            [
                MEMORY_APPLY_MARKER_DOMAIN,
                request.semantic_digest(),
                proposal_id,
                _target_logical_identity(request.target),
                intended_target_payload_digest,
            ]
        )
    )
    return "apply." + marker_hex


class MemoryProposalPreparation(DigestModel):
    """Registered ``main`` recovery-control payload — not a decision or memory.

    Freezes the exact request identity, service-derived authority, normalizer
    version, proposal ID/time/effective date, logical target, marker identity and
    the complete payload/target/container CAS digest domain BEFORE Proposal
    persistence.
    """

    schema_version: Literal["1"] = "1"
    preparation_key: NonEmptyStr
    request_ref: TypedPayloadRef
    request_digest: DigestHex
    proposer_worker_id: LogicalId
    run_id: NonEmptyStr
    plan_digest: DigestHex
    memory_session_id: LogicalId | None = None
    normalizer_version: Literal["1"] = "1"
    proposal_id: LogicalId
    proposed_at: UtcDateTime
    effective_date: NonEmptyStr
    target_kind: Literal["agent", "console"]
    target_identity_digest: DigestHex
    marker_id: LogicalId
    intended_target_payload_digest: DigestHex
    expected_before_target_store_digest: StoreDigestPrecondition
    intended_after_target_store_digest: DigestHex
    expected_before_container_digest: StoreDigestPrecondition | None = None
    intended_after_container_digest: DigestHex | None = None

    @model_validator(mode="after")
    def _verify(self) -> "MemoryProposalPreparation":
        _require_main_typed(self.request_ref, "MemoryProposalRequest", "request_ref")
        if self.request_ref.payload_ref.content_digest != self.request_digest:
            raise ValueError("request_digest must equal the persisted request ref digest")
        if not _MARKER_ID_RE.fullmatch(self.marker_id):
            raise ValueError("marker_id must be apply.<64 lowercase hex>")
        has_container = (
            self.expected_before_container_digest is not None
            or self.intended_after_container_digest is not None
        )
        if self.target_kind == "console":
            if (self.expected_before_container_digest is None
                    or self.intended_after_container_digest is None):
                raise ValueError("a console preparation requires both container digests")
        elif has_container:
            raise ValueError("an agent preparation forbids container digests")
        return self


class MemoryProposal(DigestModel):
    """The service-owned enriched proposal envelope (registered ``main`` payload)."""

    schema_version: Literal["1"] = "1"
    proposal_id: LogicalId
    proposed_at: UtcDateTime
    effective_date: NonEmptyStr
    normalizer_version: Literal["1"] = "1"
    target: MemoryProposalTarget
    proposer_worker_id: LogicalId
    run_id: NonEmptyStr
    plan_digest: DigestHex
    memory_session_id: LogicalId | None = None
    marker_id: LogicalId
    intended_target_payload_digest: DigestHex
    expected_before_target_store_digest: StoreDigestPrecondition
    intended_after_target_store_digest: DigestHex
    expected_before_container_digest: StoreDigestPrecondition | None = None
    intended_after_container_digest: DigestHex | None = None
    request_ref: TypedPayloadRef
    preparation_ref: TypedPayloadRef

    @model_validator(mode="after")
    def _verify(self) -> "MemoryProposal":
        _require_main_typed(self.request_ref, "MemoryProposalRequest", "request_ref")
        _require_main_typed(self.preparation_ref, "MemoryProposalPreparation", "preparation_ref")
        if not _MARKER_ID_RE.fullmatch(self.marker_id):
            raise ValueError("marker_id must be apply.<64 lowercase hex>")
        is_console = self.target.target_kind == "console"
        has_container = (
            self.expected_before_container_digest is not None
            or self.intended_after_container_digest is not None
        )
        if is_console:
            if (self.expected_before_container_digest is None
                    or self.intended_after_container_digest is None):
                raise ValueError("a console proposal requires both container digests")
        elif has_container:
            raise ValueError("an agent proposal forbids container digests")
        return self


def build_memory_proposal(
    request: MemoryProposalRequest,
    *,
    preparation: MemoryProposalPreparation,
    preparation_ref: TypedPayloadRef,
    worker: WorkerSpec,
    plan: Any,
    run_id: str,
    context: ContextSnapshot,
    facade: "MemoryFacadeDescriptor",
) -> MemoryProposal:
    """Verify capability/grant FIRST, then copy only preparation-frozen values.

    Cross-agent, borrowed-owner, session→global and ungranted targets fail; a
    console target from a context with no session also fails. Journal/session
    authority derives only from ``context.memory_session_id`` — there is no
    caller-supplied foreign session/journal value.
    """
    # -- (1) capability + grant, before ANY pending side effect ------------- #
    cap = facade.proposal_capability_ref
    if not any(
        c.id == cap.id and c.version == cap.version and c.content_digest == cap.content_digest
        for c in worker.capability_allowlist
    ):
        raise MemoryAuthorityError(
            f"worker {worker.id!r} does not hold the exact memory.propose capability"
        )
    grant = next((g for g in facade.proposal_grants if g.worker_id == worker.id), None)
    if grant is None:
        raise MemoryAuthorityError(f"worker {worker.id!r} has no proposal grant row")
    target = request.target
    if isinstance(target, AgentMemoryProposalTarget):
        if target.target_agent not in grant.allowed_agent_owners:
            raise MemoryAuthorityError(
                f"worker {worker.id!r} is not granted agent owner {target.target_agent!r}"
            )
        if "agent" not in facade.supported_proposal_targets:
            raise MemoryAuthorityError("the facade does not support agent proposal targets")
    else:
        if target.scope not in grant.allowed_console_scopes:
            raise MemoryAuthorityError(
                f"worker {worker.id!r} is not granted console scope {target.scope!r}"
            )
        if "console" not in facade.supported_proposal_targets:
            raise MemoryAuthorityError("the facade does not support console proposal targets")
        if context.memory_session_id is None:
            raise MemoryAuthorityError(
                "a console proposal target requires a context with a memory session"
            )
    # -- (2) the preparation must bind this exact request/authority --------- #
    _require_main_typed(preparation_ref, "MemoryProposalPreparation", "preparation_ref")
    if preparation_ref.payload_ref.content_digest != preparation.semantic_digest():
        raise MemoryConflictError("preparation_ref does not bind the given typed preparation")
    if preparation.request_digest != request.semantic_digest():
        raise MemoryConflictError("preparation does not bind this exact request")
    if preparation.proposer_worker_id != worker.id:
        raise MemoryConflictError("preparation proposer does not equal the submitting worker")
    if preparation.run_id != run_id:
        raise MemoryConflictError("preparation run does not equal the submitting run")
    if getattr(plan, "plan_digest", None) != preparation.plan_digest:
        raise MemoryConflictError("preparation plan digest does not equal the admitted plan")
    if preparation.memory_session_id != context.memory_session_id:
        raise MemoryConflictError("preparation session does not equal the frozen context session")
    if preparation.target_kind != target.target_kind:
        raise MemoryConflictError("preparation target kind drifted from the request target")
    if preparation.target_identity_digest != content_digest(_target_logical_identity(target)):
        raise MemoryConflictError("preparation logical target drifted from the request target")
    # -- (3) copy ONLY frozen values ---------------------------------------- #
    return MemoryProposal(
        proposal_id=preparation.proposal_id,
        proposed_at=preparation.proposed_at,
        effective_date=preparation.effective_date,
        target=target,
        proposer_worker_id=preparation.proposer_worker_id,
        run_id=preparation.run_id,
        plan_digest=preparation.plan_digest,
        memory_session_id=preparation.memory_session_id,
        marker_id=preparation.marker_id,
        intended_target_payload_digest=preparation.intended_target_payload_digest,
        expected_before_target_store_digest=preparation.expected_before_target_store_digest,
        intended_after_target_store_digest=preparation.intended_after_target_store_digest,
        expected_before_container_digest=preparation.expected_before_container_digest,
        intended_after_container_digest=preparation.intended_after_container_digest,
        request_ref=preparation.request_ref,
        preparation_ref=preparation_ref,
    )


# --------------------------------------------------------------------------- #
# Pending locators / receipt / decision                                        #
# --------------------------------------------------------------------------- #
_LOCATOR_SEGMENT_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9._-]*$")


class AgentPendingLocator(DigestModel):
    """The logical (never physical) pending-file locator for an agent proposal."""

    schema_version: Literal["1"] = "1"
    locator_kind: Literal["agent_pending"] = "agent_pending"
    target_agent: LogicalId
    proposal_id: LogicalId
    relative_locator: NonEmptyStr

    @model_validator(mode="after")
    def _verify(self) -> "AgentPendingLocator":
        loc = self.relative_locator
        if "\\" in loc or loc.startswith("/") or ":" in loc:
            raise ValueError("relative_locator must be a relative forward-slash locator")
        segments = loc.split("/")
        for seg in segments:
            if seg == "..":
                raise ValueError("relative_locator must not traverse ('..')")
            if not _LOCATOR_SEGMENT_RE.fullmatch(seg):
                raise ValueError(f"relative_locator segment {seg!r} is outside the closed form")
        return self


class ConsolePendingEventLocator(DigestModel):
    """The logical pending locator of a console proposal: (journal session, event)."""

    schema_version: Literal["1"] = "1"
    locator_kind: Literal["console_pending"] = "console_pending"
    journal_session_id: NonEmptyStr
    event_id: PositiveInt


class MemoryProposalReceipt(DigestModel):
    """The pending receipt — always ``pending_external_review``, never accepted."""

    schema_version: Literal["1"] = "1"
    proposal_ref: TypedPayloadRef
    pending_store_content_digest: DigestHex
    pending_locator: Annotated[
        Union[AgentPendingLocator, ConsolePendingEventLocator],
        Field(discriminator="locator_kind"),
    ]
    status: Literal["pending_external_review"] = "pending_external_review"

    @model_validator(mode="after")
    def _verify(self) -> "MemoryProposalReceipt":
        _require_main_typed(self.proposal_ref, "MemoryProposal", "proposal_ref")
        return self


class MemoryProposalDecision(DigestModel):
    """The admin-only terminal decision — it contains no ``actual_*`` field.

    The actor comes only from a fail-closed injected ``AdminReviewVerifier``,
    never a body field of the worker-facing schema; workers cannot submit it.
    """

    schema_version: Literal["1"] = "1"
    proposal_ref: TypedPayloadRef
    receipt_ref: TypedPayloadRef
    expected_before_target_store_digest: StoreDigestPrecondition
    intended_after_target_store_digest: DigestHex
    expected_before_container_digest: StoreDigestPrecondition | None = None
    intended_after_container_digest: DigestHex | None = None
    decision: Literal["approved", "rejected"]
    actor: NonEmptyStr
    reason: NonEmptyStr
    decided_at: UtcDateTime

    @model_validator(mode="after")
    def _verify(self) -> "MemoryProposalDecision":
        _require_main_typed(self.proposal_ref, "MemoryProposal", "proposal_ref")
        _require_main_typed(self.receipt_ref, "MemoryProposalReceipt", "receipt_ref")
        return self


# --------------------------------------------------------------------------- #
# Facade descriptor / prefetch binding / rendered block                        #
# --------------------------------------------------------------------------- #
class MemoryQueryProjection(DigestModel):
    """One reviewed (worker → query text/top_k) projection from admitted node params.

    It cannot select a snapshot, scope, policy, renderer, clock or live store;
    model-generated/late query expansion is structurally absent.
    """

    schema_version: Literal["1"] = "1"
    worker_id: LogicalId
    query_text_param_pointer: NonEmptyStr
    top_k: PositiveInt

    @model_validator(mode="after")
    def _verify(self) -> "MemoryQueryProjection":
        if not self.query_text_param_pointer.startswith("/"):
            raise ValueError("query_text_param_pointer must be a JSON pointer starting with '/'")
        return self


class MemoryBridgePrefetchBinding(DigestModel):
    """The reviewed catalog material binding the memory bridge to its projections.

    Unlike the data binding, an EMPTY row tuple is legal and reviewed: it is the
    honest state while no inherited final worker carries the ``memory`` read
    category (``PHASE3_FULL_MEMORY_READERS`` empty).
    """

    schema_version: Literal["1"] = "1"
    bridge_id: LogicalId
    bridge_version: NonEmptyStr
    rows: tuple[MemoryQueryProjection, ...] = ()
    binding_digest: DigestHex

    SELF_DIGEST_FIELDS: ClassVar[frozenset[str]] = frozenset({"binding_digest"})

    @model_validator(mode="after")
    def _verify(self) -> "MemoryBridgePrefetchBinding":
        keys = tuple(r.worker_id for r in self.rows)
        _require_sorted_unique(keys, "prefetch rows (by worker_id)")
        if self.binding_digest != self.semantic_digest():
            raise ValueError("declared binding_digest does not match canonical digest")
        return self

    @classmethod
    def build(cls, **fields: Any) -> "MemoryBridgePrefetchBinding":
        rows = tuple(sorted(fields.pop("rows", ()), key=lambda r: r.worker_id))
        body = dict(rows=rows, **fields)
        try:
            digest = cls.digest_of_fields(projection="semantic", **body)
        except (ValueError, TypeError, AttributeError, KeyError):
            digest = _DIGEST_PLACEHOLDER
        return cls(**body, binding_digest=digest)


class MemoryFacadeDescriptor(DigestModel):
    """The self-contained registered facade body — it contains no self ContentRef.

    ``PHASE3_MEMORY_FACADE_DESCRIPTOR_REF`` (catalog) hashes this body's complete
    canonical JSON bytes as an exact ``kind="guardrail"`` material, so supported
    target/grant or handler/policy/prefetch/renderer drift changes the external
    descriptor ref and catalog digest without a hash cycle.
    """

    schema_version: Literal["1"] = "1"
    descriptor_id: LogicalId
    descriptor_version: NonEmptyStr
    proposal_capability_ref: CapabilityRef
    proposal_handler_ref: ContentRef
    policy_ref: ContentRef
    renderer_ref: ContentRef
    prefetch_binding_ref: ContentRef
    supported_proposal_targets: tuple[Literal["agent", "console"], ...]
    proposal_grants: tuple[MemoryProposalGrant, ...] = ()

    @model_validator(mode="after")
    def _verify(self) -> "MemoryFacadeDescriptor":
        _require_sorted_unique(self.supported_proposal_targets, "supported_proposal_targets")
        if not self.supported_proposal_targets:
            raise ValueError("supported_proposal_targets must be non-empty")
        grant_keys = tuple(g.worker_id for g in self.proposal_grants)
        _require_sorted_unique(grant_keys, "proposal_grants (by worker_id)")
        return self


class RenderedMemoryBlock(DigestModel):
    """A deterministic untrusted-data prompt block over one exact selection.

    Raw memory text can never become prompt/skill/guardrail material — the block
    is returned only as an untrusted-block DTO. Byte/count overflow must fail
    before prompt assembly (enforced by the renderer, re-verified here).
    """

    schema_version: Literal["1"] = "1"
    selection_digest: DigestHex
    snapshot_digest: DigestHex
    renderer_ref: ContentRef
    trust: Literal["untrusted_data"] = "untrusted_data"
    media_type: Literal["text/markdown"] = "text/markdown"
    text: str
    per_record_bytes: tuple[NonNegativeInt, ...] = ()
    total_bytes: NonNegativeInt
    content_digest: DigestHex

    SELF_DIGEST_FIELDS: ClassVar[frozenset[str]] = frozenset({"content_digest"})

    @model_validator(mode="after")
    def _verify(self) -> "RenderedMemoryBlock":
        if self.total_bytes != len(self.text.encode("utf-8")):
            raise ValueError("total_bytes must equal the encoded byte length of text")
        if self.content_digest != self.semantic_digest():
            raise ValueError("declared content_digest does not match canonical digest")
        return self

    @classmethod
    def build(cls, **fields: Any) -> "RenderedMemoryBlock":
        try:
            digest = cls.digest_of_fields(projection="semantic", **fields)
        except (ValueError, TypeError, AttributeError, KeyError):
            digest = _DIGEST_PLACEHOLDER
        return cls(**fields, content_digest=digest)


# --------------------------------------------------------------------------- #
# Reviewed partitions                                                          #
# --------------------------------------------------------------------------- #
#: every registered Task-9 memory payload/fact schema (the full-registry tuple).
MEMORY_PUBLIC_MODELS: tuple[type, ...] = (
    # policy
    MemorySourcePolicyEntry,
    MemoryBorrowGrant,
    MemoryCapturePolicy,
    # cutover
    MemoryCutoverSourcePayload,
    MemoryCutoverManifestEntry,
    MemoryCutoverManifest,
    MemoryCutoverAttestation,
    MemoryCutoverPreparationEntry,
    MemoryCutoverPreparation,
    # source state
    MemorySourceStateEntry,
    MemorySourceStateSnapshot,
    # record / snapshot / capture preparation
    MemoryRecord,
    MemorySnapshotEntry,
    MemorySnapshot,
    MemorySnapshotCapturePreparation,
    # review evidence + owner receipt
    LegacyCutoverEvidence,
    AgentAppliedEvidence,
    ConsoleAppliedEvidence,
    MemoryReviewEvidence,
    MemoryOwnerApplySemanticReceipt,
    # query / selection
    MemoryQuery,
    MemorySelectionEntry,
    MemorySelection,
    # proposals
    AgentMemoryProposalTarget,
    ConsoleMemoryProposalTarget,
    MemoryProposalRequest,
    MemoryProposalGrant,
    MemoryProposalPreparation,
    MemoryProposal,
    AgentPendingLocator,
    ConsolePendingEventLocator,
    MemoryProposalReceipt,
    MemoryProposalDecision,
    # facade / bridge / render
    MemoryQueryProjection,
    MemoryBridgePrefetchBinding,
    MemoryFacadeDescriptor,
    RenderedMemoryBlock,
)

#: frozen internal carriers/ports — deliberately unregistered, with reasons.
#: (They are NOT ContractModel subclasses, so the Phase-1 firewall never sees
#: them; this map is asserted by the memory test suite.)
PHASE3_MEMORY_INTERNAL_MODELS: dict[type, str] = {
    MemoryContextAuthority: (
        "authenticated service-created context authority; possessing/serializing it "
        "grants nothing, so it is never a registered payload"
    ),
    AuthenticatedAdminPrincipal: (
        "fail-closed verifier output; an actor is authority, never payload data"
    ),
    ResolvedMemoryPolicy: (
        "the exact catalog-bound policy material pairing; a checker input, not a payload"
    ),
    MemoryReplayBinding: (
        "verified PIT_REPLAY projection of already persisted refs; carries no new fact"
    ),
    MemoryReadOutcome: (
        "bridge-internal frozen evidence handle (token + typed refs); never persisted"
    ),
}
