"""Task 7 — buddy /watch/* endpoints (盯盘 REST + SSE bridge).

The ``fa serve`` SSE bridge exposes the realtime-watch loop over HTTP so the
觀瀾 desktop UI can start/stop watching, subscribe to the live event stream,
acknowledge a recommendation, and add/remove watched items — *without* going
through the chat ``/run`` loop.

Everything here is **stubbed**: a fake ``WatchLoop`` (no network, no LLM, no
backtest engine) is injected by monkeypatching
``financial_analyst.watch.loop.WatchLoop`` so ``/watch/start`` constructs the
stub. ``ack_rec`` is monkeypatched to record into a list. So these are pure unit
tests of the *endpoint wiring* (singleton lifecycle / SSE framing / ack
dispatch / item add+remove), independent of the real loop and the network.

The endpoints use a **module-level singleton** (``server._watch_loop``) so the
loop survives across requests; each test resets it so they don't bleed state.
"""
from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional, Sequence

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient

from financial_analyst.buddy import server as srv
from financial_analyst.watch.models import WatchItem, WatchRec


# ==========================================================================
# stub WatchLoop — same surface the endpoints touch, zero IO
# ==========================================================================
class StubWatchLoop:
    """Stand-in for ``watch.loop.WatchLoop``.

    Captures construction kwargs, exposes the runtime attributes the endpoints
    read (``items`` / ``_queue`` / ``stopped`` / ``tick_count`` /
    ``llm_calls_made`` / ``drain``), and a ``run`` coroutine that simply parks
    until ``stop()`` (so ``asyncio.create_task(loop.run())`` is well-behaved and
    cancellable). No trigger, no feed, no agent calls actually happen.
    """

    last_instance: Optional["StubWatchLoop"] = None

    def __init__(self, items: Sequence[WatchItem], feed: Any = None,
                 agent: Any = None, **kw: Any) -> None:
        self.items: List[WatchItem] = list(items)
        self.feed = feed
        self.agent = agent
        self.kwargs = kw
        self._queue: "asyncio.Queue[Dict[str, Any]]" = asyncio.Queue()
        self.stopped = False
        self.tick_count = 0
        self.llm_calls_made = 0
        self.run_started = False
        StubWatchLoop.last_instance = self

    # --- the endpoints read these -----------------------------------------
    def drain(self) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        while True:
            try:
                out.append(self._queue.get_nowait())
            except asyncio.QueueEmpty:
                break
        return out

    def stop(self) -> None:
        self.stopped = True

    async def run(self, *a: Any, **k: Any) -> None:
        self.run_started = True
        # park until stop() flips the flag (the background task is cancelled on
        # /watch/stop, so this just needs to not return immediately).
        while not self.stopped:
            await asyncio.sleep(0.01)

    # --- test helper: push an event as if a tick produced it --------------
    def emit(self, event: Dict[str, Any]) -> None:
        self._queue.put_nowait(event)


@pytest.fixture(autouse=True)
def _reset_singleton():
    """Clear the module-level watch singleton before & after every test."""
    srv._watch_loop = None
    yield
    # best-effort stop so a lingering background task doesn't leak across tests
    loop = getattr(srv, "_watch_loop", None)
    if loop is not None:
        try:
            loop.stop()
        except Exception:
            pass
    srv._watch_loop = None


@pytest.fixture
def client(monkeypatch):
    # /watch/start builds a WatchLoop via the module attribute → swap in the stub.
    monkeypatch.setattr("financial_analyst.watch.loop.WatchLoop", StubWatchLoop)
    # never touch the real feed/agent constructors either (they import network /
    # pytdx / llm). The endpoint should build them lazily; stub them out.
    return TestClient(srv.build_app())


# ==========================================================================
# /watch/start + /watch/status
# ==========================================================================
def test_start_then_status_running(client):
    r = client.post("/watch/start", json={"items": [{"code": "600519"},
                                                     {"code": "SZ002594", "stop_loss": 80.0}]})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["running"] is True
    assert body["n_items"] == 2

    r2 = client.get("/watch/status")
    assert r2.status_code == 200
    s = r2.json()
    assert s["running"] is True
    assert s["n_items"] == 2
    # codes are normalized (bare 600519 → SH600519)
    codes = {it["code"] for it in s["items"]}
    assert "SH600519" in codes
    assert "SZ002594" in codes


def test_status_not_running_before_start(client):
    r = client.get("/watch/status")
    assert r.status_code == 200
    body = r.json()
    assert body["running"] is False
    assert body["n_items"] == 0


