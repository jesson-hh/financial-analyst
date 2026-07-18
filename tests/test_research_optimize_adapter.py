# -*- coding: utf-8 -*-
"""Task 10 — factor-research adapter over ``run_optimize`` (behavior-equivalence).

Proves the existing ``research.loop.run_research_loop`` behavior is preserved through
the Phase 4 ``run_optimize`` state machine as an adapter (``research.optimize_adapter``),
without touching the old loop. The equivalence tests script ONE behavior on the shared
``research.loop`` seams (``_call_generate`` / ``_run_graph_eval`` / ``_call_critique`` /
``_route_product`` → ``_save_draft`` / ``_save_compose_expr`` / ``_call_train_promote`` /
``_save_graph`` / ``_write_lesson``) — which *both* paths call — and assert pairwise-equal
decisions (round counts, gate verdicts, product channel, lesson writes, honest-stop
errors). Written test-first: with ``optimize_adapter.py`` absent these are RED on the
missing module.

Seven-point matrix (brief):
1. first-round pass: both stop at round 0, one draft, same channel, same gate verdict;
2. stall: identical critique twice → exactly one stall retry then honest interruption;
   a param/structure change is not a stall;
3. rounds exhausted without pass: same round count + same best round;
4. propose failure → both terminate honestly with a lesson and no product; eval failure
   mid-run → both continue to the next round;
5. rule-critique fallback prefix visibility in both paths;
6. adapter-only additions: revealed-round == raw_trial_count, reused triples skip eval,
   ``run_graph`` never called with ``prefer_model_terminal`` unset;
7. existing research suites untouched and green (run separately).
"""
from __future__ import annotations

import pytest

import guanlan_v2.research.loop as rl
import guanlan_v2.research.optimize_adapter as ad
import guanlan_v2.research.store as rs
from guanlan_v2.workflow import executor as wex

from guanlan_v2.orchestration.enums import ExperimentStatus
from guanlan_v2.orchestration.eventstore import RuntimeStores
from guanlan_v2.orchestration.governor import Governor
from guanlan_v2.orchestration.optimize import (
    OPTIMIZE_MAX_ROUNDS,
    ExperimentStateStore,
    PayloadRoundStore,
    run_optimize,
)
from guanlan_v2.orchestration.runtime_clock import SystemClock
from guanlan_v2.orchestration.trial import ValidationMetrics
from guanlan_v2.orchestration.trial_ledger import PHASE4_STATE_CELL_NAMESPACES, TrialLedger


_G0 = {"nodes": [{"id": "n1", "type": "formula", "params": {"expr": "rank(-delta(close,5))"}}],
       "edges": []}
_G1 = {"nodes": [{"id": "n1", "type": "formula", "params": {"expr": "rank(-delta(close,20))"}}],
       "edges": []}


def _ex_ok(rank_ic=0.05, sharpe=1.0, oos="robust", exprs=("rank(-delta(close,5))",), has_ml=False):
    return {"ok": True, "reason": None,
            "metrics": {"rank_ic": rank_ic, "sharpe": sharpe, "ann_return": 0.1,
                        "oos_verdict": oos, "n_dates": 20, "factor": " + ".join(exprs)},
            "terminal": {"kind": "analysis", "node_id": "an", "payload": {}},
            "exprs": list(exprs), "has_ml": has_ml, "node_errors": [], "warnings": []}


def _weak():
    return _ex_ok(rank_ic=0.001, sharpe=0.1, oos="degraded")


def _wire(monkeypatch, tmp_path, evals, critique=None, generate=None):
    """Patch the shared ``research.loop`` seams both paths delegate to; fresh queue/lists."""
    monkeypatch.setattr(rs, "RUNS_PATH", tmp_path / "runs.jsonl")
    monkeypatch.setattr(rs, "ROUNDS_PATH", tmp_path / "rounds.jsonl")
    q = list(evals)
    monkeypatch.setattr(rl, "_call_generate",
                        generate or (lambda goal: {"ok": True, "graph": _G0, "attempts": 1}))
    monkeypatch.setattr(rl, "_call_critique",
                        critique or (lambda goal, metrics, graph, constraints="":
                                     {"ok": True, "diagnosis": "换更长窗口", "graph": _G1,
                                      "source": "llm"}))
    monkeypatch.setattr(rl, "_run_graph_eval", lambda graph, p, pr, k, mr: q.pop(0))
    lessons, graphs, drafts = [], [], []
    monkeypatch.setattr(rl, "_write_lesson", lambda goal, s: lessons.append(s) or True)
    monkeypatch.setattr(rl, "_save_graph",
                        lambda goal, rid, g: graphs.append(g) or {"ok": True, "id": "w1",
                                                                  "name": "研究·x"})
    monkeypatch.setattr(rl, "_save_draft",
                        lambda rid, k, expr, goal, diag, m: drafts.append(expr) or
                        {"ok": True, "name": f"lib_rl_{rid[-6:]}_r{k}", "registered": True})
    return {"lessons": lessons, "graphs": graphs, "drafts": drafts}


