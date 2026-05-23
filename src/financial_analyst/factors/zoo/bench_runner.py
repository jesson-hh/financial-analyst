"""Alpha bench runner — for each registered alpha, compute its
cross-sectional rank-IC against forward N-day returns and aggregate
into IC mean / IR / hit rate.

Usage::

    panel = PanelData.from_loader(loader, codes, "2024-01-01", "2024-12-31")
    results = run_bench(panel, family="gtja191", fwd_days=5)
    print(results.head())  # name | family | ic | ir | rank_ic | rank_ir | hit_rate | n_dates

The runner is deliberately synchronous and panel-once: alphas re-using
the same operators benefit from pandas vectorisation, and bench scans
of 100+ alphas across 300+ stocks × 200+ dates finish in seconds.

Numpy correlation emits noisy ``RuntimeWarning: invalid value
encountered in divide`` whenever a window has zero variance — that's
expected for low-activity stocks on a quiet day, not a bug. We trap
those warnings inside the bench loop so the CLI stays readable.
"""
from __future__ import annotations
import warnings
from typing import List, Optional

import numpy as np
import pandas as pd

from financial_analyst.factors.zoo.panel import PanelData
from financial_analyst.factors.zoo.registry import AlphaSpec, list_alphas


def _forward_returns(panel: PanelData, n: int) -> pd.Series:
    """N-day forward log return per (datetime, code)."""
    close = panel.close
    # shift -n at code level so we look n bars into the future
    fwd_close = close.groupby(level="code", group_keys=False).shift(-n)
    return np.log(fwd_close / close)


def _ic_series(alpha: pd.Series, fwd: pd.Series, rank: bool = False) -> pd.Series:
    """Daily cross-sectional correlation between alpha and forward returns.

    Returns a Series indexed by datetime. NaN entries (e.g., near series
    end where fwd is undefined) are dropped.
    """
    joined = pd.concat([alpha.rename("a"), fwd.rename("f")], axis=1).dropna()
    if rank:
        # rank correlation = Spearman
        ranked = joined.groupby(level="datetime").rank()
        return ranked.groupby(level="datetime").apply(lambda d: d["a"].corr(d["f"]))
    return joined.groupby(level="datetime").apply(lambda d: d["a"].corr(d["f"]))


def _hit_rate(alpha: pd.Series, fwd: pd.Series) -> float:
    """Fraction of (date, code) cells where sign(alpha - cs_mean(alpha))
    == sign(fwd_return - cs_mean(fwd_return))."""
    joined = pd.concat([alpha.rename("a"), fwd.rename("f")], axis=1).dropna()
    if len(joined) == 0:
        return float("nan")
    a_demean = joined["a"] - joined.groupby(level="datetime")["a"].transform("mean")
    f_demean = joined["f"] - joined.groupby(level="datetime")["f"].transform("mean")
    matches = (np.sign(a_demean) == np.sign(f_demean)) & (a_demean != 0) & (f_demean != 0)
    return float(matches.sum() / matches.size)


def classify_factor(rank_ic, rank_ir, prev_rank_ic=None) -> str:
    """因子健康分类 (参考 Vibe-Trading 的 alive/reversed/dead):

    单窗口看 |RankICIR| 强度 + |RankIC| 量级 → 有效 / 一般 / 偏弱 / 失效。
    若给了上期 RankIC (prev_rank_ic) 且本期与上期反号且两期都够大 → 反向 (reversed),
    即 IC 方向翻转, 因子衰减/失效的强信号。
    """
    try:
        ric = float(rank_ic); rir = float(rank_ir)
    except (TypeError, ValueError):
        return "无数据"
    if np.isnan(ric) or np.isnan(rir):
        return "无数据"
    aic, air = abs(ric), abs(rir)
    if prev_rank_ic is not None:
        try:
            pric = float(prev_rank_ic)
            if not np.isnan(pric) and ric * pric < 0 and aic >= 0.02 and abs(pric) >= 0.02:
                return "反向"  # reversed — sign flipped vs last bench
        except (TypeError, ValueError):
            pass
    if air >= 0.5 and aic >= 0.02:
        return "有效"   # alive
    if air >= 0.3:
        return "一般"
    if air >= 0.15:
        return "偏弱"
    return "失效"       # dead


