# -*- coding: utf-8 -*-
"""Phase 10 · Task 3 — the sealed per-code screening lane preset + lease-governed
batch builder + whole-picture cost preview.

Task 6 is the pathfinder; this suite mirrors its reconciled idioms and adds the
batch/lease surface. What was read at source BEFORE the implementation (the full
table lives in ``.superpowers/sdd/task-3-report.md``):

* **The v2 preset generation is Task 6's, relocated into ``pipeline.assembly``** —
  ``PlanPresetRecordV2`` / ``load_phase10_preset_registry`` /
  ``PHASE10_PRESETS_V2_DIR`` / ``PLAN_PRESET_RECORD_V2_SCHEMA_REF``. The committed
  file MUST live in ``config/orchestration/presets/v2/``: the Phase-7 loader globs
  ``root/*.json`` non-recursively (plan_presets.py:277) and hard-fails on a
  ``schema_version="2"`` file at the root.
* **The debate expression** — seat identity ``debate_id``/``round_role``/
  ``debate_round``/``debate_turn`` (spec.py:318-321), ``opponent_case`` BLOCK
  chaining, ONE shared transcript slot reduced by ``debate.transcript_reducer@1``,
  seats ``auxiliary=True``; the judge binds through ``DebateCfg.judge_node_id``
  and the folded transcript is deliberately NOT a ``Dependency`` — Phase 1's
  dependency check compares the UPSTREAM WORKER'S OUTPUT schema against the input
  (spec.py:995-999), so a reducer's ``DebateTranscript@1`` is invisible to it and
  ``dec.research_mgr``'s optional ``bullbear_transcript`` input cannot be fed from
  a seat node (``BearCase@1 != DebateTranscript@1``).
* **The worker set is frozen against executed validation-green evidence.** The
  brief's five evidence workers + bull/bear + ``dec.research_mgr``, and —
  exactly as Task 6 found for ``dec.pm`` — ``dec.research_mgr`` REQUIRES
  ``sentiment: SentimentReport@1``, so ``text.sentiment`` joins: NINE workers.
* **The subject is run-scoped (Amendment 3)**: a committed ``RunSubject@1`` per
  code, carried beside the draft; no ``InputArtifactBinding`` is forged and no
  subject worker is invented.
* **The lease channel is structurally PRESET_FALLBACK-only** —
  ``PendingPlanApproval`` accepts only ``DYNAMIC``/``PRESET_FALLBACK``
  (plan_diff.py:261-265) and ``_find_admissible_lease`` requires preset
  provenance, which the card carries iff the source is ``PRESET_FALLBACK``
  (plan_diff.py:283-293). ``TestTheSourceIsForced`` pins that, and the request the
  builder derives genuinely names this preset in ``fallback_preset_id``, so the
  provenance in front of the reviewer is true rather than relabelled
  (adapters/replay_cards.py:284-292 is the reviewed refusal of relabelling).

Everything here runs against the REAL sealed Phase-9 catalog + registry. The only
synthetic object is the trimmed bridge-free catalog (the reviewed
test_phase8_e2e:276-322 stand-in, re-used by Task 6): ``text.news`` declares
``tool_calls=REQUIRED`` while the reviewed data prefetch grants a row to
``dec.pm`` only (data/catalog.py:95-97), so no plan containing it can satisfy
``check_runtime_support`` under the sealed catalog. That gap is named, never
faked — and it is why the real ``PlanAdmissionService`` used by the admission
tests is bound to the trimmed catalog.

Run: ``python -m pytest tests/orchestration/test_pipeline_screening.py -v``
"""
from __future__ import annotations

import ast
import codecs
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

import guanlan_v2.orchestration.lane_catalog as lc
import guanlan_v2.orchestration.worker as W
from guanlan_v2.orchestration import presets as P
from guanlan_v2.orchestration.adapters import chain
from guanlan_v2.orchestration.admission import AdmissionError, PlanAdmissionService
from guanlan_v2.orchestration.approval import (
    ApprovalJournalRow,
    LeaseAdmissionOutcome,
    PlanApprovalCoordinator,
)
from guanlan_v2.orchestration.catalog import (
    ContentManifestEntry,
    EvidencePolicy,
    SkillManifest,
    build_catalog_snapshot,
)
from guanlan_v2.orchestration.catalog_runtime import (
    BridgeCatalogView,
    CatalogRuntime,
    InMemoryMaterialSource,
)
from guanlan_v2.orchestration.budget import BudgetLedger
from guanlan_v2.orchestration.context import RunBudget
from guanlan_v2.orchestration.debate import DEBATE_MAX_ROUNDS, analyze_debates
from guanlan_v2.orchestration.digest import canonical_json
from guanlan_v2.orchestration.enums import (
    ApprovalDecision,
    ApprovalPolicy,
    ExecutionKind,
    NodeStatus,
    PlanSource,
    PortfolioRating,
    ToolCallRequirement,
)
from guanlan_v2.orchestration.events import CommittedArtifactRef, LayerCommit
from guanlan_v2.orchestration.eventstore import (
    EventAppendCommand,
    EventStoreError,
    PayloadPutCommand,
    RuntimeBatch,
    RuntimeStores,
    SchemaRegistryResolver,
    StagedPayloadKey,
)
from guanlan_v2.orchestration.market import factors as market_factors
from guanlan_v2.orchestration.memory.models import AuthenticatedAdminPrincipal
from guanlan_v2.orchestration.plan_diff import (
    PLAN_DIFF_SCHEMA_REF,
    build_pending_plan_approval,
    build_plan_diff,
)
from guanlan_v2.orchestration.plan_presets import (
    PlanPresetError,
    PlanPresetRecord,
    PlanPresetRegistry,
    load_preset_registry,
)
from guanlan_v2.orchestration.refs import PayloadRef, SchemaRef, TypedPayloadRef
from guanlan_v2.orchestration.runtime_contracts import static_runtime_profile
from guanlan_v2.orchestration.runtime_support import (
    STATIC_RUNTIME_PROFILE_V2,
    analyze_reducers,
    analyze_retry_repair,
    check_runtime_support,
)
from guanlan_v2.orchestration.schemas import NodeRun, PortfolioDecision, ResearchPlan
from guanlan_v2.orchestration.skilltree import parse_skill_v1
from guanlan_v2.orchestration.spec import (
    OrchestrationRequest,
    compute_candidate_plan_digest,
    validate_plan_draft,
)

from guanlan_v2.orchestration.data.runtime import SubjectParams
from guanlan_v2.orchestration.pipeline.assembly import (
    PHASE10_PRESETS_V2_DIR,
    PRODUCTION_PRESETS_DIR,
    PlanPresetRecordV2,
    build_phase10_preset_registry,
    load_phase10_preset_registry,
)
from guanlan_v2.orchestration.pipeline.contracts import (
    NO_CROSS_SECTIONAL_SUMMARY_BADGE,
    CandidateEntry,
    CandidateSlate,
    RecommendationEntry,
    RecommendationSlate,
    RunSubject,
)
from guanlan_v2.orchestration.pipeline import screening as screening_mod
from guanlan_v2.orchestration.pipeline.screening import (
    AUXILIARY_EVIDENCE_UNWIRED_BADGE,
    CONTEXT_SNAPSHOT_DATA_DATE_BADGE_PREFIX,
    CONTEXT_SNAPSHOT_STALE_BADGE,
    LANE_TERMINAL_DEGRADED_BADGE_PREFIX,
    RECOMMENDATION_ADVISORY_BANNER,
    RECOMMENDATION_ARCHIVE_ARTIFACT_TYPE,
    RECOMMENDATION_ARCHIVE_ID_PREFIX,
    RESEARCH_PLAN_SCHEMA_REF,
    REVIEWED_RATING_ORDER,
    SCREENING_LANE_DEBATE_ID,
    SCREENING_LANE_PRESET_FILE,
    SCREENING_LANE_PRESET_ID,
    SCREENING_LANE_TERMINAL_NODE_ID,
    SCREENING_LANE_WORKER_IDS,
    SUBJECT_RUN_SCOPED_BADGE,
    BaseRequestRefused,
    BatchAdmissionRefused,
    ClockRefused,
    ContextSnapshotRefused,
    EmptySlateRefused,
    MaterializedScreeningBatch,
    MaterializedScreeningLane,
    ScreeningBatch,
    ScreeningCostPreview,
    ScreeningError,
    SlateRefRefused,
    SubjectRefused,
    admit_screening_batch,
    build_recommendation_slate,
    build_screening_batch,
    land_recommendation,
    recommendation_archive_id,
    recommendation_sort_key,
    render_recommendation_md,
    screening_cost_preview,
    session_date_of,
)

UTC = timezone.utc
#: the frozen session clock (2026-07-24 07:00Z == 2026-07-24 15:00 +08:00).
NOW = datetime(2026, 7, 24, 7, 0, tzinfo=UTC)
VALID_FROM = NOW - timedelta(days=1)
VALID_UNTIL = NOW + timedelta(days=1)
PHASE8_REGISTRY_DIGEST = (
    "d719e19bc8c64f56324ee36ca0d3aa039e5eac1c9488d80babe6ddce81e5e089"
)
PHASE7_BASELINE = "main.research_baseline"
DEEP_DECIDE_PRESET_ID = "pipeline.luozi_deep_decide"
GOOD_CRED = "good-cred"

#: The golden record digest of the sealed screening-lane preset. ANY graph edit
#: (node, dependency, debate, reducer, sink, budget, description) moves it.
SCREENING_LANE_RECORD_DIGEST = (
    "f739f7fcce79939d23e282f6b7819a43cf5d56038825cfaf189df221e4c535ec"
)
#: The Phase-7 baseline record digest — must stay byte-identical after Phase 10
#: adds a SECOND committed v2 preset ("Phase 7 preset golden byte-identical").
PHASE7_BASELINE_RECORD_DIGEST = (
    "028ff5246ac97988ec2b778c994fe8a782c776098c1edb7d725d646ceff6704e"
)

#: the three fixture codes (rank order).
CODES = ("600519", "000001", "300750")


# =========================================================================== #
# doubles                                                                      #
# =========================================================================== #
class _FixedClock:
    def __init__(self, now: datetime = NOW) -> None:
        self._now = now

    def now(self) -> datetime:
        return self._now


class _VerifyDenied(Exception):
    """The fail-closed verifier's refusal."""


class _Verifier:
    """Mirrors the reviewed ``AdminReviewVerifier.verify`` port (fail-closed)."""

    def __init__(self, actor: str = "human-approver", *, good: str = GOOD_CRED) -> None:
        self._actor = actor
        self._good = good

    def verify(self, credential) -> AuthenticatedAdminPrincipal:
        if credential != self._good:
            raise _VerifyDenied(f"unverifiable credential {credential!r}")
        return AuthenticatedAdminPrincipal(actor=self._actor, verified_by="t3-verifier")


class _SubjectCommitter:
    """The injected subject-commit port.

    ``RunSubject@1`` is NOT registered in the sealed Phase-9 registry (Task 11
    registers the Phase-10 contracts), so a real ``stores.payloads.put`` cannot
    commit one yet. This double is the seam that stands in for it: it returns a
    typed ref whose payload content digest genuinely binds the subject.
    """

    def __init__(self) -> None:
        self.calls: list[RunSubject] = []

    def __call__(self, subject: RunSubject) -> TypedPayloadRef:
        self.calls.append(subject)
        return TypedPayloadRef(
            schema_ref=SchemaRef(name="RunSubject", version="1"),
            payload_ref=PayloadRef(
                namespace="main", object_id=f"subject-{subject.code}",
                content_digest=subject.semantic_digest()))


class _LyingSubjectCommitter:
    """A port that returns a ref which does NOT bind the subject it was handed."""

    def __call__(self, subject: RunSubject) -> TypedPayloadRef:
        return TypedPayloadRef(
            schema_ref=SchemaRef(name="RunSubject", version="1"),
            payload_ref=PayloadRef(namespace="main", object_id="wrong",
                                   content_digest="f" * 64))


# =========================================================================== #
# fixtures — the REAL sealed Phase-9 catalog / registry / context               #
# =========================================================================== #
def _slate(codes=CODES, *, as_of: datetime = NOW, top_n: int = 5) -> CandidateSlate:
    return CandidateSlate(
        source_kind="v4", as_of=as_of, top_n=top_n,
        entries=tuple(
            CandidateEntry(code=c, rank=i + 1, score=1.0 - i / 10)
            for i, c in enumerate(codes)))


def _slate_ref(slate: CandidateSlate) -> TypedPayloadRef:
    return TypedPayloadRef(
        schema_ref=SchemaRef(name="CandidateSlate", version="1"),
        payload_ref=PayloadRef(
            namespace="main", object_id="slate-1",
            content_digest=slate.semantic_digest()))


def _base_request(
    *, request_id: str = "req-screen-1",
    fallback_preset_id: str | None = SCREENING_LANE_PRESET_ID,
    approval_policy: ApprovalPolicy = ApprovalPolicy.REQUIRED,
    goal: str = "观澜 · 选股批量研判",
) -> OrchestrationRequest:
    return OrchestrationRequest(
        request_id=request_id, goal=goal, workflow="orchestrate_only",
        fallback_preset_id=fallback_preset_id, approval_policy=approval_policy)


