"""P1 backtest engine — Broker.match hand-computable cases.

Covers design §5 cases #6-10, #13-14:
one-word limit-up buy blocked, one-word sell also blocked (P1 conservative),
limit-buy clip to bar high, cash-insufficient truncation, slippage direction,
limit_pct routing by prefix, ChiNext 20% not blocked at 11%.

All toy bars; default CostModel; slippage 0 except #10.
Float tolerance 1e-6.
"""
import pytest

from financial_analyst.backtest import (
    VirtualPortfolio,
    Broker,
    Order,
    CostModel,
    limit_pct_for,
)

TOL = 1e-6


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
# #6 one-word limit-up buy blocked
# ---------------------------------------------------------------------------


def test_one_word_limit_up_buy_blocked():
    p = VirtualPortfolio()
    brk = _no_slip_broker()
    # ref_prev=10.00; bar all = 11.00 (+10%), vol>0 → one-word up
    bar = _bar(11.00, 11.00, 11.00, 11.00, trade_date="2026-05-06")
    order = Order(code="SH600000", side="buy", otype="limit", limit_price=11.00, qty=1000)
    fill = brk.match(order, bar, prev_close=10.00, portfolio=p)
    assert fill is None
    assert brk.last_reason == "one_word_limit_up"
    assert p.cash == pytest.approx(1_000_000.0, abs=TOL)


# ---------------------------------------------------------------------------
# #7 one-word limit-up sell ALSO blocked (P1 conservative — no fill-queue data)
# ---------------------------------------------------------------------------


def test_one_word_limit_up_sell_also_blocked():
    p = VirtualPortfolio()
    p.buy("SH600000", 1000, 9.00, "2026-05-05")  # seed sellable position
    brk = _no_slip_broker()
    bar = _bar(11.00, 11.00, 11.00, 11.00, trade_date="2026-05-06")
    order = Order(code="SH600000", side="sell", otype="limit", limit_price=10.00, qty=1000)
    fill = brk.match(order, bar, prev_close=10.00, portfolio=p)
    assert fill is None
    assert p.positions["SH600000"].qty == 1000


def test_one_word_limit_down_buy_blocked():
    """One-word limit-DOWN: buy must not fill (no liquidity)."""
    p = VirtualPortfolio()
    brk = _no_slip_broker()
    # ref_prev=10.00; bar all = 9.00 (-10%) → one-word down
    bar = _bar(9.00, 9.00, 9.00, 9.00, trade_date="2026-05-06")
    order = Order(code="SH600000", side="buy", otype="limit", limit_price=9.00, qty=1000)
    fill = brk.match(order, bar, prev_close=10.00, portfolio=p)
    assert fill is None
    assert p.cash == pytest.approx(1_000_000.0, abs=TOL)


# ---------------------------------------------------------------------------
# #8 limit-buy clip to bar high
# ---------------------------------------------------------------------------


def test_limit_buy_clip_to_bar_high():
    p = VirtualPortfolio()
    brk = _no_slip_broker()
    # ref_prev=10; bar low=9.5/high=10.2/close=10.0; buy limit=10.5
    bar = _bar(9.8, 10.2, 9.5, 10.0, trade_date="2026-05-06")
    order = Order(code="SH600000", side="buy", otype="limit", limit_price=10.5, qty=1000)
    fill = brk.match(order, bar, prev_close=10.0, portfolio=p)
    assert fill is not None
    # triggered (limit>=low); fill = clip(min(10.5, 10.2), low, high) = 10.20
    assert fill.price == pytest.approx(10.20, abs=TOL)


def test_limit_buy_not_touched():
    """limit below bar low → not touched."""
    p = VirtualPortfolio()
    brk = _no_slip_broker()
    bar = _bar(10.5, 10.8, 10.2, 10.6, trade_date="2026-05-06")
    order = Order(code="SH600000", side="buy", otype="limit", limit_price=10.0, qty=1000)
    fill = brk.match(order, bar, prev_close=10.0, portfolio=p)
    assert fill is None


