# -*- coding: utf-8 -*-
"""研究回路编排器(P2/P4):提案→求值(整图执行)→过门→批判改进,逐轮落档。

确定性编排,LLM 只在提案/批判两个接缝:经**同步 HTTP 自调**本进程 /workflow/generate|critique
(handler 是 router 闭包不可 import;且 engine LLM 客户端连接池绑事件循环,daemon 线程反复
asyncio.run 会炸 "Event loop is closed"——HTTP 自调让 LLM 落在 server 主循环上,零复制同一实现)。
本模块只会在 9999 进程内的 daemon 线程里跑(api.py 状态机拉起),同步自 HTTP 安全(仓级红线
只禁「协程内」同步自 HTTP)。求值段(P4)整图交给 workflow.executor.run_graph 真执行——节点
参数(ML 超参/回测 topn/analysis dir 等)全部真生效,不再是"三道菜"(report2/compose/backtest)
直调固定形状的窄口径。

红线:提案失败诚实终止(绝不降级模板,严于前端 aiLoop);规则兜底显形 source=rule;
产物一律 draft 人审,绝不自动上架(P4 三通道:ML 图→模型 train_promote 强制 draft;
≥2 表达式→组合权重物化 draft;单表达式→现状;skipped_multi 语义已退役);失败也写教训。
停滞守卫(2026-07-03 真机 v4-pro 暴露,P4 升级为图签名比较):批判若产出与上一轮完全相同的图
(graph_signature 相等)则指标必然逐位不变=烧轮次装研究;批判环显式声明求值语义(constraints),
改进产出同签名图 → 带停滞警告重批一次,仍相同 → 诚实中断。
独立小函数便于 monkeypatch(仓例 console/tools.py)。
"""
from __future__ import annotations

import json
import inspect
import os
import urllib.request
import uuid
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Tuple

from guanlan_v2.research import store as rstore
from guanlan_v2.workflow import executor as wex

GATE_OOS_OK = "robust"      # 过门要求的样本外判定
_EVAL_OOS_FRAC = 0.3        # 求值一律开样本外(oos 不开 verdict 恒缺 → 门永不过)

# 批判环求值语义声明(经 CritiqueIn.constraints 送进 LLM prompt,P4:整图执行器真跑全部节点)。
_CRITIQUE_CONSTRAINTS = (
    "本图由研究回路后端全图执行:所有节点参数均真实生效(ML 超参、回测 topn/weighting、"
    "分析 rebal/groups/dir、组合 combine 等都会改变指标);股票池与调仓频率由回路参数固定,"
    "图内数据源节点的 universe 不生效。改进可落在:因子表达式、特征组合、模型类型与超参、"
    "多因子合成方式、回测/分析参数。多特征 ML:把多个 formula 各连一条边到同一个 feature "
    "节点的 feat 口即聚合为多特征训练(保序去重);其余同一输入口多条边仅最后一条生效并记"
    "警告。feature.params.tag 只是标注:只能留空或写 IC/fwd_ret(默认标签=未来收益);写"
    "任何其他文本会被当作 label 表达式执行(如 tag=\"ML\" 会炸 ML 训练),自定义预测标签"
    "必须用 formula 连 feature.label 口。ML 图的过门指标取模型真实 OOS 成绩(analysis 模型"
    "报告),不是特征集等权回测,且必须给 mf 下游接 analysis 终端(缺失时执行器直取 mf 模型"
    "报告并记警告,backtest 口径绝不用于过门)。与上一轮完全相同的图不会产生不同指标。")


def _graph_declares_audit(graph: Dict[str, Any]) -> bool:
    """Return whether this graph opts its research lineage into audit gating."""
    nodes = graph.get("nodes") if isinstance(graph, dict) else None
    return any(isinstance(node, dict) and node.get("type") == "overfit"
               for node in (nodes if isinstance(nodes, list) else []))


def _graph_declares_orthogonalization(graph: Dict[str, Any]) -> bool:
    nodes = graph.get("nodes") if isinstance(graph, dict) else None
    return any(isinstance(node, dict) and node.get("type") == "orthogonalize"
               for node in (nodes if isinstance(nodes, list) else []))


def new_run_id() -> str:
    return "rr_" + uuid.uuid4().hex[:10]


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


# ── 桥(独立小函数便于 monkeypatch)──────────────────────────────────────

def _self_post(path: str, payload: Dict[str, Any], timeout: int = 300) -> Dict[str, Any]:
    """同步自 HTTP(仅 daemon 线程调用,永不进事件循环)。"""
    port = os.environ.get("GUANLAN_PORT", "9999")
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _call_generate(goal: str) -> Dict[str, Any]:
    return _self_post("/workflow/generate", {"goal": goal}, timeout=300)


def _call_critique(goal: str, metrics: Dict[str, Any], graph: Dict[str, Any],
                   constraints: str = "") -> Dict[str, Any]:
    return _self_post("/workflow/critique",
                      {"goal": goal, "metrics": metrics, "graph": graph,
                       "constraints": constraints}, timeout=300)


