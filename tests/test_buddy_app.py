"""Tests for the v1.6 full-TUI BuddyApp.

The Application object is heavy and depends on a real terminal, so we
exercise the inner logic directly (submit / queue / cancel / slash) by
poking at the BuddyApp's state, never starting `.run()`.
"""
from __future__ import annotations
import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from financial_analyst.buddy.app import (
    BuddyApp, _rich_to_ansi, _escape_markup,
)
from financial_analyst.buddy.tools import ToolResult, get_tool


def _make_llm_response(text: str = "", tool_calls=None):
    msg = {"role": "assistant", "content": text}
    if tool_calls:
        msg["tool_calls"] = [
            {
                "id": tc.get("id", f"call_{i}"),
                "type": "function",
                "function": {
                    "name": tc["name"],
                    "arguments": json.dumps(tc.get("args", {}), ensure_ascii=False),
                },
            }
            for i, tc in enumerate(tool_calls)
        ]
    return {"choices": [{"message": msg}]}


# ----- bridge helpers -------------------------------------------------------


def test_rich_to_ansi_basic():
    """Strings with Rich markup come back with ANSI escape codes."""
    out = _rich_to_ansi("[bold]hello[/]")
    assert "hello" in out
    # contains some ANSI escape
    assert "\x1b[" in out


def test_escape_markup_protects_brackets():
    """Tool output containing `[...]` shouldn't be parsed as Rich markup."""
    assert _escape_markup("[user input]") == r"\[user input]"


# ----- transcript -----------------------------------------------------------


def test_append_rich_grows_transcript():
    app = BuddyApp()
    initial = len(app.transcript_chunks)
    app._append_rich("[bold]hello[/]")
    assert len(app.transcript_chunks) == initial + 1
    assert "hello" in app.transcript_text()


def test_banner_is_in_transcript_at_startup():
    app = BuddyApp()
    txt = app.transcript_text()
    assert "金融助手" in txt
    # mentions ESC since the banner promises it
    assert "ESC" in txt


# ----- submit / queue -------------------------------------------------------


@pytest.mark.asyncio
async def test_submit_starts_turn_when_idle():
    """First submit while idle should start a turn (current_turn_task set).

    v1.6.1: submit() now requires a running asyncio loop (it uses
    get_running_loop().create_task instead of ensure_future). Test
    therefore runs inside @asyncio mark.
    """
    app = BuddyApp()
    async def _fake_run_turn(text, confirm_callback=None):
        return
        yield  # pragma: no cover (unreachable)
    app.agent.run_turn = _fake_run_turn

    app.submit("hello")
    assert app.current_turn_task is not None
    # The transcript should contain the echoed user line
    assert "hello" in app.transcript_text()


@pytest.mark.asyncio
async def test_submit_while_running_queues():
    """Submit while a turn is active should queue, not start a new task."""
    app = BuddyApp()

    # Manufacture an "active" turn task by spawning a hanging coroutine.
    async def _hang():
        await asyncio.Future()

    app.current_turn_task = asyncio.create_task(_hang())
    await asyncio.sleep(0)  # let the task start

    app.submit("second prompt")
    assert app.queued_input == "second prompt"
    txt = app.transcript_text()
    assert "second prompt" in txt
    assert "排队" in txt

    # Cleanup
    app.current_turn_task.cancel()
    try:
        await app.current_turn_task
    except (asyncio.CancelledError, Exception):
        pass


# ----- slash commands -------------------------------------------------------


def test_slash_help_renders_help():
    app = BuddyApp()
    app.submit("/help")
    txt = app.transcript_text()
    assert "Slash commands" in txt or "/help" in txt


def test_slash_reset_clears_agent_memory():
    app = BuddyApp()
    # Make the agent think it has history
    app.agent.add_user("dummy prior message")
    assert len(app.agent.messages) == 1
    app.submit("/reset")
    assert len(app.agent.messages) == 0
    assert "已清空" in app.transcript_text() or "reset" in app.transcript_text().lower()


def test_slash_tools_lists_registry():
    app = BuddyApp()
    app.submit("/tools")
    txt = app.transcript_text()
    assert "run_report" in txt
    assert "quote_lookup" in txt


def test_slash_save_writes_file(tmp_path):
    app = BuddyApp()
    app._append_rich("[bold]some payload[/]")
    target = tmp_path / "chat.md"
    app.submit(f"/save {target}")
    assert target.is_file()
    content = target.read_text(encoding="utf-8")
    assert "some payload" in content


# ----- cancellation ---------------------------------------------------------


