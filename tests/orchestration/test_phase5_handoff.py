# -*- coding: utf-8 -*-
"""Task 0 — the executable Phase 1/2/3/4 -> Phase 5 handoff gate.

This is a *consumer* gate, not a red->green TDD task: it imports the frozen
Phase 1 (amended) contract layer, the sealed Phase 2 runtime kernel, the Phase 3
data/PIT + memory facade and the Phase 4 Evaluator-Optimizer chain exactly as the
Phase 5 Bootstrap Lane-0 + 经验库 stack will, exercises the real
registries / builders / stores / digests through their public surfaces, and
asserts the upstream API surface Phase 5 imports is intact and singular. Every
assertion here PASSES against the already-existing upstream code (branch
``report-evidence-pack`` at authoring; ``pytest tests/orchestration`` collects
~2296 tests green). A failure means an upstream Phase 1/2/3/4 contract drifted
from its frozen exit gate and BLOCKS Phase 5 — it must NEVER be "fixed" by
weakening an assertion or editing upstream; fix the owning phase instead.

It proves the nine points of ``.superpowers/sdd/task-0-brief.md`` Step 1:

1. the Phase 1 amended golden (``schema_manifest_v1.json``, 11 registered schemas
   incl. ``TypedPayloadRef@1`` / ``InputArtifactBinding@1`` /
   ``ContextRuntimeRequirements@1``) and the digest golden vectors reproduce;
   ``ContextSnapshot`` carries ``memory_snapshot_ref`` / ``memory_selection_ref``
   as ``TypedPayloadRef`` and ``runtime_requirements_ref=None`` for the canonical
   empty-memory pair;
2. ``EventType`` already contains ``CASE_CREATED`` / ``CASE_MATURED`` /
   ``CASE_REVIEWED`` (events.py:131-133) **and** the Phase 4 Trial members; Phase 5
   adds no event type (exactly 23 members);
3. the Phase 2 store/admission/profile exports resolve and the static-runtime v1
   profile rejects a ``phase="bootstrap"`` / no-ContextSnapshot draft before any
   budget reservation (the fact Task 8 lawfully changes only via a *distinct*
   profile identity);
4. the Phase 3 PIT/error/calendar/render exports and the memory facade resolve;
   ``PHASE3_FULL_REGISTRY_DIGEST`` / ``PHASE3_FULL_CATALOG_DIGEST`` verify;
5. the Phase 4 chain (``build_phase4_registry`` / ``build_phase4_catalog_snapshot``
   + digests), ``TrialLedger`` / ``StudySpec`` / ``run_optimize`` /
   ``finalize_candidate`` and the ``WAITING_FOR_MATURITY`` resume carrier
   (``MaturityPending.resume_after`` / ``.wakeup_key``) resolve, and the Phase 2
   three-worker pilot fixtures resolve and admit;
6. ``test_contract_completeness.py`` still guards ``MarketFactorValue`` /
   ``MarketFactorReport`` / ``RegimeReport`` / ``RealizedRegime`` /
   ``RotationReport`` as **absent** deferred payloads (Task 9 flips them);
7. the frozen read-only scope-protection surfaces exist and are named (the
   non-modification audit is a pathspec audit over the Phase 5 commit range at
   exit, NOT a byte pin — concurrent sessions lawfully edit these hot files);
8. the shield gate constants (risk_off ≤25 / ≤-300亿, overheat ≥85 &
   break_rate ≥0.35) and the ``market_tape`` derived keys are asserted at their
   sources (behaviorally — there are no named constants to pin);
9. no Phase 5 source/test/golden path overwrites Phase 1–4 files, and this gate
   runs from a state in which no Phase 5 module exists yet.

Step 2's reviewed upstream digests are recorded verbatim as module constants and
re-derived from the sealed builders below.

Task-0 name corrections recorded for later tasks (binding per the plan's
correction clauses):

* **C1** — the implemented ``StaticRuntimeProfile@1`` pins ``profile_id`` to
  ``Literal["static-runtime"]`` and ``profile_version`` to ``Literal["1"]`` and
  expresses ``supports_bootstrap`` as ``Literal[False]``; the canonical instance
  is ``runtime_contracts.static_runtime_profile()``. Task 8 mints the BOOTSTRAP
  unlock as a *distinct* profile identity by additively widening this Literal (and
  ``runtime_support.check_runtime_support`` if needed), leaving static-runtime v1
  bit-unchanged — never a forked schema.
* **C5** — the Phase 4 maturity resume carrier is
  ``optimize.MaturityPending(resume_after=..., wakeup_key=...)`` (an exception,
  not a model); Task 6's grader adopts these exact names.
* the Phase 4 chain builders/digests are ``trial.build_phase4_registry`` /
  ``trial.PHASE4_REGISTRY_DIGEST`` and ``trial.build_phase4_catalog_snapshot`` /
  ``trial.phase4_catalog_snapshot`` / ``trial.PHASE4_CATALOG_DIGEST`` (frozen
  chain-naming); ``PHASE4_BASE_*`` equal the Phase-3 full digests.
* the reserved event values are ``"CaseCreated"`` / ``"CaseMatured"`` /
  ``"CaseReviewed"`` at ``events.py:131-133`` (the brief's "119-121" predates the
  Phase 4 Trial additions — the values, not the line numbers, are normative).

Run from repo root: ``python -m pytest tests/orchestration/test_phase5_handoff.py -v``
"""
from __future__ import annotations

