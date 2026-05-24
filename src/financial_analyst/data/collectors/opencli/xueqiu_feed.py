"""Xueqiu home-feed collector (cookie-mode).

Returns posts from accounts the logged-in user follows. Useful for
tracking the views of analysts / KOLs the user trusts. Each post
carries author / text / likes / replies / url.

Stocks are mentioned inline with the `$名字(SH600519)$` cashtag pattern;
we regex-extract those codes into ``related_codes`` so the news_query
tool can surface posts that mention a given stock.
"""
from __future__ import annotations
import re
from datetime import datetime
from typing import List
from financial_analyst.data.collectors.opencli.runner import run_opencli
from financial_analyst.data.net import rate_limited


# Match Chinese-name + code cashtags, e.g. "$贵州茅台(SH600519)$" or
# "$腾讯控股(00700)$". Code is one of:
#   - SH/SZ/BJ + 6 digits (A-share)
#   - 4-6 bare digits (HK)
#   - Latin letters 1-5 chars (US)
# Be permissive but keep things anchored on $...$.
_CASHTAG_RE = re.compile(
    r"\$[^$()]{1,40}?\(([A-Z]{2,3}\d{6}|\d{4,6}|[A-Z]{1,5}(?:\.[A-Z]{1,2})?)\)\$"
)
# Some posts use a shorter (CODE) form without the $...$ wrapper, e.g.
# "腾讯控股(00700)" at the start of an author label. Apply only to author/text
# combined as a fallback.
_PAREN_RE = re.compile(r"\(([A-Z]{2,3}\d{6}|\d{4,6})\)")


def _extract_mentions(text: str, *extra: str) -> str:
    """Pull a comma-separated unique list of mentioned codes from text.

    Returns "" when no mention found. Codes are normalised: A-share gets
    its prefix (SH/SZ/BJ); HK stays as 5-digit; bare digits without a
    prefix are LEFT as-is (the news_query LIKE search still matches).
    """
    haystack = " ".join([text or "", *extra])
    found: list[str] = []
    seen: set[str] = set()
    for m in _CASHTAG_RE.finditer(haystack):
        code = m.group(1).upper()
        if code not in seen:
            seen.add(code)
            found.append(code)
    if not found:
        for m in _PAREN_RE.finditer(haystack):
            code = m.group(1).upper()
            if code not in seen:
                seen.add(code)
                found.append(code)
    return ",".join(found)


class XueqiuFeedCollector:
    """Pull xueqiu home-feed posts (followed users' timeline).

    ``fetch()`` returns items shaped to drop into ``NewsDB.upsert_news``:
    ``{time, title, content, url, stocks}``. ``time`` is the collection
    timestamp because feed posts come without an explicit creation time
    in the opencli output.
    """

    @rate_limited("xueqiu", cache_key=lambda self, limit=20, page=1: f"feed:{int(limit)}:{int(page)}")
    def fetch(self, limit: int = 20, page: int = 1) -> List[dict]:
        raw = run_opencli(
            "xueqiu", "feed",
            "--limit", str(limit),
            "--page", str(page),
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
                "likes": r.get("likes"),
                "replies": r.get("replies"),
            })
        return items
