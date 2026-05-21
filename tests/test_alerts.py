"""Tests for v1.8.0 alert engine + watch loop."""
from __future__ import annotations
import datetime as dt
from pathlib import Path

import pytest

from financial_analyst.buddy.alerts import (
    AlertRule, AlertStore, evaluate, parse_pct, VALID_KINDS,
)


# ----- parse_pct ------------------------------------------------------------


def test_parse_pct():
    assert parse_pct("-0.30%") == pytest.approx(-0.30)
    assert parse_pct("5") == 5.0
    assert parse_pct(1.5) == 1.5
    assert parse_pct("") is None
    assert parse_pct(None) is None
    assert parse_pct("abc") is None


# ----- AlertRule.check ------------------------------------------------------


def test_price_below_fires():
    r = AlertRule(code="SH600519", kind="price_below", threshold=1200)
    assert r.check(price=1190, change_pct=None) is True
    assert r.check(price=1210, change_pct=None) is False


def test_price_above_fires():
    r = AlertRule(code="SH600519", kind="price_above", threshold=1300)
    assert r.check(price=1305, change_pct=None) is True
    assert r.check(price=1290, change_pct=None) is False


def test_pct_above_fires():
    r = AlertRule(code="X", kind="pct_above", threshold=5)
    assert r.check(price=None, change_pct=6.2) is True
    assert r.check(price=None, change_pct=3.0) is False


def test_pct_below_fires():
    r = AlertRule(code="X", kind="pct_below", threshold=-5)
    assert r.check(price=None, change_pct=-6.0) is True
    assert r.check(price=None, change_pct=-2.0) is False


def test_check_handles_none_quote():
    r = AlertRule(code="X", kind="price_below", threshold=100)
    assert r.check(price=None, change_pct=None) is False


def test_describe():
    r = AlertRule(code="SH600519", kind="price_below", threshold=1200, note="止损")
    d = r.describe()
    assert "SH600519" in d and "跌破" in d and "1200" in d and "止损" in d


def test_rule_id_is_composite():
    r = AlertRule(code="sh600519", kind="price_below", threshold=1)
    # code uppercased in store, not in raw rule — id reflects raw
    assert r.id == "sh600519:price_below"


# ----- AlertStore -----------------------------------------------------------


@pytest.fixture
def store(tmp_path):
    return AlertStore(path=tmp_path / "alerts.yaml")


def test_add_and_list(store):
    store.add("SH600519", "price_below", 1200, "止损")
    rules = store.list()
    assert len(rules) == 1
    assert rules[0].code == "SH600519"
    assert rules[0].threshold == 1200


def test_add_invalid_kind_raises(store):
    with pytest.raises(ValueError):
        store.add("X", "bogus", 1)


def test_add_same_code_kind_upserts(store):
    store.add("SH600519", "price_below", 1200)
    store.add("SH600519", "price_below", 1100)  # update threshold
    assert len(store) == 1
    assert store.list()[0].threshold == 1100


def test_different_kinds_coexist(store):
    store.add("SH600519", "price_below", 1200)
    store.add("SH600519", "price_above", 1400)
    assert len(store) == 2


def test_remove_by_full_id(store):
    store.add("SH600519", "price_below", 1200)
    assert store.remove("SH600519:price_below") is True
    assert len(store) == 0


def test_remove_by_code_removes_all_kinds(store):
    store.add("SH600519", "price_below", 1200)
    store.add("SH600519", "price_above", 1400)
    assert store.remove("SH600519") is True
    assert len(store) == 0


def test_remove_nonexistent(store):
    assert store.remove("NOPE") is False


def test_persistence_round_trip(tmp_path):
    p = tmp_path / "alerts.yaml"
    s1 = AlertStore(path=p)
    s1.add("SH600519", "price_below", 1200, "止损")
    # New store reads it back
    s2 = AlertStore(path=p)
    assert len(s2) == 1
    assert s2.list()[0].note == "止损"


def test_load_skips_invalid_entries(tmp_path):
    import yaml
    p = tmp_path / "alerts.yaml"
    p.write_text(yaml.safe_dump({"alerts": [
        {"code": "SH600519", "kind": "price_below", "threshold": 1200},
        {"code": "X", "kind": "bogus", "threshold": 1},      # bad kind
        {"code": "Y"},                                        # missing fields
    ]}), encoding="utf-8")
    s = AlertStore(path=p)
    assert len(s) == 1


