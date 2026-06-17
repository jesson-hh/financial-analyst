"""Tushare financial statements (income/balancesheet/cashflow) updater.

Vendored from ``G:/stocks/scripts/update_financial_incremental.py`` (research lab
side), with hardcoded ``PARQUET_DIR`` and embedded ``TOKEN`` replaced by
function parameters so the caller controls path + token.

**Tushare opt-in**: this updater requires a Tushare Pro API token. The fa CLI
threads it in via the ``FA_TUSHARE_TOKEN`` env var or ``--tushare-token`` flag.
Without one, the caller raises ``RuntimeError`` — the rest of ``fa data update``
still runs fine (zero-token paths are unaffected).

Two main modes:

  * **By ann_date range** (``update_financial(..., mode='incremental', days=7)``)
    — pulls income/balancesheet/cashflow rows announced in the last N days.
    The cheap default for daily refreshes.
  * **By stock list** (``update_financial(..., mode='backfill', codes=[...])``)
    — pulls full history for a code list. Used when you want to fill a missing
    stock from scratch.

API::

    >>> from financial_analyst.data.updaters.financial import update_financial
    >>> stats = update_financial("/path/to/parquet", tushare_token="...", days=7)
    >>> stats
    {'income': 142, 'balancesheet': 138, 'cashflow': 140, 'failed_apis': []}
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional, Union

import pandas as pd
import requests


TUSHARE_URL = "http://api.tushare.pro"

# Three financial-statement APIs we pull. Field lists match the stocks-side
# canonical schema (so the resulting parquets are byte-identical layouts).
_APIS: dict[str, tuple[str, str]] = {
    "income": (
        "profit_sheet.parquet",
        "ts_code,ann_date,f_ann_date,end_date,report_type,revenue,"
        "operate_profit,total_profit,n_income,basic_eps",
    ),
    "balancesheet": (
        "balance_sheet.parquet",
        "ts_code,ann_date,end_date,total_assets,total_liab,"
        "total_hldr_eqy_exc_min_int,cap_rese,money_cap,accounts_receiv,"
        "inventories",
    ),
    "cashflow": (
        "cash_flow.parquet",
        "ts_code,ann_date,end_date,n_cashflow_act,n_cashflow_inv_act,"
        "n_cash_flows_fnc_act,c_fr_sale_sg",
    ),
}


# ──────────────────────── Tushare HTTP wrapper ────────────────────────


def _ts_query(api_name: str, token: str, fields: str = "",
              max_retries: int = 3, **params) -> pd.DataFrame:
    """Tushare API call with retry + rate-limit handling.

    Lessons from stocks-side script (2026-04-14 incident, repeated 5/13-22):
    OHLCV fetch can succeed while ``daily_basic`` connection drops — old code
    retried once and dropped silently. Now: 3 attempts with backoff, raises on
    business errors (e.g. invalid period) but retries on network errors.
    """
    req = {"api_name": api_name, "token": token, "params": params}
    if fields:
        req["fields"] = fields
    for attempt in range(max_retries):
        try:
            r = requests.post(TUSHARE_URL, json=req, timeout=30)
            d = r.json()
            if d.get("code") != 0:
                msg = d.get("msg", "unknown")
                if "每分钟" in msg or "最多访问" in msg:
                    # rate-limit: sleep + retry
                    print(f"    [tushare rate limit] sleep 15s ...", flush=True)
                    time.sleep(15)
                    continue
                raise RuntimeError(f"Tushare {api_name} business error: {msg}")
            items = d["data"]["items"]
            cols = d["data"]["fields"]
            return pd.DataFrame(items, columns=cols)
        except requests.exceptions.RequestException as e:
            if attempt < max_retries - 1:
                time.sleep(5)
                continue
            raise
    return pd.DataFrame()


# ──────────────────────── pull strategies ────────────────────────


def _pull_by_ann_date(
    api_name: str, fields: str, start_date: str, end_date: str,
    token: str, fin_dir: Path, progress: bool = True,
) -> int:
    """Pull one API's data by ``ann_date`` range, merge into existing parquet.

    Returns number of NEW rows added (not total rows in file).
    """
    filename = _APIS[api_name][0]
    parquet_path = fin_dir / filename

    # Load existing for dedup
    existing = pd.read_parquet(parquet_path) if parquet_path.exists() else pd.DataFrame()
    if progress:
        print(f"  [{api_name}] existing: {len(existing):,} rows", flush=True)

    # Day-by-day pull
    new_dfs: list[pd.DataFrame] = []
    current = datetime.strptime(start_date, "%Y%m%d")
    end_dt = datetime.strptime(end_date, "%Y%m%d")
    while current <= end_dt:
        date_str = current.strftime("%Y%m%d")
        try:
            df = _ts_query(api_name, token=token, ann_date=date_str, fields=fields)
            if df is not None and len(df) > 0:
                new_dfs.append(df)
                if progress:
                    print(f"    [{api_name} {date_str}] {len(df)} rows", flush=True)
        except Exception as e:
            if progress:
                print(f"    [{api_name} {date_str}] FAIL: {e}", flush=True)
        time.sleep(0.4)
        current += timedelta(days=1)

    if not new_dfs:
        return 0

    new_data = pd.concat(new_dfs, ignore_index=True)
    n_new = len(new_data)

    # Merge + dedup
    if len(existing) > 0:
        combined = pd.concat([existing, new_data], ignore_index=True)
        dedup_cols = ["ts_code", "ann_date", "end_date"]
        if "report_type" in combined.columns:
            dedup_cols.append("report_type")
        before = len(combined)
        combined = combined.drop_duplicates(subset=dedup_cols, keep="last")
        combined = combined.sort_values(["ts_code", "end_date", "ann_date"]).reset_index(drop=True)
        if progress:
            print(f"  [{api_name}] merged {before:,} → {len(combined):,} rows "
                  f"(dedup {before - len(combined)})", flush=True)
    else:
        combined = new_data

    fin_dir.mkdir(parents=True, exist_ok=True)
    combined.to_parquet(str(parquet_path), index=False)
    if progress:
        print(f"  [{api_name}] wrote {filename} ({len(combined):,} rows)", flush=True)
    return n_new


def _pull_by_stock(
    api_name: str, fields: str, stock_list: List[str],
    token: str, fin_dir: Path,
    backfill_start: str = "20160101", backfill_end: Optional[str] = None,
    progress: bool = True,
) -> int:
    """Pull one API per stock for the full backfill range. Used when ``--backfill``
    targets specific codes (e.g. newly-listed stocks not covered by ann_date pulls).
    """
    backfill_end = backfill_end or datetime.now().strftime("%Y%m%d")
    filename = _APIS[api_name][0]
    parquet_path = fin_dir / filename
    existing = pd.read_parquet(parquet_path) if parquet_path.exists() else pd.DataFrame()
    if progress:
        print(f"  [{api_name}] backfill {len(stock_list)} stocks "
              f"({backfill_start}-{backfill_end}); existing {len(existing):,} rows",
              flush=True)

    new_dfs: list[pd.DataFrame] = []
    done = fail = 0
    t0 = time.time()
    for ts_code in stock_list:
        try:
            df = _ts_query(api_name, token=token, ts_code=ts_code,
                           start_date=backfill_start, end_date=backfill_end,
                           fields=fields)
            if df is not None and len(df) > 0:
                new_dfs.append(df)
            done += 1
        except Exception as e:
            msg = str(e)
            if "每分钟" in msg or "最多访问" in msg:
                time.sleep(15)
                try:
                    df = _ts_query(api_name, token=token, ts_code=ts_code,
                                   start_date=backfill_start, end_date=backfill_end,
                                   fields=fields)
                    if df is not None and len(df) > 0:
                        new_dfs.append(df)
                    done += 1
                    continue
                except Exception:
                    pass
            fail += 1
        time.sleep(0.35)
        if progress and (done + fail) % 50 == 0 and (done + fail) > 0:
            elapsed = time.time() - t0
            rate = (done + fail) / elapsed if elapsed > 0 else 1
            eta = (len(stock_list) - done - fail) / rate if rate > 0 else 0
            rows = sum(len(d) for d in new_dfs)
            print(f"    [{api_name}] {done+fail}/{len(stock_list)} | ok={done} "
                  f"fail={fail} | {rows:,} rows | ETA {eta:.0f}s", flush=True)

    if not new_dfs:
        return 0
    new_data = pd.concat(new_dfs, ignore_index=True)
    n_new = len(new_data)

    if len(existing) > 0:
        combined = pd.concat([existing, new_data], ignore_index=True)
        dedup_cols = ["ts_code", "ann_date", "end_date"]
        if "report_type" in combined.columns:
            dedup_cols.append("report_type")
        combined = combined.drop_duplicates(subset=dedup_cols, keep="last")
        combined = combined.sort_values(["ts_code", "end_date", "ann_date"]).reset_index(drop=True)
    else:
        combined = new_data

    fin_dir.mkdir(parents=True, exist_ok=True)
    combined.to_parquet(str(parquet_path), index=False)
    if progress:
        print(f"  [{api_name}] wrote {filename} ({len(combined):,} rows, {n_new:,} new)",
              flush=True)
    return n_new


# ──────────────────────── public API ────────────────────────


def update_financial(
    parquet_root: Union[str, Path],
    tushare_token: str,
    days: int = 7,
    since: Optional[str] = None,
    backfill_codes: Optional[List[str]] = None,
    dry_run: bool = False,
    progress: bool = True,
) -> dict:
    """Incremental refresh of three financial statements via Tushare.

    Parameters
    ----------
    parquet_root
        Directory containing ``financial/`` subdir. Three parquets land in
        ``parquet_root / 'financial' / {profit,balance,cash_flow}.parquet``.
    tushare_token
        Tushare Pro API token. Get one at https://tushare.pro (free tier
        covers our usage). Pass via ``FA_TUSHARE_TOKEN`` env or
        ``fa data update --tushare-token``.
    days
        Pull rows announced in the last N days (incremental mode).
    since
        Override ``days`` — pull from this date (YYYYMMDD) to today.
    backfill_codes
        If set, additionally pull full history for these qlib codes
        (e.g. newly listed). Format: ``["SH600519", "SZ300750"]`` — they get
        converted to Tushare format internally.
    dry_run
        Plan-only mode. Returns counts without hitting Tushare.
    progress
        Print progress lines.

    Returns
    -------
    dict
        ``{income, balancesheet, cashflow}`` row counts (new rows), plus
        ``failed_apis`` and metadata.
    """
    if not tushare_token:
        raise RuntimeError(
            "tushare_token required. Set FA_TUSHARE_TOKEN env or pass --tushare-token. "
            "Get one free at https://tushare.pro/."
        )

    parquet_root = Path(parquet_root)
    fin_dir = parquet_root / "financial"
    end_date = datetime.now().strftime("%Y%m%d")
    start_date = since or (datetime.now() - timedelta(days=days)).strftime("%Y%m%d")

    if dry_run:
        return {
            "dry_run": True,
            "plan": (
                f"Would pull income/balancesheet/cashflow announced "
                f"{start_date}-{end_date} via Tushare → {fin_dir}"
                + (f" + backfill {len(backfill_codes)} codes" if backfill_codes else "")
            ),
            "income": 0, "balancesheet": 0, "cashflow": 0,
            "failed_apis": [],
            "parquet_root": str(fin_dir),
        }

    stats: dict = {"income": 0, "balancesheet": 0, "cashflow": 0, "failed_apis": []}

    if progress:
        print(f"[financial] Tushare pull ann_date {start_date} → {end_date}", flush=True)

    # Step 1: ann_date incremental
    for api_name, (_, fields) in _APIS.items():
        try:
            n = _pull_by_ann_date(api_name, fields, start_date, end_date,
                                  tushare_token, fin_dir, progress=progress)
            stats[api_name] = n
        except Exception as e:
            stats["failed_apis"].append({"api": api_name, "stage": "ann_date", "error": str(e)})
            if progress:
                print(f"  [{api_name}] ann_date FAIL: {e}", flush=True)

    # Step 2: optional backfill by stock
    if backfill_codes:
        ts_codes = [_qlib_to_tushare(c) for c in backfill_codes]
        for api_name, (_, fields) in _APIS.items():
            try:
                n = _pull_by_stock(api_name, fields, ts_codes,
                                   tushare_token, fin_dir, progress=progress)
                stats[api_name] += n
            except Exception as e:
                stats["failed_apis"].append({"api": api_name, "stage": "backfill", "error": str(e)})

    stats["parquet_root"] = str(fin_dir)
    return stats


def _qlib_to_tushare(code: str) -> str:
    """``SH600519`` → ``600519.SH``."""
    code = code.upper()
    if code.startswith("SH"):
        return f"{code[2:]}.SH"
    if code.startswith("SZ"):
        return f"{code[2:]}.SZ"
    if code.startswith("BJ"):
        return f"{code[2:]}.BJ"
    raise ValueError(f"Unknown code prefix: {code}")
