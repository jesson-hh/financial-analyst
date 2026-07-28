# -*- coding: utf-8 -*-
"""Phase 3 · Task 9 — full (data + memory) catalog extension tests.

Locks: the sole canonical data base; the closed ``memory.propose`` capability;
NO worker grant / NO production proposal-grant row; inherited ``read_categories``
preserved exactly; guardrail-vs-handler material kinds; the exact builder-owned
facade ref (drift changes it); the memory bridge descriptor (``memory_refs_v1``,
distinct priority, exact analyzer ref); the pure ``0/0`` support analyzer; the
derived empty ``PHASE3_FULL_MEMORY_READERS`` set; and the two frozen goldens
(full catalog + reviewed conservative capture policy). Goldens are NEVER
auto-regenerated.

Run: ``pytest tests/orchestration/memory/test_catalog.py -v``
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from guanlan_v2.orchestration.catalog import CatalogError
from guanlan_v2.orchestration.data.catalog import phase3_data_catalog_snapshot
from guanlan_v2.orchestration.digest import CJSON_VERSION
from guanlan_v2.orchestration.memory import catalog as MC
from guanlan_v2.orchestration.memory import models as M

GOLDEN_DIR = Path(__file__).resolve().parents[1] / "golden"
CATALOG_GOLDEN = GOLDEN_DIR / "phase3_full_catalog_manifest_v1.json"
POLICY_GOLDEN = GOLDEN_DIR / "memory_capture_policy_v1.json"
DATA_CATALOG_GOLDEN = GOLDEN_DIR / "data_catalog_manifest_v1.json"


@pytest.fixture(scope="module")
def full_snapshot():
    return MC.phase3_full_catalog_snapshot()


@pytest.fixture(scope="module")
def data_snapshot():
    return phase3_data_catalog_snapshot()


@pytest.fixture(scope="module")
def surface():
    return MC.phase3_memory_surface()


# --------------------------------------------------------------------------- #
# base + workers                                                               #
# --------------------------------------------------------------------------- #
def test_full_catalog_extends_exactly_the_canonical_data_base(full_snapshot, data_snapshot):
    assert MC.PHASE3_FULL_BASE_CATALOG_DIGEST == data_snapshot.catalog_digest
    assert full_snapshot.catalog_digest != data_snapshot.catalog_digest
    assert full_snapshot.catalog_version == "phase3-full-v1"


def test_non_canonical_data_base_is_rejected(surface):
    from guanlan_v2.orchestration.presets import phase2_static_catalog_snapshot

    wrong_base = phase2_static_catalog_snapshot()
    with pytest.raises(CatalogError):
        MC.build_phase3_full_catalog(
            wrong_base,
            facade_descriptor_ref=surface.facade_ref,
            facade_descriptor=surface.facade_descriptor,
            memory_bridge_descriptor=surface.bridge_descriptor,
            memory_bridge_prefetch_binding=surface.prefetch_binding,
            memory_bridge_support_analyzer_ref=surface.analyzer_ref,
            resolved_materials=(),
        )


def test_no_worker_spec_update_and_read_categories_preserved(full_snapshot, data_snapshot):
    base_by_id = {w.id: w for w in data_snapshot.workers}
    assert {w.id for w in full_snapshot.workers} == set(base_by_id)
    for w in full_snapshot.workers:
        assert w == base_by_id[w.id], f"worker {w.id} drifted"
        assert w.read_categories == base_by_id[w.id].read_categories


def test_memory_propose_granted_to_no_existing_worker(full_snapshot):
    cap_ref, _ = MC.memory_propose_capability()
    for w in full_snapshot.workers:
        assert not any(c.id == cap_ref.id for c in w.capability_allowlist), w.id


def test_production_facade_has_no_proposal_grant_rows(surface):
    assert surface.facade_descriptor.proposal_grants == ()
    assert surface.facade_descriptor.supported_proposal_targets == ("agent", "console")


def test_derived_memory_readers_set_is_empty_and_prefetch_agrees(surface, data_snapshot):
    assert MC.PHASE3_FULL_MEMORY_READERS == frozenset()
    assert MC.memory_readers_of(data_snapshot) == frozenset()
    assert surface.prefetch_binding.rows == ()


# --------------------------------------------------------------------------- #
# capability + material kinds                                                  #
# --------------------------------------------------------------------------- #
def test_memory_propose_capability_shape(full_snapshot):
    cap_ref, material = MC.memory_propose_capability()
    entry = next(e for e in full_snapshot.capability_manifest if e.ref.id == cap_ref.id)
    assert entry.ref == cap_ref
    assert entry.capability_kind == "tool" and entry.transport == "in_process"
    d = material.descriptor
    assert d.input_schema_ref.name == "MemoryProposalRequest"
    assert d.output_schema_ref.name == "MemoryProposalReceipt"
    # the closed Phase-1 CapabilityDescriptor field set is untouched.
    assert set(type(d).model_fields) == {
        "schema_version", "id", "version", "capability_kind", "transport",
        "operation", "input_schema_ref", "output_schema_ref",
    }


def test_material_kinds_guardrail_vs_handler(full_snapshot, surface):
    kinds = {e.ref.id: e.kind for e in full_snapshot.content_manifest}
    for guardrail_id in ("memory.facade.descriptor", "memory.facade.policy",
                        "bridge.memory_runtime.prefetch",
                        "bridge.memory_runtime.descriptor"):
        assert kinds[guardrail_id] == "guardrail", guardrail_id
    for handler_id in ("memory.render.deterministic", "memory.facade.proposal_handler",
                       "bridge.memory_runtime.provider", "bridge.memory_runtime.analyzer"):
        assert kinds[handler_id] == "handler", handler_id


# --------------------------------------------------------------------------- #
# facade ref drift + builder rejection                                          #
# --------------------------------------------------------------------------- #
def test_grant_or_target_drift_changes_the_external_facade_ref(surface):
    canonical = MC.PHASE3_MEMORY_FACADE_DESCRIPTOR_REF
    assert canonical == surface.facade_ref
    fields = {f: getattr(surface.facade_descriptor, f)
              for f in type(surface.facade_descriptor).model_fields}
    fields["proposal_grants"] = (
        M.MemoryProposalGrant(worker_id="dec.pm", allowed_agent_owners=("dec.pm",)),
    )
    drifted = M.MemoryFacadeDescriptor(**fields)
    from guanlan_v2.orchestration.catalog_runtime import build_text_material

    drifted_ref, _m = build_text_material(
        id="memory.facade.descriptor", version="1", kind="guardrail",
        raw=MC.serialize_memory_facade_descriptor(drifted))
    assert drifted_ref.content_digest != canonical.content_digest


def test_builder_rejects_a_non_canonical_facade_ref(surface, data_snapshot):
    from guanlan_v2.orchestration.refs import ContentRef

    foreign = ContentRef(id="memory.facade.descriptor", version="1",
                         content_digest="f" * 64)
    with pytest.raises(CatalogError):
        MC.build_phase3_full_catalog(
            data_snapshot,
            facade_descriptor_ref=foreign,
            facade_descriptor=surface.facade_descriptor,
            memory_bridge_descriptor=surface.bridge_descriptor,
            memory_bridge_prefetch_binding=surface.prefetch_binding,
            memory_bridge_support_analyzer_ref=surface.analyzer_ref,
            resolved_materials=(),
        )


def test_policy_or_prefetch_material_drift_changes_material_ref(surface):
    from guanlan_v2.orchestration.catalog_runtime import build_text_material

    drifted_policy = M.MemoryCapturePolicy.build(**{
        **{f: getattr(surface.policy, f) for f in type(surface.policy).model_fields
           if f != "policy_digest"},
        "top_k_non_mandatory": 6,
    })
    ref, _m = build_text_material(
        id="memory.facade.policy", version="1", kind="guardrail",
        raw=MC.serialize_capture_policy(drifted_policy))
    assert ref.content_digest != surface.policy_ref.content_digest


# --------------------------------------------------------------------------- #
# bridge descriptor + priority + analyzer                                       #
# --------------------------------------------------------------------------- #
def test_memory_bridge_descriptor_shape(surface):
    d = surface.bridge_descriptor
    assert d.bridge_id == "memory.runtime"
    assert d.pre_input_kind == "memory_refs_v1"
    assert d.lifecycle == "static_prefetch_v1"
    assert d.activation_read_categories == ("memory",)
    assert d.activation_capability_refs == ()
    assert d.config_schema_ref.name == "MemoryBridgePrefetchBinding"
    assert d.support_analyzer_ref == surface.analyzer_ref
    assert d.priority == 200  # globally reviewed, distinct from data's 100


def test_priority_collision_with_the_data_bridge_is_rejected(surface, data_snapshot):
    from guanlan_v2.orchestration.catalog_runtime import (
        build_text_material,
        serialize_bridge_descriptor,
    )
    from guanlan_v2.orchestration.data.catalog import phase3_data_surface
    from guanlan_v2.orchestration.presets import load_compat_catalog
    from guanlan_v2.orchestration.runtime_contracts import ExecutionBridgeDescriptor

    fields = {f: getattr(surface.bridge_descriptor, f)
              for f in type(surface.bridge_descriptor).model_fields}
    fields["priority"] = 100  # collides with data.runtime
    colliding = ExecutionBridgeDescriptor(**fields)
    _ref, colliding_material = build_text_material(
        id="bridge.memory_runtime.descriptor", version="1", kind="guardrail",
        raw=serialize_bridge_descriptor(colliding))
    runtime = load_compat_catalog()
    p_text, p_caps = runtime.resolved_materials()
    data_surface = phase3_data_surface()
    materials = (
        tuple(p_text) + tuple(p_caps)
        + data_surface.text_materials() + data_surface.capability_materials
        + tuple(m for m in surface.text_materials()
                if m.ref.id != "bridge.memory_runtime.descriptor")
        + (colliding_material, surface.proposal_capability_material)
    )
    with pytest.raises(CatalogError, match="priority"):
        MC.build_phase3_full_catalog(
            data_snapshot,
            facade_descriptor_ref=surface.facade_ref,
            facade_descriptor=surface.facade_descriptor,
            memory_bridge_descriptor=colliding,
            memory_bridge_prefetch_binding=surface.prefetch_binding,
            memory_bridge_support_analyzer_ref=surface.analyzer_ref,
            resolved_materials=materials,
        )


def _memory_reader_worker(data_snapshot, *, worker_id=None):
    base = data_snapshot.workers[0]
    fields = {name: getattr(base, name) for name in type(base).model_fields}
    fields["read_categories"] = tuple(sorted(set(base.read_categories) | {"memory"}))
    if worker_id:
        fields["id"] = worker_id
    return type(base)(**fields)


def test_analyzer_reports_exact_zero_zero_bounds(surface, data_snapshot):
    worker = _memory_reader_worker(data_snapshot)
    binding = M.MemoryBridgePrefetchBinding.build(
        bridge_id="memory.runtime", bridge_version="1",
        rows=(M.MemoryQueryProjection(worker_id=worker.id,
                                      query_text_param_pointer="/memory_query",
                                      top_k=3),))
    node = SimpleNamespace(id="n1", params={})
    summary = MC.MemoryBridgeSupportAnalyzer().analyze(
        candidate_plan_digest="c" * 64, node=node, worker=worker,
        descriptor=surface.bridge_descriptor, descriptor_ref=surface.descriptor_ref,
        config_bytes=MC.serialize_memory_prefetch_binding(binding))
    assert summary.min_finalized_tool_calls_on_success == 0
    assert summary.max_capability_invocations == 0
    assert summary.allowed_capability_refs == ()
    assert summary.pre_input_kind == "memory_refs_v1"
    assert summary.dynamic_or_model_selected_calls is False


def test_analyzer_still_raises_on_ambiguous_multiple_projections(
        surface, data_snapshot, monkeypatch):
    """Phase 10 · Task 11 controller ruling (rowless discrimination): a reader
    with ZERO rows now gets the honest zero-contribution summary (never a
    reviewed reader — pinned in tests/orchestration/memory/
    test_rowless_discrimination.py), so this test pins the KEPT loud half:
    more than one projection is genuine ambiguity and must still raise.
    ``MemoryBridgePrefetchBinding`` itself refuses duplicate worker rows
    (sorted-unique invariant), so the ambiguous shape can only be probed past
    the parse seam — this is the analyzer's own defense-in-depth raise."""
    worker = _memory_reader_worker(data_snapshot)
    node = SimpleNamespace(id="n1", params={})
    row = SimpleNamespace(worker_id=worker.id)
    monkeypatch.setattr(
        MC, "parse_memory_prefetch_binding",
        lambda _b: SimpleNamespace(bridge_id=surface.bridge_descriptor.bridge_id,
                                   rows=(row, row)))
    with pytest.raises(CatalogError, match="exactly one"):
        MC.MemoryBridgeSupportAnalyzer().analyze(
            candidate_plan_digest="c" * 64, node=node, worker=worker,
            descriptor=surface.bridge_descriptor, descriptor_ref=surface.descriptor_ref,
            config_bytes=b"ignored-by-the-stubbed-parse")


