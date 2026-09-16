from __future__ import annotations

import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from typer.testing import CliRunner

from financial_analyst.data.pit_preflight import (
    NTP_CHECKED_AT_ENV,
    NTP_MAX_AGE_SECONDS_ENV,
    NTP_SOURCE_ENV,
    NTP_STATUS_ENV,
    deployment_preflight,
)
from financial_analyst.data.trusted_receipts import (
    KEY_ENV,
    KEY_ID_ENV,
    VERIFY_KEYS_ENV,
    inspect_key_configuration,
)


_HK = ZoneInfo("Asia/Hong_Kong")
_NOW = datetime(2026, 9, 3, 12, 0, tzinfo=_HK)
_ACTIVE_SECRET = "active-test-secret-material-that-is-at-least-32-bytes"
_OLD_SECRET = "retired-test-secret-material-that-is-at-least-32-bytes"


def _ready_env() -> dict[str, str]:
    return {
        KEY_ID_ENV: "pit-prod-2026-01",
        KEY_ENV: _ACTIVE_SECRET,
        VERIFY_KEYS_ENV: json.dumps({"pit-prod-2025-12": _OLD_SECRET}),
        NTP_STATUS_ENV: "synchronized",
        NTP_SOURCE_ENV: "w32time",
        NTP_CHECKED_AT_ENV: "2026-09-03T11:55:00+08:00",
        NTP_MAX_AGE_SECONDS_ENV: "86400",
    }


def test_missing_configuration_is_stably_blocked_and_fail_closed():
    first = deployment_preflight({}, now=_NOW)
    second = deployment_preflight({}, now=_NOW)

    assert first == second
    assert first["status"] == "blocked" and first["ready"] is False
    assert first["fail_closed"] is True
    assert first["blockers"] == [
        "pit_active_key_id_missing",
        "pit_active_key_missing",
        "pit_verify_keyring_missing",
        "pit_ntp_status_missing",
        "pit_ntp_source_missing",
        "pit_ntp_checked_at_missing",
    ]
    assert first["checks"]["hong_kong_clock"] == {
        "status": "pass", "aware": True, "timezone": "Asia/Hong_Kong",
    }


def test_complete_configuration_is_ready_without_returning_secret_material():
    env = _ready_env()
    before = dict(env)
    result = deployment_preflight(env, now=_NOW)
    rendered = json.dumps(result, ensure_ascii=False)

    assert result["status"] == "ready" and result["ready"] is True
    assert result["active_key_id"] == "pit-prod-2026-01"
    assert result["verify_key_count"] == 1
    assert result["checks"]["active_key"]["minimum_bytes"] == 32
    assert result["checks"]["ntp_attestation"]["age_seconds"] == 300
    assert _ACTIVE_SECRET not in rendered and _OLD_SECRET not in rendered
    assert "w32time" not in rendered  # source presence is reported, not its value
    assert env == before  # pure/read-only: no environment mutation


def test_empty_explicit_rotation_keyring_is_valid_but_missing_is_not():
    env = _ready_env()
    env[VERIFY_KEYS_ENV] = "{}"
    result = deployment_preflight(env, now=_NOW)
    assert result["ready"] is True and result["verify_key_count"] == 0

    env.pop(VERIFY_KEYS_ENV)
    result = deployment_preflight(env, now=_NOW)
    assert result["ready"] is False
    assert result["blockers"] == ["pit_verify_keyring_missing"]


def test_keyring_rejects_non_object_duplicate_invalid_id_and_short_key():
    cases = [
        ("null", "pit_verify_keyring_not_object"),
        ('{"old":"%s","old":"%s"}' % (_OLD_SECRET, _OLD_SECRET),
         "pit_verify_keyring_invalid_json"),
        (json.dumps({"bad id": _OLD_SECRET}), "pit_verify_keyring_invalid_key_id"),
        (json.dumps({"old": "short"}), "pit_verify_keyring_invalid_key"),
    ]
    for raw, blocker in cases:
        env = _ready_env()
        env[VERIFY_KEYS_ENV] = raw
        result = inspect_key_configuration(env)
        assert result["ready"] is False and blocker in result["blockers"], raw


