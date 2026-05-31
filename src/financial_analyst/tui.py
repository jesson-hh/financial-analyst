"""TUI module — Rich TUI + prompt-toolkit REPL with intent parsing (Task 32)."""
from __future__ import annotations

import asyncio
import json
import re
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import List, Union

# Force UTF-8 stdout/stderr on Windows so Chinese chars, ¥, and emoji render
# (GBK default on zh-CN PowerShell chokes on many of them).
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from rich.console import Console
from rich.live import Live
from rich.markdown import Markdown
from rich.panel import Panel
from rich.rule import Rule
from rich.table import Table

console = Console()

from financial_analyst.sessions import SessionManager, SessionEvent
from financial_analyst.memory_paths import default_memory_root

session_mgr = SessionManager()   # auto-creates "default" session


def _active_n_messages() -> int:
    """Return n_messages for the currently active session."""
    for m in session_mgr.list():
        if m.name == session_mgr.active_name:
            return m.n_messages
    return 0


# ---------------------------------------------------------------------------
# Report renderer — print to terminal + export HTML
# ---------------------------------------------------------------------------

def render_report(md_path: Path) -> Path:
    """Render a markdown report to the terminal (Rich Markdown) AND export an
    HTML copy next to the .md file with the same colored output.

    Returns the path to the generated HTML file.
    """
    md_text = md_path.read_text(encoding="utf-8")

    # 1) Print to live terminal so the user sees the report immediately
    console.print(Rule(f"Report: {md_path.name}", style="cyan"))
    console.print(Markdown(md_text))
    console.print(Rule(style="cyan"))

    # 2) Re-render into a recording console to capture HTML with colors
    record_console = Console(record=True, width=120)
    record_console.print(Markdown(md_text))
    html_path = md_path.with_suffix(".html")
    record_console.save_html(str(html_path), inline_styles=True)
    return html_path


def _to_file_url(path: Path) -> str:
    """Convert a Windows/POSIX path into a clickable file:/// URL."""
    abs_path = path.resolve()
    return f"file:///{str(abs_path).replace(chr(92), '/').lstrip('/')}"

# ---------------------------------------------------------------------------
# Intent dataclasses
# ---------------------------------------------------------------------------

@dataclass
class IntentReport:
    code: str


@dataclass
class IntentSlashCmd:
    command: str
    args: List[str]


@dataclass
class IntentChat:
    text: str


@dataclass
class IntentQuit:
    pass


Intent = Union[IntentReport, IntentSlashCmd, IntentChat, IntentQuit]

# ---------------------------------------------------------------------------
# Code normalisation
# ---------------------------------------------------------------------------

CODE_RE = re.compile(r"^(SH|SZ|BJ)?(\d{6})$", re.IGNORECASE)
LOOK_PREFIX = ("看看", "看下", "分析", "瞧瞧")


def normalize_code(raw: str) -> str:
    """Normalise a raw stock-code string to Qlib upper-case format."""
    raw = raw.strip().upper().replace(".", "")
    m = CODE_RE.match(raw)
    if not m:
        return raw
    prefix, body = m.group(1), m.group(2)
    if prefix:
        return f"{prefix}{body}"
    if body.startswith(("60", "68", "9")):
        return f"SH{body}"
    if body.startswith(("00", "30", "20")):
        return f"SZ{body}"
    if body.startswith("8"):
        return f"BJ{body}"
    return f"SH{body}"


# ---------------------------------------------------------------------------
# Intent parsing
# ---------------------------------------------------------------------------

