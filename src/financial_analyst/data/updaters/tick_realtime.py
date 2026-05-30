"""TDX 今日实时分笔 (tick_realtime) updater — zero-token via pytdx direct.

Fetches per-stock today's real-time tick data using ``pytdx.get_transaction_data``
and writes to a single consolidated ``tick_realtime.parquet`` under ``parquet_root``.

Semantics: **overwrite today's rows** (last call wins). The parquet keeps only
today's snapshot per code — historical days should have been moved to
``tick_history.parquet``. Cross-stock rows from *other* dates are preserved.

Wire it from ``fa data update --include-tick-realtime`` (see ``data_cli.update_cmd``).

API::

    >>> from financial_analyst.data.updaters.tick_realtime import update_tick_realtime
    >>> from financial_analyst.data.paths import get_data_paths
    >>> p = get_data_paths()
    >>> stats = update_tick_realtime(
    ...     p.parquet_root,
    ...     ["SH600519", "SZ002594"],
    ... )
    >>> stats
    {'total': 2, 'ok': 2, 'failed': 0, 'new_rows': 4800,
     'output_path': '.../parquet/tick_realtime.parquet'}

Key differences from tick_history:
  - No ``date`` param — implicit today (``datetime.now().date()``).
  - ``num`` field is populated (number of trades per tick batch).
  - API: ``get_transaction_data(mkt, code_num, start, count)`` — no date_int arg.
  - Merge pattern: drop (code × today) rows before append (not (code, date) pairs).

⚠ Volume unit (vol): pytdx ``get_transaction_data`` returns ``vol`` already in
**手** (lots, 1 手 = 100 股) — following the same convention as
``tick_history.py`` (confirmed from Phase 0 recon; see _VOL_ALREADY_IN_LOTS).

Zero-token: pytdx connects directly to broker quote hosts, no registration.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime
from pathlib import Path
from typing import List, Union

import pandas as pd

from financial_analyst.data.updaters.pytdx_pool import PytdxClient, qlib_code_to_pytdx

# Bypass system proxy (Clash intercepts localhost/internal — same pattern as f10.py)
os.environ["NO_PROXY"] = "*"
os.environ["no_proxy"] = "*"

log = logging.getLogger(__name__)

# ──────────────────────── vol unit toggle ─────────────────────────────────────
# Following tick_history.py precedent: Phase 0 recon evidence shows hist tick
# vol values (8, 17, 4, 52, 204 手 etc.) are already in 手 units.
# Realtime tick uses the same pytdx quote subsystem, so the same unit applies.
# Set to False if evidence ever shows otherwise.
_VOL_ALREADY_IN_LOTS: bool = True

# ──────────────────────── schema ──────────────────────────────────────────────
# §4.x tick_realtime.parquet field contract (8 columns).
# Same schema as tick_history.parquet; `num` is populated here (today-tick).
# Primary key: (code, date, time, idx)

TICK_FIELDS = [
    # fmt: off
    "code",        # str   "SH600519"
    "date",        # str   "YYYY-MM-DD"  — today's date when fetched (from datetime.now())
    "time",        # str   "HH:MM"       — pytdx native format
    "price",       # float 元
    "vol",         # int   手 (lots)
    "num",         # int   笔 — populated for realtime ticks (differentiator vs hist)
    "buyorsell",   # int   1=买 2=卖 5=不详
    "idx",         # int   per-code sequence index, 0-based
    # fmt: on
]


# ──────────────────────── helpers ─────────────────────────────────────────────


def _today_str() -> str:
    """Return today's date as 'YYYY-MM-DD' string (local wall clock)."""
    return datetime.now().strftime("%Y-%m-%d")


def _load_existing_dropping_today(output_path: Path, codes: List[str], today: str) -> pd.DataFrame:
    """Read existing parquet and drop (code × today) rows for the requested codes.

    Returns the surviving DataFrame (may be empty). Called before appending new
    fetched rows so that repeated calls replace rather than duplicate today's data.

    Other codes' rows (including today) and other dates' rows are preserved.
    """
    if not output_path.exists():
        return pd.DataFrame(columns=TICK_FIELDS)
    try:
        df = pd.read_parquet(output_path)
    except Exception as exc:
        log.warning("tick_realtime: could not read existing parquet (%s) — treating as empty.", exc)
        return pd.DataFrame(columns=TICK_FIELDS)

    if df.empty:
        return df

    # Drop rows where both code ∈ codes AND date == today
    codes_set = set(codes)
    mask_drop = df["code"].isin(codes_set) & (df["date"] == today)
    return df[~mask_drop].reset_index(drop=True)


