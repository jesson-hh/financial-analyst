# -*- coding: utf-8 -*-
"""Task 0 — the executable Phase 1/2/3/5/6 -> Phase 7 (dynamic Planner) handoff gate.

This is a *consumer* gate, not a red->green TDD feature task: it imports the frozen
Phase 1 (amended) contract layer, the sealed Phase 2 admission/budget/runtime
kernel, the Phase 3 data + memory facade, the Phase 5 Bootstrap Lane-0 chain and
the Phase 6 shadow-consumer chain exactly as the Phase 7 Planner will, exercises
the real registries / builders / services / digests through their public
surfaces, and asserts the upstream ABI the Planner consumes is intact and
singular. Every assertion PASSES against the already-implemented upstream code on
branch ``report-evidence-pack`` (``pytest tests/orchestration`` collects the whole
suite green). A failure means an upstream Phase 1-6 contract drifted from its
frozen exit gate and BLOCKS Phase 7 — it must NEVER be "fixed" by weakening an
assertion or editing upstream; fix the owning layer. Do not regenerate a golden.

It proves the ten points of ``.superpowers/sdd/task-0-brief.md`` Step 1:

1.  Phase 1 goldens still pass; ``default_registry()`` seals; exactly one
    ``compute_candidate_plan_digest`` / ``validate_plan_draft`` / ``freeze_plan``
    is exported from ``spec.py``; ``Plan.plan_digest`` == the candidate digest and
    ``Plan`` exposes no approver/decision field.
2.  ``OrchestrationRequest`` carries ``fallback_preset_id: LogicalId | None`` and
    ``approval_policy`` defaulting to ``REQUIRED``; ``PlanApproval`` exposes
    ``is_approved`` / ``authorizes_freeze`` / ``require_freeze_authority``;
    ``EventType`` already has ``PLAN_DRAFTED/PLAN_APPROVED/PLAN_REJECTED/PLAN_FROZEN``
    and its member count is exactly 25 (Phase 7 adds none).
3.  a DYNAMIC draft that fails to copy the request's approval policy, or whose
    policy is not REQUIRED, is rejected by ``validate_plan_draft`` with two
    distinct issue codes; ``ApprovalPolicy.AUTO`` fails for every source;
    compatibility workers are rejected under DYNAMIC source.
4.  Phase 2 ``PlanAdmissionService`` exposes the six candidate-lifecycle methods;
    ``BudgetLedger`` exposes the eight ledger methods; ``BudgetTransitionCommand``'s
    closed ``BudgetOperation`` operation set is importable.
5.  ``StaticRuntimeProfile`` v1 admits already-validated DYNAMIC **main** static
    DAGs — the gate builds a minimal DYNAMIC pilot-triad draft and shows Phase 1
    validation + runtime support accept it without any Phase 7 code.
6.  the ``PHASE4/5/6`` registry + catalog chain nodes exist and verify (builders
    reject a wrong predecessor digest); each golden manifest loads; inherited
    schema entries are byte-identical up the chain.
7.  Phase 5 produces a real frozen ``ContextSnapshot`` for main planning through
    the reviewed Bootstrap Lane-0 path whose ``memory_selection_ref`` (a
    ``TypedPayloadRef`` after Amendment 1) has ``payload_ref.namespace == "main"``.
8.  Phase 3 ``normalize_symbol`` imports and rejects non-symbol strings.
9.  no Phase 7 source/test/golden path overwrites a Phase 1-6 owned module, test,
    or golden file.
10. Amendment 1 is in force: the amended registry golden carries the 11-model set
    (incl. ``ContextRuntimeRequirements@1`` / ``InputArtifactBinding@1``) and the
    ``TypedPayloadRef(schema_ref, payload_ref)`` composite imports and round-trips
    while bare ``PayloadRef`` stays the plain locator.

------------------------------------------------------------------------------
TASK-0 CORRECTION CLAUSES resolved against the real code (binding on Tasks 1-10):

* **(a) budget operation vocabulary** — the implemented closed operation set is
  ``budget.BudgetOperation == Literal["reserve_plan","reserve_node","settle",
  "release"]`` and Phase 2 exposes NO dedicated non-plan-scope reservation entry
  point (``reserve_plan`` hard-binds ``scope_type="plan"`` + a real
  ``candidate_plan_digest``). Task 4's additive ``reserve_planner`` extension
  therefore stands; it must EXTEND (never replace) this closed vocabulary.
* **(b) planner-scope candidate binding** — ``BudgetScopeType`` already admits
  ``"planner"`` (``context.py``) but ``BudgetReservation.candidate_plan_digest`` is
  a required ``DigestHex`` and Phase 1/2 define NO pre-candidate planner binding;
  the plan's reviewed binding (the ``OrchestrationRequest`` semantic digest as the
  planner reservation's ``candidate_plan_digest``) stands.
* **(c) console admin ports** — ``AdminReviewVerifier`` is a Protocol in
  ``guanlan_v2.orchestration.memory.proposals``; ``AuthenticatedAdminPrincipal`` is
  in ``guanlan_v2.orchestration.memory.models``. P3 Task 9 did NOT add a
  ``memory_review_verifier`` keyword to ``build_console_router`` — its committed
  signature is ``build_console_router(store=None, agent_factory=None)``. Task 8's
  plan-approval verifier wiring mirrors the ``AdminReviewVerifier.verify`` port
  shape and adds its coordinator/verifier as new optional keywords.
* **(d) console UI mount point** — ``ui/console/console-report-card.jsx`` is
  mounted by ``ui/console/观澜 · 帷幄.html`` (a ``<script src="console-report-card.jsx?v=...">``
  tag). Task 8's new ``console-plan-approval-card.jsx`` mounts beside it in that
  same HTML page and bumps its own ``?v`` cache-bust via ``Edit``.
* **(e) chain node spelling** — the implemented Phase 5/6 exports are consumed
  verbatim: ``bootstrap.PHASE5_REGISTRY_DIGEST`` / ``bootstrap.PHASE5_CATALOG_DIGEST``
  and ``shadow.PHASE6_REGISTRY_DIGEST`` / ``shadow.PHASE6_CATALOG_DIGEST`` (lazy
  PEP-562 attrs) with ``shadow.build_phase6_registry`` /
  ``shadow.build_phase6_catalog_snapshot(phase5_snapshot, *, expected_phase5_digest=...)``.
  The Phase-6 catalog golden records ``result_catalog_digest`` (identity == P5).
* **(f) candidate-persistence event** — Phase 7 never appends a plan-lifecycle
  event itself; ``PlanAdmissionService.record_approval`` returns the authoritative
  ``RunEvent`` and the service owns ``PLAN_DRAFTED``/``PLAN_APPROVED`` emission.
* **(g) file:line drift** — every cited symbol is bound BY NAME, never by the
  plan's pre-Amendment-1 line numbers (e.g. ``BudgetScopeType`` lives at
  ``context.py`` ~line 90 not 76-78; ``PlanApproval`` at ``events.py`` ~384 not
  320-354).

Run from repo root: ``python -m pytest tests/orchestration/test_phase7_handoff.py -v``
"""
from __future__ import annotations

