"""Full-TUI conversational buddy (v1.6+) — Claude Code-style layout.

Layout::

    ┌────────────────────────────────────────────────┐
    │ Transcript window (scrollable)                  │
    │                                                  │
    │ ❯ 茅台多少钱                                    │
    │ ▶ quote_lookup({'code': 'SH600519'})            │
    │ ✓ quote_lookup                                  │
    │   SH600519: close=1280, PE=20.14                │
    │ 贵州茅台 现价 1280 元...                         │
    │                                                  │
    ├────────────────────────────────────────────────┤
    │ ⠋ 调用 chain_for…  [ESC 取消] ▇▃▄▇▂ +0.8% #023  │  ← spinner row
    ├────────────────────────────────────────────────┤  (only during turn)
    │ ❯ 顺便看看比亚迪█                                │  ← persistent input
    └────────────────────────────────────────────────┘

Key features vs. the v1.5 simple REPL:

- Input field is ALWAYS active. You can type the next prompt while the
  agent is still thinking; pressing Enter queues it (single slot).
- ESC at any time cancels the in-flight turn cleanly. The transcript
  shows a `✗ 已取消` marker and the input stays focused.
- Spinner row appears/disappears via a ConditionalContainer driven by
  the agent's run state — no flicker, no console scrollback pollution.
- Rich markdown / colours bridge into prompt_toolkit's renderer via
  Rich → ANSI → prompt_toolkit ANSI() — keeps the existing colour
  conventions used by all the v1.5 event handlers.

The transcript buffer is append-only; we never rewrite history. The
spinner is a separate `KLineSpinner` instance ticking on a background
asyncio task that invalidates the prompt_toolkit Application ~8 fps.
"""
from __future__ import annotations
import asyncio
from io import StringIO
from pathlib import Path
from typing import Any, List, Optional, Tuple

from prompt_toolkit import Application
from prompt_toolkit.filters import Condition
from prompt_toolkit.formatted_text import ANSI, to_formatted_text
from prompt_toolkit.history import FileHistory
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import Layout
from prompt_toolkit.layout.containers import (
    ConditionalContainer, HSplit, Window,
)
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.layout.dimension import Dimension
from prompt_toolkit.widgets import TextArea

from rich.console import Console
from rich.markdown import Markdown
from rich.text import Text

from financial_analyst import __version__ as _FA_VERSION
from financial_analyst.buddy.agent import BuddyAgent, TurnEvent
from financial_analyst.buddy.animation import (
    KLineSpinner, STATUS_THINKING, STATUS_TOOL_CALLING,
    STATUS_TOOL_FINISHED,
)


# ---------------------------------------------------------------------------
# Rich → ANSI bridge for prompt_toolkit
# ---------------------------------------------------------------------------


def _rich_to_ansi(renderable: Any, width: int = 120) -> str:
    """Render a Rich object (Markdown, Text, plain str with markup) to an
    ANSI-escaped string suitable for prompt_toolkit's ANSI() wrapper."""
    buf = StringIO()
    console = Console(
        file=buf,
        force_terminal=True,
        color_system="truecolor",
        width=width,
        legacy_windows=False,
        emoji=True,
    )
    # If it's a plain string containing Rich markup, convert via Text.
    if isinstance(renderable, str):
        # Render via Console.print so [bold] etc. get parsed.
        console.print(renderable, soft_wrap=True)
    else:
        console.print(renderable, soft_wrap=True)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# BuddyApp
# ---------------------------------------------------------------------------


_BANNER = f"""\
[bold cyan]金融助手[/]  v{_FA_VERSION} — A-share research conversational agent

Type natural language. The agent picks tools automatically.
[bold]ESC[/] cancel running turn · Enter submit · type while agent thinks (queues)
Slash: [cyan]/help /reset /tools /save /quit[/]
[dim]──────────────────────────────────────────────────────────────[/]
"""


_HELP = """[bold]Slash commands:[/]

  /help           Show this message
  /reset          Clear conversation history
  /quit  /exit    Leave the app
  /tools          List 14 available tools
  /save <path>    Dump transcript to a markdown file

[bold]Examples:[/]
  "茅台现在多少钱"
  "csi300 里 PE<20 + 股息率>3% 的"
  "AI 算力链最近怎么样"
  "跑一份寒武纪的研报"     [dim](会问 y/N 因为耗时长)[/]
"""


