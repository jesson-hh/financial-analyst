# guanlan_v2/strategy/compute/model_train.py
"""v4 模型工坊:参数化训练变体(选因子)+ 留出 OOS IC。
不碰生产 v4(只写 models/<id>/);复用 build_v4 / compile_factor / 现成 IC 公式。"""
from __future__ import annotations

from typing import List, Optional, Tuple

import pandas as pd


def holdout_split(dates, ld, horizon: int = 5, k: int = 20) -> Tuple[pd.Timestamp, List[pd.Timestamp]]:
    """返回 (train_cutoff, holdout_dates)。label=未来 horizon 日收益 → 末 horizon 天无 label 不可用;
    有 label 的最近 k 个交易日留作 OOS,train 截止 = 这些留出日的前一交易日。数据太短 → holdout 空。"""
    uniq = sorted(pd.Index(pd.to_datetime(pd.Series(dates))).unique())
    ld = pd.Timestamp(ld)
    labeled = [d for d in uniq if d <= ld - pd.Timedelta(days=horizon)]
    if len(labeled) <= k:
        return (labeled[-1] if labeled else ld), []
    return labeled[-k - 1], labeled[-k:]
