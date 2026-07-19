"""Run event envelope, visibility boundary and Plan-freeze approval contracts.

This module freezes the *append record* of the orchestration kernel and the two
structural facts that govern what a subscriber may ever see. No journal,
subscription or barrier runtime lives here — Phase 1 owns only the immutable,
self-sealed event models and their validators; the ``EventStore`` that stamps
time, allocates sequences and enforces persist-then-publish is Phase 2.

The reviewed models
-------------------
* :class:`RunEvent` — one persisted event. Its typed payload is addressed by a
  :class:`~guanlan_v2.orchestration.refs.SchemaRef` (type + version) plus a
  :class:`~guanlan_v2.orchestration.refs.PayloadRef` (namespace + stored
  content), never by untyped ``type``/``version``/``ref`` strings. It seals a
  single ``content_digest`` (its own semantic digest).
* :class:`EventCursor` — a subscriber position in a run/partition visible stream.
* :class:`CommittedArtifactRef` / :class:`LayerCommit` — the typed payload of the
  public ``LayerCommitted`` barrier: the canonically ordered set of artifacts a
  layer atomically made visible.
* :class:`PlanApproval` — the human/authority decision that can authorize a Plan
  freeze, binding one request to one *candidate* plan digest.

The RunEvent digest partition (the crux)
----------------------------------------
A ``RunEvent``'s **semantic** identity is *what happened* — the ``event_type``,
the ``partition``, the ``plan_digest``, the typed payload (``SchemaRef`` +
``PayloadRef``) and the idempotency identity. It deliberately **excludes** the
random ``event_id``, the store-assigned ``journal_seq`` / ``visible_seq``, the
``occurred_at`` wall-clock and the random causal-wiring ids
(``causation_id`` / ``correlation_id``). Those live only in the **audit** digest.
Because a nested :class:`PayloadRef` applies its own projection, re-storing
identical payload content under a new ``object_id`` moves only the audit digest,
while a change to the referenced payload's ``content_digest`` / ``namespace`` —
or to the payload ``SchemaRef``'s ``name`` / ``version`` — moves the semantic
digest. ``content_digest`` is the event's own sealed digest and sits in
``SELF_DIGEST_FIELDS`` so it never self-references.

Visibility and namespace invariants
------------------------------------
* ``journal_seq`` is a ``PositiveInt``; ``visible_seq`` is a positive int or
  ``None`` (positive when present).
* ``ArtifactStaged`` is journal-only: ``visible_seq`` is always ``None``.
  ``LayerCommitted`` is *the* public visibility boundary and requires a
  ``visible_seq``.
* A ``main`` (public) event cannot reference a ``sealed`` / ``review`` / ``audit``
  payload namespace, and a non-public event cannot masquerade as main-public —
  so audit-only refusal detail never rides a public event.
* Trial / Holdout event types (``TrialReserved`` / ``TrialRevealed`` /
  ``TrialExhausted``) are the Phase 4 (Task 3) **additive** extension: appended to
  the :class:`EventType` set without renumbering or reordering the 20 Phase 1
  members, and governed by an additive per-type payload-schema rule
  (``TrialReserved`` ⇒ ``TrialRecord``; ``TrialRevealed`` ⇒ ``TrialRecord`` |
  ``HoldoutReceipt``; ``TrialExhausted`` ⇒ ``HoldoutReceipt`` only on the public
  ``main`` partition, ``HoldoutReceipt`` | ``TrialRecord`` on the non-public
  ``audit`` ledger partition — the metrics restriction is scoped to the public
  partition). The unchanged namespace-masquerade rule still bars any ``main``
  trial event from referencing a ``sealed`` payload, so sealed holdout metrics
  never ride a public event.
"""
from __future__ import annotations

from enum import Enum
from typing import Any, ClassVar, Literal

from pydantic import model_validator

