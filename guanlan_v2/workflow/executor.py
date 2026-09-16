# -*- coding: utf-8 -*-
"""后端图执行器(P4):画布 graph JSON 服务端整图执行,直调模块级计算函数。

镜像前端 ui/factor/workflow.jsx runGraph(topoOrder:715/_universeForNode:747/_universeOf:246/
NODE_EXEC:311),计算不走 HTTP——全部直调 workflow/api.py 模块级函数(P2 三道菜直调的推广),
与画布/帷幄同一批函数口径逐位一致。纯同步:只在 daemon 线程(研究回路)或 FastAPI 线程池
(/workflow/run)里跑,绝不进事件循环协程(仓级红线)。

诚实合约:节点失败记 node_errors 继续跑(下游缺输入自然失败显形);无主终端 → ok:False;
交互入口可显式允许纯诊断图返回完成的诊断产物，但不生成过门指标或晋级资格。
metrics 抽不出 → None + warning,绝不编数。
"""
from __future__ import annotations

import hashlib
import json
import math
import time
from copy import deepcopy
from typing import Any, Callable, Dict, List, Optional

_TERMINAL_PRIORITY = ("overfit", "daily_backtest", "backtest", "analysis", "iccalc", "data_screen")   # 审计终端优先且携带源回测指标
_DIAG_TYPES = {"tsic", "event", "relstat", "risk", "garch", "attrib", "tvbeta"}  # 照跑存档不过门
# Parallel graphs must retain every terminal payload even though only the
# priority set above is eligible to become the gate/main result.  This keeps a
# screen plus an independent diagnostic/portfolio replayable without changing
# the long-standing rule that a diagnostic-only graph has no gate metrics.
_TERMINAL_CAPTURE_TYPES = set(_TERMINAL_PRIORITY) | _DIAG_TYPES | {"portfolio"}


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
    """Resolve explicit or uniquely nearest source; never choose by edge order."""
    from guanlan_v2.workflow.migrations import context_source_for_node
    from guanlan_v2.workflow.source_scope import source_codes

    source_id = context_source_for_node(nid, nodes, edges)
    if source_id is not None:
        source = next(n for n in nodes if str(n.get("id")) == source_id)
        p = source.get("params") or {}
        return {"universe": _universe_of(p),
                # source.freq is the legacy quote interval (normally day), not
                # a request to change research sampling/evaluation defaults.
                "research_freq": str(p.get("research_freq") or "").strip(),
                "start": str(p.get("start") or "").strip(),
                "end": str(p.get("end") or "").strip(),
                "codes": source_codes(p),
                "benchmark": str(p.get("benchmark") or "").strip(),
                "leader": str(p.get("leader") or "").strip(),
                "oos_frac": float(p.get("oos_frac") or 0) or 0.0,
                "wf_refit": p.get("wf_refit") in (True, "是"), "wired": True}
    return {"universe": None, "research_freq": "", "start": "", "end": "", "codes": [], "benchmark": "",
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


def _runtime_candidate_hash(
    graph: Dict[str, Any], overrides: Optional[Dict[str, Any]] = None,
) -> str:
    """Recompute the governed candidate identity for the graph being executed.

    ``audit_context_id`` is a server-injected evidence handle added only after a
    candidate's validation trial has been revealed.  It is therefore excluded
    from the graph identity; every other semantic graph parameter remains bound.
    The four candidate parameters exactly mirror
    ``research.optimize_adapter.run_research_optimize``'s ``cand_params``.

    This value is computed inside the server executor and is never accepted from
    ``WorkflowRunIn`` / ``OverfitAuditIn``.  It can consequently be used to prove
    that a sealed TrialLedger context is auditing the current graph rather than a
    caller-selected graph that happens to reproduce the same return path.
    """
    scrubbed = deepcopy(graph)
    for node in scrubbed.get("nodes") or []:
        if not isinstance(node, dict) or node.get("type") != "overfit":
            continue
        params = dict(node.get("params") or {})
        params.pop("audit_context_id", None)
        node["params"] = params
    ov = overrides or {}
    candidate_params = {
        "universe": ov.get("universe"),
        "freq": ov.get("freq"),
        "start": ov.get("start"),
        "end": ov.get("end"),
    }
    from guanlan_v2.orchestration.trial import OptimizeCandidate

    return OptimizeCandidate.build(
        candidate_kind="workflow_graph", graph=scrubbed, params=candidate_params,
    ).candidate_hash


def metrics_of_terminal(payload: Dict[str, Any]) -> Dict[str, Any]:
    """终端载荷 → 研究闸门指标。

    审计终端会携带源回测的 IC/组合指标，但必须同时投影
    ``audit_status/audit_passes_gate``；否则 INCOMPLETE/FAIL 可能因源回测
    指标漂亮而被研究回路错误晋级。
    """
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
    out = {"rank_ic": rank_ic, "sharpe": pf.get("sharpe"), "ann_return": pf.get("ann_return"),
           "oos_verdict": oos.get("verdict"), "n_dates": r.get("n_dates"),
           "factor": r.get("_label") or r.get("factor")}
    if r.get("__dt") == "overfit_audit" or r.get("method") == "overfit_audit":
        out["audit_status"] = r.get("status")
        out["audit_passes_gate"] = r.get("passes_gate") is True
    return out


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


def _call_overfit(body, **runtime_binding):
    from guanlan_v2.workflow.api import _overfit_audit
    return _overfit_audit(body, **runtime_binding)


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


def _orthogonalization_of(payload: Optional[dict]):
    value = (payload or {}).get("_orthogonalization")
    if isinstance(value, dict):
        return value
    fe = (payload or {}).get("fe")
    value = fe.get("orthogonalization") if isinstance(fe, dict) else None
    return value if isinstance(value, dict) else None


def _factor_artifact_of(payload: Optional[dict]):
    """Find the immutable fitted signal carried by a factor/portfolio payload."""
    if not isinstance(payload, dict):
        return None
    value = payload.get("factor_artifact")
    if isinstance(value, dict):
        return value
    fe = payload.get("fe")
    value = fe.get("factor_artifact") if isinstance(fe, dict) else None
    return value if isinstance(value, dict) else None


def _split_factor_controls(value: Any) -> List[str]:
    """Parse control-factor expressions without splitting commas inside DSL calls."""
    if isinstance(value, list):
        return list(dict.fromkeys(str(x).strip() for x in value if str(x).strip()))
    text = str(value or "").strip()
    if not text:
        return []
    # Semicolon/newline is the unambiguous separator for arbitrary DSL.  Keep the
    # legacy comma shorthand only for simple registered names (no parentheses).
    import re
    parts = re.split(r"[;\uff1b\r\n]+", text)
    if len(parts) == 1 and "(" not in text and ")" not in text:
        parts = text.replace("，", ",").split(",")
    return list(dict.fromkeys(x.strip() for x in parts if x.strip()))


def _check(resp_dict: Dict[str, Any], what: str) -> Dict[str, Any]:
    if resp_dict.get("ok") is False or resp_dict.get("status") not in (None, "ok"):
        raise NodeError(f"{what}: {resp_dict.get('reason') or resp_dict.get('status') or '失败'}")
    return resp_dict


def _features_of(payload: dict) -> List[str]:
    """复合因子载荷 → 可复算 features(镜像前端 backtest 统一走 features 的口径)。"""
    # feature 节点的 ``fe`` 出口本身就是 fe spec；经端口传到
    # backtest.factor 后不再包一层 {fe:...}。接受顶层 features 才与画布
    # ``const spec = f.fe || {}; features = spec.features || expr`` 的真实
    # 端口载荷等价，也让 source→formula→feature→backtest 标准图可服务端续跑。
    if isinstance(payload.get("features"), list) and payload["features"]:
        return [str(x) for x in payload["features"]]
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
    # Inject only an explicit research choice. Old graphs used per-request
    # defaults (e.g. ModelTrainIn.day versus analysis.month); a generic ctx
    # fallback must not silently alter their data/label sampling frequency.
    if ctx.get("research_freq"):
        base["freq"] = ctx["research_freq"]
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
    return {"data": {
        "universe": _universe_of(params),
        "requested_universe": str((params or {}).get("universe") or "").strip(),
    }}


def _exec_history_screen(inputs, params, ctx):
    if not isinstance(inputs.get("src"), dict):
        raise NodeError("历史逐日筛选必须显式连接 source.data → history_screen.src")
    from guanlan_v2.workflow.strategy_replay import run_history_screen
    source = inputs["src"]
    if ctx.get("universe") and ctx["universe"] != source.get("universe"):
        raise NodeError("历史筛选不接受与显式 source 不一致的股票池覆盖；请修改数据源后重新运行")
    context = {key: ctx.get(key) for key in ("start", "end", "codes", "research_freq")}
    context["universe"] = source.get("requested_universe") or source.get("universe")
    return {"history": run_history_screen(params, context, owner=ctx.get("workspace_id"))}


def _exec_daily_backtest(inputs, params, ctx):
    from guanlan_v2.workflow.strategy_replay import run_daily_backtest
    return {"result": run_daily_backtest(inputs.get("history"), params,
                                          owner=ctx.get("workspace_id"))}


def _exec_data_screen(inputs, params, ctx):
    """Thin workflow adapter over the same deterministic direct-Skill core."""
    if not isinstance(params, dict):
        raise NodeError("数据筛选 params 必须是对象")
    p = dict(params)
    allowed = {
        "preset", "selector", "conditions", "filters", "score", "codes", "as_of",
        "ma_window", "rising_transitions", "below_lookback", "max_consecutive_below",
        "min_bias", "max_bias", "top_n", "topN", "min_avg_amount_yi", "amount_window",
        "exclude_st", "require_asof_trade", "require_strict_metadata_pit",
    }
    unknown = sorted(set(p) - allowed)
    if unknown:
        raise NodeError("数据筛选存在未知参数(可能是拼写错误): " + ", ".join(unknown))

    def strict_int(name: str, default: int) -> int:
        value = p.get(name, default)
        if (isinstance(value, bool) or not isinstance(value, (int, float))
                or not math.isfinite(float(value)) or int(value) != value):
            raise NodeError(f"数据筛选参数 {name} 必须是整数，不能用字符串或小数")
        return int(value)

    def strict_number(name: str, default: float) -> float:
        value = p.get(name, default)
        if (isinstance(value, bool) or not isinstance(value, (int, float))
                or not math.isfinite(float(value))):
            raise NodeError(f"数据筛选参数 {name} 必须是有限数值，不能用字符串或布尔值")
        return float(value)

    def strict_bool(name: str, default: bool) -> bool:
        value = p.get(name, default)
        if not isinstance(value, bool):
            raise NodeError(f"数据筛选参数 {name} 必须是布尔值")
        return value

    source_ctx = inputs.get("src")
    if not isinstance(source_ctx, dict):
        raise NodeError(
            "数据筛选必须显式连接 source.data → data_screen.src；"
            "未连接时不会用快速池或其他默认股票池代替"
        )
    requested_universe = str(source_ctx.get("requested_universe") or "").strip()
    source_codes = list(ctx.get("codes") or [])
    if not source_codes and (not requested_universe or requested_universe == "自动"):
        raise NodeError(
            "数据筛选的数据源必须显式选择股票池；请选择“全A·筛选 Skill 专用”、"
            "一个具名指数，或在 source.codes 填写股票代码，不能使用空的“自动”池"
        )
    if "top_n" in p and "topN" in p:
        raise NodeError("数据筛选不能同时提供 top_n 与 topN")
    if "selector" in p and any(key in p for key in ("preset", "conditions")):
        raise NodeError("数据筛选 selector 不能与 preset/conditions 同时提供")
    if "conditions" in p and "preset" in p:
        raise NodeError("数据筛选不能同时提供 preset 与 conditions")
    if isinstance(p.get("selector"), dict):
        selector = dict(p["selector"])
    elif "selector" in p:
        raise NodeError("数据筛选 selector 必须是对象")
    elif isinstance(p.get("conditions"), list):
        selector = {"conditions": p["conditions"], "combine": "all"}
    elif "conditions" in p:
        raise NodeError("数据筛选 conditions 必须是数组")
    else:
        preset = p.get("preset", "ma_trend")
        if not isinstance(preset, str) or not preset.strip():
            raise NodeError("数据筛选 preset 必须是非空字符串")
        selector = {"preset": preset.strip(), "params": {
            "ma_window": strict_int("ma_window", 5),
            "rising_transitions": strict_int("rising_transitions", 3),
            "below_lookback": strict_int("below_lookback", 5),
            "max_consecutive_below": strict_int("max_consecutive_below", 1),
            "min_bias": strict_number("min_bias", 0.0),
            "max_bias": strict_number("max_bias", 0.03),
        }}
    if "filters" in p and any(key in p for key in (
            "exclude_st", "require_asof_trade", "min_avg_amount_yi",
            "amount_window", "require_strict_metadata_pit")):
        raise NodeError("数据筛选 filters 不能与扁平过滤参数同时提供")
    if "filters" in p:
        if not isinstance(p["filters"], dict):
            raise NodeError("数据筛选 filters 必须是对象")
        filters = dict(p["filters"])
        allowed_filter_keys = {
            "exclude_st", "require_asof_trade", "min_avg_amount_yuan",
            "amount_window", "amount_min_observations", "require_strict_metadata_pit",
        }
        unknown_filters = sorted(set(filters) - allowed_filter_keys)
        if unknown_filters:
            raise NodeError("数据筛选 filters 存在未知参数: " + ", ".join(unknown_filters))
        for key in ("exclude_st", "require_asof_trade", "require_strict_metadata_pit"):
            if key in filters and not isinstance(filters[key], bool):
                raise NodeError(f"数据筛选 filters.{key} 必须是布尔值")
        for key in ("amount_window", "amount_min_observations"):
            if key in filters:
                value = filters[key]
                if (isinstance(value, bool) or not isinstance(value, (int, float))
                        or not math.isfinite(float(value)) or int(value) != value):
                    raise NodeError(f"数据筛选 filters.{key} 必须是整数")
        if "min_avg_amount_yuan" in filters:
            value = filters["min_avg_amount_yuan"]
            if (isinstance(value, bool) or not isinstance(value, (int, float))
                    or not math.isfinite(float(value))):
                raise NodeError("数据筛选 filters.min_avg_amount_yuan 必须是有限数值")
    else:
        filters = {
            "exclude_st": strict_bool("exclude_st", True),
            "require_asof_trade": strict_bool("require_asof_trade", True),
            "min_avg_amount_yuan": strict_number("min_avg_amount_yi", 1.0) * 100_000_000.0,
            "amount_window": strict_int("amount_window", 20),
            "amount_min_observations": strict_int("amount_window", 20),
            "require_strict_metadata_pit": strict_bool("require_strict_metadata_pit", False),
        }
    if "codes" in p and not isinstance(p["codes"], list):
        raise NodeError("数据筛选 codes 必须是数组")
    if "score" in p and p["score"] is not None and not isinstance(p["score"], dict):
        raise NodeError("数据筛选 score 必须是对象")
    if "as_of" in p and not isinstance(p["as_of"], str):
        raise NodeError("数据筛选 as_of 必须是 YYYY-MM-DD 字符串")
    top_n = strict_int("top_n", 5) if "top_n" in p or "topN" not in p else strict_int("topN", 5)
    request_codes = source_codes or list(p.get("codes") or [])
    request: Dict[str, Any] = {
        "universe": (requested_universe if requested_universe and requested_universe != "自动"
                     else "all_a"),
        "codes": request_codes,
        "freq": "day", "top_n": top_n,
        "selector": selector, "filters": filters,
    }
    if p.get("score") is not None:
        request["score"] = p["score"]
    as_of = str(p.get("as_of") or ctx.get("end") or "").strip()
    if as_of:
        request["as_of"] = as_of
    from guanlan_v2.screen.data_screen import run_data_screen
    result = run_data_screen(request, persist=True,
                             parent_run_id=ctx.get("workflow_run_id"))
    if not result.get("ok"):
        run_id = str(result.get("run_id") or "未落运行号")
        raise NodeError(
            f"数据筛选(run_id={run_id}): "
            + str(result.get("reason") or "执行失败")
        )
    result.setdefault("__dt", "data_screen")
    result.setdefault("screen_run_id", result.get("run_id"))
    return {"candidates": result}


def _exec_formula(inputs, params, ctx):
    e = str((params or {}).get("expr") or "").strip()
    if not e:
        raise NodeError("公式输入: expr 为空")
    return {"out": {"expr": e}}


def _exec_factorlib(inputs, params, ctx):
    picked = str((params or {}).get("expr") or "").strip()
    name = str((params or {}).get("name") or "").strip()
    expected_spec = (params or {}).get("factor_spec")
    expected_digest = str((params or {}).get("factor_spec_digest") or "").strip()
    structured_ref = isinstance(expected_spec, dict) or bool(expected_digest)
    if not name and picked:
        if structured_ref:
            raise NodeError("结构化因子引用缺少注册名，拒绝退回基础表达式")
        return {"out": {"expr": picked, "_factorName": "", "_label": "因子"}}
    if not name:
        raise NodeError("因子库: 未选因子(params.expr/name 均空)")
    from guanlan_v2.factorlib.store import LibraryFactorStore
    allf = LibraryFactorStore().list_factors(validate=False)
    low = name.lower()
    exact = next((f for f in allf if str(f.get("name", "")).lower() == low), None)
    hit = exact if structured_ref else (
        exact or next((f for f in allf if low in str(f.get("name", "")).lower()), None)
    )
    # For a structured entry the registered name is the executable reference.
    # Returning the cached UI ``params.expr`` here would silently replay only
    # the base expression and discard its transform.
    if hit and isinstance(hit.get("factor_spec"), dict):
        from guanlan_v2.factorlib.specs import (
            factor_spec_digest, verify_factor_spec_dependencies,
        )
        try:
            canonical = verify_factor_spec_dependencies(
                hit["factor_spec"],
                fallback_expr=str(hit.get("expr") or "").strip(),
                factor_name=str(hit.get("name") or "").strip(),
            )
        except Exception as exc:
            raise NodeError(f"结构化因子 '{name}' 依赖校验失败: {exc}") from exc
        actual_digest = factor_spec_digest(canonical)
        recorded_digest = str(hit.get("factor_spec_digest") or "").strip()
        if recorded_digest and recorded_digest != actual_digest:
            raise NodeError(f"结构化因子 '{name}' 的存储摘要与规范内容不一致")
        if expected_digest and actual_digest != expected_digest:
            raise NodeError(
                f"结构化因子 '{name}' 定义已变化(digest 不一致)，请重新选择并审阅"
            )
        if isinstance(expected_spec, dict):
            try:
                expected_canonical = verify_factor_spec_dependencies(expected_spec)
            except Exception as exc:
                raise NodeError(f"结构化因子 '{name}' 工作流快照非法: {exc}") from exc
            if expected_canonical != canonical:
                raise NodeError(
                    f"结构化因子 '{name}' 工作流快照与因子库定义不一致"
                )
        # The workflow later executes by registry name.  Bind that runtime
        # callable to the same canonical spec now so a stale/hot-replaced
        # registry entry cannot bypass the store snapshot.
        try:
            from financial_analyst.factors.zoo.registry import get as get_alpha
            runtime = get_alpha(str(hit["name"]))
            runtime_raw = getattr(runtime.compute, "__guanlan_factor_spec__", None)
            runtime_spec = verify_factor_spec_dependencies(
                runtime_raw, fallback_expr=str(hit.get("expr") or "").strip(),
                factor_name=str(hit.get("name") or "").strip(),
            )
        except Exception as exc:
            raise NodeError(f"结构化因子 '{name}' 运行时定义缺失或非法: {exc}") from exc
        if runtime_spec != canonical:
            raise NodeError(f"结构化因子 '{name}' 运行时定义与因子库不一致")
        return {"out": {"expr": hit["name"], "_factorName": hit["name"],
                        "_label": hit["name"], "factor_spec": canonical,
                        "factor_spec_digest": actual_digest}}
    if structured_ref:
        raise NodeError(
            f"结构化因子 '{name}' 已缺失或不再是结构化定义，拒绝退回缓存表达式"
        )
    if picked:
        return {"out": {"expr": picked, "_factorName": name, "_label": name or "因子"}}
    if not hit:
        raise NodeError(f"因子库: 查无因子 '{name}'")
    return {"out": {"expr": hit.get("expr") or hit["name"], "_factorName": hit["name"],
                    "_label": hit["name"]}}


def _exec_orthogonalize(inputs, params, ctx):
    """Create a replayable factor-spec; downstream consumers materialise it."""
    source = inputs.get("signal")
    if isinstance(source, dict) and isinstance(source.get("factor_spec"), dict):
        raise NodeError(
            "factor_spec v1 不支持对结构化因子再次正交化；请引用其原始基础表达式"
        )
    expr = _expr_of(source)
    if not expr:
        raise NodeError("因子正交化: 上游缺可复算公式/因子库表达式")
    p = params or {}
    styles_value = p.get("styles")
    if isinstance(styles_value, (list, tuple)):
        styles = [str(s).strip() for s in styles_value if str(s).strip()]
    else:
        styles_raw = str(styles_value or "").strip()
        styles = [s.strip() for s in styles_raw.replace("，", ",").split(",") if s.strip()]
    old_factors = _split_factor_controls(p.get("old_factors"))
    size_map = {"不控制": "none", "总市值": "total_mv", "流通市值": "circ_mv"}
    size = size_map.get(str(p.get("size") or "总市值"), str(p.get("size") or "total_mv"))
    spec = {
        "version": "orthogonalization-v1",
        "method": "ols",
        "industry": p.get("industry", "是") in (True, "是"),
        "size": size,
        "styles": styles,
        "existing_factors": old_factors,
        "min_obs": int(20 if _num(p.get("min_obs")) is None else _num(p.get("min_obs"))),
        "max_condition_number": float(
            1.0e8 if _num(p.get("max_condition_number")) is None
            else _num(p.get("max_condition_number"))
        ),
    }
    if not spec["industry"] and size == "none" and not styles and not old_factors:
        raise NodeError("因子正交化: 至少选择一个行业/规模/风格/已有因子控制项")
    label = str((source or {}).get("_label") or expr)
    from guanlan_v2.factorlib.specs import (
        factor_spec_digest, pin_factor_spec_dependencies,
    )
    try:
        factor_spec = pin_factor_spec_dependencies({
            "schema": "guanlan.factor_spec.v1", "kind": "orthogonalized",
            "base_features": [expr], "transform": spec,
        })
    except Exception as exc:
        raise NodeError(f"因子正交化依赖封存失败: {exc}") from exc
    spec = factor_spec["transform"]
    digest = factor_spec_digest(factor_spec)
    fe = {"features": [expr], "orthogonalization": spec}
    return {"factor": {
        "__dt": "factor", "expr": expr, "features": [expr], "fe": fe,
        "_label": f"{label} · 正交", "_orthogonalization": spec,
        "factor_spec": factor_spec, "factor_spec_digest": digest,
    }}


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
                        label=fe.get("label"), params=_hp(node_type, params),
                        wf_refit=bool(ctx.get("wf_refit")))
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
    model_inputs = []
    for port in ("m1", "m2"):
        model_payload = inputs.get(port)
        rep = _model_report(model_payload)
        if (port in inputs or port in ctx.get("connected_input_ports", ())) and not rep:
            raise NodeError(f"多模型构建: {port} 已连接但缺少有效模型报告，拒绝回退原始因子")
        if rep:
            model_inputs.append((port, model_payload, rep))
    if len(model_inputs) >= 2:
        artifacts = [_factor_artifact_of(item[1]) or _factor_artifact_of(item[2])
                     for item in model_inputs]
        if any(not isinstance(item, dict) for item in artifacts):
            raise NodeError(
                "多模型构建: 每一路模型都必须产出可验证 factor_artifact；"
                "拒绝只取第一路或退回原始 features"
            )
        method = str((params or {}).get("combine") or "equal")
        metric_rows: List[Dict[str, Any]] = []
        for _port, _model_payload, rep in model_inputs:
            metrics = dict(rep.get("metrics") or {}) if isinstance(rep, dict) else {}
            headline = rep.get("headline_ic") if isinstance(rep.get("headline_ic"), dict) else {}
            for key in ("rank_ic", "rank_icir"):
                if metrics.get(key) is None and headline.get(key) is not None:
                    metrics[key] = headline[key]
            metric_rows.append(metrics)
        from guanlan_v2.workflow.factor_artifact import (
            artifact_features, artifact_weights, combine_factor_artifacts,
        )
        try:
            artifact = combine_factor_artifacts(
                artifacts, method=method, metrics=metric_rows,
                label="多模型[" + "+".join(
                    str((payload or {}).get("_kind") or port)
                    for port, payload, _rep in model_inputs
                ) + "]",
            )
        except Exception as exc:
            raise NodeError(f"多模型构建失败: {exc}") from exc
        features = artifact_features(artifact)
        out = {
            "ok": True, "status": "ok", "composite": True,
            "_kind": "multi_model", "_label": artifact.get("label"),
            "method": "multi_model", "members": [
                {
                    "port": port,
                    "kind": str((payload or {}).get("_kind") or "model"),
                    "artifact_id": artifact_item.get("id"),
                }
                for (port, payload, _rep), artifact_item in zip(model_inputs, artifacts)
            ],
            "weights": artifact_weights(artifact),
            "fe": {"features": features, "factor_artifact": artifact},
            "factor_artifact": artifact,
            "factor_artifact_id": artifact.get("id"),
        }
        return {"factor": out}
    if len(model_inputs) == 1:                       # ①单模型报告 + 真实预测产物透传
        port, model_payload, rep = model_inputs[0]
        out = dict(rep)
        out["composite"] = True
        out.setdefault("_kind", (model_payload or {}).get("_kind"))
        artifact = _factor_artifact_of(model_payload) or _factor_artifact_of(rep)
        if isinstance(artifact, dict):
            out["factor_artifact"] = artifact
            out["factor_artifact_id"] = artifact.get("id")
            fe = dict(out.get("fe") or {})
            fe["factor_artifact"] = artifact
            out["fe"] = fe
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
    artifact = _factor_artifact_of(resp) or _factor_artifact_of(comp)
    if isinstance(artifact, dict):
        out["factor_artifact"] = artifact
        out["factor_artifact_id"] = artifact.get("id")
        fe = dict(out.get("fe") or {})
        fe.setdefault("features", list(exprs))
        fe["factor_artifact"] = artifact
        out["fe"] = fe
    return {"factor": out}


