"""P1 backtest engine — compute_metrics NAV-level → PortfolioResult bridge.

Covers design §3 / §5 metrics专项:
mdd not lost with synthetic start (fillna not dropna), short-window ann=nan,
n-returns count, calmar权威重算, win_rate semantics.

Float tolerance 1e-6.
"""
import math

import pandas as pd
import pytest

from financial_analyst.backtest import compute_metrics
from financial_analyst.factors.eval.portfolio import PortfolioResult

TOL = 1e-6


def _nav(levels, init_cash=1_000_000.0, start="2026-05-06"):
    """Build nav_history [(dateStr, level)] from a list of levels."""
    dates = pd.bdate_range(start, periods=len(levels)).strftime("%Y-%m-%d").tolist()
    return list(zip(dates, levels))


# ---------------------------------------------------------------------------
# mdd not lost — the headline blocker fix
# ---------------------------------------------------------------------------


def test_mdd_not_lost_with_synthetic_start():
    # NAV levels [1e6, 0.90e6, 0.95e6] (idx0 is synthetic init point)
    nav_history = _nav([1_000_000.0, 900_000.0, 950_000.0])
    res = compute_metrics(nav_history, init_cash=1_000_000.0)
    assert isinstance(res, PortfolioResult)
    # true mdd = -0.10 (peak 1.0 → trough 0.90). dropna() path would give 0.0.
    assert res.max_drawdown == pytest.approx(-0.10, abs=1e-9)


def test_short_window_ann_is_nan():
    # only 2 NAV points → ann/sharpe/calmar must be nan (no 218625 explosion)
    nav_history = _nav([1_000_000.0, 1_050_000.0])
    res = compute_metrics(nav_history, init_cash=1_000_000.0)
    assert math.isnan(res.ann_return)
    assert math.isnan(res.sharpe)
    assert math.isnan(res.calmar)


def test_n_returns_count_with_synthetic_start():
    # 3 NAV points (synthetic + 2 days) → pct_change().fillna(0) has length 3,
    # first-day return NOT swallowed (validates §0.2 synthetic point + fillna).
    nav_history = _nav([1_000_000.0, 1_010_000.0, 1_020_000.0])
    res = compute_metrics(nav_history, init_cash=1_000_000.0)
    # 3 points >= 3 → ann should be finite (not nan)
    assert not math.isnan(res.ann_return)
    # nav_series length matches input
    assert len(res.nav_series) == 3
    # normalized: nv[0] == 1.0
    assert res.nav_series[0][1] == pytest.approx(1.0, abs=TOL)


def test_nav_series_format_str_float():
    nav_history = _nav([1_000_000.0, 1_010_000.0, 1_020_000.0, 1_015_000.0])
    res = compute_metrics(nav_history, init_cash=1_000_000.0)
    for ts, v in res.nav_series:
        assert isinstance(ts, str)
        assert isinstance(v, float)


def test_calmar_reconstructed_from_nav():
    # monotone-down-then-up so mdd is well-defined and ann finite
    nav_history = _nav([1_000_000.0, 1_100_000.0, 990_000.0, 1_050_000.0, 1_080_000.0])
    res = compute_metrics(nav_history, init_cash=1_000_000.0)
    #权威 mdd 直接重算: nv = [1.0, 1.1, 0.99, 1.05, 1.08]; cummax=[1,1.1,1.1,1.1,1.1]
    # drawdown min = 0.99/1.1 - 1 = -0.1 (approx)
    assert res.max_drawdown == pytest.approx(0.99 / 1.1 - 1.0, abs=1e-9)
    if not math.isnan(res.calmar):
        assert res.calmar == pytest.approx(res.ann_return / abs(res.max_drawdown), abs=1e-6)


def test_flat_nav_zero_mdd():
    nav_history = _nav([1_000_000.0, 1_000_000.0, 1_000_000.0])
    res = compute_metrics(nav_history, init_cash=1_000_000.0)
    assert res.max_drawdown == pytest.approx(0.0, abs=1e-9)


def test_turnover_and_benchmark_passthrough():
    nav_history = _nav([1_000_000.0, 1_010_000.0, 1_020_000.0])
    bench = [("2026-05-06", 1.0), ("2026-05-07", 1.01), ("2026-05-08", 1.005)]
    res = compute_metrics(nav_history, init_cash=1_000_000.0,
                          turnover=0.25, benchmark_nav=bench)
    assert res.turnover == pytest.approx(0.25, abs=TOL)
    assert res.benchmark_nav == bench


def test_win_rate_is_up_day_ratio():
    # up, up, down → 2 of 3 returns >0 (incl synthetic→day1). fillna(0) first is 0 (not >0).
    # levels [1.0, 1.01, 1.02, 1.00]*1e6 → returns after fillna: [0, +, +, -]
    # win_rate = (>0).mean() over 4 returns = 2/4 = 0.5
    nav_history = _nav([1_000_000.0, 1_010_000.0, 1_020_400.0, 1_000_000.0])
    res = compute_metrics(nav_history, init_cash=1_000_000.0)
    assert res.win_rate == pytest.approx(0.5, abs=1e-9)
