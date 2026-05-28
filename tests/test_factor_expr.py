from __future__ import annotations
import numpy as np
import pandas as pd
import pytest

from financial_analyst.factors.zoo.expr import FACTOR_VOCAB, validate_expr, compile_factor
from financial_analyst.factors.zoo import PanelData


def _panel():
    dates = pd.date_range("2024-01-01", periods=12, freq="B")
    codes = ["A", "B", "C", "D"]
    idx = pd.MultiIndex.from_product([dates, codes], names=["datetime", "code"])
    np.random.seed(1)
    close = pd.Series(50 + np.random.randn(len(idx)).cumsum() * 0.1 + 5, index=idx).abs() + 1
    df = pd.DataFrame({
        "open": close, "high": close * 1.01, "low": close * 0.99,
        "close": close, "volume": pd.Series(1e6, index=idx),
    })
    return PanelData(df)


def test_vocab_lists_fields_and_ops():
    assert "close" in FACTOR_VOCAB and "rank" in FACTOR_VOCAB and "delta" in FACTOR_VOCAB


def test_validate_rejects_forbidden_tokens():
    for bad in ["__import__('os')", "import os", "lambda x: x", ""]:
        with pytest.raises(ValueError):
            validate_expr(bad)


def test_validate_accepts_normal_expr():
    validate_expr("rank(-delta(close,5))")  # no raise


def test_compile_factor_runs_on_panel():
    fn = compile_factor("rank(-delta(close,5))")
    out = fn(_panel())
    assert isinstance(out, pd.Series)
    assert out.index.names == ["datetime", "code"]


def test_compile_factor_matches_legacy_namespace():
    """Regression: same expr → identical Series as the old buddy _factor_compute."""
    from financial_analyst.buddy import tools as _t
    p = _panel()
    a = compile_factor("rank(close) * 2 - delta(volume, 3)")(p)
    b = _t._factor_compute("rank(close) * 2 - delta(volume, 3)")(p)
    pd.testing.assert_series_equal(a, b)
