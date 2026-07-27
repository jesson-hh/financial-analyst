# -*- coding: utf-8 -*-
"""Phase 10 · Task 0 — the executable Phases 1-9 → 10 upstream handoff gate.

Phase 10 composes the sealed Phase 1-9 orchestration kernel into two product
pipelines. This module is the consumer gate every later Phase-10 task depends
on: it freezes the reviewed upstream evidence (exact chain digests +
module:function names — never local machine paths, never mutable singleton
identities) and proves, executably, that every seam the plan builds on exists
under its implemented name and shape.

Brief items covered (one test class per item):

1.  the linear registry chain resolves by exact digest through
    ``PHASE9_REGISTRY_DIGEST`` and the catalog chain through
    ``PHASE9_CATALOG_DIGEST``; no "latest" alias anywhere in
    ``guanlan_v2/orchestration/`` (structural: the ``SchemaVersion`` grammar
    refuses it; lexical: no source line assigns ``version="latest"``).
    Suite green-ness itself is proven by running ``pytest tests/orchestration``
    (Step 3) — a test cannot re-run its own suite from within.
2.  Phase 7 exports: the planner admission path (``PlanAdmissionService``),
    ``PlanPresetRegistry`` / ``PlanPresetRecord`` / ``materialize_fallback_draft``,
    the approval coordinator, and the Task 7b lease surface. RECONCILIATION
    (D1): ``issue_lease`` / ``list_leases`` / ``revoke_lease`` /
    ``register_and_try_lease`` are implemented as METHODS of
    ``PlanApprovalCoordinator``, not module-level functions — the gate binds to
    the implemented (method) names.
3.  Phase 9 exports resolve. RECONCILIATION: the real production plan-execution
    path REFUSES multi-point interval execution
    (``adapters.launcher.MultiPointPlanExecutionRefused``); ``run_interval_replay``
    drives per-point plans through the injectable
    ``ReplayRuntimeBindings.admission`` seam (``ReplayPlanCoordinator``) — the
    gate asserts both truths. The ``/orchestration`` router builder's implemented
    name is ``build_adapters_router``.
4.  the Phase 8 catalog resolves the twelve lane workers this plan schedules,
    each ``selection_scope="dynamic_allowed"`` with its output schema resolvable
    in the sealed cumulative registry; ``dec.trader`` emits only
    ``PortfolioTargetProposal@1``. RECONCILIATION (D3, corrected in round 2 —
    the earlier "code from the run/request context (DataContext)" attribution
    was WRONG: ``DataContext`` carries no code/symbol/universe field): NONE of
    the twelve carries a ``params_schema_ref`` (sole catalog carrier:
    ``dec.risk_debate``/``RiskDebateParams@1``), and there is NO single
    structural subject-code carrier in the kernel either — a paramsless worker
    REFUSES node params (spec.py ``params_not_allowed``), and the code reaches
    execution only as (i) per-call data-method params
    (``InstrumentSeriesParams.symbol`` / ``InstrumentUniverseParams.symbols``
    riding on ``DataRequest.params``) and (ii) payload content (the request
    ``goal`` text / upstream artifacts). The Phase-2 compat precedent maps base
    ``code``/``asof_date`` to ``target_kind="context"`` and deliberately places
    NOTHING on the node (``presets._CORE_BASE_INPUTS``). Task 3 must bind the
    subject code at a reviewed seam — never through an assumed DataContext
    field. Pinned executably by ``test_d3_subject_code_carrier_reality``.
5.  Phase 5/6 anchors: ``RegimeReport@1`` / ``RotationReport@1`` registered,
    the bootstrap ``ContextSnapshot`` production path
    (``build_context_snapshot_from_bootstrap``), ``TARGET_WEIGHT_BANDS``
    (Phase-6-owned, re-exported by object identity), and the Phase 6
    ``compute_*`` schedule functions.
6.  production seams by name: ``seats/watcher.py::tick`` with injectable
    ``decide_fn`` (``run_loop`` the lifespan entry); ``console/tools.py``
    exports (import-and-assert ONLY — the file is owned by a concurrent
    session); ``scripts/gen_agent_interface_doc.py`` + its drift-guard test;
    the seats decision-persistence helper (``seats.api._persist_decision``)
    and the Phase 9 Task 10 run-head only-if-present key convention (D7),
    proven behaviourally against tmp stores.
7.  the ranking read surface for ``cand.v4`` / ``cand.model`` (D5) — recorded
    by exact module:function name in :data:`FROZEN_EVIDENCE`.
8.  ``POST /archive/put`` has an in-process callable seam
    (``archive.api.build_archive_router``) and the console ``_archive_research``
    idiom exists.
9.  no Phase 10 source/test path overwrites any Phase 1-9 source, test or
    golden file: the frozen Phase 1-9 golden roster + orchestration module
    roster must all still exist, the two Phase-9 chain-sealing goldens must be
    byte-identical to their frozen sha256, and every planned Phase-10 create
    path is name-disjoint from the frozen roster.

Post-merge realities recorded (not gate failures — evidence for Task 0b):
``config/orchestration/presets/`` holds exactly one preset
(``main_research_baseline.json``); no production plan graph sinks
``PortfolioTargetProposal@1`` (stated verbatim in ``adapters/launcher.py``'s
module docstring); ``POST /orchestration/replay/wakeup`` keeps its honest 503
in production because ``ReplayRuntimeBindings`` is run-scoped
(``startup.bind_orchestration_launcher`` deliberately binds neither
``replay_bindings`` nor the shadow-wakeup context provider); the console
plan-approval wiring is honestly inert until an admission provider is bound
(``bind_process_plan_approval_coordinator`` returns ``None`` and
``plan_approval_console_kwargs(coordinator=None) == {}``); ``DualCurveReport@1``
IS registered in the sealed chain — the live ``curve_report:unresolvable``
badge is a payload-store resolution honesty badge, never an unregistered
schema.
"""
from __future__ import annotations

