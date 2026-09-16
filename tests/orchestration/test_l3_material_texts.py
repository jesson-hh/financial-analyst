# -*- coding: utf-8 -*-
"""L3 Task 4 — the sealed material texts, aligned with post-ruling reality.

Material bytes were forbidden to move for ten phases because they move sealed
digests. Phases 10/11 could therefore only record the drift in CODE comments
while the MATERIAL kept stating the invariant the controller had already
overturned. This module pins the corrected texts, and pins that the
drift-recording comments are GONE — because a tree that states an invariant
twice, once wrong, is exactly the state this task exists to end.

**What each pin owns** (plan
``docs/superpowers/plans/2026-07-31-orchestration-L3-grants-refreeze.md`` Task 4
plus the controller's Task-3-round correction clause, ledger §11.1):

* the two rowless-reader handler materials (experience + memory) must STATE the
  ruled ``rows == 0`` behaviour — rowless-and-never-granted → honest EMPTY;
  rowless-but-granted / ``rows > 1`` / activation drift → LOUD;
* the Lane-0 regime prompt must require SORTED drivers (the 2026-07-31 live
  run's single real defect was ``drivers must be sorted``) and must state the
  evidence-anchor requirement CONDITIONALLY, because the contract licenses an
  empty citation list exactly when the runtime measured nothing citable;
* the deep preset description must describe what ``pv.microstructure``
  actually does in THIS preset (its typed report blocks ``dec.research_mgr``);
* ``text.news``'s SKILL must not promise ``ww_f10`` — D-1 removed the only
  capability that supplied it, and a promise without supply is the same paper
  fiction as a grant without a row;
* two code comments that assert things the tree no longer does.

Every pin is paired with a MEASUREMENT wherever a measurement exists: a text
assertion nobody can falsify is prose, not a pin. Model compliance with the
regime prompt is NOT claimed here — it is verified against a real model in
Task 7.
"""
from __future__ import annotations

import inspect
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from guanlan_v2.orchestration import bootstrap as B
from guanlan_v2.orchestration import lane_catalog as LC
from guanlan_v2.orchestration.capability_manifest import (
    MATERIAL_PATH as CAP_MANIFEST_PATH,
    lint_skill_supply,
    parse_capability_manifest,
)
from guanlan_v2.orchestration.catalog_runtime import build_text_material
from guanlan_v2.orchestration.data.catalog import phase3_data_surface
from guanlan_v2.orchestration.market import factors as F
from guanlan_v2.orchestration.memory import catalog as MC
from guanlan_v2.orchestration.pipeline import screening as SC

_REPO = Path(__file__).resolve().parents[2]
_MATERIALS = _REPO / "config" / "orchestration" / "materials"
_LANE0 = _MATERIALS / "lane0"
_PRESETS = _REPO / "config" / "orchestration" / "presets" / "v2"
_SKILL_TREE = _REPO / "guanlan_v2" / "orchestration" / "skills"
_SKILL_MIRROR = _MATERIALS / "skills"


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


