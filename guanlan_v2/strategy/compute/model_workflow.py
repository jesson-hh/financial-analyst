# guanlan_v2/strategy/compute/model_workflow.py
"""工作流模型「存入模型库」生产训练器:把工作流小规模训练器(workflow.api._materialize_xy
+ _build_model)升到生产规模(全市场/全窗口·树模型)→ 出全截面每日排名 → 入 model_registry。
首期 kind ∈ {lightgbm, xgboost, rf}(v4-lgb 走老 model_train,不在此)。不碰 /screen 选股算法。"""
from __future__ import annotations

from typing import Any, Dict


_TREE_KINDS = ("lightgbm", "xgboost", "rf")


def _persist_technical_manifest(variant_id, panel, summary):
    """Stage complete audit facts outside pandas before the model meta commit.

    The content-named JSON is immutable by identity, like estimator artifacts.
    Summary-only derived panels are explicit; a digest cannot recreate facts.
    """
    import hashlib
    import json
    import os
    from pathlib import Path
    import tempfile
    from guanlan_v2.screen import model_registry as reg

    manifest = getattr(panel, "technical_provenance_manifest", None)
    if manifest is None:
        return {"available": False, "reason": "full technical manifest unavailable; summary only"}
    if not isinstance(manifest, dict):
        raise ValueError("full technical manifest must be a JSON object")
    payload = json.dumps(manifest, sort_keys=True, ensure_ascii=True,
                         separators=(",", ":"), allow_nan=False).encode("utf-8")
    digest = hashlib.sha256(payload).hexdigest()
    expected = (summary or {}).get("manifest_sha256")
    if expected and expected != digest:
        raise ValueError("technical manifest changed since panel summary was computed")
    root = reg.MODELS_DIR.resolve()
    directory = (root / str(variant_id)).resolve()
    if not variant_id or variant_id == "prod" or directory == root or root not in directory.parents:
        raise ValueError("technical manifest path must belong to a non-prod model variant")
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"technical_manifest_{digest}.json"
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="wb", dir=directory,
                                         prefix=".technical-manifest-", suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
    return {"available": True, "file": target.name, "sha256": digest,
            "bytes": len(payload), "format": "canonical-json-v1",
            "scope": "full technical manifest including request/cache execution metadata"}


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
    label_token = str(recipe.get("label") or "fwd_ret").strip().lower()
    known_forward = label_token in {"", "ic", "fwd_ret", "fwd", "forward", "forward_return", "return", "ret"}
    if not known_forward and not recipe.get("label_span"):
        return {"ok": False, "reason": "自定义标签必须显式声明 label_span，避免标签跨期泄漏"}
    label_span = fwd_days if known_forward else max(fwd_days, int(recipe["label_span"]))
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

    dts = fe_df.index.get_level_values("datetime")
    last = dts.max()
    x_last = fe_df[dts == last].dropna()
    if x_last.empty:
        missing = [str(c) for c in fe_df.columns if fe_df[dts == last][c].isna().all()]
        return {"ok": False, "reason": "最新截面无可预测样本",
                "data_asof": str(pd.Timestamp(last).date()), "missing_latest_features": missing}

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

    pred = pd.Series(model.predict(x_last.values), index=x_last.index)
    codes = x_last.index.get_level_values("code") if "code" in x_last.index.names \
        else x_last.index.get_level_values(-1)
    rank_df = pd.DataFrame({
        "code": [str(c) for c in codes],
        "date": pd.Timestamp(last).date().isoformat(),
        "lgb_score": pred.values,   # 原始预测值(与 prod 契约 lgb_score 同位,别只留分位丢真值)
        "lgb_pct": pred.rank(pct=True).values,
    })
    # lgb_rank:按预测值降序名次(method="first" 并列稳定、1 起),与 prod 契约同语义
    rank_df["lgb_rank"] = rank_df["lgb_score"].rank(ascending=False, method="first").astype(int)
    # T2(2026-07-11):附着五维评级 → 7 列 == prod 契约 V4_COLUMNS,选股页②决策自然复活;
    # dims 缺失/跨日/异常 → 原样 3+2 列 + 诚实 reason(附着失败绝不 fail 训练)
    rank_df, v4info = _attach_v4_dims(rank_df)

    evaluation = {}
    oos_ic = _holdout_oos_ic(kind, body.params, fe_df, label_s,
                           fwd_days=label_span, frac=0.2, evidence=evaluation,
                           observation_index=getattr(getattr(panel, "df", None), "index", label_s.index))
    from guanlan_v2.strategy.compute.training_runtime import training_provenance
    preprocessing = {"schema": "workflow-cross-section-v1", "feature_names": feature_names,
                     "features": feats, "winsorize": True, "winsorize_q": body.winsorize_q,
                     "standardize": True, "scope": "within_each_date",
                     "missing_features": "drop_incomplete_rows"}
    provenance = training_provenance(
        fe_df, label_s, X.index, prediction_date=pd.Timestamp(last).date().isoformat(),
        label_spec={"expression": body.label, "horizon": fwd_days, "label_span": label_span},
        evaluation=evaluation, feature_expressions=feats, preprocessing=preprocessing)
    panel_df = getattr(panel, "df", None)
    if panel_df is not None:
        provenance["technical_provenance"] = dict(panel_df.attrs.get("technical_provenance") or {})
    meta = {
        "id": spec["variant_id"], "name": spec.get("name") or "工作流模型",
        "source": "workflow", "kind": kind, "recipe": recipe, "retrainable": True,
        "oos_ic": oos_ic, "n_features": len(feature_names),
        "universe": body.universe, "asof": rank_df["date"].iloc[0],
        "created": spec.get("created") or "", "hyper": hyper,
        "v4_rating": v4info,   # 五维附着结果(attached/dims_date/n_rated 或诚实 reason)
        "training_provenance": provenance, "train_rows": int(len(X)),
        "requested_data_end": str(end), "ranking_rows": int(len(rank_df)),
    }
    if spec.get("status"):                   # P4:调用方强制状态(研究回路恒 draft);门只降不升
        meta["status"] = str(spec["status"])
    meta = _apply_promote_gate(meta, oos_ic)
    try:
        provenance["technical_provenance_manifest"] = _persist_technical_manifest(
            spec["variant_id"], panel, provenance.get("technical_provenance"))
        reg.save_variant(spec["variant_id"], rank_df, meta, estimator=model,
                         preprocessing=preprocessing)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "reason": f"入库失败: {type(exc).__name__}: {exc}"}
    res: Dict[str, Any] = {"ok": True, "variant_id": spec["variant_id"], "oos_ic": oos_ic,
                           "v4_rating": v4info, "model_version": meta.get("model_version"),
                           "asof": meta["asof"], "training_provenance": provenance,
                           "estimator_artifact": meta.get("estimator_artifact")}
    if meta.get("status") == "draft":
        res["status"] = "draft"
        res["gate"] = meta.get("gate")
    return res