def parse_input(text: str) -> Intent:
    """Parse a user-typed string into a typed Intent."""
    t = text.strip()
    if not t or t.lower() in {"/quit", "/exit", "exit", "quit"}:
        return IntentQuit()

    # Slash commands
    if t.startswith("/"):
        parts = t[1:].split()
        cmd, args = parts[0], parts[1:]
        if cmd == "report" and args:
            return IntentReport(code=normalize_code(args[0]))
        return IntentSlashCmd(command=cmd, args=args)

    # Natural-language look prefixes  ("看看 600519", "分析 SH600519")
    for p in LOOK_PREFIX:
        if t.startswith(p):
            tail = t[len(p):].strip()
            tok = tail.split()[0] if tail else ""
            if CODE_RE.match(tok.upper().replace(".", "")):
                return IntentReport(code=normalize_code(tok))

    # Bare stock code
    if CODE_RE.match(t.upper().replace(".", "")):
        return IntentReport(code=normalize_code(t))

    return IntentChat(text=t)


# ---------------------------------------------------------------------------
# Rich banner
# ---------------------------------------------------------------------------

def render_banner() -> None:
    from financial_analyst import __version__
    from financial_analyst.agent.registry import SubAgentRegistry
    from financial_analyst.llm.client import load_llm_config

    cfg = load_llm_config()
    panel = Panel(
        f"[bold cyan]Financial Analyst v{__version__}[/bold cyan]\n"
        f"Provider: {cfg['default_provider']} / {cfg['default_model']}\n"
        f"Sub-agents registered: {len(SubAgentRegistry.names())}\n"
        f"Active session: [bold]{session_mgr.active_name}[/bold] "
        f"({_active_n_messages()} past events)\n\n"
        "[dim]Commands: /report <code> · /memory · /sessions · /provider · /agents · /show · /quit\n"
        "Natural language: '看看 600519'[/dim]",
        title="Welcome",
        border_style="cyan",
    )
    console.print(panel)


# ---------------------------------------------------------------------------
# Agent registration
# ---------------------------------------------------------------------------

