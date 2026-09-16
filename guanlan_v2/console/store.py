"""ConsoleStore — 帷幄会话的文件级事实库。

每会话一目录:var/console/sessions/<sid>/
  meta.json     {id,title,created,updated,status,plan,next_event_id}
  events.jsonl  一行一事件,只追加,id 单调递增
  buddy_state.json  私有 LLM 协议状态(不进入 SSE),原子覆盖
  codex_state.json  私有 Codex thread 映射(不含订阅凭证),原子覆盖
  data_catalog_state.json  私有数据目录已确认指纹,原子覆盖
恢复优先读 buddy_state.json；旧会话/坏快照再降级重放 events.jsonl。
线程安全(工具在 to_thread 里写)。
**单实例契约**:一个进程只建一个 ConsoleStore(server 在 router 工厂里建一次,工具经
ContextVar 拿同一实例);锁是实例级,多实例并发写同一目录不受保护。
"""
from __future__ import annotations

import json
import os
import re
import shutil
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from guanlan_v2.research.evidence import (
    normalise_report_evidence_many,
    public_evidence,
)
from guanlan_v2.console.workspaces import GENERAL_WORKSPACE_ID, WorkspaceStore

_DEFAULT_ROOT = Path(__file__).resolve().parents[2] / "var" / "console"
_SID_RE = re.compile(r"^cs_[0-9a-f]{12}$")
_STORAGE_SID_RE = re.compile(r"^(?:cs_[0-9a-f]{12}|plan-approvals)$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_DATA_CATALOG_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_.:-]{0,127}$")
_CODEX_THREAD_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,159}$")
_WORKSPACE_ID_RE = re.compile(r"^rw_(?:general|[0-9a-f]{12})$")


def _default_root() -> Path:
    configured = str(os.environ.get("GUANLAN_CONSOLE_ROOT") or "").strip()
    return Path(configured).expanduser() if configured else _DEFAULT_ROOT


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _valid_buddy_state(state: Any) -> bool:
    """Validate a complete OpenAI/DeepSeek tool-message protocol chain."""
    if not isinstance(state, dict) or state.get("schema_version") != "1":
        return False
    checkpoint = state.get("checkpoint_event_id")
    messages = state.get("messages")
    if not isinstance(checkpoint, int) or checkpoint < 0 or not isinstance(messages, list):
        return False
    completed_turn = state.get("completed_turn")
    if completed_turn is not None:
        if not isinstance(completed_turn, dict):
            return False
        turn_id = completed_turn.get("turn_id")
        source_event_id = completed_turn.get("source_user_event_id")
        source = completed_turn.get("source")
        final_text = completed_turn.get("final_text")
        if not isinstance(turn_id, str) or not turn_id:
            return False
        if type(source_event_id) is not int or source_event_id < 0:
            return False
        if not isinstance(source, str) or not source:
            return False
        if not isinstance(final_text, str):
            return False

    pending_tool_ids: Optional[set[str]] = None
    for message in messages:
        if not isinstance(message, dict):
            return False
        role = message.get("role")
        if role not in {"user", "assistant", "tool"} or "content" not in message:
            return False
        if "reasoning_content" in message and message["reasoning_content"] is not None \
                and not isinstance(message["reasoning_content"], str):
            return False

        if pending_tool_ids is not None:
            if role != "tool":
                return False
            tool_call_id = message.get("tool_call_id")
            if not isinstance(tool_call_id, str) or tool_call_id not in pending_tool_ids:
                return False
            pending_tool_ids.remove(tool_call_id)
            if not pending_tool_ids:
                pending_tool_ids = None
            continue

        if role == "tool":
            return False
        if "tool_calls" not in message:
            continue
        calls = message.get("tool_calls")
        if role != "assistant" or not isinstance(calls, list):
            return False
        if not calls:
            continue
        ids: set[str] = set()
        for call in calls:
            if not isinstance(call, dict):
                return False
            call_id = call.get("id")
            if not isinstance(call_id, str) or not call_id or call_id in ids:
                return False
            ids.add(call_id)
        pending_tool_ids = ids
    return pending_tool_ids is None


