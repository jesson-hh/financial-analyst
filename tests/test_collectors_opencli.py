import pytest
from pathlib import Path
from unittest.mock import patch
from financial_analyst.data.collectors.opencli import (
    EastmoneyKuaixunCollector, EastmoneyLonghuCollector,
    EastmoneyHoldersCollector, SinafinanceNewsCollector,
)


def test_kuaixun_collector_filters_by_code(tmp_path):
    fake_items = [
        {"time": "2026-05-18 14:00", "title": "茅台业绩", "summary": "...", "stocks": "1.600519"},
        {"time": "2026-05-18 15:00", "title": "央行新闻", "summary": "...", "stocks": ""},
    ]
    c = EastmoneyKuaixunCollector()
    with patch.object(c, "fetch", return_value=fake_items):
        written = c.collect(code="SH600519", target_dir=tmp_path)
    assert len(written) == 1   # one date file
    content = written[0].read_text(encoding="utf-8")
    assert "茅台业绩" in content
    assert "央行新闻" not in content


def test_kuaixun_collector_market_mode(tmp_path):
    """code='' → dump all to _market/"""
    fake_items = [
        {"time": "2026-05-18 14:00", "title": "茅台", "summary": "...", "stocks": "1.600519"},
        {"time": "2026-05-18 14:30", "title": "央行", "summary": "...", "stocks": ""},
    ]
    c = EastmoneyKuaixunCollector()
    with patch.object(c, "fetch", return_value=fake_items):
        written = c.collect(code="", target_dir=tmp_path)
    assert len(written) == 1
    assert (tmp_path / "_market").exists()


def test_longhu_collector_filters_by_code(tmp_path):
    fake_items = [
        {"tradeDate": "2026-05-18", "code": "000021", "name": "深科技",
         "closePrice": 37.03, "changeRate": 10.01,
         "buyAmt": 7.3e8, "sellAmt": 2.7e8, "netAmt": 4.6e8,
         "market": "深交所主板", "reason": "异常涨幅"},
        {"tradeDate": "2026-05-18", "code": "600519", "name": "贵州茅台",
         "closePrice": 1323, "changeRate": -1.0,
         "buyAmt": 1e8, "sellAmt": 1.2e8, "netAmt": -0.2e8,
         "market": "上交所主板", "reason": "异常波动"},
    ]
    c = EastmoneyLonghuCollector()
    with patch.object(c, "fetch", return_value=fake_items):
        written = c.collect(code="SZ000021", target_dir=tmp_path)
    assert len(written) == 1
    text = written[0].read_text(encoding="utf-8")
    assert "深科技" in text
    assert "贵州茅台" not in text


def test_holders_collector(tmp_path):
    fake_items = [
        {"rank": 1, "reportDate": "2026-03-31", "name": "茅台集团",
         "holdNum": 681282935, "floatRatio": 54.40, "change": "不变"},
        {"rank": 2, "reportDate": "2026-03-31", "name": "中央汇金",
         "holdNum": 10000000, "floatRatio": 0.8, "change": "+100000"},
    ]
    c = EastmoneyHoldersCollector()
    with patch.object(c, "fetch", return_value=fake_items):
        written = c.collect(code="SH600519", target_dir=tmp_path)
    assert len(written) == 1
    text = written[0].read_text(encoding="utf-8")
    assert "茅台集团" in text
    assert "54.40%" in text


def test_sinafinance_collector(tmp_path):
    fake_items = [
        {"time": "2026-05-18 22:45", "content": "五粮液着力打造三大百亿大单品", "views": "27.41万"},
        {"time": "2026-05-18 22:30", "content": "美联储官员表态", "views": "10万"},
    ]
    c = SinafinanceNewsCollector()
    with patch.object(c, "fetch", return_value=fake_items):
        written = c.collect(code="", target_dir=tmp_path)
    assert len(written) == 1   # one date file
    text = written[0].read_text(encoding="utf-8")
    assert "五粮液" in text
