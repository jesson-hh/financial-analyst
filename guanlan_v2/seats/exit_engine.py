# -*- coding: utf-8 -*-
"""确定性出场引擎 —— 三重障碍 + 吊灯跟踪的纯数值计算(LLM 只当评审,不当计算器)。

背景:2026-08-02 研究台实证(docs/research/2026-08-02-cards-factors-backtest.md)——
知识层(经验卡)与接口层(结构化出场评估)都治不了 LLM 的持有偏好(卖出率 4.9%→4.8%),
剩余路径之一就是**出场条件由代码确定性计算,LLM 复核而非裁量**。本模块实现该计算层:

- 初始止损:结构位(入场前 N 日最低)∨ k×ATR ∨ 固定百分比,三线并报,取「离价最近」为生效线;
- 吊灯线(Chandelier):持有期内最高价 − 3×ATR(22),只上移不下移;
- 目标位:+2R 减仓带(R = 入场价 − 生效初始止损);
- 盈利回撤:浮盈峰值回吐比例(giveback);
- 时间障碍:持有 N bar 未达预期。

输出为纯 dict(触发布尔 + 数值 + 建议 position_action + 理由句),``render_lines()``
把它渲染成可直接注入研判 prompt 的证据行。**本模块不接线、不下单、不改台账**——
接入 seats 研判属独立决策(见 docs/research prompt 包 C4/后续提案)。
"""
from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd

__all__ = ["compute_exit_plan", "render_lines", "DEFAULTS"]

DEFAULTS = {
    "atr_n": 14,            # ATR 窗口(Wilder)
    "atr_k_stop": 2.0,      # 初始止损 ATR 倍数
    "pct_stop": 0.07,       # 固定百分比止损(欧奈尔 7%)
    "structure_n": 20,      # 结构位回看(入场前 N 日最低)
    "chand_n": 22,          # 吊灯窗口
    "chand_k": 3.0,         # 吊灯 ATR 倍数
    "tp_r": 2.0,            # 减仓带:浮盈 ≥ 2R
    "giveback_arm": 0.10,   # 盈利回撤武装线:峰值浮盈 ≥ 10% 才启用
    "giveback_frac": 1 / 3, # 回吐峰值浮盈的 1/3 触发
    "time_bars": 15,        # 时间障碍:持有 N bar
    "time_r_min": 0.5,      # N bar 后浮盈 < 0.5R 视为未达预期
}


