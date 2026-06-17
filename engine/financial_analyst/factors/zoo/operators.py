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
from typing import Optional, Union

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


def csmean(x: pd.Series) -> pd.Series:
    """Cross-sectional mean per date, broadcast to every code (市场/篮子均值)。

    每个交易日对全体 code 求均值并广播回每只票 → 一条"篮子/大盘收益"序列。用于"个股 vs
    篮子"共振:``correlation(returns, csmean(returns), 20)`` = 个股与篮子的滚动相关;
    beta = ``covariance(returns, csmean(returns), n) / covariance(csmean(returns), csmean(returns), n)``。
    篮子 = 当前面板的全体 code(自选股池 → 这批票;宽基池 → 近似该宽基等权大盘)。"""
    return x.groupby(level="datetime").transform("mean")


def indmean(x: pd.Series, group: pd.Series) -> pd.Series:
    """Cross-sectional mean of x WITHIN each ``group`` at each date, broadcast
    back to every code in that group (行业/分组均值 → 行业共振)。

    与 ``indneutralize`` 同源但取均值不去均值:恒等 ``indneutralize(x,g) == x - indmean(x,g)``。
    配 ``correlation`` 得"个股 vs 所在行业"的滚动共振:
    ``correlation(returns, indmean(returns, industry), 20)``;行业 beta 同 csmean 写法把篮子换成
    ``indmean(returns, industry)``。``group`` 是同 index 的分组标签 Series(如 ``industry``)。"""
    joined = pd.concat([x.rename("x"), group.rename("g")], axis=1)
    grp = joined.groupby([joined.index.get_level_values("datetime"), "g"])["x"]
    return grp.transform("mean").reindex(x.index)


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
    """Rolling correlation between x and y over the last n bars per code.

    Explicit per-code loop (not ``groupby.apply``): pandas' ``groupby.apply`` returns
    a *DataFrame* when there is a single group, which breaks single-stock panels
    (e.g. individual-stock resonance ``correlation(returns, idx_ret, 20)``). Concat of
    per-code Series is always a Series and is equivalent for the multi-code case.
    """
    pair = pd.concat([x.rename("x"), y.rename("y")], axis=1)
    parts = [df["x"].rolling(window=n, min_periods=n).corr(df["y"])
             for _code, df in pair.groupby(level="code", sort=False)]
    out = pd.concat(parts) if parts else pd.Series(dtype="float64", index=x.index)
    return out.reindex(x.index)


def covariance(x: pd.Series, y: pd.Series, n: int) -> pd.Series:
    pair = pd.concat([x.rename("x"), y.rename("y")], axis=1)
    parts = [df["x"].rolling(window=n, min_periods=n).cov(df["y"])
             for _code, df in pair.groupby(level="code", sort=False)]
    out = pd.concat(parts) if parts else pd.Series(dtype="float64", index=x.index)
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


# ----- rolling OLS regression -----------------------------------------------


def regbeta(y: pd.Series, x: pd.Series, n: int, min_periods: Optional[int] = None) -> pd.Series:
    """Rolling OLS β of y on x over the last n bars per code.

    Formula: β = cov(y, x, n) / var(x, n).

    ``min_periods`` controls how many non-NaN observations are required
    inside the n-bar window before a beta is emitted. Default is ``n``
    (strict, full window). For alphas that filter the input series
    (e.g. ``gtja149`` keeps only benchmark-down days), pass a smaller
    threshold like ``n // 4`` so the half-NaN windows still produce a
    valid regression.

    Used by gtja021 (slope of MA6 vs time), alpha101 regression alphas,
    qlib158 BETA features, gtja149 downside beta.
    """
    if min_periods is None:
        min_periods = n
    pair = pd.concat([y.rename("y"), x.rename("x")], axis=1)

    def _beta(df: pd.DataFrame) -> pd.Series:
        cov = df["y"].rolling(window=n, min_periods=min_periods).cov(df["x"])
        var = df["x"].rolling(window=n, min_periods=min_periods).var()
        return cov / var.where(var > 0)

    # 逐 code 循环(非 groupby.apply):单组(单票)时 groupby.apply 会返 DataFrame → 破坏单票
    # 共振/跟随(个股 vs 大盘 beta);concat 永返 Series,多票等价(同 correlation 的修法)。
    parts = [_beta(_df) for _code, _df in pair.groupby(level="code", sort=False)]
    out = pd.concat(parts) if parts else pd.Series(dtype="float64", index=y.index)
    return out.reindex(y.index)