def _run_both(monkeypatch, tmp_path, *, evals, critique=None, generate=None,
              max_rounds=3, min_rank_ic=0.02, universe="csi300_active", freq="month"):
    """Run the old loop then the adapter over one behavior script; return both sides."""
    side_l = _wire(monkeypatch, tmp_path, list(evals), critique=critique, generate=generate)
    end_l = rl.run_research_loop("rr_loop00", "找反转", max_rounds, min_rank_ic, universe, freq,
                                 None, None, progress=lambda **kw: None)
    side_a = _wire(monkeypatch, tmp_path, list(evals), critique=critique, generate=generate)
    end_a = ad.run_research_optimize("rr_opt000", "找反转", max_rounds, min_rank_ic, universe,
                                     freq, None, None, progress=lambda **kw: None)
    return end_l, side_l, end_a, side_a


# --------------------------------------------------------------------------- #
# invariant 1 — first-round pass                                              #
# --------------------------------------------------------------------------- #
def test_first_round_pass_equivalence(monkeypatch, tmp_path):
    end_l, side_l, end_a, side_a = _run_both(monkeypatch, tmp_path, evals=[_ex_ok()])
    assert end_l["ok"] is True and end_a["ok"] is True
    assert end_l["n_rounds"] == end_a["n_rounds"] == 1
    assert end_l["best_k"] == end_a["best_k"] == 0
    assert end_l["promoted"]["status"] == end_a["promoted"]["status"] == "draft"
    assert side_l["drafts"] == side_a["drafts"] == ["rank(-delta(close,5))"]
    assert side_l["graphs"] == side_a["graphs"]           # best graph stored identically
    assert "达标" in side_l["lessons"][-1] and "达标" in side_a["lessons"][-1]
    # first-round pass: no critique ran, so the 诊断 tail is the propose diag on both paths.
    # (Full-string equality is blocked only by the run_id-derived factor name in the draft
    # lesson — a harness artifact, not a behaviour divergence — so lock the 诊断 tail.)
    assert (side_l["lessons"][-1].rsplit("诊断:", 1)[-1]
            == side_a["lessons"][-1].rsplit("诊断:", 1)[-1] == "初始生成(LLM propose)")
    assert end_a["memory_written"] is True


def test_first_round_pass_compose_channel_equivalence(monkeypatch, tmp_path):
    g2 = {"nodes": [{"id": "a", "type": "formula", "params": {"expr": "rank(x)"}},
                    {"id": "b", "type": "formula", "params": {"expr": "rank(y)"}}], "edges": []}
    ex = _ex_ok(exprs=("rank(x)", "rank(y)"))
    ex["terminal"] = {"kind": "analysis", "node_id": "an", "payload": {
        "members": ["rank(x)", "rank(y)"],
        "weights": [{"name": "rank(x)", "weight": 0.63}, {"name": "rank(y)", "weight": 0.37}]}}
    saved_l, saved_a = {}, {}

    def gen(goal):
        return {"ok": True, "graph": g2}

    # loop path
    side_l = _wire(monkeypatch, tmp_path, [dict(ex)], generate=gen)
    monkeypatch.setattr(rl, "_save_compose_expr",
                        lambda name, expr, goal, diag, meta:
                        saved_l.update(name=name, expr=expr) or {"ok": True, "name": name})
    monkeypatch.setattr(rl, "_run_graph_eval", lambda graph, p, pr, k, mr: dict(ex))
    end_l = rl.run_research_loop("rr_loopc0", "找组合", 3, 0.02, "csi300_active", "month",
                                 None, None, progress=lambda **kw: None)
    # adapter path
    side_a = _wire(monkeypatch, tmp_path, [dict(ex)], generate=gen)
    monkeypatch.setattr(rl, "_save_compose_expr",
                        lambda name, expr, goal, diag, meta:
                        saved_a.update(name=name, expr=expr) or {"ok": True, "name": name})
    monkeypatch.setattr(rl, "_run_graph_eval", lambda graph, p, pr, k, mr: dict(ex))
    end_a = ad.run_research_optimize("rr_optc00", "找组合", 3, 0.02, "csi300_active", "month",
                                     None, None, progress=lambda **kw: None)
    assert end_l["promoted"]["status"] == end_a["promoted"]["status"] == "draft_compose"
    assert saved_l["expr"] == saved_a["expr"] == "(0.63)*(rank(x)) + (0.37)*(rank(y))"


