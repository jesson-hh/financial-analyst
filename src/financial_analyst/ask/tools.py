"""Tools the ask-agent can call. Each is a plain function with simple inputs/outputs.

Design: keep these CHEAP (no LLM calls inside). The ask-agent does ONE LLM call,
selects a tool, gets data, then synthesizes the answer in a SECOND LLM call.
For heavyweight requests (full stock report), tool returns a marker that the CLI
dispatches to the full DAG separately.
"""
from __future__ import annotations
import json
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional
import pandas as pd


def list_past_reports(out_dir: Path = Path("out"), limit: int = 10) -> List[Dict[str, Any]]:
    """List recent report files in out/. Returns code/date/path/has_html."""
    out_dir = Path(out_dir)
    if not out_dir.exists():
        return []
    md_files = sorted(out_dir.glob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True)
    results: List[Dict[str, Any]] = []
    for f in md_files[:limit]:
        stem = f.stem
        try:
            code, asof = stem.rsplit("_", 1)
        except ValueError:
            continue
        json_path = f.with_suffix(".json")
        html_path = f.with_suffix(".html")
        record = {
            "code": code,
            "asof": asof,
            "md_path": str(f),
            "has_json": json_path.exists(),
            "has_html": html_path.exists(),
        }
        # Pull a few key fields from JSON if available
        if json_path.exists():
            try:
                data = json.loads(json_path.read_text(encoding="utf-8"))
                for k in ("rating_overall", "action", "target_price", "stop_loss", "position_pct"):
                    if k in data:
                        record[k] = data[k]
            except Exception:
                pass
        results.append(record)
    return results


