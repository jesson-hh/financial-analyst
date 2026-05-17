import pandas as pd
import numpy as np
from financial_analyst.factors.sentiment import score_board, compute_vol_regime


def test_score_board_no_limit_up():
    df = pd.DataFrame({"close": [10.0, 10.1], "vol": [1e6, 1e6]})
    result = score_board(df, turnover_rate=5.0, market_cap_yi=200)
    assert result["v4_score"] is None


def test_score_board_limit_up_returns_v4():
    close = list(np.linspace(10, 10.9, 60)) + [12.0]
    df = pd.DataFrame({"close": close, "vol": [1e6] * 61})
    result = score_board(df, turnover_rate=20.0, market_cap_yi=80)
    assert result["v4_score"] is not None
    assert -4 <= result["v4_score"] <= 5


def test_volume_regime_neutral_short_history():
    close = pd.Series([10.0] * 5)
    tr = pd.Series([5.0] * 5)
    result = compute_vol_regime(close, tr)
    assert result["regime_label"] == "neutral"


def test_volume_regime_distr():
    close = pd.Series(np.linspace(100, 115, 60))
    tr = pd.Series([5.0] * 59 + [15.0])
    result = compute_vol_regime(close, tr)
    assert result["regime_label"] in ("distr", "super_distr", "neutral")
