"""Artifact / provenance / number-anchor / node-run contracts.

This module freezes the *product* records of a node execution: the typed
:class:`Artifact` a worker emits, the :class:`Provenance` that explains how it
was produced, the :class:`NumberAnchor`s that bind each surfaced figure to a
source, the lightweight :class:`ArtifactRef` / :class:`ArtifactRelation`
linkages, and the :class:`NodeRun` execution record. No pool, persistence,
model-call or event emission lives here — those are later phases.

The crux — three distinct digest meanings on :class:`Artifact`
--------------------------------------------------------------
An artifact is queried three different ways, so it seals three *different*
digests, each computed by a pure builder over a specific field subset and
re-verified on load:

1. ``content_digest`` — the typed ``payload`` plus its payload
   :class:`SchemaRef` (type/version). It answers *"is this the same value?"* and
   moves **only** when the payload or its schema ref changes.
2. ``reproducibility_digest`` — the *stable* provenance: the structural slot
   identity plus the :class:`Provenance` **semantic** projection
   (plan/code/model-config/prompt/skills/capabilities, input-snapshot digest,
   the typed data-result and execution-evidence refs, and each
   :class:`ToolCallRecord`'s ``call_ordinal`` + typed request/result refs) plus
   the input :class:`ArtifactRef`s (by their referenced content
   digest, not their random ids) plus the anchored numbers. It answers *"would
   re-running this produce the same thing?"* The whole ``Provenance`` object is
   **not** excluded — a prompt / skill / model-config / data-input change moves
   this digest.
3. ``audit_digest`` — the complete tamper-evidence digest over the entire
   record (base audit projection): random ids, wall-clock, provider response id
   and every other volatile fact. Changing *only* the provider response id (or
   wall-clock, or a random id) moves **only** this one, because those facts sit
   in no other layer: ``model_response_id`` is in ``Provenance``'s
   ``SEMANTIC_EXCLUDE`` (so absent from reproducibility) and is not payload (so
   absent from content).

The disjointness that matters is therefore between *content* and
*reproducibility* (each isolates a distinct cause), while *audit* is the
superset that additionally captures the purely-volatile facts. This is the exact
behaviour Task 6 requires: "changing only provider response id changes the audit
digest; changing prompt/skill/model-config/data-input changes the
reproducibility digest; changing payload changes the content digest."

``rendered_from_payload_digest`` separately binds the human ``rendered_md`` to
the exact payload it was rendered from; a stale binding is rejected on load, so
payload/rendered drift can never pass silently.
"""
from __future__ import annotations

from typing import Annotated, Any, ClassVar, Generic, Literal, TypeVar

from pydantic import BaseModel, Field, model_validator

from guanlan_v2.orchestration.digest import (
    DigestHex,
    DigestModel,
    FiniteFloat,
    NonEmptyStr,
    NonNegativeInt,
    PositiveInt,
    UtcDateTime,
    content_digest,
)
from guanlan_v2.orchestration.enums import (
    Confidence,
    DataMode,
    NodeStatus,
    PortfolioRating,
    SentimentBand,
)
from guanlan_v2.orchestration.refs import (
    CapabilityRef,
    ContentRef,
    SchemaRef,
    TypedPayloadRef,
    typed_ref_sort_key,
    validate_typed_ref_tuple,
)
from guanlan_v2.orchestration.schema_registry import SchemaRegistry

__all__ = [
    "ArtifactRefRelation",
    "ArtifactRelationKind",
    "ToolCallStatus",
    "ArtifactRef",
    "ToolCallRecord",
    "Provenance",
    "NumberAnchor",
    "Artifact",
    "ArtifactRelation",
    "NodeRun",
    "validate_artifact_payload",
    "ResearchPlan",
    "PortfolioDecision",
    "SentimentReport",
]

T = TypeVar("T")