# --------------------------------------------------------------------------- #
# invariant 2 — stall                                                         #
# --------------------------------------------------------------------------- #
def test_stall_identical_twice_honest_stop_equivalence(monkeypatch, tmp_path):
    calls_l, calls_a = [], []

    def crit_factory(sink):
        def crit(goal, metrics, graph, constraints=""):
            sink.append(constraints)
            return {"ok": True, "diagnosis": "原样", "graph": _G0, "source": "llm"}
        return crit

    side_l = _wire(monkeypatch, tmp_path, [_weak()], critique=crit_factory(calls_l))
    end_l = rl.run_research_loop("rr_loops0", "找反转", 3, 0.02, "csi300_active", "month",
                                 None, None, progress=lambda **kw: None)
    side_a = _wire(monkeypatch, tmp_path, [_weak()], critique=crit_factory(calls_a))
    end_a = ad.run_research_optimize("rr_opts00", "找反转", 3, 0.02, "csi300_active", "month",
                                     None, None, progress=lambda **kw: None)

    assert end_l["ok"] is False and end_a["ok"] is False
    assert "停滞" in end_l["error"] and "停滞" in end_a["error"]
    assert end_l["n_rounds"] == end_a["n_rounds"] == 1
    assert len(calls_l) == len(calls_a) == 2                    # one propose crit + one stall retry
    assert "停滞" in calls_l[1] and "停滞" in calls_a[1]
    assert "停滞" in side_l["lessons"][-1] and "停滞" in side_a["lessons"][-1]


def test_stall_retry_then_progress_equivalence(monkeypatch, tmp_path):
    def crit_factory(sink):
        def crit(goal, metrics, graph, constraints=""):
            sink.append(constraints)
            if len(sink) == 1:
                return {"ok": True, "diagnosis": "调 dir 参数", "graph": _G0, "source": "llm"}
            return {"ok": True, "diagnosis": "换20日窗口", "graph": _G1, "source": "llm"}
        return crit

    weak0 = _weak()
    weak1 = _ex_ok(rank_ic=0.001, sharpe=0.1, oos="degraded", exprs=("rank(-delta(close,20))",))
    calls_l, calls_a = [], []
    _wire(monkeypatch, tmp_path, [weak0, weak1], critique=crit_factory(calls_l))
    end_l = rl.run_research_loop("rr_loopp0", "找反转", 2, 0.02, "csi300_active", "month",
                                 None, None, progress=lambda **kw: None)
    _wire(monkeypatch, tmp_path, [weak0, weak1], critique=crit_factory(calls_a))
    end_a = ad.run_research_optimize("rr_optp00", "找反转", 2, 0.02, "csi300_active", "month",
                                     None, None, progress=lambda **kw: None)
    assert end_l["ok"] is True and end_a["ok"] is True
    assert end_l["n_rounds"] == end_a["n_rounds"] == 2
    assert len(calls_l) == len(calls_a) == 2
    assert "参数均真实生效" in calls_a[0] and "停滞" in calls_a[1]


