"""Research-workspace file, coding-agent, and private-asset primitives.

The investment tools remain global and read the platform's shared data.  This
module is a separate capability boundary: every file operation resolves its
target from a server-bound workspace context, never from a path supplied by the
model.  Private factor drafts live in the workspace until an explicit publish
operation copies an immutable version into the existing global factor library.
"""
from __future__ import annotations

import contextvars
import difflib
import fnmatch
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path, PurePath
from typing import Any, Dict, Iterable, List, Optional, Tuple


CTX_WORKSPACE: contextvars.ContextVar = contextvars.ContextVar(
    "weiwo_research_workspace", default=None,
)

READ_TOOLS = frozenset({
    "ww_workspace_tree",
    "ww_workspace_read",
    "ww_workspace_search",
    "ww_workspace_diff",
    "ww_workspace_quant_chart",
    # Publishing a previously-created image is a presentation operation: it
    # copies immutable validated bytes into Console's private media store and
    # never edits project files or crosses into a global research store.  Keep
    # it available in ordinary ``research`` mode as well as ``autonomous``.
    "ww_workspace_publish_image",
    "ww_workspace_assets",
})
MUTATING_TOOLS = frozenset({
    "ww_workspace_apply_patch",
    "ww_workspace_undo",
    "ww_workspace_run",
    "ww_workspace_factor_save",
    "ww_workspace_factor_publish",
})
ALL_WORKSPACE_TOOLS = READ_TOOLS | MUTATING_TOOLS

# Autonomous mode may perform reversible in-root edits and private draft saves
# without stopping.  Publishing into the global library and executing arbitrary
# project tests remain explicit human gates.
AUTONOMOUS_SAFE_TOOLS = frozenset({
    "ww_workspace_apply_patch",
    "ww_workspace_undo",
    "ww_workspace_factor_save",
})
# Running any project/toolchain code is a qualitatively different capability
# from editing a text file.  Autonomous mode may write and undo, but execution
# always crosses the ordinary human confirmation gate.
AUTONOMOUS_SAFE_RUN_TASKS = frozenset()

_MAX_TEXT_BYTES = 512 * 1024
_MAX_EDIT_CHARS = 240_000
_MAX_OUTPUT_CHARS = 16_000
_MAX_PROCESS_OUTPUT_BYTES = 1 * 1024 * 1024
_MAX_PROCESS_RSS_BYTES = 768 * 1024 * 1024
_MAX_PROCESS_COUNT = 24
_MAX_TREE_ENTRIES = 600
_MAX_SEARCH_FILES = 2_000
_MAX_SEARCH_RESULTS = 200
_WORKSPACE_ID_RE = re.compile(r"^rw_(?:general|[0-9a-f]{12})$")
_ASSET_ID_RE = re.compile(r"^wa_[0-9a-f]{16}$")
_CHANGE_ID_RE = re.compile(r"^wc_[0-9a-f]{16}$")

_SKIP_DIRS = frozenset({
    ".git", ".hg", ".svn", ".idea", ".vscode", "node_modules",
    ".venv", "venv", "__pycache__", ".pytest_cache", ".mypy_cache",
    ".ruff_cache", ".tox", ".nox", "dist", "build", "coverage",
})
_SECRET_NAMES = frozenset({
    "credentials", "credentials.json", "secrets.json", "secrets.yaml",
    "secrets.yml", "id_rsa", "id_ed25519", ".npmrc", ".pypirc",
})
_SECRET_SUFFIXES = (".pem", ".key", ".p12", ".pfx", ".jks", ".keystore")
_TEXT_SUFFIXES = frozenset({
    "", ".py", ".pyi", ".js", ".jsx", ".ts", ".tsx", ".json",
    ".jsonl", ".md", ".txt", ".csv", ".tsv", ".yaml", ".yml",
    ".toml", ".ini", ".cfg", ".conf", ".html", ".css", ".scss",
    ".sql", ".sh", ".ps1", ".bat", ".cmd", ".xml", ".svg", ".rst",
    ".c", ".h", ".cpp", ".hpp", ".java", ".go", ".rs", ".r",
    ".gitignore", ".dockerignore",
})
_ASSET_DETAIL_FIELDS = (
    "id", "workspace_id", "scope", "kind", "name", "status", "version",
    "created", "updated", "description", "expr", "applicability",
    "scope_detail", "subject", "published",
)

_LOCKS_GUARD = threading.Lock()
_WRITER_LOCKS: Dict[str, threading.Lock] = {}


class WorkspaceToolError(ValueError):
    """A safe error that may be shown to the user/model."""


class WorkspaceAssetNotFound(WorkspaceToolError):
    """The requested private asset is not owned by the bound workspace."""


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def bind_workspace_context(store: Any, sid: str) -> Dict[str, Any]:
    """Build the trusted value placed in ``CTX_WORKSPACE`` for one turn."""
    meta = store.get_meta(sid)
    if meta is None:
        raise WorkspaceToolError("会话不存在，无法绑定研究空间")
    workspace_id = str(meta.get("workspace_id") or "rw_general")
    workspace = store.workspace_store.require(workspace_id, active_only=True)
    return {"store": store, "sid": sid, "workspace_id": workspace_id,
            "workspace": workspace}


def _bound() -> Tuple[Any, str, Dict[str, Any]]:
    ctx = CTX_WORKSPACE.get(None)
    if not isinstance(ctx, dict):
        raise WorkspaceToolError("无研究空间上下文；该工具只能在帷幄研究空间内使用")
    store = ctx.get("store")
    workspace_id = str(ctx.get("workspace_id") or "")
    if store is None or not _WORKSPACE_ID_RE.fullmatch(workspace_id):
        raise WorkspaceToolError("研究空间上下文无效")
    workspace = store.workspace_store.require(workspace_id, active_only=True)
    return store, workspace_id, workspace


def allowed_tools_for_workspace(base: Iterable[str], workspace: Dict[str, Any]) -> set:
    """Derive one turn's tool set without changing the global registry."""
    allowed = set(base)
    mode = str(workspace.get("agent_mode") or "research")
    if mode == "research":
        allowed.difference_update(MUTATING_TOOLS)
    else:
        allowed.update(MUTATING_TOOLS)
    allowed.update(READ_TOOLS)
    # A stock/ETF workspace is private-by-default.  The old direct global-save
    # tool is hidden here; publishing must go through the explicit bridge that
    # records the originating workspace and immutable version.
    if workspace.get("id") != "rw_general":
        allowed.discard("ww_factorlib_save")
        # The legacy research loop persists drafts/workflows through the
        # platform-global stores.  Keep it available in the general workspace,
        # but never let a stock/ETF workspace silently write outside its scope.
        # The private workspace factor tools are the scoped replacement.
        allowed.discard("ww_research_loop")
        # alpha_forge(save=true) writes the engine's process-global user-factor
        # library.  It cannot express a workspace owner, so hide the whole tool
        # in private spaces instead of relying on the model to keep save=false.
        allowed.discard("alpha_forge")
    return allowed


def may_auto_approve(workspace: Dict[str, Any], tool_name: str,
                     args: Optional[Dict[str, Any]] = None) -> bool:
    if str(workspace.get("agent_mode") or "research") != "autonomous":
        return False
    if tool_name in AUTONOMOUS_SAFE_TOOLS:
        return True
    if tool_name == "ww_workspace_run":
        return str((args or {}).get("task") or "") in AUTONOMOUS_SAFE_RUN_TASKS
    return False


def _is_secret_name(path: PurePath) -> bool:
    name = path.name.lower()
    return (
        name in _SECRET_NAMES
        or name == ".env"
        or name.startswith(".env.")
        or name.endswith(_SECRET_SUFFIXES)
        or any(part.lower() in {".git", ".ssh", ".gnupg"} for part in path.parts)
    )