#: Closed vocabulary for how an input ref relates to the artifact that cites it.
ArtifactRefRelation = Literal["input", "citation", "supports", "refutes"]
#: Closed vocabulary for a standalone artifact-to-artifact relation event.
ArtifactRelationKind = Literal["supersedes", "refutes", "approves", "rejects"]
#: Closed outcome vocabulary for a single tool invocation.
ToolCallStatus = Literal["ok", "error", "refused"]

#: Statuses whose invariant is a declared, reason-coded failure.
_FAILURE_STATUSES: frozenset[NodeStatus] = frozenset(
    {NodeStatus.FAILED, NodeStatus.TIMED_OUT, NodeStatus.CANCELLED}
)

#: Well-formed but deliberately-wrong digest used by :meth:`Artifact.build` when
#: the field values are malformed: it forces the validating constructor to
#: surface the real ``ValidationError`` and can never seal a valid record.
_DIGEST_PLACEHOLDER = "0" * 64


# --------------------------------------------------------------------------- #
# Lightweight refs                                                            #
# --------------------------------------------------------------------------- #
class ArtifactRef(DigestModel):
    """A reference to a produced artifact.

    ``artifact_id`` is the random, storage-assigned identity — audit-only and
    excluded from the semantic projection. The semantic identity of a ref is
    ``schema_ref + producer_node_id + slot + output_key + content_digest``
    (``relation`` too), so two refs to re-productions of identical content under
    different random ids project to the same semantic digest. ``content_digest``
    is the digest of the *referenced* artifact's content, not this ref's own, so
    it is an ordinary semantic field.
    """

    schema_version: Literal["1"] = "1"
    artifact_id: NonEmptyStr
    schema_ref: SchemaRef
    producer_node_id: NonEmptyStr
    slot: NonEmptyStr
    output_key: NonEmptyStr
    content_digest: DigestHex
    relation: ArtifactRefRelation = "input"

    SEMANTIC_EXCLUDE: ClassVar[frozenset[str]] = frozenset({"artifact_id"})


class ToolCallRecord(DigestModel):
    """One *successful finalized* capability invocation made while producing an
    artifact.

    The record is success-only by construction: it carries no status field and
    cannot represent pending / rejected / cache-only work (that work simply has
    no record). ``call_ordinal`` is the service-issued strictly-positive
    ordering key; ``tool_ref`` (which capability) and the typed ``request_ref`` /
    ``result_ref`` (each a main-namespace :class:`TypedPayloadRef` carrying the
    exact schema identity plus the payload's namespace/content digest) are
    semantic — they belong in the reproducibility layer. ``call_id`` /
    ``provider_call_id`` and the ``started_at`` / ``finished_at`` wall-clock are
    audit-only, so a tool call's provider identity and timestamps never leak into
    a parent artifact's reproducibility digest. Each ref's ``payload_ref.object_id``
    is audit-only through the nested :class:`~guanlan_v2.orchestration.refs.PayloadRef`
    projection, so object-id relocation is audit-only too.

    The two typed refs must match the capability's request/output SchemaRefs — a
    cross-object rule the Phase 2 CapabilityGateway enforces; Phase 1 fixes only
    the shape (both refs main, result present, clock coherent).
    """

    schema_version: Literal["1"] = "1"
    call_ordinal: PositiveInt
    tool_ref: CapabilityRef
    request_ref: TypedPayloadRef
    result_ref: TypedPayloadRef
    call_id: NonEmptyStr
    provider_call_id: NonEmptyStr | None = None
    started_at: UtcDateTime
    finished_at: UtcDateTime

    SEMANTIC_EXCLUDE: ClassVar[frozenset[str]] = frozenset(
        {"call_id", "provider_call_id", "started_at", "finished_at"}
    )

    @model_validator(mode="after")
    def _coherent(self) -> "ToolCallRecord":
        if self.request_ref.payload_ref.namespace != "main":
            raise ValueError("request_ref must reference a main-namespace payload")
        if self.result_ref.payload_ref.namespace != "main":
            raise ValueError("result_ref must reference a main-namespace payload")
        if self.finished_at < self.started_at:
            raise ValueError("finished_at must not precede started_at")
        return self


