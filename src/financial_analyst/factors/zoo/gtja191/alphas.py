"""Ports of GTJA 191 — most-cited Chinese A-share alphas.

Source: Guotai Junan Securities Quant Research, "191 短周期价量阿尔法
因子" (2017). The handbook is widely circulated; this module sticks to
the original notation so each alpha can be cross-checked.
"""
from __future__ import annotations
import numpy as np
import pandas as pd

from financial_analyst.factors.zoo.panel import PanelData
from financial_analyst.factors.zoo.registry import AlphaSpec, register
from financial_analyst.factors.zoo.operators import (
    rank, ts_argmax, ts_argmin, ts_max, ts_min, ts_sum, ts_rank, ts_mean,
    delta, delay, correlation, covariance, decay_linear, stddev,
    signedpower, scale, log, sign, abs_, sma,
)

FAMILY = "gtja191"
_PAPER = "Guotai Junan 191 Alphas (国泰君安, 2017)"


def _a001(p: PanelData) -> pd.Series:
    """-1 * correlation(rank(delta(log(VOLUME),1)), rank((CLOSE-OPEN)/OPEN), 6)"""
    a = rank(delta(log(p.volume), 1))
    b = rank((p.close - p.open) / p.open)
    return -1.0 * correlation(a, b, 6)


register(AlphaSpec(
    name="gtja001", family=FAMILY, paper=_PAPER,
    description="Negative rank-corr between 1-day volume change and intraday return — short-horizon reversal",
    formula_text="-1*correlation(rank(delta(log(VOLUME),1)), rank((CLOSE-OPEN)/OPEN), 6)",
    compute=_a001,
))


def _a002(p: PanelData) -> pd.Series:
    """-1 * delta((((CLOSE-LOW)-(HIGH-CLOSE))/(HIGH-LOW)), 1)"""
    spread = (p.high - p.low).replace(0, np.nan)
    moneyflow_pos = ((p.close - p.low) - (p.high - p.close)) / spread
    return -1.0 * delta(moneyflow_pos, 1)


register(AlphaSpec(
    name="gtja002", family=FAMILY, paper=_PAPER,
    description="Reversal on intraday money-flow position change — classic short-term mean-reversion",
    formula_text="-1*delta(((CLOSE-LOW)-(HIGH-CLOSE))/(HIGH-LOW), 1)",
    compute=_a002,
))


def _a003(p: PanelData) -> pd.Series:
    """SUM(CLOSE>DELAY(CLOSE,1) ? CLOSE - MIN(LOW, DELAY(CLOSE,1)) : (CLOSE<DELAY(CLOSE,1) ? CLOSE - MAX(HIGH, DELAY(CLOSE,1)) : 0), 6)"""
    pc = delay(p.close, 1)
    up = p.close - np.minimum(p.low, pc)
    dn = p.close - np.maximum(p.high, pc)
    val = pd.Series(0.0, index=p.close.index)
    val = val.where(p.close == pc, dn)
    val = val.where(p.close <= pc, up)
    return ts_sum(val, 6)


register(AlphaSpec(
    name="gtja003", family=FAMILY, paper=_PAPER,
    description="6-day cumulative directional volume-weighted true range — momentum confirmation",
    formula_text="SUM(direction-aware true-range chunk, 6)",
    compute=_a003,
))


def _a004(p: PanelData) -> pd.Series:
    """Long when (sum_close_8/8 + std_8) < sum_close_2/2 AND vol_avg_20 / vol > 1 else short."""
    cond1 = (ts_sum(p.close, 8) / 8.0 + stddev(p.close, 8)) < (ts_sum(p.close, 2) / 2.0)
    cond2 = ts_sum(p.close, 8) / 8.0 + stddev(p.close, 8) > ts_sum(p.close, 2) / 2.0
    cond3 = (ts_mean(p.volume, 20) / p.volume.replace(0, np.nan)) >= 1.0
    long_ = pd.Series(-1.0, index=p.close.index).where(~cond1, 1.0)
    short_ = pd.Series(1.0, index=p.close.index).where(~cond2, -1.0)
    # Default path when neither extreme: 1 if volume ratio favourable else 0
    out = pd.Series(np.nan, index=p.close.index)
    out = out.where(~cond1, long_)
    out = out.where(~(cond2 & ~cond1), short_)
    out = out.fillna(0.0)
    out = out.where(~cond3, 1.0)
    return out


register(AlphaSpec(
    name="gtja004", family=FAMILY, paper=_PAPER,
    description="Step-function combining short MA pivot vs longer MA + vol-ratio gate",
    formula_text="step-function over 8d MA pivot + 20d vol ratio (see paper)",
    compute=_a004,
))


