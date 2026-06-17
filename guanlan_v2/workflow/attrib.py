# -*- coding: utf-8 -*-
"""风格归因(guanlan 自有)—— 把策略期收益分解到 Fama-French 风格因子收益。

补掉平台「收益归因层」缺口:现有只看 IC / 净值,看不出**收益来自哪种风格、有没有真 alpha**。
本模块把 TopN 策略的期收益 r_p 对四个风格因子收益做 OLS + Newey-West HAC 回归:

  · MKT 市场   = 全池等权期收益(A股无干净无风险利率 → alpha 是「风格无法解释的超额」非严格 CAPM α)
  · SMB 规模   = 小盘减大盘(按 total_mv 分位多空腿)
  · HML 价值   = 高账面市值比减低(BM=1/pb)
  · WML 动量   = 赢家减输家(mom_120 / 历史涨幅)

产出因子暴露 β(各风格载荷与 HAC 显著性)+ alpha + R²(风格解释力)+ 各因子**收益贡献**
(βⱼ×因子均值,与 alpha、残差均值加总 == 策略收益均值)。

  · ``_ols_hac``                  多元 OLS + Newey-West Bartlett HAC(``_predictive_reg`` 单变量的多元推广)
  · ``_leg_spread``               按打分分位形成等权多空腿差(每腿不足 min_leg → NaN 诚实)
  · ``build_style_factor_returns`` 逐调仓日构建 MKT/SMB/HML/WML 期收益(无前视:打分≤d、收益>d)
  · ``attribute_returns``          编排:对齐→联合回归→逐因子边际视图(复用注入的 ``_predictive_reg``)→贡献

口径锚定量化 wiki(投资组合/绩效归因·风格分析;Fama-French 多因子)。纯 ``numpy``/``pandas``;
**不碰引擎、不碰 stock_data、无文件 IO**。被 ``guanlan_v2.workflow.api._attrib`` 调用
(``_predictive_reg`` 以参数注入,保持本模块 engine-free 可独立测试)。
"""
from __future__ import annotations

import math
from typing import Any, Callable, Dict, List, Optional

import numpy as np
import pandas as pd

STYLE_FACTORS = ("MKT", "SMB", "HML", "WML")


def _num(v: Any) -> Optional[float]:
    """NaN/Inf/不可转 → None,否则 float(同 api._num 口径,JSON 安全)。"""
    try:
        f = float(v)
        return f if math.isfinite(f) else None
    except Exception:  # noqa: BLE001
        return None


