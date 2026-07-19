# -*- coding: utf-8 -*-
"""Phase 6 · Task 1 — shadow proposal contract tests (`TargetPosition` /
`PortfolioTargetProposal`).

Written test-first (RED until ``guanlan_v2.orchestration.shadow`` and its two
proposal contracts exist). The proposal is the ONLY LLM-writable payload in the
shadow phase; these tests pin the whole validation matrix (each closed reason
code observable), envelope-field smuggling rejection, the exact weight-sum
boundary tolerance vectors, digest stability across construction order, and the
completeness-firewall scoping of the new module.

Run from repo root: ``pytest tests/orchestration/test_shadow_contracts.py -v``
"""
from __future__ import annotations

import importlib
import math

import pytest
from pydantic import ValidationError

from guanlan_v2.orchestration import shadow
from guanlan_v2.orchestration.data.symbols import Symbol
from guanlan_v2.orchestration.enums import Confidence


# --------------------------------------------------------------------------- #
# builders                                                                    #
# --------------------------------------------------------------------------- #
def _sym(code: str = "600519", exchange: str = "SH", board: str = "main") -> Symbol:
    return Symbol(code=code, exchange=exchange, board=board)


def _pos(weight: float, *, code: str = "600519", exchange: str = "SH",
         board: str = "main", **kw):
    return shadow.TargetPosition(
        symbol=_sym(code, exchange, board), target_weight=weight, **kw
    )


def _proposal(positions, cash_weight, *, rationale="fully-invested thesis",
              confidence=Confidence.MEDIUM):
    return shadow.PortfolioTargetProposal(
        positions=tuple(positions),
        cash_weight=cash_weight,
        rationale=rationale,
        confidence=confidence,
    )


def _error_types(exc: ValidationError) -> set[str]:
    return {e["type"] for e in exc.errors()}


# --------------------------------------------------------------------------- #
# TargetPosition field surface                                                #
# --------------------------------------------------------------------------- #
def test_target_position_schema_version_is_frozen_literal_1():
    assert shadow.TargetPosition.model_fields["schema_version"].default == "1"
    # a payload cannot self-report a different version.
    with pytest.raises(ValidationError):
        shadow.TargetPosition(schema_version="2", symbol=_sym(), target_weight=0.5)


def test_target_position_minimal_has_optional_none_defaults():
    p = _pos(0.5)
    assert p.target_weight == 0.5
    assert p.stop_loss_pct is None
    assert p.take_profit_pct is None
    assert p.max_hold_bars is None


def test_target_position_target_weight_required():
    with pytest.raises(ValidationError):
        shadow.TargetPosition(symbol=_sym())


@pytest.mark.parametrize("bad", [-0.01, 1.01, float("nan"), float("inf"), float("-inf")])
def test_target_position_rejects_out_of_range_or_non_finite_weight(bad):
    with pytest.raises(ValidationError):
        _pos(bad)


@pytest.mark.parametrize("w", [0.0, 0.25, 0.5, 0.75, 1.0, 0.3, 0.123456])
def test_target_position_weight_is_continuous_at_this_layer(w):
    # Task 1: no band constraint on TargetPosition itself (bands land at the
    # proposal/intent layer in Task 1b) — any [0,1] finite weight is accepted.
    assert _pos(w).target_weight == w


def test_target_position_optional_exit_param_bounds():
    # stop_loss_pct in (0, 1]; take_profit_pct > 0; max_hold_bars positive int.
    assert _pos(0.5, stop_loss_pct=0.08).stop_loss_pct == 0.08
    assert _pos(0.5, take_profit_pct=0.25).take_profit_pct == 0.25
    assert _pos(0.5, take_profit_pct=3.0).take_profit_pct == 3.0  # > 1 allowed
    assert _pos(0.5, max_hold_bars=10).max_hold_bars == 10
    for kw in ({"stop_loss_pct": 0.0}, {"stop_loss_pct": 1.5}, {"stop_loss_pct": -0.1},
               {"take_profit_pct": 0.0}, {"take_profit_pct": -1.0},
               {"max_hold_bars": 0}, {"max_hold_bars": -1}):
        with pytest.raises(ValidationError):
            _pos(0.5, **kw)


