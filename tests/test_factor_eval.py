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
