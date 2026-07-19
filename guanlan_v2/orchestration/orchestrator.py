"""Phase 7 · dynamic Orchestrator Planner — contracts + strict output parser.

Phase 7 turns the static-runtime kernel (Phases 1–6) into a *dynamic* Planner:
an LLM proposes a plan under a strictly-typed authored-JSON grammar
(``planner-output-v1``), a runtime assembles a real Phase 1 ``PlanDraft`` from
that proposal, and the reviewed approval/admission machinery decides whether it
freezes. This module owns the *contracts* of that loop and the *pure* parser
that converts model output text into a validated, authority-free envelope.

Three registered ``DigestModel`` contracts (registration is Task 9's job — they
exist here unregistered):

* :class:`PlannerSpec` — the frozen identity + budget knobs of a Planner (all
  fields semantic; ``max_generation_attempts <= 3`` is unconstructible);
* :class:`PlannerAttemptRecord` — one generation attempt with an outcome-matrix
  validator; wall-clock + reservation id are audit-only (``SEMANTIC_EXCLUDE``);
* :class:`PlannerRunRecord` — the whole run: strictly-ordered ``1..n<=3``
  attempts and a coherent terminal outcome; ``run_id`` / ``created_at`` are
  audit-only.

The internal :class:`PlannerDraftEnvelope` (+ node / dependency envelopes) is the
*closed* model-authored field set: it can only *select* a catalog worker and
configure it through JSON-shaped ``params`` + dependency injection. It carries
**no** prompt / skill / tool / MCP / handler / path surface and **no** kernel
authority field (``as_of`` / ``mode`` / digests / approval policy / debates /
gates / reducers / legacy-source refs), so a proposal can never smuggle
authority past its params schema. :func:`parse_planner_output` is a pure,
deterministic function: same text ⇒ same envelope or the same canonically-sorted
:class:`PlannerOutputRejected.issue_codes`; it never constructs a
``PlanDraft`` / ``PlanNode`` (Task 2 owns runtime assembly).
"""
from __future__ import annotations

import json
import math
from typing import Any, ClassVar, Literal, NamedTuple

from pydantic import Field, field_validator, model_validator

from guanlan_v2.orchestration.budget import BudgetError, BudgetLedger
from guanlan_v2.orchestration.catalog import (
    ModelTier,
    SkillBinding,
    WorkerCatalogSnapshot,
)
from guanlan_v2.orchestration.catalog_runtime import CatalogRuntime
from guanlan_v2.orchestration.context import ContextSnapshot
from guanlan_v2.orchestration.data.symbols import Symbol, normalize_symbol
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
from guanlan_v2.orchestration.enums import DependencyPolicy, PlanSource
from guanlan_v2.orchestration.plan_presets import (
    PlanPresetError,
    PlanPresetRegistry,
    materialize_fallback_draft,
)
from guanlan_v2.orchestration.refs import (
    ContentRef,
    LogicalId,
    PayloadRef,
    SchemaRef,
    TypedPayloadRef,
)
from guanlan_v2.orchestration.runtime_clock import AuthoritativeClock
from guanlan_v2.orchestration.runtime_contracts import (
    NamedEvidenceDigest,
    PromptUntrustedBlockRef,
)
from guanlan_v2.orchestration.schema_registry import SchemaRegistry
# ``_HIDDEN_AUTHORITY_KEYS`` and the shared params JSON-shape guard are the frozen
# Phase-1 authority blacklist; re-declared here as an *import*, never a copy, so a
# future edit to the kernel's authority surface propagates to the Planner parser.
# ``assemble_dynamic_draft`` lifts the closed authored envelope into a real Phase-1
# ``PlanDraft`` using only these reviewed contracts — it never forks one.
from guanlan_v2.orchestration.spec import (
    Dependency,
    OrchestrationRequest,
    PlanDraft,
    PlanNode,
    PlanValidationReport,
    validate_plan_draft,
    _ensure_json_shaped,
    _HIDDEN_AUTHORITY_KEYS,
)
from guanlan_v2.orchestration.eventstore import PayloadStore
from guanlan_v2.orchestration.worker import ModelGateway, PromptAssembler

__all__ = [
    "PLANNER_OUTPUT_CONTRACT",
    "PlannerAttemptOutcome",
    "PlannerTerminalOutcome",
    "PlannerSpec",
    "PlannerAttemptRecord",
    "PlannerRunRecord",
    "PlannerDependencyEnvelope",
    "PlannerNodeEnvelope",
    "PlannerDraftEnvelope",
    "PlannerOutputRejected",
    "parse_planner_output",
    "PLANNER_RESERVED_AUTHORED_FIELDS",
    "PLANNER_MAX_RATIONALE_CHARS",
    "PlannerShapeRejected",
    "assemble_dynamic_draft",
    "PlannerResult",
    "run_planner",
]

#: Domain constant naming the authored-JSON grammar version this parser accepts.
PLANNER_OUTPUT_CONTRACT = "planner-output-v1"

#: Max number of generation attempts a single Planner run may declare / spend.
_MAX_GENERATION_ATTEMPTS = 3

#: Upper bound on the authored ``rationale`` (bounded so it can never grow into a
#: side channel; it is never parsed for instructions).
PLANNER_MAX_RATIONALE_CHARS = 4000


# =========================================================================== #
# Outcome vocabularies                                                        #
# =========================================================================== #
#: Per-attempt outcome. ``draft_admissible`` is the only success; the rest are the
#: reviewed failure classes of one generation attempt.
PlannerAttemptOutcome = Literal[
    "draft_admissible",
    "model_error",
    "timed_out",
    "parse_rejected",
    "shape_rejected",
    "budget_rejected",
    "validation_rejected",
]

#: Terminal outcome of a whole Planner run.
PlannerTerminalOutcome = Literal[
    "candidate_ready",
    "fallback_materialized",
    "halted_no_fallback",
]


# =========================================================================== #
# Canonical parser issue codes + reserved authored-field blacklist            #
# =========================================================================== #
_C_NOT_JSON = "planner_output_not_json"
_C_NOT_SINGLE_OBJECT = "planner_output_not_single_object"
_C_NOT_OBJECT = "planner_output_not_object"
_C_NON_FINITE = "planner_output_non_finite_number"
_C_NON_STRING_KEY = "planner_output_non_string_key"
_C_UNKNOWN_KEY = "planner_output_unknown_key"
_C_HIDDEN_AUTHORITY = "planner_hidden_authority_key"
_C_RESERVED_FIELD = "planner_authored_reserved_field"
_C_INVALID_SHAPE = "planner_output_invalid_shape"

