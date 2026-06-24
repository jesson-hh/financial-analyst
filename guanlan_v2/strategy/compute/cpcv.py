# -*- coding: utf-8 -*-
"""CPCV + Deflated Sharpe 验证引擎(纯测量,不碰交易信号)。

快速档:读 model_health 冻结快照 → 组合收益分布 + DSR(秒级,零看未来)。
严格档:全历史按组合净化交叉验证(CPCV)重训 → 路径分布 + DSR(~1h)。
不改 v4.py:严格档复用 v4 面板 primitive + workflow 的 _materialize_xy/_build_model 做掩码 fit/predict。"""
from __future__ import annotations

import itertools
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

ANNUALIZE = (252.0 / 5.0) ** 0.5   # 5 日持有期年化


def make_splits(dates, n_groups: int = 6, k: int = 2, purge: int = 5, embargo: int = 5):
    """有序唯一交易日切 n_groups 连续段,枚举 C(n_groups,k) 组合当测试段;每个测试段做
    purge(挖其前 purge 个交易日:训练样本 horizon 标签窗会探入测试段)+ embargo(剔其后 embargo 个)。
    返回 [(train_dates:list, test_dates:list), ...]。"""
    uniq = list(pd.DatetimeIndex(sorted(pd.Index(pd.to_datetime(pd.Series(dates))).unique())))
    n = len(uniq)
    if n < n_groups * 2:
        return []
    bounds = [round(i * n / n_groups) for i in range(n_groups + 1)]
    groups = [uniq[bounds[i]:bounds[i + 1]] for i in range(n_groups)]
    pos = {d: i for i, d in enumerate(uniq)}
    out = []
    for combo in itertools.combinations(range(n_groups), k):
        test = [d for gi in combo for d in groups[gi]]
        drop = set(pos[d] for d in test)
        for tp in list(drop):
            for j in range(1, purge + 1):
                drop.add(tp - j)
            for j in range(1, embargo + 1):
                drop.add(tp + j)
        train = [uniq[i] for i in range(n) if i not in drop]
        out.append((train, sorted(test)))
    return out


def decile_metrics(panel: pd.DataFrame, decile: float = 0.1) -> Dict[str, Any]:
    """panel: 长表 [date, code, lgb_pct, fwd](fwd=该 date 起未来5日收益,已 PIT)。
    每换仓日:top/bottom decile 等权 → 多头超额(top−全域等权)、多空价差(top−bottom)、截面 rank-IC。
    截面<20 跳过(诚实)。"""
    le, ls, ics, used = [], [], [], []
    for d, g in panel.dropna(subset=["lgb_pct", "fwd"]).groupby("date"):
        if len(g) < 20:
            continue
        q_hi = g["lgb_pct"].quantile(1 - decile); q_lo = g["lgb_pct"].quantile(decile)
        top = g[g["lgb_pct"] >= q_hi]["fwd"]; bot = g[g["lgb_pct"] <= q_lo]["fwd"]
        if not len(top):
            continue
        le.append(float(top.mean() - g["fwd"].mean()))
        if len(bot):
            ls.append(float(top.mean() - bot.mean()))
        ic = g["lgb_pct"].rank().corr(g["fwd"].rank())
        if pd.notna(ic):
            ics.append(float(ic))
        used.append(pd.Timestamp(d))
    return {"long_excess_ret": le, "long_short_ret": ls, "rank_ic": ics,
            "rank_ic_mean": float(np.mean(ics)) if ics else None,
            "dates": [str(x.date()) for x in used], "n": len(le)}


def sharpe(returns: List[float], annualize: float = ANNUALIZE) -> Optional[float]:
    r = np.asarray([x for x in returns if x == x], dtype="float64")
    if len(r) < 3 or r.std(ddof=1) == 0:
        return None
    return float(r.mean() / r.std(ddof=1) * annualize)


