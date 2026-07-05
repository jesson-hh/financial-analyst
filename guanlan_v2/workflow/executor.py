# -*- coding: utf-8 -*-
"""后端图执行器(P4):画布 graph JSON 服务端整图执行,直调模块级计算函数。

镜像前端 ui/factor/workflow.jsx runGraph(topoOrder:715/_universeForNode:747/_universeOf:246/
NODE_EXEC:311),计算不走 HTTP——全部直调 workflow/api.py 模块级函数(P2 三道菜直调的推广),
与画布/帷幄同一批函数口径逐位一致。纯同步:只在 daemon 线程(研究回路)或 FastAPI 线程池
(/workflow/run)里跑,绝不进事件循环协程(仓级红线)。

诚实合约:节点失败记 node_errors 继续跑(下游缺输入自然失败显形);无主终端 → ok:False;
metrics 抽不出 → None + warning,绝不编数。
"""
from __future__ import annotations

import hashlib
import json
import time
from typing import Any, Callable, Dict, List, Optional

_TERMINAL_PRIORITY = ("backtest", "analysis", "iccalc")   # 主终端优先级(过门指标来源)
_DIAG_TYPES = {"tsic", "event", "relstat", "risk", "garch", "attrib", "tvbeta"}  # 照跑存档不过门


def _resp_json(resp: Any) -> Dict[str, Any]:
    """JSONResponse → dict(镜像 research/loop.py:_resp_json)。"""
    if isinstance(resp, dict):
        return resp
    try:
        return json.loads(bytes(resp.body).decode("utf-8"))
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "reason": f"响应解析失败: {e}"}


def _universe_of(params: Dict[str, Any]) -> str:
    """source 节点 params → universe(镜像 workflow.jsx:_universeOf:246)。"""
    u = str((params or {}).get("universe") or "").strip()
    if u and u != "自动":
        return u
    code = str((params or {}).get("code") or "").lower()
    scope = (params or {}).get("scope") or ""
    if "500" in code:
        return "csi500"
    if "800" in code:
        return "csi800"
    if "300" in code:
        return "csi300_active"
    if scope == "全市场":
        return "all"
    return "csi_fast"


def topo_order(nodes: List[dict], edges: List[dict]) -> List[str]:
    """Kahn 拓扑,入度0/同层按 x 决序;有环兜底按 x 追加(镜像 workflow.jsx:715-738)。"""
    ids = [n["id"] for n in nodes]
    xmap = {n["id"]: n.get("x", 0) for n in nodes}
    indeg = {i: 0 for i in ids}
    adj: Dict[str, List[str]] = {i: [] for i in ids}
    for e in edges:
        f, t = e["from"][0], e["to"][0]
        if f in indeg and t in indeg:
            indeg[t] += 1
            adj[f].append(t)
    q = sorted([i for i in ids if indeg[i] == 0], key=lambda i: xmap[i])
    order: List[str] = []
    while q:
        cur = q.pop(0)
        order.append(cur)
        nxt = []
        for t in adj[cur]:
            indeg[t] -= 1
            if indeg[t] == 0:
                nxt.append(t)
        q.extend(sorted(nxt, key=lambda i: xmap[i]))
    if len(order) < len(ids):                                  # 环兜底
        order.extend(sorted([i for i in ids if i not in set(order)], key=lambda i: xmap[i]))
    return order


def universe_for_node(nid: str, nodes: List[dict], edges: List[dict]) -> Dict[str, Any]:
    """沿入边 BFS 回溯最近上游 source(镜像 workflow.jsx:_universeForNode:747-765)。"""
    by_id = {n["id"]: n for n in nodes}
    seen = {nid}
    frontier = [nid]
    while frontier:
        nxt: List[str] = []
        for cur in frontier:
            for e in edges:
                if e["to"][0] == cur and e["from"][0] not in seen:
                    up = by_id.get(e["from"][0])
                    if up and up.get("type") == "source":
                        p = up.get("params") or {}
                        import re as _re
                        codes = [c for c in _re.split(r"[\s,;，、]+", str(p.get("codes") or "")) if c.strip()]
                        return {"universe": _universe_of(p),
                                "start": str(p.get("start") or "").strip(),
                                "end": str(p.get("end") or "").strip(),
                                "codes": codes,
                                "benchmark": str(p.get("benchmark") or "").strip(),
                                "leader": str(p.get("leader") or "").strip(),
                                "oos_frac": float(p.get("oos_frac") or 0) or 0.0,
                                "wf_refit": (p.get("wf_refit") == "是"), "wired": True}
                    seen.add(e["from"][0])
                    nxt.append(e["from"][0])
        frontier = nxt
    return {"universe": None, "start": "", "end": "", "codes": [], "benchmark": "",
            "leader": "", "oos_frac": 0.0, "wf_refit": False, "wired": False}


