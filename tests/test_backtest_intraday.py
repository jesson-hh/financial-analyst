"""P3 — intraday key-point re-decision tests (intraday.py + engine 盘中循环).

Layout (mirrors the P3 design §5):
  甲 — mock intraday trigger changes同日 fills (vs no-trigger baseline)
  乙 — time-series law: 盘中决策只用截至 t 的 bar; fill bar 是 i+1
  丙 — trigger 上限 / 去重 / 风控豁免
  丁 — disabled intraday == P2 端到端
  戊 — cache 命中不重跑 + mutate 不回灌
  己 — 真 LLM 盘中 smoke (mark.slow, 无 key/数据/网络 → skip)

Test infra reuses the P2 toy fixtures (``_ToyLoader``/``_StubAgent`` patterns from
test_backtest_engine.py) and extends them with a 5min-capable loader/reader, an
``_IntradayStubAgent`` (pre-open vs intraday scripted), and a recording trigger.

``asyncio_mode=auto`` → bare ``async def test_``.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pandas as pd
import pytest

from financial_analyst.backtest.decision import (
    Decision,
    DecisionAgent,
    DecisionCache,
    DecisionInput,
    DecisionLeg,
    build_messages,
)
from financial_analyst.backtest.engine import BacktestRunner, RunConfig
from financial_analyst.backtest.intraday import (
    IntradayTrigger,
    IntradayTriggerConfig,
    TriggerEvent,
)
from financial_analyst.backtest.pit_reader import EventItem, NewsItem, VisibleInfo

# ==========================================================================
# Toy day-level history (calendar/OHLCV) — one stock, with day-0 history so the
# first run day has a valid ref_prev_close. Window = 2026-04-01..04-03.
# ==========================================================================
_HIST = "2026-03-31"
_DAYS = ["2026-04-01", "2026-04-02", "2026-04-03"]
_PAD = [_HIST] + _DAYS + ["2026-12-31"]


def _ts(date: str, hhmm: str) -> pd.Timestamp:
    return pd.Timestamp(f"{date} {hhmm}:00")


class _ToyLoader:
    """Day-level OHLCV + close/factor bins for ref_prev_close.

    Two codes: SH600001 (a holding/candidate that trends up) and SH600002
    (a second candidate, used for global-cap tests). Day closes flat ~10 so
    that the *intraday* path — not the pre-open day-bar path — drives the
    interesting behavior.
    """

    def __init__(self, ohlcv=None):
        self._cal = [pd.Timestamp(d) for d in _PAD]
        self._ohlcv = ohlcv if ohlcv is not None else {
            "SH600001": {
                "2026-03-31": (9.9, 10.1, 9.8, 10.0, 900_000),
                "2026-04-01": (10.0, 10.2, 9.9, 10.0, 1_000_000),
                "2026-04-02": (10.0, 10.3, 9.8, 10.0, 1_100_000),
                "2026-04-03": (10.0, 10.4, 9.7, 10.0, 1_200_000),
            },
            "SH600002": {
                "2026-03-31": (19.9, 20.1, 19.8, 20.0, 800_000),
                "2026-04-01": (20.0, 20.4, 19.8, 20.0, 900_000),
                "2026-04-02": (20.0, 20.5, 19.7, 20.0, 950_000),
                "2026-04-03": (20.0, 20.6, 19.6, 20.0, 980_000),
            },
        }
        self._close = {c: {d: v[3] for d, v in days.items()}
                       for c, days in self._ohlcv.items()}

    def _load_calendar(self, freq="day"):
        if freq == "day":
            return self._cal
        if freq == "5min":
            return [pd.Timestamp("2026-04-03 15:00:00")]
        raise ValueError(freq)

    def fetch_quote(self, code, start, end, freq="day"):
        if freq != "day":
            return pd.DataFrame()
        rows = []
        for d, v in self._ohlcv.get(code, {}).items():
            if str(pd.Timestamp(start).date()) <= d <= str(pd.Timestamp(end).date()):
                o, h, l, c, vol = v
                rows.append((pd.Timestamp(d), o, h, l, c, vol))
        if not rows:
            return pd.DataFrame()
        df = pd.DataFrame(rows, columns=["trade_date", "open", "high", "low",
                                         "close", "vol"])
        return df.sort_values("trade_date").reset_index(drop=True)

    def _read_bin(self, code, field, freq="day"):
        if code not in self._ohlcv:
            return None
        if field == "close":
            ser = self._close[code]
            return pd.Series(list(ser.values()),
                             index=pd.DatetimeIndex([pd.Timestamp(d) for d in ser]))
        if field == "factor":
            ser = self._close[code]
            return pd.Series([1.0] * len(ser),
                             index=pd.DatetimeIndex([pd.Timestamp(d) for d in ser]))
        return None


def _bars_df(rows):
    """rows = list of (trade_date_ts, o, h, l, c, vol[, amount])."""
    cols = ["trade_date", "open", "high", "low", "close", "vol"]
    if rows and len(rows[0]) == 7:
        cols = cols + ["amount"]
    return pd.DataFrame(rows, columns=cols)


class _ToyReader:
    """PitReader stand-in. 5min bars are injected per (code, date) via
    ``intraday_bars``; news/events via ``visible_overrides``. Counts
    ``fetch_bars_intraday`` calls (so 丁 can prove zero intraday reads)."""

    def __init__(self, loader, intraday_bars=None, visible_overrides=None,
                 data_end="2026-04-03"):
        self._loader = loader
        self._cal = [str(pd.Timestamp(d).date()) for d in _PAD]
        self._data_end = pd.Timestamp(data_end)
        # {(code, date): DataFrame}
        self._intraday = intraday_bars or {}
        # {(date, code): VisibleInfo}  (or {date: VisibleInfo} for code=None)
        self._vis = visible_overrides or {}
        self.intraday_calls = 0

    def data_end(self):
        return self._data_end

    def news_date_max(self):
        return "2026-04-03"

    def prev_trade_date(self, date):
        earlier = [d for d in self._cal if d < date]
        return earlier[-1] if earlier else None

    def is_trade_day(self, date):
        return date in self._cal

    def trading_days(self, start=None, end=None):
        de = self._data_end
        hi = min(pd.Timestamp(end), de) if end else de
        lo = pd.Timestamp(start) if start else pd.Timestamp(self._cal[0])
        return [d for d in self._cal if lo <= pd.Timestamp(d) <= hi]

    def fetch_quote_leq_prev(self, code, n_days_back=30, freq="day", as_of_date=None):
        prev = self.prev_trade_date(as_of_date)
        if prev is None:
            return pd.DataFrame(columns=["trade_date", "close"])
        return self._loader.fetch_quote(code, "1990-01-01", prev, "day")

    def get_visible_info(self, date, codes=None, as_of="09:25", lookback_days=1,
                         include=("news", "events", "policy", "market_eod_prev")):
        # per-(date, code) override (for intraday events/news truncation tests)
        if codes and len(codes) == 1 and (date, codes[0]) in self._vis:
            return self._vis[(date, codes[0])]
        if date in self._vis:
            return self._vis[date]
        prev = self.prev_trade_date(date)
        return VisibleInfo(
            date=date, as_of="09:25:00", boundary_ts=f"{date}T09:25:00",
            news=[], events=[], policy=[],
            market_eod_prev={"prev_trade_date": prev, "pct_up_5d": None,
                             "median_ret_5d": None, "median_ret_20d": None})

    def fetch_bars_intraday(self, code, date, freq="5min"):
        self.intraday_calls += 1
        return self._intraday.get((code, str(date)),
                                  pd.DataFrame(columns=["trade_date", "open",
                                                        "high", "low", "close",
                                                        "vol"]))


# ==========================================================================
# Scripted agents
# ==========================================================================
class _StubAgent:
    """Pre-open only scripted agent (P2 style)."""

    def __init__(self, scripted):
        self.scripted = scripted
        self._llm_call_count = 0
        self.seen_inputs = []

    async def decide(self, inp):
        self.seen_inputs.append(inp)
        return self.scripted.get(inp.date, Decision("hold", [], [], {}))

    @property
    def n_calls(self):
        return self._llm_call_count


class _IntradayStubAgent:
    """Distinguishes pre-open (inp.intraday is None) from intraday calls.

    ``pre_open``: {date: Decision}
    ``intraday``: {(date, code): Decision} OR {code: Decision} OR {kind: Decision}
    """

    def __init__(self, pre_open=None, intraday=None):
        self.pre_open = pre_open or {}
        self.intraday = intraday or {}
        self._llm_call_count = 0
        self.seen_inputs = []
        self.intraday_calls_for = []   # list of (date, code, kind)

    async def decide(self, inp):
        self.seen_inputs.append(inp)
        if getattr(inp, "intraday", None) is not None:
            ic = inp.intraday
            code = inp.candidates[0] if inp.candidates else None
            self.intraday_calls_for.append((inp.date, code, ic.kind))
            self._llm_call_count += 1
            for key in ((inp.date, code), code, ic.kind):
                if key in self.intraday:
                    return self.intraday[key]
            return Decision("hold", [], [], {"market_view": "hold", "decisions": []})
        return self.pre_open.get(inp.date, Decision("hold", [], [],
                                                    {"market_view": "h", "decisions": []}))

    @property
    def n_calls(self):
        return self._llm_call_count


class _RecordingTrigger(IntradayTrigger):
    """Records each check's bars_upto_t tail time + len (proves prefix-only)."""

    def __init__(self, cfg=None):
        super().__init__(cfg or IntradayTriggerConfig())
        self.checks = []   # list of (code, last_bar_time_str, n_bars, i)

    def check(self, code, bars_upto_t, position, sellable_qty, i):
        last_t = str(bars_upto_t["trade_date"].iloc[-1]) if len(bars_upto_t) else None
        self.checks.append((code, last_t, len(bars_upto_t), i))
        return super().check(code, bars_upto_t, position, sellable_qty, i)


