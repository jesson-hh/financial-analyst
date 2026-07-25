# -*- coding: utf-8 -*-
"""Task 0 — the executable Phase 1/2/3/4/5/6/7/8 -> Phase 9 (完整落子/帷幄 adapters,
the FINAL phase) handoff gate.

This is a *consumer* gate, not a red->green feature task: it imports the frozen
Phase-1 (Amendment-1) contract layer, the Phase-2 kernel, the Phase-3 data +
memory facade, the Phase-4 Evaluator-Optimizer, the Phase-5 Bootstrap Lane-0
chain, the Phase-6 shadow-consumer chain, the Phase-7 dynamic-planner /
approval-lease chain, and the Phase-8 四车道 lane catalog / Lane-D debate seal
exactly as the Phase-9 adapters (落子 replay/dual-curve driver, 帷幄 ONLINE
binding, durable stores, retirement gates) will, exercises the real registries /
builders / services / digests through their public surfaces, and asserts the
upstream ABI Phase 9 consumes is intact and singular. Every assertion PASSES
against the already-implemented upstream code on branch ``report-evidence-pack``.
A failure means an upstream Phase 1-8 contract drifted from its frozen exit gate
and BLOCKS Phase 9 — it must NEVER be "fixed" by weakening an assertion or editing
upstream; fix the owning layer. Do not regenerate a golden.

It proves the nine points of ``.superpowers/sdd/task-0-brief.md`` Step 1:

1.  Phase-1 goldens (``schema_manifest_v1.json`` as re-frozen by Phase-1
    Amendment 1 — the 11-model golden incl ``TypedPayloadRef@1`` /
    ``InputArtifactBinding@1`` / ``ContextRuntimeRequirements@1`` — plus digest
    vectors) still pass and ``default_registry()`` seals; ``TypedPayloadRef``
    resolves as the Phase-1 composite (``schema_ref`` + ``payload_ref``) while
    plain ``PayloadRef`` stays the bare locator.
2.  the linear registry chain resolves end-to-end by exact digest
    (``PHASE2_BASE`` -> ``PHASE3_DATA`` -> ``PHASE3_FULL`` -> P4 -> P5 -> P6 -> P7
    -> ``PHASE8_REGISTRY_DIGEST``) and the catalog chain (``PHASE2_STATIC`` -> …
    -> ``PHASE8_CATALOG_DIGEST``), each builder consuming its predecessor's exact
    digest; no "latest" alias exists in any chain module.
3.  Phase 6 shipped + registered ``TargetPosition@1`` / ``PortfolioTargetProposal@1``
    / ``TargetPortfolioIntent@1`` / ``DecisionSchedule@1`` (the presence half of the
    ``DEFERRED_PHASE_PAYLOADS`` flip), plus ``ShadowDecisionAgent`` /
    ``ShadowBacktestRunner`` with BOTH entries — ``run(intents, *, start, end)`` and
    the deterministic dual-curve ``run_targets(target_sets, *, run_config, calendar,
    clock)`` with ``DeterministicTargetSet`` / ``deterministic_apply_key`` (domain
    ``shadow-deterministic-apply-key-v1``) — the schedule-time computations, the
    ``DecisionScheduleRegistry``, the three idempotency key families, and the mirror
    stage-① compatibility profile with its explicit tolerance constants.
4.  Phase 4 exports ``run_optimize`` / ``finalize_candidate`` / ``TrialLedger`` /
    ``OptimizeRunState`` / ``HoldoutReceipt`` and honors
    ``ExperimentStatus.WAITING_FOR_MATURITY``; Phase 5 exports the versioned
    ``BootstrapPlan`` preset + the BOOTSTRAP profile; Phase 7 exports the
    dynamic-planner admission path + the ``ApprovalLease`` standing-approval surface;
    Phase 8 exports the 27-worker lane catalog whose ``dec.trader`` emits ONLY
    ``PortfolioTargetProposal`` (curators #26/#27 present; no seat count asserted).
5.  Phase 5's ``MarketFactorReport`` / regime / rotation surface exposes the
    coverage / ``UNAVAILABLE`` semantics and the ``factor_report_digest`` binding in
    consumable form (the C3 refinement anchor for Task 2b coverage floors + Task 12
    digest-binding).
6.  the ``RuntimeStateCellStore`` startup-namespace-union mechanism accepts a
    reviewed extension (constructor-injected, not a hardcoded list) and the current
    sealed union (Phase-3's seven ``memory.*``) is enumerable from code.
7.  the engine baseline symbols resolve unchanged (Broker.match, VirtualPortfolio,
    CostModel, limits.*, engine.*, PitReader.*) and ``datafeed/live_client.py``
    exports ``probe`` / ``catalog`` / ``resolve_source`` / ``known_sources``.
8.  the three legacy entry points still exist at their grounded seams (console
    ``_spawn_bg`` / ``_run_report_bg`` / ``_call_buddy_report``; engine
    ``swarm.loader.load_preset`` + its cli/tui consumers; ``POST
    /research/loop/start`` -> ``run_research_loop``).
9.  no Phase-9 source/test/golden path shadows a Phase 1-8 owned module, test, or
    golden.

------------------------------------------------------------------------------
TASK-0 CORRECTION CLAUSES resolved against the real code (BINDING on Tasks 1-12;
name-authoritative, never line-authoritative). Full prose in
``.superpowers/sdd/p9-task-0-report.md``:

* **C1 (Phase 6 shadow API).** The runner/agent + ``DeterministicTargetSet`` +
  ``deterministic_apply_key`` live in ``guanlan_v2.orchestration.adapters.luozi``
  (per P6's build). ``deterministic_apply_key_parts`` + the domain constant
  ``SHADOW_DETERMINISTIC_APPLY_KEY_DOMAIN`` live in ``shadow`` (the FLAG-1 move) and
  are re-exported by ``luozi``; the apply-record is dual-family (intent family
  ``shadow-apply-key-v1`` vs deterministic family ``shadow-deterministic-apply-key-v1``).
  The mirror stage-① profile (``COMPATIBILITY_PROFILE_ID='luozi-runbacktest-compat-v1'``
  + the five ``COMPAT_*`` tolerances) is ``luozi``-owned. Reviewed signatures:
  ``ShadowBacktestRunner.run(self, intents, *, start, end)`` /
  ``.run_targets(self, target_sets, *, run_config, calendar, clock)``. The
  ``DEFERRED_PHASE_PAYLOADS`` "flip" is the PRESENCE half (``PHASE6_FLIPPED_PAYLOADS``):
  the four names register ``@1`` in the P6 registry while remaining in
  ``DEFERRED_PHASE_PAYLOADS`` for the Phase-1 absence half.
* **C2 (Phase 4 evaluator API).** ``run_optimize`` (in ``optimize``) accepts
  ``evaluate_validation: Callable[[OptimizeCandidate, DataContext], ValidationMetrics]``;
  the resume carrier is ``MaturityPending(*, resume_after, wakeup_key)`` (in
  ``optimize``) + ``resume_optimize`` over ``WAITING_FOR_MATURITY``; the ``TrialLedger``
  reservation methods are ``reserve_validation`` / ``reserve_holdout`` /
  ``reveal_validation`` / ``exhaust_holdout`` / ``register_holdout_window``.
* **C3 (Phase 5 bootstrap API).** The preset is
  ``BOOTSTRAP_PRESET_ID='bootstrap.lane0'`` / ``BOOTSTRAP_PRESET_VERSION='1'``,
  built by ``build_bootstrap_plan``; the BOOTSTRAP profile is
  ``bootstrap_runtime_profile()`` (``bootstrap-runtime`` / ``1``). Refinement: the
  coverage/UNAVAILABLE surface is ``MarketFactorValue.status`` ∈ {OK,DEGRADED,
  UNAVAILABLE} + ``MarketFactorReport.coverage`` / ``unavailable_factor_ids`` /
  ``content_digest`` (the ``factor_report_digest`` value); ``RegimeReport`` /
  ``RotationReport`` carry ``factor_report_digest``.
* **C4 (Phase 3 data builders).** ``DataSnapshotManifest`` / ``DataRoutingSnapshot``
  -> ``data.registry``; ``DataSourceConfigSnapshot`` -> ``data.snapshot``;
  ``DataSourceRegistry`` -> ``data.runtime``; the reader protocol ``DataReader``
  (``data.reader``) has ``get_ohlcv`` / ``get_indicators`` / ``get_verified_snapshot``
  / ``get_fundamentals`` / ``get_news`` / ``get_signal``. **The memory facade landed
  TYPED**: ``MemoryContextPreparationService.prepare_pit_replay(self, data_context,
  authority, *, prior_context_ref: TypedPayloadRef)`` — Task 4 binds the TYPED form,
  NOT the bare-``PayloadRef`` verbatim quote.
* **C5 (state-cell union).** The reviewed base is
  ``PHASE3_MEMORY_STATE_CELL_NAMESPACES`` (7 ``memory.*``); the injection point is
  ``RuntimeStores(allowed_cell_namespaces=…)`` / ``RuntimeStateCellStore(
  allowed_namespaces=…)`` with ``validate_memory_namespace_wiring``. Task 6 appends
  its two new namespaces to that constructor-injected union, never to a hardcoded list.
* **C6 (seats/autonomy seams).** Decision persistence = ``seats.api._persist_decision``
  (+ ``_LEDGER_LOG`` -> ``var/seats_ledger.jsonl``); state readers =
  ``seats.watcher.load_state`` / ``save_state``; scheduler-gate sibling =
  ``autonomy.runtime.maybe_enqueue_daily_review``; playbook registry =
  ``autonomy.playbooks.PLAYBOOKS`` dict. The Task-4/6/10 additive seams
  (``note_external_llm_use`` / ``maybe_enqueue_shadow_wakeup`` /
  ``maybe_enqueue_lane0_bootstrap``) do NOT yet exist (Phase 9 adds them).
* **C7 (Phase 7 lease API).** The lease surface is on
  ``PlanApprovalCoordinator``: ``issue_lease`` / ``list_leases(*, now)`` /
  ``revoke_lease`` / ``register_and_try_lease`` + ``now()`` (the P8 clock fix).
  ``ApprovalLease`` is a ``DigestModel`` with the 14-field set below;
  ``LeaseAdmissionOutcome.outcome`` ∈ {``lease_admitted``, ``pending_human``}.

Run from repo root: ``python -m pytest tests/orchestration/test_phase9_handoff.py -v``
"""
from __future__ import annotations

