import pytest
from unittest.mock import AsyncMock, patch
from financial_analyst.agent.tier3.bear_advocate import BearAdvocate


@pytest.mark.asyncio
async def test_bear_advocate_runs(tmp_path):
    fake = {"choices": [{"message": {"content": '{"thesis_bullets": ["a","b"], "valuation_concerns": ["PE>40"], "technical_breakdown": ["broke MA200"], "target_price_low": 1400, "downside_pct": -0.18, "f_anchors": ["F4","F8"]}'}}]}
    agent = BearAdvocate(memory_root=tmp_path)
    with patch("financial_analyst.agent.tier3.bear_advocate.LLMClient.for_agent") as mock_for:
        client = AsyncMock(); client.chat = AsyncMock(return_value=fake)
        mock_for.return_value = client
        result = await agent.run({"fundamental-analyst": {"valuation_score": -1}, "whale-analyst": {"sentiment_label": "super_distr"}})
    assert result.ok is True
    assert result.output.downside_pct == -0.18
    assert "F4" in result.output.f_anchors


@pytest.mark.asyncio
async def test_bear_advocate_rejects_bad(tmp_path):
    bad = {"choices": [{"message": {"content": '{"thesis_bullets": [], "valuation_concerns": [], "technical_breakdown": [], "target_price_low": "x", "downside_pct": 0, "f_anchors": []}'}}]}
    agent = BearAdvocate(memory_root=tmp_path)
    with patch("financial_analyst.agent.tier3.bear_advocate.LLMClient.for_agent") as mock_for:
        client = AsyncMock(); client.chat = AsyncMock(return_value=bad)
        mock_for.return_value = client
        result = await agent.run({})
    assert result.ok is False