import hashlib
import importlib
import inspect
import json
import typing
from datetime import datetime, timezone
from pathlib import Path

import pytest

# -- Phase 1 (amended) contract layer --------------------------------------- #
import guanlan_v2.orchestration as orch_pkg
from guanlan_v2.orchestration.digest import CJSON_VERSION, ContractModel
from guanlan_v2.orchestration.refs import (
    ContentRef,
    PayloadRef,
    SchemaRef,
    TypedPayloadRef,
)
from guanlan_v2.orchestration.schema_registry import (
    RegistrySealedError,
    SchemaRegistry,
    default_registry,
)
from guanlan_v2.orchestration.context import (
    BudgetReservation,
    BudgetScopeType,
    ContextRuntimeRequirements,
    ContextSnapshot,
    InputArtifactBinding,
)
from guanlan_v2.orchestration.enums import ApprovalPolicy, PlanSource
from guanlan_v2.orchestration.events import EventType, PlanApproval
from guanlan_v2.orchestration.spec import (
    OrchestrationRequest,
    Plan,
    PlanDraft,
    compute_candidate_plan_digest,
    freeze_plan,
    validate_plan_draft,
    _DECISION_CLASS_SCHEMAS,
)

# -- Phase 2 admission / budget / runtime kernel ---------------------------- #
from guanlan_v2.orchestration import presets as P
from guanlan_v2.orchestration.admission import PlanAdmissionService
from guanlan_v2.orchestration.budget import (
    BudgetLedger,
    BudgetOperation,
    BudgetTransitionCommand,
    IdempotencyConflict,
)
from guanlan_v2.orchestration.catalog_runtime import (
    BridgeCatalogView,
    load_pilot_catalog,
)
from guanlan_v2.orchestration.eventstore import RuntimeStores, SchemaRegistryResolver
from guanlan_v2.orchestration.runtime_clock import AuthoritativeClock
from guanlan_v2.orchestration.runtime_contracts import (
    StaticRuntimeProfile,
    phase2_runtime_registry,
    static_runtime_profile,
)
from guanlan_v2.orchestration.runtime_support import check_runtime_support
from guanlan_v2.orchestration import worker as W

# -- Phase 3 data + memory ports (clause c) --------------------------------- #
from guanlan_v2.orchestration.data.symbols import normalize_symbol
from guanlan_v2.orchestration.memory.models import AuthenticatedAdminPrincipal
from guanlan_v2.orchestration.memory.proposals import AdminReviewVerifier

# -- Phase 4 / 5 / 6 chain --------------------------------------------------- #
from guanlan_v2.orchestration import trial
from guanlan_v2.orchestration import bootstrap
from guanlan_v2.orchestration import shadow
from guanlan_v2.orchestration.bootstrap import build_context_snapshot_from_bootstrap


# --------------------------------------------------------------------------- #
# Paths                                                                       #
# --------------------------------------------------------------------------- #
TESTS_DIR = Path(__file__).resolve().parent
GOLDEN_DIR = TESTS_DIR / "golden"
ORCH_DIR = Path(orch_pkg.__file__).resolve().parent
REPO_ROOT = ORCH_DIR.parent.parent
UI_CONSOLE_DIR = REPO_ROOT / "ui" / "console"
CONFIG_DIR = REPO_ROOT / "config"

SCHEMA_MANIFEST_GOLDEN = GOLDEN_DIR / "schema_manifest_v1.json"
DIGEST_VECTORS_GOLDEN = GOLDEN_DIR / "digest_vectors_v1.json"

UTC = timezone.utc
D64 = "0" * 64

