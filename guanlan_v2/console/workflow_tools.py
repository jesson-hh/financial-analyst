"""Workspace-bound workflow authoring and durable execution for Weiwo agents.

No raw shell, global promotion, default-model change, or caller-supplied owner.
All graph/run writes go through the existing reviewed HTTP execution contracts.
"""
from __future__ import annotations

import hashlib
import json
import re
import threading
import uuid
from urllib.parse import urlencode

from guanlan_v2.workflow.authoring import GRAPH_SCHEMA, catalog, graph_digest, validate_authoring
from guanlan_v2.console import workspace_tools as wt

_SAVE_LOCK = threading.Lock()


def _context():
    from guanlan_v2.console import tools as ct
    _, workspace_id, workspace = wt._bound()
    return ct, workspace_id, workspace, ct.CTX_SID.get(None)


def _json(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False)


def _failure(exc):
    return {"ok": False, "content": f"工作流操作未完成: {exc}", "reason": str(exc)}


def _query(workspace_id, **extra):
    return urlencode({"workspace_id": workspace_id, **extra})


def _id(value, kind="workflow"):
    pattern = r"w[0-9a-f]{12}" if kind == "workflow" else r"wr_[0-9a-f]{12,40}"
    if not re.fullmatch(pattern, str(value or "")):
        raise ValueError(f"{kind} id 格式不合法")
    return str(value)


def _card(ct, workspace_id, sid, *, graph, name="", workflow_id=None, run_id=None):
    return ct.artifact("workflow", page="factor", channel="guanlan.workflow.import", payload={
        "graph": graph, "name": name, "workflow_id": workflow_id, "run_id": run_id,
        "workspace_id": workspace_id, "session_id": sid}, ref=run_id or workflow_id)


def workflow_catalog_impl():
    data = catalog()
    try:
        _, wid, workspace, _ = _context()
        data["bound_workspace"] = {"id": wid, "subject": _subject(workspace),
                                   "agent_mode": workspace.get("agent_mode")}
    except wt.WorkspaceToolError:
        pass  # The non-private node contract is safe without a session.
    return {"ok": True, "content": _json(data)}


def _subject(workspace):
    return {k: workspace.get(k) for k in ("kind", "code", "name", "display_name") if workspace.get(k)}


def workflow_generate_impl(goal=""):
    try:
        ct, wid, workspace, sid = _context()
        goal = str(goal).strip()
        if not goal or len(goal) > 8000:
            raise ValueError("研究目标需为 1—8000 字符")
        prompt = goal + "\n当前研究空间参考（不替代用户指定的股票池）:" + _json(_subject(workspace))
        result = ct._self_post("/workflow/generate", {"goal": prompt, "max_retries": 2}, timeout=300)
        if not result.get("ok"):
            return _failure(result.get("reason") or "生成失败；没有自动改用模板")
        graph = result["graph"]
        checked = validate_authoring(graph)
        if not checked["ok"]:
            return _failure("；".join(checked["errors"]))
        return {"ok": True, "content": _json({"status": "generated_not_saved_not_run", "graph": graph,
                    "validation": checked, "source": "llm", "next": "审查目标/范围，save 后 run；未执行不得报告研究结果"}),
                "artifact": _card(ct, wid, sid, graph=graph, name=goal[:80])}
    except Exception as exc:
        return _failure(exc)


def workflow_validate_impl(graph=None):
    try:
        checked = validate_authoring(graph)
        return {**checked, "content": _json(checked)}
    except Exception as exc:
        return _failure(exc)


def workflow_save_impl(name="", graph=None):
    # Serialize the read/compare/save window for agent retries in this service.
    with _SAVE_LOCK:
        return _workflow_save_impl(name=name, graph=graph)


def _workflow_save_impl(name="", graph=None):
    try:
        ct, wid, _, sid = _context()
        checked = validate_authoring(graph)
        if not checked["ok"]:
            return _failure("；".join(checked["errors"]))
        # Immutable agent version names: never silently overwrite a user graph
        # with the same title. Repeating identical content is idempotent.
        title = str(name or "Agent 研究工作流").strip()[:80]
        suffix = checked["graph_digest"][:12]
        saved_name = f"{title} · Agent {suffix}"
        listing = ct._self_get("/workflow/list?" + _query(wid))
        if not listing.get("ok"):
            return _failure(listing.get("reason") or "无法核对既有版本")
        same = next((r for r in listing.get("items", []) if r.get("name") == saved_name), None)
        if same and graph_digest(same.get("graph")) != checked["graph_digest"]:
            raise ValueError("同名审计版本内容已变更，拒绝覆盖；请改用新的工作流名称")
        result = same or ct._self_post("/workflow/save", {"name": saved_name, "graph": graph, "workspace_id": wid})
        if not same and not result.get("ok"):
            return _failure(result.get("reason") or "保存失败")
        workflow_id = _id(result["id"])
        return {"ok": True, "workflow_id": workflow_id, "content": _json({
            "status": "saved_not_run", "workflow_id": workflow_id, "workspace_id": wid,
            "name": saved_name, "graph_digest": checked["graph_digest"], "reused": same is not None,
            "next": "调用 ww_workflow_run 后，用 ww_workflow_runs(run_id) 审查真实结果"}),
            "artifact": _card(ct, wid, sid, graph=graph, name=saved_name, workflow_id=workflow_id)}
    except Exception as exc:
        return _failure(exc)


