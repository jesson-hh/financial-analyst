"""Fail-closed storage for Telegram credentials.

Production protection is Windows CurrentUser DPAPI.  The store deliberately
exposes only single-secret reads and opaque metadata enumeration; it has no
bulk plaintext or reveal API.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import re
import stat
import sys
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Protocol, runtime_checkable


_CREDENTIAL_ID_RE = re.compile(r"^cred_[0-9a-f]{32}$")
_CREDENTIAL_FILE_RE = re.compile(r"^(cred_[0-9a-f]{32})\.bin$")
_TEMP_FILE_RE = re.compile(r"^cred_[0-9a-f]{32}\.bin\.tmp$")
_MAX_CIPHERTEXT_BYTES = 1024 * 1024
_DEFAULT_TMP_MAX_AGE_SECONDS = 3600.0


class SecretUnavailable(RuntimeError):
    """A deliberately context-free credential failure.

    Protector/backend exception messages are never retained because they may
    contain input bytes, filesystem paths, account names, or provider details.
    """

    def __init__(self, *_unsafe_details: object) -> None:
        super().__init__("credential_unavailable")


@runtime_checkable
class SecretProtector(Protocol):
    """Minimal protection boundary used by :class:`SecretStore`."""

    def protect(self, value: bytes) -> bytes:
        """Return an opaque protected representation of *value*."""

    def unprotect(self, value: bytes) -> bytes:
        """Recover a value or raise :class:`SecretUnavailable`."""


class MemoryProtector:
    """Ephemeral authenticated protector for sealed tests and local fakes.

    Its random key is process-memory only, so data cannot be recovered after
    the instance is discarded.  Production code uses Windows DPAPI instead.
    """

    _PREFIX = b"GLM1"

    def __init__(self, key: bytes | None = None) -> None:
        material = os.urandom(32) if key is None else bytes(key)
        if len(material) < 32:
            raise SecretUnavailable()
        self._key = material

    def _stream(self, nonce: bytes, length: int) -> bytes:
        output = bytearray()
        counter = 0
        while len(output) < length:
            output.extend(
                hmac.new(
                    self._key,
                    b"stream:" + nonce + counter.to_bytes(8, "big"),
                    hashlib.sha256,
                ).digest()
            )
            counter += 1
        return bytes(output[:length])

    def protect(self, value: bytes) -> bytes:
        try:
            raw = bytes(value)
            nonce = os.urandom(16)
            stream = self._stream(nonce, len(raw))
            ciphertext = bytes(left ^ right for left, right in zip(raw, stream))
            tag = hmac.new(
                self._key, b"auth:" + nonce + ciphertext, hashlib.sha256
            ).digest()
            return self._PREFIX + nonce + ciphertext + tag
        except Exception:
            raise SecretUnavailable() from None

    def unprotect(self, value: bytes) -> bytes:
        try:
            sealed = bytes(value)
            minimum = len(self._PREFIX) + 16 + hashlib.sha256().digest_size
            if len(sealed) < minimum or not sealed.startswith(self._PREFIX):
                raise SecretUnavailable()
            nonce = sealed[len(self._PREFIX) : len(self._PREFIX) + 16]
            ciphertext = sealed[len(self._PREFIX) + 16 : -hashlib.sha256().digest_size]
            supplied_tag = sealed[-hashlib.sha256().digest_size :]
            expected_tag = hmac.new(
                self._key, b"auth:" + nonce + ciphertext, hashlib.sha256
            ).digest()
            if not hmac.compare_digest(supplied_tag, expected_tag):
                raise SecretUnavailable()
            stream = self._stream(nonce, len(ciphertext))
            return bytes(left ^ right for left, right in zip(ciphertext, stream))
        except SecretUnavailable:
            raise
        except Exception:
            raise SecretUnavailable() from None


class WindowsDpapiProtector:
    """Windows CurrentUser DPAPI with UI explicitly forbidden."""

    @staticmethod
    def _backend():
        if sys.platform != "win32":
            raise SecretUnavailable()
        try:
            import win32crypt  # type: ignore[import-not-found]
        except Exception:
            raise SecretUnavailable() from None
        return win32crypt

    def protect(self, value: bytes) -> bytes:
        try:
            backend = self._backend()
            flags = int(getattr(backend, "CRYPTPROTECT_UI_FORBIDDEN", 0x1))
            result = backend.CryptProtectData(
                bytes(value),
                "Guanlan channel credential",
                None,
                None,
                None,
                flags,
            )
            sealed = result[1] if isinstance(result, tuple) else result
            if not isinstance(sealed, (bytes, bytearray)) or not sealed:
                raise SecretUnavailable()
            return bytes(sealed)
        except SecretUnavailable:
            raise
        except Exception:
            raise SecretUnavailable() from None

    def unprotect(self, value: bytes) -> bytes:
        try:
            backend = self._backend()
            flags = int(getattr(backend, "CRYPTPROTECT_UI_FORBIDDEN", 0x1))
            result = backend.CryptUnprotectData(bytes(value), None, None, None, flags)
            raw = result[1] if isinstance(result, tuple) else result
            if not isinstance(raw, (bytes, bytearray)):
                raise SecretUnavailable()
            return bytes(raw)
        except SecretUnavailable:
            raise
        except Exception:
            raise SecretUnavailable() from None


@dataclass(frozen=True)
class SecretMetadata:
    """Non-secret information used to reconcile orphaned ciphertext."""

    credential_id: str
    mtime: float


def _is_reparse(path: Path) -> bool:
    """Treat symlinks, junctions, reparse points, and stat failures as unsafe."""
    try:
        if path.is_symlink():
            return True
        is_junction = getattr(path, "is_junction", None)
        if callable(is_junction) and is_junction():
            return True
        info = path.stat(follow_symlinks=False)
        attributes = int(getattr(info, "st_file_attributes", 0))
        reparse_flag = int(getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))
        return bool(attributes & reparse_flag)
    except FileNotFoundError:
        return False
    except OSError:
        return True


def _absolute_without_following(path: Path) -> Path:
    return Path(os.path.abspath(os.fspath(path)))


def _assert_no_reparse_chain(path: Path) -> None:
    """Check every existing hop without resolving through a link."""
    absolute = _absolute_without_following(path)
    anchor = Path(absolute.anchor) if absolute.anchor else Path.cwd()
    probe = anchor
    parts = absolute.parts[1:] if absolute.anchor else absolute.parts
    for part in parts:
        probe = probe / part
        try:
            probe.stat(follow_symlinks=False)
        except FileNotFoundError:
            continue
        except OSError:
            raise SecretUnavailable() from None
        if _is_reparse(probe):
            raise SecretUnavailable()


def _inside(root: Path, candidate: Path) -> bool:
    try:
        common = os.path.commonpath(
            [os.path.normcase(os.fspath(root)), os.path.normcase(os.fspath(candidate))]
        )
    except (TypeError, ValueError):
        return False
    return os.path.normcase(common) == os.path.normcase(os.fspath(root))


def _tighten_current_user_permissions(path: Path, *, directory: bool) -> None:
    """Best-effort owner-only permissions; protection never depends on success."""
    try:
        os.chmod(path, 0o700 if directory else 0o600)
    except OSError:
        pass
    if sys.platform != "win32":
        return
    try:
        import ntsecuritycon  # type: ignore[import-not-found]
        import win32api  # type: ignore[import-not-found]
        import win32security  # type: ignore[import-not-found]

        token = win32security.OpenProcessToken(
            win32api.GetCurrentProcess(), win32security.TOKEN_QUERY
        )
        user_sid = win32security.GetTokenInformation(token, win32security.TokenUser)[0]
        dacl = win32security.ACL()
        dacl.AddAccessAllowedAce(
            win32security.ACL_REVISION, ntsecuritycon.FILE_ALL_ACCESS, user_sid
        )
        protected = int(
            getattr(win32security, "PROTECTED_DACL_SECURITY_INFORMATION", 0x80000000)
        )
        win32security.SetNamedSecurityInfo(
            os.fspath(path),
            win32security.SE_FILE_OBJECT,
            win32security.DACL_SECURITY_INFORMATION | protected,
            None,
            None,
            dacl,
            None,
        )
    except Exception:
        # DPAPI remains the security boundary if ACL hardening is unavailable.
        pass


class SecretStore:
    """Atomic ciphertext files addressed only by unguessable opaque IDs."""

    def __init__(
        self,
        root: str | os.PathLike[str],
        *,
        protector: SecretProtector | None = None,
        tmp_max_age_seconds: float = _DEFAULT_TMP_MAX_AGE_SECONDS,
    ) -> None:
        self._root = _absolute_without_following(Path(root))
        self._protector: SecretProtector = protector or WindowsDpapiProtector()
        self._lock = threading.RLock()
        try:
            max_age = float(tmp_max_age_seconds)
            if max_age < 0:
                raise ValueError
            _assert_no_reparse_chain(self._root.parent)
            self._root.mkdir(parents=True, exist_ok=True)
            _assert_no_reparse_chain(self._root)
            if not self._root.is_dir() or _is_reparse(self._root):
                raise SecretUnavailable()
            self._resolved_root = self._root.resolve(strict=True)
            _tighten_current_user_permissions(self._root, directory=True)
            self._cleanup_expired_temps(max_age)
        except SecretUnavailable:
            raise
        except Exception:
            raise SecretUnavailable() from None

    def _assert_root(self) -> None:
        _assert_no_reparse_chain(self._root)
        if not self._root.is_dir() or _is_reparse(self._root):
            raise SecretUnavailable()
        try:
            current = self._root.resolve(strict=True)
        except (OSError, RuntimeError):
            raise SecretUnavailable() from None
        if current != self._resolved_root:
            raise SecretUnavailable()

    @staticmethod
    def _validate_id(credential_id: object) -> str:
        if not isinstance(credential_id, str) or not _CREDENTIAL_ID_RE.fullmatch(
            credential_id
        ):
            raise SecretUnavailable()
        return credential_id

    def _paths(self, credential_id: object) -> tuple[Path, Path]:
        safe_id = self._validate_id(credential_id)
        self._assert_root()
        target = self._root / f"{safe_id}.bin"
        temporary = self._root / f"{safe_id}.bin.tmp"
        for candidate in (target, temporary):
            absolute = _absolute_without_following(candidate)
            if absolute.parent != self._root:
                raise SecretUnavailable()
            try:
                resolved = absolute.resolve(strict=False)
            except (OSError, RuntimeError):
                raise SecretUnavailable() from None
            if not _inside(self._resolved_root, resolved):
                raise SecretUnavailable()
            if (absolute.exists() or absolute.is_symlink()) and _is_reparse(absolute):
                raise SecretUnavailable()
        return target, temporary

    @staticmethod
    def _regular_file(path: Path) -> os.stat_result:
        try:
            info = path.stat(follow_symlinks=False)
        except OSError:
            raise SecretUnavailable() from None
        if not stat.S_ISREG(info.st_mode) or _is_reparse(path):
            raise SecretUnavailable()
        return info

    @staticmethod
    def _fsync_directory(path: Path) -> None:
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
        directory_flag = getattr(os, "O_DIRECTORY", 0)
        if directory_flag:
            flags |= directory_flag
        try:
            fd = os.open(path, flags)
        except OSError:
            return
        try:
            os.fsync(fd)
        except OSError:
            pass
        finally:
            os.close(fd)

    def _write_ciphertext(
        self, credential_id: str, ciphertext: bytes, *, replacing: bool
    ) -> None:
        target, temporary = self._paths(credential_id)
        target_exists = target.exists() or target.is_symlink()
        if replacing:
            if not target_exists:
                raise SecretUnavailable()
            self._regular_file(target)
        elif target_exists:
            raise SecretUnavailable()
        if temporary.exists() or temporary.is_symlink():
            raise SecretUnavailable()

        fd: int | None = None
        created_temp = False
        try:
            flags = (
                os.O_WRONLY
                | os.O_CREAT
                | os.O_EXCL
                | getattr(os, "O_BINARY", 0)
                | getattr(os, "O_NOFOLLOW", 0)
            )
            fd = os.open(temporary, flags, 0o600)
            created_temp = True
            view = memoryview(ciphertext)
            written = 0
            while written < len(view):
                count = os.write(fd, view[written:])
                if count <= 0:
                    raise OSError("short write")
                written += count
            os.fsync(fd)
            os.close(fd)
            fd = None
            _tighten_current_user_permissions(temporary, directory=False)
            self._regular_file(temporary)

            # Recheck the destination immediately before the atomic operation.
            self._assert_root()
            current_exists = target.exists() or target.is_symlink()
            if replacing:
                if not current_exists:
                    raise SecretUnavailable()
                self._regular_file(target)
            elif current_exists:
                raise SecretUnavailable()
            os.replace(temporary, target)
            created_temp = False
            self._regular_file(target)
            _tighten_current_user_permissions(target, directory=False)
            self._fsync_directory(self._root)
        except SecretUnavailable:
            raise
        except Exception:
            raise SecretUnavailable() from None
        finally:
            if fd is not None:
                try:
                    os.close(fd)
                except OSError:
                    pass
            if created_temp:
                try:
                    if temporary.exists() and not _is_reparse(temporary):
                        temporary.unlink()
                except OSError:
                    pass

    def put(self, value: str) -> str:
        """Protect *value* and atomically store it under a fresh opaque ID."""
        if not isinstance(value, str) or not value:
            raise SecretUnavailable()
        try:
            ciphertext = bytes(self._protector.protect(value.encode("utf-8")))
            if not ciphertext or len(ciphertext) > _MAX_CIPHERTEXT_BYTES:
                raise SecretUnavailable()
        except SecretUnavailable:
            raise
        except Exception:
            raise SecretUnavailable() from None

        with self._lock:
            for _ in range(16):
                credential_id = f"cred_{uuid.uuid4().hex}"
                try:
                    self._write_ciphertext(credential_id, ciphertext, replacing=False)
                    return credential_id
                except SecretUnavailable:
                    target = self._root / f"{credential_id}.bin"
                    if target.exists() or target.is_symlink():
                        continue
                    raise
        raise SecretUnavailable()

    def get(self, credential_id: str) -> str:
        """Decrypt one credential, returning only a context-free failure."""
        with self._lock:
            try:
                target, _ = self._paths(credential_id)
                before = self._regular_file(target)
                if before.st_size <= 0 or before.st_size > _MAX_CIPHERTEXT_BYTES:
                    raise SecretUnavailable()
                flags = (
                    os.O_RDONLY
                    | getattr(os, "O_BINARY", 0)
                    | getattr(os, "O_NOFOLLOW", 0)
                )
                fd = os.open(target, flags)
                try:
                    opened = os.fstat(fd)
                    if not stat.S_ISREG(opened.st_mode):
                        raise SecretUnavailable()
                    chunks: list[bytes] = []
                    remaining = _MAX_CIPHERTEXT_BYTES + 1
                    while remaining > 0:
                        chunk = os.read(fd, min(64 * 1024, remaining))
                        if not chunk:
                            break
                        chunks.append(chunk)
                        remaining -= len(chunk)
                finally:
                    os.close(fd)
                after = self._regular_file(target)

                def identity(item: os.stat_result) -> tuple[int, int, int, int]:
                    return (
                        item.st_dev,
                        item.st_ino,
                        item.st_size,
                        item.st_mtime_ns,
                    )

                if identity(before) != identity(opened) or identity(opened) != identity(
                    after
                ):
                    raise SecretUnavailable()
                ciphertext = b"".join(chunks)
                if (
                    len(ciphertext) != opened.st_size
                    or len(ciphertext) > _MAX_CIPHERTEXT_BYTES
                ):
                    raise SecretUnavailable()
                plaintext = self._protector.unprotect(ciphertext)
                return bytes(plaintext).decode("utf-8")
            except SecretUnavailable:
                raise
            except Exception:
                raise SecretUnavailable() from None

    def replace(self, credential_id: str, value: str) -> str:
        """Atomically replace a credential without jeopardizing old ciphertext."""
        safe_id = self._validate_id(credential_id)
        if not isinstance(value, str) or not value:
            raise SecretUnavailable()
        try:
            ciphertext = bytes(self._protector.protect(value.encode("utf-8")))
            if not ciphertext or len(ciphertext) > _MAX_CIPHERTEXT_BYTES:
                raise SecretUnavailable()
        except SecretUnavailable:
            raise
        except Exception:
            raise SecretUnavailable() from None
        with self._lock:
            self._write_ciphertext(safe_id, ciphertext, replacing=True)
        return safe_id

    def delete(self, credential_id: str) -> None:
        """Idempotently delete one ciphertext file."""
        with self._lock:
            try:
                target, _ = self._paths(credential_id)
                if not target.exists() and not target.is_symlink():
                    return
                self._regular_file(target)
                target.unlink()
                self._fsync_directory(self._root)
            except SecretUnavailable:
                raise
            except Exception:
                raise SecretUnavailable() from None

    def iter_metadata(self) -> Iterable[SecretMetadata]:
        """Snapshot legal opaque IDs and mtimes without decrypting any file."""
        with self._lock:
            try:
                self._assert_root()
                entries: list[SecretMetadata] = []
                with os.scandir(self._root) as iterator:
                    for entry in iterator:
                        match = _CREDENTIAL_FILE_RE.fullmatch(entry.name)
                        if match is None or entry.is_symlink():
                            continue
                        try:
                            info = entry.stat(follow_symlinks=False)
                        except OSError:
                            continue
                        path = self._root / entry.name
                        if not stat.S_ISREG(info.st_mode) or _is_reparse(path):
                            continue
                        entries.append(
                            SecretMetadata(
                                credential_id=match.group(1), mtime=float(info.st_mtime)
                            )
                        )
                return iter(sorted(entries, key=lambda item: item.credential_id))
            except SecretUnavailable:
                raise
            except Exception:
                raise SecretUnavailable() from None

    def _cleanup_expired_temps(self, max_age_seconds: float) -> None:
        cutoff = time.time() - max_age_seconds
        try:
            with os.scandir(self._root) as iterator:
                for entry in iterator:
                    if (
                        _TEMP_FILE_RE.fullmatch(entry.name) is None
                        or entry.is_symlink()
                    ):
                        continue
                    try:
                        info = entry.stat(follow_symlinks=False)
                    except OSError:
                        continue
                    path = self._root / entry.name
                    if (
                        stat.S_ISREG(info.st_mode)
                        and not _is_reparse(path)
                        and info.st_mtime < cutoff
                    ):
                        try:
                            path.unlink()
                        except OSError:
                            pass
        except OSError:
            raise SecretUnavailable() from None


__all__ = [
    "MemoryProtector",
    "SecretMetadata",
    "SecretProtector",
    "SecretStore",
    "SecretUnavailable",
    "WindowsDpapiProtector",
]