# =========================================================================== #
# 1 — the experience analyzer handler material (bootstrap.py's sealed text)    #
# =========================================================================== #
class TestTheExperienceAnalyzerMaterialStatesTheRuledBehaviour:
    """``lane0.experience.analyzer`` — the material behind
    :class:`~guanlan_v2.orchestration.bootstrap.ExperienceBridgeSupportAnalyzer`.
    """

    @pytest.fixture(scope="class")
    def material(self) -> str:
        return _text(_LANE0 / "experience_support_analyzer.md")

    def test_the_pinned_file_is_the_one_the_catalog_actually_seals(self, material):
        """Not a stray doc: the Lane-0 catalog's analyzer ref must be the digest
        of THESE bytes, so a text pin here is a pin on the sealed material."""
        catalog = B.load_lane0_catalog()   # raises if any material drifted
        ref, _ = build_text_material(
            id="lane0.experience.analyzer", version="1", kind="handler",
            raw=(_LANE0 / "experience_support_analyzer.md").read_bytes())
        assert catalog.refs["lane0.experience.analyzer"] == ref
        # the coupling this task had to honour: the sealed descriptor material
        # EMBEDS this digest and ``assemble_lane0_catalog`` refuses any drift,
        # so moving the analyzer text without regenerating the descriptor is a
        # loud CatalogError, never a silent divergence.
        assert catalog.descriptor.support_analyzer_ref == ref

    def test_it_states_the_ungranted_rowless_reader_gets_an_honest_empty(
            self, material):
        low = " ".join(material.lower().split())
        assert "zero-contribution" in low, material
        assert "read category" in low and "never granted" in low, material
        assert "min=0" in low and "max=0" in low, material

    def test_it_states_the_granted_rowless_reader_stays_loud(self, material):
        low = " ".join(material.lower().split())
        assert "catalogerror" in low, material
        assert "lost its row" in low, material
        assert "silently" in low, material

    def test_the_pre_c3_invariant_sentence_is_gone(self, material):
        """The overturned sentence, verbatim. Its return is a regression."""
        assert "a worker without a reviewed row fails analysis" not in material

    def test_the_material_agrees_with_the_analyzer_the_runtime_binds(self):
        """The measurement half: the ruled discrimination is real code, so the
        material is describing behaviour rather than aspiration."""
        src = inspect.getsource(B.ExperienceBridgeSupportAnalyzer.analyze)
        assert "min_finalized_tool_calls_on_success=0" in src
        assert "no reviewed experience prefetch row for worker" in src
        assert "duplicate experience prefetch rows for worker" in src


# =========================================================================== #
# 2 — the memory analyzer handler material (``_ANALYZER_BYTES``)               #
# =========================================================================== #
class TestTheMemoryAnalyzerMaterialStatesTheRuledBehaviour:

    @pytest.fixture(scope="class")
    def material(self) -> str:
        return MC._ANALYZER_BYTES.decode("utf-8")

    def test_the_pinned_bytes_are_the_ones_the_surface_actually_seals(self, material):
        surface = MC._Phase3MemorySurface()
        ref, _ = build_text_material(
            id="bridge.memory_runtime.analyzer", version="1", kind="handler",
            raw=MC._ANALYZER_BYTES)
        assert surface.analyzer_ref == ref

    def test_it_states_rows_zero_is_an_honest_zero_contribution(self, material):
        # whitespace-normalized: the material is hard-wrapped, so a clause may
        # straddle a line break and a raw substring test would be a false pin.
        low = " ".join(material.lower().split())
        assert "rows == 0" in low, material
        assert "never a reviewed" in low, material
        assert "zero-bounds" in low or "zero bounds" in low, material

    def test_it_states_rows_gt_one_and_activation_drift_stay_loud(self, material):
        low = " ".join(material.lower().split())
        assert "rows > 1" in low, material
        assert "catalogerror" in low, material
        assert "activation-drift" in low or "activation drift" in low, material

    def test_the_pre_c3_invariant_sentence_is_gone(self, material):
        assert "every activated reader needs exactly one" not in material

    def test_the_material_agrees_with_the_analyzer_the_runtime_binds(self):
        src = inspect.getsource(MC.MemoryBridgeSupportAnalyzer.analyze)
        assert "activation drift" in src
        assert "requires exactly one closed query" in src
        assert "min_finalized_tool_calls_on_success=0" in src

    def test_the_bytes_stay_ascii(self):
        """``_ANALYZER_BYTES`` is a bytes literal in a source file; a smart quote
        or an em dash smuggled in by an editor would be a silent encoding
        hazard, not a text improvement."""
        MC._ANALYZER_BYTES.decode("ascii")


