# -*- coding: utf-8 -*-
"""A股本土温度:经 datafeed.live_client(stocks 统一 probe,只读)现拉涨停池,
打板温度为确定性纯算术(常数在 themes.yaml astock 节),无 LLM;probe 缺席诚实降级。"""
from __future__ import annotations

import re

from .sources import load_themes

_ZT_LIMIT = 300   # 统一客户端上限;旧壳 50 饱和已修,极端 >300 家才截断


def _client_live(source: str, limit: int = 50, **kw) -> dict:
    """回落取数腿:统一实时客户端(与 ww_live_text 同源同信封;rows=源原生行形)。"""
    from guanlan_v2.datafeed import live_client as lc
    r = lc.probe(source, code=kw.get("code", ""), date=kw.get("date", ""), limit=limit)
    return {"ok": bool(r.get("ok")) and r.get("status") in ("ok", ""),
            "rows": lc.native_rows(r.get("items")), "n": int(r.get("n") or 0),
            "note": r.get("note") or ""}


def _read_tape_safe() -> dict:
    """读盘口快照;任何失败返回 warming 让 build_astock 回落直拉(绝不硬依赖)。"""
    try:
        from guanlan_v2.datafeed import market_tape as mt
        return mt.read_tape()
    except Exception:  # noqa: BLE001
        return {"warming": True, "sources": {}}


def _tape_rows(tape: dict, alias: str):
    """快照里取某源 rows;缺席/warming/该源本轮拉取失败(保留上轮陈旧行,note 带『新失败』)
    → None(回落直拉该源)。绝不把上轮陈旧涨停行当今日打板算温度(评审 Important 红线)。"""
    if not tape or tape.get("warming"):
        return None
    from guanlan_v2.datafeed import live_client as lc
    src = (tape.get("sources") or {}).get(lc.resolve_source(alias) or alias)
    if not isinstance(src, dict):
        return None
    if "新失败" in (src.get("note") or ""):
        return None
    return src.get("rows")


def build_astock(live_fn=None) -> dict:
    """打板温度(确定性纯算术,无 LLM)。默认路径优先读盘口快照(收敛重复拉取),
    快照缺席/warming/该源缺失则回落直拉;显式传 live_fn(测试/自定义)跳过快照。"""
    use_tape = live_fn is None            # 显式 live_fn → 不走快照,直用它
    if live_fn is None:
        live_fn = _client_live
    tape = _read_tape_safe() if use_tape else {"warming": True, "sources": {}}
    cfg = load_themes().get("astock") or {}
    out = {"available": False, "temp": None, "zt_count": 0, "max_streak": 0,
           "break_ratio": 0.0, "top_reasons": [], "hot_list": [], "notes": []}
    rows = _tape_rows(tape, "em_zt_pool")
    if rows is None:                       # 快照无 → 回落直拉
        zt = live_fn(source="em_zt_pool", limit=_ZT_LIMIT)
        if not zt.get("ok") or zt.get("note"):
            out["notes"].append(f"em_zt_pool: {zt.get('note') or 'ok=False'}")
        rows = zt.get("rows") or []
    if rows:
        out["available"] = True
        out["zt_count"] = len(rows)
        if out["zt_count"] >= _ZT_LIMIT:
            out["notes"].append(f"涨停家数按上限 {_ZT_LIMIT} 截断,实际 >= {_ZT_LIMIT};温度以截断值计")
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
        r = _tape_rows(tape, src)
        if r is None:                     # 快照无 → 回落直拉
            res = live_fn(source=src, limit=keep)
            r = res.get("rows") or []
            if not r and res.get("note"):
                out["notes"].append(f"{src}: {res['note']}")
        if r:
            out[key] = r[:keep]
    return out
