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
    rank, ts_argmax, ts_argmin, ts_max, ts_min, ts_sum, ts_rank, ts_mean,
    delta, delay, correlation, covariance, decay_linear, stddev,
    signedpower, scale, log, sign, abs_, product,
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


# ----- v1.3.5 batch: +28 ports without IndNeutralize / cap deps ------------

def _a021(p: PanelData) -> pd.Series:
    """((sum(close,8)/8+stddev(close,8)<sum(close,2)/2) ? -1 :
       ((sum(close,2)/2<sum(close,8)/8-stddev(close,8)) ? 1 :
       ((1<volume/adv20) ? 1 : -1)))"""
    sma8 = ts_sum(p.close, 8) / 8.0
    sma2 = ts_sum(p.close, 2) / 2.0
    s8 = stddev(p.close, 8)
    adv20 = ts_sum(p.volume, 20) / 20.0
    bearish = sma8 + s8 < sma2
    bullish = sma2 < sma8 - s8
    vol_high = p.volume / adv20.replace(0, np.nan) > 1.0
    default = vol_high.astype(float) * 2 - 1
    out = default.copy()
    out = out.where(~bullish, 1.0)
    out = out.where(~bearish, -1.0)
    return out


register(AlphaSpec(
    name="alpha021", family=FAMILY, paper=_PAPER,
    description="Three-state regime switch on 8d MA ± stddev vs 2d MA, with volume gate",
    formula_text="(sma8+std8<sma2 ? -1 : (sma2<sma8-std8 ? 1 : (1<vol/adv20 ? 1 : -1)))",
    compute=_a021,
))


def _a027(p: PanelData) -> pd.Series:
    """(0.5 < rank(sum(correlation(rank(volume), rank(vwap), 6), 2)/2.0)) ? -1 : 1"""
    corr = correlation(rank(p.volume), rank(p.vwap), 6)
    half_sum = ts_sum(corr, 2) / 2.0
    return pd.Series(1.0, index=p.close.index).where(rank(half_sum) <= 0.5, -1.0)


register(AlphaSpec(
    name="alpha027", family=FAMILY, paper=_PAPER,
    description="Bear regime when 2d-avg rank-corr(volume,vwap) ranks above median — fades vol-vwap consensus",
    formula_text="(0.5 < rank(sum(correlation(rank(volume),rank(vwap),6),2)/2)) ? -1 : 1",
    compute=_a027,
))


def _a029(p: PanelData) -> pd.Series:
    """(ts_min(product(rank(scale(log(sum(ts_min(rank(rank((-1*rank(delta(close-1,5))))),2),1)))),5))) + ts_rank(delay((-1*returns),6),5)"""
    inner = -1.0 * rank(delta(p.close - 1, 5))
    inner = rank(rank(inner))
    inner = ts_min(inner, 2)
    inner = ts_sum(inner, 1)
    inner = log(inner.where(inner > 0, np.nan))
    inner = scale(inner)
    inner = rank(inner)
    inner = product(inner.fillna(1.0), 5)
    inner = ts_min(inner, 5)
    return inner + ts_rank(delay(-1.0 * p.returns, 6), 5)


register(AlphaSpec(
    name="alpha029", family=FAMILY, paper=_PAPER,
    description="Deeply nested rank composite + 6d-delayed reversed-return rank — multi-stage smoothing",
    formula_text="(complex nested ranks — see paper)",
    compute=_a029,
))


def _a031(p: PanelData) -> pd.Series:
    """(rank(rank(rank(decay_linear((-1*rank(rank(delta(close,10)))),10))))
        + rank((-1*delta(close,3))))
        + sign(scale(correlation(ts_sum(p.volume,20)/20, low, 12)))"""
    adv20 = ts_sum(p.volume, 20) / 20.0
    part1 = rank(rank(rank(decay_linear(-1.0 * rank(rank(delta(p.close, 10))), 10))))
    part2 = rank(-1.0 * delta(p.close, 3))
    part3 = sign(scale(correlation(adv20, p.low, 12)))
    return part1 + part2 + part3


register(AlphaSpec(
    name="alpha031", family=FAMILY, paper=_PAPER,
    description="Triple-stacked rank composite with adv20-low corr sign — multi-stage reversal+correlation",
    formula_text="rank(rank(rank(decay_linear(...,10)))) + rank(-delta(close,3)) + sign(scale(correlation(adv20,low,12)))",
    compute=_a031,
))


