# -*- coding: utf-8 -*-
"""Phase 8 · Task 6 — Batch 3 · Lane A 量化 (5 deterministic producers + #26 quant.curator).

Covers (per brief Step 1):
* the WorkerSpec matrix per roster row (lane/tier/exec-kind/model_tier/tool policy/
  allowlist/guardrails/read_categories/output/inputs/decision authority), field-for-field;
* the deterministic invariants (``handler_ref`` present, no ``model_tier``/tier) for the
  5 producers and the single-LLM ``quant.curator`` proposal-only red lines
  (FORBIDDEN⇔empty allowlist, ``can_emit_decision=False``, critic tier, output = a
  ``draft_only`` proposal), mirroring #27 ``pv.curator`` from Task 5;
* the promise/supply lint clean (``lint_skill_supply == ()``) for every shipped seat —
  no Lane A broadening is needed (get_signal / get_fundamentals shorthands supply the
  promised tools);
* skill-v1 grammar (real ``parse_skill_v1``) + mirror byte identity for all six;
* the ``quant.curator`` DSP names zero ww_ tools (FORBIDDEN⇔empty allowlist);
* the Lane A payload schema matrices (回看-not-OOS honesty on ``FactorICRow.oos``, ranks
  unique/ascending, ``stale_days`` DL 断供显形, PBO∈[0,1], nullable-verdict honesty,
  ``MinedFactorDraft.draft_only`` + ``FactorLifecycleProposal.draft_only`` Literal-True-only,
  non-empty ``trigger_evidence``, ``proposed_expr`` revision_draft-only);
* the 5 deterministic handlers: import-safe (stdlib-only) + delegation fidelity per clause
  (h) — each consumes its legacy source's TYPED OUTPUT (the legacy compute path is not an
  import-safe pure function: it needs the engine loader / artifact-parquet IO / a full
  research-loop orchestration) and projects deterministically with the honesty rails
  (oos never flipped True, stale_days never zeroed, absent verdict stays None,
  passed_gate never upgraded);
* the classify_worker cross-test (a FORBIDDEN offline seat with zero tool calls is NOT
  killed — 合法无工具 worker 不因零调用被误杀);
* the legacy adapter regression (``market_cycle@1#stage`` via ``migrate_rotation_stage`` +
  the introspector confidence scalars via ``migrate_confidence``) replays with frozen
  verdicts, reverses exact — no adapter re-mapped.
"""
from __future__ import annotations

import ast
import importlib.util
from datetime import datetime, timezone
from pathlib import Path

import pytest

from guanlan_v2.orchestration import lane_catalog as lc
from guanlan_v2.orchestration import lane_payloads as lp
from guanlan_v2.orchestration import migration as M
from guanlan_v2.orchestration.capability_manifest import (
    MATERIAL_PATH as CAP_MANIFEST_PATH,
    lint_skill_supply,
    parse_capability_manifest,
)
from guanlan_v2.orchestration.catalog import parse_skill_v1
from guanlan_v2.orchestration.enums import (
    DataMode,
    ExecutionKind,
    MappingStatus,
    NodeStatus,
    Tier,
    ToolCallRequirement,
)
from guanlan_v2.orchestration.honesty import classify_worker
from guanlan_v2.orchestration.refs import SchemaRef
from guanlan_v2.orchestration.schemas import Artifact, NodeRun, Provenance
from guanlan_v2.orchestration.data.symbols import normalize_symbol

_REPO = Path(__file__).resolve().parents[2]
_TREE = _REPO / "guanlan_v2" / "orchestration" / "skills"
_MIRROR = _REPO / "config" / "orchestration" / "materials" / "skills"
_HANDLERS = _REPO / "config" / "orchestration" / "materials" / "handlers"
_UTC = timezone.utc


# --------------------------------------------------------------------------- #
# fixtures                                                                     #
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="module")
def specs() -> dict:
    materials = lc.load_quant_lane_materials()
    return {w.id: w for w in lc.build_quant_worker_specs(materials=materials)}


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


