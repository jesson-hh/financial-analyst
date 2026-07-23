# -*- coding: utf-8 -*-
"""Phase 8 · Task 10 — Batch 5 · Lane D 决策/风控 (4 new + 2 pilot reviewed updates).

The FINAL migration batch. Covers (per brief Step 1):
* the WorkerSpec matrix per roster row (lane/tier/model_tier/tool policy/allowlist/
  guardrails/read_categories/output/inputs/params/decision authority), field-for-field;
* the clause-(d) pilot updates: the ``dec.research_mgr`` / ``dec.pm`` WorkerSpecs differ
  from the frozen Phase-7 baseline in EXACTLY the reviewed fields;
* the 交付物③ byte-verbatim install: both pilot tree files equal the spec-extracted fenced
  blocks (+ trailing newline); the supersession-set extension in test_skilltree;
* skill-v1 grammar + mirror byte identity for all six; the sole-``reasoner_deep`` invariant
  across the WHOLE lane_catalog (converts Task 3's deferred assertion);
* ``dec.trader`` output-schema pin = Phase 6's ``PortfolioTargetProposal@1`` (no Phase-8
  redefinition); the asymmetric-ammo structural assertion (bear has announcement_risk, bull
  does not);
* the Lane D payload matrices (BullCase / BearCase / RiskDebateStance / RiskDebateParams);
* the legacy adapter regression rows (research_action MAPPED, rating UNMAPPABLE,
  position_action MAPPED, confidence, and the risk_score adapter-ABSENCE assertion);
* the canonical Lane D debate-topology fixture: the 13-node plan passes ``validate_plan_draft``
  + ``verify_debate_expansion`` (both debates) + ``analyze_debates``; the one-spec / 6-node
  risk debate is multi-instance / distinct-slot proven; and each reviewed mutation fails with
  its exact issue code (missing turn / round 3 / judge-as-seat / stance_role≠round_role /
  non-LLM seat / unauthorized sink / bad params).

Run from repo root: ``pytest tests/orchestration/test_lane_batch_decision.py -v``
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from guanlan_v2.orchestration import decision_inputs as di
from guanlan_v2.orchestration import lane_catalog as lc
from guanlan_v2.orchestration import lane_payloads as lp
from guanlan_v2.orchestration import migration as M
from guanlan_v2.orchestration import phase7_registry as p7
from guanlan_v2.orchestration import shadow as sh
from guanlan_v2.orchestration.catalog import (
    ContentManifestEntry,
    EvidencePolicy,
    OutputBinding,
    SkillManifest,
    build_catalog_snapshot,
    catalog_material_digest,
    parse_skill_v1,
)
from guanlan_v2.orchestration.catalog_runtime import (
    CatalogRuntime,
    InMemoryMaterialSource,
    build_text_material,
)
from guanlan_v2.orchestration.data.symbols import normalize_symbol
from guanlan_v2.orchestration.debate import (
    DebateMessage,
    DebateTranscript,
    analyze_debates,
    verify_debate_expansion,
)
from guanlan_v2.orchestration.enums import (
    ApprovalPolicy,
    DataMode,
    ExecutionKind,
    MappingStatus,
    PlanSource,
    ToolCallRequirement,
)
from guanlan_v2.orchestration.refs import ContentRef, PayloadRef, SchemaRef
from guanlan_v2.orchestration.runtime_support import STATIC_RUNTIME_PROFILE_V2
from guanlan_v2.orchestration.schema_registry import SchemaRegistry
from guanlan_v2.orchestration.spec import (
    DebateCfg,
    Dependency,
    PlanDraft,
    PlanNode,
    PlanStructureError,
    ReducerCfg,
    validate_plan_draft,
)

# reuse the reviewed ContextSnapshot / OrchestrationRequest boilerplate
from tests.orchestration.test_plan_catalog_validation import _context, _dt, _request

_REPO = Path(__file__).resolve().parents[2]
_TREE = _REPO / "guanlan_v2" / "orchestration" / "skills"
_MIRROR = _REPO / "config" / "orchestration" / "materials" / "skills"
_DELIVERABLE3 = _REPO / "docs" / "superpowers" / "specs" / "2026-07-17-dec-research-mgr-pm-skills.md"
_UTC = timezone.utc
_NOW = datetime(2026, 7, 20, 1, 30, tzinfo=_UTC)
_SYM = normalize_symbol("600519.SH")


# --------------------------------------------------------------------------- #
# fixtures                                                                     #
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="module")
def specs() -> dict:
    materials = lc.load_decision_lane_materials()
    return {w.id: w for w in lc.build_decision_worker_specs(materials=materials)}


@pytest.fixture(scope="module")
def baseline() -> dict:
    return {w.id: w for w in p7.phase7_catalog_snapshot().workers}


# --------------------------------------------------------------------------- #
# roster expectation (the reviewed field-for-field matrix)                     #
# --------------------------------------------------------------------------- #
EXPECTED = {
    "dec.bull": dict(
        lane="decision", tier="critic", model_tier="reasoner", tool_calls="forbidden",
        guardrails=("guardrail.debate_rounds", "guardrail.number_provenance",
                    "guardrail.untrusted_input_isolation", "guardrail.vf_anchor_dict"),
        prompt="prompt.dec.bull", skill="skill.dec.bull", output="BullCase",
        read_categories=("context", "upstream_artifacts"), params=None,
        can_emit=False, authority="none",
        inputs=(("fundamentals", "FundamentalsReport", False, "one"),
                ("technical", "TechnicalReport", False, "one"),
                ("sentiment", "SentimentReport", False, "one"),
                ("news_digest", "NewsDigestReport", False, "one"),
                ("opponent_case", "BearCase", False, "one"))),
    "dec.bear": dict(
        lane="decision", tier="critic", model_tier="reasoner", tool_calls="forbidden",
        guardrails=("guardrail.debate_rounds", "guardrail.number_provenance",
                    "guardrail.untrusted_input_isolation", "guardrail.vf_anchor_dict"),
        prompt="prompt.dec.bear", skill="skill.dec.bear", output="BearCase",
        read_categories=("context", "upstream_artifacts"), params=None,
        can_emit=False, authority="none",
        inputs=(("fundamentals", "FundamentalsReport", False, "one"),
                ("technical", "TechnicalReport", False, "one"),
                ("sentiment", "SentimentReport", False, "one"),
                ("news_digest", "NewsDigestReport", False, "one"),
                ("opponent_case", "BullCase", True, "one"),
                ("announcement_risk", "AnnouncementRiskFlags", False, "one"))),
    "dec.risk_debate": dict(
        lane="decision", tier="critic", model_tier="fast", tool_calls="forbidden",
        guardrails=("guardrail.debate_rounds", "guardrail.number_provenance",
                    "guardrail.untrusted_input_isolation"),
        prompt="prompt.dec.risk_debate", skill="skill.dec.risk_debate",
        output="RiskDebateStance", read_categories=("context", "upstream_artifacts"),
        params="RiskDebateParams", can_emit=False, authority="none",
        inputs=(("research_plan", "ResearchPlan", True, "one"),
                ("opponent_stances", "RiskDebateStance", False, "many"),
                ("allowed_actions", "AllowedActions", False, "one"))),
    "dec.trader": dict(
        lane="decision", tier="writer", model_tier="reasoner", tool_calls="forbidden",
        guardrails=("guardrail.advisory_shadow_only", "guardrail.debate_rounds",
                    "guardrail.number_provenance", "guardrail.untrusted_input_isolation"),
        prompt="prompt.dec.trader", skill="skill.dec.trader",
        output="PortfolioTargetProposal", read_categories=("context", "upstream_artifacts"),
        params=None, can_emit=True, authority="advisory_only",
        inputs=(("portfolio_decision", "PortfolioDecision", True, "one"),)),
}


def test_all_six_seats_present(specs):
    assert set(specs) == set(lc.DECISION_LANE_WORKER_IDS)
    assert set(lc.DECISION_LANE_WORKER_IDS) == {
        "dec.bull", "dec.bear", "dec.research_mgr", "dec.risk_debate", "dec.pm", "dec.trader"}


@pytest.mark.parametrize("wid", sorted(EXPECTED))
def test_new_seat_worker_spec_matrix(specs, wid):
    w = specs[wid]
    exp = EXPECTED[wid]
    assert w.catalog_role == "final"
    assert w.selection_scope == "dynamic_allowed"
    assert w.compatibility is None
    assert w.lane == exp["lane"]
    assert w.tier.value == exp["tier"]
    assert w.execution.kind is ExecutionKind.LLM
    assert w.execution.model_tier == exp["model_tier"]
    assert w.system_prompt_ref is not None and w.system_prompt_ref.id == exp["prompt"]
    assert [sb.skill_ref.id for sb in w.skills] == [exp["skill"]]
    assert tuple(g.id for g in w.guardrail_refs) == exp["guardrails"]
    # Lane-D new seats are FORBIDDEN ⇔ empty allowlist (pool-fed, no tool)
    assert w.evidence_policy.tool_calls.value == exp["tool_calls"] == "forbidden"
    assert w.capability_allowlist == ()
    assert w.read_categories == exp["read_categories"]
    assert [o.name for o in w.outputs] == ["primary"]
    assert w.outputs[0].schema_ref.name == exp["output"]
    got_inputs = tuple(
        (i.name, i.schema_ref.name, i.required, i.cardinality) for i in w.inputs)
    assert got_inputs == exp["inputs"]
    assert (w.params_schema_ref.name if w.params_schema_ref else None) == exp["params"]
    assert w.can_emit_decision is exp["can_emit"]
    assert w.decision_authority == exp["authority"]
    assert [m.value for m in w.supported_modes] == ["online", "pit_replay"]
    assert w.evidence_policy.require_number_anchors is True
    assert w.evidence_policy.allow_unsourced_numbers is False


def test_debate_seats_use_only_fast_or_reasoner(specs):
    # invariant 5
    for wid in ("dec.bull", "dec.bear", "dec.risk_debate"):
        assert specs[wid].execution.model_tier in ("fast", "reasoner")


def test_asymmetric_ammo(specs):
    # invariant 6: bear carries announcement_risk; bull does not
    bear_inputs = {i.name for i in specs["dec.bear"].inputs}
    bull_inputs = {i.name for i in specs["dec.bull"].inputs}
    assert "announcement_risk" in bear_inputs
    assert "announcement_risk" not in bull_inputs
    assert specs["dec.bear"].inputs[-1].schema_ref.name == "AnnouncementRiskFlags"


# --------------------------------------------------------------------------- #
# clause (d) — pilot updates touch EXACTLY the reviewed fields                  #
# --------------------------------------------------------------------------- #
def test_research_mgr_pilot_update_changes_only_reviewed_fields(specs, baseline):
    b, u = baseline["dec.research_mgr"], specs["dec.research_mgr"]
    changed = [f for f in type(b).model_fields if getattr(b, f) != getattr(u, f)]
    assert changed == ["skills", "guardrail_refs", "inputs"]
    # skills rebind to the 交付物③ tree install
    assert b.skills[0].skill_ref.id == "skill.research_mgr"
    assert u.skills[0].skill_ref.id == "skill.dec.research_mgr"
    # guardrail.debate_rounds appended to the frozen pilot pair
    assert [g.id for g in b.guardrail_refs] == ["guard.provenance", "guard.advisory_discipline"]
    assert [g.id for g in u.guardrail_refs] == [
        "guard.provenance", "guard.advisory_discipline", "guardrail.debate_rounds"]
    # inputs = pilot baseline + the two reviewed optional inputs
    added = tuple((i.name, i.schema_ref.name) for i in u.inputs[len(b.inputs):])
    assert added == (("bullbear_transcript", "DebateTranscript"),
                     ("upstream_ratings", "UpstreamRatingsExtract"))
    # frozen fields genuinely unchanged (clause d)
    assert u.execution == b.execution
    assert u.evidence_policy == b.evidence_policy
    assert u.capability_allowlist == b.capability_allowlist
    assert u.read_categories == b.read_categories  # NOT the roster 2-col — clause d freezes it
    assert u.semantic_digest() != b.semantic_digest()


def test_pm_pilot_update_changes_only_reviewed_fields(specs, baseline):
    b, u = baseline["dec.pm"], specs["dec.pm"]
    changed = [f for f in type(b).model_fields if getattr(b, f) != getattr(u, f)]
    assert changed == ["skills", "guardrail_refs", "read_categories", "inputs"]
    assert b.skills[0].skill_ref.id == "skill.pm"
    assert u.skills[0].skill_ref.id == "skill.dec.pm"
    assert [g.id for g in u.guardrail_refs][-1] == "guardrail.debate_rounds"
    # memory added to read_categories
    assert "memory" not in b.read_categories and "memory" in u.read_categories
    assert u.read_categories == tuple(b.read_categories) + ("memory",)
    added = tuple((i.name, i.schema_ref.name) for i in u.inputs[len(b.inputs):])
    assert added == (("riskdebate_transcript", "DebateTranscript"),
                     ("allowed_actions", "AllowedActions"),
                     ("announcement_risk", "AnnouncementRiskFlags"))
    # clause d freezes execution (already reasoner_deep), evidence policy, allowlist
    assert u.execution == b.execution
    assert u.evidence_policy == b.evidence_policy
    assert u.capability_allowlist == b.capability_allowlist


def test_pm_is_reasoner_deep(specs):
    assert specs["dec.pm"].execution.model_tier == "reasoner_deep"


# --------------------------------------------------------------------------- #
# invariant 1: dec.pm is the ONLY reasoner_deep across the whole lane_catalog   #
# --------------------------------------------------------------------------- #
def _all_lane_specs():
    out = list(lc.build_text_worker_specs(materials=lc.load_text_lane_materials()))
    out += list(lc.build_pv_worker_specs(materials=lc.load_pv_lane_materials()))
    out += list(lc.build_quant_worker_specs(materials=lc.load_quant_lane_materials()))
    out += list(lc.build_xcut_worker_specs(materials=lc.load_xcut_lane_materials()))
    out += list(lc.build_decision_worker_specs(materials=lc.load_decision_lane_materials()))
    return out


def test_dec_pm_is_the_sole_reasoner_deep_in_lane_catalog():
    deep = [w.id for w in _all_lane_specs()
            if w.execution.kind is ExecutionKind.LLM
            and w.execution.model_tier == "reasoner_deep"]
    assert deep == ["dec.pm"], deep


# --------------------------------------------------------------------------- #
# invariant 2: dec.trader emits Phase 6's PortfolioTargetProposal (never redef) #
# --------------------------------------------------------------------------- #
def test_trader_output_is_phase6_target_proposal(specs):
    assert specs["dec.trader"].outputs[0].schema_ref == SchemaRef(
        name="PortfolioTargetProposal", version="1")
    # the model lives in Phase 6 (shadow) and NO Phase-8 module redefines it
    assert hasattr(sh, "PortfolioTargetProposal")
    assert not hasattr(lp, "PortfolioTargetProposal")
    assert not hasattr(di, "PortfolioTargetProposal")
    assert not hasattr(lc, "PortfolioTargetProposal")


def test_trader_has_no_order_signal_intent_capability(specs):
    tr = specs["dec.trader"]
    assert tr.evidence_policy.tool_calls is ToolCallRequirement.FORBIDDEN
    assert tr.capability_allowlist == ()
    # no Phase-8 module can construct a TargetPortfolioIntent
    assert not hasattr(lp, "TargetPortfolioIntent")
    assert not hasattr(di, "TargetPortfolioIntent")


# --------------------------------------------------------------------------- #
# 交付物③ byte-verbatim install fidelity (both pilots)                          #
# --------------------------------------------------------------------------- #
def _extract_deliverable3_blocks() -> list[bytes]:
    """The two 逐字安装件: the two ```markdown fenced blocks (+ trailing newline each)."""
    lines = _DELIVERABLE3.read_text(encoding="utf-8").split("\n")
    blocks: list[bytes] = []
    i = 0
    while i < len(lines):
        if lines[i].strip() == "```markdown":
            start = i + 1
            j = start
            while j < len(lines) and lines[j].strip() != "```":
                j += 1
            blocks.append(("\n".join(lines[start:j]) + "\n").encode("utf-8"))
            i = j + 1
        else:
            i += 1
    return blocks