def _attach_v4_dims(rank_df):
    """变体排名附着五维评级(v4_total/v4_layer):3+2 列 → 7 列(== prod 契约 V4_COLUMNS)。

    为什么:变体 parquet 只有 code/date/lgb_pct(现加 lgb_score/lgb_rank)→ 选股页②决策
    恒空。四个非模型维(fs/ts/vs/ud)+市值分层来自 prod regen 顺手落的全截面侧产物
    v4_dims_latest.parquet(见 v4.compute_dims);model 维 ms=变体**自己的** lgb_pct 分位
    (rp=1-lgb_pct,阈值表与 _score_top200 逐档一致,封 ±mc)。只给变体自己的前 200 名
    评级(与 prod「仅顶200有评级」同语义),其余行 v4_total=NaN / v4_layer=None。

    诚实红线:dims 缺失 / dims 日期 ≠ 变体排名日期 → 原样返回 + reason(绝不跨日冒充);
    任何异常 → 原样返回(附着失败绝不 fail 训练,排名本体依旧可用)。
    返回 (rank_df, info):info = {attached, dims_date?, n_rated?} 或 {attached: False, reason}。"""
    try:
        import numpy as np
        import pandas as pd
        from guanlan_v2.strategy import paths   # 惰性 import + 属性访问:测试可 monkeypatch

        p = paths.V4_DIMS_PARQUET
        if not p.exists():
            return rank_df, {"attached": False, "reason": "dims 产物缺失(需先跑一次 regen)"}
        dims = pd.read_parquet(p)
        dd = str(dims["date"].iloc[0])
        rd = str(rank_df["date"].iloc[0])
        if dd != rd:
            return rank_df, {"attached": False,
                             "reason": f"dims 日期 {dd} ≠ 变体排名日期 {rd},拒绝跨日冒充"}
        # eligible 门(mv>30亿 / 3<close<500 / 非ST)∩ 变体榜 → 候选;
        # 按**变体自身** lgb_pct 降序取前 200(与 prod 顶200同口径,但榜是变体自己的)
        elig = dims[dims["eligible"] == True]  # noqa: E712 — parquet 读回容忍非纯 bool dtype
        cand = rank_df.merge(elig[["code", "layer", "mc", "fs", "ts", "vs", "ud"]],
                             on="code", how="inner")
        top = cand.sort_values("lgb_pct", ascending=False).head(200).copy()
        # ms(model 维):rp = 1 - lgb_pct ≈「分位更高者占比」,阈值表同 _score_top200
        rp = (1.0 - top["lgb_pct"]).to_numpy()
        ms = np.select([rp < 0.05, rp < 0.15, rp < 0.3, rp < 0.5, rp < 0.7, rp < 0.85],
                       [2, 2, 1, 1, 0, -1], default=-2)
        mc = top["mc"].to_numpy()
        ms = np.clip(ms, -mc, mc)   # 大盘 mc=1 封顶(rp<0.05 → ms=1 不是 2),同 prod
        top["v4_total"] = (top["fs"].to_numpy() + top["ts"].to_numpy() + ms
                           + top["vs"].to_numpy() + top["ud"].to_numpy()).astype(int)
        top["v4_layer"] = top["layer"]
        # left-merge 回完整榜:未入前200 → v4_total=NaN / v4_layer=None(诚实,不冒充有评级)
        out = rank_df.merge(top[["code", "v4_total", "v4_layer"]], on="code", how="left")
        from guanlan_v2.strategy.ranking import V4_COLUMNS
        head = [c for c in V4_COLUMNS if c in out.columns]   # 列顺序对齐 prod 契约
        out = out[head + [c for c in out.columns if c not in head]]
        return out, {"attached": True, "dims_date": dd, "n_rated": int(len(top))}
    except Exception as exc:  # noqa: BLE001 — 附着失败绝不 fail 训练(诚实降级为无评级榜)
        return rank_df, {"attached": False, "reason": f"{type(exc).__name__}: {exc}"}


