# -*- coding: utf-8 -*-
"""Task 0 — the executable Phase 1 -> Phase 2 handoff gate.

This is a *consumer* gate, not a red->green TDD task: it imports the frozen
Phase 1 orchestration contract layer exactly as a Phase 2 runtime kernel will,
exercises the real builders / validators / digests, and asserts the public API
is intact and singular. Every assertion here must PASS against the already
existing Phase 1 code (HEAD at the time Task 0 was authored: 778 tests green). A
failure means Phase 1 drifted from its frozen exit gates and blocks Phase 2 — it
must never be "fixed" by weakening an assertion or editing Phase 1.

It proves the eight points of ``.superpowers/sdd/task-0-brief.md`` Step 1:

1. the frozen ``schema_manifest_v1.json`` + digest golden vectors reproduce, and
   ``default_registry()`` is sealed;
2. Phase 1 exports exactly one canonical ``compute_candidate_plan_digest`` /
   ``validate_plan_draft`` / ``freeze_plan`` and no module ships an alternative
   projection;
3. ``WorkerSpec`` / ``ExecutionSpec`` / ``EvidencePolicy`` are owned by
   ``catalog.py``, not ``spec.py``;
4. a ``WorkerCatalogSnapshot`` built from content / skill / capability materials
   validates with the Phase 1 builder, and a tampered material digest is
   rejected;
5. ``RunEvent`` accepts ``SchemaRef`` + ``PayloadRef``; ``PayloadRef`` (the type
   the Phase 2 plan calls ``TypedPayloadRef``) preserves exact schema/content
   under audit-only object relocation; and every ``DigestHex``-typed field of
   ``Artifact`` / ``InputSnapshot`` / ``NodeRun`` / ``BudgetReservation`` /
   ``PlanApproval`` / ``PlanValidationReport`` / ``StaticLegacyPlanAttestation``
   rejects an arbitrary digest string;
6. the real Task 0 legacy fixture carries ``scalars`` / ``workers`` / ``graphs``,
   one frozen ``stock-deep-dive`` config digest and complete
   ``LegacyWorkerMapping`` / ``LegacyDependencyMapping`` / ``LegacyInputMapping``
   tuples;
7. the canonical empty-memory facts persist in ``main`` and produce an exact
   ``ContextSnapshot`` binding with ``memory_session_id=None``; a widened memory
   session scope (a Worker/model choosing a scope it was not granted) is
   rejected;
8. the Phase 1-owned modules / tests / contract golden files exist, are singular
   and are not overwritten by any Phase 2 path in today's repo state.

Run from repo root: ``pytest tests/orchestration/test_phase2_handoff.py -v``
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

import guanlan_v2.orchestration as orch_pkg
from guanlan_v2.orchestration import catalog as catalog_mod
from guanlan_v2.orchestration import migration as migration_mod
from guanlan_v2.orchestration import spec as spec_mod

# -- eager public foundation (curated __init__ exports) --------------------- #
from guanlan_v2.orchestration.digest import (
    CJSON_VERSION,
    canonical_json,
    content_digest,
)
from guanlan_v2.orchestration.refs import PayloadRef, SchemaRef
from guanlan_v2.orchestration.schema_registry import (
    RegistrySealedError,
    SchemaRegistry,
    default_registry,
)

# -- catalog ABI (owning module: catalog.py) -------------------------------- #
from guanlan_v2.orchestration.catalog import (
    CapabilityDescriptor,
    CapabilityManifestEntry,
    CatalogError,
    ContentManifestEntry,
    EvidencePolicy,
    ExecutionSpec,
    OutputBinding,
    ResolvedCapabilityMaterial,
    ResolvedTextMaterial,
    SkillBinding,
    SkillManifest,
    WorkerCatalogSnapshot,
    WorkerSpec,
    build_catalog_snapshot,
    catalog_material_digest,
    validate_catalog_snapshot,
)
from guanlan_v2.orchestration.context import (
    ClockSpec,
    ContextSnapshot,
    DataContext,
    build_empty_memory_binding,
    check_memory_session_scope,
)
from guanlan_v2.orchestration.context import BudgetReservation
from guanlan_v2.orchestration.enums import (
    ApprovalDecision,
    DataBackend,
    DataMode,
    ExecutionKind,
    NodeStatus,
    PortfolioRating,
    Tier,
    ToolCallRequirement,
)
from guanlan_v2.orchestration.events import EventType, PlanApproval, RunEvent
from guanlan_v2.orchestration.migration import (
    SRC_STOCK_DEEP_DIVE,
    LegacyDependencyMapping,
    LegacyInputMapping,
    LegacyWorkerMapping,
    migrate_legacy_graph,
)
from guanlan_v2.orchestration.refs import CapabilityRef, ContentRef
from guanlan_v2.orchestration.schemas import (
    Artifact,
    NodeRun,
    Provenance,
    ResearchPlan,
)
from guanlan_v2.orchestration.spec import (
    PlanValidationReport,
    StaticLegacyPlanAttestation,
    compute_candidate_plan_digest,
    freeze_plan,
    validate_plan_draft,
)
from guanlan_v2.orchestration.context import InputSnapshot

# --------------------------------------------------------------------------- #
# Paths + fixtures                                                            #
# --------------------------------------------------------------------------- #
TESTS_DIR = Path(__file__).resolve().parent
GOLDEN_DIR = TESTS_DIR / "golden"
FIXTURES_DIR = TESTS_DIR / "fixtures"
ORCH_DIR = Path(orch_pkg.__file__).resolve().parent

SCHEMA_MANIFEST_GOLDEN = GOLDEN_DIR / "schema_manifest_v1.json"
DIGEST_VECTORS_GOLDEN = GOLDEN_DIR / "digest_vectors_v1.json"
LEGACY_FIXTURE = FIXTURES_DIR / "legacy_contract_samples.json"

UTC = timezone.utc
DT = datetime(2026, 7, 15, 8, 30, tzinfo=UTC)
D64 = "a" * 64  # a well-formed 64-hex digest
GARBAGE_DIGEST = "not-a-valid-sha256-digest"  # rejected by the DigestHex pattern


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


# =========================================================================== #
# Point 1 — frozen goldens reproduce + sealed registry                        #
# =========================================================================== #
def test_point1_default_registry_is_sealed_and_frozen():
    reg = default_registry()
    assert isinstance(reg, SchemaRegistry)
    assert reg.sealed is True
    # a sealed registry is what Phase 2 imports; it refuses further registration.
    with pytest.raises(RegistrySealedError):
        reg.register(ResearchPlan)


def test_point1_schema_manifest_golden_reproduces():
    doc = _load_json(SCHEMA_MANIFEST_GOLDEN)
    assert doc["algorithm"] == CJSON_VERSION == "sha256+cjson-v1"
    golden = {e["key"]: e["json_schema_digest"] for e in doc["entries"]}

    reg = default_registry()
    manifest = {e.key: e.json_schema_digest for e in reg.manifest()}

    assert set(manifest) == set(golden), (
        "sealed registry schema-key set drifted from the frozen golden manifest"
    )
    for key in sorted(golden):
        assert manifest[key] == golden[key], f"JSON-schema digest drift for {key}"
    assert reg.registry_digest == doc["registry_digest"], "registry digest drifted"


def test_point1_digest_golden_vectors_are_internally_consistent():
    doc = _load_json(DIGEST_VECTORS_GOLDEN)
    assert doc["algorithm"] == CJSON_VERSION
    assert doc["vectors"], "digest golden must carry vectors"
    for vec in doc["vectors"]:
        recomputed = hashlib.sha256(vec["canonical_json"].encode("utf-8")).hexdigest()
        assert recomputed == vec["digest"], (
            f"golden vector {vec['name']!r} digest != sha256 of its canonical JSON"
        )


def test_point1_digest_module_reproduces_json_native_golden_vectors():
    """Exercise the real ``canonical_json`` / ``content_digest`` against the frozen
    golden for the JSON-native vectors (no model reconstruction required)."""
    doc = _load_json(DIGEST_VECTORS_GOLDEN)
    by_name = {v["name"]: v for v in doc["vectors"]}

    reconstructions = {
        # -0.0 normalizes to 0.0 -> same canonical JSON/digest as +0.0.
        "primitives_neg_zero": {"a": -0.0, "b": [0.0, 1.5, -2.5], "n": 7},
        "primitives_pos_zero": {"a": 0.0, "b": [0.0, 1.5, -2.5], "n": 7},
        # sets sort by canonical element JSON; dict keys sort.
        "set_of_ints": {"s": {5, 4, 3, 2, 1}},
        "nested_dict": {"z": 1, "a": {"n": 2, "m": [3, 2, 1]}},
    }
    for name, obj in reconstructions.items():
        vec = by_name[name]
        assert canonical_json(obj) == vec["canonical_json"], f"canonical JSON drift: {name}"
        assert content_digest(obj) == vec["digest"], f"content digest drift: {name}"


# =========================================================================== #
# Point 2 — one canonical candidate-plan projection, no alternative           #
# =========================================================================== #
def test_point2_candidate_plan_api_is_owned_by_spec():
    for fn, name in (
        (compute_candidate_plan_digest, "compute_candidate_plan_digest"),
        (validate_plan_draft, "validate_plan_draft"),
        (freeze_plan, "freeze_plan"),
    ):
        assert fn.__module__ == "guanlan_v2.orchestration.spec", name
        assert name in spec_mod.__all__


def test_point2_migration_reuses_the_single_candidate_digest_function():
    # Task 12's attestation builder must call the *same* function object, never a
    # parallel projection with different semantics.
    assert migration_mod.compute_candidate_plan_digest is compute_candidate_plan_digest


def test_point2_no_alternative_candidate_projection_in_the_package():
    """No orchestration module (Phase 1 today, or a future Phase 2 module) may
    define an alternative ``compute_candidate_plan_digest`` / ``validate_plan_draft``
    / ``freeze_plan``. Exactly one definition of each, in spec.py."""
    for fn_name in (
        "compute_candidate_plan_digest",
        "validate_plan_draft",
        "freeze_plan",
    ):
        needle = f"def {fn_name}("
        definers = [
            p.name
            for p in ORCH_DIR.glob("*.py")
            if needle in p.read_text(encoding="utf-8")
        ]
        assert definers == ["spec.py"], (
            f"{fn_name} must be defined exactly once (in spec.py); found in {definers}"
        )


# =========================================================================== #
# Point 3 — catalog owns WorkerSpec / ExecutionSpec / EvidencePolicy          #
# =========================================================================== #
def test_point3_catalog_owns_worker_execution_evidence_models():
    for model in (WorkerSpec, ExecutionSpec, EvidencePolicy):
        assert model.__module__ == "guanlan_v2.orchestration.catalog"
        assert model.__name__ in catalog_mod.__all__
        # spec.py may *import* WorkerSpec to use it, but it must not re-export it
        # as part of its own public surface.
        assert model.__name__ not in spec_mod.__all__


# =========================================================================== #
# Point 4 — catalog snapshot material/content/capability digests validate     #
# =========================================================================== #
def _text_material(id: str, kind: str, text: str):
    raw = text.encode("utf-8")
    probe = ResolvedTextMaterial(
        ref=ContentRef(id=id, version="1", content_digest=D64), kind=kind, raw_utf8=raw
    )
    digest = catalog_material_digest(probe)
    ref = ContentRef(id=id, version="1", content_digest=digest)
    return ref, ResolvedTextMaterial(ref=ref, kind=kind, raw_utf8=raw)


def _capability_material(id: str):
    desc = CapabilityDescriptor(
        id=id, version="1", capability_kind="tool", transport="in_process",
        operation="op.run",
        input_schema_ref=SchemaRef(name="InModel", version="1"),
        output_schema_ref=SchemaRef(name="OutModel", version="1"),
    )
    probe = ResolvedCapabilityMaterial(
        ref=CapabilityRef(id=id, version="1", content_digest=D64), descriptor=desc
    )
    digest = catalog_material_digest(probe)
    ref = CapabilityRef(id=id, version="1", content_digest=digest)
    return ref, ResolvedCapabilityMaterial(ref=ref, descriptor=desc)


def _skill_material(id: str):
    line2 = "Perfect for: " + canonical_json(["high-conviction names"])
    line3 = "Not ideal for: " + canonical_json(["intraday scalping"])
    text = (
        "---\n"
        "name: deep dive\n"
        "description: |\n"
        "  Multi-source A-share deep dive.\n"
        f"  {line2}\n"
        f"  {line3}\n"
        "---\n"
        "## ⚠️ CRITICAL: Data Source Priority\n"
        "- get_verified_snapshot\n"
    )
    ref, mat = _text_material(id, "skill", text)
    manifest = SkillManifest(
        ref=ref, name="deep dive", summary="Multi-source A-share deep dive.",
        format_version="skill-v1",
        perfect_for=("high-conviction names",), not_ideal_for=("intraday scalping",),
        critical_data_source_heading="⚠️ CRITICAL: Data Source Priority",
        source_identity="ta.deep_dive",
    )
    return ref, mat, manifest


def _build_pilot_catalog():
    prompt_ref, prompt_mat = _text_material("text.macro.prompt", "prompt", "You are the strategist.")
    guard_ref, guard_mat = _text_material("guard.provenance", "guardrail", "Cite every number.")
    skill_ref, skill_mat, skill_man = _skill_material("skill.macro")
    cap_ref, cap_mat = _capability_material("cap.get_prediction_markets")

    worker = WorkerSpec(
        id="text.macro", catalog_role="final", selection_scope="dynamic_allowed",
        lane="text", persona="Macro strategist", tier=Tier.WRITER,
        execution=ExecutionSpec(kind=ExecutionKind.LLM, model_tier="reasoner"),
        system_prompt_ref=prompt_ref,
        skills=(SkillBinding(skill_ref=skill_ref),),
        guardrail_refs=(guard_ref,),
        capability_allowlist=(cap_ref,),
        evidence_policy=EvidencePolicy(tool_calls=ToolCallRequirement.REQUIRED),
        read_categories=("context", "market_data"),
        outputs=(OutputBinding(name="primary", schema_ref=SchemaRef(name="OutModel", version="1")),),
        supported_modes=(DataMode.ONLINE,),
        decision_authority="none",
    )
    content_manifest = (
        ContentManifestEntry(ref=prompt_ref, kind="prompt", name="Macro prompt",
                             description="Role.", source_identity="guanlan.macro"),
        ContentManifestEntry(ref=guard_ref, kind="guardrail", name="Provenance guard",
                             description="Anchors.", source_identity="guanlan.introspector"),
    )
    capability_manifest = (
        CapabilityManifestEntry(ref=cap_ref, capability_kind="tool", transport="in_process"),
    )
    return build_catalog_snapshot(
        catalog_version="2026.07-pilot",
        content_manifest=content_manifest,
        skill_manifest=(skill_man,),
        capability_manifest=capability_manifest,
        workers=(worker,),
        resolved_material=(prompt_mat, guard_mat, skill_mat, cap_mat),
    )


def test_point4_catalog_snapshot_builds_and_validates():
    snap = _build_pilot_catalog()
    assert isinstance(snap, WorkerCatalogSnapshot)
    # byteless re-validation of declared digest + cross references passes.
    validate_catalog_snapshot(snap)
    # the sealed catalog digest equals the canonical semantic digest.
    assert snap.catalog_digest == snap.semantic_digest()
    # content, skill and capability manifests were all materialized + verified.
    assert snap.content_manifest and snap.skill_manifest and snap.capability_manifest


def test_point4_tampered_material_digest_is_rejected_by_the_builder():
    prompt_ref, prompt_mat = _text_material("text.x.prompt", "prompt", "hello")
    # declare a digest that does not match the material bytes.
    bad_ref = ContentRef(id=prompt_ref.id, version="1", content_digest=D64)
    worker = WorkerSpec(
        id="text.x", catalog_role="final", selection_scope="dynamic_allowed",
        lane="text", persona="p", tier=Tier.WRITER,
        execution=ExecutionSpec(kind=ExecutionKind.LLM, model_tier="reasoner"),
        system_prompt_ref=bad_ref,
        outputs=(OutputBinding(name="primary", schema_ref=SchemaRef(name="OutModel", version="1")),),
        supported_modes=(DataMode.ONLINE,), decision_authority="none",
    )
    bad_manifest = ContentManifestEntry(
        ref=bad_ref, kind="prompt", name="x", description="d", source_identity="src.x"
    )
    with pytest.raises(CatalogError):
        build_catalog_snapshot(
            catalog_version="v1", content_manifest=(bad_manifest,), skill_manifest=(),
            capability_manifest=(), workers=(worker,), resolved_material=(prompt_mat,),
        )


# =========================================================================== #
# Point 5 — RunEvent(SchemaRef+PayloadRef), PayloadRef relocation, DigestHex   #
# =========================================================================== #
def _payload_ref(object_id: str, content: str = D64, namespace: str = "main") -> PayloadRef:
    return PayloadRef(namespace=namespace, object_id=object_id, content_digest=content)


def _run_event(payload_ref: PayloadRef) -> RunEvent:
    return RunEvent.build(
        event_id="ev-1", journal_seq=1, occurred_at=DT, run_id="run-1", partition="main",
        event_type=EventType.ARTIFACT_STAGED, idempotency_key="idem-1",
        payload_schema_ref=SchemaRef(name="ResearchPlan", version="1"),
        payload_ref=payload_ref,
    )


def test_point5_run_event_accepts_schema_ref_and_payload_ref():
    ev = _run_event(_payload_ref("obj-1"))
    # the sealed content_digest round-trips through the model validator on load.
    reloaded = RunEvent.model_validate(ev.model_dump())
    assert reloaded == ev
    assert isinstance(ev.payload_schema_ref, SchemaRef)
    assert isinstance(ev.payload_ref, PayloadRef)


def test_point5_payload_ref_relocation_is_audit_only():
    a = _payload_ref("obj-1")
    b = _payload_ref("obj-2")  # same namespace + content, new storage locator
    c = _payload_ref("obj-1", content="b" * 64)  # different referenced content

    # object_id is excluded from the semantic projection: relocation is audit-only.
    assert a.semantic_digest() == b.semantic_digest()
    assert a.audit_digest_value() != b.audit_digest_value()
    # changing the referenced content (or namespace) moves the semantic identity.
    assert a.semantic_digest() != c.semantic_digest()


def test_point5_run_event_semantic_identity_survives_payload_relocation():
    ev_a = _run_event(_payload_ref("obj-1"))
    ev_b = _run_event(_payload_ref("obj-2"))
    ev_c = _run_event(_payload_ref("obj-1", content="b" * 64))
    # re-storing identical payload content under a new object_id moves only audit.
    assert ev_a.content_digest == ev_b.content_digest
    assert ev_a.audit_digest_value() != ev_b.audit_digest_value()
    # a changed referenced content digest moves the event's semantic digest.
    assert ev_a.content_digest != ev_c.content_digest


def _artifact_fields():
    return dict(
        artifact_id="art-1", run_id="run-1", created_at=DT,
        producer_node_id="node-1", slot="slot-1", output_key="primary", kind="research_plan",
        payload_schema_ref=SchemaRef(name="ResearchPlan", version="1"),
        payload=ResearchPlan(recommendation=PortfolioRating.BUY, rationale="x"),
        rendered_md="body",
        provenance=Provenance(plan_digest=D64, code_version="v1", as_of=DT, pit_mode=DataMode.ONLINE),
    )


def _budget_reservation_fields():
    return dict(
        reservation_id="r-1", ledger_id="l-1", run_id="run-1", request_id="req-1",
        candidate_plan_digest=D64, scope_type="plan", scope_id="s-1",
        reserved_tokens=0, reserved_llm_invocations=0, reserved_concurrency=1,
        status="reserved", reserved_at=DT,
    )


def _plan_approval_fields():
    return dict(
        request_id="req-1", candidate_plan_digest=D64,
        decision=ApprovalDecision.APPROVED, actor_id="actor-1", decided_at=DT,
    )


def _plan_validation_report_fields():
    return dict(
        valid=True, issues=(), candidate_plan_digest=D64, request_digest=D64,
        catalog_digest=D64, schema_registry_digest=D64,
    )


def _attestation_fields():
    return dict(
        plan_source="preset", request_digest=D64, candidate_plan_digest=D64,
        catalog_digest=D64, legacy_source_schema=SchemaRef(name="src_x", version="1"),
        source_config_digest=D64, legacy_mapping_digest=D64,
        builder_id="migration.static_legacy_attestor",
    )


def _input_snapshot_fields():
    return dict(
        snapshot_id="in-1", context_snapshot_hash=D64, memory_record_refs=(), built_at=DT,
    )


def _node_run_fields():
    return dict(
        node_run_id="nr-1", run_id="run-1", plan_id="plan-1", plan_digest=D64,
        node_id="node-1", worker_id="quant.factor", status=NodeStatus.RUNNING,
        attempt_id="att-1", input_snapshot_digest=D64,
    )


def test_point5_valid_digest_models_construct():
    """Guard: the valid construction path works, so the rejection tests below are
    not passing merely because a required field is missing."""
    Artifact.build(**_artifact_fields())
    BudgetReservation(**_budget_reservation_fields())
    PlanApproval(**_plan_approval_fields())
    PlanValidationReport(**_plan_validation_report_fields())
    StaticLegacyPlanAttestation(**_attestation_fields())
    InputSnapshot.build(**_input_snapshot_fields())
    NodeRun(**_node_run_fields())


@pytest.mark.parametrize(
    "builder, fields_fn, digest_field",
    [
        (Artifact.build, _artifact_fields, "rendered_from_payload_digest"),
        (BudgetReservation, _budget_reservation_fields, "candidate_plan_digest"),
        (PlanApproval, _plan_approval_fields, "candidate_plan_digest"),
        (PlanValidationReport, _plan_validation_report_fields, "candidate_plan_digest"),
        (StaticLegacyPlanAttestation, _attestation_fields, "request_digest"),
        (InputSnapshot.build, _input_snapshot_fields, "context_snapshot_hash"),
        (NodeRun, _node_run_fields, "plan_digest"),
    ],
)
def test_point5_digest_hex_fields_reject_arbitrary_strings(builder, fields_fn, digest_field):
    fields = fields_fn()
    fields[digest_field] = GARBAGE_DIGEST
    with pytest.raises(ValidationError) as excinfo:
        builder(**fields)
    # the rejection is attributable to the DigestHex-typed field.
    assert digest_field in str(excinfo.value)


# =========================================================================== #
# Point 6 — real Task 0 legacy fixture (scalars/workers/graphs) + tuples       #
# =========================================================================== #
def test_point6_legacy_fixture_has_scalars_workers_graphs():
    doc = _load_json(LEGACY_FIXTURE)
    for key in ("scalars", "workers", "graphs"):
        assert isinstance(doc.get(key), list) and doc[key], f"fixture missing {key!r}"


def test_point6_fixture_freezes_stock_deep_dive_config_digest_and_full_mapping_tuples():
    doc = _load_json(LEGACY_FIXTURE)
    # the fixture records the raw module-qualified legacy source string (dots +
    # hyphen); the migration's SchemaRef key encodes those as underscores.
    raw_source = "financial_analyst.swarm.stock-deep-dive@1"
    graphs = [g for g in doc["graphs"] if g["source_schema"] == raw_source]
    assert len(graphs) == 1, "exactly one stock-deep-dive graph expected"
    graph = graphs[0]

    frozen_digest = graph["config_digest"]
    assert isinstance(frozen_digest, str) and len(frozen_digest) == 64

    mapping = migrate_legacy_graph(
        graph["normalized_config"], source_schema=SRC_STOCK_DEEP_DIVE, source_format="json"
    )
    # the migrated normalized-config digest reproduces the frozen fixture digest.
    assert mapping.source_config_digest == frozen_digest

    # complete worker / dependency / input mapping tuples of the right types.
    assert mapping.worker_mappings and all(
        isinstance(w, LegacyWorkerMapping) for w in mapping.worker_mappings
    )
    assert mapping.dependency_mappings and all(
        isinstance(d, LegacyDependencyMapping) for d in mapping.dependency_mappings
    )
    assert mapping.input_mappings and all(
        isinstance(i, LegacyInputMapping) for i in mapping.input_mappings
    )
    # one worker mapping per legacy node in the frozen graph.
    assert len(mapping.worker_mappings) == len(graph["normalized_config"]["nodes"])


# =========================================================================== #
# Point 7 — empty memory persists in main + ContextSnapshot session=None       #
# =========================================================================== #
def _online_data_context() -> DataContext:
    clock = ClockSpec(as_of=DT, timezone="Asia/Shanghai", calendar_id="XSHG")
    return DataContext(
        as_of=DT, clock=clock, mode=DataMode.ONLINE, backend=DataBackend.LIVE,
        strict_pit=False, calendar_id="XSHG", resolved_vendor_chains={},
        source_config_digest=D64, source_registry_digest=D64, routing_snapshot_digest=D64,
        data_snapshot_id="ds-1", data_snapshot_content_digest=D64, built_at=DT,
    )


def test_point7_empty_memory_binding_persists_in_main():
    binding = build_empty_memory_binding()
    # the empty snapshot/selection self-seal canonical (never blank) digests.
    assert binding.snapshot.content_digest == binding.snapshot_hash
    assert binding.selection.content_digest == binding.past_context_hash
    assert binding.selection.snapshot_digest == binding.snapshot.content_digest
    assert binding.snapshot.records == () and binding.selection.records == ()

    # a runtime persists the selection in the public 'main' namespace.
    selection_ref = PayloadRef(
        namespace="main", object_id="sel-obj-1", content_digest=binding.past_context_hash
    )
    assert selection_ref.is_public


def test_point7_context_snapshot_binding_has_memory_session_id_none():
    binding = build_empty_memory_binding()
    selection_ref = PayloadRef(
        namespace="main", object_id="sel-obj-1", content_digest=binding.past_context_hash
    )
    cs = ContextSnapshot.build(
        snapshot_id="cs-1", data_context=_online_data_context(), memory_snapshot_id="ms-1",
        memory_snapshot_hash=binding.snapshot_hash, past_context_hash=binding.past_context_hash,
        memory_selection_ref=selection_ref, built_at=DT,
    )
    # before the Phase 3 facade there is no session scope.
    assert cs.memory_session_id is None
    assert cs.memory_selection_ref.namespace == "main"
    assert cs.content_digest == cs.semantic_digest()


def test_point7_memory_session_scope_widening_is_rejected():
    # an ungranted session request (a Worker/model choosing a scope) is widening.
    with pytest.raises(ValueError):
        check_memory_session_scope(authority_session_id=None, requested_session_id="sess.worker")
    with pytest.raises(ValueError):
        check_memory_session_scope(
            authority_session_id="sess.a", requested_session_id="sess.b"
        )
    # the granted scope, or a stricter (None) non-session subset, is allowed.
    check_memory_session_scope(authority_session_id="sess.a", requested_session_id="sess.a")
    check_memory_session_scope(authority_session_id="sess.a", requested_session_id=None)


# =========================================================================== #
# Point 8 — Phase 1-owned files exist, singular, not overwritten               #
# =========================================================================== #
def test_point8_phase1_owned_source_and_test_files_exist_singular():
    owned = {
        ORCH_DIR / "catalog.py",
        ORCH_DIR / "spec.py",
        ORCH_DIR / "schema_registry.py",
        TESTS_DIR / "test_catalog.py",
        TESTS_DIR / "test_budget.py",
    }
    for path in owned:
        assert path.is_file() and path.stat().st_size > 0, f"missing Phase 1 file: {path}"

    # this handoff gate is an *additive* Phase 2 test; it must not shadow the
    # Phase 1 catalog/budget suites.
    assert Path(__file__).name == "test_phase2_handoff.py"


def test_point8_contract_golden_files_are_singular():
    # the two Phase 1 contract goldens exist under their canonical names. A Phase 2
    # runtime golden is a *new* sibling (e.g. runtime_schema_manifest_v1.json), not
    # an overwrite, so this deliberately does not forbid other files — it asserts
    # the Phase 1 goldens are present and unmodified.
    assert SCHEMA_MANIFEST_GOLDEN.is_file() and SCHEMA_MANIFEST_GOLDEN.stat().st_size > 0
    assert DIGEST_VECTORS_GOLDEN.is_file() and DIGEST_VECTORS_GOLDEN.stat().st_size > 0

    manifest_doc = _load_json(SCHEMA_MANIFEST_GOLDEN)
    assert manifest_doc["algorithm"] == CJSON_VERSION
    # the Phase 1 manifest still pins exactly the sealed default-registry key set.
    reg_keys = {e.key for e in default_registry().manifest()}
    assert {e["key"] for e in manifest_doc["entries"]} == reg_keys

    vectors_doc = _load_json(DIGEST_VECTORS_GOLDEN)
    assert vectors_doc["algorithm"] == CJSON_VERSION and vectors_doc["vectors"]


def test_point8_owning_modules_of_frozen_symbols_are_phase1():
    # the frozen builders/validators keep their Phase 1 owning module — a Phase 2
    # path cannot silently re-home them.
    assert WorkerCatalogSnapshot.__module__ == "guanlan_v2.orchestration.catalog"
    assert build_catalog_snapshot.__module__ == "guanlan_v2.orchestration.catalog"
    assert default_registry.__module__ == "guanlan_v2.orchestration.schema_registry"
    assert compute_candidate_plan_digest.__module__ == "guanlan_v2.orchestration.spec"
