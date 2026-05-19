"""Unit tests for the alpha zoo (v1.3.0).

Smoke-tests every layer:
- registry: register / get / list / families
- panel: alias normalisation, column injection
- operators: rank / ts_max / delta / correlation on tiny synthetic data
- bench: end-to-end on a 5-code × 50-day synthetic panel
"""
from __future__ import annotations
import numpy as np
import pandas as pd
import pytest

from financial_analyst.factors.zoo import (
    AlphaSpec, register, get, list_alphas, families, PanelData,
)
from financial_analyst.factors.zoo.registry import _clear_registry_for_tests
from financial_analyst.factors.zoo.operators import (
    rank, ts_rank, ts_max, ts_min, ts_sum, ts_mean, delta, delay,
    correlation, decay_linear, stddev, signedpower, log, sign,
)
from financial_analyst.factors.zoo.bench_runner import run_bench, bench_one


# ----- fixtures -------------------------------------------------------------


def _make_panel(n_codes: int = 5, n_dates: int = 50, seed: int = 7) -> PanelData:
    np.random.seed(seed)
    codes = [f"SH{600000 + i:06d}" for i in range(n_codes)]
    dates = pd.date_range("2024-01-01", periods=n_dates, freq="B")
    rows = []
    for code in codes:
        base = 50 + np.random.randn() * 5
        rets = np.random.randn(n_dates) * 0.015
        close = base * np.exp(np.cumsum(rets))
        rows += [{
            "datetime": d, "code": code,
            "open": close[i] * (1 + np.random.randn() * 0.003),
            "high": close[i] * (1 + abs(np.random.randn()) * 0.008),
            "low": close[i] * (1 - abs(np.random.randn()) * 0.008),
            "close": close[i],
            "volume": float(int(1e6 * abs(1 + np.random.randn() * 0.2))),
        } for i, d in enumerate(dates)]
    df = pd.DataFrame(rows).set_index(["datetime", "code"]).sort_index()
    return PanelData(df)


# ----- registry --------------------------------------------------------------


def test_registry_contains_shipped_families():
    fams = set(families())
    assert "alpha101" in fams
    assert "gtja191" in fams
    assert "qlib158" in fams  # added in v1.3.2


def test_registry_list_filtered_by_family():
    a101 = list_alphas(family="alpha101")
    gtja = list_alphas(family="gtja191")
    qlib = list_alphas(family="qlib158")
    # Baselines:
    # v1.3.0: 10 + 12 = 22
    # v1.3.1: +12 alpha101, +15 gtja191 = 49
    # v1.3.2: +9 alpha101, +11 gtja191, +35 qlib158 = 104
    # v1.3.3: +11 alpha101, +6 gtja191, +21 qlib158 = 142
    # Lower bounds prevent silent regressions; expect counts to tick up
    # monotonically with future patch releases.
    assert len(a101) >= 42, f"alpha101 dropped below v1.3.3 baseline: {len(a101)}"
    assert len(gtja) >= 44, f"gtja191 dropped below v1.3.3 baseline: {len(gtja)}"
    assert len(qlib) >= 56, f"qlib158 dropped below v1.3.3 baseline: {len(qlib)}"
    assert all(s.family == "alpha101" for s in a101)
    assert all(s.family == "gtja191" for s in gtja)
    assert all(s.family == "qlib158" for s in qlib)


def test_registry_get_unknown_raises_helpful():
    with pytest.raises(KeyError, match="Known families"):
        get("alpha_made_up_name")


def test_registry_duplicate_with_different_fn_raises():
    """Re-registering an existing name with a different compute fn must fail
    — silent overwrite would mask a bug where two families clash on a name."""
    a = list_alphas()[0]
    fn = lambda p: p.close  # different identity from a.compute
    new = AlphaSpec(
        name=a.name, family="test", description="", formula_text="", compute=fn,
    )
    with pytest.raises(ValueError, match="already registered"):
        register(new)


# ----- panel ----------------------------------------------------------------


def test_panel_normalises_vol_alias():
    """Tushare returns ``vol`` not ``volume``; PanelData should accept both."""
    df = pd.DataFrame(
        [{"datetime": pd.Timestamp("2024-01-02"), "code": "SH600519",
          "open": 100, "high": 101, "low": 99, "close": 100.5, "vol": 1e6}]
    ).set_index(["datetime", "code"])
    p = PanelData(df)
    assert "volume" in p.df.columns
    assert "vol" not in p.df.columns


def test_panel_synthesises_vwap_and_amount():
    df = pd.DataFrame(
        [{"datetime": pd.Timestamp("2024-01-02"), "code": "SH600519",
          "open": 100, "high": 102, "low": 98, "close": 101, "volume": 1e6}]
    ).set_index(["datetime", "code"])
    p = PanelData(df)
    # vwap = (h+l+c)/3 = (102+98+101)/3 = 100.333...
    assert abs(p.vwap.iloc[0] - 100.3333) < 1e-3
    assert p.amount.iloc[0] == pytest.approx(101 * 1e6)