# --------------------------------------------------------------------------- #
# roster expectation (the reviewed field-for-field matrix)                     #
# --------------------------------------------------------------------------- #
EXPECTED = {
    "quant.factor": dict(
        kind="deterministic", tier="reader", model_tier=None, tool_calls="optional",
        caps=("cap.data.signals",),
        guardrails=("guardrail.number_provenance",),
        handler="handler.quant.factor", skill="skill.quant.factor", prompt=None,
        output="FactorICReport", read_categories=("market_data",),
        inputs=(), persona="Factor IC reader"),
    "quant.model": dict(
        kind="deterministic", tier="reader", model_tier=None, tool_calls="optional",
        caps=("cap.data.signals",),
        guardrails=("guardrail.number_provenance",),
        handler="handler.quant.model", skill="skill.quant.model", prompt=None,
        output="ModelPredictionReport", read_categories=("market_data",),
        inputs=(), persona="Model prediction reader"),
    "quant.backtest": dict(
        kind="deterministic", tier="reader", model_tier=None, tool_calls="forbidden",
        caps=(),
        guardrails=("guardrail.number_provenance",),
        handler="handler.quant.backtest", skill="skill.quant.backtest", prompt=None,
        output="BacktestEvidenceReport", read_categories=("upstream_artifacts",),
        inputs=(("factor_ic", "FactorICReport", "1", True, "one"),
                ("model_predictions", "ModelPredictionReport", "1", False, "one")),
        persona="Backtest evidence reader"),
    "quant.fundamentals": dict(
        kind="deterministic", tier="reader", model_tier=None, tool_calls="optional",
        caps=("cap.data.fundamentals",),
        guardrails=("guardrail.number_provenance",),
        handler="handler.quant.fundamentals", skill="skill.quant.fundamentals", prompt=None,
        output="FundamentalsReport", read_categories=("market_data",),
        inputs=(), persona="Fundamentals reader"),
    "quant.factor_miner": dict(
        kind="deterministic", tier="reader", model_tier=None, tool_calls="forbidden",
        caps=(),
        guardrails=("guardrail.draft_only_advisory", "guardrail.number_provenance"),
        handler="handler.quant.factor_miner", skill="skill.quant.factor_miner", prompt=None,
        output="MinedFactorDraft", read_categories=("upstream_artifacts",),
        inputs=(("factor_ic", "FactorICReport", "1", False, "one"),),
        persona="Mined-factor draft producer"),
    "quant.curator": dict(
        kind="llm", tier="critic", model_tier="reasoner", tool_calls="forbidden",
        caps=(),
        guardrails=("guardrail.draft_only_advisory", "guardrail.number_provenance",
                    "guardrail.revision_throttle"),
        handler=None, skill="skill.quant.curator", prompt="prompt.quant.curator",
        output="FactorLifecycleProposal", read_categories=("context", "upstream_artifacts"),
        inputs=(("factor_ic", "FactorICReport", "1", False, "one"),
                ("backtest_evidence", "BacktestEvidenceReport", "1", False, "one")),
        persona="Offline factor curator (#26, AMEND-5) — draft-only, zero trading authority"),
}


def test_all_six_seats_present(specs):
    assert set(specs) == set(EXPECTED) == set(lc.QUANT_LANE_WORKER_IDS)


@pytest.mark.parametrize("wid", sorted(EXPECTED))
def test_worker_spec_matrix(specs, wid):
    w = specs[wid]
    exp = EXPECTED[wid]
    # identity / role
    assert w.catalog_role == "final"
    assert w.selection_scope == "dynamic_allowed"
    assert w.compatibility is None
    assert w.lane == "quant"
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
    assert w.evidence_policy.require_input_refs is True


def test_five_producers_deterministic_carry_handler_and_no_tier(specs):
    for wid in ("quant.factor", "quant.model", "quant.backtest",
                "quant.fundamentals", "quant.factor_miner"):
        w = specs[wid]
        assert w.execution.kind is ExecutionKind.DETERMINISTIC
        assert w.execution.handler_ref is not None and w.execution.model_tier is None
        assert w.system_prompt_ref is None


