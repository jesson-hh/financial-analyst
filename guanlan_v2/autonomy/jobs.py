# -*- coding: utf-8 -*-
"""autonomy job 池账本:var/jobs/jobs.jsonl 事件流 + var/jobs/<job_id>/ 工作目录。
append 吞异常返 bool(落盘失败不阻断 job,由调用方显形);read 侧坏行跳过绝不抛。"""
from __future__ import annotations

import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

JOBS_DIR = Path(__file__).resolve().parents[2] / "var" / "jobs"
JOBS_PATH = JOBS_DIR / "jobs.jsonl"


def new_job_id() -> str:
    return "aj_" + uuid.uuid4().hex[:10]


def job_dir(job_id: str) -> Path:
    d = JOBS_DIR / str(job_id)
    d.mkdir(parents=True, exist_ok=True)
    return d


def append_event(row: Dict[str, Any]) -> bool:
    try:
        rec = dict(row)
        rec.setdefault("ts", datetime.now().isoformat(timespec="seconds"))
        JOBS_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(JOBS_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
        return True
    except Exception:  # noqa: BLE001
        return False


def read_jobs(limit: int = 20, running_job_id: Optional[str] = None) -> List[Dict[str, Any]]:
    """start/end 合并成每 job 一条,新在前。status: done/failed(有 end)、
    running(==running_job_id)、interrupted(无 end 且非 running=进程重启中断,诚实显形)。"""
    lim = max(1, min(int(limit), 100))
    if not JOBS_PATH.exists():
        return []
    jobs: Dict[str, Dict[str, Any]] = {}
    order: List[str] = []
    for line in open(JOBS_PATH, encoding="utf-8"):
        try:
            r = json.loads(line)
        except Exception:  # noqa: BLE001
            continue
        jid = str(r.get("job_id") or "")
        if not jid:
            continue
        if jid not in jobs:
            jobs[jid] = {"job_id": jid}
            order.append(jid)
        j = jobs[jid]
        if r.get("kind") == "start":
            j.update(playbook=r.get("playbook"), started_ts=r.get("ts"))
        elif r.get("kind") == "end":
            j.update(ended_ts=r.get("ts"), ok=r.get("ok"), error=r.get("error"),
                     report=r.get("report"))
    out = []
    for jid in reversed(order):
        j = jobs[jid]
        if "ended_ts" in j:
            j["status"] = "done" if j.get("ok") else "failed"
        elif jid == running_job_id:
            j["status"] = "running"
        else:
            j["status"] = "interrupted"
        out.append(j)
    return out[:lim]
