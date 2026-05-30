"""Tests for ETFLoader — Task 7 of the ETF data layer."""
import pandas as pd
import pytest

from financial_analyst.data.loaders.etf import ETFLoader


def _ld(tmp):
    return ETFLoader(parquet_root=tmp, etf_qlib_uri=tmp / "cn_data_etf")


def test_premium_discount_from_spot(tmp_path):
    pd.DataFrame(
        {
            "ts_code": ["SH510300"],
            "asof": ["2026-05-29"],
            "iopv": [4.93],
            "premium_discount_pct": [-0.2],
            "shares": [1e10],
            "aum": [9e10],
            "turnover": [3.5],
        }
    ).to_parquet(tmp_path / "etf_spot.parquet", index=False)
    result = _ld(tmp_path).fetch_etf_premium_discount("SH510300")
    assert abs(result["realtime_premium_discount_pct"] - (-0.2)) < 1e-6


def test_meta_from_basic(tmp_path):
    pd.DataFrame(
        {
            "ts_code": ["510300.SH"],
            "name": ["300ETF"],
            "m_fee": [0.5],
            "c_fee": [0.1],
            "benchmark": ["沪深300"],
            "index_code": ["000300.SH"],
            "fund_type": ["ETF"],
            "invest_type": ["被动指数型"],
        }
    ).to_parquet(tmp_path / "etf_basic.parquet", index=False)
    m = _ld(tmp_path).fetch_etf_meta("SH510300")
    assert m["total_fee"] == 0.6 and m["index_code"] == "000300.SH"


def test_holdings_latest_quarter_topn(tmp_path):
    pd.DataFrame(
        {
            "ts_code": ["510300.SH"] * 3,
            "end_date": ["20260331", "20260331", "20251231"],
            "symbol": ["600519.SH", "300750.SZ", "000001.SZ"],
            "mkv": [9, 8, 7],
            "stk_mkv_ratio": [9.0, 8.0, 7.0],
        }
    ).to_parquet(tmp_path / "etf_holdings.parquet", index=False)
    h = _ld(tmp_path).fetch_etf_holdings("SH510300", top_n=2)
    assert h["end_date"] == "20260331" and len(h["holdings"]) == 2
    assert h["holdings"][0]["symbol"] == "600519.SH"


def test_flow_from_share_diff(tmp_path):
    pd.DataFrame(
        {
            "ts_code": ["510300.SH"] * 2,
            "trade_date": ["20260528", "20260529"],
            "fd_share": [1.0e6, 1.1e6],  # realistic 万份 values
        }
    ).to_parquet(tmp_path / "etf_share.parquet", index=False)
    pd.DataFrame(
        {
            "ts_code": ["510300.SH"] * 2,
            "nav_date": ["20260528", "20260529"],
            "unit_nav": [4.9, 4.95],
            "accum_nav": [5, 5],
            "adj_nav": [5, 5],
            "net_asset": [1, 1],
        }
    ).to_parquet(tmp_path / "etf_nav.parquet", index=False)
    f = _ld(tmp_path).fetch_etf_flow("SH510300")
    assert f["latest_share_change"] > 0  # 1.0e10 -> 1.1e10 = net creation


def test_meta_missing_returns_empty(tmp_path):
    """fetch_etf_meta on a missing parquet should return empty dict."""
    result = _ld(tmp_path).fetch_etf_meta("SH510300")
    assert result == {}


def test_premium_discount_missing_returns_none(tmp_path):
    """fetch_etf_premium_discount when no spot parquet returns None value."""
    result = _ld(tmp_path).fetch_etf_premium_discount("SH510300")
    assert result["realtime_premium_discount_pct"] is None


def test_fetch_etf_quote_no_bin_returns_empty(tmp_path):
    """fetch_etf_quote with nonexistent uri returns empty DataFrame (no crash)."""
    result = _ld(tmp_path).fetch_etf_quote("SH510300", "2026-01-01", "2026-05-29")
    assert isinstance(result, pd.DataFrame)
    assert result.empty


def test_tracking_error_no_data_returns_none(tmp_path):
    """fetch_tracking_error returns None gracefully when no nav/index data."""
    result = _ld(tmp_path).fetch_tracking_error("SH510300")
    assert result["tracking_error_annualized"] is None


def test_tracking_error_date_joined(tmp_path):
    """Date-join TE: nav has an extra leading date the index lacks.

    With positional diff the misaligned windows inflate TE wildly (~16%).
    With date-join the returns are aligned and TE is small (<0.5 annualised).
    """
    pd.DataFrame({"ts_code": ["510300.SH"], "name": ["x"], "m_fee": [0.1], "c_fee": [0.0],
                  "benchmark": ["沪深300"], "index_code": ["000300.SH"], "fund_type": ["ETF"],
                  "invest_type": ["被动"]}).to_parquet(tmp_path / "etf_basic.parquet", index=False)
    # nav has an extra leading date (20260101) the index lacks -> positional diff would misalign
    dates_nav = ["20260101", "20260102", "20260105", "20260106", "20260107", "20260108", "20260109"]
    navs = [1.00, 1.01, 1.02, 1.011, 1.015, 1.02, 1.025]
    pd.DataFrame({"ts_code": ["510300.SH"] * 7, "nav_date": dates_nav, "unit_nav": navs,
                  "accum_nav": navs, "adj_nav": navs, "net_asset": [1] * 7}
                 ).to_parquet(tmp_path / "etf_nav.parquet", index=False)
    dates_idx = ["20260102", "20260105", "20260106", "20260107", "20260108", "20260109"]
    closes = [4000, 4040, 4001, 4016, 4040, 4060]   # tracks nav closely on shared dates
    pd.DataFrame({"ts_code": ["000300.SH"] * 6, "trade_date": dates_idx, "close": closes}
                 ).to_parquet(tmp_path / "etf_index.parquet", index=False)
    L = ETFLoader(parquet_root=tmp_path, etf_qlib_uri=tmp_path / "cn_data_etf")
    te = L.fetch_tracking_error("SH510300", window=10)["tracking_error_annualized"]
    assert te is not None and te < 0.5   # date-joined TE is small, not the inflated positional value
