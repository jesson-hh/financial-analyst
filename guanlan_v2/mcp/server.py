# -*- coding: utf-8 -*-
"""guanlan MCP server(low-level mcp.server.Server)。镜像引擎 mcp_server,工具复用 ww_/引擎 impl。"""
from __future__ import annotations

import asyncio
import json
import os
from typing import Any, Dict, List

_DECLS: List[Dict[str, Any]] | None = None


def _decls() -> List[Dict[str, Any]]:
    global _DECLS
    if _DECLS is None:
        from guanlan_v2.mcp.tooltable import build_mcp_tools
        _DECLS = build_mcp_tools()
    return _DECLS


def _by_name() -> Dict[str, Dict[str, Any]]:
    return {d["name"]: d for d in _decls()}


def _is_write_enabled() -> bool:
    return os.environ.get("GUANLAN_MCP_WRITE") == "1"


def _to_text(result: Any) -> str:
    """impl/引擎结果 → 文本。ww_ impl 回 dict{content};引擎 ToolResult 有 .content。"""
    if isinstance(result, dict):
        return str(result.get("content") or json.dumps(result, ensure_ascii=False, default=str))
    content = getattr(result, "content", None)
    return str(content if content is not None else result)


def _resolve_impl(d: Dict[str, Any], name: str):
    if d["engine"]:
        from financial_analyst.buddy import tools as bt
        return {t.name: t for t in bt.TOOL_REGISTRY}[name].run
    import guanlan_v2.console.tools as ct
    return {t["name"]: t["impl"] for t in ct.WW_TOOL_TABLE}[name]


async def dispatch_tool(name: str, arguments: Dict[str, Any]) -> List[Any]:
    """派发一次 MCP 工具调用 → list[TextContent]。写门 + to_thread + 诚实失败。"""
    from mcp.types import TextContent
    d = _by_name().get(name)
    if d is None:
        return [TextContent(type="text", text=json.dumps({"error": f"未知工具: {name}"}, ensure_ascii=False))]
    if d["gated"] and not _is_write_enabled():
        return [TextContent(type="text",
                text=f"写操作未启用:{name} 是写/销毁类工具,需在 9999 启动环境设 GUANLAN_MCP_WRITE=1 后重启才放行。")]
    try:
        impl = _resolve_impl(d, name)
        result = await asyncio.to_thread(impl, **(arguments or {}))
    except Exception as e:  # noqa: BLE001 — 诚实失败显形
        return [TextContent(type="text", text=json.dumps({"error": f"{type(e).__name__}: {e}"}, ensure_ascii=False))]
    return [TextContent(type="text", text=_to_text(result))]


def build_server():
    from mcp.server import Server
    from mcp.types import Tool, ToolAnnotations
    server = Server("guanlan")

    @server.list_tools()
    async def list_tools() -> List[Tool]:
        return [Tool(name=d["name"], description=d["description"], inputSchema=d["inputSchema"],
                     annotations=ToolAnnotations(readOnlyHint=d["read_only"], destructiveHint=d["destructive"]))
                for d in _decls()]

    @server.call_tool()
    async def call_tool(name: str, arguments: Dict[str, Any]) -> List[Any]:
        return await dispatch_tool(name, arguments or {})

    return server
