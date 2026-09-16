"""Live global-market research feeds for a bound Guanlan workspace.

The adapters in this module deliberately avoid Guanlan's stock database and
``datafeed.live_client``.  Every destination is a fixed authority URL selected
in code; callers may supply only bounded filters.  The returned envelopes keep
the observation/report date separate from retrieval time so an Agent cannot
mistake a daily or weekly publication for an intraday quote.
"""

from __future__ import annotations

import csv
import hashlib
import html as html_lib
import io
import json
import os
import re
import threading
import time
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from html.parser import HTMLParser
from typing import Any, Iterable, Mapping, Sequence
from urllib.parse import urlencode

import httpx


SEC_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SEC_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
SEC_COMPANYFACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
SEC_FRAMES_URL = "https://data.sec.gov/api/xbrl/frames/{taxonomy}/{concept}/{unit}/{frame}.json"
SEC_ARCHIVES_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{accession}/{document}"

FINRA_DAILY_FILES_PAGE = (
    "https://www.finra.org/finra-data/browse-catalog/short-sale-volume-data/"
    "daily-short-sale-volume-files"
)
FINRA_DAILY_FILE_URL = "https://cdn.finra.org/equity/regsho/daily/{prefix}shvol{day}.txt"

TREASURY_DAILY_CSV_URL = (
    "https://home.treasury.gov/resource-center/data-chart-center/interest-rates/"
    "daily-treasury-rates.csv/{year}/all?type={dataset}"
    "&field_tdr_date_value={year}&page&_format=csv"
)
TREASURY_AUCTIONS_URL = (
    "https://api.fiscaldata.treasury.gov/services/api/fiscal_service/v1/"
    "accounting/od/auctions_query"
)
TREASURY_AVG_RATES_URL = (
    "https://api.fiscaldata.treasury.gov/services/api/fiscal_service/v2/"
    "accounting/od/avg_interest_rates"
)

CFTC_RESOURCE_URL = "https://publicreporting.cftc.gov/resource/{dataset_id}.json"
NASDAQ_EARNINGS_URL = "https://api.nasdaq.com/api/calendar/earnings?date={day}"

_UA = "Mozilla/5.0 (compatible; GuanlanResearch/2.0; +local-workspace-agent)"
_NASDAQ_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; GuanlanResearch/2.0)",
    "Accept": "application/json, text/plain, */*",
    "Origin": "https://www.nasdaq.com",
    "Referer": "https://www.nasdaq.com/market-activity/earnings",
}
_TIMEOUT = httpx.Timeout(30.0, connect=8.0)
_CACHE_TTL_SECONDS = 15 * 60
_CACHE_MAX_ENTRIES = 64
_CACHE_MAX_BODY_BYTES = 4 * 1024 * 1024
_CACHE_MAX_TOTAL_BODY_BYTES = 32 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class _CacheEntry:
    stored_at: float
    expires_at: float
    body: bytes
    final_url: str
    response_headers: dict[str, str]


# Keep the mapping itself public-to-the-module and ``clear`` compatible because
# tests and diagnostics intentionally reset this process-local cache.  Ordering
# supplies deterministic least-recently-used eviction without a second index.
_cache: OrderedDict[str, _CacheEntry] = OrderedDict()
_cache_lock = threading.Lock()
_sec_rate_lock = threading.Lock()
_sec_last_request = 0.0

_SYMBOL_RE = re.compile(r"^[A-Z0-9.^-]{1,20}$")
_CIK_RE = re.compile(r"^\d{1,10}$")
_ACCESSION_RE = re.compile(r"^\d{10}-\d{2}-\d{6}$")
_DOCUMENT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,159}$")
_XBRL_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,119}$")
_XBRL_UNIT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,39}$")
_FRAME_RE = re.compile(r"^(?:CY|FY)\d{4}(?:Q[1-4])?(?:I)?$")
_CONTRACT_CODE_RE = re.compile(r"^\d{6}$")

_SEC_DEFAULT_CONCEPTS = (
    "Revenues",
    "SalesRevenueNet",
    "NetIncomeLoss",
    "Assets",
    "Liabilities",
    "StockholdersEquity",
    "EarningsPerShareDiluted",
    "CommonStockSharesOutstanding",
)

_TREASURY_DATASETS = {
    "nominal_curve": "daily_treasury_yield_curve",
    "real_curve": "daily_treasury_real_yield_curve",
    "bill_rates": "daily_treasury_bill_rates",
}

