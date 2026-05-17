import pytest
from unittest.mock import AsyncMock, patch
from financial_analyst.agent.tier1.f10_reader import F10Reader

@pytest.mark.asyncio
async def test_f10_reader_no_files(tmp_path):
    agent = F10Reader(memory_root=tmp_path, f10_root=tmp_path / "f10")
    result = await agent.run({"code": "SH600519", "asof_date": "2026-05-17"})
    assert result.ok is True
    assert result.output.recent_events == []
    assert result.output.lhb_seats == {}