import hashlib
import importlib
import inspect
import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

# --------------------------------------------------------------------------- #
# locations (repo-relative only; derived from this file, never a machine path)  #
# --------------------------------------------------------------------------- #
_REPO = Path(__file__).resolve().parents[2]
_GOLDEN_DIR = Path(__file__).resolve().parent / "golden"
_ORCH_SRC = _REPO / "guanlan_v2" / "orchestration"

# --------------------------------------------------------------------------- #
# Step 2 — the frozen reviewed upstream evidence                                #
# (exact digests + module:function names ONLY; never local machine paths,      #
#  never mutable singleton identities)                                          #
# --------------------------------------------------------------------------- #
_UNSET = "<unset>"

FROZEN_EVIDENCE = {
    # -- item 1: the sealed linear chain tips (hand-frozen from the reviewed
    #    Phase 8/9 goldens; the phase9 golden pins the phase8 base digests,
    #    which pin phase7, … — the whole lineage is transitively sealed).
    "phase8_registry_digest":
        "d719e19bc8c64f56324ee36ca0d3aa039e5eac1c9488d80babe6ddce81e5e089",
    "phase8_catalog_digest":
        "7f00dde4f78761cc4a5c991b7717b9002ea82d7d86d31ab007d4af80f0112c91",
    "phase9_registry_digest":
        "9e73ddf6d23def5a5666016b1ab113e7b9920eb940ff22a0dd73d806efd3ac07",
    "phase9_catalog_digest":
        "0c48db784ddcde2f094c7089fcf6a87c251b3ea8e321b1f231dd4177a092a21a",
    # -- item 9: byte seals of the two chain-sealing goldens (sha256 of bytes).
    "phase9_schema_golden_sha256":
        "57c6ab0c1d7c09e2924f4ea602f2a984c100ecf55ac0211264072372e7077af2",
    "phase9_catalog_golden_sha256":
        "ae8d3a25a946b93cad80d36a6cc4c9397293787ac219331e39ce911c59a20124",
    # -- item 7 / D5: the reviewed screen ranking read surface for
    #    cand.v4 (model_id None/"prod") and cand.model (variant model_id).
    "ranking_reader": "guanlan_v2.strategy.ranking:load_v4_ranking",
    "ranking_asof": "guanlan_v2.strategy.ranking:ranking_date",
    "variant_ranking_path": "guanlan_v2.screen.model_registry:variant_ranking_path",
    # -- item 6 / D7: the seats decision-persistence surface.
    "seats_decision_append": "guanlan_v2.seats.api:_persist_decision",
    "seats_compat_writer": (
        "guanlan_v2.orchestration.adapters.api:persist_replay_run_compat"),
    # -- item 8: the report-landing seam + the console idiom.
    "archive_router_builder": "guanlan_v2.archive.api:build_archive_router",
    "console_archive_idiom": "guanlan_v2.console.api:_archive_research",
    # -- item 3: the /orchestration router builder's implemented name.
    "orchestration_router_builder": (
        "guanlan_v2.orchestration.adapters.api:build_adapters_router"),
}

#: the twelve lane workers the Phase 10 plan schedules (brief item 4).
PHASE10_LANE_WORKERS = (
    "text.news", "text.research_report",
    "quant.factor", "quant.backtest",
    "pv.price_action", "pv.technical", "pv.microstructure",
    "dec.bull", "dec.bear", "dec.research_mgr", "dec.pm", "dec.trader",
)

#: the frozen Phase 1-9 golden roster (item 9 — none of these may vanish, and
#: every planned Phase-10 create path must be name-disjoint from it).
PHASE1_9_GOLDEN_FILES = (
    "data_catalog_manifest_v1.json", "data_schema_manifest_v1.json",
    "data_source_manifest_v1.json", "digest_vectors_v1.json",
    "limit_rule_policy_v1.json", "market_factor_set_v1.json",
    "memory_capture_policy_v1.json",
    "phase3_full_catalog_manifest_v1.json", "phase3_full_schema_manifest_v1.json",
    "phase4_catalog_manifest_v1.json", "phase4_schema_manifest_v1.json",
    "phase5_catalog_manifest_v1.json", "phase5_schema_manifest_v1.json",
    "phase6_catalog_manifest_v1.json", "phase6_schema_manifest_v1.json",
    "phase7_catalog_manifest_v1.json", "phase7_schema_manifest_v1.json",
    "phase8_catalog_manifest_v1.json", "phase8_schema_manifest_v1.json",
    "phase9_catalog_manifest_v1.json", "phase9_retirement_gates_v1.json",
    "phase9_schema_manifest_v1.json",
    "plan_preset_manifest_v1.json", "regime_grader_policy_v1.json",
    "runtime_schema_manifest_v1.json", "schema_manifest_v1.json",
    "shadow_execution_golden_v1.json",
)

#: the frozen Phase 1-9 top-level orchestration source roster (item 9): every
#: name must keep existing; the Phase 10 ``pipeline`` package is NOT in it.
PHASE1_9_ORCH_MODULES = (
    "__init__.py", "adapters", "admission.py", "approval.py", "bootstrap.py",
    "budget.py", "capability_manifest.py", "catalog.py", "catalog_runtime.py",
    "context.py", "dag.py", "data", "debate.py", "decision_inputs.py",
    "digest.py", "enums.py", "evaluator.py", "events.py", "eventstore.py",
    "governor.py", "honesty.py", "lane_catalog.py", "lane_payloads.py",
    "market", "memory", "migration.py", "model_tiers.py", "optimize.py",
    "orchestrator.py", "pattern_registry.py", "phase7_registry.py",
    "plan_diff.py", "plan_presets.py", "planner_gateway.py", "pool.py",
    "presets.py", "refs.py", "runtime_clock.py", "runtime_contracts.py",
    "runtime_support.py", "schema_registry.py", "schemas.py", "sealed.py",
    "shadow.py", "skills", "skilltree.py", "spec.py", "startup.py",
    "trial.py", "trial_ledger.py", "worker.py",
)

