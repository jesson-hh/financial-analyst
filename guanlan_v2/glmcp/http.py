# -*- coding: utf-8 -*-
"""guanlan MCP Streamable HTTP transport —— 供 server.py 挂在 /gl-mcp。镜像引擎 mcp_http。

返回的 Starlette 子应用自带 session-manager lifespan;Starlette 不会自动跑被挂子应用的
lifespan,故 guanlan_v2/server.py 需把它叠加进父 app 的 lifespan(后续任务)。
"""
from __future__ import annotations

import contextlib
import hmac
import os
from typing import AsyncIterator

from starlette.applications import Starlette
from starlette.datastructures import Headers
from starlette.responses import PlainTextResponse
from starlette.routing import Mount


def _mcp_http_auth_error(scope) -> tuple[int, str] | None:
    """Authenticate the HTTP transport; stdio remains a local-process channel."""
    token = str(os.environ.get("GUANLAN_MCP_BEARER_TOKEN") or "").strip()
    write_enabled = os.environ.get("GUANLAN_MCP_WRITE") == "1"
    if not token:
        if write_enabled:
            return 503, "MCP write mode requires GUANLAN_MCP_BEARER_TOKEN"
        return None
    supplied = Headers(scope=scope).get("authorization", "")
    prefix = "Bearer "
    if not supplied.startswith(prefix) or not hmac.compare_digest(supplied[len(prefix):], token):
        return 401, "Unauthorized MCP client"
    return None


def build_mcp_http_app(console_store=None, *, scope_required: bool = False) -> Starlette:
    from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
    from guanlan_v2.glmcp.server import build_server

    server = build_server(
        console_store=console_store, scope_required=scope_required,
    )
    manager = StreamableHTTPSessionManager(app=server)

    async def handle_mcp(scope, receive, send) -> None:
        scope_token = None
        if scope_required:
            from guanlan_v2.glmcp.scope import (
                CTX_CODEX_MCP_SCOPE,
                resolve_codex_scope,
            )
            supplied = Headers(scope=scope).get("x-guanlan-codex-scope", "")
            codex_scope = resolve_codex_scope(supplied)
            if codex_scope is None:
                response = PlainTextResponse(
                    "Missing or expired Codex MCP scope", status_code=403,
                )
                await response(scope, receive, send)
                return
            scope_token = CTX_CODEX_MCP_SCOPE.set(codex_scope)
        else:
            auth_error = _mcp_http_auth_error(scope)
            if auth_error is not None:
                status, message = auth_error
                response = PlainTextResponse(
                    message, status_code=status,
                    headers={"WWW-Authenticate": "Bearer"}
                    if status == 401 else None,
                )
                await response(scope, receive, send)
                return
        try:
            await manager.handle_request(scope, receive, send)
        finally:
            if scope_token is not None:
                CTX_CODEX_MCP_SCOPE.reset(scope_token)

    @contextlib.asynccontextmanager
    async def lifespan(_app: Starlette) -> AsyncIterator[None]:
        async with manager.run():
            yield

    return Starlette(routes=[Mount("/", app=handle_mcp)], lifespan=lifespan)
