"""截面预处理: 去极值 / 标准化 / 中性化(A.2 占位)。每个函数对同一日期横截面操作。"""
from __future__ import annotations
import pandas as pd


def winsorize(x: pd.Series, q: float = 0.01) -> pd.Series:
    """Per-date clip to [quantile(q), quantile(1-q)] using empirical (non-interpolated) bounds."""
    def _clip(s: pd.Series) -> pd.Series:
        lo = s.quantile(q, interpolation="lower")
        hi = s.quantile(1 - q, interpolation="lower")
        return s.clip(lo, hi)
    return x.groupby(level="datetime", group_keys=False).transform(_clip)


def zscore(x: pd.Series) -> pd.Series:
    """Per-date (x - mean) / std. Zero-std dates → NaN."""
    g = x.groupby(level="datetime")
    mean = g.transform("mean")
    std = g.transform("std")
    return (x - mean) / std.where(std > 0)


def neutralize(x: pd.Series, industry=None, mktcap=None) -> pd.Series:
    """行业 + 市值中性化。A.2 实现, 本期占位。"""
    raise NotImplementedError("neutralize() 留到 SP-A.2 (行业+市值中性化)")