def _validate_evidence_tuples(
    tool_call_records: tuple[ToolCallRecord, ...],
    data_result_refs: tuple[TypedPayloadRef, ...],
    execution_evidence_refs: tuple[TypedPayloadRef, ...],
) -> None:
    """Shared run-level evidence invariant reused by ``Provenance`` and ``NodeRun``.

    Enforces, in order:

    * ``tool_call_records`` are ordered by strictly-increasing service
      ``call_ordinal`` (duplicate-free);
    * ``data_result_refs`` and ``execution_evidence_refs`` are each canonically
      ordered by the typed semantic projection, duplicate-free and main-only
      (delegated to :func:`validate_typed_ref_tuple`);
    * the tool-result / data-result cross-match: a ``data_result_refs`` element
      that shares a finalized tool result's ``content_digest`` must be that exact
      typed result ref (same semantic identity). A verified cache hit shares no
      tool result and therefore lives in ``data_result_refs`` only, fabricating
      no :class:`ToolCallRecord`.

    Raises :class:`ValueError` naming the offending tuple.
    """
    ordinals = [rec.call_ordinal for rec in tool_call_records]
    if ordinals != sorted(ordinals):
        raise ValueError(
            "tool_call_records must be ordered by strictly increasing call_ordinal"
        )
    if len(set(ordinals)) != len(ordinals):
        raise ValueError("tool_call_records must have a duplicate-free call_ordinal")
    validate_typed_ref_tuple(
        data_result_refs, require_main=True, field_name="data_result_refs"
    )
    validate_typed_ref_tuple(
        execution_evidence_refs, require_main=True, field_name="execution_evidence_refs"
    )
    tool_result_keys: dict[str, set[tuple[str, str, str, str]]] = {}
    for rec in tool_call_records:
        result = rec.result_ref
        tool_result_keys.setdefault(result.payload_ref.content_digest, set()).add(
            typed_ref_sort_key(result)
        )
    for ref in data_result_refs:
        keys = tool_result_keys.get(ref.payload_ref.content_digest)
        if keys is not None and typed_ref_sort_key(ref) not in keys:
            raise ValueError(
                "a data_result_refs element that shares a tool result's content "
                "digest must match that exact typed result ref"
            )


class Provenance(DigestModel):
    """How an artifact was produced.

    Every field except ``model_response_id`` is *reproducibility* content: the
    plan/code/model-config pin, the ordered prompt/skill/capability refs, the
    input-snapshot digest, the typed ``data_result_refs`` / ``execution_evidence_refs``
    evidence tuples, and the ordered ``tool_call_records``. ``model_response_id``
    — the specific provider response id — is the one volatile fact here and is
    excluded from the semantic projection, so it participates only in an
    artifact's audit digest. Because a nested ``DigestModel`` applies its own
    projection, the whole ``Provenance`` object is included in — never wholesale
    excluded from — reproducibility, while each typed ref's audit-only object id
    stays out of it.

    The three evidence tuples share one invariant (:func:`_validate_evidence_tuples`):
    tool calls order by service ``call_ordinal``; data-result and
    execution-evidence refs are canonically ordered, duplicate-free and
    main-only; and a consumed data result that is also a finalized tool result
    must cross-match that exact typed result ref (a cache hit appears only in
    ``data_result_refs``).
    """

    schema_version: Literal["1"] = "1"
    plan_digest: DigestHex
    code_version: NonEmptyStr
    as_of: UtcDateTime
    pit_mode: DataMode
    model_config_digest: DigestHex | None = None
    sampling_seed: NonNegativeInt | None = None
    prompt_ref: ContentRef | None = None
    skill_refs: tuple[ContentRef, ...] = ()
    capability_refs: tuple[CapabilityRef, ...] = ()
    input_snapshot_digest: DigestHex | None = None
    data_result_refs: tuple[TypedPayloadRef, ...] = ()
    execution_evidence_refs: tuple[TypedPayloadRef, ...] = ()
    tool_call_records: tuple[ToolCallRecord, ...] = ()
    provider: NonEmptyStr | None = None
    model: NonEmptyStr | None = None
    model_response_id: NonEmptyStr | None = None

    #: the sole volatile provenance fact; absent from the reproducibility layer.
    SEMANTIC_EXCLUDE: ClassVar[frozenset[str]] = frozenset({"model_response_id"})

    @model_validator(mode="after")
    def _evidence_coherent(self) -> "Provenance":
        _validate_evidence_tuples(
            self.tool_call_records,
            self.data_result_refs,
            self.execution_evidence_refs,
        )
        return self


