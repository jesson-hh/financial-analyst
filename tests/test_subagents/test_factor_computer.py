import pandas as pd
import numpy as np
import pytest
from unittest.mock import MagicMock, patch
from financial_analyst.agent.tier1.factor_computer import FactorComputer


def _quote(n=80):
    rng = np.random.default_rng(0)
    close = 100 * np.exp(np.cumsum(rng.normal(0, 0.02, n)))
    return pd.DataFrame({
        "trade_date": pd.date_range("2026-02-01", periods=n, freq="B"),
        "open": close * 0.99, "high": close * 1.02, "low": close * 0.97,
        "close": close, "vol": rng.integers(1e6, 5e6, n), "amount": close * 1e6,
    })


@pytest.mark.asyncio
async def test_factor_computer_runs(tmp_path):
    agent = FactorComputer(memory_root=tmp_path)
    with patch.object(agent, "_get_loader") as m:
        loader = MagicMock()
        loader.fetch_quote.return_value = _quote()
        loader.fetch_daily_basic.return_value = pd.DataFrame({
            "trade_date": pd.date_range("2026-02-01", periods=80, freq="B"),
            "turnover_rate": [5.0] * 80,
        })
        m.return_value = loader
        result = await agent.run({"code": "SH600519", "asof_date": "2026-05-17"})
    assert result.ok is True
    assert "rev_20" in result.output.factor_scores
    assert "whale_judge" in result.output.whale_signals
    assert "regime_label" in result.output.vol_regime
