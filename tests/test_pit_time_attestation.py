from __future__ import annotations

import asyncio
import json
import subprocess
import threading
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from financial_analyst.data.pit_preflight import deployment_preflight
from guanlan_v2.pit_time_attestation import (
    SCHEMA_VERSION,
    apply_process_time_attestation,
    maintain_process_time_attestation,
    parse_windows_time_status,
    probe_windows_time,
)


HK = ZoneInfo("Asia/Hong_Kong")
NOW = datetime(2026, 9, 4, 19, 5, tzinfo=HK)
NTP_NAMES = {
    "GL_PIT_NTP_STATUS",
    "GL_PIT_NTP_SOURCE",
    "GL_PIT_NTP_CHECKED_AT",
    "GL_PIT_NTP_MAX_AGE_SECONDS",
}
KEY_ENV = {
    "GL_PIT_HMAC_KEY_ID": "pit-prod-test",
    "GL_PIT_HMAC_KEY": "test-secret-material-that-is-at-least-32-bytes",
    "GL_PIT_HMAC_VERIFY_KEYS": json.dumps({}),
}


def _status(*, leap: int = 0, stratum: int = 5, last_sync: str = "2026/9/4 19:03:43", source: str = "time.windows.com,0x9") -> str:
    # Labels intentionally do not matter; this also covers localized w32tm.
    return "\n".join([
        f"localized leap: {leap} (ok)",
        f"localized stratum: {stratum} (secondary)",
        "precision: -23",
        "root delay: 0.1s",
        "root dispersion: 1.0s",
        "reference id: 123",
        f"last successful sync: {last_sync}",
        f"source: {source}",
        "poll interval: 10",
        "extra stable status field: 1",
    ])


def _ready_attestation(at: datetime = NOW) -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "ready",
        "ready": True,
        "checked_at": at.isoformat(timespec="seconds"),
        "source": "w32time-os-status",
        "last_sync_age_seconds": 0,
        "blockers": [],
    }


def test_parse_ready_status_is_short_lived_and_source_value_free() -> None:
    # Put the standard trailing triplet back at the end; labels are arbitrary.
    lines = _status().splitlines()
    lines = lines[:6] + [lines[6], lines[7], lines[8], "poll detail: 2048s"]
    result = parse_windows_time_status("\n".join(lines), now=NOW)

    assert result["ready"] is True
    assert result["source"] == "w32time-os-status"
    assert result["checked_at"] == "2026-09-04T19:05:00+08:00"
    assert result["last_sync_age_seconds"] == 77
    assert "time.windows.com" not in str(result)


def test_parse_refuses_unsynchronized_stale_and_local_clock() -> None:
    base = [
        "leap: {leap}", "stratum: {stratum}", "precision: -23",
        "delay: 0", "dispersion: 0", "reference: 1",
        "sync: {sync}", "source: {source}", "poll: 10", "interval: 2048",
    ]
    cases = [
        (3, 5, "2026/9/4 19:03:43", "time.windows.com", "pit_w32tm_unsynchronized"),
        (0, 5, "2026/9/2 19:03:43", "time.windows.com", "pit_w32tm_last_sync_stale"),
        (0, 5, "2026/9/4 19:03:43", "Local CMOS Clock", "pit_w32tm_source_untrusted"),
    ]
    for leap, stratum, sync, source, blocker in cases:
        text = "\n".join(line.format(
            leap=leap, stratum=stratum, sync=sync, source=source,
        ) for line in base)
        result = parse_windows_time_status(text, now=NOW)
        assert result["ready"] is False and result["blockers"] == [blocker]


def test_probe_never_projects_command_errors() -> None:
    secret = "host-detail-must-not-leak"

    def failed(*_args, **_kwargs):
        return subprocess.CompletedProcess([], 5, stdout="", stderr=secret)

    result = probe_windows_time(run=failed, now=NOW, system="Windows")
    assert result["blockers"] == ["pit_w32tm_probe_failed"]
    assert secret not in str(result)

    result = probe_windows_time(run=failed, now=NOW, system="Linux")
    assert result["blockers"] == ["pit_w32tm_unavailable"]


