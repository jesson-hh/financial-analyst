"""MCP server — expose financial-analyst capabilities to Claude Desktop / Claude Code / OpenClaw.

Tools:
  ask, quick_quote, quick_factors, memory_search, list_past_reports,
  read_past_report, list_dream_proposals,
  report (slow), mainline, brief, intraday, dream

Wire into Claude Desktop with:
  ~/.config/claude/claude_desktop_config.json   (Linux/Mac)
  %APPDATA%\\Claude\\claude_desktop_config.json  (Windows)
  {
    "mcpServers": {
      "financial-analyst": {
        "command": "financial-analyst-mcp",
        "args": []
      }
    }
  }
"""
from __future__ import annotations
import asyncio
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

# Force UTF-8 stdio for Windows zh-CN PowerShell
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from dotenv import load_dotenv

load_dotenv(override=True)

# Load user plugins (BYOM models, collectors, etc.)
try:
    from financial_analyst.plugins import load_plugins
    load_plugins()
except Exception:
    pass


# ---------------------------------------------------------------------------
# Tool handlers
# ---------------------------------------------------------------------------

async def _tool_ask(query: str) -> Dict[str, Any]:
    from financial_analyst.ask import ask
    output = await ask(query)
    return output.model_dump()


async def _tool_quick_quote(code: str, asof: Optional[str] = None) -> Dict[str, Any]:
    from financial_analyst.ask.tools import quick_quote
    return quick_quote(code, asof=asof)


async def _tool_quick_factors(code: str, asof: Optional[str] = None) -> Dict[str, Any]:
    from financial_analyst.ask.tools import quick_factors
    return quick_factors(code, asof=asof)


async def _tool_memory_search(query: str, agent: Optional[str] = None, top_k: int = 5) -> List[Dict[str, Any]]:
    from financial_analyst.ask.tools import search_memory
    return search_memory(query, agent=agent, top_k=top_k)


async def _tool_list_past_reports(limit: int = 10) -> List[Dict[str, Any]]:
    from financial_analyst.ask.tools import list_past_reports
    return list_past_reports(limit=limit)


async def _tool_read_past_report(code: str, date_str: Optional[str] = None) -> Dict[str, Any]:
    from financial_analyst.ask.tools import read_past_report
    text = read_past_report(code, date_str=date_str)
    return {"code": code, "date": date_str, "markdown": text or "(no report found)"}


async def _tool_list_dream_proposals() -> List[Dict[str, Any]]:
    from financial_analyst.ask.tools import list_dream_proposals
    return list_dream_proposals()


