import json
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, patch
from financial_analyst.dream.introspector import Introspector


@pytest.mark.asyncio
async def test_introspector_empty_outcomes(tmp_path):
    agent = Introspector(memory_root=tmp_path)
    fake = {"choices": [{"message": {"content": '{"proposals": [], "summary": "insufficient outcomes"}'}}]}
    with patch("financial_analyst.dream.introspector.LLMClient.for_agent") as mock_for:
        client = AsyncMock(); client.chat = AsyncMock(return_value=fake)
        mock_for.return_value = client
        result = await agent.run({"outcomes": []})
    assert result.ok is True
    assert len(result.output.proposals) == 0


@pytest.mark.asyncio
async def test_introspector_with_proposals(tmp_path):
    agent = Introspector(memory_root=tmp_path)
    fake_proposals = {
        "proposals": [{
            "target_agent": "bull-advocate",
            "topic_slug": "vol-neutral-bull-bias",
            "title": "Vol-neutral RSI mean-reversion underperforms",
            "lesson_md": "# Title\n\n## Why\n3 cases...\n\n## Rule\nDowngrade...",
            "confidence": "med",
            "supporting_cases": ["SH600519 2026-05-01 buy -3.2%", "SZ000858 2026-05-03 buy -2.1%", "SH601318 2026-05-05 buy -4.5%"],
            "reasoning": "All 3 shared vol_regime=neutral + bull rating; all lost T+5d.",
        }],
        "summary": "1 pattern found in bull-advocate",
    }
    fake = {"choices": [{"message": {"content": json.dumps(fake_proposals)}}]}
    with patch("financial_analyst.dream.introspector.LLMClient.for_agent") as mock_for:
        client = AsyncMock(); client.chat = AsyncMock(return_value=fake)
        mock_for.return_value = client
        result = await agent.run({"outcomes": [{"code": "SH600519", "verdict": "wrong"}]})
    assert result.ok is True
    assert len(result.output.proposals) == 1
    p = result.output.proposals[0]
    assert p.target_agent == "bull-advocate"
    assert p.confidence == "med"
    assert len(p.supporting_cases) == 3


@pytest.mark.asyncio
async def test_introspector_rejects_invalid_confidence(tmp_path):
    agent = Introspector(memory_root=tmp_path)
    bad = {"proposals": [{
        "target_agent": "x", "topic_slug": "y", "title": "z",
        "lesson_md": "w", "confidence": "ULTRA_HIGH",
        "supporting_cases": [], "reasoning": "",
    }], "summary": ""}
    fake = {"choices": [{"message": {"content": json.dumps(bad)}}]}
    with patch("financial_analyst.dream.introspector.LLMClient.for_agent") as mock_for:
        client = AsyncMock(); client.chat = AsyncMock(return_value=fake)
        mock_for.return_value = client
        result = await agent.run({"outcomes": []})
    assert result.ok is False


@pytest.mark.asyncio
async def test_introspector_loads_memory(tmp_path):
    (tmp_path / "introspector").mkdir()
    (tmp_path / "introspector" / "rules.md").write_text("META RULE X", encoding="utf-8")
    agent = Introspector(memory_root=tmp_path)
    assert "META RULE X" in agent.memory.load_all()
