"""Example: news collector backed by Tushare's news API.

Requires TUSHARE_TOKEN env var. Writes news/<code>/<date>.txt files.

To use:
    >>> from examples.custom_news_collector import TushareNewsCollector
    >>> collector = TushareNewsCollector()
    >>> collector.collect("SH600519", days=7)
    # → news/SH600519/2026-05-18.txt and similar files
"""
from __future__ import annotations
import os
from pathlib import Path
from typing import List
import pandas as pd
import requests
from financial_analyst.data.collectors.news.base import BaseNewsCollector


class TushareNewsCollector(BaseNewsCollector):
    """Pull news for an A-share from Tushare's news endpoint."""

    def __init__(self, token: str = "", url: str = "http://api.tushare.pro"):
        self.token = token or os.environ.get("TUSHARE_TOKEN", "")
        if not self.token:
            raise ValueError("TUSHARE_TOKEN missing")
        self.url = url

    def collect(self, code: str, days: int = 7, target_dir: Path = Path("news")) -> List[Path]:
        target = Path(target_dir) / code.upper()
        target.mkdir(parents=True, exist_ok=True)

        end = pd.Timestamp.today()
        start = end - pd.Timedelta(days=days)
        # NOTE: Tushare's news APIs differ by tier. A common one is `news` (general news)
        # filtered by stock; another is `cctv_news`. Substitute the API name you have access to.
        # This is a STUB showing the pattern.
        req = {
            "api_name": "news",
            "token": self.token,
            "params": {"start_date": start.strftime("%Y%m%d"),
                       "end_date": end.strftime("%Y%m%d")},
            "fields": "datetime,title,content",
        }
        try:
            r = requests.post(self.url, json=req, timeout=20)
            data = r.json()
            if data.get("code") != 0:
                return []
            rows = data["data"]["items"]
            fields = data["data"]["fields"]
        except Exception:
            return []

        # Filter rows mentioning the code or its short name (lookup would be needed)
        # For the stub, write each row as one .txt
        written: List[Path] = []
        for i, row in enumerate(rows[:50]):       # cap at 50 to avoid spam
            d = dict(zip(fields, row))
            date_part = str(d.get("datetime", ""))[:10] or end.strftime("%Y-%m-%d")
            out = target / f"{date_part}_{i:03d}.txt"
            text = f"# {d.get('title', '')}\n\n{d.get('content', '')}"
            out.write_text(text, encoding="utf-8")
            written.append(out)
        return written