def test_param_structure_change_is_not_a_stall_equivalence(monkeypatch, tmp_path):
    g2 = {"nodes": [{"id": "n1", "type": "formula", "params": {"expr": "rank(-delta(close,5))"}},
                    {"id": "m", "type": "xgb", "params": {"trees": 300}}], "edges": []}

    def crit_factory(sink):
        def crit(goal, metrics, graph, constraints=""):
            sink.append(constraints)
            return {"ok": True, "diagnosis": "加树", "graph": g2, "source": "llm"}
        return crit

    calls_l, calls_a = [], []
    _wire(monkeypatch, tmp_path, [_weak(), _weak()], critique=crit_factory(calls_l))
    end_l = rl.run_research_loop("rr_loopn0", "找反转", 2, 0.02, "csi300_active", "month",
                                 None, None, progress=lambda **kw: None)
    _wire(monkeypatch, tmp_path, [_weak(), _weak()], critique=crit_factory(calls_a))
    end_a = ad.run_research_optimize("rr_optn00", "找反转", 2, 0.02, "csi300_active", "month",
                                     None, None, progress=lambda **kw: None)
    # no stall retry in either path: exactly one critique call, both exhaust two rounds.
    assert len(calls_l) == len(calls_a) == 1
    assert end_l["n_rounds"] == end_a["n_rounds"] == 2


# --------------------------------------------------------------------------- #
# invariant 3 — rounds exhausted without pass                                 #
# --------------------------------------------------------------------------- #
def test_exhausted_no_pass_same_best_round_equivalence(monkeypatch, tmp_path):
    end_l, side_l, end_a, side_a = _run_both(
        monkeypatch, tmp_path, evals=[_weak(), _weak()], max_rounds=2)
    assert end_l["ok"] is True and end_a["ok"] is True
    assert end_l["n_rounds"] == end_a["n_rounds"] == 2
    assert end_l["promoted"] is None and end_a["promoted"] is None
    assert end_l["best_k"] == end_a["best_k"] == 0             # equal rank_ic → first round wins
    assert side_l["graphs"] == side_a["graphs"] == [_G0]
    assert "未达标" in side_l["lessons"][-1] and "未达标" in side_a["lessons"][-1]
    # the lesson's 诊断 tail is the loop's running diag = the LAST critique's diagnosis
    # ("换更长窗口"), NOT the best round's diag ("初始生成…" for best_k==0). Full-string
    # equality locks the divergence the 未达标 substring alone masked.
    assert side_a["lessons"][-1].endswith("诊断:换更长窗口")
    assert side_l["lessons"][-1] == side_a["lessons"][-1]


# --------------------------------------------------------------------------- #
# invariant 4 — propose failure + eval failure                                #
# --------------------------------------------------------------------------- #
def test_propose_failure_honest_stop_equivalence(monkeypatch, tmp_path):
    gen = lambda goal: {"ok": False, "reason": "LLM 不可用: timeout"}
    end_l, side_l, end_a, side_a = _run_both(
        monkeypatch, tmp_path, evals=[], generate=gen)
    assert end_l["ok"] is False and end_a["ok"] is False
    assert "提案失败" in end_l["error"] and "提案失败" in end_a["error"]
    assert "不降级模板" in end_a["error"]
    assert end_l["n_rounds"] == end_a["n_rounds"] == 0
    assert end_l["promoted"] is None and end_a["promoted"] is None
    assert side_l["drafts"] == side_a["drafts"] == []
    assert "提案即失败" in side_l["lessons"][-1] and "提案即失败" in side_a["lessons"][-1]
    assert side_l["lessons"][-1] == side_a["lessons"][-1]    # run_id-free lesson: full equality


def test_eval_failure_round_continues_equivalence(monkeypatch, tmp_path):
    bad = {"ok": False, "reason": "缺少数据", "metrics": None, "exprs": [], "has_ml": False,
           "node_errors": [], "terminal": None, "warnings": []}
    end_l, side_l, end_a, side_a = _run_both(
        monkeypatch, tmp_path, evals=[bad, _ex_ok()])
    assert end_l["ok"] is True and end_a["ok"] is True
    assert end_l["n_rounds"] == end_a["n_rounds"] == 2       # failed eval round + passing round
    assert end_l["best_k"] == end_a["best_k"] == 1
    assert end_l["promoted"]["status"] == end_a["promoted"]["status"] == "draft"
    # pass at round 1: the running diag is the round-1 critique's diagnosis on both paths;
    # lock the 诊断 tail (draft factor name differs only by run_id — a harness artifact).
    assert (side_l["lessons"][-1].rsplit("诊断:", 1)[-1]
            == side_a["lessons"][-1].rsplit("诊断:", 1)[-1] == "换更长窗口")


