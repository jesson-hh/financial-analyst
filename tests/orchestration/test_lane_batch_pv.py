# -*- coding: utf-8 -*-
"""Phase 8 · Task 5 — Batch 2 · Lane B 量价几何 (3 producers + #27 pv.curator).

Covers (per brief Step 1):
* the WorkerSpec matrix per roster row (lane/tier/exec-kind/model_tier/tool policy/
  allowlist/guardrails/read_categories/output/inputs/decision authority), field-for-field;
* the deterministic invariants (``handler_ref`` present, no ``model_tier``/tier) and the
  single-LLM ``pv.curator`` proposal-only red lines (FORBIDDEN⇔empty allowlist,
  ``can_emit_decision=False``, critic tier, output = a ``draft_only`` proposal);
* the Task-0b-review allowlist broadening (``pv.microstructure`` promises
  ww_orderbook/ww_ticks/ww_fundflow ⇒ gains cap.data.verified_snapshot + cap.data.indicators)
  so every shipped seat's ``lint_skill_supply == ()``;
* the pattern-dictionary binding: the three pattern-consuming seats bind the Task-4b
  ``guardrail.pattern_dictionary`` ContentRef (digest ``3f24f8a1…``);
* skill-v1 grammar (real ``parse_skill_v1``) + mirror byte identity for all four;
* the Lane B payload schema matrices (bounds, ≤8-indicator validator, degradation
  honesty, ``draft_only`` Literal-True-only, non-empty ``trigger_evidence``, the
  numeric ``pa-15key-v1`` feature-set registry);
* the 15-key bitwise equality of ``handler.pv.price_action`` with ``compute_pa_features``
  and the ``pv.microstructure`` orderbook-空档降级 handler honesty;
* the classify_worker cross-test (pv.technical REQUIRED + zero calls ⇒ incomplete);
* the legacy adapter-absence regression (no scalar adapter invented for this batch's
  legacy schemas) + the fixture's whale-analyst → pv.microstructure design-intent pin.
"""
from __future__ import annotations

import importlib.util
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import pytest

from guanlan_v2.orchestration import lane_catalog as lc
from guanlan_v2.orchestration import lane_payloads as lp
from guanlan_v2.orchestration import migration as M
from guanlan_v2.orchestration.capability_manifest import (
    MATERIAL_PATH as CAP_MANIFEST_PATH,
    lint_skill_supply,
    parse_capability_manifest,
)
from guanlan_v2.orchestration.catalog import (
    EvidencePolicy,
    ExecutionSpec,
    InputBinding,
    OutputBinding,
    WorkerSpec,
    parse_skill_v1,
)
from guanlan_v2.orchestration.enums import (
    DataMode,
    ExecutionKind,
    MappingStatus,
    NodeStatus,
    Tier,
    ToolCallRequirement,
)
from guanlan_v2.orchestration.honesty import classify_worker
from guanlan_v2.orchestration.pattern_registry import PatternDefinition
from guanlan_v2.orchestration.refs import SchemaRef
from guanlan_v2.orchestration.schemas import Artifact, NodeRun, Provenance
from guanlan_v2.orchestration.data.symbols import normalize_symbol
from guanlan_v2.seats.price_action import compute_pa_features

_REPO = Path(__file__).resolve().parents[2]
_TREE = _REPO / "guanlan_v2" / "orchestration" / "skills"
_MIRROR = _REPO / "config" / "orchestration" / "materials" / "skills"
_HANDLERS = _REPO / "config" / "orchestration" / "materials" / "handlers"
_UTC = timezone.utc

#: the Task-4b pattern-dictionary catalog material identity (frozen).
_PATTERN_DICT_MATERIAL_ID = "guardrail.pattern_dictionary"
_PATTERN_DICT_DIGEST = "3f24f8a10e76fa6c1176b8849a335d2cdb97f94ad0c86a663c21f971fbb7cddd"


# --------------------------------------------------------------------------- #
# fixtures                                                                     #
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="module")
def specs() -> dict:
    materials = lc.load_pv_lane_materials()
    return {w.id: w for w in lc.build_pv_worker_specs(materials=materials)}


@pytest.fixture(scope="module")
def cap_manifest():
    return parse_capability_manifest(CAP_MANIFEST_PATH.read_bytes())