def _run_graph_eval(graph: Dict[str, Any], p: Dict[str, Any],
                    progress: Callable[..., None], k: int, max_rounds: int) -> Dict[str, Any]:
    """整图执行薄桥(独立便于 monkeypatch)。on_node 把当前节点类型打进进度 label。"""
    trusted_backtests: Dict[str, Any] = {}

    def _on(nid, typ, state):
        if state == "running":
            progress(phase="evaluate",
                     label=f"② 第 {k + 1}/{max_rounds} 轮 · 图执行:{typ}…", round_k=k)

    def _capture(nid, typ, out):
        # Never persist this through research.store: it remains an in-process
        # server artifact until TrialLedger has revealed the matching trial.
        if typ == "backtest" and isinstance(out, dict) and isinstance(out.get("result"), dict):
            trusted_backtests[str(nid)] = out["result"]

    call_kw = dict(overrides={
        "universe": p["universe"], "freq": p["freq"], "oos_frac": _EVAL_OOS_FRAC,
        "start": p.get("start"), "end": p.get("end"),
        # The TrialLedger family is frequency-bound.  A candidate may not
        # silently change its backtest node to another rebalance frequency.
        "force_rebalance": True}, on_node=_on,
        prefer_model_terminal=True)
    # Test doubles and third-party adapters built against the older additive
    # signature keep working; the production executor advertises this seam.
    try:
        supports_sink = "artifact_sink" in inspect.signature(wex.run_graph).parameters
    except (TypeError, ValueError):
        supports_sink = False
    if supports_sink:
        call_kw["artifact_sink"] = _capture
    outcome = wex.run_graph(graph, **call_kw)
    # Underscored keys are consumed only by optimize_adapter's trusted
    # connector.  The ordinary loop selects explicit display fields and never
    # writes these return paths into its caller-editable JSONL archive.
    if supports_sink:
        outcome["_trusted_backtests"] = trusted_backtests
        outcome["_trusted_graph"] = graph
    return outcome   # 过门语义=模型真实成绩(ML 图不吃特征等权回测口径)


def _save_draft(run_id: str, k: int, expr: str, goal: str, diag: str,
                metrics: Dict[str, Any]) -> Dict[str, Any]:
    """达标因子存 factorlib(status=draft,绝不自动上架;人审 /factorlib/promote 转正)。"""
    from guanlan_v2.factorlib.api import SaveIn, _save_factor
    from guanlan_v2.factorlib.store import LibraryFactorStore
    name = f"lib_rl_{run_id[-6:]}_r{k}"
    body = SaveIn(name=name, expr=expr, family="library_mined",
                  description=f"研究回路产出:{goal[:60]} · {str(diag)[:80]}",
                  source="research_loop", status="draft",
                  meta={"metrics": metrics, "run_id": run_id, "round": k})
    return _save_factor(body, LibraryFactorStore())


def _save_compose_expr(name: str, expr: str, goal: str, diag: str,
                       meta: Dict[str, Any]) -> Dict[str, Any]:
    """组合物化表达式存 factorlib draft(独立便于 monkeypatch)。"""
    from guanlan_v2.factorlib.api import SaveIn, _save_factor
    from guanlan_v2.factorlib.store import LibraryFactorStore
    body = SaveIn(name=name, expr=expr, family="library_mined",
                  description=f"研究回路组合产出:{goal[:60]} · {str(diag)[:80]}",
                  source="research_loop", status="draft", meta=meta)
    return _save_factor(body, LibraryFactorStore())


def _structured_factor_spec_from_graph(graph: Dict[str, Any]) -> Dict[str, Any]:
    """Extract one exact formula/factorlib -> orthogonalize product definition.

    ``guanlan.factor_spec.v1`` deliberately represents one transformed factor,
    not an arbitrary downstream model/composite.  Ambiguous/multi-transform
    graphs fail closed instead of publishing a product whose validation metrics
    belonged to a different path.
    """
    from guanlan_v2.factorlib.specs import pin_factor_spec_dependencies

    nodes = [n for n in (graph.get("nodes") or []) if isinstance(n, dict) and n.get("id")]
    by_id = {str(n["id"]): n for n in nodes}
    orths = [n for n in nodes if n.get("type") == "orthogonalize"]
    if len(orths) != 1:
        raise ValueError("结构化因子发布要求且只允许一个正交化节点")
    orth = orths[0]
    incoming = [e for e in (graph.get("edges") or []) if isinstance(e, dict)
                and isinstance(e.get("from"), list) and isinstance(e.get("to"), list)
                and len(e["from"]) >= 2 and len(e["to"]) >= 2
                and str(e["to"][0]) == str(orth["id"]) and e["to"][1] == "signal"]
    if len(incoming) != 1:
        raise ValueError("正交化 signal 必须精确连接一个公式或因子库来源")
    source = by_id.get(str(incoming[0]["from"][0])) or {}
    params = source.get("params") if isinstance(source.get("params"), dict) else {}
    if source.get("type") == "formula":
        base = str(params.get("expr") or "").strip()
    elif source.get("type") == "factorlib":
        if isinstance(params.get("factor_spec"), dict) \
                or str(params.get("factor_spec_digest") or "").strip():
            raise ValueError(
                "factor_spec v1 不支持把结构化因子作为另一正交化规范的基础依赖"
            )
        # Match executor semantics for a legacy factorlib node: its cached DSL
        # expression is the executable output.  If an older graph only stored
        # the name, resolve it exactly and refuse a structured/nested record.
        base = str(params.get("expr") or "").strip()
        library_name = str(params.get("name") or "").strip()
        if library_name:
            from guanlan_v2.factorlib.store import LibraryFactorStore
            rows = LibraryFactorStore().list_factors(validate=False)
            hit = next((row for row in rows
                        if str(row.get("name") or "").lower() == library_name.lower()), None)
            if hit is not None and isinstance(hit.get("factor_spec"), dict):
                raise ValueError("factor_spec v1 不支持嵌套结构化基础因子")
            if not base and hit is None:
                raise ValueError(f"正交化来源因子库记录已缺失: {library_name}")
            if not base and hit is not None:
                base = str(hit.get("expr") or "").strip()
    else:
        raise ValueError("正交化来源必须是公式输入或因子库")
    if not base:
        raise ValueError("正交化来源缺少可重放表达式/因子名")
    spec = pin_factor_spec_dependencies({
        "schema": "guanlan.factor_spec.v1",
        "kind": "orthogonalized",
        "base_features": [base],
        "transform": dict(orth.get("params") or {}),
    })
    return spec


