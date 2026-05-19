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
    regbeta, regresi, rsqr, sequence, wma,
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


def _a021(p: PanelData) -> pd.Series:
    """REGBETA(MEAN(CLOSE,6), SEQUENCE(6))"""
    return regbeta(ts_mean(p.close, 6), sequence(p.close, 6), 6)


register(AlphaSpec(
    name="gtja021", family=FAMILY, paper=_PAPER,
    description="Slope of 6d MA vs time index — trend strength of the moving average itself",
    formula_text="REGBETA(MEAN(CLOSE,6), SEQUENCE(6))",
    compute=_a021,
))


def _a027(p: PanelData) -> pd.Series:
    """WMA((CLOSE-DELAY(CLOSE,3))/DELAY(CLOSE,3)*100
            + (CLOSE-DELAY(CLOSE,6))/DELAY(CLOSE,6)*100, 12)"""
    r3 = (p.close - delay(p.close, 3)) / delay(p.close, 3).replace(0, np.nan) * 100.0
    r6 = (p.close - delay(p.close, 6)) / delay(p.close, 6).replace(0, np.nan) * 100.0
    return wma(r3 + r6, 12)


register(AlphaSpec(
    name="gtja027", family=FAMILY, paper=_PAPER,
    description="WMA of (3d + 6d % returns) — smoothed multi-horizon momentum",
    formula_text="WMA((CLOSE-DELAY(CLOSE,3))/DELAY(CLOSE,3)*100 + (CLOSE-DELAY(CLOSE,6))/DELAY(CLOSE,6)*100, 12)",
    compute=_a027,
))


def _a076(p: PanelData) -> pd.Series:
    """STDDEV(ABS(CLOSE/DELAY(CLOSE,1)-1)/VOLUME, 20)
       / MEAN(ABS(CLOSE/DELAY(CLOSE,1)-1)/VOLUME, 20)"""
    raw = (p.close / delay(p.close, 1).replace(0, np.nan) - 1.0).abs() / p.volume.replace(0, np.nan)
    return stddev(raw, 20) / ts_mean(raw, 20).replace(0, np.nan)


register(AlphaSpec(
    name="gtja076", family=FAMILY, paper=_PAPER,
    description="Coefficient of variation of return-per-volume — efficiency volatility",
    formula_text="STDDEV(|CLOSE/DELAY(CLOSE,1)-1|/VOLUME, 20) / MEAN(...same..., 20)",
    compute=_a076,
))


def _a095(p: PanelData) -> pd.Series:
    """STD(AMOUNT, 20)"""
    return stddev(p.amount, 20)


register(AlphaSpec(
    name="gtja095", family=FAMILY, paper=_PAPER,
    description="20d std of dollar volume — turnover volatility",
    formula_text="STD(AMOUNT, 20)",
    compute=_a095,
))


def _a128(p: PanelData) -> pd.Series:
    """100 - 100 / (1 + sum(typical*vol where typical>prev_typical, 14)
                         / sum(typical*vol where typical<prev_typical, 14))"""
    typical = (p.high + p.low + p.close) / 3.0
    prev_typical = delay(typical, 1)
    up_mass = (typical * p.volume).where(typical > prev_typical, 0.0)
    dn_mass = (typical * p.volume).where(typical < prev_typical, 0.0)
    ratio = ts_sum(up_mass, 14) / ts_sum(dn_mass, 14).replace(0, np.nan)
    return 100.0 - 100.0 / (1.0 + ratio)


register(AlphaSpec(
    name="gtja128", family=FAMILY, paper=_PAPER,
    description="Money-Flow Index style: 14d volume-weighted up/down typical-price ratio",
    formula_text="100 - 100/(1 + SUM(typical*vol_up,14)/SUM(typical*vol_down,14))",
    compute=_a128,
))


def _a160(p: PanelData) -> pd.Series:
    """SMA((CLOSE<=DELAY(CLOSE,1) ? STD(CLOSE,20) : 0), 20, 1)"""
    raw = stddev(p.close, 20).where(p.close <= delay(p.close, 1), 0.0)
    return sma(raw, 20, 1)


register(AlphaSpec(
    name="gtja160", family=FAMILY, paper=_PAPER,
    description="EWMA of down-day volatility — downside-only risk gauge",
    formula_text="SMA((CLOSE<=DELAY(CLOSE,1) ? STD(CLOSE,20) : 0), 20, 1)",
    compute=_a160,
))


# ----- v1.3.5 batch: +50 ports ---------------------------------------------

def _a015(p: PanelData) -> pd.Series:
    """OPEN / DELAY(CLOSE, 1) - 1"""
    return p.open / delay(p.close, 1).replace(0, np.nan) - 1.0


register(AlphaSpec(
    name="gtja015", family=FAMILY, paper=_PAPER,
    description="Overnight gap return — open vs prior close",
    formula_text="OPEN/DELAY(CLOSE,1) - 1",
    compute=_a015,
))


def _a016(p: PanelData) -> pd.Series:
    """-1 * TSMAX(RANK(CORR(RANK(VOLUME), RANK(VWAP), 5)), 5)"""
    return -1.0 * ts_max(rank(correlation(rank(p.volume), rank(p.vwap), 5)), 5)


register(AlphaSpec(
    name="gtja016", family=FAMILY, paper=_PAPER,
    description="Recent peak of rank-volume × rank-vwap correlation rank (negated)",
    formula_text="-TSMAX(RANK(CORR(RANK(VOLUME),RANK(VWAP),5)),5)",
    compute=_a016,
))


def _a023(p: PanelData) -> pd.Series:
    """SMA((CLOSE>DELAY(CLOSE,1) ? STD(CLOSE,20) : 0), 20, 1) /
       (SMA((CLOSE>DELAY(CLOSE,1) ? STD(CLOSE,20) : 0), 20, 1)
        + SMA((CLOSE<=DELAY(CLOSE,1) ? STD(CLOSE,20) : 0), 20, 1)) * 100"""
    std = stddev(p.close, 20)
    up = std.where(p.close > delay(p.close, 1), 0.0)
    dn = std.where(p.close <= delay(p.close, 1), 0.0)
    up_s = sma(up, 20, 1)
    dn_s = sma(dn, 20, 1)
    return up_s / (up_s + dn_s).replace(0, np.nan) * 100.0


register(AlphaSpec(
    name="gtja023", family=FAMILY, paper=_PAPER,
    description="Up-day stddev share — up-vol / (up-vol+down-vol), in percent",
    formula_text="SMA(up_std,20,1) / (SMA(up_std,20,1)+SMA(down_std,20,1)) * 100",
    compute=_a023,
))