def _a005(p: PanelData) -> pd.Series:
    """-1 * Ts_Max(correlation(Ts_Rank(VOLUME,5), Ts_Rank(HIGH,5), 5), 3)"""
    return -1.0 * ts_max(correlation(ts_rank(p.volume, 5), ts_rank(p.high, 5), 5), 3)


register(AlphaSpec(
    name="gtja005", family=FAMILY, paper=_PAPER,
    description="Negative recent peak of vol-high rank correlation — fades crowded breakouts",
    formula_text="-1*Ts_Max(correlation(Ts_Rank(VOLUME,5), Ts_Rank(HIGH,5), 5), 3)",
    compute=_a005,
))


def _a007(p: PanelData) -> pd.Series:
    """((rank(max(VWAP-CLOSE,3)) + rank(min(VWAP-CLOSE,3))) * rank(delta(VOLUME,3)))"""
    return (rank(ts_max(p.vwap - p.close, 3))
            + rank(ts_min(p.vwap - p.close, 3))) * rank(delta(p.volume, 3))


register(AlphaSpec(
    name="gtja007", family=FAMILY, paper=_PAPER,
    description="VWAP-close extremes weighted by recent volume change — volume confirmation",
    formula_text="(rank(max(VWAP-CLOSE,3)) + rank(min(VWAP-CLOSE,3))) * rank(delta(VOLUME,3))",
    compute=_a007,
))


def _a009(p: PanelData) -> pd.Series:
    """SMA(((HIGH+LOW)/2 - (delay(HIGH,1)+delay(LOW,1))/2) * (HIGH-LOW)/VOLUME, 7, 2)"""
    mid = (p.high + p.low) / 2.0
    prev_mid = (delay(p.high, 1) + delay(p.low, 1)) / 2.0
    raw = (mid - prev_mid) * (p.high - p.low) / p.volume.replace(0, np.nan)
    return sma(raw, n=7, m=2)


register(AlphaSpec(
    name="gtja009", family=FAMILY, paper=_PAPER,
    description="EWMA of midpoint shift × range / volume — large-tick imbalance",
    formula_text="SMA(((HIGH+LOW)/2 - delay((HIGH+LOW)/2,1)) * (HIGH-LOW)/VOLUME, 7, 2)",
    compute=_a009,
))


def _a012(p: PanelData) -> pd.Series:
    """rank(OPEN - SUM(VWAP,10)/10) * (-1*rank(abs(CLOSE-VWAP)))"""
    return rank(p.open - ts_sum(p.vwap, 10) / 10.0) * (-1.0 * rank((p.close - p.vwap).abs()))


register(AlphaSpec(
    name="gtja012", family=FAMILY, paper=_PAPER,
    description="Open vs 10-day VWAP rank × negative close-VWAP gap — overnight bias × close-day mean-revert",
    formula_text="rank(OPEN - SUM(VWAP,10)/10) * (-1*rank(abs(CLOSE-VWAP)))",
    compute=_a012,
))


def _a014(p: PanelData) -> pd.Series:
    """CLOSE - delay(CLOSE,5)"""
    return p.close - delay(p.close, 5)


register(AlphaSpec(
    name="gtja014", family=FAMILY, paper=_PAPER,
    description="5-day close change — pure momentum baseline (signed, no rank)",
    formula_text="CLOSE-DELAY(CLOSE,5)",
    compute=_a014,
))


def _a018(p: PanelData) -> pd.Series:
    """CLOSE / delay(CLOSE,5)"""
    return p.close / delay(p.close, 5).replace(0, np.nan)


register(AlphaSpec(
    name="gtja018", family=FAMILY, paper=_PAPER,
    description="5-day price ratio — alt momentum form, log-symmetric around 1",
    formula_text="CLOSE/DELAY(CLOSE,5)",
    compute=_a018,
))


def _a042(p: PanelData) -> pd.Series:
    """(-1 * rank(stddev(HIGH,10))) * correlation(HIGH,VOLUME,10)"""
    return (-1.0 * rank(stddev(p.high, 10))) * correlation(p.high, p.volume, 10)


register(AlphaSpec(
    name="gtja042", family=FAMILY, paper=_PAPER,
    description="Penalise high-vol-of-high names where high price correlates with volume",
    formula_text="(-1*rank(stddev(HIGH,10))) * correlation(HIGH,VOLUME,10)",
    compute=_a042,
))


def _a053(p: PanelData) -> pd.Series:
    """COUNT(CLOSE > DELAY(CLOSE,1), 12) / 12 * 100"""
    up_days = (p.close > delay(p.close, 1)).astype(float)
    return ts_sum(up_days, 12) / 12.0 * 100.0


register(AlphaSpec(
    name="gtja053", family=FAMILY, paper=_PAPER,
    description="12-day up-day count percentage — short-term momentum bias",
    formula_text="COUNT(CLOSE>DELAY(CLOSE,1),12)/12*100",
    compute=_a053,
))
