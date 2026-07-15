# -*- coding: utf-8 -*-
"""Task 7 — minimal Phase 2 compatibility payloads (``schemas.py``).

Written test-first (RED until the three payloads exist on ``schemas.py``).
Locks the reviewed, *minimal* typed outputs that exercise the Phase 2 static
compatibility path and the legacy rating / sentiment adapters:

* :class:`ResearchPlan` — five-level :class:`PortfolioRating`, rationale and
  strategic actions;
* :class:`PortfolioDecision` — rating, executive summary, thesis, a finite
  *strictly-positive* optional ``price_target`` (``bool`` / ``NaN`` / ``Inf`` /
  non-positive rejected) and an optional ``time_horizon``;
* :class:`SentimentReport` — :class:`SentimentBand`, a finite ``overall_score``
  in ``[0, 10]``, :class:`Confidence` and a narrative.

Every payload is a frozen, strict :class:`DigestModel` that forbids extra
fields, carries a closed ``schema_version`` and holds no runtime-generated
authority / identity fields.

This test also *guards the scope*: the Phase 5 / 6 / 8 deferred classes must not
have leaked into ``schemas.py`` "for completeness".

Run from repo root: ``pytest tests/orchestration/test_payloads.py -v``
"""
from __future__ import annotations

import math

import pytest
from pydantic import ValidationError

from guanlan_v2.orchestration import schemas
from guanlan_v2.orchestration.digest import DigestModel
from guanlan_v2.orchestration.enums import Confidence, PortfolioRating, SentimentBand
from guanlan_v2.orchestration.schemas import (
    PortfolioDecision,
    ResearchPlan,
    SentimentReport,
)


# --------------------------------------------------------------------------- #
# builders                                                                    #
# --------------------------------------------------------------------------- #
def _research_plan(**over) -> ResearchPlan:
    base = dict(
        recommendation=PortfolioRating.BUY,
        rationale="Earnings momentum and expanding margins support accumulation.",
        strategic_actions=("Initiate a starter position", "Add on a pullback"),
    )
    base.update(over)
    return ResearchPlan(**base)


def _portfolio_decision(**over) -> PortfolioDecision:
    base = dict(
        rating=PortfolioRating.OVERWEIGHT,
        executive_summary="Constructive risk/reward into the next print.",
        investment_thesis="Structural demand plus operating leverage.",
        price_target=182.5,
        time_horizon="6-12 months",
    )
    base.update(over)
    return PortfolioDecision(**base)


def _sentiment_report(**over) -> SentimentReport:
    base = dict(
        overall_band=SentimentBand.MILDLY_BULLISH,
        overall_score=6.5,
        confidence=Confidence.HIGH,
        narrative="Breadth improving; flows positive but not euphoric.",
    )
    base.update(over)
    return SentimentReport(**base)


# --------------------------------------------------------------------------- #
# base contract: strict immutable DigestModel with a closed schema_version     #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("cls", [ResearchPlan, PortfolioDecision, SentimentReport])
def test_payloads_are_digest_models(cls):
    assert issubclass(cls, DigestModel)


@pytest.mark.parametrize(
    "obj", [_research_plan(), _portfolio_decision(), _sentiment_report()]
)
def test_payloads_carry_closed_schema_version(obj):
    assert obj.schema_version == "1"
    # a closed Literal cannot self-report a different version
    with pytest.raises(ValidationError):
        type(obj)(**{**obj.model_dump(), "schema_version": "2"})


def test_happy_paths_construct():
    rp = _research_plan()
    assert rp.recommendation is PortfolioRating.BUY
    assert rp.strategic_actions[0] == "Initiate a starter position"

    pd = _portfolio_decision()
    assert pd.rating is PortfolioRating.OVERWEIGHT
    assert pd.price_target == 182.5
    assert pd.time_horizon == "6-12 months"

    sr = _sentiment_report()
    assert sr.overall_band is SentimentBand.MILDLY_BULLISH
    assert sr.overall_score == 6.5
    assert sr.confidence is Confidence.HIGH


# --------------------------------------------------------------------------- #
# 1. exact enum serialization (an Enum serializes by its .value)               #
# --------------------------------------------------------------------------- #
def test_research_plan_enum_serializes_by_value():
    proj = _research_plan(recommendation=PortfolioRating.SELL)._projected_mapping(
        "semantic"
    )
    assert proj["recommendation"] == "Sell"


def test_portfolio_decision_enum_serializes_by_value():
    proj = _portfolio_decision(rating=PortfolioRating.UNDERWEIGHT)._projected_mapping(
        "semantic"
    )
    assert proj["rating"] == "Underweight"


