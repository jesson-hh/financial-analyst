"""Small, live strategic data feeds used by the workspace research agent.

This module deliberately has no dependency on Guanlan's stock store or on
``datafeed.live_client``.  Every network destination is selected from constants
in this file; callers can choose query parameters, but never a URL.

The public ``query_*`` functions are synchronous because console tools are
currently synchronous.  Parsers are kept separate so source-format fixtures can
be tested without a network connection.
"""

from __future__ import annotations

import csv
import hashlib
import html as html_lib
import io
import json
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone
from html.parser import HTMLParser
from typing import Any, Iterable, Mapping, Sequence
from urllib.parse import urljoin

import httpx


SZSE_CALENDAR_URL = "https://www.szse.cn/api/report/exchange/onepersistenthour/monthList"
SZSE_CALENDAR_PAGE = "https://www.szse.cn/aboutus/calendar/"
CNINFO_PERIODS_URL = "https://www.cninfo.com.cn/new/information/getSelectData"
CNINFO_DISCLOSURE_URL = "https://www.cninfo.com.cn/new/information/getPrbookInfo"
CNINFO_DISCLOSURE_PAGE = "https://www.cninfo.com.cn/new/commonUrl?url=data/yuyuepilu"
BSE_DISCLOSURE_URL = "https://www.bse.cn/appointmentDisController/getAppointmentDisList.do"
BSE_DISCLOSURE_PAGE = "https://www.bse.cn/disclosure/appointment_disclosure.html"
MOPS_REVENUE_URL = "https://mops.twse.com.tw/mops/api/t05st10_ifrs"
MOPS_HOME = "https://mops.twse.com.tw/"
TWSE_REVENUE_OPENAPI_URL = "https://openapi.twse.com.tw/v1/opendata/t187ap05_L"
TPEX_REVENUE_OPENAPI_O_URL = "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap05_O"
TPEX_REVENUE_OPENAPI_R_URL = "https://www.tpex.org.tw/openapi/v1/t187ap05_R"
TW_REVENUE_CSV_URLS = {
    "TWSE listed": "https://mopsfin.twse.com.tw/opendata/t187ap05_L.csv",
    "TPEx OTC": "https://mopsfin.twse.com.tw/opendata/t187ap05_O.csv",
    "TPEx emerging": "https://mopsfin.twse.com.tw/opendata/t187ap05_R.csv",
}
SINA_FUTURES_URL = "https://hq.sinajs.cn/list={symbols}"
SINA_FINANCE_HOME = "https://finance.sina.com.cn/"
BIS_ENTITY_URL = "https://media.bis.gov/sites/default/files/documents/entity-list.csv"
BIS_UNVERIFIED_URL = "https://media.bis.gov/sites/default/files/documents/unverified-list.csv"
BIS_MEU_URL = "https://media.bis.gov/sites/default/files/documents/military-end-user-list.csv"
# Official destination observed in the legacy Treasury endpoint's 302 on
# 2026-09-09.  This is the same OFAC SDN list, not an additional source/mirror.
OFAC_SDN_URL = "https://sanctionslistservice.ofac.treas.gov/api/publicationpreview/exports/sdn.csv"
BIS_FEDERAL_REGISTER_URL = "https://www.federalregister.gov/api/v1/documents.json"
MOFCOM_POLICY_URL = "https://aqygzj.mofcom.gov.cn/flzc/index.html"
MOFCOM_ANNOUNCEMENTS_URL = (
    f"https://www.mofcom.gov.cn/zcfb/blgg/gg/{date.today().year}/index.html"
)
CN_MARKET_ACCESS_NEGATIVE_LIST_URL = (
    "https://www.gov.cn/zhengce/zhengceku/202504/"
    "P020250424380902075818.pdf"
)

_UA = "Mozilla/5.0 (compatible; GuanlanResearch/2.0; +local-workspace-agent)"
_TIMEOUT = httpx.Timeout(20.0, connect=8.0)
_MAX_DATE_RANGE_DAYS = 370
_CACHE_TTL_SECONDS = 15 * 60
_cache: dict[str, tuple[float, bytes]] = {}
_cache_lock = threading.Lock()


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _envelope(dataset_id: str, query: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "ok": False,
        "dataset_id": dataset_id,
        "status": "unavailable",
        "query": dict(query),
        "rows": [],
        "sources": [],
        "pulled_at": _now_iso(),
        "as_of": "",
        "warnings": [],
    }


def _invalid(dataset_id: str, query: Mapping[str, Any], message: str) -> dict[str, Any]:
    out = _envelope(dataset_id, query)
    out["status"] = "invalid"
    out["warnings"].append(message)
    return out


def _source(
    authority: str,
    url: str,
    *,
    status: str = "ok",
    published_at: str = "",
    raw: bytes | None = None,
) -> dict[str, Any]:
    source: dict[str, Any] = {
        "authority": authority,
        "url": url,
        "published_at": published_at,
        "retrieved_at": _now_iso(),
        "status": status,
    }
    if raw is not None:
        source["raw_sha256"] = hashlib.sha256(raw).hexdigest()
        source["bytes"] = len(raw)
    return source


def _finish(out: dict[str, Any]) -> dict[str, Any]:
    rows = out["rows"]
    good_sources = sum(1 for item in out["sources"] if item.get("status") == "ok")
    failed_sources = sum(1 for item in out["sources"] if item.get("status") != "ok")
    if rows:
        out["ok"] = True
        out["status"] = "partial" if failed_sources else "ok"
    elif good_sources:
        out["ok"] = True
        # A zero-row result is only a trustworthy negative finding when every
        # required source completed.  If any source failed, preserve the
        # transport gap instead of turning incomplete coverage into "empty".
        out["status"] = "partial_empty" if failed_sources else "empty"
    else:
        out["ok"] = False
        out["status"] = "unavailable"
    return out


def _client() -> httpx.Client:
    return httpx.Client(timeout=_TIMEOUT, follow_redirects=True, headers={"User-Agent": _UA})


def _cached_get(url: str, *, headers: Mapping[str, str] | None = None) -> bytes:
    now = time.monotonic()
    with _cache_lock:
        hit = _cache.get(url)
        if hit and now - hit[0] <= _CACHE_TTL_SECONDS:
            return hit[1]
    with _client() as client:
        response = client.get(url, headers=dict(headers or {}))
        response.raise_for_status()
        body = response.content
    with _cache_lock:
        _cache[url] = (now, body)
    return body


def _parse_iso_date(value: str, field: str) -> date:
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} 必须是 YYYY-MM-DD") from exc


def _month_keys(start: date, end: date) -> list[str]:
    cursor = date(start.year, start.month, 1)
    stop = date(end.year, end.month, 1)
    result: list[str] = []
    while cursor <= stop:
        result.append(cursor.strftime("%Y-%m"))
        cursor = date(cursor.year + (cursor.month == 12), 1 if cursor.month == 12 else cursor.month + 1, 1)
    return result


