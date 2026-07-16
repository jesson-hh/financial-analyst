# -*- coding: utf-8 -*-
"""Phase 2 · Task 5 — the pure static-runtime support checker.

:func:`check_runtime_support` is a **pure, I/O-free** function: it reads only the
supplied immutable snapshots / pre-resolved views and returns a frozen
:class:`~guanlan_v2.orchestration.runtime_contracts.RuntimeSupportReport`. It never
touches a payload / event / budget store (a fake store spy proves this — the
checker takes no store), never reserves budget, and never mutates anything. It
**consumes** the Phase-1 :func:`~guanlan_v2.orchestration.spec.validate_plan_draft`
result rather than re-deriving it: a support report is produced only for a *valid,
exact-input* Phase-1 report, and it can never turn an invalid Plan valid.

"Runtime support is narrower than schema validity": a plan that Phase 1 accepts
may still be rejected here (before any reservation) when it uses a feature outside
the closed :class:`StaticRuntimeProfile` v1 matrix — BOOTSTRAP / Lane-0 execution,
conditions, reducers / multi-writer, debates, gates, stop conditions,
``max_attempts>1``, an unsupported bridge shape, a missing/mismatched
ContextRuntimeRequirements closure, or a REQUIRED / FORBIDDEN tool-call discipline
that the exact active bridge summaries' numeric bounds do not satisfy.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from guanlan_v2.orchestration.catalog_runtime import (
    BridgeCatalogView,
    CatalogMaterialError,
    CatalogRuntime,
)
from guanlan_v2.orchestration.context import verify_context_runtime_requirements
from guanlan_v2.orchestration.digest import content_digest
from guanlan_v2.orchestration.enums import (
    ExecutionKind,
    PlanSource,
    ToolCallRequirement,
)
from guanlan_v2.orchestration.runtime_contracts import (
    BridgeStaticSupportSummary,
    ResolvedContextRuntimeRequirements,
    RuntimeSupportIssue,
    RuntimeSupportReport,
    StaticRuntimeProfile,
)

if TYPE_CHECKING:
    from guanlan_v2.orchestration.catalog import WorkerSpec
    from guanlan_v2.orchestration.context import ContextSnapshot
    from guanlan_v2.orchestration.schema_registry import SchemaRegistry
    from guanlan_v2.orchestration.spec import PlanDraft, PlanNode, PlanValidationReport

__all__ = [
    "check_runtime_support",
    "CHECKER_VERSION",
]

CHECKER_VERSION = "static-runtime-checker-v1"


def _issue(code: str, model_path: str, explanation: str, *, node_id: str | None = None) -> RuntimeSupportIssue:
    return RuntimeSupportIssue(
        code=code, model_path=model_path, explanation=explanation, node_id=node_id
    )


def _ref_key(ref) -> tuple[str, str, str]:
    return (ref.id, ref.version, ref.content_digest)


def check_runtime_support(
    draft: "PlanDraft",
    *,
    phase1_report: "PlanValidationReport",
    context: "ContextSnapshot | None",
    context_requirements: ResolvedContextRuntimeRequirements | None,
    catalog: CatalogRuntime,
    bridge_view: BridgeCatalogView,
    schema_registry: "SchemaRegistry",
    profile: StaticRuntimeProfile,
) -> RuntimeSupportReport:
    """Pure static-runtime support check → a frozen :class:`RuntimeSupportReport`.

    Never dereferences storage. Emits a canonically ordered issue for every
    unsupported / drifted construct (see the module docstring / brief matrix) and
    is ``supported`` iff no issue is emitted. All identity digests it validated are
    bound into the returned report.
    """
    issues: list[RuntimeSupportIssue] = []

    catalog_digest = catalog.catalog_digest
    registry_digest = schema_registry.registry_digest
    profile_digest = profile.profile_digest
    candidate = phase1_report.candidate_plan_digest
    phase1_report_digest = phase1_report.semantic_digest()
    context_content = context.content_digest if context is not None else None

    # -- (1) valid, exact-input Phase-1 report ----------------------------- #
    if not phase1_report.valid:
        issues.append(
            _issue(
                "phase1_report_invalid",
                "phase1_report.valid",
                "runtime support requires a valid Phase-1 validation report; an invalid "
                "plan can never be supported",
            )
        )
    if profile.profile_id != "static-runtime":
        issues.append(
            _issue("profile_mismatch", "profile.profile_id",
                   "the supplied profile is not the static-runtime profile")
        )
    if phase1_report.catalog_digest != catalog_digest:
        issues.append(
            _issue("catalog_digest_mismatch", "phase1_report.catalog_digest",
                   "Phase-1 report catalog digest does not equal the supplied catalog")
        )
    if phase1_report.schema_registry_digest != registry_digest:
        issues.append(
            _issue("schema_registry_digest_mismatch", "phase1_report.schema_registry_digest",
                   "Phase-1 report registry digest does not equal the supplied registry")
        )
    if phase1_report.context_content_digest != context_content:
        issues.append(
            _issue("context_binding_mismatch", "phase1_report.context_content_digest",
                   "Phase-1 report context content digest does not equal the supplied context")
        )
    if draft.catalog_digest != catalog_digest:
        issues.append(
            _issue("draft_catalog_mismatch", "draft.catalog_digest",
                   "draft catalog digest does not equal the supplied catalog")
        )
    if draft.schema_registry_digest != registry_digest:
        issues.append(
            _issue("draft_registry_mismatch", "draft.schema_registry_digest",
                   "draft registry digest does not equal the supplied registry")
        )

    # -- (2) closed support matrix (runtime is narrower than schema) ------- #
    # Affirmative allow-list: a plan source is supported only if it is IN the
    # profile's closed tuple — a future Phase-1 enum value can never reach
    # supported=True without an explicit profile change.
    if draft.source not in profile.supported_plan_sources:
        issues.append(
            _issue(
                "plan_source_unsupported",
                "draft.source",
                f"plan source {draft.source.value!r} is not in the static profile's "
                "supported_plan_sources allow-list",
            )
        )
    if draft.source is PlanSource.BOOTSTRAP or draft.phase == "bootstrap" or context is None:
        issues.append(
            _issue(
                "bootstrap_unsupported",
                "draft.source",
                "BOOTSTRAP / no-ContextSnapshot Lane-0 execution is a Phase-5 runtime "
                "profile, not static-runtime v1",
            )
        )
    if draft.reducers:
        issues.append(_issue("reducers_unsupported", "draft.reducers",
                             "reducers are outside the static-runtime v1 matrix"))
    if draft.gates:
        issues.append(_issue("gates_unsupported", "draft.gates",
                             "gates / gate metrics are outside the static-runtime v1 matrix"))
    if draft.debates:
        issues.append(_issue("debates_unsupported", "draft.debates",
                             "debates are outside the static-runtime v1 matrix"))
    if draft.stop_condition_refs:
        issues.append(_issue("stop_conditions_unsupported", "draft.stop_condition_refs",
                             "stop conditions are outside the static-runtime v1 matrix"))

    # multi-writer: a slot written by more than one node.
    writers: dict[str, list[str]] = {}
    for node in draft.nodes:
        writers.setdefault(node.writes_slot, []).append(node.id)
    for slot, node_ids in writers.items():
        if len(node_ids) > 1:
            issues.append(
                _issue("multi_writer_unsupported", f"draft.nodes[*].writes_slot={slot}",
                       f"slot {slot!r} has multiple writers {sorted(node_ids)}; multi-writer "
                       "slots are outside the static-runtime v1 matrix")
            )

    for node in draft.nodes:
        if node.condition_ref is not None:
            issues.append(_issue("conditions_unsupported", "PlanNode.condition_ref",
                                 f"node {node.id!r} carries a condition ref", node_id=node.id))
        if node.max_attempts > 1:
            issues.append(_issue("retries_unsupported", "PlanNode.max_attempts",
                                 f"node {node.id!r} requests max_attempts={node.max_attempts}",
                                 node_id=node.id))
        if node.gate_ids:
            issues.append(_issue("gates_unsupported", "PlanNode.gate_ids",
                                 f"node {node.id!r} carries gate ids", node_id=node.id))
        if node.debate_id is not None:
            issues.append(_issue("debates_unsupported", "PlanNode.debate_id",
                                 f"node {node.id!r} carries a debate id", node_id=node.id))
        # Affirmative allow-list: every dependency policy must be IN the profile's
        # closed tuple (a future policy value is rejected, never assumed supported).
        for dep in node.dependencies:
            if dep.policy not in profile.supported_dependency_policies:
                issues.append(_issue(
                    "dependency_policy_unsupported", "Dependency.policy",
                    f"node {node.id!r} dependency on {dep.upstream_node_id!r} uses policy "
                    f"{dep.policy.value!r} which is not in the static profile's "
                    "supported_dependency_policies allow-list", node_id=node.id))

    # -- (3) bridge static support (per node, per active bridge) ----------- #
    summaries: list[BridgeStaticSupportSummary] = []
    descriptor_refs: set = set()
    config_refs: set = set()
    provider_refs: set = set()
    analyzer_refs: set = set()

    for node in draft.nodes:
        try:
            worker: "WorkerSpec" = catalog.worker(node.worker_id)
        except CatalogMaterialError:
            continue  # unknown worker is a Phase-1 concern (report already invalid)

        # Affirmative allow-list: the worker's execution kind and every declared
        # input cardinality must be IN the profile's closed tuples — a future
        # Phase-1 enum/literal value is rejected, never assumed supported.
        if worker.execution.kind not in profile.supported_execution_kinds:
            issues.append(_issue(
                "execution_kind_unsupported", "WorkerSpec.execution.kind",
                f"worker {worker.id!r} execution kind {worker.execution.kind.value!r} is "
                "not in the static profile's supported_execution_kinds allow-list",
                node_id=node.id))
        for binding in worker.inputs:
            if binding.cardinality not in profile.supported_cardinalities:
                issues.append(_issue(
                    "cardinality_unsupported", "InputBinding.cardinality",
                    f"worker {worker.id!r} input {binding.name!r} cardinality "
                    f"{binding.cardinality!r} is not in the static profile's "
                    "supported_cardinalities allow-list", node_id=node.id))

        node_params_digest = content_digest(dict(node.params))
        worker_digest = worker.semantic_digest()
        worker_cap_keys = {_ref_key(c) for c in worker.capability_allowlist}

        active = bridge_view.active_bridges_for(worker)
        node_summaries: list[BridgeStaticSupportSummary] = []
        for rb in active:
            desc = rb.descriptor
            if desc.pre_input_kind not in profile.bridge_pre_input_modes:
                issues.append(_issue("unsupported_bridge_pre_input", "ExecutionBridgeDescriptor.pre_input_kind",
                                     f"bridge {desc.bridge_id!r} pre_input_kind {desc.pre_input_kind!r} "
                                     "is not in the static profile", node_id=node.id))
                continue
            if worker.execution.kind not in desc.supported_execution_kinds:
                issues.append(_issue("bridge_execution_kind_unsupported",
                                     "ExecutionBridgeDescriptor.supported_execution_kinds",
                                     f"bridge {desc.bridge_id!r} does not support execution kind "
                                     f"{worker.execution.kind.value!r} of worker {worker.id!r}",
                                     node_id=node.id))
                continue

            summary = rb.analyzer.analyze(
                candidate_plan_digest=candidate,
                node=node,
                worker=worker,
                descriptor=desc,
                descriptor_ref=rb.descriptor_ref,
                config_bytes=rb.config_bytes,
            )

            bound_ok = (
                summary.candidate_plan_digest == candidate
                and summary.node_id == node.id
                and summary.node_params_digest == node_params_digest
                and summary.worker_id == worker.id
                and summary.worker_digest == worker_digest
                and summary.bridge_id == desc.bridge_id
                and summary.descriptor_ref == rb.descriptor_ref
                and summary.config_ref == rb.config_ref
                and summary.provider_ref == rb.provider_ref
                and summary.analyzer_ref == rb.analyzer_ref
                and summary.pre_input_kind == desc.pre_input_kind
            )
            if not bound_ok:
                issues.append(_issue("analyzer_binding_mismatch", "BridgeStaticSupportSummary",
                                     f"analyzer summary for bridge {desc.bridge_id!r} does not bind the "
                                     f"exact candidate/node/worker/descriptor identity (analyzer failure)",
                                     node_id=node.id))
                continue

            if not all(_ref_key(c) in worker_cap_keys for c in summary.allowed_capability_refs):
                issues.append(_issue("analyzer_allowlist_drift", "BridgeStaticSupportSummary.allowed_capability_refs",
                                     f"bridge {desc.bridge_id!r} summary claims a capability outside worker "
                                     f"{worker.id!r}'s allowlist (analyzer failure, not authority)",
                                     node_id=node.id))
                continue

            node_summaries.append(summary)
            summaries.append(summary)
            descriptor_refs.add(rb.descriptor_ref)
            config_refs.add(rb.config_ref)
            provider_refs.add(rb.provider_ref)
            analyzer_refs.add(rb.analyzer_ref)

        # tool-call arithmetic over this node's active summaries.
        tc = worker.evidence_policy.tool_calls
        min_sum = sum(s.min_finalized_tool_calls_on_success for s in node_summaries)
        max_sum = sum(s.max_capability_invocations for s in node_summaries)
        if tc is ToolCallRequirement.REQUIRED and min_sum < 1:
            issues.append(_issue("tool_calls_required_unmet", "WorkerSpec.evidence_policy.tool_calls",
                                 f"worker {worker.id!r} requires tool calls but the summed active-bridge "
                                 f"min_finalized_tool_calls_on_success is {min_sum} (< 1)", node_id=node.id))
        if tc is ToolCallRequirement.FORBIDDEN and max_sum != 0:
            issues.append(_issue("tool_calls_forbidden_violated", "WorkerSpec.evidence_policy.tool_calls",
                                 f"worker {worker.id!r} forbids tool calls but the summed active-bridge "
                                 f"max_capability_invocations is {max_sum} (!= 0)", node_id=node.id))

    # -- (4) ContextRuntimeRequirements closure ---------------------------- #
    ctx_req_ref = None
    ctx_req_digest = None
    has_ctx_ref = context is not None and context.runtime_requirements_ref is not None

    if context is None:
        if context_requirements is not None:
            issues.append(_issue("context_requirements_unexpected", "context_requirements",
                                 "a BOOTSTRAP/no-context plan must not carry resolved runtime requirements"))
    elif not has_ctx_ref:
        # canonical empty memory requires none.
        if context_requirements is not None:
            issues.append(_issue("context_requirements_unexpected", "context_requirements",
                                 "the canonical empty-memory context requires no runtime requirements"))
    else:
        snapshot_ref = context.runtime_requirements_ref
        if context_requirements is None:
            issues.append(_issue("context_requirements_missing", "context_requirements",
                                 "a non-empty memory context requires a resolved ContextRuntimeRequirements"))
        else:
            rcr = context_requirements
            if rcr.typed_ref != snapshot_ref:
                issues.append(_issue("context_requirements_ref_mismatch", "context_requirements.typed_ref",
                                     "resolved requirements typed_ref does not equal the ContextSnapshot ref"))
            else:
                ctx_req_ref = rcr.typed_ref
                ctx_req_digest = rcr.fact.requirements_digest
                try:
                    verify_context_runtime_requirements(context, rcr.fact)
                except ValueError:
                    issues.append(_issue("context_subject_mismatch", "context_requirements.fact",
                                         "resolved requirements do not bind this exact context subject"))
                if rcr.fact.required_schema_registry_digest != registry_digest:
                    issues.append(_issue("context_required_registry_mismatch",
                                         "ContextRuntimeRequirements.required_schema_registry_digest",
                                         "required registry digest is absent from the exact runtime views"))
                if rcr.fact.required_catalog_digest != catalog_digest:
                    issues.append(_issue("context_required_catalog_mismatch",
                                         "ContextRuntimeRequirements.required_catalog_digest",
                                         "required catalog digest is absent from the exact runtime views"))
                for mref in rcr.fact.required_runtime_material_refs:
                    try:
                        catalog.text(mref)
                    except CatalogMaterialError:
                        issues.append(_issue("context_required_material_missing",
                                             "ContextRuntimeRequirements.required_runtime_material_refs",
                                             f"required material {mref.id}@{mref.version} is absent from the catalog"))
                for cref in rcr.fact.required_capability_refs:
                    try:
                        catalog.capability(cref)
                    except CatalogMaterialError:
                        issues.append(_issue("context_required_capability_missing",
                                             "ContextRuntimeRequirements.required_capability_refs",
                                             f"required capability {cref.id}@{cref.version} is absent from the catalog"))
                bridge_ids = bridge_view.bridge_ids()
                for bid in rcr.fact.required_bridge_ids:
                    if bid not in bridge_ids:
                        issues.append(_issue("context_required_bridge_missing",
                                             "ContextRuntimeRequirements.required_bridge_ids",
                                             f"required bridge {bid!r} is absent from the bridge view"))

    # -- assemble the frozen, self-sealed report --------------------------- #
    ordered_issues = tuple(sorted(issues, key=lambda i: i.sort_key))
    ordered_summaries = tuple(sorted(summaries, key=lambda s: (s.node_id, s.bridge_id)))
    return RuntimeSupportReport.build(
        supported=(len(ordered_issues) == 0),
        issues=ordered_issues,
        candidate_plan_digest=candidate,
        phase1_report_digest=phase1_report_digest,
        runtime_profile_digest=profile_digest,
        catalog_digest=catalog_digest,
        schema_registry_digest=registry_digest,
        context_requirements_ref=ctx_req_ref,
        context_requirements_digest=ctx_req_digest,
        active_bridge_descriptor_refs=tuple(sorted(descriptor_refs, key=_ref_key)),
        active_bridge_config_refs=tuple(sorted(config_refs, key=_ref_key)),
        active_bridge_provider_refs=tuple(sorted(provider_refs, key=_ref_key)),
        active_bridge_analyzer_refs=tuple(sorted(analyzer_refs, key=_ref_key)),
        bridge_support_summaries=ordered_summaries,
    )
