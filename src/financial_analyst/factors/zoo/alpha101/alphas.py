"""Ports of WorldQuant 101 Formulaic Alphas — most-cited subset.

Each implementation tracks the paper's notation exactly (modulo Python
syntax). The bench loop is in ``factors.zoo.bench_runner``.

Paper: Zura Kakushadze, "101 Formulaic Alphas" (arXiv:1601.00991, 2015).
"""
from __future__ import annotations
import numpy as np
import pandas as pd

from financial_analyst.factors.zoo.panel import PanelData
from financial_analyst.factors.zoo.registry import AlphaSpec, register
from financial_analyst.factors.zoo.operators import (
    rank, ts_argmax, ts_argmin, ts_max, ts_min, ts_sum, ts_rank,
    delta, delay, correlation, covariance, decay_linear, stddev,
    signedpower, scale, log, sign, abs_,
)

FAMILY = "alpha101"
_PAPER = "Kakushadze 2015 — 101 Formulaic Alphas (arXiv:1601.00991)"


def _a001(p: PanelData) -> pd.Series:
    """rank(Ts_ArgMax(SignedPower(((returns<0)?stddev(returns,20):close),2.),5))-0.5"""
    ret = p.returns
    x = ret.where(ret < 0, p.close)
    x = ret.copy()
    cond = ret < 0
    x = stddev(ret, 20).where(cond, p.close)
    return rank(ts_argmax(signedpower(x, 2.0), 5)) - 0.5


register(AlphaSpec(
    name="alpha001", family=FAMILY, paper=_PAPER,
    description="Recency of vol shock vs close — captures recent risk-on / risk-off pivots",
    formula_text="rank(Ts_ArgMax(SignedPower(((returns<0)?stddev(returns,20):close),2.),5))-0.5",
    compute=_a001,
))


def _a002(p: PanelData) -> pd.Series:
    """-1 * correlation(rank(delta(log(volume),2)), rank(((close-open)/open)), 6)"""
    a = rank(delta(log(p.volume), 2))
    b = rank((p.close - p.open) / p.open)
    return -1.0 * correlation(a, b, 6)


register(AlphaSpec(
    name="alpha002", family=FAMILY, paper=_PAPER,
    description="Negative rank-corr between recent log-volume change and intraday return",
    formula_text="-1*correlation(rank(delta(log(volume),2)),rank((close-open)/open),6)",
    compute=_a002,
))


def _a003(p: PanelData) -> pd.Series:
    """-1 * correlation(rank(open), rank(volume), 10)"""
    return -1.0 * correlation(rank(p.open), rank(p.volume), 10)


register(AlphaSpec(
    name="alpha003", family=FAMILY, paper=_PAPER,
    description="Negative rank-corr of open price vs volume over 10 days — reverses crowd interest",
    formula_text="-1*correlation(rank(open),rank(volume),10)",
    compute=_a003,
))


def _a004(p: PanelData) -> pd.Series:
    """-1 * Ts_Rank(rank(low), 9)"""
    return -1.0 * ts_rank(rank(p.low), 9)


register(AlphaSpec(
    name="alpha004", family=FAMILY, paper=_PAPER,
    description="Reversal on weak lows — penalises recently low-trending lows",
    formula_text="-1*Ts_Rank(rank(low),9)",
    compute=_a004,
))


def _a006(p: PanelData) -> pd.Series:
    """-1 * correlation(open, volume, 10)"""
    return -1.0 * correlation(p.open, p.volume, 10)


register(AlphaSpec(
    name="alpha006", family=FAMILY, paper=_PAPER,
    description="Direct (non-rank) negative price-volume correlation, contrarian short-horizon",
    formula_text="-1*correlation(open,volume,10)",
    compute=_a006,
))


def _a007(p: PanelData) -> pd.Series:
    """((adv20<volume) ? -1*ts_rank(abs(delta(close,7)),60)*sign(delta(close,7)) : -1)"""
    adv20 = ts_sum(p.volume, 20) / 20.0
    d7 = delta(p.close, 7)
    bull_branch = -1.0 * ts_rank(d7.abs(), 60) * sign(d7)
    return bull_branch.where(adv20 < p.volume, -1.0)