# --------------------------------------------------------------------------- #
# invariant 5 — rule-critique fallback prefix visibility                      #
# --------------------------------------------------------------------------- #
def test_rule_critique_fallback_prefix_equivalence(monkeypatch, tmp_path):
    rule_crit = (lambda goal, metrics, graph, constraints="":
                 {"ok": True, "diagnosis": "方向反了", "graph": _G1, "source": "rule",
                  "llm_error": "x"})
    _wire(monkeypatch, tmp_path, [_weak(), _weak()], critique=rule_crit)
    rl.run_research_loop("rr_loopr0", "找反转", 2, 0.02, "csi300_active", "month",
                         None, None, progress=lambda **kw: None)
    rows_l = rs.read_rounds(run_id="rr_loopr0", limit=10)
    _wire(monkeypatch, tmp_path, [_weak(), _weak()], critique=rule_crit)
    ad.run_research_optimize("rr_optr00", "找反转", 2, 0.02, "csi300_active", "month",
                             None, None, progress=lambda **kw: None)
    rows_a = rs.read_rounds(run_id="rr_optr00", limit=10)
    # the improved round (new-first) carries the visible non-LLM rule badge in both paths.
    assert any(str(r.get("diag") or "").startswith("(规则兜底·非 LLM) ") for r in rows_l)
    assert any(str(r.get("diag") or "").startswith("(规则兜底·非 LLM) ") for r in rows_a)
    assert any(r.get("critique_source") == "rule" for r in rows_a)


# --------------------------------------------------------------------------- #
# invariant 6 — adapter-only additions                                        #
# --------------------------------------------------------------------------- #
def _opt_env():
    clock = SystemClock()
    reg, resolver = ad._shared_registry()
    stores = RuntimeStores(resolver=resolver, clock=clock,
                           allowed_cell_namespaces=PHASE4_STATE_CELL_NAMESPACES)
    ledger = TrialLedger(event_store=stores.events, payload_store=stores.payloads,
                         state_cells=stores.cells, registry=reg, clock=clock,
                         uow_factory=lambda: stores.unit_of_work)
    states = ExperimentStateStore(payload_store=stores.payloads, state_cells=stores.cells,
                                  registry=reg, clock=clock,
                                  uow_factory=lambda: stores.unit_of_work)
    rounds = PayloadRoundStore(payload_store=stores.payloads, registry=reg)
    return clock, reg, stores, ledger, states, rounds


def _bindings(clock, params, imp_state, sink, min_rank_ic=0.02):
    counter = [0]

    def _eval(cand, ctx):
        k = counter[0]
        counter[0] += 1
        return ad.research_evaluate_validation(cand, ctx, params=params, round_k=k,
                                               max_rounds=OPTIMIZE_MAX_ROUNDS, sink=sink)

    def _gate(m):
        return ad.research_gate(m, min_rank_ic=min_rank_ic)

    def _improve(c, m, fb):
        return ad.research_improve(c, m, fb, goal="找反转",
                                   params=params, state=imp_state)

    return _eval, _gate, _improve


def test_revealed_round_equals_raw_trial_count(monkeypatch, tmp_path):
    # two distinct never-passing candidates → two revealed validation trials.
    def crit(goal, metrics, graph, constraints=""):
        return {"ok": True, "diagnosis": "换窗口", "graph": _G1, "source": "llm"}

    def fake_eval(graph, p, pr, k, mr):
        expr = graph["nodes"][0]["params"]["expr"]
        return _ex_ok(rank_ic=0.001, sharpe=0.1, oos="degraded", exprs=(expr,))

    monkeypatch.setattr(rl, "_call_critique", crit)
    monkeypatch.setattr(rl, "_run_graph_eval", fake_eval)

    clock, reg, stores, ledger, states, rounds = _opt_env()
    params = {"universe": "csi_fast", "freq": "month", "start": None, "end": None,
              "max_rounds": 2, "min_rank_ic": 0.02}
    seed = ad.candidate_from_graph(_G0, params={"universe": "csi_fast", "freq": "month",
                                                "start": None, "end": None})
    imp_state = {"diag_by_hash": {}, "source_by_hash": {}, "error": None}
    sink = {}
    _eval, _gate, _improve = _bindings(clock, params, imp_state, sink)
    study = ad.research_study("找反转", universe="csi_fast", freq="month", min_rank_ic=0.02)

    res = run_optimize(
        seed=seed, ctx=ad._legacy_data_context(clock, universe="csi_fast", freq="month",
                                               start=None, end=None),
        study=study, split_spec=ad.research_split_spec("month"), max_rounds=2,
        governor=Governor(trial_budget=OPTIMIZE_MAX_ROUNDS, peek_budget=OPTIMIZE_MAX_ROUNDS),
        evaluate_validation=_eval, gate=_gate, improve=_improve, ledger=ledger,
        rounds=rounds, states=states, payload_store=stores.payloads, experiment_id="e1",
        clock=clock, registry=reg, code_prompt_model_hash=ad._adapter_code_prompt_model_hash())

    assert res.state.status == ExperimentStatus.FAILED
    revealed = [r for r in rounds.rounds("e1")
                if r.trial_id is not None and r.metrics is not None and r.error is None]
    assert len(revealed) == 2
    assert ledger.effective_trial_stats(study)["raw_trial_count"] == 2 == len(revealed)


