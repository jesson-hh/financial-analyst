"""P1 backtest engine — real 5min smoke test (design §5 #16).

Runs one day of real SH600519 5min bars through Broker + VirtualPortfolio +
compute_metrics. Dynamic date = last trading day in the 5min calendar (NOT
hardcoded). Skips cleanly if local 5min data is missing (CI / other machines),
never silently green.

Structure-only assertions: PortfolioResult + nav_series non-empty (str, float).
No ann_return numeric assertion — a single/few-day annualized number is
meaningless (see §0.2 short-window guard).
"""
import os

import pytest

DAY_ROOT = "G:/stocks/stock_data/cn_data"
MIN_ROOT = "G:/stocks/stock_data/cn_data_5min"


def test_real_5min_smoke_runs():
    if not (os.path.isdir(DAY_ROOT) and os.path.isdir(MIN_ROOT)):
        pytest.skip("no local 5min data")

    from financial_analyst.data.loaders.qlib_binary import QlibBinaryLoader
    from financial_analyst.backtest import (
        VirtualPortfolio,
        Broker,
        Order,
        CostModel,
        compute_metrics,
        compute_ref_prev_close,
    )
    from financial_analyst.factors.eval.portfolio import PortfolioResult

    loader = QlibBinaryLoader({"day": DAY_ROOT, "5min": MIN_ROOT})
    try:
        cal5 = loader._load_calendar("5min")
    except Exception:
        pytest.skip("no local 5min data")
    if not cal5:
        pytest.skip("empty 5min calendar")

    code = "SH600519"
    last_day = str(cal5[-1].date())

    bars = loader.fetch_quote(code, last_day, last_day, freq="5min")
    if bars is None or bars.empty or "close" not in bars.columns:
        pytest.skip(f"no 5min bars for {code} on {last_day}")

    # ref_prev_close from day freq (factor only exists at day freq)
    day_close = loader._read_bin(code, "close", freq="day")
    factor = loader._read_bin(code, "factor", freq="day")
    if day_close is None or factor is None:
        pytest.skip("no day close/factor for ref_prev_close")
    ref_series = compute_ref_prev_close(day_close, factor)
    import pandas as pd

    ts = pd.Timestamp(last_day)
    # nearest ref_prev_close at or before last_day
    avail = ref_series.loc[ref_series.index <= ts].dropna()
    if avail.empty:
        pytest.skip("no ref_prev_close available")
    ref_prev = float(avail.iloc[-1])

    p = VirtualPortfolio()
    brk = Broker(cost_model=CostModel())

    # seed synthetic initial NAV point (design §0.2 / §1.1)
    p.seed_initial_nav(last_day)

    rows = list(bars.itertuples(index=False))
    cols = list(bars.columns)

    # Buy on the first bar with a budget large enough for >=1 lot even for a
    # high-priced name (Moutai ~1300/share → 100 lots ~130k). 300k affords a lot
    # of SH600519 specifically, so the smoke actually exercises a real fill.
    first = dict(zip(cols, rows[0]))
    order = Order(code=code, side="buy", otype="limit",
                  limit_price=float(first["high"]) * 1.05,
                  qty=None, cash_budget=300_000.0)
    fill = brk.match(order, first, prev_close=ref_prev, portfolio=p)
    # On a non-suspended liquid bar this should fill; if the data is degenerate
    # (one-word, zero vol) it returns None — still must not crash.
    if fill is not None:
        assert fill.side == "buy"
        assert fill.qty >= 100
        assert p.cash < p.init_cash

    # mark-to-market + record NAV on the last bar
    last = dict(zip(cols, rows[-1]))
    p.record_nav(last_day, prices={code: float(last["close"])})

    res = compute_metrics(p.nav_history, init_cash=p.init_cash)

    assert isinstance(res, PortfolioResult)
    assert len(res.nav_series) >= 1
    for tstr, v in res.nav_series:
        assert isinstance(tstr, str)
        assert isinstance(v, float)
    # NAV stays strictly positive (no blow-up / NaN)
    assert all(v > 0 for _, v in res.nav_series)
