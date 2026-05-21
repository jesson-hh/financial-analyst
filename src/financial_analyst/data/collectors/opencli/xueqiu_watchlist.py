"""Xueqiu watchlist + groups collectors (cookie-mode)."""
from __future__ import annotations
from typing import List
from financial_analyst.data.collectors.opencli.runner import run_opencli


class XueqiuWatchlistCollector:
    """Pull xueqiu 自选股 / 模拟组合 list. Returns
    ``list[{symbol, name, price, changePercent, url}]``.

    ``pid`` (分组ID) defaults to '-1' (全部). Other values:
    ``-4`` 模拟, ``-5`` 沪深, ``-6`` 美股, ``-7`` 港股, ``-10`` 实盘,
    or any positive integer (user-created group, from ``XueqiuGroupsCollector``).
    """

    def fetch(self, pid: str = "-1", limit: int = 100) -> List[dict]:
        return run_opencli(
            "xueqiu", "watchlist",
            "--pid", str(pid),
            "--limit", str(limit),
            timeout=60,
        ) or []


class XueqiuGroupsCollector:
    """Pull the user's group structure: ``list[{pid, name, count}]``.
    Builtin pids: -1 全部 / -4 模拟 / -5 沪深 / -6 美股 / -7 港股 / -10 实盘."""

    def fetch(self) -> List[dict]:
        return run_opencli("xueqiu", "groups", timeout=60) or []