def _a026(p: PanelData) -> pd.Series:
    """(MEAN(CLOSE,7) - CLOSE) + CORR(VWAP, DELAY(CLOSE,5), 230)"""
    return (ts_mean(p.close, 7) - p.close) + correlation(p.vwap, delay(p.close, 5), 230)


register(AlphaSpec(
    name="gtja026", family=FAMILY, paper=_PAPER,
    description="7d MA-close gap + 230d VWAP-delayed-close correlation — sister to alpha032",
    formula_text="(MEAN(CLOSE,7)-CLOSE) + CORR(VWAP, DELAY(CLOSE,5), 230)",
    compute=_a026,
))


def _a030(p: PanelData) -> pd.Series:
    """WMA((CLOSE-DELAY(CLOSE,1))/DELAY(CLOSE,1), 20)"""
    r1 = (p.close - delay(p.close, 1)) / delay(p.close, 1).replace(0, np.nan)
    return wma(r1, 20)


register(AlphaSpec(
    name="gtja030", family=FAMILY, paper=_PAPER,
    description="20d WMA of 1d returns — recency-weighted momentum",
    formula_text="WMA((CLOSE-DELAY(CLOSE,1))/DELAY(CLOSE,1), 20)",
    compute=_a030,
))


def _a032(p: PanelData) -> pd.Series:
    """-1 * SUM(RANK(CORR(RANK(HIGH), RANK(VOLUME), 3)), 3)"""
    return -1.0 * ts_sum(rank(correlation(rank(p.high), rank(p.volume), 3)), 3)


register(AlphaSpec(
    name="gtja032", family=FAMILY, paper=_PAPER,
    description="Negative 3d sum of rank-high × rank-volume corr rank (sister to alpha015)",
    formula_text="-SUM(RANK(CORR(RANK(HIGH),RANK(VOLUME),3)),3)",
    compute=_a032,
))


def _a033(p: PanelData) -> pd.Series:
    """((-1 * TSMIN(LOW,5)) + DELAY(TSMIN(LOW,5),5)) *
       RANK((SUM(returns,240) - SUM(returns,20))/220) *
       TSRANK(VOLUME, 5)"""
    tmin = ts_min(p.low, 5)
    long_avg = (ts_sum(p.returns, 240) - ts_sum(p.returns, 20)) / 220.0
    return ((-tmin + delay(tmin, 5)) * rank(long_avg)) * ts_rank(p.volume, 5)


register(AlphaSpec(
    name="gtja033", family=FAMILY, paper=_PAPER,
    description="Floor-shift × long-term return rank × short-volume rank — multi-horizon (sister to alpha052)",
    formula_text="((-TSMIN(LOW,5)+DELAY(TSMIN(LOW,5),5))*RANK((SUM(returns,240)-SUM(returns,20))/220))*TSRANK(VOLUME,5)",
    compute=_a033,
))


def _a035(p: PanelData) -> pd.Series:
    """min(RANK(DECAYLINEAR(DELTA(OPEN,1), 15)),
           RANK(DECAYLINEAR(CORR(VOLUME, (OPEN*0.65 + OPEN*0.35), 17), 7))) * -1"""
    t1 = rank(decay_linear(delta(p.open, 1), 15))
    blend = p.open * 1.0
    t2 = rank(decay_linear(correlation(p.volume, blend, 17), 7))
    return -1.0 * np.minimum(t1, t2)


register(AlphaSpec(
    name="gtja035", family=FAMILY, paper=_PAPER,
    description="Negative min of two decayed ranks — open momentum vs volume-open corr",
    formula_text="-min(RANK(DECAYLINEAR(DELTA(OPEN,1),15)), RANK(DECAYLINEAR(CORR(VOLUME,OPEN,17),7)))",
    compute=_a035,
))


def _a036(p: PanelData) -> pd.Series:
    """RANK(SUM(CORR(RANK(VOLUME), RANK(VWAP), 6), 2))"""
    return rank(ts_sum(correlation(rank(p.volume), rank(p.vwap), 6), 2))


register(AlphaSpec(
    name="gtja036", family=FAMILY, paper=_PAPER,
    description="2d-summed rank-volume × rank-vwap correlation, ranked",
    formula_text="RANK(SUM(CORR(RANK(VOLUME),RANK(VWAP),6),2))",
    compute=_a036,
))


def _a039(p: PanelData) -> pd.Series:
    """(RANK(DECAYLINEAR(DELTA(CLOSE,2), 8))
       - RANK(DECAYLINEAR(CORR((VWAP*0.3+OPEN*0.7), SUM(MEAN(VOLUME,180),37), 14), 12))) * -1"""
    adv180 = ts_mean(p.volume, 180)
    blend = p.vwap * 0.3 + p.open * 0.7
    t1 = rank(decay_linear(delta(p.close, 2), 8))
    t2 = rank(decay_linear(correlation(blend, ts_sum(adv180, 37), 14), 12))
    return -1.0 * (t1 - t2)


register(AlphaSpec(
    name="gtja039", family=FAMILY, paper=_PAPER,
    description="Negative gap between 2d close-delta decay rank and blend-ADV180 correlation decay rank",
    formula_text="-(RANK(DECAYLINEAR(DELTA(CLOSE,2),8)) - RANK(DECAYLINEAR(CORR(blend, SUM(MEAN(VOLUME,180),37), 14), 12)))",
    compute=_a039,
))


def _a041(p: PanelData) -> pd.Series:
    """RANK(MAX(DELTA(VWAP, 3), 5)) * -1"""
    return -1.0 * rank(ts_max(delta(p.vwap, 3), 5))


register(AlphaSpec(
    name="gtja041", family=FAMILY, paper=_PAPER,
    description="Negative rank of 5d max of 3d VWAP change — fade peaks of momentum",
    formula_text="-RANK(MAX(DELTA(VWAP,3),5))",
    compute=_a041,
))


def _a043(p: PanelData) -> pd.Series:
    """SUM((CLOSE > DELAY(CLOSE,1) ? VOLUME : (CLOSE < DELAY(CLOSE,1) ? -VOLUME : 0)), 6)"""
    prev = delay(p.close, 1)
    signed = p.volume.where(p.close > prev, -p.volume.where(p.close < prev, 0.0))
    return ts_sum(signed, 6)


register(AlphaSpec(
    name="gtja043", family=FAMILY, paper=_PAPER,
    description="6d cumulative signed volume — directional volume flow (sister to OBV)",
    formula_text="SUM(signed_volume, 6)",
    compute=_a043,
))