@pytest.fixture(scope="module")
def env():
    """The real sealed Phase-9 catalog + registry + an empty-memory ContextSnapshot."""
    registry = chain.build_phase9_registry(PHASE8_REGISTRY_DIGEST)
    snapshot = chain.phase9_catalog_snapshot()
    resolver = SchemaRegistryResolver()
    resolver.register(registry)
    stores = RuntimeStores(
        resolver=resolver, clock=_FixedClock(),
        allowed_cell_namespaces=(W.PROMPT_CELL_NAMESPACE,))
    context = P.build_empty_memory_context(
        data_context=P.pilot_data_context(as_of=NOW), stores=stores,
        registry_digest=registry.registry_digest, built_at=NOW).context
    ctx_ref = PayloadRef(namespace="main", object_id="ctx-screen-1",
                         content_digest=context.content_digest)
    return {
        "registry": registry, "snapshot": snapshot, "context": context,
        "ctx_ref": ctx_ref,
    }


def _build(env, *, slate=None, base_request=None, clock=None, context=...,
           ctx_ref=..., slate_ref=..., committer=None, catalog=None,
           preset_registry=None) -> MaterializedScreeningBatch:
    slate = _slate() if slate is None else slate
    return build_screening_batch(
        slate,
        preset_registry=(preset_registry
                         or load_phase10_preset_registry(PRODUCTION_PRESETS_DIR)),
        clock=clock or _FixedClock(),
        base_request=base_request if base_request is not None else _base_request(),
        slate_ref=_slate_ref(slate) if slate_ref is ... else slate_ref,
        subject_committer=committer or _SubjectCommitter(),
        context=env["context"] if context is ... else context,
        context_snapshot_ref=env["ctx_ref"] if ctx_ref is ... else ctx_ref,
        catalog=catalog if catalog is not None else env["snapshot"],
        schema_registry=env["registry"],
    )


def _fresh_context(env, *, as_of: datetime):
    """A second REAL empty-memory ContextSnapshot at another instant (a refreshed
    Lane-0 context, exactly what a new session produces)."""
    resolver = SchemaRegistryResolver()
    resolver.register(env["registry"])
    stores = RuntimeStores(
        resolver=resolver, clock=_FixedClock(as_of),
        allowed_cell_namespaces=(W.PROMPT_CELL_NAMESPACE,))
    context = P.build_empty_memory_context(
        data_context=P.pilot_data_context(as_of=as_of), stores=stores,
        registry_digest=env["registry"].registry_digest, built_at=as_of).context
    return {
        "context": context,
        "ctx_ref": PayloadRef(namespace="main", object_id="ctx-screen-2",
                              content_digest=context.content_digest),
    }


@pytest.fixture(scope="module")
def built(env):
    return _build(env)


# =========================================================================== #
# the full Phase-9 CatalogRuntime (every layer's material loader, unioned)      #
# =========================================================================== #
def _full_phase9_runtime(snapshot):
    """Task 6's recipe (its concern 4), re-used verbatim: the nine owning-module
    loaders, filtered to the snapshot's referenced refs."""
    import guanlan_v2.orchestration.bootstrap as bs
    import guanlan_v2.orchestration.data.catalog as dcat
    import guanlan_v2.orchestration.memory.catalog as mcat
    import guanlan_v2.orchestration.phase7_registry as p7
    import guanlan_v2.orchestration.trial as trial

    text: dict = {}
    caps: dict = {}

    def add(materials):
        for m in materials:
            if hasattr(m, "raw_utf8"):
                text[(m.ref.id, m.ref.version)] = m.raw_utf8
            elif hasattr(m, "descriptor"):
                caps[(m.ref.id, m.ref.version)] = m.descriptor

    add(lc.load_phase8_lane_materials())
    add(p7.load_planner_materials())
    _replay, _live, source_materials = chain.build_phase9_source_materials()
    add(source_materials)
    data_surface = dcat.phase3_data_surface()
    add(data_surface.text_materials())
    add(data_surface.capability_materials)
    memory_surface = mcat.phase3_memory_surface()
    add(memory_surface.text_materials())
    add([memory_surface.proposal_capability_material])
    add(bs.load_lane0_catalog().resolved)
    add(bs.factor_miner_placeholder().resolved)
    pilot_text, pilot_caps = P.load_pilot_catalog().resolved_materials()
    add(pilot_text)
    add(pilot_caps)
    add(P._compat_materials()["text_materials"])
    add([trial.joint_gate_material()[1]])

    want_text = {(e.ref.id, e.ref.version) for e in snapshot.content_manifest} | {
        (e.ref.id, e.ref.version) for e in snapshot.skill_manifest}
    want_caps = {(e.ref.id, e.ref.version) for e in snapshot.capability_manifest}
    source = InMemoryMaterialSource(
        text={k: v for k, v in text.items() if k in want_text},
        capabilities={k: v for k, v in caps.items() if k in want_caps})
    return CatalogRuntime.build(snapshot, source)


@pytest.fixture(scope="module")
def phase9_runtime(env):
    return _full_phase9_runtime(env["snapshot"])


# =========================================================================== #
# the trimmed BRIDGE-FREE catalog (reviewed test_phase8_e2e:276-322 idiom)      #
# =========================================================================== #
@pytest.fixture(scope="module")
def trimmed_catalog():
    """The nine real WorkerSpecs with their capability-bridge binding stood down.

    ``text.news`` declares ``tool_calls=REQUIRED`` and the reviewed data prefetch
    grants a row to ``dec.pm`` ONLY (data/catalog.py:95-97
    ``_REVIEWED_INTEGRATION_GRANTS``), so under the sealed catalog its tool-call
    arithmetic is structurally unsatisfiable — Task 6's concern 2, inherited. The
    debate/graph topology under test is orthogonal to it.
    """
    text_m = lc.load_text_lane_materials()
    pv_m = lc.load_pv_lane_materials()
    quant_m = lc.load_quant_lane_materials()
    dec_m = lc.load_decision_lane_materials()
    specs: dict = {}
    specs.update({w.id: w for w in lc.build_text_worker_specs(materials=text_m)})
    specs.update({w.id: w for w in lc.build_pv_worker_specs(materials=pv_m)})
    specs.update({w.id: w for w in lc.build_quant_worker_specs(materials=quant_m)})
    specs.update({w.id: w for w in lc.build_decision_worker_specs(materials=dec_m)})

    workers = []
    for wid in SCREENING_LANE_WORKER_IDS:
        spec = specs[wid]
        update: dict = {}
        if spec.capability_allowlist:
            update["capability_allowlist"] = ()
        if spec.evidence_policy.tool_calls is ToolCallRequirement.REQUIRED:
            update["evidence_policy"] = EvidencePolicy(
                tool_calls=ToolCallRequirement.OPTIONAL)
        workers.append(spec.model_copy(update=update) if update else spec)

    reducer_material = lc._debate_reducer_material()
    by_key = {}
    for m in list(text_m) + list(pv_m) + list(quant_m) + list(dec_m) + [
            reducer_material]:
        by_key[m.ref_key] = m
    materials = tuple(by_key.values())

    content, skills = [], []
    for m in materials:
        if m.kind == "skill":
            parsed = parse_skill_v1(m.raw_utf8.decode("utf-8"))
            skills.append(SkillManifest(
                ref=m.ref, name=parsed.name, summary=parsed.summary,
                perfect_for=parsed.perfect_for, not_ideal_for=parsed.not_ideal_for,
                critical_data_source_heading="⚠️ CRITICAL: Data Source Priority",
                source_identity=m.ref.id))
        else:
            content.append(ContentManifestEntry(
                ref=m.ref, kind=m.kind, name="x", description="d",
                source_identity="gl"))

    snapshot = build_catalog_snapshot(
        catalog_version="screening-lane-support-v1", content_manifest=tuple(content),
        skill_manifest=tuple(skills), capability_manifest=(), workers=tuple(workers),
        resolved_material=materials)
    source = InMemoryMaterialSource(
        text={(m.ref.id, m.ref.version): m.raw_utf8 for m in materials},
        capabilities={})
    runtime = CatalogRuntime.build(snapshot, source)
    return snapshot, runtime, BridgeCatalogView.build(runtime, {})


# =========================================================================== #
# the admission environment (REAL PlanAdmissionService + REAL coordinator)      #
# =========================================================================== #
class _AdmitEnv:
    def __init__(self, **kw):
        self.__dict__.update(kw)


@pytest.fixture
def admit_env(env, trimmed_catalog, tmp_path):
    """A real Phase-2 admission service + real Phase-7 coordinator over a 3-lane batch.

    In-memory Phase-2 stores, a fixed clock, a temp journal — the
    test_dynamic_e2e / test_phase8_e2e wiring, on the trimmed bridge-free catalog
    (see :func:`trimmed_catalog`).
    """
    snapshot, runtime, view = trimmed_catalog
    registry = env["registry"]
    clock = _FixedClock()
    resolver = SchemaRegistryResolver()
    resolver.register(registry)
    stores = RuntimeStores(
        resolver=resolver, clock=clock,
        allowed_cell_namespaces=(W.PROMPT_CELL_NAMESPACE,))
    context = P.build_empty_memory_context(
        data_context=P.pilot_data_context(as_of=NOW), stores=stores,
        registry_digest=registry.registry_digest, built_at=NOW).context
    ctx_ref = PayloadRef(namespace="main", object_id="ctx-admit",
                         content_digest=context.content_digest)
    run_id = "run-screen-1"
    run_budget = RunBudget(ledger_id="led-screen", max_tokens=60_000_000,
                           max_llm_invocations=200, max_concurrency=32)
    stores.bind_run_budget(run_id=run_id, run_budget=run_budget)

    slate = _slate()
    build = build_screening_batch(
        slate,
        preset_registry=load_phase10_preset_registry(PRODUCTION_PRESETS_DIR),
        clock=clock, base_request=_base_request(),
        slate_ref=_slate_ref(slate), subject_committer=_SubjectCommitter(),
        context=context, context_snapshot_ref=ctx_ref, catalog=snapshot,
        schema_registry=registry)

    approvals: dict = {}
    admission = PlanAdmissionService(
        run_id=run_id,
        requests={lane.request.request_id: lane.request for lane in build.lanes},
        drafts={lane.draft.id: lane.draft for lane in build.lanes},
        contexts={context.content_digest: context}, attestations={},
        approvals=approvals, catalog=runtime, bridge_view=view,
        phase1_registry=registry, runtime_registry_digest=registry.registry_digest,
        profile=STATIC_RUNTIME_PROFILE_V2, stores=stores, run_budget=run_budget,
        clock=clock)

    def _sink(approval) -> None:
        approvals[(approval.request_id, approval.candidate_plan_digest)] = approval

    emitted: list = []
    coordinator = PlanApprovalCoordinator(
        tmp_path / "plan_approvals.jsonl", admission=admission, clock=clock,
        verifier=_Verifier(), console_emit=lambda n, p: emitted.append((n, p)),
        approvals_sink=_sink,
        preset_registry=load_phase10_preset_registry(PRODUCTION_PRESETS_DIR),
        catalog_digest=snapshot.catalog_digest,
        registry_digest=registry.registry_digest)

    ledger = BudgetLedger(
        sink=stores.budget_event_sink(run_id=run_id, ledger_id=run_budget.ledger_id),
        run_budget=run_budget)

    def _service_with(*, profile=STATIC_RUNTIME_PROFILE_V2, run_budget_=None):
        """A second REAL service over the same lanes — a different profile or a
        different run budget, everything else identical."""
        rb = run_budget_ or run_budget
        stores.bind_run_budget(run_id=f"{run_id}-alt", run_budget=rb)
        return PlanAdmissionService(
            run_id=f"{run_id}-alt",
            requests={lane.request.request_id: lane.request for lane in build.lanes},
            drafts={lane.draft.id: lane.draft for lane in build.lanes},
            contexts={context.content_digest: context}, attestations={},
            approvals=approvals, catalog=runtime, bridge_view=view,
            phase1_registry=registry,
            runtime_registry_digest=registry.registry_digest,
            profile=profile, stores=stores, run_budget=rb, clock=clock)

    return _AdmitEnv(
        build=build, batch=build.batch, admission=admission,
        coordinator=coordinator, stores=stores, registry=registry,
        snapshot=snapshot, journal=tmp_path / "plan_approvals.jsonl",
        emitted=emitted, approvals=approvals, clock=clock, budget=ledger,
        run_budget=run_budget, run_id=run_id, context=context, ctx_ref=ctx_ref,
        service_with=_service_with)


def _issue_lease(admit_env, *, max_admissions=3, budget_cap=100):
    return admit_env.coordinator.issue_lease(
        purpose="daily 选股 batch",
        preset_id=SCREENING_LANE_PRESET_ID,
        preset_record_digest=admit_env.batch.preset_record_digest,
        catalog_digest=admit_env.snapshot.catalog_digest,
        registry_digest=admit_env.registry.registry_digest,
        valid_from=VALID_FROM, valid_until=VALID_UNTIL,
        max_admissions=max_admissions, budget_cap_llm_invocations=budget_cap,
        actor=GOOD_CRED, reason="reviewed whole-picture screening cost preview")


def _admit(admit_env, batch=None, *, admission=None, budget=None):
    return admit_screening_batch(
        admit_env.batch if batch is None else batch,
        coordinator=admit_env.coordinator,
        admission=admission or admit_env.admission, now=NOW,
        payloads=admit_env.stores.payloads,
        registry_digest=admit_env.registry.registry_digest,
        budget=budget if budget is not None else admit_env.budget)


def _journal_kinds(path: Path) -> list[str]:
    if not path.exists():
        return []
    raw = path.read_bytes().decode("utf-8")
    lines = [ln for ln in raw.split("\n") if ln]
    return [ApprovalJournalRow.model_validate_json(ln).row_kind for ln in lines]


