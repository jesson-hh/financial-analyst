"""Tests for watch/news.py — KuaixunNewsProvider (7x24 快讯 → per-code headlines).

全 stub, 不连网/不跑 opencli. 关键断言:
* 按 6 位代码在 ``stocks`` 字段过滤; 按名称在 title/summary 过滤;
* 一个刷新窗口内只拉一次 feed (TTL 缓存, N 个 code 共享一次 fetch);
* collector 抛错 / 禁用 → [] 不崩, 禁用时根本不 fetch.
"""
from __future__ import annotations

from typing import Any, Dict, List

from financial_analyst.watch.news import KuaixunNewsProvider, _symbol_of


class _FakeCollector:
    def __init__(self, items: List[Dict[str, Any]], boom: bool = False) -> None:
        self._items = items
        self._boom = boom
        self.fetch_calls = 0

    def fetch(self, limit: int = 200):
        self.fetch_calls += 1
        if self._boom:
            raise RuntimeError("opencli down")
        return self._items


_FEED = [
    {"time": "2026-06-02 10:01", "title": "贵州茅台拟回购股份", "summary": "公告", "stocks": "1.600519"},
    {"time": "2026-06-02 10:02", "title": "某公司中标重大合同", "summary": "比亚迪参与", "stocks": "0.002000"},
    {"time": "2026-06-02 10:03", "title": "大盘震荡", "summary": "", "stocks": ""},
]


def test_symbol_extraction():
    assert _symbol_of("SH600519") == "600519"
    assert _symbol_of("600519.SH") == "600519"
    assert _symbol_of("600519") == "600519"
    assert _symbol_of("") == ""


def test_filter_by_symbol_in_stocks_field():
    p = KuaixunNewsProvider(collector=_FakeCollector(_FEED), enabled=True)
    assert p("SH600519") == ["贵州茅台拟回购股份"]


def test_filter_by_name_in_title_or_summary():
    # symbol 002594 is in no stocks field; the name 比亚迪 appears in a summary.
    p = KuaixunNewsProvider(collector=_FakeCollector(_FEED), enabled=True,
                            names={"SZ002594": "比亚迪"})
    assert "某公司中标重大合同" in p("SZ002594")


def test_no_match_returns_empty():
    p = KuaixunNewsProvider(collector=_FakeCollector(_FEED), enabled=True)
    assert p("SH601318") == []


def test_feed_cached_within_refresh_window():
    coll = _FakeCollector(_FEED)
    p = KuaixunNewsProvider(collector=coll, enabled=True, refresh_seconds=9999)
    p("SH600519"); p("SZ002594"); p("SH601318")
    assert coll.fetch_calls == 1          # one pull shared across 3 codes


def test_collector_failure_returns_empty_no_raise():
    p = KuaixunNewsProvider(collector=_FakeCollector(_FEED, boom=True), enabled=True)
    assert p("SH600519") == []            # network/opencli failure → [] not exception


def test_disabled_provider_skips_fetch():
    coll = _FakeCollector(_FEED)
    p = KuaixunNewsProvider(collector=coll, enabled=False)
    assert p("SH600519") == []
    assert coll.fetch_calls == 0          # disabled → never touches the网


def test_max_headlines_cap():
    items = [{"time": "t", "title": f"利好{i}", "summary": "", "stocks": "1.600519"}
             for i in range(20)]
    p = KuaixunNewsProvider(collector=_FakeCollector(items), enabled=True, max_headlines=3)
    assert len(p("SH600519")) == 3


def test_call_is_headlines_alias():
    p = KuaixunNewsProvider(collector=_FakeCollector(_FEED), enabled=True)
    assert p("SH600519") == p.headlines("SH600519")
