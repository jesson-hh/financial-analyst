# -*- coding: utf-8 -*-
"""guanlan MCP server(low-level mcp.server.Server)。镜像引擎 mcp_server,工具复用 ww_/引擎 impl。"""
from __future__ import annotations

import asyncio
from contextlib import nullcontext
from copy import deepcopy
from datetime import date
import json
import math
import os
import re
import threading
from typing import Any, Dict, List, Optional

from guanlan_v2.glmcp.company_research import (
    CODEX_COMPANY_RESEARCH_TOOL,
    codex_company_research_declaration,
    validate_codex_company_research_arguments,
    validate_company_research_launch_result,
)

_DECLS: List[Dict[str, Any]] | None = None
_DEFAULT_STORE: Any = None
_DEFAULT_STORE_LOCK = threading.Lock()
_CODEX_HIDDEN_INPUT_FIELDS = {
    "factor_report": frozenset({"archive", "note"}),
    # Query-only screening is pinned to prod and can never create a formal
    # snapshot.  The model/path/archive selectors are absent from the scoped
    # contract rather than merely overwritten after approval.
    "ww_screen_run": frozenset({"model", "snapshot", "note"}),
    # A scoped company calendar is always bound to the workspace subject.  The
    # model cannot even advertise a second stock selector.
    "ww_workspace_trade_disclosure_calendar": frozenset({"stock_code"}),
}
_CODEX_CLOSED_INPUT_SCHEMAS = frozenset({
    "ww_workflow_catalog", "ww_workflow_generate", "ww_workflow_validate",
    "ww_workflow_save", "ww_workflow_get", "ww_workflow_run", "ww_workflow_runs",
    "ww_workspace_quant_chart",
    "alpha_bench",
    "factor_report",
    "ww_screen_run",
    "ww_workspace_trade_disclosure_calendar",
    "ww_workspace_newsradar",
    "ww_workspace_industry_review_list",
    "ww_workspace_industry_review",
    "ww_workspace_company_research_prepare",
    CODEX_COMPANY_RESEARCH_TOOL,
})
_CODEX_SCREEN_POOLS = frozenset({
    "all", "csi300", "csi500", "csi800", "csi1000",
})
_CODEX_SCREEN_MAINLINE = frozenset({
    "mainline", "initiation", "revival", "decay", "cold", "neutral",
})
_SAFE_FACTOR_ID_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")


def _decls() -> List[Dict[str, Any]]:
    global _DECLS
    if _DECLS is None:
        from guanlan_v2.glmcp.tooltable import build_mcp_tools
        _DECLS = build_mcp_tools()
    return _DECLS


def _by_name() -> Dict[str, Dict[str, Any]]:
    return {d["name"]: d for d in _decls()}


def _codex_decls() -> List[Dict[str, Any]]:
    """Public tools plus one declaration exclusive to scoped Codex transport."""
    return [*_decls(), codex_company_research_declaration()]


def _codex_by_name() -> Dict[str, Dict[str, Any]]:
    return {d["name"]: d for d in _codex_decls()}


def _codex_scope():
    from guanlan_v2.glmcp.scope import CTX_CODEX_MCP_SCOPE
    return CTX_CODEX_MCP_SCOPE.get()


