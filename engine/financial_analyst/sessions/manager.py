"""Session persistence — list / create / switch / delete + append event log."""
from __future__ import annotations
import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import List, Optional
from financial_analyst.sessions.models import SessionEvent, SessionMeta


DEFAULT_SESSION = "default"


def _default_root() -> Path:
    return Path.home() / ".financial-analyst" / "sessions"


class SessionManager:
    """File-backed session store.

    Each session lives under ``<root>/<name>/``:
      - ``meta.json`` — SessionMeta JSON
      - ``log.jsonl`` — newline-delimited SessionEvent
    """

    def __init__(self, root: Optional[Path] = None, active_name: str = DEFAULT_SESSION):
        self.root = Path(root) if root else _default_root()
        self.root.mkdir(parents=True, exist_ok=True)
        self.active_name = active_name
        self._ensure_session(active_name)

    def _session_dir(self, name: str) -> Path:
        return self.root / name

    def _ensure_session(self, name: str) -> SessionMeta:
        d = self._session_dir(name)
        if not d.exists():
            d.mkdir(parents=True, exist_ok=True)
            now = datetime.now().isoformat(timespec="seconds")
            meta = SessionMeta(name=name, created_at=now, last_active_at=now)
            (d / "meta.json").write_text(meta.model_dump_json(indent=2), encoding="utf-8")
            (d / "log.jsonl").write_text("", encoding="utf-8")
            return meta
        meta_path = d / "meta.json"
        if not meta_path.exists():
            # repair
            now = datetime.now().isoformat(timespec="seconds")
            meta = SessionMeta(name=name, created_at=now, last_active_at=now)
            meta_path.write_text(meta.model_dump_json(indent=2), encoding="utf-8")
            return meta
        return SessionMeta.model_validate_json(meta_path.read_text(encoding="utf-8"))

    def list(self) -> List[SessionMeta]:
        results: List[SessionMeta] = []
        for d in sorted(self.root.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
            if not d.is_dir():
                continue
            meta_path = d / "meta.json"
            if not meta_path.exists():
                continue
            try:
                results.append(SessionMeta.model_validate_json(
                    meta_path.read_text(encoding="utf-8")))
            except Exception:
                continue
        return results

    def create(self, name: str, description: str = "") -> SessionMeta:
        d = self._session_dir(name)
        if d.exists():
            raise ValueError(f"Session already exists: {name}")
        d.mkdir(parents=True)
        now = datetime.now().isoformat(timespec="seconds")
        meta = SessionMeta(name=name, created_at=now, last_active_at=now, description=description)
        (d / "meta.json").write_text(meta.model_dump_json(indent=2), encoding="utf-8")
        (d / "log.jsonl").write_text("", encoding="utf-8")
        return meta

    def switch(self, name: str) -> SessionMeta:
        meta = self._ensure_session(name)
        self.active_name = name
        return meta

    def delete(self, name: str) -> None:
        if name == DEFAULT_SESSION:
            raise ValueError("Cannot delete the default session")
        d = self._session_dir(name)
        if not d.exists():
            raise FileNotFoundError(f"Session not found: {name}")
        shutil.rmtree(d)
        if self.active_name == name:
            self.active_name = DEFAULT_SESSION
            self._ensure_session(DEFAULT_SESSION)

    def append(self, event: SessionEvent, name: Optional[str] = None) -> None:
        name = name or self.active_name
        d = self._session_dir(name)
        if not d.exists():
            self._ensure_session(name)
        log_path = d / "log.jsonl"
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(event.model_dump_json() + "\n")
        # Update meta
        meta_path = d / "meta.json"
        try:
            meta = SessionMeta.model_validate_json(meta_path.read_text(encoding="utf-8"))
            meta.n_messages += 1
            meta.last_active_at = datetime.now().isoformat(timespec="seconds")
            meta_path.write_text(meta.model_dump_json(indent=2), encoding="utf-8")
        except Exception:
            pass

    def history(self, name: Optional[str] = None, limit: int = 20) -> List[SessionEvent]:
        name = name or self.active_name
        log_path = self._session_dir(name) / "log.jsonl"
        if not log_path.exists():
            return []
        lines = log_path.read_text(encoding="utf-8").strip().split("\n")
        events: List[SessionEvent] = []
        for line in lines[-limit:]:
            if not line.strip():
                continue
            try:
                events.append(SessionEvent.model_validate_json(line))
            except Exception:
                continue
        return events
