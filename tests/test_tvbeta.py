# -*- coding: utf-8 -*-
"""时变β(Kalman)模块 TDD —— guanlan_v2.workflow.tvbeta。

验证:OLS 基线、Kalman 滤波+RTS 平滑在「常数β→收敛OLS」「时变β→真跟踪」两态正确,
浓缩对数似然网格 MLE 选 qr,样本不足诚实降级,编排层对齐/置信带/JSON 安全。
纯 numpy/pandas,不碰引擎、无文件 IO。"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

from guanlan_v2.workflow import tvbeta as tv


# ── _ols 基线 ────────────────────────────────────────────────────────────────
def test_ols_recovers_known_coef():
    x = np.linspace(-1, 1, 50)
    y = 2.0 + 3.0 * x
    X = np.column_stack([np.ones_like(x), x])
    coef, rvar, r2 = tv._ols(y, X)
    assert coef is not None
    assert abs(coef[0] - 2.0) < 1e-9 and abs(coef[1] - 3.0) < 1e-9
    assert r2 is not None and r2 > 0.999999


def test_ols_singular_returns_none():
    x = np.ones(20)                       # 常数列 → 与截距共线 → 奇异
    X = np.column_stack([np.ones_like(x), x])
    coef, rvar, r2 = tv._ols(np.arange(20.0), X)
    assert coef is None


# ── Kalman: 常数β 应收敛到 OLS ───────────────────────────────────────────────
def test_kalman_constant_beta_converges_to_ols():
    rng = np.random.default_rng(0)
    n = 300
    m = rng.normal(0, 0.03, n)
    beta_true = 1.2
    y = 0.0 + beta_true * m + rng.normal(0, 0.004, n)
    X = np.column_stack([np.ones(n), m])
    res = tv.fit_tvp(y, X, min_obs=24)
    assert res["ok"]
    b_smooth = np.array([r[1] for r in res["coef_smooth"]])   # 第1列=β
    assert abs(float(np.mean(b_smooth)) - beta_true) < 0.15
    assert float(np.std(b_smooth)) < 0.25                      # 常数β → 平滑路径基本平
    # 与静态 OLS 一致
    coef, _, _ = tv._ols(y, X)
    assert abs(float(np.mean(b_smooth)) - coef[1]) < 0.2


# ── Kalman: 时变β 应真跟踪 ───────────────────────────────────────────────────
def test_kalman_tracks_time_varying_beta():
    rng = np.random.default_rng(7)
    n = 400
    m = rng.normal(0, 0.03, n)
    beta_true = np.linspace(0.5, 1.5, n)              # 斜坡:0.5 → 1.5
    y = beta_true * m + rng.normal(0, 0.003, n)
    X = np.column_stack([np.ones(n), m])
    res = tv.fit_tvp(y, X, min_obs=24)
    assert res["ok"]
    b_smooth = np.array([r[1] for r in res["coef_smooth"]])
    # 末段显著高于初段(真斜坡上行)
    assert float(np.mean(b_smooth[-40:])) - float(np.mean(b_smooth[:40])) > 0.3
    # 与真 β 路径正相关
    corr = float(np.corrcoef(b_smooth, beta_true)[0, 1])
    assert corr > 0.5
    # 选中的 qr 为有限正数(模型确实允许系数游走)
    assert res["qr"] is not None and res["qr"] > 0


def test_varying_beta_picks_larger_qr_than_constant():
    rng = np.random.default_rng(11)
    n = 400
    m = rng.normal(0, 0.03, n)
    yc = 1.0 * m + rng.normal(0, 0.003, n)                       # 常数β
    yv = np.linspace(0.3, 1.7, n) * m + rng.normal(0, 0.003, n)  # 时变β
    X = np.column_stack([np.ones(n), m])
    qc = tv.fit_tvp(yc, X, min_obs=24)["qr"]
    qv = tv.fit_tvp(yv, X, min_obs=24)["qr"]
    assert qc is not None and qv is not None
    assert qv > qc            # 时变序列选更大信噪比(更允许系数变动)


# ── 浓缩似然 / 平滑器 内核 ───────────────────────────────────────────────────
def test_filter_smoother_shapes_and_finite():
    rng = np.random.default_rng(3)
    n = 80
    m = rng.normal(0, 0.03, n)
    y = m + rng.normal(0, 0.005, n)
    X = np.column_stack([np.ones(n), m])
    flt = tv.kalman_filter(y, X, qr=0.01, x0=np.array([0.0, 1.0]), P0=np.eye(2) * 1e4)
    assert flt["x_filt"].shape == (n, 2)
    assert flt["P_filt"].shape == (n, 2, 2)
    sm = tv.rts_smoother(flt)
    assert sm["x_smooth"].shape == (n, 2)
    assert np.all(np.isfinite(sm["x_smooth"]))
    ll, rhat = tv.concentrated_loglik(flt["innov"], flt["innov_var"], k=2)
    assert math.isfinite(ll) and rhat > 0


# ── 编排层 time_varying_beta ─────────────────────────────────────────────────
def _mk_series(n, seed, beta_path):
    rng = np.random.default_rng(seed)
    m = rng.normal(0, 0.03, n)
    y = np.asarray(beta_path) * m + rng.normal(0, 0.003, n)
    idx = pd.date_range("2021-01-01", periods=n, freq="W")
    return pd.Series(y, index=idx), pd.Series(m, index=idx)


def test_time_varying_beta_full_pipeline():
    n = 300
    strat, mkt = _mk_series(n, 1, np.linspace(0.6, 1.4, n))
    res = tv.time_varying_beta(strat, mkt, min_periods=24)
    assert res["ok_model"] is True
    assert res["n"] == n
    assert res["static_beta"] is not None
    bp = res["beta_path"]
    assert len(bp) == n
    # 每点 [date, beta_filt, beta_smooth, lo, hi] 且置信带有序
    for row in bp:
        assert len(row) == 5
        _, bf, bs, lo, hi = row
        assert lo is None or hi is None or (lo <= bs <= hi)
    # summary 字段齐全 + JSON 安全(无 NaN/Inf)
    for kkey in ("beta_mean", "beta_start", "beta_end", "beta_min", "beta_max", "qr", "r2"):
        v = res[kkey]
        assert v is None or math.isfinite(float(v))


def test_time_varying_beta_inner_join_alignment():
    n = 200
    strat, mkt = _mk_series(n, 2, np.full(n, 1.0))
    mkt2 = mkt.iloc[20:]                          # 市场序列只覆盖后 180 期
    res = tv.time_varying_beta(strat, mkt2, min_periods=24)
    assert res["ok_model"] is True
    assert res["n"] == 180                        # 仅交集对齐


def test_time_varying_beta_nan_robustness():
    n = 200
    strat, mkt = _mk_series(n, 4, np.full(n, 0.9))
    mkt.iloc[50] = np.nan                         # 注入 NaN → 该期丢弃
    res = tv.time_varying_beta(strat, mkt, min_periods=24)
    assert res["ok_model"] is True
    assert res["n"] == n - 1


def test_time_varying_beta_short_sample_honest():
    n = 15
    strat, mkt = _mk_series(n, 5, np.full(n, 1.0))
    res = tv.time_varying_beta(strat, mkt, min_periods=24)
    assert res["ok_model"] is False
    assert res.get("reason")
    assert res["n"] == n


def test_time_varying_beta_degenerate_market_honest():
    idx = pd.date_range("2021-01-01", periods=60, freq="W")
    strat = pd.Series(np.linspace(0, 0.1, 60), index=idx)
    mkt = pd.Series(np.zeros(60), index=idx)      # 市场恒 0 → 设计阵奇异
    res = tv.time_varying_beta(strat, mkt, min_periods=24)
    assert res["ok_model"] is False
    assert res.get("reason")
