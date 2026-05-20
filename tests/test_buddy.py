"""Tests for the Buddy conversational agent.

LLM is mocked end-to-end so these tests don't hit the network or
require API keys. We exercise the tool-use loop, tool registry,
confirmation gating, and edge cases.
"""
from __future__ import annotations
import json
import asyncio
from typing import Any, Dict, List
from unittest.mock import AsyncMock, patch

import pytest

from financial_analyst.buddy.tools import (
    TOOL_REGISTRY, Tool, ToolResult, get_tool, list_tools,
)
from financial_analyst.buddy.agent import BuddyAgent, TurnEvent


def _make_llm_response(text: str = "", tool_calls: list = None) -> Dict[str, Any]:
    """Build a LiteLLM-shaped response envelope."""
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


# ----- registry sanity -------------------------------------------------------


def test_registry_has_required_tools():
    names = [t.name for t in TOOL_REGISTRY]
    # Core tools that must be present
    for required in ("run_report", "quote_lookup", "news_query",
                     "alpha_bench", "alpha_snapshot", "chain_for",
                     "stocks_show", "industry_show"):
        assert required in names, f"missing required tool: {required}"


def test_get_tool_returns_none_for_unknown():
    assert get_tool("nonexistent_tool") is None


def test_tool_schemas_well_formed():
    for t in TOOL_REGISTRY:
        assert t.name
        assert t.description
        s = t.to_anthropic_schema()
        assert s["name"] == t.name
        assert "input_schema" in s
        # OpenAI schema also valid
        o = t.to_openai_schema()
        assert o["type"] == "function"
        assert o["function"]["name"] == t.name


def test_costly_tools_require_confirmation():
    """`run_report` and `alpha_bench` are minutes-long → require user OK."""
    for name in ("run_report", "alpha_bench"):
        t = get_tool(name)
        assert t is not None
        assert t.confirm_required, f"{name} should be confirm_required"


# ----- agent: single turn no tool -----------------------------------------


@pytest.mark.asyncio
async def test_run_turn_text_only_no_tools():
    """LLM responds with plain text, no tool calls → 1 'text' + 1 'done' event."""
    agent = BuddyAgent()
    with patch("financial_analyst.buddy.agent.LLMClient.for_agent") as mock_factory:
        client = AsyncMock()
        client.chat = AsyncMock(return_value=_make_llm_response(
            text="你好, 我是金融助手. 你想看哪只股?"
        ))
        mock_factory.return_value = client
        agent._client = client

        events = []
        async for evt in agent.run_turn("你好"):
            events.append(evt)

    kinds = [e.kind for e in events]
    assert "text" in kinds
    assert kinds[-1] == "done"


# ----- agent: single tool call + final text -------------------------------


@pytest.mark.asyncio
async def test_run_turn_single_tool_call():
    """LLM calls one tool, gets result, returns final text."""
    agent = BuddyAgent()

    # Mock the tool's run() so we don't actually hit subprocess
    industry_tool = get_tool("industry_show")
    original = industry_tool.run
    industry_tool.run = lambda code: ToolResult(f"{code}: 测试行业")

    try:
        with patch("financial_analyst.buddy.agent.LLMClient.for_agent") as mock_factory:
            client = AsyncMock()
            client.chat = AsyncMock(side_effect=[
                _make_llm_response(tool_calls=[
                    {"name": "industry_show", "args": {"code": "SH600519"}},
                ]),
                _make_llm_response(text="SH600519 是白酒行业."),
            ])
            mock_factory.return_value = client
            agent._client = client

            events = []
            async for evt in agent.run_turn("茅台是什么行业"):
                events.append(evt)
    finally:
        industry_tool.run = original

    kinds = [e.kind for e in events]
    assert "tool_call" in kinds
    assert "tool_result" in kinds
    assert kinds[-1] == "done"

    # Find the tool_call event and verify args
    tc_events = [e for e in events if e.kind == "tool_call"]
    assert tc_events[0].payload["name"] == "industry_show"
    assert tc_events[0].payload["args"] == {"code": "SH600519"}


