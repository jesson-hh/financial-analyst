"""Contract tests for causal daily selection and the research cash ledger."""
import json

import numpy as np
import pandas as pd
import pytest

from guanlan_v2.workflow.daily_simulator import simulate_daily_portfolio


def fixture(days=5, codes=("A", "B", "C"), price=10.0):
    calendar = pd.bdate_range("2025-01-02", periods=days)
    index = pd.MultiIndex.from_product([calendar, codes], names=["datetime", "instrument"])
    panel = pd.DataFrame({"open": float(price), "close": float(price)}, index=index)
    signals = pd.DataFrame({"score": 0.0, "eligible": False}, index=index)
    return panel, signals, calendar


def run(panel, signals, calendar, **kwargs):
    return simulate_daily_portfolio(panel, signals, calendar, initial_cash=1_000.0,
                                    commission=0.0, stamp_tax=0.0, slippage_bps=0.0, **kwargs)


def choose(signals, date, codes):
    signals.loc[(date, slice(None)), "eligible"] = False
    for rank, code in enumerate(codes):
        signals.loc[(date, code), ["eligible", "score"]] = [True, 100.0 - rank]


def test_signal_at_close_executes_next_open_not_same_day_close():
    panel, signals, calendar = fixture(days=3, codes=("A",))
    choose(signals, calendar[0], ["A"])
    panel.loc[(calendar[1], "A"), ["open", "close"]] = [20.0, 22.0]
    panel.loc[(calendar[2], "A"), ["open", "close"]] = [24.0, 900.0]
    result = run(panel, signals, calendar, top_n=1)
    buy, sell = result["trades"]
    assert buy["date"] == str(calendar[1].date())
    assert buy["signal_date"] == str(calendar[0].date())
    assert buy["reference_open"] == 20.0
    assert buy["quantity"] == 50.0
    assert sell["date"] == str(calendar[2].date())
    assert sell["execution_price"] == 24.0
    assert result["summary"]["final_equity"] == 1_200.0
    assert result["daily"][0]["nav"] == 1_000.0


def test_final_close_mark_to_market_does_not_force_sale_or_execute_final_signal():
    panel, signals, calendar = fixture(days=2, codes=("A", "B"))
    choose(signals, calendar[0], ["A"])
    choose(signals, calendar[1], ["B"])
    panel.loc[(calendar[1], "A"), "close"] = 11.0
    result = run(panel, signals, calendar, top_n=1)
    assert [trade["side"] for trade in result["trades"]] == ["buy"]
    assert result["summary"]["final_equity"] == 1_100.0
    assert result["summary"]["closed_position_win_rate"] is None
    assert result["candidates"][-1]["execution_date"] is None
    assert result["diagnostics"]["forced_final_liquidation"] is False


def test_prefix_invariance_even_when_future_prices_eligibility_and_ranking_change():
    panel, signals, calendar = fixture(days=7)
    for day in calendar:
        choose(signals, day, ["A", "B"])
    original = run(panel, signals, calendar, top_n=2)
    modified_panel = panel.copy()
    modified_signals = signals.copy()
    for day in calendar[4:]:
        modified_panel.loc[(day, slice(None)), "close"] = 500.0
        modified_panel.loc[(day, "A"), "open"] = np.nan
        choose(modified_signals, day, ["C"])
    future_changed = run(modified_panel, modified_signals, calendar, top_n=2)
    prefix = run(modified_panel, modified_signals, calendar[:4], top_n=2)
    assert original["daily"][:4] == future_changed["daily"][:4] == prefix["daily"]
    cutoff = str(calendar[3].date())
    assert [trade for trade in original["trades"] if trade["date"] <= cutoff] == prefix["trades"]
    for old, new in zip(original["candidates"][:4], prefix["candidates"]):
        assert old["selected"] == new["selected"]