import hashlib
import importlib
import inspect
import json
import typing
from pathlib import Path

import pytest

# -- Phase 1 (amended) contract layer --------------------------------------- #
import guanlan_v2.orchestration as orch_pkg
from guanlan_v2.orchestration.digest import CJSON_VERSION
from guanlan_v2.orchestration.refs import PayloadRef, SchemaRef, TypedPayloadRef
from guanlan_v2.orchestration.schema_registry import default_registry
from guanlan_v2.orchestration.enums import ExperimentStatus

# -- Phase 2 kernel (base registry + static catalog + runtime profile) ------- #
from guanlan_v2.orchestration.runtime_contracts import (
    PHASE2_BASE_REGISTRY_DIGEST,
    StaticRuntimeProfile,
    static_runtime_profile,
    phase2_runtime_registry,
)
from guanlan_v2.orchestration.presets import PHASE2_STATIC_CATALOG_DIGEST
from guanlan_v2.orchestration.eventstore import (
    RuntimeStateCellStore,
    RuntimeStores,
)

# -- Phase 3 data + memory chain -------------------------------------------- #
from guanlan_v2.orchestration.data import schema_registry as dsr
from guanlan_v2.orchestration.data import catalog as dcat
from guanlan_v2.orchestration.memory import schema_registry as mreg
from guanlan_v2.orchestration.memory import catalog as mcat
from guanlan_v2.orchestration.memory.store import (
    PHASE3_MEMORY_STATE_CELL_NAMESPACES,
    validate_memory_namespace_wiring,
)

# -- Phase 4 / 5 / 6 / 7 / 8 registry+catalog chain -------------------------- #
from guanlan_v2.orchestration import trial
from guanlan_v2.orchestration import optimize
from guanlan_v2.orchestration import bootstrap
from guanlan_v2.orchestration import shadow
from guanlan_v2.orchestration import phase7_registry as p7
from guanlan_v2.orchestration import lane_catalog as p8
from guanlan_v2.orchestration.adapters import luozi as lz
from guanlan_v2.orchestration.catalog import count_final_workers

# -- Phase 6 portfolio-target / intent + schedule (point 3) ------------------ #
from guanlan_v2.orchestration.shadow import (
    PortfolioTargetProposal,
    TargetPortfolioIntent,
    TargetPosition,
    DecisionSchedule,
    DecisionScheduleRegistry,
    SHADOW_APPLY_KEY_DOMAIN,
    SHADOW_DETERMINISTIC_APPLY_KEY_DOMAIN,
    SHADOW_MATCHING_ENGINE_VERSION,
    target_apply_key,
    shadow_order_id,
    shadow_fill_id,
    compute_scheduled_for,
    compute_cutoff_at,
    compute_eligible_execution_at,
    deterministic_apply_key_parts,
    UnsupportedBarFrequencyError,
)

# -- Phase 4 exports (point 4 / C2) ------------------------------------------ #
from guanlan_v2.orchestration.trial import OptimizeRunState, HoldoutReceipt
from guanlan_v2.orchestration.trial_ledger import TrialLedger
from guanlan_v2.orchestration.optimize import (
    run_optimize,
    finalize_candidate,
    resume_optimize,
    MaturityPending,
)

