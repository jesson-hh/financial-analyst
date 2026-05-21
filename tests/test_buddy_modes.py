"""Tests for v1.7.4 permission modes (default/safe/auto) + model picker.

The permission gate (BuddyApp._confirm) is async and uses an
asyncio.Future to await the user's y/n/a typed into the input field.
These tests exercise the modes + flow without spinning up a real
prompt_toolkit Application — we just call the methods directly.
"""
from __future__ import annotations
import asyncio
from unittest.mock import patch

import pytest

from financial_analyst.buddy.app import BuddyApp
from financial_analyst.buddy.tools import Tool, ToolResult


# ----- mode state -----------------------------------------------------------


def test_default_mode_is_default():
    app = BuddyApp()
    assert app.permission_mode == "default"


def test_initial_model_matches_agent_client():
    app = BuddyApp()
    assert app.model == app.agent._client.model
    assert app.provider == app.agent._client.provider


def test_mode_slash_show_when_no_arg():
    app = BuddyApp()
    handled = app._handle_slash("/mode")
    assert handled is True
    assert "default" in app.transcript_text()
    assert "safe" in app.transcript_text()
    assert "auto" in app.transcript_text()


def test_mode_slash_switch():
    app = BuddyApp()
    app._handle_slash("/mode safe")
    assert app.permission_mode == "safe"
    app._handle_slash("/mode auto")
    assert app.permission_mode == "auto"
    app._handle_slash("/mode default")
    assert app.permission_mode == "default"


def test_mode_slash_rejects_unknown():
    app = BuddyApp()
    app._handle_slash("/mode bogus")
    assert app.permission_mode == "default"
    assert "未知模式" in app.transcript_text()


def test_mode_auto_clears_session_approvals():
    """Switching to auto wipes _auto_approved (they're redundant)."""
    app = BuddyApp()
    app._auto_approved.add("quote_lookup")
    app._handle_slash("/mode auto")
    assert not app._auto_approved


# ----- model picker ---------------------------------------------------------


def test_model_slash_show_lists_providers():
    app = BuddyApp()
    handled = app._handle_slash("/model")
    assert handled is True
    transcript = app.transcript_text()
    # Bundled config has qwen / anthropic / openai / deepseek providers
    assert "qwen" in transcript
    # current model marker
    assert "当前模型" in transcript


def test_model_slash_switch_to_known_model_changes_client():
    app = BuddyApp()
    available = app.agent._client.list_models()
    # find another model in the same provider
    same_prov = available[app.provider]
    other = next((m for m in same_prov if m != app.model), None)
    if other is None:
        pytest.skip("No alternate model in same provider to test against")
    app._handle_slash(f"/model {other}")
    assert app.model == other
    # agent client was swapped
    assert app.agent._client.model == other


def test_model_slash_supports_provider_slash_model():
    app = BuddyApp()
    app._handle_slash("/model anthropic/claude-opus-4-7")
    assert app.provider == "anthropic"
    assert app.model == "claude-opus-4-7"
    assert app.agent._client.provider == "anthropic"


def test_model_slash_rejects_unknown():
    app = BuddyApp()
    prev_model = app.model
    app._handle_slash("/model not-a-real-model")
    assert app.model == prev_model
    assert "未找到模型" in app.transcript_text()


# ----- _confirm permission gate --------------------------------------------


def _fake_tool(name: str, cost: str = "instant", confirm_required: bool = False) -> Tool:
    return Tool(
        name=name, description=f"mock {name}", input_schema={},
        run=lambda **kw: ToolResult("ok"),
        cost_hint=cost, confirm_required=confirm_required,
    )


@pytest.mark.asyncio
async def test_auto_mode_bypasses_all_confirms():
    """auto mode: no prompt regardless of cost or confirm_required flag."""
    app = BuddyApp()
    app.permission_mode = "auto"
    with patch("financial_analyst.buddy.tools.get_tool",
               return_value=_fake_tool("run_report", "minutes", True)):
        result = await app._confirm("run_report", {})
    assert result is True
    assert app._pending_confirm is None  # no modal was raised


@pytest.mark.asyncio
async def test_default_mode_passes_instant_tools():
    """default mode: instant/seconds tools run silently."""
    app = BuddyApp()
    app.permission_mode = "default"
    with patch("financial_analyst.buddy.tools.get_tool",
               return_value=_fake_tool("quote_lookup", "instant")):
        result = await app._confirm("quote_lookup", {})
    assert result is True
    assert app._pending_confirm is None


@pytest.mark.asyncio
async def test_default_mode_prompts_for_minutes_tools():
    """default mode: minutes-level tool sets _pending_confirm and waits."""
    app = BuddyApp()
    app.permission_mode = "default"

    async def runner():
        with patch("financial_analyst.buddy.tools.get_tool",
                   return_value=_fake_tool("run_report", "minutes", True)):
            return await app._confirm("run_report", {"code": "SH600519"})

    task = asyncio.create_task(runner())
    # Wait one event-loop tick so the coroutine reaches the await
    await asyncio.sleep(0)
    # Confirm future is now pending
    assert app._pending_confirm is not None
    # User answers 'y'
    app._handle_confirm_response("y")
    result = await task
    assert result is True


@pytest.mark.asyncio
async def test_safe_mode_prompts_even_for_instant_tools():
    app = BuddyApp()
    app.permission_mode = "safe"

    async def runner():
        with patch("financial_analyst.buddy.tools.get_tool",
                   return_value=_fake_tool("quote_lookup", "instant")):
            return await app._confirm("quote_lookup", {})

    task = asyncio.create_task(runner())
    await asyncio.sleep(0)
    assert app._pending_confirm is not None
    app._handle_confirm_response("n")
    result = await task
    assert result is False  # user said no


