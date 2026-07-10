# -*- coding: utf-8 -*-
"""全球情绪温度计双源客户端:Polymarket Gamma + Kalshi(公开行情,免鉴权,2026-07-06 真机实测直连可达)。

外部失败恒不 raise:返回 (rows, notes),notes 记因——诚实降级由 pulse 层显形,绝不编造。
Polymarket 必须按宏观 tag_slug 过滤(按成交量全排序全是体育/电竞);Kalshi 新版价格字段
为美元字符串(last_price_dollars 等),无成交市场取 bid/ask 中价,再缺则跳过计数。
"""
from __future__ import annotations

import json
from pathlib import Path

import yaml

_GAMMA = "https://gamma-api.polymarket.com"
_KALSHI = "https://api.elections.kalshi.com/trade-api/v2"
_TIMEOUT = 10
_RETRY_SLEEP = 0.8   # 串行连打偶发 SSL 截断(2026-07-06 真机 7 连发 3 失败),单次重试+间隔即愈
_THEMES_PATH = Path(__file__).with_name("themes.yaml")


def load_themes() -> dict:
    with open(_THEMES_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


_PER_EVENT_CAP = 25   # 单 event 子市场入池上限(防极端 event 撑爆翻译批量/快照)


def _http():
    import requests
    return requests


def _f(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _get_json(http, url, params):
    """带单次重试的 GET(重试仍败则向上抛给调用方计 note;不做重试轰炸)。"""
    import time as _t
    try:
        r = http.get(url, params=params, timeout=_TIMEOUT)
        r.raise_for_status()
        return r.json()
    except Exception:
        _t.sleep(_RETRY_SLEEP)
        r = http.get(url, params=params, timeout=_TIMEOUT)
        r.raise_for_status()
        return r.json()


def fetch_polymarket(tags, limit=12, http=None):
    http = http or _http()
    rows, notes = [], []
    for tag in tags or []:
        try:
            events = _get_json(http, f"{_GAMMA}/events", {
                "tag_slug": tag, "active": "true", "closed": "false",
                "order": "volume24hr", "ascending": "false", "limit": limit})
        except Exception as e:
            notes.append(f"polymarket tag={tag} 失败: {type(e).__name__}: {e}")
            continue
        for ev in events or []:
            # event.closed=False 不代表其子市场都活着:已过期会议/已结算问题的子市场
            # closed=True、结算价 0/1,原市场页不展示——剔掉,否则既造幽灵 0% 行,
            # 又(因无自身量而借 event 总量)顶到量榜首污染锚定温度。
            #
            # 全部活跃市场都入池:锚定市场常是低成交量的尾部市场(如 geopolitics 的
            # military clash),按量截前几名会让它们永不进池 → 主题温度恒 "—"。
            # 展示层另按 display_top_n 切片,池全量 ≠ 页面全列。_PER_EVENT_CAP 仅防
            # 极端 event(数十子市场)撑爆翻译批量与快照体积。
            alive = [m for m in (ev.get("markets") or []) if not _truthy(m.get("closed"))]
            alive.sort(key=lambda m: _f(m.get("volume24hr")), reverse=True)
            for m in alive[:_PER_EVENT_CAP]:
                row = _pm_market_row(m, ev)
                if row is not None:
                    rows.append(row)
    return rows, notes


def _truthy(v):
    """Gamma 的 closed 可能是 bool 或 "true"/"false" 字符串。"""
    if isinstance(v, str):
        return v.strip().lower() == "true"
    return bool(v)


def _pm_market_row(m, ev):
    try:
        outcomes = json.loads(m.get("outcomes") or "[]")
        prices = json.loads(m.get("outcomePrices") or "[]")
    except Exception:
        return None
    if not outcomes or not prices:
        return None
    binary_yes = len(outcomes) == 2 and str(outcomes[0]).lower() == "yes"
    q = m.get("question") or ev.get("title") or ""
    if not binary_yes:
        q = f"{q} → {outcomes[0]}"
    return {"source": "polymarket", "id": f"pm_{m.get('id')}", "question": q,
            "prob": round(_f(prices[0]), 4),
            # 只取 market 自身 24h 量:回落 event 总量会让同 event 各行显示同一个数字
            # (伪造归属),并把无量的死市场顶到成交量榜首。
            "volume": _f(m.get("volume24hr")),
            "close_time": (m.get("endDate") or "")[:10],
            "url": f"https://polymarket.com/event/{ev.get('slug', '')}"}


def fetch_kalshi(series, limit=12, http=None):
    http = http or _http()
    rows, notes = [], []
    for st in series or []:
        try:
            markets = (_get_json(http, f"{_KALSHI}/markets", {
                "series_ticker": st, "status": "open", "limit": limit}) or {}).get("markets") or []
        except Exception as e:
            notes.append(f"kalshi series={st} 失败: {type(e).__name__}: {e}")
            continue
        skipped = 0
        for m in markets:
            prob = _kalshi_prob(m)
            if prob is None:
                skipped += 1
                continue
            rows.append({"source": "kalshi", "id": f"k_{m.get('ticker')}",
                         "question": str(m.get("title") or ""),
                         "prob": prob, "volume": _f(m.get("liquidity_dollars")),
                         "close_time": (m.get("close_time") or "")[:10],
                         "url": f"https://kalshi.com/markets/{str(st).lower()}"})
        if skipped:
            notes.append(f"kalshi series={st} 无价跳过 {skipped} 个")
    return rows, notes


def _kalshi_prob(m):
    last = _f(m.get("last_price_dollars"))
    if last > 0:
        return round(last, 4)
    yb, ya = _f(m.get("yes_bid_dollars")), _f(m.get("yes_ask_dollars"))
    if yb > 0 and ya > 0:
        return round((yb + ya) / 2, 4)
    return None
