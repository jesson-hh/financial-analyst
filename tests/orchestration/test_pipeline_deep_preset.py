# -*- coding: utf-8 -*-
"""Phase 10 · Task 6 — the sealed context-bound deep-decide preset.

E1 reconciliation transcribed (read at source BEFORE the implementation; the full
table lives in ``.superpowers/sdd/task-6-report.md``):

* **Phase 7 preset shape/loader.** ``PlanPresetRecord`` fields + the
  ``_admissible`` static-profile validator (plan_presets.py:102-164 — the debate
  refusal at :147-155); ``PlanPresetRegistry.register`` isinstance gate :216-218;
  ``load_preset_registry`` strict loader :266-299 (non-recursive ``root/*.json``
  glob :277); ``materialize_fallback_draft`` :305-407 (selection rule :348,
  context-ref precondition :356-363, final-workers-only gate :366-375, stamped
  draft :380-407). Exemplar: ``config/orchestration/presets/main_research_baseline.json``.
* **Phase 8 debate mechanism.** ``DebateCfg`` spec.py:384-406; ``PlanNode`` debate
  identity :318-321/:333-347; Phase-1 debate coherence :598-626; ``DEBATE_MAX_ROUNDS``
  debate.py:117; ``DEBATE_TRANSCRIPT_REDUCER_ID`` :121; ``analyze_debates`` :321-400
  (seats LLM, judge-not-a-seat, budget covers the fully expanded count).
  The reviewed PlanDraft expression of a bull/bear debate is
  tests/orchestration/test_phase8_e2e.py:328-331/:343-358/:384-393 — seat nodes are
  ``auxiliary=True``, ``opponent_case`` chains the turns, every seat writes ONE
  transcript slot reduced by ``debate.transcript_reducer@1``; the judge binding is
  ``DebateCfg.judge_node_id`` and the transcript is deliberately NOT a Dependency
  (test_phase8_e2e.py:551-565 — the same honest scoping this preset inherits).
* **Phase 1 InputArtifactBinding.** context.py:647-673 / :676-716; built at dispatch
  ONLY from upstream plan-node outputs (dag.py:380-430). ``PlanDraft`` has no
  external-input and no badge field (spec.py:647-684). Hence Amendment 3's ruling:
  the subject is **run-scoped**, carried beside the draft on
  :class:`MaterializedDeepDecide`, never forged into an ``InputArtifactBinding``.
* **Workers/outputs.** ``dec.pm`` REQUIRES ``research_plan``/``sentiment`` (sole
  producers ``dec.research_mgr``/``text.sentiment``) and ``dec.trader`` REQUIRES
  ``portfolio_decision`` — the ruled ten-worker validation-green set.

Everything here runs against the REAL sealed Phase-9 catalog + registry; the only
synthetic object is the trimmed bridge-free catalog used for the full
``check_runtime_support`` matrix (the reviewed test_phase8_e2e:276-322 stand-in
idiom — capability-bridge binding is orthogonal Phase-9 data-method work).
"""
from __future__ import annotations

import ast
import codecs
import json
import re
from pathlib import Path
from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

