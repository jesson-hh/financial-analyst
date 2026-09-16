"""Bounded official exchange daily observations, fetched on demand only.

Daily settlement reports are not live quotes.  Continuous symbols are resolved
to an explicitly named contract by largest daily open interest, not to a vendor's
continuous series.  This module has no stock-store or persistent-cache access.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from typing import Any, Mapping, Sequence

import httpx


SHFE_DAILY_URL = "https://www.shfe.com.cn/data/tradedata/future/dailydata/kx{day}.dat"
INE_DAILY_URL = "https://www.ine.cn/data/tradedata/future/dailydata/kx{day}.dat"
CZCE_DAILY_URL = "https://www.czce.com.cn/cn/DFSStaticFiles/Future/{year}/{day}/FutureDataDaily.txt"
GFEX_DAILY_URL = "http://www.gfex.com.cn/u/interfacesWebTiDayQuotes/loadList"
_EXCHANGE_PRODUCTS = {
    "SHFE": frozenset("CU AL ZN PB NI SN AO AD AU AG RB WR HC SS FU BU RU SP BR OP".split()),
    "INE": frozenset("SC NR LU BC EC".split()),
    "CZCE": frozenset("AP CF CJ CY FG JR LR MA OI PF PK PM PR PX RI RM RS SA SF SH SM SR TA UR WH ZC".split()),
    "DCE": frozenset("A B BB C CS EB EG FB I J JD JM L LH LG M P PG PP RR V Y".split()),
    "GFEX": frozenset("SI LC PS PT PD".split()),
}
_URLS = {"SHFE": SHFE_DAILY_URL, "INE": INE_DAILY_URL, "CZCE": CZCE_DAILY_URL, "GFEX": GFEX_DAILY_URL}
_AUTHORITY = {"SHFE": "上海期货交易所", "INE": "上海国际能源交易中心", "CZCE": "郑州商品交易所", "GFEX": "广州期货交易所（官网 HTTP）"}
_MAX_BODY_BYTES = 4 * 1024 * 1024


class UnsettledDailyReport(ValueError):
    """The official daily file exists but still contains an intraday snapshot."""


def _number(value: Any, *, integer: bool = False) -> int | float | None:
    try:
        result = float(str(value).replace(",", "").strip())
        return (int(result) if integer else result) if math.isfinite(result) else None
    except (TypeError, ValueError, OverflowError):
        return None


def _row(exchange: str, contract: str, product: str, day: str) -> dict[str, Any]:
    return {
        "record_type": "cn_commodity_futures_daily", "exchange": exchange,
        "symbol": contract, "actual_contract": contract, "product_code": product,
        "source_trade_date": day, "source_trade_time": "",
        "source_trade_ts_raw": day, "observation_kind": "official_daily_settlement",
        "freshness_label": "official_daily_not_realtime", "source_is_official": True,
        "volume_unit": "contracts", "open_interest_unit": "contracts",
        "price_unit": "exchange_contract_quotation_unit",
    }


def parse_shfe_daily(payload: Any, exchange: str, requested_day: date) -> list[dict[str, Any]]:
    if not isinstance(payload, Mapping) or str(payload.get("o_code")) != "0000":
        raise ValueError("exchange daily response is not a successful report")
    report_day = f"{payload.get('o_year')}-{str(payload.get('o_month')).zfill(2)}-{str(payload.get('o_day')).zfill(2)}"
    if report_day != requested_day.isoformat():
        raise ValueError("exchange report date does not match request")
    # The exchange explicitly labels the report as post-settlement only once
    # trading issue/sequence numbers appear. The same URL exists intraday.
    if not str(payload.get("o_year_num") or "").strip() or not str(payload.get("o_trade_day") or "").strip():
        raise UnsettledDailyReport("exchange report is not yet settled")
    instruments = payload.get("o_curinstrument")
    if not isinstance(instruments, list):
        raise ValueError("exchange daily response has no contract table")
    rows = []
    for item in instruments:
        if not isinstance(item, Mapping):
            continue
        product = str(item.get("PRODUCTGROUPID") or "").strip().upper()
        delivery = str(item.get("DELIVERYMONTH") or "").strip()
        if not re.fullmatch(r"[A-Z]{1,3}", product) or not re.fullmatch(r"\d{4}", delivery):
            continue  # product subtotals and all-market totals are not contracts
        row = _row(exchange, product + delivery, product, report_day)
        row.update({
            "name": str(item.get("PRODUCTNAME") or "").strip(),
            "open": _number(item.get("OPENPRICE")), "high": _number(item.get("HIGHESTPRICE")),
            "low": _number(item.get("LOWESTPRICE")), "close": _number(item.get("CLOSEPRICE")),
            "settlement": _number(item.get("SETTLEMENTPRICE")),
            "previous_settlement": _number(item.get("PRESETTLEMENTPRICE")),
            "volume": _number(item.get("VOLUME"), integer=True),
            "open_interest": _number(item.get("OPENINTEREST"), integer=True),
            "open_interest_change": _number(item.get("OPENINTERESTCHG"), integer=True),
            "source_updated_at_raw": str(payload.get("update_date") or ""),
        })
        rows.append(row)
    if not rows:
        raise ValueError("exchange daily report contains no valid contracts")
    return rows


def parse_czce_daily(text: str, requested_day: date) -> list[dict[str, Any]]:
    match = re.search(r"期货每日行情表\s*[（(](\d{4}-\d{2}-\d{2})[）)]", text)
    if not match or match.group(1) != requested_day.isoformat():
        raise ValueError("CZCE daily report title/date does not match request")
    lines = text.splitlines()
    header_index = next((i for i, line in enumerate(lines) if line.strip().startswith("合约代码|")), -1)
    if header_index < 0:
        raise ValueError("CZCE daily report has no contract header")
    headers = [part.strip() for part in lines[header_index].split("|")]
    if not {"合约代码", "今收盘", "今结算", "成交量(手)", "持仓量"} <= set(headers):
        raise ValueError("CZCE daily report field layout changed")
    rows = []
    for line in lines[header_index + 1:]:
        fields = [part.strip() for part in line.split("|")]
        item = dict(zip(headers, fields))
        contract = str(item.get("合约代码") or "").upper()
        match = re.fullmatch(r"([A-Z]{1,3})\d{3}", contract)
        if not match:
            continue
        row = _row("CZCE", contract, match.group(1), requested_day.isoformat())
        row.update({
            "name": match.group(1), "open": _number(item.get("今开盘")),
            "high": _number(item.get("最高价")), "low": _number(item.get("最低价")),
            "close": _number(item.get("今收盘")), "settlement": _number(item.get("今结算")),
            "previous_settlement": _number(item.get("昨结算")),
            "volume": _number(item.get("成交量(手)"), integer=True),
            "open_interest": _number(item.get("持仓量"), integer=True),
            "open_interest_change": _number(item.get("增减量"), integer=True),
        })
        rows.append(row)
    if not rows:
        raise ValueError("CZCE daily report contains no valid contracts")
    return rows


def _fetch_bytes(url: str) -> bytes:
    return _request_bytes(url)


def _request_bytes(url: str, *, method: str = "GET", data: Mapping[str, str] | None = None) -> bytes:
    # Follow no cross-host redirects; a branded HTML error page cannot be a report.
    with httpx.Client(timeout=httpx.Timeout(12, connect=5), follow_redirects=False) as client:
        with client.stream(method, url, data=data, headers={"User-Agent": "Mozilla/5.0 (GuanlanResearch)"}) as response:
            response.raise_for_status()
            chunks, size = [], 0
            for chunk in response.iter_bytes():
                size += len(chunk)
                if size > _MAX_BODY_BYTES:
                    raise ValueError("exchange daily response exceeds byte limit")
                chunks.append(chunk)
            return b"".join(chunks)


def parse_gfex_daily(payload: Any, requested_day: date) -> list[dict[str, Any]]:
    if not isinstance(payload, Mapping) or str(payload.get("code")) != "0":
        raise ValueError("GFEX daily report is not successful")
    params = payload.get("param") or {}
    if params.get("trade_date") != [requested_day.strftime("%Y%m%d")] or params.get("trade_type") != ["0"]:
        raise ValueError("GFEX daily response does not match requested date/type")
    items = payload.get("data")
    if not isinstance(items, list):
        raise ValueError("GFEX daily response has no data table")
    rows = []
    for item in items:
        if not isinstance(item, Mapping):
            continue
        product = str(item.get("varietyOrder") or "").strip().upper()
        delivery = str(item.get("delivMonth") or "").strip()
        if not re.fullmatch(r"[A-Z]{1,3}", product) or not re.fullmatch(r"\d{4}", delivery):
            continue
        if _number(item.get("clearPrice")) is None:
            raise UnsettledDailyReport("GFEX contracts have no settlement yet")
        row = _row("GFEX", product + delivery, product, requested_day.isoformat())
        row.update({
            "name": str(item.get("variety") or ""), "open": _number(item.get("open")),
            "high": _number(item.get("high")), "low": _number(item.get("low")),
            "close": _number(item.get("close")), "settlement": _number(item.get("clearPrice")),
            "previous_settlement": _number(item.get("lastClear")),
            "volume": _number(item.get("volumn"), integer=True),
            "open_interest": _number(item.get("openInterest"), integer=True),
            "open_interest_change": _number(item.get("diffI"), integer=True),
            "transport_security": "unencrypted_http", "date_basis": "provider_echoed_query_date",
        })
        rows.append(row)
    if not rows:
        raise UnsettledDailyReport("GFEX daily report has no contract rows yet")
    return rows


def _latest_exchange(exchange: str, today: date) -> dict[str, Any]:
    attempts, sources = [], []
    for offset in range(10):
        candidate = today - timedelta(days=offset)
        if candidate.weekday() >= 5:
            continue
        day = candidate.strftime("%Y%m%d")
        url = _URLS[exchange].format(day=day, year=candidate.year)
        try:
            if exchange == "GFEX":
                body = _request_bytes(url, method="POST", data={"trade_date": day, "trade_type": "0", "variety": ""})
                rows = parse_gfex_daily(json.loads(body), candidate)
            else:
                body = _fetch_bytes(url)
                rows = (parse_czce_daily(body.decode("utf-8-sig"), candidate) if exchange == "CZCE"
                        else parse_shfe_daily(json.loads(body), exchange, candidate))
            source = {
                "authority": _AUTHORITY[exchange], "url": url, "status": "ok",
                "retrieved_at": datetime.now(timezone.utc).isoformat(),
                "published_at": "", "report_date": candidate.isoformat(),
                "observation_kind": "official_daily_settlement", "source_is_official": True,
                "raw_sha256": hashlib.sha256(body).hexdigest(), "bytes": len(body),
            }
            if exchange == "GFEX":
                source.update({"transport_security": "unencrypted_http", "method": "POST",
                               "query": {"trade_date": day, "trade_type": "0", "variety": ""}})
            for row in rows:
                row["source_url"] = url
            return {"rows": rows, "sources": [source], "attempts": attempts,
                    "status": "ok", "report_date": candidate.isoformat()}
        except UnsettledDailyReport:
            attempts.append({"url": url, "report_date": candidate.isoformat(), "status": "not_yet_settled"})
        except httpx.HTTPStatusError as exc:
            status = f"http_{exc.response.status_code}"
            attempts.append({"url": url, "report_date": candidate.isoformat(), "status": status})
            if exc.response.status_code not in {404, 410}:
                break  # never evade a denial/rate limit by retrying other dates
        except Exception as exc:
            attempts.append({"url": url, "report_date": candidate.isoformat(), "status": type(exc).__name__})
            break
    if attempts:
        sources.append({"authority": _AUTHORITY[exchange], "url": attempts[-1]["url"],
                        "status": attempts[-1]["status"], "source_is_official": True,
                        "observation_kind": "official_daily_settlement"})
    return {"rows": [], "sources": sources, "attempts": attempts, "status": "unavailable"}


def query_official_futures_daily(symbols: Sequence[str], *, today: date | None = None) -> dict[str, Any]:
    """At most 30 symbols, one latest report per supported exchange, no disk IO."""
    current = today or datetime.now(timezone(timedelta(hours=8))).date()
    requested = list(dict.fromkeys(str(item).strip().upper() for item in symbols))
    if len(requested) > 30 or any(not re.fullmatch(r"[A-Z]{1,3}(?:0|\d{3,4})", item) for item in requested):
        raise ValueError("invalid official futures symbols")
    product_for = {symbol: re.match(r"[A-Z]+", symbol).group() for symbol in requested}
    exchange_for = {symbol: next((ex for ex, products in _EXCHANGE_PRODUCTS.items() if product_for[symbol] in products), "unknown") for symbol in requested}
    needed = sorted({ex for ex in exchange_for.values() if ex in _URLS})
    with ThreadPoolExecutor(max_workers=max(1, len(needed))) as pool:
        results = dict(zip(needed, pool.map(lambda ex: _latest_exchange(ex, current), needed)))
    rows, coverage, sources, attempts = [], {}, [], []
    for result in results.values():
        sources.extend(result["sources"])
        attempts.extend(result["attempts"])
    for symbol in requested:
        exchange = exchange_for[symbol]
        if exchange not in _URLS:
            coverage[symbol] = {"status": "not_connected", "exchange": exchange,
                                "reason": "official_daily_adapter_not_available"}
            continue
        candidates = [row for row in results[exchange]["rows"] if row["product_code"] == product_for[symbol]]
        continuous = symbol == product_for[symbol] + "0"
        if continuous:
            candidates = [row for row in candidates if isinstance(row.get("open_interest"), (float, int)) and row["open_interest"] > 0]
        if not continuous:
            if exchange == "CZCE" and len(symbol) - len(product_for[symbol]) == 4:
                def matches_full_year(row: Mapping[str, Any]) -> bool:
                    delivery = row["actual_contract"][-3:]
                    report = date.fromisoformat(row["source_trade_date"])
                    # Active contracts in the dated report cannot be from a
                    # previous decade; bind the one-digit year to its next
                    # occurrence at or after the report's calendar year.
                    year = next(y for y in range(report.year, report.year + 10) if y % 10 == int(delivery[0]))
                    return (1 <= int(delivery[1:]) <= 12
                            and (year, int(delivery[1:])) >= (report.year, report.month)
                            and symbol == product_for[symbol] + f"{year % 100:02d}" + delivery[1:])
                candidates = [row for row in candidates if matches_full_year(row)]
            else:
                candidates = [row for row in candidates if row["actual_contract"] == symbol]
        if not candidates:
            coverage[symbol] = {"status": "not_returned" if results[exchange]["rows"] else "unavailable", "exchange": exchange}
            continue
        chosen = dict(max(candidates, key=lambda row: (row.get("open_interest") or 0, row.get("volume") or 0, row["actual_contract"])))
        chosen["requested_symbol"] = symbol
        chosen["symbol"] = symbol
        chosen["contract_selection"] = "largest_daily_open_interest_not_vendor_continuous" if continuous else "requested_contract"
        rows.append(chosen)
        coverage[symbol] = {"status": "ok", "exchange": exchange,
                            "actual_contract": chosen["actual_contract"], "report_date": chosen["source_trade_date"]}
    return {"rows": rows, "sources": sources, "coverage": coverage, "attempts": attempts}