def workflow_get_impl(workflow_id=None, limit=20):
    try:
        ct, wid, _, sid = _context()
        if workflow_id is None:
            result = ct._self_get("/workflow/list?" + _query(wid))
            if not result.get("ok"):
                return _failure(result.get("reason") or "列表失败")
            rows = result.get("items", [])[:max(1, min(50, int(limit)))]
            items = [{k: row.get(k) for k in ("id", "name", "ts", "workspace_id", "migration", "legacy_recovery")} for row in rows]
            return {"ok": True, "content": _json({"workspace_id": wid, "items": items})}
        workflow_id = _id(workflow_id)
        result = ct._self_get(f"/workflow/get/{workflow_id}?" + _query(wid))
        if not result.get("ok"):
            return _failure(result.get("reason") or "不存在或不属于当前空间")
        return {"ok": True, "content": _json(result), "artifact": _card(ct, wid, sid,
                graph=result["graph"], name=result.get("name", ""), workflow_id=workflow_id)}
    except Exception as exc:
        return _failure(exc)


def workflow_run_impl(workflow_id="", new_run=False):
    try:
        ct, wid, _, sid = _context()
        workflow_id = _id(workflow_id)
        saved = ct._self_get(f"/workflow/get/{workflow_id}?" + _query(wid))
        if not saved.get("ok"):
            return _failure(saved.get("reason") or "工作流不存在或不属于当前空间")
        graph = saved["graph"]
        checked = validate_authoring(graph)
        if not checked["ok"]:
            return _failure("；".join(checked["errors"]))
        seed = wid + workflow_id + checked["graph_digest"]
        if new_run is True:
            seed += uuid.uuid4().hex
        run_id = "wr_" + hashlib.sha256(seed.encode()).hexdigest()[:24]
        result = ct._self_post("/workflow/run/start", {"run_id": run_id,
            "name": saved.get("name", "Agent 工作流"), "graph": graph, "workspace_id": wid}, timeout=30)
        if not result.get("ok"):
            return _failure(result.get("reason") or "未启动")
        return {"ok": True, "run_id": run_id, "content": _json({
            "run_id": run_id, "status": result.get("status"), "workspace_id": wid,
            "recovery": result.get("recovery"), "next": "用 ww_workflow_runs(run_id) 查询；排队/运行不代表完成；未用 new_run 的重复调用只恢复同一任务"}),
            "artifact": _card(ct, wid, sid, graph=graph, name=saved.get("name", ""),
                                   workflow_id=workflow_id, run_id=run_id)}
    except Exception as exc:
        return _failure(exc)


def _result_summary(result):
    """Only measured fields; exclude huge arrays but retain failures and lineage."""
    if not isinstance(result, dict):
        return None
    out = {k: result.get(k) for k in ("ok", "reason", "result_kind", "gate_eligible",
                                     "node_results", "node_errors", "warnings")}
    terminals = []
    items = result.get("all_terminals") or ([result["terminal"]] if isinstance(result.get("terminal"), dict) else [])
    for item in items:
        payload = item.get("payload") or {}
        terminal = {"kind": item.get("kind"), "node_id": item.get("node_id"),
            **{k: payload[k] for k in ("ok", "reason", "warnings", "headline_ic", "n_dates", "n", "oos",
                                       "factor", "universe", "start", "end", "status") if k in payload}}
        if isinstance(payload.get("backtest"), dict):
            terminal["backtest"] = {k: payload["backtest"].get(k) for k in ("portfolio_kpi", "net_ann", "gross_ann")}
        if isinstance(payload.get("portfolio"), dict):
            terminal["execution_assumptions"] = payload["portfolio"].get("execution_assumptions")
        if isinstance(payload.get("factor_artifact"), dict):
            art = payload["factor_artifact"]
            terminal["factor_artifact"] = {"id": art.get("id"), "kind": art.get("kind"), "source": art.get("source")}
        if item.get("kind") == "data_screen":
            terminal.update({k: payload.get(k) for k in ("run_id", "as_of", "requested_as_of", "n_matched", "n_loaded",
                                "spec_digest", "source_selector", "compiled_spec")})
            candidates = payload.get("candidates") or []
            terminal["candidates"] = candidates[:30]
            terminal["candidate_rows_total"] = len(candidates)
            terminal["candidate_rows_truncated"] = len(candidates) > 30
        if item.get("kind") == "relstat":
            terminal.update({k: payload.get(k) for k in (
                "expr_or_name", "summary", "sample_start", "sample_end", "requested_codes",
                "selection_mode", "configured_universe")})
            rows = payload.get("codes_relstat") or []
            terminal["codes_relstat"] = rows[:30]
            terminal["diagnostic_rows_total"] = len(rows)
            terminal["diagnostic_rows_truncated"] = len(rows) > 30
            terminal["interpretation"] = "时序描述统计，不是预测收益能力、回测成绩或晋级通过。"
        terminals.append(terminal)
    out["terminals"] = terminals
    return out