# ----- evaluate -------------------------------------------------------------


def test_evaluate_fires_matching(store):
    store.add("SH600519", "price_below", 1200)

    def provider(code):
        return {"price": 1190, "changePercent": "-0.5%"}

    fired = evaluate(store, provider)
    assert len(fired) == 1
    rule, quote = fired[0]
    assert rule.code == "SH600519"
    assert quote["price"] == 1190
    # last_fired stamped
    assert store.list()[0].last_fired is not None


def test_evaluate_no_fire_when_condition_unmet(store):
    store.add("SH600519", "price_below", 1200)
    fired = evaluate(store, lambda code: {"price": 1250, "changePercent": "1%"})
    assert fired == []


def test_evaluate_respects_cooldown(store):
    store.add("SH600519", "price_below", 1200)
    # Mark as fired 5 minutes ago
    store.list()[0].last_fired = (
        dt.datetime.now() - dt.timedelta(minutes=5)
    ).strftime("%Y-%m-%d %H:%M:%S")
    fired = evaluate(store, lambda code: {"price": 1190}, cooldown_min=30)
    assert fired == []  # within cooldown


def test_evaluate_fires_after_cooldown(store):
    store.add("SH600519", "price_below", 1200)
    store.list()[0].last_fired = (
        dt.datetime.now() - dt.timedelta(minutes=40)
    ).strftime("%Y-%m-%d %H:%M:%S")
    fired = evaluate(store, lambda code: {"price": 1190}, cooldown_min=30)
    assert len(fired) == 1


def test_evaluate_handles_provider_failure(store):
    store.add("SH600519", "price_below", 1200)

    def boom(code):
        raise RuntimeError("network down")

    fired = evaluate(store, boom)
    assert fired == []  # no crash, no fire


def test_evaluate_handles_none_quote(store):
    store.add("SH600519", "price_below", 1200)
    fired = evaluate(store, lambda code: None)
    assert fired == []


def test_evaluate_pct_alert(store):
    store.add("X", "pct_below", -5)
    fired = evaluate(store, lambda code: {"price": 10, "changePercent": "-6.5%"})
    assert len(fired) == 1


# ----- buddy tool plumbing --------------------------------------------------


def test_alert_tools_registered():
    from financial_analyst.buddy.tools import TOOL_REGISTRY
    names = {t.name for t in TOOL_REGISTRY}
    assert "alert_add" in names
    assert "alert_list" in names
    assert "alert_remove" in names
    assert "realtime_quote" in names


def test_alert_add_tool(tmp_path, monkeypatch):
    # Redirect the store's default path (~/.financial-analyst/alerts.yaml)
    # to the tmp dir so the test doesn't touch the developer's real alerts.
    monkeypatch.setattr(
        "financial_analyst.buddy.alerts.Path.home", lambda: tmp_path
    )
    from financial_analyst.buddy.tools import _tool_alert_add, _tool_alert_list
    result = _tool_alert_add("SH600519", "price_below", 1200, "止损")
    assert not result.is_error
    assert "跌破" in result.content
    # And it persists — list sees it
    listed = _tool_alert_list()
    assert "SH600519" in listed.content


def test_alert_add_tool_rejects_bad_kind(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "financial_analyst.buddy.alerts.Path.home", lambda: tmp_path
    )
    from financial_analyst.buddy.tools import _tool_alert_add
    result = _tool_alert_add("SH600519", "bogus", 1200)
    assert result.is_error


# ----- watch loop (BuddyApp) ------------------------------------------------

import asyncio
from financial_analyst.buddy.app import BuddyApp


def test_watch_off_by_default():
    app = BuddyApp()
    assert app.watch_enabled is False


def test_watch_status_when_off(tmp_path, monkeypatch):
    monkeypatch.setattr("financial_analyst.buddy.alerts.Path.home", lambda: tmp_path)
    app = BuddyApp()
    app._handle_slash("/watch")
    assert "盯盘未开" in app.transcript_text()


