"""IntradayTrigger — pure intraday key-point detector for the P3 backtest loop.

Zero IO, zero LLM. Given the 5min bar prefix ``bars.iloc[:i+1]`` (末行 = the bar
just closed at time t) plus a position snapshot, it reports whether a key point
fired (``TriggerEvent``) so the engine can run a second decision and match it on
bar ``i+1``.

Two channels with different budgets (P3 design §1.2):

* **Risk channel — ``stop_break``**: a held position's intraday ``bar.low`` <=
  ``stop_loss``. Exempt from all caps and from dedup (it must never be starved by
  追涨 signals); naturally bounded (≤ positions × bars) and resolved by a
  deterministic rule path (0 LLM). Only fires when ``sellable_qty>0`` (T+1: same-
  day pre-open buys are locked and cannot be sold intraday — no doomed orders).
* **Decision channel — ``breakout_high`` / ``volume_surge``**: bounded by
  ``max_per_day_per_code`` + ``max_per_day_global`` and deduped (same stock + same
  kind fires at most once per day). ``volume_surge`` defaults OFF (A 股 5min volume
  has a strong U-shape day profile and a bare multiple is unvalidated — see §1.6).

The time-series law is enforced by the *caller* (engine passes only
``bars.iloc[:i+1]``) and re-checked here (``hist = bars_upto_t.iloc[:-1]`` never
includes bar i+1). The detector also takes ``i`` explicitly so the event carries
the bar index the engine uses to locate the i+1 fill bar.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Dict, Optional, Set

import pandas as pd

if TYPE_CHECKING:  # avoid a hard import cycle at runtime
    from financial_analyst.backtest.portfolio import Position


@dataclass
class IntradayTriggerConfig:
    enabled: bool = False                 # master switch; False → never fires (= P2)
    # --- decision-channel caps (do NOT constrain stop_break) ---
    max_per_day_per_code: int = 2         # per-day per-stock decision-class cap
    max_per_day_global: int = 6           # per-day global decision-class cap
    min_bars_for_signal: int = 5          # decision class needs ≥N hist bars
    # --- stop_break (risk class, uncapped, never deduped) ---
    stop_break_enabled: bool = True       # held position with bar.low <= stop_loss
    # --- breakout_high (decision class) ---
    breakout_enabled: bool = True
    breakout_min_gain_pct: float = 0.008  # non-zero: filter貼边 假突破
    # --- volume_surge (decision class, default OFF) ---
    volume_surge_enabled: bool = False
    volume_surge_mult: float = 3.0
    volume_surge_window: int = 12         # rolling mean over last N bars (excl. current)


@dataclass
class TriggerEvent:
    code: str
    kind: str                  # "stop_break" | "breakout_high" | "volume_surge"
    bar_time: str              # the triggering bar's full trade_date timestamp str
    bar_index: int             # index i in that day's sorted 5min series
    detail: str                # human-readable cause (prompt + TradeLog.reason)
    metric: float              # quantified value (breakout: high; stop: low; surge: ratio)
    is_risk: bool              # True = risk class (exempt caps/dedup); False = decision


# decision-class试探 order (first matching un-fired kind wins)
_DECISION_ORDER = ("breakout_high", "volume_surge")


def _bar_invalid(cur) -> bool:
    """停牌 / 一字板全 NaN / 无量 → not a usable bar."""
    for k in ("open", "high", "low", "close"):
        v = cur.get(k)
        if v is None or pd.isna(v):
            return True
    vol = cur.get("vol")
    return vol is None or pd.isna(vol) or vol <= 0


class IntradayTrigger:
    """Stateful (per-day) intraday key-point detector."""

    def __init__(self, cfg: Optional[IntradayTriggerConfig] = None) -> None:
        self.cfg = cfg if cfg is not None else IntradayTriggerConfig()
        self._fired: Dict[str, Set[str]] = {}        # code -> {fired decision kinds}
        self._count_per_code: Dict[str, int] = {}    # code -> decision-class count
        self._count_global: int = 0                  # global decision-class count

    def reset_day(self) -> None:
        """Clear dedup + counters. Called at the head of each trading day."""
        self._fired.clear()
        self._count_per_code.clear()
        self._count_global = 0

    def check(self, code: str, bars_upto_t: pd.DataFrame,
              position: Optional["Position"], sellable_qty: int,
              i: int) -> Optional[TriggerEvent]:
        """Detect a key point for ``code`` at the bar prefix ``bars_upto_t``.

        ``bars_upto_t`` is the sorted day-5min prefix ``bars.iloc[:i+1]`` (末行 =
        the bar that just closed). ``i`` is its index in the day series. The
        caller guarantees no bar > i is present (time-series law,闸一).
        """
        cfg = self.cfg
        if len(bars_upto_t) == 0:
            return None
        cur = bars_upto_t.iloc[-1]
        if _bar_invalid(cur):
            return None
        cur_time = str(cur["trade_date"])

        # --- risk channel (stop_break): evaluated BEFORE any cap/warm-up ---
        if (cfg.stop_break_enabled and position is not None
                and getattr(position, "stop_loss", 0.0) > 0
                and float(cur["low"]) <= position.stop_loss
                and sellable_qty > 0):
            return TriggerEvent(
                code=code, kind="stop_break", bar_time=cur_time, bar_index=i,
                detail=(f"5min跌破止损 low={float(cur['low']):.2f}"
                        f"<=stop={position.stop_loss:.2f}"),
                metric=float(cur["low"]), is_risk=True)

        # --- decision channel: warm-up + caps ---
        if len(bars_upto_t) < cfg.min_bars_for_signal:
            return None
        if self._count_global >= cfg.max_per_day_global:
            return None
        if self._count_per_code.get(code, 0) >= cfg.max_per_day_per_code:
            return None

        hist = bars_upto_t.iloc[:-1]   # strictly < bar i (never includes i+1)
        fired = self._fired.get(code, set())

        for kind in _DECISION_ORDER:
            if kind in fired:
                continue
            ev = self._eval_decision(kind, code, cur, hist, cur_time, i)
            if ev is not None:
                self._fired.setdefault(code, set()).add(kind)
                self._count_per_code[code] = self._count_per_code.get(code, 0) + 1
                self._count_global += 1
                return ev
        return None

    # ------------------------------------------------------------------
    def _eval_decision(self, kind: str, code: str, cur, hist,
                       cur_time: str, i: int) -> Optional[TriggerEvent]:
        cfg = self.cfg
        if kind == "breakout_high":
            if not cfg.breakout_enabled or len(hist) < 1:
                return None
            prefix_max = float(hist["high"].max())
            cur_high = float(cur["high"])
            if cur_high > prefix_max * (1 + cfg.breakout_min_gain_pct):
                return TriggerEvent(
                    code=code, kind=kind, bar_time=cur_time, bar_index=i,
                    detail=(f"5min突破日内高 high={cur_high:.2f}>前高{prefix_max:.2f}"
                            f"(+{cfg.breakout_min_gain_pct:.1%})"),
                    metric=cur_high, is_risk=False)
            return None

        if kind == "volume_surge":
            if not cfg.volume_surge_enabled or len(hist) < cfg.volume_surge_window:
                return None
            avg_vol = float(hist["vol"].tail(cfg.volume_surge_window).mean())
            cur_vol = float(cur["vol"])
            if avg_vol > 0 and cur_vol > cfg.volume_surge_mult * avg_vol:
                ratio = cur_vol / avg_vol
                return TriggerEvent(
                    code=code, kind=kind, bar_time=cur_time, bar_index=i,
                    detail=(f"5min放量 vol={cur_vol:.0f}={ratio:.1f}x"
                            f"近{cfg.volume_surge_window}根均量"),
                    metric=ratio, is_risk=False)
            return None

        return None