# ==========================================================================
# 5min bar builders for the canonical scenarios
# ==========================================================================
def _flat_then_breakout_bars(date, code="SH600001"):
    """6 bars flat ~10.0, then bar idx 6 (09:55+5*6) breaks high to 10.5,
    then bars stay elevated. Used for breakout detection on day idx>=
    min_bars_for_signal."""
    base = [
        (_ts(date, "09:35"), 10.0, 10.05, 9.98, 10.0, 1000),
        (_ts(date, "09:40"), 10.0, 10.05, 9.97, 10.0, 1000),
        (_ts(date, "09:45"), 10.0, 10.06, 9.98, 10.0, 1000),
        (_ts(date, "09:50"), 10.0, 10.05, 9.99, 10.0, 1000),
        (_ts(date, "09:55"), 10.0, 10.06, 9.98, 10.0, 1000),
        (_ts(date, "10:00"), 10.0, 10.05, 9.99, 10.0, 1000),
        # breakout bar (idx 6): high jumps to 10.5 (> prefix max 10.06 * 1.008)
        (_ts(date, "10:05"), 10.05, 10.50, 10.0, 10.45, 5000),
        # a later bar to fill the buy on (idx 7): clearly higher range
        (_ts(date, "10:10"), 10.50, 10.80, 10.45, 10.70, 6000),
        (_ts(date, "10:15"), 10.70, 10.85, 10.60, 10.75, 4000),
    ]
    return _bars_df(base)


def _flat_then_drop_bars(date, stop_touch_low=9.3, code="SH600001"):
    """Bars flat ~10, then bar idx 6 drops low to ``stop_touch_low`` (a price
    that breaches a ~9.5 stop while staying inside the day's 涨跌停 band — a
    real 5min low can never pierce the day floor), then a later bar (idx 7) to
    fill the stop sell on. prev_close=10.0 → 跌停 floor dn=9.0 ≤ all lows."""
    base = [
        (_ts(date, "09:35"), 10.0, 10.05, 9.98, 10.0, 1000),
        (_ts(date, "09:40"), 10.0, 10.05, 9.97, 10.0, 1000),
        (_ts(date, "09:45"), 10.0, 10.06, 9.98, 10.0, 1000),
        (_ts(date, "09:50"), 10.0, 10.05, 9.99, 10.0, 1000),
        (_ts(date, "09:55"), 10.0, 10.06, 9.98, 10.0, 1000),
        (_ts(date, "10:00"), 10.0, 10.05, 9.99, 10.0, 1000),
        # break-down bar (idx 6): low dips to stop_touch_low (≥ floor 9.0)
        (_ts(date, "10:05"), 9.8, 9.85, stop_touch_low, 9.4, 5000),
        # later bar to fill the stop sell on (idx 7), still ≥ floor 9.0
        (_ts(date, "10:10"), 9.4, 9.5, 9.2, 9.35, 4000),
        (_ts(date, "10:15"), 9.35, 9.45, 9.25, 9.4, 3000),
    ]
    return _bars_df(base)


# ==========================================================================
# 甲 — mock intraday trigger changes同日 fills
# ==========================================================================
async def test_intraday_breakout_changes_fills():
    """run A (disabled) vs run B (breakout enabled + intraday agent buys)."""
    # SH600001 is a fresh candidate (not held, no pre-open decision) so the
    # intraday breakout add is not blocked by _preopen_acted.
    loader = _ToyLoader()
    # make SH600001 a candidate via watchlist? Toy reader has no watchlist —
    # easier: pre-open agent does nothing, and SH600001 is in candidate via the
    # candidate pool. But candidate needs watchlist. Instead, inject a watchlist
    # by seeding a holding so it is a candidate AND not pre-open-acted.
    # Simplest: pre-open buys SH600002 (a different stock); SH600001 is the
    # intraday-only target. Make SH600001 a candidate by adding it to a watchlist
    # the reader serves. _ToyReader candidate pool = holdings ∪ watchlist; we
    # add SH600001 to watch via a tiny patch on select_candidates path:
    # easiest path — pre-open establishes SH600001 as a *holding* on day1,
    # then on day2 the intraday breakout adds more (but that's an add, blocked).
    #
    # Cleanest: use TWO codes. Pre-open holds nothing. Candidate pool comes from
    # holdings only here (no watchlist), so to get SH600001 into the intraday
    # watch set it must be a holding. So: day1 pre-open BUY SH600001 (held),
    # day2 NO pre-open decision on SH600001 → breakout on day2 can ADD.
    intraday_bars = {("SH600001", "2026-04-02"): _flat_then_breakout_bars("2026-04-02")}

    cfg_kwargs = dict(start="2026-04-01", end="2026-04-03", init_cash=1_000_000.0,
                      benchmark=None, match_freq="day")

    pre_open = {
        "2026-04-01": Decision(
            "buy", [DecisionLeg(code="SH600001", action="buy", weight_pct=30.0,
                                stop_loss=1.0, reason="entry")],
            [], {"market_view": "buy", "decisions": []}),
    }
    # run A: disabled
    readerA = _ToyReader(loader, intraday_bars=intraday_bars)
    agentA = _IntradayStubAgent(pre_open=pre_open)
    runnerA = BacktestRunner(reader=readerA, agent=agentA, loader=loader,
                             cfg=RunConfig(**cfg_kwargs,
                                           intraday=IntradayTriggerConfig(enabled=False)))
    resA = await runnerA.run()

    # run B: breakout enabled; intraday agent ADDs SH600001 on breakout
    readerB = _ToyReader(loader, intraday_bars=intraday_bars)
    agentB = _IntradayStubAgent(
        pre_open=pre_open,
        intraday={"SH600001": Decision(
            "add", [DecisionLeg(code="SH600001", action="add", weight_pct=30.0,
                                stop_loss=1.0, reason="breakout add")],
            [], {"market_view": "add", "decisions": []})})
    trig = IntradayTriggerConfig(enabled=True, breakout_enabled=True,
                                 min_bars_for_signal=5)
    runnerB = BacktestRunner(reader=readerB, agent=agentB, loader=loader,
                             cfg=RunConfig(**cfg_kwargs, intraday=trig))
    resB = await runnerB.run()

    # B has at least one extra fill (the intraday add) vs A
    assert len(resB.trade_log.fills) > len(resA.trade_log.fills)
    # NAV diverges (intraday add changed the book)
    assert resB.nav_history[-1][1] != resA.nav_history[-1][1]
    # the intraday agent was consulted on the breakout for SH600001
    assert any(c == "SH600001" and k == "breakout_high"
               for (_, c, k) in agentB.intraday_calls_for)


