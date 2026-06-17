# -*- coding: utf-8 -*-
"""#7 FinCast/FM 在线化 —— v4 「B3 集成」读取侧(纯 pandas,**绝不跑模型**)。

把 dormant qlib ``vendor/v4_ranking.py:195-273`` 的 B3 集成(LGB + FinCast z-score 加权)
迁到引擎原生 ``compute/v4.py:build_v4``。**命门**:本模块只 ``pd.read_parquet`` 一个
**离线 GPU 批算产出**的 FinCast 预测表,绝不在此(更不在任何 HTTP 请求里)跑 GPU 模型。
``build_v4`` 本身由 ``regen.py`` 离线再生时调用,在线服务只读其落盘的 ``v4_ranking_latest.parquet``。

诚实退化(向后兼容铁律):FinCast 文件不存在 / 无当日预测 / 当日匹配 <50 只 / 读取异常
→ 一律退回**纯 LGB**(score 原样不动),并在 info 里说明原因 —— 与「FinCast 默认关」字节等价。

FinCast 预测表契约(扁平列,离线工具产出,见 ``seats/fm_backfill.py`` 同款离线范式):
  ``eval_date``  预测评估日(YYYY-MM-DD 字符串或 Timestamp)
  ``instrument`` 代码(与 build_v4 一致,如 ``SZ000001``)
  ``pred_ret_5d`` 未来 5 日收益预测(float)
PIT/look-ahead:ckpt 训练截止前的日期含模型 look-ahead,由产出方/调用方诚实标 ⚠
(本模块透传 parquet 里的可选 ``train_cutoff`` 列;无则 info.lookahead=None)。
"""
from __future__ import annotations

from typing import Optional, Tuple

import numpy as np
import pandas as pd

DEFAULT_W_FC = 0.4          # B3 v1 默认 FinCast 权重(w_lgb=0.6),退化口径
W_FC_LO, W_FC_HI = 0.1, 0.5  # B3 v2 自适应单边区间
MIN_MATCH = 50              # 当日 FinCast 匹配数下限(< 则退化纯 LGB)


def _zscore(s: pd.Series) -> pd.Series:
    """截面 z-score;std=0(常数腿)→ 全 0(不出 ±Inf/NaN),镜像 vendor `_z`。"""
    sd = s.std(skipna=True)
    if not (sd and sd > 0):
        return s * 0.0
    return (s - s.mean(skipna=True)) / (sd + 1e-9)


def b3_mix_scores(score_lgb: pd.Series, score_fincast: pd.Series,
                  w_fc: Optional[float] = None,
                  min_match: int = MIN_MATCH) -> Tuple[pd.Series, dict]:
    """B3 集成:``mixed = (1-w)·z(LGB) + w·z(FinCast)``。返回 (mixed, info)。

    - ``score_lgb`` / ``score_fincast`` 同 instrument 索引(后者可含 NaN / 缺项,内部对齐到前者)。
    - ``w_fc``:None → 默认 0.4;否则按 [0.1, 0.5] 夹(B3 v2 自适应权重已在外算好传入)。
    - 当日有效 FinCast(非 NaN)< ``min_match`` → 诚实退化:返回 ``score_lgb`` 原样、active=False。
    """
    fc = score_fincast.reindex(score_lgb.index)
    n_has = int(fc.notna().sum())
    n_total = int(len(score_lgb))
    if n_has < min_match:
        return score_lgb.copy(), {
            "active": False, "w_lgb": 1.0, "w_fc": 0.0,
            "n_has_fc": n_has, "n_total": n_total,
            "reason": f"FinCast 匹配 {n_has} < {min_match},退化纯 LGB",
        }
    w = DEFAULT_W_FC if w_fc is None else float(np.clip(w_fc, W_FC_LO, W_FC_HI))
    w_lgb = 1.0 - w
    z_lgb = _zscore(score_lgb)
    z_fc = _zscore(fc.fillna(fc.mean()))
    mixed = w_lgb * z_lgb + w * z_fc
    return mixed, {
        "active": True, "w_lgb": w_lgb, "w_fc": w,
        "n_has_fc": n_has, "n_total": n_total,
        "reason": f"B3 集成启用:w_LGB={w_lgb:.2f} + w_FC={w:.2f}({n_has}/{n_total} 只有 FinCast)",
    }


def recent_fc_icir(fc_df: pd.DataFrame, label_panel: pd.Series, ld: pd.Timestamp,
                   lookback: int = 25, skip: int = 5,
                   min_days: int = 10, min_obs: int = 30, min_ics: int = 5) -> Optional[float]:
    """FinCast 自身近 ~20 个交易日的截面 RankICIR(镜像 vendor:228-253)。

    ``fc_df``:扁平 FinCast 表(``eval_date``/``instrument``/``pred_ret_5d``)。
    ``label_panel``:特征面板的 ``label`` 列(MultiIndex (instrument, datetime),= t→t+5 真实收益)。
    取近 ``lookback`` 日去掉最后 ``skip`` 日(留未来收益);每日截面 Spearman IC;≥``min_ics`` 日才出
    ICIR=mean/std。任何缺失/异常 → None(调用方退默认权重)。
    """
    try:
        ev = pd.to_datetime(fc_df["eval_date"])
        dts = sorted(ev.dt.normalize().unique())
        recent = [d for d in dts if d < pd.Timestamp(ld).normalize()][-(lookback):]
        recent = recent[:-skip] if skip and len(recent) > skip else recent
        if len(recent) < min_days:
            return None
        ics = []
        for d in recent:
            fsub = fc_df[ev.dt.normalize() == d].set_index("instrument")["pred_ret_5d"]
            try:
                lsub = label_panel.xs(d, level="datetime")
            except Exception:  # noqa: BLE001 — 该日无标签
                continue
            j = pd.concat([fsub, lsub], axis=1, join="inner").dropna()
            if len(j) < min_obs:
                continue
            ic = j.iloc[:, 0].rank().corr(j.iloc[:, 1].rank())
            if pd.notna(ic):
                ics.append(float(ic))
        if len(ics) < min_ics:
            return None
        arr = np.asarray(ics, dtype=float)
        return float(arr.mean() / (arr.std() + 1e-9))
    except Exception:  # noqa: BLE001 — IC 估计绝不拖垮排名
        return None