register(AlphaSpec(
    name="alpha007", family=FAMILY, paper=_PAPER,
    description="On high-volume days, fade extended price moves; on low-volume days, stay bearish (-1)",
    formula_text="(adv20<volume) ? -1*ts_rank(abs(delta(close,7)),60)*sign(delta(close,7)) : -1",
    compute=_a007,
))


def _a012(p: PanelData) -> pd.Series:
    """sign(delta(volume,1)) * (-1*delta(close,1))"""
    return sign(delta(p.volume, 1)) * (-1.0 * delta(p.close, 1))


register(AlphaSpec(
    name="alpha012", family=FAMILY, paper=_PAPER,
    description="Volume-direction times reversed close-change — flow-momentum interaction",
    formula_text="sign(delta(volume,1)) * (-1*delta(close,1))",
    compute=_a012,
))


def _a013(p: PanelData) -> pd.Series:
    """-1 * rank(covariance(rank(close),rank(volume),5))"""
    return -1.0 * rank(covariance(rank(p.close), rank(p.volume), 5))


register(AlphaSpec(
    name="alpha013", family=FAMILY, paper=_PAPER,
    description="Negative rank of price-volume rank-covariance — fades crowded names",
    formula_text="-1*rank(covariance(rank(close),rank(volume),5))",
    compute=_a013,
))


def _a014(p: PanelData) -> pd.Series:
    """(-1 * rank(delta(returns,3))) * correlation(open, volume, 10)"""
    return (-1.0 * rank(delta(p.returns, 3))) * correlation(p.open, p.volume, 10)


register(AlphaSpec(
    name="alpha014", family=FAMILY, paper=_PAPER,
    description="Recent return reversal weighted by price-volume coherence",
    formula_text="(-1*rank(delta(returns,3))) * correlation(open,volume,10)",
    compute=_a014,
))


def _a015(p: PanelData) -> pd.Series:
    """-1 * sum(rank(correlation(rank(high),rank(volume),3)),3)"""
    return -1.0 * ts_sum(rank(correlation(rank(p.high), rank(p.volume), 3)), 3)


register(AlphaSpec(
    name="alpha015", family=FAMILY, paper=_PAPER,
    description="Three-day sum of rank-corr(high, volume) — sustained crowding signal, negated",
    formula_text="-1*sum(rank(correlation(rank(high),rank(volume),3)),3)",
    compute=_a015,
))


def _a005(p: PanelData) -> pd.Series:
    """rank((open - (sum(vwap,10)/10))) * (-1 * abs(rank((close - vwap))))"""
    return rank(p.open - ts_sum(p.vwap, 10) / 10.0) * (-1.0 * (rank(p.close - p.vwap)).abs())


register(AlphaSpec(
    name="alpha005", family=FAMILY, paper=_PAPER,
    description="Open vs 10d-VWAP rank weighted by negative |close-VWAP rank| — penalises overextension",
    formula_text="rank(open - sum(vwap,10)/10) * (-1*abs(rank(close - vwap)))",
    compute=_a005,
))


def _a008(p: PanelData) -> pd.Series:
    """-1 * rank(((sum(open,5)*sum(returns,5)) - delay((sum(open,5)*sum(returns,5)),10)))"""
    base = ts_sum(p.open, 5) * ts_sum(p.returns, 5)
    return -1.0 * rank(base - delay(base, 10))


register(AlphaSpec(
    name="alpha008", family=FAMILY, paper=_PAPER,
    description="10-day change of (open-sum × return-sum) — fades large compounding regime shifts",
    formula_text="-1*rank((sum(open,5)*sum(returns,5)) - delay((sum(open,5)*sum(returns,5)),10))",
    compute=_a008,
))


def _a009(p: PanelData) -> pd.Series:
    """(0 < ts_min(delta(close,1),5)) ? delta(close,1)
       : ((ts_max(delta(close,1),5) < 0) ? delta(close,1) : -1*delta(close,1))"""
    d = delta(p.close, 1)
    tmin = ts_min(d, 5)
    tmax = ts_max(d, 5)
    out = (-1.0 * d).where((tmin <= 0) & (tmax >= 0), d)
    return out


