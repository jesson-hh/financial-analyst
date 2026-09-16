"""Bounded, read-only company news and disclosed ownership queries.

These fixed Eastmoney endpoints are queried directly.  No stocks importer,
subprocess, persistent cache, LLM judgment, or global database write is involved.
News matches are external text/context; ownership is a secondary rendering of
periodic disclosures, never the user's personal holding or intraday ownership.
"""
from __future__ import annotations

import html
import json
import math
import re
import time
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

import requests

_HK = ZoneInfo("Asia/Hong_Kong")
_OWNERSHIP_URL = "https://emweb.securities.eastmoney.com/PC_HSF10/ShareholderResearch/PageAjax"
_NEWS_URL = "https://search-api-web.eastmoney.com/search/jsonp"
_MAX_BYTES = 2 * 1024 * 1024
_CALLBACK = "jQueryWeiwo"


def _code(value: str) -> str:
    from guanlan_v2.orchestration.data.symbols import normalize_symbol
    return normalize_symbol(value).engine_code


def _now() -> str:
    return datetime.now(_HK).isoformat(timespec="seconds")


def _json(url: str, params: dict[str, Any], *, referer: str) -> dict:
    """Fixed hosts, no redirects, bounded decoded body, bounded network waits."""
    if url not in {_OWNERSHIP_URL, _NEWS_URL}:
        raise ValueError("unreviewed endpoint")
    started = time.monotonic()
    with requests.get(url, params=params, headers={
        "User-Agent": "Mozilla/5.0", "Referer": referer,
    }, timeout=(3, 8), stream=True, allow_redirects=False) as response:
        if response.status_code != 200:
            raise RuntimeError(f"HTTP {response.status_code}")
        chunks, size = [], 0
        for chunk in response.iter_content(16384):
            size += len(chunk)
            if size > _MAX_BYTES or time.monotonic() - started > 12:
                raise RuntimeError("upstream response exceeded query budget")
            chunks.append(chunk)
    text = b"".join(chunks).decode("utf-8-sig").strip()
    if text.startswith(_CALLBACK + "(") and text.endswith(")"):
        text = text[len(_CALLBACK) + 1:-1]
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("upstream object missing")
    return data


def _day(value: Any) -> str | None:
    try:
        parsed = date.fromisoformat(str(value or "")[:10])
        return parsed.isoformat() if parsed <= datetime.now(_HK).date() else None
    except ValueError:
        return None


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = float(value)
        return parsed if math.isfinite(parsed) else None
    except (ValueError, TypeError):
        return None


def _text(value: Any, limit: int = 1500) -> str:
    return html.unescape(re.sub(r"<[^>]*>", "", str(value or ""))).strip()[:limit]


def query_shareholder_disclosures(code: str) -> dict[str, Any]:
    code = _code(code)
    source_ref = ("https://emweb.securities.eastmoney.com/PC_HSF10/"
                  f"ShareholderResearch/Index?type=web&code={code}")
    out: dict[str, Any] = {
        "source": "eastmoney_shareholder_disclosures", "source_ref": source_ref,
        "code": code, "scope": "issuer_disclosure", "trust": "secondary",
        "retrieved_at": _now(), "as_of": None, "status": "unavailable",
        "holders": None, "top_float_holders": [], "holder_counts": [],
        "point_in_time": False, "disclosure_date_status": "not_provided",
        "warnings": ["定期披露持股，不是实时持仓；东财为二手展示源。",
                     "此接口未给公告发布日期，不能按报告期推断当时已公开。"],
    }
    try:
        data = _json(_OWNERSHIP_URL, {"code": code}, referer=source_ref)
        if not any(isinstance(data.get(key), list) for key in ("sdgd", "sdltgd", "gdrs")):
            raise ValueError("ownership schema missing")
        rejected = 0

        def rows(key: str) -> list[dict]:
            nonlocal rejected
            accepted = []
            for row in (data.get(key) or [])[:200]:
                if not isinstance(row, dict):
                    rejected += 1
                    continue
                bare = row.get("SECURITY_CODE")
                dotted = row.get("SECUCODE")
                if ((not bare and not dotted) or
                        (bare and bare != code[2:]) or
                        (dotted and dotted != f"{code[2:]}.{code[:2]}") or
                        not _day(row.get("END_DATE"))):
                    rejected += 1
                    continue
                accepted.append(row)
            return accepted

        def holders(key: str, ratio_key: str) -> tuple[str | None, list[dict]]:
            valid = rows(key)
            period = max((_day(row["END_DATE"]) for row in valid), default=None)
            chosen = sorted((row for row in valid if _day(row["END_DATE"]) == period),
                            key=lambda row: _number(row.get("HOLDER_RANK")) or 999)
            items = []
            for row in chosen[:10]:
                if not row.get("HOLDER_NAME"):
                    continue
                items.append({
                    "rank": _number(row.get("HOLDER_RANK")),
                    "name": _text(row.get("HOLDER_NAME"), 240),
                    "shares": _number(row.get("HOLD_NUM")), "unit": "shares",
                    "pct": _number(row.get(ratio_key)),
                    "pct_basis": "float_shares" if key == "sdltgd" else "total_shares",
                    "change": _text(row.get("HOLD_NUM_CHANGE"), 120),
                    "report_date": period, "disclosure_date": None,
                    "source_ref": source_ref,
                })
            return period, items

        period, top = holders("sdgd", "HOLD_NUM_RATIO")
        _, floating = holders("sdltgd", "FREE_HOLDNUM_RATIO")
        counts = sorted(rows("gdrs"), key=lambda row: row["END_DATE"], reverse=True)[:8]
        out["holder_counts"] = [{
            "report_date": _day(row["END_DATE"]),
            "count": _number(row.get("HOLDER_TOTAL_NUM")), "unit": "accounts",
            "change_pct": _number(row.get("TOTAL_NUM_RATIO")),
            "disclosure_date": None, "source_ref": source_ref,
        } for row in counts]
        out["top_float_holders"] = floating
        if top:
            same_period = next((row for row in out["holder_counts"]
                                if row["report_date"] == period), {})
            out["holders"] = {"report_date": period, "top_holders": top,
                              "a_share_holders": same_period.get("count"),
                              "source": out["source"], "source_ref": source_ref}
        dates = [row["report_date"] for row in floating + out["holder_counts"]]
        if period:
            dates.append(period)
        out["as_of"] = max(dates, default=None)
        out["status"] = ("partial" if dates and rejected else "ok" if dates else
                         "unavailable" if rejected else "empty")
        if rejected:
            out["warnings"].append("已拒绝主体、结构或报告日期不符的行；覆盖以接受行计。")
        out["coverage"] = {"top_holders": len(top), "top_float_holders": len(floating),
                           "holder_count_periods": len(counts), "rejected_rows": rejected,
                           "complete_ownership_register": False}
    except (requests.RequestException, ValueError, RuntimeError, TypeError) as exc:
        out["error"] = type(exc).__name__  # no URL params/credentials in diagnostics
        out["warnings"].append("本次上游未返回可核验数据；不能据此判断没有股东。")
    return out


