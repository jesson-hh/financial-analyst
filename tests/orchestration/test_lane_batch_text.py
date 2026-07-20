# -*- coding: utf-8 -*-
"""Phase 8 · Task 4 — Batch 1 · Lane C 文本 (4 new + the text.sentiment pilot update).

Covers (per brief Step 1):
* the WorkerSpec matrix per roster row (lane/tier/model_tier/tool policy/allowlist/
  guardrails/read_categories/output/inputs/decision authority), field-for-field;
* the reviewed allowlist broadening (text.news += fundamentals; text.macro +=
  indicators + verified_snapshot) so every shipped seat's ``lint_skill_supply == ()``;
* the clause-(d) pilot update: the text.sentiment WorkerSpec differs from the frozen
  Phase-7 baseline in EXACTLY ``skills`` + ``guardrail_refs``;
* the 交付物② byte-verbatim install: the tree file bytes equal the spec-extracted
  fenced block (+ trailing newline);
* skill-v1 grammar (real ``parse_skill_v1``) + mirror byte identity for all five;
* the Lane C payload schema matrices (bounds, honest-shortfall fields, frozen);
* the legacy-sentiment adapter regression (all UNMAPPABLE, reverses exact) INCLUDING
  the two Task-0-review carry vectors ``SRC_PULSE_TEMP`` / ``SRC_MARKET_TEMP_GATE``.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from guanlan_v2.orchestration import lane_catalog as lc
from guanlan_v2.orchestration import lane_payloads as lp
from guanlan_v2.orchestration import migration as M
from guanlan_v2.orchestration import phase7_registry as p7
from guanlan_v2.orchestration.capability_manifest import (
    MATERIAL_PATH as CAP_MANIFEST_PATH,
    lint_skill_supply,
    parse_capability_manifest,
)
from guanlan_v2.orchestration.catalog import catalog_material_digest, parse_skill_v1
from guanlan_v2.orchestration.data.symbols import normalize_symbol
from guanlan_v2.orchestration.enums import MappingStatus

_REPO = Path(__file__).resolve().parents[2]
_TREE = _REPO / "guanlan_v2" / "orchestration" / "skills"
_MIRROR = _REPO / "config" / "orchestration" / "materials" / "skills"
_DELIVERABLE2 = _REPO / "docs" / "superpowers" / "specs" / "2026-07-17-text-sentiment-skill.md"
_UTC = timezone.utc


# --------------------------------------------------------------------------- #
# fixtures                                                                     #
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="module")
def specs() -> dict:
    materials = lc.load_text_lane_materials()
    return {w.id: w for w in lc.build_text_worker_specs(materials=materials)}


@pytest.fixture(scope="module")
def cap_manifest():
    return parse_capability_manifest(CAP_MANIFEST_PATH.read_bytes())


# --------------------------------------------------------------------------- #
# roster expectation (the reviewed field-for-field matrix)                     #
# --------------------------------------------------------------------------- #
EXPECTED = {
    "text.news": dict(
        tier="reader", model_tier="fast", tool_calls="required",
        caps=("cap.data.fundamentals", "cap.data.news"),
        guardrails=("guardrail.anti_fabrication", "guardrail.number_provenance",
                    "guardrail.untrusted_input_isolation"),
        prompt="prompt.text.news", skill="skill.text.news",
        output="NewsDigestReport", read_categories=("context", "market_data"),
        inputs=(), persona="A-share news reader"),
    "text.sentiment": dict(
        tier="writer", model_tier="reasoner", tool_calls="forbidden",
        caps=(),
        guardrails=("guard.provenance", "guardrail.anti_fabrication"),
        prompt="prompt.sentiment", skill="skill.text.sentiment",
        output="SentimentReport", read_categories=("context", "market_data"),
        inputs=(), persona="A-share sentiment analyst"),
    "text.research_report": dict(
        tier="reader", model_tier="reasoner", tool_calls="optional",
        caps=("cap.data.news",),
        guardrails=("guardrail.number_provenance", "guardrail.untrusted_input_isolation"),
        prompt="prompt.text.research_report", skill="skill.text.research_report",
        output="ResearchReportExtract",
        read_categories=("context", "market_data", "upstream_artifacts"),
        inputs=(("news_digest", "NewsDigestReport", "1", False, "one"),),
        persona="Research report extractor"),
    "text.policy": dict(
        tier="reader", model_tier="fast", tool_calls="required",
        caps=("cap.data.news",),
        guardrails=("guardrail.number_provenance", "guardrail.untrusted_input_isolation"),
        prompt="prompt.text.policy", skill="skill.text.policy",
        output="PolicyReport", read_categories=("context", "market_data"),
        inputs=(), persona="A-share policy reader"),
    "text.macro": dict(
        tier="reader", model_tier="fast", tool_calls="optional",
        caps=("cap.data.indicators", "cap.data.signals", "cap.data.verified_snapshot"),
        guardrails=("guardrail.number_provenance", "guardrail.untrusted_input_isolation"),
        prompt="prompt.text.macro", skill="skill.text.macro",
        output="MacroPulseReport", read_categories=("context", "market_data"),
        inputs=(), persona="Macro pulse reader"),
}


def test_all_five_seats_present(specs):
    assert set(specs) == set(EXPECTED) == set(lc.TEXT_LANE_WORKER_IDS)


@pytest.mark.parametrize("wid", sorted(EXPECTED))
def test_worker_spec_matrix(specs, wid):
    w = specs[wid]
    exp = EXPECTED[wid]
    # identity / role
    assert w.catalog_role == "final"
    assert w.selection_scope == "dynamic_allowed"
    assert w.compatibility is None
    assert w.lane == "text"
    assert w.persona == exp["persona"]
    assert w.tier.value == exp["tier"]
    # execution (LLM ⇒ system_prompt_ref + model_tier)
    assert w.execution.kind.value == "llm"
    assert w.execution.model_tier == exp["model_tier"]
    assert w.system_prompt_ref is not None
    assert w.system_prompt_ref.id == exp["prompt"]
    # skills / guardrails / caps
    assert [sb.skill_ref.id for sb in w.skills] == [exp["skill"]]
    assert tuple(g.id for g in w.guardrail_refs) == exp["guardrails"]
    assert tuple(c.id for c in w.capability_allowlist) == exp["caps"]
    # read categories / output / inputs
    assert w.read_categories == exp["read_categories"]
    assert [o.name for o in w.outputs] == ["primary"]
    assert w.outputs[0].schema_ref.name == exp["output"]
    got_inputs = tuple(
        (i.name, i.schema_ref.name, i.schema_ref.version, i.required, i.cardinality)
        for i in w.inputs)
    assert got_inputs == exp["inputs"]
    # tool policy vs allowlist invariants
    assert w.evidence_policy.tool_calls.value == exp["tool_calls"]
    if exp["tool_calls"] == "forbidden":
        assert w.capability_allowlist == ()
    if exp["tool_calls"] == "required":
        assert w.capability_allowlist
    # honesty / decision authority — no seat here can emit a decision
    assert w.can_emit_decision is False
    assert w.decision_authority == "none"
    assert [m.value for m in w.supported_modes] == ["online", "pit_replay"]
    # evidence policy pairs number provenance ON, unsourced OFF (never the
    # contradictory require=False + allow=False combo)
    assert w.evidence_policy.require_number_anchors is True
    assert w.evidence_policy.allow_unsourced_numbers is False


def test_allowlist_broadening_applied(specs):
    # text.news gains cap.data.fundamentals (promises ww_f10)
    assert "cap.data.fundamentals" in {c.id for c in specs["text.news"].capability_allowlist}
    # text.macro gains indicators + verified_snapshot (promises ww_macro_pulse/tape/overseas)
    macro = {c.id for c in specs["text.macro"].capability_allowlist}
    assert {"cap.data.indicators", "cap.data.verified_snapshot"} <= macro


@pytest.mark.parametrize("wid", sorted(EXPECTED))
def test_promise_supply_lint_clean(specs, cap_manifest, wid):
    skill_text = (_TREE / wid / "SKILL.md").read_text(encoding="utf-8")
    issues = lint_skill_supply(
        manifest=cap_manifest, skill_text=skill_text,
        capability_allowlist=specs[wid].capability_allowlist)
    assert issues == (), issues


# --------------------------------------------------------------------------- #
# clause (d) — pilot update touches EXACTLY skills + guardrail_refs             #
# --------------------------------------------------------------------------- #
def test_pilot_update_changes_only_two_fields(specs):
    base = next(w for w in p7.phase7_catalog_snapshot().workers if w.id == "text.sentiment")
    upd = specs["text.sentiment"]
    changed = [f for f in type(base).model_fields if getattr(base, f) != getattr(upd, f)]
    assert changed == ["skills", "guardrail_refs"]
    # skills rebind to the 交付物② install; guardrail adds anti_fabrication only
    assert base.skills[0].skill_ref.id == "skill.sentiment"
    assert upd.skills[0].skill_ref.id == "skill.text.sentiment"
    assert [g.id for g in base.guardrail_refs] == ["guard.provenance"]
    assert [g.id for g in upd.guardrail_refs] == ["guard.provenance", "guardrail.anti_fabrication"]
    # the update genuinely changes the semantic digest (a real update, not a no-op)
    assert upd.semantic_digest() != base.semantic_digest()


def test_pilot_update_keeps_forbidden_empty_allowlist(specs):
    upd = specs["text.sentiment"]
    assert upd.evidence_policy.tool_calls.value == "forbidden"
    assert upd.capability_allowlist == ()


# --------------------------------------------------------------------------- #
# 交付物② byte-verbatim install fidelity                                        #
# --------------------------------------------------------------------------- #
def _extract_deliverable2_bytes() -> bytes:
    """The 逐字安装件: the first ```markdown fenced block (+ its trailing newline)."""
    lines = _DELIVERABLE2.read_text(encoding="utf-8").split("\n")
    start = next(i + 1 for i, ln in enumerate(lines) if ln.strip() == "```markdown")
    end = next(j for j in range(start, len(lines)) if lines[j].strip() == "```")
    return ("\n".join(lines[start:end]) + "\n").encode("utf-8")


