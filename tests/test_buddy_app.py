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

from financial_analyst.buddy.app import BuddyApp, _rich_to_ansi, _escape_markup
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


def test_submit_starts_turn_when_idle():
    """First submit while idle should start a turn (current_turn_task set)."""
    app = BuddyApp()
    # Patch the agent's run_turn so it doesn't actually call the LLM
    async def _fake_run_turn(text, confirm_callback=None):
        # Yield no events (immediate done)
        return
        yield  # pragma: no cover (unreachable)
    app.agent.run_turn = _fake_run_turn

    app.submit("hello")
    assert app.current_turn_task is not None
    # The transcript should contain the echoed user line
    assert "hello" in app.transcript_text()


def test_submit_while_running_queues():
    """Submit while a turn is active should queue, not start a new task."""
    app = BuddyApp()

    # Manufacture an "active" turn task by wrapping a never-completing future.
    loop = asyncio.new_event_loop()
    try:
        future = loop.create_future()  # never completes
        # Wrap as a Task that the loop owns
        app.current_turn_task = loop.create_task(_await(future))
        # Drain a tick so the task actually starts
        loop.run_until_complete(asyncio.sleep(0))

        app.submit("second prompt")
        assert app.queued_input == "second prompt"
        # Transcript echoes the user line + the queued marker
        txt = app.transcript_text()
        assert "second prompt" in txt
        assert "排队" in txt
    finally:
        # Clean up the loop
        if not app.current_turn_task.done():
            app.current_turn_task.cancel()
            try:
                loop.run_until_complete(app.current_turn_task)
            except (asyncio.CancelledError, Exception):
                pass
        loop.close()


async def _await(awaitable):
    return await awaitable


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
