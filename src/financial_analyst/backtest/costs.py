"""Transaction cost model for the P1 backtest engine.

Decomposed (commission / stamp / transfer / slippage) so each leg is auditable.
Defaults are the *corrected* A-share retail rates — in particular stamp duty is
the post-2023-08 0.05% sell-side value, NOT the stale 0.1% baked into the
research-side ``stocks/config.py`` ``CLOSE_COST=0.00127`` blend.

Pure computation, zero IO, zero network. ``code`` only matters for the
Shanghai-vs-other transfer-fee split (SH* pays 万1 双边).
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class CostModel:
    """A-share retail cost model.

    Parameters
    ----------
    commission_rate:
        Brokerage commission, both sides, default 万2.5 (0.00025).
    min_commission:
        Per-order commission floor, default 5 元.
    stamp_rate:
        Stamp duty, **sell side only**, default 万5 (0.0005) — the correct
        post-2023-08 value, not the legacy 0.1%.
    transfer_rate_sh:
        Shanghai transfer fee, both sides, default 万1 (0.0001).
    transfer_rate_other:
        Shenzhen / Beijing transfer fee (folded into commission), default 0.
    slippage_bps:
        One-sided slippage in basis points, default 5. Applied adversely
        (buy higher, sell lower) by ``slip_buy`` / ``slip_sell``.
    """

    commission_rate: float = 0.00025
    min_commission: float = 5.0
    stamp_rate: float = 0.0005
    transfer_rate_sh: float = 0.0001
    transfer_rate_other: float = 0.0
    slippage_bps: float = 5.0

    # ------------------------------------------------------------------
    # Per-leg fees
    # ------------------------------------------------------------------

    def transfer_rate_for(self, code: str) -> float:
        return self.transfer_rate_sh if code.upper().startswith("SH") else self.transfer_rate_other

    def buy_cost(self, price: float, qty: int, code: str) -> float:
        g = price * qty
        return max(g * self.commission_rate, self.min_commission) + g * self.transfer_rate_for(code)

    def sell_cost(self, price: float, qty: int, code: str) -> float:
        g = price * qty
        return (
            max(g * self.commission_rate, self.min_commission)
            + g * self.stamp_rate
            + g * self.transfer_rate_for(code)
        )

    # ------------------------------------------------------------------
    # Slippage (one-sided, adverse)
    # ------------------------------------------------------------------

    def slip_buy(self, px: float) -> float:
        return px * (1 + self.slippage_bps / 1e4)

    def slip_sell(self, px: float) -> float:
        return px * (1 - self.slippage_bps / 1e4)

    # ------------------------------------------------------------------
    # Exact affordable quantity
    # ------------------------------------------------------------------

    def affordable_qty(self, fill_px: float, cash: float, code: str) -> int:
        """Largest 100-share lot count buyable with ``cash`` at ``fill_px``.

        Includes the 5-元 fixed commission floor: a proportional-rate estimate
        can over-count when the floor binds, so we estimate then step down by
        one lot until ``fill_px*q + buy_cost(fill_px, q) <= cash``. Guarantees
        the truncated quantity is actually affordable (closes the
        "approximate-rate truncation doesn't close" gap).
        """
        if fill_px <= 0:
            return 0
        denom = fill_px * (1 + self.commission_rate + self.transfer_rate_for(code))
        q = int(cash // denom // 100 * 100)
        while q > 0 and (fill_px * q + self.buy_cost(fill_px, q, code)) > cash:
            q -= 100
        return max(0, q)
