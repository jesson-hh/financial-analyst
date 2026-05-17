import numpy as np
import pandas as pd
from financial_analyst.factors.whale import compute_whale_signals


def _synth(n=60, seed=0):
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2025-03-01", periods=n, freq="B")
    close = 100 * np.exp(np.cumsum(rng.normal(0, 0.02, n)))
    return pd.DataFrame({
        "trade_date": dates,
        "open": close * 0.99,
        "high": close * 1.02,
        "low": close * 0.97,
        "close": close,
        "vol": rng.integers(1_000_000, 5_000_000, n),
        "amount": close * 1e6,
    })


def test_whale_signals_keys():
    df = _synth()
    sig = compute_whale_signals(df)
    for k in ["obv_trend", "vr_judge", "mfi_judge", "shadow_judge", "chip_judge", "whale_judge"]:
        assert k in sig
