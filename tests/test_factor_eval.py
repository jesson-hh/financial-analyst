from __future__ import annotations
import numpy as np
import pandas as pd
import pytest

from financial_analyst.factors.eval.config import EvalConfig
from financial_analyst.factors.eval.preprocess import winsorize, zscore, neutralize


def _xs_series(n_dates=4, codes=("A", "B", "C", "D", "E")):
    dates = pd.date_range("2024-01-01", periods=n_dates, freq="B")
    idx = pd.MultiIndex.from_product([dates, codes], names=["datetime", "code"])
    np.random.seed(3)
    return pd.Series(np.random.randn(len(idx)) * 10 + 50, index=idx)


def test_config_defaults():
    c = EvalConfig()
    assert c.universe == "csi500" and c.freq == "month" and c.n_groups == 10
    assert c.cost_bps == 0.0 and c.standardize is True


def test_effective_fwd_days_by_freq():
    assert EvalConfig(freq="day").effective_fwd_days() == 1
    assert EvalConfig(freq="week").effective_fwd_days() == 5
    assert EvalConfig(freq="month").effective_fwd_days() == 21
    assert EvalConfig(freq="month", fwd_days=10).effective_fwd_days() == 10


def test_periods_per_year():
    assert EvalConfig(freq="day").periods_per_year() == 252
    assert EvalConfig(freq="week").periods_per_year() == 52
    assert EvalConfig(freq="month").periods_per_year() == 12


def test_winsorize_clamps_to_quantile_per_date():
    s = _xs_series()
    d0 = s.index.get_level_values("datetime")[0]
    s.loc[(d0, "A")] = 1e6
    w = winsorize(s, q=0.2)
    others_max = float(s.drop((d0, "A")).xs(d0, level="datetime").max())
    assert w.loc[(d0, "A")] < 1e6
    assert w.loc[(d0, "A")] == pytest.approx(others_max)


def test_zscore_per_date_mean0_std1():
    s = _xs_series()
    z = zscore(s)
    for d, sub in z.groupby(level="datetime"):
        assert abs(float(sub.mean())) < 1e-9
        assert abs(float(sub.std()) - 1.0) < 1e-6


def test_neutralize_is_stub():
    with pytest.raises(NotImplementedError):
        neutralize(_xs_series(), industry=None)


def test_zscore_degenerate_all_equal_date_is_nan():
    dates = pd.date_range("2024-01-01", periods=2, freq="B")
    idx = pd.MultiIndex.from_product([dates, ["A", "B", "C"]], names=["datetime", "code"])
    s = pd.Series([5.0, 5.0, 5.0, 1.0, 2.0, 3.0], index=idx)  # date0 all-equal, date1 varied
    z = zscore(s)
    assert z.xs(dates[0], level="datetime").isna().all()       # zero-variance → all NaN
    assert abs(float(z.xs(dates[1], level="datetime").mean())) < 1e-9  # normal date OK


from financial_analyst.factors.eval.ic import ic_analysis, IcResult


def _aligned_alpha_fwd(relation="perfect", n_dates=30, codes=tuple("ABCDEFGH"), seed=5):
    """Build aligned (alpha, fwd) series on the same (date, code) index.
    relation: 'perfect' (alpha == fwd), 'reversed' (alpha == -fwd), 'random'."""
    dates = pd.date_range("2024-01-01", periods=n_dates, freq="B")
    idx = pd.MultiIndex.from_product([dates, codes], names=["datetime", "code"])
    rng = np.random.default_rng(seed)
    fwd = pd.Series(rng.standard_normal(len(idx)) * 0.02, index=idx)
    if relation == "perfect":
        alpha = fwd.copy()
    elif relation == "reversed":
        alpha = -fwd
    else:
        alpha = pd.Series(rng.standard_normal(len(idx)), index=idx)
    return alpha, fwd


def test_ic_perfect_factor_near_one():
    alpha, fwd = _aligned_alpha_fwd("perfect")
    r = ic_analysis(alpha, fwd)
    assert isinstance(r, IcResult)
    assert r.ic_mean > 0.95
    assert r.icir > 3
    assert r.ic_win_rate > 0.95
    assert len(r.ic_series) == 30


def test_ic_reversed_factor_near_minus_one():
    alpha, fwd = _aligned_alpha_fwd("reversed")
    r = ic_analysis(alpha, fwd)
    assert r.ic_mean < -0.95
    assert r.rank_ic_mean < -0.9


def test_ic_random_factor_near_zero():
    # 60 codes → per-date IC has low sampling variance, so a truly random factor's
    # mean IC sits tightly at 0. (8 codes was too noisy to assert < 0.1 — but the
    # fix is more statistical power, NOT a looser bound: |IC|<0.2 would let a
    # genuinely predictive factor pass as 'random'.)
    codes = tuple(f"S{i:02d}" for i in range(60))
    alpha, fwd = _aligned_alpha_fwd("random", n_dates=30, codes=codes)
    r = ic_analysis(alpha, fwd)
    assert abs(r.ic_mean) < 0.1
    assert abs(r.rank_ic_mean) < 0.1