async def _tool_report(code: str, asof: Optional[str] = None) -> Dict[str, Any]:
    """Full 13-agent deep-dive. SLOW — typically 5-10 minutes. May time out in some clients."""
    from financial_analyst.tui import run_report_oneshot
    out_dir = Path("./out")
    await run_report_oneshot(code=code, asof=asof, out_dir=out_dir)
    # Find the most recent report JSON for this code
    candidates = sorted(out_dir.glob(f"{code.upper()}_*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not candidates:
        return {"code": code, "error": "no report file generated"}
    summary = json.loads(candidates[0].read_text(encoding="utf-8"))
    md_path = candidates[0].with_suffix(".md")
    summary["_md_path"] = str(md_path)
    if md_path.exists():
        summary["_md_excerpt"] = md_path.read_text(encoding="utf-8")[:2000]
    return summary


async def _tool_mainline(asof: Optional[str] = None) -> Dict[str, Any]:
    from financial_analyst.cli import _run_mainline
    out_dir = Path("./out")
    await _run_mainline(asof=asof, panel=None, out_dir=out_dir)
    candidates = sorted(out_dir.glob("mainline_*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not candidates:
        return {"asof": asof, "error": "no mainline file generated"}
    return json.loads(candidates[0].read_text(encoding="utf-8"))


async def _tool_brief(asof: Optional[str] = None, max_scan: int = 1000) -> Dict[str, Any]:
    from financial_analyst.cli import _run_brief
    out_dir = Path("./out")
    await _run_brief(asof=asof, universe="all", universe_file=None, max_scan=max_scan, out_dir=out_dir)
    candidates = sorted(out_dir.glob("morning_brief_*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not candidates:
        return {"asof": asof, "error": "no brief file generated"}
    return json.loads(candidates[0].read_text(encoding="utf-8"))


async def _tool_intraday(codes: str = "", asof: Optional[str] = None) -> Dict[str, Any]:
    from financial_analyst.cli import _run_intraday
    out_dir = Path("./out")
    await _run_intraday(codes=codes, asof=asof, out_dir=out_dir)
    candidates = sorted(out_dir.glob("intraday_review_*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not candidates:
        return {"asof": asof, "error": "no intraday review generated"}
    return json.loads(candidates[0].read_text(encoding="utf-8"))


async def _tool_dream(since: int = 30, dry_run: bool = True) -> Dict[str, Any]:
    from financial_analyst.cli import _run_dream
    out_dir = Path("./out")
    await _run_dream(since=since, dry_run=dry_run, out_dir=out_dir)
    return {"since_days": since, "dry_run": dry_run, "see": "memories/_proposed/"}


async def _tool_dream_aggregate(min_count: int = 3, threshold: float = 0.4,
                                 dry_run: bool = False) -> Dict[str, Any]:
    """Aggregate Tier-4 introspector pending proposals via Jaccard clustering.

    与 ``dream`` 不同: 那个跑 OutcomeTracker + Introspector (基于 T+5d 实际价格反推),
    这个聚类已经写到 _pending_introspections/ 的 Tier-4 提案 (重复 ≥ min_count 升级到 _proposed/).
    """
    from financial_analyst.dream.aggregator import aggregate_pending
    written, stats = aggregate_pending(
        memory_root=Path("memories"),
        min_count=min_count,
        threshold=threshold,
        dry_run=dry_run,
    )
    return {
        "stats": stats,
        "promoted_files": [str(p) for p in written],
        "see": "memories/_proposed/  → run `fa dream review` to inspect",
    }


# ---------------------------------------------------------------------------
# Tool registry — name → {handler, description, schema}
# ---------------------------------------------------------------------------

TOOLS: Dict[str, Dict[str, Any]] = {
    "ask": {
        "handler": _tool_ask,
        "description": (
            "Natural-language question about A-share stocks. Uses tool-calling internally to search memory / "
            "past reports / quick quotes. Fast (~10-30s). Good for ad-hoc questions like 'PE of SH600519' "
            "or 'what did the last report say about SZ002594'."
        ),
        "schema": {
            "type": "object",
            "properties": {"query": {"type": "string", "description": "Natural language question"}},
            "required": ["query"],
        },
    },
    "quick_quote": {
        "handler": _tool_quick_quote,
        "description": "Fast latest OHLCV + PE/PB/MV for one A-share. <1s, no LLM, no model.",
        "schema": {
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "Stock code like SH600519"},
                "asof": {"type": "string", "description": "Optional YYYY-MM-DD; default today"},
            },
            "required": ["code"],
        },
    },
    "quick_factors": {
        "handler": _tool_quick_factors,
        "description": "Compute 34 daily factors (rev/mom/vol/macd/rsi/bb/obv etc) for one stock. ~1s.",
        "schema": {
            "type": "object",
            "properties": {
                "code": {"type": "string"},
                "asof": {"type": "string"},
            },
            "required": ["code"],
        },
    },
    "memory_search": {
        "handler": _tool_memory_search,
        "description": (
            "FTS5 search across agent memories (pitfalls, rating_system, V1-V10 playbook, "
            "sentiment signals, etc)."
        ),
        "schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "agent": {"type": "string", "description": "Optional filter to one agent's memory"},
                "top_k": {"type": "integer", "default": 5},
            },
            "required": ["query"],
        },
    },
    "list_past_reports": {
        "handler": _tool_list_past_reports,
        "description": "List recent stock research reports in out/. Returns code, date, rating, action for each.",
        "schema": {
            "type": "object",
            "properties": {"limit": {"type": "integer", "default": 10}},
        },
    },
    "read_past_report": {
        "handler": _tool_read_past_report,
        "description": "Read the markdown body of a past report. If date omitted, returns the most recent for that code.",
        "schema": {
            "type": "object",
            "properties": {
                "code": {"type": "string"},
                "date_str": {"type": "string", "description": "Optional YYYY-MM-DD"},
            },
            "required": ["code"],
        },
    },
    "list_dream_proposals": {
        "handler": _tool_list_dream_proposals,
        "description": "List staged memory proposals from /dream pending human review.",
        "schema": {"type": "object", "properties": {}},
    },
    "report": {
        "handler": _tool_report,
        "description": (
            "Full 13-agent deep-dive report on one stock. SLOW (5-10 minutes) — may time out in some MCP "
            "clients. Returns rating/action/target/stop + markdown excerpt."
        ),
        "schema": {
            "type": "object",
            "properties": {
                "code": {"type": "string"},
                "asof": {"type": "string"},
            },
            "required": ["code"],
        },
    },
    "mainline": {
        "handler": _tool_mainline,
        "description": (
            "Monthly market-structure scan. Classifies sectors into 5 states "
            "(mainline/revival/initiation/decay/cold). Returns status groups + "
            "★ golden signal (initiation→mainline switch, +5.54pp fwd_60d)."
        ),
        "schema": {
            "type": "object",
            "properties": {"asof": {"type": "string"}},
        },
    },
    "brief": {
        "handler": _tool_brief,
        "description": (
            "Daily A-share morning brief — scans market for 异动 stocks (by market-cap-tier thresholds). "
            "Returns top_gainers/losers/volume_anomalies + watchlist."
        ),
        "schema": {
            "type": "object",
            "properties": {
                "asof": {"type": "string"},
                "max_scan": {"type": "integer", "default": 1000},
            },
        },
    },
    "intraday": {
        "handler": _tool_intraday,
        "description": (
            "Lunch-break per-stock review. Judges each as OK / 警惕 / 撤离 based on "
            "intraday vs past report's predicted action."
        ),
        "schema": {
            "type": "object",
            "properties": {
                "codes": {"type": "string", "description": "Comma-separated codes; empty for auto-detect"},
                "asof": {"type": "string"},
            },
        },
    },
    "dream": {
        "handler": _tool_dream,
        "description": (
            "Run the dream loop: introspect past reports vs T+5d outcomes, propose memory updates to "
            "memories/_proposed/. Set dry_run=false to actually write."
        ),
        "schema": {
            "type": "object",
            "properties": {
                "since": {"type": "integer", "default": 30},
                "dry_run": {"type": "boolean", "default": True},
            },
        },
    },
    "dream_aggregate": {
        "handler": _tool_dream_aggregate,
        "description": (
            "Aggregate Tier-4 introspector pending proposals (memories/_pending_introspections/) "
            "via Jaccard token clustering. Cluster size >= min_count (default 3) gets promoted to "
            "memories/_proposed/<agent>/<slug>.md with confidence by case count (3-5=med, 6+=high). "
            "Distinct from `dream` which runs OutcomeTracker based on T+5d real prices."
        ),
        "schema": {
            "type": "object",
            "properties": {
                "min_count": {"type": "integer", "default": 3,
                              "description": "最少出现次数才升级到 _proposed/"},
                "threshold": {"type": "number", "default": 0.4,
                              "description": "Jaccard 相似度阈值 (boost-only 关键词主导)"},
                "dry_run": {"type": "boolean", "default": False,
                            "description": "True 只打印聚类结果不写盘"},
            },
        },
    },
}


# ---------------------------------------------------------------------------
# Server builder
# ---------------------------------------------------------------------------

def _build_server():
    """Build the MCP server with all tools registered."""
    from mcp.server import Server
    from mcp.types import Tool, TextContent

    server = Server("financial-analyst")

    @server.list_tools()
    async def list_tools() -> List[Tool]:
        return [
            Tool(name=name, description=defn["description"], inputSchema=defn["schema"])
            for name, defn in TOOLS.items()
        ]

    @server.call_tool()
    async def call_tool(name: str, arguments: Dict[str, Any]) -> List[TextContent]:
        if name not in TOOLS:
            return [TextContent(type="text", text=json.dumps({"error": f"Unknown tool: {name}"}))]
        try:
            handler = TOOLS[name]["handler"]
            result = await handler(**arguments)
            return [TextContent(type="text", text=json.dumps(result, ensure_ascii=False, default=str, indent=2))]
        except Exception as exc:
            return [TextContent(type="text", text=json.dumps({"error": f"{type(exc).__name__}: {exc}"}))]

    return server


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    """Entry point for `financial-analyst-mcp` console script.

    Reads JSON-RPC from stdin, writes responses to stdout. Standard MCP stdio mode.
    """
    from mcp.server.stdio import stdio_server

    async def _run():
        server = _build_server()
        async with stdio_server() as (read_stream, write_stream):
            await server.run(
                read_stream,
                write_stream,
                server.create_initialization_options(),
            )

    asyncio.run(_run())


if __name__ == "__main__":
    main()