# -- Phase 5 exports (point 4 / C3) ------------------------------------------ #
from guanlan_v2.orchestration.bootstrap import (
    BootstrapPlan,
    BootstrapRuntimeProfile,
    bootstrap_runtime_profile,
    build_bootstrap_plan,
    BOOTSTRAP_PRESET_ID,
    BOOTSTRAP_PRESET_VERSION,
)

# -- Phase 5 market-factor surface (point 5 / C3 refinement) ----------------- #
from guanlan_v2.orchestration.market.factors import (
    MarketFactorValue,
    MarketFactorReport,
    RegimeReport,
    RotationReport,
)

# -- Phase 7 lease surface (point 4 / C7) ------------------------------------ #
from guanlan_v2.orchestration.approval import (
    PlanApprovalCoordinator,
    ApprovalLease,
    LeaseAdmissionOutcome,
)


# --------------------------------------------------------------------------- #
# Paths                                                                        #
# --------------------------------------------------------------------------- #
TESTS_DIR = Path(__file__).resolve().parent
GOLDEN_DIR = TESTS_DIR / "golden"
FIXTURES_DIR = TESTS_DIR / "fixtures"
ORCH_DIR = Path(orch_pkg.__file__).resolve().parent
REPO_ROOT = ORCH_DIR.parent.parent
CONFIG_DIR = REPO_ROOT / "config"

D64 = "0" * 64


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _params(func) -> list[str]:
    return list(inspect.signature(func).parameters)


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


# =========================================================================== #
# Step 2 — reviewed upstream evidence recorded verbatim (branch HEAD 3fa326b).  #
# Each digest is re-derived from a sealed builder / canonical accessor below;    #
# none is a local path or a mutable singleton identity, and none is regenerated  #
# from test code (values captured out-of-band, then frozen here).               #
# =========================================================================== #

# --- the cumulative registry chain (order-independent, sealed per node) ----- #
#: Point 1 connective: the sealed Phase-1 registry digest IS the Phase-2 base.
PHASE1_SEALED_REGISTRY_DIGEST = (
    "75f7920db13cdcaac89a70e0103812a29348ab3caaa98b9c1020429bb4e18b03"
)
PHASE2_BASE_REGISTRY_DIGEST_FROZEN = PHASE1_SEALED_REGISTRY_DIGEST
#: the derived Phase-2 RUNTIME registry digest the data builder actually pins
#: (``build_phase3_registry`` requires this, not the Phase-1/Phase-2-base digest).
PHASE2_RUNTIME_REGISTRY_DIGEST = (
    "b11fcacf0efd931dc3a3d11f859d6f5bac86c1c24b78e5cc1d2b44829822d8d5"
)
PHASE3_DATA_REGISTRY_DIGEST = (
    "9119fa179a598b3d2d62e059747c6fd581d4e8b3be5eea0304da47d80ca51612"
)
PHASE3_FULL_REGISTRY_DIGEST = (
    "ff2a56d283499e347dcdbf4c2f5c5977710fd5575afbc94ee34819b2b531e49d"
)
PHASE4_REGISTRY_DIGEST = (
    "5475880847f5e44fae500545d24fd01f227b507d7b74945e0427bec32f361f0d"
)
PHASE5_REGISTRY_DIGEST = (
    "5703d4c4916c7bdbbe209fcc7cf2ee9971dd0c2fb25313eb401f2b271e1eeb4f"
)
PHASE6_REGISTRY_DIGEST = (
    "0fd0ad0f77695e16905f545e0a3184867fb0e852a97b7191700c2d191687815f"
)
PHASE7_REGISTRY_DIGEST = (
    "b23e389cda2a6af313d624613893ce5244fcc36379576e6591665aa77bb19dfe"
)
PHASE8_REGISTRY_DIGEST = (
    "d719e19bc8c64f56324ee36ca0d3aa039e5eac1c9488d80babe6ddce81e5e089"
)

# --- the cumulative catalog chain (P6 is the identity node: P6 == P5) ------- #
PHASE2_STATIC_CATALOG_DIGEST_FROZEN = (
    "b41bf223f0dd0b05c5a4f4f7bbd32960eef90d7176a17300dbe576f6b61bf0d3"
)
PHASE3_DATA_CATALOG_DIGEST = (
    "ba7086929a79de08a8d4002f36549a4e325dc4c344f615d77506adaf74454e0c"
)
PHASE3_FULL_CATALOG_DIGEST = (
    "c13294e5f020de542cc553d92e509d3dfea45673700373ac0c013d9115c3a773"
)
PHASE4_CATALOG_DIGEST = (
    "aefe0cf3b12f875d4d5062db7b6d00e8ae65cf3f5081eb527d8750671b7e651b"
)
PHASE5_CATALOG_DIGEST = (
    "42af246047adb25956df507b56aabb215baec692859772e4b4f98d776c149229"
)
PHASE6_CATALOG_DIGEST = PHASE5_CATALOG_DIGEST
PHASE7_CATALOG_DIGEST = (
    "c760df02763b4342096d07df4663e8880c91cf49715a243bcf22d5dbe1fd9a96"
)
PHASE8_CATALOG_DIGEST = (
    "7f00dde4f78761cc4a5c991b7717b9002ea82d7d86d31ab007d4af80f0112c91"
)

#: the Phase-8 cumulative registry entry count + full-catalog final-worker count.
PHASE8_REGISTRY_ENTRY_COUNT = 191
PHASE8_FINAL_WORKER_COUNT = 27

#: point 4: the Phase-8 ``dec.trader`` frozen semantic digest + its sole output.
DEC_TRADER_SEMANTIC_DIGEST = (
    "739ec36577892fcb079e5d9167c6f76462ec9edb334fa208cc81b88f2bf11113"
)

#: point 4 / R2: the two curator finals #26/#27 (accepted as handed off).
PHASE8_CURATOR_IDS = frozenset({"pv.curator", "quant.curator"})

#: point 8 / clause (f)-precedent: the two runtime-profile identities.
STATIC_PROFILE_ID = "static-runtime"
STATIC_PROFILE_VERSION = "1"
STATIC_PROFILE_DIGEST = (
    "fd31880cd1ae693cd54cda6017f282e72d71dcd5d4ec3bba170b3dcadcf07afa"
)
BOOTSTRAP_PROFILE_ID = "bootstrap-runtime"
BOOTSTRAP_PROFILE_VERSION = "1"
BOOTSTRAP_PROFILE_DIGEST = (
    "bdae258884ad3f515690ac48974ca7cfc91c809f7ad714b77adcc04d985fb7a0"
)

#: point 3 / C1: the four Phase-6 flipped payloads (presence half).
PHASE6_FLIPPED_PAYLOADS = frozenset({
    "TargetPosition",
    "PortfolioTargetProposal",
    "TargetPortfolioIntent",
    "DecisionSchedule",
})

#: point 3 / C1: the mirror stage-① profile identity + tolerance constants.
COMPATIBILITY_PROFILE_ID = "luozi-runbacktest-compat-v1"