def test_reused_triples_skip_evaluation(monkeypatch, tmp_path):
    c0, c1, c2 = _G0, _G1, {"nodes": [{"id": "n1", "type": "formula",
                                       "params": {"expr": "rank(-delta(close,3))"}}], "edges": []}
    chain = [c0, c1, c2]
    idx = {"i": 0}

    def crit(goal, metrics, graph, constraints=""):
        idx["i"] += 1
        return {"ok": True, "diagnosis": "步进", "graph": chain[min(idx["i"], 2)], "source": "llm"}

    evalcalls = {"n": 0}

    def fake_eval(graph, p, pr, k, mr):
        evalcalls["n"] += 1
        expr = graph["nodes"][0]["params"]["expr"]
        # c2 passes; c0/c1 weak.
        if "close,3" in expr:
            return _ex_ok(rank_ic=0.05, sharpe=1.0, oos="robust", exprs=(expr,))
        return _ex_ok(rank_ic=0.001, sharpe=0.1, oos="degraded", exprs=(expr,))

    monkeypatch.setattr(rl, "_call_critique", crit)
    monkeypatch.setattr(rl, "_run_graph_eval", fake_eval)

    clock, reg, stores, ledger, states, rounds = _opt_env()
    cand_params = {"universe": "csi_fast", "freq": "month", "start": None, "end": None}
    params = dict(cand_params, max_rounds=4, min_rank_ic=0.02)
    study = ad.research_study("找反转", universe="csi_fast", freq="month", min_rank_ic=0.02)

    def _run(exp):
        idx["i"] = 0
        seed = ad.candidate_from_graph(c0, params=cand_params)
        imp_state = {"diag_by_hash": {}, "source_by_hash": {}, "error": None}
        _eval, _gate, _improve = _bindings(clock, params, imp_state, {})
        return run_optimize(
            seed=seed, ctx=ad._legacy_data_context(clock, universe="csi_fast", freq="month",
                                                   start=None, end=None),
            study=study, split_spec=ad.research_split_spec("month"), max_rounds=4,
            governor=Governor(trial_budget=OPTIMIZE_MAX_ROUNDS, peek_budget=OPTIMIZE_MAX_ROUNDS),
            evaluate_validation=_eval, gate=_gate, improve=_improve, ledger=ledger,
            rounds=rounds, states=states, payload_store=stores.payloads, experiment_id=exp,
            clock=clock, registry=reg,
            code_prompt_model_hash=ad._adapter_code_prompt_model_hash())

    res1 = _run("e1")
    assert res1.state.status == ExperimentStatus.PASSED_VALIDATION
    calls_after_first = evalcalls["n"]
    raw_before = ledger.effective_trial_stats(study)["raw_trial_count"]

    res2 = _run("e2")                                 # replay over the SAME ledger
    assert res2.state.status == ExperimentStatus.PASSED_VALIDATION
    assert evalcalls["n"] == calls_after_first        # zero new evaluations on replay
    assert ledger.effective_trial_stats(study)["raw_trial_count"] == raw_before