register(AlphaSpec(
    name="alpha009", family=FAMILY, paper=_PAPER,
    description="If 5d trend is monotonic in either direction keep delta; otherwise flip — fades chop",
    formula_text="(0<ts_min(delta(close,1),5)) ? delta(close,1) : ((ts_max(delta(close,1),5)<0) ? delta(close,1) : -1*delta(close,1))",
    compute=_a009,
))


def _a010(p: PanelData) -> pd.Series:
    """rank(((0 < ts_min(delta(close,1),4)) ? delta(close,1)
           : ((ts_max(delta(close,1),4) < 0) ? delta(close,1) : -1*delta(close,1))))"""
    d = delta(p.close, 1)
    tmin = ts_min(d, 4)
    tmax = ts_max(d, 4)
    inner = (-1.0 * d).where((tmin <= 0) & (tmax >= 0), d)
    return rank(inner)


register(AlphaSpec(
    name="alpha010", family=FAMILY, paper=_PAPER,
    description="Cross-sectional rank of alpha009 (4-day window) — normalises across stocks",
    formula_text="rank(alpha009-style with window=4)",
    compute=_a010,
))


def _a011(p: PanelData) -> pd.Series:
    """((rank(ts_max((vwap-close),3)) + rank(ts_min((vwap-close),3))) * rank(delta(volume,3)))"""
    spread = p.vwap - p.close
    return (rank(ts_max(spread, 3)) + rank(ts_min(spread, 3))) * rank(delta(p.volume, 3))


register(AlphaSpec(
    name="alpha011", family=FAMILY, paper=_PAPER,
    description="VWAP-close extremes weighted by volume change — intraday flow imbalance",
    formula_text="(rank(ts_max((vwap-close),3)) + rank(ts_min((vwap-close),3))) * rank(delta(volume,3))",
    compute=_a011,
))


def _a016(p: PanelData) -> pd.Series:
    """-1 * rank(covariance(rank(high), rank(volume), 5))"""
    return -1.0 * rank(covariance(rank(p.high), rank(p.volume), 5))


register(AlphaSpec(
    name="alpha016", family=FAMILY, paper=_PAPER,
    description="Negative rank of price-volume rank-covariance via highs — sister to alpha013",
    formula_text="-1*rank(covariance(rank(high),rank(volume),5))",
    compute=_a016,
))


def _a018(p: PanelData) -> pd.Series:
    """-1 * rank((stddev(abs((close-open)),5) + (close-open)) + correlation(close, open, 10))"""
    body = (p.close - p.open).abs()
    return -1.0 * rank(stddev(body, 5) + (p.close - p.open) + correlation(p.close, p.open, 10))


register(AlphaSpec(
    name="alpha018", family=FAMILY, paper=_PAPER,
    description="Penalise stocks with rising candle-body volatility + open-close cointegration",
    formula_text="-1*rank((stddev(abs(close-open),5) + (close-open)) + correlation(close,open,10))",
    compute=_a018,
))


def _a019(p: PanelData) -> pd.Series:
    """-1 * sign(((close - delay(close,7)) + delta(close,7))) * (1 + rank(1 + sum(returns,250)))"""
    momentum_7 = (p.close - delay(p.close, 7)) + delta(p.close, 7)
    long_term = 1.0 + rank(1.0 + ts_sum(p.returns, 250))
    return -1.0 * sign(momentum_7) * long_term


register(AlphaSpec(
    name="alpha019", family=FAMILY, paper=_PAPER,
    description="Reverse 7d momentum weighted by 250d cumulative-return rank — momentum-against-trend",
    formula_text="-1*sign((close-delay(close,7)) + delta(close,7)) * (1 + rank(1 + sum(returns,250)))",
    compute=_a019,
))