#: Kernel-authority field names the Planner must NEVER author *anywhere* in its
#: JSON (at any nesting depth). These are stamped by the runtime (Task 2) or owned
#: by the kernel's own validators — a proposal that carries one is rejected with
#: :data:`_C_RESERVED_FIELD` before any model construction.
PLANNER_RESERVED_AUTHORED_FIELDS: frozenset[str] = frozenset(
    {
        "gate_ids",
        "debate_id",
        "round_role",
        "debate_round",
        "debate_turn",
        "condition_ref",
        "max_attempts",
        "auxiliary",
        "approval_policy",
        "source",
        "phase",
        "catalog_digest",
        "schema_registry_digest",
        "as_of",
        "mode",
        "context_snapshot_ref",
        "debates",
        "gates",
        "reducers",
        "stop_condition_refs",
        "legacy_source_schema",
    }
)

#: The exact schema a present ``prompt_assembly_ref`` must resolve.
_PROMPT_ASSEMBLY_SCHEMA_NAME = "PromptAssemblyRecord"
_PROMPT_ASSEMBLY_SCHEMA_VERSION = "1"


# =========================================================================== #
# PlannerSpec                                                                  #
# =========================================================================== #
class PlannerSpec(DigestModel):
    """The frozen identity + generation budget of a dynamic Planner.

    Every field is authorization identity (no ``SEMANTIC_EXCLUDE``): the Planner's
    prompt, skills, guardrails, model tier and attempt budgets all move its
    semantic digest, so two Planners that differ in any of them are distinct
    identities. ``max_generation_attempts`` is capped at 3 by a validator, so a
    Planner that could spend an unbounded number of LLM attempts is
    unconstructible. ``output_contract`` is pinned to :data:`PLANNER_OUTPUT_CONTRACT`.
    """

    schema_version: Literal["1"] = "1"
    planner_id: LogicalId
    version: NonEmptyStr
    system_prompt_ref: ContentRef
    skills: tuple[SkillBinding, ...]
    guardrail_refs: tuple[ContentRef, ...]
    model_tier: ModelTier
    thinking_budget: NonNegativeInt = 0
    max_generation_attempts: PositiveInt
    attempt_token_reservation: PositiveInt
    attempt_timeout_sec: PositiveInt = 300
    output_contract: Literal["planner-output-v1"] = "planner-output-v1"

    @field_validator("max_generation_attempts")
    @classmethod
    def _cap_attempts(cls, v: int) -> int:
        if v > _MAX_GENERATION_ATTEMPTS:
            raise ValueError(
                f"max_generation_attempts must be <= {_MAX_GENERATION_ATTEMPTS}; got {v}"
            )
        return v

    @model_validator(mode="after")
    def _skills_non_empty(self) -> "PlannerSpec":
        if not self.skills:
            raise ValueError("skills must be a non-empty tuple")
        return self


# =========================================================================== #
# PlannerAttemptRecord                                                         #
# =========================================================================== #
class PlannerAttemptRecord(DigestModel):
    """One Planner generation attempt with an outcome-coherence matrix.

    The digests + issue codes an attempt may carry are constrained by its
    ``outcome`` (see :meth:`_outcome_matrix`). ``reservation_id`` and the two
    wall-clock timestamps are audit identity: re-recording a byte-identical
    attempt under a fresh reservation id / different clock leaves the *semantic*
    digest stable while the *audit* digest still records them.
    """

    schema_version: Literal["1"] = "1"
    attempt: PositiveInt
    outcome: PlannerAttemptOutcome
    issue_codes: tuple[NonEmptyStr, ...] = ()
    candidate_plan_digest: DigestHex | None = None
    validation_report_digest: DigestHex | None = None
    prompt_assembly_ref: TypedPayloadRef | None = None
    reservation_id: NonEmptyStr
    started_at: UtcDateTime
    finished_at: UtcDateTime

    SEMANTIC_EXCLUDE: ClassVar[frozenset[str]] = frozenset(
        {"reservation_id", "started_at", "finished_at"}
    )

    @model_validator(mode="after")
    def _outcome_matrix(self) -> "PlannerAttemptRecord":
        cand = self.candidate_plan_digest
        report = self.validation_report_digest
        codes = self.issue_codes

        if self.finished_at < self.started_at:
            raise ValueError("finished_at must be >= started_at")

        if self.outcome == "draft_admissible":
            if cand is None or report is None:
                raise ValueError(
                    "draft_admissible requires both candidate_plan_digest and "
                    "validation_report_digest"
                )
            if codes:
                raise ValueError("draft_admissible must carry no issue_codes")
        elif self.outcome == "validation_rejected":
            if report is None:
                raise ValueError("validation_rejected requires validation_report_digest")
            if not codes:
                raise ValueError("validation_rejected requires non-empty issue_codes")
        elif self.outcome in ("parse_rejected", "shape_rejected", "budget_rejected"):
            if cand is not None:
                raise ValueError(
                    f"{self.outcome} must not carry a candidate_plan_digest"
                )
            if not codes:
                raise ValueError(f"{self.outcome} requires non-empty issue_codes")
        elif self.outcome in ("model_error", "timed_out"):
            if cand is not None or report is not None:
                raise ValueError(f"{self.outcome} must carry no digests")

        if self.prompt_assembly_ref is not None:
            ref = self.prompt_assembly_ref
            if (
                ref.schema_ref.name != _PROMPT_ASSEMBLY_SCHEMA_NAME
                or ref.schema_ref.version != _PROMPT_ASSEMBLY_SCHEMA_VERSION
            ):
                raise ValueError(
                    "prompt_assembly_ref must resolve "
                    f"{_PROMPT_ASSEMBLY_SCHEMA_NAME}@{_PROMPT_ASSEMBLY_SCHEMA_VERSION}"
                )
            if ref.payload_ref.namespace != "main":
                raise ValueError(
                    "prompt_assembly_ref must reference a main-namespace payload"
                )
        return self