def graph_signature(graph: Dict[str, Any]) -> str:
    """规范化图签名(停滞守卫比较键):type+params+edges,不含 x/y(挪位置≠改图)。"""
    nodes = graph.get("nodes") if isinstance(graph.get("nodes"), list) else []
    edges = graph.get("edges") if isinstance(graph.get("edges"), list) else []
    cn = sorted([{"id": str(n.get("id")), "type": str(n.get("type")),
                  "params": (n.get("params") or {})} for n in nodes if isinstance(n, dict)],
                key=lambda d: d["id"])
    ce = sorted([json.dumps(e, sort_keys=True, ensure_ascii=False)
                 for e in edges if isinstance(e, dict)])
    blob = json.dumps({"nodes": cn, "edges": ce}, sort_keys=True, ensure_ascii=False)
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()


def metrics_of_terminal(payload: Dict[str, Any]) -> Dict[str, Any]:
    """终端载荷 → 六键指标(泛化 research/loop.py:_metrics_of;composite dict 先展开)。"""
    r = payload.get("composite") if isinstance(payload.get("composite"), dict) else payload
    hic = r.get("headline_ic") if isinstance(r.get("headline_ic"), dict) else {}
    ic = r.get("ic") if isinstance(r.get("ic"), dict) else {}
    met = r.get("metrics") if isinstance(r.get("metrics"), dict) else {}
    pf = r.get("portfolio") if isinstance(r.get("portfolio"), dict) else {}
    oos = r.get("oos") if isinstance(r.get("oos"), dict) else {}
    rank_ic = hic.get("rank_ic")
    if rank_ic is None:
        rank_ic = ic.get("rank_ic_mean")
    if rank_ic is None:
        rank_ic = met.get("rank_ic")
    return {"rank_ic": rank_ic, "sharpe": pf.get("sharpe"), "ann_return": pf.get("ann_return"),
            "oos_verdict": oos.get("verdict"), "n_dates": r.get("n_dates"),
            "factor": r.get("_label") or r.get("factor")}


# ── 直调薄桥(延迟 import;独立小函数便于 monkeypatch)────────────────────
def _call_train(body, kind):
    from guanlan_v2.workflow.api import _train_eval
    return _train_eval(body, kind)


def _call_lstm(body):
    from guanlan_v2.workflow.api import _lstm_eval
    return _lstm_eval(body)


def _call_pca(body):
    from guanlan_v2.workflow.api import _pca_factor
    return _pca_factor(body)


def _call_spearman(body):
    from guanlan_v2.workflow.api import _spearman_factor
    return _spearman_factor(body)


def _call_compose(body):
    from guanlan_v2.workflow.api import _factor_compose
    return _factor_compose(body)


def _call_report2(body):
    from guanlan_v2.workflow.api import _factor_report2
    return _factor_report2(body)


def _call_backtest(body):
    from guanlan_v2.workflow.api import _backtest_vector
    return _backtest_vector(body)


def _call_portfolio(body):
    from guanlan_v2.workflow.api import _portfolio_build
    return _portfolio_build(body)


def _call_tsic(body):
    from guanlan_v2.workflow.api import _factor_tsic
    return _factor_tsic(body)


def _call_event(body):
    from guanlan_v2.workflow.api import _factor_event
    return _factor_event(body)


def _call_relstat(body):
    from guanlan_v2.workflow.api import _factor_relstat
    return _factor_relstat(body)


def _call_risk(body):
    from guanlan_v2.workflow.api import _factor_risk
    return _factor_risk(body)


def _call_garch(body):
    from guanlan_v2.workflow.api import _garch
    return _garch(body)


def _call_attrib(body):
    from guanlan_v2.workflow.api import _attrib
    return _attrib(body)


