# -*- coding: utf-8 -*-
"""AI投研看板路由(薄壳挂载,无 prefix,路由自带 /industry/)。"""
from __future__ import annotations

import asyncio
from typing import Optional

from fastapi import APIRouter
from pydantic import BaseModel


class IngestReq(BaseModel):
    limit: Optional[int] = None


def build_industry_router() -> APIRouter:
    router = APIRouter()

    @router.get("/industry/board")
    async def board(refresh: int = 0):
        from . import aggregate
        return await asyncio.to_thread(aggregate.build_board, bool(refresh))

    @router.post("/industry/ingest")
    async def ingest_start(req: Optional[IngestReq] = None):
        from . import ingest
        limit = req.limit if req else None
        return await asyncio.to_thread(ingest.start_ingest, limit)

    @router.get("/industry/ingest_state")
    async def ingest_state():
        from . import ingest
        return await asyncio.to_thread(ingest.ingest_state)

    @router.get("/industry/segment/{sid}")
    async def segment(sid: str):
        from . import aggregate
        return await asyncio.to_thread(aggregate.segment_detail, sid)

    @router.get("/industry/doc/{doc_id}")
    async def doc(doc_id: str):
        from . import aggregate
        return await asyncio.to_thread(aggregate.doc_detail, doc_id)

    return router