def test_probe_uses_absolute_system32_binary_and_a_secret_free_environment() -> None:
    secret = "must-not-reach-w32tm"
    captured: dict = {}

    def succeeded(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        return subprocess.CompletedProcess(command, 0, stdout=_status(), stderr="")

    result = probe_windows_time(
        run=succeeded,
        now=NOW,
        system="Windows",
        environ={
            "SystemRoot": r"C:\Windows",
            "GL_PIT_HMAC_KEY": secret,
            "OPENAI_API_KEY": secret,
            "DEEPSEEK_API_KEY": secret,
        },
    )

    assert result["ready"] is True
    assert captured["command"] == [
        r"C:\Windows\System32\w32tm.exe", "/query", "/status",
    ]
    assert captured["kwargs"]["timeout"] == 15
    assert captured["kwargs"]["env"] == {
        "SystemRoot": r"C:\Windows",
        "WINDIR": r"C:\Windows",
        "PATH": r"C:\Windows\System32",
        "SystemDrive": "C:",
    }
    assert secret not in repr(captured)


def test_failed_refresh_revokes_the_entire_old_lease_without_touching_secrets() -> None:
    env = dict(KEY_ENV)
    env.update({
        "GL_PIT_NTP_STATUS": "synchronized",
        "GL_PIT_NTP_SOURCE": "old-source",
        "GL_PIT_NTP_CHECKED_AT": NOW.isoformat(),
        "GL_PIT_NTP_MAX_AGE_SECONDS": "86400",
        "UNRELATED_RUNTIME_VALUE": "keep-me",
    })

    assert apply_process_time_attestation(
        {"schema_version": SCHEMA_VERSION, "ready": False,
         "status": "blocked", "blockers": ["pit_w32tm_probe_failed"]},
        environ=env,
    ) is False
    assert NTP_NAMES.isdisjoint(env)
    assert env["UNRELATED_RUNTIME_VALUE"] == "keep-me"
    assert env["GL_PIT_HMAC_KEY"] == KEY_ENV["GL_PIT_HMAC_KEY"]


def test_status_commit_sentinel_prevents_partial_refresh_from_looking_ready() -> None:
    class RecordingEnvironment(dict):
        def __init__(self, values):
            super().__init__(values)
            self.snapshots: list[dict] = []

        def pop(self, key, default=None):
            value = super().pop(key, default)
            self.snapshots.append(dict(self))
            return value

        def __setitem__(self, key, value):
            super().__setitem__(key, value)
            self.snapshots.append(dict(self))

    env = RecordingEnvironment({
        **KEY_ENV,
        "GL_PIT_NTP_STATUS": "synchronized",
        "GL_PIT_NTP_SOURCE": "old-source",
        "GL_PIT_NTP_CHECKED_AT": (NOW - timedelta(minutes=5)).isoformat(),
        "GL_PIT_NTP_MAX_AGE_SECONDS": "86400",
    })

    assert apply_process_time_attestation(_ready_attestation(), environ=env) is True
    assert len(env.snapshots) == 5
    assert all(
        deployment_preflight(snapshot, now=NOW)["ready"] is False
        for snapshot in env.snapshots[:-1]
    )
    assert deployment_preflight(env.snapshots[-1], now=NOW)["ready"] is True
    assert env.snapshots[-1]["GL_PIT_NTP_STATUS"] == "synchronized"


def test_live_refresh_keeps_a_server_ready_beyond_its_original_24_hour_lease() -> None:
    later = NOW + timedelta(hours=25)
    env = dict(KEY_ENV)
    assert apply_process_time_attestation(_ready_attestation(NOW), environ=env)
    assert deployment_preflight(env, now=later)["blockers"] == [
        "pit_ntp_attestation_stale",
    ]

    assert apply_process_time_attestation(_ready_attestation(later), environ=env)
    refreshed = deployment_preflight(env, now=later)
    assert refreshed["ready"] is True
    assert refreshed["checks"]["ntp_attestation"]["age_seconds"] == 0


def test_maintainer_probes_immediately_off_loop_and_installs_the_lease() -> None:
    called = threading.Event()
    env = dict(KEY_ENV)

    def probe() -> dict:
        called.set()
        return _ready_attestation()

    async def scenario() -> None:
        task = asyncio.create_task(maintain_process_time_attestation(
            environ=env, probe=probe, refresh_seconds=3600,
        ))
        assert await asyncio.to_thread(called.wait, 2)
        for _ in range(100):
            if env.get("GL_PIT_NTP_STATUS") == "synchronized":
                break
            await asyncio.sleep(0.01)
        assert deployment_preflight(env, now=NOW)["ready"] is True
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(scenario())
