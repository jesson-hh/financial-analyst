# -*- coding: utf-8 -*-
"""CPCV + Deflated Sharpe 验证引擎(纯测量,不碰交易信号)。

快速档:读 model_health 冻结快照 → 组合收益分布 + DSR(秒级,零看未来)。
严格档:全历史按组合净化交叉验证(CPCV)重训 → 路径分布 + DSR(~1h)。
不改 v4.py:严格档复用 v4 面板 primitive + workflow 的 _materialize_xy/_build_model 做掩码 fit/predict。"""
from __future__ import annotations

import itertools
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

ANNUALIZE = (252.0 / 5.0) ** 0.5   # 5 日持有期年化


def make_splits(dates, n_groups: int = 6, k: int = 2, purge: int = 5, embargo: int = 5):
    """有序唯一交易日切 n_groups 连续段,枚举 C(n_groups,k) 组合当测试段;每个测试段做
    purge(挖其前 purge 个交易日:训练样本 horizon 标签窗会探入测试段)+ embargo(剔其后 embargo 个)。
    返回 [(train_dates:list, test_dates:list), ...]。"""
    uniq = list(pd.DatetimeIndex(sorted(pd.Index(pd.to_datetime(pd.Series(dates))).unique())))
    n = len(uniq)
    if n < n_groups * 2:
        return []
    bounds = [round(i * n / n_groups) for i in range(n_groups + 1)]
    groups = [uniq[bounds[i]:bounds[i + 1]] for i in range(n_groups)]
    pos = {d: i for i, d in enumerate(uniq)}
    out = []
    for combo in itertools.combinations(range(n_groups), k):
        test = [d for gi in combo for d in groups[gi]]
        drop = set(pos[d] for d in test)
        for tp in list(drop):
            for j in range(1, purge + 1):
                drop.add(tp - j)
            for j in range(1, embargo + 1):
                drop.add(tp + j)
        train = [uniq[i] for i in range(n) if i not in drop]
        out.append((train, sorted(test)))
    return out


def decile_metrics(panel: pd.DataFrame, decile: float = 0.1) -> Dict[str, Any]:
    """panel: 长表 [date, code, lgb_pct, fwd](fwd=该 date 起未来5日收益,已 PIT)。
    每换仓日:top/bottom decile 等权 → 多头超额(top−全域等权)、多空价差(top−bottom)、截面 rank-IC。
    截面<20 跳过(诚实)。"""
    le, ls, ics, used = [], [], [], []
    for d, g in panel.dropna(subset=["lgb_pct", "fwd"]).groupby("date"):
        if len(g) < 20:
            continue
        q_hi = g["lgb_pct"].quantile(1 - decile); q_lo = g["lgb_pct"].quantile(decile)
        top = g[g["lgb_pct"] >= q_hi]["fwd"]; bot = g[g["lgb_pct"] <= q_lo]["fwd"]
        if not len(top):
            continue
        le.append(float(top.mean() - g["fwd"].mean()))
        if len(bot):
            ls.append(float(top.mean() - bot.mean()))
        ic = g["lgb_pct"].rank().corr(g["fwd"].rank())
        if pd.notna(ic):
            ics.append(float(ic))
        used.append(pd.Timestamp(d))
    return {"long_excess_ret": le, "long_short_ret": ls, "rank_ic": ics,
            "rank_ic_mean": float(np.mean(ics)) if ics else None,
            "dates": [str(x.date()) for x in used], "n": len(le)}


def sharpe(returns: List[float], annualize: float = ANNUALIZE) -> Optional[float]:
    r = np.asarray([x for x in returns if x == x], dtype="float64")
    if len(r) < 3 or r.std(ddof=1) == 0:
        return None
    return float(r.mean() / r.std(ddof=1) * annualize)


def _norm_cdf(x: float) -> float:
    import math
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _norm_ppf(p: float) -> float:
    try:
        from scipy.stats import norm
        return float(norm.ppf(p))
    except Exception:  # noqa: BLE001 — Acklam 近似(避免硬依赖 scipy)
        import math
        a = [-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02,
             1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00]
        b = [-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02,
             6.680131188771972e+01, -1.328068155288572e+01]
        c = [-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00,
             -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00]
        d = [7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00, 3.754408661907416e+00]
        pl, ph = 0.02425, 1 - 0.02425
        if p < pl:
            q = math.sqrt(-2 * math.log(p))
            return (((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
        if p > ph:
            q = math.sqrt(-2 * math.log(1 - p))
            return -(((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
        q = p - 0.5; r = q*q
        return (((((a[0]*r+a[1])*r+a[2])*r+a[3])*r+a[4])*r+a[5])*q / (((((b[0]*r+b[1])*r+b[2])*r+b[3])*r+b[4])*r+1)


_EULER = 0.5772156649015329


def deflated_sharpe(returns: List[float], n_trials: int, sharpes_std: Optional[float] = None):
    """DSR = P(真夏普 > SR0),SR0 = N 次试验下期望最大夏普(噪声基准)。返回 [0,1];
    样本<10 或零波动 → None。夏普口径=每周期(未年化)。sharpes_std=各试验夏普标准差(缺→1)。"""
    import math
    r = np.asarray([x for x in returns if x == x], dtype="float64")
    T = len(r)
    if T < 10 or r.std(ddof=1) == 0:
        return None
    sr = r.mean() / r.std(ddof=1)
    g3 = float(pd.Series(r).skew())
    g4 = float(pd.Series(r).kurtosis()) + 3.0          # pandas 超额峰度 → 普通峰度
    N = max(2, int(n_trials)); v = sharpes_std if (sharpes_std and sharpes_std > 0) else 1.0
    sr0 = v * ((1 - _EULER) * _norm_ppf(1 - 1.0 / N) + _EULER * _norm_ppf(1 - 1.0 / (N * math.e)))
    denom = math.sqrt(max(1e-12, 1 - g3 * sr + (g4 - 1) / 4.0 * sr * sr))
    return float(_norm_cdf((sr - sr0) * math.sqrt(T - 1) / denom))