def _root_for(store: Any, workspace_id: str, *, allow_archived: bool = False) -> Path:
    workspace = store.workspace_store.require(
        workspace_id, active_only=not allow_archived,
    )
    raw_root = Path(str(workspace.get("root_path") or ""))
    # A previously-authorized path may later be replaced by a junction/symlink.
    # Refuse the rebind before resolve() follows it, then revalidate external
    # roots against all current global/workspace boundaries on every use.
    if _is_reparse(raw_root):
        raise WorkspaceToolError("研究空间根目录已被符号链接/联接点替换")
    try:
        root = raw_root.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise WorkspaceToolError("研究空间目录离线或不可访问") from exc
    if not root.is_dir():
        raise WorkspaceToolError("研究空间根路径不是目录")
    if os.path.normcase(str(raw_root.absolute())) != os.path.normcase(str(root)):
        raise WorkspaceToolError("研究空间根目录或其父目录已被链接重定向")
    if workspace.get("root_mode") == "external":
        try:
            store.workspace_store._assert_external_root_safe(
                root, excluding_workspace_id=workspace_id,
            )
        except Exception as exc:
            raise WorkspaceToolError(f"外部研究目录安全边界已变化: {exc}") from exc
    return root


def _inside(root: Path, candidate: Path) -> bool:
    # ``relative_to`` is case-insensitive on Windows Path semantics only in
    # selected Python versions.  normcase makes the boundary explicit.
    r = os.path.normcase(str(root))
    c = os.path.normcase(str(candidate))
    try:
        return os.path.commonpath([r, c]) == r
    except ValueError:
        return False


def _relative(raw: Any, *, allow_root: bool = False) -> Path:
    text = str(raw or "").strip().replace("\\", "/")
    if not text:
        if allow_root:
            return Path(".")
        raise WorkspaceToolError("path 不能为空")
    p = Path(text)
    if p.is_absolute() or p.drive or text.startswith(("//", "\\\\")):
        raise WorkspaceToolError("只能使用研究空间内的相对路径")
    if ":" in text:
        raise WorkspaceToolError("路径不能包含冒号或 Windows 备用数据流")
    if any(part in ("..", "") for part in PurePath(text).parts):
        raise WorkspaceToolError("路径不能包含 .. 或空目录段")
    for part in PurePath(text).parts:
        if part != part.rstrip(" ."):
            raise WorkspaceToolError("路径段不能以空格或句点结尾")
        stem = part.split(".", 1)[0].upper()
        if stem in {"CON", "PRN", "AUX", "NUL"} \
                or re.fullmatch(r"(?:COM|LPT)[1-9]", stem):
            raise WorkspaceToolError("路径包含 Windows 保留设备名")
    if _is_secret_name(PurePath(text)):
        raise WorkspaceToolError("密钥、凭据和 .env 文件不向代码 Agent 暴露")
    return p


def _is_reparse(path: Path) -> bool:
    """Reject symlinks and Windows junction/reparse points at every hop."""
    try:
        if path.is_symlink():
            return True
        is_junction = getattr(path, "is_junction", None)
        if callable(is_junction) and is_junction():
            return True
        attrs = int(getattr(path.stat(follow_symlinks=False), "st_file_attributes", 0))
        return bool(attrs & int(getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)))
    except OSError:
        return False


def _resolve(root: Path, raw: Any, *, must_exist: bool,
             allow_root: bool = False) -> Tuple[Path, str]:
    rel = _relative(raw, allow_root=allow_root)
    candidate = root / rel
    probe = root
    for part in rel.parts:
        probe = probe / part
        if probe.exists() or probe.is_symlink():
            if _is_reparse(probe):
                raise WorkspaceToolError("路径包含符号链接或联接点")
    try:
        resolved = candidate.resolve(strict=must_exist)
    except (OSError, RuntimeError) as exc:
        raise WorkspaceToolError(f"路径不存在或不可访问: {rel.as_posix()}") from exc
    if not _inside(root, resolved):
        raise WorkspaceToolError("路径经符号链接/联接点越出了研究空间")
    if not must_exist:
        try:
            parent = resolved.parent.resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise WorkspaceToolError("目标文件的父目录不存在") from exc
        if not _inside(root, parent):
            raise WorkspaceToolError("目标父目录越出了研究空间")
    rel_out = resolved.relative_to(root).as_posix() if resolved != root else ""
    if rel_out and _is_secret_name(PurePath(rel_out)):
        raise WorkspaceToolError("密钥、凭据和 .env 文件不向代码 Agent 暴露")
    return resolved, rel_out


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _read_utf8(path: Path) -> Tuple[str, str]:
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise WorkspaceToolError(f"文件读取失败: {exc}") from exc
    if len(data) > _MAX_TEXT_BYTES:
        raise WorkspaceToolError(f"文件超过 {_MAX_TEXT_BYTES // 1024}KB 文本上限")
    if b"\x00" in data:
        raise WorkspaceToolError("二进制文件不可作为代码文本读取")
    try:
        return data.decode("utf-8"), _sha(data)
    except UnicodeDecodeError as exc:
        raise WorkspaceToolError("文件不是 UTF-8 文本") from exc


def _entry(path: Path, root: Path) -> Dict[str, Any]:
    stat = path.stat()
    return {
        "name": path.name,
        "path": path.relative_to(root).as_posix(),
        "type": "dir" if path.is_dir() else "file",
        "size": None if path.is_dir() else stat.st_size,
        "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
    }


def list_workspace_files(store: Any, workspace_id: str, *, path: str = "",
                         depth: int = 1) -> Dict[str, Any]:
    # Console's file viewer is read-only history; mutation tools keep the
    # default active-only root binding below.
    root = _root_for(store, workspace_id, allow_archived=True)
    target, rel = _resolve(root, path, must_exist=True, allow_root=True)
    if not target.is_dir():
        raise WorkspaceToolError("树目录 path 必须指向目录")
    cap_depth = max(1, min(int(depth or 1), 4))
    entries: List[Dict[str, Any]] = []
    truncated = False

    def walk(directory: Path, level: int) -> None:
        nonlocal truncated
        if truncated:
            return
        try:
            children = sorted(directory.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
        except OSError:
            return
        for child in children:
            if len(entries) >= _MAX_TREE_ENTRIES:
                truncated = True
                return
            if child.name in _SKIP_DIRS or _is_secret_name(PurePath(child.name)):
                continue
            if _is_reparse(child):
                continue
            try:
                resolved = child.resolve(strict=True)
            except (OSError, RuntimeError):
                continue
            if not _inside(root, resolved):
                continue
            try:
                entries.append(_entry(resolved, root))
            except OSError:
                continue
            if resolved.is_dir() and level < cap_depth:
                walk(resolved, level + 1)

    walk(target, 1)
    return {"ok": True, "path": rel, "entries": entries, "truncated": truncated}


def read_workspace_file(store: Any, workspace_id: str, path: str) -> Dict[str, Any]:
    """Read one UTF-8 workspace file for the authenticated local Dock."""
    root = _root_for(store, workspace_id, allow_archived=True)
    target, rel = _resolve(root, path, must_exist=True)
    if not target.is_file():
        raise WorkspaceToolError("path 不是文件")
    text, digest = _read_utf8(target)
    return {
        "ok": True, "path": rel, "sha256": digest,
        "content": text, "size": len(text.encode("utf-8")),
    }


def workspace_tree_impl(path: str = "", depth: int = 2) -> Dict[str, Any]:
    try:
        store, workspace_id, workspace = _bound()
        out = list_workspace_files(store, workspace_id, path=path, depth=depth)
        lines = [
            ("📁 " if item["type"] == "dir" else "📄 ") + item["path"]
            for item in out["entries"]
        ]
        suffix = "\n…目录较大，结果已截断" if out["truncated"] else ""
        return {**out, "content": (
            f"{workspace.get('display_name') or workspace.get('name')} · 文件树"
            + (f" / {out['path']}" if out["path"] else "")
            + ("\n" + "\n".join(lines) if lines else "\n(空目录)") + suffix
        )}
    except WorkspaceToolError as exc:
        return {"ok": False, "content": str(exc), "artifact": None}


def workspace_read_impl(path: str, start_line: int = 1,
                        end_line: int = 240) -> Dict[str, Any]:
    try:
        _store, workspace_id, _workspace = _bound()
        root = _root_for(_store, workspace_id)
        target, rel = _resolve(root, path, must_exist=True)
        if not target.is_file():
            raise WorkspaceToolError("path 不是文件")
        text, digest = _read_utf8(target)
        lines = text.splitlines()
        start = max(1, int(start_line or 1))
        end = max(start, min(int(end_line or start + 239), start + 799, len(lines) or 1))
        selected = lines[start - 1:end]
        numbered = "\n".join(f"{i:>5} | {line}" for i, line in enumerate(selected, start))
        return {
            "ok": True, "path": rel, "sha256": digest, "start_line": start,
            "end_line": end, "total_lines": len(lines),
            "content": (f"{rel} · lines {start}-{end}/{len(lines)} · sha256={digest}\n"
                        + (numbered or "(空文件)")),
            "artifact": None,
        }
    except WorkspaceToolError as exc:
        return {"ok": False, "content": str(exc), "artifact": None}


def _iter_text_files(root: Path, base: Path, glob: str) -> Iterable[Path]:
    seen = 0
    for directory, dirnames, filenames in os.walk(base, followlinks=False):
        here = Path(directory)
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS
                       and not _is_secret_name(PurePath(d))
                       and not _is_reparse(here / d)]
        try:
            if not _inside(root, here.resolve(strict=True)):
                dirnames[:] = []
                continue
        except (OSError, RuntimeError):
            dirnames[:] = []
            continue
        for name in filenames:
            if seen >= _MAX_SEARCH_FILES:
                return
            seen += 1
            if _is_secret_name(PurePath(name)) or not fnmatch.fnmatch(name, glob):
                continue
            path = here / name
            if _is_reparse(path):
                continue
            if path.suffix.lower() not in _TEXT_SUFFIXES:
                continue
            try:
                resolved = path.resolve(strict=True)
                if _inside(root, resolved) and resolved.stat().st_size <= _MAX_TEXT_BYTES:
                    yield resolved
            except OSError:
                continue