def test_curator_is_the_only_llm_and_proposal_only(specs):
    llm = [wid for wid, w in specs.items() if w.execution.kind is ExecutionKind.LLM]
    assert llm == ["quant.curator"]
    cur = specs["quant.curator"]
    # #26 proposal-only red lines (mirrors #27 pv.curator)
    assert cur.tier is Tier.CRITIC
    assert cur.evidence_policy.tool_calls is ToolCallRequirement.FORBIDDEN
    assert cur.capability_allowlist == ()          # FORBIDDEN ⇔ empty allowlist (no write capability)
    assert cur.can_emit_decision is False
    assert cur.decision_authority == "none"
    assert cur.outputs[0].schema_ref.name == "FactorLifecycleProposal"
    assert cur.system_prompt_ref is not None and cur.execution.model_tier == "reasoner"


def test_forbidden_seats_have_empty_allowlist(specs):
    for wid in ("quant.backtest", "quant.factor_miner", "quant.curator"):
        w = specs[wid]
        assert w.evidence_policy.tool_calls is ToolCallRequirement.FORBIDDEN
        assert w.capability_allowlist == ()


@pytest.mark.parametrize("wid", sorted(EXPECTED))
def test_promise_supply_lint_clean(specs, cap_manifest, wid):
    skill_text = (_TREE / wid / "SKILL.md").read_text(encoding="utf-8")
    issues = lint_skill_supply(
        manifest=cap_manifest, skill_text=skill_text,
        capability_allowlist=specs[wid].capability_allowlist)
    assert issues == (), issues


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


def test_curator_skill_names_no_ww_tool_in_dsp():
    # FORBIDDEN ⇒ empty allowlist ⇒ its Data Source Priority must name zero ww_ tools.
    text = (_TREE / "quant.curator" / "SKILL.md").read_text(encoding="utf-8")
    dsp = text.split("## ⚠️", 1)[1].split("## ", 1)[0]
    assert "ww_" not in dsp                                 # no ww_ in the DSP block
    assert "draft" in text.lower()


def test_forbidden_producer_skills_name_no_ww_tool_in_dsp():
    for wid in ("quant.backtest", "quant.factor_miner"):
        text = (_TREE / wid / "SKILL.md").read_text(encoding="utf-8")
        dsp = text.split("## ⚠️", 1)[1].split("## ", 1)[0]
        assert "ww_" not in dsp, wid                        # FORBIDDEN ⇒ zero ww_ in DSP


# --------------------------------------------------------------------------- #
# payload schema matrices                                                      #
# --------------------------------------------------------------------------- #
_NOW = datetime(2026, 7, 20, 1, 30, tzinfo=_UTC)
_SYM = normalize_symbol("600519.SH")
_SYM2 = normalize_symbol("000001.SZ")


def test_factor_ic_report_matrix():
    row = lp.FactorICRow(
        factor_id="roe_ttm", ic=0.045, rank_ic=0.05, window="近60日·沪深300", oos=False)
    rep = lp.FactorICReport(as_of=_NOW, rows=(row,))
    assert rep.schema_version == "1" and rep.rows[0].oos is False
    # rows sorted by factor_id + dup-free
    a = lp.FactorICRow(factor_id="bm_ratio", ic=0.02, rank_ic=None, window="w", oos=False)
    b = lp.FactorICRow(factor_id="roe_ttm", ic=0.03, rank_ic=None, window="w", oos=True)
    assert [r.factor_id for r in lp.FactorICReport(as_of=_NOW, rows=(a, b)).rows] == \
        ["bm_ratio", "roe_ttm"]
    with pytest.raises(Exception):     # unsorted
        lp.FactorICReport(as_of=_NOW, rows=(b, a))
    with pytest.raises(Exception):     # duplicate factor_id
        lp.FactorICReport(as_of=_NOW, rows=(a, a))
    # ic required finite; rank_ic nullable; window non-blank; oos is a bool
    with pytest.raises(Exception):
        lp.FactorICRow(factor_id="x", ic=float("nan"), rank_ic=None, window="w", oos=False)
    with pytest.raises(Exception):
        lp.FactorICRow(factor_id="x", ic=0.1, rank_ic=None, window="  ", oos=False)