from guanlan_v2.orchestration.digest import (
    DigestHex,
    DigestModel,
    NonEmptyStr,
    NonNegativeInt,
    PositiveInt,
    UtcDateTime,
)
from guanlan_v2.orchestration.enums import ApprovalDecision
from guanlan_v2.orchestration.refs import (
    PUBLIC_PAYLOAD_NAMESPACES,
    PayloadRef,
    SchemaRef,
)

__all__ = [
    "EventType",
    "EventPartition",
    "PUBLIC_EVENT_PARTITIONS",
    "RunEvent",
    "EventCursor",
    "CommittedArtifactRef",
    "LayerCommit",
    "PlanApproval",
]

#: Placeholder digest used by :meth:`RunEvent.build` when field values are
#: malformed: it can never seal a valid record because the ``model_validator``
#: recomputes and re-verifies ``content_digest`` on load.
_DIGEST_PLACEHOLDER = "0" * 64


# --------------------------------------------------------------------------- #
# Phase-1 event-type set + Phase 4 additive Trial / Holdout extension (Task 3)  #
# --------------------------------------------------------------------------- #
class EventType(str, Enum):
    """The run-event vocabulary: the 20 frozen Phase-1 members plus the three
    Phase 4 (Task 3) additive Trial / Holdout members.

    The 20 Phase-1 members keep their exact values and their original order.
    ``TrialReserved`` / ``TrialRevealed`` / ``TrialExhausted`` are **appended**
    (never renumbered or reordered): they are the sealed-holdout evaluation
    vocabulary consumed by the Phase 4 ``TrialLedger``, and :class:`RunEvent`
    enforces their per-type payload-schema rules additively (see
    :meth:`RunEvent._trial_payload_rules`).
    """

    RUN_REQUESTED = "RunRequested"
    PLAN_DRAFTED = "PlanDrafted"
    PLAN_APPROVED = "PlanApproved"
    PLAN_REJECTED = "PlanRejected"
    PLAN_FROZEN = "PlanFrozen"
    BUDGET_RESERVED = "BudgetReserved"
    BUDGET_SETTLED = "BudgetSettled"
    BUDGET_RELEASED = "BudgetReleased"
    NODE_STATE_CHANGED = "NodeStateChanged"
    ARTIFACT_STAGED = "ArtifactStaged"
    LAYER_COMMITTED = "LayerCommitted"
    CONTEXT_SNAPSHOT_FROZEN = "ContextSnapshotFrozen"
    ARTIFACT_RELATED = "ArtifactRelated"
    EXPERIMENT_STATE_CHANGED = "ExperimentStateChanged"
    RUN_CANCELLED = "RunCancelled"
    RUN_COMPLETED = "RunCompleted"
    RUN_FAILED = "RunFailed"
    CASE_CREATED = "CaseCreated"
    CASE_MATURED = "CaseMatured"
    CASE_REVIEWED = "CaseReviewed"

    # --- Phase 4 (Task 3) additive Trial / Holdout members --------------- #
    # Appended after the 20 Phase-1 members; their values are exactly the names
    # reserved in the Phase 1 deferral comments. Their per-type payload rules
    # live in ``RunEvent._trial_payload_rules``.
    TRIAL_RESERVED = "TrialReserved"
    TRIAL_REVEALED = "TrialRevealed"
    TRIAL_EXHAUSTED = "TrialExhausted"

    # --- Phase 6 (Task 9) additive shadow-consumer members --------------- #
    # Appended after the three Phase-4 Trial members (never renumbered or
    # reordered): the honest shadow-consumer replay emits ``ShadowIntentIssued`` when
    # it stages an LLM target book into a runtime intent, and ``ShadowTargetApplied``
    # when a staged target becomes effective at an execution bar. Both are PUBLIC
    # advisory evidence, so :meth:`RunEvent._shadow_partition_rules` pins them to
    # the ``main`` partition; the unchanged namespace-masquerade rule then bars a
    # main shadow event from referencing a non-public payload.
    SHADOW_INTENT_ISSUED = "ShadowIntentIssued"
    SHADOW_TARGET_APPLIED = "ShadowTargetApplied"