def workspace_search_impl(query: str, path: str = "", glob: str = "*") -> Dict[str, Any]:
    try:
        store, workspace_id, _workspace = _bound()
        needle = str(query or "")
        if not needle or len(needle) > 200:
            raise WorkspaceToolError("query 必须为 1-200 字符的字面文本")
        pattern = str(glob or "*").strip()
        if any(ch in pattern for ch in ("/", "\\", "..")):
            raise WorkspaceToolError("glob 只能匹配文件名，不能包含路径")
        root = _root_for(store, workspace_id)
        base, rel = _resolve(root, path, must_exist=True, allow_root=True)
        if not base.is_dir():
            raise WorkspaceToolError("搜索 path 必须是目录")
        rows: List[Dict[str, Any]] = []
        lowered = needle.casefold()
        for file in _iter_text_files(root, base, pattern):
            try:
                text, _digest = _read_utf8(file)
            except WorkspaceToolError:
                continue
            for line_no, line in enumerate(text.splitlines(), 1):
                if lowered in line.casefold():
                    rows.append({"path": file.relative_to(root).as_posix(),
                                 "line": line_no, "text": line[:300]})
                    if len(rows) >= _MAX_SEARCH_RESULTS:
                        break
            if len(rows) >= _MAX_SEARCH_RESULTS:
                break
        body = "\n".join(f"{r['path']}:{r['line']}: {r['text']}" for r in rows)
        return {"ok": True, "query": needle, "path": rel, "matches": rows,
                "truncated": len(rows) >= _MAX_SEARCH_RESULTS,
                "content": (f"搜索 {needle!r}: {len(rows)} 处"
                            + ("(已截断)" if len(rows) >= _MAX_SEARCH_RESULTS else "")
                            + ("\n" + body if body else "\n(未找到)")),
                "artifact": None}
    except WorkspaceToolError as exc:
        return {"ok": False, "content": str(exc), "artifact": None}


def _writer_lock(root: Path) -> threading.Lock:
    key = os.path.normcase(str(root))
    with _LOCKS_GUARD:
        return _WRITER_LOCKS.setdefault(key, threading.Lock())


@contextmanager
def _single_writer(root: Path):
    lock = _writer_lock(root)
    if not lock.acquire(blocking=False):
        raise WorkspaceToolError("该研究空间已有一个写任务在运行，请等待它完成")
    try:
        yield
    finally:
        lock.release()


def _state_dir(store: Any, name: str, workspace_id: str) -> Path:
    path = Path(store.root) / name / workspace_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def _atomic_json(path: Path, payload: Dict[str, Any]) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1, allow_nan=False),
                   encoding="utf-8")
    tmp.replace(path)


def _record_activity(store: Any, workspace_id: str, *, kind: str, goal: str,
                     status: str, changed_files: Optional[List[str]] = None,
                     test_summary: str = "", details: Optional[Dict[str, Any]] = None,
                     started: Optional[str] = None) -> Dict[str, Any]:
    run_id = "wr_" + uuid.uuid4().hex[:16]
    rec = {
        "id": run_id, "workspace_id": workspace_id, "kind": kind,
        "goal": str(goal or "")[:300], "status": status,
        "started": started or _now(), "ended": _now(),
        "changed_files": list(changed_files or []),
        "test_summary": str(test_summary or "")[:1000],
        "details": details or {},
    }
    _atomic_json(_state_dir(store, "workspace_activity", workspace_id) / f"{run_id}.json", rec)
    return rec


def list_workspace_runs(store: Any, workspace_id: str, limit: int = 50) -> List[Dict[str, Any]]:
    directory = _state_dir(store, "workspace_activity", workspace_id)
    rows: List[Dict[str, Any]] = []
    for path in directory.glob("wr_*.json"):
        try:
            item = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(item, dict) and item.get("workspace_id") == workspace_id:
                rows.append(item)
        except Exception:
            continue
    rows.sort(key=lambda x: str(x.get("ended") or x.get("started") or ""), reverse=True)
    return rows[:max(1, min(int(limit or 50), 200))]


def _save_change(store: Any, workspace_id: str, *, rel: str,
                 before: Optional[str], before_hash: Optional[str],
                 after_hash: str, reason: str, state: str = "pending") -> Dict[str, Any]:
    change_id = "wc_" + uuid.uuid4().hex[:16]
    item = {
        "id": change_id, "workspace_id": workspace_id, "path": rel,
        "before": before, "before_hash": before_hash, "after_hash": after_hash,
        "reason": str(reason or "")[:300], "created": _now(), "undone": False,
        "state": state,
    }
    _atomic_json(_state_dir(store, "workspace_changes", workspace_id) / f"{change_id}.json", item)
    return item


def _change_path(store: Any, workspace_id: str, change_id: str) -> Path:
    if not _CHANGE_ID_RE.fullmatch(change_id or ""):
        raise WorkspaceToolError("change_id 格式非法")
    return _state_dir(store, "workspace_changes", workspace_id) / f"{change_id}.json"


def _reconcile_change(store: Any, workspace_id: str, root: Path,
                      item: Dict[str, Any]) -> Dict[str, Any]:
    """Recover a write/undo journal from the file's observable hash.

    A process may stop between the write-ahead record, the file replacement and
    journal finalization.  The before/after hashes make every such state
    deterministic; a third hash is never overwritten automatically.
    """
    state = str(item.get("state") or "applied")
    if state not in {"pending", "undoing"}:
        return item
    target, _rel = _resolve(root, item.get("path"), must_exist=False)
    exists = target.exists()
    current_hash: Optional[str] = None
    if exists:
        if not target.is_file():
            raise WorkspaceToolError("变更目标已不是普通文件，无法恢复")
        _current, current_hash = _read_utf8(target)
    before = item.get("before")
    before_hash = item.get("before_hash")
    if before is not None and not before_hash:
        before_hash = _sha(str(before).encode("utf-8"))
    after_hash = item.get("after_hash")

    next_state = state
    if state == "pending":
        if exists and current_hash == after_hash:
            next_state = "applied"
            item.setdefault("applied_at", _now())
        elif (before is None and not exists) \
                or (before is not None and exists and current_hash == before_hash):
            next_state = "aborted"
            item["aborted_at"] = _now()
        else:
            next_state = "conflict"
    else:  # undoing
        if (before is None and not exists) \
                or (before is not None and exists and current_hash == before_hash):
            next_state = "undone"
            item["undone"] = True
            item["undone_at"] = item.get("undo_started_at") or _now()
        elif exists and current_hash == after_hash:
            # The undo mutation did not happen; return to the applied state so
            # the same confirmed undo can safely retry it.
            next_state = "applied"
        else:
            next_state = "conflict"

    item["state"] = next_state
    if next_state != "conflict":
        _atomic_json(_change_path(store, workspace_id, str(item.get("id") or "")), item)
    return item


