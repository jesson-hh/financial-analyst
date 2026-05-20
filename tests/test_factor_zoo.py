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
    # v1.3.5: +37 alpha101, +65 gtja191, +46 qlib158 = 290
    # v1.3.6: +49 gtja191, +25 qlib158 = 364
    # v1.4.0: +19 alpha101 (IndNeutralize) = 383
    # v1.4.1: +3 alpha101 (now 101/101), +31 gtja191 (now 189/191),
    #         +23 qlib158 (now 150/158) = 440 total
    # v1.4.6: gtja143 (cumprod reduction of recursive SELF) + gtja149
    #         (downside beta via BenchmarkLoader) → gtja191 100% = 191/191.
    # Lower bounds prevent silent regressions; expect counts to tick up
    # monotonically with future patch releases.
    assert len(a101) >= 101, f"alpha101 dropped below v1.4.6 baseline: {len(a101)}"
    assert len(gtja) >= 191, f"gtja191 dropped below v1.4.6 baseline: {len(gtja)}"
    assert len(qlib) >= 150, f"qlib158 dropped below v1.4.6 baseline: {len(qlib)}"
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
    # ~45 valid forward-return cells. EXCEPT gtja149 (downside beta) which
    # legitimately returns all-NaN when no benchmark column is supplied —
    # the synthetic _make_panel doesn't inject one.
    no_dates = result[(result["n_dates"] == 0) & (result["name"] != "gtja149")]
    assert no_dates.empty, (
        f"Some alphas produced 0 valid IC dates even on a 300-day panel: "
        f"{no_dates['name'].tolist()}"
    )


def test_run_bench_unknown_family_raises():
    panel = _make_panel(n_codes=3, n_dates=20)
    with pytest.raises(ValueError, match="no alphas to run"):
        run_bench(panel, family="nonexistent_family", fwd_days=5)


def test_indneutralize_demean_per_industry(tmp_path):
    """v1.4.0: confirm indneutralize zeros the cross-sectional mean within
    each (date, industry) group. This is the core guarantee that lets the
    alpha101 IndNeutralize alphas produce industry-relative signals."""
    from financial_analyst.factors.zoo.operators import indneutralize

    dates = pd.date_range("2024-01-01", periods=3, freq="B")
    codes = ["SH600519", "SZ000858", "SH600036", "SH601318", "SZ300750"]
    industries = {
        "SH600519": "白酒", "SZ000858": "白酒",
        "SH600036": "银行", "SH601318": "保险",
        "SZ300750": "电气设备",
    }
    idx = pd.MultiIndex.from_product([dates, codes], names=["datetime", "code"])
    np.random.seed(0)
    x = pd.Series(np.random.randn(len(idx)) * 10 + 100, index=idx)
    g = pd.Series([industries[c] for _, c in idx], index=idx)

    y = indneutralize(x, g)

    # Per (date, industry), mean should be ~0
    for d in dates:
        for ind in set(industries.values()):
            members = [c for c, i in industries.items() if i == ind]
            if len(members) < 2:
                continue  # singleton group → y = 0 by definition
            vals = y.loc[(d, members)]
            assert abs(vals.mean()) < 1e-9, (
                f"indneutralize failed for ({d}, {ind}): mean={vals.mean()}"
            )


def test_industry_loader_round_trip(tmp_path, monkeypatch):
    """v1.4.0: IndustryLoader cache round-trip. Doesn't touch Tushare —
    writes a synthetic parquet, reads via get / get_map / stats."""
    from financial_analyst.data.loaders import industry as ind_mod
    monkeypatch.setattr(ind_mod, "_cache_dir", lambda: tmp_path)

    seed = pd.DataFrame([
        {"code": "SH600519", "industry": "白酒", "name": "贵州茅台", "refreshed_at": "2026-05-19"},
        {"code": "SZ000858", "industry": "白酒", "name": "五粮液",   "refreshed_at": "2026-05-19"},
        {"code": "SH600036", "industry": "银行", "name": "招商银行", "refreshed_at": "2026-05-19"},
    ])
    seed.to_parquet(tmp_path / "industry_map.parquet", index=False)

    loader = ind_mod.IndustryLoader()
    assert loader.get("SH600519") == "白酒"
    assert loader.get("SH999999") == loader.UNKNOWN_INDUSTRY
    m = loader.get_map(["SH600519", "SZ000858", "SH600036", "SH999999"])
    assert m == {"SH600519": "白酒", "SZ000858": "白酒",
                 "SH600036": "银行", "SH999999": loader.UNKNOWN_INDUSTRY}
    s = loader.stats()
    assert s["n_codes"] == 3
    assert s["n_industries"] == 2