def test_panel_missing_required_raises():
    df = pd.DataFrame([{"datetime": pd.Timestamp("2024-01-02"), "code": "SH600519",
                        "open": 1, "high": 1, "low": 1, "close": 1}]  # no volume / vol
                      ).set_index(["datetime", "code"])
    with pytest.raises(ValueError, match="missing required columns"):
        PanelData(df)


# ----- operators ------------------------------------------------------------


def test_rank_is_cross_sectional_per_date():
    """rank should rank within each date, not across all (date, code) cells."""
    idx = pd.MultiIndex.from_product(
        [pd.date_range("2024-01-01", periods=2, freq="B"), ["A", "B", "C"]],
        names=["datetime", "code"],
    )
    s = pd.Series([1.0, 2.0, 3.0, 10.0, 20.0, 30.0], index=idx)
    r = rank(s)
    # Within each date, the three values should rank 1/3, 2/3, 3/3 = 0.333, 0.667, 1.0
    assert r.iloc[0] == pytest.approx(1 / 3)
    assert r.iloc[2] == pytest.approx(1.0)
    assert r.iloc[3] == pytest.approx(1 / 3)
    assert r.iloc[5] == pytest.approx(1.0)


def test_delta_per_code_no_cross_bleed():
    """delta(close, 1) for stock B should not see stock A's prior close."""
    idx = pd.MultiIndex.from_tuples(
        [(pd.Timestamp("2024-01-02"), "A"),
         (pd.Timestamp("2024-01-02"), "B"),
         (pd.Timestamp("2024-01-03"), "A"),
         (pd.Timestamp("2024-01-03"), "B")],
        names=["datetime", "code"],
    )
    s = pd.Series([100, 200, 110, 195], index=idx).sort_index()
    d = delta(s, 1)
    # A: 110-100=10 on day 2; B: 195-200=-5 on day 2; day 1 = NaN
    assert d.loc[(pd.Timestamp("2024-01-03"), "A")] == 10
    assert d.loc[(pd.Timestamp("2024-01-03"), "B")] == -5
    assert pd.isna(d.loc[(pd.Timestamp("2024-01-02"), "A")])


def test_ts_max_min_periods_window():
    """ts_max requires a full window — earlier rows must be NaN."""
    panel = _make_panel(n_codes=3, n_dates=10)
    tm = ts_max(panel.close, 5)
    # first 4 rows per code should be NaN
    for code in panel.codes():
        sub = tm.xs(code, level="code")
        assert sub.iloc[:4].isna().all()
        assert not sub.iloc[4:].isna().any()


def test_signedpower_preserves_sign():
    s = pd.Series([-2.0, -1.0, 0.0, 1.0, 2.0])
    out = signedpower(s, 2.0)
    # sign(x) * |x|^2 → -4, -1, 0, 1, 4
    assert list(out) == [-4.0, -1.0, 0.0, 1.0, 4.0]


# ----- bench runner ---------------------------------------------------------


def test_bench_one_handles_compute_error():
    """A buggy alpha should report 'compute_error' rather than crashing the run."""
    def boom(p):
        raise RuntimeError("synthetic")
    spec = AlphaSpec(
        name="boom_alpha", family="test", description="",
        formula_text="boom()", compute=boom,
    )
    panel = _make_panel(n_codes=3, n_dates=20)
    from financial_analyst.factors.zoo.bench_runner import _forward_returns
    fwd = _forward_returns(panel, 5)
    result = bench_one(spec, panel, fwd)
    assert result["status"] == "compute_error"
    assert "synthetic" in result["error"]


def test_run_bench_end_to_end():
    """Run real shipped alphas on a synthetic panel long enough to cover
    even the deep-history alphas (gtja025 uses sum(returns,250); alpha019
    and alpha024 reach back 100-250 days). Expect every alpha to score
    and the output to be sorted by |rank_IR| descending."""
    panel = _make_panel(n_codes=10, n_dates=300)
    result = run_bench(panel, family="gtja191", fwd_days=5)
    assert len(result) >= 12
    assert {"name", "family", "ic", "rank_ic", "ir", "rank_ir", "hit_rate"}.issubset(result.columns)
    # Status should be ok everywhere on this clean synthetic panel
    assert (result["status"] == "ok").all()
    # n_dates non-zero for every row — with 300 days even 250-day alphas have
    # ~45 valid forward-return cells
    assert (result["n_dates"] > 0).all(), (
        f"Some alphas produced 0 valid IC dates even on a 300-day panel: "
        f"{result[result['n_dates'] == 0]['name'].tolist()}"
    )


def test_run_bench_unknown_family_raises():
    panel = _make_panel(n_codes=3, n_dates=20)
    with pytest.raises(ValueError, match="no alphas to run"):
        run_bench(panel, family="nonexistent_family", fwd_days=5)
