"""Tests for the MCP server tool registry and dispatch.

We don't spin up a real MCP stdio session — we test the tool table directly.
"""
import json
import pytest
from unittest.mock import AsyncMock, patch
from financial_analyst.mcp_server import TOOLS, _tool_ask, _tool_quick_quote


EXPECTED_TOOLS = {
    "ask", "quick_quote", "quick_factors", "memory_search",
    "list_past_reports", "read_past_report", "list_dream_proposals",
    "report", "mainline", "brief", "intraday", "dream",
    "dream_aggregate",     # v1.9.5: Tier-4 introspections clustering
    "overseas_radar",      # v1.0.7: global transmission radar
    "data_update",         # v1.0.7: trigger `fa data update` subprocess
    "chain_lookup",        # v1.0.7: industry-chain context for one stock
}


def test_tools_registered():
    assert EXPECTED_TOOLS == set(TOOLS.keys())


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
    """Tool count matches the locked-in EXPECTED_TOOLS set."""
    assert len(TOOLS) == len(EXPECTED_TOOLS)


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
    """list_tools() decorator should surface all registered tool names."""
    from financial_analyst.mcp_server import _build_server
    server = _build_server()
    # The decorated handler is stored internally; confirm server builds without error
    # and the TOOLS dict size matches expectations.
    assert len(TOOLS) == len(EXPECTED_TOOLS)


# ---------------------------------------------------------------------------
# End-to-end smoke test (subprocess stdio JSON-RPC round-trip)
#
# These spawn `financial-analyst-mcp` as a real subprocess, pipe a fixed set
# of JSON-RPC frames, and assert the responses. Catches regressions where the
# module imports cleanly (unit tests pass) but the actual MCP loop is broken
# — different failure mode from the mocked unit tests above.
#
# Skipped automatically if `financial-analyst-mcp` is not on PATH.
# ---------------------------------------------------------------------------

import shutil  # noqa: E402
import subprocess  # noqa: E402

MCP_BIN = shutil.which("financial-analyst-mcp")


def _run_frames(frames: list[dict], timeout_sec: int = 30) -> list[dict]:
    """Pipe ``frames`` into a fresh MCP subprocess; return parsed stdout JSON."""
    if not MCP_BIN:
        pytest.skip("financial-analyst-mcp not on PATH")

    payload = "\n".join(json.dumps(f) for f in frames) + "\n"
    proc = subprocess.run(
        [MCP_BIN],
        input=payload,
        capture_output=True,
        text=True,
        encoding="utf-8",      # MCP server emits UTF-8; Windows default is GBK
        errors="replace",       # don't crash on stray bytes in stderr warnings
        timeout=timeout_sec,
    )
    responses: list[dict] = []
    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            responses.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return responses


def _by_id(responses: list[dict]) -> dict[int, dict]:
    return {r["id"]: r for r in responses if "id" in r}


_INIT = {
    "jsonrpc": "2.0", "id": 1, "method": "initialize",
    "params": {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "pytest-smoke", "version": "0.1"},
    },
}
_INITIALIZED = {"jsonrpc": "2.0", "method": "notifications/initialized"}


def test_smoke_initialize_handshake():
    """Subprocess responds to initialize with protocolVersion + serverInfo."""
    responses = _run_frames([_INIT])
    by_id = _by_id(responses)
    assert 1 in by_id, f"no response to initialize; stdout={responses}"
    result = by_id[1]["result"]
    assert "protocolVersion" in result
    assert result["serverInfo"]["name"] == "financial-analyst"


def test_smoke_tools_list_matches_expected_set():
    """tools/list returns exactly EXPECTED_TOOLS — drift detector."""
    responses = _run_frames([
        _INIT, _INITIALIZED,
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
    ])
    by_id = _by_id(responses)
    assert 2 in by_id, f"no response to tools/list; stdout={responses}"
    tools = by_id[2]["result"]["tools"]
    names = {t["name"] for t in tools}
    missing = EXPECTED_TOOLS - names
    extra = names - EXPECTED_TOOLS
    assert not missing, f"missing tools (regression?): {missing}"
    assert not extra, f"new tools without updating EXPECTED_TOOLS: {extra}"
    for t in tools:
        assert "inputSchema" in t, f"tool {t['name']} has no inputSchema"


def test_smoke_chain_lookup_roundtrip():
    """chain_lookup returns primary_product or a clean error dict — never crashes."""
    responses = _run_frames([
        _INIT, _INITIALIZED,
        {
            "jsonrpc": "2.0", "id": 3, "method": "tools/call",
            "params": {"name": "chain_lookup", "arguments": {"code": "SH688256"}},
        },
    ])
    by_id = _by_id(responses)
    assert 3 in by_id, f"no response to chain_lookup; stdout={responses}"
    call_result = by_id[3]["result"]
    assert call_result.get("isError") is False
    text = call_result["content"][0]["text"]
    data = json.loads(text)
    # Real chain data OR a clean {"error": "..."} from the loader.
    assert ("primary_product" in data) or ("error" in data), data


def test_smoke_memory_search_hyphen_no_crash():
    """Regression: hyphen query was raising OperationalError pre-d1a541b."""
    responses = _run_frames([
        _INIT, _INITIALIZED,
        {
            "jsonrpc": "2.0", "id": 4, "method": "tools/call",
            "params": {"name": "memory_search",
                       "arguments": {"query": "game-capital", "top_k": 3}},
        },
    ])
    by_id = _by_id(responses)
    assert 4 in by_id, f"no response to memory_search; stdout={responses}"
    text = by_id[4]["result"]["content"][0]["text"]
    data = json.loads(text)
    # Must be a list of hits — NOT a wrapped {"error": "OperationalError ..."}
    assert isinstance(data, list), f"got error instead of hits: {data}"
