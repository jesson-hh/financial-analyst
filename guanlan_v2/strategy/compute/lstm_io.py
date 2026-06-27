# -*- coding: utf-8 -*-
"""LSTM 港移纯函数 helper:前向收益标签 + PIT 序列窗 + 截面预测输入。
无 torch/无引擎依赖(只 numpy/pandas),可 TDD。逻辑抽自 workflow/api.py:_lstm_eval。
"""
from __future__ import annotations

from typing import List, Tuple

import numpy as np
import pandas as pd


def add_forward_return(panel: pd.DataFrame, horizon: int, close_col: str = "close",
                       out_col: str = "__fwd_ret__") -> pd.DataFrame:
    """逐 instrument 加前向 horizon 日收益列 close[t+h]/close[t]−1(末 horizon 行 NaN)。
    panel: MultiIndex (instrument, datetime)。返回带新列的副本。"""
    panel = panel.copy()
    fwd = panel[close_col].groupby(level="instrument").transform(
        lambda s: s.shift(-horizon) / s - 1.0)
    panel[out_col] = fwd
    return panel


def build_sequences(panel: pd.DataFrame, feature_cols: List[str], label_col: str,
                    seq_len: int, cutoff) -> Tuple[np.ndarray, np.ndarray, list]:
    """逐 instrument 按日期排序滑窗:每 label_date t 取前 seq_len 期特征窗 + label[t]。
    PIT 闸:仅 label_date ≤ cutoff & 窗口全有限 & label 有限 的样本入选。
    返回 (X[N,seq_len,F] float32, y[N] float32, index[(datetime,instrument)])。"""
    cutoff = pd.Timestamp(cutoff)
    X: List[np.ndarray] = []
    y: List[float] = []
    idx: list = []
    for code, g in panel.groupby(level="instrument"):
        g = g.sort_index(level="datetime")
        dts = g.index.get_level_values("datetime")
        feat = g[feature_cols].to_numpy("float64")
        lab = g[label_col].to_numpy("float64")
        m = feat.shape[0]
        for t in range(seq_len - 1, m):
            if dts[t] > cutoff:
                continue
            win = feat[t - seq_len + 1: t + 1]
            yv = lab[t]
            if not np.isfinite(win).all() or not np.isfinite(yv):
                continue
            X.append(win)
            y.append(float(yv))
            idx.append((dts[t], code))
    if not X:
        return (np.empty((0, seq_len, len(feature_cols)), dtype=np.float32),
                np.empty((0,), dtype=np.float32), [])
    return (np.asarray(X, dtype=np.float32), np.asarray(y, dtype=np.float32), idx)


def predict_index(panel: pd.DataFrame, feature_cols: List[str], seq_len: int,
                  eval_date) -> Tuple[np.ndarray, List[str]]:
    """每 instrument 取截至 ≤ eval_date 的末 seq_len 期特征窗为预测输入(不看未来)。
    历史不足 seq_len 或末窗含非有限值 → 跳该 code。返回 (X[M,seq_len,F] float32, codes)。"""
    eval_ts = pd.Timestamp(eval_date)
    X: List[np.ndarray] = []
    codes: List[str] = []
    for code, g in panel.groupby(level="instrument"):
        g = g.sort_index(level="datetime")
        g = g[g.index.get_level_values("datetime") <= eval_ts]
        if len(g) < seq_len:
            continue
        win = g[feature_cols].to_numpy("float64")[-seq_len:]
        if not np.isfinite(win).all():
            continue
        X.append(win)
        codes.append(code)
    if not X:
        return np.empty((0, seq_len, len(feature_cols)), dtype=np.float32), []
    return np.asarray(X, dtype=np.float32), codes
