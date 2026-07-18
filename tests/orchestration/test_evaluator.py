# -*- coding: utf-8 -*-
"""Phase 4 Task 7 — the four-layer evaluator (``evaluator.py``).

Written test-first: with ``guanlan_v2/orchestration/evaluator.py`` absent this module
is RED on the missing import (not a collection error elsewhere). It then locks, GREEN,
the six brief invariants of the four layers:

* **L0** — two deterministic honesty gates that refuse *before / around* the expensive
  evaluation. ``l0_candidate_gate`` is a pure function of the candidate (never touches
  an evaluation callable); ``l0_run_gate`` refuses a run whose required chain (the
  terminal's ancestry + the recipe ML node) errored, even when the terminal parsed
  (fidelity-guard parity), while accepting an off-chain diagnostic error (照跑存档不过门).
* **L1** — deterministic metrics normalization: the six ``metrics_of_terminal`` keys map
  1:1 onto ``ValidationMetrics(source="run_graph")``; a missing/NaN input becomes
  ``None`` (honest absence), never zero-filled, and no key is invented.
* **L2** — a thin pass-through to ``Governor.govern`` (asserted: the *same object*, no
  additional logic).
* **L3** — attribution feedback: honest ``ambiguous=True`` when the graph cannot be
  narrowed and no port is supplied; a port raising falls back to ``source="rule"`` with
  the visible ``"(规则兜底·非 LLM)"`` badge; a port returning >2 targets or unsourced
  evidence is coerced to ambiguous, never trusted blindly; a valid 1–2 target answer is
  passed through unmodified.
* **isolation** — ``evaluator.py`` imports no sealed read path (AST/source scan).

Run from repo root: ``pytest tests/orchestration/test_evaluator.py -v``
"""
from __future__ import annotations

import ast
from datetime import datetime, timezone
from pathlib import Path

import pytest

from guanlan_v2.orchestration.enums import Confidence
from guanlan_v2.orchestration.evaluator import (
    AttributionPort,
    l0_candidate_gate,
    l0_run_gate,
    l1_normalize_run_graph_metrics,
    l2_govern,
    l3_feedback,
)
from guanlan_v2.orchestration.governor import Governor, derive_study_family
from guanlan_v2.orchestration.trial import (
    Feedback,
    GovernanceReport,
    HonestyGateReport,
    OptimizeCandidate,
    SplitSpec,
    StudySpec,
    ValidationMetrics,
)

UTC = timezone.utc
DA = "a" * 64


# --------------------------------------------------------------------------- #
# builders                                                                     #
# --------------------------------------------------------------------------- #
def _candidate(**over) -> OptimizeCandidate:
    base = dict(
        candidate_kind="workflow_graph",
        graph={"nodes": [{"id": "a", "type": "formula", "params": {"expr": "x"}}],
               "edges": []},
        params={},
    )
    base.update(over)
    return OptimizeCandidate.build(**base)


def _study(**over) -> StudySpec:
    base = dict(
        objective="maximize risk-adjusted 5d return",
        objective_digest=DA,
        label_definition="5d forward return",
        label_digest="b" * 64,
        universe_digest="e" * 64,
        frequency="monthly",
        split_policy_digest="f" * 64,
    )
    base.update(over)
    return StudySpec(**base)


def _split(**over) -> SplitSpec:
    base = dict(scheme="cpcv", n_groups=6, k=2, purge=5, embargo=5, label_horizon=5)
    base.update(over)
    return SplitSpec(**base)


def _outcome(**over) -> dict:
    base = dict(
        ok=True,
        reason=None,
        terminal={"kind": "backtest", "node_id": "bt", "payload": {}},
        metrics={"rank_ic": 0.05, "sharpe": 1.3, "ann_return": 0.2,
                 "oos_verdict": "robust", "n_dates": 120, "factor": "mom20"},
        exprs=["x"],
        has_ml=False,
        node_results={},
        node_errors=[],
        warnings=[],
        elapsed_sec=0.1,
    )
    base.update(over)
    return base


class _ScriptPort:
    """A scripted AttributionPort: returns a fixed result or raises, counting calls."""

    def __init__(self, *, result: Feedback | None = None,
                 raises: BaseException | None = None) -> None:
        self._result = result
        self._raises = raises
        self.calls = 0

    def attribute(self, *, goal, metrics, candidate, constraints) -> Feedback:  # noqa: ANN001
        self.calls += 1
        if self._raises is not None:
            raise self._raises
        return self._result