def _direct_structured_factor_from_graph(
    graph: Dict[str, Any],
) -> Optional[Tuple[Dict[str, Any], str, str]]:
    """Resolve one direct factorlib v1 product without flattening it to expr.

    Returns ``(canonical_spec, digest, source_node_id)``.  The store is the
    current authority; graph params are a pinned snapshot and must match it.
    Plain legacy factorlib nodes return ``None`` and keep their old route.
    """
    from guanlan_v2.factorlib.specs import (
        factor_spec_digest, verify_factor_spec_dependencies,
    )
    from guanlan_v2.factorlib.store import LibraryFactorStore

    nodes = [node for node in (graph.get("nodes") or [])
             if isinstance(node, dict) and node.get("id")]
    factor_nodes = [node for node in nodes if node.get("type") == "factorlib"]
    if not factor_nodes:
        return None
    rows = LibraryFactorStore().list_factors(validate=False)
    by_name = {str(row.get("name") or "").lower(): row for row in rows}
    candidates: List[Tuple[Dict[str, Any], Dict[str, Any]]] = []
    for node in factor_nodes:
        params = node.get("params") if isinstance(node.get("params"), dict) else {}
        expected_spec = params.get("factor_spec")
        expected_digest = str(params.get("factor_spec_digest") or "").strip()
        name = str(params.get("name") or "").strip()
        hit = by_name.get(name.lower()) if name else None
        if hit is not None and isinstance(hit.get("factor_spec"), dict):
            candidates.append((node, hit))
        elif isinstance(expected_spec, dict) or expected_digest:
            raise ValueError(
                f"结构化因子库引用 '{name}' 已缺失或已降级为普通表达式"
            )
    if not candidates:
        return None
    if len(candidates) != 1:
        raise ValueError("同一研究图含多个结构化因子，v1 不能冒充复合产物发布")
    # A direct structured product must be the only factor-producing source.
    # Formula labels belong to ML graphs, but keeping this check local also
    # protects malformed/old graphs whose feature-composite path lacks a model.
    formula_nodes = [node for node in nodes if node.get("type") == "formula"]
    if len(factor_nodes) != 1 or formula_nodes:
        raise ValueError("结构化因子与其他公式/因子库来源混合，不能作为单因子发布")

    node, hit = candidates[0]
    params = node.get("params") if isinstance(node.get("params"), dict) else {}
    canonical = verify_factor_spec_dependencies(
        hit["factor_spec"], fallback_expr=str(hit.get("expr") or "").strip(),
        factor_name=str(hit.get("name") or "").strip(),
    )
    digest = factor_spec_digest(canonical)
    stored_digest = str(hit.get("factor_spec_digest") or "").strip()
    if stored_digest and stored_digest != digest:
        raise ValueError("结构化因子库记录的持久摘要与 canonical spec 不一致")
    expected_spec = params.get("factor_spec")
    if isinstance(expected_spec, dict):
        expected = verify_factor_spec_dependencies(expected_spec)
        if expected != canonical:
            raise ValueError("研究图中的结构化 factor_spec 快照与因子库不一致")
    expected_digest = str(params.get("factor_spec_digest") or "").strip()
    if expected_digest and expected_digest != digest:
        raise ValueError("研究图中的结构化 factor_spec_digest 与因子库不一致")
    # A pinned snapshot is intentionally not required: old saved graphs may
    # only carry the exact factorlib name.  Once the authoritative record is
    # structured, it must still take this non-lossy path.
    return canonical, digest, str(node["id"])