def test_target_position_forbids_extra_fields():
    with pytest.raises(ValidationError):
        _pos(0.5, entry_tranches=())  # Task 1b field; not present at Task 1


# --------------------------------------------------------------------------- #
# PortfolioTargetProposal — happy path + field surface                        #
# --------------------------------------------------------------------------- #
def test_proposal_accepts_a_valid_fully_invested_book():
    prop = _proposal([_pos(0.3), _pos(0.3, code="000001", exchange="SZ")], 0.4)
    assert prop.cash_weight == 0.4
    assert len(prop.positions) == 2
    assert prop.confidence is Confidence.MEDIUM
    assert prop.schema_version == "1"


def test_proposal_all_cash_is_valid():
    assert _proposal([], 1.0).positions == ()


def test_proposal_schema_version_frozen_literal_1():
    assert shadow.PortfolioTargetProposal.model_fields["schema_version"].default == "1"
    with pytest.raises(ValidationError):
        shadow.PortfolioTargetProposal(
            schema_version="9", positions=(), cash_weight=1.0,
            rationale="x", confidence=Confidence.LOW,
        )


def test_proposal_rationale_must_be_non_blank():
    with pytest.raises(ValidationError):
        _proposal([_pos(0.5)], 0.5, rationale="   ")


# --------------------------------------------------------------------------- #
# The model-validator reason-code matrix (① duplicate ② sum ③ leverage)        #
# --------------------------------------------------------------------------- #
def test_duplicate_symbol_by_code_and_exchange_is_rejected():
    with pytest.raises(ValidationError) as ei:
        _proposal([_pos(0.3), _pos(0.3)], 0.4)  # same 600519.SH twice
    assert "duplicate_symbol" in _error_types(ei.value)


def test_duplicate_key_is_code_plus_exchange_ignoring_board():
    # same code+exchange, different board → still a duplicate (dedup key is the
    # (code, exchange) pair per the pinned validator, not the whole Symbol).
    with pytest.raises(ValidationError) as ei:
        _proposal(
            [_pos(0.3, board="main"), _pos(0.3, board="star")], 0.4
        )
    assert "duplicate_symbol" in _error_types(ei.value)


def test_distinct_symbols_are_not_flagged_duplicate():
    prop = _proposal(
        [_pos(0.3), _pos(0.3, code="000001", exchange="SZ")], 0.4
    )
    assert len(prop.positions) == 2


def test_weight_sum_violation_is_rejected_and_never_renormalized():
    # sum(weights)+cash = 0.9, off by 0.1 >> tolerance.
    with pytest.raises(ValidationError) as ei:
        _proposal([_pos(0.5)], 0.4)
    assert "weight_sum_violation" in _error_types(ei.value)


def test_under_and_over_allocation_both_violate_the_sum_identity():
    with pytest.raises(ValidationError) as ei_under:
        _proposal([_pos(0.2)], 0.2)  # sum 0.4
    assert "weight_sum_violation" in _error_types(ei_under.value)
    with pytest.raises(ValidationError) as ei_over:
        _proposal([_pos(0.5), _pos(0.5, code="000001", exchange="SZ")], 0.3)  # 1.3
    assert "weight_sum_violation" in _error_types(ei_over.value)


def test_duplicate_check_precedes_sum_check():
    # a book that is BOTH duplicated and sum-violating surfaces duplicate_symbol
    # first (pinned validator order ① before ②).
    with pytest.raises(ValidationError) as ei:
        _proposal([_pos(0.9), _pos(0.9)], 0.9)
    assert "duplicate_symbol" in _error_types(ei.value)


# --------------------------------------------------------------------------- #
# Boundary tolerance vectors — exact at 1e-8                                   #
# --------------------------------------------------------------------------- #
def test_weight_sum_tolerance_constant_is_1e_8():
    assert shadow.WEIGHT_SUM_TOLERANCE == 1e-8


