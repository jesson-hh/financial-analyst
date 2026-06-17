"""Margin trading (融资融券) per-stock daily updater — zero-token via 东财.

Pulls `RPTA_WEB_RZRQ_GGMX` (个股融资融券明细 daily). One row per
(code, date) ≈ 250 rows/year/stock × N stocks. Writes long-format Parquet
``margin_trading.parquet`` under ``parquet_root``.

Schema:

    code        str    qlib upper-case
    date        date   trading day
    rzye        float  融资余额 (元)
    rzmre       float  融资买入额 (元)
    rzche       float  融资偿还额 (元)
    rqye        float  融券余额 (元)
    rqmcl       float  融券卖出量 (股)
    rqchl       float  融券偿还量 (股)
    rzrqye      float  融资融券余额合计 (元)

Inspired by ``simonlin1212/a-stock-data`` v3.1 endpoint discovery; this is a
fresh implementation in fa's updater style (Session pooling, atomic merge,
``paths.py``/``last_update`` integration).
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Iterable, Union

import pandas as pd
import requests

from financial_analyst.data.updaters._eastmoney_dc import datacenter_query


_REPORT = "RPTA_WEB_RZRQ_GGMX"

# 东财字段 → 我们的 schema (lower-case, 跟 fund_flow 风格一致)
_FIELD_MAP = {
    "DATE": "date",
    "RZYE": "rzye",
    "RZMRE": "rzmre",
    "RZCHE": "rzche",
    "RQYE": "rqye",
    "RQMCL": "rqmcl",
    "RQCHL": "rqchl",
    "RZRQYE": "rzrqye",
}
_DEDUP_COLS = ["code", "date"]


def _qlib_to_dcode(code: str) -> str:
    """Strip ``SH``/``SZ``/``BJ`` prefix — datacenter SCODE field is 6-digit only."""
    c = code.strip().upper()
    if c[:2] in ("SH", "SZ", "BJ"):
        return c[2:]
    return c


def _parse_rows(rows: list[dict], qlib_code: str) -> pd.DataFrame:
    out: list[dict] = []
    for r in rows:
        rec = {"code": qlib_code}
        for src, dst in _FIELD_MAP.items():
            v = r.get(src)
            if dst == "date":
                rec["date"] = str(v)[:10] if v else None
            else:
                rec[dst] = float(v) if v not in (None, "") else 0.0
        if rec["date"]:
            out.append(rec)
    if not out:
        return pd.DataFrame()
    df = pd.DataFrame(out)
    df["date"] = pd.to_datetime(df["date"]).dt.date
    return df


def update_margin_trading(
    parquet_root: Union[str, Path],
    codes: Iterable[str],
    page_size: int = 30,
    rate_sleep: float = 0.15,
    checkpoint_every: int = 200,
    dry_run: bool = False,
    progress: bool = True,
) -> dict:
    """Pull margin balance history for ``codes``, merge into ``margin_trading.parquet``."""
    codes_list = [c.strip().upper() for c in codes if c and c.strip()]
    parquet_root = Path(parquet_root)
    parquet_path = parquet_root / "margin_trading.parquet"

    if dry_run:
        n = 0
        if parquet_path.exists():
            try:
                n = len(pd.read_parquet(parquet_path, columns=["code"]))
            except Exception:
                pass
        return {
            "dry_run": True, "ok": True,
            "codes_total": len(codes_list), "codes_ok": 0, "codes_failed": 0,
            "rows_new": 0, "rows_total": n,
            "plan": (f"Would pull last {page_size} trading days margin balance "
                     f"for {len(codes_list)} codes from 东财 datacenter, "
                     f"merge into {parquet_path}"),
            "parquet_path": str(parquet_path),
        }

    if not codes_list:
        return {"ok": True, "codes_total": 0, "codes_ok": 0, "codes_failed": 0,
                "rows_new": 0, "rows_total": 0, "errors": [],
                "parquet_path": str(parquet_path)}

    parquet_root.mkdir(parents=True, exist_ok=True)
    old_df = pd.read_parquet(parquet_path) if parquet_path.exists() else pd.DataFrame()

    if progress:
        print(f"[margin] fetching {len(codes_list)} codes ...", flush=True)

    new_frames: list[pd.DataFrame] = []
    ok = failed = 0
    errors: list[dict] = []
    t0 = time.time()

    with requests.Session() as sess:
        for i, qcode in enumerate(codes_list, 1):
            dcode = _qlib_to_dcode(qcode)
            rows = datacenter_query(
                _REPORT, filter_str=f'(SCODE="{dcode}")',
                page_size=page_size, sort_columns="DATE", sort_types="-1",
                session=sess,
            )
            df = _parse_rows(rows, qcode) if rows else pd.DataFrame()
            if len(df) > 0:
                new_frames.append(df)
                ok += 1
            else:
                failed += 1
                if len(errors) < 20:
                    errors.append({"code": qcode, "reason": "empty"})

            if progress and (i % 50 == 0 or i == len(codes_list)):
                elapsed = time.time() - t0
                eta = (len(codes_list) - i) / i * elapsed if i > 0 else 0
                rows_so_far = sum(len(f) for f in new_frames)
                print(f"  {i}/{len(codes_list)} ok={ok} fail={failed} "
                      f"rows={rows_so_far} ETA {eta:.0f}s", flush=True)

            if (i % checkpoint_every == 0) and new_frames:
                old_df = _flush(new_frames, old_df, parquet_path)
                new_frames = []
            time.sleep(rate_sleep)

    if new_frames:
        old_df = _flush(new_frames, old_df, parquet_path)

    try:
        final = pd.read_parquet(parquet_path)
        n_total = len(final)
        date_range = (f"{final['date'].min()} → {final['date'].max()}"
                      if n_total > 0 else "n/a")
    except Exception:
        n_total = 0
        date_range = "n/a"

    if progress:
        print(f"[margin ✓] ok={ok}/{len(codes_list)} fail={failed} "
              f"rows={n_total} ({date_range}) 耗时 {time.time()-t0:.1f}s", flush=True)

    return {
        "ok": ok > 0,
        "codes_total": len(codes_list), "codes_ok": ok, "codes_failed": failed,
        "rows_total": n_total, "date_range": date_range,
        "errors": errors, "parquet_path": str(parquet_path),
    }


def _flush(new_frames: list[pd.DataFrame], old_df: pd.DataFrame,
           path: Path) -> pd.DataFrame:
    """Merge new with old, dedup on (code, date), atomic write. Returns new old_df."""
    new_df = pd.concat(new_frames, ignore_index=True)
    combined = (pd.concat([old_df, new_df], ignore_index=True)
                if len(old_df) > 0 else new_df)
    combined = (combined.drop_duplicates(subset=_DEDUP_COLS, keep="last")
                        .sort_values(_DEDUP_COLS).reset_index(drop=True))
    tmp = path.with_suffix(".tmp")
    combined.to_parquet(tmp, index=False)
    if path.exists():
        path.unlink()
    tmp.rename(path)
    return combined
