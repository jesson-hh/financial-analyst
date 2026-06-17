# -*- coding: utf-8 -*-
"""落子事后 TCA(执行质量分析)—— guanlan 自有,纯函数。

只读落子台账(``var/seats_ledger.jsonl`` 的 trade 事件,已记真成交价/量/方向/日期/decision_id),
对每笔成交算「成交价 vs 当日基准」的执行成本(bps),回答「这笔下单执行得好不好、滑点吃掉多少」。

  · ``day_vwap``           当日 5min bar 量价加权均价(典型价 (H+L+C)/3 × vol)
  · ``cost_bps``           滑点成本 bps,方向定号:买高于基准 / 卖低于基准 = **正成本**(吃亏)
  · ``compute_trade_tca``  逐笔 vs VWAP / 到达价 / 开盘 / 收盘 的成本(缺基准诚实 None)
  · ``summarize_tca``      成交额加权汇总 + 按日 / 按策略 + coverage(缺则 None 不补 0)

**只算不取数**:基准(VWAP/到达价/OHLC)由端点用真 5min/日线喂入,本模块零引擎/零 IO/零文件,
故可独立测试。**不碰交易执行、不改台账(只读)、缺基准显形 None 绝不伪造**(红线)。台账是日级 +
影子/纸面盘 → 这是「成交 vs 当日基准」的执行质量,非 tick 级 IS(端点 warnings 显形口径)。"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional


def _num(v: Any) -> Optional[float]:
    """NaN/Inf/不可转 → None,否则 float(JSON 安全)。"""
    try:
        f = float(v)
        return f if math.isfinite(f) else None
    except Exception:  # noqa: BLE001
        return None


# ── 当日 VWAP ────────────────────────────────────────────────────────────────
def day_vwap(bars: Optional[List[Dict[str, Any]]]) -> Optional[float]:
    """当日量价加权均价:Σ tp·vol / Σ vol,典型价 tp=(high+low+close)/3。
    ``bars`` = 当日 5min bar 列表(各含 high/low/close/vol)。空/总量≤0/无有效行 → None。"""
    if not bars:
        return None
    num = 0.0
    den = 0.0
    for b in bars:
        h, lo, c, vol = _num(b.get("high")), _num(b.get("low")), _num(b.get("close")), _num(b.get("vol"))
        if c is None or vol is None or vol <= 0:
            continue
        tp = ((h if h is not None else c) + (lo if lo is not None else c) + c) / 3.0
        num += tp * vol
        den += vol
    if den <= 0:
        return None
    return num / den


# ── 滑点成本 bps(方向定号)───────────────────────────────────────────────────
def cost_bps(exec_price: Any, ref_price: Any, side: str) -> Optional[float]:
    """成交价相对基准价的执行成本(bps,1e4 倍)。**方向定号**:买入价高于基准(多付)、
    卖出价低于基准(少收)= **正成本**(吃亏);反之为负(占便宜)。
    成交价/基准价缺失或 ≤0 → None(诚实,不除零)。"""
    ex, rf = _num(exec_price), _num(ref_price)
    if ex is None or rf is None or ex <= 0 or rf <= 0:
        return None
    raw = (ex - rf) / rf
    signed = raw if side == "buy" else -raw      # 买:exec>ref 正;卖:exec<ref 正
    return signed * 1e4


# ── 逐笔 TCA ─────────────────────────────────────────────────────────────────
def compute_trade_tca(trade: Dict[str, Any], refs: Dict[str, Any]) -> Dict[str, Any]:
    """对一笔成交,按各基准算执行成本 bps(缺基准 → None)。``refs`` 含可选键
    ``vwap``(当日 VWAP)/``arrival``(决策时到达价,仅决策链接成交可得)/``open``/``close``。"""
    side = trade.get("side")
    px = _num(trade.get("price"))
    try:
        qty = int(trade.get("qty"))
    except (TypeError, ValueError):
        qty = 0
    notional = (px * qty) if (px is not None and qty) else None
    refs = refs or {}
    return {
        "code": trade.get("code"),
        "name": trade.get("name"),
        "date": trade.get("date"),
        "side": side,
        "price": px,
        "qty": qty,
        "notional": notional,
        "source": trade.get("source"),
        "decision_id": trade.get("decision_id"),
        "strategy_id": trade.get("strategy_id"),
        "strategy_name": trade.get("strategy_name"),
        "vwap": _num(refs.get("vwap")),
        "arrival": _num(refs.get("arrival")),
        "cost_vwap_bps": cost_bps(px, refs.get("vwap"), side),
        "cost_arrival_bps": cost_bps(px, refs.get("arrival"), side),
        "cost_open_bps": cost_bps(px, refs.get("open"), side),
        "cost_close_bps": cost_bps(px, refs.get("close"), side),
    }


_METRICS = ("cost_vwap_bps", "cost_arrival_bps", "cost_open_bps", "cost_close_bps")


def _wavg(rows: List[Dict[str, Any]], key: str) -> Optional[float]:
    """成交额加权平均某成本指标:Σ(m·notional)/Σnotional,仅计 m 与 notional 均有效的笔;
    无有效样本 → None(诚实,不退化成 0)。"""
    num = 0.0
    den = 0.0
    for r in rows:
        m = r.get(key)
        w = r.get("notional")
        if m is None or w is None or w <= 0:
            continue
        num += float(m) * float(w)
        den += float(w)
    if den <= 0:
        return None
    return num / den


def _strategy_key(r: Dict[str, Any]) -> str:
    return (r.get("strategy_name") or r.get("strategy_id") or r.get("source") or "—")


def summarize_tca(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """逐笔 TCA 行 → 汇总:成交额加权各成本指标(无样本→None)、总成交额/笔数、按日、按策略、
    coverage(每个基准有几笔可算)。缺基准的指标一律 None 而非 0(诚实)。"""
    rows = list(rows or [])
    n = len(rows)
    total_notional = sum((r.get("notional") or 0.0) for r in rows)

    out: Dict[str, Any] = {
        "n_trades": n,
        "total_notional": total_notional if n else None,
        "coverage": {m.replace("cost_", "").replace("_bps", ""): sum(1 for r in rows if r.get(m) is not None)
                     for m in _METRICS},
    }
    for m in _METRICS:
        out[m] = _wavg(rows, m)

    # 按方向
    out["by_side"] = []
    for sd in ("buy", "sell"):
        srows = [r for r in rows if r.get("side") == sd]
        if srows:
            out["by_side"].append({
                "side": sd, "n_trades": len(srows),
                "notional": sum((r.get("notional") or 0.0) for r in srows),
                "cost_vwap_bps": _wavg(srows, "cost_vwap_bps"),
            })

    # 按日(逆序,今日在前)
    by_day: Dict[str, List[Dict[str, Any]]] = {}
    for r in rows:
        by_day.setdefault(str(r.get("date") or ""), []).append(r)
    out["by_day"] = [{
        "date": d, "n_trades": len(rs),
        "notional": sum((x.get("notional") or 0.0) for x in rs),
        "cost_vwap_bps": _wavg(rs, "cost_vwap_bps"),
        "cost_arrival_bps": _wavg(rs, "cost_arrival_bps"),
    } for d, rs in sorted(by_day.items(), reverse=True)]

    # 按策略
    by_strat: Dict[str, List[Dict[str, Any]]] = {}
    for r in rows:
        by_strat.setdefault(_strategy_key(r), []).append(r)
    out["by_strategy"] = [{
        "strategy": k, "n_trades": len(rs),
        "notional": sum((x.get("notional") or 0.0) for x in rs),
        "cost_vwap_bps": _wavg(rs, "cost_vwap_bps"),
        "cost_arrival_bps": _wavg(rs, "cost_arrival_bps"),
    } for k, rs in sorted(by_strat.items(), key=lambda kv: -sum((x.get("notional") or 0.0) for x in kv[1]))]

    out["trades"] = rows
    return out
