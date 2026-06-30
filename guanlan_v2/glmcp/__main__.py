# -*- coding: utf-8 -*-
"""python -m guanlan_v2.glmcp → stdio MCP(本地客户端用)。镜像引擎 mcp_server.main。"""
from __future__ import annotations

import asyncio


def main() -> None:
    from mcp.server.stdio import stdio_server
    from guanlan_v2.glmcp.server import build_server

    async def _run():
        server = build_server()
        async with stdio_server() as (read_stream, write_stream):
            await server.run(read_stream, write_stream, server.create_initialization_options())

    asyncio.run(_run())


if __name__ == "__main__":
    main()
