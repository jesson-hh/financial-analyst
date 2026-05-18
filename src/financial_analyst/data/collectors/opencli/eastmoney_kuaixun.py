"""eastmoney 7x24 快讯 collector → writes into news DB."""
from __future__ import annotations
from pathlib import Path
from typing import List
from financial_analyst.data.collectors.news.base import BaseNewsCollector
from financial_analyst.data.collectors.opencli.runner import run_opencli


class EastmoneyKuaixunCollector(BaseNewsCollector):
    """Pull 7x24 快讯 from eastmoney. Public — no login.

    The CLI's `--format json` returns list[{time, title, summary, stocks}].
    `stocks` is a comma-joined string of "marketCode.symbol" identifiers, e.g. "1.600030, 116.06030".
    """

    def fetch(self, limit: int = 50) -> List[dict]:
        """Return raw list of 快讯 dicts."""
        return run_opencli("eastmoney", "kuaixun", "--limit", str(limit))

    def collect(self, code: str = "", days: int = 7,
                target_dir: Path = Path("news")) -> List[Path]:
        """Write filtered news to target_dir/<code>/eastmoney_kuaixun_<date>.txt.

        If code is empty, dumps ALL kuaixun. If code given, filters to that code only.
        """
        target_dir = Path(target_dir)
        items = self.fetch(limit=200)
        if code:
            ts_pattern = code[2:] if len(code) > 6 else code  # "SH600519" -> "600519"
            items = [it for it in items if ts_pattern in (it.get("stocks") or "")]
        if not items:
            return []

        out_dir = target_dir / (code.upper() if code else "_market")
        out_dir.mkdir(parents=True, exist_ok=True)
        # Group by date
        by_date = {}
        for it in items:
            date_str = (it.get("time", "") or "")[:10] or "unknown"
            by_date.setdefault(date_str, []).append(it)
        written: List[Path] = []
        for date_str, batch in by_date.items():
            f = out_dir / f"eastmoney_kuaixun_{date_str}.txt"
            lines = [f"# eastmoney 7x24 快讯 — {date_str}\n"]
            for it in batch:
                lines.append(f"## {it.get('time', '')} {it.get('title', '')}")
                summary = (it.get("summary") or "").strip()
                if summary:
                    lines.append(summary)
                stocks = it.get("stocks")
                if stocks:
                    lines.append(f"\n_关联: {stocks}_")
                lines.append("")
            f.write_text("\n".join(lines), encoding="utf-8")
            written.append(f)
        return written
