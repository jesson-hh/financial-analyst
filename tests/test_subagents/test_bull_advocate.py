import pytest
from unittest.mock import AsyncMock, patch
from financial_analyst.agent.tier3.bull_advocate import BullAdvocate


@pytest.mark.asyncio
async def test_bull_advocate_runs(tmp_path):
    fake = {"choices": [{"message": {"content": '{"thesis_bullets": ["a","b","c"], "catalysts": ["Q1 earnings"], "target_price_high": 2000, "target_price_base": 1850, "disproof_signals": ["mgmt change"], "v_anchors": ["V1","V4-立讯模式"]}'}}]}
    agent = BullAdvocate(memory_root=tmp_path)
    with patch("financial_analyst.agent.tier3.bull_advocate.LLMClient.for_agent") as mock_for:
        client = AsyncMock(); client.chat = AsyncMock(return_value=fake)
        mock_for.return_value = client
        result = await agent.run({
            "fundamental-analyst": {"valuation_score": 1},
            "technical-analyst": {"technical_score": 1},
            "whale-analyst": {"whale_score": 0},
            "quant-analyst": {"quant_score": 1},
        })
    assert result.ok is True
    assert len(result.output.thesis_bullets) == 3
    assert "V1" in result.output.v_anchors


@pytest.mark.asyncio
async def test_bull_advocate_rejects_bad(tmp_path):
    bad = {"choices": [{"message": {"content": '{"thesis_bullets": "not-a-list", "catalysts": [], "target_price_high": 100, "target_price_base": 90, "disproof_signals": [], "v_anchors": []}'}}]}
    agent = BullAdvocate(memory_root=tmp_path)
    with patch("financial_analyst.agent.tier3.bull_advocate.LLMClient.for_agent") as mock_for:
        client = AsyncMock(); client.chat = AsyncMock(return_value=bad)
        mock_for.return_value = client
        result = await agent.run({})
    assert result.ok is False
