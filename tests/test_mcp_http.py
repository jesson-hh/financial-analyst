"""End-to-end tests for the HTTP Streamable MCP transport mounted at /mcp.

Uses ``httpx.ASGITransport`` to drive the buddy FastAPI app in-process — no
real TCP listen, no PATH dependency on the ``financial-analyst-mcp`` console
script, no need to start a uvicorn subprocess. Fast and hermetic.

Cross-checks the stdio test's ``EXPECTED_TOOLS`` set so a tool added to
``mcp_server.TOOLS`` triggers a failure on EITHER transport that forgets to
update its drift detector.
"""
from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager

import httpx
import pytest

from tests.test_mcp_server import EXPECTED_TOOLS


@asynccontextmanager
async def _drive_lifespan(app):
    """Manually drive an ASGI app's lifespan — startup before yield, shutdown after.

    ``httpx.ASGITransport`` doesn't trigger lifespan events, so the buddy
    FastAPI app's ``lifespan`` (which owns the MCP session manager via
    ``manager.run()``) would never start. We drive it ourselves here.
    """
    scope = {"type": "lifespan", "asgi": {"version": "3.0", "spec_version": "2.0"}}
    startup_complete = asyncio.Event()
    shutdown_complete = asyncio.Event()
    messages_in: asyncio.Queue = asyncio.Queue()

    async def receive():
        return await messages_in.get()

    async def send(msg):
        if msg["type"] == "lifespan.startup.complete":
            startup_complete.set()
        elif msg["type"] == "lifespan.shutdown.complete":
            shutdown_complete.set()
        elif msg["type"] in ("lifespan.startup.failed", "lifespan.shutdown.failed"):
            raise RuntimeError(f"ASGI lifespan failed: {msg}")

    task = asyncio.create_task(app(scope, receive, send))
    await messages_in.put({"type": "lifespan.startup"})
    await startup_complete.wait()
    try:
        yield
    finally:
        await messages_in.put({"type": "lifespan.shutdown"})
        await shutdown_complete.wait()
        await task


# The Streamable HTTP transport requires both content types in Accept per the
# MCP spec. The SDK enforces this and returns 406 if missing.
_HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json, text/event-stream",
}

_INIT_FRAME = {
    "jsonrpc": "2.0", "id": 1, "method": "initialize",
    "params": {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "pytest-http-smoke", "version": "0.1"},
    },
}
_INITIALIZED_FRAME = {"jsonrpc": "2.0", "method": "notifications/initialized"}


def _parse_sse_or_json(response: httpx.Response) -> list[dict]:
    """The SDK may return either a JSON body or an SSE stream — handle both."""
    ctype = response.headers.get("content-type", "")
    if "text/event-stream" in ctype:
        frames: list[dict] = []
        for line in response.text.splitlines():
            if line.startswith("data: "):
                payload = line[len("data: "):].strip()
                if payload:
                    try:
                        frames.append(json.loads(payload))
                    except json.JSONDecodeError:
                        pass
        return frames
    # Fallback: JSON body (single frame)
    try:
        return [response.json()]
    except Exception:
        return []


@pytest.fixture
async def client():
    """ASGI in-process HTTP client over the live buddy app (with /mcp mounted).

    Drives the buddy FastAPI lifespan so the MCP session manager actually starts.
    """
    from financial_analyst.buddy.server import build_app
    app = build_app()
    async with _drive_lifespan(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
            timeout=15.0,
        ) as ac:
            yield ac


async def _initialize(client: httpx.AsyncClient) -> tuple[dict, str]:
    """POST initialize; return (parsed first frame, session id header value)."""
    resp = await client.post("/mcp/", json=_INIT_FRAME, headers=_HEADERS)
    assert resp.status_code == 200, f"initialize failed: {resp.status_code} {resp.text[:300]}"
    sid = resp.headers.get("mcp-session-id", "")
    frames = _parse_sse_or_json(resp)
    assert frames, f"no frames in initialize response: {resp.text[:300]}"
    return frames[0], sid


async def _post_with_session(client: httpx.AsyncClient, sid: str, frame: dict) -> httpx.Response:
    headers = {**_HEADERS, "mcp-session-id": sid}
    return await client.post("/mcp/", json=frame, headers=headers)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_http_initialize_handshake(client):
    """POST /mcp initialize returns protocolVersion + serverInfo."""
    frame, sid = await _initialize(client)
    assert frame["id"] == 1
    result = frame["result"]
    assert "protocolVersion" in result
    assert result["serverInfo"]["name"] == "financial-analyst"
    assert sid, "server must issue an mcp-session-id"


@pytest.mark.asyncio
async def test_http_tools_list_matches_expected_set(client):
    """tools/list returns exactly EXPECTED_TOOLS — drift detector shared with stdio."""
    _, sid = await _initialize(client)
    # Send the initialized notification (no response expected).
    await _post_with_session(client, sid, _INITIALIZED_FRAME)
    resp = await _post_with_session(client, sid, {
        "jsonrpc": "2.0", "id": 2, "method": "tools/list",
    })
    assert resp.status_code == 200
    frames = _parse_sse_or_json(resp)
    assert frames, f"no tools/list response: {resp.text[:300]}"
    tools = frames[0]["result"]["tools"]
    names = {t["name"] for t in tools}
    missing = EXPECTED_TOOLS - names
    extra = names - EXPECTED_TOOLS
    assert not missing, f"missing tools (regression?): {missing}"
    assert not extra, f"new tools without updating EXPECTED_TOOLS: {extra}"


@pytest.mark.asyncio
async def test_http_chain_lookup_roundtrip(client):
    """chain_lookup returns primary_product or clean error — never crashes."""
    _, sid = await _initialize(client)
    await _post_with_session(client, sid, _INITIALIZED_FRAME)
    resp = await _post_with_session(client, sid, {
        "jsonrpc": "2.0", "id": 3, "method": "tools/call",
        "params": {"name": "chain_lookup", "arguments": {"code": "SH688256"}},
    })
    frames = _parse_sse_or_json(resp)
    assert frames, f"no chain_lookup response: {resp.text[:300]}"
    call_result = frames[0]["result"]
    assert call_result.get("isError") is False
    text = call_result["content"][0]["text"]
    data = json.loads(text)
    assert ("primary_product" in data) or ("error" in data), data


@pytest.mark.asyncio
async def test_http_quick_quote_roundtrip(client):
    """quick_quote returns real OHLCV dict (or clean error if data missing)."""
    _, sid = await _initialize(client)
    await _post_with_session(client, sid, _INITIALIZED_FRAME)
    resp = await _post_with_session(client, sid, {
        "jsonrpc": "2.0", "id": 4, "method": "tools/call",
        "params": {"name": "quick_quote", "arguments": {"code": "SH600519"}},
    })
    frames = _parse_sse_or_json(resp)
    assert frames, f"no quick_quote response: {resp.text[:300]}"
    text = frames[0]["result"]["content"][0]["text"]
    data = json.loads(text)
    # Either real OHLCV fields or a clean error dict.
    assert ("close" in data) or ("error" in data), data


@pytest.mark.asyncio
async def test_http_two_clients_have_isolated_sessions(client):
    """Two clients each get a distinct mcp-session-id — sessions don't bleed."""
    _, sid_a = await _initialize(client)
    _, sid_b = await _initialize(client)
    assert sid_a, "no session id for client A"
    assert sid_b, "no session id for client B"
    assert sid_a != sid_b, f"sessions collided: {sid_a} == {sid_b}"
