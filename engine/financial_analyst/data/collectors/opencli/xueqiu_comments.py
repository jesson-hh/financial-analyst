"""xueqiu comments collector — direct HTTP (no browser bridge).

2026-05-22: rewritten to fetch xueqiu's discussion API directly with
``requests`` instead of driving Chrome via opencli. The browser-bridge path
(``opencli xueqiu comments``) kept getting throttled by xueqiu's Aliyun WAF on
the *automated* Chrome session — ERR_TIMED_OUT / 60s timeouts — even though the
underlying HTTP endpoint is perfectly reachable (a plain ``requests`` call to
``stock.xueqiu.com`` / ``xueqiu.com/query`` returns 200 in ~0.2s). An anonymous
session is enough: GET the homepage to mint the ``xq_a_token`` / ``aliyungf_tc``
guest cookies, then call ``/query/v1/symbol/search/status``. No Chrome, no login.

Domestic site → connect直连 (``trust_env=False``), per project convention
(雪球国内站必须直连 — never route through the Clash international proxy).
"""
from __future__ import annotations

import html
import re
from datetime import datetime, timezone
from typing import List

from financial_analyst.data.net import domestic_session, rate_limited

_HOME = "https://xueqiu.com/"
_STATUS_API = "https://xueqiu.com/query/v1/symbol/search/status"
_TAG_RE = re.compile(r"<[^>]+>")


def _normalize_symbol(code: str) -> str:
    """Xueqiu wants an exchange-prefixed symbol (SH600519, SZ000858, BJ430139)."""
    c = str(code).upper().strip()
    if c[:2] in ("SH", "SZ", "BJ"):
        return c
    if c.isdigit() and len(c) == 6:
        if c[0] == "6":
            return "SH" + c
        if c[0] in "03":
            return "SZ" + c
        if c[0] in "84":
            return "BJ" + c
    return c


def _clean_text(raw) -> str:
    """Strip the HTML xueqiu wraps post bodies in and collapse whitespace."""
    txt = _TAG_RE.sub(" ", str(raw or ""))
    txt = html.unescape(txt)
    return re.sub(r"\s+", " ", txt).strip()


def _iso(ms) -> str:
    """xueqiu created_at is Unix milliseconds → ISO 8601 (UTC)."""
    try:
        return datetime.fromtimestamp(int(ms) / 1000, tz=timezone.utc).isoformat()
    except (TypeError, ValueError, OverflowError):
        return ""


def _normalize_item(item: dict) -> dict:
    """One raw discussion item → the row shape upsert_social_posts expects.

    Matches the legacy opencli adapter output:
    ``{id, author, text, likes, replies, retweets, created_at, url}``.
    """
    user = item.get("user") or {}
    uid = user.get("id")
    pid = item.get("id")
    return {
        "id": str(pid or ""),
        "author": str(user.get("screen_name") or ""),
        "text": _clean_text(item.get("description")),
        "likes": int(item.get("fav_count") or item.get("like_count") or 0),
        "replies": int(item.get("reply_count") or 0),
        "retweets": int(item.get("retweet_count") or 0),
        "created_at": _iso(item.get("created_at")),
        "url": f"https://xueqiu.com/{uid}/{pid}" if uid and pid else "",
    }


class XueqiuCommentsCollector:
    """Pull stock discussion comments from xueqiu.com via direct HTTP.

    No Chrome / login required: an anonymous session mints a guest token from
    the homepage, which is enough for the public discussion API. Returns a list
    of ``{id, author, text, likes, replies, retweets, created_at, url}`` dicts.
    """

    @rate_limited("xueqiu", cache_key=lambda self, code, limit=30: f"comments:{_normalize_symbol(code)}:{min(int(limit),100)}")
    def fetch(self, code: str, limit: int = 30) -> List[dict]:
        symbol = _normalize_symbol(code)
        limit = max(1, min(int(limit), 100))

        sess = domestic_session()
        # Mint the guest token (xq_a_token / aliyungf_tc) the API requires.
        sess.get(_HOME, timeout=12)

        rows: List[dict] = []
        seen: set = set()
        page_size = min(limit, 20)
        max_pages = min(5, (limit + page_size - 1) // page_size)

        for page in range(1, max_pages + 1):
            resp = sess.get(
                _STATUS_API,
                params={"symbol": symbol, "count": page_size,
                        "page": page, "sort": "time"},
                headers={
                    "Accept": "application/json, text/plain, */*",
                    "X-Requested-With": "XMLHttpRequest",
                    "Referer": f"https://xueqiu.com/S/{symbol}",
                },
                timeout=12,
            )
            ctype = resp.headers.get("content-type", "")
            if resp.status_code != 200 or "application/json" not in ctype:
                if page == 1:
                    raise RuntimeError(
                        f"xueqiu status HTTP {resp.status_code} (ctype={ctype!r}) "
                        f"for {symbol}: {resp.text[:160]}")
                break

            lst = resp.json().get("list")
            if not isinstance(lst, list) or not lst:
                break

            advanced = False
            for it in lst:
                row = _normalize_item(it)
                if not row["id"] or row["id"] in seen:
                    continue
                seen.add(row["id"])
                rows.append(row)
                advanced = True

            if len(rows) >= limit or len(lst) < page_size or not advanced:
                break

        return rows[:limit]
