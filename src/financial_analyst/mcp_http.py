"""MCP Streamable HTTP transport — mount at ``/mcp`` on the buddy backend.

Exposes the same 20 fa MCP tools as the stdio server, but over HTTP. Intended
for AI IDEs that don't speak stdio MCP (JetBrains plugins, cross-process
clients on the same host, etc).

The returned Starlette app is meant to be mounted into the buddy FastAPI
app at ``/mcp``. Its lifespan owns the underlying ``StreamableHTTPSessionManager``,
which the SDK uses to track per-client sessions.

See ``docs/superpowers/specs/2026-05-27-mcp-http-transport-design.md``.
"""
from __future__ import annotations

import contextlib
from typing import AsyncIterator

from starlette.applications import Starlette
from starlette.routing import Mount


def build_mcp_http_app() -> Starlette:
    """Build a Starlette ASGI sub-app exposing fa MCP over Streamable HTTP.

    Reuses :func:`financial_analyst.mcp_server._build_server` — same 20 tools,
    same handlers, same ``TOOLS`` dict. Adding a tool to ``mcp_server.TOOLS``
    automatically exposes it on both transports; no change here required.

    The Starlette app carries the session manager's lifespan, so when the
    parent ASGI server (uvicorn driving the buddy FastAPI) starts and stops,
    the manager's session table is set up and torn down cleanly. Without
    this, the manager would leak background tasks on shutdown.

    Returns:
        Starlette app with a single ``Mount("/", ...)`` that dispatches all
        incoming requests to the MCP session manager. Mount at ``/mcp`` on
        the buddy FastAPI app.
    """
    # Imports kept inside the function so import-time cost is paid only when
    # buddy actually builds the app (e.g. tests that don't need MCP HTTP can
    # skip the SDK import).
    from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
    from financial_analyst.mcp_server import _build_server

    server = _build_server()
    manager = StreamableHTTPSessionManager(app=server)

    async def handle_mcp(scope, receive, send) -> None:
        """Thin ASGI shim — the SDK does all the JSON-RPC + session work."""
        await manager.handle_request(scope, receive, send)

    @contextlib.asynccontextmanager
    async def lifespan(_app: Starlette) -> AsyncIterator[None]:
        """Own the session manager's lifecycle for the lifetime of the parent app."""
        async with manager.run():
            yield

    return Starlette(
        routes=[Mount("/", app=handle_mcp)],
        lifespan=lifespan,
    )