def test_active_key_accepts_decoded_32_bytes_and_rejects_short_material():
    env = _ready_env()
    env[KEY_ENV] = "hex:" + ("ab" * 32)
    assert inspect_key_configuration(env)["checks"]["active_key"]["status"] == "pass"

    env[KEY_ENV] = "base64:" + ("YQ==")  # one decoded byte
    result = inspect_key_configuration(env)
    assert result["checks"]["active_key"]["status"] == "invalid"
    assert result["blockers"] == ["pit_active_key_invalid"]


def test_ntp_attestation_stale_future_naive_and_bad_max_age_fail_closed():
    cases = [
        ("2026-09-01T11:55:00+08:00", None, "pit_ntp_attestation_stale"),
        ("2026-09-03T13:00:00+08:00", None, "pit_ntp_checked_at_future"),
        ("2026-09-03T11:55:00", None, "pit_ntp_checked_at_invalid"),
        ("2026-09-03T11:55:00+08:00", "9", "pit_ntp_max_age_invalid"),
    ]
    for checked_at, max_age, blocker in cases:
        env = _ready_env()
        env[NTP_CHECKED_AT_ENV] = checked_at
        if max_age is not None:
            env[NTP_MAX_AGE_SECONDS_ENV] = max_age
        result = deployment_preflight(env, now=_NOW)
        assert result["ready"] is False and blocker in result["blockers"]


def test_non_hong_kong_or_naive_application_clock_is_blocked():
    for now in (
        datetime(2026, 9, 3, 4, 0),
        datetime(2026, 9, 3, 4, 0, tzinfo=ZoneInfo("UTC")),
    ):
        result = deployment_preflight(_ready_env(), now=now)
        assert result["ready"] is False
        assert "pit_hong_kong_clock_invalid" in result["blockers"]


def test_internal_probe_errors_return_secret_free_blocked_status(monkeypatch):
    import financial_analyst.data.pit_preflight as preflight

    monkeypatch.setattr(
        preflight, "inspect_key_configuration",
        lambda _env: (_ for _ in ()).throw(RuntimeError(_ACTIVE_SECRET)),
    )
    monkeypatch.setattr(
        preflight, "_clock_checks",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError(_OLD_SECRET)),
    )
    result = preflight.deployment_preflight(_ready_env(), now=_NOW)
    rendered = json.dumps(result)
    assert result["ready"] is False and result["status"] == "blocked"
    assert result["blockers"] == [
        "pit_key_preflight_error", "pit_clock_preflight_error",
    ]
    assert _ACTIVE_SECRET not in rendered and _OLD_SECRET not in rendered


def test_health_projection_is_registered_ops_only_and_secret_free(monkeypatch):
    import guanlan_v2.datafeed.health as health

    env = _ready_env()
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(
        "financial_analyst.data.pit_preflight.hong_kong_now", lambda: _NOW,
    )
    item = health._item_pit_trust()
    rendered = json.dumps(item, ensure_ascii=False)
    assert item["status"] == "fresh" and item["ready"] is True
    assert "pit_trust" in health._ITEMS and "pit_trust" in health._OPS_ITEMS
    assert _ACTIVE_SECRET not in rendered and _OLD_SECRET not in rendered


def test_health_projection_reports_stale_attestation_without_secret(monkeypatch):
    import guanlan_v2.datafeed.health as health

    env = _ready_env()
    env[NTP_CHECKED_AT_ENV] = (_NOW - timedelta(days=2)).isoformat()
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(
        "financial_analyst.data.pit_preflight.hong_kong_now", lambda: _NOW,
    )
    item = health._item_pit_trust()
    assert item["status"] == "stale" and item["ready"] is False
    assert item["blockers"] == ["pit_ntp_attestation_stale"]
    assert _ACTIVE_SECRET not in json.dumps(item)


def test_cli_returns_zero_when_ready_and_two_when_blocked(monkeypatch):
    from financial_analyst.cli import app

    runner = CliRunner()
    for key in _ready_env():
        monkeypatch.delenv(key, raising=False)
    blocked = runner.invoke(app, ["pit-preflight"])
    assert blocked.exit_code == 2
    assert json.loads(blocked.stdout)["ready"] is False

    env = _ready_env()
    checked_at = datetime.now(_HK).isoformat(timespec="seconds")
    env[NTP_CHECKED_AT_ENV] = checked_at
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    ready = runner.invoke(app, ["pit-preflight"])
    assert ready.exit_code == 0, ready.stdout
    result = json.loads(ready.stdout)
    assert result["ready"] is True
    assert _ACTIVE_SECRET not in ready.stdout and _OLD_SECRET not in ready.stdout
