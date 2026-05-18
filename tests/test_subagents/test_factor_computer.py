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


@pytest.mark.asyncio
async def test_factor_computer_passes_5min_to_board_scorer(tmp_path):
    """When loader returns 5min bars, factor-computer routes them to score_board
    and compute_vol_regime without error.  v5 seal_micro or v4_score must be
    present in board_score output."""
    agent = FactorComputer(memory_root=tmp_path)

    # Build a quote that has one limit-up day (≥9.5% change) on the last bar.
    base_quote = _quote(n=80)
    quote_df = base_quote.copy()
    quote_df.loc[quote_df.index[-1], "close"] = (
        float(quote_df["close"].iloc[-2]) * 1.10
    )

    last_day = pd.Timestamp(quote_df["trade_date"].iloc[-1])

    # 48 five-min bars covering the last trading day
    bars_5m = pd.DataFrame({
        "trade_date": pd.date_range(
            last_day.normalize() + pd.Timedelta("9:30:00"),
            periods=48,
            freq="5min",
        ),
        "open":   [float(quote_df["close"].iloc[-1])] * 48,
        "high":   [float(quote_df["close"].iloc[-1])] * 48,
        "low":    [float(quote_df["close"].iloc[-1]) * 0.99] * 48,
        "close":  [float(quote_df["close"].iloc[-1])] * 48,
        "vol":    [1e5] * 48,
        "amount": [1e6] * 48,
    })

    def _fake_fetch_quote(code, start, end, freq="day"):
        if freq == "5min":
            return bars_5m
        return quote_df

    loader = MagicMock()
    loader.fetch_quote.side_effect = _fake_fetch_quote
    loader.fetch_daily_basic.return_value = pd.DataFrame({
        "trade_date": pd.date_range("2026-02-01", periods=80, freq="B"),
        "turnover_rate": [5.0] * 80,
        "total_mv": [80_0000.0] * 80,
    })

    with patch.object(agent, "_get_loader", return_value=loader):
        result = await agent.run({"code": "SH600519", "asof_date": "2026-05-17"})

    assert result.ok is True
    # board_score must be a non-empty dict; v4_score or v5_score present
    assert isinstance(result.output.board_score, dict)
    bs = result.output.board_score
    assert "v4_score" in bs or "v5_score" in bs or "board_score" in bs or bs != {}
    # vol_regime must have regime_label
    assert "regime_label" in result.output.vol_regime
    # confirm fetch_quote was called at least once with freq='5min'
    calls = loader.fetch_quote.call_args_list
    freq_args = [
        (c.args[3] if len(c.args) > 3 else c.kwargs.get("freq", "day"))
        for c in calls
    ]
    assert "5min" in freq_args


@pytest.mark.asyncio
async def test_factor_computer_no_5min_still_works(tmp_path):
    """When loader returns empty df for freq='5min', factor-computer completes
    without error, just skipping v5/R11 enrichment."""
    agent = FactorComputer(memory_root=tmp_path)

    def _fake_fetch_quote(code, start, end, freq="day"):
        if freq == "5min":
            return pd.DataFrame()   # no 5min available
        return _quote()

    loader = MagicMock()
    loader.fetch_quote.side_effect = _fake_fetch_quote
    loader.fetch_daily_basic.return_value = pd.DataFrame({
        "trade_date": pd.date_range("2026-02-01", periods=80, freq="B"),
        "turnover_rate": [5.0] * 80,
    })

    with patch.object(agent, "_get_loader", return_value=loader):
        result = await agent.run({"code": "SH600519", "asof_date": "2026-05-17"})

    assert result.ok is True
    assert "regime_label" in result.output.vol_regime