import guanlan_v2.orchestration.lane_catalog as lc
from guanlan_v2.orchestration import presets as P
from guanlan_v2.orchestration.adapters import chain
from guanlan_v2.orchestration.catalog import (
    CatalogError,
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
from guanlan_v2.orchestration.debate import DEBATE_MAX_ROUNDS, analyze_debates
from guanlan_v2.orchestration.digest import canonical_json
from guanlan_v2.orchestration.enums import (
    ApprovalPolicy,
    ExecutionKind,
    PlanSource,
    ToolCallRequirement,
)
from guanlan_v2.orchestration.eventstore import RuntimeStores, SchemaRegistryResolver
from guanlan_v2.orchestration.market import factors as market_factors
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
from guanlan_v2.orchestration.spec import OrchestrationRequest, validate_plan_draft
import guanlan_v2.orchestration.worker as W

from guanlan_v2.orchestration.pipeline import assembly as assembly_mod
from guanlan_v2.orchestration.pipeline.assembly import (
    PHASE10_PRESETS_V2_DIR,
    PLAN_PRESET_RECORD_V2_SCHEMA_REF,
    PRODUCTION_PRESETS_DIR,
    PlanPresetRecordV2,
    PresetBaseRefused,
    build_phase10_preset_registry,
    load_phase10_preset_registry,
)
from guanlan_v2.orchestration.data.runtime import SubjectParams
from guanlan_v2.orchestration.pipeline.contracts import RunSubject
from guanlan_v2.orchestration.pipeline import deep_decide as deep_decide_mod
from guanlan_v2.orchestration.pipeline.deep_decide import (
    CONTEXT_SNAPSHOT_DATA_DATE_BADGE_PREFIX,
    CONTEXT_SNAPSHOT_STALE_BADGE,
    DEEP_DECIDE_DEBATE_ID,
    DEEP_DECIDE_PRESET_FILE,
    DEEP_DECIDE_PRESET_ID,
    DEEP_DECIDE_TERMINAL_NODE_ID,
    DEEP_DECIDE_WORKER_IDS,
    SUBJECT_RUN_SCOPED_BADGE,
    ClockRefused,
    ContextSnapshotRefused,
    DeepDecideError,
    MaterializedDeepDecide,
    SubjectRefused,
    materialize_deep_decide_draft,
    session_date_of,
)

UTC = timezone.utc
#: the frozen session clock (2026-07-24 07:00Z == 2026-07-24 15:00 +08:00).
NOW = datetime(2026, 7, 24, 7, 0, tzinfo=UTC)
PHASE8_REGISTRY_DIGEST = (
    "d719e19bc8c64f56324ee36ca0d3aa039e5eac1c9488d80babe6ddce81e5e089"
)
PHASE7_BASELINE = "main.research_baseline"

#: The golden record digest of the sealed deep-decide preset. ANY graph edit
#: (node, dependency, debate, reducer, sink, budget, description) moves it.
#: Frozen 2026-07-28 over the reviewed ten-worker graph.
DEEP_DECIDE_RECORD_DIGEST = (
    "d7c5092fda61ca242de3fb91f48897e06fcc2e8a5836bfae80aae23e6e08a918"
)
#: The Phase-7 baseline record digest — must stay byte-identical after Phase 10
#: adds its own committed preset (the "Phase 7 golden byte-identical" invariant).
PHASE7_BASELINE_RECORD_DIGEST = (
    "028ff5246ac97988ec2b778c994fe8a782c776098c1edb7d725d646ceff6704e"
)


# =========================================================================== #
# fixtures — the REAL sealed Phase-9 catalog / registry / context               #
# =========================================================================== #
class _FixedClock:
    def __init__(self, now: datetime = NOW) -> None:
        self._now = now

    def now(self) -> datetime:
        return self._now


def _phase9_registry():
    return chain.build_phase9_registry(PHASE8_REGISTRY_DIGEST)


@pytest.fixture(scope="module")
def env():
    """The real sealed Phase-9 catalog + registry + an empty-memory ContextSnapshot."""
    registry = _phase9_registry()
    snapshot = chain.phase9_catalog_snapshot()
    resolver = SchemaRegistryResolver()
    resolver.register(registry)
    stores = RuntimeStores(
        resolver=resolver, clock=_FixedClock(),
        allowed_cell_namespaces=(W.PROMPT_CELL_NAMESPACE,))
    mem = P.build_empty_memory_context(
        data_context=P.pilot_data_context(as_of=NOW), stores=stores,
        registry_digest=registry.registry_digest, built_at=NOW)
    context = mem.context
    ctx_ref = PayloadRef(
        namespace="main", object_id="ctx-deep-1",
        content_digest=context.content_digest)
    request = OrchestrationRequest(
        request_id="req-deep-1", goal="观澜 · 落子深度研判 (single stock)",
        workflow="orchestrate_only", fallback_preset_id=None,
        approval_policy=ApprovalPolicy.REQUIRED)
    return {
        "registry": registry, "snapshot": snapshot, "context": context,
        "ctx_ref": ctx_ref, "request": request,
    }


def _subject_ref(code: str = "600519", as_of: datetime = NOW) -> TypedPayloadRef:
    """A committed ``RunSubject@1`` typed ref (the run-scoped subject authority)."""
    subject = RunSubject(code=code, as_of=as_of)
    return TypedPayloadRef(
        payload_ref=PayloadRef(
            namespace="main", object_id=f"subject-{code}",
            content_digest=subject.semantic_digest()),
        schema_ref=SchemaRef(name="RunSubject", version="1"))


def _materialize(env, *, clock=None, context=..., ctx_ref=..., subject=...,
                 registry_override=None, run_subject=None):
    # NOTE: the helper's historical ``subject=`` kwarg is the subject REF; the
    # materializer's L1 ``subject=`` kwarg (the committed RunSubject object for
    # the data-param stamp) is this helper's ``run_subject=``.
    presets = registry_override or load_phase10_preset_registry(PRODUCTION_PRESETS_DIR)
    return materialize_deep_decide_draft(
        request=env["request"],
        preset_registry=presets,
        context_snapshot_ref=env["ctx_ref"] if ctx_ref is ... else ctx_ref,
        subject_ref=_subject_ref() if subject is ... else subject,
        clock=clock or _FixedClock(),
        context=env["context"] if context is ... else context,
        catalog=env["snapshot"],
        schema_registry=env["registry"],
        draft_id="draft-deep-1",
        run_id="run-deep-1",
        subject=run_subject,
    )


@pytest.fixture(scope="module")
def materialized(env):
    return _materialize(env)


# =========================================================================== #
# the full Phase-9 CatalogRuntime (every layer's material loader, unioned)      #
# =========================================================================== #
def _full_phase9_runtime(snapshot):
    """Assemble EVERY sealed Phase-9 material and build the verified CatalogRuntime.

    Task 0b deliberately did not guess this union (its concern 2); it is exactly
    the nine owning-module loaders. Filtered to the snapshot's referenced refs per
    the Phase-2 extra-material rule, so the rebuild reproduces the pinned digest.
    """
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
    """The ten real WorkerSpecs with their capability-bridge binding stood down.

    Verbatim the reviewed Phase-8 e2e stand-in rationale: the data-adapter
    capabilities (and the two REQUIRED tool-call policies they serve) are
    orthogonal to the debate/graph topology under test, and real capability-bridge
    binding is Phase-9 data-method work. The sealed catalog grants a data prefetch
    row to ``dec.pm`` ONLY (data/catalog.py:95-97 ``_REVIEWED_INTEGRATION_GRANTS``),
    so ``pv.technical`` / ``text.news`` could never satisfy their REQUIRED
    tool-call arithmetic here — that gap is named, never faked.
    """
    text_m = lc.load_text_lane_materials()
    pv_m = lc.load_pv_lane_materials()
    dec_m = lc.load_decision_lane_materials()
    specs: dict = {}
    specs.update({w.id: w for w in lc.build_text_worker_specs(materials=text_m)})
    specs.update({w.id: w for w in lc.build_pv_worker_specs(materials=pv_m)})
    specs.update({w.id: w for w in lc.build_decision_worker_specs(materials=dec_m)})

    workers = []
    for wid in DEEP_DECIDE_WORKER_IDS:
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
    for m in list(text_m) + list(pv_m) + list(dec_m) + [reducer_material]:
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
        catalog_version="deep-decide-support-v1", content_manifest=tuple(content),
        skill_manifest=tuple(skills), capability_manifest=(), workers=tuple(workers),
        resolved_material=materials)
    source = InMemoryMaterialSource(
        text={(m.ref.id, m.ref.version): m.raw_utf8 for m in materials},
        capabilities={})
    runtime = CatalogRuntime.build(snapshot, source)
    return snapshot, runtime, BridgeCatalogView.build(runtime, {})


