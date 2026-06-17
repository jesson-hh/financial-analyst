"""Corporate actions (公司行为) batch updater — zero-token via 东财 datacenter.

One module covering three related but distinct per-stock event streams that
each tend to be low-frequency (quarterly/yearly) and are usually wanted
together:

  * **holder_change** (``RPT_HOLDERNUMLATEST``) — quarterly 股东户数变化
    + 户均持股 (chip-concentration signal)
  * **block_trade** (``RPT_DATA_BLOCKTRADE``) — 大宗交易: price/vol +
    buyer/seller broker + premium %
  * **dividend** (``RPT_SHAREBONUS_DET``) — 分红送转: per-share cash dividend
    + bonus/transfer ratios + plan progress

Three separate Parquet files under ``parquet_root``:
``holder_change.parquet`` / ``block_trade.parquet`` / ``dividend.parquet``.
Caller picks which to refresh via the ``include`` set.

Inspired by ``simonlin1212/a-stock-data`` v3.1 endpoint discovery.
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Iterable, Optional, Set, Union

import pandas as pd
import requests

from financial_analyst.data.updaters._eastmoney_dc import datacenter_query


# ──────────────────────── stream descriptors ────────────────────────


def _qlib_to_dcode(code: str) -> str:
    c = code.strip().upper()
    return c[2:] if c[:2] in ("SH", "SZ", "BJ") else c


def _safe_float(v) -> float:
    if v is None or v == "" or v == "-":
        return 0.0
    try:
        return float(v)
    except (ValueError, TypeError):
        return 0.0


def _safe_date_str(v) -> str:
    """Coerce a datacenter date field to ``YYYY-MM-DD`` or ``""``.

    Datacenter returns ``None`` (not a string) for missing dates — naïve
    ``str(v)[:10]`` produces ``"None"`` which trips parsers downstream. Filter
    those out plus the usual ``""`` / ``"-"`` sentinels.
    """
    if v in (None, "", "-"):
        return ""
    s = str(v)[:10]
    if s in ("None", "NaT", "nan"):
        return ""
    return s


# Holder count change — quarterly snapshot
def _parse_holder(rows: list[dict], qcode: str) -> pd.DataFrame:
    out = []
    for r in rows:
        end_date = _safe_date_str(r.get("END_DATE"))
        if not end_date:
            continue
        out.append({
            "code": qcode,
            "end_date": end_date,
            "holder_num": int(_safe_float(r.get("HOLDER_NUM"))),
            "pre_holder_num": int(_safe_float(r.get("PRE_HOLDER_NUM"))),
            "change_num": int(_safe_float(r.get("HOLDER_NUM_CHANGE"))),
            "change_ratio": _safe_float(r.get("HOLDER_NUM_RATIO")),    # QoQ %
            "interval_chrate": _safe_float(r.get("INTERVAL_CHRATE")),  # %
            "avg_free_shares": _safe_float(r.get("AVG_FREE_SHARES")),
        })
    df = pd.DataFrame(out)
    if len(df) > 0:
        df["end_date"] = pd.to_datetime(df["end_date"], errors="coerce").dt.date
        df = df.dropna(subset=["end_date"]).reset_index(drop=True)
    return df


# Block trade — single-event rows
def _parse_block(rows: list[dict], qcode: str) -> pd.DataFrame:
    out = []
    for r in rows:
        trade_date = _safe_date_str(r.get("TRADE_DATE"))
        if not trade_date:
            continue
        close = _safe_float(r.get("CLOSE_PRICE"))
        deal_price = _safe_float(r.get("DEAL_PRICE"))
        premium = ((deal_price / close - 1) * 100) if close else 0.0
        out.append({
            "code": qcode,
            "trade_date": trade_date,
            "deal_price": deal_price,
            "close_price": close,
            "premium_pct": round(premium, 2),
            "deal_volume": _safe_float(r.get("DEAL_VOLUME")),
            "deal_amount": _safe_float(r.get("DEAL_AMT")),
            "buyer": r.get("BUYER_NAME", "") or "",
            "seller": r.get("SELLER_NAME", "") or "",
        })
    df = pd.DataFrame(out)
    if len(df) > 0:
        df["trade_date"] = pd.to_datetime(df["trade_date"], errors="coerce").dt.date
        df = df.dropna(subset=["trade_date"]).reset_index(drop=True)
    return df


# Dividend — one row per ex-dividend date
def _parse_dividend(rows: list[dict], qcode: str) -> pd.DataFrame:
    out = []
    for r in rows:
        ex_date = _safe_date_str(r.get("EX_DIVIDEND_DATE"))
        if not ex_date:
            continue
        out.append({
            "code": qcode,
            "ex_dividend_date": ex_date,
            "bonus_rmb": _safe_float(r.get("PRETAX_BONUS_RMB")),  # per share, pre-tax
            "bonus_ratio": _safe_float(r.get("BONUS_RATIO")),      # 送股 per 10
            "transfer_ratio": _safe_float(r.get("TRANSFER_RATIO")),# 转增 per 10
            "plan": r.get("ASSIGN_PROGRESS", "") or "",
        })
    df = pd.DataFrame(out)
    if len(df) > 0:
        df["ex_dividend_date"] = pd.to_datetime(df["ex_dividend_date"],
                                                 errors="coerce").dt.date
        df = df.dropna(subset=["ex_dividend_date"]).reset_index(drop=True)
    return df


# (report_name, file_name, dedup_cols, parse_fn, filter_field, sort_col)
_STREAMS = {
    "holder": ("RPT_HOLDERNUMLATEST", "holder_change.parquet",
               ["code", "end_date"], _parse_holder,
               "SECURITY_CODE", "END_DATE"),
    "block":  ("RPT_DATA_BLOCKTRADE", "block_trade.parquet",
               ["code", "trade_date", "buyer", "seller", "deal_price"],
               _parse_block, "SECURITY_CODE", "TRADE_DATE"),
    "dividend": ("RPT_SHAREBONUS_DET", "dividend.parquet",
                 ["code", "ex_dividend_date", "plan"],
                 _parse_dividend, "SECURITY_CODE", "EX_DIVIDEND_DATE"),
}


# ──────────────────────── one-stream worker ────────────────────────


def _refresh_one_stream(
    stream_key: str,
    codes_list: list[str],
    parquet_root: Path,
    page_size: int,
    rate_sleep: float,
    progress: bool,
    sess: requests.Session,
) -> dict:
    report, fname, dedup_cols, parse_fn, filter_field, sort_col = _STREAMS[stream_key]
    path = parquet_root / fname
    old_df = pd.read_parquet(path) if path.exists() else pd.DataFrame()

    if progress:
        print(f"[corp/{stream_key}] {len(codes_list)} codes ...", flush=True)

    new_frames: list[pd.DataFrame] = []
    with_data = 0
    t0 = time.time()

    for i, qcode in enumerate(codes_list, 1):
        dcode = _qlib_to_dcode(qcode)
        rows = datacenter_query(
            report, filter_str=f'({filter_field}="{dcode}")',
            page_size=page_size, sort_columns=sort_col, sort_types="-1",
            session=sess,
        )
        if rows:
            df = parse_fn(rows, qcode)
            if len(df) > 0:
                new_frames.append(df)
                with_data += 1

        if progress and (i % 100 == 0 or i == len(codes_list)):
            elapsed = time.time() - t0
            eta = (len(codes_list) - i) / i * elapsed if i > 0 else 0
            print(f"  {stream_key}: {i}/{len(codes_list)} with_data={with_data} "
                  f"ETA {eta:.0f}s", flush=True)
        time.sleep(rate_sleep)

    if new_frames:
        new_df = pd.concat(new_frames, ignore_index=True)
        combined = (pd.concat([old_df, new_df], ignore_index=True)
                    if len(old_df) > 0 else new_df)
        combined = (combined.drop_duplicates(subset=dedup_cols, keep="last")
                            .sort_values(dedup_cols).reset_index(drop=True))
        tmp = path.with_suffix(".tmp")
        combined.to_parquet(tmp, index=False)
        if path.exists():
            path.unlink()
        tmp.rename(path)
        n_total = len(combined)
    else:
        n_total = len(old_df)

    if progress:
        print(f"[corp/{stream_key} ✓] with_data={with_data}/{len(codes_list)} "
              f"rows={n_total} 耗时 {time.time()-t0:.1f}s", flush=True)

    return {"codes_with_data": with_data, "rows_total": n_total,
            "parquet_path": str(path)}


# ──────────────────────── public API ────────────────────────


def update_corporate_actions(
    parquet_root: Union[str, Path],
    codes: Iterable[str],
    include: Optional[Set[str]] = None,
    page_size: int = 20,
    rate_sleep: float = 0.15,
    dry_run: bool = False,
    progress: bool = True,
) -> dict:
    """Refresh 1-3 corporate-action streams in one pass.

    ``include`` selects which sub-streams to run — defaults to all three
    (``{"holder", "block", "dividend"}``). Per-stock per-stream call is one
    datacenter request, so 3 streams × N codes × ~0.3s ≈ N seconds.
    """
    include = include or {"holder", "block", "dividend"}
    bad = include - set(_STREAMS)
    if bad:
        raise ValueError(f"Unknown corporate-action stream(s): {bad}. "
                         f"Choose from {set(_STREAMS)}.")

    codes_list = [c.strip().upper() for c in codes if c and c.strip()]
    parquet_root = Path(parquet_root)

    if dry_run:
        return {
            "dry_run": True, "ok": True,
            "codes_total": len(codes_list),
            "streams": sorted(include),
            "plan": (f"Would pull {len(include)} streams ({sorted(include)}) "
                     f"for {len(codes_list)} codes from 东财 datacenter, "
                     f"write 3 separate parquets under {parquet_root}"),
        }

    if not codes_list:
        return {"ok": True, "codes_total": 0, "streams": sorted(include),
                "results": {}}

    parquet_root.mkdir(parents=True, exist_ok=True)

    results: dict = {}
    with requests.Session() as sess:
        for key in sorted(include):
            results[key] = _refresh_one_stream(
                key, codes_list, parquet_root,
                page_size=page_size, rate_sleep=rate_sleep,
                progress=progress, sess=sess,
            )

    return {
        "ok": any(r["codes_with_data"] > 0 for r in results.values()),
        "codes_total": len(codes_list),
        "streams": sorted(include),
        "results": results,
    }