def _a044(p: PanelData) -> pd.Series:
    """TSRANK(DECAYLINEAR(CORR(LOW, MEAN(VOLUME,10), 7), 6), 4)
       + TSRANK(DECAYLINEAR(DELTA(VWAP, 3), 10), 15)"""
    adv10 = ts_mean(p.volume, 10)
    t1 = ts_rank(decay_linear(correlation(p.low, adv10, 7), 6), 4)
    t2 = ts_rank(decay_linear(delta(p.vwap, 3), 10), 15)
    return t1 + t2


register(AlphaSpec(
    name="gtja044", family=FAMILY, paper=_PAPER,
    description="Composite ts-ranked decays: low-ADV10 corr + VWAP momentum",
    formula_text="TSRANK(DECAYLINEAR(CORR(LOW, MEAN(VOLUME,10), 7), 6), 4) + TSRANK(DECAYLINEAR(DELTA(VWAP,3), 10), 15)",
    compute=_a044,
))


def _a045(p: PanelData) -> pd.Series:
    """RANK(DELTA(CLOSE*0.6 + OPEN*0.4, 1)) * RANK(CORR(VWAP, MEAN(VOLUME,150), 15))"""
    adv150 = ts_mean(p.volume, 150)
    blend = p.close * 0.6 + p.open * 0.4
    return rank(delta(blend, 1)) * rank(correlation(p.vwap, adv150, 15))


register(AlphaSpec(
    name="gtja045", family=FAMILY, paper=_PAPER,
    description="Close-open blend 1d delta rank × VWAP-ADV150 corr rank",
    formula_text="RANK(DELTA(CLOSE*0.6+OPEN*0.4, 1)) * RANK(CORR(VWAP, MEAN(VOLUME,150), 15))",
    compute=_a045,
))


def _a048(p: PanelData) -> pd.Series:
    """-1 * (RANK(SIGN(CLOSE-DELAY(CLOSE,1))+SIGN(DELAY(CLOSE,1)-DELAY(CLOSE,2))+SIGN(DELAY(CLOSE,2)-DELAY(CLOSE,3))))
              * SUM(VOLUME,5) / SUM(VOLUME,20)"""
    s1 = sign(p.close - delay(p.close, 1))
    s2 = sign(delay(p.close, 1) - delay(p.close, 2))
    s3 = sign(delay(p.close, 2) - delay(p.close, 3))
    return (-1.0 * rank(s1 + s2 + s3)) * ts_sum(p.volume, 5) / ts_sum(p.volume, 20).replace(0, np.nan)


register(AlphaSpec(
    name="gtja048", family=FAMILY, paper=_PAPER,
    description="3d directional consistency penalty × 5/20 volume ratio (sister to alpha030)",
    formula_text="-(RANK(sum_of_3_signs)) * SUM(VOLUME,5)/SUM(VOLUME,20)",
    compute=_a048,
))


def _a049(p: PanelData) -> pd.Series:
    """SUM((HIGH+LOW)>=(DELAY(HIGH,1)+DELAY(LOW,1)) ? 0 : MAX(ABS(HIGH-DELAY(HIGH,1)),ABS(LOW-DELAY(LOW,1))), 12)
       / (SUM(condition, 12) + SUM(up_condition, 12))"""
    dh = (p.high - delay(p.high, 1)).abs()
    dl = (p.low - delay(p.low, 1)).abs()
    raw = np.maximum(dh, dl)
    dn_cond = (p.high + p.low) < (delay(p.high, 1) + delay(p.low, 1))
    up_cond = (p.high + p.low) > (delay(p.high, 1) + delay(p.low, 1))
    dn = raw.where(dn_cond, 0.0)
    up = raw.where(up_cond, 0.0)
    return ts_sum(dn, 12) / (ts_sum(dn, 12) + ts_sum(up, 12)).replace(0, np.nan)


register(AlphaSpec(
    name="gtja049", family=FAMILY, paper=_PAPER,
    description="12d down-direction range sum / (down+up range sum) — directional volatility share",
    formula_text="SUM(down_range,12) / (SUM(down_range,12) + SUM(up_range,12))",
    compute=_a049,
))


def _a050(p: PanelData) -> pd.Series:
    """gtja049 inverse: SUM(up_range,12)/(SUM(down,12)+SUM(up,12)) - SUM(down,12)/(SUM(down,12)+SUM(up,12))"""
    dh = (p.high - delay(p.high, 1)).abs()
    dl = (p.low - delay(p.low, 1)).abs()
    raw = np.maximum(dh, dl)
    dn_cond = (p.high + p.low) < (delay(p.high, 1) + delay(p.low, 1))
    up_cond = (p.high + p.low) > (delay(p.high, 1) + delay(p.low, 1))
    dn = raw.where(dn_cond, 0.0)
    up = raw.where(up_cond, 0.0)
    denom = (ts_sum(dn, 12) + ts_sum(up, 12)).replace(0, np.nan)
    return ts_sum(up, 12) / denom - ts_sum(dn, 12) / denom


register(AlphaSpec(
    name="gtja050", family=FAMILY, paper=_PAPER,
    description="Up-down directional volatility net share (sister to gtja049)",
    formula_text="SUM(up_range,12)/total - SUM(down_range,12)/total",
    compute=_a050,
))


def _a051(p: PanelData) -> pd.Series:
    """Same shape as gtja049 but with up condition: SUM(up_range,12) / (SUM(down,12)+SUM(up,12))"""
    dh = (p.high - delay(p.high, 1)).abs()
    dl = (p.low - delay(p.low, 1)).abs()
    raw = np.maximum(dh, dl)
    up_cond = (p.high + p.low) > (delay(p.high, 1) + delay(p.low, 1))
    dn_cond = (p.high + p.low) < (delay(p.high, 1) + delay(p.low, 1))
    up = raw.where(up_cond, 0.0)
    dn = raw.where(dn_cond, 0.0)
    return ts_sum(up, 12) / (ts_sum(up, 12) + ts_sum(dn, 12)).replace(0, np.nan)


register(AlphaSpec(
    name="gtja051", family=FAMILY, paper=_PAPER,
    description="Up-direction range share — sister to gtja049 with opposite numerator",
    formula_text="SUM(up_range,12) / (SUM(down_range,12) + SUM(up_range,12))",
    compute=_a051,
))