# =========================================================================== #
# 1. preset load / normalize / digest pin                                      #
# =========================================================================== #
class TestPresetLoadAndSeal:
    def test_the_committed_file_is_strict_utf8_without_a_bom(self):
        raw = DEEP_DECIDE_PRESET_FILE.read_bytes()
        assert not raw.startswith(codecs.BOM_UTF8)
        payload = json.loads(raw.decode("utf-8"))
        assert payload["schema_version"] == "2"
        assert payload["preset_id"] == DEEP_DECIDE_PRESET_ID
        assert DEEP_DECIDE_PRESET_FILE.parent == PHASE10_PRESETS_V2_DIR
        assert PHASE10_PRESETS_V2_DIR.parent == PRODUCTION_PRESETS_DIR

    def test_the_phase10_loader_seals_and_holds_both_generations(self):
        registry = load_phase10_preset_registry(PRODUCTION_PRESETS_DIR)
        assert registry.sealed is True
        ids = {e.preset_id for e in registry.manifest()}
        assert {PHASE7_BASELINE, DEEP_DECIDE_PRESET_ID} <= ids
        baseline = registry.get(PHASE7_BASELINE)
        deep = registry.get(DEEP_DECIDE_PRESET_ID)
        assert type(baseline) is PlanPresetRecord and baseline.schema_version == "1"
        assert isinstance(deep, PlanPresetRecordV2) and deep.schema_version == "2"
        with pytest.raises(PlanPresetError):
            registry.register(deep)  # sealed

    def test_the_record_digest_is_golden_frozen(self):
        deep = load_phase10_preset_registry(PRODUCTION_PRESETS_DIR).get(
            DEEP_DECIDE_PRESET_ID)
        assert deep.semantic_digest() == DEEP_DECIDE_RECORD_DIGEST, (
            "the sealed deep-decide graph moved; re-review the preset before "
            "re-freezing this golden")

    def test_loading_twice_is_byte_deterministic(self):
        a = load_phase10_preset_registry(PRODUCTION_PRESETS_DIR)
        b = load_phase10_preset_registry(PRODUCTION_PRESETS_DIR)
        assert a.registry_digest == b.registry_digest
        assert a.get(DEEP_DECIDE_PRESET_ID) == b.get(DEEP_DECIDE_PRESET_ID)

    def test_the_phase7_loader_over_the_same_root_is_untouched(self):
        """The v2 file lives in the ``v2/`` subdirectory precisely so the Phase-7
        non-recursive ``root/*.json`` glob (plan_presets.py:277) cannot see it — a
        v2 record is a hard ``PlanPresetError`` for the Phase-7 loader."""
        phase7 = load_preset_registry(PRODUCTION_PRESETS_DIR)
        assert {e.preset_id for e in phase7.manifest()} == {PHASE7_BASELINE}
        assert phase7.get(PHASE7_BASELINE).semantic_digest() == (
            PHASE7_BASELINE_RECORD_DIGEST)

    def test_a_v2_record_is_refused_by_the_phase7_record_model(self):
        raw = DEEP_DECIDE_PRESET_FILE.read_text(encoding="utf-8")
        with pytest.raises(ValidationError, match="schema_version"):
            PlanPresetRecord.model_validate_json(raw)

    def test_a_duplicate_preset_id_across_generations_refuses(self, tmp_path):
        (tmp_path / "a.json").write_bytes(
            (PRODUCTION_PRESETS_DIR / "main_research_baseline.json").read_bytes())
        v2dir = tmp_path / "v2"
        v2dir.mkdir()
        clash = json.loads(DEEP_DECIDE_PRESET_FILE.read_text(encoding="utf-8"))
        clash["preset_id"] = PHASE7_BASELINE
        (v2dir / "clash.json").write_text(json.dumps(clash), encoding="utf-8")
        with pytest.raises(PlanPresetError, match="duplicate"):
            load_phase10_preset_registry(tmp_path)

    def test_a_bom_in_a_v2_file_refuses(self, tmp_path):
        (tmp_path / "v2").mkdir()
        (tmp_path / "v2" / "b.json").write_bytes(
            codecs.BOM_UTF8 + DEEP_DECIDE_PRESET_FILE.read_bytes())
        with pytest.raises(PlanPresetError, match="byte-order mark"):
            load_phase10_preset_registry(tmp_path)


# =========================================================================== #
# 2. the sealed graph: terminal + worker set + debate shape                     #
# =========================================================================== #
class TestSealedGraph:
    @pytest.fixture(scope="class")
    def record(self):
        return load_phase10_preset_registry(PRODUCTION_PRESETS_DIR).get(
            DEEP_DECIDE_PRESET_ID)

    def test_the_worker_set_is_the_ruled_ten(self, record):
        assert sorted(n.worker_id for n in record.nodes) == sorted(
            DEEP_DECIDE_WORKER_IDS)
        assert len(DEEP_DECIDE_WORKER_IDS) == 10
        assert len(set(DEEP_DECIDE_WORKER_IDS)) == 10

    def test_dec_trader_is_the_only_terminal_and_emits_only_the_proposal(
            self, record, env):
        assert record.sink_node_ids == (DEEP_DECIDE_TERMINAL_NODE_ID,)
        by_id = {n.id: n for n in record.nodes}
        terminal = by_id[DEEP_DECIDE_TERMINAL_NODE_ID]
        assert terminal.worker_id == "dec.trader"
        worker = {w.id: w for w in env["snapshot"].workers}["dec.trader"]
        assert [o.schema_ref.key for o in worker.outputs] == [
            "PortfolioTargetProposal@1"]

    def test_the_debate_is_one_bull_bear_round_judged_by_pm(self, record):
        assert len(record.debates) == 1
        debate = record.debates[0]
        assert debate.id == DEEP_DECIDE_DEBATE_ID
        assert debate.seats == ("bull", "bear")
        assert debate.turn_order == ("bull", "bear")
        assert debate.max_rounds == 1 <= DEBATE_MAX_ROUNDS
        seats = [n for n in record.nodes if n.debate_id == debate.id]
        assert {(n.round_role, n.debate_round, n.debate_turn) for n in seats} == {
            ("bull", 1, 1), ("bear", 1, 2)}
        # the judge is a real node and NOT a seat of its own debate.
        by_id = {n.id: n for n in record.nodes}
        assert debate.judge_node_id in by_id
        assert by_id[debate.judge_node_id].debate_id is None
        assert by_id[debate.judge_node_id].worker_id == "dec.pm"

    def test_the_transcript_slot_is_multi_written_and_reduced(self, record):
        seats = [n for n in record.nodes if n.debate_id is not None]
        slots = {n.writes_slot for n in seats}
        assert len(slots) == 1, "both seats write ONE transcript slot"
        slot = slots.pop()
        assert len(record.reducers) == 1
        reducer = record.reducers[0]
        assert reducer.slot == slot
        assert sorted(reducer.producer_node_ids) == sorted(n.id for n in seats)
        assert reducer.reducer_ref.id == "debate.transcript_reducer"
        assert reducer.output_schema_ref == SchemaRef(
            name="DebateTranscript", version="1")

    def test_evidence_and_debate_nodes_are_auxiliary_and_honestly_described(
            self, record):
        auxiliary = {n.id for n in record.nodes if n.auxiliary}
        reaching = {n.id for n in record.nodes if not n.auxiliary}
        # the sink chain is the only non-auxiliary path.
        assert DEEP_DECIDE_TERMINAL_NODE_ID in reaching
        for node in record.nodes:
            if node.worker_id in {"pv.price_action", "pv.technical",
                                  "pv.microstructure", "text.news",
                                  "dec.bull", "dec.bear"}:
                assert node.id in auxiliary, node.id
        assert "auxiliary" in record.description

    def test_the_preset_carries_no_code_anywhere(self, record):
        """Structurally code-free: no node params at all, and no six-digit run in
        the record's canonical JSON (a param-injected code is impossible — all ten
        workers are paramsless, so Phase 1 refuses node params outright)."""
        for node in record.nodes:
            assert node.params == {}, node.id
        blob = canonical_json(record)
        assert re.search(r'"\d{6}"', blob) is None, blob