#: the frozen tests/orchestration/*.py module roster at freeze time (2026-07-27:
#: the 122 Phase 1-9 test modules + this gate). Item 9's test half: Phase 10 may
#: ADD test files, but none of these may ever vanish or be renamed away.
TEST_MODULE_ROSTER_AT_FREEZE = (
    "__init__.py", "test_adapters_api.py", "test_adapters_contracts.py",
    "test_adapters_live_data.py", "test_adapters_replay_data.py",
    "test_admission.py", "test_approval_lease.py", "test_approval_store.py",
    "test_artifact.py", "test_bootstrap_e2e.py", "test_bootstrap_plan.py",
    "test_bootstrap_profile.py", "test_budget.py", "test_budget_ledger.py",
    "test_capability_manifest.py", "test_catalog.py", "test_catalog_runtime.py",
    "test_context.py", "test_contract_completeness.py", "test_dag.py",
    "test_data_result.py", "test_debate_runtime.py", "test_decision_inputs.py",
    "test_decision_schedule.py", "test_digest.py", "test_dual_curves.py",
    "test_durable_stores.py", "test_dynamic_e2e.py", "test_engine_equivalence.py",
    "test_enums.py", "test_evaluator.py", "test_event_refusal.py",
    "test_events.py", "test_eventstore.py", "test_experience_contracts.py",
    "test_experience_retrieval.py", "test_experience_seed.py",
    "test_experience_store.py", "test_factor_report_render.py",
    "test_governor.py", "test_honesty.py", "test_lane0_catalog.py",
    "test_lane0_reports.py", "test_lane_batch_decision.py",
    "test_lane_batch_pv.py", "test_lane_batch_quant.py",
    "test_lane_batch_text.py", "test_lane_batch_xcut.py", "test_launcher.py",
    "test_launcher_wiring.py", "test_legacy_inventory.py",
    "test_luozi_replay.py", "test_luozi_wakeup.py",
    "test_market_factor_compute.py", "test_market_factor_contracts.py",
    "test_migration.py", "test_model_tiers.py", "test_node_run.py",
    "test_operator_identity.py", "test_optimize.py",
    "test_orchestrator_assembly.py", "test_orchestrator_contracts.py",
    "test_pattern_registry.py", "test_payloads.py", "test_phase10_handoff.py",
    "test_phase2_handoff.py", "test_phase4_handoff.py", "test_phase4_registry.py",
    "test_phase5_handoff.py", "test_phase5_registry.py", "test_phase6_handoff.py",
    "test_phase6_registry.py", "test_phase7_handoff.py", "test_phase7_registry.py",
    "test_phase8_e2e.py", "test_phase8_handoff.py",
    "test_phase8_registry_catalog.py", "test_phase9_e2e.py",
    "test_phase9_handoff.py", "test_phase9_registry_chain.py",
    "test_pilot_runtime.py", "test_plan_approval_console.py",
    "test_plan_catalog_validation.py", "test_plan_diff.py",
    "test_plan_presets.py", "test_plan_structure.py", "test_planner_gateway.py",
    "test_planner_loop.py", "test_pool.py", "test_presets.py",
    "test_redline_regression.py", "test_refs.py", "test_regime_grader.py",
    "test_registry_population.py", "test_replay_approval_cards.py",
    "test_retirement_gates.py", "test_runtime_clock.py",
    "test_runtime_contracts.py", "test_runtime_profile_v2.py",
    "test_runtime_support.py", "test_schema_registry.py",
    "test_sealed_holdout.py", "test_shadow_agent.py", "test_shadow_contracts.py",
    "test_shadow_diff.py", "test_shadow_envelope.py", "test_shadow_events.py",
    "test_shadow_gaps.py", "test_shadow_golden_harness.py",
    "test_shadow_mirror.py", "test_shadow_records.py",
    "test_shadow_redlines.py", "test_shadow_runner.py", "test_skilltree.py",
    "test_spec.py", "test_startup_binding.py", "test_symbols.py",
    "test_trial_contracts.py", "test_trial_events.py", "test_trial_ledger.py",
    "test_watcher_orchestrated_registration.py", "test_weiwo_adapter.py",
    "test_worker.py",
)

#: field/attribute names that would mean "leader stock codes" surfaced upstream —
#: the D4 tripwire vocabulary (if any appears, D4's refusal design must be
#: re-reviewed before cand.lane0 goes stale).
_D4_CODE_LIKE_NAMES = (
    "code", "codes", "symbol", "symbols", "leader_codes", "leaders",
    "leader_stocks", "stocks", "tickers",
)

#: every file the Phase 10 plan CREATES (from the plan's file-structure table;
#: this gate file excluded — it is the one Phase-10 file that exists already).
PHASE10_PLANNED_CREATE_PATHS = (
    "guanlan_v2/orchestration/pipeline/__init__.py",
    "guanlan_v2/orchestration/pipeline/contracts.py",
    "guanlan_v2/orchestration/pipeline/candidates.py",
    "guanlan_v2/orchestration/pipeline/screening.py",
    "guanlan_v2/orchestration/pipeline/escalation.py",
    "guanlan_v2/orchestration/pipeline/deep_decide.py",
    "guanlan_v2/orchestration/pipeline/live_decide.py",
    "guanlan_v2/orchestration/pipeline/api.py",
    "guanlan_v2/orchestration/pipeline/chain.py",
    "ui/console/console-recommendation-card.jsx",
    "config/orchestration/presets/luozi_deep_decide_v1.json",
    "tests/orchestration/golden/phase10_schema_manifest_v1.json",
    "tests/orchestration/golden/phase10_catalog_manifest_v1.json",
    "tests/orchestration/test_pipeline_contracts.py",
    "tests/orchestration/test_pipeline_candidates.py",
    "tests/orchestration/test_pipeline_screening.py",
    "tests/orchestration/test_pipeline_escalation.py",
    "tests/orchestration/test_pipeline_deep_preset.py",
    "tests/orchestration/test_pipeline_live_decide.py",
    "tests/orchestration/test_pipeline_replay_evidence.py",
    "tests/orchestration/test_pipeline_api.py",
    "tests/orchestration/test_phase10_chain.py",
    "tests/orchestration/test_phase10_e2e.py",
)


