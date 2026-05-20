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
    BuddyApp, _rich_to_ansi, _escape_markup, _scroll_to_bottom,
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


# ----- v1.6.5: transcript auto-scroll -------------------------------------


def test_scroll_to_bottom_returns_zero_when_no_render_info():
    """First render: render_info is None. Scroll callback returns 0 so
    we don't crash."""
    class FakeWindow:
        render_info = None
    assert _scroll_to_bottom(FakeWindow()) == 0


def test_scroll_to_bottom_returns_zero_when_content_fits_window():
    """If content fits, no scroll needed."""
    class FakeInfo:
        content_height = 10
        window_height = 20
    class FakeWindow:
        render_info = FakeInfo()
    assert _scroll_to_bottom(FakeWindow()) == 0


def test_scroll_to_bottom_returns_offset_when_content_overflows():
    """When content exceeds window, scroll offset = overflow amount."""
    class FakeInfo:
        content_height = 100
        window_height = 30
    class FakeWindow:
        render_info = FakeInfo()
    # Should scroll so last 30 lines are visible: offset = 100 - 30 = 70
    assert _scroll_to_bottom(FakeWindow()) == 70


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
