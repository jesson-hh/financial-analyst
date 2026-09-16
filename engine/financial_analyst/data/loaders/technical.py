"""Causal, versioned daily technical fields; never writes provider binaries.

Definitions follow stocks/src/data/factors_calc.py, NOT v4._factors_one:
in particular ``amihud_20`` uses actual turnover amount, not share volume.
All input prices/amounts must already have the loader's normalisation applied.
"""
from __future__ import annotations

import hashlib
import json
import numpy as np
import pandas as pd

TECH_VERSION = "stocks_tech_causal_recompute_v1"
TECH_DEFINITIONS = {
    "amihud_20": "mean(abs(close/lag(close,1)-1)/(amount_cny+1e-10),20,min_valid=5)",
    "rsi_14": "100-100/(1+EMA(gain,span14)/(EMA(loss,span14)+1e-10)); adjust=False",
    "macd_signal": "EMA(close,span12)-EMA(close,span26); adjust=False; legacy name is DIF, not DEA",
    "mom_20": "close/lag(close,20)-1",
    "mom_60": "close/lag(close,60)-1",
    "mom_120": "close/lag(close,120)-1",
}


def technical_definition_fingerprint() -> str:
    """Stable semantic cache identity; bump TECH_VERSION for policy/code changes."""
    value = {"version": TECH_VERSION, "definitions": TECH_DEFINITIONS}
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=True).encode()).hexdigest()


def compute_daily_technical(quote: pd.DataFrame) -> pd.DataFrame:
    """Compute on a single stock's full causal history BEFORE request slicing.

    Retain calendar gaps (no price/technical ffill). The upstream EMA definition
    includes zero gain/loss for a missing delta; outputs on absent-close dates
    are nevertheless missing. Amihud additionally needs today's valid return
    and actual positive amount, so an absent denominator cannot reuse old impact.
    Caller must truncate source observations to its requested as-of first.
    """
    if not quote.index.is_unique or not quote.index.is_monotonic_increasing:
        raise ValueError("Technical inputs require a unique increasing daily index")
    if "close" not in quote:
        return pd.DataFrame(index=quote.index, columns=TECH_DEFINITIONS, dtype=float)
    close = pd.to_numeric(quote["close"], errors="coerce").astype(float)
    close = close.where(np.isfinite(close) & (close > 0))
    amount = (pd.to_numeric(quote["amount"], errors="coerce").astype(float)
              if "amount" in quote else pd.Series(np.nan, index=quote.index))
    amount = amount.where(np.isfinite(amount) & (amount > 0))
    ret = close / close.shift(1) - 1.0
    delta = close.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = (-delta).where(delta < 0, 0.0)
    out = pd.DataFrame(index=quote.index)
    out["amihud_20"] = ((ret.abs() / (amount + 1e-10))
                         .rolling(20, min_periods=5).mean()
                         .where(ret.notna() & amount.notna()))
    ratio = (gain.ewm(span=14, adjust=False).mean()
             / (loss.ewm(span=14, adjust=False).mean() + 1e-10))
    out["rsi_14"] = (100.0 - 100.0 / (1.0 + ratio)).where(close.notna())
    out["macd_signal"] = (close.ewm(span=12, adjust=False).mean()
                           - close.ewm(span=26, adjust=False).mean()).where(close.notna())
    for n in (20, 60, 120):
        out[f"mom_{n}"] = close / close.shift(n) - 1.0
    return out.replace([np.inf, -np.inf], np.nan)