def _a032(p: PanelData) -> pd.Series:
    """scale((ts_sum(close,7)/7 - close)) + 20*scale(correlation(vwap, delay(close,5), 230))"""
    sma_dev = ts_sum(p.close, 7) / 7.0 - p.close
    long_corr = correlation(p.vwap, delay(p.close, 5), 230)
    return scale(sma_dev) + 20.0 * scale(long_corr)


register(AlphaSpec(
    name="alpha032", family=FAMILY, paper=_PAPER,
    description="Mean-reversion score plus 230d VWAP-delayed-close correlation, equally scaled",
    formula_text="scale(sum(close,7)/7 - close) + 20*scale(correlation(vwap, delay(close,5), 230))",
    compute=_a032,
))


def _a036(p: PanelData) -> pd.Series:
    """(2.21*rank(correlation((close-open),delay(volume,1),15))
        +0.7*rank((open-close))
        +0.73*rank(ts_rank(delay((-1*returns),6),5))
        +rank(abs(correlation(vwap,ts_sum(p.volume,20)/20,6)))
        +0.6*rank((((sum(close,200)/200) - open) * (close - open))))"""
    adv20 = ts_sum(p.volume, 20) / 20.0
    t1 = 2.21 * rank(correlation(p.close - p.open, delay(p.volume, 1), 15))
    t2 = 0.7 * rank(p.open - p.close)
    t3 = 0.73 * rank(ts_rank(delay(-1.0 * p.returns, 6), 5))
    t4 = rank(correlation(p.vwap, adv20, 6).abs())
    long_ma = ts_sum(p.close, 200) / 200.0
    t5 = 0.6 * rank((long_ma - p.open) * (p.close - p.open))
    return t1 + t2 + t3 + t4 + t5


register(AlphaSpec(
    name="alpha036", family=FAMILY, paper=_PAPER,
    description="Weighted 5-term composite: candle-volume corr × OC reversal × long-MA gap",
    formula_text="weighted sum of 5 rank terms (see paper)",
    compute=_a036,
))


def _a037(p: PanelData) -> pd.Series:
    """rank(correlation(delay((open-close), 1), close, 200)) + rank((open-close))"""
    return rank(correlation(delay(p.open - p.close, 1), p.close, 200)) + rank(p.open - p.close)


register(AlphaSpec(
    name="alpha037", family=FAMILY, paper=_PAPER,
    description="200d correlation of delayed candle-body with close + intraday rank — long-term + intraday",
    formula_text="rank(correlation(delay(open-close,1), close, 200)) + rank(open-close)",
    compute=_a037,
))


def _a038(p: PanelData) -> pd.Series:
    """-1 * rank(ts_rank(close,10)) * rank(close/open)"""
    return -1.0 * rank(ts_rank(p.close, 10)) * rank(p.close / p.open.replace(0, np.nan))


register(AlphaSpec(
    name="alpha038", family=FAMILY, paper=_PAPER,
    description="Penalise recent-strong + above-open names — short-horizon trend fade",
    formula_text="-1 * rank(ts_rank(close,10)) * rank(close/open)",
    compute=_a038,
))


def _a039(p: PanelData) -> pd.Series:
    """-1*rank(delta(close,7) * (1-rank(decay_linear(volume/adv20,9)))) * (1+rank(sum(returns,250)))"""
    adv20 = ts_sum(p.volume, 20) / 20.0
    inner = delta(p.close, 7) * (1.0 - rank(decay_linear(p.volume / adv20.replace(0, np.nan), 9)))
    long_term = 1.0 + rank(ts_sum(p.returns, 250))
    return (-1.0 * rank(inner)) * long_term


register(AlphaSpec(
    name="alpha039", family=FAMILY, paper=_PAPER,
    description="7d momentum × volume-decay × 250d return rank — multi-horizon reversal (sister to gtja025)",
    formula_text="-rank(delta(close,7) * (1-rank(decay_linear(volume/adv20,9)))) * (1+rank(sum(returns,250)))",
    compute=_a039,
))


def _a046(p: PanelData) -> pd.Series:
    """(0.25 < ((delay(close,20)-delay(close,10))/10 - (delay(close,10)-close)/10))
       ? -1 : ((((delay(close,20)-delay(close,10))/10 - (delay(close,10)-close)/10) < 0)
               ? 1 : -1*(close-delay(close,1)))"""
    slope1 = (delay(p.close, 20) - delay(p.close, 10)) / 10.0
    slope2 = (delay(p.close, 10) - p.close) / 10.0
    diff = slope1 - slope2
    out = -1.0 * (p.close - delay(p.close, 1))
    out = out.where(diff >= 0, 1.0)
    out = out.where(diff <= 0.25, -1.0)
    return out