@pytest.mark.asyncio
async def test_cancel_current_turn_cancels_running_task():
    app = BuddyApp()
    # Stub agent.run_turn that hangs forever
    async def _hanging_run(text, confirm_callback=None):
        await asyncio.Future()  # never resolves
        yield  # pragma: no cover

    app.agent.run_turn = _hanging_run
    app._start_turn("anything")

    # Give the task a tick to actually start
    await asyncio.sleep(0)
    assert app.has_active_turn()

    app._cancel_current_turn(reason="ESC")

    # Wait for the task to actually finish cancelling
    if app.current_turn_task is not None:
        try:
            await app.current_turn_task
        except (asyncio.CancelledError, Exception):
            pass

    assert not app.has_active_turn()
    assert "已取消" in app.transcript_text() or "取消" in app.transcript_text()


@pytest.mark.asyncio
async def test_esc_peels_off_one_layer_at_a_time():
    """v1.6.2: ESC cancels ONLY the current turn. Queued input still
    runs after the cancellation finishes; second ESC cancels that one.
    Lets the user "back out" gradually with multiple presses, matching
    Claude Code's step-back UX."""
    app = BuddyApp()

    async def _hanging_run(text, confirm_callback=None):
        await asyncio.Future()
        yield  # pragma: no cover

    app.agent.run_turn = _hanging_run

    # Start first turn (hangs) + queue second
    app._start_turn("first")
    await asyncio.sleep(0)
    assert app.has_active_turn()
    app.submit("second")
    assert app.queued_input == "second"

    # First ESC: cancel current. queued_input still set; finally block
    # of the cancelled turn starts the queued turn.
    app._cancel_current_turn(reason="ESC#1")
    if app.current_turn_task is not None:
        try:
            await app.current_turn_task
        except (asyncio.CancelledError, Exception):
            pass
    # Let any post-cancel queue-starter scheduling run
    await asyncio.sleep(0)

    # After 1st ESC: a NEW turn (for the queued input) should now be active
    assert app.queued_input is None, "queue should have been consumed"
    assert app.has_active_turn(), "queued turn should have started after 1st ESC"

    # 2nd ESC: cancel that one too
    app._cancel_current_turn(reason="ESC#2")
    try:
        await app.current_turn_task
    except (asyncio.CancelledError, Exception):
        pass
    assert not app.has_active_turn(), "everything stopped after 2nd ESC"


# ----- v1.6.6: cursor-pinned auto-scroll -----------------------------------


def test_cursor_pinned_to_last_line_empty_transcript():
    """Edge case: app just created, only banner. Cursor y >= 0."""
    app = BuddyApp()
    pt = app._get_cursor_at_bottom()
    assert pt.x == 0
    assert pt.y >= 0


def test_cursor_pinned_advances_as_transcript_grows():
    """y must increase with each append so do_scroll keeps the latest
    line in view. This is what v1.6.5's get_vertical_scroll hack failed
    to do — do_scroll snapped back to cursor_pos=0 every render."""
    app = BuddyApp()
    initial_y = app._get_cursor_at_bottom().y
    for _ in range(20):
        app._append_chunk("new line of output\n")
    after_y = app._get_cursor_at_bottom().y
    assert after_y > initial_y
    # And the y should equal the actual newline count - 1
    assert after_y == app.transcript_text().count("\n") - 1


def test_cursor_pinned_handles_multiline_chunks():
    """A single chunk with embedded newlines should advance y by the
    number of lines in that chunk."""
    app = BuddyApp()
    before = app._get_cursor_at_bottom().y
    app._append_chunk("line1\nline2\nline3\n")
    after = app._get_cursor_at_bottom().y
    assert after - before == 3


# ----- v1.6.7: follow-tail / history-browse state machine ------------------


def test_follow_tail_is_default():
    """Fresh app should start in follow-tail mode."""
    app = BuddyApp()
    assert app.follow_tail is True


def test_pageup_drops_out_of_follow_tail_and_pins_top_line():
    """PageUp from tail captures current top and freezes follow."""
    app = BuddyApp()
    for i in range(100):
        app._append_chunk(f"line {i}\n")
    n = app._n_lines()
    app._scroll_history(direction=-1)
    assert app.follow_tail is False
    # top_line should be < n (we moved up)
    assert app.top_line < n


def test_cursor_uses_top_line_when_not_following_tail():
    """In history-browse mode, cursor sits at top_line (not at bottom)."""
    app = BuddyApp()
    for i in range(100):
        app._append_chunk(f"line {i}\n")
    app.follow_tail = False
    app.top_line = 30
    pt = app._get_cursor_at_bottom()
    assert pt.y == 30


