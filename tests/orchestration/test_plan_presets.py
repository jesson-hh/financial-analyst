# -*- coding: utf-8 -*-
"""Phase 7 · Task 3 — fallback preset registry + PRESET_FALLBACK materializer.

Locks the reviewed, sealed fallback plan-preset surface that Task 4 consumes when
the dynamic Planner produces no admissible candidate:

* :class:`~guanlan_v2.orchestration.plan_presets.PlanPresetRecord` — a
  static-profile-admissible reviewed plan (unique node ids; every node carries no
  gate ids, no debate identity, no ``condition_ref`` and ``max_attempts == 1``);
* :class:`~guanlan_v2.orchestration.plan_presets.PlanPresetRegistry` — a
  ``SchemaRegistry``-shaped register/seal/get/manifest/registry_digest service
  (duplicate ``preset_id`` with different content rejected, identical re-register
  idempotent, mutation after seal impossible);
* :func:`~guanlan_v2.orchestration.plan_presets.load_preset_registry` — a strict
  UTF-8-no-BOM, ``extra="forbid"`` loader that rejects a duplicate ``preset_id``
  across files and seals before returning (physical paths never enter a record);
* :func:`~guanlan_v2.orchestration.plan_presets.materialize_fallback_draft` — THE
  request-level rule made executable: ``request.fallback_preset_id ==
  preset.preset_id`` or :class:`PlanPresetError`; ``source=PRESET_FALLBACK``;
  final-workers-only; the materialized draft passes the REAL Phase-1
  :func:`validate_plan_draft` and produces a stable candidate digest;
* the one reviewed v1 preset ``main.research_baseline`` (the Phase-2 pilot triad
  ``text.sentiment -> dec.research_mgr -> dec.pm`` layout) and its hand-frozen
  golden ``plan_preset_manifest_v1.json`` (never auto-regenerated).

Run: ``python -m pytest tests/orchestration/test_plan_presets.py -v``
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import guanlan_v2.orchestration as orch_pkg
from guanlan_v2.orchestration import presets as P
from guanlan_v2.orchestration.catalog_runtime import load_pilot_catalog
from guanlan_v2.orchestration.digest import content_digest
from guanlan_v2.orchestration.enums import (
    ApprovalPolicy,
    DependencyPolicy,
    PlanSource,
)
from guanlan_v2.orchestration.refs import ContentRef, PayloadRef
from guanlan_v2.orchestration.schema_registry import default_registry
from guanlan_v2.orchestration.spec import (
    Dependency,
    OrchestrationRequest,
    PlanNode,
    compute_candidate_plan_digest,
    validate_plan_draft,
)

from guanlan_v2.orchestration.plan_presets import (
    PlanPresetError,
    PlanPresetRecord,
    PlanPresetRegistry,
    load_preset_registry,
    materialize_fallback_draft,
)

# --------------------------------------------------------------------------- #
# Paths                                                                       #
# --------------------------------------------------------------------------- #
ORCH_DIR = Path(orch_pkg.__file__).resolve().parent
REPO_ROOT = ORCH_DIR.parent.parent
PRESETS_DIR = REPO_ROOT / "config" / "orchestration" / "presets"
GOLDEN = Path(__file__).resolve().parent / "golden" / "plan_preset_manifest_v1.json"

RESEARCH_BASELINE = "main.research_baseline"
D64 = "0" * 64


# --------------------------------------------------------------------------- #
# Reviewed pilot-triad transcription (mirrors presets.build_pilot_draft)       #
# --------------------------------------------------------------------------- #
def _triad_nodes() -> tuple[PlanNode, ...]:
    """The exact ``text.sentiment -> dec.research_mgr -> dec.pm`` node layout."""
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
    return (n_sentiment, n_research, n_pm)


def _mk_record(**over) -> PlanPresetRecord:
    base: dict = dict(
        preset_id=RESEARCH_BASELINE,
        version="1",
        description="reviewed research baseline preset",
        nodes=_triad_nodes(),
        sink_node_ids=("pm",),
        budget_request_tokens=2_000_000,
        budget_request_llm_invocations=3,
        max_concurrency=3,
    )
    base.update(over)
    return PlanPresetRecord(**base)


# --------------------------------------------------------------------------- #
# Runtime scaffolding (reviewed Phase 1/2 builders only)                        #
# --------------------------------------------------------------------------- #
class _FixedClock:
    def now(self):
        from datetime import datetime, timezone
        return datetime(2026, 7, 19, 2, 0, tzinfo=timezone.utc)


def _runtime_pieces(*, fallback_preset_id: str | None = RESEARCH_BASELINE):
    from guanlan_v2.orchestration.eventstore import (
        RuntimeStores,
        SchemaRegistryResolver,
    )
    from guanlan_v2.orchestration.runtime_contracts import phase2_runtime_registry
    from guanlan_v2.orchestration import worker as W

    clock = _FixedClock()
    registry = default_registry()
    catalog_runtime = load_pilot_catalog()
    resolver = SchemaRegistryResolver()
    resolver.register(registry)
    rt_reg = phase2_runtime_registry(registry.registry_digest)
    rt_digest = resolver.register(rt_reg)
    stores = RuntimeStores(
        resolver=resolver, clock=clock,
        allowed_cell_namespaces=(W.PROMPT_CELL_NAMESPACE,))
    request = OrchestrationRequest(
        request_id="req-fallback", goal="A-share single-stock deep-dive (preset pilot)",
        workflow="orchestrate_only", fallback_preset_id=fallback_preset_id,
        approval_policy=ApprovalPolicy.REQUIRED)
    mem = P.build_empty_memory_context(
        data_context=P.pilot_data_context(as_of=clock.now()),
        stores=stores, registry_digest=rt_digest, built_at=clock.now())
    context = mem.context
    ctx_ref = PayloadRef(
        namespace="main", object_id="fallback-ctx", content_digest=context.content_digest)
    return dict(
        clock=clock, registry=registry, catalog=catalog_runtime.snapshot,
        request=request, context=context, ctx_ref=ctx_ref)


# =========================================================================== #
# 1 — PlanPresetRecord static-profile-admissibility matrix                     #
# =========================================================================== #
def test_reviewed_record_constructs_and_is_all_semantic():
    rec = _mk_record()
    assert rec.preset_id == RESEARCH_BASELINE
    assert rec.phase == "main"
    assert len(rec.nodes) == 3 and rec.sink_node_ids == ("pm",)
    # every declared field is semantic identity (no audit-only exclusions).
    assert PlanPresetRecord.SEMANTIC_EXCLUDE == frozenset()


def test_record_rejects_a_gate_id():
    bad = PlanNode(id="g", worker_id="text.sentiment", writes_slot="s",
                   gate_ids=("gate.honesty",))
    with pytest.raises(ValueError):
        _mk_record(nodes=(bad,), sink_node_ids=("g",))


def test_record_rejects_a_debate_node():
    bad = PlanNode(
        id="d", worker_id="text.sentiment", writes_slot="s",
        debate_id="debate.x", round_role="seat", debate_round=1, debate_turn=1)
    with pytest.raises(ValueError):
        _mk_record(nodes=(bad,), sink_node_ids=("d",))


def test_record_rejects_a_condition_ref():
    bad = PlanNode(
        id="c", worker_id="text.sentiment", writes_slot="s",
        condition_ref=ContentRef(id="cond.x", version="1", content_digest=D64))
    with pytest.raises(ValueError):
        _mk_record(nodes=(bad,), sink_node_ids=("c",))


def test_record_rejects_max_attempts_two():
    bad = PlanNode(id="m", worker_id="text.sentiment", writes_slot="s", max_attempts=2)
    with pytest.raises(ValueError):
        _mk_record(nodes=(bad,), sink_node_ids=("m",))


def test_record_rejects_duplicate_node_ids():
    a = PlanNode(id="dup", worker_id="text.sentiment", writes_slot="s1")
    b = PlanNode(id="dup", worker_id="dec.pm", writes_slot="s2")
    with pytest.raises(ValueError):
        _mk_record(nodes=(a, b), sink_node_ids=("dup",))


def test_record_rejects_empty_nodes_and_empty_sinks():
    with pytest.raises(ValueError):
        _mk_record(nodes=())
    with pytest.raises(ValueError):
        _mk_record(sink_node_ids=())


def test_record_rejects_sink_not_a_node():
    with pytest.raises(ValueError):
        _mk_record(sink_node_ids=("ghost",))


# =========================================================================== #
# 2 — PlanPresetRegistry: register / seal / get / duplicate / idempotent        #
# =========================================================================== #
def test_registry_register_get_and_sealed_flag():
    reg = PlanPresetRegistry()
    assert reg.sealed is False
    rec = _mk_record()
    reg.register(rec)
    assert reg.get(RESEARCH_BASELINE) is rec
    reg.seal()
    assert reg.sealed is True


def test_registry_get_unknown_raises():
    reg = PlanPresetRegistry()
    reg.register(_mk_record())
    with pytest.raises(PlanPresetError):
        reg.get("main.does_not_exist")


def test_registry_idempotent_reregister_of_identical_record():
    reg = PlanPresetRegistry()
    reg.register(_mk_record())
    # a byte-identical record re-registers without error (idempotent).
    reg.register(_mk_record())
    assert reg.get(RESEARCH_BASELINE).semantic_digest() == _mk_record().semantic_digest()


def test_registry_duplicate_preset_id_different_content_raises():
    reg = PlanPresetRegistry()
    reg.register(_mk_record())
    with pytest.raises(PlanPresetError):
        reg.register(_mk_record(description="a materially different reviewed preset"))


def test_registry_register_after_seal_raises():
    reg = PlanPresetRegistry()
    reg.seal()
    with pytest.raises(PlanPresetError):
        reg.register(_mk_record())


def test_registry_manifest_sorted_and_digest_order_independent():
    a = _mk_record(preset_id="main.alpha", nodes=_triad_nodes(), sink_node_ids=("pm",))
    b = _mk_record(preset_id="main.beta", nodes=_triad_nodes(), sink_node_ids=("pm",))
    r1 = PlanPresetRegistry()
    r1.register(a)
    r1.register(b)
    r1.seal()
    r2 = PlanPresetRegistry()
    r2.register(b)  # reversed registration order
    r2.register(a)
    r2.seal()
    keys = [(e.preset_id, e.version) for e in r1.manifest()]
    assert keys == sorted(keys)
    assert r1.manifest() == r2.manifest()
    assert r1.registry_digest == r2.registry_digest
    # the manifest digest equals content_digest over the sorted manifest list.
    assert r1.registry_digest == content_digest(list(r1.manifest()))


# =========================================================================== #
# 3 — strict loader                                                            #
# =========================================================================== #
def test_load_preset_registry_over_config_dir_seals_and_resolves():
    reg = load_preset_registry(PRESETS_DIR)
    assert reg.sealed is True
    rec = reg.get(RESEARCH_BASELINE)
    assert rec.preset_id == RESEARCH_BASELINE and rec.version == "1"
    assert tuple(n.worker_id for n in rec.nodes) == (
        "text.sentiment", "dec.research_mgr", "dec.pm")


def _valid_preset_json() -> str:
    return json.dumps({
        "preset_id": "main.tmp_preset",
        "version": "1",
        "description": "temp",
        "nodes": [
            {"id": "sentiment", "worker_id": "text.sentiment",
             "writes_slot": "slot-sentiment"},
        ],
        "sink_node_ids": ["sentiment"],
        "budget_request_tokens": 1000,
        "budget_request_llm_invocations": 1,
        "max_concurrency": 1,
    })


def test_loader_rejects_unknown_key(tmp_path: Path):
    doc = json.loads(_valid_preset_json())
    doc["bogus_extra"] = 1
    (tmp_path / "p.json").write_text(json.dumps(doc), encoding="utf-8")
    with pytest.raises(PlanPresetError):
        load_preset_registry(tmp_path)


def test_loader_rejects_a_bom(tmp_path: Path):
    (tmp_path / "p.json").write_bytes(
        b"\xef\xbb\xbf" + _valid_preset_json().encode("utf-8"))
    with pytest.raises(PlanPresetError):
        load_preset_registry(tmp_path)


def test_loader_rejects_duplicate_preset_id_across_files(tmp_path: Path):
    (tmp_path / "a.json").write_text(_valid_preset_json(), encoding="utf-8")
    (tmp_path / "b.json").write_text(_valid_preset_json(), encoding="utf-8")
    with pytest.raises(PlanPresetError):
        load_preset_registry(tmp_path)


def test_record_carries_no_physical_path_field():
    # physical paths never enter a record — the model has no path/file field.
    for banned in ("path", "file", "source_path", "location", "filename"):
        assert banned not in PlanPresetRecord.model_fields


# =========================================================================== #
# 4 — materialize_fallback_draft                                               #
# =========================================================================== #
def test_materialize_happy_path_passes_real_validation_and_is_stable():
    pieces = _runtime_pieces()
    preset = load_preset_registry(PRESETS_DIR).get(RESEARCH_BASELINE)
    draft = materialize_fallback_draft(
        preset, request=pieces["request"], context=pieces["context"],
        context_snapshot_ref=pieces["ctx_ref"], catalog=pieces["catalog"],
        schema_registry=pieces["registry"], draft_id="plan.fallback", run_id="run-fb")
    assert draft.source is PlanSource.PRESET_FALLBACK
    assert draft.approval_policy is ApprovalPolicy.REQUIRED
    # passes the REAL Phase-1 validator against the pilot catalog.
    report = validate_plan_draft(
        draft, request=pieces["request"], context=pieces["context"],
        catalog=pieces["catalog"], schema_registry=pieces["registry"])
    assert report.valid, [i.code for i in report.issues]
    # stable candidate digest for the fixed (request, context).
    again = materialize_fallback_draft(
        preset, request=pieces["request"], context=pieces["context"],
        context_snapshot_ref=pieces["ctx_ref"], catalog=pieces["catalog"],
        schema_registry=pieces["registry"], draft_id="plan.fallback", run_id="run-fb")
    d1 = compute_candidate_plan_digest(
        request=pieces["request"], draft=draft,
        context_content_digest=pieces["context"].content_digest)
    d2 = compute_candidate_plan_digest(
        request=pieces["request"], draft=again,
        context_content_digest=pieces["context"].content_digest)
    assert d1 == d2


def test_materialized_layout_mirrors_the_reviewed_pilot_exactly():
    pieces = _runtime_pieces()
    preset = load_preset_registry(PRESETS_DIR).get(RESEARCH_BASELINE)
    draft = materialize_fallback_draft(
        preset, request=pieces["request"], context=pieces["context"],
        context_snapshot_ref=pieces["ctx_ref"], catalog=pieces["catalog"],
        schema_registry=pieces["registry"], draft_id="plan.fallback", run_id="run-fb")
    triad = _triad_nodes()
    assert tuple(n.id for n in draft.nodes) == tuple(n.id for n in triad)
    assert tuple(n.worker_id for n in draft.nodes) == tuple(n.worker_id for n in triad)
    assert tuple(n.writes_slot for n in draft.nodes) == tuple(n.writes_slot for n in triad)
    # dependency (artifact_slot, inject_as, policy) tuples mirror the pilot exactly.
    for got, want in zip(draft.nodes, triad):
        assert tuple((d.upstream_node_id, d.artifact_slot, d.inject_as, d.policy)
                     for d in got.dependencies) == tuple(
            (d.upstream_node_id, d.artifact_slot, d.inject_as, d.policy)
            for d in want.dependencies)
    assert draft.sink_node_ids == ("pm",)
    assert draft.budget_request_tokens == 2_000_000
    assert draft.budget_request_llm_invocations == 3
    assert draft.max_concurrency == 3


def test_materialize_rejects_preset_id_mismatch():
    pieces = _runtime_pieces(fallback_preset_id="main.some_other_preset")
    preset = load_preset_registry(PRESETS_DIR).get(RESEARCH_BASELINE)
    with pytest.raises(PlanPresetError):
        materialize_fallback_draft(
            preset, request=pieces["request"], context=pieces["context"],
            context_snapshot_ref=pieces["ctx_ref"], catalog=pieces["catalog"],
            schema_registry=pieces["registry"], draft_id="plan.x", run_id="run-x")


def test_materialize_rejects_a_none_fallback_preset_id():
    pieces = _runtime_pieces(fallback_preset_id=None)
    preset = load_preset_registry(PRESETS_DIR).get(RESEARCH_BASELINE)
    with pytest.raises(PlanPresetError):
        materialize_fallback_draft(
            preset, request=pieces["request"], context=pieces["context"],
            context_snapshot_ref=pieces["ctx_ref"], catalog=pieces["catalog"],
            schema_registry=pieces["registry"], draft_id="plan.x", run_id="run-x")


def test_materialize_rejects_a_compat_worker_preset_before_validation():
    # a preset whose node binds a compatibility worker is rejected by the catalog-role
    # gate BEFORE Phase-1 validation (v1 presets are final-workers-only).
    compat_catalog = P.load_compat_catalog().snapshot
    probe = PlanNode(id="probe", worker_id="compat.news_sentiment", writes_slot="slot-probe")
    preset = _mk_record(
        preset_id="main.compat_probe", nodes=(probe,), sink_node_ids=("probe",))
    pieces = _runtime_pieces(fallback_preset_id="main.compat_probe")
    with pytest.raises(PlanPresetError):
        materialize_fallback_draft(
            preset, request=pieces["request"], context=pieces["context"],
            context_snapshot_ref=pieces["ctx_ref"], catalog=compat_catalog,
            schema_registry=pieces["registry"], draft_id="plan.c", run_id="run-c")


def test_materialize_rejects_a_mismatched_context_ref():
    pieces = _runtime_pieces()
    preset = load_preset_registry(PRESETS_DIR).get(RESEARCH_BASELINE)
    bad_ref = PayloadRef(namespace="main", object_id="x", content_digest=D64)
    with pytest.raises(ValueError):
        materialize_fallback_draft(
            preset, request=pieces["request"], context=pieces["context"],
            context_snapshot_ref=bad_ref, catalog=pieces["catalog"],
            schema_registry=pieces["registry"], draft_id="plan.x", run_id="run-x")


# =========================================================================== #
# 5 — hand-frozen golden equality (never auto-regenerated)                      #
# =========================================================================== #
def test_golden_manifest_matches_the_sealed_registry():
    assert GOLDEN.exists(), f"missing golden: {GOLDEN}"
    doc = json.loads(GOLDEN.read_text(encoding="utf-8"))
    assert doc["algorithm"] == "sha256+cjson-v1"
    reg = load_preset_registry(PRESETS_DIR)
    assert reg.registry_digest == doc["registry_digest"], (
        "sealed preset registry digest drifted from plan_preset_manifest_v1.json; "
        "regenerate + re-review by hand if intended")
    golden = {(e["preset_id"], e["version"]): e["preset_digest"] for e in doc["presets"]}
    live = {(e.preset_id, e.version): e.preset_digest for e in reg.manifest()}
    assert live == golden
    # each golden semantic digest is the record's own recomputed content digest.
    for pid, ver in golden:
        rec = reg.get(pid)
        assert rec.semantic_digest() == golden[(pid, ver)]


def test_editing_the_preset_would_break_the_golden():
    # a materially different preset yields a different registry digest -> the golden
    # test would fail (a reviewed change), proving invariant 4.
    doc = json.loads(GOLDEN.read_text(encoding="utf-8"))
    other = PlanPresetRegistry()
    other.register(_mk_record(description="an edited, un-reviewed description"))
    other.seal()
    assert other.registry_digest != doc["registry_digest"]