def _scoped_schema(d: Dict[str, Any], *, scoped: bool) -> Dict[str, Any]:
    """Hide the trusted workspace selector from a scoped Codex client."""
    schema = deepcopy(d["inputSchema"])
    if not scoped:
        return schema
    if d["name"] in _workspace_tool_names():
        properties = schema.get("properties")
        if isinstance(properties, dict):
            properties.pop("workspace_id", None)
        required = schema.get("required")
        if isinstance(required, list):
            schema["required"] = [
                name for name in required if name != "workspace_id"
            ]
    hidden = _CODEX_HIDDEN_INPUT_FIELDS.get(d["name"], frozenset())
    properties = schema.get("properties")
    if isinstance(properties, dict):
        for field in hidden:
            properties.pop(field, None)
    required = schema.get("required")
    if isinstance(required, list) and hidden:
        schema["required"] = [name for name in required if name not in hidden]

    # Schema constraints help the model form valid calls.  The dispatch
    # boundary independently enforces the same values below; JSON Schema is
    # never treated as the security boundary.
    if isinstance(properties, dict) and d["name"] in {"alpha_bench", "factor_report"}:
        from guanlan_v2.console.codex_query_tools import (
            CODEX_ALPHA_BENCH_UNIVERSES, CODEX_FACTOR_UNIVERSES,
        )
        allowed = (CODEX_ALPHA_BENCH_UNIVERSES if d["name"] == "alpha_bench"
                   else CODEX_FACTOR_UNIVERSES)
        if isinstance(properties.get("universe"), dict):
            properties["universe"]["enum"] = sorted(allowed)
            if d["name"] == "alpha_bench":
                properties["universe"]["default"] = "sample30"
        for field in ("since", "until", "start", "end"):
            if isinstance(properties.get(field), dict):
                properties[field]["pattern"] = r"^\d{4}-\d{2}-\d{2}$"
        if d["name"] == "alpha_bench" and isinstance(properties.get("top"), dict):
            properties["top"].update({"minimum": 1, "maximum": 25})
        if d["name"] == "factor_report" and isinstance(properties.get("expr_or_name"), dict):
            from guanlan_v2.console.codex_query_tools import codex_trusted_alpha_names
            properties["expr_or_name"].update({
                "maxLength": 512,
                "enum": list(codex_trusted_alpha_names()),
            })
    if isinstance(properties, dict) and d["name"] == "ww_screen_run":
        if isinstance(properties.get("topN"), dict):
            properties["topN"].update({"minimum": 1, "maximum": 50})
        if isinstance(properties.get("blend"), dict):
            properties["blend"].update({"minimum": 0, "maximum": 1})
        if isinstance(properties.get("liqMin"), dict):
            properties["liqMin"].update({"minimum": 0, "maximum": 10000})
        if isinstance(properties.get("indCap"), dict):
            properties["indCap"].update({"minimum": 0.05, "maximum": 1})
        if isinstance(properties.get("factors"), dict):
            properties["factors"]["maxItems"] = 12
    if d["name"] in _CODEX_CLOSED_INPUT_SCHEMAS:
        schema["additionalProperties"] = False
    return schema


def _unexpected_scoped_arguments(
    d: Dict[str, Any], arguments: Dict[str, Any],
) -> List[str]:
    """Return keys forbidden by a closed Codex input schema.

    MCP clients are expected to enforce the advertised JSON Schema, but direct
    dispatch is also a security boundary.  Mirror the schema's
    ``additionalProperties: false`` rule here before resolving or executing an
    implementation.
    """
    if d["name"] not in _CODEX_CLOSED_INPUT_SCHEMAS:
        return []
    schema = _scoped_schema(d, scoped=True)
    if schema.get("additionalProperties") is not False:
        return []
    properties = schema.get("properties")
    allowed = set(properties) if isinstance(properties, dict) else set()
    # ``workspace_id`` is server-owned and absent from the advertised schema,
    # but keep the explicit cross-space diagnostic in dispatch for a client
    # that nevertheless sends it.  Every other hidden/unknown key fails here.
    tolerated = {"workspace_id"} if d["name"] in _workspace_tool_names() else set()
    return sorted(
        str(key) for key in arguments if key not in allowed and key not in tolerated
    )


def _number(value: Any, *, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} 必须是数字")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{name} 必须是有限数字")
    return number


def _integer(value: Any, *, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} 必须是整数")
    return int(value)


def _iso_day(value: Any, *, name: str) -> date:
    if not isinstance(value, str) or len(value) != 10:
        raise ValueError(f"{name} 必须是 YYYY-MM-DD")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{name} 必须是有效 YYYY-MM-DD") from exc
    if parsed > date.today():
        raise ValueError(f"{name} 不能晚于今天")
    return parsed


def _bounded_date_range(
    arguments: Dict[str, Any], *, start_name: str, end_name: str,
    max_days: int = 735, default_start: Optional[str] = None,
    default_end: Optional[str] = None,
) -> None:
    start_raw = arguments.get(start_name, default_start)
    end_raw = arguments.get(end_name, default_end)
    start = (_iso_day(start_raw, name=start_name)
             if start_raw is not None else None)
    end = (_iso_day(end_raw, name=end_name)
           if end_raw is not None else None)
    if start is not None and end is not None:
        span = (end - start).days
        if span < 0:
            raise ValueError(f"{start_name} 不能晚于 {end_name}")
        if span > max_days:
            raise ValueError(f"查询窗口不能超过 {max_days} 天")


