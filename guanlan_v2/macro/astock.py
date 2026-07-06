# -*- coding: utf-8 -*-
"""A股本土温度:复用 console.tools.live_text_impl(stocks probe 子进程,只读)现拉涨停池,
打板温度为确定性纯算术(常数在 themes.yaml astock 节),无 LLM;probe 缺席诚实降级。"""
from __future__ import annotations

import re

from .sources import load_themes


def build_astock(live_fn=None) -> dict:
    if live_fn is None:
        from guanlan_v2.console.tools import live_text_impl as live_fn
    cfg = load_themes().get("astock") or {}
    out = {"available": False, "temp": None, "zt_count": 0, "max_streak": 0,
           "break_ratio": 0.0, "top_reasons": [], "hot_list": [], "notes": []}
    zt = live_fn(source="em_zt_pool", limit=50)
    if not zt.get("ok") or zt.get("note"):
        out["notes"].append(f"em_zt_pool: {zt.get('note') or 'ok=False'}")
    rows = zt.get("rows") or []
    if rows:
        out["available"] = True
        out["zt_count"] = int(zt.get("n") or len(rows))
        streaks, breaks = [], 0
        for r in rows:
            m = re.search(r"(\d+)板", str(r.get("zt_stat") or ""))
            streaks.append(int(m.group(1)) if m else int(r.get("limit_days") or 1))
            if int(r.get("break_times") or 0) > 0:
                breaks += 1
        out["max_streak"] = max(streaks)
        out["break_ratio"] = round(breaks / len(rows), 4)
        out["temp"] = round(max(0.0, min(100.0,
            float(cfg.get("base", 30)) + float(cfg.get("k_zt", 0.35)) * out["zt_count"]
            + float(cfg.get("k_streak", 3)) * out["max_streak"]
            - float(cfg.get("k_break", 30)) * out["break_ratio"])), 1)
    for src, key, keep in (("ths_hot_reason", "top_reasons", 8),
                           ("ths_hot_list", "hot_list", 10)):
        res = live_fn(source=src, limit=keep)
        if res.get("rows"):
            out[key] = res["rows"][:keep]
        elif res.get("note"):
            out["notes"].append(f"{src}: {res['note']}")
    return out
