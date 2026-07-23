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

from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, ConfigDict, model_validator

from guanlan_v2.orchestration.catalog_runtime import (
    BridgeCatalogView,
    CatalogMaterialError,
    CatalogRuntime,
)
from guanlan_v2.orchestration.context import verify_context_runtime_requirements
from guanlan_v2.orchestration.digest import DigestHex, content_digest
from guanlan_v2.orchestration.enums import (
    DependencyPolicy,
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
    from guanlan_v2.orchestration.bootstrap import BootstrapRuntimeProfile
    from guanlan_v2.orchestration.catalog import WorkerSpec
    from guanlan_v2.orchestration.context import ContextSnapshot
    from guanlan_v2.orchestration.schema_registry import SchemaRegistry
    from guanlan_v2.orchestration.spec import PlanDraft, PlanNode, PlanValidationReport

__all__ = [
    "check_runtime_support",
    "CHECKER_VERSION",
    # -- Phase 8 · Task 8 runtime profile v2 (Option-4 new model) ----------- #
    "StaticRuntimeProfileV2",
    "static_runtime_profile_v2",
    "STATIC_RUNTIME_PROFILE_V2",
    # -- Phase 8 · Task 8 v2 support analyzers (pure, I/O-free) -------------- #
    "analyze_reducers",
    "analyze_gates",
    "analyze_retry_repair",
]

CHECKER_VERSION = "static-runtime-checker-v1"


# =========================================================================== #
# Phase 8 · Task 8 — StaticRuntimeProfileV2 (the Option-4 profile widening)    #
# =========================================================================== #
#
# Why a NEW model rather than a v2 INSTANCE of ``StaticRuntimeProfile``
# --------------------------------------------------------------------
# The Phase-2 ``StaticRuntimeProfile`` pins ``profile_version: Literal["1"]``,
# every ``supports_* : Literal[False]`` and ``max_attempts_supported: Literal[1]``,
# and a ``model_validator`` that rejects any deviation — a v2 *instance* is
# structurally impossible. This mirrors the Phase-5 BOOTSTRAP ruling exactly
# (bootstrap.py ``BootstrapRuntimeProfile``): the reviewed **Option 4** resolution
# of a profile widening is a *distinct registered model* with its own closed
# Literals, NOT a Literal-widening of the frozen v1 schema (which four golden
# manifests pin). Plan clause (f): ``profile_id`` stays ``"static-runtime"`` and
# the version becomes ``"2"`` — ``bootstrap-runtime`` is a distinct ``profile_id``,
# so ``"2"`` is free. The Phase-2 v1 constant / schema / digest and every golden
# that pins them stay byte-identical; this model is defined here (a Task-8 file)
# and deliberately NOT added to ``PHASE2_RUNTIME_MODELS`` / any registry golden
# (it is a checker *input*, resolved by value like v1 is passed to
# :func:`check_runtime_support`, never SchemaRef-resolved), so no golden moves.
_V2_PLAN_SOURCES: tuple[PlanSource, ...] = (
    PlanSource.PRESET, PlanSource.PRESET_FALLBACK, PlanSource.DYNAMIC,
)
_V2_EXECUTION_KINDS: tuple[ExecutionKind, ...] = (
    ExecutionKind.LLM, ExecutionKind.DETERMINISTIC,
)
_V2_DEPENDENCY_POLICIES: tuple[DependencyPolicy, ...] = (
    DependencyPolicy.BLOCK, DependencyPolicy.DEGRADE, DependencyPolicy.SKIP,
)
_V2_ZERO_DIGEST = "0" * 64


class StaticRuntimeProfileV2(BaseModel):
    """The closed v2 static-runtime feature matrix (self-sealed ``profile_digest``).

    Extends the v1 admission matrix by exactly the reviewed v2 unlocks —
    ``debates``, deterministic ``reducers`` / ``multi_writer`` slots, ``gate_metrics``,
    ``max_attempts`` up to ``max_attempts_limit=2`` and bounded schema repair
    (``schema_repairs_per_attempt=1``). Everything the v1 checker reads is present
    with a bit-equal value except the five explicitly-unlocked switches; **conditions
    and stop conditions stay ``Literal[False]``** (they remain rejected before
    reservation under v2). Mirrors the Phase-1 strict config (``extra='forbid'`` /
    ``strict=True`` / ``frozen=True``) without ``ContractModel`` identity, so the
    Phase-1/2 completeness firewalls never discover it. ``profile_digest`` seals the
    whole matrix over the same canonical projection idiom as v1.
    """

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    schema_version: Literal["1"] = "1"
    profile_id: Literal["static-runtime"] = "static-runtime"
    profile_version: Literal["2"] = "2"

    supported_plan_sources: tuple[PlanSource, ...] = _V2_PLAN_SOURCES
    supported_execution_kinds: tuple[ExecutionKind, ...] = _V2_EXECUTION_KINDS
    supported_dependency_policies: tuple[DependencyPolicy, ...] = _V2_DEPENDENCY_POLICIES
    supported_cardinalities: tuple[str, ...] = ("one", "many")

    supports_bootstrap: Literal[False] = False
    supports_conditions: Literal[False] = False
    supports_reducers: Literal[True] = True
    supports_multi_writer: Literal[True] = True
    supports_debates: Literal[True] = True
    supports_gates: Literal[True] = True
    supports_stop_conditions: Literal[False] = False
    supports_retries: Literal[True] = True
    max_attempts_supported: Literal[2] = 2
    max_attempts_limit: Literal[2] = 2
    schema_repairs_per_attempt: Literal[1] = 1

    bridge_pre_input_modes: tuple[str, ...] = ("none", "memory_refs_v1")
    bridge_lifecycle: Literal["static_prefetch_v1"] = "static_prefetch_v1"
    max_prompt_assemblies_per_llm_node: Literal[2] = 2
    max_model_invocations_per_llm_node: Literal[2] = 2

    profile_digest: DigestHex

    def _semantic_payload(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "profile_id": self.profile_id,
            "profile_version": self.profile_version,
            "supported_plan_sources": [s.value for s in self.supported_plan_sources],
            "supported_execution_kinds": [k.value for k in self.supported_execution_kinds],
            "supported_dependency_policies": [
                p.value for p in self.supported_dependency_policies
            ],
            "supported_cardinalities": list(self.supported_cardinalities),
            "supports_bootstrap": self.supports_bootstrap,
            "supports_conditions": self.supports_conditions,
            "supports_reducers": self.supports_reducers,
            "supports_multi_writer": self.supports_multi_writer,
            "supports_debates": self.supports_debates,
            "supports_gates": self.supports_gates,
            "supports_stop_conditions": self.supports_stop_conditions,
            "supports_retries": self.supports_retries,
            "max_attempts_supported": self.max_attempts_supported,
            "max_attempts_limit": self.max_attempts_limit,
            "schema_repairs_per_attempt": self.schema_repairs_per_attempt,
            "bridge_pre_input_modes": list(self.bridge_pre_input_modes),
            "bridge_lifecycle": self.bridge_lifecycle,
            "max_prompt_assemblies_per_llm_node": self.max_prompt_assemblies_per_llm_node,
            "max_model_invocations_per_llm_node": self.max_model_invocations_per_llm_node,
        }

    @model_validator(mode="after")
    def _verify(self) -> "StaticRuntimeProfileV2":
        if tuple(sorted(s.value for s in self.supported_plan_sources)) != tuple(
            sorted(s.value for s in _V2_PLAN_SOURCES)
        ):
            raise ValueError("supported_plan_sources must equal the closed v2 matrix")
        if PlanSource.BOOTSTRAP in self.supported_plan_sources:
            raise ValueError("BOOTSTRAP is never a supported static plan source")
        if tuple(sorted(k.value for k in self.supported_execution_kinds)) != tuple(
            sorted(k.value for k in _V2_EXECUTION_KINDS)
        ):
            raise ValueError("supported_execution_kinds must equal the closed v2 matrix")
        if tuple(sorted(p.value for p in self.supported_dependency_policies)) != tuple(
            sorted(p.value for p in _V2_DEPENDENCY_POLICIES)
        ):
            raise ValueError("supported_dependency_policies must equal the closed v2 matrix")
        if tuple(sorted(self.supported_cardinalities)) != ("many", "one"):
            raise ValueError("supported_cardinalities must equal {'one','many'}")
        if tuple(sorted(self.bridge_pre_input_modes)) != ("memory_refs_v1", "none"):
            raise ValueError("bridge_pre_input_modes must equal {'none','memory_refs_v1'}")
        if self.profile_digest != content_digest(self._semantic_payload()):
            raise ValueError("declared profile_digest does not match the canonical matrix")
        return self

    @classmethod
    def build(cls, **fields: Any) -> "StaticRuntimeProfileV2":
        stub = {
            "schema_version": "1",
            "profile_id": "static-runtime",
            "profile_version": "2",
            "supported_plan_sources": _V2_PLAN_SOURCES,
            "supported_execution_kinds": _V2_EXECUTION_KINDS,
            "supported_dependency_policies": _V2_DEPENDENCY_POLICIES,
            "supported_cardinalities": ("one", "many"),
            "supports_bootstrap": False,
            "supports_conditions": False,
            "supports_reducers": True,
            "supports_multi_writer": True,
            "supports_debates": True,
            "supports_gates": True,
            "supports_stop_conditions": False,
            "supports_retries": True,
            "max_attempts_supported": 2,
            "max_attempts_limit": 2,
            "schema_repairs_per_attempt": 1,
            "bridge_pre_input_modes": ("none", "memory_refs_v1"),
            "bridge_lifecycle": "static_prefetch_v1",
            "max_prompt_assemblies_per_llm_node": 2,
            "max_model_invocations_per_llm_node": 2,
        }
        stub.update(fields)
        provisional = cls.model_construct(**stub, profile_digest=_V2_ZERO_DIGEST)
        stub["profile_digest"] = content_digest(provisional._semantic_payload())
        return cls(**stub)


def static_runtime_profile_v2() -> StaticRuntimeProfileV2:
    """The single canonical static-runtime v2 profile (a fresh, equal instance)."""
    return StaticRuntimeProfileV2.build()


#: the canonical exported v2 profile instance (plan Task 8: ``STATIC_RUNTIME_PROFILE_V2``).
STATIC_RUNTIME_PROFILE_V2: StaticRuntimeProfileV2 = static_runtime_profile_v2()


# =========================================================================== #
# Phase 8 · Task 8 — the v2 support analyzers (pure, I/O-free)                 #
# =========================================================================== #
# Each is a pure function of (already Phase-1-valid draft, resolved catalog view)
# run BEFORE reservation, exactly like every Phase-2 analyzer. They express the
# runtime-support-level checks the v1 checker used to blanket-reject: when the v2
# profile *admits* a feature, these validate its runtime well-formedness instead
# of rejecting it outright. They never mutate, never touch a store/budget, and
# never re-derive Phase-1 validity (Phase 1 already rejected an unreduced
# multi-writer slot, an unknown reducer/gate ref kind, an unregistered reducer
# output schema, and every debate-coherence violation).


def _material_kind(catalog: CatalogRuntime, ref) -> str | None:
    """The catalog material kind for ``ref`` (exact identity), or ``None`` if absent."""
    try:
        return catalog.text(ref).kind
    except CatalogMaterialError:
        return None


def analyze_reducers(
    draft: "PlanDraft", *, catalog: CatalogRuntime
) -> tuple[RuntimeSupportIssue, ...]:
    """Runtime support for the v2 deterministic multi-writer reducers.

    Every multi-writer slot must have exactly one :class:`ReducerCfg` whose
    ``reducer_ref`` resolves to a catalog ``kind="reducer"`` material and whose
    ``producer_node_ids`` equal the slot's writers (a runtime-support mirror of the
    Phase-1 structural check, expressed in the runtime-support vocabulary). Emits
    canonically stable issues; empty iff every multi-writer slot is well-reduced.
    """
    issues: list[RuntimeSupportIssue] = []
    slot_writers: dict[str, list[str]] = {}
    for node in draft.nodes:
        slot_writers.setdefault(node.writes_slot, []).append(node.id)

    reducers_by_slot: dict[str, list] = {}
    for red in draft.reducers:
        reducers_by_slot.setdefault(red.slot, []).append(red)

    # every multi-writer slot needs exactly one reducer.
    for slot, writers in sorted(slot_writers.items()):
        if len(writers) <= 1:
            continue
        reds = reducers_by_slot.get(slot, [])
        if len(reds) != 1:
            issues.append(_issue(
                "reducer_missing_or_ambiguous", f"draft.reducers[slot={slot}]",
                f"multi-writer slot {slot!r} (writers {sorted(writers)}) requires exactly one "
                f"ReducerCfg; found {len(reds)}"))

    for red in draft.reducers:
        writers = sorted(slot_writers.get(red.slot, ()))
        kind = _material_kind(catalog, red.reducer_ref)
        if kind is None:
            issues.append(_issue(
                "reducer_ref_unresolved", "ReducerCfg.reducer_ref",
                f"reducer {red.id!r} reducer_ref {red.reducer_ref.id}@{red.reducer_ref.version} "
                "does not resolve to a catalog material at its exact identity"))
        elif kind != "reducer":
            issues.append(_issue(
                "reducer_ref_wrong_kind", "ReducerCfg.reducer_ref",
                f"reducer {red.id!r} reducer_ref resolves to a {kind!r} material, not a "
                "'reducer' material"))
        if sorted(red.producer_node_ids) != writers:
            issues.append(_issue(
                "reducer_producers_mismatch", "ReducerCfg.producer_node_ids",
                f"reducer {red.id!r} producer_node_ids {sorted(red.producer_node_ids)} do not "
                f"equal slot {red.slot!r} writers {writers}"))
    return tuple(sorted(issues, key=lambda i: i.sort_key))


def analyze_gates(
    draft: "PlanDraft", *, catalog: CatalogRuntime
) -> tuple[RuntimeSupportIssue, ...]:
    """Runtime support for the v2 honesty gates over catalog-owned gate metrics.

    Every :class:`GateCfg.metric` must resolve to a ``kind="gate_metric"`` catalog
    material at its exact identity. A blocking gate with ``unavailable_policy="fail"``
    is well-formed (it is *run-blocking* by design — no issue); this analyzer only
    rejects a metric ref that does not resolve to a gate-metric material.
    """
    issues: list[RuntimeSupportIssue] = []
    for gate in draft.gates:
        kind = _material_kind(catalog, gate.metric)
        if kind is None:
            issues.append(_issue(
                "gate_metric_unresolved", "GateCfg.metric",
                f"gate {gate.id!r} metric {gate.metric.id}@{gate.metric.version} does not "
                "resolve to a catalog material at its exact identity"))
        elif kind != "gate_metric":
            issues.append(_issue(
                "gate_metric_wrong_kind", "GateCfg.metric",
                f"gate {gate.id!r} metric resolves to a {kind!r} material, not a "
                "'gate_metric' material"))
    return tuple(sorted(issues, key=lambda i: i.sort_key))


def analyze_retry_repair(
    draft: "PlanDraft", *, profile: StaticRuntimeProfileV2
) -> tuple[RuntimeSupportIssue, ...]:
    """Runtime support for v2 ``max_attempts`` (cap = ``profile.max_attempts_limit``).

    Every node's ``max_attempts`` must be ``<= profile.max_attempts_limit`` (the
    reviewed cap of 2). The per-attempt LLM-invocation upper bound
    (``max_attempts × (1 + schema_repairs_per_attempt)`` for an LLM node, zero for a
    deterministic node) is the runner's reservation formula — it needs the catalog
    execution kind and so is computed there (see
    :func:`~guanlan_v2.orchestration.worker.retry_llm_invocation_upper_bound`); this
    pure analyzer, per its ``(draft, profile)`` signature, checks only the cap.
    """
    issues: list[RuntimeSupportIssue] = []
    limit = profile.max_attempts_limit
    for node in draft.nodes:
        if node.max_attempts > limit:
            issues.append(_issue(
                "max_attempts_exceeds_limit", "PlanNode.max_attempts",
                f"node {node.id!r} requests max_attempts={node.max_attempts} which exceeds the "
                f"v2 profile cap max_attempts_limit={limit}", node_id=node.id))
    return tuple(sorted(issues, key=lambda i: i.sort_key))


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
    profile: "StaticRuntimeProfile | BootstrapRuntimeProfile | StaticRuntimeProfileV2",
) -> RuntimeSupportReport:
    """Pure static-runtime support check → a frozen :class:`RuntimeSupportReport`.

    Never dereferences storage. Emits a canonically ordered issue for every
    unsupported / drifted construct (see the module docstring / brief matrix) and
    is ``supported`` iff no issue is emitted. All identity digests it validated are
    bound into the returned report.

    Phase 5 (Task 8, additive — static-profile behavior bit-unchanged): the
    reviewed ``BootstrapRuntimeProfile`` is also accepted; its data-expressed
    delta is exactly one admission widening — a ``phase="bootstrap"`` /
    no-ContextSnapshot draft with ``source ∈ profile.bootstrap_plan_sources``
    (``PRESET`` / ``PRESET_FALLBACK``) is supported. ``PlanSource.BOOTSTRAP``
    stays dormant and a bootstrap-phase ``DYNAMIC`` draft stays rejected under
    every profile.
    """
    issues: list[RuntimeSupportIssue] = []

    # -- Phase 5 additive: the reviewed bootstrap profile identity ---------- #
    is_bootstrap_profile = (
        profile.profile_id == "bootstrap-runtime"
        and getattr(profile, "supports_bootstrap", False) is True
    )

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
    if profile.profile_id != "static-runtime" and not is_bootstrap_profile:
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
    # the exact reviewed widening: bootstrap phase + no ContextSnapshot + a static
    # preset source, and ONLY under the bootstrap profile. Everything else is
    # bit-equal to static-runtime v1 (is_bootstrap_profile is False there).
    bootstrap_admissible = (
        is_bootstrap_profile
        and draft.phase == "bootstrap"
        and context is None
        and draft.context_snapshot_ref is None
        and draft.source in getattr(profile, "bootstrap_plan_sources", ())
    )
    if (
        draft.source is PlanSource.BOOTSTRAP or draft.phase == "bootstrap" or context is None
    ) and not bootstrap_admissible:
        issues.append(
            _issue(
                "bootstrap_unsupported",
                "draft.source",
                "BOOTSTRAP / no-ContextSnapshot Lane-0 execution is a Phase-5 runtime "
                "profile, not static-runtime v1",
            )
        )
    # -- Phase 8 · Task 8 additive: v2 profiles ADMIT reducers/gates/debates/  --
    # multi-writer/retries (validating runtime well-formedness via the v2
    # analyzers) instead of blanket-rejecting them. Every gate below is on a
    # ``profile.supports_*`` switch; a v1 ``StaticRuntimeProfile`` and the Phase-5
    # BOOTSTRAP profile carry those switches ``= False``, so their branch is the
    # EXACT prior blanket rejection (byte-identical v1/BOOTSTRAP behavior — the
    # regression pins). Conditions and stop conditions stay unconditionally
    # rejected under EVERY profile (clause: "conditions and stop conditions remain
    # rejected before reservation under v2").
    supports_reducers = getattr(profile, "supports_reducers", False)
    supports_gates = getattr(profile, "supports_gates", False)
    supports_debates = getattr(profile, "supports_debates", False)
    supports_multi_writer = getattr(profile, "supports_multi_writer", False)
    supports_retries = getattr(profile, "supports_retries", False)

    if draft.reducers:
        if supports_reducers:
            issues.extend(analyze_reducers(draft, catalog=catalog))
        else:
            issues.append(_issue("reducers_unsupported", "draft.reducers",
                                 "reducers are outside the static-runtime v1 matrix"))
    if draft.gates:
        if supports_gates:
            issues.extend(analyze_gates(draft, catalog=catalog))
        else:
            issues.append(_issue("gates_unsupported", "draft.gates",
                                 "gates / gate metrics are outside the static-runtime v1 matrix"))
    if draft.debates:
        if supports_debates:
            # Phase 8 * Task 9: the v2 profile ADMITS Lane-D debates; validate their
            # runtime well-formedness (seats LLM, judge not a seat, max_rounds <= cap,
            # budget covers the fully expanded invocation count) BEFORE any reservation.
            from guanlan_v2.orchestration.debate import analyze_debates as _analyze_debates

            issues.extend(_analyze_debates(draft, catalog=catalog, profile=profile))
        else:
            issues.append(_issue("debates_unsupported", "draft.debates",
                                 "debates are outside the static-runtime v1 matrix"))
    if draft.stop_condition_refs:
        issues.append(_issue("stop_conditions_unsupported", "draft.stop_condition_refs",
                             "stop conditions are outside the static-runtime v1 matrix"))

    # multi-writer: a slot written by more than one node. v2 admits it (reducer
    # coherence is validated by analyze_reducers above); v1/BOOTSTRAP reject it.
    writers: dict[str, list[str]] = {}
    for node in draft.nodes:
        writers.setdefault(node.writes_slot, []).append(node.id)
    for slot, node_ids in writers.items():
        if len(node_ids) > 1 and not supports_multi_writer:
            issues.append(
                _issue("multi_writer_unsupported", f"draft.nodes[*].writes_slot={slot}",
                       f"slot {slot!r} has multiple writers {sorted(node_ids)}; multi-writer "
                       "slots are outside the static-runtime v1 matrix")
            )

    if supports_retries:
        # v2: max_attempts up to the profile cap is admitted; the analyzer rejects
        # only max_attempts > max_attempts_limit.
        issues.extend(analyze_retry_repair(draft, profile=profile))

    for node in draft.nodes:
        if node.condition_ref is not None:
            issues.append(_issue("conditions_unsupported", "PlanNode.condition_ref",
                                 f"node {node.id!r} carries a condition ref", node_id=node.id))
        if not supports_retries and node.max_attempts > 1:
            issues.append(_issue("retries_unsupported", "PlanNode.max_attempts",
                                 f"node {node.id!r} requests max_attempts={node.max_attempts}",
                                 node_id=node.id))
        if node.gate_ids and not supports_gates:
            issues.append(_issue("gates_unsupported", "PlanNode.gate_ids",
                                 f"node {node.id!r} carries gate ids", node_id=node.id))
        if node.debate_id is not None and not supports_debates:
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