def _resolve(spec: str):
    """Resolve a frozen ``module:attr`` evidence spec to the live object."""
    mod_name, _, attr = spec.partition(":")
    return getattr(importlib.import_module(mod_name), attr)


def _frozen(key: str) -> str:
    value = FROZEN_EVIDENCE[key]
    assert value != _UNSET, (
        f"frozen evidence {key!r} is not recorded yet — Step 2 of the handoff "
        "gate must freeze the reviewed value")
    return value


# --------------------------------------------------------------------------- #
# module-scoped chain fixtures (the catalog/registry builds are expensive)      #
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="module")
def chain():
    from guanlan_v2.orchestration.adapters import chain as chain_mod
    return chain_mod


@pytest.fixture(scope="module")
def sealed_registry(chain):
    return chain.build_phase9_registry(chain.PHASE9_BASE_REGISTRY_DIGEST)


@pytest.fixture(scope="module")
def catalog(chain):
    return chain.phase9_catalog_snapshot()


# =========================================================================== #
# item 1 — the linear chain resolves by exact digest; no "latest" alias        #
# =========================================================================== #
class TestItem1ChainDigests:
    def test_registry_chain_resolves_by_exact_frozen_digest(self, chain, sealed_registry):
        assert chain.PHASE9_BASE_REGISTRY_DIGEST == _frozen("phase8_registry_digest")
        assert chain.PHASE9_REGISTRY_DIGEST == _frozen("phase9_registry_digest")
        assert sealed_registry.registry_digest == _frozen("phase9_registry_digest")

    def test_catalog_chain_resolves_by_exact_frozen_digest(self, chain, catalog):
        assert chain.PHASE9_BASE_CATALOG_DIGEST == _frozen("phase8_catalog_digest")
        assert chain.PHASE9_CATALOG_DIGEST == _frozen("phase9_catalog_digest")
        assert catalog.catalog_digest == _frozen("phase9_catalog_digest")

    def test_goldens_pin_the_same_lineage(self):
        schema_golden = json.loads(
            (_GOLDEN_DIR / "phase9_schema_manifest_v1.json").read_text(encoding="utf-8"))
        catalog_golden = json.loads(
            (_GOLDEN_DIR / "phase9_catalog_manifest_v1.json").read_text(encoding="utf-8"))
        assert schema_golden["base_registry_digest"] == _frozen("phase8_registry_digest")
        assert schema_golden["registry_digest"] == _frozen("phase9_registry_digest")
        assert catalog_golden["base_catalog_digest"] == _frozen("phase8_catalog_digest")
        assert catalog_golden["result_catalog_digest"] == _frozen("phase9_catalog_digest")

    def test_wrong_base_digest_is_refused_before_registration(self, chain):
        with pytest.raises(chain.Phase9RegistryError):
            chain.build_phase9_registry("0" * 64)

    def test_no_latest_alias_structurally(self):
        """The SchemaVersion grammar (``^[0-9]+$``) refuses "latest" outright."""
        from pydantic import ValidationError

        from guanlan_v2.orchestration.refs import SchemaRef
        with pytest.raises(ValidationError):
            SchemaRef(name="RegimeReport", version="latest")

    def test_no_latest_alias_lexically(self):
        """No orchestration source line binds a version to the string "latest"."""
        pattern = re.compile(r"""version\s*=\s*["']latest["']""")
        offenders = [
            str(path.relative_to(_REPO))
            for path in sorted(_ORCH_SRC.rglob("*.py"))
            if pattern.search(path.read_text(encoding="utf-8"))
        ]
        assert offenders == []


# =========================================================================== #
# item 2 — Phase 7 exports + the Task 7b lease surface (D1)                    #
# =========================================================================== #
class TestItem2Phase7Exports:
    def test_planner_admission_path_and_presets_resolve(self):
        from guanlan_v2.orchestration.admission import PlanAdmissionService
        from guanlan_v2.orchestration.plan_presets import (
            PlanPresetRecord,
            PlanPresetRegistry,
            materialize_fallback_draft,
        )
        assert inspect.isclass(PlanAdmissionService)
        assert inspect.isclass(PlanPresetRegistry)
        assert inspect.isclass(PlanPresetRecord)
        assert callable(materialize_fallback_draft)

    def test_lease_surface_binds_to_the_implemented_names(self):
        """D1: the lease verbs are METHODS of PlanApprovalCoordinator (not
        module functions) — Tasks 6/7/9 bind to these exact names."""
        from guanlan_v2.orchestration.approval import (
            ApprovalLease,
            LeaseAdmissionOutcome,
            PlanApprovalCoordinator,
        )
        assert inspect.isclass(ApprovalLease)
        assert inspect.isclass(LeaseAdmissionOutcome)
        for verb in ("issue_lease", "list_leases", "revoke_lease",
                     "register_and_try_lease"):
            assert callable(getattr(PlanApprovalCoordinator, verb)), verb

        issue_params = set(
            inspect.signature(PlanApprovalCoordinator.issue_lease).parameters)
        assert {"purpose", "preset_id", "preset_record_digest", "catalog_digest",
                "registry_digest", "valid_from", "valid_until"} <= issue_params
        try_params = set(
            inspect.signature(PlanApprovalCoordinator.register_and_try_lease).parameters)
        assert {"pending", "idempotency_key", "now", "candidate_catalog_digest",
                "candidate_registry_digest"} <= try_params

    def test_lease_binds_exact_preset_and_chain(self):
        from guanlan_v2.orchestration.approval import ApprovalLease
        fields = set(ApprovalLease.model_fields)
        assert {"lease_id", "preset_id", "preset_record_digest", "catalog_digest",
                "registry_digest", "valid_from", "valid_until", "max_admissions",
                "budget_cap_llm_invocations", "issued_by"} <= fields

    def test_pending_plan_approval_carries_preset_provenance(self):
        from guanlan_v2.orchestration.plan_diff import PendingPlanApproval
        fields = set(PendingPlanApproval.model_fields)
        assert {"preset_id", "preset_record_digest"} <= fields


