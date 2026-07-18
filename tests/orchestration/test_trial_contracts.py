# -*- coding: utf-8 -*-
"""Phase 4 Task 1 — study identity, candidate and split contracts (``trial.py``).

Written test-first: with ``guanlan_v2/orchestration/trial.py`` absent the module
fails to import (RED). It then locks, GREEN, the reviewed invariants:

* every model is a strict / frozen / extra-forbid ``DigestModel`` with a closed
  ``schema_version``; naive datetimes, NaN/Inf floats and unknown fields are
  rejected by the Phase 1 base types;
* :class:`StudySpec` pairs ``parent_family_id`` + ``change_reason`` all-or-none
  and its ``identity_projection`` is byte-stable under display-text changes
  (``objective`` / ``label_definition`` never enter identity);
* :class:`StudyFamily` binds ``family_id == "fam." + identity_digest[:16]``;
* :class:`SplitSpec` scheme matrix (cpcv / oos_fraction / walk_forward), the
  ``0 < oos_frac < 1`` open interval, and ``effective_purge`` widening;
* :class:`OptimizeCandidate` canonical-graph hashing (position/order invariance,
  param-sensitivity, self-digest verification, a frozen golden vector);
* :class:`ValidationMetrics` honesty (``None`` never zero-filled, coverage 0..1),
  :class:`Feedback` ambiguity matrix, :class:`GovernanceReport` unavailable /
  budget matrix, :class:`HonestyGateReport`, and :class:`OptimizeRound`'s
  ``created_at`` audit exclusion.

Run from repo root: ``pytest tests/orchestration/test_trial_contracts.py -v``
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from guanlan_v2.orchestration.digest import DigestModel, content_digest
from guanlan_v2.orchestration.enums import Confidence
from guanlan_v2.orchestration.trial import (
    CANDIDATE_HASH_DOMAIN,
    STUDY_FAMILY_DOMAIN,
    Feedback,
    GateResult,
    GovernanceReport,
    HonestyGateReport,
    OptimizeCandidate,
    OptimizeRound,
    SplitSpec,
    StudyFamily,
    StudySpec,
    ValidationMetrics,
)

UTC = timezone.utc

# Well-formed but distinct sha256 hex digests used as opaque referenced digests.
DA = "a" * 64
DB = "b" * 64
DC = "c" * 64
DD = "d" * 64
DE = "e" * 64
DF = "f" * 64
WRONG = "0" * 64

# Frozen golden candidate hash for the fixed vector in test_candidate_golden_vector.
# Recorded once from the reviewed implementation; must never drift.
GOLDEN_CANDIDATE_HASH = "4a5f022ce99be41f8593545c9d8397e2e7aaab9f52a50aeddf80f4a7e491142e"


def _dt(hour: int = 1, minute: int = 30, *, tz: timezone = UTC) -> datetime:
    return datetime(2026, 7, 15, hour, minute, tzinfo=tz)


# --------------------------------------------------------------------------- #
# Builders                                                                    #
# --------------------------------------------------------------------------- #
def _study(**over) -> StudySpec:
    base = dict(
        objective="maximize risk-adjusted 5d return",
        objective_digest=DA,
        label_definition="forward 5-day excess return",
        label_digest=DB,
        universe_digest=DC,
        frequency="1d",
        split_policy_digest=DD,
    )
    base.update(over)
    return StudySpec(**base)


def _family(**over) -> StudyFamily:
    identity = over.pop("identity_digest", DA)
    base = dict(
        family_id="fam." + identity[:16],
        identity_digest=identity,
        objective_digest=DB,
        label_digest=DC,
        universe_digest=DD,
        frequency="1d",
        split_policy_digest=DE,
        governor_attestation=DF,
    )
    base.update(over)
    return StudyFamily(**base)


def _candidate(**over) -> OptimizeCandidate:
    base = dict(
        candidate_kind="workflow_graph",
        graph={"nodes": [{"id": "a", "type": "formula", "params": {"expr": "x"}}], "edges": []},
        params={},
    )
    base.update(over)
    return OptimizeCandidate.build(**base)


def _metrics(**over) -> ValidationMetrics:
    base = dict(source="run_graph")
    base.update(over)
    return ValidationMetrics(**base)


def _gate(**over) -> GateResult:
    base = dict(status="passed", min_rank_ic=0.02, reason="rank_ic and oos both pass")
    base.update(over)
    return GateResult(**base)


def _feedback(**over) -> Feedback:
    base = dict(
        target_worker_ids=("worker.formula",),
        reason="widen the reversal lookback",
        confidence=Confidence.MEDIUM,
        source="deterministic",
        ambiguous=False,
    )
    base.update(over)
    return Feedback(**base)


def _gov(**over) -> GovernanceReport:
    base = dict(
        status="ok",
        family_id="fam." + DA[:16],
        raw_trial_count=3,
        pbo_enabled=False,
        pbo_reason="pbo disabled: fewer than 10 trials",
        trial_budget_remaining=17,
        peek_budget_remaining=2,
    )
    base.update(over)
    return GovernanceReport(**base)


def _honesty(**over) -> HonestyGateReport:
    base = dict(passed=True, checked=("l0.no_lookahead", "l0.pit_ok"))
    base.update(over)
    return HonestyGateReport(**base)


def _round(**over) -> OptimizeRound:
    base = dict(
        experiment_id="exp-42",
        round_index=0,
        candidate_hash=DA,
        created_at=_dt(3, 0),
    )
    base.update(over)
    return OptimizeRound(**base)


# --------------------------------------------------------------------------- #
# StudySpec — identity partition + lineage pairing                            #
# --------------------------------------------------------------------------- #
def test_studyspec_is_strict_frozen_versioned():
    s = _study()
    assert isinstance(s, DigestModel)
    assert s.schema_version == "1"
    with pytest.raises(ValidationError):  # closed schema_version literal
        _study(schema_version="2")
    with pytest.raises(ValidationError):
        s.objective = "changed"  # frozen


def test_studyspec_lineage_pair_all_or_none():
    # parent set without reason -> error
    with pytest.raises(ValidationError):
        _study(parent_family_id="fam." + DB[:16])
    # reason set without parent -> error
    with pytest.raises(ValidationError):
        _study(change_reason="new label horizon")
    # both set -> ok
    s = _study(parent_family_id="fam." + DB[:16], change_reason="new label horizon")
    assert s.parent_family_id and s.change_reason
    # both none -> ok
    assert _study().parent_family_id is None


def test_studyspec_identity_projection_key_set():
    proj = _study().identity_projection()
    assert set(proj) == {
        "domain",
        "objective_digest",
        "label_digest",
        "universe_digest",
        "frequency",
        "split_policy_digest",
    }
    assert proj["domain"] == STUDY_FAMILY_DOMAIN == "study-family-v1"


def test_studyspec_identity_stable_under_display_text():
    a = _study(objective="alpha objective", label_definition="alpha label")
    b = _study(objective="beta objective wording", label_definition="beta label wording")
    # same registry-resolved handles, different free text -> identical identity
    assert a.identity_projection() == b.identity_projection()
    assert content_digest(a.identity_projection()) == content_digest(b.identity_projection())


def test_studyspec_identity_moves_with_a_handle():
    a = _study()
    b = _study(universe_digest=DE)  # a real identity handle changed
    assert a.identity_projection() != b.identity_projection()


# --------------------------------------------------------------------------- #
# StudyFamily — governor-derived id binding                                   #
# --------------------------------------------------------------------------- #
def test_studyfamily_valid_id_binding():
    fam = _family()
    assert fam.family_id == "fam." + fam.identity_digest[:16]


def test_studyfamily_rejects_mismatched_id():
    with pytest.raises(ValidationError):
        _family(family_id="fam." + DB[:16])  # id not derived from identity_digest


# --------------------------------------------------------------------------- #
# SplitSpec — scheme matrix + purge widening                                  #
# --------------------------------------------------------------------------- #
def test_splitspec_cpcv_requires_ngroups_and_k():
    ok = SplitSpec(scheme="cpcv", n_groups=6, k=2, purge=5, embargo=5, label_horizon=5)
    assert ok.effective_purge == 6
    with pytest.raises(ValidationError):  # missing k
        SplitSpec(scheme="cpcv", n_groups=6, label_horizon=6)
    with pytest.raises(ValidationError):  # k >= n_groups
        SplitSpec(scheme="cpcv", n_groups=2, k=2, label_horizon=6)
    with pytest.raises(ValidationError):  # cpcv forbids oos_frac
        SplitSpec(scheme="cpcv", n_groups=6, k=2, oos_frac=0.3, label_horizon=6)


def test_splitspec_oos_fraction_matrix():
    ok = SplitSpec(scheme="oos_fraction", oos_frac=0.3, label_horizon=6)
    assert ok.oos_frac == 0.3
    with pytest.raises(ValidationError):  # requires oos_frac
        SplitSpec(scheme="oos_fraction", label_horizon=6)
    with pytest.raises(ValidationError):  # forbids n_groups/k
        SplitSpec(scheme="oos_fraction", oos_frac=0.3, n_groups=6, label_horizon=6)
    with pytest.raises(ValidationError):  # open interval upper
        SplitSpec(scheme="oos_fraction", oos_frac=1.0, label_horizon=6)
    with pytest.raises(ValidationError):  # open interval lower
        SplitSpec(scheme="oos_fraction", oos_frac=0.0, label_horizon=6)


def test_splitspec_walk_forward_forbids_oos_frac():
    ok = SplitSpec(scheme="walk_forward", label_horizon=6)
    assert ok.scheme == "walk_forward"
    with pytest.raises(ValidationError):
        SplitSpec(scheme="walk_forward", oos_frac=0.3, label_horizon=6)


def test_splitspec_effective_purge_widening():
    assert SplitSpec(scheme="walk_forward", purge=5, label_horizon=5).effective_purge == 6
    assert SplitSpec(scheme="walk_forward", purge=9, label_horizon=5).effective_purge == 9
    assert SplitSpec(scheme="walk_forward", label_horizon=6).effective_purge == 7


# --------------------------------------------------------------------------- #
# OptimizeCandidate — canonical graph hashing                                 #
# --------------------------------------------------------------------------- #
def test_candidate_hash_position_and_order_invariant():
    g1 = {
        "nodes": [
            {"id": "a", "type": "formula", "params": {"expr": "x"}, "x": 1, "y": 2},
            {"id": "b", "type": "rank", "params": {"asc": True}, "x": 9, "y": 3},
        ],
        "edges": [{"from": "a", "to": "b"}],
    }
    # shuffled node order + different layout coordinates + dropped unknown keys
    g2 = {
        "nodes": [
            {"id": "b", "type": "rank", "params": {"asc": True}, "x": 100, "y": 200, "note": "z"},
            {"id": "a", "type": "formula", "params": {"expr": "x"}, "x": -5},
        ],
        "edges": [{"to": "b", "from": "a"}],
    }
    c1 = OptimizeCandidate.build(candidate_kind="workflow_graph", graph=g1, params={})
    c2 = OptimizeCandidate.build(candidate_kind="workflow_graph", graph=g2, params={})
    assert c1.candidate_hash == c2.candidate_hash


def test_candidate_hash_sensitive_to_param_change():
    g1 = {"nodes": [{"id": "a", "type": "formula", "params": {"expr": "x"}}], "edges": []}
    g2 = {"nodes": [{"id": "a", "type": "formula", "params": {"expr": "y"}}], "edges": []}
    c1 = OptimizeCandidate.build(candidate_kind="workflow_graph", graph=g1, params={})
    c2 = OptimizeCandidate.build(candidate_kind="workflow_graph", graph=g2, params={})
    assert c1.candidate_hash != c2.candidate_hash


def test_candidate_hash_sensitive_to_edge_change():
    g1 = {"nodes": [{"id": "a", "type": "t", "params": {}}], "edges": []}
    g2 = {"nodes": [{"id": "a", "type": "t", "params": {}}], "edges": [{"from": "a", "to": "a"}]}
    c1 = OptimizeCandidate.build(candidate_kind="workflow_graph", graph=g1, params={})
    c2 = OptimizeCandidate.build(candidate_kind="workflow_graph", graph=g2, params={})
    assert c1.candidate_hash != c2.candidate_hash


def test_candidate_kind_enters_hash():
    g = {"nodes": [{"id": "a", "type": "t", "params": {}}], "edges": []}
    c1 = OptimizeCandidate.build(candidate_kind="workflow_graph", graph=g, params={})
    c2 = OptimizeCandidate.build(candidate_kind="pattern_definition", graph=g, params={})
    assert c1.candidate_hash != c2.candidate_hash


def test_candidate_display_name_is_not_semantic():
    g = {"nodes": [{"id": "a", "type": "t", "params": {}}], "edges": []}
    c1 = OptimizeCandidate.build(candidate_kind="workflow_graph", graph=g, params={}, display_name="v1")
    c2 = OptimizeCandidate.build(candidate_kind="workflow_graph", graph=g, params={}, display_name="v2")
    # display name never enters candidate_hash nor the semantic digest
    assert c1.candidate_hash == c2.candidate_hash
    assert c1.semantic_digest() == c2.semantic_digest()


def test_candidate_rejects_wrong_self_digest():
    good = _candidate()
    with pytest.raises(ValidationError):
        OptimizeCandidate(
            candidate_kind=good.candidate_kind,
            graph=good.graph,
            params=good.params,
            candidate_hash=WRONG,
        )


def test_candidate_pattern_kind_admitted():
    c = OptimizeCandidate.build(
        candidate_kind="pattern_definition",
        graph={"nodes": [], "edges": []},
        params={"pattern": "double_bottom"},
    )
    assert c.candidate_kind == "pattern_definition"


def test_candidate_golden_vector():
    c = OptimizeCandidate.build(
        candidate_kind="workflow_graph",
        graph={"nodes": [{"id": "a", "type": "formula", "params": {"expr": "x"}, "x": 1}], "edges": []},
        params={},
    )
    # frozen golden — recompute with the domain-tagged canonical layout:
    expected = content_digest(
        {
            "domain": CANDIDATE_HASH_DOMAIN,
            "candidate_kind": "workflow_graph",
            "graph": {"nodes": [{"id": "a", "type": "formula", "params": {"expr": "x"}}], "edges": []},
            "params": {},
        }
    )
    assert c.candidate_hash == expected
    # frozen golden literal (recorded once from the reviewed implementation):
    assert c.candidate_hash == GOLDEN_CANDIDATE_HASH


# --------------------------------------------------------------------------- #
# ValidationMetrics — honest absence                                          #
# --------------------------------------------------------------------------- #
def test_metrics_all_none_but_source():
    m = _metrics()
    assert m.rank_ic is None and m.sharpe is None and m.oos_verdict is None
    assert m.source == "run_graph"


def test_metrics_reject_coverage_out_of_range():
    with pytest.raises(ValidationError):
        _metrics(coverage=1.5)
    with pytest.raises(ValidationError):
        _metrics(coverage=-0.1)
    assert _metrics(coverage=1.0).coverage == 1.0
    assert _metrics(coverage=0.0).coverage == 0.0


def test_metrics_reject_nan():
    with pytest.raises(ValidationError):
        _metrics(rank_ic=float("nan"))
    with pytest.raises(ValidationError):
        _metrics(sharpe=float("inf"))


def test_metrics_pattern_replay_fields():
    m = _metrics(source="pattern_replay", profit_loss_ratio=2.4, n_occurrences=37, win_rate=0.62)
    assert m.profit_loss_ratio == 2.4 and m.n_occurrences == 37


def test_metrics_requires_source():
    with pytest.raises(ValidationError):
        ValidationMetrics()


# --------------------------------------------------------------------------- #
# GateResult                                                                  #
# --------------------------------------------------------------------------- #
def test_gate_defaults_and_fields():
    g = _gate()
    assert g.oos_required == "robust" and g.sharpe_required is True
    assert g.status == "passed"


# --------------------------------------------------------------------------- #
# Feedback — ambiguity matrix                                                 #
# --------------------------------------------------------------------------- #
def test_feedback_ambiguous_true_forbids_targets():
    ok = _feedback(ambiguous=True, target_worker_ids=())
    assert ok.ambiguous is True
    with pytest.raises(ValidationError):
        _feedback(ambiguous=True, target_worker_ids=("worker.a",))


def test_feedback_unambiguous_needs_one_or_two_targets():
    assert _feedback(ambiguous=False, target_worker_ids=("worker.a",))
    assert _feedback(ambiguous=False, target_worker_ids=("worker.a", "worker.b"))
    with pytest.raises(ValidationError):  # zero
        _feedback(ambiguous=False, target_worker_ids=())
    with pytest.raises(ValidationError):  # three
        _feedback(ambiguous=False, target_worker_ids=("worker.a", "worker.b", "worker.c"))


# --------------------------------------------------------------------------- #
# GovernanceReport — unavailable / budget matrix                             #
# --------------------------------------------------------------------------- #
def test_gov_ok_report_valid():
    g = _gov()
    assert g.status == "ok" and g.pbo is None


def test_gov_unavailable_forbids_scores_requires_reasons():
    with pytest.raises(ValidationError):  # unavailable must not carry deflated_sharpe
        _gov(status="unavailable", deflated_sharpe=0.4, reasons=("insufficient trials",))
    with pytest.raises(ValidationError):  # unavailable requires reasons
        _gov(status="unavailable", reasons=())
    ok = _gov(status="unavailable", reasons=("insufficient trials",))
    assert ok.deflated_sharpe is None and ok.pbo is None


def test_gov_effective_trials_bounded_by_raw():
    with pytest.raises(ValidationError):
        _gov(effective_n_trials=9, raw_trial_count=5)
    assert _gov(effective_n_trials=3, raw_trial_count=5).effective_n_trials == 3


def test_gov_pbo_disabled_matrix():
    with pytest.raises(ValidationError):  # disabled but pbo present
        _gov(pbo_enabled=False, pbo=0.2, pbo_reason="x")
    with pytest.raises(ValidationError):  # disabled but no reason
        GovernanceReport(
            status="ok",
            family_id="fam." + DA[:16],
            raw_trial_count=3,
            pbo_enabled=False,
            trial_budget_remaining=1,
            peek_budget_remaining=1,
        )
    ok = _gov(pbo_enabled=True, pbo=0.12, pbo_reason=None)
    assert ok.pbo == 0.12


# --------------------------------------------------------------------------- #
# HonestyGateReport                                                           #
# --------------------------------------------------------------------------- #
def test_honesty_failed_requires_reasons():
    with pytest.raises(ValidationError):
        HonestyGateReport(passed=False, reasons=(), checked=("l0.no_lookahead",))
    ok = HonestyGateReport(passed=False, reasons=("lookahead detected",), checked=("l0.no_lookahead",))
    assert ok.passed is False


def test_honesty_checked_sorted_and_dedup():
    assert _honesty(checked=("l0.a", "l0.b")).checked == ("l0.a", "l0.b")
    with pytest.raises(ValidationError):  # not sorted
        _honesty(checked=("l0.b", "l0.a"))
    with pytest.raises(ValidationError):  # duplicate
        _honesty(checked=("l0.a", "l0.a"))


# --------------------------------------------------------------------------- #
# OptimizeRound — created_at is audit-only                                    #
# --------------------------------------------------------------------------- #
def test_round_created_at_excluded_from_semantic_digest():
    a = _round(created_at=_dt(3, 0))
    b = _round(created_at=_dt(9, 45))
    assert a.semantic_digest() == b.semantic_digest()


def test_round_embeds_nested_facts():
    r = _round(
        l0=_honesty(),
        metrics=_metrics(rank_ic=0.03),
        gate=_gate(),
        governance=_gov(),
        feedback=_feedback(),
    )
    assert r.metrics.rank_ic == 0.03 and r.gate.status == "passed"


# --------------------------------------------------------------------------- #
# Universal strict-base invariants                                            #
# --------------------------------------------------------------------------- #
def _valid_kwargs_matrix():
    good = _candidate()
    return {
        "StudySpec": (StudySpec, dict(
            objective="o", objective_digest=DA, label_definition="l", label_digest=DB,
            universe_digest=DC, frequency="1d", split_policy_digest=DD)),
        "StudyFamily": (StudyFamily, dict(
            family_id="fam." + DA[:16], identity_digest=DA, objective_digest=DB,
            label_digest=DC, universe_digest=DD, frequency="1d", split_policy_digest=DE,
            governor_attestation=DF)),
        "SplitSpec": (SplitSpec, dict(scheme="oos_fraction", oos_frac=0.3, label_horizon=6)),
        "OptimizeCandidate": (OptimizeCandidate, dict(
            candidate_kind=good.candidate_kind, graph=good.graph, params=good.params,
            candidate_hash=good.candidate_hash)),
        "ValidationMetrics": (ValidationMetrics, dict(source="run_graph")),
        "GateResult": (GateResult, dict(status="passed", min_rank_ic=0.02, reason="ok")),
        "Feedback": (Feedback, dict(
            target_worker_ids=("worker.a",), reason="r", confidence=Confidence.LOW,
            source="rule", ambiguous=False)),
        "GovernanceReport": (GovernanceReport, dict(
            status="ok", family_id="fam." + DA[:16], raw_trial_count=1, pbo_enabled=False,
            pbo_reason="disabled", trial_budget_remaining=1, peek_budget_remaining=1)),
        "HonestyGateReport": (HonestyGateReport, dict(passed=True, checked=("l0.a",))),
        "OptimizeRound": (OptimizeRound, dict(
            experiment_id="e", round_index=0, candidate_hash=DA, created_at=_dt(3, 0))),
    }


@pytest.mark.parametrize("name", list(_valid_kwargs_matrix()))
def test_every_model_rejects_unknown_field(name):
    cls, kwargs = _valid_kwargs_matrix()[name]
    assert cls(**kwargs) is not None  # baseline valid
    with pytest.raises(ValidationError):
        cls(**{**kwargs, "totally_unknown_field": "x"})


def test_round_rejects_naive_datetime():
    with pytest.raises(ValidationError):
        OptimizeRound(
            experiment_id="e", round_index=0, candidate_hash=DA,
            created_at=datetime(2026, 7, 15, 3, 0),  # naive
        )