# =========================================================================== #
# PlannerRunRecord                                                             #
# =========================================================================== #
class PlannerRunRecord(DigestModel):
    """A whole Planner run: strictly-ordered attempts + a coherent terminal outcome.

    Attempts are numbered exactly ``1..n`` with ``1 <= n <= 3``. The terminal
    outcome constrains the final digest / fallback preset id and whether any
    attempt is ``draft_admissible`` (see :meth:`_terminal_matrix`). ``run_id`` and
    ``created_at`` are audit identity (``SEMANTIC_EXCLUDE``).
    """

    schema_version: Literal["1"] = "1"
    run_id: NonEmptyStr
    request_id: NonEmptyStr
    request_digest: DigestHex
    context_content_digest: DigestHex
    planner_spec_digest: DigestHex
    catalog_digest: DigestHex
    schema_registry_digest: DigestHex
    attempts: tuple[PlannerAttemptRecord, ...]
    terminal_outcome: PlannerTerminalOutcome
    fallback_preset_id: LogicalId | None = None
    final_candidate_plan_digest: DigestHex | None = None
    created_at: UtcDateTime

    SEMANTIC_EXCLUDE: ClassVar[frozenset[str]] = frozenset({"run_id", "created_at"})

    @model_validator(mode="after")
    def _terminal_matrix(self) -> "PlannerRunRecord":
        attempts = self.attempts
        if not attempts:
            raise ValueError("a Planner run must record at least one attempt")
        if len(attempts) > _MAX_GENERATION_ATTEMPTS:
            raise ValueError(
                f"a Planner run may record at most {_MAX_GENERATION_ATTEMPTS} attempts"
            )
        for idx, rec in enumerate(attempts, start=1):
            if rec.attempt != idx:
                raise ValueError(
                    "attempts must be strictly ordered 1..n; "
                    f"attempt #{idx} declares attempt={rec.attempt}"
                )

        any_admissible = any(a.outcome == "draft_admissible" for a in attempts)
        last_admissible = attempts[-1].outcome == "draft_admissible"
        final = self.final_candidate_plan_digest
        fallback = self.fallback_preset_id

        if self.terminal_outcome == "candidate_ready":
            if final is None:
                raise ValueError("candidate_ready requires final_candidate_plan_digest")
            if not last_admissible:
                raise ValueError(
                    "candidate_ready requires the last attempt to be draft_admissible"
                )
            if fallback is not None:
                raise ValueError("candidate_ready must not carry a fallback_preset_id")
        elif self.terminal_outcome == "fallback_materialized":
            if final is None:
                raise ValueError(
                    "fallback_materialized requires final_candidate_plan_digest"
                )
            if fallback is None:
                raise ValueError("fallback_materialized requires fallback_preset_id")
            if any_admissible:
                raise ValueError(
                    "fallback_materialized forbids any draft_admissible attempt"
                )
        elif self.terminal_outcome == "halted_no_fallback":
            if final is not None or fallback is not None:
                raise ValueError(
                    "halted_no_fallback requires both final_candidate_plan_digest "
                    "and fallback_preset_id to be None"
                )
            if any_admissible:
                raise ValueError(
                    "halted_no_fallback forbids any draft_admissible attempt"
                )
        return self


# =========================================================================== #
# Model-authored envelope (closed field set)                                  #
# =========================================================================== #
class PlannerDependencyEnvelope(ContractModel):
    """The closed model-authored dependency edge.

    Mirrors the authored subset of the Phase 1 ``Dependency`` ABI. ``accept_statuses``
    is never model-authored — the Phase 1 defaults apply when the runtime lifts this
    into a real ``Dependency`` (Task 2).
    """

    upstream_node_id: LogicalId
    artifact_slot: LogicalId
    upstream_output_key: LogicalId = "primary"
    inject_as: LogicalId
    policy: Literal["block", "degrade", "skip"] = "block"


class PlannerNodeEnvelope(ContractModel):
    """The closed model-authored node: a catalog ``worker_id`` + JSON params + edges.

    Carries no prompt / skill / tool / MCP / handler / path / debate / gate /
    condition surface — a proposal can only *select* a catalog worker and configure
    it through ``params`` (kept strictly JSON-shaped, exactly as Phase 1's
    ``PlanNode.params`` guard) and dependency injection.
    """

    id: LogicalId
    worker_id: LogicalId
    params: dict[str, Any] = Field(default_factory=dict)
    dependencies: tuple[PlannerDependencyEnvelope, ...] = ()
    writes_slot: LogicalId
    timeout_sec: PositiveInt = 300
    token_reservation: NonNegativeInt = 0

    @field_validator("params")
    @classmethod
    def _params_json_shaped(cls, v: dict[str, Any]) -> dict[str, Any]:
        return _ensure_json_shaped(v)


class PlannerDraftEnvelope(ContractModel):
    """The complete closed model-authored proposal (authority-free).

    Everything the runtime needs to *assemble* a DYNAMIC ``PlanDraft`` that the
    model is allowed to choose; every kernel-authority field (``as_of`` / ``mode``
    / digests / approval policy / debates / gates / reducers / legacy refs) is
    absent and stamped later. ``universe`` holds raw symbol strings normalized by
    Phase 3 during assembly (Task 2). ``rationale`` is bounded and never parsed
    for instructions.
    """

    nodes: tuple[PlannerNodeEnvelope, ...]
    sink_node_ids: tuple[LogicalId, ...]
    universe: tuple[NonEmptyStr, ...] = ()
    budget_request_tokens: NonNegativeInt
    budget_request_llm_invocations: NonNegativeInt
    max_concurrency: PositiveInt = 4
    rationale: str = ""

    @field_validator("rationale")
    @classmethod
    def _bounded_rationale(cls, v: str) -> str:
        if len(v) > PLANNER_MAX_RATIONALE_CHARS:
            raise ValueError(
                f"rationale must be <= {PLANNER_MAX_RATIONALE_CHARS} chars; got {len(v)}"
            )
        return v


