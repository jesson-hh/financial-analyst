# -*- coding: utf-8 -*-
"""路线 A — the REDUCED-EVIDENCE deep-decide preset (eight nodes).

Why this preset exists (the empirical finding this module makes permanent):
the sealed ten-node deep preset can never pass ``check_runtime_support`` on the
production chain, because ``pv.technical`` and ``text.news`` declare
``evidence_policy.tool_calls = REQUIRED`` while the reviewed Phase-3 data
prefetch table grants a row to ``dec.pm`` ONLY — so both nodes raise
``tool_calls_required_unmet`` and the whole plan is refused before a single LLM
call. Closing that gap for real needs three layers (subject→data-bridge path L1,
a production data runtime L2, the grants + re-freeze L3), and L2 does not exist
(``docs/superpowers/specs/2026-07-29-post-p10-refreeze-design.md`` §1.5).

Dropping exactly those two auxiliary evidence nodes — they only reach the debate
through ``degrade`` dependencies, and the decision spine
``sentiment → research-mgr → pm → trader`` never touches them — makes the SAME
runtime-support check green. :class:`TestTheSupportFinding` pins BOTH sides of
that against the real sealed Phase-9 catalog + registry + the full three-analyzer
bridge view, so neither half can rot silently.

The honesty red line (deliverable 4 of the charter): a reduced-evidence verdict
must never be readable as a full-evidence verdict. Every product of this preset
— the materialization badges, the seats ledger row, the advisory order record,
the reviewer card — carries :data:`REDUCED_EVIDENCE_BADGE` plus a statement of
what is missing. The materializer additionally REFUSES a record whose worker set
contradicts its own badge in either direction (a reduced preset that smuggled the
evidence workers back, or a full preset that quietly lost them).

Everything here runs against the REAL sealed Phase-9 catalog / registry; zero
network, zero LLM, zero ``var/`` writes.

Run from repo root:
``python -m pytest tests/orchestration/test_pipeline_reduced_deep_preset.py -v``
"""
from __future__ import annotations

import codecs
import json
import re
from datetime import datetime, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

import guanlan_v2.orchestration.lane_catalog as lc
from guanlan_v2.orchestration import presets as P
from guanlan_v2.orchestration.adapters import chain
from guanlan_v2.orchestration.catalog_runtime import (
    BridgeCatalogView,
    CatalogRuntime,
    InMemoryMaterialSource,
)
from guanlan_v2.orchestration.debate import analyze_debates
from guanlan_v2.orchestration.digest import canonical_json
from guanlan_v2.orchestration.enums import (
    ApprovalPolicy,
    ExecutionKind,
    PlanSource,
)
from guanlan_v2.orchestration.eventstore import RuntimeStores, SchemaRegistryResolver
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
from guanlan_v2.orchestration.spec import OrchestrationRequest, validate_plan_draft
import guanlan_v2.orchestration.worker as W

from guanlan_v2.orchestration.pipeline.assembly import (
    PHASE10_PRESETS_V2_DIR,
    PRODUCTION_PRESETS_DIR,
    PlanPresetRecordV2,
    build_phase10_preset_registry,
    load_phase10_preset_registry,
)
from guanlan_v2.orchestration.pipeline.contracts import RunSubject
from guanlan_v2.orchestration.pipeline.deep_decide import (
    DEEP_DECIDE_PRESET_ID,
    DEEP_DECIDE_PRESET_IDS,
    DEEP_DECIDE_WORKER_IDS,
    REDUCED_DEEP_DECIDE_PRESET_FILE,
    REDUCED_DEEP_DECIDE_PRESET_ID,
    REDUCED_DEEP_DECIDE_PRESET_VERSION,
    REDUCED_DEEP_DECIDE_WORKER_IDS,
    REDUCED_EVIDENCE_BADGE,
    REDUCED_EVIDENCE_BADGES,
    REDUCED_EVIDENCE_DROPPED_WORKER_IDS,
    REDUCED_EVIDENCE_MISSING_BADGE,
    REDUCED_EVIDENCE_NOTE,
    SUBJECT_RUN_SCOPED_BADGE,
    DeepDecideError,
    EvidenceScopeMismatch,
    PresetSelectionRefused,
    materialize_deep_decide_draft,
    reduced_evidence_badges,
    render_reduced_evidence_banner,
)
from guanlan_v2.orchestration.pipeline.live_decide import (
    DEEP_PRESET_ENV_VAR,
    resolve_deep_preset_id,
)