# =========================================================================== #
# 1. preset load / seal / digest pin                                           #
# =========================================================================== #
class TestPresetLoadAndSeal:
    def test_the_committed_file_is_strict_utf8_without_a_bom(self):
        raw = SCREENING_LANE_PRESET_FILE.read_bytes()
        assert not raw.startswith(codecs.BOM_UTF8)
        payload = json.loads(raw.decode("utf-8"))
        assert payload["schema_version"] == "2"
        assert payload["preset_id"] == SCREENING_LANE_PRESET_ID
        # the v2/ subdirectory is LOAD-BEARING (Task 6 concern 1).
        assert SCREENING_LANE_PRESET_FILE.parent == PHASE10_PRESETS_V2_DIR
        assert PHASE10_PRESETS_V2_DIR.parent == PRODUCTION_PRESETS_DIR

    def test_the_phase10_loader_holds_all_three_committed_presets(self):
        registry = load_phase10_preset_registry(PRODUCTION_PRESETS_DIR)
        assert registry.sealed is True
        ids = {e.preset_id for e in registry.manifest()}
        assert {PHASE7_BASELINE, DEEP_DECIDE_PRESET_ID,
                SCREENING_LANE_PRESET_ID} <= ids
        record = registry.get(SCREENING_LANE_PRESET_ID)
        assert isinstance(record, PlanPresetRecordV2)
        assert record.schema_version == "2"

    def test_the_record_digest_is_golden_frozen(self):
        record = load_phase10_preset_registry(PRODUCTION_PRESETS_DIR).get(
            SCREENING_LANE_PRESET_ID)
        assert record.semantic_digest() == SCREENING_LANE_RECORD_DIGEST, (
            "the sealed screening-lane graph moved; re-review the preset before "
            "re-freezing this golden")

    def test_loading_twice_is_byte_deterministic(self):
        a = load_phase10_preset_registry(PRODUCTION_PRESETS_DIR)
        b = load_phase10_preset_registry(PRODUCTION_PRESETS_DIR)
        assert a.registry_digest == b.registry_digest
        assert a.get(SCREENING_LANE_PRESET_ID) == b.get(SCREENING_LANE_PRESET_ID)

    def test_the_phase7_loader_and_its_golden_are_untouched(self):
        """Phase 7 preset golden byte-identical — the registry-extension regression.
        The Phase-7 loader's ``root/*.json`` glob (plan_presets.py:277) never sees
        the v2 subdirectory, so adding a SECOND v2 preset changes nothing for it."""
        phase7 = load_preset_registry(PRODUCTION_PRESETS_DIR)
        assert {e.preset_id for e in phase7.manifest()} == {PHASE7_BASELINE}
        assert phase7.get(PHASE7_BASELINE).semantic_digest() == (
            PHASE7_BASELINE_RECORD_DIGEST)

    def test_a_v2_record_is_refused_by_the_phase7_record_model(self):
        raw = SCREENING_LANE_PRESET_FILE.read_text(encoding="utf-8")
        with pytest.raises(ValidationError, match="schema_version"):
            PlanPresetRecord.model_validate_json(raw)

    def test_the_phase10_registry_extension_holds_both_v2_records(self):
        base = load_preset_registry(PRODUCTION_PRESETS_DIR)
        committed = load_phase10_preset_registry(PRODUCTION_PRESETS_DIR)
        extended = build_phase10_preset_registry(
            base, (committed.get(SCREENING_LANE_PRESET_ID),
                   committed.get(DEEP_DECIDE_PRESET_ID)))
        assert extended.sealed is True
        assert {e.preset_id for e in extended.manifest()} == {
            PHASE7_BASELINE, SCREENING_LANE_PRESET_ID, DEEP_DECIDE_PRESET_ID}
        assert extended.get(SCREENING_LANE_PRESET_ID).semantic_digest() == (
            SCREENING_LANE_RECORD_DIGEST)
        assert extended.get(PHASE7_BASELINE).semantic_digest() == (
            PHASE7_BASELINE_RECORD_DIGEST)
        # the Phase-7 base object is never mutated.
        assert {e.preset_id for e in base.manifest()} == {PHASE7_BASELINE}


# =========================================================================== #
# 2. the sealed graph                                                          #
# =========================================================================== #
class TestSealedGraph:
    @pytest.fixture(scope="class")
    def record(self):
        return load_phase10_preset_registry(PRODUCTION_PRESETS_DIR).get(
            SCREENING_LANE_PRESET_ID)

    def test_the_worker_set_is_the_frozen_nine(self, record):
        assert sorted(n.worker_id for n in record.nodes) == sorted(
            SCREENING_LANE_WORKER_IDS)
        assert len(SCREENING_LANE_WORKER_IDS) == 9
        assert len(set(SCREENING_LANE_WORKER_IDS)) == 9
        assert set(SCREENING_LANE_WORKER_IDS) == {
            "text.news", "text.research_report", "quant.factor", "quant.backtest",
            "pv.price_action", "text.sentiment", "dec.bull", "dec.bear",
            "dec.research_mgr"}

    def test_text_sentiment_is_structural_not_decoration(self, env):
        """``text.sentiment`` joined the brief's five-evidence set because
        ``dec.research_mgr`` REQUIRES ``sentiment: SentimentReport@1`` and it is
        its only producer — the same forcing Task 6 recorded for ``dec.pm``."""
        workers = {w.id: w for w in env["snapshot"].workers}
        mgr_inputs = {i.name: i for i in workers["dec.research_mgr"].inputs}
        assert mgr_inputs["sentiment"].required is True
        assert mgr_inputs["sentiment"].schema_ref.key == "SentimentReport@1"
        # among the SCHEDULABLE (catalog_role='final') workers it is the only
        # producer — ``compat.news_sentiment`` also emits one but is a
        # compatibility worker no Phase-10 plan may schedule.
        producers = [w.id for w in env["snapshot"].workers
                     if w.catalog_role == "final"
                     for o in w.outputs if o.schema_ref.key == "SentimentReport@1"]
        assert producers == ["text.sentiment"]
        compat = [w.id for w in env["snapshot"].workers
                  if w.catalog_role != "final"
                  for o in w.outputs if o.schema_ref.key == "SentimentReport@1"]
        assert compat == ["compat.news_sentiment"]

    def test_research_mgr_is_the_only_terminal_and_emits_the_research_plan(
            self, record, env):
        assert record.sink_node_ids == (SCREENING_LANE_TERMINAL_NODE_ID,)
        by_id = {n.id: n for n in record.nodes}
        terminal = by_id[SCREENING_LANE_TERMINAL_NODE_ID]
        assert terminal.worker_id == "dec.research_mgr"
        worker = {w.id: w for w in env["snapshot"].workers}["dec.research_mgr"]
        assert [o.schema_ref.key for o in worker.outputs] == ["ResearchPlan@1"]

    def test_the_debate_is_one_bull_bear_round_judged_by_the_research_mgr(
            self, record):
        assert len(record.debates) == 1
        debate = record.debates[0]
        assert debate.id == SCREENING_LANE_DEBATE_ID
        assert debate.seats == ("bull", "bear")
        assert debate.turn_order == ("bull", "bear")
        assert debate.max_rounds == 1 <= DEBATE_MAX_ROUNDS
        seats = [n for n in record.nodes if n.debate_id == debate.id]
        assert {(n.round_role, n.debate_round, n.debate_turn) for n in seats} == {
            ("bull", 1, 1), ("bear", 1, 2)}
        by_id = {n.id: n for n in record.nodes}
        assert debate.judge_node_id == SCREENING_LANE_TERMINAL_NODE_ID
        assert by_id[debate.judge_node_id].debate_id is None

    def test_the_bear_answers_the_bull_through_opponent_case(self, record):
        by_id = {n.id: n for n in record.nodes}
        bear = next(n for n in record.nodes if n.round_role == "bear")
        bull = next(n for n in record.nodes if n.round_role == "bull")
        chain_dep = next(d for d in bear.dependencies if d.inject_as == "opponent_case")
        assert chain_dep.upstream_node_id == bull.id
        assert chain_dep.policy.value == "block"
        assert by_id[bull.id].dependencies  # the bull is fed, but not by the bear
        assert all(d.upstream_node_id != bear.id for d in bull.dependencies)

    def test_the_transcript_slot_is_multi_written_and_reduced(self, record):
        seats = [n for n in record.nodes if n.debate_id is not None]
        slots = {n.writes_slot for n in seats}
        assert len(slots) == 1, "both seats write ONE transcript slot"
        slot = slots.pop()
        assert len(record.reducers) == 1
        reducer = record.reducers[0]
        assert reducer.slot == slot
        assert sorted(reducer.producer_node_ids) == sorted(n.id for n in seats)
        assert reducer.reducer_ref == lc._debate_reducer_material().ref
        assert reducer.output_schema_ref == SchemaRef(
            name="DebateTranscript", version="1")

    def test_the_folded_transcript_is_not_a_dependency_and_says_so(self, record):
        """Honest scoping, inherited from the reviewed Phase-8 e2e: Phase 1's
        dependency check compares the upstream WORKER'S output schema against the
        input, so a seat's ``BearCase@1`` can never satisfy the judge's
        ``bullbear_transcript: DebateTranscript@1``."""
        mgr = next(n for n in record.nodes
                   if n.id == SCREENING_LANE_TERMINAL_NODE_ID)
        assert all(d.inject_as != "bullbear_transcript" for d in mgr.dependencies)
        assert "auxiliary" in record.description

    def test_evidence_and_debate_nodes_are_auxiliary(self, record):
        auxiliary = {n.worker_id for n in record.nodes if n.auxiliary}
        assert {"text.news", "text.research_report", "quant.factor",
                "quant.backtest", "pv.price_action", "dec.bull",
                "dec.bear"} <= auxiliary
        reaching = {n.worker_id for n in record.nodes if not n.auxiliary}
        assert reaching == {"text.sentiment", "dec.research_mgr"}

    def test_the_preset_carries_no_code_anywhere(self, record):
        for node in record.nodes:
            assert node.params == {}, node.id
        blob = canonical_json(record)
        assert re.search(r'"\d{6}"', blob) is None, blob


# =========================================================================== #
# 3. Phase 1 validation + profile-v2 runtime support                            #
# =========================================================================== #
class TestValidationAndRuntimeSupport:
    def test_every_materialized_draft_is_phase1_green(self, built, env):
        assert len(built.lanes) == 3
        for lane in built.lanes:
            report = validate_plan_draft(
                lane.draft, request=lane.request, context=env["context"],
                catalog=env["snapshot"], schema_registry=env["registry"])
            assert report.valid is True, [
                (i.code, i.node_id, i.message) for i in report.issues]

    def test_params_on_a_paramsless_worker_would_be_refused(self, built, env):
        """Positive control for the structural code-free claim."""
        lane = built.lanes[0]
        nodes = list(lane.draft.nodes)
        nodes[0] = nodes[0].model_copy(update={"params": {"code": "600519"}})
        tampered = lane.draft.model_copy(update={"nodes": tuple(nodes)})
        report = validate_plan_draft(
            tampered, request=lane.request, context=env["context"],
            catalog=env["snapshot"], schema_registry=env["registry"])
        assert "params_not_allowed" in {i.code for i in report.issues}

    def test_the_profile_v2_analyzers_are_clean_on_the_real_catalog_runtime(
            self, built, phase9_runtime):
        draft = built.lanes[0].draft
        assert analyze_debates(
            draft, catalog=phase9_runtime, profile=STATIC_RUNTIME_PROFILE_V2) == ()
        assert analyze_reducers(draft, catalog=phase9_runtime) == ()
        assert analyze_retry_repair(draft, profile=STATIC_RUNTIME_PROFILE_V2) == ()

    def test_check_runtime_support_admits_the_debate_draft(self, env, trimmed_catalog):
        snapshot, runtime, view = trimmed_catalog
        build = _build(env, catalog=snapshot)
        lane = build.lanes[0]
        phase1 = validate_plan_draft(
            lane.draft, request=lane.request, context=env["context"],
            catalog=snapshot, schema_registry=env["registry"])
        assert phase1.valid is True, [(i.code, i.node_id) for i in phase1.issues]
        support = check_runtime_support(
            lane.draft, phase1_report=phase1, context=env["context"],
            context_requirements=None, catalog=runtime, bridge_view=view,
            schema_registry=env["registry"], profile=STATIC_RUNTIME_PROFILE_V2)
        assert support.supported is True, [
            (i.code, i.node_id, i.explanation) for i in support.issues]

    def test_the_llm_budget_equals_the_analyzers_exact_requirement(self, built, env):
        """``analyze_debates``' pre-reservation rule: ``budget_request_llm_invocations
        >= expanded seats + non-debate LLM nodes``. Pinned to EQUALITY — an
        over-request passes the analyzer while reserving budget nobody can spend."""
        draft = built.lanes[0].draft
        workers = {w.id: w for w in env["snapshot"].workers}
        seats = sum(1 for n in draft.nodes if n.debate_id is not None)
        non_debate_llm = sum(
            1 for n in draft.nodes
            if n.debate_id is None
            and workers[n.worker_id].execution.kind is ExecutionKind.LLM)
        assert (seats, non_debate_llm) == (2, 4)
        assert draft.budget_request_llm_invocations == seats + non_debate_llm == 6


