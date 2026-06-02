"""Tests for watch/feed.py — WatchFeed (Tencent 快照 + pytdx 5min, vol 归一).

全 stub, 不连网. 关键断言: pytdx vol(股) → 手 (÷100), 列对齐 IntradayTrigger 期望的
open/high/low/close/vol/trade_date, 网络失败容错返回 None/空 不抛.
"""
from __future__ import annotations

from unittest.mock import patch

import pandas as pd

from financial_analyst.watch.feed import WatchFeed


# ─────────────────── stub 数据 ───────────────────

_FAKE_QUOTES = {
    "SH600519": {
        "code": "SH600519", "name": "贵州茅台", "price": 1311.0,
        "changePercent": -0.30, "vol_ratio": 0.77, "high": 1323.52,
        "low": 1311.0, "amount": 511274.0, "volume": 38868.0,
    },
    "SZ002594": {
        "code": "SZ002594", "name": "比亚迪", "price": 80.0,
        "changePercent": 1.25, "vol_ratio": 1.30, "high": 81.0,
        "low": 79.5, "amount": 120000.0, "volume": 50000.0,
    },
}

# pytdx fetch_5min 返回: vol 是 *股*, datetime 'YYYY-MM-DD HH:MM'
_FAKE_5MIN = [
    {"datetime": "2026-06-02 09:35", "open": 1300.0, "high": 1305.0,
     "low": 1299.0, "close": 1304.0, "vol": 120000.0, "amount": 1.5e8},
    {"datetime": "2026-06-02 09:40", "open": 1304.0, "high": 1310.0,
     "low": 1303.0, "close": 1309.0, "vol": 80000.0, "amount": 1.0e8},
    {"datetime": "2026-06-02 09:45", "open": 1309.0, "high": 1320.0,
     "low": 1308.0, "close": 1318.0, "vol": 200000.0, "amount": 2.6e8},
]


# ─────────────────── snapshot ───────────────────


def test_snapshot_batches_codes():
    """snapshot(codes) 一次批量调 TencentQuoteCollector.fetch, 返回 {code: {...}}."""
    with patch("financial_analyst.watch.feed.TencentQuoteCollector.fetch",
               return_value=_FAKE_QUOTES) as mock_fetch:
        feed = WatchFeed()
        snap = feed.snapshot(["SH600519", "SZ002594"])
    mock_fetch.assert_called_once()
    assert snap["SH600519"]["price"] == 1311.0
    assert snap["SH600519"]["changePercent"] == -0.30
    assert snap["SZ002594"]["vol_ratio"] == 1.30


def test_snapshot_empty_codes():
    feed = WatchFeed()
    assert feed.snapshot([]) == {}


def test_snapshot_network_failure_returns_empty():
    """Tencent 抛错 → snapshot 返回 {} 不崩."""
    with patch("financial_analyst.watch.feed.TencentQuoteCollector.fetch",
               side_effect=RuntimeError("network down")):
        feed = WatchFeed()
        snap = feed.snapshot(["SH600519"])
    assert snap == {}


# ─────────────────── bars5 (vol ÷100) ───────────────────


def test_bars5_returns_dataframe_with_expected_columns():
    """bars5 返回 DataFrame, 列含 open/high/low/close/vol/trade_date."""
    feed = WatchFeed()
    with patch("financial_analyst.watch.feed.fetch_5min", return_value=_FAKE_5MIN):
        df = feed.bars5("SH600519")
    assert isinstance(df, pd.DataFrame)
    for col in ("open", "high", "low", "close", "vol", "trade_date"):
        assert col in df.columns, f"missing column {col}"
    assert len(df) == 3


def test_bars5_vol_converted_to_lots():
    """⚠ 核心断言: pytdx vol(股) → 手, 必须 ÷100."""
    feed = WatchFeed()
    with patch("financial_analyst.watch.feed.fetch_5min", return_value=_FAKE_5MIN):
        df = feed.bars5("SH600519")
    # raw 股: 120000 / 80000 / 200000 → 手: 1200 / 800 / 2000
    assert df["vol"].tolist() == [1200.0, 800.0, 2000.0]


def test_bars5_trade_date_from_datetime():
    """trade_date 列来自 bar 的 datetime 字段."""
    feed = WatchFeed()
    with patch("financial_analyst.watch.feed.fetch_5min", return_value=_FAKE_5MIN):
        df = feed.bars5("SH600519")
    assert str(df["trade_date"].iloc[0]) == "2026-06-02 09:35"
    assert str(df["trade_date"].iloc[-1]) == "2026-06-02 09:45"


def test_bars5_ohlc_preserved():
    """open/high/low/close 原样保留 (不缩放)."""
    feed = WatchFeed()
    with patch("financial_analyst.watch.feed.fetch_5min", return_value=_FAKE_5MIN):
        df = feed.bars5("SH600519")
    assert df["close"].tolist() == [1304.0, 1309.0, 1318.0]
    assert df["high"].iloc[-1] == 1320.0


def test_bars5_empty_bars_returns_empty_df():
    """pytdx 返回空 (退市/拉空) → bars5 返回空 DataFrame (列齐), 不崩."""
    feed = WatchFeed()
    with patch("financial_analyst.watch.feed.fetch_5min", return_value=[]):
        df = feed.bars5("SH600519")
    assert isinstance(df, pd.DataFrame)
    assert df.empty
    for col in ("open", "high", "low", "close", "vol", "trade_date"):
        assert col in df.columns


def test_bars5_network_failure_returns_empty_df():
    """fetch_5min 抛错 → bars5 返回空 DataFrame 不崩."""
    feed = WatchFeed()
    with patch("financial_analyst.watch.feed.fetch_5min",
               side_effect=RuntimeError("tdx host down")):
        df = feed.bars5("SH600519")
    assert isinstance(df, pd.DataFrame)
    assert df.empty


def test_bars5_columns_feed_intraday_trigger():
    """bars5 产出的列名/类型可直接喂 IntradayTrigger.check (不报 KeyError)."""
    from financial_analyst.backtest.intraday import (
        IntradayTrigger, IntradayTriggerConfig,
    )
    # 末根 high=1330 明确突破前高 1310 的 0.8% 阈值 (1310*1.008=1320.48)
    breakout_bars = [
        {"datetime": "2026-06-02 09:35", "open": 1300.0, "high": 1305.0,
         "low": 1299.0, "close": 1304.0, "vol": 120000.0, "amount": 1.5e8},
        {"datetime": "2026-06-02 09:40", "open": 1304.0, "high": 1310.0,
         "low": 1303.0, "close": 1309.0, "vol": 80000.0, "amount": 1.0e8},
        {"datetime": "2026-06-02 09:45", "open": 1309.0, "high": 1330.0,
         "low": 1308.0, "close": 1328.0, "vol": 200000.0, "amount": 2.6e8},
    ]
    feed = WatchFeed()
    with patch("financial_analyst.watch.feed.fetch_5min", return_value=breakout_bars):
        df = feed.bars5("SH600519")
    trig = IntradayTrigger(IntradayTriggerConfig(enabled=True, min_bars_for_signal=1))
    # 末根 (index 2) 高点 1330 突破前高 1310 > 0.8% → 应识别 breakout_high, 不报错
    ev = trig.check("SH600519", df, position=None, sellable_qty=0, i=len(df) - 1)
    assert ev is not None
    assert ev.kind == "breakout_high"