_CFTC_DATASETS = {
    ("tff", "futures_only"): "gpe5-46if",
    ("tff", "combined"): "yw9f-hn96",
    ("disaggregated", "futures_only"): "72hh-3qpy",
    ("disaggregated", "combined"): "kh3c-gbw2",
    ("legacy", "futures_only"): "6dca-aqww",
    ("legacy", "combined"): "jun7-fc8e",
    ("cit", "combined"): "4zgm-a668",
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _envelope(dataset_id: str, query: Mapping[str, Any]) -> dict[str, Any]:
    pulled_at = _now_iso()
    return {
        "ok": False,
        "dataset_id": dataset_id,
        "status": "unavailable",
        "query": dict(query),
        "rows": [],
        "sources": [],
        "pulled_at": pulled_at,
        "as_of": "",
        "warnings": [],
        "provenance": {
            "retrieved_at": pulled_at,
            "source_as_of": None,
            "observation_basis": "source_specific",
            "source_digests_present": False,
        },
        "pit": {
            "status": "current_observation_only",
            "source_as_of": None,
            "observed_at": pulled_at,
            "immutable_archive_receipt": False,
            "reason_code": "unified_pit_receipt_unavailable",
        },
        "identity_scope": {
            "scope": "explicit_query_only",
            "workspace_subject_bound": False,
            "issuer_mapping": "unavailable",
            "contract_mapping": "unavailable",
            "reason_code": "workspace_subject_mapping_unreviewed",
        },
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
    response_headers: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    source: dict[str, Any] = {
        "authority": authority,
        "url": url,
        "published_at": published_at,
        "retrieved_at": _now_iso(),
        "status": status,
    }
    headers = {str(k).lower(): str(v) for k, v in (response_headers or {}).items()}
    if headers.get("last-modified"):
        source["source_updated_at"] = headers["last-modified"]
    if raw is not None:
        source["raw_sha256"] = hashlib.sha256(raw).hexdigest()
        source["bytes"] = len(raw)
    return source


def _finish(out: dict[str, Any]) -> dict[str, Any]:
    rows = out.get("rows") if isinstance(out.get("rows"), list) else []
    sources = out.get("sources") if isinstance(out.get("sources"), list) else []
    good_sources = sum(1 for item in sources if item.get("status") == "ok")
    failed_sources = sum(1 for item in sources if item.get("status") != "ok")
    if rows:
        out["ok"] = True
        out["status"] = "partial" if failed_sources else "ok"
    elif good_sources:
        out["ok"] = True
        out["status"] = "empty"
    else:
        out["ok"] = False
        out["status"] = "unavailable"
    source_as_of = str(out.get("as_of") or "").strip() or None
    provenance = out.setdefault("provenance", {})
    provenance["retrieved_at"] = out.get("pulled_at")
    provenance["source_as_of"] = source_as_of
    provenance["source_digests_present"] = bool(sources) and all(
        item.get("status") != "ok" or bool(item.get("raw_sha256"))
        for item in sources
    )
    pit = out.setdefault("pit", {})
    pit.update({
        "source_as_of": source_as_of,
        "observed_at": out.get("pulled_at"),
        "immutable_archive_receipt": False,
        "reason_code": "unified_pit_receipt_unavailable",
    })
    return out


def _set_identity_scope(
    out: dict[str, Any],
    *,
    scope: str,
    issuer_mapping: str = "not_applicable",
    contract_mapping: str = "not_applicable",
    reason_code: str | None = None,
) -> None:
    out["identity_scope"] = {
        "scope": scope,
        "workspace_subject_bound": False,
        "issuer_mapping": issuer_mapping,
        "contract_mapping": contract_mapping,
        "reason_code": reason_code,
    }


def _client() -> httpx.Client:
    return httpx.Client(timeout=_TIMEOUT, follow_redirects=True, headers={"User-Agent": _UA})


def _prune_cache_locked(now: float) -> None:
    """Drop expired and least-recently-used entries while holding the lock."""
    expired = [key for key, entry in _cache.items() if entry.expires_at <= now]
    for key in expired:
        _cache.pop(key, None)

    total_body_bytes = sum(len(entry.body) for entry in _cache.values())
    while (
        len(_cache) > _CACHE_MAX_ENTRIES
        or total_body_bytes > _CACHE_MAX_TOTAL_BODY_BYTES
    ):
        _key, entry = _cache.popitem(last=False)
        total_body_bytes -= len(entry.body)


def _cached_get(
    url: str,
    *,
    headers: Mapping[str, str] | None = None,
    ttl: int = _CACHE_TTL_SECONDS,
) -> tuple[bytes, str, dict[str, str]]:
    header_key = json.dumps(dict(headers or {}), ensure_ascii=True, sort_keys=True)
    key = f"{url}\n{header_key}"
    now = time.monotonic()
    with _cache_lock:
        _prune_cache_locked(now)
        hit = _cache.get(key)
        if hit and now - hit.stored_at <= ttl:
            _cache.move_to_end(key)
            return hit.body, hit.final_url, dict(hit.response_headers)
        if hit:
            # A caller can intentionally request a shorter TTL than the one
            # used to populate the entry.  Preserve that contract by treating
            # the old value as expired for this key.
            _cache.pop(key, None)
    with _client() as client:
        response = client.get(url, headers=dict(headers or {}))
        response.raise_for_status()
        body = response.content
        final_url = str(response.url)
        response_headers = dict(response.headers)
    stored_at = time.monotonic()
    if ttl > 0 and len(body) <= _CACHE_MAX_BODY_BYTES:
        entry = _CacheEntry(
            stored_at=stored_at,
            expires_at=stored_at + ttl,
            body=body,
            final_url=final_url,
            response_headers=response_headers,
        )
        with _cache_lock:
            _prune_cache_locked(stored_at)
            _cache.pop(key, None)
            _cache[key] = entry
            _prune_cache_locked(stored_at)
    return body, final_url, response_headers


def _sec_user_agent() -> tuple[str, bool]:
    configured = str(os.getenv("GUANLAN_SEC_USER_AGENT") or "").strip()
    if configured:
        return configured[:240], True
    return "GuanlanResearch/2.0 guanlan-research@example.com", False


def _sec_get(url: str, *, ttl: int = _CACHE_TTL_SECONDS) -> tuple[bytes, str, dict[str, str]]:
    # SEC asks automated clients to remain below 10 requests/second.  The local
    # adapter uses a more conservative process-wide pace and bounded retries.
    global _sec_last_request
    user_agent, _configured = _sec_user_agent()
    last_error: Exception | None = None
    for attempt in range(3):
        with _sec_rate_lock:
            delay = 0.22 - (time.monotonic() - _sec_last_request)
            if delay > 0:
                time.sleep(delay)
            _sec_last_request = time.monotonic()
        try:
            return _cached_get(
                url,
                headers={"User-Agent": user_agent, "Accept-Encoding": "gzip, deflate"},
                ttl=ttl,
            )
        except httpx.HTTPStatusError as exc:
            last_error = exc
            if exc.response.status_code not in {429, 500, 502, 503, 504} or attempt == 2:
                raise
            time.sleep(0.5 * (attempt + 1))
    assert last_error is not None
    raise last_error


def _parse_iso_date(value: str, field: str) -> date:
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} 必须是 YYYY-MM-DD") from exc


def _bounded_dates(
    start: str,
    end: str,
    *,
    default_start: date,
    default_end: date,
    max_days: int,
) -> tuple[date, date]:
    first = _parse_iso_date(start, "start") if start else default_start
    last = _parse_iso_date(end, "end") if end else default_end
    if first > last:
        raise ValueError("start 不能晚于 end")
    if (last - first).days + 1 > max_days:
        raise ValueError(f"查询跨度最多 {max_days} 个自然日")
    return first, last


def _number(value: Any) -> int | float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return value
    text = str(value).strip()
    if not text or text.lower() in {"n/a", "na", "null", "none", "·", "--"}:
        return None
    negative = text.startswith("(") and text.endswith(")")
    text = text.strip("()$% ").replace(",", "")
    try:
        parsed = float(text)
    except ValueError:
        return None
    if negative:
        parsed = -parsed
    return int(parsed) if parsed.is_integer() else parsed


def _json_bytes(raw: bytes) -> Any:
    return json.loads(raw.decode("utf-8-sig"))


def _normalize_cik(value: str) -> str:
    raw = str(value or "").strip().upper().removeprefix("CIK")
    if not _CIK_RE.fullmatch(raw):
        raise ValueError("cik 必须是 1-10 位数字")
    return raw.zfill(10)


def _normalize_symbols(symbols: Sequence[str] | None, *, required: bool = False) -> list[str]:
    result: list[str] = []
    for value in symbols or []:
        symbol = str(value or "").strip().upper()
        if not _SYMBOL_RE.fullmatch(symbol):
            raise ValueError(f"证券代码非法: {value}")
        if symbol not in result:
            result.append(symbol)
    if required and not result:
        raise ValueError("symbols 至少需要一个证券代码")
    if len(result) > 20:
        raise ValueError("symbols 最多 20 个")
    return result


def _resolve_sec_identity(ticker: str, cik: str) -> tuple[str, str, bytes | None, str]:
    if cik:
        return _normalize_cik(cik), str(ticker or "").strip().upper(), None, ""
    symbol = str(ticker or "").strip().upper()
    if not _SYMBOL_RE.fullmatch(symbol):
        raise ValueError("ticker 或 cik 至少需要一个，ticker 格式非法")
    raw, url, _headers = _sec_get(SEC_TICKERS_URL, ttl=6 * 60 * 60)
    payload = _json_bytes(raw)
    for item in payload.values() if isinstance(payload, Mapping) else []:
        if isinstance(item, Mapping) and str(item.get("ticker") or "").upper() == symbol:
            return str(int(item["cik_str"])).zfill(10), symbol, raw, url
    raise ValueError(f"SEC ticker 表未找到 {symbol}")


def parse_sec_submissions(
    payload: Mapping[str, Any],
    *,
    forms: Iterable[str] = (),
    limit: int = 25,
) -> list[dict[str, Any]]:
    recent = ((payload.get("filings") or {}).get("recent") or {})
    if not isinstance(recent, Mapping):
        return []
    accessions = recent.get("accessionNumber") or []
    allowed = {str(value).strip().upper() for value in forms if str(value).strip()}
    cik = str(payload.get("cik") or "").zfill(10)
    entity = str(payload.get("name") or "")
    tickers = [str(value) for value in payload.get("tickers") or []]
    exchanges = [str(value) for value in payload.get("exchanges") or []]

    def at(key: str, index: int) -> Any:
        values = recent.get(key) or []
        return values[index] if index < len(values) else ""

    rows: list[dict[str, Any]] = []
    for index, accession in enumerate(accessions):
        form = str(at("form", index) or "").upper()
        if allowed and form not in allowed:
            continue
        accession = str(accession or "")
        primary_document = str(at("primaryDocument", index) or "")
        archive_base = (
            f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/"
            f"{accession.replace('-', '')}"
        )
        rows.append({
            "entity": entity,
            "cik": cik,
            "tickers": tickers,
            "exchanges": exchanges,
            "sic": str(payload.get("sic") or ""),
            "sic_description": str(payload.get("sicDescription") or ""),
            "fiscal_year_end": str(payload.get("fiscalYearEnd") or ""),
            "form": form,
            "accession_number": accession,
            "filing_date": str(at("filingDate", index) or ""),
            "report_date": str(at("reportDate", index) or ""),
            "acceptance_datetime": str(at("acceptanceDateTime", index) or ""),
            "act": str(at("act", index) or ""),
            "file_number": str(at("fileNumber", index) or ""),
            "film_number": str(at("filmNumber", index) or ""),
            "items": str(at("items", index) or ""),
            "size_bytes": _number(at("size", index)),
            "is_xbrl": bool(at("isXBRL", index)),
            "is_inline_xbrl": bool(at("isInlineXBRL", index)),
            "primary_document": primary_document,
            "primary_document_description": str(at("primaryDocDescription", index) or "")[:500],
            "filing_index_url": f"{archive_base}/{accession}-index.html",
            "filing_document_url": f"{archive_base}/{primary_document}" if primary_document else "",
            "evidence_label": "filing_source_reported",
            "confidence": "high",
        })
        if len(rows) >= limit:
            break
    return rows


def parse_sec_companyfacts(
    payload: Mapping[str, Any],
    *,
    taxonomy: str = "us-gaap",
    concepts: Sequence[str] = (),
    forms: Iterable[str] = (),
    unit: str = "",
    limit: int = 100,
) -> list[dict[str, Any]]:
    facts_by_taxonomy = payload.get("facts") or {}
    fact_map = facts_by_taxonomy.get(taxonomy) if isinstance(facts_by_taxonomy, Mapping) else {}
    if not isinstance(fact_map, Mapping):
        return []
    requested = list(concepts or _SEC_DEFAULT_CONCEPTS)
    by_lower = {str(name).lower(): str(name) for name in fact_map}
    selected = [by_lower[name.lower()] for name in requested if name.lower() in by_lower]
    allowed_forms = {str(value).strip().upper() for value in forms if str(value).strip()}
    wanted_unit = str(unit or "").strip().lower()
    rows: list[dict[str, Any]] = []
    for concept in selected:
        definition = fact_map.get(concept) or {}
        units = definition.get("units") or {}
        if not isinstance(units, Mapping):
            continue
        for source_unit, facts in units.items():
            if wanted_unit and str(source_unit).lower() != wanted_unit:
                continue
            for fact in facts if isinstance(facts, list) else []:
                if not isinstance(fact, Mapping):
                    continue
                form = str(fact.get("form") or "").upper()
                if allowed_forms and form not in allowed_forms:
                    continue
                row = {
                    "entity": str(payload.get("entityName") or ""),
                    "cik": str(payload.get("cik") or "").zfill(10),
                    "taxonomy": taxonomy,
                    "concept": concept,
                    "label": str(definition.get("label") or concept)[:240],
                    "description": str(definition.get("description") or "")[:800],
                    "unit": str(source_unit),
                    "value": fact.get("val"),
                    "start": str(fact.get("start") or ""),
                    "end": str(fact.get("end") or ""),
                    "fy": fact.get("fy"),
                    "fp": str(fact.get("fp") or ""),
                    "form": form,
                    "filed": str(fact.get("filed") or ""),
                    "frame": str(fact.get("frame") or ""),
                    "accession_number": str(fact.get("accn") or ""),
                    "evidence_label": "fact_source_reported",
                    "confidence": "high",
                }
                rows.append(row)
    rows.sort(
        key=lambda row: (
            str(row.get("filed") or ""),
            str(row.get("end") or ""),
            str(row.get("concept") or ""),
        ),
        reverse=True,
    )
    return rows[:limit]


class _HTMLTextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._skip = 0
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() in {"script", "style", "noscript"}:
            self._skip += 1
        elif tag.lower() in {"p", "div", "br", "tr", "li", "h1", "h2", "h3", "h4"}:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in {"script", "style", "noscript"} and self._skip:
            self._skip -= 1
        elif tag.lower() in {"p", "div", "tr", "li"}:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._skip:
            self.parts.append(data)


def _document_text(raw: bytes) -> str:
    text = raw.decode("utf-8", errors="replace")
    if "<html" in text[:10_000].lower() or "<body" in text[:10_000].lower():
        parser = _HTMLTextExtractor()
        parser.feed(text)
        text = "".join(parser.parts)
    text = html_lib.unescape(text)
    text = re.sub(r"[ \t\xa0]+", " ", text)
    text = re.sub(r"\n\s*\n(?:\s*\n)+", "\n\n", text)
    return text.strip()


def query_sec_filings_xbrl(
    view: str = "filings",
    ticker: str = "",
    cik: str = "",
    forms: Sequence[str] | None = None,
    concepts: Sequence[str] | None = None,
    taxonomy: str = "us-gaap",
    unit: str = "",
    frame: str = "",
    accession_number: str = "",
    primary_document: str = "",
    limit: int = 50,
) -> dict[str, Any]:
    dataset_id = "research.sec_filings_xbrl.live"
    query = {
        "view": view, "ticker": ticker, "cik": cik,
        "forms": list(forms or []), "concepts": list(concepts or []),
        "taxonomy": taxonomy, "unit": unit, "frame": frame,
        "accession_number": accession_number, "primary_document": primary_document,
        "limit": limit,
    }
    if view not in {"filings", "companyfacts", "frames", "filing_document"}:
        return _invalid(dataset_id, query, "view 仅支持 filings/companyfacts/frames/filing_document")
    if not isinstance(limit, int) or not 1 <= limit <= 200:
        return _invalid(dataset_id, query, "limit 必须在 1-200")
    taxonomy = str(taxonomy or "").strip()
    if not _XBRL_NAME_RE.fullmatch(taxonomy):
        return _invalid(dataset_id, query, "taxonomy 格式非法")
    for concept in concepts or []:
        if not _XBRL_NAME_RE.fullmatch(str(concept or "")):
            return _invalid(dataset_id, query, f"XBRL concept 格式非法: {concept}")
    out = _envelope(dataset_id, query)
    out["provenance"]["observation_basis"] = (
        "sec_filed_or_source_reported_date" if view != "filing_document"
        else "retrieval_time_only_source_date_unavailable"
    )
    _set_identity_scope(
        out,
        scope=("sec_cross_issuer_frame" if view == "frames" else "explicit_sec_identity"),
        issuer_mapping=(
            "source_reported_cik_rows" if view == "frames" else
            "explicit_cik" if str(cik or "").strip() else
            "current_sec_ticker_to_cik"
        ),
        reason_code="workspace_issuer_mapping_unreviewed",
    )
    out["frequency"] = "near_real_time_official_filing_feed"
    out["latency_note"] = "SEC submissions 通常秒级更新，XBRL API 通常分钟级；抓取时刻不是申报时刻。"
    _ua, configured = _sec_user_agent()
    if not configured:
        out["warnings"].append(
            "当前使用通用 SEC 联系标识；生产部署请设置 GUANLAN_SEC_USER_AGENT 为产品名和真实联系邮箱。"
        )
    try:
        if view == "frames":
            if len(concepts or []) != 1 or not unit or not frame:
                raise ValueError("frames 需要恰好一个 concepts、unit 和 frame")
            if not _XBRL_UNIT_RE.fullmatch(str(unit)) or not _FRAME_RE.fullmatch(str(frame)):
                raise ValueError("frames 的 unit 或 frame 格式非法")
            url = SEC_FRAMES_URL.format(
                taxonomy=taxonomy,
                concept=str((concepts or [""])[0]),
                unit=str(unit),
                frame=str(frame),
            )
            raw, final_url, headers = _sec_get(url)
            payload = _json_bytes(raw)
            rows = []
            for item in payload.get("data") or []:
                if not isinstance(item, Mapping):
                    continue
                rows.append({
                    "entity": str(item.get("entityName") or ""),
                    "cik": str(item.get("cik") or "").zfill(10),
                    "location": str(item.get("loc") or ""),
                    "taxonomy": str(payload.get("taxonomy") or taxonomy),
                    "concept": str(payload.get("tag") or (concepts or [""])[0]),
                    "label": str(payload.get("label") or "")[:240],
                    "description": str(payload.get("description") or "")[:800],
                    "unit": str(payload.get("uom") or unit),
                    "frame": str(frame),
                    "value": item.get("val"),
                    "period_start": str(item.get("start") or ""),
                    "period_end": str(item.get("end") or ""),
                    "fy": item.get("fy"),
                    "fp": str(item.get("fp") or ""),
                    "form": str(item.get("form") or ""),
                    "filed": str(item.get("filed") or ""),
                    "accession_number": str(item.get("accn") or ""),
                    "evidence_label": "fact_source_reported",
                    "confidence": "high",
                })
                if len(rows) >= limit:
                    break
            out["rows"] = rows
            out["as_of"] = max(
                (str(row.get("filed") or row.get("period_end") or "") for row in rows),
                default="",
            )
            out["sources"].append(_source("U.S. SEC EDGAR", final_url, raw=raw, response_headers=headers))
            return _finish(out)

        resolved_cik, resolved_ticker, ticker_raw, ticker_url = _resolve_sec_identity(ticker, cik)
        out["query"]["resolved_cik"] = resolved_cik
        if resolved_ticker:
            out["query"]["resolved_ticker"] = resolved_ticker
        if ticker_raw is not None:
            out["sources"].append(_source("U.S. SEC", ticker_url, raw=ticker_raw))

        if view == "filings":
            url = SEC_SUBMISSIONS_URL.format(cik=resolved_cik)
            raw, final_url, headers = _sec_get(url)
            payload = _json_bytes(raw)
            out["rows"] = parse_sec_submissions(payload, forms=forms or (), limit=limit)
            out["as_of"] = max((str(row.get("filing_date") or "") for row in out["rows"]), default="")
            if ((payload.get("filings") or {}).get("files") or []):
                out["warnings"].append("本次只查询 SEC recent submissions；更早归档分片未批量下载。")
            out["sources"].append(_source("U.S. SEC EDGAR", final_url, raw=raw, response_headers=headers))
        elif view == "companyfacts":
            url = SEC_COMPANYFACTS_URL.format(cik=resolved_cik)
            raw, final_url, headers = _sec_get(url)
            payload = _json_bytes(raw)
            out["rows"] = parse_sec_companyfacts(
                payload,
                taxonomy=taxonomy,
                concepts=concepts or (),
                forms=forms or (),
                unit=unit,
                limit=limit,
            )
            out["as_of"] = max((str(row.get("filed") or "") for row in out["rows"]), default="")
            out["warnings"].append(
                "只保存筛选后的标准化 facts，不复制整份 companyfacts；同一期间可能因重述或不同表单存在多条值。"
            )
            out["sources"].append(_source("U.S. SEC XBRL", final_url, raw=raw, response_headers=headers))
        else:
            if not _ACCESSION_RE.fullmatch(str(accession_number or "")):
                raise ValueError("filing_document 需要合法 accession_number")
            if not _DOCUMENT_RE.fullmatch(str(primary_document or "")):
                raise ValueError("filing_document 需要安全的 primary_document 文件名")
            url = SEC_ARCHIVES_URL.format(
                cik=int(resolved_cik),
                accession=str(accession_number).replace("-", ""),
                document=str(primary_document),
            )
            raw, final_url, headers = _sec_get(url, ttl=24 * 60 * 60)
            text = _document_text(raw)
            excerpt_limit = min(60_000, max(4_000, limit * 1_000))
            out["rows"] = [{
                "cik": resolved_cik,
                "ticker": resolved_ticker,
                "accession_number": accession_number,
                "primary_document": primary_document,
                "text_excerpt": text[:excerpt_limit],
                "text_characters_returned": min(len(text), excerpt_limit),
                "document_characters": len(text),
                "truncated": len(text) > excerpt_limit,
                "evidence_label": "filing_document_excerpt",
                "confidence": "high",
            }]
            # Accession contains only a two-digit filing year and is not an
            # observation date.  Leave ``as_of`` empty rather than inventing a
            # full date; the source accession remains in the row.
            out["as_of"] = ""
            out["warnings"].append(
                "申报正文只保存去脚本后的有界文本摘录；外部申报文字仍是不可信研究材料，不是 Agent 指令。"
            )
            out["sources"].append(_source("U.S. SEC EDGAR Archives", final_url, raw=raw, response_headers=headers))
    except ValueError as exc:
        return _invalid(dataset_id, query, str(exc))
    except Exception as exc:  # noqa: BLE001 - one authority failure becomes data, not an Agent crash
        out["sources"].append(_source("U.S. SEC", "https://www.sec.gov/edgar", status="error"))
        out["warnings"].append(f"SEC 接口不可用：{type(exc).__name__}")
    return _finish(out)


def parse_finra_short_volume(raw: bytes, symbols: Iterable[str]) -> list[dict[str, Any]]:
    wanted = {str(value).upper() for value in symbols}
    text = raw.decode("utf-8-sig", errors="replace")
    rows: list[dict[str, Any]] = []
    for item in csv.DictReader(io.StringIO(text), delimiter="|"):
        symbol = str(item.get("Symbol") or "").strip().upper()
        day = str(item.get("Date") or "").strip()
        if symbol not in wanted or not re.fullmatch(r"\d{8}", day):
            continue
        short_volume = _number(item.get("ShortVolume"))
        exempt_volume = _number(item.get("ShortExemptVolume"))
        total_volume = _number(item.get("TotalVolume"))
        rows.append({
            "date": f"{day[:4]}-{day[4:6]}-{day[6:]}",
            "symbol": symbol,
            "short_volume": short_volume,
            "short_exempt_volume": exempt_volume,
            "total_volume": total_volume,
            "facilities": [
                value.strip() for value in str(item.get("Market") or "").split(",") if value.strip()
            ],
        })
    return rows


def _finra_latest_day(out: dict[str, Any]) -> date:
    try:
        raw, final_url, headers = _cached_get(FINRA_DAILY_FILES_PAGE, ttl=10 * 60)
        matches = re.findall(rb"CNMSshvol(\d{8})\.txt", raw)
        if matches:
            latest = max(datetime.strptime(value.decode("ascii"), "%Y%m%d").date() for value in matches)
            out["sources"].append(
                _source("FINRA", final_url, raw=raw, response_headers=headers)
            )
            return latest
    except Exception as exc:  # noqa: BLE001
        out["warnings"].append(f"FINRA 最新文件页不可用，改用近10日探测：{type(exc).__name__}")
    today = date.today()
    for offset in range(10):
        candidate = today - timedelta(days=offset)
        url = FINRA_DAILY_FILE_URL.format(prefix="CNMS", day=candidate.strftime("%Y%m%d"))
        try:
            _cached_get(url, ttl=10 * 60)
            return candidate
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code in {403, 404}:
                continue
            raise
    raise ValueError("近10个自然日未找到 FINRA 已发布日报")


def query_finra_short_volume(
    symbols: Sequence[str] | None = None,
    start: str = "",
    end: str = "",
    include_otc: bool = True,
    limit: int = 100,
) -> dict[str, Any]:
    dataset_id = "research.finra_short_volume.live"
    query = {
        "symbols": list(symbols or []), "start": start, "end": end,
        "include_otc": include_otc, "limit": limit,
    }
    try:
        normalized_symbols = _normalize_symbols(symbols, required=True)
    except ValueError as exc:
        return _invalid(dataset_id, query, str(exc))
    if not isinstance(limit, int) or not 1 <= limit <= 200:
        return _invalid(dataset_id, query, "limit 必须在 1-200")
    out = _envelope(dataset_id, query)
    out["provenance"]["observation_basis"] = "finra_file_trade_date"
    _set_identity_scope(
        out,
        scope="explicit_finra_symbols",
        issuer_mapping="explicit_symbols_only",
        reason_code="workspace_issuer_mapping_unreviewed",
    )
    out["frequency"] = "daily_after_market"
    out["latency_note"] = "FINRA 日报通常在交易日美东 18:00 前发布，且可能后续修订。"
    out["warnings"].extend([
        "Daily short volume 不是半月 short interest；高比例不能直接解释为空头仓位或看跌信号。",
        "仅覆盖 FINRA 场外公开播报成交，不含交易所成交和非公开成交；Market 是报告设施而非上市市场。",
        "FINRA 免费数据的商业使用需另行核对其使用条款。",
    ])
    try:
        if start or end:
            if not start or not end:
                raise ValueError("显式日期查询需要同时提供 start 和 end")
            first, last = _bounded_dates(
                start, end, default_start=date.today(), default_end=date.today(), max_days=7,
            )
        else:
            first = last = _finra_latest_day(out)
        raw_rows: list[dict[str, Any]] = []
        prefixes = ["CNMS", *( ["FORF"] if include_otc else [])]
        cursor = first
        while cursor <= last:
            for prefix in prefixes:
                url = FINRA_DAILY_FILE_URL.format(prefix=prefix, day=cursor.strftime("%Y%m%d"))
                try:
                    raw, final_url, headers = _cached_get(url, ttl=10 * 60)
                    parsed = parse_finra_short_volume(raw, normalized_symbols)
                    for row in parsed:
                        row["source_file"] = prefix
                    raw_rows.extend(parsed)
                    out["sources"].append(
                        _source(
                            "FINRA Reg SHO Daily Short Sale Volume",
                            final_url,
                            published_at=cursor.isoformat(),
                            raw=raw,
                            response_headers=headers,
                        )
                    )
                except httpx.HTTPStatusError as exc:
                    if exc.response.status_code in {403, 404}:
                        out["warnings"].append(f"{cursor.isoformat()} {prefix} 日报未发布或当日休市。")
                    else:
                        out["sources"].append(_source("FINRA", url, status="error"))
                        out["warnings"].append(
                            f"{cursor.isoformat()} {prefix} 下载失败：HTTP {exc.response.status_code}"
                        )
                except Exception as exc:  # noqa: BLE001
                    out["sources"].append(_source("FINRA", url, status="error"))
                    out["warnings"].append(f"{cursor.isoformat()} {prefix} 解析失败：{type(exc).__name__}")
            cursor += timedelta(days=1)

        grouped: dict[tuple[str, str], dict[str, Any]] = {}
        for row in raw_rows:
            key = (row["date"], row["symbol"])
            target = grouped.setdefault(key, {
                "date": row["date"], "symbol": row["symbol"],
                "short_volume": 0.0, "short_exempt_volume": 0.0,
                "total_volume": 0.0, "facilities": [], "source_files": [],
            })
            for field in ("short_volume", "short_exempt_volume", "total_volume"):
                target[field] += float(row.get(field) or 0)
            target["facilities"].extend(
                value for value in row.get("facilities") or [] if value not in target["facilities"]
            )
            if row.get("source_file") not in target["source_files"]:
                target["source_files"].append(row.get("source_file"))
        rows = []
        for target in grouped.values():
            for field in ("short_volume", "short_exempt_volume", "total_volume"):
                value = target[field]
                target[field] = int(value) if value.is_integer() else value
            total = float(target["total_volume"] or 0)
            target["short_volume_ratio"] = round(float(target["short_volume"]) / total, 8) if total else None
            target["short_exempt_is_subset_of_short"] = True
            rows.append(target)
        rows.sort(key=lambda row: (row["date"], row["symbol"]), reverse=True)
        out["rows"] = rows[:limit]
        out["as_of"] = max((row["date"] for row in rows), default=last.isoformat())
    except ValueError as exc:
        return _invalid(dataset_id, query, str(exc))
    except Exception as exc:  # noqa: BLE001
        out["sources"].append(_source("FINRA", FINRA_DAILY_FILES_PAGE, status="error"))
        out["warnings"].append(f"FINRA 日短量接口不可用：{type(exc).__name__}")
    return _finish(out)


_TENOR_ALIASES: dict[str, tuple[str, float | int, str]] = {
    "1 MO": ("1m", 1, "par_yield"),
    "1.5 MONTH": ("1.5m", 1.5, "par_yield"),
    "2 MO": ("2m", 2, "par_yield"),
    "3 MO": ("3m", 3, "par_yield"),
    "4 MO": ("4m", 4, "par_yield"),
    "6 MO": ("6m", 6, "par_yield"),
    "1 YR": ("1y", 12, "par_yield"),
    "2 YR": ("2y", 24, "par_yield"),
    "3 YR": ("3y", 36, "par_yield"),
    "5 YR": ("5y", 60, "par_yield"),
    "7 YR": ("7y", 84, "par_yield"),
    "10 YR": ("10y", 120, "par_yield"),
    "20 YR": ("20y", 240, "par_yield"),
    "30 YR": ("30y", 360, "par_yield"),
}


def _treasury_column(column: str) -> tuple[str, float | int, str] | None:
    upper = re.sub(r"\s+", " ", str(column).strip().upper())
    if upper in _TENOR_ALIASES:
        return _TENOR_ALIASES[upper]
    match = re.fullmatch(r"(4|6|8|13|17|26|52) WEEKS (BANK DISCOUNT|COUPON EQUIVALENT)", upper)
    if match:
        weeks = int(match.group(1))
        basis = "bank_discount" if match.group(2) == "BANK DISCOUNT" else "coupon_equivalent"
        return f"{weeks}w", round(weeks * 12 / 52, 4), basis
    return None


def parse_treasury_csv(text: str, curve_type: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in csv.DictReader(io.StringIO(text.lstrip("\ufeff"))):
        raw_date = str(item.get("Date") or "").strip()
        try:
            observation_date = datetime.strptime(raw_date, "%m/%d/%Y").date().isoformat()
        except ValueError:
            continue
        for column, value in item.items():
            tenor = _treasury_column(column)
            rate = _number(value)
            if tenor is None or rate is None:
                continue
            label, months, basis = tenor
            if curve_type == "real_curve":
                basis = "real_par_yield"
            rows.append({
                "observation_date": observation_date,
                "curve_type": curve_type,
                "tenor": label,
                "tenor_months": months,
                "rate_pct": rate,
                "rate_basis": basis,
                "frequency": "daily",
            })
    return rows


def _fiscal_url(base: str, params: Mapping[str, str]) -> str:
    return f"{base}?{urlencode(params)}"


def query_us_treasury(
    dataset: str = "nominal_curve",
    start: str = "",
    end: str = "",
    latest_only: bool = True,
    limit: int = 200,
) -> dict[str, Any]:
    dataset_id = "research.us_treasury.live"
    query = {
        "dataset": dataset, "start": start, "end": end,
        "latest_only": latest_only, "limit": limit,
    }
    allowed = {*_TREASURY_DATASETS, "auctions", "average_outstanding_rate"}
    if dataset not in allowed:
        return _invalid(dataset_id, query, f"dataset 仅支持 {sorted(allowed)}")
    if not isinstance(limit, int) or not 1 <= limit <= 400:
        return _invalid(dataset_id, query, "limit 必须在 1-400")
    out = _envelope(dataset_id, query)
    out["provenance"]["observation_basis"] = "treasury_source_record_date"
    _set_identity_scope(out, scope="global_macro_series")
    today = date.today()
    try:
        first, last = _bounded_dates(
            start, end,
            default_start=today - timedelta(days=31 if dataset == "average_outstanding_rate" else 14),
            default_end=today + (timedelta(days=31) if dataset == "auctions" else timedelta()),
            max_days=730 if dataset in _TREASURY_DATASETS else 370,
        )
    except ValueError as exc:
        return _invalid(dataset_id, query, str(exc))
    try:
        rows: list[dict[str, Any]] = []
        if dataset in _TREASURY_DATASETS:
            out["frequency"] = "daily"
            out["latency_note"] = (
                "官方 CMT 为约美东15:30取得的指示性买方报价插值曲线，不是盘中成交价。"
            )
            for year in range(first.year, last.year + 1):
                source_dataset = _TREASURY_DATASETS[dataset]
                url = TREASURY_DAILY_CSV_URL.format(year=year, dataset=source_dataset)
                try:
                    raw, final_url, headers = _cached_get(url, ttl=10 * 60 if year == today.year else 24 * 60 * 60)
                    parsed = parse_treasury_csv(raw.decode("utf-8-sig", errors="replace"), dataset)
                    rows.extend(
                        row for row in parsed if first.isoformat() <= row["observation_date"] <= last.isoformat()
                    )
                    out["sources"].append(
                        _source("U.S. Department of the Treasury", final_url, raw=raw, response_headers=headers)
                    )
                except Exception as exc:  # noqa: BLE001
                    out["sources"].append(_source("U.S. Department of the Treasury", url, status="error"))
                    out["warnings"].append(f"Treasury {year} 曲线不可用：{type(exc).__name__}")
            if latest_only and rows:
                latest = max(row["observation_date"] for row in rows)
                rows = [row for row in rows if row["observation_date"] == latest]
            rows.sort(key=lambda row: (row["observation_date"], float(row["tenor_months"])), reverse=True)
            out["rows"] = rows[:limit]
            out["as_of"] = max((row["observation_date"] for row in rows), default="")
            out["warnings"].append(
                "历史期限列并非始终齐全；空期限不插值、不用抓取时刻补值。"
            )
        elif dataset == "auctions":
            out["frequency"] = "as_needed"
            out["latency_note"] = "结果字段为空时表示仅有拍卖公告，不能当作已出结果。"
            fields = (
                "record_date,cusip,security_type,security_term,announcemt_date,auction_date,"
                "issue_date,maturity_date,offering_amt,high_yield,high_discnt_rate,"
                "bid_to_cover_ratio,direct_bidder_accepted,indirect_bidder_accepted,"
                "primary_dealer_accepted,total_tendered,total_accepted"
            )
            url = _fiscal_url(TREASURY_AUCTIONS_URL, {
                "fields": fields,
                "filter": f"auction_date:gte:{first.isoformat()},auction_date:lte:{last.isoformat()}",
                "sort": "auction_date",
                "page[size]": str(min(limit, 400)),
            })
            raw, final_url, headers = _cached_get(url, ttl=10 * 60)
            payload = _json_bytes(raw)
            for item in payload.get("data") or []:
                if not isinstance(item, Mapping):
                    continue
                result_values = [_number(item.get(key)) for key in ("high_yield", "high_discnt_rate", "bid_to_cover_ratio")]
                rows.append({
                    "record_date": str(item.get("record_date") or ""),
                    "cusip": str(item.get("cusip") or ""),
                    "security_type": str(item.get("security_type") or ""),
                    "security_term": str(item.get("security_term") or ""),
                    "announcement_date": str(item.get("announcemt_date") or ""),
                    "auction_date": str(item.get("auction_date") or ""),
                    "issue_date": str(item.get("issue_date") or ""),
                    "maturity_date": str(item.get("maturity_date") or ""),
                    "offering_amount": _number(item.get("offering_amt")),
                    "high_yield_pct": _number(item.get("high_yield")),
                    "high_discount_rate_pct": _number(item.get("high_discnt_rate")),
                    "bid_to_cover_ratio": _number(item.get("bid_to_cover_ratio")),
                    "direct_bidder_accepted": _number(item.get("direct_bidder_accepted")),
                    "indirect_bidder_accepted": _number(item.get("indirect_bidder_accepted")),
                    "primary_dealer_accepted": _number(item.get("primary_dealer_accepted")),
                    "total_tendered": _number(item.get("total_tendered")),
                    "total_accepted": _number(item.get("total_accepted")),
                    "status": "result" if any(value is not None for value in result_values) else "announced",
                    "stable_key": f"{item.get('cusip') or ''}:{item.get('auction_date') or ''}",
                })
            out["rows"] = rows[:limit]
            out["as_of"] = max((row["record_date"] for row in rows), default="")
            out["sources"].append(
                _source("U.S. Treasury Fiscal Data", final_url, raw=raw, response_headers=headers)
            )
        else:
            out["frequency"] = "monthly"
            out["latency_note"] = "这是未偿国债平均利率，不是二级市场收益率曲线。"
            fields = "record_date,security_type_desc,security_desc,avg_interest_rate_amt"
            url = _fiscal_url(TREASURY_AVG_RATES_URL, {
                "fields": fields,
                "filter": f"record_date:gte:{first.isoformat()},record_date:lte:{last.isoformat()}",
                "sort": "-record_date",
                "page[size]": str(min(limit, 400)),
            })
            raw, final_url, headers = _cached_get(url, ttl=60 * 60)
            payload = _json_bytes(raw)
            rows = [{
                "record_date": str(item.get("record_date") or ""),
                "security_type": str(item.get("security_type_desc") or ""),
                "security_description": str(item.get("security_desc") or ""),
                "average_interest_rate_pct": _number(item.get("avg_interest_rate_amt")),
                "rate_basis": "average_rate_on_outstanding_marketables",
            } for item in payload.get("data") or [] if isinstance(item, Mapping)]
            if latest_only and rows:
                latest = max(row["record_date"] for row in rows)
                rows = [row for row in rows if row["record_date"] == latest]
            out["rows"] = rows[:limit]
            out["as_of"] = max((row["record_date"] for row in rows), default="")
            out["sources"].append(
                _source("U.S. Treasury Fiscal Data", final_url, raw=raw, response_headers=headers)
            )
    except Exception as exc:  # noqa: BLE001
        authority_url = TREASURY_AUCTIONS_URL if dataset == "auctions" else (
            TREASURY_AVG_RATES_URL if dataset == "average_outstanding_rate"
            else "https://home.treasury.gov/resource-center/data-chart-center/interest-rates"
        )
        out["sources"].append(_source("U.S. Department of the Treasury", authority_url, status="error"))
        out["warnings"].append(f"美国国债接口不可用：{type(exc).__name__}")
    return _finish(out)


def _position(raw: Mapping[str, Any], aliases: Sequence[str]) -> int | float | None:
    for key in aliases:
        if key in raw:
            return _number(raw.get(key))
    return None


def _category(raw: Mapping[str, Any], prefix: str, *, suffix: str = "") -> dict[str, Any]:
    long_value = _position(raw, [f"{prefix}_positions_long{suffix}", f"{prefix}_positions_long_all"])
    short_value = _position(raw, [
        f"{prefix}_positions_short{suffix}", f"{prefix}_positions_short_all",
        f"{prefix}__positions_short_all",
    ])
    spread_value = _position(raw, [
        f"{prefix}_positions_spread{suffix}", f"{prefix}_positions_spread_all",
        f"{prefix}__positions_spread_all", f"{prefix}_postions_spread_all",
    ])
    return _category_values(long_value, short_value, spread_value)


def _category_values(
    long_value: int | float | None,
    short_value: int | float | None,
    spread_value: int | float | None,
) -> dict[str, Any]:
    return {
        "long": long_value,
        "short": short_value,
        "spreading": spread_value,
        "net_outright": (
            long_value - short_value
            if isinstance(long_value, (int, float)) and isinstance(short_value, (int, float))
            else None
        ),
    }


def normalize_cftc_rows(
    raw_rows: Sequence[Mapping[str, Any]],
    *,
    report: str,
    scope: str,
    dataset_id: str,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for source_raw in raw_rows:
        # Most PRE datasets use lowercase field names, while the supplemental
        # CIT view still exposes several legacy mixed-case names.  Socrata JSON
        # keys are case-sensitive, so normalize once before applying aliases.
        raw = {str(key).lower(): value for key, value in source_raw.items()}
        report_date = str(raw.get("report_date_as_yyyy_mm_dd") or "")[:10]
        code = str(raw.get("cftc_contract_market_code") or "").strip()
        if report == "tff":
            categories = {
                "dealer": _category(raw, "dealer", suffix="_all"),
                "asset_manager": _category(raw, "asset_mgr"),
                "leveraged_money": _category(raw, "lev_money"),
                "other_reportable": _category(raw, "other_rept"),
                "nonreportable": _category(raw, "nonrept", suffix="_all"),
            }
        elif report == "disaggregated":
            categories = {
                "producer_merchant": _category(raw, "prod_merc"),
                "swap_dealer": _category(raw, "swap"),
                "managed_money": _category(raw, "m_money"),
                "other_reportable": _category(raw, "other_rept"),
                "nonreportable": _category(raw, "nonrept", suffix="_all"),
            }
        elif report == "legacy":
            categories = {
                "noncommercial": _category(raw, "noncomm", suffix="_all"),
                "commercial": _category(raw, "comm", suffix="_all"),
                "nonreportable": _category(raw, "nonrept", suffix="_all"),
            }
        else:
            categories = {
                "noncommercial": _category_values(
                    _position(raw, ["ncomm_postions_long_all_nocit"]),
                    _position(raw, ["ncomm_postions_short_all_nocit"]),
                    _position(raw, ["ncomm_postions_spread_all_nocit"]),
                ),
                "commercial": _category_values(
                    _position(raw, ["comm_positions_long_all_nocit"]),
                    _position(raw, ["comm_positions_short_all_nocit"]),
                    None,
                ),
                "index_traders": _category_values(
                    _position(raw, ["cit_positions_long_all"]),
                    _position(raw, ["cit_positions_short_all"]),
                    None,
                ),
                "nonreportable": _category(raw, "nonrept", suffix="_all"),
            }
        rows.append({
            "report_date": report_date,
            "report_type": report,
            "scope": scope,
            "market_and_exchange": str(raw.get("market_and_exchange_names") or "").strip(),
            "contract_market_name": str(raw.get("contract_market_name") or "").strip(),
            "contract_market_code": code,
            "commodity_code": str(raw.get("cftc_commodity_code") or "").strip(),
            "commodity_name": str(raw.get("commodity_name") or raw.get("commodity") or "").strip(),
            "open_interest": _number(raw.get("open_interest_all")),
            "change_in_open_interest": _position(
                raw, ["change_in_open_interest_all", "change_open_interest_all"],
            ),
            "categories": categories,
            "source_row_id": str(raw.get("id") or ""),
            "stable_key": f"{dataset_id}:{report_date}:{code}:{scope}",
            "frequency": "weekly",
        })
    return rows


def query_cftc_cot(
    report: str = "tff",
    scope: str = "futures_only",
    contract_codes: Sequence[str] | None = None,
    market_query: str = "",
    start: str = "",
    end: str = "",
    limit: int = 50,
) -> dict[str, Any]:
    dataset_id = "research.cftc_cot.live"
    query = {
        "report": report, "scope": scope,
        "contract_codes": list(contract_codes or []), "market_query": market_query,
        "start": start, "end": end, "limit": limit,
    }
    source_dataset = _CFTC_DATASETS.get((report, scope))
    if source_dataset is None:
        return _invalid(dataset_id, query, "不支持的 report/scope 组合；CIT 仅有 combined")
    codes = []
    for raw_code in contract_codes or []:
        code = str(raw_code or "").strip()
        if not _CONTRACT_CODE_RE.fullmatch(code):
            return _invalid(dataset_id, query, f"CFTC contract code 必须是6位数字: {raw_code}")
        if code not in codes:
            codes.append(code)
    if len(codes) > 20:
        return _invalid(dataset_id, query, "contract_codes 最多20个")
    market_query = str(market_query or "").strip()
    if len(market_query) > 80 or any(ord(char) < 32 for char in market_query):
        return _invalid(dataset_id, query, "market_query 最多80字且不能含控制字符")
    if not isinstance(limit, int) or not 1 <= limit <= 100:
        return _invalid(dataset_id, query, "limit 必须在 1-100")
    where: list[str] = []
    try:
        if start:
            where.append(f"report_date_as_yyyy_mm_dd >= '{_parse_iso_date(start, 'start').isoformat()}T00:00:00.000'")
        if end:
            where.append(f"report_date_as_yyyy_mm_dd <= '{_parse_iso_date(end, 'end').isoformat()}T23:59:59.999'")
        if start and end and _parse_iso_date(start, "start") > _parse_iso_date(end, "end"):
            raise ValueError("start 不能晚于 end")
    except ValueError as exc:
        return _invalid(dataset_id, query, str(exc))
    if codes:
        where.append("cftc_contract_market_code in (" + ",".join(f"'{code}'" for code in codes) + ")")
    params = {
        "$order": "report_date_as_yyyy_mm_dd DESC",
        "$limit": str(limit),
    }
    if where:
        params["$where"] = " AND ".join(where)
    if market_query:
        params["$q"] = market_query
    url = f"{CFTC_RESOURCE_URL.format(dataset_id=source_dataset)}?{urlencode(params)}"
    out = _envelope(dataset_id, query)
    out["provenance"]["observation_basis"] = "cftc_weekly_report_date"
    _set_identity_scope(
        out,
        scope="explicit_cftc_contract_filter" if codes else "cftc_market_text_filter",
        contract_mapping="explicit_official_contract_codes" if codes else "unbound_text_filter",
        reason_code="workspace_contract_mapping_unreviewed",
    )
    out["frequency"] = "weekly"
    out["latency_note"] = "COT 是每周二收盘持仓，通常周五美东15:30发布；不是实时仓位。"
    out["warnings"].extend([
        "少于20名可报告交易者的合约可能整周不出现；少数交易者字段可能被官方抑制为空。",
        "spreading 不分配进净多空；net_outright 仅按 long-short 派生。",
        "source_row_id 不是主键，工作空间稳定键使用数据集+报告日+合约代码+口径。",
    ])
    try:
        raw, final_url, headers = _cached_get(url, ttl=30 * 60)
        payload = _json_bytes(raw)
        if not isinstance(payload, list):
            raise ValueError("CFTC 返回不是行数组")
        out["rows"] = normalize_cftc_rows(
            [item for item in payload if isinstance(item, Mapping)],
            report=report,
            scope=scope,
            dataset_id=source_dataset,
        )
        out["as_of"] = max((row["report_date"] for row in out["rows"]), default="")
        out["sources"].append(
            _source("U.S. CFTC Commitments of Traders", final_url, raw=raw, response_headers=headers)
        )
    except Exception as exc:  # noqa: BLE001
        out["sources"].append(_source("U.S. CFTC", url, status="error"))
        out["warnings"].append(f"CFTC COT 接口不可用：{type(exc).__name__}")
    return _finish(out)


def parse_nasdaq_earnings(payload: Mapping[str, Any], scheduled_date: str) -> list[dict[str, Any]]:
    data = payload.get("data") or {}
    source_rows = data.get("rows") if isinstance(data, Mapping) else []
    rows: list[dict[str, Any]] = []
    time_map = {
        "time-pre-market": "pre_market",
        "time-after-hours": "after_hours",
        "time-not-supplied": "not_supplied",
    }
    for item in source_rows or []:
        if not isinstance(item, Mapping):
            continue
        symbol = str(item.get("symbol") or "").strip().upper()
        if not _SYMBOL_RE.fullmatch(symbol):
            continue
        rows.append({
            "scheduled_date": scheduled_date,
            "symbol": symbol,
            "company_name": str(item.get("name") or "").strip()[:300],
            "session": time_map.get(str(item.get("time") or ""), "not_supplied"),
            "fiscal_quarter_ending": str(item.get("fiscalQuarterEnding") or ""),
            "consensus_eps_forecast": _number(item.get("epsForecast")),
            "estimate_count": _number(item.get("noOfEsts")),
            "last_year_report_date": str(item.get("lastYearRptDt") or ""),
            "last_year_eps": _number(item.get("lastYearEPS")),
            "market_cap_usd": _number(item.get("marketCap")),
            "confirmation_status": "expected",
            "date_confidence": "provider_algorithmic_estimate",
            "evidence_label": "calendar_provider_estimate",
        })
    return rows


def query_overseas_earnings_calendar(
    start: str = "",
    end: str = "",
    symbols: Sequence[str] | None = None,
    limit: int = 200,
) -> dict[str, Any]:
    dataset_id = "research.overseas_earnings_calendar.live"
    query = {"start": start, "end": end, "symbols": list(symbols or []), "limit": limit}
    try:
        normalized_symbols = _normalize_symbols(symbols, required=False)
        first, last = _bounded_dates(
            start, end,
            default_start=date.today(), default_end=date.today() + timedelta(days=7),
            max_days=14,
        )
    except ValueError as exc:
        return _invalid(dataset_id, query, str(exc))
    if not isinstance(limit, int) or not 1 <= limit <= 300:
        return _invalid(dataset_id, query, "limit 必须在 1-300")
    out = _envelope(dataset_id, query)
    out["provenance"]["observation_basis"] = "retrieval_time_of_provider_estimate"
    _set_identity_scope(
        out,
        scope=("explicit_calendar_symbols" if normalized_symbols else "provider_calendar_universe"),
        issuer_mapping="explicit_symbols_only" if normalized_symbols else "unbound_provider_symbols",
        reason_code="workspace_issuer_mapping_unreviewed",
    )
    out["frequency"] = "daily_provider_calendar"
    out["latency_note"] = "日期为 Nasdaq/Zacks 预期日历，不是交易所或发行人确认日。"
    out["warnings"].extend([
        "所有日期一律标 expected；在公司 IR 或监管公告回源确认前不得写成 confirmed。",
        "首版覆盖 Nasdaq 提供的美国市场候选日历，不代表全球交易所完整覆盖。",
        "空结果只表示该提供方当日无返回，不表示公司确定不发布财报。",
    ])
    days: list[date] = []
    cursor = first
    while cursor <= last:
        days.append(cursor)
        cursor += timedelta(days=1)

    def fetch_day(day: date) -> tuple[date, bytes, str, dict[str, str], list[dict[str, Any]]]:
        url = NASDAQ_EARNINGS_URL.format(day=day.isoformat())
        raw, final_url, headers = _cached_get(url, headers=_NASDAQ_HEADERS, ttl=10 * 60)
        payload = _json_bytes(raw)
        if not isinstance(payload, Mapping):
            raise ValueError("Nasdaq 返回不是 JSON 对象")
        status = payload.get("status") or {}
        if not isinstance(status, Mapping) or _number(status.get("rCode")) != 200:
            raise ValueError(f"Nasdaq 业务状态失败: {status.get('rCode') if isinstance(status, Mapping) else ''}")
        if not isinstance(payload.get("data"), Mapping):
            raise ValueError("Nasdaq 返回缺少 data")
        return day, raw, final_url, headers, parse_nasdaq_earnings(payload, day.isoformat())

    with ThreadPoolExecutor(max_workers=min(4, len(days))) as pool:
        futures = {pool.submit(fetch_day, day): day for day in days}
        for future in as_completed(futures):
            day = futures[future]
            url = NASDAQ_EARNINGS_URL.format(day=day.isoformat())
            try:
                _day, raw, final_url, headers, rows = future.result()
                if normalized_symbols:
                    wanted = set(normalized_symbols)
                    rows = [row for row in rows if row["symbol"] in wanted]
                out["rows"].extend(rows)
                out["sources"].append(
                    _source("Nasdaq earnings calendar (Zacks-derived)", final_url, raw=raw, response_headers=headers)
                )
            except Exception as exc:  # noqa: BLE001
                out["sources"].append(_source("Nasdaq", url, status="error"))
                out["warnings"].append(f"{day.isoformat()} 海外财报候选日历不可用：{type(exc).__name__}")
    out["rows"].sort(key=lambda row: (row["scheduled_date"], row["symbol"]))
    out["rows"] = out["rows"][:limit]
    out["calendar_event_max_date"] = max(
        (row["scheduled_date"] for row in out["rows"]), default=last.isoformat(),
    )
    # A scheduled date is a future event estimate, not when the estimate was
    # observed.  Anchor the envelope to retrieval and keep event dates on rows.
    out["as_of"] = out["pulled_at"]
    return _finish(out)
