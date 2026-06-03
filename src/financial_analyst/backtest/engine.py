"""BacktestRunner — day-by-day driver of the P2 decision backtest.

Time-series law (P2 §4.6): the decision sees only ≤boundary info; matching uses
the T-day bar; mark/stop use the T-day close/low; the loop is capped to
``data_end`` (the future-padded day calendar would otherwise freeze NAV on empty
bars). Reuses P1's ``VirtualPortfolio`` / ``Broker`` / ``compute_metrics`` /
``TradeLog`` untouched.

Order mapping (§4.3, with the reviewer fixes folded in):
  * sell/reduce float = 跌停价 ``dn`` (broker rejects a sell limit only when
    ``limit_price > high``; ``dn`` never exceeds high, so a sell always clears the
    touch check and the broker clips the realized fill into ``[low, high]``);
  * buy ``weight_pct`` is normalized over the same-batch buy legs before折现金,
    avoiding先到先得 偏置;
  * a buy leg's ``target_price`` is NOT passed into the Order (the broker has no
    take-profit) — it only rides along in ``decisions_by_date`` for the UI.
  * sells/reduces are matched before buys/adds (free up cash first).

Matching freq defaults to ``day`` (one day-bar = one bar). ``5min`` is optional.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

from financial_analyst.backtest.broker import Broker, Order
from financial_analyst.backtest.candidate import CandidateConfig, select_candidates
from financial_analyst.backtest.costs import CostModel
from financial_analyst.backtest.decision import (
    DecisionInput,
    DecisionLeg,
    IntradayCtx,
)
from financial_analyst.backtest.intraday import (
    IntradayTrigger,
    IntradayTriggerConfig,
    TriggerEvent,
)
from financial_analyst.backtest.limits import compute_ref_prev_close, limit_pct_for
from financial_analyst.backtest.metrics import compute_metrics
from financial_analyst.backtest.portfolio import VirtualPortfolio, _norm_date
from financial_analyst.backtest.records import TradeLog

_log = logging.getLogger(__name__)


@dataclass
class RunConfig:
    start: str = "2026-03-13"
    end: Optional[str] = None             # None → min(day data_end, 5min cal末)
    init_cash: float = 1_000_000.0
    as_of: str = "09:25"
    benchmark: Optional[str] = None       # default None (no local 2026 index)
    match_freq: str = "day"               # "day" | "5min"
    candidate: CandidateConfig = field(default_factory=CandidateConfig)
    cost: CostModel = field(default_factory=CostModel)
    run_id: Optional[str] = None
    cache_dir: Optional[Path] = None
    reduce_fraction: float = 0.5          # "reduce" sells this fraction of qty
    # P3 盘中关键点重判 — enabled=False (default) → behaviour identical to P2.
    intraday: IntradayTriggerConfig = field(default_factory=IntradayTriggerConfig)


@dataclass
class BacktestResult:
    portfolio_result: Any                 # PortfolioResult
    trade_log: TradeLog
    decisions_by_date: Dict[str, dict]
    nav_history: List[Tuple[str, float]]
    benchmark_nav: Optional[List[Tuple[str, float]]]
    n_llm_calls: int
    trade_stats: Dict[str, float]
    warnings: List[str]
    # P1.3 — last day's CandidateResult.filter_stats (for UI PoolFilterPopover).
    # Picks last day's stats as the representative end-of-window state.
    candidate_filter_stats: Dict[str, int] = field(default_factory=dict)


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def _ref_prev_close_leq(loader, code: str, on_date: str) -> Optional[float]:
    """ex-div-corrected reference prev close, last value ≤ on_date."""
    close = loader._read_bin(code, "close", "day")
    factor = loader._read_bin(code, "factor", "day")
    if close is None:
        return None
    if factor is None:
        # no factor → fall back to raw prev close
        s = close.loc[close.index <= pd.Timestamp(on_date)]
        return float(s.iloc[-1]) if len(s) else None
    rpc = compute_ref_prev_close(close, factor)
    rpc = rpc.loc[rpc.index <= pd.Timestamp(on_date)].dropna()
    return float(rpc.iloc[-1]) if len(rpc) else None


def prepare_bar(code: str, T: str, reader, loader, cfg: RunConfig
                ) -> Tuple[Optional[dict], Optional[float]]:
    """Return (bar_dict_or_None, ref_prev_close). bar always carries trade_date."""
    prev_close = _ref_prev_close_leq(loader, code, T)
    if cfg.match_freq == "day":
        df = loader.fetch_quote(code, T, T, "day")
        if df is None or len(df) == 0:
            return None, prev_close
        row = df.iloc[-1]
        o, h, l, c, v = (row.get("open"), row.get("high"), row.get("low"),
                         row.get("close"), row.get("vol"))
        if any(pd.isna(x) for x in (o, h, l, c)):
            return None, prev_close
        bar = {"trade_date": T, "open": float(o), "high": float(h),
               "low": float(l), "close": float(c), "vol": float(v)}
        return bar, prev_close
    # 5min: first bar of T (matching stays within T, never cross-day)
    bars = reader.fetch_bars_intraday(code, T, "5min")
    if bars is None or len(bars) == 0:
        return None, prev_close
    bars = bars.sort_values("trade_date")
    row = bars.iloc[0]
    o, h, l, c, v = (row.get("open"), row.get("high"), row.get("low"),
                     row.get("close"), row.get("vol"))
    if any(pd.isna(x) for x in (o, h, l, c)):
        return None, prev_close
    bar = {"trade_date": T, "open": float(o), "high": float(h),
           "low": float(l), "close": float(c), "vol": float(v)}
    return bar, prev_close


def _day_close(loader, code: str, T: str) -> Optional[float]:
    df = loader.fetch_quote(code, T, T, "day")
    if df is None or len(df) == 0:
        return None
    c = df.iloc[-1].get("close")
    return None if pd.isna(c) else float(c)


def _day_low(loader, code: str, T: str) -> Optional[float]:
    df = loader.fetch_quote(code, T, T, "day")
    if df is None or len(df) == 0:
        return None
    l = df.iloc[-1].get("low")
    return None if pd.isna(l) else float(l)


# P3 intraday helpers --------------------------------------------------------
@dataclass
class _StopLeg:
    """Internal marker for a rule-path intraday stop sell (NOT a DecisionLeg).

    ``legs_to_orders`` only emits limit orders; a protective stop sell must use
    ``Order(otype="stop")`` so the Broker fills it at a realistic price (clipped
    into the bar range), so ``_match_intraday_legs`` builds the Order directly.
    """

    code: str
    stop_px: float


def _ev_dict(ev: TriggerEvent) -> dict:
    return {"code": ev.code, "kind": ev.kind, "bar_time": ev.bar_time,
            "bar_index": ev.bar_index, "detail": ev.detail,
            "metric": ev.metric, "is_risk": ev.is_risk}


def legs_to_orders(legs: List[DecisionLeg], portfolio: VirtualPortfolio,
                   reader, loader, T: str, cfg: RunConfig) -> List[Order]:
    """Map decision legs → P1 Orders. sells first, then normalized buys."""
    sells: List[Order] = []
    buys: List[DecisionLeg] = []
    for leg in legs:
        act = leg.action
        if act in ("sell", "reduce"):
            pos = portfolio.positions.get(leg.code)
            if pos is None:
                continue
            prev_close = _ref_prev_close_leq(loader, leg.code, T)
            if prev_close is None:
                continue
            pct = limit_pct_for(leg.code)
            dn = round(prev_close * (1 - pct), 2)  # 跌停价 floor → always clears touch
            qty = (int(pos.qty * cfg.reduce_fraction) if act == "reduce" else None)
            if act == "reduce" and (qty is None or qty < 100):
                continue
            sells.append(Order(code=leg.code, side="sell", otype="limit",
                               limit_price=dn, qty=qty))
        elif act in ("buy", "add"):
            buys.append(leg)
        # "hold" → no order

    # normalize buy weights over the batch, then折现金 (avoid先到先得)
    buy_orders: List[Order] = []
    total_w = sum(max(0.0, b.weight_pct) for b in buys)
    investable = portfolio.cash
    for b in buys:
        w = max(0.0, b.weight_pct)
        if total_w > 100.0 and total_w > 0:
            w = w * 100.0 / total_w  # scale down so the batch ≤ 100%
        prev_close = _ref_prev_close_leq(loader, b.code, T)
        if prev_close is None:
            continue
        pct = limit_pct_for(b.code)
        up = round(prev_close * (1 + pct / 2.0), 2)  # ≤T-1-derived limit ceiling
        budget = w / 100.0 * investable
        if budget <= 0:
            continue
        buy_orders.append(Order(code=b.code, side="buy", otype="limit",
                               limit_price=up, qty=None, cash_budget=budget,
                               stop_loss=b.stop_loss))
    return sells + buy_orders


def build_benchmark_nav(loader, benchmark: Optional[str], days: List[str],
                        init_cash: float, warnings: List[str]
                        ) -> Optional[List[Tuple[str, float]]]:
    """Benchmark NAV aligned to nav_history (seed point + scaled close).

    Returns None — never an empty/NaN series — if the benchmark has no local
    rows in the window (so compute_metrics simply skips the comparison)."""
    if not benchmark:
        return None
    df = loader.fetch_quote(benchmark, days[0], days[-1], "day")
    if df is None or len(df) == 0:
        warnings.append(
            f"benchmark {benchmark} 本地无 {days[0]}..{days[-1]} 行情, 已禁用基准对照")
        return None
    df = df.sort_values("trade_date")
    close0 = float(df["close"].iloc[0])
    if close0 <= 0:
        warnings.append(f"benchmark {benchmark} 首日 close<=0, 已禁用基准对照")
        return None
    series: List[Tuple[str, float]] = [(days[0], float(init_cash))]
    for r in df.itertuples():
        d = str(pd.Timestamp(r.trade_date).date())
        series.append((d, init_cash * float(r.close) / close0))
    return series


# --------------------------------------------------------------------------
# BacktestRunner
# --------------------------------------------------------------------------
class BacktestRunner:
    def __init__(self, reader, agent, loader=None,
                 cfg: RunConfig = RunConfig()) -> None:
        self.reader = reader
        self.agent = agent
        self.loader = loader if loader is not None else getattr(reader, "_loader")
        self.cfg = cfg
        self.trigger = IntradayTrigger(cfg.intraday)   # P3 盘中触发器
        self._preopen_acted: set = set()               # P3 当日盘前已下非 hold 决策的股
        self._init_warnings: List[str] = []

        de = reader.data_end()
        try:
            c5 = reader._loader._load_calendar("5min")[-1]
            data_end = min(de, pd.Timestamp(str(pd.Timestamp(c5).date())))
        except Exception:
            data_end = de
        self._data_end = data_end
        if cfg.end and pd.Timestamp(cfg.end) > self._data_end:
            self._init_warnings.append(
                f"RunConfig.end={cfg.end} 越界(data_end={self._data_end.date()}), 已截断")
            _log.warning("RunConfig.end=%s 越界, 截断到 %s", cfg.end,
                         self._data_end.date())
        end_ts = (min(pd.Timestamp(cfg.end), self._data_end)
                  if cfg.end else self._data_end)
        self._end = str(end_ts.date())

    async def run(self) -> BacktestResult:
        cfg = self.cfg
        reader, agent, loader = self.reader, self.agent, self.loader
        warnings: List[str] = list(self._init_warnings)

        days = reader.trading_days(cfg.start, self._end)
        if not days:
            res = compute_metrics([], init_cash=cfg.init_cash)
            return BacktestResult(
                portfolio_result=res, trade_log=TradeLog(), decisions_by_date={},
                nav_history=[], benchmark_nav=None, n_llm_calls=agent.n_calls,
                trade_stats=TradeLog().trade_stats(),
                warnings=warnings + ["no trading days in window"])

        # news blind-spot warning (§4.1)
        ndm = reader.news_date_max()
        if ndm is not None and self._end > ndm:
            warnings.append(
                f"自 {ndm} 起无 news, 决策仅依赖 events+rev_20 (评估读新闻请落≤{ndm})")
            _log.warning("news_date_max=%s < window end %s", ndm, self._end)

        p = VirtualPortfolio(init_cash=cfg.init_cash, cost_model=cfg.cost)
        p.seed_initial_nav(days[0])
        broker = Broker(cost_model=cfg.cost)
        log = TradeLog()
        decisions: Dict[str, dict] = {}
        last_filter_stats: Dict[str, int] = {}    # P1.3: 末日 CandidateResult.filter_stats

        for T in days:
            self.trigger.reset_day()              # P3: clear dedup/counters per day
            holdings = list(p.positions.keys())
            cand = select_candidates(T, holdings, reader, cfg.candidate)
            last_filter_stats = cand.filter_stats
            visible = reader.get_visible_info(T, codes=cand.codes, as_of=cfg.as_of)

            inp = DecisionInput(
                date=T, as_of=cfg.as_of, visible=visible,
                candidates=cand.codes, rev20_rank=cand.rev20_rank,
                holdings=p.snapshot()["positions"], cash=p.cash,
                nav=p.snapshot()["nav"])
            decision = await agent.decide(inp)
            decisions[T] = decision.raw
            # P3: stocks the pre-open decision already acted on (non-hold) — the
            # intraday decision channel skips these (no重复加仓 / 省 LLM).
            self._preopen_acted = {leg.code for leg in decision.decisions
                                   if leg.action != "hold"}

            orders = legs_to_orders(decision.decisions, p, reader, loader, T, cfg)
            for order in orders:
                bar, prev_close = prepare_bar(order.code, T, reader, loader, cfg)
                if bar is None or prev_close is None:
                    continue
                fill = broker.match(order, bar, prev_close, p)
                log.add_fill(fill)

            # P3: intraday key-point re-decision (only when enabled).
            if self.trigger.cfg.enabled:
                await self._run_intraday(T, p, broker, log, cand, decisions, warnings)

            # EOD: mark + stop. stop goes through the Broker (realistic fill).
            lows = {c: _day_low(loader, c, T) for c in list(p.positions.keys())}
            lows = {c: v for c, v in lows.items() if v is not None}
            for code, stop_px in p.check_stop(lows):
                bar, prev_close = prepare_bar(code, T, reader, loader, cfg)
                if bar is None or prev_close is None:
                    continue
                sfill = broker.match(
                    Order(code=code, side="sell", otype="stop",
                          limit_price=stop_px), bar, prev_close, p)
                log.add_fill(sfill)

            eod_close = {c: _day_close(loader, c, T)
                         for c in list(p.positions.keys())}
            eod_close = {c: v for c, v in eod_close.items() if v is not None}
            p.record_nav(T, prices=eod_close)

        # turnover = total gross traded / mean nav
        mean_nav = (sum(v for _, v in p.nav_history) / len(p.nav_history)
                    if p.nav_history else float("nan"))
        gross = sum(f.gross for f in log.fills)
        turnover = gross / mean_nav if mean_nav and mean_nav == mean_nav else float("nan")

        bench_nav = build_benchmark_nav(
            loader, cfg.benchmark, days, cfg.init_cash, warnings)
        res = compute_metrics(p.nav_history, init_cash=cfg.init_cash,
                              turnover=turnover, benchmark_nav=bench_nav)
        return BacktestResult(
            portfolio_result=res, trade_log=log, decisions_by_date=decisions,
            nav_history=p.nav_history, benchmark_nav=bench_nav,
            n_llm_calls=agent.n_calls, trade_stats=log.trade_stats(),
            warnings=warnings, candidate_filter_stats=last_filter_stats)

    # ======================================================================
    # P3 — intraday key-point re-decision (only entered when intraday.enabled)
    # ======================================================================
    @staticmethod
    def _row_to_bar(row) -> dict:
        """Pick only the 6 keys Broker needs; drop amount/etc. ``trade_date`` is
        the 5min minute-level Timestamp so ``fill.bar_ts`` reflects the i+1 bar."""
        return {"trade_date": row["trade_date"],
                "open": float(row["open"]), "high": float(row["high"]),
                "low": float(row["low"]), "close": float(row["close"]),
                "vol": float(row["vol"])}

    async def _run_intraday(self, T, p, broker, log, cand, decisions,
                            warnings) -> None:
        """Drive the intraday loop for day ``T`` along the真实 time axis.

        watch set = current holdings ∪ pre-open candidates (never the full
        market). Each watch code's whole-day 5min bars are fetched once; the
        merged trade_date axis is推进 in real wall-clock order so the global cap
        is allocated by真实 先后 (m1 fix). Per stock, only its own ≤t prefix is
        ever passed to the trigger (PIT correctness, independent of the merge).
        """
        reader = self.reader
        trigger = self.trigger

        watch_codes = list(dict.fromkeys(
            list(p.positions.keys()) + list(cand.codes)))

        bars_by_code: Dict[str, pd.DataFrame] = {}
        for code in watch_codes:
            bars = reader.fetch_bars_intraday(code, T, "5min")
            if bars is None or len(bars) == 0:
                continue
            bars_by_code[code] = (bars.sort_values("trade_date")
                                  .reset_index(drop=True))
        if not bars_by_code:
            return

        # merged real time axis + (ts -> {code: row index})
        idx_by_ts: Dict[str, Dict[str, int]] = {}
        for code, df in bars_by_code.items():
            for i, t in enumerate(df["trade_date"]):
                idx_by_ts.setdefault(str(t), {})[code] = i
        ts_axis = sorted(idx_by_ts.keys())

        raw_T = decisions.get(T)
        # Only a normal (non-_error) pre-open raw dict can carry the _intraday
        # log; the key is created LAZILY on the first appended record (§2.4 —
        # a zero-trigger day never grows an _intraday key).
        can_log = isinstance(raw_T, dict) and "_error" not in raw_T

        def _append_intraday(rec: dict) -> None:
            if can_log:
                raw_T.setdefault("_intraday", []).append(rec)

        for ts in ts_axis:
            present = idx_by_ts[ts]
            # same instant: holdings first (risk priority), then other candidates
            ordered = ([c for c in p.positions.keys() if c in present]
                       + [c for c in watch_codes
                          if c in present and c not in p.positions])
            for code in ordered:
                i = present[code]
                bars = bars_by_code[code]
                bars_upto_t = bars.iloc[: i + 1]           # 闸一: only ≤ bar i
                pos = p.positions.get(code)
                sellable = pos.sellable(_norm_date(T)) if pos is not None else 0
                ev = trigger.check(code, bars_upto_t, pos, sellable_qty=sellable,
                                   i=i)
                if ev is None:
                    continue
                legs, decision_raw = await self._intraday_decide(T, ev, p, cand)
                self._match_intraday_legs(T, ev, legs, bars, p, broker, log,
                                          _append_intraday, decision_raw, warnings)

    async def _intraday_decide(self, T, ev: TriggerEvent, p, cand):
        """Return (legs, decision_raw). Risk class → deterministic rule (0 LLM);
        decision class → second LLM decide, but only when there is room to act."""
        if ev.is_risk:                       # stop_break
            pos = p.positions.get(ev.code)
            if pos is None or pos.sellable(_norm_date(T)) <= 0:
                return [], None              # T+1 locked / gone → no order
            return [_StopLeg(code=ev.code, stop_px=pos.stop_loss)], None

        # decision class (breakout / volume_surge)
        # (a) pre-open already acted on this stock → skip (no重复 + 省 LLM)
        if ev.code in self._preopen_acted:
            return [], None
        # (b) add with no cash → skip before spending an LLM call
        pos = p.positions.get(ev.code)
        if pos is None and p.cash < self._min_buy_notional():
            return [], None
        decision = await self._do_intraday_llm(T, ev, p, cand)
        return decision.decisions, decision.raw

    def _min_buy_notional(self) -> float:
        """Rough one-lot (100 sh) notional floor for the intraday cash pre-check.

        No市场价 here yet, so use a conservative absolute floor: 100 shares at a
        nominal 1 元 + the fixed 5-元 commission floor. Below this, even the
        cheapest lot is unaffordable → skip the LLM call. (Generous on purpose —
        the Broker still truncates by真实 cash; this只 avoids a guaranteed-丢弃
        LLM round trip.)"""
        return 100.0 + self.cfg.cost.min_commission

    async def _do_intraday_llm(self, T, ev: TriggerEvent, p, cand):
        bar_hhmm = pd.Timestamp(ev.bar_time).strftime("%H:%M:%S")
        visible = self._intraday_visible(T, ev.code, bar_hhmm)
        snap = p.snapshot()
        inp = DecisionInput(
            date=T, as_of=bar_hhmm, visible=visible, candidates=[ev.code],
            rev20_rank={ev.code: cand.rev20_rank.get(ev.code, float("nan"))},
            holdings=snap["positions"], cash=p.cash, nav=snap["nav"],
            intraday=IntradayCtx(kind=ev.kind, bar_index=ev.bar_index,
                                 metric=ev.metric, detail=ev.detail))
        return await self.agent.decide(inp)

    def _intraday_visible(self, T, code, bar_hhmm):
        """as_of-aware visible info for the intraday path.

        news/policy are already truncated by ``ts <= boundary`` inside
        ``get_visible_info``. events use a日级 ``ann_date <= T`` judge that is
        *independent of as_of* (pit_reader §R4), so an ann_date==T event whose
        ``session`` is intraday/post_close would leak future info at an intraday
        boundary. Drop those here so events align with the news/policy semantics.
        """
        vi = self.reader.get_visible_info(T, codes=[code], as_of=bar_hhmm)
        if bar_hhmm < "15:00:00":
            vi.events = [
                e for e in vi.events
                if not (str(getattr(e, "ann_date", "")) == T
                        and getattr(e, "session", "pre_open") != "pre_open")]
        return vi

    def _match_intraday_legs(self, T, ev: TriggerEvent, legs, bars, p, broker,
                             log, append_intraday, decision_raw, warnings) -> None:
        """Match the second-decision legs on bar i+1 (never bar i — that would
        price off the triggering bar's未来 info). legs_to_orders only emits limit
        orders → a single fill bar suffices; a triggered ``_StopLeg`` becomes a
        dn-floor limit sell. ``append_intraday(rec)`` records lazily."""
        fill_idx = ev.bar_index + 1
        if fill_idx >= len(bars):
            # triggered on the day's last bar (≈15:00) → no bar after → no fill
            append_intraday({"trigger": _ev_dict(ev), "filled": [],
                             "decision_raw": decision_raw,
                             "note": "no_bar_after_trigger"})
            return
        fill_bar = self._row_to_bar(bars.iloc[fill_idx])
        prev_close = _ref_prev_close_leq(self.loader, ev.code, T)

        # build orders
        orders: List[Order] = []
        stop_legs = [lg for lg in legs if isinstance(lg, _StopLeg)]
        decision_legs = [lg for lg in legs if not isinstance(lg, _StopLeg)]
        # A triggered intraday stop is executed on bar i+1 as a 跌停价(dn)-floor
        # LIMIT sell — NOT an otype="stop" (whose touch re-check on i+1 could
        # mis-reject if the price已 recovered above the stop by the next bar).
        # The breach was already confirmed on bar i; the dn floor always clears
        # the touch check and the Broker clips the realized fill into [low,high].
        for sl in stop_legs:
            if prev_close is None:
                continue
            pct = limit_pct_for(sl.code)
            dn = round(prev_close * (1 - pct), 2)
            orders.append(Order(code=sl.code, side="sell", otype="limit",
                                limit_price=dn))
        if decision_legs:
            orders.extend(legs_to_orders(decision_legs, p, self.reader,
                                         self.loader, T, self.cfg))

        filled = []
        note = None
        for order in orders:
            pc = (prev_close if order.code == ev.code
                  else _ref_prev_close_leq(self.loader, order.code, T))
            if pc is None:
                continue
            fill = broker.match(order, fill_bar, pc, p)
            if fill is not None:
                fill.reason = ev.detail
                log.add_fill(fill)
                filled.append({"code": order.code, "side": order.side,
                               "price": fill.price, "qty": fill.qty})
            elif (ev.is_risk and order.side == "sell"
                  and broker.last_reason == "t1_locked_or_empty"):
                note = "t1_locked"
        rec = {"trigger": _ev_dict(ev), "bar_time": str(fill_bar["trade_date"]),
               "filled": filled, "decision_raw": decision_raw}
        if note:
            rec["note"] = note
        append_intraday(rec)
