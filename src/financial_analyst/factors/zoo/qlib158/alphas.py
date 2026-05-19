"""Ports of Microsoft Qlib Alpha158 — simple OHLC ratios + moving stats.

Naming follows Qlib's exact convention so a researcher familiar with
``qlib.contrib.data.handler.Alpha158`` can spot the equivalent here
without translation.

Source: ``microsoft/qlib`` github, handler.py and config.py.
"""
from __future__ import annotations
import numpy as np
import pandas as pd

from financial_analyst.factors.zoo.panel import PanelData
from financial_analyst.factors.zoo.registry import AlphaSpec, register
from financial_analyst.factors.zoo.operators import (
    rank, ts_max, ts_min, ts_sum, ts_mean, ts_rank, ts_argmax, ts_argmin,
    delta, delay, correlation, stddev, sign,
)

FAMILY = "qlib158"
_PAPER = "Microsoft Qlib Alpha158 (github.com/microsoft/qlib)"


def _eps_div(num: pd.Series, den: pd.Series) -> pd.Series:
    return num / den.replace(0, np.nan)


# ----- daily candle shape (5 features) ---------------------------------------


def _KMID(p: PanelData) -> pd.Series:
    return _eps_div(p.close - p.open, p.open)


register(AlphaSpec(
    name="qlib_KMID", family=FAMILY, paper=_PAPER,
    description="(close - open) / open — body / open ratio (signed intraday return)",
    formula_text="(close - open) / open",
    compute=_KMID,
))


def _KLEN(p: PanelData) -> pd.Series:
    return _eps_div(p.high - p.low, p.open)


register(AlphaSpec(
    name="qlib_KLEN", family=FAMILY, paper=_PAPER,
    description="(high - low) / open — daily range / open",
    formula_text="(high - low) / open",
    compute=_KLEN,
))


def _KMID2(p: PanelData) -> pd.Series:
    return _eps_div(p.close - p.open, p.high - p.low)


register(AlphaSpec(
    name="qlib_KMID2", family=FAMILY, paper=_PAPER,
    description="(close - open) / (high - low) — body / range, normalised intraday return",
    formula_text="(close - open) / (high - low + eps)",
    compute=_KMID2,
))


def _KUP(p: PanelData) -> pd.Series:
    upper = p.high - np.maximum(p.open, p.close)
    return _eps_div(upper, p.open)


register(AlphaSpec(
    name="qlib_KUP", family=FAMILY, paper=_PAPER,
    description="Upper wick / open — rejection on highs",
    formula_text="(high - max(open, close)) / open",
    compute=_KUP,
))


def _KLOW(p: PanelData) -> pd.Series:
    lower = np.minimum(p.open, p.close) - p.low
    return _eps_div(lower, p.open)


register(AlphaSpec(
    name="qlib_KLOW", family=FAMILY, paper=_PAPER,
    description="Lower wick / open — buying support at lows",
    formula_text="(min(open, close) - low) / open",
    compute=_KLOW,
))


def _KSFT(p: PanelData) -> pd.Series:
    return _eps_div(2 * p.close - p.high - p.low, p.open)


register(AlphaSpec(
    name="qlib_KSFT", family=FAMILY, paper=_PAPER,
    description="(2*close - high - low) / open — close skew relative to range, signed",
    formula_text="(2*close - high - low) / open",
    compute=_KSFT,
))


# ----- moving averages and dispersion (3N features) --------------------------


def _make_MA(n: int):
    def _fn(p: PanelData) -> pd.Series:
        return ts_mean(p.close, n) / p.close.replace(0, np.nan)
    return _fn


def _make_STD(n: int):
    def _fn(p: PanelData) -> pd.Series:
        return stddev(p.close, n) / p.close.replace(0, np.nan)
    return _fn


def _make_ROC(n: int):
    def _fn(p: PanelData) -> pd.Series:
        return delay(p.close, n) / p.close.replace(0, np.nan)
    return _fn