class NumberAnchor(DigestModel):
    """A single surfaced figure bound to its origin in the payload and source.

    ``value`` is a finite float (``NaN`` / ``Inf`` / ``bool`` rejected).
    ``is_unsourced`` is an explicit, required flag: a sourced anchor
    (``is_unsourced=False``) must name a ``source_artifact_id`` or
    ``source_data_result_id``, and an unsourced anchor must name neither. A
    number is therefore never *silently* treated as sourced.
    """

    schema_version: Literal["1"] = "1"
    label: NonEmptyStr
    value: FiniteFloat
    unit: NonEmptyStr | None = None
    as_of: UtcDateTime | None = None
    payload_path: NonEmptyStr
    source_artifact_id: NonEmptyStr | None = None
    source_data_result_id: NonEmptyStr | None = None
    is_unsourced: bool

    @model_validator(mode="after")
    def _sourced_coherent(self) -> "NumberAnchor":
        has_source = (
            self.source_artifact_id is not None or self.source_data_result_id is not None
        )
        if self.is_unsourced and has_source:
            raise ValueError("an unsourced NumberAnchor must not name a source")
        if not self.is_unsourced and not has_source:
            raise ValueError(
                "a sourced NumberAnchor (is_unsourced=False) must name a source "
                "artifact or data result; a number is never silently sourced"
            )
        return self