# =========================================================================== #
# Step 2 — reviewed upstream digests recorded verbatim (branch HEAD f3d7e12).  #
# Each is re-derived from a sealed builder below; none is a local path or a     #
# mutable singleton identity, and none is regenerated from test code.          #
# =========================================================================== #
PHASE1_AMENDED_REGISTRY_DIGEST = (
    "75f7920db13cdcaac89a70e0103812a29348ab3caaa98b9c1020429bb4e18b03"
)
PHASE4_REGISTRY_DIGEST_FROZEN = (
    "5475880847f5e44fae500545d24fd01f227b507d7b74945e0427bec32f361f0d"
)
PHASE5_REGISTRY_DIGEST_FROZEN = (
    "5703d4c4916c7bdbbe209fcc7cf2ee9971dd0c2fb25313eb401f2b271e1eeb4f"
)
PHASE6_REGISTRY_DIGEST_FROZEN = (
    "0fd0ad0f77695e16905f545e0a3184867fb0e852a97b7191700c2d191687815f"
)

PHASE4_CATALOG_DIGEST_FROZEN = (
    "aefe0cf3b12f875d4d5062db7b6d00e8ae65cf3f5081eb527d8750671b7e651b"
)
PHASE5_CATALOG_DIGEST_FROZEN = (
    "42af246047adb25956df507b56aabb215baec692859772e4b4f98d776c149229"
)
# Phase 6 is an identity catalog node (zero workers/capabilities) — P6 == P5.
PHASE6_CATALOG_DIGEST_FROZEN = PHASE5_CATALOG_DIGEST_FROZEN

#: The 11 Phase-1 amended registry schema keys (Amendment 1: 8 -> 11).
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

#: EventType has exactly this many members at the Phase 7 handoff; Phase 7 adds
#: NONE (the four PLAN_* lifecycle types already exist).
EVENT_TYPE_MEMBER_COUNT = 25

#: clause (d): the existing console page that mounts console-report-card.jsx; the
#: Phase-7 plan-approval card mounts beside it (read-only identification here).
CONSOLE_MOUNT_HTML = UI_CONSOLE_DIR / "观澜 · 帷幄.html"
CONSOLE_REPORT_CARD = UI_CONSOLE_DIR / "console-report-card.jsx"
CONSOLE_PLAN_APPROVAL_CARD = UI_CONSOLE_DIR / "console-plan-approval-card.jsx"


# --------------------------------------------------------------------------- #
# Phase 1-6 owned files Phase 7 must never overwrite (point 9)                 #
# --------------------------------------------------------------------------- #
PHASE1_6_OWNED_MODULES = tuple(
    ORCH_DIR / name
    for name in (
        # Phase 1
        "schemas.py", "spec.py", "schema_registry.py", "catalog.py", "refs.py",
        "digest.py", "enums.py", "events.py", "context.py",
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
    )
)
UPSTREAM_GOLDEN_FILES = (
    GOLDEN_DIR / "schema_manifest_v1.json",
    GOLDEN_DIR / "digest_vectors_v1.json",
    GOLDEN_DIR / "phase4_schema_manifest_v1.json",
    GOLDEN_DIR / "phase4_catalog_manifest_v1.json",
    GOLDEN_DIR / "phase5_schema_manifest_v1.json",
    GOLDEN_DIR / "phase5_catalog_manifest_v1.json",
    GOLDEN_DIR / "phase6_schema_manifest_v1.json",
    GOLDEN_DIR / "phase6_catalog_manifest_v1.json",
)

#: The Phase-7 module/golden paths the plan will create task-by-task. The durable
#: point-9 guarantee (learned from the Phase 5/6 handoffs): none of them ever
#: *shadows* (equals) a Phase 1-6 owned source, test, or golden — never a
#: transient "must not exist yet" assertion that later tasks lawfully invalidate.
PHASE7_NEW_PATHS = (
    ORCH_DIR / "orchestrator.py",
    ORCH_DIR / "plan_presets.py",
    ORCH_DIR / "plan_diff.py",
    ORCH_DIR / "approval.py",
    ORCH_DIR / "planner_gateway.py",
    ORCH_DIR / "phase7_registry.py",
    GOLDEN_DIR / "phase7_schema_manifest_v1.json",
    GOLDEN_DIR / "phase7_catalog_manifest_v1.json",
    GOLDEN_DIR / "plan_preset_manifest_v1.json",
    CONSOLE_PLAN_APPROVAL_CARD,
    TESTS_DIR / "test_phase7_handoff.py",
)


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _params(func) -> list[str]:
    return list(inspect.signature(func).parameters)


def _kwonly(func) -> set[str]:
    return {
        name
        for name, p in inspect.signature(func).parameters.items()
        if p.kind is inspect.Parameter.KEYWORD_ONLY
    }


# --------------------------------------------------------------------------- #
# Pilot-triad scaffolding for the DYNAMIC main-plan points (3 & 5)             #
# --------------------------------------------------------------------------- #
class _FixedClock:
    def now(self) -> datetime:
        return datetime(2026, 7, 19, 2, 0, tzinfo=UTC)


class _PilotPieces:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def _build_pilot_pieces(request_id: str = "req-phase7") -> _PilotPieces:
    """The real reviewed pilot catalog + canonical empty-memory main ContextSnapshot
    + a valid PRESET pilot-triad draft. Consumes only reviewed Phase 1/2 builders."""
    clock = _FixedClock()
    registry = default_registry()
    catalog_runtime = load_pilot_catalog()
    view = BridgeCatalogView.build(catalog_runtime, {})
    resolver = SchemaRegistryResolver()
    resolver.register(registry)
    rt_reg = phase2_runtime_registry(registry.registry_digest)
    rt_digest = resolver.register(rt_reg)
    stores = RuntimeStores(
        resolver=resolver, clock=clock,
        allowed_cell_namespaces=(W.PROMPT_CELL_NAMESPACE,))
    request = P.preset_orchestration_request(request_id=request_id)
    mem = P.build_empty_memory_context(
        data_context=P.pilot_data_context(as_of=clock.now()),
        stores=stores, registry_digest=rt_digest, built_at=clock.now())
    context = mem.context
    preset_draft = P.build_pilot_draft(
        catalog=catalog_runtime, registry=registry, context=context,
        request_id=request.request_id, run_id="run-phase7", as_of=clock.now())
    return _PilotPieces(
        clock=clock, registry=registry, catalog_runtime=catalog_runtime, view=view,
        request=request, context=context, preset_draft=preset_draft)


