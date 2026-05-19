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
    delta, delay, correlation, stddev, sign, log,
    regbeta, regresi, rsqr, sequence,
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


# ----- rolling OLS regression of close vs time (3N features) ----------------


def _make_BETA(n: int):
    def _fn(p: PanelData) -> pd.Series:
        seq = sequence(p.close, n)
        return regbeta(p.close, seq, n) / p.close.replace(0, np.nan)
    return _fn


def _make_RSQR(n: int):
    def _fn(p: PanelData) -> pd.Series:
        seq = sequence(p.close, n)
        return rsqr(p.close, seq, n)
    return _fn


def _make_RESI(n: int):
    def _fn(p: PanelData) -> pd.Series:
        seq = sequence(p.close, n)
        return regresi(p.close, seq, n) / p.close.replace(0, np.nan)
    return _fn


for _n in (5, 10, 20, 60):
    register(AlphaSpec(
        name=f"qlib_BETA{_n}", family=FAMILY, paper=_PAPER,
        description=f"{_n}-day OLS slope of close vs time / close — normalised trend strength",
        formula_text=f"regbeta(close, sequence, {_n}) / close",
        compute=_make_BETA(_n),
    ))
    register(AlphaSpec(
        name=f"qlib_RSQR{_n}", family=FAMILY, paper=_PAPER,
        description=f"{_n}-day OLS R² of close vs time — trend linearity in [0, 1]",
        formula_text=f"rsqr(close, sequence, {_n})",
        compute=_make_RSQR(_n),
    ))
    register(AlphaSpec(
        name=f"qlib_RESI{_n}", family=FAMILY, paper=_PAPER,
        description=f"{_n}-day OLS residual of close vs time / close — deviation from trend line",
        formula_text=f"regresi(close, sequence, {_n}) / close",
        compute=_make_RESI(_n),
    ))


# ----- volume statistics (3N features) ---------------------------------------


def _make_VMA(n: int):
    def _fn(p: PanelData) -> pd.Series:
        return ts_mean(p.volume, n) / p.volume.replace(0, np.nan)
    return _fn


def _make_VSTD(n: int):
    def _fn(p: PanelData) -> pd.Series:
        return stddev(p.volume, n) / p.volume.replace(0, np.nan)
    return _fn


def _make_VSUMP(n: int):
    def _fn(p: PanelData) -> pd.Series:
        d = delta(p.volume, 1)
        return ts_sum(d.clip(lower=0), n) / ts_sum(d.abs(), n).replace(0, np.nan)
    return _fn


for _n in (5, 20, 60):
    register(AlphaSpec(
        name=f"qlib_VMA{_n}", family=FAMILY, paper=_PAPER,
        description=f"{_n}-day volume MA / current volume — relative-volume gauge",
        formula_text=f"mean(volume, {_n}) / volume",
        compute=_make_VMA(_n),
    ))
    register(AlphaSpec(
        name=f"qlib_VSTD{_n}", family=FAMILY, paper=_PAPER,
        description=f"{_n}-day volume stddev / current volume — volume-volatility ratio",
        formula_text=f"stddev(volume, {_n}) / volume",
        compute=_make_VSTD(_n),
    ))
    register(AlphaSpec(
        name=f"qlib_VSUMP{_n}", family=FAMILY, paper=_PAPER,
        description=f"{_n}-day fraction of volume increases — positive-flow direction",
        formula_text=f"sum(max(delta(volume,1), 0), {_n}) / sum(|delta(volume,1)|, {_n})",
        compute=_make_VSUMP(_n),
    ))


# ----- v1.3.5: SUMP/SUMN/SUMD on close (3N features) ------------------------


def _make_SUMP(n: int):
    def _fn(p: PanelData) -> pd.Series:
        d = delta(p.close, 1)
        return ts_sum(d.clip(lower=0), n) / ts_sum(d.abs(), n).replace(0, np.nan)
    return _fn


def _make_SUMN(n: int):
    def _fn(p: PanelData) -> pd.Series:
        d = delta(p.close, 1)
        return ts_sum((-d).clip(lower=0), n) / ts_sum(d.abs(), n).replace(0, np.nan)
    return _fn


def _make_SUMD(n: int):
    def _fn(p: PanelData) -> pd.Series:
        d = delta(p.close, 1)
        denom = ts_sum(d.abs(), n).replace(0, np.nan)
        return (ts_sum(d.clip(lower=0), n) - ts_sum((-d).clip(lower=0), n)) / denom
    return _fn


