# -*- coding: utf-8 -*-
"""guanlan MCP Streamable HTTP transport —— 供 server.py 挂在 /gl-mcp。镜像引擎 mcp_http。

返回的 Starlette 子应用自带 session-manager lifespan;Starlette 不会自动跑被挂子应用的
lifespan,故 guanlan_v2/server.py 需把它叠加进父 app 的 lifespan(后续任务)。
"""
from __future__ import annotations

import contextlib
from typing import AsyncIterator

from starlette.applications import Starlette
from starlette.routing import Mount


def build_mcp_http_app() -> Starlette:
    from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
    from guanlan_v2.glmcp.server import build_server

    server = build_server()
    manager = StreamableHTTPSessionManager(app=server)

    async def handle_mcp(scope, receive, send) -> None:
        await manager.handle_request(scope, receive, send)

    @contextlib.asynccontextmanager
    async def lifespan(_app: Starlette) -> AsyncIterator[None]:
        async with manager.run():
            yield

    return Starlette(routes=[Mount("/", app=handle_mcp)], lifespan=lifespan)
