# -*- coding: utf-8 -*-
"""做T参考引擎 —— 持仓语境下的确定性 T 带位与纪律(advisory only,LLM 只复核不报点位)。

实证基础(docs/research/2026-08-07-t-trade-backtest.md,120票×3年×30万回合):
- 无条件做T扣成本净 -0.11%~-0.15%/回合(给券商打工);
- 利润全在震荡日(+0.2%~+0.9%/回合,胜率55-75%),单边日 -0.5%~-1.4%(屠宰场);
- 开盘30分钟|涨幅|四分位 → 单边日率 25%→50% 单调;冲高卖优于杀跌买;腿止损1%不可省。

本模块只做两件事:①从**已完结的基准日**日线算枢轴带位(P/R1/S1)与 VWAP 带宽 θ
(0.5×前20日振幅中位数)——全部 PIT 安全;②若调用方给了当日盘口(open/price),
按「方向占振幅比」给日型倾向的**粗读**(明确标注为倾向而非判定)。不接实时分钟数据、
不下单、不替人决定。
"""
from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd

__all__ = ["compute_t_ref", "render_t_lines"]

_DISCIPLINE = ("实证纪律(30万回合回测):无条件做T扣成本必亏;利润全在震荡日"
               "(+0.2%~+0.9%/回合),单边日-0.5%~-1.4%——判不清日型就不做T;"
               "开盘30分钟波动越大单边日率越高(25%→50%);冲高卖优于杀跌买(接刀更贵);"
               "任何一腿逆行≥1%立即反向完成,不许扛。")


def compute_t_ref(df: pd.DataFrame, quote: Optional[dict] = None) -> dict:
    """df=日线 bar(trade_date/open/high/low/close,升序,末行=已完结基准日)。
    quote(可选)={open, price}(当日开盘与现价)→ 日型倾向粗读。"""
    d = df.copy()
    if "date" in d.columns and "trade_date" not in d.columns:
        d = d.rename(columns={"date": "trade_date"})
    need = {"trade_date", "high", "low", "close"}
    if not need.issubset(d.columns):
        raise ValueError(f"日线 bar 缺列: {sorted(need - set(d.columns))}")
    d = d[d["close"].notna()].sort_values("trade_date")
    if len(d) < 21:
        raise ValueError(f"bar 数不足: {len(d)}")
    base = d.iloc[-1]
    H, L, C = float(base["high"]), float(base["low"]), float(base["close"])
    P = (H + L + C) / 3.0
    r1, s1 = 2 * P - L, 2 * P - H
    amp = (d["high"] / d["low"] - 1.0).tail(20)
    amp_med = float(amp.median())
    theta = float(np.clip(0.5 * amp_med, 0.004, 0.03))

    tendency = None
    if quote:
        o = quote.get("open")
        px = quote.get("price")
        try:
            o, px = float(o), float(px)
        except (TypeError, ValueError):
            o = px = None
        if o and px and o > 0 and amp_med > 0:
            drift = abs(px / o - 1.0)
            ratio = drift / amp_med
            if ratio >= 0.6:
                tendency = (f"今日至今|方向|={drift:.1%},已达典型振幅的 {ratio:.0%}"
                            "——单边倾向,今日不做T")
            elif ratio <= 0.3:
                tendency = (f"今日至今|方向|={drift:.1%}(典型振幅的 {ratio:.0%})"
                            "——暂偏震荡,T带可参考(仍须自判日型)")
            else:
                tendency = (f"今日至今|方向|={drift:.1%}(典型振幅的 {ratio:.0%})"
                            "——日型未明,倾向观望不做T")

    return {
        "base_date": str(pd.Timestamp(base["trade_date"]).date()),
        "pivot": P, "r1": r1, "s1": s1,
        "theta": theta, "amp20_med": amp_med,
        "tendency": tendency,
    }


def render_t_lines(ref: dict) -> str:
    """渲染为研判可注入的证据行。"""
    lines = [
        f"【做T参考·确定性带位(基准日 {ref['base_date']},适用其后首个交易日)】",
        f"枢轴 P={ref['pivot']:.2f} · 卖T带 R1={ref['r1']:.2f} · 买回带 S1={ref['s1']:.2f}"
        f" · VWAP带宽 θ=±{ref['theta']:.1%}(=0.5×近20日振幅中位数 {ref['amp20_med']:.1%})",
    ]
    if ref.get("tendency"):
        lines.append(f"日型倾向(粗读):{ref['tendency']}")
    lines.append(_DISCIPLINE)
    return "\n".join(lines)
