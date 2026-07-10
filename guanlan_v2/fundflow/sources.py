# -*- coding: utf-8 -*-
"""资金流现拉腿:统一经 datafeed.live_client(stocks 探针)只读拉取。

两条腿,各自独立、互不冒充:
  · fetch_sector(kind) —— 板块资金流一档(concept|industry),含净流入/净流出两端
  · fetch_market()     —— 全市场资金五档(沪深合计),独立源

红线:大盘五档绝不可由板块加总得出。东财 t:2 混排一/二/三级行业,股票重复归属
(真机 up+down=16545 >> A股约 5400;加总主力 +963.50亿 vs 独立源真值 -397.91亿,
连符号都相反)。失败诚实降级不抛穿。"""
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


def fetch_market(live_fn=None) -> dict:
    """拉全市场资金五档(沪深合计,独立源)。返回 {ok, row, note}。

    row = {date, main_net, super_net, large_net, mid_net, small_net, src_host}(单位:元)。
    绝不由板块加总兜底——宁可 ok=False 显形,也不给错数。
    """
    if live_fn is None:
        live_fn = _client_live
    r = live_fn(source="eastmoney_market_fund_flow", code="", limit=1)

    rows = r.get("rows")
    if rows is None:
        from guanlan_v2.datafeed import live_client as lc
        rows = lc.native_rows(r.get("items") or [])

    rows = rows or []
    if not rows or not isinstance(rows[0], dict):
        return {"ok": False, "row": {},
                "note": r.get("note") or "大盘资金源本次 0 行(源降级)"}
    return {"ok": True, "row": rows[0], "note": r.get("note") or ""}
