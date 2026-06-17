# -*- coding: utf-8 -*-
"""条件波动模型(guanlan 自有)—— EWMA(RiskMetrics)+ GARCH(1,1) MLE + 多步预测。

补掉平台「波动率预测层」缺口:现有只有**历史/滚动**波动(回看),本模块给**条件/预测**波动
(波动聚集 → 预测下期 σ)。纯 ``numpy``;MLE 用 ``scipy.optimize`` 惰性 import,不可用 →
诚实降级到 EWMA 等价参数(α=1-λ, β=λ)并标 ``converged=False``。

  · ``ewma_vol``          指数加权移动波动(λ=0.94 日频 RiskMetrics)
  · ``garch11_filter``    给定 (ω,α,β) 的条件方差递推 hₜ=ω+α·r²ₜ₋₁+β·hₜ₋₁
  · ``garch11_fit``       高斯 MLE 拟合 (ω,α,β)(内部按样本标准差缩放求数值稳健)
  · ``garch11_forecast``  k 步方差预测 σ²_{T+k}=σ²∞+(α+β)^{k-1}(h_{T+1}-σ²∞) 均值回复
  · ``fit_vol_models``    统一入口:吃收益序列 → EWMA/GARCH 路径 + 预测 + 年化诊断

口径锚定量化 wiki(数据分析/时间序列·波动率建模)。**不碰引擎、不碰 stock_data、无文件 IO**;
只吃内存收益数组。被 ``guanlan_v2.workflow.api._garch`` 调用。
"""
from __future__ import annotations

from typing import Dict, Optional

import numpy as np

EWMA_LAMBDA = 0.94          # RiskMetrics 日频衰减
MIN_OBS = 60                # 拟合 GARCH 的最少样本(诚实门槛)
_LOG2PI = float(np.log(2.0 * np.pi))


# ── EWMA ──────────────────────────────────────────────────────────────────────
def ewma_vol(returns: np.ndarray, lam: float = EWMA_LAMBDA) -> np.ndarray:
    """指数加权移动波动(每期 σ),与输入等长。σ²ₜ=λ·σ²ₜ₋₁+(1-λ)·r²ₜ₋₁,首格用 r[0]² 播种。"""
    r = np.asarray(returns, dtype=float)
    n = len(r)
    if n == 0:
        return np.asarray([], dtype=float)
    var = np.empty(n)
    var[0] = r[0] ** 2
    for t in range(1, n):
        var[t] = lam * var[t - 1] + (1.0 - lam) * r[t - 1] ** 2
    return np.sqrt(np.clip(var, 0.0, None))


def ewma_forecast_vol(returns: np.ndarray, lam: float = EWMA_LAMBDA) -> float:
    """下一期 EWMA 预测 σ:把方差递推到末(σ²ₙ₋₁,用到 r[n-2])再走一步用 r[n-1] →
    σ²_pred = λ·σ²ₙ₋₁ + (1-λ)·r²ₙ₋₁。NaN 剔除;空/单点 → 0。"""
    r = np.asarray(returns, dtype=float)
    r = r[np.isfinite(r)]
    if r.size == 0:
        return 0.0
    var = r[0] ** 2
    for t in range(1, r.size):
        var = lam * var + (1.0 - lam) * r[t - 1] ** 2
    fvar = lam * var + (1.0 - lam) * r[-1] ** 2
    return float(np.sqrt(max(fvar, 0.0)))


# ── GARCH(1,1) 滤波 ───────────────────────────────────────────────────────────
def garch11_filter(returns: np.ndarray, omega: float, alpha: float, beta: float,
                   h0: Optional[float] = None) -> np.ndarray:
    """给定参数的条件方差路径。h[0]=h0(缺省=样本方差);h[t]=ω+α·r[t-1]²+β·h[t-1]。"""
    r = np.asarray(returns, dtype=float)
    n = len(r)
    h = np.empty(n)
    if n == 0:
        return h
    h[0] = float(np.var(r)) if h0 is None else float(h0)
    for t in range(1, n):
        h[t] = omega + alpha * r[t - 1] ** 2 + beta * h[t - 1]
    return h


