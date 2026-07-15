# -*- coding: utf-8 -*-
"""Task 10 — OrchestrationRequest, single candidate digest and Plan freeze.

Written test-first (RED before ``spec.py`` exists). Locks, from
``task-10-brief.md``:

* the request workflow / existing-ref matrix, REQUIRED default and typed
  ``decision_schedule_ref``;
* one ``compute_candidate_plan_digest`` whose value moves on params / node /
  dependency / context-content / catalog / registry / budget changes but not on
  audit facts;
* ``freeze_plan`` re-runs ``validate_plan_draft`` and requires the supplied
  report to equal the recomputed result — a forged ``valid=True`` report or a
  report bound to different inputs never freezes;
* report + reservation + APPROVED approval + final Plan all bind ONE candidate
  digest; missing / rejected / mismatched approval blocks REQUIRED freeze; AUTO
  always fails; freeze wall-clock / reservation id do not move ``plan_digest``;
* the persisted Plan exposes every binding needed to recompute its digest and
  rejects a declared-digest mismatch.

Run from repo root: ``pytest tests/orchestration/test_spec.py -v``
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

import pytest
from pydantic import ValidationError

from guanlan_v2.orchestration.catalog import (
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
    BudgetReservation,
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
    ApprovalDecision,
    ApprovalPolicy,
    DataBackend,
    DataMode,
    ExecutionKind,
    PlanSource,
    Tier,
)
from guanlan_v2.orchestration.events import PlanApproval
from guanlan_v2.orchestration.refs import ContentRef, PayloadRef, SchemaRef
from guanlan_v2.orchestration.schema_registry import SchemaRegistry
from guanlan_v2.orchestration.spec import (
    Dependency,
    OrchestrationRequest,
    Plan,
    PlanDraft,
    PlanFreezeError,
    PlanNode,
    PlanValidationReport,
    compute_candidate_plan_digest,
    freeze_plan,
    validate_plan_draft,
)

UTC = timezone.utc
DA = "a" * 64
DB = "b" * 64
DC = "c" * 64
DD = "d" * 64


def _dt(minute: int = 30) -> datetime:
    return datetime(2026, 7, 15, 1, minute, tzinfo=UTC)


# --------------------------------------------------------------------------- #
# schemas / registry / catalog / context builders (self-contained)           #
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


def _registry() -> SchemaRegistry:
    reg = SchemaRegistry()
    reg.register(UpstreamOut)
    reg.register(OtherOut)
    reg.register(NodeParams)
    reg.seal()
    return reg


def _make_text(id: str, kind: str, text: str):
    raw = text.encode("utf-8")
    tmp = ResolvedTextMaterial(
        ref=ContentRef(id=id, version="1", content_digest="0" * 64), kind=kind, raw_utf8=raw
    )
    digest = catalog_material_digest(tmp)
    ref = ContentRef(id=id, version="1", content_digest=digest)
    return ref, ResolvedTextMaterial(ref=ref, kind=kind, raw_utf8=raw)


def _worker(id: str, *, outputs, inputs=(), params_schema_ref=None):
    pref, pmat = _make_text(f"{id}.prompt", "prompt", f"You are {id}.")
    pentry = ContentManifestEntry(
        ref=pref, kind="prompt", name=id, description="d", source_identity="gl.src"
    )
    w = WorkerSpec(
        id=id, catalog_role="final", selection_scope="dynamic_allowed", lane="text",
        persona="p", tier=Tier.WRITER,
        execution=ExecutionSpec(kind=ExecutionKind.LLM, model_tier="reasoner"),
        system_prompt_ref=pref, inputs=tuple(inputs), outputs=tuple(outputs),
        params_schema_ref=params_schema_ref, evidence_policy=EvidencePolicy(),
        supported_modes=(DataMode.ONLINE,), can_emit_decision=False, decision_authority="none",
    )
    return w, [pmat], [pentry]


def _chain_catalog():
    up = _worker("up.worker", outputs=(OutputBinding(name="primary", schema_ref=SR_UP),))
    sink = _worker(
        "sink.worker",
        inputs=(InputBinding(name="feed", schema_ref=SR_UP, required=True),),
        outputs=(OutputBinding(name="primary", schema_ref=SR_OTHER),),
        params_schema_ref=SR_PARAMS,
    )
    workers, mats, content = [], [], []
    for w, wmats, wentries in (up, sink):
        workers.append(w)
        mats += wmats
        content += wentries
    return build_catalog_snapshot(
        catalog_version="cat.v1", content_manifest=tuple(content), skill_manifest=(),
        capability_manifest=(), workers=tuple(workers), resolved_material=tuple(mats),
    )


def _context(*, config_digest: str = DA) -> ContextSnapshot:
    binding = build_empty_memory_binding()
    clock = ClockSpec(as_of=_dt(), timezone="Asia/Shanghai", calendar_id="cn_a_share")
    dc = DataContext(
        as_of=_dt(), clock=clock, mode=DataMode.ONLINE, backend=DataBackend.LIVE,
        strict_pit=False, calendar_id="cn_a_share", resolved_vendor_chains={"prices": ("tushare",)},
        source_config_digest=config_digest, source_registry_digest=DB, routing_snapshot_digest=DC,
        data_snapshot_id="snap-1", data_snapshot_content_digest=DD, built_at=_dt(),
    )
    sel = PayloadRef(namespace="main", object_id="sel-1", content_digest=binding.past_context_hash)
    return ContextSnapshot.build(
        snapshot_id="ctx-1", data_context=dc, memory_snapshot_id="ms-1",
        memory_snapshot_hash=binding.snapshot_hash, past_context_hash=binding.past_context_hash,
        memory_selection_ref=sel, built_at=_dt(),
    )


def _request(**over) -> OrchestrationRequest:
    base = dict(request_id="req-1", goal="g", workflow="orchestrate_only",
                approval_policy=ApprovalPolicy.REQUIRED)
    base.update(over)
    return OrchestrationRequest(**base)


def _draft(catalog, registry, context, *, params=None, budget_tokens=1000, extra_slot="su"):
    up_node = PlanNode(id="up", worker_id="up.worker", writes_slot=extra_slot)
    sink_node = PlanNode(
        id="sink", worker_id="sink.worker", writes_slot="ss",
        params=params if params is not None else {"top_k": 3, "label": "y"},
        dependencies=(
            Dependency(upstream_node_id="up", artifact_slot=extra_slot, inject_as="feed"),
        ),
    )
    ref = PayloadRef(namespace="main", object_id="ctx-obj", content_digest=context.content_digest)
    return PlanDraft(
        id="plan.x", run_id="run-1", request_id="req-1", phase="main",
        source=PlanSource.DYNAMIC, goal="g", as_of=_dt(), mode=DataMode.ONLINE,
        context_snapshot_ref=ref, nodes=(up_node, sink_node), sink_node_ids=("sink",),
        catalog_version=catalog.catalog_version, catalog_digest=catalog.catalog_digest,
        schema_registry_digest=registry.registry_digest, approval_policy=ApprovalPolicy.REQUIRED,
        budget_request_tokens=budget_tokens, budget_request_llm_invocations=2, max_concurrency=2,
    )


def _report(draft, request, context, catalog, registry, attestation=None):
    return validate_plan_draft(
        draft, request=request, context=context, catalog=catalog,
        schema_registry=registry, legacy_attestation=attestation,
    )


def _reservation(candidate_digest: str, *, reservation_id="res-1", request_id="req-1"):
    return BudgetReservation(
        reservation_id=reservation_id, ledger_id="ledger-1", run_id="run-1",
        request_id=request_id, candidate_plan_digest=candidate_digest,
        scope_type="plan", scope_id="plan.x", reserved_tokens=1000,
        reserved_llm_invocations=2, reserved_concurrency=2, status="reserved",
        reserved_at=_dt(),
    )


def _approval(candidate_digest: str, *, decision=ApprovalDecision.APPROVED, request_id="req-1"):
    return PlanApproval(
        request_id=request_id, candidate_plan_digest=candidate_digest,
        decision=decision, actor_id="reviewer-1", decided_at=_dt(),
    )


def _cand(draft, request, context):
    ccd = context.content_digest if context is not None else None
    return compute_candidate_plan_digest(request=request, draft=draft, context_content_digest=ccd)


# --------------------------------------------------------------------------- #
# 1. OrchestrationRequest matrix                                             #
# --------------------------------------------------------------------------- #
def test_request_defaults_required_approval():
    assert _request().approval_policy is ApprovalPolicy.REQUIRED


def test_orchestrate_only_forbids_existing_refs():
    with pytest.raises(ValidationError):
        _request(workflow="orchestrate_only", existing_candidate_artifact_id="art-1")


def test_optimize_existing_requires_all_three_refs():
    ok = _request(
        workflow="optimize_existing",
        existing_candidate_artifact_id="art-1",
        existing_candidate_hash=DA,
        existing_context_snapshot_id="ctx-1",
    )
    assert ok.workflow == "optimize_existing"
    with pytest.raises(ValidationError):
        _request(
            workflow="optimize_existing",
            existing_candidate_artifact_id="art-1",
            existing_candidate_hash=DA,
            # missing existing_context_snapshot_id
        )


def test_request_has_typed_decision_schedule_ref_not_three_strings():
    fields = set(OrchestrationRequest.model_fields)
    assert "decision_schedule_ref" in fields
    assert "decision_schedule_id" not in fields
    assert "decision_schedule_version" not in fields
    assert "decision_schedule_digest" not in fields
    # and it accepts a typed ContentRef
    req = _request(decision_schedule_ref=ContentRef(id="sched.a", version="1", content_digest=DA))
    assert req.decision_schedule_ref.id == "sched.a"


def test_fallback_preset_id_is_explicit_request_field():
    req = _request(fallback_preset_id="preset.a")
    assert req.fallback_preset_id == "preset.a"


# --------------------------------------------------------------------------- #
# 2. single candidate digest sensitivity                                     #
# --------------------------------------------------------------------------- #
def test_candidate_digest_is_deterministic():
    catalog, registry, context = _chain_catalog(), _registry(), _context()
    request, draft = _request(), _draft(_chain_catalog(), registry, context)
    d1 = _cand(draft, request, context)
    d2 = _cand(draft, request, context)
    assert d1 == d2


def test_validator_and_compute_agree_on_candidate_digest():
    catalog, registry, context = _chain_catalog(), _registry(), _context()
    request = _request()
    draft = _draft(catalog, registry, context)
    report = _report(draft, request, context, catalog, registry)
    assert report.candidate_plan_digest == _cand(draft, request, context)


def test_params_change_moves_candidate_digest():
    catalog, registry, context = _chain_catalog(), _registry(), _context()
    request = _request()
    a = _draft(catalog, registry, context, params={"top_k": 1, "label": "y"})
    b = _draft(catalog, registry, context, params={"top_k": 2, "label": "y"})
    assert _cand(a, request, context) != _cand(b, request, context)


def test_dependency_change_moves_candidate_digest():
    catalog, registry, context = _chain_catalog(), _registry(), _context()
    request = _request()
    a = _draft(catalog, registry, context, extra_slot="su")
    b = _draft(catalog, registry, context, extra_slot="sv")
    assert _cand(a, request, context) != _cand(b, request, context)


def test_context_content_change_moves_candidate_digest():
    catalog, registry = _chain_catalog(), _registry()
    ctx_a = _context(config_digest=DA)
    ctx_b = _context(config_digest=DB)
    request = _request()
    da = _cand(_draft(catalog, registry, ctx_a), request, ctx_a)
    db = _cand(_draft(catalog, registry, ctx_b), request, ctx_b)
    assert da != db


def test_catalog_digest_change_moves_candidate_digest():
    catalog, registry, context = _chain_catalog(), _registry(), _context()
    request = _request()
    a = _draft(catalog, registry, context)
    b = a.model_copy(update={"catalog_digest": "e" * 64})
    assert _cand(a, request, context) != _cand(b, request, context)


def test_registry_digest_change_moves_candidate_digest():
    catalog, registry, context = _chain_catalog(), _registry(), _context()
    request = _request()
    a = _draft(catalog, registry, context)
    b = a.model_copy(update={"schema_registry_digest": "e" * 64})
    assert _cand(a, request, context) != _cand(b, request, context)


def test_budget_request_change_moves_candidate_digest():
    catalog, registry, context = _chain_catalog(), _registry(), _context()
    request = _request()
    a = _draft(catalog, registry, context, budget_tokens=1000)
    b = _draft(catalog, registry, context, budget_tokens=2000)
    assert _cand(a, request, context) != _cand(b, request, context)


# --------------------------------------------------------------------------- #
# 3. freeze: one digest binds report + reservation + approval + Plan         #
# --------------------------------------------------------------------------- #
def _freeze_ok(**over):
    catalog, registry, context = _chain_catalog(), _registry(), _context()
    request = _request()
    draft = _draft(catalog, registry, context)
    report = _report(draft, request, context, catalog, registry)
    cand = report.candidate_plan_digest
    kw = dict(
        request=request, context=context, catalog=catalog, schema_registry=registry,
        legacy_attestation=None, report=report,
        reservation=_reservation(cand), approval=_approval(cand),
    )
    kw.update(over)
    return freeze_plan(draft, **kw), cand, (catalog, registry, context, request, draft, report)


def test_happy_freeze_binds_one_candidate_digest():
    plan, cand, _ = _freeze_ok()
    assert isinstance(plan, Plan)
    assert plan.plan_digest == cand


def test_plan_is_frozen():
    plan, _cand, _ = _freeze_ok()
    with pytest.raises(ValidationError):
        plan.plan_digest = "e" * 64


def test_plan_exposes_all_digest_recomputation_bindings():
    plan, cand, ctx = _freeze_ok()
    _catalog, _registry, context, request, draft, _report = ctx
    assert plan.request_id == request.request_id
    assert plan.request_digest == request.semantic_digest()
    assert plan.catalog_digest == draft.catalog_digest
    assert plan.schema_registry_digest == draft.schema_registry_digest
    assert plan.context_content_digest == context.content_digest
    assert plan.context_snapshot_ref is not None
    assert plan.budget_request_tokens == draft.budget_request_tokens
    assert plan.budget_reservation_id == "res-1"
    # the exposed bindings recompute the same candidate digest
    assert plan.plan_digest == cand


def test_freeze_time_and_reservation_id_do_not_move_plan_digest():
    catalog, registry, context = _chain_catalog(), _registry(), _context()
    request = _request()
    draft = _draft(catalog, registry, context)
    report = _report(draft, request, context, catalog, registry)
    cand = report.candidate_plan_digest
    p1 = freeze_plan(
        draft, request=request, context=context, catalog=catalog, schema_registry=registry,
        legacy_attestation=None, report=report, reservation=_reservation(cand, reservation_id="res-1"),
        approval=_approval(cand), frozen_at=_dt(30),
    )
    p2 = freeze_plan(
        draft, request=request, context=context, catalog=catalog, schema_registry=registry,
        legacy_attestation=None, report=report, reservation=_reservation(cand, reservation_id="res-9"),
        approval=_approval(cand), frozen_at=_dt(45),
    )
    assert p1.plan_digest == p2.plan_digest
    assert p1.budget_reservation_id != p2.budget_reservation_id
    assert p1.frozen_at != p2.frozen_at


# --------------------------------------------------------------------------- #
# 4. freeze re-runs validation; forged / reused reports never freeze         #
# --------------------------------------------------------------------------- #
def test_forged_valid_report_cannot_freeze_invalid_draft():
    catalog, registry, context = _chain_catalog(), _registry(), _context()
    request = _request()
    draft = _draft(catalog, registry, context)
    # make the draft catalog-invalid (unknown worker) via model_copy
    bad = draft.model_copy(
        update={"nodes": (draft.nodes[0].model_copy(update={"worker_id": "ghost"}), draft.nodes[1])}
    )
    cand = _cand(bad, request, context)
    forged = PlanValidationReport(
        valid=True, issues=(), candidate_plan_digest=cand,
        request_digest=request.semantic_digest(), context_content_digest=context.content_digest,
        catalog_digest=catalog.catalog_digest, schema_registry_digest=registry.registry_digest,
    )
    with pytest.raises(PlanFreezeError):
        freeze_plan(
            bad, request=request, context=context, catalog=catalog, schema_registry=registry,
            legacy_attestation=None, report=forged,
            reservation=_reservation(cand), approval=_approval(cand),
        )


def test_report_bound_to_different_inputs_cannot_freeze():
    catalog, registry = _chain_catalog(), _registry()
    ctx_a, ctx_b = _context(config_digest=DA), _context(config_digest=DB)
    request = _request()
    draft_a = _draft(catalog, registry, ctx_a)
    report_a = _report(draft_a, request, ctx_a, catalog, registry)
    cand_a = report_a.candidate_plan_digest
    # try to freeze draft_b/ctx_b using report_a (bound to ctx_a)
    draft_b = _draft(catalog, registry, ctx_b)
    with pytest.raises(PlanFreezeError):
        freeze_plan(
            draft_b, request=request, context=ctx_b, catalog=catalog, schema_registry=registry,
            legacy_attestation=None, report=report_a,
            reservation=_reservation(cand_a), approval=_approval(cand_a),
        )


# --------------------------------------------------------------------------- #
# 5. approval / reservation binding                                          #
# --------------------------------------------------------------------------- #
def test_rejected_approval_blocks_freeze():
    catalog, registry, context = _chain_catalog(), _registry(), _context()
    request = _request()
    draft = _draft(catalog, registry, context)
    report = _report(draft, request, context, catalog, registry)
    cand = report.candidate_plan_digest
    with pytest.raises(PlanFreezeError):
        freeze_plan(
            draft, request=request, context=context, catalog=catalog, schema_registry=registry,
            legacy_attestation=None, report=report, reservation=_reservation(cand),
            approval=_approval(cand, decision=ApprovalDecision.REJECTED),
        )


def test_approval_bound_to_other_candidate_blocks_freeze():
    catalog, registry, context = _chain_catalog(), _registry(), _context()
    request = _request()
    draft = _draft(catalog, registry, context)
    report = _report(draft, request, context, catalog, registry)
    cand = report.candidate_plan_digest
    with pytest.raises(PlanFreezeError):
        freeze_plan(
            draft, request=request, context=context, catalog=catalog, schema_registry=registry,
            legacy_attestation=None, report=report, reservation=_reservation(cand),
            approval=_approval("f" * 64),  # different candidate digest
        )


def test_reservation_bound_to_other_candidate_blocks_freeze():
    catalog, registry, context = _chain_catalog(), _registry(), _context()
    request = _request()
    draft = _draft(catalog, registry, context)
    report = _report(draft, request, context, catalog, registry)
    cand = report.candidate_plan_digest
    with pytest.raises(PlanFreezeError):
        freeze_plan(
            draft, request=request, context=context, catalog=catalog, schema_registry=registry,
            legacy_attestation=None, report=report, reservation=_reservation("f" * 64),
            approval=_approval(cand),
        )


def test_auto_policy_always_fails_freeze():
    catalog, registry, context = _chain_catalog(), _registry(), _context()
    request = _request()
    draft = _draft(catalog, registry, context).model_copy(
        update={"approval_policy": ApprovalPolicy.AUTO}
    )
    # a report recomputed on this AUTO draft is invalid; freeze must fail
    report = _report(draft, request, context, catalog, registry)
    assert not report.valid
    cand = report.candidate_plan_digest
    with pytest.raises(PlanFreezeError):
        freeze_plan(
            draft, request=request, context=context, catalog=catalog, schema_registry=registry,
            legacy_attestation=None, report=report, reservation=_reservation(cand),
            approval=_approval(cand),
        )


# --------------------------------------------------------------------------- #
# 6. persisted Plan digest re-verification                                   #
# --------------------------------------------------------------------------- #
def test_declared_plan_digest_mismatch_rejected_on_load():
    plan, _cand, _ctx = _freeze_ok()
    dumped = plan.model_dump()
    dumped["plan_digest"] = "e" * 64
    with pytest.raises(ValidationError):
        Plan.model_validate(dumped)


def test_changing_executable_field_changes_plan_digest_and_needs_new_approval():
    catalog, registry, context = _chain_catalog(), _registry(), _context()
    request = _request()
    draft_a = _draft(catalog, registry, context, params={"top_k": 1, "label": "y"})
    draft_b = _draft(catalog, registry, context, params={"top_k": 2, "label": "y"})
    report_b = _report(draft_b, request, context, catalog, registry)
    cand_b = report_b.candidate_plan_digest
    # approval/reservation minted for draft_a's candidate cannot freeze draft_b
    cand_a = _cand(draft_a, request, context)
    assert cand_a != cand_b
    with pytest.raises(PlanFreezeError):
        freeze_plan(
            draft_b, request=request, context=context, catalog=catalog, schema_registry=registry,
            legacy_attestation=None, report=report_b, reservation=_reservation(cand_a),
            approval=_approval(cand_a),
        )
