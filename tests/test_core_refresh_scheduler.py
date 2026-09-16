from __future__ import annotations

import subprocess
import json
from pathlib import Path

from guanlan_v2.datafeed import core_refresh_scheduler as scheduler


def _xml(
    *, arguments: str | None = None, enabled: str = "true",
    start_boundary: str = "2026-09-04T18:30:00+08:00",
) -> bytes:
    args = arguments or f'"{scheduler.EXPECTED_RUNNER}" --execute'
    text = f'''<?xml version="1.0" encoding="UTF-16"?>
<Task xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <Principals><Principal><LogonType>InteractiveToken</LogonType></Principal></Principals>
  <Settings>
    <ExecutionTimeLimit>PT8H</ExecutionTimeLimit>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <StartWhenAvailable>true</StartWhenAvailable>
    <Enabled>{enabled}</Enabled>
  </Settings>
  <Triggers><CalendarTrigger><StartBoundary>{start_boundary}</StartBoundary><ScheduleByWeek><DaysOfWeek>
    <Monday/><Tuesday/><Wednesday/><Thursday/><Friday/>
  </DaysOfWeek></ScheduleByWeek></CalendarTrigger></Triggers>
  <Actions><Exec>
    <Command>{scheduler.EXPECTED_COMMAND}</Command>
    <Arguments>{args.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')}</Arguments>
    <WorkingDirectory>{scheduler.EXPECTED_WORKDIR}</WorkingDirectory>
  </Exec></Actions>
</Task>'''
    return text.encode("utf-8")


def _run_with(xml: bytes, returncode: int = 0):
    def run(*_args, **_kwargs):
        return subprocess.CompletedProcess([], returncode, stdout=xml, stderr=b"")

    return run


def test_exact_task_is_registered_but_index_not_verified_before_first_run(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(scheduler.os, "name", "nt")
    result = scheduler._probe_uncached(
        run=_run_with(_xml()), state_path=tmp_path / "missing.json",
    )

    assert result["status"] == "fresh"
    assert result["quality"] == "degraded"
    assert result["enabled"] is True and result["direct_python"] is True
    assert result["blockers"] == [] and result["last_run"] is None
    assert result["index_daily"]["status"] == "pending_first_run"


def test_argument_drift_is_explicit_error(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(scheduler.os, "name", "nt")
    result = scheduler._probe_uncached(
        run=_run_with(_xml(arguments='"unsafe.py" --execute')),
        state_path=tmp_path / "missing.json",
    )

    assert result["status"] == "error"
    assert result["enabled"] is False
    assert result["blockers"] == ["closed_arguments"]


def test_disabled_or_intraday_task_is_explicit_drift(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(scheduler.os, "name", "nt")
    result = scheduler._probe_uncached(
        run=_run_with(_xml(enabled="false", start_boundary="2026-09-04T10:00:00+08:00")),
        state_path=tmp_path / "missing.json",
    )

    assert result["status"] == "error"
    assert result["enabled"] is False
    assert result["blockers"] == ["task_enabled", "after_close_trigger"]


def test_missing_task_is_not_projected_as_healthy(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(scheduler.os, "name", "nt")
    result = scheduler._probe_uncached(
        run=_run_with(b"", returncode=1), state_path=tmp_path / "missing.json",
    )

    assert result["status"] == "missing"
    assert result["enabled"] is False


def test_failed_last_receipt_keeps_task_but_marks_error(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(scheduler.os, "name", "nt")
    state = tmp_path / "latest.json"
    state.write_text(
        '{"schema_version":"core-data-refresh/v1","status":"failed",'
        '"returncode":2,"timed_out":false}',
        encoding="utf-8",
    )
    result = scheduler._probe_uncached(run=_run_with(_xml()), state_path=state)

    assert result["status"] == "error"
    assert result["enabled"] is True
    assert result["last_run"]["returncode"] == 2


def test_old_success_does_not_claim_new_index_stage_has_run(tmp_path, monkeypatch):
    monkeypatch.setattr(scheduler.os, "name", "nt")
    state = tmp_path / "latest.json"
    state.write_text(json.dumps({"schema_version": "core-data-refresh/v1",
                                 "status": "completed", "returncode": 0}), encoding="utf-8")
    result = scheduler._probe_uncached(run=_run_with(_xml()), state_path=state)
    assert result["enabled"] is True and result["quality"] == "degraded"
    assert result["index_daily"]["status"] == "pending_first_run"


def test_index_receipt_is_projected_without_provider_extras(tmp_path, monkeypatch):
    monkeypatch.setattr(scheduler.os, "name", "nt")
    state = tmp_path / "latest.json"
    state.write_text(json.dumps({"schema_version": "core-data-refresh/v1",
                                 "status": "completed", "returncode": 0,
                                 "index_daily": {"status": "complete", "code": "399300.SZ",
                                                 "new_latest": "20260908", "applied": True,
                                                 "provider_secret": "not-for-health"}}), encoding="utf-8")
    result = scheduler._probe_uncached(run=_run_with(_xml()), state_path=state)
    assert result["quality"] == "complete" and result["index_daily"]["new_latest"] == "20260908"
    assert "not-for-health" not in json.dumps(result)


def test_index_partial_failure_does_not_mark_whole_schedule_green(tmp_path, monkeypatch):
    monkeypatch.setattr(scheduler.os, "name", "nt")
    state = tmp_path / "latest.json"
    state.write_text(json.dumps({"schema_version": "core-data-refresh/v1",
                                 "status": "partial_failed", "core_status": "completed",
                                 "returncode": 2, "index_daily": {"status": "failed"}}), encoding="utf-8")
    result = scheduler._probe_uncached(run=_run_with(_xml()), state_path=state)
    assert result["enabled"] is True and result["status"] == "error"
    assert result["last_run"]["core_status"] == "completed"