def test_analyzer_rejects_a_non_activated_worker(surface, data_snapshot):
    worker = data_snapshot.workers[0]  # no 'memory' category
    assert "memory" not in worker.read_categories
    node = SimpleNamespace(id="n1", params={})
    with pytest.raises(CatalogError, match="activation"):
        MC.MemoryBridgeSupportAnalyzer().analyze(
            candidate_plan_digest="c" * 64, node=node, worker=worker,
            descriptor=surface.bridge_descriptor, descriptor_ref=surface.descriptor_ref,
            config_bytes=MC.serialize_memory_prefetch_binding(surface.prefetch_binding))


def test_derived_test_surface_supports_readers_and_grants(data_snapshot):
    """The separately reviewed derived TEST snapshot path: a surface built over a
    reader set + grant rows assembles coherently (used by Stage-B/C conformance)."""
    reader = _memory_reader_worker(data_snapshot)
    surface = MC._Phase3MemorySurface(
        base_memory_readers=frozenset({reader.id}),
        prefetch_rows=(M.MemoryQueryProjection(worker_id=reader.id,
                                               query_text_param_pointer="/memory_query",
                                               top_k=3),),
        proposal_grants=(M.MemoryProposalGrant(worker_id=reader.id,
                                               allowed_agent_owners=(reader.id,)),),
    )
    assert surface.facade_descriptor.proposal_grants[0].worker_id == reader.id
    assert surface.facade_ref.content_digest != MC.PHASE3_MEMORY_FACADE_DESCRIPTOR_REF.content_digest
    with pytest.raises(CatalogError):  # rows must equal the reader set exactly
        MC._Phase3MemorySurface(base_memory_readers=frozenset({reader.id}),
                                prefetch_rows=())