def _a055(p: PanelData) -> pd.Series:
    """SUM(16 * (CLOSE - DELAY(CLOSE,1) + (CLOSE-OPEN)/2 + DELAY(CLOSE,1) - DELAY(OPEN,1)) / TR, 20)
       where TR = MAX(|HIGH-DELAY(CLOSE,1)|, |LOW-DELAY(CLOSE,1)|, |HIGH-LOW|)"""
    pc = delay(p.close, 1)
    po = delay(p.open, 1)
    tr = pd.concat([(p.high - pc).abs(), (p.low - pc).abs(), (p.high - p.low).abs()], axis=1).max(axis=1)
    num = 16.0 * (p.close - pc + (p.close - p.open) / 2.0 + pc - po) / tr.replace(0, np.nan)
    return ts_sum(num, 20)


register(AlphaSpec(
    name="gtja055", family=FAMILY, paper=_PAPER,
    description="20d cumulative complex true-range-normalised momentum (simplified)",
    formula_text="SUM(16*(CLOSE-DELAY(CLOSE,1)+(CLOSE-OPEN)/2+DELAY(CLOSE,1)-DELAY(OPEN,1))/TR, 20)",
    compute=_a055,
))


def _a056(p: PanelData) -> pd.Series:
    """RANK((OPEN-TSMIN(OPEN,12)))
       < RANK((RANK(CORR(SUM((HIGH+LOW)/2, 19), SUM(MEAN(VOLUME,40),19), 13))^5))"""
    adv40 = ts_mean(p.volume, 40)
    mid = (p.high + p.low) / 2.0
    t1 = rank(p.open - ts_min(p.open, 12))
    inner = correlation(ts_sum(mid, 19), ts_sum(adv40, 19), 13)
    t2 = rank(np.power(rank(inner).clip(lower=1e-6), 5))
    return (t1 < t2).astype(float)


register(AlphaSpec(
    name="gtja056", family=FAMILY, paper=_PAPER,
    description="Boolean: open-floor rank vs quintic mid-ADV40 correlation rank",
    formula_text="RANK(OPEN-TSMIN(OPEN,12)) < RANK(RANK(CORR(SUM(mid,19), SUM(MEAN(VOLUME,40),19), 13))^5)",
    compute=_a056,
))


def _a059(p: PanelData) -> pd.Series:
    """SUM((CLOSE = DELAY(CLOSE,1) ? 0 :
          CLOSE - (CLOSE > DELAY(CLOSE,1) ? MIN(LOW, DELAY(CLOSE,1)) : MAX(HIGH, DELAY(CLOSE,1)))), 20)"""
    pc = delay(p.close, 1)
    up_val = p.close - np.minimum(p.low, pc)
    dn_val = p.close - np.maximum(p.high, pc)
    val = up_val.where(p.close > pc, dn_val)
    val = val.where(p.close != pc, 0.0)
    return ts_sum(val, 20)


register(AlphaSpec(
    name="gtja059", family=FAMILY, paper=_PAPER,
    description="20d cumulative directional true-range chunk (sister to gtja003 with window 20)",
    formula_text="SUM(directional_chunk, 20)",
    compute=_a059,
))


def _a060(p: PanelData) -> pd.Series:
    """SUM((CLOSE-LOW-(HIGH-CLOSE))/(HIGH-LOW)*VOLUME, 20)"""
    rng = (p.high - p.low).replace(0, np.nan)
    return ts_sum(((p.close - p.low) - (p.high - p.close)) / rng * p.volume, 20)


register(AlphaSpec(
    name="gtja060", family=FAMILY, paper=_PAPER,
    description="20d cumulative volume-weighted money-flow position (longer-window sister to gtja011)",
    formula_text="SUM(((CLOSE-LOW)-(HIGH-CLOSE))/(HIGH-LOW)*VOLUME, 20)",
    compute=_a060,
))


def _a061(p: PanelData) -> pd.Series:
    """MAX(RANK(DECAYLINEAR(DELTA(VWAP, 1), 12)),
           RANK(DECAYLINEAR(RANK(CORR(LOW, MEAN(VOLUME,80), 8)), 17))) * -1"""
    adv80 = ts_mean(p.volume, 80)
    t1 = rank(decay_linear(delta(p.vwap, 1), 12))
    t2 = rank(decay_linear(rank(correlation(p.low, adv80, 8)), 17))
    return -1.0 * np.maximum(t1, t2)


register(AlphaSpec(
    name="gtja061", family=FAMILY, paper=_PAPER,
    description="Negative max of two decayed ranks: VWAP delta vs low-ADV80 corr",
    formula_text="-max(RANK(DECAYLINEAR(DELTA(VWAP,1),12)), RANK(DECAYLINEAR(RANK(CORR(LOW, MEAN(VOLUME,80), 8)), 17)))",
    compute=_a061,
))


def _a062(p: PanelData) -> pd.Series:
    """-1 * CORR(HIGH, RANK(VOLUME), 5)"""
    return -1.0 * correlation(p.high, rank(p.volume), 5)


register(AlphaSpec(
    name="gtja062", family=FAMILY, paper=_PAPER,
    description="Negative 5d high-rank-volume correlation (sister to alpha044)",
    formula_text="-CORR(HIGH, RANK(VOLUME), 5)",
    compute=_a062,
))


def _a063(p: PanelData) -> pd.Series:
    """SMA(MAX(CLOSE-DELAY(CLOSE,1),0), 6, 1) / SMA(ABS(CLOSE-DELAY(CLOSE,1)), 6, 1) * 100"""
    diff = p.close - delay(p.close, 1)
    up = diff.clip(lower=0)
    abs_diff = diff.abs()
    return sma(up, 6, 1) / sma(abs_diff, 6, 1).replace(0, np.nan) * 100.0


register(AlphaSpec(
    name="gtja063", family=FAMILY, paper=_PAPER,
    description="6d RSI-style up-day ratio in percent",
    formula_text="SMA(MAX(CLOSE-DELAY(CLOSE,1),0),6,1) / SMA(ABS(CLOSE-DELAY(CLOSE,1)),6,1) * 100",
    compute=_a063,
))


def _a066(p: PanelData) -> pd.Series:
    """(CLOSE - MEAN(CLOSE,6)) / MEAN(CLOSE,6) * 100"""
    m = ts_mean(p.close, 6)
    return (p.close - m) / m.replace(0, np.nan) * 100.0


register(AlphaSpec(
    name="gtja066", family=FAMILY, paper=_PAPER,
    description="6d deviation from MA in percent (sister to gtja031 shorter window)",
    formula_text="(CLOSE - MEAN(CLOSE,6)) / MEAN(CLOSE,6) * 100",
    compute=_a066,
))


def _a067(p: PanelData) -> pd.Series:
    """SMA(MAX(CLOSE-DELAY(CLOSE,1),0), 24, 1) / SMA(ABS(CLOSE-DELAY(CLOSE,1)), 24, 1) * 100"""
    diff = p.close - delay(p.close, 1)
    return sma(diff.clip(lower=0), 24, 1) / sma(diff.abs(), 24, 1).replace(0, np.nan) * 100.0