def _exec_analysis(inputs, params, ctx):
    f = inputs.get("factor")
    artifact = _factor_artifact_of(f)
    if isinstance(artifact, dict):
        from guanlan_v2.workflow.api import FactorReport2In
        p = params or {}
        label = str((f or {}).get("_label") or artifact.get("label") or artifact.get("id") or "fitted factor")
        body = _mk_body(
            FactorReport2In, ctx, expr_or_name=label,
            freq=str(p.get("rebal") or ctx["freq"]),
            n_groups=int(_num(p.get("groups")) or 10),
            direction=int(_num(p.get("dir")) or 0),
            neutralize=(p.get("neutral") == "是"),
            factor_artifact=artifact,
        )
        report = dict(_check(_resp_json(_call_report2(body)), "因子分析"))
        report.setdefault("factor_artifact", artifact)
        report.setdefault("factor_artifact_id", artifact.get("id"))
        if (f or {}).get("_kind"):
            report.setdefault("_kind", f["_kind"])
        return {"report": report}
    if isinstance(f, dict) and f.get("composite"):
        return {"report": f}                        # 复合因子报告透传为终端(ML 图到终端的通路)
    e = _expr_of(f)
    if not e:
        raise NodeError("因子分析: 上游未提供因子表达式")
    from guanlan_v2.workflow.api import FactorReport2In
    p = params or {}
    orthogonalization = _orthogonalization_of(f)
    body = _mk_body(FactorReport2In, ctx, expr_or_name=e,
                    freq=str(p.get("rebal") or ctx["freq"]),
                    n_groups=int(_num(p.get("groups")) or 10),
                    direction=int(_num(p.get("dir")) or 0),
                    neutralize=(p.get("neutral") == "是" and not orthogonalization),
                    orthogonalization=orthogonalization)
    report = dict(_check(_resp_json(_call_report2(body)), "因子分析"))
    if isinstance((f or {}).get("factor_spec"), dict):
        report.setdefault("factor_spec", f["factor_spec"])
        from guanlan_v2.factorlib.specs import factor_spec_digest
        report.setdefault(
            "factor_spec_digest",
            str(f.get("factor_spec_digest") or factor_spec_digest(f["factor_spec"])),
        )
    return {"report": report}


