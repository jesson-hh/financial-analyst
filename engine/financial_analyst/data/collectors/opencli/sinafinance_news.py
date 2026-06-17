"""sinafinance 7x24 collector — alternative news source."""
from __future__ import annotations
from pathlib import Path
from typing import List
from financial_analyst.data.collectors.news.base import BaseNewsCollector
from financial_analyst.data.collectors.opencli.runner import run_opencli
from financial_analyst.data.net import rate_limited


class SinafinanceNewsCollector(BaseNewsCollector):
    """Pull 7x24 快讯 from sinafinance. Public."""

    @rate_limited("sinafinance", cache_key=lambda self, limit=50: f"sina:{int(limit)}")
    def fetch(self, limit: int = 50) -> List[dict]:
        return run_opencli("sinafinance", "news", "--limit", str(limit))

    def collect(self, code: str = "", days: int = 7,
                target_dir: Path = Path("news")) -> List[Path]:
        target_dir = Path(target_dir)
        items = self.fetch(limit=200)
        if code:
            # sinafinance's content may mention the stock code or its name
            short = code.replace("SH", "").replace("SZ", "").replace("BJ", "")
            items = [it for it in items if short in (it.get("content") or "")]
        if not items:
            return []
        out_dir = target_dir / (code.upper() if code else "_market")
        out_dir.mkdir(parents=True, exist_ok=True)
        by_date = {}
        for it in items:
            date_str = (it.get("time", "") or "")[:10] or "unknown"
            by_date.setdefault(date_str, []).append(it)
        written: List[Path] = []
        for date_str, batch in by_date.items():
            f = out_dir / f"sinafinance_news_{date_str}.txt"
            lines = [f"# sinafinance 7x24 — {date_str}\n"]
            for it in batch:
                lines.append(f"## {it.get('time', '')}")
                lines.append((it.get("content") or "").strip())
                views = it.get("views")
                if views:
                    lines.append(f"\n_{views}_")
                lines.append("")
            f.write_text("\n".join(lines), encoding="utf-8")
            written.append(f)
        return written
