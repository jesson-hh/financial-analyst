import json
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, patch
from financial_analyst.agent.market.morning_brief_writer import MorningBriefWriter


@pytest.mark.asyncio
async def test_writer_creates_files(tmp_path):
    upstream = {
        "as_of": "2026-05-15",
        "universe": "all",
        "n_scanned": 5000,
        "n_flagged": 47,
        "top_gainers": [{"code": "SH600519", "pct_chg": 5.2, "mv_tier": "large",
                          "flagged_by": ["pct_chg"], "close": 1700.0, "prev_close": 1616.0,
                          "volume_ratio": 1.5, "mv_yi": 2100}],
        "top_losers": [],
        "volume_anomalies": [],
        "index_snapshot": {"SH000300_close": 4000.0, "SH000300_pct": 0.5},
    }
    fake_content = json.dumps({
        "markdown_body": "# Morning Brief 2026-05-15\n茅台领涨大盘股",
        "headline": "白酒板块异动, 茅台领涨",
        "watchlist_today": ["SH600519"],
        "hot_themes": ["白酒"],
        "summary_json": {"flagged": 47, "watchlist": ["SH600519"]},
    })
    fake = {"choices": [{"message": {"content": fake_content}}]}
    out_dir = tmp_path / "out"
    agent = MorningBriefWriter(memory_root=tmp_path)
    with patch("financial_analyst.agent.market.morning_brief_writer.LLMClient.for_agent") as mock_for:
        client = AsyncMock(); client.chat = AsyncMock(return_value=fake)
        mock_for.return_value = client
        result = await agent.run({"market-scanner": upstream, "out_dir": str(out_dir)})
    assert result.ok is True
    assert "茅台" in result.output.headline
    assert "SH600519" in result.output.watchlist_today
    md = out_dir / "morning_brief_2026-05-15.md"
    assert md.exists()
    assert "Morning Brief 2026-05-15" in md.read_text(encoding="utf-8")