# --------------------------------------------------------------------------- #
# L0 — candidate honesty gate                                                  #
# --------------------------------------------------------------------------- #
def test_l0_candidate_gate_passes_a_well_formed_candidate():
    report = l0_candidate_gate(_candidate())
    assert isinstance(report, HonestyGateReport)
    assert report.passed is True
    assert report.reasons == ()
    # every executed check is listed, canonically sorted + duplicate-free.
    assert set(report.checked) == {
        "classifiable_terminal", "edges_reference_known_nodes",
        "nodes_nonempty", "params_finite_json",
    }
    assert list(report.checked) == sorted(report.checked)


def test_l0_candidate_gate_refuses_empty_nodes():
    report = l0_candidate_gate(_candidate(graph={"nodes": [], "edges": []}))
    assert report.passed is False
    assert any("no nodes" in r for r in report.reasons)
    assert "nodes_nonempty" in report.checked


def test_l0_candidate_gate_refuses_edge_to_unknown_node():
    cand = _candidate(graph={
        "nodes": [{"id": "a", "type": "formula", "params": {"expr": "x"}}],
        "edges": [{"from": ["a", "out"], "to": ["ghost", "in"]}],
    })
    report = l0_candidate_gate(cand)
    assert report.passed is False
    assert any("ghost" in r for r in report.reasons)


def test_l0_candidate_gate_refuses_nan_param():
    # a candidate can normally never hold a NaN param (the model rejects it); L0
    # re-checks defensively, so even a model_construct'd candidate is refused before
    # any evaluation spend.
    bad = OptimizeCandidate.model_construct(
        candidate_kind="workflow_graph",
        graph={"nodes": [{"id": "a", "type": "formula",
                          "params": {"expr": "x", "k": float("nan")}}], "edges": []},
        params={},
        candidate_hash="0" * 64,
    )
    report = l0_candidate_gate(bad)
    assert report.passed is False
    assert any("non-finite" in r or "NaN" in r for r in report.reasons)
    assert "params_finite_json" in report.checked


def test_l0_candidate_gate_refuses_unclassifiable_terminal():
    cand = _candidate(graph={
        "nodes": [{"id": "a", "type": "noop", "params": {}}], "edges": []})
    report = l0_candidate_gate(cand)
    assert report.passed is False
    assert any("classifiable" in r or "dish" in r for r in report.reasons)
    assert "classifiable_terminal" in report.checked


def test_l0_candidate_gate_never_calls_an_evaluation_callable():
    # invariant 1: pure function of the candidate — the signature takes only a
    # candidate, so there is structurally no evaluation seam to invoke.
    import inspect

    params = inspect.signature(l0_candidate_gate).parameters
    assert list(params) == ["candidate"]


# --------------------------------------------------------------------------- #
# L0 — run honesty gate                                                        #
# --------------------------------------------------------------------------- #
def test_l0_run_gate_passes_a_clean_run():
    report = l0_run_gate(_outcome(), required_chain_node_ids=("bt",))
    assert report.passed is True
    assert report.reasons == ()


def test_l0_run_gate_refuses_not_ok_outcome():
    report = l0_run_gate(
        _outcome(ok=False, terminal=None, metrics=None, reason="图无主终端节点"),
        required_chain_node_ids=("bt",))
    assert report.passed is False
    assert any("ok=False" in r or "terminal" in r for r in report.reasons)


def test_l0_run_gate_refuses_required_chain_node_error_even_when_terminal_parsed():
    # fidelity-guard parity vector (invariant 2): metrics parsed, ok=True, but an
    # ML/ancestor node on the required chain errored → metrics come from a fallback
    # path and are refused.
    report = l0_run_gate(
        _outcome(node_errors=[{"nid": "mlnode", "type": "gat", "error": "train failed"}]),
        required_chain_node_ids=("mlnode", "bt"))
    assert report.passed is False
    assert any("mlnode" in r for r in report.reasons)
    assert any("fidelity" in r or "fallback" in r for r in report.reasons)


def test_l0_run_gate_accepts_off_chain_diagnostic_node_error():
    # 照跑存档不过门: an off-chain diagnostic node error does not refuse the run.
    report = l0_run_gate(
        _outcome(node_errors=[{"nid": "diag", "type": "analysis", "error": "whatever"}]),
        required_chain_node_ids=("bt",))
    assert report.passed is True
    assert report.reasons == ()


