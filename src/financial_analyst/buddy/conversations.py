"""On-disk conversation store for the desktop UI (觀瀾).

Conversations are saved as one JSON file each under
``~/.financial-analyst/conversations/{id}.json`` so they survive browser
cache clears and can be shared across devices. Pure stdlib, no deps.

Each conversation dict mirrors the frontend session shape:
    {id, title, createdAt, updatedAt, context, messages: [...]}
plus a server-side ``savedAt`` timestamp added on write.

**Soft-delete (回收站)**: ``delete()`` 不真删, 把文件 move 到 ``_trash/`` 子目录.
``list_trash()`` / ``restore()`` 配套. ``purge_old_trash()`` 清理 N 天前的, 默认
30 天. ``permanent_delete()`` 立刻硬删 (跳过回收站).
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

_SAFE = re.compile(r"[^A-Za-z0-9_.-]")
_TRASH_TTL_DAYS = 30


def _safe_name(cid: str) -> str:
    """Sanitize a conversation id into a safe filename stem."""
    stem = _SAFE.sub("_", str(cid))[:120]
    return stem or "untitled"


class ConversationStore:
    def __init__(self, path: Optional[Path] = None):
        self.dir = path or (Path.home() / ".financial-analyst" / "conversations")
        self.trash = self.dir / "_trash"

    def _ensure(self) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)

    def _ensure_trash(self) -> None:
        self.trash.mkdir(parents=True, exist_ok=True)

    # ──────────────────────────── live ────────────────────────────

    def save(self, conv: Dict[str, Any]) -> Optional[str]:
        cid = conv.get("id")
        if not cid:
            return None
        self._ensure()
        conv = dict(conv)
        conv["savedAt"] = int(time.time() * 1000)
        path = self.dir / f"{_safe_name(cid)}.json"
        # atomic-ish write: tmp then replace
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(conv, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)
        return cid

    def load(self, cid: str) -> Optional[Dict[str, Any]]:
        path = self.dir / f"{_safe_name(cid)}.json"
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None

    def list(self) -> List[Dict[str, Any]]:
        if not self.dir.exists():
            return []
        out: List[Dict[str, Any]] = []
        for p in self.dir.glob("*.json"):
            try:
                out.append(json.loads(p.read_text(encoding="utf-8")))
            except Exception:
                continue
        out.sort(key=lambda c: c.get("updatedAt", 0), reverse=True)
        return out

    # ──────────────────────────── trash ────────────────────────────

    def delete(self, cid: str) -> bool:
        """**软删** — 移动到 ``_trash/`` 子目录, 不真删. 走 ``permanent_delete``
        或 ``purge_old_trash`` 才硬删.

        Returns True if found + moved, False if not present.
        """
        src = self.dir / f"{_safe_name(cid)}.json"
        if not src.exists():
            return False
        self._ensure_trash()
        # 带时间戳避免重名 (多次删同 cid → 各有副本)
        stamp = int(time.time() * 1000)
        dst = self.trash / f"{_safe_name(cid)}__{stamp}.json"
        src.rename(dst)
        return True

    def permanent_delete(self, cid: str) -> bool:
        """硬删 — live 或 trash 任一找到立刻 unlink. UI 用户在回收站点
        "永久删除" 时调."""
        deleted = False
        live = self.dir / f"{_safe_name(cid)}.json"
        if live.exists():
            live.unlink()
            deleted = True
        if self.trash.exists():
            prefix = _safe_name(cid) + "__"
            for tp in self.trash.glob(f"{prefix}*.json"):
                tp.unlink()
                deleted = True
        return deleted

    def list_trash(self) -> List[Dict[str, Any]]:
        """回收站全部已删会话, 含 deletedAt (从文件名 timestamp 解)."""
        if not self.trash.exists():
            return []
        out: List[Dict[str, Any]] = []
        for p in self.trash.glob("*.json"):
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                # 文件名: <cid>__<ms_timestamp>.json — 抽 deletedAt
                stem = p.stem
                if "__" in stem:
                    _, _, ts = stem.rpartition("__")
                    try:
                        data["deletedAt"] = int(ts)
                    except ValueError:
                        pass
                data["_trash_filename"] = p.name   # 用于 restore (避免重名歧义)
                out.append(data)
            except Exception:
                continue
        out.sort(key=lambda c: c.get("deletedAt", 0), reverse=True)
        return out

    def restore(self, cid: str, trash_filename: Optional[str] = None) -> bool:
        """从回收站恢复 — 最新的副本回到 live 目录.

        Args:
            cid: 会话 id
            trash_filename: 可选, 指定要恢复哪个副本 (来自 list_trash 的 ``_trash_filename``).
                            None = 自动挑最新 (deletedAt 最大).
        """
        if not self.trash.exists():
            return False
        prefix = _safe_name(cid) + "__"

        if trash_filename:
            src = self.trash / trash_filename
            if not src.exists() or not src.name.startswith(prefix):
                return False
        else:
            candidates = list(self.trash.glob(f"{prefix}*.json"))
            if not candidates:
                return False
            # 挑最新 (filename 末尾 timestamp 最大)
            src = max(candidates, key=lambda p: p.stem)

        self._ensure()
        dst = self.dir / f"{_safe_name(cid)}.json"
        if dst.exists():
            # live 已经存在同 cid (例如用户软删后又建新的) — 加 "_restored" 后缀避免覆盖
            stamp = int(time.time() * 1000)
            dst = self.dir / f"{_safe_name(cid)}_restored_{stamp}.json"
        src.rename(dst)
        return True

    def purge_old_trash(self, ttl_days: int = _TRASH_TTL_DAYS) -> int:
        """硬删 trash 里超过 ttl_days 的文件. 返回删除条数. 定时调或 list_trash 时调."""
        if not self.trash.exists():
            return 0
        cutoff_ms = int(time.time() * 1000) - ttl_days * 86400 * 1000
        n = 0
        for p in self.trash.glob("*.json"):
            stem = p.stem
            if "__" not in stem:
                continue
            try:
                _, _, ts = stem.rpartition("__")
                if int(ts) < cutoff_ms:
                    p.unlink()
                    n += 1
            except (ValueError, OSError):
                continue
        return n