def _validate_scoped_query_arguments(
    name: str, arguments: Dict[str, Any],
) -> None:
    """Enforce scoped query values before resolving any implementation.

    list_tools JSON Schema is advisory to an MCP client.  Direct calls must
    independently reject path-like universes, resource-amplifying ranges and
    unsafe legacy selectors at this server boundary.
    """
    if name == CODEX_COMPANY_RESEARCH_TOOL:
        validate_codex_company_research_arguments(arguments)
        return
    if name in {"alpha_bench", "factor_report"}:
        from guanlan_v2.console.codex_query_tools import (
            CODEX_ALPHA_BENCH_UNIVERSES, CODEX_FACTOR_UNIVERSES,
        )
        allowed = (CODEX_ALPHA_BENCH_UNIVERSES if name == "alpha_bench"
                   else CODEX_FACTOR_UNIVERSES)
        universe = arguments.get(
            "universe", "sample30" if name == "alpha_bench" else "csi500",
        )
        if not isinstance(universe, str) or universe not in allowed:
            raise ValueError(
                f"{name}.universe 只允许内置命名池: {', '.join(sorted(allowed))}"
            )
        if name == "alpha_bench":
            _bounded_date_range(
                arguments, start_name="since", end_name="until",
                default_start="2024-06-01", default_end="2024-12-31",
                max_days=365,
            )
            if "top" in arguments:
                top = _integer(arguments["top"], name="top")
                if not 1 <= top <= 25:
                    raise ValueError("top 必须在 1..25")
        else:
            expr = arguments.get("expr_or_name")
            if not isinstance(expr, str) or not expr.strip() or len(expr) > 512:
                raise ValueError("expr_or_name 必须是 1..512 字符")
            if any(ord(ch) < 32 for ch in expr):
                raise ValueError("expr_or_name 不允许控制字符")
            from guanlan_v2.console.codex_query_tools import codex_trusted_alpha_names
            if expr not in codex_trusted_alpha_names():
                raise ValueError("factor_report 只允许内置已注册 Alpha 名称")
            freq = arguments.get("freq", "month")
            if freq not in {"day", "week", "month"}:
                raise ValueError("freq 只允许 day/week/month")
            # When start is omitted the strict adapter derives exactly two
            # years from end, so only a caller-supplied start needs a span
            # check against the effective end.
            effective_end = arguments.get("end", date.today().isoformat())
            _bounded_date_range(
                arguments, start_name="start", end_name="end",
                default_end=effective_end,
            )

    if name == "ww_screen_run":
        pool = arguments.get("pool", "all")
        if pool not in _CODEX_SCREEN_POOLS:
            raise ValueError("pool 不是允许的内置股票池")
        if "topN" in arguments:
            top_n = _integer(arguments["topN"], name="topN")
            if not 1 <= top_n <= 50:
                raise ValueError("topN 必须在 1..50")
        for field, low, high in (
            ("blend", 0.0, 1.0), ("liqMin", 0.0, 10000.0),
            ("indCap", 0.05, 1.0),
        ):
            if field in arguments:
                value = _number(arguments[field], name=field)
                if not low <= value <= high:
                    raise ValueError(f"{field} 必须在 {low}..{high}")
        for field in (
            "industryNeutral", "exclST", "exclHalt", "exclLimit", "exclNew",
        ):
            if field in arguments and type(arguments[field]) is not bool:
                raise ValueError(f"{field} 必须是布尔值")
        statuses = arguments.get("mlStatus", [])
        if not isinstance(statuses, list) or len(statuses) > 6 or any(
            not isinstance(item, str) or item not in _CODEX_SCREEN_MAINLINE
            for item in statuses
        ):
            raise ValueError("mlStatus 包含非法状态")
        factors = arguments.get("factors", [])
        if not isinstance(factors, list) or len(factors) > 12:
            raise ValueError("factors 必须是最多 12 项的数组")
        for index, factor in enumerate(factors):
            if not isinstance(factor, dict) or set(factor) - {"id", "w"}:
                raise ValueError(f"factors[{index}] 只能包含 id/w")
            factor_id = factor.get("id")
            if not isinstance(factor_id, str) or not _SAFE_FACTOR_ID_RE.fullmatch(factor_id):
                raise ValueError(f"factors[{index}].id 非法")
            if "w" in factor and not -100.0 <= _number(
                factor["w"], name=f"factors[{index}].w",
            ) <= 100.0:
                raise ValueError(f"factors[{index}].w 必须在 -100..100")


def _apply_scoped_query_overrides(
    name: str, arguments: Dict[str, Any], *, scope: Any = None,
) -> None:
    """Inject private query-only switches absent from every public schema."""
    if name == "ww_news_search":
        arguments["_write_through"] = False
    elif name == "ww_screen_run":
        arguments["_write_through"] = False
    elif name == "alpha_bench":
        arguments["save"] = False
    elif name == "factor_report":
        arguments["archive"] = False
        arguments["note"] = ""
    elif name == "ww_workspace_trade_disclosure_calendar":
        workspace_code = str(getattr(scope, "workspace_code", "") or "")
        if workspace_code:
            arguments["stock_code"] = workspace_code
    elif name == "ww_workspace_newsradar":
        arguments["_allow_refresh"] = False
    elif name == "ww_workspace_company_research_prepare":
        # A Codex automatic call may create immutable assets inside its current
        # workspace, but it may neither mutate the global event store nor start
        # a shared NewsRadar cache writer.
        arguments["_global_event_write"] = False
        arguments["_newsradar_allow_refresh"] = False