def test_missing_exit_is_held_and_recovers_without_fake_execution_or_skipped_nav_day():
    panel, signals, calendar = fixture(days=5, codes=("A",))
    choose(signals, calendar[0], ["A"])
    panel.loc[(calendar[2], "A"), ["open", "close"]] = np.nan
    panel.loc[(calendar[3], "A"), ["open", "close"]] = [12.0, 13.0]
    result = run(panel, signals, calendar, top_n=1)
    assert len(result["daily"]) == 5
    missing_day = result["daily"][2]
    assert missing_day["nav"] == 1_000.0
    assert missing_day["holdings"][0]["mark_source"] == "last_observed_close"
    assert missing_day["holdings"][0]["stale_calendar_days"] == 1
    assert missing_day["holdings"][0]["pending_exit"] is True
    assert missing_day["trade_count"] == 0
    assert result["trades"][1]["date"] == str(calendar[3].date())
    assert result["trades"][1]["execution_price"] == 12.0
    assert result["summary"]["final_equity"] == 1_200.0
    assert result["diagnostics"]["stale_position_days"] == 1
    assert result["diagnostics"]["final_unresolved_positions"] == []


def test_missing_entire_quote_row_behaves_like_missing_prices_and_is_disclosed_at_end():
    panel, signals, calendar = fixture(days=3, codes=("A",))
    choose(signals, calendar[0], ["A"])
    panel = panel.drop(index=(calendar[2], "A"))
    result = run(panel, signals, calendar, top_n=1)
    assert result["daily"][-1]["nav"] == 1_000
    assert result["diagnostics"]["days_without_quote_rows"] == 1
    assert result["diagnostics"]["final_unresolved_positions"][0]["instrument"] == "A"
    assert len(result["trades"]) == 1


def test_unavailable_top_candidate_is_not_replaced_with_available_lower_rank():
    panel, signals, calendar = fixture(days=2, codes=("A", "B"))
    choose(signals, calendar[0], ["A", "B"])
    panel.loc[(calendar[1], "A"), "open"] = np.nan
    panel.loc[(calendar[1], "B"), "close"] = 1_000.0
    result = run(panel, signals, calendar, top_n=1)
    assert result["candidates"][0]["selected"] == [{"instrument": "A", "score": 100.0}]
    assert result["daily"][1]["selected"] == [{"instrument": "A", "score": 100.0}]
    assert not result["trades"]
    assert result["summary"]["final_cash"] == 1_000.0


def test_empty_selection_keeps_full_calendar_and_cash_without_dropping_dates():
    panel, signals, calendar = fixture(days=7)
    result = run(panel, signals, calendar, top_n=10)
    assert result["portfolio"]["nav_series"] == [[str(day.date()), 1.0] for day in calendar]
    assert result["portfolio"]["return_series"] == [[str(day.date()), 0.0] for day in calendar]
    assert result["summary"]["daily_win_rate"] == 0.0
    assert result["summary"]["sharpe"] is None
    assert result["summary"]["average_cash_weight"] == 1.0
    assert result["diagnostics"]["skipped_rebalance_days"] == 1  # Initial cash day only.
    assert not result["trades"]
    json.dumps(result, allow_nan=False)


def test_fewer_candidates_use_fixed_slots_not_full_investment():
    panel, signals, calendar = fixture(days=3)
    for date in calendar:
        choose(signals, date, ["A", "B"])
    result = run(panel, signals, calendar, top_n=10)
    assert result["daily"][1]["cash"] == 800.0
    assert result["daily"][1]["target_cash_weight"] == 0.8
    assert all(holding["weight"] == 0.1 for holding in result["daily"][1]["holdings"])
    assert result["summary"]["final_cash"] == 800.0


