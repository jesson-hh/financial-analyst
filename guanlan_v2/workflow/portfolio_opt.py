# -*- coding: utf-8 -*-
"""组合优化器(guanlan 自有)—— 真协方差 + Ledoit-Wolf 收缩 + 三类定权闭式/迭代解。

补掉 ``_cross_weights`` 里 ``risk_parity`` 的「反波动伪实现」(只用对角波动、未用协方差矩阵)。
本模块用**选中票截至调仓日的收益协方差矩阵**(Ledoit-Wolf 收缩,N≫T 病态时稳健)算:

  · ``min_var``           最小方差     w = argmin wᵀΣw  s.t. Σw=1, w≥0(long-only QP)
  · ``max_sharpe``        最大夏普切点  w ∝ Σ⁻¹μ(μ=因子分代理的预期收益视图,去均值;long-only 截负)
  · ``true_risk_parity``  真风险平价    等风险贡献 ERC:wᵢ·(Σw)ᵢ 全相等(乘性迭代)

口径锚定量化 wiki(基本概念/投资理论·资产组合理论;百宝箱/分析工具·优化工具)。
纯 ``numpy``;Ledoit-Wolf / SLSQP 惰性 import ``sklearn`` / ``scipy``,不可用 → 诚实降级
(手写收缩 + 闭式 Σ⁻¹ 截负)。**不碰引擎、不碰 stock_data、无文件 IO**;只吃内存收益矩阵。

被 ``guanlan_v2.workflow.api._cross_weights`` 调用(weighting ∈ 下列三方案时)。
"""
from __future__ import annotations

from typing import Optional, Tuple

import numpy as np

# _cross_weights / _topn_portfolio / _backtest_vector 的白名单需与此一致。
OPT_SCHEMES = ("min_var", "max_sharpe", "true_risk_parity")


def shrink_cov(R: np.ndarray) -> Tuple[np.ndarray, str]:
    """Ledoit-Wolf 收缩协方差。``R``: (T 样本 × N 资产) 收益矩阵(已去 NaN,矩形)。
    返回 ``(Sigma NxN, note)``。sklearn 不可用 → 手写「向缩放单位阵收缩」兜底。"""
    R = np.asarray(R, dtype=float)
    N = R.shape[1]
    try:
        from sklearn.covariance import LedoitWolf

        lw = LedoitWolf().fit(R)
        return np.asarray(lw.covariance_, dtype=float), f"Ledoit-Wolf 收缩 δ={float(lw.shrinkage_):.3f}"
    except Exception:  # noqa: BLE001  sklearn 不可用 → 手写收缩
        S = np.cov(R, rowvar=False)
        if np.ndim(S) == 0:
            S = np.asarray(S, dtype=float).reshape(1, 1)
        mu_var = float(np.trace(S) / max(1, N))
        delta = 0.2  # 固定收缩强度(Ledoit-Wolf 风格的保守近似)
        return (1.0 - delta) * S + delta * mu_var * np.eye(N), f"手写收缩 δ={delta:.2f}(sklearn 不可用)"


def rescale_cov_vols(Sigma: np.ndarray, target_vols: np.ndarray) -> np.ndarray:
    """把协方差矩阵的**对角波动换成 ``target_vols``、保留历史相关结构**(单变量 GARCH/EWMA
    预测注入多元 Σ 的标准做法,DCC-lite):``Σ' = D_t·C·D_t``,C=历史相关,D_t=diag(目标波动)。
    目标非法(NaN/≤0)或历史方差≤0(病态)的资产 → **保留其原始波动/原行列不变**。
    实现 = 对角同余缩放 ``Σ' = Σ ⊙ (s sᵀ)``,``s_i=σ_target,i / σ_hist,i``(保相关)。"""
    S = np.asarray(Sigma, dtype=float)
    dh = np.sqrt(np.clip(np.diag(S), 0.0, None))            # 历史波动
    tv = np.asarray(target_vols, dtype=float).reshape(-1)
    valid = np.isfinite(tv) & (tv > 0) & (dh > 0)
    dt = np.where(valid, tv, dh)                            # 非法 → 退历史波动
    with np.errstate(divide="ignore", invalid="ignore"):
        s = np.where(dh > 0, dt / dh, 1.0)                 # 病态(dh=0)处缩放=1,保原行列
    s = np.where(np.isfinite(s), s, 1.0)
    return S * np.outer(s, s)


