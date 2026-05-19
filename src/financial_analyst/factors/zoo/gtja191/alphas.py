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


def _a006(p: PanelData) -> pd.Series:
    """rank(sign(delta(((OPEN*0.85)+(HIGH*0.15)),4))) * -1"""
    base = p.open * 0.85 + p.high * 0.15
    return -1.0 * rank(sign(delta(base, 4)))


register(AlphaSpec(
    name="gtja006", family=FAMILY, paper=_PAPER,
    description="Negative rank of 4-day signed change in weighted open/high — fades upward bias",
    formula_text="-1 * rank(sign(delta((OPEN*0.85+HIGH*0.15),4)))",
    compute=_a006,
))


def _a008(p: PanelData) -> pd.Series:
    """rank(delta(((HIGH+LOW)/2*0.2)+(VWAP*0.8), 4)) * -1"""
    base = (p.high + p.low) / 2.0 * 0.2 + p.vwap * 0.8
    return -1.0 * rank(delta(base, 4))


register(AlphaSpec(
    name="gtja008", family=FAMILY, paper=_PAPER,
    description="Negative 4-day rank of midpoint+VWAP blend change — fades short-term price extension",
    formula_text="-1 * rank(delta((HIGH+LOW)/2*0.2 + VWAP*0.8, 4))",
    compute=_a008,
))


def _a010(p: PanelData) -> pd.Series:
    """rank(max(((returns < 0) ? stddev(returns,20) : close)^2, 5))"""
    r = p.returns
    base = stddev(r, 20).where(r < 0, p.close)
    return rank(ts_max(base * base, 5))


register(AlphaSpec(
    name="gtja010", family=FAMILY, paper=_PAPER,
    description="Recent peak of conditional vol²/close² — risk-spike detector",
    formula_text="rank(max(((returns<0) ? stddev(returns,20) : close)^2, 5))",
    compute=_a010,
))


def _a011(p: PanelData) -> pd.Series:
    """SUM(((CLOSE-LOW)-(HIGH-CLOSE))/(HIGH-LOW)*VOLUME, 6)"""
    spread = (p.high - p.low).replace(0, np.nan)
    moneyflow = ((p.close - p.low) - (p.high - p.close)) / spread
    return ts_sum(moneyflow * p.volume, 6)


register(AlphaSpec(
    name="gtja011", family=FAMILY, paper=_PAPER,
    description="6-day cumulative volume-weighted money-flow position — accumulation gauge",
    formula_text="SUM(((CLOSE-LOW)-(HIGH-CLOSE))/(HIGH-LOW)*VOLUME, 6)",
    compute=_a011,
))


def _a013(p: PanelData) -> pd.Series:
    """((HIGH*LOW)^0.5) - VWAP"""
    return np.sqrt(p.high * p.low) - p.vwap


register(AlphaSpec(
    name="gtja013", family=FAMILY, paper=_PAPER,
    description="Geometric mean of high+low minus VWAP — VWAP-relative range bias",
    formula_text="sqrt(HIGH*LOW) - VWAP",
    compute=_a013,
))


def _a017(p: PanelData) -> pd.Series:
    """rank((VWAP - max(VWAP, 15)))^delta(CLOSE,5)"""
    spread = rank(p.vwap - ts_max(p.vwap, 15))
    # spread is in [0,1]; raising to a varying power yields a soft monotone transform
    exponent = delta(p.close, 5)
    return np.power(spread, exponent.clip(lower=-4, upper=4))


register(AlphaSpec(
    name="gtja017", family=FAMILY, paper=_PAPER,
    description="VWAP exhaustion vs 15d max, exponentiated by 5d close change — non-linear momentum",
    formula_text="rank(VWAP - max(VWAP,15))^delta(CLOSE,5)",
    compute=_a017,
))


def _a019(p: PanelData) -> pd.Series:
    """(CLOSE<DELAY(CLOSE,5)) ? (CLOSE-DELAY(CLOSE,5))/DELAY(CLOSE,5)
       : ((CLOSE==DELAY(CLOSE,5)) ? 0 : (CLOSE-DELAY(CLOSE,5))/CLOSE)"""
    prev = delay(p.close, 5)
    diff = p.close - prev
    # left branch: diff/prev when close<prev; right branch: diff/close otherwise; 0 when equal
    out = diff / p.close.replace(0, np.nan)
    out = out.where(p.close > prev, diff / prev.replace(0, np.nan))
    out = out.where(p.close != prev, 0.0)
    return out


register(AlphaSpec(
    name="gtja019", family=FAMILY, paper=_PAPER,
    description="5-day asymmetric return — denominator switches on direction to capture skewed move sizes",
    formula_text="asymmetric 5-day return (see paper)",
    compute=_a019,
))


