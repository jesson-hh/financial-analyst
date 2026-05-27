"""Internal helper for 东财 datacenter HTTP queries.

Backs the *margin_trading*, *lockup_expiry*, and *corporate_actions* updaters
(plus any future module that needs the same REST pattern). The datacenter
endpoint exposes hundreds of report tables — each has its own primary key /
sort column / filter syntax, but the HTTP envelope is identical.

Endpoint: ``https://datacenter-web.eastmoney.com/api/data/v1/get``

Public API::

    >>> from financial_analyst.data.updaters._eastmoney_dc import datacenter_query
    >>> rows = datacenter_query(
    ...     "RPTA_WEB_RZRQ_GGMX",
    ...     filter_str='(SCODE="600519")',
    ...     page_size=30,
    ...     sort_columns="DATE", sort_types="-1",
    ... )
    >>> rows[0].keys()
    dict_keys(['DATE', 'SCODE', 'SECNAME', 'RZYE', ...])

Zero token. Browser-mimic UA. Network errors return ``[]`` so callers can
treat empty as "no data" without distinguishing failure modes.
"""
from __future__ import annotations

import time
from typing import List, Optional

import requests


DATACENTER_URL = "https://datacenter-web.eastmoney.com/api/data/v1/get"

_DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
_HEADERS = {"User-Agent": _DEFAULT_UA}


def datacenter_query(
    report_name: str,
    *,
    columns: str = "ALL",
    filter_str: str = "",
    page_size: int = 50,
    page_number: int = 1,
    sort_columns: str = "",
    sort_types: str = "-1",
    timeout: float = 15.0,
    session: Optional[requests.Session] = None,
    retries: int = 2,
) -> List[dict]:
    """Fetch one page from the 东财 datacenter.

    ``filter_str`` follows 东财's bracketed equality syntax, e.g.
    ``'(SECURITY_CODE="600519")(TRADE_DATE>="2026-01-01")'`` — every clause
    in its own parentheses, AND-joined implicitly.

    ``sort_columns`` is a single column name; ``sort_types`` is ``"1"`` (asc)
    or ``"-1"`` (desc).

    Returns ``[]`` on any error (network, JSON parse, non-200, empty result).
    Caller is expected to handle empty as "no data available" without
    distinguishing causes — the datacenter genuinely returns empty for many
    valid queries (e.g. a stock with no past block trades).
    """
    params = {
        "reportName": report_name,
        "columns": columns,
        "filter": filter_str,
        "pageNumber": str(page_number),
        "pageSize": str(page_size),
        "sortColumns": sort_columns,
        "sortTypes": sort_types,
        "source": "WEB",
        "client": "WEB",
    }
    sess = session or requests
    last_err: Optional[Exception] = None
    for attempt in range(retries + 1):
        try:
            r = sess.get(DATACENTER_URL, params=params, headers=_HEADERS, timeout=timeout)
            if r.status_code != 200:
                if attempt < retries:
                    time.sleep(0.5 * (attempt + 1))
                    continue
                return []
            d = r.json()
            res = d.get("result") or {}
            return res.get("data") or []
        except (requests.exceptions.RequestException, ValueError) as e:
            last_err = e
            if attempt < retries:
                time.sleep(0.5 * (attempt + 1))
                continue
            return []
    return []


def datacenter_query_all_pages(
    report_name: str,
    *,
    columns: str = "ALL",
    filter_str: str = "",
    page_size: int = 100,
    sort_columns: str = "",
    sort_types: str = "-1",
    max_pages: int = 20,
    session: Optional[requests.Session] = None,
    rate_sleep: float = 0.15,
) -> List[dict]:
    """Paginate through every page until empty or ``max_pages`` reached.

    Most reports we use cap at <2 pages per stock, but daily-DLB (full-market
    dragon-tiger) etc. can have 5-10+ pages. ``max_pages=20`` is a safety net.
    """
    all_rows: list[dict] = []
    sess = session or requests.Session()
    own_session = session is None
    try:
        for page in range(1, max_pages + 1):
            rows = datacenter_query(
                report_name, columns=columns, filter_str=filter_str,
                page_size=page_size, page_number=page,
                sort_columns=sort_columns, sort_types=sort_types,
                session=sess,
            )
            if not rows:
                break
            all_rows.extend(rows)
            if len(rows) < page_size:
                break  # last page
            time.sleep(rate_sleep)
    finally:
        if own_session:
            sess.close()
    return all_rows
