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

import ast
import inspect
import json
from datetime import datetime, timezone
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
from guanlan_v2.orchestration.data import runtime as RT
from guanlan_v2.orchestration.data.schema_registry import build_phase3_registry
from guanlan_v2.orchestration.data.source import RouteEntry
from guanlan_v2.orchestration.enums import ApprovalPolicy, DataMode, PlanSource
from guanlan_v2.orchestration.refs import ContentRef, SchemaRef
from guanlan_v2.orchestration.runtime_contracts import (
    PHASE2_BASE_REGISTRY_DIGEST,
    ExecutionBridgeDescriptor,
    phase2_runtime_registry,
)
from guanlan_v2.orchestration.spec import (
    OrchestrationRequest,
    PlanDraft,
    PlanNode,
    validate_plan_draft,
)

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


# --------------------------------------------------------------------------- #
# the ONE reviewed integration grant: RUNNABLE only under the subject          #
# projection (L1); SERVABLE only once L2-b binds a production world           #
# --------------------------------------------------------------------------- #
UTC = timezone.utc
_ROW_AS_OF = datetime(2026, 7, 16, 7, 0, tzinfo=UTC)


@pytest.fixture(scope="module")
def phase3_registry():
    return build_phase3_registry(
        phase2_runtime_registry(PHASE2_BASE_REGISTRY_DIGEST).registry_digest)


@pytest.fixture(scope="module")
def verified_snapshot_row(surface):
    """The single real prefetch row the reviewed grant produces."""
    rows = surface.prefetch_binding.operations
    assert [(r.worker_id, r.method_ref.id) for r in rows] == [
        ("dec.pm", "verified_snapshot")]
    return rows[0]


def _pm_draft(snapshot, registry, params):
    """A bootstrap-phase draft whose ONLY variable is the pm node's params."""
    node = PlanNode(id="pm", worker_id="dec.pm", writes_slot="slot-pm", params=params)
    return PlanDraft(
        id="plan.pm", run_id="run-1", request_id="req-1", phase="bootstrap",
        source=PlanSource.BOOTSTRAP, goal="g", as_of=_ROW_AS_OF, mode=DataMode.ONLINE,
        context_snapshot_ref=None, nodes=(node,), sink_node_ids=("pm",),
        catalog_version=snapshot.catalog_version, catalog_digest=snapshot.catalog_digest,
        schema_registry_digest=registry.registry_digest,
        approval_policy=ApprovalPolicy.REQUIRED,
        budget_request_tokens=500_000, budget_request_llm_invocations=100,
        max_concurrency=8)


def _pm_codes(snapshot, registry, params):
    report = validate_plan_draft(
        _pm_draft(snapshot, registry, params),
        request=OrchestrationRequest(
            request_id="req-1", goal="g", workflow="orchestrate_only",
            approval_policy=ApprovalPolicy.REQUIRED),
        context=None, catalog=snapshot, schema_registry=registry)
    return {i.code for i in report.issues}


