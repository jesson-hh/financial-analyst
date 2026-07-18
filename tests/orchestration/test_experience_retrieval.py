# -*- coding: utf-8 -*-
"""Phase 5 · Task 5 — versioned PIT scaler + numeric nearest-neighbour retrieval.

Written test-first (RED until ``memory.experience`` grows the scaler + retrieval
contracts). Covers the brief's Step-1 matrix:

* scaler fit PIT differential — a future case (``available_at > fit_as_of``) cannot
  move ``mu``/``sd`` (invariant 1);
* degenerate-feature drop (constant feature, single-observation feature);
* empty-store scaler validity (``n_obs == 0`` cold start);
* per-feature mu/sd computed only over present values — missing is never imputed
  as zero (invariant 4);
* only-matching-``feature_schema_version`` cases fit the scaler;
* schema-version mismatch rejection between query and scaler (invariant 3);
* mixed-version store filtering — only matching-schema cases are ranked;
* the distance formula pinned numerically on a 3-case hand-computed fixture;
* overlap threshold tested both sides of 0.6 and exactly at 0.6 (invariant 4);
* tie-break by ``case_id``; k-truncation;
* a pending neighbour hides realized numbers (unmatured numbers never leak);
* retrieval-PIT invariance — adding a future case to the store leaves the folded
  ``ExperienceSelection.content_digest`` byte-identical (invariant 2);
* cold-start badge on zero candidates;
* determinism across repeated calls (invariant 5);
* defense-in-depth ``FutureDataRefused`` when a caller passes a future candidate.

Run: ``pytest tests/orchestration/test_experience_retrieval.py -v``
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from guanlan_v2.orchestration.data.errors import FutureDataRefused
from guanlan_v2.orchestration.digest import content_digest
from guanlan_v2.orchestration.enums import Confidence
from guanlan_v2.orchestration.eventstore import (
    RuntimeStores,
    SchemaRegistryResolver,
)
from guanlan_v2.orchestration.events import EventType
from guanlan_v2.orchestration.market.factors import (
    EvidenceAnchor,
    HeatState,
    RegimeReport,
    RiskState,
    TrendState,
)
from guanlan_v2.orchestration.memory.experience import (
    MIN_FEATURE_OVERLAP,
    CaseMatured,
    CaseReviewed,
    CaseView,
    ExperienceLog,
    ExperienceNeighbour,
    ExperienceQuery,
    ExperienceScalerSnapshot,
    ExperienceSelection,
    RealizedRegime,
    RegimeCase,
    fit_scaler,
    fold_case_views,
    retrieve_neighbours,
)
from guanlan_v2.orchestration.runtime_contracts import Phase2RuntimeRegistry

UTC = timezone.utc
AS_OF = datetime(2026, 7, 17, 7, 5, tzinfo=UTC)
AVAIL = datetime(2026, 7, 17, 7, 5, tzinfo=UTC)
FUTURE = datetime(2026, 8, 20, 7, 5, tzinfo=UTC)
DH = "ab" * 32
FSV = "mfs-v1"


# --------------------------------------------------------------------------- #
# payload factories                                                            #
# --------------------------------------------------------------------------- #
def _judgment(as_of: datetime = AS_OF) -> RegimeReport:
    return RegimeReport.build(
        as_of=as_of, factor_report_digest=DH,
        trend=TrendState.BULL, risk_state=RiskState.RISK_ON, heat_state=HeatState.NORMAL,
        trend_probabilities={TrendState.BULL: 0.6, TrendState.BEAR: 0.2, TrendState.RANGE: 0.1, TrendState.UNKNOWN: 0.1},
        risk_probabilities={RiskState.RISK_ON: 0.5, RiskState.RISK_OFF: 0.2, RiskState.NEUTRAL: 0.2, RiskState.UNKNOWN: 0.1},
        heat_probabilities={HeatState.NORMAL: 0.7, HeatState.OVERHEAT: 0.2, HeatState.UNKNOWN: 0.1},
        confidence=Confidence.MEDIUM,
        evidence=(EvidenceAnchor(factor_id="breadth.ad_ratio", value=0.7, reading="broadening"),),
        drivers=("breadth broadening",),
        evidence_factor_ids=("breadth.ad_ratio",),
        narrative="Breadth broadening while heat stays contained.",
    )


def _case(
    case_id: str,
    features: dict[str, float],
    *,
    as_of: datetime = AS_OF,
    available_at: datetime = AVAIL,
    feature_schema_version: str = FSV,
) -> RegimeCase:
    return RegimeCase.build(
        id=case_id, as_of=as_of, available_at=available_at,
        feature_schema_version=feature_schema_version, scaler_digest=DH,
        features=dict(features),
        feature_coverage={k: 1.0 for k in features},
        missing_features=(),
        judgment=_judgment(as_of=as_of), links=(),
    )


def _realized(available_at: datetime = FUTURE, forward_return: float = 0.043) -> RealizedRegime:
    return RealizedRegime.build(
        case_as_of=AS_OF, horizon_trading_days=20, entry_date="2026-07-18", exit_date="2026-08-15",
        forward_return=forward_return, max_drawdown=-0.02, realized_volatility=0.12,
        realized_trend="牛", realized_risk="risk_on", realized_heat=None,
        heat_unavailable_reason="no realized-heat definition in v1",
        available_at=available_at, data_snapshot_hash=DH, grader_version="grader-v1",
        grader_digest=DH, benchmark_id="eqw.all_a",
    )


def _pending(case: RegimeCase) -> CaseView:
    return CaseView(case=case, state="pending")


def _matured_view(case: RegimeCase, realized: RealizedRegime | None = None) -> CaseView:
    return CaseView(
        case=case, state="matured", realized=realized or _realized(),
        maturity_event_id="evt.matured.1",
    )


# --------------------------------------------------------------------------- #
# fit_scaler — PIT + degeneracy + missing-never-imputed                         #
# --------------------------------------------------------------------------- #
def test_fit_scaler_numeric_mu_sd_over_present_values():
    views = [
        _pending(_case("c1", {"feat.a": 1.0})),
        _pending(_case("c2", {"feat.a": 2.0})),
        _pending(_case("c3", {"feat.a": 3.0})),
    ]
    scaler = fit_scaler(views, as_of=AS_OF, feature_schema_version=FSV)
    assert scaler.n_obs == 3
    assert scaler.feature_schema_version == FSV
    assert scaler.scaler_version == "expanding-zscore-v1"
    assert scaler.mu["feat.a"] == pytest.approx(2.0)
    assert scaler.sd["feat.a"] == pytest.approx(math.sqrt(2.0 / 3.0))
    assert scaler.degenerate_features == ()


def test_fit_scaler_pit_differential_future_case_cannot_move_mu_sd():
    base = [
        _pending(_case("c1", {"feat.a": 1.0})),
        _pending(_case("c2", {"feat.a": 3.0})),
    ]
    s_base = fit_scaler(base, as_of=AS_OF, feature_schema_version=FSV)
    # a future case (available after fit_as_of) with an extreme value is added:
    with_future = base + [_pending(_case("c3", {"feat.a": 100.0}, available_at=FUTURE))]
    s_with_future = fit_scaler(with_future, as_of=AS_OF, feature_schema_version=FSV)
    assert s_base.mu == s_with_future.mu
    assert s_base.sd == s_with_future.sd
    assert s_base.n_obs == s_with_future.n_obs == 2
    assert s_base.content_digest == s_with_future.content_digest


def test_fit_scaler_constant_feature_is_degenerate_dropped():
    views = [
        _pending(_case("c1", {"feat.a": 1.0, "feat.k": 5.0})),
        _pending(_case("c2", {"feat.a": 3.0, "feat.k": 5.0})),
    ]
    scaler = fit_scaler(views, as_of=AS_OF, feature_schema_version=FSV)
    assert "feat.k" in scaler.degenerate_features
    assert "feat.k" not in scaler.mu and "feat.k" not in scaler.sd
    assert "feat.a" in scaler.mu


def test_fit_scaler_single_observation_feature_is_degenerate():
    views = [
        _pending(_case("c1", {"feat.a": 1.0, "feat.solo": 9.0})),
        _pending(_case("c2", {"feat.a": 3.0})),
    ]
    scaler = fit_scaler(views, as_of=AS_OF, feature_schema_version=FSV)
    assert "feat.solo" in scaler.degenerate_features
    assert "feat.solo" not in scaler.mu


def test_fit_scaler_missing_never_imputed_as_zero():
    # feat.b present only in c1 and c3 (missing in c2). Its mu/sd are over {10, 20}
    # only — a zero-fill for c2 would give mu=10, not 15.
    views = [
        _pending(_case("c1", {"feat.a": 1.0, "feat.b": 10.0})),
        _pending(_case("c2", {"feat.a": 3.0})),
        _pending(_case("c3", {"feat.a": 5.0, "feat.b": 20.0})),
    ]
    scaler = fit_scaler(views, as_of=AS_OF, feature_schema_version=FSV)
    assert scaler.mu["feat.b"] == pytest.approx(15.0)
    assert scaler.sd["feat.b"] == pytest.approx(5.0)


def test_fit_scaler_empty_store_is_valid_cold_start():
    scaler = fit_scaler([], as_of=AS_OF, feature_schema_version=FSV)
    assert scaler.n_obs == 0
    assert scaler.mu == {} and scaler.sd == {}
    assert scaler.degenerate_features == ()
    # a valid, digest-bearing snapshot.
    assert scaler.content_digest == scaler.semantic_digest()


def test_fit_scaler_only_matching_feature_schema_version():
    views = [
        _pending(_case("c1", {"feat.a": 1.0})),
        _pending(_case("c2", {"feat.a": 3.0})),
        _pending(_case("cx", {"feat.a": 1000.0}, feature_schema_version="mfs-v2")),
    ]
    scaler = fit_scaler(views, as_of=AS_OF, feature_schema_version=FSV)
    assert scaler.n_obs == 2
    assert scaler.mu["feat.a"] == pytest.approx(2.0)


# --------------------------------------------------------------------------- #
# ExperienceScalerSnapshot — contract validators                                #
# --------------------------------------------------------------------------- #
def test_scaler_snapshot_rejects_nonpositive_sd():
    with pytest.raises(Exception):
        ExperienceScalerSnapshot.build(
            feature_schema_version=FSV, scaler_version="expanding-zscore-v1",
            fit_as_of=AS_OF, n_obs=2, mu={"feat.a": 0.0}, sd={"feat.a": 0.0},
            degenerate_features=(),
        )


def test_scaler_snapshot_self_digest_roundtrip():
    scaler = fit_scaler(
        [_pending(_case("c1", {"feat.a": 1.0})), _pending(_case("c2", {"feat.a": 3.0}))],
        as_of=AS_OF, feature_schema_version=FSV,
    )
    assert scaler.content_digest == content_digest(scaler)


def test_scaler_versions_produce_distinguishable_digests():
    views = [_pending(_case("c1", {"feat.a": 1.0})), _pending(_case("c2", {"feat.a": 3.0}))]
    v1 = fit_scaler(views, as_of=AS_OF, feature_schema_version=FSV)
    v2 = fit_scaler(views, as_of=AS_OF, feature_schema_version=FSV, scaler_version="expanding-zscore-v2")
    assert v1.content_digest != v2.content_digest  # version participates in identity


# --------------------------------------------------------------------------- #
# new-model contract validators                                                 #
# --------------------------------------------------------------------------- #
def test_query_k_is_bounded_1_to_20():
    with pytest.raises(Exception):
        ExperienceQuery.build(as_of=AS_OF, feature_schema_version=FSV, features={"feat.a": 1.0}, k=0)
    with pytest.raises(Exception):
        ExperienceQuery.build(as_of=AS_OF, feature_schema_version=FSV, features={"feat.a": 1.0}, k=21)
    assert ExperienceQuery.build(as_of=AS_OF, feature_schema_version=FSV, features={"feat.a": 1.0}, k=20).k == 20


def test_neighbour_pending_cannot_carry_realized_or_lesson():
    with pytest.raises(Exception):
        ExperienceNeighbour(
            case_id="c", case_as_of=AS_OF, distance=0.0, feature_overlap=1.0,
            state="pending", realized=_realized(),
        )
    with pytest.raises(Exception):
        ExperienceNeighbour(
            case_id="c", case_as_of=AS_OF, distance=0.0, feature_overlap=1.0,
            state="pending", lesson="leaked",
        )


def test_selection_requires_sorted_neighbours_and_cold_start_badge():
    n_far = ExperienceNeighbour(case_id="c.far", case_as_of=AS_OF, distance=2.0, feature_overlap=1.0, state="pending")
    n_near = ExperienceNeighbour(case_id="c.near", case_as_of=AS_OF, distance=1.0, feature_overlap=1.0, state="pending")
    with pytest.raises(Exception):  # out of (distance, case_id) order
        ExperienceSelection.build(
            query_digest=DH, scaler_digest=DH, feature_schema_version=FSV,
            neighbours=(n_far, n_near), visible_case_count=2,
        )
    with pytest.raises(Exception):  # empty without the honesty badge
        ExperienceSelection.build(
            query_digest=DH, scaler_digest=DH, feature_schema_version=FSV,
            neighbours=(), visible_case_count=0,
        )


# --------------------------------------------------------------------------- #
# distance formula — pinned on a hand-computed fixture                          #
# --------------------------------------------------------------------------- #
def test_distance_formula_pinned_three_case_fixture():
    scaler = ExperienceScalerSnapshot.build(
        feature_schema_version=FSV, scaler_version="expanding-zscore-v1",
        fit_as_of=AS_OF, n_obs=4, mu={"feat.a": 0.0, "feat.b": 0.0},
        sd={"feat.a": 1.0, "feat.b": 2.0}, degenerate_features=(),
    )
    query = ExperienceQuery.build(
        as_of=AS_OF, feature_schema_version=FSV,
        features={"feat.a": 0.0, "feat.b": 0.0}, k=3,
    )
    views = [
        _pending(_case("case.a", {"feat.a": 1.0, "feat.b": 0.0})),
        _pending(_case("case.b", {"feat.a": 0.0, "feat.b": 4.0})),
        _pending(_case("case.c", {"feat.a": 3.0, "feat.b": 0.0})),
    ]
    sel = retrieve_neighbours(query, views=views, scaler=scaler)
    assert [n.case_id for n in sel.neighbours] == ["case.a", "case.b", "case.c"]
    # distance = sqrt( ((q-c)/sd)^2 summed ) / sqrt(|K|), |K| = 2
    assert sel.neighbours[0].distance == pytest.approx(1.0 / math.sqrt(2))       # feat.a diff 1 / sd 1
    assert sel.neighbours[1].distance == pytest.approx(2.0 / math.sqrt(2))       # feat.b diff 4 / sd 2 = 2
    assert sel.neighbours[2].distance == pytest.approx(3.0 / math.sqrt(2))       # feat.a diff 3 / sd 1
    assert all(n.feature_overlap == pytest.approx(1.0) for n in sel.neighbours)
    assert sel.visible_case_count == 3
    assert sel.badges == ()


# --------------------------------------------------------------------------- #
# overlap threshold — both sides of 0.6 and exactly at 0.6                       #
# --------------------------------------------------------------------------- #
def _uniform_scaler(keys: list[str]) -> ExperienceScalerSnapshot:
    return ExperienceScalerSnapshot.build(
        feature_schema_version=FSV, scaler_version="expanding-zscore-v1",
        fit_as_of=AS_OF, n_obs=5, mu={k: 0.0 for k in keys}, sd={k: 1.0 for k in keys},
        degenerate_features=(),
    )


def test_overlap_threshold_both_sides_of_0_6():
    keys = ["feat.a", "feat.b", "feat.c"]
    scaler = _uniform_scaler(keys)
    query = ExperienceQuery.build(
        as_of=AS_OF, feature_schema_version=FSV,
        features={"feat.a": 1.0, "feat.b": 1.0, "feat.c": 1.0}, k=5,
    )
    views = [
        _pending(_case("keep", {"feat.a": 1.0, "feat.b": 1.0})),   # overlap 2/3 ≈ 0.667 → kept
        _pending(_case("drop", {"feat.a": 1.0})),                  # overlap 1/3 ≈ 0.333 → dropped
    ]
    sel = retrieve_neighbours(query, views=views, scaler=scaler)
    got = {n.case_id for n in sel.neighbours}
    assert got == {"keep"}
    kept = sel.neighbours[0]
    assert kept.feature_overlap == pytest.approx(2.0 / 3.0)


def test_overlap_threshold_exactly_0_6_is_included():
    keys = ["f.a", "f.b", "f.c", "f.d", "f.e"]
    scaler = _uniform_scaler(keys)
    query = ExperienceQuery.build(
        as_of=AS_OF, feature_schema_version=FSV,
        features={k: 1.0 for k in keys}, k=5,
    )
    views = [
        _pending(_case("edge", {"f.a": 1.0, "f.b": 1.0, "f.c": 1.0})),   # 3/5 = 0.6 → included
        _pending(_case("below", {"f.a": 1.0, "f.b": 1.0})),              # 2/5 = 0.4 → excluded
    ]
    sel = retrieve_neighbours(query, views=views, scaler=scaler)
    assert {n.case_id for n in sel.neighbours} == {"edge"}
    assert sel.neighbours[0].feature_overlap == pytest.approx(0.6)


def test_missing_feature_ignored_not_imputed_zero():
    keys = ["feat.a", "feat.b", "feat.c"]
    scaler = _uniform_scaler(keys)
    query = ExperienceQuery.build(
        as_of=AS_OF, feature_schema_version=FSV,
        features={"feat.a": 5.0, "feat.b": 5.0, "feat.c": 5.0}, k=5,
    )
    # candidate shares feat.a/feat.b exactly; feat.c is absent (missing, not zero).
    views = [_pending(_case("cand", {"feat.a": 5.0, "feat.b": 5.0}))]
    sel = retrieve_neighbours(query, views=views, scaler=scaler)
    assert len(sel.neighbours) == 1
    # if feat.c were imputed to 0, distance would be > 0; it must be exactly 0.
    assert sel.neighbours[0].distance == pytest.approx(0.0)
    assert sel.neighbours[0].feature_overlap == pytest.approx(2.0 / 3.0)


# --------------------------------------------------------------------------- #
# schema-version isolation                                                      #
# --------------------------------------------------------------------------- #
def test_retrieve_schema_mismatch_between_query_and_scaler_rejects():
    scaler = _uniform_scaler(["feat.a"])
    query = ExperienceQuery.build(
        as_of=AS_OF, feature_schema_version="mfs-v2",
        features={"feat.a": 1.0}, k=3,
    )
    with pytest.raises(ValueError):
        retrieve_neighbours(query, views=[], scaler=scaler)


def test_retrieve_mixed_version_store_ranks_only_matching():
    scaler = _uniform_scaler(["feat.a"])
    query = ExperienceQuery.build(
        as_of=AS_OF, feature_schema_version=FSV, features={"feat.a": 1.0}, k=5,
    )
    views = [
        _pending(_case("match", {"feat.a": 1.0})),
        _pending(_case("other", {"feat.a": 1.0}, feature_schema_version="mfs-v2")),
    ]
    sel = retrieve_neighbours(query, views=views, scaler=scaler)
    assert {n.case_id for n in sel.neighbours} == {"match"}
    assert sel.visible_case_count == 1  # only the matching-schema candidate is visible


# --------------------------------------------------------------------------- #
# ordering — tie break + k-truncation                                           #
# --------------------------------------------------------------------------- #
def test_tie_break_by_case_id():
    scaler = _uniform_scaler(["feat.a"])
    query = ExperienceQuery.build(
        as_of=AS_OF, feature_schema_version=FSV, features={"feat.a": 0.0}, k=5,
    )
    # identical features ⇒ identical distance ⇒ order decided by case_id.
    views = [
        _pending(_case("case.b", {"feat.a": 1.0})),
        _pending(_case("case.a", {"feat.a": 1.0})),
        _pending(_case("case.c", {"feat.a": 1.0})),
    ]
    sel = retrieve_neighbours(query, views=views, scaler=scaler)
    assert [n.case_id for n in sel.neighbours] == ["case.a", "case.b", "case.c"]


def test_k_truncation_returns_only_k_nearest():
    scaler = _uniform_scaler(["feat.a"])
    query = ExperienceQuery.build(
        as_of=AS_OF, feature_schema_version=FSV, features={"feat.a": 0.0}, k=2,
    )
    views = [
        _pending(_case("c1", {"feat.a": 1.0})),
        _pending(_case("c2", {"feat.a": 2.0})),
        _pending(_case("c3", {"feat.a": 3.0})),
        _pending(_case("c4", {"feat.a": 4.0})),
    ]
    sel = retrieve_neighbours(query, views=views, scaler=scaler)
    assert [n.case_id for n in sel.neighbours] == ["c1", "c2"]
    assert len(sel.neighbours) == 2
    assert sel.visible_case_count == 4


# --------------------------------------------------------------------------- #
# unmatured numbers never leak                                                  #
# --------------------------------------------------------------------------- #
def test_pending_neighbour_hides_realized_matured_shows_it():
    scaler = _uniform_scaler(["feat.a"])
    query = ExperienceQuery.build(
        as_of=AS_OF, feature_schema_version=FSV, features={"feat.a": 0.0}, k=5,
    )
    pending = _pending(_case("pend", {"feat.a": 1.0}))
    matured = _matured_view(_case("mat", {"feat.a": 2.0}))
    sel = retrieve_neighbours(query, views=[pending, matured], scaler=scaler)
    by_id = {n.case_id: n for n in sel.neighbours}
    assert by_id["pend"].state == "pending"
    assert by_id["pend"].realized is None and by_id["pend"].lesson is None
    assert by_id["mat"].state == "matured"
    assert by_id["mat"].realized is not None


# --------------------------------------------------------------------------- #
# cold start                                                                    #
# --------------------------------------------------------------------------- #
def test_cold_start_badge_on_empty_scaler():
    scaler = fit_scaler([], as_of=AS_OF, feature_schema_version=FSV)
    query = ExperienceQuery.build(
        as_of=AS_OF, feature_schema_version=FSV, features={"feat.a": 1.0}, k=5,
    )
    views = [_pending(_case("c1", {"feat.a": 1.0}))]
    sel = retrieve_neighbours(query, views=views, scaler=scaler)
    assert sel.neighbours == ()
    assert sel.badges == ("cold_start:0_neighbours",)


def test_cold_start_badge_when_all_below_overlap():
    scaler = _uniform_scaler(["feat.a", "feat.b", "feat.c"])
    query = ExperienceQuery.build(
        as_of=AS_OF, feature_schema_version=FSV,
        features={"feat.a": 1.0, "feat.b": 1.0, "feat.c": 1.0}, k=5,
    )
    views = [_pending(_case("thin", {"feat.a": 1.0}))]  # overlap 1/3 < 0.6
    sel = retrieve_neighbours(query, views=views, scaler=scaler)
    assert sel.neighbours == ()
    assert sel.badges == ("cold_start:0_neighbours",)


# --------------------------------------------------------------------------- #
# determinism + defense-in-depth PIT                                            #
# --------------------------------------------------------------------------- #
def test_retrieve_is_deterministic_across_repeated_calls():
    scaler = _uniform_scaler(["feat.a", "feat.b"])
    query = ExperienceQuery.build(
        as_of=AS_OF, feature_schema_version=FSV,
        features={"feat.a": 0.5, "feat.b": 0.5}, k=5,
    )
    views = [
        _pending(_case("c1", {"feat.a": 1.0, "feat.b": 2.0})),
        _pending(_case("c2", {"feat.a": -1.0, "feat.b": 0.0})),
    ]
    a = retrieve_neighbours(query, views=views, scaler=scaler)
    b = retrieve_neighbours(query, views=list(reversed(views)), scaler=scaler)
    assert a.content_digest == b.content_digest


def test_retrieve_refuses_future_candidate_passed_directly():
    scaler = _uniform_scaler(["feat.a"])
    query = ExperienceQuery.build(
        as_of=AS_OF, feature_schema_version=FSV, features={"feat.a": 1.0}, k=5,
    )
    views = [_pending(_case("future", {"feat.a": 1.0}, as_of=FUTURE, available_at=FUTURE))]
    with pytest.raises(FutureDataRefused):
        retrieve_neighbours(query, views=views, scaler=scaler)


# --------------------------------------------------------------------------- #
# retrieval-PIT invariance through the real fold (invariant 2)                   #
# --------------------------------------------------------------------------- #
def _experience_registry() -> Phase2RuntimeRegistry:
    reg = Phase2RuntimeRegistry()
    for model in (RegimeCase, RealizedRegime, CaseMatured, CaseReviewed):
        reg.register(model)
    reg.seal()
    return reg


class _SteppingClock:
    def __init__(self) -> None:
        self.current = datetime(2026, 7, 17, 1, 0, tzinfo=UTC)

    def now(self) -> datetime:
        self.current = self.current + timedelta(seconds=1)
        return self.current


class _Env:
    def __init__(self) -> None:
        self.resolver = SchemaRegistryResolver()
        self.registry = _experience_registry()
        self.digest = self.resolver.register(self.registry)
        self.clock = _SteppingClock()
        self.stores = RuntimeStores(resolver=self.resolver, clock=self.clock)

    def log(self) -> ExperienceLog:
        return ExperienceLog(
            event_store=self.stores.events,
            payload_store=self.stores.payloads,
            registry=self.registry,
            clock=self.clock,
            uow_factory=lambda: self.stores.unit_of_work,
        )


def test_retrieval_pit_invariance_future_case_leaves_selection_byte_identical():
    env = _Env()
    log = env.log()
    log.append_case(_case("c1", {"feat.a": 1.0}), correlation_run_id=None, idempotency_key="k.c1")
    log.append_case(_case("c2", {"feat.a": 3.0}), correlation_run_id=None, idempotency_key="k.c2")

    def _select() -> ExperienceSelection:
        views = fold_case_views(log.visible_case_events(), resolve_payload=log.resolve_payload, as_of=AS_OF)
        scaler = fit_scaler(views, as_of=AS_OF, feature_schema_version=FSV)
        query = ExperienceQuery.build(
            as_of=AS_OF, feature_schema_version=FSV, features={"feat.a": 2.0}, k=5,
        )
        return retrieve_neighbours(query, views=views, scaler=scaler)

    before = _select()
    assert len(before.neighbours) == 2

    # a FUTURE case (available after AS_OF) is appended to the same store …
    log.append_case(
        _case("c3", {"feat.a": 100.0}, as_of=FUTURE, available_at=FUTURE),
        correlation_run_id=None, idempotency_key="k.c3",
    )
    # … the store now holds three cases, but only two are PIT-visible at AS_OF.
    assert len(log.visible_case_events()) == 3
    after = _select()
    assert before.content_digest == after.content_digest