# --------------------------------------------------------------------------- #
# L1 — deterministic metrics normalization                                     #
# --------------------------------------------------------------------------- #
def test_l1_maps_the_six_keys_one_to_one():
    vm = l1_normalize_run_graph_metrics(_outcome())
    assert isinstance(vm, ValidationMetrics)
    assert vm.source == "run_graph"
    assert vm.rank_ic == 0.05
    assert vm.sharpe == 1.3
    assert vm.ann_return == 0.2
    assert vm.oos_verdict == "robust"
    assert vm.n_dates == 120
    assert vm.factor == "mom20"
    # no key is invented — fields outside the six run_graph keys stay honestly None.
    assert vm.turnover is None
    assert vm.win_rate is None
    assert vm.coverage is None
    assert vm.tail_ratio is None
    assert vm.max_drawdown is None


def test_l1_missing_sharpe_is_none_not_zero():
    vm = l1_normalize_run_graph_metrics(
        _outcome(metrics={"rank_ic": 0.05, "oos_verdict": "robust"}))
    assert vm.sharpe is None
    assert vm.rank_ic == 0.05


def test_l1_nan_rank_ic_becomes_none():
    vm = l1_normalize_run_graph_metrics(
        _outcome(metrics={"rank_ic": float("nan"), "sharpe": 1.0}))
    assert vm.rank_ic is None
    assert vm.sharpe == 1.0


def test_l1_absent_oos_verdict_is_none_and_unknown_verdict_coerced():
    vm = l1_normalize_run_graph_metrics(_outcome(metrics={"rank_ic": 0.01}))
    assert vm.oos_verdict is None
    vm2 = l1_normalize_run_graph_metrics(
        _outcome(metrics={"rank_ic": 0.01, "oos_verdict": "not-a-verdict"}))
    assert vm2.oos_verdict is None


def test_l1_ignores_extra_diagnostic_keys():
    vm = l1_normalize_run_graph_metrics(
        _outcome(metrics={"rank_ic": 0.02, "diagnostic_only": {"foo": "bar"}}))
    assert vm.rank_ic == 0.02  # extra key ignored, no error (照跑存档不过门)


# --------------------------------------------------------------------------- #
# L2 — governance delegate                                                     #
# --------------------------------------------------------------------------- #
class _SpyGovernor:
    """A stand-in governor that returns a sentinel and records the forwarded kwargs."""

    def __init__(self, sentinel: GovernanceReport) -> None:
        self.sentinel = sentinel
        self.calls: list[dict] = []

    def govern(self, **kwargs) -> GovernanceReport:
        self.calls.append(kwargs)
        return self.sentinel


def test_l2_is_a_thin_pass_through_same_object():
    sentinel = GovernanceReport(
        status="unavailable", family_id="fam.0123456789abcdef", raw_trial_count=1,
        trial_budget_remaining=1, peek_budget_remaining=1, pbo_reason="x",
        reasons=("y",))
    spy = _SpyGovernor(sentinel)
    fam = derive_study_family(_study())
    cand, split = _candidate(), _split()
    out = l2_govern(
        governor=spy, family=fam, stats={"raw_trial_count": 1}, returns=None,
        perf_matrix=None, candidate=cand, split_spec=split)
    # the exact object the governor returned — no wrapping, no additional logic.
    assert out is sentinel
    assert spy.calls == [dict(
        family=fam, stats={"raw_trial_count": 1}, returns=None, perf_matrix=None,
        candidate=cand, split_spec=split)]


def test_l2_value_equals_direct_governor_call():
    gov = Governor(trial_budget=100, peek_budget=10)
    fam = derive_study_family(_study())
    cand, split = _candidate(), _split()
    kwargs = dict(family=fam, stats={"raw_trial_count": 5, "reveal_count": 1},
                  returns=(0.01, 0.02, -0.01), perf_matrix=None,
                  candidate=cand, split_spec=split)
    direct = gov.govern(**kwargs)
    via = l2_govern(governor=gov, **kwargs)
    assert via == direct


# --------------------------------------------------------------------------- #
# L3 — attribution feedback matrix                                             #
# --------------------------------------------------------------------------- #
def _metrics() -> ValidationMetrics:
    return ValidationMetrics(source="run_graph", rank_ic=0.01, sharpe=-0.5,
                            oos_verdict="degraded")