def _call_tvbeta(body):
    from guanlan_v2.workflow.api import _tvbeta
    return _tvbeta(body)


class NodeError(Exception):
    """节点级诚实失败(记 node_errors 继续跑)。"""


_ML_KINDS = {"xgb": "xgboost", "lgbm": "lightgbm", "svm": "svm", "rf": "rf",
             "nn": "mlp", "lstm": "lstm"}
# 后端 _build_model 取值键 → 画布超参字段(逐字段镜像 workflow.jsx _trainModel:392-404)
_HPMAP = {
    "xgb": {"n_estimators": "trees", "max_depth": "depth", "learning_rate": "lr", "subsample": "sub"},
    "lgbm": {"num_leaves": "leaves", "learning_rate": "lr"},
    "svm": {"C": "c"},
    "rf": {"n_estimators": "trees"},
    "nn": {"hidden": "hidden", "layers": "layers", "lr": "lr", "epochs": "epochs", "alpha": "alpha"},
    "lstm": {"seq_len": "seq_len", "hidden": "hidden", "layers": "layers", "lr": "lr", "epochs": "epochs"},
}


def _num(v):
    try:
        f = float(v)
        return f if f == f else None
    except (TypeError, ValueError):
        return None


def _hp(node_type: str, params: Dict[str, Any]) -> Dict[str, Any]:
    """画布超参 → 后端 params(数值化;空串/None 跳过;镜像 _trainModel:278;
    int 型超参保持 int——float 无小数则转 int,防 sklearn 拒 n_estimators=120.0)。"""
    out: Dict[str, Any] = {}
    for bk, ck in _HPMAP.get(node_type, {}).items():
        v = (params or {}).get(ck)
        if v is None or v == "":
            continue
        n = _num(v)
        if n is None:
            out[bk] = v
        else:
            out[bk] = int(n) if float(n).is_integer() else n
    return out


def _expr_of(payload: Optional[dict]) -> str:
    return str((payload or {}).get("expr") or (payload or {}).get("_factorName")
               or (payload or {}).get("_label") or "").strip()


def _check(resp_dict: Dict[str, Any], what: str) -> Dict[str, Any]:
    if resp_dict.get("ok") is False or resp_dict.get("status") not in (None, "ok"):
        raise NodeError(f"{what}: {resp_dict.get('reason') or resp_dict.get('status') or '失败'}")
    return resp_dict


def _features_of(payload: dict) -> List[str]:
    """复合因子载荷 → 可复算 features(镜像前端 backtest 统一走 features 的口径)。"""
    fe = payload.get("fe") if isinstance(payload.get("fe"), dict) else {}
    if isinstance(fe.get("features"), list) and fe["features"]:
        return [str(x) for x in fe["features"]]
    if isinstance(payload.get("members"), list) and payload["members"]:
        return [str(x) for x in payload["members"]]
    e = _expr_of(payload)
    if e:
        return [e]
    raise NodeError("上游因子无可复算表达式")


# ── 节点求值器(inputs, params, ctx) -> {port: payload} ─────────────────
def _mk_body(cls, ctx, **kw):
    """构直调入参:ctx 的 universe/oos_frac/start/end/benchmark/leader/codes 统一注入。"""
    base = dict(universe=ctx["universe"], oos_frac=ctx["oos_frac"])
    if ctx.get("start"):
        base["start"] = ctx["start"]
    if ctx.get("end"):
        base["end"] = ctx["end"]
    if ctx.get("codes"):
        base["codes"] = ctx["codes"]
    if ctx.get("benchmark"):
        base["benchmark"] = ctx["benchmark"]
    if ctx.get("leader"):
        base["leader"] = ctx["leader"]
    base.update(kw)
    return cls(**base)


def _exec_source(inputs, params, ctx):
    return {"data": {"universe": _universe_of(params)}}


def _exec_formula(inputs, params, ctx):
    e = str((params or {}).get("expr") or "").strip()
    if not e:
        raise NodeError("公式输入: expr 为空")
    return {"out": {"expr": e}}


