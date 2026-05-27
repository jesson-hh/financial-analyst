"""MCP server — expose financial-analyst capabilities to Claude Desktop / Claude Code / Cursor / Codex CLI.

Tools (20):
  Fast read (<1s):     quick_quote, quick_factors, memory_search,
                       list_past_reports, read_past_report,
                       list_dream_proposals, chain_lookup, list_audit
  Medium (~10s-3min):  ask, mainline, brief, intraday, dream,
                       dream_aggregate, overseas_radar
  Slow (3-30min):      report, data_update
  Memory mutation:     accept_proposal, reject_proposal, revert_proposal
                       (all audit-logged to ~/.financial-analyst/audit.jsonl
                        with source='mcp'; reversible via revert_proposal)

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


async def _tool_overseas_radar(asof: Optional[str] = None) -> Dict[str, Any]:
    """v1.9.7 global-market transmission radar. ~1-2 min."""
    from financial_analyst.cli import _run_overseas_radar
    out_dir = Path("./out")
    await _run_overseas_radar(asof=asof, out_dir=out_dir)
    json_candidates = sorted(out_dir.glob("overseas_radar_*.json"),
                             key=lambda p: p.stat().st_mtime, reverse=True)
    if json_candidates:
        return json.loads(json_candidates[0].read_text(encoding="utf-8"))
    md_candidates = sorted(out_dir.glob("overseas_radar_*.md"),
                           key=lambda p: p.stat().st_mtime, reverse=True)
    if md_candidates:
        md = md_candidates[0]
        return {
            "asof": asof,
            "_md_path": str(md),
            "_md_excerpt": md.read_text(encoding="utf-8")[:3000],
        }
    return {"asof": asof, "error": "no overseas radar output file generated"}


async def _tool_data_update(
    codes: Optional[str] = None,
    skip_5min: bool = False,
    include_f10: bool = False,
    include_concepts: bool = False,
    include_northbound: bool = False,
    include_fund_flow: bool = False,
    fund_flow_lmt: int = 120,
    include_margin: bool = False,
    include_lockup: bool = False,
    include_corporate_actions: bool = False,
    include_ths_hot: bool = False,
    include_announcements: bool = False,
    timeout_sec: int = 600,
) -> Dict[str, Any]:
    """Trigger incremental data refresh via `fa data update` subprocess.

    Default scope: 日线 OHLCV + 5min + daily_basic (PE/PB/MV/turnover_rate).
    With include_* flags, extends to F10 events / THS concepts / northbound flow /
    per-stock 东财 fund flow / 融资融券 / 限售解禁 / 公司行为 / 同花顺强势股 / 巨潮公告.

    Slow: ~3-5 min default; include_f10 adds ~30 min; include_fund_flow scales
    with N codes × fund_flow_lmt days.
    """
    cmd = ["financial-analyst", "data", "update"]
    if codes:
        cmd += ["--codes", codes]
    if skip_5min:
        cmd += ["--skip-5min"]
    if include_f10:
        cmd += ["--include-f10"]
    if include_concepts:
        cmd += ["--include-concepts"]
    if include_northbound:
        cmd += ["--include-northbound"]
    if include_fund_flow:
        cmd += ["--include-fund-flow", "--fund-flow-lmt", str(fund_flow_lmt)]
    if include_margin:
        cmd += ["--include-margin"]
    if include_lockup:
        cmd += ["--include-lockup"]
    if include_corporate_actions:
        cmd += ["--include-corporate-actions"]
    if include_ths_hot:
        cmd += ["--include-ths-hot"]
    if include_announcements:
        cmd += ["--include-announcements"]

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout_sec)
    except asyncio.TimeoutError:
        proc.kill()
        return {"cmd": " ".join(cmd), "error": f"timeout >{timeout_sec}s"}

    return {
        "cmd": " ".join(cmd),
        "returncode": proc.returncode,
        "stdout_tail": stdout.decode("utf-8", errors="replace")[-2000:] if stdout else "",
        "stderr_tail": stderr.decode("utf-8", errors="replace")[-1000:] if stderr else "",
    }


async def _tool_chain_lookup(code: str) -> Dict[str, Any]:
    """Industry-chain context for a stock — primary product, peers, up/downstream. <1s."""
    from financial_analyst.data.loaders.chain_kb import ChainKBLoader
    loader = ChainKBLoader()
    ctx = loader.chain_context(code)
    if ctx is None:
        return {"code": code, "error": "no chain membership found"}
    return ctx


async def _tool_accept_proposal(target: str, dry_run: bool = False) -> Dict[str, Any]:
    """Promote a dream-loop proposal to active agent memory.

    SAFETY: every accept writes to ~/.financial-analyst/audit.jsonl with
    source='mcp' and git-stages the file. Reversible via revert_proposal.
    """
    from financial_analyst.memory_ops import accept_proposal
    return accept_proposal(target, source="mcp", dry_run=dry_run, project_root=Path.cwd())


async def _tool_reject_proposal(target: str) -> Dict[str, Any]:
    """Delete a dream-loop proposal without promoting. Audited as source='mcp'."""
    from financial_analyst.memory_ops import reject_proposal
    return reject_proposal(target, source="mcp", project_root=Path.cwd())


async def _tool_revert_proposal(target: str) -> Dict[str, Any]:
    """Undo a prior accept — move memories/<agent>/<slug>.md back to _proposed/.

    Audit entry includes reverted_id pointing to the original accept's audit id.
    """
    from financial_analyst.memory_ops import revert_proposal
    return revert_proposal(target, source="mcp", project_root=Path.cwd())


async def _tool_list_audit(limit: int = 20) -> List[Dict[str, Any]]:
    """Return the last N audit entries from ~/.financial-analyst/audit.jsonl (newest first)."""
    from financial_analyst.memory_ops import list_audit
    return list_audit(limit=limit)


async def _tool_dream_aggregate(min_count: int = 3, threshold: float = 0.4,
                                 dry_run: bool = False) -> Dict[str, Any]:
    """Aggregate Tier-4 introspector pending proposals via Jaccard clustering.

    Different from ``dream``: that one runs OutcomeTracker + Introspector (back-derived
    from T+5d real prices); this one clusters Tier-4 proposals already written to
    _pending_introspections/ (clusters with duplicates >= min_count get promoted to _proposed/).
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
    "overseas_radar": {
        "handler": _tool_overseas_radar,
        "description": (
            "v1.9.7 global-market transmission radar. Overnight US/HK indices + global news "
            "→ A-share follow-through judgment + actionable signals. ~1-2 min."
        ),
        "schema": {
            "type": "object",
            "properties": {"asof": {"type": "string"}},
        },
    },
    "data_update": {
        "handler": _tool_data_update,
        "description": (
            "Trigger incremental data refresh (日线 OHLCV + 5min + daily_basic). "
            "Default ~3-5 min, all instruments. Use codes to limit. include_f10 adds ~30 min "
            "for TDX F10 events; include_concepts pulls THS concept stocks; include_northbound "
            "pulls 沪深股通 flow. Returns subprocess stdout/stderr tail for diagnosis."
        ),
        "schema": {
            "type": "object",
            "properties": {
                "codes": {
                    "type": "string",
                    "description": "Comma-separated codes (SH600519,SZ300750) or @file path; null = all instruments",
                },
                "skip_5min": {"type": "boolean", "default": False, "description": "Skip 5min update (only daily)"},
                "include_f10": {"type": "boolean", "default": False, "description": "Also refresh TDX F10 events. Adds ~30 min."},
                "include_concepts": {"type": "boolean", "default": False, "description": "Also refresh THS concept stocks (needs adata)."},
                "include_northbound": {"type": "boolean", "default": False, "description": "Also refresh northbound flow (needs akshare)."},
                "include_fund_flow": {"type": "boolean", "default": False, "description": "Also refresh per-stock 东财 fund flow (主力/大单/中单/小单/超大单, zero token)."},
                "fund_flow_lmt": {"type": "integer", "default": 120, "description": "Fund-flow lookback in trading days (max ~120 upstream limit). Only used when include_fund_flow=true."},
                "include_margin": {"type": "boolean", "default": False, "description": "Also refresh 融资融券明细 daily (东财 datacenter, zero token)."},
                "include_lockup": {"type": "boolean", "default": False, "description": "Also refresh 限售解禁 calendar + 90-day forward warning."},
                "include_corporate_actions": {"type": "boolean", "default": False, "description": "Also refresh 公司行为: 股东户数 + 大宗交易 + 分红送转."},
                "include_ths_hot": {"type": "boolean", "default": False, "description": "Also refresh 同花顺当日强势股 + 题材归因 (default on UI plain click)."},
                "include_announcements": {"type": "boolean", "default": False, "description": "Also refresh 巨潮公告索引 (board-wide coverage)."},
                "timeout_sec": {"type": "integer", "default": 600, "description": "Subprocess timeout in seconds"},
            },
        },
    },
    "chain_lookup": {
        "handler": _tool_chain_lookup,
        "description": (
            "Industry-chain context for one stock — primary product node + peer codes + "
            "upstream/downstream products + catalyst markdown excerpt. <1s, no LLM."
        ),
        "schema": {
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "Stock code like SH688256"},
            },
            "required": ["code"],
        },
    },
    "accept_proposal": {
        "handler": _tool_accept_proposal,
        "description": (
            "Promote a dream-loop proposal (memories/_proposed/<agent>/<slug>.md) to "
            "active agent memory. Writes audit entry (source='mcp') to "
            "~/.financial-analyst/audit.jsonl and git-stages the file. "
            "Use dry_run=true to preview without changes. Reversible via revert_proposal. "
            "Refuses to overwrite existing memory files."
        ),
        "schema": {
            "type": "object",
            "properties": {
                "target": {"type": "string", "description": "Proposal id, format '<agent>/<slug>' (e.g. 'bear-advocate/F15_new_pitfall')"},
                "dry_run": {"type": "boolean", "default": False, "description": "If true, return {would_move, dry_run:true} without touching files or audit"},
            },
            "required": ["target"],
        },
    },
    "reject_proposal": {
        "handler": _tool_reject_proposal,
        "description": (
            "Delete a dream-loop proposal without promoting. Writes audit entry "
            "(source='mcp') and irreversibly removes the file from _proposed/."
        ),
        "schema": {
            "type": "object",
            "properties": {
                "target": {"type": "string", "description": "Proposal id, format '<agent>/<slug>'"},
            },
            "required": ["target"],
        },
    },
    "revert_proposal": {
        "handler": _tool_revert_proposal,
        "description": (
            "Undo a prior accept — moves memories/<agent>/<slug>.md back to _proposed/ "
            "and git-stages the removal. Audit entry includes reverted_id pointing to "
            "the original accept. Errors if the file is not in active memory."
        ),
        "schema": {
            "type": "object",
            "properties": {
                "target": {"type": "string", "description": "Same '<agent>/<slug>' identifier that was accepted"},
            },
            "required": ["target"],
        },
    },
    "list_audit": {
        "handler": _tool_list_audit,
        "description": (
            "Return the last N entries from ~/.financial-analyst/audit.jsonl (newest first). "
            "Covers all accept/reject/revert actions across CLI, MCP, and future UI."
        ),
        "schema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "default": 20, "description": "Max entries to return (default 20)"},
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