# =========================================================================== #
# 3. the v2 record model's own admissibility surface                            #
# =========================================================================== #
class TestPlanPresetRecordV2:
    def _fields(self, **overrides):
        base = json.loads(DEEP_DECIDE_PRESET_FILE.read_text(encoding="utf-8"))
        base.update(overrides)
        return base

    def test_it_is_a_plan_preset_record_so_the_phase7_registry_accepts_it(self):
        record = load_phase10_preset_registry(PRODUCTION_PRESETS_DIR).get(
            DEEP_DECIDE_PRESET_ID)
        assert isinstance(record, PlanPresetRecord)
        registry = PlanPresetRegistry()
        registry.register(record)
        assert registry.get(DEEP_DECIDE_PRESET_ID) is record

    def test_the_registered_schema_identity_is_plan_preset_record_v2(self):
        assert PLAN_PRESET_RECORD_V2_SCHEMA_REF == SchemaRef(
            name="PlanPresetRecord", version="2")

    def test_a_gate_id_is_refused(self):
        fields = self._fields()
        fields["nodes"][0]["gate_ids"] = ["g1"]
        with pytest.raises(ValidationError, match="gate_ids"):
            PlanPresetRecordV2.model_validate_json(json.dumps(fields))

    def test_node_params_are_refused(self):
        fields = self._fields()
        fields["nodes"][0]["params"] = {"code": "600519"}
        with pytest.raises(ValidationError, match="params"):
            PlanPresetRecordV2.model_validate_json(json.dumps(fields))

    def test_max_attempts_beyond_the_v2_cap_is_refused(self):
        fields = self._fields()
        fields["nodes"][0]["max_attempts"] = 3
        with pytest.raises(ValidationError, match="max_attempts"):
            PlanPresetRecordV2.model_validate_json(json.dumps(fields))

    def test_a_seat_referencing_an_undeclared_debate_is_refused(self):
        fields = self._fields()
        for node in fields["nodes"]:
            if node.get("debate_id"):
                node["debate_id"] = "nope"
                break
        with pytest.raises(ValidationError, match="debate"):
            PlanPresetRecordV2.model_validate_json(json.dumps(fields))

    def test_a_judge_that_is_a_seat_of_its_own_debate_is_refused(self):
        fields = self._fields()
        seat_id = next(n["id"] for n in fields["nodes"] if n.get("debate_id"))
        fields["debates"][0]["judge_node_id"] = seat_id
        with pytest.raises(ValidationError, match="judge"):
            PlanPresetRecordV2.model_validate_json(json.dumps(fields))

    def test_a_multi_writer_slot_without_a_reducer_is_refused(self):
        fields = self._fields()
        fields["reducers"] = []
        with pytest.raises(ValidationError, match="reducer"):
            PlanPresetRecordV2.model_validate_json(json.dumps(fields))

    def test_a_debate_round_beyond_the_lane_d_cap_is_refused(self):
        fields = self._fields()
        fields["debates"][0]["max_rounds"] = DEBATE_MAX_ROUNDS + 1
        with pytest.raises(ValidationError, match="max_rounds"):
            PlanPresetRecordV2.model_validate_json(json.dumps(fields))


# =========================================================================== #
# 4. materialization — happy path                                              #
# =========================================================================== #
class TestMaterializeHappy:
    def test_it_returns_the_composite_with_the_bound_subject(self, materialized):
        assert isinstance(materialized, MaterializedDeepDecide)
        assert materialized.subject_ref == _subject_ref()
        assert materialized.subject_ref.schema_ref == SchemaRef(
            name="RunSubject", version="1")

    def test_the_draft_is_a_preset_sourced_main_draft(self, materialized, env):
        draft = materialized.draft
        assert draft.source is PlanSource.PRESET
        assert draft.phase == "main"
        assert draft.request_id == env["request"].request_id
        assert draft.context_snapshot_ref == env["ctx_ref"]
        assert draft.catalog_digest == env["snapshot"].catalog_digest
        assert draft.schema_registry_digest == env["registry"].registry_digest
        assert draft.approval_policy is ApprovalPolicy.REQUIRED

    def test_the_code_appears_nowhere_in_the_draft(self, materialized):
        draft = materialized.draft
        assert draft.universe == ()
        for node in draft.nodes:
            assert node.params == {}
        blob = canonical_json(draft.executable_projection())
        assert "600519" not in blob
        assert re.search(r'"\d{6}"', blob) is None, blob

    def test_the_reviewed_graph_is_copied_verbatim_from_the_record(
            self, materialized):
        record = load_phase10_preset_registry(PRODUCTION_PRESETS_DIR).get(
            DEEP_DECIDE_PRESET_ID)
        draft = materialized.draft
        assert draft.nodes == record.nodes
        assert draft.sink_node_ids == record.sink_node_ids
        assert draft.debates == record.debates
        assert draft.reducers == record.reducers
        assert draft.budget_request_tokens == record.budget_request_tokens
        assert (draft.budget_request_llm_invocations
                == record.budget_request_llm_invocations)
        assert draft.max_concurrency == record.max_concurrency
        assert draft.gates == () and draft.stop_condition_refs == ()

    def test_one_record_digest_serves_every_stock(self, env):
        """Code-agnostic: two different subjects yield drafts whose executable
        projections are byte-identical (only the run-scoped subject differs)."""
        a = _materialize(env, subject=_subject_ref("600519"))
        b = _materialize(env, subject=_subject_ref("000001"))
        assert canonical_json(a.draft.executable_projection()) == canonical_json(
            b.draft.executable_projection())
        assert a.subject_ref != b.subject_ref

    def test_the_run_scoped_subject_badge_is_always_present(self, materialized):
        assert SUBJECT_RUN_SCOPED_BADGE in materialized.badges