def _neg_loglik(theta: np.ndarray, r: np.ndarray, h0: float) -> float:
    omega, alpha, beta = theta
    h = garch11_filter(r, omega, alpha, beta, h0=h0)
    h = np.clip(h, 1e-300, None)
    return 0.5 * float(np.sum(_LOG2PI + np.log(h) + r ** 2 / h))


def _pack_garch(r: np.ndarray, omega: float, alpha: float, beta: float,
                converged: bool) -> Dict[str, float]:
    omega = max(float(omega), 1e-300)
    persistence = float(alpha + beta)
    uncond_var = omega / (1.0 - persistence) if persistence < 1.0 else float(np.var(r))
    uncond_var = max(uncond_var, 1e-300)
    loglik = -_neg_loglik(np.array([omega, alpha, beta]), r, float(np.var(r))) if len(r) else float("nan")
    return {
        "omega": omega,
        "alpha": float(alpha),
        "beta": float(beta),
        "persistence": persistence,
        "uncond_var": uncond_var,
        "uncond_vol": float(np.sqrt(uncond_var)),
        "loglik": float(loglik),
        "converged": bool(converged),
    }


def _default_garch(r: np.ndarray, lam: float = EWMA_LAMBDA) -> Dict[str, float]:
    """scipy 不可用 / 拟合失败时的诚实降级:EWMA 等价 GARCH(α=1-λ, β=λ),ω 配样本方差。"""
    sv = float(np.var(r)) if len(r) else 0.0
    alpha, beta = 1.0 - lam, lam
    omega = sv * (1.0 - alpha - beta) if (1.0 - alpha - beta) > 0 else sv * 1e-3
    return _pack_garch(r, max(omega, 1e-12), alpha, beta, converged=False)


def garch11_fit(returns: np.ndarray) -> Dict[str, float]:
    """高斯 MLE 拟合 GARCH(1,1)。内部按样本标准差缩放(r→r/sd,方差≈1)求数值稳健,
    再把 ω 换回原始单位(α,β 缩放无关)。scipy 不可用 / 不收敛 → :func:`_default_garch`。"""
    r = np.asarray(returns, dtype=float)
    r = r[np.isfinite(r)]
    sd = float(np.std(r))
    if len(r) < 5 or sd <= 0:
        return _default_garch(r)
    c = 1.0 / sd                                    # 缩放到单位方差
    rs = r * c
    h0 = float(np.var(rs))                          # ≈1
    try:
        from scipy.optimize import minimize

        x0 = np.array([h0 * 0.05, 0.05, 0.90])      # ω≈var·(1-持续度), α,β 典型日频值
        bnds = [(1e-8, None), (0.0, 1.0), (0.0, 1.0)]
        cons = ({"type": "ineq", "fun": lambda t: 1.0 - t[1] - t[2] - 1e-6},)  # 平稳性 α+β<1
        res = minimize(_neg_loglik, x0, args=(rs, h0), method="SLSQP",
                       bounds=bnds, constraints=cons,
                       options={"maxiter": 500, "ftol": 1e-10})
        if getattr(res, "success", False) and np.all(np.isfinite(res.x)):
            omega_s, alpha, beta = res.x
            omega = float(omega_s) / (c * c)         # ω 换回原始单位
            if omega > 0 and alpha >= 0 and beta >= 0 and (alpha + beta) < 1.0:
                return _pack_garch(r, omega, float(alpha), float(beta), converged=True)
    except Exception:  # noqa: BLE001  scipy 不可用 → 降级
        pass
    return _default_garch(r)