# ---------------------------------------------------------------------------
# #9 cash insufficient truncates qty (affordable_qty exact back-solve)
# ---------------------------------------------------------------------------


def test_cash_insufficient_truncates_qty():
    p = VirtualPortfolio(init_cash=10000.0, cash=10000.0)
    brk = _no_slip_broker()
    bar = _bar(10.0, 10.1, 9.9, 10.0, trade_date="2026-05-06")
    order = Order(code="SH600000", side="buy", otype="limit", limit_price=10.0,
                  qty=None, cash_budget=10000.0)
    fill = brk.match(order, bar, prev_close=10.0, portfolio=p)
    assert fill is not None
    # 900 shares: 10*900 + buy_cost(10,900)= 9000 + max(2.25,5) + 0.9 = 9005.9 <= 10000
    # 1000 shares: 10000 + 5 + 1 = 10006 > 10000 → truncate to 900
    assert fill.qty == 900
    assert p.cash >= 0
    # exact: cash = 10000 - 9005.9 = 994.1
    assert p.cash == pytest.approx(994.1, abs=TOL)


def test_below_one_lot_rejected():
    """budget too small for 100 shares → None."""
    p = VirtualPortfolio(init_cash=500.0, cash=500.0)
    brk = _no_slip_broker()
    bar = _bar(10.0, 10.1, 9.9, 10.0, trade_date="2026-05-06")
    order = Order(code="SH600000", side="buy", otype="limit", limit_price=10.0,
                  qty=None, cash_budget=500.0)
    fill = brk.match(order, bar, prev_close=10.0, portfolio=p)
    assert fill is None


# ---------------------------------------------------------------------------
# #10 slippage direction
# ---------------------------------------------------------------------------


def test_slippage_direction():
    brk = Broker(cost_model=CostModel(slippage_bps=10.0))
    # buy: limit=10.0 touched (low=9.9/high=10.1)
    p1 = VirtualPortfolio()
    bar = _bar(10.0, 10.1, 9.9, 10.0, trade_date="2026-05-06")
    ob = Order(code="SH600000", side="buy", otype="limit", limit_price=10.0, qty=1000)
    fb = brk.match(ob, bar, prev_close=10.0, portfolio=p1)
    assert fb is not None
    # buy fill = clip(min(10,10.1)*1.001, 9.9, 10.1) = clip(10.01, ...) = 10.01 (worse/higher)
    assert fb.price == pytest.approx(10.01, abs=TOL)

    # sell: limit=10.0 touched, symmetric
    p2 = VirtualPortfolio()
    p2.buy("SH600000", 1000, 9.0, "2026-05-05")
    os_ = Order(code="SH600000", side="sell", otype="limit", limit_price=10.0, qty=1000)
    fs = brk.match(os_, bar, prev_close=10.0, portfolio=p2)
    assert fs is not None
    # sell fill = clip(max(10,9.9)*0.999, 9.9, 10.1) = clip(9.99, ...) = 9.99 (worse/lower)
    assert fs.price == pytest.approx(9.99, abs=TOL)


# ---------------------------------------------------------------------------
# #13 limit_pct by prefix
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "code,is_st,expected",
    [
        ("SH600000", False, 0.10),
        ("SZ000001", False, 0.10),
        ("SZ002594", False, 0.10),
        ("SZ300001", False, 0.20),
        ("SH688001", False, 0.20),
        ("BJ835174", False, 0.30),
        ("SH600000", True, 0.05),
        ("SZ300001", True, 0.05),  # ST overrides board
    ],
)
def test_limit_pct_by_prefix(code, is_st, expected):
    assert limit_pct_for(code, is_st=is_st) == pytest.approx(expected, abs=TOL)


# ---------------------------------------------------------------------------
# #14 ChiNext 20% not blocked at 11%
# ---------------------------------------------------------------------------