def _exec_iccalc(inputs, params, ctx):
    f = inputs.get("factor")
    artifact = _factor_artifact_of(f)
    if isinstance(artifact, dict):
        period = int(_num((params or {}).get("period")) or 5)
        freq = "month" if period >= 20 else ("week" if period >= 5 else "day")
        from guanlan_v2.workflow.api import FactorReport2In
        label = str((f or {}).get("_label") or artifact.get("label") or artifact.get("id") or "fitted factor")
        body = _mk_body(
            FactorReport2In, ctx, expr_or_name=label, freq=freq,
            fwd_days=period, factor_artifact=artifact,
        )
        report = dict(_check(_resp_json(_call_report2(body)), "IC 计算"))
        report.setdefault("factor_artifact", artifact)
        report.setdefault("factor_artifact_id", artifact.get("id"))
        if (f or {}).get("_kind"):
            report.setdefault("_kind", f["_kind"])
        return {"ic": report}
    if isinstance(f, dict) and f.get("composite"):
        return {"ic": f}
    e = _expr_of(f)
    if not e:
        raise NodeError("IC 计算: 上游缺因子")
    period = int(_num((params or {}).get("period")) or 5)
    freq = "month" if period >= 20 else ("week" if period >= 5 else "day")
    from guanlan_v2.workflow.api import FactorReport2In
    body = _mk_body(FactorReport2In, ctx, expr_or_name=e, freq=freq, fwd_days=period,
                    orthogonalization=_orthogonalization_of(f))
    report = dict(_check(_resp_json(_call_report2(body)), "IC 计算"))
    if isinstance((f or {}).get("factor_spec"), dict):
        report.setdefault("factor_spec", f["factor_spec"])
        from guanlan_v2.factorlib.specs import factor_spec_digest
        report.setdefault(
            "factor_spec_digest",
            str(f.get("factor_spec_digest") or factor_spec_digest(f["factor_spec"])),
        )
    return {"ic": report}