class TestVerifiedSnapshotRowRunnableOnlyUnderSubjectProjection:
    """The one reviewed data grant is RUNNABLE under the subject projection — and
    ONLY under it (L1, D-0 option (i)); it is not yet SERVABLE (no production
    ``DataRuntimeWorld`` is bound — the chartered L2-b gap).

    The structural facts did NOT move: ``_REVIEWED_INTEGRATION_GRANTS =
    {"dec.pm": ("verified_snapshot",)}`` still produces exactly one prefetch row
    whose bindings are the sealed bytes ``/as_of <- /asof_date`` and
    ``/symbols <- /code`` (both ``source_kind="node_param"``); ``dec.pm`` still
    carries ``params_schema_ref=None``; Phase-1 validation still refuses ANY node
    params for such a worker (``params_not_allowed``) in a sealed preset *and* in
    a dynamic plan alike. What changed (2026-07-31, the L1 plan) is that
    ``_assemble_params`` gained a second, CLOSED source for ``node_param``
    pointers: the run-subject projection (``SubjectParams``), service-stamped at
    materialize time from the digest-committed ``RunSubject@1``. Driven with the
    projection bound, the REAL sealed row resolves and validates as a real
    ``InstrumentUniverseParams``; driven without it, the refusal still fires and
    now names the runner seam (a wiring gap), no longer an unbuilt projection.

    The sealed bytes stayed sealed: rewriting the row's bindings would move the
    ``bridge.data_runtime.prefetch`` material digest and the Phase-3 catalog
    digest, so the bindings are untouched and the projection is out-of-band by
    design (the L1 plan's R1 framing, recorded at ``_assemble_params``).
    """

    # -- fact 1/3: the worker can never legally carry params ----------------- #
    def test_dec_pm_declares_no_params_schema_in_either_real_catalog(self, base, snapshot):
        """Base Phase-2 catalog and the Phase-3 catalog that carries the grant agree."""
        assert next(w for w in base.workers if w.id == "dec.pm").params_schema_ref is None
        assert next(w for w in snapshot.workers if w.id == "dec.pm").params_schema_ref is None

    def test_a_node_carrying_the_rows_params_is_refused_by_the_real_validator(
            self, snapshot, phase3_registry):
        """The REAL ``validate_plan_draft`` over the REAL granting catalog.

        The two drafts differ in exactly one thing — whether the pm node carries the
        params this row binds — and the issue sets differ in exactly one code.
        """
        row_params = {"asof_date": "2026-07-16", "code": "600519"}
        clean = _pm_codes(snapshot, phase3_registry, {})
        carrying = _pm_codes(snapshot, phase3_registry, row_params)
        assert "params_not_allowed" not in clean
        assert "params_not_allowed" in carrying
        assert carrying - clean == {"params_not_allowed"}

    # -- fact 2/4: the row's bindings cannot resolve against such a node ----- #
    def test_the_reviewed_row_binds_every_param_out_of_node_params(
            self, verified_snapshot_row):
        assert [(b.target_pointer, b.source_kind, b.source_pointer)
                for b in verified_snapshot_row.param_bindings] == [
            ("/as_of", "node_param", "/asof_date"),
            ("/symbols", "node_param", "/code")]

    def test_without_the_subject_the_row_refuses_naming_the_runner_seam(
            self, verified_snapshot_row):
        """The exact typed error from the REAL runtime path, not a re-implementation.

        A legally-shaped ``dec.pm`` node carries no params, so without the
        subject projection bound the first ``node_param`` binding cannot resolve
        — and the cause message now names the RUNNER SEAM (the projection exists
        as of L1), never the pre-L1 'until the projection is built' story.
        """
        node = PlanNode(id="pm", worker_id="dec.pm", writes_slot="slot-pm")
        assert node.params == {}
        with pytest.raises(RT.DataRuntimeError) as exc:
            RT._assemble_params(verified_snapshot_row, node)
        msg = str(exc.value)
        assert "'/asof_date'" in msg
        assert "does not resolve" in msg
        # the improved message names the CAUSE, not just the symptom.
        assert "params_not_allowed" in msg
        assert "params_schema_ref" in msg
        assert "not bound at the runner seam" in msg
        assert "is built" not in msg  # the pre-L1 unbuilt-projection story is gone

    def test_with_the_subject_the_same_real_row_resolves_and_validates(
            self, verified_snapshot_row):
        """The conscious flip: the SAME row + the SAME params-less node now
        resolve under the projection, and the document validates against the
        row's REAL params class — the row is RUNNABLE, healing defect H at its
        root. (It is still not SERVABLE: no production world — L2-b.)"""
        node = PlanNode(id="pm", worker_id="dec.pm", writes_slot="slot-pm")
        subject = RT.SubjectParams.project(code="600519", as_of=_ROW_AS_OF)
        doc = RT._assemble_params(verified_snapshot_row, node, subject_params=subject)
        params = RT._BINDING_BY_METHOD["verified_snapshot"].params_cls.model_validate(doc)
        assert [s.code for s in params.symbols] == ["600519"]
        assert datetime.fromisoformat(params.as_of) == _ROW_AS_OF

    def test_that_projection_is_the_one_the_live_provider_session_calls(self):
        """Pins the call site, so the tests above cannot drift onto a dead helper.

        NOTE (chartered CONSCIOUS FLIP, owned by L2-b Task 5 — the L1<->L2-b
        integration seam): the real ``_DataRuntimeBridgeSession`` does NOT yet
        pass ``subject_params`` — this source-text pin asserts exactly that
        state. L2-b Task 5 gives the real session the subject param source and
        flips this pin by name; it must never go red as a surprise.
        """
        src = inspect.getsource(RT._DataRuntimeBridgeSession.freeze_for_execution)
        assert "_assemble_params(row, req.node)" in src

    # -- the bridge went live: ONE enumerated production caller --------------- #
    def test_the_data_bridge_provider_has_exactly_one_production_caller(self):
        """CONSCIOUS FLIP (L2-b Task 4; was ``…has_no_production_caller``).

        Pre-flip (the L1 landing state, fact F): the factory's only code
        occurrence in ``guanlan_v2/orchestration`` was its own ``def``, because
        the reviewed ``verified_snapshot`` row was unrunnable. L1 healed the row
        at its root (the subject projection) and L2-b bound a real production
        ``DataRuntimeWorld``, so the bridge IS live — and this pin flips from
        "no caller" to a POSITIVE ENUMERATION of the one caller:
        ``adapters/data_world.py::ProductionDataProvider.open_execution``
        delegates its rows-present session THROUGH the factory (never past it),
        so the single-symbol pin discipline is preserved.

        Same discriminating AST idiom as before (prose in a docstring or comment
        neither satisfies nor trips it) — never deleted, never loosened to a
        substring scan. A THIRD occurrence, a delegation moving to another
        module, or the ``def`` disappearing is the RED arm.
        """
        pkg = Path(RT.__file__).resolve().parent.parent
        assert pkg.name == "orchestration"
        refs: dict[str, list[str]] = {}
        for path in sorted(pkg.rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for n in ast.walk(tree):
                named = (n.name if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                         else n.id if isinstance(n, ast.Name)
                         else n.attr if isinstance(n, ast.Attribute)
                         else n.name if isinstance(n, ast.alias) else None)
                if named == "data_runtime_provider_factory":
                    refs.setdefault(
                        path.relative_to(pkg).as_posix(), []).append(type(n).__name__)
        assert sorted(refs) == ["adapters/data_world.py", "data/runtime.py"], (
            "the world-bound data factory's production callers moved; L2-b "
            "Task 4 enumerated exactly one (adapters/data_world.py) — re-pin "
            "consciously, never absorb")
        assert refs["data/runtime.py"] == ["FunctionDef"], (
            "the defining module now references its own factory")
        assert refs["adapters/data_world.py"] == ["alias", "Name"], (
            "the production caller is the import + the ONE delegation call; a "
            "second call site is a second recipe")
