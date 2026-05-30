"""Tests for etf_fund updater (Tushare fund_* -> etf_*.parquet)."""
import pandas as pd
import pytest

from financial_analyst.data.updaters import etf_fund


def test_update_basic_writes_parquet(tmp_path, monkeypatch):
    fake = pd.DataFrame({
        "ts_code": ["510300.SH"],
        "name": ["300ETF"],
        "management": ["华泰柏瑞"],
        "m_fee": [0.5],
        "c_fee": [0.1],
        "benchmark": ["沪深300指数收益率"],
        "fund_type": ["ETF"],
        "invest_type": ["被动指数型"],
        "list_date": ["20120528"],
    })
    monkeypatch.setattr(etf_fund, "_fund_query", lambda token, api, **kw: fake)
    monkeypatch.setattr(etf_fund, "_resolve_index_code", lambda b, token=None: "000300.SH")
    etf_fund.update_etf_basic(["SH510300"], parquet_root=tmp_path, token="x")
    df = pd.read_parquet(tmp_path / "etf_basic.parquet")
    assert "510300.SH" in df["ts_code"].values
    assert {"m_fee", "c_fee", "benchmark", "index_code"} <= set(df.columns)


def test_update_nav_concats_per_code(tmp_path, monkeypatch):
    def fake_q(token, api, fields="", **kw):
        ts = kw.get("ts_code", "")
        return pd.DataFrame({
            "ts_code": [ts],
            "nav_date": ["20260529"],
            "unit_nav": [4.9],
            "accum_nav": [5.1],
            "adj_nav": [5.2],
            "net_asset": [9e10],
        })

    monkeypatch.setattr(etf_fund, "_fund_query", fake_q)
    etf_fund.update_etf_nav(["SH510300", "SZ159915"], parquet_root=tmp_path, token="x")
    df = pd.read_parquet(tmp_path / "etf_nav.parquet")
    assert set(df["ts_code"]) == {"510300.SH", "159915.SZ"}
    assert {"unit_nav", "accum_nav", "net_asset"} <= set(df.columns)


def test_resolve_index_code_fuzzy_match(monkeypatch):
    """_resolve_index_code should fuzzy-match free-text benchmark to index ts_code."""
    # clear module-level cache first
    etf_fund._INDEX_CACHE.clear()

    fake_index_basic = pd.DataFrame({
        "name": ["沪深300", "中证500", "上证50"],
        "ts_code": ["000300.SH", "000905.SH", "000016.SH"],
    })
    monkeypatch.setattr(
        etf_fund, "_fund_query",
        lambda token, api, **kw: fake_index_basic if api == "index_basic" else pd.DataFrame()
    )

    result = etf_fund._resolve_index_code("沪深300指数收益率", token="x")
    assert result == "000300.SH"

    # second call uses cached data (no re-call needed)
    result2 = etf_fund._resolve_index_code("中证500指数收益率", token="x")
    assert result2 == "000905.SH"

    # no match returns None
    result3 = etf_fund._resolve_index_code("纳斯达克100指数", token="x")
    assert result3 is None

    # cleanup
    etf_fund._INDEX_CACHE.clear()
