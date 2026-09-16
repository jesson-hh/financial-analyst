from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from scripts.migrate_runtime_gates_from_secrets import (
    LEGACY_RUNTIME_GATE_KEYS,
    filter_legacy_runtime_gates,
    migrate,
)


def test_filter_removes_only_exact_runtime_gate_assignments() -> None:
    secret = "credential-value-must-survive"
    raw = (
        f"KIMI_API_KEY={secret}\r\n"
        "GUANLAN_REGEN_DAILY=1\r\n"
        "# GUANLAN_MCP_WRITE=1\r\n"
        "GUANLAN_REGEN_DAILY_SUFFIX=1\r\n"
        " GUANLAN_MCP_WRITE = 0\r\n"
    ).encode()

    filtered, removed = filter_legacy_runtime_gates(raw)

    assert removed == ["GUANLAN_REGEN_DAILY", "GUANLAN_MCP_WRITE"]
    assert filtered == (
        f"KIMI_API_KEY={secret}\r\n"
        "# GUANLAN_MCP_WRITE=1\r\n"
        "GUANLAN_REGEN_DAILY_SUFFIX=1\r\n"
    ).encode()
    assert len(LEGACY_RUNTIME_GATE_KEYS) == 11


def test_migration_dry_run_is_value_free_and_does_not_write(tmp_path: Path) -> None:
    target = tmp_path / "secrets.env"
    original = b"OPENAI_API_KEY=top-secret\nGUANLAN_EVENTS_INGEST=1\n"
    target.write_bytes(original)

    result = migrate(target, apply=False, backup_dir=tmp_path / "backup")

    assert result == {
        "schema_version": "runtime-gate-secrets-migration/v1",
        "status": "pending",
        "applied": False,
        "removed_count": 1,
        "removed_keys": ["GUANLAN_EVENTS_INGEST"],
        "backup_created": False,
        "acl_tightened": False,
    }
    assert target.read_bytes() == original
    assert "top-secret" not in str(result)


def test_migration_applies_atomically_and_keeps_exact_backup(tmp_path: Path) -> None:
    target = tmp_path / "secrets.env"
    original = (
        b"KIMI_API_KEY=real-secret\n"
        b"GUANLAN_REGEN_DAILY=1\n"
        b"GL_PIT_HMAC_VERIFY_KEYS={}\n"
    )
    target.write_bytes(original)
    backup_dir = tmp_path / "backup"

    result = migrate(
        target,
        apply=True,
        backup_dir=backup_dir,
        now=datetime(2026, 9, 4, 12, 30, tzinfo=timezone.utc),
        acl_setter=lambda _paths: True,
    )

    assert result["status"] == "migrated"
    assert result["applied"] is True
    assert result["backup_created"] is True
    assert result["acl_tightened"] is True
    assert target.read_bytes() == (
        b"KIMI_API_KEY=real-secret\n"
        b"GL_PIT_HMAC_VERIFY_KEYS={}\n"
    )
    assert (backup_dir / result["backup_name"]).read_bytes() == original
    assert "real-secret" not in str(result)


def test_acl_failure_blocks_replacement_and_removes_unsecured_backup(tmp_path: Path) -> None:
    target = tmp_path / "secrets.env"
    original = b"OPENAI_API_KEY=keep\nGUANLAN_REGEN_DAILY=1\n"
    target.write_bytes(original)
    backup_dir = tmp_path / "backup"

    try:
        migrate(
            target,
            apply=True,
            backup_dir=backup_dir,
            now=datetime(2026, 9, 4, 12, 31, tzinfo=timezone.utc),
            acl_setter=lambda _paths: False,
        )
    except PermissionError:
        pass
    else:
        raise AssertionError("ACL failure must block migration")

    assert target.read_bytes() == original
    assert list(backup_dir.glob("*.bak")) == []


def test_duplicate_legacy_gate_blocks_apply(tmp_path: Path) -> None:
    target = tmp_path / "secrets.env"
    original = b"GUANLAN_REGEN_DAILY=1\nGUANLAN_REGEN_DAILY=0\n"
    target.write_bytes(original)

    result = migrate(target, apply=True, backup_dir=tmp_path / "backup")

    assert result["status"] == "blocked"
    assert result["blockers"] == ["duplicate_legacy_runtime_gate"]
    assert result["duplicate_keys"] == ["GUANLAN_REGEN_DAILY"]
    assert target.read_bytes() == original
    assert not (tmp_path / "backup").exists()


def test_symlink_target_is_never_migrated(tmp_path: Path) -> None:
    real = tmp_path / "real.env"
    real.write_bytes(b"GUANLAN_REGEN_DAILY=1\n")
    target = tmp_path / "secrets.env"
    try:
        target.symlink_to(real)
    except OSError:
        return

    result = migrate(target, apply=True, backup_dir=tmp_path / "backup")

    assert result["status"] == "blocked"
    assert result["blockers"] == ["credential_file_symlink"]
    assert real.read_bytes() == b"GUANLAN_REGEN_DAILY=1\n"
    assert not (tmp_path / "backup").exists()
