# -*- coding: utf-8 -*-
"""autonomy 端点(薄壳,无 prefix,fundflow 范式):GET /autonomy/jobs、
GET /autonomy/report/latest、POST /autonomy/run。

协程红线:async 端点内同步工作一律 asyncio.to_thread;POST /autonomy/run 本身只做
一次锁内状态检查+起后台线程(同步快),无需 to_thread。项目内 import 全放函数体内
(防 guanlan_v2.autonomy 包 __init__ → api → runtime/review_officer 的循环导入)。"""
from __future__ import annotations

import asyncio

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel


class AutonomyRunIn(BaseModel):
    """``POST /autonomy/run`` 入参;playbook 缺省 review_officer。"""

    playbook: str = "review_officer"


def build_autonomy_router() -> APIRouter:
    router = APIRouter(tags=["autonomy"])

    @router.get("/autonomy/jobs")
    async def jobs_ep(limit: int = 20):
        from guanlan_v2.autonomy.jobs import read_jobs
        from guanlan_v2.autonomy.runtime import _autonomy_public_state

        state = _autonomy_public_state()
        running_id = state.get("job_id") if state.get("running") else None
        jobs = await asyncio.to_thread(read_jobs, limit, running_id)
        return JSONResponse({"ok": True, "state": state, "jobs": jobs})

    @router.get("/autonomy/report/latest")
    async def report_latest_ep(date: str = ""):
        from guanlan_v2.autonomy.review_officer import read_report

        out = await asyncio.to_thread(read_report, date)
        return JSONResponse(out)

    @router.post("/autonomy/run")
    def run_ep(body: AutonomyRunIn):
        from guanlan_v2.autonomy.runtime import start_job_bg

        playbook = (body.playbook or "").strip() or "review_officer"
        return JSONResponse(start_job_bg(playbook))

    return router