def _a020(p: PanelData) -> pd.Series:
    """(-1*rank(open - delay(high,1))) * rank(open - delay(close,1)) * rank(open - delay(low,1))"""
    return (-1.0 * rank(p.open - delay(p.high, 1))) * rank(p.open - delay(p.close, 1)) * rank(p.open - delay(p.low, 1))


register(AlphaSpec(
    name="alpha020", family=FAMILY, paper=_PAPER,
    description="Triple-rank product on overnight gap vs prior HLC — captures gap-fade behaviour",
    formula_text="(-1*rank(open-delay(high,1))) * rank(open-delay(close,1)) * rank(open-delay(low,1))",
    compute=_a020,
))


def _a022(p: PanelData) -> pd.Series:
    """-1 * (delta(correlation(high, volume, 5), 5) * rank(stddev(close, 20)))"""
    return -1.0 * (delta(correlation(p.high, p.volume, 5), 5) * rank(stddev(p.close, 20)))


register(AlphaSpec(
    name="alpha022", family=FAMILY, paper=_PAPER,
    description="Fade names where high-volume correlation just turned, weighted by recent vol",
    formula_text="-1 * (delta(correlation(high,volume,5),5) * rank(stddev(close,20)))",
    compute=_a022,
))


def _a024(p: PanelData) -> pd.Series:
    """((delta(sum(close,100)/100, 100)/delay(close,100)) <= 0.05) ? -1*(close-ts_min(close,100)) : -1*delta(close,3)"""
    sma100 = ts_sum(p.close, 100) / 100.0
    pct_change_sma = delta(sma100, 100) / delay(p.close, 100).replace(0, np.nan)
    long_term = -1.0 * (p.close - ts_min(p.close, 100))
    short_term = -1.0 * delta(p.close, 3)
    return long_term.where(pct_change_sma <= 0.05, short_term)


register(AlphaSpec(
    name="alpha024", family=FAMILY, paper=_PAPER,
    description="Regime switch on 100d SMA growth — fade either floor-distance or 3d momentum",
    formula_text="((delta(sum(close,100)/100,100)/delay(close,100)) <= 0.05) ? -1*(close-ts_min(close,100)) : -1*delta(close,3)",
    compute=_a024,
))


def _a025(p: PanelData) -> pd.Series:
    """rank((((-1 * returns) * (sum(volume, 20)/20)) * vwap) * (high - close))"""
    adv20 = ts_sum(p.volume, 20) / 20.0
    return rank(((-1.0 * p.returns) * adv20) * p.vwap * (p.high - p.close))


register(AlphaSpec(
    name="alpha025", family=FAMILY, paper=_PAPER,
    description="Negative-return × ADV × VWAP × upper-wick — fade upper-wick rejections on volume",
    formula_text="rank((((-1*returns) * (sum(volume,20)/20)) * vwap) * (high - close))",
    compute=_a025,
))


def _a017(p: PanelData) -> pd.Series:
    """(((-1 * rank(ts_rank(close,10))) * rank(delta(delta(close,1),1))) * rank(ts_rank((volume/adv20),5)))"""
    adv20 = ts_sum(p.volume, 20) / 20.0
    vol_ratio = p.volume / adv20.replace(0, np.nan)
    return ((-1.0 * rank(ts_rank(p.close, 10)))
            * rank(delta(delta(p.close, 1), 1))
            * rank(ts_rank(vol_ratio, 5)))


register(AlphaSpec(
    name="alpha017", family=FAMILY, paper=_PAPER,
    description="Triple-rank composite — recency × acceleration × relative volume rank",
    formula_text="(-1*rank(ts_rank(close,10))) * rank(delta(delta(close,1),1)) * rank(ts_rank(volume/adv20,5))",
    compute=_a017,
))


def _a023(p: PanelData) -> pd.Series:
    """((sum(high,20)/20 < high) ? (-1*delta(high,2)) : 0)"""
    sma20_high = ts_sum(p.high, 20) / 20.0
    fade = -1.0 * delta(p.high, 2)
    return fade.where(sma20_high < p.high, 0.0)


