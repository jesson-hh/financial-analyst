import pytest
from unittest.mock import AsyncMock, patch
from financial_analyst.agent.tier2.quant_analyst import QuantAnalyst


@pytest.mark.asyncio
async def test_quant_analyst_strong_long(tmp_path):
    fake = {"choices": [{"message": {"content": '{"quant_score": 2, "model_consensus": "strong_long", "conviction_level": "high", "anti_signals": [], "bull_points": ["LGB rank_pct=0.85"], "bear_points": []}'}}]}
    agent = QuantAnalyst(memory_root=tmp_path)
    with patch("financial_analyst.agent.tier2.quant_analyst.LLMClient.for_agent") as mock_for:
        client = AsyncMock(); client.chat = AsyncMock(return_value=fake)
        mock_for.return_value = client
        result = await agent.run({"model-predictor": {"consensus_rank_pct": 0.85}, "factor-computer": {"factor_scores": {"rev_20": -0.1}}})
    assert result.ok is True
    assert result.output.quant_score == 2
    assert result.output.conviction_level == "high"


@pytest.mark.asyncio
async def test_quant_analyst_game_capital_neutralized(tmp_path):
    fake = {"choices": [{"message": {"content": '{"quant_score": 0, "model_consensus": "neutral", "conviction_level": "low", "anti_signals": ["game_capital_speculation"], "bull_points": [], "bear_points": ["model unreliable for game-capital ticker"]}'}}]}
    agent = QuantAnalyst(memory_root=tmp_path)
    with patch("financial_analyst.agent.tier2.quant_analyst.LLMClient.for_agent") as mock_for:
        client = AsyncMock(); client.chat = AsyncMock(return_value=fake)
        mock_for.return_value = client
        result = await agent.run({"model-predictor": {"consensus_rank_pct": 0.9}})
    assert result.ok is True
    assert "game_capital_speculation" in result.output.anti_signals


@pytest.mark.asyncio
async def test_quant_analyst_rejects_bad(tmp_path):
    bad = {"choices": [{"message": {"content": '{"quant_score": "x", "model_consensus": "y", "conviction_level": "z", "anti_signals": [], "bull_points": [], "bear_points": []}'}}]}
    agent = QuantAnalyst(memory_root=tmp_path)
    with patch("financial_analyst.agent.tier2.quant_analyst.LLMClient.for_agent") as mock_for:
        client = AsyncMock(); client.chat = AsyncMock(return_value=bad)
        mock_for.return_value = client
        result = await agent.run({})
    assert result.ok is False