def test_cregem_20pct_not_blocked_at_11pct():
    p = VirtualPortfolio()
    brk = _no_slip_broker()
    # SZ300xxx ref_prev=10; bar all=11.00 (+10%); ChiNext limit-up = 12.00
    bar = _bar(11.00, 11.00, 11.00, 11.00, trade_date="2026-05-06")
    order = Order(code="SZ300001", side="buy", otype="limit", limit_price=11.0, qty=1000)
    fill = brk.match(order, bar, prev_close=10.0, portfolio=p)
    # NOT one-word up (11 < up=12.00 and 11 != 12) → can fill
    assert fill is not None
    # fill = clip(min(11,11), low, high) = 11.0
    assert fill.price == pytest.approx(11.0, abs=TOL)


# ---------------------------------------------------------------------------
# suspended / missing-bar guards
# ---------------------------------------------------------------------------


def test_suspended_zero_vol_no_fill():
    p = VirtualPortfolio()
    brk = _no_slip_broker()
    bar = _bar(10.0, 10.1, 9.9, 10.0, vol=0, trade_date="2026-05-06")
    order = Order(code="SH600000", side="buy", otype="limit", limit_price=10.5, qty=1000)
    fill = brk.match(order, bar, prev_close=10.0, portfolio=p)
    assert fill is None


def test_missing_column_no_fill():
    """_build_df omits column when .bin absent → use `col in bar` not pd.isna."""
    p = VirtualPortfolio()
    brk = _no_slip_broker()
    bar = {"open": 10.0, "high": 10.1, "low": 9.9, "trade_date": "2026-05-06", "vol": 1e6}
    # 'close' missing
    order = Order(code="SH600000", side="buy", otype="limit", limit_price=10.5, qty=1000)
    fill = brk.match(order, bar, prev_close=10.0, portfolio=p)
    assert fill is None


def test_none_bar_no_fill():
    p = VirtualPortfolio()
    brk = _no_slip_broker()
    order = Order(code="SH600000", side="buy", otype="limit", limit_price=10.5, qty=1000)
    fill = brk.match(order, None, prev_close=10.0, portfolio=p)
    assert fill is None


# ---------------------------------------------------------------------------
# market order: next-bar same-day required
# ---------------------------------------------------------------------------


def test_market_order_fills_next_bar_open():
    p = VirtualPortfolio()
    brk = _no_slip_broker()
    bar = _bar(10.0, 10.2, 9.8, 10.1, trade_date="2026-05-06")
    nbo = 10.05
    order = Order(code="SH600000", side="buy", otype="market", qty=1000)
    fill = brk.match(order, bar, prev_close=10.0, portfolio=p,
                     next_bar_open=nbo, next_bar_date="2026-05-06")
    assert fill is not None
    # slippage 0 → fill = clip(10.05, low, high) = 10.05
    assert fill.price == pytest.approx(10.05, abs=TOL)


def test_market_order_cross_day_rejected():
    """next_bar belongs to a different trade_date → no fill (no leakage)."""
    p = VirtualPortfolio()
    brk = _no_slip_broker()
    bar = _bar(10.0, 10.2, 9.8, 10.1, trade_date="2026-05-06")
    order = Order(code="SH600000", side="buy", otype="market", qty=1000)
    fill = brk.match(order, bar, prev_close=10.0, portfolio=p,
                     next_bar_open=10.05, next_bar_date="2026-05-07")
    assert fill is None
    assert brk.last_reason == "no_next_bar"


def test_market_order_no_next_bar_rejected():
    p = VirtualPortfolio()
    brk = _no_slip_broker()
    bar = _bar(10.0, 10.2, 9.8, 10.1, trade_date="2026-05-06")
    order = Order(code="SH600000", side="buy", otype="market", qty=1000)
    fill = brk.match(order, bar, prev_close=10.0, portfolio=p)
    assert fill is None