#: point 7 / Step 2: the engine baseline modules' frozen sha256 BYTE digests — the
#: Task 5/8 in-test engine-untouched comparison baseline. Recomputed from the pinned
#: engine fork (``financial_analyst.backtest.*``); a drift here flags a touched engine.
ENGINE_MODULE_BYTE_DIGESTS: dict[str, str] = {
    "broker.py": "59277b42c47baceab8aa0c4a2cf5f0fe97f90b9361562fe73c32b947f8364e1d",
    "portfolio.py": "eddcfd8e962fa9bbbf0fb577395e0304a89f51f88d655eb8cfc7b02066d52c00",
    "costs.py": "aae26994fe7b352280414daebab482e358ab2bfea6698ebe75f48a8d1d76d7a4",
    "limits.py": "85b3d660766e263f235feef8a152b3a15a8a2403f5b1756ec99d1e45540917a0",
    "engine.py": "85488fa8e6b451956e1f0cd87e72062198baf4bcafe5ebf95c72913fc750e306",
    "pit_reader.py": "1c052bddaa7fb4155b279f57580cadab3bd00cf61aa72bdc7e7a269a1acfa58a",
}

#: C7: the reviewed ``ApprovalLease`` field set + admission-outcome vocabulary.
APPROVAL_LEASE_FIELDS = (
    "schema_version", "lease_id", "purpose", "preset_id", "preset_record_digest",
    "catalog_digest", "registry_digest", "valid_from", "valid_until",
    "max_admissions", "budget_cap_llm_invocations", "issued_by", "issued_at",
    "reason",
)
LEASE_ADMISSION_OUTCOMES = frozenset({"lease_admitted", "pending_human"})


# --------------------------------------------------------------------------- #
# Phase 1-8 owned files Phase 9 must never overwrite (point 9)                 #
# --------------------------------------------------------------------------- #
PHASE1_8_OWNED_MODULES = tuple(
    ORCH_DIR / name
    for name in (
        # Phase 1
        "schemas.py", "spec.py", "schema_registry.py", "catalog.py", "refs.py",
        "digest.py", "enums.py", "events.py", "context.py", "migration.py",
        # Phase 2
        "admission.py", "budget.py", "eventstore.py", "catalog_runtime.py",
        "worker.py", "runtime_clock.py", "runtime_contracts.py",
        "runtime_support.py", "presets.py", "dag.py", "pool.py",
        # Phase 4
        "trial.py", "trial_ledger.py", "evaluator.py", "optimize.py", "governor.py",
        # Phase 5
        "bootstrap.py",
        # Phase 6
        "shadow.py",
        # Phase 7
        "orchestrator.py", "plan_presets.py", "plan_diff.py", "approval.py",
        "planner_gateway.py", "phase7_registry.py",
        # Phase 8
        "skilltree.py", "capability_manifest.py", "pattern_registry.py",
        "decision_inputs.py", "honesty.py", "model_tiers.py", "lane_payloads.py",
        "lane_catalog.py", "debate.py",
    )
)
#: the Phase-6 adapters file Phase 9 EXTENDS (never overwrites its shipped surface).
PHASE6_ADAPTERS_LUOZI = ORCH_DIR / "adapters" / "luozi.py"

UPSTREAM_GOLDEN_FILES = tuple(
    GOLDEN_DIR / name
    for name in (
        "schema_manifest_v1.json", "digest_vectors_v1.json",
        "phase3_full_schema_manifest_v1.json", "phase3_full_catalog_manifest_v1.json",
        "phase4_schema_manifest_v1.json", "phase4_catalog_manifest_v1.json",
        "phase5_schema_manifest_v1.json", "phase5_catalog_manifest_v1.json",
        "phase6_schema_manifest_v1.json", "phase6_catalog_manifest_v1.json",
        "phase7_schema_manifest_v1.json", "phase7_catalog_manifest_v1.json",
        "phase8_schema_manifest_v1.json", "phase8_catalog_manifest_v1.json",
    )
)

#: The Phase-9 module/golden paths the plan will create task-by-task (File Structure
#: table). The durable point-9 guarantee: none of them ever *shadows* (equals) a
#: Phase 1-8 owned source or golden. ``adapters/luozi.py`` is EXTENDED, not created.
PHASE9_NEW_PATHS = (
    ORCH_DIR / "adapters" / "contracts.py",
    ORCH_DIR / "adapters" / "replay_data.py",
    ORCH_DIR / "adapters" / "live_data.py",
    ORCH_DIR / "adapters" / "weiwo.py",
    ORCH_DIR / "adapters" / "retirement.py",
    ORCH_DIR / "adapters" / "chain.py",
    ORCH_DIR / "adapters" / "api.py",
    ORCH_DIR / "adapters" / "durable.py",
    GOLDEN_DIR / "phase9_schema_manifest_v1.json",
    GOLDEN_DIR / "phase9_catalog_manifest_v1.json",
    GOLDEN_DIR / "shadow_execution_golden_v1.json",
    GOLDEN_DIR / "phase9_retirement_gates_v1.json",
    TESTS_DIR / "test_phase9_handoff.py",
)


# =========================================================================== #
# Point 1 — Phase-1 (Amendment-1) goldens + TypedPayloadRef composite.           #
# =========================================================================== #
def test_point1_phase1_goldens_11_models_and_amendment_payloads():
    s = _load_json(GOLDEN_DIR / "schema_manifest_v1.json")
    assert s["algorithm"] == CJSON_VERSION
    assert len(s["entries"]) == 11
    assert s["registry_digest"] == PHASE1_SEALED_REGISTRY_DIGEST
    keys = {e["key"] for e in s["entries"]}
    for amended in ("TypedPayloadRef@1", "InputArtifactBinding@1",
                    "ContextRuntimeRequirements@1"):
        assert amended in keys, f"Amendment-1 golden lost {amended}"
    # digest vectors golden still loads with the same algorithm tag.
    dv = _load_json(GOLDEN_DIR / "digest_vectors_v1.json")
    assert dv["algorithm"] == CJSON_VERSION


def test_point1_default_registry_seals_and_is_the_chain_root():
    reg = default_registry()
    assert reg.sealed
    # the sealed Phase-1 registry digest IS the Phase-2 base (the chain root).
    assert reg.registry_digest == PHASE1_SEALED_REGISTRY_DIGEST
    assert PHASE2_BASE_REGISTRY_DIGEST == PHASE2_BASE_REGISTRY_DIGEST_FROZEN
    assert reg.registry_digest == PHASE2_BASE_REGISTRY_DIGEST