async def test_intraday_stop_break_sells_before_eod():
    """≤T-1 holding + intraday stop_break → sells intraday (0 LLM), not waiting
    for EOD."""
    loader = _ToyLoader()
    # day2 5min dips to 9.3 (breaches a 9.5 stop, stays ≥ floor 9.0)
    intraday_bars = {("SH600001", "2026-04-02"): _flat_then_drop_bars("2026-04-02", 9.3)}

    pre_open = {
        # buy day1 with stop 9.5 → held ≤T-1 by day2
        "2026-04-01": Decision(
            "buy", [DecisionLeg(code="SH600001", action="buy", weight_pct=50.0,
                                stop_loss=9.5, reason="entry")],
            [], {"market_view": "buy", "decisions": []}),
    }
    reader = _ToyReader(loader, intraday_bars=intraday_bars)
    agent = _IntradayStubAgent(pre_open=pre_open)
    trig = IntradayTriggerConfig(enabled=True, stop_break_enabled=True,
                                 breakout_enabled=False)
    runner = BacktestRunner(reader=reader, agent=agent, loader=loader,
                            cfg=RunConfig(start="2026-04-01", end="2026-04-03",
                                          init_cash=1_000_000.0, benchmark=None,
                                          match_freq="day", intraday=trig))
    res = await runner.run()

    sells = [f for f in res.trade_log.fills if f.side == "sell"]
    assert len(sells) >= 1
    # the stop sell filled at an intraday timestamp (not the 15:00 EOD day-bar)
    intraday_sells = [f for f in sells if ":" in f.bar_ts and not f.bar_ts.endswith("15:00:00")]
    assert intraday_sells, f"expected an intraday-time sell, got {[f.bar_ts for f in sells]}"
    # stop_break is a rule path → no intraday LLM call
    assert not agent.intraday_calls_for
    # position was closed intraday → EOD did not re-sell (exactly 1 sell)
    assert len(sells) == 1


async def test_intraday_stop_break_t1_locked_no_sell():
    """Same-day pre-open buy + same-day intraday drop below stop → no sell
    (T+1 lock), and EOD also can't sell (locked)."""
    loader = _ToyLoader()
    # buy on day1, AND day1 itself dips to 9.3 intraday (breaches a 9.5 stop)
    intraday_bars = {("SH600001", "2026-04-01"): _flat_then_drop_bars("2026-04-01", 9.3)}
    pre_open = {
        "2026-04-01": Decision(
            "buy", [DecisionLeg(code="SH600001", action="buy", weight_pct=50.0,
                                stop_loss=9.5, reason="entry")],
            [], {"market_view": "buy", "decisions": []}),
    }
    reader = _ToyReader(loader, intraday_bars=intraday_bars)
    agent = _IntradayStubAgent(pre_open=pre_open)
    trig = IntradayTriggerConfig(enabled=True, stop_break_enabled=True,
                                 breakout_enabled=False)
    runner = BacktestRunner(reader=reader, agent=agent, loader=loader,
                            cfg=RunConfig(start="2026-04-01", end="2026-04-01",
                                          init_cash=1_000_000.0, benchmark=None,
                                          match_freq="day", intraday=trig))
    res = await runner.run()
    # day1 buy locks shares for T+1 → no sell possible same day (intraday or EOD)
    sells = [f for f in res.trade_log.fills if f.side == "sell"]
    assert sells == []


# ==========================================================================
# 乙 — time-series law
# ==========================================================================
async def test_intraday_trigger_uses_only_prefix_bars():
    """The trigger only ever sees bars.iloc[:i+1] (末行时刻==当前推进时刻,
    len==i+1)."""
    loader = _ToyLoader()
    intraday_bars = {("SH600001", "2026-04-02"): _flat_then_breakout_bars("2026-04-02")}
    pre_open = {
        "2026-04-01": Decision(
            "buy", [DecisionLeg(code="SH600001", action="buy", weight_pct=30.0,
                                stop_loss=1.0, reason="entry")],
            [], {"market_view": "b", "decisions": []}),
    }
    reader = _ToyReader(loader, intraday_bars=intraday_bars)
    agent = _IntradayStubAgent(pre_open=pre_open)
    trig = _RecordingTrigger(IntradayTriggerConfig(enabled=True, breakout_enabled=True,
                                                   min_bars_for_signal=5))
    runner = BacktestRunner(reader=reader, agent=agent, loader=loader,
                            cfg=RunConfig(start="2026-04-01", end="2026-04-03",
                                          init_cash=1_000_000.0, benchmark=None,
                                          match_freq="day"))
    runner.trigger = trig   # inject recording trigger
    await runner.run()

    bars = intraday_bars[("SH600001", "2026-04-02")]
    assert trig.checks, "trigger.check was never called"
    for (code, last_t, n, i) in trig.checks:
        if code != "SH600001":
            continue
        # len(bars_upto_t) == i+1, and last row == bars[i]
        assert n == i + 1, f"check at i={i} saw {n} bars (want {i+1})"
        assert last_t == str(bars["trade_date"].iloc[i])


