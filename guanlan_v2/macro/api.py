# -*- coding: utf-8 -*-
"""全球情绪温度计路由(薄壳挂载,无 prefix,路由自带 /macro/)。
协程内严禁同步 HTTP——实现一律 asyncio.to_thread。"""
from __future__ import annotations

import asyncio

from fastapi import APIRouter


def build_macro_router() -> APIRouter:
    router = APIRouter()

    @router.get("/macro/pulse")
    async def pulse_ep(refresh: int = 0):
        from . import pulse
        return await asyncio.to_thread(pulse.build_pulse, bool(refresh))

    @router.get("/macro/history")
    async def history_ep(market_id: str = "", theme: str = ""):
        from . import pulse
        return await asyncio.to_thread(pulse.load_history, market_id, theme)

    return router
