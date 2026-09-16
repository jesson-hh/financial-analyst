# -*- coding: utf-8 -*-
"""datafeed 路由(薄壳挂载,无 prefix)。协程内严禁同步 IO——实现走 asyncio.to_thread。"""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException, Query
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

    @router.get("/data/overseas")
    async def overseas_ep(code: str = ""):
        """海外(美股/港股)个股行情。code 为空 → 精选清单 SWR 快照;传 code → 单只直拉 lookup。"""
        from guanlan_v2.datafeed import overseas as ov
        if code.strip():
            row = await asyncio.to_thread(ov.probe_one, code.strip())
            return JSONResponse({"ok": row is not None, "code": code.strip(), "row": row})
        return JSONResponse(await asyncio.to_thread(ov.read_overseas))

    @router.get("/data/newsradar")
    async def newsradar_ep(
            sectors: str = "",
            days: int = Query(7, ge=1, le=30),
            limit: int = Query(200, ge=1, le=500),
            include_future: bool = False,
            refresh: bool = False):
        """产业 RSS 雷达(SWR)。源声明未来时间条目仅在 include_future=true 时独立返回。"""
        from guanlan_v2.datafeed import newsradar as nr
        secs = list(dict.fromkeys(
            s.strip().lower() for s in (sectors or "").split(",") if s.strip()
        )) or None
        if secs:
            industries, _sources = await asyncio.to_thread(nr.load_sources)
            allowed = {str(row.get("key") or "") for row in industries if isinstance(row, dict)}
            bad = [value for value in secs if value not in allowed]
            if bad:
                raise HTTPException(
                    status_code=422,
                    detail="未知 NewsRadar 赛道: " + ",".join(bad)
                           + "；可选 " + ",".join(sorted(allowed)),
                )
        return JSONResponse(await asyncio.to_thread(
            nr.read_radar_swr, secs, int(days), int(limit),
            include_future=bool(include_future), force_refresh=bool(refresh),
        ))

    return router