register(AlphaSpec(
    name="alpha046", family=FAMILY, paper=_PAPER,
    description="Three-state regime: strong-rev / weak-rev / fade-1d-delta based on 10/20d slope diff",
    formula_text="(slope_diff > 0.25 ? -1 : (slope_diff < 0 ? 1 : -delta(close,1)))",
    compute=_a046,
))


def _a047(p: PanelData) -> pd.Series:
    """((rank(1/close) * volume) / (ts_sum(p.volume,20)/20)) * (high*rank(high-close) / (ts_sum(high,5)/5))
       - rank(vwap - delay(vwap, 5))"""
    adv20 = ts_sum(p.volume, 20) / 20.0
    h_avg5 = ts_sum(p.high, 5) / 5.0
    inv_close = rank(1.0 / p.close.replace(0, np.nan))
    return ((inv_close * p.volume) / adv20.replace(0, np.nan)
            * (p.high * rank(p.high - p.close) / h_avg5.replace(0, np.nan))
            - rank(p.vwap - delay(p.vwap, 5)))


register(AlphaSpec(
    name="alpha047", family=FAMILY, paper=_PAPER,
    description="Inverse-price weighted volume × upper-wick rank divided by recent-high MA, minus 5d VWAP change rank",
    formula_text="((rank(1/close) * volume) / adv20) * (high*rank(high-close) / mean(high,5)) - rank(vwap-delay(vwap,5))",
    compute=_a047,
))


def _a051(p: PanelData) -> pd.Series:
    """((-1*0.05) < (delay(close,20)-delay(close,10))/10 - (delay(close,10)-close)/10)
       ? -1*(close-delay(close,1)) : 1"""
    slope1 = (delay(p.close, 20) - delay(p.close, 10)) / 10.0
    slope2 = (delay(p.close, 10) - p.close) / 10.0
    diff = slope1 - slope2
    out = pd.Series(1.0, index=p.close.index)
    out = out.where(diff <= -0.05, -1.0 * (p.close - delay(p.close, 1)))
    return out


register(AlphaSpec(
    name="alpha051", family=FAMILY, paper=_PAPER,
    description="If 10/20d slope diff > -0.05 then fade 1d delta else long-flat — sister to #46",
    formula_text="(slope_diff > -0.05 ? -delta(close,1) : 1)",
    compute=_a051,
))


def _a057(p: PanelData) -> pd.Series:
    """(0 - 1*((close - vwap) / decay_linear(rank(ts_argmax(close, 30)), 2)))"""
    return -1.0 * (p.close - p.vwap) / decay_linear(rank(ts_argmax(p.close, 30)), 2)


register(AlphaSpec(
    name="alpha057", family=FAMILY, paper=_PAPER,
    description="Close-VWAP gap divided by smoothed argmax rank — reversal weighted by recent extreme",
    formula_text="-1 * (close - vwap) / decay_linear(rank(ts_argmax(close,30)), 2)",
    compute=_a057,
))


def _a060(p: PanelData) -> pd.Series:
    """0 - 1*((2 * scale(rank(((((close-low)-(high-close))/(high-low)) * volume))))
              - scale(rank(ts_argmax(close, 10))))"""
    rng = (p.high - p.low).replace(0, np.nan)
    mfm = ((p.close - p.low) - (p.high - p.close)) / rng * p.volume
    return -1.0 * (2.0 * scale(rank(mfm)) - scale(rank(ts_argmax(p.close, 10))))


register(AlphaSpec(
    name="alpha060", family=FAMILY, paper=_PAPER,
    description="Money-flow-position rank scaled, minus 10d argmax rank — fades MFI extremes",
    formula_text="-(2*scale(rank((close-low)-(high-close))/(high-low)*volume) - scale(rank(ts_argmax(close,10))))",
    compute=_a060,
))


def _a061(p: PanelData) -> pd.Series:
    """rank((vwap - ts_min(vwap, 16))) < rank(correlation(vwap, ts_sum(p.volume,180)/180, 18))"""
    adv180 = ts_sum(p.volume, 180) / 180.0
    return (rank(p.vwap - ts_min(p.vwap, 16)) < rank(correlation(p.vwap, adv180, 18))).astype(float)


