# -*- coding: utf-8 -*-
"""板块资金流路由(薄壳,无 prefix)。
协程内严禁同步 HTTP——一律 asyncio.to_thread。

盘中曲线由东财分钟线直出(fflow/kline klt=1),故无 poller、无自累快照:
开盘即完整、进程重启不断线。两端点各自带 SWR 缓存,反复刷新不反复打东财。"""
from __future__ import annotations

import asyncio

from fastapi import APIRouter


def build_fundflow_router() -> APIRouter:
    router = APIRouter()

    @router.get("/fundflow/live")
    async def live_ep(kind: str = "concept", refresh: int = 0):
        # SWR 秒回:缓存新鲜直接返、过期返旧值+后台单飞刷新、冷启动一次性阻塞首拉;
        # 反复点刷新不再反复起 probe 打东财(refresh=1 才显式强拉)。
        from . import pulse
        return await asyncio.to_thread(pulse.read_live, kind, bool(refresh))

    @router.get("/fundflow/history")
    async def history_ep(kind: str = "concept", date: str = "", refresh: int = 0):
        # 一次 history = 1 次板块排行 + N 条板块分钟线 + 1 条大盘分钟线(~21 个东财请求),
        # 必须走 SWR(TTL 60s):盘中数据每分钟才变一次。
        from . import pulse
        return await asyncio.to_thread(pulse.read_history, kind, date, bool(refresh))

    return router
