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

    @router.get("/data/market_tape")
    async def market_tape_ep():
        from guanlan_v2.datafeed.market_tape import read_tape
        return JSONResponse(await asyncio.to_thread(read_tape))

    return router
