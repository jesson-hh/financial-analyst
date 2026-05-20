"""REPL — prompt_toolkit input + Rich streaming output for the buddy agent.

Reuses the existing TUI's prompt history and styling conventions where
possible. The loop is dead simple:

    while True:
        user = prompt()
        if user in {/quit, /reset, /help}: handle
        else: await agent.run_turn(user) and render events as they arrive
"""
from __future__ import annotations
import asyncio
import sys
from pathlib import Path
from typing import Any, Dict

from prompt_toolkit import PromptSession
from prompt_toolkit.history import FileHistory
from prompt_toolkit.styles import Style
from rich.console import Console
from rich.live import Live
from rich.markdown import Markdown
from rich.panel import Panel
from rich.text import Text

from financial_analyst.buddy.agent import BuddyAgent, TurnEvent
from financial_analyst.buddy.animation import (
    KLineSpinner, STATUS_THINKING, STATUS_TOOL_CALLING,
    STATUS_TOOL_PARSING, STATUS_TOOL_FINISHED,
)


from financial_analyst import __version__ as _FA_VERSION

BANNER = f"""\
[bold cyan]金融助手[/]  v{_FA_VERSION} — A-share research conversational agent

Type natural language. The agent picks tools automatically.
Slash commands: /help /reset /quit /tools /save"""


SLASH_HELP = """\
[bold]Slash commands:[/]

  /help          Show this message
  /reset         Clear conversation history
  /quit  /exit   Leave the chat
  /tools         List available tools
  /save <path>   Save conversation to a markdown file

[bold]Examples:[/]
  "茅台现在多少钱"
  "csi300 里 PE<20 + 股息率>3% 的"
  "跑一份寒武纪的研报"
  "我之前怎么看 SH600100"
  "AI 算力链最近怎么样"
  "看看 SH688256 的同行"
"""


def _style() -> Style:
    return Style.from_dict({
        "prompt": "ansicyan bold",
        "": "",
    })


async def _confirm_tool(console: Console) -> "_ConfirmCallback":
    """Build a per-tool confirmation callback bound to the REPL's stdin."""
    async def _confirm(tool_name: str, args: dict) -> bool:
        console.print(
            f"\n[yellow]⚠ This tool ({tool_name}) takes minutes to run. Continue?[/]\n"
            f"  args: {args}"
        )
        # Use prompt_toolkit's in_terminal pattern via input()
        try:
            ans = await asyncio.get_event_loop().run_in_executor(
                None, lambda: input("    (y/N): ").strip().lower()
            )
        except (EOFError, KeyboardInterrupt):
            return False
        return ans in ("y", "yes", "是", "好", "ok")
    return _confirm


async def run_repl() -> None:
    """Main REPL loop. Each user turn runs the agent and streams TurnEvents."""
    console = Console()
    console.print(Panel(BANNER, border_style="cyan"))

    hist_path = Path.home() / ".financial-analyst" / "buddy_history.txt"
    hist_path.parent.mkdir(parents=True, exist_ok=True)
    session: PromptSession = PromptSession(
        history=FileHistory(str(hist_path)),
        style=_style(),
    )

    agent = BuddyAgent()
    confirm = await _confirm_tool(console)

    while True:
        try:
            user_input = await session.prompt_async([("class:prompt", "❯ ")])
        except (EOFError, KeyboardInterrupt):
            console.print("[dim]bye.[/]")
            return

        user_input = (user_input or "").strip()
        if not user_input:
            continue

        # --- slash commands ----------------------------------------------
        if user_input.startswith("/"):
            cmd, *rest = user_input.split(maxsplit=1)
            if cmd in ("/quit", "/exit"):
                console.print("[dim]bye.[/]")
                return
            if cmd == "/help":
                console.print(Panel(SLASH_HELP, title="Help", border_style="dim"))
                continue
            if cmd == "/reset":
                agent.reset()
                console.print("[dim]Conversation reset.[/]")
                continue
            if cmd == "/tools":
                from financial_analyst.buddy.tools import TOOL_REGISTRY
                console.print(f"[bold]{len(TOOL_REGISTRY)} tools registered:[/]")
                for t in TOOL_REGISTRY:
                    cost = f" [{t.cost_hint}]" if t.cost_hint != "instant" else ""
                    console.print(f"  [cyan]{t.name}[/]{cost}")
                    console.print(f"    [dim]{t.description.split('.')[0]}.[/]")
                continue
            if cmd == "/save":
                if not rest:
                    console.print("[red]Usage: /save <path>[/]")
                    continue
                _save_conversation(agent, Path(rest[0]))
                console.print(f"[dim]Saved to {rest[0]}[/]")
                continue
            console.print(f"[red]Unknown slash command: {cmd}. Try /help.[/]")
            continue

        # --- run agent turn with animated K-line spinner ----------------
        try:
            await _run_turn_with_spinner(console, agent, user_input, confirm)
        except KeyboardInterrupt:
            console.print("[yellow]Interrupted. Type a new prompt or /quit.[/]")
            continue
        console.print()  # blank line between turns