def test_deliverable3_has_exactly_two_install_blocks():
    assert len(_extract_deliverable3_blocks()) == 2


@pytest.mark.parametrize("wid,idx", [("dec.research_mgr", 0), ("dec.pm", 1)])
def test_pilot_tree_is_deliverable3_byte_verbatim(wid, idx):
    tree_bytes = (_TREE / wid / "SKILL.md").read_bytes().replace(b"\r\n", b"\n")
    assert tree_bytes == _extract_deliverable3_blocks()[idx]


@pytest.mark.parametrize("wid", ["dec.research_mgr", "dec.pm"])
def test_pilot_install_digest_matches_bound_skill(specs, wid):
    from guanlan_v2.orchestration.catalog import ResolvedTextMaterial
    tree_bytes = (_TREE / wid / "SKILL.md").read_bytes()
    mat = ResolvedTextMaterial(
        ref=ContentRef(id=f"skill.{wid}", version="1", content_digest="0" * 64),
        kind="skill", raw_utf8=tree_bytes)
    d = catalog_material_digest(mat)
    assert specs[wid].skills[0].skill_ref.content_digest == d
    # the install digest differs from the pilot relocation digest the Phase-7 catalog binds
    base = next(w for w in p7.phase7_catalog_snapshot().workers if w.id == wid)
    assert base.skills[0].skill_ref.content_digest != d


