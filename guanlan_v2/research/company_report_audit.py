"""Read-only archival of report-writer diagnostics, never research evidence.

Paths are derived only from the trusted report path.  JSON fields (including
``artifact_path`` or links to other diagnostics) are not interpreted or opened.
The finalizer must compare the report-input receipt hash with its separately
loaded risk-sidecar hash before archiving, to reject a changed second read.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import stat
from typing import Any


_DEFAULT_MAX_BYTES = 9 * 1024 * 1024
_RECEIPT_SCHEMA = "company-report-diagnostic-receipt@1"


def _is_reparse(info: os.stat_result) -> bool:
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, "st_file_attributes", 0)
        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    )


def _inspect_path(path: Path) -> os.stat_result:
    """Inspect components from root outward, without resolving link targets."""
    absolute = path.absolute()
    current = Path(absolute.anchor)
    info = current.lstat()
    if _is_reparse(info):
        raise ValueError("reparse_point")
    for component in absolute.parts[1:]:
        current = current / component
        info = current.lstat()
        if _is_reparse(info):
            raise ValueError("reparse_point")
    return info


def _identity(info: os.stat_result) -> tuple[int, int, int, int]:
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns


def _read_diagnostic(path: Path, max_bytes: int) -> tuple[bytes | None, dict[str, Any]]:
    def receipt(status: str, reason: str, detail: str, **extra: Any) -> dict[str, Any]:
        return {
            "schema_version": _RECEIPT_SCHEMA,
            "filename": path.name,
            "status": status,
            "reason_code": reason,
            "detail": detail,
            "diagnostic_only": True,
            **extra,
        }

    try:
        original = _inspect_path(path)
        if not stat.S_ISREG(original.st_mode):
            return None, receipt("invalid", "not_regular_file", "诊断路径不是普通文件。")
        if original.st_size <= 0:
            return None, receipt("invalid", "empty_file", "诊断文件为空。", bytes=0)
        if original.st_size > max_bytes:
            return None, receipt(
                "invalid", "too_large", "诊断文件超过读取上限。", bytes=original.st_size,
            )
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags)
        with os.fdopen(descriptor, "rb") as handle:
            opened = os.fstat(handle.fileno())
            if _is_reparse(opened) or not stat.S_ISREG(opened.st_mode):
                return None, receipt("invalid", "not_regular_file", "打开后的诊断不是普通文件。")
            if _identity(original) != _identity(opened) \
                    or _identity(_inspect_path(path)) != _identity(opened):
                return None, receipt("invalid", "changed_during_read", "诊断文件在读取前发生变化。")
            raw = handle.read(max_bytes + 1)
            if _identity(opened) != _identity(os.fstat(handle.fileno())) \
                    or _identity(_inspect_path(path)) != _identity(opened):
                return None, receipt("invalid", "changed_during_read", "诊断文件在读取中发生变化。")
    except FileNotFoundError:
        return None, receipt("missing", "file_missing", "同 stem 诊断文件不存在。")
    except ValueError:
        return None, receipt("invalid", "reparse_point", "诊断路径包含符号链接或 reparse point。")
    except OSError as exc:
        return None, receipt(
            "invalid", "unreadable", f"无法读取诊断文件（{type(exc).__name__}）。",
        )

    if not raw or len(raw) > max_bytes:
        return None, receipt("invalid", "invalid_size", "诊断读取结果为空或过大。", bytes=len(raw))
    metadata = {"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}
    if b"\x00" in raw:
        return None, receipt("invalid", "nul_byte", "诊断文件包含 NUL。", **metadata)
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return None, receipt("invalid", "not_utf8", "诊断文件不是 UTF-8。", **metadata)

    def reject_constant(value: str) -> None:
        raise ValueError(f"Non-JSON constant: {value}")

    try:
        payload = json.loads(text, parse_constant=reject_constant)
    except (ValueError, RecursionError):
        return None, receipt("invalid", "invalid_json", "诊断文件不是合法 JSON。", **metadata)
    if not isinstance(payload, dict):
        return None, receipt("invalid", "root_not_object", "诊断 JSON 顶层不是对象。", **metadata)
    return raw, receipt(
        "loaded", "diagnostic_loaded",
        "只读保存原始诊断；该文件不作为证据或验收通过依据。", **metadata,
    )


def load_company_report_diagnostics(
    source: Path, max_bytes: int = _DEFAULT_MAX_BYTES,
) -> dict[str, Any]:
    """Return safe same-stem JSON bytes plus explicit per-file receipts.

    Missing, malformed, linked or oversized diagnostics are omitted from
    ``files`` and retained in ``receipts``.  No status here changes report
    acceptance; this function does not write files or follow JSON paths.
    """
    source = Path(source)
    paths = {
        "writer_draft_review.json": source.with_suffix(".draft-review.json"),
        "report_input.json": source.with_suffix(".json"),
    }
    files: dict[str, bytes] = {}
    receipts: dict[str, dict[str, Any]] = {}
    for name, path in paths.items():
        if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes <= 0:
            receipts[name] = {
                "schema_version": _RECEIPT_SCHEMA, "filename": path.name,
                "status": "invalid", "reason_code": "invalid_read_limit",
                "detail": "诊断读取上限无效；未读取任何内容。", "diagnostic_only": True,
            }
            continue
        raw, receipt = _read_diagnostic(path, max_bytes)
        receipts[name] = receipt
        if raw is not None:
            files[name] = raw
    return {"files": files, "receipts": receipts}


__all__ = ["load_company_report_diagnostics"]