# =========================================================================== #
# 4. per-code materialization + batch determinism                              #
# =========================================================================== #
class TestBuildScreeningBatch:
    def test_one_request_and_one_draft_per_slate_entry(self, built):
        assert isinstance(built, MaterializedScreeningBatch)
        assert isinstance(built.batch, ScreeningBatch)
        assert tuple(lane.code for lane in built.lanes) == CODES
        assert tuple(e.code for e in built.batch.entries) == CODES
        assert tuple(e.lane_index for e in built.batch.entries) == (0, 1, 2)
        assert len({lane.request.request_id for lane in built.lanes}) == 3
        assert len({lane.draft.id for lane in built.lanes}) == 3
        for lane, entry in zip(built.lanes, built.batch.entries):
            assert lane.request.request_id == entry.request_id
            assert lane.draft.id == entry.draft_id
            assert lane.draft.request_id == entry.request_id

    def test_the_draft_is_a_preset_fallback_main_draft(self, built, env):
        for lane in built.lanes:
            draft = lane.draft
            assert draft.source is PlanSource.PRESET_FALLBACK
            assert draft.phase == "main"
            assert draft.approval_policy is ApprovalPolicy.REQUIRED
            assert draft.context_snapshot_ref == env["ctx_ref"]
            assert draft.catalog_digest == env["snapshot"].catalog_digest
            assert draft.schema_registry_digest == env["registry"].registry_digest
            assert lane.request.fallback_preset_id == SCREENING_LANE_PRESET_ID

    def test_the_reviewed_graph_is_copied_verbatim_from_the_record(self, built):
        record = load_phase10_preset_registry(PRODUCTION_PRESETS_DIR).get(
            SCREENING_LANE_PRESET_ID)
        for lane in built.lanes:
            draft = lane.draft
            assert draft.nodes == record.nodes
            assert draft.sink_node_ids == record.sink_node_ids
            assert draft.debates == record.debates
            assert draft.reducers == record.reducers
            assert draft.budget_request_tokens == record.budget_request_tokens
            assert (draft.budget_request_llm_invocations
                    == record.budget_request_llm_invocations)
            assert draft.max_concurrency == record.max_concurrency
            assert draft.gates == () and draft.stop_condition_refs == ()
            assert draft.universe == ()

    def test_one_sealed_graph_serves_every_code(self, built):
        """The whole point of the code-agnostic preset: the N executable
        projections are BYTE-IDENTICAL; only the run-scoped subject and the
        per-code request identity differ."""
        projections = {canonical_json(lane.draft.executable_projection())
                       for lane in built.lanes}
        assert len(projections) == 1
        assert len({lane.subject_ref for lane in built.lanes}) == 3

    def test_the_code_reaches_no_executable_plan_field(self, built):
        for lane in built.lanes:
            blob = canonical_json(lane.draft.executable_projection())
            assert re.search(r'"\d{6}"', blob) is None, blob
            assert lane.code not in lane.draft.goal
            for node in lane.draft.nodes:
                assert node.params == {}

    def test_each_lane_commits_its_own_run_subject(self, built):
        for lane, entry in zip(built.lanes, built.batch.entries):
            subject = RunSubject(code=lane.code, as_of=NOW)
            assert lane.subject_ref.schema_ref == SchemaRef(
                name="RunSubject", version="1")
            assert lane.subject_ref.payload_ref.content_digest == (
                subject.semantic_digest())
            assert entry.subject_ref == lane.subject_ref
        # each lane's subject is a DIFFERENT committed artifact.
        assert len({l.subject_ref.payload_ref.content_digest
                    for l in built.lanes}) == 3

    def test_the_subject_committer_port_is_called_once_per_code(self, env):
        committer = _SubjectCommitter()
        _build(env, committer=committer)
        assert [s.code for s in committer.calls] == list(CODES)
        assert {s.as_of for s in committer.calls} == {NOW}

    def test_the_run_scoped_subject_badge_is_always_present(self, built):
        assert SUBJECT_RUN_SCOPED_BADGE in built.batch.badges
        for lane in built.lanes:
            assert SUBJECT_RUN_SCOPED_BADGE in lane.badges

    def test_batch_determinism(self, env):
        a = _build(env)
        b = _build(env)
        assert a.batch.batch_id == b.batch.batch_id
        assert a.batch == b.batch
        assert [e.candidate_plan_digest for e in a.batch.entries] == [
            e.candidate_plan_digest for e in b.batch.entries]

    def test_a_different_slate_yields_a_different_batch_id(self, env):
        a = _build(env)
        b = _build(env, slate=_slate(("600519", "000001")))
        assert a.batch.batch_id != b.batch.batch_id

    def test_a_refreshed_context_yields_a_different_batch_id(self, env):
        """Amendment I-2: the per-lane candidate digests bind the ContextSnapshot
        content, so a batch id blind to it would key DIFFERENT plans under one
        identity — and every derived idempotency key would collide on the second
        run. Same slate, same base request, a refreshed Lane-0 context."""
        other = _fresh_context(env, as_of=NOW + timedelta(days=1))
        a = _build(env)
        b = _build(env, context=other["context"], ctx_ref=other["ctx_ref"])
        assert a.batch.context_content_digest != b.batch.context_content_digest
        assert a.batch.batch_id != b.batch.batch_id
        # and the plans really do differ, which is what makes that mandatory.
        assert [e.candidate_plan_digest for e in a.batch.entries] != [
            e.candidate_plan_digest for e in b.batch.entries]
        # the derived per-lane admission keys therefore never collide.
        assert {f"{a.batch.batch_id}:{e.code}" for e in a.batch.entries}.isdisjoint(
            {f"{b.batch.batch_id}:{e.code}" for e in b.batch.entries})

    def test_the_batch_pins_the_context_it_was_built_against(self, built, env):
        assert built.batch.context_content_digest == env["context"].content_digest

    def test_a_different_base_request_yields_a_different_batch_id(self, env):
        a = _build(env)
        b = _build(env, base_request=_base_request(request_id="req-screen-2"))
        assert a.batch.batch_id != b.batch.batch_id

    def test_the_candidate_digests_are_the_kernel_function_of_each_lane(
            self, built, env):
        """Not re-derived here: the batch stores exactly what
        ``compute_candidate_plan_digest`` computes for that lane."""
        for lane, entry in zip(built.lanes, built.batch.entries):
            assert entry.candidate_plan_digest == compute_candidate_plan_digest(
                request=lane.request, draft=lane.draft,
                context_content_digest=env["context"].content_digest)
        assert len({e.candidate_plan_digest for e in built.batch.entries}) == 3

    def test_the_per_code_request_differs_from_the_base_in_identity_only(
            self, built):
        """The trusted request record is re-stamped, never rewritten: only
        ``request_id`` moves, so the goal / workflow / policy / fallback the human
        authorized are carried verbatim into every lane."""
        base = _base_request()
        for lane in built.lanes:
            differing = {
                name for name in type(base).model_fields
                if getattr(lane.request, name) != getattr(base, name)}
            assert differing == {"request_id"}, differing
            assert lane.request.request_id == f"{base.request_id}:{lane.code}"

    def test_the_composite_exposes_the_batch_identity_and_cost(self, built):
        assert built.batch_id == built.batch.batch_id
        assert built.cost_preview == built.batch.cost_preview

    def test_the_batch_pins_its_slate_and_preset_provenance(self, built):
        slate = _slate()
        assert built.batch.candidate_slate_ref == _slate_ref(slate)
        assert built.batch.preset_id == SCREENING_LANE_PRESET_ID
        assert built.batch.preset_record_digest == SCREENING_LANE_RECORD_DIGEST


# =========================================================================== #
# 4b. materialization-time subject-params stamping (L1 / D-0)                   #
# =========================================================================== #
class TestSubjectParamsStamp:
    """L1 Task 2 — the screening half of the D-0 stamp: every lane's composite
    carries the closed subject-params document projected (by the ONE reviewed
    recipe, ``SubjectParams.project``) from the SAME digest-verified committed
    ``RunSubject@1`` the lane's bond checks just bound. Honestly vacuous today —
    zero screening workers carry data prefetch rows (the vacuity pin below) —
    but the path is already true for the day an L3 grant lands.
    """

    def test_every_lane_is_stamped_from_its_own_committed_subject(self, built):
        for lane in built.lanes:
            assert lane.subject_params == SubjectParams.project(
                code=lane.code, as_of=NOW)
            assert lane.subject_params.code_value[0].code == lane.code
        # each lane's stamp is a DIFFERENT projection (per-code subjects).
        assert len({lane.subject_params for lane in built.lanes}) == 3

    def test_the_stamp_never_reaches_the_draft(self, built):
        """The sealed-record rule, re-pinned against the STAMPED composite: the
        N executable projections stay byte-identical and every ``PlanNode.params``
        stays empty — the stamp travels beside the plan, never inside it."""
        projections = {canonical_json(lane.draft.executable_projection())
                       for lane in built.lanes}
        assert len(projections) == 1
        for lane in built.lanes:
            for node in lane.draft.nodes:
                assert node.params == {}

    def test_a_bare_composite_defaults_to_an_honest_none(self):
        """Pre-existing constructions keep meaning exactly what they meant: the
        field defaults to None (never a forged or defaulted subject)."""
        import dataclasses
        fields = {f.name: f for f in dataclasses.fields(MaterializedScreeningLane)}
        assert fields["subject_params"].default is None

    def test_zero_screening_workers_carry_data_prefetch_rows_today(self):
        """The vacuity pin (plan R4): TODAY the ONE reviewed prefetch row targets
        the deep lane's ``dec.pm`` only, so the screening stamp and its Task-4
        runner thread are exercised structurally, not behaviorally. When a
        screening worker ever gains a reviewed grant (L3), this pin flips
        CONSCIOUSLY there — and the already-stamped path starts serving it."""
        import guanlan_v2.orchestration.data.catalog as dcat
        surface = dcat.phase3_data_surface()
        targeted = {op.worker_id for op in surface.prefetch_binding.operations}
        assert targeted == {"dec.pm"}
        assert not (targeted & set(SCREENING_LANE_WORKER_IDS))


# =========================================================================== #
# 5. the whole-picture cost preview                                            #
# =========================================================================== #
class TestCostPreview:
    def test_the_preview_totals_are_the_sum_over_the_drafts(self, built):
        preview = screening_cost_preview(built.batch)
        assert isinstance(preview, ScreeningCostPreview)
        assert preview == built.batch.cost_preview
        assert preview.n_codes == 3
        assert preview.per_lane_llm_nodes == 6
        assert preview.total_llm_nodes == 18
        assert preview.total_budget_tokens == sum(
            lane.draft.budget_request_tokens for lane in built.lanes)
        assert preview.total_budget_llm_invocations == sum(
            lane.draft.budget_request_llm_invocations for lane in built.lanes)
        assert preview.per_lane_budget_llm_invocations == 6
        assert preview.per_lane_max_concurrency == 4

    def test_the_preview_declares_that_the_evidence_is_unwired(self, built):
        """Amendment I-4 (product honesty): the price tag names what it does not
        yet buy — seven of the nine workers are auxiliary evidence whose reports
        reach the terminal only through the debate transcript."""
        assert AUXILIARY_EVIDENCE_UNWIRED_BADGE in built.batch.cost_preview.badges
        assert AUXILIARY_EVIDENCE_UNWIRED_BADGE in screening_cost_preview(
            built.batch).badges
        record = load_phase10_preset_registry(PRODUCTION_PRESETS_DIR).get(
            SCREENING_LANE_PRESET_ID)
        assert sum(1 for n in record.nodes if n.auxiliary) == 7

    def test_a_preview_without_the_badge_is_unconstructible(self, built):
        fields = built.batch.cost_preview.model_dump()
        fields["badges"] = ()
        with pytest.raises(ValidationError,
                           match=AUXILIARY_EVIDENCE_UNWIRED_BADGE):
            ScreeningCostPreview.model_validate(fields)

    def test_the_preview_is_a_deterministic_pure_function_of_the_batch(self, built):
        assert screening_cost_preview(built.batch) == screening_cost_preview(
            built.batch)
        assert canonical_json(screening_cost_preview(built.batch)) == canonical_json(
            built.batch.cost_preview)

    def test_the_llm_node_count_is_the_real_catalogs_llm_node_count(
            self, built, env):
        workers = {w.id: w for w in env["snapshot"].workers}
        llm_nodes = sum(1 for n in built.lanes[0].draft.nodes
                        if workers[n.worker_id].execution.kind is ExecutionKind.LLM)
        assert built.batch.cost_preview.per_lane_llm_nodes == llm_nodes == 6

    def test_the_arithmetic_is_structural(self, built):
        """A preview whose totals do not equal ``n_codes × per-lane`` is
        unconstructible — the human's whole picture cannot be quietly wrong."""
        fields = built.batch.cost_preview.model_dump()
        fields["total_llm_nodes"] = fields["total_llm_nodes"] + 1
        with pytest.raises(ValidationError, match="total_llm_nodes"):
            ScreeningCostPreview.model_validate(fields)

    def test_a_batch_whose_stored_preview_disagrees_is_unconstructible(self, built):
        fields = built.batch.model_dump()
        fields["entries"] = fields["entries"][:2]  # 2 lanes, a 3-lane preview
        with pytest.raises(ValidationError, match="cost_preview"):
            ScreeningBatch.model_validate(fields)

    def test_a_batch_whose_lanes_disagree_on_cost_is_unconstructible(self, built):
        """Every lane runs the SAME sealed preset, so 'per lane' is only
        meaningful if it is uniform — a batch of lanes with different budgets has
        no honest whole picture and is refused rather than averaged."""
        fields = built.batch.model_dump()
        entries = [dict(e) for e in fields["entries"]]
        entries[1]["budget_request_llm_invocations"] = 99
        # a tuple, not a list: strict mode rejects a list for a tuple field with a
        # ``tuple_type`` error BEFORE any of our validators run (the Task-6 lesson).
        fields["entries"] = tuple(entries)
        with pytest.raises(ValidationError, match="uniform"):
            ScreeningBatch.model_validate(fields)


