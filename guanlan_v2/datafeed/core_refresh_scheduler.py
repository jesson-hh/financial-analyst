"""Read-only health probe for the reviewed core-data scheduled task."""
from __future__ import annotations

import json
import os
import subprocess
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Callable


TASK_NAME = "Guanlan Core Data Refresh"
EXPECTED_COMMAND = Path("D:/app/miniconda/envs/stocks/python.exe")
EXPECTED_RUNNER = Path(__file__).resolve().parents[2] / "scripts" / "run_scheduled_core_data_refresh.py"
EXPECTED_WORKDIR = Path(__file__).resolve().parents[2]
STATE_PATH = EXPECTED_WORKDIR / "var" / "core-data-refresh" / "latest.json"
_NS = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}
_CACHE: dict[str, Any] = {"at": 0.0, "value": None}
_CACHE_SECONDS = 60.0


def _norm_path(value: str | Path) -> str:
    return os.path.normcase(os.path.normpath(str(value).strip().strip('"')))


def _text(root: ET.Element, path: str) -> str:
    node = root.find(path, _NS)
    return str(node.text or "").strip() if node is not None else ""


def _read_last_receipt(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict) or payload.get("schema_version") != "core-data-refresh/v1":
        return None
    # Closed projection: the receipt itself is value-free, but keep the health
    # response restricted to operational fields anyway.
    index = payload.get("index_daily")
    index = index if isinstance(index, dict) else {}
    return {
        "status": payload.get("status"),
        "started_at": payload.get("started_at"),
        "ended_at": payload.get("ended_at"),
        "returncode": payload.get("returncode"),
        "timed_out": bool(payload.get("timed_out")),
        "log_name": payload.get("log_name"),
        "core_status": payload.get("core_status"),
        "index_daily": {
            "code": "399300.SZ",
            "status": index.get("status") or "pending_first_run",
            "blocker": index.get("blocker"),
            "expected_session": index.get("expected_session"),
            "new_latest": index.get("new_latest"),
            "appended_rows": index.get("appended_rows"),
            "applied": bool(index.get("applied")),
        },
    }


def _probe_uncached(
    *,
    run: Callable[..., subprocess.CompletedProcess] = subprocess.run,
    state_path: Path = STATE_PATH,
) -> dict[str, Any]:
    if os.name != "nt":
        return {
            "status": "unknown", "enabled": False,
            "note": "Windows Task Scheduler probe unavailable on this host",
        }
    try:
        result = run(
            ["schtasks.exe", "/Query", "/TN", TASK_NAME, "/XML"],
            capture_output=True,
            timeout=5,
            check=False,
        )
    except Exception:  # never project host command errors
        return {
            "status": "error", "enabled": False,
            "note": "核心数据计划任务查询失败",
        }
    if int(result.returncode) != 0:
        return {
            "status": "missing", "enabled": False,
            "task_name": TASK_NAME,
            "note": "核心数据盘后生产任务未注册",
        }
    try:
        raw = result.stdout
        xml = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else str(raw)
        root = ET.fromstring(xml)
    except Exception:
        return {
            "status": "error", "enabled": False,
            "task_name": TASK_NAME,
            "note": "核心数据计划任务 XML 无法校验",
        }

    command = _text(root, ".//t:Actions/t:Exec/t:Command")
    arguments = _text(root, ".//t:Actions/t:Exec/t:Arguments")
    working_directory = _text(root, ".//t:Actions/t:Exec/t:WorkingDirectory")
    logon_type = _text(root, ".//t:Principals/t:Principal/t:LogonType")
    multiple_instances = _text(root, ".//t:Settings/t:MultipleInstancesPolicy")
    start_when_available = _text(root, ".//t:Settings/t:StartWhenAvailable").lower()
    execution_limit = _text(root, ".//t:Settings/t:ExecutionTimeLimit")
    task_enabled = _text(root, ".//t:Settings/t:Enabled").lower()
    start_boundary = _text(root, ".//t:Triggers/t:CalendarTrigger/t:StartBoundary")
    days = {
        node.tag.rsplit("}", 1)[-1]
        for node in root.findall(".//t:ScheduleByWeek/t:DaysOfWeek/*", _NS)
    }
    expected_arguments = f'"{EXPECTED_RUNNER}" --execute'
    checks = {
        "direct_python": _norm_path(command) == _norm_path(EXPECTED_COMMAND),
        "closed_arguments": arguments == expected_arguments,
        "working_directory": _norm_path(working_directory) == _norm_path(EXPECTED_WORKDIR),
        "interactive_logon": logon_type == "InteractiveToken",
        "single_flight": multiple_instances == "IgnoreNew",
        "start_when_available": start_when_available == "true",
        "eight_hour_limit": execution_limit == "PT8H",
        # Omitted Enabled means Task Scheduler's documented default=true.
        "task_enabled": task_enabled != "false",
        "weekday_trigger": days == {"Monday", "Tuesday", "Wednesday", "Thursday", "Friday"},
        "after_close_trigger": "T18:30:00" in start_boundary,
    }
    blockers = [name for name, passed in checks.items() if not passed]
    last = _read_last_receipt(Path(state_path))
    last_failed = bool(last and last.get("status") in {"failed", "partial_failed"})
    index = (last or {}).get("index_daily") or {"code": "399300.SZ", "status": "pending_first_run"}
    index_ready = index.get("status") == "complete"
    if blockers:
        status, note = "error", "核心数据计划任务配置漂移: " + ",".join(blockers)
    elif last_failed:
        status, note = "error", "核心数据计划任务最近一次执行失败"
    else:
        status = "fresh"
        note = "" if index_ready else "核心调度已注册；沪深300追加链尚无成功回执"
    return {
        "status": status,
        "quality": "degraded" if blockers or last_failed or not index_ready else "complete",
        "enabled": not blockers,
        "task_name": TASK_NAME,
        "direct_python": checks["direct_python"],
        "checks": checks,
        "blockers": blockers,
        "last_run": last,
        "index_daily": index,
        "note": note,
    }


def probe_core_refresh_scheduler() -> dict[str, Any]:
    """Return a cached live Task Scheduler configuration probe."""

    now = time.monotonic()
    cached = _CACHE.get("value")
    if isinstance(cached, dict) and now - float(_CACHE.get("at") or 0.0) < _CACHE_SECONDS:
        return dict(cached)
    value = _probe_uncached()
    _CACHE.update({"at": now, "value": dict(value)})
    return value


__all__ = ["TASK_NAME", "probe_core_refresh_scheduler"]