register(AlphaSpec(
    name="gtja067", family=FAMILY, paper=_PAPER,
    description="24d RSI-style up-day ratio (longer than gtja063)",
    formula_text="SMA(MAX(CLOSE-DELAY(CLOSE,1),0),24,1) / SMA(ABS(CLOSE-DELAY(CLOSE,1)),24,1) * 100",
    compute=_a067,
))


def _a069(p: PanelData) -> pd.Series:
    """SUM(DTM, 20) > SUM(DBM, 20) ? (SUM(DTM,20) - SUM(DBM,20))/SUM(DTM,20)
                                    : (SUM(DBM,20) > SUM(DTM,20) ? (SUM(DTM,20)-SUM(DBM,20))/SUM(DBM,20) : 0)
       where DTM = OPEN<=DELAY(OPEN,1)?0:MAX(HIGH-OPEN, OPEN-DELAY(OPEN,1))
             DBM = OPEN>=DELAY(OPEN,1)?0:MAX(OPEN-LOW, OPEN-DELAY(OPEN,1))"""
    po = delay(p.open, 1)
    dtm = pd.Series(0.0, index=p.close.index).where(
        p.open <= po,
        np.maximum(p.high - p.open, p.open - po)
    )
    dbm = pd.Series(0.0, index=p.close.index).where(
        p.open >= po,
        np.maximum(p.open - p.low, p.open - po)
    )
    sdtm = ts_sum(dtm, 20)
    sdbm = ts_sum(dbm, 20)
    out = pd.Series(0.0, index=p.close.index)
    out = out.where(~(sdbm > sdtm), (sdtm - sdbm) / sdbm.replace(0, np.nan))
    out = out.where(~(sdtm > sdbm), (sdtm - sdbm) / sdtm.replace(0, np.nan))
    return out


register(AlphaSpec(
    name="gtja069", family=FAMILY, paper=_PAPER,
    description="Three-state OPEN-direction range ratio over 20 days",
    formula_text="DTM/DBM directional range share (see paper)",
    compute=_a069,
))


def _a070(p: PanelData) -> pd.Series:
    """STD(AMOUNT, 6)"""
    return stddev(p.amount, 6)


register(AlphaSpec(
    name="gtja070", family=FAMILY, paper=_PAPER,
    description="6d stddev of dollar volume (shorter-window sister to gtja095)",
    formula_text="STD(AMOUNT, 6)",
    compute=_a070,
))


def _a071(p: PanelData) -> pd.Series:
    """(CLOSE - MEAN(CLOSE,24)) / MEAN(CLOSE,24) * 100"""
    m = ts_mean(p.close, 24)
    return (p.close - m) / m.replace(0, np.nan) * 100.0


register(AlphaSpec(
    name="gtja071", family=FAMILY, paper=_PAPER,
    description="24d deviation from MA in percent (longer than gtja031/066)",
    formula_text="(CLOSE - MEAN(CLOSE,24)) / MEAN(CLOSE,24) * 100",
    compute=_a071,
))


def _a072(p: PanelData) -> pd.Series:
    """SMA((TSMAX(HIGH,6) - CLOSE) / (TSMAX(HIGH,6) - TSMIN(LOW,6)) * 100, 15, 1)"""
    rng = (ts_max(p.high, 6) - ts_min(p.low, 6)).replace(0, np.nan)
    return sma((ts_max(p.high, 6) - p.close) / rng * 100.0, 15, 1)


register(AlphaSpec(
    name="gtja072", family=FAMILY, paper=_PAPER,
    description="Williams %R style 6d with 15d EWMA smoothing (longer than gtja047)",
    formula_text="SMA((TSMAX(HIGH,6)-CLOSE)/(TSMAX(HIGH,6)-TSMIN(LOW,6))*100, 15, 1)",
    compute=_a072,
))


def _a074(p: PanelData) -> pd.Series:
    """RANK(CORR(SUM((LOW*0.35 + VWAP*0.65), 20), SUM(MEAN(VOLUME,40),20), 7))
       + RANK(CORR(RANK(VWAP), RANK(VOLUME), 6))"""
    adv40 = ts_mean(p.volume, 40)
    blend = p.low * 0.35 + p.vwap * 0.65
    t1 = rank(correlation(ts_sum(blend, 20), ts_sum(adv40, 20), 7))
    t2 = rank(correlation(rank(p.vwap), rank(p.volume), 6))
    return t1 + t2


register(AlphaSpec(
    name="gtja074", family=FAMILY, paper=_PAPER,
    description="Long blend-ADV40 corr rank + short VWAP-volume rank-corr rank",
    formula_text="RANK(CORR(SUM(blend,20), SUM(MEAN(VOLUME,40),20), 7)) + RANK(CORR(RANK(VWAP),RANK(VOLUME),6))",
    compute=_a074,
))


def _a077(p: PanelData) -> pd.Series:
    """MIN(RANK(DECAYLINEAR((HIGH+LOW)/2 + HIGH - VWAP, 20)),
           RANK(DECAYLINEAR(CORR((HIGH+LOW)/2, MEAN(VOLUME,40), 3), 6)))"""
    adv40 = ts_mean(p.volume, 40)
    mid = (p.high + p.low) / 2.0
    t1 = rank(decay_linear(mid + p.high - p.vwap, 20))
    t2 = rank(decay_linear(correlation(mid, adv40, 3), 6))
    return np.minimum(t1, t2)


register(AlphaSpec(
    name="gtja077", family=FAMILY, paper=_PAPER,
    description="Min of two decayed ranks: midpoint+high-vwap vs mid-ADV40 correlation",
    formula_text="MIN(RANK(DECAYLINEAR(mid+high-vwap, 20)), RANK(DECAYLINEAR(CORR(mid, MEAN(VOLUME,40), 3), 6)))",
    compute=_a077,
))


def _a078(p: PanelData) -> pd.Series:
    """((HIGH+LOW+CLOSE)/3 - MEAN((HIGH+LOW+CLOSE)/3, 12))
       / (0.015 * MEAN(ABS(CLOSE - MEAN((HIGH+LOW+CLOSE)/3, 12)), 12))"""
    tp = (p.high + p.low + p.close) / 3.0
    sma12 = ts_mean(tp, 12)
    mad = ts_mean((p.close - sma12).abs(), 12)
    return (tp - sma12) / (0.015 * mad).replace(0, np.nan)


