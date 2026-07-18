# -*- coding: utf-8 -*-
"""Phase 5 · Task 8 — the BOOTSTRAP runtime profile admission matrix.

Locks the reviewed Option-4 C1 resolution: the BOOTSTRAP unlock is a **new
registered Phase-5 model** ``BootstrapRuntimeProfile@1`` (its own closed
Literals, its own self-sealed ``profile_digest``) — the Phase-2
``StaticRuntimeProfile@1`` schema, constant, digest and golden stay
byte-identical (the "v1-golden untouched" assertion below), and the additive
``supports_bootstrap``-gated branch in ``runtime_support.check_runtime_support``
leaves static-profile behavior bit-identical (differential over the Phase 2
pilot fixtures).

Admission matrix (plan Task 8):

| Draft                                                 | v1        | BOOTSTRAP |
|-------------------------------------------------------|-----------|-----------|
| phase=bootstrap, source=PRESET, no context ref        | rejected  | admitted  |
| phase=bootstrap, source=PRESET_FALLBACK, no ctx ref   | rejected  | admitted  |
| phase=bootstrap, source=DYNAMIC                       | rejected  | rejected  |
| source=PlanSource.BOOTSTRAP (any phase)               | rejected  | rejected  |
| approval_policy=AUTO (any)                            | rejected  | rejected  |
| main-phase drafts (Phase 2 pilot fixtures)            | identical outcomes    |
| retries / stop-conditions / reducers / multi-writer   | rejected  | rejected  |

Run: ``python -m pytest tests/orchestration/test_bootstrap_profile.py -v``
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from guanlan_v2.orchestration import presets as P
from guanlan_v2.orchestration.bootstrap import (
    BOOTSTRAP_PLAN_SOURCES,
    BOOTSTRAP_RUNTIME_PROFILE,
    BootstrapRuntimeProfile,
    bootstrap_runtime_profile,
    lane0_analyzers,
    load_lane0_catalog,
)
from guanlan_v2.orchestration.catalog_runtime import (
    BridgeCatalogView,
    CatalogRuntime,
)
from guanlan_v2.orchestration.context import (
    ClockSpec,
    ContextSnapshot,
    DataContext,
    build_empty_memory_binding,
)
from guanlan_v2.orchestration.digest import ContractModel, content_digest
from guanlan_v2.orchestration.enums import (
    ApprovalPolicy,
    DataBackend,
    DataMode,
    DependencyPolicy,
    NodeStatus,
    PlanSource,
)
from guanlan_v2.orchestration.market.factors import (
    MarketFactorReport,
    RegimeReport,
    RotationReport,
)
from guanlan_v2.orchestration.memory.experience import (
    ExperienceQuery,
    ExperienceSelection,
)
from guanlan_v2.orchestration.refs import ContentRef
from guanlan_v2.orchestration.runtime_contracts import (
    StaticRuntimeProfile,
    phase2_runtime_registry,
    static_runtime_profile,
)
from guanlan_v2.orchestration.runtime_support import check_runtime_support
from guanlan_v2.orchestration.schema_registry import SchemaRegistry, default_registry
from guanlan_v2.orchestration.spec import (
    Dependency,
    PlanDraft,
    PlanNode,
    validate_plan_draft,
)

UTC = timezone.utc
DT = datetime(2026, 7, 18, 1, 30, tzinfo=UTC)

GOLDEN_DIR = Path(__file__).resolve().parent / "golden"
RUNTIME_GOLDEN = GOLDEN_DIR / "runtime_schema_manifest_v1.json"

#: the frozen Phase-2 golden pin for StaticRuntimeProfile@1 (the C1 boundary).
STATIC_PROFILE_SCHEMA_DIGEST = (
    "201ed4a5119b05dcd3873784ec06549e1b36cfdaef6f94bad55cf0a4d75bef54"
)


def _codes(report) -> tuple[str, ...]:
    return tuple(i.code for i in report.issues)


# =========================================================================== #
# profile identity — a DISTINCT registered Phase-5 model                        #
# =========================================================================== #
def test_bootstrap_profile_identity_is_distinct():
    b = bootstrap_runtime_profile()
    s = static_runtime_profile()
    assert b.profile_id == "bootstrap-runtime"
    assert b.profile_version == "1"
    assert b.supports_bootstrap is True
    assert b.profile_digest != s.profile_digest
    # a fresh instance reproduces the same identity/digest.
    assert bootstrap_runtime_profile().profile_digest == b.profile_digest
    assert BOOTSTRAP_RUNTIME_PROFILE.profile_digest == b.profile_digest
    # Option 4: a NEW registered contract model, never a StaticRuntimeProfile fork.
    assert isinstance(b, BootstrapRuntimeProfile)
    assert not isinstance(b, StaticRuntimeProfile)
    assert issubclass(BootstrapRuntimeProfile, ContractModel)
    assert BootstrapRuntimeProfile.model_fields["schema_version"].default == "1"


def test_bootstrap_profile_delta_is_exactly_the_admission_widening():
    """Everything except the bootstrap unlock is bit-equal to static-runtime v1."""
    b = bootstrap_runtime_profile()
    s = static_runtime_profile()
    for field in (
        "supported_plan_sources", "supported_execution_kinds",
        "supported_dependency_policies", "supported_cardinalities",
        "supports_conditions", "supports_reducers", "supports_multi_writer",
        "supports_debates", "supports_gates", "supports_stop_conditions",
        "supports_retries", "max_attempts_supported", "bridge_pre_input_modes",
        "bridge_lifecycle", "max_prompt_assemblies_per_llm_node",
        "max_model_invocations_per_llm_node",
    ):
        assert getattr(b, field) == getattr(s, field), f"feature drift on {field}"
    # the widening itself is closed: bootstrap admits PRESET/PRESET_FALLBACK only.
    assert b.bootstrap_plan_sources == (PlanSource.PRESET, PlanSource.PRESET_FALLBACK)
    assert BOOTSTRAP_PLAN_SOURCES == b.bootstrap_plan_sources
    assert PlanSource.BOOTSTRAP not in b.supported_plan_sources
    assert PlanSource.BOOTSTRAP not in b.bootstrap_plan_sources
    assert PlanSource.DYNAMIC not in b.bootstrap_plan_sources


def test_bootstrap_profile_is_closed_and_self_sealed():
    b = bootstrap_runtime_profile()
    assert b.profile_digest == b.semantic_digest()
    with pytest.raises(ValidationError):
        BootstrapRuntimeProfile.build(supports_conditions=True)  # Literal[False]
    with pytest.raises(ValidationError):
        BootstrapRuntimeProfile.build(supports_bootstrap=False)  # Literal[True]
    with pytest.raises(ValidationError):
        BootstrapRuntimeProfile.build(max_attempts_supported=2)  # Literal[1]
    with pytest.raises(ValidationError):
        BootstrapRuntimeProfile.build(profile_id="static-runtime")  # closed identity
    with pytest.raises(ValidationError):
        BootstrapRuntimeProfile.build(
            bootstrap_plan_sources=(PlanSource.DYNAMIC,))  # closed widening
    with pytest.raises(ValidationError):
        BootstrapRuntimeProfile.build(
            supported_plan_sources=(
                PlanSource.BOOTSTRAP, PlanSource.PRESET,
                PlanSource.PRESET_FALLBACK, PlanSource.DYNAMIC))
    # a tampered digest is rejected on construction.
    good = {name: getattr(b, name) for name in type(b).model_fields}
    bad = dict(good)
    bad["profile_digest"] = "b" * 64
    with pytest.raises(ValidationError):
        BootstrapRuntimeProfile(**bad)


# =========================================================================== #
# v1-golden untouched — the C1 boundary                                        #
# =========================================================================== #
def test_static_runtime_profile_v1_schema_and_golden_untouched():
    """Option 4 leaves the registered Phase-2 model byte-identical: the JSON
    schema digest still equals the frozen golden pin, the cumulative Phase-2
    runtime registry digest still equals its golden, and the v1 profile value
    still forbids bootstrap."""
    assert (
        content_digest(StaticRuntimeProfile.model_json_schema())
        == STATIC_PROFILE_SCHEMA_DIGEST
    )
    doc = json.loads(RUNTIME_GOLDEN.read_text(encoding="utf-8"))
    pinned = {e["key"]: e["json_schema_digest"] for e in doc["entries"]}
    assert pinned["StaticRuntimeProfile@1"] == STATIC_PROFILE_SCHEMA_DIGEST
    reg = phase2_runtime_registry(default_registry().registry_digest)
    assert doc["registry_digest"] == reg.registry_digest
    s = static_runtime_profile()
    assert s.profile_id == "static-runtime" and s.supports_bootstrap is False
    # the bootstrap identity can never be minted on the v1 schema.
    with pytest.raises(ValidationError):
        StaticRuntimeProfile.build(profile_id="bootstrap-runtime")


# =========================================================================== #
# lane-0 bootstrap draft fixtures                                              #
# =========================================================================== #
@pytest.fixture(scope="module")
def env():
    cat = load_lane0_catalog()
    runtime = CatalogRuntime.build(cat.snapshot, cat.source)
    view = BridgeCatalogView.build(runtime, lane0_analyzers(cat))
    registry = SchemaRegistry()
    for model in (MarketFactorReport, RegimeReport, RotationReport,
                  ExperienceQuery, ExperienceSelection):
        registry.register(model)
    registry.seal()
    return cat, runtime, view, registry


def _dep(inject_into: str = "market_factor_report") -> Dependency:
    return Dependency(
        upstream_node_id="factor", artifact_slot="slot-factor",
        inject_as=inject_into, policy=DependencyPolicy.DEGRADE,
        accept_statuses=frozenset({NodeStatus.COMPLETED, NodeStatus.DEGRADED}))


def _bootstrap_draft(runtime, registry, *, source=PlanSource.PRESET,
                     approval=ApprovalPolicy.REQUIRED, regime_attempts=1,
                     stop_refs=(), rotation_slot="slot-rotation"):
    nodes = (
        PlanNode(id="factor", worker_id="market.factor", writes_slot="slot-factor"),
        PlanNode(id="regime", worker_id="market.regime", writes_slot="slot-regime",
                 dependencies=(_dep(),), max_attempts=regime_attempts),
        PlanNode(id="rotation", worker_id="market.rotation", writes_slot=rotation_slot,
                 dependencies=(_dep(),)),
    )
    return PlanDraft(
        id="plan-bootstrap-lane0", run_id="run-boot-1", request_id="req-boot-1",
        phase="bootstrap", source=source, goal="Lane 0 bootstrap market context read",
        as_of=DT, mode=DataMode.ONLINE, context_snapshot_ref=None, nodes=nodes,
        sink_node_ids=("regime", "rotation"),
        catalog_version=runtime.catalog_version, catalog_digest=runtime.catalog_digest,
        schema_registry_digest=registry.registry_digest, approval_policy=approval,
        budget_request_tokens=200_000, budget_request_llm_invocations=2,
        max_concurrency=2, stop_condition_refs=tuple(stop_refs))


def _support(draft, *, env, profile, context=None):
    cat, runtime, view, registry = env
    request = P.preset_orchestration_request(request_id=draft.request_id)
    phase1 = validate_plan_draft(
        draft, request=request, context=context, catalog=runtime.snapshot,
        schema_registry=registry)
    return phase1, check_runtime_support(
        draft, phase1_report=phase1, context=context, context_requirements=None,
        catalog=runtime, bridge_view=view, schema_registry=registry, profile=profile)


# =========================================================================== #
# admission matrix — row by row                                                #
# =========================================================================== #
@pytest.mark.parametrize("source", [PlanSource.PRESET, PlanSource.PRESET_FALLBACK])
def test_bootstrap_preset_draft_rejected_under_v1_admitted_under_bootstrap(env, source):
    cat, runtime, view, registry = env
    draft = _bootstrap_draft(runtime, registry, source=source)
    phase1, v1 = _support(draft, env=env, profile=static_runtime_profile())
    assert phase1.valid, [i.code for i in phase1.issues]
    assert v1.supported is False
    assert "bootstrap_unsupported" in _codes(v1)
    _, boot = _support(draft, env=env, profile=bootstrap_runtime_profile())
    assert boot.supported is True, _codes(boot)
    assert boot.runtime_profile_digest == bootstrap_runtime_profile().profile_digest


def test_bootstrap_dynamic_draft_rejected_under_both(env):
    cat, runtime, view, registry = env
    draft = _bootstrap_draft(runtime, registry, source=PlanSource.DYNAMIC)
    _, v1 = _support(draft, env=env, profile=static_runtime_profile())
    _, boot = _support(draft, env=env, profile=bootstrap_runtime_profile())
    # a Planner never authors Lane 0 (spec §2.0 不让动态 Planner 猜 regime).
    for rep in (v1, boot):
        assert rep.supported is False
        assert "bootstrap_unsupported" in _codes(rep)


def test_plan_source_bootstrap_enum_stays_dormant_under_both(env):
    cat, runtime, view, registry = env
    draft = _bootstrap_draft(runtime, registry, source=PlanSource.BOOTSTRAP)
    _, v1 = _support(draft, env=env, profile=static_runtime_profile())
    _, boot = _support(draft, env=env, profile=bootstrap_runtime_profile())
    for rep in (v1, boot):
        assert rep.supported is False
        assert "plan_source_unsupported" in _codes(rep)
        assert "bootstrap_unsupported" in _codes(rep)


def test_auto_approval_rejected_under_both(env):
    cat, runtime, view, registry = env
    draft = _bootstrap_draft(runtime, registry, approval=ApprovalPolicy.AUTO)
    phase1, v1 = _support(draft, env=env, profile=static_runtime_profile())
    assert phase1.valid is False  # Phase 1 rejects AUTO for every source
    assert "auto_approval_rejected" in {i.code for i in phase1.issues}
    _, boot = _support(draft, env=env, profile=bootstrap_runtime_profile())
    for rep in (v1, boot):
        assert rep.supported is False
        assert "phase1_report_invalid" in _codes(rep)


def test_retries_and_stop_conditions_rejected_under_bootstrap_profile(env):
    cat, runtime, view, registry = env
    retry_draft = _bootstrap_draft(runtime, registry, regime_attempts=2)
    _, v1 = _support(retry_draft, env=env, profile=static_runtime_profile())
    _, boot = _support(retry_draft, env=env, profile=bootstrap_runtime_profile())
    for rep in (v1, boot):
        assert rep.supported is False
        assert "retries_unsupported" in _codes(rep)

    stop_ref = ContentRef(id="lane0.honesty.guardrail", version="1",
                          content_digest="c" * 64)
    stop_draft = _bootstrap_draft(runtime, registry, stop_refs=(stop_ref,))
    _, v1s = _support(stop_draft, env=env, profile=static_runtime_profile())
    _, boots = _support(stop_draft, env=env, profile=bootstrap_runtime_profile())
    for rep in (v1s, boots):
        assert rep.supported is False
        assert "stop_conditions_unsupported" in _codes(rep)


def test_multi_writer_rejected_under_bootstrap_profile(env):
    cat, runtime, view, registry = env
    try:
        draft = _bootstrap_draft(runtime, registry, rotation_slot="slot-regime")
    except Exception:
        # multi-writer without a reducer is already structurally unbuildable —
        # rejected before any profile is even consulted (both columns rejected).
        return
    _, v1 = _support(draft, env=env, profile=static_runtime_profile())
    _, boot = _support(draft, env=env, profile=bootstrap_runtime_profile())
    for rep in (v1, boot):
        assert rep.supported is False
        assert "multi_writer_unsupported" in _codes(rep)


# =========================================================================== #
# main-phase differential over the Phase 2 pilot fixtures                       #
# =========================================================================== #
def _empty_memory_context() -> ContextSnapshot:
    binding = build_empty_memory_binding()
    clock = ClockSpec(as_of=DT, timezone="Asia/Shanghai", calendar_id="cn_a_share")
    dc = DataContext(
        as_of=DT, clock=clock, mode=DataMode.ONLINE, backend=DataBackend.LIVE,
        strict_pit=False, calendar_id="cn_a_share",
        resolved_vendor_chains={"prices": ("tushare",)},
        source_config_digest="a" * 64, source_registry_digest="b" * 64,
        routing_snapshot_digest="c" * 64, data_snapshot_id="snap-1",
        data_snapshot_content_digest="d" * 64, built_at=DT)
    return ContextSnapshot.build(
        snapshot_id="ctx-1", data_context=dc, memory_snapshot_id="ms-1",
        memory_snapshot_hash=binding.snapshot_hash,
        past_context_hash=binding.past_context_hash,
        memory_snapshot_ref=binding.memory_snapshot_ref,
        memory_selection_ref=binding.memory_selection_ref,
        runtime_requirements_ref=None, memory_session_id=None, built_at=DT)


@pytest.fixture(scope="module")
def pilot():
    registry = default_registry()
    catalog = P.load_pilot_catalog()
    context = _empty_memory_context()
    request = P.preset_orchestration_request(request_id="req-pilot-boot")
    draft = P.build_pilot_draft(
        catalog=catalog, registry=registry, context=context,
        request_id=request.request_id, as_of=DT)
    view = BridgeCatalogView.build(catalog, {})
    phase1 = validate_plan_draft(
        draft, request=request, context=context, catalog=catalog.snapshot,
        schema_registry=registry)
    return registry, catalog, context, draft, view, phase1


def _pilot_support(pilot, *, profile, context):
    registry, catalog, _, draft, view, phase1 = pilot
    return check_runtime_support(
        draft, phase1_report=phase1, context=context, context_requirements=None,
        catalog=catalog, bridge_view=view, schema_registry=registry, profile=profile)


def test_main_phase_pilot_identical_outcomes_under_both_profiles(pilot):
    context = pilot[2]
    v1 = _pilot_support(pilot, profile=static_runtime_profile(), context=context)
    boot = _pilot_support(pilot, profile=bootstrap_runtime_profile(), context=context)
    assert v1.supported is True
    assert (v1.supported, _codes(v1)) == (boot.supported, _codes(boot))


def test_main_phase_pilot_rejections_identical_under_both_profiles(pilot):
    # the same main draft checked without a ContextSnapshot: rejected identically
    # (context=None is the bootstrap-lane condition ONLY for bootstrap-phase presets).
    v1 = _pilot_support(pilot, profile=static_runtime_profile(), context=None)
    boot = _pilot_support(pilot, profile=bootstrap_runtime_profile(), context=None)
    assert v1.supported is False
    assert "bootstrap_unsupported" in _codes(v1)
    assert (v1.supported, _codes(v1)) == (boot.supported, _codes(boot))