def query_company_news(code: str, *, limit: int = 10) -> dict[str, Any]:
    code = _code(code)
    limit = min(20, max(1, int(limit)))
    source_ref = f"https://so.eastmoney.com/news/s?keyword={code[2:]}"
    out: dict[str, Any] = {
        "source": "eastmoney_company_news_search", "source_ref": source_ref,
        "code": code, "scope": "context", "trust": "external_untrusted",
        "retrieved_at": _now(), "as_of": None, "items": [], "status": "unavailable",
        "judgment_status": "not_run", "tag": None, "read": None,
        "warnings": ["搜索结果仅保留代码精确命中；可能为多公司新闻，需 Agent 判断关联及情绪。",
                     "仅本次搜索页，排序不保证时间全覆盖；空结果不代表没有新闻。"],
    }
    query = {"uid": "", "keyword": code[2:], "type": ["cmsArticleWebOld"],
             "client": "web", "clientType": "web", "clientVersion": "curr",
             "param": {"cmsArticleWebOld": {"searchScope": "default", "sort": "default",
                       "pageIndex": 1, "pageSize": limit, "preTag": "<em>", "postTag": "</em>"}}}
    try:
        data = _json(_NEWS_URL, {"cb": _CALLBACK, "param": json.dumps(query),
                               "_": str(int(time.time() * 1000))}, referer=source_ref)
        result = data.get("result")
        raw = result.get("cmsArticleWebOld") if isinstance(result, dict) else None
        if data.get("code") != 0 or not isinstance(raw, list):
            raise ValueError("news schema missing")
        rejected = 0
        exact = re.compile(r"(?<!\d)" + code[2:] + r"(?!\d)")
        for row in raw[:20]:
            if not isinstance(row, dict):
                rejected += 1
                continue
            title, summary = _text(row.get("title"), 400), _text(row.get("content"))
            if not exact.search(title + " " + summary) or not _day(row.get("date")):
                rejected += 1
                continue
            url = str(row.get("url") or "")
            if not re.fullmatch(r"https?://(?:finance|stock)\.eastmoney\.com/a/\d+\.html", url):
                url = source_ref
            out["items"].append({"title": title, "summary": summary,
                                 "publish_ts": str(row["date"]), "source_ref": url,
                                 "publisher": _text(row.get("mediaName"), 120),
                                 "match_method": "exact_code_mention", "issuer_fact": False,
                                 "judgment_required": True})
        out["items"].sort(key=lambda row: row["publish_ts"], reverse=True)
        out["as_of"] = max((row["publish_ts"] for row in out["items"]), default=None)
        out["status"] = "ok" if out["items"] else "empty"
        out["coverage"] = {"returned_rows": len(out["items"]), "rejected_rows": rejected,
                           "queried_page": 1, "requested_limit": limit,
                           "complete_news_coverage": False}
    except (requests.RequestException, ValueError, RuntimeError, TypeError) as exc:
        out["error"] = type(exc).__name__
    return out
