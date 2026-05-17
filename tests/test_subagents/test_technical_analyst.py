import pytest
from unittest.mock import AsyncMock, patch
from financial_analyst.agent.tier2.technical_analyst import TechnicalAnalyst


@pytest.mark.asyncio
async def test_technical_analyst_runs(tmp_path):
    fake = {
        "choices": [{"message": {"content": '{"technical_score": 1, "ma_state": "bullish", "rsi_state": "neutral", "macd_state": "bullish_cross", "factor_consensus": "weak_long", "breakout_signal": null, "bull_points": ["MA20 up"], "bear_points": []}'}}]
    }
    agent = TechnicalAnalyst(memory_root=tmp_path)
    with patch("financial_analyst.agent.tier2.technical_analyst.LLMClient.for_agent") as mock_for:
        client = AsyncMock()
        client.chat = AsyncMock(return_value=fake)
        mock_for.return_value = client
        result = await agent.run({"quote-fetcher": {"price": 100}, "factor-computer": {"factor_scores": {"rev_20": -0.05}}})
    assert result.ok is True
    assert result.output.ma_state == "bullish"


@pytest.mark.asyncio
async def test_technical_analyst_rejects_bad_output(tmp_path):
    bad = {"choices": [{"message": {"content": '{"technical_score": "x", "ma_state": "neutral", "rsi_state": "neutral", "macd_state": "neutral", "factor_consensus": "neutral", "bull_points": [], "bear_points": []}'}}]}
    agent = TechnicalAnalyst(memory_root=tmp_path)
    with patch("financial_analyst.agent.tier2.technical_analyst.LLMClient.for_agent") as mock_for:
        client = AsyncMock()
        client.chat = AsyncMock(return_value=bad)
        mock_for.return_value = client
        result = await agent.run({})
    assert result.ok is False


@pytest.mark.asyncio
async def test_technical_analyst_bearish_scenario(tmp_path):
    """Bearish technical setup should yield negative score and bearish states."""
    fake = {
        "choices": [{"message": {"content": '{"technical_score": -2, "ma_state": "bearish", "rsi_state": "overbought", "macd_state": "bearish_cross", "factor_consensus": "strong_short", "breakout_signal": null, "bull_points": [], "bear_points": ["price below MA20", "MACD bearish cross", "RSI overbought at 78"]}'}}]
    }
    agent = TechnicalAnalyst(memory_root=tmp_path)
    with patch("financial_analyst.agent.tier2.technical_analyst.LLMClient.for_agent") as mock_for:
        client = AsyncMock()
        client.chat = AsyncMock(return_value=fake)
        mock_for.return_value = client
        result = await agent.run({"quote-fetcher": {"price": 50, "ma20": 55}, "factor-computer": {"factor_scores": {"rev_20": 0.08}}})
    assert result.ok is True
    assert result.output.ma_state == "bearish"
    assert result.output.rsi_state == "overbought"
    assert result.output.macd_state == "bearish_cross"
    assert result.output.factor_consensus == "strong_short"
    assert result.output.technical_score == -2


@pytest.mark.asyncio
async def test_technical_analyst_oversold_bounce(tmp_path):
    """Oversold conditions with negative rev_20 should signal potential reversal UP."""
    fake = {
        "choices": [{"message": {"content": '{"technical_score": 2, "ma_state": "neutral", "rsi_state": "oversold", "macd_state": "neutral", "factor_consensus": "strong_long", "breakout_signal": "volume_breakout", "bull_points": ["RSI oversold at 22", "rev_20 negative signals mean-reversion up", "volume spike"], "bear_points": ["price below MA60"]}'}}]
    }
    agent = TechnicalAnalyst(memory_root=tmp_path)
    with patch("financial_analyst.agent.tier2.technical_analyst.LLMClient.for_agent") as mock_for:
        client = AsyncMock()
        client.chat = AsyncMock(return_value=fake)
        mock_for.return_value = client
        result = await agent.run({"quote-fetcher": {"price": 30, "rsi_14": 22}, "factor-computer": {"factor_scores": {"rev_20": -0.10}}})
    assert result.ok is True
    assert result.output.rsi_state == "oversold"
    assert result.output.breakout_signal == "volume_breakout"
    assert result.output.technical_score == 2
    assert len(result.output.bull_points) >= 1


@pytest.mark.asyncio
async def test_technical_analyst_passes_both_upstream_sources(tmp_path):
    """Verify both quote-fetcher and factor-computer data appear in LLM prompt."""
    fake = {
        "choices": [{"message": {"content": '{"technical_score": 0, "ma_state": "neutral", "rsi_state": "neutral", "macd_state": "neutral", "factor_consensus": "neutral", "breakout_signal": null, "bull_points": ["stable"], "bear_points": ["no trend"]}'}}]
    }
    agent = TechnicalAnalyst(memory_root=tmp_path)
    with patch("financial_analyst.agent.tier2.technical_analyst.LLMClient.for_agent") as mock_for:
        client = AsyncMock()
        client.chat = AsyncMock(return_value=fake)
        mock_for.return_value = client
        await agent.run({
            "quote-fetcher": {"code": "SH600519", "price": 1700},
            "factor-computer": {"factor_scores": {"rev_20": 0.01, "mom_20": -0.02}},
        })
    call_args = client.chat.call_args
    messages = call_args.kwargs.get("messages") or call_args.args[0]
    user_msg = next(m["content"] for m in messages if m["role"] == "user")
    assert "SH600519" in user_msg
    assert "rev_20" in user_msg


@pytest.mark.asyncio
async def test_technical_analyst_optional_breakout_signal(tmp_path):
    """breakout_signal is optional and can be absent from JSON response."""
    fake = {
        "choices": [{"message": {"content": '{"technical_score": 0, "ma_state": "neutral", "rsi_state": "neutral", "macd_state": "neutral", "factor_consensus": "neutral", "bull_points": ["flat"], "bear_points": ["no momentum"]}'}}]
    }
    agent = TechnicalAnalyst(memory_root=tmp_path)
    with patch("financial_analyst.agent.tier2.technical_analyst.LLMClient.for_agent") as mock_for:
        client = AsyncMock()
        client.chat = AsyncMock(return_value=fake)
        mock_for.return_value = client
        result = await agent.run({})
    assert result.ok is True
    assert result.output.breakout_signal is None