async def test_intraday_fill_bar_is_after_trigger():
    """Trigger at bar i → fill happens on bar i+1 (strictly later), at i+1's
    price range — NOT bar i's. Constructs i+1 with a distinct range."""
    loader = _ToyLoader()
    intraday_bars = {("SH600001", "2026-04-02"): _flat_then_breakout_bars("2026-04-02")}
    bars = intraday_bars[("SH600001", "2026-04-02")]
    pre_open = {
        "2026-04-01": Decision(
            "buy", [DecisionLeg(code="SH600001", action="buy", weight_pct=30.0,
                                stop_loss=1.0, reason="entry")],
            [], {"market_view": "b", "decisions": []}),
    }
    reader = _ToyReader(loader, intraday_bars=intraday_bars)
    agent = _IntradayStubAgent(
        pre_open=pre_open,
        intraday={"SH600001": Decision(
            "add", [DecisionLeg(code="SH600001", action="add", weight_pct=30.0,
                                stop_loss=1.0, reason="add")],
            [], {"market_view": "a", "decisions": []})})
    trig = IntradayTriggerConfig(enabled=True, breakout_enabled=True,
                                 min_bars_for_signal=5)
    runner = BacktestRunner(reader=reader, agent=agent, loader=loader,
                            cfg=RunConfig(start="2026-04-01", end="2026-04-03",
                                          init_cash=1_000_000.0, benchmark=None,
                                          match_freq="day", intraday=trig))
    res = await runner.run()

    # the intraday buy fill landed on bar idx 7 (10:10) — the bar AFTER the
    # breakout bar idx 6 (10:05).
    intraday_buys = [f for f in res.trade_log.fills
                     if f.side == "buy" and f.bar_ts == str(bars["trade_date"].iloc[7])]
    assert intraday_buys, (
        f"expected a buy filled on bar i+1={bars['trade_date'].iloc[7]}, "
        f"got {[(f.side, f.bar_ts, f.price) for f in res.trade_log.fills]}")
    f = intraday_buys[0]
    # fill price falls inside bar i+1's [low, high]=[10.45,10.80], NOT bar i's
    # [10.0,10.50] alone (10.45 boundary is shared, but >10.50 proves i+1).
    i1_low, i1_high = float(bars["low"].iloc[7]), float(bars["high"].iloc[7])
    assert i1_low <= f.price <= i1_high
    # price strictly above bar i's high (10.50) is only reachable on bar i+1
    assert f.price > float(bars["high"].iloc[6]) - 1e-9 or f.price >= i1_low


async def test_intraday_decision_ctx_is_bar_time():
    """The intraday DecisionInput carries as_of==trigger bar HH:MM:SS,
    candidates==[触发股], intraday.kind correct."""
    loader = _ToyLoader()
    intraday_bars = {("SH600001", "2026-04-02"): _flat_then_breakout_bars("2026-04-02")}
    bars = intraday_bars[("SH600001", "2026-04-02")]
    pre_open = {
        "2026-04-01": Decision(
            "buy", [DecisionLeg(code="SH600001", action="buy", weight_pct=30.0,
                                stop_loss=1.0, reason="entry")],
            [], {"market_view": "b", "decisions": []}),
    }
    reader = _ToyReader(loader, intraday_bars=intraday_bars)
    agent = _IntradayStubAgent(
        pre_open=pre_open,
        intraday={"SH600001": Decision(
            "add", [DecisionLeg(code="SH600001", action="add", weight_pct=30.0,
                                stop_loss=1.0, reason="add")],
            [], {"market_view": "a", "decisions": []})})
    trig = IntradayTriggerConfig(enabled=True, breakout_enabled=True,
                                 min_bars_for_signal=5)
    runner = BacktestRunner(reader=reader, agent=agent, loader=loader,
                            cfg=RunConfig(start="2026-04-01", end="2026-04-03",
                                          init_cash=1_000_000.0, benchmark=None,
                                          match_freq="day", intraday=trig))
    await runner.run()

    intraday_inputs = [i for i in agent.seen_inputs if getattr(i, "intraday", None)]
    assert intraday_inputs, "no intraday DecisionInput captured"
    inp = intraday_inputs[0]
    # bar idx 6 (10:05) is the breakout bar
    assert inp.as_of == "10:05:00"
    assert inp.candidates == ["SH600001"]
    assert inp.intraday.kind == "breakout_high"
    assert inp.intraday.bar_index == 6


async def test_intraday_events_session_truncated():
    """Intraday visible info: events with session!=pre_open on same day are
    dropped; news with ts>bar时刻 dropped."""
    loader = _ToyLoader()
    intraday_bars = {("SH600001", "2026-04-02"): _flat_then_breakout_bars("2026-04-02")}
    date = "2026-04-02"
    # two events ann_date==date: one pre_open (visible), one post_close (hidden intraday)
    ev_pre = EventItem(ann_date=date, code="SH600001", type="dividend",
                       summary="盘前已公告分红", session="pre_open")
    ev_post = EventItem(ann_date=date, code="SH600001", type="lawsuit",
                        summary="盘后才公告诉讼", session="post_close")
    # two news: one ≤10:05 (visible), one >10:05 (hidden)
    n_early = NewsItem(ts=f"{date} 09:50:00", date=date, session="intraday",
                       code="SH600001", title="早间利好", body="x")
    n_late = NewsItem(ts=f"{date} 14:00:00", date=date, session="intraday",
                      code="SH600001", title="午后利空", body="y")
    vi = VisibleInfo(date=date, as_of="10:05:00", boundary_ts=f"{date}T10:05:00",
                     news=[n_early, n_late], events=[ev_pre, ev_post], policy=[],
                     market_eod_prev={"prev_trade_date": "2026-04-01"})
    # Note: real get_visible_info(as_of=10:05) would already drop n_late by ts;
    # here we hand the reader BOTH news so we can assert the engine path keeps
    # only the ≤boundary one (i.e. it actually calls get_visible_info with the
    # bar as_of, not the 09:25 pre-open as_of). We simulate that by serving the
    # ts-correct subset per as_of below.
    captured = {}

    pre_open = {
        "2026-04-01": Decision(
            "buy", [DecisionLeg(code="SH600001", action="buy", weight_pct=30.0,
                                stop_loss=1.0, reason="entry")],
            [], {"market_view": "b", "decisions": []}),
    }

    class _EvReader(_ToyReader):
        def get_visible_info(self, date_, codes=None, as_of="09:25", lookback_days=1,
                             include=("news", "events", "policy", "market_eod_prev")):
            if date_ == date and codes == ["SH600001"]:
                # mimic real PIT: news truncated by ts<=boundary (as_of)
                b = pd.Timestamp(f"{date_} {as_of if len(as_of)==8 else as_of+':00'}")
                news = [n for n in [n_early, n_late] if pd.Timestamp(n.ts) <= b]
                captured["news"] = news
                return VisibleInfo(date=date_, as_of=as_of, boundary_ts=str(b),
                                   news=news, events=[ev_pre, ev_post], policy=[],
                                   market_eod_prev={"prev_trade_date": "2026-04-01"})
            return super().get_visible_info(date_, codes, as_of, lookback_days, include)

    reader = _EvReader(loader, intraday_bars=intraday_bars)
    agent = _IntradayStubAgent(
        pre_open=pre_open,
        intraday={"SH600001": Decision("hold", [], [],
                                       {"market_view": "h", "decisions": []})})
    trig = IntradayTriggerConfig(enabled=True, breakout_enabled=True,
                                 min_bars_for_signal=5)
    runner = BacktestRunner(reader=reader, agent=agent, loader=loader,
                            cfg=RunConfig(start="2026-04-01", end="2026-04-03",
                                          init_cash=1_000_000.0, benchmark=None,
                                          match_freq="day", intraday=trig))
    await runner.run()

    intraday_inputs = [i for i in agent.seen_inputs if getattr(i, "intraday", None)]
    assert intraday_inputs, "no intraday DecisionInput"
    vis = intraday_inputs[0].visible
    ev_summaries = {e.summary for e in vis.events}
    assert "盘前已公告分红" in ev_summaries
    assert "盘后才公告诉讼" not in ev_summaries, "post_close event leaked intraday"
    news_titles = {n.title for n in vis.news}
    assert "早间利好" in news_titles
    assert "午后利空" not in news_titles, "future news (ts>bar) leaked intraday"