# =========================================================================== #
# Strict authored-output parser                                               #
# =========================================================================== #
class PlannerOutputRejected(ValueError):
    """Raised when raw Planner output is not an admissible authored proposal.

    ``issue_codes`` is a canonically-sorted, duplicate-free tuple of the exact
    machine codes that fired, so the same text always yields the same codes.
    """

    def __init__(self, issue_codes: tuple[str, ...]) -> None:
        self.issue_codes: tuple[str, ...] = tuple(sorted(set(issue_codes)))
        super().__init__(
            "planner output rejected: " + ", ".join(self.issue_codes)
        )


def _extract_json_text(raw_text: str) -> str:
    """Strip a single optional ```json fence and surrounding whitespace."""
    s = raw_text.strip()
    if s.startswith("```"):
        newline = s.find("\n")
        if newline == -1:
            return ""  # a lone fence — decodes to empty -> not_json
        body = s[newline + 1:].rstrip()
        if body.endswith("```"):
            body = body[:-3]
        s = body.strip()
    return s


def _scan(value: Any, codes: set[str]) -> None:
    """Recursively collect the structural + blacklist codes at ANY depth."""
    if isinstance(value, float):
        if not math.isfinite(value):
            codes.add(_C_NON_FINITE)
        return
    if isinstance(value, dict):
        for key, sub in value.items():
            if not isinstance(key, str):
                codes.add(_C_NON_STRING_KEY)
                continue
            if key in _HIDDEN_AUTHORITY_KEYS:
                codes.add(_C_HIDDEN_AUTHORITY)
            if key in PLANNER_RESERVED_AUTHORED_FIELDS:
                codes.add(_C_RESERVED_FIELD)
            _scan(sub, codes)
        return
    if isinstance(value, (list, tuple)):
        for item in value:
            _scan(item, codes)


def parse_planner_output(raw_text: str) -> PlannerDraftEnvelope:
    """Parse raw Planner output into a validated, authority-free envelope.

    Pure and deterministic: accepts exactly one JSON object (optionally inside a
    single ```json fence, surrounding whitespace tolerated) and either returns a
    :class:`PlannerDraftEnvelope` or raises :class:`PlannerOutputRejected` with a
    canonically-sorted ``issue_codes`` tuple. Never constructs a
    ``PlanDraft`` / ``PlanNode`` — the envelope is authority-free (Task 2 owns
    runtime assembly).
    """
    text = _extract_json_text(raw_text)

    # -- 1. decode exactly one JSON value ---------------------------------- #
    decoder = json.JSONDecoder()
    try:
        value, end = decoder.raw_decode(text)
    except json.JSONDecodeError:
        raise PlannerOutputRejected((_C_NOT_JSON,))
    if text[end:].strip():
        raise PlannerOutputRejected((_C_NOT_SINGLE_OBJECT,))

    # -- 2. root must be an object ----------------------------------------- #
    if not isinstance(value, dict):
        raise PlannerOutputRejected((_C_NOT_OBJECT,))

    # -- 3. depth-agnostic structural + blacklist scan --------------------- #
    codes: set[str] = set()
    _scan(value, codes)
    if codes:
        raise PlannerOutputRejected(tuple(codes))

    # -- 4. strict typed build (JSON mode keeps scalar strictness while       #
    #        admitting JSON arrays for the envelope's tuple fields) --------- #
    try:
        return PlannerDraftEnvelope.model_validate_json(text)
    except ValueError as exc:
        build_codes: set[str] = set()
        errors = getattr(exc, "errors", None)
        error_list = errors() if callable(errors) else []
        for err in error_list:
            if err.get("type") == "extra_forbidden":
                build_codes.add(_C_UNKNOWN_KEY)
            else:
                build_codes.add(_C_INVALID_SHAPE)
        if not build_codes:
            build_codes.add(_C_INVALID_SHAPE)
        raise PlannerOutputRejected(tuple(build_codes))


# =========================================================================== #
# Runtime-stamped, shrink-only dynamic draft assembly (Task 2)                 #
# =========================================================================== #
# The five generation-side shape issue codes. Each is a *planner-attributed*
# rejection of an authored proposal that the closed parser grammar cannot catch
# (worker identity / dynamic-selectability / budget headroom / symbol grammar /
# duplicate node id). ``planner_worker_not_dynamic`` structurally excludes every
# ``compat.*`` / ``static_legacy_only`` worker here, *before* Phase-1
# ``validate_plan_draft`` would re-reject it under ``compat_worker_forbidden_source``.
_S_UNKNOWN_WORKER = "planner_unknown_worker"
_S_WORKER_NOT_DYNAMIC = "planner_worker_not_dynamic"
_S_BUDGET_EXCEEDS = "planner_budget_exceeds_remaining"
_S_UNIVERSE_INVALID = "planner_universe_symbol_invalid"
_S_DUPLICATE_NODE_ID = "planner_duplicate_node_id"


class PlannerShapeRejected(ValueError):
    """Raised when an authored envelope cannot be assembled into a DYNAMIC draft.

    Mirrors :class:`PlannerOutputRejected`: ``issue_codes`` is a canonically-sorted,
    duplicate-free tuple of the exact machine codes that fired, so identical inputs
    always yield the identical codes. The assembler never partially constructs a
    ``PlanDraft`` — every shape check is accumulated and, if any fired, raised here
    before any node/draft is built.
    """

    def __init__(self, issue_codes: tuple[str, ...]) -> None:
        self.issue_codes: tuple[str, ...] = tuple(sorted(set(issue_codes)))
        super().__init__(
            "planner draft rejected: " + ", ".join(self.issue_codes)
        )


def _lift_dependency(dep: PlannerDependencyEnvelope) -> Dependency:
    """Lift one authored dependency edge into a real Phase-1 :class:`Dependency`.

    The authored ``policy`` string literal is lifted to the Phase-1
    :class:`DependencyPolicy`; ``accept_statuses`` is never model-authored, so the
    Phase-1 default applies (a ``BLOCK`` edge therefore accepts exactly
    ``{COMPLETED}``).
    """
    return Dependency(
        upstream_node_id=dep.upstream_node_id,
        artifact_slot=dep.artifact_slot,
        upstream_output_key=dep.upstream_output_key,
        inject_as=dep.inject_as,
        policy=DependencyPolicy(dep.policy),
    )