def _ensure_registered() -> None:
    """Register all 24 sub-agents into SubAgentRegistry (idempotent).

    14 single-stock (stock-deep-dive) + 5 market + 2 mainline + ...
    v1.9.7: +5 international/morning-brief agents (overseas-market-scanner,
    global-news-aggregator, macro-impact-analyzer, catalyst-extractor,
    sector-rotation-analyzer).
    """
    from financial_analyst.agent.registry import SubAgentRegistry

    from financial_analyst.agent.tier1.quote_fetcher import QuoteFetcher
    from financial_analyst.agent.tier1.factor_computer import FactorComputer
    from financial_analyst.agent.tier1.model_predictor import ModelPredictor
    from financial_analyst.agent.tier1.news_reader import NewsReader
    from financial_analyst.agent.tier1.f10_reader import F10Reader
    from financial_analyst.agent.tier2.fundamental_analyst import FundamentalAnalyst
    from financial_analyst.agent.tier2.technical_analyst import TechnicalAnalyst
    from financial_analyst.agent.tier2.whale_analyst import WhaleAnalyst
    from financial_analyst.agent.tier2.quant_analyst import QuantAnalyst
    from financial_analyst.agent.tier3.bull_advocate import BullAdvocate
    from financial_analyst.agent.tier3.bear_advocate import BearAdvocate
    from financial_analyst.agent.tier3.risk_officer import RiskOfficer
    from financial_analyst.agent.tier3.report_writer import ReportWriter
    from financial_analyst.agent.tier3.introspector import Introspector
    from financial_analyst.agent.mainline.mainline_classifier import MainlineClassifier
    from financial_analyst.agent.mainline.mainline_writer import MainlineWriter
    from financial_analyst.agent.market.market_scanner import MarketScanner
    from financial_analyst.agent.market.morning_brief_writer import MorningBriefWriter
    from financial_analyst.agent.market.intraday_reviewer import IntradayReviewer
    from financial_analyst.agent.market.overseas_market_scanner import OverseasMarketScanner
    from financial_analyst.agent.market.catalyst_extractor import CatalystExtractor
    from financial_analyst.agent.market.sector_rotation_analyzer import SectorRotationAnalyzer
    from financial_analyst.agent.market.global_news_aggregator import GlobalNewsAggregator
    from financial_analyst.agent.market.macro_impact_analyzer import MacroImpactAnalyzer
    from financial_analyst.agent.etf.quote_fetcher import EtfQuoteFetcher
    from financial_analyst.agent.etf.metrics_fetcher import EtfMetricsFetcher
    from financial_analyst.agent.etf.holdings_analyst import EtfHoldingsAnalyst
    from financial_analyst.agent.etf.technical_analyst import EtfTechnicalAnalyst
    from financial_analyst.agent.etf.flow_analyst import EtfFlowAnalyst
    from financial_analyst.agent.etf.valuation_analyst import EtfValuationAnalyst
    from financial_analyst.agent.etf.bull_advocate import EtfBullAdvocate
    from financial_analyst.agent.etf.bear_advocate import EtfBearAdvocate

    for name, cls in [
        ("quote-fetcher", QuoteFetcher),
        ("factor-computer", FactorComputer),
        ("model-predictor", ModelPredictor),
        ("news-reader", NewsReader),
        ("f10-reader", F10Reader),
        ("fundamental-analyst", FundamentalAnalyst),
        ("technical-analyst", TechnicalAnalyst),
        ("whale-analyst", WhaleAnalyst),
        ("quant-analyst", QuantAnalyst),
        ("bull-advocate", BullAdvocate),
        ("bear-advocate", BearAdvocate),
        ("risk-officer", RiskOfficer),
        ("report-writer", ReportWriter),
        ("introspector", Introspector),
        ("mainline-classifier", MainlineClassifier),
        ("mainline-writer", MainlineWriter),
        ("market-scanner", MarketScanner),
        ("morning-brief-writer", MorningBriefWriter),
        ("intraday-reviewer", IntradayReviewer),
        ("overseas-market-scanner", OverseasMarketScanner),
        ("catalyst-extractor", CatalystExtractor),
        ("sector-rotation-analyzer", SectorRotationAnalyzer),
        ("global-news-aggregator", GlobalNewsAggregator),
        ("macro-impact-analyzer", MacroImpactAnalyzer),
        ("etf-quote-fetcher", EtfQuoteFetcher),
        ("etf-metrics-fetcher", EtfMetricsFetcher),
        ("etf-holdings-analyst", EtfHoldingsAnalyst),
        ("etf-technical-analyst", EtfTechnicalAnalyst),
        ("etf-flow-analyst", EtfFlowAnalyst),
        ("etf-valuation-analyst", EtfValuationAnalyst),
        ("etf-bull-advocate", EtfBullAdvocate),
        ("etf-bear-advocate", EtfBearAdvocate),
    ]:
        if name not in SubAgentRegistry.names():
            SubAgentRegistry.register(name, cls)


# ---------------------------------------------------------------------------
# Slash-command handler
# ---------------------------------------------------------------------------

