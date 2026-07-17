# -*- coding: utf-8 -*-
"""Phase 3 · Task 5 — cumulative data catalog extension tests (brief item 14).

Locks the reviewed Phase-3 catalog: only the canonical end-of-Phase-2 base is
accepted; the closed ``CapabilityDescriptor@1`` gains no field; worker grants are
capability-allowlist-only, reviewed and one-to-one with prefetch rows;
``compat.*`` gains nothing; the exact data bridge descriptor/config/provider/
analyzer cross-resolve and activate for every granted worker; analyzer bounds
are summed from rows (multi-route never claims max one; optional/cache success
never claims a false minimum); any drift alters the catalog digest while
unchanged entries stay digest-identical; and the result matches the frozen
``data_catalog_manifest_v1.json`` golden (NEVER auto-regenerated).

Run: ``pytest tests/orchestration/data/test_data_catalog.py -v``
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from guanlan_v2.orchestration.catalog import (
    CapabilityDescriptor,
    CatalogError,
    WorkerSpec,
)
from guanlan_v2.orchestration.catalog_runtime import (
    BridgeCatalogView,
    CatalogRuntime,
    InMemoryMaterialSource,
    build_text_material,
    serialize_bridge_descriptor,
)
from guanlan_v2.orchestration.data.catalog import (
    DataBridgePrefetchBinding,
    DataBridgeSupportAnalyzer,
    DataPrefetchOperation,
    ParamBinding,
    build_phase3_catalog,
    phase3_data_catalog_snapshot,
    phase3_data_surface,
)
from guanlan_v2.orchestration.data.source import RouteEntry
from guanlan_v2.orchestration.refs import ContentRef, SchemaRef
from guanlan_v2.orchestration.runtime_contracts import ExecutionBridgeDescriptor
from guanlan_v2.orchestration.spec import PlanNode

GOLDEN_PATH = (
    Path(__file__).resolve().parents[1] / "golden" / "data_catalog_manifest_v1.json"
)


@pytest.fixture(scope="module")
def surface():
    return phase3_data_surface()


@pytest.fixture(scope="module")
def compat_runtime():
    from guanlan_v2.orchestration.presets import load_compat_catalog

    return load_compat_catalog()


@pytest.fixture(scope="module")
def base(compat_runtime):
    return compat_runtime.snapshot


@pytest.fixture(scope="module")
def base_materials(compat_runtime):
    text, caps = compat_runtime.resolved_materials()
    return tuple(text) + tuple(caps)


@pytest.fixture(scope="module")
def snapshot():
    return phase3_data_catalog_snapshot()


def _pm_update(base, surface, method_ids=("verified_snapshot",), extra_fields=None):
    worker = next(w for w in base.workers if w.id == "dec.pm")
    grants = tuple(surface.spec_by_method[m].capability_ref for m in method_ids)
    fields = {name: getattr(worker, name) for name in type(worker).model_fields}
    fields["capability_allowlist"] = tuple(sorted(
        tuple(worker.capability_allowlist) + grants, key=lambda c: (c.id, c.version)))
    if extra_fields:
        fields.update(extra_fields)
    return WorkerSpec(**fields)


def _build(base, base_materials, surface, **over):
    kwargs = dict(
        reviewed_worker_updates=(_pm_update(base, surface),),
        reviewed_source_descriptors=(surface.source_descriptor,),
        reviewed_method_specs=surface.method_specs,
        data_bridge_descriptor=surface.bridge_descriptor,
        data_bridge_prefetch_binding=surface.prefetch_binding,
        resolved_materials=tuple(base_materials) + surface.text_materials()
        + surface.capability_materials,
    )
    kwargs.update(over)
    return build_phase3_catalog(base, **kwargs)


# --------------------------------------------------------------------------- #
# base discipline                                                              #
# --------------------------------------------------------------------------- #
def test_canonical_snapshot_builds_and_extends_the_phase2_base(snapshot, base):
    from guanlan_v2.orchestration.presets import PHASE2_STATIC_CATALOG_DIGEST

    assert base.catalog_digest == PHASE2_STATIC_CATALOG_DIGEST
    assert snapshot.catalog_digest != base.catalog_digest
    assert snapshot.catalog_version == "phase3-data-v1"
    # never mutates the Phase-2 snapshot.
    assert base.catalog_digest == PHASE2_STATIC_CATALOG_DIGEST


def test_non_canonical_base_is_rejected(base_materials, surface):
    from guanlan_v2.orchestration.catalog_runtime import load_pilot_catalog

    pilot = load_pilot_catalog().snapshot  # a real catalog, but NOT the canonical base
    with pytest.raises(CatalogError, match="canonical end-of-Phase-2"):
        _build(pilot, base_materials, surface)


# --------------------------------------------------------------------------- #
# the closed CapabilityDescriptor@1                                            #
# --------------------------------------------------------------------------- #
def test_capability_descriptor_v1_gains_no_field(surface):
    desc = surface.capability_descriptors[0]
    with pytest.raises(ValidationError):
        CapabilityDescriptor(**desc.model_dump(), supported_backends=("live",))
    with pytest.raises(ValidationError):
        CapabilityDescriptor(**desc.model_dump(), side_effects="none")


def test_data_capabilities_output_named_envelopes_never_rawfetch(surface):
    for desc in surface.capability_descriptors:
        assert desc.capability_kind == "data_adapter"
        assert desc.input_schema_ref == SchemaRef(name="DataRequest", version="1")
        assert desc.output_schema_ref.name.endswith("DataResult")
        assert desc.output_schema_ref.name != "RawFetch"


def test_missing_capability_material_fails(base, base_materials, surface):
    thinned = tuple(base_materials) + surface.text_materials() + tuple(
        m for m in surface.capability_materials if m.ref.id != "cap.data.news")
    with pytest.raises(CatalogError, match="no resolved capability material"):
        _build(base, base_materials, surface, resolved_materials=thinned)


# --------------------------------------------------------------------------- #
# worker grants: reviewed, allowlist-only, one-to-one, never compat.*           #
# --------------------------------------------------------------------------- #
def test_unreviewed_worker_update_is_rejected(base, base_materials, surface):
    tampered = _pm_update(base, surface, extra_fields={"persona": "rogue persona"})
    with pytest.raises(CatalogError, match="more than the capability allowlist"):
        _build(base, base_materials, surface, reviewed_worker_updates=(tampered,))


def test_grant_without_prefetch_row_fails(base, base_materials, surface):
    two_grants = _pm_update(base, surface, method_ids=("news", "verified_snapshot"))
    with pytest.raises(CatalogError, match="one-to-one"):
        _build(base, base_materials, surface, reviewed_worker_updates=(two_grants,))


def test_prefetch_row_without_grant_fails(base, base_materials, surface):
    spec = surface.spec_by_method["news"]
    extra_row = DataPrefetchOperation(
        worker_id="dec.research_mgr",  # never granted a data capability
        method_ref=spec.method_ref, capability_ref=spec.capability_ref,
        frozen_route=(RouteEntry(source_ref=surface.source_ref,
                                 capability_ref=spec.capability_ref),),
        invocation_mode="cache_or_invoke")
    binding = DataBridgePrefetchBinding.build(
        bridge_id="data.runtime", bridge_version="1",
        operations=tuple(surface.prefetch_binding.operations) + (extra_row,))
    config_ref, config_material = build_text_material(
        id="bridge.data_runtime.prefetch", version="1", kind="guardrail",
        raw=__import__("guanlan_v2.orchestration.data.catalog", fromlist=["x"])
        .serialize_prefetch_binding(binding))
    descriptor = ExecutionBridgeDescriptor(
        **{**{n: getattr(surface.bridge_descriptor, n)
              for n in type(surface.bridge_descriptor).model_fields},
           "config_ref": config_ref})
    desc_ref, desc_material = build_text_material(
        id="bridge.data_runtime.descriptor", version="1", kind="guardrail",
        raw=serialize_bridge_descriptor(descriptor))
    materials = tuple(base_materials) + (
        surface.renderer_material, surface.source_handler_material,
        surface.provider_material, surface.analyzer_material,
        config_material, desc_material) + surface.capability_materials
    with pytest.raises(CatalogError, match="one-to-one"):
        _build(base, base_materials, surface,
               data_bridge_descriptor=descriptor,
               data_bridge_prefetch_binding=binding,
               resolved_materials=materials)


def test_compat_workers_gain_no_data_capability(base, base_materials, surface, snapshot):
    for w in snapshot.workers:
        if w.id.startswith("compat."):
            assert w.selection_scope == "static_legacy_only"
            assert not any(c.id.startswith("cap.data.") for c in w.capability_allowlist)
    compat = next(w for w in base.workers if w.id.startswith("compat."))
    fields = {n: getattr(compat, n) for n in type(compat).model_fields}
    fields["capability_allowlist"] = (
        surface.spec_by_method["verified_snapshot"].capability_ref,)
    with pytest.raises(CatalogError, match="compat"):
        _build(base, base_materials, surface,
               reviewed_worker_updates=(_pm_update(base, surface), WorkerSpec(**fields)))


# --------------------------------------------------------------------------- #
# bridge cross-resolution + activation                                         #
# --------------------------------------------------------------------------- #
def test_missing_provider_or_analyzer_material_fails(base, base_materials, surface):
    without_analyzer = tuple(base_materials) + (
        surface.renderer_material, surface.source_handler_material,
        surface.provider_material, surface.config_material,
        surface.descriptor_material) + surface.capability_materials
    with pytest.raises(CatalogError, match="bridge analyzer"):
        _build(base, base_materials, surface, resolved_materials=without_analyzer)


def test_provider_and_analyzer_must_be_distinct(base, base_materials, surface):
    same = ExecutionBridgeDescriptor(
        **{**{n: getattr(surface.bridge_descriptor, n)
              for n in type(surface.bridge_descriptor).model_fields},
           "support_analyzer_ref": surface.provider_ref})
    desc_ref, desc_material = build_text_material(
        id="bridge.data_runtime.descriptor", version="1", kind="guardrail",
        raw=serialize_bridge_descriptor(same))
    materials = tuple(base_materials) + (
        surface.renderer_material, surface.source_handler_material,
        surface.provider_material, surface.analyzer_material,
        surface.config_material, desc_material) + surface.capability_materials
    with pytest.raises(CatalogError, match="distinct"):
        _build(base, base_materials, surface, data_bridge_descriptor=same,
               resolved_materials=materials)


def test_activation_predicates_are_the_exact_data_capability_refs(base, base_materials,
                                                                  surface):
    partial = ExecutionBridgeDescriptor(
        **{**{n: getattr(surface.bridge_descriptor, n)
              for n in type(surface.bridge_descriptor).model_fields},
           "activation_capability_refs":
               surface.bridge_descriptor.activation_capability_refs[:3]})
    desc_ref, desc_material = build_text_material(
        id="bridge.data_runtime.descriptor", version="1", kind="guardrail",
        raw=serialize_bridge_descriptor(partial))
    materials = tuple(base_materials) + (
        surface.renderer_material, surface.source_handler_material,
        surface.provider_material, surface.analyzer_material,
        surface.config_material, desc_material) + surface.capability_materials
    with pytest.raises(CatalogError, match="activation"):
        _build(base, base_materials, surface, data_bridge_descriptor=partial,
               resolved_materials=materials)


def test_bridge_cross_resolves_and_activates_for_every_granted_worker(
        snapshot, base_materials, surface):
    text_bytes = {}
    caps = {}
    for m in tuple(base_materials) + surface.text_materials():
        if hasattr(m, "raw_utf8"):
            text_bytes[(m.ref.id, m.ref.version)] = m.raw_utf8
        else:
            caps[(m.ref.id, m.ref.version)] = m.descriptor
    for m in surface.capability_materials:
        caps[(m.ref.id, m.ref.version)] = m.descriptor
    for m in base_materials:
        if not hasattr(m, "raw_utf8"):
            caps[(m.ref.id, m.ref.version)] = m.descriptor
    runtime = CatalogRuntime.build(
        snapshot, InMemoryMaterialSource(text=text_bytes, capabilities=caps))
    analyzer_key = (surface.analyzer_ref.id, surface.analyzer_ref.version,
                    surface.analyzer_ref.content_digest)
    view = BridgeCatalogView.build(runtime, {analyzer_key: DataBridgeSupportAnalyzer()})
    assert "data.runtime" in view.bridge_ids()

    pm = runtime.worker("dec.pm")
    active = view.active_bridges_for(pm)
    assert [rb.bridge_id for rb in active] == ["data.runtime"]

    # end-to-end: the pure analyzer sums the reviewed row bounds for dec.pm.
    rb = active[0]
    node = PlanNode(id="pm", worker_id="dec.pm", writes_slot="slot-pm")
    summary = rb.analyzer.analyze(
        candidate_plan_digest="a" * 64, node=node, worker=pm,
        descriptor=rb.descriptor, descriptor_ref=rb.descriptor_ref,
        config_bytes=rb.config_bytes)
    assert summary.min_finalized_tool_calls_on_success == 0  # cache_or_invoke
    assert summary.max_capability_invocations == 1           # one 1-entry route
    assert [c.id for c in summary.allowed_capability_refs] == ["cap.data.verified_snapshot"]

    # ungranted workers never activate the data bridge.
    sentiment = runtime.worker("text.sentiment")
    assert view.active_bridges_for(sentiment) == ()


# --------------------------------------------------------------------------- #
# analyzer bound arithmetic (multi-route / always_invoke)                       #
# --------------------------------------------------------------------------- #
def _op(surface, *, mode="cache_or_invoke", required=False, route_len=1):
    spec = surface.spec_by_method["verified_snapshot"]
    return DataPrefetchOperation(
        worker_id="dec.pm", method_ref=spec.method_ref,
        capability_ref=spec.capability_ref,
        frozen_route=tuple(
            RouteEntry(
                source_ref=ContentRef(id=f"vendor{i}", version="1",
                                      content_digest="b" * 64),
                capability_ref=spec.capability_ref)
            for i in range(route_len)),
        invocation_mode=mode, success_requires_finalized_call=required)


def test_multi_route_fallback_never_claims_max_one(surface):
    op = _op(surface, route_len=3)
    assert op.row_max_invocations == 3
    assert op.row_min_finalized == 0


def test_always_invoke_minimum_requires_finalized_call(surface):
    assert _op(surface, mode="always_invoke").row_min_finalized == 0
    assert _op(surface, mode="always_invoke", required=True).row_min_finalized == 1


def test_required_finalized_call_is_illegal_under_cache_or_invoke(surface):
    with pytest.raises(ValidationError, match="always_invoke"):
        _op(surface, mode="cache_or_invoke", required=True)


def test_analyzer_sums_rows_and_rejects_off_allowlist_rows(base, surface):
    binding = DataBridgePrefetchBinding.build(
        bridge_id="data.runtime", bridge_version="1",
        operations=(_op(surface, route_len=2),
                    _op_news(surface, mode="always_invoke", required=True)))
    from guanlan_v2.orchestration.data.catalog import serialize_prefetch_binding

    pm = _pm_update(base, surface, method_ids=("news", "verified_snapshot"))
    node = PlanNode(id="pm", worker_id="dec.pm", writes_slot="slot-pm")
    summary = DataBridgeSupportAnalyzer().analyze(
        candidate_plan_digest="a" * 64, node=node, worker=pm,
        descriptor=surface.bridge_descriptor,
        descriptor_ref=surface.descriptor_ref,
        config_bytes=serialize_prefetch_binding(binding))
    assert summary.max_capability_invocations == 3   # 2 + 1, summed from rows
    assert summary.min_finalized_tool_calls_on_success == 1  # only the reviewed row

    # a row outside the worker allowlist is an analyzer failure, not authority.
    ungranted_pm = next(w for w in base.workers if w.id == "dec.pm")
    with pytest.raises(CatalogError, match="allowlist"):
        DataBridgeSupportAnalyzer().analyze(
            candidate_plan_digest="a" * 64, node=node, worker=ungranted_pm,
            descriptor=surface.bridge_descriptor,
            descriptor_ref=surface.descriptor_ref,
            config_bytes=serialize_prefetch_binding(binding))


def _op_news(surface, *, mode="cache_or_invoke", required=False):
    spec = surface.spec_by_method["news"]
    return DataPrefetchOperation(
        worker_id="dec.pm", method_ref=spec.method_ref,
        capability_ref=spec.capability_ref,
        frozen_route=(RouteEntry(source_ref=surface.source_ref,
                                 capability_ref=spec.capability_ref),),
        invocation_mode=mode, success_requires_finalized_call=required)


# --------------------------------------------------------------------------- #
# no dynamic / model-selected / late-call escape                               #
# --------------------------------------------------------------------------- #
def test_param_bindings_are_closed_pointer_value_projections():
    ParamBinding(target_pointer="/as_of", source_kind="node_param",
                 source_pointer="/asof_date")
    ParamBinding(target_pointer="/limit", source_kind="const", const_value=10)
    with pytest.raises(ValidationError):  # no expression/callable escape hatch
        ParamBinding(target_pointer="/as_of", source_kind="expression",
                     source_pointer="clock.now()")
    with pytest.raises(ValidationError):  # closed model: no extra field
        ParamBinding(target_pointer="/as_of", source_kind="node_param",
                     source_pointer="/asof_date", late_call="model.decide")
    with pytest.raises(ValidationError):  # pointers must be JSON pointers
        ParamBinding(target_pointer="as_of", source_kind="node_param",
                     source_pointer="/asof_date")


def test_binding_digest_is_verified_and_operations_canonical(surface):
    binding = surface.prefetch_binding
    fields = {n: getattr(binding, n) for n in type(binding).model_fields
              if n != "binding_digest"}
    with pytest.raises(ValidationError):
        DataBridgePrefetchBinding(**fields, binding_digest="0" * 64)


# --------------------------------------------------------------------------- #
# drift sensitivity + unchanged-identity stability + frozen golden              #
# --------------------------------------------------------------------------- #
def test_priority_drift_alters_the_catalog_digest(base, base_materials, surface, snapshot):
    drifted = ExecutionBridgeDescriptor(
        **{**{n: getattr(surface.bridge_descriptor, n)
              for n in type(surface.bridge_descriptor).model_fields},
           "priority": surface.bridge_descriptor.priority + 1})
    desc_ref, desc_material = build_text_material(
        id="bridge.data_runtime.descriptor", version="1", kind="guardrail",
        raw=serialize_bridge_descriptor(drifted))
    materials = tuple(base_materials) + (
        surface.renderer_material, surface.source_handler_material,
        surface.provider_material, surface.analyzer_material,
        surface.config_material, desc_material) + surface.capability_materials
    other = _build(base, base_materials, surface, data_bridge_descriptor=drifted,
                   resolved_materials=materials)
    assert other.catalog_digest != snapshot.catalog_digest


def test_unchanged_workers_and_materials_stay_digest_identical(base, snapshot):
    base_workers = {w.id: w for w in base.workers}
    for w in snapshot.workers:
        if w.id == "dec.pm":
            assert w.semantic_digest() != base_workers[w.id].semantic_digest()
        else:
            assert w.semantic_digest() == base_workers[w.id].semantic_digest()
    base_content = {(e.ref.id, e.ref.version): e.ref.content_digest
                    for e in base.content_manifest}
    for e in snapshot.content_manifest:
        key = (e.ref.id, e.ref.version)
        if key in base_content:
            assert e.ref.content_digest == base_content[key]


def test_matches_the_frozen_golden_manifest(snapshot, surface):
    """NEVER regenerates the golden: a drifted catalog is a reviewed change."""
    from guanlan_v2.orchestration.presets import PHASE2_STATIC_CATALOG_DIGEST
    import guanlan_v2.orchestration.data.catalog as dcat

    doc = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))
    assert doc["algorithm"] == "sha256+cjson-v1"
    assert doc["base_catalog_digest"] == PHASE2_STATIC_CATALOG_DIGEST
    assert doc["base_catalog_digest"] == dcat.PHASE3_BASE_CATALOG_DIGEST
    assert doc["catalog_digest"] == snapshot.catalog_digest
    assert doc["catalog_digest"] == dcat.PHASE3_DATA_CATALOG_DIGEST
    assert doc["catalog_version"] == snapshot.catalog_version
    assert doc["bridge_id"] == "data.runtime"
    assert doc["prefetch_binding_digest"] == surface.prefetch_binding.binding_digest
    assert doc["integration_grants"] == {"dec.pm": ["verified_snapshot"]}
    golden_caps = {(c["id"], c["version"]): c["content_digest"]
                   for c in doc["data_capabilities"]}
    for m in surface.capability_materials:
        assert golden_caps[(m.ref.id, m.ref.version)] == m.ref.content_digest
    golden_mats = {(m["id"], m["version"]): m["content_digest"]
                   for m in doc["data_materials"]}
    for ref in (surface.renderer_ref, surface.source_handler_ref, surface.provider_ref,
                surface.analyzer_ref, surface.config_ref, surface.descriptor_ref):
        assert golden_mats[(ref.id, ref.version)] == ref.content_digest