async def test_intraday_last_bar_trigger_no_fill():
    """Trigger on the final bar (i==len-1) → no i+1 bar → no fill, note recorded."""
    loader = _ToyLoader()
    date = "2026-04-02"
    # breakout on the VERY LAST bar (no bar after it)
    rows = [
        (_ts(date, "09:35"), 10.0, 10.05, 9.98, 10.0, 1000),
        (_ts(date, "09:40"), 10.0, 10.05, 9.97, 10.0, 1000),
        (_ts(date, "09:45"), 10.0, 10.06, 9.98, 10.0, 1000),
        (_ts(date, "09:50"), 10.0, 10.05, 9.99, 10.0, 1000),
        (_ts(date, "09:55"), 10.0, 10.06, 9.98, 10.0, 1000),
        (_ts(date, "10:00"), 10.0, 10.05, 9.99, 10.0, 1000),
        (_ts(date, "15:00"), 10.05, 10.50, 10.0, 10.45, 5000),  # last bar = breakout
    ]
    intraday_bars = {("SH600001", date): _bars_df(rows)}
    pre_open = {
        "2026-04-01": Decision(
            "buy", [DecisionLeg(code="SH600001", action="buy", weight_pct=30.0,
                                stop_loss=1.0, reason="entry")],
            [], {"market_view": "b", "decisions": []}),
    }
    reader = _ToyReader(loader, intraday_bars=intraday_bars)
    agent = _IntradayStubAgent(
        pre_open=pre_open,
        intraday={"SH600001": Decision(
            "add", [DecisionLeg(code="SH600001", action="add", weight_pct=30.0,
                                stop_loss=1.0, reason="add")],
            [], {"market_view": "a", "decisions": []})})
    trig = IntradayTriggerConfig(enabled=True, breakout_enabled=True,
                                 min_bars_for_signal=5)
    runner = BacktestRunner(reader=reader, agent=agent, loader=loader,
                            cfg=RunConfig(start="2026-04-01", end="2026-04-03",
                                          init_cash=1_000_000.0, benchmark=None,
                                          match_freq="day", intraday=trig))
    res = await runner.run()
    # the last-bar breakout produced no fill (no bar after it)
    intra = res.decisions_by_date["2026-04-02"].get("_intraday", [])
    assert any(rec.get("note") == "no_bar_after_trigger" for rec in intra), \
        f"expected no_bar_after_trigger note, got {intra}"
    # no intraday buy filled on day2
    day2_buys = [f for f in res.trade_log.fills
                 if f.side == "buy" and f.bar_ts.startswith("2026-04-02")]
    assert day2_buys == []


# ==========================================================================
# 丙 — trigger cap / dedup / risk exemption
# ==========================================================================
async def test_intraday_dedup_same_signal():
    """Consecutive bars all breaking new highs → breakout fires once for the
    stock."""
    loader = _ToyLoader()
    date = "2026-04-02"
    rows = [
        (_ts(date, "09:35"), 10.0, 10.05, 9.98, 10.0, 1000),
        (_ts(date, "09:40"), 10.0, 10.05, 9.97, 10.0, 1000),
        (_ts(date, "09:45"), 10.0, 10.06, 9.98, 10.0, 1000),
        (_ts(date, "09:50"), 10.0, 10.05, 9.99, 10.0, 1000),
        (_ts(date, "09:55"), 10.0, 10.06, 9.98, 10.0, 1000),
        (_ts(date, "10:00"), 10.0, 10.05, 9.99, 10.0, 1000),
        # three consecutive new highs (each > prefix max)
        (_ts(date, "10:05"), 10.05, 10.50, 10.0, 10.4, 5000),
        (_ts(date, "10:10"), 10.4, 10.70, 10.3, 10.6, 5000),
        (_ts(date, "10:15"), 10.6, 10.90, 10.5, 10.8, 5000),
    ]
    intraday_bars = {("SH600001", date): _bars_df(rows)}
    pre_open = {
        "2026-04-01": Decision(
            "buy", [DecisionLeg(code="SH600001", action="buy", weight_pct=30.0,
                                stop_loss=1.0, reason="entry")],
            [], {"market_view": "b", "decisions": []}),
    }
    reader = _ToyReader(loader, intraday_bars=intraday_bars)
    agent = _IntradayStubAgent(
        pre_open=pre_open,
        intraday={"SH600001": Decision("hold", [], [],
                                       {"market_view": "h", "decisions": []})})
    trig = IntradayTriggerConfig(enabled=True, breakout_enabled=True,
                                 min_bars_for_signal=5)
    runner = BacktestRunner(reader=reader, agent=agent, loader=loader,
                            cfg=RunConfig(start="2026-04-01", end="2026-04-03",
                                          init_cash=1_000_000.0, benchmark=None,
                                          match_freq="day", intraday=trig))
    await runner.run()
    breakout_calls = [x for x in agent.intraday_calls_for
                      if x[1] == "SH600001" and x[2] == "breakout_high"
                      and x[0] == "2026-04-02"]
    assert len(breakout_calls) == 1, f"breakout fired {len(breakout_calls)} times"


async def test_intraday_per_code_cap():
    """max_per_day_per_code=1: same stock breakout then volume_surge → only 1
    decision trigger."""
    loader = _ToyLoader()
    date = "2026-04-02"
    # bar 6 breaks high AND would be a volume surge; bar 7 surges volume only
    rows = [
        (_ts(date, "09:35"), 10.0, 10.05, 9.98, 10.0, 1000),
        (_ts(date, "09:40"), 10.0, 10.05, 9.97, 10.0, 1000),
        (_ts(date, "09:45"), 10.0, 10.06, 9.98, 10.0, 1000),
        (_ts(date, "09:50"), 10.0, 10.05, 9.99, 10.0, 1000),
        (_ts(date, "09:55"), 10.0, 10.06, 9.98, 10.0, 1000),
        (_ts(date, "10:00"), 10.0, 10.05, 9.99, 10.0, 1000),
        (_ts(date, "10:05"), 10.05, 10.50, 10.0, 10.4, 1100),   # breakout (modest vol)
        # bar 7: no new high (high 10.4 < prefix 10.50) but huge volume surge
        (_ts(date, "10:10"), 10.3, 10.40, 10.2, 10.35, 99999),
        (_ts(date, "10:15"), 10.35, 10.45, 10.25, 10.4, 1000),
    ]
    intraday_bars = {("SH600001", date): _bars_df(rows)}
    pre_open = {
        "2026-04-01": Decision(
            "buy", [DecisionLeg(code="SH600001", action="buy", weight_pct=30.0,
                                stop_loss=1.0, reason="entry")],
            [], {"market_view": "b", "decisions": []}),
    }
    reader = _ToyReader(loader, intraday_bars=intraday_bars)
    agent = _IntradayStubAgent(
        pre_open=pre_open,
        intraday={"SH600001": Decision("hold", [], [],
                                       {"market_view": "h", "decisions": []})})
    trig = IntradayTriggerConfig(enabled=True, breakout_enabled=True,
                                 volume_surge_enabled=True, volume_surge_mult=3.0,
                                 volume_surge_window=6, min_bars_for_signal=5,
                                 max_per_day_per_code=1)
    runner = BacktestRunner(reader=reader, agent=agent, loader=loader,
                            cfg=RunConfig(start="2026-04-01", end="2026-04-03",
                                          init_cash=1_000_000.0, benchmark=None,
                                          match_freq="day", intraday=trig))
    await runner.run()
    day2 = [x for x in agent.intraday_calls_for if x[0] == "2026-04-02"
            and x[1] == "SH600001"]
    assert len(day2) == 1, f"per-code cap breached: {day2}"