def test_text_sentiment_tree_is_deliverable2_byte_verbatim():
    # Compare after folding CRLF->LF on the tree side — exactly the normalization
    # catalog_material_digest applies before hashing — so the assertion is faithful
    # on both an LF checkout and an autocrlf=true (CRLF) working copy.
    tree_bytes = (_TREE / "text.sentiment" / "SKILL.md").read_bytes().replace(b"\r\n", b"\n")
    assert tree_bytes == _extract_deliverable2_bytes()


def test_text_sentiment_install_digest_matches_bound_skill(specs):
    tree_bytes = (_TREE / "text.sentiment" / "SKILL.md").read_bytes()
    from guanlan_v2.orchestration.catalog import ResolvedTextMaterial
    from guanlan_v2.orchestration.refs import ContentRef
    mat = ResolvedTextMaterial(
        ref=ContentRef(id="skill.text.sentiment", version="1", content_digest="0" * 64),
        kind="skill", raw_utf8=tree_bytes)
    d = catalog_material_digest(mat)
    assert specs["text.sentiment"].skills[0].skill_ref.content_digest == d


# --------------------------------------------------------------------------- #
# skill grammar + mirror byte identity                                        #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("wid", sorted(EXPECTED))
def test_skill_grammar_and_mirror_identity(wid):
    tree_bytes = (_TREE / wid / "SKILL.md").read_bytes()
    parse_skill_v1(tree_bytes.decode("utf-8"))            # raises on any deviation
    assert not tree_bytes.startswith(b"\xef\xbb\xbf")      # no BOM
    mirror = _MIRROR / f"{wid}.md"
    assert mirror.read_bytes() == tree_bytes               # mirror is a verbatim build product