# =========================================================================== #
# item 3 — Phase 9 exports (with the plan-execution seam reality)               #
# =========================================================================== #
class TestItem3Phase9Exports:
    def test_durable_stores_builder_signature(self):
        """D2: ``build_durable_runtime_stores(root)`` with defaultable seams."""
        from guanlan_v2.orchestration.adapters.durable import (
            JsonlEventStore,
            build_durable_runtime_stores,
        )
        assert inspect.isclass(JsonlEventStore)
        params = inspect.signature(build_durable_runtime_stores).parameters
        names = list(params)
        assert names[0] == "root"
        for optional in ("resolver", "clock", "allowed_cell_namespaces"):
            assert optional in params
            assert params[optional].default is not inspect.Parameter.empty

    def test_orchestration_router_builder_resolves(self):
        from guanlan_v2.orchestration.adapters.api import (
            ORCHESTRATION_ROUTE_PATHS,
            ORCHESTRATION_ROUTER_PREFIX,
        )
        builder = _resolve(FROZEN_EVIDENCE["orchestration_router_builder"])
        assert callable(builder)
        assert ORCHESTRATION_ROUTER_PREFIX == "/orchestration"
        assert "/orchestration/replay/wakeup" in ORCHESTRATION_ROUTE_PATHS

    def test_replay_and_compat_exports_resolve(self):
        from guanlan_v2.orchestration.adapters.luozi import run_interval_replay
        compat = _resolve(FROZEN_EVIDENCE["seats_compat_writer"])
        assert callable(compat)
        assert {"state", "decisions", "run_head"} <= set(
            inspect.signature(compat).parameters)
        replay_params = set(inspect.signature(run_interval_replay).parameters)
        assert {"request", "schedule", "execution_config", "interval_start",
                "interval_end", "bindings"} <= replay_params

    def test_multi_point_execution_is_refused_at_the_production_seam(self):
        """RECONCILIATION 1: the real plan-execution path refuses multi-point
        interval execution; Phase 9's replay ran the per-point plans through the
        injectable ``ReplayRuntimeBindings.admission`` seam."""
        import dataclasses

        from guanlan_v2.orchestration.adapters.launcher import (
            LauncherError,
            MultiPointPlanExecutionRefused,
        )
        from guanlan_v2.orchestration.adapters.luozi import ReplayRuntimeBindings
        assert issubclass(MultiPointPlanExecutionRefused, LauncherError)
        binding_fields = {f.name for f in dataclasses.fields(ReplayRuntimeBindings)}
        assert "admission" in binding_fields  # the injectable per-point plan seam

    def test_seats_and_autonomy_seams_resolve(self):
        from guanlan_v2.autonomy.runtime import maybe_enqueue_lane0_bootstrap
        from guanlan_v2.seats.watcher import note_external_llm_use, orchestrated_codes
        assert callable(note_external_llm_use)
        assert callable(orchestrated_codes)
        assert list(inspect.signature(maybe_enqueue_lane0_bootstrap).parameters
                    )[0] == "note"