def _normalize_long(w: np.ndarray) -> np.ndarray:
    """截负 + 归一为 long-only 权重(∑=1);全零 → 等权。"""
    w = np.clip(np.asarray(w, dtype=float), 0.0, None)
    s = float(w.sum())
    return (w / s) if s > 0 else np.full(len(w), 1.0 / max(1, len(w)))


def min_var_weights(Sigma: np.ndarray) -> np.ndarray:
    """长仓最小方差。优先 scipy SLSQP(w≥0,∑=1 的真约束 QP);不可用 → 闭式 Σ⁻¹1 截负重归一。"""
    N = Sigma.shape[0]
    try:
        from scipy.optimize import minimize

        w0 = np.full(N, 1.0 / N)
        cons = ({"type": "eq", "fun": lambda w: float(w.sum() - 1.0)},)
        bnds = tuple((0.0, 1.0) for _ in range(N))
        res = minimize(lambda w: float(w @ Sigma @ w), w0, method="SLSQP",
                       bounds=bnds, constraints=cons, options={"maxiter": 250, "ftol": 1e-11})
        if getattr(res, "success", False) and np.all(np.isfinite(res.x)):
            return _normalize_long(res.x)
    except Exception:  # noqa: BLE001
        pass
    try:
        inv1 = np.linalg.solve(Sigma + 1e-8 * np.eye(N), np.ones(N))
        return _normalize_long(inv1)
    except Exception:  # noqa: BLE001
        return np.full(N, 1.0 / N)


def max_sharpe_weights(Sigma: np.ndarray, mu: np.ndarray) -> np.ndarray:
    """长仓最大夏普切点 w ∝ Σ⁻¹μ,截负重归一(long-only 近似)。μ 视图全无正向 → 退最小方差。"""
    N = Sigma.shape[0]
    mu = np.asarray(mu, dtype=float)
    mu = mu - float(np.nanmean(mu))            # 去均值 → 相对视图(scale 无关的多空倾斜)
    mu = np.where(np.isfinite(mu), mu, 0.0)
    if not np.any(mu > 0):
        return min_var_weights(Sigma)
    try:
        w = np.linalg.solve(Sigma + 1e-8 * np.eye(N), mu)
        wn = _normalize_long(w)
        if float(wn.sum()) > 0 and np.any(wn > 0):
            return wn
    except Exception:  # noqa: BLE001
        pass
    return min_var_weights(Sigma)


def risk_parity_weights(Sigma: np.ndarray, iters: int = 2000, tol: float = 1e-12) -> np.ndarray:
    """真风险平价(ERC:各资产**风险贡献** RCᵢ = wᵢ·(Σw)ᵢ 相等,非边际风险相等)。

    主算法 = **Spinu(2013)凸形式**:最小化 ``½wᵀΣw − Σbᵢ·ln(wᵢ)``(b=1/N),梯度 ``Σw − b/w``,
    最优处 ``wᵢ·(Σw)ᵢ = bᵢ`` 严格等风险贡献(凸 → L-BFGS-B 可靠收敛)。scipy 不可用 → sqrt 阻尼
    乘性迭代兜底。Σ 病态(对角≤0/含 NaN)→ 回退反波动(对角口径)。"""
    N = Sigma.shape[0]
    diag = np.diag(Sigma)
    if np.any(diag <= 0) or not np.all(np.isfinite(Sigma)):
        return _normalize_long(1.0 / np.sqrt(np.clip(diag, 1e-12, None)))
    w0 = _normalize_long(1.0 / np.sqrt(np.clip(diag, 1e-12, None)))   # 反波动初值
    b = np.full(N, 1.0 / N)
    try:
        from scipy.optimize import minimize

        def _obj(w):
            return 0.5 * float(w @ Sigma @ w) - float(b @ np.log(w))

        def _grad(w):
            return Sigma @ w - b / w

        res = minimize(_obj, w0, jac=_grad, method="L-BFGS-B",
                       bounds=[(1e-9, None)] * N, options={"maxiter": 500, "ftol": 1e-14})
        if getattr(res, "success", False) and np.all(np.isfinite(res.x)) and bool((res.x > 0).all()):
            return _normalize_long(res.x)
    except Exception:  # noqa: BLE001  scipy 不可用 → 乘性迭代兜底
        pass
    w = w0
    for _ in range(iters):
        rc = np.clip(w * (Sigma @ w), 1e-15, None)   # 风险贡献 wᵢ·(Σw)ᵢ(非边际)
        w_new = _normalize_long(w * np.sqrt(b / rc))  # sqrt 阻尼朝等风险贡献收敛
        if float(np.abs(w_new - w).sum()) < tol:
            return w_new
        w = w_new
    return w


