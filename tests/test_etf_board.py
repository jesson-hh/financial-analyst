import pandas as pd
import financial_analyst.data.etf_board as eb


def _fake_sina_df():
    return pd.DataFrame({
        "代码": ["sz159995", "sh510300", "sh510999"],
        "名称": ["芯片ETF华夏", "沪深300ETF华泰柏瑞", "停牌ETF"],
        "最新价": [2.357, 4.923, 0.0],          # last row suspended (price 0) -> dropped
        "涨跌幅": [-5.379, -0.18, 0.0],
        "成交额": [1531415752, 324345000, 0],
        "成交量": [634511783, 65000000, 0],
    })


def test_maps_and_filters(monkeypatch):
    import types
    fake_ak = types.SimpleNamespace(fund_etf_category_sina=lambda symbol: _fake_sina_df())
    monkeypatch.setitem(__import__("sys").modules, "akshare", fake_ak)
    rows = eb.etf_market_board()
    assert len(rows) == 2                     # suspended (price 0) dropped
    assert rows[0]["code"] == "SZ159995"      # sorted by amount desc -> 芯片ETF first
    assert rows[0]["name"] == "芯片ETF华夏"
    assert rows[0]["price"] == 2.357
    assert rows[0]["change_pct"] == -5.379
    assert rows[0]["amount"] == 1531415752.0
    assert rows[0]["volume"] == 634511783.0
    assert rows[1]["code"] == "SH510300"      # code uppercased to qlib form


def test_no_proxy_restored(monkeypatch):
    import os, types
    monkeypatch.delenv("NO_PROXY", raising=False)
    fake_ak = types.SimpleNamespace(fund_etf_category_sina=lambda symbol: _fake_sina_df())
    monkeypatch.setitem(__import__("sys").modules, "akshare", fake_ak)
    eb.etf_market_board()
    assert os.environ.get("NO_PROXY") is None  # restored, not leaked globally