class BuddyApp:
    """Conversational TUI Application with persistent input + transcript +
    cancellable agent turns + submission queue.

    Public API (used by tests / CLI entry):

      - ``run()``                async coroutine, blocks until /quit or Ctrl-D
      - ``submit(text)``          programmatic submission (used by tests)
      - ``has_active_turn()``     bool
      - ``transcript_text()``     full transcript as a single ANSI string
    """

    def __init__(self) -> None:
        # ----- terminal-free state (safe to use in tests) ---------------
        self.agent = BuddyAgent()
        self.spinner = KLineSpinner()
        self.spinner_visible: bool = False
        self.transcript_chunks: List[str] = []  # already-rendered ANSI strings
        self.queued_input: Optional[str] = None
        self.current_turn_task: Optional[asyncio.Task] = None
        self.animator_task: Optional[asyncio.Task] = None

        # Banner goes into the transcript even before the Application exists,
        # so it's visible on first render.
        self._append_chunk(_rich_to_ansi(_BANNER))

        # ----- Application built lazily ----------------------------------
        # Creating prompt_toolkit's Application requires a real terminal
        # (Windows console screen buffer), so we defer it to run().
        self.input_field: Optional[TextArea] = None
        self.application: Optional[Application] = None

    def _build_application(self) -> None:
        """Construct the prompt_toolkit Application + child widgets.
        Called once from ``run()`` so tests that instantiate BuddyApp
        directly don't need a real terminal."""
        hist_path = Path.home() / ".financial-analyst" / "buddy_history.txt"
        hist_path.parent.mkdir(parents=True, exist_ok=True)
        self.input_field = TextArea(
            prompt="❯ ",
            multiline=False,
            wrap_lines=False,
            accept_handler=self._on_submit,
            history=FileHistory(str(hist_path)),
            height=1,
        )

        transcript_window = Window(
            content=FormattedTextControl(
                text=self._get_transcript_ansi,
                focusable=False,
                show_cursor=False,
            ),
            wrap_lines=True,
            always_hide_cursor=True,
            ignore_content_height=False,
        )
        spinner_window = ConditionalContainer(
            Window(
                content=FormattedTextControl(
                    text=self._get_spinner_ansi,
                    focusable=False,
                    show_cursor=False,
                ),
                height=Dimension(min=2, max=2),
                wrap_lines=False,
                always_hide_cursor=True,
            ),
            filter=Condition(lambda: self.spinner_visible),
        )
        layout = Layout(
            HSplit([transcript_window, spinner_window, self.input_field]),
            focused_element=self.input_field,
        )

        kb = KeyBindings()

        @kb.add("escape", eager=True)
        def _esc(event):  # noqa: ARG001
            self._cancel_current_turn(reason="ESC")

        @kb.add("c-c")
        def _ctrlc(event):
            if self._has_active_turn():
                self._cancel_current_turn(reason="Ctrl+C")
            else:
                event.app.exit()

        @kb.add("c-d")
        def _ctrld(event):
            event.app.exit()

        self.application = Application(
            layout=layout,
            key_bindings=kb,
            full_screen=True,
            mouse_support=False,
            refresh_interval=0.1,
        )

    # ----- public read-only helpers (used by tests + /save) --------------

    def has_active_turn(self) -> bool:
        return self._has_active_turn()

    def transcript_text(self) -> str:
        return "".join(self.transcript_chunks)

    # ----- transcript management -----------------------------------------

    def _append_chunk(self, ansi_text: str) -> None:
        """Append a pre-rendered ANSI chunk to the transcript and request
        a redraw (no-op until the Application is running)."""
        # prompt_toolkit's ANSI() consumes the trailing newline; keep one.
        if not ansi_text.endswith("\n"):
            ansi_text += "\n"
        self.transcript_chunks.append(ansi_text)
        app = getattr(self, "application", None)
        if app is not None:
            try:
                app.invalidate()
            except Exception:
                pass

    def _append_rich(self, renderable: Any, width: int = 120) -> None:
        self._append_chunk(_rich_to_ansi(renderable, width=width))

    def _get_transcript_ansi(self):
        return ANSI(self.transcript_text())

    def _get_spinner_ansi(self):
        return ANSI(_rich_to_ansi(self.spinner.render(), width=80))

    # ----- submission / queueing -----------------------------------------

    def _on_submit(self, buf) -> bool:  # noqa: ARG002
        """prompt_toolkit accept_handler. Returns False so the buffer keeps
        focus and is reusable for the next prompt."""
        text = self.input_field.text.strip() if self.input_field is not None else ""
        # Clear the text immediately so the user sees a fresh input line.
        if self.input_field is not None:
            self.input_field.text = ""
        if not text:
            return False
        self.submit(text)
        return False

    def submit(self, text: str) -> None:
        """Submit a user prompt (programmatic or via accept_handler).

        - If the agent is idle: start a turn immediately.
        - If the agent is busy: queue (single slot — newest replaces older).
        - If the text starts with ``/``: handle as a slash command instead.
        """
        if text.startswith("/"):
            handled = self._handle_slash(text)
            if handled:
                return
            # Unknown slash → echo as a normal prompt so user sees the
            # mistake reflected and the LLM gets a chance to clarify.

        # Echo the user line into the transcript.
        self._append_rich(f"[bold cyan]❯[/] {_escape_markup(text)}")

        if self._has_active_turn():
            self.queued_input = text
            self._append_rich(
                "  [yellow]…已排队 (当前 turn 结束后自动执行)[/]"
            )
        else:
            self._start_turn(text)

    def _has_active_turn(self) -> bool:
        return (
            self.current_turn_task is not None
            and not self.current_turn_task.done()
        )

    def _start_turn(self, text: str) -> None:
        """Schedule the agent turn on the running event loop.

        In production this is called from prompt_toolkit's accept_handler,
        which always runs inside Application.run_async() — so a loop
        exists. In tests / standalone scripts that poke ``submit()``
        without an asyncio context, we catch the missing-loop error and
        surface it to the transcript instead of crashing.
        """
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            self._append_rich(
                "[red]Cannot start turn — no running asyncio loop. "
                "Did you call submit() outside of run()?[/]"
            )
            return
        self.current_turn_task = loop.create_task(self._run_turn(text))

    async def _run_turn(self, text: str) -> None:
        """Drive the BuddyAgent for one turn, mirroring events into the
        transcript and updating spinner status."""
        self.spinner_visible = True
        self.spinner.set_status(STATUS_THINKING)
        if self.application is not None:
            self.application.invalidate()
        try:
            async for evt in self.agent.run_turn(text, confirm_callback=self._confirm):
                self._handle_event(evt)
                if self.application is not None:
                    self.application.invalidate()
        except asyncio.CancelledError:
            self._append_rich("[yellow]✗ 已取消[/]")
            # Re-raise so callers see CancelledError if they await us.
            raise
        finally:
            self.spinner_visible = False
            if self.application is not None:
                self.application.invalidate()
            # Run any queued prompt.
            if self.queued_input is not None:
                queued = self.queued_input
                self.queued_input = None
                self._append_rich(f"[dim]→ 处理排队的输入[/]")
                # Don't await — start as fire-and-forget task so this
                # coroutine can return cleanly. We assign to current_turn
                # so submit() sees it as active. We MUST be inside a
                # running loop here (we're called from inside _run_turn
                # which is itself a task on the loop).
                self._start_turn(queued)

    def _handle_event(self, evt: TurnEvent) -> None:
        if evt.kind == "text":
            if evt.payload:
                # Render markdown so headers + bullets show nicely.
                self._append_rich(Markdown(evt.payload))
            self.spinner.set_status(STATUS_THINKING)
        elif evt.kind == "tool_call":
            name = evt.payload["name"]
            args = evt.payload["args"]
            self._append_rich(f"[cyan]▶ {name}[/]  [dim]{args}[/]")
            self.spinner.set_status(STATUS_TOOL_CALLING.format(tool=name))
        elif evt.kind == "tool_result":
            name = evt.payload["name"]
            content = evt.payload["content"]
            is_err = evt.payload["is_error"]
            prefix = "[red]✗" if is_err else "[green]✓"
            self._append_rich(f"{prefix} {name}[/]")
            # Truncate long content visually; LLM still gets full thing.
            if len(content) > 600:
                self._append_rich(f"  [dim]{_escape_markup(content[:480])}…[/]")
                self._append_rich(
                    f"  [dim]({len(content) - 480} more chars sent to LLM)[/]"
                )
            else:
                self._append_rich(f"  [dim]{_escape_markup(content)}[/]")
            self.spinner.set_status(STATUS_TOOL_FINISHED)
        elif evt.kind == "error":
            self._append_rich(f"[red]Error: {_escape_markup(str(evt.payload))}[/]")

    # ----- cancellation ---------------------------------------------------

    def _cancel_current_turn(self, reason: str = "") -> None:
        """ESC / Ctrl+C handler: cancel ONLY the currently running turn.

        Peel-off semantics (v1.6.2+): each ESC press cancels exactly
        one layer. If you have a turn running plus a queued prompt:
          - 1st ESC → cancel current; queued starts next
          - 2nd ESC → cancel the now-running queued turn
        Press ESC repeatedly to back out to a clean state.

        This matches Claude Code's "step back one level" behaviour and
        keeps the type-while-thinking workflow snappy — the user can
        type, ESC the current, let theirs run, and ESC that too if
        they change their mind mid-execution.
        """
        if not self._has_active_turn():
            # No turn active, but maybe we have a queue lingering — drop it
            # so a stale prompt doesn't suddenly fire on the next loop tick.
            if self.queued_input is not None:
                self.queued_input = None
                self._append_rich("[dim]排队的输入已清空[/]")
            return
        task = self.current_turn_task
        if task is not None and not task.done():
            task.cancel()
        # The _run_turn finally block writes the "已取消" marker and
        # drains queued_input (if any) by starting the next turn.

    async def _confirm(self, tool_name: str, args: dict) -> bool:  # noqa: ARG002
        """Confirmation callback for confirm-required tools.

        v1.6: auto-accept and surface a hint in the transcript. The user
        can press ESC to cancel mid-flight if they regret it. A proper
        modal dialog is a future polish item.
        """
        self._append_rich(
            f"[yellow]⚠ 启动耗时工具 {tool_name} — 按 ESC 随时取消[/]"
        )
        return True

    # ----- slash commands -------------------------------------------------

    def _handle_slash(self, cmd_line: str) -> bool:
        cmd, *rest = cmd_line.split(maxsplit=1)
        if cmd in ("/quit", "/exit"):
            if self.application is not None:
                self.application.exit()
            return True
        if cmd == "/help":
            self._append_rich(_HELP)
            return True
        if cmd == "/reset":
            self.agent.reset()
            self._append_rich("[dim]对话历史已清空[/]")
            return True
        if cmd == "/tools":
            from financial_analyst.buddy.tools import TOOL_REGISTRY
            lines = [f"[bold]{len(TOOL_REGISTRY)} tools:[/]"]
            for t in TOOL_REGISTRY:
                cost = f" [yellow][{t.cost_hint}][/]" if t.cost_hint != "instant" else ""
                lines.append(f"  [cyan]{t.name}[/]{cost}")
                first_sentence = t.description.split(".")[0]
                lines.append(f"    [dim]{first_sentence}.[/]")
            self._append_rich("\n".join(lines))
            return True
        if cmd == "/save":
            target = (rest[0] if rest else "buddy_chat.md").strip()
            self._save_transcript(Path(target))
            self._append_rich(f"[dim]已保存到 {target}[/]")
            return True
        return False

    def _save_transcript(self, path: Path) -> None:
        # Strip ANSI codes for the markdown file.
        import re
        ansi_re = re.compile(r"\x1b\[[0-9;]*m")
        plain = "".join(ansi_re.sub("", c) for c in self.transcript_chunks)
        path.write_text(plain, encoding="utf-8")

    # ----- animator -------------------------------------------------------

    async def _animate(self) -> None:
        """Background ticker: advance the K-line spinner every ~120 ms while
        a turn is active. Runs on the same event loop as the Application."""
        try:
            while True:
                await asyncio.sleep(0.12)
                if self.spinner_visible:
                    self.spinner.tick()
                    if self.application is not None:
                        try:
                            self.application.invalidate()
                        except Exception:
                            pass
        except asyncio.CancelledError:
            pass

    # ----- entrypoint -----------------------------------------------------

    async def run(self) -> None:
        if self.application is None:
            self._build_application()
        self.animator_task = asyncio.ensure_future(self._animate())
        try:
            await self.application.run_async()
        finally:
            if self.animator_task is not None and not self.animator_task.done():
                self.animator_task.cancel()
                try:
                    await self.animator_task
                except (asyncio.CancelledError, Exception):
                    pass


def _escape_markup(text: str) -> str:
    """Escape Rich markup brackets in user-supplied / tool-output text
    so that `[anything]` doesn't get interpreted as a style tag."""
    return text.replace("[", r"\[")


async def run_app() -> None:
    """Launch the v1.6 full-TUI buddy app."""
    app = BuddyApp()
    await app.run()