register(AlphaSpec(
    name="alpha061", family=FAMILY, paper=_PAPER,
    description="Boolean: VWAP-floor proximity rank vs long-ADV-VWAP correlation rank — relative-strength gate",
    formula_text="rank((vwap - ts_min(vwap,16))) < rank(correlation(vwap, adv180, 18))",
    compute=_a061,
))


def _a062(p: PanelData) -> pd.Series:
    """(rank(correlation(vwap, sum(adv20,22), 10)) < rank(((rank(open) + rank(open)) <
       (rank(((high+low)/2)) + rank(high))))) * -1"""
    adv20 = ts_sum(p.volume, 20) / 20.0
    adv_sum = ts_sum(adv20, 22)
    c_corr = rank(correlation(p.vwap, adv_sum, 10))
    twice_o = rank(p.open) * 2
    mid = rank((p.high + p.low) / 2.0) + rank(p.high)
    return -1.0 * (c_corr < rank(twice_o < mid)).astype(float)


register(AlphaSpec(
    name="alpha062", family=FAMILY, paper=_PAPER,
    description="Composite boolean: VWAP-ADVsum corr rank vs open-vs-midpoint rank — penalty product",
    formula_text="(rank(correlation(vwap,sum(adv20,22),10)) < rank(2*rank(open) < rank((high+low)/2)+rank(high))) * -1",
    compute=_a062,
))


def _a064(p: PanelData) -> pd.Series:
    """(rank(correlation(sum(((open*0.178404)+(low*0.821596)),13), sum(adv120,13), 17))
        < rank(delta(((((high+low)/2)*0.178404)+(vwap*0.821596)), 3.69741))) * -1"""
    adv120 = ts_sum(p.volume, 120) / 120.0
    blend1 = p.open * 0.178404 + p.low * 0.821596
    blend2 = (p.high + p.low) / 2.0 * 0.178404 + p.vwap * 0.821596
    c_corr = rank(correlation(ts_sum(blend1, 13), ts_sum(adv120, 13), 17))
    c_delta = rank(delta(blend2, 4))
    return -1.0 * (c_corr < c_delta).astype(float)


register(AlphaSpec(
    name="alpha064", family=FAMILY, paper=_PAPER,
    description="Long-window blend correlation vs midpoint-VWAP-blend delta rank — boolean penalty",
    formula_text="(rank(correlation(...long blends, 17)) < rank(delta(...midpoint-vwap blend, 4))) * -1",
    compute=_a064,
))


def _a065(p: PanelData) -> pd.Series:
    """(rank(correlation(((open*0.00817205)+(vwap*0.99182795)), sum(adv60,9), 6))
        < rank(open - ts_min(open, 14))) * -1"""
    adv60 = ts_sum(p.volume, 60) / 60.0
    blend = p.open * 0.00817205 + p.vwap * 0.99182795
    c_corr = rank(correlation(blend, ts_sum(adv60, 9), 6))
    c_floor = rank(p.open - ts_min(p.open, 14))
    return -1.0 * (c_corr < c_floor).astype(float)


register(AlphaSpec(
    name="alpha065", family=FAMILY, paper=_PAPER,
    description="VWAP-heavy blend × ADV-60 corr vs open-floor distance — boolean penalty",
    formula_text="(rank(correlation((open*0.008+vwap*0.992), sum(adv60,9), 6)) < rank(open - ts_min(open,14))) * -1",
    compute=_a065,
))


def _a066(p: PanelData) -> pd.Series:
    """(rank(decay_linear(delta(vwap, 4), 7))
        + ts_rank(decay_linear(((((low*0.96633)+(low*0.03367)) - vwap)
                                / (open - ((high+low)/2))), 11), 7)) * -1"""
    inner1 = delta(p.vwap, 4)
    t1 = rank(decay_linear(inner1, 7))
    spread = (p.low - p.vwap) / (p.open - (p.high + p.low) / 2.0).replace(0, np.nan)
    t2 = ts_rank(decay_linear(spread, 11), 7)
    return -1.0 * (t1 + t2)


register(AlphaSpec(
    name="alpha066", family=FAMILY, paper=_PAPER,
    description="Penalise smoothed-VWAP-momentum + decayed low-VWAP gap over open-mid gap",
    formula_text="-(rank(decay_linear(delta(vwap,4),7)) + ts_rank(decay_linear((low-vwap)/(open-mid),11),7))",
    compute=_a066,
))