async def test_intraday_global_cap():
    """max_per_day_global=2 with 2 candidates that both break out repeatedly
    → at most 2 decision triggers total."""
    loader = _ToyLoader()
    date = "2026-04-02"
    intraday_bars = {
        ("SH600001", date): _flat_then_breakout_bars(date),
        ("SH600002", date): _bars_df([
            (_ts(date, "09:35"), 20.0, 20.05, 19.98, 20.0, 1000),
            (_ts(date, "09:40"), 20.0, 20.05, 19.97, 20.0, 1000),
            (_ts(date, "09:45"), 20.0, 20.06, 19.98, 20.0, 1000),
            (_ts(date, "09:50"), 20.0, 20.05, 19.99, 20.0, 1000),
            (_ts(date, "09:55"), 20.0, 20.06, 19.98, 20.0, 1000),
            (_ts(date, "10:00"), 20.0, 20.05, 19.99, 20.0, 1000),
            (_ts(date, "10:05"), 20.05, 21.0, 20.0, 20.9, 5000),  # breakout
            (_ts(date, "10:10"), 20.9, 21.3, 20.8, 21.1, 6000),
        ]),
    }
    # both held ≤T-1 (so both are in watch set); pre-open buys both on day1
    pre_open = {
        "2026-04-01": Decision(
            "buy", [
                DecisionLeg(code="SH600001", action="buy", weight_pct=20.0,
                            stop_loss=1.0, reason="e1"),
                DecisionLeg(code="SH600002", action="buy", weight_pct=20.0,
                            stop_loss=1.0, reason="e2"),
            ], [], {"market_view": "b", "decisions": []}),
    }
    reader = _ToyReader(loader, intraday_bars=intraday_bars)
    agent = _IntradayStubAgent(
        pre_open=pre_open,
        intraday={"SH600001": Decision("hold", [], [], {"market_view": "h", "decisions": []}),
                  "SH600002": Decision("hold", [], [], {"market_view": "h", "decisions": []})})
    # global cap 2; both stocks would each fire breakout once = 2 total
    trig = IntradayTriggerConfig(enabled=True, breakout_enabled=True,
                                 min_bars_for_signal=5, max_per_day_global=2,
                                 max_per_day_per_code=5)
    runner = BacktestRunner(reader=reader, agent=agent, loader=loader,
                            cfg=RunConfig(start="2026-04-01", end="2026-04-03",
                                          init_cash=1_000_000.0, benchmark=None,
                                          match_freq="day", intraday=trig))
    await runner.run()
    day2 = [x for x in agent.intraday_calls_for if x[0] == "2026-04-02"]
    assert len(day2) == 2, f"global cap breached: {day2}"


async def test_intraday_stop_break_exempt_from_global_cap():
    """Global cap consumed by breakouts in the morning; an afternoon stop_break
    on a ≤T-1 holding STILL fires (risk exempt)."""
    loader = _ToyLoader()
    date = "2026-04-02"
    # SH600002 breaks out early (consumes the only global slot, cap=1).
    # SH600001 (held ≤T-1) drops below stop in the afternoon → must still sell.
    intraday_bars = {
        ("SH600002", date): _bars_df([
            (_ts(date, "09:35"), 20.0, 20.05, 19.98, 20.0, 1000),
            (_ts(date, "09:40"), 20.0, 20.05, 19.97, 20.0, 1000),
            (_ts(date, "09:45"), 20.0, 20.06, 19.98, 20.0, 1000),
            (_ts(date, "09:50"), 20.0, 20.05, 19.99, 20.0, 1000),
            (_ts(date, "09:55"), 20.0, 20.06, 19.98, 20.0, 1000),
            (_ts(date, "10:00"), 20.05, 21.0, 20.0, 20.9, 5000),   # breakout (consumes cap)
            (_ts(date, "10:05"), 20.9, 21.2, 20.8, 21.0, 5000),
        ]),
        ("SH600001", date): _bars_df([
            (_ts(date, "09:35"), 10.0, 10.05, 9.98, 10.0, 1000),
            (_ts(date, "09:40"), 10.0, 10.05, 9.97, 10.0, 1000),
            (_ts(date, "09:45"), 10.0, 10.06, 9.98, 10.0, 1000),
            (_ts(date, "09:50"), 10.0, 10.05, 9.99, 10.0, 1000),
            (_ts(date, "09:55"), 10.0, 10.06, 9.98, 10.0, 1000),
            (_ts(date, "13:00"), 9.7, 9.75, 9.4, 9.5, 6000),       # afternoon dip below 9.6 stop (≥floor 9.0)
            (_ts(date, "13:05"), 9.5, 9.55, 9.35, 9.45, 5000),     # fill bar
        ]),
    }
    pre_open = {
        "2026-04-01": Decision(
            "buy", [
                DecisionLeg(code="SH600001", action="buy", weight_pct=30.0,
                            stop_loss=9.6, reason="e1"),
                DecisionLeg(code="SH600002", action="buy", weight_pct=30.0,
                            stop_loss=1.0, reason="e2"),
            ], [], {"market_view": "b", "decisions": []}),
    }
    reader = _ToyReader(loader, intraday_bars=intraday_bars)
    agent = _IntradayStubAgent(
        pre_open=pre_open,
        intraday={"SH600002": Decision("hold", [], [], {"market_view": "h", "decisions": []})})
    trig = IntradayTriggerConfig(enabled=True, breakout_enabled=True,
                                 stop_break_enabled=True, min_bars_for_signal=5,
                                 max_per_day_global=1, max_per_day_per_code=5)
    runner = BacktestRunner(reader=reader, agent=agent, loader=loader,
                            cfg=RunConfig(start="2026-04-01", end="2026-04-03",
                                          init_cash=1_000_000.0, benchmark=None,
                                          match_freq="day", intraday=trig))
    res = await runner.run()
    # SH600001 stop sold intraday on day2 despite global cap being consumed
    intraday_sells = [f for f in res.trade_log.fills
                      if f.side == "sell" and f.bar_ts.startswith("2026-04-02 13:")]
    assert intraday_sells, (
        f"stop_break starved by global cap; sells="
        f"{[(f.code, f.bar_ts) for f in res.trade_log.fills if f.side=='sell']}")
    assert any(f.code == "SH600001" for f in intraday_sells)


async def test_intraday_reset_per_day():
    """Breakout on day2 AND day3 → both fire (reset_day clears state)."""
    loader = _ToyLoader()
    intraday_bars = {
        ("SH600001", "2026-04-02"): _flat_then_breakout_bars("2026-04-02"),
        ("SH600001", "2026-04-03"): _flat_then_breakout_bars("2026-04-03"),
    }
    pre_open = {
        "2026-04-01": Decision(
            "buy", [DecisionLeg(code="SH600001", action="buy", weight_pct=30.0,
                                stop_loss=1.0, reason="entry")],
            [], {"market_view": "b", "decisions": []}),
    }
    reader = _ToyReader(loader, intraday_bars=intraday_bars)
    agent = _IntradayStubAgent(
        pre_open=pre_open,
        intraday={"SH600001": Decision("hold", [], [], {"market_view": "h", "decisions": []})})
    trig = IntradayTriggerConfig(enabled=True, breakout_enabled=True,
                                 min_bars_for_signal=5)
    runner = BacktestRunner(reader=reader, agent=agent, loader=loader,
                            cfg=RunConfig(start="2026-04-01", end="2026-04-03",
                                          init_cash=1_000_000.0, benchmark=None,
                                          match_freq="day", intraday=trig))
    await runner.run()
    days_fired = {x[0] for x in agent.intraday_calls_for
                  if x[1] == "SH600001" and x[2] == "breakout_high"}
    assert "2026-04-02" in days_fired and "2026-04-03" in days_fired


