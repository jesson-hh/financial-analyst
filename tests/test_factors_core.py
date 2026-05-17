import numpy as np
import pandas as pd
from financial_analyst.factors.core import compute_factors, FACTOR_NAMES


def _synth(n=120, seed=0):
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2025-01-01", periods=n, freq="B")
    close = 100 * np.exp(np.cumsum(rng.normal(0, 0.02, n)))
    return pd.DataFrame({
        "trade_date": dates,
        "open": close * (1 + rng.normal(0, 0.005, n)),
        "high": close * 1.02,
        "low": close * 0.98,
        "close": close,
        "vol": rng.integers(1_000_000, 5_000_000, n),
        "amount": close * 1e6,
    })


def test_factor_count():
    assert len(FACTOR_NAMES) == 34


def test_compute_factors_returns_dict():
    df = _synth()
    factors = compute_factors(df)
    assert isinstance(factors, dict)
    for name in FACTOR_NAMES:
        assert name in factors


def test_rev_20_is_finite():
    df = _synth()
    factors = compute_factors(df)
    assert np.isfinite(factors["rev_20"])