def _a068(p: PanelData) -> pd.Series:
    """(ts_rank(correlation(rank(high), rank(ts_sum(p.volume,15)/15), 9), 14)
        < rank(delta(((close*0.518371)+(low*0.481629)), 1.06157))) * -1"""
    adv15 = ts_sum(p.volume, 15) / 15.0
    blend = p.close * 0.518371 + p.low * 0.481629
    c_corr = ts_rank(correlation(rank(p.high), rank(adv15), 9), 14)
    c_delta = rank(delta(blend, 1))
    return -1.0 * (c_corr < c_delta).astype(float)


register(AlphaSpec(
    name="alpha068", family=FAMILY, paper=_PAPER,
    description="14d ts-rank of high-volume corr vs blend-delta rank — boolean penalty",
    formula_text="(ts_rank(correlation(rank(high),rank(adv15),9),14) < rank(delta(blend,1))) * -1",
    compute=_a068,
))


def _a072(p: PanelData) -> pd.Series:
    """rank(decay_linear(correlation(((high+low)/2), sum(adv40,9), 9), 10))
       / rank(decay_linear(correlation(ts_rank(vwap, 4), ts_rank(volume, 19), 7), 3))"""
    adv40 = ts_sum(p.volume, 40) / 40.0
    mid = (p.high + p.low) / 2.0
    num = rank(decay_linear(correlation(mid, ts_sum(adv40, 9), 9), 10))
    den = rank(decay_linear(correlation(ts_rank(p.vwap, 4), ts_rank(p.volume, 19), 7), 3))
    return num / den.replace(0, np.nan)


register(AlphaSpec(
    name="alpha072", family=FAMILY, paper=_PAPER,
    description="Ratio of two decayed correlations — midpoint-ADV vs VWAP-volume ts-rank",
    formula_text="rank(decay_linear(correlation((high+low)/2, sum(adv40,9), 9), 10)) / rank(decay_linear(correlation(ts_rank(vwap,4), ts_rank(volume,19), 7), 3))",
    compute=_a072,
))


def _a074(p: PanelData) -> pd.Series:
    """(rank(correlation(close, sum(adv30,37), 15))
        < rank(correlation(rank(((high*0.0261661)+(vwap*0.9738339))), rank(volume), 11))) * -1"""
    adv30 = ts_sum(p.volume, 30) / 30.0
    blend = p.high * 0.0261661 + p.vwap * 0.9738339
    c1 = rank(correlation(p.close, ts_sum(adv30, 37), 15))
    c2 = rank(correlation(rank(blend), rank(p.volume), 11))
    return -1.0 * (c1 < c2).astype(float)


register(AlphaSpec(
    name="alpha074", family=FAMILY, paper=_PAPER,
    description="Close-ADV-sum corr rank vs blend-volume rank-corr rank — boolean penalty",
    formula_text="(rank(correlation(close, sum(adv30,37), 15)) < rank(correlation(rank(blend), rank(volume), 11))) * -1",
    compute=_a074,
))


def _a075(p: PanelData) -> pd.Series:
    """rank(correlation(vwap, volume, 4)) < rank(correlation(rank(low), rank(ts_sum(p.volume,50)/50), 12))"""
    adv50 = ts_sum(p.volume, 50) / 50.0
    return (rank(correlation(p.vwap, p.volume, 4))
            < rank(correlation(rank(p.low), rank(adv50), 12))).astype(float)


register(AlphaSpec(
    name="alpha075", family=FAMILY, paper=_PAPER,
    description="Short VWAP-vol corr rank vs long low-ADV50 rank-corr rank — boolean indicator",
    formula_text="rank(correlation(vwap,volume,4)) < rank(correlation(rank(low),rank(adv50),12))",
    compute=_a075,
))


def _a077(p: PanelData) -> pd.Series:
    """min(rank(decay_linear(((high+low)/2+high-(vwap+high)), 20)),
           rank(decay_linear(correlation((high+low)/2, sum(adv40,3), 6), 6)))"""
    adv40 = ts_sum(p.volume, 40) / 40.0
    mid = (p.high + p.low) / 2.0
    t1 = rank(decay_linear(mid + p.high - p.vwap - p.high, 20))
    t2 = rank(decay_linear(correlation(mid, ts_sum(adv40, 3), 6), 6))
    return np.minimum(t1, t2)


