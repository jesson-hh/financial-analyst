"""Governance layer for Weiwo's existing Markdown memory stores.

Markdown remains the source of truth.  This module adds a small sidecar index
for stable ids and pin metadata, but never rewrites a file merely to list it.
All mutations target one exact parsed row and are serialized with the legacy
``ww_memory_write`` lock so old tool writes and the management API can coexist.
"""
from __future__ import annotations

import hashlib
import json
import re
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

from guanlan_v2.console import tools as ct
from guanlan_v2.console.store import ConsoleStore


_LINE_RE = re.compile(
    r"^- \[(?P<date>\d{4}-\d{2}-\d{2})\] "
    r"(?:\((?P<key>[^)]+)\) )?(?P<text>.+)$"
)
_VALID_SCOPES = frozenset({"global", "global_archive", "workspace", "session"})
_COPY_TARGETS = frozenset({"global", "workspace", "session"})
_SCOPE_LABELS = {
    "global": "全局记忆",
    "global_archive": "全局归档",
    "workspace": "当前研究空间",
    "session": "本会话",
}
_INDEX_SCHEMA = 1


class MemoryAdminError(ValueError):
    """Safe error carrying the HTTP status expected by the router."""

    def __init__(self, status: int, reason: str, *, detail: Optional[str] = None):
        super().__init__(reason)
        self.status = int(status)
        self.reason = reason
        self.detail = detail or reason


@dataclass(frozen=True)
class _Context:
    session_id: str = ""
    workspace_id: str = ""
    session: Optional[Mapping[str, Any]] = None
    workspace: Optional[Mapping[str, Any]] = None


@dataclass(frozen=True)
class _Source:
    scope: str
    owner: str
    path: Path

    @property
    def source_key(self) -> str:
        return f"{self.scope}:{self.owner}"


@dataclass
class _Row:
    source: _Source
    raw_index: int
    line: str
    occurrence: int
    day: str
    key: str
    text: str
    memory_id: str = ""
    pinned: bool = False
    subject_code: str = ""
    created_at: str = ""
    updated_at: str = ""

    @property
    def scope(self) -> str:
        return self.source.scope

    @property
    def owner(self) -> str:
        return self.source.owner


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    for attempt in range(3):
        try:
            tmp.replace(path)
            return
        except PermissionError:
            if attempt == 2:
                raise
            time.sleep(0.05)


def _parse_rows(source: _Source) -> List[_Row]:
    try:
        raw_lines = source.path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return []
    except (OSError, UnicodeError) as exc:
        raise MemoryAdminError(500, "记忆文件读取失败", detail=str(exc)) from exc
    occurrences: Dict[str, int] = {}
    rows: List[_Row] = []
    for raw_index, raw in enumerate(raw_lines):
        line = raw.strip()
        match = _LINE_RE.fullmatch(line)
        if not match:
            continue
        occurrence = occurrences.get(line, 0)
        occurrences[line] = occurrence + 1
        rows.append(_Row(
            source=source,
            raw_index=raw_index,
            line=line,
            occurrence=occurrence,
            day=match.group("date"),
            key=(match.group("key") or "").strip(),
            text=match.group("text").strip(),
        ))
    return rows


def _initial_id(row: _Row) -> str:
    """Match phase-one recall ids on first discovery, then persist in sidecar."""
    source = f"{row.scope}:{row.raw_index + 1}"
    raw = f"{row.scope}\0{source}\0{row.key}\0{row.line}".encode("utf-8")
    return "mem_" + hashlib.sha256(raw).hexdigest()[:12]


def _sanitize_text(value: Any) -> str:
    text = re.sub(r"[\r\n]+", " ", str(value or "")).strip()
    if not text:
        raise MemoryAdminError(400, "记忆正文不能为空")
    return text[: int(getattr(ct, "_MEMORY_MAX_LINE", 280))]


def _sanitize_key(value: Any) -> str:
    key = re.sub(r"[\[\]()\r\n]", "", str(value or "")).strip()
    return key[:80]


