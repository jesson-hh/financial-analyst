from __future__ import annotations
from typing import Any, Dict, Optional
import pandas as pd


def score_board(
    daily_df: pd.DataFrame,
    turnover_rate: float,
    market_cap_yi: float,
    bars_5m_on_board_day: Optional[pd.DataFrame] = None,
) -> Dict[str, Any]:
    """v5 board scorer. v4 dims: turnover_surge + t1_tr_surge + amount + pct_range.
    v5 fifth dim: seal_micro using 5min bars on the limit-up day.
    """
    last_close = daily_df["close"].iloc[-1]
    prev_close = daily_df["close"].iloc[-2] if len(daily_df) >= 2 else last_close
    pct = (last_close / prev_close - 1) if prev_close else 0.0

    if pct < 0.095:
        return {"v4_score": None, "v5_score": None, "total_score": None,
                "detail": "no limit-up detected"}

    avg_tr_60 = daily_df["vol"].iloc[-60:].mean() if len(daily_df) >= 60 else daily_df["vol"].mean()
    tr_surge = turnover_rate / max(avg_tr_60 / max(daily_df["vol"].iloc[-1], 1) * turnover_rate, 1e-6)
    v4 = 0
    if tr_surge > 3.0:
        v4 += 2
    elif tr_surge > 2.0:
        v4 += 1
    elif tr_surge < 0.5:
        v4 -= 1

    if turnover_rate > 15:
        v4 += 1
    elif turnover_rate < 3:
        v4 -= 1

    pct_range_5d = (daily_df["close"].iloc[-5:].max() / daily_df["close"].iloc[-5:].min() - 1) if len(daily_df) >= 5 else 0
    if pct_range_5d > 0.30:
        v4 -= 1
    elif pct_range_5d < 0.05:
        v4 += 1

    if market_cap_yi < 100:
        v4 += 1

    v5_micro = 0
    micro_detail = {}
    if bars_5m_on_board_day is not None and not bars_5m_on_board_day.empty:
        n_bars = len(bars_5m_on_board_day)
        seal_bar = n_bars
        limit_price = last_close
        for i, row in enumerate(bars_5m_on_board_day.itertuples()):
            if abs(getattr(row, "close") - limit_price) / limit_price < 0.001:
                seal_bar = i
                break
        if seal_bar <= 1:
            v5_micro += 2
        elif seal_bar <= 6:
            v5_micro += 1
        elif seal_bar >= 42:
            v5_micro -= 2
        elif seal_bar >= 24:
            v5_micro -= 1
        seal_at_close = abs(bars_5m_on_board_day["close"].iloc[-1] - limit_price) / limit_price < 0.001
        if not seal_at_close:
            v5_micro -= 2
        open_price = bars_5m_on_board_day["open"].iloc[0]
        gap_pct = (open_price / prev_close - 1) if prev_close else 0
        if gap_pct >= 0.09:
            v5_micro += 1
        micro_detail = {
            "seal_bar": seal_bar,
            "seal_at_close": bool(seal_at_close),
            "gap_open": float(gap_pct),
        }

    total = v4 + (v5_micro if bars_5m_on_board_day is not None else 0)
    return {
        "v4_score": v4,
        "v5_score": v5_micro if bars_5m_on_board_day is not None else None,
        "total_score": total,
        "detail": micro_detail,
    }
