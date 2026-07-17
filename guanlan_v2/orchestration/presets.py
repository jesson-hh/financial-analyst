# -*- coding: utf-8 -*-
"""Phase 2 · Task 9 — reviewed static presets: the three-worker pilot plan and the
attested legacy compatibility bridge.

This module is a **consumer** of the Phase-1 contracts and the Phase-2 runtime
kernel; it never forks a canonical digest, WorkerSpec, catalog snapshot, PlanDraft
or freeze semantic. It provides two reviewed static-plan surfaces:

* **9.1 — the three-final-worker pilot** (``text.sentiment -> dec.research_mgr ->
  dec.pm``): a strict ``PRESET`` :class:`OrchestrationRequest`, the canonical
  pre-Phase-3 *empty-memory* :class:`ContextSnapshot` (built from Phase-1's
  :func:`build_empty_memory_binding`, persisting both ``EmptyMemory*`` facts,
  wrapping each as its exact main :class:`TypedPayloadRef`, ``memory_session_id=None``
  and ``runtime_requirements_ref=None`` — never a blank/random locator, plain
  ``PayloadRef`` or a caller-selected session scope), and the reviewed triad
  :class:`PlanDraft`.
* **9.2 — the attested legacy compatibility bridge**: the pure adapter
  :func:`legacy_draft_from_mapping` (a fully-MAPPED :class:`LegacyGraphMapping`
  → a ``compat.*`` :class:`PlanDraft`), a service-only attestation store path
  (:func:`attest_and_store_legacy_plan` delegating to Phase-1
  :func:`attest_static_legacy_plan`), the reviewed ``stock-deep-dive-compat-v1``
  catalog loader, the reviewed MAPPED mapping builder and the cumulative
  end-of-Phase-2 static catalog snapshot (:func:`phase2_static_catalog_snapshot`
  / :data:`PHASE2_STATIC_CATALOG_DIGEST`).

There is deliberately **no** public ``plandraft_from_dagnodes(list[DAGNode])``
heuristic: a legacy node only becomes a runnable plan node through a reviewed,
digest-bound :class:`LegacyGraphMapping`.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, NamedTuple

import yaml

from guanlan_v2.orchestration.catalog import (
    CompatibilityBinding,
    ContentManifestEntry,
    EvidencePolicy,
    ExecutionSpec,
    InputBinding,
    OutputBinding,
    SkillBinding,
    SkillManifest,
    WorkerCatalogSnapshot,
    WorkerSpec,
    build_catalog_snapshot,
    parse_skill_v1,
)
from guanlan_v2.orchestration.catalog_runtime import (
    CatalogMaterialError,
    CatalogRuntime,
    InMemoryMaterialSource,
    build_text_material,
    load_pilot_catalog,
)
from guanlan_v2.orchestration.context import (
    ClockSpec,
    ContextSnapshot,
    DataContext,
    EmptyMemoryBinding,
    build_empty_memory_binding,
)
from guanlan_v2.orchestration.digest import content_digest
from guanlan_v2.orchestration.enums import (
    ApprovalPolicy,
    DataBackend,
    DataMode,
    DependencyPolicy,
    ExecutionKind,
    MappingStatus,
    NodeStatus,
    PlanSource,
    Tier,
    ToolCallRequirement,
)
from guanlan_v2.orchestration.migration import (
    ADAPTER_VERSION,
    LegacyDependencyMapping,
    LegacyGraphMapping,
    LegacyInputMapping,
    LegacyWorkerMapping,
    MigrationError,
    SRC_STOCK_DEEP_DIVE,
    attest_static_legacy_plan,
    compatibility_binding_for,
    normalize_legacy_graph_config,
)
from guanlan_v2.orchestration.refs import ContentRef, PayloadRef, SchemaRef
from guanlan_v2.orchestration.spec import (
    Dependency,
    OrchestrationRequest,
    PlanDraft,
    PlanNode,
    StaticLegacyPlanAttestation,
)

__all__ = [
    # -- 9.1 pilot --------------------------------------------------------- #
    "EMPTY_MEMORY_SNAPSHOT_SR",
    "EMPTY_MEMORY_SELECTION_SR",
    "EMPTY_MEMORY_SNAPSHOT_REF",
    "EMPTY_MEMORY_SELECTION_REF",
    "PresetEmptyMemoryContext",
    "preset_orchestration_request",
    "pilot_data_context",
    "build_empty_memory_context",
    "build_pilot_draft",
    "PILOT_NODE_IDS",
    # -- 9.2 legacy compatibility bridge ----------------------------------- #
    "legacy_draft_from_mapping",
    "attest_and_store_legacy_plan",
    "COMPAT_CATALOG_PATH",
    "COMPAT_MATERIALS_DIR",
    "load_compat_catalog",
    "build_stock_deep_dive_compat_mapping",
    "phase2_static_catalog_snapshot",
    "PHASE2_STATIC_CATALOG_DIGEST",
]

_PLACEHOLDER_DIGEST = "0" * 64


# =========================================================================== #
# 9.1 — canonical empty-memory context (pre-Phase-3 no-memory runtime)         #
# =========================================================================== #
#: registered schema identities of the two canonical empty-memory facts.
EMPTY_MEMORY_SNAPSHOT_SR = SchemaRef(name="EmptyMemorySnapshot", version="1")
EMPTY_MEMORY_SELECTION_SR = SchemaRef(name="EmptyMemorySelection", version="1")
#: the canonical typed refs the ContextSnapshot binds (audit-only object ids).
_CANONICAL_BINDING: EmptyMemoryBinding = build_empty_memory_binding()
EMPTY_MEMORY_SNAPSHOT_REF = _CANONICAL_BINDING.memory_snapshot_ref
EMPTY_MEMORY_SELECTION_REF = _CANONICAL_BINDING.memory_selection_ref


class PresetEmptyMemoryContext(NamedTuple):
    """A built canonical empty-memory context + the persisted-fact store refs.

    ``context`` is the sealed :class:`ContextSnapshot`; ``binding`` is the pure
    :func:`build_empty_memory_binding` output whose exact typed refs the context
    embeds; ``snapshot_store_ref`` / ``selection_store_ref`` are the payload-store
    refs where the runtime persisted the two canonical facts (a real no-memory
    runtime persists them even though the empty path never dereferences them).
    """

    context: ContextSnapshot
    binding: EmptyMemoryBinding
    snapshot_store_ref: PayloadRef
    selection_store_ref: PayloadRef


def preset_orchestration_request(
    *, request_id: str, goal: str = "A-share single-stock deep-dive (preset pilot)",
) -> OrchestrationRequest:
    """A strict ``PRESET``-appropriate :class:`OrchestrationRequest`.

    The plan *source* is declared on the draft (``PlanSource.PRESET``); the request
    carries the reviewed ``orchestrate_only`` workflow and a ``REQUIRED`` approval
    policy (``AUTO`` is rejected for every source in Phase 1).
    """
    return OrchestrationRequest(
        request_id=request_id, goal=goal, workflow="orchestrate_only",
        approval_policy=ApprovalPolicy.REQUIRED)


def pilot_data_context(*, as_of: datetime) -> DataContext:
    """A canonical online :class:`DataContext` for the reviewed pilot run."""
    clock = ClockSpec(as_of=as_of, timezone="Asia/Shanghai", calendar_id="cn_a_share")
    return DataContext(
        as_of=as_of, clock=clock, mode=DataMode.ONLINE, backend=DataBackend.LIVE,
        strict_pit=False, calendar_id="cn_a_share",
        resolved_vendor_chains={"prices": ("tushare",)},
        source_config_digest="a" * 64, source_registry_digest="b" * 64,
        routing_snapshot_digest="c" * 64, data_snapshot_id="pilot-snap-1",
        data_snapshot_content_digest="d" * 64, built_at=as_of)


def build_empty_memory_context(
    *,
    data_context: DataContext,
    stores: Any,
    registry_digest: str,
    built_at: datetime,
    snapshot_id: str = "preset-ctx-1",
    memory_snapshot_id: str = "preset-ms-1",
) -> PresetEmptyMemoryContext:
    """Persist the two canonical empty-memory facts and seal the empty-memory context.

    Builds the Phase-1 canonical empty snapshot/selection, persists both as typed
    payloads in the ``main`` namespace, then seals a :class:`ContextSnapshot` that
    embeds each fact's exact typed ref, binds its nested content digest as
    ``memory_snapshot_hash`` / ``past_context_hash``, sets ``memory_session_id=None``
    and (because the pair is the canonical empty pair) ``runtime_requirements_ref=None``.
    """
    binding = build_empty_memory_binding()
    snap_ref = stores.payloads.put(
        EMPTY_MEMORY_SNAPSHOT_SR, binding.snapshot, registry_digest=registry_digest,
        namespace="main", idempotency_key="preset:empty-memory-snapshot")
    sel_ref = stores.payloads.put(
        EMPTY_MEMORY_SELECTION_SR, binding.selection, registry_digest=registry_digest,
        namespace="main", idempotency_key="preset:empty-memory-selection")
    context = ContextSnapshot.build(
        snapshot_id=snapshot_id, data_context=data_context,
        memory_snapshot_id=memory_snapshot_id, memory_snapshot_hash=binding.snapshot_hash,
        past_context_hash=binding.past_context_hash,
        memory_snapshot_ref=binding.memory_snapshot_ref,
        memory_selection_ref=binding.memory_selection_ref,
        runtime_requirements_ref=None, memory_session_id=None, built_at=built_at)
    return PresetEmptyMemoryContext(
        context=context, binding=binding, snapshot_store_ref=snap_ref,
        selection_store_ref=sel_ref)


# =========================================================================== #
# 9.1 — the reviewed three-worker pilot draft                                  #
# =========================================================================== #
#: reviewed pilot node ids (stable; the plan node id, not the worker id).
PILOT_NODE_IDS: tuple[str, str, str] = ("sentiment", "research", "pm")


def build_pilot_draft(
    *,
    catalog: CatalogRuntime,
    registry: Any,
    context: ContextSnapshot,
    request_id: str,
    run_id: str = "preset-pilot-run",
    as_of: datetime,
    plan_id: str = "plan-preset-pilot",
    budget_request_tokens: int = 2_000_000,
) -> PlanDraft:
    """The reviewed ``text.sentiment -> dec.research_mgr -> dec.pm`` PRESET draft.

    All three nodes reference ``final/dynamic_allowed`` workers (none ``compat.*``);
    every dependency is ``BLOCK`` so the sink's :class:`PortfolioDecision` requires
    a completed :class:`ResearchPlan` (and both upstream :class:`SentimentReport`
    reads). Binds the exact catalog/registry digests and a budget request of one
    LLM invocation per node.
    """
    n_sentiment = PlanNode(
        id="sentiment", worker_id="text.sentiment", writes_slot="slot-sentiment")
    n_research = PlanNode(
        id="research", worker_id="dec.research_mgr", writes_slot="slot-research",
        dependencies=(
            Dependency(upstream_node_id="sentiment", artifact_slot="slot-sentiment",
                       inject_as="sentiment", policy=DependencyPolicy.BLOCK),))
    n_pm = PlanNode(
        id="pm", worker_id="dec.pm", writes_slot="slot-pm",
        dependencies=(
            Dependency(upstream_node_id="research", artifact_slot="slot-research",
                       inject_as="research_plan", policy=DependencyPolicy.BLOCK),
            Dependency(upstream_node_id="sentiment", artifact_slot="slot-sentiment",
                       inject_as="sentiment", policy=DependencyPolicy.BLOCK)))
    ctx_ref = PayloadRef(
        namespace="main", object_id="preset-pilot-ctx", content_digest=context.content_digest)
    return PlanDraft(
        id=plan_id, run_id=run_id, request_id=request_id, phase="main",
        source=PlanSource.PRESET, goal="A-share single-stock deep-dive (preset pilot)",
        as_of=as_of, mode=DataMode.ONLINE, context_snapshot_ref=ctx_ref,
        nodes=(n_sentiment, n_research, n_pm), sink_node_ids=("pm",),
        catalog_version=catalog.catalog_version, catalog_digest=catalog.catalog_digest,
        schema_registry_digest=registry.registry_digest,
        approval_policy=ApprovalPolicy.REQUIRED,
        budget_request_tokens=budget_request_tokens, budget_request_llm_invocations=3,
        max_concurrency=3)


# =========================================================================== #
# 9.2 — legacy compatibility bridge (adapter + attestation + static catalog)   #
# =========================================================================== #
_CONFIG_ROOT = Path(__file__).resolve().parents[2] / "config" / "orchestration"
COMPAT_CATALOG_PATH = _CONFIG_ROOT / "catalogs" / "stock-deep-dive-compat-v1.yaml"
COMPAT_MATERIALS_DIR = _CONFIG_ROOT / "materials" / "stock-deep-dive-compat-v1"


class _AdapterError(MigrationError):
    """A legacy mapping cannot be adapted into a runnable compat draft."""


def _require_mapped(mapping: LegacyGraphMapping) -> None:
    if not isinstance(mapping, LegacyGraphMapping):
        raise _AdapterError("expected a LegacyGraphMapping")
    if mapping.mapping_status is not MappingStatus.MAPPED:
        raise _AdapterError(
            "legacy_draft_from_mapping requires a fully MAPPED legacy graph "
            "(every worker/dependency/input item mapped); an UNMAPPABLE graph "
            "yields no draft, binding or attestation")


def legacy_draft_from_mapping(
    mapping: LegacyGraphMapping,
    *,
    request: OrchestrationRequest,
    context: ContextSnapshot,
    catalog: CatalogRuntime,
    schema_registry: Any,
    budget_request: Mapping[str, int],
    sink_mapping: tuple[str, ...],
    run_id: str = "preset-legacy-run",
    plan_id: str = "plan-stock-deep-dive-compat",
    as_of: datetime | None = None,
) -> PlanDraft:
    """Build a ``compat.*`` :class:`PlanDraft` from a fully-MAPPED legacy graph.

    Node → worker comes only from :class:`LegacyWorkerMapping`; edge policy /
    accepted statuses / missing-output behavior only from
    :class:`LegacyDependencyMapping`; every legacy ``input_keys`` entry resolves
    through exactly one mapped :class:`LegacyInputMapping` (base ``code``/``asof_date``
    → ``param``/``context``; ``out_dir`` → a service ``service_binding``; every
    upstream value → an ``input_binding`` bound to the full upstream output
    SchemaRef, with a proven transitive ancestor for a non-direct dependency). The
    single-field-unwrap / ``model_dump`` projection is *not* applied here — it is
    recorded on the mapping and performed later inside the attested catalog-owned
    compatibility handler. The all-set legacy source/config/mapping tuple is written
    into the draft so the single Phase-1 candidate digest binds it.
    """
    _require_mapped(mapping)
    as_of = as_of if as_of is not None else context.data_context.as_of

    worker_by_node = {w.source_node_id: w.target_worker_id for w in mapping.worker_mappings}
    # every mapped node must resolve to a catalog worker; params come from base inputs.
    node_ids = list(worker_by_node)
    node_set = set(node_ids)

    # -- dependency policy per (upstream, downstream) edge ----------------- #
    edge_policy: dict[tuple[str, str], DependencyPolicy] = {}
    succ: dict[str, set[str]] = {}
    for d in mapping.dependency_mappings:
        edge_policy[(d.source_upstream_node_id, d.source_downstream_node_id)] = d.target_policy
        succ.setdefault(d.source_upstream_node_id, set()).add(d.source_downstream_node_id)

    def _is_ancestor(up: str, down: str) -> bool:
        seen: set[str] = set()
        stack = [up]
        while stack:
            cur = stack.pop()
            for nxt in succ.get(cur, ()):
                if nxt == down:
                    return True
                if nxt not in seen:
                    seen.add(nxt)
                    stack.append(nxt)
        return False

    # -- resolve inputs per consumer node ---------------------------------- #
    params_by_node: dict[str, dict[str, Any]] = {n: {} for n in node_ids}
    deps_by_node: dict[str, list[Dependency]] = {n: [] for n in node_ids}
    for im in mapping.input_mappings:
        consumer = im.source_consumer_node_id
        if consumer not in node_set:
            raise _AdapterError(
                f"input {im.source_key!r} names consumer {consumer!r} with no worker mapping")
        if im.source_kind == "base":
            if im.target_kind == "param":
                params_by_node[consumer][im.target_param_key] = _base_param_value(im)
            elif im.target_kind == "context":
                # a context-targeted base value is supplied by the ContextSnapshot,
                # never a plan param/caller path — nothing to place on the node.
                continue
            elif im.target_kind == "service_binding":
                # supplied by the service (e.g. out_dir → output_locator); never a
                # plan/caller path. Nothing is placed on the node.
                continue
            else:  # pragma: no cover - matrix-guarded upstream base target
                raise _AdapterError(
                    f"base input {im.source_key!r} has unexpected target_kind {im.target_kind!r}")
        else:  # upstream → input_binding
            up = im.source_upstream_node_id
            if up not in node_set:
                raise _AdapterError(
                    f"upstream input {im.source_key!r} references {up!r} with no worker mapping")
            pol = edge_policy.get((up, consumer))
            if pol is None:
                # a non-direct upstream must be a proven transitive ancestor; the
                # adapter never invents a dependency to satisfy it.
                if not _is_ancestor(up, consumer):
                    raise _AdapterError(
                        f"upstream input {im.source_key!r} of {consumer!r} references {up!r}, "
                        "which is not a direct dependency nor a proven transitive ancestor")
                continue
            # BLOCK accepts exactly {COMPLETED}; a DEGRADE/SKIP upstream is still
            # usable when it DEGRADED, so the success accept-set widens accordingly.
            accept = (
                frozenset({NodeStatus.COMPLETED}) if pol is DependencyPolicy.BLOCK
                else frozenset({NodeStatus.COMPLETED, NodeStatus.DEGRADED}))
            deps_by_node[consumer].append(Dependency(
                upstream_node_id=up, artifact_slot=f"slot-{up}",
                inject_as=im.target_input_binding, policy=pol, accept_statuses=accept))

    # -- assemble nodes in the mapping's declared order -------------------- #
    nodes: list[PlanNode] = []
    for node_id in node_ids:
        worker_id = worker_by_node[node_id]
        if worker_id is None:  # pragma: no cover - MAPPED graph guarantees a target
            raise _AdapterError(f"mapped node {node_id!r} has no target worker id")
        try:
            catalog.worker(worker_id)
        except CatalogMaterialError as exc:
            raise _AdapterError(
                f"node {node_id!r} target worker {worker_id!r} is not in the catalog: {exc}"
            ) from exc
        nodes.append(PlanNode(
            id=node_id, worker_id=worker_id, writes_slot=f"slot-{node_id}",
            params=params_by_node[node_id],
            dependencies=tuple(deps_by_node[node_id])))

    for sink in sink_mapping:
        if sink not in node_set:
            raise _AdapterError(f"sink node {sink!r} is not a mapped node")

    return PlanDraft(
        id=plan_id, run_id=run_id, request_id=request.request_id, phase="main",
        source=PlanSource.PRESET, goal=request.goal, as_of=as_of, mode=DataMode.ONLINE,
        context_snapshot_ref=PayloadRef(
            namespace="main", object_id="legacy-compat-ctx",
            content_digest=context.content_digest),
        nodes=tuple(nodes), sink_node_ids=tuple(sink_mapping),
        catalog_version=catalog.catalog_version, catalog_digest=catalog.catalog_digest,
        schema_registry_digest=schema_registry.registry_digest,
        approval_policy=ApprovalPolicy.REQUIRED,
        budget_request_tokens=int(budget_request["tokens"]),
        budget_request_llm_invocations=int(budget_request["llm_invocations"]),
        max_concurrency=int(budget_request.get("max_concurrency", 4)),
        legacy_source_schema=mapping.source_schema,
        legacy_source_config_digest=mapping.source_config_digest,
        legacy_mapping_digest=mapping.semantic_digest())


def _base_param_value(im) -> str:
    """The reviewed default param value for a base ``code`` / ``asof_date`` input.

    NOTE: exercised only by the synthetic param-branch unit fixture in
    ``test_presets.py``; the production core maps base inputs to context /
    service_binding targets, never to node params.
    """
    if im.source_key == "code":
        return "600519"
    if im.source_key == "asof_date":
        return "2026-07-16"
    return im.source_key


def attest_and_store_legacy_plan(
    mapping: LegacyGraphMapping,
    draft: PlanDraft,
    request: OrchestrationRequest,
    *,
    context: ContextSnapshot,
    catalog: WorkerCatalogSnapshot,
    schema_registry: Any,
    attestations: dict[str, StaticLegacyPlanAttestation],
) -> StaticLegacyPlanAttestation:
    """The service-only attestation path: build (Phase-1) + store by candidate digest.

    Delegates the entire build to Phase-1 :func:`attest_static_legacy_plan` (the
    single candidate-digest function) and stores the result in the service-owned
    ``attestations`` map keyed by ``candidate_plan_digest`` — exactly the key the
    admission service loads it under. The attestation grants neither AUTO approval
    nor dynamic selection.
    """
    attestation = attest_static_legacy_plan(
        mapping, draft, request, context=context, catalog=catalog,
        schema_registry=schema_registry)
    attestations[attestation.candidate_plan_digest] = attestation
    return attestation


# --------------------------------------------------------------------------- #
# The reviewed MAPPED stock-deep-dive compatibility mapping (row table)        #
# --------------------------------------------------------------------------- #
# Evidence-faithful core ONLY (user ruling: Option A — honest core; no interim
# invented schemas). The Task-0 fixture's per-node ``target_worker`` column plus
# its 24-worker table support exactly TWO legacy nodes whose planned worker has a
# REGISTERED Phase-1 output schema and the right execution kind:
#
#   news-sentiment -> text.sentiment    (llm, "SentimentReport (band/score/confidence)")
#   report-writer  -> dec.research_mgr  (llm, "ReportOutput -> ResearchPlan 5-band",
#                                        via the documented Phase-1 rating migration)
#
# Every other node's planned worker output schema is unregistered (it arrives with
# Phase 3 data/PIT) or its execution kind is deterministic; the frozen Phase-1
# ``MappingBasis`` vocabulary ("authoritative_code" / "approved_policy"+policy-id /
# "none") has no honest structural/stand-in label, so those nodes stay OUT of the
# core rather than carrying a fabricated basis. The two mirrors are
# migration-mediated (not byte-identical), so they are ``compat.*`` compatibility
# workers under attestation — never the final ids themselves. The core is connected:
# ``report-writer.soft_deps`` includes ``news-sentiment`` (one reviewed SOFT edge).
# ``normalized_raw_config`` remains the FULL 18-node config, so
# ``source_config_digest`` equals the frozen Task-0 fixture digest (invariant 1)
# and any legacy-config change still invalidates the attestation.
_SWARM_YAML = _CONFIG_ROOT.parent / "swarm" / "stock-deep-dive.yaml"
_ENGINE_SWARM_YAML = (
    Path(__file__).resolve().parents[2] / "engine" / "financial_analyst" / "_resources"
    / "config" / "swarm" / "stock-deep-dive.yaml"
)
_SR_SENTIMENT = SchemaRef(name="SentimentReport", version="1")
_SR_RESEARCH = SchemaRef(name="ResearchPlan", version="1")

_COMPAT_PROMPT_FILE = COMPAT_MATERIALS_DIR / "prompts" / "compat_mirror.md"
_COMPAT_SKILL_FILE = COMPAT_MATERIALS_DIR / "skills" / "compat_mirror.md"
_COMPAT_GUARD_FILE = COMPAT_MATERIALS_DIR / "guardrails" / "compat_discipline.md"


def _compat_id(node: str) -> str:
    return "compat." + node.replace("-", "_")


#: fixture evidence pointer: legacy node -> planned worker in the Task-0 table.
_CORE_FIXTURE_WORKER: dict[str, str] = {
    "news-sentiment": "text.sentiment",
    "report-writer": "dec.research_mgr",
}
#: legacy node -> its compat mirror's registered output schema (per the table).
_CORE_OUTPUT: dict[str, SchemaRef] = {
    "news-sentiment": _SR_SENTIMENT,
    "report-writer": _SR_RESEARCH,
}
_CORE_NODES: tuple[str, ...] = tuple(_CORE_OUTPUT)
#: lane / decision authority mirrored from the fixture worker table.
_CORE_LANE: dict[str, str] = {"news-sentiment": "text", "report-writer": "decision"}
_CORE_CAN_EMIT: dict[str, bool] = {"news-sentiment": False, "report-writer": True}

#: reviewed direct edges among the core: report-writer's dep on news-sentiment is
#: listed in the legacy ``soft_deps`` (fixture dependency_semantics: soft).
_CORE_EDGES: tuple[tuple[str, str, str], ...] = (
    ("news-sentiment", "report-writer", "soft"),
)

#: read categories mirrored from the fixture roles. NEITHER core node declares
#: ``memory_mode`` or ``borrows_memory`` in the legacy YAML, so (invariant 6) their
#: mirrors honestly carry NO memory read category and NO borrowed_from entry.
_CORE_READ_CATEGORIES: dict[str, tuple[str, ...]] = {
    "news-sentiment": ("context", "market_data"),
    "report-writer": ("context", "upstream_artifacts"),
}
_CORE_BORROWED: dict[str, tuple[str, ...]] = {}

_SOFT_ACCEPTED_STATUSES = (
    NodeStatus.COMPLETED, NodeStatus.DEGRADED, NodeStatus.INCOMPLETE,
    NodeStatus.FAILED, NodeStatus.TIMED_OUT, NodeStatus.SKIPPED, NodeStatus.CANCELLED,
)
_SERVICE_OUT_LOCATOR = "output_locator"


class _CoreInput(NamedTuple):
    """A reviewed compat input row: a worker input binding + its legacy input mapping."""

    binding: str
    schema: SchemaRef
    required: bool
    source: str            # upstream legacy node id
    missing_behavior: str
    projection: str = "raw"
    projection_field: str | None = None


def _up(binding, schema, source, *, required, missing, projection="raw", projection_field=None):
    return _CoreInput(binding, schema, required, source, missing, projection, projection_field)


#: reviewed per-consumer upstream input rows. ``report-writer``'s input on the
#: news-sentiment output is soft (DEGRADE) per the legacy YAML, so the mirror's
#: binding is optional and the reviewed missing behavior is ``omit``. Projection is
#: ``raw`` — the fixture records no single-field-unwrap/model_dump evidence.
_CORE_INPUTS: dict[str, tuple[_CoreInput, ...]] = {
    "news-sentiment": (),
    "report-writer": (
        _up("sentiment", _SR_SENTIMENT, "news-sentiment", required=False, missing="omit"),
    ),
}

#: reviewed per-consumer base (request-origin) input rows: (source_key, target_kind,
#: target) — both nodes' ``input_keys`` per the fixture normalized config.
_CORE_BASE_INPUTS: dict[str, tuple[tuple[str, str, str], ...]] = {
    "news-sentiment": (("code", "context", "code"), ("asof_date", "context", "asof_date")),
    "report-writer": (
        ("code", "context", "code"), ("asof_date", "context", "asof_date"),
        ("out_dir", "service_binding", _SERVICE_OUT_LOCATOR),
    ),
}


def _full_normalized_config() -> dict[str, Any]:
    """The 18-node ``{name, nodes}`` config whose digest equals the Task-0 fixture.

    Proves the repo swarm YAML normalizes identically to the engine copy before any
    attestation (the brief's pre-attestation requirement).
    """
    repo_text = _SWARM_YAML.read_text(encoding="utf-8")
    raw = yaml.safe_load(repo_text)
    if not _ENGINE_SWARM_YAML.exists():
        raise MigrationError(
            "engine copy of stock-deep-dive.yaml is missing; the repo/engine identity "
            "cannot be proven, so the legacy graph is not frozen and cannot be attested")
    eng_text = _ENGINE_SWARM_YAML.read_text(encoding="utf-8")
    if content_digest(normalize_legacy_graph_config(repo_text, source_format="yaml")) != \
            content_digest(normalize_legacy_graph_config(eng_text, source_format="yaml")):
        raise MigrationError(
            "repo swarm config does not normalize identically to the engine copy; "
            "the legacy graph is not frozen and cannot be attested")
    nodes = {
        a["name"]: {
            "deps": list(a.get("deps", []) or []),
            "soft_deps": list(a.get("soft_deps", []) or []),
            "input_keys": list(a.get("input_keys", []) or []),
        }
        for a in raw["agents"]
    }
    return {"name": raw["name"], "nodes": nodes}


def _worker_mapping_row(node: str, raw_node: Mapping[str, Any]) -> LegacyWorkerMapping:
    """One evidence-backed worker row: ``raw_node`` is the REAL normalized legacy node.

    ``authoritative_code`` basis is backed by the Task-0 fixture's per-node
    ``target_worker`` column (:data:`_CORE_FIXTURE_WORKER`); the compat mirror id
    wraps that planned worker because the mapping is migration-mediated, not
    byte-identical (invariant 3).
    """
    return LegacyWorkerMapping(
        source_node_id=node, raw_node=dict(raw_node), target_worker_id=_compat_id(node),
        mapping_status=MappingStatus.MAPPED, mapping_basis="authoritative_code",
        mapping_policy_id=None, reason=None)


def _dep_mapping_row(up: str, down: str, strength: str) -> LegacyDependencyMapping:
    if strength == "hard":
        return LegacyDependencyMapping(
            source_upstream_node_id=up, source_downstream_node_id=down,
            raw_edge={"strength": "hard"}, source_strength="hard",
            accepted_statuses=(NodeStatus.COMPLETED,), missing_output_behavior="block",
            target_policy=DependencyPolicy.BLOCK, mapping_status=MappingStatus.MAPPED,
            mapping_basis="authoritative_code", mapping_policy_id=None, reason=None)
    return LegacyDependencyMapping(
        source_upstream_node_id=up, source_downstream_node_id=down,
        raw_edge={"strength": "soft"}, source_strength="soft",
        accepted_statuses=_SOFT_ACCEPTED_STATUSES, missing_output_behavior="degrade",
        target_policy=DependencyPolicy.DEGRADE, mapping_status=MappingStatus.MAPPED,
        mapping_basis="authoritative_code", mapping_policy_id=None, reason=None)


def _base_input_row(consumer: str, source_key: str, target_kind: str, target: str) -> LegacyInputMapping:
    kw: dict[str, Any] = dict(
        source_consumer_node_id=consumer, source_key=source_key, source_kind="base",
        source_upstream_node_id=None, target_kind=target_kind, projection="raw",
        missing_behavior="error", mapping_status=MappingStatus.MAPPED,
        mapping_basis="authoritative_code", mapping_policy_id=None,
        mapping_evidence=f"reviewed base {source_key} -> {target_kind}", reason=None)
    if target_kind == "context":
        kw["target_context_field"] = target
    elif target_kind == "service_binding":
        kw["target_service_binding"] = target
    elif target_kind == "param":
        kw["target_param_key"] = target
    return LegacyInputMapping(**kw)


def _upstream_input_row(consumer: str, spec: _CoreInput) -> LegacyInputMapping:
    return LegacyInputMapping(
        source_consumer_node_id=consumer, source_key=spec.source, source_kind="upstream",
        source_upstream_node_id=spec.source, target_kind="input_binding",
        target_input_binding=spec.binding, upstream_output_schema_ref=spec.schema,
        projection=spec.projection, projection_field=spec.projection_field,
        missing_behavior=spec.missing_behavior, mapping_status=MappingStatus.MAPPED,
        mapping_basis="authoritative_code", mapping_policy_id=None,
        mapping_evidence=f"reviewed upstream {spec.source} -> {spec.binding}", reason=None)


def build_stock_deep_dive_compat_mapping() -> LegacyGraphMapping:
    """The reviewed, fully-MAPPED ``stock-deep-dive`` compatibility mapping.

    Its ``normalized_raw_config`` is the full 18-node config (``source_config_digest``
    equals the frozen Task-0 fixture); its worker/dependency/input rows cover ONLY
    the evidence-faithful connected core ``news-sentiment -> report-writer`` (see the
    row-table comment above for the audit).
    """
    cfg = _full_normalized_config()
    worker_mappings = tuple(
        _worker_mapping_row(n, cfg["nodes"][n]) for n in _CORE_NODES)
    dependency_mappings = tuple(_dep_mapping_row(up, down, s) for up, down, s in _CORE_EDGES)
    input_mappings: list[LegacyInputMapping] = []
    for node in _CORE_NODES:
        for src_key, tkind, tgt in _CORE_BASE_INPUTS.get(node, ()):
            input_mappings.append(_base_input_row(node, src_key, tkind, tgt))
        for spec in _CORE_INPUTS[node]:
            input_mappings.append(_upstream_input_row(node, spec))
    return LegacyGraphMapping(
        adapter_version=ADAPTER_VERSION, source_schema=SRC_STOCK_DEEP_DIVE,
        source_format="yaml", normalized_raw_config=cfg,
        source_config_digest=content_digest(cfg), worker_mappings=worker_mappings,
        dependency_mappings=dependency_mappings, input_mappings=tuple(input_mappings),
        mapping_status=MappingStatus.MAPPED, reason=None)


# --------------------------------------------------------------------------- #
# Compat catalog materials + worker specs                                     #
# --------------------------------------------------------------------------- #
def _compat_materials() -> dict[str, Any]:
    """Build the three shared compat materials (digests recomputed from bytes)."""
    p_raw = _COMPAT_PROMPT_FILE.read_bytes()
    s_raw = _COMPAT_SKILL_FILE.read_bytes()
    g_raw = _COMPAT_GUARD_FILE.read_bytes()
    p_ref, p_mat = build_text_material(id="compat.prompt.mirror", version="1", kind="prompt", raw=p_raw)
    s_ref, s_mat = build_text_material(id="compat.skill.mirror", version="1", kind="skill", raw=s_raw)
    g_ref, g_mat = build_text_material(id="compat.guard.discipline", version="1", kind="guardrail", raw=g_raw)
    parsed = parse_skill_v1(s_raw.decode("utf-8"))
    return dict(
        prompt_ref=p_ref, skill_ref=s_ref, guard_ref=g_ref,
        content_entries=(
            ContentManifestEntry(ref=p_ref, kind="prompt", name="Compat mirror prompt",
                                 description="Static-legacy compatibility mirror system prompt.",
                                 source_identity="guanlan.compat.mirror"),
            ContentManifestEntry(ref=g_ref, kind="guardrail", name="Compat discipline guardrail",
                                 description="Static-legacy compatibility discipline.",
                                 source_identity="guanlan.compat.discipline"),
        ),
        skill_entries=(SkillManifest(
            ref=s_ref, name=parsed.name, summary=parsed.summary, perfect_for=parsed.perfect_for,
            not_ideal_for=parsed.not_ideal_for,
            critical_data_source_heading="⚠️ CRITICAL: Data Source Priority",
            source_identity="guanlan.compat.mirror"),),
        text_materials=(p_mat, g_mat, s_mat))


def _compat_worker_spec(node: str, *, binding: CompatibilityBinding, mats) -> WorkerSpec:
    out_schema = _CORE_OUTPUT[node]
    inputs = tuple(
        InputBinding(name=i.binding, schema_ref=i.schema, required=i.required, cardinality="one")
        for i in _CORE_INPUTS[node])
    # lane / decision authority / execution kind mirror the fixture worker table
    # (both core planned workers are execution_kind=llm; dec.research_mgr can emit).
    can_emit = _CORE_CAN_EMIT[node]
    return WorkerSpec(
        id=_compat_id(node), catalog_role="compatibility", selection_scope="static_legacy_only",
        compatibility=binding, lane=_CORE_LANE[node], persona=f"legacy {node} mirror",
        tier=Tier.WRITER,
        execution=ExecutionSpec(kind=ExecutionKind.LLM, model_tier="reasoner"),
        system_prompt_ref=mats["prompt_ref"], skills=(SkillBinding(skill_ref=mats["skill_ref"]),),
        guardrail_refs=(mats["guard_ref"],), capability_allowlist=(),
        read_categories=_CORE_READ_CATEGORIES[node], inputs=inputs,
        outputs=(OutputBinding(name="primary", schema_ref=out_schema),),
        evidence_policy=EvidencePolicy(tool_calls=ToolCallRequirement.OPTIONAL),
        supported_modes=(DataMode.ONLINE,), can_emit_decision=can_emit,
        decision_authority="advisory_only" if can_emit else "none",
        borrowed_from=_CORE_BORROWED.get(node, ()))


def _compat_worker_specs(mats) -> tuple[WorkerSpec, ...]:
    binding = compatibility_binding_for(build_stock_deep_dive_compat_mapping())
    return tuple(_compat_worker_spec(n, binding=binding, mats=mats) for n in _CORE_NODES)


# --------------------------------------------------------------------------- #
# Cumulative end-of-Phase-2 static catalog snapshot + loader                   #
# --------------------------------------------------------------------------- #
def phase2_static_catalog_snapshot() -> WorkerCatalogSnapshot:
    """The reviewed cumulative end-of-Phase-2 catalog snapshot.

    Contains the unchanged three ``final`` pilot workers plus the complete Task-9
    ``compat.*`` compatibility subset (and every material both need), rebuilt through
    the Phase-1 material-aware builder. This is the sole canonical base for a Phase-3
    catalog extension; earlier pilot / compat digests stay resolvable for old Plans
    but are not alternative bases. Location / load-order independent: every material
    is addressed by ref identity.
    """
    pilot = load_pilot_catalog()
    p_snap = pilot.snapshot
    p_text, p_caps = pilot.resolved_materials()
    mats = _compat_materials()
    return build_catalog_snapshot(
        catalog_version="phase2-static-v1",
        content_manifest=tuple(p_snap.content_manifest) + mats["content_entries"],
        skill_manifest=tuple(p_snap.skill_manifest) + mats["skill_entries"],
        capability_manifest=tuple(p_snap.capability_manifest),
        workers=tuple(p_snap.workers) + _compat_worker_specs(mats),
        resolved_material=tuple(p_text) + tuple(p_caps) + mats["text_materials"])


def _static_catalog_source() -> InMemoryMaterialSource:
    pilot = load_pilot_catalog()
    p_text, p_caps = pilot.resolved_materials()
    mats = _compat_materials()
    text_bytes = {(m.ref.id, m.ref.version): m.raw_utf8 for m in p_text}
    for m in mats["text_materials"]:
        text_bytes[(m.ref.id, m.ref.version)] = m.raw_utf8
    caps = {(c.ref.id, c.ref.version): c.descriptor for c in p_caps}
    return InMemoryMaterialSource(text=text_bytes, capabilities=caps)


def load_compat_catalog(*, catalog_path: Path = COMPAT_CATALOG_PATH) -> CatalogRuntime:
    """Load the reviewed cumulative static catalog (pilot final + ``compat.*``) runtime.

    The compat workers embed a :class:`CompatibilityBinding` derived from the reviewed
    :func:`build_stock_deep_dive_compat_mapping`, so the snapshot is rebuilt through
    the Phase-1 material-aware builder from the reviewed mapping + on-disk materials.
    The ``stock-deep-dive-compat-v1.yaml`` manifest pins the resulting cumulative
    ``catalog_digest``; any material byte drift (or a manifest copy that no longer
    matches) recomputes a different digest and fails startup — location / load-order
    independent.
    """
    spec = yaml.safe_load(catalog_path.read_text(encoding="utf-8"))
    snapshot = phase2_static_catalog_snapshot()
    pinned = spec.get("catalog_digest")
    if pinned != snapshot.catalog_digest:
        raise CatalogMaterialError(
            "compat catalog digest drift: pinned "
            f"{pinned!r} != built {snapshot.catalog_digest!r}")
    return CatalogRuntime.build(snapshot, _static_catalog_source())


#: cached cumulative static-catalog digest (computed lazily on first access so the
#: pilot surface imports without the 9.2 compat machinery loaded).
_PHASE2_STATIC_CATALOG_DIGEST: str | None = None


def __getattr__(name: str) -> Any:  # module-level lazy attribute (PEP 562)
    if name == "PHASE2_STATIC_CATALOG_DIGEST":
        global _PHASE2_STATIC_CATALOG_DIGEST
        if _PHASE2_STATIC_CATALOG_DIGEST is None:
            _PHASE2_STATIC_CATALOG_DIGEST = phase2_static_catalog_snapshot().catalog_digest
        return _PHASE2_STATIC_CATALOG_DIGEST
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
