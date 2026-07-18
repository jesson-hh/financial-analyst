# -*- coding: utf-8 -*-
"""Task 0 — the executable Phase 1/2/3 -> Phase 4 handoff gate.

This is a *consumer* gate, not a red->green TDD task: it imports the frozen
Phase 1 (amended) contract layer, the sealed Phase 2 runtime kernel and the
Phase 3 data/PIT + memory facade exactly as the Phase 4 Evaluator-Optimizer /
Governor stack will, exercises the real registries / builders / stores / digests
through their public surfaces, and asserts the upstream API surface Phase 4
imports is intact and singular. Every assertion here PASSES against the
already-existing upstream code (branch ``report-evidence-pack`` at authoring;
``pytest tests/orchestration`` collects ~1950 tests green). A failure means an
upstream Phase 1/2/3 contract drifted from its frozen exit gate and BLOCKS
Phase 4 — it must NEVER be "fixed" by weakening an assertion or editing
upstream; fix the owning phase instead.

It proves the eight points of ``.superpowers/sdd/task-0-brief.md`` Step 1:

1. the Phase 1 amended golden (``schema_manifest_v1.json``, 11 registered schemas
   incl. ``TypedPayloadRef@1`` / ``InputArtifactBinding@1`` /
   ``ContextRuntimeRequirements@1``) and digest golden vectors reproduce, and
   ``default_registry()`` is sealed;
2. ``TypedPayloadRef(schema_ref: SchemaRef, payload_ref: PayloadRef)`` resolves
   from ``guanlan_v2.orchestration.refs`` and plain ``PayloadRef`` (object_id
   relocation = audit-only) is unchanged;
3. ``ExperimentStatus`` (with ``WAITING_FOR_MATURITY`` / ``PASSED_VALIDATION`` /
   ``SEALED_EVALUATING``) resolves from ``enums.py`` and
   ``EventType.EXPERIMENT_STATE_CHANGED`` exists — while the Trial/Holdout event
   types are still absent (that half is deleted by Task 3's flip);
4. the Phase 2 store/clock exports resolve with their public method surface, and
   ``refs.py`` payload namespaces include ``"sealed"`` with
   ``PUBLIC_PAYLOAD_NAMESPACES == frozenset({"main"})``;
5. the Phase 3 registry/catalog/namespace/error exports resolve and the sealed
   full registry + catalog reproduce their frozen digests;
6. the reuse-surface facts (research loop gate, ``run_graph`` /
   ``metrics_of_terminal``, ``workflow.api`` overfitting statistics, CPCV
   helpers, factor-vintage gate, factorlib save-status) still hold;
7. the Phase 1 amendment's evidence surface holds: ``Provenance`` carries
   ``data_result_refs`` / ``execution_evidence_refs`` typed
   ``tuple[TypedPayloadRef, ...]`` and ``ContextSnapshot`` carries
   ``memory_snapshot_ref: TypedPayloadRef`` + ``runtime_requirements_ref``;
8. the Phase 1/2/3-owned modules / tests / goldens exist, are singular, keep
   their owning module, and are not shadowed by this additive gate.

Step 2's reviewed upstream digests are recorded verbatim as module constants and
re-derived from the sealed builders below.

Run from repo root: ``python -m pytest tests/orchestration/test_phase4_handoff.py -v``
"""
from __future__ import annotations

import hashlib
import inspect
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

# -- Phase 1 (amended) contract layer --------------------------------------- #
import guanlan_v2.orchestration as orch_pkg
from guanlan_v2.orchestration.digest import (
    CJSON_VERSION,
    canonical_json,
    content_digest,
)
from guanlan_v2.orchestration.refs import (
    NON_PUBLIC_PAYLOAD_NAMESPACES,
    PAYLOAD_NAMESPACES,
    PUBLIC_PAYLOAD_NAMESPACES,
    PayloadRef,
    SchemaRef,
    TypedPayloadRef,
)
from guanlan_v2.orchestration.schema_registry import (
    RegistrySealedError,
    SchemaRegistry,
    default_registry,
)
from guanlan_v2.orchestration.enums import ExperimentStatus
from guanlan_v2.orchestration.events import EventType
from guanlan_v2.orchestration.schemas import Artifact, Provenance, ResearchPlan
from guanlan_v2.orchestration.enums import DataBackend, DataMode, PortfolioRating
from guanlan_v2.orchestration.context import (
    ClockSpec,
    ContextSnapshot,
    DataContext,
    build_empty_memory_binding,
)