def test_panel_carries_industry_when_loader_supplied():
    """from_loader(..., industry_loader=...) should inject an ``industry``
    column into the panel so alphas can call panel.industry."""
    from financial_analyst.factors.zoo import PanelData

    class StubLoader:
        def fetch_quote(self, code, start, end, freq="day"):
            np.random.seed(hash(code) & 0xFFFF)
            dates = pd.date_range(start, end, freq="B")[:10]
            n = len(dates)
            base = 50 + (hash(code) % 50)
            close = base * np.exp(np.cumsum(np.random.randn(n) * 0.02))
            df = pd.DataFrame({
                "trade_date": dates,
                "open": close, "high": close * 1.01, "low": close * 0.99,
                "close": close, "vol": np.full(n, 1e6),
            }).set_index("trade_date")
            df.index.name = "datetime"
            return df

    class StubIndLoader:
        UNKNOWN_INDUSTRY = "未知"
        def get_map(self, codes):
            mapping = {"SH600519": "白酒", "SZ000858": "白酒", "SH600036": "银行"}
            return {c: mapping.get(c, self.UNKNOWN_INDUSTRY) for c in codes}

    panel = PanelData.from_loader(
        StubLoader(),
        ["SH600519", "SZ000858", "SH600036", "SH999999"],
        "2024-01-01", "2024-01-15",
        industry_loader=StubIndLoader(),
    )
    assert "industry" in panel.df.columns
    # Each code maps to its expected industry, repeated across dates
    by_code = panel.industry.groupby(level="code").first()
    assert by_code["SH600519"] == "白酒"
    assert by_code["SH600036"] == "银行"
    assert by_code["SH999999"] == "未知"


def test_selector_picks_top_n_by_abs_rank_ir():
    """v1.4.2: select_top_alphas should filter noise then sort by |rank_IR|."""
    from financial_analyst.factors.zoo.selector import select_top_alphas
    bench = pd.DataFrame([
        {"name": "alpha_strong_bull", "family": "alpha101", "rank_ir": +0.30,
         "rank_ic": +0.05, "hit_rate": 0.55, "n_dates": 100, "status": "ok"},
        {"name": "alpha_strong_bear", "family": "alpha101", "rank_ir": -0.28,
         "rank_ic": -0.04, "hit_rate": 0.45, "n_dates": 100, "status": "ok"},
        {"name": "alpha_noise",       "family": "alpha101", "rank_ir": +0.01,
         "rank_ic": +0.001, "hit_rate": 0.50, "n_dates": 100, "status": "ok"},
        {"name": "alpha_short_window","family": "alpha101", "rank_ir": +0.50,
         "rank_ic": +0.10, "hit_rate": 0.60, "n_dates": 10, "status": "ok"},
        {"name": "alpha_sign_disagree","family": "alpha101", "rank_ir": +0.20,
         "rank_ic": +0.03, "hit_rate": 0.45, "n_dates": 100, "status": "ok"},
        {"name": "alpha_error",       "family": "alpha101", "rank_ir": None,
         "rank_ic": None, "hit_rate": None, "n_dates": None, "status": "compute_error"},
    ])
    picked = select_top_alphas(bench, n=10)
    # noise (under threshold), short_window (n_dates=10), sign_disagree (rank_ir>0
    # but hit<0.5), error (NaN) all filtered out
    assert picked == ["alpha_strong_bull", "alpha_strong_bear"]


