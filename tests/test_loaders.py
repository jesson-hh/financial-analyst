import pandas as pd
import pytest
from unittest.mock import MagicMock, patch
from financial_analyst.data.loaders.base import BaseLoader
from financial_analyst.data.loaders.tushare import TushareLoader


def test_base_loader_is_abstract():
    with pytest.raises(TypeError):
        BaseLoader()


def test_tushare_loader_normalizes_code():
    with patch("tushare.pro_api"):
        loader = TushareLoader(token="fake")
    assert loader._to_tushare_code("SH600519") == "600519.SH"
    assert loader._to_tushare_code("SZ000858") == "000858.SZ"
    assert loader._to_tushare_code("BJ430090") == "430090.BJ"
    assert loader._to_tushare_code("600519.SH") == "600519.SH"


def test_tushare_loader_supports_a_share():
    with patch("tushare.pro_api"):
        loader = TushareLoader(token="fake")
    assert loader.supports("a_share") is True
    assert loader.supports("us") is False


def test_tushare_fetch_quote_calls_pro_api():
    fake_df = pd.DataFrame({
        "trade_date": ["20260515", "20260516"],
        "open": [1700.0, 1710.0],
        "high": [1720.0, 1730.0],
        "low": [1690.0, 1700.0],
        "close": [1715.0, 1725.0],
        "vol": [10000, 12000],
        "amount": [17150000.0, 20700000.0],
    })
    with patch("tushare.pro_api") as mock_pro_api:
        mock_pro_instance = MagicMock()
        mock_pro_api.return_value = mock_pro_instance
        loader = TushareLoader(token="fake")
        mock_pro_instance.daily.return_value = fake_df
        df = loader.fetch_quote("SH600519", "2026-05-15", "2026-05-16")
        mock_pro_instance.daily.assert_called_once()
        assert len(df) == 2
        assert "close" in df.columns
        assert pd.api.types.is_datetime64_any_dtype(df["trade_date"])
