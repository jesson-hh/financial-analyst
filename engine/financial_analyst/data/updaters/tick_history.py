"""TDX 历史分笔 (tick_history) updater — zero-token via pytdx direct.

Fetches per-stock historical tick data using ``pytdx.get_history_transaction_data``
and appends to a single consolidated ``tick_history.parquet`` under ``parquet_root``.

Increment strategy: skip (code, date) pairs already present in the parquet
(checked before each fetch call).  Only newly requested (code, date) pairs
are fetched and appended.

Wire it from ``fa data update --include-tick-history`` (see ``data_cli.update_cmd``).

API::

    >>> from financial_analyst.data.updaters.tick_history import update_tick_history
    >>> from financial_analyst.data.paths import get_data_paths
    >>> p = get_data_paths()
    >>> stats = update_tick_history(
    ...     p.parquet_root,
    ...     ["SH600519", "SZ002594"],
    ...     dates=["2026-05-28", "2026-05-29"],
    ... )
    >>> stats
    {'total': 4, 'ok': 4, 'failed': 0, 'skipped': 0, 'new_rows': 12300,
     'output_path': '.../parquet/tick_history.parquet'}

⚠ Volume unit (vol): pytdx ``get_history_transaction_data`` returns ``vol``
already in **手** (lots, 1 手 = 100 股) — confirmed from Phase 0 recon sample:
SH600519 ~1276 元 single-tick vol=8 (手) → 800股 × 1276 ≈ 1M 元, plausible.
This differs from K-line data where pytdx returns vol in 股 (shares).
**No /100 conversion is applied here.** If future evidence shows otherwise,
change the ``_VOL_ALREADY_IN_LOTS`` constant below.

Zero-token: pytdx connects directly to broker quote hosts, no registration.
Same network path as ``pytdx_kline`` / ``f10`` / ``xdxr``.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import List, Optional, Union

import pandas as pd

from financial_analyst.data.updaters.pytdx_pool import PytdxClient, qlib_code_to_pytdx

# Bypass system proxy (Clash intercepts localhost/internal — same pattern as f10.py)
os.environ["NO_PROXY"] = "*"
os.environ["no_proxy"] = "*"

log = logging.getLogger(__name__)

# ──────────────────────── vol unit toggle ─────────────────────────────────────
# Phase 0 recon evidence: hist tick vol values (8, 17, 4, 52, 204 手 etc.) are
# already in 手 units.  Set to False if evidence ever shows otherwise.
_VOL_ALREADY_IN_LOTS: bool = True

# ──────────────────────── schema ──────────────────────────────────────────────
# §4.3 tick_history.parquet field contract (8 columns).
# Primary key: (code, date, time, idx)

TICK_FIELDS = [
    # fmt: off
    "code",        # str   "SH600519"
    "date",        # str   "YYYY-MM-DD"  — the trading date (from params, not pytdx row)
    "time",        # str   "HH:MM"       — pytdx native format
    "price",       # float 元
    "vol",         # int   手 (lots)
    "num",         # int   笔; hist tick always 0 (pytdx hist has no `num` field)
    "buyorsell",   # int   1=买 2=卖 0/5=不详
    "idx",         # int   per-(code, date) sequence index, 0-based — primary key component
    # fmt: on
]


# ──────────────────────── helpers ─────────────────────────────────────────────


def _date_to_int(date_str: str) -> int:
    """Convert "YYYY-MM-DD" → yyyymmdd int for pytdx API."""
    return int(date_str.replace("-", ""))


def _default_dates(n_days: int = 30) -> List[str]:
    """Return last ``n_days`` business days (Mon–Fri) as YYYY-MM-DD strings.

    Uses ``pandas.bdate_range`` for correct business-day logic.
    Does NOT account for Chinese public holidays — pass explicit ``dates`` for
    holiday-aware behaviour.
    """
    today = pd.Timestamp.today().normalize()
    dates = pd.bdate_range(end=today, periods=n_days)
    return [d.strftime("%Y-%m-%d") for d in dates]


def _load_existing_pairs(output_path: Path) -> set:
    """Return set of (code, date) tuples already in the parquet, or empty set."""
    if not output_path.exists():
        return set()
    try:
        df = pd.read_parquet(output_path, columns=["code", "date"])
        return set(zip(df["code"], df["date"]))
    except Exception as exc:
        log.warning("tick_history: could not read existing parquet (%s) — treating as empty.", exc)
        return set()


def _row_to_dict(code: str, date: str, raw: dict, idx: int) -> dict:
    """Normalise a single pytdx hist-tick row to the tick_history schema.

    pytdx hist row keys: ``time``, ``price``, ``vol``, ``buyorsell``.
    ``num`` is absent from hist ticks — stored as 0 for cross-source uniformity.
    """
    raw_vol = raw.get("vol", 0) or 0
    if _VOL_ALREADY_IN_LOTS:
        vol_lots = int(raw_vol)
    else:
        # If future evidence shows vol is in 股, divide by 100
        vol_lots = int(raw_vol) // 100

    return {
        "code": code,
        "date": date,
        "time": str(raw.get("time", "")),
        "price": float(raw.get("price", 0.0) or 0.0),
        "vol": vol_lots,
        "num": 0,          # pytdx hist does not provide `num`; today-tick would have it
        "buyorsell": int(raw.get("buyorsell", 5) or 5),
        "idx": idx,
    }


# ──────────────────────── public API ──────────────────────────────────────────


def update_tick_history(
    parquet_root: Union[Path, str],
    codes: List[str],
    dates: Optional[List[str]] = None,
    *,
    rows_per_call: int = 2000,
    log_progress: bool = True,
) -> dict:
    """Fetch historical tick data per (code, date) and append to tick_history.parquet.

    Parameters
    ----------
    parquet_root
        Root directory where ``tick_history.parquet`` will be written.
        Caller typically passes ``get_data_paths().parquet_root``.
    codes
        Qlib-format codes, e.g. ``["SH600519", "SZ002594"]``.
    dates
        List of trading dates as ``"YYYY-MM-DD"`` strings.  Defaults to the last
        30 business days (Mon–Fri, no holiday awareness).  Pass explicit dates
        for holiday-aware behaviour.
    rows_per_call
        Maximum rows requested per pytdx call.  pytdx hard limit is 2000.
        If a call returns ``rows_per_call`` rows, a second call is made with
        ``start += rows_per_call`` to fetch any remaining ticks.
    log_progress
        Print progress when processing each (code, date) pair and warn on failures.

    Returns
    -------
    dict
        ``{'total': N_pairs, 'ok': N, 'failed': N, 'skipped': N,
           'new_rows': N, 'output_path': str}``

    Notes
    -----
    vol unit: pytdx ``get_history_transaction_data`` returns vol already in 手.
    See module docstring for evidence and the ``_VOL_ALREADY_IN_LOTS`` toggle.
    """
    parquet_root = Path(parquet_root)
    output_path = parquet_root / "tick_history.parquet"

    if not codes:
        return {
            "total": 0,
            "ok": 0,
            "failed": 0,
            "skipped": 0,
            "new_rows": 0,
            "output_path": str(output_path),
        }

    if dates is None:
        dates = _default_dates(30)

    parquet_root.mkdir(parents=True, exist_ok=True)

    # Determine which (code, date) pairs already exist — skip those
    existing_pairs = _load_existing_pairs(output_path)

    # Build work list
    work = [(code, date) for code in codes for date in dates]
    total = len(work)

    ok = failed = skipped = 0
    new_rows: List[dict] = []

    if log_progress:
        print(f"[TICK] connecting to TDX...", flush=True)

    with PytdxClient() as client:
        if log_progress:
            print(
                f"[TICK] connected, fetching {total} (code×date) pairs "
                f"({len(codes)} codes × {len(dates)} dates)",
                flush=True,
            )

        for i, (code, date) in enumerate(work):
            # Incremental skip
            if (code, date) in existing_pairs:
                skipped += 1
                continue

            try:
                mkt, code_num = qlib_code_to_pytdx(code)
                date_int = _date_to_int(date)

                # Fetch all ticks for this (code, date), paginating if needed
                all_raw: list = []
                start = 0
                while True:
                    chunk = client.call(
                        "get_history_transaction_data",
                        mkt,
                        code_num,
                        start,
                        rows_per_call,
                        date_int,
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
                    log.warning("[TICK] (%s, %s) failed: %s", code, date, exc)
                continue

            # Convert rows (even empty result = 0 rows, still counts as ok for
            # the pair — market closed / holiday → pytdx returns [])
            for idx_within_day, raw in enumerate(all_raw):
                new_rows.append(_row_to_dict(code, date, raw, idx_within_day))
            ok += 1

            if log_progress and (i + 1) % 50 == 0:
                print(
                    f"  [TICK] {i+1}/{total} ok={ok} skip={skipped} fail={failed} "
                    f"new_rows={len(new_rows)}",
                    flush=True,
                )

    # ── Merge new rows with existing parquet ──────────────────────────────────
    frames: List[pd.DataFrame] = []

    if output_path.exists() and len(existing_pairs) > 0:
        try:
            existing_df = pd.read_parquet(output_path)
            if len(existing_df) > 0:
                frames.append(existing_df)
        except Exception as exc:
            log.warning("tick_history: could not read existing parquet (%s) — will overwrite.", exc)

    if new_rows:
        new_df = pd.DataFrame(new_rows, columns=TICK_FIELDS)
        frames.append(new_df)

    if frames:
        out_df = pd.concat(frames, ignore_index=True)
        out_df.to_parquet(output_path, index=False)
        if log_progress:
            print(f"[TICK] wrote {len(out_df)} rows → {output_path}", flush=True)
    else:
        if log_progress:
            log.warning("[TICK] no rows to write (all pairs failed, skipped, or empty)")

    return {
        "total": total,
        "ok": ok,
        "failed": failed,
        "skipped": skipped,
        "new_rows": len(new_rows),
        "output_path": str(output_path),
    }