UTC = timezone.utc
#: the frozen session clock (2026-07-24 07:00Z == 2026-07-24 15:00 +08:00).
NOW = datetime(2026, 7, 24, 7, 0, tzinfo=UTC)
PHASE8_REGISTRY_DIGEST = (
    "d719e19bc8c64f56324ee36ca0d3aa039e5eac1c9488d80babe6ddce81e5e089"
)
PHASE7_BASELINE = "main.research_baseline"

#: The golden record digest of the REDUCED deep-decide preset. ANY graph edit
#: (node, dependency, debate, reducer, sink, budget, description) moves it.
#: Hand-frozen 2026-07-29 over the reviewed eight-node graph — the test only
#: READS it; it is never regenerated from the artifact under test.
REDUCED_RECORD_DIGEST = (
    "2c4b4e0d33e4db487e93c7aa66a4400371f9eb9e969b3db38bfbc3c769ba2053"
)
#: The sealed TEN-node record digest — must stay byte-identical now that a second
#: v2 deep preset sits beside it in the same committed directory.
DEEP_DECIDE_RECORD_DIGEST = (
    "d7c5092fda61ca242de3fb91f48897e06fcc2e8a5836bfae80aae23e6e08a918"
)
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
    mem = P.build_empty_memory_context(
        data_context=P.pilot_data_context(as_of=NOW), stores=stores,
        registry_digest=registry.registry_digest, built_at=NOW)
    context = mem.context
    ctx_ref = PayloadRef(
        namespace="main", object_id="ctx-reduced-1",
        content_digest=context.content_digest)
    request = OrchestrationRequest(
        request_id="req-reduced-1", goal="观澜 · 落子深度研判 (reduced evidence)",
        workflow="orchestrate_only", fallback_preset_id=None,
        approval_policy=ApprovalPolicy.REQUIRED)
    return {
        "registry": registry, "snapshot": snapshot, "context": context,
        "ctx_ref": ctx_ref, "request": request,
    }


def _subject_ref(code: str = "600519", as_of: datetime = NOW) -> TypedPayloadRef:
    subject = RunSubject(code=code, as_of=as_of)
    return TypedPayloadRef(
        payload_ref=PayloadRef(
            namespace="main", object_id=f"subject-{code}",
            content_digest=subject.semantic_digest()),
        schema_ref=SchemaRef(name="RunSubject", version="1"))


def _materialize(env, *, preset_id=REDUCED_DEEP_DECIDE_PRESET_ID,
                 registry_override=None, subject=...):
    presets = registry_override or load_phase10_preset_registry(PRODUCTION_PRESETS_DIR)
    return materialize_deep_decide_draft(
        request=env["request"],
        preset_registry=presets,
        preset_id=preset_id,
        context_snapshot_ref=env["ctx_ref"],
        subject_ref=_subject_ref() if subject is ... else subject,
        clock=_FixedClock(),
        context=env["context"],
        catalog=env["snapshot"],
        schema_registry=env["registry"],
        draft_id="draft-reduced-1",
        run_id="run-reduced-1",
    )


@pytest.fixture(scope="module")
def materialized(env):
    return _materialize(env)


@pytest.fixture(scope="module")
def full_materialized(env):
    return _materialize(env, preset_id=DEEP_DECIDE_PRESET_ID)


# =========================================================================== #
# the full Phase-9 CatalogRuntime + three-analyzer bridge view                  #
# (verbatim the reviewed test_pipeline_deep_preset assembly — the ONLY way to    #
#  reproduce production's own support arithmetic)                               #
# =========================================================================== #
def _full_phase9_runtime(snapshot):
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


def _full_phase9_bridge_view(phase9_runtime):
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


def _support(env, phase9_runtime, draft):
    view = _full_phase9_bridge_view(phase9_runtime)
    phase1 = validate_plan_draft(
        draft, request=env["request"], context=env["context"],
        catalog=env["snapshot"], schema_registry=env["registry"])
    report = check_runtime_support(
        draft, phase1_report=phase1, context=env["context"],
        context_requirements=None, catalog=phase9_runtime, bridge_view=view,
        schema_registry=env["registry"], profile=STATIC_RUNTIME_PROFILE_V2)
    return phase1, report