def test_run_graph_eval_always_opts_in_model_terminal(monkeypatch):
    seen = {}

    def fake_run_graph(graph, overrides=None, on_node=None, prefer_model_terminal=False):
        seen["flag"] = prefer_model_terminal
        seen["overrides"] = overrides
        return {"ok": True, "metrics": {"rank_ic": 0.05, "sharpe": 1.0, "oos_verdict": "robust"},
                "terminal": {"kind": "analysis"}, "node_errors": [], "exprs": ["x"]}

    monkeypatch.setattr(wex, "run_graph", fake_run_graph)
    cand = ad.candidate_from_graph(
        {"nodes": [{"id": "n1", "type": "formula", "params": {"expr": "x"}}], "edges": []},
        params={"universe": "csi_fast", "freq": "month", "start": None, "end": None})
    m = ad.research_evaluate_validation(
        cand, None, params={"universe": "csi_fast", "freq": "month", "start": None, "end": None},
        sink={})
    assert seen["flag"] is True
    assert seen["overrides"]["oos_frac"] == 0.3
    assert isinstance(m, ValidationMetrics) and m.rank_ic == 0.05


# --------------------------------------------------------------------------- #
# helper-function contracts (Produces)                                        #
# --------------------------------------------------------------------------- #
def test_candidate_from_graph_stall_matches_graph_signature():
    p = {"universe": "csi_fast", "freq": "month", "start": None, "end": None}
    # positions ignored: same signature -> same candidate_hash.
    g_a = {"nodes": [{"id": "n1", "type": "formula", "params": {"expr": "a"}, "x": 1, "y": 2}],
           "edges": []}
    g_b = {"nodes": [{"id": "n1", "type": "formula", "params": {"expr": "a"}, "x": 9, "y": 9}],
           "edges": []}
    assert wex.graph_signature(g_a) == wex.graph_signature(g_b)
    assert (ad.candidate_from_graph(g_a, params=p).candidate_hash
            == ad.candidate_from_graph(g_b, params=p).candidate_hash)
    # param change counted: different signature -> different hash.
    g_c = {"nodes": [{"id": "n1", "type": "formula", "params": {"expr": "a", "w": 2}}], "edges": []}
    assert wex.graph_signature(g_a) != wex.graph_signature(g_c)
    assert (ad.candidate_from_graph(g_a, params=p).candidate_hash
            != ad.candidate_from_graph(g_c, params=p).candidate_hash)


def test_research_gate_joint_parity_with_loop_gate():
    def _both(rank_ic, sharpe, oos, mn=0.02):
        m = ValidationMetrics(source="run_graph", rank_ic=rank_ic, sharpe=sharpe, oos_verdict=oos)
        loop_passed = rl._gate({"rank_ic": rank_ic, "sharpe": sharpe, "oos_verdict": oos},
                               mn)["passed"]
        gate = ad.research_gate(m, min_rank_ic=mn)
        return loop_passed, gate

    lp, g = _both(0.05, 1.0, "robust")
    assert lp is True and g.status == "passed"
    lp, g = _both(0.05, 1.0, "overfit")
    assert lp is False and g.status == "failed"
    lp, g = _both(0.01, 1.0, "robust")
    assert lp is False and g.status == "failed"
    lp, g = _both(0.05, -0.9, "robust")
    assert lp is False and g.status == "failed"                # negative sharpe blocked
    # all-absent -> unavailable (loop gate: not passed).
    empty = ValidationMetrics(source="run_graph")
    assert rl._gate({}, 0.02)["passed"] is False
    assert ad.research_gate(empty, min_rank_ic=0.02).status == "unavailable"


def test_research_split_spec_forced_oos_fraction():
    sp = ad.research_split_spec("month")
    assert sp.scheme == "oos_fraction" and sp.oos_frac == 0.3 and sp.label_horizon == 5


def test_research_study_display_and_identity_display_free():
    a = ad.research_study("找一个反转因子", universe="csi_fast", freq="month", min_rank_ic=0.02)
    b = ad.research_study("换个说法找反转", universe="csi_fast", freq="month", min_rank_ic=0.02)
    assert a.objective == "找一个反转因子"                      # display text = goal
    # different objective text -> different objective_digest, so identity differs by design
    # (goal is the objective here); universe/split handles are stable.
    assert a.universe_digest == b.universe_digest
    assert a.split_policy_digest == b.split_policy_digest


def test_honesty_inventory_in_module_docstring():
    doc = ad.__doc__ or ""
    assert "run_research_loop" in doc            # production cutover stays on the old path
    assert "graph_signature" in doc              # sha1 stall key stays in loop.py
    assert "legacy_data_binding" in doc          # degradation badged
    assert "draft" in doc                        # draft-only, no rejected status
    assert "rejected" in doc
