# -*- coding: utf-8 -*-
"""Phase 2 · Task 9.2 — the attested legacy mapping adapter + compatibility catalog.

Locks :mod:`guanlan_v2.orchestration.presets`' legacy compatibility bridge:

* the reviewed fully-MAPPED ``stock-deep-dive`` mapping (source schema/config digest
  == the frozen Task-0 fixture; a reviewed connected core exercising every
  LegacyInputMapping shape);
* :func:`legacy_draft_from_mapping` (MAPPED graph → ``compat.*`` PlanDraft; refuses
  UNMAPPABLE / unknown node/input; base ``code``/``asof_date`` → context, ``out_dir``
  → service_binding, upstream → input_binding under BLOCK/DEGRADE, a proven
  transitive ancestor with NO invented dependency; the param branch on a synthetic
  catalog);
* the service-only attestation path (:func:`attest_and_store_legacy_plan`) and its
  rejection AT THE ADMISSION GATE when missing/mismatched (invariant 2);
* PRESET still needs support→reserve→REQUIRED approval→freeze (invariant 10);
* the cumulative static catalog snapshot / :data:`PHASE2_STATIC_CATALOG_DIGEST`
  (3 final pilot workers + 7 compat workers; compat excluded from the final count;
  digest location/load-order independent; YAML-copy drift detected).

Run: ``python -m pytest tests/orchestration/test_presets.py -v``
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

import pytest

from guanlan_v2.orchestration import presets as P
from guanlan_v2.orchestration import worker as W
from guanlan_v2.orchestration import migration as M
from guanlan_v2.orchestration.catalog import (
    CompatibilityBinding,
    ExecutionSpec,
    InputBinding,
    OutputBinding,
    WorkerSpec,
    build_catalog_snapshot,
    count_final_workers,
)
from guanlan_v2.orchestration.catalog_runtime import (
    CatalogMaterialError,
    CatalogRuntime,
    InMemoryMaterialSource,
    BridgeCatalogView,
    build_text_material,
)
from guanlan_v2.orchestration.context import RunBudget
from guanlan_v2.orchestration.digest import content_digest
from guanlan_v2.orchestration.enums import (
    ApprovalDecision,
    ApprovalPolicy,
    DataMode,
    DependencyPolicy,
    ExecutionKind,
    MappingStatus,
    NodeStatus,
    PlanSource,
    Tier,
)
from guanlan_v2.orchestration.eventstore import (
    RuntimeStores,
    SchemaRegistryResolver,
)
from guanlan_v2.orchestration.events import PlanApproval
from guanlan_v2.orchestration.refs import ContentRef, PayloadRef, SchemaRef
from guanlan_v2.orchestration.runtime_contracts import (
    phase2_runtime_registry,
    static_runtime_profile,
)
from guanlan_v2.orchestration.schema_registry import SchemaRegistry, default_registry
from guanlan_v2.orchestration.schemas import DigestModel, NonEmptyStr
from guanlan_v2.orchestration.spec import (
    Dependency,
    PlanNode,
    StaticLegacyPlanAttestation,
    validate_plan_draft,
)
from guanlan_v2.orchestration.admission import (
    AdmissionRejected,
    ApprovalSubmission,
    PlanAdmissionService,
)

UTC = timezone.utc
_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "legacy_contract_samples.json"
_FIXTURE_CONFIG_DIGEST = "5a175cb91de0cff8358da93272a4ac26fb29013a833a062a096fae56be5b9dbb"


class FixedClock:
    def now(self) -> datetime:
        return datetime(2026, 7, 17, 2, 0, tzinfo=UTC)


class CompatEnv:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def build_compat_env(*, with_attestation: bool = True, request_id: str = "req-legacy"):
    clock = FixedClock()
    registry = default_registry()
    catalog = P.load_compat_catalog()
    view = BridgeCatalogView.build(catalog, {})
    resolver = SchemaRegistryResolver()
    resolver.register(registry)
    rt_digest = resolver.register(phase2_runtime_registry(registry.registry_digest))
    stores = RuntimeStores(
        resolver=resolver, clock=clock, allowed_cell_namespaces=(W.PROMPT_CELL_NAMESPACE,))
    mem = P.build_empty_memory_context(
        data_context=P.pilot_data_context(as_of=clock.now()),
        stores=stores, registry_digest=rt_digest, built_at=clock.now())
    context = mem.context
    mapping = P.build_stock_deep_dive_compat_mapping()
    request = P.preset_orchestration_request(request_id=request_id)
    draft = P.legacy_draft_from_mapping(
        mapping, request=request, context=context, catalog=catalog, schema_registry=registry,
        budget_request={"tokens": 8_000_000, "llm_invocations": 64, "max_concurrency": 16},
        sink_mapping=("report-writer",))
    attestations: dict = {}
    if with_attestation:
        P.attest_and_store_legacy_plan(
            mapping, draft, request, context=context, catalog=catalog.snapshot,
            schema_registry=registry, attestations=attestations)
    run_budget = RunBudget(
        ledger_id="led-legacy", max_tokens=16_000_000, max_llm_invocations=128,
        max_concurrency=32)
    service = PlanAdmissionService(
        run_id=draft.run_id, requests={request.request_id: request}, drafts={draft.id: draft},
        contexts={context.content_digest: context}, attestations=attestations, approvals={},
        catalog=catalog, bridge_view=view, phase1_registry=registry,
        runtime_registry_digest=rt_digest, profile=static_runtime_profile(), stores=stores,
        run_budget=run_budget, clock=clock)
    return CompatEnv(
        clock=clock, registry=registry, catalog=catalog, view=view, rt_digest=rt_digest,
        stores=stores, context=context, mapping=mapping, request=request, draft=draft,
        attestations=attestations, service=service, run_budget=run_budget)


# =========================================================================== #
# 1. reviewed MAPPED mapping matches the frozen Task-0 fixture (invariant 1)    #
# =========================================================================== #
def test_mapping_is_mapped_and_binds_the_fixture_source_identity():
    m = P.build_stock_deep_dive_compat_mapping()
    assert m.mapping_status is MappingStatus.MAPPED
    assert m.source_schema == M.SRC_STOCK_DEEP_DIVE
    assert m.source_config_digest == _FIXTURE_CONFIG_DIGEST
    # the fixture froze the same source config digest.
    fx = json.loads(_FIXTURE.read_text(encoding="utf-8"))
    assert fx["graphs"][0]["config_digest"] == m.source_config_digest
    # every worker/dependency/input row is mapped.
    assert all(w.mapping_status is MappingStatus.MAPPED for w in m.worker_mappings)
    assert all(d.mapping_status is MappingStatus.MAPPED for d in m.dependency_mappings)
    assert all(i.mapping_status is MappingStatus.MAPPED for i in m.input_mappings)


def test_normalized_config_identical_to_engine_copy():
    # _full_normalized_config raises unless repo == engine normalized content.
    cfg = P._full_normalized_config()
    assert content_digest(cfg) == _FIXTURE_CONFIG_DIGEST
    assert set(cfg["nodes"]) == set(json.loads(_FIXTURE.read_text(encoding="utf-8"))
                                    ["graphs"][0]["normalized_config"]["nodes"])


# =========================================================================== #
# 2. compat workers: compat.* id / role / scope / binding (invariants 3, 4)    #
# =========================================================================== #
def test_compat_workers_use_compat_prefix_role_scope_and_binding():
    env = build_compat_env()
    m = env.mapping
    for wm in m.worker_mappings:
        w = env.catalog.worker(wm.target_worker_id)
        assert w.id.startswith("compat.")
        assert w.catalog_role == "compatibility"
        assert w.selection_scope == "static_legacy_only"
        assert isinstance(w.compatibility, CompatibilityBinding)
        # the binding matches the reviewed graph (schema/config/mapping digests).
        assert w.compatibility.legacy_source_schema == m.source_schema
        assert w.compatibility.source_config_digest == m.source_config_digest
        assert w.compatibility.legacy_mapping_digest == m.semantic_digest()
        # complete: prompt/skill/guardrail + output SchemaRef all resolve.
        assert w.system_prompt_ref is not None and w.skills and w.guardrail_refs
        assert w.outputs[0].schema_ref.name in ("SentimentReport", "ResearchPlan", "PortfolioDecision")


def test_no_legacy_agent_name_becomes_a_final_worker_id():
    env = build_compat_env()
    finals = {w.id for w in env.catalog.snapshot.workers if w.catalog_role == "final"}
    legacy_names = {wm.source_node_id for wm in env.mapping.worker_mappings}
    assert finals.isdisjoint(legacy_names)   # no coincidental final ids
    assert finals == {"text.sentiment", "dec.research_mgr", "dec.pm"}


# =========================================================================== #
# 3. the adapter builds a valid compat draft (invariants 2, 5, 8)              #
# =========================================================================== #
def test_adapter_builds_a_valid_attested_compat_draft():
    env = build_compat_env()
    draft = env.draft
    assert draft.source is PlanSource.PRESET
    assert [n.id for n in draft.nodes] == list(P._CORE_NODES)
    assert draft.sink_node_ids == ("report-writer",)
    # legacy tuple written into the draft (invariant 8) → candidate digest binds it.
    assert draft.legacy_source_schema == env.mapping.source_schema
    assert draft.legacy_source_config_digest == env.mapping.source_config_digest
    assert draft.legacy_mapping_digest == env.mapping.semantic_digest()
    # phase-1 validates with the stored attestation.
    att = env.attestations[
        next(iter(env.attestations))]
    report = validate_plan_draft(
        draft, request=env.request, context=env.context, catalog=env.catalog.snapshot,
        schema_registry=env.registry, legacy_attestation=att)
    assert report.valid, [i.code for i in report.issues]


def test_edge_policy_and_missing_behavior_come_from_the_mapping():
    env = build_compat_env()
    node_by_id = {n.id: n for n in env.draft.nodes}
    # quote-fetcher → fundamental-analyst is a HARD edge → BLOCK dependency.
    fund = node_by_id["fundamental-analyst"]
    quote_dep = [d for d in fund.dependencies if d.upstream_node_id == "quote-fetcher"][0]
    assert quote_dep.policy is DependencyPolicy.BLOCK
    assert quote_dep.accept_statuses == frozenset({NodeStatus.COMPLETED})
    # evidence-loader → fundamental-analyst is a SOFT edge → DEGRADE dependency.
    ev_dep = [d for d in fund.dependencies if d.upstream_node_id == "evidence-loader"][0]
    assert ev_dep.policy is DependencyPolicy.DEGRADE
    assert ev_dep.accept_statuses == frozenset({NodeStatus.COMPLETED, NodeStatus.DEGRADED})


def test_out_dir_maps_to_a_service_binding_not_a_plan_path():
    env = build_compat_env()
    out_row = [i for i in env.mapping.input_mappings
               if i.source_key == "out_dir" and i.source_consumer_node_id == "report-writer"][0]
    assert out_row.target_kind == "service_binding"
    assert out_row.target_service_binding == "output_locator"
    # the service binding never becomes a node param / caller path.
    report = [n for n in env.draft.nodes if n.id == "report-writer"][0]
    assert "out_dir" not in report.params and report.params == {}


def test_base_code_and_asof_map_to_context_not_node_params():
    env = build_compat_env()
    base_rows = [i for i in env.mapping.input_mappings if i.source_kind == "base"]
    kinds = {(i.source_key, i.target_kind) for i in base_rows}
    assert ("code", "context") in kinds and ("asof_date", "context") in kinds
    # context-supplied base values are not placed on the node as params.
    for n in env.draft.nodes:
        assert n.params == {}


def test_transitive_ancestor_input_creates_no_invented_dependency():
    env = build_compat_env()
    node_by_id = {n.id: n for n in env.draft.nodes}
    # quote-fetcher is in bull-advocate's input_keys but is NOT a direct dep.
    bull = node_by_id["bull-advocate"]
    assert "quote-fetcher" not in {d.upstream_node_id for d in bull.dependencies}
    # yet the mapping records it as an upstream input with a proven ancestor.
    row = [i for i in env.mapping.input_mappings
           if i.source_consumer_node_id == "bull-advocate" and i.source_key == "quote-fetcher"][0]
    assert row.source_kind == "upstream" and row.target_kind == "input_binding"
    # bull's direct deps are exactly fundamental (BLOCK) + evidence (DEGRADE).
    assert {d.upstream_node_id for d in bull.dependencies} == {"fundamental-analyst", "evidence-loader"}


def test_upstream_input_binds_full_schema_projection_recorded_not_applied():
    # single_field_unwrap / model_dump live on the mapping row (handled later by the
    # attested compat handler); the Plan Dependency binds the full upstream schema.
    env = build_compat_env()
    rows = {(i.source_consumer_node_id, i.source_key): i for i in env.mapping.input_mappings}
    unwrap = rows[("bull-advocate", "evidence-loader")]
    assert unwrap.projection == "single_field_unwrap" and unwrap.projection_field == "narrative"
    dump = rows[("fundamental-analyst", "evidence-loader")]
    assert dump.projection == "model_dump"
    # the draft dependency injects the full artifact (no projection at plan level).
    fund = [n for n in env.draft.nodes if n.id == "fundamental-analyst"][0]
    ev_dep = [d for d in fund.dependencies if d.upstream_node_id == "evidence-loader"][0]
    assert ev_dep.upstream_output_key == "primary"


def test_memory_mode_and_borrows_memory_map_to_worker_metadata():
    env = build_compat_env()
    risk = env.catalog.worker("compat.risk_officer")
    assert "experience_cases" in risk.read_categories            # memory_mode: retrieval
    assert risk.borrowed_from == ("compat.bear_advocate",)       # borrows_memory
    bear = env.catalog.worker("compat.bear_advocate")
    assert "experience_cases" in bear.read_categories


# =========================================================================== #
# 4. adapter refuses UNMAPPABLE / unknown (invariant 7)                        #
# =========================================================================== #
def test_adapter_refuses_unmappable_graph(_fixture_graph):
    env = build_compat_env()
    unmappable = M.migrate_legacy_graph(
        _fixture_graph, source_schema=M.SRC_STOCK_DEEP_DIVE, source_format="json")
    assert unmappable.mapping_status is MappingStatus.UNMAPPABLE
    with pytest.raises(M.MigrationError):
        P.legacy_draft_from_mapping(
            unmappable, request=env.request, context=env.context, catalog=env.catalog,
            schema_registry=env.registry,
            budget_request={"tokens": 1000, "llm_invocations": 1}, sink_mapping=("quote-fetcher",))


def test_adapter_refuses_unknown_sink():
    env = build_compat_env()
    with pytest.raises(M.MigrationError):
        P.legacy_draft_from_mapping(
            env.mapping, request=env.request, context=env.context, catalog=env.catalog,
            schema_registry=env.registry,
            budget_request={"tokens": 1000, "llm_invocations": 1},
            sink_mapping=("does-not-exist",))


def test_no_public_plandraft_from_dagnodes_heuristic():
    assert not hasattr(P, "plandraft_from_dagnodes")


# =========================================================================== #
# 5. attestation at the admission gate (invariant 2) + PRESET approval (10)     #
# =========================================================================== #
def test_admission_reserves_the_attested_compat_plan():
    env = build_compat_env(with_attestation=True)
    prep = env.service.prepare_candidate(env.draft.id, request_id=env.request.request_id)
    assert prep.phase1_report.valid and prep.support_report.supported
    cand, res = env.service.persist_and_reserve_candidate(prep, idempotency_key="reserve-1")
    assert cand.candidate_plan_digest == prep.candidate_plan_digest
    assert res.reserved_llm_invocations == env.draft.budget_request_llm_invocations


def test_admission_rejects_missing_attestation_at_the_gate():
    env = build_compat_env(with_attestation=False)
    prep = env.service.prepare_candidate(env.draft.id, request_id=env.request.request_id)
    # every compat node fails phase-1 (attestation required) → not supported.
    assert not prep.phase1_report.valid
    assert any(i.code == "compat_attestation_required" for i in prep.phase1_report.issues)
    assert not prep.support_report.supported
    with pytest.raises(AdmissionRejected) as ei:
        env.service.persist_and_reserve_candidate(prep, idempotency_key="reserve-bad")
    assert ei.value.code == "unsupported"


def test_admission_rejects_mismatched_attestation_at_the_gate():
    # an attestation bound to a DIFFERENT context/candidate must not admit.
    env = build_compat_env(with_attestation=False)
    other = build_compat_env(with_attestation=True, request_id="req-other")
    wrong = other.attestations[next(iter(other.attestations))]
    env.attestations[env.service.prepare_candidate(
        env.draft.id, request_id=env.request.request_id).candidate_plan_digest] = wrong
    prep = env.service.prepare_candidate(env.draft.id, request_id=env.request.request_id)
    assert not prep.phase1_report.valid
    assert any(i.code in ("compat_attestation_mismatch", "compat_attestation_required")
               for i in prep.phase1_report.issues)


def test_preset_still_requires_support_reserve_required_approval_freeze():
    env = build_compat_env(with_attestation=True)
    svc = env.service
    prep = svc.prepare_candidate(env.draft.id, request_id=env.request.request_id)
    cand, res = svc.persist_and_reserve_candidate(prep, idempotency_key="reserve-1")
    # freeze requires an APPROVED authenticated approval — PRESET is not AUTO.
    svc._approvals[(env.request.request_id, cand.candidate_plan_digest)] = PlanApproval(
        request_id=env.request.request_id, candidate_plan_digest=cand.candidate_plan_digest,
        decision=ApprovalDecision.APPROVED, actor_id="human-1", decided_at=env.clock.now())
    ev = svc.record_approval(
        cand.candidate_plan_digest,
        ApprovalSubmission(request_id=env.request.request_id,
                           candidate_plan_digest=cand.candidate_plan_digest,
                           decision=ApprovalDecision.APPROVED),
        authenticated_actor="human-1", idempotency_key="approve-1")
    plan, admitted = svc.freeze_and_admit_candidate(
        cand.candidate_plan_digest, reservation_id=res.reservation_id,
        approval_event_id=ev.event_id, idempotency_key="freeze-1")
    bundle = svc.verify_for_dispatch(plan.plan_digest)
    assert bundle.plan.source is PlanSource.PRESET
    assert bundle.approval.decision is ApprovalDecision.APPROVED


# =========================================================================== #
# 6. attestation is context/registry-sensitive (invariant 9)                   #
# =========================================================================== #
def test_attestation_candidate_digest_moves_with_context():
    a = build_compat_env(request_id="req-a")
    b = build_compat_env(request_id="req-b")   # different request → different candidate
    da = a.attestations[next(iter(a.attestations))].candidate_plan_digest
    db = b.attestations[next(iter(b.attestations))].candidate_plan_digest
    assert da != db
    # the attestation grants neither AUTO nor dynamic selection.
    att = a.attestations[da]
    assert att.plan_source in ("preset", "preset_fallback")
    assert att.legacy_mapping_digest == a.mapping.semantic_digest()


def test_attestation_stored_under_the_candidate_digest_key():
    env = build_compat_env()
    att = env.attestations[next(iter(env.attestations))]
    assert att.candidate_plan_digest in env.attestations
    assert isinstance(att, StaticLegacyPlanAttestation)


# =========================================================================== #
# 7. cumulative static catalog snapshot (invariants 11, 12)                     #
# =========================================================================== #
def test_static_catalog_has_three_finals_plus_compat_subset():
    snap = P.phase2_static_catalog_snapshot()
    assert count_final_workers(snap) == 3          # compat excluded from the final count
    finals = {w.id for w in snap.workers if w.catalog_role == "final"}
    compat = {w.id for w in snap.workers if w.catalog_role == "compatibility"}
    assert finals == {"text.sentiment", "dec.research_mgr", "dec.pm"}
    assert len(compat) == len(P._CORE_NODES) == 7
    assert all(c.startswith("compat.") for c in compat)


def test_static_catalog_digest_is_stable_and_exported():
    d1 = P.phase2_static_catalog_snapshot().catalog_digest
    d2 = P.phase2_static_catalog_snapshot().catalog_digest
    assert d1 == d2 == P.PHASE2_STATIC_CATALOG_DIGEST


def test_compat_catalog_manifest_pins_the_static_digest():
    # the on-disk YAML manifest pins the exact rebuilt digest (load-order independent).
    rt = P.load_compat_catalog()
    assert rt.catalog_digest == P.PHASE2_STATIC_CATALOG_DIGEST


def test_compat_catalog_fails_on_material_byte_drift(tmp_path):
    # tamper the pinned digest in a copied manifest → load must fail.
    import yaml
    spec = yaml.safe_load(P.COMPAT_CATALOG_PATH.read_text(encoding="utf-8"))
    spec["catalog_digest"] = "0" * 64
    drifted = tmp_path / "compat-drift.yaml"
    drifted.write_text(yaml.safe_dump(spec), encoding="utf-8")
    with pytest.raises(CatalogMaterialError):
        P.load_compat_catalog(catalog_path=drifted)


# =========================================================================== #
# 8. LegacyInputMapping param target-kind on a synthetic catalog (invariant 5)  #
# =========================================================================== #
class _Params(DigestModel):
    schema_version: Literal["1"] = "1"
    code: NonEmptyStr


def _synthetic_param_catalog() -> tuple[CatalogRuntime, SchemaRegistry]:
    reg = SchemaRegistry()
    reg.register(_Params)
    reg.seal()
    p_ref, p_mat = build_text_material(id="p.syn", version="1", kind="prompt", raw=b"You are syn.")
    from guanlan_v2.orchestration.catalog import ContentManifestEntry, EvidencePolicy
    worker = WorkerSpec(
        id="quant.param", catalog_role="final", selection_scope="dynamic_allowed", lane="quant",
        persona="p", tier=Tier.WRITER,
        execution=ExecutionSpec(kind=ExecutionKind.LLM, model_tier="reasoner"),
        system_prompt_ref=p_ref, params_schema_ref=SchemaRef(name="_Params", version="1"),
        inputs=(), outputs=(OutputBinding(name="primary", schema_ref=SchemaRef(name="ResearchPlan", version="1")),),
        evidence_policy=EvidencePolicy(), supported_modes=(DataMode.ONLINE,),
        can_emit_decision=False, decision_authority="none")
    snap = build_catalog_snapshot(
        catalog_version="syn-v1",
        content_manifest=(ContentManifestEntry(ref=p_ref, kind="prompt", name="P", description="d",
                                               source_identity="gl.syn"),),
        skill_manifest=(), capability_manifest=(), workers=(worker,),
        resolved_material=(p_mat,))
    source = InMemoryMaterialSource(text={(p_mat.ref.id, p_mat.ref.version): p_mat.raw_utf8},
                                    capabilities={})
    return CatalogRuntime.build(snap, source), reg


def _synthetic_param_mapping() -> M.LegacyGraphMapping:
    cfg = {"nodes": {"consumer": {}}}
    norm = M.normalize_legacy_graph_config(cfg, source_format="json")
    wm = M.LegacyWorkerMapping(
        source_node_id="consumer", raw_node={"deps": []}, target_worker_id="quant.param",
        mapping_status=MappingStatus.MAPPED, mapping_basis="authoritative_code",
        mapping_policy_id=None, reason=None)
    im = M.LegacyInputMapping(
        source_consumer_node_id="consumer", source_key="code", source_kind="base",
        target_kind="param", target_param_key="code", projection="raw", missing_behavior="error",
        mapping_status=MappingStatus.MAPPED, mapping_basis="authoritative_code",
        mapping_evidence="reviewed base code -> param", reason=None)
    return M.LegacyGraphMapping(
        adapter_version="v1", source_schema=M.SRC_STOCK_DEEP_DIVE, source_format="json",
        normalized_raw_config=norm, source_config_digest=content_digest(norm),
        worker_mappings=(wm,), dependency_mappings=(), input_mappings=(im,),
        mapping_status=MappingStatus.MAPPED, reason=None)


def test_adapter_places_base_param_on_the_node():
    catalog, reg = _synthetic_param_catalog()
    mapping = _synthetic_param_mapping()
    clock = FixedClock()
    resolver = SchemaRegistryResolver()
    resolver.register(reg)
    rt_digest = resolver.register(phase2_runtime_registry(reg.registry_digest)) \
        if False else None  # not needed here
    stores = RuntimeStores(resolver=resolver, clock=clock,
                           allowed_cell_namespaces=(W.PROMPT_CELL_NAMESPACE,))
    request = P.preset_orchestration_request(request_id="req-syn")
    # a minimal empty-memory context registered in this synthetic registry.
    reg2 = default_registry()
    resolver.register(reg2)
    mem = P.build_empty_memory_context(
        data_context=P.pilot_data_context(as_of=clock.now()), stores=stores,
        registry_digest=reg2.registry_digest, built_at=clock.now())
    draft = P.legacy_draft_from_mapping(
        mapping, request=request, context=mem.context, catalog=catalog, schema_registry=reg,
        budget_request={"tokens": 1000, "llm_invocations": 1}, sink_mapping=("consumer",))
    node = draft.nodes[0]
    assert node.params == {"code": "600519"}   # base param placed on the node
    # and it validates against the worker's strict params schema.
    reg.validate_payload(SchemaRef(name="_Params", version="1"), node.params)


@pytest.fixture
def _fixture_graph():
    fx = json.loads(_FIXTURE.read_text(encoding="utf-8"))
    return fx["graphs"][0]["normalized_config"]