def test_start_requires_items(client):
    r = client.post("/watch/start", json={"items": []})
    assert r.status_code == 400
    assert r.json()["ok"] is False


def test_start_passes_watch_items_to_loop(client):
    """The stub records the WatchItem list it was constructed with — verify
    avg_cost / stop_loss are threaded through (not just the code)."""
    client.post("/watch/start", json={"items": [
        {"code": "600519", "avg_cost": 1500.0, "stop_loss": 1400.0}]})
    inst = StubWatchLoop.last_instance
    assert inst is not None
    assert len(inst.items) == 1
    it = inst.items[0]
    assert isinstance(it, WatchItem)
    assert it.code == "SH600519"
    assert it.avg_cost == 1500.0
    assert it.stop_loss == 1400.0


# ==========================================================================
# /watch/stop
# ==========================================================================
def test_stop_after_start(client):
    client.post("/watch/start", json={"items": [{"code": "600519"}]})
    r = client.post("/watch/stop")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["running"] is False

    # status now reports stopped
    s = client.get("/watch/status").json()
    assert s["running"] is False


def test_stop_when_not_running_is_ok(client):
    r = client.post("/watch/stop")
    assert r.status_code == 200
    # idempotent: stopping a non-running watcher is a no-op success
    assert r.json()["ok"] is True


# ==========================================================================
# /watch/stream — SSE: a recommendation pushed onto the queue is framed out
# ==========================================================================
def test_stream_emits_recommendation_event(client):
    client.post("/watch/start", json={"items": [{"code": "600519"}]})
    inst = StubWatchLoop.last_instance
    assert inst is not None
    # pre-populate the queue with one recommendation event (as a tick would)
    rec = WatchRec(code="SH600519", action="add", reason="放量突破",
                   trigger_kind="breakout_high", ts="2026-06-02 10:05:00",
                   target_price=1600.0, stop_loss=1480.0, confidence=0.7)
    inst.emit({"type": "recommendation", "code": "SH600519",
               "ts": rec.ts, "rec": rec.to_dict()})

    # max_events=1 → the generator returns after the single queued frame
    # (Starlette's TestClient can't consume an unbounded stream).
    got = []
    with client.stream("GET", "/watch/stream?max_events=1") as resp:
        assert resp.status_code == 200
        assert "text/event-stream" in resp.headers["content-type"]
        for line in resp.iter_lines():
            got.append(line)

    blob = "\n".join(got)
    assert "event: recommendation" in blob
    assert "SH600519" in blob
    assert "breakout_high" in blob


def test_stream_emits_quote_update_event(client):
    client.post("/watch/start", json={"items": [{"code": "600519"}]})
    inst = StubWatchLoop.last_instance
    inst.emit({"type": "quote_update", "code": "SH600519",
               "ts": "2026-06-02 10:05:00",
               "quote": {"price": 1555.0, "changePercent": 1.2}})

    got = []
    with client.stream("GET", "/watch/stream?max_events=1") as resp:
        assert resp.status_code == 200
        for line in resp.iter_lines():
            got.append(line)
    blob = "\n".join(got)
    assert "event: quote_update" in blob
    assert "1555" in blob


def test_stream_404_when_not_running(client):
    r = client.get("/watch/stream")
    # not started → no loop to stream from
    assert r.status_code == 404


# ==========================================================================
# /watch/ack — flips user_action on the persisted rec
# ==========================================================================
def test_ack_calls_store(client, monkeypatch):
    calls: List[dict] = []

    def _fake_ack(path, ts, code, user_action):
        calls.append({"path": path, "ts": ts, "code": code,
                      "user_action": user_action})
        return True

    monkeypatch.setattr("financial_analyst.watch.store.ack_rec", _fake_ack)

    r = client.post("/watch/ack", json={"ts": "2026-06-02 10:05:00",
                                        "code": "600519",
                                        "user_action": "confirm"})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert len(calls) == 1
    assert calls[0]["ts"] == "2026-06-02 10:05:00"
    assert calls[0]["code"] == "SH600519"   # normalized
    assert calls[0]["user_action"] == "confirm"


def test_ack_not_found_returns_false(client, monkeypatch):
    monkeypatch.setattr("financial_analyst.watch.store.ack_rec",
                        lambda path, ts, code, user_action: False)
    r = client.post("/watch/ack", json={"ts": "x", "code": "600519",
                                        "user_action": "ignore"})
    assert r.status_code == 200
    assert r.json()["ok"] is False


def test_ack_rejects_bad_user_action(client):
    r = client.post("/watch/ack", json={"ts": "x", "code": "600519",
                                        "user_action": "frobnicate"})
    assert r.status_code == 400
    assert r.json()["ok"] is False