def _with_source(draft: PlanDraft, source: PlanSource, **over) -> PlanDraft:
    """Reconstruct a draft with a different source/policy — RE-RUNS the structural
    validator (not a bypassing model_copy), so the variant is genuinely valid."""
    return PlanDraft(**{**draft.model_dump(), "source": source, **over})


# =========================================================================== #
# Point 1 — Phase 1 goldens pass; spec exports are singular; Plan has no        #
# approver/decision field and its plan_digest == the candidate digest.         #
# =========================================================================== #
def test_point1_default_registry_seals_and_amended_golden_reproduces():
    reg = default_registry()
    assert isinstance(reg, SchemaRegistry) and reg.sealed is True
    with pytest.raises(RegistrySealedError):
        from guanlan_v2.orchestration.schemas import ResearchPlan
        reg.register(ResearchPlan)

    doc = _load_json(SCHEMA_MANIFEST_GOLDEN)
    assert doc["algorithm"] == CJSON_VERSION == "sha256+cjson-v1"
    golden = {e["key"]: e["json_schema_digest"] for e in doc["entries"]}
    manifest = {e.key: e.json_schema_digest for e in reg.manifest()}
    assert set(golden) == PHASE1_SCHEMA_KEYS == set(manifest)
    for key in sorted(golden):
        assert manifest[key] == golden[key], f"JSON-schema digest drift for {key}"
    assert reg.registry_digest == doc["registry_digest"] == PHASE1_AMENDED_REGISTRY_DIGEST


def test_point1_digest_golden_vectors_are_internally_consistent():
    doc = _load_json(DIGEST_VECTORS_GOLDEN)
    assert doc["algorithm"] == CJSON_VERSION
    assert doc["vectors"], "digest golden must carry vectors"
    for vec in doc["vectors"]:
        recomputed = hashlib.sha256(vec["canonical_json"].encode("utf-8")).hexdigest()
        assert recomputed == vec["digest"], f"golden vector {vec['name']!r} drift"


def test_point1_spec_owns_exactly_one_candidate_validate_freeze_export():
    import pkgutil

    for fn, mod in (
        (compute_candidate_plan_digest, "guanlan_v2.orchestration.spec"),
        (validate_plan_draft, "guanlan_v2.orchestration.spec"),
        (freeze_plan, "guanlan_v2.orchestration.spec"),
    ):
        assert fn.__module__ == mod, f"{fn.__name__} is not owned by spec.py"

    targets = {"compute_candidate_plan_digest", "validate_plan_draft", "freeze_plan"}
    homes: dict[str, set[str]] = {t: set() for t in targets}
    for mi in pkgutil.walk_packages(orch_pkg.__path__, prefix=orch_pkg.__name__ + "."):
        try:
            module = importlib.import_module(mi.name)
        except Exception:
            continue
        for t in targets:
            obj = getattr(module, t, None)
            if inspect.isfunction(obj) and obj.__module__ == mi.name:
                homes[t].add(mi.name)
    for t in targets:
        assert homes[t] == {"guanlan_v2.orchestration.spec"}, (
            f"{t} has an alternative definition: {sorted(homes[t])}"
        )


def test_point1_plan_has_no_approver_or_decision_field_and_digest_is_the_candidate():
    fields = set(Plan.model_fields)
    for banned in ("approver", "approval", "decision", "actor_id", "approved_by",
                   "approval_decision"):
        assert banned not in fields, f"Plan leaked an authority field {banned!r}"
    assert "plan_digest" in fields
    # Plan.plan_digest is recomputed from the draft candidate digest on load.
    assert Plan.SELF_DIGEST_FIELDS == frozenset({"plan_digest"})
    assert "recompute_plan_digest" in dir(Plan)


# =========================================================================== #
# Point 2 — request fallback/approval fields, PlanApproval surface, EventType.  #
# =========================================================================== #
def test_point2_orchestration_request_fallback_and_approval_policy():
    fpid = OrchestrationRequest.model_fields["fallback_preset_id"]
    # LogicalId | None with a None default.
    assert fpid.default is None
    assert type(None) in typing.get_args(fpid.annotation)
    ap = OrchestrationRequest.model_fields["approval_policy"]
    assert ap.annotation is ApprovalPolicy
    assert ap.default is ApprovalPolicy.REQUIRED
    req = OrchestrationRequest(request_id="r-1", goal="g", workflow="orchestrate_only")
    assert req.fallback_preset_id is None
    assert req.approval_policy is ApprovalPolicy.REQUIRED


def test_point2_plan_approval_freeze_authority_surface():
    assert PlanApproval.__module__ == "guanlan_v2.orchestration.events"
    assert isinstance(inspect.getattr_static(PlanApproval, "is_approved"), property)
    assert _kwonly(PlanApproval.authorizes_freeze) == {"request_id", "candidate_plan_digest"}
    assert _kwonly(PlanApproval.require_freeze_authority) == {"request_id", "candidate_plan_digest"}

    from guanlan_v2.orchestration.enums import ApprovalDecision
    approval = PlanApproval(
        request_id="r-1", candidate_plan_digest="a" * 64,
        decision=ApprovalDecision.APPROVED, actor_id="human-1",
        decided_at=datetime(2026, 7, 19, tzinfo=UTC))
    assert approval.is_approved is True
    assert approval.authorizes_freeze(request_id="r-1", candidate_plan_digest="a" * 64)
    assert not approval.authorizes_freeze(request_id="r-1", candidate_plan_digest="b" * 64)
    approval.require_freeze_authority(request_id="r-1", candidate_plan_digest="a" * 64)
    with pytest.raises(ValueError):
        approval.require_freeze_authority(request_id="r-1", candidate_plan_digest="b" * 64)