# =========================================================================== #
# 4b. materialization-time subject-params stamping (L1 / D-0)                   #
# =========================================================================== #
class TestSubjectParamsStamp:
    """L1 Task 2 — the D-0 verbatim seam: at materialize time the ONE reviewed
    recipe (``SubjectParams.project``) projects the run's committed
    ``RunSubject@1`` into the closed two-key document, carried BESIDE the draft
    on :class:`MaterializedDeepDecide`. The sealed record stays code-free:
    ``PlanNode.params`` stays empty and the draft's executable projection does
    not move a byte — the stamp is out-of-band by construction.
    """

    def test_the_stamp_equals_the_one_recipe_over_the_committed_subject(self, env):
        """The stamped params provably come from THE committed artifact the ref
        names. The ref here is opaque (its object_id encodes nothing) and the
        subject's ``as_of`` differs from the clock's instant, so a stamp
        fabricated from ref metadata, goal text or the clock cannot equal the
        ONE recipe's projection of the verified subject object."""
        subject = RunSubject(code="600519", as_of=NOW - timedelta(hours=5))
        ref = TypedPayloadRef(
            payload_ref=PayloadRef(
                namespace="main", object_id="deep-artifact-0007",
                content_digest=subject.semantic_digest()),
            schema_ref=SchemaRef(name="RunSubject", version="1"))
        result = _materialize(env, subject=ref, run_subject=subject)
        assert result.subject_params == SubjectParams.project(
            code=subject.code, as_of=subject.as_of)
        assert result.subject_params.code_value[0].code == "600519"

    def test_a_ref_that_does_not_bind_the_subject_refuses_typed(self, env):
        """The digest bond — ``subject.semantic_digest() ==
        subject_ref.payload_ref.content_digest``, the SAME equality the
        screening lane enforces on its committed subjects (screening.py's
        SubjectRefused) — is verified, never assumed."""
        mismatched = RunSubject(code="000001", as_of=NOW)
        with pytest.raises(SubjectRefused, match="does not bind"):
            _materialize(env, run_subject=mismatched)  # default ref binds 600519

    def test_a_non_run_subject_object_refuses_typed(self, env):
        with pytest.raises(SubjectRefused, match="RunSubject"):
            _materialize(env, run_subject={"code": "600519", "as_of": NOW})

    def test_without_a_subject_the_stamp_is_honestly_none(self, materialized):
        """``subject=None`` keeps every pre-existing construction meaning what
        it meant (the module's own ``preset_id`` convention): no default and no
        forged subject — the data bridge refuses loudly downstream instead."""
        assert materialized.subject_params is None

    def test_the_draft_is_untouched_by_the_stamp(self, env):
        """The sealed-record rule never moved: stamping happens BESIDE the
        draft, so ``PlanNode.params`` stays empty and the executable projection
        is byte-identical to an unstamped run's — and across stocks."""
        stamped = _materialize(
            env, run_subject=RunSubject(code="600519", as_of=NOW))
        unstamped = _materialize(env)
        blob = canonical_json(stamped.draft.executable_projection())
        for node in stamped.draft.nodes:
            assert node.params == {}
        assert blob == canonical_json(unstamped.draft.executable_projection())
        assert "600519" not in blob
        other = _materialize(
            env, subject=_subject_ref("000001"),
            run_subject=RunSubject(code="000001", as_of=NOW))
        assert canonical_json(other.draft.executable_projection()) == blob
        assert other.subject_params != stamped.subject_params

    def test_subject_checks_still_precede_context_checks(self, env):
        """Refusal ORDER preserved: the bond check joins the SUBJECT block, so
        a lying subject outranks a missing context — and a missing subject_ref
        still outranks everything the context block would say."""
        with pytest.raises(SubjectRefused):
            _materialize(env, subject=None, context=None, ctx_ref=None)
        mismatched = RunSubject(code="000001", as_of=NOW)
        with pytest.raises(SubjectRefused):
            _materialize(env, run_subject=mismatched, context=None, ctx_ref=None)


# =========================================================================== #
# 5. Phase 1 validation + profile-v2 runtime support                            #
# =========================================================================== #
class TestValidationAndRuntimeSupport:
    def test_phase1_validation_is_green_against_the_real_sealed_catalog(
            self, materialized, env):
        report = validate_plan_draft(
            materialized.draft, request=env["request"], context=env["context"],
            catalog=env["snapshot"], schema_registry=env["registry"])
        assert report.valid is True, [
            (i.code, i.node_id, i.message) for i in report.issues]

    def test_params_on_a_paramsless_worker_would_be_refused(
            self, materialized, env):
        """The positive control for the structural code-free claim: injecting any
        param onto one of these workers is a Phase-1 ``params_not_allowed``."""
        draft = materialized.draft
        nodes = list(draft.nodes)
        nodes[0] = nodes[0].model_copy(update={"params": {"code": "600519"}})
        tampered = draft.model_copy(update={"nodes": tuple(nodes)})
        report = validate_plan_draft(
            tampered, request=env["request"], context=env["context"],
            catalog=env["snapshot"], schema_registry=env["registry"])
        assert "params_not_allowed" in {i.code for i in report.issues}

    def test_the_profile_v2_analyzers_are_clean_on_the_real_catalog_runtime(
            self, materialized, phase9_runtime):
        draft = materialized.draft
        assert analyze_debates(
            draft, catalog=phase9_runtime, profile=STATIC_RUNTIME_PROFILE_V2) == ()
        assert analyze_reducers(draft, catalog=phase9_runtime) == ()
        assert analyze_retry_repair(
            draft, profile=STATIC_RUNTIME_PROFILE_V2) == ()

    def test_check_runtime_support_admits_the_preset_sourced_debate_draft(
            self, env, trimmed_catalog):
        """The Amendment-3 contingency, answered: profile v2 does NOT reject a
        ``source=PRESET`` debate draft."""
        snapshot, runtime, view = trimmed_catalog
        result = _materialize(env)
        reducers = tuple(
            r.model_copy(update={"reducer_ref": lc._debate_reducer_material().ref})
            for r in result.draft.reducers)
        draft = result.draft.model_copy(update={
            "catalog_version": snapshot.catalog_version,
            "catalog_digest": snapshot.catalog_digest,
            "reducers": reducers,
        })
        phase1 = validate_plan_draft(
            draft, request=env["request"], context=env["context"],
            catalog=snapshot, schema_registry=env["registry"])
        assert phase1.valid is True, [(i.code, i.node_id) for i in phase1.issues]
        support = check_runtime_support(
            draft, phase1_report=phase1, context=env["context"],
            context_requirements=None, catalog=runtime, bridge_view=view,
            schema_registry=env["registry"], profile=STATIC_RUNTIME_PROFILE_V2)
        assert support.supported is True, [
            (i.code, i.node_id, i.explanation) for i in support.issues]

    def test_the_llm_budget_equals_the_analyzers_exact_requirement(
            self, materialized, env):
        """`analyze_debates`' exact pre-reservation rule (debate.py:384-398):
        `budget_request_llm_invocations >= expanded debate seats + non-debate LLM
        nodes`. The sealed preset is pinned to EQUALITY — an over-request would
        pass the analyzer while silently reserving budget nobody can spend."""
        draft = materialized.draft
        workers = {w.id: w for w in env["snapshot"].workers}
        seats = sum(1 for n in draft.nodes if n.debate_id is not None)
        non_debate_llm = sum(
            1 for n in draft.nodes
            if n.debate_id is None
            and workers[n.worker_id].execution.kind is ExecutionKind.LLM)
        assert (seats, non_debate_llm) == (2, 6)
        assert draft.budget_request_llm_invocations == seats + non_debate_llm == 8


