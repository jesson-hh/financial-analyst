"""Bound the disposable runtime state of Guanlan's legacy live probe bridge.

This is not a market-data store.  Only this bridge's last-good response copies
are expired; canonical Stocks data and its existing caches are never swept.
Provider leases deliberately remain shared with Stocks to preserve rate limits.
"""
from __future__ import annotations

import os
from pathlib import Path
import re
import threading
import time

MAX_FILES = 128
MAX_ITEM_BYTES = 4 * 1024 * 1024
MAX_TOTAL_BYTES = 32 * 1024 * 1024
MAX_AGE_SECONDS = 7 * 86400
TEMP_GRACE_SECONDS = 3600
LOG_MAX_BYTES = 1024 * 1024
_LOCK = threading.Lock()
_KEY = re.compile(r"^[0-9a-f]{24}\.json$")
_TEMP_KEY = re.compile(r"^\.[0-9a-f]{24}\.json\.\d+\.\d+\.tmp$")


def runtime_root() -> Path:
    base = Path(os.environ.get("GUANLAN_RUNTIME_ROOT") or (
        Path(__file__).resolve().parents[2] / "var"
    )).resolve()
    return base / "live" / "probe-runtime-v1"


def child_environment() -> dict[str, str]:
    root = runtime_root()
    # These are host-owned constants, never tool arguments. Do not change the
    # canonical data root or provider lease root while isolating cache writes.
    return {
        **os.environ,
        "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1",
        "GUANLAN_LIVE_RESILIENCE_ROOT": str(root / "resilience"),
        "GUANLAN_LIVE_HEALTH_ROOT": str(root / "health"),
    }


def maintain_last_good() -> dict[str, int]:
    """TTL + newest-first quotas, before/after each bridge operation.

    A concurrent upstream writer can briefly exceed the quota until operation
    completion. Refuse symlinks/junction escapes and only remove upstream's
    exact hashed JSON filenames inside our newly owned cache subdirectory.
    """
    removed = errors = temp_files = temp_bytes = 0
    root = runtime_root()
    base = root / "resilience" / "last_good"
    with _LOCK:
        if not base.exists():
            return {"files": 0, "bytes": 0, "removed": 0, "errors": 0,
                    "temp_files": 0, "temp_bytes": 0}
        if base.resolve() != base or root.resolve() != root:
            return {"files": 0, "bytes": 0, "removed": 0, "errors": 1,
                    "temp_files": 0, "temp_bytes": 0}
        rows = []
        now = time.time()
        try:
            for source in base.iterdir():
                if source.is_symlink() or source.resolve() != source or not source.is_dir():
                    continue
                for path in source.iterdir():
                    is_temp = bool(_TEMP_KEY.fullmatch(path.name))
                    if not (_KEY.fullmatch(path.name) or is_temp) or path.is_symlink():
                        continue
                    if path.resolve() != path or not path.is_file():
                        continue
                    try:
                        stat = path.stat()
                        if is_temp:
                            # A killed upstream writer may leave an atomic temp
                            # file. Never remove a potentially active write.
                            if now - stat.st_mtime > TEMP_GRACE_SECONDS:
                                if path.resolve() != path or path.is_symlink():
                                    errors += 1
                                    continue
                                path.unlink()
                                removed += 1
                            else:
                                temp_files += 1
                                temp_bytes += stat.st_size
                            continue
                        rows.append((stat.st_mtime, stat.st_size, path))
                    except OSError:
                        errors += 1
        except OSError:
            errors += 1
        count, total = temp_files, temp_bytes
        for modified, size, path in sorted(rows, reverse=True):
            expire = (now - modified > MAX_AGE_SECONDS or size > MAX_ITEM_BYTES
                      or count >= MAX_FILES or total + size > MAX_TOTAL_BYTES)
            if expire:
                try:
                    # Recheck after enumeration; never follow a replaced link.
                    if path.is_symlink() or path.resolve() != path:
                        errors += 1
                        continue
                    path.unlink()
                    removed += 1
                    continue
                except OSError:
                    errors += 1
            count += 1
            total += size
        return {"files": count, "bytes": total, "removed": removed, "errors": errors,
                "temp_files": temp_files, "temp_bytes": temp_bytes}


def drain_worker_stderr(stream, path: Path) -> None:
    """Drain even newline-free native output; keep at most two 1 MiB logs."""
    try:
        raw = getattr(stream, "buffer", stream)
        read = getattr(raw, "read1", raw.read)
        while True:
            chunk = read(8192)
            if not chunk:
                break
            if isinstance(chunk, str):
                chunk = chunk.encode("utf-8", "replace")
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                if path.exists() and path.stat().st_size + len(chunk) > LOG_MAX_BYTES:
                    path.replace(path.with_suffix(path.suffix + ".1"))
                with path.open("ab") as handle:
                    handle.write(chunk)
            except OSError:
                # A full/unwritable disk must not block the child's protocol.
                pass
    except (OSError, ValueError):
        pass
    finally:
        try:
            stream.close()
        except (OSError, ValueError):
            pass
