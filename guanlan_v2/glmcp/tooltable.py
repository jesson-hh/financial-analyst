# -*- coding: utf-8 -*-
"""guanlan MCP 工具表:从 WW_TOOL_TABLE(去 console-UI-only)+ alpha-zoo 派生。"""
from __future__ import annotations

from typing import Any, Dict, List

# console-UI-only(改会话计划 / 往 console 右栏弹页面),MCP 无意义 → 不暴露
_EXCLUDED = {"ww_plan_update", "ww_show_page"}
# 引擎 alpha-zoo 研究线(已在 CONSOLE_ALLOWED 放行)
_ALPHA_ZOO = ["alpha_list", "alpha_show", "alpha_compare", "alpha_bench",
              "event_report", "alpha_forge", "factor_report"]
# alpha-zoo 里唯一会写盘的(save 写引擎因子库)→ 锁;其余贵但只读
_ALPHA_WRITERS = {"alpha_forge"}
# confirm=False 但写全局记忆 → 标非只读,但不锁(低危)
_MUTATES_UNGATED = {"ww_memory_write"}


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
            "name": name, "description": t["description"], "inputSchema": t["input_schema"],
            "read_only": (not confirm) and name not in _MUTATES_UNGATED,
            "destructive": confirm, "gated": confirm, "engine": False,
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
