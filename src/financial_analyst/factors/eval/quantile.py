"""分位(十分位)回测: 每个调仓日按因子值分 N 组, 算组前瞻收益/组净值/单调性/多空价差。"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import List

import numpy as np
import pandas as pd


@dataclass
class QuantileResult:
    n_groups: int
    group_ann_return: List[float] = field(default_factory=list)
    group_nav: List[List[float]] = field(default_factory=list)
    monotonicity: float = float("nan")
    long_short_spread: float = float("nan")


def _assign_groups(a: pd.Series, n_groups: int) -> pd.Series:
    """Per-date qcut into n_groups buckets, label 0=lowest .. (k-1)=highest.

    Uses ``duplicates="drop"``, so a date with fewer than n_groups distinct
    factor values yields fewer buckets (k < n_groups) — on a normal index
    universe (hundreds of names) all n_groups buckets always form. Degenerate
    dates that can't be cut return NaN (caught), excluded downstream.
    """
    def _q(s: pd.Series) -> pd.Series:
        try:
            return pd.qcut(s, n_groups, labels=False, duplicates="drop")
        except (ValueError, IndexError):
            return pd.Series(np.nan, index=s.index)
    return a.groupby(level="datetime", group_keys=False).transform(_q)


def quantile_backtest(alpha: pd.Series, fwd: pd.Series,
                      n_groups: int = 10, ppy: int = 12) -> QuantileResult:
    """Run quantile backtest: assign factor to n_groups buckets per date, compute group NAVs.

    NOTE: ``group_ann_return`` / ``group_nav`` have one entry per bucket that
    actually formed (index 0 = bottom, -1 = top). On a normal universe with
    adequate cross-sectional dispersion this equals ``n_groups``; under low
    dispersion (bins collapse) it can be fewer, and group-level stats become
    less reliable — callers should ensure adequate breadth (the report layer
    surfaces a low-coverage warning).
    """
    joined = pd.concat([alpha.rename("a"), fwd.rename("f")], axis=1).dropna()
    if joined.empty:
        return QuantileResult(n_groups)
    joined["g"] = _assign_groups(joined["a"], n_groups)
    joined = joined.dropna(subset=["g"])
    if joined.empty:
        return QuantileResult(n_groups)

    dt = joined.index.get_level_values("datetime")
    grp_ret = joined.groupby([dt, joined["g"]])["f"].mean()
    # Rename index levels so unstack("g") works reliably
    grp_ret.index.names = ["datetime", "g"]
    wide = grp_ret.unstack("g").sort_index()   # rows=date, cols=group label
    cols = sorted(wide.columns)
    nper = len(wide)

    group_ann: List[float] = []
    group_nav: List[List[float]] = []
    for g in cols:
        col = wide[g].fillna(0.0)
        nav = (1 + col).cumprod()
        group_nav.append([float(v) for v in nav.values])
        navend = float(nav.iloc[-1]) if nper else float("nan")
        ann = navend ** (ppy / nper) - 1 if (nper > 0 and navend > 0) else float("nan")
        group_ann.append(float(ann))

    if len(group_ann) >= 2:
        gi = pd.Series(range(len(group_ann)), dtype=float)
        ga = pd.Series(group_ann, dtype=float)
        monotonicity = float(gi.corr(ga, method="spearman"))
        long_short_spread = float(group_ann[-1] - group_ann[0])
    else:
        monotonicity = float("nan")
        long_short_spread = float("nan")

    return QuantileResult(n_groups, group_ann, group_nav, monotonicity, long_short_spread)