def _scoped_query_impl(name: str):
    if name == "alpha_bench":
        from guanlan_v2.console.codex_query_tools import (
            codex_alpha_bench_query_impl,
        )
        return codex_alpha_bench_query_impl
    if name == "factor_report":
        from guanlan_v2.console.codex_query_tools import (
            codex_factor_report_query_impl,
        )
        return codex_factor_report_query_impl
    return None


def _workspace_tool_names():
    from guanlan_v2.console.workspace_tools import ALL_WORKSPACE_TOOLS
    return ALL_WORKSPACE_TOOLS


def _is_write_enabled() -> bool:
    return os.environ.get("GUANLAN_MCP_WRITE") == "1"


def _to_text(result: Any) -> str:
    """impl/引擎结果 → 文本。ww_ impl 回 dict{content};引擎 ToolResult 有 .content。"""
    if isinstance(result, dict):
        return str(result.get("content") or json.dumps(result, ensure_ascii=False, default=str))
    content = getattr(result, "content", None)
    return str(content if content is not None else result)


def _success_reply(result: Any, *, name: str, scope: Any = None):
    """Return text plus the closed Weiwo artifact envelope for scoped Codex.

    Public ``/gl-mcp`` keeps its historical list-of-content response.  Only the
    authenticated, workspace-bound Codex endpoint emits structured content,
    and even there the full implementation result is never serialized.
    """
    from mcp.types import CallToolResult, TextContent

    text = _to_text(result)
    if scope is None:
        return [TextContent(type="text", text=text)]
    from guanlan_v2.console.artifact_bridge import build_artifact_envelope

    envelope = build_artifact_envelope(
        result, tool_name=name, workspace_id=scope.workspace_id,
    )
    if envelope is None:
        if isinstance(result, dict) and result.get("artifact") is not None:
            text += "\n[产物回传已拦截：返回结构未通过帷幄安全协议]"
        return [TextContent(type="text", text=text)]
    return CallToolResult(
        content=[TextContent(type="text", text=text)],
        structuredContent=envelope,
    )


def _error_reply(message: str, *, protocol_error: bool):
    from mcp.types import CallToolResult, TextContent
    content = [TextContent(type="text", text=str(message))]
    if protocol_error:
        return CallToolResult(content=content, isError=True)
    return content


def _resolve_impl(d: Dict[str, Any], name: str):
    if d["engine"]:
        from financial_analyst.buddy import tools as bt
        return {t.name: t for t in bt.TOOL_REGISTRY}[name].run
    import guanlan_v2.console.tools as ct
    return {t["name"]: t["impl"] for t in ct.WW_TOOL_TABLE}[name]


def _console_store(store: Any = None):
    """Use the production process's shared store; stdio gets one lazy singleton."""
    if store is not None:
        return store
    global _DEFAULT_STORE
    with _DEFAULT_STORE_LOCK:
        if _DEFAULT_STORE is None:
            from guanlan_v2.console.store import ConsoleStore
            _DEFAULT_STORE = ConsoleStore()
    return _DEFAULT_STORE


def _workspace_list(store: Any, arguments: Dict[str, Any]) -> Dict[str, Any]:
    include_archived = bool((arguments or {}).get("include_archived", False))
    fields = ("id", "name", "display_name", "kind", "code", "agent_mode",
              "archived", "updated")
    items = []
    for raw in store.workspace_store.list(include_archived=include_archived):
        items.append({key: raw.get(key) for key in fields})
    return {"ok": True, "workspaces": items}