def _validate_structured_terminal_binding(
    graph: Dict[str, Any], terminal: Optional[Dict[str, Any]],
    factor_spec: Dict[str, Any], *, product_node_id: Optional[str] = None,
) -> str:
    """Bind a structured product to the exact terminal that supplied its metrics.

    Merely finding an orthogonalize node somewhere in the graph is insufficient:
    an unrelated branch could otherwise supply passing metrics while the orthogonal
    branch is published.  Require a directed path to this run's terminal and require
    the terminal's propagated spec to canonically match the graph-derived spec.
    """
    from guanlan_v2.factorlib.specs import (
        factor_spec_digest, verify_factor_spec_dependencies,
    )

    if not isinstance(terminal, dict):
        raise ValueError("结构化因子发布缺少本轮实际终点")
    terminal_id = str(terminal.get("node_id") or "").strip()
    if not terminal_id:
        raise ValueError("结构化因子发布要求 terminal.node_id")

    nodes = [n for n in (graph.get("nodes") or [])
             if isinstance(n, dict) and n.get("id")]
    by_id = {str(n["id"]): n for n in nodes}
    terminal_node = by_id.get(terminal_id)
    if terminal_node is None:
        raise ValueError(f"本轮终点 {terminal_id} 不属于当前工作流")
    terminal_kind = str(terminal.get("kind") or "").strip()
    if terminal_kind and terminal_kind != str(terminal_node.get("type") or ""):
        raise ValueError("本轮 terminal.kind/node_id 与当前工作流节点不一致")

    if product_node_id is None:
        orths = [n for n in nodes if n.get("type") == "orthogonalize"]
        if len(orths) != 1:  # normally rejected by extraction; keep this guard local too
            raise ValueError("结构化因子发布要求且只允许一个正交化节点")
        product_node_id = str(orths[0]["id"])
    product_node_id = str(product_node_id or "").strip()
    if product_node_id not in by_id:
        raise ValueError("结构化产物来源节点不属于当前工作流")
    adjacency: Dict[str, List[str]] = {}
    for edge in (graph.get("edges") or []):
        if not isinstance(edge, dict):
            continue
        source, target = edge.get("from"), edge.get("to")
        if not (isinstance(source, list) and source
                and isinstance(target, list) and target):
            continue
        source_id, target_id = str(source[0]), str(target[0])
        if source_id in by_id and target_id in by_id:
            adjacency.setdefault(source_id, []).append(target_id)
    pending = [product_node_id]
    reachable = set()
    while pending:
        node_id = pending.pop()
        if node_id in reachable:
            continue
        reachable.add(node_id)
        pending.extend(adjacency.get(node_id, ()))
    if terminal_id not in reachable:
        raise ValueError("结构化产物节点不可达本轮实际终点，拒绝用其他分支指标发布")

    payload = terminal.get("payload")
    if not isinstance(payload, dict) or not isinstance(payload.get("factor_spec"), dict):
        raise ValueError("本轮终点未携带可验证的 factor_spec")
    terminal_spec = verify_factor_spec_dependencies(payload["factor_spec"])
    if terminal_spec is None or terminal_spec != factor_spec:
        raise ValueError("本轮终点 factor_spec 与工作流正交化定义不一致")
    expected_digest = factor_spec_digest(factor_spec)
    if factor_spec_digest(terminal_spec) != expected_digest:
        raise ValueError("本轮终点 factor_spec digest 与工作流定义不一致")
    declared_digest = str(payload.get("factor_spec_digest") or "").strip()
    if declared_digest and declared_digest != expected_digest:
        raise ValueError("本轮终点声明的 factor_spec_digest 与工作流定义不一致")
    return expected_digest


def _save_structured_draft(run_id: str, k: int, factor_spec: Dict[str, Any],
                           goal: str, diag: str, metrics: Dict[str, Any]) -> Dict[str, Any]:
    """Persist a validated orthogonalized factor as an executable draft."""
    from guanlan_v2.factorlib.api import SaveIn, _save_factor
    from guanlan_v2.factorlib.store import LibraryFactorStore

    name = f"lib_rl_{run_id[-6:]}_r{k}"
    body = SaveIn(
        name=name, expr=str(factor_spec["base_features"][0]), factor_spec=factor_spec,
        family="library_mined", status="draft", source="research_loop",
        description=f"研究回路结构化产出:{goal[:60]} · {str(diag)[:80]}",
        meta={"metrics": metrics, "run_id": run_id, "round": k,
              "product_kind": "structured_factor"},
    )
    return _save_factor(body, LibraryFactorStore())


def _call_train_promote(spec: Dict[str, Any]) -> Dict[str, Any]:
    from guanlan_v2.strategy.compute.model_workflow import train_promote
    return train_promote(spec)


def _derive_ml_recipe(graph: Dict[str, Any], universe: str) -> Optional[Dict[str, Any]]:
    """镜像前端 deriveRecipeForNode(workflow.jsx:779-807):首个 ML 节点 → recipe。
    features=其上游 feature 节点 feat 口表达式保序去重;label 同前端语义;params 经
    executor._HPMAP 画布名→后端名;universe=回路 run 参数权威。查无 ML 节点 → None。"""
    from guanlan_v2.workflow.executor import _HPMAP, _ML_KINDS, _num
    nodes = graph.get("nodes") if isinstance(graph.get("nodes"), list) else []
    edges = graph.get("edges") if isinstance(graph.get("edges"), list) else []
    by_id = {n.get("id"): n for n in nodes if isinstance(n, dict)}
    ml = next((n for n in nodes if isinstance(n, dict) and n.get("type") in _ML_KINDS), None)
    if ml is None:
        return None
    feats: List[str] = []
    label: Optional[str] = None
    for e in edges:
        to = e.get("to") or [None, None]
        if to[0] != ml.get("id") or to[1] != "fe":
            continue
        fn = by_id.get((e.get("from") or [None])[0])
        if not (fn and fn.get("type") == "feature"):
            continue
        for e2 in edges:
            t2 = e2.get("to") or [None, None]
            if t2[0] != fn.get("id"):
                continue
            up = by_id.get((e2.get("from") or [None])[0])
            expr = str(((up or {}).get("params") or {}).get("expr") or "").strip()
            if t2[1] == "feat" and expr and expr not in feats:
                feats.append(expr)
            if t2[1] == "label" and label is None and expr:
                label = expr
        if label is None:
            tag = str((fn.get("params") or {}).get("tag") or "").strip()
            if tag.lower() not in ("", "ic", "fwd_ret"):
                label = tag
    params: Dict[str, Any] = {}
    for bk, ck in _HPMAP.get(ml["type"], {}).items():
        v = (ml.get("params") or {}).get(ck)
        if v in (None, ""):
            continue
        n = _num(v)
        params[bk] = (int(n) if (n is not None and float(n).is_integer())
                      else (n if n is not None else v))
    return {"kind": _ML_KINDS[ml["type"]], "features": feats, "label": label,
            "fwd_days": 5, "universe": universe, "params": params,
            "_node_id": ml.get("id")}   # 路由用(该 ML 节点求值是否失败),入 spec 前 pop


