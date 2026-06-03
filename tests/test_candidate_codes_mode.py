"""CandidateConfig.codes 模式: 用户指定代码 (单股/watchlist), 优先级 codes > pool > watchlist."""
import pytest
from unittest.mock import MagicMock, patch
import pandas as pd
from financial_analyst.backtest.candidate import CandidateConfig, select_candidates


def _mk_reader_with_close(close_len: int = 25):
    """Helper: 让 fetch_quote_leq_prev 返一段够 rev_20 用的 close 序列."""
    reader = MagicMock()
    reader.prev_trade_date.return_value = "2026-05-30"
    reader.fetch_quote_leq_prev.return_value = pd.DataFrame({
        "trade_date": pd.date_range("2026-04-01", periods=close_len, freq="D").astype(str),
        "close": [10.0 - i * 0.1 for i in range(close_len)],
    })
    return reader


class TestCandidateConfigCodes:
    def test_default_codes_is_none_backward_compat(self):
        cfg = CandidateConfig()
        assert cfg.codes is None

    def test_accepts_codes_arg(self):
        cfg = CandidateConfig(codes=["SH600519", "SZ002594"])
        assert cfg.codes == ["SH600519", "SZ002594"]


@patch("financial_analyst.data.universe.resolve_universe_codes")
def test_codes_mode_uses_user_codes_as_base(mock_resolve):
    """codes 模式: base = user codes, 不解析 pool/watchlist."""
    reader = _mk_reader_with_close()
    cfg = CandidateConfig(codes=["SH600519", "SZ002594"], topn=2)
    result = select_candidates("2026-05-31", holdings=[], reader=reader, cfg=cfg)
    # resolve_universe_codes NOT called in codes mode
    assert not mock_resolve.called
    # base = codes
    assert set(result.codes) == {"SH600519", "SZ002594"}
    # filter_stats n_pool = len(codes)
    assert result.filter_stats["n_pool"] == 2


@patch("financial_analyst.data.universe.resolve_universe_codes")
def test_codes_mode_overrides_pool(mock_resolve):
    """codes 非空 + pool 非空 → codes 优先, pool 被忽略."""
    mock_resolve.return_value = ["SH600000", "SH600001"]  # 不应被调用
    reader = _mk_reader_with_close()
    cfg = CandidateConfig(codes=["SH600519"], pool="csi300", topn=1)
    result = select_candidates("2026-05-31", holdings=[], reader=reader, cfg=cfg)
    # codes 优先, pool 不解析
    assert not mock_resolve.called
    assert set(result.codes) == {"SH600519"}


@patch("financial_analyst.data.universe.resolve_universe_codes")
def test_codes_mode_includes_holdings(mock_resolve):
    """codes 模式: holdings ∪ codes 去重."""
    reader = _mk_reader_with_close()
    cfg = CandidateConfig(codes=["SH600519"], topn=1)
    result = select_candidates("2026-05-31", holdings=["SZ000001"], reader=reader, cfg=cfg)
    # holdings + codes deduped
    assert "SZ000001" in result.codes  # holding 入选
    assert "SH600519" in result.codes  # user code 入选
    # filter_stats
    assert result.filter_stats["n_holdings"] == 1
    assert result.filter_stats["n_pool"] == 1  # codes 长度
    assert not mock_resolve.called