def _a020(p: PanelData) -> pd.Series:
    """(CLOSE - DELAY(CLOSE,6)) / DELAY(CLOSE,6) * 100"""
    prev = delay(p.close, 6)
    return (p.close - prev) / prev.replace(0, np.nan) * 100.0


register(AlphaSpec(
    name="gtja020", family=FAMILY, paper=_PAPER,
    description="6-day percentage return — classic short-horizon momentum",
    formula_text="(CLOSE - DELAY(CLOSE,6)) / DELAY(CLOSE,6) * 100",
    compute=_a020,
))


def _a025(p: PanelData) -> pd.Series:
    """((-1*rank(delta(CLOSE,7)*(1-rank(decay_linear(VOLUME/mean(VOLUME,20),9)))))
       * (1+rank(sum(returns,250))))"""
    adv20 = ts_mean(p.volume, 20)
    vol_ratio = p.volume / adv20.replace(0, np.nan)
    decayed_vol = decay_linear(vol_ratio, 9)
    inner = delta(p.close, 7) * (1.0 - rank(decayed_vol))
    long_term = 1.0 + rank(ts_sum(p.returns, 250))
    return (-1.0 * rank(inner)) * long_term


register(AlphaSpec(
    name="gtja025", family=FAMILY, paper=_PAPER,
    description="7d momentum × volume-decay rank × 250d return rank — multi-horizon composite",
    formula_text="(-1*rank(delta(CLOSE,7) * (1-rank(decay_linear(VOLUME/mean(VOLUME,20),9))))) * (1+rank(sum(returns,250)))",
    compute=_a025,
))


def _a028(p: PanelData) -> pd.Series:
    """3*SMA((CLOSE-MIN(LOW,9))/(MAX(HIGH,9)-MIN(LOW,9))*100, 3, 1)
       - 2*SMA(SMA((CLOSE-MIN(LOW,9))/(MAX(HIGH,9)-MIN(LOW,9))*100, 3, 1), 3, 1)"""
    rng = (ts_max(p.high, 9) - ts_min(p.low, 9)).replace(0, np.nan)
    raw = (p.close - ts_min(p.low, 9)) / rng * 100.0
    sma_3 = sma(raw, 3, 1)
    return 3.0 * sma_3 - 2.0 * sma(sma_3, 3, 1)


register(AlphaSpec(
    name="gtja028", family=FAMILY, paper=_PAPER,
    description="Stochastic Oscillator (KDJ-style) hand-rolled — 9d range, double-smoothed",
    formula_text="3*SMA(KDJ_raw, 3, 1) - 2*SMA(SMA(KDJ_raw, 3, 1), 3, 1)",
    compute=_a028,
))


def _a037(p: PanelData) -> pd.Series:
    """-1 * rank(((sum(OPEN, 5) * sum(returns, 5))
                  - delay((sum(OPEN, 5) * sum(returns, 5)), 10)))"""
    base = ts_sum(p.open, 5) * ts_sum(p.returns, 5)
    return -1.0 * rank(base - delay(base, 10))


register(AlphaSpec(
    name="gtja037", family=FAMILY, paper=_PAPER,
    description="Open-sum × return-sum 10d change — sister to alpha101#008 on A-share",
    formula_text="-1 * rank(sum(OPEN,5)*sum(returns,5) - delay(sum(OPEN,5)*sum(returns,5), 10))",
    compute=_a037,
))


def _a047(p: PanelData) -> pd.Series:
    """SMA((MAX(HIGH,6)-CLOSE)/(MAX(HIGH,6)-MIN(LOW,6))*100, 9, 1)"""
    rng = (ts_max(p.high, 6) - ts_min(p.low, 6)).replace(0, np.nan)
    raw = (ts_max(p.high, 6) - p.close) / rng * 100.0
    return sma(raw, 9, 1)


register(AlphaSpec(
    name="gtja047", family=FAMILY, paper=_PAPER,
    description="Williams %R style ceiling-pressure indicator over 6d, EWMA-smoothed",
    formula_text="SMA((MAX(HIGH,6)-CLOSE) / (MAX(HIGH,6)-MIN(LOW,6)) * 100, 9, 1)",
    compute=_a047,
))


def _a052(p: PanelData) -> pd.Series:
    """SUM(MAX(0,HIGH-DELAY((HIGH+LOW+CLOSE)/3,1)),26) /
       SUM(MAX(0,DELAY((HIGH+LOW+CLOSE)/3,1)-LOW),26) * 100"""
    typical = delay((p.high + p.low + p.close) / 3.0, 1)
    pos = (p.high - typical).clip(lower=0)
    neg = (typical - p.low).clip(lower=0)
    return ts_sum(pos, 26) / ts_sum(neg, 26).replace(0, np.nan) * 100.0


