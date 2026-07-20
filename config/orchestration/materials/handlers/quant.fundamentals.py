# -*- coding: utf-8 -*-
"""handler.quant.fundamentals — deterministic fundamentals projection (Phase 8, Task 6).

Clause (h) impure-fallback: the legacy source (TA ``tier2/fundamental_analyst.py``,
producing ``FundamentalOutput``, plus the astock ``get_profit_forecast`` provenance) lives
in the read-only pinned engine and is a full LLM agent tier — NOT an import-safe pure
function — so this handler is a self-contained stdlib projection over the legacy TYPED
OUTPUT (the valuation score, market-value tier, and forecast provenance note) rather than
importing / re-running it.

Honesty rail: ``inputs_complete`` reflects the HONEST presence of the two core valuation
inputs (valuation score + market-value tier); a missing field stays ``None`` and
``inputs_complete=False`` — never a fabricated score. Any non-finite ``valuation_score``
is dropped to ``None`` (absent).

Pure + import-safe: no engine / datafeed / pandas / network import (only ``math`` for a
finiteness guard).
"""
from __future__ import annotations

import math


def _finite(value) -> float | None:
    if value is None:
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def project(*, valuation_score=None, mv_tier=None, profit_forecast_note=None) -> dict:
    """Project a ``FundamentalOutput`` + forecast provenance into a report field set.

    ``inputs_complete`` = both core inputs present (valuation score + market-value tier).
    ``profit_forecast_note`` is provenance-only (astock ``get_profit_forecast``) and does
    NOT gate completeness — an absent forecast leaves the note ``None`` without lying about
    the valuation inputs' completeness.
    """
    vs = _finite(valuation_score)
    tier = mv_tier if (mv_tier is None or str(mv_tier).strip()) else None
    note = profit_forecast_note if (
        profit_forecast_note is None or str(profit_forecast_note).strip()) else None
    return {
        "valuation_score": vs,
        "mv_tier": tier,
        "profit_forecast_note": note,
        "inputs_complete": vs is not None and tier is not None,
    }
