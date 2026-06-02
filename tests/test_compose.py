"""Tests for SP-D compose orchestration (factors/compose/compose.py).

compose_factors loads a panel (via a monkeypatched universe + loader), builds a
member factor matrix, splits rebalance dates into train/test, fits a composite
(combine) on TRAIN only, evaluates it OOS via SP-A build_report, and compares
each member's OOS metrics → a verdict. It must NEVER raise: all failure modes
surface as structured ``status``/``error``.

These tests use a stub loader (datetime-indexed OHLCV ~120 business days for a
handful of codes; fetch_daily_basic returns empty) and monkeypatch the home
modules ``financial_analyst.data.universe.resolve_universe_codes`` +
``financial_analyst.data.loader_factory.get_default_loader`` — compose_factors
imports those locally (like factor_report), so the home-module patch is what
takes effect.

NOTE: we never call _clear_registry_for_tests (it would wipe the global alpha
registry and break cross-file tests). compose_factors only reads the registry.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

# Importing the zoo package auto-registers alpha families; not strictly needed
# here since the tests use pure expressions, but harmless and mirrors siblings.
import financial_analyst.factors.zoo  # noqa: F401
from financial_analyst.factors.compose import (
    ComposeResult,
    MemberOOS,
    compose_factors,
)
from financial_analyst.factors.eval import EvalConfig, FactorReport

CODES = ["SH600519", "SZ000858", "SH600036", "SH601318", "SZ300750", "SH600276"]


# ---------------------------------------------------------------------------
# Stub loaders.
# ---------------------------------------------------------------------------
def _random_walk_loader():
    """Per-code geometric random walk close, ~120 business days. Deterministic
    per code via hash-seeded RNG. fetch_daily_basic returns empty (no funda)."""

    class StubLoader:
        def fetch_quote(self, code, start, end, freq="day"):
            dates = pd.date_range("2023-01-02", periods=120, freq="B")
            rng = np.random.default_rng(abs(hash(code)) % 9999)
            close = 50 * np.exp(np.cumsum(rng.standard_normal(len(dates)) * 0.02))
            df = pd.DataFrame(
                {
                    "open": close,
                    "high": close * 1.01,
                    "low": close * 0.99,
                    "close": close,
                    "volume": np.full(len(dates), 1e6),
                },
                index=dates,
            )
            df.index.name = "datetime"
            return df

        def fetch_daily_basic(self, code, start, end):
            return pd.DataFrame()

    return StubLoader()


def _trend_loader():
    """Per-code constant drift → close grows monotonically by a per-code rate.

    Codes are assigned increasing drifts, so cross-sectionally a momentum/level
    factor (e.g. rank(close) or rank(ts_mean(returns,5))) is monotonic in drift,
    and drift also governs forward returns → a deterministic POSITIVE cross-
    sectional relationship between those factors and forward return.
    """

    drifts = {code: 0.001 + 0.0015 * i for i, code in enumerate(CODES)}

    class TrendLoader:
        def fetch_quote(self, code, start, end, freq="day"):
            dates = pd.date_range("2023-01-02", periods=120, freq="B")
            mu = drifts[code]
            n = len(dates)
            close = 50.0 * np.exp(mu * np.arange(n))
            df = pd.DataFrame(
                {
                    "open": close,
                    "high": close * 1.005,
                    "low": close * 0.995,
                    "close": close,
                    "volume": np.full(n, 1e6),
                },
                index=dates,
            )
            df.index.name = "datetime"
            return df

        def fetch_daily_basic(self, code, start, end):
            return pd.DataFrame()

    return TrendLoader()


def _patch(monkeypatch, loader, codes=CODES):
    # compose_factors imports these from their home modules (local imports),
    # so patch THOSE, not any buddy alias.
    monkeypatch.setattr(
        "financial_analyst.data.universe.resolve_universe_codes",
        lambda u: list(codes),
    )
    monkeypatch.setattr(
        "financial_analyst.data.loader_factory.get_default_loader",
        lambda: loader,
    )


# ---------------------------------------------------------------------------
# End-to-end happy path: 2 members + method="equal".
# ---------------------------------------------------------------------------
def test_compose_factors_equal_end_to_end(monkeypatch):
    _patch(monkeypatch, _random_walk_loader())
    cfg = EvalConfig(universe="csi500", freq="week")
    res = compose_factors(
        ["rank(-delta(close,5))", "rank(close)"],
        config=cfg,
        method="equal",
        train_frac=0.6,
    )

    assert isinstance(res, ComposeResult)
    assert res.status == "ok", f"unexpected status: {res.status} / {res.error}"
    assert res.method == "equal"
    assert res.members == ["rank(-delta(close,5))", "rank(close)"]

    # composite is an OK FactorReport.
    assert isinstance(res.composite, FactorReport)
    assert res.composite.status == "ok"

    # member_oos has one entry per member.
    assert len(res.member_oos) == 2
    assert all(isinstance(m, MemberOOS) for m in res.member_oos)
    assert {m.name for m in res.member_oos} == {"rank(-delta(close,5))", "rank(close)"}

    # train/test split non-trivial.
    assert res.n_train_dates > 0
    assert res.n_test_dates > 0

    # verdict rendered.
    assert isinstance(res.verdict, str)
    assert res.verdict != ""
    assert "OOS Sharpe" in res.verdict

    # weights keyed by members, equal-weight.
    assert set(res.weights.keys()) == {"rank(-delta(close,5))", "rank(close)"}
    for w in res.weights.values():
        assert abs(w - 0.5) < 1e-9


# ---------------------------------------------------------------------------
# lgbm method also runs end-to-end on the stub panel.
# ---------------------------------------------------------------------------
def test_compose_factors_lgbm_runs(monkeypatch):
    _patch(monkeypatch, _random_walk_loader())
    cfg = EvalConfig(universe="csi500", freq="week")
    res = compose_factors(
        ["rank(-delta(close,5))", "rank(close)", "rank(ts_mean(returns,5))"],
        config=cfg,
        method="lgbm",
        train_frac=0.6,
    )
    assert res.status == "ok", f"unexpected status: {res.status} / {res.error}"
    assert isinstance(res.composite, FactorReport)
    assert res.composite.status == "ok"
    assert len(res.member_oos) == 3
    # lgbm feature importances normalized → non-negative, sum ~1 (or all 0).
    vals = np.array(list(res.weights.values()), dtype=float)
    assert (vals >= -1e-9).all()
    s = vals.sum()
    assert abs(s - 1.0) < 1e-6 or abs(s) < 1e-9


# ---------------------------------------------------------------------------
# Fewer than 2 members → structured too_few_factors, no exception, no I/O.
# ---------------------------------------------------------------------------
def test_compose_factors_too_few_factors():
    # No monkeypatch needed: the guard returns before any universe/loader call.
    res = compose_factors(["rank(close)"], config=EvalConfig(), method="equal")
    assert isinstance(res, ComposeResult)
    assert res.status == "too_few_factors"
    assert res.error == "合成至少需要 2 个因子"
    assert res.composite is None
    assert res.member_oos == []


def test_compose_factors_empty_input():
    res = compose_factors([], config=EvalConfig(), method="lgbm")
    assert res.status == "too_few_factors"
    assert res.composite is None


# ---------------------------------------------------------------------------
# Empty universe → structured empty_universe (no exception).
# ---------------------------------------------------------------------------
def test_compose_factors_empty_universe(monkeypatch):
    monkeypatch.setattr(
        "financial_analyst.data.universe.resolve_universe_codes", lambda u: []
    )
    res = compose_factors(
        ["rank(close)", "rank(-delta(close,5))"],
        config=EvalConfig(universe="nonexistent_xyz"),
        method="equal",
    )
    assert res.status == "empty_universe"
    assert res.composite is None
    assert "nonexistent_xyz" in res.error


# ---------------------------------------------------------------------------
# Load failure (loader.from_loader raises because no code loads) → load_error.
# ---------------------------------------------------------------------------
def test_compose_factors_load_error(monkeypatch):
    class BadLoader:
        def fetch_quote(self, code, start, end, freq="day"):
            raise RuntimeError("boom")

        def fetch_daily_basic(self, code, start, end):
            return pd.DataFrame()

    _patch(monkeypatch, BadLoader())
    res = compose_factors(
        ["rank(close)", "rank(-delta(close,5))"],
        config=EvalConfig(universe="csi500", freq="week"),
        method="equal",
    )
    assert res.status == "load_error"
    assert res.composite is None
    assert res.error  # non-empty


# ---------------------------------------------------------------------------
# Effective members < 2 after skipping uncompilable members → too_few_factors.
# ---------------------------------------------------------------------------
def test_compose_factors_too_few_effective(monkeypatch):
    _patch(monkeypatch, _random_walk_loader())
    res = compose_factors(
        ["rank(close)", "this is not @ valid factor!!!"],
        config=EvalConfig(universe="csi500", freq="week"),
        method="equal",
    )
    # Only one member compiles → effective < 2.
    assert res.status == "too_few_factors"
    assert res.composite is None
    # The skipped member should be noted in warnings.
    assert any("跳过" in w for w in res.warnings)


# ---------------------------------------------------------------------------
# Deterministic "predictive members" → composite OOS rank_ic positive.
#
# Trend loader: per-code constant drift, so cross-sectionally both rank(close)
# and rank(ts_mean(returns,5)) increase with drift, and drift governs forward
# returns → a deterministic POSITIVE cross-sectional relationship. The equal
# composite of two positively-predictive members must have rank_ic_mean > 0.
# ---------------------------------------------------------------------------
def test_compose_factors_predictive_members_positive_ic(monkeypatch):
    _patch(monkeypatch, _trend_loader())
    cfg = EvalConfig(universe="csi500", freq="week")
    res = compose_factors(
        ["rank(close)", "rank(ts_mean(returns,5))"],
        config=cfg,
        method="equal",
        train_frac=0.6,
    )
    assert res.status == "ok", f"unexpected status: {res.status} / {res.error}"
    assert isinstance(res.composite, FactorReport)
    assert res.composite.status == "ok"
    assert res.composite.ic is not None
    ric = res.composite.ic.rank_ic_mean
    assert ric == ric, "rank_ic_mean is NaN"
    assert ric > 0.3, f"expected strong positive OOS rank_ic, got {ric}"


# ---------------------------------------------------------------------------
# SP-3: method='lgbm' 必含 composite_shap_top5; 其它方法应为 None.
# ---------------------------------------------------------------------------
def test_lgbm_composite_has_shap_top5(monkeypatch):
    """method='lgbm' 跑完后, ComposeResult.composite_shap_top5:
    - 不是 None
    - 是 dict
    - 每个 code 列表长度 <= 5
    - 每项 (factor_name_str, signed_contrib_float).
    """
    _patch(monkeypatch, _random_walk_loader())
    cfg = EvalConfig(universe="csi500", freq="week")
    res = compose_factors(
        ["rank(-delta(close,5))", "rank(close)", "rank(ts_mean(returns,5))"],
        config=cfg,
        method="lgbm",
        train_frac=0.6,
    )
    assert res.status == "ok", f"unexpected status: {res.status} / {res.error}"
    assert res.composite_shap_top5 is not None, "lgbm 应填充 composite_shap_top5"
    assert isinstance(res.composite_shap_top5, dict)
    assert len(res.composite_shap_top5) > 0
    for code, contribs in res.composite_shap_top5.items():
        assert isinstance(code, str)
        assert isinstance(contribs, list)
        assert len(contribs) <= 5
        for item in contribs:
            assert isinstance(item, tuple)
            assert len(item) == 2
            assert isinstance(item[0], str)
            assert isinstance(item[1], float)


def test_non_lgbm_compose_shap_top5_is_none(monkeypatch):
    """method != 'lgbm' (equal/ic_weighted/linear) → composite_shap_top5 应为 None."""
    _patch(monkeypatch, _random_walk_loader())
    cfg = EvalConfig(universe="csi500", freq="week")
    res = compose_factors(
        ["rank(-delta(close,5))", "rank(close)"],
        config=cfg,
        method="equal",
        train_frac=0.6,
    )
    assert res.status == "ok"
    assert res.composite_shap_top5 is None
