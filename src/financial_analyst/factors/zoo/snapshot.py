"""Zoo snapshot — materialise top-N alpha values for an entire universe at
one as-of date, so individual stock reports can pull pre-computed signals
in O(1) instead of re-bench-ing the universe every time.

Workflow:
1. User runs ``financial-analyst alpha snapshot --universe csi300_active
   --asof 2024-12-31 --top 10`` periodically (weekly is plenty).
2. Output: ``~/.financial-analyst/cache/zoo_snapshot_<universe>_<asof>.parquet``
   with columns ``code | alpha | value | rank_pct | n_obs``.
3. ``factor-computer`` looks up the most-recent snapshot covering the
   target stock and surfaces ``zoo_signals`` to downstream analysts.

A "production top-10" curated list comes from the CSI300 2024-H2 bench
(see ``docs/csi300_bench_2024h2.md``). It is the default selection when
``--top`` is omitted; pass ``--names`` to override.
"""
from __future__ import annotations
from pathlib import Path
from typing import List, Optional, Sequence

import numpy as np
import pandas as pd

from financial_analyst.factors.zoo.panel import PanelData
from financial_analyst.factors.zoo.registry import get, list_alphas


# Curated production list — strongest 10 alphas on the CSI300 2024-H2
# bench filtered to (|rank_IR| > 0.3 AND hit_rate > 50%) OR (very high
# |rank_IR|). See docs/csi300_bench_2024h2.md §8 for the derivation.
PRODUCTION_TOP10: List[str] = [
    "qlib_VSTD60",   # 60d volume volatility (+0.542)
    "gtja095",       # 20d stddev of dollar volume (-0.430)
    "qlib_STD10",    # 10d close stddev / close (-0.416)
    "gtja052",       # 26d wick ratio (-0.409)
    "gtja042",       # vol-of-high × high-vol corr (+0.408)
    "qlib_VSUMP20",  # 20d volume-direction fraction (-0.404)
    "qlib_KLEN",     # daily range / open (-0.352)
    "qlib_BETA20",   # 20d close-vs-time slope (-0.318)
    "qlib_ROC60",    # 60d close ratio (+0.331)
    "qlib_IMAX20",   # high-recency position (-0.335)
]


def _cache_dir() -> Path:
    p = Path.home() / ".financial-analyst" / "cache"
    p.mkdir(parents=True, exist_ok=True)
    return p


def snapshot_path(universe: str, asof: str) -> Path:
    """Canonical filename for a (universe, asof) snapshot."""
    return _cache_dir() / f"zoo_snapshot_{universe}_{asof}.parquet"


def build_snapshot(
    loader,
    universe_codes: Sequence[str],
    asof: str,
    names: Optional[Sequence[str]] = None,
    lookback_days: int = 365,
    industry_loader=None,
    bench_metadata: Optional[dict] = None,
) -> pd.DataFrame:
    """Compute the latest-bar value of each alpha for every stock in the
    universe at ``asof``. ``rank_pct`` is the cross-sectional percentile
    rank of the alpha value across the universe on that date.

    When ``bench_metadata`` is supplied (``{name: {bench_rank_ic,
    bench_hit_rate, bench_n_dates}}``), those fields land on every row so
    downstream LLM consumers can interpret rank_pct against the alpha's
    bench-validated direction without hard-coded sign conventions.

    Returns a tidy DataFrame: ``code | alpha | value | rank_pct | n_obs
    [| bench_rank_ic | bench_hit_rate | bench_n_dates]``.
    """
    if names is None:
        names = PRODUCTION_TOP10
    names = list(names)

    # Resolve specs early so we fail fast if a name is unknown
    specs = [get(n) for n in names]

    # Pull a year of history so every alpha has its window filled at asof
    end_dt = pd.Timestamp(asof)
    start_dt = end_dt - pd.Timedelta(days=lookback_days)

    panel = PanelData.from_loader(
        loader, list(universe_codes),
        start_dt.strftime("%Y-%m-%d"), asof, freq="day",
        industry_loader=industry_loader,
    )

    rows: list[dict] = []
    for spec in specs:
        try:
            series = spec.compute(panel)
        except Exception as e:
            # Skip a broken alpha but continue the snapshot — partial output
            # is more useful than no output.
            rows.append({
                "code": "__error__", "alpha": spec.name,
                "value": np.nan, "rank_pct": np.nan, "n_obs": 0,
                "error": f"{type(e).__name__}: {e}",
            })
            continue

        # Latest bar per code (the row at the panel's max date)
        max_date = panel.dates().max()
        latest = series.loc[(max_date,) if isinstance(series.index, pd.MultiIndex) else slice(None)]
        if isinstance(latest, pd.Series) and latest.index.nlevels == 1:
            # Single-day cross-section already
            cs = latest.dropna()
        else:
            cs = series.xs(max_date, level="datetime").dropna()
        ranks = cs.rank(pct=True)
        meta = (bench_metadata or {}).get(spec.name, {})
        for code in cs.index:
            row = {
                "code": code,
                "alpha": spec.name,
                "value": float(cs.loc[code]),
                "rank_pct": float(ranks.loc[code]),
                "n_obs": int(len(cs)),
            }
            if meta:
                row["bench_rank_ic"] = meta.get("bench_rank_ic")
                row["bench_hit_rate"] = meta.get("bench_hit_rate")
                row["bench_n_dates"] = meta.get("bench_n_dates")
            rows.append(row)

    return pd.DataFrame(rows)


def load_snapshot_for_code(
    universe: str, code: str, asof_or_earlier: Optional[str] = None,
) -> Optional[pd.DataFrame]:
    """Look up the most-recent snapshot for ``universe`` whose asof <=
    ``asof_or_earlier`` (default: today). Returns the rows for ``code``
    only, or ``None`` if no snapshot is available.
    """
    cache = _cache_dir()
    pattern = f"zoo_snapshot_{universe}_*.parquet"
    candidates = sorted(cache.glob(pattern))
    if not candidates:
        return None
    # filename → asof
    def _asof(p: Path) -> str:
        return p.stem.split("_")[-1]
    if asof_or_earlier:
        candidates = [p for p in candidates if _asof(p) <= asof_or_earlier]
        if not candidates:
            return None
    target = candidates[-1]
    df = pd.read_parquet(target)
    sub = df[df["code"] == code]
    if sub.empty:
        return None
    return sub.assign(snapshot_path=str(target), snapshot_asof=_asof(target))