def test_sum_within_tolerance_is_accepted_1_minus_1e_9():
    # sum + cash = 1 - 1e-9  → |diff| = 1e-9 ≤ 1e-8 → accepted.
    prop = _proposal([_pos(0.5)], 0.5 - 1e-9)
    assert math.isclose(prop.positions[0].target_weight + prop.cash_weight, 1.0,
                        abs_tol=1e-8)


def test_sum_just_outside_tolerance_is_rejected_1_minus_1e_7():
    # sum + cash = 1 - 1e-7  → |diff| = 1e-7 > 1e-8 → weight_sum_violation.
    with pytest.raises(ValidationError) as ei:
        _proposal([_pos(0.5)], 0.5 - 1e-7)
    assert "weight_sum_violation" in _error_types(ei.value)


def test_no_normalization_side_channel_weights_reread_byte_identical():
    prop = _proposal([_pos(0.3), _pos(0.3, code="000001", exchange="SZ")], 0.4)
    # an accepted proposal is NEVER renormalized: the exact inputs survive.
    assert prop.positions[0].target_weight == 0.3
    assert prop.positions[1].target_weight == 0.3
    assert prop.cash_weight == 0.4


# --------------------------------------------------------------------------- #
# ③ long-only leverage guard (defense-in-depth; subsumed by ② for cash ≥ 0)    #
# --------------------------------------------------------------------------- #
def test_aggregate_leverage_input_fails_construction():
    # sum(weights) = 1.2 > 1 with cash 0 → rejected before any staging. Because
    # ② (sum-identity) precedes ③ and cash_weight ≥ 0, the surfaced code is
    # weight_sum_violation; leverage_or_short is the long-only guard's named
    # reason (see the closed-set test) and is subsumed here.
    with pytest.raises(ValidationError) as ei:
        _proposal([_pos(0.6), _pos(0.6, code="000001", exchange="SZ")], 0.0)
    assert "weight_sum_violation" in _error_types(ei.value)


def test_short_and_negative_weights_are_impossible_by_field_bounds():
    with pytest.raises(ValidationError):
        _pos(-0.5)  # ge=0 makes a short leg impossible at the field layer


# --------------------------------------------------------------------------- #
# Invariant 1 — zero envelope fields; extra="forbid" blocks smuggling          #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("field", ["intent_id", "authority", "scheduled_for",
                                  "decision_schedule_ref", "run_id"])
def test_proposal_rejects_smuggled_envelope_fields(field):
    with pytest.raises(ValidationError) as ei:
        shadow.PortfolioTargetProposal(
            positions=(_pos(0.5),), cash_weight=0.5, rationale="x",
            confidence=Confidence.LOW, **{field: "smuggled"},
        )
    assert "extra_forbidden" in _error_types(ei.value)


def test_proposal_carries_zero_envelope_fields():
    fields = set(shadow.PortfolioTargetProposal.model_fields)
    assert fields == {"schema_version", "positions", "cash_weight", "rationale",
                      "confidence"}
    for envelope in ("intent_id", "authority", "scheduled_for",
                     "decision_schedule_ref", "run_id"):
        assert envelope not in fields


# --------------------------------------------------------------------------- #
# Invariant 4 — semantic digest stable across construction order, excludes none #
# --------------------------------------------------------------------------- #
def test_semantic_digest_is_stable_across_construction_order():
    a = shadow.PortfolioTargetProposal(
        positions=(_pos(0.5),), cash_weight=0.5, rationale="thesis",
        confidence=Confidence.HIGH,
    )
    # same logical value, kwargs supplied in a different order.
    b = shadow.PortfolioTargetProposal(
        confidence=Confidence.HIGH, rationale="thesis", cash_weight=0.5,
        positions=(_pos(0.5),),
    )
    assert a.semantic_digest() == b.semantic_digest()