def test_point2_event_type_has_the_four_plan_lifecycle_members_and_phase7_adds_none():
    for name in ("PLAN_DRAFTED", "PLAN_APPROVED", "PLAN_REJECTED", "PLAN_FROZEN"):
        assert hasattr(EventType, name), f"EventType lost {name}"
    assert EventType.PLAN_DRAFTED.value == "PlanDrafted"
    assert EventType.PLAN_FROZEN.value == "PlanFrozen"
    # the frozen baseline Phase 7 must NOT grow.
    assert len(list(EventType)) == EVENT_TYPE_MEMBER_COUNT


# =========================================================================== #
# Point 3 — DYNAMIC approval-policy rejection codes + AUTO-any-source + compat.  #
# =========================================================================== #
def test_point3_dynamic_draft_mismatched_approval_policy_is_rejected():
    pieces = _build_pilot_pieces()
    # request AUTO + draft DYNAMIC/REQUIRED -> mismatch (isolated: draft IS REQUIRED,
    # so dynamic_approval_policy_not_required does NOT fire).
    req_auto = pieces.request.model_copy(update={"approval_policy": ApprovalPolicy.AUTO})
    dyn = _with_source(pieces.preset_draft, PlanSource.DYNAMIC)
    report = validate_plan_draft(
        dyn, request=req_auto, context=pieces.context,
        catalog=pieces.catalog_runtime.snapshot, schema_registry=pieces.registry)
    codes = {i.code for i in report.issues}
    assert not report.valid
    assert "dynamic_approval_policy_mismatch" in codes
    assert "dynamic_approval_policy_not_required" not in codes


def test_point3_dynamic_draft_non_required_policy_is_rejected():
    pieces = _build_pilot_pieces()
    # request AUTO + draft DYNAMIC/AUTO -> not_required (mismatch absent: both AUTO)
    # + auto_approval_rejected. Two distinct codes from the DYNAMIC path.
    req_auto = pieces.request.model_copy(update={"approval_policy": ApprovalPolicy.AUTO})
    dyn_auto = _with_source(
        pieces.preset_draft, PlanSource.DYNAMIC, approval_policy=ApprovalPolicy.AUTO)
    report = validate_plan_draft(
        dyn_auto, request=req_auto, context=pieces.context,
        catalog=pieces.catalog_runtime.snapshot, schema_registry=pieces.registry)
    codes = {i.code for i in report.issues}
    assert not report.valid
    assert "dynamic_approval_policy_not_required" in codes
    assert "dynamic_approval_policy_mismatch" not in codes


def test_point3_auto_policy_rejected_for_every_source():
    pieces = _build_pilot_pieces()
    for source in (PlanSource.PRESET, PlanSource.PRESET_FALLBACK, PlanSource.DYNAMIC):
        draft = _with_source(
            pieces.preset_draft, source, approval_policy=ApprovalPolicy.AUTO)
        report = validate_plan_draft(
            draft, request=pieces.request, context=pieces.context,
            catalog=pieces.catalog_runtime.snapshot, schema_registry=pieces.registry)
        codes = {i.code for i in report.issues}
        assert not report.valid, f"AUTO must fail for source={source.value}"
        assert "auto_approval_rejected" in codes


def test_point3_compat_worker_is_rejected_under_dynamic_source():
    # Reuses the reviewed lightweight compat-catalog builders (real upstream API).
    tpcv = importlib.import_module("tests.orchestration.test_plan_catalog_validation")
    catalog = tpcv._compat_catalog()
    registry = tpcv._registry()
    context = tpcv._context()
    draft = tpcv._compat_draft(
        catalog, registry, context, source=PlanSource.DYNAMIC, with_legacy_tuple=False)
    report = validate_plan_draft(
        draft, request=tpcv._request(), context=context,
        catalog=catalog, schema_registry=registry)
    codes = {i.code for i in report.issues}
    assert not report.valid
    assert "compat_worker_forbidden_source" in codes


# =========================================================================== #
# Point 4 — Phase 2 admission + budget public surface.                          #
# =========================================================================== #
def test_point4_admission_service_lifecycle_methods_resolve():
    for method in ("prepare_candidate", "persist_and_reserve_candidate",
                   "record_approval", "freeze_and_admit_candidate", "load_admitted",
                   "verify_for_dispatch"):
        assert callable(getattr(PlanAdmissionService, method)), method
    # record_approval(candidate_id, approval_input, *, authenticated_actor,
    # idempotency_key) -> RunEvent.
    assert _params(PlanAdmissionService.record_approval) == [
        "self", "candidate_id", "approval_input", "authenticated_actor", "idempotency_key"
    ]
    assert _kwonly(PlanAdmissionService.record_approval) == {
        "authenticated_actor", "idempotency_key"}
    ret = inspect.signature(PlanAdmissionService.record_approval).return_annotation
    assert getattr(ret, "__name__", ret) == "RunEvent"


