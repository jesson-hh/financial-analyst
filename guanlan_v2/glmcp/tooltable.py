# -*- coding: utf-8 -*-
"""guanlan MCP 工具表:从 WW_TOOL_TABLE(去 console-UI-only)+ alpha-zoo 派生。"""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, List

from guanlan_v2.channels.agent_tools import CHANNEL_TOOL_NAMES
from guanlan_v2.console.workspace_tools import ALL_WORKSPACE_TOOLS

# console-UI-only(改会话计划/返回浏览器顶栏导航提示)与前端信封类(靠 window.GL 落地,MCP 语境=空转假成功)
# → 不暴露。ww_report_run/ww_etf_report_run 不在此列:其 background 信封经 dispatch_tool 的
# _spawn_background_detached detached 子进程真跑(见 server.py)。
_EXCLUDED = {
    "ww_plan_update", "ww_show_page", "ww_seats_bind",
} | set(CHANNEL_TOOL_NAMES)
# 引擎 alpha-zoo 研究线(已在 CONSOLE_ALLOWED 放行)
_ALPHA_ZOO = ["alpha_list", "alpha_show", "alpha_compare", "alpha_bench",
              "event_report", "alpha_forge", "factor_report"]
# alpha_forge 的 save 会写引擎因子库，保留公共 MCP 写门。alpha_bench 与
# factor_report 也有可选/隐式持久化，但公共 MCP 的历史契约没有确认回调；先把
# annotations 诚实标为非只读，Codex 专用端再由 server.py 强制成 query-only。
_ALPHA_WRITERS = {"alpha_forge"}
_ALPHA_MUTATES_UNGATED = {
    "alpha_bench", "factor_report",
    # The legacy free-form expression DSL can call pandas Series writers;
    # these are not safe to describe as read-only even though writing is not
    # their advertised purpose.
    "alpha_compare", "event_report",
}
# confirm=False 但会落持久状态 → 标非只读,但不锁。数据快照工具只把
# 查询命中写入调用方显式选择的私有 workspace，不改 stocks/全局研究库。
_MUTATES_UNGATED = {
    # Default/public MCP news search writes the shared sentiment cache.  The
    # dedicated Codex transport is safe only because server.py injects the
    # private keyword-only _write_through=False argument.
    "ww_news_search",
    # /screen/run 的 v4 主路径会无条件追加 picks 记录。Codex 专用端通过
    # 私有 _write_through=False 改走不落档、也不刷新缓存的查询路径。
    "ww_screen_run",
    # These legacy research endpoints pass model-controlled expressions to a
    # permissive pandas-Series DSL.  Until a trusted-name adapter replaces it,
    # their public annotations must not claim read-only semantics.
    "ww_factor_analyze",
    "ww_backtest",
    "ww_factor_compose",
    "ww_feature_build",
    "ww_factor_tsic",
    "ww_memory_write",
    "ww_workspace_trade_disclosure_calendar",
    "ww_workspace_tw_monthly_revenue",
    "ww_workspace_cn_commodity_futures",
    "ww_workspace_control_lists",
    "ww_workspace_sec_filings_xbrl",
    "ww_workspace_finra_short_volume",
    "ww_workspace_us_treasury",
    "ww_workspace_cftc_cot",
    "ww_workspace_overseas_earnings_calendar",
    "ww_workspace_newsradar",
    "ww_workspace_company_research_prepare",
    # Presentation creates media/download records or fresh derived chart
    # files.  They remain non-destructive but must never advertise read-only.
    "ww_workspace_quant_chart",
    "ww_workspace_publish_image",
    "ww_workspace_publish_document",
}

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
        mutates_ungated = name in _ALPHA_MUTATES_UNGATED
        out.append({
            "name": name, "description": tool.description, "inputSchema": tool.input_schema,
            "read_only": not (writer or mutates_ungated),
            "destructive": writer, "gated": writer, "engine": True,
        })
    return out