# --------------------------------------------------------------------------- #
# Artifact — three-layer sealed record                                        #
# --------------------------------------------------------------------------- #
class Artifact(DigestModel, Generic[T]):
    """A typed product of one node, sealed with three independent digests.

    See the module docstring for the reviewed three-layer partition. Use the
    pure :meth:`build` classmethod, which computes ``content_digest``,
    ``reproducibility_digest``, ``audit_digest`` and ``rendered_from_payload_digest``
    from the field values; the ``model_validator`` recomputes and re-verifies all
    four on load, so a persisted record with any mismatched declared digest — or a
    ``rendered_md`` whose payload binding drifted — is rejected.
    """

    # schema_version pinned to "2" (siblings default to "1") per the plan's
    # Global Constraints: Artifact and PlanDraft are the two contracts on "2".
    schema_version: Literal["2"] = "2"

    # --- audit identity (volatile) -------------------------------------- #
    artifact_id: NonEmptyStr
    run_id: NonEmptyStr
    created_at: UtcDateTime

    # --- structural / reproducibility identity -------------------------- #
    producer_node_id: NonEmptyStr
    slot: NonEmptyStr
    output_key: NonEmptyStr
    kind: NonEmptyStr

    # --- content -------------------------------------------------------- #
    payload_schema_ref: SchemaRef
    payload: T

    # --- rendered view + reproducibility evidence ----------------------- #
    rendered_md: str
    input_refs: tuple[ArtifactRef, ...] = ()
    provenance: Provenance
    numbers: tuple[NumberAnchor, ...] = ()
    badges: tuple[NonEmptyStr, ...] = ()

    # --- self-sealed digests -------------------------------------------- #
    rendered_from_payload_digest: DigestHex
    content_digest: DigestHex
    reproducibility_digest: DigestHex
    audit_digest: DigestHex

    #: the three self-declared digests are excluded from every projection so the
    #: full audit projection (``audit_digest_value``) never self-references.
    SELF_DIGEST_FIELDS: ClassVar[frozenset[str]] = frozenset(
        {"content_digest", "reproducibility_digest", "audit_digest"}
    )

    #: field subsets each declared digest is computed over.
    _CONTENT_FIELDS: ClassVar[tuple[str, ...]] = (
        "schema_version",
        "payload_schema_ref",
        "payload",
    )
    _REPRODUCIBILITY_FIELDS: ClassVar[tuple[str, ...]] = (
        "producer_node_id",
        "slot",
        "output_key",
        "kind",
        "input_refs",
        "provenance",
        "numbers",
        "badges",
    )

    # -- pure digest projections ----------------------------------------- #
    def _content_projection(self) -> DigestHex:
        """Digest of the typed payload plus its payload schema ref/version."""
        return content_digest(
            {name: getattr(self, name) for name in self._CONTENT_FIELDS}
        )

    def _reproducibility_projection(self) -> DigestHex:
        """Digest of the stable provenance + structural identity + inputs.

        Nested ``DigestModel``s (``provenance``, ``input_refs``, ``numbers``)
        arrive under their *own* semantic projection, so ``provenance``'s
        ``model_response_id`` and each input ref's random ``artifact_id`` are
        excluded here while their stable content participates.
        """
        return content_digest(
            {name: getattr(self, name) for name in self._REPRODUCIBILITY_FIELDS}
        )

    @model_validator(mode="after")
    def _verify(self) -> "Artifact[T]":
        if self.content_digest != self._content_projection():
            raise ValueError("declared content_digest does not match canonical digest")
        if self.reproducibility_digest != self._reproducibility_projection():
            raise ValueError(
                "declared reproducibility_digest does not match canonical digest"
            )
        if self.audit_digest != self.audit_digest_value():
            raise ValueError("declared audit_digest does not match canonical audit digest")
        if self.rendered_from_payload_digest != content_digest(self.payload):
            raise ValueError(
                "rendered_from_payload_digest does not bind the current payload"
            )
        return self

    @classmethod
    def build(cls, **fields: Any) -> "Artifact[T]":
        """Seal an artifact: compute all four declared digests from the fields.

        ``rendered_from_payload_digest`` is computed from ``payload`` unless
        explicitly provided (a persisted record round-trips it, and a test can
        force a mismatch). Malformed field values make digest projection raise;
        we then fall back to placeholder digests so the validating constructor
        surfaces the precise ``ValidationError``. Placeholders can never seal a
        real record because the ``model_validator`` re-verifies on load.
        """
        sealed = dict(fields)
        if "rendered_from_payload_digest" not in sealed:
            payload = sealed.get("payload")
            try:
                sealed["rendered_from_payload_digest"] = content_digest(payload)
            except (ValueError, TypeError):
                sealed["rendered_from_payload_digest"] = _DIGEST_PLACEHOLDER
        try:
            stub = cls.model_construct(
                content_digest=_DIGEST_PLACEHOLDER,
                reproducibility_digest=_DIGEST_PLACEHOLDER,
                audit_digest=_DIGEST_PLACEHOLDER,
                **sealed,
            )
            content = stub._content_projection()
            reproducibility = stub._reproducibility_projection()
            audit = stub.audit_digest_value()
        except (ValueError, TypeError, AttributeError, KeyError):
            content = reproducibility = audit = _DIGEST_PLACEHOLDER
        return cls(
            **sealed,
            content_digest=content,
            reproducibility_digest=reproducibility,
            audit_digest=audit,
        )


class ArtifactRelation(DigestModel):
    """A standalone relation event between two artifacts.

    ``relation`` / ``from_artifact_id`` / ``to_artifact_id`` are semantic; the
    ``event_id`` and ``created_at`` wall-clock are audit-only. An artifact may
    not relate to itself.
    """

    schema_version: Literal["1"] = "1"
    event_id: NonEmptyStr
    relation: ArtifactRelationKind
    from_artifact_id: NonEmptyStr
    to_artifact_id: NonEmptyStr
    created_at: UtcDateTime

    SEMANTIC_EXCLUDE: ClassVar[frozenset[str]] = frozenset({"event_id", "created_at"})

    @model_validator(mode="after")
    def _distinct_endpoints(self) -> "ArtifactRelation":
        if self.from_artifact_id == self.to_artifact_id:
            raise ValueError("an ArtifactRelation must relate two distinct artifacts")
        return self


