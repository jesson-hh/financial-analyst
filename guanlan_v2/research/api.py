# -*- coding: utf-8 -*-
"""研究回路状态机 + 端点(照 screen/api.py regen 范式:daemon 线程+单飞锁+状态轮询)。

零 env 开关、零定时器、零子进程:回路只能被显式 POST 发起,合并即零行为变化。
锁纪律:threading.Lock 非可重入——快照只取一次锁绝不嵌套;线程体 finally 必清 running。
"""
from __future__ import annotations

import threading
import time
from typing import Any, Dict, Optional

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from guanlan_v2.research import loop as rloop
from guanlan_v2.research import store as rstore

_RESEARCH_LOCK = threading.Lock()
_RESEARCH_STATE: Dict[str, Any] = {
    "running": False, "phase": "idle", "label": "", "round_k": 0, "total_rounds": 0,
    "run_id": None, "started_at": None, "ended_at": None, "ok": None, "error": None,
    "lines": [],
}


def _research_public_state() -> Dict[str, Any]:
    """快照(只取一次锁,绝不嵌套)+ elapsed_sec;lines 截尾 [-12:]。"""
    with _RESEARCH_LOCK:
        s = dict(_RESEARCH_STATE)
        s["lines"] = list(s.get("lines") or [])[-12:]
    if s.get("started_at"):
        s["elapsed_sec"] = int((s.get("ended_at") or time.time()) - s["started_at"])
    return s


class ResearchLoopIn(BaseModel):
    """``POST /research/loop/start`` 入参(钳制在端点内做,服务端权威)。"""

    goal: str = ""
    max_rounds: int = 3
    min_rank_ic: float = 0.02
    universe: str = "csi300_active"
    freq: str = "month"
    start: Optional[str] = None
    end: Optional[str] = None


def _progress(**kw: Any) -> None:
    """loop 线程的进度回调:白名单键合并进状态 + label 追加进 lines(≤40)。"""
    with _RESEARCH_LOCK:
        for k, v in kw.items():
            if k in ("phase", "label", "round_k"):
                _RESEARCH_STATE[k] = v
        label = kw.get("label")
        if label:
            _RESEARCH_STATE["lines"].append(str(label))
            if len(_RESEARCH_STATE["lines"]) > 40:
                _RESEARCH_STATE["lines"] = _RESEARCH_STATE["lines"][-40:]


def _run_loop_thread(run_id: str, body: ResearchLoopIn) -> None:
    """线程体:跑回路;任何异常兜底,finally 必清 running(防卡死)。"""
    err: Optional[str] = None
    end_row: Dict[str, Any] = {}
    try:
        end_row = rloop.run_research_loop(
            run_id=run_id, goal=body.goal, max_rounds=body.max_rounds,
            min_rank_ic=body.min_rank_ic, universe=body.universe, freq=body.freq,
            start=body.start, end=body.end, progress=_progress)
    except Exception as exc:  # noqa: BLE001
        err = f"{type(exc).__name__}: {exc}"
    finally:
        ok = (err is None and bool(end_row.get("ok")))
        with _RESEARCH_LOCK:
            _RESEARCH_STATE.update(
                running=False, ended_at=time.time(), ok=ok,
                phase=("done" if ok else "error"),
                error=(err or end_row.get("error")))


def _start_loop_bg(body: ResearchLoopIn) -> Optional[str]:
    """抢单飞锁并起回路 daemon 线程;已在跑 → None。"""
    run_id = rloop.new_run_id()
    with _RESEARCH_LOCK:
        if _RESEARCH_STATE.get("running"):
            return None
        _RESEARCH_STATE.update(
            running=True, phase="starting", label="启动研究回路…", round_k=0,
            total_rounds=body.max_rounds, run_id=run_id,
            started_at=time.time(), ended_at=None, ok=None, error=None, lines=[])
    threading.Thread(target=lambda: _run_loop_thread(run_id, body),
                     name="research-loop", daemon=True).start()
    return run_id


def build_research_router() -> APIRouter:
    """研究回路路由组(/research/* 已核实引擎与 guanlan 两侧空闲,无遮蔽)。"""
    router = APIRouter(tags=["research"])

    @router.post("/research/loop/start")
    def research_loop_start(body: ResearchLoopIn):
        goal = (body.goal or "").strip()
        if not goal:
            return JSONResponse({"ok": False, "reason": "goal 不能为空"})
        body.goal = goal
        body.max_rounds = max(1, min(int(body.max_rounds or 3), 5))
        body.min_rank_ic = max(0.0, min(float(body.min_rank_ic or 0.02), 0.2))
        try:
            from guanlan_v2.workflow.api import _UNIVERSE_OK
            if body.universe not in _UNIVERSE_OK:
                return JSONResponse({"ok": False, "reason":
                                     f"universe 非法: {body.universe}(允许 {sorted(_UNIVERSE_OK)})"})
        except Exception:  # noqa: BLE001 — workflow 模块不可用时不拦(回路内求值会诚实失败)
            pass
        rid = _start_loop_bg(body)
        if rid is None:
            return JSONResponse({"ok": False, "reason": "already_running",
                                 "state": _research_public_state()})
        return JSONResponse({"ok": True, "started": True, "run_id": rid,
                             "state": _research_public_state()})

    @router.get("/research/loop/status")
    def research_loop_status():
        return JSONResponse({"ok": True, "state": _research_public_state()})

    @router.get("/research/runs")
    def research_runs(limit: int = 20):
        with _RESEARCH_LOCK:
            rid = _RESEARCH_STATE.get("run_id") if _RESEARCH_STATE.get("running") else None
        items = rstore.read_runs(limit=limit, running_run_id=rid)
        return JSONResponse({"ok": True, "runs": items, "n": len(items),
                             "path": str(rstore.RUNS_PATH)})

    @router.get("/research/rounds")
    def research_rounds(run_id: str = "", limit: int = 50):
        items = rstore.read_rounds(run_id=(run_id or None), limit=limit)
        return JSONResponse({"ok": True, "rounds": items, "n": len(items),
                             "path": str(rstore.ROUNDS_PATH)})

    return router
