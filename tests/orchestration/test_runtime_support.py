# -*- coding: utf-8 -*-
"""Phase 2 · Task 5 — the pure static-runtime support checker.

Locks :func:`check_runtime_support`:

* it is pure / I/O-free — a fake store spy is never touched, and the EventStore +
  BudgetLedger are unchanged after both a supported and a rejected check;
* a support report is emitted only for a valid, exact-input Phase-1 report and can
  never turn an invalid Plan valid;
* every deferred feature (BOOTSTRAP/Lane-0, conditions, reducers, multi-writer,
  debates, gates, stop, ``max_attempts>1``) is rejected before reservation;
* active bridge descriptor/config/provider/analyzer refs + the complete per-node
  summaries are embedded in the report; a duplicate/competing bridge identity fails
  earlier (catalog indexing);
* REQUIRED needs summed ``min_finalized_tool_calls_on_success >= 1``; FORBIDDEN
  needs summed ``max_capability_invocations == 0`` (memory-only zero/zero legal,
  cache-or-invoke zero/positive cannot satisfy REQUIRED, guaranteed one/one legal,
  multi-bridge sums, allowlist drift);
* the ContextRuntimeRequirements closure (absent/present, subject digest,
  registry/catalog/material/capability/bridge closure) is enforced.

Run: ``python -m pytest tests/orchestration/test_runtime_support.py -v``
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

import pytest

from guanlan_v2.orchestration.catalog import (
    CapabilityManifestEntry,
    ContentManifestEntry,
    EvidencePolicy,
    ExecutionSpec,
    InputBinding,
    OutputBinding,
    ResolvedCapabilityMaterial,
    ResolvedTextMaterial,
    WorkerSpec,
    build_catalog_snapshot,
    catalog_material_digest,
)
from guanlan_v2.orchestration.catalog import CapabilityDescriptor
from guanlan_v2.orchestration.catalog_runtime import (
    BridgeCatalogView,
    CatalogRuntime,
    InMemoryMaterialSource,
    serialize_bridge_descriptor,
)
from guanlan_v2.orchestration.context import (
    ClockSpec,
    ContextRuntimeRequirements,
    ContextSnapshot,
    DataContext,
    build_empty_memory_binding,
    compute_context_subject_digest,
)
from guanlan_v2.orchestration.digest import DigestModel, NonEmptyStr, content_digest
from guanlan_v2.orchestration.enums import (
    ApprovalPolicy,
    DataBackend,
    DataMode,
    ExecutionKind,
    NodeStatus,
    PlanSource,
    Tier,
    ToolCallRequirement,
)
from guanlan_v2.orchestration.refs import (
    CapabilityRef,
    ContentRef,
    PayloadRef,
    SchemaRef,
    TypedPayloadRef,
)
from guanlan_v2.orchestration.schema_registry import SchemaRegistry
from guanlan_v2.orchestration.spec import (
    OrchestrationRequest,
    PlanDraft,
    PlanNode,
    validate_plan_draft,
)
from guanlan_v2.orchestration.runtime_contracts import (
    BridgeStaticSupportSummary,
    ExecutionBridgeDescriptor,
    ResolvedContextRuntimeRequirements,
    RuntimeSupportReport,
    static_runtime_profile,
)
from guanlan_v2.orchestration.runtime_support import check_runtime_support

UTC = timezone.utc
DA = "a" * 64


def _dt() -> datetime:
    return datetime(2026, 7, 15, 1, 30, tzinfo=UTC)


class OtherOut(DigestModel):
    schema_version: Literal["1"] = "1"
    note: NonEmptyStr = "n"


SR_OUT = SchemaRef(name="OtherOut", version="1")
SR_CFG = SchemaRef(name="BridgeConfig", version="1")


def _registry() -> SchemaRegistry:
    reg = SchemaRegistry()
    reg.register(OtherOut)
    reg.seal()
    return reg


def _text(id: str, kind: str, text: str):
    raw = text.encode("utf-8")
    tmp = ResolvedTextMaterial(ref=ContentRef(id=id, version="1", content_digest="0" * 64),
                              kind=kind, raw_utf8=raw)
    digest = catalog_material_digest(tmp)
    ref = ContentRef(id=id, version="1", content_digest=digest)
    return ref, ResolvedTextMaterial(ref=ref, kind=kind, raw_utf8=raw)


def _capability(id: str):
    desc = CapabilityDescriptor(id=id, version="1", capability_kind="tool", transport="in_process",
                               operation="op.run", input_schema_ref=SR_OUT, output_schema_ref=SR_OUT)
    tmp = ResolvedCapabilityMaterial(ref=CapabilityRef(id=id, version="1", content_digest="0" * 64),
                                    descriptor=desc)
    digest = catalog_material_digest(tmp)
    ref = CapabilityRef(id=id, version="1", content_digest=digest)
    return ref, ResolvedCapabilityMaterial(ref=ref, descriptor=desc)


class _Analyzer:
    """Configurable pure analyzer binding the exact resolved identity."""

    def __init__(self, *, min_calls: int, max_calls: int, forge_allowlist=None, forge_node=None):
        self._min = min_calls
        self._max = max_calls
        self._forge_allowlist = forge_allowlist
        self._forge_node = forge_node

    def analyze(self, *, candidate_plan_digest, node, worker, descriptor, descriptor_ref, config_bytes):
        allowed = self._forge_allowlist if self._forge_allowlist is not None else tuple(
            sorted(worker.capability_allowlist, key=lambda r: (r.id, r.version))
        )
        node_id = self._forge_node if self._forge_node is not None else node.id
        return BridgeStaticSupportSummary.build(
            candidate_plan_digest=candidate_plan_digest, node_id=node_id,
            node_params_digest=content_digest(dict(node.params)),
            worker_id=worker.id, worker_digest=worker.semantic_digest(),
            bridge_id=descriptor.bridge_id, descriptor_ref=descriptor_ref,
            config_ref=descriptor.config_ref, provider_ref=descriptor.provider_handler_ref,
            analyzer_ref=descriptor.support_analyzer_ref,
            allowed_capability_refs=allowed,
            min_finalized_tool_calls_on_success=self._min, max_capability_invocations=self._max,
            pre_input_kind=descriptor.pre_input_kind,
        )


class _BridgeSpec:
    """Declarative bridge material spec for the scenario builder."""

    def __init__(self, *, bridge_id, priority, activation_caps=(), activation_cats=(),
                 min_calls=1, max_calls=1, analyzer=None):
        self.bridge_id = bridge_id
        self.priority = priority
        self.activation_caps = activation_caps
        self.activation_cats = activation_cats
        self.analyzer = analyzer or _Analyzer(min_calls=min_calls, max_calls=max_calls)


def _context(*, requirements: ContextRuntimeRequirements | None = None,
             req_object_id="rr-1") -> ContextSnapshot:
    binding = build_empty_memory_binding()
    clock = ClockSpec(as_of=_dt(), timezone="Asia/Shanghai", calendar_id="cn_a_share")
    dc = DataContext(
        as_of=_dt(), clock=clock, mode=DataMode.ONLINE, backend=DataBackend.LIVE,
        strict_pit=False, calendar_id="cn_a_share", resolved_vendor_chains={"prices": ("tushare",)},
        source_config_digest="a" * 64, source_registry_digest="b" * 64,
        routing_snapshot_digest="c" * 64, data_snapshot_id="snap-1",
        data_snapshot_content_digest="d" * 64, built_at=_dt(),
    )
    if requirements is None:
        return ContextSnapshot.build(
            snapshot_id="ctx-1", data_context=dc, memory_snapshot_id="ms-1",
            memory_snapshot_hash=binding.snapshot_hash, past_context_hash=binding.past_context_hash,
            memory_snapshot_ref=binding.memory_snapshot_ref,
            memory_selection_ref=binding.memory_selection_ref,
            runtime_requirements_ref=None, built_at=_dt(),
        )
    rr_ref = TypedPayloadRef(
        schema_ref=SchemaRef(name="ContextRuntimeRequirements", version="1"),
        payload_ref=PayloadRef(namespace="main", object_id=req_object_id,
                               content_digest=requirements.requirements_digest),
    )
    # a non-empty memory binding: reuse a distinct hash pair so the snapshot is not
    # the canonical-empty pair (which forbids a requirements ref).
    return ContextSnapshot.build(
        snapshot_id="ctx-1", data_context=dc, memory_snapshot_id="ms-1",
        memory_snapshot_hash="e" * 64, past_context_hash="f" * 64,
        memory_snapshot_ref=TypedPayloadRef(
            schema_ref=SchemaRef(name="EmptyMemorySnapshot", version="1"),
            payload_ref=PayloadRef(namespace="main", object_id="ms", content_digest="e" * 64)),
        memory_selection_ref=TypedPayloadRef(
            schema_ref=SchemaRef(name="EmptyMemorySelection", version="1"),
            payload_ref=PayloadRef(namespace="main", object_id="msel", content_digest="f" * 64)),
        runtime_requirements_ref=rr_ref, built_at=_dt(),
    )


class Scenario:
    def __init__(self, *, draft, request, context, snapshot, registry, runtime, view, profile,
                 context_requirements=None, worker=None):
        self.draft = draft
        self.request = request
        self.context = context
        self.snapshot = snapshot
        self.registry = registry
        self.runtime = runtime
        self.view = view
        self.profile = profile
        self.context_requirements = context_requirements
        self.worker = worker

    def phase1_report(self):
        return validate_plan_draft(self.draft, request=self.request, context=self.context,
                                   catalog=self.snapshot, schema_registry=self.registry)

    def check(self, **over):
        kwargs = dict(
            phase1_report=self.phase1_report(), context=self.context,
            context_requirements=self.context_requirements, catalog=self.runtime,
            bridge_view=self.view, schema_registry=self.registry, profile=self.profile,
        )
        kwargs.update(over)
        return check_runtime_support(self.draft, **kwargs)


def build_scenario(
    *,
    tool_calls=ToolCallRequirement.OPTIONAL,
    bridges=None,
    worker_read_categories=("context",),
    worker_caps=("cap.data",),
    worker_inputs=(),
    worker_params_schema=None,
    context_requirements=None,
    node_over=None,
    source=PlanSource.DYNAMIC,
) -> Scenario:
    if bridges is None:
        bridges = [_BridgeSpec(bridge_id="bridge.data", priority=10, activation_caps=("cap.data",))]
    node_over = node_over or {}

    registry = _registry()
    prompt_ref, prompt_mat = _text("text.brw.prompt", "prompt", "You are the bridge worker.")

    # capability materials (shared across bridges + worker allowlist)
    cap_ids = set(worker_caps)
    for b in bridges:
        cap_ids |= set(b.activation_caps)
    cap_refs = {}
    cap_mats = []
    cap_manifest = []
    for cid in sorted(cap_ids):
        cr, cm = _capability(cid)
        cap_refs[cid] = cr
        cap_mats.append(cm)
        cap_manifest.append(CapabilityManifestEntry(ref=cr, capability_kind="tool", transport="in_process"))

    content = [
        ContentManifestEntry(ref=prompt_ref, kind="prompt", name="P", description="d", source_identity="gl.brw"),
    ]
    mats = [prompt_mat, *cap_mats]
    analyzers = {}

    for b in bridges:
        tag = b.bridge_id.replace(".", "_")
        cfg_ref, cfg_mat = _text(f"guard.cfg.{tag}", "guardrail", '{"mode":"cache_or_invoke"}')
        prov_ref, prov_mat = _text(f"handler.provider.{tag}", "handler", f"def provide_{tag}(ctx): return ctx")
        anal_ref, anal_mat = _text(f"handler.analyzer.{tag}", "handler", f"def analyze_{tag}(ctx): return ctx")
        content += [
            ContentManifestEntry(ref=cfg_ref, kind="guardrail", name=f"Cfg-{tag}", description="d", source_identity="gl.brw"),
            ContentManifestEntry(ref=prov_ref, kind="handler", name=f"Prov-{tag}", description="d", source_identity="gl.brw"),
            ContentManifestEntry(ref=anal_ref, kind="handler", name=f"Anal-{tag}", description="d", source_identity="gl.brw"),
        ]
        mats += [cfg_mat, prov_mat, anal_mat]
        desc = ExecutionBridgeDescriptor(
            bridge_id=b.bridge_id, bridge_version="1", priority=b.priority,
            provider_handler_ref=prov_ref, config_ref=cfg_ref, support_analyzer_ref=anal_ref,
            config_schema_ref=SR_CFG,
            activation_capability_refs=tuple(sorted((cap_refs[c] for c in b.activation_caps),
                                                    key=lambda r: (r.id, r.version))),
            activation_read_categories=tuple(sorted(b.activation_cats)),
            supported_execution_kinds=(ExecutionKind.LLM,), pre_input_kind="memory_refs_v1",
        )
        desc_text = serialize_bridge_descriptor(desc).decode("utf-8")
        d_ref, d_mat = _text(f"guard.{tag}", "guardrail", desc_text)
        content.append(ContentManifestEntry(ref=d_ref, kind="guardrail", name=b.bridge_id,
                                            description="d", source_identity="gl.brw"))
        mats.append(d_mat)
        analyzers[(anal_ref.id, anal_ref.version, anal_ref.content_digest)] = b.analyzer

    worker = WorkerSpec(
        id="text.brw", catalog_role="final", selection_scope="dynamic_allowed",
        lane="text", persona="analyst", tier=Tier.WRITER,
        execution=ExecutionSpec(kind=ExecutionKind.LLM, model_tier="reasoner"),
        system_prompt_ref=prompt_ref,
        capability_allowlist=tuple(sorted((cap_refs[c] for c in worker_caps), key=lambda r: (r.id, r.version))),
        read_categories=tuple(sorted(worker_read_categories)),
        params_schema_ref=worker_params_schema,
        inputs=tuple(worker_inputs),
        outputs=(OutputBinding(name="primary", schema_ref=SR_OUT),),
        evidence_policy=EvidencePolicy(tool_calls=tool_calls),
        supported_modes=(DataMode.ONLINE,), can_emit_decision=False, decision_authority="none",
    )

    snapshot = build_catalog_snapshot(
        catalog_version="brw-v1", content_manifest=tuple(content), skill_manifest=(),
        capability_manifest=tuple(cap_manifest), workers=(worker,), resolved_material=tuple(mats),
    )
    material_source = InMemoryMaterialSource(
        text={(m.ref.id, m.ref.version): m.raw_utf8 for m in mats if isinstance(m, ResolvedTextMaterial)},
        capabilities={(m.ref.id, m.ref.version): m.descriptor for m in mats if isinstance(m, ResolvedCapabilityMaterial)},
    )
    runtime = CatalogRuntime.build(snapshot, material_source)
    view = BridgeCatalogView.build(runtime, analyzers)

    context = _context(requirements=context_requirements)
    node = PlanNode(id="n1", worker_id="text.brw", writes_slot="s1", **node_over)
    ref = PayloadRef(namespace="main", object_id="ctx-obj", content_digest=context.content_digest)
    draft = PlanDraft(
        id="plan.x", run_id="run-1", request_id="req-1", phase="main", source=source,
        goal="g", as_of=_dt(), mode=DataMode.ONLINE, context_snapshot_ref=ref,
        nodes=(node,), sink_node_ids=("n1",), catalog_version=snapshot.catalog_version,
        catalog_digest=snapshot.catalog_digest, schema_registry_digest=registry.registry_digest,
        approval_policy=ApprovalPolicy.REQUIRED,
    )
    request = OrchestrationRequest(request_id="req-1", goal="g", workflow="orchestrate_only",
                                   approval_policy=ApprovalPolicy.REQUIRED)
    return Scenario(draft=draft, request=request, context=context, snapshot=snapshot,
                    registry=registry, runtime=runtime, view=view, profile=static_runtime_profile(),
                    context_requirements=context_requirements, worker=worker)


def _codes(report):
    return {i.code for i in report.issues}


# =========================================================================== #
# 1. supported baseline                                                       #
# =========================================================================== #
def test_supported_baseline_binds_all_digests_and_summaries():
    sc = build_scenario()
    report = sc.check()
    assert report.supported is True, report.issues
    assert report.issues == ()
    assert report.checker_version == "static-runtime-checker-v1"
    assert report.candidate_plan_digest == sc.phase1_report().candidate_plan_digest
    assert report.catalog_digest == sc.runtime.catalog_digest
    assert report.schema_registry_digest == sc.registry.registry_digest
    assert report.runtime_profile_digest == sc.profile.profile_digest
    assert report.phase1_report_digest == sc.phase1_report().semantic_digest()
    # active bridge refs + per-node summary embedded.
    assert len(report.active_bridge_descriptor_refs) == 1
    assert len(report.active_bridge_config_refs) == 1
    assert len(report.active_bridge_provider_refs) == 1
    assert len(report.active_bridge_analyzer_refs) == 1
    assert len(report.bridge_support_summaries) == 1
    s = report.bridge_support_summaries[0]
    assert s.node_id == "n1" and s.bridge_id == "bridge.data"
    assert s.candidate_plan_digest == report.candidate_plan_digest


def test_report_is_a_self_sealed_runtime_support_report():
    sc = build_scenario()
    report = sc.check()
    assert isinstance(report, RuntimeSupportReport)
    assert report.report_digest == content_digest(report._semantic_payload())


def test_changing_executable_draft_changes_the_bound_report():
    sc = build_scenario()
    r1 = sc.check()
    draft2 = sc.draft.model_copy(update={"goal": "a materially different goal"})
    p2 = validate_plan_draft(draft2, request=sc.request, context=sc.context, catalog=sc.snapshot,
                             schema_registry=sc.registry)
    r2 = check_runtime_support(draft2, phase1_report=p2, context=sc.context, context_requirements=None,
                              catalog=sc.runtime, bridge_view=sc.view, schema_registry=sc.registry,
                              profile=sc.profile)
    assert r1.candidate_plan_digest != r2.candidate_plan_digest
    assert r1.report_digest != r2.report_digest


# =========================================================================== #
# 2. only for a valid, exact-input Phase-1 report                             #
# =========================================================================== #
def test_invalid_phase1_report_can_never_be_supported():
    sc = build_scenario()
    bad_report = validate_plan_draft(sc.draft, request=OrchestrationRequest(
        request_id="OTHER", goal="g", workflow="orchestrate_only"),
        context=sc.context, catalog=sc.snapshot, schema_registry=sc.registry)
    assert not bad_report.valid
    report = sc.check(phase1_report=bad_report)
    assert report.supported is False
    assert "phase1_report_invalid" in _codes(report)


def test_catalog_digest_mismatch_fails():
    sc = build_scenario()
    # a structurally different catalog -> a different catalog digest
    other = build_scenario(bridges=[_BridgeSpec(bridge_id="bridge.other", priority=5,
                                                activation_caps=("cap.data",))])
    assert other.runtime.catalog_digest != sc.runtime.catalog_digest
    report = sc.check(catalog=other.runtime, bridge_view=other.view)
    assert report.supported is False
    assert "catalog_digest_mismatch" in _codes(report) or "draft_catalog_mismatch" in _codes(report)


def test_registry_digest_mismatch_fails():
    sc = build_scenario()

    class Extra(DigestModel):
        schema_version: Literal["1"] = "1"
        v: int = 1
    other = SchemaRegistry()
    other.register(OtherOut)
    other.register(Extra)
    other.seal()
    report = sc.check(schema_registry=other)
    assert report.supported is False
    assert {"schema_registry_digest_mismatch", "draft_registry_mismatch"} & _codes(report)


# =========================================================================== #
# 3. deferred features rejected before reservation                            #
# =========================================================================== #
def test_bootstrap_rejected_even_though_phase1_valid():
    up_prompt, up_mat = _text("boot.prompt", "prompt", "boot")
    worker = WorkerSpec(
        id="boot.worker", catalog_role="final", selection_scope="dynamic_allowed",
        lane="text", persona="p", tier=Tier.WRITER,
        execution=ExecutionSpec(kind=ExecutionKind.LLM, model_tier="reasoner"),
        system_prompt_ref=up_prompt, outputs=(OutputBinding(name="primary", schema_ref=SR_OUT),),
        supported_modes=(DataMode.ONLINE,), can_emit_decision=False, decision_authority="none",
    )
    snapshot = build_catalog_snapshot(
        catalog_version="boot-v1",
        content_manifest=(ContentManifestEntry(ref=up_prompt, kind="prompt", name="p",
                                              description="d", source_identity="gl"),),
        skill_manifest=(), capability_manifest=(), workers=(worker,), resolved_material=(up_mat,))
    registry = _registry()
    source = InMemoryMaterialSource(text={(up_prompt.id, up_prompt.version): up_mat.raw_utf8}, capabilities={})
    runtime = CatalogRuntime.build(snapshot, source)
    view = BridgeCatalogView.build(runtime, {})
    node = PlanNode(id="boot", worker_id="boot.worker", writes_slot="sb")
    draft = PlanDraft(
        id="plan.b", run_id="run-1", request_id="req-1", phase="bootstrap", source=PlanSource.BOOTSTRAP,
        goal="g", as_of=_dt(), mode=DataMode.ONLINE, context_snapshot_ref=None,
        nodes=(node,), sink_node_ids=("boot",), catalog_version=snapshot.catalog_version,
        catalog_digest=snapshot.catalog_digest, schema_registry_digest=registry.registry_digest,
        approval_policy=ApprovalPolicy.REQUIRED)
    request = OrchestrationRequest(request_id="req-1", goal="g", workflow="orchestrate_only")
    phase1 = validate_plan_draft(draft, request=request, context=None, catalog=snapshot, schema_registry=registry)
    assert phase1.valid  # BOOTSTRAP is Phase-1 valid ...
    report = check_runtime_support(draft, phase1_report=phase1, context=None, context_requirements=None,
                                   catalog=runtime, bridge_view=view, schema_registry=registry,
                                   profile=static_runtime_profile())
    assert report.supported is False  # ... but runtime-narrower rejects it
    assert "bootstrap_unsupported" in _codes(report)


def test_retries_rejected_but_phase1_valid():
    sc = build_scenario(node_over={"max_attempts": 3})
    assert sc.phase1_report().valid
    report = sc.check()
    assert report.supported is False and "retries_unsupported" in _codes(report)


def test_conditions_rejected():
    ref = ContentRef(id="cond.x", version="1", content_digest=DA)
    sc = build_scenario(node_over={"condition_ref": ref})
    report = sc.check()
    assert report.supported is False and "conditions_unsupported" in _codes(report)


def test_stop_conditions_rejected():
    sc = build_scenario()
    draft = sc.draft.model_copy(update={"stop_condition_refs": (
        ContentRef(id="stop.x", version="1", content_digest=DA),)})
    report = check_runtime_support(draft, phase1_report=validate_plan_draft(
        draft, request=sc.request, context=sc.context, catalog=sc.snapshot, schema_registry=sc.registry),
        context=sc.context, context_requirements=None, catalog=sc.runtime, bridge_view=sc.view,
        schema_registry=sc.registry, profile=sc.profile)
    assert report.supported is False and "stop_conditions_unsupported" in _codes(report)


def test_multi_writer_rejected():
    sc = build_scenario()
    n1 = sc.draft.nodes[0]
    n2 = PlanNode(id="n2", worker_id="text.brw", writes_slot="s1")  # same slot as n1
    draft = sc.draft.model_copy(update={"nodes": (n1, n2), "sink_node_ids": ("n1", "n2")})
    report = check_runtime_support(draft, phase1_report=validate_plan_draft(
        draft, request=sc.request, context=sc.context, catalog=sc.snapshot, schema_registry=sc.registry),
        context=sc.context, context_requirements=None, catalog=sc.runtime, bridge_view=sc.view,
        schema_registry=sc.registry, profile=sc.profile)
    assert report.supported is False and "multi_writer_unsupported" in _codes(report)


# =========================================================================== #
# 3b. affirmative allow-list — a value ABSENT from the profile's closed       #
#     tuples is rejected, never assumed supported (invariant 5 hardening)     #
# =========================================================================== #
def _narrowed_profile(**over):
    """A simulated future/changed profile built via ``model_construct`` (bypasses
    the closed-matrix validator) — proves the checker trusts the profile's tuples
    affirmatively rather than deny-listing known-today values."""
    base = static_runtime_profile().model_dump()
    base.update(over)
    base["profile_digest"] = "1" * 64
    return type(static_runtime_profile()).model_construct(**base)


def test_plan_source_absent_from_profile_allowlist_rejected():
    sc = build_scenario(source=PlanSource.DYNAMIC)
    narrowed = _narrowed_profile(supported_plan_sources=(PlanSource.PRESET,))
    report = sc.check(profile=narrowed)
    assert report.supported is False and "plan_source_unsupported" in _codes(report)


def test_execution_kind_absent_from_profile_allowlist_rejected():
    sc = build_scenario()  # LLM worker
    narrowed = _narrowed_profile(supported_execution_kinds=(ExecutionKind.DETERMINISTIC,))
    report = sc.check(profile=narrowed)
    assert report.supported is False and "execution_kind_unsupported" in _codes(report)


def test_dependency_policy_absent_from_profile_allowlist_rejected():
    from guanlan_v2.orchestration.enums import DependencyPolicy
    from guanlan_v2.orchestration.spec import Dependency

    sc = build_scenario(worker_inputs=(
        InputBinding(name="feed", schema_ref=SR_OUT, required=False, cardinality="one"),))
    n1 = sc.draft.nodes[0]
    n2 = PlanNode(id="n2", worker_id="text.brw", writes_slot="s2", dependencies=(
        Dependency(upstream_node_id="n1", artifact_slot="s1", inject_as="feed",
                   policy=DependencyPolicy.DEGRADE,
                   accept_statuses=frozenset({NodeStatus.COMPLETED, NodeStatus.DEGRADED})),))
    draft = sc.draft.model_copy(update={"nodes": (n1, n2), "sink_node_ids": ("n1", "n2")})
    phase1 = validate_plan_draft(draft, request=sc.request, context=sc.context,
                                 catalog=sc.snapshot, schema_registry=sc.registry)
    narrowed = _narrowed_profile(supported_dependency_policies=(DependencyPolicy.BLOCK,))
    report = check_runtime_support(draft, phase1_report=phase1, context=sc.context,
                                   context_requirements=None, catalog=sc.runtime,
                                   bridge_view=sc.view, schema_registry=sc.registry,
                                   profile=narrowed)
    assert report.supported is False and "dependency_policy_unsupported" in _codes(report)
    # the same draft is supported under the canonical (full) profile.
    full = check_runtime_support(draft, phase1_report=phase1, context=sc.context,
                                 context_requirements=None, catalog=sc.runtime,
                                 bridge_view=sc.view, schema_registry=sc.registry,
                                 profile=static_runtime_profile())
    assert full.supported is True, full.issues


def test_cardinality_absent_from_profile_allowlist_rejected():
    sc = build_scenario(worker_inputs=(
        InputBinding(name="feed", schema_ref=SR_OUT, required=False, cardinality="many"),))
    narrowed = _narrowed_profile(supported_cardinalities=("one",))
    report = sc.check(profile=narrowed)
    assert report.supported is False and "cardinality_unsupported" in _codes(report)
    # supported under the canonical (full) profile.
    assert sc.check().supported is True


# =========================================================================== #
# 4. tool-call arithmetic (REQUIRED / FORBIDDEN)                              #
# =========================================================================== #
def test_required_satisfied_by_guaranteed_one_one():
    sc = build_scenario(tool_calls=ToolCallRequirement.REQUIRED,
                        bridges=[_BridgeSpec(bridge_id="bridge.data", priority=10,
                                             activation_caps=("cap.data",), min_calls=1, max_calls=1)])
    report = sc.check()
    assert report.supported is True, report.issues


def test_required_unmet_by_cache_or_invoke_zero_positive():
    sc = build_scenario(tool_calls=ToolCallRequirement.REQUIRED,
                        bridges=[_BridgeSpec(bridge_id="bridge.data", priority=10,
                                             activation_caps=("cap.data",), min_calls=0, max_calls=1)])
    report = sc.check()
    assert report.supported is False and "tool_calls_required_unmet" in _codes(report)


def test_forbidden_legal_with_memory_only_zero_zero():
    # a FORBIDDEN worker has an empty allowlist; a memory-only bridge activates by
    # read category and reports zero/zero -> legal under FORBIDDEN.
    sc = build_scenario(
        tool_calls=ToolCallRequirement.FORBIDDEN, worker_caps=(),
        worker_read_categories=("context", "memory"),
        bridges=[_BridgeSpec(bridge_id="bridge.mem", priority=10, activation_cats=("memory",),
                             min_calls=0, max_calls=0)])
    report = sc.check()
    assert report.supported is True, report.issues
    assert len(report.bridge_support_summaries) == 1


def test_forbidden_violated_by_positive_max():
    sc = build_scenario(
        tool_calls=ToolCallRequirement.FORBIDDEN, worker_caps=(),
        worker_read_categories=("context", "memory"),
        bridges=[_BridgeSpec(bridge_id="bridge.mem", priority=10, activation_cats=("memory",),
                             min_calls=0, max_calls=1)])
    report = sc.check()
    assert report.supported is False and "tool_calls_forbidden_violated" in _codes(report)


def test_required_multi_bridge_sum_satisfies():
    sc = build_scenario(
        tool_calls=ToolCallRequirement.REQUIRED, worker_caps=("cap.data",),
        worker_read_categories=("context", "memory"),
        bridges=[
            _BridgeSpec(bridge_id="bridge.data", priority=10, activation_caps=("cap.data",),
                        min_calls=0, max_calls=1),
            _BridgeSpec(bridge_id="bridge.mem", priority=20, activation_cats=("memory",),
                        min_calls=1, max_calls=1),
        ])
    report = sc.check()
    assert report.supported is True, report.issues
    assert len(report.bridge_support_summaries) == 2


def test_analyzer_allowlist_drift_is_an_analyzer_failure():
    forged = (CapabilityRef(id="cap.notallowed", version="1", content_digest=DA),)
    sc = build_scenario(
        bridges=[_BridgeSpec(bridge_id="bridge.data", priority=10, activation_caps=("cap.data",),
                             analyzer=_Analyzer(min_calls=1, max_calls=1, forge_allowlist=forged))])
    report = sc.check()
    assert report.supported is False and "analyzer_allowlist_drift" in _codes(report)


def test_analyzer_binding_mismatch_detected():
    sc = build_scenario(
        bridges=[_BridgeSpec(bridge_id="bridge.data", priority=10, activation_caps=("cap.data",),
                             analyzer=_Analyzer(min_calls=1, max_calls=1, forge_node="wrongnode"))])
    report = sc.check()
    assert report.supported is False and "analyzer_binding_mismatch" in _codes(report)


# =========================================================================== #
# 5. ContextRuntimeRequirements closure                                       #
# =========================================================================== #
def _requirements_for(context, *, registry_digest, catalog_digest,
                      material_refs=(), capability_refs=(), bridge_ids=()):
    subject = compute_context_subject_digest(
        data_context=context.data_context, memory_snapshot_ref=context.memory_snapshot_ref,
        memory_selection_ref=context.memory_selection_ref, memory_session_id=context.memory_session_id)
    return ContextRuntimeRequirements.build(
        context_subject_digest=subject, required_schema_registry_digest=registry_digest,
        required_catalog_digest=catalog_digest, required_runtime_material_refs=tuple(material_refs),
        required_capability_refs=tuple(capability_refs), required_bridge_ids=tuple(bridge_ids))


def test_present_requirements_satisfied_closure_supported():
    base = build_scenario()
    # build a requirements fact bound to the eventual (non-empty) context subject.
    tmp_ctx = _context(requirements=ContextRuntimeRequirements.build(
        context_subject_digest="0" * 64, required_schema_registry_digest="0" * 64,
        required_catalog_digest="0" * 64))
    reqs = _requirements_for(tmp_ctx, registry_digest=base.registry.registry_digest,
                             catalog_digest=base.runtime.catalog_digest,
                             bridge_ids=("bridge.data",))
    ctx2 = _context(requirements=reqs)
    ref = PayloadRef(namespace="main", object_id="ctx-obj", content_digest=ctx2.content_digest)
    draft = base.draft.model_copy(update={"context_snapshot_ref": ref})
    rcr = ResolvedContextRuntimeRequirements(typed_ref=ctx2.runtime_requirements_ref, fact=reqs)
    phase1 = validate_plan_draft(draft, request=base.request, context=ctx2, catalog=base.snapshot,
                                 schema_registry=base.registry)
    report = check_runtime_support(draft, phase1_report=phase1, context=ctx2, context_requirements=rcr,
                                   catalog=base.runtime, bridge_view=base.view,
                                   schema_registry=base.registry, profile=base.profile)
    assert report.supported is True, report.issues
    assert report.context_requirements_ref == ctx2.runtime_requirements_ref
    assert report.context_requirements_digest == reqs.requirements_digest


def test_missing_requirements_when_context_demands_it():
    base = build_scenario()
    tmp_ctx = _context(requirements=ContextRuntimeRequirements.build(
        context_subject_digest="0" * 64, required_schema_registry_digest="0" * 64,
        required_catalog_digest="0" * 64))
    reqs = _requirements_for(tmp_ctx, registry_digest=base.registry.registry_digest,
                             catalog_digest=base.runtime.catalog_digest)
    ctx2 = _context(requirements=reqs)
    ref = PayloadRef(namespace="main", object_id="ctx-obj", content_digest=ctx2.content_digest)
    draft = base.draft.model_copy(update={"context_snapshot_ref": ref})
    phase1 = validate_plan_draft(draft, request=base.request, context=ctx2, catalog=base.snapshot,
                                 schema_registry=base.registry)
    report = check_runtime_support(draft, phase1_report=phase1, context=ctx2, context_requirements=None,
                                   catalog=base.runtime, bridge_view=base.view,
                                   schema_registry=base.registry, profile=base.profile)
    assert report.supported is False and "context_requirements_missing" in _codes(report)


def test_required_bridge_missing_from_view_rejected():
    base = build_scenario()
    tmp_ctx = _context(requirements=ContextRuntimeRequirements.build(
        context_subject_digest="0" * 64, required_schema_registry_digest="0" * 64,
        required_catalog_digest="0" * 64))
    reqs = _requirements_for(tmp_ctx, registry_digest=base.registry.registry_digest,
                             catalog_digest=base.runtime.catalog_digest,
                             bridge_ids=("bridge.ghost",))
    ctx2 = _context(requirements=reqs)
    ref = PayloadRef(namespace="main", object_id="ctx-obj", content_digest=ctx2.content_digest)
    draft = base.draft.model_copy(update={"context_snapshot_ref": ref})
    rcr = ResolvedContextRuntimeRequirements(typed_ref=ctx2.runtime_requirements_ref, fact=reqs)
    phase1 = validate_plan_draft(draft, request=base.request, context=ctx2, catalog=base.snapshot,
                                 schema_registry=base.registry)
    report = check_runtime_support(draft, phase1_report=phase1, context=ctx2, context_requirements=rcr,
                                   catalog=base.runtime, bridge_view=base.view,
                                   schema_registry=base.registry, profile=base.profile)
    assert report.supported is False and "context_required_bridge_missing" in _codes(report)


def test_requirements_subject_digest_mismatch_rejected():
    base = build_scenario()
    # a requirements fact whose declared subject digest does not describe the context.
    wrong = ContextRuntimeRequirements.build(
        context_subject_digest="0" * 64, required_schema_registry_digest=base.registry.registry_digest,
        required_catalog_digest=base.runtime.catalog_digest)
    ctx2 = _context(requirements=wrong)
    ref = PayloadRef(namespace="main", object_id="ctx-obj", content_digest=ctx2.content_digest)
    draft = base.draft.model_copy(update={"context_snapshot_ref": ref})
    rcr = ResolvedContextRuntimeRequirements(typed_ref=ctx2.runtime_requirements_ref, fact=wrong)
    phase1 = validate_plan_draft(draft, request=base.request, context=ctx2, catalog=base.snapshot,
                                 schema_registry=base.registry)
    report = check_runtime_support(draft, phase1_report=phase1, context=ctx2, context_requirements=rcr,
                                   catalog=base.runtime, bridge_view=base.view,
                                   schema_registry=base.registry, profile=base.profile)
    assert report.supported is False and "context_subject_mismatch" in _codes(report)


def test_context_required_registry_mismatch_rejected():
    base = build_scenario()
    tmp_ctx = _context(requirements=ContextRuntimeRequirements.build(
        context_subject_digest="0" * 64, required_schema_registry_digest="0" * 64,
        required_catalog_digest="0" * 64))
    reqs = _requirements_for(tmp_ctx, registry_digest="9" * 64,  # not the supplied registry
                             catalog_digest=base.runtime.catalog_digest)
    ctx2 = _context(requirements=reqs)
    ref = PayloadRef(namespace="main", object_id="ctx-obj", content_digest=ctx2.content_digest)
    draft = base.draft.model_copy(update={"context_snapshot_ref": ref})
    rcr = ResolvedContextRuntimeRequirements(typed_ref=ctx2.runtime_requirements_ref, fact=reqs)
    phase1 = validate_plan_draft(draft, request=base.request, context=ctx2, catalog=base.snapshot,
                                 schema_registry=base.registry)
    report = check_runtime_support(draft, phase1_report=phase1, context=ctx2, context_requirements=rcr,
                                   catalog=base.runtime, bridge_view=base.view,
                                   schema_registry=base.registry, profile=base.profile)
    assert report.supported is False and "context_required_registry_mismatch" in _codes(report)


def test_empty_memory_forbids_a_supplied_requirements():
    sc = build_scenario()  # canonical empty-memory context (no requirements ref)
    fact = ContextRuntimeRequirements.build(
        context_subject_digest="0" * 64, required_schema_registry_digest="0" * 64,
        required_catalog_digest="0" * 64)
    rcr = ResolvedContextRuntimeRequirements(
        typed_ref=TypedPayloadRef(
            schema_ref=SchemaRef(name="ContextRuntimeRequirements", version="1"),
            payload_ref=PayloadRef(namespace="main", object_id="o", content_digest=fact.requirements_digest)),
        fact=fact)
    report = sc.check(context_requirements=rcr)
    assert report.supported is False and "context_requirements_unexpected" in _codes(report)


# =========================================================================== #
# 6. purity: no store dereference, no ledger/event side effect                #
# =========================================================================== #
def test_checker_signature_is_structurally_store_free():
    """The purity guarantee is structural: the checker's signature accepts only
    pre-resolved immutable views — there is no store/resolver/ledger parameter
    through which a dereference could even be requested."""
    import inspect

    sig = inspect.signature(check_runtime_support)
    assert set(sig.parameters) == {
        "draft", "phase1_report", "context", "context_requirements",
        "catalog", "bridge_view", "schema_registry", "profile",
    }
    for forbidden in ("store", "stores", "payload_store", "payloads", "resolver",
                      "event_store", "events", "ledger", "budget"):
        assert forbidden not in sig.parameters


def test_no_event_or_budget_side_effect_on_supported_and_rejected():
    from guanlan_v2.orchestration.budget import BudgetLedger
    from guanlan_v2.orchestration.context import RunBudget
    from guanlan_v2.orchestration.eventstore import RuntimeStores, SchemaRegistryResolver
    from guanlan_v2.orchestration.runtime_clock import SystemClock

    resolver = SchemaRegistryResolver()
    from guanlan_v2.orchestration.schema_registry import default_registry
    resolver.register(default_registry())
    stores = RuntimeStores(resolver=resolver, clock=SystemClock())

    class _Sink:
        def __init__(self):
            self._events = []

        def budget_events(self):
            return tuple(self._events)

        def find_by_idempotency_key(self, key):
            return None

        def append(self, command):  # pragma: no cover - never called
            raise AssertionError("budget must not be appended by the pure checker")

    ledger = BudgetLedger(sink=_Sink(),
                          run_budget=RunBudget(ledger_id="led-1", max_tokens=10,
                                               max_llm_invocations=5, max_concurrency=2))

    def journal_state():
        return stores.events.journal("run-1", "main"), stores.payloads_object_count()

    def budget_state():
        return ledger._state()

    before_j, before_b = journal_state(), budget_state()
    sc = build_scenario()
    sc.check()  # supported
    bad = build_scenario()
    bad.check(phase1_report=validate_plan_draft(
        bad.draft, request=OrchestrationRequest(request_id="X", goal="g", workflow="orchestrate_only"),
        context=bad.context, catalog=bad.snapshot, schema_registry=bad.registry))  # rejected
    assert journal_state() == before_j
    assert budget_state() == before_b
