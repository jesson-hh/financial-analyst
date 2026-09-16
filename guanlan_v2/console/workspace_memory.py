"""Safe, review-first import path for condensed workspace conclusions.

The live path is ``ww_memory_write`` called by the existing console agent in the
same turn.  This module is only the one-time migration seam for older sessions:
it never reads conversation events or ``buddy_state.json`` and never calls an
LLM.  A human (or a separately reviewed process) supplies at most four short
conclusions, previews the exact replacement, then commits the same digest.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List

from guanlan_v2.console.workspaces import normalize_security_code


WORKSPACE_MEMORY_KEYS = frozenset({"thesis", "risk", "invalidation", "catalyst"})
MAX_WORKSPACE_MEMORY_TEXT = 280
_SID_RE = re.compile(r"^cs_[0-9a-f]{12}$")
_KEYED_LINE_RE = re.compile(
    r"^- \[\d{4}-\d{2}-\d{2}\] \((thesis|risk|invalidation|catalyst)\) ",
)


class WorkspaceMemoryImportError(ValueError):
    """A safe validation error suitable for the local console API."""


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _one_line(value: Any) -> str:
    # The import path fails rather than silently truncating a human-reviewed
    # conclusion: the preview must be byte-for-byte representative of commit.
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if not text:
        raise WorkspaceMemoryImportError("workspace 记忆正文不能为空")
    if len(text) > MAX_WORKSPACE_MEMORY_TEXT:
        raise WorkspaceMemoryImportError(
            f"workspace 记忆每条最多 {MAX_WORKSPACE_MEMORY_TEXT} 字；请先人工浓缩",
        )
    return text


def _bound_sources(store: Any, workspace_id: str,
                   source_session_ids: Iterable[Any]) -> List[str]:
    raw = source_session_ids
    if not isinstance(raw, (list, tuple)):
        raise WorkspaceMemoryImportError("source_session_ids 必须是会话编号列表")
    out: List[str] = []
    for value in raw:
        sid = str(value or "").strip()
        if not _SID_RE.fullmatch(sid):
            raise WorkspaceMemoryImportError(f"历史会话编号不合法: {sid or '(空)'}")
        if sid in out:
            continue
        meta = store.get_meta(sid)
        if meta is None:
            raise WorkspaceMemoryImportError(f"历史会话不存在: {sid}")
        if str(meta.get("workspace_id") or "") != workspace_id:
            raise WorkspaceMemoryImportError(f"历史会话 {sid} 不属于目标研究空间")
        out.append(sid)
    if not out:
        raise WorkspaceMemoryImportError("一次性历史回填必须列出至少一个同空间来源会话")
    return out


def _normalized_entries(entries: Any) -> List[Dict[str, str]]:
    if not isinstance(entries, list) or not 1 <= len(entries) <= 4:
        raise WorkspaceMemoryImportError("entries 必须包含 1～4 条人工审核后的浓缩结论")
    out: List[Dict[str, str]] = []
    seen: set[str] = set()
    for raw in entries:
        if not isinstance(raw, dict):
            raise WorkspaceMemoryImportError("每条 entry 必须包含 key 与 text")
        key = str(raw.get("key") or "").strip().lower()
        if key not in WORKSPACE_MEMORY_KEYS:
            raise WorkspaceMemoryImportError(
                "entry.key 仅允许 thesis/risk/invalidation/catalyst",
            )
        if key in seen:
            raise WorkspaceMemoryImportError(f"同一次回填不能重复 key: {key}")
        seen.add(key)
        out.append({"key": key, "text": _one_line(raw.get("text"))})
    return out


def _proposed_bytes(current: bytes, entries: List[Dict[str, str]]) -> tuple[bytes, List[str]]:
    text = current.decode("utf-8", errors="strict") if current else ""
    replace_keys = {item["key"] for item in entries}
    kept: List[str] = []
    replaced: List[str] = []
    for line in text.splitlines():
        match = _KEYED_LINE_RE.match(line)
        if match and match.group(1) in replace_keys:
            replaced.append(match.group(1))
            continue
        if line.strip():
            kept.append(line)
    day = date.today().isoformat()
    kept.extend(f"- [{day}] ({item['key']}) {item['text']}" for item in entries)
    rendered = (("\n".join(kept) + "\n") if kept else "").encode("utf-8")
    return rendered, sorted(set(replaced))


def prepare_workspace_memory_import(store: Any, workspace_id: str, code: Any,
                                    entries: Any, source_session_ids: Any) -> Dict[str, Any]:
    """Validate a migration and return the exact commit token without writing."""
    try:
        workspace = store.workspace_store.require(str(workspace_id), active_only=True)
    except Exception as exc:
        raise WorkspaceMemoryImportError("目标研究空间不存在或已归档") from exc
    if str(workspace.get("kind") or "") not in {"stock", "etf"}:
        raise WorkspaceMemoryImportError("历史结论只能回填到股票/ETF 独立空间")
    try:
        expected_code = normalize_security_code(workspace.get("code"))
        supplied_code = normalize_security_code(code)
    except Exception as exc:
        raise WorkspaceMemoryImportError("证券代码格式不合法") from exc
    if not supplied_code or supplied_code != expected_code:
        raise WorkspaceMemoryImportError(
            f"标的不匹配:目标空间是 {expected_code},收到 {supplied_code or '(空)'}",
        )

    normalized = _normalized_entries(entries)
    sources = _bound_sources(store, str(workspace_id), source_session_ids)
    path = Path(store.root) / "workspace_memory" / str(workspace_id) / "memory.md"
    try:
        current = path.read_bytes() if path.exists() else b""
        proposed, replaced = _proposed_bytes(current, normalized)
    except (OSError, UnicodeError) as exc:
        raise WorkspaceMemoryImportError(f"workspace 记忆读取失败: {exc}") from exc

    before_sha = _digest(current)
    after_sha = _digest(proposed)
    token_payload = {
        "workspace_id": str(workspace_id),
        "code": expected_code,
        "source_session_ids": sources,
        "entries": normalized,
        "before_sha256": before_sha,
        "after_sha256": after_sha,
    }
    token = _digest(json.dumps(
        token_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8"))
    return {
        "ok": True,
        "dry_run": True,
        "workspace_id": str(workspace_id),
        "code": expected_code,
        "source_session_ids": sources,
        "entries": normalized,
        "replaces": replaced,
        "before_sha256": before_sha,
        "after_sha256": after_sha,
        "commit_token": token,
        "path": path,
        "_proposed": proposed,
    }


def apply_workspace_memory_import(store: Any, workspace_id: str, code: Any,
                                  entries: Any, source_session_ids: Any,
                                  commit_token: str) -> Dict[str, Any]:
    """Atomically commit an unchanged preview and append a body-free audit row."""
    # Share the exact writer lock with ww_memory_write so a stale preview cannot
    # interleave with a normal conclusion save between verification and replace.
    from guanlan_v2.console import tools as console_tools

    with console_tools._MEMORY_LOCK:
        preview = prepare_workspace_memory_import(
            store, workspace_id, code, entries, source_session_ids,
        )
        if not commit_token or str(commit_token) != preview["commit_token"]:
            raise WorkspaceMemoryImportError("回填预览已变化或缺少 commit_token；请重新预览后确认")
        path = preview["path"]
        tmp = path.with_suffix(".md.import.tmp")
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_bytes(preview["_proposed"])
            for attempt in range(3):
                try:
                    tmp.replace(path)
                    break
                except PermissionError:
                    if attempt == 2:
                        raise
                    time.sleep(0.05)
        except OSError as exc:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
            raise WorkspaceMemoryImportError(f"workspace 记忆原子写入失败: {exc}") from exc

        audit = {
            "ts": datetime.now().isoformat(timespec="seconds"),
            "workspace_id": preview["workspace_id"],
            "code": preview["code"],
            "keys": [item["key"] for item in preview["entries"]],
            "source_session_ids": preview["source_session_ids"],
            "before_sha256": preview["before_sha256"],
            "after_sha256": preview["after_sha256"],
            "commit_token": preview["commit_token"],
        }
        # Deliberately omit entry bodies from the audit trail.  The condensed
        # memory file is the one body-bearing source; there is no hidden copy of
        # the conversation or report.
        audit_written = True
        try:
            with (path.parent / "imports.jsonl").open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(audit, ensure_ascii=False) + "\n")
        except OSError:
            # The memory replace has already committed.  Never return a generic
            # failure that could mislead an operator into retrying a successful
            # import; expose the degraded audit status explicitly instead.
            audit_written = False

    return {
        "ok": True,
        "applied": True,
        "workspace_id": preview["workspace_id"],
        "code": preview["code"],
        "keys": audit["keys"],
        "source_session_ids": preview["source_session_ids"],
        "after_sha256": preview["after_sha256"],
        "audit": "imports.jsonl" if audit_written else None,
        "audit_written": audit_written,
    }


def public_import_preview(preview: Dict[str, Any]) -> Dict[str, Any]:
    """Strip local filesystem/private implementation fields from an API preview."""
    return {key: value for key, value in preview.items()
            if key not in {"path", "_proposed"}}