def _exec_backtest(inputs, params, ctx):
    f = inputs.get("pf") or inputs.get("factor")
    if not isinstance(f, dict):
        raise NodeError("向量化回测: 上游缺因子/组合")
    from guanlan_v2.workflow.api import BacktestVectorIn
    p = params or {}
    # Mirror the canvas backtest payload.  Durable execution must not silently
    # reset the user's frequency/cost/weighting choices when ownership moves
    # from the page to the server worker.
    features = _features_of(f)
    topn = int(_num(p.get("topn")) or 30)
    cash = float(_num(p.get("cash")) or 1_000_000)
    kw = dict(
        features=features,
        rebalance=str(ctx["freq"] if ctx.get("force_rebalance")
                      else (p.get("rebalance") or ctx["freq"])),
        topn=topn,
        combine=str(p.get("combine") or "equal"),
        orthogonalization=_orthogonalization_of(f),
    )
    artifact = _factor_artifact_of(f)
    if isinstance(artifact, dict):
        kw["factor_artifact"] = artifact
        # A fitted artifact is already the final signal; replaying an upstream
        # transform on it would change its identity.
        kw["orthogonalization"] = None
    kw["cash"] = cash
    for key in ("start", "end"):
        value = str(p.get(key) or "").strip()
        if value:
            kw[key] = value
    for key in ("commission", "stamp_tax"):
        if _num(p.get(key)) is not None:
            kw[key] = float(_num(p.get(key)))
    if _num(p.get("slippage")) is not None:
        kw["slippage_bps"] = float(_num(p.get("slippage")))
    # A connected portfolio node owns its portfolio construction choices;
    # otherwise the backtest node's own params apply (same precedence as JSX).
    for key in ("weighting", "vol_forecast"):
        value = f.get(key) if inputs.get("pf") and f.get(key) else p.get(key)
        if value:
            kw[key] = str(value)
    if inputs.get("pf"):
        kw["industry_neutral"] = bool(f.get("industry_neutral"))
        if _num(f.get("max_weight")) is not None:
            kw["max_weight"] = float(_num(f.get("max_weight")))
    result = dict(_check(
        _resp_json(_call_backtest(_mk_body(BacktestVectorIn, ctx, **kw))),
        "向量化回测",
    ))
    # The browser executor decorates /backtest/vector before sending it to the
    # drawer. Keep the same display contract for a reconnected durable run.
    result.setdefault("__dt", "result")
    result.setdefault("composite", True)
    result.setdefault("_compose", True)
    result.setdefault("topn", topn)
    result.setdefault("cash", cash)
    result.setdefault("_universe", ctx["universe"])
    result.setdefault("_label", str(f.get("_label") or "+".join(features) or f"TopN{topn}"))
    result.setdefault("_warnings", result.get("warnings") or f.get("_warnings") or [])
    if isinstance(artifact, dict):
        result.setdefault("factor_artifact", artifact)
        result.setdefault("factor_artifact_id", artifact.get("id"))
    if isinstance(f.get("factor_spec"), dict):
        result.setdefault("factor_spec", f["factor_spec"])
        from guanlan_v2.factorlib.specs import factor_spec_digest
        result.setdefault(
            "factor_spec_digest",
            str(f.get("factor_spec_digest") or factor_spec_digest(f["factor_spec"])),
        )
    if inputs.get("pf"):
        result.setdefault("_pf_weights", f.get("weights") or f.get("holdings") or [])
        result.setdefault("_pf_weighting", f.get("weighting"))
        result.setdefault("_pf_asof", f.get("asof"))
        result.setdefault("_pf_industry_neutral", f.get("industry_neutral"))
    return {"result": result}


