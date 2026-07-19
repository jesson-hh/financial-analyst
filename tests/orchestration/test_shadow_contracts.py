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
import inspect
import math

import pytest
from pydantic import ValidationError

from guanlan_v2.orchestration import shadow
from guanlan_v2.orchestration.data.symbols import Symbol
from guanlan_v2.orchestration.digest import DigestModel
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
    # a permanently-unknown key (never a real field in any task) — tests the
    # inherited extra="forbid" behaviour without coupling to a future field name.
    with pytest.raises(ValidationError):
        _pos(0.5, not_a_real_field="x")


# --------------------------------------------------------------------------- #
# PortfolioTargetProposal — happy path + field surface                        #
# --------------------------------------------------------------------------- #
def test_proposal_accepts_a_valid_fully_invested_book():
    # band-legal weights (Task 1b): 0.25 + 0.25 + cash 0.5 == 1.
    prop = _proposal([_pos(0.25), _pos(0.25, code="000001", exchange="SZ")], 0.5)
    assert prop.cash_weight == 0.5
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
        [_pos(0.25), _pos(0.25, code="000001", exchange="SZ")], 0.5
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
    prop = _proposal([_pos(0.25), _pos(0.25, code="000001", exchange="SZ")], 0.5)
    # an accepted proposal is NEVER renormalized: the exact inputs survive.
    assert prop.positions[0].target_weight == 0.25
    assert prop.positions[1].target_weight == 0.25
    assert prop.cash_weight == 0.5


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