def test_point1_typed_payload_ref_is_the_composite_and_payload_ref_stays_bare():
    # the composite carries a SchemaRef + a PayloadRef, nothing else.
    assert list(TypedPayloadRef.model_fields) == [
        "schema_version", "schema_ref", "payload_ref"]
    # the bare locator carries the raw namespace/object/digest triple.
    assert list(PayloadRef.model_fields) == [
        "schema_version", "namespace", "object_id", "content_digest"]
    bare = PayloadRef(namespace="main", object_id="obj-1", content_digest=D64)
    typed = TypedPayloadRef(
        schema_ref=SchemaRef(name="ContextSnapshot", version="1"),
        payload_ref=bare)
    assert typed.payload_ref == bare
    assert typed.schema_ref.name == "ContextSnapshot"
    # a TypedPayloadRef is NOT a PayloadRef (composite vs bare locator, distinct types).
    assert not isinstance(bare, TypedPayloadRef)
    assert typed.__class__ is not PayloadRef


# =========================================================================== #
# Point 2 — the registry + catalog chains resolve by exact digest, no "latest".  #
# =========================================================================== #
def test_point2_registry_chain_resolves_end_to_end_by_exact_digest():
    # the derived Phase-2 runtime registry (the data builder's real pin).
    assert phase2_runtime_registry(PHASE2_BASE_REGISTRY_DIGEST).registry_digest == \
        PHASE2_RUNTIME_REGISTRY_DIGEST
    # each cumulative node reproduces its frozen digest from its sealed accessor.
    assert dsr.PHASE3_DATA_REGISTRY_DIGEST == PHASE3_DATA_REGISTRY_DIGEST
    assert mreg.PHASE3_FULL_REGISTRY_DIGEST == PHASE3_FULL_REGISTRY_DIGEST
    assert trial.PHASE4_REGISTRY_DIGEST == PHASE4_REGISTRY_DIGEST
    assert bootstrap.PHASE5_REGISTRY_DIGEST == PHASE5_REGISTRY_DIGEST
    assert shadow.PHASE6_REGISTRY_DIGEST == PHASE6_REGISTRY_DIGEST
    assert p7.PHASE7_REGISTRY_DIGEST == PHASE7_REGISTRY_DIGEST
    assert p8.PHASE8_REGISTRY_DIGEST == PHASE8_REGISTRY_DIGEST
    # the eight cumulative registry nodes are genuinely distinct.
    assert len({
        PHASE2_RUNTIME_REGISTRY_DIGEST, PHASE3_DATA_REGISTRY_DIGEST,
        PHASE3_FULL_REGISTRY_DIGEST, PHASE4_REGISTRY_DIGEST, PHASE5_REGISTRY_DIGEST,
        PHASE6_REGISTRY_DIGEST, PHASE7_REGISTRY_DIGEST, PHASE8_REGISTRY_DIGEST}) == 8


def test_point2_registry_chain_base_pins_verify_each_predecessor():
    # each node's declared base == its predecessor's sealed digest.
    assert trial.PHASE4_BASE_REGISTRY_DIGEST == PHASE3_FULL_REGISTRY_DIGEST
    assert bootstrap.PHASE5_BASE_REGISTRY_DIGEST == PHASE4_REGISTRY_DIGEST
    assert shadow.PHASE6_BASE_REGISTRY_DIGEST == PHASE5_REGISTRY_DIGEST
    assert p7.PHASE7_BASE_REGISTRY_DIGEST == PHASE6_REGISTRY_DIGEST
    assert p8.PHASE8_BASE_REGISTRY_DIGEST == PHASE7_REGISTRY_DIGEST
    # each builder rejects a drifted base BEFORE any registration.
    for build, err in (
        (trial.build_phase4_registry, trial.Phase4RegistryError),
        (bootstrap.build_phase5_registry, bootstrap.Phase5RegistryError),
        (shadow.build_phase6_registry, shadow.Phase6RegistryError),
        (p7.build_phase7_registry, p7.Phase7RegistryError),
        (p8.build_phase8_registry, p8.Phase8RegistryError),
        (dsr.build_phase3_registry, dsr.Phase3DataRegistryError),
        (mreg.build_phase3_full_registry, mreg.Phase3FullRegistryError),
    ):
        with pytest.raises(err):
            build(D64)


def test_point2_catalog_chain_resolves_and_base_pins_verify():
    assert dcat.PHASE3_DATA_CATALOG_DIGEST == PHASE3_DATA_CATALOG_DIGEST
    assert mcat.PHASE3_FULL_CATALOG_DIGEST == PHASE3_FULL_CATALOG_DIGEST
    assert trial.PHASE4_CATALOG_DIGEST == PHASE4_CATALOG_DIGEST
    assert bootstrap.PHASE5_CATALOG_DIGEST == PHASE5_CATALOG_DIGEST
    assert shadow.PHASE6_CATALOG_DIGEST == PHASE6_CATALOG_DIGEST == PHASE5_CATALOG_DIGEST
    assert p7.PHASE7_CATALOG_DIGEST == PHASE7_CATALOG_DIGEST
    assert p8.PHASE8_CATALOG_DIGEST == PHASE8_CATALOG_DIGEST
    # base pins: data <- P2 static; full <- data; P4..P8 <- predecessor.
    assert dcat.PHASE3_BASE_CATALOG_DIGEST == PHASE2_STATIC_CATALOG_DIGEST == \
        PHASE2_STATIC_CATALOG_DIGEST_FROZEN
    assert mcat.PHASE3_FULL_BASE_CATALOG_DIGEST == PHASE3_DATA_CATALOG_DIGEST
    assert trial.PHASE4_BASE_CATALOG_DIGEST == PHASE3_FULL_CATALOG_DIGEST
    assert bootstrap.PHASE5_BASE_CATALOG_DIGEST == PHASE4_CATALOG_DIGEST
    assert shadow.PHASE6_BASE_CATALOG_DIGEST == PHASE5_CATALOG_DIGEST
    assert p7.PHASE7_BASE_CATALOG_DIGEST == PHASE6_CATALOG_DIGEST
    assert p8.PHASE8_BASE_CATALOG_DIGEST == PHASE7_CATALOG_DIGEST


def test_point2_no_latest_alias_in_any_chain_module():
    # a mutable "latest registry/catalog" fork is exactly what the digest chain
    # forbids; no chain module may expose a public ``latest``-named attribute.
    chain_mods = (
        "guanlan_v2.orchestration.runtime_contracts",
        "guanlan_v2.orchestration.presets",
        "guanlan_v2.orchestration.data.schema_registry",
        "guanlan_v2.orchestration.data.catalog",
        "guanlan_v2.orchestration.data.registry",
        "guanlan_v2.orchestration.memory.schema_registry",
        "guanlan_v2.orchestration.memory.catalog",
        "guanlan_v2.orchestration.trial",
        "guanlan_v2.orchestration.bootstrap",
        "guanlan_v2.orchestration.shadow",
        "guanlan_v2.orchestration.phase7_registry",
        "guanlan_v2.orchestration.lane_catalog",
    )
    for mod_name in chain_mods:
        mod = importlib.import_module(mod_name)
        offenders = [n for n in dir(mod)
                     if "latest" in n.lower() and not n.startswith("_")]
        assert offenders == [], f"{mod_name} exposes a 'latest' alias: {offenders}"


