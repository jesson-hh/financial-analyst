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
from guanlan_v2.orchestration.context import RunBudget
from guanlan_v2.orchestration.debate import DEBATE_MAX_ROUNDS, analyze_debates
from guanlan_v2.orchestration.digest import canonical_json
from guanlan_v2.orchestration.enums import (
    ApprovalDecision,
    ApprovalPolicy,
    ExecutionKind,
    PlanSource,
    ToolCallRequirement,
)
from guanlan_v2.orchestration.eventstore import RuntimeStores, SchemaRegistryResolver
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
from guanlan_v2.orchestration.runtime_support import (
    STATIC_RUNTIME_PROFILE_V2,
    analyze_reducers,
    analyze_retry_repair,
    check_runtime_support,
)
from guanlan_v2.orchestration.skilltree import parse_skill_v1
from guanlan_v2.orchestration.spec import (
    OrchestrationRequest,
    compute_candidate_plan_digest,
    validate_plan_draft,
)

from guanlan_v2.orchestration.pipeline.assembly import (
    PHASE10_PRESETS_V2_DIR,
    PRODUCTION_PRESETS_DIR,
    PlanPresetRecordV2,
    build_phase10_preset_registry,
    load_phase10_preset_registry,
)
from guanlan_v2.orchestration.pipeline.contracts import (
    CandidateEntry,
    CandidateSlate,
    RunSubject,
)
from guanlan_v2.orchestration.pipeline import screening as screening_mod
from guanlan_v2.orchestration.pipeline.screening import (
    CONTEXT_SNAPSHOT_DATA_DATE_BADGE_PREFIX,
    CONTEXT_SNAPSHOT_STALE_BADGE,
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
    ScreeningBatch,
    ScreeningCostPreview,
    ScreeningError,
    SlateRefRefused,
    SubjectRefused,
    admit_screening_batch,
    build_screening_batch,
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

    return _AdmitEnv(
        build=build, batch=build.batch, admission=admission,
        coordinator=coordinator, stores=stores, registry=registry,
        snapshot=snapshot, journal=tmp_path / "plan_approvals.jsonl",
        emitted=emitted, approvals=approvals, clock=clock)


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


def _admit(admit_env):
    return admit_screening_batch(
        admit_env.batch, coordinator=admit_env.coordinator,
        admission=admit_env.admission, now=NOW,
        payloads=admit_env.stores.payloads,
        registry_digest=admit_env.registry.registry_digest)


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
                registry_digest=admit_env.registry.registry_digest)
        # pre-flight: nothing was reserved or journalled — not even for lane 0,
        # which precedes the tampered one.
        assert admit_env.journal.exists() is False
        assert _journal_kinds(admit_env.journal) == []
        # and the untouched batch still admits cleanly afterwards.
        assert [o.outcome for o in _admit(admit_env)] == ["pending_human"] * 3

    def test_a_lane_the_service_does_not_hold_refuses_typed(self, admit_env):
        entries = list(admit_env.batch.entries)
        entries[2] = entries[2].model_copy(update={"draft_id": "screening.nope"})
        tampered = admit_env.batch.model_copy(update={"entries": tuple(entries)})
        with pytest.raises(BatchAdmissionRefused, match="unknown_draft"):
            admit_screening_batch(
                tampered, coordinator=admit_env.coordinator,
                admission=admit_env.admission, now=NOW,
                payloads=admit_env.stores.payloads,
                registry_digest=admit_env.registry.registry_digest)
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
