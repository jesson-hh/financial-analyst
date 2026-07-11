# -*- coding: utf-8 -*-
"""autonomy 运行时:单飞状态机 + 预算护栏 + daemon 线程跑 playbook。
红线:playbook 只读+写报告;任何异常 finally 必清 running 并落 end 事件(诚实显形)。"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from guanlan_v2.autonomy import jobs as J

JOB_MAX_LLM = 12          # 每 job LLM 动作(fork/单发)上限
JOB_DEADLINE_SEC = 1800   # 全 job 软墙钟:段间检查,超则跳过余段标 degraded
SECTION_TIMEOUT_SEC = 300  # 每段(fork/单发)硬超时

_AUTONOMY_LOCK = threading.Lock()
_AUTONOMY_STATE: Dict[str, Any] = {
    "running": False, "phase": "idle", "label": "", "job_id": None, "playbook": None,
    "started_at": None, "ended_at": None, "ok": None, "error": None, "lines": []}


class Budget:
    def __init__(self, max_llm: int = JOB_MAX_LLM):
        self.max_llm = max(1, int(max_llm))
        self.used = 0
        self.exhausted = False

    def charge(self, n: int = 1) -> bool:
        if self.used + n > self.max_llm:
            self.exhausted = True
            return False
        self.used += n
        return True


@dataclass
class JobCtx:
    job_id: str
    dir: Optional[Path]
    budget: Budget
    progress: Callable[..., None]
    deadline_ts: float
    extras: Dict[str, Any] = field(default_factory=dict)

    def over_deadline(self) -> bool:
        return time.time() >= self.deadline_ts


def _progress(**kw) -> None:
    with _AUTONOMY_LOCK:
        for k in ("phase", "label"):
            if k in kw:
                _AUTONOMY_STATE[k] = kw[k]
        if kw.get("label"):
            _AUTONOMY_STATE["lines"].append(str(kw["label"]))
            if len(_AUTONOMY_STATE["lines"]) > 40:
                _AUTONOMY_STATE["lines"][:] = _AUTONOMY_STATE["lines"][-40:]


def _autonomy_public_state() -> Dict[str, Any]:
    with _AUTONOMY_LOCK:
        st = dict(_AUTONOMY_STATE)
        st["lines"] = list(st["lines"])[-12:]
    if st.get("started_at"):
        st["elapsed_sec"] = int((st.get("ended_at") or time.time()) - st["started_at"])
    return st


def _playbooks() -> Dict[str, Callable[[JobCtx], Dict[str, Any]]]:
    from guanlan_v2.autonomy.playbooks import PLAYBOOKS
    return PLAYBOOKS


def _PLAYBOOKS_FOR_TEST() -> Dict[str, Any]:
    """测试注入口(monkeypatch.setitem);生产不调。"""
    return _playbooks()


def _run_job_thread(job_id: str, playbook: str) -> None:
    ok, err, report = False, None, None
    try:
        fn = _playbooks()[playbook]
        ctx = JobCtx(job_id=job_id, dir=J.job_dir(job_id), budget=Budget(),
                     progress=_progress, deadline_ts=time.time() + JOB_DEADLINE_SEC)
        out = fn(ctx) or {}
        ok = bool(out.get("ok"))
        err = out.get("error")
        report = out.get("report")
    except Exception as exc:  # noqa: BLE001
        ok, err = False, f"{type(exc).__name__}: {exc}"
    finally:
        J.append_event({"job_id": job_id, "kind": "end", "ok": ok, "error": err,
                        "report": report})
        with _AUTONOMY_LOCK:
            _AUTONOMY_STATE.update(running=False, ended_at=time.time(), ok=ok,
                                   error=err, phase="done" if ok else "error")


def start_job_bg(playbook: str) -> Dict[str, Any]:
    if playbook not in _playbooks():
        return {"ok": False, "reason": "unknown_playbook"}
    with _AUTONOMY_LOCK:
        if _AUTONOMY_STATE["running"]:
            return {"ok": False, "reason": "already_running",
                    "state": dict(_AUTONOMY_STATE, lines=[])}
        job_id = J.new_job_id()
        _AUTONOMY_STATE.update(running=True, phase="starting", label="", job_id=job_id,
                               playbook=playbook, started_at=time.time(), ended_at=None,
                               ok=None, error=None, lines=[])
    J.append_event({"job_id": job_id, "kind": "start", "playbook": playbook})
    threading.Thread(target=_run_job_thread, args=(job_id, playbook),
                     name="autonomy-job", daemon=True).start()
    return {"ok": True, "job_id": job_id}


def maybe_enqueue_daily_review(note: str) -> bool:
    """rescore 落定后的调度钩子(挂在 screen/rescore.py `_run_thread` finally 里,ok 分支
    才调):三门全过才排队复盘官——①env `GUANLAN_REVIEW_DAILY=="1"`、②note=="daily-scheduler"
    (仅日跑重排收尾才排队,手动重排不排)、③今日尚未跑过(read_jobs 里查不到当日
    playbook==review_officer 且 status∈{done,running} 的 job,防重复排队)。
    自吞异常返回 False——排队失败绝不拖垮调用方(rescore 主流程)。"""
    try:
        import os
        if os.environ.get("GUANLAN_REVIEW_DAILY") != "1":
            return False
        if note != "daily-scheduler":
            return False
        today = time.strftime("%Y-%m-%d")
        for j in J.read_jobs(limit=20):
            if (j.get("playbook") == "review_officer"
                    and str(j.get("started_ts") or "")[:10] == today
                    and j.get("status") in ("done", "running")):
                return False
        return bool(start_job_bg("review_officer").get("ok"))
    except Exception:  # noqa: BLE001 — 排队失败绝不拖垮 rescore 主流程
        return False