def test_watch_on_without_loop_reports_error():
    """No running event loop (sync test) → graceful 'can't start' message."""
    app = BuddyApp()
    app._handle_slash("/watch on 3")
    # _start_watch returns False (no loop) → watch_enabled reset
    assert app.watch_enabled is False
    assert "无法启动盯盘" in app.transcript_text()


def test_watch_off_command():
    app = BuddyApp()
    app.watch_enabled = True
    app._handle_slash("/watch off")
    assert app.watch_enabled is False
    assert "盯盘已关" in app.transcript_text()


@pytest.mark.asyncio
async def test_watch_on_inside_loop_starts_task(tmp_path, monkeypatch):
    monkeypatch.setattr("financial_analyst.buddy.alerts.Path.home", lambda: tmp_path)
    app = BuddyApp()
    app._handle_slash("/watch on 1")
    assert app.watch_enabled is True
    assert app.watch_task is not None
    # interval parsed as 1 min = 60s
    assert app.watch_interval == 60
    # cleanup
    app._stop_watch()
    assert app.watch_enabled is False


@pytest.mark.asyncio
async def test_watch_on_parses_sources(tmp_path, monkeypatch):
    monkeypatch.setattr("financial_analyst.buddy.alerts.Path.home", lambda: tmp_path)
    app = BuddyApp()
    app._handle_slash("/watch on 5 ths-fund-flow,xueqiu-hot")
    assert app.watch_interval == 300
    assert app.watch_sources == "ths-fund-flow,xueqiu-hot"
    app._stop_watch()


def test_status_line_shows_watch_when_on():
    app = BuddyApp()
    app.watch_enabled = True
    app.watch_interval = 180
    text = app._get_status_ansi().value
    assert "盯盘" in text


def test_market_session_weekend():
    from financial_analyst.buddy.alerts import market_session
    # 2026-05-23 is a Saturday
    sat = dt.datetime(2026, 5, 23, 10, 0)
    assert market_session(sat) == "weekend"


def test_market_session_open_morning():
    from financial_analyst.buddy.alerts import market_session
    # 2026-05-21 is a Thursday
    assert market_session(dt.datetime(2026, 5, 21, 10, 0)) == "open"


def test_market_session_open_afternoon():
    from financial_analyst.buddy.alerts import market_session
    assert market_session(dt.datetime(2026, 5, 21, 14, 0)) == "open"


def test_market_session_lunch():
    from financial_analyst.buddy.alerts import market_session
    assert market_session(dt.datetime(2026, 5, 21, 12, 0)) == "lunch"


def test_market_session_closed_evening():
    from financial_analyst.buddy.alerts import market_session
    assert market_session(dt.datetime(2026, 5, 21, 16, 0)) == "closed"


def test_market_session_before_open():
    from financial_analyst.buddy.alerts import market_session
    assert market_session(dt.datetime(2026, 5, 21, 9, 0)) == "closed"


def test_is_trading_now():
    from financial_analyst.buddy.alerts import is_trading_now
    assert is_trading_now(dt.datetime(2026, 5, 21, 10, 0)) is True
    assert is_trading_now(dt.datetime(2026, 5, 23, 10, 0)) is False


def test_watch_status_line_shows_session(monkeypatch):
    from financial_analyst.buddy import alerts
    monkeypatch.setattr(alerts, "market_session", lambda now=None: "closed")
    app = BuddyApp()
    app.watch_enabled = True
    text = app._get_status_ansi().value
    assert "已收盘" in text


@pytest.mark.asyncio
async def test_eval_alerts_uses_store_and_provider(tmp_path, monkeypatch):
    """_eval_alerts wires AlertStore + XueqiuStockCollector together."""
    monkeypatch.setattr("financial_analyst.buddy.alerts.Path.home", lambda: tmp_path)
    # Seed an alert
    from financial_analyst.buddy.alerts import AlertStore
    AlertStore().add("SH600519", "price_below", 1200)
    # Stub the realtime quote provider to a triggering price
    monkeypatch.setattr(
        "financial_analyst.data.collectors.opencli.xueqiu_stock.XueqiuStockCollector.fetch",
        lambda self, code: {"price": 1190, "changePercent": "-1%", "market_status": "交易中"},
    )
    app = BuddyApp()
    fired = app._eval_alerts()
    assert len(fired) == 1
    assert fired[0][0].code == "SH600519"