def _atr(df: pd.DataFrame, n: int) -> pd.Series:
    """Wilder ATR(EMA α=1/n)。"""
    h, lo, c = df["high"], df["low"], df["close"]
    pc = c.shift(1)
    tr = pd.concat([h - lo, (h - pc).abs(), (lo - pc).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1.0 / n, adjust=False, min_periods=n).mean()


def compute_exit_plan(df: pd.DataFrame, entry_price: float,
                      entry_date: Optional[str] = None,
                      params: Optional[dict] = None) -> dict:
    """对一笔持仓计算完整出场计划。

    df:日线 bar(须含 date/open/high/low/close,升序;date 可为列或 index)。
    entry_price:持仓成本。entry_date:入场日(缺省=按成本价在近 60 bar 内回溯
    首个「低≤成本≤高」的 bar;再缺省=取 60 bar 前,并在 assumptions 里如实标注)。
    """
    p = {**DEFAULTS, **(params or {})}
    df = df.copy()
    if "date" in df.columns:
        df["date"] = pd.to_datetime(df["date"])
        df = df.sort_values("date").set_index("date")
    else:
        df.index = pd.to_datetime(df.index)
        df = df.sort_index()
    need = {"open", "high", "low", "close"}
    if not need.issubset(df.columns):
        raise ValueError(f"日线 bar 缺列: {sorted(need - set(df.columns))}")
    df = df[df["close"].notna()]
    if len(df) < max(p["atr_n"], p["chand_n"]) + 5:
        raise ValueError(f"bar 数不足: {len(df)}")

    entry_price = float(entry_price)
    if not np.isfinite(entry_price) or entry_price <= 0:
        raise ValueError(f"entry_price 非法: {entry_price}")

    assumptions: list[str] = []
    # ── 入场位定位 ──
    if entry_date is not None:
        ed = pd.to_datetime(entry_date)
        pos_arr = np.nonzero(df.index >= ed)[0]
        if len(pos_arr) == 0:
            raise ValueError(f"entry_date {entry_date} 晚于数据末端")
        ei = int(pos_arr[0])
    else:
        tail = df.iloc[-60:]
        hit = np.nonzero((tail["low"].to_numpy() <= entry_price)
                         & (entry_price <= tail["high"].to_numpy()))[0]
        if len(hit):
            ei = len(df) - 60 + int(hit[0]) if len(df) >= 60 else int(hit[0])
            assumptions.append("入场日按成本价在近60bar内回溯推定")
        else:
            ei = max(len(df) - 60, 0)
            assumptions.append("入场日不可考,按60bar前近似(时间障碍仅供参考)")

    atr = _atr(df, p["atr_n"])
    atr_entry = float(atr.iloc[ei]) if np.isfinite(atr.iloc[ei]) else float(atr.iloc[-1])
    last = df.iloc[-1]
    close = float(last["close"])
    held_bars = len(df) - 1 - ei

    # ── 初始止损三线(生效 = 离价最近但仍低于入场价的那条;全在入场价上方则取最高线) ──
    struct_lo = float(df["low"].iloc[max(0, ei - p["structure_n"]):ei + 1].min()) if ei > 0 else float("nan")
    stops = {
        "structure": struct_lo if np.isfinite(struct_lo) else None,
        "atr": entry_price - p["atr_k_stop"] * atr_entry,
        "pct": entry_price * (1 - p["pct_stop"]),
    }
    cand = [v for v in stops.values() if v is not None and np.isfinite(v)]
    below = [v for v in cand if v < entry_price]
    initial_stop = max(below) if below else max(cand)
    r_unit = entry_price - initial_stop
    if r_unit <= 0:
        r_unit = p["atr_k_stop"] * atr_entry   # 结构位倒挂时退回 ATR 口径
        assumptions.append("初始止损高于入场价(结构倒挂),R 退回 ATR 口径")

    # ── 持有期滚动量 ──
    hold = df.iloc[ei:]
    peak_close = float(hold["close"].max())
    chand_hh = float(hold["high"].iloc[-p["chand_n"]:].max()) if len(hold) >= 1 else close
    atr_now = float(atr.iloc[-1])
    chandelier = chand_hh - p["chand_k"] * atr_now

    unreal = close / entry_price - 1.0
    peak_ret = peak_close / entry_price - 1.0
    r_mult = (close - entry_price) / r_unit
    giveback = ((peak_close - close) / (peak_close - entry_price)
                if peak_close > entry_price * 1.001 else 0.0)

    # ── 触发判定 ──
    trig = {
        "stop_hit": close < initial_stop,
        "chandelier_hit": (close < chandelier) and (chandelier > initial_stop),
        "takeprofit_zone": r_mult >= p["tp_r"],
        "giveback_hit": (peak_ret >= p["giveback_arm"]) and (giveback >= p["giveback_frac"]),
        "time_hit": (held_bars >= p["time_bars"]) and (r_mult < p["time_r_min"]),
    }

    reasons: list[str] = []
    if trig["stop_hit"]:
        action = "清仓"
        reasons.append(f"收盘 {close:.2f} 已破初始止损 {initial_stop:.2f}(-1R 证伪线),错了就是全错")
    elif trig["chandelier_hit"]:
        action = "清仓"
        reasons.append(f"收盘 {close:.2f} 已破吊灯线 {chandelier:.2f}(持有期高点 {chand_hh:.2f} − {p['chand_k']}×ATR),趋势由市场宣告结束")
    elif trig["giveback_hit"]:
        action = "减仓"
        reasons.append(f"峰值浮盈 {peak_ret:+.1%} 已回吐 {giveback:.0%}(≥1/3 触发),先落袋一半、止损提保本")
    elif trig["takeprofit_zone"]:
        action = "减仓"
        reasons.append(f"浮盈 {r_mult:+.1f}R(≥{p['tp_r']:.0f}R 减仓带),顺强势了结一半、余仓吊灯跟踪")
    elif trig["time_hit"]:
        action = "清仓"
        reasons.append(f"持有 {held_bars} bar 浮盈仅 {r_mult:+.1f}R(<{p['time_r_min']}R),时间障碍触发:不涨本身就是亏损")
    else:
        action = "继续持有"
        dist_stop = (close - max(initial_stop, chandelier)) / close
        reasons.append(f"未触发任一障碍:距最近防线 {dist_stop:+.1%},浮盈 {r_mult:+.1f}R,持有 {held_bars} bar")

    return {
        "action": action, "reasons": reasons, "triggers": trig,
        "entry_price": entry_price, "close": close, "held_bars": held_bars,
        "unrealized_ret": unreal, "peak_ret": peak_ret, "r_multiple": r_mult,
        "r_unit": r_unit, "giveback": giveback,
        "initial_stop": initial_stop, "stops": stops,
        "chandelier": chandelier, "chand_high": chand_hh, "atr": atr_now,
        "suggested_stop": max(initial_stop,
                              entry_price if peak_ret >= p["giveback_arm"] else initial_stop),
        "params": p, "assumptions": assumptions,
        "asof": str(df.index[-1].date()),
    }


def render_lines(plan: dict) -> str:
    """渲染成研判 prompt 可直接注入的证据行(确定性计算,LLM 只复核)。"""
    t = plan["triggers"]
    flags = " ".join(f"{'✓' if v else '·'}{k}" for k, v in t.items())
    lines = [
        f"【出场引擎·确定性计算 @{plan['asof']}】建议:{plan['action']}",
        f"成本 {plan['entry_price']:.2f} → 现价 {plan['close']:.2f}"
        f"(浮盈 {plan['unrealized_ret']:+.1%} = {plan['r_multiple']:+.1f}R,持有 {plan['held_bars']} bar)",
        f"初始止损 {plan['initial_stop']:.2f} · 吊灯线 {plan['chandelier']:.2f}"
        f"(高点 {plan['chand_high']:.2f} − 3×ATR {plan['atr']:.2f}) · 建议防线 {plan['suggested_stop']:.2f}",
        f"触发:{flags}",
    ] + [f"依据:{r}" for r in plan["reasons"]]
    if plan["assumptions"]:
        lines.append("假设:" + ";".join(plan["assumptions"]))
    return "\n".join(lines)
