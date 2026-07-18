# -*- coding: utf-8 -*-
"""Phase 5 · Task 10 — the fixed BootstrapPlan preset + its static draft builder.

Locks the pure half of the bootstrap runtime: the fixed, versioned
:class:`BootstrapPlan` record (deterministic digest; any field change is a new
preset), the three-node Lane 0 static draft (``phase="bootstrap"``,
``source=PRESET``, ``context_snapshot_ref=None``, REQUIRED approval, the DEGRADE
factor dependency accepting COMPLETED/DEGRADED), Phase-1 structural + full
validation against the Lane 0 catalog, and the ``context_snapshot_id=None``
bootstrap :class:`RunContext` with the canonical empty-memory hash. Any node
tamper (extra param, fourth node, changed dependency policy) moves the fixed
preset's candidate digest — auditability.

Run: ``python -m pytest tests/orchestration/test_bootstrap_plan.py -v``
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from guanlan_v2.orchestration import presets as P
from guanlan_v2.orchestration.bootstrap import (
    BOOTSTRAP_PRESET_ID,
    build_bootstrap_plan,
    build_bootstrap_plan_draft,
    build_bootstrap_run_context,
    load_lane0_catalog,
)
from guanlan_v2.orchestration.context import RunBudget, build_empty_memory_binding
from guanlan_v2.orchestration.enums import (
    ApprovalPolicy,
    DataMode,
    DependencyPolicy,
    NodeStatus,
    PlanSource,
)
from guanlan_v2.orchestration.market.factors import (
    MarketFactorReport,
    RegimeReport,
    RotationReport,
    build_market_factor_set_v1,
)
from guanlan_v2.orchestration.memory.experience import (
    ExperienceQuery,
    ExperienceSelection,
    RegimeGraderSpec,
)
from guanlan_v2.orchestration.schema_registry import SchemaRegistry
from guanlan_v2.orchestration.spec import (
    PlanNode,
    compute_candidate_plan_digest,
    validate_plan_draft,
    validate_plan_structure,
)

UTC = timezone.utc
DT = datetime(2026, 7, 18, 1, 30, tzinfo=UTC)


def _grader() -> RegimeGraderSpec:
    return RegimeGraderSpec.build(
        grader_version="regime-grader-v1", horizon_trading_days=20, benchmark_id="eqw_all_a",
        bull_min_return=0.03, bear_max_return=-0.03, risk_off_max_drawdown=-0.05,
        risk_on_min_return=0.02, risk_on_min_drawdown=-0.03)


def _preset(**overrides):
    return build_bootstrap_plan(
        spec=build_market_factor_set_v1(), grader=_grader(), **overrides)


@pytest.fixture(scope="module")
def env():
    cat = load_lane0_catalog()
    registry = SchemaRegistry()
    for model in (MarketFactorReport, RegimeReport, RotationReport,
                  ExperienceQuery, ExperienceSelection):
        registry.register(model)
    registry.seal()
    return cat, registry


def _request(rid: str = "req-boot-1"):
    return P.preset_orchestration_request(request_id=rid)


def _draft(env):
    cat, registry = env
    return build_bootstrap_plan_draft(
        _preset(), request=_request(), as_of=DT, mode=DataMode.ONLINE,
        catalog=cat.snapshot, schema_registry_digest=registry.registry_digest,
        draft_id="plan-bootstrap-lane0", run_id="run-boot-1")


# =========================================================================== #
# 1 — the fixed, versioned preset record                                       #
# =========================================================================== #
def test_preset_construction_and_digest_stability():
    a = _preset()
    b = _preset()
    assert a.preset_id == BOOTSTRAP_PRESET_ID == "bootstrap.lane0"
    assert a.preset_version == "1"
    assert a.experience_k == 5
    assert a.node_timeout_sec == 300
    # v1 budget: factor deterministic (0), regime + rotation one each.
    assert a.budget_request_llm_invocations == 2
    assert a.factor_set_version == build_market_factor_set_v1().factor_set_version
    assert a.factor_set_digest == build_market_factor_set_v1().content_digest
    assert a.grader_digest == _grader().content_digest
    # deterministic + self-sealed.
    assert a.content_digest == b.content_digest
    assert a.content_digest == a.semantic_digest()


def test_any_preset_field_change_is_a_new_digest():
    base = _preset().content_digest
    assert _preset(experience_k=6).content_digest != base
    assert _preset(node_timeout_sec=301).content_digest != base
    assert _preset(budget_request_tokens=1).content_digest != base
    assert _preset(preset_version="2").content_digest != base


# =========================================================================== #
# 2 — the static three-node draft passes Phase-1 validation                    #
# =========================================================================== #
def test_draft_shape_is_the_fixed_lane0_graph(env):
    draft = _draft(env)
    assert draft.phase == "bootstrap"
    assert draft.source is PlanSource.PRESET
    assert draft.context_snapshot_ref is None
    assert draft.approval_policy is ApprovalPolicy.REQUIRED
    assert tuple(n.id for n in draft.nodes) == ("lane0.factor", "lane0.regime", "lane0.rotation")
    assert draft.sink_node_ids == ("lane0.regime", "lane0.rotation")
    # every node: no params, single attempt, preset timeout.
    for node in draft.nodes:
        assert node.params == {}
        assert node.max_attempts == 1
        assert node.timeout_sec == _preset().node_timeout_sec
    # legacy tuple all-None (clause C3).
    assert draft.legacy_source_schema is None
    assert draft.legacy_source_config_digest is None
    assert draft.legacy_mapping_digest is None
    # the DEGRADE factor dependency accepts COMPLETED/DEGRADED.
    regime = draft.nodes[1]
    dep = regime.dependencies[0]
    assert dep.upstream_node_id == "lane0.factor"
    assert dep.inject_as == "market_factor_report"
    assert dep.policy is DependencyPolicy.DEGRADE
    assert dep.accept_statuses == frozenset({NodeStatus.COMPLETED, NodeStatus.DEGRADED})


def test_draft_passes_structure_and_phase1_validation(env):
    cat, registry = env
    draft = _draft(env)
    validate_plan_structure(draft)  # no raise
    report = validate_plan_draft(
        draft, request=_request(), context=None, catalog=cat.snapshot,
        schema_registry=registry)
    assert report.valid, [i.code for i in report.issues]


def test_wrong_catalog_digest_is_a_validation_issue(env):
    cat, registry = env
    draft = _draft(env).model_copy(update={"catalog_digest": "0" * 64})
    report = validate_plan_draft(
        draft, request=_request(), context=None, catalog=cat.snapshot,
        schema_registry=registry)
    assert not report.valid


def test_auto_approval_draft_is_rejected(env):
    cat, registry = env
    auto = _draft(env).model_copy(update={"approval_policy": ApprovalPolicy.AUTO})
    report = validate_plan_draft(
        auto, request=_request(), context=None, catalog=cat.snapshot,
        schema_registry=registry)
    assert not report.valid
    assert "auto_approval_rejected" in {i.code for i in report.issues}


# =========================================================================== #
# 3 — the fixed preset is auditable: any node tamper moves the digest          #
# =========================================================================== #
def test_node_tamper_moves_the_candidate_digest(env):
    request = _request()
    base = _draft(env)
    base_digest = compute_candidate_plan_digest(
        request=request, draft=base, context_content_digest=None)

    # (a) an extra param on the factor node.
    nodes = list(base.nodes)
    nodes[0] = nodes[0].model_copy(update={"params": {"x": 1}})
    param_drift = base.model_copy(update={"nodes": tuple(nodes)})
    assert compute_candidate_plan_digest(
        request=request, draft=param_drift, context_content_digest=None) != base_digest

    # (b) a changed dependency policy on the regime node.
    nodes2 = list(base.nodes)
    dep = nodes2[1].dependencies[0].model_copy(update={
        "policy": DependencyPolicy.BLOCK,
        "accept_statuses": frozenset({NodeStatus.COMPLETED})})
    nodes2[1] = nodes2[1].model_copy(update={"dependencies": (dep,)})
    dep_drift = base.model_copy(update={"nodes": tuple(nodes2)})
    assert compute_candidate_plan_digest(
        request=request, draft=dep_drift, context_content_digest=None) != base_digest

    # (c) a fourth node.
    fourth = PlanNode(id="lane0.extra", worker_id="market.factor", writes_slot="extra_slot")
    four_drift = base.model_copy(update={"nodes": base.nodes + (fourth,)})
    assert compute_candidate_plan_digest(
        request=request, draft=four_drift, context_content_digest=None) != base_digest


# =========================================================================== #
# 4 — the bootstrap RunContext has no snapshot + the canonical empty-mem hash   #
# =========================================================================== #
def test_bootstrap_run_context_has_no_snapshot_and_empty_memory_hash():
    budget = RunBudget(
        ledger_id="led-boot", max_tokens=1_000_000, max_llm_invocations=4, max_concurrency=2)
    ctx = build_bootstrap_run_context(
        run_id="run-boot-1", data=P.pilot_data_context(as_of=DT), budget=budget,
        cancellation_token_id="cancel-boot")
    assert ctx.context_snapshot_id is None
    assert ctx.memory_snapshot_hash == build_empty_memory_binding().snapshot_hash
    assert ctx.run_id == "run-boot-1"
    assert ctx.data.as_of == DT
