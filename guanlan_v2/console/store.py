"""ConsoleStore — 帷幄会话的文件级事实库。

每会话一目录:var/console/sessions/<sid>/
  meta.json     {id,title,created,updated,status,plan,next_event_id}
  events.jsonl  一行一事件,只追加,id 单调递增
恢复 = 重放 events.jsonl;线程安全(工具在 to_thread 里写)。
**单实例契约**:一个进程只建一个 ConsoleStore(server 在 router 工厂里建一次,工具经
ContextVar 拿同一实例);锁是实例级,多实例并发写同一目录不受保护。
"""
from __future__ import annotations

import json
import re
import shutil
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

_DEFAULT_ROOT = Path(__file__).resolve().parents[2] / "var" / "console"
_SID_RE = re.compile(r"^cs_[0-9a-f]{12}$")


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


class ConsoleStore:
    def __init__(self, root: Optional[Path] = None):
        self.root = Path(root) if root else _DEFAULT_ROOT
        self.sessions_dir = self.root / "sessions"
        self.sessions_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()   # 可重入:merge_meta_sub 等锁内方法可互调(H4 读路径加锁铺路)

    # ── 会话 ──
    def _sdir(self, sid: str) -> Path:
        return self.sessions_dir / sid

    def create_session(self, title: str = "新对话") -> Dict[str, Any]:
        sid = "cs_" + uuid.uuid4().hex[:12]
        meta = {"id": sid, "title": title, "created": _now(), "updated": _now(),
                "status": "idle", "plan": [], "next_event_id": 1}
        d = self._sdir(sid)
        d.mkdir(parents=True, exist_ok=True)
        (d / "events.jsonl").touch()
        self._save_meta(meta)
        return meta

    def get_meta(self, sid: str) -> Optional[Dict[str, Any]]:
        with self._lock:   # 读持锁:不读 _save_meta replace 瞬间的半状态(Windows 上还会撞句柄)
            p = self._sdir(sid) / "meta.json"
            if not p.exists():
                return None
            try:
                return json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                return None

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
            p = self._sdir(sid) / "events.jsonl"
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