def test_new_seats_data_source_priority_names_real_tools():
    # every DSP ww_ token a seat names is one an allowlisted capability supplies —
    # already machine-checked by lint; here we pin the key promised names exist.
    news = (_TREE / "text.news" / "SKILL.md").read_text(encoding="utf-8")
    assert "ww_news_live" in news and "ww_f10" in news
    macro = (_TREE / "text.macro" / "SKILL.md").read_text(encoding="utf-8")
    assert "ww_macro_pulse" in macro and "ww_overseas" in macro


# --------------------------------------------------------------------------- #
# payload schema matrices                                                      #
# --------------------------------------------------------------------------- #
_NOW = datetime(2026, 7, 20, 1, 30, tzinfo=_UTC)
_SYM = normalize_symbol("600519.SH")


def test_news_digest_report_matrix():
    item = lp.NewsDigestItem(headline="h", summary="s", source_label="src")
    r = lp.NewsDigestReport(as_of=_NOW, scope="both", items=(item,))
    assert r.schema_version == "1"
    # honest shortfall: empty items + a coverage_note is legal
    empty = lp.NewsDigestReport(as_of=_NOW, scope="market", items=(), coverage_note="feeds quiet")
    assert empty.items == ()
    # published_at / available_at are independently nullable
    assert item.published_at is None and item.available_at is None
    with pytest.raises(Exception):
        lp.NewsDigestReport(as_of=_NOW, scope="bogus", items=())
    with pytest.raises(Exception):
        item.headline = "x"  # frozen


