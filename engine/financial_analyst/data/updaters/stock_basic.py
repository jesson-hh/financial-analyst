"""Tushare ``stock_basic`` snapshot updater — refresh A-share company master list.

One Tushare API call → ``tushare_stock_basic.parquet`` (5500+ rows × 6 cols).
Snapshot semantics: full overwrite each refresh (the source is authoritative
and idempotent — no merge logic needed).

Columns: ``ts_code, name, area, industry, market, list_date``.

**Tushare opt-in**: requires ``FA_TUSHARE_TOKEN`` env or ``--tushare-token``.

API::

    >>> from financial_analyst.data.updaters.stock_basic import update_stock_basic
    >>> stats = update_stock_basic("/path/to/parquet", tushare_token="...")
    >>> stats
    {'ok': True, 'rows': 5502, 'parquet_path': '.../tushare_stock_basic.parquet'}
"""
from __future__ import annotations

from pathlib import Path
from typing import Union

import pandas as pd
import requests


TUSHARE_URL = "http://api.tushare.pro"
FIELDS = "ts_code,name,area,industry,market,list_date"


def update_stock_basic(
    parquet_root: Union[str, Path],
    tushare_token: str,
    dry_run: bool = False,
    progress: bool = True,
) -> dict:
    """Pull ``stock_basic`` from Tushare, write to ``tushare_stock_basic.parquet``.

    Parameters
    ----------
    parquet_root
        Directory holding ``tushare_stock_basic.parquet`` directly.
    tushare_token
        Tushare Pro API token (https://tushare.pro). The stock_basic interface
        is on the free tier.
    dry_run
        Plan-only mode.
    progress
        Print one-line status.

    Returns
    -------
    dict
        ``{ok, rows, parquet_path}`` on success;
        ``{ok: False, error: ..., parquet_path}`` on failure.
    """
    if not tushare_token:
        raise RuntimeError(
            "tushare_token required. Set FA_TUSHARE_TOKEN env or pass --tushare-token. "
            "Get one free at https://tushare.pro/."
        )

    parquet_root = Path(parquet_root)
    parquet_path = parquet_root / "tushare_stock_basic.parquet"

    if dry_run:
        return {
            "dry_run": True,
            "ok": True,
            "rows": 0,
            "plan": f"Would pull Tushare stock_basic → {parquet_path}",
            "parquet_path": str(parquet_path),
        }

    if progress:
        print(f"[stock_basic] pulling Tushare stock_basic ...", flush=True)

    req = {
        "api_name": "stock_basic",
        "token": tushare_token,
        "params": {"exchange": "", "list_status": "L"},
        "fields": FIELDS,
    }
    try:
        r = requests.post(TUSHARE_URL, json=req, timeout=30)
        d = r.json()
        if d.get("code") != 0:
            return {
                "ok": False,
                "rows": 0,
                "error": f"Tushare business error: {d.get('msg', 'unknown')}",
                "parquet_path": str(parquet_path),
            }
    except requests.exceptions.RequestException as e:
        return {
            "ok": False,
            "rows": 0,
            "error": f"Tushare network error: {e}",
            "parquet_path": str(parquet_path),
        }

    items = d["data"]["items"]
    cols = d["data"]["fields"]
    df = pd.DataFrame(items, columns=cols)

    # Atomic write
    parquet_root.mkdir(parents=True, exist_ok=True)
    tmp = parquet_path.with_suffix(".tmp")
    df.to_parquet(tmp, index=False)
    if parquet_path.exists():
        parquet_path.unlink()
    tmp.rename(parquet_path)

    if progress:
        print(f"[stock_basic] wrote {len(df):,} rows → {parquet_path.name}", flush=True)

    return {
        "ok": True,
        "rows": len(df),
        "parquet_path": str(parquet_path),
    }
