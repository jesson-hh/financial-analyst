"""Historical 5-minute condition orders must never touch today's live feed."""
from __future__ import annotations

import json

import pandas as pd
from fastapi import FastAPI
from fastapi.testclient import TestClient

from guanlan_v2.seats import api as seats_api
from guanlan_v2.seats.realtime_store import RealtimeBarStore


class _LLM:
    provider = "deepseek"
    model = "deepseek-v4-flash"
    calls = 0

    @classmethod
    def for_agent(cls, _name):
        return cls()

    def with_overrides(self, **_kwargs):
        return self

    async def chat(self, *_args, **_kwargs):
        type(self).calls += 1
        content = json.dumps({
            "side": "观望", "triggers": [], "logic": "AND",
            "stop": None, "take": None, "note": "PIT", "validity": "今日有效",
        }, ensure_ascii=False)
        return {"choices": [{"message": {"content": content}}]}


def _client(monkeypatch, store, loader):
    import financial_analyst.data.loader_factory as loader_factory
    import financial_analyst.llm.client as llm_module
    import financial_analyst.data.collectors.tencent_quote as tq
    import financial_analyst.watch.feed as feed_module

    def _no_live(*_args, **_kwargs):
        raise AssertionError("historical PIT must not touch live quote/feed")

    monkeypatch.setattr(seats_api, "_realtime_store", lambda: store)
    monkeypatch.setattr(seats_api, "_DEC_LOG", store.path.parent / "decisions.jsonl")
    monkeypatch.setattr(loader_factory, "get_default_loader", lambda: loader)
    monkeypatch.setattr(llm_module, "LLMClient", _LLM)
    monkeypatch.setattr(tq.TencentQuoteCollector, "fetch", _no_live)
    monkeypatch.setattr(feed_module, "WatchFeed", _no_live)
    app = FastAPI()
    app.include_router(seats_api.build_seats_router())
    return TestClient(app)


def _row(ts, close, *, volume=1000.0):
    return {"trade_date": ts, "open": close - 0.1, "high": close + 0.2,
            "low": close - 0.2, "close": close, "vol": volume,
            "is_final": True}


def test_historical_5min_order_uses_durable_asof_and_ignores_future_bar(
        tmp_path, monkeypatch):
    store = RealtimeBarStore(tmp_path / "bars.sqlite3")
    store.ingest("600519", "5min", [
        _row("2026-08-14 10:00", 10.0),
        _row("2026-08-14 10:30", 10.5),
        _row("2026-08-14 10:35", 99.0),
    ], pulled_at="2026-08-15T08:00:00Z")

    class _NoLoader:
        def fetch_quote(self, *_args, **_kwargs):
            raise AssertionError("durable snapshot should win")

    _LLM.calls = 0
    response = _client(monkeypatch, store, _NoLoader()).get(
        "/seats/order", params={"code": "600519", "tf": "5min",
                                  "date": "2026-08-14 10:30"})
    body = response.json()
    assert body["ok"] is True and body["pit"] is True
    assert body["asof"].startswith("2026-08-14 10:30")
    assert body["ctx"]["price"] == 10.5 and body["ctx"]["high"] == 10.7
    assert _LLM.calls == 1


def test_historical_5min_loader_is_sorted_cleaned_and_date_only_includes_close(
        tmp_path, monkeypatch):
    store = RealtimeBarStore(tmp_path / "bars.sqlite3")

    class _Loader:
        def fetch_quote(self, *_args, **_kwargs):
            return pd.DataFrame([
                _row("2026-08-15 09:35", 88.0),  # future, must be cut
                _row("2026-08-14 15:00", 11.0),
                _row("2026-08-14 14:55", float("nan")),
                _row("2026-08-14 14:50", 10.8),
            ])

    _LLM.calls = 0
    body = _client(monkeypatch, store, _Loader()).get(
        "/seats/order", params={"code": "600519", "tf": "5min",
                                  "date": "2026-08-14"}).json()
    assert body["ok"] is True
    assert body["asof"].startswith("2026-08-14 15:00")
    assert body["ctx"]["price"] == 11.0 and _LLM.calls == 1


def test_historical_5min_missing_evidence_fails_before_llm(tmp_path, monkeypatch):
    store = RealtimeBarStore(tmp_path / "bars.sqlite3")

    class _Loader:
        def fetch_quote(self, *_args, **_kwargs):
            return None

    _LLM.calls = 0
    body = _client(monkeypatch, store, _Loader()).get(
        "/seats/order", params={"code": "600519", "tf": "5min",
                                  "date": "2026-08-14 10:30"}).json()
    assert body["ok"] is False
    assert body["reason"] == "historical_5min_evidence_missing"
    assert _LLM.calls == 0


def test_live_5min_order_uses_and_seals_durable_minute_evidence(
        tmp_path, monkeypatch):
    """Live condition-order Agent shares the replayable realtime evidence path."""
    import financial_analyst.data.collectors.tencent_quote as tq
    import financial_analyst.llm.client as llm_module
    import financial_analyst.watch.feed as feed_module

    store = RealtimeBarStore(tmp_path / "live-bars.sqlite3")
    now = pd.Timestamp.now().floor("min")
    rows = [
        _row(now - pd.Timedelta(minutes=5 * (19 - i)), 10.0 + i * 0.05)
        for i in range(20)
    ]

    class _Feed:
        def bars5(self, _code, _n):
            return pd.DataFrame(rows)

        def close(self):
            pass

    monkeypatch.setattr(seats_api, "_realtime_store", lambda: store)
    monkeypatch.setattr(seats_api, "_DEC_LOG", tmp_path / "decisions.jsonl")
    monkeypatch.setattr(llm_module, "LLMClient", _LLM)
    monkeypatch.setattr(feed_module, "WatchFeed", _Feed)
    monkeypatch.setattr(
        tq.TencentQuoteCollector, "fetch",
        lambda _self, _codes: {"SH600519": {"name": "贵州茅台"}},
    )
    # Session-clock boundaries are covered independently; this receipt test can
    # run at any host time without manufacturing an exchange-session timestamp.
    monkeypatch.setattr(
        seats_api, "_watcher_minute_evidence_fresh", lambda *_args: True)
    monkeypatch.setattr(
        seats_api, "_ctx5_sync",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("live order must not bypass durable evidence")),
    )

    app = FastAPI()
    app.include_router(seats_api.build_seats_router())
    _LLM.calls = 0
    body = TestClient(app).get(
        "/seats/order", params={"code": "600519", "tf": "5min"}).json()

    assert body["ok"] is True and body["pit"] is False
    assert body["evidence_source"] == "pytdx+realtime_store"
    assert body["data_ref"].startswith("seats-realtime://SH600519/5min?")
    assert body["data_digest"] and body["ctx"]["price"] == rows[-1]["close"]
    assert body["asof"] == str(now)[:16]
    receipt = store.receipt(body["data_digest"])
    assert receipt is not None and receipt["data_ref"] == body["data_ref"]
    assert _LLM.calls == 1

    record = json.loads(
        (tmp_path / "decisions.jsonl").read_text(encoding="utf-8").splitlines()[-1])
    for key in ("evidence_source", "data_ref", "data_digest", "data_quality"):
        assert record[key] == body[key]
