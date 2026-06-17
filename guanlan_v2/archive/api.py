# -*- coding: utf-8 -*-
"""GL 档案总线的后端持久层(互通审计 P2-C)。

总线本体是浏览器 localStorage(guanlan:store:v1,清缓存即丢)。卡(/cards)与因子
(/factorlib)有后端库可回灌,但 **strategy(校场策略)/ research(研报入档)/
decision(选股决策)** 三类用户真产物此前无任何落点 —— 换浏览器/清缓存即永久丢失。

本层 = 极薄 JSON 文件影子库(总线仍是唯一事实源,这里只防丢):
  GET  /archive/list             → {ok, items:[artifact, …]}
  POST /archive/put  {artifact}  → 按 id 落 var/archive/<id>.json(upsert,原样存)
  POST /archive/remove {id}      → 删对应文件

约束:只收三类型且非 demo;id 白名单字符(防路径穿越)。前端桥在
ui/_shared/guanlan-bus.js:首拍拉 list 合并本地缺失 id(本地优先),
put/patch/remove 对三类型 fire-and-forget 上推。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from fastapi import APIRouter, Body
from fastapi.responses import JSONResponse

_DIR = Path(__file__).resolve().parent.parent.parent / "var" / "archive"
_ID_RE = re.compile(r"^[A-Za-z0-9_\-\.]{1,80}$")
_TYPES = {"strategy", "research", "decision"}


def build_archive_router() -> APIRouter:
    router = APIRouter(prefix="/archive", tags=["archive"])

    @router.get("/list")
    def archive_list():
        items = []
        if _DIR.is_dir():
            for p in sorted(_DIR.glob("*.json")):
                try:
                    items.append(json.loads(p.read_text(encoding="utf-8")))
                except Exception:  # noqa: BLE001 — 坏文件跳过,不挡其余
                    continue
        return JSONResponse({"ok": True, "items": items})

    @router.post("/put")
    def archive_put(body: dict = Body(default={})):
        art = body.get("artifact") or {}
        aid = str(art.get("id") or "")
        if not _ID_RE.match(aid) or art.get("type") not in _TYPES or art.get("demo"):
            return JSONResponse({"ok": False,
                                 "reason": "仅收 strategy/research/decision 非 demo 物料,id 须为白名单字符"})
        _DIR.mkdir(parents=True, exist_ok=True)
        (_DIR / f"{aid}.json").write_text(json.dumps(art, ensure_ascii=False), encoding="utf-8")
        return JSONResponse({"ok": True, "id": aid})

    @router.post("/remove")
    def archive_remove(body: dict = Body(default={})):
        aid = str(body.get("id") or "")
        if not _ID_RE.match(aid):
            return JSONResponse({"ok": False, "reason": "非法 id"})
        p = _DIR / f"{aid}.json"
        if p.exists():
            p.unlink()
        return JSONResponse({"ok": True, "id": aid})

    return router
