"""Broker.match — single-bar order matching for the P1 backtest engine.

Pure function over (order, bar, ref_prev_close, portfolio): no bin reads, no
network. The caller supplies the ex-div-corrected ``prev_close`` (see
``limits.compute_ref_prev_close``) so the Broker stays single-testable.

Matching rules (order = priority), see P1 spec §2.2:

1. column-existence + suspension guards (``col in bar``, NaN, vol<=0)
2. compute limit prices off ref_prev_close
3. one-word board → P1 blocks BOTH directions (no fill-queue data)
4. market order → next-bar open, but next_bar must be the SAME trade_date
5. limit / stop matching
6. quantity resolve + exact cash truncation
7. land on the portfolio (buy/sell)

Slippage is applied then clipped to ``[low, high]`` (the bar's realized range,
which already embeds the day's limit band) — never to ``[dn, up]`` (that would
let slippage push fills to prices the bar never traded at).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Optional

import pandas as pd

from financial_analyst.backtest.costs import CostModel
from financial_analyst.backtest.limits import is_one_word, limit_pct_for
from financial_analyst.backtest.portfolio import Fill, VirtualPortfolio, _norm_date

_REQUIRED_COLS = ("open", "high", "low", "close", "vol")
_TOL = 1e-4


@dataclass
class Order:
    code: str
    side: str                       # "buy" | "sell"
    otype: str = "limit"            # "limit" | "market" | "stop"
    limit_price: float = 0.0        # limit: buy=max acceptable / sell=min acceptable; stop: trigger
    qty: Optional[int] = None       # target shares; None → buy uses cash_budget / sell uses sellable
    cash_budget: float = 0.0        # buy + qty=None → size by this budget
    stop_loss: float = 0.0          # written into Position.stop_loss on fill


def _clip(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def _round2(x: float) -> float:
    return round(x, 2)


class Broker:
    """Matches one order against one bar and lands the fill on a portfolio."""

    def __init__(self, cost_model: Optional[CostModel] = None) -> None:
        self.cost_model = cost_model or CostModel()
        # last rejection reason — observable since rejected orders return None.
        self.last_reason: str = ""

    def _reject(self, reason: str) -> None:
        self.last_reason = reason
        return None

    def match(
        self,
        order: Order,
        bar: Optional[Mapping],
        prev_close: float,
        portfolio: VirtualPortfolio,
        next_bar_open: Optional[float] = None,
        next_bar_date=None,
        is_st: bool = False,
    ) -> Optional[Fill]:
        cm = self.cost_model
        self.last_reason = ""

        # --- rule 1: column existence + suspension --------------------
        if bar is None:
            return self._reject("no_bar")
        for col in _REQUIRED_COLS:
            if col not in bar:
                return self._reject("no_bar")
        o, h, low, c, vol = bar["open"], bar["high"], bar["low"], bar["close"], bar["vol"]
        if any(pd.isna(v) for v in (o, h, low, c)):
            return self._reject("suspended")
        if vol is None or vol <= 0:
            return self._reject("suspended")

        td = _norm_date(bar["trade_date"])
        bar_ts = str(bar.get("trade_date", td))

        # --- rule 2: limit prices off ref_prev_close ------------------
        pct = limit_pct_for(order.code, is_st=is_st)
        up = _round2(prev_close * (1 + pct))
        dn = _round2(prev_close * (1 - pct))

        # --- rule 3: one-word board → block BOTH directions -----------
        if is_one_word(bar, prev_close, pct, "up"):
            return self._reject(
                "one_word_limit_up" if order.side == "buy"
                else "one_word_limit_up_no_liquidity")
        if is_one_word(bar, prev_close, pct, "down"):
            return self._reject(
                "one_word_limit_down_no_liquidity" if order.side == "buy"
                else "one_word_limit_down")

        # --- rule 4/5: determine fill price ---------------------------
        fill_px: Optional[float] = None

        if order.otype == "market":
            if next_bar_open is None or next_bar_date is None:
                return self._reject("no_next_bar")
            if _norm_date(next_bar_date) != td:
                return self._reject("no_next_bar")  # cross-day → no leakage
            raw = cm.slip_buy(next_bar_open) if order.side == "buy" else cm.slip_sell(next_bar_open)
            fill_px = _clip(raw, low, h)

        elif order.otype == "stop":
            # stop (protective sell): trigger when bar.low <= stop price
            if low > order.limit_price:
                return self._reject("stop_not_touched")
            raw = cm.slip_sell(min(order.limit_price, o))
            fill_px = _clip(raw, dn, up)
            fill_px = _clip(fill_px, low, h)

        elif order.otype == "limit":
            if order.side == "buy":
                if order.limit_price < low:
                    return self._reject("limit_not_touched")
                raw = cm.slip_buy(min(order.limit_price, h))
                fill_px = _clip(raw, low, h)
            else:  # sell
                if order.limit_price > h:
                    return self._reject("limit_not_touched")
                raw = cm.slip_sell(max(order.limit_price, low))
                fill_px = _clip(raw, low, h)
        else:
            return self._reject("unknown_otype")

        if fill_px is None or fill_px <= 0:
            return self._reject("bad_fill_price")

        # --- rule 6: quantity resolve + truncation --------------------
        if order.side == "buy":
            if order.qty is not None:
                qty = int(order.qty)
            else:
                qty = cm.affordable_qty(fill_px, order.cash_budget, order.code)
                # hard cap by actual cash
                qty = min(qty, cm.affordable_qty(fill_px, portfolio.cash, order.code))
            if qty < 100:
                return self._reject("below_one_lot")
        else:  # sell / stop
            sellable = 0
            pos = portfolio.positions.get(order.code)
            if pos is not None:
                sellable = pos.sellable(td)
            if sellable <= 0:
                return self._reject("t1_locked_or_empty")
            qty = int(order.qty) if order.qty is not None else sellable
            qty = min(qty, sellable)
            if qty <= 0:
                return self._reject("t1_locked_or_empty")

        # --- rule 7: land on the portfolio ----------------------------
        if order.side == "buy":
            fill = portfolio.buy(order.code, qty, fill_px, td, stop_loss=order.stop_loss)
        else:
            fill = portfolio.sell(order.code, qty, fill_px, td)
        if fill is None:
            return self._reject("portfolio_rejected")
        fill.bar_ts = bar_ts
        fill.reason = ""
        return fill
