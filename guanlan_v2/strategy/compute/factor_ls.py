# -*- coding: utf-8 -*-
"""因子族多空(L/S)收益序列:regime 层地基产物(PIT:available_date = t+1)。

- 白名单 = 6 个纯价量族(估值/财务/成长/规模依赖坏管线、情绪/资金面数据源未审计 → 均排除,spec §3);
- t 日按因子截面排序,top/bottom quintile 等权的 t→t+1 收益差 = 当日 L/S;族内成员等权平均;
- 下游 regime 特征在 t 只允许用 available_date ≤ t 的行(PIT 命门);
- 全量物化重(10-30min)→ 只走 __main__ 独立子进程 + 独立锁,不进 regen 锁临界区(评审前置条件);
  regen 内只跑日频增量(秒-分钟级)。
"""
from __future__ import annotations

import os
from typing import Dict, Optional, Tuple

import numpy as np
import pandas as pd

from guanlan_v2.strategy.paths import FACTOR_LS_PARQUET

WHITELIST_FAMILIES = ("动量反转", "技术", "波动率", "流动性", "共振", "跟随")
CSV_ID = "_csv"          # 市场截面收益离散度(连续机会空间代理,深研 3-0)伪因子行
CSV_FAMILY = "_market"   # 不参与族聚合
LS_Q = 0.2
LS_MIN_N = 30            # 截面最少票数,低于不算(诚实缺席)


def ls_series(fac_wide: pd.DataFrame, close_wide: pd.DataFrame, q: float = LS_Q,
              min_n: Optional[int] = None) -> pd.DataFrame:
    """单因子 L/S 日收益(值已预定向:高=看多)。行 index=t、available_date=t+1(PIT)。"""
    mn = LS_MIN_N if min_n is None else int(min_n)
    cw = close_wide.sort_index()
    fw = fac_wide.reindex(index=cw.index, columns=cw.columns)
    ret_next = cw.shift(-1) / cw - 1.0        # r_{t→t+1} 挂在 t 行
    dates = list(cw.index)
    rows = []
    for i, t in enumerate(dates[:-1]):        # 末日无次日收益 → 不出行
        f = fw.loc[t].dropna()
        r = ret_next.loc[t].reindex(f.index).dropna()
        f = f.reindex(r.index)
        if len(f) < mn:
            continue
        n_side = max(1, int(len(f) * q))
        order = f.sort_values()
        top = float(r.reindex(order.index[-n_side:]).mean())
        bot = float(r.reindex(order.index[:n_side]).mean())
        if not (np.isfinite(top) and np.isfinite(bot)):
            continue
        rows.append({"date": t, "ls_ret": top - bot, "available_date": dates[i + 1]})
    return pd.DataFrame(rows, columns=["date", "ls_ret", "available_date"])


def load_family_ls() -> pd.DataFrame:
    """族等权 L/S 长表(date, family, ls_ret, available_date);缺产物 → 空表(诚实缺席)。"""
    if not FACTOR_LS_PARQUET.exists():
        return pd.DataFrame(columns=["date", "family", "ls_ret", "available_date"])
    df = pd.read_parquet(FACTOR_LS_PARQUET)
    df = df[df["family"] != CSV_FAMILY]
    g = (df.groupby(["date", "family"], as_index=False)
           .agg(ls_ret=("ls_ret", "mean"), available_date=("available_date", "max")))
    return g.sort_values(["family", "date"]).reset_index(drop=True)


def load_csv_series() -> pd.Series:
    """市场截面收益离散度序列(index=date);缺产物 → 空序列。"""
    if not FACTOR_LS_PARQUET.exists():
        return pd.Series(dtype=float)
    df = pd.read_parquet(FACTOR_LS_PARQUET)
    df = df[df["factor_id"] == CSV_ID]
    return pd.Series(df["ls_ret"].values, index=pd.DatetimeIndex(df["date"])).sort_index()