register(AlphaSpec(
    name="alpha023", family=FAMILY, paper=_PAPER,
    description="Fade 2d high change when today's high pierces 20d high SMA — breakout-fade",
    formula_text="(sum(high,20)/20 < high) ? -1*delta(high,2) : 0",
    compute=_a023,
))


def _a026(p: PanelData) -> pd.Series:
    """-1 * ts_max(correlation(ts_rank(volume,5), ts_rank(high,5), 5), 3)"""
    return -1.0 * ts_max(correlation(ts_rank(p.volume, 5), ts_rank(p.high, 5), 5), 3)


register(AlphaSpec(
    name="alpha026", family=FAMILY, paper=_PAPER,
    description="Negative recent peak of vol-high ts-rank correlation — sister to gtja005",
    formula_text="-1 * ts_max(correlation(ts_rank(volume,5), ts_rank(high,5), 5), 3)",
    compute=_a026,
))


def _a028(p: PanelData) -> pd.Series:
    """scale(((correlation(adv20,low,5) + ((high+low)/2)) - close))"""
    adv20 = ts_sum(p.volume, 20) / 20.0
    return scale(correlation(adv20, p.low, 5) + (p.high + p.low) / 2.0 - p.close)


register(AlphaSpec(
    name="alpha028", family=FAMILY, paper=_PAPER,
    description="Scaled (ADV-low correlation + midpoint) − close — flow-weighted reversion",
    formula_text="scale((correlation(adv20,low,5) + (high+low)/2) - close)",
    compute=_a028,
))


def _a030(p: PanelData) -> pd.Series:
    """(((1.0 - rank((sign(close-delay(close,1)) + sign(delay(close,1)-delay(close,2)) + sign(delay(close,2)-delay(close,3))))) * sum(volume,5)) / sum(volume,20))"""
    s1 = sign(p.close - delay(p.close, 1))
    s2 = sign(delay(p.close, 1) - delay(p.close, 2))
    s3 = sign(delay(p.close, 2) - delay(p.close, 3))
    return (1.0 - rank(s1 + s2 + s3)) * ts_sum(p.volume, 5) / ts_sum(p.volume, 20).replace(0, np.nan)


register(AlphaSpec(
    name="alpha030", family=FAMILY, paper=_PAPER,
    description="3-day directional consistency penalty × 5/20 volume ratio — fades sustained one-side runs",
    formula_text="(1 - rank(sum of 3 sign-of-deltas)) * sum(volume,5) / sum(volume,20)",
    compute=_a030,
))


def _a033(p: PanelData) -> pd.Series:
    """rank(-1 * (1 - open/close)^1)"""
    safe_close = p.close.replace(0, np.nan)
    return rank(-1.0 * (1.0 - p.open / safe_close))


register(AlphaSpec(
    name="alpha033", family=FAMILY, paper=_PAPER,
    description="Rank of (open/close - 1) — intraday-return rank, simple gap fade",
    formula_text="rank(-1 * (1 - open/close))",
    compute=_a033,
))


def _a034(p: PanelData) -> pd.Series:
    """rank(((1 - rank(stddev(returns,2)/stddev(returns,5))) + (1 - rank(delta(close,1)))))"""
    vol_ratio = stddev(p.returns, 2) / stddev(p.returns, 5).replace(0, np.nan)
    return rank((1.0 - rank(vol_ratio)) + (1.0 - rank(delta(p.close, 1))))


register(AlphaSpec(
    name="alpha034", family=FAMILY, paper=_PAPER,
    description="Combine low-recent-vol rank and low-recent-delta rank — quiet stock reversion",
    formula_text="rank((1 - rank(stddev(returns,2)/stddev(returns,5))) + (1 - rank(delta(close,1))))",
    compute=_a034,
))


def _a035(p: PanelData) -> pd.Series:
    """(ts_rank(volume,32) * (1 - ts_rank((close+high-low),16))) * (1 - ts_rank(returns,32))"""
    return (ts_rank(p.volume, 32)
            * (1.0 - ts_rank(p.close + p.high - p.low, 16))
            * (1.0 - ts_rank(p.returns, 32)))