# ==========================================================================
# /watch/item — add / remove an item on the running loop
# ==========================================================================
def test_item_add_grows_watchlist(client):
    client.post("/watch/start", json={"items": [{"code": "600519"}]})
    r = client.post("/watch/item", json={"op": "add", "code": "SZ002594",
                                         "stop_loss": 80.0})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["n_items"] == 2

    s = client.get("/watch/status").json()
    codes = {it["code"] for it in s["items"]}
    assert codes == {"SH600519", "SZ002594"}
    # stop_loss set on the added item enables stop_break
    inst = StubWatchLoop.last_instance
    added = next(it for it in inst.items if it.code == "SZ002594")
    assert added.stop_loss == 80.0


def test_item_add_is_idempotent_on_duplicate(client):
    client.post("/watch/start", json={"items": [{"code": "600519"}]})
    client.post("/watch/item", json={"op": "add", "code": "600519"})
    s = client.get("/watch/status").json()
    # adding the same code again must NOT duplicate it
    assert s["n_items"] == 1


def test_item_remove_shrinks_watchlist(client):
    client.post("/watch/start", json={"items": [{"code": "600519"},
                                                 {"code": "SZ002594"}]})
    r = client.post("/watch/item", json={"op": "remove", "code": "600519"})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["n_items"] == 1

    s = client.get("/watch/status").json()
    codes = {it["code"] for it in s["items"]}
    assert codes == {"SZ002594"}


def test_item_when_not_running_400(client):
    r = client.post("/watch/item", json={"op": "add", "code": "600519"})
    assert r.status_code == 400
    assert r.json()["ok"] is False


def test_item_rejects_bad_op(client):
    client.post("/watch/start", json={"items": [{"code": "600519"}]})
    r = client.post("/watch/item", json={"op": "explode", "code": "600519"})
    assert r.status_code == 400
    assert r.json()["ok"] is False


# ==========================================================================
# /watch/bars — historical 5min K线 for the chart (Gap D)
# ==========================================================================
def _bars_df():
    import pandas as pd
    return pd.DataFrame(
        [{"open": 10.0, "high": 10.5, "low": 9.9, "close": 10.4,
          "vol": 1200.0, "trade_date": "2026-06-02 10:05"}],
        columns=["open", "high", "low", "close", "vol", "trade_date"],
    )


def test_watch_bars_uses_running_loop_feed(client):
    """When a loop is running, /watch/bars reuses its feed (no new connection)."""
    client.post("/watch/start", json={"items": [{"code": "600519"}]})
    inst = StubWatchLoop.last_instance
    assert inst is not None

    class _FeedOnLoop:
        def bars5(self, code, n=240):
            return _bars_df()

    inst.feed = _FeedOnLoop()

    r = client.get("/watch/bars?code=600519&n=50")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["code"] == "SH600519"            # normalized
    assert len(body["bars"]) == 1
    assert body["bars"][0]["close"] == 10.4
    assert body["bars"][0]["vol"] == 1200.0      # 手
    assert body["bars"][0]["trade_date"] == "2026-06-02 10:05"


def test_watch_bars_transient_feed_when_not_running(client, monkeypatch):
    """No loop running → a transient WatchFeed is built AND closed."""
    closed = {"v": False}

    class _StubFeed:
        def bars5(self, code, n=240):
            return _bars_df()

        def close(self):
            closed["v"] = True

    monkeypatch.setattr("financial_analyst.watch.feed.WatchFeed", _StubFeed)
    r = client.get("/watch/bars?code=SH600519")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert len(body["bars"]) == 1
    assert closed["v"] is True                   # transient feed closed in finally


def test_watch_bars_error_returns_empty(client, monkeypatch):
    """A feed failure degrades to ok:False + empty bars (HTTP 200, not 500)."""
    class _BoomFeed:
        def bars5(self, code, n=240):
            raise RuntimeError("tdx host down")

        def close(self):
            pass

    monkeypatch.setattr("financial_analyst.watch.feed.WatchFeed", _BoomFeed)
    r = client.get("/watch/bars?code=600519")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False
    assert body["bars"] == []


def test_watch_bars_empty_code(client):
    r = client.get("/watch/bars?code=")
    assert r.status_code == 200
    assert r.json()["ok"] is False


