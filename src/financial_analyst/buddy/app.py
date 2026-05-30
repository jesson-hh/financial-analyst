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
- v1.6.7 follow-tail state machine: PageUp pauses auto-scroll so you
  can read history; End / Ctrl-↓ jumps back to latest. New appends
  don't snatch the viewport from underneath you while you're paused.

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
from prompt_toolkit.data_structures import Point
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.layout.dimension import Dimension
from prompt_toolkit.mouse_events import MouseEvent, MouseEventType
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
[bold]ESC[/] cancel · Enter submit · [bold]PageUp[/] 翻历史 · [bold]End[/] 回到最新
[bold]/mode[/] safe/default/auto · [bold]/model[/] switch LLM · [bold]/help[/] more
[dim]──────────────────────────────────────────────────────────────[/]
"""


_HELP = """[bold]Slash commands:[/]

  /help                  Show this message
  /reset                 Clear conversation history
  /quit  /exit           Leave the app
  /tools                 List available tools
  /save <path>           Dump transcript to a markdown file
  /mode [default|safe|auto]
                         Show or set permission mode (see below)
  /model [<name>]        Show available models or switch to one
  /watch [on [min] [sources] | off]
                         Background 盯盘: evaluate price alerts every
                         N min (+ optional auto-collect), fire matches
                         into the transcript. Add alerts with natural
                         language ("茅台跌破1200提醒我").

[bold]Permission modes:[/]
  default   Auto-run instant/seconds tools; ASK before minutes-level
            ones (run_report, alpha_bench). Recommended for daily use.
  safe      ASK before EVERY tool call. Every step requires y/n.
            Use when reviewing the agent's behaviour.
  auto      Auto-run EVERYTHING including minutes-level tools.
            No prompts. Use only when you trust the prompt + model.