def _norm_cdf(x: float) -> float:
    import math
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _norm_ppf(p: float) -> float:
    try:
        from scipy.stats import norm
        return float(norm.ppf(p))
    except Exception:  # noqa: BLE001 — Acklam 近似(避免硬依赖 scipy)
        import math
        a = [-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02,
             1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00]
        b = [-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02,
             6.680131188771972e+01, -1.328068155288572e+01]
        c = [-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00,
             -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00]
        d = [7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00, 3.754408661907416e+00]
        pl, ph = 0.02425, 1 - 0.02425
        if p < pl:
            q = math.sqrt(-2 * math.log(p))
            return (((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
        if p > ph:
            q = math.sqrt(-2 * math.log(1 - p))
            return -(((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
        q = p - 0.5; r = q*q
        return (((((a[0]*r+a[1])*r+a[2])*r+a[3])*r+a[4])*r+a[5])*q / (((((b[0]*r+b[1])*r+b[2])*r+b[3])*r+b[4])*r+1)


_EULER = 0.5772156649015329


def deflated_sharpe(returns: List[float], n_trials: int, sharpes_std: Optional[float] = None):
    """DSR = P(真夏普 > SR0),SR0 = N 次试验下期望最大夏普(噪声基准)。返回 [0,1];
    样本<10 或零波动 → None。夏普口径=每周期(未年化)。sharpes_std=各试验夏普标准差(per-period;缺→用解析夏普标准误SE)。"""
    import math
    r = np.asarray([x for x in returns if x == x], dtype="float64")
    T = len(r)
    if T < 10 or r.std(ddof=1) == 0:
        return None
    sr = r.mean() / r.std(ddof=1)
    g3 = float(pd.Series(r).skew())
    g4 = float(pd.Series(r).kurtosis()) + 3.0          # pandas 超额峰度 → 普通峰度
    N = max(2, int(n_trials))
    denom = math.sqrt(max(1e-12, 1 - g3 * sr + (g4 - 1) / 4.0 * sr * sr))
    se = denom / math.sqrt(T - 1)   # 每周期夏普估计量的标准误:无显式多试验夏普时的默认噪声尺度(per-period 口径)
    v = sharpes_std if (sharpes_std and sharpes_std > 0) else se
    sr0 = v * ((1 - _EULER) * _norm_ppf(1 - 1.0 / N) + _EULER * _norm_ppf(1 - 1.0 / (N * math.e)))
    return float(_norm_cdf((sr - sr0) * math.sqrt(T - 1) / denom))


MIN_OOS_DAYS = 10


def _fwd_returns_for_snapshots(hist: pd.DataFrame, horizon: int = 5) -> Dict[Tuple[str, str], float]:
    """对快照 (date,code) 算真 horizon 日前向收益(引擎 close bins,PIT:只取已实现)。单测桩掉。"""
    from financial_analyst.data.loaders.qlib_binary import QlibBinaryLoader
    from guanlan_v2.strategy.compute.model_train import DEFAULT_PROVIDER
    ld = QlibBinaryLoader(DEFAULT_PROVIDER)
    probe = ld._read_bin("SH600519", "close")
    if probe is None or probe.dropna().empty:
        return {}
    last = pd.Timestamp(probe.dropna().index[-1])
    cal = pd.DatetimeIndex([d for d in ld._load_calendar("day") if pd.Timestamp(d) <= last])
    by_code = {c: ld._read_bin(str(c), "close") for c in hist["code"].astype(str).unique()}
    out: Dict[Tuple[str, str], float] = {}
    for d in sorted(hist["date"].astype(str).unique()):
        ts = pd.Timestamp(d); posn = cal.searchsorted(ts)
        if posn >= len(cal) or cal[posn] != ts or posn + horizon >= len(cal):
            continue
        t1 = cal[posn + horizon]
        for c in hist[hist["date"] == d]["code"].astype(str):
            s = by_code.get(c)
            if s is None:
                continue
            c0, c1 = s.get(ts), s.get(t1)
            if c0 and c1 and pd.notna(c0) and pd.notna(c1) and float(c0) > 0:
                out[(d, c)] = float(c1) / float(c0) - 1.0
    return out


def _registry_trials() -> int:
    try:
        from guanlan_v2.screen.model_registry import list_variants
        return max(2, len(list_variants()))
    except Exception:  # noqa: BLE001
        return 2


def quick_validate(model_id: Optional[str] = None, n_trials: Optional[int] = None) -> Dict[str, Any]:
    """读 model_health 冻结快照 → 多头超额夏普 + DSR + RankIC 分布。秒级零看未来;不足→ready=False。
    仅 prod 当前积累快照;变体暂无 → ready=False(诚实)。"""
    mid = model_id or "prod"
    if mid != "prod":
        return {"ready": False, "model_id": mid,
                "note": "变体暂无独立快照(快验只读 prod 冻结快照)——请用严格档验证本变体"}
    from guanlan_v2.strategy import model_health as mh
    if not mh.SCORE_HISTORY_PARQUET.exists():
        return {"ready": False, "model_id": model_id or "prod",
                "note": "证据不足:无快照(仅生产 v4 在 regen 时积累)"}
    hist = pd.read_parquet(mh.SCORE_HISTORY_PARQUET)
    fwd = _fwd_returns_for_snapshots(hist)
    hist = hist.assign(fwd=[fwd.get((str(r.date), str(r.code))) for r in hist.itertuples()])
    realized = hist.dropna(subset=["fwd"])
    n_days = int(realized["date"].nunique())
    if n_days < MIN_OOS_DAYS:
        return {"ready": False, "model_id": model_id or "prod", "n_oos_days": n_days,
                "note": f"证据不足:已实现 OOS 仅 {n_days} 天(<{MIN_OOS_DAYS}),随 regen 变厚"}
    m = decile_metrics(realized)
    n_trials = n_trials or _registry_trials()
    ic_dist = m["rank_ic"]
    if mh.VINTAGE_IC_PARQUET.exists():
        v = pd.read_parquet(mh.VINTAGE_IC_PARQUET)
        if len(v):
            ic_dist = [float(x) for x in v["ic"].tolist()]
    return {"ready": True, "model_id": model_id or "prod", "n_oos_days": n_days,
            "sharpe": sharpe(m["long_excess_ret"]),
            "dsr": deflated_sharpe(m["long_excess_ret"], n_trials=n_trials),
            "ic_mean": (float(np.mean(ic_dist)) if ic_dist else None),
            "ic_dist": [round(x, 4) for x in ic_dist], "n_trials": n_trials,
            "note": "快速档:复用已积累真OOS快照(零看未来);PBO跨变体需严格档"}


def retrain_core(kind, panel_ctx, train_mask, test_dates):
    """train_mask 行 fit、test_dates 行 predict → test 行预测分 Series(MultiIndex datetime,code)。
    panel_ctx 含已物化 `_fe`(特征)+ `_label`;v4-lgb 用 LGB_PARAMS + 复刻 prod NaN 策略(只去NaN标签行·特征fillna(0)),
    tree 用 workflow._build_model(整行 dropna)。不改 v4.py / model_workflow.py。"""
    fe, label = panel_ctx["_fe"], panel_ctx["_label"]
    fill0 = (kind == "v4-lgb")   # 复刻 prod build_v4 的 NaN 策略
    tr = fe[train_mask]
    if fill0:
        ytr = label.reindex(tr.index).dropna()
        Xtr = tr.reindex(ytr.index).fillna(0)
    else:
        Xtr = tr.dropna()
        ytr = label.reindex(Xtr.index).dropna()
        Xtr = Xtr.reindex(ytr.index)
    if len(Xtr) < 200:
        return pd.Series(dtype="float64")
    if kind == "v4-lgb":
        import lightgbm as lgb
        from guanlan_v2.strategy.compute.v4 import LGB_PARAMS
        model = lgb.train(LGB_PARAMS, lgb.Dataset(Xtr.values, label=ytr.values), num_boost_round=500)
        predict = model.predict
    else:
        from guanlan_v2.workflow.api import _build_model
        model, _ = _build_model(kind, panel_ctx.get("params", {}))
        model.fit(Xtr.values, ytr.values)
        predict = model.predict
    dts = fe.index.get_level_values("datetime")
    Xte = fe[pd.Index(dts).isin(set(pd.to_datetime(test_dates)))]
    Xte = Xte.fillna(0) if fill0 else Xte.dropna()
    if Xte.empty:
        return pd.Series(dtype="float64")
    return pd.Series(predict(Xte.values), index=Xte.index, name="pred")


def _materialize_panel(model_id, universe, start, end):
    """按模型 kind 物化一次面板(贵·复用所有路径)→ (kind, ctx{_fe,_label,params}) 或 (kind, None)。
    v4-lgb:复用 v4 的 build_feature_panel+add_ind_turnover+add_breadth_resid(read-only,不改 v4)。
    tree:复用 workflow._materialize_xy(recipe→ModelTrainIn)。"""
    from guanlan_v2.screen.model_registry import variant_meta
    meta = variant_meta(model_id) if (model_id and model_id != "prod") else {"kind": "v4-lgb", "recipe": {}}
    kind = meta.get("kind", "v4-lgb")
    if kind == "v4-lgb":
        from financial_analyst.data.loaders.qlib_binary import QlibBinaryLoader
        from financial_analyst.data.universe import resolve_universe_codes
        from guanlan_v2.strategy.compute.model_train import DEFAULT_PROVIDER
        from guanlan_v2.strategy.compute.breadth import list_all_instruments
        from guanlan_v2.strategy.compute.v4 import (build_feature_panel, add_ind_turnover,
                                                    add_breadth_resid, _select_mf)
        ld = QlibBinaryLoader(DEFAULT_PROVIDER)
        codes = ([str(c) for c in resolve_universe_codes(universe)]
                 if universe not in ("all", "", None) else list_all_instruments(DEFAULT_PROVIDER))
        data = add_breadth_resid(add_ind_turnover(build_feature_panel(ld, codes, start, end),
                                                  ld, codes, start, end))
        if "label" not in data.columns:
            return kind, None
        if "instrument" in (data.index.names or []) and "code" not in (data.index.names or []):
            data = data.rename_axis(index={"instrument": "code"})
        mf = _select_mf(list(data.columns), None)
        return kind, {"_fe": data[mf], "_label": data["label"], "params": {}}
    recipe = meta.get("recipe") or {}
    if not recipe.get("features"):
        return kind, None
    from guanlan_v2.workflow.api import ModelTrainIn, _materialize_xy
    body = ModelTrainIn(kind=kind, features=list(recipe["features"]), label=recipe.get("label") or "fwd_ret",
                        fwd_days=int(recipe.get("fwd_days") or 5), universe=recipe.get("universe") or universe,
                        start=recipe.get("start") or start, end=end,
                        params=dict(recipe.get("params") or {}), winsorize=True, standardize=True)
    mat = _materialize_xy(body, body.universe, body.features, body.start, body.end)
    if not isinstance(mat, tuple):
        return kind, None
    _p, fe_df, label_s, _n = mat
    label_s = label_s.rename("label")
    if "instrument" in (fe_df.index.names or []) and "code" not in (fe_df.index.names or []):
        fe_df = fe_df.rename_axis(index={"instrument": "code"})
        label_s = label_s.rename_axis(index={"instrument": "code"})
    return kind, {"_fe": fe_df, "_label": label_s, "params": dict(recipe.get("params") or {})}


def strict_validate(model_id=None, n_groups=6, k=2, purge=5, embargo=5,
                    universe="all", start="2022-01-01", horizon=5, n_trials=None, progress=None):
    """全历史 retrain-CPCV:面板物化一次 → 15 路径各 retrain_core → 多头超额组合 → 分布+DSR。
    retrainable=False / 物化失败 → ready=False(诚实)。"""
    from guanlan_v2.screen.model_registry import variant_meta
    mid = model_id or "prod"
    if mid != "prod" and not variant_meta(mid).get("retrainable", False):
        return {"ready": False, "model_id": mid, "note": "不可重训(无 recipe)→ 只可快速档"}
    from guanlan_v2.strategy.compute.regen import _latest_trade_date
    from guanlan_v2.strategy.compute.model_train import DEFAULT_PROVIDER
    end = _latest_trade_date(DEFAULT_PROVIDER)
    kind, ctx = _materialize_panel(mid, universe, start, end)
    if ctx is None:
        return {"ready": False, "model_id": mid, "note": "面板物化失败"}
    fe, label = ctx["_fe"], ctx["_label"]
    dts = pd.DatetimeIndex(sorted(set(fe.index.get_level_values("datetime"))))
    eff_purge = max(int(purge), int(horizon) + 1)   # 修:purge 须 ≥ 标签前向跨度(v4 标签 shift(-1)..shift(-6)=6 bar),否则 tp-6 训练行标签探入测试段
    splits = make_splits(dts, n_groups, k, eff_purge, embargo)
    if not splits:
        return {"ready": False, "model_id": mid, "note": "交易日不足以切分"}
    paths, all_excess = [], []
    for i, (train_dates, test_dates) in enumerate(splits):
        if progress:
            progress(i + 1, len(splits))
        train_mask = pd.Index(fe.index.get_level_values("datetime")).isin(set(train_dates))
        pred = retrain_core(kind, ctx, train_mask, test_dates)
        if pred.empty:
            continue
        panel = pd.DataFrame({"date": pred.index.get_level_values("datetime"),
                              "code": pred.index.get_level_values("code"),
                              "lgb_pct": pd.Series(pred.values).rank(pct=True).values,
                              "fwd": label.reindex(pred.index).values})
        rb = sorted(panel["date"].unique())[::horizon]            # 非重叠 5 日换仓
        m = decile_metrics(panel[panel["date"].isin(rb)])
        paths.append({"test_groups": i, "sharpe": sharpe(m["long_excess_ret"]),
                      "ic": m["rank_ic_mean"], "n": m["n"]})
        all_excess += m["long_excess_ret"]
    sps = [p["sharpe"] for p in paths if p["sharpe"] is not None]
    ics = [p["ic"] for p in paths if p["ic"] is not None]
    n_trials = n_trials or _registry_trials()

    def _dist(xs):
        a = np.asarray(xs, dtype="float64")
        return ({"median": float(np.median(a)), "std": float(a.std(ddof=1)) if len(a) > 1 else 0.0,
                 "p05": float(np.percentile(a, 5)), "p95": float(np.percentile(a, 95))} if len(a) else None)
    return {"ready": True, "model_id": mid, "kind": kind, "n_paths": len(paths), "paths": paths,
            "sharpe_dist": _dist(sps), "ic_dist": _dist(ics),
            "dsr": deflated_sharpe(all_excess, n_trials=n_trials),
            "n_trials": n_trials, "asof": str(end),
            "note": "严格档:全历史 retrain-CPCV(purge+embargo);DSR 按 registry 变体数 deflate"}


if __name__ == "__main__":   # python -m guanlan_v2.strategy.compute.cpcv <spec.json>(严格档子进程)
    import json, sys
    spec = json.loads(open(sys.argv[1], encoding="utf-8").read())
    print(f"[cpcv] strict validate model={spec.get('model_id')} ...", flush=True)
    res = strict_validate(**spec)
    from guanlan_v2.strategy import model_health as mh
    mh.write_cpcv(spec.get("model_id") or "prod", res)
    print(f"[cpcv] done ready={res.get('ready')} dsr={res.get('dsr')}", flush=True)
    sys.exit(0 if res.get("ready") else 1)
