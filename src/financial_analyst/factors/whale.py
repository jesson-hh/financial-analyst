from __future__ import annotations

from typing import Dict

import numpy as np
import pandas as pd


def _obv(close: pd.Series, vol: pd.Series) -> pd.Series:
    return (np.sign(close.diff()).fillna(0) * vol).cumsum()


def _mfi(df: pd.DataFrame, period: int = 14) -> float:
    typ = (df["high"] + df["low"] + df["close"]) / 3
    mf = typ * df["vol"]
    direction = np.sign(typ.diff()).fillna(0)
    pos = mf[direction > 0].rolling(period).sum().iloc[-1]
    neg = mf[direction < 0].rolling(period).sum().iloc[-1]
    if neg == 0 or np.isnan(neg):
        return 100.0
    mr = pos / neg
    return float(100 - 100 / (1 + mr))


def _vr(df: pd.DataFrame, period: int = 26) -> float:
    direction = np.sign(df["close"].diff()).fillna(0)
    up = df["vol"][direction > 0].iloc[-period:].sum()
    down = df["vol"][direction < 0].iloc[-period:].sum()
    if down == 0:
        return 999.0
    return float(up / down)


def _shadow_ratio(df: pd.DataFrame, lookback: int = 20) -> float:
    recent = df.iloc[-lookback:]
    lower = (recent["close"].combine(recent["open"], min) - recent["low"]).clip(lower=0)
    body = (recent["close"] - recent["open"]).abs()
    return float((lower / (body + 1e-9)).mean())


def compute_whale_signals(df: pd.DataFrame) -> Dict[str, str]:
    obv = _obv(df["close"], df["vol"])
    obv_slope = np.polyfit(range(20), obv.iloc[-20:].values, 1)[0] if len(obv) >= 20 else 0
    obv_trend = "up" if obv_slope > 0 else ("down" if obv_slope < 0 else "flat")

    vr = _vr(df)
    vr_judge = "strong" if vr > 2.0 else ("weak" if vr < 0.5 else "neutral")

    mfi = _mfi(df)
    mfi_judge = "overbought" if mfi > 80 else ("oversold" if mfi < 20 else "neutral")

    shadow = _shadow_ratio(df)
    shadow_judge = "support" if shadow > 1.5 else "neutral"

    chip = "concentrated" if vr > 1.5 and obv_slope > 0 else "dispersed"

    whale_bullish = (obv_trend == "up") + (vr_judge == "strong") + (shadow_judge == "support")
    whale_judge = (
        "accumulating"
        if whale_bullish >= 2
        else ("distributing" if vr_judge == "weak" and obv_trend == "down" else "neutral")
    )

    return {
        "obv_trend": obv_trend,
        "vr_judge": vr_judge,
        "mfi_judge": mfi_judge,
        "shadow_judge": shadow_judge,
        "chip_judge": chip,
        "whale_judge": whale_judge,
        "vr_value": float(vr),
        "mfi_value": float(mfi),
        "shadow_value": float(shadow),
    }
