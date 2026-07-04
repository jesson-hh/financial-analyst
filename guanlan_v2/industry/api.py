# -*- coding: utf-8 -*-
"""AI投研看板路由(薄壳挂载,无 prefix,路由自带 /industry/)。"""
from __future__ import annotations

import asyncio
from typing import Optional

from fastapi import APIRouter
from pydantic import BaseModel


class IngestReq(BaseModel):
    limit: Optional[int] = None
    backfill_days: Optional[int] = None   # 深回填:无视水位按窗口扫历史(已抽取剔重)
    fw: Optional[str] = None              # 框架 id(缺省 ai_chain)


def build_industry_router() -> APIRouter:
    router = APIRouter()

    @router.get("/industry/frameworks")
    async def frameworks():
        from .framework import list_frameworks
        return await asyncio.to_thread(list_frameworks)

    @router.get("/industry/board")
    async def board(refresh: int = 0, fw: str = "ai_chain"):
        from . import aggregate
        return await asyncio.to_thread(aggregate.build_board, bool(refresh), fw)

    @router.post("/industry/ingest")
    async def ingest_start(req: Optional[IngestReq] = None):
        from . import ingest
        limit = req.limit if req else None
        backfill_days = req.backfill_days if req else None
        fw = (req.fw if req and req.fw else "ai_chain")
        return await asyncio.to_thread(ingest.start_ingest, limit, None, backfill_days, fw)

    @router.get("/industry/ingest_state")
    async def ingest_state(fw: str = "ai_chain"):
        from . import ingest
        return await asyncio.to_thread(ingest.ingest_state, fw)

    @router.get("/industry/segment/{sid}")
    async def segment(sid: str, fw: str = "ai_chain"):
        from . import aggregate
        return await asyncio.to_thread(aggregate.segment_detail, sid, fw)

    @router.get("/industry/doc/{doc_id}")
    async def doc(doc_id: str, fw: str = "ai_chain"):
        from . import aggregate
        return await asyncio.to_thread(aggregate.doc_detail, doc_id, fw)

    return router