# =========================================================================== #
# 6. typed refusals                                                            #
# =========================================================================== #
class TestRefusals:
    def test_an_empty_slate_refuses_typed(self, env):
        empty = CandidateSlate(source_kind="v4", as_of=NOW, top_n=5, entries=())
        with pytest.raises(EmptySlateRefused):
            _build(env, slate=empty, slate_ref=_slate_ref(empty))

    def test_a_slate_ref_that_does_not_bind_the_slate_refuses(self, env):
        wrong = TypedPayloadRef(
            schema_ref=SchemaRef(name="CandidateSlate", version="1"),
            payload_ref=PayloadRef(namespace="main", object_id="slate-x",
                                   content_digest="a" * 64))
        with pytest.raises(SlateRefRefused):
            _build(env, slate_ref=wrong)

    def test_a_slate_ref_of_the_wrong_schema_refuses(self, env):
        slate = _slate()
        wrong = TypedPayloadRef(
            schema_ref=SchemaRef(name="RunSubject", version="1"),
            payload_ref=PayloadRef(namespace="main", object_id="slate-1",
                                   content_digest=slate.semantic_digest()))
        with pytest.raises(SlateRefRefused):
            _build(env, slate_ref=wrong)

    def test_a_base_request_that_does_not_name_this_preset_refuses(self, env):
        with pytest.raises(BaseRequestRefused, match="fallback_preset_id"):
            _build(env, base_request=_base_request(fallback_preset_id=None))
        with pytest.raises(BaseRequestRefused, match="fallback_preset_id"):
            _build(env, base_request=_base_request(
                fallback_preset_id=DEEP_DECIDE_PRESET_ID))

    def test_an_auto_approval_policy_refuses_typed(self, env):
        """AUTO is a Phase-10 red line: refused at the door, never carded."""
        with pytest.raises(BaseRequestRefused, match="AUTO"):
            _build(env, base_request=_base_request(
                approval_policy=ApprovalPolicy.AUTO))

    def test_a_missing_context_snapshot_refuses_typed(self, env):
        with pytest.raises(ContextSnapshotRefused):
            _build(env, context=None)
        with pytest.raises(ContextSnapshotRefused):
            _build(env, ctx_ref=None)

    def test_a_context_ref_that_does_not_bind_the_snapshot_refuses(self, env):
        wrong = PayloadRef(namespace="main", object_id="ctx-x",
                           content_digest="a" * 64)
        with pytest.raises(ContextSnapshotRefused):
            _build(env, ctx_ref=wrong)
        off_namespace = env["ctx_ref"].model_copy(update={"namespace": "shadow"})
        with pytest.raises(ContextSnapshotRefused):
            _build(env, ctx_ref=off_namespace)

    def test_a_naive_clock_refuses_typed(self, env):
        naive = _FixedClock(datetime(2026, 7, 24, 7, 0))
        with pytest.raises(ClockRefused, match="timezone-aware"):
            _build(env, clock=naive)

    def test_a_subject_committer_that_does_not_bind_the_subject_refuses(self, env):
        with pytest.raises(SubjectRefused):
            _build(env, committer=_LyingSubjectCommitter())

    def test_a_registry_without_the_screening_preset_refuses_typed(self, env):
        empty = PlanPresetRegistry()
        empty.seal()
        with pytest.raises(PlanPresetError, match=SCREENING_LANE_PRESET_ID):
            _build(env, preset_registry=empty)

    def test_a_v1_record_under_the_screening_preset_id_refuses_typed(self, env):
        baseline = load_preset_registry(PRODUCTION_PRESETS_DIR).get(PHASE7_BASELINE)
        impostor = PlanPresetRecord(
            preset_id=SCREENING_LANE_PRESET_ID, version="1",
            description=baseline.description, nodes=baseline.nodes,
            sink_node_ids=baseline.sink_node_ids,
            budget_request_tokens=baseline.budget_request_tokens,
            budget_request_llm_invocations=baseline.budget_request_llm_invocations,
            max_concurrency=baseline.max_concurrency)
        registry = PlanPresetRegistry()
        registry.register(impostor)
        registry.seal()
        with pytest.raises(ScreeningError):
            _build(env, preset_registry=registry)


# =========================================================================== #
# 7. the recency badge matrix (mirrors Task 6)                                 #
# =========================================================================== #
class TestRecencyBadge:
    def test_the_session_date_helper_is_the_identity_pinned_one(self):
        assert session_date_of is market_factors._session_date
        assert session_date_of(datetime(2026, 7, 24, 22, 0, tzinfo=UTC)) == "2026-07-25"

    def test_no_badge_when_the_snapshot_is_the_current_session(self, built):
        assert CONTEXT_SNAPSHOT_STALE_BADGE not in built.batch.badges

    def test_badge_when_the_snapshot_data_date_is_behind_the_session(self, env):
        build = _build(env, clock=_FixedClock(NOW + timedelta(days=1)))
        assert CONTEXT_SNAPSHOT_STALE_BADGE in build.batch.badges
        assert f"{CONTEXT_SNAPSHOT_DATA_DATE_BADGE_PREFIX}2026-07-24" in (
            build.batch.badges)
        for lane in build.lanes:
            assert CONTEXT_SNAPSHOT_STALE_BADGE in lane.badges

    def test_no_badge_when_the_snapshot_is_ahead_of_the_session(self, env):
        build = _build(env, clock=_FixedClock(NOW - timedelta(days=3)))
        assert CONTEXT_SNAPSHOT_STALE_BADGE not in build.batch.badges


# =========================================================================== #
# 8. the lease channel is structurally PRESET_FALLBACK-only                     #
# =========================================================================== #
class TestTheSourceIsForced:
    def test_a_preset_sourced_draft_could_not_be_carded_at_all(self, built, env):
        """Why the screening draft is ``PRESET_FALLBACK`` and not ``PRESET``
        (Task 6's stamp): the Phase-7 reviewer card structurally covers only
        DYNAMIC / PRESET_FALLBACK, and the lease matches only a card carrying
        preset provenance — which only a PRESET_FALLBACK card may carry."""
        lane = built.lanes[0]
        preset_sourced = lane.draft.model_copy(
            update={"source": PlanSource.PRESET})
        digest = compute_candidate_plan_digest(
            request=lane.request, draft=preset_sourced,
            context_content_digest=env["context"].content_digest)
        diff = build_plan_diff(
            preset_sourced, request=lane.request, candidate_plan_digest=digest,
            baseline=None, baseline_kind="none")
        ref = TypedPayloadRef(
            schema_ref=PLAN_DIFF_SCHEMA_REF,
            payload_ref=PayloadRef(namespace="main", object_id="diff-x",
                                   content_digest=diff.semantic_digest()))
        with pytest.raises(ValidationError, match="DYNAMIC or PRESET_FALLBACK"):
            build_pending_plan_approval(
                draft=preset_sourced, request=lane.request,
                candidate_plan_digest=digest, diff=diff, plan_diff_ref=ref,
                planner_rationale=None, candidate_id="c-1", requested_at=NOW)

    def test_the_fallback_provenance_is_true_not_relabelled(self, built):
        """The reviewed refusal of relabelling (adapters/replay_cards.py:284-292)
        is respected: the request genuinely names THIS preset as its fallback and
        the draft genuinely materializes THAT record."""
        for lane in built.lanes:
            assert lane.request.fallback_preset_id == SCREENING_LANE_PRESET_ID
        assert built.batch.preset_id == SCREENING_LANE_PRESET_ID


