"""Tonghuashun (同花顺) concept-stock updater — zero-token via AData.

Vendored from ``G:/stocks/scripts/update_concepts_adata.py`` (research lab side),
with hardcoded ``G:/stocks/stock_data/parquet/`` paths replaced by a
``parquet_root`` function parameter so it composes cleanly with
``financial_analyst.data.paths.get_data_paths()``.

Two output Parquet files (both directly under ``parquet_root``):

  * ``concept_ths_index.parquet`` — concept master list (~391 rows × 5 cols:
    ``concept_code, name, index_code, source, last_fetched``)
  * ``concept_ths_constituent.parquet`` — concept → stock mapping (~100K rows
    × 5 cols: ``concept_code, name, stock_code, short_name, last_fetched``)

Data source: 同花顺 via the AData package. Only the THS source is stable —
the 东财 source has parse bugs in AData ≥ 2.9.5. AData is an **optional**
dependency: install via ``pip install adata`` (or ``pip install 'financial-analyst[concepts]'``).

API::

    >>> from financial_analyst.data.updaters.concepts import update_concepts
    >>> from financial_analyst.data.paths import get_data_paths
    >>> p = get_data_paths()
    >>> stats = update_concepts(p.parquet_root, max_age_days=30)
    >>> stats
    {'concepts_total': 391, 'concepts_refreshed': 12, 'rows_written': 2347,
     'failed': 0, 'index_path': '...', 'constituent_path': '...'}

Zero-token: AData scrapes 同花顺 web pages, no registration required. Network
path: ``http://www.10jqka.com.cn/`` direct.
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, Union

import pandas as pd


# ──────────────────────── adata optional import ────────────────────────


def _import_adata():
    """Import ``adata`` lazily with a friendly error if missing.

    Also nukes a known AData cache-dir bug — internal 东财 source code paths
    assume two cache subdirs exist, otherwise it raises. We create them blank.
    """
    try:
        import adata as _adata
    except ImportError as e:
        raise ImportError(
            "adata package not installed. Install it for concept-stock updates:\n"
            "  pip install adata\n"
            "or pull in the optional extra:\n"
            "  pip install 'financial-analyst[concepts]'\n"
            f"(original error: {e})"
        ) from e
    _ad_dir = Path(_adata.__file__).parent
    for sub in ("stock/info/cache", "stock/market/cache"):
        try:
            (_ad_dir / sub).mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
    return _adata


# ──────────────────────── helpers ────────────────────────


def _retry(fn, *args, max_attempts: int = 3, backoff: float = 2.0, **kwargs):
    """Exponential-backoff retry — 3 attempts default, doubling sleep."""
    last_err: Exception | None = None
    for i in range(max_attempts):
        try:
            return fn(*args, **kwargs)
        except Exception as e:
            last_err = e
            wait = backoff * (2**i)
            print(f"    [retry {i+1}/{max_attempts}] {type(e).__name__}: {e}, sleep {wait:.1f}s", flush=True)
            time.sleep(wait)
    assert last_err is not None
    raise last_err


def _write_atomic(df: pd.DataFrame, path: Path) -> None:
    """Write parquet via tmp-file + rename (crash-safe)."""
    tmp = path.with_suffix(".tmp")
    df.to_parquet(tmp, index=False)
    if path.exists():
        path.unlink()
    tmp.rename(path)


def _fetch_concept_index(adata) -> pd.DataFrame:
    """Pull 同花顺 concept master list — ~391 rows."""
    df = adata.stock.info.all_concept_code_ths()
    df = df.copy()
    df["last_fetched"] = datetime.now()
    return df[["concept_code", "name", "index_code", "source", "last_fetched"]]


def _fetch_constituent_one(adata, index_code: str, name: str) -> pd.DataFrame:
    """Pull constituents for one concept by its THS ``index_code``."""
    df = adata.stock.info.concept_constituent_ths(index_code=str(index_code))
    if df is None or len(df) == 0:
        return pd.DataFrame(columns=["concept_code", "name", "stock_code", "short_name", "last_fetched"])
    df = df.copy()
    df["concept_code"] = ""  # caller sets
    df["name"] = name
    df["last_fetched"] = datetime.now()
    return df[["concept_code", "name", "stock_code", "short_name", "last_fetched"]]


# ──────────────────────── public API ────────────────────────


def update_concepts(
    parquet_root: Union[str, Path],
    max_age_days: int = 30,
    full: bool = False,
    concept_filter: Optional[str] = None,
    limit: Optional[int] = None,
    rate_sleep: float = 0.5,
    checkpoint_every: int = 50,
    dry_run: bool = False,
    progress: bool = True,
) -> dict:
    """Incremental refresh of 同花顺 concept stocks.

    Parameters
    ----------
    parquet_root
        Directory holding ``concept_ths_index.parquet`` and
        ``concept_ths_constituent.parquet``. Pass ``get_data_paths().parquet_root``.
    max_age_days
        Concepts whose ``last_fetched`` is older than this get re-pulled
        (incremental mode). New concepts (never seen) are always pulled.
    full
        Ignore ``max_age_days`` and re-pull all concepts. Slow (~5 min).
    concept_filter
        Restrict the queue to a single THS ``index_code`` (e.g. ``"886108"``).
        Useful for debugging a flaky concept.
    limit
        After universe selection, take first N concepts only (debug).
    rate_sleep
        Pause between THS requests, in seconds. AData internal page parsing
        is already slow (~2s/concept), so 0.5s is conservative.
    checkpoint_every
        Flush constituent parquet every N concepts (crash safety).
    dry_run
        Plan-only mode. Returns counts without hitting the network.
    progress
        Print per-concept progress lines.

    Returns
    -------
    dict
        ``{concepts_total, concepts_refreshed, rows_written, failed, errors,
        index_path, constituent_path}``. Shape mirrors the other updaters.
    """
    parquet_root = Path(parquet_root)
    parquet_root.mkdir(parents=True, exist_ok=True)
    index_path = parquet_root / "concept_ths_index.parquet"
    constituent_path = parquet_root / "concept_ths_constituent.parquet"

    if dry_run:
        # Best-effort age estimate without hitting network
        n_stale = -1
        if constituent_path.exists():
            try:
                cons_old = pd.read_parquet(constituent_path, columns=["concept_code", "last_fetched"])
                cutoff = datetime.now() - timedelta(days=max_age_days)
                fresh = cons_old.groupby("concept_code")["last_fetched"].max()
                n_stale = int((pd.to_datetime(fresh) < cutoff).sum())
            except Exception:
                pass
        return {
            "dry_run": True,
            "concepts_total": -1,  # would query THS
            "concepts_refreshed": 0,
            "rows_written": 0,
            "failed": 0,
            "plan": (
                f"Would pull THS concept index, then refresh "
                f"{'ALL concepts' if full else f'concepts >{max_age_days}d old (estimated {n_stale} stale)'} "
                f"→ {constituent_path}"
            ),
            "index_path": str(index_path),
            "constituent_path": str(constituent_path),
        }

    adata = _import_adata()

    # Step 1: pull current concept master list
    if progress:
        print(f"[concepts] Step 1: pulling THS concept index ...", flush=True)
    t0 = time.perf_counter()
    idx_new = _retry(_fetch_concept_index, adata)
    if progress:
        print(f"  fetched {len(idx_new)} concepts in {time.perf_counter()-t0:.1f}s", flush=True)

    # Merge with existing index (replace rows that overlap on concept_code)
    idx_old = pd.read_parquet(index_path) if index_path.exists() else pd.DataFrame()
    if len(idx_old) > 0:
        idx_old = idx_old[~idx_old["concept_code"].isin(idx_new["concept_code"])]
        idx_merged = pd.concat([idx_old, idx_new], ignore_index=True)
    else:
        idx_merged = idx_new
    _write_atomic(idx_merged, index_path)
    if progress:
        print(f"  index parquet: {len(idx_merged)} rows → {index_path.name}", flush=True)

    # Step 2: build queue
    cons_old = pd.read_parquet(constituent_path) if constituent_path.exists() else pd.DataFrame()
    if concept_filter:
        target = idx_new[idx_new["index_code"].astype(str) == str(concept_filter)]
        if len(target) == 0:
            return {
                "concepts_total": len(idx_new),
                "concepts_refreshed": 0,
                "rows_written": 0,
                "failed": 1,
                "errors": [f"concept_filter={concept_filter} not in THS concept list"],
                "index_path": str(index_path),
                "constituent_path": str(constituent_path),
            }
        queue = target.to_dict("records")
    elif full:
        queue = idx_new.to_dict("records")
    else:
        cutoff = datetime.now() - timedelta(days=max_age_days)
        if len(cons_old) > 0:
            last_per_concept = cons_old.groupby("concept_code")["last_fetched"].max().to_dict()
        else:
            last_per_concept = {}
        queue = []
        for _, row in idx_new.iterrows():
            cc = row["concept_code"]
            last = last_per_concept.get(cc)
            if last is None or pd.Timestamp(last).to_pydatetime() < cutoff:
                queue.append(row.to_dict())
        if progress:
            print(f"[concepts] Step 2: incremental queue = {len(queue)} "
                  f"(cutoff={cutoff:%Y-%m-%d})", flush=True)

    if limit:
        queue = queue[:limit]

    if not queue:
        return {
            "concepts_total": len(idx_new),
            "concepts_refreshed": 0,
            "rows_written": 0,
            "failed": 0,
            "errors": [],
            "msg": "all concepts fresh, nothing to refresh",
            "index_path": str(index_path),
            "constituent_path": str(constituent_path),
        }

    # Step 3: pull constituents in batches, checkpoint to parquet
    if progress:
        print(f"[concepts] Step 3: pulling {len(queue)} concept constituents "
              f"(checkpoint every {checkpoint_every}) ...", flush=True)
    pending: list[pd.DataFrame] = []
    written_total = 0
    failed: list[dict] = []
    t_start = time.perf_counter()

    def _flush(buf: list[pd.DataFrame], cons_ref: pd.DataFrame) -> pd.DataFrame:
        if not buf:
            return cons_ref
        new_df = pd.concat(buf, ignore_index=True)
        if len(cons_ref) > 0:
            cons_ref = cons_ref[~cons_ref["concept_code"].isin(new_df["concept_code"])]
            merged = pd.concat([cons_ref, new_df], ignore_index=True)
        else:
            merged = new_df
        _write_atomic(merged, constituent_path)
        return merged

    for i, row in enumerate(queue, 1):
        cc, ic, nm = row["concept_code"], str(row["index_code"]), row["name"]
        try:
            df_c = _retry(_fetch_constituent_one, adata, ic, nm, max_attempts=3, backoff=2.0)
            df_c["concept_code"] = cc
            pending.append(df_c)
            if progress:
                elapsed = time.perf_counter() - t_start
                eta = elapsed / i * (len(queue) - i)
                print(f"  [{i}/{len(queue)}] {ic} {nm}: {len(df_c)} stocks | "
                      f"elapsed {elapsed:.0f}s ETA {eta:.0f}s", flush=True)
        except Exception as e:
            failed.append({"concept_code": cc, "index_code": ic, "name": nm,
                           "error": f"{type(e).__name__}: {e}"})
            if progress:
                print(f"  [{i}/{len(queue)}] {ic} {nm}: FAIL {type(e).__name__}", flush=True)

        if len(pending) >= checkpoint_every or i == len(queue):
            cons_old = _flush(pending, cons_old)
            written_total += len(pending)
            if progress:
                print(f"  >>> checkpoint @ {i}: parquet now {len(cons_old)} rows "
                      f"(this batch: {len(pending)} concepts)", flush=True)
            pending = []
        time.sleep(rate_sleep)

    return {
        "concepts_total": len(idx_new),
        "concepts_refreshed": written_total,
        "rows_written": int(len(cons_old)),
        "failed": len(failed),
        "errors": failed[:10],  # truncate
        "elapsed_sec": time.perf_counter() - t_start,
        "index_path": str(index_path),
        "constituent_path": str(constituent_path),
    }
