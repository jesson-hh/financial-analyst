"""eastmoney 十大流通股东 collector."""
from __future__ import annotations
from pathlib import Path
from typing import List
from financial_analyst.data.collectors.f10.base import BaseF10Collector
from financial_analyst.data.collectors.opencli.runner import run_opencli


class EastmoneyHoldersCollector(BaseF10Collector):
    """Pull 十大流通股东 from eastmoney. Public."""

    def fetch(self, code: str, limit: int = 10) -> List[dict]:
        """Return list of {rank, reportDate, name, holdNum, floatRatio, change}."""
        short = code.replace("SH", "").replace("SZ", "").replace("BJ", "")
        return run_opencli("eastmoney", "holders", short, "--limit", str(limit))

    def collect(self, code: str, days: int = 30,
                target_dir: Path = Path("f10")) -> List[Path]:
        if not code:
            return []
        items = self.fetch(code)
        if not items:
            return []
        report_date = items[0].get("reportDate", "")
        target_dir = Path(target_dir)
        out_dir = target_dir / code.upper()
        out_dir.mkdir(parents=True, exist_ok=True)
        f = out_dir / f"holders_{report_date}.txt"
        lines = [f"# {code.upper()} 十大流通股东 — {report_date}\n"]
        lines.append("| 排名 | 股东 | 持股 | 流通比例 | 变动 |")
        lines.append("|---|---|---|---|---|")
        for it in items:
            lines.append(
                f"| {it.get('rank')} | {it.get('name')} | "
                f"{it.get('holdNum'):,} | {it.get('floatRatio'):.2f}% | {it.get('change')} |"
            )
        f.write_text("\n".join(lines), encoding="utf-8")
        return [f]