#: Closed set of event partitions — the visibility domain of an event. Only
#: ``main`` is publicly visible; ``sealed`` / ``review`` / ``audit`` mirror the
#: non-public payload namespaces. A partition is *never* an authorization by
#: itself; it only classifies which stream an event belongs to.
EventPartition = Literal["main", "sealed", "review", "audit"]
#: The single publicly visible partition.
PUBLIC_EVENT_PARTITIONS: frozenset[str] = frozenset({"main"})


# --------------------------------------------------------------------------- #
# RunEvent — self-sealed append record                                        #
# --------------------------------------------------------------------------- #
class RunEvent(DigestModel):
    """One immutable, self-sealed event in a run's append log.

    Use the pure :meth:`build` classmethod, which computes ``content_digest``
    (the event's own semantic digest) from the field values; the
    ``model_validator`` recomputes and re-verifies it on load, so a persisted
    record whose declared ``content_digest`` drifted — or a per-event-type
    visibility / namespace violation — is rejected.
    """

    schema_version: Literal["1"] = "1"

    # --- audit identity (volatile / store-assigned) --------------------- #
    event_id: NonEmptyStr
    journal_seq: PositiveInt
    visible_seq: PositiveInt | None = None
    occurred_at: UtcDateTime
    causation_id: NonEmptyStr | None = None
    correlation_id: NonEmptyStr | None = None

    # --- semantic identity ---------------------------------------------- #
    run_id: NonEmptyStr
    partition: EventPartition
    event_type: EventType
    plan_digest: DigestHex | None = None
    idempotency_key: NonEmptyStr
    payload_schema_ref: SchemaRef
    payload_ref: PayloadRef

    # --- self-sealed digest --------------------------------------------- #
    content_digest: DigestHex

    #: store-assigned / random / wall-clock identity — excluded from the
    #: *semantic* digest but retained in the *audit* digest.
    SEMANTIC_EXCLUDE: ClassVar[frozenset[str]] = frozenset(
        {"event_id", "journal_seq", "visible_seq", "occurred_at",
         "causation_id", "correlation_id"}
    )
    #: the event's own semantic digest — excluded from every projection so it
    #: never self-references.
    SELF_DIGEST_FIELDS: ClassVar[frozenset[str]] = frozenset({"content_digest"})

    @model_validator(mode="after")
    def _visibility_matrix(self) -> "RunEvent":
        if self.event_type is EventType.ARTIFACT_STAGED and self.visible_seq is not None:
            raise ValueError(
                "ArtifactStaged is journal-only and must have visible_seq=None"
            )
        if self.event_type is EventType.LAYER_COMMITTED and self.visible_seq is None:
            raise ValueError(
                "LayerCommitted is the public visibility boundary and requires a visible_seq"
            )
        return self

    @model_validator(mode="after")
    def _namespace_boundary(self) -> "RunEvent":
        partition_public = self.partition in PUBLIC_EVENT_PARTITIONS
        payload_public = self.payload_ref.namespace in PUBLIC_PAYLOAD_NAMESPACES
        if partition_public and not payload_public:
            raise ValueError(
                "a main/public RunEvent cannot reference a "
                "sealed/review/audit payload namespace"
            )
        if payload_public and not partition_public:
            raise ValueError(
                "a sealed/review/audit RunEvent cannot reference a "
                "main/public payload (namespace masquerade)"
            )
        return self

    @model_validator(mode="after")
    def _trial_payload_rules(self) -> "RunEvent":
        """Phase 4 (Task 3) additive per-type payload-schema rules, expressed over
        ``payload_schema_ref.name`` strings only — Phase 1 never imports ``trial.py``.

        ``TrialExhausted`` is partition-conditional: the metrics restriction
        (``HoldoutReceipt`` only) is scoped to the public ``main`` partition, so the
        full terminal holdout ``TrialRecord`` audit copy stays constructible on the
        non-public ``audit`` ledger partition. The pre-existing namespace-masquerade
        rule is unaffected and still bars a ``main`` trial event from a ``sealed``
        payload.
        """
        et = self.event_type
        if et is EventType.TRIAL_RESERVED:
            allowed = frozenset({"TrialRecord"})
        elif et is EventType.TRIAL_REVEALED:
            allowed = frozenset({"TrialRecord", "HoldoutReceipt"})
        elif et is EventType.TRIAL_EXHAUSTED:
            if self.partition in PUBLIC_EVENT_PARTITIONS:
                allowed = frozenset({"HoldoutReceipt"})
            else:
                allowed = frozenset({"HoldoutReceipt", "TrialRecord"})
        else:
            return self
        if self.payload_schema_ref.name not in allowed:
            raise ValueError(
                f"{et.value} on partition {self.partition!r} accepts payload "
                f"schema name(s) {sorted(allowed)}, not "
                f"{self.payload_schema_ref.name!r}"
            )
        return self

    @model_validator(mode="after")
    def _shadow_partition_rules(self) -> "RunEvent":
        """Phase 6 (Task 9) additive per-type rule: both shadow-consumer event
        types are PUBLIC advisory shadow evidence and live only on the ``main``
        partition (never ``sealed`` / ``review`` / ``audit``).

        This is the shadow analogue of the ``ArtifactStaged`` / ``LayerCommitted``
        visibility rules - expressed over ``event_type`` only, added additively
        without touching any existing validator. The pre-existing
        namespace-masquerade rule (:meth:`_namespace_boundary`) is unaffected and
        still bars a ``main`` shadow event from referencing a ``sealed`` / ``review``
        / ``audit`` payload, so audit-only detail can never ride a public shadow fact.
        """
        if self.event_type in (
            EventType.SHADOW_INTENT_ISSUED,
            EventType.SHADOW_TARGET_APPLIED,
        ) and self.partition not in PUBLIC_EVENT_PARTITIONS:
            raise ValueError(
                f"{self.event_type.value} is public shadow advisory evidence and "
                f"must be on the 'main' partition, not {self.partition!r}"
            )
        return self

    @model_validator(mode="after")
    def _verify_content_digest(self) -> "RunEvent":
        if self.content_digest != self.semantic_digest():
            raise ValueError(
                "declared content_digest does not match canonical semantic digest"
            )
        return self

    @classmethod
    def build(cls, **fields: Any) -> "RunEvent":
        """Seal a RunEvent: compute ``content_digest`` from the field values.

        Malformed field values make the semantic projection raise; we then fall
        back to a placeholder digest so the validating constructor surfaces the
        precise ``ValidationError``. The placeholder can never seal a real record
        because the ``model_validator`` re-verifies on load.
        """
        try:
            stub = cls.model_construct(content_digest=_DIGEST_PLACEHOLDER, **fields)
            digest = stub.semantic_digest()
        except (ValueError, TypeError, AttributeError, KeyError):
            digest = _DIGEST_PLACEHOLDER
        return cls(**fields, content_digest=digest)


