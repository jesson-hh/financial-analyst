import pandas as pd
import pytest
from pathlib import Path
from unittest.mock import MagicMock
from financial_analyst.agent.market.market_scanner import (
    MarketScanner, _mv_tier_threshold,
)


def test_mv_tier_threshold():
    assert _mv_tier_threshold(2000) == 3.0
    assert _mv_tier_threshold(500) == 4.0
    assert _mv_tier_threshold(200) == 5.0
    assert _mv_tier_threshold(50) == 7.0


@pytest.mark.asyncio
async def test_scanner_flags_pct_anomaly(tmp_path):
    universe_file = tmp_path / "instruments.txt"
    universe_file.write_text("SH600519\t2024-01-01\t2026-05-15\n", encoding="utf-8")
    fake_quote = pd.DataFrame({
        "trade_date": pd.date_range("2026-04-01", periods=30, freq="B"),
        "open": [100]*29 + [108], "high": [105]*30, "low": [95]*30,
        "close": [100]*29 + [108],   # +8% jump on last day
        "vol": [1e6]*30, "amount": [1e8]*30,
    })
    fake_db = pd.DataFrame({"total_mv": [200_0000.0]*30})  # 200亿 small-mid
    loader = MagicMock()
    loader.fetch_quote.return_value = fake_quote
    loader.fetch_daily_basic.return_value = fake_db
    loader._roots = None   # so it uses universe_file directly

    agent = MarketScanner(memory_root=tmp_path, loader=loader,
                           universe_file=str(universe_file))
    result = await agent.run({"asof_date": "2026-05-15"})
    assert result.ok is True
    assert result.output.n_flagged == 1   # 8% > 5% threshold for small-mid tier
    first = result.output.top_gainers[0]
    code = first["code"] if isinstance(first, dict) else first.code
    assert code == "SH600519"


@pytest.mark.asyncio
async def test_scanner_flags_volume_anomaly(tmp_path):
    universe_file = tmp_path / "instruments.txt"
    universe_file.write_text("SH600519\t2024-01-01\t2026-05-15\n", encoding="utf-8")
    fake_quote = pd.DataFrame({
        "trade_date": pd.date_range("2026-04-01", periods=30, freq="B"),
        "open": [100]*29 + [101], "high": [105]*30, "low": [95]*30,
        "close": [100]*29 + [101],   # only +1%
        "vol": [1e6]*29 + [5e6],     # but 5x volume
        "amount": [1e8]*30,
    })
    fake_db = pd.DataFrame({"total_mv": [2000_0000.0]*30})  # 2000亿 large
    loader = MagicMock()
    loader.fetch_quote.return_value = fake_quote
    loader.fetch_daily_basic.return_value = fake_db
    loader._roots = None

    agent = MarketScanner(memory_root=tmp_path, loader=loader,
                           universe_file=str(universe_file))
    result = await agent.run({"asof_date": "2026-05-15"})
    assert result.ok is True
    assert result.output.n_flagged == 1   # volume_ratio 5x > 3 threshold
    first = result.output.top_gainers[0]
    flagged_by = first["flagged_by"] if isinstance(first, dict) else first.flagged_by
    assert "volume_ratio" in flagged_by


@pytest.mark.asyncio
async def test_scanner_missing_universe_raises(tmp_path):
    loader = MagicMock()
    loader._roots = None
    # Ensure FA_UNIVERSE_FILE is not set in env so the empty-path branch is hit
    import os
    old = os.environ.pop("FA_UNIVERSE_FILE", None)
    try:
        agent = MarketScanner(memory_root=tmp_path, loader=loader)
        result = await agent.run({"asof_date": "2026-05-15"})
        assert result.ok is False
        assert "universe" in result.error.lower() or "instruments" in result.error.lower()
    finally:
        if old is not None:
            os.environ["FA_UNIVERSE_FILE"] = old
