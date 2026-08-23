from __future__ import annotations

import os
import re
import sys
import time
from pathlib import Path

import pytest

from guanlan_v2.channels import secrets as secrets_mod
from guanlan_v2.channels.secrets import (
    MemoryProtector,
    SecretStore,
    SecretUnavailable,
    WindowsDpapiProtector,
)


RAW_TOKEN = "123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZ_abcdefghi"


class FakeProtector:
    def protect(self, value: bytes) -> bytes:
        return b"sealed:" + value[::-1]

    def unprotect(self, value: bytes) -> bytes:
        if not value.startswith(b"sealed:"):
            raise SecretUnavailable("wrong account")
        return value[7:][::-1]


class NeverDecryptProtector(FakeProtector):
    def unprotect(self, value: bytes) -> bytes:  # pragma: no cover - must stay unused
        raise AssertionError("metadata enumeration decrypted a credential")


def test_memory_protector_round_trip_is_not_plaintext():
    protector = MemoryProtector()
    raw = b"high-entropy-test-value"
    sealed = protector.protect(raw)
    assert raw not in sealed
    assert protector.unprotect(sealed) == raw
    with pytest.raises(SecretUnavailable, match="credential_unavailable"):
        protector.unprotect(sealed[:-1] + bytes([sealed[-1] ^ 1]))


def test_secret_file_never_contains_plaintext(tmp_path):
    store = SecretStore(tmp_path / "secrets", protector=FakeProtector())
    credential_id = store.put(RAW_TOKEN)

    assert store.get(credential_id) == RAW_TOKEN
    files = list((tmp_path / "secrets").iterdir())
    assert [path.name for path in files] == [f"{credential_id}.bin"]
    assert RAW_TOKEN.encode() not in files[0].read_bytes()


def test_credential_ids_are_strict_opaque_and_unpredictable(tmp_path):
    store = SecretStore(tmp_path / "secrets", protector=FakeProtector())
    ids = {store.put(f"secret-{index}") for index in range(16)}

    assert len(ids) == 16
    assert all(re.fullmatch(r"cred_[0-9a-f]{32}", value) for value in ids)
    for invalid in ("cred_1", "../cred_" + "a" * 32, "cred_" + "A" * 32, "x" * 37):
        with pytest.raises(SecretUnavailable, match="credential_unavailable"):
            store.get(invalid)


def test_replace_is_atomic_and_delete_is_idempotent(tmp_path):
    store = SecretStore(tmp_path / "secrets", protector=FakeProtector())
    credential_id = store.put("old-value")

    assert store.replace(credential_id, "new-value") == credential_id
    assert store.get(credential_id) == "new-value"
    assert not list((tmp_path / "secrets").glob("*.tmp"))

    store.delete(credential_id)
    store.delete(credential_id)
    with pytest.raises(SecretUnavailable, match="credential_unavailable"):
        store.get(credential_id)


def test_replace_failure_preserves_old_ciphertext(tmp_path, monkeypatch):
    store = SecretStore(tmp_path / "secrets", protector=FakeProtector())
    credential_id = store.put("old-value")
    old_ciphertext = (tmp_path / "secrets" / f"{credential_id}.bin").read_bytes()

    def fail_replace(source, destination):
        raise OSError("simulated replace failure")

    monkeypatch.setattr(secrets_mod.os, "replace", fail_replace)
    with pytest.raises(SecretUnavailable, match="credential_unavailable"):
        store.replace(credential_id, RAW_TOKEN)

    assert (
        tmp_path / "secrets" / f"{credential_id}.bin"
    ).read_bytes() == old_ciphertext
    assert not list((tmp_path / "secrets").glob("*.tmp"))


def test_write_failure_leaves_no_partial_file(tmp_path, monkeypatch):
    store = SecretStore(tmp_path / "secrets", protector=FakeProtector())

    def fail_write(fd, data):
        raise OSError("simulated disk failure")

    monkeypatch.setattr(secrets_mod.os, "write", fail_write)
    with pytest.raises(SecretUnavailable, match="credential_unavailable"):
        store.put(RAW_TOKEN)

    assert list((tmp_path / "secrets").iterdir()) == []


