"""Xueqiu real-time quote collector (cookie-mode).

`opencli xueqiu stock <symbol>` returns a single dict with live price,
change %, OHLC, volume, amount, turnover, market cap, and a
``market_status`` field (交易中 / 已收盘 / 集合竞价 ...).

Used as the price provider for the alert engine (盯盘提醒) and as a
standalone ``realtime_quote`` buddy tool — distinct from quote_lookup
which reads end-of-day Tushare/Qlib data.
"""
from __future__ import annotations
from typing import Any, Dict, Optional
from financial_analyst.data.collectors.opencli.runner import run_opencli
from financial_analyst.data.net import rate_limited


class XueqiuStockCollector:
    """Pull one stock's real-time quote. Returns a dict (or None if the
    symbol isn't found / market data unavailable)."""

    @rate_limited("xueqiu", cache_key=lambda self, code: f"stock:{str(code).upper()}")
    def fetch(self, code: str) -> Optional[Dict[str, Any]]:
        raw = run_opencli("xueqiu", "stock", code, timeout=45)
        # opencli returns a list with one element for this endpoint.
        if isinstance(raw, list):
            return raw[0] if raw else None
        if isinstance(raw, dict):
            return raw
        return None

    def price(self, code: str) -> Optional[float]:
        """Convenience: just the latest price as a float, or None."""
        d = self.fetch(code)
        if not d:
            return None
        p = d.get("price")
        try:
            return float(p) if p is not None else None
        except (TypeError, ValueError):
            return None