def read_past_report(code: str, date_str: Optional[str] = None,
                     out_dir: Path = Path("out")) -> Optional[str]:
    """Read a past report's markdown. If date_str omitted, returns the most recent one for that code."""
    out_dir = Path(out_dir)
    if not out_dir.exists():
        return None
    code = code.upper()
    if date_str:
        path = out_dir / f"{code}_{date_str}.md"
        return path.read_text(encoding="utf-8") if path.exists() else None
    # Find most recent for this code
    candidates = sorted(out_dir.glob(f"{code}_*.md"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not candidates:
        return None
    return candidates[0].read_text(encoding="utf-8")


def search_memory(query: str, agent: Optional[str] = None, top_k: int = 5,
                  memory_root: Path = Path("memories"),
                  cache_dir: Optional[Path] = None) -> List[Dict[str, Any]]:
    """FTS5 search across memories. Returns top-K hits with agent/file/snippet."""
    from financial_analyst.agent.memory_index import MemoryIndex
    from financial_analyst.settings import Settings

    if cache_dir is None:
        cache_dir = Path(Settings().cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    idx = MemoryIndex(memory_root=memory_root, db_path=cache_dir / "memory.fts5.db")
    try:
        idx.update_changed()
    except Exception:
        pass
    return [
        {
            "agent": h["agent"],
            "filename": h["filename"],
            "snippet": h["content"][:240],
        }
        for h in idx.search(query, agent=agent, top_k=top_k)
    ]


def quick_quote(code: str, asof: Optional[str] = None) -> Dict[str, Any]:
    """Return latest quote info for a stock (no LLM, no model). Fast."""
    from financial_analyst.data.loader_factory import get_default_loader
    loader = get_default_loader()
    asof = asof or date.today().isoformat()
    start = (pd.Timestamp(asof) - pd.Timedelta(days=40)).strftime("%Y-%m-%d")
    df = loader.fetch_quote(code, start, asof)
    if df is None or df.empty:
        return {"code": code, "error": "no data"}
    last = df.iloc[-1]
    out = {
        "code": code,
        "asof": str(last["trade_date"])[:10],
        "open": float(last["open"]),
        "high": float(last["high"]),
        "low": float(last["low"]),
        "close": float(last["close"]),
        "vol": float(last["vol"]),
    }
    if len(df) >= 6:
        out["ret_5d"] = float(df["close"].iloc[-1] / df["close"].iloc[-6] - 1)
    if len(df) >= 21:
        out["ret_20d"] = float(df["close"].iloc[-1] / df["close"].iloc[-21] - 1)
    # daily_basic if available
    db = loader.fetch_daily_basic(code, start, asof)
    if db is not None and not db.empty:
        last_db = db.iloc[-1]
        for key in ("pe_ttm", "pb", "total_mv", "turnover_rate"):
            val = last_db.get(key) if hasattr(last_db, "get") else None
            try:
                if val is not None and pd.notna(val):
                    out[key] = float(val)
            except Exception:
                pass
    return out


def quick_factors(code: str, asof: Optional[str] = None) -> Dict[str, Any]:
    """Compute 34 factors for a stock (no LLM analysis). Wraps factors/core.py."""
    from financial_analyst.data.loader_factory import get_default_loader
    from financial_analyst.factors.core import compute_factors
    loader = get_default_loader()
    asof = asof or date.today().isoformat()
    start = (pd.Timestamp(asof) - pd.Timedelta(days=180)).strftime("%Y-%m-%d")
    df = loader.fetch_quote(code, start, asof)
    if df is None or df.empty:
        return {"code": code, "error": "no data"}
    factors = compute_factors(df)
    # Sanitize NaN
    return {
        "code": code,
        "asof": asof,
        "factors": {k: (float(v) if v == v else None) for k, v in factors.items()},
    }


def list_dream_proposals(memory_root: Path = Path("memories")) -> List[Dict[str, Any]]:
    """List staged dream proposals in memories/_proposed/<agent>/."""
    proposed = Path(memory_root) / "_proposed"
    if not proposed.exists():
        return []
    import yaml as _yaml
    out: List[Dict[str, Any]] = []
    for agent_dir in sorted(proposed.iterdir()):
        if not agent_dir.is_dir():
            continue
        for f in sorted(agent_dir.glob("*.md")):
            text = f.read_text(encoding="utf-8")
            fm: Dict[str, Any] = {}
            if text.startswith("---"):
                end = text.find("---", 3)
                if end > 0:
                    try:
                        fm = _yaml.safe_load(text[3:end]) or {}
                    except Exception:
                        fm = {}
            out.append({
                "agent": agent_dir.name,
                "file": f.name,
                "path": str(f.relative_to(memory_root)),
                "title": fm.get("title", ""),
                "confidence": fm.get("confidence", "?"),
            })
    return out


# Mapping for tool-call dispatch
TOOLS = {
    "list_past_reports": list_past_reports,
    "read_past_report": read_past_report,
    "search_memory": search_memory,
    "quick_quote": quick_quote,
    "quick_factors": quick_factors,
    "list_dream_proposals": list_dream_proposals,
}


# Tool schema for OpenAI-compatible function calling
TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "list_past_reports",
            "description": "List recent stock research reports under out/. Use when user asks about past work.",
            "parameters": {"type": "object", "properties": {"limit": {"type": "integer", "default": 10}}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_past_report",
            "description": "Read the markdown content of a past report. Use when user asks 'what did the report say about X' or 'why did we recommend buying Y'.",
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {"type": "string", "description": "Stock code like SH600519"},
                    "date_str": {"type": "string", "description": "Optional YYYY-MM-DD; omit for latest"},
                },
                "required": ["code"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_memory",
            "description": "FTS5 search across agent memories (pitfalls, factor insights, rating system, V1-V10 playbook). Use when user asks about rules / strategies / past lessons.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "agent": {"type": "string", "description": "Optional filter to one agent's memory"},
                    "top_k": {"type": "integer", "default": 5},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "quick_quote",
            "description": "Fetch latest quote (OHLCV + PE/PB/MV/turnover) for a stock. Fast, no LLM. Use when user asks current price / valuation.",
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {"type": "string"},
                    "asof": {"type": "string", "description": "Optional YYYY-MM-DD"},
                },
                "required": ["code"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "quick_factors",
            "description": "Compute 34 daily factors for a stock without running the full report. Use when user asks specifically about factor values.",
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {"type": "string"},
                    "asof": {"type": "string"},
                },
                "required": ["code"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_dream_proposals",
            "description": "List staged memory proposals from /dream. Use when user asks 'what did dream suggest' or 'any pending proposals'.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
]