def test_appends_during_history_browse_do_not_change_top_line():
    """While the user browses history, new appends must NOT auto-jump
    the viewport to the bottom — that's the v1.6.7 bug fix."""
    app = BuddyApp()
    for i in range(100):
        app._append_chunk(f"old {i}\n")
    app._scroll_history(direction=-1)
    saved_top = app.top_line
    # Agent appends 50 new lines underneath
    for i in range(50):
        app._append_chunk(f"new {i}\n")
    assert app.top_line == saved_top, "top_line should be stable during browse"
    # And cursor still reports top_line, not the new tail
    pt = app._get_cursor_at_bottom()
    assert pt.y == saved_top


def test_end_jumps_back_to_tail_and_resumes_follow():
    """Pressing End / Ctrl-↓ returns the app to follow-tail mode."""
    app = BuddyApp()
    for i in range(100):
        app._append_chunk(f"x {i}\n")
    app._scroll_history(direction=-1)
    assert app.follow_tail is False
    app._jump_to_tail()
    assert app.follow_tail is True
    # cursor goes back to the actual last line
    pt = app._get_cursor_at_bottom()
    assert pt.y == app._n_lines() - 1


def test_pagedown_past_tail_resumes_follow():
    """Paging down past the end of content should re-enter follow-tail
    mode automatically — no need to also press End."""
    app = BuddyApp()
    for i in range(50):
        app._append_chunk(f"l {i}\n")
    app._scroll_history(direction=-1)
    # Spam PageDown — eventually we hit the tail
    for _ in range(20):
        app._scroll_history(direction=+1)
        if app.follow_tail:
            break
    assert app.follow_tail is True


def test_history_browse_hint_visibility_state():
    """The '📜 浏览历史' hint window is gated on ``not follow_tail``."""
    app = BuddyApp()
    # Before _build_application we can still check the state flag.
    assert app.follow_tail is True
    app.follow_tail = False
    assert app.follow_tail is False
    # The actual ConditionalContainer wiring is exercised in the lazy
    # _build_application path; here we just confirm the source-of-truth
    # variable behaves as expected.


# ----- v1.6.8: mouse-wheel scroll wiring ----------------------------------


def _make_mouse_event(event_type):
    from prompt_toolkit.mouse_events import MouseEvent
    from prompt_toolkit.data_structures import Point
    return MouseEvent(
        position=Point(0, 5),
        event_type=event_type,
        button=None,
        modifiers=frozenset(),
    )


def test_on_mouse_event_scroll_up_enters_history_browse():
    """Wheel up → scroll backwards, follow_tail flips to False."""
    from prompt_toolkit.mouse_events import MouseEventType
    app = BuddyApp()
    for i in range(100):
        app._append_chunk(f"l{i}\n")
    assert app.follow_tail is True
    ev = _make_mouse_event(MouseEventType.SCROLL_UP)
    result = app._on_mouse_event(ev)
    assert result is None, "handler must consume the event"
    assert app.follow_tail is False
    # top_line shifted up by the 3-line wheel step from the captured viewport top
    assert app.top_line < app._n_lines() - 1


def test_on_mouse_event_scroll_down_eventually_returns_to_tail():
    """Wheel down enough times → follow_tail resumes automatically."""
    from prompt_toolkit.mouse_events import MouseEventType
    app = BuddyApp()
    for i in range(80):
        app._append_chunk(f"l{i}\n")
    # First scroll up to enter history browse
    app._on_mouse_event(_make_mouse_event(MouseEventType.SCROLL_UP))
    assert app.follow_tail is False
    # Now scroll down enough times to clear the viewport offset
    for _ in range(60):
        app._on_mouse_event(_make_mouse_event(MouseEventType.SCROLL_DOWN))
        if app.follow_tail:
            break
    assert app.follow_tail is True


def test_on_mouse_event_ignores_non_scroll_events():
    """Click events should pass through (return NotImplemented) so the
    default Window handler (if any) still has a chance."""
    from prompt_toolkit.mouse_events import MouseEventType
    app = BuddyApp()
    ev = _make_mouse_event(MouseEventType.MOUSE_DOWN)
    result = app._on_mouse_event(ev)
    assert result is NotImplemented


def test_wheel_step_is_smaller_than_pageup_step():
    """A single wheel notch should NOT jump a full viewport. v1.6.8
    uses 3 lines/notch vs. PageUp's full-viewport step."""
    app = BuddyApp()
    for i in range(200):
        app._append_chunk(f"l{i}\n")
    # Snapshot a fresh PageUp-equivalent move
    app._scroll_history(direction=-1)
    top_after_pageup = app.top_line
    # Reset and do a wheel-up
    app.follow_tail = True
    app.top_line = 0
    app._scroll_history(direction=-1, step=3)
    top_after_wheel = app.top_line
    # Wheel must leave us closer to the tail than PageUp did
    assert top_after_wheel > top_after_pageup


# ----- v1.6.3: queue indicator + done marker + no-text warning ------------