# =========================================================================== #
# 9. admission — the lease channel, on a REAL admission service + coordinator   #
# =========================================================================== #
class TestAdmitScreeningBatch:
    def test_an_active_lease_admits_every_candidate_in_its_envelope(self, admit_env):
        lease = _issue_lease(admit_env, max_admissions=3, budget_cap=18)
        outcomes = _admit(admit_env)
        assert len(outcomes) == 3
        assert all(isinstance(o, LeaseAdmissionOutcome) for o in outcomes)
        assert [o.outcome for o in outcomes] == ["lease_admitted"] * 3
        for outcome, entry in zip(outcomes, admit_env.batch.entries):
            assert outcome.lease_id == lease.lease_id
            assert outcome.approval.decision is ApprovalDecision.APPROVED
            assert outcome.approval.actor_id == f"lease:{lease.lease_id}"
            assert outcome.approval.candidate_plan_digest == (
                entry.candidate_plan_digest)
        # per candidate: pending, lease_consumed, decision — in that order.
        assert _journal_kinds(admit_env.journal) == (
            ["lease_issued"] + ["pending", "lease_consumed", "decision"] * 3)

    def test_the_lease_envelope_mirrors_the_cost_preview(self, admit_env):
        preview = admit_env.batch.cost_preview
        lease = _issue_lease(admit_env, max_admissions=preview.n_codes,
                             budget_cap=preview.total_budget_llm_invocations)
        assert lease.max_admissions == preview.n_codes
        assert lease.budget_cap_llm_invocations == preview.total_budget_llm_invocations
        outcomes = _admit(admit_env)
        assert [o.outcome for o in outcomes] == ["lease_admitted"] * 3
        view = {v.lease.lease_id: v for v in admit_env.coordinator.list_leases(
            now=NOW)}[lease.lease_id]
        assert view.admissions_used == 3 and view.admissions_remaining == 0
        assert view.budget_used == preview.total_budget_llm_invocations
        assert view.status == "exhausted"

    def test_mid_batch_envelope_exhaustion_leaves_the_remainder_pending(
            self, admit_env):
        """Never an error: the lease admits what it can and the rest stay human
        cards. Admitted and pending are disjoint and cover the batch exactly."""
        _issue_lease(admit_env, max_admissions=2, budget_cap=100)
        outcomes = _admit(admit_env)
        assert [o.outcome for o in outcomes] == [
            "lease_admitted", "lease_admitted", "pending_human"]
        admitted = {e.candidate_plan_digest
                    for e, o in zip(admit_env.batch.entries, outcomes)
                    if o.outcome == "lease_admitted"}
        pending = {e.candidate_plan_digest
                   for e, o in zip(admit_env.batch.entries, outcomes)
                   if o.outcome == "pending_human"}
        assert admitted & pending == set()
        assert admitted | pending == {
            e.candidate_plan_digest for e in admit_env.batch.entries}
        # the pending one is a REAL registered card a human can still decide.
        still_pending = admit_env.coordinator.list_pending()
        assert {p.candidate_plan_digest for p in still_pending} == pending

    def test_budget_cap_exhaustion_also_falls_back_to_pending(self, admit_env):
        _issue_lease(admit_env, max_admissions=9, budget_cap=13)  # 2 lanes = 12
        outcomes = _admit(admit_env)
        assert [o.outcome for o in outcomes] == [
            "lease_admitted", "lease_admitted", "pending_human"]

    def test_without_a_lease_every_candidate_stays_a_pending_human_card(
            self, admit_env):
        outcomes = _admit(admit_env)
        assert [o.outcome for o in outcomes] == ["pending_human"] * 3
        assert all(o.approval is None and o.lease_id is None for o in outcomes)
        cards = admit_env.coordinator.list_pending()
        assert len(cards) == 3
        assert {c.candidate_plan_digest for c in cards} == {
            e.candidate_plan_digest for e in admit_env.batch.entries}
        for card in cards:
            assert card.source is PlanSource.PRESET_FALLBACK
            assert card.preset_id == SCREENING_LANE_PRESET_ID
            assert card.preset_record_digest == admit_env.batch.preset_record_digest
            assert card.approval_policy is ApprovalPolicy.REQUIRED
        assert _journal_kinds(admit_env.journal) == ["pending"] * 3

    def test_the_card_shows_the_human_which_stock_and_the_whole_graph(
            self, admit_env):
        _admit(admit_env)
        by_request = {c.request_id: c for c in admit_env.coordinator.list_pending()}
        for entry in admit_env.batch.entries:
            card = by_request[entry.request_id]
            assert entry.code in card.request_id
            assert card.node_count == 9
            assert card.worker_ids == tuple(sorted(SCREENING_LANE_WORKER_IDS))
            assert card.budget_request_llm_invocations == 6
            assert card.rendered_md.strip()

    def test_admission_stops_at_admission(self, admit_env):
        """No execution, no freeze, no approval minted outside the lease channel."""
        _issue_lease(admit_env)
        _admit(admit_env)
        kinds = set(_journal_kinds(admit_env.journal))
        assert kinds <= {"lease_issued", "pending", "lease_consumed", "decision"}
        for entry in admit_env.batch.entries:
            with pytest.raises(AdmissionError, match="no admitted plan"):
                admit_env.admission.load_admitted(entry.candidate_plan_digest)
        names = [n for n, _ in admit_env.emitted]
        assert "plan_lease_admitted" in names
        assert not any("frozen" in n or "dispatch" in n for n in names)

    def test_no_self_approval_without_a_lease(self, admit_env):
        _admit(admit_env)
        for entry in admit_env.batch.entries:
            assert admit_env.coordinator.load_decision(
                entry.request_id, entry.candidate_plan_digest) is None
        assert admit_env.approvals == {}
        assert "decision" not in _journal_kinds(admit_env.journal)

    def test_re_admission_is_idempotent(self, admit_env):
        _issue_lease(admit_env, max_admissions=3, budget_cap=18)
        first = _admit(admit_env)
        rows = _journal_kinds(admit_env.journal)
        second = _admit(admit_env)
        assert [o.outcome for o in second] == [o.outcome for o in first]
        assert [o.approval for o in second] == [o.approval for o in first]
        assert _journal_kinds(admit_env.journal) == rows  # no second consume

    def test_a_batch_entry_whose_digest_drifted_refuses_before_any_write(
            self, admit_env):
        entries = list(admit_env.batch.entries)
        entries[1] = entries[1].model_copy(
            update={"candidate_plan_digest": "b" * 64})
        tampered = admit_env.batch.model_copy(
            update={"entries": tuple(entries)})
        with pytest.raises(BatchAdmissionRefused, match="candidate_plan_digest"):
            admit_screening_batch(
                tampered, coordinator=admit_env.coordinator,
                admission=admit_env.admission, now=NOW,
                payloads=admit_env.stores.payloads,
                registry_digest=admit_env.registry.registry_digest,
                budget=admit_env.budget)
        # pre-flight: nothing was reserved or journalled — not even for lane 0,
        # which precedes the tampered one.
        assert admit_env.journal.exists() is False
        assert _journal_kinds(admit_env.journal) == []
        # and the untouched batch still admits cleanly afterwards.
        assert [o.outcome for o in _admit(admit_env)] == ["pending_human"] * 3

    def test_an_unsupported_lane_refuses_write_free(self, admit_env):
        """Amendment I-1. Under the sealed production catalog EVERY lane is
        unsupported (the data-bridge grant gap), and the service only says so from
        ``persist_and_reserve_candidate`` — after both report payloads are written.
        Reproduced here with the reviewed v1 profile, which locks debates: the
        pre-flight must refuse it typed, with nothing written."""
        service = admit_env.service_with(profile=static_runtime_profile())
        prep = service.prepare_candidate(
            admit_env.batch.entries[0].draft_id,
            request_id=admit_env.batch.entries[0].request_id)
        assert prep.phase1_report.valid is True
        assert prep.support_report.supported is False  # the real refusal
        with pytest.raises(BatchAdmissionRefused, match="not runtime-supported"):
            _admit(admit_env, admission=service)
        assert _journal_kinds(admit_env.journal) == []
        # write-free: not even the Phase-1 / support report payloads landed.
        for entry in admit_env.batch.entries:
            with pytest.raises(EventStoreError):
                admit_env.stores.payloads.get(
                    PayloadRef(namespace="main",
                               object_id=f"phase1-report:{entry.candidate_plan_digest}",
                               content_digest="0" * 64),
                    expected_schema_ref=SchemaRef(
                        name="PlanValidationReport", version="1"))
        assert admit_env.budget.available().llm_invocations == (
            admit_env.run_budget.max_llm_invocations)  # nothing reserved

    def test_a_batch_that_cannot_fit_the_run_budget_refuses_write_free(
            self, admit_env):
        """Amendment I-3: the whole picture is checked against the run's remaining
        capacity BEFORE the first reservation — one lane short must not leave two
        lanes reserved and carded."""
        preview = admit_env.batch.cost_preview
        short = RunBudget(
            ledger_id="led-short",
            max_tokens=preview.total_budget_tokens,
            # room for two lanes, not three.
            max_llm_invocations=preview.total_budget_llm_invocations - 1,
            max_concurrency=32)
        service = admit_env.service_with(run_budget_=short)
        ledger = BudgetLedger(
            sink=admit_env.stores.budget_event_sink(
                run_id=f"{admit_env.run_id}-alt", ledger_id=short.ledger_id),
            run_budget=short)
        with pytest.raises(BatchAdmissionRefused, match="exceeds the run's remaining"):
            _admit(admit_env, admission=service, budget=ledger)
        assert _journal_kinds(admit_env.journal) == []
        assert ledger.available().llm_invocations == short.max_llm_invocations

    def test_a_batch_that_exactly_fits_the_run_budget_admits(self, admit_env):
        """The guard is a real bound, not a margin: exact capacity passes."""
        preview = admit_env.batch.cost_preview
        exact = RunBudget(
            ledger_id="led-exact", max_tokens=preview.total_budget_tokens,
            max_llm_invocations=preview.total_budget_llm_invocations,
            max_concurrency=32)
        service = admit_env.service_with(run_budget_=exact)
        ledger = BudgetLedger(
            sink=admit_env.stores.budget_event_sink(
                run_id=f"{admit_env.run_id}-alt", ledger_id=exact.ledger_id),
            run_budget=exact)
        outcomes = _admit(admit_env, admission=service, budget=ledger)
        assert [o.outcome for o in outcomes] == ["pending_human"] * 3
        assert ledger.available().llm_invocations == 0  # fully held for the human

    def test_a_pending_lane_keeps_holding_its_reservation(self, admit_env):
        """Controller ruling, documented and pinned: a ``pending_human`` lane holds
        its reservation until a human decides — Phase 7's ``decide(REJECTED)``
        releases it. The budget must still be there if the human approves."""
        before = admit_env.budget.available().llm_invocations
        _admit(admit_env)
        after = admit_env.budget.available().llm_invocations
        assert before - after == admit_env.batch.cost_preview.total_budget_llm_invocations

    def test_a_lane_the_service_does_not_hold_refuses_typed(self, admit_env):
        entries = list(admit_env.batch.entries)
        entries[2] = entries[2].model_copy(update={"draft_id": "screening.nope"})
        tampered = admit_env.batch.model_copy(update={"entries": tuple(entries)})
        with pytest.raises(BatchAdmissionRefused, match="unknown_draft"):
            admit_screening_batch(
                tampered, coordinator=admit_env.coordinator,
                admission=admit_env.admission, now=NOW,
                payloads=admit_env.stores.payloads,
                registry_digest=admit_env.registry.registry_digest,
                budget=admit_env.budget)
        assert _journal_kinds(admit_env.journal) == []

    def test_phase1_validation_is_real_inside_admission(self, admit_env):
        """The admission path runs the unmodified Phase-1 validator per lane."""
        for entry in admit_env.batch.entries:
            prep = admit_env.admission.prepare_candidate(
                entry.draft_id, request_id=entry.request_id)
            assert prep.phase1_report.valid is True, [
                i.code for i in prep.phase1_report.issues]
            assert prep.support_report.supported is True
            assert prep.candidate_plan_digest == entry.candidate_plan_digest

    def test_a_dynamic_card_could_never_lease_admit(self, admit_env):
        """DYNAMIC is never involved in screening — and structurally could not be.

        Every batch lane is PRESET_FALLBACK with true preset provenance; a DYNAMIC
        card carries none, and ``_find_admissible_lease`` refuses it outright, so
        the chat path keeps its per-plan human approval.
        """
        lease = _issue_lease(admit_env)
        entry = admit_env.batch.entries[0]
        prep = admit_env.admission.prepare_candidate(
            entry.draft_id, request_id=entry.request_id)
        assert prep.draft.source is PlanSource.PRESET_FALLBACK

        dynamic_draft = prep.draft.model_copy(update={"source": PlanSource.DYNAMIC})
        digest = compute_candidate_plan_digest(
            request=prep.request, draft=dynamic_draft,
            context_content_digest=prep.context_content_digest)
        diff = build_plan_diff(
            dynamic_draft, request=prep.request, candidate_plan_digest=digest,
            baseline=None, baseline_kind="none")
        card = build_pending_plan_approval(
            draft=dynamic_draft, request=prep.request, candidate_plan_digest=digest,
            diff=diff,
            plan_diff_ref=TypedPayloadRef(
                schema_ref=PLAN_DIFF_SCHEMA_REF,
                payload_ref=PayloadRef(
                    namespace="main", object_id="diff-dyn",
                    content_digest=diff.semantic_digest())),
            planner_rationale=None, candidate_id="dyn-1", requested_at=NOW)
        assert card.preset_id is None and card.preset_record_digest is None
        outcome = admit_env.coordinator.register_and_try_lease(
            card, idempotency_key="dyn-1", now=NOW,
            candidate_catalog_digest=dynamic_draft.catalog_digest,
            candidate_registry_digest=dynamic_draft.schema_registry_digest)
        assert outcome.outcome == "pending_human"
        assert outcome.lease_id is None
        view = {v.lease.lease_id: v for v in admit_env.coordinator.list_leases(
            now=NOW)}[lease.lease_id]
        assert view.admissions_used == 0  # the lease was never drawn against


# =========================================================================== #
# 10. red lines — what this module may never call                              #
# =========================================================================== #
class TestRedLines:
    def test_the_module_never_names_an_approval_or_execution_verb(self):
        """Source-level (a call-time import/attribute would not show in
        ``sys.modules``): admission stops at admission — no decide, no freeze, no
        lease issuance, no dispatch, no run."""
        source = Path(screening_mod.__file__).read_text(encoding="utf-8")
        code = "\n".join(
            line for line in source.splitlines()
            if not line.lstrip().startswith("#"))
        tree = ast.parse(source)
        called = {
            node.func.attr for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        } | {
            node.func.id for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        banned = {
            "decide", "issue_lease", "revoke_lease", "freeze_and_admit_candidate",
            "verify_for_dispatch", "load_admitted", "record_approval", "run_plan",
            "admit_after_approval",
        }
        assert called & banned == set(), sorted(called & banned)
        for name in ("seats", "trade", "order", "httpx", "requests"):
            assert f"import {name}" not in code, name

    def test_the_only_admission_verbs_are_prepare_reserve_and_try_lease(self):
        tree = ast.parse(Path(screening_mod.__file__).read_text(encoding="utf-8"))
        called = {node.func.attr for node in ast.walk(tree)
                  if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)}
        assert {"prepare_candidate", "persist_and_reserve_candidate",
                "register_and_try_lease"} <= called

    def test_the_mandatory_v1_badge_is_imported_never_hardcoded(self):
        """Task 1 carry-forward: the badge string has exactly one source of truth
        (``pipeline.contracts``). A second literal in this module would silently
        outlive a rename of the contract's own."""
        source = Path(screening_mod.__file__).read_text(encoding="utf-8")
        assert f'"{NO_CROSS_SECTIONAL_SUMMARY_BADGE}"' not in source
        assert f"'{NO_CROSS_SECTIONAL_SUMMARY_BADGE}'" not in source
        assert "NO_CROSS_SECTIONAL_SUMMARY_BADGE" in source


# =========================================================================== #
# 11. recommendation assembly + advisory archive landing (Task 4)              #
# =========================================================================== #
#: the three control schemas the assembler's event-sourced lookup reads. Restated
#: here (house style) from dag.py:118-119 / pool.py:153 and the terminal worker's
#: sole output binding.
_NODE_RUN_SR = SchemaRef(name="NodeRun", version="1")
_LAYER_COMMIT_SR = SchemaRef(name="LayerCommit", version="1")
#: the archive router's id whitelist — archive/api.py:27 (restated, not imported:
#: an orchestration test must not pull the FastAPI router onto its import path).
_ARCHIVE_ID_RE = re.compile(r"^[A-Za-z0-9_\-\.]{1,80}$")


