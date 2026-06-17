"""xueqiu hot-stock collector — direct HTTP (no browser bridge).

2026-05-23: rewritten from opencli browser-bridge to direct HTTP, matching the
xueqiu_comments.py pattern. Same anti-bot risk as comments command, same fix:
mint a guest token from the homepage, then call the public hot_stock API.
See reference_guanlan_ui.md 头号守则 #1 (直连优先, 代理工具次之).

API: ``https://stock.xueqiu.com/v5/stock/hot_stock/list.json?size=N&type=10|12``
type=10 → 人气榜 (default), type=12 → 关注榜.
"""
from __future__ import annotations

from typing import List

from financial_analyst.data.net import domestic_session, rate_limited

_HOME = "https://xueqiu.com/"
_HOT_API = "https://stock.xueqiu.com/v5/stock/hot_stock/list.json"


class XueqiuHotStockCollector:
    """Pull xueqiu 热股榜 via direct HTTP.

    Returns ``list[{rank, symbol, name, price, changePercent, heat, url}]``
    — same shape the legacy opencli adapter produced, so upsert_hot_stocks
    keeps working unchanged.
    """

    @rate_limited("xueqiu_hot", cache_key=lambda self, limit=50, type_=10: f"hot:{int(type_)}:{max(1,min(int(limit),50))}")
    def fetch(self, limit: int = 50, type_: int = 10) -> List[dict]:
        """``type=10`` 人气榜 (default), ``type=12`` 关注榜.

        ``limit`` clamped to [1, 50] per xueqiu API.
        """
        limit = max(1, min(int(limit), 50))

        sess = domestic_session()
        # Mint the guest token (xq_a_token / aliyungf_tc) the API requires.
        sess.get(_HOME, timeout=12)

        resp = sess.get(
            _HOT_API,
            params={"size": limit, "type": int(type_)},
            headers={
                "Accept": "application/json, text/plain, */*",
                "X-Requested-With": "XMLHttpRequest",
                "Referer": "https://xueqiu.com/",
            },
            timeout=12,
        )
        ctype = resp.headers.get("content-type", "")
        if resp.status_code != 200 or "application/json" not in ctype:
            raise RuntimeError(
                f"xueqiu hot-stock HTTP {resp.status_code} (ctype={ctype!r}): "
                f"{resp.text[:160]}")

        items = ((resp.json() or {}).get("data") or {}).get("items") or []
        out: List[dict] = []
        for i, s in enumerate(items, start=1):
            pct = s.get("percent")
            sym = s.get("symbol")
            out.append({
                "rank": i,
                "symbol": sym,
                "name": s.get("name"),
                "price": s.get("current"),
                "changePercent": f"{pct:.2f}%" if pct is not None else None,
                "heat": s.get("value"),
                "url": f"https://xueqiu.com/S/{sym}" if sym else None,
            })
        return out
