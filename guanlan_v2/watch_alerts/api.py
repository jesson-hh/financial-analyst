# -*- coding: utf-8 -*-
"""全局预警 HTTP API。研究只读；规则写入由界面/LLM 的确认门负责。"""
from __future__ import annotations

import asyncio
from typing import Any, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .core import AlertStore, WatchAlertEngine, get_default_engine
from .research import research_alert


class RuleCreate(BaseModel):
    name: str
    kind: str
    threshold: float
    code: Optional[str] = None
    window_minutes: Optional[int] = None
    down_ratio_min: Optional[float] = None
    universe: Optional[str] = None
    sample_size: Optional[int] = None
    enabled: bool = False
    cooldown_minutes: int = 30
    hysteresis: float = 0.2
    note: str = ""
    creator: str = "human"


class RulePatch(BaseModel):
    name: Optional[str] = None
    kind: Optional[str] = None
    threshold: Optional[float] = None
    code: Optional[str] = None
    window_minutes: Optional[int] = None
    down_ratio_min: Optional[float] = None
    universe: Optional[str] = None
    sample_size: Optional[int] = None
    enabled: Optional[bool] = None
    cooldown_minutes: Optional[int] = None
    hysteresis: Optional[float] = None
    note: Optional[str] = None
    creator: Optional[str] = None


class ResearchIn(BaseModel):
    scope: str = Field(default="market", pattern="^(market|stock)$")
    code: str = ""
    start: str = ""
    end: str = ""
    window_minutes: int = 15
    threshold_pct: Optional[float] = None
    down_ratio_min: float = 0.7
    forward_minutes: int = 30
    max_codes: int = 60


def _clean(model: BaseModel) -> dict[str, Any]:
    return model.model_dump(exclude_none=True)


def build_watch_alerts_router(*, store: Optional[AlertStore] = None,
                              engine: Optional[WatchAlertEngine] = None) -> APIRouter:
    worker = engine or (WatchAlertEngine(store=store) if store else get_default_engine())
    db = worker.store
    router = APIRouter(prefix="/watch-alerts", tags=["watch-alerts"])

    @router.get("/rules")
    def rules():
        return {"ok": True, "rules": db.list_rules()}

    @router.post("/rules")
    def create(body: RuleCreate):
        try:
            return {"ok": True, "rule": db.create_rule(_clean(body))}
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @router.patch("/rules/{rule_id}")
    def update(rule_id: str, body: RulePatch):
        try:
            return {"ok": True, "rule": db.update_rule(rule_id, _clean(body))}
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="规则不存在") from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @router.delete("/rules/{rule_id}")
    def delete(rule_id: str):
        try:
            return {"ok": True, "deleted": db.delete_rule(rule_id)}
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="规则不存在") from exc

    @router.get("/events")
    def events(limit: int = 50, since: str = ""):
        return {"ok": True, "events": db.list_events(limit=limit, since=since or None)}

    @router.get("/status")
    def status():
        return worker.status()

    @router.post("/tick")
    async def tick():
        """Manual deterministic tick for diagnostics; market-hours guards still apply."""
        return await asyncio.to_thread(worker.tick)

    @router.post("/research")
    async def research(body: ResearchIn):
        return await asyncio.to_thread(research_alert, **_clean(body))

    return router
