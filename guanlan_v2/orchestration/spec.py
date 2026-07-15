# -*- coding: utf-8 -*-
"""Task 10 — request, offline-validated PlanDraft and frozen Plan contracts.

This module freezes the *authorization surface* of the orchestration kernel: the
persisted :class:`OrchestrationRequest`, the Planner/preset candidate
:class:`PlanDraft`, the frozen executable :class:`Plan`, and the **pure**
validators and freeze builder that stand between them.

Security spine (the load-bearing invariants)
--------------------------------------------
* **No hidden authority in a Plan.** :class:`PlanNode` carries only
  ``worker_id`` + typed ``params`` + graph metadata. It has *no*
  prompt/skill/tool/MCP/callable/path override field. ``params`` is strict
  JSON-shaped config validated **only** against the selected Worker's
  ``params_schema_ref`` (Task 9A), so a hidden ``handler`` / ``system_prompt`` /
  ``skills`` / ``tools`` / ``mcp`` / ``path`` key cannot bypass that schema — the
  strict (``extra="forbid"``) schema rejects it. Conditions, reducers,
  stop-conditions and gate metrics are catalog-owned
  :class:`~guanlan_v2.orchestration.refs.ContentRef` values, never embedded
  Python/expression text.
* **One candidate digest.** :func:`compute_candidate_plan_digest` (domain tag
  ``candidate-plan-v1``) is computed once over the request semantic digest, every
  executable draft field (incl. legacy source/config/mapping digests),
  ContextSnapshot **content** digest, catalog digest, schema-registry digest and
  the exact budget request — excluding its own value, approval/attestation
  records, reservation id and freeze wall-clock. Validator, freeze and Task 12's
  attestation builder all call this one function. ``Plan.plan_digest`` **equals**
  the validated ``candidate_plan_digest``; there is no second post-approval digest.
* **AUTO always fails Phase 1.** ``PlanSource`` is server-recorded provenance,
  not authority; :func:`validate_plan_draft` rejects ``ApprovalPolicy.AUTO`` for
  every source. A ``DYNAMIC`` draft must copy ``REQUIRED`` from the trusted
  request.
* **Freeze re-runs validation.** :func:`freeze_plan` recomputes
  :func:`validate_plan_draft` from the immutable inputs and requires the supplied
  report to *equal* the recomputed result — a caller-constructed ``valid=True``
  report is never authority. The report, the reservation and an
  ``APPROVED`` :class:`~guanlan_v2.orchestration.events.PlanApproval` must all
  bind the *same* request/candidate digest.

Everything here is I/O-free: it reads only supplied immutable snapshots and
performs no file/catalog loading, ledger mutation, scheduling, model/tool call or
approval.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any, ClassVar, Literal

from pydantic import Field, field_validator, model_validator

from guanlan_v2.orchestration.catalog import (
    CatalogError,
    WorkerCatalogSnapshot,
    WorkerSpec,
    validate_catalog_snapshot,
)
from guanlan_v2.orchestration.context import BudgetReservation, ContextSnapshot
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
    ApprovalDecision,
    ApprovalPolicy,
    DataMode,
    DependencyPolicy,
    NodeStatus,
    PlanSource,
)
from guanlan_v2.orchestration.events import PlanApproval
from guanlan_v2.orchestration.refs import (
    ContentRef,
    LogicalId,
    PayloadRef,
    SchemaRef,
)
from guanlan_v2.orchestration.schema_registry import (
    SchemaRegistry,
    SchemaRegistryError,
)
from guanlan_v2.orchestration.data.symbols import Symbol

try:  # pydantic v2 raises ValidationError from payload validation
    from pydantic import ValidationError as _PydValidationError
except Exception:  # pragma: no cover - defensive
    _PydValidationError = Exception  # type: ignore[assignment]

__all__ = [
    "Workflow",
    "PlanPhase",
    "GateOperator",
    "GateStatus",
    "UnavailablePolicy",
    "PlanStructureError",
    "PlanFreezeError",
    "OrchestrationRequest",
    "Dependency",
    "PlanNode",
    "GateCfg",
    "GateResult",
    "DebateCfg",
    "ReducerCfg",
    "PlanDraft",
    "Plan",
    "PlanValidationIssue",
    "PlanValidationReport",
    "StaticLegacyPlanAttestation",
    "validate_plan_structure",
    "compute_candidate_plan_digest",
    "validate_plan_draft",
    "freeze_plan",
    "VALIDATOR_VERSION",
    "CANDIDATE_PLAN_DOMAIN",
]

# --------------------------------------------------------------------------- #
# Closed vocabularies + constants                                             #
# --------------------------------------------------------------------------- #
Workflow = Literal["orchestrate_only", "orchestrate_and_optimize", "optimize_existing"]
PlanPhase = Literal["bootstrap", "main"]
GateOperator = Literal[">", ">=", "<", "<=", "=="]
GateStatus = Literal["passed", "failed", "unavailable"]
UnavailablePolicy = Literal["fail", "degrade", "skip"]

#: domain tag prefixing the single canonical candidate-plan digest projection.
CANDIDATE_PLAN_DOMAIN = "candidate-plan-v1"
#: version stamped on every :class:`PlanValidationReport`.
VALIDATOR_VERSION = "plan-validator-v1"

#: statuses a dependency may legitimately declare as "success enough" to proceed;
#: ``INCOMPLETE`` / ``FAILED`` / ``BLOCKED`` / ``CANCELLED`` etc. never qualify.
_SUCCESS_STATUSES: frozenset[NodeStatus] = frozenset(
    {NodeStatus.COMPLETED, NodeStatus.DEGRADED, NodeStatus.SKIPPED}
)

#: hidden authority keys that must never bypass a worker's params schema.
_HIDDEN_AUTHORITY_KEYS: frozenset[str] = frozenset(
    {"handler", "system_prompt", "skills", "tools", "mcp", "path"}
)

#: payload schema names whose emitting sink requires ``can_emit_decision=True``.
_DECISION_CLASS_SCHEMAS: frozenset[str] = frozenset(
    {"PortfolioDecision", "PortfolioTargetProposal", "TargetPortfolioIntent"}
)

#: the exact executable draft fields the candidate digest binds. Deliberately
#: excludes plan/run identity (``id`` / ``run_id`` / ``request_id`` — request is
#: bound via its own semantic digest) and the audit ``context_snapshot_ref``
#: locator (its content is bound via the separately-passed content digest).
_EXECUTABLE_FIELDS: tuple[str, ...] = (
    "schema_version",
    "phase",
    "source",
    "goal",
    "as_of",
    "mode",
    "universe",
    "nodes",
    "sink_node_ids",
    "debates",
    "gates",
    "reducers",
    "catalog_version",
    "catalog_digest",
    "schema_registry_digest",
    "approval_policy",
    "budget_request_tokens",
    "budget_request_llm_invocations",
    "max_concurrency",
    "stop_condition_refs",
    "legacy_source_schema",
    "legacy_source_config_digest",
    "legacy_mapping_digest",
)


class PlanStructureError(ValueError):
    """A pure, catalog-free graph/shape invariant of a ``PlanDraft`` was violated."""


class PlanFreezeError(ValueError):
    """A Plan freeze precondition (report/approval/reservation) was not satisfied."""


# --------------------------------------------------------------------------- #
# JSON-shape guard for params                                                 #
# --------------------------------------------------------------------------- #
def _ensure_json_shaped(value: Any) -> Any:
    """Reject any non-JSON-native value inside strict ``params`` config."""
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("params float must be finite (NaN/Inf rejected)")
        return value
    if isinstance(value, (list, tuple)):
        return [_ensure_json_shaped(v) for v in value]
    if isinstance(value, dict):
        for k in value:
            if not isinstance(k, str):
                raise ValueError("params dict keys must be strings")
        return {k: _ensure_json_shaped(v) for k, v in value.items()}
    raise ValueError(
        f"params must be strict JSON-shaped config; got {type(value).__name__}"
    )


# --------------------------------------------------------------------------- #
# OrchestrationRequest                                                        #
# --------------------------------------------------------------------------- #
class OrchestrationRequest(DigestModel):
    """The persisted, pre-Plan request that authorizes an orchestration run.

    Persisted *before* any Plan exists. ``fallback_preset_id`` is an explicit
    field of this trusted record (never inferred from a Plan that may fail to
    generate). ``approval_policy`` defaults to ``REQUIRED``. The
    ``optimize_existing`` workflow requires the three existing-candidate refs
    together; every other workflow forbids them. Decision-schedule detail is a
    Phase 6 consumer contract, so Phase 1 carries at most a typed
    ``decision_schedule_ref``, never three untyped id/version/digest strings.
    """

    schema_version: Literal["1"] = "1"
    request_id: NonEmptyStr
    goal: NonEmptyStr
    workflow: Workflow
    fallback_preset_id: LogicalId | None = None
    approval_policy: ApprovalPolicy = ApprovalPolicy.REQUIRED
    existing_candidate_artifact_id: NonEmptyStr | None = None
    existing_candidate_hash: DigestHex | None = None
    existing_context_snapshot_id: NonEmptyStr | None = None
    decision_schedule_ref: ContentRef | None = None

    @model_validator(mode="after")
    def _workflow_matrix(self) -> "OrchestrationRequest":
        existing = (
            self.existing_candidate_artifact_id,
            self.existing_candidate_hash,
            self.existing_context_snapshot_id,
        )
        if self.workflow == "optimize_existing":
            if any(x is None for x in existing):
                raise ValueError(
                    "workflow='optimize_existing' requires existing candidate "
                    "artifact id, hash and context-snapshot id together"
                )
        else:
            if any(x is not None for x in existing):
                raise ValueError(
                    f"workflow={self.workflow!r} must not carry existing-candidate refs"
                )
        return self


# --------------------------------------------------------------------------- #
# Dependency + PlanNode                                                        #
# --------------------------------------------------------------------------- #
class Dependency(DigestModel):
    """The complete v1 edge ABI between two plan nodes.

    ``artifact_slot`` must equal the upstream node's ``writes_slot`` and
    ``inject_as`` must name one downstream :class:`InputBinding`. A ``BLOCK``
    dependency accepts *exactly* ``{COMPLETED}``; ``accept_statuses`` may never
    include a non-success status (``FAILED`` / ``INCOMPLETE`` / ``BLOCKED`` / …).
    """

    schema_version: Literal["1"] = "1"
    upstream_node_id: LogicalId
    artifact_slot: LogicalId
    upstream_output_key: LogicalId = "primary"
    inject_as: LogicalId
    policy: DependencyPolicy = DependencyPolicy.BLOCK
    accept_statuses: frozenset[NodeStatus] = Field(
        default_factory=lambda: frozenset({NodeStatus.COMPLETED})
    )

    @model_validator(mode="after")
    def _accept_matrix(self) -> "Dependency":
        if not self.accept_statuses:
            raise ValueError("accept_statuses must be non-empty")
        bad = set(self.accept_statuses) - _SUCCESS_STATUSES
        if bad:
            raise ValueError(
                "accept_statuses may only contain success statuses; got "
                + ", ".join(sorted(s.value for s in bad))
            )
        if self.policy is DependencyPolicy.BLOCK and set(self.accept_statuses) != {
            NodeStatus.COMPLETED
        }:
            raise ValueError("a BLOCK dependency must accept exactly {COMPLETED}")
        return self


class PlanNode(DigestModel):
    """One Plan-instance node: a catalog ``worker_id`` + typed params + graph meta.

    Carries **no** prompt/skill/tool/MCP/callable/path override field — a node can
    only *select* a catalog worker and *configure* it through ``params`` (validated
    against that worker's ``params_schema_ref``) and dependency injection. The
    debate identity fields are all-set or all-none; ``condition_ref`` is a
    catalog-owned :class:`ContentRef`, never expression text.
    """

    schema_version: Literal["1"] = "1"
    id: LogicalId
    worker_id: LogicalId
    params: dict[str, Any] = Field(default_factory=dict)
    dependencies: tuple[Dependency, ...] = ()
    writes_slot: LogicalId
    gate_ids: tuple[LogicalId, ...] = ()
    debate_id: LogicalId | None = None
    round_role: LogicalId | None = None
    debate_round: PositiveInt | None = None
    debate_turn: PositiveInt | None = None
    condition_ref: ContentRef | None = None
    auxiliary: bool = False
    timeout_sec: PositiveInt = 300
    max_attempts: PositiveInt = 1
    token_reservation: NonNegativeInt = 0

    @field_validator("params")
    @classmethod
    def _params_json_shaped(cls, v: dict[str, Any]) -> dict[str, Any]:
        return _ensure_json_shaped(v)

    @model_validator(mode="after")
    def _local(self) -> "PlanNode":
        debate_fields = (
            self.debate_id,
            self.round_role,
            self.debate_round,
            self.debate_turn,
        )
        some = any(x is not None for x in debate_fields)
        every = all(x is not None for x in debate_fields)
        if some and not every:
            raise ValueError(
                "debate identity fields (debate_id/round_role/debate_round/"
                "debate_turn) are all-set or all-none"
            )
        if len(set(self.gate_ids)) != len(self.gate_ids):
            raise ValueError("gate_ids must be unique")
        return self


# --------------------------------------------------------------------------- #
# Gate / Debate / Reducer config                                              #
# --------------------------------------------------------------------------- #
class GateCfg(DigestModel):
    """A plan-registered honesty gate over a catalog-owned metric ref."""

    schema_version: Literal["1"] = "1"
    id: LogicalId
    metric: ContentRef
    operator: GateOperator
    threshold: FiniteFloat | NonEmptyStr
    scope: NonEmptyStr
    blocking: bool = True
    unavailable_policy: UnavailablePolicy = "fail"
    min_samples: PositiveInt | None = None


class GateResult(DigestModel):
    """The typed outcome of evaluating one :class:`GateCfg` at runtime."""

    schema_version: Literal["1"] = "1"
    gate_id: LogicalId
    metric: ContentRef
    status: GateStatus
    observed: FiniteFloat | NonEmptyStr | None = None
    threshold: FiniteFloat | NonEmptyStr
    blocking: bool
    reason: NonEmptyStr
    metrics_artifact_id: NonEmptyStr


class DebateCfg(DigestModel):
    """A bounded debate: seats, a turn order that permutes them, and a judge node."""

    schema_version: Literal["1"] = "1"
    id: LogicalId
    seats: tuple[LogicalId, ...]
    turn_order: tuple[LogicalId, ...]
    max_rounds: PositiveInt
    judge_node_id: LogicalId

    @model_validator(mode="after")
    def _coherent(self) -> "DebateCfg":
        if not self.seats:
            raise ValueError("debate seats must be non-empty")
        if len(set(self.seats)) != len(self.seats):
            raise ValueError("debate seats must be unique")
        if not self.turn_order:
            raise ValueError("debate turn_order must be non-empty")
        if len(set(self.turn_order)) != len(self.turn_order):
            raise ValueError("debate turn_order must be duplicate-free")
        if set(self.turn_order) != set(self.seats):
            raise ValueError("debate turn_order must be a permutation of seats")
        return self


class ReducerCfg(DigestModel):
    """A deterministic multi-writer reducer bound to one slot + catalog reducer ref."""

    schema_version: Literal["1"] = "1"
    id: LogicalId
    slot: LogicalId
    reducer_ref: ContentRef
    producer_node_ids: tuple[LogicalId, ...]
    output_schema_ref: SchemaRef

    @model_validator(mode="after")
    def _coherent(self) -> "ReducerCfg":
        if not self.producer_node_ids:
            raise ValueError("reducer producer_node_ids must be non-empty")
        if len(set(self.producer_node_ids)) != len(self.producer_node_ids):
            raise ValueError("reducer producer_node_ids must be unique")
        return self


# --------------------------------------------------------------------------- #
# PlanValidationIssue                                                          #
# --------------------------------------------------------------------------- #
class PlanValidationIssue(DigestModel):
    """One canonical, machine-coded validation finding."""

    schema_version: Literal["1"] = "1"
    code: NonEmptyStr
    message: NonEmptyStr
    node_id: LogicalId | None = None
    pointer: NonEmptyStr | None = None

    @property
    def sort_key(self) -> tuple[str, str, str, str]:
        return (self.code, self.node_id or "", self.pointer or "", self.message)


def _issue(code: str, message: str, *, node_id: str | None = None, pointer: str | None = None) -> PlanValidationIssue:
    return PlanValidationIssue(code=code, message=message, node_id=node_id, pointer=pointer)


def _sorted_issues(issues) -> tuple[PlanValidationIssue, ...]:
    return tuple(sorted(issues, key=lambda i: i.sort_key))


# --------------------------------------------------------------------------- #
# Pure, catalog-free structural validation                                    #
# --------------------------------------------------------------------------- #
def _structural_issues(draft: "PlanDraft") -> list[PlanValidationIssue]:
    issues: list[PlanValidationIssue] = []
    nodes = draft.nodes
    node_ids = [n.id for n in nodes]
    node_by_id: dict[str, PlanNode] = {}
    for n in nodes:
        if n.id in node_by_id:
            issues.append(_issue("duplicate_node_id", f"duplicate node id {n.id!r}", node_id=n.id))
        else:
            node_by_id[n.id] = n

    # -- phase / context presence ----------------------------------------- #
    if draft.phase == "bootstrap":
        if draft.context_snapshot_ref is not None:
            issues.append(_issue("bootstrap_has_context_ref", "bootstrap plan must not carry a context_snapshot_ref"))
    else:  # main
        ref = draft.context_snapshot_ref
        if ref is None:
            issues.append(_issue("main_missing_context_ref", "main plan requires a context_snapshot_ref"))
        elif ref.namespace != "main":
            issues.append(_issue("context_ref_namespace", "context_snapshot_ref must use namespace='main'"))

    # -- legacy compatibility tuple: all-set or all-none ------------------ #
    legacy = (draft.legacy_source_schema, draft.legacy_source_config_digest, draft.legacy_mapping_digest)
    if any(x is not None for x in legacy) and any(x is None for x in legacy):
        issues.append(_issue("legacy_tuple_incomplete", "legacy source/config/mapping tuple is all-set or all-none"))

    # -- sinks ------------------------------------------------------------- #
    sinks = draft.sink_node_ids
    if not sinks:
        issues.append(_issue("no_sink", "a plan requires at least one sink node"))
    if len(set(sinks)) != len(sinks):
        issues.append(_issue("duplicate_sink", "sink_node_ids must be duplicate-free"))
    for sid in sinks:
        if sid not in node_by_id:
            issues.append(_issue("missing_sink", f"sink id {sid!r} does not reference a node", node_id=sid))

    # -- dependency refs + slot binding ----------------------------------- #
    for n in nodes:
        for dep in n.dependencies:
            up = node_by_id.get(dep.upstream_node_id)
            if up is None:
                issues.append(_issue(
                    "dependency_missing_node",
                    f"node {n.id!r} depends on missing node {dep.upstream_node_id!r}",
                    node_id=n.id,
                ))
                continue
            if dep.artifact_slot != up.writes_slot:
                issues.append(_issue(
                    "artifact_slot_mismatch",
                    f"node {n.id!r} dependency artifact_slot {dep.artifact_slot!r} != "
                    f"upstream {up.id!r} writes_slot {up.writes_slot!r}",
                    node_id=n.id,
                ))

    # -- cycles (Kahn over dependency edges upstream->node) --------------- #
    succ: dict[str, list[str]] = {nid: [] for nid in node_by_id}
    indeg: dict[str, int] = {nid: 0 for nid in node_by_id}
    for n in nodes:
        preds = {dep.upstream_node_id for dep in n.dependencies if dep.upstream_node_id in node_by_id}
        indeg[n.id] = len(preds)
        for p in preds:
            succ[p].append(n.id)
    queue = [nid for nid, d in indeg.items() if d == 0]
    processed = 0
    indeg_work = dict(indeg)
    while queue:
        cur = queue.pop()
        processed += 1
        for nxt in succ.get(cur, ()):
            indeg_work[nxt] -= 1
            if indeg_work[nxt] == 0:
                queue.append(nxt)
    has_cycle = processed != len(node_by_id)
    if has_cycle:
        issues.append(_issue("cycle", "plan dependency graph contains a cycle"))

    # -- reachability: non-auxiliary nodes must reach a sink -------------- #
    if not has_cycle:
        reachable: set[str] = set()
        stack = [sid for sid in sinks if sid in node_by_id]
        while stack:
            cur = stack.pop()
            if cur in reachable:
                continue
            reachable.add(cur)
            for dep in node_by_id[cur].dependencies:
                if dep.upstream_node_id in node_by_id:
                    stack.append(dep.upstream_node_id)
        for n in nodes:
            if n.id not in reachable and not n.auxiliary:
                issues.append(_issue(
                    "unreachable_node",
                    f"non-auxiliary node {n.id!r} cannot reach any sink",
                    node_id=n.id,
                ))

    # -- multi-writer slots + reducers ------------------------------------ #
    slot_writers: dict[str, list[str]] = {}
    for n in nodes:
        slot_writers.setdefault(n.writes_slot, []).append(n.id)
    reducer_by_slot: dict[str, ReducerCfg] = {}
    reducer_ids: set[str] = set()
    for red in draft.reducers:
        if red.id in reducer_ids:
            issues.append(_issue("duplicate_reducer_id", f"duplicate reducer id {red.id!r}"))
        reducer_ids.add(red.id)
        if red.slot in reducer_by_slot:
            issues.append(_issue("duplicate_reducer_slot", f"slot {red.slot!r} has more than one reducer"))
        reducer_by_slot[red.slot] = red
        writers = set(slot_writers.get(red.slot, ()))
        if not writers:
            issues.append(_issue("reducer_slot_unwritten", f"reducer {red.id!r} slot {red.slot!r} is written by no node"))
        missing_producers = [p for p in red.producer_node_ids if p not in node_by_id]
        for p in missing_producers:
            issues.append(_issue("reducer_missing_producer", f"reducer {red.id!r} names missing producer {p!r}"))
        producers = set(red.producer_node_ids)
        wrong_slot = [p for p in red.producer_node_ids if p in node_by_id and node_by_id[p].writes_slot != red.slot]
        for p in wrong_slot:
            issues.append(_issue("reducer_producer_wrong_slot", f"reducer {red.id!r} producer {p!r} does not write slot {red.slot!r}"))
        if writers and producers != writers:
            issues.append(_issue(
                "reducer_producers_incoherent",
                f"reducer {red.id!r} producers {sorted(producers)} != slot writers {sorted(writers)}",
            ))
    for slot, writers in slot_writers.items():
        if len(writers) > 1 and slot not in reducer_by_slot:
            issues.append(_issue(
                "unreduced_multi_write",
                f"slot {slot!r} is written by {sorted(writers)} without a deterministic reducer",
            ))

    # -- gate id references ----------------------------------------------- #
    gate_ids = {g.id for g in draft.gates}
    if len(gate_ids) != len(draft.gates):
        issues.append(_issue("duplicate_gate_id", "gate ids must be unique"))
    for n in nodes:
        for gid in n.gate_ids:
            if gid not in gate_ids:
                issues.append(_issue("unknown_gate_id", f"node {n.id!r} references undeclared gate {gid!r}", node_id=n.id))

    # -- debate coherence -------------------------------------------------- #
    debate_by_id: dict[str, DebateCfg] = {}
    for deb in draft.debates:
        if deb.id in debate_by_id:
            issues.append(_issue("duplicate_debate_id", f"duplicate debate id {deb.id!r}"))
        debate_by_id[deb.id] = deb
        if deb.judge_node_id not in node_by_id:
            issues.append(_issue("debate_missing_judge", f"debate {deb.id!r} judge {deb.judge_node_id!r} is not a node"))
    seen_turn: set[tuple[str, int, int]] = set()
    seen_role: set[tuple[str, int, str]] = set()
    for n in nodes:
        if n.debate_id is None:
            continue
        deb = debate_by_id.get(n.debate_id)
        if deb is None:
            issues.append(_issue("undefined_debate", f"node {n.id!r} references undefined debate {n.debate_id!r}", node_id=n.id))
            continue
        if n.round_role not in set(deb.seats):
            issues.append(_issue("debate_role_not_seat", f"node {n.id!r} role {n.round_role!r} is not a debate seat", node_id=n.id))
        if not (1 <= (n.debate_round or 0) <= deb.max_rounds):
            issues.append(_issue("debate_round_out_of_range", f"node {n.id!r} debate_round {n.debate_round} exceeds max_rounds {deb.max_rounds}", node_id=n.id))
        turn_key = (n.debate_id, n.debate_round or 0, n.debate_turn or 0)
        if turn_key in seen_turn:
            issues.append(_issue("duplicate_debate_turn", f"duplicate debate turn {turn_key}", node_id=n.id))
        seen_turn.add(turn_key)
        role_key = (n.debate_id, n.debate_round or 0, n.round_role or "")
        if role_key in seen_role:
            issues.append(_issue("duplicate_debate_role", f"role {n.round_role!r} appears twice in round {n.debate_round}", node_id=n.id))
        seen_role.add(role_key)

    return issues


def validate_plan_structure(draft: "PlanDraft") -> None:
    """Raise :class:`PlanStructureError` on any pure, catalog-free graph violation.

    Pure over the draft alone (no catalog / registry / context). Also invoked by
    the :class:`PlanDraft` model validator so a structurally-invalid draft cannot
    be *constructed*; a draft assembled via ``model_construct`` / ``model_copy``
    (which bypass validation) is still caught here.
    """
    issues = _structural_issues(draft)
    if issues:
        raise PlanStructureError("; ".join(i.message for i in _sorted_issues(issues)))


# --------------------------------------------------------------------------- #
# PlanDraft                                                                    #
# --------------------------------------------------------------------------- #
class PlanDraft(DigestModel):
    """A Planner/preset candidate plan — immutable, not yet executable.

    Carries the exact ``catalog_digest`` / ``schema_registry_digest`` /
    ``context_snapshot_ref`` and budget request selected by the trusted runtime,
    plus the all-set/all-none legacy compatibility tuple. Validation compares each
    against supplied immutable evidence rather than trusting Planner authority.
    """

    # schema_version pinned to "2" (siblings default to "1") per the plan's
    # Global Constraints: PlanDraft and Artifact are the two contracts on "2".
    schema_version: Literal["2"] = "2"
    id: LogicalId
    run_id: NonEmptyStr
    request_id: NonEmptyStr
    phase: PlanPhase
    source: PlanSource
    goal: NonEmptyStr
    as_of: UtcDateTime
    mode: DataMode
    context_snapshot_ref: PayloadRef | None = None
    universe: tuple[Symbol, ...] = ()
    nodes: tuple[PlanNode, ...]
    sink_node_ids: tuple[LogicalId, ...]
    debates: tuple[DebateCfg, ...] = ()
    gates: tuple[GateCfg, ...] = ()
    reducers: tuple[ReducerCfg, ...] = ()
    catalog_version: NonEmptyStr
    catalog_digest: DigestHex
    schema_registry_digest: DigestHex
    approval_policy: ApprovalPolicy = ApprovalPolicy.REQUIRED
    budget_request_tokens: NonNegativeInt = 0
    budget_request_llm_invocations: NonNegativeInt = 0
    max_concurrency: PositiveInt = 4
    stop_condition_refs: tuple[ContentRef, ...] = ()
    legacy_source_schema: SchemaRef | None = None
    legacy_source_config_digest: DigestHex | None = None
    legacy_mapping_digest: DigestHex | None = None

    @model_validator(mode="after")
    def _structure(self) -> "PlanDraft":
        validate_plan_structure(self)
        return self

    def executable_projection(self) -> dict[str, Any]:
        """The exact field subset the single candidate digest binds."""
        return {name: getattr(self, name) for name in _EXECUTABLE_FIELDS}


# --------------------------------------------------------------------------- #
# Static legacy attestation (CONSUMED here; BUILT in Task 12)                  #
# --------------------------------------------------------------------------- #
class StaticLegacyPlanAttestation(DigestModel):
    """Service-owned evidence that a preset/preset-fallback plan mirrors one
    reviewed legacy graph. It is *not* a Planner field and grants no AUTO."""

    schema_version: Literal["1"] = "1"
    attestation_version: Literal["static-legacy-v1"] = "static-legacy-v1"
    plan_source: Literal["preset", "preset_fallback"]
    request_digest: DigestHex
    candidate_plan_digest: DigestHex
    catalog_digest: DigestHex
    legacy_source_schema: SchemaRef
    source_config_digest: DigestHex
    legacy_mapping_digest: DigestHex
    builder_id: LogicalId


# --------------------------------------------------------------------------- #
# PlanValidationReport                                                         #
# --------------------------------------------------------------------------- #
class PlanValidationReport(DigestModel):
    """The frozen result of :func:`validate_plan_draft`.

    Binds the exact request/context/catalog/registry/attestation digests it
    validated. ``valid`` is true iff ``issues`` is empty; issues are canonically
    ordered so two runs over identical inputs produce an *equal* report.
    """

    schema_version: Literal["1"] = "1"
    validator_version: Literal["plan-validator-v1"] = "plan-validator-v1"
    valid: bool
    issues: tuple[PlanValidationIssue, ...] = ()
    candidate_plan_digest: DigestHex
    request_digest: DigestHex
    context_content_digest: DigestHex | None = None
    catalog_digest: DigestHex
    schema_registry_digest: DigestHex
    legacy_attestation_digest: DigestHex | None = None

    @model_validator(mode="after")
    def _coherent(self) -> "PlanValidationReport":
        if self.valid and self.issues:
            raise ValueError("a valid report must carry no issues")
        if not self.valid and not self.issues:
            raise ValueError("an invalid report must carry at least one issue")
        ordered = _sorted_issues(self.issues)
        if tuple(self.issues) != ordered:
            raise ValueError("report issues must be canonically ordered")
        return self


# --------------------------------------------------------------------------- #
# The single candidate-plan digest                                            #
# --------------------------------------------------------------------------- #
def _candidate_digest_from_parts(
    *, request_digest: str, context_content_digest: str | None, executable: dict[str, Any]
) -> DigestHex:
    return content_digest(
        {
            "domain": CANDIDATE_PLAN_DOMAIN,
            "request_digest": request_digest,
            "context_content_digest": context_content_digest,
            "draft": executable,
        }
    )


def compute_candidate_plan_digest(
    *,
    request: OrchestrationRequest,
    draft: PlanDraft,
    context_content_digest: DigestHex | None,
) -> DigestHex:
    """The one canonical ``candidate-plan-v1`` digest.

    Binds the request semantic digest, every executable draft field (incl. legacy
    source/config/mapping digests, catalog/registry digests and the exact budget
    request) and the ContextSnapshot **content** digest. Excludes its own value,
    approval/attestation records, reservation id and freeze wall-clock. Validator,
    freeze and Task 12's attestation builder all call this identical function.
    """
    return _candidate_digest_from_parts(
        request_digest=request.semantic_digest(),
        context_content_digest=context_content_digest,
        executable=draft.executable_projection(),
    )


# --------------------------------------------------------------------------- #
# Catalog resolution helpers                                                   #
# --------------------------------------------------------------------------- #
def _content_kind_index(catalog: WorkerCatalogSnapshot) -> dict[tuple[str, str], str]:
    """(id, version) -> declared content-manifest kind + its digest, for ref checks."""
    return {(e.ref.id, e.ref.version): e for e in catalog.content_manifest}


def _resolve_content_ref(
    ref: ContentRef,
    *,
    expected_kind: str,
    index,
    issues: list[PlanValidationIssue],
    unknown_code: str,
    wrong_kind_code: str,
    node_id: str | None = None,
) -> None:
    entry = index.get((ref.id, ref.version))
    if entry is None:
        issues.append(_issue(unknown_code, f"unknown {expected_kind} ref {ref.id}@{ref.version}", node_id=node_id))
        return
    if entry.kind != expected_kind:
        issues.append(_issue(
            wrong_kind_code,
            f"ref {ref.id}@{ref.version} is kind={entry.kind!r}, expected {expected_kind!r}",
            node_id=node_id,
        ))
        return
    if entry.ref.content_digest != ref.content_digest:
        issues.append(_issue(
            wrong_kind_code,
            f"declared digest mismatch for {expected_kind} ref {ref.id}@{ref.version}",
            node_id=node_id,
        ))


def _worker_primary_output(worker: WorkerSpec):
    for o in worker.outputs:
        if o.name == "primary":
            return o
    return None


# --------------------------------------------------------------------------- #
# validate_plan_draft                                                          #
# --------------------------------------------------------------------------- #
def validate_plan_draft(
    draft: PlanDraft,
    *,
    request: OrchestrationRequest,
    context: ContextSnapshot | None,
    catalog: WorkerCatalogSnapshot,
    schema_registry: SchemaRegistry,
    legacy_attestation: StaticLegacyPlanAttestation | None = None,
) -> PlanValidationReport:
    """Pure, I/O-free contract validation of a candidate plan against sealed inputs.

    Reads only the supplied immutable snapshots. Computes the single
    :func:`compute_candidate_plan_digest`, then accumulates every rejection into a
    frozen :class:`PlanValidationReport` bound to the exact digests it validated.
    Performs no file/catalog load, ledger mutation, scheduling, model/tool call or
    approval.
    """
    request_digest = request.semantic_digest()
    context_content_digest = context.content_digest if context is not None else None
    candidate = compute_candidate_plan_digest(
        request=request, draft=draft, context_content_digest=context_content_digest
    )
    catalog_digest = catalog.catalog_digest
    schema_registry_digest = schema_registry.registry_digest
    legacy_attestation_digest = (
        legacy_attestation.semantic_digest() if legacy_attestation is not None else None
    )

    issues: list[PlanValidationIssue] = []

    # -- structural (defensive: re-run the pure graph checks) ------------- #
    issues.extend(_structural_issues(draft))

    # -- request / draft consistency + approval policy -------------------- #
    if draft.request_id != request.request_id:
        issues.append(_issue("request_id_mismatch", "draft.request_id does not match the request"))
    if draft.approval_policy is ApprovalPolicy.AUTO:
        issues.append(_issue("auto_approval_rejected", "ApprovalPolicy.AUTO is rejected for every source in Phase 1"))
    if draft.source is PlanSource.DYNAMIC:
        if draft.approval_policy != request.approval_policy:
            issues.append(_issue("dynamic_approval_policy_mismatch", "a DYNAMIC draft must copy approval_policy from the request"))
        if draft.approval_policy is not ApprovalPolicy.REQUIRED:
            issues.append(_issue("dynamic_approval_policy_not_required", "a DYNAMIC draft's approval_policy must be REQUIRED"))

    # -- catalog / registry / version bindings ---------------------------- #
    if draft.catalog_digest != catalog_digest:
        issues.append(_issue("catalog_digest_mismatch", "draft.catalog_digest != supplied catalog digest"))
    if draft.schema_registry_digest != schema_registry_digest:
        issues.append(_issue("schema_registry_digest_mismatch", "draft.schema_registry_digest != supplied registry digest"))
    if draft.catalog_version != catalog.catalog_version:
        issues.append(_issue("catalog_version_mismatch", "draft.catalog_version != supplied catalog version"))

    # -- catalog self-consistency (pure re-check, no I/O) ----------------- #
    try:
        validate_catalog_snapshot(catalog)
    except CatalogError as exc:  # pragma: no cover - defensive
        issues.append(_issue("catalog_invalid", f"supplied catalog is not self-consistent: {exc}"))

    # -- context / phase matrix ------------------------------------------- #
    if draft.phase == "bootstrap":
        if context is not None:
            issues.append(_issue("bootstrap_context_present", "bootstrap validation must be given context=None"))
    else:  # main
        if context is None:
            issues.append(_issue("main_context_missing", "main plan validation requires a ContextSnapshot"))
        else:
            ref = draft.context_snapshot_ref
            if ref is None:
                issues.append(_issue("main_context_ref_missing", "main plan requires a context_snapshot_ref"))
            elif ref.content_digest != context.content_digest or ref.namespace != "main":
                issues.append(_issue("context_mismatch", "context_snapshot_ref does not bind the supplied ContextSnapshot content"))

    worker_by_id = {w.id: w for w in catalog.workers}
    node_by_id = {n.id: n for n in draft.nodes}
    sinks = set(draft.sink_node_ids)
    content_index = _content_kind_index(catalog)

    # -- compatibility attestation gate (one attested legacy graph) ------- #
    def _attestation_matches() -> bool:
        if legacy_attestation is None:
            return False
        return (
            legacy_attestation.plan_source == draft.source.value
            and legacy_attestation.request_digest == request_digest
            and legacy_attestation.candidate_plan_digest == candidate
            and legacy_attestation.catalog_digest == catalog_digest
            and legacy_attestation.legacy_source_schema == draft.legacy_source_schema
            and legacy_attestation.source_config_digest == draft.legacy_source_config_digest
            and legacy_attestation.legacy_mapping_digest == draft.legacy_mapping_digest
        )

    # -- per-node catalog checks ------------------------------------------ #
    for node in draft.nodes:
        worker = worker_by_id.get(node.worker_id)
        if worker is None:
            issues.append(_issue("unknown_worker", f"node {node.id!r} references unknown worker {node.worker_id!r}", node_id=node.id))
            continue

        # compatibility-worker source gating
        if worker.catalog_role == "compatibility":
            if draft.source in (PlanSource.BOOTSTRAP, PlanSource.DYNAMIC):
                issues.append(_issue("compat_worker_forbidden_source", f"compatibility worker {worker.id!r} is not selectable under source={draft.source.value!r}", node_id=node.id))
            else:  # preset / preset_fallback
                if legacy_attestation is None:
                    issues.append(_issue("compat_attestation_required", f"compatibility worker {worker.id!r} under {draft.source.value!r} requires a StaticLegacyPlanAttestation", node_id=node.id))
                elif not _attestation_matches():
                    issues.append(_issue("compat_attestation_mismatch", "the supplied attestation does not match this request/candidate/catalog/config/mapping", node_id=node.id))
                elif worker.compatibility is not None and (
                    worker.compatibility.legacy_source_schema != legacy_attestation.legacy_source_schema
                    or worker.compatibility.source_config_digest != legacy_attestation.source_config_digest
                    or worker.compatibility.legacy_mapping_digest != legacy_attestation.legacy_mapping_digest
                ):
                    issues.append(_issue("compat_attestation_mismatch", f"compat binding of {worker.id!r} does not match the attested legacy graph", node_id=node.id))

        # supported mode
        if draft.mode not in worker.supported_modes:
            issues.append(_issue("unsupported_mode", f"worker {worker.id!r} does not support mode {draft.mode.value!r}", node_id=node.id))

        # params vs the worker's strict params schema
        if worker.params_schema_ref is None:
            if node.params:
                issues.append(_issue("params_not_allowed", f"worker {worker.id!r} declares no params schema but node has params", node_id=node.id))
        else:
            try:
                schema_registry.validate_payload(worker.params_schema_ref, node.params)
            except SchemaRegistryError as exc:
                issues.append(_issue("unknown_params_schema", f"params schema for {worker.id!r} is unresolvable: {exc}", node_id=node.id))
            except _PydValidationError:
                issues.append(_issue("params_schema_violation", f"node {node.id!r} params fail worker {worker.id!r} params schema (extra/missing/typed field)", node_id=node.id))

        # decision-class sink authorization
        if node.id in sinks:
            primary = _worker_primary_output(worker)
            if primary is not None and primary.schema_ref.name in _DECISION_CLASS_SCHEMAS and not worker.can_emit_decision:
                issues.append(_issue("unauthorized_decision_sink", f"sink {node.id!r} emits decision-class {primary.schema_ref.name} but worker cannot emit decisions", node_id=node.id))

        # condition ref
        if node.condition_ref is not None:
            _resolve_content_ref(
                node.condition_ref, expected_kind="condition", index=content_index, issues=issues,
                unknown_code="unknown_condition_ref", wrong_kind_code="wrong_kind_condition_ref", node_id=node.id,
            )

        # dependency injection: schema equality + cardinality + coverage
        input_by_name = {b.name: b for b in worker.inputs}
        injected: dict[str, list[Dependency]] = {}
        for dep in node.dependencies:
            up_node = node_by_id.get(dep.upstream_node_id)
            binding = input_by_name.get(dep.inject_as)
            if binding is None:
                issues.append(_issue("unknown_inject_target", f"node {node.id!r} injects into unknown input {dep.inject_as!r}", node_id=node.id))
                continue
            injected.setdefault(dep.inject_as, []).append(dep)
            if up_node is None:
                continue  # structural issue already recorded
            up_worker = worker_by_id.get(up_node.worker_id)
            if up_worker is None:
                continue  # unknown upstream worker already recorded
            up_out = None
            for o in up_worker.outputs:
                if o.name == dep.upstream_output_key:
                    up_out = o
                    break
            if up_out is None:
                issues.append(_issue("unknown_upstream_output", f"upstream {up_node.id!r} worker {up_worker.id!r} has no output {dep.upstream_output_key!r}", node_id=node.id))
                continue
            if up_out.schema_ref != binding.schema_ref:
                issues.append(_issue("input_schema_mismatch", f"upstream output {up_out.schema_ref.key} != input {dep.inject_as!r} schema {binding.schema_ref.key} (no coercion)", node_id=node.id))
            # a soft dependency must not weaken a required, must-reference,
            # non-degradable input's evidence requirement
            if binding.required and dep.policy is not DependencyPolicy.BLOCK:
                ep = worker.evidence_policy
                if ep.require_input_refs and not ep.optional_data_may_degrade:
                    issues.append(_issue("dependency_weakens_evidence", f"required input {dep.inject_as!r} of {worker.id!r} cannot be fed by a {dep.policy.value} dependency", node_id=node.id))
        for binding in worker.inputs:
            deps = injected.get(binding.name, [])
            if binding.required and not deps:
                issues.append(_issue("required_input_unsatisfied", f"required input {binding.name!r} of {worker.id!r} has no dependency", node_id=node.id))
            if binding.cardinality == "one" and len(deps) > 1:
                issues.append(_issue("duplicate_single_input", f"cardinality-one input {binding.name!r} of {worker.id!r} has {len(deps)} dependencies", node_id=node.id))

    # -- gate metrics ------------------------------------------------------ #
    for gate in draft.gates:
        _resolve_content_ref(
            gate.metric, expected_kind="gate_metric", index=content_index, issues=issues,
            unknown_code="unknown_gate_metric_ref", wrong_kind_code="wrong_kind_gate_metric_ref",
        )

    # -- reducers ---------------------------------------------------------- #
    for red in draft.reducers:
        _resolve_content_ref(
            red.reducer_ref, expected_kind="reducer", index=content_index, issues=issues,
            unknown_code="unknown_reducer_ref", wrong_kind_code="wrong_kind_reducer_ref",
        )
        try:
            schema_registry.resolve(red.output_schema_ref)
        except SchemaRegistryError:
            issues.append(_issue("unknown_reducer_output_schema", f"reducer {red.id!r} output schema {red.output_schema_ref.key} is not registered"))

    # -- stop conditions --------------------------------------------------- #
    for ref in draft.stop_condition_refs:
        _resolve_content_ref(
            ref, expected_kind="stop_condition", index=content_index, issues=issues,
            unknown_code="unknown_stop_condition_ref", wrong_kind_code="wrong_kind_stop_condition_ref",
        )

    ordered = _sorted_issues(issues)
    return PlanValidationReport(
        valid=(len(ordered) == 0),
        issues=ordered,
        candidate_plan_digest=candidate,
        request_digest=request_digest,
        context_content_digest=context_content_digest,
        catalog_digest=catalog_digest,
        schema_registry_digest=schema_registry_digest,
        legacy_attestation_digest=legacy_attestation_digest,
    )


# --------------------------------------------------------------------------- #
# Frozen Plan + freeze builder                                                #
# --------------------------------------------------------------------------- #
class Plan(DigestModel):
    """The single executable plan type — frozen after a validated, approved freeze.

    Embeds the immutable executable :class:`PlanDraft` plus the freeze bindings.
    ``plan_digest`` **equals** the pre-freeze ``candidate_plan_digest`` and is
    recomputed from the embedded draft + ``request_digest`` + context content on
    load; a declared mismatch is rejected. ``budget_reservation_id`` and
    ``frozen_at`` are audit facts excluded from the candidate digest, so they
    (and the approval/attestation) can bind the same digest without a cycle.
    """

    schema_version: Literal["1"] = "1"
    plan_id: LogicalId
    run_id: NonEmptyStr
    request_id: NonEmptyStr
    request_digest: DigestHex
    draft: PlanDraft
    context_content_digest: DigestHex | None = None
    legacy_attestation_digest: DigestHex | None = None
    budget_reservation_id: NonEmptyStr
    frozen_at: UtcDateTime
    plan_digest: DigestHex

    SEMANTIC_EXCLUDE: ClassVar[frozenset[str]] = frozenset(
        {"budget_reservation_id", "frozen_at"}
    )
    SELF_DIGEST_FIELDS: ClassVar[frozenset[str]] = frozenset({"plan_digest"})

    # -- exposed digest-recomputation bindings ---------------------------- #
    @property
    def phase(self) -> PlanPhase:
        return self.draft.phase

    @property
    def source(self) -> PlanSource:
        return self.draft.source

    @property
    def mode(self) -> DataMode:
        return self.draft.mode

    @property
    def context_snapshot_ref(self) -> PayloadRef | None:
        return self.draft.context_snapshot_ref

    @property
    def catalog_version(self) -> str:
        return self.draft.catalog_version

    @property
    def catalog_digest(self) -> DigestHex:
        return self.draft.catalog_digest

    @property
    def schema_registry_digest(self) -> DigestHex:
        return self.draft.schema_registry_digest

    @property
    def budget_request_tokens(self) -> int:
        return self.draft.budget_request_tokens

    @property
    def budget_request_llm_invocations(self) -> int:
        return self.draft.budget_request_llm_invocations

    @property
    def max_concurrency(self) -> int:
        return self.draft.max_concurrency

    @property
    def nodes(self):
        return self.draft.nodes

    @property
    def sink_node_ids(self):
        return self.draft.sink_node_ids

    def recompute_plan_digest(self) -> DigestHex:
        return _candidate_digest_from_parts(
            request_digest=self.request_digest,
            context_content_digest=self.context_content_digest,
            executable=self.draft.executable_projection(),
        )

    @model_validator(mode="after")
    def _verify(self) -> "Plan":
        if self.plan_id != self.draft.id:
            raise ValueError("Plan.plan_id must equal the embedded draft id")
        if self.run_id != self.draft.run_id:
            raise ValueError("Plan.run_id must equal the embedded draft run_id")
        if self.request_id != self.draft.request_id:
            raise ValueError("Plan.request_id must equal the embedded draft request_id")
        if self.draft.phase == "main":
            ref = self.draft.context_snapshot_ref
            if ref is None or self.context_content_digest is None:
                raise ValueError("a main Plan requires a bound context content digest")
            if ref.content_digest != self.context_content_digest:
                raise ValueError("context_snapshot_ref content digest != context_content_digest")
        else:  # bootstrap
            if self.context_content_digest is not None:
                raise ValueError("a bootstrap Plan must not carry a context content digest")
        if self.plan_digest != self.recompute_plan_digest():
            raise ValueError("declared plan_digest does not match the recomputed candidate digest")
        return self


def freeze_plan(
    draft: PlanDraft,
    *,
    request: OrchestrationRequest,
    context: ContextSnapshot | None,
    catalog: WorkerCatalogSnapshot,
    schema_registry: SchemaRegistry,
    legacy_attestation: StaticLegacyPlanAttestation | None,
    report: PlanValidationReport,
    reservation: BudgetReservation,
    approval: PlanApproval,
    frozen_at: UtcDateTime | None = None,
) -> Plan:
    """Freeze a validated, approved, reserved draft into an executable :class:`Plan`.

    Re-runs :func:`validate_plan_draft` from the immutable inputs and requires the
    supplied ``report`` to *equal* the recomputed result — a caller-constructed
    ``valid=True`` report (or a report bound to different inputs) is never
    authority. The report, ``reservation`` and an ``APPROVED`` ``approval`` must
    all bind the same request/candidate digest; a missing/rejected/mismatched
    approval fails, and Phase 1 ``AUTO`` always fails (an AUTO draft is invalid,
    so the recomputed report is not ``valid``). This pure builder verifies the
    supplied immutable records; it never allocates budget or emits approval.
    """
    recomputed = validate_plan_draft(
        draft,
        request=request,
        context=context,
        catalog=catalog,
        schema_registry=schema_registry,
        legacy_attestation=legacy_attestation,
    )
    if report != recomputed:
        raise PlanFreezeError(
            "supplied validation report does not match the recomputed result "
            "(forged flag or report bound to different inputs)"
        )
    if not recomputed.valid:
        raise PlanFreezeError(
            "draft failed validation: "
            + "; ".join(i.message for i in recomputed.issues)
        )

    candidate = recomputed.candidate_plan_digest

    # approval must be APPROVED and bind this exact request + candidate digest
    if not approval.authorizes_freeze(
        request_id=request.request_id, candidate_plan_digest=candidate
    ):
        raise PlanFreezeError(
            "no APPROVED PlanApproval binds this request and candidate plan digest"
        )

    # reservation must bind the same request + candidate digest
    if reservation.request_id != request.request_id:
        raise PlanFreezeError("reservation.request_id does not match the request")
    if reservation.candidate_plan_digest != candidate:
        raise PlanFreezeError("reservation does not bind the validated candidate plan digest")

    ctx_content = context.content_digest if context is not None else None
    return Plan(
        plan_id=draft.id,
        run_id=draft.run_id,
        request_id=request.request_id,
        request_digest=request.semantic_digest(),
        draft=draft,
        context_content_digest=ctx_content,
        legacy_attestation_digest=(
            legacy_attestation.semantic_digest() if legacy_attestation is not None else None
        ),
        budget_reservation_id=reservation.reservation_id,
        frozen_at=frozen_at if frozen_at is not None else datetime.now(timezone.utc),
        plan_digest=candidate,
    )
