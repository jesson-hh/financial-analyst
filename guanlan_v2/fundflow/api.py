# -*- coding: utf-8 -*-
"""板块资金流路由(薄壳,无 prefix)+ opt-in 盘中 poller。
协程内严禁同步 HTTP——一律 asyncio.to_thread。"""
from __future__ import annotations

import asyncio
import os
import threading
import time

from fastapi import APIRouter


def _snapshot_dir():
    return os.environ.get("GUANLAN_FUNDFLOW_DIR") or None


def build_fundflow_router() -> APIRouter:
    router = APIRouter()

    @router.get("/fundflow/live")
    async def live_ep(kind: str = "concept", refresh: int = 0):
        from . import pulse
        return await asyncio.to_thread(pulse.build_live, kind, bool(refresh), _snapshot_dir())

    @router.get("/fundflow/history")
    async def history_ep(kind: str = "concept", date: str = ""):
        from . import pulse
        return await asyncio.to_thread(pulse.load_history, kind, date, _snapshot_dir())

    return router


_POLLER_STARTED = [False]


def start_fundflow_poller() -> None:
    """opt-in:GUANLAN_FUNDFLOW_POLL=1 才起。进程内 daemon,交易时段每 N 秒拉两档落点。
    随本进程存亡——非 24/7 保证。幂等只起一次。"""
    if _POLLER_STARTED[0] or os.environ.get("GUANLAN_FUNDFLOW_POLL") != "1":
        return
    _POLLER_STARTED[0] = True
    interval = int(os.environ.get("GUANLAN_FUNDFLOW_POLL_SEC") or 180)

    def _loop():
        from datetime import datetime
        from . import pulse
        while True:
            try:
                if pulse._is_trading(datetime.now()):
                    pulse.build_live("concept", refresh=False, snapshot_dir=_snapshot_dir())
            except Exception:
                pass
            time.sleep(max(30, interval))

    threading.Thread(target=_loop, name="fundflow-poller", daemon=True).start()