def workspace_apply_patch_impl(path: str, old_text: str = "", new_text: str = "",
                               expected_sha256: str = "", create: bool = False,
                               replace_all: bool = False, reason: str = "") -> Dict[str, Any]:
    """Apply one optimistic-concurrency text replacement inside the bound root."""
    change: Optional[Dict[str, Any]] = None
    root: Optional[Path] = None
    try:
        store, workspace_id, workspace = _bound()
        if str(workspace.get("agent_mode") or "research") == "research":
            raise WorkspaceToolError("当前空间是只读研究模式；请先切换为协作写或自主模式")
        old = str(old_text if old_text is not None else "")
        new = str(new_text if new_text is not None else "")
        if len(old) > _MAX_EDIT_CHARS or len(new) > _MAX_EDIT_CHARS:
            raise WorkspaceToolError("单次编辑文本过大，请拆成更小的补丁")
        root = _root_for(store, workspace_id)
        with _single_writer(root):
            target, rel = _resolve(root, path, must_exist=not bool(create))
            if create:
                if target.exists():
                    raise WorkspaceToolError("create=true 但目标文件已存在")
                if old:
                    raise WorkspaceToolError("创建文件时 old_text 必须为空")
                before = None
                before_hash = None
                result = new
            else:
                if not target.is_file():
                    raise WorkspaceToolError("编辑目标不是文件")
                before, before_hash = _read_utf8(target)
                expected = str(expected_sha256 or "").strip().lower()
                if not re.fullmatch(r"[0-9a-f]{64}", expected):
                    raise WorkspaceToolError("编辑现有文件必须携带 ww_workspace_read 返回的 sha256")
                if expected != before_hash:
                    raise WorkspaceToolError("文件已变化，sha256 不匹配；请重新读取后再编辑")
                if not old:
                    raise WorkspaceToolError("编辑现有文件时 old_text 不能为空")
                count = before.count(old)
                if count == 0:
                    raise WorkspaceToolError("old_text 在当前文件中不存在")
                if count > 1 and not replace_all:
                    raise WorkspaceToolError(
                        f"old_text 匹配 {count} 处；请扩大上下文使其唯一，或明确 replace_all=true"
                    )
                result = before.replace(old, new, -1 if replace_all else 1)
            encoded = result.encode("utf-8")
            if len(encoded) > _MAX_TEXT_BYTES:
                raise WorkspaceToolError("编辑后的文件超过文本写入上限")
            after_hash = _sha(encoded)
            # Write-ahead journal: once the real file changes, recovery/undo
            # metadata already exists.  Finalization failure never leaves an
            # unjournaled autonomous edit.
            change = _save_change(store, workspace_id, rel=rel, before=before,
                                  before_hash=before_hash, after_hash=after_hash,
                                  reason=reason, state="pending")
            tmp = target.with_name(f".{target.name}.guanlan-{uuid.uuid4().hex[:8]}.tmp")
            try:
                tmp.write_bytes(encoded)
                os.replace(tmp, target)
            finally:
                tmp.unlink(missing_ok=True)
            change["state"] = "applied"
            change["applied_at"] = _now()
            _atomic_json(_change_path(store, workspace_id, change["id"]), change)
            try:
                _record_activity(store, workspace_id, kind="file_edit",
                                 goal=reason or ("创建 " if create else "编辑 ") + rel,
                                 status="done", changed_files=[rel],
                                 details={"change_id": change["id"], "sha256": after_hash})
            except Exception:
                pass  # activity is a projection; the write-ahead journal is truth
        art = {
            "kind": "workspace_change", "page": None, "channel": "workspace",
            "payload": {"workspace_id": workspace_id, "path": rel,
                        "change_id": change["id"], "sha256": after_hash},
        }
        return {"ok": True, "change_id": change["id"], "sha256": after_hash,
                "artifact": art,
                "content": f"已{'创建' if create else '修改'} {rel} · change {change['id']} · sha256={after_hash}"}
    except WorkspaceToolError as exc:
        return {"ok": False, "content": str(exc), "artifact": None}
    except OSError as exc:
        recovered = None
        if change is not None and root is not None:
            try:
                # ``change`` may already have been mutated to state=applied in
                # memory even though that final write failed.  Recovery must
                # start from the durable journal, never that optimistic copy.
                durable = json.loads(_change_path(
                    store, workspace_id, str(change.get("id") or ""),
                ).read_text(encoding="utf-8"))
                recovered = _reconcile_change(store, workspace_id, root, durable)
            except Exception:
                recovered = None
        if recovered and recovered.get("state") == "applied":
            return {
                "ok": True, "change_id": recovered.get("id"),
                "sha256": recovered.get("after_hash"), "artifact": None,
                "content": f"文件已写入并从中断状态恢复: {recovered.get('path')}",
            }
        state = (recovered or change or {}).get("state", "unknown")
        return {
            "ok": False, "change_id": (change or {}).get("id"), "artifact": None,
            "content": f"文件写入未完成: {exc} · journal={state}",
        }


def workspace_undo_impl(change_id: str) -> Dict[str, Any]:
    try:
        store, workspace_id, workspace = _bound()
        if str(workspace.get("agent_mode") or "research") == "research":
            raise WorkspaceToolError("当前空间是只读研究模式")
        root = _root_for(store, workspace_id)
        with _single_writer(root):
            journal_path = _change_path(store, workspace_id, str(change_id or ""))
            if not journal_path.exists():
                raise WorkspaceToolError("找不到该变更记录")
            try:
                item = json.loads(journal_path.read_text(encoding="utf-8"))
            except Exception as exc:
                raise WorkspaceToolError("变更记录损坏") from exc
            if item.get("workspace_id") != workspace_id:
                raise WorkspaceToolError("变更不属于当前研究空间")
            original_state = str(item.get("state") or "applied")
            if original_state in {"pending", "undoing"}:
                item = _reconcile_change(store, workspace_id, root, item)
            if item.get("state") == "aborted":
                raise WorkspaceToolError("该变更未曾应用，无需撤销")
            if item.get("state") == "conflict":
                raise WorkspaceToolError("文件状态与变更日志均不一致，拒绝自动撤销")
            if item.get("undone"):
                if original_state == "undoing":
                    return {"ok": True, "artifact": None,
                            "content": f"已恢复并完成撤销 {change_id}: {item.get('path')}"}
                raise WorkspaceToolError("该变更已经撤销")
            target, rel = _resolve(root, item.get("path"), must_exist=True)
            current, current_hash = _read_utf8(target)
            if current_hash != item.get("after_hash"):
                raise WorkspaceToolError("文件在该变更后又被修改，拒绝覆盖；请人工处理 diff")
            before = item.get("before")
            item["state"] = "undoing"
            item["undo_started_at"] = _now()
            _atomic_json(journal_path, item)
            if before is None:
                # Only files created by this exact journal entry can be removed.
                target.unlink()
                restored_hash = None
            else:
                encoded = str(before).encode("utf-8")
                tmp = target.with_name(f".{target.name}.guanlan-undo.tmp")
                try:
                    tmp.write_bytes(encoded)
                    os.replace(tmp, target)
                finally:
                    tmp.unlink(missing_ok=True)
                restored_hash = _sha(encoded)
            item["undone"] = True
            item["undone_at"] = _now()
            item["state"] = "undone"
            _atomic_json(journal_path, item)
            try:
                _record_activity(store, workspace_id, kind="undo",
                                 goal=f"撤销 {change_id}", status="done",
                                 changed_files=[rel], details={"restored_hash": restored_hash})
            except Exception:
                pass
        return {"ok": True, "artifact": {
                    "kind": "workspace_change", "page": None, "channel": "workspace",
                    "payload": {"workspace_id": workspace_id, "path": rel,
                                "change_id": change_id, "undone": True}},
                "content": f"已撤销 {change_id}: {rel}"
                + ("(删除由该变更新建的文件)" if restored_hash is None else "")}
    except WorkspaceToolError as exc:
        return {"ok": False, "content": str(exc), "artifact": None}
    except OSError as exc:
        return {"ok": False, "content": f"撤销失败: {exc}", "artifact": None}