def _adaptive_w_fc(fc_icir: Optional[float]) -> float:
    """ICIR → w_fc 的 logistic 映射(vendor:252):ICIR 0→0.25、+0.5→~0.4、−0.5→~0.1,夹 [0.1,0.5]。"""
    if fc_icir is None:
        return DEFAULT_W_FC
    return float(np.clip(DEFAULT_W_FC / (1 + np.exp(-2 * fc_icir)), W_FC_LO, W_FC_HI))


def _load_fincast_for_date(fincast_path: str, ld: pd.Timestamp) -> Tuple[Optional[pd.Series], Optional[pd.DataFrame], Optional[str], Optional[str]]:
    """只读 parquet → (当日 fc_series[instrument→pred_ret_5d], 全表 fc_df, train_cutoff, reason_if_fail)。"""
    import os
    if not fincast_path or not os.path.exists(fincast_path):
        return None, None, None, "FinCast 预测文件不存在,退化纯 LGB(离线产出:见 seats/fm_backfill.py 同款工具)"
    try:
        fc_df = pd.read_parquet(fincast_path)
    except Exception as e:  # noqa: BLE001
        return None, None, None, f"FinCast parquet 读取失败({type(e).__name__}),退化纯 LGB"
    # 支持 MultiIndex(含 eval_date 级)或扁平列两种落盘形态 → 统一成扁平列
    if not {"eval_date", "instrument", "pred_ret_5d"}.issubset(fc_df.columns):
        try:
            fc_df = fc_df.reset_index()
        except Exception:  # noqa: BLE001
            pass
    if not {"eval_date", "instrument", "pred_ret_5d"}.issubset(fc_df.columns):
        return None, None, None, "FinCast parquet 缺 eval_date/instrument/pred_ret_5d 列,退化纯 LGB"
    cutoff = None
    if "train_cutoff" in fc_df.columns and len(fc_df):
        try:
            cutoff = str(pd.Timestamp(fc_df["train_cutoff"].iloc[0]).date())
        except Exception:  # noqa: BLE001
            cutoff = None
    ev_norm = pd.to_datetime(fc_df["eval_date"]).dt.normalize()
    today = pd.Timestamp(ld).normalize()
    sub = fc_df[ev_norm == today]
    if sub.empty:
        return None, fc_df, cutoff, f"FinCast 无 {today.date()} 预测,退化纯 LGB"
    fc_series = sub.set_index("instrument")["pred_ret_5d"]
    fc_series = fc_series[~fc_series.index.duplicated(keep="last")]
    return fc_series, fc_df, cutoff, None


def apply_fincast_ensemble(pred: pd.DataFrame, ld: pd.Timestamp, fincast_path: Optional[str],
                           data: Optional[pd.DataFrame] = None,
                           min_match: int = MIN_MATCH) -> dict:
    """对 ``pred``(build_v4 末日截面,MultiIndex (instrument, datetime),含 'score' 列)就地
    应用 B3 集成:只读 ``fincast_path`` parquet,有当日 FinCast 且匹配 ≥min_match → 把 'score'
    原地替换为混合分;否则诚实退化(pred 不动)。返回 provenance info。

    ``data``(可选):build_v4 的特征面板(含 'label'),给则按 FinCast 近期 RankICIR 自适应 w_fc;
    不给则用默认 0.4。返回 info 供 regen/serving 落盘 + UI 诚实徽章。
    """
    info = {"active": False, "w_lgb": 1.0, "w_fc": 0.0, "n_has_fc": 0,
            "n_total": int(len(pred)), "fc_icir_recent": None,
            "lookahead": None, "reason": None, "eval_date": str(pd.Timestamp(ld).date())}

    fc_series, fc_df, cutoff, fail = _load_fincast_for_date(fincast_path, ld)
    if fail is not None:
        info["reason"] = fail
        return info
    if cutoff is not None:
        info["lookahead"] = (str(pd.Timestamp(ld).date()) <= cutoff)
        info["train_cutoff"] = cutoff

    # build_v4 的 pred 同一末日 → instrument 唯一;抽成 instrument 索引的 LGB 分
    inst = pred.index.get_level_values("instrument")
    lgb_by_inst = pd.Series(pred["score"].values, index=inst)

    # 自适应 w_fc(给了 data 才算;否则默认 0.4)
    fc_icir = None
    if data is not None and fc_df is not None and "label" in getattr(data, "columns", []):
        fc_icir = recent_fc_icir(fc_df, data["label"], ld)
    info["fc_icir_recent"] = fc_icir
    w_fc = _adaptive_w_fc(fc_icir)

    mixed, mix_info = b3_mix_scores(lgb_by_inst, fc_series, w_fc=w_fc, min_match=min_match)
    info.update({k: mix_info[k] for k in ("active", "w_lgb", "w_fc", "n_has_fc")})
    if not mix_info["active"]:
        info["reason"] = mix_info["reason"]
        return info

    # 写回 pred['score'](按 instrument 对齐回 MultiIndex 顺序)
    pred["score"] = mixed.reindex(inst).values
    info["reason"] = mix_info["reason"]
    return info
