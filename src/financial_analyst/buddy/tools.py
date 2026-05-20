"""Tool registry for the Buddy conversational agent.

Each tool wraps an existing CLI helper. Tools expose:
    - ``name``: identifier the LLM uses in tool calls
    - ``description``: 1-2 sentences for the LLM to decide when to use it
    - ``input_schema``: JSON schema for tool arguments
    - ``run(**args) → ToolResult``: invokes the wrapped helper

Tool descriptions are written in BOTH Chinese and English so the LLM
can match user requests in either language.

Anthropic / OpenAI / Qwen all accept the same JSON-schema-shaped tool
list (LiteLLM normalises across providers).
"""
from __future__ import annotations
import asyncio
import json
import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional


@dataclass
class ToolResult:
    """Return value from a tool. ``content`` is what the LLM sees; ``side_effect``
    is freeform metadata for the REPL to display (e.g., file paths written).
    """
    content: str
    is_error: bool = False
    side_effect: Optional[Dict[str, Any]] = None


@dataclass
class Tool:
    """One LLM-callable tool."""
    name: str
    description: str
    input_schema: Dict[str, Any]
    run: Callable[..., ToolResult]
    cost_hint: str = "instant"  # one of: instant | seconds | minutes
    confirm_required: bool = False  # ask user before running

    def to_anthropic_schema(self) -> Dict[str, Any]:
        """Render for Anthropic / Claude tool-use."""
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
        }

    def to_openai_schema(self) -> Dict[str, Any]:
        """Render for OpenAI / Qwen function-calling."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.input_schema,
            },
        }


# ---------------------------------------------------------------------------
# Tool implementations — each wraps a CLI helper
# ---------------------------------------------------------------------------


def _tool_report(code: str, asof: Optional[str] = None) -> ToolResult:
    """Run a full single-stock deep-dive report."""
    asof = asof or "today"  # CLI handles 'today' as None
    cmd = ["financial-analyst", "report", code]
    if asof and asof != "today":
        cmd += ["--asof", asof]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=900)
    except subprocess.TimeoutExpired:
        return ToolResult("Report timed out after 15 minutes.", is_error=True)
    if proc.returncode != 0:
        return ToolResult(
            f"Report failed (exit {proc.returncode}):\n{proc.stderr[-500:]}",
            is_error=True,
        )
    # Find the markdown output path
    asof_for_path = asof if asof and asof != "today" else None
    md_files = sorted(Path("out").glob(f"{code}_*.md"))
    if not md_files:
        return ToolResult(f"Report finished but no markdown found for {code}.")
    md_path = md_files[-1]
    body = md_path.read_text(encoding="utf-8", errors="replace")
    # Extract the executive summary (sections 一 + 八)
    import re
    summary_parts = []
    for sect in (r"## 一、综合评级.*?(?=## 二)", r"## 八、操作建议.*?(?=---|\Z)"):
        m = re.search(sect, body, re.DOTALL)
        if m:
            summary_parts.append(m.group(0).strip())
    summary = "\n\n".join(summary_parts) or body[:1500]
    return ToolResult(
        f"Report written to {md_path}.\n\nExec summary:\n{summary}",
        side_effect={"md_path": str(md_path)},
    )


def _tool_news_collect(sources: str = "kuaixun,longhu,sinafinance",
                       limit: int = 200,
                       code: Optional[str] = None) -> ToolResult:
    """Fetch fresh news from upstream sources into the local DB.

    ``sources`` is comma-separated. Public ones (no cookie): kuaixun /
    longhu / sinafinance / shareholders. Cookie-required (need Chrome
    extension): xueqiu-comments (needs code) / xueqiu-hot / xueqiu-earnings.
    """
    cmd = ["financial-analyst", "news-collect",
           "--sources", sources, "--limit", str(limit)]
    if code:
        cmd += ["--code", code]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=300)
    except subprocess.TimeoutExpired:
        return ToolResult("news-collect timed out (5 min limit).", is_error=True)
    if proc.returncode != 0:
        return ToolResult(f"news-collect failed: {proc.stderr[-500:]}", is_error=True)
    return ToolResult(proc.stdout[-2000:])


def _tool_news_query(code: Optional[str] = None, days: int = 7,
                     fts: Optional[str] = None, limit: int = 10) -> ToolResult:
    """Query the local news DB. Either by stock code, by FTS keyword, or both."""
    from financial_analyst.data.news_db import NewsDB
    db = NewsDB()
    if fts:
        rows = db.search_news(fts, limit=limit)
    else:
        rows = db.query_news(code=code, since_days=days, limit=limit)
    db.close()
    if not rows:
        return ToolResult(
            f"No news matching code={code!r} days={days} fts={fts!r}. "
            f"Database may be empty for this filter — run `news_collect` first to refresh."
        )
    lines = [f"Found {len(rows)} news entries:"]
    for r in rows:
        ts = r.get("ts", "")
        title = (r.get("title") or "")[:80]
        src = r.get("source", "")
        content = (r.get("content") or "")[:200]
        lines.append(f"  [{ts}] {src}  {title}")
        if content:
            lines.append(f"    {content}")
    return ToolResult("\n".join(lines))


def _tool_alpha_bench(universe: str = "csi300_active",
                      since: str = "2024-06-01",
                      until: str = "2024-12-31",
                      top: int = 15, save: bool = True) -> ToolResult:
    """Run the alpha-zoo benchmark on a universe. Slow (~3 min on csi300)."""
    cmd = ["financial-analyst", "alpha", "bench",
           "--universe", universe, "--since", since, "--until", until,
           "--top", str(top)]
    if save:
        cmd.append("--save")
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=900)
    except subprocess.TimeoutExpired:
        return ToolResult("alpha bench timed out.", is_error=True)
    if proc.returncode != 0:
        return ToolResult(f"alpha bench failed: {proc.stderr[-500:]}", is_error=True)
    # Return the top-K table from stdout
    out = proc.stdout
    # Truncate intermediate progress lines
    if "Alpha Bench" in out:
        out = out[out.find("Alpha Bench"):]
    return ToolResult(out[-3000:])


def _tool_alpha_snapshot(universe: str = "csi300_active",
                         asof: str = "2024-12-31",
                         top_n: int = 20) -> ToolResult:
    """Build a top-N alpha snapshot using the latest cached bench. Fast (<2 min)."""
    cmd = ["financial-analyst", "alpha", "snapshot", "auto",
           "--universe", universe, "--until", asof, "--top-n", str(top_n)]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=300)
    except subprocess.TimeoutExpired:
        return ToolResult("alpha snapshot timed out.", is_error=True)
    if proc.returncode != 0:
        return ToolResult(f"alpha snapshot failed: {proc.stderr[-500:]}", is_error=True)
    return ToolResult(proc.stdout[-2000:])


def _tool_chain_for(code: str) -> ToolResult:
    """Look up which industry-chain products a stock belongs to."""
    from financial_analyst.data.loaders.chain_kb import ChainKBLoader
    loader = ChainKBLoader()
    ctx = loader.chain_context(code, max_peers=6)
    if ctx is None:
        return ToolResult(f"Stock {code} not in any chain product.")
    pp = ctx["primary_product"]
    lines = [
        f"{code} → primary product: {pp['id']} ({pp['display_name']})",
        f"  Chain: {pp['category']} layer={pp['layer']}",
        f"  Role: {pp['role_for_stock']} weight={pp['weight_for_stock']:+.2f}",
        f"  Summary: {pp['summary']}",
        f"  Upstream:   {ctx['upstream_products']}",
        f"  Downstream: {ctx['downstream_products']}",
        f"  Peers ({len(ctx['peer_codes'])}):",
    ]
    for r in ctx["peer_codes"]:
        lines.append(f"    {r['code']} {r['name']} ({r['role']}, w={r['weight']:+.2f})")
    if ctx["catalyst_md"]:
        lines.append(f"\n  Catalyst (excerpt):\n{ctx['catalyst_md'][:500]}")
    return ToolResult("\n".join(lines))


def _tool_stocks_show(code: str, tail: int = 2000) -> ToolResult:
    """Show the per-stock research timeline (your prior notes for this code)."""
    from financial_analyst.data.loaders.stock_timeline import StockTimelineLoader
    loader = StockTimelineLoader()
    text = loader.load_tail(code, max_chars=tail)
    if text is None:
        return ToolResult(f"No timeline file for {code}.")
    return ToolResult(text)


def _tool_industry_show(code: str) -> ToolResult:
    """Look up the Shenwan industry classification for a stock."""
    from financial_analyst.data.loaders.industry import IndustryLoader
    loader = IndustryLoader()
    industry = loader.get(code)
    return ToolResult(f"{code}: {industry}")


def _tool_dream_review() -> ToolResult:
    """List pending memory proposals from the dream loop."""
    import yaml
    proposed_root = Path("memories") / "_proposed"
    if not proposed_root.exists():
        return ToolResult("No pending proposals. Run `dream run` to generate some.")
    files = sorted(proposed_root.rglob("*.md"))
    if not files:
        return ToolResult("_proposed/ is empty.")
    lines = [f"{len(files)} pending proposals:"]
    for f in files:
        try:
            text = f.read_text(encoding="utf-8")
            fm = {}
            if text.startswith("---\n"):
                end = text.find("\n---\n", 4)
                if end > 0:
                    fm = yaml.safe_load(text[4:end]) or {}
            agent = f.parent.name
            slug = f.stem.split("_", 1)[1] if "_" in f.stem else f.stem
            lines.append(
                f"  [{fm.get('confidence', '?')}] {agent}/{slug}  ({len(fm.get('supporting_cases', []))} cases)"
            )
            lines.append(f"    title: {fm.get('title', '')}")
        except Exception:
            lines.append(f"  err: {f}")
    return ToolResult("\n".join(lines))


def _tool_mainline() -> ToolResult:
    """Run monthly industry-chain mainline radar (5-state per chain)."""
    try:
        proc = subprocess.run(
            ["financial-analyst", "mainline"],
            capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=600,
        )
    except subprocess.TimeoutExpired:
        return ToolResult("mainline timed out.", is_error=True)
    if proc.returncode != 0:
        return ToolResult(f"mainline failed: {proc.stderr[-500:]}", is_error=True)
    return ToolResult(proc.stdout[-3000:])


def _tool_brief() -> ToolResult:
    """Pre-market morning brief (overnight moves + day's watchlist)."""
    try:
        proc = subprocess.run(
            ["financial-analyst", "brief"],
            capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=600,
        )
    except subprocess.TimeoutExpired:
        return ToolResult("brief timed out.", is_error=True)
    if proc.returncode != 0:
        return ToolResult(f"brief failed: {proc.stderr[-500:]}", is_error=True)
    return ToolResult(proc.stdout[-3000:])


def _tool_ask_quote(code: str) -> ToolResult:
    """Quick price/PE/PB lookup for a stock (no full report)."""
    from financial_analyst.data.loader_factory import get_default_loader
    import pandas as pd
    loader = get_default_loader()
    today = pd.Timestamp.now().strftime("%Y-%m-%d")
    start = (pd.Timestamp.now() - pd.Timedelta(days=10)).strftime("%Y-%m-%d")
    df = loader.fetch_quote(code, start, today)
    if df is None or df.empty:
        return ToolResult(f"No quote data for {code}.", is_error=True)
    last = df.iloc[-1]
    db = loader.fetch_daily_basic(code, start, today)
    line = (
        f"{code}: close={last.get('close', 'N/A'):.2f} "
        f"vol={last.get('vol', last.get('volume', 0)):,.0f}"
    )
    if db is not None and not db.empty:
        b = db.iloc[-1]
        line += (
            f" | PE={b.get('pe_ttm', 'N/A')} PB={b.get('pb', 'N/A')} "
            f"mv={b.get('total_mv', 0)/10000:.0f}亿 turnover={b.get('turnover_rate', 0):.2f}%"
        )
    return ToolResult(line)


def _tool_alpha_list(family: Optional[str] = None) -> ToolResult:
    """List registered alphas (or filter by family: alpha101 / gtja191 / qlib158)."""
    from financial_analyst.factors.zoo import list_alphas
    rows = list_alphas(family=family)
    lines = [f"{len(rows)} alphas registered ({family or 'all'}):"]
    for a in rows[:50]:
        lines.append(f"  {a.name:18s} [{a.family}] {a.description[:80]}")
    if len(rows) > 50:
        lines.append(f"  ... ({len(rows) - 50} more)")
    return ToolResult("\n".join(lines))


def _tool_alpha_show(name: str) -> ToolResult:
    """Show one alpha's formula + paper citation + description."""
    from financial_analyst.factors.zoo.registry import get
    try:
        spec = get(name)
    except KeyError as e:
        return ToolResult(str(e), is_error=True)
    return ToolResult(
        f"{spec.name} ({spec.family})\n"
        f"Paper: {spec.paper}\n"
        f"Description: {spec.description}\n"
        f"Formula: {spec.formula_text}"
    )


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


TOOL_REGISTRY: List[Tool] = [
    Tool(
        name="run_report",
        description=(
            "Run a complete single-stock deep-dive research report (中文研报). "
            "Takes 5-8 minutes. Outputs star rating, target price, stop loss, "
            "position size, bull/bear arguments, and risk officer review. "
            "Use this when the user asks for a deep analysis / 研报 / full report. "
            "DO NOT use this if the user just wants a quick price quote."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "Stock code in Qlib format, e.g. SH600519 / SZ002594 / BJ430489."},
                "asof": {"type": "string", "description": "As-of date YYYY-MM-DD (default: today). Use the most recent trading day for current analysis."},
            },
            "required": ["code"],
        },
        run=_tool_report,
        cost_hint="minutes",
        confirm_required=True,
    ),
    Tool(
        name="quote_lookup",
        description=(
            "Quick price / PE / PB / market cap / turnover lookup for one stock. "
            "Instant. Use for 看下 X 现在多少钱 / PE 多少 / 市值 type quick questions."
        ),
        input_schema={
            "type": "object",
            "properties": {"code": {"type": "string"}},
            "required": ["code"],
        },
        run=_tool_ask_quote,
    ),
    Tool(
        name="news_query",
        description=(
            "Query the local news database (SQLite + FTS5). "
            "Can filter by stock code, by FTS keyword, or both. "
            "Use when the user asks about recent news for a stock or theme. "
            "If results are empty, call `news_collect` FIRST to refresh the DB, "
            "THEN query again."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "Optional stock code filter (omit for market-wide)"},
                "days": {"type": "integer", "default": 7},
                "fts": {"type": "string", "description": "Optional full-text-search keyword (Chinese supported)"},
                "limit": {"type": "integer", "default": 10},
            },
        },
        run=_tool_news_query,
    ),
    Tool(
        name="news_collect",
        description=(
            "Pull fresh news from upstream into the local DB. "
            "Use when `news_query` returns empty, or when user explicitly "
            "asks for 最新 / 今日 / 今天 news, or wants 雪球 sentiment. "
            "Sources: kuaixun (东方财富快讯) / longhu (龙虎榜) / sinafinance / "
            "shareholders / xueqiu-comments (per-stock retail sentiment, needs code) / "
            "xueqiu-hot (hot-stock board) / xueqiu-earnings. Takes 30-120s."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "sources": {
                    "type": "string",
                    "description": "Comma-separated source list. Public (no auth): kuaixun,longhu,sinafinance,shareholders. Cookie-mode (needs Chrome ext): xueqiu-comments,xueqiu-hot,xueqiu-earnings.",
                    "default": "kuaixun,longhu,sinafinance",
                },
                "limit": {"type": "integer", "default": 200, "description": "Max items per source"},
                "code": {"type": "string", "description": "Required only for xueqiu-comments (per-stock)."},
            },
        },
        run=_tool_news_collect,
        cost_hint="seconds",
    ),
    Tool(
        name="alpha_bench",
        description=(
            "Run the full 442-alpha bench against a universe over a date range. "
            "Outputs top-N alphas by |rank_IR|. Takes ~3 minutes on csi300. "
            "Use when the user wants to find the strongest alphas right now."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "universe": {"type": "string", "default": "csi300_active"},
                "since": {"type": "string", "default": "2024-06-01"},
                "until": {"type": "string", "default": "2024-12-31"},
                "top": {"type": "integer", "default": 15},
            },
        },
        run=_tool_alpha_bench,
        cost_hint="minutes",
        confirm_required=True,
    ),
    Tool(
        name="alpha_snapshot",
        description=(
            "Build a top-N alpha snapshot for a universe using the latest "
            "cached bench. Reports will automatically use this snapshot. "
            "Use when the user wants to refresh the alpha signals before "
            "running reports."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "universe": {"type": "string", "default": "csi300_active"},
                "asof": {"type": "string", "default": "2024-12-31"},
                "top_n": {"type": "integer", "default": 20},
            },
        },
        run=_tool_alpha_snapshot,
        cost_hint="seconds",
    ),
    Tool(
        name="alpha_list",
        description="List all registered alphas (or filter by family: alpha101 / gtja191 / qlib158).",
        input_schema={
            "type": "object",
            "properties": {"family": {"type": "string", "enum": ["alpha101", "gtja191", "qlib158", None]}},
        },
        run=_tool_alpha_list,
    ),
    Tool(
        name="alpha_show",
        description="Show one alpha's formula + paper citation + description.",
        input_schema={
            "type": "object",
            "properties": {"name": {"type": "string", "description": "Alpha name, e.g. gtja089, alpha040, qlib_VSTD60"}},
            "required": ["name"],
        },
        run=_tool_alpha_show,
    ),
    Tool(
        name="chain_for",
        description=(
            "Look up which industry-chain product a stock belongs to. "
            "Returns the primary product (anchor / data_supported / llm_inferred role), "
            "upstream / downstream products, peer stocks, and chain catalyst. "
            "Use when the user asks about 产业链 / 上下游 / 同行."
        ),
        input_schema={
            "type": "object",
            "properties": {"code": {"type": "string"}},
            "required": ["code"],
        },
        run=_tool_chain_for,
    ),
    Tool(
        name="stocks_show",
        description=(
            "Show the user's prior research timeline for a stock (markdown). "
            "Use when the user asks 之前怎么看 X / 上次 X 评级 / X 历史研报."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "code": {"type": "string"},
                "tail": {"type": "integer", "default": 2000},
            },
            "required": ["code"],
        },
        run=_tool_stocks_show,
    ),
    Tool(
        name="industry_show",
        description="Look up the Shenwan industry classification for a stock (e.g. 白酒 / 银行 / 电气设备).",
        input_schema={
            "type": "object",
            "properties": {"code": {"type": "string"}},
            "required": ["code"],
        },
        run=_tool_industry_show,
    ),
    Tool(
        name="dream_review",
        description="List pending memory proposals from the introspection loop. Use when the user asks 反思 / 教训 / proposal.",
        input_schema={"type": "object", "properties": {}},
        run=_tool_dream_review,
    ),
    Tool(
        name="mainline_radar",
        description=(
            "Run monthly industry-chain mainline radar: classifies each chain "
            "into mainline / initiation / revival / decay / cold. "
            "Use when the user asks which sectors are leading / 主线 / 板块轮动."
        ),
        input_schema={"type": "object", "properties": {}},
        run=_tool_mainline,
        cost_hint="seconds",
    ),
    Tool(
        name="morning_brief",
        description="Generate the pre-market morning brief (overnight moves + day's watchlist).",
        input_schema={"type": "object", "properties": {}},
        run=_tool_brief,
        cost_hint="seconds",
    ),
]


def get_tool(name: str) -> Optional[Tool]:
    for t in TOOL_REGISTRY:
        if t.name == name:
            return t
    return None


def list_tools() -> List[Tool]:
    return list(TOOL_REGISTRY)