def workspace_diff_impl(path: str = "") -> Dict[str, Any]:
    """Render only Guanlan's own reversible edit journal.

    Calling ``git diff/status`` is not a pure read: repository-local filters,
    textconv and fsmonitor settings may execute arbitrary programs.  The
    journal already contains the exact preimage for every Agent edit, so it is
    both safer and more precise for the capability promised here.
    """
    try:
        store, workspace_id, _workspace = _bound()
        root = _root_for(store, workspace_id)
        wanted = ""
        if path:
            wanted = _relative(path).as_posix()
        directory = _state_dir(store, "workspace_changes", workspace_id)
        rows: List[Dict[str, Any]] = []
        for journal in directory.glob("wc_*.json"):
            try:
                item = json.loads(journal.read_text(encoding="utf-8"))
            except Exception:
                continue
            if not isinstance(item, dict) or item.get("workspace_id") != workspace_id:
                continue
            if item.get("state") in {"pending", "undoing"}:
                try:
                    item = _reconcile_change(store, workspace_id, root, item)
                except (WorkspaceToolError, OSError):
                    pass
            if wanted and item.get("path") != wanted:
                continue
            if item.get("state") == "aborted":
                continue
            rows.append(item)
        rows.sort(key=lambda x: str(x.get("created") or ""), reverse=True)
        if not rows:
            return {
                "ok": True, "artifact": None,
                "content": "暂无观澜 Agent 记录的空间变更。为安全起见未执行项目 Git 配置。",
            }
        chunks: List[str] = []
        for item in rows[:20]:
            rel = str(item.get("path") or "")
            if item.get("state") == "conflict":
                chunks.append(f"# {item.get('id')} · {rel} · 状态冲突，未自动覆盖")
                continue
            if item.get("undone"):
                chunks.append(f"# {item.get('id')} · {rel} · 已撤销")
                continue
            try:
                target, _ = _resolve(root, rel, must_exist=True)
                current, current_hash = _read_utf8(target)
            except WorkspaceToolError as exc:
                chunks.append(f"# {item.get('id')} · {rel} · 当前不可读: {exc}")
                continue
            if current_hash != item.get("after_hash"):
                chunks.append(
                    f"# {item.get('id')} · {rel} · 此后又有外部修改，跳过旧差异"
                )
                continue
            before = item.get("before")
            before_text = "" if before is None else str(before)
            diff = "".join(difflib.unified_diff(
                before_text.splitlines(keepends=True),
                current.splitlines(keepends=True),
                fromfile=("/dev/null" if before is None else f"a/{rel}"),
                tofile=f"b/{rel}",
            ))
            chunks.append(f"# {item.get('id')} · {item.get('created')}\n{diff or '(无文本差异)'}")
        text = "\n\n".join(chunks)
        return {"ok": True, "content": text[-_MAX_OUTPUT_CHARS:], "artifact": None,
                "journal_only": True, "count": len(rows)}
    except WorkspaceToolError as exc:
        return {"ok": False, "content": str(exc), "artifact": None}


def _safe_target(root: Path, value: Any) -> str:
    text = str(value or "").strip()
    if not text or text.startswith("-") or "\x00" in text:
        raise WorkspaceToolError("运行目标必须是空间内的相对路径，且不能是命令选项")
    base, sep, node = text.partition("::")
    _path, rel = _resolve(root, base, must_exist=True)
    if sep:
        if not re.fullmatch(r"[A-Za-z0-9_\-\[\].:]+", node or ""):
            raise WorkspaceToolError("pytest 节点选择器含非法字符")
        return rel + "::" + node
    return rel


def _subprocess_env() -> Dict[str, str]:
    # Deliberately do not inherit API keys, cloud credentials, model tokens, or
    # user-specific Python hooks.  Proxy variables point at a closed loopback
    # port as a best-effort network brake; direct sockets are why arbitrary test
    # execution remains a human-confirmed operation even in autonomous mode.
    keep = ("PATH", "PATHEXT", "SYSTEMROOT", "WINDIR", "COMSPEC", "TEMP", "TMP")
    env = {key: os.environ[key] for key in keep if os.environ.get(key)}
    env.update({
        "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1", "PYTHONNOUSERSITE": "1",
        "PIP_NO_INDEX": "1", "PIP_DISABLE_PIP_VERSION_CHECK": "1",
        "npm_config_offline": "true", "npm_config_audit": "false",
        "HTTP_PROXY": "http://127.0.0.1:9", "HTTPS_PROXY": "http://127.0.0.1:9",
        "ALL_PROXY": "http://127.0.0.1:9", "NO_PROXY": "127.0.0.1,localhost",
        "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_OPTIONAL_LOCKS": "0",
    })
    return env


def _kill_tree(pid: int) -> None:
    try:
        import psutil
        parent = psutil.Process(pid)
        children = parent.children(recursive=True)
        for child in children:
            child.terminate()
        parent.terminate()
        _gone, alive = psutil.wait_procs(children + [parent], timeout=2)
        for proc in alive:
            proc.kill()
    except Exception:
        pass


def _process_usage(pid: int) -> Tuple[int, int, List[Tuple[int, float]]]:
    """Aggregate RSS/process count; inability to inspect fails closed."""
    try:
        import psutil
        parent = psutil.Process(pid)
        procs = [parent, *parent.children(recursive=True)]
        rss = 0
        for proc in procs:
            try:
                rss += int(proc.memory_info().rss)
            except psutil.NoSuchProcess:
                continue
            except psutil.AccessDenied as exc:
                raise WorkspaceToolError("无法验证代码任务的进程资源边界") from exc
        identities: List[Tuple[int, float]] = []
        for proc in procs[1:]:
            try:
                identities.append((proc.pid, float(proc.create_time())))
            except psutil.NoSuchProcess:
                continue
            except psutil.AccessDenied as exc:
                raise WorkspaceToolError("无法验证代码任务的子进程身份") from exc
        return rss, len(procs), identities
    except ImportError as exc:
        raise WorkspaceToolError("缺少 psutil，代码任务无法安全监控，已拒绝运行") from exc
    except Exception as exc:
        try:
            import psutil
            if isinstance(exc, psutil.NoSuchProcess):
                return 0, 0, []
        except ImportError:
            pass
        if isinstance(exc, WorkspaceToolError):
            raise
        raise WorkspaceToolError("无法验证代码任务的进程资源边界") from exc


def _stop_observed_children(identities: Iterable[Tuple[int, float]]) -> bool:
    """Terminate descendants observed before their launcher exits."""
    import psutil
    alive = []
    for pid, created_at in set(identities):
        try:
            proc = psutil.Process(pid)
            # A PID may be reused after a short-lived child exits.  Never touch
            # a process unless its creation time matches the observed child.
            if abs(float(proc.create_time()) - created_at) > 0.001:
                continue
            if proc.is_running():
                proc.terminate()
                alive.append(proc)
        except psutil.NoSuchProcess:
            continue
        except psutil.AccessDenied:
            return False
    _gone, survivors = psutil.wait_procs(alive, timeout=2)
    for proc in survivors:
        try:
            proc.kill()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    _gone, survivors = psutil.wait_procs(survivors, timeout=2)
    return not survivors


def _stop_process(proc: subprocess.Popen) -> None:
    _kill_tree(proc.pid)
    try:
        proc.wait(timeout=3)
        return
    except subprocess.TimeoutExpired:
        pass
    try:
        proc.kill()
    except OSError:
        pass
    try:
        proc.wait(timeout=2)
    except subprocess.TimeoutExpired:
        # Never block the server waiting on a process that escaped the tree.
        pass


