# guanlan_v2/strategy/compute/model_train.py
"""v4 模型工坊:参数化训练变体(选因子)+ 留出 OOS IC。
不碰生产 v4(只写 models/<id>/);复用 build_v4 / compile_factor / 现成 IC 公式。"""
from __future__ import annotations

from typing import List, Optional, Tuple

import pandas as pd


def holdout_split(dates, ld, horizon: int = 5, k: int = 20,
                  label_span: Optional[int] = None) -> Tuple[pd.Timestamp, List[pd.Timestamp]]:
    """返回 (train_cutoff, holdout_dates)。label=未来 horizon 个【交易日】收益 → 最后 horizon 个交易日无 label;
    有 label 的最近 k 个交易日留作 OOS;train 截止再向前 purge horizon 个交易日,使训练样本的 label 窗口不与留出期重叠。"""
    span = max(1, int(label_span if label_span is not None else horizon))
    k = max(1, int(k))
    uniq = [d for d in sorted(pd.Index(pd.to_datetime(pd.Series(dates))).unique()) if d <= pd.Timestamp(ld)]
    labeled = uniq[:-span] if len(uniq) > span else []
    if len(labeled) <= k:
        return (labeled[-1] if labeled else (uniq[-1] if uniq else pd.Timestamp(ld))), []
    holdout = labeled[-k:]
    cut_idx = len(labeled) - k - 1 - span
    # No unpurged fallback: for a short history the cutoff precedes data, so
    # callers can report insufficient training rows rather than leak labels.
    train_cutoff = labeled[cut_idx] if cut_idx >= 0 else uniq[0] - pd.Timedelta(days=1)
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


def evaluate_library_factors(codes, factor_ids, start, end, definitions=None):
    """选中库因子 → (DataFrame[列=factor_id, index=instrument×datetime], unsupported列表)。
    复用 factor_ic.py 同款 compile_factor;无 expr/不在目录/求值失败 → unsupported,诚实缺席。"""
    defs = definitions if definitions is not None else _factor_defs()
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
    if getattr(panel, "df", None) is not None:
        out.attrs["technical_provenance"] = dict(panel.df.attrs.get("technical_provenance") or {})
    return out, unsup


DEFAULT_PROVIDER = "G:/stocks/stock_data/cn_data"


def _build_v4(*a, **kw):
    from guanlan_v2.strategy.compute.v4 import build_v4
    return build_v4(*a, **kw)


def _latest_date():
    from guanlan_v2.strategy.compute.regen import _latest_trade_date
    return _latest_trade_date(DEFAULT_PROVIDER)


def _list_codes(universe):
    if universe in ("all", "", None):
        from guanlan_v2.strategy.compute.breadth import list_all_instruments
        return list_all_instruments(DEFAULT_PROVIDER)
    from financial_analyst.data.universe import resolve_universe_codes
    return [str(c) for c in resolve_universe_codes(universe)]


def _base_feature_names():
    """v4 基础特征名(供前端〈v4 基础特征〉组)。小宇宙跑 build_feature_panel 取列名 + 注入列。"""
    from guanlan_v2.strategy.compute.v4 import _select_mf, build_feature_panel
    from financial_analyst.data.loaders.qlib_binary import QlibBinaryLoader
    end = _latest_date()
    start = (pd.Timestamp(end) - pd.Timedelta(days=400)).date().isoformat()
    panel = build_feature_panel(QlibBinaryLoader(DEFAULT_PROVIDER),
                                ["SH600519", "SZ000001", "SH600036"], start, end)
    base = set(_select_mf(list(panel.columns), None)) | {"ind_turnover", "lu_resid_pct60", "amt_resid_pct60"}
    return sorted(base)


def train_variant(variant_id, name, factor_ids, base_features, universe="all",
                  created="", holdout_k=20, status=None) -> dict:
    from guanlan_v2.screen import model_registry as reg
    end = _latest_date()
    start = "2022-01-01"
    codes = _list_codes(universe)
    extra, unsup = evaluate_library_factors(codes, factor_ids, start, end)
    feature_cols = resolve_feature_cols(
        list(_base_feature_names()) + list(extra.columns), base_features, list(extra.columns))
    hd = {"k": holdout_k, "horizon": 5, "label_span": 6}
    trained = {}
    df = _build_v4(DEFAULT_PROVIDER, start, end, codes=codes, feature_cols=feature_cols,
                   extra_factor_panel=(extra if len(extra.columns) else None), holdout=hd,
                   model_out=trained)
    meta = {"id": variant_id, "name": name, "factor_ids": list(factor_ids),
            "base_features": list(base_features), "n_features": len(feature_cols),
            "unsupported_factors": unsup, "universe": universe,
            "oos_ic": hd.get("oos_ic"), "oos_icir": hd.get("oos_icir"), "n_holdout": hd.get("n_holdout"),
            "asof": str(df["date"].iloc[0]) if len(df) else end, "created": created,
            "train_rows": hd.get("final_train_rows"), "ranking_rows": int(len(df)),
            "error": hd.get("error"), "source": "workshop", "kind": "v4-lgb",
            "recipe": {"schema": "v4-workshop-v1", "base_features": list(base_features),
                       "factor_ids": list(factor_ids), "universe": universe,
                       "factor_definitions": {fid: {"expr": (_factor_defs().get(fid) or {}).get("expr")}
                                              for fid in factor_ids if fid not in unsup},
                       "start": start, "end": end, "label_span": 6,
                       "label": "close(t+6)/close(t+1)-1", "holdout_k": holdout_k},
            "retrainable": True, "training_provenance": trained.get("provenance", {}),
            "evaluation": {key: value for key, value in hd.items()}}
    meta["training_provenance"]["technical_provenance"] = dict(extra.attrs.get("technical_provenance") or {})
    meta["training_provenance"]["v4_computed_features"] = {
        "source": "v4._factors_one", "version": "v4-v1",
        "amihud_20": "rolling_mean(abs(close/lag(close,1)-1)/(volume+1e-8),20)"}
    if status:
        meta["status"] = str(status)
    if unsup or hd.get("error") or hd.get("oos_ic") is None:
        meta["status"] = "draft"
    reg.save_variant(variant_id, df, meta, estimator=trained.get("estimator"),
                     preprocessing=trained.get("preprocessing"))
    return {"ok": True, "variant_id": variant_id, "meta": meta}


if __name__ == "__main__":   # 子进程入口:python -m ...model_train <spec.json>
    import json, sys
    from guanlan_v2.strategy.compute.training_runtime import bootstrap_training_engine
    bootstrap_training_engine()
    spec = json.loads(open(sys.argv[1], encoding="utf-8").read())
    print(f"[model_train] variant={spec['variant_id']} factors={len(spec.get('factor_ids', []))} ...", flush=True)
    r = train_variant(**spec)
    print(f"[model_train] done oos_ic={r['meta'].get('oos_ic')}", flush=True)
