from __future__ import annotations

import json
import subprocess
from pathlib import Path

from scripts.run_scheduled_core_data_refresh import SOURCE_IDS, build_command, run
from scripts import run_scheduled_core_data_refresh as runner_module


def test_command_is_closed_direct_python_chain(tmp_path: Path) -> None:
    command = build_command(python=Path("python.exe"), stocks_root=tmp_path)

    assert command == [
        "python.exe",
        str(tmp_path / "scripts" / "run_data_block_update.py"),
        "--block",
        "auto_refresh",
        "--sources",
        "cn_day,cn_daily_basic,fincast_daily_pred,pit_store",
    ]
    assert SOURCE_IDS == (
        "cn_day", "cn_daily_basic", "fincast_daily_pred", "pit_store",
    )


def test_probe_is_read_only_and_reports_missing_runtime(tmp_path: Path, capsys) -> None:
    state = tmp_path / "state"
    result = run(
        python=tmp_path / "missing-python.exe",
        stocks_root=tmp_path / "stocks",
        state_dir=state,
        execute=False,
    )

    payload = json.loads(capsys.readouterr().out)
    assert result == 2
    assert payload["status"] == "blocked"
    assert payload["blocker"] == "runtime_path_missing"
    assert payload["shell_used"] is False
    assert not state.exists()


def test_probe_validates_paths_without_running_or_writing(tmp_path: Path, capsys) -> None:
    python = tmp_path / "python.exe"
    runner = tmp_path / "stocks" / "scripts" / "run_data_block_update.py"
    python.write_bytes(b"")
    runner.parent.mkdir(parents=True)
    runner.write_text("raise AssertionError('must not run')\n", encoding="utf-8")
    state = tmp_path / "state"

    result = run(
        python=python,
        stocks_root=runner.parents[1],
        state_dir=state,
        execute=False,
    )

    payload = json.loads(capsys.readouterr().out)
    assert result == 0 and payload["status"] == "ready"
    assert payload["source_ids"] == list(SOURCE_IDS)
    assert payload["shell_used"] is False
    assert not state.exists()


def test_installer_uses_direct_python_and_safe_task_settings() -> None:
    text = (
        Path(__file__).resolve().parents[1]
        / "scripts"
        / "install_core_data_refresh_task.ps1"
    ).read_text(encoding="utf-8")

    assert "New-ScheduledTaskAction" in text
    assert "-Execute $Python" in text
    assert "powershell.exe" not in text.lower().split("[cmdletbinding()]", 1)[1]
    assert "-MultipleInstances IgnoreNew" in text
    assert "-StartWhenAvailable" in text
    assert "-LogonType Interactive" in text


def _runtime(tmp_path):
    python = tmp_path / "python.exe"
    python.write_bytes(b"")
    stocks = tmp_path / "stocks"
    script = stocks / "scripts/run_data_block_update.py"
    script.parent.mkdir(parents=True)
    script.write_text("", encoding="utf-8")
    return python, stocks, tmp_path / "state"


def test_success_runs_bounded_index_append_after_stocks(tmp_path, monkeypatch):
    python, stocks, state = _runtime(tmp_path)
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        if "--receipt-path" in command:
            path = Path(command[command.index("--receipt-path") + 1])
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps({"schema_version": "csi300-append-refresh/v1",
                                        "code": "399300.SZ", "status": "complete",
                                        "new_latest": "20260908", "expected_session": "20260908",
                                        "appended_rows": 1, "applied": True}), encoding="utf-8")
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(runner_module.subprocess, "run", fake_run)
    assert run(python=python, stocks_root=stocks, state_dir=state, execute=True) == 0
    receipt = json.loads((state / "latest.json").read_text())
    assert receipt["status"] == "completed" and receipt["core_status"] == "completed"
    assert receipt["index_daily"]["appended_rows"] == 1
    assert len(calls) == 2 and calls[0][1]["cwd"] == str(stocks)
    assert calls[1][0][1:3] == ["-m", "guanlan_v2.datafeed.index_refresh"]
    assert calls[1][1]["cwd"] == str(runner_module.ROOT)
    assert 0 < calls[1][1]["timeout"] <= 90
    assert "shell" not in calls[1][1]


def test_core_failure_does_not_start_index_append(tmp_path, monkeypatch):
    python, stocks, state = _runtime(tmp_path)
    calls = []

    def fake_run(command, **_):
        calls.append(command)
        return subprocess.CompletedProcess(command, 2)

    monkeypatch.setattr(runner_module.subprocess, "run", fake_run)
    assert run(python=python, stocks_root=stocks, state_dir=state, execute=True) == 2
    receipt = json.loads((state / "latest.json").read_text())
    assert len(calls) == 1 and receipt["status"] == "failed"
    assert receipt["index_daily"]["status"] == "not_run"


def test_missing_index_receipt_is_partial_failure_even_with_zero_exit(tmp_path, monkeypatch):
    python, stocks, state = _runtime(tmp_path)
    monkeypatch.setattr(runner_module.subprocess, "run",
                        lambda command, **_: subprocess.CompletedProcess(command, 0))
    assert run(python=python, stocks_root=stocks, state_dir=state, execute=True) == 2
    receipt = json.loads((state / "latest.json").read_text())
    assert receipt["status"] == "partial_failed" and receipt["core_status"] == "completed"
    assert receipt["index_daily"]["blocker"] == "index_receipt_missing_or_invalid"


def test_index_timeout_keeps_completed_core_visible(tmp_path, monkeypatch):
    python, stocks, state = _runtime(tmp_path)

    def fake_run(command, **kwargs):
        if "--receipt-path" in command:
            raise subprocess.TimeoutExpired(command, kwargs["timeout"])
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(runner_module.subprocess, "run", fake_run)
    assert run(python=python, stocks_root=stocks, state_dir=state, execute=True) == 124
    receipt = json.loads((state / "latest.json").read_text())
    assert receipt["status"] == "partial_failed" and receipt["timed_out"] is True
    assert receipt["index_daily"]["blocker"] == "index_refresh_timeout"