def test_semantic_digest_excludes_nothing():
    assert shadow.PortfolioTargetProposal.SEMANTIC_EXCLUDE == frozenset()
    assert shadow.PortfolioTargetProposal.SELF_DIGEST_FIELDS == frozenset()
    assert shadow.TargetPosition.SEMANTIC_EXCLUDE == frozenset()
    assert shadow.TargetPosition.SELF_DIGEST_FIELDS == frozenset()


def test_semantic_digest_is_sensitive_to_every_proposal_field():
    base = _proposal([_pos(0.5)], 0.5)
    variants = [
        _proposal([_pos(0.75)], 0.25),                       # weight
        _proposal([_pos(0.5)], 0.5, rationale="different"),  # rationale
        _proposal([_pos(0.5)], 0.5, confidence=Confidence.HIGH),  # confidence
        _proposal([_pos(0.5, code="000001", exchange="SZ")], 0.5),  # symbol
    ]
    for v in variants:
        assert v.semantic_digest() != base.semantic_digest()


def test_position_digest_is_sensitive_to_exit_params():
    base = _pos(0.5)
    assert _pos(0.5, stop_loss_pct=0.1).semantic_digest() != base.semantic_digest()
    assert _pos(0.5, take_profit_pct=0.2).semantic_digest() != base.semantic_digest()
    assert _pos(0.5, max_hold_bars=5).semantic_digest() != base.semantic_digest()


# --------------------------------------------------------------------------- #
# Exception hierarchy + the closed reason-code vocabulary                       #
# --------------------------------------------------------------------------- #
def test_shadow_error_hierarchy():
    assert issubclass(shadow.ShadowContractError, ValueError)
    assert issubclass(shadow.ProposalRejected, shadow.ShadowContractError)


def test_closed_reason_code_set_is_exactly_the_five_task1_members():
    assert shadow.PROPOSAL_REASON_CODES == frozenset({
        "duplicate_symbol", "non_finite_weight", "negative_weight",
        "weight_sum_violation", "leverage_or_short",
    })
    assert isinstance(shadow.PROPOSAL_REASON_CODES, frozenset)


def test_proposal_rejected_carries_each_closed_reason_code():
    for code in shadow.PROPOSAL_REASON_CODES:
        err = shadow.ProposalRejected(code)
        assert err.reason_code == code
        assert isinstance(err, ValueError)


def test_proposal_rejected_refuses_an_out_of_vocabulary_reason_code():
    with pytest.raises(ValueError):
        shadow.ProposalRejected("not_a_real_reason")


# --------------------------------------------------------------------------- #
# Invariant 5 — the completeness firewall scopes shadow.py and its classes      #
# --------------------------------------------------------------------------- #
def test_shadow_module_is_scoped_into_phase6_and_excluded_from_phase1():
    tcc = importlib.import_module("tests.orchestration.test_contract_completeness")
    assert "guanlan_v2.orchestration.shadow" in tcc.PHASE6_MODULES

    defining = tcc._modules_defining_public_contract_models()
    assert "guanlan_v2.orchestration.shadow" in defining, (
        "the disk walk must discover shadow.py as a public-contract module"
    )
    missing = (
        defining
        - set(tcc.PHASE1_MODULES)
        - set(tcc.PHASE4_MODULES)
        - set(tcc.PHASE5_MODULES)
        - set(tcc.PHASE6_MODULES)
    )
    assert "guanlan_v2.orchestration.shadow" not in missing

    # the two Phase-6 contracts are NOT classified into the Phase-1 buckets.
    phase1_classified = {
        m.__name__ for m in set(tcc.PHASE1_PUBLIC_MODELS) | set(tcc.INTERNAL_MODELS)
    }
    assert "TargetPosition" not in phase1_classified
    assert "PortfolioTargetProposal" not in phase1_classified


def test_phase6_names_defined_only_in_the_shadow_module():
    # the two Task-1 classes are defined by shadow.py (and nowhere else).
    assert shadow.TargetPosition.__module__ == "guanlan_v2.orchestration.shadow"
    assert shadow.PortfolioTargetProposal.__module__ == "guanlan_v2.orchestration.shadow"