def test_selector_filter_relaxed():
    """Relaxing thresholds should let weaker alphas through."""
    from financial_analyst.factors.zoo.selector import select_top_alphas
    bench = pd.DataFrame([
        {"name": "a1", "family": "x", "rank_ir": +0.30, "rank_ic": +0.05,
         "hit_rate": 0.55, "n_dates": 100, "status": "ok"},
        {"name": "a2", "family": "x", "rank_ir": +0.10, "rank_ic": +0.02,
         "hit_rate": 0.51, "n_dates": 100, "status": "ok"},
    ])
    picked = select_top_alphas(bench, n=10, min_abs_rank_ir=0.0,
                                min_n_dates=0, require_sign_agreement=False)
    assert set(picked) == {"a1", "a2"}


def test_alpha_metadata_from_bench():
    """Snapshot enrichment helper — pulls per-alpha bench metadata."""
    from financial_analyst.factors.zoo.selector import alpha_metadata_from_bench
    bench = pd.DataFrame([
        {"name": "a1", "rank_ic": +0.05, "hit_rate": 0.55, "n_dates": 100},
        {"name": "a2", "rank_ic": -0.03, "hit_rate": 0.48, "n_dates": 80},
    ])
    meta = alpha_metadata_from_bench(bench, ["a1", "a2", "a_missing"])
    assert meta["a1"]["bench_rank_ic"] == pytest.approx(0.05)
    assert meta["a2"]["bench_hit_rate"] == pytest.approx(0.48)
    assert meta["a_missing"]["bench_rank_ic"] is None


def test_gtja143_cumprod_reduction():
    """v1.4.6: gtja143 recursive SELF reduces to cumprod of per-bar
    multiplier. Each up-day multiplies; down/flat days keep prior value."""
    from financial_analyst.factors.zoo.registry import get

    # Hand-built panel: 5 dates, 2 codes. Stock A: up, up, down, up, flat.
    dates = pd.date_range("2024-01-01", periods=5, freq="B")
    idx = pd.MultiIndex.from_product([dates, ["A"]], names=["datetime", "code"])
    closes = [100.0, 105.0, 110.25, 100.0, 102.0, 102.0]  # 6 values for ref+5
    # Actually use 5: prev + 4 transitions
    closes = [100.0, 105.0, 110.25, 105.0, 110.0]  # up, up, down, up
    df = pd.DataFrame({
        "open":  closes, "high": [c * 1.01 for c in closes],
        "low":   [c * 0.99 for c in closes], "close": closes,
        "volume": [1e6] * 5,
    }, index=idx)
    panel = PanelData(df)

    g143 = get("gtja143").compute(panel)
    series = g143.xs("A", level="code")
    # Day 0 (no prior): up condition is NaN/False, multiplier=1.0 → cumprod=1.0
    assert series.iloc[0] == pytest.approx(1.0)
    # Day 1: 105/100 = 1.05 up-day → cumprod = 1.05
    assert series.iloc[1] == pytest.approx(1.05)
    # Day 2: 110.25/105 = 1.05 up-day → cumprod = 1.05 * 1.05 = 1.1025
    assert series.iloc[2] == pytest.approx(1.1025)
    # Day 3: 105/110.25 = down-day → cumprod stays 1.1025
    assert series.iloc[3] == pytest.approx(1.1025)
    # Day 4: 110/105 = 1.04762 up-day → cumprod = 1.1025 * 1.04762 ≈ 1.155
    assert series.iloc[4] == pytest.approx(1.1025 * (110 / 105), rel=1e-4)


def test_gtja149_downside_beta_returns_nan_without_benchmark():
    """When no benchmark column is supplied, gtja149 should silently
    return all-NaN — not crash."""
    from financial_analyst.factors.zoo.registry import get
    panel = _make_panel(n_codes=3, n_dates=300)  # no benchmark_close
    g149 = get("gtja149").compute(panel)
    assert g149.isna().all(), "gtja149 should be all NaN without benchmark"


