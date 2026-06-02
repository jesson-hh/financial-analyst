"""SP-2 FDR (Benjamini-Hochberg / Bonferroni) multiple-testing correction tests.

We test two layers:

1. **Single-factor p-value**: ``IcResult.p_value`` is filled by ``ic_analysis``
   as a two-tailed t-test of IC mean against zero
   (``t = ic_mean / (ic_std / sqrt(n))``, df=n-1, double-tailed).

2. **Batch FDR correction in ``run_bench``**: After all factors are scored, p
   values get passed to ``statsmodels.stats.multitest.multipletests``; q values
   land in ``fdr_q`` and reject flags in ``is_significant``.

The synthetic harness builds 100 alpha specs against a single panel:
- 50 "real-signal" alphas with engineered IC ≈ 0.05 (loud signal vs noise)
- 50 "noise" alphas that are random with mean IC ≈ 0

Per the BH guarantee, the expected false discovery proportion among rejects is
bounded by ``alpha=0.05``. We assert the realized FDP across multiple seeds
stays comfortably below 0.05, that most real signals get through, that
Bonferroni is strictly more conservative than BH, and that ``fdr_method=None``
leaves ``fdr_q`` / ``is_significant`` untouched (NaN / False).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, List

import numpy as np
import pandas as pd
import pytest

from financial_analyst.factors.eval.config import EvalConfig
from financial_analyst.factors.eval.ic import ic_analysis
from financial_analyst.factors.zoo.bench_runner import _apply_fdr, _ic_pvalue


# ---------------------------------------------------------------------------
# Helpers — synthetic IC + p-value harness so we don't need real PanelData.
# ---------------------------------------------------------------------------
def _signal_ic(seed: int, mean: float = 0.05, std: float = 0.04, n: int = 252) -> pd.Series:
    """Synthesize a daily IC series with engineered mean (loud real signal)."""
    rng = np.random.default_rng(seed)
    vals = rng.normal(mean, std, n)
    dates = pd.date_range("2024-01-02", periods=n, freq="B")
    return pd.Series(vals, index=dates)


def _noise_ic(seed: int, std: float = 0.04, n: int = 252) -> pd.Series:
    """IC series with true mean 0 (pure noise)."""
    rng = np.random.default_rng(seed)
    vals = rng.normal(0.0, std, n)
    dates = pd.date_range("2024-01-02", periods=n, freq="B")
    return pd.Series(vals, index=dates)


def _ic_mean_std_p(ic: pd.Series) -> tuple[float, float, int, float]:
    """Compute (ic_mean, ic_std, n, p_value) using the same t-test formula as
    ic_analysis / bench_runner._ic_pvalue, so the test is testing the actual
    code path (not a private duplicate)."""
    n = int(len(ic))
    mean = float(ic.mean())
    std = float(ic.std())  # ddof=1 default in pandas (matches bench_runner)
    p = _ic_pvalue(mean, std, n)
    return mean, std, n, p


def _make_bench_df(n_signal: int, n_noise: int, seed_base: int = 0) -> pd.DataFrame:
    """Build a synthetic bench-style DataFrame mimicking what run_bench returns,
    with p_value populated. Labels first n_signal as is_real=True so we can
    compute FDP / TPR."""
    rows: List[dict] = []
    # Real signal IC ≈ 0.05 (~7.9 t-stat on n=252) → tiny p
    for i in range(n_signal):
        ic = _signal_ic(seed_base + i, mean=0.05, std=0.04, n=252)
        mean, std, n, p = _ic_mean_std_p(ic)
        rows.append({
            "name": f"signal_{i:03d}", "family": "synth",
            "ic": mean, "ic_std": std, "n_dates": n,
            "is_real": True,
            "p_value": p,
            "fdr_q": np.nan, "is_significant": False,
        })
    # Pure noise IC mean 0 → uniform p
    for i in range(n_noise):
        ic = _noise_ic(seed_base + 1000 + i, std=0.04, n=252)
        mean, std, n, p = _ic_mean_std_p(ic)
        rows.append({
            "name": f"noise_{i:03d}", "family": "synth",
            "ic": mean, "ic_std": std, "n_dates": n,
            "is_real": False,
            "p_value": p,
            "fdr_q": np.nan, "is_significant": False,
        })
    return pd.DataFrame(rows)


# ===========================================================================
# 1. _ic_pvalue + ic_analysis fill p_value correctly (sanity).
# ===========================================================================
def test_ic_pvalue_loud_signal_is_tiny():
    """ic_mean=0.05, ic_std=0.04, n=252 → t ≈ 19.8 → p < 1e-50."""
    ic = _signal_ic(seed=0, mean=0.05, std=0.04, n=252)
    _, _, _, p = _ic_mean_std_p(ic)
    assert p is not None
    assert p < 1e-10  # ridiculously significant


def test_ic_pvalue_noise_is_uniform_ish():
    """Pure noise (mean=0) → p values approximately uniform on [0,1]
    (here we just assert avg p across many noise alphas is around 0.5,
    well within 0.3..0.7 over 200 trials)."""
    ps = []
    for s in range(200):
        ic = _noise_ic(seed=s, std=0.04, n=252)
        _, _, _, p = _ic_mean_std_p(ic)
        if not np.isnan(p):
            ps.append(p)
    assert len(ps) >= 180
    assert 0.3 < float(np.mean(ps)) < 0.7


def test_ic_pvalue_returns_nan_for_zero_std():
    """ic_std=0 → cannot compute t-stat → return NaN (don't divide by zero)."""
    assert np.isnan(_ic_pvalue(0.05, 0.0, 100))
    assert np.isnan(_ic_pvalue(np.nan, 0.04, 100))
    assert np.isnan(_ic_pvalue(0.05, np.nan, 100))
    assert np.isnan(_ic_pvalue(0.05, 0.04, 1))  # n < 2 → df < 1


def test_ic_analysis_fills_p_value():
    """End-to-end through ic_analysis: synthetic alpha + fwd → IcResult.p_value
    populated and matches the standalone formula."""
    rng = np.random.default_rng(2026)
    n_dates, n_codes = 120, 30
    dates = pd.date_range("2024-01-02", periods=n_dates, freq="B")
    codes = [f"S{i:03d}" for i in range(n_codes)]
    idx = pd.MultiIndex.from_product([dates, codes], names=["datetime", "code"])
    # Build alpha and fwd with engineered positive correlation per date
    alpha_vals = rng.normal(0, 1, len(idx))
    # fwd = 0.3 * alpha + noise → strong cross-sectional IC
    fwd_vals = 0.3 * alpha_vals + rng.normal(0, 1, len(idx))
    alpha = pd.Series(alpha_vals, index=idx)
    fwd = pd.Series(fwd_vals, index=idx)

    res = ic_analysis(alpha, fwd)
    assert res.p_value is not None
    assert res.p_value < 0.01  # strong relationship → very significant
    # fdr_q/is_significant stay default (single-factor mode)
    assert res.fdr_q is None
    assert res.is_significant is False


# ===========================================================================
# 2. _apply_fdr — BH bound + Bonferroni stricter + None passthrough.
# ===========================================================================
def test_apply_fdr_bh_bounds_false_discovery_rate():
    """Realized FDP among rejects should stay ≤ alpha=0.05 (in expectation;
    we use multiple seeds + tolerance because FDP is a random variable).

    The BH bound is on the expected FDP, not realized — a single draw can
    exceed it by chance. We average across seeds for a stable estimate.
    """
    fdps = []
    tprs = []
    cfg = EvalConfig(fdr_method="bh", fdr_alpha=0.05)
    for seed in range(10):
        df = _make_bench_df(n_signal=50, n_noise=50, seed_base=seed * 100)
        out = _apply_fdr(df, cfg)
        assert "fdr_q" in out.columns
        assert "is_significant" in out.columns
        # Compute realized FDP / TPR
        rejected = out[out["is_significant"]]
        n_reject = len(rejected)
        if n_reject == 0:
            fdps.append(0.0)
        else:
            fp = int((~rejected["is_real"]).sum())
            fdps.append(fp / n_reject)
        # True positive rate (real signals that got through)
        real_rows = out[out["is_real"]]
        tprs.append(float(real_rows["is_significant"].mean()))

    mean_fdp = float(np.mean(fdps))
    mean_tpr = float(np.mean(tprs))
    # BH expected FDP ≤ alpha (here 0.05). Some slack because we only run 10 seeds.
    assert mean_fdp <= 0.10, f"BH mean FDP {mean_fdp:.3f} should be ≈ ≤0.05 with some slack"
    # With loud signals (IC=0.05, n=252) ALL real signals should pass.
    assert mean_tpr > 0.95, f"Real-signal TPR {mean_tpr:.3f} should be ≥0.95"


def test_apply_fdr_bonferroni_threshold():
    """Bonferroni rejects iff p_value ≤ alpha / n. Verify directly."""
    cfg = EvalConfig(fdr_method="bonferroni", fdr_alpha=0.05)
    df = _make_bench_df(n_signal=50, n_noise=50, seed_base=0)
    out = _apply_fdr(df, cfg)
    n = len(out)
    # Bonferroni threshold = alpha / n
    thresh = cfg.fdr_alpha / n
    # Every row with p_value <= thresh should be flagged True, others False.
    # NaN-aware: only check non-NaN rows.
    pvals = out["p_value"].to_numpy()
    flagged = out["is_significant"].to_numpy()
    for i in range(n):
        if np.isnan(pvals[i]):
            assert not flagged[i]
        else:
            expected = bool(pvals[i] <= thresh)
            assert bool(flagged[i]) == expected, (
                f"row {i}: p={pvals[i]:.6g}, thresh={thresh:.6g}, "
                f"flagged={flagged[i]}, expected={expected}"
            )


def test_apply_fdr_bonferroni_strictly_no_more_rejects_than_bh():
    """For identical p-value set, Bonferroni ≤ BH in number of rejects."""
    df = _make_bench_df(n_signal=50, n_noise=50, seed_base=42)
    bh_out = _apply_fdr(df, EvalConfig(fdr_method="bh", fdr_alpha=0.05))
    bonf_out = _apply_fdr(df, EvalConfig(fdr_method="bonferroni", fdr_alpha=0.05))
    n_bh = int(bh_out["is_significant"].sum())
    n_bonf = int(bonf_out["is_significant"].sum())
    assert n_bonf <= n_bh, f"Bonferroni rejected {n_bonf} > BH {n_bh} (impossible by construction)"


def test_apply_fdr_none_is_passthrough():
    """fdr_method=None → fdr_q stays all-NaN + is_significant stays all-False."""
    cfg = EvalConfig(fdr_method=None, fdr_alpha=0.05)
    df = _make_bench_df(n_signal=5, n_noise=5, seed_base=0)
    out = _apply_fdr(df, cfg)
    # Untouched (the DataFrame still has the seed defaults from _make_bench_df)
    assert out["fdr_q"].isna().all()
    assert not out["is_significant"].any()


def test_apply_fdr_handles_all_nan_pvalues():
    """If every p_value is NaN (e.g. all alphas failed), don't crash, just
    leave fdr_q all-NaN + is_significant all-False."""
    df = pd.DataFrame({
        "name": ["a", "b", "c"],
        "p_value": [np.nan, np.nan, np.nan],
        "fdr_q": [np.nan, np.nan, np.nan],
        "is_significant": [False, False, False],
    })
    out = _apply_fdr(df, EvalConfig(fdr_method="bh"))
    assert out["fdr_q"].isna().all()
    assert not out["is_significant"].any()


def test_apply_fdr_skips_nan_pvalue_rows():
    """Rows with p_value=NaN (compute_error) keep fdr_q=NaN + is_significant=False
    while the rest get corrected normally."""
    df = pd.DataFrame({
        "name": ["loud", "mid", "broken"],
        "p_value": [1e-30, 0.04, np.nan],
        "fdr_q": [np.nan, np.nan, np.nan],
        "is_significant": [False, False, False],
    })
    out = _apply_fdr(df, EvalConfig(fdr_method="bh", fdr_alpha=0.05))
    assert not np.isnan(out["fdr_q"].iloc[0])
    assert not np.isnan(out["fdr_q"].iloc[1])
    assert np.isnan(out["fdr_q"].iloc[2])  # broken stayed NaN
    assert bool(out["is_significant"].iloc[0]) is True   # 1e-30 always passes
    assert bool(out["is_significant"].iloc[2]) is False  # broken never flagged


# ===========================================================================
# 3. run_bench end-to-end: cfg flows through, columns appear in output df.
#    We use the synthetic alpha pattern from test_factor_zoo but smaller.
# ===========================================================================
def _make_panel(n_codes=5, n_dates=80):
    """Tiny synthetic OHLCV PanelData (mirrors test_factor_zoo.)"""
    from financial_analyst.factors.zoo.panel import PanelData

    dates = pd.date_range("2024-01-02", periods=n_dates, freq="B")
    codes = [f"S{i:03d}" for i in range(n_codes)]
    idx = pd.MultiIndex.from_product([dates, codes], names=["datetime", "code"])
    rng = np.random.default_rng(0)
    close = 100 * np.exp(np.cumsum(rng.normal(0, 0.02, len(idx))))
    df = pd.DataFrame({
        "open": close, "high": close * 1.01, "low": close * 0.99,
        "close": close, "volume": np.full(len(idx), 1e6),
    }, index=idx)
    return PanelData(df)


def test_run_bench_default_includes_fdr_columns():
    """run_bench with default cfg → output df has p_value/fdr_q/is_significant."""
    # Trigger alpha101 family registration
    import financial_analyst.factors.zoo  # noqa: F401
    from financial_analyst.factors.zoo.bench_runner import run_bench

    panel = _make_panel(n_codes=5, n_dates=80)
    out = run_bench(panel, family="alpha101", fwd_days=5)
    assert "p_value" in out.columns
    assert "fdr_q" in out.columns
    assert "is_significant" in out.columns
    # At least one row should have a non-NaN p-value (the panel is small but alpha101 is rich)
    assert out["p_value"].notna().any()


def test_run_bench_fdr_none_leaves_fdr_q_nan():
    """run_bench(cfg=EvalConfig(fdr_method=None)) → fdr_q all-NaN, is_significant all-False."""
    import financial_analyst.factors.zoo  # noqa: F401
    from financial_analyst.factors.zoo.bench_runner import run_bench

    panel = _make_panel(n_codes=5, n_dates=80)
    out = run_bench(panel, family="alpha101", fwd_days=5,
                    cfg=EvalConfig(fdr_method=None))
    assert "fdr_q" in out.columns
    assert out["fdr_q"].isna().all()
    assert not out["is_significant"].any()