def _route_product(run_id: str, k: int, graph: Dict[str, Any], goal: str, diag: str,
                   metrics: Dict[str, Any], terminal: Optional[Dict[str, Any]],
                   universe: str,
                   node_errors: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """达标产物三通道路由(spec §4):ML 图→模型 draft;≥2 表达式→组合物化 draft;单→现状。

    保真度1(rr_3af347074b 实证):recipe 所属 ML 节点本轮求值失败(node_errors 有记录)时,
    过闸指标来自 mf 回退口径而非模型 → 模型通道不可信,改走组合/单因子通道并带 note 显形。"""
    if _graph_declares_orthogonalization(graph):
        from guanlan_v2.workflow.executor import _ML_KINDS as _MODEL_TYPES
        node_types = {str(n.get("type") or "") for n in (graph.get("nodes") or [])
                      if isinstance(n, dict)}
        if node_types.intersection(set(_MODEL_TYPES) | {"mf", "pca", "spearman"}):
            return {
                "name": None, "status": "workflow_only",
                "reason": (
                    "该图的达标指标属于模型/复合产物，不能拿其中一个正交因子冒充整图产物；"
                    "工作流已保留，待模型 recipe/复合 factor_spec 支持后再发布"
                ),
            }
        try:
            factor_spec = _structured_factor_spec_from_graph(graph)
            expected_digest = _validate_structured_terminal_binding(
                graph, terminal, factor_spec,
            )
            pr = _save_structured_draft(run_id, k, factor_spec, goal, diag, metrics)
        except Exception as exc:  # noqa: BLE001 - malformed/ambiguous spec is visible
            pr = {"ok": False, "reason": f"{type(exc).__name__}: {exc}"}
        if pr.get("ok") and pr.get("registered"):
            if str(pr.get("factor_spec_digest") or "") != expected_digest:
                return {
                    "name": None, "status": "save_failed",
                    "reason": "结构化因子保存返回的 factor_spec_digest 与已验证定义不一致",
                }
            return {"name": pr.get("name"), "status": "draft_structured",
                    "factor_spec_digest": pr.get("factor_spec_digest")}
        return {"name": None, "status": "save_failed",
                "reason": pr.get("reason") or "结构化因子保存/注册失败"}

    # A graph may reference an already structured factorlib factor directly.
    # It remains structured across report/backtest publication; routing it
    # through _pick_dish/_save_draft would persist only its display name/base
    # expression and silently discard the transform.
    try:
        direct_structured = _direct_structured_factor_from_graph(graph)
    except Exception as exc:  # malformed/missing pinned reference is fail-closed
        return {"name": None, "status": "save_failed",
                "reason": f"{type(exc).__name__}: {exc}"}
    if direct_structured is not None:
        from guanlan_v2.workflow.executor import _ML_KINDS as _MODEL_TYPES
        node_types = {str(node.get("type") or "") for node in (graph.get("nodes") or [])
                      if isinstance(node, dict)}
        if node_types.intersection(set(_MODEL_TYPES) | {"mf", "pca", "spearman"}):
            return {
                "name": None, "status": "workflow_only",
                "reason": (
                    "该图的指标属于模型/复合产物，不能把其中直接引用的结构化因子"
                    "冒充整图产物；工作流已保留"
                ),
            }
        factor_spec, pinned_digest, source_node_id = direct_structured
        try:
            terminal_digest = _validate_structured_terminal_binding(
                graph, terminal, factor_spec, product_node_id=source_node_id,
            )
            if terminal_digest != pinned_digest:
                raise ValueError("终点摘要与因子库固定摘要不一致")
            pr = _save_structured_draft(
                run_id, k, factor_spec, goal, diag, metrics,
            )
        except Exception as exc:  # noqa: BLE001
            pr = {"ok": False, "reason": f"{type(exc).__name__}: {exc}"}
        if pr.get("ok") and pr.get("registered"):
            if str(pr.get("factor_spec_digest") or "") != pinned_digest:
                return {
                    "name": None, "status": "save_failed",
                    "reason": "结构化因子复用保存返回了不同的 factor_spec_digest",
                }
            return {"name": pr.get("name"), "status": "draft_structured",
                    "factor_spec_digest": pinned_digest}
        return {"name": None, "status": "save_failed",
                "reason": pr.get("reason") or "结构化因子复用保存/注册失败"}

    note: Optional[str] = None
    recipe = _derive_ml_recipe(graph, universe)
    if recipe is not None:
        ml_nid = recipe.pop("_node_id", None)
        failed = {str((e or {}).get("nid")) for e in (node_errors or []) if isinstance(e, dict)}
        if ml_nid is not None and str(ml_nid) in failed:
            note = (f"ML 节点 {ml_nid} 本轮求值失败(见 node_errors),过闸指标为 mf 回退"
                    "口径 → 模型通道不可信,改走组合/单因子通道")
            recipe = None
    if recipe is not None:                                  # ── 模型通道
        if not recipe["features"]:
            return {"name": None, "status": "save_failed",
                    "reason": "ML 图缺 feature 上游表达式,无法提取 recipe"}
        spec = {"variant_id": f"m_rl_{run_id[-6:]}_r{k}",
                "name": f"研究·{goal[:12]}·r{k}", "kind": recipe.pop("kind"),
                "recipe": recipe, "created": _now(), "status": "draft"}
        try:
            pr = _call_train_promote(spec)
        except Exception as exc:  # noqa: BLE001
            pr = {"ok": False, "reason": f"{type(exc).__name__}: {exc}"}
        if pr.get("ok"):
            return {"name": pr.get("variant_id"), "status": "draft_model"}
        return {"name": None, "status": "save_failed", "reason": pr.get("reason")}
    _, exprs2 = _pick_dish(graph)
    if len(exprs2) >= 2:                                    # ── 组合通道(权重物化)
        payload = (terminal or {}).get("payload") or {}
        members = payload.get("members") or exprs2
        weights = payload.get("weights") or []
        wvals = [(w or {}).get("weight") for w in weights]
        if len(wvals) != len(members) or any(not isinstance(w, (int, float)) for w in wvals):
            wvals = [round(1.0 / len(members), 4)] * len(members)   # 无权重 → 等权(诚实缺省)
        expr = " + ".join(f"({w})*({m})" for m, w in zip(members, wvals))
        name = f"lib_rl_{run_id[-6:]}_r{k}"
        try:
            pr = _save_compose_expr(name, expr, goal, diag,
                                    {"members": members, "weights": weights,
                                     "metrics": metrics, "run_id": run_id, "round": k})
        except Exception as exc:  # noqa: BLE001
            pr = {"ok": False, "reason": f"{type(exc).__name__}: {exc}"}
        res = ({"name": pr.get("name") or name, "status": "draft_compose"} if pr.get("ok")
               else {"name": None, "status": "save_failed", "reason": pr.get("reason")})
    elif len(exprs2) == 1:                                  # ── 单因子通道(现状)
        try:
            pr = _save_draft(run_id, k, exprs2[0], goal, diag, metrics)
        except Exception as exc:  # noqa: BLE001
            pr = {"ok": False, "reason": f"{type(exc).__name__}: {exc}"}
        res = ({"name": pr.get("name"), "status": "draft"} if pr.get("ok")
               else {"name": None, "status": "save_failed", "reason": pr.get("reason")})
    else:
        res = {"name": None, "status": "save_failed", "reason": "图内无可入库表达式"}
    if note:
        res["note"] = note                                  # 改道显形(luozi 卡/终态行直显)
    return res


def _save_graph(goal: str, run_id: str, graph: Dict[str, Any]) -> Dict[str, Any]:
    """最佳轮图存工作流库(用户在工作流页点开上画布;与 /workflow/save 同一 store 文件)。"""
    try:
        from guanlan_v2.workflow.store import WorkflowStore
        rec = WorkflowStore().save(f"研究·{goal[:16]}·{run_id[-6:]}", graph)
        return {"ok": True, "id": rec["id"], "name": rec["name"]}
    except Exception as exc:  # noqa: BLE001 — 存图失败显形进终态行,不挡收工
        return {"ok": False, "reason": f"{type(exc).__name__}: {exc}"}


def _write_lesson(goal: str, summary: str) -> bool:
    """每 run 一条 keyed 常驻记忆(失败也记);同 key 收敛覆盖=同一目标最新认知。
    单行化在此做(memory_write_impl 正常路径不去换行);cap 280 由其内做。"""
    try:
        from guanlan_v2.console.tools import memory_write_impl
        key = "研究·" + goal.replace("\n", " ").strip()[:24]
        r = memory_write_impl(text=summary.replace("\n", " ").replace("\r", " ").strip(),
                              scope="global", key=key)
        return bool(r.get("ok"))
    except Exception:  # noqa: BLE001 — 教训写失败不挡收工,memory_written=False 显形
        return False


# ── 纯函数 ───────────────────────────────────────────────────────────────

def _pick_dish(graph: Dict[str, Any]) -> Tuple[Optional[str], List[str]]:
    """图形状→(菜名, exprs)。formula.params.expr + factorlib.params.name;
    有 backtest 节点→backtest;≥2 表达式→compose;1→report2;0→(None,[]) 诚实不支持。"""
    nodes = graph.get("nodes") if isinstance(graph.get("nodes"), list) else []
    exprs: List[str] = []
    for n in nodes:
        if not isinstance(n, dict):
            continue
        p = n.get("params") or {}
        if n.get("type") == "formula":
            e = str(p.get("expr") or "").strip()
            if e:
                exprs.append(e)
        elif n.get("type") == "factorlib":
            e = str(p.get("name") or p.get("expr") or "").strip()
            if e:
                exprs.append(e)
    if not exprs:
        return None, []
    if any(isinstance(n, dict) and n.get("type") == "backtest" for n in nodes):
        return "backtest", exprs
    if len(exprs) >= 2:
        return "compose", exprs
    return "report2", exprs


def _gate(metrics: Dict[str, Any], min_rank_ic: float,
          audit_required: bool = False) -> Dict[str, Any]:
    """过门(P4 联合门)。

    基础门是 rank_ic + OOS + Sharpe。若工作流显式放了“过拟合审计”
    终端，审计就是额外硬门：只有 PASS 且 ``passes_gate=true`` 才可
    晋级，FAIL/INCOMPLETE 不会被源回测的漂亮指标绕过。
    """
    ric, shp = metrics.get("rank_ic"), metrics.get("sharpe")
    ok_ric = isinstance(ric, (int, float)) and ric == ric
    ok_shp = isinstance(shp, (int, float)) and shp == shp and shp > 0
    audit_present = audit_required or bool(metrics.get("audit_declared")) \
        or "audit_status" in metrics or "audit_passes_gate" in metrics
    audit_ok = (not audit_present) or (
        metrics.get("audit_status") == "PASS"
        and metrics.get("audit_passes_gate") is True
    )
    passed = bool(ok_ric and float(ric) >= float(min_rank_ic)
                  and metrics.get("oos_verdict") == GATE_OOS_OK and ok_shp
                  and audit_ok)
    return {"passed": passed, "min_rank_ic": float(min_rank_ic),
            "oos_required": GATE_OOS_OK, "sharpe_required": True,
            "audit_required": audit_present, "audit_passed": audit_ok}


# ── 主体 ─────────────────────────────────────────────────────────────────

def run_research_loop(run_id: str, goal: str, max_rounds: int, min_rank_ic: float,
                      universe: str, freq: str, start: Optional[str], end: Optional[str],
                      progress: Callable[..., None]) -> Dict[str, Any]:
    """回路主体(daemon 线程内跑)。返回终态行(已落档)。progress(**kw) 由 api.py 状态机提供。"""
    params = {"max_rounds": max_rounds, "min_rank_ic": min_rank_ic,
              "universe": universe, "freq": freq, "start": start, "end": end}
    rounds_ok = rstore.append_run({"run_id": run_id, "kind": "start", "goal": goal,
                                   "params": params, "ts": _now()})
    rounds: List[Dict[str, Any]] = []
    promoted: Optional[Dict[str, Any]] = None
    error: Optional[str] = None

    # ── 提案(失败诚实终止,绝不降级模板)──
    progress(phase="propose", label="① LLM 生成初始工作流…", round_k=0)
    try:
        gen = _call_generate(goal)
    except Exception as exc:  # noqa: BLE001
        gen = {"ok": False, "reason": f"{type(exc).__name__}: {exc}"}
    if not gen.get("ok") or not isinstance(gen.get("graph"), dict):
        error = f"提案失败(诚实终止,不降级模板): {gen.get('reason')}"
        mem = _write_lesson(goal, f"研究「{goal[:40]}」提案即失败:{str(gen.get('reason'))[:120]}")
        end_row = {"run_id": run_id, "kind": "end", "ok": False, "error": error,
                   "n_rounds": 0, "best_k": None, "best_metrics": None, "promoted": None,
                   "workflow_saved": None, "memory_written": mem,
                   "rounds_recorded": rounds_ok, "ts": _now()}
        rstore.append_run(end_row)
        return end_row

    graph: Dict[str, Any] = gen["graph"]
    # Monotonic lineage policy: once any accepted round opts into audit,
    # later critique graphs cannot remove it and promote on unaudited metrics.
    # A later round may add audit, making it sticky from that point onward.
    audit_required_for_run = _graph_declares_audit(graph)
    diag = "初始生成(LLM propose)"
    crit_source: Optional[str] = None
    for k in range(max_rounds):
        # ── 求值(后端整图执行;消掉 critique「指标自报」缺口)──
        progress(phase="evaluate", label=f"② 第 {k + 1}/{max_rounds} 轮 · 后端整图执行…", round_k=k)
        try:
            ex = _run_graph_eval(graph, params, progress, k, max_rounds)
        except Exception as exc:  # noqa: BLE001
            ex = {"ok": False, "reason": f"{type(exc).__name__}: {exc}", "metrics": None,
                  "exprs": [], "has_ml": False, "node_errors": [], "terminal": None}
        failed = not ex.get("ok")
        err = ex.get("reason") if failed else None
        metrics = dict(ex.get("metrics") or {})
        current_has_audit = _graph_declares_audit(graph)
        audit_required_for_run = audit_required_for_run or current_has_audit
        if audit_required_for_run and (
                "audit_status" not in metrics or "audit_passes_gate" not in metrics):
            metrics.update({
                "audit_declared": True,
                "audit_status": "INCOMPLETE",
                "audit_passes_gate": False,
                "audit_missing_from_graph": not current_has_audit,
            })
        exprs = list(ex.get("exprs") or [])
        dish, _ = _pick_dish(graph)                    # 展示口径保留(产物路由 T5 也用)
        gate = (_gate(metrics, min_rank_ic, audit_required=audit_required_for_run) if not failed
                else {"passed": False, "min_rank_ic": float(min_rank_ic),
                      "oos_required": GATE_OOS_OK, "sharpe_required": True})
        row = {"run_id": run_id, "k": k, "ts": _now(),
               "stage": ("propose" if k == 0 else "improve"),
               "diag": diag, "critique_source": crit_source,
               "exprs": exprs, "dish": dish, "metrics": metrics, "gate": gate,
               "failed": failed, "error": err, "graph": graph,
               "terminal_kind": ((ex.get("terminal") or {}) or {}).get("kind"),
               "node_errors": (ex.get("node_errors") or [])[:5]}
        if not rstore.append_round(row):
            rounds_ok = False
        rounds.append(row)
        # ── 过门 → draft 入库 + 提前收工 ──
        if gate["passed"]:
            progress(phase="promote", label="③ 达标 · 产物入库(draft 待人审)…", round_k=k)
            promoted = _route_product(run_id, k, graph, goal, diag, metrics,
                                      ex.get("terminal"), universe,
                                      node_errors=ex.get("node_errors"))
            break
        if k + 1 >= max_rounds:
            break
        # ── 批判改进(LLM;失败规则兜底显形;批判环整体失败 → 诚实中断)──
        progress(phase="critique", label=f"④ 第 {k + 1} 轮未达标 · LLM 批判改进…", round_k=k)
        critique_constraints = _CRITIQUE_CONSTRAINTS
        if audit_required_for_run:
            critique_constraints += (
                " 本研究谱系已启用过拟合审计:后续每一轮必须保留且正确连接 overfit 节点;"
                "不得删除审计、断开其回测输入,或改用普通回测/模型报告绕过审计硬门。")
        try:
            cr = _call_critique(goal, metrics, graph, constraints=critique_constraints)
        except Exception as exc:  # noqa: BLE001
            cr = {"ok": False, "reason": f"{type(exc).__name__}: {exc}"}
        if not cr.get("ok") or not isinstance(cr.get("graph"), dict):
            error = f"批判环失败: {cr.get('reason')}"
            break
        audit_retried = False
        if audit_required_for_run and not _graph_declares_audit(cr["graph"]):
            progress(phase="critique", label="④b 改进图删除了审计 · 强制重批…", round_k=k)
            audit_fb = (
                critique_constraints
                + "\n【审计不变量违规】你刚才删除了 overfit 审计节点。请在保留审计节点及"
                  " backtest.result→overfit.result 连线的前提下重新改进，否则本研究不能晋级。"
            )
            try:
                cr2 = _call_critique(goal, metrics, graph, constraints=audit_fb)
            except Exception as exc:  # noqa: BLE001
                cr2 = {"ok": False, "reason": f"{type(exc).__name__}: {exc}"}
            if (cr2.get("ok") and isinstance(cr2.get("graph"), dict)
                    and _graph_declares_audit(cr2["graph"])):
                cr, audit_retried = cr2, True
            else:
                error = (
                    "批判环违反审计不变量: 两次改进均删除了过拟合审计；"
                    "为防止用未审计指标绕门，研究诚实中断"
                )
                break
        # ── 停滞守卫(P4:图签名比较):改进图与本轮图签名相同 → 指标必不变(求值确定性),
        #    复算是烧轮次装研究。带停滞警告重批一次;仍相同 → 诚实中断。──
        retried = False
        if wex.graph_signature(cr["graph"]) == wex.graph_signature(graph):
            progress(phase="critique", label="④b 改进图与上一轮相同 · 停滞重批…", round_k=k)
            fb = (critique_constraints
                  + f"\n【停滞警告】你上一份改进(诊断:{str(cr.get('diagnosis'))[:100]})图与上一轮"
                    "完全相同(签名一致),指标必然与本轮完全相同。这次必须对图做出实质修改"
                    "(换表达式/换模型类型或超参/改合成方式或回测参数皆可)。")
            try:
                cr2 = _call_critique(goal, metrics, graph, constraints=fb)
            except Exception as exc:  # noqa: BLE001
                cr2 = {"ok": False, "reason": f"{type(exc).__name__}: {exc}"}
            if (cr2.get("ok") and isinstance(cr2.get("graph"), dict)
                    and wex.graph_signature(cr2["graph"]) != wex.graph_signature(graph)):
                cr, retried = cr2, True
            else:
                error = ("批判环停滞: 两次改进产出的图与上一轮完全相同(签名一致,节点参数亦未变),"
                         "指标不会变,诚实中断")
                break
        crit_source = str(cr.get("source") or "?")
        diag = (("(审计保留重批) " if audit_retried else "")
                + ("(停滞重批) " if retried else "")
                + ("(规则兜底·非 LLM) " if crit_source == "rule" else "")
                + str(cr.get("diagnosis") or ""))
        graph = cr["graph"]

    # ── 收工三件套:最佳图入工作流库 + 教训 + 终态行 ──
    best: Optional[Dict[str, Any]] = None
    for r in rounds:
        ric = (r.get("metrics") or {}).get("rank_ic")
        if r.get("failed") or not isinstance(ric, (int, float)) or ric != ric:
            continue
        if (r.get("gate") or {}).get("passed"):
            best = r
            break
        if best is None or float(ric) > float((best.get("metrics") or {}).get("rank_ic")):
            best = r
    ws = _save_graph(goal, run_id, best["graph"]) if best else None
    n = len(rounds)
    bm = (best or {}).get("metrics") or {}
    if promoted and promoted.get("status") in ("draft", "draft_structured", "draft_compose", "draft_model"):
        lesson = (f"研究「{goal[:40]}」{n}轮达标:{promoted['name']}(status={promoted['status']}) "
                  f"rank_ic={bm.get('rank_ic')} oos={bm.get('oos_verdict')} 已入draft待人审;"
                  f"诊断:{str(diag)[:80]}")
    elif promoted and promoted.get("status") == "workflow_only":
        lesson = (f"研究「{goal[:40]}」{n}轮达标但仅保存工作流:"
                  f"{str(promoted.get('reason'))[:110]};rank_ic={bm.get('rank_ic')} "
                  f"oos={bm.get('oos_verdict')}")
    elif promoted and promoted.get("status") == "save_failed":
        lesson = (f"研究「{goal[:40]}」{n}轮达标但入库失败:{str(promoted.get('reason'))[:80]};"
                  f"rank_ic={bm.get('rank_ic')} oos={bm.get('oos_verdict')}")
    elif error:
        lesson = f"研究「{goal[:40]}」{n}轮中断:{str(error)[:120]}"
    else:
        lesson = (f"研究「{goal[:40]}」{n}轮未达标(门 rank_ic≥{min_rank_ic} 且 oos=robust):"
                  f"最佳 rank_ic={bm.get('rank_ic')} oos={bm.get('oos_verdict')};诊断:{str(diag)[:80]}")
    mem_ok = _write_lesson(goal, lesson)
    end_row = {"run_id": run_id, "kind": "end", "ok": error is None, "error": error,
               "n_rounds": n, "best_k": (best or {}).get("k"),
               "best_metrics": (bm or None), "promoted": promoted,
               "workflow_saved": ws, "memory_written": mem_ok,
               "rounds_recorded": rounds_ok, "ts": _now()}
    rstore.append_run(end_row)
    return end_row
