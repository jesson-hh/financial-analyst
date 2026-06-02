"""P1 backtest engine — VirtualPortfolio + Position hand-computable cases.

Covers design §5 cases #1-5, #11-12, #15:
cash deduction with costs, mark-to-market NAV, sell with stamp duty,
T+1 same-day-sell rejection, min commission floor, stop-loss protective fill,
stop-loss gap-down fill, realized-pnl self-consistency.

All toy bars; default CostModel; slippage 0 for hand-computable cases.
Float tolerance 1e-6 (to the fen / cent).
"""
import pytest

from financial_analyst.backtest import (
    VirtualPortfolio,
    Position,
    Broker,
    Order,
    CostModel,
)

TOL = 1e-6


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _bar(open_, high, low, close, vol=1_000_000, trade_date="2026-05-06"):
    return {
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "vol": vol,
        "trade_date": trade_date,
    }


def _no_slip_broker():
    return Broker(cost_model=CostModel(slippage_bps=0.0))


# ---------------------------------------------------------------------------
# #1 buy deducts cash with costs
# ---------------------------------------------------------------------------


def test_buy_deducts_cash_with_costs():
    p = VirtualPortfolio()
    fill = p.buy("SH600000", 1000, 10.00, "2026-05-06")
    assert fill is not None
    # gross=10000; commission=max(2.5,5)=5; transfer(SH)=10000*0.0001=1.0; buy_cost=6.0
    assert p.cash == pytest.approx(1_000_000 - 10000 - 6.0, abs=TOL)  # 989994.0
    assert p.cash == pytest.approx(989994.0, abs=TOL)
    pos = p.positions["SH600000"]
    assert pos.qty == 1000
    # avg_cost = (10000 + 6) / 1000 = 10.006
    assert pos.avg_cost == pytest.approx(10.006, abs=TOL)
    # buy must not touch realized pnl
    assert p.realized_pnl_total == pytest.approx(0.0, abs=TOL)


# ---------------------------------------------------------------------------
# #2 mark-to-market NAV
# ---------------------------------------------------------------------------


def test_mark_to_market_nav():
    p = VirtualPortfolio()
    p.buy("SH600000", 1000, 10.00, "2026-05-06")  # cash 989994.0
    nav = p.record_nav("2026-05-06", prices={"SH600000": 11.00})
    # mkt_value = 11000; nav = 989994 + 11000 = 1000994.0
    assert p.positions["SH600000"].mkt_value == pytest.approx(11000.0, abs=TOL)
    assert nav == pytest.approx(1_000_994.0, abs=TOL)
    assert p.nav_history[-1][1] == pytest.approx(1_000_994.0, abs=TOL)


# ---------------------------------------------------------------------------
# #3 sell adds cash with stamp duty
# ---------------------------------------------------------------------------


def test_sell_adds_cash_with_stamp():
    p = VirtualPortfolio()
    p.buy("SH600000", 1000, 10.00, "2026-05-06")  # cash 989994.0
    fill = p.sell("SH600000", 1000, 11.00, "2026-05-07")
    assert fill is not None
    # gross=11000; commission=max(2.75,5)=5; stamp=11000*0.0005=5.5; transfer=1.1
    # sell_cost=11.6; cash = 989994 + (11000 - 11.6) = 1000982.4
    assert p.cash == pytest.approx(1_000_982.4, abs=TOL)
    assert "SH600000" not in p.positions  # cleared


# ---------------------------------------------------------------------------
# #4 T+1 same-day sell rejected
# ---------------------------------------------------------------------------


def test_t1_same_day_sell_rejected():
    p = VirtualPortfolio()
    p.buy("SH600000", 1000, 10.00, "2026-05-06")
    # sellable today == 0 (bought today)
    assert p.positions["SH600000"].sellable("2026-05-06") == 0
    fill = p.sell("SH600000", 1000, 11.00, "2026-05-06")
    assert fill is None
    # cash and qty unchanged from post-buy
    assert p.cash == pytest.approx(989994.0, abs=TOL)
    assert p.positions["SH600000"].qty == 1000


def test_t1_via_broker_rejected():
    """Same-day sell through Broker.match also returns None reason t1_locked."""
    p = VirtualPortfolio()
    p.buy("SH600000", 1000, 10.00, "2026-05-06")
    brk = _no_slip_broker()
    bar = _bar(10.0, 10.2, 9.8, 10.0, trade_date="2026-05-06")
    order = Order(code="SH600000", side="sell", otype="limit", limit_price=9.0, qty=1000)
    fill = brk.match(order, bar, prev_close=10.0, portfolio=p)
    assert fill is None


def test_t1_next_day_sellable():
    """Bought 2026-05-06 → sellable on 2026-05-07."""
    p = VirtualPortfolio()
    p.buy("SH600000", 1000, 10.00, "2026-05-06")
    assert p.positions["SH600000"].sellable("2026-05-07") == 1000


# ---------------------------------------------------------------------------
# #5 min commission floor
# ---------------------------------------------------------------------------


def test_min_commission_floor():
    p = VirtualPortfolio()
    fill = p.buy("SH600000", 100, 5.00, "2026-05-06")
    assert fill is not None
    # gross=500; commission=max(0.125,5)=5.0; transfer=500*0.0001=0.05; cost=5.05
    # cash = 1000000 - 505.05 = 999494.95
    assert p.cash == pytest.approx(999_494.95, abs=TOL)