register(AlphaSpec(
    name="alpha035", family=FAMILY, paper=_PAPER,
    description="High recent volume × low recent price-range × low recent return — accumulation gauge",
    formula_text="ts_rank(volume,32) * (1 - ts_rank(close+high-low,16)) * (1 - ts_rank(returns,32))",
    compute=_a035,
))


def _a040(p: PanelData) -> pd.Series:
    """(-1 * rank(stddev(high,10))) * correlation(high,volume,10)"""
    return (-1.0 * rank(stddev(p.high, 10))) * correlation(p.high, p.volume, 10)


register(AlphaSpec(
    name="alpha040", family=FAMILY, paper=_PAPER,
    description="Penalise vol-of-high names × high-volume corr — sister to gtja042",
    formula_text="(-1*rank(stddev(high,10))) * correlation(high,volume,10)",
    compute=_a040,
))


def _a041(p: PanelData) -> pd.Series:
    """((high * low)^0.5) - vwap"""
    return np.sqrt(p.high * p.low) - p.vwap


register(AlphaSpec(
    name="alpha041", family=FAMILY, paper=_PAPER,
    description="Geometric mean of high+low minus VWAP — sister to gtja013",
    formula_text="sqrt(high*low) - vwap",
    compute=_a041,
))


def _a042(p: PanelData) -> pd.Series:
    """rank(vwap - close) / rank(vwap + close)"""
    return rank(p.vwap - p.close) / rank(p.vwap + p.close).replace(0, np.nan)


register(AlphaSpec(
    name="alpha042", family=FAMILY, paper=_PAPER,
    description="VWAP-close gap rank divided by VWAP+close rank — close-out skew",
    formula_text="rank(vwap-close) / rank(vwap+close)",
    compute=_a042,
))


def _a043(p: PanelData) -> pd.Series:
    """ts_rank(volume/adv20, 20) * ts_rank((-1*delta(close,7)), 8)"""
    adv20 = ts_sum(p.volume, 20) / 20.0
    return ts_rank(p.volume / adv20.replace(0, np.nan), 20) * ts_rank(-1.0 * delta(p.close, 7), 8)


register(AlphaSpec(
    name="alpha043", family=FAMILY, paper=_PAPER,
    description="High recent volume × reversed 7d close change — high-volume reversal",
    formula_text="ts_rank(volume/adv20, 20) * ts_rank(-1*delta(close,7), 8)",
    compute=_a043,
))


def _a044(p: PanelData) -> pd.Series:
    """-1 * correlation(high, rank(volume), 5)"""
    return -1.0 * correlation(p.high, rank(p.volume), 5)


register(AlphaSpec(
    name="alpha044", family=FAMILY, paper=_PAPER,
    description="Negative 5d correlation of price highs with volume rank — fades crowd-on-highs",
    formula_text="-1 * correlation(high, rank(volume), 5)",
    compute=_a044,
))


def _a045(p: PanelData) -> pd.Series:
    """-1 * (rank(sum(delay(close,5),20)/20) * correlation(close, volume, 2)
              * rank(correlation(sum(close,5), sum(close,20), 2)))"""
    delayed_sma = ts_sum(delay(p.close, 5), 20) / 20.0
    return -1.0 * (rank(delayed_sma)
                   * correlation(p.close, p.volume, 2)
                   * rank(correlation(ts_sum(p.close, 5), ts_sum(p.close, 20), 2)))


register(AlphaSpec(
    name="alpha045", family=FAMILY, paper=_PAPER,
    description="Triple combo: delayed-SMA rank × short corr × MA-MA corr — penalises trend-aligned crowding",
    formula_text="-1 * (rank(sum(delay(close,5),20)/20) * correlation(close,volume,2) * rank(correlation(sum(close,5),sum(close,20),2)))",
    compute=_a045,
))