def test_closed_reason_code_set_is_exactly_the_six_members_after_task1b():
    # Task 1b extends the Task-1 closed five-member set by EXACTLY one member,
    # ``non_band_weight`` (the off-band target-weight rejection).
    assert shadow.PROPOSAL_REASON_CODES == frozenset({
        "duplicate_symbol", "non_finite_weight", "negative_weight",
        "weight_sum_violation", "leverage_or_short", "non_band_weight",
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


# =========================================================================== #
# Task 1b — closed target-weight band vocabulary + minimal TrancheTrigger       #
# =========================================================================== #

# --------------------------------------------------------------------------- #
# TARGET_WEIGHT_BANDS — the exported, immutable, single-source closed vocabulary #
# --------------------------------------------------------------------------- #
def test_target_weight_bands_is_the_closed_five_band_tuple():
    assert shadow.TARGET_WEIGHT_BANDS == (0.0, 0.25, 0.5, 0.75, 1.0)
    assert isinstance(shadow.TARGET_WEIGHT_BANDS, tuple)


def test_target_weight_bands_is_exported_in_all():
    # invariant 4: the band vocabulary is exported (Phase 8's allowed_actions
    # "maximum target-weight band" reuses this exact closed set).
    assert "TARGET_WEIGHT_BANDS" in shadow.__all__


def test_no_second_in_module_band_literal_copy():
    # invariant 4: exactly ONE literal definition of the band values exists in the
    # module — the validator reads the constant, never a parallel inlined copy.
    src = inspect.getsource(shadow)
    assert src.count("(0.0, 0.25, 0.5, 0.75, 1.0)") == 1


def test_band_validator_reads_the_exported_constant(monkeypatch):
    # widen the module constant to admit 0.3 → a 0.3 book now passes the band
    # check, proving the validator consumes TARGET_WEIGHT_BANDS (single source of
    # truth) rather than an inlined literal it would ignore the monkeypatch on.
    monkeypatch.setattr(
        shadow, "TARGET_WEIGHT_BANDS", (0.0, 0.25, 0.3, 0.5, 0.75, 1.0)
    )
    prop = _proposal([_pos(0.3)], 0.7)
    assert prop.positions[0].target_weight == 0.3


# --------------------------------------------------------------------------- #
# Band boundary matrix at the proposal layer (④ appended after ③ leverage)      #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("w", [0.0, 0.25, 0.5, 0.75, 1.0])
def test_proposal_accepts_every_target_weight_band(w):
    # invariant 1: every value in TARGET_WEIGHT_BANDS passes.
    prop = _proposal([_pos(w)], 1.0 - w)
    assert prop.positions[0].target_weight == w


@pytest.mark.parametrize(
    "bad", [0.3, 0.1, 0.2, 0.4, 0.6, 0.7, 0.8, 0.9, 0.123456, 0.99, 0.5000001]
)
def test_proposal_rejects_off_band_target_weight(bad):
    # invariant 1: any off-band value raises ProposalRejected's reason code, with
    # the code observable off the ValidationError — no snapping, no tolerance.
    with pytest.raises(ValidationError) as ei:
        _proposal([_pos(bad)], 1.0 - bad)
    assert "non_band_weight" in _error_types(ei.value)


def test_off_band_weight_is_never_snapped_to_the_nearest_band():
    # 0.3 sits nearer 0.25 than 0.5 but is NOT snapped — it is rejected outright.
    with pytest.raises(ValidationError) as ei:
        _proposal([_pos(0.3)], 0.7)
    assert "non_band_weight" in _error_types(ei.value)


def test_off_band_weight_among_otherwise_valid_bands_is_rejected():
    with pytest.raises(ValidationError) as ei:
        _proposal([_pos(0.5), _pos(0.3, code="000001", exchange="SZ")], 0.2)
    assert "non_band_weight" in _error_types(ei.value)


def test_all_band_multi_position_book_is_accepted():
    prop = _proposal([_pos(0.25), _pos(0.75, code="000001", exchange="SZ")], 0.0)
    assert len(prop.positions) == 2


# --------------------------------------------------------------------------- #
# Validator order — band check (④) runs AFTER ① duplicate, ② sum, ③ leverage    #
# --------------------------------------------------------------------------- #
def test_sum_violation_precedes_band_check():
    # a book that is BOTH sum-violating AND off-band surfaces weight_sum_violation
    # first (② precedes ④): the sequential-raise chain stops at the first breach.
    with pytest.raises(ValidationError) as ei:
        _proposal([_pos(0.3)], 0.5)  # sum 0.8 (off by 0.2) and 0.3 off-band
    types = _error_types(ei.value)
    assert "weight_sum_violation" in types
    assert "non_band_weight" not in types


def test_duplicate_precedes_band_check():
    # both duplicate AND off-band → duplicate surfaces first (① precedes ④).
    with pytest.raises(ValidationError) as ei:
        _proposal([_pos(0.3), _pos(0.3)], 0.4)  # dup 600519.SH, 0.3 off-band, sum 1.0
    types = _error_types(ei.value)
    assert "duplicate_symbol" in types
    assert "non_band_weight" not in types


def test_target_position_itself_stays_continuous_no_band_check():
    # placement pin: the band check lives at the proposal layer ONLY — a bare
    # TargetPosition with an off-band weight still constructs (Task 6's
    # deterministic lane needs continuous rule-computed weights).
    assert _pos(0.3).target_weight == 0.3
    assert _pos(0.123456).target_weight == 0.123456


def test_proposal_rejected_accepts_the_new_non_band_weight_code():
    err = shadow.ProposalRejected("non_band_weight")
    assert err.reason_code == "non_band_weight"
    assert isinstance(err, ValueError)


# --------------------------------------------------------------------------- #
# TrancheTrigger — minimal all-Optional frozen sub-model                        #
# --------------------------------------------------------------------------- #
def test_tranche_trigger_all_none_is_valid_and_constructible():
    # invariant 2: an all-None trigger is valid and constructible.
    t = shadow.TrancheTrigger()
    assert t.price_low is None
    assert t.price_high is None
    assert t.fraction is None


def test_tranche_trigger_fields_default_none_no_computed_default():
    # invariant 2: no field acquires a computed default on any path.
    for name in ("price_low", "price_high", "fraction"):
        assert shadow.TrancheTrigger.model_fields[name].default is None
        assert shadow.TrancheTrigger.model_fields[name].is_required() is False
    # a partially-specified trigger leaves the others honestly None.
    t = shadow.TrancheTrigger(price_low=10.0)
    assert t.price_low == 10.0
    assert t.price_high is None
    assert t.fraction is None


def test_tranche_trigger_accepts_finite_floats():
    t = shadow.TrancheTrigger(price_low=10.0, price_high=12.5, fraction=0.5)
    assert (t.price_low, t.price_high, t.fraction) == (10.0, 12.5, 0.5)


@pytest.mark.parametrize("field", ["price_low", "price_high", "fraction"])
@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_tranche_trigger_rejects_non_finite(field, bad):
    with pytest.raises(ValidationError):
        shadow.TrancheTrigger(**{field: bad})


def test_tranche_trigger_carries_no_own_schema_version():
    # it versions/digests through its host — no independent schema_version field.
    assert "schema_version" not in shadow.TrancheTrigger.model_fields


def test_tranche_trigger_is_a_frozen_digest_model_excluding_nothing():
    assert issubclass(shadow.TrancheTrigger, DigestModel)
    assert shadow.TrancheTrigger.SEMANTIC_EXCLUDE == frozenset()
    assert shadow.TrancheTrigger.SELF_DIGEST_FIELDS == frozenset()
    t = shadow.TrancheTrigger(price_low=10.0)
    with pytest.raises(ValidationError):
        t.price_low = 11.0  # frozen


def test_tranche_trigger_forbids_extra_fields():
    with pytest.raises(ValidationError):
        shadow.TrancheTrigger(not_a_real_field="x")


def test_tranche_trigger_is_exported_and_defined_in_shadow():
    assert "TrancheTrigger" in shadow.__all__
    assert shadow.TrancheTrigger.__module__ == "guanlan_v2.orchestration.shadow"


# --------------------------------------------------------------------------- #
# TargetPosition.entry_tranches attachment + digest sensitivity (invariant 3)   #
# --------------------------------------------------------------------------- #
def test_target_position_entry_tranches_defaults_to_empty_tuple():
    assert _pos(0.5).entry_tranches == ()


def test_target_position_field_set_now_includes_entry_tranches():
    assert set(shadow.TargetPosition.model_fields) == {
        "schema_version", "symbol", "target_weight", "stop_loss_pct",
        "take_profit_pct", "max_hold_bars", "entry_tranches",
    }


def test_proposal_field_set_unchanged_by_task1b():
    # entry_tranches lands on TargetPosition, NOT the proposal — the proposal
    # keeps exactly its five Task-1 fields.
    assert set(shadow.PortfolioTargetProposal.model_fields) == {
        "schema_version", "positions", "cash_weight", "rationale", "confidence",
    }


def test_target_position_accepts_entry_tranches_tuple():
    t = shadow.TrancheTrigger(price_low=10.0, fraction=0.5)
    p = _pos(0.5, entry_tranches=(t,))
    assert p.entry_tranches == (t,)
    assert p.entry_tranches[0].price_low == 10.0


def test_proposal_with_entry_tranches_is_accepted():
    t = shadow.TrancheTrigger(price_low=10.0, price_high=12.0, fraction=0.5)
    prop = _proposal([_pos(0.5, entry_tranches=(t,))], 0.5)
    assert prop.positions[0].entry_tranches == (t,)


def test_position_digest_is_sensitive_to_entry_tranches():
    # invariant 3: two positions differing only in entry_tranches differ in digest.
    base = _pos(0.5)
    withtranche = _pos(
        0.5, entry_tranches=(shadow.TrancheTrigger(price_low=10.0),)
    )
    assert withtranche.semantic_digest() != base.semantic_digest()


def test_position_digest_is_sensitive_to_a_single_tranche_field():
    a = _pos(0.5, entry_tranches=(shadow.TrancheTrigger(price_low=10.0),))
    b = _pos(0.5, entry_tranches=(shadow.TrancheTrigger(price_low=11.0),))
    assert a.semantic_digest() != b.semantic_digest()


def test_default_empty_entry_tranches_leaves_task1_digest_vectors_meaningful():
    # invariant 3 tail: an explicit empty tuple digests identically to the default,
    # so every Task-1 digest vector (built without entry_tranches) keeps its meaning.
    assert (
        _pos(0.5).semantic_digest() == _pos(0.5, entry_tranches=()).semantic_digest()
    )
