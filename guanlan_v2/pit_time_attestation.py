"""Windows Time deployment attestation for strict PIT.

This module is intentionally separate from the read-only PIT preflight.  The
watchdog (the deployment manager on this host) invokes it immediately before
starting a server generation.  Only a live, synchronized ``w32tm`` status can
produce a short-lived attestation; inherited/stored claims are not trusted.
"""
from __future__ import annotations

import asyncio
import ntpath
import os
import platform
import re
import subprocess
from datetime import datetime
from typing import Any, Callable, Mapping, MutableMapping
from zoneinfo import ZoneInfo


SCHEMA_VERSION = "windows-time-attestation/v1"
_HK = ZoneInfo("Asia/Hong_Kong")
_MAX_LAST_SYNC_AGE_SECONDS = 86_400
_MAX_FUTURE_SKEW_SECONDS = 300
_PROCESS_ATTESTATION_MAX_AGE_SECONDS = 86_400
_PROCESS_ATTESTATION_REFRESH_SECONDS = 300
_NTP_STATUS_ENV = "GL_PIT_NTP_STATUS"
_NTP_SOURCE_ENV = "GL_PIT_NTP_SOURCE"
_NTP_CHECKED_AT_ENV = "GL_PIT_NTP_CHECKED_AT"
_NTP_MAX_AGE_SECONDS_ENV = "GL_PIT_NTP_MAX_AGE_SECONDS"
_PROCESS_ATTESTATION_NAMES = (
    _NTP_STATUS_ENV,
    _NTP_SOURCE_ENV,
    _NTP_CHECKED_AT_ENV,
    _NTP_MAX_AGE_SECONDS_ENV,
)
_DATE_RE = re.compile(
    r"(?P<year>\d{4})[/-](?P<month>\d{1,2})[/-](?P<day>\d{1,2})"
    r"\s+(?P<hour>\d{1,2}):(?P<minute>\d{2}):(?P<second>\d{2})"
)


def _blocked(*codes: str) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "blocked",
        "ready": False,
        "checked_at": None,
        "source": None,
        "blockers": list(codes),
    }


def _value_after_colon(line: str) -> str:
    return line.split(":", 1)[1].strip() if ":" in line else ""


def _leading_integer(value: str) -> int | None:
    match = re.match(r"\s*(-?\d+)", value)
    return int(match.group(1)) if match else None


def parse_windows_time_status(
    output: str, *, now: datetime | None = None,
) -> dict[str, Any]:
    """Parse standard ``w32tm /query /status`` output without label text.

    Windows localizes field labels.  The command's stable field order and the
    ASCII values after the first colon are used instead, so Chinese and English
    installations follow the same validation path.
    """

    observed = datetime.now(_HK) if now is None else now
    if observed.tzinfo is None or observed.utcoffset() is None:
        return _blocked("pit_time_probe_clock_naive")
    observed = observed.astimezone(_HK)
    lines = [line.strip() for line in str(output or "").splitlines() if line.strip()]
    if len(lines) < 9:
        return _blocked("pit_w32tm_status_shape_invalid")

    leap = _leading_integer(_value_after_colon(lines[0]))
    stratum = _leading_integer(_value_after_colon(lines[1]))
    if leap not in {0, 1}:
        return _blocked("pit_w32tm_unsynchronized")
    if stratum is None or not 1 <= stratum <= 15:
        return _blocked("pit_w32tm_stratum_invalid")

    # Standard status fields 7/8 are last-success and source.  Use their stable
    # positions rather than localized labels; tolerate future trailing detail.
    sync_text = _value_after_colon(lines[6])
    source_text = _value_after_colon(lines[7])
    match = _DATE_RE.search(sync_text)
    if match is None:
        return _blocked("pit_w32tm_last_sync_invalid")
    last_sync = datetime(
        *(int(match.group(name)) for name in (
            "year", "month", "day", "hour", "minute", "second"
        )),
        tzinfo=_HK,
    )
    age_seconds = int((observed - last_sync).total_seconds())
    if age_seconds < -_MAX_FUTURE_SKEW_SECONDS:
        return _blocked("pit_w32tm_last_sync_future")
    if age_seconds > _MAX_LAST_SYNC_AGE_SECONDS:
        return _blocked("pit_w32tm_last_sync_stale")

    lowered_source = source_text.casefold()
    if not source_text or any(token in lowered_source for token in (
        "local cmos", "free-running", "unspecified", "unknown",
    )):
        return _blocked("pit_w32tm_source_untrusted")

    return {
        "schema_version": SCHEMA_VERSION,
        "status": "ready",
        "ready": True,
        "checked_at": observed.isoformat(timespec="seconds"),
        # Do not project the host/source string.  Presence and freshness were
        # validated above; the deployment environment only needs a stable kind.
        "source": "w32time-os-status",
        "last_sync_age_seconds": age_seconds,
        "blockers": [],
    }