def _exec_factorlib(inputs, params, ctx):
    picked = str((params or {}).get("expr") or "").strip()
    name = str((params or {}).get("name") or "").strip()
    if picked:
        return {"out": {"expr": picked, "_factorName": name, "_label": name or "因子"}}
    if not name:
        raise NodeError("因子库: 未选因子(params.expr/name 均空)")
    from guanlan_v2.factorlib.store import LibraryFactorStore
    allf = LibraryFactorStore().list_factors(validate=False)
    low = name.lower()
    hit = (next((f for f in allf if str(f.get("name", "")).lower() == low), None)
           or next((f for f in allf if low in str(f.get("name", "")).lower()), None))
    if not hit:
        raise NodeError(f"因子库: 查无因子 '{name}'")
    return {"out": {"expr": hit.get("expr") or hit["name"], "_factorName": hit["name"],
                    "_label": hit["name"]}}


def _exec_feature(inputs, params, ctx):
    raw = inputs.get("feat")
    feats: List[str] = []
    for p in (raw if isinstance(raw, list) else [raw]):   # feat 口多边 → 多特征(保序去重)
        e = _expr_of(p)
        if e and e not in feats:
            feats.append(e)
    if not feats:
        raise NodeError("特征工程: 上游未提供特征表达式")
    label = _expr_of(inputs.get("label"))
    if not label:
        tag = str((params or {}).get("tag") or "").strip()
        label = "" if tag.lower() in ("", "ic", "fwd_ret") else tag
    return {"fe": {"features": feats, "label": (label or None)}}


def _exec_ml(node_type):
    def _run(inputs, params, ctx):
        fe = inputs.get("fe")
        if not isinstance(fe, dict) or not fe.get("features"):
            raise NodeError(f"{node_type}: 上游缺特征工程(需 feature 直连 fe 口)")
        from guanlan_v2.workflow.api import ModelTrainIn
        kind = _ML_KINDS[node_type]
        body = _mk_body(ModelTrainIn, ctx, kind=kind, features=list(fe["features"]),
                        label=fe.get("label"), params=_hp(node_type, params))
        resp = _check(_resp_json(_call_lstm(body) if node_type == "lstm"
                                 else _call_train(body, kind)), node_type)
        resp["_kind"] = kind
        return {"model": resp}
    return _run


def _exec_pca(inputs, params, ctx):
    fe = inputs.get("fe")
    if not isinstance(fe, dict) or not fe.get("features"):
        raise NodeError("PCA: 上游缺特征工程")
    from guanlan_v2.workflow.api import PCAFactorIn
    kw = dict(features=list(fe["features"]), label=fe.get("label"))
    k = _num((params or {}).get("k"))
    if k:
        kw["k"] = int(k)
    resp = _check(_resp_json(_call_pca(_mk_body(PCAFactorIn, ctx, **kw))), "PCA")
    resp.setdefault("composite", True)
    return {"factor": resp}


def _exec_spearman(inputs, params, ctx):
    fe = inputs.get("fe")
    if not isinstance(fe, dict) or not fe.get("features"):
        raise NodeError("Spearman: 上游缺特征工程")
    from guanlan_v2.workflow.api import SpearmanFactorIn
    resp = _check(_resp_json(_call_spearman(
        _mk_body(SpearmanFactorIn, ctx, features=list(fe["features"]), label=fe.get("label")))),
        "Spearman")
    resp.setdefault("composite", True)
    return {"factor": resp}


def _model_report(m):
    """镜像前端 _modelReport:297:顶层/嵌套 report/composite 三形态取 OOS 报告。"""
    if not isinstance(m, dict):
        return None
    if m.get("ic") or m.get("portfolio") or m.get("quantile"):
        return m
    r = m.get("report")
    if isinstance(r, dict) and (r.get("ic") or r.get("portfolio") or r.get("quantile")):
        return r
    c = m.get("composite")
    if isinstance(c, dict) and (c.get("ic") or c.get("portfolio") or c.get("quantile")):
        return c
    return None