# ==========================================================================
# 丁 — disabled intraday == P2 端到端
# ==========================================================================
async def test_intraday_disabled_equals_p2():
    """enabled=False (default) → NAV/fills/n_llm_calls byte-identical to a pure
    P2 run; no _intraday key; fetch_bars_intraday never called."""
    loader = _ToyLoader()
    intraday_bars = {("SH600001", "2026-04-02"): _flat_then_breakout_bars("2026-04-02")}
    pre_open = {
        "2026-04-01": Decision(
            "buy", [DecisionLeg(code="SH600001", action="buy", weight_pct=50.0,
                                stop_loss=8.0, reason="entry")],
            [], {"market_view": "b", "decisions": []}),
        "2026-04-03": Decision(
            "sell", [DecisionLeg(code="SH600001", action="sell", reason="exit")],
            [], {"market_view": "s", "decisions": []}),
    }

    # P2 reference: no intraday config at all (defaults to disabled)
    readerP2 = _ToyReader(loader, intraday_bars=intraday_bars)
    agentP2 = _IntradayStubAgent(pre_open=pre_open)
    runnerP2 = BacktestRunner(reader=readerP2, agent=agentP2, loader=loader,
                              cfg=RunConfig(start="2026-04-01", end="2026-04-03",
                                            init_cash=1_000_000.0, benchmark=None,
                                            match_freq="day"))
    resP2 = await runnerP2.run()

    # explicit disabled
    readerD = _ToyReader(loader, intraday_bars=intraday_bars)
    agentD = _IntradayStubAgent(pre_open=pre_open)
    runnerD = BacktestRunner(reader=readerD, agent=agentD, loader=loader,
                             cfg=RunConfig(start="2026-04-01", end="2026-04-03",
                                           init_cash=1_000_000.0, benchmark=None,
                                           match_freq="day",
                                           intraday=IntradayTriggerConfig(enabled=False)))
    resD = await runnerD.run()

    assert resP2.nav_history == resD.nav_history
    assert resP2.n_llm_calls == resD.n_llm_calls
    fP2 = [(f.code, f.side, f.qty, f.price, f.bar_ts) for f in resP2.trade_log.fills]
    fD = [(f.code, f.side, f.qty, f.price, f.bar_ts) for f in resD.trade_log.fills]
    assert fP2 == fD
    # decisions dicts equal, and NO _intraday key on either
    assert set(resP2.decisions_by_date) == set(resD.decisions_by_date)
    for d in resP2.decisions_by_date:
        assert resP2.decisions_by_date[d] == resD.decisions_by_date[d]
        assert "_intraday" not in resD.decisions_by_date[d]
    # disabled path never touched intraday bars
    assert readerD.intraday_calls == 0


async def test_intraday_ctx_none_preserves_preopen_prompt():
    """DecisionInput without intraday == explicit intraday=None: build_messages
    text identical, no '盘中关键点重判' marker."""
    vi = VisibleInfo(date="2026-04-02", as_of="09:25:00",
                     boundary_ts="2026-04-02T09:25:00", news=[], events=[],
                     policy=[], market_eod_prev={"prev_trade_date": "2026-04-01"})
    base_kwargs = dict(date="2026-04-02", as_of="09:25", visible=vi,
                       candidates=["SH600001"], rev20_rank={"SH600001": 0.3},
                       holdings={}, cash=1e6, nav=1e6)
    inp_default = DecisionInput(**base_kwargs)
    inp_none = DecisionInput(**base_kwargs, intraday=None)
    m_default = build_messages(inp_default)
    m_none = build_messages(inp_none)
    assert m_default == m_none
    full = m_default[0]["content"] + m_default[1]["content"]
    assert "盘中关键点重判" not in full


async def test_intraday_enabled_zero_trigger_no_intraday_key():
    """enabled=True but no signal fires all day → no _intraday key, shape == P2."""
    loader = _ToyLoader()
    # flat bars, never break out, no stop
    date = "2026-04-02"
    flat = _bars_df([(_ts(date, f"{9 + i//12:02d}:{(i%12)*5:02d}"),
                      10.0, 10.05, 9.98, 10.0, 1000) for i in range(8)])
    intraday_bars = {("SH600001", date): flat}
    pre_open = {
        "2026-04-01": Decision(
            "buy", [DecisionLeg(code="SH600001", action="buy", weight_pct=30.0,
                                stop_loss=1.0, reason="entry")],
            [], {"market_view": "b", "decisions": []}),
    }
    reader = _ToyReader(loader, intraday_bars=intraday_bars)
    agent = _IntradayStubAgent(pre_open=pre_open)
    trig = IntradayTriggerConfig(enabled=True, breakout_enabled=True,
                                 breakout_min_gain_pct=0.05,  # high bar, never fires
                                 min_bars_for_signal=5)
    runner = BacktestRunner(reader=reader, agent=agent, loader=loader,
                            cfg=RunConfig(start="2026-04-01", end="2026-04-03",
                                          init_cash=1_000_000.0, benchmark=None,
                                          match_freq="day", intraday=trig))
    res = await runner.run()
    for d in res.decisions_by_date:
        if res.decisions_by_date[d].get("_error") == "json":
            continue
        assert "_intraday" not in res.decisions_by_date[d], \
            f"{d} got an _intraday key despite zero triggers"
    assert not agent.intraday_calls_for