# ── 多元 OLS + Newey-West HAC ─────────────────────────────────────────────────
def _ols_hac(Y: np.ndarray, X: np.ndarray, hac_lag: int) -> Dict[str, Any]:
    """多元 OLS ``Y = Xβ + e`` + Newey-West(Bartlett 核)HAC 协方差(把 ``_predictive_reg``
    的三明治从单变量推广到 k 元,逐系数取 V[i,i])。``X`` 含截距列(第 0 列全 1)。
    ``hac_lag`` 夹到 ``[0, min(n-k-1, n//2)]``。短样本(n≤k+1)/ 奇异 X'X → 全 None(永不抛)。
    返回 ``{alpha, betas[k-1], coef[k], t[k], alpha_t, r2, nw_lag, n}``。"""
    out: Dict[str, Any] = {"alpha": None, "betas": None, "coef": None, "t": None,
                           "alpha_t": None, "r2": None, "nw_lag": None, "n": 0}
    try:
        Y = np.asarray(Y, dtype="float64").reshape(-1)
        X = np.asarray(X, dtype="float64")
        if X.ndim != 2 or X.shape[0] != Y.shape[0]:
            return out
        finite = np.isfinite(Y) & np.all(np.isfinite(X), axis=1)
        Y = Y[finite]
        X = X[finite]
        n, k = X.shape
        out["n"] = int(n)
        if n <= k + 1:
            return out
        XtX = X.T @ X
        try:
            XtXinv = np.linalg.inv(XtX)
        except np.linalg.LinAlgError:
            return out
        if not np.all(np.isfinite(XtXinv)):
            return out
        coef = XtXinv @ (X.T @ Y)
        e = Y - X @ coef
        ss_res = float(e @ e)
        ss_tot = float(np.sum((Y - Y.mean()) ** 2))
        r2 = (1.0 - ss_res / ss_tot) if ss_tot > 0 else None
        # HAC 长程方差 S = g0 + Σ_{l=1..L}(1-l/(L+1))(Gl+Gl');得分 U_t = x_t·e_t
        L = int(max(0, min(int(hac_lag), n - k - 1, n // 2)))
        U = X * e[:, None]
        S = U.T @ U
        for lag in range(1, L + 1):
            w = 1.0 - lag / (L + 1.0)
            G = U[lag:].T @ U[: n - lag]
            S = S + w * (G + G.T)
        V = XtXinv @ S @ XtXinv
        diag = np.diag(V)
        t = []
        for i in range(k):
            vi = float(diag[i])
            t.append((float(coef[i]) / math.sqrt(vi)) if (vi > 0 and math.isfinite(vi)) else None)
        out.update({
            "alpha": _num(coef[0]),
            "betas": [_num(c) for c in coef[1:]],
            "coef": [_num(c) for c in coef],
            "t": [_num(x) for x in t],
            "alpha_t": _num(t[0]),
            "r2": _num(r2),
            "nw_lag": int(L),
        })
        return out
    except Exception:  # noqa: BLE001  绝不抛出
        return out


# ── 风格因子腿差 ──────────────────────────────────────────────────────────────
def _leg_spread(returns: pd.Series, score: Optional[pd.Series], long_high: bool,
                q: float = 0.3, min_leg: int = 10) -> float:
    """按 ``score`` 的 q/1-q 分位把截面分多空腿(等权),返回腿差收益。
    ``long_high=True`` 做多高分腿(高减低);``False`` 做多低分腿(低减高,如 SMB 小盘)。
    任一腿 < ``min_leg`` 名 → NaN(诚实,不硬凑)。"""
    if score is None:
        return float("nan")
    try:
        sc = pd.to_numeric(score, errors="coerce").dropna()
        r = pd.to_numeric(returns, errors="coerce")
        common = r.index.intersection(sc.index)
        r = r.reindex(common)
        sc = sc.reindex(common)
        m = r.notna() & sc.notna()
        r = r[m]
        sc = sc[m]
        if len(sc) < 2 * int(min_leg):
            return float("nan")
        lo = float(sc.quantile(q))
        hi = float(sc.quantile(1.0 - q))
        low_codes = sc.index[sc <= lo]
        high_codes = sc.index[sc >= hi]
        if len(low_codes) < min_leg or len(high_codes) < min_leg:
            return float("nan")
        low_ret = float(r.reindex(low_codes).mean())
        high_ret = float(r.reindex(high_codes).mean())
        return (high_ret - low_ret) if long_high else (low_ret - high_ret)
    except Exception:  # noqa: BLE001
        return float("nan")


def _col_at(panel_df: pd.DataFrame, col: str, d) -> Optional[pd.Series]:
    """取面板在日期 d 的某列截面(code→值);列缺失/取不到 → None。值用 ≤d 已知信息(无前视)。"""
    if col not in panel_df.columns:
        return None
    try:
        return panel_df[col].xs(d, level="datetime")
    except Exception:  # noqa: BLE001
        return None


def build_style_factor_returns(panel_df: pd.DataFrame, reb_dates: List, fwd_r: pd.Series,
                               min_leg: int = 10, q: float = 0.3,
                               mom_col: str = "mom_120") -> pd.DataFrame:
    """逐调仓日构建四风格因子的期收益,返回 DataFrame(index=有效调仓日, 列=MKT/SMB/HML/WML)。
    无前视:打分(total_mv/pb/mom)取**日期 d 当日**(≤d 已知),腿收益用 ``fwd_r`` 在 d 的**前向收益**
    (d 之后实现)。腿不足 ``min_leg`` 名 → 该日该因子 NaN。"""
    rows: List[Dict[str, float]] = []
    idx: List[Any] = []
    for d in reb_dates:
        try:
            fr = fwd_r.xs(d, level="datetime")
        except Exception:  # noqa: BLE001
            continue
        fr = pd.to_numeric(fr, errors="coerce").dropna()
        if len(fr) < 2 * int(min_leg):
            continue
        rec: Dict[str, float] = {"MKT": float(fr.mean())}
        # 规模 SMB:小市值做多(long_high=False)
        rec["SMB"] = _leg_spread(fr, _col_at(panel_df, "total_mv", d), long_high=False, q=q, min_leg=min_leg)
        # 价值 HML:高 BM(=1/pb)做多
        pb = _col_at(panel_df, "pb", d)
        bm = None
        if pb is not None:
            pbn = pd.to_numeric(pb, errors="coerce")
            bm = 1.0 / pbn.where(pbn > 0)
        rec["HML"] = _leg_spread(fr, bm, long_high=True, q=q, min_leg=min_leg)
        # 动量 WML:高动量(赢家)做多
        rec["WML"] = _leg_spread(fr, _col_at(panel_df, mom_col, d), long_high=True, q=q, min_leg=min_leg)
        rows.append(rec)
        idx.append(d)
    if not rows:
        return pd.DataFrame(columns=list(STYLE_FACTORS))
    return pd.DataFrame(rows, index=pd.DatetimeIndex(idx))[list(STYLE_FACTORS)]


# ── 归因编排 ──────────────────────────────────────────────────────────────────
def attribute_returns(strategy_ret: pd.Series, factor_df: pd.DataFrame,
                      predictive_reg: Optional[Callable] = None,
                      hac_lag: int = 1, ppy: int = 12, min_periods: int = 12) -> Dict[str, Any]:
    """编排:把策略期收益 ``strategy_ret`` 与因子收益 ``factor_df`` 按调仓日内连接→去 NaN→
    联合 OLS+HAC(:func:`_ols_hac`)得 β/alpha/R²;再对每个因子用注入的 ``predictive_reg``
    (= api._predictive_reg)做单变量边际视图;算各因子贡献 βⱼ×mean(因子ⱼ)。期收益**非重叠**
    (= 调仓频率),故 HAC lag 取小(默认 1)。样本 < ``min_periods`` 或回归奇异 → 诚实空。"""
    factor_df = factor_df.reindex(columns=[c for c in STYLE_FACTORS if c in factor_df.columns])
    factor_df = factor_df.dropna(axis=1, how="all")     # 整列 NaN(如动量列缺)→ 丢该因子,不拖垮联合回归
    dropped = [c for c in STYLE_FACTORS if c not in factor_df.columns]
    joined = factor_df.join(pd.Series(strategy_ret, name="_y"), how="inner").dropna()
    cols = [c for c in STYLE_FACTORS if c in joined.columns]
    n = int(len(joined))
    if n < int(min_periods) or len(cols) < 1:
        return {"ok_model": False, "n": n, "dropped_factors": dropped,
                "reason": f"归因样本不足(对齐后 n={n} < {min_periods},或无有效风格因子);请加宽窗口或用更密调仓频率。"}

    Y = joined["_y"].to_numpy(dtype="float64")
    Fmat = joined[cols].to_numpy(dtype="float64")
    X = np.column_stack([np.ones(n), Fmat])
    joint = _ols_hac(Y, X, hac_lag=hac_lag)
    if joint.get("betas") is None:
        return {"ok_model": False, "n": n,
                "reason": "联合回归奇异(因子共线/样本不足),无法归因。"}

    betas = joint["betas"]
    tvals = joint["t"]  # [alpha_t, b1_t, ...]
    pred = predictive_reg if callable(predictive_reg) else (lambda f, r, h: {})
    exposures: List[Dict[str, Any]] = []
    contrib_sum = 0.0
    for j, name in enumerate(cols):
        beta = betas[j]
        fmean = float(np.nanmean(Fmat[:, j]))
        contribution = (beta * fmean) if beta is not None else None
        if contribution is not None:
            contrib_sum += contribution
        marg = pred(joined[name], joined["_y"], hac_lag + 1) or {}
        exposures.append({
            "name": name,
            "beta": _num(beta),
            "t": _num(tvals[j + 1] if tvals and j + 1 < len(tvals) else None),
            "sig": (bool(abs(tvals[j + 1]) >= 2.0) if (tvals and tvals[j + 1] is not None) else None),
            "factor_mean": _num(fmean),
            "contribution": _num(contribution),
            "marg_beta": _num(marg.get("beta")),
            "marg_t": _num(marg.get("nw_t")),
            "marg_sig": marg.get("nw_sig"),
        })

    alpha = joint["alpha"]
    y_mean = float(np.nanmean(Y))
    residual_mean = (y_mean - (alpha or 0.0) - contrib_sum)
    ann = float(ppy)
    return {
        "ok_model": True,
        "n": n,
        "dropped_factors": dropped,
        "alpha": _num(alpha),
        "alpha_annual": _num((alpha * ann) if alpha is not None else None),
        "alpha_t": _num(joint["alpha_t"]),
        "alpha_sig": (bool(abs(joint["alpha_t"]) >= 2.0) if joint["alpha_t"] is not None else None),
        "r2": _num(joint["r2"]),
        "nw_lag": joint["nw_lag"],
        "exposures": exposures,
        "strategy_mean": _num(y_mean),
        "strategy_mean_annual": _num(y_mean * ann),
        "residual_mean": _num(residual_mean),
        "factor_returns": [
            [str(pd.Timestamp(d).date())] + [_num(joined[c].iloc[i]) for c in cols] + [_num(Y[i])]
            for i, d in enumerate(joined.index)
        ],
        "factor_cols": cols,
    }