# =========================================================================== #
# 6. recency badge matrix                                                      #
# =========================================================================== #
class TestRecencyBadge:
    def test_the_session_date_helper_is_the_identity_pinned_one(self):
        assert session_date_of is market_factors._session_date
        # +08:00 session terms: 22:00Z is already the NEXT session date.
        assert session_date_of(datetime(2026, 7, 24, 22, 0, tzinfo=UTC)) == "2026-07-25"

    def test_no_badge_when_the_snapshot_is_the_current_session(self, env):
        result = _materialize(env, clock=_FixedClock(NOW))
        assert CONTEXT_SNAPSHOT_STALE_BADGE not in result.badges

    def test_badge_when_the_snapshot_data_date_is_behind_the_session(self, env):
        later = _FixedClock(NOW + timedelta(days=1))
        result = _materialize(env, clock=later)
        assert CONTEXT_SNAPSHOT_STALE_BADGE in result.badges
        # the dated detail names WHICH session the snapshot is from.
        assert f"{CONTEXT_SNAPSHOT_DATA_DATE_BADGE_PREFIX}2026-07-24" in result.badges

    def test_no_badge_when_the_snapshot_is_ahead_of_the_session(self, env):
        """Honest: a snapshot dated after the session is never badged 'stale'
        (and is never re-run intraday)."""
        earlier = _FixedClock(NOW - timedelta(days=3))
        result = _materialize(env, clock=earlier)
        assert CONTEXT_SNAPSHOT_STALE_BADGE not in result.badges

    def test_the_badge_crosses_at_the_plus_eight_session_boundary(self, env):
        """22:00Z on the snapshot's own UTC date is already the NEXT +08:00
        session, so the badge fires there but not at 15:00 +08:00."""
        assert CONTEXT_SNAPSHOT_STALE_BADGE not in _materialize(
            env, clock=_FixedClock(datetime(2026, 7, 24, 15, 59, tzinfo=UTC))).badges
        assert CONTEXT_SNAPSHOT_STALE_BADGE in _materialize(
            env, clock=_FixedClock(datetime(2026, 7, 24, 16, 0, tzinfo=UTC))).badges


# =========================================================================== #
# 7. typed refusals — missing snapshot / missing subject                        #
# =========================================================================== #
class TestRefusals:
    def test_a_missing_context_snapshot_refuses_typed(self, env):
        with pytest.raises(ContextSnapshotRefused):
            _materialize(env, context=None)
        with pytest.raises(ContextSnapshotRefused):
            _materialize(env, ctx_ref=None)

    def test_a_context_ref_that_does_not_bind_the_snapshot_refuses(self, env):
        wrong = PayloadRef(namespace="main", object_id="ctx-x",
                           content_digest="a" * 64)
        with pytest.raises(ContextSnapshotRefused):
            _materialize(env, ctx_ref=wrong)
        off_namespace = env["ctx_ref"].model_copy(update={"namespace": "shadow"})
        with pytest.raises(ContextSnapshotRefused):
            _materialize(env, ctx_ref=off_namespace)

    def test_a_missing_subject_refuses_typed(self, env):
        with pytest.raises(SubjectRefused):
            _materialize(env, subject=None)

    def test_a_naive_clock_refuses_typed(self, env):
        """`session_date_of` calls `astimezone`, which reads a naive datetime as
        MACHINE-LOCAL time — on a non-UTC host that silently shifts the +08:00
        session date and flips the recency badge. Refused, never coerced."""
        naive = _FixedClock(datetime(2026, 7, 24, 7, 0))  # no tzinfo
        with pytest.raises(ClockRefused, match="timezone-aware"):
            _materialize(env, clock=naive)

    def test_a_subject_ref_of_the_wrong_schema_refuses_typed(self, env):
        wrong = TypedPayloadRef(
            payload_ref=PayloadRef(namespace="main", object_id="not-a-subject",
                                   content_digest="b" * 64),
            schema_ref=SchemaRef(name="CandidateSlate", version="1"))
        with pytest.raises(SubjectRefused):
            _materialize(env, subject=wrong)

    def test_a_registry_without_the_deep_preset_refuses_typed(self, env):
        empty = PlanPresetRegistry()
        empty.seal()
        with pytest.raises(PlanPresetError, match=DEEP_DECIDE_PRESET_ID):
            _materialize(env, registry_override=empty)

    def test_a_v1_record_under_the_deep_preset_id_refuses_typed(self, env):
        baseline = load_preset_registry(PRODUCTION_PRESETS_DIR).get(PHASE7_BASELINE)
        impostor = PlanPresetRecord(
            preset_id=DEEP_DECIDE_PRESET_ID, version="1",
            description=baseline.description, nodes=baseline.nodes,
            sink_node_ids=baseline.sink_node_ids,
            budget_request_tokens=baseline.budget_request_tokens,
            budget_request_llm_invocations=baseline.budget_request_llm_invocations,
            max_concurrency=baseline.max_concurrency)
        registry = PlanPresetRegistry()
        registry.register(impostor)
        registry.seal()
        with pytest.raises(DeepDecideError):
            _materialize(env, registry_override=registry)