# =========================================================================== #
# Point 3 — Phase-6 shadow API: targets, both runner entries, keys, mirror.      #
# =========================================================================== #
def test_point3_four_target_payloads_registered_at_1_and_flip_is_presence_half():
    reg = shadow.build_phase6_registry(shadow.PHASE6_BASE_REGISTRY_DIGEST)
    keys = {e.key for e in reg.manifest()}
    for name in PHASE6_FLIPPED_PAYLOADS:
        assert f"{name}@1" in keys, f"Phase-6 registry lost {name}@1"
    # the "flip" is the PRESENCE half: the names register @1 in the P6 registry AND
    # remain in the Phase-1 absence guard (DEFERRED_PHASE_PAYLOADS).
    tcc = importlib.import_module("tests.orchestration.test_contract_completeness")
    assert frozenset(tcc.PHASE6_FLIPPED_PAYLOADS) == PHASE6_FLIPPED_PAYLOADS
    for name in PHASE6_FLIPPED_PAYLOADS:
        assert name in tcc.DEFERRED_PHASE_PAYLOADS


def test_point3_shadow_runner_has_both_entries_at_reviewed_locations():
    # C1: the runner + agent + DeterministicTargetSet + deterministic_apply_key are
    # ``adapters.luozi``-owned (NOT shadow.py).
    for name in ("ShadowDecisionAgent", "ShadowBacktestRunner",
                 "DeterministicTargetSet", "deterministic_apply_key"):
        assert hasattr(lz, name), f"adapters.luozi missing {name}"
    assert not hasattr(shadow, "ShadowBacktestRunner")
    # both execute entries with the reviewed signatures.
    assert _params(lz.ShadowBacktestRunner.run) == ["self", "intents", "start", "end"]
    assert _params(lz.ShadowBacktestRunner.run_targets) == [
        "self", "target_sets", "run_config", "calendar", "clock"]
    # the deterministic apply key is a thin wrapper over the shadow-owned parts fn.
    assert _params(lz.deterministic_apply_key) == ["target_set"]


def test_point3_deterministic_apply_key_family_is_disjoint_from_intent_family():
    # C1: the domain constant + parts builder moved INTO shadow (FLAG-1) and are
    # re-exported by luozi; the two apply-key families are structurally disjoint.
    assert SHADOW_DETERMINISTIC_APPLY_KEY_DOMAIN == "shadow-deterministic-apply-key-v1"
    assert SHADOW_APPLY_KEY_DOMAIN == "shadow-apply-key-v1"
    assert SHADOW_DETERMINISTIC_APPLY_KEY_DOMAIN != SHADOW_APPLY_KEY_DOMAIN
    assert lz.SHADOW_DETERMINISTIC_APPLY_KEY_DOMAIN == SHADOW_DETERMINISTIC_APPLY_KEY_DOMAIN
    # the deterministic parts builder is shadow-owned + re-exported by luozi.
    assert deterministic_apply_key_parts.__module__ == "guanlan_v2.orchestration.shadow"


def test_point3_schedule_time_computations_and_bar_frequency_guard():
    assert _params(compute_scheduled_for) == ["schedule", "session_date", "calendar"]
    assert _params(compute_cutoff_at) == ["schedule", "session_date"]
    assert _params(compute_eligible_execution_at) == [
        "schedule", "scheduled_for", "calendar"]
    # the matching-engine version + its unsupported-bar guard.
    assert SHADOW_MATCHING_ENGINE_VERSION == "shadow-match-v1"
    assert issubclass(UnsupportedBarFrequencyError, Exception)


def test_point3_three_idempotency_key_families_are_domain_tagged_and_pure():
    # the three families are keyed on their exact tuples; each is a pure function of
    # its own components (re-derivation is deterministic).
    assert _params(shadow_order_id) == [
        "apply_key", "symbol", "order_kind", "trigger_bar", "ordinal"]
    assert _params(shadow_fill_id) == ["order_id", "fill_seq"]
    assert _params(target_apply_key) == ["intent"]
    # DecisionScheduleRegistry is the schedule registry service.
    assert hasattr(DecisionScheduleRegistry, "register")
    assert hasattr(DecisionScheduleRegistry, "resolve")
    assert hasattr(DecisionScheduleRegistry, "seal")


def test_point3_mirror_stage_one_profile_and_tolerances_are_luozi_owned():
    # C1: the frontend runBacktest compatibility mirror + its five explicit
    # numeric tolerances are ``adapters.luozi``-owned.
    assert lz.COMPATIBILITY_PROFILE_ID == COMPATIBILITY_PROFILE_ID
    assert lz.COMPAT_TRADE_STRUCTURE_TOL == 0
    assert lz.COMPAT_PRICE_REL_TOL == 1e-9
    assert lz.COMPAT_RETURN_ABS_TOL == 1e-9
    assert lz.COMPAT_EQUITY_REL_TOL == 1e-9
    assert lz.COMPAT_METRIC_REL_TOL == 1e-6
    for carrier in ("CompatSignal", "CompatClock", "CompatTrade",
                    "CompatibilityRunResult", "CompatibilityProfileError"):
        assert hasattr(lz, carrier), f"adapters.luozi missing mirror carrier {carrier}"


# =========================================================================== #
# Point 4 — Phase 4/5/7/8 export surfaces the Phase-9 driver consumes.           #
# =========================================================================== #
def test_point4_phase4_exports_and_maturity_status():
    assert run_optimize.__module__ == "guanlan_v2.orchestration.optimize"
    assert finalize_candidate.__module__ == "guanlan_v2.orchestration.optimize"
    assert TrialLedger.__module__ == "guanlan_v2.orchestration.trial_ledger"
    assert OptimizeRunState.__module__ == "guanlan_v2.orchestration.trial"
    assert HoldoutReceipt.__module__ == "guanlan_v2.orchestration.trial"
    assert ExperimentStatus.WAITING_FOR_MATURITY.value == "waiting_for_maturity"
    # C2: the evaluator callable + the resume carrier are Phase-4-owned.
    assert "evaluate_validation" in _params(run_optimize)
    assert _params(MaturityPending.__init__) == ["self", "resume_after", "wakeup_key"]
    assert callable(resume_optimize)
    for meth in ("reserve_validation", "reserve_holdout", "reveal_validation",
                 "exhaust_holdout", "register_holdout_window"):
        assert hasattr(TrialLedger, meth), f"TrialLedger lost {meth}"


