"""SP-B.2 事件信号: cross 算子 + 事件研究引擎 + I/O + REST。"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

import financial_analyst.factors.zoo  # noqa: F401  (注册 alpha families)
from financial_analyst.factors.zoo import operators as ops
from financial_analyst.factors.zoo.panel import PanelData


def _series(code, vals):
    dates = pd.date_range("2024-01-02", periods=len(vals), freq="B")
    idx = pd.MultiIndex.from_product([dates, [code]], names=["datetime", "code"])
    return pd.Series(vals, index=idx, dtype=float)


def test_cross_up_and_down():
    a = _series("A", [1, 1, 3, 3, 1])
    b = _series("A", [2, 2, 2, 2, 2])
    up = ops.cross(a, b)            # a 上穿 b
    assert list(up.values) == [0.0, 0.0, 1.0, 0.0, 0.0]   # 仅 idx2 上穿
    down = ops.cross(b, a)          # 死叉 = 反向
    assert down.iloc[4] == 1.0 and down.iloc[2] == 0.0