# --------------------------------------------------------------------------- #
# NodeRun — execution record                                                  #
# --------------------------------------------------------------------------- #
class NodeRun(DigestModel):
    """One attempt at running one plan node.

    Counters are strict non-negative ints and ``attempt`` is a ``PositiveInt``
    that starts at 1. The status matrix: ``COMPLETED`` requires declared outputs
    (a non-empty ``output_keys`` with a matching count of
    ``output_artifact_ids``); ``FAILED`` / ``TIMED_OUT`` / ``CANCELLED`` each
    require a ``reason_code``. Random ids and wall-clock are audit-only.

    A ``NodeRun`` freezes the exact ``input_snapshot_digest`` plus the three
    typed evidence tuples (``tool_call_records`` / ``data_result_refs`` /
    ``execution_evidence_refs``, under the shared
    :func:`_validate_evidence_tuples` invariant) for **every** terminal status —
    including INCOMPLETE / FAILED / TIMED_OUT / CANCELLED after a successful
    data/tool step. Evidence cannot disappear merely because later prompt/model/
    output work failed. There is deliberately no ``tool_call_count``:
    ``len(tool_call_records)`` is the only truth, so a worker cannot self-report
    a divergent count. On COMPLETED / DEGRADED the produced Artifact's
    ``Provenance`` must carry all three tuples exactly equal to this record — a
    cross-object equality the Phase 2 executor enforces.
    """

    schema_version: Literal["1"] = "1"
    node_run_id: NonEmptyStr
    run_id: NonEmptyStr
    plan_id: NonEmptyStr
    plan_digest: DigestHex
    node_id: NonEmptyStr
    worker_id: NonEmptyStr
    status: NodeStatus
    reason_code: NonEmptyStr | None = None
    reason: NonEmptyStr | None = None
    attempt_id: NonEmptyStr
    attempt: PositiveInt = 1
    input_snapshot_digest: DigestHex
    started_at: UtcDateTime | None = None
    finished_at: UtcDateTime | None = None
    output_keys: tuple[NonEmptyStr, ...] = ()
    output_artifact_ids: tuple[NonEmptyStr, ...] = ()
    tool_call_records: tuple[ToolCallRecord, ...] = ()
    data_result_refs: tuple[TypedPayloadRef, ...] = ()
    execution_evidence_refs: tuple[TypedPayloadRef, ...] = ()
    input_tokens: NonNegativeInt = 0
    output_tokens: NonNegativeInt = 0
    warnings: tuple[NonEmptyStr, ...] = ()
    error_type: NonEmptyStr | None = None

    SEMANTIC_EXCLUDE: ClassVar[frozenset[str]] = frozenset(
        {"node_run_id", "attempt_id", "started_at", "finished_at"}
    )

    @model_validator(mode="after")
    def _evidence_coherent(self) -> "NodeRun":
        _validate_evidence_tuples(
            self.tool_call_records,
            self.data_result_refs,
            self.execution_evidence_refs,
        )
        return self

    @model_validator(mode="after")
    def _status_matrix(self) -> "NodeRun":
        if self.status is NodeStatus.COMPLETED:
            if not self.output_keys:
                raise ValueError("COMPLETED requires at least one declared output key")
            if len(self.output_artifact_ids) != len(self.output_keys):
                raise ValueError(
                    "COMPLETED requires one output artifact id per declared output key"
                )
        if self.status in _FAILURE_STATUSES and self.reason_code is None:
            raise ValueError(f"status={self.status.value} requires a reason_code")
        if (
            self.started_at is not None
            and self.finished_at is not None
            and self.finished_at < self.started_at
        ):
            raise ValueError("finished_at must not precede started_at")
        return self