def test_ic_decay_one_row_per_horizon():
    alpha, fwd = _aligned_alpha_fwd("perfect")
    decay_fwd = {1: fwd, 5: fwd, 21: fwd}
    # NOTE: same fwd reused for every horizon → this asserts decay SHAPE + sign
    # (one sorted (h, ic, rank_ic) row per horizon), not that IC actually decays.
    r = ic_analysis(alpha, fwd, fwd_by_horizon=decay_fwd)
    assert [h for h, _, _ in r.ic_decay] == [1, 5, 21]
    assert all(ic > 0.9 for _, ic, _ in r.ic_decay)


from financial_analyst.factors.eval.quantile import quantile_backtest, QuantileResult


def test_quantile_perfect_factor_monotonic():
    alpha, fwd = _aligned_alpha_fwd("perfect", n_dates=40)
    r = quantile_backtest(alpha, fwd, n_groups=5, ppy=12)
    assert isinstance(r, QuantileResult)
    # group 0 = lowest factor = lowest return; group 4 = highest
    assert r.group_ann_return[-1] > r.group_ann_return[0]
    assert r.monotonicity > 0.9
    assert r.long_short_spread > 0
    assert len(r.group_nav) == len(r.group_ann_return)
    assert len(r.group_ann_return) == r.n_groups  # 8 distinct values → 5 clean buckets


def test_quantile_reversed_factor_negative_spread():
    alpha, fwd = _aligned_alpha_fwd("reversed", n_dates=40)
    r = quantile_backtest(alpha, fwd, n_groups=5, ppy=12)
    assert r.long_short_spread < 0
    assert r.monotonicity < -0.9


def test_quantile_random_factor_flat():
    # 5-group Spearman monotonicity is too coarse / high-variance to assert on a
    # random factor (rank corr of 5 near-zero group means is ~uniformly random,
    # regardless of sample size). Robust check instead: a random factor's
    # long-short spread is far smaller than a perfect factor's on the same data.
    codes = tuple(f"S{i:02d}" for i in range(60))
    p_alpha, p_fwd = _aligned_alpha_fwd("perfect", n_dates=60, codes=codes)
    r_alpha, r_fwd = _aligned_alpha_fwd("random", n_dates=60, codes=codes)
    perfect = quantile_backtest(p_alpha, p_fwd, n_groups=5, ppy=12)
    rand = quantile_backtest(r_alpha, r_fwd, n_groups=5, ppy=12)
    assert abs(perfect.long_short_spread) > 0  # perfect factor separates groups
    assert abs(rand.long_short_spread) < abs(perfect.long_short_spread) / 5


from financial_analyst.factors.eval.portfolio import (
    long_short_portfolio, portfolio_stats, PortfolioResult,
)


def test_portfolio_stats_hand_computed():
    # 4 periods of +10% each, monthly (ppy=12)
    ls = pd.Series([0.1, 0.1, 0.1, 0.1],
                   index=pd.to_datetime(["2024-01-31", "2024-02-29", "2024-03-31", "2024-04-30"]))
    st = portfolio_stats(ls, ppy=12)
    # nav_end = 1.1**4 = 1.4641; ann = 1.4641**(12/4) - 1
    assert st["ann_return"] == pytest.approx(1.4641 ** 3 - 1, rel=1e-6)
    assert st["max_drawdown"] == pytest.approx(0.0, abs=1e-9)  # monotonic up
    assert st["win_rate"] == pytest.approx(1.0)
    assert st["volatility"] == pytest.approx(0.0, abs=1e-9)  # zero variance


def test_portfolio_stats_drawdown():
    ls = pd.Series([0.2, -0.5, 0.1],
                   index=pd.to_datetime(["2024-01-31", "2024-02-29", "2024-03-31"]))
    st = portfolio_stats(ls, ppy=12)
    # nav = [1.2, 0.6, 0.66]; peak 1.2 → trough 0.6 → dd = 0.6/1.2 - 1 = -0.5
    assert st["max_drawdown"] == pytest.approx(-0.5, rel=1e-6)
    assert st["win_rate"] == pytest.approx(2 / 3, rel=1e-6)


def test_long_short_perfect_factor_positive_sharpe():
    alpha, fwd = _aligned_alpha_fwd("perfect", n_dates=40)
    r = long_short_portfolio(alpha, fwd, n_groups=5, ppy=12, cost_bps=0.0)
    assert isinstance(r, PortfolioResult)
    assert r.ann_return > 0
    assert r.sharpe > 0
    assert len(r.nav_series) >= 1


def test_long_short_cost_reduces_return():
    alpha, fwd = _aligned_alpha_fwd("random", n_dates=60)
    gross = long_short_portfolio(alpha, fwd, n_groups=5, ppy=12, cost_bps=0.0)
    net = long_short_portfolio(alpha, fwd, n_groups=5, ppy=12, cost_bps=50.0)
    assert net.ann_return <= gross.ann_return + 1e-9
    assert gross.turnover >= 0