def _lift_node(node: PlannerNodeEnvelope) -> PlanNode:
    """Lift one authored node into a real Phase-1 :class:`PlanNode`.

    Only the authored subset (``id`` / ``worker_id`` / ``params`` / ``dependencies``
    / ``writes_slot`` / ``timeout_sec`` / ``token_reservation``) is carried; every
    other :class:`PlanNode` field (gate ids, the debate identity quartet,
    ``condition_ref``, ``auxiliary``, ``max_attempts``) takes its Phase-1 default,
    so a proposal can never smuggle gate/debate/condition authority.
    """
    return PlanNode(
        id=node.id,
        worker_id=node.worker_id,
        params=node.params,
        dependencies=tuple(_lift_dependency(d) for d in node.dependencies),
        writes_slot=node.writes_slot,
        timeout_sec=node.timeout_sec,
        token_reservation=node.token_reservation,
    )


def assemble_dynamic_draft(
    envelope: PlannerDraftEnvelope,
    *,
    request: OrchestrationRequest,
    context: ContextSnapshot,
    context_snapshot_ref: PayloadRef,
    catalog: WorkerCatalogSnapshot,
    schema_registry: SchemaRegistry,
    draft_id: LogicalId,
    run_id: NonEmptyStr,
    remaining_tokens: NonNegativeInt | None = None,
    remaining_llm_invocations: NonNegativeInt | None = None,
) -> PlanDraft:
    """Assemble a runtime-stamped, shrink-only DYNAMIC :class:`PlanDraft`.

    The returned object is a *plain* Phase-1 ``PlanDraft`` (schema_version "2"): no
    subclass, wrapper or extra field. Every kernel-authority / identity field is
    **runtime-stamped**, never model-authored — ``id`` / ``run_id`` / ``request_id``
    / ``phase='main'`` / ``source=DYNAMIC`` / ``goal`` come from the trusted request
    + caller; ``as_of`` / ``mode`` come from ``context.data_context``;
    ``catalog_version`` / ``catalog_digest`` from the sealed catalog snapshot;
    ``schema_registry_digest`` from the sealed registry; ``approval_policy`` is a
    verbatim copy of the request's (the Planner never chooses it); and
    ``debates`` / ``gates`` / ``reducers`` / ``stop_condition_refs`` are empty while
    the legacy compatibility tuple is ``None``. Only the model-authored fields pass
    through: the nodes (each lifted to a ``PlanNode`` with Phase-1 defaults for every
    non-authored field), ``sink_node_ids``, the ``universe`` (each raw symbol string
    normalized through the Phase-3 grammar), the budget request pair and
    ``max_concurrency``.

    ``context_snapshot_ref`` is a trusted runtime input; it must reference the
    supplied ``ContextSnapshot`` (``namespace == 'main'`` and
    ``content_digest == context.content_digest``). A mismatch is a caller wiring
    error, raised as a plain :class:`ValueError` *before* any construction.

    Every generation-side shape rejection (unknown worker; a worker that is not
    ``dynamic_allowed``; an authored budget request that exceeds a supplied
    remaining figure; an unnormalizable universe symbol; a duplicate node id) is
    accumulated into one :class:`PlannerShapeRejected` with canonically-sorted
    ``issue_codes``; the function never partially constructs a draft. Semantic
    catalog/registry checks (params-schema conformance, dependency schema equality,
    decision-sink authority, …) are deliberately left to Phase-1
    :func:`~guanlan_v2.orchestration.spec.validate_plan_draft`, which processes the
    returned plain draft unshimmed.
    """
    # -- 1. runtime context-ref precondition (trusted input, not the planner) - #
    if context_snapshot_ref.namespace != "main":
        raise ValueError(
            "context_snapshot_ref must use namespace='main'; got "
            f"{context_snapshot_ref.namespace!r}"
        )
    if context_snapshot_ref.content_digest != context.content_digest:
        raise ValueError(
            "context_snapshot_ref.content_digest does not bind the supplied "
            "ContextSnapshot content"
        )

    # -- 2. generation-side shape checks (accumulate; never partial-construct) - #
    codes: set[str] = set()
    worker_by_id = {w.id: w for w in catalog.workers}
    seen_ids: set[str] = set()
    for node in envelope.nodes:
        if node.id in seen_ids:
            codes.add(_S_DUPLICATE_NODE_ID)
        seen_ids.add(node.id)
        worker = worker_by_id.get(node.worker_id)
        if worker is None:
            codes.add(_S_UNKNOWN_WORKER)
        elif worker.selection_scope != "dynamic_allowed":
            codes.add(_S_WORKER_NOT_DYNAMIC)

    if remaining_tokens is not None and envelope.budget_request_tokens > remaining_tokens:
        codes.add(_S_BUDGET_EXCEEDS)
    if (
        remaining_llm_invocations is not None
        and envelope.budget_request_llm_invocations > remaining_llm_invocations
    ):
        codes.add(_S_BUDGET_EXCEEDS)

    universe: list[Symbol] = []
    for raw in envelope.universe:
        try:
            universe.append(normalize_symbol(raw))
        except (ValueError, TypeError):
            codes.add(_S_UNIVERSE_INVALID)

    if codes:
        raise PlannerShapeRejected(tuple(codes))

    # -- 3. lift the authored nodes into real Phase-1 PlanNodes --------------- #
    nodes = tuple(_lift_node(n) for n in envelope.nodes)

    # -- 4. construct the runtime-stamped DYNAMIC main PlanDraft -------------- #
    return PlanDraft(
        id=draft_id,
        run_id=run_id,
        request_id=request.request_id,
        phase="main",
        source=PlanSource.DYNAMIC,
        goal=request.goal,
        as_of=context.data_context.as_of,
        mode=context.data_context.mode,
        context_snapshot_ref=context_snapshot_ref,
        universe=tuple(universe),
        nodes=nodes,
        sink_node_ids=envelope.sink_node_ids,
        debates=(),
        gates=(),
        reducers=(),
        catalog_version=catalog.catalog_version,
        catalog_digest=catalog.catalog_digest,
        schema_registry_digest=schema_registry.registry_digest,
        approval_policy=request.approval_policy,
        budget_request_tokens=envelope.budget_request_tokens,
        budget_request_llm_invocations=envelope.budget_request_llm_invocations,
        max_concurrency=envelope.max_concurrency,
        stop_condition_refs=(),
        legacy_source_schema=None,
        legacy_source_config_digest=None,
        legacy_mapping_digest=None,
    )


