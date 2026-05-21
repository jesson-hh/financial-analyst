"""Tests for v1.9.0 stock_brief structured output (desktop UI bridge)."""
from __future__ import annotations
from unittest.mock import patch

from financial_analyst.buddy.tools import normalize_code, _pct_to_num, brief_data


# ----- code normalization ---------------------------------------------------


def test_normalize_bare_6digit():
    assert normalize_code("300750") == "SZ300750"
    assert normalize_code("600519") == "SH600519"
    assert normalize_code("002594") == "SZ002594"
    assert normalize_code("830799") == "BJ830799"


def test_normalize_already_prefixed():
    assert normalize_code("SH600519") == "SH600519"
    assert normalize_code("sz300750") == "SZ300750"


def test_normalize_suffix_form():
    assert normalize_code("300750.SZ") == "SZ300750"
    assert normalize_code("600519.SH") == "SH600519"


def test_pct_to_num():
    assert _pct_to_num("-0.30%") == -0.30
    assert _pct_to_num("2.21%") == 2.21
    assert _pct_to_num(1.5) == 1.5
    assert _pct_to_num("") is None
    assert _pct_to_num(None) is None


# ----- brief_data structured fields -----------------------------------------


def test_brief_data_has_all_card_fields():
    """Even with every source failing, the dict must have the full schema
    so the UI card never KeyErrors."""
    with patch("financial_analyst.data.collectors.opencli.xueqiu_stock."
               "XueqiuStockCollector.fetch", side_effect=Exception("no cookie")):
        d = brief_data("300750")
    for field in ("code", "name", "market", "industry", "mc", "price",
                  "change", "deltaPct", "vol_ratio", "turn", "amp",
                  "pe", "pb", "roe", "main_in", "prev_main_in",
                  "market_status", "xq_bull"):
        assert field in d
    # code normalized, market derived from prefix
    assert d["code"] == "SZ300750"
    assert d["market"] == "深市"


def test_brief_data_fills_from_realtime():
    fake_q = {
        "name": "宁德时代", "price": 325.1, "change": 7.04,
        "changePercent": "+2.21%", "turnover_rate": "2.18%",
        "amplitude": "3.84%", "marketCap": "14206亿", "market_status": "交易中",
    }
    with patch("financial_analyst.data.collectors.opencli.xueqiu_stock."
               "XueqiuStockCollector.fetch", return_value=fake_q), \
         patch("financial_analyst.buddy.tools._tool_industry_show",
               side_effect=Exception("skip")), \
         patch("financial_analyst.data.loader_factory.get_default_loader",
               side_effect=Exception("skip")):
        d = brief_data("300750")
    assert d["name"] == "宁德时代"
    assert d["price"] == 325.1
    assert d["deltaPct"] == 2.21
    assert d["turn"] == "2.18%"
    assert d["market_status"] == "交易中"


def test_stock_brief_attaches_structured_side_effect():
    """_tool_stock_brief must put the dict in ToolResult.side_effect['brief']."""
    from financial_analyst.buddy import tools
    fake = {"code": "SZ300750", "name": "宁德时代", "price": 325.1}
    with patch.object(tools, "brief_data", return_value=fake), \
         patch.object(tools, "_tool_ask_quote", side_effect=Exception("skip")), \
         patch.object(tools, "_tool_industry_show", side_effect=Exception("skip")), \
         patch.object(tools, "_tool_chain_for", side_effect=Exception("skip")), \
         patch.object(tools, "_tool_stocks_show", side_effect=Exception("skip")), \
         patch("financial_analyst.data.news_db.NewsDB", side_effect=Exception("skip")):
        result = tools._tool_stock_brief("300750")
    assert result.side_effect is not None
    assert result.side_effect["brief"]["name"] == "宁德时代"