for _n in (5, 10, 20, 60):
    register(AlphaSpec(
        name=f"qlib_MA{_n}", family=FAMILY, paper=_PAPER,
        description=f"{_n}-day MA / close — mean-reversion gauge",
        formula_text=f"mean(close, {_n}) / close",
        compute=_make_MA(_n),
    ))
    register(AlphaSpec(
        name=f"qlib_STD{_n}", family=FAMILY, paper=_PAPER,
        description=f"{_n}-day stddev / close — relative volatility",
        formula_text=f"stddev(close, {_n}) / close",
        compute=_make_STD(_n),
    ))
    register(AlphaSpec(
        name=f"qlib_ROC{_n}", family=FAMILY, paper=_PAPER,
        description=f"{_n}-day close ratio (lagged/current) — reversal-strength indicator",
        formula_text=f"delay(close, {_n}) / close",
        compute=_make_ROC(_n),
    ))


# ----- stochastic / range location (N features) ------------------------------


def _make_RSV(n: int):
    def _fn(p: PanelData) -> pd.Series:
        rng = (ts_max(p.high, n) - ts_min(p.low, n)).replace(0, np.nan)
        return (p.close - ts_min(p.low, n)) / rng
    return _fn


def _make_IMAX(n: int):
    def _fn(p: PanelData) -> pd.Series:
        return ts_argmax(p.high, n) / n
    return _fn


def _make_IMIN(n: int):
    def _fn(p: PanelData) -> pd.Series:
        return ts_argmin(p.low, n) / n
    return _fn


for _n in (5, 10, 20):
    register(AlphaSpec(
        name=f"qlib_RSV{_n}", family=FAMILY, paper=_PAPER,
        description=f"{_n}-day stochastic %K position [0,1] in high-low range",
        formula_text=f"(close - ts_min(low,{_n})) / (ts_max(high,{_n}) - ts_min(low,{_n}))",
        compute=_make_RSV(_n),
    ))
    register(AlphaSpec(
        name=f"qlib_IMAX{_n}", family=FAMILY, paper=_PAPER,
        description=f"Position of {_n}-day high (1=oldest, {_n}=newest), normalised by N",
        formula_text=f"ts_argmax(high, {_n}) / {_n}",
        compute=_make_IMAX(_n),
    ))
    register(AlphaSpec(
        name=f"qlib_IMIN{_n}", family=FAMILY, paper=_PAPER,
        description=f"Position of {_n}-day low (1=oldest, {_n}=newest), normalised by N",
        formula_text=f"ts_argmin(low, {_n}) / {_n}",
        compute=_make_IMIN(_n),
    ))


# ----- up/down day counts (N features) ---------------------------------------


def _make_CNTP(n: int):
    def _fn(p: PanelData) -> pd.Series:
        up = (p.close > delay(p.close, 1)).astype(float)
        return ts_sum(up, n) / n
    return _fn


def _make_CNTN(n: int):
    def _fn(p: PanelData) -> pd.Series:
        dn = (p.close < delay(p.close, 1)).astype(float)
        return ts_sum(dn, n) / n
    return _fn


for _n in (5, 20, 60):
    register(AlphaSpec(
        name=f"qlib_CNTP{_n}", family=FAMILY, paper=_PAPER,
        description=f"{_n}-day up-day fraction — positive momentum count",
        formula_text=f"count(close > delay(close,1), {_n}) / {_n}",
        compute=_make_CNTP(_n),
    ))
    register(AlphaSpec(
        name=f"qlib_CNTN{_n}", family=FAMILY, paper=_PAPER,
        description=f"{_n}-day down-day fraction — negative momentum count",
        formula_text=f"count(close < delay(close,1), {_n}) / {_n}",
        compute=_make_CNTN(_n),
    ))


# ----- price-volume correlation (N features) ---------------------------------


def _make_CORR(n: int):
    def _fn(p: PanelData) -> pd.Series:
        return correlation(p.close, np.log(p.volume.replace(0, np.nan)), n)
    return _fn


for _n in (5, 20):
    register(AlphaSpec(
        name=f"qlib_CORR{_n}", family=FAMILY, paper=_PAPER,
        description=f"{_n}-day rolling correlation(close, log(volume)) — confirmation strength",
        formula_text=f"correlation(close, log(volume), {_n})",
        compute=_make_CORR(_n),
    ))
