"""Remove reviewed legacy runtime gates from ``var/secrets.env`` safely.

The credential fallback is deliberately not a general dotenv file.  Older
deployments may still contain the non-secret runtime profile that now lives in
``scripts/check_9999.ps1``.  This migration removes *only* the exact reviewed
keys, keeps every other byte-level line intact, creates a timestamped backup,
and never prints credential values.

Dry-run is the default.  Pass ``--apply`` for the atomic replacement.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TARGET = REPO_ROOT / "var" / "secrets.env"
DEFAULT_BACKUP_DIR = REPO_ROOT / "var" / "runtime-gate-migration"

# Keep this closed set aligned with the reviewed watchdog profile.  Never use
# prefix/suffix matching here: an unreviewed key must remain visible to the
# credential loader instead of being silently deleted by a cleanup command.
LEGACY_RUNTIME_GATE_KEYS = frozenset({
    "CONSOLE_REVIEW_MODE",
    "GUANLAN_DL_DAILY",
    "GUANLAN_EVENTS_INGEST",
    "GUANLAN_MCP_WRITE",
    "GUANLAN_ORCH_LAUNCHER",
    "GUANLAN_REGEN_DAILY",
    "GUANLAN_RERANK_DAILY",
    "GUANLAN_REVIEW_DAILY",
    "GUANLAN_SEATS_DEEP",
    "GUANLAN_SEATS_WATCH",
    "GUANLAN_SNAPSHOT_ARCHIVE",
})

_ASSIGNMENT_RE = re.compile(r"^(?P<indent>\s*)(?P<key>[A-Z][A-Z0-9_]{0,127})\s*=")


def _split_preserving_lines(raw: bytes) -> list[bytes]:
    return raw.splitlines(keepends=True)


def _assignment_key(line: bytes) -> str | None:
    try:
        text = line.decode("utf-8")
    except UnicodeDecodeError:
        return None
    match = _ASSIGNMENT_RE.match(text)
    return match.group("key") if match else None


def filter_legacy_runtime_gates(raw: bytes) -> tuple[bytes, list[str]]:
    """Return filtered bytes and removed key names without exposing values."""

    kept: list[bytes] = []
    removed: list[str] = []
    for line in _split_preserving_lines(raw):
        key = _assignment_key(line)
        if key in LEGACY_RUNTIME_GATE_KEYS:
            removed.append(key)
        else:
            kept.append(line)
    return b"".join(kept), removed


def _duplicate_names(names: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for name in names:
        if name in seen:
            duplicates.add(name)
        seen.add(name)
    return sorted(duplicates)


def tighten_windows_acl(paths: Iterable[Path]) -> bool:
    """Restrict sensitive files to the operator, SYSTEM and Administrators.

    The migration backup contains the original credential file, so it must be
    protected to the same standard as the active file before the atomic
    replacement is allowed to proceed.  No path contents are inspected or
    projected by this helper.
    """

    if os.name != "nt":
        return False
    who = subprocess.run(
        ["whoami"], text=True, capture_output=True, timeout=10, check=False,
    )
    user = who.stdout.strip()
    if who.returncode != 0 or not user:
        return False
    for path in paths:
        grant = subprocess.run(
            [
                "icacls", str(path), "/grant:r",
                f"{user}:(F)", "*S-1-5-18:(F)", "*S-1-5-32-544:(F)",
            ],
            text=True, capture_output=True, timeout=20, check=False,
        )
        if grant.returncode != 0:
            return False
        inheritance = subprocess.run(
            ["icacls", str(path), "/inheritance:r"],
            text=True, capture_output=True, timeout=20, check=False,
        )
        if inheritance.returncode != 0:
            return False
    return True


def migrate(
    target: Path,
    *,
    apply: bool,
    backup_dir: Path,
    now: datetime | None = None,
    acl_setter: Callable[[Iterable[Path]], bool] = tighten_windows_acl,
) -> dict:
    """Inspect or atomically migrate one credential fallback file.

    A duplicate legacy gate is treated as an ambiguous source file and blocks
    mutation.  The dry-run result remains useful and value-free.
    """

    target = Path(target)
    result = {
        "schema_version": "runtime-gate-secrets-migration/v1",
        "status": "missing",
        "applied": False,
        "removed_count": 0,
        "removed_keys": [],
        "backup_created": False,
        "acl_tightened": False,
    }
    if target.is_symlink():
        result.update({
            "status": "blocked",
            "blockers": ["credential_file_symlink"],
        })
        return result
    if not target.is_file():
        return result

    raw = target.read_bytes()
    filtered, removed = filter_legacy_runtime_gates(raw)
    duplicate_keys = _duplicate_names(removed)
    result.update({
        "status": "blocked" if duplicate_keys else ("clean" if not removed else "pending"),
        "removed_count": len(removed),
        "removed_keys": sorted(set(removed)),
    })
    if duplicate_keys:
        result["blockers"] = ["duplicate_legacy_runtime_gate"]
        result["duplicate_keys"] = duplicate_keys
        return result
    if not apply or not removed:
        return result

    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = (now or datetime.now(timezone.utc)).strftime("%Y%m%dT%H%M%SZ")
    backup = backup_dir / f"secrets.env.before-runtime-gate-migration.{stamp}.bak"
    if backup.exists():
        raise FileExistsError("migration backup already exists")
    shutil.copy2(target, backup)

    fd, temp_name = tempfile.mkstemp(prefix=".secrets.env.migrate-", dir=str(target.parent))
    temp = Path(temp_name)
    secured = False
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(filtered)
            handle.flush()
            os.fsync(handle.fileno())
        if not acl_setter((backup, temp)):
            raise PermissionError("credential ACL hardening failed")
        secured = True
        os.replace(temp, target)
    finally:
        if temp.exists():
            temp.unlink()
        # An unsecured copy of the original credential file must never be
        # retained after a failed migration attempt.
        if not secured and backup.exists():
            backup.unlink()

    result.update({
        "status": "migrated",
        "applied": True,
        "backup_created": True,
        "backup_name": backup.name,
        "acl_tightened": True,
    })
    return result


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Safely remove reviewed legacy runtime gates from a credential file.",
    )
    parser.add_argument("--target", type=Path, default=DEFAULT_TARGET)
    parser.add_argument("--backup-dir", type=Path, default=DEFAULT_BACKUP_DIR)
    parser.add_argument("--apply", action="store_true")
    return parser


def main() -> int:
    args = _parser().parse_args()
    try:
        result = migrate(args.target, apply=args.apply, backup_dir=args.backup_dir)
    except Exception as exc:  # value-free failure projection
        result = {
            "schema_version": "runtime-gate-secrets-migration/v1",
            "status": "error",
            "applied": False,
            "error_type": type(exc).__name__,
        }
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result["status"] in {"clean", "pending", "migrated"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