def _exec_overfit(inputs, params, ctx):
    result = inputs.get("result")
    if not isinstance(result, dict):
        raise NodeError("过拟合审计: 上游缺回测结果")
    portfolio = result.get("portfolio") if isinstance(result.get("portfolio"), dict) else {}
    nav_series = portfolio.get("nav_series")
    audit_context = result.get("audit_context") if isinstance(result.get("audit_context"), dict) else {}
    backtest = result.get("backtest") if isinstance(result.get("backtest"), dict) else {}
    rebalance = str(backtest.get("rebalance") or backtest.get("rebalance_freq") or "month")
    ppy = {"day": 252.0, "week": 52.0, "month": 12.0}.get(rebalance, 12.0)
    p = params or {}
    from guanlan_v2.workflow.api import OverfitAuditIn
    body = OverfitAuditIn(
        return_series=portfolio.get("return_series"),
        nav_series=nav_series,
        trial_returns=audit_context.get("trial_returns"),
        trial_dates=audit_context.get("trial_dates"),
        selection_score_matrix=audit_context.get("selection_score_matrix"),
        selection_dates=audit_context.get("selection_dates"),
        eligible_column_indices=audit_context.get("eligible_column_indices"),
        selected_column_index=audit_context.get("selected_column_index"),
        selection_objective=audit_context.get("selection_objective"),
        selection_score_source=audit_context.get("selection_score_source"),
        selection_eligibility_scope=audit_context.get("selection_eligibility_scope"),
        selection_rule_digest=audit_context.get("selection_rule_digest"),
        n_trials=audit_context.get("n_trials"),
        family_id=audit_context.get("family_id"),
        selected_trial_id=audit_context.get("selected_trial_id"),
        audit_context_id=(str(p.get("audit_context_id") or
                              audit_context.get("audit_context_id") or "").strip() or None),
        periods_per_year=ppy,
        confidence=float(0.95 if _num(p.get("confidence")) is None else _num(p.get("confidence"))),
        pbo_max=float(0.30 if _num(p.get("pbo_max")) is None else _num(p.get("pbo_max"))),
        n_blocks=int(8 if _num(p.get("n_blocks")) is None else _num(p.get("n_blocks"))),
        overlapping_returns=bool(backtest.get("overlapping_returns", False)),
    )
    audit = _resp_json(_call_overfit(
        body,
        _runtime_candidate_hash=ctx.get("_runtime_candidate_hash"),
        _runtime_data_snapshot=result.get("data_snapshot"),
    ))
    if audit.get("ok") is False:
        raise NodeError(f"过拟合审计: {audit.get('reason') or '失败'}")
    # Keep the source performance visible so this higher-priority terminal still
    # supplies research-loop headline metrics and the report drawer can link back.
    for key in ("portfolio", "ic", "oos", "headline_ic", "orthogonalization", "factor_spec"):
        if key in result:
            audit[key] = result[key]
    # The audit drawer is the last decision surface.  Preserve warnings from
    # both the source backtest and the audit itself so data fallbacks, short
    # windows, OOS degradation, etc. cannot disappear at this hand-off.
    merged_warnings: List[str] = []
    for group in (result.get("warnings"), result.get("_warnings"), audit.get("warnings")):
        if not isinstance(group, list):
            continue
        for warning in group:
            text = str(warning).strip()
            if text and text not in merged_warnings:
                merged_warnings.append(text)
    audit["warnings"] = merged_warnings
    audit["_warnings"] = merged_warnings
    audit.update({
        "__dt": "overfit_audit", "_label": result.get("_label") or "回测过拟合审计",
        "_universe": result.get("_universe") or ctx.get("universe"),
        "source_backtest": {"method": result.get("method"), "backtest": backtest},
    })
    return {"audit": audit}


