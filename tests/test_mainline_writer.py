import json
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, patch
from financial_analyst.agent.mainline.mainline_writer import MainlineWriter


@pytest.mark.asyncio
async def test_writer_creates_md_and_json(tmp_path):
    upstream = {
        "as_of": "2026-05-15",
        "status_groups": {
            "mainline": [{"industry": "AI算力", "ex_60d": 12.3}],
            "cold": [{"industry": "白酒", "ex_60d": -5.0}],
        },
        "just_become_mainline": [{"industry": "半导体", "ex_60d": 8.0}],
        "alpha_summary": {"mainline": "fwd_60d +4.05pp"},
    }
    fake_response_content = json.dumps({
        "markdown_body": "# Mainline 2026-05-15\nHeadline ...",
        "headline": "AI算力主升, 半导体金信号",
        "actionable_signals": ["半导体 3-5% 仓位"],
        "summary_json": {"mainlines": ["AI算力"], "golden": ["半导体"]},
    })
    fake = {"choices": [{"message": {"content": fake_response_content}}]}
    out_dir = tmp_path / "out"
    agent = MainlineWriter(memory_root=tmp_path)
    with patch("financial_analyst.agent.mainline.mainline_writer.LLMClient.for_agent") as mock_for:
        client = AsyncMock(); client.chat = AsyncMock(return_value=fake)
        mock_for.return_value = client
        result = await agent.run({"mainline-classifier": upstream, "out_dir": str(out_dir)})
    assert result.ok is True
    assert "半导体金信号" in result.output.headline
    md = out_dir / "mainline_2026-05-15.md"
    assert md.exists()
    assert "Mainline 2026-05-15" in md.read_text(encoding="utf-8")
