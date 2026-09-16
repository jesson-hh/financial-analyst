"""Read-only production preflight for strict point-in-time evidence.

The probe validates configuration shape only.  It never generates a key,
writes configuration, contacts a time server, or returns secret material.
NTP trust is represented by a short-lived operator/deployment attestation;
the operating-system check that justifies that attestation remains an explicit
operations step.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any, Mapping, Optional

from financial_analyst.data.pit import hong_kong_now
from financial_analyst.data.trusted_receipts import inspect_key_configuration


SCHEMA_VERSION = "pit-deployment-preflight/v1"
NTP_STATUS_ENV = "GL_PIT_NTP_STATUS"
NTP_SOURCE_ENV = "GL_PIT_NTP_SOURCE"
NTP_CHECKED_AT_ENV = "GL_PIT_NTP_CHECKED_AT"
NTP_MAX_AGE_SECONDS_ENV = "GL_PIT_NTP_MAX_AGE_SECONDS"
DEFAULT_NTP_MAX_AGE_SECONDS = 86_400
_MIN_NTP_MAX_AGE_SECONDS = 60
_MAX_NTP_MAX_AGE_SECONDS = 604_800
_MAX_FUTURE_SKEW_SECONDS = 300


def _parse_aware_timestamp(value: Any) -> Optional[datetime]:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed


def _clock_checks(
    source: Mapping[str, str], *, now: Optional[datetime] = None,
) -> tuple[dict[str, dict[str, Any]], list[str]]:
    observed = hong_kong_now() if now is None else now
    checks: dict[str, dict[str, Any]] = {}
    blockers: list[str] = []

    aware = observed.tzinfo is not None and observed.utcoffset() is not None
    zone_key = getattr(observed.tzinfo, "key", None) if aware else None
    hk_aware = bool(aware and zone_key == "Asia/Hong_Kong")
    checks["hong_kong_clock"] = {
        "status": "pass" if hk_aware else "invalid",
        "aware": bool(aware),
        "timezone": "Asia/Hong_Kong" if hk_aware else None,
    }
    if not hk_aware:
        blockers.append("pit_hong_kong_clock_invalid")

    raw_max_age = str(
        source.get(NTP_MAX_AGE_SECONDS_ENV) or DEFAULT_NTP_MAX_AGE_SECONDS
    ).strip()
    try:
        max_age = int(raw_max_age)
    except ValueError:
        max_age = DEFAULT_NTP_MAX_AGE_SECONDS
        max_age_valid = False
    else:
        max_age_valid = _MIN_NTP_MAX_AGE_SECONDS <= max_age <= _MAX_NTP_MAX_AGE_SECONDS
    if not max_age_valid:
        blockers.append("pit_ntp_max_age_invalid")

    declared_synced = str(source.get(NTP_STATUS_ENV) or "").strip().lower()
    source_declared = bool(str(source.get(NTP_SOURCE_ENV) or "").strip())
    source_reasonable = source_declared and len(str(source.get(NTP_SOURCE_ENV))) <= 128
    raw_checked_at = source.get(NTP_CHECKED_AT_ENV)
    checked_at = _parse_aware_timestamp(raw_checked_at)
    ntp_issues: list[str] = []
    age_seconds: Optional[int] = None

    if declared_synced != "synchronized":
        ntp_issues.append("missing" if not declared_synced else "invalid")
        blockers.append(
            "pit_ntp_status_missing" if not declared_synced else "pit_ntp_status_invalid"
        )
    if not source_reasonable:
        ntp_issues.append("missing" if not source_declared else "invalid")
        blockers.append(
            "pit_ntp_source_missing" if not source_declared else "pit_ntp_source_invalid"
        )
    if raw_checked_at in (None, ""):
        ntp_issues.append("missing")
        blockers.append("pit_ntp_checked_at_missing")
    elif checked_at is None:
        ntp_issues.append("invalid")
        blockers.append("pit_ntp_checked_at_invalid")
    elif hk_aware:
        delta = observed.astimezone(timezone.utc) - checked_at.astimezone(timezone.utc)
        age_seconds = int(delta.total_seconds())
        if age_seconds < -_MAX_FUTURE_SKEW_SECONDS:
            ntp_issues.append("invalid")
            blockers.append("pit_ntp_checked_at_future")
        elif max_age_valid and age_seconds > max_age:
            ntp_issues.append("stale")
            blockers.append("pit_ntp_attestation_stale")
    if not max_age_valid:
        ntp_issues.append("invalid")

    ntp_rank = {"pass": 0, "stale": 1, "missing": 2, "invalid": 3}
    ntp_status = max(ntp_issues, key=ntp_rank.__getitem__, default="pass")

    checks["ntp_attestation"] = {
        "status": ntp_status,
        "declared_synchronized": declared_synced == "synchronized",
        "source_declared": source_declared,
        "checked_at": checked_at.isoformat(timespec="seconds") if checked_at else None,
        "age_seconds": age_seconds,
        "max_age_seconds": max_age if max_age_valid else None,
    }
    return checks, blockers


def deployment_preflight(
    environ: Optional[Mapping[str, str]] = None, *, now: Optional[datetime] = None,
) -> dict[str, Any]:
    """Return a deterministic, secret-free readiness record.

    ``ready`` is true only when the active key, explicit verification keyring,
    Hong Kong aware application clock, and recent NTP attestation all pass.
    Missing or malformed fields produce stable blocker codes and a blocked
    result; exceptions and secret values are never projected to callers.
    """

    source = os.environ if environ is None else environ
    try:
        key_status = inspect_key_configuration(source)
    except Exception:  # noqa: BLE001 — a trust probe must fail closed without details
        key_status = {
            "ready": False,
            "active_key_id": None,
            "verify_key_count": 0,
            "checks": {
                "active_key_id": {"status": "invalid", "configured": False},
                "active_key": {
                    "status": "invalid", "configured": False,
                    "minimum_bytes": 32,
                },
                "verify_keyring": {
                    "status": "invalid", "configured": False, "key_count": 0,
                },
            },
            "blockers": ["pit_key_preflight_error"],
        }
    try:
        clock_checks, clock_blockers = _clock_checks(source, now=now)
    except Exception:  # noqa: BLE001 — never serialize clock/provider exception text
        clock_checks = {
            "hong_kong_clock": {
                "status": "invalid", "aware": False, "timezone": None,
            },
            "ntp_attestation": {
                "status": "invalid", "declared_synchronized": False,
                "source_declared": False, "checked_at": None,
                "age_seconds": None, "max_age_seconds": None,
            },
        }
        clock_blockers = ["pit_clock_preflight_error"]
    checks = dict(key_status["checks"])
    checks.update(clock_checks)
    blockers = list(key_status["blockers"]) + clock_blockers
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "ready" if not blockers else "blocked",
        "ready": not blockers,
        "fail_closed": True,
        "active_key_id": key_status.get("active_key_id"),
        "verify_key_count": key_status.get("verify_key_count", 0),
        "checks": checks,
        "blockers": blockers,
    }


__all__ = [
    "DEFAULT_NTP_MAX_AGE_SECONDS",
    "NTP_CHECKED_AT_ENV",
    "NTP_MAX_AGE_SECONDS_ENV",
    "NTP_SOURCE_ENV",
    "NTP_STATUS_ENV",
    "SCHEMA_VERSION",
    "deployment_preflight",
]