# ── 多步预测 ──────────────────────────────────────────────────────────────────
def garch11_forecast(returns: np.ndarray, params: Dict[str, float], horizon: int = 10) -> np.ndarray:
    """k 步方差预测(返回长度 horizon 的方差路径,fc[0]=一步前 h_{T+1})。
    σ²_{T+k}=σ²∞+(α+β)^{k-1}·(h_{T+1}-σ²∞),k=1..horizon,长程均值回复到无条件方差。"""
    r = np.asarray(returns, dtype=float)
    omega, alpha, beta = params["omega"], params["alpha"], params["beta"]
    uncond = params["uncond_var"]
    h = garch11_filter(r, omega, alpha, beta)
    h_next = omega + alpha * r[-1] ** 2 + beta * h[-1]      # 一步前条件方差(T 时已知)
    pers = alpha + beta
    k = np.arange(int(max(1, horizon)))                     # 指数 0..horizon-1
    return uncond + (pers ** k) * (h_next - uncond)


def forecast_sigma(returns: np.ndarray, model: str = "garch",
                   cached_params: Optional[Dict[str, float]] = None):
    """一步前预测 σ(供组合优化器 ``vol_asof`` 钩子用)。``model`` ∈ {``ewma``, ``garch``}。
    garch 传 ``cached_params`` → **复用参数跳过重拟合**,但 σ 仍按传入收益重算(适配最新数据·
    防前视靠调用方切片 ≤asof)。返回 ``(sigma, params)``:ewma → params=None;garch → params 字典。"""
    r = np.asarray(returns, dtype=float)
    r = r[np.isfinite(r)]
    if r.size < 2:
        return 0.0, (cached_params if model == "garch" else None)
    if model == "ewma":
        return ewma_forecast_vol(r), None
    params = cached_params if cached_params is not None else garch11_fit(r)
    fvar = float(garch11_forecast(r, params, horizon=1)[0])
    return float(np.sqrt(max(fvar, 0.0))), params


# ── 统一入口 ──────────────────────────────────────────────────────────────────
def fit_vol_models(returns: np.ndarray, periods_per_year: int = 252,
                   horizon: int = 10, ewma_lambda: float = EWMA_LAMBDA) -> Dict:
    """吃收益序列 → EWMA/GARCH 条件波动路径 + 多步预测 + 年化诊断。
    NaN 剔除;样本 < :data:`MIN_OBS` 或零波动 → ``{"ok": False, "reason": ...}`` 诚实降级。"""
    r = np.asarray(returns, dtype=float)
    r = r[np.isfinite(r)]
    n = len(r)
    if n < MIN_OBS:
        return {"ok": False, "n": n, "reason": f"样本不足(n={n} < {MIN_OBS},无法稳健拟合 GARCH)"}
    if float(np.std(r)) <= 0:
        return {"ok": False, "n": n, "reason": "零波动(收益恒定),无条件波动模型不适用"}

    ann = float(np.sqrt(periods_per_year))
    ewma = ewma_vol(r, ewma_lambda)
    g = garch11_fit(r)
    g["uncond_vol_annual"] = g["uncond_vol"] * ann

    gvar = garch11_filter(r, g["omega"], g["alpha"], g["beta"])
    gvol = np.sqrt(np.clip(gvar, 0.0, None))
    fc_var = garch11_forecast(r, g, horizon)
    fc_vol = np.sqrt(np.clip(fc_var, 0.0, None))

    return {
        "ok": True,
        "n": n,
        "periods_per_year": int(periods_per_year),
        "horizon": int(max(1, horizon)),
        "ewma_lambda": float(ewma_lambda),
        "ewma_vol": ewma,
        "ewma_vol_last": float(ewma[-1]),
        "ewma_vol_annual": float(ewma[-1] * ann),
        "garch": g,
        "garch_vol": gvol,
        "garch_vol_last": float(gvol[-1]),
        "garch_vol_annual_last": float(gvol[-1] * ann),
        "forecast_vol": fc_vol,
        "forecast_vol_annual": fc_vol * ann,
    }