register(AlphaSpec(
    name="gtja078", family=FAMILY, paper=_PAPER,
    description="Commodity Channel Index (CCI) over 12d typical price",
    formula_text="(TP - MA12(TP)) / (0.015 * MAD(CLOSE-MA12(TP), 12))",
    compute=_a078,
))


def _a079(p: PanelData) -> pd.Series:
    """SMA(MAX(CLOSE-DELAY(CLOSE,1),0), 12, 1) / SMA(ABS(CLOSE-DELAY(CLOSE,1)), 12, 1) * 100"""
    diff = p.close - delay(p.close, 1)
    return sma(diff.clip(lower=0), 12, 1) / sma(diff.abs(), 12, 1).replace(0, np.nan) * 100.0


register(AlphaSpec(
    name="gtja079", family=FAMILY, paper=_PAPER,
    description="12d RSI-style up-day ratio (mid-window between gtja063/067)",
    formula_text="SMA(MAX(CLOSE-DELAY(CLOSE,1),0),12,1) / SMA(ABS(CLOSE-DELAY(CLOSE,1)),12,1) * 100",
    compute=_a079,
))


def _a080(p: PanelData) -> pd.Series:
    """(VOLUME - DELAY(VOLUME, 5)) / DELAY(VOLUME, 5) * 100"""
    pv = delay(p.volume, 5)
    return (p.volume - pv) / pv.replace(0, np.nan) * 100.0


register(AlphaSpec(
    name="gtja080", family=FAMILY, paper=_PAPER,
    description="5d volume change in percent",
    formula_text="(VOLUME - DELAY(VOLUME,5)) / DELAY(VOLUME,5) * 100",
    compute=_a080,
))


def _a081(p: PanelData) -> pd.Series:
    """SMA(VOLUME, 21, 2)"""
    return sma(p.volume, 21, 2)


register(AlphaSpec(
    name="gtja081", family=FAMILY, paper=_PAPER,
    description="21d EWMA of volume",
    formula_text="SMA(VOLUME, 21, 2)",
    compute=_a081,
))


def _a082(p: PanelData) -> pd.Series:
    """SMA((TSMAX(HIGH,6) - CLOSE) / (TSMAX(HIGH,6) - TSMIN(LOW,6)) * 100, 20, 1)"""
    rng = (ts_max(p.high, 6) - ts_min(p.low, 6)).replace(0, np.nan)
    return sma((ts_max(p.high, 6) - p.close) / rng * 100.0, 20, 1)


register(AlphaSpec(
    name="gtja082", family=FAMILY, paper=_PAPER,
    description="Williams %R style 6d with 20d EWMA smoothing (longest of family)",
    formula_text="SMA((TSMAX(HIGH,6)-CLOSE)/(TSMAX(HIGH,6)-TSMIN(LOW,6))*100, 20, 1)",
    compute=_a082,
))


def _a083(p: PanelData) -> pd.Series:
    """-1 * RANK(COVARIANCE(RANK(HIGH), RANK(VOLUME), 5))"""
    return -1.0 * rank(covariance(rank(p.high), rank(p.volume), 5))


register(AlphaSpec(
    name="gtja083", family=FAMILY, paper=_PAPER,
    description="Negative rank of high-volume rank-covariance (sister to alpha013/016)",
    formula_text="-RANK(COV(RANK(HIGH),RANK(VOLUME),5))",
    compute=_a083,
))


def _a084(p: PanelData) -> pd.Series:
    """SUM(CLOSE>DELAY(CLOSE,1) ? VOLUME : (CLOSE<DELAY(CLOSE,1) ? -VOLUME : 0), 20)"""
    pc = delay(p.close, 1)
    signed = p.volume.where(p.close > pc, -p.volume.where(p.close < pc, 0.0))
    return ts_sum(signed, 20)


register(AlphaSpec(
    name="gtja084", family=FAMILY, paper=_PAPER,
    description="20d cumulative signed volume — OBV-style flow (longer than gtja043)",
    formula_text="SUM(signed_volume, 20)",
    compute=_a084,
))


def _a085(p: PanelData) -> pd.Series:
    """TSRANK(VOLUME / MEAN(VOLUME, 20), 20) * TSRANK(-DELTA(CLOSE, 7), 8)"""
    adv20 = ts_mean(p.volume, 20)
    return (ts_rank(p.volume / adv20.replace(0, np.nan), 20)
            * ts_rank(-delta(p.close, 7), 8))


register(AlphaSpec(
    name="gtja085", family=FAMILY, paper=_PAPER,
    description="Volume-ratio ts-rank × reversed 7d close-delta ts-rank (sister to alpha043)",
    formula_text="TSRANK(VOLUME/MEAN(VOLUME,20),20) * TSRANK(-DELTA(CLOSE,7),8)",
    compute=_a085,
))


def _a086(p: PanelData) -> pd.Series:
    """((0.25 < (DELAY(CLOSE,20)-DELAY(CLOSE,10))/10 - (DELAY(CLOSE,10)-CLOSE)/10) ? -1
       : (((DELAY(CLOSE,20)-DELAY(CLOSE,10))/10 - (DELAY(CLOSE,10)-CLOSE)/10 < 0) ? 1 : -(CLOSE-DELAY(CLOSE,1))))"""
    s1 = (delay(p.close, 20) - delay(p.close, 10)) / 10.0
    s2 = (delay(p.close, 10) - p.close) / 10.0
    diff = s1 - s2
    out = -1.0 * (p.close - delay(p.close, 1))
    out = out.where(diff >= 0, 1.0)
    out = out.where(diff <= 0.25, -1.0)
    return out


register(AlphaSpec(
    name="gtja086", family=FAMILY, paper=_PAPER,
    description="Three-state slope regime switch (sister to alpha046)",
    formula_text="(slope_diff > 0.25 ? -1 : (slope_diff < 0 ? 1 : -delta(close,1)))",
    compute=_a086,
))


def _a088(p: PanelData) -> pd.Series:
    """(CLOSE - DELAY(CLOSE, 20)) / DELAY(CLOSE, 20) * 100"""
    pc = delay(p.close, 20)
    return (p.close - pc) / pc.replace(0, np.nan) * 100.0


register(AlphaSpec(
    name="gtja088", family=FAMILY, paper=_PAPER,
    description="20d percentage return — classic monthly momentum",
    formula_text="(CLOSE - DELAY(CLOSE,20)) / DELAY(CLOSE,20) * 100",
    compute=_a088,
))


def _a093(p: PanelData) -> pd.Series:
    """SUM((OPEN >= DELAY(OPEN,1) ? 0 : MAX(OPEN-LOW, OPEN-DELAY(OPEN,1))), 20)"""
    po = delay(p.open, 1)
    val = pd.Series(0.0, index=p.open.index).where(
        p.open >= po,
        np.maximum(p.open - p.low, p.open - po)
    )
    return ts_sum(val, 20)