# --------------------------------------------------------------------------- #
# frozen goldens                                                                #
# --------------------------------------------------------------------------- #
def test_full_catalog_golden_matches(full_snapshot, surface):
    """NEVER regenerated by tests — a drift is a reviewed catalog change."""
    assert CATALOG_GOLDEN.exists(), f"missing golden: {CATALOG_GOLDEN}"
    doc = json.loads(CATALOG_GOLDEN.read_text(encoding="utf-8"))
    assert doc["algorithm"] == CJSON_VERSION
    assert doc["base_catalog_digest"] == MC.PHASE3_FULL_BASE_CATALOG_DIGEST
    assert doc["catalog_version"] == "phase3-full-v1"
    assert doc["catalog_digest"] == full_snapshot.catalog_digest
    assert doc["catalog_digest"] == MC.PHASE3_FULL_CATALOG_DIGEST
    assert doc["bridge_id"] == "memory.runtime"
    assert doc["bridge_priority"] == 200
    assert doc["facade_descriptor_ref"]["content_digest"] == surface.facade_ref.content_digest
    assert doc["prefetch_binding_digest"] == surface.prefetch_binding.binding_digest
    assert doc["policy_digest"] == surface.policy.policy_digest
    assert doc["memory_propose_capability"]["content_digest"] == (
        MC.memory_propose_capability()[0].content_digest)
    assert doc["proposal_grants"] == []
    assert doc["memory_readers"] == []