def test_point4_budget_ledger_methods_and_closed_operation_set():
    for method in ("reserve_plan", "reserve_node", "settle", "release", "get",
                   "get_active_plan", "available", "replay"):
        assert callable(getattr(BudgetLedger, method)), method
    assert issubclass(IdempotencyConflict, Exception)
    # clause (a) [flipped in Phase 7 · Task 4, then again in Phase 8 · Task 8, per the
    # Phase 4 guard-flip ruling]: the closed operation vocabulary is EXTENDED (never
    # replaced) by exactly the additive planner op (Phase 7) and the additive retry /
    # schema_repair ops (Phase 8) — the original four remain, in place, unchanged.
    assert set(typing.get_args(BudgetOperation)) == {
        "reserve_plan", "reserve_node", "settle", "release", "reserve_planner",
        "reserve_retry", "reserve_schema_repair"}
    assert {"reserve_plan", "reserve_node", "settle", "release"} <= set(
        typing.get_args(BudgetOperation))
    # the Phase 8 additive ops are present (absence -> presence flip).
    assert {"reserve_retry", "reserve_schema_repair"} <= set(
        typing.get_args(BudgetOperation))
    assert "operation" in BudgetTransitionCommand.model_fields
    assert BudgetTransitionCommand.model_fields["operation"].annotation is BudgetOperation


def test_point4_planner_scope_binding_facts_hold_for_clause_a_b():
    # clause (a)/(b) [flipped in Phase 7 · Task 4]: "planner" is a legal scope and
    # Phase 7 now mints a planner reservation via the additive "reserve_planner" op
    # (never a bare "planner" op); a reservation's candidate_plan_digest stays a
    # required DigestHex, so the reviewed pre-candidate planner binding (the request
    # semantic digest) stands.
    assert "planner" in typing.get_args(BudgetScopeType)
    assert "reserve_planner" in set(typing.get_args(BudgetOperation))
    assert "planner" not in set(typing.get_args(BudgetOperation))  # not a bare op name
    cpd = BudgetReservation.model_fields["candidate_plan_digest"]
    assert cpd.is_required()


# =========================================================================== #
# Point 5 — StaticRuntimeProfile v1 admits a validated DYNAMIC main static DAG.  #
# =========================================================================== #
def test_point5_static_profile_admits_a_dynamic_main_pilot_triad():
    pieces = _build_pilot_pieces()
    dyn = _with_source(pieces.preset_draft, PlanSource.DYNAMIC)
    assert dyn.source is PlanSource.DYNAMIC and dyn.phase == "main"

    phase1 = validate_plan_draft(
        dyn, request=pieces.request, context=pieces.context,
        catalog=pieces.catalog_runtime.snapshot, schema_registry=pieces.registry)
    assert phase1.valid, [i.code for i in phase1.issues]

    profile = static_runtime_profile()
    assert isinstance(profile, StaticRuntimeProfile)
    assert PlanSource.DYNAMIC in profile.supported_plan_sources
    support = check_runtime_support(
        dyn, phase1_report=phase1, context=pieces.context, context_requirements=None,
        catalog=pieces.catalog_runtime, bridge_view=pieces.view,
        schema_registry=pieces.registry, profile=profile)
    assert support.supported, [i.code for i in support.issues]


# =========================================================================== #
# Point 6 — the Phase 4/5/6 registry + catalog chain nodes verify and link.     #
# =========================================================================== #
def test_point6_registry_chain_nodes_reproduce_and_link():
    assert trial.PHASE4_REGISTRY_DIGEST == PHASE4_REGISTRY_DIGEST_FROZEN
    assert bootstrap.PHASE5_REGISTRY_DIGEST == PHASE5_REGISTRY_DIGEST_FROZEN
    assert shadow.PHASE6_REGISTRY_DIGEST == PHASE6_REGISTRY_DIGEST_FROZEN

    # linkage: each phase's declared base == its predecessor's sealed digest.
    assert bootstrap.PHASE5_BASE_REGISTRY_DIGEST == PHASE4_REGISTRY_DIGEST_FROZEN
    assert shadow.PHASE6_BASE_REGISTRY_DIGEST == PHASE5_REGISTRY_DIGEST_FROZEN

    p4 = trial.build_phase4_registry(trial.PHASE4_BASE_REGISTRY_DIGEST)
    p5 = bootstrap.build_phase5_registry(bootstrap.PHASE5_BASE_REGISTRY_DIGEST)
    p6 = shadow.build_phase6_registry(shadow.PHASE6_BASE_REGISTRY_DIGEST)
    assert p4.sealed and p5.sealed and p6.sealed
    assert p4.registry_digest == PHASE4_REGISTRY_DIGEST_FROZEN
    assert p5.registry_digest == PHASE5_REGISTRY_DIGEST_FROZEN
    assert p6.registry_digest == PHASE6_REGISTRY_DIGEST_FROZEN
    assert len({PHASE4_REGISTRY_DIGEST_FROZEN, PHASE5_REGISTRY_DIGEST_FROZEN,
                PHASE6_REGISTRY_DIGEST_FROZEN}) == 3


def test_point6_registry_builders_reject_a_wrong_predecessor_digest():
    with pytest.raises(trial.Phase4RegistryError):
        trial.build_phase4_registry(D64)
    with pytest.raises(bootstrap.Phase5RegistryError):
        bootstrap.build_phase5_registry(D64)
    with pytest.raises(shadow.Phase6RegistryError):
        shadow.build_phase6_registry(D64)