def _run_workspace_impl(impl: Any, name: str, arguments: Dict[str, Any],
                        store: Any, sid: str = "mcp", *,
                        codex_scope: Any = None) -> Any:
    """Bind one explicit MCP workspace inside the worker thread and reset it."""
    import guanlan_v2.console.tools as ct
    from guanlan_v2.console import workspace_tools as wt
    from guanlan_v2.glmcp.scope import (
        codex_scope_is_active,
        workspace_root_sha256,
        workspace_subject_sha256,
    )

    args = dict(arguments or {})
    workspace_id = str(args.pop("workspace_id", "")).strip()
    # Project/file tools hold the metadata lock across the operation. Scoped
    # workflow tools use only workspace-owned research graphs, not project
    # paths; their self-HTTP save/start handlers recheck the active workspace.
    # Never hold this RLock across that HTTP hop: the handler runs in another
    # thread, so require() would deadlock until timeout, then execute too late.
    binding_lock = store.workspace_store._lock if (
        codex_scope is not None and name not in wt.WORKFLOW_CONTEXT_TOOLS
    ) \
        else nullcontext()
    with binding_lock:
        workspace = store.workspace_store.require(workspace_id, active_only=True)
        if codex_scope is not None:
            if not codex_scope_is_active(codex_scope):
                raise wt.WorkspaceToolError("Codex MCP scope 已撤销，本次调用未执行")
            root = wt._root_for(store, workspace_id)  # type: ignore[attr-defined]
            if (
                str(workspace.get("code") or "") != codex_scope.workspace_code
                or workspace_subject_sha256(workspace)
                != codex_scope.workspace_subject_sha256
                or (
                    codex_scope.workspace_root_sha256
                    and workspace_root_sha256(root)
                    != codex_scope.workspace_root_sha256
                )
            ):
                raise wt.WorkspaceToolError(
                    "当前研究空间标的或目录已变更；本次调用未执行"
                )
        if name not in wt.allowed_tools_for_workspace({name}, workspace):
            raise wt.WorkspaceToolError(
                f"研究空间 {workspace_id} 当前为 research 模式，不允许执行 {name}"
            )
        ctx = {"store": store, "sid": str(sid), "workspace_id": workspace_id,
               "workspace": workspace}
        workspace_token = wt.CTX_WORKSPACE.set(ctx)
        store_token = ct.CTX_STORE.set(store)
        sid_token = ct.CTX_SID.set(str(sid))
        try:
            if codex_scope is not None and not codex_scope_is_active(codex_scope):
                raise wt.WorkspaceToolError("Codex MCP scope 已撤销，本次调用未执行")
            return impl(**args)
        finally:
            ct.CTX_SID.reset(sid_token)
            ct.CTX_STORE.reset(store_token)
            wt.CTX_WORKSPACE.reset(workspace_token)


def _assert_scoped_workspace_binding(scope: Any, name: str, store: Any) -> None:
    """Revalidate the server-owned workspace identity before/after approval."""
    from guanlan_v2.console import workspace_tools as wt
    from guanlan_v2.glmcp.scope import (
        codex_scope_is_active,
        workspace_root_sha256,
        workspace_subject_sha256,
    )

    if not codex_scope_is_active(scope):
        raise wt.WorkspaceToolError("Codex MCP scope 缺失或已过期")
    with store.workspace_store._lock:
        current = store.workspace_store.require(
            scope.workspace_id, active_only=True,
        )
        root = wt._root_for(store, scope.workspace_id)  # type: ignore[attr-defined]
        if (
            str(current.get("code") or "") != scope.workspace_code
            or workspace_subject_sha256(current) != scope.workspace_subject_sha256
            or (
                scope.workspace_root_sha256
                and workspace_root_sha256(root) != scope.workspace_root_sha256
            )
        ):
            raise wt.WorkspaceToolError(
                "当前研究空间标的或目录已变更；请新建帷幄对话"
            )
        if name not in wt.allowed_tools_for_workspace({name}, current):
            raise wt.WorkspaceToolError(
                f"研究空间 {scope.workspace_id} 当前模式不允许执行 {name}"
            )


def _frozen_arguments(arguments: Dict[str, Any]) -> Dict[str, Any]:
    """Create a JSON-owned execution copy detached from request/callback data."""
    encoded = json.dumps(
        arguments or {}, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False,
    )
    decoded = json.loads(encoded)
    if not isinstance(decoded, dict):
        raise ValueError("MCP arguments 必须是对象")
    return decoded


def _report_spawn_env() -> Dict[str, str]:
    """研报类后台子进程 env(与 console `_call_buddy_report` 逐项对齐):
    PYTHONIOENCODING 防 GBK UnicodeEncodeError + PYTHONPATH 挂仓内 engine/(崩溃根修——
    缺此键子进程吃 venv 里 pinned 旧引擎,缺 news-sentiment 注册即起跑即崩)+ FA_CONFIG_DIR
    钉死仓内 config/(find_config 显式注入赢过 pinned workspace,不靠父进程环境继承)。"""
    from pathlib import Path
    repo = Path(__file__).resolve().parents[2]
    return {**os.environ, "PYTHONIOENCODING": "utf-8",
            "PYTHONPATH": str(repo / "engine") + os.pathsep + os.environ.get("PYTHONPATH", ""),
            "FA_CONFIG_DIR": os.environ.get("FA_CONFIG_DIR", str(repo / "config"))}


