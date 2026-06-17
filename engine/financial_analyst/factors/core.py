from __future__ import annotations
from typing import Dict
import numpy as np
import pandas as pd

FACTOR_NAMES = [
    "rev_5", "rev_10", "rev_20", "rev_60",
    "mom_5", "mom_10", "mom_20", "mom_60",
    "vol_5", "vol_10", "vol_20", "vol_60",
    "turnover_5", "turnover_20", "turnover_60",
    "amount_log_5", "amount_log_20",
    "high_low_5", "high_low_20",
    "close_to_high_20", "close_to_low_20",
    "ma_diff_5", "ma_diff_20", "ma_diff_60",
    "ema_diff_12_26",
    "rsi_14",
    "macd_bar",
    "bb_pct_20",
    "skew_20", "kurt_20",
    "updown_ratio_20",
    "updown_vol_20",
    "obv_slope_20",
    "max_drawdown_20",
]


def _safe_pct_change(s: pd.Series, periods: int) -> float:
    if len(s) <= periods or s.iloc[-1 - periods] == 0:
        return float("nan")
    return float(s.iloc[-1] / s.iloc[-1 - periods] - 1)


def _rsi(close: pd.Series, period: int = 14) -> float:
    diff = close.diff().dropna()
    if len(diff) < period:
        return float("nan")
    gains = diff.clip(lower=0).rolling(period).mean().iloc[-1]
    losses = (-diff.clip(upper=0)).rolling(period).mean().iloc[-1]
    if losses == 0:
        return 100.0
    rs = gains / losses
    return float(100 - 100 / (1 + rs))


def _macd_bar(close: pd.Series) -> float:
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    dif = ema12 - ema26
    dea = dif.ewm(span=9, adjust=False).mean()
    return float((dif - dea).iloc[-1])


def _bb_pct(close: pd.Series, period: int = 20) -> float:
    ma = close.rolling(period).mean().iloc[-1]
    sd = close.rolling(period).std().iloc[-1]
    if sd == 0 or np.isnan(sd):
        return float("nan")
    return float((close.iloc[-1] - (ma - 2 * sd)) / (4 * sd))


def _obv_slope(close: pd.Series, vol: pd.Series, period: int = 20) -> float:
    direction = np.sign(close.diff()).fillna(0)
    obv = (direction * vol).cumsum()
    seg = obv.iloc[-period:]
    if len(seg) < period:
        return float("nan")
    return float(np.polyfit(range(period), seg.values, 1)[0])


def compute_factors(df: pd.DataFrame) -> Dict[str, float]:
    close = df["close"]
    vol = df["vol"]
    amount = df["amount"]
    high = df["high"]
    low = df["low"]
    ret = close.pct_change()
    factors: Dict[str, float] = {}

    for w in [5, 10, 20, 60]:
        factors[f"rev_{w}"] = -_safe_pct_change(close, w)
        factors[f"mom_{w}"] = _safe_pct_change(close, w)
        factors[f"vol_{w}"] = float(ret.rolling(w).std().iloc[-1]) if len(ret) >= w else float("nan")

    for w in [5, 20, 60]:
        avg_vol = vol.rolling(w).mean().iloc[-1] if len(vol) >= w else float("nan")
        factors[f"turnover_{w}"] = float(vol.iloc[-1] / avg_vol) if avg_vol else float("nan")

    factors["amount_log_5"] = float(np.log1p(amount.iloc[-5:].mean()))
    factors["amount_log_20"] = float(np.log1p(amount.iloc[-20:].mean()))

    for w in [5, 20]:
        rng = (high.iloc[-w:].max() - low.iloc[-w:].min())
        factors[f"high_low_{w}"] = float(rng / close.iloc[-1]) if close.iloc[-1] else float("nan")

    h20 = high.iloc[-20:].max()
    l20 = low.iloc[-20:].min()
    factors["close_to_high_20"] = float(close.iloc[-1] / h20 - 1) if h20 else float("nan")
    factors["close_to_low_20"] = float(close.iloc[-1] / l20 - 1) if l20 else float("nan")

    for w in [5, 20, 60]:
        ma = close.rolling(w).mean().iloc[-1]
        factors[f"ma_diff_{w}"] = float(close.iloc[-1] / ma - 1) if ma else float("nan")

    ema12 = close.ewm(span=12, adjust=False).mean().iloc[-1]
    ema26 = close.ewm(span=26, adjust=False).mean().iloc[-1]
    factors["ema_diff_12_26"] = float(ema12 / ema26 - 1) if ema26 else float("nan")

    factors["rsi_14"] = _rsi(close)
    factors["macd_bar"] = _macd_bar(close)
    factors["bb_pct_20"] = _bb_pct(close)

    factors["skew_20"] = float(ret.rolling(20).skew().iloc[-1])
    factors["kurt_20"] = float(ret.rolling(20).kurt().iloc[-1])

    up_days = (ret.iloc[-20:] > 0).sum()
    factors["updown_ratio_20"] = float(up_days / 20)
    up_vol = vol.iloc[-20:][ret.iloc[-20:] > 0].sum()
    down_vol = vol.iloc[-20:][ret.iloc[-20:] < 0].sum()
    factors["updown_vol_20"] = float(up_vol / (down_vol + 1))

    factors["obv_slope_20"] = _obv_slope(close, vol)

    peak = close.iloc[-20:].cummax()
    dd = (close.iloc[-20:] - peak) / peak
    factors["max_drawdown_20"] = float(dd.min())

    return factors
