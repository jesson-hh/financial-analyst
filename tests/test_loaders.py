import pandas as pd
import pytest
from unittest.mock import MagicMock, patch
from financial_analyst.data.loaders.base import BaseLoader
from financial_analyst.data.loaders.tushare import TushareLoader
from financial_analyst.data.cache import ParquetCache


def test_base_loader_is_abstract():
    with pytest.raises(TypeError):
        BaseLoader()


def test_tushare_loader_normalizes_code():
    loader = TushareLoader(token="fake")
    assert loader._to_tushare_code("SH600519") == "600519.SH"
    assert loader._to_tushare_code("SZ000858") == "000858.SZ"
    assert loader._to_tushare_code("BJ430090") == "430090.BJ"
    assert loader._to_tushare_code("600519.SH") == "600519.SH"


def test_tushare_loader_supports_a_share():
    loader = TushareLoader(token="fake")
    assert loader.supports("a_share") is True
    assert loader.supports("us") is False


def test_tushare_fetch_quote_calls_pro_api():
    loader = TushareLoader(token="fake", enable_cache=False)
    fake_response = {
        "code": 0,
        "data": {
            "fields": ["trade_date", "open", "high", "low", "close", "vol", "amount"],
            "items": [
                ["20260515", 1700.0, 1720.0, 1690.0, 1715.0, 10000, 17150000.0],
                ["20260516", 1710.0, 1730.0, 1700.0, 1725.0, 12000, 20700000.0],
            ],
        },
    }
    with patch("financial_analyst.data.loaders.tushare.requests.post") as mock_post:
        mock_post.return_value = MagicMock(json=lambda: fake_response)
        df = loader.fetch_quote("SH600519", "2026-05-15", "2026-05-16")
        mock_post.assert_called_once()
        # Verify the request payload uses the daily api_name + ts_code conversion
        sent = mock_post.call_args.kwargs["json"]
        assert sent["api_name"] == "daily"
        assert sent["params"]["ts_code"] == "600519.SH"
        assert sent["params"]["start_date"] == "20260515"
    assert len(df) == 2
    assert "close" in df.columns
    assert pd.api.types.is_datetime64_any_dtype(df["trade_date"])


def test_tushare_cache_hit_skips_api(tmp_path):
    """Second identical call must be served from cache without hitting the API."""
    loader = TushareLoader(token="fake", cache_dir=tmp_path)
    fake_response = {
        "code": 0,
        "data": {
            "fields": ["trade_date", "open", "high", "low", "close", "vol", "amount"],
            "items": [["20260515", 1700.0, 1720.0, 1690.0, 1715.0, 10000, 17150000.0]],
        },
    }
    with patch("financial_analyst.data.loaders.tushare.requests.post") as mock_post:
        mock_post.return_value = MagicMock(json=lambda: fake_response)
        df1 = loader.fetch_quote("SH600519", "2026-05-01", "2026-05-15")
        assert mock_post.call_count == 1
        df2 = loader.fetch_quote("SH600519", "2026-05-01", "2026-05-15")
        assert mock_post.call_count == 1   # cache hit — no second network call
        assert len(df2) == 1
        assert df2["close"].iloc[0] == pytest.approx(1715.0)


def test_tushare_cache_disabled_always_calls_api(tmp_path):
    """With enable_cache=False every call hits the API."""
    loader = TushareLoader(token="fake", enable_cache=False)
    fake_response = {
        "code": 0,
        "data": {
            "fields": ["trade_date", "close"],
            "items": [["20260515", 100.0]],
        },
    }
    with patch("financial_analyst.data.loaders.tushare.requests.post") as mock_post:
        mock_post.return_value = MagicMock(json=lambda: fake_response)
        loader.fetch_quote("SH600519", "2026-05-01", "2026-05-15")
        loader.fetch_quote("SH600519", "2026-05-01", "2026-05-15")
        assert mock_post.call_count == 2


def test_tushare_daily_basic_cache_hit(tmp_path):
    """fetch_daily_basic cache works independently from fetch_quote cache."""
    loader = TushareLoader(token="fake", cache_dir=tmp_path)
    fake_response = {
        "code": 0,
        "data": {
            "fields": ["ts_code", "trade_date", "pe_ttm", "pb", "ps_ttm",
                       "dv_ttm", "total_mv", "circ_mv", "turnover_rate"],
            "items": [["600519.SH", "20260515", 30.0, 8.0, 10.0, 1.5, 2e6, 1.5e6, 2.5]],
        },
    }
    with patch("financial_analyst.data.loaders.tushare.requests.post") as mock_post:
        mock_post.return_value = MagicMock(json=lambda: fake_response)
        loader.fetch_daily_basic("SH600519", "2026-05-01", "2026-05-15")
        assert mock_post.call_count == 1
        loader.fetch_daily_basic("SH600519", "2026-05-01", "2026-05-15")
        assert mock_post.call_count == 1   # cache hit
