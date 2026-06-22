# guanlan_v2/strategy/compute/model_workflow.py
"""工作流模型「存入模型库」生产训练器:把工作流小规模训练器(workflow.api._materialize_xy
+ _build_model)升到生产规模(全市场/全窗口·树模型)→ 出全截面每日排名 → 入 model_registry。
首期 kind ∈ {lightgbm, xgboost, rf}(v4-lgb 走老 model_train,不在此)。不碰 /screen 选股算法。"""
from __future__ import annotations

from typing import Any, Dict


_TREE_KINDS = ("lightgbm", "xgboost", "rf")


def train_promote(spec: Dict[str, Any]) -> Dict[str, Any]:
    """spec={variant_id,name,kind,recipe:{features,label,fwd_days,universe,start,end,params},created}
    → 全窗口 fit → 最新截面预测 → lgb_pct 分位排名 → save_variant(source=workflow)。
    失败返回 {ok:False, reason}(不入库,诚实)。"""
    import pandas as pd
    from guanlan_v2.screen import model_registry as reg
    from guanlan_v2.workflow.api import ModelTrainIn, _materialize_xy, _build_model

    kind = str(spec.get("kind") or "").strip()
    if kind not in _TREE_KINDS:
        return {"ok": False, "reason": f"kind '{kind}' 暂不支持生产入库(首期树模型 {_TREE_KINDS})"}
    recipe = dict(spec.get("recipe") or {})
    feats = list(recipe.get("features") or [])
    if not feats:
        return {"ok": False, "reason": "recipe.features 为空"}

    body = ModelTrainIn(
        kind=kind, features=feats, label=recipe.get("label") or "fwd_ret",
        fwd_days=int(recipe.get("fwd_days") or 5),
        universe=str(recipe.get("universe") or "all"),
        start=recipe.get("start") or "2022-01-01", end=recipe.get("end"),
        params=dict(recipe.get("params") or {}), winsorize=True, standardize=True,
    )
    if not body.end:
        from guanlan_v2.strategy.compute.regen import _latest_trade_date
        from guanlan_v2.strategy.compute.model_train import DEFAULT_PROVIDER
        body.end = _latest_trade_date(DEFAULT_PROVIDER)

    mat = _materialize_xy(body, body.universe, feats, body.start, body.end)
    if not isinstance(mat, tuple):
        return {"ok": False, "reason": "materialize 失败(universe/特征/标签求值)"}
    panel, fe_df, label_s, feature_names = mat

    X = fe_df.dropna()
    y = label_s.reindex(X.index).dropna()
    X = X.reindex(y.index)
    if len(X) < 500:
        return {"ok": False, "reason": f"训练样本太少({len(X)})"}

    model, hyper = _build_model(kind, body.params)
    model.fit(X.values, y.values)

    dts = fe_df.index.get_level_values("datetime")
    last = dts.max()
    x_last = fe_df[dts == last].dropna()
    if x_last.empty:
        return {"ok": False, "reason": "最新截面无可预测样本"}
    pred = pd.Series(model.predict(x_last.values), index=x_last.index)
    codes = x_last.index.get_level_values("code") if "code" in x_last.index.names \
        else x_last.index.get_level_values(-1)
    rank_df = pd.DataFrame({
        "code": [str(c) for c in codes],
        "date": pd.Timestamp(last).date().isoformat(),
        "lgb_pct": pred.rank(pct=True).values,
    })

    oos_ic = _oos_rank_ic(model, fe_df, label_s, frac=0.2)
    meta = {
        "id": spec["variant_id"], "name": spec.get("name") or "工作流模型",
        "source": "workflow", "kind": kind, "recipe": recipe, "retrainable": True,
        "oos_ic": oos_ic, "n_features": len(feature_names),
        "universe": body.universe, "asof": rank_df["date"].iloc[0],
        "created": spec.get("created") or "", "hyper": hyper,
    }
    reg.save_variant(spec["variant_id"], rank_df, meta)
    return {"ok": True, "variant_id": spec["variant_id"], "oos_ic": oos_ic}


def _oos_rank_ic(model, fe_df, label_s, frac: float = 0.2):
    """末 frac 调仓日做 OOS:逐日 spearman(pred, fwd) 取均值。失败/无样本 → None(诚实)。"""
    import numpy as np, pandas as pd
    try:
        dts = pd.DatetimeIndex(sorted(set(fe_df.index.get_level_values("datetime"))))
        if len(dts) < 10:
            return None
        cut = dts[int(len(dts) * (1 - frac))]
        ics = []
        for d in dts[dts >= cut]:
            xi = fe_df[fe_df.index.get_level_values("datetime") == d].dropna()
            yi = label_s.reindex(xi.index).dropna()
            xi = xi.reindex(yi.index)
            if len(xi) < 30:
                continue
            p = pd.Series(model.predict(xi.values), index=xi.index)
            ic = p.rank().corr(yi.rank())
            if np.isfinite(ic):
                ics.append(float(ic))
        return round(float(np.mean(ics)), 4) if ics else None
    except Exception:  # noqa: BLE001
        return None
