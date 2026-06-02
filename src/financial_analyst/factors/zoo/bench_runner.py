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

from financial_analyst.factors.eval.config import EvalConfig
from financial_analyst.factors.zoo.panel import PanelData
from financial_analyst.factors.zoo.registry import AlphaSpec, list_alphas

# SP-2 FDR method → statsmodels.multipletests method name. None = skip correction.
_FDR_METHOD_MAP = {"bh": "fdr_bh", "bonferroni": "bonferroni"}


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
    """Factor health classification (alive / reversed / dead):

    Single window: judge by |RankICIR| strength + |RankIC| magnitude
    → strong / moderate / weak / dead. If prev_rank_ic is given AND current
    sign flips with both magnitudes sufficient → reversed, a strong signal
    of factor decay / failure.
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


def _ic_pvalue(ic_mean: float, ic_std: float, n: int) -> float:
    """Single-factor IC t-test 双尾 p (SP-2). 大 n 时 t-dist 接近正态.

    NaN/0 std/n<2 → np.nan (无法判定 → 后续 FDR 跳过此因子).
    与 ic_analysis 中算法等价, 共享同一公式 t = ic_mean / (ic_std / sqrt(n)).
    """
    if n < 2 or np.isnan(ic_mean) or np.isnan(ic_std) or ic_std <= 0:
        return np.nan
    try:
        from scipy import stats as _scipy_stats
        t_stat = ic_mean / (ic_std / np.sqrt(n))
        return float(2.0 * _scipy_stats.t.sf(abs(t_stat), df=n - 1))
    except Exception:
        return np.nan


def bench_one(
    spec: AlphaSpec,
    panel: PanelData,
    fwd_returns: pd.Series,
) -> dict:
    """Run one alpha against a pre-computed forward returns panel.

    Returns a dict with: name, family, ic, rank_ic, ir, rank_ir,
    hit_rate, n_dates, n_obs, status, error, p_value, fdr_q, is_significant.

    SP-2: ``p_value`` 是单因子 IC t-test 双尾 p. ``fdr_q`` / ``is_significant``
    默认 NaN / False, 由 ``run_bench`` 跑完全部因子后批量回填 (BH/Bonferroni 校正).
    """
    try:
        alpha = spec.compute(panel)
    except Exception as e:
        return {
            "name": spec.name, "family": spec.family,
            "ic": np.nan, "rank_ic": np.nan, "ir": np.nan, "rank_ir": np.nan,
            "hit_rate": np.nan, "n_dates": 0, "n_obs": 0,
            "status": "compute_error", "error": f"{type(e).__name__}: {e}", "state": "无数据",
            "p_value": np.nan, "fdr_q": np.nan, "is_significant": False,
        }

    if not isinstance(alpha, pd.Series):
        return {
            "name": spec.name, "family": spec.family,
            "ic": np.nan, "rank_ic": np.nan, "ir": np.nan, "rank_ir": np.nan,
            "hit_rate": np.nan, "n_dates": 0, "n_obs": 0,
            "status": "bad_output", "error": f"compute returned {type(alpha).__name__}, expected pd.Series", "state": "无数据",
            "p_value": np.nan, "fdr_q": np.nan, "is_significant": False,
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
    p_value = _ic_pvalue(ic_mean, ic_std, len(daily_ic))

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
        "p_value": p_value,
        "fdr_q": np.nan,            # 由 run_bench 批量回填
        "is_significant": False,    # 由 run_bench 批量回填
    }


def _apply_fdr(df: pd.DataFrame, cfg: EvalConfig) -> pd.DataFrame:
    """SP-2: 批量应用 FDR/Bonferroni 校正, 回填 fdr_q + is_significant 列.

    - ``cfg.fdr_method=None`` → 跳过, fdr_q 全 NaN + is_significant 全 False (无操作).
    - 'bh' → Benjamini-Hochberg (statsmodels 'fdr_bh').
    - 'bonferroni' → Bonferroni (statsmodels 'bonferroni'), 阈值 alpha/n.
    - 行 p_value=NaN 的因子 (compute_error 或 ic_std=0) 不参与校正, 保留 NaN/False.
    - statsmodels 未装 → 留 NaN/False (软降级, 不抛). 测试装 statsmodels.
    """
    if cfg.fdr_method is None:
        return df
    method_sm = _FDR_METHOD_MAP.get(cfg.fdr_method)
    if method_sm is None:
        return df
    pvals_raw = df["p_value"].to_numpy(dtype=float)
    valid_mask = ~np.isnan(pvals_raw)
    if not valid_mask.any():
        return df
    try:
        from statsmodels.stats.multitest import multipletests
    except ImportError:
        return df
    valid_p = pvals_raw[valid_mask]
    reject, qvals, _, _ = multipletests(valid_p, method=method_sm, alpha=cfg.fdr_alpha)
    # 用 numpy 数组本地拼接, 避免 pandas 2.x SettingWithCopy 警告
    q_full = np.full(len(df), np.nan, dtype=float)
    sig_full = np.zeros(len(df), dtype=bool)
    q_full[valid_mask] = qvals
    sig_full[valid_mask] = reject
    df = df.copy()
    df["fdr_q"] = q_full
    df["is_significant"] = sig_full
    return df


def run_bench(
    panel: PanelData,
    family: Optional[str] = None,
    fwd_days: int = 5,
    names: Optional[List[str]] = None,
    prev_bench: Optional[pd.DataFrame] = None,
    cfg: Optional[EvalConfig] = None,
) -> pd.DataFrame:
    """Bench every registered alpha (optionally filtered) against the
    panel's forward N-day returns. Returns a tidy DataFrame sorted by
    |rank_ir| descending so the strongest-signal alphas surface on top.

    When ``prev_bench`` (a previous bench DataFrame with name + rank_ic) is
    supplied, any alpha whose rank_ic flipped sign vs last time is marked
    state="反向" (reversed) — the IC-decay early-warning.

    SP-2: ``cfg`` controls FDR correction. Default ``EvalConfig()`` →
    ``fdr_method='bh'`` (Benjamini-Hochberg). After all alphas are scored, p
    values are collected and passed to ``multipletests``; the resulting q values
    + reject flags are backfilled to ``fdr_q`` + ``is_significant``. Pass
    ``cfg=EvalConfig(fdr_method=None)`` to skip correction (fdr_q all-NaN,
    is_significant all-False).
    """
    cfg = cfg or EvalConfig()
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
    # SP-2: 批量 FDR/Bonferroni 校正 (默认 bh; cfg.fdr_method=None 时直通)
    out = _apply_fdr(out, cfg)
    out = out.sort_values("rank_ir", key=lambda s: s.abs(), ascending=False, na_position="last").reset_index(drop=True)
    return out