def _exec_portfolio(inputs, params, ctx):
    f = inputs.get("factor")
    if not isinstance(f, dict):
        raise NodeError("组合构建: 上游缺因子")
    from guanlan_v2.workflow.api import PortfolioBuildIn
    p = params or {}
    kw = dict(features=_features_of(f), topn=int(_num(p.get("topn")) or 30),
              orthogonalization=_orthogonalization_of(f))
    artifact = _factor_artifact_of(f)
    if isinstance(artifact, dict):
        kw["factor_artifact"] = artifact
        kw["orthogonalization"] = None
    for key in ("weighting", "vol_forecast"):
        if p.get(key):
            kw[key] = str(p[key])
    if p.get("max_weight") not in (None, "") and _num(p.get("max_weight")) is not None:
        kw["max_weight"] = float(_num(p.get("max_weight")))
    if p.get("industry_neutral") in (True, "是"):
        kw["industry_neutral"] = True
    result = dict(_check(_resp_json(_call_portfolio(_mk_body(PortfolioBuildIn, ctx, **kw))),
                         "组合构建"))
    if isinstance(artifact, dict):
        result.setdefault("factor_artifact", artifact)
        result.setdefault("factor_artifact_id", artifact.get("id"))
        fe = dict(result.get("fe") or {})
        fe["factor_artifact"] = artifact
        result["fe"] = fe
    if isinstance(f.get("factor_spec"), dict):
        result.setdefault("factor_spec", f["factor_spec"])
        from guanlan_v2.factorlib.specs import factor_spec_digest
        result.setdefault(
            "factor_spec_digest",
            str(f.get("factor_spec_digest") or factor_spec_digest(f["factor_spec"])),
        )
    return {"pf": result}


def _exec_diag(node_type):
    """七个诊断终端:表达式 + 各自 params → 对应直调;载荷原样出端口(照跑存档)。"""
    def _run(inputs, params, ctx):
        src = inputs.get("factor") or inputs.get("trigger")
        artifact = _factor_artifact_of(src)
        e = _expr_of(src)
        if not e and not artifact:
            raise NodeError(f"{node_type}: 上游缺表达式")
        p = params or {}
        from guanlan_v2.workflow import api as wapi
        if node_type == "tsic":
            if artifact:
                raise NodeError("tsic 暂不接受拟合 factor_artifact，拒绝退回原始 features")
            body = _mk_body(wapi.FactorTsicIn, ctx, expr_or_name=e,
                            fwd_days=int(_num(p.get("fwd_days")) or 20))
            resp = _call_tsic(body)
        elif node_type == "event":
            if artifact:
                raise NodeError("event 暂不接受拟合 factor_artifact，拒绝退回原始 features")
            hz = [int(s) for s in str(p.get("horizons") or "1,5,10,20").split(",")
                  if s.strip().isdigit() and int(s) >= 1] or [1, 5, 10, 20]
            body = _mk_body(wapi.FactorEventIn, ctx, trigger=e, horizons=hz,
                            direction=int(_num(p.get("direction")) or 0))
            resp = _call_event(body)
        elif node_type == "relstat":
            if artifact:
                raise NodeError("relstat 暂不接受拟合 factor_artifact，拒绝退回原始 features")
            body = _mk_body(wapi.FactorRelstatIn, ctx, expr_or_name=e)
            resp = _call_relstat(body)
        else:                                        # risk/garch/attrib/tvbeta 继承回测,因子走 features
            cls = {"risk": wapi.FactorRiskIn, "garch": wapi.FactorGarchIn,
                   "attrib": wapi.FactorAttribIn, "tvbeta": wapi.FactorTVBetaIn}[node_type]
            kw = dict(features=_features_of(src))
            if artifact:
                kw["factor_artifact"] = artifact
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
    "data_screen": _exec_data_screen,
    "history_screen": _exec_history_screen, "daily_backtest": _exec_daily_backtest,
    "orthogonalize": _exec_orthogonalize,
    "feature": _exec_feature, "pca": _exec_pca, "spearman": _exec_spearman,
    "mf": _exec_mf, "analysis": _exec_analysis, "iccalc": _exec_iccalc,
    "backtest": _exec_backtest, "overfit": _exec_overfit, "portfolio": _exec_portfolio,
}
for _t in _ML_KINDS:
    _DISPATCH[_t] = _exec_ml(_t)
for _t in _DIAG_TYPES:
    _DISPATCH[_t] = _exec_diag(_t)