def test_cash_is_nonnegative_with_costs_and_equal_budget_shortfall_scaling():
    panel, signals, calendar = fixture(days=3, codes=("A", "B"))
    for date in calendar:
        choose(signals, date, ["A", "B"])
    result = simulate_daily_portfolio(panel, signals, calendar, top_n=2, initial_cash=1_000,
                                      commission=0.01, stamp_tax=0.01, slippage_bps=100)
    first = result["daily"][1]
    assert first["buy_budget_scale"] < 1
    assert [trade["quantity"] for trade in result["trades"][:2]] == pytest.approx([50 / 1.0201] * 2)
    assert all(row["cash"] >= 0 for row in result["daily"])
    assert all(trade["cash_after"] >= 0 for trade in result["trades"])
    for row in result["daily"]:
        assert row["cash"] + sum(holding["market_value"] for holding in row["holdings"]) == pytest.approx(row["nav"])


def test_commission_sell_tax_slippage_and_average_cost_closed_position_accounting():
    panel, signals, calendar = fixture(days=3, codes=("A",))
    choose(signals, calendar[0], ["A"])
    result = simulate_daily_portfolio(panel, signals, calendar, top_n=1, initial_cash=1_000,
                                      commission=0.01, stamp_tax=0.02, slippage_bps=100)
    buy, sell = result["trades"]
    quantity = 1_000 / (10.1 * 1.01)
    expected_cash = quantity * 9.9 * (1 - 0.01 - 0.02)
    assert buy["quantity"] == pytest.approx(quantity)
    assert buy["stamp_tax"] == 0
    assert sell["stamp_tax"] == pytest.approx(quantity * 9.9 * 0.02)
    assert result["summary"]["final_cash"] == pytest.approx(expected_cash)
    assert result["summary"]["costs_paid"] == pytest.approx(1_000 - expected_cash)
    assert result["closed_positions"][0]["realized_pnl"] == pytest.approx(expected_cash - 1_000)
    assert result["summary"]["closed_position_win_rate"] == 0.0
    assert result["summary"]["daily_win_rate"] == 0.0
    assert "not trade win rate" in result["summary"]["daily_win_rate_definition"]


def test_daily_rebalance_sells_overnight_inventory_before_buys_and_never_borrows():
    panel, signals, calendar = fixture(days=4, codes=("A", "B"))
    choose(signals, calendar[0], ["A"])
    choose(signals, calendar[1], ["B"])
    choose(signals, calendar[2], ["A"])
    result = run(panel, signals, calendar, top_n=1)
    middle = [trade for trade in result["trades"] if trade["date"] == str(calendar[2].date())]
    assert [(trade["instrument"], trade["side"]) for trade in middle] == [("A", "sell"), ("B", "buy")]
    for day in calendar:
        buys = {trade["instrument"] for trade in result["trades"] if trade["date"] == str(day.date()) and trade["side"] == "buy"}
        sells = {trade["instrument"] for trade in result["trades"] if trade["date"] == str(day.date()) and trade["side"] == "sell"}
        assert not buys.intersection(sells)


def test_blocked_holdings_prevent_target_weights_and_have_honest_concentration_drift():
    panel, signals, calendar = fixture(days=3, codes=("A", "B", "C"))
    choose(signals, calendar[0], ["A", "B"])
    choose(signals, calendar[1], ["B", "C"])
    panel.loc[(calendar[2], "A"), ["open", "close"]] = [np.nan, 30.0]
    result = run(panel, signals, calendar, top_n=2)
    last = result["daily"][-1]
    holdings = {row["instrument"]: row for row in last["holdings"]}
    assert set(holdings) == {"A", "B"}  # No cash to fund C; A cannot be fabricated as sold.
    assert holdings["A"]["weight"] == 0.75
    assert holdings["A"]["pending_exit"] is True
    assert last["buy_budget_scale"] == 0
    assert last["cash"] == 0
    assert result["summary"]["max_position_weight"] == 0.75