def _run_command(command: List[str], root: Path, timeout: int) -> Tuple[int, str, float]:
    try:
        import psutil  # noqa: F401 — mandatory runtime safety dependency
    except ImportError as exc:
        raise WorkspaceToolError("缺少 psutil，代码任务无法安全监控，已拒绝运行") from exc
    started = time.monotonic()
    flags = 0
    if sys.platform == "win32":
        flags = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
    reason = ""
    forced_code = 0
    observed_children: set[Tuple[int, float]] = set()
    # A file sink avoids PIPE deadlocks and avoids accumulating unbounded child
    # output in the 9999 process.  The monitor terminates the whole tree as soon
    # as output/RSS/process-count/time crosses its hard cap.
    with tempfile.TemporaryFile(mode="w+b") as sink:
        proc = subprocess.Popen(
            command, cwd=str(root), stdout=sink, stderr=subprocess.STDOUT,
            env=_subprocess_env(), creationflags=flags,
        )
        while proc.poll() is None:
            elapsed = time.monotonic() - started
            try:
                output_size = os.fstat(sink.fileno()).st_size
            except OSError:
                output_size = 0
            try:
                rss, process_count, child_pids = _process_usage(proc.pid)
                observed_children.update(child_pids)
            except WorkspaceToolError as exc:
                reason, forced_code = str(exc), 128
                _stop_process(proc)
                break
            if elapsed > timeout:
                reason, forced_code = f"超过 {timeout}s", 124
            elif output_size > _MAX_PROCESS_OUTPUT_BYTES:
                reason, forced_code = "输出超过 1MB", 125
            elif rss > _MAX_PROCESS_RSS_BYTES:
                reason, forced_code = "进程树内存超过 768MB", 126
            elif process_count > _MAX_PROCESS_COUNT:
                reason, forced_code = f"子进程数量超过 {_MAX_PROCESS_COUNT}", 127
            if reason:
                _stop_process(proc)
                break
            time.sleep(0.05)
        if proc.poll() is None:
            _stop_process(proc)
        if observed_children and not _stop_observed_children(observed_children):
            reason = reason or "后台子进程未能完整收口"
            forced_code = forced_code or 128
        try:
            size = os.fstat(sink.fileno()).st_size
            if size > _MAX_PROCESS_OUTPUT_BYTES and not reason:
                reason, forced_code = "输出超过 1MB", 125
            sink.seek(max(0, size - _MAX_PROCESS_OUTPUT_BYTES))
            raw = sink.read(_MAX_PROCESS_OUTPUT_BYTES)
        except OSError:
            raw = b""
    output = raw.decode("utf-8", errors="replace")
    if reason:
        output += f"\n[观澜] {reason}，已终止进程树。"
    return forced_code or int(proc.returncode or 0), output, time.monotonic() - started


def workspace_run_impl(task: str, targets: Optional[List[str]] = None,
                       timeout_seconds: int = 120) -> Dict[str, Any]:
    try:
        store, workspace_id, workspace = _bound()
        if str(workspace.get("agent_mode") or "research") == "research":
            raise WorkspaceToolError("当前空间是只读研究模式")
        task = str(task or "").strip()
        root = _root_for(store, workspace_id)
        safe_targets = [_safe_target(root, value) for value in (targets or [])]
        timeout = max(5, min(int(timeout_seconds or 120), 300))
        py = sys.executable
        if task == "compile_python":
            command = [py, "-I", "-m", "compileall", "-q", *(safe_targets or ["."])]
        elif task == "ruff":
            command = [py, "-I", "-m", "ruff", "check", *(safe_targets or ["."])]
        elif task == "pytest":
            command = [py, "-m", "pytest", "-q", *safe_targets]
        elif task == "unittest":
            if safe_targets:
                raise WorkspaceToolError("unittest 任务不接收 targets；使用项目默认 discover")
            command = [py, "-m", "unittest", "discover"]
        elif task == "node_test":
            node = shutil.which("node")
            if not node:
                raise WorkspaceToolError("本机未安装 node")
            command = [node, "--test", *safe_targets]
        elif task == "npm_test":
            if safe_targets:
                raise WorkspaceToolError("npm_test 不接收 targets")
            npm = shutil.which("npm.cmd") or shutil.which("npm")
            if not npm:
                raise WorkspaceToolError("本机未安装 npm")
            command = [npm, "test", "--", "--runInBand"]
        elif task in {"git_status", "git_diff"}:
            git = shutil.which("git")
            if not git:
                raise WorkspaceToolError("本机未安装 git")
            command = [git, "-c", "core.fsmonitor=false",
                       "-c", "core.untrackedCache=false",
                       "-c", "credential.helper="]
            if task == "git_status":
                command.extend(["status", "--short", "--untracked-files=normal"])
            else:
                command.extend(["diff", "--no-ext-diff", "--no-textconv", "--no-color"])
        else:
            raise WorkspaceToolError(
                "task 仅支持 compile_python/ruff/pytest/unittest/node_test/npm_test/git_status/git_diff"
            )
        with _single_writer(root):
            rc, output, elapsed = _run_command(command, root, timeout)
        clipped = output[-_MAX_OUTPUT_CHARS:]
        note = ("通过" if rc == 0 else f"失败(exit {rc})") + f" · {elapsed:.1f}s"
        rec = _record_activity(
            store, workspace_id, kind="task", goal=f"运行 {task}",
            status="done" if rc == 0 else "error", test_summary=note,
            details={"task": task, "targets": safe_targets, "exit_code": rc,
                     "network_policy": "best-effort-deny; arbitrary tests require confirmation"},
        )
        return {"ok": rc == 0, "run_id": rec["id"], "exit_code": rc,
                "artifact": {"kind": "workspace_run", "page": None,
                             "channel": "workspace", "payload": {
                                 "workspace_id": workspace_id, "run_id": rec["id"],
                                 "task": task, "status": rec["status"]}},
                "content": f"{task}: {note}\n{clipped or '(无输出)'}"}
    except WorkspaceToolError as exc:
        return {"ok": False, "content": str(exc), "artifact": None}
    except (OSError, subprocess.SubprocessError) as exc:
        return {"ok": False, "content": f"任务启动失败: {exc}", "artifact": None}


def _new_quant_output_dir(root: Path) -> Tuple[Path, str]:
    """Create one unique, reparse-checked derived-output directory."""
    base, _ = _resolve(root, "output", must_exist=False)
    try:
        base.mkdir(exist_ok=True)
    except OSError as exc:
        raise WorkspaceToolError(f"无法创建量化输出目录: {exc}") from exc
    base, _ = _resolve(root, "output", must_exist=True)
    if not base.is_dir() or _is_reparse(base):
        raise WorkspaceToolError("量化输出目录不是安全的普通目录")
    rel = f"output/quant_{uuid.uuid4().hex[:16]}"
    target, rel = _resolve(root, rel, must_exist=False)
    try:
        target.mkdir()
    except OSError as exc:
        raise WorkspaceToolError(f"无法创建本次量化输出目录: {exc}") from exc
    target, rel = _resolve(root, rel, must_exist=True)
    if not target.is_dir() or _is_reparse(target):
        raise WorkspaceToolError("本次量化输出目录校验失败")
    return target, rel


def _quant_chart_content(result: Dict[str, Any], json_rel: str) -> str:
    freshness = result.get("freshness") or {}
    position = result.get("position_levels") or {}
    status = "当前数据口径已对齐" if result.get("current_ready") else "仅可作为历史口径"
    lines = [
        f"量化位置图已生成并发送（{status}）。",
        f"位置: {result.get('position_price') if result.get('position_price') is not None else '—'}"
        f" · 截至 {result.get('position_asof') or '—'}",
        f"数据截止: 个股 {result.get('analysis_asof') or '—'} · "
        f"沪深300 {result.get('benchmark_asof') or '—'} · "
        f"共同样本 {result.get('common_cutoff') or '—'}",
    ]
    supports = position.get("supports") or []
    resistances = position.get("resistances") or []
    lines.append("支撑区: " + (" / ".join(f"{float(x['price']):.2f}" for x in supports) or "未形成"))
    lines.append("压力区: " + (" / ".join(f"{float(x['price']):.2f}" for x in resistances) or "未形成"))
    lines.append("相对沪深300（日简单收益，OLS: stock=alpha+beta×benchmark，无风险利率=0）：")
    for row in result.get("regressions") or []:
        window = int(row.get("window") or 0)
        if not row.get("available"):
            lines.append(
                f"- {window}日: 不可用（n={row.get('n')}/{row.get('required_n')}；"
                f"{row.get('reason') or '样本不足'}）"
            )
            continue
        lines.append(
            f"- {window}日 n={row.get('n')}: 相关性 {float(row['corr']):.4f} · "
            f"Alpha(日) {float(row['alpha_daily']) * 100:.4f}% · "
            f"Beta {float(row['beta']):.4f} · R² {float(row['r2']):.4f}"
        )
    warnings = list(freshness.get("warnings") or [])
    if warnings:
        lines.append("口径警示: " + "；".join(str(x) for x in warnings[:3]))
    lines.append(f"可复算 JSON: {json_rel}")
    return "\n".join(lines)


