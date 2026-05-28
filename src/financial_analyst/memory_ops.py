"""Memory proposal lifecycle operations — shared by CLI, MCP, future UI.

All write paths to ``memories/<agent>/`` flow through this module:

- :func:`accept_proposal` — promote ``_proposed/<agent>/<slug>.md`` → ``<agent>/<slug>.md``
- :func:`reject_proposal` — delete ``_proposed/<agent>/<slug>.md``
- :func:`revert_proposal` — demote ``<agent>/<slug>.md`` back to ``_proposed/``
- :func:`list_audit` — read recent entries from ``~/.financial-analyst/audit.jsonl``

Every action appends one JSON line to the audit log with a ``source``
field (cli / mcp / buddy / etc.), so the full lifecycle is observable
regardless of which surface initiated the action.

Files are moved (atomic rename) and best-effort git-staged. Git failure
is logged in the audit entry but does not block the accept/reject/revert.
Audit write failure rolls back the file move (atomic rollback via reverse
rename), so an audit-less mutation is impossible.

See ``docs/superpowers/specs/2026-05-27-mcp-accept-proposal-design.md``.
"""
from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from financial_analyst.memory_paths import default_memory_root


AUDIT_PATH = Path.home() / ".financial-analyst" / "audit.jsonl"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _ensure_audit_dir() -> None:
    AUDIT_PATH.parent.mkdir(parents=True, exist_ok=True)


def _next_audit_id() -> str:
    """Monotonic ``a-<N>`` derived from current line count in audit.jsonl."""
    if not AUDIT_PATH.exists():
        return "a-0001"
    try:
        with AUDIT_PATH.open("r", encoding="utf-8") as fh:
            n = sum(1 for _ in fh)
    except Exception:
        n = 0
    return f"a-{n + 1:04d}"


def _now_iso() -> str:
    """Local-timezone ISO 8601 with seconds precision."""
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _write_audit(entry: dict) -> None:
    """Append a single JSON line. Raises on I/O error (caller handles)."""
    _ensure_audit_dir()
    with AUDIT_PATH.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _try_git_stage(project_root: Path, file_path: Path) -> tuple[bool, Optional[str]]:
    """Run ``git -C project_root add file_path``. Returns ``(ok, error_msg)``."""
    try:
        proc = subprocess.run(
            ["git", "-C", str(project_root), "add", str(file_path)],
            capture_output=True, text=True, timeout=10,
        )
        if proc.returncode != 0:
            err = (proc.stderr or "").strip()[:200] or f"exit {proc.returncode}"
            return (False, err)
        return (True, None)
    except FileNotFoundError:
        return (False, "git executable not found")
    except subprocess.TimeoutExpired:
        return (False, "git add timeout")
    except Exception as exc:
        return (False, f"{type(exc).__name__}: {exc}")


def _parse_target(target: str) -> tuple[str, str]:
    """Split ``<agent>/<slug>`` or raise ValueError with a clear message."""
    if "/" not in target:
        raise ValueError(f"target must be <agent>/<slug>, got {target!r}")
    agent, slug = target.split("/", 1)
    if not agent or not slug:
        raise ValueError(f"target must be <agent>/<slug>, got {target!r}")
    return agent, slug


def _find_proposal(proposed_dir: Path, slug: str) -> Optional[Path]:
    """Match exact ``<slug>.md`` or ``<date>_<slug>.md``. Returns None on no/multi match."""
    if not proposed_dir.exists():
        return None
    candidates = list(proposed_dir.glob(f"*{slug}*.md"))
    matches = [c for c in candidates if c.stem == slug or c.stem.endswith(f"_{slug}")]
    if len(matches) != 1:
        return None
    return matches[0]


def _list_candidates(proposed_dir: Path, slug: str) -> list[Path]:
    """For error messages — return ambiguous match list."""
    if not proposed_dir.exists():
        return []
    return list(proposed_dir.glob(f"*{slug}*.md"))


def _find_last_accept_id(target: str) -> Optional[str]:
    """Walk audit.jsonl and return the most recent accept id for this target."""
    if not AUDIT_PATH.exists():
        return None
    last_id: Optional[str] = None
    try:
        for line in AUDIT_PATH.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if entry.get("action") == "accept" and entry.get("target") == target:
                last_id = entry.get("id")
    except Exception:
        return None
    return last_id


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def accept_proposal(
    target: str,
    *,
    source: str,
    dry_run: bool = False,
    project_root: Optional[Path] = None,
) -> dict:
    """Promote ``memories/_proposed/<agent>/<slug>.md`` to ``memories/<agent>/<slug>.md``.

    Args:
        target: ``"<agent>/<slug>"`` identifier.
        source: which surface initiated this — ``"cli"``, ``"mcp"``, ``"buddy"``, etc.
        dry_run: if True, do not touch files or audit; return ``{would_move, dry_run: True}``.
        project_root: project containing ``memories/``. Defaults to cwd.

    Returns:
        On success: ``{id, action, target, src, dst, git_staged, [git_error]}``.
        On error: ``{"error": "..."}``.
    """
    project_root = project_root or default_memory_root().parent
    try:
        agent, slug = _parse_target(target)
    except ValueError as exc:
        return {"error": str(exc)}

    proposed_dir = project_root / "memories" / "_proposed" / agent
    src = _find_proposal(proposed_dir, slug)
    if src is None:
        cands = _list_candidates(proposed_dir, slug)
        if not cands:
            return {"error": f"no proposal matching {target!r} in {proposed_dir}"}
        return {"error": f"ambiguous match in {proposed_dir}: {[c.name for c in cands]}"}

    dst_dir = project_root / "memories" / agent
    dst = dst_dir / f"{slug}.md"

    if dst.exists():
        return {"error": f"refusing to overwrite existing {dst}; move or rename manually"}

    if dry_run:
        return {
            "would_move": {
                "src": str(src.relative_to(project_root)),
                "dst": str(dst.relative_to(project_root)),
            },
            "dry_run": True,
        }

    # Perform the move (atomic rename).
    dst_dir.mkdir(parents=True, exist_ok=True)
    src.rename(dst)

    # Best-effort git stage.
    git_ok, git_err = _try_git_stage(project_root, dst)

    # Build + write audit. On failure, roll back the move.
    audit_id = _next_audit_id()
    entry: dict[str, Any] = {
        "id": audit_id,
        "ts": _now_iso(),
        "action": "accept",
        "source": source,
        "target": target,
        "src": str(src.relative_to(project_root)),
        "dst": str(dst.relative_to(project_root)),
        "project_root": str(project_root),
        "git_staged": git_ok,
    }
    if git_err:
        entry["git_error"] = git_err

    try:
        _write_audit(entry)
    except Exception as exc:
        # Roll back so we never have a silent mutation.
        try:
            dst.rename(src)
        except Exception:
            pass
        return {"error": f"audit write failed: {exc}; rolled back move"}

    return {
        "id": audit_id,
        "action": "accept",
        "target": target,
        "src": entry["src"],
        "dst": entry["dst"],
        "git_staged": git_ok,
        "git_error": git_err,
    }


