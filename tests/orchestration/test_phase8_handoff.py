# -*- coding: utf-8 -*-
"""Task 0 — the executable Phase 2/3/4/5/6/7 -> Phase 8 (四车道目录 / skills /
Lane D 有界辩论) handoff gate.

This is a *consumer* gate, not a red->green feature task: it imports the frozen
Phase-1 (Amendment-1) contract layer, the Phase-2 kernel, the Phase-3 data +
memory facade, the Phase-4 Evaluator-Optimizer, the Phase-5 Bootstrap Lane-0
chain, the Phase-6 shadow-consumer chain, and the Phase-7 cumulative
registry/catalog chain exactly as the Phase-8 batches (skills tree, capability
manifest, 20-new/5-updated WorkerSpecs, Lane-D bounded debate) will, exercises
the real registries / builders / services / digests through their public
surfaces, and asserts the upstream ABI Phase 8 consumes is intact and singular.
Every assertion PASSES against the already-implemented upstream code on branch
``report-evidence-pack``. A failure means an upstream Phase 1-7 contract drifted
from its frozen exit gate and BLOCKS Phase 8 — it must NEVER be "fixed" by
weakening an assertion or editing upstream; fix the owning layer. Do not
regenerate a golden.

It proves the nine points of ``.superpowers/sdd/task-0-brief.md`` Step 1:

1.  the Phase-7 cumulative registry/catalog chain resolves
    (``PHASE7_REGISTRY_DIGEST`` / ``build_phase7_registry`` /
    ``PHASE7_CATALOG_DIGEST`` / ``build_phase7_catalog_snapshot``), its goldens
    pass, and the chain verifies back through Phase 6/5/4 to
    ``PHASE3_FULL_REGISTRY_DIGEST`` / ``PHASE3_FULL_CATALOG_DIGEST``.
2.  the Phase-7 catalog already contains as ``catalog_role="final"`` the 3 Phase-2
    pilots + the 3 Phase-5 Lane-0 workers + the Phase-5-assembled #25
    ``market.factor_miner`` (``count_final_workers == 7``); their exact WorkerSpec
    semantic digests are recorded as the pre-update baseline (clause (d)).
3.  Phase-1 debate contracts are intact and unchanged: ``DebateCfg`` (seats /
    turn_order-permutation / max_rounds / judge validators), the PlanNode
    all-set-or-all-none debate identity fields, and the six plan-level debate
    issue codes.
4.  skill-v1 grammar is intact: ``parse_skill_v1`` / ``SkillManifest`` /
    ``SkillFormatError`` / ``_CRITICAL_HEADING_LINE`` and canonical-JSON trigger
    enforcement all resolve from ``catalog.py``.
5.  Phase 6 exports ``PortfolioTargetProposal`` and ``TargetPortfolioIntent`` as
    registered ``@1`` schemas, and the runtime intent envelope is service-owned:
    the LLM-writable surface is the 5-field proposal; the intent adds 16
    runtime-only facts no worker payload can author, so no PUBLIC constructor a
    WORKER PAYLOAD could satisfy exists (``wrap_proposal_as_intent`` is the sole
    proposal->intent staging path).
6.  Phase-1 migration adapters + their frozen verdicts hold against the frozen
    ``legacy_contract_samples.json`` (14 scalars, 24 planned workers, 1 graph):
    ``migrate_rating`` UNMAPPABLE, ``migrate_confidence("med")`` UNMAPPABLE, all
    sentiment sources UNMAPPABLE, ``migrate_rotation_stage`` UNMAPPABLE (its
    implemented Phase-5 status), and every reverse returns the exact original raw.
7.  ``BudgetScopeType`` already contains ``"schema_repair"`` / ``"retry"`` and
    ``TextMaterialKind`` already contains ``"reducer"`` / ``"gate_metric"`` — Phase 8
    needs no vocabulary change there.
8.  the Phase-2 ``StaticRuntimeProfile`` v1 and the Phase-5 BOOTSTRAP profile
    resolve; BOOTSTRAP is ``profile_id="bootstrap-runtime"`` / version ``"1"`` — a
    ``profile_id`` distinct from ``static-runtime`` so ``"2"`` is free (clause (f));
    both identity pairs + profile digests are recorded.
9.  no Phase-8 source/test/golden path shadows a Phase 1-7 owned module, test, or
    golden.

------------------------------------------------------------------------------
TASK-0 CORRECTION CLAUSES resolved against the real code (BINDING on Tasks 0b-12;
name-authoritative, never line-authoritative):

* **(a) Upstream chain symbols.** The implemented registry chain matches the CRIB
  4.5 recursion by name: ``PHASE<N>_REGISTRY_DIGEST`` /
  ``build_phase<N>_registry(expected_phase<N-1>_digest)`` /
  ``Phase<N>RegistryError`` for N=4..7, and the lazy ``PHASE<N>_CATALOG_DIGEST`` +
  canonical ``phase<N>_catalog_snapshot()`` accessors. The **catalog builders are
  heterogeneous by keyword** and NOT a uniform ``expected_phase<N-1>_digest``
  recursion: P4 ``build_phase4_catalog_snapshot(base, *, joint_gate_material,
  resolved_materials)``, P5 ``(base, *, lane0, factor_miner)``, P6 ``(base, *,
  expected_phase5_digest)`` (the identity node), P7
  ``build_phase7_catalog_snapshot(phase6_snapshot, *, planner_materials)``. Phase 8
  therefore consumes the chain through the **lazy digests + canonical
  ``phase<N>_catalog_snapshot()`` accessors** (signature-independent), never by
  assuming a uniform catalog-builder kwarg.
* **(b) EventType guards.** The current reviewed shape is ``len(EventType) == 25``
  (Phase 6 added 2, Phase 7 added none) and ``DebateMessage`` is STILL a pure
  Phase-1-absence guard in ``test_contract_completeness.DEFERRED_PHASE_PAYLOADS``
  (no owning-phase registry). Phase-8 Task 9/11 will additively add
  ``DEBATE_MESSAGE_PUBLISHED`` and flip ``DebateMessage`` — this gate records the
  pre-flip shape; the flip EXTENDS the then-current reviewed set, never restores an
  older one.
* **(c) Phase-3 data capability ids.** The six plan-ABI intents bind to the exact
  implemented ``CapabilityRef`` ids (all version ``"1"``), keyed by method_id via
  ``data.catalog.data_capability_refs()``: ``get_news``->``cap.data.news``,
  ``get_ohlcv``->``cap.data.ohlcv``, ``get_indicators``->``cap.data.indicators``,
  ``get_verified_snapshot``->``cap.data.verified_snapshot``,
  ``get_fundamentals``->``cap.data.fundamentals``,
  ``get_signal``->``cap.data.signals`` (reader method ``get_signal`` /
  method_id ``signals``). Later tasks bind these ids, never the semantics.
* **(d) Pilot WorkerSpec baselines.** The 3 pilots' + 3 Lane-0 + #25 implemented
  WorkerSpec semantic digests are frozen below as the pre-update baseline; Task
  4/10 reviewed updates change only the fields each task names (a no-op is a
  recorded no-op).
* **(e) Lane-0 skill materials.** Phase 5 placed the Lane-0 SKILL.md sources
  OUTSIDE ``guanlan_v2/orchestration/skills/`` (which does not exist yet) — they
  live under ``config/orchestration/materials/lane0/`` as
  ``regime_skill.md`` / ``rotation_skill.md`` / ``factor_miner_skill.md``. Task 1
  MUST relocate these three into the ``guanlan_v2/orchestration/skills/<worker>/``
  tree with byte-identical (digest-preserving) content — it is NOT a no-op.
* **(f) Profile version strings — resolved, not an open collision.** Phase 5 minted
  a distinct identity ``BootstrapRuntimeProfile``: ``profile_id="bootstrap-runtime"``
  / ``profile_version="1"`` (Option-4). This plan keeps ``profile_id="static-runtime"``
  and mints ``profile_version="2"`` (``STATIC_RUNTIME_PROFILE_V2``); v1 + BOOTSTRAP
  constants are never edited. ``bootstrap-runtime`` is a ``profile_id`` distinct
  from ``static-runtime``, so ``"2"`` is free.
* **(g) llm.yaml values.** ``config/llm.yaml`` at implementation time has the sole
  live provider ``deepseek`` (default ``deepseek``/``deepseek-chat``; ``deepseek-reasoner``
  the reasoning model); ``kimi`` is domestic-only, ``anthropic``/``openai`` are
  dormant (no key, no override). Task 3's ``orchestration-fast/-reasoner/
  -reasoner-deep`` aliases therefore bind ``deepseek``/``deepseek-chat`` (fast) and
  ``deepseek``/``deepseek-reasoner`` (reasoner + reasoner-deep) — never a vendor
  absent from the file.
* **(h) Legacy pure functions.** ``compute_pa_features`` lives in
  ``guanlan_v2.seats.price_action`` with only ``import math`` at module top and a
  ``(df, code="", name="") -> dict`` signature; it is import-safe and
  side-effect-free (纯函数、零 I/O), so the deterministic handler binds it directly
  as an import-safe pure function — the impure fallback (consume its typed output
  as a declared input artifact) is NOT needed.

Run from repo root: ``python -m pytest tests/orchestration/test_phase8_handoff.py -v``
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
from guanlan_v2.orchestration.refs import PayloadRef, SchemaRef
from guanlan_v2.orchestration.schema_registry import default_registry
from guanlan_v2.orchestration.context import BudgetScopeType
from guanlan_v2.orchestration.events import EventType
from guanlan_v2.orchestration.spec import DebateCfg, PlanNode
from guanlan_v2.orchestration import spec as spec_mod

# -- Phase 1 catalog (skill-v1 grammar + text-material vocabulary) ----------- #
from guanlan_v2.orchestration.catalog import (
    SkillFormatError,
    SkillManifest,
    TextMaterialKind,
    _CRITICAL_HEADING_LINE,
    canonical_json,
    count_final_workers,
    parse_skill_v1,
)

# -- Phase 3 data capability ids (clause c) ---------------------------------- #
from guanlan_v2.orchestration.data.catalog import data_capability_refs

# -- Phase 3 full (data + memory) chain root --------------------------------- #
from guanlan_v2.orchestration.memory import schema_registry as mem_reg
from guanlan_v2.orchestration.memory import catalog as mem_cat

# -- Phase 4 / 5 / 6 / 7 chain ----------------------------------------------- #
from guanlan_v2.orchestration import trial
from guanlan_v2.orchestration import bootstrap
from guanlan_v2.orchestration import shadow
from guanlan_v2.orchestration import phase7_registry as p7

# -- Phase 6 portfolio-target / intent envelope (point 5) -------------------- #
from guanlan_v2.orchestration.shadow import (
    PortfolioTargetProposal,
    TargetPortfolioIntent,
    wrap_proposal_as_intent,
)

# -- Phase 2 static runtime profile + Phase 5 BOOTSTRAP profile (point 8) ---- #
from guanlan_v2.orchestration.runtime_contracts import (
    StaticRuntimeProfile,
    static_runtime_profile,
)
from guanlan_v2.orchestration.bootstrap import (
    BootstrapRuntimeProfile,
    bootstrap_runtime_profile,
)

# -- Phase 1 migration adapters (point 6) ------------------------------------ #
from guanlan_v2.orchestration import migration as M
from guanlan_v2.orchestration.migration import MappingStatus

# -- clause (h): the legacy price-action pure function ----------------------- #
from guanlan_v2.seats.price_action import compute_pa_features


# --------------------------------------------------------------------------- #
# Paths                                                                        #
# --------------------------------------------------------------------------- #
TESTS_DIR = Path(__file__).resolve().parent
GOLDEN_DIR = TESTS_DIR / "golden"
FIXTURES_DIR = TESTS_DIR / "fixtures"
ORCH_DIR = Path(orch_pkg.__file__).resolve().parent
REPO_ROOT = ORCH_DIR.parent.parent
CONFIG_DIR = REPO_ROOT / "config"
LANE0_MATERIALS_DIR = CONFIG_DIR / "orchestration" / "materials" / "lane0"
LEGACY_FIXTURE = FIXTURES_DIR / "legacy_contract_samples.json"
LLM_YAML = CONFIG_DIR / "llm.yaml"

D64 = "0" * 64


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _params(func) -> list[str]:
    return list(inspect.signature(func).parameters)


# =========================================================================== #
# Step 2 — reviewed upstream evidence recorded verbatim (branch HEAD dd5e4f4). #
# Each digest is re-derived from a sealed builder / canonical accessor below;   #
# none is a local path or a mutable singleton identity, and none is regenerated #
# from test code.                                                              #
# =========================================================================== #

# Phase-3 FULL (data + memory) chain root the Phase-4 base pins to.
PHASE3_FULL_REGISTRY_DIGEST = (
    "ff2a56d283499e347dcdbf4c2f5c5977710fd5575afbc94ee34819b2b531e49d"
)
PHASE3_FULL_CATALOG_DIGEST = (
    "c13294e5f020de542cc553d92e509d3dfea45673700373ac0c013d9115c3a773"
)

# Cumulative registry chain digests (order-independent, sealed per node).
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

# Cumulative catalog chain digests (P6 is the identity node: P6 == P5).
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

#: The Phase-7 cumulative registry entry count (Phase 8 additively grows it).
PHASE7_REGISTRY_ENTRY_COUNT = 164

#: point 2 / clause (d): the SEVEN Phase-7 ``catalog_role="final"`` workers and
#: their exact WorkerSpec semantic digests — the pre-update Phase-8 baseline.
FINAL_WORKER_DIGESTS: dict[str, str] = {
    "dec.pm": "5557bd4a351eaa7bebc93c5d08b4b8d18f35072915acd5ff9de22c224a1c9797",
    "dec.research_mgr": "8a8d49b4bf9151eb234a911849797497a2b26b87aa46a4d36aaf7c194546964e",
    "market.factor": "c20c62f9cd90e0963b126b1b992d42689674675dc2aef5a4634e9f2999ff5148",
    "market.factor_miner": "e0da877ba6f3a0458f431a280684b5a173bd2e3f3b7dd4c6465995c0ebd8828b",
    "market.regime": "4e1a747870f3dc79331efa32e6421b9f508655e0dbaa61c4eb2218da9cf22292",
    "market.rotation": "30e9c27d379c244e4529bf8486926f2bf6e2ce08d8162d8f143de4bfa5a14d40",
    "text.sentiment": "df636a963d55c393ac9b3f3d3ed11e434e556d37cc6391d88f67fe1384bebfda",
}
#: the 3 pilots + 3 Lane-0 + #25 partition of the final-7 (clause (d) provenance).
PILOT_WORKER_IDS = frozenset({"text.sentiment", "dec.research_mgr", "dec.pm"})
LANE0_WORKER_IDS = frozenset({"market.factor", "market.regime", "market.rotation"})
MINER_WORKER_ID = "market.factor_miner"  # #25, assembled in Phase 5

#: point 8 / clause (f): the two profile identity pairs + frozen profile digests.
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

#: clause (c): the six plan-ABI intents -> exact implemented CapabilityRef ids.
DATA_CAPABILITY_IDS: dict[str, str] = {
    "get_news": "cap.data.news",
    "get_ohlcv": "cap.data.ohlcv",
    "get_indicators": "cap.data.indicators",
    "get_verified_snapshot": "cap.data.verified_snapshot",
    "get_fundamentals": "cap.data.fundamentals",
    "get_signal": "cap.data.signals",
}
#: plan-ABI intent -> the implemented method_id it binds through.
_INTENT_TO_METHOD_ID = {
    "get_news": "news",
    "get_ohlcv": "ohlcv",
    "get_indicators": "indicators",
    "get_verified_snapshot": "verified_snapshot",
    "get_fundamentals": "fundamentals",
    "get_signal": "signals",
}

#: point 6: the frozen legacy-contract fixture identity + design-intent counts.
LEGACY_FIXTURE_SHA256 = (
    "6afe090c38bbe931496bafe4effdca26b79b340a3fb5d5fbff8af232669cf131"
)
LEGACY_FIXTURE_SCALARS = 14
LEGACY_FIXTURE_WORKERS = 24  # frozen design-intent map (live final count is 27, D5)
LEGACY_FIXTURE_GRAPHS = 1

#: point 3: the six plan-level debate issue codes (spec.py structural validator).
DEBATE_ISSUE_CODES = frozenset({
    "debate_missing_judge",
    "undefined_debate",
    "debate_role_not_seat",
    "debate_round_out_of_range",
    "duplicate_debate_turn",
    "duplicate_debate_role",
})


# --------------------------------------------------------------------------- #
# Phase 1-7 owned files Phase 8 must never overwrite (point 9)                 #
# --------------------------------------------------------------------------- #
PHASE1_7_OWNED_MODULES = tuple(
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
    )
)
UPSTREAM_GOLDEN_FILES = tuple(
    GOLDEN_DIR / name
    for name in (
        "schema_manifest_v1.json", "digest_vectors_v1.json",
        "phase3_full_schema_manifest_v1.json", "phase3_full_catalog_manifest_v1.json",
        "phase4_schema_manifest_v1.json", "phase4_catalog_manifest_v1.json",
        "phase5_schema_manifest_v1.json", "phase5_catalog_manifest_v1.json",
        "phase6_schema_manifest_v1.json", "phase6_catalog_manifest_v1.json",
        "phase7_schema_manifest_v1.json", "phase7_catalog_manifest_v1.json",
    )
)

#: The Phase-8 module/golden paths the plan will create task-by-task (from the
#: plan's File Structure table). The durable point-9 guarantee: none of them ever
#: *shadows* (equals) a Phase 1-7 owned source or golden.
PHASE8_NEW_PATHS = (
    ORCH_DIR / "skilltree.py",
    ORCH_DIR / "capability_manifest.py",
    ORCH_DIR / "pattern_registry.py",
    ORCH_DIR / "decision_inputs.py",
    ORCH_DIR / "honesty.py",
    ORCH_DIR / "model_tiers.py",
    ORCH_DIR / "lane_payloads.py",
    ORCH_DIR / "lane_catalog.py",
    ORCH_DIR / "debate.py",
    GOLDEN_DIR / "phase8_schema_manifest_v1.json",
    GOLDEN_DIR / "phase8_catalog_manifest_v1.json",
    TESTS_DIR / "test_phase8_handoff.py",
)


# =========================================================================== #
# Point 1 — the Phase-7 chain resolves + goldens pass + verifies back to P3.    #
# =========================================================================== #
def test_point1_phase7_chain_symbols_resolve_and_reproduce():
    # symbols exist with the reviewed shapes.
    assert callable(p7.build_phase7_registry)
    assert callable(p7.build_phase7_catalog_snapshot)
    assert callable(p7.phase7_catalog_snapshot)

    reg = p7.build_phase7_registry(p7.PHASE7_BASE_REGISTRY_DIGEST)
    assert reg.sealed
    assert reg.registry_digest == p7.PHASE7_REGISTRY_DIGEST == PHASE7_REGISTRY_DIGEST
    assert len(reg.manifest()) == PHASE7_REGISTRY_ENTRY_COUNT

    snap = p7.phase7_catalog_snapshot()
    assert snap.catalog_digest == p7.PHASE7_CATALOG_DIGEST == PHASE7_CATALOG_DIGEST

    # a wrong base is rejected before any registration.
    with pytest.raises(p7.Phase7RegistryError):
        p7.build_phase7_registry(D64)


def test_point1_chain_verifies_back_through_p6_p5_p4_to_p3_full():
    # each node's declared base == its predecessor's sealed digest.
    assert p7.PHASE7_BASE_REGISTRY_DIGEST == shadow.PHASE6_REGISTRY_DIGEST == PHASE6_REGISTRY_DIGEST
    assert shadow.PHASE6_BASE_REGISTRY_DIGEST == bootstrap.PHASE5_REGISTRY_DIGEST == PHASE5_REGISTRY_DIGEST
    assert bootstrap.PHASE5_BASE_REGISTRY_DIGEST == trial.PHASE4_REGISTRY_DIGEST == PHASE4_REGISTRY_DIGEST
    assert trial.PHASE4_BASE_REGISTRY_DIGEST == mem_reg.PHASE3_FULL_REGISTRY_DIGEST == PHASE3_FULL_REGISTRY_DIGEST

    assert p7.PHASE7_BASE_CATALOG_DIGEST == shadow.PHASE6_CATALOG_DIGEST == PHASE6_CATALOG_DIGEST
    assert shadow.PHASE6_BASE_CATALOG_DIGEST == bootstrap.PHASE5_CATALOG_DIGEST == PHASE5_CATALOG_DIGEST
    assert bootstrap.PHASE5_BASE_CATALOG_DIGEST == trial.PHASE4_CATALOG_DIGEST == PHASE4_CATALOG_DIGEST
    assert trial.PHASE4_BASE_CATALOG_DIGEST == mem_cat.PHASE3_FULL_CATALOG_DIGEST == PHASE3_FULL_CATALOG_DIGEST

    # the four cumulative registry nodes are genuinely distinct.
    assert len({PHASE4_REGISTRY_DIGEST, PHASE5_REGISTRY_DIGEST,
                PHASE6_REGISTRY_DIGEST, PHASE7_REGISTRY_DIGEST}) == 4


def test_point1_phase7_goldens_load_and_record_the_chain():
    s = _load_json(GOLDEN_DIR / "phase7_schema_manifest_v1.json")
    assert s["algorithm"] == CJSON_VERSION
    assert len(s["entries"]) == PHASE7_REGISTRY_ENTRY_COUNT
    assert s["registry_digest"] == PHASE7_REGISTRY_DIGEST
    assert s["base_registry_digest"] == PHASE6_REGISTRY_DIGEST

    c = _load_json(GOLDEN_DIR / "phase7_catalog_manifest_v1.json")
    assert c["result_catalog_digest"] == PHASE7_CATALOG_DIGEST
    assert c["base_catalog_digest"] == PHASE6_CATALOG_DIGEST


def test_point1_phase7_catalog_builder_rejects_a_non_canonical_base():
    from guanlan_v2.orchestration.catalog import CatalogError
    # feeding the Phase-5 (== Phase-6 identity) snapshot is canonical; feeding the
    # Phase-4 snapshot (a different digest) must be rejected.
    with pytest.raises(CatalogError):
        p7.build_phase7_catalog_snapshot(
            trial.phase4_catalog_snapshot(),
            planner_materials=p7.load_planner_materials(),
        )


# =========================================================================== #
# Point 2 / clause (d) — the seven final workers + their WorkerSpec baselines.  #
# =========================================================================== #
def test_point2_phase7_catalog_has_exactly_seven_final_workers():
    snap = p7.phase7_catalog_snapshot()
    assert count_final_workers(snap) == 7
    finals = {w.id for w in snap.workers if w.catalog_role == "final"}
    assert finals == set(FINAL_WORKER_DIGESTS)
    # the 3 pilots + 3 Lane-0 + #25 partition.
    assert PILOT_WORKER_IDS | LANE0_WORKER_IDS | {MINER_WORKER_ID} == finals
    assert len(PILOT_WORKER_IDS) == 3 and len(LANE0_WORKER_IDS) == 3


def test_point2_final_worker_semantic_digests_are_the_frozen_baseline():
    snap = p7.phase7_catalog_snapshot()
    got = {
        w.id: w.semantic_digest()
        for w in snap.workers
        if w.catalog_role == "final"
    }
    assert got == FINAL_WORKER_DIGESTS


# =========================================================================== #
# Point 3 — Phase-1 debate contracts intact and unchanged.                      #
# =========================================================================== #
def test_point3_debate_cfg_seats_permutation_max_rounds_judge_validators():
    assert DebateCfg.__module__ == "guanlan_v2.orchestration.spec"
    good = DebateCfg(
        id="deb-1", seats=("bull", "bear"), turn_order=("bear", "bull"),
        max_rounds=2, judge_node_id="judge")
    assert set(good.turn_order) == set(good.seats)
    # turn_order must be a permutation of seats.
    with pytest.raises(ValueError):
        DebateCfg(id="deb-1", seats=("bull", "bear"), turn_order=("bull", "bull"),
                  max_rounds=2, judge_node_id="judge")
    # seats must be non-empty + unique.
    with pytest.raises(ValueError):
        DebateCfg(id="deb-1", seats=("bull", "bull"), turn_order=("bull",),
                  max_rounds=1, judge_node_id="judge")
    # max_rounds is a PositiveInt.
    with pytest.raises(ValueError):
        DebateCfg(id="deb-1", seats=("bull",), turn_order=("bull",),
                  max_rounds=0, judge_node_id="judge")


def test_point3_plan_node_debate_fields_are_all_set_or_all_none():
    base = dict(id="n1", worker_id="w1", writes_slot="s1")
    # all-none is valid.
    PlanNode(**base)
    # all-set is valid.
    PlanNode(**base, debate_id="d1", round_role="bull", debate_round=1, debate_turn=1)
    # a partial debate identity is rejected.
    with pytest.raises(ValueError):
        PlanNode(**base, debate_id="d1")
    with pytest.raises(ValueError):
        PlanNode(**base, debate_id="d1", round_role="bull", debate_round=1)


def test_point3_six_plan_level_debate_issue_codes_are_referenced_in_spec():
    src = inspect.getsource(spec_mod)
    for code in DEBATE_ISSUE_CODES:
        assert f'"{code}"' in src, f"spec.py lost debate issue code {code!r}"
    assert len(DEBATE_ISSUE_CODES) == 6


# =========================================================================== #
# Point 4 — skill-v1 grammar intact (parse + manifest + canonical trigger).     #
# =========================================================================== #
def test_point4_skill_v1_symbols_and_critical_heading_resolve_from_catalog():
    assert parse_skill_v1.__module__ == "guanlan_v2.orchestration.catalog"
    assert SkillManifest.__module__ == "guanlan_v2.orchestration.catalog"
    assert issubclass(SkillFormatError, Exception)
    assert _CRITICAL_HEADING_LINE == "## ⚠️ CRITICAL: Data Source Priority"


def _skill_doc(trigger_perfect: str) -> str:
    return (
        "---\n"
        "name: demo\n"
        "description: |\n"
        "  A demo skill summary line.\n"
        f"  Perfect for: {trigger_perfect}\n"
        '  Not ideal for: ["c"]\n'
        "---\n"
        "## ⚠️ CRITICAL: Data Source Priority\n"
        "- ww_news_live first\n"
    )


def test_point4_canonical_json_trigger_enforcement_is_intact():
    # a canonical trigger array parses.
    parsed = parse_skill_v1(_skill_doc(canonical_json(["a", "b"])))
    assert parsed.perfect_for == ("a", "b")
    assert parsed.critical_data_source_heading in _CRITICAL_HEADING_LINE
    # a valid-JSON-but-non-canonical trigger array is rejected.
    assert canonical_json(["a", "b"]) != '["a", "b"]'
    with pytest.raises(SkillFormatError):
        parse_skill_v1(_skill_doc('["a", "b"]'))


# =========================================================================== #
# Point 5 — proposal + intent registered; intent envelope is service-owned.     #
# =========================================================================== #
def test_point5_proposal_and_intent_registered_at_1():
    reg = p7.build_phase7_registry(p7.PHASE7_BASE_REGISTRY_DIGEST)
    keys = {e.key for e in reg.manifest()}
    assert "PortfolioTargetProposal@1" in keys
    assert "TargetPortfolioIntent@1" in keys


def test_point5_intent_envelope_has_no_worker_payload_constructor():
    # the LLM-writable surface is the proposal: exactly 5 fields, all authorable.
    proposal_fields = set(PortfolioTargetProposal.model_fields)
    assert proposal_fields == {
        "schema_version", "positions", "cash_weight", "rationale", "confidence"}
    # the intent adds 16 runtime-only facts a worker payload can never author
    # (schedule triple, three ruled instants, proposal identity + closed Literals).
    intent_only = set(TargetPortfolioIntent.model_fields) - proposal_fields
    for runtime_fact in (
        "origin", "authority", "execution_scope", "proposal_artifact_id",
        "proposal_digest", "decision_schedule_id", "scheduled_for",
        "decision_as_of", "eligible_execution_at",
    ):
        assert runtime_fact in intent_only
    # the closed single-value Literals make "advisory / shadow-only / LLM" structural.
    for field, allowed in (
        ("origin", "LLM"),
        ("authority", "ADVISORY_ONLY"),
        ("execution_scope", "SHADOW_ONLY"),
    ):
        ann = TargetPortfolioIntent.model_fields[field].annotation
        assert typing.get_args(ann) == (allowed,)
    # the sole proposal->intent staging path is service-owned in shadow.py.
    assert wrap_proposal_as_intent.__module__ == "guanlan_v2.orchestration.shadow"
    assert "proposal_artifact" in _params(wrap_proposal_as_intent)


# =========================================================================== #
# Point 6 — Phase-1 migration adapters + frozen UNMAPPABLE verdicts hold.        #
# =========================================================================== #
def test_point6_legacy_fixture_identity_and_design_intent_counts():
    raw = LEGACY_FIXTURE.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == LEGACY_FIXTURE_SHA256
    doc = json.loads(raw.decode("utf-8"))
    assert len(doc["scalars"]) == LEGACY_FIXTURE_SCALARS
    assert len(doc["workers"]) == LEGACY_FIXTURE_WORKERS
    assert len(doc["graphs"]) == LEGACY_FIXTURE_GRAPHS


def test_point6_rating_and_confidence_are_unmappable_and_reverse_exactly():
    r = M.migrate_rating(5, source_schema=M.SRC_REPORT_OUTPUT)
    assert r.mapping_status is MappingStatus.UNMAPPABLE and r.normalized is None
    assert M.rating_to_legacy(r) == 5 and type(M.rating_to_legacy(r)) is int

    m = M.migrate_confidence("med", source_schema=M.SRC_INTROSPECTION_PROPOSAL)
    assert m.mapping_status is MappingStatus.UNMAPPABLE and m.normalized is None
    assert M.confidence_to_legacy(m) == "med"


def test_point6_all_sentiment_sources_are_unmappable_and_reverse_exactly():
    scaled = M.migrate_sentiment(1.0, source_schema=M.SRC_TAG_SCORE, scale="pm1")
    temp = M.migrate_sentiment(50.0, source_schema=M.SRC_ASTOCK_TEMP)
    categorical = M.migrate_sentiment("利好", source_schema=M.SRC_NEWS_SENTIMENT)
    for mig, raw in ((scaled, 1.0), (temp, 50.0), (categorical, "利好")):
        assert mig.mapping_status is MappingStatus.UNMAPPABLE
        assert mig.normalized is None
        assert M.sentiment_to_legacy(mig) == raw


def test_point6_rotation_stage_is_unmappable_per_its_phase5_status():
    r = M.migrate_rotation_stage("冰点", source_schema=M.SRC_MARKET_CYCLE)
    assert r.mapping_status is MappingStatus.UNMAPPABLE and r.normalized is None
    assert M.rotation_stage_to_legacy(r) == "冰点"


# =========================================================================== #
# Point 7 — the existing budget/text vocabulary already covers Phase 8.         #
# =========================================================================== #
def test_point7_budget_and_text_material_vocabulary_needs_no_change():
    scope = set(typing.get_args(BudgetScopeType))
    assert {"schema_repair", "retry"} <= scope
    text_kinds = set(typing.get_args(TextMaterialKind))
    assert {"reducer", "gate_metric"} <= text_kinds


# =========================================================================== #
# Point 8 / clause (f) — both runtime profiles resolve with distinct identities. #
# =========================================================================== #
def test_point8_static_and_bootstrap_profiles_resolve_with_distinct_identity():
    srp = static_runtime_profile()
    assert isinstance(srp, StaticRuntimeProfile)
    assert srp.profile_id == STATIC_PROFILE_ID == "static-runtime"
    assert srp.profile_version == STATIC_PROFILE_VERSION == "1"
    assert srp.profile_digest == STATIC_PROFILE_DIGEST

    brp = bootstrap_runtime_profile()
    assert isinstance(brp, BootstrapRuntimeProfile)
    assert brp.profile_id == BOOTSTRAP_PROFILE_ID == "bootstrap-runtime"
    assert brp.profile_version == BOOTSTRAP_PROFILE_VERSION == "1"
    assert brp.profile_digest == BOOTSTRAP_PROFILE_DIGEST

    # clause (f): bootstrap-runtime is a profile_id distinct from static-runtime,
    # so static profile_version "2" is free for STATIC_RUNTIME_PROFILE_V2.
    assert brp.profile_id != srp.profile_id
    assert srp.profile_digest != brp.profile_digest


# =========================================================================== #
# Point 9 — no Phase-8 path shadows a Phase 1-7 owned module/test/golden.        #
# =========================================================================== #
def test_point9_phase1_7_owned_modules_and_goldens_exist_singular():
    for path in PHASE1_7_OWNED_MODULES + UPSTREAM_GOLDEN_FILES:
        assert path.is_file() and path.stat().st_size > 0, f"missing upstream file: {path}"
    assert Path(__file__).name == "test_phase8_handoff.py"


def test_point9_phase8_paths_never_shadow_owned_or_golden_files():
    owned = set(PHASE1_7_OWNED_MODULES) | set(UPSTREAM_GOLDEN_FILES)
    for path in PHASE8_NEW_PATHS:
        assert path not in owned, (
            f"Phase 8 path {path} shadows a Phase 1-7 owned/golden file — Phase 8 is "
            "additive and may never overwrite an upstream source or golden")
    for golden in (GOLDEN_DIR / "phase8_schema_manifest_v1.json",
                   GOLDEN_DIR / "phase8_catalog_manifest_v1.json"):
        assert golden.name.startswith("phase8_")


# =========================================================================== #
# Clause (a) — the implemented chain builder API (name-authoritative).          #
# =========================================================================== #
def test_clause_a_registry_chain_builders_share_the_crib_recursion_shape():
    for build, err, base_param in (
        (trial.build_phase4_registry, trial.Phase4RegistryError, "expected_phase3_full_digest"),
        (bootstrap.build_phase5_registry, bootstrap.Phase5RegistryError, "expected_phase4_digest"),
        (shadow.build_phase6_registry, shadow.Phase6RegistryError, "expected_phase5_digest"),
        (p7.build_phase7_registry, p7.Phase7RegistryError, "expected_phase6_digest"),
    ):
        assert _params(build) == [base_param]
        assert issubclass(err, Exception)
        with pytest.raises(err):
            build(D64)


def test_clause_a_catalog_builders_are_heterogeneous_but_accessors_are_uniform():
    # the catalog builders are NOT a uniform expected-digest recursion (recorded).
    assert _params(p7.build_phase7_catalog_snapshot) == ["phase6_snapshot", "planner_materials"]
    assert "expected_phase5_digest" in _params(shadow.build_phase6_catalog_snapshot)
    # the uniform, signature-independent consumption path is the canonical accessor.
    for accessor, frozen in (
        (trial.phase4_catalog_snapshot, PHASE4_CATALOG_DIGEST),
        (bootstrap.phase5_catalog_snapshot, PHASE5_CATALOG_DIGEST),
        (shadow.phase6_catalog_snapshot, PHASE6_CATALOG_DIGEST),
        (p7.phase7_catalog_snapshot, PHASE7_CATALOG_DIGEST),
    ):
        assert accessor().catalog_digest == frozen


# =========================================================================== #
# Clause (b) — the current EventType shape + DebateMessage still guarded.        #
# =========================================================================== #
def test_clause_b_event_type_count_and_debate_message_still_deferred():
    assert len(list(EventType)) == 25
    assert not hasattr(EventType, "DEBATE_MESSAGE_PUBLISHED")
    tcc = importlib.import_module("tests.orchestration.test_contract_completeness")
    assert "DebateMessage" in tcc.DEFERRED_PHASE_PAYLOADS
    # DebateMessage is not yet registered anywhere (pure Phase-1 absence guard).
    reg = p7.build_phase7_registry(p7.PHASE7_BASE_REGISTRY_DIGEST)
    assert "DebateMessage" not in {e.schema_ref.name for e in reg.manifest()}


# =========================================================================== #
# Clause (c) — the six plan-ABI intents bind to exact CapabilityRef ids.         #
# =========================================================================== #
def test_clause_c_data_capability_ids_bind_to_the_implemented_refs():
    refs = data_capability_refs()
    for intent, cap_id in DATA_CAPABILITY_IDS.items():
        method_id = _INTENT_TO_METHOD_ID[intent]
        ref = refs[method_id]
        assert ref.id == cap_id, f"{intent} -> {method_id} bound to {ref.id!r} not {cap_id!r}"
        assert ref.version == "1"


# =========================================================================== #
# Clause (e) — the Lane-0 skill sources currently live under materials/lane0.    #
# =========================================================================== #
def test_clause_e_lane0_skill_sources_are_at_the_pre_relocation_location():
    # Phase 5 placed the three Lane-0 SKILL.md sources OUTSIDE the skills tree;
    # Task 1 must relocate them byte-identical into guanlan_v2/orchestration/skills/.
    for skill_file in ("regime_skill.md", "rotation_skill.md", "factor_miner_skill.md"):
        path = LANE0_MATERIALS_DIR / skill_file
        assert path.is_file() and path.stat().st_size > 0, f"missing Lane-0 skill {path}"


# =========================================================================== #
# Clause (g) — llm.yaml has deepseek as the sole live provider.                  #
# =========================================================================== #
def test_clause_g_llm_yaml_live_provider_is_deepseek():
    assert LLM_YAML.is_file()
    text = LLM_YAML.read_text(encoding="utf-8")
    assert "default_provider: deepseek" in text
    assert "deepseek-chat" in text and "deepseek-reasoner" in text


# =========================================================================== #
# Clause (h) — compute_pa_features is import-safe + side-effect-free.            #
# =========================================================================== #
def test_clause_h_compute_pa_features_is_import_safe_and_pure():
    mod = importlib.import_module("guanlan_v2.seats.price_action")
    assert compute_pa_features.__module__ == "guanlan_v2.seats.price_action"
    assert _params(compute_pa_features) == ["df", "code", "name"]
    # module top-level imports are stdlib-only (no network / global-state deps).
    top_imports = [
        ln.strip() for ln in inspect.getsource(mod).splitlines()
        if ln.startswith(("import ", "from "))
    ]
    assert top_imports == ["from __future__ import annotations", "import math"]