def optimize_weights(R: np.ndarray, scheme: str,
                     mu: Optional[np.ndarray] = None,
                     target_vols: Optional[np.ndarray] = None) -> Tuple[Optional[np.ndarray], str]:
    """统一入口。``R``=(T×N) 收益矩阵(列序 = 资产序);``scheme`` ∈ :data:`OPT_SCHEMES`。
    ``target_vols``(可选,长度 N)= 每资产**预测波动**(EWMA/GARCH);给定则把 Ledoit-Wolf Σ 的
    对角波动换成预测值、保历史相关(:func:`rescale_cov_vols`)→ 优化器用「预测风险」而非历史风险定权。
    返回 ``(weights np.ndarray[N], note)``;样本不足(T<max(30, N+5))→ ``(None, reason)`` 让调用方降级。"""
    R = np.asarray(R, dtype=float)
    if R.ndim != 2:
        return None, "收益矩阵非二维"
    T, N = R.shape
    if N < 2:
        return None, f"资产数不足(N={N})"
    if T < 30 or T < N + 5:
        return None, f"协方差样本不足(T={T}, N={N};需 T≥max(30, N+5))"
    Sigma, note = shrink_cov(R)
    if target_vols is not None:
        Sigma = rescale_cov_vols(Sigma, target_vols)
        note += "·对角=预测波动"
    if scheme == "min_var":
        w = min_var_weights(Sigma)
    elif scheme == "max_sharpe":
        if mu is None:
            mu = np.nanmean(R, axis=0)
            note += "·μ=历史均值(噪声大)"
        else:
            note += "·μ=因子分视图"
        w = max_sharpe_weights(Sigma, np.asarray(mu, dtype=float))
    elif scheme == "true_risk_parity":
        w = risk_parity_weights(Sigma)
    else:
        return None, f"未知优化方案 {scheme}"
    if not np.all(np.isfinite(w)) or float(np.sum(w)) <= 0:
        return None, "优化解非法(NaN/全零)"
    return w, note


# 正态分位(成分 VaR 用;与 api._risk_block 的 z95/z99 口径一致)。
_RC_Z = {"95": 1.6448536269514722, "99": 2.3263478740408408}


def risk_contributions(Sigma: np.ndarray, w: np.ndarray) -> dict:
    """组合层风险归因(欧拉分解)。给权重 ``w`` 与协方差 ``Σ``:
      · 组合波动        σ_p  = √(wᵀΣw)
      · 边际风险贡献    MCRᵢ = (Σw)ᵢ / σ_p
      · 成分风险贡献    CRᵢ  = wᵢ·MCRᵢ   (欧拉恒等 Σ CRᵢ = σ_p)
      · 风险占比        pctᵢ = CRᵢ / σ_p  (Σ pctᵢ = 1)
      · 成分 VaR_α,ᵢ    = z_α·CRᵢ         (正态近似;同 _risk_block z 口径)

    返回 ``dict{port_vol, mcr, cr, pct, comp_var95, comp_var99}``(后五者 list[float])。
    σ_p≤0(零方差/病态)→ 诚实降级:``port_vol=0``、其余 ``None``(占比 0/0 无定义,绝不编 0)。
    **纯描述性**:当前持仓的风险结构快照,非历史业绩、非回测;成分 VaR 为正态近似(消费方须标注)。
    与 :func:`risk_parity_weights` 互证:真 ERC 权重下各 ``pct`` 应≈1/N。"""
    S = np.asarray(Sigma, dtype=float)
    w = np.asarray(w, dtype=float).reshape(-1)
    Sw = S @ w
    var_p = float(w @ Sw)
    if not np.isfinite(var_p) or var_p <= 0:
        return {"port_vol": 0.0, "mcr": None, "cr": None, "pct": None,
                "comp_var95": None, "comp_var99": None}
    sp = float(np.sqrt(var_p))
    mcr = Sw / sp
    cr = w * mcr
    return {
        "port_vol": sp,
        "mcr": [float(x) for x in mcr],
        "cr": [float(x) for x in cr],
        "pct": [float(x) for x in (cr / sp)],
        "comp_var95": [float(_RC_Z["95"] * x) for x in cr],
        "comp_var99": [float(_RC_Z["99"] * x) for x in cr],
    }


