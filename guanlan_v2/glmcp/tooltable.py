# -*- coding: utf-8 -*-
"""guanlan MCP 工具表:从 WW_TOOL_TABLE(去 console-UI-only)+ alpha-zoo 派生。"""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, List

from guanlan_v2.console.workspace_tools import ALL_WORKSPACE_TOOLS

# console-UI-only(改会话计划/往右栏弹页面)与前端信封类(靠 window.GL 落地,MCP 语境=空转假成功)
# → 不暴露。ww_report_run/ww_etf_report_run 不在此列:其 background 信封经 dispatch_tool 的
# _spawn_background_detached detached 子进程真跑(见 server.py)。
_EXCLUDED = {"ww_plan_update", "ww_show_page", "ww_seats_bind"}
# 引擎 alpha-zoo 研究线(已在 CONSOLE_ALLOWED 放行)
_ALPHA_ZOO = ["alpha_list", "alpha_show", "alpha_compare", "alpha_bench",
              "event_report", "alpha_forge", "factor_report"]
# alpha-zoo 里唯一会写盘的(save 写引擎因子库)→ 锁;其余贵但只读
_ALPHA_WRITERS = {"alpha_forge"}
# confirm=False 但写全局记忆 → 标非只读,但不锁(低危)
_MUTATES_UNGATED = {"ww_memory_write"}

_WORKSPACE_ID_PROPERTY = {
    "type": "string",
    "pattern": r"^rw_(?:general|[0-9a-f]{12})$",
    "description": "目标研究空间编号；先调用 guanlan_workspace_list 获取",
}


def _mcp_schema(name: str, schema: Dict[str, Any]) -> Dict[str, Any]:
    """Add an explicit workspace selector without changing Console schemas."""
    result = deepcopy(schema)
    if name not in ALL_WORKSPACE_TOOLS:
        return result
    result.setdefault("type", "object")
    result.setdefault("properties", {})["workspace_id"] = dict(_WORKSPACE_ID_PROPERTY)
    required = list(result.get("required") or [])
    if "workspace_id" not in required:
        required.append("workspace_id")
    result["required"] = required
    return result


def _engine_registry() -> Dict[str, Any]:
    from financial_analyst.buddy import tools as bt
    return {t.name: t for t in bt.TOOL_REGISTRY}


def build_mcp_tools() -> List[Dict[str, Any]]:
    """MCP 工具声明 list。每条:name/description/inputSchema/read_only/destructive/gated/engine。"""
    import guanlan_v2.console.tools as ct
    out: List[Dict[str, Any]] = []
    for t in ct.WW_TOOL_TABLE:
        name = t["name"]
        if name in _EXCLUDED:
            continue
        confirm = bool(t.get("confirm"))
        out.append({
            "name": name, "description": t["description"],
            "inputSchema": _mcp_schema(name, t["input_schema"]),
            "read_only": (not confirm) and name not in _MUTATES_UNGATED,
            "destructive": confirm, "gated": confirm, "engine": False,
        })
    out.append({
        "name": "guanlan_workspace_list",
        "description": (
            "列出观澜研究空间及其 workspace_id，供本 MCP 的 ww_workspace_* 工具选择目标。"
            "只返回安全元数据，不返回本机目录路径。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "include_archived": {
                    "type": "boolean", "default": False,
                    "description": "是否同时列出已归档空间",
                },
            },
        },
        "read_only": True, "destructive": False, "gated": False,
        "engine": False, "builtin": "workspace_list",
    })
    eng = _engine_registry()
    for name in _ALPHA_ZOO:
        tool = eng.get(name)
        if tool is None:
            continue
        writer = name in _ALPHA_WRITERS
        out.append({
            "name": name, "description": tool.description, "inputSchema": tool.input_schema,
            "read_only": not writer, "destructive": writer, "gated": writer, "engine": True,
        })
    return out
