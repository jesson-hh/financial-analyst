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


def _load_dl_for_date(path: str, ld: pd.Timestamp, score_col: str = "pred_ret_5d"):
    """读 DL 预测 parquet → (当日 series[instrument→score], 全表 df, train_cutoff, reason_if_fail)。
    泛化 v4_fincast._load_fincast_for_date(列名 score_col 参数化)。缺文件/缺列/无当日/读失败 → None+reason。"""
    if not path or not os.path.exists(path):
        return None, None, None, "预测文件不存在,退出(离线产出:见 scripts/fincast_predict.py 同款工具)"
    try:
        df = pd.read_parquet(path)
    except Exception as e:  # noqa: BLE001
        return None, None, None, f"预测 parquet 读取失败({type(e).__name__}),退出"
    need = {"eval_date", "instrument", score_col}
    if not need.issubset(df.columns):
        try:
            df = df.reset_index()
        except Exception:  # noqa: BLE001
            pass
    if not need.issubset(df.columns):
        return None, None, None, f"预测 parquet 缺 {need} 列,退出"
    cutoff = None
    if "train_cutoff" in df.columns and len(df):
        try:
            cutoff = str(pd.Timestamp(df["train_cutoff"].iloc[0]).date())
        except Exception:  # noqa: BLE001
            cutoff = None
    ev = pd.to_datetime(df["eval_date"]).dt.normalize()
    today = pd.Timestamp(ld).normalize()
    sub = df[ev == today]
    if sub.empty:
        return None, df, cutoff, f"无 {today.date()} 预测,退出"
    s = sub.set_index("instrument")[score_col]
    s = s[~s.index.duplicated(keep="last")]
    return s, df, cutoff, None


def default_dl_sources() -> list:
    """Phase 1 默认 DL 源注册表:仅 FinCast(沿用现有 var/v4_fincast_pred.parquet)。
    Phase 2/3 加 LSTM 等:在此 append 一个 DLSource(指向 var/dl_pred_<model_id>.parquet)即接入。"""
    from pathlib import Path
    var = Path(__file__).resolve().parents[3] / "var"
    return [
        DLSource(model_id="fincast", path=str(var / "v4_fincast_pred.parquet"),
                 score_col="pred_ret_5d", weight_mode="adaptive"),
        # Phase 2: DLSource(model_id="lstm", path=str(var / "dl_pred_lstm.parquet"), ...)
    ]


def apply_dl_ensemble(pred: pd.DataFrame, ld: pd.Timestamp, sources: list,
                      data: Optional[pd.DataFrame] = None, min_match: int = MIN_MATCH) -> dict:
    """对 build_v4 末日截面 pred(MultiIndex (instrument, datetime),含 'score')就地多源混合。
    只读每个源的 parquet;有效源加权 z 混合写回 pred['score'];无则诚实退纯 LGB。返回 provenance。"""
    info = {"date": str(pd.Timestamp(ld).date()), "active": False, "w_lgb": 1.0,
            "sources": [], "reason": None}
    inst = pred.index.get_level_values("instrument")
    lgb_by_inst = pd.Series(pred["score"].values, index=inst)
    dl_scores, weights, meta, missing = {}, {}, {}, []
    for src in sources:
        s, df, cutoff, fail = _load_dl_for_date(src.path, ld, src.score_col)
        if fail is not None:
            missing.append({"model_id": src.model_id, "active": False, "weight": 0.0,
                            "n_has": 0, "lookahead": None, "reason": fail})
            continue
        if src.weight_mode == "fixed" and src.fixed_w is not None:
            w, icir = float(src.fixed_w), None
        else:
            icir = None
            if data is not None and df is not None and "label" in getattr(data, "columns", []):
                icir = recent_fc_icir(df, data["label"], ld)
            w = _adaptive_w_fc(icir)
        look = (str(pd.Timestamp(ld).date()) <= cutoff) if cutoff is not None else None
        dl_scores[src.model_id] = s
        weights[src.model_id] = w
        meta[src.model_id] = {"lookahead": look, "fc_icir_recent": icir}
    if not dl_scores:
        info["sources"] = missing
        info["reason"] = "无可用 DL 源(全部缺文件/无当日预测),纯 LGB"
        return info
    mixed, mix = dl_mix_scores(lgb_by_inst, dl_scores, weights, min_match=min_match)
    for s in mix["sources"]:
        m = meta.get(s["model_id"], {})
        s["lookahead"] = m.get("lookahead")
        s["fc_icir_recent"] = m.get("fc_icir_recent")
    info["sources"] = mix["sources"] + missing
    info["w_lgb"] = mix["w_lgb"]
    info["active"] = mix["active"]
    if mix["active"]:
        pred["score"] = mixed.reindex(inst).values
        info["reason"] = ("DL 集成:LGB %.2f + " % mix["w_lgb"]) + "、".join(
            f"{s['model_id']} {s['weight']:.2f}" for s in mix["sources"] if s.get("active"))
    else:
        info["reason"] = "所有 DL 源退化,纯 LGB"
    return info