# ==========================================================================
# /watch/outcome/backfill + /watch/hitrate + /watch/history (C 复盘闭环)
# ==========================================================================
def _outcomes_df():
    import pandas as pd
    return pd.DataFrame([
        {"ts": "2026-05-20 10:30:00", "code": "SH600519", "trigger_kind": "breakout_high",
         "action": "buy", "base_close": 10.0, "return_t1": 0.01, "return_t5": 0.05,
         "hit_target": False, "hit_stop": False, "verdict": "correct", "n_fwd": 5,
         "scored_at": "2026-05-28 18:00:00"},
        {"ts": "2026-05-20 14:00:00", "code": "SZ002594", "trigger_kind": "vol_regime",
         "action": "reduce", "base_close": 20.0, "return_t1": -0.01, "return_t5": -0.04,
         "hit_target": False, "hit_stop": False, "verdict": "correct", "n_fwd": 5,
         "scored_at": "2026-05-28 18:00:00"},
        {"ts": "2026-06-01 10:00:00", "code": "SH600000", "trigger_kind": "breakout_high",
         "action": "buy", "base_close": 5.0, "return_t1": None, "return_t5": None,
         "hit_target": False, "hit_stop": False, "verdict": "pending", "n_fwd": 1,
         "scored_at": "2026-06-02 18:00:00"},
    ])


def test_outcome_backfill_runs(client, monkeypatch):
    """POST /watch/outcome/backfill runs the scorer in a thread, returns counts."""
    monkeypatch.setattr("financial_analyst.watch.outcome.backfill_outcomes",
                        lambda *a, **k: _outcomes_df())
    r = client.post("/watch/outcome/backfill")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["n_total"] == 3
    assert body["n_scored"] == 2          # 2 final verdicts
    assert body["n_pending"] == 1


def test_outcome_backfill_error_500(client, monkeypatch):
    def _boom(*a, **k):
        raise RuntimeError("loader down")
    monkeypatch.setattr("financial_analyst.watch.outcome.backfill_outcomes", _boom)
    r = client.post("/watch/outcome/backfill")
    assert r.status_code == 500
    assert r.json()["ok"] is False


def test_hitrate_endpoint(client, monkeypatch):
    """GET /watch/hitrate aggregates the outcome log → overall + breakdowns."""
    monkeypatch.setattr("financial_analyst.watch.outcome.load_outcomes",
                        lambda *a, **k: _outcomes_df())
    r = client.get("/watch/hitrate")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["overall"]["n"] == 2          # pending excluded
    assert body["overall"]["correct"] == 2
    assert body["overall"]["win_rate"] == 1.0
    assert "breakout_high" in body["by_trigger"]
    assert "buy" in body["by_action"]


def test_history_endpoint_joins_recs_and_outcomes(client, monkeypatch):
    """GET /watch/history left-joins recs with outcomes (verdict/return attached)."""
    import pandas as pd
    from financial_analyst.watch.store import RECS_COLUMNS
    recs = pd.DataFrame([
        {"ts": "2026-05-20 10:30:00", "code": "SH600519", "trigger_kind": "breakout_high",
         "action": "buy", "target_price": 11.0, "stop_loss": 9.5, "reason": "放量突破",
         "confidence": 0.7, "user_action": "confirm", "user_action_ts": "2026-05-20 10:31:00"},
        {"ts": "2026-06-01 10:00:00", "code": "SH600000", "trigger_kind": "breakout_high",
         "action": "buy", "target_price": 0.0, "stop_loss": 0.0, "reason": "突破",
         "confidence": 0.6, "user_action": "none", "user_action_ts": ""},
    ])[RECS_COLUMNS]
    monkeypatch.setattr("financial_analyst.watch.store.load_recs", lambda *a, **k: recs)
    monkeypatch.setattr("financial_analyst.watch.outcome.load_outcomes",
                        lambda *a, **k: _outcomes_df())
    r = client.get("/watch/history?n=50")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["n"] == 2
    by_code = {row["code"]: row for row in body["rows"]}
    assert by_code["SH600519"]["verdict"] == "correct"
    assert by_code["SH600519"]["return_t5"] == 0.05
    assert by_code["SH600519"]["user_action"] == "confirm"
    # the rec with no matching outcome → verdict defaults to "pending"
    assert by_code["SH600000"]["verdict"] == "pending"
    # newest first
    assert body["rows"][0]["ts"] >= body["rows"][1]["ts"]


def test_history_empty_when_no_recs(client, monkeypatch):
    import pandas as pd
    from financial_analyst.watch.store import RECS_COLUMNS
    monkeypatch.setattr("financial_analyst.watch.store.load_recs",
                        lambda *a, **k: pd.DataFrame(columns=RECS_COLUMNS))
    monkeypatch.setattr("financial_analyst.watch.outcome.load_outcomes",
                        lambda *a, **k: _outcomes_df())
    r = client.get("/watch/history")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["rows"] == []