async def handle_slash(cmd: str, args: List[str]) -> None:
    from financial_analyst.agent.registry import SubAgentRegistry

    if cmd == "agents":
        tbl = Table(title="Registered Sub-Agents")
        tbl.add_column("Name")
        tbl.add_column("Tier")
        for name in SubAgentRegistry.names():
            if any(s in name for s in ["fetcher", "reader", "computer", "predictor"]):
                tier = "1"
            elif "analyst" in name:
                tier = "2"
            elif "mainline" in name:
                tier = "M"
            else:
                tier = "3"
            tbl.add_row(name, tier)
        console.print(tbl)

    elif cmd == "memory":
        await handle_memory_cmd(args)

    elif cmd == "show":
        out_dir = Path("./out")
        files = sorted(out_dir.glob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True)
        if not files:
            console.print("[yellow]no reports yet[/yellow]")
        else:
            html_path = render_report(files[0])
            console.print(f"[dim]html: [link={_to_file_url(html_path)}]{_to_file_url(html_path)}[/link][/dim]")

    elif cmd == "dream":
        # parse args: /dream --since 30 --dry-run
        since = 30
        dry_run = False
        out_dir_arg = Path("out")
        for a in args:
            if a.startswith("--since="):
                try:
                    since = int(a.split("=", 1)[1])
                except ValueError:
                    pass
            elif a == "--dry-run":
                dry_run = True
        from financial_analyst.cli import _run_dream
        await _run_dream(since=since, dry_run=dry_run, out_dir=out_dir_arg)

    elif cmd == "ask":
        query = " ".join(args).strip()
        if not query:
            console.print("[dim]usage: /ask <question>[/dim]")
            return
        from financial_analyst.ask import ask
        output = await ask(query)
        from rich.markdown import Markdown
        console.print(Markdown(output.answer or "(no answer)"))
        if output.actions_taken:
            console.print(f"\n[dim]actions: {output.actions_taken}[/dim]")
        if output.needs_full_report and output.suggested_code:
            console.print(
                f"\n[yellow]Recommend: /report {output.suggested_code}[/yellow]"
            )

    elif cmd == "mainline":
        # parse args: /mainline [--asof=YYYY-MM-DD] [--panel=/path]
        asof = None
        panel = None
        out_dir = Path("out")
        for a in args:
            if a.startswith("--asof="):
                asof = a.split("=", 1)[1]
            elif a.startswith("--panel="):
                panel = a.split("=", 1)[1]
        from financial_analyst.cli import _run_mainline
        await _run_mainline(asof=asof, panel=panel, out_dir=out_dir)

    elif cmd == "brief":
        asof = None
        universe = "all"
        for a in args:
            if a.startswith("--asof="):
                asof = a.split("=", 1)[1]
            elif a.startswith("--universe="):
                universe = a.split("=", 1)[1]
        from financial_analyst.cli import _run_brief
        await _run_brief(asof=asof, universe=universe, universe_file=None,
                          max_scan=5000, out_dir=Path("out"))

    elif cmd == "intraday":
        codes = ""
        asof = None
        for a in args:
            if a.startswith("--codes="):
                codes = a.split("=", 1)[1]
            elif a.startswith("--asof="):
                asof = a.split("=", 1)[1]
        from financial_analyst.cli import _run_intraday
        await _run_intraday(codes=codes, asof=asof, out_dir=Path("out"))

    elif cmd == "provider":
        console.print(
            "[yellow]provider switching: see config/llm.yaml (live switch TBD in v0.2)[/yellow]"
        )

    elif cmd == "sessions":
        if not args:
            # /sessions — list all
            tbl = Table(title="Sessions")
            tbl.add_column("Name", style="cyan")
            tbl.add_column("Active")
            tbl.add_column("Messages", justify="right")
            tbl.add_column("Last Active")
            for m in session_mgr.list():
                active_marker = "✓" if m.name == session_mgr.active_name else ""
                name_cell = ("* " if m.name == session_mgr.active_name else "  ") + m.name
                tbl.add_row(name_cell, active_marker, str(m.n_messages), m.last_active_at)
            console.print(tbl)
        elif args[0] == "new" and len(args) >= 2:
            try:
                meta = session_mgr.create(args[1])
                session_mgr.switch(args[1])
                console.print(f"[green]created + switched to:[/green] {meta.name}")
            except ValueError as exc:
                console.print(f"[red]{exc}[/red]")
        elif args[0] == "switch" and len(args) >= 2:
            try:
                meta = session_mgr.switch(args[1])
                console.print(f"[green]switched to:[/green] {meta.name}")
            except Exception as exc:
                console.print(f"[red]{exc}[/red]")
        elif args[0] == "delete" and len(args) >= 2:
            try:
                session_mgr.delete(args[1])
                console.print(f"[yellow]deleted:[/yellow] {args[1]}")
            except (ValueError, FileNotFoundError) as exc:
                console.print(f"[red]{exc}[/red]")
        elif args[0] == "show":
            target = args[1] if len(args) >= 2 else session_mgr.active_name
            events = session_mgr.history(name=target, limit=30)
            if not events:
                console.print(f"[yellow]no events in session: {target}[/yellow]")
                return
            tbl = Table(title=f"Session {target} — last {len(events)} events")
            tbl.add_column("Time", style="dim")
            tbl.add_column("Kind", style="cyan")
            tbl.add_column("Input")
            tbl.add_column("Summary")
            for e in events:
                input_short = (e.input[:50] + "...") if len(e.input) > 50 else e.input
                sum_short = (e.output_summary[:50] + "...") if len(e.output_summary) > 50 else e.output_summary
                tbl.add_row(e.ts, e.kind, input_short, sum_short)
            console.print(tbl)
        else:
            console.print(
                "[dim]usage:[/dim]\n"
                "  /sessions               — list all\n"
                "  /sessions new <name>    — create + switch\n"
                "  /sessions switch <name> — switch active\n"
                "  /sessions show [name]   — view history (default: active)\n"
                "  /sessions delete <name> — delete (not 'default')"
            )

    else:
        console.print(f"[red]unknown command: /{cmd}[/red]")