def _un_narrowable_candidate() -> OptimizeCandidate:
    return OptimizeCandidate.build(
        candidate_kind="workflow_graph",
        graph={"nodes": [
            {"id": "a", "type": "formula", "params": {"expr": "x"}},
            {"id": "b", "type": "formula", "params": {"expr": "y"}},
            {"id": "c", "type": "compose", "params": {}},
        ], "edges": []},
        params={})


def test_attribution_port_protocol_is_importable_and_structural():
    port = _ScriptPort(result=None)
    assert isinstance(port, AttributionPort)


def test_l3_no_port_un_narrowable_is_honest_ambiguous():
    fb = l3_feedback(goal="g", metrics=_metrics(), candidate=_un_narrowable_candidate(),
                     evidence_artifact_ids=("art1",), port=None)
    assert fb.ambiguous is True
    assert fb.target_worker_ids == ()
    assert fb.source == "deterministic"


def test_l3_no_port_narrowable_attributes_deterministically():
    fb = l3_feedback(goal="g", metrics=_metrics(), candidate=_candidate(),
                     evidence_artifact_ids=("art1",), port=None)
    assert fb.ambiguous is False
    assert fb.source == "deterministic"
    assert fb.target_worker_ids == ("a",)


def test_l3_port_raising_falls_back_to_rule_with_visible_badge():
    port = _ScriptPort(raises=RuntimeError("boom"))
    fb = l3_feedback(goal="g", metrics=_metrics(), candidate=_candidate(),
                     evidence_artifact_ids=("art1",), port=port)
    assert port.calls == 1
    assert fb.source == "rule"
    assert "(规则兜底·非 LLM)" in fb.reason


def test_l3_port_returning_three_targets_is_coerced_ambiguous():
    # a misbehaving port bypasses the Feedback validator via model_construct; L3
    # re-validates and coerces, never trusting it blindly.
    bad = Feedback.model_construct(
        target_worker_ids=("a", "b", "c"), evidence_artifact_ids=(),
        allowed_changes=(), reason="three targets", confidence=Confidence.HIGH,
        ambiguous=False, source="llm")
    port = _ScriptPort(result=bad)
    fb = l3_feedback(goal="g", metrics=_metrics(), candidate=_candidate(),
                     evidence_artifact_ids=("art1",), port=port)
    assert fb.ambiguous is True
    assert fb.target_worker_ids == ()
    assert "3 target" in fb.reason or "coerced" in fb.reason


def test_l3_port_returning_unsourced_evidence_is_coerced_ambiguous():
    bad = Feedback(target_worker_ids=("a",), evidence_artifact_ids=("artX",),
                   reason="cites unknown evidence", confidence=Confidence.HIGH,
                   ambiguous=False, source="llm")
    port = _ScriptPort(result=bad)
    fb = l3_feedback(goal="g", metrics=_metrics(), candidate=_candidate(),
                     evidence_artifact_ids=("art1",), port=port)
    assert fb.ambiguous is True
    assert "unsourced" in fb.reason or "artX" in fb.reason


def test_l3_port_returning_valid_one_target_is_passed_through_unmodified():
    good = Feedback(target_worker_ids=("a",), evidence_artifact_ids=("art1",),
                    allowed_changes=("swap_expr",), reason="node a defines the signal",
                    confidence=Confidence.MEDIUM, ambiguous=False, source="llm")
    port = _ScriptPort(result=good)
    fb = l3_feedback(goal="g", metrics=_metrics(), candidate=_candidate(),
                     evidence_artifact_ids=("art1",), port=port)
    assert fb is good
    assert fb.ambiguous is False
    assert fb.target_worker_ids == ("a",)
    assert fb.source == "llm"


# --------------------------------------------------------------------------- #
# isolation — no sealed read path (invariant 6)                                #
# --------------------------------------------------------------------------- #
def test_evaluator_imports_no_sealed_read_path():
    import guanlan_v2.orchestration.evaluator as ev

    src = Path(ev.__file__).read_text(encoding="utf-8")
    for token in ("SealedResultStore", "SealedEvaluationRecord"):
        assert token not in src, f"evaluator.py must not reference {token}"
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            assert node.module is None or "sealed" not in node.module.split("."), (
                "evaluator.py must not import from a sealed module")
        elif isinstance(node, ast.Import):
            for alias in node.names:
                assert "sealed" not in alias.name.split("."), (
                    "evaluator.py must not import a sealed module")