def reject_proposal(
    target: str,
    *,
    source: str,
    project_root: Optional[Path] = None,
) -> dict:
    """Delete ``memories/_proposed/<agent>/<slug>.md`` without promoting.

    Returns ``{id, action, target, src}`` or ``{"error": "..."}``.
    """
    project_root = project_root or default_memory_root().parent
    try:
        agent, slug = _parse_target(target)
    except ValueError as exc:
        return {"error": str(exc)}

    proposed_dir = project_root / "memories" / "_proposed" / agent
    src = _find_proposal(proposed_dir, slug)
    if src is None:
        cands = _list_candidates(proposed_dir, slug)
        if not cands:
            return {"error": f"no proposal matching {target!r} in {proposed_dir}"}
        return {"error": f"ambiguous match in {proposed_dir}: {[c.name for c in cands]}"}

    # Read src content first so we can roll back if audit write fails.
    backup_content = src.read_text(encoding="utf-8")
    src.unlink()

    audit_id = _next_audit_id()
    entry: dict[str, Any] = {
        "id": audit_id,
        "ts": _now_iso(),
        "action": "reject",
        "source": source,
        "target": target,
        "src": str(src.relative_to(project_root)),
        "project_root": str(project_root),
    }

    try:
        _write_audit(entry)
    except Exception as exc:
        # Roll back the delete.
        try:
            src.write_text(backup_content, encoding="utf-8")
        except Exception:
            pass
        return {"error": f"audit write failed: {exc}; rolled back delete"}

    return {
        "id": audit_id,
        "action": "reject",
        "target": target,
        "src": entry["src"],
    }


def revert_proposal(
    target: str,
    *,
    source: str,
    project_root: Optional[Path] = None,
) -> dict:
    """Undo an accept — move ``memories/<agent>/<slug>.md`` back to ``_proposed/``.

    Returns ``{id, action, target, src, dst, reverted_id, git_staged}`` or error.
    """
    project_root = project_root or default_memory_root().parent
    try:
        agent, slug = _parse_target(target)
    except ValueError as exc:
        return {"error": str(exc)}

    src = project_root / "memories" / agent / f"{slug}.md"
    if not src.exists():
        return {"error": f"no accepted file at {src}, nothing to revert"}

    dst_dir = project_root / "memories" / "_proposed" / agent
    dst = dst_dir / f"{slug}.md"

    if dst.exists():
        return {"error": f"refusing to overwrite existing proposal {dst}; manual cleanup needed"}

    reverted_id = _find_last_accept_id(target)

    # Move file back.
    dst_dir.mkdir(parents=True, exist_ok=True)
    src.rename(dst)

    # Git stage the removal of the main-memory file.
    git_ok, git_err = _try_git_stage(project_root, src)

    audit_id = _next_audit_id()
    entry: dict[str, Any] = {
        "id": audit_id,
        "ts": _now_iso(),
        "action": "revert",
        "source": source,
        "target": target,
        "src": str(src.relative_to(project_root)),
        "dst": str(dst.relative_to(project_root)),
        "project_root": str(project_root),
        "git_staged": git_ok,
    }
    if git_err:
        entry["git_error"] = git_err
    if reverted_id:
        entry["reverted_id"] = reverted_id

    try:
        _write_audit(entry)
    except Exception as exc:
        # Roll back the revert.
        try:
            dst.rename(src)
        except Exception:
            pass
        return {"error": f"audit write failed: {exc}; rolled back revert"}

    return {
        "id": audit_id,
        "action": "revert",
        "target": target,
        "src": entry["src"],
        "dst": entry["dst"],
        "reverted_id": reverted_id,
        "git_staged": git_ok,
    }


def list_audit(limit: int = 20) -> list[dict]:
    """Return the last ``limit`` audit entries from ``~/.financial-analyst/audit.jsonl``.

    Newest first. Returns empty list if the file does not exist or cannot be read.
    """
    if not AUDIT_PATH.exists():
        return []
    entries: list[dict] = []
    try:
        for line in AUDIT_PATH.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    except Exception:
        return []
    return list(reversed(entries[-limit:]))