# =========================================================================== #
# 1. THE FINDING — both sides, permanently pinned                              #
# =========================================================================== #
class TestTheSupportFinding:
    """The controller's 2026-07-29 probe, promoted to a resident regression.

    Same fixtures, same production analyzers, same profile: the ten-node preset
    is honestly UNSUPPORTED for exactly two reasons, and the eight-node preset
    that drops exactly those two nodes is SUPPORTED.
    """

    @pytest.fixture(scope="class")
    def reduced(self, env, materialized, phase9_runtime):
        return _support(env, phase9_runtime, materialized.draft)

    @pytest.fixture(scope="class")
    def baseline(self, env, full_materialized, phase9_runtime):
        return _support(env, phase9_runtime, full_materialized.draft)

    def test_the_reduced_draft_is_phase1_valid(self, reduced):
        phase1, _report = reduced
        assert phase1.valid is True, [
            (i.code, i.node_id, i.message) for i in phase1.issues]

    def test_the_reduced_draft_is_runtime_supported(self, reduced):
        """THE POINT OF ROUTE A: green on the production support arithmetic."""
        _phase1, report = reduced
        assert report.supported is True, [
            (i.code, i.node_id, i.explanation) for i in report.issues]

    def test_the_untouched_ten_node_baseline_is_still_honestly_unsupported(
            self, baseline, full_materialized):
        """The regression half: adding a reduced preset must NOT make the sealed
        ten-node preset pretend to be supported. Its honest refusal is exactly
        the two REQUIRED-tool-call evidence readers, unchanged."""
        phase1, report = baseline
        assert phase1.valid is True
        assert report.supported is False
        by_code: dict = {}
        for issue in report.issues:
            by_code.setdefault(issue.code, set()).add(issue.node_id)
        node_worker = {n.id: n.worker_id for n in full_materialized.draft.nodes}
        unmet = by_code.pop("tool_calls_required_unmet")
        assert {node_worker[nid] for nid in unmet} == set(
            REDUCED_EVIDENCE_DROPPED_WORKER_IDS)
        assert not by_code, sorted(by_code)

    def test_the_two_dropped_nodes_are_exactly_the_unmet_ones(
            self, baseline, full_materialized):
        """The causal link, asserted rather than assumed: the node ids the
        reduced preset removes ARE the node ids the baseline reports unmet."""
        _phase1, report = baseline
        unmet_nodes = {i.node_id for i in report.issues
                       if i.code == "tool_calls_required_unmet"}
        full_ids = {n.id for n in full_materialized.draft.nodes}
        reduced_ids = {n.id for n in _reduced_record().nodes}
        assert full_ids - reduced_ids == unmet_nodes

    def test_the_profile_v2_analyzers_are_clean_on_the_reduced_draft(
            self, materialized, phase9_runtime):
        draft = materialized.draft
        assert analyze_debates(
            draft, catalog=phase9_runtime, profile=STATIC_RUNTIME_PROFILE_V2) == ()
        assert analyze_reducers(draft, catalog=phase9_runtime) == ()
        assert analyze_retry_repair(
            draft, profile=STATIC_RUNTIME_PROFILE_V2) == ()


def _reduced_record():
    return load_phase10_preset_registry(PRODUCTION_PRESETS_DIR).get(
        REDUCED_DEEP_DECIDE_PRESET_ID)