# -- Phase 2 runtime kernel ------------------------------------------------- #
from guanlan_v2.orchestration import budget as budget_mod
from guanlan_v2.orchestration.eventstore import (
    EventRefusalAuditSink,
    EventStore,
    IdempotencyConflict,
    PayloadStore,
    RuntimeStateCellStore,
    RuntimeUnitOfWork,
    StateCellCompareAndSwapCommand,
)
from guanlan_v2.orchestration.runtime_clock import AuthoritativeClock

# -- Phase 3 data/PIT + memory facade --------------------------------------- #
from guanlan_v2.orchestration.data.errors import FutureDataRefused
from guanlan_v2.orchestration.memory import catalog as mem_catalog
from guanlan_v2.orchestration.memory import schema_registry as mem_schema_registry
from guanlan_v2.orchestration.memory.catalog import (
    PHASE3_FULL_CATALOG_DIGEST,
    build_phase3_full_catalog,
    phase3_full_catalog_snapshot,
)
from guanlan_v2.orchestration.memory.models import PHASE3_MEMORY_STATE_CELL_NAMESPACES
from guanlan_v2.orchestration.memory.schema_registry import (
    PHASE3_FULL_BASE_REGISTRY_DIGEST,
    PHASE3_FULL_REGISTRY_DIGEST,
    build_phase3_full_registry,
)

# -- Reuse surface (consumed unchanged by Phase 4) -------------------------- #
from guanlan_v2.research import loop as research_loop
from guanlan_v2.workflow import api as workflow_api
from guanlan_v2.workflow import executor as workflow_executor
from guanlan_v2.strategy.compute import cpcv as strategy_cpcv
from guanlan_v2.screen import factor_vintage as screen_factor_vintage
from guanlan_v2.factorlib import api as factorlib_api

# --------------------------------------------------------------------------- #
# Paths + fixtures                                                            #
# --------------------------------------------------------------------------- #
TESTS_DIR = Path(__file__).resolve().parent
GOLDEN_DIR = TESTS_DIR / "golden"
ORCH_DIR = Path(orch_pkg.__file__).resolve().parent

SCHEMA_MANIFEST_GOLDEN = GOLDEN_DIR / "schema_manifest_v1.json"
DIGEST_VECTORS_GOLDEN = GOLDEN_DIR / "digest_vectors_v1.json"

UTC = timezone.utc
DT = datetime(2026, 7, 17, 8, 30, tzinfo=UTC)
D64 = "a" * 64
DB = "b" * 64

# =========================================================================== #
# Step 2 — reviewed upstream digests recorded verbatim.                        #
# These are the *exact* sealed Phase 1 amended registry digest and the Phase 3 #
# cumulative full registry / catalog digests as implemented and reviewed on    #
# this branch — not a local path, a guess, or a mutable singleton identity.    #
# They are re-derived from the sealed builders in the Step-2 tests below.       #
# =========================================================================== #
PHASE1_AMENDED_REGISTRY_DIGEST = (
    "75f7920db13cdcaac89a70e0103812a29348ab3caaa98b9c1020429bb4e18b03"
)
PHASE3_DATA_REGISTRY_DIGEST = (
    "9119fa179a598b3d2d62e059747c6fd581d4e8b3be5eea0304da47d80ca51612"
)
PHASE3_FULL_REGISTRY_DIGEST_FROZEN = (
    "ff2a56d283499e347dcdbf4c2f5c5977710fd5575afbc94ee34819b2b531e49d"
)
PHASE3_FULL_CATALOG_DIGEST_FROZEN = (
    "c13294e5f020de542cc553d92e509d3dfea45673700373ac0c013d9115c3a773"
)