def retrain_variant(vid: str) -> Dict[str, Any]:
    """重训一个已存在变体:旧股池快照 + 数据滚动到最新交易日,覆盖同 vid。
    关键=清死 end——变体 meta.recipe 存了死 end(如 '2026-04-01')会让 train_promote 永冻旧日期;
    清后 train_promote 自动取 _latest_trade_date 最新交易日。保留原 name/id/created/kind/status,
    只更新 asof/oos_ic/parquet/hyper。股池仍是 recipe.universe 快照(仅数据滚动,新上市股不纳入)——
    诚实标注 universe_note,绝不冒充股池也更新。失败原样透传 reason,train_promote 失败前不 save_variant→原快照不动。"""
    from guanlan_v2.screen import model_registry as reg

    meta = reg.variant_meta(vid)
    if not meta:
        return {"ok": False, "variant_id": vid, "reason": "变体不存在"}
    kind = str(meta.get("kind") or "").strip()
    if kind == "v4-lgb" and meta.get("retrainable") is True:
        from guanlan_v2.strategy.compute.model_train import train_variant
        recipe = meta.get("recipe") or {}
        if recipe.get("schema") != "v4-workshop-v1":
            return {"ok": False, "variant_id": vid, "reason": "v4 变体缺少可核验训练 recipe"}
        result = train_variant(vid, meta.get("name"), list(recipe.get("factor_ids") or []),
                               list(recipe.get("base_features") or []),
                               universe=recipe.get("universe") or "all",
                               created=meta.get("created") or "", status=meta.get("status"),
                               holdout_k=int(recipe.get("holdout_k") or 20))
        latest_meta = result.get("meta") or {}
        return {"ok": bool(result.get("ok")), "variant_id": vid, "date": latest_meta.get("asof"),
                "oos_ic": latest_meta.get("oos_ic"), "model_version": latest_meta.get("model_version"),
                "universe_note": "原特征选择重训，数据滚动；历史成分/PIT未认证"}
    if meta.get("retrainable") is not True or kind not in _TREE_KINDS:
        return {"ok": False, "variant_id": vid,
                "reason": f"该变体不支持重训(kind={kind or '?'} / retrainable={meta.get('retrainable')})"}

    recipe2 = {**(meta.get("recipe") or {})}
    recipe2.pop("end", None)   # 关键:清死 end → train_promote 自动取最新交易日(不清则永冻旧日期)
    spec: Dict[str, Any] = {
        "variant_id": vid, "name": meta.get("name"), "kind": kind,
        "created": meta.get("created") or "", "recipe": recipe2,
    }
    if meta.get("status"):     # 保留原 draft 语义(重训不擅自升格)
        spec["status"] = meta["status"]

    res = train_promote(spec)
    if not res.get("ok"):
        return res             # 透传 reason;save_variant 未发生 → 原快照未动

    new_meta = reg.variant_meta(vid)   # 复读取重训后 asof(诚实取真值,不回填 spec)
    universe = new_meta.get("universe") or recipe2.get("universe") or "all"
    # 注意:res 来自 train_promote,已带 v4_rating(五维附着结果)——update 只增键不覆盖它
    res.update({
        "ok": True, "variant_id": vid, "date": new_meta.get("asof"),
        "oos_ic": new_meta.get("oos_ic"), "universe": universe,
        "universe_note": f"股池为 {universe} 快照,仅数据滚动到最新(新上市股不纳入)",
    })
    return res


