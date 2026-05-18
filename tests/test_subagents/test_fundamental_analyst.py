import pytest
from unittest.mock import AsyncMock, patch
from financial_analyst.agent.tier2.fundamental_analyst import FundamentalAnalyst


@pytest.mark.asyncio
async def test_fundamental_analyst_runs(tmp_path):
    fake_response = {
        "choices": [{"message": {"content": '{"valuation_score": 1, "mv_tier": "large", "dimension_detail": {"pe": "in line"}, "red_flags": [], "bull_points": ["strong brand"], "bear_points": ["high valuation"]}'}}]
    }
    agent = FundamentalAnalyst(memory_root=tmp_path)
    with patch("financial_analyst.agent.tier2.fundamental_analyst.LLMClient.for_agent") as mock_for:
        mock_client = AsyncMock()
        mock_client.chat = AsyncMock(return_value=fake_response)
        mock_for.return_value = mock_client
        result = await agent.run({"quote-fetcher": {"code": "SH600519", "price": 1700, "pe": 30, "mv_yi": 2100}})
    assert result.ok is True
    assert result.output.mv_tier == "large"
    assert result.output.valuation_score == 1


@pytest.mark.asyncio
async def test_fundamental_analyst_rejects_invalid_output(tmp_path):
    bad_response = {
        "choices": [{"message": {"content": '{"valuation_score": "not-int", "mv_tier": "large", "dimension_detail": {}, "red_flags": [], "bull_points": [], "bear_points": []}'}}]
    }
    agent = FundamentalAnalyst(memory_root=tmp_path)
    with patch("financial_analyst.agent.tier2.fundamental_analyst.LLMClient.for_agent") as mock_for:
        mock_client = AsyncMock()
        mock_client.chat = AsyncMock(return_value=bad_response)
        mock_for.return_value = mock_client
        result = await agent.run({"quote-fetcher": {}})
    assert result.ok is False  # pydantic should reject "not-int" for int field


@pytest.mark.asyncio
async def test_fundamental_analyst_small_cap_game_capital_flag(tmp_path):
    """Small-cap ticker with pe>100, ret60>50%, mv<200 should be flagged."""
    fake_response = {
        "choices": [{"message": {"content": '{"valuation_score": 0, "mv_tier": "small", "dimension_detail": {"pe": "extreme"}, "red_flags": ["game-capital ticker: pe>100 AND ret60>50% AND mv<200"], "bull_points": ["momentum"], "bear_points": ["game capital risk"]}'}}]
    }
    agent = FundamentalAnalyst(memory_root=tmp_path)
    with patch("financial_analyst.agent.tier2.fundamental_analyst.LLMClient.for_agent") as mock_for:
        mock_client = AsyncMock()
        mock_client.chat = AsyncMock(return_value=fake_response)
        mock_for.return_value = mock_client
        result = await agent.run({
            "quote-fetcher": {"code": "SZ000001", "price": 50, "pe": 150, "mv_yi": 80, "ret_60d": 0.6}
        })
    assert result.ok is True
    assert result.output.mv_tier == "small"
    assert any("game" in flag.lower() for flag in result.output.red_flags)


@pytest.mark.asyncio
async def test_fundamental_analyst_mid_cap_score_clamp(tmp_path):
    """Mid-cap tickers should have valuation_score capped at ±1."""
    fake_response = {
        "choices": [{"message": {"content": '{"valuation_score": 1, "mv_tier": "mid", "dimension_detail": {"pe": "slightly elevated"}, "red_flags": [], "bull_points": ["decent earnings growth"], "bear_points": ["sector headwinds"]}'}}]
    }
    agent = FundamentalAnalyst(memory_root=tmp_path)
    with patch("financial_analyst.agent.tier2.fundamental_analyst.LLMClient.for_agent") as mock_for:
        mock_client = AsyncMock()
        mock_client.chat = AsyncMock(return_value=fake_response)
        mock_for.return_value = mock_client
        result = await agent.run({
            "quote-fetcher": {"code": "SH601318", "price": 45, "pe": 12, "mv_yi": 600}
        })
    assert result.ok is True
    assert result.output.mv_tier == "mid"
    assert -1 <= result.output.valuation_score <= 1


@pytest.mark.asyncio
async def test_fundamental_analyst_passes_upstream_data_to_llm(tmp_path):
    """Verify upstream quote-fetcher data is included in the LLM prompt."""
    fake_response = {
        "choices": [{"message": {"content": '{"valuation_score": -1, "mv_tier": "small", "dimension_detail": {"pb": "premium"}, "red_flags": ["high pb"], "bull_points": ["asset rich"], "bear_points": ["pb premium"]}'}}]
    }
    agent = FundamentalAnalyst(memory_root=tmp_path)
    with patch("financial_analyst.agent.tier2.fundamental_analyst.LLMClient.for_agent") as mock_for:
        mock_client = AsyncMock()
        mock_client.chat = AsyncMock(return_value=fake_response)
        mock_for.return_value = mock_client
        await agent.run({"quote-fetcher": {"code": "SZ002594", "price": 250, "pe": 20, "pb": 5.0, "mv_yi": 50}})
    call_args = mock_client.chat.call_args
    messages = call_args.kwargs.get("messages") or call_args.args[0]
    user_msg = next(m["content"] for m in messages if m["role"] == "user")
    assert "SZ002594" in user_msg


@pytest.mark.asyncio
async def test_fundamental_analyst_normalizes_chinese_mv_tier(tmp_path):
    """Qwen may return '中小盘' — agent should normalize to 'small' before pydantic."""
    fake_response = {
        "choices": [{"message": {"content": '{"valuation_score": 0, "mv_tier": "中小盘", "dimension_detail": {}, "red_flags": [], "bull_points": [], "bear_points": []}'}}]
    }
    agent = FundamentalAnalyst(memory_root=tmp_path)
    with patch("financial_analyst.agent.tier2.fundamental_analyst.LLMClient.for_agent") as mock_for:
        mock_client = AsyncMock()
        mock_client.chat = AsyncMock(return_value=fake_response)
        mock_for.return_value = mock_client
        result = await agent.run({"quote-fetcher": {"mv_yi": 158}})
    assert result.ok is True
    assert result.output.mv_tier == "small"


@pytest.mark.asyncio
async def test_fundamental_analyst_rejects_invalid_mv_tier_after_norm(tmp_path):
    """If mv_tier is something truly unrecognized, pydantic Literal rejects."""
    fake_response = {
        "choices": [{"message": {"content": '{"valuation_score": 0, "mv_tier": "garbage_value", "dimension_detail": {}, "red_flags": [], "bull_points": [], "bear_points": []}'}}]
    }
    agent = FundamentalAnalyst(memory_root=tmp_path)
    with patch("financial_analyst.agent.tier2.fundamental_analyst.LLMClient.for_agent") as mock_for:
        mock_client = AsyncMock()
        mock_client.chat = AsyncMock(return_value=fake_response)
        mock_for.return_value = mock_client
        result = await agent.run({"quote-fetcher": {"mv_yi": 158}})
    assert result.ok is False