def test_supersession_set_extended_to_both_pilots():
    from tests.orchestration.test_skilltree import SUPERSEDED_IN_PHASE8
    assert {"dec.research_mgr", "dec.pm"} <= SUPERSEDED_IN_PHASE8


# --------------------------------------------------------------------------- #
# skill grammar + mirror byte identity (all six)                               #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("wid", sorted(lc.DECISION_LANE_WORKER_IDS))
def test_skill_grammar_and_mirror_identity(wid):
    tree_bytes = (_TREE / wid / "SKILL.md").read_bytes()
    parse_skill_v1(tree_bytes.decode("utf-8"))
    assert not tree_bytes.startswith(b"\xef\xbb\xbf")
    assert (_MIRROR / f"{wid}.md").read_bytes() == tree_bytes


def test_new_seat_skills_encode_charter_doctrine():
    bear = (_TREE / "dec.bear" / "SKILL.md").read_text(encoding="utf-8")
    assert "must-oppose" in bear and "F1" in bear and "announcement" in bear.lower()
    risk = (_TREE / "dec.risk_debate" / "SKILL.md").read_text(encoding="utf-8")
    assert "[-2, 0]" in risk and "aggressive" in risk
    trader = (_TREE / "dec.trader" / "SKILL.md").read_text(encoding="utf-8")
    assert "TARGET_WEIGHT_BANDS" in trader and "TargetPortfolioIntent" in trader