def test_point4_phase5_bootstrap_preset_and_profile():
    # C3: the versioned BootstrapPlan preset id/version + builder.
    assert BOOTSTRAP_PRESET_ID == "bootstrap.lane0"
    assert BOOTSTRAP_PRESET_VERSION == "1"
    assert "preset_version" in _params(build_bootstrap_plan)
    assert list(BootstrapPlan.model_fields)[:3] == [
        "schema_version", "preset_id", "preset_version"]
    # the BOOTSTRAP-enabled profile (distinct identity from static-runtime).
    brp = bootstrap_runtime_profile()
    assert isinstance(brp, BootstrapRuntimeProfile)
    assert brp.profile_id == BOOTSTRAP_PROFILE_ID
    assert brp.profile_version == BOOTSTRAP_PROFILE_VERSION
    assert brp.profile_digest == BOOTSTRAP_PROFILE_DIGEST
    srp = static_runtime_profile()
    assert isinstance(srp, StaticRuntimeProfile)
    assert srp.profile_id == STATIC_PROFILE_ID
    assert srp.profile_digest == STATIC_PROFILE_DIGEST
    assert srp.profile_id != brp.profile_id


def test_point4_phase7_lease_surface_and_admission_path():
    # C7: the standing-approval lease surface lives on the coordinator + the
    # digest-bound admission path is present.
    for meth in ("issue_lease", "list_leases", "revoke_lease",
                 "register_and_try_lease", "now"):
        assert hasattr(PlanApprovalCoordinator, meth), f"coordinator lost {meth}"
    assert _params(PlanApprovalCoordinator.list_leases) == ["self", "now"]
    assert tuple(ApprovalLease.model_fields) == APPROVAL_LEASE_FIELDS
    # the admission outcome vocabulary is the closed 2-value set. The dataclass uses
    # ``from __future__ import annotations`` so the field is a string annotation;
    # resolve ONLY it in the approval module namespace (a full ``get_type_hints``
    # trips on sibling TYPE_CHECKING-only fields).
    import guanlan_v2.orchestration.approval as _ap_mod
    outcome_ann = eval(  # noqa: S307 - resolving a frozen module-owned annotation
        LeaseAdmissionOutcome.__annotations__["outcome"], dict(vars(_ap_mod)))
    assert frozenset(typing.get_args(outcome_ann)) == LEASE_ADMISSION_OUTCOMES


def test_point4_phase8_catalog_dec_trader_emits_only_portfolio_target_proposal():
    snap = p8.phase8_catalog_snapshot()
    # accept the R2-revised catalog as handed off: assert NO seat count; the final
    # count is the 27-worker seal incl the two curators #26/#27.
    assert count_final_workers(snap) == PHASE8_FINAL_WORKER_COUNT
    finals = {w.id for w in snap.workers if w.catalog_role == "final"}
    assert PHASE8_CURATOR_IDS <= finals
    # dec.trader emits ONLY PortfolioTargetProposal@1 — the single decision surface.
    trader = next(w for w in snap.workers if w.id == "dec.trader")
    assert len(trader.outputs) == 1
    out = trader.outputs[0]
    assert out.schema_ref.name == "PortfolioTargetProposal"
    assert out.schema_ref.version == "1"
    assert trader.semantic_digest() == DEC_TRADER_SEMANTIC_DIGEST


def test_point4_phase8_registry_seal_reproduces_the_frozen_entry_count():
    reg = p8.build_phase8_registry(p8.PHASE8_BASE_REGISTRY_DIGEST)
    assert reg.sealed
    assert reg.registry_digest == PHASE8_REGISTRY_DIGEST
    assert len(reg.manifest()) == PHASE8_REGISTRY_ENTRY_COUNT


# =========================================================================== #
# Point 5 — coverage/UNAVAILABLE + factor_report_digest surface (C3 refine).     #
# =========================================================================== #
def test_point5_market_factor_report_coverage_and_unavailable_surface():
    # C3 refinement anchor for Task 2b coverage floors.
    status_ann = MarketFactorValue.model_fields["status"].annotation
    assert set(typing.get_args(status_ann)) == {"OK", "DEGRADED", "UNAVAILABLE"}
    assert "coverage" in MarketFactorValue.model_fields
    for field in ("coverage", "unavailable_factor_ids", "content_digest"):
        assert field in MarketFactorReport.model_fields, \
            f"MarketFactorReport lost {field}"


def test_point5_regime_and_rotation_carry_factor_report_digest_binding():
    # C3 refinement anchor for Task 12 digest-binding: the ①->④ report binding.
    assert "factor_report_digest" in RegimeReport.model_fields
    assert "factor_report_digest" in RotationReport.model_fields


# =========================================================================== #
# Point 6 — the RuntimeStateCellStore startup-namespace-union mechanism.         #
# =========================================================================== #
def test_point6_state_cell_union_is_constructor_injected_not_hardcoded():
    # C5: the union is injected at construction (a reviewed extension point), never
    # a hardcoded module list — Task 6 appends its two namespaces to what is passed.
    assert "allowed_namespaces" in _params(RuntimeStateCellStore.__init__)
    assert "allowed_cell_namespaces" in _params(RuntimeStores.__init__)
    # the current sealed union (Phase-3's seven memory.*) is enumerable from code.
    assert len(PHASE3_MEMORY_STATE_CELL_NAMESPACES) == 7
    assert all(ns.startswith("memory.") for ns in PHASE3_MEMORY_STATE_CELL_NAMESPACES)
    assert callable(validate_memory_namespace_wiring)
    # a store built over the reviewed union enumerates exactly it.
    store = RuntimeStateCellStore(
        _NullShared(), allowed_namespaces=frozenset(PHASE3_MEMORY_STATE_CELL_NAMESPACES))
    assert store.allowed_namespaces == frozenset(PHASE3_MEMORY_STATE_CELL_NAMESPACES)


class _NullShared:
    """A structural stand-in for the store's ``_Shared`` backend (never read here —
    this gate only exercises the namespace-union surface, never a load)."""
    backend = None


# =========================================================================== #
# Point 7 — engine baseline symbols unchanged + byte-digest baseline.            #
# =========================================================================== #
def _engine_backtest_dir() -> Path:
    import financial_analyst  # pinned to the in-repo engine fork by tests/conftest.py
    return Path(financial_analyst.__file__).resolve().parent / "backtest"


def test_point7_engine_baseline_symbols_resolve_unchanged():
    from financial_analyst.backtest.broker import Broker
    from financial_analyst.backtest.portfolio import VirtualPortfolio
    from financial_analyst.backtest.costs import CostModel  # noqa: F401
    from financial_analyst.backtest import limits
    from financial_analyst.backtest.engine import (  # noqa: F401
        BacktestRunner, RunConfig, BacktestResult)
    from financial_analyst.backtest.pit_reader import PitReader

    assert hasattr(Broker, "match")
    assert hasattr(VirtualPortfolio, "seed_initial_nav")
    assert hasattr(VirtualPortfolio, "record_nav")
    for fn in ("limit_pct_for", "compute_ref_prev_close", "is_one_word"):
        assert hasattr(limits, fn), f"engine limits lost {fn}"
    for m in ("get_visible_info", "trading_days", "fetch_bars_intraday"):
        assert hasattr(PitReader, m), f"PitReader lost {m}"


