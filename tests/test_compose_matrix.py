"""Tests for SP-D factor matrix builder (factors/compose/matrix.py).

build_factor_matrix takes a PanelData + a list of member strings (registered
alpha names and/or whitelisted expressions), computes each, per-date winsorizes
(q=0.01) then zscores, and stacks them into a (datetime, code) x factor DataFrame
whose columns are the member strings. Members whose compute fails (e.g. a garbage
expression that won't compile) are silently skipped.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

# Importing the zoo package auto-registers all alpha families (alpha101 etc.),
# so reg_get("alpha006") resolves without an explicit family import.
import financial_analyst.factors.zoo  # noqa: F401
from financial_analyst.factors.zoo.panel import PanelData
from financial_analyst.factors.compose.matrix import build_factor_matrix


def _make_panel(n_codes: int = 6, n_dates: int = 30, seed: int = 7) -> PanelData:
    """Synthetic OHLCV panel: geometric-random-walk close per code, MultiIndex
    (datetime, code). Enough dates (30) and codes (6) that delta(close,5) /
    correlation(.,.,10) have real values and per-date cross-sections are
    non-degenerate for zscore."""
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


def test_matrix_mixed_registered_and_expressions():
    """Registered alpha name + two expressions → 3 columns named by member,
    MultiIndex preserved, each column per-date ~zscored (mean ≈ 0)."""
    panel = _make_panel()
    members = ["alpha006", "rank(-delta(close,5))", "rank(close)"]
    mat, names = build_factor_matrix(panel, members)

    # One column per successful member, columns named by the member string.
    assert names == members
    assert list(mat.columns) == members
    assert mat.shape[1] == 3

    # Index is the panel's (datetime, code) MultiIndex.
    assert isinstance(mat.index, pd.MultiIndex)
    assert list(mat.index.names) == ["datetime", "code"]
    assert mat.index.equals(panel.df.index)

    # Each column is per-date zscored → per-date mean ≈ 0 wherever the date's
    # cross-section had >1 finite value (degenerate/all-NaN dates are skipped).
    for col in mat.columns:
        s = mat[col]
        for _, sub in s.groupby(level="datetime"):
            sub = sub.dropna()
            if len(sub) > 1:
                assert abs(float(sub.mean())) < 1e-9


def test_matrix_two_expressions_only():
    """Registry-independent: two pure expressions both succeed → 2 columns."""
    panel = _make_panel()
    members = ["rank(-delta(close,5))", "rank(close)"]
    mat, names = build_factor_matrix(panel, members)

    assert names == members
    assert list(mat.columns) == members
    assert mat.shape[1] == 2
    assert mat.index.equals(panel.df.index)


def test_matrix_skips_member_that_fails_to_compile():
    """A garbage expression that won't compile is skipped; the good members
    remain, columns still named by the surviving member strings."""
    panel = _make_panel()
    members = ["rank(close)", "this is not @ valid factor!!!", "rank(-delta(close,5))"]
    mat, names = build_factor_matrix(panel, members)

    # The garbage member is dropped; the two valid ones survive in order.
    assert names == ["rank(close)", "rank(-delta(close,5))"]
    assert list(mat.columns) == ["rank(close)", "rank(-delta(close,5))"]
    assert "this is not @ valid factor!!!" not in mat.columns
    assert mat.shape[1] == 2
    assert mat.index.equals(panel.df.index)


def test_matrix_skips_member_whose_compute_raises():
    """A syntactically-valid expression referencing an unknown symbol compiles
    but raises at compute time (NameError in restricted eval) → skipped."""
    panel = _make_panel()
    members = ["rank(close)", "no_such_field + 1"]
    mat, names = build_factor_matrix(panel, members)

    assert names == ["rank(close)"]
    assert list(mat.columns) == ["rank(close)"]
    assert mat.shape[1] == 1


def test_matrix_all_fail_returns_empty():
    """If zero members succeed, return an empty DataFrame and empty name list."""
    panel = _make_panel()
    members = ["@@@ garbage @@@", "import os"]  # second tripped by validate_expr
    mat, names = build_factor_matrix(panel, members)

    assert names == []
    assert isinstance(mat, pd.DataFrame)
    assert mat.empty
    assert mat.shape[1] == 0