def workflow_runs_impl(run_id=None, limit=20):
    try:
        ct, wid, _, sid = _context()
        if run_id is None:
            result = ct._self_get("/workflow/run/list?" + _query(wid, limit=max(1, min(50, int(limit)))))
            return {"ok": bool(result.get("ok")), "content": _json(result)}
        run_id = _id(run_id, "run")
        result = ct._self_get(f"/workflow/run/status/{run_id}?" + _query(wid))
        if not result.get("ok"):
            return _failure(result.get("reason") or "任务不存在或不属于当前空间")
        summary = {k: result.get(k) for k in ("id", "name", "status", "reason", "node_state", "recovery", "started_at", "ended_at")}
        summary["result"] = _result_summary(result.get("result"))
        summary["interpretation"] = "这里只报告真实执行状态；失败或无数据不是筛选/回测成功，研究结果不是实盘放行"
        return {"ok": True, "content": _json(summary), "artifact": _card(ct, wid, sid,
                graph=result.get("graph") or {}, name=result.get("name", ""), run_id=run_id)}
    except Exception as exc:
        return _failure(exc)


def _tool(name, description, impl, properties=None, required=None, confirm=False, reachable=None):
    return {"name": name, "description": description,
            "input_schema": {"type": "object", "additionalProperties": False,
                             "properties": properties or {}, "required": required or []},
            "impl": impl, "cost": "seconds" if confirm else "instant", "confirm": confirm,
            "reachable": reachable or []}


TOOL_TABLE = [
    _tool("ww_workflow_catalog", "自主搭建流程图前必读：真实节点/端口/参数/完整graph schema、作用域及连线规则。根据用户目标自行构图，不用虚构节点。", workflow_catalog_impl),
    _tool("ww_workflow_generate", "将用户目标交给平台AI生成完整流程图并回传预览。花一次生成请求；仅生成不保存、不运行。失败明确返回，不拿模板冒充AI。也可自己构造graph调用validate。", workflow_generate_impl,
          {"goal": {"type": "string"}}, ["goal"], reachable=["/workflow/generate"]),
    _tool("ww_workflow_validate", "无行情计算的完整图与上下文校验，返回节点级错误供你修正。通过不代表数据齐全/策略有效。", workflow_validate_impl,
          {"graph": GRAPH_SCHEMA}, ["graph"], reachable=["/workflow/validate"]),
    _tool("ww_workflow_save", "把已校验graph保存成当前空间私有版本，并返回可打开画布的卡片；保留同名旧图，不发布全局，不运行。需要确认。", workflow_save_impl,
          {"name": {"type": "string"}, "graph": GRAPH_SCHEMA}, ["name", "graph"], True, ["/workflow/list", "/workflow/save"]),
    _tool("ww_workflow_get", "读取当前空间保存的工作流；省略workflow_id列目录。保留原始图、历史错误和恢复信息，不跨空间。", workflow_get_impl,
          {"workflow_id": {"type": "string"}, "limit": {"type": "integer"}}, reachable=["/workflow/list", "/workflow/get/{wid}"]),
    _tool("ww_workflow_run", "后台执行已保存的当前空间流程图，立刻返回run_id，跨页不停止。需要确认计算成本；默认重复调用不重复执行。用户明确要求重新计算才设置new_run=true。必须随后用runs查最终结果，不把queued/running说成成功。", workflow_run_impl,
          {"workflow_id": {"type": "string"}, "new_run": {"type": "boolean", "default": False}}, ["workflow_id"], True,
          ["/workflow/get/{wid}", "/workflow/run/start"]),
    _tool("ww_workflow_runs", "恢复发现当前空间历史任务；指定run_id查询进度、失败节点、真实指标与可打开的结果卡。只读，不重复执行；进程重启仅恢复图和结果，不冒充节点断点续算。", workflow_runs_impl,
          {"run_id": {"type": "string"}, "limit": {"type": "integer"}}, reachable=["/workflow/run/list", "/workflow/run/status/{run_id}"]),
]
