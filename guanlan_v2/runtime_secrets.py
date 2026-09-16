"""Fail-closed loader for the optional local credential fallback file.

``var/secrets.env`` exists solely to bridge credentials into watchdog-started
desktop processes whose inherited environment can be stale.  It is *not* a
general dotenv file: runtime configuration, feature switches and write gates
must come from the real process environment/deployment configuration.

The public status returned by :func:`load_credential_file` deliberately keeps
only key names and counters.  Secret values, raw lines, file paths and parser
exception messages are never retained in that status.
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import MutableMapping


# Exact allowlist.  Do not replace this with suffix matching (``*_TOKEN`` or
# ``*_API_KEY``): an unknown variable that merely looks credential-like must
# remain blocked until its consumer and security semantics have been reviewed.
ALLOWED_CREDENTIAL_KEYS = frozenset({
    # LLM providers declared by the in-repo and bundled engine configs.
    "ANTHROPIC_API_KEY",
    "DASHSCOPE_API_KEY",
    "DEEPSEEK_API_KEY",
    "KIMI_API_KEY",
    "OPENAI_API_KEY",
    # Reviewed data-provider credentials used by the vendored engine.
    "FA_TUSHARE_TOKEN",
    "HUGGINGFACE_TOKEN",
    "TDX_API_KEY",
    "TUSHARE_TOKEN",
    # Local MCP transport authentication.  The separate GUANLAN_MCP_WRITE
    # feature gate is intentionally not allowlisted.
    "GUANLAN_MCP_BEARER_TOKEN",
    # Strict PIT signing/rotation material.  Clock/NTP declarations are runtime
    # configuration and therefore intentionally excluded.
    "GL_PIT_HMAC_KEY_ID",
    "GL_PIT_HMAC_KEY",
    "GL_PIT_HMAC_VERIFY_KEYS",
})

_KEY_RE = re.compile(r"^[A-Z][A-Z0-9_]{0,127}$")
_TEST_MODE_ENV = "GUANLAN_TESTING"


def _blank_status(state: str) -> dict:
    return {
        "schema_version": "credential-fallback-status@1",
        "state": state,
        "file_present": False,
        "loaded_count": 0,
        "loaded_keys": [],
        "existing_count": 0,
        "existing_keys": [],
        "blocked_count": 0,
        "blocked_keys": [],
        "duplicate_count": 0,
        "duplicate_keys": [],
        "empty_count": 0,
        "empty_keys": [],
        "invalid_line_count": 0,
        "values_exposed": False,
    }


def load_credential_file(
    path: Path | str,
    *,
    environ: MutableMapping[str, str] | None = None,
    skip_when_testing: bool = False,
) -> dict:
    """Load reviewed credentials from ``path`` without widening runtime state.

    Existing non-empty process values win.  The file is parsed completely
    before any value is installed, allowing duplicate keys to be rejected as a
    unit instead of making line order a hidden security control.

    ``skip_when_testing`` is used by the production server's default fallback
    call.  Tests can still exercise this function with an explicit temporary
    file by leaving it false; they never need to open the real fallback file.
    """

    target_env = os.environ if environ is None else environ
    if skip_when_testing and target_env.get(_TEST_MODE_ENV) == "1":
        return _blank_status("skipped_testing")

    target = Path(path)
    status = _blank_status("missing")
    try:
        if not target.is_file():
            return status
        status["file_present"] = True
        text = target.read_text(encoding="utf-8")
    except Exception:  # no path/error text: it could disclose deployment details
        status["state"] = "unreadable"
        return status

    candidates: dict[str, str] = {}
    duplicates: set[str] = set()
    blocked: set[str] = set()
    empty: set[str] = set()
    invalid_lines = 0

    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            invalid_lines += 1
            continue
        raw_key, _, raw_value = line.partition("=")
        key = raw_key.strip()
        value = raw_value.strip()
        if _KEY_RE.fullmatch(key) is None:
            # Invalid names may contain arbitrary text, so count but do not
            # reflect them in the public key-name list.
            invalid_lines += 1
            continue
        if key in candidates or key in duplicates:
            candidates.pop(key, None)
            duplicates.add(key)
            continue
        if key not in ALLOWED_CREDENTIAL_KEYS:
            blocked.add(key)
            continue
        if not value:
            empty.add(key)
            continue
        candidates[key] = value

    loaded: list[str] = []
    existing: list[str] = []
    for key in sorted(candidates):
        if str(target_env.get(key) or "").strip():
            existing.append(key)
            continue
        target_env[key] = candidates[key]
        loaded.append(key)

    status.update({
        "state": (
            "blocked_entries"
            if blocked or duplicates or empty or invalid_lines
            else "loaded"
        ),
        "loaded_count": len(loaded),
        "loaded_keys": loaded,
        "existing_count": len(existing),
        "existing_keys": existing,
        "blocked_count": len(blocked),
        "blocked_keys": sorted(blocked),
        "duplicate_count": len(duplicates),
        "duplicate_keys": sorted(duplicates),
        "empty_count": len(empty),
        "empty_keys": sorted(empty),
        "invalid_line_count": invalid_lines,
    })
    return status


def status_is_value_free(status: dict) -> bool:
    """Small defensive invariant used by tests and operational projections."""

    expected = {
        "schema_version", "state", "file_present",
        "loaded_count", "loaded_keys", "existing_count", "existing_keys",
        "blocked_count", "blocked_keys", "duplicate_count", "duplicate_keys",
        "empty_count", "empty_keys", "invalid_line_count", "values_exposed",
    }
    return set(status) == expected and status.get("values_exposed") is False