# =========================================================================== #
# item 4 — the Phase 8 lane-worker roster (D3 reconciled)                       #
# =========================================================================== #
class TestItem4LaneWorkers:
    def test_final_worker_roster_matches_the_frozen_golden(self, catalog):
        golden = json.loads(
            (_GOLDEN_DIR / "phase8_catalog_manifest_v1.json").read_text(encoding="utf-8"))
        finals = sorted(w.id for w in catalog.workers if w.catalog_role == "final")
        assert finals == sorted(golden["final_worker_ids"])
        assert len(finals) == golden["final_worker_count"]

    def test_the_twelve_scheduled_workers_resolve(self, catalog, sealed_registry):
        workers = {w.id: w for w in catalog.workers}
        for wid in PHASE10_LANE_WORKERS:
            assert wid in workers, f"lane worker {wid!r} missing from the catalog"
            spec = workers[wid]
            assert spec.catalog_role == "final", wid
            assert spec.selection_scope == "dynamic_allowed", wid
            for out in spec.outputs:  # every output schema resolves in the chain
                sealed_registry.resolve(out.schema_ref)

    def test_params_schema_reality_matches_the_implemented_convention(
            self, catalog, sealed_registry):
        """D3 RECONCILIATION: none of the twelve carries a params_schema_ref,
        so the subject code can NOT ride on node params (spec.py refuses params
        on a paramsless worker). Where it actually rides is pinned separately by
        test_d3_subject_code_carrier_reality. The sole params carrier in the
        whole catalog is dec.risk_debate."""
        workers = {w.id: w for w in catalog.workers}
        for wid in PHASE10_LANE_WORKERS:
            assert workers[wid].params_schema_ref is None, (
                f"{wid} grew a params_schema_ref — re-review D3 before Task 3")
        carriers = sorted(
            w.id for w in catalog.workers if w.params_schema_ref is not None)
        assert carriers == ["dec.risk_debate"]
        # where a params schema exists it must be resolvable (importable) too.
        sealed_registry.resolve(workers["dec.risk_debate"].params_schema_ref)

    def test_dec_trader_emits_only_portfolio_target_proposal(self, catalog):
        trader = next(w for w in catalog.workers if w.id == "dec.trader")
        refs = [(o.schema_ref.name, o.schema_ref.version) for o in trader.outputs]
        assert refs == [("PortfolioTargetProposal", "1")]

    def test_d3_run_context_convention_is_pinned(self):
        """D3 run-context SEAM pins (round-2 corrected scope: these are the
        one-frozen-data-universe-per-run names Task 3's plans must be
        *consistent with* — NOT a subject-code carrier; the code carrier truth
        is test_d3_subject_code_carrier_reality). A rename must fail HERE:
        ``bootstrap.derive_main_run_context`` (name + keyword surface) and the
        ContextSnapshot → DataContext linkage (field ``data_context``; the
        RunContext side is field ``data``, same ``DataContext`` type — the pair
        ``derive_main_run_context`` itself cross-checks for equality)."""
        from guanlan_v2.orchestration.bootstrap import derive_main_run_context
        from guanlan_v2.orchestration.context import (
            ContextSnapshot,
            DataContext,
            RunContext,
        )
        sig = inspect.signature(derive_main_run_context)
        params = list(sig.parameters.values())
        assert params[0].name == "bootstrap_ctx"
        kw_only = {p.name for p in params
                   if p.kind is inspect.Parameter.KEYWORD_ONLY}
        # exact-set EQUALITY is deliberate (siblings use subset): a NEW kwarg
        # here changes what a context-consistent plan must supply, so growth
        # must trip this gate too, not just renames/removals.
        assert kw_only == {"snapshot", "main_run_id", "budget",
                          "cancellation_token_id"}
        assert ContextSnapshot.model_fields["data_context"].annotation is DataContext
        assert RunContext.model_fields["data"].annotation is DataContext

    def test_d3_subject_code_carrier_reality(self):
        """D3 carrier truth (round-2 correction of a wrong attribution): there
        is NO single structural subject-code carrier in the implemented kernel.
        Pinned so a later change re-opens D3 loudly:

        (a) ``DataContext``'s EXACT field set — no code/symbol/universe field
            (the earlier gate text claimed the code rode here; it does not);
        (b) the only TYPED code carriers are the per-call data-method params —
            ``InstrumentSeriesParams.symbol`` / ``InstrumentUniverseParams.symbols``
            — riding on each ``DataRequest.params`` (PIT-scoped by the frozen
            as_of, never plan-bound to one code);
        (c) the Phase-2 compat/pilot precedent maps the base ``code``/``asof_date``
            inputs to ``target_kind="context"`` and deliberately places NOTHING
            on the node (``presets._CORE_BASE_INPUTS``; presets.py's context
            branch is a documented no-op) — so Task 3's single-code screening
            builder must bind the code at a seam it reviews (params-schema
            extension or goal/data-call convention), never via an assumed
            DataContext field."""
        from guanlan_v2.orchestration import presets
        from guanlan_v2.orchestration.context import DataContext
        from guanlan_v2.orchestration.data.source import (
            DataRequest,
            InstrumentSeriesParams,
            InstrumentUniverseParams,
        )
        assert set(DataContext.model_fields) == {
            "schema_version", "as_of", "clock", "mode", "backend", "strict_pit",
            "calendar_id", "resolved_vendor_chains", "source_config_digest",
            "source_registry_digest", "routing_snapshot_digest",
            "data_snapshot_id", "data_snapshot_content_digest",
            "vintage_manifest_digest", "built_at"}
        for name in _D4_CODE_LIKE_NAMES:
            assert name not in DataContext.model_fields, (
                f"DataContext.{name} appeared — a structural subject carrier "
                "surfaced; re-open D3 before Task 3's builder binds it")
        assert "symbol" in InstrumentSeriesParams.model_fields
        assert "symbols" in InstrumentUniverseParams.model_fields
        assert {"params", "params_schema_ref"} <= set(DataRequest.model_fields)
        assert presets._CORE_BASE_INPUTS["news-sentiment"] == (
            ("code", "context", "code"), ("asof_date", "context", "asof_date"))
        assert presets._CORE_BASE_INPUTS["report-writer"][:2] == (
            ("code", "context", "code"), ("asof_date", "context", "asof_date"))


# =========================================================================== #
# item 5 — Phase 5/6 anchors                                                    #
# =========================================================================== #
class TestItem5Phase56Anchors:
    def test_regime_and_rotation_reports_are_registered(self, sealed_registry):
        from guanlan_v2.orchestration.refs import SchemaRef
        for name in ("RegimeReport", "RotationReport", "PortfolioTargetProposal",
                     "MarketFactorReport", "ContextSnapshot", "DualCurveReport"):
            sealed_registry.resolve(SchemaRef(name=name, version="1"))

    def test_bootstrap_context_snapshot_production_path(self):
        from guanlan_v2.orchestration.bootstrap import (
            build_context_snapshot_from_bootstrap,
        )
        from guanlan_v2.orchestration.context import ContextSnapshot
        assert callable(build_context_snapshot_from_bootstrap)
        assert inspect.isclass(ContextSnapshot)

    def test_target_weight_bands_are_phase6_owned(self):
        from guanlan_v2.orchestration import decision_inputs, shadow
        assert shadow.TARGET_WEIGHT_BANDS == (0.0, 0.25, 0.5, 0.75, 1.0)
        # re-exported by OBJECT (never a second copy that could drift):
        assert decision_inputs.TARGET_WEIGHT_BANDS is shadow.TARGET_WEIGHT_BANDS

    def test_phase6_schedule_compute_functions(self):
        from guanlan_v2.orchestration.shadow import (
            compute_cutoff_at,
            compute_eligible_execution_at,
            compute_scheduled_for,
        )
        for fn in (compute_scheduled_for, compute_cutoff_at,
                   compute_eligible_execution_at):
            assert callable(fn)

    def test_d4_rotation_report_carries_no_leader_codes(self):
        """D4 tripwire: TODAY neither the RotationReport mainlines nor the
        ladder factor's point type carries leader stock codes, so ``cand.lane0``
        must be the honest typed refusal. If upstream later DOES grow a code
        field/accessor, this test reddens and re-opens D4 before the refusal
        design goes stale — the exact field sets are pinned."""
        from guanlan_v2.orchestration.market.factors import (
            MainlineRead,
            MarketFactorPoint,
            RotationReport,
        )
        assert set(MainlineRead.model_fields) == {
            "name", "universe_key", "stage", "strength", "persistence",
            "evidence", "chain_nodes"}
        assert set(MarketFactorPoint.model_fields) == {"date", "value", "aux"}
        assert set(RotationReport.model_fields) == {
            "schema_version", "as_of", "factor_report_digest", "mainlines",
            "confidence", "conflicts", "analog_case_ids", "narrative",
            "evidence_factor_ids", "unknown_reason", "content_digest"}
        for model in (MainlineRead, MarketFactorPoint, RotationReport):
            for name in _D4_CODE_LIKE_NAMES:
                assert name not in model.model_fields, (
                    f"{model.__name__}.{name} appeared — leader codes surfaced "
                    "upstream; re-open D4 before cand.lane0's refusal goes stale")
                assert not hasattr(model, name), (
                    f"{model.__name__}.{name} accessor appeared — re-open D4")