# =========================================================================== #
# 8. the registry extension chain (Task 0b)                                    #
# =========================================================================== #
class TestRegistryExtensionChain:
    def test_the_phase10_registry_holds_the_base_plus_the_deep_record(self):
        base = load_preset_registry(PRODUCTION_PRESETS_DIR)
        deep = load_phase10_preset_registry(PRODUCTION_PRESETS_DIR).get(
            DEEP_DECIDE_PRESET_ID)
        extended = build_phase10_preset_registry(base, (deep,))
        assert extended.sealed is True
        assert {e.preset_id for e in extended.manifest()} == {
            PHASE7_BASELINE, DEEP_DECIDE_PRESET_ID}
        assert extended.get(DEEP_DECIDE_PRESET_ID).semantic_digest() == (
            DEEP_DECIDE_RECORD_DIGEST)
        # the Phase-7 base is never mutated.
        assert {e.preset_id for e in base.manifest()} == {PHASE7_BASELINE}

    def test_the_phase7_baseline_record_stays_byte_identical(self):
        base = load_preset_registry(PRODUCTION_PRESETS_DIR)
        extended = build_phase10_preset_registry(
            base,
            (load_phase10_preset_registry(PRODUCTION_PRESETS_DIR).get(
                DEEP_DECIDE_PRESET_ID),))
        assert extended.get(PHASE7_BASELINE).semantic_digest() == (
            PHASE7_BASELINE_RECORD_DIGEST)
        assert extended.get(PHASE7_BASELINE) == base.get(PHASE7_BASELINE)

    def test_a_v2_record_the_committed_directory_does_not_hold_is_refused_as_base(
            self):
        """The Task-0b subset seal, extended to v2: a base record absent from the
        committed directory (either generation) never reaches production."""
        committed = load_phase10_preset_registry(PRODUCTION_PRESETS_DIR)
        smuggled = committed.get(DEEP_DECIDE_PRESET_ID).model_copy(
            update={"preset_id": "smuggle.deep"})
        base = PlanPresetRegistry()
        base.register(load_preset_registry(PRODUCTION_PRESETS_DIR).get(
            PHASE7_BASELINE))
        base.register(smuggled)
        base.seal()
        with pytest.raises(PresetBaseRefused, match="smuggle.deep"):
            build_phase10_preset_registry(base, ())

    def test_a_committed_v2_record_is_an_admissible_base_member(self):
        """The committed-directory check now spans v2: a base that holds the
        committed deep-decide record is accepted (it IS committed)."""
        committed = load_phase10_preset_registry(PRODUCTION_PRESETS_DIR)
        base = PlanPresetRegistry()
        base.register(committed.get(PHASE7_BASELINE))
        base.register(committed.get(DEEP_DECIDE_PRESET_ID))
        base.seal()
        extended = build_phase10_preset_registry(base, ())
        assert {e.preset_id for e in extended.manifest()} == {
            PHASE7_BASELINE, DEEP_DECIDE_PRESET_ID}

    def test_a_mutated_committed_v2_record_is_refused_as_base(self):
        committed = load_phase10_preset_registry(PRODUCTION_PRESETS_DIR)
        mutated = committed.get(DEEP_DECIDE_PRESET_ID).model_copy(
            update={"description": "mutated"})
        base = PlanPresetRegistry()
        base.register(committed.get(PHASE7_BASELINE))
        base.register(mutated)
        base.seal()
        with pytest.raises(PresetBaseRefused):
            build_phase10_preset_registry(base, ())


# =========================================================================== #
# 9. module layering — the v2 loader lives in assembly, one-directional         #
# =========================================================================== #
class TestPresetLoaderLayering:
    """Review I-2: `PlanPresetRecordV2` / `load_phase10_preset_registry` live in
    ``assembly`` beside ``PRODUCTION_PRESETS_DIR``, so ``build_phase10_preset_registry``
    no longer reaches back into ``deep_decide`` at call time. Task 3 binds the same
    loader without importing this task's module."""

    def test_the_v2_surface_is_owned_by_assembly(self):
        assert PlanPresetRecordV2.__module__ == (
            "guanlan_v2.orchestration.pipeline.assembly")
        assert load_phase10_preset_registry.__module__ == (
            "guanlan_v2.orchestration.pipeline.assembly")
        for name in ("PHASE10_PRESETS_V2_DIR", "PHASE10_PRESETS_V2_DIRNAME",
                     "PLAN_PRESET_RECORD_V2_SCHEMA_REF", "PlanPresetRecordV2",
                     "load_phase10_preset_registry"):
            assert name in assembly_mod.__all__, name

    def test_assembly_never_imports_deep_decide(self):
        """Source-level, not just import-time: a function-local import would not
        show up in ``sys.modules`` until the call, so the module text is the pin."""
        source = Path(assembly_mod.__file__).read_text(encoding="utf-8")
        assert "deep_decide" not in source
        tree = ast.parse(source)
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
            elif isinstance(node, ast.Import):
                imported.update(a.name for a in node.names)
        assert not any("pipeline.deep_decide" in m for m in imported)

    def test_deep_decide_consumes_the_assembly_surface(self):
        """The one-directional edge, asserted by object identity (not by name)."""
        assert deep_decide_mod.PlanPresetRecordV2 is PlanPresetRecordV2
        assert deep_decide_mod.PHASE10_PRESETS_V2_DIR is PHASE10_PRESETS_V2_DIR
        assert deep_decide_mod.PLAN_PRESET_RECORD_V2_SCHEMA_REF is (
            PLAN_PRESET_RECORD_V2_SCHEMA_REF)
        assert DEEP_DECIDE_PRESET_FILE.parent == PHASE10_PRESETS_V2_DIR