# =========================================================================== #
# 2b — the experience PROVIDER handler material (the third drift note)         #
# =========================================================================== #
class TestTheExperienceProviderMaterialStatesTheRuledBehaviour:
    """``lane0.experience.provider`` — the third byte-frozen material whose text
    still described only the GRANTED path. Its drift note claimed the bytes
    "stay BYTE-FROZEN"; this window is the only one in which that could stop
    being true, so it stops being true here."""

    @pytest.fixture(scope="class")
    def material(self) -> str:
        return _text(_LANE0 / "experience_provider.md")

    def test_the_pinned_file_is_the_one_the_catalog_actually_seals(self):
        catalog = B.load_lane0_catalog()
        ref, _ = build_text_material(
            id="lane0.experience.provider", version="1", kind="handler",
            raw=(_LANE0 / "experience_provider.md").read_bytes())
        assert catalog.refs["lane0.experience.provider"] == ref
        # the same one-mirror coupling the analyzer material has
        assert catalog.descriptor.provider_handler_ref == ref

    def test_the_one_successful_call_sentence_is_now_scoped_to_the_granted_path(
            self, material):
        low = " ".join(material.lower().split())
        assert "still finalizes one successful call" in low, material
        idx = low.index("still finalizes one successful call")
        assert "granted" in low[max(0, idx - 260):idx], (
            "the sentence is still stated unconditionally; under C3 a "
            "non-granted worker finalizes ZERO calls")

    def test_it_states_the_non_granted_arm(self, material):
        low = " ".join(material.lower().split())
        assert "non-granted discrimination" in low, material
        assert "max_capability_invocations=0" in low, material
        assert "does not invoke" in low, material
        assert "not even the empty sentinel" in low, material

    def test_the_material_agrees_with_the_provider_the_runtime_binds(self):
        doc = B.Lane0ExperienceBridgeProvider.__doc__ or ""
        assert "Non-granted discrimination" in doc
        assert "zero invocations" in doc


# =========================================================================== #
# 3 — the P10-era drift-recording comments are GONE                            #
# =========================================================================== #
class TestTheDriftRecordingCommentsAreGone:
    """They existed only because the bytes could not move. The bytes moved, so
    keeping them would state the invariant twice — once wrong."""

    def test_the_experience_analyzer_docstring_no_longer_records_drift(self):
        doc = B.ExperienceBridgeSupportAnalyzer.__doc__ or ""
        assert "Material-vs-code drift note" not in doc
        assert "BYTE-FROZEN" not in doc
        assert "pre-Task-11 invariant" not in doc

    def test_the_memory_analyzer_docstring_no_longer_records_drift(self):
        doc = MC.MemoryBridgeSupportAnalyzer.__doc__ or ""
        assert "Material-vs-code drift note" not in doc
        assert "BYTE-FROZEN" not in doc
        assert "pre-Task-11 invariant" not in doc

    def test_the_experience_provider_docstring_no_longer_records_drift(self):
        doc = B.Lane0ExperienceBridgeProvider.__doc__ or ""
        assert "Material-vs-code drift note" not in doc
        assert "BYTE-FROZEN" not in doc
        assert "pre-ruling invariant" not in doc

    def test_no_drift_note_pattern_survives_in_either_module(self):
        """The pin must match its own name. An earlier version grepped ONE
        phrasing (``pre-Task-11 invariant``) and therefore walked straight past
        the third drift note, which said ``pre-ruling invariant`` — the pin
        looked comprehensive and was not. Both phrasings, plus the note's own
        heading and its BYTE-FROZEN conclusion, are refused in BOTH modules."""
        needles = ("Material-vs-code drift note",
                   "still states the pre-Task-11 invariant",
                   "still states the pre-ruling invariant",
                   "stay BYTE-FROZEN")
        offenders = [
            (mod.__name__, needle)
            for mod in (B, MC)
            for needle in needles
            if needle in inspect.getsource(mod)
        ]
        assert offenders == [], offenders