def _format_line(day: str, key: str, text: str) -> str:
    return f"- [{day}] {f'({key}) ' if key else ''}{text}"


class MemoryAdmin:
    """Context-scoped list/search and exact row mutations."""

    def __init__(self, store: ConsoleStore):
        self.store = store
        self._lock = threading.RLock()

    @property
    def _index_path(self) -> Path:
        return Path(self.store.root) / "memory.admin.json"

    @property
    def _audit_path(self) -> Path:
        return Path(self.store.root) / "memory.admin.audit.jsonl"

    @property
    def _trash_path(self) -> Path:
        return Path(self.store.root) / "memory.admin.trash.jsonl"

    def _retired_ids(self) -> set[str]:
        """IDs in durable trash are tombstones and must never be reused."""
        try:
            lines = self._trash_path.read_text(encoding="utf-8").splitlines()
        except (FileNotFoundError, OSError, UnicodeError):
            return set()
        retired: set[str] = set()
        for line in lines:
            try:
                payload = json.loads(line)
            except (TypeError, json.JSONDecodeError):
                continue
            memory_id = str(payload.get("memory_id") or "") if isinstance(payload, dict) else ""
            if re.fullmatch(r"mem_[0-9a-f]{12,32}", memory_id):
                retired.add(memory_id)
        return retired

    @staticmethod
    def _global_path() -> Path:
        return Path(ct._MEMORY_PATH)

    @classmethod
    def _archive_path(cls) -> Path:
        path = Path(ct._ARCHIVE_PATH)
        global_path = cls._global_path()
        # Test/injected stores commonly redirect only _MEMORY_PATH.  Follow the
        # same co-location rule as phase-one recall in that case.
        if path.parent != global_path.parent:
            path = global_path.parent / "memory.archive.md"
        return path

    def _resolve_context(self, session_id: Any = "", workspace_id: Any = "") -> _Context:
        sid = str(session_id or "").strip()
        requested_workspace = str(workspace_id or "").strip()
        if not sid:
            if requested_workspace:
                raise MemoryAdminError(
                    403,
                    "读取研究空间记忆必须提供其绑定会话",
                    detail="workspace_id 不能脱离 session_id 单独授权",
                )
            return _Context()
        meta = self.store.get_meta(sid)
        if meta is None:
            raise MemoryAdminError(404, "会话不存在")
        derived_workspace = str(meta.get("workspace_id") or "rw_general")
        if requested_workspace and requested_workspace != derived_workspace:
            raise MemoryAdminError(
                403,
                "会话与研究空间不匹配，已拒绝访问",
                detail=(f"session_id={sid} 实际绑定 {derived_workspace}，"
                        f"不能访问 {requested_workspace}"),
            )
        workspace = self.store.workspace_store.get(derived_workspace)
        if workspace is None:
            raise MemoryAdminError(409, "会话绑定的研究空间不存在")
        return _Context(
            session_id=sid,
            workspace_id=derived_workspace,
            session=meta,
            workspace=workspace,
        )

    def _sources(self, context: _Context, *, include_archive: bool) -> List[_Source]:
        sources = [_Source("global", "", self._global_path())]
        if include_archive:
            sources.append(_Source("global_archive", "", self._archive_path()))
        if context.workspace_id:
            sources.append(_Source(
                "workspace", context.workspace_id,
                Path(self.store.root) / "workspace_memory" / context.workspace_id / "memory.md",
            ))
        if context.session_id:
            sources.append(_Source(
                "session", context.session_id,
                Path(self.store.sessions_dir) / context.session_id / "notes.md",
            ))
        return sources

    def _load_index(self) -> Dict[str, Any]:
        for path in (self._index_path, self._index_path.with_suffix(".json.bak")):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (FileNotFoundError, OSError, UnicodeError, json.JSONDecodeError):
                continue
            if isinstance(payload, dict) and isinstance(payload.get("items"), dict):
                return {"schema_version": _INDEX_SCHEMA, "items": dict(payload["items"])}
        # Markdown remains usable if both optional metadata copies are damaged.
        return {"schema_version": _INDEX_SCHEMA, "items": {}}

    def _save_index(self, index: Mapping[str, Any]) -> None:
        try:
            current = self._index_path.read_text(encoding="utf-8")
            payload = json.loads(current)
            if isinstance(payload, dict) and isinstance(payload.get("items"), dict):
                _atomic_write(self._index_path.with_suffix(".json.bak"), current)
        except (FileNotFoundError, OSError, UnicodeError, json.JSONDecodeError):
            pass
        _atomic_write(
            self._index_path,
            json.dumps(index, ensure_ascii=False, indent=1, allow_nan=False),
        )

    @staticmethod
    def _entry_matches_source(entry: Mapping[str, Any], source: _Source) -> bool:
        return (entry.get("scope") == source.scope
                and str(entry.get("owner") or "") == source.owner)

    @staticmethod
    def _subject_map(context: _Context, sources: Sequence[_Source]) -> Dict[str, str]:
        current_code = str((context.workspace or {}).get("code") or "")
        return {
            source.source_key: current_code
            for source in sources if source.scope in {"workspace", "session"}
        }

    def _reconcile(self, sources: Sequence[_Source], *,
                   subject_codes: Optional[Mapping[str, str]] = None
                   ) -> tuple[List[_Row], Dict[str, Any]]:
        """Attach persistent ids without changing any Markdown source."""
        index = self._load_index()
        items: Dict[str, Dict[str, Any]] = {
            str(mid): dict(entry) for mid, entry in index["items"].items()
            if isinstance(mid, str) and mid.startswith("mem_") and isinstance(entry, dict)
        }
        changed = items != index["items"]
        all_rows: List[_Row] = []
        now = _now()
        used_all: set[str] = set()
        scanned_source_keys = {source.source_key for source in sources}
        retired_ids = self._retired_ids()

        for source in sources:
            rows = _parse_rows(source)
            candidates = {
                mid: entry for mid, entry in items.items()
                if self._entry_matches_source(entry, source)
            }
            used: set[str] = set()

            # Exact content+occurrence is stable across process restarts and
            # ordinary appends.  API edits update this identity below.
            exact: Dict[tuple[str, int], List[str]] = {}
            for mid, entry in candidates.items():
                exact.setdefault(
                    (str(entry.get("line") or ""), int(entry.get("occurrence") or 0)), []
                ).append(mid)
            for row in rows:
                choices = exact.get((row.line, row.occurrence), [])
                mid = next((candidate for candidate in choices if candidate not in used), "")
                if mid:
                    row.memory_id = mid
                    used.add(mid)

            # A keyed legacy writer replaces the whole line.  Preserve the
            # stable id (and pin) when the key remains uniquely identifiable.
            for row in rows:
                if row.memory_id or not row.key:
                    continue
                choices = [
                    mid for mid, entry in candidates.items()
                    if mid not in used and str(entry.get("key") or "") == row.key
                ]
                if len(choices) == 1:
                    row.memory_id = choices[0]
                    used.add(choices[0])

            existing_ids = set(items) | retired_ids
            for row in rows:
                # Curator moves an exact live global line into global_archive.
                # Carry its governance identity across that move instead of
                # minting a second id and losing its audit lineage.
                if not row.memory_id and source.scope == "global_archive":
                    migrated = [
                        mid for mid, entry in items.items()
                        if mid not in used_all
                        and entry.get("scope") == "global"
                        and str(entry.get("owner") or "") == ""
                        and str(entry.get("line") or "") == row.line
                        and int(entry.get("occurrence") or 0) == row.occurrence
                    ]
                    if len(migrated) == 1:
                        row.memory_id = migrated[0]
                if not row.memory_id:
                    mid = _initial_id(row)
                    if mid in existing_ids:
                        mid = "mem_" + uuid.uuid4().hex[:16]
                    row.memory_id = mid
                    existing_ids.add(mid)
                old = items.get(row.memory_id) or {}
                content_changed = bool(
                    old and str(old.get("line") or "") != row.line
                )
                row.pinned = bool(old.get("pinned"))
                if "subject_code" in old:
                    row.subject_code = str(old.get("subject_code") or "")
                else:
                    row.subject_code = str(
                        (subject_codes or {}).get(source.source_key) or ""
                    )
                row.created_at = str(old.get("created_at") or now)
                row.updated_at = now if content_changed else str(old.get("updated_at") or now)
                updated = {
                    "scope": row.scope,
                    "owner": row.owner,
                    "line": row.line,
                    "occurrence": row.occurrence,
                    "raw_index": row.raw_index,
                    "key": row.key,
                    "pinned": row.pinned,
                    "subject_code": row.subject_code,
                    "created_at": row.created_at,
                    "updated_at": row.updated_at,
                }
                if items.get(row.memory_id) != updated:
                    items[row.memory_id] = updated
                    changed = True
                used.add(row.memory_id)

            used_all.update(used)
            all_rows.extend(rows)

        # Drop stale metadata only after every scanned source has had a chance
        # to adopt rows moved by the curator.  Entries for other sessions and
        # workspaces remain untouched.
        for mid, entry in list(items.items()):
            source_key = f"{entry.get('scope')}:{str(entry.get('owner') or '')}"
            if source_key in scanned_source_keys and mid not in used_all:
                items.pop(mid, None)
                changed = True

        next_index = {"schema_version": _INDEX_SCHEMA, "items": items}
        if changed:
            self._save_index(next_index)
        return all_rows, next_index

    @staticmethod
    def _visible(row: _Row, context: _Context) -> bool:
        if row.scope in {"global", "global_archive"}:
            return True
        if row.scope == "workspace":
            return bool(context.workspace_id and row.owner == context.workspace_id)
        if row.scope == "session":
            return bool(context.session_id and row.owner == context.session_id)
        return False

    @staticmethod
    def _dto(row: _Row, context: _Context) -> Dict[str, Any]:
        archived = row.scope == "global_archive"
        read_only_context = bool(
            (row.scope == "session" and context.session and context.session.get("archived"))
            or (row.scope == "workspace" and context.workspace and context.workspace.get("archived"))
        )
        editable = not archived and not read_only_context
        scope_ref = row.owner or row.scope
        subject_code = row.subject_code or None
        current_context_code = None
        if row.scope in {"workspace", "session"} and context.workspace:
            current_context_code = str(context.workspace.get("code") or "") or None
        subject_mismatch = bool(subject_code != current_context_code)
        if row.scope not in {"workspace", "session"}:
            subject_mismatch = False
        if subject_mismatch:
            editable = False
        etag_raw = f"{row.memory_id}\0{row.line}\0{int(row.pinned)}".encode("utf-8")
        return {
            "id": row.memory_id,
            "scope": row.scope,
            "scope_ref": scope_ref,
            "scope_label": _SCOPE_LABELS[row.scope],
            "subject_code": subject_code,
            "current_context_code": current_context_code,
            "subject_mismatch": subject_mismatch,
            "text": row.text,
            "key": row.key or None,
            "date": row.day,
            "pinned": bool(row.pinned),
            # resident means auto-curator protected.  A stable key is resident
            # even when the user has not explicitly pinned it in the UI.
            "resident": bool(row.key or row.pinned),
            "archived": archived,
            "read_only": not editable,
            "editable": editable,
            "deletable": editable,
            "can_pin": editable,
            "can_copy": True,
            "workspace_id": row.owner if row.scope == "workspace" else None,
            "session_id": row.owner if row.scope == "session" else None,
            "etag": hashlib.sha256(etag_raw).hexdigest()[:20],
            "created_at": row.created_at or None,
            "updated_at": row.updated_at or None,
            "provenance": "markdown",
        }

    @staticmethod
    def _matches_query(row: _Row, query: str) -> bool:
        q = str(query or "").strip().casefold()
        if not q:
            return True
        haystack = f"{row.key} {row.text} {row.day} {row.scope}".casefold()
        if q in haystack:
            return True
        terms = [term for term in re.split(r"[\s,，;；]+", q) if term]
        return bool(terms) and all(term in haystack for term in terms)

    def list(self, *, session_id: Any = "", workspace_id: Any = "",
             include_archived: Any = False, query: Any = "",
             scopes: Optional[Iterable[str]] = None, limit: Any = 500) -> Dict[str, Any]:
        with self._lock, ct._MEMORY_LOCK:
            context = self._resolve_context(session_id, workspace_id)
            wanted = set(scopes or _VALID_SCOPES)
            invalid = wanted - _VALID_SCOPES
            if invalid:
                raise MemoryAdminError(400, f"记忆范围非法: {', '.join(sorted(invalid))}")
            show_archive = _truthy(include_archived) and "global_archive" in wanted
            sources = self._sources(context, include_archive=show_archive)
            rows, _index = self._reconcile(
                sources, subject_codes=self._subject_map(context, sources),
            )
            visible = [
                row for row in rows
                if row.scope in wanted and self._visible(row, context)
                and self._matches_query(row, str(query or ""))
            ]
            def _day_ordinal(row: _Row) -> int:
                try:
                    return date.fromisoformat(row.day).toordinal()
                except ValueError:
                    return 0

            visible.sort(key=lambda row: (
                not row.pinned,
                -_day_ordinal(row),
                {"workspace": 0, "session": 1, "global": 2,
                 "global_archive": 3}.get(row.scope, 9),
                row.memory_id,
            ))
            try:
                cap = max(1, min(int(limit or 500), 1000))
            except (TypeError, ValueError):
                raise MemoryAdminError(400, "limit 必须是整数") from None
            counts = {scope: 0 for scope in ("global", "global_archive", "workspace", "session")}
            for row in visible:
                counts[row.scope] += 1
            counts["total"] = len(visible)
            counts["pinned"] = sum(1 for row in visible if row.pinned)
            return {
                "ok": True,
                "memories": [self._dto(row, context) for row in visible[:cap]],
                "counts": counts,
                "total": len(visible),
                "truncated": len(visible) > cap,
                "context": {
                    "session_id": context.session_id or None,
                    "workspace_id": context.workspace_id or None,
                },
            }

    def recall_projection(self, *, session_id: Any, workspace_id: Any) -> Dict[str, Any]:
        """Return subject-safe Markdown bodies plus management-stable ids.

        The console turn path uses this projection before phase-one ranking.
        It keeps old Markdown compatibility while ensuring that a session move
        or a workspace code change cannot inject rows bound to the old stock.
        """
        with self._lock, ct._MEMORY_LOCK:
            context = self._resolve_context(session_id, workspace_id)
            sources = self._sources(context, include_archive=True)
            rows, _index = self._reconcile(
                sources, subject_codes=self._subject_map(context, sources),
            )
            current_code = str((context.workspace or {}).get("code") or "")
            bodies = {scope: "" for scope in _VALID_SCOPES}
            ids: Dict[str, str] = {}
            pinned_lines: List[str] = []
            grouped: Dict[str, List[str]] = {scope: [] for scope in _VALID_SCOPES}
            for row in rows:
                if row.scope in {"workspace", "session"} \
                        and row.subject_code != current_code:
                    continue
                grouped[row.scope].append(row.line)
                ids[f"{row.scope}\0{row.line}"] = row.memory_id
                if row.scope == "global" and row.pinned:
                    pinned_lines.append(row.line)
            for scope, lines in grouped.items():
                bodies[scope] = "\n".join(lines)
            return {
                "sources": bodies,
                "stable_ids": ids,
                "pinned_global_lines": pinned_lines,
            }

    def _rows_for_mutation(self, context: _Context) -> tuple[List[_Row], Dict[str, Any]]:
        # Always scan archive here so an archived id yields an explicit
        # read-only error rather than an ambiguous not-found response.
        sources = self._sources(context, include_archive=True)
        return self._reconcile(
            sources, subject_codes=self._subject_map(context, sources),
        )

    def _find(self, rows: Sequence[_Row], memory_id: str, context: _Context) -> _Row:
        if not re.fullmatch(r"mem_[0-9a-f]{12,32}", str(memory_id or "")):
            raise MemoryAdminError(404, "记忆不存在")
        row = next((item for item in rows if item.memory_id == memory_id), None)
        if row is None or not self._visible(row, context):
            raise MemoryAdminError(404, "记忆不存在或不在当前上下文")
        return row

    @staticmethod
    def _assert_mutable(row: _Row, context: _Context) -> None:
        if row.scope == "global_archive":
            raise MemoryAdminError(
                409,
                "全局归档为只读，不能直接编辑、删除或置顶",
                detail="请使用 copy 并明确确认，将该条复制回全局后再管理",
            )
        if row.scope == "session" and context.session and context.session.get("archived"):
            raise MemoryAdminError(409, "已归档会话的记忆仅供查看")
        if row.scope == "workspace" and context.workspace and context.workspace.get("archived"):
            raise MemoryAdminError(409, "已归档研究空间的记忆仅供查看")
        current_code = str((context.workspace or {}).get("code") or "")
        if row.scope in {"workspace", "session"} and row.subject_code != current_code:
            raise MemoryAdminError(
                409,
                "记忆所属标的与当前上下文不一致，已拒绝修改",
                detail=(f"记忆标的={row.subject_code or '通用'}，"
                        f"当前标的={current_code or '通用'}"),
            )

    @staticmethod
    def _rewrite_row(row: _Row, replacement: Optional[str]) -> None:
        path = row.source.path
        try:
            body = path.read_text(encoding="utf-8")
        except FileNotFoundError as exc:
            raise MemoryAdminError(409, "记忆源已变化，请刷新后重试") from exc
        raw_lines = body.splitlines()
        if row.raw_index >= len(raw_lines) or raw_lines[row.raw_index].strip() != row.line:
            raise MemoryAdminError(409, "记忆源已变化，请刷新后重试")
        if replacement is None:
            raw_lines.pop(row.raw_index)
        else:
            raw_lines[row.raw_index] = replacement
        rendered = "\n".join(raw_lines)
        if raw_lines and body.endswith(("\n", "\r")):
            rendered += "\n"
        _atomic_write(path, rendered)

    def _audit(self, action: str, row: _Row, *, before: Optional[Mapping[str, Any]] = None,
               after: Optional[Mapping[str, Any]] = None) -> None:
        payload = {
            "ts": _now(),
            "action": action,
            "memory_id": row.memory_id,
            "scope": row.scope,
            "owner": row.owner or None,
            "before": dict(before or {}),
            "after": dict(after or {}),
        }
        self._audit_path.parent.mkdir(parents=True, exist_ok=True)
        with self._audit_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False, allow_nan=False) + "\n")

    def patch(self, memory_id: str, body: Mapping[str, Any], *,
              session_id: Any = "", workspace_id: Any = "") -> Dict[str, Any]:
        allowed = {"text", "key", "pinned"}
        fields = allowed & set(body or {})
        if not fields:
            raise MemoryAdminError(400, "无可更新字段(text/key/pinned)")
        with self._lock, ct._MEMORY_LOCK:
            context = self._resolve_context(session_id, workspace_id)
            rows, index = self._rows_for_mutation(context)
            row = self._find(rows, memory_id, context)
            self._assert_mutable(row, context)
            expected_etag = str(body.get("etag") or "").strip()
            if expected_etag and expected_etag != self._dto(row, context)["etag"]:
                raise MemoryAdminError(409, "记忆已被其他操作更新，请刷新后重试")
            new_text = _sanitize_text(body.get("text")) if "text" in body else row.text
            new_key = _sanitize_key(body.get("key")) if "key" in body else row.key
            new_pinned = _truthy(body.get("pinned")) if "pinned" in body else row.pinned

            if new_key and (new_key != row.key):
                conflict = next((other for other in rows
                                 if other.source.source_key == row.source.source_key
                                 and other.memory_id != row.memory_id
                                 and other.key == new_key), None)
                if conflict:
                    raise MemoryAdminError(
                        409, "同一记忆范围已存在该主题键",
                        detail=f"冲突记忆 id={conflict.memory_id}",
                    )

            replacement = _format_line(row.day, new_key, new_text)
            before = {"text": row.text, "key": row.key or None, "pinned": row.pinned}
            if replacement != row.line:
                self._rewrite_row(row, replacement)
                row.line = replacement
                row.text = new_text
                row.key = new_key
            row.pinned = new_pinned
            row.updated_at = _now()
            entry = dict(index["items"].get(row.memory_id) or {})
            entry.update({
                "scope": row.scope,
                "owner": row.owner,
                "line": row.line,
                "occurrence": row.occurrence,
                "raw_index": row.raw_index,
                "key": row.key,
                "pinned": row.pinned,
                "subject_code": row.subject_code,
                "updated_at": row.updated_at,
                "created_at": entry.get("created_at") or _now(),
            })
            index["items"][row.memory_id] = entry
            self._save_index(index)
            after = {"text": row.text, "key": row.key or None, "pinned": row.pinned}
            self._audit("patch", row, before=before, after=after)
            return {"ok": True, "memory": self._dto(row, context)}

    def delete(self, memory_id: str, *, session_id: Any = "",
               workspace_id: Any = "") -> Dict[str, Any]:
        with self._lock, ct._MEMORY_LOCK:
            context = self._resolve_context(session_id, workspace_id)
            rows, index = self._rows_for_mutation(context)
            row = self._find(rows, memory_id, context)
            self._assert_mutable(row, context)
            before = self._dto(row, context)
            # Durable local trash precedes source mutation, so an operator can
            # recover the exact Markdown row even if the UI has no restore yet.
            self._trash_path.parent.mkdir(parents=True, exist_ok=True)
            with self._trash_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps({
                    "deleted_at": _now(),
                    "memory_id": row.memory_id,
                    "scope": row.scope,
                    "owner": row.owner or None,
                    "line": row.line,
                }, ensure_ascii=False, allow_nan=False) + "\n")
            self._rewrite_row(row, None)
            index["items"].pop(row.memory_id, None)
            self._save_index(index)
            self._audit("delete", row, before=before)
            return {
                "ok": True,
                "deleted": True,
                "id": row.memory_id,
                "recoverable": True,
            }

    def _target_source(self, target_scope: str, context: _Context,
                       body: Mapping[str, Any]) -> _Source:
        if target_scope == "global":
            return _Source("global", "", self._global_path())
        if target_scope == "workspace":
            if not context.workspace_id:
                raise MemoryAdminError(403, "复制到研究空间需要当前绑定会话")
            requested = str(body.get("workspace_id") or context.workspace_id).strip()
            if requested != context.workspace_id:
                raise MemoryAdminError(
                    403,
                    "禁止跨研究空间复制记忆",
                    detail=(f"当前会话绑定 {context.workspace_id}，不能写入 {requested}"),
                )
            if context.workspace and context.workspace.get("archived"):
                raise MemoryAdminError(409, "已归档研究空间不能写入记忆")
            return _Source(
                "workspace", context.workspace_id,
                Path(self.store.root) / "workspace_memory" / context.workspace_id / "memory.md",
            )
        if target_scope == "session":
            if not context.session_id:
                raise MemoryAdminError(403, "复制到会话需要当前 session_id")
            requested = str(body.get("session_id") or context.session_id).strip()
            if requested != context.session_id:
                raise MemoryAdminError(403, "禁止向其他会话复制记忆")
            if context.session and context.session.get("archived"):
                raise MemoryAdminError(409, "已归档会话不能写入记忆")
            return _Source(
                "session", context.session_id,
                Path(self.store.sessions_dir) / context.session_id / "notes.md",
            )
        raise MemoryAdminError(400, "target_scope 非法(允许 global/workspace/session)")

    def copy(self, memory_id: str, body: Mapping[str, Any], *,
             session_id: Any = "", workspace_id: Any = "") -> Dict[str, Any]:
        target_scope = str(body.get("target_scope") or "").strip()
        if target_scope not in _COPY_TARGETS:
            raise MemoryAdminError(400, "target_scope 非法(允许 global/workspace/session)")
        with self._lock, ct._MEMORY_LOCK:
            context = self._resolve_context(session_id, workspace_id)
            rows, index = self._rows_for_mutation(context)
            source_row = self._find(rows, memory_id, context)
            if source_row.scope == "global_archive":
                if target_scope != "global":
                    raise MemoryAdminError(
                        409,
                        "全局归档记忆只能显式复制回 global",
                        detail="请提交 target_scope=global；复制不会删除归档原件",
                    )
            target = self._target_source(target_scope, context, body)
            if target.source_key == source_row.source.source_key:
                raise MemoryAdminError(409, "源范围与目标范围相同，无需复制")

            target_rows = [row for row in rows if row.source.source_key == target.source_key]
            if source_row.key:
                conflict = next((row for row in target_rows if row.key == source_row.key), None)
            else:
                conflict = next((row for row in target_rows
                                 if not row.key and row.text == source_row.text), None)
            if conflict:
                raise MemoryAdminError(
                    409,
                    "目标范围已有同主题记忆，未执行覆盖",
                    detail=f"冲突记忆 id={conflict.memory_id}",
                )

            copied_line = _format_line(date.today().isoformat(), source_row.key, source_row.text)
            target.path.parent.mkdir(parents=True, exist_ok=True)
            try:
                old_body = target.path.read_text(encoding="utf-8")
            except FileNotFoundError:
                old_body = ""
            except (OSError, UnicodeError) as exc:
                raise MemoryAdminError(500, "目标记忆文件读取失败", detail=str(exc)) from exc
            separator = "" if not old_body or old_body.endswith(("\n", "\r")) else "\n"
            _atomic_write(target.path, old_body + separator + copied_line + "\n")
            # Discover the appended row, assign a fresh stable id, then save
            # the sidecar without changing or deleting the source record.
            copied_rows = _parse_rows(target)
            copied = copied_rows[-1]
            copied_id = _initial_id(copied)
            if copied_id in index["items"] or copied_id in self._retired_ids():
                copied_id = "mem_" + uuid.uuid4().hex[:16]
            copied.memory_id = copied_id
            copied.pinned = False
            copied.subject_code = (
                str((context.workspace or {}).get("code") or "")
                if copied.scope in {"workspace", "session"} else ""
            )
            copied.created_at = _now()
            copied.updated_at = copied.created_at
            index["items"][copied_id] = {
                "scope": copied.scope,
                "owner": copied.owner,
                "line": copied.line,
                "occurrence": copied.occurrence,
                "raw_index": copied.raw_index,
                "key": copied.key,
                "pinned": False,
                "subject_code": copied.subject_code,
                "created_at": copied.created_at,
                "updated_at": copied.updated_at,
                "copied_from": source_row.memory_id,
            }
            self._save_index(index)
            self._audit(
                "copy", source_row,
                before={"source_retained": True},
                after={"copied_id": copied_id, "target_scope": target_scope,
                       "target_owner": target.owner or None},
            )
            return {
                "ok": True,
                "memory": self._dto(copied, context),
                "copied_from": source_row.memory_id,
                "source_retained": True,
            }