[bold]Examples:[/]
  "茅台现在多少钱"
  "csi300 里 PE<20 + 股息率>3% 的"
  "AI 算力链最近怎么样"
  "跑一份寒武纪的研报"     [dim](default 模式下会问 y/n)[/]
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

        # v1.6.7: scroll state machine.
        # follow_tail=True (default): new appends auto-scroll into view.
        # follow_tail=False: user has paged up and is browsing history.
        #   ``top_line`` is the row index that should sit at the top of
        #   the transcript viewport. Press End / PageDown-at-bottom to
        #   return to follow-tail mode.
        self.follow_tail: bool = True
        self.top_line: int = 0
        # Fallback step when render_info isn't yet available (very first
        # PageUp before the first render). 10 rows is the conventional
        # half-screen jump.
        self._scroll_fallback_step: int = 10

        # v1.7.4: Claude-Code-style permission gating + model picker.
        # permission_mode controls when the agent asks the user before
        # running a tool. ``_pending_confirm`` is the future the agent
        # awaits while we collect y/n from the input box.
        # ``_auto_approved`` accumulates tool names the user said
        # "always" to during this session.
        self.permission_mode: str = "default"  # default | safe | auto
        self._pending_confirm: Optional[asyncio.Future] = None
        self._pending_confirm_tool: Optional[str] = None
        self._auto_approved: set[str] = set()

        # Model picker — initial selection comes from the agent's
        # LLMClient which reads agent_overrides / default_model from
        # llm.yaml. Both fields kept so /model can switch live.
        client = self.agent._client
        self.provider: str = client.provider
        self.model: str = client.model

        # v1.8.0: background watch loop state. Off by default.
        # /watch on starts a task that every ``watch_interval`` seconds
        # (optionally re-collects ``watch_sources`` then) evaluates the
        # alert store and fires matched rules into the transcript.
        self.watch_enabled: bool = False
        self.watch_interval: int = 300  # seconds
        self.watch_sources: Optional[str] = None
        self.watch_task: Optional[asyncio.Task] = None

        # v1.9.0: Hermes-style background skill review.
        # v1.9.3: skill_mode is the source of truth; agent mirrors it.
        self.skill_mode: str = "manual"  # "auto" | "manual"

        # v1.7.5: restore persisted prefs (permission_mode + model) from
        # ~/.financial-analyst/buddy.yaml so the user doesn't re-set them
        # every launch. Applied AFTER reading defaults above.
        self._apply_saved_prefs()

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
                # v1.6.6: pin an invisible cursor to the last rendered
                # line. prompt_toolkit's Window.do_scroll() resets
                # vertical_scroll back to cursor_pos on every render,
                # so v1.6.5's get_vertical_scroll callback was being
                # immediately undone. Pinning the cursor to the bottom
                # lets do_scroll's "scroll down to keep cursor visible"
                # branch do the right thing for us.
                get_cursor_position=self._get_cursor_at_bottom,
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
        # v1.6.3: persistent queue indicator. Visible whenever
        # ``queued_input`` is set, so the user always knows what will
        # run next (and can decide to ESC if they typed something
        # they no longer want).
        queue_window = ConditionalContainer(
            Window(
                content=FormattedTextControl(
                    text=self._get_queue_ansi,
                    focusable=False,
                    show_cursor=False,
                ),
                height=Dimension(min=1, max=1),
                wrap_lines=False,
                always_hide_cursor=True,
            ),
            filter=Condition(lambda: self.queued_input is not None),
        )
        # Keep a reference to the transcript window so the scroll key
        # handlers (v1.6.7) can read render_info to compute window-sized
        # PageUp/PageDown steps.
        self._transcript_window = transcript_window

        # v1.6.7: history-browse hint, shows when follow_tail=False so
        # the user sees they're paused, with the key that returns to
        # latest output.
        history_hint = ConditionalContainer(
            Window(
                content=FormattedTextControl(
                    text=lambda: ANSI(_rich_to_ansi(
                        "  [yellow]📜 浏览历史[/] [dim]按 End / Ctrl-↓ 回到最新输出[/]"
                    )),
                    focusable=False, show_cursor=False,
                ),
                height=Dimension(min=1, max=1),
                wrap_lines=False,
                always_hide_cursor=True,
            ),
            filter=Condition(lambda: not self.follow_tail),
        )

        # v1.8.2: persistent confirm indicator. When a tool-confirm modal
        # is pending, this red bar stays above the input so the user never
        # loses track of "I'm being asked y/n" even if a background watch
        # alert or scrolling pushes the transcript prompt out of view.
        # (Mirrors the v1.6.3 queue-indicator fix — the confirm modal added
        # in v1.7.4 had no such indicator, a gap caught by selftest_tui.)
        confirm_window = ConditionalContainer(
            Window(
                content=FormattedTextControl(
                    text=self._get_confirm_ansi,
                    focusable=False, show_cursor=False,
                ),
                height=Dimension(min=1, max=1),
                wrap_lines=False,
                always_hide_cursor=True,
            ),
            filter=Condition(
                lambda: self._pending_confirm is not None
                and not self._pending_confirm.done()
            ),
        )

        # v1.7.4: persistent status line above the input — current
        # permission_mode + model. Always visible so the user knows
        # exactly which mode they're in before sending a prompt.
        status_line = Window(
            content=FormattedTextControl(
                text=self._get_status_ansi,
                focusable=False, show_cursor=False,
            ),
            height=Dimension(min=1, max=1),
            wrap_lines=False,
            always_hide_cursor=True,
        )

        layout = Layout(
            HSplit([
                transcript_window,
                spinner_window,
                queue_window,
                confirm_window,
                history_hint,
                status_line,
                self.input_field,
            ]),
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

        # v1.6.7: transcript scrolling. Single-line TextArea doesn't use
        # PageUp/PageDown so these are safe to bind globally. End / Ctrl-↓
        # are the "resume follow-tail" keys.
        @kb.add("pageup")
        def _pageup(event):  # noqa: ARG001
            self._scroll_history(direction=-1)

        @kb.add("pagedown")
        def _pagedown(event):  # noqa: ARG001
            self._scroll_history(direction=+1)

        @kb.add("end")
        def _end(event):  # noqa: ARG001
            self._jump_to_tail()

        @kb.add("c-down")
        def _ctrl_down(event):  # noqa: ARG001
            self._jump_to_tail()

        # v1.6.8: wire mouse wheel to transcript scroll.
        # Without this, Windows Terminal / cmd.exe in alt-screen mode
        # remap wheel events to Up/Down keys, which the single-line
        # TextArea then interprets as "browse input history" — that's
        # why the user only ever saw their own prompts when scrolling.
        # Window._mouse_handler is normally a no-op for FormattedTextControl
        # (no .move_cursor_up()), so we replace it on the instance.
        import types as _types
        transcript_window._mouse_handler = _types.MethodType(
            lambda _win, ev: self._on_mouse_event(ev), transcript_window,
        )

        self.application = Application(
            layout=layout,
            key_bindings=kb,
            full_screen=True,
            # v1.6.8: mouse_support=True is required for SCROLL_UP/DOWN
            # to reach our handler. Trade-off: native click-drag selection
            # is intercepted; Windows Terminal users can still hold Shift
            # while dragging to copy text, and we expose /save to dump
            # the transcript to a file.
            mouse_support=True,
            refresh_interval=0.1,
        )

    # ----- preferences persistence (v1.7.5) ------------------------------

    @staticmethod
    def _prefs_path() -> Path:
        return Path.home() / ".financial-analyst" / "buddy.yaml"

    def _apply_saved_prefs(self) -> None:
        """Load permission_mode + provider/model from buddy.yaml if present.
        Silent no-op on any error (missing file, bad yaml) — defaults stand."""
        path = self._prefs_path()
        if not path.exists():
            return
        try:
            import yaml
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except Exception:
            return
        mode = data.get("permission_mode")
        if mode in ("default", "safe", "auto"):
            self.permission_mode = mode
        skill_mode = data.get("skill_mode")
        if skill_mode in ("auto", "manual"):
            self.skill_mode = skill_mode
            self.agent.skill_mode = skill_mode
        prov = data.get("provider")
        model = data.get("model")
        if prov and model:
            # Validate against available models before applying
            try:
                available = self.agent._client.list_models()
            except Exception:
                available = {}
            if prov in available and model in available.get(prov, []):
                self.provider = prov
                self.model = model
                self.agent._client = self.agent._client.with_overrides(
                    provider=prov, model=model,
                )

    def _save_prefs(self) -> None:
        """Persist current permission_mode + provider/model. Best-effort."""
        path = self._prefs_path()
        try:
            import yaml
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                yaml.safe_dump({
                    "permission_mode": self.permission_mode,
                    "provider": self.provider,
                    "model": self.model,
                    "skill_mode": self.skill_mode,
                }, allow_unicode=True),
                encoding="utf-8",
            )
        except Exception:
            pass

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

    # ----- v1.6.7: history-browse scrolling ------------------------------

    def _viewport_step(self) -> int:
        """Page step in rendered lines. Falls back to a fixed value if
        the transcript window hasn't been rendered yet (e.g. user hits
        PageUp before any output)."""
        win = getattr(self, "_transcript_window", None)
        if win is None:
            return self._scroll_fallback_step
        info = getattr(win, "render_info", None)
        if info is None:
            return self._scroll_fallback_step
        return max(1, info.window_height - 1)  # leave 1 line of context

    def _n_lines(self) -> int:
        return self.transcript_text().count("\n")

    def _scroll_history(self, direction: int, step: Optional[int] = None) -> None:
        """direction = -1 (back/PageUp) | +1 (forward/PageDown).

        ``step`` overrides the line count (default = one viewport).
        Mouse wheel passes 3 — a notch shouldn't jump a whole page.

        Entering history mode pins ``top_line`` at the current viewport
        top so the user's chosen position survives subsequent appends.
        Scrolling forward past the tail returns to follow-tail mode.
        """
        if step is None:
            step = self._viewport_step()
        n_lines = self._n_lines()
        win = getattr(self, "_transcript_window", None)
        viewport_h = max(1, getattr(getattr(win, "render_info", None), "window_height", 0) or self._viewport_step())

        if self.follow_tail and direction < 0:
            # First scroll-up from tail: capture current top
            self.top_line = max(0, n_lines - viewport_h)
            self.follow_tail = False

        if direction < 0:
            self.top_line = max(0, self.top_line - step)
        else:
            self.top_line += step
            # If we've scrolled forward to/past the tail, resume follow_tail
            if self.top_line + viewport_h >= n_lines:
                self._jump_to_tail()
                return

        app = getattr(self, "application", None)
        if app is not None:
            try:
                app.invalidate()
            except Exception:
                pass

    def _jump_to_tail(self) -> None:
        """End / Ctrl-↓ handler: snap back to latest output and resume
        auto-follow."""
        self.follow_tail = True
        self.top_line = 0
        app = getattr(self, "application", None)
        if app is not None:
            try:
                app.invalidate()
            except Exception:
                pass

    # ----- v1.8.0: background watch loop --------------------------

    def _eval_alerts(self):
        """Sync alert evaluation (runs on a worker thread). Uses the
        Tencent BATCH path (v1.9.2): one HTTP request fetches every
        watched code (~120ms for dozens, no cookie), so no per-stock
        opencli bottleneck and no max_codes cap needed."""
        from financial_analyst.buddy.alerts import AlertStore, evaluate_batch
        from financial_analyst.data.collectors.tencent_quote import TencentQuoteCollector
        store = AlertStore()
        if len(store) == 0:
            return []
        coll = TencentQuoteCollector()
        return evaluate_batch(store, coll.fetch, cooldown_min=30.0)

    def _run_watch_collect(self) -> None:
        """Sync data refresh (worker thread) for the configured sources."""
        if not self.watch_sources:
            return
        import subprocess
        from financial_analyst.buddy.tools import _project_root
        try:
            subprocess.run(
                ["financial-analyst", "news-collect",
                 "--sources", self.watch_sources, "--limit", "100"],
                cwd=str(_project_root()),
                capture_output=True, timeout=300,
            )
        except Exception:
            pass

    async def _watch_loop(self) -> None:
        """Background ticker: every ``watch_interval`` s, optionally
        refresh data, then evaluate alerts and fire matched rules into
        the transcript. Cancelled by /watch off."""
        from financial_analyst.buddy.alerts import market_session
        try:
            while self.watch_enabled:
                await asyncio.sleep(self.watch_interval)
                if not self.watch_enabled:
                    break
                # v1.8.1: skip evaluation outside A-share trading hours —
                # off-hours prices are stale (would mis-fire) and hitting
                # opencli every 5 min on a weekend is pure waste.
                if market_session() != "open":
                    continue
                if self.watch_sources:
                    await asyncio.to_thread(self._run_watch_collect)
                try:
                    fired = await asyncio.to_thread(self._eval_alerts)
                except Exception:
                    fired = []
                for rule, quote in fired:
                    self._append_rich(
                        f"[bold red]🔔 盯盘提醒[/] [bold]{rule.describe()}[/]\n"
                        f"  现价 {quote.get('price','?')} "
                        f"({quote.get('changePercent','?')}) "
                        f"[{quote.get('market_status','?')}]"
                    )
                if self.application is not None:
                    try:
                        self.application.invalidate()
                    except Exception:
                        pass
        except asyncio.CancelledError:
            pass

    def _start_watch(self) -> bool:
        """Spawn the watch task on the running loop. Returns False if no
        loop (test/standalone context)."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return False
        if self.watch_task is not None and not self.watch_task.done():
            self.watch_task.cancel()
        self.watch_task = loop.create_task(self._watch_loop())
        return True

    def _stop_watch(self) -> None:
        self.watch_enabled = False
        if self.watch_task is not None and not self.watch_task.done():
            self.watch_task.cancel()
        self.watch_task = None

    def _on_mouse_event(self, mouse_event: MouseEvent):
        """Mouse handler attached to the transcript window. Returns
        ``None`` if the event was consumed, ``NotImplemented`` otherwise
        so prompt_toolkit's default behaviour kicks in.

        Wheel up/down → ``_scroll_history``. The actual scroll step is
        roughly half a viewport per wheel notch (a smaller step makes
        wheel scrolling feel jumpy on long transcripts).
        """
        if mouse_event.event_type == MouseEventType.SCROLL_UP:
            self._scroll_history(direction=-1, step=3)
            return None
        if mouse_event.event_type == MouseEventType.SCROLL_DOWN:
            self._scroll_history(direction=+1, step=3)
            return None
        return NotImplemented

    def _get_transcript_ansi(self):
        return ANSI(self.transcript_text())

    def _get_cursor_at_bottom(self) -> Point:
        """Position the (invisible) cursor so do_scroll() picks the
        right viewport.

        prompt_toolkit's scrolling algorithm normalises vertical_scroll
        against cursor_pos every render. We exploit that by placing
        the cursor wherever we want the viewport to be:

        - ``follow_tail=True`` (default): cursor at the last line →
          do_scroll lays the viewport flush against the bottom, so
          new appends are always visible.
        - ``follow_tail=False`` (user paged up): cursor at
          ``self.top_line`` → do_scroll aligns the viewport's TOP row
          with the cursor, keeping the user's chosen scroll position
          stable even while the agent appends new content underneath.
        """
        text = self.transcript_text()
        n_lines = text.count("\n")
        if self.follow_tail:
            return Point(x=0, y=max(0, n_lines - 1))
        # browsing history: clamp top_line into range and use it
        self.top_line = max(0, min(self.top_line, max(0, n_lines - 1)))
        return Point(x=0, y=self.top_line)

    def _get_spinner_ansi(self):
        return ANSI(_rich_to_ansi(self.spinner.render(), width=80))

    def _get_queue_ansi(self):
        """Persistent queue indicator (1 line above input)."""
        q = self.queued_input or ""
        # Truncate so it doesn't wrap
        if len(q) > 80:
            q = q[:77] + "…"
        return ANSI(_rich_to_ansi(
            f"  [yellow]⏳ 排队中[/] [dim]→[/] [italic]{_escape_markup(q)}[/]   [dim](再按 ESC 也可取消)[/]"
        ))

    def _get_confirm_ansi(self):
        """Persistent confirm indicator (shown only while a tool-confirm
        modal is pending). Keeps the y/n ask visible above the input even
        if the transcript scrolls or a watch alert fires."""
        tool = self._pending_confirm_tool or "?"
        return ANSI(_rich_to_ansi(
            f"  [bold red]⚠ 等待工具确认[/] [bold]{_escape_markup(tool)}[/] — "
            f"[bold]y[/]同意 · [bold]n[/]拒绝 · [bold]a[/]总是 · [bold]ESC[/]取消"
        ))

    def _get_status_ansi(self):
        """Persistent mode + model + skill_mode + token-usage status line."""
        icons = {"default": "🛡", "safe": "🚦", "auto": "⚡"}
        colors = {"default": "cyan", "safe": "yellow", "auto": "magenta"}
        icon = icons.get(self.permission_mode, "❓")
        color = colors.get(self.permission_mode, "white")
        approved = ""
        if self._auto_approved:
            approved = (
                f"  [dim]·[/] [dim]auto-approved: "
                f"{', '.join(sorted(self._auto_approved))}[/]"
            )
        # v1.7.5: session token usage (carried across model switches)
        tokens = ""
        client = getattr(self.agent, "_client", None)
        if client is not None and getattr(client, "n_calls", 0) > 0:
            total = client.total_tokens
            disp = f"{total/1000:.1f}k" if total >= 1000 else str(total)
            tokens = (
                f"  [dim]·[/] [dim]🪙 {disp} tok "
                f"(↑{client.total_prompt_tokens} ↓{client.total_completion_tokens}, "
                f"{client.n_calls} calls)[/]"
            )
        watch = ""
        if self.watch_enabled:
            from financial_analyst.buddy.alerts import market_session
            sess = market_session()
            sess_label = {"open": "交易中", "lunch": "午休",
                          "closed": "已收盘", "weekend": "休市"}.get(sess, sess)
            sess_color = "green" if sess == "open" else "dim"
            watch = (
                f"  [dim]·[/] [green]👁 盯盘 {self.watch_interval // 60}m[/] "
                f"[{sess_color}]({sess_label})[/]"
            )
        # v1.9.0: skill mode indicator
        skill_mode_str = ""
        if self.skill_mode == "auto":
            skill_mode_str = "  [dim]·[/] [magenta]🔧 skill:auto[/]"
        else:
            skill_mode_str = "  [dim]·[/] [dim]🔧 skill:manual[/]"
        return ANSI(_rich_to_ansi(
            f"  [{color}]{icon} {self.permission_mode}[/] "
            f"[dim]·[/] [bold]{self.model}[/] [dim]({self.provider})[/]"
            f"{skill_mode_str}{watch}{tokens}{approved}"
        ))

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
        # v1.7.4: when a confirm modal is pending, the user's input is
        # interpreted as the y/n/a response instead of a new prompt.
        if self._pending_confirm is not None and not self._pending_confirm.done():
            self._handle_confirm_response(text)
            return False
        self.submit(text)
        return False

    def _handle_confirm_response(self, text: str) -> None:
        """Resolve the pending tool-confirmation future from a y/n/a reply.

        Acceptance grammar (case-insensitive; Chinese aliases also accepted):
          y / yes / 是 / 同意   → True
          n / no  / 否 / 取消   → False
          a / always / 总是     → True + remember tool name for session
        Anything else → re-prompt (don't resolve the future).
        """
        ans = text.lower().strip()
        future = self._pending_confirm
        tool_name = self._pending_confirm_tool or ""
        if future is None or future.done():
            return
        if ans in ("y", "yes", "是", "同意"):
            self._pending_confirm = None
            self._pending_confirm_tool = None
            self._append_rich(f"[green]✓ 已同意[/] {tool_name}")
            future.set_result(True)
        elif ans in ("n", "no", "否", "取消"):
            self._pending_confirm = None
            self._pending_confirm_tool = None
            self._append_rich(f"[red]✗ 已拒绝[/] {tool_name}")
            future.set_result(False)
        elif ans in ("a", "always", "总是"):
            if tool_name:
                self._auto_approved.add(tool_name)
            self._pending_confirm = None
            self._pending_confirm_tool = None
            self._append_rich(
                f"[green]✓ 已同意 + 本会话内 [italic]{tool_name}[/] 自动通过[/]"
            )
            future.set_result(True)
        else:
            # Unrecognised response — keep waiting; re-print the prompt.
            # (Input during a pending confirm is interpreted as the y/n/a
            # answer, NOT queued as a new prompt — answer or ESC first.)
            self._append_rich(
                f"[yellow]正在等待工具确认 — 请输入 [bold]y[/]同意 / [bold]n[/]拒绝 / "
                f"[bold]a[/]总是, 或按 [bold]ESC[/] 取消. 你输入的是: "
                f"[italic]{_escape_markup(text)}[/][/]"
            )

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
            # If something was already queued, warn that it's being replaced
            # so the user doesn't lose track.
            prior = self.queued_input
            if prior is not None and prior != text:
                short = (prior[:40] + "…") if len(prior) > 40 else prior
                self._append_rich(
                    f"  [yellow]⚠ 之前排队的 [italic]{_escape_markup(short)}[/] 被替换为新输入[/]"
                )
            self.queued_input = text
            self._append_rich(
                "  [yellow]…已排队 (输入框上方有 ⏳ 提示)[/]"
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

        # v1.6.3+: track per-event counts so the end-of-turn marker can
        # discriminate between (a) LLM call failed, (b) LLM said nothing,
        # (c) LLM chained tools but never wrote a summary, (d) normal.
        text_count = 0
        tool_count = 0
        error_count = 0
        cancelled = False
        last_error_msg: Optional[str] = None

        try:
            async for evt in self.agent.run_turn(text, confirm_callback=self._confirm):
                self._handle_event(evt)
                if evt.kind == "text" and evt.payload:
                    text_count += 1
                elif evt.kind == "tool_call":
                    tool_count += 1
                elif evt.kind == "error":
                    error_count += 1
                    last_error_msg = str(evt.payload or "")
                if self.application is not None:
                    self.application.invalidate()
        except asyncio.CancelledError:
            cancelled = True
            self._append_rich("[yellow]✗ 已取消[/]")
            raise
        finally:
            self.spinner_visible = False

            # Clear marker so the user knows the turn ended + why.
            if not cancelled:
                if error_count > 0 and last_error_msg and "LLM 调用失败" in last_error_msg:
                    # v1.6.4: LLM API call exhausted retries — distinct
                    # from "LLM said nothing", needs different fix
                    # (network / API key / DashScope status).
                    self._append_rich(
                        f"[red]✗ LLM 网络/API 错误 — 上面有详情. "
                        f"检查: DASHSCOPE_API_KEY 是否有效 / 网络是否通畅 / "
                        f"DashScope 服务是否正常.[/]"
                    )
                elif error_count > 0 and last_error_msg and "tool 调用上限" in last_error_msg:
                    # Already showed clear remediation in the error itself
                    self._append_rich(
                        "[yellow]✗ 达上限退出 — 见上面提示, 可再问一句让 LLM 补总结[/]"
                    )
                elif text_count == 0 and tool_count > 0:
                    self._append_rich(
                        f"[yellow]⚠ 完成 (调了 {tool_count} 个 tool 但没文字总结) — "
                        f"试试再问一句 '前面的结果总结一下' 让 LLM 再补输出[/]"
                    )
                elif text_count == 0 and tool_count == 0 and error_count == 0:
                    self._append_rich(
                        "[yellow]⚠ 完成 (LLM 返回空响应) — 可能 prompt 太抽象, 换个具体问法?[/]"
                    )
                else:
                    self._append_rich("[green]✓ 完成[/]")

            if self.application is not None:
                self.application.invalidate()

            # v1.9.3: background skill review is now handled by BuddyAgent,
            # which covers both TUI and SSE paths. See agent.py _after_turn().

            # Run any queued prompt.
            if self.queued_input is not None:
                queued = self.queued_input
                self.queued_input = None
                self._append_rich(f"[dim]→ 处理排队的输入: [italic]{_escape_markup(queued)}[/][/]")
                # Don't await — start as fire-and-forget task. We MUST
                # be inside a running loop here (this coroutine is
                # itself a Task on the loop).
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
        # If a confirm prompt is open, cancel its future first so
        # _confirm() raises CancelledError and the turn unwinds cleanly.
        if self._pending_confirm is not None and not self._pending_confirm.done():
            self._pending_confirm.cancel()
            self._pending_confirm = None
            self._pending_confirm_tool = None
        task = self.current_turn_task
        if task is not None and not task.done():
            task.cancel()
        # The _run_turn finally block writes the "Cancelled" marker and
        # drains queued_input (if any) by starting the next turn.

    async def _confirm(self, tool_name: str, args: dict) -> bool:
        """Permission gate for tool execution.

        Behaviour depends on ``self.permission_mode``:
          - ``auto``: no prompt, always run
          - ``default``: run instant/seconds tools; ask before minutes-level
          - ``safe``: ask before EVERY tool

        Independent of mode, a tool the user previously said "always"
        for in this session passes through silently.

        The actual y/n prompt lives in the input field — we set
        ``_pending_confirm`` to an asyncio.Future, append a prompt to
        the transcript, and return when the user types one of
        y/n/a. ESC during the wait cancels the whole turn (the future
        gets a CancelledError which we surface as False).
        """
        # Session-wide "always" passthrough
        if tool_name in self._auto_approved:
            return True

        if self.permission_mode == "auto":
            return True

        tool = None
        try:
            from financial_analyst.buddy.tools import get_tool
            tool = get_tool(tool_name)
        except Exception:
            pass
        cost = tool.cost_hint if tool else "?"

        if self.permission_mode == "default":
            # Only minutes-level tools need confirmation
            if cost != "minutes" and not (tool and tool.confirm_required):
                return True

        # safe mode, or default + minutes-level → modal prompt
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # No event loop (test path) — fall back to auto-approve so
            # programmatic callers don't deadlock.
            return True

        # Format args concisely for the prompt
        try:
            args_short = json.dumps(args, ensure_ascii=False)
        except Exception:
            args_short = str(args)
        if len(args_short) > 80:
            args_short = args_short[:77] + "…"

        self._pending_confirm = loop.create_future()
        self._pending_confirm_tool = tool_name
        self._append_rich(
            f"[yellow]⚠ 工具确认 ({self.permission_mode} 模式 / cost={cost}):[/] "
            f"[bold]{tool_name}[/]({_escape_markup(args_short)})\n"
            f"  输入 [bold]y[/]同意 · [bold]n[/]拒绝 · [bold]a[/]同意并本会话内自动通过"
        )
        if self.application is not None:
            try:
                self.application.invalidate()
            except Exception:
                pass
        try:
            return await self._pending_confirm
        except asyncio.CancelledError:
            # ESC during wait — propagate so the turn cancels cleanly.
            self._pending_confirm = None
            self._pending_confirm_tool = None
            raise

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
        if cmd == "/mode":
            return self._handle_mode_cmd(rest)
        if cmd == "/model":
            return self._handle_model_cmd(rest)
        if cmd == "/watch":
            return self._handle_watch_cmd(rest)
        if cmd == "/save":
            target = (rest[0] if rest else "buddy_chat.md").strip()
            self._save_transcript(Path(target))
            self._append_rich(f"[dim]已保存到 {target}[/]")
            return True
        if cmd == "/skill":
            return self._handle_skill_cmd(rest)
        return False

    def _handle_mode_cmd(self, rest: List[str]) -> bool:
        """``/mode`` (show) or ``/mode <default|safe|auto>`` (set)."""
        valid = ("default", "safe", "auto")
        if not rest:
            descriptions = {
                "default": "Ask only before minutes-level tools (run_report, alpha_bench).",
                "safe": "Ask before EVERY tool call. Step-by-step approval.",
                "auto": "Run everything without prompting. No safety net.",
            }
            lines = [f"[bold]当前权限模式:[/] [cyan]{self.permission_mode}[/]"]
            lines.append("可选:")
            for m in valid:
                marker = " [green]✓[/]" if m == self.permission_mode else ""
                lines.append(f"  [cyan]{m}[/]{marker} — [dim]{descriptions[m]}[/]")
            lines.append("[dim]用法: /mode default | safe | auto[/]")
            self._append_rich("\n".join(lines))
            return True
        target = rest[0].strip().lower()
        if target not in valid:
            self._append_rich(
                f"[red]未知模式: {_escape_markup(target)}[/] (合法: {', '.join(valid)})"
            )
            return True
        if target == self.permission_mode:
            self._append_rich(f"[dim]已是 {target} 模式[/]")
            return True
        prev = self.permission_mode
        self.permission_mode = target
        # auto mode resets manual approvals (they're redundant)
        if target == "auto":
            self._auto_approved.clear()
        self._save_prefs()
        self._append_rich(
            f"[green]权限模式: {prev} → [bold]{target}[/][/] [dim](已保存)[/]"
        )
        return True

    def _handle_watch_cmd(self, rest: List[str]) -> bool:
        """``/watch`` (status) · ``/watch on [min] [sources]`` · ``/watch off``.

        Examples:
          /watch on              → watch on, default 5-min interval, no auto-collect
          /watch on 3            → 3-min interval
          /watch on 5 ths-fund-flow,xueqiu-hot
                                 → 5-min interval + collect these sources each tick
          /watch off
        """
        from financial_analyst.buddy.alerts import AlertStore
        if not rest:
            n_alerts = len(AlertStore())
            if self.watch_enabled:
                src = self.watch_sources or "(不自动采集)"
                self._append_rich(
                    f"[green]👁 盯盘中[/] · 间隔 {self.watch_interval // 60} 分钟 · "
                    f"采集源: {src} · {n_alerts} 条提醒"
                )
            else:
                self._append_rich(
                    f"[dim]👁 盯盘未开. /watch on 开启. 当前 {n_alerts} 条提醒.[/]\n"
                    f"[dim]用法: /watch on [分钟] [采集源]  ·  /watch off[/]"
                )
            return True
        arg = rest[0].strip().lower()
        tokens = rest[0].split()
        sub = tokens[0].lower()
        if sub == "off":
            self._stop_watch()
            self._append_rich("[yellow]👁 盯盘已关[/]")
            return True
        if sub == "on":
            # parse optional interval (minutes) + sources
            interval_min = 5
            sources = None
            if len(tokens) >= 2:
                try:
                    interval_min = max(1, int(tokens[1]))
                except ValueError:
                    sources = tokens[1]
            if len(tokens) >= 3:
                sources = tokens[2]
            self.watch_interval = interval_min * 60
            self.watch_sources = sources
            self.watch_enabled = True
            started = self._start_watch()
            if not started:
                self.watch_enabled = False
                self._append_rich(
                    "[red]无法启动盯盘 — 不在运行的 event loop 里 "
                    "(需要在 chat 会话内 /watch on)[/]"
                )
                return True
            n_alerts = len(AlertStore())
            src = sources or "(不自动采集, 只评估已有数据)"
            from financial_analyst.buddy.alerts import market_session
            sess = market_session()
            sess_note = ""
            if sess != "open":
                label = {"lunch": "午休", "closed": "已收盘",
                         "weekend": "周末休市"}.get(sess, sess)
                sess_note = (
                    f"\n[yellow]⚠ 当前 {label}, 盯盘会等到开盘 (9:30-11:30 / 13:00-15:00) 才评估.[/]"
                )
            self._append_rich(
                f"[green]👁 盯盘已开[/] · 间隔 {interval_min} 分钟 · "
                f"采集源: {src} · 当前 {n_alerts} 条提醒\n"
                f"[dim]触发的提醒会弹进这里. /watch off 关闭.[/]{sess_note}"
            )
            return True
        self._append_rich(f"[red]未知 /watch 参数: {_escape_markup(arg)}[/] (on / off)")
        return True

    def _handle_skill_cmd(self, rest: List[str]) -> bool:
        """``/skill`` — autonomous skill generation.

        Subcommands:
          /skill generate <desc>  → generate a new skill
          /skill list             → show pending proposals
          /skill review <t>/<n>   → show proposal code
          /skill accept <t>/<n>   → deploy
          /skill reject <t>/<n>   → delete
          /skill config [auto|manual] → show/set skill mode
        """
        sub = rest[0] if rest else ""
        if not sub:
            auto_marker = " [green]✓[/]" if self.skill_mode == "auto" else ""
            manual_marker = " [green]✓[/]" if self.skill_mode == "manual" else ""
            self._append_rich(
                "[bold]/skill[/] 自主技能生成\n"
                "  [cyan]/skill generate <描述>[/] — 生成新技能\n"
                "  [cyan]/skill list[/]             — 查看待审批提案\n"
                "  [cyan]/skill review <类型>/<名称>[/] — 查看提案代码\n"
                "  [cyan]/skill accept <类型>/<名称>[/] — 部署技能\n"
                "  [cyan]/skill reject <类型>/<名称>[/] — 删除提案\n"
                "  [cyan]/skill status[/]           — 统计提案\n"
                "  [cyan]/skill config [auto|manual][/] — 技能生成模式\n"
                f"    当前: auto{auto_marker}  manual{manual_marker}"
            )
            return True

        if sub == "generate":
            description = " ".join(rest[1:]) if len(rest) > 1 else ""
            if not description:
                self._append_rich("[red]用法: /skill generate <描述>[/]")
                return True
            self._append_rich(f"[dim]正在生成技能: {description[:40]}...[/]")
            try:
                from financial_analyst.skill_gen import SkillGenerator, save_proposal
                import asyncio
                loop = asyncio.get_event_loop()
                gen = SkillGenerator()
                proposal = loop.run_until_complete(gen.generate(description=description))
                dest = save_proposal(proposal)
                self._append_rich(
                    f"[green]已生成 [{proposal.skill_type.value}] {proposal.name}[/]\n"
                    f"  标题: {proposal.title}\n"
                    f"  置信度: {proposal.confidence}\n"
                    f"  [dim]审查: /skill review {proposal.skill_type.value}/{proposal.name}[/]"
                )
            except Exception as exc:
                self._append_rich(f"[red]生成失败: {exc}[/]")
            return True

        if sub == "list":
            from financial_analyst.skill_gen import list_proposals
            proposals = list_proposals()
            if not proposals:
                self._append_rich("[dim]暂无待审批的技能提案[/]")
                return True
            lines = ["[bold]待审批技能提案:[/]"]
            for p in proposals:
                lines.append(f"  [{p.skill_type.value}] [cyan]{p.name}[/] — {p.title[:50]}")
            self._append_rich("\n".join(lines))
            return True

        if sub in ("review", "accept", "reject"):
            if len(rest) < 2:
                self._append_rich(f"[red]用法: /skill {sub} <类型>/<名称>[/]")
                return True
            target = rest[1]
            if "/" not in target:
                self._append_rich("[red]参数格式: <类型>/<名称> (如 tool/convertible_bond)[/]")
                return True
            type_str, name = target.split("/", 1)
            from financial_analyst.skill_gen import SkillType, load_proposal, accept_proposal, reject_proposal
            try:
                st = SkillType(type_str)
            except ValueError:
                self._append_rich(f"[red]未知类型: {type_str} (agent/tool/preset)[/]")
                return True

            if sub == "review":
                proposal = load_proposal(name, st)
                if proposal is None:
                    self._append_rich(f"[yellow]未找到提案: {target}[/]")
                    return True
                lang = "yaml" if st.value == "preset" else "python"
                self._append_rich(
                    f"[bold]{proposal.title}[/] [{st.value}]\n"
                    f"[dim]{proposal.description}[/]\n\n"
                    f"```{lang}\n{proposal.generated_code[:3000]}\n```"
                )
                return True

            if sub == "accept":
                result = accept_proposal(name, st)
                if "error" in result:
                    self._append_rich(f"[red]Error: {result['error']}[/]")
                else:
                    self._append_rich(f"[green]已部署 {st.value}/{name}[/]")
                return True

            if sub == "reject":
                result = reject_proposal(name, st)
                if "error" in result:
                    self._append_rich(f"[red]Error: {result['error']}[/]")
                else:
                    self._append_rich(f"[yellow]已删除提案 {st.value}/{name}[/]")
                return True

        if sub == "status":
            from financial_analyst.skill_gen import list_proposals, SkillType
            proposals = list_proposals()
            if not proposals:
                self._append_rich("[dim]暂无待审批的技能提案[/]")
                return True
            counts: dict[str, int] = {}
            for p in proposals:
                counts[p.skill_type.value] = counts.get(p.skill_type.value, 0) + 1
            parts = [f"[bold]待审批提案: {len(proposals)}[/]"]
            for st in SkillType:
                n = counts.get(st.value, 0)
                parts.append(f"  {st.value}: {n}")
            self._append_rich("\n".join(parts))
            return True

        if sub == "config":
            mode_arg = rest[1] if len(rest) > 1 else ""
            if not mode_arg:
                other = "auto" if self.skill_mode == "manual" else "manual"
                self._append_rich(
                    f"[bold]技能生成模式:[/] [cyan]{self.skill_mode}[/]\n"
                    f"  auto   — 后台审查后自动部署，不需确认\n"
                    f"  manual — 生成提案保存到 _proposed/，需人工审查\n"
                    f"[dim]切换: /skill config {other}[/]"
                )
                return True
            mode_arg = mode_arg.strip().lower()
            if mode_arg not in ("auto", "manual"):
                self._append_rich(f"[red]无效模式: {mode_arg}. 请用 auto 或 manual.[/]")
                return True
            prev = self.skill_mode
            self.skill_mode = mode_arg
            self.agent.skill_mode = mode_arg
            self._save_prefs()
            self._append_rich(
                f"[green]技能生成模式: {prev} → [bold]{mode_arg}[/][/] [dim](已保存)[/]"
            )
            if mode_arg == "auto":
                self._append_rich(
                    "[yellow]⚠ auto 模式: 技能将由后台审查自动部署，不需确认.[/]\n"
                    "[dim]审查记录: ~/.financial-analyst/audit.jsonl[/]"
                )
            return True

        self._append_rich(f"[red]未知 /skill 参数: {sub}[/]")
        return True

    def _handle_model_cmd(self, rest: List[str]) -> bool:
        """``/model`` (list) or ``/model <name>`` (switch).

        Accepts either bare model name (e.g. ``qwen3-max``) or
        ``provider/model`` form (``anthropic/claude-opus-4-7``). For
        bare names we search the configured providers in order.
        """
        try:
            available = self.agent._client.list_models()
        except Exception as exc:
            self._append_rich(f"[red]无法列出模型: {exc}[/]")
            return True
        if not rest:
            lines = [f"[bold]当前模型:[/] [cyan]{self.model}[/] [dim]({self.provider})[/]"]
            lines.append("可用:")
            for prov, models in available.items():
                lines.append(f"  [bold]{prov}[/]")
                for m in models:
                    marker = " [green]✓[/]" if (m == self.model and prov == self.provider) else ""
                    lines.append(f"    [cyan]{m}[/]{marker}")
            lines.append("[dim]用法: /model <name>  或  /model <provider>/<model>[/]")
            self._append_rich("\n".join(lines))
            return True
        spec = rest[0].strip()
        new_provider: Optional[str] = None
        new_model: Optional[str] = None
        if "/" in spec:
            p, m = spec.split("/", 1)
            if p in available and m in available[p]:
                new_provider, new_model = p, m
        else:
            for p, models in available.items():
                if spec in models:
                    new_provider, new_model = p, spec
                    break
        if new_model is None:
            self._append_rich(
                f"[red]未找到模型 [italic]{_escape_markup(spec)}[/][/] — "
                f"运行 /model 看可用列表"
            )
            return True
        prev = f"{self.provider}/{self.model}"
        self.provider = new_provider
        self.model = new_model
        self.agent._client = self.agent._client.with_overrides(
            provider=new_provider, model=new_model,
        )
        self._save_prefs()
        self._append_rich(
            f"[green]切换模型: {prev} → [bold]{new_provider}/{new_model}[/][/] [dim](已保存)[/]"
        )
        return True

    def _save_transcript(self, path: Path) -> None:
        # Strip ANSI codes for the markdown file.
        import re
        ansi_re = re.compile(r"\x1b\[[0-9;]*m")
        plain = "".join(ansi_re.sub("", c) for c in self.transcript_chunks)
        path.write_text(plain, encoding="utf-8")

    # ----- v1.9.3: background skill review moved to BuddyAgent --------------
    # See agent.py _after_turn() / _run_background_skill_review().
    # The agent handles both TUI and SSE paths, building its snapshot from
    # self.messages instead of the Rich transcript.

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
            # v1.8.0: tear down the watch loop on exit
            if self.watch_task is not None and not self.watch_task.done():
                self.watch_task.cancel()
                try:
                    await self.watch_task
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