def workspace_quant_chart_impl(benchmark: str = "csi300",
                               asof: str = "") -> Dict[str, Any]:
    """Compute bound-workspace levels/regressions and publish one image card."""
    try:
        store, workspace_id, workspace = _bound()
        code = str(workspace.get("code") or "").strip()
        if str(workspace.get("kind") or "") != "stock" or not code:
            raise WorkspaceToolError("当前量化位置图只支持已绑定证券代码的股票研究空间")
        if benchmark != "csi300":
            raise WorkspaceToolError("当前量化位置图只支持沪深300基准")
        reference_date: Optional[str] = None
        if str(asof or "").strip():
            reference_date = str(asof).strip()
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", reference_date):
                raise WorkspaceToolError("asof 必须是 YYYY-MM-DD")
        root = _root_for(store, workspace_id)
        output_dir, output_rel = _new_quant_output_dir(root)
        from guanlan_v2.console.media import MediaStore, artifact_payload
        from guanlan_v2.console.quant_chart import build_workspace_quant_chart

        result = build_workspace_quant_chart(
            str(workspace.get("name") or workspace.get("display_name") or code)[:80],
            code,
            output_dir=output_dir,
            benchmark=benchmark,
            windows=(20, 60, 120),
            reference_date=reference_date,
        )
        png_name = str((result.get("artifacts") or {}).get("png") or "")
        json_name = str((result.get("artifacts") or {}).get("json") or "")
        if not png_name or not json_name:
            raise WorkspaceToolError("量化核心未返回完整 PNG/JSON 产物")
        png_rel = f"{output_rel}/{png_name}"
        json_rel = f"{output_rel}/{json_name}"
        ctx = CTX_WORKSPACE.get(None) or {}
        metadata = MediaStore(store).publish_workspace_image(
            workspace_id,
            png_rel,
            session_id=str(ctx.get("sid") or ""),
            alt=(
                f"{workspace.get('name') or code} 支撑压力位及相对沪深300 "
                "20/60/120日 Alpha Beta 量化图"
            ),
        )
        payload = artifact_payload(metadata)
        return {
            "ok": True,
            "content": _quant_chart_content(result, json_rel),
            "artifact": {
                "kind": "media_image",
                "page": None,
                "channel": "conversation",
                "payload": payload,
                "ref": metadata["id"],
            },
            "media": payload,
            "result": result,
        }
    except (WorkspaceToolError, ValueError, OSError, RuntimeError, ImportError) as exc:
        return {"ok": False, "content": f"量化图生成失败: {exc}", "artifact": None}


def workspace_publish_image_impl(path: str, alt: str = "") -> Dict[str, Any]:
    """Publish one bound-workspace raster image as an opaque media artifact."""
    try:
        store, workspace_id, _workspace = _bound()
        ctx = CTX_WORKSPACE.get(None) or {}
        from guanlan_v2.console.media import MediaStore, artifact_payload

        metadata = MediaStore(store).publish_workspace_image(
            workspace_id,
            path,
            session_id=str(ctx.get("sid") or ""),
            alt=alt,
        )
        payload = artifact_payload(metadata)
        return {
            "ok": True,
            "content": (
                f"图片已安全发布到当前对话: {metadata['name']} "
                f"({metadata['width']}×{metadata['height']})。"
            ),
            "artifact": {
                "kind": "media_image",
                "page": None,
                "channel": "conversation",
                "payload": payload,
                "ref": metadata["id"],
            },
            "media": payload,
        }
    except (WorkspaceToolError, ValueError, OSError) as exc:
        return {"ok": False, "content": str(exc), "artifact": None}


def _assets_dir(store: Any, workspace_id: str) -> Path:
    return _state_dir(store, "workspace_assets", workspace_id)


def list_workspace_assets(store: Any, workspace_id: str, limit: int = 200) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for path in _assets_dir(store, workspace_id).glob("wa_*.json"):
        try:
            item = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(item, dict) and item.get("workspace_id") == workspace_id:
                rows.append(item)
        except Exception:
            continue
    rows.sort(key=lambda x: str(x.get("updated") or x.get("created") or ""), reverse=True)
    return rows[:max(1, min(int(limit or 200), 500))]


def _asset_path(store: Any, workspace_id: str, asset_id: str) -> Path:
    if not _ASSET_ID_RE.fullmatch(asset_id or ""):
        raise WorkspaceToolError("asset_id 格式非法")
    return _assets_dir(store, workspace_id) / f"{asset_id}.json"


def get_workspace_asset(store: Any, workspace_id: str,
                        asset_id: str) -> Dict[str, Any]:
    """Load one full private-asset record without widening its owner scope.

    ``public_asset_rows`` deliberately redacts factor bodies for the Dock's
    list surface.  This exact-load helper is the only browser-facing path that
    returns the full record (including ``expr``), after checking both the
    requested filename and the owner fields persisted inside that record.
    """
    if not _WORKSPACE_ID_RE.fullmatch(str(workspace_id or "")):
        raise WorkspaceToolError("workspace_id 格式非法")
    if not _ASSET_ID_RE.fullmatch(str(asset_id or "")):
        raise WorkspaceToolError("asset_id 格式非法")
    # Do not call ``_assets_dir`` here: a GET must not create state merely by
    # probing a valid-but-missing id.  Reject redirected state directories as
    # well, otherwise a junction could alias one workspace's assets to another.
    base = Path(store.root) / "workspace_assets"
    directory = base / workspace_id
    path = directory / f"{asset_id}.json"
    for probe in (base, directory, path):
        if (probe.exists() or probe.is_symlink()) and _is_reparse(probe):
            raise WorkspaceToolError("空间资产目录已被链接重定向")
    if not path.exists() or not path.is_file():
        raise WorkspaceAssetNotFound("空间资产不存在")
    try:
        item = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise WorkspaceToolError("空间资产记录损坏") from exc
    if not isinstance(item, dict) \
            or item.get("id") != asset_id \
            or item.get("workspace_id") != workspace_id \
            or item.get("scope") != "workspace":
        # A copied/misfiled record must never become readable merely because
        # its filename was placed below another workspace's state directory.
        raise WorkspaceAssetNotFound("空间资产不存在或不属于当前研究空间")
    # Fixed DTO: future internal bookkeeping fields must not silently become a
    # browser API merely because they were added to the durable record.
    return {key: item.get(key) for key in _ASSET_DETAIL_FIELDS}


def workspace_assets_impl(kind: str = "", limit: int = 50) -> Dict[str, Any]:
    try:
        store, workspace_id, _workspace = _bound()
        rows = list_workspace_assets(store, workspace_id, limit=limit)
        if kind:
            rows = [row for row in rows if row.get("kind") == kind]
        lines = [
            f"{row.get('id')} · {row.get('kind')} · {row.get('name')} · "
            f"{row.get('status')} · v{row.get('version', 1)}"
            for row in rows
        ]
        return {"ok": True, "assets": rows, "artifact": None,
                "content": f"当前空间研究资产 {len(rows)} 项:\n"
                           + ("\n".join(lines) if lines else "(暂无；空间草稿不会自动进入全局库)")}
    except WorkspaceToolError as exc:
        return {"ok": False, "content": str(exc), "artifact": None}


def workspace_factor_save_impl(name: str, expr: str, description: str = "",
                               applicability: str = "subject",
                               scope_detail: str = "") -> Dict[str, Any]:
    try:
        store, workspace_id, workspace = _bound()
        if str(workspace.get("agent_mode") or "research") == "research":
            raise WorkspaceToolError("当前空间是只读研究模式")
        name = str(name or "").strip()[:80]
        expr = str(expr or "").strip()
        if not name or not expr:
            raise WorkspaceToolError("因子 name/expr 必填")
        if len(expr) > 20_000:
            raise WorkspaceToolError("因子表达式过长")
        allowed_scope = {"subject", "single_security", "etf_constituents", "custom_pool"}
        applicability = str(applicability or "subject").strip()
        if applicability not in allowed_scope:
            raise WorkspaceToolError("applicability 非法")
        try:
            from financial_analyst.factors.zoo.expr import validate_expr
            validate_expr(expr)
        except Exception as exc:  # noqa: BLE001
            raise WorkspaceToolError(f"因子表达式校验失败: {type(exc).__name__}: {exc}") from exc
        root = _root_for(store, workspace_id)
        with _single_writer(root):
            existing = [a for a in list_workspace_assets(store, workspace_id, 500)
                        if a.get("kind") == "factor" and a.get("name") == name]
            version = max((int(a.get("version") or 1) for a in existing), default=0) + 1
            asset_id = "wa_" + uuid.uuid4().hex[:16]
            now = _now()
            item = {
                "id": asset_id, "workspace_id": workspace_id, "scope": "workspace",
                "kind": "factor", "name": name, "status": "draft", "version": version,
                "expr": expr, "description": str(description or "")[:1000],
                "applicability": applicability, "scope_detail": str(scope_detail or "")[:500],
                "subject": {"kind": workspace.get("kind"), "code": workspace.get("code"),
                            "name": workspace.get("display_name")},
                "created": now, "updated": now, "published": None,
                "summary": f"{applicability} · {expr[:120]}",
                "open_page": "factor", "ref": asset_id,
            }
            _atomic_json(_asset_path(store, workspace_id, asset_id), item)
            try:
                _record_activity(store, workspace_id, kind="factor_draft",
                                 goal=f"保存空间因子 {name} v{version}", status="done",
                                 details={"asset_id": asset_id})
            except Exception:
                pass
        return {"ok": True, "asset": item,
                "artifact": {"kind": "workspace_factor", "page": "factor",
                             "channel": "workspace", "payload": {
                                 "workspace_id": workspace_id, "asset_id": asset_id,
                                 "name": name, "status": "draft", "version": version}},
                "content": (f"已保存为空间私有因子草稿: {name} v{version} ({asset_id})。"
                            "未注册到全局因子库，也不会进入落子；发布需单独确认。")}
    except WorkspaceToolError as exc:
        return {"ok": False, "content": str(exc), "artifact": None}
    except OSError as exc:
        return {"ok": False, "content": f"空间因子写入失败: {exc}", "artifact": None}


