from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from scripts.provision_pit_hmac_key import PIT_KEYS, provision


def test_dry_run_does_not_generate_or_write(tmp_path: Path) -> None:
    target = tmp_path / "secrets.env"
    target.write_text("KIMI_API_KEY=keep-me\n", encoding="utf-8")
    called = False

    def rng(_size: int) -> bytes:
        nonlocal called
        called = True
        return b"x" * 32

    result = provision(
        target, key_id="pit-prod-2026-09", apply=False,
        audit_dir=tmp_path / "audit", random_bytes=rng,
    )

    assert result["status"] == "pending" and result["applied"] is False
    assert called is False
    assert target.read_text(encoding="utf-8") == "KIMI_API_KEY=keep-me\n"


def test_provision_keeps_credentials_creates_backup_and_never_reports_key(tmp_path: Path) -> None:
    target = tmp_path / "secrets.env"
    original = "KIMI_API_KEY=existing-secret\n"
    target.write_text(original, encoding="utf-8")
    secured: list[Path] = []

    def acl(paths) -> bool:
        secured.extend(Path(path) for path in paths)
        return True

    result = provision(
        target, key_id="pit-prod-2026-09", apply=True,
        audit_dir=tmp_path / "audit",
        now=datetime(2026, 9, 4, 15, 0, tzinfo=timezone.utc),
        random_bytes=lambda size: bytes(range(size)), acl_setter=acl,
    )

    assert result["status"] == "provisioned" and result["acl_tightened"] is True
    assert result["legacy_assets_resigned"] is False
    rendered = json.dumps(result)
    assert "AAECAwQF" not in rendered and "existing-secret" not in rendered
    written = target.read_text(encoding="utf-8")
    assert "KIMI_API_KEY=existing-secret" in written
    assert "GL_PIT_HMAC_KEY_ID=pit-prod-2026-09" in written
    assert "GL_PIT_HMAC_KEY=base64:AAECAwQF" in written
    assert "GL_PIT_HMAC_VERIFY_KEYS={}" in written
    backup = tmp_path / "audit" / result["backup_name"]
    assert backup.read_text(encoding="utf-8") == original
    assert len(secured) == 2 and secured[0] == backup
    assert secured[1].name.startswith(".secrets.env.pit-key-")
    audit = next((tmp_path / "audit").glob("provision-audit.*.json"))
    assert "AAECAwQF" not in audit.read_text(encoding="utf-8")


def test_existing_partial_or_complete_pit_configuration_is_never_overwritten(tmp_path: Path) -> None:
    for key in PIT_KEYS:
        target = tmp_path / f"{key}.env"
        target.write_text(f"{key}=existing\n", encoding="utf-8")
        result = provision(
            target, key_id="pit-prod-2026-09", apply=True,
            audit_dir=tmp_path / "audit", random_bytes=lambda _: b"x" * 32,
            acl_setter=lambda _paths: True,
        )
        assert result["status"] == "already_configured"
        assert target.read_text(encoding="utf-8") == f"{key}=existing\n"


def test_acl_failure_blocks_replacement(tmp_path: Path) -> None:
    target = tmp_path / "secrets.env"
    original = b"OPENAI_API_KEY=keep\n"
    target.write_bytes(original)

    try:
        provision(
            target, key_id="pit-prod-2026-09", apply=True,
            audit_dir=tmp_path / "audit", random_bytes=lambda _: b"x" * 32,
            acl_setter=lambda _paths: False,
        )
    except PermissionError:
        pass
    else:
        raise AssertionError("ACL failure must block provisioning")
    assert target.read_bytes() == original
