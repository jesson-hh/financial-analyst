# -*- coding: utf-8 -*-
"""落子·五档盘口 + 逐笔面板取数(经 datafeed.live_client 统一门户,只读)。

零重造:盘口=stocks tdx_orderbook(五档挂单薄)、逐笔=tdx_transaction、报价 failover=
tdx_realtime_quote,全走观澜唯一现拉门户 live_client.probe(与 ww_live_text 同源同信封)。
纯展示/盯盘上下文,绝不回写 seats 信号。tdx 不可达 → ok:False + note 诚实降级,绝不编造挂单。
"""
from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional


def _num(v: Any) -> Optional[float]:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _client_live(source: str, code: str = "", limit: int = 30) -> Dict[str, Any]:
    """统一实时客户端取数腿(与 ww_live_text/astock 同源同信封;rows=源原生行形)。"""
    from guanlan_v2.datafeed import live_client as lc
    r = lc.probe(source, code=code, limit=limit)
    return {"ok": bool(r.get("ok")) and r.get("status") in ("ok", ""),
            "rows": lc.native_rows(r.get("items")), "n": int(r.get("n") or 0),
            "note": r.get("note") or ""}


def read_orderbook(code: str, live_fn: Optional[Callable] = None) -> Dict[str, Any]:
    """五档盘口快照(tdx_orderbook)。返回 {ok, code, price, last_close, open, high, low,
    levels:[{level,bid,bid_vol,ask,ask_vol}×≤5], note}。tdx 不可达/无数据→ok:False+note。"""
    live_fn = live_fn or _client_live
    r = live_fn("tdx_orderbook", code=code)
    rows = r.get("rows") or []
    book = rows[0] if rows and isinstance(rows[0], dict) else {}
    if not book:
        return {"ok": False, "code": code, "levels": [],
                "note": r.get("note") or "盘口不可用(tdx 未返回)"}
    levels: List[Dict[str, Any]] = []
    for i in range(1, 6):
        bid, ask = _num(book.get(f"bid{i}")), _num(book.get(f"ask{i}"))
        bv, av = _num(book.get(f"bid_vol{i}")), _num(book.get(f"ask_vol{i}"))
        if bid is None and ask is None and bv is None and av is None:
            continue                     # 该档整体缺失则跳过,不塞 0 价假档
        levels.append({"level": i, "bid": bid, "bid_vol": bv, "ask": ask, "ask_vol": av})
    if not levels:   # 有 book 壳但无任何真档(退市/空报价:get_security_quotes 返 []→退化 book)→ 诚实降级
        return {"ok": False, "code": str(book.get("code") or code), "levels": [],
                "note": r.get("note") or "盘口无挂单档(退市/空报价),诚实降级"}
    return {"ok": True, "code": str(book.get("code") or code),
            "price": _num(book.get("price")), "last_close": _num(book.get("last_close")),
            "open": _num(book.get("open")), "high": _num(book.get("high")),
            "low": _num(book.get("low")), "levels": levels, "note": r.get("note") or ""}


# pytdx buyorsell:0=主动买 1=主动卖 2=中性(部分版本 -1 未知)
_SIDE = {0: "buy", 1: "sell", 2: "neutral"}


def _tick_side(row: Dict[str, Any]) -> str:
    v = row.get("buyorsell")
    if v is None:
        v = row.get("bs")
    try:
        return _SIDE.get(int(v), "neutral")
    except (TypeError, ValueError):
        return "neutral"


def read_ticks(code: str, limit: int = 30, live_fn: Optional[Callable] = None) -> Dict[str, Any]:
    """最新逐笔成交(tdx_transaction)。返回 {ok, code, ticks:[{time,price,vol,side}], n, note}。
    tdx 不可达/无数据→ok:False+note,绝不编造成交。"""
    live_fn = live_fn or _client_live
    try:
        lim = max(1, min(int(limit or 30), 100))
    except (TypeError, ValueError):
        lim = 30
    # pytdx get_transaction_data 按时间升序返回(最旧在前),handler 未反转;若只请求 lim 笔则
    # probe 的 items[:lim] 会截到「最旧 lim 笔」丢掉最新成交(评审 live 复现)。故取满窗口(默认 50)
    # 再本地反转成最新在前,末端 [:lim] 取最新 lim 笔——面板「最新逐笔」名实相符。
    r = live_fn("tdx_transaction", code=code, limit=max(lim, 50))
    rows = list(reversed(r.get("rows") or []))
    ticks: List[Dict[str, Any]] = []
    for t in rows:
        if not isinstance(t, dict):
            continue
        vol = t.get("vol")
        if vol is None:
            vol = t.get("volume")
        ticks.append({"time": t.get("time"), "price": _num(t.get("price")),
                      "vol": _num(vol), "side": _tick_side(t)})
    out = ticks[:lim]   # 取满窗口反转后只返最新 lim 笔;n 与返回条数一致(不报满窗口大小)
    return {"ok": bool(out), "code": code, "ticks": out, "n": len(out),
            "note": r.get("note") or ("" if out else "无逐笔(非交易时段/tdx 不可达)")}


def read_quote_failover(code: str, live_fn: Optional[Callable] = None) -> Dict[str, Any]:
    """腾讯报价失败时的 failover:tdx_realtime_quote 给核心 price/OHLC(标 source=tdx,
    turnover/量比/pe 等腾讯专有字段留空,诚实标降级)。返回 {ok, code, source, price, ...} 或 ok:False。"""
    live_fn = live_fn or _client_live
    r = live_fn("tdx_realtime_quote", code=code, limit=1)
    rows = r.get("rows") or []
    q = rows[0] if rows and isinstance(rows[0], dict) else {}
    price = _num(q.get("price"))
    if not q or price is None:
        return {"ok": False, "code": code, "source": "tdx",
                "note": r.get("note") or "tdx 报价 failover 亦不可达"}
    last_close = _num(q.get("last_close"))
    change = round(price - last_close, 4) if (price is not None and last_close) else None
    pct = round((price / last_close - 1.0) * 100.0, 4) if (price is not None and last_close) else None
    return {"ok": True, "code": str(q.get("code") or code), "source": "tdx",
            "price": price, "prevClose": last_close,
            "open": _num(q.get("open")), "high": _num(q.get("high")), "low": _num(q.get("low")),
            "change": change, "changePercent": pct,
            "volume": _num(q.get("vol") if q.get("vol") is not None else q.get("volume")),
            "amount": _num(q.get("amount")),
            "note": "腾讯不可达,tdx 报价 failover(turnover/量比/pe/pb 等腾讯专有字段缺,诚实降级)"}
