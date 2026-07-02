# guanlan_v2/strategy/compute/model_workflow.py
"""工作流模型「存入模型库」生产训练器:把工作流小规模训练器(workflow.api._materialize_xy
+ _build_model)升到生产规模(全市场/全窗口·树模型)→ 出全截面每日排名 → 入 model_registry。
首期 kind ∈ {lightgbm, xgboost, rf}(v4-lgb 走老 model_train,不在此)。不碰 /screen 选股算法。"""
from __future__ import annotations

from typing import Any, Dict


_TREE_KINDS = ("lightgbm", "xgboost", "rf")


def _apply_promote_gate(meta: Dict[str, Any], oos_ic) -> Dict[str, Any]:
    """P1 §5 opt-in 阈值门:env GUANLAN_PROMOTE_MIN_OOS_IC 设了才生效(缺省零行为变化)。
    不达标(含 oos_ic=None)→ meta.status="draft"(不进正式列表/不能设默认);达标记 passed。
    门只拦「不合格自动进正式货架」;采纳(设默认)永远人工确认。"""
    import os
    raw = os.environ.get("GUANLAN_PROMOTE_MIN_OOS_IC")
    if not raw:
        return meta
    try:
        gate = float(raw)
    except ValueError:
        print(f"[model_promote] warn: GUANLAN_PROMOTE_MIN_OOS_IC 非法值 '{raw}',门未启用", flush=True)
        return meta
    passed = (oos_ic is not None) and (float(oos_ic) >= gate)
    meta["gate"] = {"min_oos_ic": gate, "oos_ic": oos_ic, "passed": bool(passed)}
    if not passed:
        meta["status"] = "draft"
    return meta


def train_promote(spec: Dict[str, Any]) -> Dict[str, Any]:
    """spec={variant_id,name,kind,recipe:{features,label,fwd_days,universe,start,end,params},created}
    → 全窗口 fit → 最新截面预测 → lgb_pct 分位排名 → 真OOS留出IC → save_variant(source=workflow)。
    任一失败返回 {ok:False, reason}(不入库,诚实)。"""
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

    fwd_days = int(recipe.get("fwd_days") or 5)
    end = recipe.get("end")
    if not end:
        from guanlan_v2.strategy.compute.regen import _latest_trade_date
        from guanlan_v2.strategy.compute.model_train import DEFAULT_PROVIDER
        end = _latest_trade_date(DEFAULT_PROVIDER)
    body = ModelTrainIn(
        kind=kind, features=feats, label=recipe.get("label") or "fwd_ret",
        fwd_days=fwd_days, universe=str(recipe.get("universe") or "all"),
        start=recipe.get("start") or "2022-01-01", end=end,
        params=dict(recipe.get("params") or {}), winsorize=True, standardize=True,
        codes=recipe.get("codes"), benchmark=recipe.get("benchmark"),
        leader=recipe.get("leader"), freq=recipe.get("freq") or "day",
    )

    mat = _materialize_xy(body, body.universe, feats, body.start, end)
    if not isinstance(mat, tuple):
        return {"ok": False, "reason": _jsonresp_reason(mat)}
    panel, fe_df, label_s, feature_names = mat

    X = fe_df.dropna()
    y = label_s.reindex(X.index).dropna()
    X = X.reindex(y.index)
    if len(X) < 500:
        return {"ok": False, "reason": f"训练样本太少({len(X)})"}

    try:
        model, hyper = _build_model(kind, body.params)
        model.fit(X.values, y.values)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "reason": f"训练失败: {type(exc).__name__}: {exc}"}

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

    oos_ic = _holdout_oos_ic(kind, body.params, fe_df, label_s, fwd_days=fwd_days, frac=0.2)
    meta = {
        "id": spec["variant_id"], "name": spec.get("name") or "工作流模型",
        "source": "workflow", "kind": kind, "recipe": recipe, "retrainable": True,
        "oos_ic": oos_ic, "n_features": len(feature_names),
        "universe": body.universe, "asof": rank_df["date"].iloc[0],
        "created": spec.get("created") or "", "hyper": hyper,
    }
    meta = _apply_promote_gate(meta, oos_ic)
    try:
        reg.save_variant(spec["variant_id"], rank_df, meta)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "reason": f"入库失败: {type(exc).__name__}: {exc}"}
    res: Dict[str, Any] = {"ok": True, "variant_id": spec["variant_id"], "oos_ic": oos_ic}
    if meta.get("status") == "draft":
        res["status"] = "draft"
        res["gate"] = meta.get("gate")
    return res


def _jsonresp_reason(resp) -> str:
    """从 _materialize_xy 失败 JSONResponse 抽真 reason(诚实透传)。"""
    import json
    try:
        return json.loads(resp.body).get("reason", "materialize 失败")
    except Exception:  # noqa: BLE001
        return "materialize 失败(universe/特征/标签求值)"


def _holdout_oos_ic(kind, params, fe_df, label_s, fwd_days=5, frac=0.2):
    """真 OOS:末 frac 调仓日留出 + purge fwd_days 防标签泄漏;**新建模型只在训练段 fit**、
    在留出段预测算逐日 rank-IC 均值(不复用全窗口模型→非样本内)。样本不足/失败 → None(诚实)。"""
    import numpy as np, pandas as pd
    try:
        from guanlan_v2.workflow.api import _build_model
        dts_all = pd.DatetimeIndex(sorted(set(fe_df.index.get_level_values("datetime"))))
        if len(dts_all) < 30:
            return None
        n_hold = max(1, int(len(dts_all) * frac))
        train_cut = len(dts_all) - n_hold - fwd_days   # purge fwd_days
        if train_cut < 10:
            return None
        train_dates = set(dts_all[:train_cut])
        hold_dates = list(dts_all[-n_hold:])
        dcol = fe_df.index.get_level_values("datetime")
        Xtr = fe_df[pd.Index(dcol).isin(train_dates)].dropna()
        ytr = label_s.reindex(Xtr.index).dropna()
        Xtr = Xtr.reindex(ytr.index)
        if len(Xtr) < 500:
            return None
        m, _ = _build_model(kind, params)
        m.fit(Xtr.values, ytr.values)
        ics = []
        for d in hold_dates:
            xi = fe_df[dcol == d].dropna()
            yi = label_s.reindex(xi.index).dropna()
            xi = xi.reindex(yi.index)
            if len(xi) < 30:
                continue
            p = pd.Series(m.predict(xi.values), index=xi.index)
            ic = p.rank().corr(yi.rank())
            if np.isfinite(ic):
                ics.append(float(ic))
        return round(float(np.mean(ics)), 4) if ics else None
    except Exception:  # noqa: BLE001
        return None


if __name__ == "__main__":   # python -m guanlan_v2.strategy.compute.model_workflow <spec.json>
    import json, sys
    spec = json.loads(open(sys.argv[1], encoding="utf-8").read())
    print(f"[model_promote] variant={spec['variant_id']} kind={spec.get('kind')} ...", flush=True)
    r = train_promote(spec)
    print(f"[model_promote] done ok={r.get('ok')} oos_ic={r.get('oos_ic')} "
          f"status={r.get('status') or 'ok'} reason={r.get('reason')}", flush=True)
    sys.exit(0 if r.get("ok") else 1)     # 失败非零退出码(供父进程状态机判 ok)
