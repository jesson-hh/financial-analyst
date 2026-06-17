"""Lockup expiry (限售解禁) calendar updater — zero-token via 东财.

Pulls ``RPT_LIFT_STAGE`` per stock (历史 + 未来 90 天). Idempotent re-runs
update the ``status`` column (history vs upcoming) as time passes.

Schema (one row per (code, free_date, type)):

    code           str    qlib upper-case
    free_date      date   解禁日
    type           str    限售股类型 (e.g. 首发原股东限售)
    shares         float  解禁股数
    ratio          float  占总股本比例 (%)
    status         str    'history' / 'upcoming' / 'today'

Inspired by ``simonlin1212/a-stock-data`` v3.1 endpoint discovery.
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable, Union

import pandas as pd
import requests

from financial_analyst.data.updaters._eastmoney_dc import datacenter_query


_REPORT = "RPT_LIFT_STAGE"
_DEDUP_COLS = ["code", "free_date", "type"]


def _qlib_to_dcode(code: str) -> str:
    c = code.strip().upper()
    return c[2:] if c[:2] in ("SH", "SZ", "BJ") else c


def _parse_rows(rows: list[dict], qlib_code: str, today: datetime) -> pd.DataFrame:
    out: list[dict] = []
    for r in rows:
        free_date_str = str(r.get("FREE_DATE", ""))[:10]
        if not free_date_str:
            continue
        try:
            fd = datetime.strptime(free_date_str, "%Y-%m-%d")
        except ValueError:
            continue
        if fd > today:
            status = "upcoming"
        elif fd.date() == today.date():
            status = "today"
        else:
            status = "history"
        out.append({
            "code": qlib_code,
            "free_date": fd.date(),
            "type": r.get("LIMITED_STOCK_TYPE", "") or "",
            "shares": float(r.get("FREE_SHARES_NUM") or 0),
            "ratio": float(r.get("FREE_RATIO") or 0),
            "status": status,
        })
    return pd.DataFrame(out) if out else pd.DataFrame()


def update_lockup_expiry(
    parquet_root: Union[str, Path],
    codes: Iterable[str],
    forward_days: int = 90,
    history_size: int = 15,
    rate_sleep: float = 0.15,
    checkpoint_every: int = 200,
    dry_run: bool = False,
    progress: bool = True,
) -> dict:
    """Pull lockup-expiry calendar for ``codes``, merge into ``lockup_expiry.parquet``.

    Each stock gets two filter queries — recent ``history_size`` expiries and
    everything within ``forward_days`` ahead. Status column lets downstream
    `risk-officer` agent filter for upcoming risk events without recomputing
    today's date.
    """
    codes_list = [c.strip().upper() for c in codes if c and c.strip()]
    parquet_root = Path(parquet_root)
    parquet_path = parquet_root / "lockup_expiry.parquet"

    if dry_run:
        n = 0
        if parquet_path.exists():
            try:
                n = len(pd.read_parquet(parquet_path, columns=["code"]))
            except Exception:
                pass
        return {
            "dry_run": True, "ok": True,
            "codes_total": len(codes_list), "codes_ok": 0,
            "rows_total": n,
            "plan": (f"Would pull lockup history ({history_size}) + upcoming "
                     f"({forward_days}d) for {len(codes_list)} codes → {parquet_path}"),
            "parquet_path": str(parquet_path),
        }

    if not codes_list:
        return {"ok": True, "codes_total": 0, "codes_ok": 0, "codes_failed": 0,
                "rows_total": 0, "errors": [], "parquet_path": str(parquet_path)}

    parquet_root.mkdir(parents=True, exist_ok=True)
    old_df = pd.read_parquet(parquet_path) if parquet_path.exists() else pd.DataFrame()

    now = datetime.now()
    today_str = now.strftime("%Y-%m-%d")
    end_str = (now + timedelta(days=forward_days)).strftime("%Y-%m-%d")

    if progress:
        print(f"[lockup] fetching {len(codes_list)} codes ...", flush=True)

    new_frames: list[pd.DataFrame] = []
    ok = failed = with_data = 0
    errors: list[dict] = []
    t0 = time.time()

    with requests.Session() as sess:
        for i, qcode in enumerate(codes_list, 1):
            dcode = _qlib_to_dcode(qcode)
            # History
            hist = datacenter_query(
                _REPORT, filter_str=f'(SECURITY_CODE="{dcode}")',
                page_size=history_size, sort_columns="FREE_DATE", sort_types="-1",
                session=sess,
            )
            # Upcoming (forward window)
            upcoming = datacenter_query(
                _REPORT,
                filter_str=(f'(SECURITY_CODE="{dcode}")'
                            f"(FREE_DATE>='{today_str}')"
                            f"(FREE_DATE<='{end_str}')"),
                page_size=20, sort_columns="FREE_DATE", sort_types="1",
                session=sess,
            )
            all_rows = (hist or []) + (upcoming or [])
            df = _parse_rows(all_rows, qcode, now) if all_rows else pd.DataFrame()
            if len(df) > 0:
                new_frames.append(df)
                with_data += 1
            else:
                # Empty for a stock is normal (mature blue chips often have no future lockup)
                if len(errors) < 5:
                    errors.append({"code": qcode, "reason": "no lockup data"})
            ok += 1

            if progress and (i % 100 == 0 or i == len(codes_list)):
                elapsed = time.time() - t0
                eta = (len(codes_list) - i) / i * elapsed if i > 0 else 0
                rows_so_far = sum(len(f) for f in new_frames)
                print(f"  {i}/{len(codes_list)} with_data={with_data} "
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
        n_upcoming = int((final.get("status") == "upcoming").sum()) if "status" in final.columns else 0
    except Exception:
        n_total = n_upcoming = 0

    if progress:
        print(f"[lockup ✓] codes={ok} with_data={with_data} "
              f"rows={n_total} (upcoming={n_upcoming}) "
              f"耗时 {time.time()-t0:.1f}s", flush=True)

    return {
        "ok": True,
        "codes_total": len(codes_list), "codes_ok": ok, "codes_with_data": with_data,
        "rows_total": n_total, "rows_upcoming": n_upcoming,
        "errors": errors, "parquet_path": str(parquet_path),
    }


def _flush(new_frames: list[pd.DataFrame], old_df: pd.DataFrame,
           path: Path) -> pd.DataFrame:
    new_df = pd.concat(new_frames, ignore_index=True)
    combined = (pd.concat([old_df, new_df], ignore_index=True)
                if len(old_df) > 0 else new_df)
    combined = (combined.drop_duplicates(subset=_DEDUP_COLS, keep="last")
                        .sort_values(["code", "free_date"]).reset_index(drop=True))
    tmp = path.with_suffix(".tmp")
    combined.to_parquet(tmp, index=False)
    if path.exists():
        path.unlink()
    tmp.rename(path)
    return combined