def _build_pack_safe(code: str) -> Optional[Dict[str, Any]]:
    """研报起跑前构证据包(best-effort,与 console.api._build_pack_safe 逐项对齐)。整体
    try/except——任何异常(含 import 失败)绝不挡报,只 print 诊断日志返 None。glmcp 与
    9999 同进程,import guanlan_v2.reports.evidence 可行(不像子进程有包边界)。"""
    try:
        from guanlan_v2.reports.evidence import build_evidence_pack
        return build_evidence_pack(code)
    except Exception as e:  # noqa: BLE001 — 构包失败绝不挡报,诚实打印后放行
        print(f"[report] evidence pack build failed for {code}: {type(e).__name__}: {e}")
        return None


def _spawn_background_detached(bg: dict) -> str:
    """background 信封 → detached 子进程真跑(不随 MCP 客户端退出而死)→ 诚实受理凭证。
    console 事件循环外的 MCP 通道没有 _spawn_bg 跑道 —— 此处补齐真执行,修假成功红线。
    kind=report → console 同款 CLI `financial-analyst report`;etf_report → 引擎 run_etf_report。
    本函数内部同步跑 _build_pack_safe(证据包构建,含秒级网络调用)+ Popen——dispatch_tool
    经 asyncio.to_thread 派本函数到工作线程,绝不同步跑在 9999 事件循环线程上(堵 loop→
    健康检查失败→看门狗误杀 9999 的历史事故模式)。"""
    import shutil
    import subprocess
    import sys as _sys
    import uuid
    from pathlib import Path
    repo = Path(__file__).resolve().parents[2]
    kind = str((bg or {}).get("kind") or "")
    code = str((bg or {}).get("code") or "")
    asof = (bg or {}).get("asof")
    pack_path: Optional[str] = None
    if kind == "report":
        configured_cli = os.environ.get("GUANLAN_FINANCIAL_ANALYST_CLI", "").strip()
        if configured_cli:
            exe = shutil.which(configured_cli) or configured_cli
            if not Path(exe).is_file():
                raise FileNotFoundError(
                    "GUANLAN_FINANCIAL_ANALYST_CLI does not name an executable file: "
                    f"{configured_cli}"
                )
            cli = [str(Path(exe).resolve())]
        elif exe := shutil.which("financial-analyst"):
            cli = [exe]
        else:
            # The vendored engine is on PYTHONPATH in _report_spawn_env().
            # Using this process' interpreter keeps the child in the active
            # virtual environment and avoids an author-machine fallback.
            cli = [_sys.executable, "-m", "financial_analyst.cli"]
        cmd = cli + ["report", code] + (["--asof", str(asof)] if asof else [])
        pack = _build_pack_safe(code)
        if pack and pack.get("path"):
            pack_path = pack["path"]
    elif kind == "etf_report":
        py = ("import sys; sys.path.insert(0, r'{eng}');"
              "import financial_analyst.buddy.tools as bt;"
              "t = bt.get_tool('run_etf_report');"
              "r = t.run(code={code!r}, asof={asof!r});"
              "print('[etf_report] is_error=', getattr(r, 'is_error', None), flush=True);"
              "print(str(getattr(r, 'content', ''))[:2000], flush=True);"
              "sys.exit(0 if not getattr(r, 'is_error', False) else 1)").format(
                  eng=str(repo / "engine"), code=code, asof=asof)
        cmd = [_sys.executable, "-c", py]
    else:
        return f"该后台任务类型 MCP 通道暂不支持:{kind or '(空)'}(请经帷幄 console 执行)"
    job = "mcpbg_" + uuid.uuid4().hex[:8]
    log = repo / "var" / f"mcp_bg_{job}.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    flags = 0x00000008 | 0x00000200   # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    env = _report_spawn_env()   # PYTHONIOENCODING + PYTHONPATH=engine/ + FA_CONFIG_DIR(与 console 逐项对齐)
    if pack_path:
        env["FA_EVIDENCE_PACK"] = pack_path
    with open(log, "ab") as lf:
        subprocess.Popen(cmd, cwd=str(repo), stdout=lf, stderr=subprocess.STDOUT,
                         creationflags=flags, env=env)
    return (f"已真启动后台研报(job {job} · {code} · 预计 5-8 分钟 · "
            f"产物落 reports store · 日志 {log})")


