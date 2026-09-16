"""Fail-closed checks for the watchdog's non-secret runtime profile."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest


REPO = Path(__file__).resolve().parents[1]
CHECKER = REPO / "scripts" / "check_9999.ps1"
POWERSHELL = shutil.which("powershell.exe") or shutil.which("powershell")

EXPECTED = {
    "CONSOLE_REVIEW_MODE": "enforce",
    "GUANLAN_DL_DAILY": "1",
    "GUANLAN_EVENTS_INGEST": "1",
    "GUANLAN_MCP_WRITE": "1",
    "GUANLAN_ORCH_LAUNCHER": "1",
    "GUANLAN_REGEN_DAILY": "1",
    "GUANLAN_RERANK_DAILY": "1",
    "GUANLAN_REVIEW_DAILY": "1",
    "GUANLAN_SEATS_DEEP": "1",
    "GUANLAN_SEATS_WATCH": "1",
    "GUANLAN_SNAPSHOT_ARCHIVE": "1",
}


def _validate(path: Path, *, inherited_write_gate: str = "0") -> subprocess.CompletedProcess[str]:
    if POWERSHELL is None:
        pytest.skip("Windows PowerShell is required for watchdog profile validation")
    env = os.environ.copy()
    # The checked-in reviewed profile must override a contrary inherited value.
    env["GUANLAN_MCP_WRITE"] = inherited_write_gate
    return subprocess.run(
        [
            POWERSHELL,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(path),
            "-ValidateRuntimeProfileOnly",
        ],
        cwd=REPO,
        env=env,
        text=True,
        capture_output=True,
        timeout=15,
        check=False,
    )


def _mutated_checker(tmp_path: Path, old: str, new: str) -> Path:
    source = CHECKER.read_text(encoding="utf-8")
    assert source.count(old) == 1
    target = tmp_path / "check_9999.ps1"
    target.write_text(source.replace(old, new), encoding="utf-8")
    return target


def test_watchdog_runtime_profile_exactly_matches_the_reviewed_deployment() -> None:
    result = _validate(CHECKER)

    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    payload = json.loads(result.stdout)
    assert payload == {
        "schema_version": "runtime-gate-profile@1",
        "states": EXPECTED,
    }


@pytest.mark.parametrize(
    ("old", "new", "blocker"),
    [
        (
            "GUANLAN_DL_DAILY          = '1'",
            "GUANLAN_DL_DAILY          = 'true'",
            "GUANLAN_DL_DAILY must be exactly 0 or 1",
        ),
        (
            "CONSOLE_REVIEW_MODE       = 'enforce'",
            "CONSOLE_REVIEW_MODE       = 'enabled'",
            "CONSOLE_REVIEW_MODE is outside off/monitor/enforce",
        ),
        (
            "GUANLAN_MCP_WRITE         = '1'",
            "GUANLAN_MCP_WRITE         = '0'",
            "GUANLAN_MCP_WRITE must match the user-approved 2026-09-05 profile",
        ),
    ],
)
def test_watchdog_runtime_profile_invalid_value_stops_before_health_loop(
    tmp_path: Path,
    old: str,
    new: str,
    blocker: str,
) -> None:
    result = _validate(_mutated_checker(tmp_path, old, new))

    assert result.returncode == 78
    assert "[runtime-config-blocker]" in result.stderr
    assert blocker in result.stderr
    assert result.stdout == ""


def test_watchdog_runtime_profile_rejects_dependency_drift(tmp_path: Path) -> None:
    path = _mutated_checker(
        tmp_path,
        "GUANLAN_REGEN_DAILY       = '1'",
        "GUANLAN_REGEN_DAILY       = '0'",
    )

    result = _validate(path)

    assert result.returncode == 78
    assert "DL/RERANK daily require GUANLAN_REGEN_DAILY=1" in result.stderr
    assert result.stdout == ""


def test_watchdog_runtime_profile_contains_no_credential_variable_names() -> None:
    source = CHECKER.read_text(encoding="utf-8")
    credential_names = {
        "ANTHROPIC_API_KEY",
        "DASHSCOPE_API_KEY",
        "DEEPSEEK_API_KEY",
        "KIMI_API_KEY",
        "OPENAI_API_KEY",
        "TUSHARE_TOKEN",
        "FA_TUSHARE_TOKEN",
        "HUGGINGFACE_TOKEN",
        "TDX_API_KEY",
        "GUANLAN_MCP_BEARER_TOKEN",
        "GL_PIT_HMAC_KEY_ID",
        "GL_PIT_HMAC_KEY",
        "GL_PIT_HMAC_VERIFY_KEYS",
    }

    assert all(name not in source for name in credential_names)