def test_point7_engine_module_byte_digests_are_the_untouched_baseline():
    # the Task 5/8 engine-untouched comparison baseline: each backtest module's
    # on-disk sha256 must equal its frozen value (a drift flags a touched engine).
    backtest = _engine_backtest_dir()
    for name, frozen in ENGINE_MODULE_BYTE_DIGESTS.items():
        raw = (backtest / name).read_bytes()
        assert hashlib.sha256(raw).hexdigest() == frozen, \
            f"engine module {name} byte digest drifted from the baseline"


def test_point7_datafeed_live_client_exports_the_facade():
    lc = importlib.import_module("guanlan_v2.datafeed.live_client")
    for fn in ("probe", "catalog", "resolve_source", "known_sources"):
        assert hasattr(lc, fn), f"datafeed.live_client lost {fn}"


# =========================================================================== #
# Point 8 — the three legacy entry points still exist at their grounded seams.   #
# =========================================================================== #
def test_point8_console_background_seams_still_present():
    # console/api.py is FOREIGN-DIRTY (concurrent session) — read-only inspection.
    # _spawn_bg / _run_report_bg are NESTED inside the router mount, so bind by
    # source-text presence (never import the dirty router).
    src = _read_text(REPO_ROOT / "guanlan_v2" / "console" / "api.py")
    for seam in ("def _call_buddy_report", "def _spawn_bg", "def _run_report_bg"):
        assert seam in src, f"console legacy seam removed: {seam}"


def test_point8_engine_swarm_load_preset_and_its_cli_tui_consumers():
    from financial_analyst.swarm import loader as swarm_loader
    assert callable(swarm_loader.load_preset)
    # the cli/tui consumers live at the engine package root (NOT under swarm/).
    engine_root = Path(swarm_loader.__file__).resolve().parents[1]
    for consumer in ("cli.py", "tui.py"):
        text = _read_text(engine_root / consumer)
        assert "load_preset" in text, f"engine {consumer} no longer consumes load_preset"


def test_point8_research_loop_start_route_and_runner():
    rloop = importlib.import_module("guanlan_v2.research.loop")
    assert callable(rloop.run_research_loop)
    api_src = _read_text(REPO_ROOT / "guanlan_v2" / "research" / "api.py")
    assert "/research/loop/start" in api_src
    assert "run_research_loop" in api_src


# =========================================================================== #
# Point 9 — no Phase-9 path shadows a Phase 1-8 owned module/test/golden.        #
# =========================================================================== #
def test_point9_phase1_8_owned_modules_and_goldens_exist_singular():
    for path in PHASE1_8_OWNED_MODULES + UPSTREAM_GOLDEN_FILES + (PHASE6_ADAPTERS_LUOZI,):
        assert path.is_file() and path.stat().st_size > 0, f"missing upstream file: {path}"
    assert Path(__file__).name == "test_phase9_handoff.py"


def test_point9_phase9_paths_never_shadow_owned_or_golden_files():
    owned = set(PHASE1_8_OWNED_MODULES) | set(UPSTREAM_GOLDEN_FILES) | {PHASE6_ADAPTERS_LUOZI}
    for path in PHASE9_NEW_PATHS:
        assert path not in owned, (
            f"Phase 9 path {path} shadows a Phase 1-8 owned/golden file — Phase 9 is "
            "additive and may never overwrite an upstream source or golden")
    for golden in (GOLDEN_DIR / "phase9_schema_manifest_v1.json",
                   GOLDEN_DIR / "phase9_catalog_manifest_v1.json"):
        assert golden.name.startswith("phase9_")
    # adapters/luozi.py is EXTENDED by Phase 9, never replaced — it stays owned.
    assert PHASE6_ADAPTERS_LUOZI not in PHASE9_NEW_PATHS


# =========================================================================== #
# C4 — Phase-3 data builders + the TYPED memory facade (name-authoritative).     #
# =========================================================================== #
def test_clause_c4_data_builders_and_reader_protocol_resolve():
    from guanlan_v2.orchestration.data.registry import (
        DataSnapshotManifest, DataRoutingSnapshot)  # noqa: F401
    from guanlan_v2.orchestration.data.snapshot import DataSourceConfigSnapshot  # noqa: F401
    from guanlan_v2.orchestration.data.runtime import DataSourceRegistry  # noqa: F401
    from guanlan_v2.orchestration.data.reader import DataReader
    for m in ("get_ohlcv", "get_indicators", "get_verified_snapshot",
              "get_fundamentals", "get_news", "get_signal"):
        assert hasattr(DataReader, m), f"DataReader lost method {m}"


def test_clause_c4_memory_facade_prepare_pit_replay_landed_typed():
    # C4: the verbatim brief quote was ``prior_context_ref: PayloadRef``; the
    # IMPLEMENTED facade landed Amendment-1-consistent TYPED — Task 4 binds the
    # typed form, never invents the bare-locator variant.
    from guanlan_v2.orchestration.memory.store import MemoryContextPreparationService
    sig = inspect.signature(MemoryContextPreparationService.prepare_pit_replay)
    assert list(sig.parameters) == [
        "self", "data_context", "authority", "prior_context_ref"]
    ann = sig.parameters["prior_context_ref"].annotation
    assert ann in ("TypedPayloadRef", TypedPayloadRef), \
        f"prepare_pit_replay.prior_context_ref is {ann!r}, expected TypedPayloadRef"


# =========================================================================== #
# C6 — the seats/autonomy seams Tasks 4/6/10 extend (verified against code).      #
# =========================================================================== #
def test_clause_c6_seats_and_autonomy_seams_exist_at_reviewed_names():
    seats_api = importlib.import_module("guanlan_v2.seats.api")
    assert hasattr(seats_api, "_persist_decision")
    assert hasattr(seats_api, "_LEDGER_LOG")
    seats_watcher = importlib.import_module("guanlan_v2.seats.watcher")
    assert hasattr(seats_watcher, "load_state")
    assert hasattr(seats_watcher, "save_state")
    autonomy_runtime = importlib.import_module("guanlan_v2.autonomy.runtime")
    assert hasattr(autonomy_runtime, "maybe_enqueue_daily_review")
    autonomy_playbooks = importlib.import_module("guanlan_v2.autonomy.playbooks")
    assert isinstance(autonomy_playbooks.PLAYBOOKS, dict)
    # Task 4 LANDED its two additive watcher seams (note_external_llm_use +
    # orchestrated_codes) — the gate now records them as present. Task 6 LANDED its two
    # autonomy scheduler seams (maybe_enqueue_shadow_wakeup + maybe_enqueue_lane0_bootstrap);
    # this "does-not-exist-yet" gate flips to present as each seam lands (the Task-4 C6
    # precedent — an additive gate flip, never a weakened assertion).
    assert hasattr(seats_watcher, "note_external_llm_use")
    assert hasattr(seats_watcher, "orchestrated_codes")
    assert hasattr(autonomy_runtime, "maybe_enqueue_shadow_wakeup")
    assert hasattr(autonomy_runtime, "maybe_enqueue_lane0_bootstrap")
