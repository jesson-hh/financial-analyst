"""eastmoney 龙虎榜 collector → writes into F10 drop-zone."""
from __future__ import annotations
from pathlib import Path
from typing import List
from financial_analyst.data.collectors.f10.base import BaseF10Collector
from financial_analyst.data.collectors.opencli.runner import run_opencli


class EastmoneyLonghuCollector(BaseF10Collector):
    """Pull 龙虎榜 from eastmoney. Public — no login."""

    def fetch(self, date: str = "") -> List[dict]:
        """Return raw 龙虎榜 list. If date empty, today's."""
        args = ["eastmoney", "longhu"]
        if date:
            args.extend(["--date", date])
        return run_opencli(*args)

    def collect(self, code: str = "", days: int = 1,
                target_dir: Path = Path("f10")) -> List[Path]:
        """Write 龙虎榜 entries for `code` to target_dir/<code>/longhu_<date>.txt.

        If code is empty, dumps all entries to f10/_market/.
        """
        target_dir = Path(target_dir)
        items = self.fetch()
        if code:
            short = code.replace("SH", "").replace("SZ", "").replace("BJ", "")
            items = [it for it in items if str(it.get("code", "")) == short]
        if not items:
            return []
        out_dir = target_dir / (code.upper() if code else "_market")
        out_dir.mkdir(parents=True, exist_ok=True)
        date_str = (items[0].get("tradeDate") or "today")
        f = out_dir / f"longhu_{date_str}.txt"
        lines = [f"# 龙虎榜 — {date_str}\n"]
        for it in items:
            lines.append(f"## {it.get('code')} {it.get('name')} ({it.get('market')})")
            lines.append(f"- 收盘: {it.get('closePrice'):.2f} ({it.get('changeRate'):+.2f}%)")
            lines.append(f"- 买入: {it.get('buyAmt')/1e8:.2f}亿  卖出: {it.get('sellAmt')/1e8:.2f}亿  净: {it.get('netAmt')/1e8:+.2f}亿")
            lines.append(f"- 上榜原因: {it.get('reason')}")
            lines.append("")
        f.write_text("\n".join(lines), encoding="utf-8")
        return [f]
