"""Per-stock fund flow updater — zero-token via 东财 push2/push2his.

Pulls daily net buy/sell breakdowns (主力 / 大单 / 中单 / 小单 / 超大单)
for individual stocks and writes a long-format Parquet:

    parquet_root / stock_fund_flow_daily.parquet

Schema (one row per (code, date)):

    code         str    qlib upper-case ('SH600519')
    date         date   trading day
    main_net     float  主力净流入额 (元)
    small_net    float  小单净流入额 (元)
    mid_net      float  中单净流入额 (元)
    large_net    float  大单净流入额 (元)
    super_net    float  超大单净流入额 (元)
    main_pct     float  主力净占比 (%, 净流入 / 成交额)
    small_pct    float  ...
    mid_pct      float
    large_pct    float
    super_pct    float
    close        float  收盘价 (元)
    pct_chg      float  当日涨跌幅 (%)

────────────────────────────────────────────────────────────────────
**Source**: 东方财富 push2his (HTTP, zero auth). The protocol comes from
the upstream 东财 quote frontend; the spec was reverse-engineered by the
community (cf. ``simonlin1212/a-stock-data`` v3.1). We implement against the
spec directly — no third-party SDK in the call path, so version skew elsewhere
can't break us.

**Why a separate Parquet instead of cn_data bin**: fund flow is per-stock
multi-field timeseries but the use cases are mostly cross-sectional ("today's
top main_net inflows") + recent history (~120 days). A long-format Parquet
joins cleanly with the daily quote bin in downstream agents (whale-sentiment,
quant, mainline-radar) without inventing a new bin schema.

API::

    >>> from financial_analyst.data.updaters.fund_flow import update_fund_flow
    >>> from financial_analyst.data.paths import get_data_paths
    >>> p = get_data_paths()
    >>> stats = update_fund_flow(p.parquet_root, ["SH600519", "SZ000858"], lmt=120)
    >>> stats
    {'ok': True, 'codes_total': 2, 'codes_ok': 2, 'codes_failed': 0,
     'rows_new': 240, 'rows_total': 240, 'date_range': '...',
     'parquet_path': '.../stock_fund_flow_daily.parquet'}
"""
from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path
from typing import Iterable, List, Optional, Union

import pandas as pd
import requests


_BASE_URL = "https://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get"
_FIELDS1 = "f1,f2,f3,f7"
_FIELDS2 = "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63,f64,f65"

# Browser-mimic headers — 东财 push2his rejects unsigned requests
_DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
_HEADERS = {
    "User-Agent": _DEFAULT_UA,
    "Referer": "https://quote.eastmoney.com/",
    "Origin": "https://quote.eastmoney.com",
}

# CSV parse order — matches the kline string layout returned by push2his
_COLS = [
    "date",
    "main_net", "small_net", "mid_net", "large_net", "super_net",
    "main_pct", "small_pct", "mid_pct", "large_pct", "super_pct",
    "close", "pct_chg", "_f64", "_f65",   # last two often 0, kept for forward compat
]


# ──────────────────────── code conversion ────────────────────────


def _qlib_to_secid(code: str) -> str:
    """``SH600519`` → ``1.600519``, ``SZ000858`` → ``0.000858``, ``BJ830779`` → ``2.830779``.

    The leading digit is the 东财 exchange market: 1=SSE, 0=SZSE, 2=BSE. Falls
    back to the standard SSE-by-leading-6 rule if no prefix supplied (some
    upstream lists drop it).
    """
    code = code.strip().upper()
    if code.startswith("SH"):
        return f"1.{code[2:]}"
    if code.startswith("SZ"):
        return f"0.{code[2:]}"
    if code.startswith("BJ"):
        return f"2.{code[2:]}"
    # Bare 6-digit fallback — guess by leading char (6/9 ≈ SSE; 0/3 ≈ SZSE; 4/8 ≈ BSE)
    if code[:1] in ("6", "9"):
        return f"1.{code}"
    if code[:1] in ("0", "3"):
        return f"0.{code}"
    if code[:1] in ("4", "8"):
        return f"2.{code}"
    raise ValueError(f"Unrecognized code format: {code!r}")


# ──────────────────────── single-stock fetch ────────────────────────


def _safe_float(s: str) -> float:
    """Parse a single field — 东财 uses ``"-"`` for missing values, not ``null``."""
    if s == "-" or s == "":
        return 0.0
    try:
        return float(s)
    except ValueError:
        return 0.0