# --------------------------------------------------------------------------- #
# Pure registry validator                                                     #
# --------------------------------------------------------------------------- #
def validate_artifact_payload(
    artifact: Artifact[Any], registry: SchemaRegistry
) -> Any:
    """Resolve the artifact's payload :class:`SchemaRef` and validate the payload.

    Delegates to ``registry.validate_payload``: it raises
    ``UnknownSchemaError`` for an unregistered ref, ``SchemaVersionMismatchError``
    for a version disagreement, and ``ValidationError`` when the payload does not
    conform to the resolved schema. Returns the validated typed payload.
    """
    payload = artifact.payload
    raw = payload.model_dump() if isinstance(payload, BaseModel) else payload
    return registry.validate_payload(artifact.payload_schema_ref, raw)


# --------------------------------------------------------------------------- #
# Minimal Phase 2 compatibility payloads                                      #
# --------------------------------------------------------------------------- #
# These are the *minimal* typed outputs that exercise the Phase 2 static
# compatibility path and the legacy rating / sentiment adapters. Each is a pure
# value payload: a strict, frozen ``DigestModel`` with a closed
# ``schema_version`` and **no** runtime-generated authority / identity field
# (no artifact id, run id or wall-clock) — a runtime seals those on the wrapping
# :class:`Artifact`, never on the payload. Deferred Phase 5 / 6 / 8 report and
# proposal schemas are intentionally *not* defined here; their consumer phases
# own the final invariants.

#: A strictly-positive finite price. ``FiniteFloat`` already rejects ``bool`` and
#: ``NaN`` / ``±Inf``; ``gt=0`` additionally rejects zero and negatives, so a
#: quoted target can never be non-positive or non-finite.
PositivePrice = Annotated[FiniteFloat, Field(gt=0)]

#: A sentiment score confined to the reviewed ``[0, 10]`` band. ``FiniteFloat``
#: rejects ``bool`` / ``NaN`` / ``±Inf``; ``ge=0, le=10`` bounds it.
SentimentScore = Annotated[FiniteFloat, Field(ge=0, le=10)]


class ResearchPlan(DigestModel):
    """A research recommendation: a five-level rating with its supporting case.

    ``recommendation`` is the closed five-level :class:`PortfolioRating`,
    ``rationale`` is the non-blank narrative behind it, and ``strategic_actions``
    is an ordered, immutable tuple of the concrete actions the plan proposes.
    """

    schema_version: Literal["1"] = "1"
    recommendation: PortfolioRating
    rationale: NonEmptyStr
    strategic_actions: tuple[NonEmptyStr, ...] = ()


class PortfolioDecision(DigestModel):
    """A portfolio-level decision: rating, thesis and an optional price target.

    ``rating`` is the closed five-level :class:`PortfolioRating`.
    ``price_target`` is an optional strictly-positive finite price
    (``bool`` / ``NaN`` / ``Inf`` and non-positive values are rejected) and
    ``time_horizon`` is an optional free-text horizon.
    """

    schema_version: Literal["1"] = "1"
    rating: PortfolioRating
    executive_summary: NonEmptyStr
    investment_thesis: NonEmptyStr
    price_target: PositivePrice | None = None
    time_horizon: str | None = None


class SentimentReport(DigestModel):
    """A sentiment read: a labelled band, a bounded finite score and confidence.

    ``overall_band`` is the closed :class:`SentimentBand`, ``overall_score`` is a
    finite score in ``[0, 10]`` (``bool`` / ``NaN`` / ``Inf`` and out-of-band
    values are rejected), ``confidence`` is the closed :class:`Confidence` and
    ``narrative`` is the non-blank supporting text.
    """

    schema_version: Literal["1"] = "1"
    overall_band: SentimentBand
    overall_score: SentimentScore
    confidence: Confidence
    narrative: NonEmptyStr