#: The 11 Phase 1 amended registry schema keys (order-independent).
PHASE1_SCHEMA_KEYS = frozenset(
    {
        "ContextRuntimeRequirements@1",
        "ContextSnapshot@1",
        "EmptyMemorySelection@1",
        "EmptyMemorySnapshot@1",
        "InputArtifactBinding@1",
        "InputSnapshot@1",
        "MemoryRecordRef@1",
        "PortfolioDecision@1",
        "ResearchPlan@1",
        "SentimentReport@1",
        "TypedPayloadRef@1",
    }
)


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _sr(name: str, version: str = "1") -> SchemaRef:
    return SchemaRef(name=name, version=version)


def _payload_ref(object_id: str, content: str = D64, namespace: str = "main") -> PayloadRef:
    return PayloadRef(namespace=namespace, object_id=object_id, content_digest=content)


def _typed(schema: str, object_id: str, content: str = D64, namespace: str = "main") -> TypedPayloadRef:
    return TypedPayloadRef(schema_ref=_sr(schema), payload_ref=_payload_ref(object_id, content, namespace))


# =========================================================================== #
# Point 1 — Phase 1 amended golden reproduces + sealed registry                #
# =========================================================================== #
def test_point1_default_registry_is_sealed_and_frozen():
    reg = default_registry()
    assert isinstance(reg, SchemaRegistry)
    assert reg.sealed is True
    # a sealed registry is what Phase 4 inherits; it refuses further registration.
    with pytest.raises(RegistrySealedError):
        reg.register(ResearchPlan)


def test_point1_amended_schema_manifest_golden_reproduces_11_schemas():
    doc = _load_json(SCHEMA_MANIFEST_GOLDEN)
    assert doc["algorithm"] == CJSON_VERSION == "sha256+cjson-v1"
    golden = {e["key"]: e["json_schema_digest"] for e in doc["entries"]}

    reg = default_registry()
    manifest = {e.key: e.json_schema_digest for e in reg.manifest()}

    # exactly the 11 amended schema keys, incl. the three amendment landings.
    assert set(golden) == PHASE1_SCHEMA_KEYS
    for landed in ("TypedPayloadRef@1", "InputArtifactBinding@1", "ContextRuntimeRequirements@1"):
        assert landed in golden, f"amendment schema {landed} missing from the frozen golden"
    assert set(manifest) == set(golden), (
        "sealed registry schema-key set drifted from the frozen amended golden manifest"
    )
    for key in sorted(golden):
        assert manifest[key] == golden[key], f"JSON-schema digest drift for {key}"
    assert reg.registry_digest == doc["registry_digest"] == PHASE1_AMENDED_REGISTRY_DIGEST


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
    doc = _load_json(DIGEST_VECTORS_GOLDEN)
    by_name = {v["name"]: v for v in doc["vectors"]}
    reconstructions = {
        "primitives_neg_zero": {"a": -0.0, "b": [0.0, 1.5, -2.5], "n": 7},
        "primitives_pos_zero": {"a": 0.0, "b": [0.0, 1.5, -2.5], "n": 7},
        "set_of_ints": {"s": {5, 4, 3, 2, 1}},
        "nested_dict": {"z": 1, "a": {"n": 2, "m": [3, 2, 1]}},
    }
    for name, obj in reconstructions.items():
        if name not in by_name:  # tolerate an extended golden set
            continue
        vec = by_name[name]
        assert canonical_json(obj) == vec["canonical_json"], f"canonical JSON drift: {name}"
        assert content_digest(obj) == vec["digest"], f"content digest drift: {name}"


