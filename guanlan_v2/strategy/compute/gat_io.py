# -*- coding: utf-8 -*-
"""GAT 源数据准备纯函数:close/volume 面板 → PIT 节点特征 / 收益相关图 / 前向标签 / 换仓日。
无 torch、无引擎依赖(只 numpy/pandas),guanlan 主 env 与 conda stocks 都可 import(同 fincast_io 约束)。
**PIT 命门**:一律对面板 `.loc[:date]` 截断后再算,绝不看未来;标签未来收益只在训练日(已实现)取用。
"""
from __future__ import annotations

from typing import List, Optional, Tuple

import numpy as np
import pandas as pd

DEFAULT_GAT_FACTORS = ("mom_5", "mom_20", "mom_60", "rev_1", "vol_20", "ma_gap", "turn", "amihud_20")


def _zscore_cols(df: pd.DataFrame) -> pd.DataFrame:
    """逐列横截面 z-score(std=0 或全 NaN → 0)。"""
    mu = df.mean(axis=0)
    sd = df.std(axis=0, ddof=0).replace(0.0, np.nan)
    return ((df - mu) / sd).fillna(0.0)


def compute_node_features(close_panel: pd.DataFrame, volume_panel: Optional[pd.DataFrame],
                          date, *, factors=DEFAULT_GAT_FACTORS) -> Tuple[List[str], np.ndarray]:
    """date 横截面 PIT 价量因子快照(只用 ≤date 数据)→ (codes, (N,F) float32),逐因子横截面 z。
    入选 = close 末值非空;volume 缺/空 → turn/amihud 置 0。"""
    cp = close_panel.loc[:pd.Timestamp(date)]
    ret1 = cp.pct_change(fill_method=None)
    last = cp.iloc[-1]
    feat = {
        "mom_5": last / cp.shift(5).iloc[-1] - 1.0,
        "mom_20": last / cp.shift(20).iloc[-1] - 1.0,
        "mom_60": last / cp.shift(60).iloc[-1] - 1.0,
        "rev_1": -(last / cp.shift(1).iloc[-1] - 1.0),
        "vol_20": ret1.tail(20).std(axis=0, ddof=0),
        "ma_gap": last / cp.tail(20).mean(axis=0) - 1.0,
    }
    has_vol = volume_panel is not None and not volume_panel.empty
    if has_vol:
        # 对齐到 close 的交易日历(index)+ 标的(columns):否则 vp.iloc[-1] 取的是成交量面板自己的
        # 末行(可能是更早日期),turn/amihud 会拿陈旧日 vs close 末值错位;对齐后缺量诚实成 NaN→z=0。
        vp = volume_panel.loc[:pd.Timestamp(date)].reindex(index=cp.index, columns=cp.columns)
        vma20 = vp.tail(20).mean(axis=0).replace(0.0, np.nan)
        feat["turn"] = vp.iloc[-1] / vma20
        feat["amihud_20"] = (ret1.abs() / (cp * vp).replace(0.0, np.nan)).tail(20).mean(axis=0)
    else:
        feat["turn"] = pd.Series(0.0, index=cp.columns)
        feat["amihud_20"] = pd.Series(0.0, index=cp.columns)
    fdf = pd.DataFrame({k: feat[k] for k in factors})
    fdf = fdf.loc[cp.columns[last.notna().values]]               # 入选:close 末值非空
    fdf = fdf.replace([np.inf, -np.inf], np.nan)
    z = _zscore_cols(fdf)
    return list(fdf.index), z.to_numpy(dtype=np.float32)


