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
                       keep_days: int = 60) -> pd.DataFrame:
    """写 FinCast 预测表(扁平契约 eval_date/instrument/pred_ret_5d):同日覆盖 + 只保留最近 keep_days 日。
    返回写入后的全表 DataFrame。"""
    ed = pd.Timestamp(eval_date)
    new_df = pd.DataFrame({"eval_date": ed, "instrument": list(chosen),
                           "pred_ret_5d": np.asarray(preds, dtype=np.float32)})
    if os.path.exists(out_path):
        old = pd.read_parquet(out_path)
        if "eval_date" not in old.columns:
            old = old.reset_index()
        old = old[pd.to_datetime(old["eval_date"]) != ed]      # 同日覆盖
        old = old[["eval_date", "instrument", "pred_ret_5d"]]
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
    combined.to_parquet(out_path, index=False)
    return combined
