"""Task 3 — watch/triggers.py: 无组合触发适配 (synthesized Position + 新闻触发).

``WatchTrigger`` wraps the *pure* ``backtest.intraday.IntradayTrigger`` so the
盯盘 loop can evaluate a ``WatchItem`` (no portfolio) for key points:

* ``breakout_high`` / ``volume_surge`` — need no position; just the 5min bars.
* ``stop_break`` — needs a held position's ``stop_loss``; we synthesize a
  lightweight ``Position`` from ``WatchItem.stop_loss/avg_cost`` (``sellable_qty``
  > 0 so the risk channel can fire). Items WITHOUT a ``stop_loss`` pass
  ``position=None`` → stop_break is silently skipped (no doomed risk signal).
* ``news_trigger`` — a keyword hit on a headline synthesizes a
  ``kind="news"`` ``TriggerEvent`` (dedup is the loop's job).

The bar builders mirror ``tests/test_backtest_intraday.py`` so the synthesized
inputs match what the real engine feeds ``IntradayTrigger.check``.

``asyncio_mode=auto`` → these are plain sync tests (no async here).
"""
from __future__ import annotations

import pandas as pd

from financial_analyst.backtest.intraday import IntradayTriggerConfig, TriggerEvent
from financial_analyst.watch.models import WatchItem
from financial_analyst.watch.triggers import WatchTrigger, news_trigger


def _ts(hhmm: str, date: str = "2026-06-02") -> pd.Timestamp:
    return pd.Timestamp(f"{date} {hhmm}:00")


def _bars_df(rows):
    """rows = list of (trade_date_ts, o, h, l, c, vol)."""
    return pd.DataFrame(rows, columns=["trade_date", "open", "high", "low",
                                       "close", "vol"])


def _flat_then_breakout_bars():
    """6 flat bars ~10.0 then bar idx 6 高 jumps to 10.5 (> prefix max 10.06 *
    1.008) → a clean breakout on i==6 (>= min_bars_for_signal=5)."""
    return _bars_df([
        (_ts("09:35"), 10.0, 10.05, 9.98, 10.0, 1000),
        (_ts("09:40"), 10.0, 10.05, 9.97, 10.0, 1000),
        (_ts("09:45"), 10.0, 10.06, 9.98, 10.0, 1000),
        (_ts("09:50"), 10.0, 10.05, 9.99, 10.0, 1000),
        (_ts("09:55"), 10.0, 10.06, 9.98, 10.0, 1000),
        (_ts("10:00"), 10.0, 10.05, 9.99, 10.0, 1000),
        (_ts("10:05"), 10.05, 10.50, 10.0, 10.45, 5000),   # breakout bar (idx 6)
    ])


def _flat_then_drop_bars(stop_touch_low=9.3):
    """6 flat bars ~10 then bar idx 6 low dips to ``stop_touch_low`` (breaches a
    ~9.5 stop). No new high → never a breakout; only the risk channel matters."""
    return _bars_df([
        (_ts("09:35"), 10.0, 10.05, 9.98, 10.0, 1000),
        (_ts("09:40"), 10.0, 10.05, 9.97, 10.0, 1000),
        (_ts("09:45"), 10.0, 10.06, 9.98, 10.0, 1000),
        (_ts("09:50"), 10.0, 10.05, 9.99, 10.0, 1000),
        (_ts("09:55"), 10.0, 10.06, 9.98, 10.0, 1000),
        (_ts("10:00"), 10.0, 10.05, 9.99, 10.0, 1000),
        (_ts("10:05"), 9.8, 9.85, stop_touch_low, 9.4, 5000),   # break-down bar (idx 6)
    ])


# ==========================================================================
# 1 — breakout: no position needed
# ==========================================================================
def test_breakout_high_no_position():
    bars = _flat_then_breakout_bars()
    wt = WatchTrigger()
    ev = wt.check_item(WatchItem(code="SH600519"), bars, len(bars) - 1)
    assert ev is not None
    assert isinstance(ev, TriggerEvent)
    assert ev.kind == "breakout_high"
    assert ev.code == "SH600519"
    assert ev.is_risk is False
    # the triggering bar index is the prefix末行 (i)
    assert ev.bar_index == len(bars) - 1


