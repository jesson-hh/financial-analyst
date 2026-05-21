"""同花顺热股榜 collector (public, no cookie).

opencli backs this via the ``ths hot-rank`` browser-bridge command. The
raw payload has ``rank, name, changePercent, heat, tags`` but NO code —
the THS frontend renders by name only. We best-effort extract the 6-digit
code from the ``tags`` string when it leads with one (`"002342,商业航天,军工"`).

Notes vs. ``xueqiu-hot``:
- THS is public, no Chrome cookie required.
- Heat is a string like ``"686.7万热度"``; we keep it verbatim — downstream
  consumers can parse if they need a number.
- Tags include 涨停天数 / 概念板块 — useful sentiment signal that we
  forward into the ``tags`` field of the returned dict.
"""
from __future__ import annotations
import re
from typing import List
from financial_analyst.data.collectors.opencli.runner import run_opencli


_CODE_RE = re.compile(r"^\s*(\d{6})\b")


def _extract_code(tags: str) -> str:
    """Pull the 6-digit code from the leading position of ``tags`` if present.
    Returns "" when tags doesn't start with one.
    """
    m = _CODE_RE.match(tags or "")
    return m.group(1) if m else ""


class THSHotRankCollector:
    """Pull 同花顺热股榜 (top ranked stocks by retail heat).

    Returns ``list[{rank, code, name, changePercent, heat, tags}]`` —
    same shape as ``xueqiu-hot`` so the same ``upsert_hot_stocks`` path
    consumes it without schema gymnastics.
    """

    def fetch(self, limit: int = 20) -> List[dict]:
        raw = run_opencli(
            "ths", "hot-rank",
            "--limit", str(limit),
            timeout=60,
        )
        items: List[dict] = []
        for r in raw or []:
            tags = r.get("tags") or ""
            items.append({
                "rank": r.get("rank"),
                "code": _extract_code(tags),
                "name": r.get("name"),
                "changePercent": r.get("changePercent"),
                "heat": r.get("heat"),
                "tags": tags,
            })
        return items