def _valid_data_catalog_state(state: Any) -> bool:
    """Validate the exact private per-session catalog acknowledgement shape."""
    if not isinstance(state, dict) or set(state) != {
        "schema_version", "catalog_digest", "datasets", "sources",
    }:
        return False
    if state.get("schema_version") != "1":
        return False
    if not isinstance(state.get("catalog_digest"), str) \
            or _SHA256_RE.fullmatch(state["catalog_digest"]) is None:
        return False
    for key in ("datasets", "sources"):
        fingerprints = state.get(key)
        if not isinstance(fingerprints, dict):
            return False
        for item_id, digest in fingerprints.items():
            if not isinstance(item_id, str) \
                    or _DATA_CATALOG_ID_RE.fullmatch(item_id) is None:
                return False
            if not isinstance(digest, str) or _SHA256_RE.fullmatch(digest) is None:
                return False
    return True


def _valid_codex_state(state: Any) -> bool:
    """Validate the token-free, private Console-to-Codex thread mapping."""
    if not isinstance(state, dict) or set(state) != {
        "schema_version", "backend", "thread_id", "workspace_id",
        "workspace_root_sha256", "workspace_subject_sha256",
        "checkpoint_event_id", "health",
        "created_at", "updated_at", "last_error", "pending_receipts",
        "tool_transport", "active_turn_id", "turn_nonce",
    }:
        return False
    if state.get("schema_version") != "1" or state.get("backend") != "codex":
        return False
    thread_id = state.get("thread_id")
    workspace_id = state.get("workspace_id")
    if not isinstance(thread_id, str) \
            or _CODEX_THREAD_ID_RE.fullmatch(thread_id) is None:
        return False
    if not isinstance(workspace_id, str) \
            or _WORKSPACE_ID_RE.fullmatch(workspace_id) is None:
        return False
    root_digest = state.get("workspace_root_sha256")
    if not isinstance(root_digest, str) or _SHA256_RE.fullmatch(root_digest) is None:
        return False
    subject_digest = state.get("workspace_subject_sha256")
    if not isinstance(subject_digest, str) \
            or _SHA256_RE.fullmatch(subject_digest) is None:
        return False
    checkpoint = state.get("checkpoint_event_id")
    if type(checkpoint) is not int or checkpoint < 0:
        return False
    if state.get("health") not in {"ready", "inflight", "poisoned"}:
        return False
    active_turn_id = state.get("active_turn_id")
    if active_turn_id is not None and (
            not isinstance(active_turn_id, str)
            or _CODEX_THREAD_ID_RE.fullmatch(active_turn_id) is None):
        return False
    turn_nonce = state.get("turn_nonce")
    if turn_nonce is not None and (
            not isinstance(turn_nonce, str)
            or re.fullmatch(r"[0-9a-f]{32}", turn_nonce) is None):
        return False
    if state.get("health") == "ready" and (
            active_turn_id is not None or turn_nonce is not None):
        return False
    if state.get("health") == "inflight" and turn_nonce is None:
        return False
    for key in ("created_at", "updated_at"):
        value = state.get(key)
        if not isinstance(value, str) or not value or len(value) > 64:
            return False
    last_error = state.get("last_error")
    if last_error is not None and (
            not isinstance(last_error, str) or len(last_error) > 1000):
        return False
    receipts = state.get("pending_receipts")
    if not isinstance(receipts, list) or len(receipts) > 8:
        return False
    if any(not isinstance(item, str) or not item or len(item) > 4000
           for item in receipts):
        return False
    return state.get("tool_transport") == "mcp:guanlan-scoped"