# =========================================================================== #
# 4 — the Lane-0 regime prompt: sorted drivers + a CONDITIONAL anchor           #
# =========================================================================== #
class TestTheRegimePromptRequiresSortedDriversAndAConditionalAnchor:

    @pytest.fixture(scope="class")
    def prompt(self) -> str:
        return _text(_LANE0 / "regime_prompt.md")

    def test_the_pinned_file_is_the_one_the_catalog_actually_seals(self):
        catalog = B.load_lane0_catalog()
        ref, _ = build_text_material(
            id="lane0.regime.prompt", version="1", kind="prompt",
            raw=(_LANE0 / "regime_prompt.md").read_bytes())
        assert any(entry.ref == ref for entry in catalog.snapshot.content_manifest)

    def test_it_requires_sorted_duplicate_free_drivers(self, prompt):
        assert "`drivers`" in prompt, prompt
        window = prompt[prompt.index("`drivers`"):prompt.index("`drivers`") + 400]
        low = " ".join(window.lower().split())
        assert "sorted" in low, window
        assert "duplicate-free" in low or "duplicate free" in low, window

    def test_the_sorted_driver_rule_is_machine_enforced_not_advice(self):
        """The measurement half — the live 2026-07-31 defect, reproduced."""
        with pytest.raises(ValidationError, match="drivers must be sorted"):
            F.RegimeReport.build(**_regime_fields(drivers=("z driver", "a driver")))

    def test_the_anchor_requirement_is_stated_conditionally(self, prompt):
        assert "`evidence` needs at least one EvidenceAnchor (exact" not in prompt, (
            "the anchor rule is still stated unconditionally; the contract "
            "licenses an empty citation list when the runtime measured nothing "
            "citable, so an unconditional rule instructs a fabrication")
        # …and the replacement must actually carry BOTH arms. An earlier version
        # of this pin only looked for the word "when" somewhere nearby, which
        # ordinary English prose satisfies by accident — it could not tell a
        # conditional rule from a paragraph that happens to contain "when".
        low = " ".join(prompt.lower().split())
        assert "the anchor rule is conditional" in low, prompt
        assert low.count("when the ") >= 2, (
            "a conditional rule needs BOTH arms stated, not just the one that "
            "used to be unconditional")
        assert "cite nothing" in low, (
            "the second arm must say what to do when nothing is citable")

    def test_it_states_who_authors_the_empty_evidence_reason(self, prompt):
        low = prompt.lower()
        assert "unknown_reason" in low, prompt
        assert "runtime" in low, prompt

    def test_the_empty_citation_licence_is_real_and_runtime_owned(self):
        """The measurement half of the conditional anchor: an all-unknown read
        carrying the RUNTIME's measured reason validates with zero anchors."""
        report = F.RegimeReport.build(**_regime_fields(
            factor_report_digest=F.NO_FACTOR_REPORT_DIGEST,
            trend=F.TrendState.UNKNOWN,
            risk_state=F.RiskState.UNKNOWN,
            heat_state=F.HeatState.UNKNOWN,
            trend_probabilities={
                F.TrendState.BULL: 0.1, F.TrendState.BEAR: 0.1,
                F.TrendState.RANGE: 0.1, F.TrendState.UNKNOWN: 0.7},
            risk_probabilities={
                F.RiskState.RISK_ON: 0.1, F.RiskState.RISK_OFF: 0.1,
                F.RiskState.NEUTRAL: 0.1, F.RiskState.UNKNOWN: 0.7},
            heat_probabilities={
                F.HeatState.NORMAL: 0.2, F.HeatState.OVERHEAT: 0.1,
                F.HeatState.UNKNOWN: 0.7},
            confidence=F.Confidence.LOW,
            evidence=(), evidence_factor_ids=(),
            unknown_reason=F.NO_FACTOR_REPORT_REASON))
        assert report.evidence == ()
        assert report.unknown_reason == F.NO_FACTOR_REPORT_REASON
        assert "unknown_reason" not in B.LANE0_RUNTIME_OWNED_FIELDS
        # …and it is nonetheless the runtime's to write: the normalizer
        # overwrites whatever the model put there.
        src = inspect.getsource(B.normalize_lane0_llm_output)
        assert 'raw["unknown_reason"] = empty_evidence_reason' in src


