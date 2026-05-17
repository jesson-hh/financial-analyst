import pytest
from unittest.mock import AsyncMock, patch
from financial_analyst.agent.tier2.whale_analyst import WhaleAnalyst


@pytest.mark.asyncio
async def test_whale_analyst_runs(tmp_path):
    fake = {"choices": [{"message": {"content": '{"whale_score": -2, "sentiment_label": "super_distr", "vol_regime_label": "super_distr", "board_total_score": null, "alerts": ["super distribution"], "bull_points": [], "bear_points": ["super_distr SS-grade signal"]}'}}]}
    agent = WhaleAnalyst(memory_root=tmp_path)
    with patch("financial_analyst.agent.tier2.whale_analyst.LLMClient.for_agent") as mock_for:
        client = AsyncMock()
        client.chat = AsyncMock(return_value=fake)
        mock_for.return_value = client
        result = await agent.run({"factor-computer": {"vol_regime": {"regime_label": "super_distr"}}})
    assert result.ok is True
    assert result.output.sentiment_label == "super_distr"
    assert result.output.whale_score == -2


@pytest.mark.asyncio
async def test_whale_analyst_rejects_bad(tmp_path):
    bad = {"choices": [{"message": {"content": '{"whale_score": "bad", "sentiment_label": "x", "vol_regime_label": "y", "alerts": [], "bull_points": [], "bear_points": []}'}}]}
    agent = WhaleAnalyst(memory_root=tmp_path)
    with patch("financial_analyst.agent.tier2.whale_analyst.LLMClient.for_agent") as mock_for:
        client = AsyncMock()
        client.chat = AsyncMock(return_value=bad)
        mock_for.return_value = client
        result = await agent.run({})
    assert result.ok is False


@pytest.mark.asyncio
async def test_whale_analyst_bounce_scenario(tmp_path):
    """Bounce regime with accumulating whale should yield positive score."""
    fake = {"choices": [{"message": {"content": '{"whale_score": 2, "sentiment_label": "bounce", "vol_regime_label": "bounce", "board_total_score": null, "alerts": ["bounce setup"], "bull_points": ["OBV accumulating", "bounce S6/S7 +0.94pp"], "bear_points": []}'}}]}
    agent = WhaleAnalyst(memory_root=tmp_path)
    with patch("financial_analyst.agent.tier2.whale_analyst.LLMClient.for_agent") as mock_for:
        client = AsyncMock()
        client.chat = AsyncMock(return_value=fake)
        mock_for.return_value = client
        result = await agent.run({
            "quote-fetcher": {"price": 50},
            "factor-computer": {
                "whale_signals": {"whale_judge": "accumulating", "obv_trend": "up"},
                "vol_regime": {"regime_label": "bounce"},
            },
        })
    assert result.ok is True
    assert result.output.whale_score == 2
    assert result.output.sentiment_label == "bounce"
    assert "bounce setup" in result.output.alerts


@pytest.mark.asyncio
async def test_whale_analyst_board_total_score(tmp_path):
    """board_total_score is present when a limit-up day exists."""
    fake = {"choices": [{"message": {"content": '{"whale_score": 1, "sentiment_label": "neutral", "vol_regime_label": "neutral", "board_total_score": 6, "alerts": [], "bull_points": ["strong board quality score 6"], "bear_points": []}'}}]}
    agent = WhaleAnalyst(memory_root=tmp_path)
    with patch("financial_analyst.agent.tier2.whale_analyst.LLMClient.for_agent") as mock_for:
        client = AsyncMock()
        client.chat = AsyncMock(return_value=fake)
        mock_for.return_value = client
        result = await agent.run({
            "factor-computer": {
                "board_score": {"board_total_score": 6, "seal_at_close": True},
                "vol_regime": {"regime_label": "neutral"},
            },
        })
    assert result.ok is True
    assert result.output.board_total_score == 6


@pytest.mark.asyncio
async def test_whale_analyst_broken_board_alert(tmp_path):
    """seal_at_close=False should trigger broken board alert and negative score."""
    fake = {"choices": [{"message": {"content": '{"whale_score": -2, "sentiment_label": "distr", "vol_regime_label": "distr", "board_total_score": -3, "alerts": ["broken board", "distribution"], "bull_points": [], "bear_points": ["seal_at_close=False extreme negative", "distr -1.42pp signal"]}'}}]}
    agent = WhaleAnalyst(memory_root=tmp_path)
    with patch("financial_analyst.agent.tier2.whale_analyst.LLMClient.for_agent") as mock_for:
        client = AsyncMock()
        client.chat = AsyncMock(return_value=fake)
        mock_for.return_value = client
        result = await agent.run({
            "factor-computer": {
                "board_score": {"board_total_score": -3, "seal_at_close": False},
                "vol_regime": {"regime_label": "distr"},
            },
        })
    assert result.ok is True
    assert result.output.whale_score == -2
    assert "broken board" in result.output.alerts


@pytest.mark.asyncio
async def test_whale_analyst_passes_upstream_data(tmp_path):
    """Verify whale_signals, board_score, and vol_regime appear in LLM user message."""
    fake = {"choices": [{"message": {"content": '{"whale_score": 0, "sentiment_label": "neutral", "vol_regime_label": "neutral", "board_total_score": null, "alerts": [], "bull_points": ["stable"], "bear_points": ["no trend"]}'}}]}
    agent = WhaleAnalyst(memory_root=tmp_path)
    with patch("financial_analyst.agent.tier2.whale_analyst.LLMClient.for_agent") as mock_for:
        client = AsyncMock()
        client.chat = AsyncMock(return_value=fake)
        mock_for.return_value = client
        await agent.run({
            "quote-fetcher": {"code": "SH600519", "price": 1700},
            "factor-computer": {
                "whale_signals": {"whale_judge": "neutral", "obv_trend": "flat"},
                "board_score": {"board_total_score": 2},
                "vol_regime": {"regime_label": "neutral"},
            },
        })
    call_args = client.chat.call_args
    messages = call_args.kwargs.get("messages") or call_args.args[0]
    user_msg = next(m["content"] for m in messages if m["role"] == "user")
    assert "SH600519" in user_msg
    assert "whale_signals" in user_msg
    assert "vol_regime" in user_msg


@pytest.mark.asyncio
async def test_whale_analyst_tail_surge(tmp_path):
    """tail_surge regime should produce negative score and alert."""
    fake = {"choices": [{"message": {"content": '{"whale_score": -1, "sentiment_label": "tail_surge", "vol_regime_label": "tail_surge", "board_total_score": null, "alerts": ["tail-surge"], "bull_points": [], "bear_points": ["tail-surge -1.40pp signal"]}'}}]}
    agent = WhaleAnalyst(memory_root=tmp_path)
    with patch("financial_analyst.agent.tier2.whale_analyst.LLMClient.for_agent") as mock_for:
        client = AsyncMock()
        client.chat = AsyncMock(return_value=fake)
        mock_for.return_value = client
        result = await agent.run({
            "factor-computer": {"vol_regime": {"regime_label": "tail_surge"}},
        })
    assert result.ok is True
    assert result.output.sentiment_label == "tail_surge"
    assert "tail-surge" in result.output.alerts