for _n in (5, 20, 60):
    register(AlphaSpec(
        name=f"qlib_SUMP{_n}", family=FAMILY, paper=_PAPER,
        description=f"{_n}-day fraction of positive close-changes vs total absolute change",
        formula_text=f"sum(max(delta(close,1),0), {_n}) / sum(|delta(close,1)|, {_n})",
        compute=_make_SUMP(_n),
    ))
    register(AlphaSpec(
        name=f"qlib_SUMN{_n}", family=FAMILY, paper=_PAPER,
        description=f"{_n}-day fraction of negative close-changes vs total absolute change",
        formula_text=f"sum(max(-delta(close,1),0), {_n}) / sum(|delta(close,1)|, {_n})",
        compute=_make_SUMN(_n),
    ))
    register(AlphaSpec(
        name=f"qlib_SUMD{_n}", family=FAMILY, paper=_PAPER,
        description=f"{_n}-day net (up-down) close-change fraction — momentum direction",
        formula_text=f"(sum_pos - sum_neg) / sum_abs, n={_n}",
        compute=_make_SUMD(_n),
    ))


# ----- VSUMN / VSUMD (volume direction) -------------------------------------


def _make_VSUMN(n: int):
    def _fn(p: PanelData) -> pd.Series:
        d = delta(p.volume, 1)
        return ts_sum((-d).clip(lower=0), n) / ts_sum(d.abs(), n).replace(0, np.nan)
    return _fn


def _make_VSUMD(n: int):
    def _fn(p: PanelData) -> pd.Series:
        d = delta(p.volume, 1)
        denom = ts_sum(d.abs(), n).replace(0, np.nan)
        return (ts_sum(d.clip(lower=0), n) - ts_sum((-d).clip(lower=0), n)) / denom
    return _fn


for _n in (5, 20, 60):
    register(AlphaSpec(
        name=f"qlib_VSUMN{_n}", family=FAMILY, paper=_PAPER,
        description=f"{_n}-day fraction of volume decreases",
        formula_text=f"sum(max(-delta(volume,1),0), {_n}) / sum(|delta(volume,1)|, {_n})",
        compute=_make_VSUMN(_n),
    ))
    register(AlphaSpec(
        name=f"qlib_VSUMD{_n}", family=FAMILY, paper=_PAPER,
        description=f"{_n}-day net volume direction — flow direction",
        formula_text=f"(sum_pos_vdelta - sum_neg_vdelta) / sum_abs, n={_n}",
        compute=_make_VSUMD(_n),
    ))


# ----- CORD: corr of returns vs volume returns (2 features) -----------------


def _make_CORD(n: int):
    def _fn(p: PanelData) -> pd.Series:
        cr = p.close / delay(p.close, 1).replace(0, np.nan)
        vr = p.volume / delay(p.volume, 1).replace(0, np.nan)
        return correlation(cr, log(vr), n)
    return _fn


for _n in (5, 20):
    register(AlphaSpec(
        name=f"qlib_CORD{_n}", family=FAMILY, paper=_PAPER,
        description=f"{_n}-day correlation(close-ratio, log volume-ratio) — return-flow alignment",
        formula_text=f"correlation(close/delay(close,1), log(volume/delay(volume,1)), {_n})",
        compute=_make_CORD(_n),
    ))


# ----- WVMA: weighted volume-volatility (2 features) ------------------------


def _make_WVMA(n: int):
    def _fn(p: PanelData) -> pd.Series:
        ret = (p.close / delay(p.close, 1).replace(0, np.nan) - 1.0).abs()
        # stddev of (return * volume) / mean of (return * volume)
        x = ret * p.volume
        return stddev(x, n) / ts_mean(x, n).replace(0, np.nan)
    return _fn


for _n in (5, 20, 60):
    register(AlphaSpec(
        name=f"qlib_WVMA{_n}", family=FAMILY, paper=_PAPER,
        description=f"{_n}-day weighted-volume-volatility — |return|*vol coefficient of variation",
        formula_text=f"stddev(|ret|*volume, {_n}) / mean(|ret|*volume, {_n})",
        compute=_make_WVMA(_n),
    ))


# ----- MAX / MIN / QTLU / QTLD (relative to close, 4N features) -------------


def _make_MAX(n: int):
    def _fn(p: PanelData) -> pd.Series:
        return ts_max(p.high, n) / p.close.replace(0, np.nan)
    return _fn