def _a049(p: PanelData) -> pd.Series:
    """(((delay(close,20) - delay(close,10)) / 10 - (delay(close,10) - close)/10) < -0.1) ? 1 : -1*(close-delay(close,1))"""
    slope_long = (delay(p.close, 20) - delay(p.close, 10)) / 10.0
    slope_short = (delay(p.close, 10) - p.close) / 10.0
    cond = (slope_long - slope_short) < -0.1
    return pd.Series(1.0, index=p.close.index).where(cond, -1.0 * delta(p.close, 1))


register(AlphaSpec(
    name="alpha049", family=FAMILY, paper=_PAPER,
    description="Long-flat when 20-vs-10 slope reversal sharp; else fade 1d delta — regime switch",
    formula_text="(slope_diff < -0.1) ? 1 : -delta(close,1)",
    compute=_a049,
))


def _a050(p: PanelData) -> pd.Series:
    """-1 * ts_max(rank(correlation(rank(volume), rank(vwap), 5)), 5)"""
    return -1.0 * ts_max(rank(correlation(rank(p.volume), rank(p.vwap), 5)), 5)


register(AlphaSpec(
    name="alpha050", family=FAMILY, paper=_PAPER,
    description="Negative recent peak of vol-vwap rank correlation — fades crowded VWAP names",
    formula_text="-1 * ts_max(rank(correlation(rank(volume), rank(vwap), 5)), 5)",
    compute=_a050,
))


def _a052(p: PanelData) -> pd.Series:
    """(((-1 * ts_min(low,5)) + delay(ts_min(low,5),5)) * rank((sum(returns,240)-sum(returns,20))/220)) * ts_rank(volume,5)"""
    tmin5 = ts_min(p.low, 5)
    long_ret = (ts_sum(p.returns, 240) - ts_sum(p.returns, 20)) / 220.0
    return ((-1.0 * tmin5 + delay(tmin5, 5)) * rank(long_ret)) * ts_rank(p.volume, 5)


register(AlphaSpec(
    name="alpha052", family=FAMILY, paper=_PAPER,
    description="Floor-shift × long-term return rank × recent volume — multi-horizon composite",
    formula_text="((-ts_min(low,5) + delay(ts_min(low,5),5)) * rank((sum(returns,240)-sum(returns,20))/220)) * ts_rank(volume,5)",
    compute=_a052,
))


def _a053(p: PanelData) -> pd.Series:
    """-1 * delta(((close-low)-(high-close))/(close-low), 9)"""
    spread = (p.close - p.low).replace(0, np.nan)
    moneyflow = ((p.close - p.low) - (p.high - p.close)) / spread
    return -1.0 * delta(moneyflow, 9)


register(AlphaSpec(
    name="alpha053", family=FAMILY, paper=_PAPER,
    description="Negative 9d change in close-relative money-flow — medium-horizon reversal",
    formula_text="-1 * delta(((close-low)-(high-close))/(close-low), 9)",
    compute=_a053,
))


def _a054(p: PanelData) -> pd.Series:
    """((-1 * (low - close) * open^5) / ((low - high) * close^5))"""
    return ((-1.0 * (p.low - p.close) * np.power(p.open, 5))
            / ((p.low - p.high).replace(0, np.nan) * np.power(p.close, 5)))


register(AlphaSpec(
    name="alpha054", family=FAMILY, paper=_PAPER,
    description="(close-low) × (open/close)^5 inverted on range — non-linear close-position indicator",
    formula_text="-(low-close) * open^5 / ((low-high) * close^5)",
    compute=_a054,
))


def _a055(p: PanelData) -> pd.Series:
    """-1 * correlation(rank((close - ts_min(low,12)) / (ts_max(high,12) - ts_min(low,12))), rank(volume), 6)"""
    rng = (ts_max(p.high, 12) - ts_min(p.low, 12)).replace(0, np.nan)
    raw_rsv = (p.close - ts_min(p.low, 12)) / rng
    return -1.0 * correlation(rank(raw_rsv), rank(p.volume), 6)


register(AlphaSpec(
    name="alpha055", family=FAMILY, paper=_PAPER,
    description="Negative correlation of stochastic %K rank with volume rank — fades %K-based crowd",
    formula_text="-1 * correlation(rank(rsv_12), rank(volume), 6)",
    compute=_a055,
))