def test_sentiment_report_enums_serialize_by_value():
    proj = _sentiment_report(
        overall_band=SentimentBand.MILDLY_BEARISH, confidence=Confidence.LOW
    )._projected_mapping("semantic")
    assert proj["overall_band"] == "Mildly Bearish"
    assert proj["confidence"] == "low"


def test_enum_identity_changes_semantic_digest():
    a = _research_plan(recommendation=PortfolioRating.BUY)
    b = _research_plan(recommendation=PortfolioRating.SELL)
    assert a.semantic_digest() != b.semantic_digest()


# --------------------------------------------------------------------------- #
# 2. price_target rejects bool, NaN, infinity and non-positive values          #
# --------------------------------------------------------------------------- #
def test_price_target_optional_defaults_to_none():
    pd = _portfolio_decision(price_target=None)
    assert pd.price_target is None


def test_price_target_accepts_positive_finite():
    assert _portfolio_decision(price_target=0.01).price_target == 0.01


def test_price_target_rejects_bool():
    with pytest.raises(ValidationError):
        _portfolio_decision(price_target=True)


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_price_target_rejects_non_finite(bad):
    with pytest.raises(ValidationError):
        _portfolio_decision(price_target=bad)


@pytest.mark.parametrize("bad", [0.0, -0.01, -5.0])
def test_price_target_rejects_non_positive(bad):
    with pytest.raises(ValidationError):
        _portfolio_decision(price_target=bad)


def test_time_horizon_optional_defaults_to_none():
    assert _portfolio_decision(time_horizon=None).time_horizon is None


# --------------------------------------------------------------------------- #
# 3. sentiment score bounds and finite checks                                  #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("ok", [0.0, 5.0, 10.0])
def test_sentiment_score_accepts_in_band(ok):
    assert _sentiment_report(overall_score=ok).overall_score == ok


@pytest.mark.parametrize("bad", [-0.01, 10.01, 11.0, -1.0])
def test_sentiment_score_rejects_out_of_band(bad):
    with pytest.raises(ValidationError):
        _sentiment_report(overall_score=bad)


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_sentiment_score_rejects_non_finite(bad):
    with pytest.raises(ValidationError):
        _sentiment_report(overall_score=bad)


def test_sentiment_score_rejects_bool():
    with pytest.raises(ValidationError):
        _sentiment_report(overall_score=True)


def test_sentiment_score_is_required():
    with pytest.raises(ValidationError):
        SentimentReport(
            overall_band=SentimentBand.NEUTRAL,
            confidence=Confidence.MEDIUM,
            narrative="flat",
        )


# --------------------------------------------------------------------------- #
# 4. extra fields rejected                                                     #
# --------------------------------------------------------------------------- #
def test_research_plan_rejects_extra_field():
    with pytest.raises(ValidationError):
        _research_plan(bogus=1)


def test_portfolio_decision_rejects_extra_field():
    with pytest.raises(ValidationError):
        _portfolio_decision(bogus=1)


def test_sentiment_report_rejects_extra_field():
    with pytest.raises(ValidationError):
        _sentiment_report(bogus=1)


# --------------------------------------------------------------------------- #
# 5. models immutable                                                          #
# --------------------------------------------------------------------------- #
def test_research_plan_is_frozen():
    rp = _research_plan()
    with pytest.raises(ValidationError):
        rp.recommendation = PortfolioRating.SELL


def test_portfolio_decision_is_frozen():
    pd = _portfolio_decision()
    with pytest.raises(ValidationError):
        pd.price_target = 999.0


def test_sentiment_report_is_frozen():
    sr = _sentiment_report()
    with pytest.raises(ValidationError):
        sr.overall_score = 1.0


# --------------------------------------------------------------------------- #
# blank text fields rejected (non-empty string convention)                     #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "builder,field",
    [
        (_research_plan, "rationale"),
        (_portfolio_decision, "executive_summary"),
        (_portfolio_decision, "investment_thesis"),
        (_sentiment_report, "narrative"),
    ],
)
def test_required_text_fields_reject_blank(builder, field):
    with pytest.raises(ValidationError):
        builder(**{field: "   "})


# --------------------------------------------------------------------------- #
# scope discipline: deferred Phase 5 / 6 / 8 classes must NOT be present        #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "deferred",
    [
        "MarketFactorValue",
        "MarketFactorReport",
        "RegimeReport",
        "RealizedRegime",
        "RotationReport",
        "TargetPosition",
        "PortfolioTargetProposal",
        "TargetPortfolioIntent",
        "DecisionSchedule",
        "DebateMessage",
    ],
)
def test_deferred_classes_not_leaked_into_schemas(deferred):
    assert not hasattr(schemas, deferred), (
        f"{deferred} is frozen in a later phase and must not be added here"
    )
