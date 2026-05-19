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
