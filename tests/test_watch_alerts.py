from __future__ import annotations

from datetime import datetime, timezone, timedelta

import pytest

from guanlan_v2.watch_alerts.core import AlertStore, WatchAlertEngine


TZ = timezone(timedelta(hours=8))


def test_rule_store_is_durable_and_defaults_disabled(tmp_path):
    store = AlertStore(tmp_path)
    rule = store.create_rule({
        "name": "宁德时代回撤线", "kind": "stock_drawdown_from_high_pct",
        "code": "300750", "threshold": -3,
    })
    assert rule["code"] == "SZ300750"
    assert rule["enabled"] is False
    assert AlertStore(tmp_path).get_rule(rule["id"])["name"] == "宁德时代回撤线"

    updated = store.update_rule(rule["id"], {"enabled": True})
    assert updated["enabled"] is True
    assert store.delete_rule(rule["id"])["id"] == rule["id"]
    assert store.list_rules() == []


def test_drop_threshold_rejects_positive_value(tmp_path):
    with pytest.raises(ValueError, match="负数"):
        AlertStore(tmp_path).create_rule({
            "name": "错误阈值", "kind": "stock_pct_below",
            "code": "SH600000", "threshold": 2,
        })


def test_stock_alert_fires_once_on_crossing(tmp_path):
    store = AlertStore(tmp_path)
    rule = store.create_rule({
        "name": "浦发跌幅", "kind": "stock_pct_below", "code": "SH600000",
        "threshold": -2, "enabled": True,
    })
    values = iter([-1.0, -2.5, -3.0])

    def quotes(_codes):
        pct = next(values)
        return {"SH600000": {"code": "SH600000", "name": "浦发银行", "price": 10,
                             "high": 10.5, "changePercent": pct, "asof": current[0].strftime("%Y%m%d%H%M%S")}}

    current = [datetime(2026, 8, 21, 10, 0, tzinfo=TZ)]
    engine = WatchAlertEngine(store, quote_fn=quotes)
    assert engine.tick(current[0])["events"] == 0
    current[0] = datetime(2026, 8, 21, 10, 1, tzinfo=TZ)
    assert engine.tick(current[0])["events"] == 1
    current[0] = datetime(2026, 8, 21, 10, 2, tzinfo=TZ)
    assert engine.tick(current[0])["events"] == 0
    events = store.list_events()
    assert len(events) == 1 and events[0]["rule_id"] == rule["id"]


def test_market_proxy_uses_window_and_down_ratio(tmp_path):
    store = AlertStore(tmp_path)
    store.create_rule({
        "name": "全盘跳水", "kind": "market_proxy_drop_window_pct",
        "threshold": -1.0, "window_minutes": 15, "down_ratio_min": 0.7,
        "sample_size": 20, "enabled": True,
    })
    codes = [f"SH{600000 + i:06d}" for i in range(20)]
    current = [datetime(2026, 8, 21, 10, 0, tzinfo=TZ)]
    multiplier = [1.0]

    def quotes(requested):
        return {c: {"code": c, "name": c, "price": 100 * multiplier[0], "high": 100,
                    "changePercent": 0, "asof": current[0].strftime("%Y%m%d%H%M%S")}
                for c in requested}

    engine = WatchAlertEngine(store, quote_fn=quotes, universe_fn=lambda n: codes[:n])
    assert engine.tick(current[0])["events"] == 0
    current[0] = datetime(2026, 8, 21, 10, 15, tzinfo=TZ)
    multiplier[0] = 0.98
    out = engine.tick(current[0])
    assert out["events"] == 1
    event = store.list_events(limit=1)[0]
    assert event["detail"]["down_ratio"] == 1.0


def test_closed_session_does_not_fetch_quotes(tmp_path):
    store = AlertStore(tmp_path)
    store.create_rule({"name": "休市不取数", "kind": "stock_price_below",
                       "code": "SH600000", "threshold": 10, "enabled": True})

    def fail(_codes):
        raise AssertionError("closed session must not fetch")

    out = WatchAlertEngine(store, quote_fn=fail).tick(datetime(2026, 8, 22, 16, 0, tzinfo=TZ))
    assert out["market_phase"] == "non_trading_day"
    assert out["events"] == 0


def test_hysteresis_and_cooldown_prevent_repeat_noise(tmp_path):
    store = AlertStore(tmp_path)
    store.create_rule({
        "name": "防抖线", "kind": "stock_pct_below", "code": "SH600000",
        "threshold": -2, "hysteresis": 0.5, "cooldown_minutes": 30, "enabled": True,
    })
    current = [datetime(2026, 8, 21, 10, 0, tzinfo=TZ)]
    pct = [-2.1]

    def quotes(_codes):
        return {"SH600000": {"code": "SH600000", "name": "浦发", "price": 10,
                             "high": 10, "changePercent": pct[0],
                             "asof": current[0].strftime("%Y%m%d%H%M%S")}}

    engine = WatchAlertEngine(store, quote_fn=quotes)
    assert engine.tick(current[0])["events"] == 1
    # 回到 -1.8 仍在 0.5% 回差带内，不重新武装。
    current[0] = datetime(2026, 8, 21, 10, 5, tzinfo=TZ); pct[0] = -1.8
    assert engine.tick(current[0])["events"] == 0
    # 越过 -1.5 才重新武装，但 30 分钟冷却内再次穿越仍不报警。
    current[0] = datetime(2026, 8, 21, 10, 10, tzinfo=TZ); pct[0] = -1.4
    assert engine.tick(current[0])["events"] == 0
    current[0] = datetime(2026, 8, 21, 10, 15, tzinfo=TZ); pct[0] = -2.2
    assert engine.tick(current[0])["events"] == 0

    # 冷却期结束后必须先越过回差带解除锁存，再次向下穿越才重新报警。
    # 这同时验收 positive trigger → hysteresis re-arm → cooldown → re-trigger
    # 的完整闭环，避免只测“不会重复”却从未证明“还能再次触发”。
    current[0] = datetime(2026, 8, 21, 10, 40, tzinfo=TZ); pct[0] = -1.4
    assert engine.tick(current[0])["events"] == 0
    current[0] = datetime(2026, 8, 21, 10, 41, tzinfo=TZ); pct[0] = -2.2
    assert engine.tick(current[0])["events"] == 1
    events = store.list_events()
    assert len(events) == 2
    assert events[0]["rule_id"] == events[1]["rule_id"]
