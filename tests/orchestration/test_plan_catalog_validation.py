# -*- coding: utf-8 -*-
"""Task 10 — I/O-free ``validate_plan_draft`` catalog / schema / evidence gate.

Written test-first (RED before ``spec.py`` exists). Locks the catalog-aware
rejections from ``task-10-brief.md`` that require a sealed
``WorkerCatalogSnapshot`` + ``SchemaRegistry`` + ``ContextSnapshot``:

* unknown worker; catalog / registry / catalog-version / context digest mismatch;
* strict Worker ``params_schema_ref`` validation incl. hidden
  ``handler/system_prompt/skills/tools/mcp/path`` keys;
* input-binding coverage / cardinality / slot / upstream-output / schema equality;
* unsupported mode; unauthorized decision sink; dependency policy that weakens
  ``EvidencePolicy``;
* wrong-kind condition / reducer / stop-condition / gate-metric refs;
* DYNAMIC/BOOTSTRAP compatibility-worker rejection and the
  PRESET-requires-one-matching-attestation rule;
* AUTO rejection for every source and the DYNAMIC approval-policy copy rule;
* the report binds the exact inputs it validated and has no PlanNode override
  field.

Run from repo root: ``pytest tests/orchestration/test_plan_catalog_validation.py -v``
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

import pytest

from guanlan_v2.orchestration.catalog import (
    CompatibilityBinding,
    ContentManifestEntry,
    EvidencePolicy,
    ExecutionSpec,
    InputBinding,
    OutputBinding,
    ResolvedTextMaterial,
    WorkerSpec,
    build_catalog_snapshot,
    catalog_material_digest,
)
from guanlan_v2.orchestration.context import (
    ClockSpec,
    ContextSnapshot,
    DataContext,
    build_empty_memory_binding,
)
from guanlan_v2.orchestration.digest import (
    DigestModel,
    FiniteFloat,
    NonEmptyStr,
    NonNegativeInt,
)
from guanlan_v2.orchestration.enums import (
    ApprovalPolicy,
    DataBackend,
    DataMode,
    DependencyPolicy,
    ExecutionKind,
    NodeStatus,
    PlanSource,
    Tier,
)
from guanlan_v2.orchestration.refs import ContentRef, PayloadRef, SchemaRef
from guanlan_v2.orchestration.schema_registry import SchemaRegistry
from guanlan_v2.orchestration.schemas import PortfolioDecision
from guanlan_v2.orchestration.spec import (
    DebateCfg,
    Dependency,
    GateCfg,
    OrchestrationRequest,
    PlanDraft,
    PlanNode,
    ReducerCfg,
    StaticLegacyPlanAttestation,
    validate_plan_draft,
)

UTC = timezone.utc
DA = "a" * 64
DB = "b" * 64
DC = "c" * 64
DD = "d" * 64


def _dt() -> datetime:
    return datetime(2026, 7, 15, 1, 30, tzinfo=UTC)


# --------------------------------------------------------------------------- #
# schema models + registry                                                    #
# --------------------------------------------------------------------------- #
class UpstreamOut(DigestModel):
    schema_version: Literal["1"] = "1"
    value: FiniteFloat = 1.0


class OtherOut(DigestModel):
    schema_version: Literal["1"] = "1"
    note: NonEmptyStr = "n"


class NodeParams(DigestModel):
    schema_version: Literal["1"] = "1"
    top_k: NonNegativeInt = 5
    label: NonEmptyStr = "x"


SR_UP = SchemaRef(name="UpstreamOut", version="1")
SR_OTHER = SchemaRef(name="OtherOut", version="1")
SR_PARAMS = SchemaRef(name="NodeParams", version="1")
SR_DEC = SchemaRef(name="PortfolioDecision", version="1")


def _registry() -> SchemaRegistry:
    reg = SchemaRegistry()
    reg.register(UpstreamOut)
    reg.register(OtherOut)
    reg.register(NodeParams)
    reg.register(PortfolioDecision)
    reg.seal()
    return reg


# --------------------------------------------------------------------------- #
# catalog builders                                                            #
# --------------------------------------------------------------------------- #
def make_text(id: str, kind: str, text: str):
    raw = text.encode("utf-8")
    tmp = ResolvedTextMaterial(
        ref=ContentRef(id=id, version="1", content_digest="0" * 64), kind=kind, raw_utf8=raw
    )
    digest = catalog_material_digest(tmp)
    ref = ContentRef(id=id, version="1", content_digest=digest)
    return ref, ResolvedTextMaterial(ref=ref, kind=kind, raw_utf8=raw)


def catalog_content(id: str, kind: str, text: str = "body"):
    ref, mat = make_text(id, kind, text)
    entry = ContentManifestEntry(
        ref=ref, kind=kind, name=id, description="d", source_identity="gl.src"
    )
    return ref, mat, entry


def make_worker(
    id: str,
    *,
    outputs,
    inputs=(),
    params_schema_ref=None,
    supported_modes=(DataMode.ONLINE,),
    can_emit_decision: bool = False,
    decision_authority: str = "none",
    evidence_policy=None,
    catalog_role: str = "final",
    selection_scope: str = "dynamic_allowed",
    compatibility=None,
    lane: str = "text",
):
    pref, pmat = make_text(f"{id}.prompt", "prompt", f"You are {id}.")
    pentry = ContentManifestEntry(
        ref=pref, kind="prompt", name=id, description="d", source_identity="gl.src"
    )
    worker = WorkerSpec(
        id=id,
        catalog_role=catalog_role,
        selection_scope=selection_scope,
        compatibility=compatibility,
        lane=lane,
        persona="p",
        tier=Tier.WRITER,
        execution=ExecutionSpec(kind=ExecutionKind.LLM, model_tier="reasoner"),
        system_prompt_ref=pref,
        inputs=tuple(inputs),
        outputs=tuple(outputs),
        params_schema_ref=params_schema_ref,
        evidence_policy=evidence_policy or EvidencePolicy(),
        supported_modes=tuple(supported_modes),
        can_emit_decision=can_emit_decision,
        decision_authority=decision_authority,
    )
    return worker, [pmat], [pentry]


def build_catalog(worker_bundles, *, extra=(), catalog_version="cat.v1"):
    workers, mats, content = [], [], []
    for w, wmats, wentries in worker_bundles:
        workers.append(w)
        mats += wmats
        content += wentries
    for _ref, mat, entry in extra:
        mats.append(mat)
        content.append(entry)
    return build_catalog_snapshot(
        catalog_version=catalog_version,
        content_manifest=tuple(content),
        skill_manifest=(),
        capability_manifest=(),
        workers=tuple(workers),
        resolved_material=tuple(mats),
    )


def _context() -> ContextSnapshot:
    binding = build_empty_memory_binding()
    clock = ClockSpec(as_of=_dt(), timezone="Asia/Shanghai", calendar_id="cn_a_share")
    dc = DataContext(
        as_of=_dt(),
        clock=clock,
        mode=DataMode.ONLINE,
        backend=DataBackend.LIVE,
        strict_pit=False,
        calendar_id="cn_a_share",
        resolved_vendor_chains={"prices": ("tushare",)},
        source_config_digest=DA,
        source_registry_digest=DB,
        routing_snapshot_digest=DC,
        data_snapshot_id="snap-1",
        data_snapshot_content_digest=DD,
        built_at=_dt(),
    )
    sel = PayloadRef(namespace="main", object_id="sel-1", content_digest=binding.past_context_hash)
    return ContextSnapshot.build(
        snapshot_id="ctx-1",
        data_context=dc,
        memory_snapshot_id="ms-1",
        memory_snapshot_hash=binding.snapshot_hash,
        past_context_hash=binding.past_context_hash,
        memory_selection_ref=sel,
        built_at=_dt(),
    )


def _request(**over) -> OrchestrationRequest:
    base = dict(
        request_id="req-1",
        goal="g",
        workflow="orchestrate_only",
        approval_policy=ApprovalPolicy.REQUIRED,
    )
    base.update(over)
    return OrchestrationRequest(**base)


def _primary(schema_ref):
    return OutputBinding(name="primary", schema_ref=schema_ref)


def _codes(report):
    return {i.code for i in report.issues}


# --------------------------------------------------------------------------- #
# standard happy scenario: up.worker -> sink.worker                           #
# --------------------------------------------------------------------------- #
def _chain_catalog():
    up = make_worker("up.worker", outputs=(_primary(SR_UP),))
    sink = make_worker(
        "sink.worker",
        inputs=(InputBinding(name="feed", schema_ref=SR_UP, required=True, cardinality="one"),),
        outputs=(_primary(SR_OTHER),),
        params_schema_ref=SR_PARAMS,
    )
    return build_catalog((up, sink))


def _chain_draft(catalog, registry, context, *, source=PlanSource.DYNAMIC, **node_over):
    up_node = PlanNode(id="up", worker_id="up.worker", writes_slot="su")
    sink_node = PlanNode(
        id="sink",
        worker_id="sink.worker",
        writes_slot="ss",
        params={"top_k": 3, "label": "y"},
        dependencies=(
            Dependency(
                upstream_node_id="up",
                artifact_slot="su",
                upstream_output_key="primary",
                inject_as="feed",
            ),
        ),
        **node_over,
    )
    ref = PayloadRef(namespace="main", object_id="ctx-obj", content_digest=context.content_digest)
    return PlanDraft(
        id="plan.x",
        run_id="run-1",
        request_id="req-1",
        phase="main",
        source=source,
        goal="g",
        as_of=_dt(),
        mode=DataMode.ONLINE,
        context_snapshot_ref=ref,
        nodes=(up_node, sink_node),
        sink_node_ids=("sink",),
        catalog_version=catalog.catalog_version,
        catalog_digest=catalog.catalog_digest,
        schema_registry_digest=registry.registry_digest,
        approval_policy=ApprovalPolicy.REQUIRED,
        budget_request_tokens=1000,
        budget_request_llm_invocations=2,
        max_concurrency=2,
    )


def _validate(draft, request, context, catalog, registry, attestation=None):
    return validate_plan_draft(
        draft,
        request=request,
        context=context,
        catalog=catalog,
        schema_registry=registry,
        legacy_attestation=attestation,
    )


# --------------------------------------------------------------------------- #
# 1. happy paths                                                              #
# --------------------------------------------------------------------------- #
def test_valid_main_draft_reports_valid():
    catalog, registry, context = _chain_catalog(), _registry(), _context()
    draft = _chain_draft(catalog, registry, context)
    report = _validate(draft, _request(), context, catalog, registry)
    assert report.valid, report.issues
    assert report.issues == ()
    assert report.candidate_plan_digest
    # report binds the exact inputs it validated
    assert report.request_digest == _request().semantic_digest()
    assert report.catalog_digest == catalog.catalog_digest
    assert report.schema_registry_digest == registry.registry_digest
    assert report.context_content_digest == context.content_digest


def test_valid_bootstrap_draft_reports_valid():
    up = make_worker("boot.worker", outputs=(_primary(SR_UP),))
    catalog = build_catalog((up,))
    registry = _registry()
    node = PlanNode(id="boot", worker_id="boot.worker", writes_slot="sb")
    draft = PlanDraft(
        id="plan.b",
        run_id="run-1",
        request_id="req-1",
        phase="bootstrap",
        source=PlanSource.BOOTSTRAP,
        goal="g",
        as_of=_dt(),
        mode=DataMode.ONLINE,
        context_snapshot_ref=None,
        nodes=(node,),
        sink_node_ids=("boot",),
        catalog_version=catalog.catalog_version,
        catalog_digest=catalog.catalog_digest,
        schema_registry_digest=registry.registry_digest,
        approval_policy=ApprovalPolicy.REQUIRED,
    )
    report = _validate(draft, _request(), None, catalog, registry)
    assert report.valid, report.issues
    assert report.context_content_digest is None


def test_report_valid_flag_matches_issue_emptiness():
    catalog, registry, context = _chain_catalog(), _registry(), _context()
    draft = _chain_draft(catalog, registry, context)
    good = _validate(draft, _request(), context, catalog, registry)
    assert good.valid and good.issues == ()
    bad = _validate(draft, _request(request_id="other"), context, catalog, registry)
    assert not bad.valid and bad.issues


# --------------------------------------------------------------------------- #
# 2. digest / identity binding rejections                                    #
# --------------------------------------------------------------------------- #
def test_unknown_worker_rejected():
    catalog, registry, context = _chain_catalog(), _registry(), _context()
    draft = _chain_draft(catalog, registry, context)
    bad = draft.model_copy(
        update={
            "nodes": (
                draft.nodes[0].model_copy(update={"worker_id": "ghost.worker"}),
                draft.nodes[1],
            )
        }
    )
    report = _validate(bad, _request(), context, catalog, registry)
    assert not report.valid and "unknown_worker" in _codes(report)


def test_catalog_digest_mismatch_rejected():
    catalog, registry, context = _chain_catalog(), _registry(), _context()
    draft = _chain_draft(catalog, registry, context).model_copy(
        update={"catalog_digest": "e" * 64}
    )
    report = _validate(draft, _request(), context, catalog, registry)
    assert not report.valid and "catalog_digest_mismatch" in _codes(report)


def test_schema_registry_digest_mismatch_rejected():
    catalog, registry, context = _chain_catalog(), _registry(), _context()
    draft = _chain_draft(catalog, registry, context).model_copy(
        update={"schema_registry_digest": "e" * 64}
    )
    report = _validate(draft, _request(), context, catalog, registry)
    assert not report.valid and "schema_registry_digest_mismatch" in _codes(report)


def test_catalog_version_mismatch_rejected():
    catalog, registry, context = _chain_catalog(), _registry(), _context()
    draft = _chain_draft(catalog, registry, context).model_copy(
        update={"catalog_version": "other.v"}
    )
    report = _validate(draft, _request(), context, catalog, registry)
    assert not report.valid and "catalog_version_mismatch" in _codes(report)


def test_request_id_mismatch_rejected():
    catalog, registry, context = _chain_catalog(), _registry(), _context()
    draft = _chain_draft(catalog, registry, context)
    report = _validate(draft, _request(request_id="different"), context, catalog, registry)
    assert not report.valid and "request_id_mismatch" in _codes(report)


def test_main_context_digest_mismatch_rejected():
    catalog, registry, context = _chain_catalog(), _registry(), _context()
    wrong_ref = PayloadRef(namespace="main", object_id="ctx-obj", content_digest="f" * 64)
    draft = _chain_draft(catalog, registry, context).model_copy(
        update={"context_snapshot_ref": wrong_ref}
    )
    report = _validate(draft, _request(), context, catalog, registry)
    assert not report.valid and "context_mismatch" in _codes(report)


def test_main_context_missing_rejected():
    catalog, registry, context = _chain_catalog(), _registry(), _context()
    draft = _chain_draft(catalog, registry, context)
    report = _validate(draft, _request(), None, catalog, registry)
    assert not report.valid and "main_context_missing" in _codes(report)


def test_bootstrap_context_present_rejected():
    up = make_worker("boot.worker", outputs=(_primary(SR_UP),))
    catalog, registry, context = build_catalog((up,)), _registry(), _context()
    node = PlanNode(id="boot", worker_id="boot.worker", writes_slot="sb")
    draft = PlanDraft(
        id="plan.b",
        run_id="run-1",
        request_id="req-1",
        phase="bootstrap",
        source=PlanSource.BOOTSTRAP,
        goal="g",
        as_of=_dt(),
        mode=DataMode.ONLINE,
        context_snapshot_ref=None,
        nodes=(node,),
        sink_node_ids=("boot",),
        catalog_version=catalog.catalog_version,
        catalog_digest=catalog.catalog_digest,
        schema_registry_digest=registry.registry_digest,
        approval_policy=ApprovalPolicy.REQUIRED,
    )
    report = _validate(draft, _request(), context, catalog, registry)
    assert not report.valid and "bootstrap_context_present" in _codes(report)


# --------------------------------------------------------------------------- #
# 3. params-schema (incl. hidden authority keys)                             #
# --------------------------------------------------------------------------- #
def test_valid_params_accepted():
    catalog, registry, context = _chain_catalog(), _registry(), _context()
    draft = _chain_draft(catalog, registry, context)
    assert _validate(draft, _request(), context, catalog, registry).valid


@pytest.mark.parametrize(
    "hidden_key",
    ["handler", "system_prompt", "skills", "tools", "mcp", "path"],
)
def test_hidden_authority_key_in_params_rejected(hidden_key):
    catalog, registry, context = _chain_catalog(), _registry(), _context()
    draft = _chain_draft(catalog, registry, context)
    poisoned = draft.nodes[1].model_copy(
        update={"params": {"top_k": 3, "label": "y", hidden_key: "prompts/evil.md"}}
    )
    bad = draft.model_copy(update={"nodes": (draft.nodes[0], poisoned)})
    report = _validate(bad, _request(), context, catalog, registry)
    assert not report.valid and "params_schema_violation" in _codes(report)


def test_params_forbidden_when_worker_has_no_params_schema():
    catalog, registry, context = _chain_catalog(), _registry(), _context()
    draft = _chain_draft(catalog, registry, context)
    # up.worker has no params_schema_ref; give its node params -> rejected
    poisoned = draft.nodes[0].model_copy(update={"params": {"x": 1}})
    bad = draft.model_copy(update={"nodes": (poisoned, draft.nodes[1])})
    report = _validate(bad, _request(), context, catalog, registry)
    assert not report.valid and "params_not_allowed" in _codes(report)


def test_params_wrong_type_rejected():
    catalog, registry, context = _chain_catalog(), _registry(), _context()
    draft = _chain_draft(catalog, registry, context)
    poisoned = draft.nodes[1].model_copy(update={"params": {"top_k": -5, "label": "y"}})
    bad = draft.model_copy(update={"nodes": (draft.nodes[0], poisoned)})
    report = _validate(bad, _request(), context, catalog, registry)
    assert not report.valid and "params_schema_violation" in _codes(report)


# --------------------------------------------------------------------------- #
# 4. input binding coverage / cardinality / schema                           #
# --------------------------------------------------------------------------- #
def test_required_input_unsatisfied_rejected():
    catalog, registry, context = _chain_catalog(), _registry(), _context()
    draft = _chain_draft(catalog, registry, context)
    # drop the sink's only dependency -> required "feed" is unsatisfied
    stripped = draft.nodes[1].model_copy(update={"dependencies": ()})
    bad = draft.model_copy(update={"nodes": (draft.nodes[0], stripped)})
    report = _validate(bad, _request(), context, catalog, registry)
    assert not report.valid and "required_input_unsatisfied" in _codes(report)


def test_unknown_inject_target_rejected():
    catalog, registry, context = _chain_catalog(), _registry(), _context()
    draft = _chain_draft(catalog, registry, context)
    dep = Dependency(
        upstream_node_id="up", artifact_slot="su", inject_as="not_a_binding"
    )
    rewired = draft.nodes[1].model_copy(update={"dependencies": (dep,)})
    bad = draft.model_copy(update={"nodes": (draft.nodes[0], rewired)})
    report = _validate(bad, _request(), context, catalog, registry)
    assert not report.valid and "unknown_inject_target" in _codes(report)


def test_unknown_upstream_output_key_rejected():
    catalog, registry, context = _chain_catalog(), _registry(), _context()
    draft = _chain_draft(catalog, registry, context)
    dep = Dependency(
        upstream_node_id="up",
        artifact_slot="su",
        upstream_output_key="secondary",
        inject_as="feed",
    )
    rewired = draft.nodes[1].model_copy(update={"dependencies": (dep,)})
    bad = draft.model_copy(update={"nodes": (draft.nodes[0], rewired)})
    report = _validate(bad, _request(), context, catalog, registry)
    assert not report.valid and "unknown_upstream_output" in _codes(report)


def test_input_schema_mismatch_rejected():
    # upstream primary is OtherOut but the downstream "feed" binding expects UpstreamOut
    up = make_worker("up.worker", outputs=(_primary(SR_OTHER),))
    sink = make_worker(
        "sink.worker",
        inputs=(InputBinding(name="feed", schema_ref=SR_UP, required=True),),
        outputs=(_primary(SR_OTHER),),
    )
    catalog = build_catalog((up, sink))
    registry, context = _registry(), _context()
    up_node = PlanNode(id="up", worker_id="up.worker", writes_slot="su")
    sink_node = PlanNode(
        id="sink",
        worker_id="sink.worker",
        writes_slot="ss",
        dependencies=(
            Dependency(upstream_node_id="up", artifact_slot="su", inject_as="feed"),
        ),
    )
    ref = PayloadRef(namespace="main", object_id="ctx-obj", content_digest=context.content_digest)
    draft = PlanDraft(
        id="plan.x", run_id="run-1", request_id="req-1", phase="main",
        source=PlanSource.DYNAMIC, goal="g", as_of=_dt(), mode=DataMode.ONLINE,
        context_snapshot_ref=ref, nodes=(up_node, sink_node), sink_node_ids=("sink",),
        catalog_version=catalog.catalog_version, catalog_digest=catalog.catalog_digest,
        schema_registry_digest=registry.registry_digest, approval_policy=ApprovalPolicy.REQUIRED,
    )
    report = _validate(draft, _request(), context, catalog, registry)
    assert not report.valid and "input_schema_mismatch" in _codes(report)


def test_duplicate_single_input_rejected():
    up1 = make_worker("up.one", outputs=(_primary(SR_UP),))
    up2 = make_worker("up.two", outputs=(_primary(SR_UP),))
    sink = make_worker(
        "sink.worker",
        inputs=(InputBinding(name="feed", schema_ref=SR_UP, required=True, cardinality="one"),),
        outputs=(_primary(SR_OTHER),),
    )
    catalog = build_catalog((up1, up2, sink))
    registry, context = _registry(), _context()
    n1 = PlanNode(id="uone", worker_id="up.one", writes_slot="s1")
    n2 = PlanNode(id="utwo", worker_id="up.two", writes_slot="s2")
    sink_node = PlanNode(
        id="sink", worker_id="sink.worker", writes_slot="ss",
        dependencies=(
            Dependency(upstream_node_id="uone", artifact_slot="s1", inject_as="feed"),
            Dependency(upstream_node_id="utwo", artifact_slot="s2", inject_as="feed"),
        ),
    )
    ref = PayloadRef(namespace="main", object_id="ctx-obj", content_digest=context.content_digest)
    draft = PlanDraft(
        id="plan.x", run_id="run-1", request_id="req-1", phase="main",
        source=PlanSource.DYNAMIC, goal="g", as_of=_dt(), mode=DataMode.ONLINE,
        context_snapshot_ref=ref, nodes=(n1, n2, sink_node), sink_node_ids=("sink",),
        catalog_version=catalog.catalog_version, catalog_digest=catalog.catalog_digest,
        schema_registry_digest=registry.registry_digest, approval_policy=ApprovalPolicy.REQUIRED,
    )
    report = _validate(draft, _request(), context, catalog, registry)
    assert not report.valid and "duplicate_single_input" in _codes(report)


def test_cardinality_many_accepts_multiple():
    up1 = make_worker("up.one", outputs=(_primary(SR_UP),))
    up2 = make_worker("up.two", outputs=(_primary(SR_UP),))
    sink = make_worker(
        "sink.worker",
        inputs=(InputBinding(name="feeds", schema_ref=SR_UP, required=True, cardinality="many"),),
        outputs=(_primary(SR_OTHER),),
    )
    catalog = build_catalog((up1, up2, sink))
    registry, context = _registry(), _context()
    n1 = PlanNode(id="uone", worker_id="up.one", writes_slot="s1")
    n2 = PlanNode(id="utwo", worker_id="up.two", writes_slot="s2")
    sink_node = PlanNode(
        id="sink", worker_id="sink.worker", writes_slot="ss",
        dependencies=(
            Dependency(upstream_node_id="uone", artifact_slot="s1", inject_as="feeds"),
            Dependency(upstream_node_id="utwo", artifact_slot="s2", inject_as="feeds"),
        ),
    )
    ref = PayloadRef(namespace="main", object_id="ctx-obj", content_digest=context.content_digest)
    draft = PlanDraft(
        id="plan.x", run_id="run-1", request_id="req-1", phase="main",
        source=PlanSource.DYNAMIC, goal="g", as_of=_dt(), mode=DataMode.ONLINE,
        context_snapshot_ref=ref, nodes=(n1, n2, sink_node), sink_node_ids=("sink",),
        catalog_version=catalog.catalog_version, catalog_digest=catalog.catalog_digest,
        schema_registry_digest=registry.registry_digest, approval_policy=ApprovalPolicy.REQUIRED,
    )
    report = _validate(draft, _request(), context, catalog, registry)
    assert report.valid, report.issues


# --------------------------------------------------------------------------- #
# 5. mode / decision sink / evidence policy                                  #
# --------------------------------------------------------------------------- #
def test_unsupported_mode_rejected():
    catalog, registry, context = _chain_catalog(), _registry(), _context()
    # workers support ONLINE only; ask for PIT_REPLAY
    draft = _chain_draft(catalog, registry, context).model_copy(
        update={"mode": DataMode.PIT_REPLAY}
    )
    report = _validate(draft, _request(), context, catalog, registry)
    assert not report.valid and "unsupported_mode" in _codes(report)


def test_unauthorized_decision_sink_rejected():
    dec = make_worker(
        "dec.worker",
        outputs=(_primary(SR_DEC),),
        can_emit_decision=False,
        decision_authority="none",
        lane="decision",
    )
    catalog, registry, context = build_catalog((dec,)), _registry(), _context()
    node = PlanNode(id="dec", worker_id="dec.worker", writes_slot="sd")
    ref = PayloadRef(namespace="main", object_id="ctx-obj", content_digest=context.content_digest)
    draft = PlanDraft(
        id="plan.x", run_id="run-1", request_id="req-1", phase="main",
        source=PlanSource.DYNAMIC, goal="g", as_of=_dt(), mode=DataMode.ONLINE,
        context_snapshot_ref=ref, nodes=(node,), sink_node_ids=("dec",),
        catalog_version=catalog.catalog_version, catalog_digest=catalog.catalog_digest,
        schema_registry_digest=registry.registry_digest, approval_policy=ApprovalPolicy.REQUIRED,
    )
    report = _validate(draft, _request(), context, catalog, registry)
    assert not report.valid and "unauthorized_decision_sink" in _codes(report)


def test_authorized_decision_sink_accepted():
    dec = make_worker(
        "dec.worker",
        outputs=(_primary(SR_DEC),),
        can_emit_decision=True,
        decision_authority="advisory_only",
        lane="decision",
    )
    catalog, registry, context = build_catalog((dec,)), _registry(), _context()
    node = PlanNode(id="dec", worker_id="dec.worker", writes_slot="sd")
    ref = PayloadRef(namespace="main", object_id="ctx-obj", content_digest=context.content_digest)
    draft = PlanDraft(
        id="plan.x", run_id="run-1", request_id="req-1", phase="main",
        source=PlanSource.DYNAMIC, goal="g", as_of=_dt(), mode=DataMode.ONLINE,
        context_snapshot_ref=ref, nodes=(node,), sink_node_ids=("dec",),
        catalog_version=catalog.catalog_version, catalog_digest=catalog.catalog_digest,
        schema_registry_digest=registry.registry_digest, approval_policy=ApprovalPolicy.REQUIRED,
    )
    report = _validate(draft, _request(), context, catalog, registry)
    assert report.valid, report.issues


def test_dependency_that_weakens_evidence_policy_rejected():
    up = make_worker("up.worker", outputs=(_primary(SR_UP),))
    strict = EvidencePolicy(require_input_refs=True, optional_data_may_degrade=False)
    sink = make_worker(
        "sink.worker",
        inputs=(InputBinding(name="feed", schema_ref=SR_UP, required=True),),
        outputs=(_primary(SR_OTHER),),
        evidence_policy=strict,
    )
    catalog = build_catalog((up, sink))
    registry, context = _registry(), _context()
    up_node = PlanNode(id="up", worker_id="up.worker", writes_slot="su")
    sink_node = PlanNode(
        id="sink", worker_id="sink.worker", writes_slot="ss",
        dependencies=(
            Dependency(
                upstream_node_id="up",
                artifact_slot="su",
                inject_as="feed",
                policy=DependencyPolicy.DEGRADE,
                accept_statuses=frozenset({NodeStatus.COMPLETED, NodeStatus.DEGRADED}),
            ),
        ),
    )
    ref = PayloadRef(namespace="main", object_id="ctx-obj", content_digest=context.content_digest)
    draft = PlanDraft(
        id="plan.x", run_id="run-1", request_id="req-1", phase="main",
        source=PlanSource.DYNAMIC, goal="g", as_of=_dt(), mode=DataMode.ONLINE,
        context_snapshot_ref=ref, nodes=(up_node, sink_node), sink_node_ids=("sink",),
        catalog_version=catalog.catalog_version, catalog_digest=catalog.catalog_digest,
        schema_registry_digest=registry.registry_digest, approval_policy=ApprovalPolicy.REQUIRED,
    )
    report = _validate(draft, _request(), context, catalog, registry)
    assert not report.valid and "dependency_weakens_evidence" in _codes(report)


# --------------------------------------------------------------------------- #
# 6. catalog-owned condition / reducer / stop / gate refs                    #
# --------------------------------------------------------------------------- #
def _refs_catalog():
    up = make_worker("up.worker", outputs=(_primary(SR_UP),))
    sink = make_worker(
        "sink.worker",
        inputs=(InputBinding(name="feed", schema_ref=SR_UP, required=True),),
        outputs=(_primary(SR_OTHER),),
    )
    cond = catalog_content("cond.a", "condition")
    red = catalog_content("reducer.a", "reducer")
    stop = catalog_content("stop.a", "stop_condition")
    gate = catalog_content("gate.metric", "gate_metric")
    catalog = build_catalog((up, sink), extra=(cond, red, stop, gate))
    return catalog, cond[0], red[0], stop[0], gate[0]


def _wire_refs_draft(catalog, registry, context, *, cond_ref, stop_refs, gate, reducer_ref):
    up_node = PlanNode(id="up", worker_id="up.worker", writes_slot="su", condition_ref=cond_ref)
    sink_node = PlanNode(
        id="sink", worker_id="sink.worker", writes_slot="ss", gate_ids=("gate.a",),
        dependencies=(
            Dependency(upstream_node_id="up", artifact_slot="su", inject_as="feed"),
        ),
    )
    gate_cfg = GateCfg(id="gate.a", metric=gate, operator=">=", threshold=0.5, scope="csi300")
    ref = PayloadRef(namespace="main", object_id="ctx-obj", content_digest=context.content_digest)
    return PlanDraft(
        id="plan.x", run_id="run-1", request_id="req-1", phase="main",
        source=PlanSource.DYNAMIC, goal="g", as_of=_dt(), mode=DataMode.ONLINE,
        context_snapshot_ref=ref, nodes=(up_node, sink_node), sink_node_ids=("sink",),
        gates=(gate_cfg,), stop_condition_refs=tuple(stop_refs),
        catalog_version=catalog.catalog_version, catalog_digest=catalog.catalog_digest,
        schema_registry_digest=registry.registry_digest, approval_policy=ApprovalPolicy.REQUIRED,
    )


def test_valid_catalog_owned_refs_accepted():
    catalog, cond, red, stop, gate = _refs_catalog()
    registry, context = _registry(), _context()
    draft = _wire_refs_draft(
        catalog, registry, context, cond_ref=cond, stop_refs=(stop,), gate=gate, reducer_ref=red
    )
    report = _validate(draft, _request(), context, catalog, registry)
    assert report.valid, report.issues


def test_wrong_kind_condition_ref_rejected():
    catalog, cond, red, stop, gate = _refs_catalog()
    registry, context = _registry(), _context()
    # use the reducer ref where a condition is expected
    draft = _wire_refs_draft(
        catalog, registry, context, cond_ref=red, stop_refs=(stop,), gate=gate, reducer_ref=red
    )
    report = _validate(draft, _request(), context, catalog, registry)
    assert not report.valid and "wrong_kind_condition_ref" in _codes(report)


def test_unknown_condition_ref_rejected():
    catalog, cond, red, stop, gate = _refs_catalog()
    registry, context = _registry(), _context()
    ghost = ContentRef(id="cond.ghost", version="1", content_digest=DA)
    draft = _wire_refs_draft(
        catalog, registry, context, cond_ref=ghost, stop_refs=(stop,), gate=gate, reducer_ref=red
    )
    report = _validate(draft, _request(), context, catalog, registry)
    assert not report.valid and "unknown_condition_ref" in _codes(report)


def test_wrong_kind_stop_condition_ref_rejected():
    catalog, cond, red, stop, gate = _refs_catalog()
    registry, context = _registry(), _context()
    draft = _wire_refs_draft(
        catalog, registry, context, cond_ref=cond, stop_refs=(cond,), gate=gate, reducer_ref=red
    )
    report = _validate(draft, _request(), context, catalog, registry)
    assert not report.valid and "wrong_kind_stop_condition_ref" in _codes(report)


def test_wrong_kind_gate_metric_ref_rejected():
    catalog, cond, red, stop, gate = _refs_catalog()
    registry, context = _registry(), _context()
    draft = _wire_refs_draft(
        catalog, registry, context, cond_ref=cond, stop_refs=(stop,), gate=cond, reducer_ref=red
    )
    report = _validate(draft, _request(), context, catalog, registry)
    assert not report.valid and "wrong_kind_gate_metric_ref" in _codes(report)


def test_wrong_kind_reducer_ref_rejected():
    up = make_worker("wa.worker", outputs=(_primary(SR_UP),))
    up2 = make_worker("wb.worker", outputs=(_primary(SR_UP),))
    sink = make_worker(
        "sink.worker",
        inputs=(InputBinding(name="feeds", schema_ref=SR_UP, required=True, cardinality="many"),),
        outputs=(_primary(SR_OTHER),),
    )
    cond = catalog_content("cond.a", "condition")
    catalog = build_catalog((up, up2, sink), extra=(cond,))
    registry, context = _registry(), _context()
    a = PlanNode(id="wa", worker_id="wa.worker", writes_slot="shared")
    b = PlanNode(id="wb", worker_id="wb.worker", writes_slot="shared")
    sink_node = PlanNode(
        id="sink", worker_id="sink.worker", writes_slot="ss",
        dependencies=(
            Dependency(upstream_node_id="wa", artifact_slot="shared", inject_as="feeds"),
            Dependency(upstream_node_id="wb", artifact_slot="shared", inject_as="feeds"),
        ),
    )
    # reducer_ref points at a condition-kind material -> wrong kind
    reducer = ReducerCfg(
        id="red.a", slot="shared", reducer_ref=cond[0],
        producer_node_ids=("wa", "wb"), output_schema_ref=SR_OTHER,
    )
    ref = PayloadRef(namespace="main", object_id="ctx-obj", content_digest=context.content_digest)
    draft = PlanDraft(
        id="plan.x", run_id="run-1", request_id="req-1", phase="main",
        source=PlanSource.DYNAMIC, goal="g", as_of=_dt(), mode=DataMode.ONLINE,
        context_snapshot_ref=ref, nodes=(a, b, sink_node), sink_node_ids=("sink",),
        reducers=(reducer,),
        catalog_version=catalog.catalog_version, catalog_digest=catalog.catalog_digest,
        schema_registry_digest=registry.registry_digest, approval_policy=ApprovalPolicy.REQUIRED,
    )
    report = _validate(draft, _request(), context, catalog, registry)
    assert not report.valid and "wrong_kind_reducer_ref" in _codes(report)


# --------------------------------------------------------------------------- #
# 7. AUTO + dynamic approval-policy copy                                      #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "source",
    [PlanSource.DYNAMIC, PlanSource.PRESET, PlanSource.BOOTSTRAP, PlanSource.PRESET_FALLBACK],
)
def test_auto_approval_rejected_for_every_source(source):
    catalog, registry, context = _chain_catalog(), _registry(), _context()
    draft = _chain_draft(catalog, registry, context, source=source).model_copy(
        update={"approval_policy": ApprovalPolicy.AUTO}
    )
    report = _validate(draft, _request(), context, catalog, registry)
    assert not report.valid and "auto_approval_rejected" in _codes(report)


def test_dynamic_draft_must_copy_request_approval_policy():
    # request REQUIRED, dynamic draft AUTO -> rejected (mismatch + AUTO)
    catalog, registry, context = _chain_catalog(), _registry(), _context()
    draft = _chain_draft(catalog, registry, context, source=PlanSource.DYNAMIC).model_copy(
        update={"approval_policy": ApprovalPolicy.AUTO}
    )
    report = _validate(draft, _request(approval_policy=ApprovalPolicy.REQUIRED), context, catalog, registry)
    assert not report.valid
    assert "auto_approval_rejected" in _codes(report)


def test_dynamic_draft_approval_policy_mismatch_isolated():
    # DYNAMIC draft REQUIRED vs request AUTO -> only the mismatch branch fires:
    # the draft is REQUIRED (no AUTO rejection, no not-required rejection), so this
    # isolates the dynamic_approval_policy_mismatch issue.
    catalog, registry, context = _chain_catalog(), _registry(), _context()
    draft = _chain_draft(catalog, registry, context, source=PlanSource.DYNAMIC)
    assert draft.approval_policy is ApprovalPolicy.REQUIRED
    report = _validate(draft, _request(approval_policy=ApprovalPolicy.AUTO), context, catalog, registry)
    codes = _codes(report)
    assert not report.valid
    assert "dynamic_approval_policy_mismatch" in codes
    assert "auto_approval_rejected" not in codes
    assert "dynamic_approval_policy_not_required" not in codes


def test_dynamic_draft_approval_policy_not_required_branch():
    # DYNAMIC draft AUTO with request also AUTO -> policies match (no mismatch) but
    # a DYNAMIC draft's approval_policy must be REQUIRED, so the not_required branch
    # fires (alongside the blanket auto_approval_rejected).
    catalog, registry, context = _chain_catalog(), _registry(), _context()
    draft = _chain_draft(catalog, registry, context, source=PlanSource.DYNAMIC).model_copy(
        update={"approval_policy": ApprovalPolicy.AUTO}
    )
    report = _validate(draft, _request(approval_policy=ApprovalPolicy.AUTO), context, catalog, registry)
    codes = _codes(report)
    assert not report.valid
    assert "dynamic_approval_policy_not_required" in codes
    assert "dynamic_approval_policy_mismatch" not in codes


# --------------------------------------------------------------------------- #
# 8. compatibility worker / static legacy attestation                        #
# --------------------------------------------------------------------------- #
LEGACY_SCHEMA = SchemaRef(name="LegacyConfig", version="1")
LEGACY_CONFIG_DIGEST = "1" * 64
LEGACY_MAPPING_DIGEST = "2" * 64


def _compat_binding():
    return CompatibilityBinding(
        legacy_source_schema=LEGACY_SCHEMA,
        source_config_digest=LEGACY_CONFIG_DIGEST,
        legacy_mapping_digest=LEGACY_MAPPING_DIGEST,
    )


def _compat_catalog():
    compat = make_worker(
        "compat.legacy",
        outputs=(_primary(SR_OTHER),),
        catalog_role="compatibility",
        selection_scope="static_legacy_only",
        compatibility=_compat_binding(),
        lane="decision",
    )
    return build_catalog((compat,))


def _compat_draft(catalog, registry, context, *, source, with_legacy_tuple: bool):
    node = PlanNode(id="legacy", worker_id="compat.legacy", writes_slot="sl")
    ref = None
    phase = "bootstrap"
    if source in (PlanSource.DYNAMIC, PlanSource.PRESET, PlanSource.PRESET_FALLBACK):
        phase = "main"
        ref = PayloadRef(namespace="main", object_id="ctx-obj", content_digest=context.content_digest)
    legacy = {}
    if with_legacy_tuple:
        legacy = dict(
            legacy_source_schema=LEGACY_SCHEMA,
            legacy_source_config_digest=LEGACY_CONFIG_DIGEST,
            legacy_mapping_digest=LEGACY_MAPPING_DIGEST,
        )
    return PlanDraft(
        id="plan.x", run_id="run-1", request_id="req-1", phase=phase,
        source=source, goal="g", as_of=_dt(), mode=DataMode.ONLINE,
        context_snapshot_ref=ref, nodes=(node,), sink_node_ids=("legacy",),
        catalog_version=catalog.catalog_version, catalog_digest=catalog.catalog_digest,
        schema_registry_digest=registry.registry_digest, approval_policy=ApprovalPolicy.REQUIRED,
        **legacy,
    )


def _attestation(catalog, request, draft, context, *, plan_source, **over):
    from guanlan_v2.orchestration.spec import compute_candidate_plan_digest

    ccd = context.content_digest if context is not None else None
    cand = compute_candidate_plan_digest(request=request, draft=draft, context_content_digest=ccd)
    base = dict(
        plan_source=plan_source,
        request_digest=request.semantic_digest(),
        candidate_plan_digest=cand,
        catalog_digest=catalog.catalog_digest,
        legacy_source_schema=LEGACY_SCHEMA,
        source_config_digest=LEGACY_CONFIG_DIGEST,
        legacy_mapping_digest=LEGACY_MAPPING_DIGEST,
        builder_id="legacy.builder",
    )
    base.update(over)
    return StaticLegacyPlanAttestation(**base)


def test_compat_worker_under_dynamic_rejected():
    catalog, registry, context = _compat_catalog(), _registry(), _context()
    draft = _compat_draft(catalog, registry, context, source=PlanSource.DYNAMIC, with_legacy_tuple=False)
    report = _validate(draft, _request(), context, catalog, registry)
    assert not report.valid and "compat_worker_forbidden_source" in _codes(report)


def test_compat_worker_under_bootstrap_rejected():
    catalog, registry = _compat_catalog(), _registry()
    draft = _compat_draft(catalog, registry, None, source=PlanSource.BOOTSTRAP, with_legacy_tuple=False)
    report = _validate(draft, _request(), None, catalog, registry)
    assert not report.valid and "compat_worker_forbidden_source" in _codes(report)


def test_compat_worker_under_preset_without_attestation_rejected():
    catalog, registry, context = _compat_catalog(), _registry(), _context()
    draft = _compat_draft(catalog, registry, context, source=PlanSource.PRESET, with_legacy_tuple=True)
    report = _validate(draft, _request(), context, catalog, registry, attestation=None)
    assert not report.valid and "compat_attestation_required" in _codes(report)


def test_compat_worker_under_preset_with_matching_attestation_accepted():
    catalog, registry, context = _compat_catalog(), _registry(), _context()
    request = _request()
    draft = _compat_draft(catalog, registry, context, source=PlanSource.PRESET, with_legacy_tuple=True)
    att = _attestation(catalog, request, draft, context, plan_source="preset")
    report = _validate(draft, request, context, catalog, registry, attestation=att)
    assert report.valid, report.issues
    assert report.legacy_attestation_digest == att.semantic_digest()


def test_compat_worker_under_preset_with_mismatched_attestation_rejected():
    catalog, registry, context = _compat_catalog(), _registry(), _context()
    request = _request()
    draft = _compat_draft(catalog, registry, context, source=PlanSource.PRESET, with_legacy_tuple=True)
    # attestation with a different source-config digest than the draft/binding
    att = _attestation(
        catalog, request, draft, context, plan_source="preset", source_config_digest="9" * 64
    )
    report = _validate(draft, request, context, catalog, registry, attestation=att)
    assert not report.valid and "compat_attestation_mismatch" in _codes(report)


# --------------------------------------------------------------------------- #
# 9. PlanNode has no override field in its JSON schema                        #
# --------------------------------------------------------------------------- #
def test_plannode_json_schema_has_no_authority_override_field():
    props = set(PlanNode.model_json_schema()["properties"].keys())
    forbidden = {
        "system_prompt", "system_prompt_ref", "prompt", "prompt_ref",
        "skills", "skill_refs", "tools", "capability_allowlist",
        "mcp", "mcp_servers", "handler", "handler_ref", "path",
    }
    assert props.isdisjoint(forbidden), props & forbidden