def probe_windows_time(
    *,
    run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    now: datetime | None = None,
    system: str | None = None,
    environ: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Run the local OS status probe and return a value-free attestation.

    The production server holds HMAC and provider credentials in its process
    environment.  The OS utility therefore receives a deliberately minimal
    environment and an absolute ``System32`` executable path; a time probe
    never needs, and must never inherit, those credentials.
    """

    if (platform.system() if system is None else system) != "Windows":
        return _blocked("pit_w32tm_unavailable")
    source = os.environ if environ is None else environ
    windows_root = str(
        source.get("SystemRoot") or source.get("WINDIR") or r"C:\Windows"
    ).strip()
    if not ntpath.isabs(windows_root):
        windows_root = r"C:\Windows"
    windows_root = ntpath.normpath(windows_root)
    system32 = ntpath.join(windows_root, "System32")
    executable = ntpath.join(system32, "w32tm.exe")
    child_env = {
        "SystemRoot": windows_root,
        "WINDIR": windows_root,
        "PATH": system32,
    }
    system_drive, _ = ntpath.splitdrive(windows_root)
    if system_drive:
        child_env["SystemDrive"] = system_drive
    try:
        result = run(
            [executable, "/query", "/status"],
            text=True,
            capture_output=True,
            errors="replace",
            timeout=15,
            check=False,
            env=child_env,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except Exception:  # never reflect command/host exception details
        return _blocked("pit_w32tm_probe_error")
    if result.returncode != 0:
        return _blocked("pit_w32tm_probe_failed")
    return parse_windows_time_status(result.stdout, now=now)


def clear_process_time_attestation(
    environ: MutableMapping[str, str] | None = None,
) -> None:
    """Remove only the four non-secret runtime NTP declaration fields."""

    target = os.environ if environ is None else environ
    # STATUS is the commit sentinel.  Removing it first means a concurrent
    # preflight can observe only the old complete lease or fail closed; it can
    # never accept a partially replaced declaration as synchronized.
    target.pop(_NTP_STATUS_ENV, None)
    for name in _PROCESS_ATTESTATION_NAMES[1:]:
        target.pop(name, None)


def apply_process_time_attestation(
    attestation: Mapping[str, Any] | None,
    *,
    environ: MutableMapping[str, str] | None = None,
) -> bool:
    """Fail-closed install of one freshly probed, non-secret process lease.

    No result is trusted merely because it has a recent timestamp.  The exact
    schema/status/source emitted by :func:`probe_windows_time` must be present.
    ``GL_PIT_NTP_STATUS`` is written last as a commit sentinel, so readers may
    briefly see a blocked state during refresh but can never see partial data
    as ready.  Any malformed/blocked result clears the whole previous lease.
    """

    target = os.environ if environ is None else environ
    target.pop(_NTP_STATUS_ENV, None)
    payload: Mapping[str, Any] = attestation if isinstance(attestation, Mapping) else {}
    checked_at = str(payload.get("checked_at") or "").strip()
    ready = bool(
        isinstance(attestation, Mapping)
        and payload.get("schema_version") == SCHEMA_VERSION
        and payload.get("status") == "ready"
        and payload.get("ready") is True
        and payload.get("source") == "w32time-os-status"
        and not (payload.get("blockers") or [])
        and checked_at
    )
    if ready:
        try:
            parsed = datetime.fromisoformat(checked_at.replace("Z", "+00:00"))
            ready = parsed.tzinfo is not None and parsed.utcoffset() is not None
        except (TypeError, ValueError):
            ready = False
    if not ready:
        for name in _PROCESS_ATTESTATION_NAMES[1:]:
            target.pop(name, None)
        return False

    try:
        target[_NTP_SOURCE_ENV] = "w32time-os-status"
        target[_NTP_CHECKED_AT_ENV] = checked_at
        target[_NTP_MAX_AGE_SECONDS_ENV] = str(
            _PROCESS_ATTESTATION_MAX_AGE_SECONDS
        )
        target[_NTP_STATUS_ENV] = "synchronized"
    except Exception:  # mapping/process-environment failure must stay blocked
        clear_process_time_attestation(target)
        return False
    return True


async def maintain_process_time_attestation(
    *,
    environ: MutableMapping[str, str] | None = None,
    probe: Callable[[], Mapping[str, Any]] | None = None,
    refresh_seconds: float = _PROCESS_ATTESTATION_REFRESH_SECONDS,
) -> None:
    """Continuously renew the long-lived server's in-process NTP lease.

    Every renewal is a new live OS probe, run off the event loop.  Failure or
    an unexpected exception revokes the old lease immediately, retries on the
    normal cadence, and never stops the current/live research service.
    """

    target = os.environ if environ is None else environ
    selected_probe = probe_windows_time if probe is None else probe
    delay = max(1.0, float(refresh_seconds))
    while True:
        try:
            result = await asyncio.to_thread(selected_probe)
        except Exception:  # probe details must not escape into logs/status
            result = None
        apply_process_time_attestation(result, environ=target)
        await asyncio.sleep(delay)


__all__ = [
    "SCHEMA_VERSION",
    "apply_process_time_attestation",
    "clear_process_time_attestation",
    "maintain_process_time_attestation",
    "parse_windows_time_status",
    "probe_windows_time",
]