def _exec_mf(inputs, params, ctx):
    for port in ("m1", "m2"):                       # ①模型报告透传(model→factor 唯一桥)
        rep = _model_report(inputs.get(port))
        if rep:
            out = dict(rep)
            out["composite"] = True
            out.setdefault("_kind", (inputs.get(port) or {}).get("_kind"))
            return {"factor": out}
    exprs = [e for e in (_expr_of(inputs.get(p)) for p in ("f1", "f2", "m1", "m2")) if e]
    # fe 载荷经 f1/f2 连入时无 expr → 回退全图表达式集合(镜像前端 allExprs 兜底)
    if len(exprs) < 2:
        exprs = list(ctx.get("all_exprs") or [])
    if len(exprs) < 2:
        raise NodeError("多因子构建: 需要至少 2 个因子表达式")
    from guanlan_v2.workflow.api import FactorComposeIn
    body = _mk_body(FactorComposeIn, ctx, members=exprs,
                    method=str((params or {}).get("combine") or "equal"), freq=ctx["freq"])
    resp = _check(_resp_json(_call_compose(body)), "多因子构建")
    comp = resp.get("composite") if isinstance(resp.get("composite"), dict) else {}
    out = dict(comp)
    out["composite"] = True
    out.setdefault("members", resp.get("members"))
    out.setdefault("weights", resp.get("weights"))
    return {"factor": out}


def _exec_analysis(inputs, params, ctx):
    f = inputs.get("factor")
    if isinstance(f, dict) and f.get("composite"):
        return {"report": f}                        # 复合因子报告透传为终端(ML 图到终端的通路)
    e = _expr_of(f)
    if not e:
        raise NodeError("因子分析: 上游未提供因子表达式")
    from guanlan_v2.workflow.api import FactorReport2In
    p = params or {}
    body = _mk_body(FactorReport2In, ctx, expr_or_name=e,
                    freq=str(p.get("rebal") or ctx["freq"]),
                    n_groups=int(_num(p.get("groups")) or 10),
                    direction=int(_num(p.get("dir")) or 0),
                    neutralize=(p.get("neutral") == "是"))
    return {"report": _check(_resp_json(_call_report2(body)), "因子分析")}


def _exec_iccalc(inputs, params, ctx):
    f = inputs.get("factor")
    if isinstance(f, dict) and f.get("composite"):
        return {"ic": f}
    e = _expr_of(f)
    if not e:
        raise NodeError("IC 计算: 上游缺因子")
    period = int(_num((params or {}).get("period")) or 5)
    freq = "month" if period >= 20 else ("week" if period >= 5 else "day")
    from guanlan_v2.workflow.api import FactorReport2In
    body = _mk_body(FactorReport2In, ctx, expr_or_name=e, freq=freq, fwd_days=period)
    return {"ic": _check(_resp_json(_call_report2(body)), "IC 计算")}


def _exec_backtest(inputs, params, ctx):
    f = inputs.get("pf") or inputs.get("factor")
    if not isinstance(f, dict):
        raise NodeError("向量化回测: 上游缺因子/组合")
    from guanlan_v2.workflow.api import BacktestVectorIn
    p = params or {}
    kw = dict(features=_features_of(f), rebalance=ctx["freq"],
              topn=int(_num(p.get("topn")) or 30))
    if _num(p.get("cash")):
        kw["cash"] = float(_num(p.get("cash")))
    if p.get("weighting"):
        kw["weighting"] = str(p["weighting"])
    if p.get("vol_forecast"):
        kw["vol_forecast"] = str(p["vol_forecast"])
    return {"result": _check(_resp_json(_call_backtest(_mk_body(BacktestVectorIn, ctx, **kw))),
                             "向量化回测")}


def _exec_portfolio(inputs, params, ctx):
    f = inputs.get("factor")
    if not isinstance(f, dict):
        raise NodeError("组合构建: 上游缺因子")
    from guanlan_v2.workflow.api import PortfolioBuildIn
    p = params or {}
    kw = dict(features=_features_of(f), topn=int(_num(p.get("topn")) or 30))
    for key in ("weighting", "vol_forecast"):
        if p.get(key):
            kw[key] = str(p[key])
    if p.get("max_weight") not in (None, "") and _num(p.get("max_weight")) is not None:
        kw["max_weight"] = float(_num(p.get("max_weight")))
    if p.get("industry_neutral") in (True, "是"):
        kw["industry_neutral"] = True
    return {"pf": _check(_resp_json(_call_portfolio(_mk_body(PortfolioBuildIn, ctx, **kw))),
                         "组合构建")}


