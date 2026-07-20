# -*- coding: utf-8 -*-
"""handler.pv.microstructure — deterministic microstructure projection (Phase 8, Task 5).

A pure projection over the optional ``l1 book / ticks / tape`` DataResult feeds into the
``MicrostructureReport`` field set, with the orderbook 空档降级 honesty precedent: for
EVERY absent optional feed the corresponding metric is ``None`` AND the absence is named
in ``degradation`` — a down feed is never back-filled with a zero or an imputed value.

Pure + import-safe: no engine / datafeed / network / LLM import (only ``math`` for a
finiteness guard). 端点常坏, 降级不 crash.
"""
from __future__ import annotations

import math


def _finite(value) -> float | None:
    """Coerce to a finite float, or ``None`` (a non-finite / non-numeric datum is absent)."""
    if value is None:
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def project(*, l1_book=None, ticks=None, tape=None) -> dict:
    """Project the microstructure metric set + an honest ``degradation`` tuple.

    ``l1_book`` supplies ``l1_spread_bp`` + ``bid_ask_imbalance``; ``tape`` supplies
    ``break_ratio`` + ``whale_net_inflow``; ``ticks`` is a confirmation feed. A ``None``
    feed contributes a ``degradation`` row and leaves its metrics ``None`` — nothing is
    imputed.
    """
    degradation: list[str] = []

    l1_spread_bp: float | None = None
    bid_ask_imbalance: float | None = None
    if l1_book is None:
        degradation.append("l1 order book unavailable")
    else:
        bid = _finite(l1_book.get("bid"))
        ask = _finite(l1_book.get("ask"))
        if bid is not None and ask is not None:
            mid = (bid + ask) / 2.0
            l1_spread_bp = ((ask - bid) / mid * 10000.0) if mid > 0 else None
        bid_vol = _finite(l1_book.get("bid_vol"))
        ask_vol = _finite(l1_book.get("ask_vol"))
        if bid_vol is not None and ask_vol is not None and (bid_vol + ask_vol) > 0:
            bid_ask_imbalance = (bid_vol - ask_vol) / (bid_vol + ask_vol)

    if ticks is None:
        degradation.append("tick tape unavailable")

    break_ratio: float | None = None
    whale_net_inflow: float | None = None
    if tape is None:
        degradation.append("market tape unavailable (break ratio / whale net inflow)")
    else:
        break_ratio = _finite(tape.get("break_ratio"))
        whale_net_inflow = _finite(tape.get("whale_net_inflow"))

    return {
        "l1_spread_bp": l1_spread_bp,
        "bid_ask_imbalance": bid_ask_imbalance,
        "break_ratio": break_ratio,
        "whale_net_inflow": whale_net_inflow,
        "degradation": tuple(degradation),
    }