register(AlphaSpec(
    name="alpha077", family=FAMILY, paper=_PAPER,
    description="Min of two decayed ranks: midpoint-VWAP spread vs mid-ADV40 correlation",
    formula_text="min(rank(decay_linear(((high+low)/2 - vwap), 20)), rank(decay_linear(correlation(mid, sum(adv40,3), 6), 6)))",
    compute=_a077,
))


def _a078(p: PanelData) -> pd.Series:
    """rank(correlation(sum(((low*0.352233)+(vwap*0.647767)),20), sum(adv40,20), 7))
       ^ rank(correlation(rank(vwap), rank(volume), 6))"""
    adv40 = ts_sum(p.volume, 40) / 40.0
    blend = p.low * 0.352233 + p.vwap * 0.647767
    base = rank(correlation(ts_sum(blend, 20), ts_sum(adv40, 20), 7))
    expo = rank(correlation(rank(p.vwap), rank(p.volume), 6))
    return np.power(base.clip(lower=1e-6), expo.clip(lower=-4, upper=4))


register(AlphaSpec(
    name="alpha078", family=FAMILY, paper=_PAPER,
    description="Long blend-ADV correlation rank exponentiated by VWAP-vol rank-corr rank",
    formula_text="rank(correlation(sum(blend,20), sum(adv40,20), 7)) ^ rank(correlation(rank(vwap), rank(volume), 6))",
    compute=_a078,
))


def _a081(p: PanelData) -> pd.Series:
    """(rank(log(product(rank(rank(correlation(vwap, sum(adv10,49), 8))^4), 14)))
        < rank(correlation(rank(vwap), rank(volume), 5))) * -1"""
    adv10 = ts_sum(p.volume, 10) / 10.0
    inner_corr = correlation(p.vwap, ts_sum(adv10, 49), 8)
    inner = np.power(rank(rank(inner_corr)).clip(lower=1e-6), 4)
    base = rank(log(product(inner.fillna(1.0), 14)))
    other = rank(correlation(rank(p.vwap), rank(p.volume), 5))
    return -1.0 * (base < other).astype(float)


register(AlphaSpec(
    name="alpha081", family=FAMILY, paper=_PAPER,
    description="Log-product of quartic ranks vs simple rank-corr — boolean penalty",
    formula_text="(rank(log(product(rank(rank(correlation(vwap,sum(adv10,49),8)))^4, 14))) < rank(correlation(rank(vwap),rank(volume),5))) * -1",
    compute=_a081,
))


def _a083(p: PanelData) -> pd.Series:
    """(rank(delay((high-low)/(sum(close,5)/5), 2)) * rank(rank(volume)))
       / ((high-low)/(sum(close,5)/5)) / (vwap - close)"""
    spread = (p.high - p.low) / (ts_sum(p.close, 5) / 5.0).replace(0, np.nan)
    num = rank(delay(spread, 2)) * rank(rank(p.volume))
    return num / spread.replace(0, np.nan) / (p.vwap - p.close).replace(0, np.nan)


register(AlphaSpec(
    name="alpha083", family=FAMILY, paper=_PAPER,
    description="Delayed range-spread rank × volume-rank-rank divided by current spread and VWAP-close gap",
    formula_text="(rank(delay((high-low)/(sum(close,5)/5), 2)) * rank(rank(volume))) / spread / (vwap - close)",
    compute=_a083,
))


def _a084(p: PanelData) -> pd.Series:
    """sign((ts_rank(vwap - ts_max(vwap, 15), 21)))^(delta(close, 5))"""
    base = sign(ts_rank(p.vwap - ts_max(p.vwap, 15), 21))
    expo = delta(p.close, 5)
    return np.power(base, expo.clip(lower=-4, upper=4))


register(AlphaSpec(
    name="alpha084", family=FAMILY, paper=_PAPER,
    description="Signed VWAP-exhaustion ts-rank exponentiated by 5d close delta",
    formula_text="sign(ts_rank(vwap - ts_max(vwap,15), 21)) ^ delta(close, 5)",
    compute=_a084,
))


