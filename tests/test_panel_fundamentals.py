from __future__ import annotations
import numpy as np
import pandas as pd
import pytest
from financial_analyst.factors.zoo import PanelData


def _df_with(cols_extra: dict):
    dates = pd.date_range("2024-01-01", periods=6, freq="B")
    idx = pd.MultiIndex.from_product([dates, ["A", "B", "C"]], names=["datetime", "code"])
    base = {"open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "volume": 1e6}
    data = {k: pd.Series(v, index=idx) for k, v in base.items()}
    for k, v in cols_extra.items():
        data[k] = pd.Series(v, index=idx)
    return pd.DataFrame(data)


def test_fundamental_property_returns_column_when_present():
    p = PanelData(_df_with({"pe_ttm": 15.0, "dv_ttm": 2.5, "total_mv": 5e6}))
    assert (p.pe_ttm == 15.0).all()
    assert (p.dv_ttm == 2.5).all()
    assert (p.total_mv == 5e6).all()


def test_fundamental_property_nan_when_absent():
    p = PanelData(_df_with({}))  # only OHLCV
    for name in ["pe_ttm", "pb", "ps_ttm", "dv_ttm", "total_mv", "circ_mv", "turnover_rate"]:
        s = getattr(p, name)
        assert isinstance(s, pd.Series)
        assert s.index.equals(p.df.index)
        assert s.isna().all()


def _stub_loader(daily_basic_shape="trade_date_col", db_empty=False):
    class Stub:
        def fetch_quote(self, code, start, end, freq="day"):
            dates = pd.date_range("2024-01-02", periods=20, freq="B")
            rng = np.random.default_rng(abs(hash(code)) % 9999)
            close = 50 * np.exp(np.cumsum(rng.standard_normal(len(dates)) * 0.02))
            df = pd.DataFrame({"open": close, "high": close * 1.01, "low": close * 0.99,
                               "close": close, "volume": np.full(len(dates), 1e6)}, index=dates)
            df.index.name = "datetime"
            return df

        def fetch_daily_basic(self, code, start, end):
            if db_empty:
                return pd.DataFrame()
            dates = pd.date_range("2024-01-02", periods=20, freq="B")
            db = pd.DataFrame({
                "pe_ttm": np.linspace(10, 30, len(dates)),
                "pb": np.linspace(1, 3, len(dates)),
                "ps_ttm": np.linspace(2, 5, len(dates)),
                "dv_ttm": np.linspace(0.5, 4, len(dates)),
                "total_mv": np.linspace(1e6, 5e6, len(dates)),
                "circ_mv": np.linspace(8e5, 4e6, len(dates)),
                "turnover_rate": np.linspace(0.5, 3, len(dates)),
            }, index=dates)
            db.index.name = "trade_date"
            if daily_basic_shape == "trade_date_col":
                return db.reset_index()   # trade_date as a COLUMN (real-loader shape)
            db.index.name = "datetime"
            return db                      # datetime-indexed (stub shape)
    return Stub()


def test_from_loader_merges_daily_basic_trade_date_col():
    panel = PanelData.from_loader(_stub_loader("trade_date_col"),
                                  ["SH600519", "SZ000858", "SH600036"], "2024-01-01", "2024-02-01")
    assert "close" in panel.df.columns and "pe_ttm" in panel.df.columns
    assert panel.pe_ttm.notna().any()
    assert panel.dv_ttm.notna().any()


def test_from_loader_merges_daily_basic_datetime_index():
    panel = PanelData.from_loader(_stub_loader("datetime_index"),
                                  ["SH600519", "SZ000858"], "2024-01-01", "2024-02-01")
    assert panel.pe_ttm.notna().any()


def test_from_loader_daily_basic_missing_ok():
    panel = PanelData.from_loader(_stub_loader(db_empty=True),
                                  ["SH600519", "SZ000858"], "2024-01-01", "2024-02-01")
    assert "close" in panel.df.columns
    assert panel.pe_ttm.isna().all()


def test_from_loader_intraday_skips_daily_basic():
    class StubIntraday:
        def fetch_quote(self, code, start, end, freq="day"):
            dates = pd.date_range("2024-01-02 09:30", periods=20, freq="5min")
            close = np.full(len(dates), 50.0)
            df = pd.DataFrame({"open": close, "high": close, "low": close,
                               "close": close, "volume": np.full(len(dates), 1e6)}, index=dates)
            df.index.name = "datetime"
            return df
        def fetch_daily_basic(self, code, start, end):
            raise AssertionError("fetch_daily_basic must NOT be called for intraday freq")
    panel = PanelData.from_loader(StubIntraday(), ["SH600519"], "2024-01-02", "2024-01-03", freq="5min")
    assert "close" in panel.df.columns