# =========================================================================== #
# Point 2 — TypedPayloadRef(schema_ref, payload_ref) + PayloadRef unchanged     #
# =========================================================================== #
def test_point2_typed_payload_ref_composes_schema_ref_and_payload_ref():
    typed = TypedPayloadRef(schema_ref=_sr("ResearchPlan"), payload_ref=_payload_ref("obj-1"))
    assert typed.__class__.__module__ == "guanlan_v2.orchestration.refs"
    assert isinstance(typed.schema_ref, SchemaRef)
    assert isinstance(typed.payload_ref, PayloadRef)
    # the composite's field set is exactly (schema_version, schema_ref, payload_ref).
    assert set(TypedPayloadRef.model_fields) == {"schema_version", "schema_ref", "payload_ref"}


def test_point2_plain_payload_ref_relocation_is_audit_only():
    a = _payload_ref("obj-1")
    b = _payload_ref("obj-2")  # same namespace + content, new storage locator
    c = _payload_ref("obj-1", content=DB)  # different referenced content
    # object_id is excluded from the semantic projection: relocation is audit-only.
    assert a.semantic_digest() == b.semantic_digest()
    assert a.audit_digest_value() != b.audit_digest_value()
    # changing referenced content (or namespace) moves the semantic identity.
    assert a.semantic_digest() != c.semantic_digest()
    assert a.is_public and not _payload_ref("o", namespace="sealed").is_public


def test_point2_typed_ref_relocation_is_audit_only_content_and_schema_are_semantic():
    a = _typed("ResearchPlan", "obj-1")
    b = _typed("ResearchPlan", "obj-2")  # relocated payload
    c = _typed("ResearchPlan", "obj-1", content=DB)  # new referenced content
    d = _typed("PortfolioDecision", "obj-1")  # different schema identity
    assert a.semantic_digest() == b.semantic_digest()
    assert a.audit_digest_value() != b.audit_digest_value()
    assert a.semantic_digest() != c.semantic_digest()
    assert a.semantic_digest() != d.semantic_digest()


# =========================================================================== #
# Point 3 — ExperimentStatus members + EXPERIMENT_STATE_CHANGED present         #
# =========================================================================== #
def test_point3_experiment_status_has_the_phase4_lifecycle_members():
    for name in ("RUNNING", "WAITING_FOR_MATURITY", "PASSED_VALIDATION",
                 "SEALED_EVALUATING", "COMPLETED", "FAILED"):
        assert hasattr(ExperimentStatus, name), f"ExperimentStatus.{name} missing"
    assert ExperimentStatus.WAITING_FOR_MATURITY.value == "waiting_for_maturity"
    assert ExperimentStatus.PASSED_VALIDATION.value == "passed_validation"
    assert ExperimentStatus.SEALED_EVALUATING.value == "sealed_evaluating"


def test_point3_experiment_state_changed_event_type_exists():
    assert EventType.EXPERIMENT_STATE_CHANGED.value == "ExperimentStateChanged"


def test_point3_trial_and_holdout_event_types_are_still_absent():
    """DELETED BY TASK 3: this half of point 3 asserts the pre-Phase-4 vocabulary.
    Task 3's additive ``events.py`` extension adds ``TrialReserved`` /
    ``TrialRevealed`` / ``TrialExhausted``; that same commit removes this test
    (per the brief's Task 3 diff). Until then, the reserved names are absent."""
    values = {m.value for m in EventType}
    names = {m.name for m in EventType}
    for reserved in ("TrialReserved", "TrialRevealed", "TrialExhausted"):
        assert reserved not in values
    for reserved in ("TRIAL_RESERVED", "TRIAL_REVEALED", "TRIAL_EXHAUSTED"):
        assert reserved not in names
    assert len(EventType) == 20  # the frozen Phase 1 vocabulary size