# =========================================================================== #
# Task 4 — bounded, budget-reserved dynamic Planner generation loop            #
# =========================================================================== #
#: The exact schema refs the loop persists into ``main``.
_PROMPT_ASSEMBLY_SR = SchemaRef(name="PromptAssemblyRecord", version="1")
_PLANNER_RUN_RECORD_SR = SchemaRef(name="PlannerRunRecord", version="1")

#: Synthetic per-attempt issue codes for the non-parser/-shape/-validation outcomes.
_A_BUDGET_REJECTED = "planner_budget_exhausted"
_A_MODEL_ERROR = "planner_model_error"
_A_TIMED_OUT = "planner_timed_out"


class PlannerResult(NamedTuple):
    """The internal carrier :func:`run_planner` returns.

    ``record`` is the persisted :class:`PlannerRunRecord`; ``record_ref`` pairs
    ``PlannerRunRecord@1`` with its stored ``main``-namespace locator. ``draft`` /
    ``report`` are the admissible candidate (``candidate_ready``) or materialized
    fallback (``fallback_materialized``) draft + Phase-1 report, or ``None`` when the
    run halted with no admissible/fallback plan.
    """

    record: PlannerRunRecord
    record_ref: TypedPayloadRef
    draft: PlanDraft | None
    report: PlanValidationReport | None


def _planner_worker_roster(snapshot: WorkerCatalogSnapshot) -> list[dict[str, Any]]:
    """Deterministic trusted projection of the ``dynamic_allowed`` final workers only.

    A ``final`` worker is exactly a ``dynamic_allowed`` worker (Phase-1 invariant), so
    the roster the Planner may select from excludes every ``compat.*`` /
    ``static_legacy_only`` mirror. Sorted by worker id for a byte-stable projection.
    """
    rows: list[dict[str, Any]] = []
    for w in snapshot.workers:
        if w.catalog_role != "final":
            continue
        rows.append(
            {
                "worker_id": w.id,
                "lane": w.lane,
                "persona": w.persona,
                "params_schema_ref": w.params_schema_ref.key if w.params_schema_ref else None,
                "inputs": sorted([ib.name, ib.schema_ref.key] for ib in w.inputs),
                "outputs": sorted([ob.name, ob.schema_ref.key] for ob in w.outputs),
            }
        )
    rows.sort(key=lambda r: r["worker_id"])
    return rows


def _planner_trusted_digests(
    *,
    request: OrchestrationRequest,
    remaining_tokens: int,
    remaining_llm_invocations: int,
    roster: list[dict[str, Any]],
    prior_issue_codes: tuple[str, ...],
) -> tuple[NamedEvidenceDigest, ...]:
    """The trusted-channel named digests (goal + remaining budget + roster + prior
    codes on k>1), canonically ordered by name — never raw untrusted narrative."""
    named: dict[str, str] = {
        "goal": content_digest(request.goal),
        "budget_headroom": content_digest(
            {"tokens": remaining_tokens, "llm_invocations": remaining_llm_invocations}
        ),
        "worker_roster": content_digest(roster),
    }
    if prior_issue_codes:
        named["prior_issue_codes"] = content_digest(sorted(set(prior_issue_codes)))
    return tuple(NamedEvidenceDigest(name=n, digest=named[n]) for n in sorted(named))


def _planner_untrusted_blocks(
    *, context: ContextSnapshot, context_snapshot_ref: PayloadRef
) -> tuple[PromptUntrustedBlockRef, ...]:
    """The ordered untrusted channel: one ``ContextSnapshot``-narrative block *ref*
    (never bytes). Untrusted content can only enter the prompt as a data reference,
    so hostile narrative can at most shape authored low-authority fields."""
    typed = TypedPayloadRef(
        schema_ref=SchemaRef(name="ContextSnapshot", version=context.schema_version),
        payload_ref=context_snapshot_ref,
    )
    block = PromptUntrustedBlockRef.build(
        ordinal=1,
        payload_ref=typed,
        media_type="application/json",
        rendered_length=len(context.content_digest),
    )
    return (block,)


def _classify_planner_output(
    raw_text: str,
    *,
    request: OrchestrationRequest,
    context: ContextSnapshot,
    context_snapshot_ref: PayloadRef,
    catalog: WorkerCatalogSnapshot,
    schema_registry: SchemaRegistry,
    draft_id: LogicalId,
    run_id: NonEmptyStr,
    remaining_tokens: int,
    remaining_llm_invocations: int,
) -> tuple[
    str, tuple[str, ...], str | None, str | None, PlanDraft | None, PlanValidationReport | None
]:
    """Parse -> assemble DYNAMIC draft -> Phase-1 validate, returning the reviewed
    per-attempt classification ``(outcome, issue_codes, candidate_digest,
    report_digest, draft, report)``.

    ``PlannerShapeRejected`` (including the assembly budget-headroom shape code) is a
    generation-side *shape* rejection; ledger exhaustion (a distinct ``budget_rejected``
    outcome) is handled by the caller's reserve step, never here. A plain
    ``ValueError`` from assembly is a caller wiring error and is deliberately not
    caught.
    """
    try:
        envelope = parse_planner_output(raw_text)
    except PlannerOutputRejected as exc:
        return "parse_rejected", exc.issue_codes, None, None, None, None

    try:
        draft = assemble_dynamic_draft(
            envelope,
            request=request,
            context=context,
            context_snapshot_ref=context_snapshot_ref,
            catalog=catalog,
            schema_registry=schema_registry,
            draft_id=draft_id,
            run_id=run_id,
            remaining_tokens=remaining_tokens,
            remaining_llm_invocations=remaining_llm_invocations,
        )
    except PlannerShapeRejected as exc:
        return "shape_rejected", exc.issue_codes, None, None, None, None

    report = validate_plan_draft(
        draft,
        request=request,
        context=context,
        catalog=catalog,
        schema_registry=schema_registry,
    )
    if report.valid:
        return (
            "draft_admissible",
            (),
            report.candidate_plan_digest,
            report.semantic_digest(),
            draft,
            report,
        )
    codes = tuple(i.code for i in report.issues)
    return "validation_rejected", codes, None, report.semantic_digest(), None, report