# --------------------------------------------------------------------------- #
# EventCursor — subscriber position                                           #
# --------------------------------------------------------------------------- #
class EventCursor(DigestModel):
    """A subscriber's position in a run/partition *visible* stream.

    ``visible_seq`` points at a real visible event, so it is a ``PositiveInt``
    (a "consumed nothing yet" start is expressed by the store's ``after=0``
    parameter, not by a persisted cursor).
    """

    schema_version: Literal["1"] = "1"
    run_id: NonEmptyStr
    partition: EventPartition
    visible_seq: PositiveInt


# --------------------------------------------------------------------------- #
# LayerCommit — the typed payload of the public LayerCommitted barrier         #
# --------------------------------------------------------------------------- #
class CommittedArtifactRef(DigestModel):
    """One artifact made visible by a layer commit, bound to its public sequence.

    ``artifact_seq`` is the canonical public order assigned at commit and is a
    ``PositiveInt``.
    """

    schema_version: Literal["1"] = "1"
    artifact_id: NonEmptyStr
    artifact_seq: PositiveInt


class LayerCommit(DigestModel):
    """The atomic visibility boundary for one DAG layer's successful outputs.

    ``artifacts`` is canonically ordered by ``artifact_seq`` and both the
    sequences and the artifact ids are unique. ``committed_at`` is wall-clock and
    is the sole audit-only fact.
    """

    schema_version: Literal["1"] = "1"
    plan_digest: DigestHex
    layer_index: NonNegativeInt
    node_run_ids: tuple[NonEmptyStr, ...] = ()
    artifacts: tuple[CommittedArtifactRef, ...] = ()
    committed_at: UtcDateTime

    SEMANTIC_EXCLUDE: ClassVar[frozenset[str]] = frozenset({"committed_at"})

    @model_validator(mode="after")
    def _artifacts_sorted_unique(self) -> "LayerCommit":
        seqs = [a.artifact_seq for a in self.artifacts]
        if seqs != sorted(seqs):
            raise ValueError(
                "LayerCommit.artifacts must be canonically sorted by artifact_seq"
            )
        if len(set(seqs)) != len(seqs):
            raise ValueError("LayerCommit.artifacts must have unique artifact_seq")
        ids = [a.artifact_id for a in self.artifacts]
        if len(set(ids)) != len(ids):
            raise ValueError("LayerCommit.artifacts must have unique artifact_id")
        return self


