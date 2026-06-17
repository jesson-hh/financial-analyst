"""Alpha selector — pick the strongest alphas from a bench CSV.

Replaces the hard-coded ``PRODUCTION_TOP10`` list in v1.3.4 with a
dynamic top-N picked from the most-recent benchmark run. The workflow
becomes:

1. ``alpha bench --universe csi300_active --since ... --until ... --save``
   → writes ``~/.financial-analyst/cache/bench_<universe>_latest.csv``
2. ``alpha snapshot --auto --universe csi300_active --until <asof>
   --top-n 20`` → reads the bench CSV, picks top-N alphas by quality,
   builds the snapshot.
3. Reports auto-pick up the snapshot — quant-analyst sees the
   bench-validated alpha list with each row's ``bench_rank_ic`` and
   ``bench_hit_rate`` for direction interpretation.

The selection rule has three filters, applied in order:

* ``min_n_dates`` — drops alphas whose bench window was too short to be
  statistically meaningful (default 30 trading days).
* ``min_abs_rank_ir`` — drops pure-noise alphas. Default 0.05 = signal
  magnitude floor.
* ``min_quality`` — drops alphas with ``rank_ir`` and ``hit_rate``
  pointing different ways (sign disagreement = unreliable direction).

Survivors are sorted by ``|rank_ir|`` descending; top-N returned.
"""
from __future__ import annotations
from pathlib import Path
from typing import List, Optional

import pandas as pd


def _cache_dir() -> Path:
    p = Path.home() / ".financial-analyst" / "cache"
    p.mkdir(parents=True, exist_ok=True)
    return p


def bench_csv_path(universe: str) -> Path:
    """Canonical filename for a saved bench result for a given universe."""
    return _cache_dir() / f"bench_{universe}_latest.csv"


def select_top_alphas(
    bench_df: pd.DataFrame,
    n: int = 20,
    *,
    min_n_dates: int = 30,
    min_abs_rank_ir: float = 0.05,
    require_sign_agreement: bool = True,
    family: Optional[str] = None,
) -> List[str]:
    """Pick the top-N alphas from a bench DataFrame.

    Parameters
    ----------
    bench_df : DataFrame with columns ``name, family, rank_ir, hit_rate,
               n_dates`` (and optionally ``status``, ``rank_ic``).
    n : top-N to return.
    min_n_dates : minimum bench window. Default 30.
    min_abs_rank_ir : noise floor on rank_IR. Default 0.05.
    require_sign_agreement : drop alphas whose rank_IR and
        (hit_rate - 0.5) disagree on direction. Default True.
    family : optional filter ('alpha101', 'gtja191', 'qlib158').

    Returns
    -------
    List of alpha names, sorted by |rank_IR| descending.
    """
    df = bench_df.copy()
    if "status" in df.columns:
        df = df[df["status"] == "ok"]
    df = df.dropna(subset=["rank_ir", "hit_rate", "n_dates"])
    if family:
        df = df[df["family"] == family]

    df = df[df["n_dates"] >= min_n_dates]
    df = df[df["rank_ir"].abs() >= min_abs_rank_ir]

    if require_sign_agreement and len(df):
        # rank_ir > 0 should pair with hit_rate > 0.5
        # rank_ir < 0 should pair with hit_rate < 0.5
        same_sign = ((df["rank_ir"] > 0) & (df["hit_rate"] > 0.5)) | (
            (df["rank_ir"] < 0) & (df["hit_rate"] < 0.5)
        )
        df = df[same_sign]

    df = df.sort_values("rank_ir", key=lambda s: s.abs(), ascending=False)
    return df.head(n)["name"].tolist()


def load_latest_bench(universe: str) -> Optional[pd.DataFrame]:
    """Read the canonical cached bench CSV for ``universe`` if it exists."""
    path = bench_csv_path(universe)
    if not path.exists():
        return None
    return pd.read_csv(path)


def alpha_metadata_from_bench(
    bench_df: pd.DataFrame, names: List[str]
) -> dict:
    """Build ``{name: {bench_rank_ic, bench_hit_rate, n_dates}}`` for the
    requested names. Used to enrich snapshot rows with bench-validated
    sign and reliability info so LLM consumers can interpret values
    without hard-coded sign conventions.
    """
    sub = bench_df[bench_df["name"].isin(names)].set_index("name")
    out = {}
    for name in names:
        if name not in sub.index:
            out[name] = {"bench_rank_ic": None, "bench_hit_rate": None, "bench_n_dates": None}
            continue
        row = sub.loc[name]
        out[name] = {
            "bench_rank_ic": float(row["rank_ic"]) if pd.notna(row.get("rank_ic")) else None,
            "bench_hit_rate": float(row["hit_rate"]) if pd.notna(row.get("hit_rate")) else None,
            "bench_n_dates": int(row["n_dates"]) if pd.notna(row.get("n_dates")) else None,
        }
    return out