def build_corr_graph(close_panel: pd.DataFrame, date, codes, *, window: int = 60, topk: int = 20) -> np.ndarray:
    """≤date 末 window 日日收益 → codes 两两 Pearson 相关 → 每节点 topk 最相关邻居(|corr|, 排自身)
    → 对称 0/1 邻接 + 自环。窗口不足(<5 日)/全空 → 单位阵(只自注意,诚实退化)。返回 (N,N) float32。"""
    n = len(codes)
    eye = np.eye(n, dtype=np.float32)
    cp = close_panel.loc[:pd.Timestamp(date), list(codes)]
    rets = cp.pct_change(fill_method=None).tail(window)
    if len(rets) < 5:
        return eye
    # 相关矩阵走 numpy(去均值+归一化后 R^T·R = 完整窗 Pearson),比 pandas .corr() 在数千列时快 ~100×;
    # 缺值按"零偏差"计(同 kNN 鲁棒);常量/空列 → norm 0 → 该行全 0 → 后续零相关掩码留自环。
    R = rets.to_numpy(dtype="float64")                 # (T, N)
    R = np.nan_to_num(R - np.nanmean(R, axis=0, keepdims=True), nan=0.0)
    norm = np.sqrt((R * R).sum(axis=0))                # (N,)
    with np.errstate(divide="ignore", invalid="ignore"):
        Rn = np.nan_to_num(R / norm, nan=0.0, posinf=0.0, neginf=0.0)
    C = Rn.T @ Rn                                       # (N, N) Pearson(完整窗精确等价 pandas)
    if not np.isfinite(C).any() or not C.any():
        return eye
    np.fill_diagonal(C, 0.0)
    k = min(topk, n - 1)
    if k <= 0:
        return eye
    A = np.zeros((n, n), dtype=np.float32)
    order = np.argsort(-np.abs(C), axis=1)[:, :k]
    rows = np.repeat(np.arange(n), k)
    cols = order.reshape(-1)
    keep = np.abs(C)[rows, cols] > 0     # 退化节点(常量/全 NaN 序列→全零相关行)不连任意邻居,只留自环
    A[rows[keep], cols[keep]] = 1.0
    A = np.maximum(A, A.T)               # 对称化
    np.fill_diagonal(A, 1.0)             # 自环
    return A


def build_corr_neighbors(close_panel: pd.DataFrame, date, codes, *, window: int = 60, topk: int = 20) -> np.ndarray:
    """≤date 末 window 日收益相关 → 每节点 [自身 + top-topk 最相关邻居] 的索引 (N, K+1) int64(K=min(topk,N-1))。
    有向 kNN 稀疏表示(不对称化·省显存,全市场 6075 不爆),相关口径与 build_corr_graph 同(numpy);
    退化(零相关/常量序列)节点的邻居槽全填自身;窗口<5 日 → 全填自身。"""
    n = len(codes)
    self_idx = np.arange(n)
    K = min(topk, n - 1)
    out = np.tile(self_idx[:, None], (1, K + 1))          # (N, K+1) 默认全自身
    cp = close_panel.loc[:pd.Timestamp(date), list(codes)]
    rets = cp.pct_change(fill_method=None).tail(window)
    if len(rets) < 5 or K <= 0:
        return out
    R = rets.to_numpy(dtype="float64")
    R = np.nan_to_num(R - np.nanmean(R, axis=0, keepdims=True), nan=0.0)
    norm = np.sqrt((R * R).sum(axis=0))
    with np.errstate(divide="ignore", invalid="ignore"):
        Rn = np.nan_to_num(R / norm, nan=0.0, posinf=0.0, neginf=0.0)
    C = Rn.T @ Rn
    np.fill_diagonal(C, 0.0)
    order = np.argsort(-np.abs(C), axis=1)[:, :K]          # (N, K) 按 |corr| 取 topk
    keep = np.take_along_axis(np.abs(C), order, axis=1) > 0
    out[:, 1:] = np.where(keep, order, self_idx[:, None])  # 非邻居(零相关)槽 → 自身
    return out


def forward_label(close_panel: pd.DataFrame, date, codes, *, horizon: int = 5) -> np.ndarray:
    """codes 在 date 起未来 horizon 交易日收益 close[t+h]/close[t]-1(仅训练日可用:t+h ≤ 面板末日)。
    缺失/无未来 置 nan。返回 (N,) float32。"""
    idx = close_panel.index
    ts = pd.Timestamp(date)
    out = np.full(len(codes), np.nan, dtype=np.float32)
    pos = idx.searchsorted(ts)
    if pos >= len(idx) or idx[pos] != ts or pos + horizon >= len(idx):
        return out
    c0 = close_panel.loc[ts, list(codes)].to_numpy(dtype="float64")
    c1 = close_panel.loc[idx[pos + horizon], list(codes)].to_numpy(dtype="float64")
    with np.errstate(divide="ignore", invalid="ignore"):
        r = c1 / c0 - 1.0
    r[~np.isfinite(r)] = np.nan
    out[:] = r.astype(np.float32)
    return out


def rebalance_dates(panel_index, *, horizon: int = 5, start=None) -> List[pd.Timestamp]:
    """从 start(缺省=首日)到 末日-horizon 的非重叠 horizon 日换仓训练日(标签已实现)。"""
    idx = pd.DatetimeIndex(panel_index)
    if start is not None:
        idx = idx[idx >= pd.Timestamp(start)]
    if len(idx) <= horizon:
        return []
    return list(idx[:-horizon][::horizon])
