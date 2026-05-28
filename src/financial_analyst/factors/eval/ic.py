"""IC 分析: 截面 Pearson/Spearman IC, ICIR, t值, 命中率, IC 序列, IC 衰减。"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd


@dataclass
class IcResult:
    ic_mean: float
    ic_std: float
    icir: float
    ic_tstat: float
    ic_win_rate: float
    rank_ic_mean: float
    rank_icir: float
    ic_series: List[Tuple[str, float]] = field(default_factory=list)
    ic_decay: List[Tuple[int, float, float]] = field(default_factory=list)


def _daily_corr(joined: pd.DataFrame, rank: bool) -> pd.Series:
    """Compute per-date correlation between columns 'a' and 'f'.

    If rank=True, rank within each date first (Spearman via rank-then-Pearson).
    """
    df = joined
    if rank:
        df = joined.groupby(level="datetime", group_keys=False).rank()
    with np.errstate(invalid="ignore", divide="ignore"):
        return df.groupby(level="datetime").apply(
            lambda d: d["a"].corr(d["f"]), include_groups=False
        )


def ic_analysis(
    alpha: pd.Series,
    fwd: pd.Series,
    fwd_by_horizon: Optional[Dict[int, pd.Series]] = None,
) -> IcResult:
    """Compute IC metrics between alpha factor and forward returns.

    Parameters
    ----------
    alpha:
        Factor values; MultiIndex (datetime, code).
    fwd:
        Forward returns; same index structure as alpha.
    fwd_by_horizon:
        Optional dict mapping horizon (int days) → forward-return Series for
        IC-decay computation. One (horizon, ic, rank_ic) row per horizon.

    Returns
    -------
    IcResult with Pearson IC, Spearman IC, ICIR, t-stat, win-rate,
    IC series and IC-decay profile.
    """
    joined = pd.concat([alpha.rename("a"), fwd.rename("f")], axis=1).dropna()
    nan = float("nan")

    if joined.empty:
        return IcResult(nan, nan, nan, nan, nan, nan, nan, [], [])

    ic = _daily_corr(joined, rank=False).dropna()
    ric = _daily_corr(joined, rank=True).dropna()

    n = len(ic)
    ic_mean = float(ic.mean()) if n else nan
    ic_std = float(ic.std()) if n else nan
    icir = ic_mean / ic_std if (not np.isnan(ic_std) and ic_std > 0) else nan
    ic_tstat = ic_mean / ic_std * np.sqrt(n) if (not np.isnan(ic_std) and ic_std > 0) else nan
    _nz = ic[ic != 0]
    ic_win = float((np.sign(_nz) == np.sign(ic_mean)).mean()) if len(_nz) else nan

    r_n = len(ric)
    rank_ic_mean = float(ric.mean()) if r_n else nan
    rank_ic_std = float(ric.std()) if r_n else nan
    rank_icir = rank_ic_mean / rank_ic_std if (not np.isnan(rank_ic_std) and rank_ic_std > 0) else nan

    ic_series = [(str(pd.Timestamp(d).date()), float(v)) for d, v in ic.items()]

    decay: List[Tuple[int, float, float]] = []
    if fwd_by_horizon:
        for h in sorted(fwd_by_horizon):
            jh = pd.concat(
                [alpha.rename("a"), fwd_by_horizon[h].rename("f")], axis=1
            ).dropna()
            if jh.empty:
                decay.append((int(h), nan, nan))
                continue
            ich = float(_daily_corr(jh, rank=False).mean())
            rich = float(_daily_corr(jh, rank=True).mean())
            decay.append((int(h), ich, rich))

    return IcResult(
        ic_mean,
        ic_std,
        icir,
        ic_tstat,
        ic_win,
        rank_ic_mean,
        rank_icir,
        ic_series,
        decay,
    )