def test_model_prediction_report_matrix():
    rows = (lp.ModelScoreRow(symbol=_SYM, score=1.2, rank=1),
            lp.ModelScoreRow(symbol=_SYM2, score=0.9, rank=2))
    rep = lp.ModelPredictionReport(
        as_of=_NOW, model_id="m_abc", model_asof=_NOW, rows=rows, stale_days=0)
    assert rep.schema_version == "1" and [r.rank for r in rep.rows] == [1, 2]
    # stale_days carried verbatim (DL 断供显形)
    assert lp.ModelPredictionReport(
        as_of=_NOW, model_id="m", model_asof=_NOW, rows=rows, stale_days=5).stale_days == 5
    # empty rows are an honest empty prediction
    assert lp.ModelPredictionReport(
        as_of=_NOW, model_id="m", model_asof=_NOW, rows=(), stale_days=1).rows == ()
    # ranks must be strictly ascending + unique
    with pytest.raises(Exception):
        lp.ModelPredictionReport(
            as_of=_NOW, model_id="m", model_asof=_NOW,
            rows=(lp.ModelScoreRow(symbol=_SYM, score=1.0, rank=2),
                  lp.ModelScoreRow(symbol=_SYM2, score=0.5, rank=1)), stale_days=0)
    with pytest.raises(Exception):     # rank must be a PositiveInt
        lp.ModelScoreRow(symbol=_SYM, score=1.0, rank=0)
    with pytest.raises(Exception):     # stale_days is non-negative
        lp.ModelPredictionReport(
            as_of=_NOW, model_id="m", model_asof=_NOW, rows=rows, stale_days=-1)


def test_backtest_evidence_report_matrix():
    b = lp.BacktestEvidenceReport(
        as_of=_NOW, subject="roe_ttm", vintage_ic=0.03,
        oos_verdict="robust>0 于 realized-date OOS", pbo=0.4)
    assert b.schema_version == "1" and b.pbo == 0.4 and b.caveats == ()
    # honest None: no matured OOS ⇒ oos_verdict None, pbo None, a caveat states it
    n = lp.BacktestEvidenceReport(
        as_of=_NOW, subject="x", vintage_ic=None, oos_verdict=None, pbo=None,
        caveats=("no matured realized-date OOS window; verdict UNAVAILABLE",))
    assert n.oos_verdict is None and n.pbo is None and n.caveats
    for bad in (-0.1, 1.1):            # pbo ∈ [0,1]
        with pytest.raises(Exception):
            lp.BacktestEvidenceReport(
                as_of=_NOW, subject="x", vintage_ic=None, oos_verdict=None, pbo=bad)
    with pytest.raises(Exception):     # blank subject
        lp.BacktestEvidenceReport(
            as_of=_NOW, subject="  ", vintage_ic=None, oos_verdict=None, pbo=None)


def test_fundamentals_report_matrix():
    f = lp.FundamentalsReport(
        symbol=_SYM, as_of=_NOW, valuation_score=1.1, mv_tier="大盘",
        profit_forecast_note="3 家机构一致预期 2026 EPS 上修", inputs_complete=True)
    assert f.schema_version == "1" and f.inputs_complete is True
    # honest incompleteness: nullable fields None, inputs_complete False
    g = lp.FundamentalsReport(
        symbol=_SYM, as_of=_NOW, valuation_score=None, mv_tier=None,
        profit_forecast_note=None, inputs_complete=False)
    assert g.valuation_score is None and g.inputs_complete is False
    for bad in (float("inf"), float("nan")):
        with pytest.raises(Exception):
            lp.FundamentalsReport(
                symbol=_SYM, as_of=_NOW, valuation_score=bad, mv_tier=None,
                profit_forecast_note=None, inputs_complete=False)


