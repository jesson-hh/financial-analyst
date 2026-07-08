# -*- coding: utf-8 -*-
"""板块资金流现拉腿:统一经 datafeed.live_client(stocks 探针)只读拉取。

一个源两档(concept/industry);大盘分解与涨跌头条由 pulse 层按行业档加总导出,
本模块只负责把一档板块资金流拉成源原生行,失败诚实降级不抛穿。"""
from __future__ import annotations


def _client_live(source: str, code: str = "", limit: int = 200) -> dict:
    from guanlan_v2.datafeed import live_client as lc
    r = lc.probe(source, code=code, limit=limit)
    return {"ok": bool(r.get("ok")) and r.get("status") in ("ok", ""),
            "rows": lc.native_rows(r.get("items")), "n": int(r.get("n") or 0),
            "note": r.get("note") or ""}


def fetch_sector(kind: str, live_fn=None) -> dict:
    """拉一档板块资金流(concept|industry)。返回 {ok, rows, note}。"""
    if live_fn is None:
        live_fn = _client_live
    k = "industry" if str(kind).lower().startswith("ind") else "concept"
    r = live_fn(source="eastmoney_sector_fund_flow", code=k, limit=200)

    rows = r.get("rows")
    if rows is None:
        from guanlan_v2.datafeed import live_client as lc
        rows = lc.native_rows(r.get("items") or [])

    rows = rows or []
    if not rows:
        return {"ok": False, "rows": [],
                "note": r.get("note") or f"{k} 档板块资金流本次 0 行(非交易日/源降级)"}
    return {"ok": True, "rows": rows, "note": r.get("note") or ""}
