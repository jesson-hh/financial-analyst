# tests/test_attrib.py
# 风格归因(guanlan_v2/workflow/attrib.py)纯数学门禁:
#   多元 OLS + Newey-West HAC(_ols_hac)+ 风格因子收益构建(build_style_factor_returns)
#   + 归因编排(attribute_returns 复用注入的 _predictive_reg)。
# 不碰数据、不连服务器、不依赖 engine —— 纯 numpy/pandas,确定性(固定 rng 种子)。
import math

import numpy as np
import pandas as pd
import pytest

from guanlan_v2.workflow.attrib import (
    STYLE_FACTORS,
    _ols_hac,
    _leg_spread,
    build_style_factor_returns,
    attribute_returns,
)


# ── 多元 OLS + HAC ────────────────────────────────────────────────────────────
def test_ols_hac_parameter_recovery():
    rng = np.random.default_rng(11)
    n = 600
    F = rng.standard_normal((n, 4)) * 0.02
    true_b = np.array([0.8, 0.5, -0.3, 0.2])
    Y = 0.001 + F @ true_b + rng.standard_normal(n) * 0.002
    X = np.column_stack([np.ones(n), F])
    res = _ols_hac(Y, X, hac_lag=1)
    assert res["alpha"] == pytest.approx(0.001, abs=4e-4)
    for i, b in enumerate(true_b):
        assert res["betas"][i] == pytest.approx(b, abs=0.05)
    assert 0.0 <= res["r2"] <= 1.0
    assert res["n"] == n


def test_ols_hac_zero_noise_r2_one():
    rng = np.random.default_rng(3)
    n = 200
    F = rng.standard_normal((n, 3))
    X = np.column_stack([np.ones(n), F])
    Y = X @ np.array([0.01, 1.0, -0.5, 0.3])
    res = _ols_hac(Y, X, 1)
    assert res["r2"] == pytest.approx(1.0, abs=1e-9)


def test_ols_hac_pure_noise_low_r2():
    rng = np.random.default_rng(5)
    n = 400
    F = rng.standard_normal((n, 3))
    X = np.column_stack([np.ones(n), F])
    Y = rng.standard_normal(n)              # 与 X 独立
    res = _ols_hac(Y, X, 1)
    assert res["r2"] < 0.1


def test_ols_hac_contribution_identity():
    """归因加总恒等:alpha + Σ βⱼ·mean(factorⱼ) + mean(residual) == mean(Y)。"""
    rng = np.random.default_rng(7)
    n = 300
    F = rng.standard_normal((n, 4)) * 0.01
    X = np.column_stack([np.ones(n), F])
    Y = 0.002 + F @ np.array([0.5, 0.3, -0.2, 0.1]) + rng.standard_normal(n) * 0.003
    res = _ols_hac(Y, X, 1)
    contrib = sum(res["betas"][j] * float(F[:, j].mean()) for j in range(4))
    e = Y - X @ np.array(res["coef"])
    assert res["alpha"] + contrib + float(e.mean()) == pytest.approx(float(Y.mean()), abs=1e-9)


def test_ols_hac_lag_clamp_and_tsign():
    rng = np.random.default_rng(9)
    n = 30
    F = rng.standard_normal((n, 2))
    X = np.column_stack([np.ones(n), F])
    Y = F @ np.array([1.0, -1.0]) + rng.standard_normal(n) * 0.01
    res = _ols_hac(Y, X, hac_lag=100)          # 过大 → 夹到 [0, n-k-1] 与 n//2
    assert res["nw_lag"] <= n // 2 and res["nw_lag"] <= n - 3
    assert res["t"][1] > 2 and res["t"][2] < -2    # 强关系 → |t|大,符号对


def test_ols_hac_singular_and_short_safe():
    # n <= k+1 → 诚实 None
    X = np.column_stack([np.ones(4), np.arange(4.0), np.arange(4.0) ** 2])
    res = _ols_hac(np.arange(4.0), X, 1)
    assert res["betas"] is None
    # 共线列 → 奇异守卫
    rng = np.random.default_rng(1)
    n = 50
    a = rng.standard_normal(n)
    Xc = np.column_stack([np.ones(n), a, a * 2.0])
    res2 = _ols_hac(rng.standard_normal(n), Xc, 1)
    assert res2["betas"] is None


