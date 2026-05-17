import numpy as np
import pandas as pd
import pytest
from unittest.mock import MagicMock, patch
from financial_analyst.models.lgb_momentum import LGBMomentumModel


def _make_synth_quote(n=300, seed=0):
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2024-01-01", periods=n, freq="B")
    close = 100 * np.exp(np.cumsum(rng.normal(0, 0.02, n)))
    return pd.DataFrame({
        "trade_date": dates,
        "open": close * (1 + rng.normal(0, 0.005, n)),
        "high": close * (1 + abs(rng.normal(0, 0.01, n))),
        "low": close * (1 - abs(rng.normal(0, 0.01, n))),
        "close": close,
        "vol": rng.integers(1_000_000, 5_000_000, n),
        "amount": close * rng.integers(1_000_000, 5_000_000, n),
    })


def test_lgb_metadata_has_required_keys():
    model = LGBMomentumModel()
    meta = model.metadata()
    assert "name" in meta
    assert "version" in meta
    assert meta["name"] == "lgb_momentum"


def test_lgb_predict_returns_score_and_rank_keys():
    model = LGBMomentumModel()
    synth = _make_synth_quote()
    with patch.object(model, "_fetch_quote", return_value=synth):
        result = model.predict("SH600519", "2026-05-17")
        assert "score" in result
        assert "rank_pct" in result
        assert 0.0 <= result["rank_pct"] <= 1.0
        assert isinstance(result["score"], float)