class ConsoleStore:
    def __init__(self, root: Optional[Path] = None):
        self.root = Path(root) if root is not None else _default_root()
        self.sessions_dir = self.root / "sessions"
        self.sessions_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()   # 可重入:merge_meta_sub 等锁内方法可互调(H4 读路径加锁铺路)
        self._event_scan_offsets: Dict[str, tuple[int, int]] = {}
        self.workspace_store = WorkspaceStore(self.root)

    # ── 会话 ──
    def _sdir(self, sid: str) -> Path:
        if not isinstance(sid, str) or _STORAGE_SID_RE.fullmatch(sid) is None:
            raise ValueError("invalid canonical session id")
        return self.sessions_dir / sid

    def create_session(self, title: str = "新对话",
                       workspace_id: str = GENERAL_WORKSPACE_ID) -> Dict[str, Any]:
        # 会话只能绑定真实、未归档空间。旧调用不传值时稳定进入「通用研究」。
        self.workspace_store.require(workspace_id, active_only=True)
        sid = "cs_" + uuid.uuid4().hex[:12]
        meta = {"id": sid, "title": title, "created": _now(), "updated": _now(),
                "status": "idle", "plan": [], "next_event_id": 1,
                "workspace_id": workspace_id, "archived": False}
        d = self._sdir(sid)
        d.mkdir(parents=True, exist_ok=True)
        (d / "events.jsonl").touch()
        self._save_meta(meta)
        return meta

    def get_meta(self, sid: str) -> Optional[Dict[str, Any]]:
        with self._lock:   # 读持锁:不读 _save_meta replace 瞬间的半状态(Windows 上还会撞句柄)
            try:
                p = self._sdir(sid) / "meta.json"
            except ValueError:
                return None
            if not p.exists():
                return None
            try:
                meta = json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                return None
            if not isinstance(meta, dict):
                return None
            if meta.get("id") != sid:
                return None
            # Additive compatibility: old conversations remain byte-for-byte on
            # disk until their next ordinary write, while every read/API response
            # places them in the stable general workspace.
            meta.setdefault("workspace_id", GENERAL_WORKSPACE_ID)
            meta.setdefault("archived", False)
            return meta

    def _save_meta(self, meta: Dict[str, Any]) -> None:
        p = self._sdir(meta["id"]) / "meta.json"
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
        for attempt in range(3):
            try:
                tmp.replace(p)
                return
            except PermissionError:   # Windows:目标被并发读句柄短暂占用 → 稍候重试,3 次仍败则抛
                if attempt == 2:
                    raise
                time.sleep(0.05)

    def list_sessions(self) -> List[Dict[str, Any]]:
        with self._lock:   # RLock:内部 get_meta 重入安全
            out = []
            if self.sessions_dir.exists():
                for d in self.sessions_dir.iterdir():
                    m = self.get_meta(d.name)
                    if m:
                        out.append(m)
            return sorted(out, key=lambda m: m.get("updated", ""), reverse=True)

    def delete_session(self, sid: str) -> bool:
        if not _SID_RE.fullmatch(sid or ""):
            return False   # 防路径穿越(../ 等):非法 sid 一律拒,不碰文件系统
        with self._lock:
            d = self._sdir(sid)
            if not d.exists():
                return False
            try:
                shutil.rmtree(d)        # rmtree:会话目录含子目录(笔记/附件)也整树删,iterdir+unlink 对目录会炸
            except PermissionError:     # Windows 句柄短暂占用 → 重试一次,再败则抛
                time.sleep(0.05)
                shutil.rmtree(d)
            return True

    # ── 事件 ──
    def append_event(self, sid: str, etype: str, **fields: Any) -> Dict[str, Any]:
        """追加一条事件。meta 先行落盘:崩溃产生 id 空洞而非重复。"""
        with self._lock:
            meta = self.get_meta(sid)
            if meta is None:
                raise KeyError(f"unknown session {sid}")
            ev = {"id": meta["next_event_id"], "ts": _now(), "type": etype}
            ev.update(fields)
            meta["next_event_id"] = ev["id"] + 1
            meta["updated"] = _now()
            self._save_meta(meta)
            with (self._sdir(sid) / "events.jsonl").open("a", encoding="utf-8") as f:
                f.write(json.dumps(ev, ensure_ascii=False) + "\n")
            return ev

    def read_events(self, sid: str, after_id: int = 0, limit: int = 2000) -> List[Dict[str, Any]]:
        """读事件。limit 取**尾部**最近 N 条(SSE snapshot 语义:新订阅者要最近上下文);
        增量轮询用 after_id,两者不混用。"""
        with self._lock:   # 读持锁:不与 append_event 的写句柄并发(Windows 共享读写易撞)
            try:
                p = self._sdir(sid) / "events.jsonl"
            except ValueError:
                return []
            if not p.exists():
                return []
            out: List[Dict[str, Any]] = []
            for line in p.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except Exception:
                    continue
                if ev.get("id", 0) > after_id:
                    out.append(ev)
            return out[-limit:]

    def read_events_forward(self, sid: str, after_id: int = 0,
                            limit: int = 200) -> List[Dict[str, Any]]:
        """Read the earliest persisted page strictly after ``after_id``."""
        if limit <= 0:
            return []
        with self._lock:
            try:
                p = self._sdir(sid) / "events.jsonl"
            except ValueError:
                return []
            if not p.exists():
                return []
            out: List[Dict[str, Any]] = []
            with p.open("r", encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        ev = json.loads(line)
                    except Exception:
                        continue
                    if ev.get("id", 0) <= after_id:
                        continue
                    out.append(ev)
                    if len(out) >= limit:
                        break
            return out

    def iter_events_forward(self, sid: str, after_id: int = 0,
                            through_id: Optional[int] = None):
        """Stream one stable id range without materializing JSONL history."""
        with self._lock:
            try:
                p = self._sdir(sid) / "events.jsonl"
            except ValueError:
                return
            if not p.exists():
                return
            cached_id, cached_offset = self._event_scan_offsets.get(sid, (0, 0))
            file_size = p.stat().st_size
            if after_id < cached_id or cached_offset > file_size:
                cached_id, cached_offset = 0, 0
            scan_id = max(int(after_id), int(cached_id))
            resume_offset = int(cached_offset)
            with p.open("rb") as handle:
                handle.seek(resume_offset)
                try:
                    while True:
                        line_start = handle.tell()
                        raw_line = handle.readline()
                        if not raw_line:
                            resume_offset = handle.tell()
                            break
                        line_end = handle.tell()
                        try:
                            event = json.loads(raw_line.decode("utf-8").strip())
                        except Exception:
                            resume_offset = line_end
                            continue
                        if not isinstance(event, dict):
                            resume_offset = line_end
                            continue
                        event_id = event.get("id")
                        if (
                            isinstance(event_id, bool)
                            or not isinstance(event_id, int)
                            or event_id <= after_id
                        ):
                            resume_offset = line_end
                            continue
                        if through_id is not None and event_id > through_id:
                            scan_id = max(scan_id, int(through_id))
                            resume_offset = line_start
                            break
                        scan_id = max(scan_id, event_id)
                        resume_offset = line_end
                        yield event
                finally:
                    self._event_scan_offsets[sid] = (scan_id, resume_offset)

    def read_events_by_ids(
        self, sid: str, event_ids: Any,
    ) -> Dict[int, Dict[str, Any]]:
        """Read a small exact-id set in one file pass for provenance checks."""
        if not isinstance(event_ids, (set, frozenset, list, tuple)):
            raise TypeError("event_ids must be a collection")
        wanted = {
            event_id for event_id in event_ids
            if isinstance(event_id, int) and not isinstance(event_id, bool) and event_id > 0
        }
        if not wanted:
            return {}
        found: Dict[int, Dict[str, Any]] = {}
        with self._lock:
            try:
                p = self._sdir(sid) / "events.jsonl"
            except ValueError:
                return {}
            if not p.exists():
                return {}
            with p.open("r", encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        event = json.loads(line)
                    except Exception:
                        continue
                    if not isinstance(event, dict):
                        continue
                    event_id = event.get("id")
                    if event_id not in wanted:
                        continue
                    if event_id in found:
                        raise ValueError("duplicate Console event id")
                    found[event_id] = event
                    if len(found) == len(wanted):
                        break
        return found

    # ── 私有 LLM 上下文 ──
    def save_buddy_state(self, sid: str, state: Dict[str, Any]) -> None:
        """原子保存完整 Buddy/OpenAI 消息链。

        该 sidecar 刻意不走 ``events.jsonl``，因此 reasoning_content、完整工具
        结果和 tool_call_id 不会被 ``/console/stream`` 发往浏览器。调用方只应在
        一轮协议完整收尾后写入，避免保存悬空 tool_calls。
        """
        with self._lock:
            if self.get_meta(sid) is None:
                raise KeyError(f"unknown session {sid}")
            if not isinstance(state, dict):
                raise TypeError("buddy state must be a mapping")
            if not _valid_buddy_state(state):
                raise ValueError("buddy state contains an incomplete tool-call protocol chain")
            # ``allow_nan=False`` 既验证可 JSON 化，也阻止协议状态落入非标准值。
            raw = json.dumps(state, ensure_ascii=False, indent=1, allow_nan=False)
            p = self._sdir(sid) / "buddy_state.json"
            tmp = p.with_suffix(".json.tmp")
            tmp.write_text(raw, encoding="utf-8")
            for attempt in range(3):
                try:
                    tmp.replace(p)
                    return
                except PermissionError:
                    if attempt == 2:
                        raise
                    time.sleep(0.05)

    def load_buddy_state(self, sid: str) -> Optional[Dict[str, Any]]:
        """读取并做最小协议校验；缺失、损坏或未来版本均返回 ``None``。"""
        with self._lock:
            try:
                p = self._sdir(sid) / "buddy_state.json"
            except ValueError:
                return None
            if not p.exists():
                return None
            try:
                state = json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                return None
            return state if _valid_buddy_state(state) else None

    # ── 私有 Codex thread 映射 ──
    def save_codex_state(self, sid: str, state: Dict[str, Any]) -> None:
        """Atomically save a token-free Codex thread mapping.

        OAuth refresh/access tokens remain owned by Codex/Windows credentials;
        this sidecar deliberately contains only routing and recovery metadata.
        """
        with self._lock:
            if self.get_meta(sid) is None:
                raise KeyError(f"unknown session {sid}")
            if not isinstance(state, dict):
                raise TypeError("codex state must be a mapping")
            if not _valid_codex_state(state):
                raise ValueError("invalid codex thread state")
            raw = json.dumps(state, ensure_ascii=False, indent=1, allow_nan=False)
            p = self._sdir(sid) / "codex_state.json"
            tmp = p.with_suffix(".json.tmp")
            tmp.write_text(raw, encoding="utf-8")
            for attempt in range(3):
                try:
                    tmp.replace(p)
                    return
                except PermissionError:
                    if attempt == 2:
                        raise
                    time.sleep(0.05)

    def load_codex_state(self, sid: str) -> Optional[Dict[str, Any]]:
        """Return a valid private Codex mapping; corrupt/future files fail closed."""
        with self._lock:
            try:
                p = self._sdir(sid) / "codex_state.json"
            except ValueError:
                return None
            if not p.exists():
                return None
            try:
                state = json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                return None
            return state if _valid_codex_state(state) else None

    # ── 私有数据目录已确认状态 ──
    def save_data_catalog_state(self, sid: str, state: Dict[str, Any]) -> None:
        """原子保存会话已确认的数据目录指纹，不进入 meta/SSE。"""
        with self._lock:
            if self.get_meta(sid) is None:
                raise KeyError(f"unknown session {sid}")
            if not isinstance(state, dict):
                raise TypeError("data catalog state must be a mapping")
            if not _valid_data_catalog_state(state):
                raise ValueError("invalid data catalog state")
            raw = json.dumps(state, ensure_ascii=False, indent=1, allow_nan=False)
            p = self._sdir(sid) / "data_catalog_state.json"
            tmp = p.with_suffix(".json.tmp")
            tmp.write_text(raw, encoding="utf-8")
            for attempt in range(3):
                try:
                    tmp.replace(p)
                    return
                except PermissionError:
                    if attempt == 2:
                        raise
                    time.sleep(0.05)

    def load_data_catalog_state(self, sid: str) -> Optional[Dict[str, Any]]:
        """读取会话目录指纹；缺失、损坏或未来版本均 fail-closed 返回 ``None``。"""
        with self._lock:
            try:
                p = self._sdir(sid) / "data_catalog_state.json"
            except ValueError:
                return None
            if not p.exists():
                return None
            try:
                state = json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                return None
            return state if _valid_data_catalog_state(state) else None

    # ── 不可压缩证据账本 ──
    def load_evidence(self, sid: str, *, public: bool = False) -> List[Dict[str, Any]]:
        """读取会话来源账本；损坏/缺失时 fail-closed 为空。

        ``public=True`` 会去掉观点摘录，只留下浏览器渲染引用所需的元数据。
        """
        with self._lock:
            if self.get_meta(sid) is None:
                return []
            p = self._sdir(sid) / "evidence_ledger.json"
            if not p.exists():
                return []
            try:
                payload = json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                return []
            if not isinstance(payload, dict) or payload.get("schema_version") != "1":
                return []
            rows = payload.get("items")
            if not isinstance(rows, list):
                return []
            clean = normalise_report_evidence_many(rows, limit=200)
            by_id = {str(row.get("evidence_id")): row for row in rows if isinstance(row, dict)}
            for row in clean:
                previous = by_id.get(str(row["evidence_id"])) or {}
                row["first_event_id"] = int(previous.get("first_event_id") or 0)
                row["last_event_id"] = int(previous.get("last_event_id") or 0)
                aliases = previous.get("aliases")
                if isinstance(aliases, list):
                    row["aliases"] = [str(x)[:24] for x in aliases if str(x).strip()][:8]
            return [public_evidence(row) for row in clean] if public else clean

    def upsert_evidence(self, sid: str, items: List[Dict[str, Any]], *,
                        event_id: int = 0) -> List[Dict[str, Any]]:
        """原子合并来源项，不改写 events 或 Buddy 协议历史。"""
        with self._lock:
            if self.get_meta(sid) is None:
                raise KeyError(f"unknown session {sid}")
            incoming = normalise_report_evidence_many(items, limit=24)
            if not incoming:
                return []
            current = self.load_evidence(sid)
            merged = {str(row["evidence_id"]): dict(row) for row in current}
            changed: List[Dict[str, Any]] = []
            eid = max(0, int(event_id))
            for row in incoming:
                key = str(row["evidence_id"])
                old = merged.get(key) or {}
                aliases: List[str] = []
                for alias in [*(old.get("aliases") or []), *(row.get("aliases") or [])]:
                    alias = str(alias).strip()[:24]
                    if alias and alias not in aliases:
                        aliases.append(alias)
                row["aliases"] = aliases[:8]
                row["first_event_id"] = int(old.get("first_event_id") or eid)
                row["last_event_id"] = max(int(old.get("last_event_id") or 0), eid)
                merged[key] = row
                changed.append(row)
            order = [str(row["evidence_id"]) for row in current]
            order.extend(key for key in merged if key not in order)
            rows = [merged[key] for key in order if key in merged][-200:]
            raw = json.dumps(
                {"schema_version": "1", "items": rows},
                ensure_ascii=False, indent=1, allow_nan=False,
            )
            p = self._sdir(sid) / "evidence_ledger.json"
            tmp = p.with_suffix(".json.tmp")
            tmp.write_text(raw, encoding="utf-8")
            for attempt in range(3):
                try:
                    tmp.replace(p)
                    break
                except PermissionError:
                    if attempt == 2:
                        raise
                    time.sleep(0.05)
            return [public_evidence(row) for row in changed]

    # ── 计划 / 状态 ──
    def set_plan(self, sid: str, todos: List[Dict[str, Any]]) -> Dict[str, Any]:
        with self._lock:
            meta = self.get_meta(sid)
            if meta is None:
                raise KeyError(f"unknown session {sid}")
            meta["plan"] = todos
            meta["updated"] = _now()
            self._save_meta(meta)
            return meta

    def set_status(self, sid: str, status: str) -> None:
        """best-effort:cleanup/finally 路径调用,缺会话静默返回不抛
        (与 set_plan 的 raise 形成对比是故意的)。"""
        with self._lock:
            meta = self.get_meta(sid)
            if meta is None:
                return
            meta["status"] = status
            meta["updated"] = _now()
            self._save_meta(meta)

    def merge_meta(self, sid: str, **fields: Any) -> Optional[Dict[str, Any]]:
        """加锁合并写 meta 顶层键(后台任务留档等);缺会话返回 None。"""
        with self._lock:
            meta = self.get_meta(sid)
            if meta is None:
                return None
            meta.update(fields)
            meta["updated"] = _now()
            self._save_meta(meta)
            return meta

    def set_workspace(self, sid: str, workspace_id: str) -> Optional[Dict[str, Any]]:
        """Move one conversation after validating the target workspace."""
        self.workspace_store.require(workspace_id, active_only=True)
        return self.merge_meta(sid, workspace_id=workspace_id)

    def set_archived(self, sid: str, archived: bool) -> Optional[Dict[str, Any]]:
        """Soft-archive one ordinary conversation without touching its history."""
        if not _SID_RE.fullmatch(sid or ""):
            return None
        with self._lock:
            meta = self.get_meta(sid)
            if meta is None:
                return None
            want = bool(archived)
            meta["archived"] = want
            if want:
                meta["archived_at"] = meta.get("archived_at") or _now()
            else:
                meta.pop("archived_at", None)
            meta["updated"] = _now()
            self._save_meta(meta)
            return meta

    def merge_meta_sub(self, sid: str, key: str, sub_id: str, fields: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """锁内对 meta[key][sub_id] 合并(后台任务留档等嵌套字典,避免锁外读改写竞态)。"""
        with self._lock:
            meta = self.get_meta(sid)
            if meta is None:
                return None
            sub = dict(meta.get(key) or {})
            sub[sub_id] = {**(sub.get(sub_id) or {}), **fields}
            meta[key] = sub
            meta["updated"] = _now()
            self._save_meta(meta)
            return meta