def _a085(p: PanelData) -> pd.Series:
    """rank(correlation(((high*0.876703)+(close*0.123297)), ts_sum(p.volume,30)/30, 9))
       ^ rank(correlation(ts_rank((high+low)/2, 4), ts_rank(volume, 10), 7))"""
    adv30 = ts_sum(p.volume, 30) / 30.0
    blend = p.high * 0.876703 + p.close * 0.123297
    base = rank(correlation(blend, adv30, 9))
    expo = rank(correlation(ts_rank((p.high + p.low) / 2.0, 4), ts_rank(p.volume, 10), 7))
    return np.power(base.clip(lower=1e-6), expo.clip(lower=-4, upper=4))


register(AlphaSpec(
    name="alpha085", family=FAMILY, paper=_PAPER,
    description="High-heavy-blend × ADV30 correlation rank exponentiated by midpoint-volume ts-rank corr",
    formula_text="rank(correlation(blend, adv30, 9)) ^ rank(correlation(ts_rank((high+low)/2,4), ts_rank(volume,10), 7))",
    compute=_a085,
))


def _a086(p: PanelData) -> pd.Series:
    """(ts_rank(correlation(close, sum(ts_sum(p.volume,20)/20,15), 6), 20)
        < rank(((open + close) - (vwap + open)))) * -1"""
    adv20 = ts_sum(p.volume, 20) / 20.0
    c1 = ts_rank(correlation(p.close, ts_sum(adv20, 15), 6), 20)
    c2 = rank((p.open + p.close) - (p.vwap + p.open))
    return -1.0 * (c1 < c2).astype(float)


register(AlphaSpec(
    name="alpha086", family=FAMILY, paper=_PAPER,
    description="ts-ranked close-ADVsum corr vs open-close-vwap rank — boolean penalty",
    formula_text="(ts_rank(correlation(close, sum(adv20,15), 6), 20) < rank((open+close)-(vwap+open))) * -1",
    compute=_a086,
))


def _a088(p: PanelData) -> pd.Series:
    """min(rank(decay_linear((rank(open)+rank(low)-rank(high)-rank(close)), 8)),
           ts_rank(decay_linear(correlation(ts_rank(close,8), ts_rank(ts_sum(p.volume,60)/60,21), 8), 7), 3))"""
    adv60 = ts_sum(p.volume, 60) / 60.0
    t1 = rank(decay_linear(rank(p.open) + rank(p.low) - rank(p.high) - rank(p.close), 8))
    t2 = ts_rank(decay_linear(correlation(ts_rank(p.close, 8), ts_rank(adv60, 21), 8), 7), 3)
    return np.minimum(t1, t2)


register(AlphaSpec(
    name="alpha088", family=FAMILY, paper=_PAPER,
    description="Min of OL-HC rank-spread decay and close-ADV60 ts-rank-corr decay",
    formula_text="min(rank(decay_linear(rank(open)+rank(low)-rank(high)-rank(close), 8)), ts_rank(decay_linear(correlation(ts_rank(close,8), ts_rank(adv60,21), 8), 7), 3))",
    compute=_a088,
))


def _a092(p: PanelData) -> pd.Series:
    """min(ts_rank(decay_linear(((((high+low)/2)+close) < (low+open)), 15), 19),
           ts_rank(decay_linear(correlation(rank(low), rank(ts_sum(p.volume,30)/30), 8), 7), 7))"""
    adv30 = ts_sum(p.volume, 30) / 30.0
    cond = (((p.high + p.low) / 2.0 + p.close) < (p.low + p.open)).astype(float)
    t1 = ts_rank(decay_linear(cond, 15), 19)
    t2 = ts_rank(decay_linear(correlation(rank(p.low), rank(adv30), 8), 7), 7)
    return np.minimum(t1, t2)


register(AlphaSpec(
    name="alpha092", family=FAMILY, paper=_PAPER,
    description="Min of inverted-candle-pattern frequency decay and low-ADV30 rank-corr decay",
    formula_text="min(ts_rank(decay_linear(((high+low)/2+close < low+open), 15), 19), ts_rank(decay_linear(correlation(rank(low), rank(adv30), 8), 7), 7))",
    compute=_a092,
))


def _a094(p: PanelData) -> pd.Series:
    """(rank((vwap - ts_min(vwap, 12)))^ts_rank(correlation(ts_rank(vwap, 20), ts_rank(ts_sum(p.volume,60)/60, 4), 18), 3)) * -1"""
    adv60 = ts_sum(p.volume, 60) / 60.0
    base = rank(p.vwap - ts_min(p.vwap, 12))
    expo = ts_rank(correlation(ts_rank(p.vwap, 20), ts_rank(adv60, 4), 18), 3)
    return -1.0 * np.power(base.clip(lower=1e-6), expo.clip(lower=-4, upper=4))


