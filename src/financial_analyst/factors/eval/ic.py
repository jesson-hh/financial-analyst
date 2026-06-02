"""IC 分析: 截面 Pearson/Spearman IC, ICIR, t值, 命中率, IC 序列, IC 衰减。

SP-2 加 FDR 多重检验校正字段:
- ``p_value``: 单因子 IC t-test 双尾 p (基于 ic_mean / (ic_std / sqrt(n_dates))).
- ``fdr_q``: 批量校正后 q 值 (单因子模式留 None, 由 bench_runner.run_bench 在跑完
  所有因子后回填). 用法: ``df[df.is_significant]`` 取 FDR-adjusted 通过子集.
- ``is_significant``: bool, 校正后是否过阈值 (单因子模式恒 False, 等 bench 回填).
"""
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
    # SP-2 FDR 多重检验校正字段 (向后兼容: 默认 None / False 不影响旧调用方)
    p_value: Optional[float] = None
    fdr_q: Optional[float] = None
    is_significant: bool = False


def _daily_corr(joined: pd.DataFrame, rank: bool) -> pd.Series:
    """Compute per-date correlation between columns 'a' and 'f'.

    If rank=True, rank within each date first (Spearman via rank-then-Pearson).
    """
    df = joined
    if rank:
        df = joined.groupby(level="datetime", group_keys=False).rank()
    with np.errstate(invalid="ignore", divide="ignore"):
        # group by the index level (not a column), so no grouping column is
        # passed to the lambda — matches bench_runner._ic_series and stays
        # portable across pandas 2.0–2.3 (avoids the 2.2-only include_groups kwarg).
        return df.groupby(level="datetime").apply(lambda d: d["a"].corr(d["f"]))


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

    # SP-2: 单因子 IC t-test 双尾 p (大 n 时 t-dist → 正态; 用 scipy.stats.t 兼容小 n).
    # 需要 n>=2 才有非零自由度; ic_std 必须正才能除. 否则 p_value=None (无法判定).
    p_value: Optional[float] = None
    if n >= 2 and not np.isnan(ic_std) and ic_std > 0:
        try:
            from scipy import stats as _scipy_stats
            t_stat = ic_mean / (ic_std / np.sqrt(n))
            # 双尾 p = 2 * P(T > |t_stat|), df = n - 1
            p_value = float(2.0 * _scipy_stats.t.sf(abs(t_stat), df=n - 1))
        except Exception:
            p_value = None

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
        p_value=p_value,
        fdr_q=None,           # 单因子模式不批校正; 由 run_bench 回填
        is_significant=False,  # 同上
    )