# 各节点类型的出端口名(端口对端口传递用;与前端 SPECS 一致)
_OUT_PORT = {"source": "data", "formula": "out", "factorlib": "out",
             "history_screen": "history", "daily_backtest": "result",
             "data_screen": "candidates",
             "orthogonalize": "factor", "feature": "fe",
             "xgb": "model", "lgbm": "model", "svm": "model", "rf": "model", "nn": "model",
             "lstm": "model", "pca": "factor", "spearman": "factor", "mf": "factor",
             "analysis": "report", "iccalc": "ic", "backtest": "result", "overfit": "audit", "portfolio": "pf",
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
              on_node: Optional[Callable[[str, str, str], None]] = None,
              prefer_model_terminal: bool = False,
              artifact_sink: Optional[Callable[[str, str, Dict[str, Any]], None]] = None,
              allow_diagnostic_result: bool = False,
              ) -> Dict[str, Any]:
    """整图执行(spec §1.1)。on_node(nid, type, state) state∈running|done|error,供回路进度显形。

    prefer_model_terminal(默认 False=画布 /workflow/run 零行为变化,仅研究回路 opt-in):
    True 时携带模型报告(payload._kind,唯 ML 报告经 mf 透传才有)的 analysis/iccalc 终端
    优先于 backtest——backtest 按 fe.features 重推导不吃模型预测(2026-07-05 真机实证:
    死/活模型 backtest 指标逐位同),ML 图过门指标须对齐模型真实成绩;让位写 warnings 显形。
    图无模型报告终端时(rr_3e2e4dbe58 残余缺口)直取 mf/ML 节点输出为主终端(kind=节点
    类型)并记警告;全图无模型报告(ML 训练失败)也记警告——绝不静默回落 backtest 口径。"""
    # Interactive inspection is not research promotion. This opt-in never
    # applies to a graph declaring a primary/ML/audit terminal, even if that
    # branch failed and an unrelated diagnostic succeeded.
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
    source_nodes = [n for n in nodes if n.get("type") == "source"]
    src = source_nodes[0] if source_nodes else None
    global_universe = _universe_of(src.get("params") or {}) if src else "csi_fast"
    outputs: Dict[str, Dict[str, Any]] = {}
    node_results: Dict[str, Dict[str, Any]] = {}
    node_errors: List[Dict[str, Any]] = []
    warnings: List[str] = []
    terminals: List[Dict[str, Any]] = []
    has_ml = any(n.get("type") in _ML_KINDS for n in nodes)
    audit_node_ids = [str(n["id"]) for n in nodes if n.get("type") == "overfit"]
    runtime_candidate_hash: Optional[str] = None
    if audit_node_ids:
        try:
            runtime_candidate_hash = _runtime_candidate_hash(graph, ov)
        except Exception as exc:  # noqa: BLE001 - trusted audit must fail closed
            warnings.append(
                "无法生成当前工作流候选绑定，可信过拟合审计将拒绝 PASS: "
                f"{type(exc).__name__}: {exc}"
            )

    invalid_src_edges = []
    for edge in edges:
        if edge["to"][1] != "src":
            continue
        source_node = by_id.get(edge["from"][0]) or {}
        if source_node.get("type") != "source" or edge["from"][1] != "data":
            invalid_src_edges.append(edge)
    if invalid_src_edges:
        edge = invalid_src_edges[0]
        reason = (
            f"上下文入口 {edge['to'][0]}.src 只能由 source.data 供给；"
            f"收到 {edge['from'][0]}.{edge['from'][1]}"
        )
        audit_gate = ({"declared": True, "status": "INCOMPLETE",
                       "passes_gate": False, "execution_error": True,
                       "nodes": []} if audit_node_ids else None)
        return {"ok": False, "reason": reason, "terminal": None, "metrics": None,
                "audit": audit_gate, "exprs": all_exprs, "has_ml": has_ml,
                "node_results": {}, "node_errors": [{"nid": edge["to"][0],
                "type": str((by_id.get(edge["to"][0]) or {}).get("type") or ""),
                "error": reason}], "warnings": [],
                "elapsed_sec": round(time.time() - t0, 1)}

    for nid in order:
        node = by_id[nid]
        typ = str(node.get("type") or "")
        fn = _DISPATCH.get(typ)
        inputs: Dict[str, Any] = {}
        in_edge_n: Dict[str, int] = {}
        connected_input_ports = set()
        failed_upstream = []
        for e in edges:
            if e["to"][0] == nid:
                connected_input_ports.add(e["to"][1])
                if node_results.get(e["from"][0], {}).get("ok") is False:
                    failed_upstream.append(str(e["from"][0]))
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
        context_error = None
        try:
            u = universe_for_node(nid, nodes, edges)
            if not u["wired"] and len(source_nodes) > 1 and not ov.get("universe") \
                    and typ not in {"source", "formula", "factorlib"}:
                context_error = ("图含多个 source，但本计算节点未连接或引用数据上下文；"
                                 "请显式设置 context_source_id，拒绝使用第一个数据源")
        except (ValueError, TypeError) as exc:
            # Surface a per-node failure and continue independent branches;
            # never silently use global defaults for a broken explicit scope.
            context_error = str(exc)
            u = {"wired": False}
        research_freq = str(ov.get("freq") or
                            (u.get("research_freq") if u["wired"] else "") or "").strip()
        ctx = {
            # Mirror the canvas: a connected source wins per node; when source
            # is present but not wired, execution falls back to that first
            # source's selected pool (and the UI can warn), not hard-coded
            # csi_fast. With no source at all the legacy csi_fast default stays.
            "universe": ov.get("universe") or (u["universe"] if u["wired"] else None) or global_universe,
            "freq": research_freq or freq,
            "research_freq": research_freq,
            "oos_frac": (ov["oos_frac"] if ov.get("oos_frac") is not None
                         else (u["oos_frac"] if u["wired"] else 0.0)),
            "start": ov.get("start") or (u["start"] if u["wired"] else ""),
            "end": ov.get("end") or (u["end"] if u["wired"] else ""),
            "codes": (u["codes"] if u["wired"] else []),
            "benchmark": (u["benchmark"] if u["wired"] else ""),
            "leader": (u["leader"] if u["wired"] else ""),
            "wf_refit": (ov["wf_refit"] if ov.get("wf_refit") is not None
                         else (u["wf_refit"] if u["wired"] else False)),
            "all_exprs": all_exprs,
            "connected_input_ports": connected_input_ports,
            # Governed research owns one Study frequency.  Canvas/durable
            # runs leave this false so the user's node setting is unchanged.
            "force_rebalance": bool(ov.get("force_rebalance")),
            # Durable runner injects its server-owned id so nested Skill runs
            # can record a verifiable wr_* -> ds_* parent link. Public request
            # models never accept this private key.
            "workflow_run_id": ov.get("_workflow_run_id"),
            "workspace_id": ov.get("_workspace_id"),
            # Private server-computed evidence.  No workflow HTTP request model
            # accepts this field, and an overfit node never reads it from params.
            "_runtime_candidate_hash": runtime_candidate_hash,
        }
        if on_node:
            on_node(nid, typ, "running")
        try:
            if context_error:
                raise NodeError(context_error)
            if failed_upstream:
                raise NodeError("上游节点执行失败，拒绝使用降级/替代输入: "
                                + ", ".join(sorted(set(failed_upstream))))
            if fn is None:
                raise NodeError(f"不支持的节点类型: {typ}")
            out = fn(inputs, node.get("params") or {}, ctx) or {}
            outputs[nid] = out
            node_results[nid] = {"ok": True, "type": typ}
            # Research's TrialLedger connector needs the exact server-produced
            # backtest payload after the ledger reveals this candidate.  Keep
            # it out of the public run response: an injected in-process sink is
            # the only capture seam, and sink failure fails this node loudly.
            if artifact_sink is not None:
                artifact_sink(nid, typ, out)
            if typ in _TERMINAL_CAPTURE_TYPES:
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
    if prefer_model_terminal:                       # opt-in:模型报告终端优先(过门=模型真实成绩)
        for kind in ("analysis", "iccalc"):
            cand = [t for t in terminals if t["kind"] == kind
                    and isinstance(t.get("payload"), dict) and t["payload"].get("_kind")]
            if cand:
                main = cand[-1]
                if any(t["kind"] == "backtest" for t in terminals):
                    warnings.append(
                        f"ML 图主终端改取 {kind} 模型报告(prefer_model_terminal):"
                        "backtest 为特征集等权口径不吃模型预测,过门指标已让位给模型真实成绩")
                break
        if main is None and has_ml:                 # 残余缺口(rr_3e2e4dbe58):图无模型报告终端
            for nid in reversed(order):             # → 直取 mf/ML 节点输出,绝不静默回落 backtest
                typ = str(by_id[nid].get("type") or "")
                o = outputs.get(nid) or {}
                payload = None
                if typ == "mf" and isinstance(o.get("factor"), dict) \
                        and o["factor"].get("_kind"):
                    payload = o["factor"]
                elif typ in _ML_KINDS:
                    rep = _model_report(o.get("model"))
                    if isinstance(rep, dict):
                        payload = dict(rep)
                        payload.setdefault("_kind", (o.get("model") or {}).get("_kind"))
                if isinstance(payload, dict) and payload.get("_kind"):
                    main = {"kind": typ, "node_id": nid, "payload": payload}
                    warnings.append(
                        f"ML 图缺 analysis/iccalc 模型报告终端——主终端直取 {nid}({typ}) "
                        "模型报告(prefer_model_terminal)"
                        + (";backtest 为特征集等权口径不吃模型预测,已让位"
                           if any(t["kind"] == "backtest" for t in terminals) else "")
                        + ";建议给 mf 下游补 analysis 终端")
                    break
            if main is None:
                warnings.append(
                    "prefer_model_terminal 开启但图中无模型报告(ML 训练失败或 mf 未接模型)"
                    "——过门指标为非模型口径")
    if main is None:
        for kind in _TERMINAL_PRIORITY:             # 优先级取主终端;同类取拓扑序最后
            cand = [t for t in terminals if t["kind"] == kind]
            if cand:
                main = cand[-1]
                break
    metrics = metrics_of_terminal(main["payload"]) if main else None

    # An overfit node is a graph-level hard gate, not merely another terminal
    # candidate.  In particular, ``prefer_model_terminal`` may legitimately
    # choose an ML analysis report, and a failed audit node may leave a healthy
    # backtest terminal behind.  Neither situation may erase the declared
    # audit.  Fold every declared audit outcome into the selected terminal's
    # metrics and fail closed when any audit did not produce trustworthy PASS.
    audit_gate = None
    if audit_node_ids:
        audit_payloads = {
            str(t["node_id"]): t["payload"]
            for t in terminals
            if t.get("kind") == "overfit" and isinstance(t.get("payload"), dict)
        }
        audit_outcomes: List[Dict[str, Any]] = []
        for audit_id in audit_node_ids:
            payload = audit_payloads.get(audit_id)
            if payload is None:
                error = next((e.get("error") for e in node_errors
                              if e.get("nid") == audit_id), None)
                audit_outcomes.append({"node_id": audit_id, "status": "ERROR",
                                       "passes_gate": False,
                                       "reason": error or "审计节点未产出"})
                continue
            status = str(payload.get("status") or "INCOMPLETE").upper()
            if status not in {"PASS", "FAIL", "INCOMPLETE"}:
                status = "INCOMPLETE"
            passes = status == "PASS" and payload.get("passes_gate") is True
            # A contradictory payload (PASS but passes_gate=false) is not a
            # valid PASS.  Treat it as incomplete evidence rather than trusting
            # either field in isolation.
            if status == "PASS" and not passes:
                status = "INCOMPLETE"
            audit_outcomes.append({"node_id": audit_id, "status": status,
                                   "passes_gate": passes})

        statuses = {item["status"] for item in audit_outcomes}
        execution_error = "ERROR" in statuses
        multiple_audits = len(audit_node_ids) > 1
        model_binding_required = bool(
            prefer_model_terminal and has_ml and main is not None
            and main.get("kind") != "overfit"
        )
        model_binding_verified = False
        if model_binding_required:
            for payload in audit_payloads.values():
                binding = payload.get("selection_binding") \
                    if isinstance(payload.get("selection_binding"), dict) else {}
                if (binding.get("verified") is True
                        and str(binding.get("terminal_node_id") or "") == str(main.get("node_id") or "")):
                    model_binding_verified = True
                    break
        # Public audit status intentionally stays three-state.  Execution
        # failures mean evidence is unavailable, hence INCOMPLETE; the detailed
        # node outcome and execution_error flag retain the operational cause.
        if "FAIL" in statuses:
            audit_status = "FAIL"
        elif (execution_error or multiple_audits
              or (model_binding_required and not model_binding_verified)
              or "INCOMPLETE" in statuses):
            audit_status = "INCOMPLETE"
        else:
            audit_status = "PASS"
        audit_passes = bool(audit_outcomes) and not multiple_audits \
            and (not model_binding_required or model_binding_verified) \
            and audit_status == "PASS" \
            and all(item["passes_gate"] is True for item in audit_outcomes)
        audit_gate = {"declared": True, "status": audit_status,
                      "passes_gate": audit_passes,
                      "execution_error": execution_error,
                      "configuration_error": multiple_audits,
                      "model_binding_required": model_binding_required,
                      "model_binding_verified": model_binding_verified,
                      "nodes": audit_outcomes}
        if metrics is None:
            metrics = {}
        metrics["audit_declared"] = True
        metrics["audit_status"] = audit_status
        metrics["audit_passes_gate"] = audit_passes
        metrics["audit_execution_error"] = execution_error

        missing = [item for item in audit_outcomes if item["status"] == "ERROR"]
        if missing:
            ids = ", ".join(item["node_id"] for item in missing)
            warnings.append(
                f"图声明了过拟合审计，但节点 {ids} 未产出；"
                "审计按 INCOMPLETE 拒绝晋级并保留执行错误")
        if multiple_audits:
            warnings.append(
                "图含多个过拟合审计节点；统计证据不可聚合，硬门拒绝 PASS。每张图只保留一个审计节点")
        if model_binding_required and not model_binding_verified:
            warnings.append(
                "ML 晋级口径来自模型报告，但审计收益来自另一条回测路径；"
                "缺少服务端 selection_binding，审计按 INCOMPLETE 拒绝替模型背书")
        aggregate_reasons: List[str] = []
        if multiple_audits:
            aggregate_reasons.append("每张工作流只能有一个过拟合审计节点；多个审计无统一统计口径")
        if model_binding_required and not model_binding_verified:
            aggregate_reasons.append(
                "审计收益尚未与本轮实际晋级的模型终端/variant 精确绑定，不能用特征等权回测替模型背书")
        for item in audit_outcomes:
            payload = audit_payloads.get(item["node_id"]) or {}
            groups = (payload.get("failed_checks"), payload.get("reasons"),
                      [item.get("reason")] if item.get("reason") else [])
            for group in groups:
                if not isinstance(group, list):
                    continue
                for reason_item in group:
                    reason_text = str(reason_item).strip()
                    if reason_text and reason_text not in aggregate_reasons:
                        aggregate_reasons.append(reason_text)
        # Multiple audits are unusual but legal.  The durable result drawer
        # consumes terminal.payload, so project the aggregate status there as
        # well; otherwise the last PASS terminal could visually contradict an
        # earlier FAIL even though metrics correctly failed closed.
        if main is not None and main.get("kind") == "overfit":
            aggregate_payload = dict(main["payload"])
            aggregate_payload["status"] = audit_status
            aggregate_payload["passes_gate"] = audit_passes
            aggregate_payload["audit_aggregate"] = audit_gate
            if multiple_audits:
                # Do not display one node's green PSR/DSR/PBO as if it were the
                # aggregate. The graph configuration itself is unsupported.
                aggregate_payload["metrics"] = {}
                aggregate_payload["evidence"] = {"audit_nodes": audit_outcomes}
                aggregate_payload["thresholds"] = {}
            if audit_status == "FAIL":
                aggregate_payload["failed_checks"] = aggregate_reasons
            elif audit_status == "INCOMPLETE":
                aggregate_payload["reasons"] = aggregate_reasons
            main = dict(main, payload=aggregate_payload)
        elif main is not None and not audit_payloads:
            # The audit node itself failed, but a source backtest/model terminal
            # survived. Surface a synthetic INCOMPLETE audit terminal so durable
            # reconnect opens the audit decision view instead of a pretty source
            # report that visually hides the hard-gate failure.
            source_payload = main.get("payload") if isinstance(main.get("payload"), dict) else {}
            synthetic = {
                "ok": True, "__dt": "overfit_audit", "method": "overfit_audit",
                "status": "INCOMPLETE", "passes_gate": False,
                "metrics": {}, "evidence": {}, "thresholds": {},
                "reasons": aggregate_reasons or ["过拟合审计节点未产出"],
                "warnings": list(warnings), "audit_aggregate": audit_gate,
                "_label": source_payload.get("_label") or "回测过拟合审计",
                "_universe": source_payload.get("_universe") or global_universe,
                "source_terminal": {"kind": main.get("kind"),
                                    "node_id": main.get("node_id")},
            }
            for key in ("portfolio", "ic", "oos", "headline_ic", "orthogonalization", "factor_spec"):
                if key in source_payload:
                    synthetic[key] = source_payload[key]
            main = {"kind": "overfit", "node_id": audit_node_ids[-1],
                    "payload": synthetic, "synthetic": True}
            warnings.append("审计执行未产出；已合成 INCOMPLETE 审计终端供结果页显形")
        if main is not None and main.get("kind") != "overfit":
            warnings.append(
                f"主终端为 {main.get('kind')}；已独立合并过拟合审计硬门，"
                "模型或回测指标不能绕过审计结果")
    diagnostic_result = False
    declared_primary = any(n.get("type") in set(_TERMINAL_PRIORITY) | set(_ML_KINDS) | {"mf", "portfolio"}
                           for n in nodes)
    if (main is None and allow_diagnostic_result and not prefer_model_terminal
            and not declared_primary and not node_errors):
        diagnostics = [t for t in terminals if t["kind"] in _DIAG_TYPES]
        if diagnostics:
            main = diagnostics[-1]
            diagnostic_result = True
            # Do not extract return/IC-looking numbers from diagnostic payloads
            # into the research gate contract, including portfolio diagnostics.
            metrics = None
            warnings.append("诊断产物已完成；仅供描述/体检，不是回测或策略晋级通过，过门指标为空。")
    if main is None:
        warnings.append("无主终端产出(回测/分析/IC)——过门指标缺失")
    ok = main is not None
    reason = None if ok else (node_errors[0]["error"] if node_errors else "图无主终端节点")
    return {"ok": ok, "reason": reason, "terminal": main,
            **({"result_kind": "diagnostic", "gate_eligible": False} if diagnostic_result else {}),
            # Compatibility keeps ``terminal`` as the selected main result.
            # Only parallel graphs need the extra list; single heavy backtests
            # are not duplicated in every durable JSON record.
            **({"all_terminals": terminals} if len(terminals) > 1 else {}),
            "metrics": metrics,
            "audit": audit_gate,
            "exprs": all_exprs, "has_ml": has_ml, "node_results": node_results,
            "node_errors": node_errors, "warnings": warnings,
            "elapsed_sec": round(time.time() - t0, 1)}