def test_explicit_opening_masks_hold_then_resume_and_require_provenance():
    panel, signals, calendar = fixture(days=4, codes=("A",))
    choose(signals, calendar[0], ["A"])
    panel["sellable"] = True
    panel.loc[(calendar[2], "A"), "sellable"] = False
    with pytest.raises(ValueError, match="tradability_provenance"):
        run(panel, signals, calendar, top_n=1)
    result = run(panel, signals, calendar, top_n=1, tradability_provenance="synthetic fixture known before opening")
    assert result["daily"][2]["holdings"][0]["stale_mark"] is False
    assert result["daily"][2]["blocked_orders"][0]["reason"] == "sellable_false_or_unknown"
    assert result["trades"][-1]["date"] == str(calendar[3].date())


def test_final_volume_or_daily_high_low_does_not_control_open_execution():
    panel, signals, calendar = fixture(days=2, codes=("A",))
    choose(signals, calendar[0], ["A"])
    panel["volume"] = 0
    panel["high"] = np.nan
    panel["low"] = np.nan
    result = run(panel, signals, calendar, top_n=1)
    assert len(result["trades"]) == 1
    assert result["diagnostics"]["opening_tradability_masks"] == []


def test_rank_ties_are_stable_code_order_and_scores_need_not_be_positive():
    panel, signals, calendar = fixture(days=2, codes=("C", "A", "B"))
    signals["eligible"] = True
    signals["score"] = -3.0
    result = run(panel.sample(frac=1, random_state=2), signals.sample(frac=1, random_state=3), calendar, top_n=2)
    assert [row["instrument"] for row in result["candidates"][0]["selected"]] == ["A", "B"]


def test_nonfinite_scores_are_disclosed_not_silently_ranked():
    panel, signals, calendar = fixture(days=2)
    signals["eligible"] = True
    signals.loc[(calendar[0], "A"), "score"] = np.inf
    signals.loc[(calendar[0], "B"), "score"] = np.nan
    result = run(panel, signals, calendar, top_n=2)
    assert result["candidates"][0]["rejected_nonfinite_scores"] == 2
    assert result["daily"][1]["cash"] == 500


def test_invalid_data_day_skips_next_rebalance_instead_of_liquidating_positions():
    panel, signals, calendar = fixture(days=4, codes=("A",))
    choose(signals, calendar[0], ["A"])
    validity = pd.Series([True, False, True, True], index=calendar)
    result = run(panel, signals, calendar, top_n=1, signal_valid=validity)
    assert result["daily"][2]["rebalance_skipped"] is True
    assert result["daily"][2]["skipped_rebalance_reason"] == "invalid_or_missing_prior_signal"
    assert result["daily"][2]["holdings_count"] == 1
    assert result["daily"][2]["trade_count"] == 0
    assert result["trades"][-1]["date"] == str(calendar[3].date())


def test_missing_selector_date_is_unknown_but_explicit_valid_empty_date_exits():
    panel, signals, calendar = fixture(days=3, codes=("A",))
    choose(signals, calendar[0], ["A"])
    signals = signals.drop(index=calendar[1], level="datetime")
    unknown = run(panel, signals, calendar, top_n=1)
    assert unknown["daily"][-1]["holdings_count"] == 1
    declared_empty = run(panel, signals, calendar, top_n=1, signal_valid={day: True for day in calendar})
    assert declared_empty["daily"][-1]["holdings_count"] == 0
    assert declared_empty["trades"][-1]["side"] == "sell"


def test_missing_current_close_never_uses_future_close_for_valuation():
    panel, signals, calendar = fixture(days=4, codes=("A",))
    for day in calendar:
        choose(signals, day, ["A"])
    panel.loc[(calendar[1], "A"), ["open", "close"]] = [20.0, np.nan]
    panel.loc[(calendar[2], "A"), ["open", "close"]] = [20.0, np.nan]
    panel.loc[(calendar[3], "A"), ["open", "close"]] = [20.0, 100.0]
    result = run(panel, signals, calendar, top_n=1)
    assert result["daily"][1]["holdings"][0]["mark"] == 10.0
    assert result["daily"][2]["holdings"][0]["mark"] == 10.0
    assert result["daily"][2]["holdings"][0]["stale_calendar_days"] == 2