def _mark_fallback_invalid(
    attempts: list[PlannerAttemptRecord], marker: str
) -> None:
    """Record a ``fallback_invalid:<code>`` marker on the last attempt so a failed /
    unknown fallback is never a silent halt (there is no separate terminal-check
    slot on :class:`PlannerRunRecord`, and the last generation attempt is never
    ``draft_admissible`` in the halt branch)."""
    if not attempts:
        return
    last = attempts[-1]
    if marker in last.issue_codes:
        return
    attempts[-1] = last.model_copy(update={"issue_codes": last.issue_codes + (marker,)})


def _resolve_planner_fallback(
    *,
    request: OrchestrationRequest,
    context: ContextSnapshot,
    context_snapshot_ref: PayloadRef,
    catalog: WorkerCatalogSnapshot,
    schema_registry: SchemaRegistry,
    presets: PlanPresetRegistry,
    draft_id: LogicalId,
    run_id: NonEmptyStr,
    attempts: list[PlannerAttemptRecord],
) -> tuple[
    str, str | None, str | None, PlanDraft | None, PlanValidationReport | None
]:
    """The request-level fallback rule made executable.

    Returns ``(terminal_outcome, final_candidate_digest, fallback_preset_id, draft,
    report)``. A missing fallback field, an unknown preset id, a non-materializable
    preset or an invalid fallback draft all yield ``halted_no_fallback`` — never a
    silent substitute — with a ``fallback_invalid:*`` marker recorded on the last
    attempt for the non-trivial failures.
    """
    if request.fallback_preset_id is None:
        return "halted_no_fallback", None, None, None, None

    try:
        preset = presets.get(request.fallback_preset_id)
    except PlanPresetError:
        _mark_fallback_invalid(attempts, "fallback_invalid:unknown_preset")
        return "halted_no_fallback", None, None, None, None

    try:
        fb_draft = materialize_fallback_draft(
            preset,
            request=request,
            context=context,
            context_snapshot_ref=context_snapshot_ref,
            catalog=catalog,
            schema_registry=schema_registry,
            draft_id=draft_id,
            run_id=run_id,
        )
    except PlanPresetError:
        _mark_fallback_invalid(attempts, "fallback_invalid:preset_not_materializable")
        return "halted_no_fallback", None, None, None, None

    fb_report = validate_plan_draft(
        fb_draft,
        request=request,
        context=context,
        catalog=catalog,
        schema_registry=schema_registry,
    )
    if not fb_report.valid:
        code = fb_report.issues[0].code if fb_report.issues else "invalid_draft"
        _mark_fallback_invalid(attempts, f"fallback_invalid:{code}")
        return "halted_no_fallback", None, None, None, None

    return (
        "fallback_materialized",
        fb_report.candidate_plan_digest,
        request.fallback_preset_id,
        fb_draft,
        fb_report,
    )