def workspace_factor_publish_impl(asset_id: str, global_name: str = "") -> Dict[str, Any]:
    try:
        store, workspace_id, _workspace = _bound()
        root = _root_for(store, workspace_id)
        with _single_writer(root):
            path = _asset_path(store, workspace_id, str(asset_id or ""))
            if not path.exists():
                raise WorkspaceToolError("空间因子不存在")
            try:
                item = json.loads(path.read_text(encoding="utf-8"))
            except Exception as exc:
                raise WorkspaceToolError("空间因子记录损坏") from exc
            if item.get("kind") != "factor" or item.get("workspace_id") != workspace_id:
                raise WorkspaceToolError("资产不是当前空间的因子")
            if item.get("published"):
                p = item["published"]
                return {"ok": True, "artifact": None,
                        "content": f"该版本已发布为全局因子 {p.get('global_name')}，不重复写入。"}
            requested_name = str(global_name or "").strip()
            if not requested_name:
                code = str((item.get("subject") or {}).get("code") or "workspace")
                requested_name = f"{item.get('name')}__{code}__v{int(item.get('version') or 1)}"
            provenance = {
                "workspace_id": workspace_id,
                "workspace_asset_id": item.get("id"),
                "workspace_version": int(item.get("version") or 1),
                "expr_sha256": _sha(str(item.get("expr") or "").encode("utf-8")),
            }
            provenance_digest = _sha(json.dumps(
                provenance, ensure_ascii=False, sort_keys=True,
            ).encode("utf-8"))
            pending = item.get("publish_pending")
            if pending:
                if not isinstance(pending, dict) \
                        or pending.get("provenance_digest") != provenance_digest:
                    raise WorkspaceToolError("发布恢复记录与当前资产版本不一致，拒绝覆盖")
                # The first confirmed attempt fixes the public name.  Retrying
                # after a crash cannot silently publish a second global copy.
                name = str(pending.get("global_name") or "").strip()
                if not name:
                    raise WorkspaceToolError("发布恢复记录缺少全局名称")
            else:
                name = requested_name
                item["publish_pending"] = {
                    "global_name": name, "provenance_digest": provenance_digest,
                    "started_at": _now(),
                }
                _atomic_json(path, item)
            from guanlan_v2.factorlib.api import SaveIn, save_factor_idempotent
            from guanlan_v2.factorlib.store import LibraryFactorStore
            result = save_factor_idempotent(SaveIn(
                name=name, expr=str(item.get("expr") or ""), family="library_mined",
                description=(str(item.get("description") or "")
                             + f" · 发布自研究空间 {workspace_id}/{item.get('id')}")[:1000],
                source="research_workspace_publish", status="",
                meta={"workspace_id": workspace_id, "workspace_asset_id": item.get("id"),
                      "workspace_version": item.get("version"),
                      "applicability": item.get("applicability"),
                      "subject": item.get("subject")},
            ), LibraryFactorStore(), workspace_id=workspace_id,
                workspace_asset_id=str(item.get("id") or ""),
                workspace_version=int(item.get("version") or 1))
            if not result.get("ok"):
                # The global helper only returns ok=False before publishing a
                # complete destination file.  Release the reserved name so the
                # user can correct a collision/validation failure and retry.
                item.pop("publish_pending", None)
                try:
                    _atomic_json(path, item)
                except OSError:
                    pass
                raise WorkspaceToolError(f"发布到全局因子库失败: {result.get('reason')}")
            name = str(result.get("name") or name)
            item["status"] = "published"
            item["updated"] = _now()
            item["published"] = {"global_name": name, "published_at": _now(),
                                 "registered": bool(result.get("registered")),
                                 "recovered": bool(result.get("recovered")),
                                 "provenance_digest": provenance_digest}
            item.pop("publish_pending", None)
            _atomic_json(path, item)
            try:
                _record_activity(store, workspace_id, kind="factor_publish",
                                 goal=f"发布因子 {item.get('name')} → {name}", status="done",
                                 details={"asset_id": item.get("id"), "global_name": name})
            except Exception:
                pass
        return {"ok": True, "asset": item,
                "artifact": {"kind": "factor_saved", "page": "factor",
                             "channel": "workflow", "payload": {
                                 "workspace_id": workspace_id, "asset_id": item.get("id"),
                                 "name": name, "registered": bool(result.get("registered"))}},
                "content": (f"已显式发布全局不可变版本: {name}。来源仍记录为"
                            f" {workspace_id}/{item.get('id')}；落子未被自动修改。")}
    except WorkspaceToolError as exc:
        return {"ok": False, "content": str(exc), "artifact": None}
    except OSError as exc:
        return {"ok": False, "content": f"发布状态写入失败: {exc}", "artifact": None}


def public_asset_rows(store: Any, workspace_id: str, limit: int = 200) -> List[Dict[str, Any]]:
    """Redacted, workspace-exact projection used by the research Dock.

    Private factors, saved workflow instances and workflow reports have three
    separate durable stores.  The Dock is their common index only; it never
    copies bodies or broadens their visibility rules.
    """
    cap = max(1, min(int(limit or 200), 500))
    rows: List[Dict[str, Any]] = []
    for item in list_workspace_assets(store, workspace_id, limit=cap):
        rows.append({
            key: item.get(key) for key in (
                "id", "kind", "name", "status", "scope", "version", "updated",
                "summary", "open_page", "ref", "applicability", "subject", "published",
            )
        })
    try:
        from guanlan_v2.workflow.store import WorkflowStore
        for item in WorkflowStore().list(workspace_id=workspace_id, include_global=False):
            graph = item.get("graph") or {}
            nodes = graph.get("nodes") or []
            edges = graph.get("edges") or []
            rows.append({
                "id": item.get("id"), "kind": "workflow",
                "name": item.get("name") or "未命名工作流",
                "status": "saved", "scope": "workspace", "version": None,
                "updated": item.get("ts"),
                "summary": f"{len(nodes)} 个节点 · {len(edges)} 条连接",
                "open_page": "factor", "ref": item.get("id"),
            })
    except Exception:
        pass  # a secondary index must not hide the factors that were readable
    try:
        from guanlan_v2.reports.api import list_report_summaries
        for item in list_report_summaries(workspace_id, limit=cap):
            label = item.get("label") or item.get("method") or "工作流报告"
            rows.append({
                "id": item.get("id"), "kind": "report",
                "name": item.get("name") or "未命名报告",
                "status": "saved", "scope": "workspace", "version": None,
                "updated": item.get("ts"),
                "summary": f"{label}" + (f" · {item.get('universe')}" if item.get("universe") else ""),
                "open_page": None, "ref": item.get("id"),
            })
    except Exception:
        pass

    def _sort_key(item: Dict[str, Any]) -> str:
        value = item.get("updated")
        if isinstance(value, (int, float)):
            try:
                return datetime.fromtimestamp(float(value) / 1000.0).isoformat()
            except (OverflowError, OSError, ValueError):
                return ""
        return str(value or "")

    rows.sort(key=_sort_key, reverse=True)
    return rows[:cap]