def _jsonresp_reason(resp) -> str:
    """从 _materialize_xy 失败 JSONResponse 抽真 reason(诚实透传)。"""
    import json
    try:
        return json.loads(resp.body).get("reason", "materialize 失败")
    except Exception:  # noqa: BLE001
        return "materialize 失败(universe/特征/标签求值)"


def _holdout_oos_ic(kind, params, fe_df, label_s, fwd_days=5, frac=0.2, evidence=None,
                    observation_index=None):
    """真 OOS:末 frac 调仓日留出 + purge fwd_days 防标签泄漏;**新建模型只在训练段 fit**、
    在留出段预测算逐日 rank-IC 均值(不复用全窗口模型→非样本内)。样本不足/失败 → None(诚实)。"""
    import numpy as np, pandas as pd
    try:
        from guanlan_v2.workflow.api import _build_model
        from guanlan_v2.workflow.temporal_split import purged_dates
        # Label shifts operate on the original per-code observation rows, not
        # the feature-complete subset and not an assumed dense market calendar.
        if observation_index is None:
            observation_index = label_s.index
        dts_all = pd.DatetimeIndex(sorted(set(fe_df.index.get_level_values("datetime"))))
        # Holdout count is based on actually matured labels, not the unlabelled
        # prediction tail. Splits/purge still use the complete trading calendar.
        labeled_dates = sorted(set(label_s.dropna().index.get_level_values("datetime")))
        n_hold = max(1, int(len(labeled_dates) * frac))
        hold_dates = labeled_dates[-n_hold:]
        if evidence is not None:
            evidence.update({"metric_scope": "purged_chronological_holdout",
                             "label_span": int(fwd_days), "n_test_dates_requested": n_hold,
                             "test_start": str(pd.Timestamp(hold_dates[0]).date()) if hold_dates else None,
                             "test_end": str(pd.Timestamp(hold_dates[-1]).date()) if hold_dates else None})
        if len(dts_all) < 30:
            if evidence is not None:
                evidence["reason"] = "too_few_dates"
            return None
        if not hold_dates:
            return None
        candidates = dts_all[dts_all < hold_dates[0]]
        train_dates, split = purged_dates(dts_all, candidates, hold_dates, fwd_days,
                                         observation_index=observation_index)
        if evidence is not None:
            evidence["split"] = split
        if len(train_dates) < 10:
            return None
        dcol = fe_df.index.get_level_values("datetime")
        Xtr = fe_df[pd.Index(dcol).isin(train_dates)].dropna()
        ytr = label_s.reindex(Xtr.index).dropna()
        Xtr = Xtr.reindex(ytr.index)
        if evidence is not None:
            from guanlan_v2.strategy.compute.training_runtime import date_bounds
            evidence["train"] = date_bounds(Xtr.index)
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
        if evidence is not None:
            evidence.update({"n_test_dates_scored": len(ics), "ready": bool(ics)})
        return round(float(np.mean(ics)), 4) if ics else None
    except Exception as exc:  # noqa: BLE001
        if evidence is not None:
            evidence.update({"ready": False, "reason": f"{type(exc).__name__}: {exc}"})
        return None


if __name__ == "__main__":   # spec 模式:<spec.json> 训新变体;重训模式:--retrain <vid> 覆盖旧变体
    import json, sys
    from guanlan_v2.strategy.compute.training_runtime import bootstrap_training_engine
    bootstrap_training_engine()
    if len(sys.argv) >= 3 and sys.argv[1] == "--retrain":   # 手动/日跑重训子进程入口(api.py 调)
        vid = sys.argv[2]
        print(f"[model_retrain] variant={vid} ...", flush=True)
        r = retrain_variant(vid)
        print(f"[model_retrain] done ok={r.get('ok')} asof={r.get('date')} "
              f"oos_ic={r.get('oos_ic')} reason={r.get('reason')}", flush=True)
        sys.exit(0 if r.get("ok") else 1)     # 失败非零退出码(供父进程状态机判 ok)
    spec = json.loads(open(sys.argv[1], encoding="utf-8").read())
    print(f"[model_promote] variant={spec['variant_id']} kind={spec.get('kind')} ...", flush=True)
    r = train_promote(spec)
    print(f"[model_promote] done ok={r.get('ok')} oos_ic={r.get('oos_ic')} "
          f"status={r.get('status') or 'ok'} reason={r.get('reason')}", flush=True)
    sys.exit(0 if r.get("ok") else 1)     # 失败非零退出码(供父进程状态机判 ok)