async def dispatch_tool(name: str, arguments: Dict[str, Any], *,
                        console_store: Any = None,
                        scope_required: bool = False) -> Any:
    """派发一次 MCP 工具调用 → list[TextContent]。写门 + to_thread + 诚实失败。
    background 信封(研报类长任务)→ detached 子进程真执行 + 受理凭证(修假成功红线)。
    _spawn_background_detached 内部同步跑证据包构建(秒级网络调用)+ Popen,必须经
    asyncio.to_thread 派到工作线程——直调会把网络调用堵在 9999 事件循环线程上,
    触发看门狗误杀(仓级红线,见 Task 4 评审)。"""
    from mcp.types import TextContent
    d = (_codex_by_name() if scope_required else _by_name()).get(name)
    if d is None:
        return _error_reply(
            json.dumps({"error": f"未知工具: {name}"}, ensure_ascii=False),
            protocol_error=scope_required,
        )
    scope = _codex_scope() if scope_required else None
    if scope_required and scope is None:
        return _error_reply(
            json.dumps({"error": "Codex MCP scope 缺失或已过期"}, ensure_ascii=False),
            protocol_error=True,
        )
    if scope is not None:
        from guanlan_v2.glmcp.scope import codex_scope_is_active
        if not codex_scope_is_active(scope):
            return _error_reply(
                json.dumps({"error": "Codex MCP scope 缺失或已过期"}, ensure_ascii=False),
                protocol_error=True,
            )
        requires_confirmation = name in scope.confirmation_required_tools
        if (
            name not in scope.allowed_tools
            or (d.get("destructive") and not requires_confirmation)
            or (
                requires_confirmation
                and name not in _workspace_tool_names()
                and name != CODEX_COMPANY_RESEARCH_TOOL
            )
        ):
            return _error_reply(
                json.dumps({"error": f"Codex 受限通道未授权工具: {name}"}, ensure_ascii=False),
                protocol_error=True,
            )
        try:
            arguments = _frozen_arguments(dict(arguments or {}))
        except (TypeError, ValueError) as exc:
            return _error_reply(
                json.dumps({"error": f"Codex 工具参数无效: {exc}"}, ensure_ascii=False),
                protocol_error=True,
            )
        unexpected = _unexpected_scoped_arguments(d, arguments)
        if unexpected:
            return _error_reply(
                json.dumps({
                    "error": (
                        "Codex 工具参数包含 schema 未声明字段: "
                        + ", ".join(unexpected)
                    ),
                }, ensure_ascii=False),
                protocol_error=True,
            )
        try:
            _validate_scoped_query_arguments(name, arguments)
        except (TypeError, ValueError) as exc:
            return _error_reply(
                json.dumps({
                    "error": f"Codex 工具参数不符合受限查询合同: {exc}",
                }, ensure_ascii=False),
                protocol_error=True,
            )
        is_workspace_call = (
            name in _workspace_tool_names()
            or name == CODEX_COMPANY_RESEARCH_TOOL
        )
        if is_workspace_call:
            requested = str(arguments.get("workspace_id") or "").strip()
            if requested and requested != scope.workspace_id:
                return _error_reply(
                    json.dumps({
                        "error": "Codex 工具调用试图越过当前研究空间绑定",
                    }, ensure_ascii=False),
                    protocol_error=True,
                )
            store = _console_store(console_store)
            try:
                _assert_scoped_workspace_binding(scope, name, store)
            except Exception as exc:  # noqa: BLE001 - safe protocol boundary
                return _error_reply(
                    json.dumps({"error": str(exc)}, ensure_ascii=False),
                    protocol_error=True,
                )
            if name != CODEX_COMPANY_RESEARCH_TOOL:
                arguments["workspace_id"] = scope.workspace_id
        _apply_scoped_query_overrides(name, arguments, scope=scope)

        if requires_confirmation:
            from guanlan_v2.glmcp.scope import confirm_codex_tool_call
            approved = await confirm_codex_tool_call(scope, name, arguments)
            if not approved:
                return _error_reply(
                    json.dumps({
                        "error": f"用户未批准或已取消 Codex 工具调用: {name}",
                    }, ensure_ascii=False),
                    protocol_error=True,
                )
            try:
                _assert_scoped_workspace_binding(scope, name, store)
            except Exception as exc:  # noqa: BLE001 - approval can wait minutes
                return _error_reply(
                    json.dumps({"error": str(exc)}, ensure_ascii=False),
                    protocol_error=True,
                )
        elif d.get("gated"):
            return _error_reply(
                json.dumps({
                    "error": f"Codex 工具缺少逐次确认授权: {name}",
                }, ensure_ascii=False),
                protocol_error=True,
            )

    if d["gated"] and scope is None and not _is_write_enabled():
        return _error_reply(
            f"写操作未启用:{name} 是写/销毁类工具,需在 9999 启动环境设 GUANLAN_MCP_WRITE=1 后重启才放行。",
            protocol_error=scope_required,
        )
    try:
        if d.get("builtin") == "workspace_list":
            store = _console_store(console_store)
            result = await asyncio.to_thread(_workspace_list, store, arguments or {})
        elif d.get("builtin") == "codex_company_research_launch":
            if scope is None:
                raise RuntimeError("company_research_v1 只允许 Codex scoped transport")
            callback = getattr(scope, "company_research_launch_callback", None)
            if not callable(callback):
                raise RuntimeError("company_research_v1 Console 后台接缝未连接")
            normalized = validate_codex_company_research_arguments(arguments)
            launched = await callback(deepcopy(normalized))
            result = validate_company_research_launch_result(
                launched,
                sid=scope.sid,
                workspace_id=scope.workspace_id,
                code=scope.workspace_code,
                subject_sha256=scope.workspace_subject_sha256,
                root_sha256=scope.workspace_root_sha256,
            )
        else:
            impl = (_scoped_query_impl(name) if scope is not None else None)
            if impl is None:
                impl = _resolve_impl(d, name)
            from guanlan_v2.console.workspace_tools import ALL_WORKSPACE_TOOLS
            if name in ALL_WORKSPACE_TOOLS:
                store = _console_store(console_store)
                result = await asyncio.to_thread(
                    _run_workspace_impl, impl, name, arguments or {}, store,
                    scope.sid if scope is not None else "mcp",
                    codex_scope=scope,
                )
            else:
                result = await asyncio.to_thread(impl, **(arguments or {}))
    except Exception as e:  # noqa: BLE001 — 诚实失败显形
        return _error_reply(
            json.dumps({"error": f"{type(e).__name__}: {e}"}, ensure_ascii=False),
            protocol_error=scope_required,
        )
    if isinstance(result, dict) and result.get("background"):
        try:
            receipt = await asyncio.to_thread(_spawn_background_detached, result["background"])
        except Exception as e:  # noqa: BLE001 — 诚实失败显形,绝不假成功
            return _error_reply(json.dumps(
                {"error": f"后台任务启动失败: {type(e).__name__}: {e}"}, ensure_ascii=False),
                protocol_error=scope_required,
            )
        base = str(result.get("content") or "")
        return [TextContent(type="text", text=(base + "\n" + receipt).strip())]
    return _success_reply(result, name=name, scope=scope)


