# -*- coding: utf-8 -*-
"""Phase 6 - Task 5 - the pure target-portfolio diff step.

Written test-first (RED until ``guanlan_v2.orchestration.adapters.luozi`` exists).
Exercises :func:`diff_target_portfolio` and the three internal frozen carriers
(:class:`ShadowOrderPlanEntry` / :class:`ShadowOrderSkip` / :class:`ShadowOrderPlan`)
against the closed diff rules of the brief:

* target qty per position = ``floor((target_weight * nav / price) / lot) * lot``;
* held symbols absent from the target -> full ``target_sell``;
* ``delta < 0`` -> ``target_sell`` of ``-delta`` (partial reduce or full exit);
* ``delta > 0`` -> ``target_buy`` of ``delta`` with ``cash_budget = delta * price``;
* ``delta == 0`` -> ``already_at_target`` skip;
* missing reference price -> ``no_reference_price`` skip (never a fabricated price);
* lot-floored target of 0 while ``target_weight > 0`` -> ``below_lot_resolution`` skip;
* canonical ordering: all sells (by ``Symbol.code``) then all buys (by ``Symbol.code``),
  ``ordinal`` incrementing 0.. across the whole plan; NO weight renormalization.

Run from repo root: ``pytest tests/orchestration/test_shadow_diff.py -v``.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone

import pytest

from guanlan_v2.orchestration import shadow
from guanlan_v2.orchestration.adapters.luozi import (
    ShadowOrderPlan,
    ShadowOrderPlanEntry,
    ShadowOrderSkip,
    diff_target_portfolio,
)
from guanlan_v2.orchestration.data.symbols import Symbol
from guanlan_v2.orchestration.digest import ContractModel, DigestModel
from guanlan_v2.orchestration.enums import Confidence

UTC = timezone.utc
_SCHED_UTC = datetime(2026, 7, 20, 1, 30, tzinfo=UTC)
_ELIGIBLE_UTC = datetime(2026, 7, 21, 1, 30, tzinfo=UTC)


# --------------------------------------------------------------------------- #
# builders                                                                    #
# --------------------------------------------------------------------------- #
def _sym(code: str = "600519", exchange: str = "SH", board: str = "main") -> Symbol:
    return Symbol(code=code, exchange=exchange, board=board)


def _pos(weight: float, *, code: str = "600519", exchange: str = "SH",
         board: str = "main", **kw):
    return shadow.TargetPosition(symbol=_sym(code, exchange, board),
                                 target_weight=weight, **kw)


def _intent(positions, cash_weight: float, **over):
    base = dict(
        intent_id="intent-1",
        target_version=1,
        proposal_artifact_id="art-prop-1",
        proposal_digest="a" * 64,
        source_decision_artifact_id="dec-art-1",
        decision_schedule_id="shadow.daily.ashare",
        decision_schedule_version="1",
        decision_schedule_digest="b" * 64,
        scheduled_for=_SCHED_UTC,
        decision_as_of=_SCHED_UTC,
        eligible_execution_at=_ELIGIBLE_UTC,
        positions=tuple(positions),
        cash_weight=cash_weight,
        rationale="thesis",
        confidence=Confidence.MEDIUM,
        created_at=datetime(2026, 7, 20, 2, 0, tzinfo=UTC),
    )
    base.update(over)
    return shadow.TargetPortfolioIntent(**base)


# --------------------------------------------------------------------------- #
# carrier shapes                                                              #
# --------------------------------------------------------------------------- #
def test_carriers_are_frozen_contract_models_not_digest_models():
    for carrier in (ShadowOrderPlanEntry, ShadowOrderSkip, ShadowOrderPlan):
        assert issubclass(carrier, ContractModel)
        assert not issubclass(carrier, DigestModel)  # unregistered internal carriers
        assert carrier.model_config.get("frozen") is True
        assert carrier.model_config.get("extra") == "forbid"

    assert set(ShadowOrderPlanEntry.model_fields) == {
        "symbol", "order_kind", "side", "qty", "cash_budget", "ordinal"
    }
    assert set(ShadowOrderSkip.model_fields) == {"symbol", "reason"}
    assert set(ShadowOrderPlan.model_fields) == {"entries", "skipped"}


def test_plan_entry_is_frozen():
    plan = diff_target_portfolio(
        _intent([_pos(0.5)], 0.5),
        holdings={},
        reference_prices={"600519.SH": 10.0},
        nav=10000.0,
    )
    entry = plan.entries[0]
    with pytest.raises(Exception):
        entry.ordinal = 99  # frozen


def test_skip_reason_is_a_closed_set():
    # a reason outside the closed vocabulary is rejected at construction.
    with pytest.raises(Exception):
        ShadowOrderSkip(symbol=_sym(), reason="totally_made_up")
    for reason in ("no_reference_price", "below_lot_resolution", "already_at_target"):
        assert ShadowOrderSkip(symbol=_sym(), reason=reason).reason == reason


# --------------------------------------------------------------------------- #
# the closed diff rules                                                        #
# --------------------------------------------------------------------------- #
def test_fresh_buy_sizes_by_lot_floor_and_cash_budget():
    plan = diff_target_portfolio(
        _intent([_pos(0.5)], 0.5),
        holdings={},
        reference_prices={"600519.SH": 10.0},
        nav=10000.0,
    )
    assert plan.skipped == ()
    assert len(plan.entries) == 1
    e = plan.entries[0]
    assert e.symbol == _sym("600519")
    assert e.order_kind == "target_buy"
    assert e.side == "buy"
    assert e.qty == 500  # floor(0.5*10000/10/100)*100
    assert e.cash_budget == 5000.0  # delta * price
    assert e.ordinal == 0


def test_full_sell_of_held_symbol_absent_from_target_needs_no_price():
    # 000001 held but not a target -> full sell, no reference price required.
    plan = diff_target_portfolio(
        _intent([_pos(0.5, code="600519")], 0.5),
        holdings={"000001.SZ": 200, "600519.SH": 0},
        reference_prices={"600519.SH": 10.0},  # no price for 000001
        nav=10000.0,
    )
    sells = [e for e in plan.entries if e.side == "sell"]
    buys = [e for e in plan.entries if e.side == "buy"]
    assert len(sells) == 1 and len(buys) == 1
    s = sells[0]
    assert s.symbol == _sym("000001", "SZ", "main")
    assert s.order_kind == "target_sell"
    assert s.qty == 200
    assert s.cash_budget is None
    # sells precede buys: ordinal ordering
    assert s.ordinal == 0
    assert buys[0].ordinal == 1


def test_partial_reduce_when_target_below_held():
    plan = diff_target_portfolio(
        _intent([_pos(0.25, code="600519")], 0.75),
        holdings={"600519.SH": 500},
        reference_prices={"600519.SH": 10.0},
        nav=10000.0,
    )
    assert len(plan.entries) == 1
    e = plan.entries[0]
    assert e.side == "sell"
    assert e.order_kind == "target_sell"
    # target = floor(0.25*10000/10/100)*100 = 200 ; delta = 200-500 = -300
    assert e.qty == 300
    assert e.cash_budget is None


def test_already_at_target_yields_skip_not_entry():
    plan = diff_target_portfolio(
        _intent([_pos(0.5, code="600519")], 0.5),
        holdings={"600519.SH": 500},
        reference_prices={"600519.SH": 10.0},
        nav=10000.0,
    )
    assert plan.entries == ()
    assert len(plan.skipped) == 1
    assert plan.skipped[0].reason == "already_at_target"
    assert plan.skipped[0].symbol == _sym("600519")


def test_missing_reference_price_is_an_honest_skip_never_fabricated():
    plan = diff_target_portfolio(
        _intent([_pos(0.5, code="600519")], 0.5),
        holdings={},
        reference_prices={},  # no price at all
        nav=10000.0,
    )
    assert plan.entries == ()
    assert len(plan.skipped) == 1
    assert plan.skipped[0].reason == "no_reference_price"


def test_below_lot_resolution_skip_when_positive_target_floors_to_zero():
    plan = diff_target_portfolio(
        _intent([_pos(0.25, code="600519")], 0.75),
        holdings={},
        reference_prices={"600519.SH": 10000.0},  # one lot dwarfs the tiny target
        nav=10000.0,
    )
    # 0.25*10000/10000 = 0.25 shares -> floor(0.25/100)*100 = 0, target_weight > 0
    assert plan.entries == ()
    assert len(plan.skipped) == 1
    assert plan.skipped[0].reason == "below_lot_resolution"


def test_lot_size_one_degenerates_to_whole_share_sizing():
    plan = diff_target_portfolio(
        _intent([_pos(0.25, code="600519")], 0.75),
        holdings={},
        reference_prices={"600519.SH": 33.0},
        nav=10000.0,
        lot_size=1,
    )
    # 0.25*10000/33 = 75.75... -> floor(/1)*1 = 75 whole shares
    assert len(plan.entries) == 1
    e = plan.entries[0]
    assert e.qty == 75
    assert e.cash_budget == pytest.approx(75 * 33.0)


def test_target_weight_zero_held_position_is_a_full_exit():
    plan = diff_target_portfolio(
        _intent([_pos(0.0, code="600519")], 1.0),
        holdings={"600519.SH": 400},
        reference_prices={"600519.SH": 10.0},
        nav=10000.0,
    )
    assert len(plan.entries) == 1
    e = plan.entries[0]
    assert e.side == "sell"
    assert e.qty == 400  # full held qty


# --------------------------------------------------------------------------- #
# ordering / ordinals / no-renormalization / determinism                       #
# --------------------------------------------------------------------------- #
def test_sells_precede_buys_and_ordinals_run_0_up_by_code():
    # two sells + two buys interleaved by symbol; canonical order = sells(by code)
    # then buys(by code), ordinals 0..3.
    positions = [
        _pos(0.5, code="600519"),  # held 200 -> buy (delta +300)
        _pos(0.0, code="600000"),  # held 100 -> full sell
        _pos(0.25, code="000001", exchange="SZ", board="main"),  # held 500 -> reduce
        _pos(0.25, code="002415", exchange="SZ", board="main"),  # held 0 -> buy
    ]
    plan = diff_target_portfolio(
        _intent(positions, 0.0),
        holdings={"600519.SH": 200, "600000.SH": 100, "000001.SZ": 500,
                  "300750.SZ": 300},  # 300750 held, absent from target -> full sell
        reference_prices={"600519.SH": 10.0, "600000.SH": 10.0, "000001.SZ": 10.0,
                          "002415.SZ": 10.0},
        nav=10000.0,
    )
    sides = [e.side for e in plan.entries]
    # all sells come before all buys
    assert sides == sorted(sides, key=lambda s: 0 if s == "sell" else 1)
    assert [e.ordinal for e in plan.entries] == list(range(len(plan.entries)))
    sells = [e for e in plan.entries if e.side == "sell"]
    buys = [e for e in plan.entries if e.side == "buy"]
    assert [e.symbol.code for e in sells] == sorted(e.symbol.code for e in sells)
    assert [e.symbol.code for e in buys] == sorted(e.symbol.code for e in buys)
    # 300750 (held, not a target) is a full sell of 300
    full_exit = [e for e in sells if e.symbol.code == "300750"]
    assert full_exit and full_exit[0].qty == 300


def test_no_weight_renormalization_each_buy_is_local():
    # A buy's cash_budget is exactly delta*price and does NOT depend on the
    # presence of other positions in the book (the engine legs->order path
    # re-normalizes buy weights over the batch; this diff never does).
    a_alone = diff_target_portfolio(
        _intent([_pos(0.5, code="600519")], 0.5),
        holdings={},
        reference_prices={"600519.SH": 10.0},
        nav=10000.0,
    )
    a_with_b = diff_target_portfolio(
        _intent([_pos(0.5, code="600519"), _pos(0.25, code="000001",
                exchange="SZ", board="main")], 0.25),
        holdings={},
        reference_prices={"600519.SH": 10.0, "000001.SZ": 10.0},
        nav=10000.0,
    )
    a1 = a_alone.entries[0]
    a2 = [e for e in a_with_b.entries if e.symbol.code == "600519"][0]
    assert a1.qty == a2.qty == 500
    assert a1.cash_budget == a2.cash_budget == 5000.0  # unscaled by B's presence


def test_diff_is_deterministic_independent_of_mapping_iteration_order():
    positions = [
        _pos(0.25, code="600519"),
        _pos(0.5, code="000001", exchange="SZ", board="main"),
    ]
    intent = _intent(positions, 0.25)
    holdings_a = {"600519.SH": 500, "000001.SZ": 100, "300750.SZ": 200}
    prices_a = {"600519.SH": 10.0, "000001.SZ": 10.0}
    # same content, reversed insertion order
    holdings_b = {k: holdings_a[k] for k in reversed(list(holdings_a))}
    prices_b = {k: prices_a[k] for k in reversed(list(prices_a))}

    plan_a = diff_target_portfolio(intent, holdings=holdings_a,
                                   reference_prices=prices_a, nav=10000.0)
    plan_b = diff_target_portfolio(intent, holdings=holdings_b,
                                   reference_prices=prices_b, nav=10000.0)
    assert plan_a == plan_b  # identical including ordinals and skips
    # and stable across repeated calls
    assert plan_a == diff_target_portfolio(intent, holdings=holdings_a,
                                           reference_prices=prices_a, nav=10000.0)


def test_idempotent_over_a_conforming_portfolio():
    positions = [
        _pos(0.5, code="600519"),
        _pos(0.25, code="000001", exchange="SZ", board="main"),
    ]
    plan = diff_target_portfolio(
        _intent(positions, 0.25),
        # exactly the lot-floored targets: 0.5 -> 500, 0.25 -> floor(250/100)*100 = 200
        holdings={"600519.SH": 500, "000001.SZ": 200},
        reference_prices={"600519.SH": 10.0, "000001.SZ": 10.0},
        nav=10000.0,
    )
    assert plan.entries == ()
    assert {s.reason for s in plan.skipped} == {"already_at_target"}
    assert len(plan.skipped) == 2


def test_key_form_robustness_engine_bare_and_dotted_all_resolve():
    # a held symbol keyed by the engine_code form still exits cleanly.
    plan = diff_target_portfolio(
        _intent([_pos(0.0, code="600519")], 1.0),
        holdings={"SH600519": 300},  # engine_code form
        reference_prices={"SH600519": 10.0},
        nav=10000.0,
    )
    assert len(plan.entries) == 1
    assert plan.entries[0].symbol == _sym("600519")
    assert plan.entries[0].qty == 300