def _row_to_dict(code: str, date: str, raw: dict, idx: int) -> dict:
    """Normalise a single pytdx realtime-tick row to the tick_realtime schema.

    pytdx realtime row keys: ``time``, ``price``, ``vol``, ``num``, ``buyorsell``.
    ``num`` is the trade-count batch for this tick (only present in today's API).
    """
    raw_vol = raw.get("vol", 0) or 0
    if _VOL_ALREADY_IN_LOTS:
        vol_lots = int(raw_vol)
    else:
        # If future evidence shows vol is in 股, divide by 100
        vol_lots = int(raw_vol) // 100

    raw_num = raw.get("num", None)
    num_val = int(raw_num) if raw_num is not None else 0  # defensive default

    return {
        "code": code,
        "date": date,
        "time": str(raw.get("time", "")),
        "price": float(raw.get("price", 0.0) or 0.0),
        "vol": vol_lots,
        "num": num_val,
        "buyorsell": int(raw.get("buyorsell", 5) or 5),
        "idx": idx,
    }


# ──────────────────────── public API ──────────────────────────────────────────


def update_tick_realtime(
    parquet_root: Union[Path, str],
    codes: List[str],
    *,
    rows_per_call: int = 2000,
    log_progress: bool = True,
) -> dict:
    """Fetch today's real-time tick data per code → parquet_root/tick_realtime.parquet.

    **Semantics**: overwrite today's rows (last call wins). The parquet keeps
    today's snapshot only — historical days should be moved to tick_history.

    Parameters
    ----------
    parquet_root
        Root directory where ``tick_realtime.parquet`` will be written.
        Caller typically passes ``get_data_paths().parquet_root``.
    codes
        Qlib-format codes, e.g. ``["SH600519", "SZ002594"]``.
    rows_per_call
        Maximum rows requested per pytdx call. pytdx hard limit is 2000.
        If a call returns ``rows_per_call`` rows, a subsequent call is made
        with ``start += rows_per_call`` to fetch remaining ticks.
    log_progress
        Print progress when processing each code and warn on failures.

    Returns
    -------
    dict
        ``{'total': N_codes, 'ok': N, 'failed': N, 'new_rows': N,
           'output_path': str}``

    Notes
    -----
    vol unit: pytdx ``get_transaction_data`` returns vol already in 手.
    See module docstring and ``_VOL_ALREADY_IN_LOTS`` toggle.

    num field: pytdx today-tick includes ``num`` (trade count per batch).
    Missing/None values default to 0 (defensive).
    """
    parquet_root = Path(parquet_root)
    output_path = parquet_root / "tick_realtime.parquet"

    if not codes:
        return {
            "total": 0,
            "ok": 0,
            "failed": 0,
            "new_rows": 0,
            "output_path": str(output_path),
        }

    today = _today_str()
    parquet_root.mkdir(parents=True, exist_ok=True)

    # Load existing parquet, dropping (codes × today) rows to enable refresh
    existing_df = _load_existing_dropping_today(output_path, codes, today)

    total = len(codes)
    ok = failed = 0
    new_rows: List[dict] = []

    if log_progress:
        print(f"[TICK-RT] connecting to TDX...", flush=True)

    with PytdxClient() as client:
        if log_progress:
            print(
                f"[TICK-RT] connected, fetching today ({today}) ticks for {total} codes",
                flush=True,
            )

        for i, code in enumerate(codes):
            try:
                mkt, code_num = qlib_code_to_pytdx(code)

                # Fetch all ticks for this code (today), paginating if needed
                all_raw: list = []
                start = 0
                while True:
                    chunk = client.call(
                        "get_transaction_data",
                        mkt,
                        code_num,
                        start,
                        rows_per_call,
                    )
                    if not chunk:
                        break
                    all_raw.extend(chunk)
                    if len(chunk) < rows_per_call:
                        # Fewer than max returned → no more pages
                        break
                    start += rows_per_call

            except Exception as exc:
                failed += 1
                if log_progress:
                    log.warning("[TICK-RT] %s failed: %s", code, exc)
                continue

            # Convert rows
            for idx_within_code, raw in enumerate(all_raw):
                new_rows.append(_row_to_dict(code, today, raw, idx_within_code))
            ok += 1

            if log_progress and (i + 1) % 50 == 0:
                print(
                    f"  [TICK-RT] {i+1}/{total} ok={ok} fail={failed} "
                    f"new_rows={len(new_rows)}",
                    flush=True,
                )

    # ── Merge new rows with surviving existing rows ────────────────────────────
    frames: List[pd.DataFrame] = []

    if len(existing_df) > 0:
        frames.append(existing_df)

    if new_rows:
        new_df = pd.DataFrame(new_rows, columns=TICK_FIELDS)
        frames.append(new_df)

    if frames:
        out_df = pd.concat(frames, ignore_index=True)
        out_df.to_parquet(output_path, index=False)
        if log_progress:
            print(f"[TICK-RT] wrote {len(out_df)} rows → {output_path}", flush=True)
    else:
        if log_progress:
            log.warning("[TICK-RT] no rows to write (all codes failed or returned empty)")

    return {
        "total": total,
        "ok": ok,
        "failed": failed,
        "new_rows": len(new_rows),
        "output_path": str(output_path),
    }
