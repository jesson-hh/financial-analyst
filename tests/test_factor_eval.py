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
