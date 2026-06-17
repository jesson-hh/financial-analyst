# -*- coding: utf-8 -*-
"""时变β(Kalman / 经典状态空间)—— guanlan 自有。

把 ``attrib`` 的「静态因子β」延伸成「随时间演化的市场β」:对策略期收益 r_t 与市场期收益 m_t
拟合时变参数(TVP)回归 ``r_t = α_t + β_t·m_t + ε_t``,系数 ``[α_t, β_t]`` 服从随机游走
(局部水平模型)。用 Kalman 滤波(因果、单边)+ RTS 平滑(全样本、双边)估系数路径;观测噪声 R
被解析消去(浓缩似然),在信噪比 qr 上做网格 MLE 自动选平滑度。

  · ``_ols``                 普通 OLS(静态 β 基线;秩亏/奇异 → None)
  · ``kalman_filter``        前向滤波(R=1 单位;Q=qr·I);出滤波系数/协方差/新息
  · ``rts_smoother``         后向 RTS 平滑(全样本系数路径)
  · ``concentrated_loglik``  新息分解的浓缩对数似然 + 解析 R̂(跳过前 k 期扩散初值)
  · ``fit_tvp``              qr 网格 MLE → 最优平滑度下的滤波+平滑系数路径(含标准误)
  · ``time_varying_beta``    编排:对齐策略/市场期收益 → 时变β路径 + 静态β对照 + summary

口径锚定经典时序(状态空间/Kalman、时变参数回归)。纯 ``numpy``/``pandas``;**不碰引擎、无文件 IO**。
被 ``guanlan_v2.workflow.api._tvbeta`` 调用。平滑路径用全样本信息(双边、非因果);滤波路径
单边因果(无前视)—— 两者都返回,诚实区分。"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd


def _num(v: Any) -> Optional[float]:
    """NaN/Inf/不可转 → None,否则 float(JSON 安全,同 api._num 口径)。"""
    try:
        f = float(v)
        return f if math.isfinite(f) else None
    except Exception:  # noqa: BLE001
        return None


# ── 静态 OLS 基线 ─────────────────────────────────────────────────────────────
def _ols(y: np.ndarray, X: np.ndarray) -> Tuple[Optional[np.ndarray], Optional[float], Optional[float]]:
    """普通 OLS ``y = Xβ + e``。返回 ``(coef[k], resid_var, r2)``;秩亏/奇异/样本不足 → ``(None, None, None)``。"""
    try:
        y = np.asarray(y, dtype="float64").reshape(-1)
        X = np.asarray(X, dtype="float64")
        if X.ndim != 2 or X.shape[0] != y.shape[0]:
            return None, None, None
        n, k = X.shape
        if n <= k:
            return None, None, None
        if int(np.linalg.matrix_rank(X)) < k:          # 共线/常数列 → 设计阵秩亏
            return None, None, None
        XtX = X.T @ X
        XtXinv = np.linalg.inv(XtX)
        if not np.all(np.isfinite(XtXinv)):
            return None, None, None
        coef = XtXinv @ (X.T @ y)
        e = y - X @ coef
        ss_res = float(e @ e)
        ss_tot = float(np.sum((y - y.mean()) ** 2))
        r2 = (1.0 - ss_res / ss_tot) if ss_tot > 0 else None
        rvar = ss_res / max(1, n - k)
        return coef, rvar, r2
    except np.linalg.LinAlgError:
        return None, None, None
    except Exception:  # noqa: BLE001
        return None, None, None


# ── 前向 Kalman 滤波(R=1 单位,Q=qr·I)──────────────────────────────────────
def kalman_filter(y: np.ndarray, X: np.ndarray, qr: float,
                  x0: np.ndarray, P0: np.ndarray, R: float = 1.0) -> Dict[str, Any]:
    """局部水平 TVP 滤波:状态 ``x_t = x_{t-1} + w``(F=I,Q=qr·R·I),观测 ``y_t = X_t·x_t + v``
    (v~N(0,R))。返回滤波/预测系数与协方差、新息 ``innov`` 与其方差 ``innov_var``。纯 numpy,绝不抛。"""
    y = np.asarray(y, dtype="float64").reshape(-1)
    X = np.asarray(X, dtype="float64")
    n, k = X.shape
    Q = float(qr) * float(R) * np.eye(k)
    x_filt = np.zeros((n, k)); P_filt = np.zeros((n, k, k))
    x_pred = np.zeros((n, k)); P_pred = np.zeros((n, k, k))
    innov = np.zeros(n); innov_var = np.zeros(n)
    x_prev = np.asarray(x0, dtype="float64").reshape(-1).copy()
    P_prev = np.asarray(P0, dtype="float64").copy()
    for t in range(n):
        xp = x_prev                          # 预测(随机游走 F=I)
        Pp = P_prev + Q
        x_pred[t] = xp; P_pred[t] = Pp
        H = X[t]                             # (k,)
        nu = float(y[t] - H @ xp)            # 新息
        S = float(H @ Pp @ H + R)           # 新息方差(标量)
        innov[t] = nu; innov_var[t] = S
        if math.isfinite(S) and S > 0:
            K = (Pp @ H) / S                # 卡尔曼增益 (k,)
            xf = xp + K * nu
            Pf = Pp - np.outer(K, H @ Pp)   # (I-KH)Pp
            Pf = 0.5 * (Pf + Pf.T)          # 对称化(数值稳定)
        else:
            xf = xp; Pf = Pp
        x_filt[t] = xf; P_filt[t] = Pf
        x_prev = xf; P_prev = Pf
    return {"x_filt": x_filt, "P_filt": P_filt, "x_pred": x_pred, "P_pred": P_pred,
            "innov": innov, "innov_var": innov_var, "n": int(n), "k": int(k), "R": float(R), "qr": float(qr)}


# ── 后向 RTS 平滑 ─────────────────────────────────────────────────────────────
def rts_smoother(flt: Dict[str, Any]) -> Dict[str, Any]:
    """Rauch-Tung-Striebel 后向平滑:用全样本信息精修系数路径。``C_t=P_filt[t]·P_pred[t+1]⁻¹``,
    ``x_smooth[t]=x_filt[t]+C_t(x_smooth[t+1]-x_pred[t+1])``。预测协方差用 pinv 防扩散初值病态。"""
    x_filt = flt["x_filt"]; P_filt = flt["P_filt"]
    x_pred = flt["x_pred"]; P_pred = flt["P_pred"]
    n, k = int(flt["n"]), int(flt["k"])
    x_smooth = x_filt.copy(); P_smooth = P_filt.copy()
    for t in range(n - 2, -1, -1):
        try:
            inv_next = np.linalg.pinv(P_pred[t + 1])
        except Exception:  # noqa: BLE001
            inv_next = np.linalg.pinv(P_pred[t + 1] + np.eye(k) * 1e-12)
        C = P_filt[t] @ inv_next
        x_smooth[t] = x_filt[t] + C @ (x_smooth[t + 1] - x_pred[t + 1])
        P_smooth[t] = P_filt[t] + C @ (P_smooth[t + 1] - P_pred[t + 1]) @ C.T
        P_smooth[t] = 0.5 * (P_smooth[t] + P_smooth[t].T)
    return {"x_smooth": x_smooth, "P_smooth": P_smooth}


# ── 浓缩对数似然(R 解析消去)────────────────────────────────────────────────
def concentrated_loglik(innov: np.ndarray, innov_var: np.ndarray, k: int,
                        skip: Optional[int] = None) -> Tuple[float, Optional[float]]:
    """新息分解的浓缩对数似然:R̂=mean(ν²/S),``ll=-0.5[m·ln2π+m·lnR̂+Σln S+m]``。
    跳过前 ``skip``(默认 k)期扩散初值的贡献。可用样本 <2 或 R̂≤0 → ``(-inf, None)``。"""
    nu = np.asarray(innov, dtype="float64")
    S = np.asarray(innov_var, dtype="float64")
    n = nu.size
    if skip is None:
        skip = int(k)
    if skip < 0:
        skip = 0
    sel = slice(skip, n)
    Ss = S[sel]; nus = nu[sel]
    good = np.isfinite(Ss) & (Ss > 0) & np.isfinite(nus)
    Ss = Ss[good]; nus = nus[good]
    m = int(Ss.size)
    if m < 2:
        return float("-inf"), None
    rhat = float(np.mean(nus ** 2 / Ss))
    if not (rhat > 0 and math.isfinite(rhat)):
        return float("-inf"), None
    ll = -0.5 * (m * math.log(2.0 * math.pi) + m * math.log(rhat) + float(np.sum(np.log(Ss))) + m)
    return float(ll), rhat


# ── qr 网格 MLE → 时变系数路径 ───────────────────────────────────────────────
def fit_tvp(y: np.ndarray, X: np.ndarray, qr_grid: Optional[np.ndarray] = None,
            min_obs: int = 24, p0_diffuse: float = 1e4) -> Dict[str, Any]:
    """时变参数回归拟合:在信噪比 ``qr`` 对数网格上跑 Kalman 滤波取浓缩似然,选 MLE 最优 qr,
    再在该 qr 下滤波+RTS 平滑出系数路径(协方差按 R̂ 还原真实量纲,出 ±2se 标准误)。
    初值 x0=静态 OLS 系数(扩散 P0 使数据快速主导);样本 < ``min_obs`` 或 OLS 奇异 → ``ok=False``。"""
    out: Dict[str, Any] = {"ok": False, "n": 0, "k": 0, "qr": None, "rhat": None,
                           "r2": None, "static_coef": None,
                           "coef_filt": None, "coef_smooth": None, "se_smooth": None, "ll": None}
    try:
        y = np.asarray(y, dtype="float64").reshape(-1)
        X = np.asarray(X, dtype="float64")
        if X.ndim != 2 or X.shape[0] != y.shape[0]:
            out["reason"] = "输入维度不匹配"
            return out
        finite = np.isfinite(y) & np.all(np.isfinite(X), axis=1)
        y = y[finite]; X = X[finite]
        n, k = X.shape
        out["n"] = int(n); out["k"] = int(k)
        if n < int(min_obs):
            out["reason"] = f"样本不足(n={n} < {min_obs})"
            return out
        coef0, _, r2 = _ols(y, X)
        if coef0 is None:
            out["reason"] = "静态 OLS 奇异(市场收益常数/共线),无法估β"
            return out
        out["static_coef"] = [_num(c) for c in coef0]
        out["r2"] = _num(r2)

        x0 = np.asarray(coef0, dtype="float64")
        P0 = float(p0_diffuse) * np.eye(k)
        if qr_grid is None:
            qr_grid = np.logspace(-8.0, 1.0, 60)
        best_ll = float("-inf"); best_qr = None; best_rhat = None
        for qr in qr_grid:
            flt = kalman_filter(y, X, float(qr), x0, P0)
            ll, rhat = concentrated_loglik(flt["innov"], flt["innov_var"], k=k, skip=k)
            if ll > best_ll and rhat is not None:
                best_ll = ll; best_qr = float(qr); best_rhat = float(rhat)
        if best_qr is None:
            out["reason"] = "似然全程非有限,Kalman 拟合失败"
            return out

        flt = kalman_filter(y, X, best_qr, x0, P0)
        sm = rts_smoother(flt)
        x_smooth = sm["x_smooth"]; P_smooth = sm["P_smooth"]
        # 协方差按 R̂ 还原真实量纲(滤波以 R=1 跑);标准误 = sqrt(R̂·P_smooth[t,j,j])
        se_smooth = np.zeros((n, k))
        for t in range(n):
            d = np.diag(P_smooth[t]) * best_rhat
            se_smooth[t] = np.sqrt(np.clip(d, 0.0, None))
        out.update({
            "ok": True, "qr": best_qr, "rhat": _num(best_rhat), "ll": _num(best_ll),
            "coef_filt": flt["x_filt"], "coef_smooth": x_smooth, "se_smooth": se_smooth,
        })
        return out
    except Exception as exc:  # noqa: BLE001  绝不抛
        out["reason"] = f"{type(exc).__name__}: {exc}"
        return out


# ── 编排:策略期收益 vs 市场期收益 → 时变β ──────────────────────────────────
def time_varying_beta(strategy_ret: pd.Series, market_ret: pd.Series,
                      min_periods: int = 24, qr_grid: Optional[np.ndarray] = None) -> Dict[str, Any]:
    """把策略期收益与市场期收益按调仓日内连接→去 NaN,拟合时变 ``r_t=α_t+β_t·m_t``,
    返回时变β路径(滤波因果 + 平滑全样本 + ±2se 置信带)、静态 OLS β 对照、summary。
    样本 < ``min_periods`` 或市场退化(OLS 奇异)→ 诚实 ``ok_model=False`` 带 reason。"""
    try:
        df = pd.concat([
            pd.to_numeric(strategy_ret, errors="coerce").rename("y"),
            pd.to_numeric(market_ret, errors="coerce").rename("m"),
        ], axis=1, join="inner").dropna()
    except Exception as exc:  # noqa: BLE001
        return {"ok_model": False, "n": 0, "reason": f"序列对齐失败:{type(exc).__name__}: {exc}"}

    n = int(len(df))
    if n < int(min_periods):
        return {"ok_model": False, "n": n,
                "reason": f"时变β样本不足(对齐后 n={n} < {min_periods});请加宽窗口或用更密调仓频率。"}

    idx = df.index
    y = df["y"].to_numpy(dtype="float64")
    m = df["m"].to_numpy(dtype="float64")
    X = np.column_stack([np.ones(n), m])

    static_coef, _, static_r2 = _ols(y, X)
    if static_coef is None:
        return {"ok_model": False, "n": n,
                "reason": "市场期收益近似常数/共线,静态回归奇异,无法估时变β。"}

    fit = fit_tvp(y, X, qr_grid=qr_grid, min_obs=min_periods)
    if not fit.get("ok"):
        return {"ok_model": False, "n": n, "reason": fit.get("reason") or "Kalman 时变β 拟合失败"}

    cf = fit["coef_filt"]; cs = fit["coef_smooth"]; se = fit["se_smooth"]
    beta_filt = cf[:, 1]; beta_smooth = cs[:, 1]
    alpha_smooth = cs[:, 0]; se_beta = se[:, 1]

    def _date(d) -> str:
        return str(pd.Timestamp(d).date())

    beta_path: List[List[Any]] = []
    alpha_path: List[List[Any]] = []
    for t in range(n):
        bs = float(beta_smooth[t]); s = float(se_beta[t])
        lo = bs - 2.0 * s; hi = bs + 2.0 * s
        beta_path.append([_date(idx[t]), _num(beta_filt[t]), _num(bs), _num(lo), _num(hi)])
        alpha_path.append([_date(idx[t]), _num(alpha_smooth[t])])

    bsm = beta_smooth.astype("float64")
    return {
        "ok_model": True,
        "n": n,
        "static_beta": _num(static_coef[1]),
        "static_alpha": _num(static_coef[0]),
        "r2": _num(static_r2),
        "qr": _num(fit["qr"]),
        "rhat": _num(fit["rhat"]),
        "beta_mean": _num(float(np.mean(bsm))),
        "beta_start": _num(float(bsm[0])),
        "beta_end": _num(float(bsm[-1])),
        "beta_min": _num(float(np.min(bsm))),
        "beta_max": _num(float(np.max(bsm))),
        "beta_drift": _num(float(bsm[-1] - bsm[0])),
        "alpha_mean": _num(float(np.mean(alpha_smooth))),
        "beta_path": beta_path,
        "alpha_path": alpha_path,
    }