def test_gtja149_with_benchmark_produces_betas():
    """With a synthetic benchmark column on a 300-day panel, gtja149
    should emit a meaningful downside-beta series."""
    from financial_analyst.factors.zoo.registry import get
    np.random.seed(7)
    panel = _make_panel(n_codes=3, n_dates=300)

    # Inject a synthetic benchmark close — random walk
    dates = panel.dates()
    bench_rets = np.random.randn(len(dates)) * 0.012
    bench_close = 3000.0 * np.exp(np.cumsum(bench_rets))
    bench_map = dict(zip(dates, bench_close))
    panel.df["benchmark_close"] = (
        panel.df.index.get_level_values("datetime").map(bench_map)
    )

    g149 = get("gtja149").compute(panel)
    n_valid = g149.notna().sum()
    # 252-day window + 50-day min_periods → expect ~50+ valid points
    assert n_valid > 50, f"too few valid betas: {n_valid}"
    # Should produce finite, real betas (not all 0)
    valid = g149[g149.notna()]
    assert valid.abs().mean() > 0.001, "betas suspiciously small (near zero)"


def test_filter_where_masks_to_nan():
    from financial_analyst.factors.zoo.operators import filter_where

    s = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0])
    mask = pd.Series([True, False, True, False, True])
    out = filter_where(s, mask)
    assert out.tolist()[0] == 1.0
    assert pd.isna(out.iloc[1])
    assert out.iloc[2] == 3.0
    assert pd.isna(out.iloc[3])
    assert out.iloc[4] == 5.0


def test_benchmark_loader_broadcast_to_panel_index(tmp_path):
    """BenchmarkLoader.broadcast_to_panel_index repeats the close at each
    date across every code in the panel."""
    from financial_analyst.data.loaders.benchmark import BenchmarkLoader

    class StubLoader:
        def fetch_quote(self, code, start, end, freq="day"):
            dates = pd.date_range(start, periods=5, freq="B")
            return pd.DataFrame({
                "open":  [1.0]*5, "high": [1.0]*5, "low": [1.0]*5,
                "close": [100.0, 101.0, 102.0, 101.5, 103.0],
                "volume": [1.0]*5,
            }, index=dates)

    bench = BenchmarkLoader(loader=StubLoader(), benchmark="csi300")
    assert bench.benchmark_code == "SH000300"
    close = bench.fetch_close("2024-01-01", "2024-01-05")
    assert len(close) == 5

    # Build a 2-code panel index
    dates = pd.date_range("2024-01-01", periods=5, freq="B")
    panel_idx = pd.MultiIndex.from_product([dates, ["A", "B"]],
                                            names=["datetime", "code"])
    broadcasted = bench.broadcast_to_panel_index(close, panel_idx)
    # Each (date, code) gets the same close as the date's underlying value
    assert broadcasted.loc[(dates[0], "A")] == 100.0
    assert broadcasted.loc[(dates[0], "B")] == 100.0
    assert broadcasted.loc[(dates[3], "A")] == 101.5
    assert broadcasted.loc[(dates[3], "B")] == 101.5


def test_benchmark_loader_env_override(monkeypatch):
    """FA_BENCHMARK env var overrides default 'csi300'."""
    from financial_analyst.data.loaders.benchmark import BenchmarkLoader

    class StubLoader:
        def fetch_quote(self, *a, **k):
            return pd.DataFrame({"close": [1.0]})

    monkeypatch.setenv("FA_BENCHMARK", "zz500")
    bench = BenchmarkLoader(loader=StubLoader())
    assert bench.benchmark_key == "zz500"
    assert bench.benchmark_code == "SH000905"