def _fetch_one(
    code: str,
    lmt: int = 120,
    timeout: float = 15.0,
    session: Optional[requests.Session] = None,
) -> pd.DataFrame:
    """Pull one stock's daily fund flow. Returns empty DataFrame on any failure.

    ``code`` is qlib format (SH600519 etc). The returned DataFrame is long-form
    with all 14 schema columns plus ``code``.
    """
    secid = _qlib_to_secid(code)
    params = {
        "secid": secid,
        "fields1": _FIELDS1,
        "fields2": _FIELDS2,
        "lmt": str(lmt),
    }
    sess = session or requests
    try:
        r = sess.get(_BASE_URL, params=params, headers=_HEADERS, timeout=timeout)
    except requests.exceptions.RequestException:
        return pd.DataFrame()

    if r.status_code != 200:
        return pd.DataFrame()
    try:
        d = r.json()
    except ValueError:
        return pd.DataFrame()

    # ``data`` is None for invalid / delisted codes
    inner = d.get("data") or {}
    klines = inner.get("klines") or []
    if not klines:
        return pd.DataFrame()

    rows: list[dict] = []
    for line in klines:
        parts = line.split(",")
        if len(parts) < 13:
            continue   # malformed line; skip rather than abort the whole stock
        row = {"code": code}
        for i, col in enumerate(_COLS):
            if i >= len(parts):
                break
            row[col] = parts[i] if col == "date" else _safe_float(parts[i])
        rows.append(row)
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    # Coerce date string ``YYYY-MM-DD`` → date object for clean parquet roundtrip
    df["date"] = pd.to_datetime(df["date"]).dt.date
    # Drop forward-compat scratch columns before write
    df = df.drop(columns=[c for c in ("_f64", "_f65") if c in df.columns])
    return df


# ──────────────────────── public API ────────────────────────


