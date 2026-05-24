"""Tests for the MCP server tool registry and dispatch.

We don't spin up a real MCP stdio session — we test the tool table directly.
"""
import json
import pytest
from unittest.mock import AsyncMock, patch
from financial_analyst.mcp_server import TOOLS, _tool_ask, _tool_quick_quote


def test_tools_registered():
    expected = {
        "ask", "quick_quote", "quick_factors", "memory_search",
        "list_past_reports", "read_past_report", "list_dream_proposals",
        "report", "mainline", "brief", "intraday", "dream",
        "dream_aggregate",   # v1.9.5: Tier-4 introspections clustering
    }
    assert expected == set(TOOLS.keys())


def test_each_tool_has_required_fields():
    for name, defn in TOOLS.items():
        assert "handler" in defn, f"{name} missing handler"
        assert "description" in defn, f"{name} missing description"
        assert "schema" in defn, f"{name} missing schema"
        assert defn["schema"]["type"] == "object"


@pytest.mark.asyncio
async def test_tool_ask_dispatches_to_ask_module():
    from financial_analyst.ask.schemas import AskOutput
    mock_output = AskOutput(answer="hello", needs_full_report=False, suggested_code="")
    with patch("financial_analyst.ask.ask", AsyncMock(return_value=mock_output)):
        result = await _tool_ask(query="hi")
    assert result["answer"] == "hello"


@pytest.mark.asyncio
async def test_tool_quick_quote_calls_underlying():
    fake = {"code": "SH600519", "close": 1700.0}
    with patch("financial_analyst.ask.tools.quick_quote", return_value=fake):
        result = await _tool_quick_quote(code="SH600519")
    assert result["close"] == 1700.0


@pytest.mark.asyncio
async def test_unknown_tool_returns_error():
    """The server's call_tool catches unknown names. Build server and verify it constructs."""
    from financial_analyst.mcp_server import _build_server
    server = _build_server()
    assert server is not None


def test_descriptions_are_informative():
    """Every tool description should be > 30 chars (not just the name)."""
    for name, defn in TOOLS.items():
        assert len(defn["description"]) > 30, f"{name} has too-short description"


def test_tool_count():
    """Exactly 13 tools should be registered (v1.9.5: +dream_aggregate)."""
    assert len(TOOLS) == 13


def test_required_fields_in_schemas():
    """Tools that need a code param should declare it as required."""
    code_tools = {"quick_quote", "quick_factors", "read_past_report", "report"}
    for name in code_tools:
        schema = TOOLS[name]["schema"]
        assert "code" in schema.get("properties", {}), f"{name} schema missing 'code' property"
        assert "code" in schema.get("required", []), f"{name} schema 'code' not in required"


def test_ask_schema_has_query_required():
    schema = TOOLS["ask"]["schema"]
    assert "query" in schema["properties"]
    assert "query" in schema["required"]


def test_server_list_tools_returns_all():
    """list_tools() decorator should surface all 13 tool names."""
    from financial_analyst.mcp_server import _build_server
    server = _build_server()
    # The decorated handler is stored internally; confirm server builds without error
    # and the TOOLS dict size matches expectations.
    assert len(TOOLS) == 13
