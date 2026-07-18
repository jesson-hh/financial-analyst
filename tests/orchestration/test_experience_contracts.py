# -*- coding: utf-8 -*-
"""Phase 5 · Task 4 — experience-library case contracts.

Written test-first (RED until ``guanlan_v2.orchestration.memory.experience``
exists). Covers the brief's contract matrix for the four registered payloads
(``RegimeCase`` / ``RealizedRegime`` / ``CaseMatured`` / ``CaseReviewed``) plus the
internal folded ``CaseView``:

* field presence under the exact spec §6.4 names;
* ``available_at < as_of`` rejected on ``RegimeCase``;
* features / missing_features disjointness (both directions) + the
  ``feature_coverage`` superset rule + missing sorted / dup-free;
* ``judgment.as_of == as_of`` pin;
* realized ``max_drawdown <= 0`` / ``realized_volatility >= 0`` bounds;
* realized-heat ``None`` ⇔ ``heat_unavailable_reason`` (R6: no ``unknown`` label);
* R6 realized 词表 == Task 2 axis enums minus ``unknown`` (one word list);
* ``CaseMatured.available_at == realized.available_at`` pin;
* self-digest verification on load; frozen / extra-forbid; digest movement vs
  content-addressed stability (relocation invariance of the embedding parent).

Run from repo root: ``pytest tests/orchestration/test_experience_contracts.py -v``
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import get_args

import pytest
from pydantic import ValidationError

from guanlan_v2.orchestration.enums import Confidence
from guanlan_v2.orchestration.market.factors import (
    EvidenceAnchor,
    HeatState,
    RegimeReport,
    RiskState,
    TrendState,
)
from guanlan_v2.orchestration.memory.experience import (
    CaseMatured,
    CaseReviewed,
    CaseView,
    RealizedRegime,
    RegimeCase,
    _REALIZED_HEAT_VALUES,
    _REALIZED_RISK_VALUES,
    _REALIZED_TREND_VALUES,
)

UTC = timezone.utc
AS_OF = datetime(2026, 7, 17, 7, 5, tzinfo=UTC)
AVAIL = datetime(2026, 7, 17, 7, 5, tzinfo=UTC)
LATER = datetime(2026, 8, 1, 7, 5, tzinfo=UTC)
DH = "ab" * 32  # a valid 64-char lowercase hex


# --------------------------------------------------------------------------- #
# factories                                                                    #
# --------------------------------------------------------------------------- #
def _judgment(as_of: datetime = AS_OF) -> RegimeReport:
    return RegimeReport.build(
        as_of=as_of,
        factor_report_digest=DH,
        trend=TrendState.BULL,
        risk_state=RiskState.RISK_ON,
        heat_state=HeatState.NORMAL,
        trend_probabilities={
            TrendState.BULL: 0.6, TrendState.BEAR: 0.2,
            TrendState.RANGE: 0.1, TrendState.UNKNOWN: 0.1,
        },
        risk_probabilities={
            RiskState.RISK_ON: 0.5, RiskState.RISK_OFF: 0.2,
            RiskState.NEUTRAL: 0.2, RiskState.UNKNOWN: 0.1,
        },
        heat_probabilities={
            HeatState.NORMAL: 0.7, HeatState.OVERHEAT: 0.2, HeatState.UNKNOWN: 0.1,
        },
        confidence=Confidence.MEDIUM,
        evidence=(EvidenceAnchor(factor_id="breadth.ad_ratio", value=0.7, reading="broadening"),),
        drivers=("breadth broadening",),
        evidence_factor_ids=("breadth.ad_ratio",),
        narrative="Breadth broadening while heat stays contained.",
    )


def _case_fields(**over):
    base = dict(
        id="case.20260717",
        as_of=AS_OF,
        available_at=AVAIL,
        feature_schema_version="mfs-v1",
        scaler_digest=DH,
        features={"breadth.ad_ratio": 0.7, "vol.rv": 0.15},
        feature_coverage={"breadth.ad_ratio": 1.0, "vol.rv": 1.0, "flow.northbound": 0.0},
        missing_features=("flow.northbound",),
        judgment=_judgment(),
        links=(),
    )
    base.update(over)
    return base


def _case(**over) -> RegimeCase:
    return RegimeCase.build(**_case_fields(**over))


def _realized_fields(**over):
    base = dict(
        case_as_of=AS_OF,
        horizon_trading_days=20,
        entry_date="2026-07-18",
        exit_date="2026-08-15",
        forward_return=0.043,
        max_drawdown=-0.021,
        realized_volatility=0.12,
        realized_trend="牛",
        realized_risk="risk_on",
        realized_heat=None,
        heat_unavailable_reason="no realized-heat definition in v1",
        available_at=LATER,
        data_snapshot_hash=DH,
        grader_version="grader-v1",
        grader_digest=DH,
        benchmark_id="eqw.all_a",
    )
    base.update(over)
    return base


def _realized(**over) -> RealizedRegime:
    return RealizedRegime.build(**_realized_fields(**over))


def _matured(**over) -> CaseMatured:
    realized = over.pop("realized", None) or _realized()
    base = dict(
        case_id="case.20260717",
        realized=realized,
        matured_at=LATER,
        available_at=realized.available_at,
    )
    base.update(over)
    return CaseMatured.build(**base)


def _reviewed(**over) -> CaseReviewed:
    base = dict(
        case_id="case.20260717",
        maturity_event_id="event-experience.lane0.v1-main-2",
        lesson="Broadening breadth without heat resolved bullishly over 20 sessions.",
        reviewed_at=LATER,
        available_at=LATER,
    )
    base.update(over)
    return CaseReviewed.build(**base)


# --------------------------------------------------------------------------- #
# RegimeCase                                                                    #
# --------------------------------------------------------------------------- #
def test_regime_case_constructs_and_self_verifies():
    case = _case()
    assert case.id == "case.20260717"
    assert case.content_digest == case.semantic_digest()
    assert case.judgment.as_of == case.as_of


def test_regime_case_is_frozen_and_extra_forbid():
    case = _case()
    with pytest.raises(ValidationError):
        RegimeCase(**_case_fields(surprise=1), content_digest=case.content_digest)
    with pytest.raises((ValidationError, TypeError)):
        case.id = "x"  # frozen


def test_regime_case_available_at_before_as_of_rejected():
    with pytest.raises(ValidationError):
        _case(available_at=AS_OF - timedelta(seconds=1))


def test_regime_case_available_at_equal_as_of_accepted():
    case = _case(available_at=AS_OF)
    assert case.available_at == case.as_of


def test_regime_case_missing_feature_that_is_also_a_feature_rejected():
    # disjointness: a key cannot be both present and missing.
    with pytest.raises(ValidationError):
        _case(
            features={"breadth.ad_ratio": 0.7},
            feature_coverage={"breadth.ad_ratio": 1.0},
            missing_features=("breadth.ad_ratio",),
        )


def test_regime_case_missing_features_must_be_sorted_and_dup_free():
    with pytest.raises(ValidationError):
        _case(
            feature_coverage={"breadth.ad_ratio": 1.0, "vol.rv": 1.0, "a.b": 0.0, "c.d": 0.0},
            missing_features=("c.d", "a.b"),  # unsorted
        )
    with pytest.raises(ValidationError):
        _case(
            feature_coverage={"breadth.ad_ratio": 1.0, "vol.rv": 1.0, "a.b": 0.0},
            missing_features=("a.b", "a.b"),  # dup
        )


def test_regime_case_feature_coverage_must_be_superset_of_features():
    with pytest.raises(ValidationError):
        _case(
            features={"breadth.ad_ratio": 0.7, "vol.rv": 0.15},
            feature_coverage={"breadth.ad_ratio": 1.0},  # missing vol.rv coverage
            missing_features=(),
        )


def test_regime_case_judgment_as_of_must_equal_case_as_of():
    with pytest.raises(ValidationError):
        _case(judgment=_judgment(as_of=datetime(2026, 7, 16, 7, 5, tzinfo=UTC)))


def test_regime_case_tampered_content_digest_rejected_on_load():
    fields = _case_fields()
    with pytest.raises(ValidationError):
        RegimeCase(**fields, content_digest="0" * 64)


def test_regime_case_digest_moves_on_semantic_change_but_is_content_addressed():
    a = _case()
    b = _case()  # rebuilt identically
    assert a.content_digest == b.content_digest  # content-addressed / relocation-invariant
    c = _case(feature_schema_version="mfs-v2")
    assert c.content_digest != a.content_digest


def test_regime_case_embedded_judgment_relocation_invariant():
    # the embedding parent digests the child through the child's own projection;
    # an identically-rebuilt embedded judgment keeps the parent digest stable.
    j1 = _judgment()
    j2 = _judgment()
    assert _case(judgment=j1).content_digest == _case(judgment=j2).content_digest


# --------------------------------------------------------------------------- #
# RealizedRegime                                                                #
# --------------------------------------------------------------------------- #
def test_realized_regime_constructs_and_self_verifies():
    r = _realized()
    assert r.content_digest == r.semantic_digest()
    assert r.realized_heat is None
    assert r.heat_unavailable_reason is not None


def test_realized_regime_max_drawdown_must_be_non_positive():
    with pytest.raises(ValidationError):
        _realized(max_drawdown=0.01)
    assert _realized(max_drawdown=0.0).max_drawdown == 0.0


def test_realized_regime_volatility_must_be_non_negative():
    with pytest.raises(ValidationError):
        _realized(realized_volatility=-0.01)
    assert _realized(realized_volatility=0.0).realized_volatility == 0.0


def test_realized_regime_heat_none_requires_reason():
    with pytest.raises(ValidationError):
        _realized(realized_heat=None, heat_unavailable_reason=None)


def test_realized_regime_heat_present_forbids_reason():
    with pytest.raises(ValidationError):
        _realized(realized_heat="normal", heat_unavailable_reason="nope")
    ok = _realized(realized_heat="overheat", heat_unavailable_reason=None)
    assert ok.realized_heat == "overheat"


def test_realized_regime_trend_only_chinese_labels_accepted():
    for v in ("牛", "熊", "震荡"):
        assert _realized(realized_trend=v).realized_trend == v
    for bad in ("unknown", "bull", "牛市"):
        with pytest.raises(ValidationError):
            _realized(realized_trend=bad)


def test_realized_regime_risk_and_heat_labels_closed():
    for bad in ("unknown", "on"):
        with pytest.raises(ValidationError):
            _realized(realized_risk=bad)
    with pytest.raises(ValidationError):
        _realized(realized_heat="unknown", heat_unavailable_reason=None)


def test_realized_regime_r6_vocabulary_equals_task2_enums_minus_unknown():
    # R6: 判读 (Task 2 axis enums) and realized share ONE 词表, minus 'unknown'
    # (grading is deterministic or it does not happen — no unknown realized label).
    assert set(_REALIZED_TREND_VALUES) == {m.value for m in TrendState if m is not TrendState.UNKNOWN}
    assert set(_REALIZED_RISK_VALUES) == {m.value for m in RiskState if m is not RiskState.UNKNOWN}
    assert set(_REALIZED_HEAT_VALUES) == {m.value for m in HeatState if m is not HeatState.UNKNOWN}
    # and the literal annotations carry exactly those values
    from guanlan_v2.orchestration.memory.experience import RealizedRegime as RR

    trend_lit = get_args(RR.model_fields["realized_trend"].annotation)
    assert set(trend_lit) == set(_REALIZED_TREND_VALUES)


def test_realized_regime_chinese_labels_are_byte_exact_in_digest():
    from guanlan_v2.orchestration.digest import canonical_json

    r = _realized(realized_trend="震荡")
    assert '"震荡"' in canonical_json(r)


# --------------------------------------------------------------------------- #
# CaseMatured                                                                   #
# --------------------------------------------------------------------------- #
def test_case_matured_constructs_and_self_verifies():
    m = _matured()
    assert m.content_digest == m.semantic_digest()
    assert m.available_at == m.realized.available_at


def test_case_matured_available_at_must_equal_realized_available_at():
    with pytest.raises(ValidationError):
        _matured(available_at=LATER + timedelta(days=1))


def test_case_matured_is_frozen_extra_forbid():
    m = _matured()
    with pytest.raises((ValidationError, TypeError)):
        m.case_id = "x"


def test_case_matured_tampered_digest_rejected():
    realized = _realized()
    with pytest.raises(ValidationError):
        CaseMatured(
            case_id="case.20260717", realized=realized, matured_at=LATER,
            available_at=realized.available_at, content_digest="0" * 64,
        )


# --------------------------------------------------------------------------- #
# CaseReviewed                                                                  #
# --------------------------------------------------------------------------- #
def test_case_reviewed_constructs_and_self_verifies():
    rv = _reviewed()
    assert rv.content_digest == rv.semantic_digest()
    assert rv.lesson


def test_case_reviewed_lesson_must_be_non_empty():
    with pytest.raises(ValidationError):
        _reviewed(lesson="   ")


def test_case_reviewed_is_frozen():
    rv = _reviewed()
    with pytest.raises((ValidationError, TypeError)):
        rv.lesson = "x"


# --------------------------------------------------------------------------- #
# CaseView (internal folded read model)                                        #
# --------------------------------------------------------------------------- #
def test_case_view_pending_forbids_realized_lesson_maturity():
    case = _case()
    ok = CaseView(case=case, state="pending", realized=None, lesson=None, maturity_event_id=None)
    assert ok.state == "pending"
    with pytest.raises(ValidationError):
        CaseView(case=case, state="pending", realized=_realized(), lesson=None, maturity_event_id=None)


def test_case_view_matured_requires_realized_and_maturity_no_lesson():
    case = _case()
    ok = CaseView(
        case=case, state="matured", realized=_realized(), lesson=None,
        maturity_event_id="event-x",
    )
    assert ok.realized is not None
    with pytest.raises(ValidationError):
        CaseView(case=case, state="matured", realized=None, lesson=None, maturity_event_id="event-x")
    with pytest.raises(ValidationError):
        CaseView(
            case=case, state="matured", realized=_realized(), lesson="premature",
            maturity_event_id="event-x",
        )


def test_case_view_reviewed_requires_realized_lesson_maturity():
    case = _case()
    ok = CaseView(
        case=case, state="reviewed", realized=_realized(), lesson="a lesson",
        maturity_event_id="event-x",
    )
    assert ok.lesson == "a lesson"
    with pytest.raises(ValidationError):
        CaseView(
            case=case, state="reviewed", realized=_realized(), lesson=None,
            maturity_event_id="event-x",
        )
