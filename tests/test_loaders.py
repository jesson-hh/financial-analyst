import pandas as pd
import pytest
from unittest.mock import MagicMock, patch
from financial_analyst.data.loaders.base import BaseLoader
from financial_analyst.data.loaders.tushare import TushareLoader


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
    loader = TushareLoader(token="fake")
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