def _load_handler_module(name: str):
    """Load a handler material .py (dotted stem) by file path, as a fresh module."""
    path = _HANDLERS / f"{name}.py"
    spec = importlib.util.spec_from_file_location(
        name.replace(".", "_") + "_handler", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def _fixture_bar_frame(n: int = 30) -> pd.DataFrame:
    """A deterministic n-bar OHLCV frame with enough history for every PA key."""
    rows = []
    px = 10.0
    for k in range(n):
        px = px * (1.0 + (0.01 if k % 2 == 0 else -0.006))
        o = px
        c = px * 1.004
        h = max(o, c) * 1.006
        lo = min(o, c) * 0.994
        rows.append({"open": o, "high": h, "low": lo, "close": c,
                     "vol": 1000.0 + 10.0 * k, "trade_date": 20260101 + k})
    return pd.DataFrame(rows)


# --------------------------------------------------------------------------- #
# roster expectation (the reviewed field-for-field matrix)                     #
# --------------------------------------------------------------------------- #
EXPECTED = {
    "pv.price_action": dict(
        kind="deterministic", tier="reader", model_tier=None, tool_calls="optional",
        caps=("cap.data.ohlcv",),
        guardrails=("guardrail.number_provenance", "guardrail.pattern_dictionary"),
        handler="handler.pv.price_action", skill="skill.pv.price_action", prompt=None,
        output="PriceActionFeatureReport", read_categories=("market_data",),
        inputs=(), persona="Price-action geometry reader"),
    "pv.technical": dict(
        kind="llm", tier="reader", model_tier="reasoner", tool_calls="required",
        caps=("cap.data.indicators", "cap.data.verified_snapshot"),
        guardrails=("guardrail.number_provenance", "guardrail.pattern_dictionary",
                    "guardrail.untrusted_input_isolation"),
        handler=None, skill="skill.pv.technical", prompt="prompt.pv.technical",
        output="TechnicalReport", read_categories=("context", "market_data"),
        inputs=(("price_action", "PriceActionFeatureReport", "1", False, "one"),),
        persona="Technical indicator reader"),
    "pv.microstructure": dict(
        kind="deterministic", tier="reader", model_tier=None, tool_calls="optional",
        caps=("cap.data.indicators", "cap.data.signals", "cap.data.verified_snapshot"),
        guardrails=("guardrail.number_provenance",),
        handler="handler.pv.microstructure", skill="skill.pv.microstructure", prompt=None,
        output="MicrostructureReport", read_categories=("market_data",),
        inputs=(), persona="Microstructure projection reader"),
    "pv.curator": dict(
        kind="llm", tier="critic", model_tier="reasoner", tool_calls="forbidden",
        caps=(),
        guardrails=("guardrail.draft_only_advisory", "guardrail.external_ta_ingest",
                    "guardrail.number_provenance", "guardrail.pattern_dictionary",
                    "guardrail.revision_throttle", "guardrail.untrusted_input_isolation"),
        handler=None, skill="skill.pv.curator", prompt="prompt.pv.curator",
        output="PatternLifecycleProposal", read_categories=("context", "upstream_artifacts"),
        inputs=(("price_action", "PriceActionFeatureReport", "1", False, "one"),
                ("technical", "TechnicalReport", "1", False, "one")),
        persona="Offline K-line pattern curator (#27, AMEND-6) — draft-only, zero trading authority"),
}


def test_all_four_seats_present(specs):
    assert set(specs) == set(EXPECTED) == set(lc.PV_LANE_WORKER_IDS)


@pytest.mark.parametrize("wid", sorted(EXPECTED))
def test_worker_spec_matrix(specs, wid):
    w = specs[wid]
    exp = EXPECTED[wid]
    # identity / role
    assert w.catalog_role == "final"
    assert w.selection_scope == "dynamic_allowed"
    assert w.compatibility is None
    assert w.lane == "pv"
    assert w.persona == exp["persona"]
    assert w.tier.value == exp["tier"]
    # execution matrix (deterministic ⇒ handler_ref, no tier; LLM ⇒ prompt + tier)
    assert w.execution.kind.value == exp["kind"]
    assert w.execution.model_tier == exp["model_tier"]
    if exp["kind"] == "deterministic":
        assert w.execution.handler_ref is not None
        assert w.execution.handler_ref.id == exp["handler"]
        assert w.execution.model_tier is None
        assert w.execution.thinking_budget is None
        assert w.system_prompt_ref is None
    else:
        assert w.execution.handler_ref is None
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
    # every seat here is a reader/critic — none may emit a decision
    assert w.can_emit_decision is False
    assert w.decision_authority == "none"
    assert [m.value for m in w.supported_modes] == ["online", "pit_replay"]
    # evidence policy: number provenance ON, unsourced OFF (never the contradictory combo)
    assert w.evidence_policy.require_number_anchors is True
    assert w.evidence_policy.allow_unsourced_numbers is False


def test_deterministic_specs_carry_handler_and_no_tier(specs):
    for wid in ("pv.price_action", "pv.microstructure"):
        w = specs[wid]
        assert w.execution.kind is ExecutionKind.DETERMINISTIC
        assert w.execution.handler_ref is not None and w.execution.model_tier is None


def test_curator_is_the_only_llm_and_proposal_only(specs):
    llm = [wid for wid, w in specs.items()
           if w.execution.kind is ExecutionKind.LLM]
    assert set(llm) == {"pv.technical", "pv.curator"}
    cur = specs["pv.curator"]
    # #27 proposal-only red lines
    assert cur.tier is Tier.CRITIC
    assert cur.evidence_policy.tool_calls is ToolCallRequirement.FORBIDDEN
    assert cur.capability_allowlist == ()          # FORBIDDEN ⇔ empty allowlist (no write capability)
    assert cur.can_emit_decision is False
    assert cur.decision_authority == "none"
    assert cur.outputs[0].schema_ref.name == "PatternLifecycleProposal"
    assert cur.system_prompt_ref is not None and cur.execution.model_tier == "reasoner"


def test_allowlist_broadening_applied(specs):
    # Task-0b review: pv.microstructure promises ww_orderbook/ww_ticks (verified_snapshot)
    # + ww_fundflow (indicators), so its allowlist is broadened beyond the get_signal
    # shorthand to include the SUPPLYING capabilities.
    micro = {c.id for c in specs["pv.microstructure"].capability_allowlist}
    assert {"cap.data.verified_snapshot", "cap.data.indicators"} <= micro
    assert "cap.data.signals" in micro          # the plan shorthand is retained


@pytest.mark.parametrize("wid", sorted(EXPECTED))
def test_promise_supply_lint_clean(specs, cap_manifest, wid):
    skill_text = (_TREE / wid / "SKILL.md").read_text(encoding="utf-8")
    issues = lint_skill_supply(
        manifest=cap_manifest, skill_text=skill_text,
        capability_allowlist=specs[wid].capability_allowlist)
    assert issues == (), issues


# --------------------------------------------------------------------------- #
# pattern-dictionary binding                                                   #
# --------------------------------------------------------------------------- #
def test_pattern_consuming_seats_bind_the_task4b_dictionary(specs):
    for wid in ("pv.price_action", "pv.technical", "pv.curator"):
        refs = {g.id: g for g in specs[wid].guardrail_refs}
        assert _PATTERN_DICT_MATERIAL_ID in refs, wid
        assert refs[_PATTERN_DICT_MATERIAL_ID].content_digest == _PATTERN_DICT_DIGEST
    # microstructure is NOT a pattern consumer
    assert _PATTERN_DICT_MATERIAL_ID not in {
        g.id for g in specs["pv.microstructure"].guardrail_refs}


# --------------------------------------------------------------------------- #
# skill grammar + mirror byte identity                                         #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("wid", sorted(EXPECTED))
def test_skill_grammar_and_mirror_identity(wid):
    tree_bytes = (_TREE / wid / "SKILL.md").read_bytes()
    parse_skill_v1(tree_bytes.decode("utf-8"))            # raises on any deviation
    assert not tree_bytes.startswith(b"\xef\xbb\xbf")      # no BOM
    mirror = _MIRROR / f"{wid}.md"
    assert mirror.read_bytes() == tree_bytes               # mirror is a verbatim build product


def test_curator_skill_names_no_ww_tool_and_states_untrusted_ingest():
    # FORBIDDEN ⇒ empty allowlist ⇒ its Data Source Priority must name zero ww_ tools,
    # and its untrusted-external-feed doctrine must be present.
    text = (_TREE / "pv.curator" / "SKILL.md").read_text(encoding="utf-8")
    assert "ww_" not in text.split("## ⚠️", 1)[1].split("## ", 1)[0]  # no ww_ in the DSP block
    assert "untrusted" in text.lower()


# --------------------------------------------------------------------------- #
# payload schema matrices                                                      #
# --------------------------------------------------------------------------- #
_NOW = datetime(2026, 7, 20, 1, 30, tzinfo=_UTC)
_SYM = normalize_symbol("600519.SH")
_D64 = "a" * 64


def test_price_action_feature_report_matrix():
    keys = lp.PA_FEATURE_SET_KEYS["pa-15key-v1"]
    feats = {k: float(i) for i, k in enumerate(keys)}
    r = lp.PriceActionFeatureReport(
        symbol=_SYM, as_of=_NOW, feature_set_version="pa-15key-v1", features=feats)
    assert r.schema_version == "1"
    assert r.patterns == () and r.methodology_ref is None
    # unknown feature-set version is rejected
    with pytest.raises(Exception):
        lp.PriceActionFeatureReport(
            symbol=_SYM, as_of=_NOW, feature_set_version="bogus", features=feats)
    # features must be EXACTLY the registry keys (missing / extra ⇒ reject)
    with pytest.raises(Exception):
        lp.PriceActionFeatureReport(
            symbol=_SYM, as_of=_NOW, feature_set_version="pa-15key-v1",
            features={k: 0.0 for k in keys[:-1]})
    with pytest.raises(Exception):
        lp.PriceActionFeatureReport(
            symbol=_SYM, as_of=_NOW, feature_set_version="pa-15key-v1",
            features={**feats, "ghost": 1.0})
    # a pattern hit must be pattern_id@definition_version — never a bare id
    ok = lp.PriceActionFeatureReport(
        symbol=_SYM, as_of=_NOW, feature_set_version="pa-15key-v1", features=feats,
        patterns=("pv.single.hammer@1",))
    assert ok.patterns == ("pv.single.hammer@1",)
    with pytest.raises(Exception):
        lp.PriceActionFeatureReport(
            symbol=_SYM, as_of=_NOW, feature_set_version="pa-15key-v1", features=feats,
            patterns=("hammer",))


def test_indicator_reading_and_technical_report_matrix():
    ind = lp.IndicatorReading(name="RSI14", value=71.2, note="overbought")
    tr = lp.TechnicalReport(
        symbol=_SYM, as_of=_NOW, indicators=(ind,), verified_anchor_digest=_D64,
        bias="bullish", summary="momentum extended vs the verified quote anchor")
    assert tr.schema_version == "1" and tr.bias == "bullish"
    # ≤8 complementary indicators, unique names
    many = tuple(lp.IndicatorReading(name=f"I{i}", value=float(i)) for i in range(8))
    assert lp.TechnicalReport(
        symbol=_SYM, as_of=_NOW, indicators=many, verified_anchor_digest=None,
        bias="neutral", summary="flat").indicators == many
    with pytest.raises(Exception):    # 0 indicators
        lp.TechnicalReport(symbol=_SYM, as_of=_NOW, indicators=(),
                           verified_anchor_digest=None, bias="unknown", summary="x")
    with pytest.raises(Exception):    # 9 indicators
        lp.TechnicalReport(
            symbol=_SYM, as_of=_NOW,
            indicators=tuple(lp.IndicatorReading(name=f"J{i}", value=float(i)) for i in range(9)),
            verified_anchor_digest=None, bias="neutral", summary="x")
    with pytest.raises(Exception):    # duplicate names
        lp.TechnicalReport(
            symbol=_SYM, as_of=_NOW,
            indicators=(lp.IndicatorReading(name="D", value=1.0),
                        lp.IndicatorReading(name="D", value=2.0)),
            verified_anchor_digest=None, bias="neutral", summary="x")
    with pytest.raises(Exception):    # closed bias literal
        lp.TechnicalReport(symbol=_SYM, as_of=_NOW, indicators=(ind,),
                           verified_anchor_digest=None, bias="mooning", summary="x")


def test_microstructure_report_matrix():
    # all four metrics nullable; degradation is the honest shortfall channel
    m = lp.MicrostructureReport(
        symbol=_SYM, as_of=_NOW, l1_spread_bp=None, bid_ask_imbalance=None,
        break_ratio=None, whale_net_inflow=None,
        degradation=("l1 order book unavailable", "tape unavailable"),
        narrative="all optional feeds down; nothing imputed")
    assert m.l1_spread_bp is None and m.degradation
    full = lp.MicrostructureReport(
        symbol=_SYM, as_of=_NOW, l1_spread_bp=3.5, bid_ask_imbalance=-0.2,
        break_ratio=0.1, whale_net_inflow=-3.2e8, narrative="present")
    assert full.degradation == ()
    for bad in (float("inf"), float("nan")):
        with pytest.raises(Exception):
            lp.MicrostructureReport(symbol=_SYM, as_of=_NOW, l1_spread_bp=bad,
                                    bid_ask_imbalance=None, break_ratio=None,
                                    whale_net_inflow=None, narrative="x")


def test_pattern_lifecycle_proposal_red_lines():
    defn = PatternDefinition(
        pattern_id="pv.single.newbar", definition_version="1", family="single_bar",
        display_name="示例", predicate="示例判定规则", geometry_inputs=("body", "close_pos"),
        rule_params={"max_body_frac": 0.3}, approximate=False)
    p = lp.PatternLifecycleProposal(
        as_of=_NOW, kind="pattern_definition", pattern_id="pv.single.newbar",
        proposed_definition=defn, trigger_evidence=("历史日线回放命中率显著",))
    assert p.draft_only is True and p.kind == "pattern_definition"
    # draft_only can NEVER be constructed False (Literal[True])
    with pytest.raises(Exception):
        lp.PatternLifecycleProposal(
            as_of=_NOW, kind="skill_diff", skill_diff_summary="收紧锤子线上影阈值",
            trigger_evidence=("x",), draft_only=False)
    # trigger_evidence must be non-empty (不许"顺手优化")
    with pytest.raises(Exception):
        lp.PatternLifecycleProposal(
            as_of=_NOW, kind="skill_diff", skill_diff_summary="x", trigger_evidence=())
    # a skill_diff proposal from an external feed labels its source author
    sd = lp.PatternLifecycleProposal(
        as_of=_NOW, kind="skill_diff", skill_diff_summary="补充旗形回撤上限",
        source_label="某公众号作者 X", trigger_evidence=("外部投喂,作者已标注",))
    assert sd.source_label == "某公众号作者 X"
    # kind-consistency: a pattern_definition proposal requires a proposed_definition
    with pytest.raises(Exception):
        lp.PatternLifecycleProposal(
            as_of=_NOW, kind="pattern_definition", pattern_id="pv.single.x",
            trigger_evidence=("x",))
    # closed kind literal
    with pytest.raises(Exception):
        lp.PatternLifecycleProposal(as_of=_NOW, kind="rewrite", trigger_evidence=("x",))


def test_lane_b_public_models_partition():
    assert set(lp.LANE_B_PUBLIC_MODELS) == {
        lp.PriceActionFeatureReport, lp.IndicatorReading, lp.TechnicalReport,
        lp.MicrostructureReport, lp.PatternLifecycleProposal}


# --------------------------------------------------------------------------- #
# handler.pv.price_action — 15-key bitwise equality with compute_pa_features    #
# --------------------------------------------------------------------------- #
def test_price_action_handler_reproduces_compute_pa_features_bitwise():
    handler = _load_handler_module("pv.price_action")
    df = _fixture_bar_frame()
    got = handler.compute_geometry(df, code="600519", name="贵州茅台")
    ref = compute_pa_features(df, code="600519", name="贵州茅台")
    assert got == ref                                     # 15-key bitwise identity
    assert set(ref) >= set(lp.PA_FEATURE_SET_KEYS["pa-15key-v1"]) - {"inside_streak"}
    assert handler.FEATURE_SET_VERSION == "pa-15key-v1"


def test_price_action_handler_numeric_projection_is_exactly_registry_keys():
    handler = _load_handler_module("pv.price_action")
    geom = handler.compute_geometry(_fixture_bar_frame(), code="600519")
    feats = handler.numeric_features(geom)
    assert set(feats) == set(lp.PA_FEATURE_SET_KEYS["pa-15key-v1"])
    # the projection builds a valid, exactly-keyed report (no fabrication of categoricals)
    rep = lp.PriceActionFeatureReport(
        symbol=_SYM, as_of=_NOW, feature_set_version=handler.FEATURE_SET_VERSION,
        features=feats)
    assert set(rep.features) == set(lp.PA_FEATURE_SET_KEYS["pa-15key-v1"])


# --------------------------------------------------------------------------- #
# handler.pv.microstructure — orderbook 空档降级 honesty                        #
# --------------------------------------------------------------------------- #
def test_microstructure_handler_degrades_honestly_for_absent_feeds():
    handler = _load_handler_module("pv.microstructure")
    # every feed absent ⇒ every metric None + a degradation row per feed, nothing imputed
    out = handler.project(l1_book=None, ticks=None, tape=None)
    assert out["l1_spread_bp"] is None and out["bid_ask_imbalance"] is None
    assert out["break_ratio"] is None and out["whale_net_inflow"] is None
    assert len(out["degradation"]) >= 1
    rep = lp.MicrostructureReport(
        symbol=_SYM, as_of=_NOW, narrative="degraded", **out)
    assert rep.degradation and rep.l1_spread_bp is None
    # a present L1 book yields real spread/imbalance and NO l1 degradation row
    book = {"bid": 9.98, "ask": 10.02, "bid_vol": 1200.0, "ask_vol": 800.0}
    out2 = handler.project(l1_book=book, ticks=None, tape=None)
    assert out2["l1_spread_bp"] is not None and out2["bid_ask_imbalance"] is not None
    assert not any("order book" in d.lower() for d in out2["degradation"])  # l1 present ⇒ no l1 row
    assert out2["bid_ask_imbalance"] == pytest.approx(0.2)  # (1200-800)/2000


# --------------------------------------------------------------------------- #
# classify_worker cross-test: pv.technical REQUIRED + zero calls ⇒ incomplete   #
# --------------------------------------------------------------------------- #
def _min_node_run(worker_id: str) -> NodeRun:
    return NodeRun(
        node_run_id="nr-1", run_id="run-1", plan_id="plan-1", plan_digest=_D64,
        node_id="pv", worker_id=worker_id, status=NodeStatus.COMPLETED,
        attempt_id="att-1", input_snapshot_digest="1" * 64, tool_call_records=(),
        output_keys=("primary",), output_artifact_ids=("art-1",))


def _min_artifact() -> Artifact:
    return Artifact.build(
        artifact_id="art-1", run_id="run-1", created_at=_NOW,
        producer_node_id="pv", slot="pv", output_key="primary", kind="technical_report",
        payload_schema_ref=SchemaRef(name="TechnicalReport", version="1"),
        payload={"note": "ok"}, rendered_md="# tech", input_refs=(),
        provenance=Provenance(plan_digest=_D64, code_version="git:abc",
                              as_of=_NOW, pit_mode=DataMode.ONLINE),
        numbers=(), badges=())


def test_pv_technical_zero_call_run_classifies_incomplete(specs):
    rep = classify_worker(
        worker=specs["pv.technical"], node_run=_min_node_run("pv.technical"),
        artifact=_min_artifact())
    assert rep.verdict == "incomplete"
    assert "required_tools_zero_calls" in {i.code for i in rep.issues}


# --------------------------------------------------------------------------- #
# legacy adapter-absence regression + whale-analyst → pv.microstructure pin     #
# --------------------------------------------------------------------------- #
def test_no_scalar_adapter_invented_for_this_batch():
    # none of the batch's legacy schemas (price_action / technical_analyst / whale_analyst
    # / live_book) has a Phase-1 scalar migrate_* adapter — assert exactly that.
    src_keys = [getattr(M, n).key for n in dir(M)
                if n.startswith("SRC_") and isinstance(getattr(M, n), SchemaRef)]
    for legacy in ("price_action", "technical_analyst", "whale_analyst", "live_book"):
        assert not any(legacy in k for k in src_keys), legacy


def test_whale_analyst_is_the_reviewed_fixture_node_unmapped():
    # the design intent is whale-analyst (legacy) → pv.microstructure (this batch); in the
    # Phase-1 fixture that node is a reviewed member that maps UNMAPPABLE (no runnable
    # adapter bridges it — pin the design-intent row, invent no adapter).
    assert "whale-analyst" in M._STOCK_DEEP_DIVE_NODE_IDS
    mapping = M.migrate_legacy_graph(
        {"nodes": {"whale-analyst": {}}}, source_schema=M.SRC_STOCK_DEEP_DIVE,
        source_format="json")
    wm = {m.source_node_id: m for m in mapping.worker_mappings}["whale-analyst"]
    assert wm.mapping_status is MappingStatus.UNMAPPABLE
    assert wm.target_worker_id is None