def black_litterman_posterior(Sigma, w_mkt, P, Q, Omega, delta: float = 2.5, tau: float = 0.05):
    """Black-Litterman 后验预期(超额)收益 + 均衡先验。口径锚 wiki『Black-Litterman / 主动组合管理』。
      · 均衡先验(反向优化)  Π = δ·Σ·w_mkt   (w_mkt=市值比例,δ=风险厌恶系数)
      · 观点后验             E[R] = [(τΣ)⁻¹ + PᵀΩ⁻¹P]⁻¹ · [(τΣ)⁻¹Π + PᵀΩ⁻¹Q]

    ``P`` (K×N 观点拾取矩阵) / ``Q`` (K 观点收益) / ``Ω`` (K×K 观点不确定性,对角越小=越确信)。
    无观点(P 行数=0)→ 后验=先验 Π(贝叶斯自洽)。返回 ``(E_R, Pi)``;Σ/Ω 求逆失败 → ``(None, Pi)``。
    **纯 numpy**;不碰数据/不碰执行。观点须 PIT(只用 ≤asof 的研判),Σ 用 ≤asof 收益(调用方保证)。"""
    S = np.asarray(Sigma, dtype=float)
    wm = np.asarray(w_mkt, dtype=float).reshape(-1)
    N = S.shape[0]
    Pi = float(delta) * (S @ wm)                            # 均衡先验超额收益
    P = np.asarray(P, dtype=float)
    P = P.reshape(-1, N) if P.size else np.zeros((0, N))
    if P.shape[0] == 0:
        return Pi.copy(), Pi                               # 无观点 → 后验=先验
    Q = np.asarray(Q, dtype=float).reshape(-1)
    K = P.shape[0]
    Om = np.asarray(Omega, dtype=float).reshape(K, K)
    try:
        tauS_inv = np.linalg.inv(tau * S)
        Om_inv = np.linalg.inv(Om)
        A = tauS_inv + P.T @ Om_inv @ P
        b = tauS_inv @ Pi + P.T @ Om_inv @ Q
        ER = np.linalg.solve(A, b)
        if not np.all(np.isfinite(ER)):
            return None, Pi
        return ER, Pi
    except Exception:  # noqa: BLE001 — Σ/Ω 求逆失败 → 诚实降级(后验不可得)
        return None, Pi


def black_litterman_weights(Sigma, w_mkt, P, Q, Omega, delta: float = 2.5, tau: float = 0.05):
    """BL 长仓权重 = 后验 E[R] 喂均值-方差最优 ``w ∝ Σ⁻¹E[R]``(截负归一)。
    **不去均值**(后验 E[R] 已含均衡基线)→ 无观点时 ``Σ⁻¹Π = δ·w_mkt ∝ w_mkt``(权重回到市值,自洽铁律)。
    后验失败 / Σ 奇异 → ``None``(让调用方降级)。"""
    ER, _Pi = black_litterman_posterior(Sigma, w_mkt, P, Q, Omega, delta=delta, tau=tau)
    if ER is None:
        return None
    S = np.asarray(Sigma, dtype=float)
    N = S.shape[0]
    try:
        w = np.linalg.solve(S + 1e-8 * np.eye(N), ER)
        wn = _normalize_long(w)
        if np.all(np.isfinite(wn)) and float(wn.sum()) > 0:
            return wn
    except Exception:  # noqa: BLE001
        pass
    return None
