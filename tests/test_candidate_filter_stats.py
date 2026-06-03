"""CandidateResult.filter_stats 由 select_candidates 填充, 供前端 PoolFilterPopover 显示真数字."""
import pytest
from unittest.mock import MagicMock, patch
from financial_analyst.backtest.candidate import CandidateConfig, select_candidates


@patch("financial_analyst.data.universe.resolve_universe_codes")
def test_pool_mode_emits_filter_stats(mock_resolve):
    mock_resolve.return_value = ["SH600000", "SH600001", "SH600002", "SH600003", "SH600004"]
    reader = MagicMock()
    reader.prev_trade_date.return_value = "2026-05-30"
    import pandas as pd
    reader.fetch_quote_leq_prev.return_value = pd.DataFrame({
        "trade_date": pd.date_range("2026-04-01", periods=25, freq="D").astype(str),
        "close": [10.0 - i*0.1 for i in range(25)],
    })
    cfg = CandidateConfig(pool="csi300", topn=3)
    result = select_candidates("2026-05-31", holdings=["SH600100"], reader=reader, cfg=cfg)
    assert result.filter_stats
    assert result.filter_stats["n_pool"] == 5
    assert result.filter_stats["n_holdings"] == 1
    assert result.filter_stats["n_base"] >= 5  # holdings ∪ pool, deduped
    assert result.filter_stats["n_rev20_computable"] >= 5  # 都能算 rev_20 (mock 数据 25 点)
    assert result.filter_stats["n_final"] >= 3  # topn=3 + holdings


@patch("financial_analyst.backtest.candidate._load_watchlist_codes")
def test_watchlist_mode_emits_filter_stats(mock_watch):
    mock_watch.return_value = ["SH000001", "SH000002"]
    reader = MagicMock()
    reader.prev_trade_date.return_value = "2026-05-30"
    reader.fetch_quote_leq_prev.return_value = None
    cfg = CandidateConfig(pool=None)
    result = select_candidates("2026-05-31", holdings=[], reader=reader, cfg=cfg)
    assert "n_pool" in result.filter_stats  # 即使 watchlist mode 也填
    assert result.filter_stats["n_pool"] == 2  # len(watch)
    assert result.filter_stats["n_holdings"] == 0