def _exec_diag(node_type):
    """七个诊断终端:表达式 + 各自 params → 对应直调;载荷原样出端口(照跑存档)。"""
    def _run(inputs, params, ctx):
        src = inputs.get("factor") or inputs.get("trigger")
        e = _expr_of(src)
        if not e:
            raise NodeError(f"{node_type}: 上游缺表达式")
        p = params or {}
        from guanlan_v2.workflow import api as wapi
        if node_type == "tsic":
            body = _mk_body(wapi.FactorTsicIn, ctx, expr_or_name=e,
                            fwd_days=int(_num(p.get("fwd_days")) or 20))
            resp = _call_tsic(body)
        elif node_type == "event":
            hz = [int(s) for s in str(p.get("horizons") or "1,5,10,20").split(",")
                  if s.strip().isdigit() and int(s) >= 1] or [1, 5, 10, 20]
            body = _mk_body(wapi.FactorEventIn, ctx, trigger=e, horizons=hz,
                            direction=int(_num(p.get("direction")) or 0))
            resp = _call_event(body)
        elif node_type == "relstat":
            body = _mk_body(wapi.FactorRelstatIn, ctx, expr_or_name=e)
            resp = _call_relstat(body)
        else:                                        # risk/garch/attrib/tvbeta 继承回测,因子走 features
            cls = {"risk": wapi.FactorRiskIn, "garch": wapi.FactorGarchIn,
                   "attrib": wapi.FactorAttribIn, "tvbeta": wapi.FactorTVBetaIn}[node_type]
            kw = dict(features=[e])
            if _num(p.get("topn")):
                kw["topn"] = int(_num(p.get("topn")))
            if node_type == "garch" and _num(p.get("horizon")):
                kw["horizon"] = int(_num(p.get("horizon")))
            body = _mk_body(cls, ctx, **kw)
            resp = {"risk": _call_risk, "garch": _call_garch,
                    "attrib": _call_attrib, "tvbeta": _call_tvbeta}[node_type](body)
        return {node_type: _check(_resp_json(resp), node_type)}
    return _run


_DISPATCH: Dict[str, Callable] = {
    "source": _exec_source, "formula": _exec_formula, "factorlib": _exec_factorlib,
    "feature": _exec_feature, "pca": _exec_pca, "spearman": _exec_spearman,
    "mf": _exec_mf, "analysis": _exec_analysis, "iccalc": _exec_iccalc,
    "backtest": _exec_backtest, "portfolio": _exec_portfolio,
}
for _t in _ML_KINDS:
    _DISPATCH[_t] = _exec_ml(_t)
for _t in _DIAG_TYPES:
    _DISPATCH[_t] = _exec_diag(_t)

# 各节点类型的出端口名(端口对端口传递用;与前端 SPECS 一致)
_OUT_PORT = {"source": "data", "formula": "out", "factorlib": "out", "feature": "fe",
             "xgb": "model", "lgbm": "model", "svm": "model", "rf": "model", "nn": "model",
             "lstm": "model", "pca": "factor", "spearman": "factor", "mf": "factor",
             "analysis": "report", "iccalc": "ic", "backtest": "result", "portfolio": "pf",
             "tsic": "tsic", "event": "event", "relstat": "relstat", "risk": "risk",
             "garch": "garch", "attrib": "attrib", "tvbeta": "tvbeta"}


def graph_exprs(graph: Dict[str, Any]) -> List[str]:
    """图内 formula/factorlib 表达式集合(保序;供档案 exprs 显示与 mf 兜底)。"""
    out: List[str] = []
    for n in (graph.get("nodes") or []):
        if not isinstance(n, dict):
            continue
        p = n.get("params") or {}
        if n.get("type") == "formula":
            e = str(p.get("expr") or "").strip()
        elif n.get("type") == "factorlib":
            e = str(p.get("expr") or p.get("name") or "").strip()
        else:
            continue
        if e and e not in out:
            out.append(e)
    return out