def test_decrypt_failure_has_safe_error_and_no_token_in_logs(tmp_path, caplog):
    store = SecretStore(tmp_path / "secrets", protector=FakeProtector())
    credential_id = store.put(RAW_TOKEN)
    (tmp_path / "secrets" / f"{credential_id}.bin").write_bytes(RAW_TOKEN.encode())

    with pytest.raises(SecretUnavailable, match="^credential_unavailable$") as caught:
        store.get(credential_id)

    assert RAW_TOKEN not in str(caught.value)
    assert RAW_TOKEN not in repr(caught.value)
    assert RAW_TOKEN not in caplog.text


def test_store_has_no_plaintext_bulk_or_reveal_api_and_metadata_never_decrypts(
    tmp_path,
):
    root = tmp_path / "secrets"
    store = SecretStore(root, protector=NeverDecryptProtector())
    first = store.put("first")
    second = store.put("second")
    (root / "not-a-credential.bin").write_bytes(b"ignore")
    (root / ("cred_" + "f" * 32 + ".txt")).write_bytes(b"ignore")

    public_names = {name for name in dir(store) if not name.startswith("_")}
    assert "reveal" not in public_names
    assert "list_plaintext" not in public_names
    metadata = list(store.iter_metadata())
    assert {item.credential_id for item in metadata} == {first, second}
    assert all(isinstance(item.mtime, float) for item in metadata)


def test_startup_only_removes_expired_well_formed_tmp_files(tmp_path):
    root = tmp_path / "secrets"
    root.mkdir()
    old_valid = root / ("cred_" + "a" * 32 + ".bin.tmp")
    fresh_valid = root / ("cred_" + "b" * 32 + ".bin.tmp")
    old_invalid = root / "unrelated.tmp"
    for path in (old_valid, fresh_valid, old_invalid):
        path.write_bytes(b"partial")
    old = time.time() - 7200
    os.utime(old_valid, (old, old))
    os.utime(old_invalid, (old, old))

    SecretStore(root, protector=FakeProtector(), tmp_max_age_seconds=3600)

    assert not old_valid.exists()
    assert fresh_valid.exists()
    assert old_invalid.exists()


def _make_symlink(link: Path, target: Path) -> None:
    try:
        link.symlink_to(target, target_is_directory=target.is_dir())
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"symlinks unavailable: {exc}")


def test_symlinked_parent_is_rejected(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    link = tmp_path / "linked"
    _make_symlink(link, outside)

    with pytest.raises(SecretUnavailable, match="credential_unavailable"):
        SecretStore(link / "secrets", protector=FakeProtector())


def test_symlinked_target_is_rejected_without_reading_outside(tmp_path):
    root = tmp_path / "secrets"
    store = SecretStore(root, protector=FakeProtector())
    credential_id = "cred_" + "d" * 32
    outside = tmp_path / "outside.bin"
    outside.write_bytes(FakeProtector().protect(RAW_TOKEN.encode()))
    _make_symlink(root / f"{credential_id}.bin", outside)

    with pytest.raises(SecretUnavailable, match="credential_unavailable"):
        store.get(credential_id)


def test_non_windows_dpapi_has_no_plaintext_fallback(monkeypatch):
    monkeypatch.setattr(secrets_mod.sys, "platform", "linux")
    protector = WindowsDpapiProtector()

    with pytest.raises(SecretUnavailable, match="credential_unavailable"):
        protector.protect(RAW_TOKEN.encode())
    with pytest.raises(SecretUnavailable, match="credential_unavailable"):
        protector.unprotect(b"ciphertext")


@pytest.mark.skipif(sys.platform != "win32", reason="Windows CurrentUser DPAPI only")
def test_windows_dpapi_smoke_round_trip_and_delete(tmp_path):
    pytest.importorskip("win32crypt")
    store = SecretStore(tmp_path / "secrets")
    fake_high_entropy_value = "dpapi-smoke-" + os.urandom(32).hex()
    credential_id = store.put(fake_high_entropy_value)
    try:
        assert store.get(credential_id) == fake_high_entropy_value
    finally:
        store.delete(credential_id)
    assert not (tmp_path / "secrets" / f"{credential_id}.bin").exists()