# =========================================================================== #
# item 6 — production seams by name                                             #
# =========================================================================== #
class TestItem6ProductionSeams:
    def test_watcher_tick_accepts_injectable_decide_fn(self):
        from guanlan_v2.seats import watcher
        params = inspect.signature(watcher.tick).parameters
        assert "decide_fn" in params
        assert params["decide_fn"].default is None  # injectable, default production
        assert inspect.iscoroutinefunction(watcher.run_loop)  # the lifespan entry

    def test_console_tools_exports(self):
        """Import-and-assert ONLY — guanlan_v2/console/tools.py is owned by a
        concurrent session; this gate never modifies it."""
        from guanlan_v2.console import tools as console_tools
        assert isinstance(console_tools.WW_TOOL_TABLE, list)
        assert len(console_tools.WW_TOOL_TABLE) > 0
        for name in ("register_console_tools", "_wrap", "_self_get", "_self_post"):
            assert callable(getattr(console_tools, name)), name

    def test_agent_interface_doc_generator_and_drift_guard_exist(self):
        assert (_REPO / "scripts" / "gen_agent_interface_doc.py").is_file()
        assert (_REPO / "tests" / "test_agent_interface_doc.py").is_file()

    def test_seats_decision_persistence_helper_resolves(self):
        append = _resolve(FROZEN_EVIDENCE["seats_decision_append"])
        assert callable(append)
        assert list(inspect.signature(append).parameters) == ["kind", "rec"]

    def test_run_head_only_if_present_convention(self, tmp_path, monkeypatch):
        """D7 behavioural proof: a run head lands append-only in the seats runs
        log with None/"" values DROPPED (only-if-present), idempotent by run_id.
        Runs against tmp stores — production var/ is never touched."""
        from guanlan_v2.seats import api as seats_api
        monkeypatch.setattr(seats_api, "_DEC_LOG", tmp_path / "seats_decisions.jsonl")
        monkeypatch.setattr(seats_api, "_RUNS_LOG", tmp_path / "seats_runs.jsonl")
        compat = _resolve(FROZEN_EVIDENCE["seats_compat_writer"])

        state = SimpleNamespace(run_id="p10-handoff-gate")
        run_head = {"run_id": "p10-handoff-gate", "code": "SH600000",
                    "strategy_id": None, "tf": "", "model": "gate-probe"}
        compat(state, decisions=(), run_head=run_head)
        compat(state, decisions=(), run_head=run_head)  # idempotent replay

        lines = (tmp_path / "seats_runs.jsonl").read_text(
            encoding="utf-8").strip().splitlines()
        assert len(lines) == 1  # append-only + idempotent: exactly one head
        head = json.loads(lines[0])
        assert head["run_id"] == "p10-handoff-gate"
        assert head["code"] == "SH600000"
        assert head["model"] == "gate-probe"
        assert head["source"] == "orchestrated"
        assert "strategy_id" not in head  # None dropped (only-if-present)
        assert "tf" not in head           # "" dropped (only-if-present)
        assert not (tmp_path / "seats_decisions.jsonl").exists()  # no rows, no file


# =========================================================================== #
# item 7 — the ranking read surface for cand.v4 / cand.model (D5)               #
# =========================================================================== #
class TestItem7RankingReadSurface:
    def test_reviewed_reader_resolves_by_frozen_name(self):
        reader = _resolve(FROZEN_EVIDENCE["ranking_reader"])
        asof = _resolve(FROZEN_EVIDENCE["ranking_asof"])
        assert callable(reader)
        assert callable(asof)
        # one surface serves BOTH candidate workers: model_id None/"prod" is the
        # production v4 artifact (cand.v4); a variant id reads
        # models/<id>/v4_ranking.parquet (cand.model).
        assert "model_id" in inspect.signature(reader).parameters
        assert "model_id" in inspect.signature(asof).parameters

    def test_variant_artifact_path_helper_resolves(self):
        helper = _resolve(FROZEN_EVIDENCE["variant_ranking_path"])
        assert callable(helper)


# =========================================================================== #
# item 8 — the /archive/put in-process seam                                     #
# =========================================================================== #
class TestItem8ArchiveSeam:
    def test_archive_put_route_exists_in_process(self):
        builder = _resolve(FROZEN_EVIDENCE["archive_router_builder"])
        router = builder()
        put_routes = [r for r in router.routes
                      if getattr(r, "path", "") == "/archive/put"]
        assert len(put_routes) == 1
        assert "POST" in put_routes[0].methods

    def test_console_archive_research_idiom_exists(self):
        idiom = _resolve(FROZEN_EVIDENCE["console_archive_idiom"])
        assert callable(idiom)