async def _run_turn_with_spinner(
    console: Console,
    agent: "BuddyAgent",
    user_input: str,
    confirm_callback,
) -> None:
    """Run one agent turn, animating a K-line spinner during waits.

    The spinner lives in a transient Rich Live region at the bottom; each
    TurnEvent is printed ABOVE it (so it scrolls into the transcript as
    a normal message), and the spinner status updates to reflect what
    the agent is currently doing.
    """
    spinner = KLineSpinner()

    async def _animate(live: Live) -> None:
        """Background task: tick the spinner ~8 fps."""
        try:
            while True:
                await asyncio.sleep(0.12)
                spinner.tick()
                live.update(spinner.render())
        except asyncio.CancelledError:
            pass

    with Live(
        spinner.render(),
        console=console,
        refresh_per_second=10,
        transient=True,
    ) as live:
        animator = asyncio.create_task(_animate(live))
        try:
            spinner.set_status(STATUS_THINKING)
            async for evt in agent.run_turn(user_input, confirm_callback=confirm_callback):
                # Render the event above the live region.
                _render_above_live(live, evt)
                # Adjust spinner status for the next wait.
                if evt.kind == "tool_call":
                    spinner.set_status(
                        STATUS_TOOL_CALLING.format(tool=evt.payload["name"])
                    )
                elif evt.kind == "tool_result":
                    spinner.set_status(STATUS_TOOL_FINISHED)
                elif evt.kind == "text":
                    spinner.set_status(STATUS_THINKING)
                elif evt.kind == "done":
                    break
                # Refresh the live region immediately so the new status is visible.
                live.update(spinner.render())
        finally:
            animator.cancel()
            try:
                await animator
            except asyncio.CancelledError:
                pass


def _render_above_live(live: Live, evt: TurnEvent) -> None:
    """Render a TurnEvent above the Live region — Rich handles the scroll.

    ``live.console.print`` writes to the transcript ABOVE the spinner,
    so each event becomes a permanent line while the K-line keeps
    animating below.
    """
    console = live.console
    if evt.kind == "text":
        if evt.payload:
            console.print(Markdown(evt.payload))
    elif evt.kind == "tool_call":
        name = evt.payload["name"]
        args = evt.payload["args"]
        console.print(f"[cyan]▶ {name}[/]  [dim]{args}[/]")
    elif evt.kind == "tool_result":
        name = evt.payload["name"]
        content = evt.payload["content"]
        is_err = evt.payload["is_error"]
        prefix = "[red]✗" if is_err else "[green]✓"
        console.print(f"{prefix} {name}[/]")
        if len(content) > 800:
            console.print(f"  [dim]{content[:600]}[/]")
            console.print(f"  [dim]... ({len(content) - 600} more chars sent to LLM)[/]")
        else:
            console.print(f"  [dim]{content}[/]")
    elif evt.kind == "error":
        console.print(f"[red]Error: {evt.payload}[/]")
    # 'done' is silent — Live exit handles cleanup.


def _save_conversation(agent: BuddyAgent, path: Path) -> None:
    """Write the conversation to a markdown file."""
    lines = ["# 金融助手 conversation\n"]
    for m in agent.messages:
        if m.role == "user":
            lines.append(f"\n**user**: {m.content}\n")
        elif m.role == "assistant":
            text = m.raw.get("content") if m.raw else m.content
            if text:
                lines.append(f"\n**assistant**: {text}\n")
            for tc in (m.raw or {}).get("tool_calls", []) or []:
                fn = tc.get("function", {})
                lines.append(f"\n**tool_call**: {fn.get('name', '?')}({fn.get('arguments', '{}')})\n")
        elif m.role == "tool":
            lines.append(f"\n**tool_result**: {m.content[:500]}...\n")
    path.write_text("".join(lines), encoding="utf-8")
