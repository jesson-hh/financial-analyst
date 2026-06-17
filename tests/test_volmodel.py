# tests/test_volmodel.py
# 条件波动模型(guanlan_v2/workflow/volmodel.py)的纯数学门禁:
#   EWMA(RiskMetrics)+ GARCH(1,1) MLE + 多步预测。
# 不碰数据、不连服务器、不依赖 engine —— 纯 numpy/scipy,确定性(固定 rng 种子)。
import numpy as np
import pytest

from guanlan_v2.workflow.volmodel import (
    ewma_vol,
    ewma_forecast_vol,
    forecast_sigma,
    garch11_fit,
    garch11_filter,
    garch11_forecast,
    fit_vol_models,
)


# ── 测试用 GARCH(1,1) 生成器(确定性,固定种子)──────────────────────────────
def _simulate_garch(n, omega, alpha, beta, seed):
    rng = np.random.default_rng(seed)
    z = rng.standard_normal(n)
    h = np.empty(n)
    r = np.empty(n)
    h[0] = omega / (1.0 - alpha - beta)        # 从无条件方差起步
    r[0] = np.sqrt(h[0]) * z[0]
    for t in range(1, n):
        h[t] = omega + alpha * r[t - 1] ** 2 + beta * h[t - 1]
        r[t] = np.sqrt(h[t]) * z[t]
    return r


# ── EWMA ────────────────────────────────────────────────────────────────────
def test_ewma_recursion_matches_manual():
    """σ²_t = λ σ²_{t-1} + (1-λ) r²_{t-1};首格用 r[0]² 播种。逐位对手算。"""
    r = np.array([0.01, -0.02, 0.015, -0.005])
    lam = 0.94
    out = ewma_vol(r, lam)
    var = [r[0] ** 2]
    for t in range(1, len(r)):
        var.append(lam * var[t - 1] + (1.0 - lam) * r[t - 1] ** 2)
    assert out == pytest.approx(np.sqrt(var), rel=1e-9)


def test_ewma_length_matches_input():
    r = np.linspace(-0.01, 0.01, 50)
    assert len(ewma_vol(r, 0.94)) == 50


def test_ewma_responds_to_vol_spike():
    """一段平静收益后接一个大冲击 → 冲击后的 EWMA 波动应明显高于冲击前。"""
    calm = np.full(60, 0.001)
    spike = np.array([0.10])              # 单个大冲击
    r = np.concatenate([calm, spike, calm])
    out = ewma_vol(r, 0.94)
    assert out[-1] > out[55] * 3.0        # 冲击后远高于冲击前平静水平


# ── GARCH(1,1) 滤波(给定参数,确定性递推)─────────────────────────────────
def test_garch_filter_recursion_exact():
    """h[0]=h0;h[t]=ω+α·r[t-1]²+β·h[t-1](t 时条件方差只用 t-1 前信息)。"""
    r = np.array([0.01, -0.02, 0.015, -0.005])
    omega, alpha, beta = 1e-5, 0.10, 0.85
    h0 = 4e-4
    h = garch11_filter(r, omega, alpha, beta, h0=h0)
    exp = [h0]
    for t in range(1, len(r)):
        exp.append(omega + alpha * r[t - 1] ** 2 + beta * exp[t - 1])
    assert h == pytest.approx(exp, rel=1e-9)


def test_garch_filter_default_h0_is_sample_var():
    r = np.array([0.01, -0.02, 0.015, -0.005, 0.02])
    h = garch11_filter(r, 1e-5, 0.1, 0.85)
    assert h[0] == pytest.approx(float(np.var(r)), rel=1e-9)


# ── GARCH(1,1) MLE 拟合 ──────────────────────────────────────────────────────
def test_garch_fit_respects_stationarity():
    r = _simulate_garch(3000, omega=1.125e-5, alpha=0.08, beta=0.87, seed=7)
    g = garch11_fit(r)
    assert g["omega"] > 0
    assert g["alpha"] >= 0
    assert g["beta"] >= 0
    assert g["alpha"] + g["beta"] < 1.0          # 平稳性
    assert np.isfinite(g["loglik"])


def test_garch_fit_recovers_known_params():
    """模拟已知 (ω,α,β) 的 GARCH 过程,MLE 应恢复持续度与参数(宽容差,n=6000)。"""
    true_alpha, true_beta = 0.08, 0.87           # 持续度 0.95
    r = _simulate_garch(6000, omega=1.125e-5, alpha=true_alpha, beta=true_beta, seed=42)
    g = garch11_fit(r)
    assert g["converged"]
    assert g["persistence"] == pytest.approx(0.95, abs=0.05)
    assert 0.02 < g["alpha"] < 0.18
    assert 0.78 < g["beta"] < 0.96
    assert 0.005 < g["uncond_vol"] < 0.05        # 真值 1.5% 的安全带