def run_graph(graph: Dict[str, Any], overrides: Optional[Dict[str, Any]] = None,
              on_node: Optional[Callable[[str, str, str], None]] = None) -> Dict[str, Any]:
    """整图执行(spec §1.1)。on_node(nid, type, state) state∈running|done|error,供回路进度显形。"""
    t0 = time.time()
    ov = overrides or {}
    nodes = graph.get("nodes") if isinstance(graph.get("nodes"), list) else []
    edges = graph.get("edges") if isinstance(graph.get("edges"), list) else []
    nodes = [n for n in nodes if isinstance(n, dict) and n.get("id")]
    edges = [e for e in edges if isinstance(e, dict)
             and isinstance(e.get("from"), list) and isinstance(e.get("to"), list)]
    by_id = {n["id"]: n for n in nodes}
    order = topo_order(nodes, edges)
    all_exprs = graph_exprs(graph)
    freq = str(ov.get("freq") or "month")
    outputs: Dict[str, Dict[str, Any]] = {}
    node_results: Dict[str, Dict[str, Any]] = {}
    node_errors: List[Dict[str, Any]] = []
    warnings: List[str] = []
    terminals: List[Dict[str, Any]] = []
    has_ml = any(n.get("type") in _ML_KINDS for n in nodes)

    for nid in order:
        node = by_id[nid]
        typ = str(node.get("type") or "")
        fn = _DISPATCH.get(typ)
        inputs: Dict[str, Any] = {}
        in_edge_n: Dict[str, int] = {}
        for e in edges:
            if e["to"][0] == nid and e["from"][0] in outputs:
                port = e["to"][1]
                payload = outputs[e["from"][0]].get(e["from"][1])
                in_edge_n[port] = in_edge_n.get(port, 0) + 1
                if typ == "feature" and port == "feat":
                    # feat 口多边聚合为多特征(镜像前端 deriveRecipeForNode 的多边解读)
                    inputs.setdefault(port, []).append(payload)
                else:
                    inputs[port] = payload
        for port, n in in_edge_n.items():
            if n > 1 and not (typ == "feature" and port == "feat"):
                warnings.append(f"节点 {nid}({typ}) 输入口 {port} 收到 {n} 条边——仅最后一条生效")
        u = universe_for_node(nid, nodes, edges)
        ctx = {
            "universe": ov.get("universe") or (u["universe"] if u["wired"] else None) or "csi_fast",
            "freq": freq,
            "oos_frac": (ov["oos_frac"] if ov.get("oos_frac") is not None
                         else (u["oos_frac"] if u["wired"] else 0.0)),
            "start": ov.get("start") or (u["start"] if u["wired"] else ""),
            "end": ov.get("end") or (u["end"] if u["wired"] else ""),
            "codes": (u["codes"] if u["wired"] else []),
            "benchmark": (u["benchmark"] if u["wired"] else ""),
            "leader": (u["leader"] if u["wired"] else ""),
            "all_exprs": all_exprs,
        }
        if on_node:
            on_node(nid, typ, "running")
        try:
            if fn is None:
                raise NodeError(f"不支持的节点类型: {typ}")
            out = fn(inputs, node.get("params") or {}, ctx) or {}
            outputs[nid] = out
            node_results[nid] = {"ok": True, "type": typ}
            if typ in _TERMINAL_PRIORITY:
                payload = out.get(_OUT_PORT[typ])
                if isinstance(payload, dict):
                    terminals.append({"kind": typ, "node_id": nid, "payload": payload})
            if on_node:
                on_node(nid, typ, "done")
        except Exception as exc:  # noqa: BLE001 — 节点失败不中断(镜像前端)
            node_results[nid] = {"ok": False, "type": typ}
            node_errors.append({"nid": nid, "type": typ, "error": f"{exc}"})
            if on_node:
                on_node(nid, typ, "error")

    main = None
    for kind in _TERMINAL_PRIORITY:                 # 优先级取主终端;同类取拓扑序最后
        cand = [t for t in terminals if t["kind"] == kind]
        if cand:
            main = cand[-1]
            break
    metrics = metrics_of_terminal(main["payload"]) if main else None
    if main is None:
        warnings.append("无主终端产出(回测/分析/IC)——过门指标缺失")
    ok = main is not None
    reason = None if ok else (node_errors[0]["error"] if node_errors else "图无主终端节点")
    return {"ok": ok, "reason": reason, "terminal": main, "metrics": metrics,
            "exprs": all_exprs, "has_ml": has_ml, "node_results": node_results,
            "node_errors": node_errors, "warnings": warnings,
            "elapsed_sec": round(time.time() - t0, 1)}
