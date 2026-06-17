"""经验卡 REST 端点(guanlan 自有,挂到薄壳 app 上)。

``build_cards_router(store)`` 返回一个 ``/cards/*`` 路由组:
- ``GET  /cards/list?status=approved|draft|rejected|all``  列出卡(默认 approved)
- ``GET  /cards/{id}``                                     取单卡(404 若无)
- ``POST /cards``                                          upsert(无 id → next_id;status 默认 approved)
- ``POST /cards/{id}/status``                              迁移状态(draft/approved/rejected)

store 可注入(测试传 tmp 根);缺省 ``CardStore()`` 走 GUANLAN_WISDOM_ROOT/仓库 .data。
"""
from __future__ import annotations

from datetime import date
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from guanlan_v2.cards.card import Card
from guanlan_v2.cards.refine import refine_card
from guanlan_v2.cards.store import CardStore


class CardIn(BaseModel):
    id: Optional[str] = None
    title: str
    status: str = "approved"
    cat: str = "其他"
    tags: list[str] = Field(default_factory=list)
    verdict: str = "存疑"
    conf: int = 0
    ic: str = ""
    expr: str = ""
    insight: str = ""
    src: str = ""
    refs: list[str] = Field(default_factory=list)
    created: str = ""
    reviewed_by: Optional[str] = None


class StatusIn(BaseModel):
    status: str
    reviewed_by: Optional[str] = None


class RefineIn(BaseModel):
    draft: dict
    chat: list = Field(default_factory=list)
    instruction: str


def build_cards_router(store: Optional[CardStore] = None) -> APIRouter:
    store = store or CardStore()
    router = APIRouter(prefix="/cards", tags=["cards"])

    @router.get("/list")
    def list_cards(status: str = "approved"):
        cards = store.list_all() if status == "all" else store.list_by_status(status)
        return {"cards": [c.to_dict() for c in cards]}

    @router.get("/{card_id}")
    def get_card(card_id: str):
        try:
            return store.load(card_id).to_dict()
        except KeyError:
            raise HTTPException(status_code=404, detail=f"card not found: {card_id}")

    @router.post("")
    def upsert_card(body: CardIn):
        cid = body.id or store.next_id()
        card = Card(
            id=cid, title=body.title, status=body.status, cat=body.cat,
            tags=body.tags, verdict=body.verdict, conf=body.conf, ic=body.ic,
            expr=body.expr, insight=body.insight, src=body.src, refs=body.refs,
            created=body.created or date.today().isoformat(),
            reviewed_by=body.reviewed_by,
        )
        store.save(card)
        return card.to_dict()

    @router.post("/{card_id}/status")
    def set_status(card_id: str, body: StatusIn):
        try:
            store.set_status(card_id, body.status, reviewed_by=body.reviewed_by)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"invalid status: {body.status}")
        except KeyError:
            raise HTTPException(status_code=404, detail=f"card not found: {card_id}")
        return {"id": card_id, "status": body.status}

    @router.post("/refine")
    async def refine(body: RefineIn):
        """炼:经验卡 + 用户指令 → 引擎大模型(deepseek)精炼 → {reply, patch}。"""
        try:
            return await refine_card(body.draft, body.chat, body.instruction)
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"refine failed: {type(e).__name__}: {e}")

    return router
