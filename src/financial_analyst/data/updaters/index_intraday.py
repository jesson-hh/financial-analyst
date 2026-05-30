"""TDX 大盘指数 1min K 线 (index_intraday) updater — zero-token via pytdx direct.

Fetches 1-minute OHLCV + 涨跌停家数 (market breadth) for the 5 major A-share
indices using ``pytdx.get_index_bars(category=7, ...)`` and appends to a
single consolidated ``index_intraday.parquet`` under ``parquet_root``.

Increment strategy: fetch first, then filter out (index_code, date) pairs
already present in the parquet before writing.  Existing dates are dropped
from the parquet and replaced with freshly-fetched data (overwrite per date).

Wire it from ``fa data update --include-index-intraday`` (see
``data_cli.update_cmd``).

API::

    >>> from financial_analyst.data.updaters.index_intraday import update_index_intraday
    >>> from financial_analyst.data.paths import get_data_paths
    >>> p = get_data_paths()
    >>> stats = update_index_intraday(p.parquet_root, n_days=1)
    >>> stats
    {'total': 5, 'ok': 5, 'failed': 0, 'skipped': 0, 'new_rows': 1200,
     'output_path': '.../parquet/index_intraday.parquet'}

⚠ Volume unit (vol): pytdx ``get_index_bars`` returns ``vol`` in **股**
(shares), same as ``get_security_bars`` for equities.  Confirmed from Phase 0
recon sample (上证指数 2026-05-29 09:31 vol=435063040, i.e. ~4.35M 手 at
open — plausible for the market-wide index).  We divide by 100 to convert to
**手** for consistency with ``pytdx_kline.py`` and Qlib conventions.
If future evidence shows the unit is already 手, flip ``_VOL_IN_GU`` to False.

Zero-token: pytdx connects directly to broker quote hosts, no registration.
Same network path as ``pytdx_kline`` / ``f10`` / ``xdxr`` / ``tick_history``.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Union

import pandas as pd

from financial_analyst.data.updaters.pytdx_pool import PytdxClient

# Bypass system proxy (Clash intercepts localhost/internal — same pattern as f10.py)
os.environ["NO_PROXY"] = "*"
os.environ["no_proxy"] = "*"

log = logging.getLogger(__name__)

# ──────────────────────── vol unit toggle ─────────────────────────────────────
# Phase 0 recon evidence: index_bars vol=435063040 (股) for 上证指数 09:31.
# Dividing by 100 → 4350630 手 per minute, consistent with market-wide open.
# Set to False if evidence ever shows index vol is already in 手.
_VOL_IN_GU: bool = True

# ──────────────────────── index registry ──────────────────────────────────────
# (qlib_code, name, pytdx_mkt, pytdx_code)
# mkt: 1=沪 0=深  (indices use same market convention as equities)
INDICES: List[Tuple[str, str, int, str]] = [
    ("SH000001", "上证指数", 1, "000001"),
    ("SZ399001", "深证成指", 0, "399001"),
    ("SH000300", "沪深300",  1, "000300"),
    ("SH000688", "科创50",   1, "000688"),
    ("SZ399006", "创业板指", 0, "399006"),
]

# Lookup: qlib_code → (name, mkt, pytdx_code)
_INDEX_MAP: Dict[str, Tuple[str, int, str]] = {
    qc: (name, mkt, pc) for qc, name, mkt, pc in INDICES
}

# ──────────────────────── schema ──────────────────────────────────────────────
# §4.4 index_intraday.parquet field contract (12 columns).
# Primary key: (index_code, date, time)

INDEX_INTRADAY_FIELDS = [
    # fmt: off
    "index_code",   # str   "SH000001"
    "index_name",   # str   "上证指数"
    "date",         # str   "YYYY-MM-DD"
    "time",         # str   "HH:MM"
    "open",         # float
    "high",         # float
    "low",          # float
    "close",        # float
    "vol",          # int   手 (pytdx vol / 100)
    "amount",       # float 元
    "up_count",     # int   涨停家数
    "down_count",   # int   跌停家数
    # fmt: on
]


# ──────────────────────── helpers ─────────────────────────────────────────────


def _bar_to_dict(index_code: str, index_name: str, raw: dict) -> dict:
    """Normalise a single pytdx index bar to the index_intraday schema.

    pytdx ``get_index_bars`` row keys:
    ``open``, ``close``, ``high``, ``low``, ``vol``, ``amount``,
    ``year``, ``month``, ``day``, ``hour``, ``minute``, ``datetime``,
    ``up_count``, ``down_count``.
    """
    year = int(raw.get("year", 0) or 0)
    month = int(raw.get("month", 0) or 0)
    day = int(raw.get("day", 0) or 0)
    hour = int(raw.get("hour", 0) or 0)
    minute = int(raw.get("minute", 0) or 0)

    date_str = f"{year:04d}-{month:02d}-{day:02d}"
    time_str = f"{hour:02d}:{minute:02d}"

    raw_vol = raw.get("vol", 0) or 0
    vol_lots = int(raw_vol) // 100 if _VOL_IN_GU else int(raw_vol)

    return {
        "index_code": index_code,
        "index_name": index_name,
        "date": date_str,
        "time": time_str,
        "open": float(raw.get("open", 0.0) or 0.0),
        "high": float(raw.get("high", 0.0) or 0.0),
        "low": float(raw.get("low", 0.0) or 0.0),
        "close": float(raw.get("close", 0.0) or 0.0),
        "vol": vol_lots,
        "amount": float(raw.get("amount", 0.0) or 0.0),
        "up_count": int(raw.get("up_count", 0) or 0),
        "down_count": int(raw.get("down_count", 0) or 0),
    }


def _load_existing_pairs(output_path: Path) -> set:
    """Return set of (index_code, date) tuples already in the parquet."""
    if not output_path.exists():
        return set()
    try:
        df = pd.read_parquet(output_path, columns=["index_code", "date"])
        return set(zip(df["index_code"], df["date"]))
    except Exception as exc:
        log.warning(
            "index_intraday: could not read existing parquet (%s) — treating as empty.",
            exc,
        )
        return set()


# ──────────────────────── public API ──────────────────────────────────────────


def update_index_intraday(
    parquet_root: Union[Path, str],
    indices: Optional[List[str]] = None,
    n_days: int = 1,
    *,
    bars_per_day: int = 240,
    log_progress: bool = True,
) -> dict:
    """Fetch 1min OHLCV+breadth for major indices → parquet_root/index_intraday.parquet.

    pytdx ``get_index_bars(category=7, mkt, code, start=0, count=240)`` returns
    the most recent 240 bars (one full trading day).  To go further back,
    increment ``start`` by 240: start=0 → today, start=240 → yesterday, etc.
    We stop early if pytdx returns fewer than ``bars_per_day`` rows (no more data).

    Increment strategy: fetch → group by date → drop (index_code, date) pairs
    already in parquet → write only new data.

    Parameters
    ----------
    parquet_root
        Root directory where ``index_intraday.parquet`` will be written.
        Caller typically passes ``get_data_paths().parquet_root``.
    indices
        Subset of qlib-format index codes to update, e.g. ``["SH000001"]``.
        ``None`` (default) fetches all 5 indices in the registry.
    n_days
        How many trading days to fetch per index.  pytdx 1min data is retained
        for roughly 10 days.  Values > 10 may return empty results.
    bars_per_day
        Expected bars per full trading day (default 240 for 1min).  Used as the
        ``count`` argument to pytdx and as the ``start`` stride.
    log_progress
        Print progress messages to stdout.

    Returns
    -------
    dict
        ``{'total': N_index_days, 'ok': N, 'failed': N, 'skipped': N,
           'new_rows': N, 'output_path': str}``

    Notes
    -----
    vol unit: pytdx ``get_index_bars`` returns vol in 股 (shares).
    We divide by 100 to store in 手 (lots).  See module docstring + ``_VOL_IN_GU``.
    """
    parquet_root = Path(parquet_root)
    output_path = parquet_root / "index_intraday.parquet"

    # Resolve requested indices
    if indices is None:
        target_indices = list(INDICES)
    else:
        target_indices = []
        for qc in indices:
            if qc in _INDEX_MAP:
                name, mkt, pc = _INDEX_MAP[qc]
                target_indices.append((qc, name, mkt, pc))
            else:
                log.warning("index_intraday: unknown index code %r — skipping.", qc)

    if not target_indices:
        return {
            "total": 0,
            "ok": 0,
            "failed": 0,
            "skipped": 0,
            "new_rows": 0,
            "output_path": str(output_path),
        }

    parquet_root.mkdir(parents=True, exist_ok=True)

    # total = number of (index, day) fetch attempts
    total = len(target_indices) * n_days

    # Load existing (index_code, date) pairs for incremental skip
    existing_pairs = _load_existing_pairs(output_path)

    ok = failed = skipped = 0
    new_rows: List[dict] = []

    if log_progress:
        print("[IDX] connecting to TDX...", flush=True)

    with PytdxClient() as client:
        if log_progress:
            print(
                f"[IDX] connected, fetching {len(target_indices)} indices × {n_days} days",
                flush=True,
            )

        for qlib_code, name, mkt, pytdx_code in target_indices:
            for day_offset in range(n_days):
                start = day_offset * bars_per_day
                try:
                    raw_bars = client.call(
                        "get_index_bars", 7, mkt, pytdx_code, start, bars_per_day
                    )
                except Exception as exc:
                    failed += 1
                    log.warning(
                        "[IDX] %s (start=%d) failed: %s", qlib_code, start, exc
                    )
                    continue

                if not raw_bars:
                    # pytdx returns empty list when no data at this offset
                    failed += 1
                    if log_progress:
                        log.warning(
                            "[IDX] %s start=%d returned empty — stopping.", qlib_code, start
                        )
                    break

                # Group bars by date; each offset call should be one calendar day
                # but we group defensively in case pytdx spans midnight.
                day_rows: List[dict] = [
                    _bar_to_dict(qlib_code, name, b) for b in raw_bars
                ]

                # Find the date(s) returned in this batch
                dates_in_batch = set(r["date"] for r in day_rows)

                # Skip dates already in parquet
                new_dates = dates_in_batch - {d for (c, d) in existing_pairs if c == qlib_code}
                if not new_dates:
                    skipped += len(dates_in_batch)
                    ok += 1
                    continue

                # Keep only rows for new dates
                for row in day_rows:
                    if row["date"] in new_dates:
                        new_rows.append(row)

                ok += 1

                # Early-stop heuristic: if fewer bars than a full day, no more data
                if len(raw_bars) < bars_per_day:
                    break

    # ── Merge new rows with existing parquet ──────────────────────────────────
    # Drop existing rows for (index_code, date) pairs we are refreshing,
    # then append the new rows.
    new_pairs: set = set()
    for r in new_rows:
        new_pairs.add((r["index_code"], r["date"]))

    frames: List[pd.DataFrame] = []

    if output_path.exists():
        try:
            existing_df = pd.read_parquet(output_path)
            if len(existing_df) > 0:
                # Keep rows NOT covered by the new fetch
                mask = ~(
                    existing_df["index_code"].isin({c for c, _ in new_pairs})
                    & existing_df["date"].isin({d for _, d in new_pairs})
                )
                retained = existing_df[mask]
                if len(retained) > 0:
                    frames.append(retained)
        except Exception as exc:
            log.warning(
                "index_intraday: could not read existing parquet (%s) — will overwrite.", exc
            )

    if new_rows:
        new_df = pd.DataFrame(new_rows, columns=INDEX_INTRADAY_FIELDS)
        frames.append(new_df)

    if frames:
        out_df = pd.concat(frames, ignore_index=True)
        out_df.to_parquet(output_path, index=False)
        if log_progress:
            print(f"[IDX] wrote {len(out_df)} rows → {output_path}", flush=True)
    else:
        if log_progress:
            log.warning("[IDX] no rows to write (all indices failed, skipped, or empty)")

    return {
        "total": total,
        "ok": ok,
        "failed": failed,
        "skipped": skipped,
        "new_rows": len(new_rows),
        "output_path": str(output_path),
    }
