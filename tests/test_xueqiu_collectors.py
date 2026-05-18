from unittest.mock import patch
from financial_analyst.data.collectors.opencli import (
    XueqiuCommentsCollector, XueqiuHotStockCollector, XueqiuEarningsCollector,
)


def test_comments_collector_normalizes_code():
    fake_items = [{"id": "1", "ts": "2026-05-18", "content": "hi", "likes": 5}]
    c = XueqiuCommentsCollector()
    with patch("financial_analyst.data.collectors.opencli.xueqiu_comments.run_opencli",
               return_value=fake_items) as m:
        result = c.fetch("SH600519")
        # Verify the short code was passed
        call_args = m.call_args[0]
        assert "600519" in call_args
    assert result == fake_items


def test_hot_stock_collector():
    fake_items = [{"rank": 1, "symbol": "600519", "name": "贵州茅台", "heat": 1000}]
    c = XueqiuHotStockCollector()
    with patch("financial_analyst.data.collectors.opencli.xueqiu_hot_stock.run_opencli",
               return_value=fake_items):
        result = c.fetch(limit=10)
    assert len(result) == 1


def test_earnings_collector_adds_code_field():
    """If opencli returns earnings entries without 'code' field, fetch() adds it."""
    fake_items = [{"report_date": "2026-08-15", "quarter": "Q2-2026"}]
    c = XueqiuEarningsCollector()
    with patch("financial_analyst.data.collectors.opencli.xueqiu_earnings.run_opencli",
               return_value=fake_items):
        result = c.fetch("SH600519")
    assert result[0]["code"] == "SH600519"