# --------------------------------------------------------------------------- #
# payload schema matrices                                                       #
# --------------------------------------------------------------------------- #
def test_bull_case_matrix():
    b = lp.BullCase(symbol=_SYM, as_of=_NOW, thesis_bullets=("undervalued",),
                    target_price_high=250.0, v_anchors=("V3",))
    assert b.schema_version == "1" and b.symbol.dotted == "600519.SH"
    # non-empty thesis
    with pytest.raises(Exception):
        lp.BullCase(symbol=_SYM, as_of=_NOW, thesis_bullets=())
    # PositivePrice: non-positive / non-finite rejected
    for bad in (0.0, -1.0, float("inf")):
        with pytest.raises(Exception):
            lp.BullCase(symbol=_SYM, as_of=_NOW, thesis_bullets=("x",), target_price_high=bad)
    # justified-belief: an 'update' flip must name evidence
    with pytest.raises(Exception):
        lp.BullCase(symbol=_SYM, as_of=_NOW, thesis_bullets=("x",), stance_change="update")
    assert lp.BullCase(symbol=_SYM, as_of=_NOW, thesis_bullets=("x",),
                       stance_change="update", stance_evidence="new print").stance_evidence
    with pytest.raises(Exception):
        b.thesis_bullets = ()  # frozen


def test_bear_case_matrix():
    be = lp.BearCase(symbol=_SYM, as_of=_NOW, thesis_bullets=("overvalued",),
                     f_anchors=("F1",), downside_pct=-0.2, target_price_low=100.0)
    assert be.schema_version == "1"
    with pytest.raises(Exception):
        lp.BearCase(symbol=_SYM, as_of=_NOW, thesis_bullets=())
    with pytest.raises(Exception):
        lp.BearCase(symbol=_SYM, as_of=_NOW, thesis_bullets=("x",), target_price_low=0.0)


def test_risk_debate_stance_matrix():
    r = lp.RiskDebateStance(symbol=_SYM, as_of=_NOW, stance_role="steady",
                            risk_score=-1, position_sizing_advice="quarter")
    assert r.risk_score == -1
    # legacy domain: risk_score is an int in [-2, 0], never positive, bool rejected
    for bad in (1, -3, True, False, 0.5):
        with pytest.raises(Exception):
            lp.RiskDebateStance(symbol=_SYM, as_of=_NOW, stance_role="steady",
                                risk_score=bad, position_sizing_advice="x")
    assert lp.RiskDebateStance(symbol=_SYM, as_of=_NOW, stance_role="aggressive",
                               risk_score=0, position_sizing_advice="x").risk_score == 0
    # closed stance_role literal
    with pytest.raises(Exception):
        lp.RiskDebateStance(symbol=_SYM, as_of=_NOW, stance_role="bogus",
                            risk_score=0, position_sizing_advice="x")


