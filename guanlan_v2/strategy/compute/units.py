# -*- coding: utf-8 -*-
"""单股 frame 的 vol/amount **量纲校准**(离线 compute 路径用,2026-06-12)。

三支柱(breadth/mainline/v4)逐字段 ``loader._read_bin`` 直读二进制,不走
``fetch_quote``,故引擎读取层的 ``_normalize_vol_units`` 对它们无效——污染
(2026-03-16 全市场 amount=千元+vol=手;03-17~06-11 全市场 vol=手)会原样
进产物(实证:v4 vol_trend_5_60 科创板被系统性压低 ~6.5×)。本模块提供与
引擎**同一检测带**的单股校准,挂在各支柱的逐股 frame 构建处。

口径(与 engine qlib_binary._normalize_vol_units 一致):
  r = (amount/close)/vol —— ≈1 正常;r∈[50,200] vol=手 → vol×100;
  r∈[0.05,0.2] vol=手且 amount=千元 → vol×100 且 amount×1000。
正常 bar 的 r≈VWAP/close∈[0.9,1.1],与检测带无重叠零误伤。
"""
from __future__ import annotations

from typing import Optional

import pandas as pd


def normalize_frame_units(df: pd.DataFrame, vol_col: str = "volume",
                          ref_vol: Optional[pd.Series] = None) -> pd.DataFrame:
    """校准单股 OHLCV frame 的 vol/amount 量纲(原地修改并返回同一 df)。

    - ``df`` 须含 ``close``/``amount`` 列;vol 取 ``df[vol_col]``。
    - df 无 vol 列时(breadth/mainline 只读 close+amount)传 ``ref_vol``
      (同一股票的 volume 序列,仅作参照定标、不写回 df)。
    - NaN/零量/缺列 → 原样;任何异常原样返回(校准绝不挡数据)。
    """
    try:
        if df is None or len(df) == 0:
            return df
        if "close" not in df.columns or "amount" not in df.columns:
            return df
        if vol_col in df.columns:
            v = df[vol_col]
            write_vol = True
        elif ref_vol is not None:
            v = pd.to_numeric(ref_vol, errors="coerce").reindex(df.index)
            write_vol = False
        else:
            return df
        c, a = df["close"], df["amount"]
        ok = c.notna() & v.notna() & a.notna() & (c > 0) & (v > 0) & (a > 0)
        if not bool(ok.any()):
            return df
        r = pd.Series(float("nan"), index=df.index, dtype="float64")
        r[ok] = (a[ok] / c[ok]) / v[ok]
        hand = ok & r.between(50.0, 200.0)            # vol=手
        dual = ok & r.between(0.05, 0.2)              # vol=手 且 amount=千元
        if write_vol and bool(hand.any()):
            df.loc[hand, vol_col] = v[hand] * 100.0
        if bool(dual.any()):
            if write_vol:
                df.loc[dual, vol_col] = v[dual] * 100.0
            df.loc[dual, "amount"] = a[dual] * 1000.0
        return df
    except Exception:  # noqa: BLE001 — 校准自身故障绝不挡数据
        return df