# =========================================================================== #
# 10. the Lane-0 experience bridge over the full Phase-9 runtime (the flip)     #
# =========================================================================== #
def _full_phase9_bridge_view(phase9_runtime):
    """The full three-analyzer bridge view Task 7's real runner assembles."""
    import guanlan_v2.orchestration.bootstrap as bs
    import guanlan_v2.orchestration.data.catalog as dcat
    import guanlan_v2.orchestration.memory.catalog as mcat

    data_surface = dcat.phase3_data_surface()
    memory_surface = mcat.phase3_memory_surface()
    lane0 = bs.load_lane0_catalog()

    def _key(ref):
        return (ref.id, ref.version, ref.content_digest)

    return BridgeCatalogView.build(phase9_runtime, {
        _key(data_surface.analyzer_ref): dcat.DataBridgeSupportAnalyzer(),
        _key(memory_surface.analyzer_ref): mcat.MemoryBridgeSupportAnalyzer(),
        lane0.analyzer_key: bs.ExperienceBridgeSupportAnalyzer(),
    })


class TestFullPhase9BridgeView:
    """THE CONSCIOUS FLIP (Task 11, controller ruling on the grant-gap plate).

    This class replaces the strict-xfail that pinned the old behaviour: the
    Lane-0 experience bridge activates on the ``experience_cases`` read category
    (which ``dec.research_mgr`` declares) and its analyzer used to RAISE
    ``CatalogError`` for any worker without a reviewed prefetch row — so
    ``check_runtime_support`` over the full Phase-9 runtime could not even
    COMPLETE for a plan containing ``dec.research_mgr`` (bootstrap.py:462).

    The ruled discrimination (bootstrap.py, Task 11): a worker whose capability
    allowlist does NOT hold the experience capability (i.e. no grant was ever
    reviewed for it — the ``dec.research_mgr`` case) now receives an honest
    ZERO-CONTRIBUTION summary (``min=0``/``max=0``/no capability refs — exactly
    the data analyzer's posture for a rowless worker), while a worker whose
    allowlist DOES hold the capability but has no row keeps the LOUD raise
    (a Lane-0 misconfiguration must never degrade silently).

    The GRANT GAP ITSELF IS NOT CLOSED and is accepted as permanent-honest for
    Phase 10 (charter evidence in .superpowers/sdd/task-11-p10-report.md): the
    support report below completes but is honestly UNSUPPORTED — the REQUIRED
    tool-call arithmetic of ``pv.technical`` / ``text.news`` stays unmet because
    the sealed Phase-3 prefetch binding grants a data row to ``dec.pm`` only.
    """

    @pytest.fixture(scope="class")
    def support(self, env, materialized, phase9_runtime):
        view = _full_phase9_bridge_view(phase9_runtime)
        draft = materialized.draft
        assert any(n.worker_id == "dec.research_mgr" for n in draft.nodes)
        phase1 = validate_plan_draft(
            draft, request=env["request"], context=env["context"],
            catalog=env["snapshot"], schema_registry=env["registry"])
        report = check_runtime_support(
            draft, phase1_report=phase1, context=env["context"],
            context_requirements=None, catalog=phase9_runtime, bridge_view=view,
            schema_registry=env["registry"], profile=STATIC_RUNTIME_PROFILE_V2)
        return draft, report

    def test_the_full_view_now_completes_for_a_research_mgr_plan(self, support):
        _draft, report = support
        assert report is not None  # no CatalogError escaped — the flip's core

    def test_the_report_is_honestly_unsupported_naming_the_data_grant_gap(
            self, support):
        """The remaining gap surfaces as ISSUES, never a crash and never a fake
        green: exactly the two REQUIRED-tool-call workers without a reviewed
        data prefetch row (`pv.technical` / `text.news`)."""
        draft, report = support
        assert report.supported is False
        by_code: dict = {}
        for issue in report.issues:
            by_code.setdefault(issue.code, set()).add(issue.node_id)
        node_worker = {n.id: n.worker_id for n in draft.nodes}
        unmet = by_code.pop("tool_calls_required_unmet")
        assert {node_worker[nid] for nid in unmet} == {
            "pv.technical", "text.news"}
        assert not by_code, (
            "unexpected extra support issues beyond the accepted data grant "
            f"gap: {sorted(by_code)}")

    def test_research_mgr_gets_a_zero_contribution_experience_summary(
            self, support):
        """The bridge ACTIVATED and answered honestly (not skipped): the report
        carries an experience.bridge summary for the research node with zero
        bounds and no capability refs."""
        draft, report = support
        research_nodes = {
            n.id for n in draft.nodes if n.worker_id == "dec.research_mgr"}
        mine = [s for s in report.bridge_support_summaries
                if s.bridge_id == "experience.bridge"
                and s.node_id in research_nodes]
        assert len(mine) == len(research_nodes) == 1
        summary = mine[0]
        assert summary.allowed_capability_refs == ()
        assert summary.min_finalized_tool_calls_on_success == 0
        assert summary.max_capability_invocations == 0

    def test_an_allowlisted_worker_without_a_row_still_raises_loudly(
            self, env, phase9_runtime):
        """The DISCRIMINATION tripwire: the loud half is kept. A worker whose
        allowlist HOLDS the experience capability but has no reviewed row is a
        Lane-0 misconfiguration — the analyzer must still raise. Reverting the
        bootstrap.py discrimination cannot pass this class both ways."""
        import guanlan_v2.orchestration.bootstrap as bs

        cap_ref, _mat = bs.experience_retrieve_capability()
        workers = {w.id: w for w in env["snapshot"].workers}
        rm = workers["dec.research_mgr"]
        allowlisted = rm.model_copy(update={
            "capability_allowlist": (cap_ref,),
            # FORBIDDEN ⇔ empty allowlist is a WorkerSpec invariant; the probe
            # worker flips to OPTIONAL so the allowlist can hold the capability.
            "evidence_policy": rm.evidence_policy.model_copy(
                update={"tool_calls": ToolCallRequirement.OPTIONAL}),
        })
        # the sealed descriptor + config bytes, resolved from the REAL view —
        # the exact objects check_runtime_support hands the analyzer.
        view = _full_phase9_bridge_view(phase9_runtime)
        resolved = view.resolve("experience.bridge")
        descriptor = resolved.descriptor
        config_bytes = resolved.config_bytes
        descriptor_ref = resolved.descriptor_ref
        node = type("_N", (), {"id": "n-probe", "params": {}})()
        with pytest.raises(CatalogError, match="no reviewed experience prefetch row"):
            bs.ExperienceBridgeSupportAnalyzer().analyze(
                candidate_plan_digest="0" * 64,
                node=node,
                worker=allowlisted,
                descriptor=descriptor,
                descriptor_ref=descriptor_ref,
                config_bytes=config_bytes,
            )