# =========================================================================== #
# 2. the committed file + its own golden record digest                          #
# =========================================================================== #
class TestReducedPresetSeal:
    def test_the_committed_file_is_strict_utf8_without_a_bom(self):
        raw = REDUCED_DEEP_DECIDE_PRESET_FILE.read_bytes()
        assert not raw.startswith(codecs.BOM_UTF8)
        payload = json.loads(raw.decode("utf-8"))
        assert payload["schema_version"] == "2"
        assert payload["preset_id"] == REDUCED_DEEP_DECIDE_PRESET_ID
        assert payload["version"] == REDUCED_DEEP_DECIDE_PRESET_VERSION
        assert REDUCED_DEEP_DECIDE_PRESET_FILE.parent == PHASE10_PRESETS_V2_DIR

    def test_the_preset_id_differs_from_the_sealed_ten_node_one(self):
        assert REDUCED_DEEP_DECIDE_PRESET_ID != DEEP_DECIDE_PRESET_ID
        assert DEEP_DECIDE_PRESET_IDS == (
            DEEP_DECIDE_PRESET_ID, REDUCED_DEEP_DECIDE_PRESET_ID)

    def test_the_loader_holds_both_deep_presets_side_by_side(self):
        registry = load_phase10_preset_registry(PRODUCTION_PRESETS_DIR)
        assert registry.sealed is True
        ids = {e.preset_id for e in registry.manifest()}
        assert {DEEP_DECIDE_PRESET_ID, REDUCED_DEEP_DECIDE_PRESET_ID} <= ids
        record = registry.get(REDUCED_DEEP_DECIDE_PRESET_ID)
        assert isinstance(record, PlanPresetRecordV2)
        assert record.schema_version == "2"

    def test_the_reduced_record_digest_is_golden_frozen(self):
        assert _reduced_record().semantic_digest() == REDUCED_RECORD_DIGEST, (
            "the reduced deep-decide graph moved; re-review the preset before "
            "re-freezing this golden")

    def test_the_golden_is_load_bearing(self):
        """Positive control: the pin really tracks the graph (a one-field edit
        moves it), so a green digest test is evidence, not decoration."""
        mutated = _reduced_record().model_copy(update={"description": "mutated"})
        assert mutated.semantic_digest() != REDUCED_RECORD_DIGEST

    def test_the_sealed_ten_node_record_digest_did_not_move(self):
        """Zero upstream golden movement: a second committed v2 file in the same
        directory must not disturb the record beside it."""
        registry = load_phase10_preset_registry(PRODUCTION_PRESETS_DIR)
        assert registry.get(DEEP_DECIDE_PRESET_ID).semantic_digest() == (
            DEEP_DECIDE_RECORD_DIGEST)

    def test_the_phase7_loader_over_the_same_root_is_still_untouched(self):
        phase7 = load_preset_registry(PRODUCTION_PRESETS_DIR)
        assert {e.preset_id for e in phase7.manifest()} == {PHASE7_BASELINE}
        assert phase7.get(PHASE7_BASELINE).semantic_digest() == (
            PHASE7_BASELINE_RECORD_DIGEST)

    def test_a_v2_reduced_record_is_refused_by_the_phase7_record_model(self):
        raw = REDUCED_DEEP_DECIDE_PRESET_FILE.read_text(encoding="utf-8")
        with pytest.raises(ValidationError, match="schema_version"):
            PlanPresetRecord.model_validate_json(raw)

    def test_loading_twice_is_byte_deterministic(self):
        a = load_phase10_preset_registry(PRODUCTION_PRESETS_DIR)
        b = load_phase10_preset_registry(PRODUCTION_PRESETS_DIR)
        assert a.registry_digest == b.registry_digest
        assert a.get(REDUCED_DEEP_DECIDE_PRESET_ID) == b.get(
            REDUCED_DEEP_DECIDE_PRESET_ID)

    def test_the_committed_reduced_record_is_an_admissible_base_member(self):
        committed = load_phase10_preset_registry(PRODUCTION_PRESETS_DIR)
        base = PlanPresetRegistry()
        base.register(committed.get(PHASE7_BASELINE))
        base.register(committed.get(REDUCED_DEEP_DECIDE_PRESET_ID))
        base.seal()
        extended = build_phase10_preset_registry(base, ())
        assert {e.preset_id for e in extended.manifest()} == {
            PHASE7_BASELINE, REDUCED_DEEP_DECIDE_PRESET_ID}