def regresi(y: pd.Series, x: pd.Series, n: int) -> pd.Series:
    """Rolling OLS residual y - (β x + α) over the last n bars per code.

    Used by qlib158 RESI features, gtja regression alphas, alpha101 #29.
    """
    pair = pd.concat([y.rename("y"), x.rename("x")], axis=1)

    def _resi(df: pd.DataFrame) -> pd.Series:
        ym = df["y"].rolling(window=n, min_periods=n).mean()
        xm = df["x"].rolling(window=n, min_periods=n).mean()
        cov = df["y"].rolling(window=n, min_periods=n).cov(df["x"])
        var = df["x"].rolling(window=n, min_periods=n).var()
        beta = cov / var.where(var > 0)
        alpha = ym - beta * xm
        # residual at the latest point of the window: y_t - (beta * x_t + alpha)
        return df["y"] - (beta * df["x"] + alpha)

    parts = [_resi(_df) for _code, _df in pair.groupby(level="code", sort=False)]
    out = pd.concat(parts) if parts else pd.Series(dtype="float64", index=y.index)
    return out.reindex(y.index)


def rsqr(y: pd.Series, x: pd.Series, n: int) -> pd.Series:
    """Rolling OLS R² over the last n bars per code. In [0, 1] when well-
    defined; NaN when var(x) == 0 or var(y) == 0.
    """
    pair = pd.concat([y.rename("y"), x.rename("x")], axis=1)

    def _rsq(df: pd.DataFrame) -> pd.Series:
        cov = df["y"].rolling(window=n, min_periods=n).cov(df["x"])
        var_x = df["x"].rolling(window=n, min_periods=n).var()
        var_y = df["y"].rolling(window=n, min_periods=n).var()
        denom = (var_x * var_y).where((var_x > 0) & (var_y > 0))
        return (cov * cov) / denom

    parts = [_rsq(_df) for _code, _df in pair.groupby(level="code", sort=False)]
    out = pd.concat(parts) if parts else pd.Series(dtype="float64", index=y.index)
    return out.reindex(y.index)


def sequence(template: pd.Series, n: int) -> pd.Series:
    """Return a Series same-shape as ``template`` whose value at each
    (date, code) is the position within the trailing n-bar window
    (1, 2, ..., n). Used as a synthetic time index in regression alphas
    (e.g., GTJA-191 #021: ``REGBETA(MEAN(CLOSE,6), SEQUENCE(6))``).

    Because the value is constant for every code, this is mostly useful
    as the ``x`` argument to ``regbeta`` — the resulting β is the slope
    of ``y`` against time.
    """
    # Use the per-code position within the panel as the time index — for
    # rolling-window regression, only the LAST n positions matter, so any
    # monotonic series works as long as deltas are constant. We use
    # cumulative count within code.
    counts = template.groupby(level="code").cumcount() + 1
    return counts.astype(float)


# ----- weighted moving average ----------------------------------------------


def wma(x: pd.Series, n: int) -> pd.Series:
    """Linear-weighted moving average with weights 1, 2, ..., n.

    Differs from ``decay_linear`` only in weight normalisation:
    ``wma`` divides by ``sum(weights)`` (= n*(n+1)/2); the older operator
    ``decay_linear`` already does this too. Provided under both names so
    formulas porting from gtja191 / alpha101 don't need to be rewritten.
    """
    return decay_linear(x, n)


# ----- element-wise max/min pair --------------------------------------------


def max_pair(a, b):
    """Element-wise max of two scalars or Series. ``np.maximum`` already
    does this; the alias exists so formulas can write ``max_pair(...)``
    matching paper notation ``MAX(a, b)`` without ambiguity vs the
    time-series ``ts_max(x, n)``.
    """
    return np.maximum(a, b)


def min_pair(a, b):
    return np.minimum(a, b)


def filter_where(x: pd.Series, mask) -> pd.Series:
    """Keep ``x`` where ``mask`` is True; replace the rest with NaN.

    Used by GTJA-style ``FILTER(series, condition)`` constructs, e.g.
    gtja149's "regress stock returns vs bench returns ON DAYS WHERE
    benchmark fell" — feed regbeta inputs through filter_where to
    drop the unwanted days before the rolling regression.

    NaNs propagate naturally through ``regbeta`` (variance/cov skip them
    in pandas .rolling), so masked-out days simply don't influence the
    slope. Window size n still counts those bars but they contribute 0
    weight — close enough for the GTJA convention.
    """
    return x.where(mask)


def cross(a, b):
    """a 上穿 b: a[t-1] <= b[t-1] 且 a[t] > b[t] → 1.0, 否则 0.0 (逐 code)。

    金叉 = cross(dif, dea) / 突破均线 = cross(close, sma(close, 20)); 死叉 = cross(b, a)。
    a, b 为同 panel 索引的 Series (delay 已逐 code shift)。
    """
    prev_a = delay(a, 1) if hasattr(a, "index") else a
    prev_b = delay(b, 1) if hasattr(b, "index") else b
    up = (a > b) & (prev_a <= prev_b)
    return up.astype(float)