def test_risk_debate_params_matrix():
    p = lp.RiskDebateParams(stance_role="neutral")
    assert p.stance_role == "neutral"
    with pytest.raises(Exception):
        lp.RiskDebateParams(stance_role="bogus")


def test_lane_d_public_models_partition():
    assert set(lp.LANE_D_PUBLIC_MODELS) == {
        lp.BullCase, lp.BearCase, lp.RiskDebateStance, lp.RiskDebateParams}


# --------------------------------------------------------------------------- #
# legacy adapter regression rows                                               #
# --------------------------------------------------------------------------- #
def test_report_action_mapped_and_reverses_exact():
    mig = M.migrate_research_action("hold", source_schema=M.SRC_REPORT_OUTPUT)
    assert mig.mapping_status is MappingStatus.MAPPED
    assert M.research_action_to_legacy(mig) == "hold"


def test_report_rating_unmappable_and_reverses_exact():
    mig = M.migrate_rating(5, source_schema=M.SRC_REPORT_OUTPUT)
    assert mig.mapping_status is MappingStatus.UNMAPPABLE
    assert mig.mapping_basis == "none"
    assert M.rating_to_legacy(mig) == 5  # no invented 5-band binning


def test_position_action_mapped_and_reverses_exact():
    mig = M.migrate_position_action("reduce", source_schema=M.SRC_WATCH_REC)
    assert mig.mapping_status is MappingStatus.MAPPED
    assert M.position_action_to_legacy(mig) == "reduce"


def test_confidence_adapter_identity_and_med_unmappable():
    hi = M.migrate_confidence("high", source_schema=M.SRC_INTROSPECTION_PROPOSAL)
    assert hi.mapping_status is MappingStatus.MAPPED
    med = M.migrate_confidence("med", source_schema=M.SRC_INTROSPECTION_PROPOSAL)
    assert med.mapping_status is MappingStatus.UNMAPPABLE


def test_risk_score_has_no_phase1_scalar_adapter():
    # the batch preserves RiskOutput.risk_score's domain in RiskDebateStance and
    # asserts adapter ABSENCE rather than inventing one.
    assert not hasattr(M, "migrate_risk_score")
    assert not hasattr(M, "risk_score_to_legacy")


# --------------------------------------------------------------------------- #
# SymbolConstraintFacts contract text (fail-closed + T+1 lot semantics)         #
# --------------------------------------------------------------------------- #
def test_symbol_constraint_facts_docstring_carries_the_contract():
    doc = di.SymbolConstraintFacts.__doc__ or ""
    assert "fail-closed" in doc
    assert "lot" in doc and "T+1" in doc


# =========================================================================== #
# Canonical Lane D debate-topology fixture (invariant 3 / 4)                    #
# =========================================================================== #
_RED = "debate.transcript_reducer"
_DIG = "a" * 64


def _reducer_material():
    return build_text_material(id=_RED, version="1", kind="reducer",
                               raw=b"debate transcript reducer\n")


def _fixture_registry() -> SchemaRegistry:
    reg = SchemaRegistry()
    reg.register(lp.RiskDebateParams)
    reg.register(DebateMessage)
    reg.register(DebateTranscript)
    reg.seal()
    return reg


def _fixture_catalog():
    """A purpose-built Lane D catalog for the topology fixture.

    Uses the REAL Lane-D worker specs (the four new seats + ``dec.research_mgr`` — all
    FORBIDDEN ⇔ empty allowlist) + a real ``text.sentiment`` producer + the two ``x.*`` critics.
    ``dec.pm`` is included as a FORBIDDEN stand-in (its two capabilities are orthogonal to the
    plan topology and kept out of the fixture; the real capability handling is Task 12's e2e).
    Every material gets a manifest entry so ``build_catalog_snapshot`` sees no orphans; only the
    plan's workers are bound.
    """
    text_mats = lc.load_text_lane_materials()
    xcut_mats = lc.load_xcut_lane_materials()
    dec_mats = lc.load_decision_lane_materials()
    text_specs = {w.id: w for w in lc.build_text_worker_specs(materials=text_mats)}
    xcut_specs = {w.id: w for w in lc.build_xcut_worker_specs(materials=xcut_mats)}
    dec_specs = {w.id: w for w in lc.build_decision_worker_specs(materials=dec_mats)}
    pm_standin = dec_specs["dec.pm"].model_copy(update=dict(
        evidence_policy=EvidencePolicy(tool_calls=ToolCallRequirement.FORBIDDEN),
        capability_allowlist=()))
    workers = (
        text_specs["text.sentiment"],
        dec_specs["dec.bull"], dec_specs["dec.bear"], dec_specs["dec.risk_debate"],
        dec_specs["dec.research_mgr"], pm_standin, dec_specs["dec.trader"],
        xcut_specs["x.number_critic"], xcut_specs["x.quality_gate"],
    )
    red_ref, red_mat = _reducer_material()
    by_key = {}
    for m in list(text_mats) + list(xcut_mats) + list(dec_mats) + [red_mat]:
        by_key[m.ref_key] = m
    mats = tuple(by_key.values())
    content, skills = [], []
    for m in mats:
        if m.kind == "skill":
            parsed = parse_skill_v1(m.raw_utf8.decode("utf-8"))
            skills.append(SkillManifest(
                ref=m.ref, name=parsed.name, summary=parsed.summary,
                perfect_for=parsed.perfect_for, not_ideal_for=parsed.not_ideal_for,
                critical_data_source_heading="⚠️ CRITICAL: Data Source Priority",
                source_identity=m.ref.id))
        else:
            content.append(ContentManifestEntry(
                ref=m.ref, kind=m.kind, name="x", description="d", source_identity="gl"))
    snapshot = build_catalog_snapshot(
        catalog_version="lane-d-fixture-v1", content_manifest=tuple(content),
        skill_manifest=tuple(skills), capability_manifest=(), workers=workers,
        resolved_material=mats)
    source = InMemoryMaterialSource(
        text={(m.ref.id, m.ref.version): m.raw_utf8 for m in mats}, capabilities={})
    return snapshot, source, red_ref