# =========================================================================== #
# 3. the eight-node graph: exactly the ten minus two                            #
# =========================================================================== #
class TestReducedGraph:
    @pytest.fixture(scope="class")
    def record(self):
        return _reduced_record()

    @pytest.fixture(scope="class")
    def full(self):
        return load_phase10_preset_registry(PRODUCTION_PRESETS_DIR).get(
            DEEP_DECIDE_PRESET_ID)

    def test_the_worker_set_is_the_ten_minus_the_two_evidence_readers(
            self, record):
        assert sorted(n.worker_id for n in record.nodes) == sorted(
            REDUCED_DEEP_DECIDE_WORKER_IDS)
        assert len(REDUCED_DEEP_DECIDE_WORKER_IDS) == 8
        assert set(DEEP_DECIDE_WORKER_IDS) - set(REDUCED_DEEP_DECIDE_WORKER_IDS) == (
            set(REDUCED_EVIDENCE_DROPPED_WORKER_IDS))

    def test_the_dropped_workers_are_wired_into_no_node(self, record):
        """Structural, not textual: no node RUNS them and no dependency names
        their node ids (the description may — and must — still NAME them as
        absent; that is the honesty half, asserted below)."""
        assert not ({n.worker_id for n in record.nodes}
                    & set(REDUCED_EVIDENCE_DROPPED_WORKER_IDS))
        for node in record.nodes:
            for dep in node.dependencies:
                assert dep.upstream_node_id not in {"technical", "news"}, node.id

    def test_no_dependency_points_at_a_removed_node(self, record, full):
        """The two pruned edges were the ONLY references, and both were
        ``degrade`` — pruning them changes no other node's semantics."""
        node_ids = {n.id for n in record.nodes}
        for node in record.nodes:
            for dep in node.dependencies:
                assert dep.upstream_node_id in node_ids, (node.id, dep)
        full_by_id = {n.id: n for n in full.nodes}
        removed = {n.id for n in full.nodes} - node_ids
        for node in full.nodes:
            for dep in node.dependencies:
                if dep.upstream_node_id in removed:
                    assert dep.policy.value == "degrade", (node.id, dep)
        assert removed == {"technical", "news"}
        assert {full_by_id[nid].worker_id for nid in removed} == set(
            REDUCED_EVIDENCE_DROPPED_WORKER_IDS)

    def test_every_surviving_node_is_byte_identical_to_the_full_preset_except_the_two_debate_seats(
            self, record, full):
        """Structural sameness: only ``bull-r1`` / ``bear-r1`` differ, and only
        by having lost the two degrade dependencies."""
        full_by_id = {n.id: n for n in full.nodes}
        differing = {n.id for n in record.nodes if n != full_by_id[n.id]}
        assert differing == {"bull-r1", "bear-r1"}
        for nid in sorted(differing):
            before = {(d.upstream_node_id, d.inject_as)
                      for d in full_by_id[nid].dependencies}
            after = {(d.upstream_node_id, d.inject_as)
                     for d in next(n for n in record.nodes if n.id == nid).dependencies}
            assert before - after == {("technical", "technical"),
                                      ("news", "news_digest")}
            assert after - before == set()

    def test_the_decision_spine_and_debate_are_untouched(self, record, full):
        assert record.sink_node_ids == full.sink_node_ids == ("trader",)
        assert record.debates == full.debates
        assert record.reducers == full.reducers
        assert record.max_concurrency == full.max_concurrency

    def test_the_llm_budget_equals_the_analyzers_exact_requirement(
            self, record, env):
        """The same equality discipline the sealed preset holds: an over-request
        would pass the analyzer while reserving budget nobody can spend."""
        workers = {w.id: w for w in env["snapshot"].workers}
        seats = sum(1 for n in record.nodes if n.debate_id is not None)
        non_debate_llm = sum(
            1 for n in record.nodes
            if n.debate_id is None
            and workers[n.worker_id].execution.kind is ExecutionKind.LLM)
        assert (seats, non_debate_llm) == (2, 4)
        assert record.budget_request_llm_invocations == seats + non_debate_llm == 6

    def test_the_reduced_preset_carries_no_code_anywhere(self, record):
        for node in record.nodes:
            assert node.params == {}, node.id
        blob = canonical_json(record)
        assert re.search(r'"\d{6}"', blob) is None, blob

    def test_the_description_names_exactly_what_is_missing(self, record):
        for token in ("减证据", "auxiliary", *REDUCED_EVIDENCE_DROPPED_WORKER_IDS):
            assert token in record.description, token