import hashlib
import importlib
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
    PayloadRef,
    SchemaRef,
    TypedPayloadRef,
)
from guanlan_v2.orchestration.schema_registry import (
    RegistrySealedError,
    SchemaRegistry,
    default_registry,
)
from guanlan_v2.orchestration.enums import (
    DataBackend,
    DataMode,
    ExperimentStatus,
    PlanSource,
)
from guanlan_v2.orchestration.events import EventType
from guanlan_v2.orchestration.context import (
    ClockSpec,
    ContextRuntimeRequirements,
    ContextSnapshot,
    DataContext,
    InputArtifactBinding,
    build_empty_memory_binding,
)
from guanlan_v2.orchestration.spec import validate_plan_draft

# -- Phase 2 runtime kernel ------------------------------------------------- #
from guanlan_v2.orchestration.budget import BudgetLedger, IdempotencyConflict
from guanlan_v2.orchestration.eventstore import (
    EventStore,
    PayloadStore,
    RuntimeUnitOfWork,
)
from guanlan_v2.orchestration.catalog_runtime import BridgeCatalogView, CatalogRuntime
from guanlan_v2.orchestration.admission import PlanAdmissionService
from guanlan_v2.orchestration.dag import run_plan
from guanlan_v2.orchestration.runtime_contracts import (
    StaticRuntimeProfile,
    static_runtime_profile,
)
from guanlan_v2.orchestration.runtime_support import check_runtime_support
from guanlan_v2.orchestration import presets as P

# -- Phase 3 data/PIT + memory facade --------------------------------------- #
from guanlan_v2.orchestration.data.errors import (
    FutureDataRefused,
    MissingAvailabilityRefused,
)
from guanlan_v2.orchestration.data.pit import PitGuard
from guanlan_v2.orchestration.data.calendar import (
    ImmutableTradingCalendar,
    TradingCalendar,
    TradingCalendarResolver,
)
from guanlan_v2.orchestration.data.render import RenderedDataBlock
from guanlan_v2.orchestration.memory.models import (
    MemoryProposal,
    MemoryProposalDecision,
    MemoryProposalGrant,
    MemoryProposalRequest,
)
from guanlan_v2.orchestration.memory.proposals import AdminReviewVerifier
from guanlan_v2.orchestration.memory import catalog as mem_catalog
from guanlan_v2.orchestration.memory import schema_registry as mem_schema_registry
from guanlan_v2.orchestration.memory.catalog import (
    PHASE3_FULL_CATALOG_DIGEST,
    phase3_full_catalog_snapshot,
)
from guanlan_v2.orchestration.memory.schema_registry import (
    PHASE3_FULL_BASE_REGISTRY_DIGEST,
    PHASE3_FULL_REGISTRY_DIGEST,
    build_phase3_full_registry,
)

# -- Phase 4 Evaluator-Optimizer chain -------------------------------------- #
from guanlan_v2.orchestration import trial
from guanlan_v2.orchestration.trial import StudySpec
from guanlan_v2.orchestration.trial_ledger import TrialLedger
from guanlan_v2.orchestration.optimize import (
    MaturityPending,
    finalize_candidate,
    run_optimize,
)