# =========================================================================== #
# Point 4 — Phase 2 store/clock exports resolve; refs namespaces include sealed  #
# =========================================================================== #
def test_point4_phase2_store_and_clock_exports_resolve_with_public_methods():
    assert callable(getattr(PayloadStore, "put")) and callable(getattr(PayloadStore, "get"))
    for method in ("append", "journal", "visible"):
        assert callable(getattr(EventStore, method)), f"EventStore.{method} missing"
    assert callable(getattr(RuntimeUnitOfWork, "commit"))
    # RuntimeStateCellStore + the CAS command + the refusal sink + the clock port.
    assert inspect.isclass(RuntimeStateCellStore)
    assert inspect.isclass(StateCellCompareAndSwapCommand)
    assert inspect.isclass(EventRefusalAuditSink)
    assert AuthoritativeClock.__module__ == "guanlan_v2.orchestration.runtime_clock"
    # the single shared conflict type, re-exported verbatim from budget.
    assert IdempotencyConflict is budget_mod.IdempotencyConflict


def test_point4_refs_payload_namespaces_include_sealed_and_only_main_is_public():
    assert "sealed" in PAYLOAD_NAMESPACES
    assert PUBLIC_PAYLOAD_NAMESPACES == frozenset({"main"})
    assert "sealed" in NON_PUBLIC_PAYLOAD_NAMESPACES
    # a sealed payload ref is structurally non-public.
    assert not _payload_ref("o", namespace="sealed").is_public


# =========================================================================== #
# Point 5 — Phase 3 registry/catalog/namespace/error exports resolve            #
# =========================================================================== #
def test_point5_phase3_full_registry_export_reproduces_the_frozen_digest():
    assert PHASE3_FULL_REGISTRY_DIGEST == PHASE3_FULL_REGISTRY_DIGEST_FROZEN
    assert PHASE3_FULL_BASE_REGISTRY_DIGEST == PHASE3_DATA_REGISTRY_DIGEST
    rebuilt = build_phase3_full_registry(PHASE3_FULL_BASE_REGISTRY_DIGEST)
    assert rebuilt.sealed is True
    assert rebuilt.registry_digest == PHASE3_FULL_REGISTRY_DIGEST_FROZEN


def test_point5_phase3_full_catalog_export_reproduces_the_frozen_digest():
    assert PHASE3_FULL_CATALOG_DIGEST == PHASE3_FULL_CATALOG_DIGEST_FROZEN
    assert callable(build_phase3_full_catalog)
    sig = inspect.signature(build_phase3_full_catalog)
    assert list(sig.parameters)[0] == "phase3_data_snapshot"
    snap = phase3_full_catalog_snapshot()
    assert snap.catalog_digest == PHASE3_FULL_CATALOG_DIGEST_FROZEN


def test_point5_phase3_memory_state_cell_namespaces_are_sorted_unique():
    ns = PHASE3_MEMORY_STATE_CELL_NAMESPACES
    assert isinstance(ns, tuple) and ns
    assert list(ns) == sorted(ns) and len(set(ns)) == len(ns)
    for name in ns:
        assert name.startswith("memory.")


def test_point5_future_data_refused_is_an_exception_type():
    assert isinstance(FutureDataRefused, type) and issubclass(FutureDataRefused, Exception)
    assert FutureDataRefused.__module__ == "guanlan_v2.orchestration.data.errors"


# =========================================================================== #
# Point 6 — reuse-surface facts still hold                                      #
# =========================================================================== #
def test_point6_research_loop_gate_surface_is_intact():
    assert list(inspect.signature(research_loop.run_research_loop).parameters) == [
        "run_id", "goal", "max_rounds", "min_rank_ic", "universe", "freq", "start", "end", "progress",
    ]
    assert callable(research_loop._gate)
    assert research_loop.GATE_OOS_OK == "robust"
    assert research_loop._EVAL_OOS_FRAC == 0.3


def test_point6_run_graph_and_metrics_of_terminal_shapes_are_intact():
    params = inspect.signature(workflow_executor.run_graph).parameters
    assert list(params) == ["graph", "overrides", "on_node", "prefer_model_terminal"]
    assert params["overrides"].default is None
    assert params["on_node"].default is None
    assert params["prefer_model_terminal"].default is False
    # metrics_of_terminal projects the frozen six-key shape.
    assert set(workflow_executor.metrics_of_terminal({})) == {
        "rank_ic", "sharpe", "ann_return", "oos_verdict", "n_dates", "factor",
    }


