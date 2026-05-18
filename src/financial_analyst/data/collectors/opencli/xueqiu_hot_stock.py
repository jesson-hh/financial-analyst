"""xueqiu hot-stock collector (cookie-mode)."""
from __future__ import annotations
from typing import List
from financial_analyst.data.collectors.opencli.runner import run_opencli


class XueqiuHotStockCollector:
    """Pull xueqiu 热股榜. Returns list[{rank, symbol, name, price, changePercent, heat}]."""

    def fetch(self, limit: int = 50, type_: int = 10) -> List[dict]:
        """type=10 人气榜 (default), type=12 关注榜."""
        return run_opencli(
            "xueqiu", "hot-stock",
            "--limit", str(limit),
            "--type", str(type_),
            timeout=60,
        )
