# -*- coding: utf-8 -*-
"""Phase 5 · Task 2 — Lane 0 LLM output contracts (``RegimeReport`` / ``RotationReport``).

Written test-first (RED until the report classes, the three Phase-5-local axis
enums and the tolerance/gating constants exist in
``guanlan_v2.orchestration.market.factors``). Covers the full brief matrix:

* three-axis closed probability maps (member-set, sum-tolerance, range);
* modal fields == argmax with frozen-enum declaration-order tie-break; the R6
  Chinese ``TrendState`` serialization pinned byte-exact into canonical JSON;
* honest unknown gating (attention threshold, HIGH-confidence caps, modal-unknown
  forcing LOW);
* ④ additions (``factor_report_digest`` binding, ``evidence`` ≥1, EvidenceAnchor
  id consistency);
* ``RotationReport`` / ``MainlineRead`` (ranking order, per-mainline stage,
  strength bounds, empty-mainlines honesty);
* neither schema is decision-class; the legacy rotation migration stays
  UNMAPPABLE (explicit non-goal); digest movement + self-digest on load.

Run from repo root: ``pytest tests/orchestration/test_lane0_reports.py -v``
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from guanlan_v2.orchestration.digest import canonical_json
from guanlan_v2.orchestration.enums import Confidence, RotationStage
from guanlan_v2.orchestration.market.factors import (
    AXIS_SUM_TOLERANCE,
    HIGH_CONFIDENCE_UNKNOWN_MAX,
    UNKNOWN_ATTENTION_THRESHOLD,
    EvidenceAnchor,
    HeatState,
    MainlineRead,
    RegimeReport,
    RiskState,
    RotationReport,
    TrendState,
    MARKET_FACTOR_REPORT_SCHEMA_REF,
    REGIME_REPORT_SCHEMA_REF,
    ROTATION_REPORT_SCHEMA_REF,
)

UTC = timezone.utc
AS_OF = datetime(2026, 7, 17, 7, 5, tzinfo=UTC)
DIGEST = "ab" * 32  # a valid 64-char lowercase hex


# --------------------------------------------------------------------------- #
# helpers                                                                      #
# --------------------------------------------------------------------------- #
def _anchor(factor_id: str = "breadth.ad_ratio", value: float = 0.7, reading: str = "breadth broadening") -> EvidenceAnchor:
    return EvidenceAnchor(factor_id=factor_id, value=value, reading=reading)


def _regime_fields(**over):
    base = dict(
        as_of=AS_OF,
        factor_report_digest=DIGEST,
        trend=TrendState.BULL,
        risk_state=RiskState.RISK_ON,
        heat_state=HeatState.NORMAL,
        trend_probabilities={
            TrendState.BULL: 0.6, TrendState.BEAR: 0.2, TrendState.RANGE: 0.1, TrendState.UNKNOWN: 0.1,
        },
        risk_probabilities={
            RiskState.RISK_ON: 0.5, RiskState.RISK_OFF: 0.2, RiskState.NEUTRAL: 0.2, RiskState.UNKNOWN: 0.1,
        },
        heat_probabilities={
            HeatState.NORMAL: 0.7, HeatState.OVERHEAT: 0.2, HeatState.UNKNOWN: 0.1,
        },
        confidence=Confidence.MEDIUM,
        evidence=(_anchor(),),
        conflicts=(),
        analog_case_ids=(),
        drivers=("breadth broadening",),
        evidence_factor_ids=("breadth.ad_ratio",),
        narrative="Breadth is broadening while heat stays contained.",
        unknown_reason=None,
    )
    base.update(over)
    return base


def _regime(**over) -> RegimeReport:
    return RegimeReport.build(**_regime_fields(**over))


def _mainline(
    name: str = "AI compute",
    universe_key: str = "theme.ai_compute",
    stage: RotationStage = RotationStage.SPREAD,
    strength: float = 6.0,
    evidence=None,
    **over,
) -> MainlineRead:
    base = dict(
        name=name,
        universe_key=universe_key,
        stage=stage,
        strength=strength,
        persistence="4 consecutive sessions of net inflow",
        evidence=evidence if evidence is not None else (_anchor("rot.hhi", 0.4, "concentration rising"),),
    )
    base.update(over)
    return MainlineRead(**base)


def _rotation_fields(**over):
    base = dict(
        as_of=AS_OF,
        factor_report_digest=DIGEST,
        mainlines=(
            _mainline("AI compute", "theme.ai_compute"),
            _mainline("Robotics", "theme.robotics", stage=RotationStage.START),
        ),
        confidence=Confidence.MEDIUM,
        conflicts=(),
        analog_case_ids=(),
        narrative="Two mainlines active; AI compute leads.",
        evidence_factor_ids=("rot.hhi",),
        unknown_reason=None,
    )
    base.update(over)
    return base


def _rotation(**over) -> RotationReport:
    return RotationReport.build(**_rotation_fields(**over))


# --------------------------------------------------------------------------- #
# baseline construction + frozen/extra-forbid                                  #
# --------------------------------------------------------------------------- #
def test_regime_baseline_constructs_and_is_frozen():
    r = _regime()
    assert r.trend is TrendState.BULL
    assert r.content_digest and r.content_digest != "0" * 64
    with pytest.raises(ValidationError):
        r.narrative = "mutate"  # frozen


def test_reports_forbid_extra_fields():
    with pytest.raises(ValidationError):
        _regime(junk=1)
    with pytest.raises(ValidationError):
        _rotation(junk=1)
    with pytest.raises(ValidationError):
        EvidenceAnchor(factor_id="a.b", value=1.0, reading="x", junk=1)  # type: ignore[call-arg]


# --------------------------------------------------------------------------- #
# axis key sets — exactly the enum's member set                                #
# --------------------------------------------------------------------------- #
def test_trend_label_injected_into_heat_probabilities_rejected():
    probs = {HeatState.NORMAL: 0.5, HeatState.OVERHEAT: 0.2, HeatState.UNKNOWN: 0.1, TrendState.BULL: 0.2}
    with pytest.raises(ValidationError):
        _regime(heat_probabilities=probs)


def test_missing_axis_label_rejected():
    probs = {TrendState.BULL: 0.7, TrendState.BEAR: 0.2, TrendState.RANGE: 0.1}  # missing UNKNOWN
    with pytest.raises(ValidationError):
        _regime(trend_probabilities=probs)


def test_extra_key_rejected():
    # a heat label as an extra key in the trend axis (foreign to dict[TrendState, ...])
    probs = {
        TrendState.BULL: 0.5, TrendState.BEAR: 0.2, TrendState.RANGE: 0.1,
        TrendState.UNKNOWN: 0.1, HeatState.NORMAL: 0.1,
    }
    with pytest.raises(ValidationError):
        _regime(trend_probabilities=probs)


# --------------------------------------------------------------------------- #
# sums + range                                                                 #
# --------------------------------------------------------------------------- #
def test_axis_sum_within_tolerance_accepted():
    probs = {TrendState.BULL: 0.6, TrendState.BEAR: 0.2, TrendState.RANGE: 0.1, TrendState.UNKNOWN: 0.1 + 5e-9}
    assert _regime(trend_probabilities=probs).content_digest


def test_trend_sum_off_by_2e8_rejected():
    probs = {TrendState.BULL: 0.6, TrendState.BEAR: 0.2, TrendState.RANGE: 0.1, TrendState.UNKNOWN: 0.1 + 2e-8}
    with pytest.raises(ValidationError):
        _regime(trend_probabilities=probs)


def test_risk_sum_off_by_2e8_rejected():
    probs = {RiskState.RISK_ON: 0.5, RiskState.RISK_OFF: 0.2, RiskState.NEUTRAL: 0.2, RiskState.UNKNOWN: 0.1 + 2e-8}
    with pytest.raises(ValidationError):
        _regime(risk_probabilities=probs)


def test_heat_sum_off_by_2e8_rejected():
    probs = {HeatState.NORMAL: 0.7, HeatState.OVERHEAT: 0.2, HeatState.UNKNOWN: 0.1 + 2e-8}
    with pytest.raises(ValidationError):
        _regime(heat_probabilities=probs)


def test_negative_probability_rejected():
    probs = {TrendState.BULL: -0.1, TrendState.BEAR: 0.5, TrendState.RANGE: 0.4, TrendState.UNKNOWN: 0.2}
    with pytest.raises(ValidationError):
        _regime(trend_probabilities=probs, trend=TrendState.BEAR)


def test_probability_above_one_rejected():
    probs = {TrendState.BULL: 1.5, TrendState.BEAR: 0.0, TrendState.RANGE: 0.0, TrendState.UNKNOWN: 0.0}
    with pytest.raises(ValidationError):
        _regime(trend_probabilities=probs)


def test_bool_probability_value_rejected():
    probs = {TrendState.BULL: True, TrendState.BEAR: 0.0, TrendState.RANGE: 0.0, TrendState.UNKNOWN: 0.0}
    with pytest.raises(ValidationError):
        _regime(trend_probabilities=probs)


# --------------------------------------------------------------------------- #
# modal fields == argmax (declaration-order tie-break)                          #
# --------------------------------------------------------------------------- #
def test_trend_modal_not_argmax_rejected():
    with pytest.raises(ValidationError):
        _regime(trend=TrendState.BEAR)  # argmax is BULL


def test_risk_modal_not_argmax_rejected():
    with pytest.raises(ValidationError):
        _regime(risk_state=RiskState.RISK_OFF)  # argmax is RISK_ON


def test_heat_modal_not_argmax_rejected():
    with pytest.raises(ValidationError):
        _regime(heat_state=HeatState.OVERHEAT)  # argmax is NORMAL


def test_argmax_tie_broken_by_declaration_order_accepted():
    probs = {TrendState.BULL: 0.4, TrendState.BEAR: 0.4, TrendState.RANGE: 0.1, TrendState.UNKNOWN: 0.1}
    r = _regime(trend_probabilities=probs, trend=TrendState.BULL)  # BULL declared first
    assert r.trend is TrendState.BULL


def test_argmax_tie_wrong_winner_rejected():
    probs = {TrendState.BULL: 0.4, TrendState.BEAR: 0.4, TrendState.RANGE: 0.1, TrendState.UNKNOWN: 0.1}
    with pytest.raises(ValidationError):
        _regime(trend_probabilities=probs, trend=TrendState.BEAR)  # loses the tie to BULL


def test_trend_state_chinese_values_pinned_r6():
    assert TrendState.BULL.value == "牛"
    assert TrendState.BEAR.value == "熊"
    assert TrendState.RANGE.value == "震荡"
    r = _regime()
    cj = canonical_json(r, projection="semantic")
    assert "牛" in cj and "熊" in cj and "震荡" in cj
    # the declared digest is genuinely sha256 over the canonical (UTF-8) bytes
    assert hashlib.sha256(cj.encode("utf-8")).hexdigest() == r.content_digest


# --------------------------------------------------------------------------- #
# unknown gating (honesty)                                                     #
# --------------------------------------------------------------------------- #
def test_unknown_at_threshold_requires_reason():
    probs = {TrendState.BULL: 0.5, TrendState.BEAR: 0.15, TrendState.RANGE: 0.1, TrendState.UNKNOWN: 0.25}
    with pytest.raises(ValidationError):
        _regime(trend_probabilities=probs, unknown_reason=None)
    r = _regime(trend_probabilities=probs, unknown_reason="short breadth coverage this read")
    assert r.unknown_reason


def test_unknown_below_threshold_forbids_reason():
    probs = {TrendState.BULL: 0.5, TrendState.BEAR: 0.16, TrendState.RANGE: 0.1, TrendState.UNKNOWN: 0.24}
    with pytest.raises(ValidationError):
        _regime(trend_probabilities=probs, unknown_reason="decorative, not evidence-driven")


def test_modal_unknown_with_medium_rejected_with_low_accepted():
    probs = {TrendState.BULL: 0.3, TrendState.BEAR: 0.2, TrendState.RANGE: 0.1, TrendState.UNKNOWN: 0.4}
    with pytest.raises(ValidationError):
        _regime(
            trend_probabilities=probs, trend=TrendState.UNKNOWN,
            unknown_reason="families genuinely disagree", confidence=Confidence.MEDIUM,
        )
    r = _regime(
        trend_probabilities=probs, trend=TrendState.UNKNOWN,
        unknown_reason="families genuinely disagree", confidence=Confidence.LOW,
    )
    assert r.trend is TrendState.UNKNOWN and r.confidence is Confidence.LOW


# --------------------------------------------------------------------------- #
# HIGH confidence caps                                                          #
# --------------------------------------------------------------------------- #
def test_high_confidence_with_all_axes_at_cap_accepted():
    r = _regime(confidence=Confidence.HIGH)  # baseline unknown = 0.10 on every axis
    assert r.confidence is Confidence.HIGH


def test_high_confidence_single_axis_over_cap_rejected():
    probs = {TrendState.BULL: 0.6, TrendState.BEAR: 0.19, TrendState.RANGE: 0.1, TrendState.UNKNOWN: 0.11}
    with pytest.raises(ValidationError):
        _regime(trend_probabilities=probs, confidence=Confidence.HIGH)


# --------------------------------------------------------------------------- #
# ④ RegimeReport additions                                                     #
# --------------------------------------------------------------------------- #
def test_malformed_factor_report_digest_rejected():
    with pytest.raises(ValidationError):
        _regime(factor_report_digest="not-a-valid-digest")


def test_empty_evidence_rejected():
    with pytest.raises(ValidationError):
        _regime(evidence=(), evidence_factor_ids=())


def test_evidence_factor_ids_mismatch_rejected():
    with pytest.raises(ValidationError):
        _regime(evidence=(_anchor("breadth.ad_ratio"),), evidence_factor_ids=("vol.rv",))


def test_evidence_factor_ids_equal_sorted_distinct_anchor_ids():
    r = _regime(
        evidence=(_anchor("vol.rv", 1.0), _anchor("breadth.ad_ratio", 0.7), _anchor("vol.rv", 1.0)),
        evidence_factor_ids=("breadth.ad_ratio", "vol.rv"),
    )
    assert r.evidence_factor_ids == ("breadth.ad_ratio", "vol.rv")


def test_drivers_must_be_sorted_and_dup_free():
    with pytest.raises(ValidationError):
        _regime(drivers=("z driver", "a driver"))
    with pytest.raises(ValidationError):
        _regime(drivers=("same driver", "same driver"))


def test_conflicts_and_analog_case_ids_default_empty():
    r = _regime()
    assert r.conflicts == () and r.analog_case_ids == ()


# --------------------------------------------------------------------------- #
# RotationReport / MainlineRead                                                #
# --------------------------------------------------------------------------- #
def test_rotation_constructs_and_tuple_order_is_ranking():
    r = _rotation()
    assert [m.name for m in r.mainlines] == ["AI compute", "Robotics"]
    assert "rank" not in MainlineRead.model_fields  # order is the ranking, no rank field


def test_duplicate_mainline_names_rejected():
    a = _mainline("Dup", "theme.a")
    b = _mainline("Dup", "theme.b")
    with pytest.raises(ValidationError):
        _rotation(mainlines=(a, b))


def test_mainline_stage_unknown_accepted():
    m = _mainline("Murky", "theme.murky", stage=RotationStage.UNKNOWN)
    r = _rotation(mainlines=(m,))
    assert r.mainlines[0].stage is RotationStage.UNKNOWN


def test_mainline_strength_bounds():
    assert _mainline("Max", "theme.max", strength=10.0).strength == 10.0
    with pytest.raises(ValidationError):
        _mainline("Over", "theme.over", strength=10.1)
    with pytest.raises(ValidationError):
        _mainline("Neg", "theme.neg", strength=-0.1)


def test_mainline_empty_persistence_rejected():
    with pytest.raises(ValidationError):
        _mainline("NoPersist", "theme.np", persistence="")


def test_mainline_chain_nodes_default_empty():
    assert _mainline("AI", "theme.ai").chain_nodes == ()


def test_empty_mainlines_with_reason_accepted():
    r = _rotation(mainlines=(), unknown_reason="themeless tape; no concentrated flow", evidence_factor_ids=())
    assert r.mainlines == () and r.unknown_reason


def test_empty_mainlines_without_reason_rejected():
    with pytest.raises(ValidationError):
        _rotation(mainlines=(), unknown_reason=None, evidence_factor_ids=())


def test_nonempty_mainlines_with_reason_rejected():
    with pytest.raises(ValidationError):
        _rotation(unknown_reason="must be absent when mainlines is non-empty")


# --------------------------------------------------------------------------- #
# invariants: not decision-class; legacy migration stays UNMAPPABLE            #
# --------------------------------------------------------------------------- #
def test_neither_schema_is_decision_class():
    from guanlan_v2.orchestration.spec import _DECISION_CLASS_SCHEMAS

    assert "RegimeReport" not in _DECISION_CLASS_SCHEMAS
    assert "RotationReport" not in _DECISION_CLASS_SCHEMAS


def test_legacy_rotation_stage_migration_stays_unmappable():
    from guanlan_v2.orchestration.enums import LegacyMarketCycleStage, MappingStatus
    from guanlan_v2.orchestration.migration import SRC_MARKET_CYCLE, migrate_rotation_stage

    res = migrate_rotation_stage(LegacyMarketCycleStage.FREEZE.value, source_schema=SRC_MARKET_CYCLE)
    assert res.mapping_status is MappingStatus.UNMAPPABLE
    assert res.normalized is None


# --------------------------------------------------------------------------- #
# exported SchemaRef constants                                                 #
# --------------------------------------------------------------------------- #
def test_exported_schema_ref_constants():
    assert (MARKET_FACTOR_REPORT_SCHEMA_REF.name, MARKET_FACTOR_REPORT_SCHEMA_REF.version) == ("MarketFactorReport", "1")
    assert (REGIME_REPORT_SCHEMA_REF.name, REGIME_REPORT_SCHEMA_REF.version) == ("RegimeReport", "1")
    assert (ROTATION_REPORT_SCHEMA_REF.name, ROTATION_REPORT_SCHEMA_REF.version) == ("RotationReport", "1")


def test_gating_constants_frozen():
    assert AXIS_SUM_TOLERANCE == 1e-8
    assert UNKNOWN_ATTENTION_THRESHOLD == 0.25
    assert HIGH_CONFIDENCE_UNKNOWN_MAX == 0.10


# --------------------------------------------------------------------------- #
# digest movement + self-digest on load                                        #
# --------------------------------------------------------------------------- #
def test_regime_digest_moves_on_semantic_change():
    a = _regime()
    assert a.content_digest != _regime(narrative="An entirely different narrative.").content_digest
    assert a.content_digest != _regime(confidence=Confidence.LOW).content_digest
    moved = _regime(trend_probabilities={
        TrendState.BULL: 0.55, TrendState.BEAR: 0.25, TrendState.RANGE: 0.1, TrendState.UNKNOWN: 0.1,
    })
    assert a.content_digest != moved.content_digest


def test_regime_self_digest_verifies_on_load():
    r = _regime()
    loaded = RegimeReport.model_validate_json(r.model_dump_json())
    assert loaded.content_digest == r.content_digest
    assert loaded.semantic_digest() == loaded.content_digest


def test_regime_tampered_digest_rejected():
    with pytest.raises(ValidationError):
        RegimeReport(**_regime_fields(), content_digest="0" * 64)


def test_rotation_digest_moves_on_mainline_change():
    a = _rotation()
    b = _rotation(mainlines=(
        _mainline("AI compute", "theme.ai_compute", strength=9.0),
        _mainline("Robotics", "theme.robotics", stage=RotationStage.START),
    ))
    assert a.content_digest != b.content_digest


def test_rotation_self_digest_verifies_on_load():
    r = _rotation()
    loaded = RotationReport.model_validate_json(r.model_dump_json())
    assert loaded.content_digest == r.content_digest
    assert loaded.semantic_digest() == loaded.content_digest


def test_rotation_tampered_digest_rejected():
    with pytest.raises(ValidationError):
        RotationReport(**_rotation_fields(), content_digest="0" * 64)
