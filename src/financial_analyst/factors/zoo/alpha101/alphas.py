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
