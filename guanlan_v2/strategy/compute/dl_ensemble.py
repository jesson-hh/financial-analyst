# -*- coding: utf-8 -*-
"""统一深度学习集成层(多源)—— 把单源 FinCast B3 泛化成「N 个 DL 源加权 z 混合进 v4 score」。

**命门**(同 v4_fincast):只 pd.read_parquet 离线产出的预测表,绝不在此/任何 HTTP 请求里跑模型。
LGB 恒 ≥0.5 主导(总 DL 权重封顶 MAX_TOTAL_DL_W)。复用 v4_fincast 的 z/ICIR/自适应权重 helpers。
单源时与 v4_fincast.b3_mix_scores 字节等价(回归守护)。
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional, Tuple

import numpy as np
import pandas as pd

from guanlan_v2.strategy.compute.v4_fincast import (
    _zscore, recent_fc_icir, _adaptive_w_fc, DEFAULT_W_FC, MIN_MATCH,
)

MAX_TOTAL_DL_W = 0.5   # 总 DL 权重封顶 → w_lgb = 1 - Σwᵢ ≥ 0.5,LGB 主导


@dataclass
class DLSource:
    model_id: str
    path: str
    score_col: str = "pred_ret_5d"
    weight_mode: str = "adaptive"          # "adaptive"(按近期 ICIR)| "fixed"
    fixed_w: Optional[float] = None


def dl_mix_scores(score_lgb: pd.Series, dl_scores: dict, weights: dict,
                  min_match: int = MIN_MATCH) -> Tuple[pd.Series, dict]:
    """多源 z 混合:mixed = w_lgb·z(LGB) + Σ wᵢ·z(DLᵢ)。

    dl_scores: {model_id: Series};weights: {model_id: float(已 clip 好)}。
    每源 reindex 到 LGB 索引;非空 < min_match 或权重 ≤0 → 退出(weight=0)。
    活跃源总权重 > MAX_TOTAL_DL_W → 按比例缩到和为 MAX_TOTAL_DL_W。
    返回 (mixed, info{active, w_lgb, sources:[{model_id,active,weight,n_has,reason}]})。
    单源时与 b3_mix_scores 字节等价。"""
    src_info = []
    active = {}
    for mid, raw in dl_scores.items():
        s = raw.reindex(score_lgb.index)
        n_has = int(s.notna().sum())
        w_raw = float(weights.get(mid, 0.0))
        if n_has < min_match or w_raw <= 0:
            src_info.append({"model_id": mid, "active": False, "weight": 0.0, "n_has": n_has,
                             "reason": (f"匹配 {n_has} < {min_match},退出" if n_has < min_match else "权重 0")})
        else:
            active[mid] = (s, w_raw, n_has)
    total = sum(w for _, w, _ in active.values())
    scale = (MAX_TOTAL_DL_W / total) if total > MAX_TOTAL_DL_W else 1.0
    if not active:
        return score_lgb.copy(), {"active": False, "w_lgb": 1.0, "sources": src_info}
    w_lgb = 1.0 - sum(w * scale for _, w, _ in active.values())
    mixed = w_lgb * _zscore(score_lgb)
    for mid, (s, w_raw, n_has) in active.items():
        w = w_raw * scale
        mixed = mixed + w * _zscore(s.fillna(s.mean()))
        src_info.append({"model_id": mid, "active": True, "weight": w, "n_has": n_has,
                         "reason": f"w={w:.3f}({n_has} 只匹配)"})
    return mixed, {"active": True, "w_lgb": w_lgb, "sources": src_info}