def parse_szse_calendar_payload(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Normalize one SZSE ``monthList`` response."""

    data = payload.get("data")
    if not isinstance(data, list) or not data:
        raise ValueError("SZSE monthList 缺少 data")
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in data:
        if not isinstance(item, Mapping):
            raise ValueError("SZSE monthList 含非对象行")
        day = str(item.get("jyrq") or "").strip()
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day):
            raise ValueError("SZSE monthList 含无效日期")
        flag = str(item.get("jybz") or "")
        if flag not in {"0", "1"}:
            raise ValueError("SZSE monthList 的 jybz 不是 0/1")
        if day in seen:
            raise ValueError("SZSE monthList 含重复日期")
        seen.add(day)
        is_trading = flag == "1"
        rows.append(
            {
                "record_type": "trading_calendar",
                "date": day,
                "trading": is_trading,
                "reason": "scheduled_session" if is_trading else "exchange_closed",
                "market": "CN_A_SHARE",
                "exchange_authority": "SZSE",
            }
        )
    parsed_dates = sorted(date.fromisoformat(row["date"]) for row in rows)
    if len({(item.year, item.month) for item in parsed_dates}) != 1:
        raise ValueError("SZSE monthList 混入多个自然月")
    first = date(parsed_dates[0].year, parsed_dates[0].month, 1)
    next_month = date(first.year + (first.month == 12), 1 if first.month == 12 else first.month + 1, 1)
    expected = {first + timedelta(days=offset) for offset in range((next_month - first).days)}
    if set(parsed_dates) != expected:
        raise ValueError("SZSE monthList 自然日不完整")
    return rows


def _query_trading_calendar(start: date, end: date, out: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with _client() as client:
        for month in _month_keys(start, end):
            try:
                response = client.get(
                    SZSE_CALENDAR_URL,
                    params={"month": month},
                    headers={"Referer": SZSE_CALENDAR_PAGE},
                )
                response.raise_for_status()
                parsed = parse_szse_calendar_payload(response.json())
                if any(not row["date"].startswith(month) for row in parsed):
                    raise ValueError("SZSE monthList 返回月份与请求不一致")
                rows.extend(parsed)
                out["sources"].append(
                    _source("深圳证券交易所", str(response.url), raw=response.content)
                )
            except Exception as exc:  # one bad month must not poison good months
                out["sources"].append(
                    _source("深圳证券交易所", SZSE_CALENDAR_URL, status="error")
                )
                out["warnings"].append(f"深交所 {month} 交易日历不可用：{type(exc).__name__}")
    return [row for row in rows if start.isoformat() <= row["date"] <= end.isoformat()]


def parse_cninfo_disclosure_payload(payload: Mapping[str, Any], market: str) -> list[dict[str, Any]]:
    """Normalize CNInfo appointment rows."""

    rows: list[dict[str, Any]] = []
    for item in payload.get("prbookinfos") or []:
        if not isinstance(item, Mapping):
            continue
        rows.append(
            {
                "record_type": "disclosure_schedule",
                "code": str(item.get("seccode") or "").strip(),
                "name": str(item.get("secname") or "").strip(),
                "report_period": str(item.get("f001d_0102") or "").strip(),
                "first_scheduled_date": str(item.get("f002d_0102") or "").strip(),
                "first_change_date": str(item.get("f003d_0102") or "").strip(),
                "second_change_date": str(item.get("f004d_0102") or "").strip(),
                "third_change_date": str(item.get("f005d_0102") or "").strip(),
                "fourth_change_date": "",
                "fifth_change_date": "",
                "actual_date": str(item.get("f006d_0102") or "").strip(),
                "market": market,
                "org_id": str(item.get("orgId") or "").strip(),
                "source_updated_at": "",
                "revision_history_scope": "provider_current_record_up_to_three_changes",
            }
        )
    return rows


_BSE_REPORT_PERIODS = {
    "9503-1001": (12, 31),
    "9503-1003": (3, 31),
    "9503-1002": (6, 30),
    "9503-1004": (9, 30),
}


def _bse_report_selector(requested: str, *, today: date | None = None) -> tuple[str, str]:
    """Map the public calendar's report period to BSE's bounded form fields."""
    raw = str(requested or "").strip()
    if raw:
        period = _parse_iso_date(raw, "report_period")
        wanted = (period.month, period.day)
        for report_type, month_day in _BSE_REPORT_PERIODS.items():
            if wanted == month_day:
                return str(period.year), report_type
        raise ValueError("北交所 report_period 仅支持季末/半年末/年末日期")

    current = today or date.today()
    month_day = current.month * 100 + current.day
    if month_day <= 331:
        return str(current.year - 1), "9503-1001"
    if month_day <= 630:
        return str(current.year), "9503-1003"
    if month_day <= 930:
        return str(current.year), "9503-1002"
    return str(current.year), "9503-1004"


def _bse_report_period(report_year: Any, report_type: Any) -> str:
    year = str(report_year or "").strip()
    month_day = _BSE_REPORT_PERIODS.get(str(report_type or "").strip())
    if not re.fullmatch(r"\d{4}", year) or month_day is None:
        return ""
    return f"{year}-{month_day[0]:02d}-{month_day[1]:02d}"


def parse_bse_disclosure_payload(payload: Any) -> tuple[list[dict[str, Any]], int]:
    """Normalize one official BSE appointment response and its row count."""
    wrapper = payload[0] if isinstance(payload, list) and payload else {}
    result = wrapper.get("result") if isinstance(wrapper, Mapping) else {}
    if not isinstance(result, Mapping):
        raise ValueError("BSE appointment response 缺少 result")
    content = result.get("content")
    if not isinstance(content, list):
        raise ValueError("BSE appointment response 缺少 content")
    total = _number(result.get("totalElements"), integer=True)
    rows: list[dict[str, Any]] = []
    for item in content:
        if not isinstance(item, Mapping):
            continue
        code = str(item.get("stockCode") or "").strip()
        if not re.fullmatch(r"\d{6}", code):
            continue
        destination = str(item.get("destFilePath") or "").strip()
        rows.append({
            "record_type": "disclosure_schedule",
            "code": code,
            "name": str(item.get("stockName") or "").strip(),
            "report_period": _bse_report_period(
                item.get("reportYear"), item.get("reportType"),
            ),
            "report_type_name": str(item.get("reportTypeName") or "").strip(),
            "first_scheduled_date": str(item.get("appointmentDate") or "").strip(),
            "first_change_date": str(item.get("firstDate") or "").strip(),
            "second_change_date": str(item.get("secondDate") or "").strip(),
            "third_change_date": str(item.get("thirdDate") or "").strip(),
            "fourth_change_date": str(item.get("forthDate") or "").strip(),
            "fifth_change_date": str(item.get("fifthDate") or "").strip(),
            "latest_scheduled_date": str(item.get("latestDate") or "").strip(),
            "actual_date": str(item.get("publishDate") or "").strip(),
            "actual_document_url": urljoin(BSE_DISCLOSURE_PAGE, destination) if destination else "",
            "market": "bse",
            "org_id": "",
            "provider_record_id": str(item.get("id") or "").strip(),
            "source_updated_at": str(item.get("updateDate") or "").strip(),
            "revision_history_scope": "provider_current_record_up_to_five_changes",
        })
    return rows, max(0, int(total or 0))


def _parse_bse_jsonp(raw: bytes, callback: str) -> Any:
    text = raw.decode("utf-8-sig", errors="strict").strip()
    match = re.fullmatch(
        rf"{re.escape(callback)}\((.*)\)\s*;?", text, flags=re.DOTALL,
    )
    if not match:
        raise ValueError("BSE appointment response 不是预期 JSONP")
    return json.loads(match.group(1))


def _query_bse_disclosures(
    stock_code: str,
    report_period: str,
    limit: int,
    out: dict[str, Any],
) -> list[dict[str, Any]]:
    callback = "guanlanBseAppointment"
    try:
        report_year, report_type = _bse_report_selector(report_period)
        rows: list[dict[str, Any]] = []
        raw_parts: list[bytes] = []
        with _client() as client:
            page = 0
            total = limit
            while len(rows) < limit and page * 20 < max(total, 1):
                response = client.post(
                    BSE_DISCLOSURE_URL,
                    params={"callback": callback},
                    data={
                        "page": str(page),
                        "stockCode": stock_code,
                        "reportYear": report_year,
                        "reportType": report_type,
                        "startTime": "",
                        "endTime": "",
                        "sortfield": "bean.latest_date",
                        "sorttype": "asc",
                    },
                    headers={"Referer": BSE_DISCLOSURE_PAGE},
                )
                response.raise_for_status()
                raw_parts.append(response.content)
                page_rows, total = parse_bse_disclosure_payload(
                    _parse_bse_jsonp(response.content, callback),
                )
                rows.extend(page_rows)
                if not page_rows:
                    break
                page += 1
        out["sources"].append(
            _source("北京证券交易所", BSE_DISCLOSURE_URL, raw=b"\n".join(raw_parts))
        )
        return rows[:limit]
    except Exception as exc:
        out["sources"].append(
            _source("北京证券交易所", BSE_DISCLOSURE_URL, status="error")
        )
        out["warnings"].append(f"北交所披露预约暂不可用：{type(exc).__name__}")
        out.setdefault("reason_codes", []).append("bse_disclosure_transport_unavailable")
        return []


def _cninfo_period(client: httpx.Client, requested: str) -> str:
    response = client.post(
        CNINFO_PERIODS_URL,
        data={"rows": "8"},
        headers={"Referer": CNINFO_DISCLOSURE_PAGE},
    )
    response.raise_for_status()
    periods = response.json()
    if not isinstance(periods, list) or not periods:
        raise ValueError("巨潮资讯未返回报告期")
    requested = requested.strip()
    if not requested:
        return str(periods[0].get("value0") or "")
    for item in periods:
        if requested in {str(item.get("value0") or ""), str(item.get("value1") or "")}:
            return str(item.get("value0") or "")
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", requested):
        return requested
    raise ValueError("report_period 不是巨潮当前可选报告期")


def _query_disclosures(
    market: str,
    stock_code: str,
    report_period: str,
    limit: int,
    out: dict[str, Any],
) -> list[dict[str, Any]]:
    markets = (
        ["szmb", "szcn", "shmb", "shkcp"]
        if market == "all" else ([] if market == "bse" else [market])
    )
    rows: list[dict[str, Any]] = []
    if markets:
        try:
            with _client() as client:
                period = _cninfo_period(client, report_period)
                raw_parts: list[bytes] = []
                for market_code in markets:
                    response = client.post(
                        CNINFO_DISCLOSURE_URL,
                        data={
                            "sectionTime": period,
                            "firstTime": "",
                            "lastTime": "",
                            "market": market_code,
                            "stockCode": stock_code,
                            "isDesc": "false",
                        },
                        headers={"Referer": CNINFO_DISCLOSURE_PAGE},
                    )
                    response.raise_for_status()
                    raw_parts.append(response.content)
                    rows.extend(parse_cninfo_disclosure_payload(response.json(), market_code))
            out["sources"].append(
                _source(
                    "巨潮资讯（中国证监会指定信息披露网站）",
                    CNINFO_DISCLOSURE_URL,
                    raw=b"\n".join(raw_parts),
                )
            )
        except Exception as exc:
            out["sources"].append(
                _source("巨潮资讯（中国证监会指定信息披露网站）", CNINFO_DISCLOSURE_URL, status="error")
            )
            out["warnings"].append(f"披露预约暂不可用：{type(exc).__name__}")
    if market in {"all", "bse"}:
        rows.extend(_query_bse_disclosures(stock_code, report_period, limit, out))
    return rows[:limit]


def query_trade_disclosure_calendar(
    kind: str = "both",
    start: str = "",
    end: str = "",
    market: str = "all",
    stock_code: str = "",
    report_period: str = "",
    limit: int = 50,
) -> dict[str, Any]:
    """Query official mainland trading dates and/or disclosure appointments."""

    query = {
        "kind": kind,
        "start": start,
        "end": end,
        "market": market,
        "stock_code": stock_code,
        "report_period": report_period,
        "limit": limit,
    }
    out = _envelope("research.trade_disclosure_calendar.live", query)
    kind = str(kind).strip().lower()
    if kind == "trade":
        kind = "trading"
    if kind not in {"trading", "disclosure", "both"}:
        return _invalid(out["dataset_id"], query, "kind 必须是 trading、disclosure 或 both")
    if market not in {"all", "szmb", "szcn", "shmb", "shkcp", "bse"}:
        return _invalid(out["dataset_id"], query, "market 必须是 all/szmb/szcn/shmb/shkcp/bse")
    try:
        limit = max(1, min(int(limit), 100))
    except (TypeError, ValueError):
        return _invalid(out["dataset_id"], query, "limit 必须是整数")
    raw_code = str(stock_code or "").strip().upper()
    clean_code = re.sub(r"^(?:SH|SZ|BJ)[.:]?", "", raw_code)
    if clean_code and not re.fullmatch(r"\d{6}", clean_code):
        return _invalid(out["dataset_id"], query, "stock_code 必须是六位证券代码")
    today = date.today()
    try:
        start_date = _parse_iso_date(start, "start") if start else today
        end_date = _parse_iso_date(end, "end") if end else start_date + timedelta(days=30)
    except ValueError as exc:
        return _invalid(out["dataset_id"], query, str(exc))
    if end_date < start_date:
        return _invalid(out["dataset_id"], query, "end 不能早于 start")
    if (end_date - start_date).days > _MAX_DATE_RANGE_DAYS:
        return _invalid(out["dataset_id"], query, f"交易日历区间不能超过 {_MAX_DATE_RANGE_DAYS} 天")

    if kind in {"trading", "both"}:
        out["rows"].extend(_query_trading_calendar(start_date, end_date, out))
    if kind in {"disclosure", "both"}:
        out["rows"].extend(_query_disclosures(market, clean_code, report_period, limit, out))
        out.setdefault("reason_codes", []).append(
            "disclosure_revision_observation_history_unavailable"
        )
        out["warnings"].append(
            "预约行保留源端当前记录中的改期字段，但没有逐次抓取时点和不可变历史快照；"
            "不能据此声称拥有完整的改期修订历史。"
        )
    out["coverage"] = {
        "trading_calendar": {
            "requested": kind in {"trading", "both"},
            "authority": "SZSE" if kind in {"trading", "both"} else None,
            "scope": "single_exchange_official_calendar" if kind in {"trading", "both"} else "not_requested",
            "reason_code": (
                "cross_exchange_calendar_not_independently_attested"
                if kind in {"trading", "both"} else None
            ),
        },
        "disclosure_schedule": {
            "requested": kind in {"disclosure", "both"},
            "requested_market": market if kind in {"disclosure", "both"} else None,
            "authorities": (
                (["CNINFO", "BSE"] if market == "all" else
                 ["BSE"] if market == "bse" else ["CNINFO"])
                if kind in {"disclosure", "both"} else []
            ),
            "revision_fields": (
                [
                    "first_scheduled_date", "first_change_date", "second_change_date",
                    "third_change_date", "fourth_change_date", "fifth_change_date",
                    "actual_date", "source_updated_at",
                ] if kind in {"disclosure", "both"} else []
            ),
            "historical_observation_archive": False,
            "reason_code": (
                "disclosure_revision_observation_history_unavailable"
                if kind in {"disclosure", "both"} else None
            ),
        },
    }
    # ``limit`` is part of the public contract for every calendar kind.  Keep
    # the fetched date range intact in provenance, but cap the returned rows so
    # an agent asking for one record does not unexpectedly receive a full month.
    out["rows"] = out["rows"][:limit]
    # ``as_of`` is the observation time, not the latest event date.  A trading
    # calendar deliberately contains future trading days and a disclosure
    # calendar contains future scheduled appointments that are already public
    # today.  Promoting either event date to ``as_of`` makes valid calendar
    # evidence look like post-cutoff/look-ahead data in the company Evidence
    # Hub.  Event dates remain on each row; the envelope is anchored to the
    # retrieval timestamp.
    out["as_of"] = out["pulled_at"]
    return _finish(out)


def _number(value: Any, *, integer: bool = False) -> int | float | None:
    text = str(value or "").strip().replace(",", "")
    if not text or text in {"-", "--", "N/A", "null"}:
        return None
    try:
        return int(float(text)) if integer else float(text)
    except ValueError:
        return None


def parse_mops_monthly_revenue(payload: Mapping[str, Any], company_id: str) -> dict[str, Any] | None:
    """Normalize a MOPS t05st10_ifrs result (amount unit: thousand TWD)."""

    result = payload.get("result")
    if not isinstance(result, Mapping):
        return None
    pairs = result.get("data") or []
    values = [item[1] if isinstance(item, (list, tuple)) and len(item) > 1 else "" for item in pairs]
    if len(values) < 8:
        return None
    yymm = str(result.get("yymm") or "")
    period = ""
    if re.fullmatch(r"\d{5,6}", yymm):
        roc_year, month = int(yymm[:-2]), int(yymm[-2:])
        period = f"{roc_year + 1911:04d}-{month:02d}"
    return {
        "record_type": "tw_monthly_revenue",
        "company_id": company_id,
        "company_name": str(result.get("companyAbbreviation") or "").strip(),
        "market": str(result.get("marketKindName") or "").strip(),
        "period": period,
        "revenue_thousand_twd": _number(values[0], integer=True),
        "prior_year_revenue_thousand_twd": _number(values[1], integer=True),
        "monthly_change_thousand_twd": _number(values[2], integer=True),
        "monthly_yoy_pct": _number(values[3]),
        "ytd_revenue_thousand_twd": _number(values[4], integer=True),
        "prior_ytd_revenue_thousand_twd": _number(values[5], integer=True),
        "ytd_change_thousand_twd": _number(values[6], integer=True),
        "ytd_yoy_pct": _number(values[7]),
        "note": str(values[8] if len(values) > 8 else "").strip(),
        "source_datetime": str(payload.get("datetime") or "").strip(),
        "unit": "thousand_TWD",
    }


def _roc_period(value: Any) -> str:
    text = str(value or "").strip()
    if not re.fullmatch(r"\d{5,6}", text):
        return ""
    roc_year, month = int(text[:-2]), int(text[-2:])
    if not 1 <= month <= 12:
        return ""
    return f"{roc_year + 1911:04d}-{month:02d}"


def parse_tw_revenue_openapi_rows(
    payload: Any,
    market: str,
    company_ids: Iterable[str] = (),
) -> list[dict[str, Any]]:
    """Normalize TWSE/TPEx latest full-market monthly-revenue rows."""

    wanted = {str(item).strip() for item in company_ids}
    rows: list[dict[str, Any]] = []
    if not isinstance(payload, list):
        return rows
    for item in payload:
        if not isinstance(item, Mapping):
            continue
        company_id = str(item.get("公司代號") or "").strip()
        if not company_id or (wanted and company_id not in wanted):
            continue
        rows.append(
            {
                "record_type": "tw_monthly_revenue",
                "company_id": company_id,
                "company_name": str(item.get("公司名稱") or "").strip(),
                "market": market,
                "industry": str(item.get("產業別") or "").strip(),
                "period": _roc_period(item.get("資料年月")),
                "revenue_thousand_twd": _number(item.get("營業收入-當月營收"), integer=True),
                "prior_month_revenue_thousand_twd": _number(
                    item.get("營業收入-上月營收"), integer=True
                ),
                "prior_year_revenue_thousand_twd": _number(
                    item.get("營業收入-去年當月營收"), integer=True
                ),
                "monthly_mom_pct": _number(item.get("營業收入-上月比較增減(%)")),
                "monthly_yoy_pct": _number(item.get("營業收入-去年同月增減(%)")),
                "ytd_revenue_thousand_twd": _number(
                    item.get("累計營業收入-當月累計營收"), integer=True
                ),
                "prior_ytd_revenue_thousand_twd": _number(
                    item.get("累計營業收入-去年累計營收"), integer=True
                ),
                "ytd_yoy_pct": _number(item.get("累計營業收入-前期比較增減(%)")),
                "note": str(item.get("備註") or "").strip(),
                "source_datetime": str(item.get("出表日期") or "").strip(),
                "unit": "thousand_TWD",
            }
        )
    return rows


def _normalize_company_ids(company_ids: Sequence[str] | str, limit: int) -> list[str]:
    candidates = [company_ids] if isinstance(company_ids, str) else list(company_ids or [])
    result: list[str] = []
    for item in candidates:
        company_id = str(item).strip()
        if not re.fullmatch(r"[0-9A-Z]{4,8}", company_id):
            raise ValueError(f"无效台湾公司代码：{company_id!r}")
        if company_id not in result:
            result.append(company_id)
    if not result:
        raise ValueError("company_ids 不能为空")
    return result[:limit]


def parse_tw_revenue_csv(body: bytes, market: str, company_ids: Iterable[str] = ()) -> list[dict[str, Any]]:
    """Parse the official MOPS UTF-8 CSV fallback; reject HTML/error payloads."""
    reader = csv.DictReader(io.StringIO(body.decode("utf-8-sig")))
    required = {"公司代號", "資料年月", "營業收入-當月營收"}
    if not required <= set(reader.fieldnames or []):
        raise ValueError("MOPS monthly revenue CSV header changed or response is not CSV")
    return parse_tw_revenue_openapi_rows(list(reader), market, company_ids)


def query_tw_monthly_revenue(
    company_ids: Sequence[str] | str,
    year: str | int = "",
    month: str | int = "",
    limit: int = 20,
) -> dict[str, Any]:
    """Query Taiwan monthly revenue, preferring TWSE/TPEx OpenAPI with MOPS fallback."""

    query = {"company_ids": company_ids, "year": year, "month": month, "limit": limit}
    out = _envelope("research.tw_monthly_revenue.live", query)
    try:
        limit = max(1, min(int(limit), 20))
        ids = _normalize_company_ids(company_ids, limit)
    except (TypeError, ValueError) as exc:
        return _invalid(out["dataset_id"], query, str(exc))
    exact = str(year).strip() or str(month).strip()
    if bool(str(year).strip()) != bool(str(month).strip()):
        return _invalid(out["dataset_id"], query, "year 与 month 必须同时提供，或同时留空查询最新")
    roc_year = ""
    month_text = ""
    if exact:
        try:
            year_num = int(str(year))
            month_num = int(str(month))
        except ValueError:
            return _invalid(out["dataset_id"], query, "year/month 必须是整数")
        if not 1 <= month_num <= 12:
            return _invalid(out["dataset_id"], query, "month 必须在 1 到 12 之间")
        if year_num >= 1912:
            year_num -= 1911
        if not 1 <= year_num <= 999:
            return _invalid(out["dataset_id"], query, "year 必须是民国年或公历年")
        roc_year, month_text = str(year_num), str(month_num)

    # Latest queries prefer the full-market OpenAPIs.  Besides being cheaper for
    # baskets, these expose industry and month-on-month fields absent from the
    # single-company MOPS response.  Exact historical periods fall through to
    # MOPS because the OpenAPIs publish only the newest available month.
    requested_period = f"{int(roc_year) + 1911:04d}-{int(month_text):02d}" if exact else ""
    openapi_specs = (
        ("TWSE listed", TWSE_REVENUE_OPENAPI_URL, "台湾证券交易所 OpenAPI"),
        ("TPEx OTC", TPEX_REVENUE_OPENAPI_O_URL, "证券柜台买卖中心 OpenAPI"),
        ("TPEx emerging", TPEX_REVENUE_OPENAPI_R_URL, "证券柜台买卖中心 OpenAPI"),
    )

    def fetch_openapi(spec: tuple[str, str, str]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
        market_name, url, authority = spec
        sources: list[dict[str, Any]] = []
        warnings: list[str] = []
        with _client() as client:
            try:
                response = client.get(url, headers={"Accept": "application/json"})
                response.raise_for_status()
                payload = response.json()
                if not isinstance(payload, list) or (payload and not all(
                    isinstance(item, Mapping) and "公司代號" in item and "資料年月" in item
                    for item in payload
                )):
                    raise ValueError("monthly revenue API returned an unexpected schema")
                parsed = parse_tw_revenue_openapi_rows(payload, market_name, ids)
                source = _source(authority, url, raw=response.content)
                sources.append(source)
                for row in parsed:
                    row.update({"source_url": url, "source_format": "official_openapi"})
                return parsed, sources, warnings
            except Exception as exc:
                failure = (f"http_{exc.response.status_code}" if isinstance(exc, httpx.HTTPStatusError)
                           else type(exc).__name__)
                sources.append(_source(authority, url, status=failure))
                warnings.append(f"{market_name} OpenAPI 暂不可用（{failure}）；尝试官方 MOPS CSV 备源。")
            csv_url = TW_REVENUE_CSV_URLS[market_name]
            try:
                response = client.get(csv_url, headers={"Accept": "text/csv"})
                response.raise_for_status()
                if len(response.content) > 4 * 1024 * 1024:
                    raise ValueError("monthly revenue CSV exceeds byte limit")
                parsed = parse_tw_revenue_csv(response.content, market_name, ids)
                sources.append(_source("台湾公开资讯观测站 MOPS 官方 CSV", csv_url, raw=response.content))
                for row in parsed:
                    row.update({"source_url": csv_url, "source_format": "official_csv_fallback"})
                return parsed, sources, warnings
            except Exception as exc:
                failure = (f"http_{exc.response.status_code}" if isinstance(exc, httpx.HTTPStatusError)
                           else type(exc).__name__)
                sources.append(_source("台湾公开资讯观测站 MOPS 官方 CSV", csv_url, status=failure))
                warnings.append(f"{market_name} 官方 CSV 备源暂不可用：{failure}")
                return [], sources, warnings

    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = {pool.submit(fetch_openapi, spec): spec for spec in openapi_specs}
        for future in as_completed(futures):
            market_name, url, authority = futures[future]
            try:
                parsed_rows, sources, warnings = future.result()
                if requested_period:
                    parsed_rows = [row for row in parsed_rows if row["period"] == requested_period]
                out["rows"].extend(parsed_rows)
                out["sources"].extend(sources)
                out["warnings"].extend(warnings)
            except Exception as exc:
                out["sources"].append(_source(authority, url, status="error"))
                out["warnings"].append(f"{market_name} 月营收 OpenAPI 暂不可用：{type(exc).__name__}")

    found_ids = {row["company_id"] for row in out["rows"]}
    fallback_ids = [company_id for company_id in ids if company_id not in found_ids]
    if fallback_ids:
        fallback_raw: list[bytes] = []
        fallback_failures = 0

        def fetch_mops(company_id: str) -> tuple[str, dict[str, Any] | None, bytes, str]:
            with _client() as client:
                response = client.post(
                    MOPS_REVENUE_URL,
                    json={
                        "companyId": company_id,
                        "dataType": "2" if exact else "1",
                        "month": month_text,
                        "year": roc_year,
                        "subsidiaryCompanyId": "",
                    },
                    headers={"Origin": MOPS_HOME.rstrip("/"), "Referer": MOPS_HOME},
                )
                response.raise_for_status()
                payload = response.json()
                parsed = parse_mops_monthly_revenue(payload, company_id)
                if parsed and requested_period and parsed.get("period") != requested_period:
                    raise ValueError("MOPS returned a different revenue month")
                if parsed:
                    parsed.update({"source_url": MOPS_REVENUE_URL, "source_format": "official_company_api"})
                message = "" if parsed else str(payload.get("message") or "未返回月营收数据")
                return company_id, parsed, response.content, message

        with ThreadPoolExecutor(max_workers=min(5, len(fallback_ids))) as pool:
            futures = {pool.submit(fetch_mops, company_id): company_id for company_id in fallback_ids}
            for future in as_completed(futures):
                company_id = futures[future]
                try:
                    _company_id, parsed, raw, message = future.result()
                    fallback_raw.append(raw)
                    if parsed:
                        out["rows"].append(parsed)
                    else:
                        out["warnings"].append(f"{company_id}: {message}")
                except Exception as exc:
                    fallback_failures += 1
                    out["warnings"].append(
                        f"{company_id}: MOPS 后备接口暂不可用（{type(exc).__name__}）"
                    )
        out["sources"].append(
            _source(
                "台湾公开资讯观测站（MOPS）",
                MOPS_REVENUE_URL,
                status=("partial" if fallback_raw and fallback_failures else
                        "ok" if fallback_raw else "error"),
                raw=b"\n".join(fallback_raw) if fallback_raw else None,
            )
        )
    # A company belongs to exactly one market; make the result stable despite
    # concurrent source completion order.
    out["rows"].sort(key=lambda row: ids.index(row["company_id"]))
    out["as_of"] = max((row["period"] for row in out["rows"]), default="")
    missing = [company_id for company_id in ids if company_id not in {row["company_id"] for row in out["rows"]}]
    out["coverage"] = {"requested": len(ids), "returned": len(ids) - len(missing), "missing_company_ids": missing}
    out["warnings"].append("月营收接口按需查询，数据期为 period；最新汇总表可能尚未包含个别公司的更新申报，查询时间不代表营收月份。")
    return _finish(out)


_FUTURES_SYMBOL = re.compile(r"^[A-Z]{1,3}(?:0|\d{3,4})$")
_DEFAULT_FUTURES = ["RB0", "CU0", "AL0", "AU0", "AG0", "SC0", "M0", "P0", "I0", "JM0"]


def parse_sina_futures_text(text: str, requested_symbols: Iterable[str] = ()) -> list[dict[str, Any]]:
    """Parse Sina ``nf_*`` quote lines without assigning fetch time to trades."""

    wanted = {str(item).upper() for item in requested_symbols}
    rows: list[dict[str, Any]] = []
    pattern = re.compile(r'var\s+hq_str_nf_([A-Z0-9]+)="([^"]*)"')
    for match in pattern.finditer(text):
        symbol = match.group(1).upper()
        if wanted and symbol not in wanted:
            continue
        fields = match.group(2).split(",")
        if len(fields) < 18 or not fields[0]:
            continue
        raw_time = fields[1].strip()
        display_time = raw_time
        if re.fullmatch(r"\d{6}", raw_time):
            display_time = f"{raw_time[:2]}:{raw_time[2:4]}:{raw_time[4:]}"
        source_date = fields[17].strip()
        rows.append(
            {
                "record_type": "cn_commodity_futures_quote",
                "symbol": symbol,
                "name": fields[0].strip(),
                "open": _number(fields[2]),
                "high": _number(fields[3]),
                "low": _number(fields[4]),
                "bid": _number(fields[6]),
                "ask": _number(fields[7]),
                "last": _number(fields[8]),
                "previous_settlement": _number(fields[10]),
                "bid_volume": _number(fields[11], integer=True),
                "ask_volume": _number(fields[12], integer=True),
                # Sina stockhq.js prepends the symbol before its view consumes
                # amount=arr[14], volume=arr[15]; raw feed indices are 13/14.
                "volume": _number(fields[14], integer=True),
                "open_interest": _number(fields[13], integer=True),
                "exchange_marker": fields[15].strip(),
                "product": fields[16].strip(),
                "source_trade_date": source_date,
                "source_trade_time": display_time,
                "source_trade_ts_raw": f"{source_date} {raw_time}".strip(),
                "source_status": fields[18].strip() if len(fields) > 18 else "",
                "freshness_label": "latest_available_from_source",
                "observation_kind": "vendor_latest_quote",
                "source_is_official": False,
            }
        )
    return rows


def query_cn_commodity_futures(
    symbols: Sequence[str] | str | None = None,
    limit: int = 30,
) -> dict[str, Any]:
    """Query vendor latest quotes and official daily reports as distinct layers."""

    query = {"symbols": symbols, "limit": limit}
    out = _envelope("research.cn_commodity_futures.live", query)
    try:
        limit = max(1, min(int(limit), 30))
    except (TypeError, ValueError):
        return _invalid(out["dataset_id"], query, "limit 必须是整数")
    candidates = _DEFAULT_FUTURES if symbols is None else ([symbols] if isinstance(symbols, str) else list(symbols))
    normalized: list[str] = []
    for item in candidates:
        symbol = str(item).strip().upper()
        if symbol.startswith("NF_"):
            symbol = symbol[3:]
        if not _FUTURES_SYMBOL.fullmatch(symbol):
            return _invalid(out["dataset_id"], query, f"无效期货代码：{symbol!r}")
        if symbol not in normalized:
            normalized.append(symbol)
    if not normalized:
        return _invalid(out["dataset_id"], query, "symbols 不能为空")
    normalized = normalized[:limit]
    from guanlan_v2.datafeed.official_futures_live import query_official_futures_daily

    official = query_official_futures_daily(normalized)
    official_by_symbol = {row["symbol"]: row for row in official["rows"]}
    out["official_daily_rows"] = official["rows"]
    out["official_daily_attempts"] = official["attempts"]
    out["sources"].extend(official["sources"])
    url = SINA_FUTURES_URL.format(symbols=",".join(f"nf_{item}" for item in normalized))
    try:
        with _client() as client:
            response = client.get(url, headers={"Referer": SINA_FINANCE_HOME})
            response.raise_for_status()
            text = response.content.decode("gb18030", errors="replace")
        out["rows"] = parse_sina_futures_text(text, normalized)
        for row in out["rows"]:
            row["source_url"] = url
            if row["symbol"] in official_by_symbol:
                row["official_daily"] = official_by_symbol[row["symbol"]]
        out["sources"].append(
            _source("新浪财经公开行情（非交易所官方）", url, raw=response.content)
        )
    except Exception as exc:
        out["sources"].append(_source("新浪财经公开行情（非交易所官方）", url, status="error"))
        out["warnings"].append(f"商品期货批量行情暂不可用：{type(exc).__name__}")
    quoted = {row["symbol"] for row in out["rows"]}
    for symbol in normalized:
        if symbol not in quoted and symbol in official_by_symbol:
            fallback = dict(official_by_symbol[symbol])
            fallback["fallback_reason"] = "vendor_quote_unavailable"
            out["rows"].append(fallback)
    out["rows"].sort(key=lambda row: normalized.index(row["symbol"]))
    out["coverage"] = {
        "requested": len(normalized), "vendor_quote_symbols": sorted(quoted),
        "official_daily": official["coverage"],
    }
    missing = sorted(set(normalized) - {row["symbol"] for row in out["rows"]})
    if missing:
        out["warnings"].append("数据源未返回：" + ", ".join(missing))
    out["warnings"].append("这是公开源的最新可得快照；源内交易时间才是行情时间，pulled_at 仅代表查询时间。")
    out["warnings"].append("official_daily 为交易所日行情，含结算价/成交量/持仓量，不能当实时价格；连续代码按当日最大持仓量选取真实合约，不保证等于新浪连续主力。")
    no_official = [symbol for symbol, item in official["coverage"].items() if item["status"] != "ok"]
    if no_official:
        out["warnings"].append("官方日行情未覆盖或未返回：" + ", ".join(no_official) + "；详见 coverage，不代表这些合约没有交易。")
    unencrypted_official = any(row.get("transport_security") == "unencrypted_http" for row in official["rows"])
    if unencrypted_official:
        out["warnings"].append("广期所已接官网日行情，但目前公开通路为未加密 HTTP，传输真实性保障较 HTTPS 弱；保留来源和原始摘要，供研究核验。")
    out["as_of"] = max((row["source_trade_ts_raw"] for row in out["rows"]), default="")
    _finish(out)
    if (no_official or set(normalized) - quoted or unencrypted_official) and out["rows"]:
        out["status"] = "partial"
    return out


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        value = re.sub(r"\s+", " ", data).strip()
        if value:
            self.parts.append(value)


def _clean_html(value: str) -> str:
    parser = _TextExtractor()
    parser.feed(value)
    return " ".join(parser.parts)


def parse_mofcom_items(html: str, base_url: str, *, query: str = "", since: str = "") -> list[dict[str, Any]]:
    """Extract dated MOFCOM list/announcement links from list pages."""

    rows: list[dict[str, Any]] = []
    query_fold = query.casefold().strip()
    blocks = re.findall(r"<li\b[^>]*>(.*?)</li>", html, flags=re.I | re.S)
    for block in blocks:
        link = re.search(r"<a\b([^>]*)>(.*?)</a>", block, flags=re.I | re.S)
        if not link:
            continue
        attrs, body = link.groups()
        href_match = re.search(r"href\s*=\s*['\"]([^'\"]+)", attrs, flags=re.I)
        title_match = re.search(r"title\s*=\s*['\"]([^'\"]+)", attrs, flags=re.I)
        title = html_lib.unescape(title_match.group(1) if title_match else _clean_html(body)).strip()
        date_match = re.search(r"(20\d{2}-\d{2}-\d{2})", block)
        published_at = date_match.group(1) if date_match else ""
        if since and (not published_at or published_at < since):
            continue
        if query_fold and query_fold not in title.casefold():
            continue
        href = urljoin(base_url, href_match.group(1)) if href_match else base_url
        rows.append(
            {
                "record_type": "control_policy_update",
                "jurisdiction": "CN",
                "list_type": "export_control_or_restrictive_measure",
                "name": title,
                "published_at": published_at,
                "url": href,
                "source_list": "MOFCOM policy/announcement",
            }
        )
    return rows


def _decode_web_page(body: bytes) -> str:
    for encoding in ("utf-8-sig", "gb18030"):
        try:
            return body.decode(encoding)
        except UnicodeDecodeError:
            continue
    return body.decode("utf-8", errors="replace")


def _parse_bis_csv(body: bytes, source_list: str, query: str, since: str) -> list[dict[str, Any]]:
    reader = csv.DictReader(io.StringIO(body.decode("utf-8-sig", errors="replace")))
    normalized_fields = [name.strip().casefold() for name in (reader.fieldnames or [])]
    if "name" not in normalized_fields or len(set(normalized_fields)) != len(normalized_fields):
        raise ValueError("control_list_csv_schema_invalid")
    needle = query.casefold()
    rows: list[dict[str, Any]] = []
    for original in reader:
        # BIS UVL uses COUNTRY,NAME,ADDRESS while Entity/MEU use title case.
        # Normalize known column labels only; never guess a missing name/value.
        item = {str(key).strip().casefold(): value for key, value in original.items() if key is not None}
        if None in original or not str(item.get("name") or "").strip():
            raise ValueError("control_list_csv_record_invalid")
        haystack = " | ".join(str(value or "") for value in item.values())
        if needle and needle not in haystack.casefold():
            continue
        effective = str(item.get("effective date") or "").strip()
        if since and re.fullmatch(r"\d{4}-\d{2}-\d{2}", effective) and effective < since:
            continue
        rows.append(
            {
                "record_type": "control_list_match",
                "jurisdiction": "US",
                "list_type": "export_control",
                "source_list": source_list,
                "name": str(item.get("name") or "").strip(),
                "alternate_name": str(item.get("alternate name") or "").strip(),
                "country": str(item.get("country") or "").strip(),
                "address": str(item.get("address") or "").strip(),
                "programs": str(item.get("programs") or "").strip(),
                "effective_date": effective,
                "federal_register_notice": str(item.get("federal register notice") or "").strip(),
                "entity_number": str(item.get("entity number") or "").strip(),
                "source_url": str(item.get("web link") or "").strip(),
            }
        )
    return rows


def _parse_ofac_csv(body: bytes, query: str) -> list[dict[str, Any]]:
    needle = query.casefold()
    rows: list[dict[str, Any]] = []
    valid_records = 0
    for fields in csv.reader(io.StringIO(body.decode("utf-8-sig", errors="replace"))):
        if len(fields) < 12 or not fields[0].strip().isdigit() or not fields[1].strip():
            if any(value.strip() for value in fields):
                raise ValueError("control_list_csv_schema_invalid")
            continue
        valid_records += 1
        if needle and needle not in " | ".join(fields).casefold():
            continue
        rows.append(
            {
                "record_type": "control_list_match",
                "jurisdiction": "US",
                "list_type": "sanctions",
                "source_list": "OFAC SDN",
                "entity_number": fields[0].strip(),
                "name": fields[1].strip(),
                "entity_type": fields[2].strip(),
                "programs": fields[3].strip(),
                "title": fields[4].strip() if len(fields) > 4 else "",
                "remarks": fields[11].strip() if len(fields) > 11 else "",
            }
        )
    if not valid_records:
        raise ValueError("control_list_csv_no_valid_records")
    return rows


def parse_bis_federal_register_payload(
    payload: Mapping[str, Any], *, query: str = "", since: str = "",
) -> list[dict[str, Any]]:
    """Normalize recent BIS rule/notices from the Federal Register API."""
    needle = query.casefold().strip()
    rows: list[dict[str, Any]] = []
    for item in payload.get("results") or []:
        if not isinstance(item, Mapping):
            continue
        title = str(item.get("title") or "").strip()
        published_at = str(item.get("publication_date") or "").strip()
        if since and (not published_at or published_at < since):
            continue
        if needle and needle not in title.casefold():
            continue
        rows.append({
            "record_type": "control_policy_update",
            "jurisdiction": "US",
            "list_type": "export_control",
            "source_list": "BIS Federal Register",
            "name": title,
            "published_at": published_at,
            "document_number": str(item.get("document_number") or "").strip(),
            "document_type": str(item.get("type") or "").strip(),
            "url": str(item.get("html_url") or item.get("pdf_url") or "").strip(),
            "pdf_url": str(item.get("pdf_url") or "").strip(),
        })
    return rows


def _normalize_choices(value: Sequence[str] | str | None, default: set[str]) -> set[str]:
    if value is None:
        return set(default)
    values = [value] if isinstance(value, str) else list(value)
    result = {str(item).strip().lower() for item in values if str(item).strip()}
    if not result or "all" in result:
        return set(default)
    return result


def _control_cached_get(url: str) -> bytes:
    """One bounded normal GET; access denial/redirect is not retried or bypassed."""
    now = time.monotonic()
    with _cache_lock:
        hit = _cache.get(url)
        if hit and now - hit[0] <= _CACHE_TTL_SECONDS:
            return hit[1]
    with _client() as client:
        response = client.get(url, follow_redirects=False)
        response.raise_for_status()
        body = response.content
    with _cache_lock:
        _cache[url] = (now, body)
    return body


def query_control_lists(
    mode: str = "search",
    query: str = "",
    jurisdictions: Sequence[str] | str | None = None,
    list_types: Sequence[str] | str | None = None,
    since: str = "",
    limit: int = 50,
) -> dict[str, Any]:
    """Search selected official US lists and recent official China updates.

    This is a research aid, not a compliance determination.  It intentionally
    does not fetch the large consolidated screening list (CSL).
    """

    request_query = {
        "mode": mode,
        "query": query,
        "jurisdictions": jurisdictions,
        "list_types": list_types,
        "since": since,
        "limit": limit,
    }
    out = _envelope("research.control_lists.live", request_query)
    mode = str(mode).strip().lower()
    if mode not in {"search", "recent_changes"}:
        return _invalid(out["dataset_id"], request_query, "mode 必须是 search 或 recent_changes")
    try:
        limit = max(1, min(int(limit), 50))
    except (TypeError, ValueError):
        return _invalid(out["dataset_id"], request_query, "limit 必须是整数")
    if len(str(query)) > 200:
        return _invalid(out["dataset_id"], request_query, "query 不能超过 200 个字符")
    if since:
        try:
            _parse_iso_date(since, "since")
        except ValueError as exc:
            return _invalid(out["dataset_id"], request_query, str(exc))
    jurisdictions_set = _normalize_choices(jurisdictions, {"cn", "us"})
    types_set = _normalize_choices(list_types, {"export_control", "sanctions", "market_access"})
    if not jurisdictions_set <= {"cn", "us"}:
        return _invalid(out["dataset_id"], request_query, "jurisdictions 仅支持 cn/us/all")
    if not types_set <= {"export_control", "sanctions", "market_access"}:
        return _invalid(
            out["dataset_id"], request_query, "list_types 仅支持 export_control/sanctions/market_access/all"
        )

    query_text = str(query).strip()

    def failed_source(authority: str, url: str, exc: Exception) -> dict[str, Any]:
        source = _source(authority, url, status="error")
        status = getattr(getattr(exc, "response", None), "status_code", None)
        if isinstance(exc, httpx.HTTPStatusError):
            reason = (
                "access_denied" if status in {401, 403} else
                "redirect_not_followed" if status is not None and 300 <= status < 400 else
                "http_error"
            )
        elif isinstance(exc, httpx.TimeoutException):
            reason = "network_timeout"
        elif isinstance(exc, httpx.TransportError):
            reason = "network_error"
        elif isinstance(exc, (ValueError, csv.Error)):
            reason = "invalid_source_format"
        else:
            reason = "source_error"
        source.update(error_kind=reason, error_type=type(exc).__name__)
        if isinstance(status, int):
            source["http_status"] = status
        return source

    if "cn" in jurisdictions_set and types_set & {"export_control", "sanctions"}:
        for url in (MOFCOM_ANNOUNCEMENTS_URL, MOFCOM_POLICY_URL):
            try:
                body = _control_cached_get(url)
                text = _decode_web_page(body)
                out["rows"].extend(parse_mofcom_items(text, url, query=query_text, since=since))
                out["sources"].append(_source("中华人民共和国商务部", url, raw=body))
            except Exception as exc:
                out["sources"].append(failed_source("中华人民共和国商务部", url, exc))
                out["warnings"].append(f"商务部页面暂不可用：{type(exc).__name__}")
    if "cn" in jurisdictions_set and "market_access" in types_set:
        out["sources"].append(
            _source("中华人民共和国中央人民政府（市场准入负面清单入口）", CN_MARKET_ACCESS_NEGATIVE_LIST_URL,
                    status="reference_only")
        )
        out["warnings"].append("市场准入负面清单首版只提供官方原文入口，尚未逐条结构化。")

    if "us" in jurisdictions_set:
        if "market_access" in types_set:
            out["warnings"].append("美国市场准入规则首版未结构化；当前仅覆盖 BIS/OFAC 与 BIS 变更流。")
        if mode == "recent_changes":
            if "export_control" in types_set:
                try:
                    with _client() as client:
                        response = client.get(
                            BIS_FEDERAL_REGISTER_URL,
                            params={
                                "per_page": min(limit, 100),
                                "order": "newest",
                                "conditions[agencies][]": "industry-and-security-bureau",
                            },
                            headers={"Accept": "application/json"},
                            follow_redirects=False,
                        )
                        response.raise_for_status()
                        payload = response.json()
                    out["rows"].extend(parse_bis_federal_register_payload(
                        payload, query=query_text, since=since,
                    ))
                    out["sources"].append(
                        _source(
                            "U.S. Federal Register / Bureau of Industry and Security",
                            str(response.url),
                            raw=response.content,
                        )
                    )
                except Exception as exc:
                    out["sources"].append(
                        failed_source(
                            "U.S. Federal Register / Bureau of Industry and Security",
                            BIS_FEDERAL_REGISTER_URL,
                            exc,
                        )
                    )
                    out["warnings"].append(
                        f"BIS Federal Register 变更流暂不可用：{type(exc).__name__}"
                    )
            if "sanctions" in types_set:
                out["warnings"].append("美国制裁近期变更流首版未结构化；可切换 search 按实体查询 OFAC。")
        elif not query_text:
            out["warnings"].append("未提供 query，已跳过多 MB 的美国名单下载；请按实体名称搜索。")
        else:
            if "export_control" in types_set:
                for name, url in (
                    ("BIS Entity List", BIS_ENTITY_URL),
                    ("BIS Unverified List", BIS_UNVERIFIED_URL),
                    ("BIS Military End User List", BIS_MEU_URL),
                ):
                    try:
                        body = _control_cached_get(url)
                        out["rows"].extend(_parse_bis_csv(body, name, query_text, since))
                        out["sources"].append(
                            _source("U.S. Bureau of Industry and Security", url, raw=body)
                        )
                    except Exception as exc:
                        out["sources"].append(
                            failed_source("U.S. Bureau of Industry and Security", url, exc)
                        )
                        out["warnings"].append(f"{name} 暂不可用：{type(exc).__name__}")
            if "sanctions" in types_set:
                try:
                    body = _control_cached_get(OFAC_SDN_URL)
                    out["rows"].extend(_parse_ofac_csv(body, query_text))
                    out["sources"].append(_source("U.S. Treasury OFAC", OFAC_SDN_URL, raw=body))
                except Exception as exc:
                    out["sources"].append(failed_source("U.S. Treasury OFAC", OFAC_SDN_URL, exc))
                    out["warnings"].append(f"OFAC SDN 暂不可用：{type(exc).__name__}")

    if query_text:
        needle = query_text.casefold()
        out["rows"] = [
            row
            for row in out["rows"]
            if needle in json.dumps(row, ensure_ascii=False, sort_keys=True).casefold()
        ]
    out["rows"].sort(
        key=lambda row: str(row.get("published_at") or row.get("effective_date") or ""), reverse=True
    )
    out["rows"] = out["rows"][:limit]
    out["as_of"] = max(
        (str(row.get("published_at") or row.get("effective_date") or "") for row in out["rows"]),
        default=out["pulled_at"],
    )
    out["warnings"].append(
        "名单匹配仅供研究线索，可能存在别名、所有权规则和同名误报；不是法律意见或完整合规筛查。"
    )
    good = sum(source.get("status") == "ok" for source in out["sources"])
    out["coverage"] = {
        "jurisdictions_requested": sorted(jurisdictions_set),
        "list_types_requested": sorted(types_set),
        "sources_checked": sum(source.get("status") != "reference_only" for source in out["sources"]),
        "sources_succeeded": good,
        "sources_failed": sum(source.get("status") == "error" for source in out["sources"]),
        "reference_only": [source["url"] for source in out["sources"]
                           if source.get("status") == "reference_only"],
        "source_checks": [{key: source[key] for key in (
            "authority", "url", "status", "error_kind", "error_type", "http_status",
        ) if key in source} for source in out["sources"]],
        "matching": "literal_substring_in_selected_source_records",
        "alias_expansion": False,
        "ownership_rules_checked": False,
        "screening_complete": False,
        "zero_rows_means_no_risk": False,
        "limitations": [
            "CN sources are recent policy pages, not complete entity/ownership lists",
            "Market-access rules are not structurally screened",
            "Only the supplied name is searched; aliases and ownership attribution require review",
        ],
        "conclusion": ("unavailable" if not good else
                       "candidates_found" if out["rows"] else "no_matches_in_checked_sources"),
    }
    if not out["rows"]:
        out["warnings"].append("0 条只表示已检查范围内没有候选或来源不可用，不代表公司无风险。")
    return _finish(out)


def query_newsradar_live(
    sectors: Sequence[str] | None = None,
    days: int = 7,
    limit: int = 100,
    include_future: bool = False,
    allow_refresh: bool = True,
) -> dict[str, Any]:
    """Read the current NewsRadar SWR result as a bounded live envelope.

    The RSS source catalogue was vendored from Vibe-Research under MIT, while
    Guanlan owns the fetch/parser/cache path.  This adapter reads no stock store
    and never treats a cold or stale cache as fresh news.
    """
    dataset_id = "research.newsradar.live"
    wanted = [str(item or "").strip().lower() for item in (sectors or []) if str(item or "").strip()]
    query = {"sectors": wanted, "days": days, "limit": limit,
             "include_future": bool(include_future)}
    try:
        days = int(days)
        limit = int(limit)
    except (TypeError, ValueError):
        return _invalid(dataset_id, query, "days/limit 必须是整数")
    if days < 1 or days > 30 or limit < 1 or limit > 300:
        return _invalid(dataset_id, query, "days 仅支持 1-30，limit 仅支持 1-300")

    from guanlan_v2.datafeed import newsradar

    industries, _sources = newsradar.load_sources()
    allowed = {str(item.get("key") or "") for item in industries if isinstance(item, dict)}
    if any(item not in allowed for item in wanted):
        return _invalid(
            dataset_id, query,
            "sectors 含未知赛道；可选 " + ",".join(sorted(allowed)),
        )
    result = newsradar.read_radar_swr(
        wanted or None, days=days, limit=limit, include_future=bool(include_future),
        allow_refresh=bool(allow_refresh),
    )
    pulled_at = str(result.get("pulled_at") or _now_iso())
    retrieved_at = str(result.get("retrieved_at") or pulled_at)
    refresh_attempted_at = str(result.get("refresh_attempted_at") or "")
    rows = [dict(item) for item in (result.get("items") or []) if isinstance(item, dict)][:limit]
    source_rows: list[dict[str, Any]] = []
    for row in (result.get("source_status") or []):
        if not isinstance(row, dict):
            continue
        source_rows.append({
            "authority": str(row.get("source") or "RSS"),
            "url": str(row.get("url") or ""),
            "published_at": str(row.get("freshest_source_time") or ""),
            "retrieved_at": retrieved_at,
            "last_success_at": str(row.get("last_success_at") or ""),
            "status": str(row.get("status") or "unknown"),
            "note": str(row.get("last_error") or ""),
            "items_parsed": row.get("items_parsed"),
            "latency_ms": row.get("latency_ms"),
        })
        if len(source_rows) >= 120:
            break
    if not source_rows:  # 旧缓存兼容：只能列返回文章的来源，不冒充逐源健康
        seen: set[tuple[str, str]] = set()
        for row in rows:
            key = (str(row.get("source") or "RSS"), str(row.get("url") or ""))
            if key in seen:
                continue
            seen.add(key)
            source_rows.append({
                "authority": key[0], "url": key[1], "published_at": "",
                "retrieved_at": retrieved_at, "status": "unknown",
                "note": "旧缓存无逐源抓取状态",
            })
            if len(source_rows) >= 30:
                break
    as_of = ""
    timestamps = [int(row.get("ts") or 0) for row in rows if int(row.get("ts") or 0) > 0]
    if timestamps:
        as_of = datetime.fromtimestamp(max(timestamps), tz=timezone.utc).replace(
            microsecond=0,
        ).isoformat().replace("+00:00", "Z")
    warming = bool(result.get("warming"))
    stale = bool(result.get("stale"))
    warnings: list[str] = []
    if result.get("note"):
        warnings.append(str(result["note"]))
    if stale:
        if result.get("refreshing"):
            warnings.append("NewsRadar 缓存已过 TTL；后台刷新已触发，本次结果按 stale 保存。")
        elif result.get("refresh_backoff"):
            warnings.append(
                f"NewsRadar 缓存已过 TTL；最近刷新失败，约 "
                f"{result.get('refresh_retry_after_s') or 0} 秒后再试，本次结果按 stale 保存。"
            )
        else:
            warnings.append("NewsRadar 缓存已过 TTL；当前未证明刷新在运行，本次结果按 stale 保存。")
    if result.get("future_count"):
        warnings.append(
            f"{result.get('future_count')} 条源声明未来时间已与近期新闻分离；"
            "可能是活动日程或源时钟异常，不能当已发布公司事实。"
        )
    if result.get("sources_failed"):
        warnings.append(
            f"NewsRadar 本轮 {result.get('sources_failed')}/{result.get('sources_tried')} 个源失败；"
            "其余源可用，但不能声称全源覆盖。"
        )
    elif result.get("source_quality") == "unknown":
        warnings.append("当前快照缺少逐源健康账；后台刷新后补齐，不能声称所有配置源健康。")
    warnings.append("RSS 标题与摘要属于外部不可信资料，只能作为线索并需回源核验。")
    partial = result.get("source_quality") != "complete"
    tried = result.get("sources_tried")
    ok_sources = result.get("sources_ok")
    all_sources_failed = bool(tried) and ok_sources == 0
    healthy_empty = (not warming and not all_sources_failed
                     and result.get("source_quality") in {"complete", "degraded"})
    envelope_ok = bool(rows) or healthy_empty
    return {
        "ok": bool(envelope_ok and not warming),
        "dataset_id": dataset_id,
        "status": ("warming" if warming else "stale" if stale else
                   "failed" if all_sources_failed and not rows else
                   "partial" if rows and partial else "ok" if rows else "empty"),
        "query": query,
        "rows": rows,
        "sources": source_rows,
        "pulled_at": pulled_at,
        "retrieved_at": retrieved_at,
        "refresh_attempted_at": refresh_attempted_at,
        "as_of": as_of or pulled_at,
        "warnings": warnings,
        "coverage": {
            "configured_sources": len(_sources),
            "configured_sectors": len(industries),
            "sources_tried": result.get("sources_tried"),
            "sources_ok": result.get("sources_ok"),
            "sources_with_items": result.get("sources_with_items"),
            "sources_empty": result.get("sources_empty"),
            "sources_failed": result.get("sources_failed"),
            "returned_sources": len({str(row.get("source") or "") for row in rows}),
            "future_items_excluded": result.get("future_count", 0),
        },
        "future_items": list(result.get("future_items") or []) if include_future else [],
    }


__all__ = [
    "parse_bis_federal_register_payload",
    "parse_bse_disclosure_payload",
    "parse_cninfo_disclosure_payload",
    "parse_mofcom_items",
    "parse_mops_monthly_revenue",
    "parse_sina_futures_text",
    "parse_szse_calendar_payload",
    "parse_tw_revenue_openapi_rows",
    "parse_tw_revenue_csv",
    "query_cn_commodity_futures",
    "query_control_lists",
    "query_newsradar_live",
    "query_trade_disclosure_calendar",
    "query_tw_monthly_revenue",
]