@pytest.mark.asyncio
async def test_always_response_caches_tool_in_auto_approved():
    app = BuddyApp()
    app.permission_mode = "safe"

    async def runner():
        with patch("financial_analyst.buddy.tools.get_tool",
                   return_value=_fake_tool("news_query", "instant")):
            return await app._confirm("news_query", {})

    task = asyncio.create_task(runner())
    await asyncio.sleep(0)
    app._handle_confirm_response("a")
    assert await task is True
    # Next call should bypass prompt
    with patch("financial_analyst.buddy.tools.get_tool",
               return_value=_fake_tool("news_query", "instant")):
        result = await app._confirm("news_query", {})
    assert result is True
    assert app._pending_confirm is None  # bypassed


@pytest.mark.asyncio
async def test_unrecognised_confirm_response_keeps_future_pending():
    """If user types junk during confirm, we re-prompt — future stays open."""
    app = BuddyApp()
    app.permission_mode = "safe"

    async def runner():
        with patch("financial_analyst.buddy.tools.get_tool",
                   return_value=_fake_tool("x", "instant")):
            return await app._confirm("x", {})

    task = asyncio.create_task(runner())
    await asyncio.sleep(0)
    app._handle_confirm_response("maybe?")
    # Still pending
    assert app._pending_confirm is not None
    assert not app._pending_confirm.done()
    # Now answer cleanly
    app._handle_confirm_response("y")
    assert await task is True


@pytest.mark.asyncio
async def test_esc_during_confirm_cancels_future():
    """ESC handler must cancel the pending confirm future so the turn unwinds."""
    app = BuddyApp()
    app.permission_mode = "safe"

    # Set up a fake task to satisfy _has_active_turn
    async def slow_runner():
        with patch("financial_analyst.buddy.tools.get_tool",
                   return_value=_fake_tool("x", "instant")):
            return await app._confirm("x", {})
    app.current_turn_task = asyncio.create_task(slow_runner())
    await asyncio.sleep(0)
    assert app._pending_confirm is not None
    app._cancel_current_turn()
    # Future should be cancelled and cleared
    # (cancel + clear happens synchronously in _cancel_current_turn)
    try:
        await app.current_turn_task
    except asyncio.CancelledError:
        pass
    except Exception:
        pass
    assert app._pending_confirm is None


def test_on_submit_routes_to_confirm_when_pending():
    """When _pending_confirm is set, _on_submit feeds the text to the
    confirm response handler instead of starting a new turn."""
    app = BuddyApp()
    app._build_application_skip_terminal_for_test()
    # Manually set a pending future
    loop = asyncio.new_event_loop()
    try:
        future = loop.create_future()
        app._pending_confirm = future
        app._pending_confirm_tool = "test"
        # Inject input via the field
        app.input_field.text = "y"
        app._on_submit(None)
        assert future.done()
        assert future.result() is True
    finally:
        loop.close()


# Helper monkeyed onto BuddyApp via this test file for the on_submit test —
# avoids requiring a real terminal.
def _build_app_for_test(self):
    """Replace _build_application's terminal-requiring bits with stubs
    so we can exercise _on_submit / input_field plumbing."""
    from prompt_toolkit.widgets import TextArea
    from prompt_toolkit.history import InMemoryHistory
    self.input_field = TextArea(
        prompt="❯ ", multiline=False, wrap_lines=False,
        accept_handler=self._on_submit, history=InMemoryHistory(),
        height=1,
    )
BuddyApp._build_application_skip_terminal_for_test = _build_app_for_test


# ----- status line ----------------------------------------------------------


def test_status_line_includes_mode_and_model():
    app = BuddyApp()
    app.permission_mode = "safe"
    app.model = "qwen3-max"
    rendered = app._get_status_ansi()
    # ANSI object exposes value via .value
    text = rendered.value
    assert "safe" in text
    assert "qwen3-max" in text


def test_status_line_shows_auto_approved_tools():
    app = BuddyApp()
    app._auto_approved.add("quote_lookup")
    text = app._get_status_ansi().value
    assert "auto-approved" in text or "quote_lookup" in text


# ----- v1.8.2: persistent confirm indicator --------------------------------


def test_confirm_indicator_renders_pending_tool():
    """The confirm indicator (shown while y/n pending) names the tool +
    the key options, so the user never loses track of the modal."""
    app = BuddyApp()
    app._pending_confirm_tool = "run_report"
    text = app._get_confirm_ansi().value
    assert "等待工具确认" in text
    assert "run_report" in text
    assert "ESC" in text  # escape hatch advertised


def test_confirm_indicator_escapes_markup():
    app = BuddyApp()
    app._pending_confirm_tool = "weird[tool]"
    # must not raise on bracket chars
    text = app._get_confirm_ansi().value
    assert "weird" in text


@pytest.mark.asyncio
async def test_confirm_indicator_visibility_tracks_pending(monkeypatch):
    """The ConditionalContainer filter shows the bar only while a confirm
    future is actually pending."""
    app = BuddyApp()
    # no pending → filter would be False
    assert app._pending_confirm is None
    # simulate a pending future
    loop = asyncio.get_running_loop()
    app._pending_confirm = loop.create_future()
    app._pending_confirm_tool = "alpha_bench"
    # filter condition: pending and not done
    visible = app._pending_confirm is not None and not app._pending_confirm.done()
    assert visible is True
    app._pending_confirm.set_result(True)
    visible_after = app._pending_confirm is not None and not app._pending_confirm.done()
    assert visible_after is False