def test_regbeta_min_periods_relaxed_for_filtered_input():
    """regbeta(..., min_periods=k) should emit betas even when the
    rolling window has many NaNs (k << n)."""
    from financial_analyst.factors.zoo.operators import regbeta

    np.random.seed(13)
    # Use 2 codes to avoid the pandas single-group .apply quirk where
    # groupby returns a DataFrame instead of Series.
    codes = ["A", "B"]
    dates = pd.date_range("2024-01-01", periods=300, freq="B")
    idx = pd.MultiIndex.from_product([dates, codes], names=["datetime", "code"])
    n_total = 300 * 2
    base = np.random.randn(n_total)
    x = pd.Series(base, index=idx)
    y = pd.Series(0.7 * base + 0.3 * np.random.randn(n_total), index=idx)
    mask = pd.Series([i % 2 == 0 for i in range(n_total)], index=idx)
    x_masked = x.where(mask)
    y_masked = y.where(mask)

    # Default min_periods=n: pandas' rolling.cov uses skipna internally
    # so the half-NaN windows still produce betas after warm-up (~49 valid).
    # The relaxed min_periods=50 should give MORE valid betas (~300+ — the
    # 252-row window has 126 valid obs once warmed, well over 50).
    b_strict = regbeta(y_masked, x_masked, 252)
    b_relaxed = regbeta(y_masked, x_masked, 252, min_periods=50)
    n_strict = int(b_strict.notna().to_numpy().sum())
    n_relaxed = int(b_relaxed.notna().to_numpy().sum())
    assert n_relaxed >= n_strict, (
        f"relaxed should produce >= valid betas: strict={n_strict} relaxed={n_relaxed}"
    )
    assert n_relaxed > 50, f"relaxed too few: {n_relaxed}"
    # The beta should be near 0.7 (positive correlation by construction)
    valid = b_relaxed[b_relaxed.notna()]
    mean_b = float(valid.to_numpy().mean())
    assert 0.4 < mean_b < 1.0, f"mean beta off: {mean_b}"


def test_snapshot_round_trip(tmp_path, monkeypatch):
    """v1.3.4: build_snapshot → load_snapshot_for_code round-trip.
    Uses a stub loader that returns synthetic panels per code, so the test
    doesn't depend on any real data source."""
    from financial_analyst.factors.zoo import snapshot as snap_mod
    from financial_analyst.factors.zoo.snapshot import (
        build_snapshot, load_snapshot_for_code, snapshot_path, PRODUCTION_TOP10,
    )

    # Redirect cache to tmp_path
    monkeypatch.setattr(snap_mod, "_cache_dir", lambda: tmp_path)

    class StubLoader:
        def fetch_quote(self, code, start, end, freq="day"):
            np.random.seed(hash(code) & 0xFFFF)
            dates = pd.date_range(start, end, freq="B")
            n = len(dates)
            base = 50 + (hash(code) % 50)
            rets = np.random.randn(n) * 0.02
            close = base * np.exp(np.cumsum(rets))
            df = pd.DataFrame({
                "trade_date": dates,
                "open": close * (1 + np.random.randn(n) * 0.005),
                "high": close * (1 + np.abs(np.random.randn(n)) * 0.01),
                "low":  close * (1 - np.abs(np.random.randn(n)) * 0.01),
                "close": close,
                "vol":  np.abs(1 + np.random.randn(n) * 0.2) * 1e6,
            })
            df = df.set_index("trade_date")
            df.index.name = "datetime"
            return df

    codes = [f"SH{600000 + i:06d}" for i in range(40)]
    df = build_snapshot(
        StubLoader(), codes, asof="2024-12-31",
        # Use just 3 alphas to keep the test fast (full PRODUCTION_TOP10 also works)
        names=["qlib_KLEN", "qlib_STD10", "gtja042"],
    )
    assert {"code", "alpha", "value", "rank_pct", "n_obs"}.issubset(df.columns)
    # Three alphas × ~40 codes = ~120 rows. Allow some NaN drops.
    assert len(df) > 80, f"snapshot too small: {len(df)} rows"

    # Persist + look up
    df.to_parquet(snapshot_path("test_universe", "2024-12-31"), index=False)
    sub = load_snapshot_for_code("test_universe", codes[0], asof_or_earlier="2024-12-31")
    assert sub is not None
    assert set(sub["alpha"]) == {"qlib_KLEN", "qlib_STD10", "gtja042"}
    assert (sub["rank_pct"] >= 0).all() and (sub["rank_pct"] <= 1).all()