def test_long_short_turnover_known_rotation():
    # date1: 4 codes, n_groups=2 → top(label1)={C,D}
    # date2: 6 codes, n_groups=2 → top={D,E,F}  (qcut of [1..6] into 2 → bottom A,B,C / top D,E,F)
    # symdiff({D,E,F},{C,D}) = {C,E,F} = 3 ; combined denom = 3+2 = 5 → turn=0.6
    # mean turnover = (first=0 + 0.6)/2 = 0.3  (the OLD 2*max(len) denom would give 0.25)
    d1, d2 = pd.Timestamp("2024-01-31"), pd.Timestamp("2024-02-29")
    vals = {(d1, "A"): 1, (d1, "B"): 2, (d1, "C"): 3, (d1, "D"): 4,
            (d2, "A"): 1, (d2, "B"): 2, (d2, "C"): 3, (d2, "D"): 4, (d2, "E"): 5, (d2, "F"): 6}
    idx = pd.MultiIndex.from_tuples(list(vals), names=["datetime", "code"])
    alpha = pd.Series([float(v) for v in vals.values()], index=idx)
    fwd = pd.Series(0.01, index=idx)  # arbitrary non-NaN; turnover is membership-only
    r = long_short_portfolio(alpha, fwd, n_groups=2, ppy=12, cost_bps=0.0)
    assert r.turnover == pytest.approx(0.3)


from financial_analyst.factors.eval.report import (
    build_report, FactorReport, factor_characteristics, rebalance_dates,
    forward_simple_returns,
)
from financial_analyst.factors.zoo import PanelData
from financial_analyst.factors.eval.config import EvalConfig


def _signal_panel(n_dates=80, codes=tuple("ABCDEFGH"), seed=11):
    """Synthetic daily price panel: per-code cumulative product of small lognormal
    returns → a random-walk price series. Used to exercise build_report end-to-end."""
    dates = pd.date_range("2023-01-02", periods=n_dates, freq="B")
    idx = pd.MultiIndex.from_product([dates, codes], names=["datetime", "code"])
    rng = np.random.default_rng(seed)
    rets = pd.Series(rng.lognormal(0.0, 0.02, len(idx)), index=idx)
    close = rets.groupby(level="code").cumprod() * 50 + 10
    df = pd.DataFrame({
        "open": close, "high": close * 1.01, "low": close * 0.99,
        "close": close, "volume": pd.Series(1e6, index=idx),
    })
    return PanelData(df)


def test_rebalance_dates_month():
    dates = pd.date_range("2024-01-01", "2024-03-31", freq="B")
    reb = rebalance_dates(list(dates), "month")
    assert len(reb) == 3  # one per calendar month (last business day)


def test_forward_simple_returns_basic():
    p = _signal_panel(n_dates=10, codes=("A",))
    fwd = forward_simple_returns(p, 1)
    a = p.close.xs("A", level="code")
    f = fwd.xs("A", level="code")
    assert f.iloc[0] == pytest.approx(a.iloc[1] / a.iloc[0] - 1, rel=1e-9)
    assert pd.isna(f.iloc[-1])


def test_build_report_perfect_factor_ok():
    p = _signal_panel(n_dates=80)
    compute = lambda panel: panel.close.groupby(level="code").pct_change()  # 1d momentum
    cfg = EvalConfig(freq="week", standardize=True)
    rpt = build_report(p, compute, cfg, factor_label="mom1", family="custom")
    assert isinstance(rpt, FactorReport)
    assert rpt.status == "ok"
    assert rpt.meta.factor == "mom1" and rpt.meta.freq == "week"
    assert rpt.ic is not None and rpt.quantile is not None and rpt.portfolio is not None
    assert 0.0 <= rpt.characteristics.coverage <= 1.0
    assert rpt.portfolio.benchmark_nav is not None


def test_build_report_compute_error_no_raise():
    p = _signal_panel(n_dates=40)
    def boom(panel):
        raise RuntimeError("synthetic boom")
    rpt = build_report(p, boom, EvalConfig(freq="week"), factor_label="boom", family="custom")
    assert rpt.status == "compute_error"
    assert "synthetic boom" in rpt.error


def test_build_report_bad_output_status():
    p = _signal_panel(n_dates=40)
    rpt = build_report(p, lambda panel: 123, EvalConfig(freq="week"),
                       factor_label="bad", family="custom")
    assert rpt.status == "bad_output"


def test_factor_characteristics_coverage():
    p = _signal_panel(n_dates=30, codes=tuple("ABCDE"))
    alpha = p.close.groupby(level="code").pct_change()
    ch = factor_characteristics(alpha, n_codes=5)
    assert 0.0 <= ch.coverage <= 1.0


def test_build_report_neutralize_warns():
    p = _signal_panel(n_dates=40)
    cfg = EvalConfig(freq="week", neutralize=True)
    rpt = build_report(p, lambda panel: panel.close.groupby(level="code").pct_change(),
                       cfg, factor_label="x", family="custom")
    assert rpt.status == "ok"
    assert any(("中性化" in w) or ("neutralize" in w) for w in rpt.warnings)