def bench_one(
    spec: AlphaSpec,
    panel: PanelData,
    fwd_returns: pd.Series,
) -> dict:
    """Run one alpha against a pre-computed forward returns panel.

    Returns a dict with: name, family, ic, rank_ic, ir, rank_ir,
    hit_rate, n_dates, n_obs, status, error.
    """
    try:
        alpha = spec.compute(panel)
    except Exception as e:
        return {
            "name": spec.name, "family": spec.family,
            "ic": np.nan, "rank_ic": np.nan, "ir": np.nan, "rank_ir": np.nan,
            "hit_rate": np.nan, "n_dates": 0, "n_obs": 0,
            "status": "compute_error", "error": f"{type(e).__name__}: {e}", "state": "无数据",
        }

    if not isinstance(alpha, pd.Series):
        return {
            "name": spec.name, "family": spec.family,
            "ic": np.nan, "rank_ic": np.nan, "ir": np.nan, "rank_ir": np.nan,
            "hit_rate": np.nan, "n_dates": 0, "n_obs": 0,
            "status": "bad_output", "error": f"compute returned {type(alpha).__name__}, expected pd.Series", "state": "无数据",
        }

    daily_ic = _ic_series(alpha, fwd_returns, rank=False).dropna()
    daily_rank_ic = _ic_series(alpha, fwd_returns, rank=True).dropna()
    ic_mean = float(daily_ic.mean()) if len(daily_ic) else np.nan
    ic_std = float(daily_ic.std()) if len(daily_ic) else np.nan
    ir = float(ic_mean / ic_std) if ic_std and not np.isnan(ic_std) and ic_std > 0 else np.nan
    rank_ic_mean = float(daily_rank_ic.mean()) if len(daily_rank_ic) else np.nan
    rank_ic_std = float(daily_rank_ic.std()) if len(daily_rank_ic) else np.nan
    rank_ir = float(rank_ic_mean / rank_ic_std) if rank_ic_std and not np.isnan(rank_ic_std) and rank_ic_std > 0 else np.nan
    hit = _hit_rate(alpha, fwd_returns)

    joined = pd.concat([alpha.rename("a"), fwd_returns.rename("f")], axis=1).dropna()
    return {
        "name": spec.name, "family": spec.family,
        "ic": ic_mean, "rank_ic": rank_ic_mean,
        "ir": ir, "rank_ir": rank_ir,
        "hit_rate": hit,
        "n_dates": int(len(daily_ic)),
        "n_obs": int(len(joined)),
        "status": "ok",
        "error": "",
        "state": classify_factor(rank_ic_mean, rank_ir),
    }


def run_bench(
    panel: PanelData,
    family: Optional[str] = None,
    fwd_days: int = 5,
    names: Optional[List[str]] = None,
    prev_bench: Optional[pd.DataFrame] = None,
) -> pd.DataFrame:
    """Bench every registered alpha (optionally filtered) against the
    panel's forward N-day returns. Returns a tidy DataFrame sorted by
    |rank_ir| descending so the strongest-signal alphas surface on top.

    When ``prev_bench`` (a previous bench DataFrame with name + rank_ic) is
    supplied, any alpha whose rank_ic flipped sign vs last time is marked
    state="反向" (reversed) — the IC-decay early-warning.
    """
    if names:
        specs = []
        from financial_analyst.factors.zoo.registry import get
        for n in names:
            specs.append(get(n))
    else:
        specs = list_alphas(family=family)

    if not specs:
        raise ValueError(
            f"run_bench: no alphas to run (family={family!r}, names={names!r}). "
            f"Did you forget to import the family module?"
        )

    fwd = _forward_returns(panel, fwd_days)
    with warnings.catch_warnings(), np.errstate(invalid="ignore", divide="ignore"):
        warnings.filterwarnings("ignore", category=RuntimeWarning,
                                message="invalid value encountered")
        warnings.filterwarnings("ignore", category=RuntimeWarning,
                                message="Mean of empty slice")
        warnings.filterwarnings("ignore", category=RuntimeWarning,
                                message="Degrees of freedom <= 0")
        rows = [bench_one(spec, panel, fwd) for spec in specs]
    # 反向 (reversed) 检测: 与上次 bench 比 rank_ic 符号是否翻转
    if prev_bench is not None and "name" in getattr(prev_bench, "columns", []):
        prev_map = dict(zip(prev_bench["name"], prev_bench["rank_ic"]))
        for r in rows:
            st = classify_factor(r.get("rank_ic"), r.get("rank_ir"), prev_map.get(r["name"]))
            r["state"] = st
    out = pd.DataFrame(rows)
    out = out.sort_values("rank_ir", key=lambda s: s.abs(), ascending=False, na_position="last").reset_index(drop=True)
    return out
