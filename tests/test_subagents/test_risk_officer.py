import pytest
from unittest.mock import AsyncMock, patch
from financial_analyst.agent.tier3.risk_officer import RiskOfficer


@pytest.mark.asyncio
async def test_risk_officer_veto_on_game_capital(tmp_path):
    fake = {"choices": [{"message": {"content": '{"risk_score": -2, "blind_spots": [], "position_sizing_advice": "0%", "veto_flags": ["game_capital_speculation"], "conditional_approval": "do not enter", "hard_rule_triggers": ["Rule 1 game-capital veto"]}'}}]}
    agent = RiskOfficer(memory_root=tmp_path)
    with patch("financial_analyst.agent.tier3.risk_officer.LLMClient.for_agent") as mock_for:
        client = AsyncMock(); client.chat = AsyncMock(return_value=fake)
        mock_for.return_value = client
        result = await agent.run({"factor-computer": {"vol_regime": {}}})
    assert result.ok is True
    assert "game_capital_speculation" in result.output.veto_flags
    assert result.output.position_sizing_advice == "0%"


@pytest.mark.asyncio
async def test_risk_officer_normal_approval(tmp_path):
    fake = {"choices": [{"message": {"content": '{"risk_score": 0, "blind_spots": ["no analyst noticed inventory drawdown"], "position_sizing_advice": "3-5%", "veto_flags": [], "conditional_approval": "OK with stop at 1450", "hard_rule_triggers": []}'}}]}
    agent = RiskOfficer(memory_root=tmp_path)
    with patch("financial_analyst.agent.tier3.risk_officer.LLMClient.for_agent") as mock_for:
        client = AsyncMock(); client.chat = AsyncMock(return_value=fake)
        mock_for.return_value = client
        result = await agent.run({})
    assert result.ok is True
    assert result.output.veto_flags == []
    assert result.output.position_sizing_advice == "3-5%"


@pytest.mark.asyncio
async def test_risk_officer_rejects_bad(tmp_path):
    bad = {"choices": [{"message": {"content": '{"risk_score": "bad", "blind_spots": [], "position_sizing_advice": "1%", "veto_flags": [], "conditional_approval": "", "hard_rule_triggers": []}'}}]}
    agent = RiskOfficer(memory_root=tmp_path)
    with patch("financial_analyst.agent.tier3.risk_officer.LLMClient.for_agent") as mock_for:
        client = AsyncMock(); client.chat = AsyncMock(return_value=bad)
        mock_for.return_value = client
        result = await agent.run({})
    assert result.ok is False