class _RunHistory:
    """Writes ONE lane run's REAL terminal history into a REAL ``RuntimeStores``.

    Not a fake store and not a fake journal: the payload puts and the three events
    are the reviewed producers' own command shapes — ``ArtifactStaged``
    (pool.py:361-388, ``correlation_id`` = the artifact id), ``NodeStateChanged``
    (dag.py:1043-1062) and the ``LayerCommitted`` barrier (pool.py:485-505) —
    committed through the real :class:`RuntimeUnitOfWork` against the sealed
    Phase-9 registry. The assembler is therefore exercised against exactly the
    journal a real run leaves behind, and every registry / digest / namespace rule
    that guards a real write guards these too.
    """

    def __init__(self, stores: RuntimeStores, registry_digest: str) -> None:
        self._stores = stores
        self._rd = registry_digest
        self._seq = 0

    def commit_terminal_plan(
        self, run_id: str, plan, *,
        node_id: str = SCREENING_LANE_TERMINAL_NODE_ID,
        output_key: str = "primary",
        schema_ref: SchemaRef = RESEARCH_PLAN_SCHEMA_REF,
        status: NodeStatus = NodeStatus.COMPLETED,
        commit_the_layer: bool = True,
    ) -> str:
        """Stage the terminal artifact, record its NodeRun, cross the barrier.

        ``schema_ref`` is a parameter (not a constant) so a terminal node that
        committed some OTHER registered payload can be written honestly — the third
        of the assembler's three facts needs a real counter-example, not a mock.
        """
        self._seq += 1
        tag = f"t{self._seq}"
        plan_digest = "a" * 63 + str(self._seq)
        artifact_id = f"artifact-{run_id}-{tag}"
        payload_ref = self._stores.unit_of_work.commit(RuntimeBatch(
            idempotency_key=f"stage:{run_id}:{tag}",
            payload_puts=(PayloadPutCommand(
                staged_key=StagedPayloadKey(key="payload"),
                schema_ref=schema_ref, namespace="main",
                payload_template=dict(plan), registry_digest=self._rd,
                idempotency_key=f"{run_id}:{tag}:payload"),),
            event_appends=(EventAppendCommand(
                run_id=run_id, partition="main", event_type="ArtifactStaged",
                payload_schema_ref=schema_ref,
                payload_target=StagedPayloadKey(key="payload"),
                registry_digest=self._rd,
                idempotency_key=f"{run_id}:{tag}:staged-event",
                plan_digest=plan_digest, correlation_id=artifact_id),),
        )).payload_ref("payload")

        node_run = NodeRun(
            node_run_id=f"nr-{run_id}-{tag}", run_id=run_id, plan_id=f"plan-{run_id}",
            plan_digest=plan_digest, node_id=node_id, worker_id="dec.research_mgr",
            status=status, attempt_id=f"att-{tag}", attempt=1,
            input_snapshot_digest="b" * 64, output_keys=(output_key,),
            output_artifact_ids=(artifact_id,))
        self._append_node_run(run_id, node_run, tag, plan_digest)
        if commit_the_layer:
            self._commit_layer(run_id, node_run, tag, plan_digest, (artifact_id,))
        return payload_ref.content_digest

    def record_failed_terminal(self, run_id: str) -> None:
        """A terminal node that produced nothing (the honest failure history)."""
        self._seq += 1
        tag = f"t{self._seq}"
        plan_digest = "c" * 63 + str(self._seq)
        node_run = NodeRun(
            node_run_id=f"nr-{run_id}-{tag}", run_id=run_id, plan_id=f"plan-{run_id}",
            plan_digest=plan_digest, node_id=SCREENING_LANE_TERMINAL_NODE_ID,
            worker_id="dec.research_mgr", status=NodeStatus.FAILED,
            reason_code="worker_error", attempt_id=f"att-{tag}", attempt=1,
            input_snapshot_digest="b" * 64)
        self._append_node_run(run_id, node_run, tag, plan_digest)

    def _append_node_run(self, run_id, node_run, tag, plan_digest) -> None:
        self._stores.unit_of_work.commit(RuntimeBatch(
            idempotency_key=f"node:{run_id}:{tag}",
            payload_puts=(PayloadPutCommand(
                staged_key=StagedPayloadKey(key="nodeterm"), schema_ref=_NODE_RUN_SR,
                namespace="main", payload_template=dict(node_run),
                registry_digest=self._rd,
                idempotency_key=f"{run_id}:{tag}:nodeterm"),),
            event_appends=(EventAppendCommand(
                run_id=run_id, partition="main", event_type="NodeStateChanged",
                payload_schema_ref=_NODE_RUN_SR,
                payload_target=StagedPayloadKey(key="nodeterm"),
                registry_digest=self._rd,
                idempotency_key=f"{run_id}:{tag}:node-event",
                plan_digest=plan_digest, correlation_id=node_run.node_run_id),),
        ))

    def _commit_layer(self, run_id, node_run, tag, plan_digest, artifact_ids) -> None:
        layer = LayerCommit(
            plan_digest=plan_digest, layer_index=self._seq,
            node_run_ids=(node_run.node_run_id,),
            artifacts=tuple(
                CommittedArtifactRef(artifact_id=aid, artifact_seq=i + 1)
                for i, aid in enumerate(artifact_ids)),
            committed_at=NOW)
        self._stores.unit_of_work.commit(RuntimeBatch(
            idempotency_key=f"layer:{run_id}:{tag}",
            payload_puts=(PayloadPutCommand(
                staged_key=StagedPayloadKey(key="lc"), schema_ref=_LAYER_COMMIT_SR,
                namespace="main", payload_template=dict(layer),
                registry_digest=self._rd, idempotency_key=f"{run_id}:{tag}:lc"),),
            event_appends=(EventAppendCommand(
                run_id=run_id, partition="main", event_type="LayerCommitted",
                payload_schema_ref=_LAYER_COMMIT_SR,
                payload_target=StagedPayloadKey(key="lc"),
                registry_digest=self._rd,
                idempotency_key=f"{run_id}:{tag}:lc-event",
                plan_digest=plan_digest),),
        ))


class _ArchiveSpy:
    """The injected archive seam (the console ``_archive_research`` idiom, in-process)."""

    def __init__(self, *, boom: Exception | None = None) -> None:
        self.calls: list[tuple[str, str, str]] = []
        self._boom = boom

    def __call__(self, artifact_id: str, artifact_type: str, markdown: str) -> None:
        self.calls.append((artifact_id, artifact_type, markdown))
        if self._boom is not None:
            raise self._boom


class _RecEnv:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def _fresh_run_store(env) -> tuple[RuntimeStores, _RunHistory]:
    """An empty REAL store over the sealed Phase-9 registry + its history writer."""
    resolver = SchemaRegistryResolver()
    resolver.register(env["registry"])
    stores = RuntimeStores(
        resolver=resolver, clock=_FixedClock(),
        allowed_cell_namespaces=(W.PROMPT_CELL_NAMESPACE,))
    return stores, _RunHistory(stores, env["registry"].registry_digest)


#: lane 0 is the WORSE grade on purpose: sorting by grade must reorder the slate.
_LANE_RATINGS = {0: PortfolioRating.HOLD, 1: PortfolioRating.BUY}
_RATIONALE = {
    0: "估值中枢已修复,等待旺季验证。",
    1: "订单能见度延伸至明年一季度,产能爬坡兑现。",
}


@pytest.fixture
def rec_env(env, built):
    """Two lanes with a committed ``ResearchPlan@1``, one lane with nothing.

    The runs are keyed by the lanes' OWN ``draft.run_id`` — the production
    identity :func:`build_screening_batch` stamped — so the assembler must find
    each lane's run the same way the runner will.
    """
    stores, writer = _fresh_run_store(env)
    plans, digests = {}, {}
    for lane_index, rating in _LANE_RATINGS.items():
        plan = ResearchPlan(
            recommendation=rating, rationale=_RATIONALE[lane_index],
            strategic_actions=(f"分批建仓 lane{lane_index}", "跌破前低离场"))
        plans[lane_index] = plan
        digests[lane_index] = writer.commit_terminal_plan(
            built.lanes[lane_index].draft.run_id, plan)
    return _RecEnv(stores=stores, writer=writer, batch=built.batch, built=built,
                   plans=plans, digests=digests)


class TestBuildRecommendationSlate:
    def test_two_committed_lanes_fold_in_and_the_third_degrades(self, rec_env):
        slate = build_recommendation_slate(rec_env.batch, stores=rec_env.stores)
        assert isinstance(slate, RecommendationSlate)
        assert {e.code for e in slate.entries} == {CODES[0], CODES[1]}
        assert slate.degraded_lanes == (2,)
        assert len(slate.entries) == 2

    def test_the_rating_is_copied_verbatim_from_the_committed_plan(self, rec_env):
        slate = build_recommendation_slate(rec_env.batch, stores=rec_env.stores)
        by_lane = {e.lane_index: e for e in slate.entries}
        for lane_index, rating in _LANE_RATINGS.items():
            assert by_lane[lane_index].rating == rating.value
            # …and the ref pins the exact plan that rating was read off.
            ref = by_lane[lane_index].research_plan_ref
            assert ref.schema_ref == RESEARCH_PLAN_SCHEMA_REF
            assert ref.payload_ref.content_digest == rec_env.digests[lane_index]
            assert rec_env.stores.payloads.get(
                ref.payload_ref, expected_schema_ref=ref.schema_ref
            ) == rec_env.plans[lane_index]

    def test_lane_index_is_the_slate_rank_order_not_the_sorted_position(
            self, rec_env):
        slate = build_recommendation_slate(rec_env.batch, stores=rec_env.stores)
        # sorted BY GRADE: the Buy lane (slate rank 1) comes first…
        assert [e.code for e in slate.entries] == [CODES[1], CODES[0]]
        assert [e.rating for e in slate.entries] == ["Buy", "Hold"]
        # …while lane_index keeps naming each candidate's slate rank order.
        assert [e.lane_index for e in slate.entries] == [1, 0]
        by_code = {e.code: e.lane_index for e in slate.entries}
        for entry in rec_env.batch.entries:
            if entry.code in by_code:
                assert by_code[entry.code] == entry.lane_index

    def test_the_entries_are_exactly_the_documented_sort(self, rec_env):
        slate = build_recommendation_slate(rec_env.batch, stores=rec_env.stores)
        assert list(slate.entries) == sorted(
            slate.entries, key=recommendation_sort_key)

    def test_the_v1_cross_sectional_slot_is_none_with_its_mandatory_badge(
            self, rec_env):
        slate = build_recommendation_slate(rec_env.batch, stores=rec_env.stores)
        assert slate.portfolio_decision_ref is None
        assert NO_CROSS_SECTIONAL_SUMMARY_BADGE in slate.badges
        assert slate.badges[0] == NO_CROSS_SECTIONAL_SUMMARY_BADGE

    def test_the_batch_badges_are_carried_verbatim_onto_the_product(self, rec_env):
        """What the human was told about the BATCH must survive to the product they
        read: the run-scoped subject and the unwired auxiliary evidence."""
        slate = build_recommendation_slate(rec_env.batch, stores=rec_env.stores)
        assert SUBJECT_RUN_SCOPED_BADGE in slate.badges
        assert AUXILIARY_EVIDENCE_UNWIRED_BADGE in slate.badges
        assert len(set(slate.badges)) == len(slate.badges)

    def test_the_slate_pins_the_batch_and_the_candidate_slate_it_came_from(
            self, rec_env):
        slate = build_recommendation_slate(rec_env.batch, stores=rec_env.stores)
        assert slate.batch_id == rec_env.batch.batch_id
        assert slate.candidate_slate_ref == rec_env.batch.candidate_slate_ref
        assert slate.as_of == rec_env.batch.as_of

    def test_a_staged_but_uncommitted_plan_is_a_degraded_lane(self, env, built):
        """Committed-only, exactly like the pool's own visibility rule: a staged
        artifact is evidence of work, never of a verdict."""
        stores, writer = _fresh_run_store(env)
        writer.commit_terminal_plan(
            built.lanes[0].draft.run_id,
            ResearchPlan(recommendation=PortfolioRating.BUY, rationale="未过闸"),
            commit_the_layer=False)
        slate = build_recommendation_slate(built.batch, stores=stores)
        assert slate.entries == ()
        assert slate.degraded_lanes == (0, 1, 2)

    def test_a_failed_terminal_node_is_a_degraded_lane(self, env, built):
        stores, writer = _fresh_run_store(env)
        writer.record_failed_terminal(built.lanes[1].draft.run_id)
        slate = build_recommendation_slate(built.batch, stores=stores)
        assert slate.entries == ()
        assert slate.degraded_lanes == (0, 1, 2)

    def test_a_degraded_terminal_is_a_real_entry_that_says_it_was_degraded(
            self, env, built):
        """``COMPLETED`` and ``DEGRADED`` both commit a plan. A verdict reached with
        an input missing IS a verdict — so it is published — but it must never
        publish indistinguishable from a clean one."""
        stores, writer = _fresh_run_store(env)
        writer.commit_terminal_plan(
            built.lanes[0].draft.run_id,
            ResearchPlan(recommendation=PortfolioRating.BUY, rationale="缺一路证据"),
            status=NodeStatus.DEGRADED)
        writer.commit_terminal_plan(
            built.lanes[1].draft.run_id,
            ResearchPlan(recommendation=PortfolioRating.HOLD, rationale="齐活"))
        slate = build_recommendation_slate(built.batch, stores=stores)
        assert {e.lane_index for e in slate.entries} == {0, 1}
        assert f"{LANE_TERMINAL_DEGRADED_BADGE_PREFIX}0" in slate.badges
        # the CLEAN lane is not badged, and the badge is not degraded_lanes.
        assert f"{LANE_TERMINAL_DEGRADED_BADGE_PREFIX}1" not in slate.badges
        assert slate.degraded_lanes == (2,)
        # and the human reading the document is told which lane it was.
        assert f"{LANE_TERMINAL_DEGRADED_BADGE_PREFIX}0" in render_recommendation_md(
            slate, stores=stores)

    def test_a_clean_batch_carries_no_terminal_degraded_badge(self, rec_env):
        slate = build_recommendation_slate(rec_env.batch, stores=rec_env.stores)
        assert not any(b.startswith(LANE_TERMINAL_DEGRADED_BADGE_PREFIX)
                       for b in slate.badges)

    def test_a_terminal_artifact_of_another_schema_degrades_without_raising(
            self, env, built):
        """The THIRD fact, with a real counter-example. A terminal node that
        committed a ``PortfolioDecision@1`` has produced something — but not this
        lane's verdict. It must degrade the lane **quietly**: building an entry
        around a foreign ref would raise out of ``RecommendationEntry``'s own
        schema pin and take the WHOLE slate down, turning one odd lane into no
        product at all."""
        stores, writer = _fresh_run_store(env)
        writer.commit_terminal_plan(
            built.lanes[0].draft.run_id,
            PortfolioDecision(
                rating=PortfolioRating.BUY, executive_summary="摘要",
                investment_thesis="论点"),
            schema_ref=SchemaRef(name="PortfolioDecision", version="1"))
        writer.commit_terminal_plan(
            built.lanes[1].draft.run_id,
            ResearchPlan(recommendation=PortfolioRating.HOLD, rationale="正常"))
        slate = build_recommendation_slate(built.batch, stores=stores)  # no raise
        assert [e.lane_index for e in slate.entries] == [1]
        assert slate.degraded_lanes == (0, 2)

    def test_an_artifact_from_another_node_is_never_read_as_the_verdict(
            self, env, built):
        """Node identity is checked, not just schema: a ``ResearchPlan@1`` committed
        by some other node of the graph is not this lane's terminal verdict."""
        stores, writer = _fresh_run_store(env)
        writer.commit_terminal_plan(
            built.lanes[0].draft.run_id,
            ResearchPlan(recommendation=PortfolioRating.SELL, rationale="旁路"),
            node_id="bull")
        slate = build_recommendation_slate(built.batch, stores=stores)
        assert slate.entries == ()
        assert slate.degraded_lanes == (0, 1, 2)

    def test_a_batch_whose_runs_never_ran_yields_an_honest_empty_slate(
            self, env, built):
        stores, _writer = _fresh_run_store(env)
        slate = build_recommendation_slate(built.batch, stores=stores)
        assert slate.entries == ()
        assert slate.degraded_lanes == (0, 1, 2)
        assert NO_CROSS_SECTIONAL_SUMMARY_BADGE in slate.badges

    def test_assembly_is_read_only_over_the_runs(self, rec_env):
        """It never mutates kernel state: no payload, no event, no budget row."""
        before_payloads = rec_env.stores.payloads_object_count()
        before_events = {
            lane.draft.run_id: rec_env.stores.events.journal(lane.draft.run_id, "main")
            for lane in rec_env.built.lanes}
        build_recommendation_slate(rec_env.batch, stores=rec_env.stores)
        build_recommendation_slate(rec_env.batch, stores=rec_env.stores)
        assert rec_env.stores.payloads_object_count() == before_payloads
        for lane in rec_env.built.lanes:
            assert rec_env.stores.events.journal(
                lane.draft.run_id, "main") == before_events[lane.draft.run_id]

    def test_assembly_is_deterministic(self, rec_env):
        a = build_recommendation_slate(rec_env.batch, stores=rec_env.stores)
        b = build_recommendation_slate(rec_env.batch, stores=rec_env.stores)
        assert a == b
        assert canonical_json(a) == canonical_json(b)


