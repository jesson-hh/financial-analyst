"""Tests for batch 2 collectors: watchlist, groups, fund-snapshot, fund-holdings.

The 蛋卷 (danjuanfunds.com) endpoints need a separate cookie session that
most CI envs won't have. We mock run_opencli to verify the data flow,
schema, and graceful error paths.
"""
from __future__ import annotations
import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from financial_analyst.data.collectors.opencli.xueqiu_watchlist import (
    XueqiuWatchlistCollector, XueqiuGroupsCollector,
)
from financial_analyst.data.collectors.opencli.xueqiu_fund import (
    XueqiuFundSnapshotCollector, XueqiuFundHoldingsCollector,
)
from financial_analyst.data.news_db import NewsDB


@pytest.fixture
def tmp_db():
    with tempfile.TemporaryDirectory() as d:
        db = NewsDB(path=Path(d) / "test.sqlite")
        yield db
        db.close()


# ----- watchlist -------------------------------------------------------------


def test_watchlist_collector_passes_pid_and_limit():
    with patch("financial_analyst.data.collectors.opencli.xueqiu_watchlist.run_opencli",
               return_value=[]) as mock:
        XueqiuWatchlistCollector().fetch(pid="-5", limit=50)
    args = mock.call_args.args
    assert "watchlist" in args
    assert "-5" in args
    assert "50" in args


def test_watchlist_collector_returns_empty_list_on_none():
    with patch("financial_analyst.data.collectors.opencli.xueqiu_watchlist.run_opencli",
               return_value=None):
        assert XueqiuWatchlistCollector().fetch() == []


def test_upsert_watchlist_then_query(tmp_db):
    items = [
        {"symbol": "SH600519", "name": "贵州茅台", "price": 1280.0,
         "changePercent": -0.5, "url": "https://xueqiu.com/S/SH600519"},
        {"symbol": "00700", "name": "腾讯控股", "price": 350.0,
         "changePercent": 1.2, "url": "https://xueqiu.com/S/00700"},
    ]
    n = tmp_db.upsert_watchlist(items, group_pid="-1")
    assert n == 2
    rows = tmp_db.query_watchlist(group_pid="-1")
    assert len(rows) == 2
    syms = {r["symbol"] for r in rows}
    assert syms == {"SH600519", "00700"}


def test_upsert_watchlist_dedup_by_pk(tmp_db):
    """Same snapshot_date + group_pid + symbol = REPLACE, not duplicate."""
    item = [{"symbol": "SH600519", "name": "茅台", "price": 1280}]
    tmp_db.upsert_watchlist(item, group_pid="-5")
    tmp_db.upsert_watchlist(item, group_pid="-5")
    rows = tmp_db.query_watchlist(group_pid="-5")
    assert len(rows) == 1


def test_query_watchlist_uses_latest_snapshot_when_none_given(tmp_db):
    """Don't accidentally fall through to an empty result set."""
    tmp_db.upsert_watchlist([{"symbol": "SH600519"}], group_pid="-1",
                            snapshot_date="2026-05-21")
    rows = tmp_db.query_watchlist(group_pid="-1")
    assert len(rows) == 1
    assert rows[0]["snapshot_date"] == "2026-05-21"


# ----- groups ----------------------------------------------------------------


def test_groups_collector():
    fake = [{"pid": "-1", "name": "全部", "count": 6},
            {"pid": "-5", "name": "沪深", "count": 4}]
    with patch("financial_analyst.data.collectors.opencli.xueqiu_watchlist.run_opencli",
               return_value=fake):
        out = XueqiuGroupsCollector().fetch()
    assert out == fake


def test_upsert_and_query_groups(tmp_db):
    items = [
        {"pid": "-1", "name": "全部", "count": 6},
        {"pid": "-5", "name": "沪深", "count": 4},
        {"pid": "-7", "name": "港股", "count": 2},
    ]
    n = tmp_db.upsert_groups(items)
    assert n == 3
    rows = tmp_db.query_groups()
    pids = {r["pid"]: r["count"] for r in rows}
    assert pids["-1"] == 6
    assert pids["-5"] == 4


# ----- fund-snapshot / fund-holdings ----------------------------------------


def test_fund_snapshot_collector_calls_opencli():
    with patch("financial_analyst.data.collectors.opencli.xueqiu_fund.run_opencli",
               return_value=[{"accountId": "a1", "totalAssets": 12345}]) as mock:
        out = XueqiuFundSnapshotCollector().fetch()
    assert "fund-snapshot" in mock.call_args.args
    assert out == [{"accountId": "a1", "totalAssets": 12345}]