register(AlphaSpec(
    name="gtja093", family=FAMILY, paper=_PAPER,
    description="20d cumulative DBM (down-direction open-range exposure)",
    formula_text="SUM(DBM_open_range, 20)",
    compute=_a093,
))


def _a096(p: PanelData) -> pd.Series:
    """SMA(SMA((CLOSE-TSMIN(LOW,9))/(TSMAX(HIGH,9)-TSMIN(LOW,9))*100, 3, 1), 3, 1)"""
    rng = (ts_max(p.high, 9) - ts_min(p.low, 9)).replace(0, np.nan)
    raw = (p.close - ts_min(p.low, 9)) / rng * 100.0
    return sma(sma(raw, 3, 1), 3, 1)


register(AlphaSpec(
    name="gtja096", family=FAMILY, paper=_PAPER,
    description="9d stochastic K%, double-smoothed — standard %D indicator",
    formula_text="SMA(SMA((CLOSE-TSMIN(LOW,9))/(TSMAX(HIGH,9)-TSMIN(LOW,9))*100, 3, 1), 3, 1)",
    compute=_a096,
))


def _a097(p: PanelData) -> pd.Series:
    """STD(VOLUME, 10)"""
    return stddev(p.volume, 10)


register(AlphaSpec(
    name="gtja097", family=FAMILY, paper=_PAPER,
    description="10d stddev of volume — short-window volume volatility",
    formula_text="STD(VOLUME, 10)",
    compute=_a097,
))


def _a098(p: PanelData) -> pd.Series:
    """((DELTA(SUM(CLOSE,100)/100, 100) / DELAY(CLOSE,100) <= 0.05)
       ? -1*(CLOSE-TSMIN(CLOSE,100)) : -DELTA(CLOSE,3))"""
    sma100 = ts_sum(p.close, 100) / 100.0
    pct_change = delta(sma100, 100) / delay(p.close, 100).replace(0, np.nan)
    long_term = -1.0 * (p.close - ts_min(p.close, 100))
    short_term = -1.0 * delta(p.close, 3)
    return long_term.where(pct_change <= 0.05, short_term)


register(AlphaSpec(
    name="gtja098", family=FAMILY, paper=_PAPER,
    description="100d SMA-growth regime switch (sister to alpha024)",
    formula_text="((delta_sma100/delay_close <= 0.05) ? -(close-tsmin100) : -delta(close,3))",
    compute=_a098,
))


def _a099(p: PanelData) -> pd.Series:
    """-1 * RANK(COV(RANK(CLOSE), RANK(VOLUME), 5))"""
    return -1.0 * rank(covariance(rank(p.close), rank(p.volume), 5))


register(AlphaSpec(
    name="gtja099", family=FAMILY, paper=_PAPER,
    description="Negative rank of close-volume rank-covariance (sister to alpha013)",
    formula_text="-RANK(COV(RANK(CLOSE),RANK(VOLUME),5))",
    compute=_a099,
))


def _a100(p: PanelData) -> pd.Series:
    """STD(VOLUME, 20)"""
    return stddev(p.volume, 20)


register(AlphaSpec(
    name="gtja100", family=FAMILY, paper=_PAPER,
    description="20d stddev of volume",
    formula_text="STD(VOLUME, 20)",
    compute=_a100,
))


def _a102(p: PanelData) -> pd.Series:
    """SMA(MAX(VOLUME-DELAY(VOLUME,1),0), 6, 1) / SMA(ABS(VOLUME-DELAY(VOLUME,1)),6,1) * 100"""
    diff = p.volume - delay(p.volume, 1)
    return sma(diff.clip(lower=0), 6, 1) / sma(diff.abs(), 6, 1).replace(0, np.nan) * 100.0


register(AlphaSpec(
    name="gtja102", family=FAMILY, paper=_PAPER,
    description="6d RSI-style up-volume ratio in percent — volume-side momentum",
    formula_text="SMA(MAX(VOLUME-DELAY(VOLUME,1),0),6,1) / SMA(ABS(VOLUME-DELAY(VOLUME,1)),6,1) * 100",
    compute=_a102,
))


def _a106(p: PanelData) -> pd.Series:
    """CLOSE - DELAY(CLOSE, 20)"""
    return p.close - delay(p.close, 20)


register(AlphaSpec(
    name="gtja106", family=FAMILY, paper=_PAPER,
    description="20d close change (absolute) — sister to gtja014/088",
    formula_text="CLOSE - DELAY(CLOSE, 20)",
    compute=_a106,
))


def _a109(p: PanelData) -> pd.Series:
    """SMA(HIGH - LOW, 10, 2) / SMA(SMA(HIGH-LOW, 10, 2), 10, 2)"""
    rng = p.high - p.low
    s1 = sma(rng, 10, 2)
    s2 = sma(s1, 10, 2)
    return s1 / s2.replace(0, np.nan)


register(AlphaSpec(
    name="gtja109", family=FAMILY, paper=_PAPER,
    description="Range / smoothed-smoothed range — range expansion regime",
    formula_text="SMA(HIGH-LOW,10,2) / SMA(SMA(HIGH-LOW,10,2),10,2)",
    compute=_a109,
))


def _a118(p: PanelData) -> pd.Series:
    """SUM(HIGH-OPEN, 20) / SUM(OPEN-LOW, 20) * 100"""
    return ts_sum(p.high - p.open, 20) / ts_sum(p.open - p.low, 20).replace(0, np.nan) * 100.0


register(AlphaSpec(
    name="gtja118", family=FAMILY, paper=_PAPER,
    description="20d cumulative upper-wick / lower-wick ratio in percent",
    formula_text="SUM(HIGH-OPEN, 20) / SUM(OPEN-LOW, 20) * 100",
    compute=_a118,
))


def _a126(p: PanelData) -> pd.Series:
    """(CLOSE + HIGH + LOW) / 3"""
    return (p.close + p.high + p.low) / 3.0


register(AlphaSpec(
    name="gtja126", family=FAMILY, paper=_PAPER,
    description="Typical price — pivot baseline used in many indicators",
    formula_text="(CLOSE + HIGH + LOW) / 3",
    compute=_a126,
))


def _a129(p: PanelData) -> pd.Series:
    """SUM(CLOSE < DELAY(CLOSE,1) ? ABS(CLOSE-DELAY(CLOSE,1)) : 0, 12)"""
    diff = p.close - delay(p.close, 1)
    raw = diff.abs().where(p.close < delay(p.close, 1), 0.0)
    return ts_sum(raw, 12)