class TestRecommendationSortKey:
    def test_the_reviewed_grades_sort_in_their_reviewed_order(self):
        assert REVIEWED_RATING_ORDER == tuple(r.value for r in PortfolioRating)
        assert REVIEWED_RATING_ORDER == (
            "Buy", "Overweight", "Hold", "Underweight", "Sell")

    def _entry(self, code: str, lane_index: int, rating: str) -> RecommendationEntry:
        return RecommendationEntry(
            code=code, lane_index=lane_index, rating=rating,
            research_plan_ref=TypedPayloadRef(
                schema_ref=RESEARCH_PLAN_SCHEMA_REF,
                payload_ref=PayloadRef(
                    namespace="main", object_id=f"rp-{code}",
                    content_digest=f"{lane_index:064d}")))

    def test_known_grades_first_then_unknown_grades_stable_by_rank(self):
        """``RecommendationEntry.rating`` is a FREE string (Task 1), so the sort must
        handle a grade no closed vocabulary knows — honestly, not by guessing where
        it belongs: reviewed grades first in reviewed order, everything else after
        in slate rank order."""
        entries = [
            self._entry("600519", 0, "强烈推荐"),   # unknown
            self._entry("000001", 1, "Sell"),
            self._entry("300750", 2, "Buy"),
            self._entry("600036", 3, "zzz"),        # unknown
            self._entry("000002", 4, "Hold"),
        ]
        ordered = sorted(entries, key=recommendation_sort_key)
        assert [e.rating for e in ordered] == [
            "Buy", "Hold", "Sell", "强烈推荐", "zzz"]
        # the two unknowns keep slate rank order (0 before 3) — never re-ranked by
        # their own text.
        assert [e.lane_index for e in ordered][-2:] == [0, 3]

    def test_equal_grades_keep_slate_rank_order(self):
        entries = [self._entry("000001", 2, "Buy"), self._entry("600519", 0, "Buy")]
        assert [e.lane_index for e in sorted(entries, key=recommendation_sort_key)] == [
            0, 2]


class TestRenderRecommendationMd:
    def test_the_advisory_banner_is_the_mandatory_first_line(self, rec_env):
        slate = build_recommendation_slate(rec_env.batch, stores=rec_env.stores)
        md = render_recommendation_md(slate, stores=rec_env.stores)
        assert RECOMMENDATION_ADVISORY_BANNER == (
            "> 本推荐为编排研究产物,仅供参考,不构成交易指令")
        assert md.splitlines()[0] == RECOMMENDATION_ADVISORY_BANNER
        assert md.startswith(RECOMMENDATION_ADVISORY_BANNER)

    def test_an_empty_slate_still_opens_with_the_banner(self, env, built):
        stores, _writer = _fresh_run_store(env)
        slate = build_recommendation_slate(built.batch, stores=stores)
        md = render_recommendation_md(slate, stores=stores)
        assert md.splitlines()[0] == RECOMMENDATION_ADVISORY_BANNER
        for lane_index in (0, 1, 2):
            assert f"lane_index {lane_index}" in md

    def test_the_md_carries_every_rating_code_and_rationale_verbatim(self, rec_env):
        slate = build_recommendation_slate(rec_env.batch, stores=rec_env.stores)
        md = render_recommendation_md(slate, stores=rec_env.stores)
        for lane_index, rating in _LANE_RATINGS.items():
            assert rec_env.built.lanes[lane_index].code in md
            assert rating.value in md
            assert _RATIONALE[lane_index] in md
            for action in rec_env.plans[lane_index].strategic_actions:
                assert action in md
        assert rec_env.batch.batch_id in md
        # the degraded lane appears as its lane index and nothing else: the v1
        # ``RecommendationSlate`` carries no code for a lane that produced no plan.
        assert CODES[2] not in md
        assert "lane_index 2" in md

    def test_the_slate_badges_are_rendered_verbatim(self, rec_env):
        """Task 2 carry-forward: a badge is rendered as it was minted — this layer
        never re-counts it or invents a denominator for it."""
        slate = build_recommendation_slate(rec_env.batch, stores=rec_env.stores)
        md = render_recommendation_md(slate, stores=rec_env.stores)
        for badge in slate.badges:
            assert badge in md

    def test_every_number_in_the_md_traces_to_a_payload_field(self, rec_env):
        slate = build_recommendation_slate(rec_env.batch, stores=rec_env.stores)
        md = render_recommendation_md(slate, stores=rec_env.stores)
        traceable = [slate.batch_id, slate.as_of.isoformat(),
                     slate.candidate_slate_ref.payload_ref.content_digest]
        for entry in slate.entries:
            traceable += [entry.code, entry.rating,
                          entry.research_plan_ref.payload_ref.content_digest]
        for plan in rec_env.plans.values():
            traceable += [plan.rationale, *plan.strategic_actions]
        stripped = md
        for value in sorted(traceable, key=len, reverse=True):
            stripped = stripped.replace(value, "·")
        leftover = set(re.findall(r"\d+", stripped))
        allowed = ({str(e.lane_index) for e in slate.entries}
                   | {str(i) for i in slate.degraded_lanes}
                   | {str(len(slate.entries)), str(len(slate.degraded_lanes))}
                   | {"1"})  # the two schema@version pins (ResearchPlan@1 …)
        assert leftover <= allowed, sorted(leftover - allowed)

    def test_rationale_text_can_never_forge_document_structure(self, env, built):
        """Model text is DATA. A rationale carrying its own headings — or a second
        advisory banner — is flattened onto one line, so it can neither open a
        section nor impersonate the mandatory banner."""
        stores, writer = _fresh_run_store(env)
        hostile = ("真理由\n## 伪造小节\n" + RECOMMENDATION_ADVISORY_BANNER
                   + "\n- 伪造条目")
        writer.commit_terminal_plan(
            built.lanes[0].draft.run_id,
            ResearchPlan(recommendation=PortfolioRating.BUY, rationale=hostile,
                         strategic_actions=("第一行\n### 伪造标题",)))
        slate = build_recommendation_slate(built.batch, stores=stores)
        md = render_recommendation_md(slate, stores=stores)
        lines = md.splitlines()
        # exactly ONE line IS the banner, and it is the first — the copy inside the
        # rationale is mid-line prose, which markdown cannot read as a blockquote.
        assert [i for i, ln in enumerate(lines)
                if ln == RECOMMENDATION_ADVISORY_BANNER] == [0]
        assert not any(ln.startswith("#") and "伪造" in ln for ln in lines)
        assert not any(ln.lstrip().startswith("- 伪造条目") for ln in lines)
        # the text itself is not censored — only its line breaks are flattened.
        assert "伪造小节" in md and "伪造条目" in md and "伪造标题" in md

    def test_the_render_is_deterministic(self, rec_env):
        slate = build_recommendation_slate(rec_env.batch, stores=rec_env.stores)
        first = render_recommendation_md(slate, stores=rec_env.stores)
        second = render_recommendation_md(slate, stores=rec_env.stores)
        assert first == second


class TestLandRecommendation:
    def test_it_lands_the_md_under_the_dated_research_id(self, rec_env):
        slate = build_recommendation_slate(rec_env.batch, stores=rec_env.stores)
        spy = _ArchiveSpy()
        assert land_recommendation(
            slate, stores=rec_env.stores, archive_put=spy) is None
        assert len(spy.calls) == 1
        artifact_id, artifact_type, markdown = spy.calls[0]
        assert artifact_id == "rs_orch_screen_20260724"
        assert artifact_id.startswith(RECOMMENDATION_ARCHIVE_ID_PREFIX)
        assert artifact_type == RECOMMENDATION_ARCHIVE_ARTIFACT_TYPE == "research"
        assert markdown == render_recommendation_md(slate, stores=rec_env.stores)
        assert markdown.splitlines()[0] == RECOMMENDATION_ADVISORY_BANNER

    def test_the_id_is_the_plus_eight_session_date_and_passes_the_archive_whitelist(
            self, rec_env):
        slate = build_recommendation_slate(rec_env.batch, stores=rec_env.stores)
        artifact_id = recommendation_archive_id(slate)
        assert artifact_id == (
            f"{RECOMMENDATION_ARCHIVE_ID_PREFIX}"
            f"{session_date_of(slate.as_of).replace('-', '')}")
        assert _ARCHIVE_ID_RE.match(artifact_id), artifact_id
        # a 22:00Z as_of is already the NEXT +08:00 session day.
        late = slate.model_copy(
            update={"as_of": datetime(2026, 7, 24, 22, 0, tzinfo=UTC)})
        assert recommendation_archive_id(late) == "rs_orch_screen_20260725"

    def test_re_landing_the_same_slate_reuses_the_same_id(self, rec_env):
        """Idempotent per batch: the archive is an upsert by id (archive/api.py:45-55),
        so a second landing overwrites — it never mints a second artifact."""
        slate = build_recommendation_slate(rec_env.batch, stores=rec_env.stores)
        spy = _ArchiveSpy()
        land_recommendation(slate, stores=rec_env.stores, archive_put=spy)
        land_recommendation(slate, stores=rec_env.stores, archive_put=spy)
        assert len({call[0] for call in spy.calls}) == 1
        assert spy.calls[0] == spy.calls[1]

    def test_the_id_follows_the_data_as_of_so_a_replay_refiles_the_old_day(
            self, rec_env):
        """The filing day is the CANDIDATE SLATE's +08:00 session day — the day of
        the DATA, not the production day and not the timestamp the recency badge
        compares (``context.data_context.as_of``). So a PIT replay run today from a
        stale ``as_of`` files under the OLD day's id and overwrites whatever genuine
        product was archived for it. Pinned so Task 7/9 wiring reads it as the
        contract rather than discovering it in production."""
        today = build_recommendation_slate(rec_env.batch, stores=rec_env.stores)
        replayed = today.model_copy(
            update={"as_of": NOW - timedelta(days=6), "batch_id": "e" * 64})
        assert recommendation_archive_id(today) == "rs_orch_screen_20260724"
        assert recommendation_archive_id(replayed) == "rs_orch_screen_20260718"
        # a real overwrite risk, not a theoretical one: same id family, different
        # document — the id alone cannot tell the two products apart.
        spy = _ArchiveSpy()
        land_recommendation(replayed, stores=rec_env.stores, archive_put=spy)
        land_recommendation(today, stores=rec_env.stores, archive_put=spy)
        assert spy.calls[0][0] != spy.calls[1][0]
        assert spy.calls[0][2] != spy.calls[1][2]

    def test_the_id_is_day_scoped_so_a_same_day_second_batch_overwrites(self, rec_env):
        """Recorded honestly rather than papered over: the reviewed id format is
        dated, not batch-keyed, so two batches on one session day land on ONE
        archive id. The md names its own batch_id, which is how a reader tells
        which batch the archived document is."""
        slate = build_recommendation_slate(rec_env.batch, stores=rec_env.stores)
        other = slate.model_copy(update={"batch_id": "f" * 64})
        assert recommendation_archive_id(other) == recommendation_archive_id(slate)
        assert render_recommendation_md(
            other, stores=rec_env.stores) != render_recommendation_md(
            slate, stores=rec_env.stores)
        assert "f" * 64 in render_recommendation_md(other, stores=rec_env.stores)

    def test_a_landing_failure_raises_and_is_never_a_silent_skip(self, rec_env):
        slate = build_recommendation_slate(rec_env.batch, stores=rec_env.stores)
        spy = _ArchiveSpy(boom=RuntimeError("archive is down"))
        with pytest.raises(RuntimeError, match="archive is down"):
            land_recommendation(slate, stores=rec_env.stores, archive_put=spy)
        assert len(spy.calls) == 1  # it really tried, and it really surfaced

    def test_landing_writes_nothing_into_the_kernel(self, rec_env):
        slate = build_recommendation_slate(rec_env.batch, stores=rec_env.stores)
        before = rec_env.stores.payloads_object_count()
        land_recommendation(slate, stores=rec_env.stores, archive_put=_ArchiveSpy())
        assert rec_env.stores.payloads_object_count() == before
