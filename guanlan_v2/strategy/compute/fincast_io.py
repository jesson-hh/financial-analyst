# -*- coding: utf-8 -*-
"""FinCast 港移纯函数 helper:close 面板→context 矩阵 + 预测表 rolling-keep 写出。
无 GPU/无引擎依赖(只 numpy/pandas),guanlan 主env 与 conda stocks 都可 import。
"""
from __future__ import annotations

import os
from typing import List, Tuple

import numpy as np
import pandas as pd


def build_context_matrix(panel: pd.DataFrame, eval_date, context_len: int = 512,
                         min_valid_frac: float = 0.9) -> Tuple[List[str], np.ndarray]:
    """close 面板(datetime 索引 × instrument 列)→ (chosen 标的, (N×context_len) float32 矩阵)。
    截到 ≤ eval_date(不看未来),取末 context_len 日;末值非空 & 非NaN比例≥min_valid_frac 才入选;
    ffill→bfill 补窗内洞。面板长度 < context_len 抛 ValueError。"""
    panel = panel.loc[:pd.Timestamp(eval_date)]
    if len(panel) < context_len:
        raise ValueError(f"面板长度 {len(panel)} < context_len {context_len};多拉历史")
    window = panel.tail(context_len)
    last_row = window.iloc[-1]
    valid_frac = window.notna().mean(axis=0)
    mask = last_row.notna() & (valid_frac >= min_valid_frac)
    chosen = window.columns[mask].tolist()
    if not chosen:
        raise ValueError("无有效标的(末值非空 + 非NaN比例达标)")
    sub = window[chosen].ffill().bfill()
    arr = sub.to_numpy(dtype=np.float32).T   # (N, T)
    return chosen, arr


def write_pred_rolling(out_path: str, eval_date, chosen: List[str], preds,
                       keep_days: int = 60, train_cutoff=None) -> pd.DataFrame:
    """写 FinCast 预测表(扁平契约 eval_date/instrument/pred_ret_5d):同日覆盖 + 只保留最近 keep_days 日。
    返回写入后的全表 DataFrame。"""
    ed = pd.Timestamp(eval_date)
    insts = list(chosen)
    # 显式 [ed]*n 广播:pandas 2.1(conda stocks)不会把标量 Timestamp 在 dict 构造里广播到数组长度
    # (报 "Shape of passed values is (1,N)"),新版会;显式列表跨 pandas 版本稳。
    cols = {"eval_date": [ed] * len(insts), "instrument": insts,
            "pred_ret_5d": np.asarray(preds, dtype=np.float32)}
    if train_cutoff is not None:                               # LSTM 等训练源诚实显形 train_cutoff
        cols["train_cutoff"] = [pd.Timestamp(train_cutoff)] * len(insts)
    new_df = pd.DataFrame(cols)
    if os.path.exists(out_path):
        old = pd.read_parquet(out_path)
        if "eval_date" not in old.columns:
            old = old.reset_index()
        old = old[pd.to_datetime(old["eval_date"]) != ed]      # 同日覆盖
        keep_cols = ["eval_date", "instrument", "pred_ret_5d"]
        if "train_cutoff" in old.columns and train_cutoff is not None:
            keep_cols.append("train_cutoff")
        old = old[[c for c in keep_cols if c in old.columns]]
        frames = [f for f in (old, new_df) if not f.empty]     # 防空帧 concat 的 FutureWarning
        combined = pd.concat(frames, ignore_index=True) if frames else new_df
        dates = sorted(pd.to_datetime(combined["eval_date"]).unique())
        if len(dates) > keep_days:
            keep = set(pd.to_datetime(dates[-keep_days:]))
            combined = combined[pd.to_datetime(combined["eval_date"]).isin(keep)]
    else:
        combined = new_df
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    combined = combined.reset_index(drop=True)
    # 强制 datetime64[ns]:标量 Timestamp 广播在 pandas 2.1/pyarrow22(conda stocks)会落 object 列,
    # pyarrow 无法序列化("Expected bytes, got a 'Timestamp'");显式 to_datetime 跨 env 稳。
    combined["eval_date"] = pd.to_datetime(combined["eval_date"])
    if "train_cutoff" in combined.columns:                    # 同 eval_date:防 train_cutoff 落 object
        combined["train_cutoff"] = pd.to_datetime(combined["train_cutoff"])
    combined.to_parquet(out_path, index=False)
    return combined