# -- Frozen read-only scope-protection surfaces (imported = they resolve) --- #
from guanlan_v2.screen.market_temp import _gate as market_temp_gate
from guanlan_v2.datafeed import market_tape


# --------------------------------------------------------------------------- #
# Paths + fixtures                                                            #
# --------------------------------------------------------------------------- #
TESTS_DIR = Path(__file__).resolve().parent
GOLDEN_DIR = TESTS_DIR / "golden"
ORCH_DIR = Path(orch_pkg.__file__).resolve().parent
REPO_ROOT = TESTS_DIR.parents[1]

SCHEMA_MANIFEST_GOLDEN = GOLDEN_DIR / "schema_manifest_v1.json"
DIGEST_VECTORS_GOLDEN = GOLDEN_DIR / "digest_vectors_v1.json"

UTC = timezone.utc
DT = datetime(2026, 7, 17, 2, 0, tzinfo=UTC)
D64 = "a" * 64

# =========================================================================== #
# Step 2 — reviewed upstream digests recorded verbatim.                        #
# The exact sealed Phase 1 amended registry digest and the Phase 3 full +      #
# Phase 4 cumulative registry / catalog digests as implemented and reviewed on #
# this branch — not a local path, a guess, or a mutable singleton identity.    #
# Re-derived from the sealed builders in the Step-2 tests below.                #
# =========================================================================== #
PHASE1_AMENDED_REGISTRY_DIGEST = (
    "75f7920db13cdcaac89a70e0103812a29348ab3caaa98b9c1020429bb4e18b03"
)
PHASE3_FULL_BASE_REGISTRY_DIGEST_FROZEN = (
    "9119fa179a598b3d2d62e059747c6fd581d4e8b3be5eea0304da47d80ca51612"
)
PHASE3_FULL_REGISTRY_DIGEST_FROZEN = (
    "ff2a56d283499e347dcdbf4c2f5c5977710fd5575afbc94ee34819b2b531e49d"
)
PHASE3_FULL_CATALOG_DIGEST_FROZEN = (
    "c13294e5f020de542cc553d92e509d3dfea45673700373ac0c013d9115c3a773"
)
PHASE4_REGISTRY_DIGEST_FROZEN = (
    "5475880847f5e44fae500545d24fd01f227b507d7b74945e0427bec32f361f0d"
)
PHASE4_CATALOG_DIGEST_FROZEN = (
    "aefe0cf3b12f875d4d5062db7b6d00e8ae65cf3f5081eb527d8750671b7e651b"
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

#: The Phase 5 registered-schema payloads deferred out of Phase 1–4 — Task 9
#: flips these completeness guards from "absent" to "present in the Phase 5
#: registry". They must NOT resolve anywhere upstream at Task 0.
PHASE5_DEFERRED_PAYLOADS = (
    "MarketFactorValue",
    "MarketFactorReport",
    "RegimeReport",
    "RealizedRegime",
    "RotationReport",
)

#: The user-frozen read-only production surfaces Phase 5 must not modify. The
#: real non-modification proof is a pathspec audit over the Phase 5 commit range
#: at exit (never a byte pin — concurrent sessions lawfully edit these files).
FROZEN_READONLY_SURFACES = (
    REPO_ROOT / "guanlan_v2" / "workflow" / "executor.py",
    REPO_ROOT / "guanlan_v2" / "screen" / "market_temp.py",
    REPO_ROOT / "guanlan_v2" / "datafeed" / "market_tape.py",
    REPO_ROOT / "guanlan_v2" / "macro" / "astock.py",
)

#: The Phase 5 module/golden paths the plan creates — none may exist yet, and
#: none may shadow a Phase 1–4 owned path.
PHASE5_NEW_PATHS = (
    ORCH_DIR / "market",
    ORCH_DIR / "market" / "factors.py",
    ORCH_DIR / "bootstrap.py",
    ORCH_DIR / "memory" / "experience.py",
    GOLDEN_DIR / "market_factor_set_v1.json",
    GOLDEN_DIR / "regime_grader_policy_v1.json",
    GOLDEN_DIR / "phase5_schema_manifest_v1.json",
    GOLDEN_DIR / "phase5_catalog_manifest_v1.json",
)


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _empty_memory_context() -> ContextSnapshot:
    """A sealed canonical empty-memory :class:`ContextSnapshot` (store-free)."""
    binding = build_empty_memory_binding()
    clock = ClockSpec(as_of=DT, timezone="Asia/Shanghai", calendar_id="cn_a_share")
    dc = DataContext(
        as_of=DT, clock=clock, mode=DataMode.ONLINE, backend=DataBackend.LIVE,
        strict_pit=False, calendar_id="cn_a_share",
        resolved_vendor_chains={"prices": ("tushare",)},
        source_config_digest="a" * 64, source_registry_digest="b" * 64,
        routing_snapshot_digest="c" * 64, data_snapshot_id="snap-1",
        data_snapshot_content_digest="d" * 64, built_at=DT,
    )
    return ContextSnapshot.build(
        snapshot_id="ctx-1", data_context=dc, memory_snapshot_id="ms-1",
        memory_snapshot_hash=binding.snapshot_hash,
        past_context_hash=binding.past_context_hash,
        memory_snapshot_ref=binding.memory_snapshot_ref,
        memory_selection_ref=binding.memory_selection_ref,
        runtime_requirements_ref=None, memory_session_id=None, built_at=DT,
    )


def _pilot_admission():
    """The reviewed three-worker PRESET pilot, resolved + admitted at Phase 1.

    Returns ``(registry, catalog, context, draft, view, phase1_report)`` — the
    exact public preset fixtures the Task 8 BOOTSTRAP-profile differential builds
    on. This is a Phase-1 admission (``validate_plan_draft``), the layer that
    proves the pilot "admits"; the full reserve/approve/freeze pipeline is owned
    by ``test_pilot_runtime.py``.
    """
    registry = default_registry()
    catalog = P.load_pilot_catalog()
    context = _empty_memory_context()
    request = P.preset_orchestration_request(request_id="gate-pilot")
    draft = P.build_pilot_draft(
        catalog=catalog, registry=registry, context=context,
        request_id=request.request_id, as_of=DT,
    )
    view = BridgeCatalogView.build(catalog, {})
    report = validate_plan_draft(
        draft, request=request, context=context,
        catalog=catalog.snapshot, schema_registry=registry,
    )
    return registry, catalog, context, draft, view, report


# =========================================================================== #
# Point 1 — Phase 1 amended golden reproduces + amendment surface resolves      #
# =========================================================================== #
def test_point1_default_registry_is_sealed_and_frozen():
    reg = default_registry()
    assert isinstance(reg, SchemaRegistry)
    assert reg.sealed is True
    with pytest.raises(RegistrySealedError):
        from guanlan_v2.orchestration.schemas import ResearchPlan

        reg.register(ResearchPlan)


def test_point1_amended_schema_manifest_golden_reproduces_11_schemas():
    doc = _load_json(SCHEMA_MANIFEST_GOLDEN)
    assert doc["algorithm"] == CJSON_VERSION == "sha256+cjson-v1"
    golden = {e["key"]: e["json_schema_digest"] for e in doc["entries"]}

    reg = default_registry()
    manifest = {e.key: e.json_schema_digest for e in reg.manifest()}

    assert set(golden) == PHASE1_SCHEMA_KEYS
    for landed in ("TypedPayloadRef@1", "InputArtifactBinding@1",
                   "ContextRuntimeRequirements@1"):
        assert landed in golden, f"amendment schema {landed} missing from the golden"
    assert set(manifest) == set(golden), (
        "sealed registry schema-key set drifted from the frozen amended golden"
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


def test_point1_amendment_types_resolve_from_phase1_modules():
    assert TypedPayloadRef.__module__ == "guanlan_v2.orchestration.refs"
    assert InputArtifactBinding.__module__ == "guanlan_v2.orchestration.context"
    assert ContextRuntimeRequirements.__module__ == "guanlan_v2.orchestration.context"
    # TypedPayloadRef composes exactly (schema_version, schema_ref, payload_ref).
    assert set(TypedPayloadRef.model_fields) == {
        "schema_version", "schema_ref", "payload_ref"
    }


def test_point1_context_snapshot_carries_typed_memory_refs_and_none_requirements():
    assert ContextSnapshot.model_fields["memory_snapshot_ref"].annotation is TypedPayloadRef
    assert ContextSnapshot.model_fields["memory_selection_ref"].annotation is TypedPayloadRef
    assert ContextSnapshot.model_fields["runtime_requirements_ref"].annotation == (
        TypedPayloadRef | None
    )
    binding = build_empty_memory_binding()
    assert isinstance(binding.memory_snapshot_ref, TypedPayloadRef)
    assert isinstance(binding.memory_selection_ref, TypedPayloadRef)
    cs = _empty_memory_context()
    assert isinstance(cs.memory_snapshot_ref, TypedPayloadRef)
    assert isinstance(cs.memory_selection_ref, TypedPayloadRef)
    assert cs.runtime_requirements_ref is None  # canonical empty-memory pair
    assert cs.memory_session_id is None
    assert cs.content_digest == cs.semantic_digest()


# =========================================================================== #
# Point 2 — the reserved case event types + Phase-4 Trial members; +0 in P5     #
# =========================================================================== #
def test_point2_reserved_case_event_types_exist_with_frozen_values():
    assert EventType.CASE_CREATED.value == "CaseCreated"
    assert EventType.CASE_MATURED.value == "CaseMatured"
    assert EventType.CASE_REVIEWED.value == "CaseReviewed"


def test_point2_phase4_trial_event_types_present():
    assert EventType.TRIAL_RESERVED.value == "TrialReserved"
    assert EventType.TRIAL_REVEALED.value == "TrialRevealed"
    assert EventType.TRIAL_EXHAUSTED.value == "TrialExhausted"


def test_point2_event_type_vocabulary_is_exactly_23_members():
    """20 Phase-1 members + 3 Phase-4 Trial members; Phase 5 adds no event type."""
    assert len(list(EventType)) == 23
    # the three reserved case members are Phase-1 members (not renumbered away).
    names = [e.name for e in EventType]
    for name in ("CASE_CREATED", "CASE_MATURED", "CASE_REVIEWED"):
        assert name in names


# =========================================================================== #
# Point 3 — Phase 2 exports resolve; static v1 profile rejects bootstrap         #
# =========================================================================== #
def test_point3_phase2_exports_resolve_with_public_surface():
    assert inspect.isclass(BudgetLedger)
    for method in ("append", "journal", "visible"):
        assert callable(getattr(EventStore, method)), f"EventStore.{method} missing"
    assert callable(getattr(PayloadStore, "put")) and callable(getattr(PayloadStore, "get"))
    assert callable(getattr(RuntimeUnitOfWork, "commit"))
    assert inspect.isclass(CatalogRuntime)
    assert inspect.isclass(PlanAdmissionService)
    assert inspect.iscoroutinefunction(run_plan)
    assert inspect.isclass(StaticRuntimeProfile)
    assert callable(check_runtime_support)
    assert IdempotencyConflict.__module__ == "guanlan_v2.orchestration.budget"


def test_point3_static_runtime_profile_v1_identity_forbids_bootstrap():
    prof = static_runtime_profile()
    # C1: the implemented v1 identity Task 8 must NOT reuse — it mints a distinct one.
    assert prof.profile_id == "static-runtime"
    assert prof.profile_version == "1"
    assert prof.supports_bootstrap is False
    assert PlanSource.BOOTSTRAP not in prof.supported_plan_sources
    # the digest reproduces (a fresh, equal instance).
    assert static_runtime_profile().profile_digest == prof.profile_digest
    # the v1 matrix structurally cannot admit BOOTSTRAP as a supported source.
    with pytest.raises(ValueError):
        StaticRuntimeProfile.build(
            supported_plan_sources=(
                PlanSource.BOOTSTRAP, PlanSource.PRESET,
                PlanSource.PRESET_FALLBACK, PlanSource.DYNAMIC,
            )
        )


def test_point3_checker_is_store_free_and_rejects_a_no_context_draft():
    # the checker never takes a store (a fake-store spy is impossible by signature).
    params = set(inspect.signature(check_runtime_support).parameters)
    assert "profile" in params and "context" in params
    assert not any("store" in p for p in params)

    registry, catalog, context, draft, view, phase1 = _pilot_admission()
    assert phase1.valid, [i.code for i in phase1.issues]

    # a supported baseline: the static PRESET pilot with a ContextSnapshot is admitted.
    ok = check_runtime_support(
        draft, phase1_report=phase1, context=context, context_requirements=None,
        catalog=catalog, bridge_view=view, schema_registry=registry,
        profile=static_runtime_profile(),
    )
    assert ok.supported is True

    # a no-ContextSnapshot draft is rejected before any reservation (context=None
    # is the bootstrap-lane condition Task 8 unlocks only under a distinct profile).
    none_rep = check_runtime_support(
        draft, phase1_report=phase1, context=None, context_requirements=None,
        catalog=catalog, bridge_view=view, schema_registry=registry,
        profile=static_runtime_profile(),
    )
    assert none_rep.supported is False
    assert "bootstrap_unsupported" in {i.code for i in none_rep.issues}


# =========================================================================== #
# Point 4 — Phase 3 PIT/error/calendar/render + memory facade resolve            #
# =========================================================================== #
def test_point4_phase3_error_pit_calendar_render_exports_resolve():
    assert issubclass(FutureDataRefused, Exception)
    assert issubclass(MissingAvailabilityRefused, Exception)
    assert FutureDataRefused.__module__ == "guanlan_v2.orchestration.data.errors"
    assert MissingAvailabilityRefused.__module__ == "guanlan_v2.orchestration.data.errors"
    assert inspect.isclass(PitGuard)
    # TradingCalendar is a Protocol; TradingCalendarResolver is the service.
    assert TradingCalendar.__module__ == "guanlan_v2.orchestration.data.calendar"
    assert inspect.isclass(TradingCalendarResolver)
    assert callable(getattr(TradingCalendarResolver, "resolve"))
    assert inspect.isclass(ImmutableTradingCalendar)
    assert inspect.isclass(RenderedDataBlock)
    assert RenderedDataBlock.__module__ == "guanlan_v2.orchestration.data.render"


def test_point4_memory_facade_resolves():
    for model in (MemoryProposal, MemoryProposalRequest, MemoryProposalGrant,
                  MemoryProposalDecision):
        assert model.__module__ == "guanlan_v2.orchestration.memory.models"
    # AdminReviewVerifier is the reviewed admin-approval verifier protocol.
    assert AdminReviewVerifier.__module__ == "guanlan_v2.orchestration.memory.proposals"


def test_point4_phase3_full_registry_and_catalog_reproduce():
    assert PHASE3_FULL_REGISTRY_DIGEST == PHASE3_FULL_REGISTRY_DIGEST_FROZEN
    assert PHASE3_FULL_BASE_REGISTRY_DIGEST == PHASE3_FULL_BASE_REGISTRY_DIGEST_FROZEN
    rebuilt = build_phase3_full_registry(PHASE3_FULL_BASE_REGISTRY_DIGEST)
    assert rebuilt.sealed is True
    assert rebuilt.registry_digest == PHASE3_FULL_REGISTRY_DIGEST_FROZEN

    assert PHASE3_FULL_CATALOG_DIGEST == PHASE3_FULL_CATALOG_DIGEST_FROZEN
    assert phase3_full_catalog_snapshot().catalog_digest == PHASE3_FULL_CATALOG_DIGEST_FROZEN
    # owning modules are upstream (lazy digest exports resolve through them).
    assert mem_schema_registry.PHASE3_FULL_REGISTRY_DIGEST == PHASE3_FULL_REGISTRY_DIGEST_FROZEN
    assert mem_catalog.PHASE3_FULL_CATALOG_DIGEST == PHASE3_FULL_CATALOG_DIGEST_FROZEN


# =========================================================================== #
# Point 5 — Phase 4 chain + optimize/ledger + maturity carrier + pilot admit     #
# =========================================================================== #
def test_point5_phase4_registry_and_catalog_chain_reproduce():
    assert trial.PHASE4_BASE_REGISTRY_DIGEST == PHASE3_FULL_REGISTRY_DIGEST_FROZEN
    assert trial.PHASE4_BASE_CATALOG_DIGEST == PHASE3_FULL_CATALOG_DIGEST_FROZEN
    reg = trial.build_phase4_registry(trial.PHASE4_BASE_REGISTRY_DIGEST)
    assert reg.sealed is True
    assert reg.registry_digest == trial.PHASE4_REGISTRY_DIGEST == PHASE4_REGISTRY_DIGEST_FROZEN
    assert callable(trial.build_phase4_catalog_snapshot)
    snap = trial.phase4_catalog_snapshot()
    assert snap.catalog_digest == trial.PHASE4_CATALOG_DIGEST == PHASE4_CATALOG_DIGEST_FROZEN


def test_point5_optimize_ledger_and_maturity_carrier_resolve():
    assert inspect.isclass(StudySpec)
    assert inspect.isclass(TrialLedger)
    assert callable(run_optimize)
    assert callable(finalize_candidate)
    assert ExperimentStatus.WAITING_FOR_MATURITY.value == "waiting_for_maturity"
    # C5: the resume carrier is the MaturityPending exception with resume_after/wakeup_key.
    pending = MaturityPending(resume_after=DT, wakeup_key="wk-1")
    assert pending.resume_after == DT
    assert pending.wakeup_key == "wk-1"
    assert isinstance(pending, Exception)


def test_point5_three_worker_pilot_fixtures_resolve_and_admit():
    assert P.PILOT_NODE_IDS == ("sentiment", "research", "pm")
    for name in ("build_pilot_draft", "load_pilot_catalog", "pilot_data_context",
                 "preset_orchestration_request", "build_empty_memory_context"):
        assert callable(getattr(P, name)), f"preset fixture {name} missing"
    _registry, _catalog, _context, draft, _view, report = _pilot_admission()
    assert report.valid, [i.code for i in report.issues]
    assert draft.source is PlanSource.PRESET
    assert draft.phase == "main"
    assert tuple(n.id for n in draft.nodes) == P.PILOT_NODE_IDS


# =========================================================================== #
# Point 6 — the completeness firewall still guards the Phase-5 payloads absent   #
# =========================================================================== #
def test_point6_completeness_gate_still_defers_the_phase5_payloads():
    completeness = importlib.import_module(
        "tests.orchestration.test_contract_completeness"
    )
    deferred = set(completeness.DEFERRED_PHASE_PAYLOADS)
    for name in PHASE5_DEFERRED_PAYLOADS:
        assert name in deferred, (
            f"{name} dropped from the completeness firewall's DEFERRED_PHASE_PAYLOADS "
            "before Task 9 flips it"
        )


def test_point6_phase5_payloads_absent_from_every_upstream_registry():
    p1_names = {e.schema_ref.name for e in default_registry().manifest()}
    p3_names = {
        e.schema_ref.name
        for e in build_phase3_full_registry(PHASE3_FULL_BASE_REGISTRY_DIGEST).manifest()
    }
    p4_names = {
        e.schema_ref.name
        for e in trial.build_phase4_registry(trial.PHASE4_BASE_REGISTRY_DIGEST).manifest()
    }
    for name in PHASE5_DEFERRED_PAYLOADS:
        assert name not in p1_names, f"{name} leaked into the Phase-1 registry"
        assert name not in p3_names, f"{name} leaked into the Phase-3 full registry"
        assert name not in p4_names, f"{name} leaked into the Phase-4 registry"


# =========================================================================== #
# Point 7 — frozen read-only scope-protection surfaces exist (audit at exit)     #
# =========================================================================== #
def test_point7_frozen_readonly_surfaces_exist_and_are_named():
    for path in FROZEN_READONLY_SURFACES:
        assert path.is_file() and path.stat().st_size > 0, f"missing frozen surface: {path}"
    # run_graph is not consumed here at all — the shield parity work is the real
    # check; non-modification is a pathspec audit over the Phase 5 commit range at
    # exit, never a byte pin (concurrent sessions lawfully edit these hot files).
    assert (REPO_ROOT / "guanlan_v2" / "workflow" / "executor.py").is_file()


# =========================================================================== #
# Point 8 — shield gate constants + market_tape derived keys at their sources    #
# =========================================================================== #
def test_point8_shield_gate_boundary_constants_hold():
    # risk_off: astock_temp ≤ 25 (冰点) OR main_net_yi ≤ -300亿.
    assert market_temp_gate(25, None, None)["level"] == "risk_off"
    assert market_temp_gate(26, None, None)["level"] == "neutral"
    assert market_temp_gate(None, -300, None)["level"] == "risk_off"
    assert market_temp_gate(None, -299, None)["level"] == "neutral"
    # overheat: astock_temp ≥ 85 AND break_rate ≥ 0.35.
    assert market_temp_gate(85, None, 0.35)["level"] == "overheat"
    assert market_temp_gate(85, None, 0.34)["level"] == "neutral"
    assert market_temp_gate(84, None, 0.35)["level"] == "neutral"
    # all-missing → shield sleeps (None), never a fabricated level.
    assert market_temp_gate(None, None, None) is None


def test_point8_market_tape_derived_keys_present():
    derived = market_tape._derive({})
    for key in ("zt_count", "zb_count", "max_streak", "break_ratio", "break_rate",
                "promotion_rate", "ladder", "north_net", "north_scope"):
        assert key in derived, f"market_tape._derive dropped derived key {key!r}"
    # 空池诚实: an empty session yields None derived values, never a fabricated 0.0.
    assert derived["max_streak"] is None
    assert derived["break_rate"] is None
    assert derived["promotion_rate"] is None
    # board_date / board_backfilled are stamped by the read_tape assembly.
    tape_src = inspect.getsource(market_tape)
    assert '"board_date"' in tape_src and '"board_backfilled"' in tape_src


# =========================================================================== #
# Point 9 — no Phase 5 path overwrites Phase 1–4; fresh no-Phase-5 state          #
# =========================================================================== #
def test_point9_upstream_owned_source_files_exist_singular():
    owned = {
        # Phase 1
        ORCH_DIR / "digest.py", ORCH_DIR / "refs.py", ORCH_DIR / "enums.py",
        ORCH_DIR / "events.py", ORCH_DIR / "schemas.py", ORCH_DIR / "context.py",
        ORCH_DIR / "schema_registry.py",
        # Phase 2
        ORCH_DIR / "eventstore.py", ORCH_DIR / "budget.py", ORCH_DIR / "admission.py",
        ORCH_DIR / "runtime_contracts.py", ORCH_DIR / "runtime_support.py",
        ORCH_DIR / "catalog_runtime.py", ORCH_DIR / "presets.py", ORCH_DIR / "dag.py",
        # Phase 3
        ORCH_DIR / "data" / "errors.py", ORCH_DIR / "data" / "pit.py",
        ORCH_DIR / "data" / "calendar.py", ORCH_DIR / "data" / "render.py",
        ORCH_DIR / "memory" / "schema_registry.py", ORCH_DIR / "memory" / "catalog.py",
        ORCH_DIR / "memory" / "models.py", ORCH_DIR / "memory" / "proposals.py",
        # Phase 4
        ORCH_DIR / "trial.py", ORCH_DIR / "trial_ledger.py", ORCH_DIR / "optimize.py",
    }
    for path in owned:
        assert path.is_file() and path.stat().st_size > 0, f"missing upstream file: {path}"
    # this handoff gate is an *additive* Phase 5 test; it must not shadow upstream.
    assert Path(__file__).name == "test_phase5_handoff.py"


def test_point9_no_phase5_module_or_golden_exists_yet():
    for path in PHASE5_NEW_PATHS:
        assert not path.exists(), (
            f"Phase 5 path {path} already exists — Task 0 must run from a state with "
            "no Phase 5 module, and no Phase 5 path may overwrite a Phase 1–4 file"
        )
    # the Phase 5 contract modules are not importable yet.
    for mod in ("guanlan_v2.orchestration.market.factors",
                "guanlan_v2.orchestration.memory.experience",
                "guanlan_v2.orchestration.bootstrap"):
        with pytest.raises(ModuleNotFoundError):
            importlib.import_module(mod)


# =========================================================================== #
# Step 2 — the reviewed upstream digests are the frozen ones                    #
# =========================================================================== #
def test_step2_records_the_exact_phase1_amended_registry_digest():
    assert default_registry().registry_digest == PHASE1_AMENDED_REGISTRY_DIGEST


def test_step2_records_the_exact_phase3_full_registry_and_catalog_digests():
    assert build_phase3_full_registry(
        PHASE3_FULL_BASE_REGISTRY_DIGEST_FROZEN
    ).registry_digest == PHASE3_FULL_REGISTRY_DIGEST_FROZEN
    assert phase3_full_catalog_snapshot().catalog_digest == PHASE3_FULL_CATALOG_DIGEST_FROZEN


def test_step2_records_the_exact_phase4_registry_and_catalog_digests():
    assert trial.build_phase4_registry(
        trial.PHASE4_BASE_REGISTRY_DIGEST
    ).registry_digest == PHASE4_REGISTRY_DIGEST_FROZEN
    assert trial.phase4_catalog_snapshot().catalog_digest == PHASE4_CATALOG_DIGEST_FROZEN
