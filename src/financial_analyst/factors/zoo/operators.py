"""Operators for alpha formulas.

All operators consume pd.Series indexed by MultiIndex ``(datetime, code)``
(as produced by ``PanelData``) and return same-shape Series. Time-series
operators group by ``code`` so windows don't bleed across stocks;
cross-sectional operators group by ``datetime``.

Naming follows the alpha101 / gtja191 papers so formulas can be ported
character-for-character:

    rank, ts_rank, ts_argmax, ts_argmin, ts_max, ts_min, ts_sum,
    delta, delay, correlation, covariance, decay_linear, scale, sma,
    stddev, signedpower, log, sign, abs_, product, indneutralize

Lookahead protection: every ``ts_*`` op uses ``min_periods=window`` so
rows before the window has filled out emit NaN, never a partial signal.
"""
from __future__ import annotations
from typing import Union

import numpy as np
import pandas as pd

# ----- cross-sectional ops ---------------------------------------------------


def rank(x: pd.Series) -> pd.Series:
    """Cross-sectional percentile rank per date, in [0, 1]."""
    return x.groupby(level="datetime").rank(pct=True)


def scale(x: pd.Series, a: float = 1.0) -> pd.Series:
    """Cross-sectional scale to sum(|x|) == a per date."""
    def _f(s: pd.Series) -> pd.Series:
        denom = s.abs().sum()
        if denom == 0 or np.isnan(denom):
            return s * np.nan
        return s * (a / denom)
    return x.groupby(level="datetime").transform(_f)


def indneutralize(x: pd.Series, group: pd.Series) -> pd.Series:
    """Demean x within ``group`` at each date. ``group`` is a Series of
    the same index whose values are the group label (e.g. industry id).
    """
    joined = pd.concat([x.rename("x"), group.rename("g")], axis=1)
    return (joined["x"] - joined.groupby([joined.index.get_level_values("datetime"), "g"])["x"].transform("mean")).reindex(x.index)


# ----- time-series ops -------------------------------------------------------


def _per_code_rolling(x: pd.Series, n: int, fn_name: str, **kw) -> pd.Series:
    """Apply a rolling fn per code without bleeding across stocks."""
    grouped = x.groupby(level="code", group_keys=False)
    fn = getattr(grouped.rolling(window=n, min_periods=n, **kw), fn_name)
    out = fn()
    if isinstance(out.index, pd.MultiIndex) and out.index.nlevels > 2:
        out = out.droplevel(0)
    return out.reindex(x.index)


def ts_sum(x: pd.Series, n: int) -> pd.Series:
    return _per_code_rolling(x, n, "sum")


def ts_mean(x: pd.Series, n: int) -> pd.Series:
    return _per_code_rolling(x, n, "mean")


def stddev(x: pd.Series, n: int) -> pd.Series:
    return _per_code_rolling(x, n, "std")


def ts_max(x: pd.Series, n: int) -> pd.Series:
    return _per_code_rolling(x, n, "max")


def ts_min(x: pd.Series, n: int) -> pd.Series:
    return _per_code_rolling(x, n, "min")


def ts_argmax(x: pd.Series, n: int) -> pd.Series:
    """Position of max within last n bars (1-indexed: 1 = oldest, n = latest)."""
    def _argmax(arr):
        return float(np.argmax(arr) + 1)
    return _per_code_rolling_apply(x, n, _argmax)


def ts_argmin(x: pd.Series, n: int) -> pd.Series:
    def _argmin(arr):
        return float(np.argmin(arr) + 1)
    return _per_code_rolling_apply(x, n, _argmin)


def ts_rank(x: pd.Series, n: int) -> pd.Series:
    """Rank of the latest value within the last n bars, in [1, n]."""
    def _rank(arr):
        s = pd.Series(arr)
        return float(s.rank(method="min").iloc[-1])
    return _per_code_rolling_apply(x, n, _rank)


def _per_code_rolling_apply(x: pd.Series, n: int, fn) -> pd.Series:
    grouped = x.groupby(level="code", group_keys=False)
    out = grouped.rolling(window=n, min_periods=n).apply(fn, raw=True)
    if isinstance(out.index, pd.MultiIndex) and out.index.nlevels > 2:
        out = out.droplevel(0)
    return out.reindex(x.index)


def delta(x: pd.Series, n: int) -> pd.Series:
    """x_t - x_{t-n}, per code."""
    return x.groupby(level="code", group_keys=False).diff(n)


def delay(x: pd.Series, n: int) -> pd.Series:
    """x_{t-n}, per code."""
    return x.groupby(level="code", group_keys=False).shift(n)


def correlation(x: pd.Series, y: pd.Series, n: int) -> pd.Series:
    """Rolling correlation between x and y over the last n bars per code."""
    pair = pd.concat([x.rename("x"), y.rename("y")], axis=1)

    def _corr(df: pd.DataFrame) -> pd.Series:
        # rolling correlation of two columns of a per-code slice
        return df["x"].rolling(window=n, min_periods=n).corr(df["y"])

    out = pair.groupby(level="code", group_keys=False).apply(_corr)
    return out.reindex(x.index)


def covariance(x: pd.Series, y: pd.Series, n: int) -> pd.Series:
    pair = pd.concat([x.rename("x"), y.rename("y")], axis=1)

    def _cov(df: pd.DataFrame) -> pd.Series:
        return df["x"].rolling(window=n, min_periods=n).cov(df["y"])

    out = pair.groupby(level="code", group_keys=False).apply(_cov)
    return out.reindex(x.index)


def decay_linear(x: pd.Series, n: int) -> pd.Series:
    """Linear-weighted moving average with weights n, n-1, ..., 1
    (most recent gets weight n)."""
    weights = np.arange(1, n + 1, dtype=float)
    weights /= weights.sum()

    def _wsum(arr: np.ndarray) -> float:
        return float(np.dot(arr, weights))

    return _per_code_rolling_apply(x, n, _wsum)


def sma(x: pd.Series, n: int, m: int = 1) -> pd.Series:
    """Geometric SMA used in GTJA: SMA(X, n, m) = (m * X_t + (n-m) * SMA_{t-1}) / n.
    Recursive EWMA-style.
    """
    if m >= n:
        raise ValueError(f"SMA requires m < n; got m={m}, n={n}")

    def _sma(s: pd.Series) -> pd.Series:
        out = np.full(len(s), np.nan, dtype=float)
        prev = np.nan
        for i, v in enumerate(s.values):
            if np.isnan(v):
                continue
            if np.isnan(prev):
                prev = v
            else:
                prev = (m * v + (n - m) * prev) / n
            out[i] = prev
        return pd.Series(out, index=s.index)

    return x.groupby(level="code", group_keys=False).apply(_sma).reindex(x.index)


# ----- elementwise -----------------------------------------------------------


def signedpower(x: pd.Series, p: float) -> pd.Series:
    """sign(x) * |x|^p — preserves sign while raising magnitude to power p."""
    return np.sign(x) * np.power(np.abs(x), p)


def log(x: pd.Series) -> pd.Series:
    return np.log(x.where(x > 0))


def sign(x: pd.Series) -> pd.Series:
    return np.sign(x)


def abs_(x: pd.Series) -> pd.Series:
    return x.abs()


def product(x: pd.Series, n: int) -> pd.Series:
    """Product over the last n bars per code."""
    def _prod(arr: np.ndarray) -> float:
        return float(np.prod(arr))
    return _per_code_rolling_apply(x, n, _prod)


def power(x: pd.Series, p: float) -> pd.Series:
    return np.power(x, p)