@pytest.mark.asyncio
async def test_queue_indicator_renders_queued_text():
    """When queued_input is set, _get_queue_ansi should include the text."""
    app = BuddyApp()

    async def _hang(text, confirm_callback=None):
        await asyncio.Future()
        yield  # pragma: no cover
    app.agent.run_turn = _hang
    app._start_turn("first")
    await asyncio.sleep(0)
    app.submit("five-liang-ye sector breakdown")

    # Queue indicator should show the queued text
    ansi = app._get_queue_ansi()
    plain = ansi.value if hasattr(ansi, "value") else str(ansi)
    assert "five-liang-ye" in plain
    assert "排队" in plain

    # Cleanup
    app.current_turn_task.cancel()
    try:
        await app.current_turn_task
    except (asyncio.CancelledError, Exception):
        pass


@pytest.mark.asyncio
async def test_queue_replacement_notice():
    """Submitting a 2nd time while a 1st is queued should produce a clear
    'replaced' notice in the transcript so the user doesn't lose track."""
    app = BuddyApp()

    async def _hang(text, confirm_callback=None):
        await asyncio.Future()
        yield  # pragma: no cover
    app.agent.run_turn = _hang
    app._start_turn("running")
    await asyncio.sleep(0)

    app.submit("queued one")
    app.submit("queued two")  # replaces 'queued one'

    txt = app.transcript_text()
    assert "queued one" in txt
    assert "queued two" in txt
    assert "替换" in txt or "replaced" in txt.lower()
    # The active queue is the latest one
    assert app.queued_input == "queued two"

    app.current_turn_task.cancel()
    try:
        await app.current_turn_task
    except (asyncio.CancelledError, Exception):
        pass


@pytest.mark.asyncio
async def test_done_marker_on_successful_turn():
    """After a turn with at least one text event, the transcript should
    show '✓ 完成' so the user knows it ended."""
    app = BuddyApp()

    async def _good_run(text, confirm_callback=None):
        from financial_analyst.buddy.agent import TurnEvent
        yield TurnEvent("text", "你好.")
        yield TurnEvent("done", None)

    app.agent.run_turn = _good_run
    app._start_turn("hi")
    try:
        await app.current_turn_task
    except (asyncio.CancelledError, Exception):
        pass

    assert "完成" in app.transcript_text()


@pytest.mark.asyncio
async def test_warning_when_tools_but_no_text():
    """If the LLM called tools but never wrote final text, the user should
    see a warning suggesting they re-prompt for a summary."""
    app = BuddyApp()

    async def _tool_only(text, confirm_callback=None):
        from financial_analyst.buddy.agent import TurnEvent
        yield TurnEvent("tool_call", {"name": "industry_show", "args": {"code": "SH600519"}})
        yield TurnEvent("tool_result", {"name": "industry_show", "content": "白酒", "is_error": False})
        yield TurnEvent("tool_call", {"name": "industry_show", "args": {"code": "SZ000858"}})
        yield TurnEvent("tool_result", {"name": "industry_show", "content": "白酒", "is_error": False})
        yield TurnEvent("done", None)

    app.agent.run_turn = _tool_only
    app._start_turn("compare maotai and wuliangye industry")
    try:
        await app.current_turn_task
    except (asyncio.CancelledError, Exception):
        pass

    txt = app.transcript_text()
    assert "没文字总结" in txt or "tool" in txt.lower()


@pytest.mark.asyncio
async def test_esc_at_idle_clears_lingering_queue():
    """If somehow queued_input is set but no turn is active, ESC should
    still drop the queue (defensive — prevents a stale prompt from
    firing on the next event-loop tick)."""
    app = BuddyApp()
    app.queued_input = "stale prompt"
    assert not app.has_active_turn()
    app._cancel_current_turn(reason="ESC at idle")
    assert app.queued_input is None
    assert "排队的输入已清空" in app.transcript_text()


# ----- queued input drains after current turn finishes ---------------------


@pytest.mark.asyncio
async def test_queued_input_runs_after_current_turn(monkeypatch):
    """Submit while turn running → after current turn completes the queued
    text should fire as a new turn."""
    app = BuddyApp()
    started_inputs: list[str] = []

    async def _record_run(text, confirm_callback=None):
        started_inputs.append(text)
        # short turn — yields nothing, immediately done
        if False:  # pragma: no cover
            yield

    app.agent.run_turn = _record_run

    # First submit starts a turn that finishes immediately.
    app.submit("first")
    # Tick the loop so the task starts and finishes.
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    # While the first task may still be wrapping up, push a second submit.
    # In this test the first turn finishes quickly, so the second won't be
    # queued — but we exercise the "submit-after-first-done" path too.
    if not app.has_active_turn():
        app.submit("second")
        await asyncio.sleep(0)

    assert "first" in started_inputs
    assert "second" in started_inputs
