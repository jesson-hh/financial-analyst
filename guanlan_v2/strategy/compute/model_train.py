# guanlan_v2/strategy/compute/model_train.py
"""v4 模型工坊:参数化训练变体(选因子)+ 留出 OOS IC。
不碰生产 v4(只写 models/<id>/);复用 build_v4 / compile_factor / 现成 IC 公式。"""
from __future__ import annotations

from typing import List, Optional, Tuple

import pandas as pd


def holdout_split(dates, ld, horizon: int = 5, k: int = 20) -> Tuple[pd.Timestamp, List[pd.Timestamp]]:
    """返回 (train_cutoff, holdout_dates)。label=未来 horizon 个【交易日】收益 → 最后 horizon 个交易日无 label;
    有 label 的最近 k 个交易日留作 OOS;train 截止再向前 purge horizon 个交易日,使训练样本的 label 窗口不与留出期重叠。"""
    uniq = [d for d in sorted(pd.Index(pd.to_datetime(pd.Series(dates))).unique()) if d <= pd.Timestamp(ld)]
    labeled = uniq[:-horizon] if len(uniq) > horizon else []   # 排除末 horizon 个【交易日】(positional)
    if len(labeled) <= k:
        return (labeled[-1] if labeled else (uniq[-1] if uniq else pd.Timestamp(ld))), []
    holdout = labeled[-k:]
    cut_idx = len(labeled) - k - 1 - horizon          # purge:留出窗前再退 horizon 个交易日
    train_cutoff = labeled[cut_idx] if cut_idx >= 0 else labeled[0]
    return train_cutoff, holdout


NON_FEATURE = {"label", "pe_ttm", "pb", "total_mv", "ps_ttm_raw"}


def resolve_feature_cols(available, base_features: List[str], factor_ids: List[str]) -> List[str]:
    """最终训练特征列 = (选中基础 ∪ 选中库因子),必须在 available 且非 label/估值原始列。
    顺序稳定(基础在前);全空 → ValueError(至少选 1 个)。"""
    av = set(available)
    picked = [c for c in base_features if c in av and c not in NON_FEATURE]
    picked += [c for c in factor_ids if c in av and c not in picked]
    if not picked:
        raise ValueError("至少选 1 个可用因子(基础或库因子)")
    return picked


def _factor_defs():
    from guanlan_v2.screen.catalog import FACTOR_DEFS
    return FACTOR_DEFS


def _compile_factor(expr):
    from financial_analyst.factors.zoo.expr import compile_factor
    return compile_factor(expr)


def _load_panel(codes, start, end):
    from financial_analyst.data.loader_factory import get_default_loader
    from financial_analyst.factors.zoo.panel_cache import load_panel_cached
    return load_panel_cached(get_default_loader(), codes, start, end, freq="day")


def evaluate_library_factors(codes, factor_ids, start, end):
    """选中库因子 → (DataFrame[列=factor_id, index=instrument×datetime], unsupported列表)。
    复用 factor_ic.py 同款 compile_factor;无 expr/不在目录/求值失败 → unsupported,诚实缺席。"""
    defs = _factor_defs()
    panel = None
    cols, unsup = {}, []
    for fid in factor_ids:
        expr = (defs.get(fid) or {}).get("expr")
        if not expr:
            unsup.append(fid); continue
        if panel is None:
            panel = _load_panel([str(c) for c in codes], start, end)
        try:
            s = _compile_factor(expr)(panel)
            if s is None or not hasattr(s, "index"):
                unsup.append(fid); continue
            cols[fid] = s
        except Exception:  # noqa: BLE001
            unsup.append(fid)
    if not cols:
        return pd.DataFrame(), unsup
    out = pd.DataFrame(cols)
    # 引擎真实 PanelData 索引为 (datetime, code);build_v4 面板是 (instrument, datetime)。
    # 按【级名】归一(不靠位置):code→instrument,再 reorder 成 (instrument, datetime) 便于 join。
    out.index = out.index.set_names(["instrument" if n == "code" else n for n in out.index.names])
    if set(out.index.names) == {"instrument", "datetime"}:
        out = out.reorder_levels(["instrument", "datetime"]).sort_index()
    else:  # 兜底:未知索引名 → 按位置命名(2 级假定 instrument,datetime)
        out.index = out.index.set_names((["instrument", "datetime"])[: out.index.nlevels])
    return out, unsup