# --------------------------------------------------------------------------- #
# PlanApproval — Plan-freeze authority                                        #
# --------------------------------------------------------------------------- #
class PlanApproval(DigestModel):
    """A single authority decision over one candidate plan.

    It binds exactly one ``request_id`` to the exact pre-freeze
    ``candidate_plan_digest`` it is deciding, with a closed
    :class:`ApprovalDecision`, the deciding ``actor_id`` and an optional
    ``reason``. Only an ``APPROVED`` decision whose request and candidate digest
    both match can authorize a Plan freeze; a ``REJECTED`` decision — or any
    request/candidate mismatch — can never satisfy freeze. ``decided_at`` is
    wall-clock and audit-only.
    """

    schema_version: Literal["1"] = "1"
    request_id: NonEmptyStr
    candidate_plan_digest: DigestHex
    decision: ApprovalDecision
    actor_id: NonEmptyStr
    decided_at: UtcDateTime
    reason: NonEmptyStr | None = None

    SEMANTIC_EXCLUDE: ClassVar[frozenset[str]] = frozenset({"decided_at"})

    @property
    def is_approved(self) -> bool:
        """True iff the decision is ``APPROVED`` (necessary, not sufficient, for freeze)."""
        return self.decision is ApprovalDecision.APPROVED

    def authorizes_freeze(
        self, *, request_id: str, candidate_plan_digest: str
    ) -> bool:
        """Return True only if this approval authorizes freezing that exact candidate.

        Requires an ``APPROVED`` decision **and** an exact match on both the
        request id and the pre-freeze candidate plan digest.
        """
        return (
            self.is_approved
            and self.request_id == request_id
            and self.candidate_plan_digest == candidate_plan_digest
        )

    def require_freeze_authority(
        self, *, request_id: str, candidate_plan_digest: str
    ) -> None:
        """Raise ``ValueError`` unless this approval authorizes the given freeze."""
        if not self.is_approved:
            raise ValueError(
                f"a {self.decision.value} PlanApproval cannot authorize a Plan freeze"
            )
        if self.request_id != request_id:
            raise ValueError(
                "PlanApproval request_id does not match the freeze request"
            )
        if self.candidate_plan_digest != candidate_plan_digest:
            raise ValueError(
                "PlanApproval candidate_plan_digest does not match the frozen candidate"
            )