# ----- agent: tool error doesn't crash loop ---------------------------------


@pytest.mark.asyncio
async def test_tool_error_surfaces_and_loop_continues():
    agent = BuddyAgent()
    industry_tool = get_tool("industry_show")
    original = industry_tool.run

    def _explode(**kwargs):
        raise RuntimeError("synthetic failure")

    industry_tool.run = _explode

    try:
        with patch("financial_analyst.buddy.agent.LLMClient.for_agent") as mock_factory:
            client = AsyncMock()
            client.chat = AsyncMock(side_effect=[
                _make_llm_response(tool_calls=[
                    {"name": "industry_show", "args": {"code": "SH600519"}},
                ]),
                _make_llm_response(text="Tool failed but I recovered."),
            ])
            mock_factory.return_value = client
            agent._client = client

            events = []
            async for evt in agent.run_turn("foo"):
                events.append(evt)
    finally:
        industry_tool.run = original

    # tool_result with is_error=True should be emitted
    result_events = [e for e in events if e.kind == "tool_result"]
    assert len(result_events) == 1
    assert result_events[0].payload["is_error"] is True
    assert "synthetic failure" in result_events[0].payload["content"]
    # Final 'done' still reached
    assert events[-1].kind == "done"


# ----- agent: confirm callback declines costly tool -------------------------


@pytest.mark.asyncio
async def test_confirm_callback_declined_skips_tool():
    agent = BuddyAgent()
    report_tool = get_tool("run_report")
    assert report_tool.confirm_required

    original = report_tool.run
    report_called = [False]

    def _track(**kwargs):
        report_called[0] = True
        return ToolResult("would have run")

    report_tool.run = _track

    async def _decline(name, args):
        return False  # user says no

    try:
        with patch("financial_analyst.buddy.agent.LLMClient.for_agent") as mock_factory:
            client = AsyncMock()
            client.chat = AsyncMock(side_effect=[
                _make_llm_response(tool_calls=[
                    {"name": "run_report", "args": {"code": "SH600519"}},
                ]),
                _make_llm_response(text="OK, 没跑."),
            ])
            mock_factory.return_value = client
            agent._client = client

            events = []
            async for evt in agent.run_turn("跑个研报", confirm_callback=_decline):
                events.append(evt)
    finally:
        report_tool.run = original

    assert not report_called[0], "tool should not have been called after decline"
    # Check that the tool result reflects the decline
    msgs = [m.content for m in agent.messages if m.role == "tool"]
    assert any("declined" in (m or "").lower() for m in msgs)


# ----- agent: unknown tool name ---------------------------------------------


@pytest.mark.asyncio
async def test_unknown_tool_name_emits_error_and_continues():
    agent = BuddyAgent()
    with patch("financial_analyst.buddy.agent.LLMClient.for_agent") as mock_factory:
        client = AsyncMock()
        client.chat = AsyncMock(side_effect=[
            _make_llm_response(tool_calls=[
                {"name": "made_up_tool_xyz", "args": {}},
            ]),
            _make_llm_response(text="I tried but the tool wasn't there."),
        ])
        mock_factory.return_value = client
        agent._client = client

        events = []
        async for evt in agent.run_turn("foo"):
            events.append(evt)

    err_events = [e for e in events if e.kind == "error"]
    assert any("Unknown tool" in (e.payload or "") for e in err_events)


# ----- agent: max_tool_iters loop guard -------------------------------------


