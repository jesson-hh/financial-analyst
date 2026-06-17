"""On-disk conversation store for the desktop UI (觀瀾).

Conversations are saved as one JSON file each under
``~/.financial-analyst/conversations/{id}.json`` so they survive browser
cache clears and can be shared across devices. Pure stdlib, no deps.

Each conversation dict mirrors the frontend session shape:
    {id, title, createdAt, updatedAt, context, messages: [...]}
plus a server-side ``savedAt`` timestamp added on write.

**Soft-delete (trash)**: ``delete()`` does not actually remove — it moves the file
to the ``_trash/`` subdirectory. ``list_trash()`` / ``restore()`` form the pair.
``purge_old_trash()`` cleans files older than N days, default 30. ``permanent_delete()``
hard-deletes immediately (skips trash).
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
        """**Soft delete** — move to ``_trash/`` subdirectory, no real delete.
        Use ``permanent_delete`` or ``purge_old_trash`` for the hard delete.

        Returns True if found + moved, False if not present.
        """
        src = self.dir / f"{_safe_name(cid)}.json"
        if not src.exists():
            return False
        self._ensure_trash()
        # Include a timestamp in the filename to avoid collisions (multiple deletes of the same cid → multiple copies)
        stamp = int(time.time() * 1000)
        dst = self.trash / f"{_safe_name(cid)}__{stamp}.json"
        src.rename(dst)
        return True

    def permanent_delete(self, cid: str) -> bool:
        """Hard delete — if found in live or trash, unlink immediately.
        Called when the UI user clicks 'Delete permanently' in the trash view."""
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
        """All deleted conversations in trash, including deletedAt (parsed from filename timestamp)."""
        if not self.trash.exists():
            return []
        out: List[Dict[str, Any]] = []
        for p in self.trash.glob("*.json"):
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                # Filename: <cid>__<ms_timestamp>.json — extract deletedAt
                stem = p.stem
                if "__" in stem:
                    _, _, ts = stem.rpartition("__")
                    try:
                        data["deletedAt"] = int(ts)
                    except ValueError:
                        pass
                data["_trash_filename"] = p.name   # for restore (disambiguates duplicate names)
                out.append(data)
            except Exception:
                continue
        out.sort(key=lambda c: c.get("deletedAt", 0), reverse=True)
        return out

    def restore(self, cid: str, trash_filename: Optional[str] = None) -> bool:
        """Restore from trash — the latest copy returns to the live directory.

        Args:
            cid: conversation id
            trash_filename: optional, specifies which copy to restore (from list_trash's ``_trash_filename``).
                            None = automatically pick the latest (max deletedAt).
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
            # Pick the latest (filename trailing timestamp is largest)
            src = max(candidates, key=lambda p: p.stem)

        self._ensure()
        dst = self.dir / f"{_safe_name(cid)}.json"
        if dst.exists():
            # The live dir already has this cid (e.g. user soft-deleted then created a new one) — add "_restored" suffix to avoid overwrite
            stamp = int(time.time() * 1000)
            dst = self.dir / f"{_safe_name(cid)}_restored_{stamp}.json"
        src.rename(dst)
        return True

    def purge_old_trash(self, ttl_days: int = _TRASH_TTL_DAYS) -> int:
        """Hard-delete files in trash older than ttl_days. Returns number deleted. Called periodically or at list_trash time."""
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
