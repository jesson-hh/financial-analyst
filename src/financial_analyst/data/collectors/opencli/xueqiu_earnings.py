"""xueqiu earnings-date collector (cookie-mode)."""
from __future__ import annotations
from typing import List
from financial_analyst.data.collectors.opencli.runner import run_opencli


class XueqiuEarningsCollector:
    """Pull expected earnings release dates for a stock from xueqiu."""

    def fetch(self, code: str) -> List[dict]:
        """Returns list[{code, report_date, quarter}]."""
        short = code.replace("SH", "").replace("SZ", "").replace("BJ", "")
        result = run_opencli("xueqiu", "earnings-date", short, timeout=60)
        # Normalize: ensure 'code' field is set
        if isinstance(result, list):
            for r in result:
                r.setdefault("code", code.upper())
        return result
