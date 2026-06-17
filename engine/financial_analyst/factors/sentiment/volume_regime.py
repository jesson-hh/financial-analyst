from __future__ import annotations
from typing import Any, Dict, Optional
import pandas as pd


def compute_vol_regime(
    close_day: pd.Series,
    turnover_rate_day: pd.Series,
    bars_5m_last_day: Optional[pd.DataFrame] = None,
) -> Dict[str, Any]:
    """R16 volume regime classifier. Returns label + signal flags."""
    if len(close_day) < 20:
        return {"regime_label": "neutral", "detail": "insufficient history"}

    ret_20d = float(close_day.iloc[-1] / close_day.iloc[-20] - 1)
    avg_tr_60 = float(turnover_rate_day.iloc[-60:].mean()) if len(turnover_rate_day) >= 60 else float(turnover_rate_day.mean())
    tr_surge_60 = float(turnover_rate_day.iloc[-1] / max(avg_tr_60, 1e-6))

    r9_distr = (ret_20d >= 0.10) and (tr_surge_60 >= 2.5)
    r9_bounce = (ret_20d <= -0.05) and (tr_surge_60 >= 1.5)

    r11_tail_surge = False
    ret_close_30m = vs_close_30m = 0.0
    if bars_5m_last_day is not None and len(bars_5m_last_day) >= 6:
        last_6 = bars_5m_last_day.iloc[-6:]
        ret_close_30m = float(last_6["close"].iloc[-1] / last_6["close"].iloc[0] - 1)
        avg_vol_day = bars_5m_last_day["vol"].mean()
        vs_close_30m = float(last_6["vol"].sum() / (6 * avg_vol_day) - 1) if avg_vol_day else 0.0
        r11_tail_surge = (ret_close_30m > 0.02) and (vs_close_30m > 0.18)

    super_distr = r9_distr and r11_tail_surge

    if super_distr:
        label, expected_spread = "super_distr", -4.20
    elif r9_distr:
        label, expected_spread = "distr", -1.42
    elif r11_tail_surge:
        label, expected_spread = "tail_surge", -1.40
    elif r9_bounce:
        label, expected_spread = "bounce", 0.85
    else:
        label, expected_spread = "neutral", 0.0

    return {
        "regime_label": label,
        "r9_distr": r9_distr,
        "r9_bounce": r9_bounce,
        "r11_tail_surge": r11_tail_surge,
        "super_distr": super_distr,
        "expected_spread_pp": expected_spread,
        "ret_20d": ret_20d,
        "tr_surge_60": tr_surge_60,
        "ret_close_30m": ret_close_30m,
        "vs_close_30m": vs_close_30m,
    }
