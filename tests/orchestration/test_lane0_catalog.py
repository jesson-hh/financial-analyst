# -*- coding: utf-8 -*-
"""Phase 5 · Task 8 — Lane 0 worker catalog materials + experience bridge.

Locks the three Lane 0 final WorkerSpecs (``market.factor`` deterministic /
``market.regime`` + ``market.rotation`` LLM), the physical material inventory
under ``config/orchestration/materials/lane0/`` (skill-v1 grammar, digest
round-trip through the Phase 1 ``build_catalog_snapshot``), the experience
capability + execution bridge (descriptor / config / provider / renderer /
analyzer), the closed CapabilityGateway arithmetic (max=1 fails before backend
I/O; an empty ``ExperienceSelection`` still finalizes one successful call) and
the pure, bounded, sentinel-honest experience renderer.

Run: ``python -m pytest tests/orchestration/test_lane0_catalog.py -v``
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from guanlan_v2.orchestration import bootstrap as B
from guanlan_v2.orchestration.bootstrap import (
    EXPERIENCE_BRIDGE_ID,
    EXPERIENCE_EMPTY_SENTINEL,
    MAX_EXPERIENCE_RENDER_BYTES,
    ExperienceBridgeSupportAnalyzer,
    ExperiencePrefetchBinding,
    ExperienceRenderError,
    ExperienceRetrievalBackend,
    assemble_lane0_catalog,
    lane0_analyzers,
    load_lane0_catalog,
    load_lane0_material_bytes,
    parse_experience_prefetch_bindings,
    render_experience_selection_for_prompt,
    serialize_experience_prefetch_bindings,
)
from guanlan_v2.orchestration.catalog import (
    CatalogError,
    ResolvedTextMaterial,
    SkillFormatError,
    WorkerSpec,
    build_catalog_snapshot,
    parse_skill_v1,
)
from guanlan_v2.orchestration.catalog_runtime import (
    BridgeCatalogView,
    CatalogMaterialError,
    CatalogRuntime,
    InMemoryMaterialSource,
    TrustedFactoryRegistry,
    build_text_material,
)
from guanlan_v2.orchestration.digest import content_digest
from guanlan_v2.orchestration.enums import (
    DataMode,
    ExecutionKind,
    Tier,
    ToolCallRequirement,
)
from guanlan_v2.orchestration.market.factors import (
    MARKET_FACTOR_REPORT_SCHEMA_REF,
    REGIME_REPORT_SCHEMA_REF,
    ROTATION_REPORT_SCHEMA_REF,
)
from guanlan_v2.orchestration.memory.experience import (
    EXPERIENCE_QUERY_SCHEMA_REF,
    EXPERIENCE_SELECTION_SCHEMA_REF,
    ExperienceNeighbour,
    ExperienceQuery,
    ExperienceScalerSnapshot,
    ExperienceSelection,
)
from guanlan_v2.orchestration.refs import PayloadRef, SchemaRef, TypedPayloadRef
from guanlan_v2.orchestration.schema_registry import SchemaRegistry
from guanlan_v2.orchestration.spec import PlanNode
from guanlan_v2.orchestration.worker import (
    CapabilityGateway,
    CapabilityGatewayError,
    ExecutionEvidenceSequencer,
)

UTC = timezone.utc
DT = datetime(2026, 7, 18, 1, 30, tzinfo=UTC)
DA = "a" * 64


class FixedClock:
    def now(self) -> datetime:
        return DT


class FakeRefusalSink:
    def __init__(self) -> None:
        self.recorded: list[str] = []

    def record(self, **kw):
        self.recorded.append(kw.get("reason_code", ""))
        return None


class FakeReader:
    """A read-only payload view that accepts any main ref (no digest recheck)."""

    def get(self, ref, *, expected_schema_ref):
        return object()


@pytest.fixture(scope="module")
def cat():
    return load_lane0_catalog()


@pytest.fixture(scope="module")
def runtime(cat):
    return CatalogRuntime.build(cat.snapshot, cat.source)


@pytest.fixture(scope="module")
def view(cat, runtime):
    return BridgeCatalogView.build(runtime, lane0_analyzers(cat))


def _worker(cat, worker_id: str) -> WorkerSpec:
    return {w.id: w for w in cat.workers}[worker_id]


# =========================================================================== #
# 1 — the three WorkerSpecs pass the Phase 1 matrix                            #
# =========================================================================== #
def test_lane0_worker_ids_roles_and_lane(cat):
    ids = tuple(w.id for w in cat.workers)
    assert set(ids) == {"market.factor", "market.regime", "market.rotation"}
    for w in cat.workers:
        assert w.catalog_role == "final"
        assert w.selection_scope == "dynamic_allowed"
        assert w.lane == "market"
        assert w.can_emit_decision is False
        assert w.decision_authority == "none"
        assert tuple(m.value for m in w.supported_modes) == ("online", "pit_replay")
        # sorted tuples
        cap_keys = [(c.id, c.version) for c in w.capability_allowlist]
        assert cap_keys == sorted(cap_keys)
        modes = [m.value for m in w.supported_modes]
        assert modes == sorted(modes)


def test_market_factor_is_deterministic_reader(cat):
    w = _worker(cat, "market.factor")
    assert w.tier is Tier.READER
    assert w.execution.kind is ExecutionKind.DETERMINISTIC
    assert w.execution.handler_ref is not None
    assert w.execution.handler_ref.id == "lane0.market.factor.handler"
    assert w.execution.model_tier is None and w.execution.thinking_budget is None
    assert w.system_prompt_ref is None
    assert w.read_categories == ("market_data",)
    assert w.inputs == ()
    assert [o.name for o in w.outputs] == ["primary"]
    assert w.outputs[0].schema_ref == MARKET_FACTOR_REPORT_SCHEMA_REF
    ep = w.evidence_policy
    assert ep.tool_calls is ToolCallRequirement.FORBIDDEN
    assert ep.require_input_refs is False and ep.require_number_anchors is False
    assert w.capability_allowlist == ()


@pytest.mark.parametrize("worker_id,skill_id,out_ref", [
    ("market.regime", "lane0.regime.skill", "RegimeReport"),
    ("market.rotation", "lane0.rotation.skill", "RotationReport"),
])
def test_llm_workers_regime_rotation_shape(cat, worker_id, skill_id, out_ref):
    w = _worker(cat, worker_id)
    assert w.tier is Tier.WRITER
    assert w.execution.kind is ExecutionKind.LLM
    assert w.execution.model_tier == "reasoner"
    assert w.execution.thinking_budget == 0
    assert w.execution.handler_ref is None
    assert w.system_prompt_ref is not None
    assert [s.skill_ref.id for s in w.skills] == [skill_id]
    assert [g.id for g in w.guardrail_refs] == ["lane0.honesty.guardrail"]
    assert [c.id for c in w.capability_allowlist] == ["experience.retrieve"]
    assert w.read_categories == ("experience_cases", "upstream_artifacts")
    assert [o.name for o in w.outputs] == ["primary"]
    assert w.outputs[0].schema_ref.name == out_ref
    # the load-bearing required=False market_factor_report binding
    assert len(w.inputs) == 1
    binding = w.inputs[0]
    assert binding.name == "market_factor_report"
    assert binding.schema_ref == MARKET_FACTOR_REPORT_SCHEMA_REF
    assert binding.required is False
    assert binding.cardinality == "one"
    ep = w.evidence_policy
    assert ep.tool_calls is ToolCallRequirement.REQUIRED
    assert ep.require_input_refs is True and ep.require_number_anchors is True
    assert ep.allow_unsourced_numbers is False and ep.optional_data_may_degrade is True


def test_no_worker_holds_a_write_or_propose_capability(cat):
    for w in cat.workers:
        for c in w.capability_allowlist:
            assert c.id == "experience.retrieve"
            for banned in ("propose", "write", "append", "grade", "review"):
                assert banned not in c.id
    # the one capability is a read-side data adapter, never a write channel.
    assert cat.capability_material.descriptor.capability_kind == "data_adapter"
    assert cat.capability_material.descriptor.transport == "in_process"
    assert cat.capability_material.descriptor.operation == "experience.retrieve"


# =========================================================================== #
# 2 — material resolution round-trip via build_catalog_snapshot                #
# =========================================================================== #
def test_snapshot_round_trip_and_runtime_resolution(cat, runtime):
    # every worker's refs resolve with matching kind + digest.
    for w in cat.workers:
        resolved = runtime.resolve_worker(w.id)
        assert resolved.worker == w
        if w.execution.handler_ref is not None:
            assert resolved.handler is not None
            assert resolved.handler.ref.content_digest == w.execution.handler_ref.content_digest
        for sb, mat in zip(w.skills, resolved.skills):
            assert mat.ref.content_digest == sb.skill_ref.content_digest
        for cap_ref, cap_mat in zip(w.capability_allowlist, resolved.capabilities):
            assert cap_mat.ref.content_digest == cap_ref.content_digest
    assert runtime.catalog_digest == cat.snapshot.catalog_digest
    assert runtime.final_worker_count() == 3


def test_orphan_material_fails_the_build(cat):
    _, extra = build_text_material(
        id="lane0.orphan.note", version="1", kind="guardrail", raw=b"orphan prose\n")
    with pytest.raises(CatalogError, match="orphan"):
        build_catalog_snapshot(
            catalog_version=cat.snapshot.catalog_version,
            content_manifest=cat.content_manifest,
            skill_manifest=cat.skill_manifest,
            capability_manifest=cat.capability_manifest,
            workers=cat.workers,
            resolved_material=(*cat.resolved, extra),
        )


def test_dangling_ref_fails_the_build(cat):
    # drop one referenced material -> "no resolved material" in the build.
    trimmed = tuple(
        m for m in cat.resolved
        if not (isinstance(m, ResolvedTextMaterial) and m.ref.id == "lane0.honesty.guardrail")
    )
    with pytest.raises(CatalogError, match="no resolved material"):
        build_catalog_snapshot(
            catalog_version=cat.snapshot.catalog_version,
            content_manifest=cat.content_manifest,
            skill_manifest=cat.skill_manifest,
            capability_manifest=cat.capability_manifest,
            workers=cat.workers,
            resolved_material=trimmed,
        )


# =========================================================================== #
# 3 — skill grammar (the two ④ 逐字安装件 parse; malformed variants raise)      #
# =========================================================================== #
def test_both_real_skills_parse_with_frozen_names(cat):
    raw = load_lane0_material_bytes()
    regime = parse_skill_v1(raw["lane0.regime.skill"].decode("utf-8"))
    rotation = parse_skill_v1(raw["lane0.rotation.skill"].decode("utf-8"))
    # R12: frontmatter name stays ④'s human-readable display name.
    assert regime.name == "Market regime read"
    assert rotation.name == "Mainline rotation read"
    assert regime.critical_data_source_heading == "⚠️ CRITICAL: Data Source Priority"
    assert regime.perfect_for and regime.not_ideal_for
    # the rendered factor-report block is the FIRST (only-numeric-source) priority.
    assert "market_factor_report" in regime.source_priority[0]
    assert "rotation family" in rotation.source_priority[0]
    # ④: the industry-chain block is when-supplied — it IS a listed source.
    assert any("industry-chain" in s for s in rotation.source_priority)


def test_skill_grammar_rejects_malformed_variants(cat):
    raw = load_lane0_material_bytes()["lane0.regime.skill"].decode("utf-8")
    # wrong heading (missing the ⚠️).
    broken_heading = raw.replace("## ⚠️ CRITICAL: Data Source Priority",
                                 "## CRITICAL: Data Source Priority")
    with pytest.raises(SkillFormatError):
        parse_skill_v1(broken_heading)
    # non-canonical trigger JSON (pretty-printed array).
    broken_pretty = raw.replace(
        'Perfect for: ["daily market regime assessment"',
        'Perfect for: [ "daily market regime assessment"')
    with pytest.raises(SkillFormatError):
        parse_skill_v1(broken_pretty)
    # duplicate trigger entries.
    broken_dup = raw.replace(
        '"single-name calls","intraday timing"',
        '"single-name calls","single-name calls"')
    with pytest.raises(SkillFormatError):
        parse_skill_v1(broken_dup)


# =========================================================================== #
# 4 — handler material bytes pin the factor set                                #
# =========================================================================== #
def test_handler_material_pins_factor_set_and_moves_catalog_digest(cat):
    raw = load_lane0_material_bytes()
    handler = raw["lane0.market.factor.handler"].decode("utf-8")
    assert "factor_set_version: mfs-v1" in handler
    assert "factor_set_digest: 40819483d104520aa6a3c25a7dfdc3c89a522e07c0cfe04fa9f70ce835d01ab7" in handler
    mutated = dict(raw)
    mutated["lane0.market.factor.handler"] = raw["lane0.market.factor.handler"].replace(
        b"40819483d104520aa6a3c25a7dfdc3c89a522e07c0cfe04fa9f70ce835d01ab7", b"0" * 64)
    other = assemble_lane0_catalog(mutated)
    assert other.snapshot.catalog_digest != cat.snapshot.catalog_digest


# =========================================================================== #
# 5 — experience bridge: descriptor / config / provider / renderer / analyzer  #
# =========================================================================== #
def test_bridge_indexes_and_activates_for_llm_workers_only(cat, runtime, view):
    assert view.bridge_ids() == frozenset({EXPERIENCE_BRIDGE_ID})
    rb = view.resolve(EXPERIENCE_BRIDGE_ID)
    assert rb.descriptor.pre_input_kind == "none"
    assert rb.descriptor.lifecycle == "static_prefetch_v1"
    assert rb.descriptor.config_ref.id == "lane0.experience.bridge.config"
    assert rb.provider_ref.id == "lane0.experience.provider"
    assert rb.analyzer_ref.id == "lane0.experience.analyzer"
    # activation: capability OR read category experience_cases.
    assert len(view.active_bridges_for(runtime.worker("market.regime"))) == 1
    assert len(view.active_bridges_for(runtime.worker("market.rotation"))) == 1
    assert view.active_bridges_for(runtime.worker("market.factor")) == ()


def test_duplicate_bridge_id_rejected(cat, runtime):
    from guanlan_v2.orchestration.catalog import ContentManifestEntry

    raw = load_lane0_material_bytes()
    dup_ref, dup_mat = build_text_material(
        id="lane0.experience.bridge.descriptor2", version="1", kind="guardrail",
        raw=raw["lane0.experience.bridge.descriptor"])
    entry = ContentManifestEntry(
        ref=dup_ref, kind="guardrail", name="dup descriptor",
        description="competing bridge identity", source_identity="phase5.task8")
    snapshot = build_catalog_snapshot(
        catalog_version=cat.snapshot.catalog_version,
        content_manifest=(*cat.content_manifest, entry),
        skill_manifest=cat.skill_manifest,
        capability_manifest=cat.capability_manifest,
        workers=cat.workers,
        resolved_material=(*cat.resolved, dup_mat),
    )
    text = {(m.ref.id, m.ref.version): m.raw_utf8
            for m in (*cat.resolved, dup_mat) if isinstance(m, ResolvedTextMaterial)}
    source = InMemoryMaterialSource(
        text=text, capabilities={
            (cat.capability_ref.id, cat.capability_ref.version):
                cat.capability_material.descriptor})
    dup_runtime = CatalogRuntime.build(snapshot, source)
    with pytest.raises(CatalogMaterialError, match="more than one execution-bridge"):
        BridgeCatalogView.build(dup_runtime, lane0_analyzers(cat))


def test_analyzer_bounds_are_max_one_min_one(cat, runtime, view):
    rb = view.resolve(EXPERIENCE_BRIDGE_ID)
    node = PlanNode(id="regime", worker_id="market.regime", writes_slot="slot-regime")
    summary = rb.analyzer.analyze(
        candidate_plan_digest=DA, node=node, worker=runtime.worker("market.regime"),
        descriptor=rb.descriptor, descriptor_ref=rb.descriptor_ref,
        config_bytes=rb.config_bytes)
    assert summary.max_capability_invocations == 1
    assert summary.min_finalized_tool_calls_on_success == 1
    assert [c.id for c in summary.allowed_capability_refs] == ["experience.retrieve"]
    assert summary.pre_input_kind == "none"
    assert summary.bridge_id == EXPERIENCE_BRIDGE_ID
    # Phase 10 · Task 11 controller ruling (rowless discrimination): a rowless
    # worker whose allowlist does NOT hold the experience capability is the
    # honest ABSENCE of a grant — a zero-contribution summary, never a raise.
    # The kept LOUD half (allowlisted-but-rowless = a reviewed grant lost its
    # row) is pinned by tests/orchestration/test_pipeline_deep_preset.py::
    # TestFullPhase9BridgeView::test_an_allowlisted_worker_without_a_row_still_raises_loudly.
    factor_node = PlanNode(id="factor", worker_id="market.factor", writes_slot="slot-f")
    factor = runtime.worker("market.factor")
    assert not any(c.id == "experience.retrieve" for c in factor.capability_allowlist)
    zero = rb.analyzer.analyze(
        candidate_plan_digest=DA, node=factor_node,
        worker=factor, descriptor=rb.descriptor,
        descriptor_ref=rb.descriptor_ref, config_bytes=rb.config_bytes)
    assert zero.max_capability_invocations == 0
    assert zero.min_finalized_tool_calls_on_success == 0
    assert zero.allowed_capability_refs == ()


def test_config_covers_exactly_the_two_llm_workers(cat):
    bindings = parse_experience_prefetch_bindings(
        serialize_experience_prefetch_bindings(cat.bindings))
    assert tuple(b.worker_id for b in bindings) == ("market.regime", "market.rotation")
    for b in bindings:
        assert b.bridge_id == EXPERIENCE_BRIDGE_ID
        assert b.invocation_mode == "always_invoke"
        assert b.success_requires_finalized_call is True
        assert b.feature_vector_pointer == "/feature_vector"
        assert b.feature_schema_version_pointer == "/feature_schema_version"
        assert b.k == 5
        assert b.capability_ref == cat.capability_ref
        # self-sealed digest verifies.
        assert b.content_digest == b.semantic_digest()


def test_config_parse_rejects_malformed_material():
    with pytest.raises(CatalogError):
        parse_experience_prefetch_bindings(b"not json")
    with pytest.raises(CatalogError):
        parse_experience_prefetch_bindings(b'{"schema_version":"1"}')  # not an array
    with pytest.raises(CatalogError):
        parse_experience_prefetch_bindings(b"[]")  # empty is not a reviewed config


def test_prefetch_binding_model_is_closed():
    from pydantic import ValidationError

    good = dict(
        bridge_id=EXPERIENCE_BRIDGE_ID, worker_id="market.regime",
        capability_ref=load_lane0_catalog().capability_ref,
        feature_vector_pointer="/feature_vector",
        feature_schema_version_pointer="/feature_schema_version", k=5)
    b = ExperiencePrefetchBinding.build(**good)
    assert b.invocation_mode == "always_invoke"
    with pytest.raises(ValidationError):
        ExperiencePrefetchBinding.build(**{**good, "k": 21})  # k <= 20
    with pytest.raises(ValidationError):
        ExperiencePrefetchBinding.build(**{**good, "invocation_mode": "cache_or_invoke"})
    with pytest.raises(ValidationError):
        ExperiencePrefetchBinding.build(**{**good, "success_requires_finalized_call": False})
    with pytest.raises(ValidationError):  # closed JSON-pointer projection, no expressions
        ExperiencePrefetchBinding.build(**{**good, "feature_vector_pointer": "feature_vector"})
    with pytest.raises(ValidationError):
        ExperiencePrefetchBinding.build(
            **{**good, "feature_vector_pointer": "/feature_vector[0].x"})


# =========================================================================== #
# 6 — the provider is reachable only through the gateway; max+1 pre-I/O        #
# =========================================================================== #
def _gateway_env(cat, runtime, view):
    rb = view.resolve(EXPERIENCE_BRIDGE_ID)
    worker = runtime.worker("market.regime")
    node = PlanNode(id="regime", worker_id="market.regime", writes_slot="slot-regime")
    summary = rb.analyzer.analyze(
        candidate_plan_digest=DA, node=node, worker=worker, descriptor=rb.descriptor,
        descriptor_ref=rb.descriptor_ref, config_bytes=rb.config_bytes)
    scaler = ExperienceScalerSnapshot.build(
        feature_schema_version="mfs-v1", scaler_version="scaler-v1", fit_as_of=DT,
        n_obs=0, mu={}, sd={})
    backend = ExperienceRetrievalBackend(views=(), scaler=scaler)
    calls: list = []

    class SpyBackend:
        def invoke(self, *, capability_ref, request):
            calls.append(request)
            return backend.invoke(capability_ref=capability_ref, request=request)

    factories = TrustedFactoryRegistry(runtime)
    factories.register_capability_backend(cat.capability_ref, lambda **kw: SpyBackend())
    registry = SchemaRegistry()
    registry.register(ExperienceQuery)
    registry.register(ExperienceSelection)
    registry.seal()
    seq = ExecutionEvidenceSequencer(node_id="regime", attempt=1)
    token = seq.issue_call_token(
        bridge_priority=rb.priority, bridge_id=rb.bridge_id,
        summary_digest=summary.summary_digest)
    gw = CapabilityGateway(
        plan_digest=DA, worker=worker, summaries={summary.summary_digest: summary},
        catalog=runtime, factories=factories, phase1_registry=registry,
        refusal_sink=FakeRefusalSink(), clock=FixedClock(), sequencer=seq)
    gw.mark_running()
    gw.bind_reader(FakeReader())
    return gw, token, summary, calls


def test_empty_selection_still_finalizes_one_successful_call(cat, runtime, view):
    gw, token, summary, calls = _gateway_env(cat, runtime, view)
    query = ExperienceQuery.build(
        as_of=DT, feature_schema_version="mfs-v1",
        features={"breadth.ad_ratio": 1.0}, k=5)
    pending = gw.begin(
        ordinal_token=token, capability_ref=cat.capability_ref,
        request_schema_ref=EXPERIENCE_QUERY_SCHEMA_REF, idempotency_key="call-1")
    unpub = gw.invoke(pending, query)
    assert len(calls) == 1
    selection = gw.validate_result(unpub)
    assert isinstance(selection, ExperienceSelection)
    assert selection.neighbours == ()
    assert "cold_start:0_neighbours" in selection.badges
    req_ref = TypedPayloadRef(
        schema_ref=EXPERIENCE_QUERY_SCHEMA_REF,
        payload_ref=PayloadRef(namespace="main", object_id="q-1",
                               content_digest=query.content_digest))
    res_ref = TypedPayloadRef(
        schema_ref=EXPERIENCE_SELECTION_SCHEMA_REF,
        payload_ref=PayloadRef(namespace="main", object_id="s-1",
                               content_digest=selection.content_digest))
    record = gw.finalize_success(pending, request_ref=req_ref, result_ref=res_ref)
    assert record.tool_ref == cat.capability_ref
    # an EMPTY selection is one successful finalized call (cold start honest).
    assert gw.summary_charge(summary.summary_digest).finalized == 1


def test_second_begin_max_plus_one_fails_before_backend_io(cat, runtime, view):
    gw, token, summary, calls = _gateway_env(cat, runtime, view)
    gw.begin(ordinal_token=token, capability_ref=cat.capability_ref,
             request_schema_ref=EXPERIENCE_QUERY_SCHEMA_REF, idempotency_key="call-1")
    with pytest.raises(CapabilityGatewayError, match="max_capability_invocations"):
        gw.begin(ordinal_token=token, capability_ref=cat.capability_ref,
                 request_schema_ref=EXPERIENCE_QUERY_SCHEMA_REF, idempotency_key="call-2")
    # the second call died in the gateway BEFORE any backend I/O.
    assert calls == []


def test_provider_backend_is_read_only(cat):
    scaler = ExperienceScalerSnapshot.build(
        feature_schema_version="mfs-v1", scaler_version="scaler-v1", fit_as_of=DT,
        n_obs=0, mu={}, sd={})
    backend = ExperienceRetrievalBackend(views=(), scaler=scaler)
    # the backend exposes ONLY invoke — no append/grade/propose surface exists.
    public = [n for n in dir(backend) if not n.startswith("_")]
    assert public == ["invoke"]


# =========================================================================== #
# 7 — pure, bounded, sentinel-honest experience renderer                       #
# =========================================================================== #
def _selection(neighbours=(), badges=None) -> ExperienceSelection:
    if badges is None:
        badges = () if neighbours else ("cold_start:0_neighbours",)
    return ExperienceSelection.build(
        query_digest=DA, scaler_digest="b" * 64, feature_schema_version="mfs-v1",
        neighbours=tuple(neighbours), visible_case_count=len(neighbours),
        badges=badges)


def test_renderer_is_pure_and_binds_the_selection_digest():
    n = ExperienceNeighbour(
        case_id="case-1", case_as_of=DT, distance=0.5, feature_overlap=1.0,
        state="pending")
    sel = _selection((n,))
    a = render_experience_selection_for_prompt(sel)
    b = render_experience_selection_for_prompt(sel)
    assert a == b  # pure: byte-identical on the same payload
    assert sel.content_digest in a  # rendered bytes bind the selection payload digest
    assert "case-1" in a
    assert EXPERIENCE_EMPTY_SENTINEL not in a


def test_renderer_empty_selection_renders_the_no_analogy_sentinel():
    text = render_experience_selection_for_prompt(_selection())
    assert EXPERIENCE_EMPTY_SENTINEL in text
    assert "cold_start:0_neighbours" in text


def test_renderer_overflow_fails_before_prompt_assembly_no_truncation():
    big = ExperienceNeighbour(
        case_id="case-" + "x" * (MAX_EXPERIENCE_RENDER_BYTES + 100), case_as_of=DT,
        distance=0.5, feature_overlap=1.0, state="pending")
    with pytest.raises(ExperienceRenderError):
        render_experience_selection_for_prompt(_selection((big,)))


# =========================================================================== #
# 8 — trusted handler registration binds by exact catalog identity             #
# =========================================================================== #
def test_register_lane0_trusted_factories_binds_catalog_identities(cat, runtime):
    scaler = ExperienceScalerSnapshot.build(
        feature_schema_version="mfs-v1", scaler_version="scaler-v1", fit_as_of=DT,
        n_obs=0, mu={}, sd={})
    factories = TrustedFactoryRegistry(runtime)
    B.register_lane0_trusted_factories(
        factories=factories, catalog=cat, experience_views=(), experience_scaler=scaler)
    # the deterministic market.factor handler resolves by its exact ref.
    handler_ref = _worker(cat, "market.factor").execution.handler_ref
    from guanlan_v2.orchestration.market.factors import market_factor_handler
    assert factories.handler_factory(handler_ref)() is market_factor_handler
    # the capability backend resolves by the exact catalog capability identity.
    backend = factories.capability_backend_factory(cat.capability_ref)(
        capability_ref=cat.capability_ref)
    assert isinstance(backend, ExperienceRetrievalBackend)
