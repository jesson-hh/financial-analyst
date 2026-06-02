"""WatchLoop — 盯盘 tick 编排 (Realtime Watch Task 6).

The orchestrator that ties the watch sub-modules together. Each *tick* it:

1. **gate on trading hours** — outside 09:30–11:30 / 13:00–15:00 (Mon–Fri) the
   tick is a no-op (no network, no LLM);
2. **batch-snapshot** every watched code (one ``feed.snapshot`` call) and push a
   ``quote_update`` event per code onto the out-queue;
3. for **each** ``WatchItem`` (independently — one stock raising never aborts the
   tick): pull its 5min bars, ask the *pure* trigger
   (:class:`~financial_analyst.watch.triggers.WatchTrigger`) whether a key point
   just fired, and — subject to **per-(code, kind) cooldown** and a **per-session
   global LLM cap** — build a :class:`~financial_analyst.watch.models.WatchContext`
   and ask the single-stock :class:`~financial_analyst.watch.agent.WatchAgent`
   for a :class:`~financial_analyst.watch.models.WatchRec`. Each rec is enqueued
   as a ``recommendation`` event and appended to the parquet log;
4. every ``news_every_n_ticks`` ticks it additionally pulls headlines (via an
   optional ``news_provider``) and runs the keyword
   :func:`~financial_analyst.watch.triggers.news_trigger`, feeding any hit through
   the same cooldown / cap / agent path.

Design notes
------------
* **Cooldown** is keyed on ``(code, trigger_kind)`` — a different kind on the same
  stock is *not* suppressed (a breakout and a later stop-break are distinct
  events). ``cooldown_minutes`` measured against the tick's ``now``.
* **Global LLM cap** (``global_llm_cap_per_session``) is a hard session ceiling on
  ``agent.decide_one`` calls — once reached, no further agent calls happen, even
  across ticks (mirrors the design's cost guard). Cooldown is checked *before* the
  cap so a cooled-down signal never burns a call.
* **Lazy backtest import.** The trigger engine lives in
  ``financial_analyst.backtest.intraday``; we import :class:`IntradayTriggerConfig`
  / :class:`WatchTrigger` *lazily* (inside the config factory / when no trigger is
  injected) so this module — and its unit tests, which inject a fake trigger —
  import cleanly even when the backtest source isn't present. Inject ``trigger``
  to bypass construction entirely.
* **Single-writer** parquet append (``store.append_rec``) — the loop is the sole
  writer (CLAUDE.md data-write rule); persistence failures are swallowed (advisory
  log, not the source of truth) so a disk hiccup never drops a live rec.

The loop holds an :class:`asyncio.Queue` of events the ``fa serve`` SSE layer
drains; tests use :meth:`drain` to read them synchronously.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple, Union

import pandas as pd

from financial_analyst.watch.models import WatchContext, WatchItem, WatchRec

log = logging.getLogger(__name__)

# Trading session windows (inclusive edges), Asia/Shanghai wall-clock.
_AM_OPEN = (9, 30)
_AM_CLOSE = (11, 30)
_PM_OPEN = (13, 0)
_PM_CLOSE = (15, 0)

# News headlines kept per fired stock in the agent context.
_NEWS_MAX = 10


# ──────────────────────── default trigger config ───────────────────────────────


def _default_trigger_cfg() -> Any:
    """Build a default :class:`IntradayTriggerConfig` (enabled) — *lazily*, so this
    module imports even when ``backtest.intraday`` isn't on the path.

    Returns ``None`` if the backtest package is absent; in that case a real trigger
    cannot be auto-constructed and one must be injected (the unit tests do exactly
    that). The public contract is unchanged when the package is present.
    """
    try:
        from financial_analyst.backtest.intraday import IntradayTriggerConfig
    except Exception as exc:  # pragma: no cover — only on a partial source tree
        log.debug("WatchLoop: IntradayTriggerConfig unavailable (%s); "
                  "trigger_cfg defaults to None (inject a trigger)", exc)
        return None
    return IntradayTriggerConfig(enabled=True)


# ──────────────────────── config ───────────────────────────────────────────────


@dataclass
class WatchLoopConfig:
    """Tunables for the 盯盘 loop.

    Attributes:
        tick_seconds: Seconds between ticks in :meth:`WatchLoop.run`.
        news_every_n_ticks: Pull news + run the keyword trigger every Nth tick.
        cooldown_minutes: Per-(code, kind) re-fire suppression window.
        global_llm_cap_per_session: Hard ceiling on agent calls for the session.
        negative_min_severity: Min tdx_f10 warning severity (B1) that fires a
            *hard* negative-event rec (no LLM). 2 = 立案/处罚/减持/业绩预减 等.
        trigger_cfg: An ``IntradayTriggerConfig`` used when the loop must build its
            own :class:`WatchTrigger`. Defaults (lazily) to an enabled config;
            ``None`` when the backtest package is unavailable.
    """

    tick_seconds: float = 60
    news_every_n_ticks: int = 5
    cooldown_minutes: int = 15
    global_llm_cap_per_session: int = 20
    negative_min_severity: int = 2
    trigger_cfg: Any = field(default_factory=_default_trigger_cfg)


# ──────────────────────── loop ──────────────────────────────────────────────────


class WatchLoop:
    """Tick orchestrator over a list of :class:`WatchItem`.

    Args:
        items: Stocks to watch.
        feed: Object exposing ``snapshot(codes) -> {code: {...}}`` and
            ``bars5(code, n) -> DataFrame`` (a :class:`WatchFeed` in production;
            a stub in tests).
        agent: Object exposing ``async decide_one(ctx) -> WatchRec`` and an
            ``n_calls`` counter (a :class:`WatchAgent` in production).
        store_path: Parquet recommendation-log path; ``None`` -> the store's
            default. Pass ``False`` (or set ``persist=False``) to disable
            persistence entirely (tests).
        config: :class:`WatchLoopConfig`; defaults applied if ``None``.
        trigger: Injected trigger exposing ``check_item(item, bars, i=None)`` and
            ``reset_day()``. If ``None``, a :class:`WatchTrigger` is built lazily
            from ``config.trigger_cfg`` (requires the backtest package).
        names: Optional ``{code: display_name}`` for nicer prompts.
        news_provider: Optional ``callable(code) -> Sequence[str]`` returning
            today's headlines (used on news ticks). ``None`` disables news.
        factors_provider: Optional ``callable(code) -> dict`` of EOD factors for
            the agent context. ``None`` -> empty.
        news_keywords: Optional keyword list for :func:`news_trigger`.
        is_trading_day: Optional ``callable(ts) -> bool`` for a holiday-aware day
            gate (see :func:`watch.calendar.make_market_open_check`). ``None``
            (default) → plain Mon–Fri weekday check.
        warnings_provider: Optional ``callable() -> {code: {severity,title,event_date}}``
            (B1, see :func:`watch.signals.load_negative_warnings`). A severity≥
            ``cfg.negative_min_severity`` hit fires a *hard* sell (held) / 禁建仓
            (not held) rec — no LLM. ``None`` disables the negative-event channel.
        regime_provider: Optional ``callable(code, bars_5min_today) -> regime dict``
            (B2, see :class:`watch.signals.RegimeProvider`). A risk regime
            (super_distr / distr / tail_surge) fires a ``vol_regime`` trigger →
            the *advisor* (which carries the super_distr knowledge from package A).
            ``None`` disables the vol-regime channel.
    """

    def __init__(
        self,
        items: Sequence[WatchItem],
        feed: Any,
        agent: Any,
        store_path: Union[str, Path, None, bool] = None,
        config: Optional[WatchLoopConfig] = None,
        trigger: Any = None,
        names: Optional[Dict[str, str]] = None,
        news_provider: Optional[Callable[[str], Sequence[str]]] = None,
        factors_provider: Optional[Callable[[str], Dict[str, Any]]] = None,
        news_keywords: Optional[Sequence[str]] = None,
        is_trading_day: Optional[Callable[[Any], bool]] = None,
        warnings_provider: Optional[Callable[[], Dict[str, Any]]] = None,
        regime_provider: Optional[Callable[[str, Any], Dict[str, Any]]] = None,
        persist: bool = True,
    ) -> None:
        self.items: List[WatchItem] = list(items)
        self.feed = feed
        self.agent = agent
        self.cfg = config or WatchLoopConfig()
        self._trigger = trigger          # lazily built on first use if None
        self.names = dict(names or {})
        self._news_provider = news_provider
        self._factors_provider = factors_provider
        self._news_keywords = list(news_keywords) if news_keywords is not None else None
        self._is_trading_day = is_trading_day
        self._warnings_provider = warnings_provider
        self._regime_provider = regime_provider

        # persistence: store_path is False -> off; else remember the path.
        self._persist = bool(persist) and (store_path is not False)
        self._store_path = None if store_path in (None, False) else store_path

        # runtime state
        self._queue: "asyncio.Queue[Dict[str, Any]]" = asyncio.Queue()
        self._cooldown: Dict[Tuple[str, str], pd.Timestamp] = {}
        self.llm_calls_made: int = 0
        self.tick_count: int = 0
        self.stopped: bool = False
        self._last_day: Optional[Any] = None   # for reset_day() at day rollover

    # ─────────────────────── trigger (lazy) ───────────────────────

    def _get_trigger(self) -> Any:
        if self._trigger is None:
            # Build the real wrapper only when needed (keeps import lazy).
            from financial_analyst.watch.triggers import WatchTrigger
            self._trigger = WatchTrigger(self.cfg.trigger_cfg)
        return self._trigger

    # ─────────────────────── trading hours ───────────────────────

    @staticmethod
    def _in_window(t: Any, lo: Tuple[int, int], hi: Tuple[int, int]) -> bool:
        cur = (t.hour, t.minute)
        return lo <= cur <= hi

    def is_market_open(self, ts: Any) -> bool:
        """``True`` iff ``ts`` is within an A 股 trading session.

        A trading day and time in ``[09:30, 11:30]`` or ``[13:00, 15:00]``
        (inclusive edges). The *day* gate uses an injected ``is_trading_day``
        callable when present (holiday-aware, see :mod:`watch.calendar`),
        otherwise a plain Mon–Fri weekday check. ``ts`` is anything with
        ``.weekday()/.hour/.minute`` (e.g. a :class:`pandas.Timestamp`).
        """
        t = pd.Timestamp(ts)
        # day gate: holiday-aware check if injected, else weekday-only.
        if self._is_trading_day is not None:
            try:
                if not self._is_trading_day(t):
                    return False
            except Exception:  # noqa: BLE001 — a calendar hiccup must never wedge the loop
                if t.weekday() >= 5:
                    return False
        elif t.weekday() >= 5:                     # Sat/Sun
            return False
        return (self._in_window(t, _AM_OPEN, _AM_CLOSE)
                or self._in_window(t, _PM_OPEN, _PM_CLOSE))

    # ─────────────────────── cooldown ───────────────────────

    def _on_cooldown(self, code: str, kind: str, now: pd.Timestamp) -> bool:
        last = self._cooldown.get((code, kind))
        if last is None:
            return False
        delta_min = (now - last).total_seconds() / 60.0
        return delta_min < self.cfg.cooldown_minutes

    # ─────────────────────── event queue ───────────────────────

    def _emit(self, event: Dict[str, Any]) -> None:
        self._queue.put_nowait(event)

    def drain(self) -> List[Dict[str, Any]]:
        """Pop and return all currently queued events (non-blocking)."""
        out: List[Dict[str, Any]] = []
        while True:
            try:
                out.append(self._queue.get_nowait())
            except asyncio.QueueEmpty:
                break
        return out

    # ─────────────────────── context build ───────────────────────

    def _event_to_trigger_dict(self, ev: Any) -> Dict[str, Any]:
        return {
            "kind": getattr(ev, "kind", ""),
            "detail": getattr(ev, "detail", ""),
            "metric": getattr(ev, "metric", 0.0),
            "is_risk": bool(getattr(ev, "is_risk", False)),
            "bar_index": getattr(ev, "bar_index", -1),
        }

    def _build_context(self, item: WatchItem, ev: Any, now: pd.Timestamp,
                       snap: Dict[str, Any], bars: pd.DataFrame,
                       headlines: Sequence[str]) -> WatchContext:
        bars_records: List[Dict[str, Any]] = []
        if bars is not None and len(bars) > 0:
            bars_records = bars.to_dict("records")
        factors: Dict[str, Any] = {}
        if self._factors_provider is not None:
            try:
                factors = dict(self._factors_provider(item.code) or {})
            except Exception as exc:  # noqa: BLE001 — context build must not crash tick
                log.debug("WatchLoop: factors_provider failed for %s: %s", item.code, exc)
        return WatchContext(
            code=item.code,
            name=self.names.get(item.code, item.code),
            now_ts=now.strftime("%Y-%m-%d %H:%M:%S"),
            trigger=self._event_to_trigger_dict(ev),
            realtime=dict(snap or {}),
            bars_5min=bars_records,
            factors_eod=factors,
            news_today=list(headlines)[:_NEWS_MAX],
            item=item,
        )

    # ─────────────────────── one fired stock → rec ───────────────────────

    async def _handle_event(self, item: WatchItem, ev: Any, now: pd.Timestamp,
                            snap: Dict[str, Any], bars: pd.DataFrame,
                            headlines: Sequence[str]) -> Optional[WatchRec]:
        """Cooldown → cap → agent → enqueue + persist. Returns the rec or ``None``
        (cooled down, capped, or the event was falsy)."""
        if ev is None:
            return None
        kind = getattr(ev, "kind", "") or ""

        if self._on_cooldown(item.code, kind, now):
            log.debug("WatchLoop: %s/%s on cooldown, skip", item.code, kind)
            return None

        # global LLM cap — checked AFTER cooldown so a cooled signal never burns it.
        if self.llm_calls_made >= self.cfg.global_llm_cap_per_session:
            log.debug("WatchLoop: global LLM cap %d reached, skip %s/%s",
                      self.cfg.global_llm_cap_per_session, item.code, kind)
            return None

        ctx = self._build_context(item, ev, now, snap, bars, headlines)
        rec = await self.agent.decide_one(ctx)
        self.llm_calls_made += 1

        # mark cooldown on the (code, kind) regardless of the action returned —
        # we consumed a slot and don't want to re-prompt the same event next tick.
        self._cooldown[(item.code, kind)] = now

        self._emit({"type": "recommendation", "code": item.code,
                    "ts": ctx.now_ts, "rec": rec.to_dict()})
        self._persist_rec(rec)
        return rec

    def _persist_rec(self, rec: WatchRec) -> None:
        if not self._persist:
            return
        try:
            from financial_analyst.watch.store import append_rec
            append_rec(self._store_path, rec)
        except Exception as exc:  # noqa: BLE001 — advisory log, never drop a live tick
            log.warning("WatchLoop: append_rec failed (%s); rec kept in queue only", exc)

    # ─────────────────────── tick ───────────────────────

    async def tick(self, now: Optional[Any] = None) -> List[WatchRec]:
        """Run one watch cycle. Returns the recommendations produced this tick.

        A no-op (empty list, no network/LLM) outside trading hours. Each item is
        handled in isolation — an exception on one stock is logged and skipped, the
        rest of the tick proceeds.
        """
        now_ts = pd.Timestamp(now) if now is not None else pd.Timestamp.now()
        if not self.is_market_open(now_ts):
            return []

        # day rollover → reset the trigger's per-day dedup/counters.
        day = now_ts.normalize()
        if self._last_day is not None and day != self._last_day:
            try:
                self._get_trigger().reset_day()
            except Exception as exc:  # noqa: BLE001
                log.debug("WatchLoop: trigger.reset_day failed: %s", exc)
        self._last_day = day

        self.tick_count += 1
        codes = [it.code for it in self.items]

        # 1) batch snapshot + quote_update events
        snaps: Dict[str, Dict[str, Any]] = {}
        try:
            # feed.snapshot is a SYNC blocking network call — offload to a thread
            # so it never freezes the FastAPI event loop (SSE/other endpoints).
            snaps = await asyncio.to_thread(self.feed.snapshot, codes) or {}
        except Exception as exc:  # noqa: BLE001 — snapshot failure must not abort tick
            log.warning("WatchLoop: feed.snapshot failed: %s", exc)
        for code in codes:
            self._emit({"type": "quote_update", "code": code,
                        "ts": now_ts.strftime("%Y-%m-%d %H:%M:%S"),
                        "quote": snaps.get(code, {})})

        # 2) is this a news tick?
        do_news = (self._news_provider is not None
                   and self.cfg.news_every_n_ticks > 0
                   and (self.tick_count % self.cfg.news_every_n_ticks == 0))

        # 3) negative-event warnings (B1): one read per tick, shared across items
        #    (a blocking parquet read → offload off the event loop).
        warnings: Dict[str, Any] = {}
        if self._warnings_provider is not None:
            try:
                warnings = await asyncio.to_thread(self._warnings_provider) or {}
            except Exception as exc:  # noqa: BLE001 — warnings failure must not abort tick
                log.debug("WatchLoop: warnings_provider failed: %s", exc)

        trigger = self._get_trigger()
        recs: List[WatchRec] = []
        for item in self.items:
            try:
                rec = await self._handle_item(item, trigger, now_ts,
                                              snaps.get(item.code, {}), do_news, warnings)
                if rec is not None:
                    recs.append(rec)
            except Exception as exc:  # noqa: BLE001 — isolate per-stock failures
                log.warning("WatchLoop: item %s failed this tick: %s", item.code, exc)
                continue
        return recs

    def _negative_rec(self, item: WatchItem, ev: Any, now: pd.Timestamp) -> WatchRec:
        """Build the *hard* (rule-based) rec for a severity≥N negative event (B1).

        Held (user set avg_cost/stop_loss) → ``sell``; otherwise ``hold`` (规避/禁建仓).
        """
        held = item.avg_cost is not None or item.stop_loss is not None
        action = "sell" if held else "hold"
        suffix = " → 持仓硬卖" if held else " → 未持仓, 规避/禁建仓"
        return WatchRec(
            code=item.code, action=action,
            reason=str(getattr(ev, "detail", "")) + suffix,
            trigger_kind="negative_event",
            ts=now.strftime("%Y-%m-%d %H:%M:%S"),
            confidence=0.9,
        )

    def _emit_hard_rec(self, item: WatchItem, kind: str, now: pd.Timestamp,
                       rec: WatchRec) -> WatchRec:
        """Emit + persist a RULE-based rec — no LLM, does NOT consume the cap.
        Marks ``(code, kind)`` cooldown so it doesn't re-fire every tick."""
        self._cooldown[(item.code, kind)] = now
        self._emit({"type": "recommendation", "code": item.code,
                    "ts": rec.ts, "rec": rec.to_dict()})
        self._persist_rec(rec)
        return rec

    @staticmethod
    def _today_bars(bars: Any) -> Any:
        """Most-recent trading day's 5min bars with ``vol`` renamed to ``volume``
        (for :func:`watch.signals.compute_vol_regime`). ``None`` if empty. A bars5
        frame may span several days → keep only the last day's rows so the intraday
        tail (vs_close_30m) is computed over a single session."""
        if bars is None or len(bars) == 0:
            return None
        b = bars
        if "trade_date" in b.columns:
            last = str(b["trade_date"].iloc[-1])[:10]
            b = b[b["trade_date"].astype(str).str.startswith(last)]
        if "vol" in b.columns and "volume" not in b.columns:
            b = b.rename(columns={"vol": "volume"})
        return b

    async def _handle_item(self, item: WatchItem, trigger: Any, now: pd.Timestamp,
                           snap: Dict[str, Any], do_news: bool,
                           warnings: Optional[Dict[str, Any]] = None) -> Optional[WatchRec]:
        """Evaluate one stock: negative-event hard rule → bars trigger → news → rec."""
        # B1: negative-event hard rule — highest priority, NO LLM. Checked first
        # (a 立案/处罚/减持 dominates any breakout). Cooldown-gated like the rest.
        if warnings:
            from financial_analyst.watch.triggers import negative_event_trigger
            nev = negative_event_trigger(item.code, warnings, self.cfg.negative_min_severity)
            if nev is not None and not self._on_cooldown(item.code, nev.kind, now):
                return self._emit_hard_rec(item, nev.kind, now,
                                           self._negative_rec(item, nev, now))

        # feed.bars5 is a SYNC blocking network call — offload to a thread so it
        # never freezes the FastAPI event loop (SSE/other endpoints).
        bars = await asyncio.to_thread(self.feed.bars5, item.code)

        # headlines (only fetched on news ticks; reused for both the news trigger
        # and the agent context so a hit cites the actual headline).
        headlines: List[str] = []
        if do_news:
            try:
                # news_provider is a BLOCKING opencli subprocess — offload to a
                # thread so it never freezes the FastAPI event loop (SSE etc.),
                # mirroring the snapshot/bars5 offload above.
                headlines = list(
                    await asyncio.to_thread(self._news_provider, item.code) or []  # type: ignore[arg-type]
                )
            except Exception as exc:  # noqa: BLE001
                log.debug("WatchLoop: news_provider failed for %s: %s", item.code, exc)

        # B2: 量能 regime channel — checked before the bar trigger (a distribution
        # regime should pre-empt a breakout "buy"). Routed to the advisor, which
        # carries the super_distr knowledge from package A. Cooldown-gated.
        if self._regime_provider is not None:
            try:
                regime = self._regime_provider(item.code, self._today_bars(bars))
            except Exception as exc:  # noqa: BLE001 — regime failure must not abort the tick
                regime = None
                log.debug("WatchLoop: regime_provider failed for %s: %s", item.code, exc)
            if regime:
                from financial_analyst.watch.triggers import vol_regime_trigger
                vev = vol_regime_trigger(item.code, regime)
                if vev is not None and not self._on_cooldown(item.code, vev.kind, now):
                    return await self._handle_event(item, vev, now, snap, bars, headlines)

        # primary (bar) trigger first — it's the higher-signal channel.
        ev = trigger.check_item(item, bars)
        if ev is not None:
            rec = await self._handle_event(item, ev, now, snap, bars, headlines)
            if rec is not None:
                return rec
            # fired but cooled-down/capped → don't also fire news this tick.
            return None

        # news channel (independent of the bar engine).
        if do_news and headlines:
            from financial_analyst.watch.triggers import news_trigger
            nev = news_trigger(item.code, headlines, self._news_keywords)
            if nev is not None:
                return await self._handle_event(item, nev, now, snap, bars, headlines)
        return None

    # ─────────────────────── run / stop ───────────────────────

    def stop(self) -> None:
        """Signal :meth:`run` to exit after the current tick."""
        self.stopped = True

    async def run(self, now: Optional[Any] = None, max_ticks: Optional[int] = None) -> None:
        """Drive ticks every ``tick_seconds`` until :meth:`stop` (or ``max_ticks``).

        Args:
            now: Optional fixed start timestamp (mostly for tests). In production
                each tick reads the wall clock; if ``now`` is given it is used for
                the first tick only and real time advances thereafter.
            max_ticks: Optional hard cap on the number of ticks (testing aid).

        Outside trading hours each cycle still sleeps ``tick_seconds`` (cheap poll)
        so the loop wakes up when the session opens.
        """
        ticks = 0
        first = True
        while not self.stopped:
            ts = pd.Timestamp(now) if (first and now is not None) else pd.Timestamp.now()
            first = False
            try:
                await self.tick(now=ts)
            except Exception as exc:  # noqa: BLE001 — a tick must never kill the loop
                log.warning("WatchLoop: tick raised, continuing: %s", exc)
            ticks += 1
            if max_ticks is not None and ticks >= max_ticks:
                break
            if self.stopped:
                break
            await asyncio.sleep(self.cfg.tick_seconds)


__all__ = ["WatchLoop", "WatchLoopConfig"]
