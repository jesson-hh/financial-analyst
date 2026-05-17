import pytest
from unittest.mock import AsyncMock, patch
from financial_analyst.agent.tier1.news_reader import NewsReader

@pytest.mark.asyncio
async def test_news_reader_with_no_files(tmp_path):
    news_dir = tmp_path / "news" / "SH600519"
    news_dir.mkdir(parents=True)
    agent = NewsReader(memory_root=tmp_path, news_root=tmp_path / "news")
    result = await agent.run({"code": "SH600519", "asof_date": "2026-05-17"})
    assert result.ok is True
    assert result.output.events == []

@pytest.mark.asyncio
async def test_news_reader_parses_files(tmp_path):
    news_dir = tmp_path / "news" / "SH600519"
    news_dir.mkdir(parents=True)
    (news_dir / "2026-05-10.txt").write_text("公司发布2026年Q1业绩, 营收同比增长12%, 净利润同比增长18%")
    fake_llm = {
        "choices": [{"message": {"content": '{"events":[{"date":"2026-05-10","category":"earnings","sentiment":"pos","summary":"Q1 revenue 12% YoY net profit 18% YoY","severity":0}],"numbers":[]}'}}]
    }
    agent = NewsReader(memory_root=tmp_path, news_root=tmp_path / "news")
    with patch.object(agent, "_call_llm", AsyncMock(return_value=fake_llm)):
        result = await agent.run({"code": "SH600519", "asof_date": "2026-05-17"})
    assert result.ok is True
    assert len(result.output.events) == 1
    assert result.output.events[0].category == "earnings"