def _make_MIN(n: int):
    def _fn(p: PanelData) -> pd.Series:
        return ts_min(p.low, n) / p.close.replace(0, np.nan)
    return _fn


def _make_QTLU(n: int):
    """80th percentile of close in last n days / current close — upper band proxy."""
    def _fn(p: PanelData) -> pd.Series:
        grouped = p.close.groupby(level="code", group_keys=False)
        out = grouped.rolling(window=n, min_periods=n).quantile(0.8)
        if isinstance(out.index, pd.MultiIndex) and out.index.nlevels > 2:
            out = out.droplevel(0)
        return out.reindex(p.close.index) / p.close.replace(0, np.nan)
    return _fn


def _make_QTLD(n: int):
    """20th percentile of close in last n days / current close — lower band proxy."""
    def _fn(p: PanelData) -> pd.Series:
        grouped = p.close.groupby(level="code", group_keys=False)
        out = grouped.rolling(window=n, min_periods=n).quantile(0.2)
        if isinstance(out.index, pd.MultiIndex) and out.index.nlevels > 2:
            out = out.droplevel(0)
        return out.reindex(p.close.index) / p.close.replace(0, np.nan)
    return _fn


for _n in (5, 10, 20, 60):
    register(AlphaSpec(
        name=f"qlib_MAX{_n}", family=FAMILY, paper=_PAPER,
        description=f"{_n}-day high / current close — resistance distance",
        formula_text=f"ts_max(high, {_n}) / close",
        compute=_make_MAX(_n),
    ))
    register(AlphaSpec(
        name=f"qlib_MIN{_n}", family=FAMILY, paper=_PAPER,
        description=f"{_n}-day low / current close — support distance",
        formula_text=f"ts_min(low, {_n}) / close",
        compute=_make_MIN(_n),
    ))
    register(AlphaSpec(
        name=f"qlib_QTLU{_n}", family=FAMILY, paper=_PAPER,
        description=f"{_n}-day 80th percentile of close / current — upper-band proxy",
        formula_text=f"quantile(close, 0.8, {_n}) / close",
        compute=_make_QTLU(_n),
    ))
    register(AlphaSpec(
        name=f"qlib_QTLD{_n}", family=FAMILY, paper=_PAPER,
        description=f"{_n}-day 20th percentile of close / current — lower-band proxy",
        formula_text=f"quantile(close, 0.2, {_n}) / close",
        compute=_make_QTLD(_n),
    ))


# ----- RANK (relative position within window, 4 features) -------------------


def _make_RANK(n: int):
    def _fn(p: PanelData) -> pd.Series:
        return ts_rank(p.close, n) / n
    return _fn


for _n in (5, 10, 20, 60):
    register(AlphaSpec(
        name=f"qlib_RANK{_n}", family=FAMILY, paper=_PAPER,
        description=f"{_n}-day rank of close within window — relative position [0, 1]",
        formula_text=f"ts_rank(close, {_n}) / {_n}",
        compute=_make_RANK(_n),
    ))


# ----- CNTD: count diff (3 features) ----------------------------------------


def _make_CNTD(n: int):
    def _fn(p: PanelData) -> pd.Series:
        up = (p.close > delay(p.close, 1)).astype(float)
        dn = (p.close < delay(p.close, 1)).astype(float)
        return (ts_sum(up, n) - ts_sum(dn, n)) / n
    return _fn


for _n in (5, 20, 60):
    register(AlphaSpec(
        name=f"qlib_CNTD{_n}", family=FAMILY, paper=_PAPER,
        description=f"{_n}-day count diff (up - down) / n — net day-direction",
        formula_text=f"(count_up - count_down) / {_n}",
        compute=_make_CNTD(_n),
    ))


# ----- IMXD: argmax - argmin position diff (3 features) ----------------------


def _make_IMXD(n: int):
    def _fn(p: PanelData) -> pd.Series:
        return (ts_argmax(p.high, n) - ts_argmin(p.low, n)) / n
    return _fn


for _n in (5, 10, 20):
    register(AlphaSpec(
        name=f"qlib_IMXD{_n}", family=FAMILY, paper=_PAPER,
        description=f"{_n}-day argmax(high) − argmin(low) / n — high-low recency gap",
        formula_text=f"(ts_argmax(high, {_n}) - ts_argmin(low, {_n})) / {_n}",
        compute=_make_IMXD(_n),
    ))
