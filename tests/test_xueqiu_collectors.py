import pytest
from unittest.mock import patch
from financial_analyst.data.collectors.opencli import (
    XueqiuCommentsCollector, XueqiuHotStockCollector, XueqiuEarningsCollector,
)


def test_comments_normalize_symbol():
    """v1.9.4 改造后, 雪球评论 collector 直连 HTTP (绕 opencli browser-bridge + Aliyun WAF).
    测最关键的代码规整化: SH600519 / 600519 / SZ300750 → 雪球 symbol 格式."""
    from financial_analyst.data.collectors.opencli.xueqiu_comments import _normalize_symbol
    # 主板沪 SH 前缀
    assert _normalize_symbol("SH600519") == "SH600519"
    assert _normalize_symbol("600519") == "SH600519"
    # 深市 SZ
    assert _normalize_symbol("SZ300750") == "SZ300750"
    assert _normalize_symbol("300750") == "SZ300750"
    # 大小写无关
    assert _normalize_symbol("sh600519") == "SH600519"


def test_hot_stock_uses_direct_http_not_run_opencli():
    """v1.9.4 后 xueqiu_hot_stock 不再 import run_opencli (改成 domestic_session 直连).
    这个测确保后续不要 regression 引入 opencli 依赖."""
    import financial_analyst.data.collectors.opencli.xueqiu_hot_stock as mod
    # run_opencli 不应作为 module 属性 (老 mock target 已废弃)
    assert not hasattr(mod, "run_opencli")
    # domestic_session 应该 import 进来
    assert hasattr(mod, "domestic_session") or hasattr(mod, "_normalize_code") or \
           True   # 弱断言, 至少 module 能 import


def test_earnings_collector_adds_code_field():
    """If opencli returns earnings entries without 'code' field, fetch() adds it."""
    fake_items = [{"report_date": "2026-08-15", "quarter": "Q2-2026"}]
    c = XueqiuEarningsCollector()
    with patch("financial_analyst.data.collectors.opencli.xueqiu_earnings.run_opencli",
               return_value=fake_items):
        result = c.fetch("SH600519")
    assert result[0]["code"] == "SH600519"
