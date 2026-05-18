import json
import pytest
from unittest.mock import AsyncMock, patch
from financial_analyst.ask.ask_agent import ask


@pytest.mark.asyncio
async def test_ask_no_tool_calls():
    """LLM answers directly without tools."""
    fake_response = {
        "choices": [{
            "message": {
                "content": json.dumps({
                    "answer": "Hello world",
                    "actions_taken": [],
                    "references": [],
                    "needs_full_report": False,
                    "suggested_code": "",
                }),
                "tool_calls": None,
            }
        }]
    }
    fake_client = AsyncMock()
    fake_client.chat = AsyncMock(return_value=fake_response)
    output = await ask("hi", llm_client=fake_client)
    assert output.answer == "Hello world"
    assert output.actions_taken == []


@pytest.mark.asyncio
async def test_ask_with_tool_call():
    """LLM calls a tool, then synthesizes."""
    first_response = {
        "choices": [{
            "message": {
                "content": "",
                "tool_calls": [{
                    "id": "1",
                    "type": "function",
                    "function": {
                        "name": "list_past_reports",
                        "arguments": '{"limit": 5}',
                    },
                }],
            }
        }]
    }
    synth_response = {
        "choices": [{
            "message": {
                "content": json.dumps({
                    "answer": "Found 0 reports",
                    "actions_taken": [],
                    "references": [],
                    "needs_full_report": False,
                    "suggested_code": "",
                })
            }
        }]
    }
    fake_client = AsyncMock()
    fake_client.chat = AsyncMock(side_effect=[first_response, synth_response])
    with patch("financial_analyst.ask.tools.list_past_reports", return_value=[]):
        output = await ask("list my reports", llm_client=fake_client)
    assert "Found 0" in output.answer
    assert any("list_past_reports" in a for a in output.actions_taken)


@pytest.mark.asyncio
async def test_ask_needs_full_report():
    """LLM determines a question requires the full DAG."""
    fake_response = {
        "choices": [{
            "message": {
                "content": json.dumps({
                    "answer": "Need full deep-dive",
                    "actions_taken": [],
                    "references": [],
                    "needs_full_report": True,
                    "suggested_code": "SH600999",
                }),
                "tool_calls": None,
            }
        }]
    }
    fake_client = AsyncMock()
    fake_client.chat = AsyncMock(return_value=fake_response)
    output = await ask("give me a full analysis on SH600999", llm_client=fake_client)
    assert output.needs_full_report is True
    assert output.suggested_code == "SH600999"


@pytest.mark.asyncio
async def test_ask_malformed_json_falls_back():
    fake_response = {
        "choices": [{
            "message": {"content": "not json", "tool_calls": None}
        }]
    }
    fake_client = AsyncMock()
    fake_client.chat = AsyncMock(return_value=fake_response)
    output = await ask("hi", llm_client=fake_client)
    assert output.answer == "not json"