def build_server(console_store: Any = None, *, scope_required: bool = False):
    from mcp.server import Server
    from mcp.types import Tool, ToolAnnotations
    # 急切预热:把 console.tools / 引擎 buddy.tools 这类重导入在进入 stdio 读循环
    # 【之前】跑完。否则首个 list_tools 在请求处理中触发冷导入,会卡死 stdio 读循环
    # (初始化能回、tools/list 永不返回)。镜像引擎模块级 TOOLS 的急切构建。
    # 注(2026-07-30):litellm 已懒化,不再被本预热拉起——首个 LLM 调用在 to_thread
    # 工作线程付冷税,不堵 stdio 读循环;预热护的是 buddy/console 其余重导入。
    _codex_decls() if scope_required else _decls()
    server = Server(
        "guanlan",
        instructions=(
            (
                "观澜帷幄当前研究空间的受限控制面。ww_workspace_* 和整图 ww_workflow_* 的空间已由服务端绑定，"
                "客户端不得选择 workspace_id；需确认的写入会在执行点等待帷幄批准。"
                if scope_required else
                "观澜本机研究控制面。调用 ww_workspace_* 或整图 ww_workflow_* 前先用 guanlan_workspace_list "
                "取得 workspace_id，并在每次调用中显式传入。"
            )
            + "写入和执行类工具受服务端写门及空间模式双重限制；错误必须原样报告，绝不假成功。"
        ),
    )

    @server.list_tools()
    async def list_tools() -> List[Tool]:
        scope = _codex_scope() if scope_required else None
        if scope_required and scope is None:
            return []
        if scope is not None:
            from guanlan_v2.glmcp.scope import codex_scope_is_active
            if not codex_scope_is_active(scope):
                return []
        visible = (
            [d for d in _codex_decls()
             if d["name"] in scope.allowed_tools and (
                 not d.get("destructive")
                 or (
                     d["name"] in scope.confirmation_required_tools
                     and (
                         d["name"] in _workspace_tool_names()
                         or d["name"] == CODEX_COMPANY_RESEARCH_TOOL
                     )
                     and callable(scope.confirm_callback)
                     and (
                         d["name"] != CODEX_COMPANY_RESEARCH_TOOL
                         or callable(scope.company_research_launch_callback)
                     )
                 )
             )]
            if scope is not None else _decls()
        )
        return [Tool(name=d["name"], description=d["description"],
                     inputSchema=_scoped_schema(d, scoped=scope is not None),
                     annotations=ToolAnnotations(readOnlyHint=d["read_only"], destructiveHint=d["destructive"]))
                for d in visible]

    @server.call_tool()
    async def call_tool(name: str, arguments: Dict[str, Any]) -> Any:
        return await dispatch_tool(
            name, arguments or {}, console_store=console_store,
            scope_required=scope_required,
        )

    return server