# =========================================================================== #
# 5 — the deep preset description vs what pv.microstructure actually does      #
# =========================================================================== #
class TestThePresetDescriptionMatchesWhatTheWorkerDoes:

    @pytest.fixture(scope="class")
    def preset(self) -> dict:
        return json.loads(_text(_PRESETS / "luozi_deep_decide_v1.json"))

    def test_the_preset_routes_microstructure_to_research_manager(self, preset):
        """Measure the reviewed direct-input wiring against the real catalog."""
        workers = {w.id: w for w in LC.phase8_catalog_snapshot().workers}
        scheduled = {n["worker_id"] for n in preset["nodes"]}
        produced = {
            o.schema_ref.name
            for o in workers["pv.microstructure"].outputs}
        assert produced == {"MicrostructureReport"}
        consumers_in_preset = {
            wid for wid in scheduled
            for i in workers[wid].inputs
            if i.schema_ref.name == "MicrostructureReport"}
        assert consumers_in_preset == {"dec.research_mgr"}
        # The catalog also holds the generic quality-gate consumer; this preset
        # intentionally injects the report directly into its research manager.
        consumers_in_catalog = {
            w.id for w in workers.values()
            for i in w.inputs if i.schema_ref.name == "MicrostructureReport"}
        assert consumers_in_catalog == {"dec.research_mgr", "x.quality_gate"}
        assert "x.quality_gate" not in scheduled

        research_mgr = next(n for n in preset["nodes"]
                            if n["worker_id"] == "dec.research_mgr")
        micro_edges = [d for d in research_mgr.get("dependencies", [])
                       if d.get("inject_as") == "microstructure"]
        assert micro_edges == [{
            "upstream_node_id": "microstructure",
            "artifact_slot": "slot.microstructure",
            "inject_as": "microstructure",
            "policy": "block",
        }]

    def test_the_description_no_longer_lists_it_among_what_feeds_the_debate(
            self, preset):
        assert (
            "pv.price_action + pv.technical + pv.microstructure + text.news "
            "evidence and text.sentiment feed") not in preset["description"]

    def test_the_description_states_what_it_actually_does(self, preset):
        desc = preset["description"]
        assert "pv.microstructure" in desc
        assert "blocking typed inputs to dec.research_mgr" in desc
        assert "No evidence seat is auxiliary decoration" in desc

    def test_the_reduced_preset_carries_the_same_correction(self):
        """The reduced preset repeated the overstatement verbatim. Truing one
        and leaving the other would leave the same false sentence shipping under
        a different preset id."""
        reduced = json.loads(_text(_PRESETS / "luozi_deep_decide_reduced_v1.json"))
        desc = reduced["description"]
        assert "remaining price-action, microstructure, sentiment" in desc
        assert "blocking typed inputs to dec.research_mgr" in desc
        assert "No retained evidence seat is auxiliary decoration" in desc

    def test_the_reduced_preset_routes_it_to_research_manager_too(self):
        workers = {w.id: w for w in LC.phase8_catalog_snapshot().workers}
        reduced = json.loads(_text(_PRESETS / "luozi_deep_decide_reduced_v1.json"))
        scheduled = {n["worker_id"] for n in reduced["nodes"]}
        assert "pv.microstructure" in scheduled
        assert {wid for wid in scheduled
                for i in workers[wid].inputs
                if i.schema_ref.name == "MicrostructureReport"} == {"dec.research_mgr"}
        research_mgr = next(n for n in reduced["nodes"]
                            if n["worker_id"] == "dec.research_mgr")
        assert any(
            d == {
                "upstream_node_id": "microstructure",
                "artifact_slot": "slot.microstructure",
                "inject_as": "microstructure",
                "policy": "block",
            }
            for d in research_mgr.get("dependencies", [])
        )


# =========================================================================== #
# 6 — text.news promises only what its allowlist supplies                      #
# =========================================================================== #
class TestTheNewsSkillPromisesOnlyWhatItsAllowlistSupplies:

    def test_the_ww_f10_promise_is_gone_from_the_data_source_priority(self):
        skill = _text(_SKILL_TREE / "text.news" / "SKILL.md")
        assert "ww_f10" not in skill, (
            "D-1 removed cap.data.fundamentals from text.news, so the only "
            "capability that supplied ww_f10 is gone; the promise must go too")

    def test_the_committed_mirror_stays_byte_identical(self):
        tree = (_SKILL_TREE / "text.news" / "SKILL.md").read_bytes()
        assert (_SKILL_MIRROR / "text.news.md").read_bytes() == tree

    def test_the_real_lint_is_clean_against_the_real_allowlist(self):
        """The measurement: real manifest material, real Phase-8 allowlist."""
        manifest = parse_capability_manifest(CAP_MANIFEST_PATH.read_bytes())
        worker = next(w for w in LC.phase8_catalog_snapshot().workers
                      if w.id == "text.news")
        assert [c.id for c in worker.capability_allowlist] == ["cap.data.news"]
        issues = lint_skill_supply(
            manifest=manifest,
            skill_text=_text(_SKILL_TREE / "text.news" / "SKILL.md"),
            capability_allowlist=worker.capability_allowlist)
        assert issues == (), issues

    def test_the_remaining_promises_are_all_supplied(self):
        skill = _text(_SKILL_TREE / "text.news" / "SKILL.md")
        for tool in ("ww_news_live", "ww_newsradar", "ww_live_text"):
            assert tool in skill, tool