def test_point6_workflow_api_overfitting_statistics_exist():
    assert callable(workflow_api._cscv_pbo)
    assert callable(workflow_api._oos_verdict)


def test_point6_cpcv_helpers_keep_their_signatures():
    assert list(inspect.signature(strategy_cpcv.deflated_sharpe).parameters) == [
        "returns", "n_trials", "sharpes_std",
    ]
    ms = inspect.signature(strategy_cpcv.make_splits).parameters
    assert list(ms) == ["dates", "n_groups", "k", "purge", "embargo"]
    assert (ms["n_groups"].default, ms["k"].default, ms["purge"].default, ms["embargo"].default) == (6, 2, 5, 5)


def test_point6_factor_vintage_and_factorlib_save_status_surface():
    assert callable(screen_factor_vintage.cs_vintage_from_frame)
    # no "rejected" save status is assumed anywhere in this plan.
    assert factorlib_api._VALID_SAVE_STATUS == {"", "draft"}


# =========================================================================== #
# Point 7 — Phase 1 amendment evidence surface holds                           #
# =========================================================================== #
def test_point7_provenance_carries_typed_evidence_tuples():
    for field in ("data_result_refs", "execution_evidence_refs"):
        assert Provenance.model_fields[field].annotation == tuple[TypedPayloadRef, ...]
    # behavioral: a typed data-result ref binds and the tuple is immutable.
    ref = _typed("DataResult", "dr-obj")
    prov = Provenance(
        plan_digest=D64, code_version="v1", as_of=DT, pit_mode=DataMode.ONLINE,
        data_result_refs=(ref,),
    )
    assert prov.data_result_refs == (ref,)
    with pytest.raises(ValidationError):
        prov.data_result_refs = ()  # frozen
    # an Artifact surfaces the same immutable typed provenance refs.
    art = Artifact.build(
        artifact_id="art-1", run_id="run-1", created_at=DT, producer_node_id="node-1",
        slot="slot-1", output_key="primary", kind="research_plan",
        payload_schema_ref=_sr("ResearchPlan"),
        payload=ResearchPlan(recommendation=PortfolioRating.BUY, rationale="x"),
        rendered_md="body", provenance=prov,
    )
    assert art.provenance.data_result_refs == (ref,)


def test_point7_context_snapshot_carries_typed_memory_and_requirements_refs():
    assert ContextSnapshot.model_fields["memory_snapshot_ref"].annotation is TypedPayloadRef
    assert ContextSnapshot.model_fields["runtime_requirements_ref"].annotation == (
        TypedPayloadRef | None
    )
    assert "memory_session_id" in ContextSnapshot.model_fields
    # a canonical empty-memory binding builds with session=None and no requirements ref.
    binding = build_empty_memory_binding()
    clock = ClockSpec(as_of=DT, timezone="Asia/Shanghai", calendar_id="XSHG")
    dc = DataContext(
        as_of=DT, clock=clock, mode=DataMode.ONLINE, backend=DataBackend.LIVE,
        strict_pit=False, calendar_id="XSHG", resolved_vendor_chains={},
        source_config_digest=D64, source_registry_digest=D64, routing_snapshot_digest=D64,
        data_snapshot_id="ds-1", data_snapshot_content_digest=D64, built_at=DT,
    )
    cs = ContextSnapshot.build(
        snapshot_id="cs-1", data_context=dc, memory_snapshot_id="ms-1",
        memory_snapshot_hash=binding.snapshot_hash, past_context_hash=binding.past_context_hash,
        memory_snapshot_ref=binding.memory_snapshot_ref,
        memory_selection_ref=binding.memory_selection_ref,
        runtime_requirements_ref=None, built_at=DT,
    )
    assert isinstance(cs.memory_snapshot_ref, TypedPayloadRef)
    assert cs.memory_session_id is None
    assert cs.runtime_requirements_ref is None
    assert cs.content_digest == cs.semantic_digest()