register(AlphaSpec(
    name="alpha094", family=FAMILY, paper=_PAPER,
    description="Negative VWAP-floor rank exponentiated by VWAP-ADV60 ts-rank correlation",
    formula_text="-(rank(vwap - ts_min(vwap,12)) ^ ts_rank(correlation(ts_rank(vwap,20), ts_rank(adv60,4), 18), 3))",
    compute=_a094,
))


def _a096(p: PanelData) -> pd.Series:
    """max(ts_rank(decay_linear(correlation(rank(vwap), rank(volume), 4), 4), 8),
           ts_rank(decay_linear(ts_argmax(correlation(ts_rank(close, 7), ts_rank(ts_sum(p.volume,60)/60, 4), 4), 13), 14), 13)) * -1"""
    adv60 = ts_sum(p.volume, 60) / 60.0
    t1 = ts_rank(decay_linear(correlation(rank(p.vwap), rank(p.volume), 4), 4), 8)
    inner_corr = correlation(ts_rank(p.close, 7), ts_rank(adv60, 4), 4)
    t2 = ts_rank(decay_linear(ts_argmax(inner_corr, 13), 14), 13)
    return -1.0 * np.maximum(t1, t2)


register(AlphaSpec(
    name="alpha096", family=FAMILY, paper=_PAPER,
    description="Negative max of two decayed ts-ranks: VWAP-vol rank-corr and argmax of close-ADV60 ts-corr",
    formula_text="-max(ts_rank(decay_linear(correlation(rank(vwap),rank(volume),4),4),8), ts_rank(decay_linear(ts_argmax(correlation(ts_rank(close,7), ts_rank(adv60,4), 4), 13), 14), 13))",
    compute=_a096,
))


def _a098(p: PanelData) -> pd.Series:
    """rank(decay_linear(correlation(vwap, sum(adv5, 26), 5), 7))
       - rank(decay_linear(ts_rank(ts_argmin(correlation(rank(open), rank(adv15), 21), 9), 7), 8))"""
    adv5 = ts_sum(p.volume, 5) / 5.0
    adv15 = ts_sum(p.volume, 15) / 15.0
    t1 = rank(decay_linear(correlation(p.vwap, ts_sum(adv5, 26), 5), 7))
    inner_corr = correlation(rank(p.open), rank(adv15), 21)
    t2 = rank(decay_linear(ts_rank(ts_argmin(inner_corr, 9), 7), 8))
    return t1 - t2


register(AlphaSpec(
    name="alpha098", family=FAMILY, paper=_PAPER,
    description="VWAP-ADV5sum corr decay rank minus open-ADV15 argmin ts-rank decay",
    formula_text="rank(decay_linear(correlation(vwap, sum(adv5,26), 5), 7)) - rank(decay_linear(ts_rank(ts_argmin(correlation(rank(open), rank(adv15), 21), 9), 7), 8))",
    compute=_a098,
))


def _a099(p: PanelData) -> pd.Series:
    """(rank(correlation(sum((high+low)/2, 19), sum(adv60,19), 9))
        < rank(correlation(low, volume, 6))) * -1"""
    adv60 = ts_sum(p.volume, 60) / 60.0
    mid = (p.high + p.low) / 2.0
    c1 = rank(correlation(ts_sum(mid, 19), ts_sum(adv60, 19), 9))
    c2 = rank(correlation(p.low, p.volume, 6))
    return -1.0 * (c1 < c2).astype(float)


register(AlphaSpec(
    name="alpha099", family=FAMILY, paper=_PAPER,
    description="Long mid-ADV60 corr rank vs short low-vol corr rank — boolean penalty",
    formula_text="(rank(correlation(sum((high+low)/2, 19), sum(adv60,19), 9)) < rank(correlation(low, volume, 6))) * -1",
    compute=_a099,
))


def _a101(p: PanelData) -> pd.Series:
    """(close - open) / (high - low + 0.001)"""
    return (p.close - p.open) / (p.high - p.low + 0.001)


register(AlphaSpec(
    name="alpha101", family=FAMILY, paper=_PAPER,
    description="Intraday body / range — the classic close-position indicator, sister to qlib_KMID2",
    formula_text="(close - open) / (high - low + 0.001)",
    compute=_a101,
))