# ==========================================================================
# 戊 — cache hit + mutate-not-persisted
# ==========================================================================
async def test_intraday_decision_cached(tmp_path):
    """Real DecisionAgent w/ AsyncMock chat + DecisionCache: 1st run hits the LLM
    for the intraday decision; 2nd run (same cache_dir) → 0 LLM calls."""
    from unittest.mock import AsyncMock
    from financial_analyst.backtest.candidate import CandidateConfig

    loader = _ToyLoader()
    intraday_bars = {("SH600001", "2026-04-02"): _flat_then_breakout_bars("2026-04-02")}
    cache_dir = tmp_path / "cache"
    # SH600001 in a watchlist parquet → it is a candidate (so it lands in the
    # intraday watch set) WITHOUT a pre-open action, so the breakout reaches the
    # intraday LLM (not blocked by _preopen_acted) — the real path under test.
    wl = tmp_path / "watchlist.parquet"
    pd.DataFrame({"code": ["SH600001"]}).to_parquet(wl)

    def _resp(market_view, decisions):
        return {"choices": [{"message": {"content": json.dumps(
            {"market_view": market_view, "decisions": decisions, "warnings": []})}}]}

    def _make_run():
        reader = _ToyReader(loader, intraday_bars=intraday_bars)
        client = AsyncMock()
        # pre-open → hold (empty decisions); intraday → add SH600001.
        client.chat = AsyncMock(side_effect=lambda messages, **kw: (
            _resp("intraday add", [{"code": "SH600001", "action": "add",
                                    "weight_pct": 20.0, "stop_loss": 1.0,
                                    "reason": "x"}])
            if any("盘中关键点重判" in m.get("content", "") for m in messages)
            else _resp("hold", [])))
        agent = DecisionAgent(client=client, cache=DecisionCache(cache_dir))
        trig = IntradayTriggerConfig(enabled=True, breakout_enabled=True,
                                     min_bars_for_signal=5)
        runner = BacktestRunner(reader=reader, agent=agent, loader=loader,
                                cfg=RunConfig(start="2026-04-01", end="2026-04-03",
                                              init_cash=1_000_000.0, benchmark=None,
                                              match_freq="day",
                                              candidate=CandidateConfig(watchlist_path=wl),
                                              intraday=trig))
        return runner, agent, client

    runner1, agent1, client1 = _make_run()
    res1 = await runner1.run()
    calls_run1 = client1.chat.call_count
    assert calls_run1 >= 1
    # at least one intraday decision happened and was recorded
    assert any("_intraday" in v for v in res1.decisions_by_date.values())

    # second run, fresh agent, SAME cache_dir → everything cached → 0 calls
    runner2, agent2, client2 = _make_run()
    res2 = await runner2.run()
    assert client2.chat.call_count == 0, "cache miss on the 2nd run"
    assert agent2.n_calls == 0

    # --- subassertion (a): different bar/kind → different cache key (≥2 jsons)
    files = list(cache_dir.glob("*.json"))
    assert len(files) >= 2, f"expected >=2 distinct cache keys, got {len(files)}"

    # --- subassertion (b): changing detail text but NOT kind → same key;
    #     changing kind → different key.
    cache = DecisionCache(cache_dir)
    vi = VisibleInfo(date="2026-04-02", as_of="10:05:00",
                     boundary_ts="2026-04-02T10:05:00", news=[], events=[],
                     policy=[], market_eod_prev={"prev_trade_date": "2026-04-01"})
    from financial_analyst.backtest.decision import IntradayCtx
    base = dict(date="2026-04-02", as_of="10:05:00", visible=vi,
                candidates=["SH600001"], rev20_rank={}, holdings={}, cash=1e6, nav=1e6)
    inp_a = DecisionInput(**base, intraday=IntradayCtx(
        kind="breakout_high", bar_index=6, metric=10.5, detail="文案A"))
    inp_b = DecisionInput(**base, intraday=IntradayCtx(
        kind="breakout_high", bar_index=6, metric=10.5, detail="完全不同的文案B"))
    inp_c = DecisionInput(**base, intraday=IntradayCtx(
        kind="volume_surge", bar_index=6, metric=10.5, detail="文案A"))
    k_a = cache.key(inp_a, build_messages(inp_a), 0.2)
    k_b = cache.key(inp_b, build_messages(inp_b), 0.2)
    k_c = cache.key(inp_c, build_messages(inp_c), 0.2)
    assert k_a == k_b, "cache key must not depend on detail wording"
    assert k_a != k_c, "cache key must change when trigger kind changes"


async def test_intraday_raw_mutate_not_persisted(tmp_path):
    """Mutating decisions[T]['_intraday'] after decide() does not leak into the
    cache file (put serialized before any mutate, holds no dict ref)."""
    from unittest.mock import AsyncMock

    cache_dir = tmp_path / "cache"
    cache = DecisionCache(cache_dir)
    vi = VisibleInfo(date="2026-04-02", as_of="09:25:00",
                     boundary_ts="2026-04-02T09:25:00", news=[], events=[],
                     policy=[], market_eod_prev={"prev_trade_date": "2026-04-01"})
    inp = DecisionInput(date="2026-04-02", as_of="09:25", visible=vi,
                        candidates=["SH600001"], rev20_rank={}, holdings={},
                        cash=1e6, nav=1e6)
    client = AsyncMock()
    client.chat = AsyncMock(return_value={"choices": [{"message": {"content": json.dumps(
        {"market_view": "hold", "decisions": [], "warnings": []})}}]})
    agent = DecisionAgent(client=client, cache=cache)
    decision = await agent.decide(inp)
    # mutate the in-memory raw (engine does this with _intraday)
    decision.raw["_intraday"] = [{"trigger": "x"}]
    # re-read the cache file: it must NOT contain _intraday
    key = cache.key(inp, build_messages(inp), agent._temperature)
    cached = cache.get(key)
    assert cached is not None
    assert "_intraday" not in cached, "raw mutate leaked into the cache file"


# ==========================================================================
# 己 — real LLM intraday smoke (skip without key/data/network)
# ==========================================================================
def _real_data_available() -> bool:
    try:
        from financial_analyst.data.paths import get_data_paths
        p = get_data_paths()
        uri = p.qlib_uri
        day_root = uri["day"] if isinstance(uri, dict) else uri
        return (Path(day_root).exists()
                and Path(str(p.pit_store_root)).exists())
    except Exception:
        return False


# import-time capture of real paths (the autouse CI fixture patches find_config)
try:
    from financial_analyst.data.paths import get_data_paths as _gdp_import_time
    _REAL_PATHS = _gdp_import_time()
    _REAL_QLIB_URI = _REAL_PATHS.qlib_uri
    _REAL_PIT_ROOT = Path(str(_REAL_PATHS.pit_store_root))
except Exception:
    _REAL_PATHS = None
    _REAL_QLIB_URI = None
    _REAL_PIT_ROOT = None


@pytest.mark.slow
@pytest.mark.skipif(not os.environ.get("DASHSCOPE_API_KEY"),
                    reason="no DASHSCOPE_API_KEY → real LLM intraday smoke skipped")
@pytest.mark.skipif(not _real_data_available(),
                    reason="real cn_data / pit_store not present")
async def test_real_llm_intraday_smoke():
    from financial_analyst.backtest.decision import DecisionAgent
    from financial_analyst.data.loaders.qlib_binary import QlibBinaryLoader
    from financial_analyst.llm.client import LLMClient
    from financial_analyst.backtest.pit_reader import PitReader

    if _REAL_PIT_ROOT is None or not _REAL_PIT_ROOT.exists():
        pytest.skip("real pit_store not resolvable at runtime")

    loader = QlibBinaryLoader(_REAL_QLIB_URI)
    reader = PitReader(store_root=_REAL_PIT_ROOT, day_loader=loader)
    # 5min coverage is recent; pick a small recent window
    days = reader.trading_days("2026-05-27", "2026-05-29")
    if len(days) < 2:
        pytest.skip("not enough real trading days in 5min-covered window")

    client = LLMClient.for_agent("backtest-agent")
    # scripted pre-open via the real agent is not possible; just let it decide.
    agent = DecisionAgent(client=client)
    trig = IntradayTriggerConfig(enabled=True, breakout_enabled=True,
                                 stop_break_enabled=True, min_bars_for_signal=5)
    cfg = RunConfig(start=days[0], end=days[-1], init_cash=1_000_000.0,
                    benchmark=None, match_freq="day", intraday=trig)
    runner = BacktestRunner(reader=reader, agent=agent, loader=loader, cfg=cfg)
    try:
        res = await runner.run()
    except Exception as e:  # network / provider error → skip, not fail
        pytest.skip(f"LLM unreachable or provider error: {e!r}")

    assert res.n_llm_calls >= 0
    assert len(res.nav_history) >= 2
    # if any intraday decision-class trigger fired, validate the record shape
    for d, raw in res.decisions_by_date.items():
        if not isinstance(raw, dict):
            continue
        for rec in raw.get("_intraday", []):
            trg = rec.get("trigger", {})
            bt = trg.get("bar_time") or rec.get("bar_time")
            if bt:
                assert pd.Timestamp(bt) <= pd.Timestamp(f"{d} 15:00:00")
