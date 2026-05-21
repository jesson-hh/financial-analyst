"""Reproduce v1.6.5 failure mode reported by user.

User scenario:
  - submit: "我想看看今天雪球社区大家的情绪 还有大量新闻"
  - LLM calls news_collect(limit=200, sources=...) -> succeeds
  - LLM calls news_query(days=1, limit=30) -> returns 30 entries
  - User screenshot: transcript stops mid-news-listing, no end marker

Hypotheses to test:
  H1. LLM yields tool_calls but no text on final turn -> v1.6.3 marker
      "调了 N tool 但没文字总结" should fire.
  H2. End marker IS emitted but auto-scroll fails so it's below viewport.
  H3. LLM call silently swallows an exception inside the to_thread() block.
"""
from __future__ import annotations
import asyncio
import json
from unittest.mock import patch

import pytest

from financial_analyst.buddy.app import BuddyApp
from financial_analyst.buddy.tools import ToolResult


def _llm_resp(text="", tool_calls=None):
    msg = {"role": "assistant", "content": text}
    if tool_calls:
        msg["tool_calls"] = [
            {"id": f"call_{i}", "type": "function",
             "function": {"name": tc["name"],
                          "arguments": json.dumps(tc.get("args", {}), ensure_ascii=False)}}
            for i, tc in enumerate(tool_calls)
        ]
    return {"choices": [{"message": msg}]}


@pytest.mark.asyncio
async def test_user_scenario_news_collect_then_query_then_no_summary():
    """H1: LLM calls 2 news tools, then yields nothing on the 3rd turn.

    Expected: v1.6.3 yellow "完成 (调了 N 个 tool 但没文字总结)" marker fires.
    """
    app = BuddyApp()

    big_news_payload = "\n".join(
        f"[2026-05-20] sinafinance_news entry {i}: long-form headline about market xxx..."
        for i in range(30)
    )

    llm_sequence = [
        _llm_resp(tool_calls=[{"name": "news_collect",
                               "args": {"limit": 200,
                                        "sources": "xueqiu-hot,kuaixun,longhu,sinafinance"}}]),
        _llm_resp(tool_calls=[{"name": "news_query",
                               "args": {"days": 1, "limit": 30}}]),
        _llm_resp(text=""),  # <-- the bug: empty content, no tool_calls -> "done"
    ]
    llm_iter = iter(llm_sequence)

    async def fake_chat(**_kwargs):
        return next(llm_iter)

    tool_results = {
        "news_collect": ToolResult("xueqiu_hot:100\nkuaixun:100\nlonghu:20\nsinafinance:50"),
        "news_query": ToolResult(big_news_payload),
    }

    def fake_get_tool(name):
        from financial_analyst.buddy.tools import Tool
        return Tool(
            name=name, description=f"mock {name}", input_schema={},
            run=lambda **kw: tool_results[name],
            cost_hint="instant",
        )

    with patch.object(app.agent._client, "chat", side_effect=fake_chat), \
         patch("financial_analyst.buddy.agent.get_tool", side_effect=fake_get_tool):
        app.submit("我想看看今天雪球社区大家的情绪 还有大量新闻")
        assert app._has_active_turn()
        try:
            await asyncio.wait_for(app.current_turn_task, timeout=5.0)
        except asyncio.TimeoutError:
            pytest.fail("Turn hung — did not complete in 5s")

    transcript = app.transcript_text()
    import re
    plain = re.sub(r"\x1b\[[0-9;]*m", "", transcript)
    from pathlib import Path
    Path("G:/financial-analyst/transcript_repro.txt").write_text(plain, encoding="utf-8")

    # H1 verification: did the "调了 N 个 tool 但没文字总结" marker fire?
    # Use Chinese chars directly — pytest assertion error display is gbk on Windows
    # which mojibakes the message, but the actual string content is fine.
    assert "调了" in plain, "v1.6.3 'no summary' marker did NOT fire"
    assert "tool 但没文字总结" in plain, "marker text wrong"
    assert "调了 2 个 tool" in plain, "expected tool count of 2"


@pytest.mark.asyncio
async def test_cursor_position_tracks_transcript_bottom():
    """H2 root cause: v1.6.5's get_vertical_scroll callback got reset to 0
    by Window.do_scroll() because cursor_pos was 0. v1.6.6 fix:
    cursor is pinned to last line, so do_scroll's natural
    'keep cursor visible' logic auto-scrolls for us.
    """
    app = BuddyApp()
    pt_initial = app._get_cursor_at_bottom()
    # Simulate a long news_query result landing in transcript
    for i in range(50):
        app._append_chunk(f"[2026-05-20] entry {i}: ...\n")
    pt_after = app._get_cursor_at_bottom()
    # Cursor y must advance — that's what enables auto-scroll
    assert pt_after.y > pt_initial.y
    assert pt_after.y - pt_initial.y == 50


@pytest.mark.asyncio
async def test_full_scenario_with_summary_works_too():
    """Baseline sanity: when LLM DOES produce a summary, '✓ 完成' should fire."""
    app = BuddyApp()
    llm_sequence = [
        _llm_resp(tool_calls=[{"name": "news_query", "args": {"days": 1, "limit": 30}}]),
        _llm_resp(text="今日雪球热门 5 条: ..."),
    ]
    llm_iter = iter(llm_sequence)

    async def fake_chat(**_kwargs):
        return next(llm_iter)

    def fake_get_tool(name):
        from financial_analyst.buddy.tools import Tool
        return Tool(name=name, description="x", input_schema={},
                    run=lambda **kw: ToolResult("30 entries..."), cost_hint="instant")

    with patch.object(app.agent._client, "chat", side_effect=fake_chat), \
         patch("financial_analyst.buddy.agent.get_tool", side_effect=fake_get_tool):
        app.submit("xx")
        await asyncio.wait_for(app.current_turn_task, timeout=5.0)

    transcript = app.transcript_text()
    assert "完成" in transcript
    assert "调了" not in transcript or "tool 但没文字总结" not in transcript