# =========================================================================== #
# 4. materialization — the badge is mandatory and honest in both directions     #
# =========================================================================== #
class TestReducedMaterialization:
    def test_the_reduced_draft_is_a_preset_sourced_main_draft(
            self, materialized, env):
        draft = materialized.draft
        assert draft.source is PlanSource.PRESET
        assert draft.phase == "main"
        assert len(draft.nodes) == 8
        assert draft.context_snapshot_ref == env["ctx_ref"]

    def test_the_reduced_badges_are_always_present(self, materialized):
        assert REDUCED_EVIDENCE_BADGE in materialized.badges
        assert REDUCED_EVIDENCE_MISSING_BADGE in materialized.badges
        assert SUBJECT_RUN_SCOPED_BADGE in materialized.badges

    def test_the_missing_badge_names_both_absent_evidence_workers(self):
        for worker_id in REDUCED_EVIDENCE_DROPPED_WORKER_IDS:
            assert worker_id in REDUCED_EVIDENCE_MISSING_BADGE
        assert REDUCED_EVIDENCE_BADGES == (
            REDUCED_EVIDENCE_BADGE, REDUCED_EVIDENCE_MISSING_BADGE)

    def test_the_full_preset_never_carries_a_reduced_badge(self, full_materialized):
        """The other half of the honesty rule: a full-evidence run must not be
        readable as reduced either."""
        assert not set(REDUCED_EVIDENCE_BADGES) & set(full_materialized.badges)
        assert len(full_materialized.draft.nodes) == 10

    def test_the_badge_helper_is_preset_scoped(self):
        assert reduced_evidence_badges(REDUCED_DEEP_DECIDE_PRESET_ID) == (
            REDUCED_EVIDENCE_BADGES)
        assert reduced_evidence_badges(DEEP_DECIDE_PRESET_ID) == ()

    def test_the_note_states_plainly_what_the_debate_never_saw(self):
        assert "技术" in REDUCED_EVIDENCE_NOTE and "新闻" in REDUCED_EVIDENCE_NOTE
        banner = render_reduced_evidence_banner(REDUCED_DEEP_DECIDE_PRESET_ID)
        assert REDUCED_EVIDENCE_BADGE in banner
        assert "pv.technical" in banner and "text.news" in banner
        assert render_reduced_evidence_banner(DEEP_DECIDE_PRESET_ID) == ""

    def test_one_record_digest_still_serves_every_stock(self, env):
        a = _materialize(env, subject=_subject_ref("600519"))
        b = _materialize(env, subject=_subject_ref("000001"))
        assert canonical_json(a.draft.executable_projection()) == canonical_json(
            b.draft.executable_projection())
        assert a.subject_ref != b.subject_ref

    def test_an_unknown_preset_id_is_refused_typed(self, env):
        """The closed vocabulary: an arbitrary registry entry can never be
        materialized through this door (and the refusal is a DeepDecideError, so
        every existing caller's `except DeepDecideError` arm still catches it)."""
        with pytest.raises(PresetSelectionRefused, match="deep-decide"):
            _materialize(env, preset_id=PHASE7_BASELINE)
        assert issubclass(PresetSelectionRefused, DeepDecideError)
        assert issubclass(EvidenceScopeMismatch, DeepDecideError)

    def test_a_reduced_record_that_smuggled_the_evidence_workers_back_refuses(
            self, env):
        """The badge would become a LIE, so the materializer refuses instead:
        a record registered under the reduced id must not hold the two workers
        the badge says the debate never saw."""
        committed = load_phase10_preset_registry(PRODUCTION_PRESETS_DIR)
        full = committed.get(DEEP_DECIDE_PRESET_ID)
        impostor = full.model_copy(
            update={"preset_id": REDUCED_DEEP_DECIDE_PRESET_ID})
        registry = PlanPresetRegistry()
        registry.register(impostor)
        registry.seal()
        with pytest.raises(EvidenceScopeMismatch, match="reduced"):
            _materialize(env, registry_override=registry)

    def test_a_full_record_that_quietly_lost_the_evidence_workers_refuses(
            self, env):
        """The symmetric guard: an un-badged deep verdict must never come out of
        a graph that silently dropped the evidence readers."""
        committed = load_phase10_preset_registry(PRODUCTION_PRESETS_DIR)
        reduced = committed.get(REDUCED_DEEP_DECIDE_PRESET_ID)
        impostor = reduced.model_copy(update={"preset_id": DEEP_DECIDE_PRESET_ID})
        registry = PlanPresetRegistry()
        registry.register(impostor)
        registry.seal()
        with pytest.raises(EvidenceScopeMismatch, match="reduced"):
            _materialize(env, preset_id=DEEP_DECIDE_PRESET_ID,
                         registry_override=registry)

    def test_a_registry_without_the_reduced_preset_refuses_typed(self, env):
        empty = PlanPresetRegistry()
        empty.seal()
        with pytest.raises(PlanPresetError, match=REDUCED_DEEP_DECIDE_PRESET_ID):
            _materialize(env, registry_override=empty)