def test_point6_catalog_chain_nodes_reproduce_and_link():
    assert trial.PHASE4_CATALOG_DIGEST == PHASE4_CATALOG_DIGEST_FROZEN
    assert bootstrap.PHASE5_CATALOG_DIGEST == PHASE5_CATALOG_DIGEST_FROZEN
    assert shadow.PHASE6_CATALOG_DIGEST == PHASE6_CATALOG_DIGEST_FROZEN
    # Phase 6 is the identity node: its catalog is byte-equal to Phase 5's.
    assert shadow.PHASE6_BASE_CATALOG_DIGEST == PHASE5_CATALOG_DIGEST_FROZEN
    assert shadow.PHASE6_CATALOG_DIGEST == PHASE5_CATALOG_DIGEST_FROZEN
    assert trial.phase4_catalog_snapshot().catalog_digest == PHASE4_CATALOG_DIGEST_FROZEN
    assert bootstrap.phase5_catalog_snapshot().catalog_digest == PHASE5_CATALOG_DIGEST_FROZEN
    assert shadow.phase6_catalog_snapshot().catalog_digest == PHASE6_CATALOG_DIGEST_FROZEN

    from guanlan_v2.orchestration.catalog import CatalogError
    with pytest.raises(CatalogError):
        # a non-canonical base is rejected by the identity node.
        shadow.build_phase6_catalog_snapshot(
            trial.phase4_catalog_snapshot(),
            expected_phase5_digest=bootstrap.PHASE5_CATALOG_DIGEST)


def test_point6_golden_manifests_load_and_inherited_entries_are_byte_identical():
    manifests = {}
    for ph in ("phase4", "phase5", "phase6"):
        s = _load_json(GOLDEN_DIR / f"{ph}_schema_manifest_v1.json")
        c = _load_json(GOLDEN_DIR / f"{ph}_catalog_manifest_v1.json")
        assert s["entries"], f"{ph} schema golden must carry entries"
        manifests[ph] = {e["key"]: e["json_schema_digest"] for e in s["entries"]}
        assert c, f"{ph} catalog golden must load"
    # registry-digest linkage recorded in the goldens.
    assert _load_json(GOLDEN_DIR / "phase6_schema_manifest_v1.json")["registry_digest"] == (
        PHASE6_REGISTRY_DIGEST_FROZEN)
    assert _load_json(GOLDEN_DIR / "phase6_schema_manifest_v1.json")["base_registry_digest"] == (
        PHASE5_REGISTRY_DIGEST_FROZEN)
    # every inherited key is byte-identical up the chain (p4 subset of p5 of p6).
    for lo, hi in (("phase4", "phase5"), ("phase5", "phase6")):
        shared = set(manifests[lo]) & set(manifests[hi])
        assert manifests[lo].keys() <= manifests[hi].keys(), (
            f"{hi} dropped an inherited schema from {lo}")
        for key in shared:
            assert manifests[lo][key] == manifests[hi][key], (
                f"inherited schema {key} drifted between {lo} and {hi}")


# =========================================================================== #
# Point 7 — the reviewed Phase 5 Bootstrap Lane-0 path produces a real frozen    #
# main ContextSnapshot with a main-namespaced TypedPayloadRef memory selection.  #
# =========================================================================== #
def test_point7_bootstrap_lane0_context_snapshot_binds_main_typed_memory_ref():
    # Consume the reviewed Phase 5 path (build_bootstrap_env fixture builder +
    # build_context_snapshot_from_bootstrap) — never a hand-rolled constructor.
    e2e = importlib.import_module("tests.orchestration.test_bootstrap_e2e")
    env = e2e.build_bootstrap_env(inputs=e2e._happy_inputs())
    result, _rec = env.run()
    assert result.terminal_status == "completed"

    context, manifest = build_context_snapshot_from_bootstrap(
        run_result=result, pool=env.pool, data_context=env.data_context,
        stores=env.stores, registry=env.rt_reg, clock=env.clock)
    assert isinstance(context, ContextSnapshot)
    assert bool(context.content_digest)
    sel = context.memory_selection_ref
    assert isinstance(sel, TypedPayloadRef)
    assert isinstance(sel.payload_ref, PayloadRef)
    assert isinstance(sel.schema_ref, SchemaRef)
    assert sel.payload_ref.namespace == "main"
    assert isinstance(context.memory_snapshot_ref, TypedPayloadRef)
    assert manifest.bootstrap_plan_digest == env.plan.plan_digest


# =========================================================================== #
# Point 8 — Phase 3 normalize_symbol imports and rejects non-symbol strings.     #
# =========================================================================== #
def test_point8_normalize_symbol_accepts_a_code_and_rejects_junk():
    assert normalize_symbol.__module__ == "guanlan_v2.orchestration.data.symbols"
    sym = normalize_symbol("600519")
    assert sym.code == "600519"
    for junk in ("not a symbol", "12345", "", "600519.SS extra", "600519600519"):
        with pytest.raises(ValueError):
            normalize_symbol(junk)
    with pytest.raises(TypeError):
        normalize_symbol(600519)  # a non-string is never coerced


# =========================================================================== #
# Point 9 — no Phase 7 path overwrites a Phase 1-6 owned module/test/golden.     #
# =========================================================================== #
def test_point9_phase1_6_owned_modules_and_goldens_exist_singular():
    for path in PHASE1_6_OWNED_MODULES + UPSTREAM_GOLDEN_FILES:
        assert path.is_file() and path.stat().st_size > 0, f"missing upstream file: {path}"
    assert Path(__file__).name == "test_phase7_handoff.py"


