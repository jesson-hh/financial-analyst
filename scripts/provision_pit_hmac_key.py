"""Provision a new strict-PIT HMAC trust anchor without exposing key material.

This is a one-time deployment operation, not an automatic startup fallback.
It only provisions an absent active key, writes an explicit empty verification
keyring, preserves the credential file through an atomic replacement, and
tightens Windows ACLs before the new file becomes visible.  Existing PIT keys
are never overwritten by this command; key rotation requires a separate
reviewed operation.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from guanlan_v2.runtime_secrets import load_credential_file  # noqa: E402


DEFAULT_TARGET = ROOT / "var" / "secrets.env"
DEFAULT_AUDIT_DIR = ROOT / "var" / "pit-key-provisioning"
PIT_KEYS = (
    "GL_PIT_HMAC_KEY_ID",
    "GL_PIT_HMAC_KEY",
    "GL_PIT_HMAC_VERIFY_KEYS",
)
_KEY_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")
_ASSIGNMENT_RE = re.compile(rb"^\s*([A-Z][A-Z0-9_]{0,127})\s*=", re.MULTILINE)


def _existing_assignment_keys(raw: bytes) -> set[str]:
    return {match.group(1).decode("ascii") for match in _ASSIGNMENT_RE.finditer(raw)}


def tighten_windows_acl(paths: Iterable[Path]) -> bool:
    """Restrict files to the current user, SYSTEM and Administrators."""

    if os.name != "nt":
        return False
    who = subprocess.run(
        ["whoami"], text=True, capture_output=True, timeout=10, check=False,
    )
    user = who.stdout.strip()
    if who.returncode != 0 or not user:
        return False
    for path in paths:
        # Grant the explicit safe principals before removing inherited ACEs so
        # a mid-operation failure cannot lock the operator out of the file.
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


def provision(
    target: Path,
    *,
    key_id: str,
    apply: bool,
    audit_dir: Path,
    now: datetime | None = None,
    random_bytes: Callable[[int], bytes] = secrets.token_bytes,
    acl_setter: Callable[[Iterable[Path]], bool] = tighten_windows_acl,
) -> dict:
    """Provision an absent key and return a secret-free audit summary."""

    result = {
        "schema_version": "pit-key-provisioning/v1",
        "status": "blocked",
        "applied": False,
        "key_id": key_id if _KEY_ID_RE.fullmatch(str(key_id or "")) else None,
        "verify_key_count": 0,
        "acl_tightened": False,
        "legacy_assets_resigned": False,
    }
    if result["key_id"] is None:
        result["blockers"] = ["pit_key_id_invalid"]
        return result

    target = Path(target)
    if target.is_symlink():
        result["blockers"] = ["pit_credential_file_symlink"]
        return result
    raw = target.read_bytes() if target.is_file() else b""
    existing = _existing_assignment_keys(raw)
    configured = sorted(existing.intersection(PIT_KEYS))
    if configured:
        result["status"] = "already_configured"
        result["configured_keys"] = configured
        return result
    if not apply:
        result["status"] = "pending"
        return result

    generated = random_bytes(32)
    if not isinstance(generated, bytes) or len(generated) != 32:
        result["blockers"] = ["pit_csprng_invalid"]
        return result
    encoded = base64.b64encode(generated).decode("ascii")
    prefix = raw
    if prefix and not prefix.endswith((b"\n", b"\r")):
        prefix += b"\n"
    payload = prefix + (
        "# Strict PIT trust anchor; provisioned locally, never commit.\n"
        f"GL_PIT_HMAC_KEY_ID={key_id}\n"
        f"GL_PIT_HMAC_KEY=base64:{encoded}\n"
        "GL_PIT_HMAC_VERIFY_KEYS={}\n"
    ).encode("utf-8")

    target.parent.mkdir(parents=True, exist_ok=True)
    audit_dir = Path(audit_dir)
    audit_dir.mkdir(parents=True, exist_ok=True)
    stamp_dt = now or datetime.now(timezone.utc)
    stamp = stamp_dt.strftime("%Y%m%dT%H%M%SZ")
    backup = audit_dir / f"secrets.env.before-pit-key.{stamp}.bak"
    if backup.exists():
        raise FileExistsError("PIT key backup already exists")

    fd, temp_name = tempfile.mkstemp(prefix=".secrets.env.pit-key-", dir=str(target.parent))
    temp = Path(temp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())

        # Validate the complete candidate through the production allowlist and
        # key parser before it can replace the active credential file.
        credential_status = load_credential_file(temp, environ={})
        if credential_status.get("state") != "loaded" or credential_status.get("blocked_count") != 0:
            raise RuntimeError("candidate credential file failed closed-schema validation")
        if raw:
            shutil.copy2(target, backup)
        else:
            backup.write_bytes(b"")
        if not acl_setter((backup, temp)):
            raise PermissionError("credential ACL hardening failed")
        os.replace(temp, target)
    finally:
        if temp.exists():
            temp.unlink()

    result.update({
        "status": "provisioned",
        "applied": True,
        "generated_at": stamp_dt.isoformat(timespec="seconds"),
        "backup_name": backup.name,
        "acl_tightened": True,
    })
    audit = audit_dir / f"provision-audit.{stamp}.json"
    audit.write_text(
        json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", type=Path, default=DEFAULT_TARGET)
    parser.add_argument("--audit-dir", type=Path, default=DEFAULT_AUDIT_DIR)
    parser.add_argument("--key-id", required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    try:
        result = provision(
            args.target,
            key_id=args.key_id,
            apply=args.apply,
            audit_dir=args.audit_dir,
        )
    except Exception as exc:  # never serialize secret values or raw lines
        result = {
            "schema_version": "pit-key-provisioning/v1",
            "status": "error",
            "applied": False,
            "error_type": type(exc).__name__,
        }
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result["status"] in {"pending", "provisioned", "already_configured"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