def test_research_report_extract_matrix():
    claim = lp.ExtractedClaim(text="beats guidance", kind="fact", anchored=True)
    rr = lp.ResearchReportExtract(
        symbol=_SYM, as_of=_NOW, source_report_label="broker A",
        report_age_days=5, staleness_downweight=0.4, claims=(claim,))
    assert rr.schema_version == "1"
    assert rr.symbol.dotted == "600519.SH"
    # bounds on staleness_downweight
    for bad in (-0.1, 1.5):
        with pytest.raises(Exception):
            lp.ResearchReportExtract(
                symbol=_SYM, as_of=_NOW, source_report_label="x",
                report_age_days=0, staleness_downweight=bad, claims=())
    # empty claims is an honest empty extraction
    assert lp.ResearchReportExtract(
        symbol=_SYM, as_of=_NOW, source_report_label="x",
        report_age_days=0, staleness_downweight=1.0, claims=()).claims == ()
    with pytest.raises(Exception):
        lp.ExtractedClaim(text="x", kind="rumor", anchored=False)  # closed literal


def test_policy_report_matrix():
    entry = lp.PolicyEntry(title="t", summary="s", source_label="MOF")
    pr = lp.PolicyReport(as_of=_NOW, stance="supportive", entries=(entry,))
    assert pr.schema_version == "1"
    # unknown stance is a first-class honest outcome
    assert lp.PolicyReport(as_of=_NOW, stance="unknown", entries=()).stance == "unknown"
    assert entry.effective_hint is None
    with pytest.raises(Exception):
        lp.PolicyReport(as_of=_NOW, stance="dovish", entries=())  # closed literal


def test_macro_pulse_report_matrix():
    pm = lp.PredictionMarketRead(market_label="Fed cut", probability=0.62, direction_hint="cut")
    mp = lp.MacroPulseReport(
        as_of=_NOW, prediction_markets=(pm,), board_temp=None,
        degradation=("overseas snapshot unavailable",), narrative="mixed")
    assert mp.schema_version == "1"
    # board_temp nullable (UNAVAILABLE 不补零); degradation is the honest channel
    assert mp.board_temp is None and mp.degradation
    for bad in (-0.01, 1.01, float("inf")):
        with pytest.raises(Exception):
            lp.PredictionMarketRead(market_label="m", probability=bad, direction_hint="up")


def test_lane_c_public_models_partition():
    assert set(lp.LANE_C_PUBLIC_MODELS) == {
        lp.NewsDigestItem, lp.NewsDigestReport, lp.ExtractedClaim, lp.ResearchReportExtract,
        lp.PolicyEntry, lp.PolicyReport, lp.PredictionMarketRead, lp.MacroPulseReport}


# --------------------------------------------------------------------------- #
# legacy-sentiment adapter regression (all UNMAPPABLE, reverses exact)         #
# incl. the two Task-0-review carry vectors SRC_PULSE_TEMP / SRC_MARKET_TEMP_GATE
# --------------------------------------------------------------------------- #
def test_sentiment_sources_unmappable_and_reverse_exact():
    cases = [
        # brief batch rows
        (M.migrate_sentiment("利好", source_schema=M.SRC_NEWS_SENTIMENT), "利好"),
        (M.migrate_sentiment(50.0, source_schema=M.SRC_ASTOCK_TEMP), 50.0),
        (M.migrate_sentiment(50.0, source_schema=M.SRC_PULSE_TEMP), 50.0),
        # Task-0-review carry: the two sentiment vectors missing from the handoff gate's
        # 3/5 coverage — SRC_PULSE_TEMP (also above) + SRC_MARKET_TEMP_GATE
        (M.migrate_sentiment("risk_off", source_schema=M.SRC_MARKET_TEMP_GATE), "risk_off"),
    ]
    for mig, raw in cases:
        assert mig.mapping_status is MappingStatus.UNMAPPABLE
        assert mig.normalized is None
        assert mig.mapping_basis == "none"
        rev = M.sentiment_to_legacy(mig)
        assert rev == raw and type(rev) is type(raw)


def test_carry_vectors_are_the_two_gate_missing_sources():
    # the handoff gate covers SRC_TAG_SCORE / SRC_ASTOCK_TEMP / SRC_NEWS_SENTIMENT (3/5);
    # this batch completes the 5/5 with the two carry vectors below.
    carry = (M.SRC_PULSE_TEMP, M.SRC_MARKET_TEMP_GATE)
    assert M.migrate_sentiment(50.0, source_schema=carry[0]).mapping_status is MappingStatus.UNMAPPABLE
    assert M.migrate_sentiment("neutral", source_schema=carry[1]).mapping_status is MappingStatus.UNMAPPABLE