def test_capture_policy_golden_matches_the_reviewed_policy():
    """The hand-reviewed conservative policy golden (real digest, never regenerated)."""
    assert POLICY_GOLDEN.exists(), f"missing golden: {POLICY_GOLDEN}"
    doc = json.loads(POLICY_GOLDEN.read_text(encoding="utf-8"))
    assert doc["algorithm"] == CJSON_VERSION
    policy = MC.reviewed_capture_policy()
    assert doc["policy_digest"] == policy.policy_digest
    body = doc["policy"]
    assert body["policy_id"] == "memory.policy.conservative"
    assert body["archive_tail_chars"] == 4000  # existing console _archive_tail size
    assert body["top_k_non_mandatory"] == 5    # existing AgentMemory top_k default
    assert body["require_global_cutover"] is True
    assert [e["source_id"] for e in body["source_mappings"]] == [
        "agent.own", "agent.shared", "console.global.archive",
        "console.global.keyed", "console.global.unkeyed", "console.session",
    ]
    # the golden body re-validates to the exact reviewed policy (round-trip).
    reparsed = M.MemoryCapturePolicy.model_validate(body, strict=False)
    assert reparsed == policy


def test_data_catalog_golden_is_byte_untouched():
    doc = json.loads(DATA_CATALOG_GOLDEN.read_text(encoding="utf-8"))
    assert doc["catalog_digest"] == phase3_data_catalog_snapshot().catalog_digest