async def handle_memory_cmd(args: List[str]) -> None:
    """Handle /memory subcommands.

    Subcommands:
      list <agent>           — list files in memories/<agent>/
      show <agent>/<file>    — print file content
      edit <agent>/<file>    — open in $EDITOR
      search <query>         — FTS5 search across all memories
      stats                  — show MemoryIndex stats
      diff                   — show recent git log in memories/
      reindex                — force rebuild MemoryIndex
      reload                 — clear in-memory caches (next agent invocation re-reads)
    """
    from financial_analyst.agent.memory_index import MemoryIndex
    from financial_analyst.settings import Settings

    if not args:
        console.print(
            "[dim]usage:[/dim]\n"
            "  /memory list <agent>\n"
            "  /memory show <agent>/<file>\n"
            "  /memory edit <agent>/<file>\n"
            "  /memory search <query>\n"
            "  /memory stats\n"
            "  /memory diff\n"
            "  /memory reindex\n"
            "  /memory reload\n"
            "  /memory list-proposals\n"
            "  /memory accept _proposed/<agent>/<file>.md\n"
            "  /memory reject _proposed/<agent>/<file>.md"
        )
        return

    sub = args[0]
    mem_root = default_memory_root()

    def _get_index() -> MemoryIndex:
        settings = Settings()
        cache_dir = Path(settings.cache_dir)
        cache_dir.mkdir(parents=True, exist_ok=True)
        idx = MemoryIndex(memory_root=mem_root, db_path=cache_dir / "memory.fts5.db")
        idx.update_changed()
        return idx

    if sub == "list" and len(args) >= 2:
        agent_dir = mem_root / args[1]
        if not agent_dir.exists():
            console.print(f"[red]no memory dir: {agent_dir}[/red]")
            return
        for f in sorted(agent_dir.glob("*.md")):
            console.print(f"  {f.name}")

    elif sub == "show" and len(args) >= 2:
        path = mem_root / args[1]
        if not path.exists() or not str(path.resolve()).startswith(str(mem_root.resolve())):
            console.print(f"[red]not found: {path}[/red]")
            return
        from rich.markdown import Markdown
        console.print(Markdown(path.read_text(encoding="utf-8")))

    elif sub == "edit" and len(args) >= 2:
        import os
        import subprocess
        path = mem_root / args[1]
        if not path.exists():
            console.print(f"[red]not found: {path}[/red]")
            return
        editor = os.environ.get("EDITOR")
        if not editor:
            editor = "notepad" if os.name == "nt" else "vi"
        try:
            subprocess.Popen([editor, str(path)])
            console.print(f"[green]opened {path} in {editor}[/green]")
        except FileNotFoundError:
            console.print(f"[red]editor not found: {editor}[/red]")

    elif sub == "search" and len(args) >= 2:
        query = " ".join(args[1:])
        idx = _get_index()
        hits = idx.search(query, top_k=10)
        if not hits:
            console.print(f"[yellow]no matches for {query!r}[/yellow]")
            return
        tbl = Table(title=f"Memory search: {query}")
        tbl.add_column("Agent", style="cyan")
        tbl.add_column("File", style="green")
        tbl.add_column("Snippet")
        for h in hits:
            snippet = h["content"][:120].replace("\n", " ")
            if len(h["content"]) > 120:
                snippet += "…"
            tbl.add_row(h["agent"], h["filename"], snippet)
        console.print(tbl)

    elif sub == "stats":
        idx = _get_index()
        s = idx.stats()
        tbl = Table(title="Memory Stats")
        tbl.add_column("Agent", style="cyan")
        tbl.add_column("Files", justify="right")
        tbl.add_column("Bytes", justify="right")
        for agent, count in sorted(s.get("per_agent", {}).items()):
            agent_bytes = s.get("per_agent_bytes", {}).get(agent, 0)
            tbl.add_row(agent, str(count), str(agent_bytes))
        tbl.add_row("[bold]TOTAL[/bold]", str(s.get("total_files", 0)), str(s.get("total_bytes", 0)))
        console.print(tbl)

    elif sub == "diff":
        import subprocess
        try:
            result = subprocess.run(
                ["git", "log", "--since=7 days ago", "--oneline", "--", "memories/"],
                capture_output=True, text=True, cwd=".",
            )
            if result.returncode != 0:
                console.print(f"[red]git log failed:[/red] {result.stderr.strip()}")
                return
            output = result.stdout.strip()
            if not output:
                console.print("[yellow]no memory changes in last 7 days[/yellow]")
            else:
                console.print(output)
        except FileNotFoundError:
            console.print("[red]git not found[/red]")

    elif sub == "reindex":
        idx = MemoryIndex(memory_root=mem_root, db_path=Path(Settings().cache_dir) / "memory.fts5.db")
        n = idx.rebuild()
        console.print(f"[green]reindexed {n} memory files[/green]")

    elif sub == "reload":
        console.print("[green]memory cache cleared; next agent invocation will reload[/green]")

    elif sub == "list-proposals":
        proposed = mem_root / "_proposed"
        if not proposed.exists():
            console.print("[yellow]no proposals yet — run /dream to generate[/yellow]")
            return
        tbl = Table(title="Memory Proposals (staged)")
        tbl.add_column("Agent", style="cyan")
        tbl.add_column("File", style="green")
        tbl.add_column("Confidence")
        import yaml as _yaml
        n = 0
        for agent_dir in sorted(proposed.iterdir()):
            if not agent_dir.is_dir():
                continue
            for f in sorted(agent_dir.glob("*.md")):
                text = f.read_text(encoding="utf-8")
                conf = "?"
                if text.startswith("---"):
                    end = text.find("---", 3)
                    if end > 0:
                        try:
                            fm = _yaml.safe_load(text[3:end])
                            conf = (fm or {}).get("confidence", "?")
                        except Exception:
                            pass
                tbl.add_row(agent_dir.name, f.name, conf)
                n += 1
        if n == 0:
            console.print("[yellow]no proposals yet — run /dream to generate[/yellow]")
        else:
            console.print(tbl)

    elif sub == "accept" and len(args) >= 2:
        src_rel = args[1]
        if not src_rel.startswith("_proposed/"):
            console.print("[red]accept argument must start with '_proposed/'[/red]")
            return
        src = mem_root / src_rel
        if not src.exists():
            console.print(f"[red]not found: {src}[/red]")
            return
        rel_parts = src_rel.split("/", 2)
        if len(rel_parts) != 3:
            console.print(f"[red]bad path: {src_rel}[/red]")
            return
        dest = mem_root / rel_parts[1] / rel_parts[2]
        dest.parent.mkdir(parents=True, exist_ok=True)
        src.rename(dest)
        console.print(f"[green]accepted -> {dest}[/green]")
        console.print("[dim]Next agent invocation will read it (memory hot-reloads on mtime change)[/dim]")

    elif sub == "reject" and len(args) >= 2:
        src_rel = args[1]
        if not src_rel.startswith("_proposed/"):
            console.print("[red]reject argument must start with '_proposed/'[/red]")
            return
        src = mem_root / src_rel
        if not src.exists():
            console.print(f"[red]not found: {src}[/red]")
            return
        src.unlink()
        console.print(f"[yellow]rejected (deleted): {src_rel}[/yellow]")

    else:
        console.print(f"[red]unknown subcommand: {sub}[/red]")
        console.print(
            "[dim]usage:[/dim]\n"
            "  /memory list <agent> | show <agent>/<file> | edit <agent>/<file>\n"
            "  /memory search <query> | stats | diff | reindex | reload\n"
            "  /memory list-proposals\n"
            "  /memory accept _proposed/<agent>/<file>.md\n"
            "  /memory reject _proposed/<agent>/<file>.md"
        )