# =========================================================================== #
# 5. the selection seam — explicit opt-in, default unchanged                    #
# =========================================================================== #
class TestPresetSelectionSeam:
    def test_an_unset_env_selects_the_full_ten_node_preset(self):
        assert resolve_deep_preset_id({}) == DEEP_DECIDE_PRESET_ID

    def test_a_blank_env_selects_the_full_ten_node_preset(self):
        assert resolve_deep_preset_id({DEEP_PRESET_ENV_VAR: "  "}) == (
            DEEP_DECIDE_PRESET_ID)

    def test_reduced_is_the_explicit_opt_in(self):
        assert resolve_deep_preset_id({DEEP_PRESET_ENV_VAR: "reduced"}) == (
            REDUCED_DEEP_DECIDE_PRESET_ID)
        assert resolve_deep_preset_id({DEEP_PRESET_ENV_VAR: " REDUCED "}) == (
            REDUCED_DEEP_DECIDE_PRESET_ID)

    def test_full_is_spellable_explicitly(self):
        assert resolve_deep_preset_id({DEEP_PRESET_ENV_VAR: "full"}) == (
            DEEP_DECIDE_PRESET_ID)

    def test_an_unrecognized_value_refuses_loudly_rather_than_defaulting(self):
        """A typo must never silently pick a lane: the deep lane dies loudly and
        the fast path is untouched (the server catches and logs)."""
        with pytest.raises(PresetSelectionRefused, match="reduced"):
            resolve_deep_preset_id({DEEP_PRESET_ENV_VAR: "redcued"})

    def test_the_env_var_name_is_the_documented_one(self):
        assert DEEP_PRESET_ENV_VAR == "GUANLAN_SEATS_DEEP_PRESET"

    def test_the_process_env_is_the_default_source(self, monkeypatch):
        monkeypatch.setenv(DEEP_PRESET_ENV_VAR, "reduced")
        assert resolve_deep_preset_id() == REDUCED_DEEP_DECIDE_PRESET_ID
        monkeypatch.delenv(DEEP_PRESET_ENV_VAR)
        assert resolve_deep_preset_id() == DEEP_DECIDE_PRESET_ID

    def test_build_production_bindings_resolves_before_anything_else(
            self, monkeypatch):
        """The wiring proof that needs no durable stores: a bad env value refuses
        with the SELECTION error, not the store-binding one — so the selection is
        really on the production path."""
        from guanlan_v2.orchestration.pipeline import live_decide as ld

        monkeypatch.setenv(DEEP_PRESET_ENV_VAR, "nonsense")
        with pytest.raises(PresetSelectionRefused):
            ld.build_production_bindings()


# =========================================================================== #
# 6. zero upstream golden movement — the P10 catalog never names this preset    #
# =========================================================================== #
class TestNoUpstreamMovement:
    def test_the_phase10_catalog_reference_names_only_the_sealed_ten_node_preset(
            self):
        """The screening-preset precedent: ``chain.py`` binds exactly ONE preset
        reference material. A reduced preset that entered the catalog would move
        the sealed Phase-10 catalog digest — that belongs to the re-freeze phase,
        not to route A."""
        from guanlan_v2.orchestration.pipeline import chain as p10_chain

        doc = p10_chain._deep_decide_preset_ref_doc()
        assert doc["preset_id"] == DEEP_DECIDE_PRESET_ID
        assert doc["record_digest"] == DEEP_DECIDE_RECORD_DIGEST
        source = Path(p10_chain.__file__).read_text(encoding="utf-8")
        assert REDUCED_DEEP_DECIDE_PRESET_ID not in source
        assert "REDUCED_DEEP_DECIDE" not in source

    def test_the_phase10_catalog_digest_is_unmoved(self, env):
        """The sealed Phase-9/10 catalog digest the whole chain pins."""
        assert env["snapshot"].catalog_digest == chain.phase9_catalog_snapshot(
        ).catalog_digest