def test_no_close_ever_observed_uses_disclosed_entry_open_not_zero_or_future_price():
    panel, signals, calendar = fixture(days=2, codes=("A",))
    panel["close"] = np.nan
    choose(signals, calendar[0], ["A"])
    result = run(panel, signals, calendar, top_n=1)
    holding = result["daily"][1]["holdings"][0]
    assert holding["mark"] == 10.0
    assert holding["mark_source"] == "entry_open_no_observed_close"
    assert holding["stale_mark"] is True
    assert result["diagnostics"]["final_unresolved_positions"]


def test_partial_sell_is_not_a_completed_trade_until_position_fully_closed():
    panel, signals, calendar = fixture(days=4, codes=("A", "B"))
    choose(signals, calendar[0], ["A"])
    choose(signals, calendar[1], ["A"])
    panel.loc[(calendar[2], "A"), ["open", "close"]] = [20.0, 20.0]
    panel.loc[(calendar[3], "A"), ["open", "close"]] = [20.0, 20.0]
    partial = run(panel, signals, calendar[:3], top_n=2)
    assert len(partial["trades"]) == 2
    assert partial["trades"][1]["side"] == "sell"
    assert partial["summary"]["closed_position_count"] == 0
    full = run(panel, signals, calendar, top_n=2)
    assert full["summary"]["closed_position_count"] == 1
    assert full["closed_positions"][0]["realized_pnl"] == 500
    assert full["summary"]["closed_position_win_rate"] == 1


def test_valid_empty_calendar_returns_a_json_safe_cash_ledger():
    panel, signals, _ = fixture()
    result = run(panel, signals, [], top_n=2)
    assert result["summary"]["final_cash"] == 1_000.0
    assert result["summary"]["n_periods"] == 0
    assert result["daily"] == []
    json.dumps(result, allow_nan=False)


@pytest.mark.parametrize("invalid", [0, -1, True, 1.5, 1001])
def test_top_n_is_bounded_integer(invalid):
    panel, signals, calendar = fixture()
    with pytest.raises(ValueError, match="top_n"):
        run(panel, signals, calendar, top_n=invalid)


@pytest.mark.parametrize("option", [{"commission": -1}, {"stamp_tax": np.nan}, {"slippage_bps": 10_000}, {"commission": 0.5, "stamp_tax": 0.5}, {"initial_cash": 0}])
def test_invalid_financial_assumptions_fail_closed(option):
    panel, signals, calendar = fixture()
    with pytest.raises(ValueError):
        simulate_daily_portfolio(panel, signals, calendar, **option)


def test_duplicate_rows_calendar_or_ambiguous_boolean_do_not_silently_pass():
    panel, signals, calendar = fixture()
    with pytest.raises(ValueError, match="duplicate"):
        run(pd.concat([panel, panel.iloc[:1]]), signals, calendar)
    with pytest.raises(ValueError, match="calendar"):
        run(panel, signals, calendar[::-1])
    with pytest.raises(ValueError, match="calendar"):
        run(panel, signals, [calendar[0], calendar[0]])
    signals["eligible"] = "false"
    with pytest.raises(ValueError, match="boolean"):
        run(panel, signals, calendar)


def test_legacy_code_index_alias_is_supported_without_mutating_inputs():
    panel, signals, calendar = fixture()
    panel.index = panel.index.set_names(["datetime", "code"])
    signals.index = signals.index.set_names(["datetime", "code"])
    old_panel, old_signals = panel.copy(), signals.copy()
    run(panel, signals, calendar)
    pd.testing.assert_frame_equal(panel, old_panel)
    pd.testing.assert_frame_equal(signals, old_signals)