# =========================================================================== #
# item 9 — Phase 10 never overwrites Phase 1-9                                  #
# =========================================================================== #
class TestItem9NoOverwrite:
    def test_phase1_9_golden_roster_is_intact(self):
        missing = [n for n in PHASE1_9_GOLDEN_FILES
                   if not (_GOLDEN_DIR / n).is_file()]
        assert missing == []

    def test_phase1_9_orchestration_roster_is_intact(self):
        missing = [n for n in PHASE1_9_ORCH_MODULES
                   if not (_ORCH_SRC / n).exists()]
        assert missing == []

    def test_frozen_test_module_roster_is_intact(self):
        """Item 9's test half: every test module present at freeze time keeps
        existing. Phase 10's new test files are ADDITIONS — a loss or rename of
        any frozen name is an overwrite/delete of the upstream test surface."""
        tests_dir = Path(__file__).resolve().parent
        missing = [n for n in TEST_MODULE_ROSTER_AT_FREEZE
                   if not (tests_dir / n).is_file()]
        assert missing == []

    def test_chain_sealing_goldens_are_byte_identical(self):
        schema_bytes = (_GOLDEN_DIR / "phase9_schema_manifest_v1.json").read_bytes()
        catalog_bytes = (_GOLDEN_DIR / "phase9_catalog_manifest_v1.json").read_bytes()
        assert hashlib.sha256(schema_bytes).hexdigest() == _frozen(
            "phase9_schema_golden_sha256")
        assert hashlib.sha256(catalog_bytes).hexdigest() == _frozen(
            "phase9_catalog_golden_sha256")

    def test_planned_phase10_paths_are_disjoint_from_phase1_9(self):
        golden_names = set(PHASE1_9_GOLDEN_FILES)
        module_names = set(PHASE1_9_ORCH_MODULES)
        # the frozen TEST roster too (minus this gate, the one pre-existing
        # Phase-10 file): a planned test path colliding with an upstream test
        # module name would be an overwrite, not an addition.
        test_names = set(TEST_MODULE_ROSTER_AT_FREEZE) - {"test_phase10_handoff.py"}
        for planned in PHASE10_PLANNED_CREATE_PATHS:
            name = planned.rsplit("/", 1)[-1]
            assert name not in golden_names, planned
            # only a path that would LAND in tests/orchestration/ can collide
            # with a test module name (pipeline/__init__.py is a different dir).
            if planned.startswith("tests/orchestration/") and planned.endswith(".py"):
                assert name not in test_names, planned
            if planned.startswith("guanlan_v2/orchestration/"):
                top = planned.split("/")[2]
                assert top == "pipeline"  # everything lands in the NEW package
                assert top not in module_names
        # the one existing preset is never replaced by the Phase 10 preset.
        assert (_REPO / "config" / "orchestration" / "presets"
                / "main_research_baseline.json").is_file()
        assert ("config/orchestration/presets/luozi_deep_decide_v1.json"
                in PHASE10_PLANNED_CREATE_PATHS)


# =========================================================================== #
# post-merge realities (recorded truths — honest, executable where possible)    #
# =========================================================================== #
class TestRecordedRealities:
    def test_wakeup_stays_honestly_unwired_of_run_scoped_bindings(self):
        """/orchestration/replay/wakeup returned 503 in production because a
        ReplayRuntimeBindings is run-scoped; the R3 launcher DELIBERATELY binds
        neither replay_bindings nor the shadow-wakeup context provider."""
        from guanlan_v2.orchestration.startup import bind_orchestration_launcher
        source = inspect.getsource(bind_orchestration_launcher)
        assert "replay_bindings is NOT bound" in source
        assert "honest clock/bindings 503" in source

    def test_console_plan_approval_wiring_is_honestly_inert_until_provided(self):
        """The console decide endpoint's production wiring is inert by DESIGN
        until an admission provider is bound: no provider → None coordinator →
        empty console kwargs → the endpoints keep their honest 503."""
        from guanlan_v2.orchestration.adapters.api import (
            bind_process_plan_approval_coordinator,
            plan_approval_console_kwargs,
        )
        assert callable(bind_process_plan_approval_coordinator)
        assert plan_approval_console_kwargs(coordinator=None) == {}

    def test_reviewed_baseline_preset_is_intact(self):
        """Task 0b evidence: at freeze time (2026-07-27) the presets directory
        held exactly ONE preset — ``main_research_baseline.json``. The standing
        assertion is only that the reviewed baseline never vanishes (Phase 10's
        Task 6 legitimately ADDS ``luozi_deep_decide_v1.json`` beside it)."""
        preset_dir = _REPO / "config" / "orchestration" / "presets"
        assert (preset_dir / "main_research_baseline.json").is_file()

    def test_no_production_graph_sinks_portfolio_target_proposal(self):
        """Task 0b evidence, stated verbatim by the production launcher: the
        missing piece is a production interval-shaped plan graph — 'no
        production graph emits ``PortfolioTargetProposal@1``'."""
        from guanlan_v2.orchestration.adapters import launcher
        # (the phrase wraps across a source line: match the unbroken tail)
        assert ("production graph emits ``PortfolioTargetProposal@1``"
                in (launcher.__doc__ or ""))

    def test_dual_curve_report_is_registered_badge_is_store_honesty(self, sealed_registry):
        """DualCurveReport@1 IS registered in the sealed chain; the live
        ``curve_report:unresolvable`` badge is payload-store resolution honesty
        (adapters/api.py::_resolve_report_for_badges), never a missing schema."""
        from guanlan_v2.orchestration.adapters.api import _resolve_report_for_badges
        from guanlan_v2.orchestration.refs import SchemaRef
        sealed_registry.resolve(SchemaRef(name="DualCurveReport", version="1"))
        report, unresolved = _resolve_report_for_badges(
            object(), SimpleNamespace(curve_report_ref=None))
        assert (report, unresolved) == (None, False)