_BULLBEAR = DebateCfg(id="bullbear", seats=("bull", "bear"), turn_order=("bull", "bear"),
                      max_rounds=2, judge_node_id="research-mgr")
_RISK_ROLES = ("aggressive", "steady", "neutral")
_RISKDEBATE = DebateCfg(id="riskdebate", seats=_RISK_ROLES, turn_order=_RISK_ROLES,
                        max_rounds=2, judge_node_id="pm")


def _dep(up, slot, inject):
    return Dependency(upstream_node_id=up, artifact_slot=slot, upstream_output_key="primary",
                      inject_as=inject)


def _seat(node_id, worker, debate, role, rnd, turn, slot, deps=()):
    return PlanNode(id=node_id, worker_id=worker, writes_slot=slot, debate_id=debate,
                    round_role=role, debate_round=rnd, debate_turn=turn, auxiliary=True,
                    dependencies=tuple(deps),
                    params=({"stance_role": role} if worker == "dec.risk_debate" else {}))


def _fixture_nodes():
    BB = "slot.bullbear_transcript"
    RD = "slot.riskdebate_transcript"
    nodes = [
        PlanNode(id="sentiment", worker_id="text.sentiment", writes_slot="slot.sentiment"),
        _seat("bull-r1", "dec.bull", "bullbear", "bull", 1, 1, BB),
        _seat("bear-r1", "dec.bear", "bullbear", "bear", 1, 2, BB,
              deps=[_dep("bull-r1", BB, "opponent_case")]),
        _seat("bull-r2", "dec.bull", "bullbear", "bull", 2, 1, BB,
              deps=[_dep("bear-r1", BB, "opponent_case")]),
        _seat("bear-r2", "dec.bear", "bullbear", "bear", 2, 2, BB,
              deps=[_dep("bull-r2", BB, "opponent_case")]),
        PlanNode(id="research-mgr", worker_id="dec.research_mgr",
                 writes_slot="slot.research_plan",
                 dependencies=(_dep("sentiment", "slot.sentiment", "sentiment"),)),
    ]
    for i, role in enumerate(_RISK_ROLES, start=1):
        nodes.append(_seat(f"risk-{role}-r1", "dec.risk_debate", "riskdebate", role, 1, i, RD,
                           deps=[_dep("research-mgr", "slot.research_plan", "research_plan")]))
    for i, role in enumerate(_RISK_ROLES, start=1):
        deps = [_dep("research-mgr", "slot.research_plan", "research_plan")]
        deps += [_dep(f"risk-{r}-r1", RD, "opponent_stances") for r in _RISK_ROLES]
        nodes.append(_seat(f"risk-{role}-r2", "dec.risk_debate", "riskdebate", role, 2, i, RD,
                           deps=deps))
    nodes.append(PlanNode(
        id="pm", worker_id="dec.pm", writes_slot="slot.portfolio_decision",
        dependencies=(_dep("research-mgr", "slot.research_plan", "research_plan"),
                      _dep("sentiment", "slot.sentiment", "sentiment"))))
    nodes.append(PlanNode(
        id="trader", worker_id="dec.trader", writes_slot="slot.target_proposal",
        dependencies=(_dep("pm", "slot.portfolio_decision", "portfolio_decision"),)))
    nodes.append(PlanNode(id="number-critic", worker_id="x.number_critic",
                          writes_slot="slot.honesty"))
    return nodes


def _fixture_reducers(red_ref):
    BB = "slot.bullbear_transcript"
    RD = "slot.riskdebate_transcript"
    tr = SchemaRef(name="DebateTranscript", version="1")
    return (
        ReducerCfg(id="red.bullbear", slot=BB, reducer_ref=red_ref,
                   producer_node_ids=("bull-r1", "bear-r1", "bull-r2", "bear-r2"),
                   output_schema_ref=tr),
        ReducerCfg(id="red.riskdebate", slot=RD, reducer_ref=red_ref,
                   producer_node_ids=tuple(f"risk-{r}-r{k}" for k in (1, 2) for r in _RISK_ROLES),
                   output_schema_ref=tr),
    )


def _fixture_draft(nodes, reducers, snapshot, registry, context, *, budget=14):
    ref = PayloadRef(namespace="main", object_id="ctx-obj",
                     content_digest=context.content_digest)
    return PlanDraft(
        id="plan.lane-d", run_id="run-1", request_id="req-1", phase="main",
        source=PlanSource.DYNAMIC, goal="g", as_of=_dt(), mode=DataMode.ONLINE,
        context_snapshot_ref=ref, nodes=tuple(nodes), sink_node_ids=("trader", "number-critic"),
        debates=(_BULLBEAR, _RISKDEBATE), reducers=tuple(reducers),
        catalog_version=snapshot.catalog_version, catalog_digest=snapshot.catalog_digest,
        schema_registry_digest=registry.registry_digest, approval_policy=ApprovalPolicy.REQUIRED,
        budget_request_tokens=100000, budget_request_llm_invocations=budget, max_concurrency=4)


