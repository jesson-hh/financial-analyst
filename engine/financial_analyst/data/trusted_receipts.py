"""External-key trust anchor for historical evidence receipts.

The data directory and its sidecars are attacker-controlled inputs.  A receipt
is trusted only when its canonical payload has an HMAC made with a key supplied
outside that directory through the process environment.

Configuration (both values are required):

``GL_PIT_HMAC_KEY_ID``
    Stable identifier for the active key.  It is public and may be written to
    receipts.

``GL_PIT_HMAC_KEY``
    Secret key material.  Raw UTF-8 is accepted, as are ``hex:...`` and
    ``base64:...`` encodings.  Decoded keys shorter than 32 bytes are rejected.

``GL_PIT_HMAC_VERIFY_KEYS``
    Optional JSON object mapping retired ``key_id`` values to secret material.
    These keys are verification-only; new records are always signed by the
    active key above.  This permits explicit key rotation without making old
    historical assets unreadable.

No default or sidecar-provided key is ever accepted.  Readers and writers can
share this small module while using distinct HMAC domains for acquisition,
section, and manifest records.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import os
import re
from typing import Any, Mapping, Optional


KEY_ID_ENV = "GL_PIT_HMAC_KEY_ID"
KEY_ENV = "GL_PIT_HMAC_KEY"
VERIFY_KEYS_ENV = "GL_PIT_HMAC_VERIFY_KEYS"
ALGORITHM = "HMAC-SHA256"

F10_ACQUISITION_DOMAIN = "guanlan/f10-acquisition-receipt/v1"
REPORT_SECTION_DOMAIN = "guanlan/report-section-receipt/v1"
REPORT_MANIFEST_DOMAIN = "guanlan/report-evidence-manifest/v1"
WORKSPACE_SNAPSHOT_DOMAIN = "guanlan/workspace-data-snapshot/v1"

_KEY_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")
_SIGNATURE_RE = re.compile(r"[0-9a-f]{64}")
_MIN_KEY_BYTES = 32


class _DuplicateKeyId(ValueError):
    """Internal sentinel used to reject ambiguous JSON keyrings."""


def _strict_json_object(raw: str) -> Any:
    """Decode JSON while refusing duplicate object keys.

    ``json.loads`` normally keeps the last duplicate key silently.  That is a
    poor property for a verification keyring because an operator and the
    runtime could then disagree about which key material is active.
    """

    def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise _DuplicateKeyId
            result[key] = value
        return result

    return json.loads(raw, object_pairs_hook=_pairs)


def _decode_secret(raw: str) -> Optional[bytes]:
    try:
        if raw.startswith("hex:"):
            key = bytes.fromhex(raw[4:])
        elif raw.startswith("base64:"):
            key = base64.b64decode(raw[7:], validate=True)
        else:
            key = raw.encode("utf-8")
    except (ValueError, binascii.Error, UnicodeError):
        return None
    return key if len(key) >= _MIN_KEY_BYTES else None


def active_key() -> Optional[tuple[str, bytes]]:
    """Return the explicitly configured trusted key, never a local fallback."""
    key_id = str(os.environ.get(KEY_ID_ENV) or "").strip()
    raw = os.environ.get(KEY_ENV)
    if not _KEY_ID_RE.fullmatch(key_id) or raw in (None, ""):
        return None
    key = _decode_secret(str(raw))
    return (key_id, key) if key is not None else None


def inspect_key_configuration(
    environ: Optional[Mapping[str, str]] = None,
) -> dict[str, Any]:
    """Return a secret-free deployment view of the PIT signing key setup.

    This function is deliberately read-only and accepts an explicit mapping so
    deployment checks and tests do not have to mutate process state.  It never
    returns raw key material, encoded key text, decoded key length, or invalid
    key identifiers.  An explicitly configured JSON object (``{}`` is valid)
    is required for the verification-only keyring so key-rotation intent is
    unambiguous in production.
    """

    source = os.environ if environ is None else environ
    checks: dict[str, dict[str, Any]] = {}
    blockers: list[str] = []

    key_id = str(source.get(KEY_ID_ENV) or "").strip()
    if not key_id:
        checks["active_key_id"] = {"status": "missing", "configured": False}
        blockers.append("pit_active_key_id_missing")
        public_key_id: Optional[str] = None
    elif not _KEY_ID_RE.fullmatch(key_id):
        checks["active_key_id"] = {"status": "invalid", "configured": True}
        blockers.append("pit_active_key_id_invalid")
        public_key_id = None
    else:
        checks["active_key_id"] = {"status": "pass", "configured": True}
        public_key_id = key_id

    raw_active = source.get(KEY_ENV)
    if raw_active in (None, ""):
        checks["active_key"] = {
            "status": "missing", "configured": False,
            "minimum_bytes": _MIN_KEY_BYTES,
        }
        blockers.append("pit_active_key_missing")
    elif _decode_secret(str(raw_active)) is None:
        checks["active_key"] = {
            "status": "invalid", "configured": True,
            "minimum_bytes": _MIN_KEY_BYTES,
        }
        blockers.append("pit_active_key_invalid")
    else:
        checks["active_key"] = {
            "status": "pass", "configured": True,
            "minimum_bytes": _MIN_KEY_BYTES,
        }

    raw_keyring = source.get(VERIFY_KEYS_ENV)
    keyring_count = 0
    if raw_keyring in (None, ""):
        checks["verify_keyring"] = {
            "status": "missing", "configured": False, "key_count": 0,
        }
        blockers.append("pit_verify_keyring_missing")
    else:
        parsed_ok = True
        try:
            keyring = _strict_json_object(str(raw_keyring))
        except (TypeError, ValueError, RecursionError):
            keyring = None
            parsed_ok = False
            checks["verify_keyring"] = {
                "status": "invalid", "configured": True, "key_count": 0,
            }
            blockers.append("pit_verify_keyring_invalid_json")
        if parsed_ok:
            if not isinstance(keyring, Mapping):
                checks["verify_keyring"] = {
                    "status": "invalid", "configured": True, "key_count": 0,
                }
                blockers.append("pit_verify_keyring_not_object")
            else:
                invalid_id = any(not _KEY_ID_RE.fullmatch(str(k)) for k in keyring)
                invalid_key = any(
                    not isinstance(value, str) or _decode_secret(value) is None
                    for value in keyring.values()
                )
                keyring_count = len(keyring)
                if invalid_id or invalid_key:
                    checks["verify_keyring"] = {
                        "status": "invalid", "configured": True,
                        "key_count": keyring_count,
                    }
                    if invalid_id:
                        blockers.append("pit_verify_keyring_invalid_key_id")
                    if invalid_key:
                        blockers.append("pit_verify_keyring_invalid_key")
                else:
                    checks["verify_keyring"] = {
                        "status": "pass", "configured": True,
                        "key_count": keyring_count,
                    }

    return {
        "ready": not blockers,
        "active_key_id": public_key_id,
        "verify_key_count": keyring_count,
        "checks": checks,
        "blockers": blockers,
    }


def verification_key(key_id: Any) -> Optional[bytes]:
    """Resolve an active or explicitly configured verification-only key."""
    wanted = str(key_id or "").strip()
    if not _KEY_ID_RE.fullmatch(wanted):
        return None
    configured = active_key()
    if configured is not None and hmac.compare_digest(configured[0], wanted):
        return configured[1]
    raw_keyring = os.environ.get(VERIFY_KEYS_ENV)
    if raw_keyring in (None, ""):
        return None
    try:
        keyring = _strict_json_object(str(raw_keyring))
    except (TypeError, ValueError, RecursionError):
        return None
    if not isinstance(keyring, Mapping):
        return None
    candidate = keyring.get(wanted)
    if not isinstance(candidate, str):
        return None
    return _decode_secret(candidate)


def canonical_bytes(domain: str, payload: Mapping[str, Any]) -> bytes:
    """Canonical, domain-separated bytes used by both signers and verifiers."""
    if not str(domain or "").strip() or not isinstance(payload, Mapping):
        raise ValueError("a non-empty trust domain and mapping payload are required")
    body = json.dumps(
        dict(payload), ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False, default=str,
    ).encode("utf-8")
    return str(domain).encode("utf-8") + b"\0" + body


def sign_payload(domain: str, payload: Mapping[str, Any]) -> Optional[dict[str, str]]:
    """Return public authentication fields, or ``None`` without a trusted key."""
    configured = active_key()
    if configured is None:
        return None
    key_id, key = configured
    signature = hmac.new(key, canonical_bytes(domain, payload), hashlib.sha256).hexdigest()
    return {"algorithm": ALGORITHM, "key_id": key_id, "signature": signature}


def verify_payload(
    domain: str, payload: Mapping[str, Any], auth: Any,
) -> bool:
    """Verify against the external configured key and exact canonical payload."""
    if not isinstance(auth, Mapping):
        return False
    key_id = str(auth.get("key_id") or "")
    if auth.get("algorithm") != ALGORITHM:
        return False
    key = verification_key(key_id)
    if key is None:
        return False
    supplied = str(auth.get("signature") or "").lower()
    if not _SIGNATURE_RE.fullmatch(supplied):
        return False
    expected = hmac.new(
        key, canonical_bytes(domain, payload), hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(supplied, expected)


def workspace_snapshot_auth_payload(
    *, asset_id: Any, workspace_id: Any, dataset_id: Any,
    payload_sha256: Any, acquired_at: Any, pulled_at: Any, as_of: Any, source: Any,
    subject: Any, asset_version: Any,
) -> dict[str, Any]:
    """Canonical trust statement for one durable workspace snapshot asset."""
    return {
        "schema_version": "workspace-data-snapshot-auth/v1",
        "asset_id": str(asset_id or ""),
        "workspace_id": str(workspace_id or ""),
        "dataset_id": str(dataset_id or ""),
        "payload_sha256": str(payload_sha256 or ""),
        "acquired_at": str(acquired_at or ""),
        "pulled_at": str(pulled_at or ""),
        "as_of": str(as_of or ""),
        "source": str(source or ""),
        "subject": dict(subject) if isinstance(subject, Mapping) else {},
        "asset_version": asset_version,
    }


__all__ = [
    "ALGORITHM",
    "F10_ACQUISITION_DOMAIN",
    "KEY_ENV",
    "KEY_ID_ENV",
    "VERIFY_KEYS_ENV",
    "REPORT_MANIFEST_DOMAIN",
    "REPORT_SECTION_DOMAIN",
    "WORKSPACE_SNAPSHOT_DOMAIN",
    "active_key",
    "canonical_bytes",
    "inspect_key_configuration",
    "sign_payload",
    "verify_payload",
    "verification_key",
    "workspace_snapshot_auth_payload",
]