register(AlphaSpec(
    name="gtja052", family=FAMILY, paper=_PAPER,
    description="26d cumulative upper/lower wick ratio relative to typical price — accumulation/distribution",
    formula_text="SUM(MAX(0, HIGH-typical), 26) / SUM(MAX(0, typical-LOW), 26) * 100",
    compute=_a052,
))


def _a058(p: PanelData) -> pd.Series:
    """COUNT(CLOSE>DELAY(CLOSE,1),20) / 20 * 100"""
    up_days = (p.close > delay(p.close, 1)).astype(float)
    return ts_sum(up_days, 20) / 20.0 * 100.0


register(AlphaSpec(
    name="gtja058", family=FAMILY, paper=_PAPER,
    description="20d up-day percentage — longer-window sister to gtja053",
    formula_text="COUNT(CLOSE>DELAY(CLOSE,1),20) / 20 * 100",
    compute=_a058,
))


def _a068(p: PanelData) -> pd.Series:
    """SMA(((HIGH+LOW)/2-(DELAY(HIGH,1)+DELAY(LOW,1))/2)*(HIGH-LOW)/VOLUME, 15, 2)"""
    mid = (p.high + p.low) / 2.0
    prev_mid = (delay(p.high, 1) + delay(p.low, 1)) / 2.0
    raw = (mid - prev_mid) * (p.high - p.low) / p.volume.replace(0, np.nan)
    return sma(raw, n=15, m=2)


register(AlphaSpec(
    name="gtja068", family=FAMILY, paper=_PAPER,
    description="Long-window EWMA of midpoint-shift × range / volume — sister to gtja009 with n=15",
    formula_text="SMA(((HIGH+LOW)/2 - delay((HIGH+LOW)/2,1)) * (HIGH-LOW)/VOLUME, 15, 2)",
    compute=_a068,
))


def _a022(p: PanelData) -> pd.Series:
    """SMA(((CLOSE - MEAN(CLOSE,6)) / MEAN(CLOSE,6)
            - DELAY((CLOSE - MEAN(CLOSE,6)) / MEAN(CLOSE,6), 3)), 12, 1)"""
    mean6 = ts_mean(p.close, 6)
    rel = (p.close - mean6) / mean6.replace(0, np.nan)
    return sma(rel - delay(rel, 3), 12, 1)


register(AlphaSpec(
    name="gtja022", family=FAMILY, paper=_PAPER,
    description="EWMA of 3d change in close-vs-MA6 deviation — mean-reversion accelerator",
    formula_text="SMA(((CLOSE-MEAN(CLOSE,6))/MEAN(CLOSE,6) - DELAY(...,3)), 12, 1)",
    compute=_a022,
))


def _a024(p: PanelData) -> pd.Series:
    """SMA(CLOSE - DELAY(CLOSE,5), 5, 1)"""
    return sma(p.close - delay(p.close, 5), 5, 1)


register(AlphaSpec(
    name="gtja024", family=FAMILY, paper=_PAPER,
    description="EWMA of 5d close change — smoothed momentum",
    formula_text="SMA(CLOSE - DELAY(CLOSE,5), 5, 1)",
    compute=_a024,
))


def _a029(p: PanelData) -> pd.Series:
    """(CLOSE - DELAY(CLOSE,6)) / DELAY(CLOSE,6) * VOLUME"""
    prev = delay(p.close, 6)
    return (p.close - prev) / prev.replace(0, np.nan) * p.volume


register(AlphaSpec(
    name="gtja029", family=FAMILY, paper=_PAPER,
    description="6d return × volume — flow-weighted medium-horizon momentum",
    formula_text="(CLOSE - DELAY(CLOSE,6)) / DELAY(CLOSE,6) * VOLUME",
    compute=_a029,
))


def _a031(p: PanelData) -> pd.Series:
    """(CLOSE - MEAN(CLOSE,12)) / MEAN(CLOSE,12) * 100"""
    mean12 = ts_mean(p.close, 12)
    return (p.close - mean12) / mean12.replace(0, np.nan) * 100.0


register(AlphaSpec(
    name="gtja031", family=FAMILY, paper=_PAPER,
    description="12d close-vs-MA deviation in pp — overbought/oversold indicator",
    formula_text="(CLOSE - MEAN(CLOSE,12)) / MEAN(CLOSE,12) * 100",
    compute=_a031,
))