# ==========================================================================
# 2 — stop_break: WatchItem.stop_loss synthesizes a Position
# ==========================================================================
def test_stop_break_with_stop_loss():
    bars = _flat_then_drop_bars(stop_touch_low=9.3)
    wt = WatchTrigger()
    # last bar low 9.3 <= stop 9.5 → risk channel fires (no real portfolio)
    ev = wt.check_item(WatchItem(code="SZ002594", avg_cost=10.0, stop_loss=9.5),
                       bars, len(bars) - 1)
    assert ev is not None
    assert ev.kind == "stop_break"
    assert ev.is_risk is True
    assert ev.code == "SZ002594"
    assert ev.metric <= 9.5   # metric = the breaching low


# ==========================================================================
# 3 — no stop_loss → stop_break skipped even on a break-down
# ==========================================================================
def test_no_stop_loss_skips_stop_break():
    bars = _flat_then_drop_bars(stop_touch_low=9.3)
    wt = WatchTrigger()
    # same drop, but the item has no stop_loss → position=None → no stop_break.
    # The drop also makes no new high, so no breakout either → None.
    ev = wt.check_item(WatchItem(code="SH600000"), bars, len(bars) - 1)
    assert ev is None


# ==========================================================================
# 4 — news_trigger: keyword hit synthesizes a kind="news" TriggerEvent
# ==========================================================================
def test_news_trigger_hit():
    headlines = [
        "公司公告:全资子公司签订重大合同",
        "今日无关行情综述",
    ]
    ev = news_trigger("SH600519", headlines)
    assert ev is not None
    assert isinstance(ev, TriggerEvent)
    assert ev.kind == "news"
    assert ev.code == "SH600519"
    assert ev.is_risk is False
    assert ev.bar_index == -1
    # detail carries the matched headline so the agent prompt can cite it
    assert "重大合同" in ev.detail


def test_news_trigger_no_hit_returns_none():
    headlines = ["今日大盘震荡", "板块轮动加快"]
    assert news_trigger("SH600519", headlines) is None


# ==========================================================================
# negative_event_trigger (B1) — tdx_f10 severity>=2 风险事件 (硬卖/禁建仓)
# ==========================================================================
from financial_analyst.watch.triggers import negative_event_trigger  # noqa: E402


def test_negative_event_fires_sev2():
    w = {"SH600052": {"severity": 2, "title": "股东减持计划", "event_date": "2026-05-23"}}
    ev = negative_event_trigger("SH600052", w)
    assert ev is not None and isinstance(ev, TriggerEvent)
    assert ev.kind == "negative_event"
    assert ev.is_risk is True
    assert ev.metric == 2.0
    assert ev.bar_index == -1
    assert "减持" in ev.detail


def test_negative_event_sev1_below_threshold():
    w = {"SH600000": {"severity": 1, "title": "风险提示", "event_date": "x"}}
    assert negative_event_trigger("SH600000", w) is None


def test_negative_event_missing_code_or_empty():
    w = {"SH600052": {"severity": 3, "title": "立案", "event_date": "x"}}
    assert negative_event_trigger("SH999999", w) is None
    assert negative_event_trigger("SH600052", {}) is None
    assert negative_event_trigger("SH600052", None) is None


def test_negative_event_custom_threshold():
    w = {"SH600000": {"severity": 2, "title": "减持", "event_date": "x"}}
    assert negative_event_trigger("SH600000", w, min_severity=3) is None
    assert negative_event_trigger("SH600000", w, min_severity=2) is not None


# ==========================================================================
# vol_regime_trigger (B2) — risk regimes → advisor (not hard rule)
# ==========================================================================
from financial_analyst.watch.triggers import vol_regime_trigger  # noqa: E402


def test_vol_regime_trigger_fires_super_distr():
    r = {"regime_label": "super_distr", "expected_spread_pp": -4.2, "detail": "派发"}
    ev = vol_regime_trigger("SH600000", r)
    assert ev is not None and isinstance(ev, TriggerEvent)
    assert ev.kind == "vol_regime"
    assert ev.is_risk is True
    assert ev.metric == -4.2
    assert "super_distr" in ev.detail


def test_vol_regime_trigger_fires_distr_and_tail():
    assert vol_regime_trigger("X", {"regime_label": "distr", "expected_spread_pp": -1.42, "detail": ""}) is not None
    assert vol_regime_trigger("X", {"regime_label": "tail_surge", "expected_spread_pp": -1.4, "detail": ""}) is not None


def test_vol_regime_trigger_skips_neutral_bounce_none():
    assert vol_regime_trigger("X", {"regime_label": "neutral", "expected_spread_pp": 0.0}) is None
    assert vol_regime_trigger("X", {"regime_label": "bounce", "expected_spread_pp": 0.94}) is None
    assert vol_regime_trigger("X", None) is None
