import pandas as pd
import pytest
from unittest.mock import MagicMock, patch
from financial_analyst.agent.tier1.quote_fetcher import QuoteFetcher


@pytest.mark.asyncio
async def test_quote_fetcher_outputs_required_fields(tmp_path):
    fake_quote = pd.DataFrame({
        "trade_date": pd.date_range("2026-04-01", periods=80, freq="B"),
        "open": [10.0] * 80, "high": [11.0] * 80, "low": [9.5] * 80,
        "close": [10.5] * 80, "vol": [1e6] * 80, "amount": [1e7] * 80,
    })
    fake_db = pd.DataFrame({
        "trade_date": [pd.Timestamp("2026-05-17")],
        "pe_ttm": [25.0], "pb": [3.0], "ps_ttm": [2.0], "dv_ttm": [1.5],
        "total_mv": [80_0000.0], "circ_mv": [50_0000.0], "turnover_rate": [3.5],
    })
    agent = QuoteFetcher(memory_root=tmp_path)
    with patch.object(agent, "_get_loader") as m:
        loader = MagicMock()
        loader.fetch_quote.return_value = fake_quote
        loader.fetch_daily_basic.return_value = fake_db
        m.return_value = loader
        result = await agent.run({"code": "SH600519", "asof_date": "2026-05-17"})
    assert result.ok is True
    out = result.output
    assert out.code == "SH600519"
    assert out.price == 10.5
    assert out.pe == 25.0
    assert out.mv_yi == 80.0
