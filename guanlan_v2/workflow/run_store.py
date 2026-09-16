# -*- coding: utf-8 -*-
"""Durable workflow-run records used by the canvas reconnect protocol.

The graph editor is a normal web page.  Navigating to another Guanlan board
destroys its JavaScript runtime, so an in-page ``fetch`` cannot be the owner of
a long backtest.  This store gives the server-owned worker a small, atomic JSON
checkpoint under ``.data/workflows/_runs``.  The UI keeps only the run id and
can reconnect after a reload or board switch.

Records are namespace checked in exactly the same way as saved workflows:
global callers cannot read workspace runs and one workspace cannot read
another workspace's run.  Runtime files live below the already ignored
``.data/workflows`` tree and are never source artifacts.
"""
from __future__ import annotations

import json
import math
import os
import re
import tempfile
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Optional

from guanlan_v2.workflow.store import _default_root, _workspace_id


_RUN_ID = re.compile(r"^wr_[0-9a-f]{12,40}$")
_ACTIVE = {"queued", "running"}


def _json_safe(value: Any) -> Any:
    """Return a strict-JSON value without inventing numeric evidence."""
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    return str(value)


class WorkflowRunStore:
    """Thread-safe atomic JSON store for reconnectable workflow runs."""

    def __init__(self, root: Optional[Path] = None):
        self.root = Path(root) if root is not None else (_default_root() / "_runs")
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        # Identifies workers owned by this server process/router instance.  If
        # the process restarts, a stale active record is failed honestly on
        # read instead of displaying "running" forever.
        self.instance_id = uuid.uuid4().hex

    @staticmethod
    def new_id() -> str:
        return "wr_" + uuid.uuid4().hex[:24]

    @staticmethod
    def valid_id(run_id: str) -> bool:
        return bool(_RUN_ID.fullmatch(str(run_id or "")))

    def _path(self, run_id: str) -> Path:
        if not self.valid_id(run_id):
            raise ValueError("run_id 格式不合法")
        return self.root / f"{run_id}.json"

    @staticmethod
    def _owner_matches(rec: Dict[str, Any], workspace_id: Optional[str]) -> bool:
        # Missing fields in old global records are legitimate. Conflicting or
        # malformed private metadata is not: never downgrade it to global.
        owner = rec.get("workspace_id")
        scope = rec.get("scope")
        if owner is None:
            return scope in (None, "global") and workspace_id is None
        if not isinstance(owner, str) or scope not in (None, "workspace"):
            return False
        try:
            valid_owner = _workspace_id(owner)
        except ValueError:
            return False
        return owner == valid_owner == workspace_id

    @staticmethod
    def _now_ms() -> int:
        return int(time.time() * 1000)

    @staticmethod
    def _sort_time(value: Any) -> int:
        """One corrupt historical timestamp must not hide all other runs."""
        try:
            return int(value or 0)
        except (TypeError, ValueError, OverflowError):
            return 0

    def _write(self, path: Path, rec: Dict[str, Any]) -> None:
        # Unique same-directory temporary files avoid collisions between
        # readers/router instances. The server still owns worker coordination;
        # atomic JSON replacement alone is not a distributed task lease.
        fd, name = tempfile.mkstemp(
            prefix=f".{path.stem}.", suffix=".tmp", dir=str(path.parent),
        )
        tmp = Path(name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                json.dump(_json_safe(rec), handle, ensure_ascii=False,
                          indent=1, allow_nan=False)
                handle.flush()
                os.fsync(handle.fileno())
            tmp.replace(path)
        finally:
            tmp.unlink(missing_ok=True)

    def _read(self, path: Path) -> Optional[Dict[str, Any]]:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001 - corrupt runtime record is unavailable
            return None
        if not isinstance(data, dict) or not self.valid_id(data.get("id")):
            return None
        return data if data["id"] == path.stem else None

    @staticmethod
    def _completed_status(result: Any) -> str:
        """Distinguish a usable result from a silently degraded success."""
        if isinstance(result, dict) and result.get("node_errors"):
            return "completed_with_errors"
        return "completed"

    def _fail_orphaned_locked(
        self, path: Path, rec: Dict[str, Any], *, enabled: bool,
    ) -> Dict[str, Any]:
        """Persist restart-orphan evidence while keeping legacy ``failed`` UI semantics."""
        if not (
            enabled
            and rec.get("status") in _ACTIVE
            and rec.get("worker_instance") != self.instance_id
        ):
            return rec
        now = self._now_ms()
        # These are progress events, not persisted node outputs/checkpoints.
        # A formerly running side-effecting node may already have committed;
        # do not convert it to done, queued, or silently replay it.
        states = rec.get("node_state")
        if isinstance(states, dict):
            rec["node_state"] = {
                nid: {**state, "state": "interrupted", "updated_at": now}
                if isinstance(state, dict) and state.get("state") == "running"
                else state
                for nid, state in states.items()
            }
        rec.update({
            "status": "failed",
            "reason": "服务已重启，本次运行已中断；可恢复查看原图与记录，不支持节点续算；重新运行前请确认副作用",
            "failure_kind": "orphaned",
            "orphaned": True,
            "interrupted": True,
            "orphaned_at": now,
            "ended_at": now,
            "updated_at": now,
        })
        self._write(path, rec)
        return rec

    def create(
        self,
        *,
        graph: Dict[str, Any],
        overrides: Dict[str, Any],
        name: str = "",
        workspace_id: Optional[str] = None,
        run_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        workspace_id = _workspace_id(workspace_id)
        rid = str(run_id or self.new_id())
        path = self._path(rid)
        now = self._now_ms()
        with self._lock:
            if path.exists():
                existing = self._read(path)
                if existing is not None and self._owner_matches(existing, workspace_id):
                    return existing  # client retry is idempotent
                raise ValueError("run_id 已存在")
            rec: Dict[str, Any] = {
                "id": rid,
                "name": str(name or "").strip(),
                "status": "queued",
                "workspace_id": workspace_id,
                "scope": "workspace" if workspace_id else "global",
                "graph": {
                    "nodes": list(graph.get("nodes") or []),
                    "edges": list(graph.get("edges") or []),
                },
                "overrides": dict(overrides or {}),
                "node_state": {},
                "created_at": now,
                "updated_at": now,
                "started_at": None,
                "ended_at": None,
                "result": None,
                "reason": None,
                "worker_instance": self.instance_id,
            }
            self._write(path, rec)
            self._prune_locked(workspace_id)
            return rec

    def _prune_locked(self, workspace_id: Optional[str], keep: int = 60) -> None:
        """Keep recent history while never pruning queued/running work."""
        rows = []
        for path in self.root.glob("wr_*.json"):
            rec = self._read(path)
            if rec is None or not self._owner_matches(rec, workspace_id):
                continue
            if rec.get("status") in _ACTIVE:
                continue
            rows.append((self._sort_time(rec.get("updated_at")), path))
        for _, path in sorted(rows, reverse=True)[keep:]:
            path.unlink(missing_ok=True)

    def get(
        self, run_id: str, *, workspace_id: Optional[str] = None,
        fail_orphaned: bool = True,
    ) -> Optional[Dict[str, Any]]:
        workspace_id = _workspace_id(workspace_id)
        try:
            path = self._path(run_id)
        except ValueError:
            return None
        with self._lock:
            rec = self._read(path)
            if rec is None or not self._owner_matches(rec, workspace_id):
                return None
            return self._fail_orphaned_locked(
                path, rec, enabled=fail_orphaned,
            )

    def list_runs(
        self,
        *,
        workspace_id: Optional[str] = None,
        limit: int = 50,
        statuses: Optional[set[str]] = None,
        fail_orphaned: bool = True,
    ) -> list[Dict[str, Any]]:
        """Return bounded, namespace-safe run summaries newest first.

        Heavy graph/result payloads stay behind the existing status-by-id call.
        Recovery means reconnecting/viewing records, never replaying nodes.
        """
        workspace_id = _workspace_id(workspace_id)
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 200:
            raise ValueError("limit 必须是 1..200 的整数")
        wanted = {str(value) for value in statuses} if statuses is not None else None
        rows: list[Dict[str, Any]] = []
        with self._lock:
            for path in self.root.glob("wr_*.json"):
                rec = self._read(path)
                if rec is None or not self._owner_matches(rec, workspace_id):
                    continue
                rec = self._fail_orphaned_locked(
                    path, rec, enabled=fail_orphaned,
                )
                if wanted is not None and str(rec.get("status")) not in wanted:
                    continue
                result = rec.get("result") if isinstance(rec.get("result"), dict) else {}
                terminal = result.get("terminal") if isinstance(result.get("terminal"), dict) else {}
                row = self.public(rec)
                row.pop("graph", None)
                row.pop("result", None)
                row["has_result"] = bool(result)
                row["result_ok"] = result.get("ok") if result else None
                row["terminal_kind"] = terminal.get("kind")
                row["node_error_count"] = len(result.get("node_errors") or [])
                row["warning_count"] = len(result.get("warnings") or [])
                rows.append(row)
        rows.sort(
            key=lambda row: (self._sort_time(row.get("updated_at")), str(row.get("id") or "")),
            reverse=True,
        )
        return rows[:limit]

    def update(self, run_id: str, **patch: Any) -> Optional[Dict[str, Any]]:
        path = self._path(run_id)
        with self._lock:
            rec = self._read(path)
            if rec is None:
                return None
            safe_patch = _json_safe(patch)
            if safe_patch.get("status") == "completed":
                safe_patch["status"] = self._completed_status(
                    safe_patch.get("result")
                )
            rec.update(safe_patch)
            rec["updated_at"] = self._now_ms()
            self._write(path, rec)
            return rec

    def update_node(self, run_id: str, node_id: str, node_type: str, state: str) -> None:
        path = self._path(run_id)
        with self._lock:
            rec = self._read(path)
            if rec is None:
                return
            states = dict(rec.get("node_state") or {})
            states[str(node_id)] = {
                "type": str(node_type or ""),
                "state": str(state or ""),
                "updated_at": self._now_ms(),
            }
            rec["node_state"] = states
            rec["updated_at"] = self._now_ms()
            self._write(path, rec)

    @staticmethod
    def public(rec: Dict[str, Any]) -> Dict[str, Any]:
        """Expose viewing capabilities, not a promise of resumable execution.

        Call after namespace-checked ``get``/``list_runs`` so restart-orphaned
        workers have already been reconciled. This method never executes work.
        """
        graph = rec.get("graph")
        nodes = graph.get("nodes") if isinstance(graph, dict) else None
        has_graph = isinstance(nodes, list) and bool(nodes)
        has_result = isinstance(rec.get("result"), dict) and bool(rec["result"])
        active = rec.get("status") in _ACTIVE
        if active:
            mode, reason = "reconnect", "连接现有服务端任务，只读取进度，不重复提交运行"
        elif has_result:
            mode, reason = "read_result", "恢复查看已保存结果；再次计算必须显式新建运行"
        elif has_graph:
            mode, reason = "restore_graph", "仅恢复原图与进度；无可续算检查点，重新运行前请确认副作用"
        else:
            mode, reason = "unavailable", "记录中没有可恢复的原图或结果"
        return {
            **{k: v for k, v in rec.items() if k != "worker_instance"},
            "recovery": {
                "mode": mode,
                "can_reconnect": active,
                "can_restore_graph": has_graph,
                "can_read_result": has_result,
                "can_resume": False,
                "requires_new_run": not active and has_graph,
                "reason": reason,
            },
        }