def test_fund_snapshot_handles_dict_envelope():
    """opencli may return {accounts: [...]} on some endpoints."""
    fake = {"accounts": [{"accountId": "a1"}]}
    with patch("financial_analyst.data.collectors.opencli.xueqiu_fund.run_opencli",
               return_value=fake):
        out = XueqiuFundSnapshotCollector().fetch()
    assert out == [{"accountId": "a1"}]


def test_fund_holdings_collector_passes_account_filter():
    with patch("financial_analyst.data.collectors.opencli.xueqiu_fund.run_opencli",
               return_value=[]) as mock:
        XueqiuFundHoldingsCollector().fetch(account="主账户")
    args = mock.call_args.args
    assert "--account" in args
    assert "主账户" in args


def test_upsert_fund_snapshot_and_query(tmp_db):
    accounts = [
        {"accountId": "a1", "accountName": "主账户",
         "totalAssets": 50000.0, "availableCash": 1000.0,
         "dailyGain": 250.0, "holdGain": 8000.0},
    ]
    tmp_db.upsert_fund_snapshot(accounts)
    rows = tmp_db.query_fund_snapshot()
    assert len(rows) == 1
    assert rows[0]["total_assets"] == 50000.0


def test_upsert_fund_holdings_and_query(tmp_db):
    holdings = [
        {"accountName": "主账户", "fdCode": "110011",
         "fdName": "易方达中小盘", "marketValue": 30000.0,
         "volume": 5000.0, "dailyGain": 150.0,
         "holdGain": 3000.0, "holdGainRate": 0.10, "marketPercent": 0.60},
        {"accountName": "主账户", "fdCode": "163406",
         "fdName": "兴全合润", "marketValue": 20000.0,
         "volume": 4000.0, "dailyGain": 100.0,
         "holdGain": 2000.0, "holdGainRate": 0.10, "marketPercent": 0.40},
    ]
    tmp_db.upsert_fund_holdings(holdings)
    rows = tmp_db.query_fund_holdings()
    # Sorted by market_value DESC
    assert rows[0]["fd_code"] == "110011"
    assert rows[1]["fd_code"] == "163406"


def test_query_fund_holdings_filter_by_account(tmp_db):
    tmp_db.upsert_fund_holdings([
        {"accountName": "a", "fdCode": "1", "marketValue": 100},
        {"accountName": "b", "fdCode": "2", "marketValue": 200},
    ])
    rows = tmp_db.query_fund_holdings(account_name="a")
    assert len(rows) == 1
    assert rows[0]["fd_code"] == "1"


# ----- buddy-tool plumbing ---------------------------------------------------


def test_buddy_tool_registry_has_new_tools():
    from financial_analyst.buddy.tools import TOOL_REGISTRY
    names = {t.name for t in TOOL_REGISTRY}
    assert "watchlist_show" in names
    assert "fund_snapshot" in names
    assert "fund_holdings" in names


def test_watchlist_show_tool_handles_empty_cache(tmp_db, monkeypatch):
    """When no snapshot exists, the tool should give a helpful hint, not crash."""
    # NewsDB is imported inside the tool function — patch its module path.
    monkeypatch.setattr(
        "financial_analyst.data.news_db.NewsDB",
        lambda *a, **kw: _NoCloseDB(tmp_db),
    )
    from financial_analyst.buddy.tools import _tool_watchlist_show
    result = _tool_watchlist_show(pid="-1")
    # Should not raise; helpful message instead.
    assert "No watchlist data" in result.content or "refresh" in result.content.lower()


def test_fund_tool_graceful_when_collector_fails(tmp_db, monkeypatch):
    """When refresh=True but opencli errors (no login), tool returns
    is_error=True with a hint, not an uncaught exception."""

    def boom(*a, **kw):
        raise RuntimeError("opencli exit 1: No fund accounts found")

    monkeypatch.setattr(
        "financial_analyst.data.collectors.opencli.xueqiu_fund.run_opencli",
        boom,
    )
    monkeypatch.setattr(
        "financial_analyst.data.news_db.NewsDB",
        lambda *a, **kw: _NoCloseDB(tmp_db),
    )
    from financial_analyst.buddy.tools import _tool_fund_snapshot
    result = _tool_fund_snapshot(refresh=True)
    assert result.is_error
    assert "danjuanfunds" in result.content.lower() or "login" in result.content.lower() or "蛋卷" in result.content


class _NoCloseDB:
    """Wrap a NewsDB fixture so the tool's .close() call is a no-op
    (the fixture teardown owns lifecycle)."""
    def __init__(self, real):
        self._real = real
    def __getattr__(self, name):
        return getattr(self._real, name)
    def close(self):
        pass
