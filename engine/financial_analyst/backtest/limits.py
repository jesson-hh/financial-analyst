"""Price-limit (涨跌停) helpers for the P1 backtest engine.

Three pieces, all pure / zero-IO:

* ``limit_pct_for`` — per-board daily limit width (10% main board, 20%
  ChiNext/STAR, 30% Beijing, 5% ST).
* ``compute_ref_prev_close`` — ex-div-corrected reference previous close
  ``raw_close[t-1]*factor[t-1]/factor[t]``. Empirically (SZ000001 full
  history) this collapses the 4 raw ex-div days that look like >10.5% moves
  down to 0 days >10.5%. The exchange computes limits off the ex-div-adjusted
  prev close, not the raw one, so the raw prev close must be corrected.
* ``is_one_word`` — one-word (一字板) detection using the *reference* prev
  close, so ex-div days are not misread as limit days.

``factor`` only exists at day frequency in the Qlib layout; within a day it is
constant, so the caller computes ``ref_prev_close`` once per trade_date off the
day series and reuses the scalar across all 5min bars of that date.
"""
from __future__ import annotations

from typing import Mapping

import pandas as pd

# one-word / limit comparison tolerance (prices rounded to fen)
_TOL = 1e-4


def limit_pct_for(code: str, is_st: bool = False) -> float:
    """Daily price-limit fraction for ``code``.

    Priority: ST (5%) > Beijing (30%) > ChiNext/STAR (20%) > main board (10%).
    ``is_st`` is caller-injected (the bin store has no ST/name field); when
    unknown it defaults False and ST stocks fall back to 10%/20% — a known
    limitation (ST one-word at 5% can be misjudged).
    """
    if is_st:
        return 0.05
    code = code.upper()
    if code.startswith("BJ"):
        return 0.30
    if code.startswith(("SH688", "SZ300")):
        return 0.20
    return 0.10


def compute_ref_prev_close(day_close: pd.Series, factor: pd.Series) -> pd.Series:
    """Ex-div-corrected reference previous close, date-aligned.

    ``ref_prev_close[t] = raw_close[t-1] * factor[t-1] / factor[t]``.

    Inputs are indexed by datetime (day freq). They may have slightly
    different lengths (e.g. SZ000001 close=3981 vs factor=3930); a
    ``concat(...).dropna()`` aligns them and drops the early days without a
    factor (immaterial for recent-year backtests).
    """
    df = pd.concat([day_close.rename("c"), factor.rename("f")], axis=1).dropna()
    return df["c"].shift(1) * df["f"].shift(1) / df["f"]


def is_one_word(bar: Mapping, prev_close: float, pct: float, side: str) -> bool:
    """One-word board detection (``side='up'`` or ``'down'``).

    True iff high==low (no intraday range), volume>0, and close is at the
    limit relative to the *reference* prev close.
    """
    high = bar["high"]
    low = bar["low"]
    if high != low or bar.get("vol", 0) <= 0:
        return False
    sign = 1.0 if side == "up" else -1.0
    return abs(bar["close"] / prev_close - (1 + sign * pct)) < _TOL
