"""TDX XDXR (复权) updater — zero-token via pytdx direct.

Fetches per-stock 除权除息 event history using ``pytdx.get_xdxr_info`` and
writes a single consolidated ``xdxr.parquet`` under ``parquet_root``.  The
full history per stock is small (typically 10–150 rows), so we do a
**full-replace per code** rather than event-level append: simpler, no
primary-key dedup headaches.

Wire it from ``fa data update --include-xdxr`` (see ``data_cli.update_cmd``).

API::

    >>> from financial_analyst.data.updaters.xdxr import update_xdxr
    >>> from financial_analyst.data.paths import get_data_paths
    >>> p = get_data_paths()
    >>> stats = update_xdxr(p.parquet_root, ["SH600519", "SZ002594"])
    >>> stats
    {'total': 2, 'ok': 2, 'failed': 0, 'new_rows': 87,
     'output_path': '.../parquet/xdxr.parquet'}

Zero-token: pytdx connects directly to broker quote hosts (招商证券 / 东兴 /
华泰 等), no registration. Same network path as ``pytdx_kline`` / ``f10``.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Union

import pandas as pd

from financial_analyst.data.updaters.pytdx_pool import PytdxClient, qlib_code_to_pytdx

# Bypass system proxy (Clash intercepts localhost/internal — same pattern as f10.py)
os.environ["NO_PROXY"] = "*"
os.environ["no_proxy"] = "*"

log = logging.getLogger(__name__)

# ──────────────────────── schema ──────────────────────────────────────────────
# §4.1 xdxr.parquet field contract (18 columns).
# Primary key: (code, event_date, category).
# Increment strategy: full-replace per code (history small ~150 rows/stock max).

XDXR_FIELDS = [
    # fmt: off
    "code",             # str   "SH600519"
    "year",             # int   e.g. 2021
    "month",            # int   1-12
    "day",              # int   1-31
    "event_date",       # str   "YYYY-MM-DD" derived from year/month/day; None if year<1990
    "category",         # int   1=除权除息 2=送配股上市 3=配股缴款 5=股本变化 9/14=其他
    "fenhong",          # float 元/股  cash dividend
    "peigujia",         # float 元/股  rights price
    "songzhuangu",      # float 股/股  bonus/transfer share ratio
    "peigu",            # float 股/股  rights issue ratio
    "suogu",            # float        reverse split ratio
    "panqianliutong",   # float 股     pre-event circulation shares
    "panhouliutong",    # float 股     post-event circulation shares
    "qianzongguben",    # float 股     pre-event total share capital
    "houzongguben",     # float 股     post-event total share capital
    "fenshu",           # float        share change qty / direction
    "xingquanjia",      # float 元/股  option strike price
    "name",             # str          event description (Chinese)
    # fmt: on
]


# ──────────────────────── helpers ─────────────────────────────────────────────


def _safe_float(v) -> float:
    """Coerce to float; return 0.0 for None / empty / non-numeric."""
    if v is None or v == "" or v == "-":
        return 0.0
    try:
        return float(v)
    except (ValueError, TypeError):
        return 0.0


def _safe_int(v, default: int = 0) -> int:
    try:
        return int(v) if v is not None else default
    except (ValueError, TypeError):
        return default


def _row_to_dict(code: str, raw: dict) -> dict:
    """Normalise a single pytdx OrderedDict row to the xdxr schema.

    pytdx returns OrderedDicts with snake_case keys matching the field names
    but may omit keys entirely — use `.get(key, None)` everywhere.
    """
    year = _safe_int(raw.get("year"), 0)
    month = _safe_int(raw.get("month"), 0)
    day = _safe_int(raw.get("day"), 0)

    # Sanity guard: pytdx occasionally returns year=0 for stub rows
    if year >= 1990 and 1 <= month <= 12 and 1 <= day <= 31:
        event_date: str | None = f"{year:04d}-{month:02d}-{day:02d}"
    else:
        event_date = None

    return {
        "code": code,
        "year": year,
        "month": month,
        "day": day,
        "event_date": event_date,
        "category": _safe_int(raw.get("category"), 0),
        "fenhong": _safe_float(raw.get("fenhong")),
        "peigujia": _safe_float(raw.get("peigujia")),
        "songzhuangu": _safe_float(raw.get("songzhuangu")),
        "peigu": _safe_float(raw.get("peigu")),
        "suogu": _safe_float(raw.get("suogu")),
        "panqianliutong": _safe_float(raw.get("panqianliutong")),
        "panhouliutong": _safe_float(raw.get("panhouliutong")),
        "qianzongguben": _safe_float(raw.get("qianzongguben")),
        "houzongguben": _safe_float(raw.get("houzongguben")),
        "fenshu": _safe_float(raw.get("fenshu")),
        "xingquanjia": _safe_float(raw.get("xingquanjia")),
        "name": str(raw.get("name") or ""),
    }


# ──────────────────────── public API ──────────────────────────────────────────


def update_xdxr(
    parquet_root: Union[Path, str],
    codes: list[str],
    *,
    log_progress: bool = True,
) -> dict:
    """Update ``parquet_root/xdxr.parquet`` with XDXR (复权) events for given codes.

    Walks pytdx ``get_xdxr_info`` per code, normalises 18 fields per spec §4.1,
    writes one consolidated parquet (full replace per code, no append-by-event).

    Parameters
    ----------
    parquet_root
        Root directory where ``xdxr.parquet`` will be written.
        Caller typically passes ``get_data_paths().parquet_root``.
    codes
        Qlib-format codes, e.g. ``["SH600519", "SZ002594"]``.
    log_progress
        Print progress every 50 codes and warn on per-code failures.

    Returns
    -------
    dict
        ``{'total': N, 'ok': N, 'failed': N, 'new_rows': N, 'output_path': str}``
    """
    parquet_root = Path(parquet_root)
    output_path = parquet_root / "xdxr.parquet"

    if not codes:
        return {
            "total": 0,
            "ok": 0,
            "failed": 0,
            "new_rows": 0,
            "output_path": str(output_path),
        }

    parquet_root.mkdir(parents=True, exist_ok=True)

    # Load existing parquet; keep rows for codes NOT in this refresh batch
    existing_df: pd.DataFrame | None = None
    if output_path.exists():
        try:
            existing_df = pd.read_parquet(output_path)
        except Exception as exc:
            log.warning("xdxr.parquet read failed (%s) — will overwrite.", exc)
            existing_df = None

    refreshing_set = set(codes)

    ok = failed = 0
    new_rows: list[dict] = []

    if log_progress:
        print(f"[XDXR] connecting to TDX...", flush=True)

    with PytdxClient() as client:
        if log_progress:
            print(f"[XDXR] connected, fetching {len(codes)} codes", flush=True)

        for i, code in enumerate(codes):
            try:
                mkt, code_num = qlib_code_to_pytdx(code)
                raw_list = client.call("get_xdxr_info", mkt, code_num)
            except Exception as exc:
                failed += 1
                if log_progress:
                    log.warning("[XDXR] %s failed: %s", code, exc)
                continue

            if not raw_list:
                # Normal for very new listings or delisted stocks with no events
                failed += 1
                if log_progress:
                    log.warning("[XDXR] %s returned empty — skipping", code)
                continue

            for raw in raw_list:
                new_rows.append(_row_to_dict(code, raw))
            ok += 1

            if log_progress and (i + 1) % 50 == 0:
                print(f"  [XDXR] {i+1}/{len(codes)} ok={ok} fail={failed}", flush=True)

    # Merge: keep existing rows for OTHER codes + all new rows for refreshed codes
    frames: list[pd.DataFrame] = []
    if existing_df is not None and len(existing_df) > 0:
        others = existing_df[~existing_df["code"].isin(refreshing_set)]
        if len(others) > 0:
            frames.append(others)

    if new_rows:
        frames.append(pd.DataFrame(new_rows, columns=XDXR_FIELDS))

    if frames:
        out_df = pd.concat(frames, ignore_index=True)
        out_df.to_parquet(output_path, index=False)
        if log_progress:
            print(f"[XDXR] wrote {len(out_df)} rows → {output_path}", flush=True)
    else:
        if log_progress:
            log.warning("[XDXR] no rows to write (all codes failed or empty)")

    return {
        "total": len(codes),
        "ok": ok,
        "failed": failed,
        "new_rows": len(new_rows),
        "output_path": str(output_path),
    }
