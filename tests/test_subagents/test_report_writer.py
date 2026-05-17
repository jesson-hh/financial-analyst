import pytest
import json
from unittest.mock import AsyncMock, patch
from financial_analyst.agent.tier3.report_writer import ReportWriter


@pytest.mark.asyncio
async def test_report_writer_creates_md_and_json(tmp_path):
    fake_content = json.dumps({
        "markdown_body": "# SH600519 Report\n\n## 综合评级\n6/10",
        "rating_overall": 6,
        "rating_dimensions": {"fundamental": 1, "technical": 1, "whale": 1, "quant": 2, "risk": 0},
        "action": "buy",
        "target_price": 1850.0,
        "stop_loss": 1500.0,
        "position_pct": 0.04,
        "summary_json": {"code": "SH600519", "decision": "buy"}
    })
    fake = {"choices": [{"message": {"content": fake_content}}]}
    out_dir = tmp_path / "out"
    agent = ReportWriter(memory_root=tmp_path)
    with patch("financial_analyst.agent.tier3.report_writer.LLMClient.for_agent") as mock_for:
        client = AsyncMock(); client.chat = AsyncMock(return_value=fake)
        mock_for.return_value = client
        result = await agent.run({"code": "SH600519", "asof_date": "2026-05-17", "out_dir": str(out_dir)})

    assert result.ok is True
    assert result.output.rating_overall == 6
    assert result.output.action == "buy"
    md_path = out_dir / "SH600519_2026-05-17.md"
    json_path = out_dir / "SH600519_2026-05-17.json"
    assert md_path.exists()
    assert json_path.exists()
    assert "SH600519 Report" in md_path.read_text(encoding="utf-8")
    assert json.loads(json_path.read_text(encoding="utf-8"))["decision"] == "buy"


@pytest.mark.asyncio
async def test_report_writer_rejects_bad_schema(tmp_path):
    bad = json.dumps({"markdown_body": "x", "rating_overall": "not-int", "rating_dimensions": {}, "action": "buy", "target_price": 1.0, "stop_loss": 1.0, "position_pct": 0.0})
    fake = {"choices": [{"message": {"content": bad}}]}
    agent = ReportWriter(memory_root=tmp_path)
    with patch("financial_analyst.agent.tier3.report_writer.LLMClient.for_agent") as mock_for:
        client = AsyncMock(); client.chat = AsyncMock(return_value=fake)
        mock_for.return_value = client
        result = await agent.run({"code": "X", "asof_date": "2026-05-17", "out_dir": str(tmp_path / "out")})
    # rating_overall = int(...) will coerce "not-int" → raises ValueError, caught in SubAgent.run
    assert result.ok is False