# ── 风格因子腿差 + 因子收益构建 ───────────────────────────────────────────────
def _panel(dates, codes, data):
    idx = pd.MultiIndex.from_product([dates, codes], names=["datetime", "code"])
    return pd.DataFrame(data, index=idx)


def test_leg_spread_direction_and_antisymmetry():
    score = pd.Series(range(1, 11), index=[f"c{i}" for i in range(10)], dtype=float)
    ret = pd.Series([0.10, 0.09, 0.08, 0.07, 0.06, 0.05, 0.04, 0.03, 0.02, 0.01], index=score.index)
    s = _leg_spread(ret, score, long_high=False, q=0.3, min_leg=3)   # 低分(小)做多 → >0
    assert s > 0
    s2 = _leg_spread(ret, score, long_high=True, q=0.3, min_leg=3)
    assert s2 == pytest.approx(-s, abs=1e-12)


def test_leg_spread_min_leg_nan():
    score = pd.Series([1.0, 2.0, 3.0], index=["a", "b", "c"])
    ret = pd.Series([0.1, 0.2, 0.3], index=score.index)
    assert math.isnan(_leg_spread(ret, score, long_high=True, q=0.3, min_leg=10))


def test_build_style_factor_returns_smb_positive():
    dates = pd.to_datetime(["2024-01-31", "2024-02-29", "2024-03-29"])
    codes = [f"s{i:02d}" for i in range(30)]
    data = {"total_mv": [], "pb": [], "mom_120": []}
    for _d in dates:
        for i in range(len(codes)):
            data["total_mv"].append(float(i + 1))    # i 小 = 小市值
            data["pb"].append(1.0 + 0.1 * i)
            data["mom_120"].append(float(i))
    panel_df = _panel(dates, codes, data)
    fwd_idx = pd.MultiIndex.from_product([dates, codes], names=["datetime", "code"])
    fwd_r = pd.Series([0.20 - 0.005 * i for _d in dates for i in range(len(codes))], index=fwd_idx)
    fdf = build_style_factor_returns(panel_df, list(dates), fwd_r, min_leg=5, q=0.3)
    assert set(STYLE_FACTORS).issubset(fdf.columns)
    assert (fdf["SMB"] > 0).all()                    # 小市值收益更高 → SMB>0
    assert len(fdf) == 3


# ── 归因编排 ──────────────────────────────────────────────────────────────────
def test_attribute_returns_honest_empty_short():
    idx = pd.to_datetime(["2024-01-31", "2024-02-29", "2024-03-29"])
    sr = pd.Series([0.01, -0.02, 0.03], index=idx)
    fdf = pd.DataFrame({"MKT": [0.01, 0.0, 0.02], "SMB": [0.0, 0.01, -0.01],
                        "HML": [0.0, 0.0, 0.0], "WML": [0.0, 0.0, 0.0]}, index=idx)
    res = attribute_returns(sr, fdf, predictive_reg=lambda f, r, h: {}, min_periods=12)
    assert res["ok_model"] is False and res.get("reason")


def test_attribute_returns_reuses_predictive_reg_and_fullpipe():
    rng = np.random.default_rng(21)
    n = 120
    idx = pd.date_range("2016-01-31", periods=n, freq="ME")
    F = pd.DataFrame(rng.standard_normal((n, 4)) * 0.02, index=idx, columns=list(STYLE_FACTORS))
    sr = 0.001 + F.values @ np.array([0.9, 0.4, -0.2, 0.3]) + rng.standard_normal(n) * 0.003
    sr = pd.Series(sr, index=idx)
    calls = []

    def spy(f, r, h):
        calls.append((getattr(f, "name", None), h))
        return {"beta": 0.5, "nw_t": 2.5, "nw_sig": True}

    res = attribute_returns(sr, F, predictive_reg=spy, min_periods=12, ppy=12)
    assert res["ok_model"] is True
    assert len(calls) == 4                           # 每个因子调一次 _predictive_reg
    assert res["r2"] is not None and 0.0 <= res["r2"] <= 1.0
    names = [e["name"] for e in res["exposures"]]
    assert names == list(STYLE_FACTORS)
    bymap = {e["name"]: e["beta"] for e in res["exposures"]}
    assert bymap["MKT"] == pytest.approx(0.9, abs=0.12)   # 联合 β 近似恢复
    assert res["alpha"] == pytest.approx(0.001, abs=6e-4)
    # 每个 exposure 带边际视图(来自注入的 predictive_reg)
    assert all("marg_t" in e for e in res["exposures"])
