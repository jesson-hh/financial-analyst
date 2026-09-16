from datetime import date, datetime

import pandas as pd
from fastapi import FastAPI
from fastapi.testclient import TestClient

from financial_analyst.data import loader_factory
from financial_analyst.data.collectors import tencent_quote
from guanlan_v2.seats import api as seats_api
from guanlan_v2.seats import watcher


class _QuoteSource:
    asof = "20260814161400"

    def fetch(self, _codes):
        row = {
            "code": "SZ300750", "name": "宁德时代", "price": 393.93,
            "prevClose": 390.0, "open": 391.0, "high": 399.0, "low": 388.0,
            "change": 3.93, "changePercent": 1.01, "volume": 1234,
            "amount": 4567, "turnover_rate": 1.2, "vol_ratio": 0.8,
            "pe": 20.0, "pb": 3.0, "asof": self.asof,
        }
        return {"SZ300750": row, "300750": row}


class _Loader:
    def fetch_quote(self, *_args, **_kwargs):
        days = pd.date_range("2026-07-01", periods=32, freq="B")
        return pd.DataFrame({
            "trade_date": days,
            "open": [380.0] * len(days), "high": [400.0] * len(days),
            "low": [375.0] * len(days), "close": [390.0] * len(days),
            "volume": [1_000_000.0] * len(days),
        })


def _client(monkeypatch):
    monkeypatch.setattr(tencent_quote, "TencentQuoteCollector", _QuoteSource)
    monkeypatch.setattr(loader_factory, "get_default_loader", lambda: _Loader())
    app = FastAPI()
    app.include_router(seats_api.build_seats_router())
    return TestClient(app)


def test_quote_and_live_eval_never_call_after_close_snapshot_live(monkeypatch):
    _QuoteSource.asof = "20260814161400"
    monkeypatch.setattr(watcher, "_calendar_dates", lambda: {date(2026, 8, 14)})
    monkeypatch.setattr(
        watcher, "_market_now", lambda: datetime(2026, 8, 15, 10, 0))
    client = _client(monkeypatch)

    quote = client.get("/seats/quote", params={"code": "300750"}).json()
    live_eval = client.get(
        "/seats/live_eval", params={"code": "300750", "tf": "day"}).json()

    for payload in (quote, live_eval):
        assert payload["ok"] is True
        assert payload["fresh"] is False
        assert payload["market_open"] is False
        assert payload["market_phase"] == "non_trading_day"
        assert payload["quote_state"] == "closed"
        assert payload["newer_than_settled_bar"] is True


def test_quote_is_live_only_for_recent_in_session_provider_time(monkeypatch):
    _QuoteSource.asof = "20260814100000"
    monkeypatch.setattr(watcher, "_calendar_dates", lambda: {date(2026, 8, 14)})
    monkeypatch.setattr(
        watcher, "_market_now", lambda: datetime(2026, 8, 14, 10, 0, 20))
    client = _client(monkeypatch)

    payload = client.get("/seats/quote", params={"code": "300750"}).json()
    assert payload["source"] == "tencent_realtime_quote"
    assert payload["asof"] == "2026-08-14 10:00:00"
    assert payload["fresh"] is True
    assert payload["market_open"] is True
    assert payload["market_phase"] == "open"
    assert payload["quote_state"] == "live"