def test_mined_factor_draft_red_lines():
    d = lp.MinedFactorDraft(
        as_of=_NOW, factor_expr="rank(roe)-rank(turnover_5d)", rank_ic=0.048,
        sharpe=1.2, robust=0.045, passed_gate=True)
    assert d.draft_only is True and d.passed_gate is True
    # draft_only can NEVER be constructed False (Literal[True])
    with pytest.raises(Exception):
        lp.MinedFactorDraft(
            as_of=_NOW, factor_expr="x", rank_ic=0.01, passed_gate=False, draft_only=False)
    # a failed gate is recorded honestly, never upgraded; sharpe/robust nullable
    fail = lp.MinedFactorDraft(
        as_of=_NOW, factor_expr="x", rank_ic=0.01, sharpe=None, robust=None, passed_gate=False)
    assert fail.passed_gate is False and fail.draft_only is True
    with pytest.raises(Exception):     # rank_ic required finite
        lp.MinedFactorDraft(as_of=_NOW, factor_expr="x", rank_ic=float("nan"), passed_gate=True)
    with pytest.raises(Exception):     # blank expr
        lp.MinedFactorDraft(as_of=_NOW, factor_expr="  ", rank_ic=0.01, passed_gate=True)


def test_factor_lifecycle_proposal_red_lines():
    p = lp.FactorLifecycleProposal(
        as_of=_NOW, kind="revision_draft", factor_id="roe_ttm", definition_version="3",
        proposed_expr="rank(roe_ttm)*0.8", trigger_evidence=("vintage IC 连续 3 期衰减",))
    assert p.draft_only is True and p.kind == "revision_draft"
    # draft_only can NEVER be constructed False (Literal[True])
    with pytest.raises(Exception):
        lp.FactorLifecycleProposal(
            as_of=_NOW, kind="decay_alert", factor_id="x", definition_version="1",
            trigger_evidence=("y",), draft_only=False)
    # trigger_evidence must be non-empty (不许"顺手优化")
    with pytest.raises(Exception):
        lp.FactorLifecycleProposal(
            as_of=_NOW, kind="decay_alert", factor_id="x", definition_version="1",
            trigger_evidence=())
    # proposed_expr is revision_draft-only: required for revision_draft ...
    with pytest.raises(Exception):
        lp.FactorLifecycleProposal(
            as_of=_NOW, kind="revision_draft", factor_id="x", definition_version="1",
            trigger_evidence=("z",))
    # ... and forbidden for the other three kinds
    with pytest.raises(Exception):
        lp.FactorLifecycleProposal(
            as_of=_NOW, kind="retirement", factor_id="x", definition_version="1",
            proposed_expr="q", trigger_evidence=("z",))
    # the other three kinds construct cleanly without proposed_expr
    for k in ("decay_alert", "retirement", "portfolio_trigger"):
        q = lp.FactorLifecycleProposal(
            as_of=_NOW, kind=k, factor_id="roe_ttm", definition_version="2",
            trigger_evidence=("触发证据工件",))
        assert q.kind == k and q.proposed_expr is None
    # closed kind literal
    with pytest.raises(Exception):
        lp.FactorLifecycleProposal(
            as_of=_NOW, kind="rewrite", factor_id="x", definition_version="1",
            trigger_evidence=("z",))


def test_lane_a_public_models_partition():
    assert set(lp.LANE_A_PUBLIC_MODELS) == {
        lp.FactorICRow, lp.FactorICReport, lp.ModelScoreRow, lp.ModelPredictionReport,
        lp.BacktestEvidenceReport, lp.FundamentalsReport, lp.MinedFactorDraft,
        lp.FactorLifecycleProposal}


# --------------------------------------------------------------------------- #
# handlers: import-safe (stdlib-only) + delegation fidelity (clause h)          #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("name", [
    "quant.factor", "quant.model", "quant.backtest",
    "quant.fundamentals", "quant.factor_miner"])
