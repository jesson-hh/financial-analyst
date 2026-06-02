"""CandidateConfig.pool 语义切换: None=旧 watchlist 路径, 非空=池子模式"""
import pytest
from unittest.mock import MagicMock, patch
from financial_analyst.backtest.candidate import CandidateConfig, select_candidates


class TestCandidateConfigPool:
    def test_default_pool_is_none_backward_compat(self):
        cfg = CandidateConfig()
        assert cfg.pool is None

    def test_accepts_pool_arg(self):
        cfg = CandidateConfig(pool="csi300")
        assert cfg.pool == "csi300"

    @patch("financial_analyst.data.universe.resolve_universe_codes")
    def test_pool_mode_uses_resolved_codes(self, mock_resolve):
        mock_resolve.return_value = ["SH600000", "SH600001", "SH600002"]
        reader = MagicMock()
        reader.prev_trade_date.return_value = "2026-05-30"
        # 让 fetch_quote_leq_prev 返一段够长的 close 序列
        import pandas as pd
        reader.fetch_quote_leq_prev.return_value = pd.DataFrame({
            "trade_date": pd.date_range("2026-04-01", periods=25, freq="D").astype(str),
            "close": [10.0 - i*0.1 for i in range(25)],
        })
        cfg = CandidateConfig(pool="csi300", topn=2)
        result = select_candidates("2026-05-31", holdings=[], reader=reader, cfg=cfg)
        assert mock_resolve.called
        assert mock_resolve.call_args[0][0] == "csi300"
        # base 来自 pool, 不是 watchlist
        assert all(c in ("SH600000", "SH600001", "SH600002") for c in result.codes)

    @patch("financial_analyst.backtest.candidate._load_watchlist_codes")
    def test_pool_none_keeps_old_watchlist_path(self, mock_watch):
        mock_watch.return_value = ["SH000001", "SH000002"]
        reader = MagicMock()
        reader.prev_trade_date.return_value = "2026-05-30"
        reader.fetch_quote_leq_prev.return_value = None
        cfg = CandidateConfig(pool=None)
        result = select_candidates("2026-05-31", holdings=[], reader=reader, cfg=cfg)
        # 老路径仍调 watchlist
        assert mock_watch.called

    @patch("financial_analyst.data.universe.resolve_universe_codes")
    def test_pool_unresolvable_raises(self, mock_resolve):
        mock_resolve.return_value = []
        reader = MagicMock()
        reader.prev_trade_date.return_value = "2026-05-30"
        cfg = CandidateConfig(pool="bad_pool_name")
        with pytest.raises(ValueError, match="resolved to 0 codes"):
            select_candidates("2026-05-31", holdings=[], reader=reader, cfg=cfg)
