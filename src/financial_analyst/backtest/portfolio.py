"""VirtualPortfolio + Position — cash/position ledger for the P1 backtest engine.

Aligns with PLAN §7.4 portfolio state
``{cash, nav, positions:{code:{qty, avg_cost, stop_loss, mkt_value}}, date}``.

Design invariants (see P1 spec §0-§1):

* **RAW prices throughout.** ``close.day.bin`` is unadjusted; cash, avg_cost and
  mkt_value all use raw prices so nominal cash conserves against the tape.
* **trade_date is always 'YYYY-MM-DD' str.** ``_norm_date`` normalizes any
  ``pd.Timestamp`` at the boundary (the loader emits datetime64), so T+1 string
  comparisons never TypeError.
* **NAV history stores levels (元), normalized to net value only at the metrics
  boundary.** ``nav_history[0]`` must be a synthetic init point (nav==init_cash)
  so the metrics bridge keeps the initial peak (correct max-drawdown).
* **Realized PnL is recorded on every sell** (gross_sell - sell_cost -
  qty*avg_cost), with avg_cost carrying the buy-side cost — so both legs' costs
  land in realized pnl, self-consistent with the cash delta.

Pure ledger: no bin reads, no network. Matching lives in ``broker.py``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import pandas as pd

from financial_analyst.backtest.costs import CostModel


def _norm_date(d) -> str:
    """Normalize a trade_date to 'YYYY-MM-DD' str.

    The Qlib loader's ``_build_df`` emits a datetime64 ``trade_date`` column
    (reset_index off a DatetimeIndex); putting that straight into ``locked``
    keys and comparing to a str ``today`` would TypeError / mis-sort. Every
    public entry point normalizes first.
    """
    if isinstance(d, str):
        return d
    return pd.Timestamp(d).strftime("%Y-%m-%d")


@dataclass
class Position:
    code: str
    qty: int                      # total shares (buys累积 in 100-lots; sells may leave odd lots)
    avg_cost: float               # holding average cost (buy-side cost amortized in)
    stop_loss: float = 0.0        # stop price (0 = none)
    mkt_value: float = 0.0        # qty * last_close after last mark_to_market
    locked: Dict[str, int] = field(default_factory=dict)   # T+1: {trade_date_str: qty_bought}
    realized_pnl: float = 0.0     # cumulative realized pnl for this code (incl. both-side cost)

    def sellable(self, today: str) -> int:
        """Sellable = total qty - shares bought on or after ``today`` (T+1)."""
        today = _norm_date(today)
        locked_today = sum(q for d, q in self.locked.items() if d >= today)
        return max(0, self.qty - locked_today)


@dataclass
class Fill:
    """A single executed trade (also produced by Broker; re-exported there)."""

    code: str
    side: str
    qty: int
    price: float
    trade_date: str
    bar_ts: str = ""
    gross: float = 0.0
    cost: float = 0.0
    cash_after: float = 0.0
    realized_pnl: float = 0.0   # sell only (buy = 0)
    reason: str = ""


@dataclass
class VirtualPortfolio:
    init_cash: float = 1_000_000.0
    cash: float = 1_000_000.0
    positions: Dict[str, Position] = field(default_factory=dict)
    date: Optional[str] = None
    nav_history: List[Tuple[str, float]] = field(default_factory=list)
    cost_model: CostModel = field(default_factory=CostModel)
    realized_pnl_total: float = 0.0
    _last_mark_date: Optional[str] = None

    # ------------------------------------------------------------------
    # Trading
    # ------------------------------------------------------------------

    def buy(self, code: str, qty: int, price: float, trade_date,
            stop_loss: float = 0.0) -> Optional[Fill]:
        """Buy ``qty`` shares at ``price``. Returns None if cash insufficient
        (no partial fill). On success: deduct cash incl. cost, update/create
        Position, lock the qty for T+1, return Fill. Does not touch realized pnl.
        """
        td = _norm_date(trade_date)
        if qty <= 0:
            return None
        gross = price * qty
        cost = self.cost_model.buy_cost(price, qty, code)
        total = gross + cost
        if total > self.cash + 1e-9:
            return None
        self.cash -= total
        pos = self.positions.get(code)
        if pos is None:
            pos = Position(code=code, qty=0, avg_cost=0.0)
            self.positions[code] = pos
        new_qty = pos.qty + qty
        pos.avg_cost = (pos.qty * pos.avg_cost + gross + cost) / new_qty
        pos.qty = new_qty
        if stop_loss:
            pos.stop_loss = stop_loss
        pos.locked[td] = pos.locked.get(td, 0) + qty
        self.date = td
        return Fill(code=code, side="buy", qty=qty, price=price, trade_date=td,
                    gross=gross, cost=cost, cash_after=self.cash, realized_pnl=0.0)

    def sell(self, code: str, qty: int, price: float, trade_date) -> Optional[Fill]:
        """Sell ``qty`` shares (must be <= sellable). Adds cash net of cost,
        reduces qty, records realized pnl. Returns None if qty > sellable.
        avg_cost is unchanged; Position deleted when qty hits zero (its
        realized_pnl already folded into the running total).
        """
        td = _norm_date(trade_date)
        pos = self.positions.get(code)
        if pos is None or qty <= 0:
            return None
        if qty > pos.sellable(td):
            return None
        gross = price * qty
        cost = self.cost_model.sell_cost(price, qty, code)
        realized = gross - cost - qty * pos.avg_cost
        self.cash += gross - cost
        self.realized_pnl_total += realized
        pos.realized_pnl += realized
        pos.qty -= qty
        if pos.qty <= 0:
            del self.positions[code]
        else:
            pos.mkt_value = pos.qty * price
        self.date = td
        return Fill(code=code, side="sell", qty=qty, price=price, trade_date=td,
                    gross=gross, cost=cost, cash_after=self.cash, realized_pnl=realized)

    # ------------------------------------------------------------------
    # Valuation
    # ------------------------------------------------------------------

    def mark_to_market(self, prices: Dict[str, float], on_date) -> None:
        """Refresh each Position.mkt_value = qty*close. Missing price (停牌/no
        bar) keeps the previous mkt_value (no clear, no future price)."""
        for code, pos in self.positions.items():
            px = prices.get(code)
            if px is not None:
                pos.mkt_value = pos.qty * px
        self._last_mark_date = _norm_date(on_date)

    def check_stop(self, lows: Dict[str, float]) -> List[Tuple[str, float]]:
        """Report (code, stop_price) for positions whose bar ``low`` breached a
        positive stop. Reporting only — the actual stop sell goes through the
        Broker (stop semantics), so it still passes limit/T+1/cost checks and
        fills at a realistic price, not the optimistic stop price."""
        out: List[Tuple[str, float]] = []
        for code, pos in self.positions.items():
            if pos.stop_loss > 0:
                low = lows.get(code)
                if low is not None and low <= pos.stop_loss:
                    out.append((code, pos.stop_loss))
        return out

    def snapshot(self) -> Dict:
        """PLAN §7.4 contract dict."""
        nav = self.cash + sum(p.mkt_value for p in self.positions.values())
        return {
            "cash": self.cash,
            "nav": nav,
            "positions": {
                c: {
                    "qty": p.qty,
                    "avg_cost": p.avg_cost,
                    "stop_loss": p.stop_loss,
                    "mkt_value": p.mkt_value,
                }
                for c, p in self.positions.items()
            },
            "date": self.date,
        }

    # ------------------------------------------------------------------
    # NAV recording
    # ------------------------------------------------------------------

    def seed_initial_nav(self, start_date) -> float:
        """Drop the synthetic initial NAV point (start_date, init_cash).

        Must be called once before the first bar is matched so ``nav_history[0]``
        carries the initial peak (correct max-drawdown in the metrics bridge).
        """
        sd = _norm_date(start_date)
        self.date = sd
        nav = self.cash + sum(p.mkt_value for p in self.positions.values())
        self.nav_history = [(sd, nav)]
        return nav

    def record_nav(self, date, prices: Optional[Dict[str, float]] = None) -> float:
        """Append the current NAV as ``date``'s close point; return NAV.

        If ``prices`` is given, mark_to_market(prices, date) runs first
        (mark+record atomic — no stale-NAV risk). If ``prices`` is None, asserts
        that this bar was already marked (``_last_mark_date == date``), except
        for the first synthetic point with no positions. Repeated calls for the
        same date overwrite the last point (no intra-day duplicate points).
        """
        d = _norm_date(date)
        if prices is not None:
            self.mark_to_market(prices, d)
        elif self.positions:
            assert self._last_mark_date == d, (
                f"record_nav({d}) without prices but last mark was "
                f"{self._last_mark_date}: this bar was not marked"
            )
        nav = self.cash + sum(p.mkt_value for p in self.positions.values())
        if self.nav_history and self.nav_history[-1][0] == d:
            self.nav_history[-1] = (d, nav)
        else:
            self.nav_history.append((d, nav))
        self.date = d
        self._compact_locked(d)
        return nav

    # ------------------------------------------------------------------
    # Housekeeping
    # ------------------------------------------------------------------

    def _compact_locked(self, today: str) -> None:
        """Drop fully-unlocked (d < today) locked entries to keep dicts compact
        over long backtests. They no longer affect sellable()."""
        today = _norm_date(today)
        for pos in self.positions.values():
            stale = [d for d in pos.locked if d < today]
            for d in stale:
                del pos.locked[d]