def _a034(p: PanelData) -> pd.Series:
    """MEAN(CLOSE,12) / CLOSE"""
    return ts_mean(p.close, 12) / p.close.replace(0, np.nan)


register(AlphaSpec(
    name="gtja034", family=FAMILY, paper=_PAPER,
    description="12d MA / close ratio — simple mean-reversion gauge (>1 = below trend)",
    formula_text="MEAN(CLOSE,12) / CLOSE",
    compute=_a034,
))


def _a038(p: PanelData) -> pd.Series:
    """((SUM(HIGH,20)/20) < HIGH) ? (-1 * DELTA(HIGH,2)) : 0"""
    return ((-1.0 * delta(p.high, 2))
            .where(ts_sum(p.high, 20) / 20.0 < p.high, 0.0))


register(AlphaSpec(
    name="gtja038", family=FAMILY, paper=_PAPER,
    description="Fade 2d high change when today's high pierces 20d high SMA — sister to alpha023",
    formula_text="((SUM(HIGH,20)/20) < HIGH) ? -1*DELTA(HIGH,2) : 0",
    compute=_a038,
))


def _a040(p: PanelData) -> pd.Series:
    """SUM((CLOSE>DELAY(CLOSE,1) ? VOLUME : 0), 26)
       / SUM((CLOSE<=DELAY(CLOSE,1) ? VOLUME : 0), 26) * 100"""
    up_vol = p.volume.where(p.close > delay(p.close, 1), 0.0)
    dn_vol = p.volume.where(p.close <= delay(p.close, 1), 0.0)
    return ts_sum(up_vol, 26) / ts_sum(dn_vol, 26).replace(0, np.nan) * 100.0


register(AlphaSpec(
    name="gtja040", family=FAMILY, paper=_PAPER,
    description="26d up-volume vs down-volume ratio — money-flow direction",
    formula_text="SUM(up_volume,26) / SUM(down_volume,26) * 100",
    compute=_a040,
))


def _a046(p: PanelData) -> pd.Series:
    """(MEAN(CLOSE,3) + MEAN(CLOSE,6) + MEAN(CLOSE,12) + MEAN(CLOSE,24)) / (4 * CLOSE)"""
    return (ts_mean(p.close, 3) + ts_mean(p.close, 6) + ts_mean(p.close, 12) + ts_mean(p.close, 24)) / (4.0 * p.close.replace(0, np.nan))


register(AlphaSpec(
    name="gtja046", family=FAMILY, paper=_PAPER,
    description="Average of 4 MAs / close — multi-timeframe mean-reversion composite",
    formula_text="(MA3 + MA6 + MA12 + MA24) / (4 * CLOSE)",
    compute=_a046,
))


def _a054(p: PanelData) -> pd.Series:
    """-1 * RANK(STDDEV(ABS(CLOSE-OPEN), 10) + (CLOSE-OPEN) + CORRELATION(CLOSE, OPEN, 10))"""
    body = (p.close - p.open).abs()
    return -1.0 * rank(stddev(body, 10) + (p.close - p.open) + correlation(p.close, p.open, 10))


register(AlphaSpec(
    name="gtja054", family=FAMILY, paper=_PAPER,
    description="Penalise body-vol + intraday return + open-close cointegration — sister to alpha018",
    formula_text="-1 * RANK(STDDEV(ABS(CLOSE-OPEN),10) + (CLOSE-OPEN) + CORR(CLOSE,OPEN,10))",
    compute=_a054,
))


def _a057(p: PanelData) -> pd.Series:
    """SMA((CLOSE - TSMIN(LOW,9)) / (TSMAX(HIGH,9) - TSMIN(LOW,9)) * 100, 3, 1)"""
    rng = (ts_max(p.high, 9) - ts_min(p.low, 9)).replace(0, np.nan)
    raw = (p.close - ts_min(p.low, 9)) / rng * 100.0
    return sma(raw, 3, 1)


register(AlphaSpec(
    name="gtja057", family=FAMILY, paper=_PAPER,
    description="9d stochastic K%, 3d EWMA-smoothed — standard %K indicator",
    formula_text="SMA((CLOSE - TSMIN(LOW,9)) / (TSMAX(HIGH,9) - TSMIN(LOW,9)) * 100, 3, 1)",
    compute=_a057,
))


def _a065(p: PanelData) -> pd.Series:
    """MEAN(CLOSE,6) / CLOSE"""
    return ts_mean(p.close, 6) / p.close.replace(0, np.nan)


register(AlphaSpec(
    name="gtja065", family=FAMILY, paper=_PAPER,
    description="6d MA / close ratio — shorter-horizon sister to gtja034",
    formula_text="MEAN(CLOSE,6) / CLOSE",
    compute=_a065,
))