# ---------------------------------------------------------------------------
# One-shot report runner (called by CLI + TUI)
# ---------------------------------------------------------------------------

async def run_report_oneshot(code: str, asof, out_dir: Path, trace: bool = False) -> None:
    _ensure_registered()

    from financial_analyst.agent.memory_index import MemoryIndex
    from financial_analyst.agent.orchestrator import Orchestrator
    from financial_analyst.settings import Settings
    from financial_analyst.swarm import load_preset

    asof = asof or date.today().isoformat()

    # Build shared FTS5 index (incremental — cheap if already up-to-date)
    settings = Settings()
    cache_dir = Path(settings.cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    mem_index = MemoryIndex(
        memory_root=default_memory_root(),
        db_path=cache_dir / "memory.fts5.db",
    )
    mem_index.update_changed()

    nodes = load_preset("stock-deep-dive", memory_root=default_memory_root(), memory_index=mem_index)

    status: dict[str, dict] = {
        n.agent.NAME: {"state": "pending", "elapsed": 0.0} for n in nodes
    }

    def make_table() -> Table:
        tbl = Table(title=f"DAG · {code} · {asof}")
        tbl.add_column("Agent")
        tbl.add_column("Status")
        tbl.add_column("Elapsed")
        for n in nodes:
            name = n.agent.NAME
            s = status[name]
            color = {
                "pending": "white",
                "running": "yellow",
                "done": "green",
                "fail": "red",
            }[s["state"]]
            tbl.add_row(name, f"[{color}]{s['state']}[/{color}]", f"{s['elapsed']:.1f}s")
        return tbl

    # Progress state file — GuanLan UI polls this via /report-progress?code=X
    import time as _t
    progress_path = out_dir / f"{code}_progress.json"

    def _write_progress(extra: dict = None) -> None:
        snapshot = {
            "code": code, "asof": asof, "ts": _t.time(),
            "total": len(status),
            "done":    sum(1 for s in status.values() if s["state"] == "done"),
            "fail":    sum(1 for s in status.values() if s["state"] == "fail"),
            "running": sum(1 for s in status.values() if s["state"] == "running"),
            "pending": sum(1 for s in status.values() if s["state"] == "pending"),
            "agents":  {n: {"state": s["state"], "elapsed": s["elapsed"]} for n, s in status.items()},
        }
        if extra:
            snapshot.update(extra)
        try:
            progress_path.write_text(json.dumps(snapshot, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass

    _write_progress({"started": _t.time()})  # initial: all pending

    cancelled = False
    results: dict = {}
    try:
        with Live(make_table(), console=console, refresh_per_second=4) as live:
            def on_event(evt: str, data: dict) -> None:
                if evt == "wave_start":
                    for n in data["agents"]:
                        status[n]["state"] = "running"
                elif evt == "agent_done":
                    status[data["agent"]]["state"] = "done" if data["ok"] else "fail"
                    status[data["agent"]]["elapsed"] = data["elapsed"]
                live.update(make_table())
                _write_progress()  # sync state to file after each event so the front-end can poll

            orch = Orchestrator(nodes, on_event=on_event)
            console.print(f"[bold]Running stock-deep-dive for {code} (asof={asof})…[/bold]")
            results = await orch.run({"code": code, "asof_date": asof, "out_dir": str(out_dir)})
    except (KeyboardInterrupt, asyncio.CancelledError):
        cancelled = True
        console.print("\n[bold yellow]Cancelled by user.[/bold yellow]")

    if trace:
        ttbl = Table(title=f"Trace · {code} · {asof}")
        ttbl.add_column("Agent", style="cyan")
        ttbl.add_column("Status")
        ttbl.add_column("Elapsed", justify="right")
        ttbl.add_column("Output bytes (rough token proxy)", justify="right")
        total_elapsed = 0.0
        for n in nodes:
            name = n.agent.NAME
            r = results.get(name)
            ok = "✓" if (r and r.ok) else ("✗" if r else "—")
            elapsed = r.elapsed_seconds if r else 0.0
            total_elapsed += elapsed
            output_size = 0
            if r and r.ok and r.output is not None:
                try:
                    output_size = len(r.output.model_dump_json())
                except Exception:
                    output_size = 0
            ttbl.add_row(name, ok, f"{elapsed:.1f}s", str(output_size))
        ttbl.add_row("[bold]TOTAL[/bold]", "", f"[bold]{total_elapsed:.1f}s[/bold]", "")
        console.print(ttbl)

    if cancelled:
        return

    writer_result = results.get("report-writer")
    if writer_result and writer_result.ok:
        md_path = Path(writer_result.output.output_md_path)
        # Render the report inline + write a colored HTML copy next to it
        try:
            html_path = render_report(md_path)
            console.print(
                f"\n[bold green]Report rendered.[/bold green]\n"
                f"  md:   {md_path}\n"
                f"  html: [link={_to_file_url(html_path)}]{_to_file_url(html_path)}[/link]"
            )
        except Exception as exc:  # rendering is best-effort
            console.print(
                f"\n[bold green]Report saved:[/bold green] {md_path}\n"
                f"[yellow]inline render failed: {exc}[/yellow]"
            )
    else:
        console.print("\n[bold red]Failed.[/bold red]")
        for n, r in results.items():
            if not r.ok:
                console.print(f"  [red]{n}: {r.error}[/red]")


# ---------------------------------------------------------------------------
# Interactive REPL
# ---------------------------------------------------------------------------

async def dispatch(intent: Intent) -> None:
    if isinstance(intent, IntentReport):
        await run_report_oneshot(code=intent.code, asof=None, out_dir=Path("./out"))
    elif isinstance(intent, IntentSlashCmd):
        await handle_slash(intent.command, intent.args)
    elif isinstance(intent, IntentChat):
        console.print(
            f"[yellow](chat mode TBD in v0.2)[/yellow] you said: {intent.text}"
        )


async def run_tui() -> None:
    import time
    from prompt_toolkit import PromptSession
    from prompt_toolkit.history import InMemoryHistory

    _ensure_registered()
    render_banner()
    session: PromptSession = PromptSession(history=InMemoryHistory())
    while True:
        try:
            text = await asyncio.to_thread(session.prompt, "> ")
        except (EOFError, KeyboardInterrupt):
            break
        intent = parse_input(text)
        if isinstance(intent, IntentQuit):
            break
        start = time.time()
        try:
            await dispatch(intent)
        finally:
            if isinstance(intent, IntentReport):
                kind = "report"
            elif isinstance(intent, IntentSlashCmd):
                kind = "slash"
            else:
                kind = "chat"
            session_mgr.append(SessionEvent(
                kind=kind,
                input=text,
                output_summary="",
                duration_s=round(time.time() - start, 2),
            ))
    console.print("[dim]bye[/dim]")
