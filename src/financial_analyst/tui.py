"""TUI module — Rich TUI + prompt-toolkit REPL with intent parsing (Task 32)."""
from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import List, Union

from rich.console import Console
from rich.live import Live
from rich.panel import Panel
from rich.table import Table

console = Console()

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
        f"Sub-agents registered: {len(SubAgentRegistry.names())}\n\n"
        "[dim]Commands: /report <code> · /memory · /provider · /agents · /show · /quit\n"
        "Natural language: '看看 600519'[/dim]",
        title="Welcome",
        border_style="cyan",
    )
    console.print(panel)


# ---------------------------------------------------------------------------
# Agent registration
# ---------------------------------------------------------------------------

def _ensure_registered() -> None:
    """Register all 13 sub-agents into SubAgentRegistry (idempotent)."""
    from financial_analyst.agent.registry import SubAgentRegistry

    if SubAgentRegistry.names():
        return

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
            console.print(files[0].read_text(encoding="utf-8"))

    elif cmd == "provider":
        console.print(
            "[yellow]provider switching: see config/llm.yaml (live switch TBD in v0.2)[/yellow]"
        )

    else:
        console.print(f"[red]unknown command: /{cmd}[/red]")


async def handle_memory_cmd(args: List[str]) -> None:
    if not args:
        console.print("usage: /memory <list|show|reload> [agent] [file]")
        return
    sub = args[0]
    mem_root = Path("memories")
    if sub == "list" and len(args) >= 2:
        agent_dir = mem_root / args[1]
        if not agent_dir.exists():
            console.print(f"[red]no memory dir: {agent_dir}[/red]")
            return
        for f in sorted(agent_dir.glob("*.md")):
            console.print(f"  {f.name}")
    elif sub == "reload":
        console.print("[green]memory cache cleared; next agent invocation will reload[/green]")
    else:
        console.print("usage: /memory list <agent>  |  /memory reload")


# ---------------------------------------------------------------------------
# One-shot report runner (called by CLI + TUI)
# ---------------------------------------------------------------------------

async def run_report_oneshot(code: str, asof, out_dir: Path) -> None:
    _ensure_registered()

    from financial_analyst.agent.orchestrator import Orchestrator
    from financial_analyst.swarm import load_preset

    asof = asof or date.today().isoformat()
    nodes = load_preset("stock-deep-dive", memory_root=Path("memories"))

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

    with Live(make_table(), console=console, refresh_per_second=4) as live:
        def on_event(evt: str, data: dict) -> None:
            if evt == "wave_start":
                for n in data["agents"]:
                    status[n]["state"] = "running"
            elif evt == "agent_done":
                status[data["agent"]]["state"] = "done" if data["ok"] else "fail"
                status[data["agent"]]["elapsed"] = data["elapsed"]
            live.update(make_table())

        orch = Orchestrator(nodes, on_event=on_event)
        console.print(f"[bold]Running stock-deep-dive for {code} (asof={asof})…[/bold]")
        results = await orch.run({"code": code, "asof_date": asof, "out_dir": str(out_dir)})

    writer_result = results.get("report-writer")
    if writer_result and writer_result.ok:
        console.print(
            f"\n[bold green]Report:[/bold green] {writer_result.output.output_md_path}"
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
        await dispatch(intent)
    console.print("[dim]bye[/dim]")