def test_handlers_are_import_safe_stdlib_only(name):
    # clause (h) impure fallback: the legacy compute path is NOT an import-safe pure
    # function, so the handler is a self-contained stdlib projection over the legacy
    # TYPED OUTPUT — it never imports the engine / datafeed / pandas / network surface.
    tree = ast.parse((_HANDLERS / f"{name}.py").read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in tree.body:            # module top-level only (ignores docstring prose)
        if isinstance(node, ast.Import):
            imported.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")
    assert imported <= {"__future__", "math"}, imported


def test_factor_handler_marks_lookback_not_oos_and_sorts():
    h = _load_handler_module("quant.factor")
    rows = h.project_rows(
        [{"factor_id": "roe_ttm", "ic": 0.03, "rank_ic": 0.03},
         {"factor_id": "bm_ratio", "ic": 0.01}], window="近60日·沪深300")
    assert [r["factor_id"] for r in rows] == ["bm_ratio", "roe_ttm"]   # sorted
    assert all(r["oos"] is False for r in rows)                        # 回看 honesty
    assert rows[0]["rank_ic"] is None                                  # absent rank_ic → None
    # builds a valid, exactly-shaped report — every row oos=False
    rep = lp.FactorICReport(as_of=_NOW, rows=tuple(lp.FactorICRow(**r) for r in rows))
    assert all(r.oos is False for r in rep.rows)
    assert h.FEEDBACK_IS_OOS is False                                  # documented Task-8 contract
    with pytest.raises(Exception):     # duplicate factor_id rejected
        h.project_rows([{"factor_id": "x", "ic": 0.1}, {"factor_id": "x", "ic": 0.2}], window="w")


def test_model_handler_preserves_stale_days_and_validates_ranks():
    h = _load_handler_module("quant.model")
    out = h.project(
        [{"symbol": _SYM, "score": 1.2, "rank": 1},
         {"symbol": _SYM2, "score": 0.8, "rank": 2}],
        model_id="m_abc", model_asof=_NOW, stale_days=5)
    assert out["stale_days"] == 5                                      # never zeroed to hide staleness
    rep = lp.ModelPredictionReport(
        as_of=_NOW, model_id=out["model_id"], model_asof=out["model_asof"],
        rows=tuple(lp.ModelScoreRow(**r) for r in out["rows"]), stale_days=out["stale_days"])
    assert [r.rank for r in rep.rows] == [1, 2]
    with pytest.raises(Exception):     # descending / duplicate ranks rejected at the handler
        h.project([{"symbol": _SYM, "score": 1.0, "rank": 2},
                   {"symbol": _SYM2, "score": 0.5, "rank": 1}],
                  model_id="m", model_asof=_NOW, stale_days=0)


def test_backtest_handler_absent_verdict_is_honest_none():
    h = _load_handler_module("quant.backtest")
    out = h.project(subject="roe_ttm", vintage_ic=0.03, oos_verdict=None, pbo=None)
    assert out["oos_verdict"] is None and out["pbo"] is None
    assert any("unavailable" in c.lower() or "matured" in c.lower()
               for c in out["caveats"])                               # honest caveat, never faked
    rep = lp.BacktestEvidenceReport(as_of=_NOW, **out)
    assert rep.oos_verdict is None
    # a present verdict + pbo passes through verbatim
    out2 = h.project(subject="x", vintage_ic=0.02,
                     oos_verdict="robust 于 realized-date OOS", pbo=0.4)
    assert out2["pbo"] == 0.4 and out2["oos_verdict"].startswith("robust")
    # a non-finite number is dropped to None (absent), never fabricated in-range
    out3 = h.project(subject="x", vintage_ic=float("nan"), oos_verdict=None, pbo=float("inf"))
    assert out3["vintage_ic"] is None and out3["pbo"] is None


def test_fundamentals_handler_inputs_complete_reflects_presence():
    h = _load_handler_module("quant.fundamentals")
    out = h.project(valuation_score=1.1, mv_tier="大盘", profit_forecast_note="机构上修")
    assert out["inputs_complete"] is True
    rep = lp.FundamentalsReport(symbol=_SYM, as_of=_NOW, **out)
    assert rep.inputs_complete is True
    # a missing core input ⇒ inputs_complete False, nothing fabricated
    out2 = h.project(valuation_score=None, mv_tier=None, profit_forecast_note=None)
    assert out2["inputs_complete"] is False and out2["valuation_score"] is None
    # a non-finite valuation is dropped to None and completeness reads False
    out3 = h.project(valuation_score=float("nan"), mv_tier="小盘", profit_forecast_note=None)
    assert out3["valuation_score"] is None and out3["inputs_complete"] is False


def test_factor_miner_handler_draft_only_and_gate_verbatim():
    h = _load_handler_module("quant.factor_miner")
    out = h.project(factor_expr="rank(roe)-rank(turn_5d)", rank_ic=0.048,
                    sharpe=1.2, robust=0.045, passed_gate=True)
    assert out["passed_gate"] is True
    rep = lp.MinedFactorDraft(as_of=_NOW, **out)
    assert rep.draft_only is True and rep.passed_gate is True
    # a failed gate is carried verbatim, never upgraded
    out2 = h.project(factor_expr="x", rank_ic=0.005, sharpe=None, robust=None, passed_gate=False)
    assert out2["passed_gate"] is False
    assert h.DRAFT_ONLY is True


# --------------------------------------------------------------------------- #
# classify_worker cross-test: a FORBIDDEN offline seat with zero calls is OK    #
# --------------------------------------------------------------------------- #
_D64 = "a" * 64


def _min_node_run(worker_id: str) -> NodeRun:
    return NodeRun(
        node_run_id="nr-1", run_id="run-1", plan_id="plan-1", plan_digest=_D64,
        node_id="quant", worker_id=worker_id, status=NodeStatus.COMPLETED,
        attempt_id="att-1", input_snapshot_digest="1" * 64, tool_call_records=(),
        output_keys=("primary",), output_artifact_ids=("art-1",))


def _min_artifact(kind: str, schema_name: str) -> Artifact:
    return Artifact.build(
        artifact_id="art-1", run_id="run-1", created_at=_NOW,
        producer_node_id="quant", slot="quant", output_key="primary", kind=kind,
        payload_schema_ref=SchemaRef(name=schema_name, version="1"),
        payload={"note": "ok"}, rendered_md="# out", input_refs=(),
        provenance=Provenance(plan_digest=_D64, code_version="git:abc",
                              as_of=_NOW, pit_mode=DataMode.ONLINE),
        numbers=(), badges=())


def test_forbidden_offline_miner_zero_calls_is_not_killed(specs):
    # quant.factor_miner is FORBIDDEN with no REQUIRED input; a zero-tool-call run that
    # produced a non-empty payload is a legitimate offline run — never incomplete.
    rep = classify_worker(
        worker=specs["quant.factor_miner"], node_run=_min_node_run("quant.factor_miner"),
        artifact=_min_artifact("mined_factor_draft", "MinedFactorDraft"))
    assert rep.verdict == "ok"
    assert "required_tools_zero_calls" not in {i.code for i in rep.issues}


# --------------------------------------------------------------------------- #
# legacy adapter regression — frozen verdicts replay, reverses exact            #
# --------------------------------------------------------------------------- #
def test_rotation_stage_scalars_replay_unmappable_and_reverse_exact():
    # market_cycle@1#stage via migrate_rotation_stage — UNMAPPABLE until Phase 5,
    # frozen (not re-mapped by this batch); reverse returns the exact raw.
    for raw in ("冰点", "分化", "逼空", "发酵", "回踩/启动"):
        r = M.migrate_rotation_stage(raw, source_schema=M.SRC_MARKET_CYCLE)
        assert r.mapping_status is MappingStatus.UNMAPPABLE and r.normalized is None
        rev = M.rotation_stage_to_legacy(r)
        assert rev == raw and type(rev) is str


def test_confidence_scalars_replay_frozen_and_reverse_exact():
    # low / high are authoritative identity (MAPPED); 'med' stays UNMAPPABLE (frozen,
    # not re-mapped); every reverse returns the exact raw.
    lo = M.migrate_confidence("low", source_schema=M.SRC_INTROSPECTION_PROPOSAL)
    hi = M.migrate_confidence("high", source_schema=M.SRC_INTROSPECTION_PROPOSAL)
    assert lo.mapping_status is MappingStatus.MAPPED
    assert hi.mapping_status is MappingStatus.MAPPED
    assert M.confidence_to_legacy(lo) == "low" and M.confidence_to_legacy(hi) == "high"
    med = M.migrate_confidence("med", source_schema=M.SRC_INTROSPECTION_PROPOSAL)
    assert med.mapping_status is MappingStatus.UNMAPPABLE and med.normalized is None
    assert M.confidence_to_legacy(med) == "med"
