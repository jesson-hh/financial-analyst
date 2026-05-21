"""Xueqiu hot-posts collector (cookie-mode).

Pulls the platform-wide hot-posts board — what's trending across all
of xueqiu right now (not user-specific like feed). Each entry has
rank / author / text / likes / url. Mentioned tickers are regex-pulled
into ``related_codes`` so they're discoverable via news_query.

Note: this is distinct from ``xueqiu hot-stock`` (which lists trending
*tickers*, not posts). The corresponding collector is the existing
``XueqiuHotStockCollector``.
"""
from __future__ import annotations
from datetime import datetime
from typing import List
from financial_analyst.data.collectors.opencli.runner import run_opencli
from financial_analyst.data.collectors.opencli.xueqiu_feed import _extract_mentions


class XueqiuHotPostsCollector:
    """Pull xueqiu site-wide hot posts. Returns shape ready for upsert_news."""

    def fetch(self, limit: int = 20) -> List[dict]:
        raw = run_opencli(
            "xueqiu", "hot",
            "--limit", str(limit),
            timeout=60,
        )
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        items: List[dict] = []
        for r in raw or []:
            author = r.get("author") or ""
            text = r.get("text") or ""
            title = (text[:80] + "…") if len(text) > 80 else text
            items.append({
                "time": now,
                "title": title,
                "content": text,
                "url": r.get("url") or "",
                "stocks": _extract_mentions(text, author),
                "author": author,
                "rank": r.get("rank"),
                "likes": r.get("likes"),
            })
        return items
