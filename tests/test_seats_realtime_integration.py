"""End-to-end contracts for Seats' durable realtime evidence chain.

These tests deliberately cross the HTTP route, SQLite store and decision
kernel.  All market/LLM dependencies are fakes and every durable artifact is
redirected to ``tmp_path`` so the developer's real ``var/`` is untouched.
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pandas as pd
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


_REPO = Path(__file__).resolve().parent.parent
_ENGINE = _REPO / "engine"
if _ENGINE.is_dir() and "financial_analyst" not in sys.modules:
    sys.path.insert(0, str(_ENGINE))

from guanlan_v2.seats import api as seats_api  # noqa: E402
from guanlan_v2.seats import minute_evidence  # noqa: E402
from guanlan_v2.seats.realtime_store import RealtimeBarStore  # noqa: E402


class _FakeLLM:
    provider = "deepseek"
    model = "deepseek-v4-flash"

    @classmethod
    def for_agent(cls, _name):
        return cls()

    def with_overrides(self, **_kwargs):
        return self

    async def chat(self, _messages, **_kwargs):
        return {"choices": [{"message": {
            "content": json.dumps({
                "direction": "观望",
                "confidence": 61,
                "rationale": "分钟证据桩",
                "key_evidence": ["durable minute snapshot"],
            }, ensure_ascii=False),
            "reasoning_content": "",
        }}]}


class _UnusedLoader:
    def fetch_quote(self, *_args, **_kwargs):
        return pd.DataFrame()


def _patch_decide_dependencies(monkeypatch, tmp_path, store):
    import financial_analyst.data.loader_factory as loader_factory
    import financial_analyst.llm.client as llm_client

    monkeypatch.setattr(seats_api, "_DEC_LOG", tmp_path / "decisions.jsonl")
    monkeypatch.setattr(seats_api, "_realtime_store", lambda: store)
    monkeypatch.setattr(seats_api, "_surface_for_decide", lambda *_args: [])
    monkeypatch.setattr(loader_factory, "get_default_loader", lambda: _UnusedLoader())
    monkeypatch.setattr(llm_client, "LLMClient", _FakeLLM)


def _bars(start, count, freq, *, base=10.0):
    stamps = pd.date_range(start, periods=count, freq=freq)
    rows = []
    for i, stamp in enumerate(stamps):
        close = base + i * 0.01
        rows.append({
            "trade_date": stamp,
            "open": close - 0.01,
            "high": close + 0.03,
            "low": close - 0.03,
            "close": close,
            "vol": 1000.0 + i,
        })
    return rows


def test_bars_live_persists_clean_snapshot_and_replays_source_outage(
        tmp_path, monkeypatch):
    """The UI route returns only validated bars and a replayable receipt."""
    store_path = tmp_path / "realtime.sqlite3"
    store = RealtimeBarStore(store_path)
    monkeypatch.setattr(seats_api, "_realtime_store", lambda: store)

    good = _bars("2026-08-14 14:57", 2, "1min")
    good[-1]["vol"] = 5.877471754111438e-41
    bad = dict(_bars("2026-08-14 14:59", 1, "1min")[0])
    bad["high"] = bad["low"] - 1.0

    class Feed:
        outage = False

        def bars1(self, _code, _n):
            if self.outage:
                raise OSError("feed offline")
            return pd.DataFrame([*good, bad])

        def bars5(self, _code, _n):  # pragma: no cover - branch guard
            raise AssertionError("1min request must not call bars5")

        def close(self):
            pass

    import financial_analyst.watch.feed as feed_module
    monkeypatch.setattr(feed_module, "WatchFeed", Feed)
    app = FastAPI()
    app.include_router(seats_api.build_seats_router())
    client = TestClient(app)

    live = client.get("/seats/bars_live", params={
        "code": "600667", "freq": "1min", "n": 20,
    }).json()
    assert live["ok"] is True
    assert live["source"] == "pytdx+realtime_store"
    assert live["code"] == "SH600667" and live["freq"] == "1min"
    assert live["n"] == 2  # invalid OHLC never reaches browser or durable replay
    assert live["bars"][-1]["vol"] == 0.0
    assert live["bars"][-1]["quality_flags"] == ["volume_subnormal_zeroed"]
    assert live["quality_flagged_n"] == 1
    assert live["data_asof"] == "2026-08-14 14:58"
    assert live["data_ref"].startswith("seats-realtime://SH600667/1min?")
    assert f"sha256={live['digest']}" in live["data_ref"]

    # Reopening the SQLite file proves that this was not an in-memory response.
    reopened = RealtimeBarStore(store_path)
    durable = reopened.snapshot("SH600667", "1min", limit=20)
    assert durable is not None
    assert durable["digest"] == live["digest"]
    assert durable["data_ref"] == live["data_ref"]
    assert [row["close"] for row in durable["bars"]] == [
        row["close"] for row in live["bars"]]
    assert reopened.receipt(live["digest"]) is None  # chart polling is not an audit event

    # A later source outage replays the exact durable receipt, visibly labelled.
    Feed.outage = True
    replay = client.get("/seats/bars_live", params={
        "code": "SH600667", "freq": "1min", "n": 20,
    }).json()
    assert replay["ok"] is True
    assert replay["source"] == "realtime_store_cache"
    # The adapter intentionally exposes the failure class, not arbitrary
    # upstream exception text that could contain host details.
    assert "OSError" in replay["source_error"]
    assert replay["digest"] == live["digest"]
    assert replay["data_ref"] == live["data_ref"]
    assert replay["bars"] == live["bars"]
    assert reopened.receipt(live["digest"]) is None


@pytest.mark.parametrize(
    ("evidence_tf", "bar_freq", "start", "anchor"),
    [
        ("1min", "1min", "2026-08-14 09:30", "2026-08-14 10:09"),
        ("5min", "5min", "2026-08-14 09:35", "2026-08-14 12:50"),
    ],
)
def test_decide_uses_pit_durable_snapshot_and_persists_provenance(
        evidence_tf, bar_freq, start, anchor, tmp_path, monkeypatch):
    """1/5-minute decisions cite precisely the durable PIT slice they used."""
    store = RealtimeBarStore(tmp_path / f"{evidence_tf}.sqlite3")
    rows = _bars(start, 42, bar_freq, base=20.0)
    # The last two rows sit beyond the decision anchor and must not enter either
    # the frame or its receipt.  The quality flag before the anchor must survive.
    rows[10]["vol"] = 5.877471754111438e-41
    store.ingest("SH600667", evidence_tf, rows,
                 pulled_at="2026-08-14T08:00:00Z")
    expected = store.snapshot("SH600667", evidence_tf, limit=800, asof=anchor)
    assert expected is not None and expected["last_bar_time"] == anchor
    _patch_decide_dependencies(monkeypatch, tmp_path, store)

    result = asyncio.run(seats_api._decide_impl({
        "code": "600667",
        "name": "太极实业",
        "date": anchor,
        "mode": "fast",
        "regime": "测试市况",
        "freq": evidence_tf,
        "requested_evidence_tf": evidence_tf,
        "effective_evidence_tf": evidence_tf,
        "source": "watcher",
        "strategy_id": f"strategy-{evidence_tf}",
        "decision_cadence": "10min",
        "assignment_revision": 8,
    }))

    assert result["ok"] is True
    assert result["freq"] == evidence_tf
    assert result["requested_evidence_tf"] == evidence_tf
    assert result["effective_evidence_tf"] == evidence_tf
    assert result["data_asof"] == anchor
    assert result["evidence_source"] == "realtime_store_cache"
    assert result["data_ref"] == expected["data_ref"]
    assert result["data_digest"] == expected["digest"]
    assert result["data_quality"] == {
        "quality_flagged_n": expected["quality_flagged_n"],
        "complete_n": expected["complete_n"],
        "n": expected["n"],
    }
    receipt = store.receipt(result["data_digest"])
    assert receipt is not None and receipt["data_ref"] == result["data_ref"]

    record = json.loads(
        (tmp_path / "decisions.jsonl").read_text(encoding="utf-8").splitlines()[-1])
    for key in (
        "freq", "requested_evidence_tf", "effective_evidence_tf", "data_asof",
        "evidence_source", "data_ref", "data_digest", "data_quality",
        "decision_cadence", "assignment_revision",
    ):
        assert record[key] == result[key]
    assert record["strategy_id"] == f"strategy-{evidence_tf}"
    assert record["source"] == "watcher"


def test_decide_replays_fresh_snapshot_when_live_source_is_down(
        tmp_path, monkeypatch):
    """A source outage is survivable only through an honest, still-fresh cache."""
    store = RealtimeBarStore(tmp_path / "outage.sqlite3")
    anchor = pd.Timestamp.now().floor("min")
    start = anchor - pd.Timedelta(minutes=39)
    store.ingest("SZ300750", "1min", _bars(start, 40, "1min", base=30.0))
    expected = store.snapshot("SZ300750", "1min", limit=800, asof=anchor)
    assert expected is not None
    _patch_decide_dependencies(monkeypatch, tmp_path, store)
    # This test may run overnight and exercises outage replay rather than the
    # exchange-session clock (which has its own boundary contract below).
    monkeypatch.setattr(
        seats_api, "_watcher_minute_evidence_fresh", lambda *_args: True)

    def unavailable(*_args, **_kwargs):
        raise minute_evidence.MinuteEvidenceError("primary feed offline")

    monkeypatch.setattr(minute_evidence, "fetch_live_snapshot", unavailable)
    result = asyncio.run(seats_api._decide_impl({
        "code": "300750",
        "name": "宁德时代",
        "date": anchor.isoformat(timespec="minutes"),
        "mode": "fast",
        "regime": "测试市况",
        "freq": "1min",
        "requested_evidence_tf": "1min",
        "effective_evidence_tf": "1min",
        "source": "watcher",
        "strategy_id": "strategy-live",
        "decision_cadence": "10min",
        "assignment_revision": 3,
    }))
    assert result["ok"] is True
    assert result["evidence_source"] == "realtime_store_cache"
    assert result["data_ref"] == expected["data_ref"]
    assert result["data_digest"] == expected["digest"]
    assert "primary feed offline" in result["data_quality"]["source_error"]

    record = json.loads(
        (tmp_path / "decisions.jsonl").read_text(encoding="utf-8").splitlines()[-1])
    assert record["data_ref"] == result["data_ref"]
    assert record["data_digest"] == result["data_digest"]
    assert record["data_quality"]["source_error"] == result["data_quality"]["source_error"]


def test_live_decide_receipt_is_cut_to_anchor_not_future_bars(
        tmp_path, monkeypatch):
    """PIT applies to the replay receipt as well as the in-memory DataFrame."""
    store = RealtimeBarStore(tmp_path / "pit.sqlite3")
    anchor = pd.Timestamp.now().floor("min") - pd.Timedelta(minutes=1)
    rows = _bars(anchor - pd.Timedelta(minutes=37), 40, "1min", base=40.0)
    full = store.ingest("SH600667", "1min", rows)["snapshot"]
    assert pd.Timestamp(full["last_bar_time"]) > anchor
    expected = store.snapshot("SH600667", "1min", limit=800, asof=anchor)
    assert expected is not None
    _patch_decide_dependencies(monkeypatch, tmp_path, store)
    # Keep this PIT/receipt test independent of the host wall clock.
    monkeypatch.setattr(
        seats_api, "_watcher_minute_evidence_fresh", lambda *_args: True)

    # Simulate a successful current pull containing a forming/future bar.  The
    # kernel must re-read the store at its PIT anchor before recording a digest.
    monkeypatch.setattr(minute_evidence, "fetch_live_snapshot",
                        lambda *_args, **_kwargs: full)
    result = asyncio.run(seats_api._decide_impl({
        "code": "SH600667",
        "name": "太极实业",
        "date": anchor.isoformat(timespec="minutes"),
        "mode": "fast",
        "regime": "测试市况",
        "freq": "1min",
        "requested_evidence_tf": "1min",
        "effective_evidence_tf": "1min",
        "source": "watcher",
    }))
    assert result["ok"] is True
    assert result["data_asof"] == expected["last_bar_time"]
    assert result["data_ref"] == expected["data_ref"]
    assert result["data_digest"] == expected["digest"]
    assert result["data_quality"]["n"] == expected["n"]
    assert f"to={anchor.isoformat(timespec='minutes')}" in result["data_ref"]


def test_watcher_fails_closed_when_only_stale_durable_minute_data_exists(
        tmp_path, monkeypatch):
    store = RealtimeBarStore(tmp_path / "stale.sqlite3")
    anchor = pd.Timestamp.now().floor("min")
    stale = anchor - pd.Timedelta(minutes=30)
    store.ingest("SZ300750", "1min", _bars(stale - pd.Timedelta(minutes=9), 10, "1min"))
    _patch_decide_dependencies(monkeypatch, tmp_path, store)
    monkeypatch.setattr(
        minute_evidence, "fetch_live_snapshot",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            minute_evidence.MinuteEvidenceError("feed offline")),
    )
    freshness_calls = []
    monkeypatch.setattr(
        seats_api, "_watcher_minute_evidence_fresh",
        lambda data, decision_time, source_freq: (
            freshness_calls.append((data, decision_time, source_freq)) or False),
    )

    result = asyncio.run(seats_api._decide_impl({
        "code": "SZ300750",
        "date": anchor.isoformat(timespec="minutes"),
        "freq": "1min",
        "requested_evidence_tf": "1min",
        "effective_evidence_tf": "1min",
        "source": "watcher",
    }))
    assert result["ok"] is False
    assert result["reason"] == "minute_evidence_stale"
    assert pd.Timestamp(result["data_asof"]) == stale
    assert result["data_ref"].startswith("seats-realtime://SZ300750/1min?")
    assert len(freshness_calls) == 1 and freshness_calls[0][2] == "1min"
    assert not (tmp_path / "decisions.jsonl").exists()


def test_manual_live_minute_decide_rejects_stale_cache_before_llm(
        tmp_path, monkeypatch):
    """Manual clicks cannot turn an old cache into a current-minute opinion."""
    store = RealtimeBarStore(tmp_path / "manual-stale.sqlite3")
    anchor = pd.Timestamp.now().floor("min")
    stale = anchor - pd.Timedelta(minutes=4)  # outside the 1min <=3m contract
    store.ingest(
        "SH600667", "1min",
        _bars(stale - pd.Timedelta(minutes=19), 20, "1min", base=50.0),
    )
    _patch_decide_dependencies(monkeypatch, tmp_path, store)
    monkeypatch.setattr(
        minute_evidence, "fetch_live_snapshot",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            minute_evidence.MinuteEvidenceError("feed offline")),
    )
    freshness_calls = []
    monkeypatch.setattr(
        seats_api, "_watcher_minute_evidence_fresh",
        lambda data, decision_time, source_freq: (
            freshness_calls.append((data, decision_time, source_freq)) or False),
    )

    llm_calls = []

    class CountingLLM(_FakeLLM):
        @classmethod
        def for_agent(cls, name):
            llm_calls.append(name)
            return cls()

    import financial_analyst.llm.client as llm_client
    monkeypatch.setattr(llm_client, "LLMClient", CountingLLM)
    result = asyncio.run(seats_api._decide_impl({
        "code": "SH600667",
        "date": anchor.isoformat(timespec="minutes"),
        "mode": "fast",
        "regime": "测试市况",
        "freq": "1min",
        "requested_evidence_tf": "1min",
        "effective_evidence_tf": "1min",
        # Deliberately no source=watcher: live-day manual and automatic
        # judgments share the same freshness safety gate.
    }))
    assert result["ok"] is False
    assert result["reason"] == "minute_evidence_stale"
    assert pd.Timestamp(result["data_asof"]) == stale
    assert len(freshness_calls) == 1 and freshness_calls[0][2] == "1min"
    assert llm_calls == []
    assert not (tmp_path / "decisions.jsonl").exists()


@pytest.mark.parametrize(
    ("source_freq", "lag_minutes", "is_fresh"),
    [
        ("1min", 3, True),
        ("1min", 4, False),
        ("5min", 10, True),
        ("5min", 11, False),
    ],
)
def test_live_minute_freshness_boundaries_by_frequency(
        source_freq, lag_minutes, is_fresh):
    """1min and 5min-backed evidence have intentionally different age budgets."""
    anchor = pd.Timestamp("2026-08-14 14:30")
    data_time = anchor - pd.Timedelta(minutes=lag_minutes)
    assert seats_api._watcher_minute_evidence_fresh(
        data_time, anchor, source_freq) is is_fresh


@pytest.mark.parametrize(
    ("evidence_tf", "store_freq", "bar_step", "expected_source_freq"),
    [
        ("1min", "1min", "1min", "1min"),
        ("5min", "5min", "5min", "5min"),
        ("30min", "5min", "5min", "5min"),
    ],
)
def test_decide_routes_the_actual_source_frequency_to_freshness_gate(
        evidence_tf, store_freq, bar_step, expected_source_freq,
        tmp_path, monkeypatch):
    store = RealtimeBarStore(tmp_path / f"wiring-{evidence_tf}.sqlite3")
    anchor = pd.Timestamp.now().floor("min")
    step = pd.Timedelta(bar_step)
    store.ingest(
        "SZ300750", store_freq,
        _bars(anchor - step * 19, 20, bar_step, base=60.0),
    )
    _patch_decide_dependencies(monkeypatch, tmp_path, store)
    monkeypatch.setattr(
        minute_evidence, "fetch_live_snapshot",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            minute_evidence.MinuteEvidenceError("feed offline")),
    )
    seen = []
    monkeypatch.setattr(
        seats_api, "_watcher_minute_evidence_fresh",
        lambda data, decision_time, source_freq: (
            seen.append(source_freq) or True),
    )
    result = asyncio.run(seats_api._decide_impl({
        "code": "SZ300750",
        "date": anchor.isoformat(timespec="minutes"),
        "mode": "fast",
        "regime": "测试市况",
        "freq": evidence_tf,
        "requested_evidence_tf": evidence_tf,
        "effective_evidence_tf": evidence_tf,
        "source": "watcher",
    }))
    assert result["ok"] is True
    assert seen == [expected_source_freq]


def test_minute_freshness_clock_skew_and_lunch_boundaries():
    fresh = seats_api._watcher_minute_evidence_fresh
    # Exchange/host clock skew up to 60 seconds is tolerated; anything larger
    # is a future observation and fails closed.
    assert fresh("2026-08-14 14:31:00", "2026-08-14 14:30:00") is True
    assert fresh("2026-08-14 14:31:01", "2026-08-14 14:30:00") is False
    # Lunch contributes zero trading-time lag, but it must not widen the normal
    # 1m=3 minute / 5m=10 minute freshness budgets.
    assert fresh("2026-08-14 11:27", "2026-08-14 12:45", "1min") is True
    assert fresh("2026-08-14 11:26", "2026-08-14 12:45", "1min") is False
    assert fresh("2026-08-14 11:20", "2026-08-14 12:45", "5min") is True
    assert fresh("2026-08-14 11:19", "2026-08-14 12:45", "5min") is False
    assert fresh("2026-08-14 11:30", "2026-08-14 13:03", "1min") is True
    assert fresh("2026-08-14 11:30", "2026-08-14 13:04", "1min") is False
    assert fresh("2026-08-14 11:30", "2026-08-14 13:10", "5min") is True
    assert fresh("2026-08-14 11:30", "2026-08-14 13:11", "5min") is False
    # A timestamp inside the lunch pause is not a valid exchange bar.
    assert fresh("2026-08-14 12:30", "2026-08-14 13:00", "5min") is False
    # After close, the anchor is capped at 15:00 and the same budgets apply.
    assert fresh("2026-08-14 14:57", "2026-08-14 22:00", "1min") is True
    assert fresh("2026-08-14 14:56", "2026-08-14 22:00", "1min") is False
    assert fresh("2026-08-14 14:50", "2026-08-14 22:00", "5min") is True
    assert fresh("2026-08-14 14:49", "2026-08-14 22:00", "5min") is False
