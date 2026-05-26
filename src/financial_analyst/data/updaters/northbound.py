"""Northbound (港股通) capital flow updater — zero-token via akshare / 东财.

Writes ``northbound_flow.parquet`` — historical daily net buy/sell totals for
沪股通 + 深股通 (one row per (date, market) ≈ 2 rows/trading-day). Schema:

  ``日期, 当日成交净买额, 买入成交额, 卖出成交额, 历史累计净买额, 当日资金流入, market``

(Plus a few other 东财 columns akshare ships — kept verbatim from upstream.)

Idempotent: pulling twice on the same day dedups on ``(日期, market)``.

────────────────────────────────────────────────────────────────────
**Why this updater is "flow" not "holding"**
────────────────────────────────────────────────────────────────────

The stocks-side ``northbound_holding.parquet`` (17 cols, per-stock snapshot)
was populated from ``ak.stock_hsgt_hold_stock_em(market=..., indicator=...)``,
which **currently returns** ``None`` on akshare 1.18.63 — 东财 changed their
list-page DOM and the upstream parser has not caught up. So we can't refresh
that file via akshare today.

In the meantime, ``ak.stock_hsgt_hist_em(symbol=...)`` still works and
provides the **aggregate** daily flow numbers — which is what 95% of
research users actually need for market-direction signals (北向今日 +50亿
or -30亿). That's what this updater pulls.

Once akshare's per-stock parser is fixed (or we migrate to Tushare
``hk_hold``), a separate ``update_northbound_holding`` can be added to refresh
the per-stock parquet. The CLI flag ``--include-northbound`` is wired to this
flow updater for now.

API::

    >>> from financial_analyst.data.updaters.northbound import update_northbound
    >>> from financial_analyst.data.paths import get_data_paths
    >>> p = get_data_paths()
    >>> stats = update_northbound(p.parquet_root)
    >>> stats
    {'ok': True, 'rows_new': 5350, 'rows_total': 5350, 'markets': {'沪股通': 2675, '深股通': 2675},
     'date_range': '2014-11-17 → 2026-05-26',
     'parquet_path': '.../northbound_flow.parquet'}
"""
from __future__ import annotations

from datetime import date as _date
from pathlib import Path
from typing import Union

import pandas as pd


# Dedup key — one row per (snapshot date, market direction)
_DEDUP_COLS = ["日期", "market"]


def _import_akshare():
    """Lazy import with friendly install hint."""
    try:
        import akshare as _ak
    except ImportError as e:
        raise ImportError(
            "akshare package not installed. Install for northbound updates:\n"
            "  pip install akshare\n"
            "or pull the optional extra:\n"
            "  pip install 'financial-analyst[northbound]'\n"
            f"(original error: {e})"
        ) from e
    return _ak


def update_northbound(
    parquet_root: Union[str, Path],
    dry_run: bool = False,
    progress: bool = True,
) -> dict:
    """Pull 沪+深股通 historical daily flow, write to ``northbound_flow.parquet``.

    Each call refreshes full history (~2675 rows × 2 markets = ~5350 rows total).
    akshare returns the full series each time so a single replace-write is the
    simplest correct approach — no incremental dating needed.

    Parameters
    ----------
    parquet_root
        Directory holding ``northbound_flow.parquet`` directly.
    dry_run
        Plan-only mode (no network).
    progress
        Print per-market line.

    Returns
    -------
    dict
        ``{ok, rows_new, rows_total, markets, date_range, parquet_path, errors}``
        on success; ``{ok: False, ...}`` if both markets fail.
    """
    parquet_root = Path(parquet_root)
    parquet_path = parquet_root / "northbound_flow.parquet"

    if dry_run:
        n_existing = 0
        last_date = None
        if parquet_path.exists():
            try:
                df = pd.read_parquet(parquet_path, columns=["日期"])
                n_existing = len(df)
                last_date = df["日期"].max()
            except Exception:
                pass
        return {
            "dry_run": True,
            "ok": True,
            "rows_new": 0,
            "rows_total": n_existing,
            "plan": (
                f"Would pull akshare stock_hsgt_hist_em(沪股通 + 深股通) full history, "
                f"write {parquet_path} (existing {n_existing} rows; "
                f"last date {last_date}). Expected: ~5350 rows total."
            ),
            "parquet_path": str(parquet_path),
        }

    ak = _import_akshare()

    market_dfs: list[pd.DataFrame] = []
    counts: dict[str, int] = {}
    errors: list[str] = []

    for market in ("沪股通", "深股通"):
        if progress:
            print(f"[northbound] pulling akshare stock_hsgt_hist_em({market!r}) ...", flush=True)
        try:
            df = ak.stock_hsgt_hist_em(symbol=market)
            if df is None or len(df) == 0:
                errors.append(f"{market}: empty result")
                continue
            df = df.copy()
            # Coerce 日期 to plain date
            if "日期" in df.columns:
                df["日期"] = pd.to_datetime(df["日期"]).dt.date
            df["market"] = market
            market_dfs.append(df)
            counts[market] = len(df)
            if progress:
                print(f"  [{market}] {len(df)} rows", flush=True)
        except Exception as e:
            errors.append(f"{market}: {type(e).__name__}: {e}")
            if progress:
                print(f"  [{market}] FAIL {type(e).__name__}: {e}", flush=True)

    if not market_dfs:
        return {
            "ok": False,
            "rows_new": 0,
            "rows_total": 0,
            "errors": errors,
            "parquet_path": str(parquet_path),
        }

    combined = pd.concat(market_dfs, ignore_index=True)
    # Dedup on (日期, market) — same-day re-runs idempotent
    before = len(combined)
    combined = combined.drop_duplicates(subset=_DEDUP_COLS, keep="last")
    combined = combined.sort_values(["日期", "market"]).reset_index(drop=True)

    # Atomic write (full replace — akshare returns full history each time)
    parquet_root.mkdir(parents=True, exist_ok=True)
    tmp = parquet_path.with_suffix(".tmp")
    combined.to_parquet(tmp, index=False)
    if parquet_path.exists():
        parquet_path.unlink()
    tmp.rename(parquet_path)

    date_range = "n/a"
    if "日期" in combined.columns and len(combined) > 0:
        date_range = f"{combined['日期'].min()} → {combined['日期'].max()}"

    if progress:
        print(f"[northbound ✓] wrote {len(combined):,} rows ({date_range}) → "
              f"{parquet_path.name}", flush=True)

    return {
        "ok": True,
        "rows_new": before,
        "rows_total": int(len(combined)),
        "markets": counts,
        "date_range": date_range,
        "errors": errors,  # non-fatal per-market errors
        "parquet_path": str(parquet_path),
    }