# ── 多步预测 ──────────────────────────────────────────────────────────────────
def test_garch_forecast_first_step_matches_onestep_filter():
    """预测第 1 步 = 样本末的一步前条件方差 h_{T+1}=ω+α·r_T²+β·h_T。"""
    r = _simulate_garch(1500, omega=1.125e-5, alpha=0.08, beta=0.87, seed=3)
    g = garch11_fit(r)
    h = garch11_filter(r, g["omega"], g["alpha"], g["beta"])
    h_next = g["omega"] + g["alpha"] * r[-1] ** 2 + g["beta"] * h[-1]
    fc = garch11_forecast(r, g, horizon=10)       # 返回方差路径
    assert fc[0] == pytest.approx(h_next, rel=1e-9)


def test_garch_forecast_reverts_to_unconditional():
    """长程预测均值回复到无条件方差 ω/(1-α-β)。"""
    r = _simulate_garch(2000, omega=1.125e-5, alpha=0.08, beta=0.87, seed=11)
    g = garch11_fit(r)
    fc = garch11_forecast(r, g, horizon=400)
    assert fc[-1] == pytest.approx(g["uncond_var"], rel=0.02)


# ── 统一入口 fit_vol_models ───────────────────────────────────────────────────
def test_fit_vol_models_short_series_honest_none():
    g = fit_vol_models(np.array([0.01, -0.02, 0.0]), periods_per_year=252)
    assert g["ok"] is False
    assert "reason" in g and g["reason"]


def test_fit_vol_models_basic_shape_and_annualization():
    r = _simulate_garch(800, omega=1.125e-5, alpha=0.08, beta=0.87, seed=5)
    g = fit_vol_models(r, periods_per_year=252, horizon=10)
    assert g["ok"] is True
    assert g["n"] == 800
    assert len(g["garch_vol"]) == 800
    assert len(g["forecast_vol"]) == 10
    # 年化 = 每期 × sqrt(ppy)
    assert g["garch"]["uncond_vol_annual"] == pytest.approx(
        g["garch"]["uncond_vol"] * np.sqrt(252), rel=1e-9)
    assert g["forecast_vol_annual"][0] == pytest.approx(
        g["forecast_vol"][0] * np.sqrt(252), rel=1e-9)


def test_fit_vol_models_handles_nan():
    r = _simulate_garch(400, omega=1.125e-5, alpha=0.08, beta=0.87, seed=9)
    r = r.copy()
    r[10] = np.nan
    r[200] = np.nan
    g = fit_vol_models(r, periods_per_year=252)
    assert g["ok"] is True
    assert g["n"] == 398                          # 两个 NaN 被剔除
    assert np.all(np.isfinite(g["garch_vol"]))


# ── 一步前预测 σ(供组合优化器 vol_asof 钩子用)─────────────────────────────────
def test_ewma_forecast_vol_matches_manual():
    """下一期 EWMA 预测 σ = sqrt(λ·var_last + (1-λ)·r_last²);var 递推到末。"""
    r = np.array([0.01, -0.02, 0.015, -0.005])
    lam = 0.94
    var = [r[0] ** 2]
    for t in range(1, len(r)):
        var.append(lam * var[t - 1] + (1.0 - lam) * r[t - 1] ** 2)
    expect = (lam * var[-1] + (1.0 - lam) * r[-1] ** 2) ** 0.5
    assert ewma_forecast_vol(r, lam) == pytest.approx(expect, rel=1e-9)


def test_forecast_sigma_ewma_no_params():
    r = _simulate_garch(300, omega=1.125e-5, alpha=0.08, beta=0.87, seed=2)
    sigma, params = forecast_sigma(r, model="ewma")
    assert params is None
    assert sigma == pytest.approx(ewma_forecast_vol(r), rel=1e-9)
    assert sigma > 0


def test_forecast_sigma_garch_matches_onestep_forecast():
    r = _simulate_garch(1200, omega=1.125e-5, alpha=0.08, beta=0.87, seed=4)
    sigma, params = forecast_sigma(r, model="garch")
    assert params is not None and "alpha" in params
    expect = float(garch11_forecast(r, params, horizon=1)[0]) ** 0.5
    assert sigma == pytest.approx(expect, rel=1e-9)
    assert sigma > 0


def test_forecast_sigma_garch_cached_skips_refit():
    """传 cached_params → 复用同一参数对象(不重拟合),σ 仍按传入收益重算(适配新数据)。"""
    r = _simulate_garch(1200, omega=1.125e-5, alpha=0.08, beta=0.87, seed=6)
    sigma1, p1 = forecast_sigma(r, model="garch")
    sigma2, p2 = forecast_sigma(r, model="garch", cached_params=p1)
    assert p2 is p1                                # 复用,未重拟合
    assert sigma2 == pytest.approx(sigma1, rel=1e-9)
    sigma3, p3 = forecast_sigma(r[:-100], model="garch", cached_params=p1)
    assert p3 is p1
    assert sigma3 > 0 and np.isfinite(sigma3)      # 新收益 → 重算 σ(参数复用)