def run_planner(
    *,
    request: OrchestrationRequest,
    context: ContextSnapshot,
    context_snapshot_ref: PayloadRef,
    catalog_runtime: CatalogRuntime,
    schema_registry: SchemaRegistry,
    planner_spec: PlannerSpec,
    presets: PlanPresetRegistry,
    budget: BudgetLedger,
    prompt_assembler: PromptAssembler,
    model_gateway: ModelGateway,
    payload_store: PayloadStore,
    clock: AuthoritativeClock,
    run_id: NonEmptyStr,
    draft_id: LogicalId,
) -> PlannerResult:
    """Run the bounded, budget-reserved dynamic-Planner generation loop.

    Per attempt ``k = 1..planner_spec.max_generation_attempts``: (1) reserve one
    planner-scope allocation (ledger exhaustion => ``budget_rejected`` + immediate
    terminal, no free retry); (2) assemble the prompt (trusted planner materials +
    goal + remaining budget + dynamic-worker roster + prior issue codes; untrusted =
    ordered ContextSnapshot narrative block refs) and persist ONE
    ``PromptAssemblyRecord`` to ``main`` *before* invocation; (3) invoke the gateway
    exactly once, bounded by ``attempt_timeout_sec``; (4) parse -> assemble DYNAMIC
    draft -> Phase-1 validate; (5) settle the reservation with actual usage (release
    the remainder). The first ``draft_admissible`` draft ends the loop as
    ``candidate_ready``; exhaustion resolves the request-persisted fallback preset
    (only if valid) else ``halted_no_fallback``.

    Strictly upstream of admission: it never calls ``PlanAdmissionService``, never
    freezes and never emits a plan-lifecycle ``RunEvent``. The persisted
    :class:`PlannerRunRecord` (``record_ref`` = ``PlannerRunRecord@1`` in ``main``) is
    returned inside a :class:`PlannerResult`.
    """
    # -- runtime context-ref precondition (trusted wiring, not the planner) --- #
    if context_snapshot_ref.namespace != "main":
        raise ValueError(
            "context_snapshot_ref must use namespace='main'; got "
            f"{context_snapshot_ref.namespace!r}"
        )
    if context_snapshot_ref.content_digest != context.content_digest:
        raise ValueError(
            "context_snapshot_ref.content_digest does not bind the supplied "
            "ContextSnapshot content"
        )

    request_digest = request.semantic_digest()
    snapshot = catalog_runtime.snapshot
    roster = _planner_worker_roster(snapshot)
    untrusted_blocks = _planner_untrusted_blocks(
        context=context, context_snapshot_ref=context_snapshot_ref
    )
    # trusted planner materials, resolved by exact ref + content-digest identity.
    system_material = catalog_runtime.text(planner_spec.system_prompt_ref)
    skill_materials = tuple(catalog_runtime.text(sb.skill_ref) for sb in planner_spec.skills)
    guardrail_materials = tuple(catalog_runtime.text(g) for g in planner_spec.guardrail_refs)

    attempts: list[PlannerAttemptRecord] = []
    prior_issue_codes: tuple[str, ...] = ()
    admissible_draft: PlanDraft | None = None
    admissible_report: PlanValidationReport | None = None
    admissible_candidate: str | None = None

    for k in range(1, planner_spec.max_generation_attempts + 1):
        started_at = clock.now()

        # -- step 1: reserve the attempt (ledger exhaustion => budget_rejected) - #
        try:
            reservation = budget.reserve_planner(
                request_id=request.request_id,
                request_digest=request_digest,
                attempt=k,
                tokens=planner_spec.attempt_token_reservation,
                llm_invocations=1,
                idempotency_key=f"{run_id}:planner:reserve:{k}",
            )
        except BudgetError:
            finished_at = clock.now()
            attempts.append(
                PlannerAttemptRecord(
                    attempt=k,
                    outcome="budget_rejected",
                    issue_codes=(_A_BUDGET_REJECTED,),
                    reservation_id=f"planner.attempt.{k}.unreserved",
                    started_at=started_at,
                    finished_at=finished_at,
                )
            )
            prior_issue_codes = (_A_BUDGET_REJECTED,)
            break  # immediate terminal branch — no free retry

        # -- step 2: assemble + persist ONE prompt record BEFORE invocation ---- #
        remaining = budget.available()
        trusted = _planner_trusted_digests(
            request=request,
            remaining_tokens=remaining.tokens,
            remaining_llm_invocations=remaining.llm_invocations,
            roster=roster,
            prior_issue_codes=prior_issue_codes,
        )
        assembled = prompt_assembler.assemble(
            plan_digest=request_digest,
            node_id=f"planner.attempt.{k}",
            worker_id=planner_spec.planner_id,
            system_prompt=system_material,
            skills=skill_materials,
            guardrails=guardrail_materials,
            trusted_input_digests=trusted,
            untrusted_blocks=untrusted_blocks,
        )
        prompt_ref = TypedPayloadRef(
            schema_ref=_PROMPT_ASSEMBLY_SR,
            payload_ref=payload_store.put(
                _PROMPT_ASSEMBLY_SR,
                assembled.prompt_record,
                registry_digest=schema_registry.registry_digest,
                namespace="main",
                idempotency_key=f"{run_id}:planner:prompt:{k}",
            ),
        )

        # -- step 3: exactly one model invocation, bounded by attempt_timeout_sec  #
        outcome: str
        codes: tuple[str, ...] = ()
        candidate_digest: str | None = None
        report_digest: str | None = None
        result = None
        invoke_start = clock.now()
        try:
            result = model_gateway.invoke(assembled, prompt_assembly_ref=prompt_ref)
        except Exception:  # provider exception -> model_error (no output produced)
            outcome = "model_error"
            codes = (_A_MODEL_ERROR,)
        else:
            elapsed = (clock.now() - invoke_start).total_seconds()
            if elapsed > planner_spec.attempt_timeout_sec:
                # wall-clock classification (v1): the sync gateway has already
                # returned, so a truly-hung call is not preempted here — a real
                # deadline must be enforced inside the gateway adapter (mirroring
                # Phase 2's provider-raised timeout signal). See report.
                outcome = "timed_out"
                codes = (_A_TIMED_OUT,)
                result = None  # a timed-out call's output is discarded
            else:
                # -- step 4: classify parse -> shape -> validation ------------- #
                (
                    outcome,
                    codes,
                    candidate_digest,
                    report_digest,
                    cand_draft,
                    cand_report,
                ) = _classify_planner_output(
                    result.rendered_text,
                    request=request,
                    context=context,
                    context_snapshot_ref=context_snapshot_ref,
                    catalog=snapshot,
                    schema_registry=schema_registry,
                    draft_id=draft_id,
                    run_id=run_id,
                    remaining_tokens=remaining.tokens,
                    remaining_llm_invocations=remaining.llm_invocations,
                )
                if outcome == "draft_admissible":
                    admissible_draft = cand_draft
                    admissible_report = cand_report
                    admissible_candidate = candidate_digest

        # -- step 5: settle actual usage (release the remainder) --------------- #
        if result is not None:
            used = min(
                result.input_tokens + result.output_tokens,
                planner_spec.attempt_token_reservation,
            )
            budget.settle(
                reservation.reservation_id,
                actual_tokens=used,
                actual_llm_invocations=1,
                idempotency_key=f"{run_id}:planner:settle:{k}",
            )
        else:
            budget.release(
                reservation.reservation_id,
                reason=outcome,
                idempotency_key=f"{run_id}:planner:release:{k}",
            )

        finished_at = clock.now()
        attempts.append(
            PlannerAttemptRecord(
                attempt=k,
                outcome=outcome,
                issue_codes=codes,
                candidate_plan_digest=candidate_digest,
                validation_report_digest=report_digest,
                prompt_assembly_ref=prompt_ref,
                reservation_id=reservation.reservation_id,
                started_at=started_at,
                finished_at=finished_at,
            )
        )
        prior_issue_codes = codes

        if outcome == "draft_admissible":
            break

    # -- terminal resolution --------------------------------------------------- #
    if admissible_draft is not None:
        terminal_outcome = "candidate_ready"
        final_candidate = admissible_candidate
        fallback_id: str | None = None
        final_draft: PlanDraft | None = admissible_draft
        final_report: PlanValidationReport | None = admissible_report
    else:
        (
            terminal_outcome,
            final_candidate,
            fallback_id,
            final_draft,
            final_report,
        ) = _resolve_planner_fallback(
            request=request,
            context=context,
            context_snapshot_ref=context_snapshot_ref,
            catalog=snapshot,
            schema_registry=schema_registry,
            presets=presets,
            draft_id=draft_id,
            run_id=run_id,
            attempts=attempts,
        )

    record = PlannerRunRecord(
        run_id=run_id,
        request_id=request.request_id,
        request_digest=request_digest,
        context_content_digest=context.content_digest,
        planner_spec_digest=planner_spec.semantic_digest(),
        catalog_digest=catalog_runtime.catalog_digest,
        schema_registry_digest=schema_registry.registry_digest,
        attempts=tuple(attempts),
        terminal_outcome=terminal_outcome,
        fallback_preset_id=fallback_id,
        final_candidate_plan_digest=final_candidate,
        created_at=clock.now(),
    )
    record_ref = TypedPayloadRef(
        schema_ref=_PLANNER_RUN_RECORD_SR,
        payload_ref=payload_store.put(
            _PLANNER_RUN_RECORD_SR,
            record,
            registry_digest=schema_registry.registry_digest,
            namespace="main",
            idempotency_key=f"{run_id}:planner:record",
        ),
    )
    return PlannerResult(
        record=record, record_ref=record_ref, draft=final_draft, report=final_report
    )