@pytest.mark.asyncio
async def test_max_tool_iters_guard_breaks_infinite_loop():
    """If LLM keeps requesting tools forever, the loop should bail out."""
    # v1.6.4: default max_tool_iters bumped to 15; pass 3 explicitly here.
    agent = BuddyAgent(max_tool_iters=3, max_llm_retries=0)
    industry_tool = get_tool("industry_show")
    industry_tool.run = lambda code: ToolResult("ok")

    with patch("financial_analyst.buddy.agent.LLMClient.for_agent") as mock_factory:
        client = AsyncMock()
        # Every turn requests another tool — infinite loop
        client.chat = AsyncMock(return_value=_make_llm_response(tool_calls=[
            {"name": "industry_show", "args": {"code": "SH600519"}},
        ]))
        mock_factory.return_value = client
        agent._client = client

        events = []
        async for evt in agent.run_turn("loop test"):
            events.append(evt)

    err_events = [e for e in events if e.kind == "error"]
    # v1.6.4: error message is in Chinese now ("tool 调用上限")
    assert any(
        "tool 调用上限" in (e.payload or "") or "max tool-use iterations" in (e.payload or "")
        for e in err_events
    )


# ----- agent: conversation state persists across turns ----------------------


@pytest.mark.asyncio
async def test_llm_failure_retries_then_yields_error_and_done():
    """v1.6.4: transient LLM failures should retry max_llm_retries times,
    then yield BOTH an error event AND a done event so the BuddyApp's
    end-of-turn finalizer prints the right marker."""
    import asyncio as _a
    agent = BuddyAgent(max_tool_iters=2, max_llm_retries=2)
    industry_tool = get_tool("industry_show")
    industry_tool.run = lambda code: ToolResult("ok")

    call_count = [0]
    async def _flaky_chat(*args, **kwargs):
        call_count[0] += 1
        raise RuntimeError(f"synthetic network error #{call_count[0]}")

    with patch("financial_analyst.buddy.agent.LLMClient.for_agent") as mock_factory:
        client = AsyncMock()
        client.chat = AsyncMock(side_effect=_flaky_chat)
        mock_factory.return_value = client
        agent._client = client

        events = []
        async for evt in agent.run_turn("test"):
            events.append(evt)

    # max_llm_retries=2 → 1 initial + 2 retries = 3 total calls
    assert call_count[0] == 3, f"expected 3 LLM calls, got {call_count[0]}"
    # Yields exactly one error then one done — no more iterations attempted
    err_events = [e for e in events if e.kind == "error"]
    done_events = [e for e in events if e.kind == "done"]
    assert len(err_events) == 1
    assert "LLM 调用失败" in err_events[0].payload
    assert "synthetic network error" in err_events[0].payload
    assert len(done_events) == 1


@pytest.mark.asyncio
async def test_llm_recovers_on_second_attempt():
    """If LLM fails once then succeeds, the agent should NOT yield an error."""
    agent = BuddyAgent(max_tool_iters=2, max_llm_retries=2)

    attempts = [0]
    fake_ok = _make_llm_response(text="ok")
    async def _flaky_then_ok(*args, **kwargs):
        attempts[0] += 1
        if attempts[0] == 1:
            raise RuntimeError("first try fails")
        return fake_ok

    with patch("financial_analyst.buddy.agent.LLMClient.for_agent") as mock_factory:
        client = AsyncMock()
        client.chat = AsyncMock(side_effect=_flaky_then_ok)
        mock_factory.return_value = client
        agent._client = client

        events = []
        async for evt in agent.run_turn("test"):
            events.append(evt)

    assert attempts[0] == 2, "should have retried once and succeeded"
    err_events = [e for e in events if e.kind == "error"]
    text_events = [e for e in events if e.kind == "text"]
    assert len(err_events) == 0
    assert any("ok" in (e.payload or "") for e in text_events)


@pytest.mark.asyncio
async def test_conversation_state_accumulates_across_turns():
    agent = BuddyAgent()
    with patch("financial_analyst.buddy.agent.LLMClient.for_agent") as mock_factory:
        client = AsyncMock()
        client.chat = AsyncMock(side_effect=[
            _make_llm_response(text="first response"),
            _make_llm_response(text="second response"),
        ])
        mock_factory.return_value = client
        agent._client = client

        async for _ in agent.run_turn("hello"):
            pass
        async for _ in agent.run_turn("again"):
            pass

    # 2 user + 2 assistant messages
    roles = [m.role for m in agent.messages]
    assert roles == ["user", "assistant", "user", "assistant"]

    agent.reset()
    assert agent.messages == []