@pytest.fixture(scope="module")
def fixture_env():
    snapshot, source, red_ref = _fixture_catalog()
    runtime = CatalogRuntime.build(snapshot, source)
    registry = _fixture_registry()
    context = _context()
    request = _request()
    nodes = _fixture_nodes()
    reducers = _fixture_reducers(red_ref)
    draft = _fixture_draft(nodes, reducers, snapshot, registry, context)
    return dict(snapshot=snapshot, runtime=runtime, registry=registry, context=context,
                request=request, nodes=nodes, reducers=reducers, red_ref=red_ref, draft=draft)


def test_canonical_fixture_passes_validate_plan_draft(fixture_env):
    report = validate_plan_draft(
        fixture_env["draft"], request=fixture_env["request"], context=fixture_env["context"],
        catalog=fixture_env["snapshot"], schema_registry=fixture_env["registry"])
    assert report.valid, [i.code for i in report.issues]


def test_canonical_fixture_debates_fully_expand(fixture_env):
    assert verify_debate_expansion(fixture_env["draft"], _BULLBEAR) == ()
    assert verify_debate_expansion(fixture_env["draft"], _RISKDEBATE) == ()


def test_canonical_fixture_analyze_debates_clean(fixture_env):
    issues = analyze_debates(fixture_env["draft"], catalog=fixture_env["runtime"],
                             profile=STATIC_RUNTIME_PROFILE_V2)
    assert issues == (), [i.code for i in issues]


def test_risk_debate_is_one_spec_multi_instance_distinct_role(fixture_env):
    # ONE dec.risk_debate WorkerSpec instantiated as SIX PlanNodes with distinct role/round,
    # all writing the single reduced slot (market.rotation dual-node precedent, generalized).
    risk_nodes = [n for n in fixture_env["nodes"] if n.worker_id == "dec.risk_debate"]
    assert len(risk_nodes) == 6
    assert {n.writes_slot for n in risk_nodes} == {"slot.riskdebate_transcript"}
    assert {(n.round_role, n.debate_round) for n in risk_nodes} == {
        (r, k) for k in (1, 2) for r in _RISK_ROLES}
    # each node's params.stance_role equals its round_role seat
    assert all(n.params["stance_role"] == n.round_role for n in risk_nodes)


def test_bull_bear_late_rebuttal_dependencies(fixture_env):
    # invariant 4: bull-r2 depends on bear-r1; bear-r2 depends on bull-r2 (晚一波 lives in deps)
    by_id = {n.id: n for n in fixture_env["nodes"]}
    assert any(d.upstream_node_id == "bear-r1" for d in by_id["bull-r2"].dependencies)
    assert any(d.upstream_node_id == "bull-r2" for d in by_id["bear-r2"].dependencies)
    assert any(d.upstream_node_id == "bull-r1" for d in by_id["bear-r1"].dependencies)


# --------------------------------------------------------------------------- #
# mutations — each fails with its exact issue code                             #
# --------------------------------------------------------------------------- #
def _codes(issues):
    return {i.code for i in issues}


def test_mutation_missing_turn_is_incomplete_round(fixture_env):
    nodes = [n for n in fixture_env["nodes"] if n.id != "bear-r2"]
    # minimal draft for the expansion check (drop the reducer/full validate — the seat set
    # alone drives verify_debate_expansion)
    draft = fixture_env["draft"].model_construct(
        **{**fixture_env["draft"].__dict__, "nodes": tuple(nodes)})
    assert "debate_incomplete_round" in _codes(verify_debate_expansion(draft, _BULLBEAR))


def test_mutation_round_three_is_phase1_out_of_range(fixture_env):
    # a round-3 seat is rejected by Phase 1 at PlanDraft construction
    bad = _seat("risk-aggressive-r3", "dec.risk_debate", "riskdebate", "aggressive", 3, 1,
                "slot.riskdebate_transcript",
                deps=[_dep("research-mgr", "slot.research_plan", "research_plan")])
    nodes = list(fixture_env["nodes"]) + [bad]
    reducers = _fixture_reducers(fixture_env["red_ref"])
    with pytest.raises((PlanStructureError, Exception)):
        _fixture_draft(nodes, reducers, fixture_env["snapshot"], fixture_env["registry"],
                       fixture_env["context"])
    # and the exact Phase-1 code is debate_round_out_of_range
    from guanlan_v2.orchestration.spec import _structural_issues
    over = fixture_env["draft"].model_construct(
        **{**fixture_env["draft"].__dict__, "nodes": tuple(nodes)})
    assert "debate_round_out_of_range" in {i.code for i in _structural_issues(over)}


def test_mutation_judge_as_seat_is_debate_judge_is_seat(fixture_env):
    bad_debate = DebateCfg(id="bullbear", seats=("bull", "bear"), turn_order=("bull", "bear"),
                           max_rounds=2, judge_node_id="bull-r1")  # judge points at a seat
    draft = fixture_env["draft"].model_construct(
        **{**fixture_env["draft"].__dict__, "debates": (bad_debate, _RISKDEBATE)})
    issues = analyze_debates(draft, catalog=fixture_env["runtime"],
                             profile=STATIC_RUNTIME_PROFILE_V2)
    assert "debate_judge_is_seat" in _codes(issues)