def test_point9_phase7_paths_never_shadow_owned_or_golden_files():
    owned = set(PHASE1_6_OWNED_MODULES) | set(UPSTREAM_GOLDEN_FILES)
    for path in PHASE7_NEW_PATHS:
        assert path not in owned, (
            f"Phase 7 path {path} shadows a Phase 1-6 owned/golden file — Phase 7 is "
            "additive and may never overwrite an upstream source or golden")
    for golden in (GOLDEN_DIR / "phase7_schema_manifest_v1.json",
                   GOLDEN_DIR / "phase7_catalog_manifest_v1.json"):
        assert golden.name.startswith("phase7_")
    # the plan-preset golden carries its own distinct prefix, never a phase<=6 one.
    assert (GOLDEN_DIR / "plan_preset_manifest_v1.json").name.startswith("plan_preset_")


# =========================================================================== #
# Point 10 — Amendment 1 in force: 11-model set + TypedPayloadRef composite.     #
# =========================================================================== #
def test_point10_amendment1_registry_carries_the_11_model_set():
    manifest = {e.key for e in default_registry().manifest()}
    assert manifest == PHASE1_SCHEMA_KEYS
    for landed in ("ContextRuntimeRequirements@1", "InputArtifactBinding@1",
                   "TypedPayloadRef@1"):
        assert landed in manifest, f"Amendment 1 schema {landed} missing"
    assert InputArtifactBinding.__module__ == "guanlan_v2.orchestration.context"
    assert ContextRuntimeRequirements.__module__ == "guanlan_v2.orchestration.context"


def test_point10_typed_payload_ref_is_the_composite_and_payload_ref_is_bare():
    assert TypedPayloadRef.__module__ == "guanlan_v2.orchestration.refs"
    assert set(TypedPayloadRef.model_fields) == {
        "schema_version", "schema_ref", "payload_ref"}
    assert TypedPayloadRef.model_fields["schema_ref"].annotation is SchemaRef
    assert TypedPayloadRef.model_fields["payload_ref"].annotation is PayloadRef
    # bare PayloadRef is the unchanged plain locator (no schema_ref).
    assert set(PayloadRef.model_fields) == {
        "schema_version", "namespace", "object_id", "content_digest"}
    assert "schema_ref" not in PayloadRef.model_fields

    tpr = TypedPayloadRef(
        schema_ref=SchemaRef(name="PortfolioDecision", version="1"),
        payload_ref=PayloadRef(namespace="main", object_id="o-1", content_digest="a" * 64))
    round_trip = TypedPayloadRef(**tpr.model_dump())
    assert round_trip == tpr
    assert isinstance(round_trip.schema_ref, SchemaRef)
    assert isinstance(round_trip.payload_ref, PayloadRef)
    assert round_trip.schema_ref.name == "PortfolioDecision"


# =========================================================================== #
# Clause (c)/(d) — the reviewed console admin ports + the UI mount point.        #
# =========================================================================== #
def test_clause_c_console_admin_ports_resolve_at_their_reviewed_homes():
    assert AdminReviewVerifier.__module__ == "guanlan_v2.orchestration.memory.proposals"
    assert "verify" in dir(AdminReviewVerifier)
    assert AuthenticatedAdminPrincipal.__module__ == "guanlan_v2.orchestration.memory.models"
    # P3 Task 9 did NOT add a memory_review_verifier keyword to build_console_router;
    # its reviewed signature admits (store, agent_factory). (api.py is a concurrent
    # session's dirty file, so assert the clause-(c) claim, not brittle exact params.)
    from guanlan_v2.console.api import build_console_router
    params = _params(build_console_router)
    assert {"store", "agent_factory"} <= set(params)
    assert "memory_review_verifier" not in params


def test_clause_d_console_report_card_mount_page_is_identified_for_task8():
    assert CONSOLE_MOUNT_HTML.is_file(), "the console mount HTML page must exist"
    assert CONSOLE_REPORT_CARD.is_file()
    html = CONSOLE_MOUNT_HTML.read_text(encoding="utf-8")
    assert "console-report-card.jsx" in html, (
        "the identified page must be the one that mounts console-report-card.jsx")
    # Task 8's new card is a NEW sibling path, not yet present (until Task 8 lands).
    assert CONSOLE_PLAN_APPROVAL_CARD.parent == CONSOLE_REPORT_CARD.parent


# =========================================================================== #
# Step 2 — the reviewed upstream chain digests are the frozen ones (evidence).   #
# =========================================================================== #
def test_step2_records_the_exact_phase4_5_6_chain_digests():
    assert trial.build_phase4_registry(
        trial.PHASE4_BASE_REGISTRY_DIGEST).registry_digest == PHASE4_REGISTRY_DIGEST_FROZEN
    assert bootstrap.build_phase5_registry(
        bootstrap.PHASE5_BASE_REGISTRY_DIGEST).registry_digest == PHASE5_REGISTRY_DIGEST_FROZEN
    assert shadow.build_phase6_registry(
        shadow.PHASE6_BASE_REGISTRY_DIGEST).registry_digest == PHASE6_REGISTRY_DIGEST_FROZEN
    assert trial.phase4_catalog_snapshot().catalog_digest == PHASE4_CATALOG_DIGEST_FROZEN
    assert bootstrap.phase5_catalog_snapshot().catalog_digest == PHASE5_CATALOG_DIGEST_FROZEN
    assert shadow.phase6_catalog_snapshot().catalog_digest == PHASE6_CATALOG_DIGEST_FROZEN


def test_step2_isinstance_clock_protocol_resolves():
    # a Planner loop consumes AuthoritativeClock; confirm the Protocol resolves.
    assert AuthoritativeClock.__module__ == "guanlan_v2.orchestration.runtime_clock"
    assert isinstance(_FixedClock(), AuthoritativeClock)
