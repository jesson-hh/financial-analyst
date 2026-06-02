"""Task 6 — watch/loop.py: tick 编排 loop (cooldown / 全局 LLM 上限 / 交易时段 / 事件队列).

``WatchLoop`` is the orchestrator that, each tick, asks the (pure) trigger which
watched stocks just hit a key point, builds a ``WatchContext`` from the feed, and
asks the (single-stock) agent for a ``WatchRec`` — subject to:

* **per-(code, kind) cooldown** — the same stock + same trigger kind cannot
  re-fire within ``cooldown_minutes``;
* **a per-session global LLM cap** — once ``global_llm_cap_per_session`` agent
  calls have been made, no further agent calls happen this session;
* **trading hours** — ticks outside 09:30–11:30 / 13:00–15:00 (Mon–Fri) do
  nothing.

Every tick pushes ``quote_update`` events (one per snapshotted code) and, when a
recommendation is produced, a ``recommendation`` event onto an ``asyncio.Queue``
the UI/SSE layer drains.

**Everything is stubbed here** — a fake trigger (so this test does NOT import the
heavy ``backtest.intraday`` engine, which the loop pulls in lazily), a fake feed
(``snapshot`` / ``bars5``) and a fake agent (``decide_one``). That keeps the test
a pure unit test of the loop's *orchestration* (cooldown / cap / hours / queue /
per-item exception isolation), independent of the trigger engine and the network.

pytest runs in ``asyncio_mode = auto`` (see pyproject) so ``async def test_*`` is
awaited automatically.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

import pandas as pd

from financial_analyst.watch.loop import WatchLoop, WatchLoopConfig
from financial_analyst.watch.models import WatchItem, WatchRec


# ==========================================================================
# fakes — no network, no backtest engine, no LLM
# ==========================================================================
class _FakeEvent:
    """Stand-in for ``backtest.intraday.TriggerEvent`` (only the fields the loop
    reads). Keeps the test free of the (currently absent) backtest package."""

    def __init__(self, code: str, kind: str, detail: str = "d",
                 metric: float = 1.0, is_risk: bool = False,
                 bar_index: int = 0, bar_time: str = "") -> None:
        self.code = code
        self.kind = kind
        self.detail = detail
        self.metric = metric
        self.is_risk = is_risk
        self.bar_index = bar_index
        self.bar_time = bar_time


class FakeTrigger:
    """Returns a scripted event per code. ``events[code]`` may be a single event
    or a list consumed one-per-tick (None = no signal that tick)."""

    def __init__(self, events: Dict[str, Any]) -> None:
        self._events = events
        self.reset_calls = 0
        self.check_codes: List[str] = []

    def reset_day(self) -> None:
        self.reset_calls += 1

    def check_item(self, item: WatchItem, bars_5min: pd.DataFrame,
                   i: Optional[int] = None):
        self.check_codes.append(item.code)
        ev = self._events.get(item.code)
        if isinstance(ev, list):
            return ev.pop(0) if ev else None
        return ev


class FakeFeed:
    """Returns canned snapshots + 5min bars; records call counts."""

    def __init__(self, snap: Optional[Dict[str, Dict[str, Any]]] = None) -> None:
        self._snap = snap or {}
        self.snapshot_calls = 0
        self.bars5_calls = 0

    def snapshot(self, codes: List[str]) -> Dict[str, Dict[str, Any]]:
        self.snapshot_calls += 1
        return {c: self._snap.get(c, {"price": 10.0, "changePercent": 1.0})
                for c in codes}

    def bars5(self, code: str, n: int = 240) -> pd.DataFrame:
        self.bars5_calls += 1
        return pd.DataFrame(
            [{"open": 10.0, "high": 10.5, "low": 9.9, "close": 10.4,
              "vol": 1000.0, "trade_date": "2026-06-02 10:05"}],
            columns=["open", "high", "low", "close", "vol", "trade_date"],
        )


class FakeAgent:
    """``decide_one`` returns a fixed ``WatchRec`` and counts its calls.

    ``trigger_kind`` / ``ts`` echo the context (matching the real agent's pin)."""

    def __init__(self, action: str = "add") -> None:
        self.n_calls = 0
        self._action = action
        self.seen_kinds: List[str] = []

    async def decide_one(self, ctx) -> WatchRec:
        self.n_calls += 1
        kind = (ctx.trigger or {}).get("kind", "")
        self.seen_kinds.append(kind)
        return WatchRec(
            code=ctx.code, action=self._action, reason="r",
            trigger_kind=kind, ts=ctx.now_ts, confidence=0.5,
        )


# trade-day timestamps (2026-06-02 is a Tuesday)
_OPEN_AM = pd.Timestamp("2026-06-02 10:00:00")
_OPEN_AM2 = pd.Timestamp("2026-06-02 10:05:00")   # 5 min later (< cooldown 15m)
_OPEN_AM3 = pd.Timestamp("2026-06-02 10:20:00")   # 20 min later (> cooldown 15m)
_LUNCH = pd.Timestamp("2026-06-02 12:00:00")
_WEEKEND = pd.Timestamp("2026-06-06 10:00:00")     # Saturday


def _loop(items, feed, agent, trigger, **cfg_kw) -> WatchLoop:
    cfg = WatchLoopConfig(**cfg_kw) if cfg_kw else WatchLoopConfig()
    # persist=False keeps these unit tests hermetic — they must NOT write to the
    # real recommendation parquet (default path resolves into the data root).
    return WatchLoop(items=items, feed=feed, agent=agent, trigger=trigger,
                     config=cfg, persist=False)


# ==========================================================================
# 1 — a trigger produces exactly ONE recommendation (+ queue events)
# ==========================================================================
async def test_tick_trigger_emits_one_recommendation():
    items = [WatchItem(code="SH600519")]
    feed = FakeFeed()
    agent = FakeAgent(action="add")
    trig = FakeTrigger({"SH600519": _FakeEvent("SH600519", "breakout_high")})
    loop = _loop(items, feed, agent, trig)

    recs = await loop.tick(now=_OPEN_AM)

    assert len(recs) == 1
    rec = recs[0]
    assert isinstance(rec, WatchRec)
    assert rec.code == "SH600519"
    assert rec.action == "add"
    # trigger_kind threaded through context → agent → rec
    assert rec.trigger_kind == "breakout_high"
    # exactly one LLM call for the one fired stock
    assert agent.n_calls == 1

    # queue carries a recommendation event + at least one quote_update
    events = loop.drain()
    kinds = [e["type"] for e in events]
    assert "recommendation" in kinds
    assert "quote_update" in kinds
    rec_evt = next(e for e in events if e["type"] == "recommendation")
    assert rec_evt["rec"]["code"] == "SH600519"


# ==========================================================================
# 2 — same (code, kind) on the 2nd tick is blocked by cooldown
# ==========================================================================
async def test_cooldown_blocks_same_code_kind_second_tick():
    items = [WatchItem(code="SH600519")]
    feed = FakeFeed()
    agent = FakeAgent()
    # fires breakout_high on BOTH ticks
    trig = FakeTrigger({
        "SH600519": [
            _FakeEvent("SH600519", "breakout_high"),
            _FakeEvent("SH600519", "breakout_high"),
        ]
    })
    loop = _loop(items, feed, agent, trig, cooldown_minutes=15)

    recs1 = await loop.tick(now=_OPEN_AM)        # 10:00 — fires
    recs2 = await loop.tick(now=_OPEN_AM2)       # 10:05 — within 15m cooldown

    assert len(recs1) == 1
    assert len(recs2) == 0                       # blocked
    assert agent.n_calls == 1                    # agent NOT consulted again


async def test_cooldown_expires_allows_refire():
    items = [WatchItem(code="SH600519")]
    feed = FakeFeed()
    agent = FakeAgent()
    trig = FakeTrigger({
        "SH600519": [
            _FakeEvent("SH600519", "breakout_high"),
            _FakeEvent("SH600519", "breakout_high"),
        ]
    })
    loop = _loop(items, feed, agent, trig, cooldown_minutes=15)

    await loop.tick(now=_OPEN_AM)                 # 10:00
    recs2 = await loop.tick(now=_OPEN_AM3)        # 10:20 — past 15m cooldown

    assert len(recs2) == 1                        # cooldown expired → re-fires
    assert agent.n_calls == 2


async def test_cooldown_is_per_kind_not_global_to_code():
    """A different trigger kind on the same code is NOT cooled down by the first."""
    items = [WatchItem(code="SH600519", stop_loss=9.0)]
    feed = FakeFeed()
    agent = FakeAgent()
    trig = FakeTrigger({
        "SH600519": [
            _FakeEvent("SH600519", "breakout_high"),
            _FakeEvent("SH600519", "stop_break", is_risk=True),
        ]
    })
    loop = _loop(items, feed, agent, trig, cooldown_minutes=15)

    recs1 = await loop.tick(now=_OPEN_AM)         # breakout_high
    recs2 = await loop.tick(now=_OPEN_AM2)        # stop_break (5 min later)

    assert len(recs1) == 1 and recs1[0].trigger_kind == "breakout_high"
    assert len(recs2) == 1 and recs2[0].trigger_kind == "stop_break"
    assert agent.n_calls == 2


# ==========================================================================
# 3 — global LLM cap: once reached, no more agent calls
# ==========================================================================
async def test_global_llm_cap_stops_agent_calls():
    # two stocks both firing, but the per-session cap is 1
    items = [WatchItem(code="SH600519"), WatchItem(code="SZ002594")]
    feed = FakeFeed()
    agent = FakeAgent()
    trig = FakeTrigger({
        "SH600519": _FakeEvent("SH600519", "breakout_high"),
        "SZ002594": _FakeEvent("SZ002594", "breakout_high"),
    })
    loop = _loop(items, feed, agent, trig, global_llm_cap_per_session=1)

    recs = await loop.tick(now=_OPEN_AM)

    # cap=1 → only the first fired stock consults the agent
    assert agent.n_calls == 1
    assert len(recs) == 1


async def test_global_llm_cap_persists_across_ticks():
    items = [WatchItem(code="SH600519")]
    feed = FakeFeed()
    agent = FakeAgent()
    # fires every tick on distinct kinds (so cooldown never blocks)
    trig = FakeTrigger({
        "SH600519": [
            _FakeEvent("SH600519", "breakout_high"),
            _FakeEvent("SH600519", "volume_surge"),
        ]
    })
    loop = _loop(items, feed, agent, trig, global_llm_cap_per_session=1)

    await loop.tick(now=_OPEN_AM)
    recs2 = await loop.tick(now=_OPEN_AM2)

    # session cap reached on tick 1 → tick 2 makes no agent call
    assert agent.n_calls == 1
    assert len(recs2) == 0
    assert loop.llm_calls_made == 1


# ==========================================================================
# 4 — is_market_open: 10:00 True · 12:00 (lunch) False · weekend False
# ==========================================================================
def test_is_market_open_trading_morning_true():
    loop = _loop([WatchItem(code="SH600519")], FakeFeed(), FakeAgent(),
                 FakeTrigger({}))
    assert loop.is_market_open(_OPEN_AM) is True            # 10:00 Tue


def test_is_market_open_lunch_false():
    loop = _loop([WatchItem(code="SH600519")], FakeFeed(), FakeAgent(),
                 FakeTrigger({}))
    assert loop.is_market_open(_LUNCH) is False             # 12:00 Tue (lunch)


def test_is_market_open_weekend_false():
    loop = _loop([WatchItem(code="SH600519")], FakeFeed(), FakeAgent(),
                 FakeTrigger({}))
    assert loop.is_market_open(_WEEKEND) is False           # 10:00 Saturday


def test_is_market_open_session_edges():
    loop = _loop([WatchItem(code="SH600519")], FakeFeed(), FakeAgent(),
                 FakeTrigger({}))
    # inclusive 09:30 / 11:30 / 13:00 / 15:00 boundaries are open
    assert loop.is_market_open(pd.Timestamp("2026-06-02 09:30:00")) is True
    assert loop.is_market_open(pd.Timestamp("2026-06-02 11:30:00")) is True
    assert loop.is_market_open(pd.Timestamp("2026-06-02 13:00:00")) is True
    assert loop.is_market_open(pd.Timestamp("2026-06-02 15:00:00")) is True
    # before open / after close
    assert loop.is_market_open(pd.Timestamp("2026-06-02 09:29:00")) is False
    assert loop.is_market_open(pd.Timestamp("2026-06-02 15:01:00")) is False


# ==========================================================================
# 5 — tick outside market hours is a no-op (no feed/agent calls)
# ==========================================================================
async def test_tick_outside_hours_is_noop():
    items = [WatchItem(code="SH600519")]
    feed = FakeFeed()
    agent = FakeAgent()
    trig = FakeTrigger({"SH600519": _FakeEvent("SH600519", "breakout_high")})
    loop = _loop(items, feed, agent, trig)

    recs = await loop.tick(now=_LUNCH)            # lunch → closed

    assert recs == []
    assert agent.n_calls == 0
    assert feed.snapshot_calls == 0
    assert feed.bars5_calls == 0


# ==========================================================================
# 6 — a single stock blowing up does NOT kill the rest of the tick
# ==========================================================================
async def test_per_item_exception_isolated():
    items = [WatchItem(code="BOOM"), WatchItem(code="SH600519")]
    feed = FakeFeed()
    agent = FakeAgent()

    class _BoomTrigger(FakeTrigger):
        def check_item(self, item, bars_5min, i=None):
            if item.code == "BOOM":
                raise RuntimeError("trigger blew up on BOOM")
            return super().check_item(item, bars_5min, i)

    trig = _BoomTrigger({"SH600519": _FakeEvent("SH600519", "breakout_high")})
    loop = _loop(items, feed, agent, trig)

    recs = await loop.tick(now=_OPEN_AM)

    # BOOM raised, but SH600519 still produced its recommendation
    assert len(recs) == 1
    assert recs[0].code == "SH600519"
    assert agent.n_calls == 1


# ==========================================================================
# 7 — no trigger → no recommendation, but quote_update still flows
# ==========================================================================
async def test_no_trigger_no_rec_but_quotes_flow():
    items = [WatchItem(code="SH600519")]
    feed = FakeFeed()
    agent = FakeAgent()
    trig = FakeTrigger({"SH600519": None})        # never fires
    loop = _loop(items, feed, agent, trig)

    recs = await loop.tick(now=_OPEN_AM)

    assert recs == []
    assert agent.n_calls == 0
    events = loop.drain()
    assert any(e["type"] == "quote_update" for e in events)
    assert all(e["type"] != "recommendation" for e in events)


# ==========================================================================
# 7b — when persistence is on, the rec is appended to the parquet log
# ==========================================================================
async def test_recommendation_persisted_to_store(tmp_path):
    from financial_analyst.watch.store import load_recs

    store_path = tmp_path / "recs.parquet"
    items = [WatchItem(code="SH600519")]
    feed = FakeFeed()
    agent = FakeAgent(action="add")
    trig = FakeTrigger({"SH600519": _FakeEvent("SH600519", "breakout_high")})
    # explicit store_path under tmp → real append, no pollution of the data root
    loop = WatchLoop(items=items, feed=feed, agent=agent, trigger=trig,
                     config=WatchLoopConfig(), store_path=str(store_path),
                     persist=True)

    recs = await loop.tick(now=_OPEN_AM)
    assert len(recs) == 1

    df = load_recs(str(store_path))
    assert len(df) == 1
    row = df.iloc[0]
    assert row["code"] == "SH600519"
    assert row["trigger_kind"] == "breakout_high"
    assert row["action"] == "add"
    assert row["user_action"] == "none"


# ==========================================================================
# 8 — config defaults match the spec
# ==========================================================================
def test_config_defaults():
    cfg = WatchLoopConfig()
    assert cfg.tick_seconds == 60
    assert cfg.news_every_n_ticks == 5
    assert cfg.cooldown_minutes == 15
    assert cfg.global_llm_cap_per_session == 20


# ==========================================================================
# 9 — news channel: a keyword-matching headline fires a rec when no bar signal
# ==========================================================================
async def test_news_tick_fires_recommendation_via_news_provider():
    items = [WatchItem(code="SH600519")]
    feed = FakeFeed()
    agent = FakeAgent(action="hold")
    trig = FakeTrigger({"SH600519": None})       # no bar signal this tick
    # news_every_n_ticks=1 → every tick is a news tick; the headline contains
    # default keywords ("签订" / "重大合同") so news_trigger matches.
    loop = WatchLoop(items=items, feed=feed, agent=agent, trigger=trig,
                     config=WatchLoopConfig(news_every_n_ticks=1), persist=False,
                     news_provider=lambda code: ["公司签订重大合同"])

    recs = await loop.tick(now=_OPEN_AM)

    assert len(recs) == 1
    assert recs[0].trigger_kind == "news"
    assert agent.n_calls == 1


async def test_news_provider_disabled_when_none():
    """No news_provider → news channel silent (no rec from a would-be headline)."""
    items = [WatchItem(code="SH600519")]
    feed = FakeFeed()
    agent = FakeAgent()
    trig = FakeTrigger({"SH600519": None})
    loop = WatchLoop(items=items, feed=feed, agent=agent, trigger=trig,
                     config=WatchLoopConfig(news_every_n_ticks=1), persist=False,
                     news_provider=None)

    recs = await loop.tick(now=_OPEN_AM)

    assert recs == []
    assert agent.n_calls == 0


# ==========================================================================
# 10 — injected holiday-aware day gate overrides the weekday check
# ==========================================================================
async def test_injected_trading_day_makes_holiday_a_noop():
    items = [WatchItem(code="SH600519")]
    feed = FakeFeed()
    agent = FakeAgent()
    trig = FakeTrigger({"SH600519": _FakeEvent("SH600519", "breakout_high")})
    # is_trading_day says CLOSED even at 10:00 on a Tuesday (a holiday).
    loop = WatchLoop(items=items, feed=feed, agent=agent, trigger=trig,
                     config=WatchLoopConfig(), persist=False,
                     is_trading_day=lambda ts: False)

    assert loop.is_market_open(_OPEN_AM) is False
    recs = await loop.tick(now=_OPEN_AM)

    assert recs == []
    assert feed.snapshot_calls == 0
    assert agent.n_calls == 0


async def test_injected_trading_day_open_still_respects_session_window():
    """is_trading_day=True (a trading day) but lunch → still closed (time gate)."""
    items = [WatchItem(code="SH600519")]
    loop = WatchLoop(items=items, feed=FakeFeed(), agent=FakeAgent(),
                     trigger=FakeTrigger({}), config=WatchLoopConfig(),
                     persist=False, is_trading_day=lambda ts: True)
    assert loop.is_market_open(_OPEN_AM) is True       # 10:00 trading day
    assert loop.is_market_open(_LUNCH) is False        # 12:00 lunch (time gate)


# ==========================================================================
# 11 — B1 negative-event HARD rule: severity>=2 → sell(held)/hold(not), NO LLM
# ==========================================================================
async def test_negative_event_hard_sell_when_held():
    items = [WatchItem(code="SH600052", stop_loss=9.0)]      # held proxy
    feed = FakeFeed()
    agent = FakeAgent()
    # a bar trigger WOULD also fire — but the negative event must pre-empt it.
    trig = FakeTrigger({"SH600052": _FakeEvent("SH600052", "breakout_high")})
    warns = {"SH600052": {"severity": 2, "title": "股东减持", "event_date": "2026-05-23"}}
    loop = WatchLoop(items=items, feed=feed, agent=agent, trigger=trig,
                     config=WatchLoopConfig(), persist=False,
                     warnings_provider=lambda: warns)

    recs = await loop.tick(now=_OPEN_AM)

    assert len(recs) == 1
    assert recs[0].action == "sell"
    assert recs[0].trigger_kind == "negative_event"
    assert "减持" in recs[0].reason
    # HARD rule → no LLM, and the negative-event short-circuits before bars5
    assert agent.n_calls == 0
    assert feed.bars5_calls == 0


async def test_negative_event_hold_when_not_held():
    items = [WatchItem(code="SH600052")]                     # no avg_cost/stop_loss
    agent = FakeAgent()
    warns = {"SH600052": {"severity": 3, "title": "立案", "event_date": "x"}}
    loop = WatchLoop(items=items, feed=FakeFeed(), agent=agent,
                     trigger=FakeTrigger({}), config=WatchLoopConfig(),
                     persist=False, warnings_provider=lambda: warns)

    recs = await loop.tick(now=_OPEN_AM)

    assert len(recs) == 1
    assert recs[0].action == "hold"                          # 禁建仓
    assert ("禁建仓" in recs[0].reason or "规避" in recs[0].reason)
    assert agent.n_calls == 0


async def test_negative_event_cooldown_blocks_refire():
    items = [WatchItem(code="SH600052", stop_loss=9.0)]
    agent = FakeAgent()
    warns = {"SH600052": {"severity": 2, "title": "减持", "event_date": "x"}}
    loop = WatchLoop(items=items, feed=FakeFeed(), agent=agent,
                     trigger=FakeTrigger({}), config=WatchLoopConfig(cooldown_minutes=15),
                     persist=False, warnings_provider=lambda: warns)

    r1 = await loop.tick(now=_OPEN_AM)
    r2 = await loop.tick(now=_OPEN_AM2)                       # 5 min later, within cooldown
    assert len(r1) == 1 and len(r2) == 0


async def test_negative_event_sev1_does_not_fire():
    items = [WatchItem(code="SH600052", stop_loss=9.0)]
    agent = FakeAgent()
    warns = {"SH600052": {"severity": 1, "title": "风险提示", "event_date": "x"}}
    # sev1 below threshold → no negative rec; no bar signal → no rec at all.
    loop = WatchLoop(items=items, feed=FakeFeed(), agent=agent,
                     trigger=FakeTrigger({"SH600052": None}), config=WatchLoopConfig(),
                     persist=False, warnings_provider=lambda: warns)

    recs = await loop.tick(now=_OPEN_AM)
    assert recs == []
    assert agent.n_calls == 0


# ==========================================================================
# 9 — stop() makes run() return; drain() empties the queue
# ==========================================================================
async def test_stop_then_run_returns_quickly():
    items = [WatchItem(code="SH600519")]
    feed = FakeFeed()
    agent = FakeAgent()
    trig = FakeTrigger({"SH600519": None})
    # tick_seconds tiny so the sleep between ticks is negligible
    loop = _loop(items, feed, agent, trig, tick_seconds=0.01)

    loop.stop()                  # pre-stopped → run() must exit promptly
    await loop.run(now=_OPEN_AM)
    assert loop.stopped is True


async def test_drain_empties_queue():
    items = [WatchItem(code="SH600519")]
    feed = FakeFeed()
    agent = FakeAgent()
    trig = FakeTrigger({"SH600519": _FakeEvent("SH600519", "breakout_high")})
    loop = _loop(items, feed, agent, trig)

    await loop.tick(now=_OPEN_AM)
    first = loop.drain()
    assert len(first) >= 1
    # a second drain right after is empty (queue consumed)
    assert loop.drain() == []


# ==========================================================================
# 12 — B2 vol_regime channel: risk regime → advisor (consumes LLM), pre-empts bar
# ==========================================================================
async def test_vol_regime_fires_advisor():
    items = [WatchItem(code="SH600519")]
    agent = FakeAgent(action="reduce")
    trig = FakeTrigger({"SH600519": None})            # no bar signal
    rp = lambda code, bars=None: {"regime_label": "super_distr", "super_distr": True,
                                  "expected_spread_pp": -4.2, "detail": "派发"}
    loop = WatchLoop(items=items, feed=FakeFeed(), agent=agent, trigger=trig,
                     config=WatchLoopConfig(), persist=False, regime_provider=rp)

    recs = await loop.tick(now=_OPEN_AM)

    assert len(recs) == 1
    assert recs[0].trigger_kind == "vol_regime"
    assert agent.n_calls == 1                          # routed to the advisor


async def test_vol_regime_neutral_no_fire():
    items = [WatchItem(code="SH600519")]
    agent = FakeAgent()
    rp = lambda code, bars=None: {"regime_label": "neutral", "expected_spread_pp": 0.0}
    loop = WatchLoop(items=items, feed=FakeFeed(), agent=agent,
                     trigger=FakeTrigger({"SH600519": None}), config=WatchLoopConfig(),
                     persist=False, regime_provider=rp)

    recs = await loop.tick(now=_OPEN_AM)
    assert recs == []
    assert agent.n_calls == 0


async def test_vol_regime_preempts_bar_trigger():
    """A distribution regime is checked before — and pre-empts — a breakout buy."""
    items = [WatchItem(code="SH600519")]
    agent = FakeAgent()
    trig = FakeTrigger({"SH600519": _FakeEvent("SH600519", "breakout_high")})
    rp = lambda code, bars=None: {"regime_label": "distr", "expected_spread_pp": -1.42, "detail": "派发"}
    loop = WatchLoop(items=items, feed=FakeFeed(), agent=agent, trigger=trig,
                     config=WatchLoopConfig(), persist=False, regime_provider=rp)

    recs = await loop.tick(now=_OPEN_AM)

    assert len(recs) == 1
    assert recs[0].trigger_kind == "vol_regime"        # NOT breakout_high
    assert agent.n_calls == 1
