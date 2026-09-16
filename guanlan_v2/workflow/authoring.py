"""Shared, bounded authoring contract for canvas and conversational agents."""
from __future__ import annotations

import copy
import hashlib
import json
from datetime import date

GRAPH_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "nodes": {"type": "array", "minItems": 1, "maxItems": 60, "items": {
            "type": "object", "properties": {
                "id": {"type": "string"}, "type": {"type": "string"},
                "x": {"type": "number"}, "y": {"type": "number"},
                "params": {"type": "object"}}, "required": ["id", "type", "params"]}},
        "edges": {"type": "array", "maxItems": 160, "items": {
            "type": "object", "properties": {
                "from": {"type": "array", "items": {"type": "string"}, "minItems": 2, "maxItems": 2},
                "to": {"type": "array", "items": {"type": "string"}, "minItems": 2, "maxItems": 2}},
            "required": ["from", "to"]}}}, "required": ["nodes", "edges"]}


def graph_digest(graph):
    # Match the store's migrated nodes/edges contract; metadata is not execution.
    from guanlan_v2.workflow.migrations import migrate_graph
    migrated, _ = migrate_graph(graph)
    persisted = {"nodes": migrated.get("nodes") or [], "edges": migrated.get("edges") or []}
    return hashlib.sha256(json.dumps(persisted, sort_keys=True, ensure_ascii=True,
                                    separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def validate_authoring(graph):
    """No market calls or expression execution; errors retain node-level context."""
    from guanlan_v2.workflow.api import validate_graph
    try:
        raw = json.dumps(graph, ensure_ascii=True, allow_nan=False)
    except (TypeError, ValueError) as exc:
        return {"ok": False, "errors": [f"图必须是有限 JSON: {exc}"]}
    if len(raw) > 200_000:
        return {"ok": False, "errors": ["图超过 200KB 编写上限，请拆分研究任务"]}
    ok, errors = validate_graph(graph, require_complete=True)
    errors = list(errors)
    if not ok:
        return {"ok": False, "errors": errors}
    if not isinstance(graph, dict):
        return {"ok": False, "errors": errors}
    nodes = graph.get("nodes") or []
    edges = graph.get("edges") or []
    if not isinstance(nodes, list) or not isinstance(edges, list):
        return {"ok": False, "errors": errors}
    if len(nodes) > 60 or len(edges) > 160:
        errors.append("图超过 60 节点/160 连线编写上限，请拆分研究任务")
    sources = {n.get("id"): n for n in nodes if isinstance(n, dict) and n.get("type") == "source"}
    used = {e["from"][0] for e in edges if isinstance(e, dict)
            and isinstance(e.get("from"), list) and len(e["from"]) == 2}
    for node in nodes:
        if not isinstance(node, dict):
            continue
        nid, kind = node.get("id"), node.get("type")
        params = node.get("params") or {}
        if not isinstance(params, dict):
            continue
        if kind in {"formula", "factorlib"} and nid not in used:
            errors.append(f"节点 {nid}: 因子没有连接到下游，不能靠图内扫描隐式参与计算")
        if kind == "source":
            if params.get("research_freq") not in (None, "", "day", "week", "month"):
                errors.append(f"节点 {nid}: research_freq 仅支持 day/week/month")
            dates = {}
            for field in ("start", "end"):
                if params.get(field):
                    try:
                        dates[field] = date.fromisoformat(str(params[field]))
                    except ValueError:
                        errors.append(f"节点 {nid}: {field} 必须为 YYYY-MM-DD")
            if len(dates) == 2 and dates["start"] > dates["end"]:
                errors.append(f"节点 {nid}: 起始日不能晚于截止日")
        # Require actual source lineage for newly authored research terminals.
        if kind in {"analysis", "iccalc", "backtest", "portfolio", "risk", "garch", "attrib", "tvbeta"}:
            queue, seen, found = [nid], set(), set()
            while queue:
                current = queue.pop()
                if current in seen:
                    continue
                seen.add(current)
                if current in sources:
                    found.add(current)
                current_node = next((n for n in nodes if isinstance(n, dict) and n.get("id") == current), {})
                ref = (current_node.get("params") or {}).get("context_source_id")
                if ref in sources:
                    found.add(ref)
                queue.extend(e["from"][0] for e in edges if isinstance(e, dict)
                             and isinstance(e.get("to"), list) and len(e["to"]) == 2 and e["to"][0] == current
                             and isinstance(e.get("from"), list) and len(e["from"]) == 2)
            if not found:
                errors.append(f"节点 {nid}: 缺少数据源上下文；连接 source.data→上游.src，或明确 params.context_source_id")
            elif len(found) > 1 and params.get("context_source_id") not in sources:
                errors.append(f"节点 {nid}: 多数据源有歧义，需明确 params.context_source_id")
    from guanlan_v2.workflow.expression_safety import validate_graph_expressions
    errors.extend(validate_graph_expressions(graph))
    errors = list(dict.fromkeys(errors))
    return {"ok": not errors, "errors": errors,
            "graph_digest": graph_digest(graph) if not errors else None,
            "validation_scope": "graph_structure_and_context_only_not_data_or_profitability",
            "warnings": ["结构通过不保证数据覆盖或策略有效；执行后按真实节点结果审查。"]}


def catalog():
    from guanlan_v2.workflow.api import _CATALOG, _TERMINAL_TYPES, _UNIVERSE_OK, SYSTEM_PROMPT
    nodes = copy.deepcopy(_CATALOG)
    nodes["source"]["params"] += ["start", "end", "freq", "research_freq", "oos_frac", "wf_refit"]
    nodes["factorlib"]["params"] += ["expr", "factor_spec", "factor_spec_digest"]
    for kind in _TERMINAL_TYPES:
        if kind in nodes:
            nodes[kind]["params"] += ["context_source_id"]
    return {"schema": "workflow-authoring-v1", "nodes": nodes, "graph_schema": GRAPH_SCHEMA,
            "expression_contract": SYSTEM_PROMPT.split("【因子表达式 DSL 白名单(formula.expr)】", 1)[1].split("【配方模板", 1)[0].strip(),
            "terminal_types": sorted(_TERMINAL_TYPES), "universes": sorted(_UNIVERSE_OK),
            "rules": [
                "所有因子必须显式连线；多个公式都连 feature.feat，不扫描孤立因子。",
                "单股票 source 使用 scope=个股(兼容single)，code 或 codes 必须填写且仅一个合法股票代码；不会回落到快速池。多票请使用自选范围和显式 codes。",
                "只允许受限数学DSL：不能访问属性/文件、下标或执行Python；窗口需有界字面整数。负delay仅预测标签可用，不能进入特征。opaque Python注册因子或预封存结构依赖需先展开可审计DSL。",
                "source.data 是上下文，只连 src；无 src 的终端可用 context_source_id 明确引用。多源不可猜第一源。",
                "模型必须 feature→模型→mf→analysis/backtest，传真实 OOS 信号；多模型仅预设等权。",
                "feature.tag 只留空/IC/fwd_ret；不要填显示标题。自定义标签用 formula 连 label 端口。",
                "source 可指定代码 codes、start/end、research_freq=day/week/month（研究采样/标签频率）、oos_frac 和 wf_refit=true；旧freq是日线行情字段，不用于研究调频。窗口单位取决于研究采样，请区分5交易日与5采样期。布局 x/y 左到右每级约280像素。",
                "单股连续因子用 tsic，不用截面 rank/analysis；布尔事件用 event。",
                "只看收益率均值/波动/正占比等描述统计：source.data→relstat.src，formula(expr=returns).out→relstat.factor；纯诊断图可执行并返回诊断产物，metrics=null、gate_eligible=false，不代表回测或晋级通过。",
                "data_screen.candidates 仅是当前截面终端；历史规则验证用 source.data→history_screen.src，再 history_screen.history→daily_backtest.history。新路线逐日重算、次日开盘日调仓；静态股票池/当前ST和陈旧估值限制必须披露。",
                "先读目录→自行构造 graph→validate→save→run→runs 查询最终结果。失败时保留原图，新版本修正后再验证。",
                "生成/校验/保存不是执行完成；queued/running 不是已完成。服务重启仅恢复图/结果，不能节点断点续算。",
                "本工具不下单、不发布因子、不切换默认模型；回测仅研究证据。"]}