def test_mutation_non_llm_seat_is_debate_seat_not_llm(fixture_env):
    # point a bull seat at the deterministic x.number_critic worker
    nodes = []
    for n in fixture_env["nodes"]:
        if n.id == "bull-r1":
            nodes.append(n.model_copy(update={"worker_id": "x.number_critic"}))
        else:
            nodes.append(n)
    draft = fixture_env["draft"].model_construct(
        **{**fixture_env["draft"].__dict__, "nodes": tuple(nodes)})
    issues = analyze_debates(draft, catalog=fixture_env["runtime"],
                             profile=STATIC_RUNTIME_PROFILE_V2)
    assert "debate_seat_not_llm" in _codes(issues)


def _risk_role_param_mismatches(nodes, *, worker_id="dec.risk_debate", key="stance_role"):
    """Fixture-level coherence: a risk seat's params.stance_role must equal its round_role
    (the per-instance seat assignment). No shipped Task-9 analyzer binds params↔round_role
    at runtime yet (Task 12 / Phase 9); the batch owes the fixture-level proof."""
    out = []
    for n in nodes:
        if n.worker_id == worker_id and n.debate_id is not None:
            if n.params.get(key) != n.round_role:
                out.append(("debate_seat_role_param_mismatch", n.id))
    return out


def test_wellformed_risk_seats_are_role_coherent(fixture_env):
    assert _risk_role_param_mismatches(fixture_env["nodes"]) == []


def test_mutation_stance_role_ne_round_role_is_caught(fixture_env):
    nodes = []
    for n in fixture_env["nodes"]:
        if n.id == "risk-aggressive-r1":
            # a valid literal, but DISAGREEING with the round_role seat assignment
            nodes.append(n.model_copy(update={"params": {"stance_role": "neutral"}}))
        else:
            nodes.append(n)
    mismatches = _risk_role_param_mismatches(nodes)
    assert mismatches and mismatches[0][0] == "debate_seat_role_param_mismatch"
    assert mismatches[0][1] == "risk-aggressive-r1"


def test_mutation_invalid_stance_role_param_is_schema_violation(fixture_env):
    nodes = []
    for n in fixture_env["nodes"]:
        if n.id == "risk-aggressive-r1":
            nodes.append(n.model_copy(update={"params": {"stance_role": "bogus"}}))
        else:
            nodes.append(n)
    draft = fixture_env["draft"].model_construct(
        **{**fixture_env["draft"].__dict__, "nodes": tuple(nodes)})
    report = validate_plan_draft(
        draft, request=fixture_env["request"], context=fixture_env["context"],
        catalog=fixture_env["snapshot"], schema_registry=fixture_env["registry"])
    assert "params_schema_violation" in _codes(report.issues)


def test_mutation_trader_cannot_emit_is_unauthorized_sink(fixture_env):
    # a trader stand-in that cannot emit a decision fails sink authorization on its
    # PortfolioTargetProposal (decision-class) output.
    snapshot, source, red_ref = _fixture_catalog()
    # rebuild the catalog with a can_emit=False trader
    from guanlan_v2.orchestration.catalog import WorkerSpec  # noqa: F401
    dec_specs = {w.id: w for w in lc.build_decision_worker_specs(
        materials=lc.load_decision_lane_materials())}
    trader_noemit = dec_specs["dec.trader"].model_copy(update=dict(
        can_emit_decision=False, decision_authority="none"))
    workers = tuple(
        trader_noemit if w.id == "dec.trader" else w for w in snapshot.workers)
    # rebuild snapshot with the swapped worker (reuse the same materials via source)
    text_mats = lc.load_text_lane_materials()
    xcut_mats = lc.load_xcut_lane_materials()
    dec_mats = lc.load_decision_lane_materials()
    red_mat = _reducer_material()[1]
    by_key = {}
    for m in list(text_mats) + list(xcut_mats) + list(dec_mats) + [red_mat]:
        by_key[m.ref_key] = m
    mats = tuple(by_key.values())
    content, skills = [], []
    for m in mats:
        if m.kind == "skill":
            parsed = parse_skill_v1(m.raw_utf8.decode("utf-8"))
            skills.append(SkillManifest(
                ref=m.ref, name=parsed.name, summary=parsed.summary,
                perfect_for=parsed.perfect_for, not_ideal_for=parsed.not_ideal_for,
                critical_data_source_heading="⚠️ CRITICAL: Data Source Priority",
                source_identity=m.ref.id))
        else:
            content.append(ContentManifestEntry(
                ref=m.ref, kind=m.kind, name="x", description="d", source_identity="gl"))
    snap2 = build_catalog_snapshot(
        catalog_version="lane-d-fixture-v1", content_manifest=tuple(content),
        skill_manifest=tuple(skills), capability_manifest=(), workers=workers,
        resolved_material=mats)
    registry = _fixture_registry()
    context = _context()
    draft = _fixture_draft(_fixture_nodes(), _fixture_reducers(red_ref), snap2, registry, context)
    report = validate_plan_draft(draft, request=_request(), context=context,
                                 catalog=snap2, schema_registry=registry)
    assert "unauthorized_decision_sink" in _codes(report.issues)


def test_mutation_under_budget_is_debate_budget_under_requested(fixture_env):
    draft = _fixture_draft(_fixture_nodes(), _fixture_reducers(fixture_env["red_ref"]),
                           fixture_env["snapshot"], fixture_env["registry"],
                           fixture_env["context"], budget=3)
    issues = analyze_debates(draft, catalog=fixture_env["runtime"],
                             profile=STATIC_RUNTIME_PROFILE_V2)
    assert "debate_budget_under_requested" in _codes(issues)
