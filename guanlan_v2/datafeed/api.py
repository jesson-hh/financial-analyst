# -*- coding: utf-8 -*-
"""datafeed 路由(薄壳挂载,无 prefix)。协程内严禁同步 IO——实现走 asyncio.to_thread。"""
from __future__ import annotations

import asyncio

from fastapi import APIRouter
from fastapi.responses import JSONResponse


def build_datafeed_router() -> APIRouter:
    router = APIRouter(tags=["datafeed"])

    @router.get("/data/health")
    async def data_health_ep():
        from guanlan_v2.datafeed.health import collect_data_health
        return JSONResponse(await asyncio.to_thread(collect_data_health))

    return router