# ---------------------------------------------------------------------------
# #11 stop-loss in range uses protective price (min(stop, open))
# ---------------------------------------------------------------------------


def test_stop_loss_in_range_uses_protective_px():
    p = VirtualPortfolio()
    # seed a position bought yesterday (sellable today)
    p.buy("SH600000", 1000, 9.80, "2026-05-05")
    brk = _no_slip_broker()
    # stop=9.50; bar open=9.45/high=9.60/low=9.40 → low<=stop triggers
    bar = _bar(9.45, 9.60, 9.40, 9.50, trade_date="2026-05-06")
    order = Order(code="SH600000", side="sell", otype="stop", limit_price=9.50, qty=1000)
    fill = brk.match(order, bar, prev_close=10.0, portfolio=p)
    assert fill is not None
    # fill = clip(min(9.50, 9.45), dn, up) = 9.45 (protective open side, NOT optimistic 9.50)
    assert fill.price == pytest.approx(9.45, abs=TOL)
    assert fill.side == "sell"
    assert "SH600000" not in p.positions


# ---------------------------------------------------------------------------
# #12 stop-loss gap-down fills at open (limit-sell would have missed it)
# ---------------------------------------------------------------------------


def test_stop_loss_gap_down_fills_at_open():
    p = VirtualPortfolio()
    p.buy("SH600000", 1000, 9.80, "2026-05-05")
    brk = _no_slip_broker()
    # stop=9.50; gap-down bar open=9.00/high=9.20/low=8.80; ref_prev=10 → dn=9.00
    bar = _bar(9.00, 9.20, 8.80, 9.00, trade_date="2026-05-06")
    order = Order(code="SH600000", side="sell", otype="stop", limit_price=9.50, qty=1000)
    fill = brk.match(order, bar, prev_close=10.0, portfolio=p)
    assert fill is not None
    # low=8.80<=9.50 triggers; fill = clip(min(9.50,9.00), dn=9.00, up) = 9.00; MUST fill
    assert fill.price == pytest.approx(9.00, abs=TOL)
    assert "SH600000" not in p.positions


def test_stop_loss_not_touched():
    """bar low above stop → not triggered, position kept."""
    p = VirtualPortfolio()
    p.buy("SH600000", 1000, 9.80, "2026-05-05")
    brk = _no_slip_broker()
    bar = _bar(9.80, 9.90, 9.70, 9.85, trade_date="2026-05-06")
    order = Order(code="SH600000", side="sell", otype="stop", limit_price=9.50, qty=1000)
    fill = brk.match(order, bar, prev_close=10.0, portfolio=p)
    assert fill is None
    assert p.positions["SH600000"].qty == 1000


# ---------------------------------------------------------------------------
# #15 realized pnl self-consistency
# ---------------------------------------------------------------------------


def test_realized_pnl_self_consistent():
    p = VirtualPortfolio()
    p.buy("SH600000", 1000, 10.00, "2026-05-06")  # avg_cost = 10.006
    p.sell("SH600000", 1000, 11.00, "2026-05-07")
    # realized = gross_sell - sell_cost - qty*avg_cost
    #          = (11000 - 11.6) - 1000*10.006 = 10988.4 - 10006 = 982.4
    assert p.realized_pnl_total == pytest.approx(982.4, abs=TOL)
    # self-consistent: realized == cash_end - init_cash
    assert p.realized_pnl_total == pytest.approx(p.cash - p.init_cash, abs=TOL)
    assert p.cash - p.init_cash == pytest.approx(982.4, abs=TOL)


# ---------------------------------------------------------------------------
# snapshot contract (§7.4)
# ---------------------------------------------------------------------------


def test_snapshot_contract_shape():
    p = VirtualPortfolio()
    p.buy("SH600000", 1000, 10.00, "2026-05-06")
    p.mark_to_market({"SH600000": 11.00}, "2026-05-06")
    snap = p.snapshot()
    assert set(snap.keys()) == {"cash", "nav", "positions", "date"}
    assert snap["nav"] == pytest.approx(p.cash + 11000.0, abs=TOL)
    pos = snap["positions"]["SH600000"]
    assert set(pos.keys()) == {"qty", "avg_cost", "stop_loss", "mkt_value"}
    assert pos["qty"] == 1000


# ---------------------------------------------------------------------------
# Position.sellable basic
# ---------------------------------------------------------------------------


def test_position_sellable_partial_lock():
    pos = Position(code="SH600000", qty=2000, avg_cost=10.0)
    pos.locked = {"2026-05-05": 1000, "2026-05-06": 1000}
    # on 2026-05-06: d>=today locks the 2026-05-06 batch (1000) → sellable 1000
    assert pos.sellable("2026-05-06") == 1000
    # on 2026-05-07: both batches unlocked → sellable 2000
    assert pos.sellable("2026-05-07") == 2000


def test_norm_date_accepts_timestamp():
    """buy with a pd.Timestamp trade_date is normalized to str key in locked."""
    import pandas as pd

    p = VirtualPortfolio()
    p.buy("SH600000", 1000, 10.00, pd.Timestamp("2026-05-06"))
    pos = p.positions["SH600000"]
    assert "2026-05-06" in pos.locked
    assert pos.sellable("2026-05-06") == 0
    assert pos.sellable("2026-05-07") == 1000