# =========================================================================== #
# Point 8 — upstream-owned files exist, singular, keep their owning module      #
# =========================================================================== #
def test_point8_upstream_owned_source_files_exist_singular():
    owned = {
        # Phase 1
        ORCH_DIR / "digest.py", ORCH_DIR / "refs.py", ORCH_DIR / "enums.py",
        ORCH_DIR / "events.py", ORCH_DIR / "schemas.py", ORCH_DIR / "context.py",
        ORCH_DIR / "schema_registry.py",
        # Phase 2
        ORCH_DIR / "eventstore.py", ORCH_DIR / "runtime_clock.py", ORCH_DIR / "budget.py",
        # Phase 3
        ORCH_DIR / "data" / "errors.py",
        ORCH_DIR / "memory" / "schema_registry.py", ORCH_DIR / "memory" / "catalog.py",
        ORCH_DIR / "memory" / "models.py",
    }
    for path in owned:
        assert path.is_file() and path.stat().st_size > 0, f"missing upstream file: {path}"
    # this handoff gate is an *additive* Phase 4 test; it must not shadow upstream.
    assert Path(__file__).name == "test_phase4_handoff.py"


def test_point8_phase1_goldens_are_singular_and_pin_the_sealed_key_set():
    assert SCHEMA_MANIFEST_GOLDEN.is_file() and SCHEMA_MANIFEST_GOLDEN.stat().st_size > 0
    assert DIGEST_VECTORS_GOLDEN.is_file() and DIGEST_VECTORS_GOLDEN.stat().st_size > 0
    manifest_doc = _load_json(SCHEMA_MANIFEST_GOLDEN)
    assert manifest_doc["algorithm"] == CJSON_VERSION
    # the Phase 1 manifest still pins exactly the sealed default-registry key set.
    reg_keys = {e.key for e in default_registry().manifest()}
    assert {e["key"] for e in manifest_doc["entries"]} == reg_keys == PHASE1_SCHEMA_KEYS


def test_point8_owning_modules_of_frozen_symbols_are_upstream():
    assert default_registry.__module__ == "guanlan_v2.orchestration.schema_registry"
    assert TypedPayloadRef.__module__ == "guanlan_v2.orchestration.refs"
    assert EventType.__module__ == "guanlan_v2.orchestration.events"
    assert ExperimentStatus.__module__ == "guanlan_v2.orchestration.enums"
    assert EventStore.__module__ == "guanlan_v2.orchestration.eventstore"
    assert build_phase3_full_registry.__module__ == (
        "guanlan_v2.orchestration.memory.schema_registry"
    )
    assert build_phase3_full_catalog.__module__ == "guanlan_v2.orchestration.memory.catalog"
    # the two Phase 3 digest lazy-exports resolve through their owning modules.
    assert mem_schema_registry.PHASE3_FULL_REGISTRY_DIGEST == PHASE3_FULL_REGISTRY_DIGEST_FROZEN
    assert mem_catalog.PHASE3_FULL_CATALOG_DIGEST == PHASE3_FULL_CATALOG_DIGEST_FROZEN


# =========================================================================== #
# Step 2 — the reviewed upstream digests are the frozen ones                    #
# =========================================================================== #
def test_step2_records_the_exact_phase1_amended_registry_digest():
    assert default_registry().registry_digest == PHASE1_AMENDED_REGISTRY_DIGEST


def test_step2_records_the_exact_phase3_full_registry_and_catalog_digests():
    assert build_phase3_full_registry(
        PHASE3_DATA_REGISTRY_DIGEST
    ).registry_digest == PHASE3_FULL_REGISTRY_DIGEST_FROZEN
    assert phase3_full_catalog_snapshot().catalog_digest == PHASE3_FULL_CATALOG_DIGEST_FROZEN
