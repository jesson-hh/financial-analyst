# guanlan_v2/strategy/compute/model_train.py
"""v4 模型工坊:参数化训练变体(选因子)+ 留出 OOS IC。
不碰生产 v4(只写 models/<id>/);复用 build_v4 / compile_factor / 现成 IC 公式。"""
from __future__ import annotations

from typing import List, Optional, Tuple

import pandas as pd


def holdout_split(dates, ld, horizon: int = 5, k: int = 20) -> Tuple[pd.Timestamp, List[pd.Timestamp]]:
    """返回 (train_cutoff, holdout_dates)。label=未来 horizon 个【交易日】收益 → 最后 horizon 个交易日无 label;
    有 label 的最近 k 个交易日留作 OOS,train 截止=这些留出日的前一交易日。数据太短→holdout 空。"""
    uniq = [d for d in sorted(pd.Index(pd.to_datetime(pd.Series(dates))).unique()) if d <= pd.Timestamp(ld)]
    labeled = uniq[:-horizon] if len(uniq) > horizon else []   # 排除末 horizon 个【交易日】(positional)
    if len(labeled) <= k:
        return (labeled[-1] if labeled else (uniq[-1] if uniq else pd.Timestamp(ld))), []
    return labeled[-k - 1], labeled[-k:]