# =========================================================================== #
# 7 — the screening seam comment is true again                                 #
# =========================================================================== #
class TestTheScreeningSeamCommentIsTrue:

    def test_the_vacuity_claim_is_gone(self):
        src = inspect.getsource(SC)
        assert "zero screening workers carry data prefetch rows" not in src, (
            "text.news is a screening-lane worker AND now carries a prefetch "
            "row, so the seam is no longer behaviourally vacuous")

    def test_the_measured_truth_the_comment_must_now_state(self):
        preset = json.loads(
            _text(_PRESETS / "screening_lane_v1.json"))
        scheduled = {n["worker_id"] for n in preset["nodes"]}
        row_workers = {
            op.worker_id
            for op in phase3_data_surface().prefetch_binding.operations}
        assert scheduled & row_workers == {"pv.price_action", "text.news"}

    def test_the_comment_names_the_worker_that_makes_it_non_vacuous(self):
        """Scoped to the ``subject_params`` field comment itself — the seam the
        stale claim was attached to — so a mention elsewhere in the module
        cannot satisfy this pin."""
        src = inspect.getsource(SC.MaterializedScreeningLane)
        assert "subject_params" in src
        comment = "\n".join(
            line for line in src.splitlines() if line.strip().startswith("#:"))
        assert "text.news" in comment, comment
        assert "pv.price_action" in comment, comment


# =========================================================================== #
# 8 — the Phase-8 call-site comment describes the RULED mechanism               #
# =========================================================================== #
class TestThePhase8CallSiteCommentDescribesTheRuledMechanism:

    def test_the_threaded_row_story_is_gone(self):
        src = inspect.getsource(LC)
        assert "carried every other row forward" not in src, (
            "nothing is threaded between the two evaluation points; the sealed "
            "binding is the single source both read (ruling D-1(0))")

    def test_the_call_site_states_the_single_source_mechanism(self):
        src = inspect.getsource(LC.build_phase8_catalog_snapshot)
        assert "verify_cumulative_prefetch_coverage" in src
        low = src.lower()
        assert "single source" in low or "same sealed binding" in low, src[-1600:]


# --------------------------------------------------------------------------- #
# a local RegimeReport fixture (the contract's own shape, no test double)      #
# --------------------------------------------------------------------------- #
def _regime_fields(**over) -> dict:
    base = dict(
        as_of=datetime(2026, 7, 31, 7, 0, tzinfo=timezone.utc),
        factor_report_digest="a" * 64,
        trend=F.TrendState.BULL,
        risk_state=F.RiskState.RISK_ON,
        heat_state=F.HeatState.NORMAL,
        trend_probabilities={
            F.TrendState.BULL: 0.6, F.TrendState.BEAR: 0.2,
            F.TrendState.RANGE: 0.1, F.TrendState.UNKNOWN: 0.1},
        risk_probabilities={
            F.RiskState.RISK_ON: 0.5, F.RiskState.RISK_OFF: 0.2,
            F.RiskState.NEUTRAL: 0.2, F.RiskState.UNKNOWN: 0.1},
        heat_probabilities={
            F.HeatState.NORMAL: 0.7, F.HeatState.OVERHEAT: 0.2,
            F.HeatState.UNKNOWN: 0.1},
        confidence=F.Confidence.MEDIUM,
        evidence=(F.EvidenceAnchor(
            factor_id="breadth.ad_ratio", value=0.7,
            reading="breadth broadening"),),
        conflicts=(),
        analog_case_ids=(),
        drivers=("breadth broadening",),
        evidence_factor_ids=("breadth.ad_ratio",),
        narrative="Breadth is broadening while heat stays contained.",
        unknown_reason=None,
    )
    base.update(over)
    return base
