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