def update_fund_flow(
    parquet_root: Union[str, Path],
    codes: Iterable[str],
    lmt: int = 120,
    rate_sleep: float = 0.10,
    checkpoint_every: int = 200,
    dry_run: bool = False,
    progress: bool = True,
) -> dict:
    """Pull daily fund flow for ``codes``, merge into ``stock_fund_flow_daily.parquet``.

    Idempotent: re-running on the same day dedups on ``(code, date)`` so no
    duplicates. Per-stock failures are non-fatal — the batch keeps going and
    we count failures in the return dict.

    Parameters
    ----------
    parquet_root
        Directory holding ``stock_fund_flow_daily.parquet`` directly.
    codes
        Iterable of qlib-format codes (e.g. ``["SH600519", "SZ000858"]``).
    lmt
        How many trailing trading days to request per stock. The upstream caps
        at ~120 for the free endpoint; pass smaller (e.g. 5) for daily refresh
        to save bandwidth.
    rate_sleep
        Sleep after each per-stock request. 0.1s is fine for the free tier
        based on V3.1 testing on 茅台 + 五粮液 + ETF (no 429 observed).
    checkpoint_every
        Flush merged Parquet every N successful stocks (crash safety on
        large universes).
    dry_run
        Plan-only mode. Returns a synthesized stats dict without hitting
        the network.
    progress
        Print per-batch progress lines.

    Returns
    -------
    dict
        ``{ok, codes_total, codes_ok, codes_failed, rows_new, rows_total,
        date_range, parquet_path, errors}``.
    """
    codes_list = [c.strip().upper() for c in codes if c and c.strip()]
    parquet_root = Path(parquet_root)
    parquet_path = parquet_root / "stock_fund_flow_daily.parquet"

    if dry_run:
        n_existing = 0
        if parquet_path.exists():
            try:
                n_existing = len(pd.read_parquet(parquet_path, columns=["code"]))
            except Exception:
                pass
        return {
            "dry_run": True,
            "ok": True,
            "codes_total": len(codes_list),
            "codes_ok": 0,
            "codes_failed": 0,
            "rows_new": 0,
            "rows_total": n_existing,
            "plan": (
                f"Would pull last {lmt} days fund flow for {len(codes_list)} codes "
                f"from 东财 push2his, merge into {parquet_path} "
                f"(existing {n_existing} rows). "
                f"ETA ~{len(codes_list) * (rate_sleep + 0.15):.0f}s."
            ),
            "parquet_path": str(parquet_path),
        }

    if not codes_list:
        return {
            "ok": True, "codes_total": 0, "codes_ok": 0, "codes_failed": 0,
            "rows_new": 0, "rows_total": 0, "errors": [],
            "parquet_path": str(parquet_path),
        }

    parquet_root.mkdir(parents=True, exist_ok=True)

    # Load existing for merge / dedup
    old_df = pd.DataFrame()
    if parquet_path.exists():
        try:
            old_df = pd.read_parquet(parquet_path)
        except Exception as e:
            if progress:
                print(f"[fund_flow warn] existing parquet read failed ({e}), "
                      f"overwriting", flush=True)

    if progress:
        print(f"[fund_flow] fetching {len(codes_list)} codes, lmt={lmt} ...",
              flush=True)

    new_frames: list[pd.DataFrame] = []
    codes_ok = 0
    codes_failed = 0
    errors: list[dict] = []
    t0 = time.time()

    # Reuse one session for connection pooling
    with requests.Session() as sess:
        for i, code in enumerate(codes_list, 1):
            df = _fetch_one(code, lmt=lmt, session=sess)
            if len(df) > 0:
                new_frames.append(df)
                codes_ok += 1
            else:
                codes_failed += 1
                # Cap error list to keep stats payload small
                if len(errors) < 20:
                    errors.append({"code": code, "reason": "empty result"})

            if progress and (i % 50 == 0 or i == len(codes_list)):
                elapsed = time.time() - t0
                rate = i / elapsed if elapsed > 0 else 1.0
                eta = (len(codes_list) - i) / rate if rate > 0 else 0
                rows_so_far = sum(len(f) for f in new_frames)
                print(f"  {i}/{len(codes_list)} | ok={codes_ok} fail={codes_failed} "
                      f"| rows={rows_so_far} | ETA {eta:.0f}s", flush=True)

            # Periodic checkpoint flush — write intermediate so a crash mid-run
            # doesn't lose hours of fetching
            if (i % checkpoint_every == 0) and new_frames:
                _flush(new_frames, old_df, parquet_path)
                # _flush mutates old_df conceptually — re-load to keep dedup right
                try:
                    old_df = pd.read_parquet(parquet_path)
                except Exception:
                    pass
                new_frames = []

            time.sleep(rate_sleep)

    # Final flush
    if new_frames:
        _flush(new_frames, old_df, parquet_path)

    # Re-read for final stats
    try:
        final = pd.read_parquet(parquet_path)
        rows_total = len(final)
        date_range = (f"{final['date'].min()} → {final['date'].max()}"
                      if "date" in final.columns and rows_total > 0 else "n/a")
    except Exception:
        rows_total = 0
        date_range = "n/a"

    rows_new_estimate = rows_total - len(old_df) if len(old_df) > 0 else rows_total

    if progress:
        print(f"[fund_flow ✓] codes ok={codes_ok}/{len(codes_list)} "
              f"failed={codes_failed} | parquet rows={rows_total} ({date_range}) "
              f"耗时 {time.time() - t0:.1f}s", flush=True)

    return {
        "ok": codes_ok > 0,
        "codes_total": len(codes_list),
        "codes_ok": codes_ok,
        "codes_failed": codes_failed,
        "rows_new": max(0, rows_new_estimate),
        "rows_total": rows_total,
        "date_range": date_range,
        "errors": errors,
        "parquet_path": str(parquet_path),
    }


# ──────────────────────── internal: merge + atomic write ────────────────────────


def _flush(new_frames: list[pd.DataFrame], old_df: pd.DataFrame,
           parquet_path: Path) -> None:
    """Concat + dedup (last write wins) + atomic write."""
    if not new_frames:
        return
    new_df = pd.concat(new_frames, ignore_index=True)
    if len(old_df) > 0:
        combined = pd.concat([old_df, new_df], ignore_index=True)
    else:
        combined = new_df
    combined = combined.drop_duplicates(subset=["code", "date"], keep="last")
    combined = combined.sort_values(["code", "date"]).reset_index(drop=True)

    tmp = parquet_path.with_suffix(".tmp")
    combined.to_parquet(tmp, index=False)
    if parquet_path.exists():
        parquet_path.unlink()
    tmp.rename(parquet_path)