register(AlphaSpec(
    name="gtja129", family=FAMILY, paper=_PAPER,
    description="12d cumulative absolute down-move — drawdown intensity",
    formula_text="SUM((CLOSE<DELAY(CLOSE,1) ? |CLOSE-DELAY(CLOSE,1)| : 0), 12)",
    compute=_a129,
))


def _a133(p: PanelData) -> pd.Series:
    """((20-HIGHDAY(HIGH,20))/20)*100 - ((20-LOWDAY(LOW,20))/20)*100"""
    # HIGHDAY = position of max in last N days (1-indexed from latest backward)
    # equivalent to: N - ts_argmax(.) where ts_argmax returns 1=oldest, N=latest
    high_pos = 20 - ts_argmax(p.high, 20)
    low_pos = 20 - ts_argmin(p.low, 20)
    return (high_pos / 20.0 - low_pos / 20.0) * 100.0


register(AlphaSpec(
    name="gtja133", family=FAMILY, paper=_PAPER,
    description="20d recency-difference between highest high and lowest low",
    formula_text="((20-HIGHDAY(HIGH,20))/20)*100 - ((20-LOWDAY(LOW,20))/20)*100",
    compute=_a133,
))


def _a135(p: PanelData) -> pd.Series:
    """SMA(DELAY(CLOSE/DELAY(CLOSE,20), 1), 20, 1)"""
    rel = p.close / delay(p.close, 20).replace(0, np.nan)
    return sma(delay(rel, 1), 20, 1)


register(AlphaSpec(
    name="gtja135", family=FAMILY, paper=_PAPER,
    description="20d EWMA of 20d-prior 20d price ratio — long lagged momentum",
    formula_text="SMA(DELAY(CLOSE/DELAY(CLOSE,20),1), 20, 1)",
    compute=_a135,
))


def _a139(p: PanelData) -> pd.Series:
    """-1 * CORR(OPEN, VOLUME, 10)"""
    return -1.0 * correlation(p.open, p.volume, 10)


register(AlphaSpec(
    name="gtja139", family=FAMILY, paper=_PAPER,
    description="Negative 10d open-volume correlation (sister to alpha006)",
    formula_text="-CORR(OPEN, VOLUME, 10)",
    compute=_a139,
))


def _a150(p: PanelData) -> pd.Series:
    """(CLOSE+HIGH+LOW)/3 * VOLUME"""
    return (p.close + p.high + p.low) / 3.0 * p.volume


register(AlphaSpec(
    name="gtja150", family=FAMILY, paper=_PAPER,
    description="Typical price × volume — dollar-volume proxy",
    formula_text="(CLOSE+HIGH+LOW)/3 * VOLUME",
    compute=_a150,
))


def _a161(p: PanelData) -> pd.Series:
    """MEAN(MAX(MAX(HIGH-LOW, ABS(DELAY(CLOSE,1)-HIGH)), ABS(DELAY(CLOSE,1)-LOW)), 12)"""
    pc = delay(p.close, 1)
    tr = pd.concat([
        (p.high - p.low),
        (pc - p.high).abs(),
        (pc - p.low).abs(),
    ], axis=1).max(axis=1)
    return ts_mean(tr, 12)


register(AlphaSpec(
    name="gtja161", family=FAMILY, paper=_PAPER,
    description="12d mean of true range — ATR-style volatility",
    formula_text="MEAN(TRUE_RANGE, 12)",
    compute=_a161,
))


def _a167(p: PanelData) -> pd.Series:
    """SUM(CLOSE > DELAY(CLOSE,1) ? CLOSE-DELAY(CLOSE,1) : 0, 12)"""
    diff = p.close - delay(p.close, 1)
    return ts_sum(diff.where(p.close > delay(p.close, 1), 0.0), 12)


register(AlphaSpec(
    name="gtja167", family=FAMILY, paper=_PAPER,
    description="12d cumulative up-move — upside intensity",
    formula_text="SUM((CLOSE>DELAY(CLOSE,1) ? CLOSE-DELAY(CLOSE,1) : 0), 12)",
    compute=_a167,
))


def _a168(p: PanelData) -> pd.Series:
    """-1 * VOLUME / MEAN(VOLUME, 20)"""
    return -1.0 * p.volume / ts_mean(p.volume, 20).replace(0, np.nan)


register(AlphaSpec(
    name="gtja168", family=FAMILY, paper=_PAPER,
    description="Negative relative volume — fade high-volume days",
    formula_text="-VOLUME / MEAN(VOLUME, 20)",
    compute=_a168,
))


def _a176(p: PanelData) -> pd.Series:
    """CORR(RANK((CLOSE-TSMIN(LOW,12)) / (TSMAX(HIGH,12)-TSMIN(LOW,12))), RANK(VOLUME), 6)"""
    rng = (ts_max(p.high, 12) - ts_min(p.low, 12)).replace(0, np.nan)
    rsv = (p.close - ts_min(p.low, 12)) / rng
    return correlation(rank(rsv), rank(p.volume), 6)


register(AlphaSpec(
    name="gtja176", family=FAMILY, paper=_PAPER,
    description="6d correlation of stochastic %K rank with volume rank — confirmation signal",
    formula_text="CORR(RANK(RSV12), RANK(VOLUME), 6)",
    compute=_a176,
))


def _a178(p: PanelData) -> pd.Series:
    """(CLOSE - DELAY(CLOSE,1)) / DELAY(CLOSE,1) * VOLUME"""
    pc = delay(p.close, 1)
    return (p.close - pc) / pc.replace(0, np.nan) * p.volume


register(AlphaSpec(
    name="gtja178", family=FAMILY, paper=_PAPER,
    description="1d return × volume — single-day flow-momentum (shorter than gtja029)",
    formula_text="(CLOSE-DELAY(CLOSE,1))/DELAY(CLOSE,1) * VOLUME",
    compute=_a178,
))


def _a184(p: PanelData) -> pd.Series:
    """RANK(CORR(DELAY((OPEN-CLOSE),1), CLOSE, 200)) + RANK((OPEN-CLOSE))"""
    return rank(correlation(delay(p.open - p.close, 1), p.close, 200)) + rank(p.open - p.close)


register(AlphaSpec(
    name="gtja184", family=FAMILY, paper=_PAPER,
    description="200d delayed body-close corr rank + intraday body rank (sister to alpha037)",
    formula_text="RANK(CORR(DELAY((OPEN-CLOSE),1), CLOSE, 200)) + RANK((OPEN-CLOSE))",
    compute=_a184,
))
