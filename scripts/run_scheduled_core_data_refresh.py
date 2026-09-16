"""Run the reviewed after-close core data chain with durable, value-free state.

Stock writes remain delegated to the established Stocks
``run_data_block_update.py`` entry point. After that chain succeeds, the
bounded CSI 300 appender fills only its existing benchmark tail. Both stages
share a single-flight lock, an eight-hour ceiling and explicit receipts.

Without ``--execute`` the command is a read-only plan/probe.  Windows Task
Scheduler should launch this file directly with Python (never through
PowerShell/cmd on this host).
"""
from __future__ import annotations

import argparse
import contextlib
import json
import os
import subprocess
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Iterator, Sequence
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_STOCKS_ROOT = Path("G:/stocks")
DEFAULT_PYTHON = Path("D:/app/miniconda/envs/stocks/python.exe")
DEFAULT_STATE_DIR = ROOT / "var" / "core-data-refresh"
SOURCE_IDS = (
    "cn_day",
    "cn_daily_basic",
    "fincast_daily_pred",
    "pit_store",
)
TIMEOUT_SECONDS = 8 * 60 * 60
HK = ZoneInfo("Asia/Hong_Kong")


def build_command(*, python: Path, stocks_root: Path) -> list[str]:
    """Return the closed production command; no caller-supplied shell text."""

    return [
        str(python),
        str(stocks_root / "scripts" / "run_data_block_update.py"),
        "--block",
        "auto_refresh",
        "--sources",
        ",".join(SOURCE_IDS),
    ]


def build_index_command(*, python: Path, stocks_root: Path, receipt_path: Path) -> list[str]:
    return [str(python), "-m", "guanlan_v2.datafeed.index_refresh", "--stocks-root",
            str(stocks_root), "--receipt-path", str(receipt_path), "--execute"]


def _write_json_atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    try:
        temp.write_text(
            json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


@contextlib.contextmanager
def _single_flight(path: Path) -> Iterator[bool]:
    """Hold a one-byte non-blocking OS lock for this wrapper invocation."""

    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+b")
    try:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:  # pragma: no cover - production host is Windows
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (OSError, BlockingIOError):
            yield False
            return
        try:
            yield True
        finally:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:  # pragma: no cover
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    finally:
        handle.close()


def _public_plan(*, python: Path, stocks_root: Path, state_dir: Path) -> dict:
    command = build_command(python=python, stocks_root=stocks_root)
    return {
        "schema_version": "core-data-refresh/v1",
        "mode": "after_close_weekday",
        "python_exists": python.is_file(),
        "runner_exists": Path(command[1]).is_file(),
        "source_ids": list(SOURCE_IDS),
        "timeout_seconds": TIMEOUT_SECONDS,
        "state_path": str(state_dir / "latest.json"),
        "shell_used": False,
        "index_daily": {"code": "399300.SZ", "status": "planned", "mode": "append_only"},
    }


def run(
    *,
    python: Path,
    stocks_root: Path,
    state_dir: Path,
    execute: bool,
    timeout_seconds: int = TIMEOUT_SECONDS,
) -> int:
    """Validate or execute the production chain and persist a safe receipt."""

    plan = _public_plan(python=python, stocks_root=stocks_root, state_dir=state_dir)
    if not plan["python_exists"] or not plan["runner_exists"]:
        plan.update({"status": "blocked", "blocker": "runtime_path_missing"})
        print(json.dumps(plan, ensure_ascii=False, sort_keys=True))
        return 2
    if not execute:
        plan["status"] = "ready"
        print(json.dumps(plan, ensure_ascii=False, sort_keys=True))
        return 0

    command = build_command(python=python, stocks_root=stocks_root)
    state_path = state_dir / "latest.json"
    lock_path = state_dir / "scheduler.lock"
    started = datetime.now(HK)
    with _single_flight(lock_path) as acquired:
        if not acquired:
            receipt = {
                **plan,
                "status": "skipped_busy",
                "started_at": started.isoformat(timespec="seconds"),
            }
            _write_json_atomic(state_path, receipt)
            print(json.dumps(receipt, ensure_ascii=False, sort_keys=True))
            return 75

        state_dir.mkdir(parents=True, exist_ok=True)
        log_name = started.strftime("refresh-%Y%m%dT%H%M%S%z.log")
        log_path = state_dir / log_name
        running = {
            **plan,
            "status": "running",
            "started_at": started.isoformat(timespec="seconds"),
            "log_name": log_name,
        }
        _write_json_atomic(state_path, running)
        env = {
            **os.environ,
            "PYTHONIOENCODING": "utf-8",
            "NO_PROXY": "*",
            "no_proxy": "*",
        }
        timed_out = False
        returncode = 1
        core_completed = False
        index_result = {"code": "399300.SZ", "status": "not_run"}
        deadline = time.monotonic() + max(60, int(timeout_seconds))
        with log_path.open("w", encoding="utf-8", errors="replace") as log:
            log.write(
                "# Guanlan core data refresh\n"
                f"# started_at={running['started_at']}\n"
                f"# sources={','.join(SOURCE_IDS)}\n\n"
            )
            log.flush()
            try:
                completed = subprocess.run(
                    command,
                    cwd=str(stocks_root),
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    env=env,
                    timeout=max(60, int(timeout_seconds)),
                    check=False,
                )
                returncode = int(completed.returncode)
                core_completed = returncode == 0
                if core_completed:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise subprocess.TimeoutExpired("core-data-refresh", timeout_seconds)
                    index_path = state_dir / "index_daily" / (
                        log_name.removesuffix(".log") + "-" + uuid.uuid4().hex[:8] + ".json")
                    index_result = {"code": "399300.SZ", "status": "running"}
                    index_completed = subprocess.run(
                        build_index_command(python=python, stocks_root=stocks_root,
                                            receipt_path=index_path),
                        cwd=str(ROOT), stdout=log, stderr=subprocess.STDOUT, env=env,
                        timeout=min(90, remaining), check=False,
                    )
                    try:
                        detail = json.loads(index_path.read_text(encoding="utf-8"))
                    except (OSError, json.JSONDecodeError):
                        detail = {}
                    if not isinstance(detail, dict):
                        detail = {}
                    valid = (detail.get("schema_version") == "csi300-append-refresh/v1"
                             and detail.get("code") == "399300.SZ")
                    index_result = {
                        "code": "399300.SZ", "status": detail.get("status") if valid else "failed",
                        "blocker": detail.get("blocker") if valid else "index_receipt_missing_or_invalid",
                        "expected_session": detail.get("expected_session") if valid else None,
                        "new_latest": detail.get("new_latest") if valid else None,
                        "appended_rows": detail.get("appended_rows") if valid else None,
                        "applied": bool(detail.get("applied")) if valid else False,
                        "receipt_path": str(index_path),
                    }
                    if index_completed.returncode != 0 or index_result["status"] != "complete":
                        returncode = int(index_completed.returncode) or 2
            except subprocess.TimeoutExpired:
                timed_out = True
                returncode = 124
                if core_completed:
                    index_result.update(status="failed", blocker="index_refresh_timeout")
                log.write(f"\n# timeout after {timeout_seconds}s\n")
            except OSError:
                returncode = 2
                if core_completed:
                    index_result.update(status="failed", blocker="index_process_start_failed")
                log.write("\n# producer process could not be started\n")

        ended = datetime.now(HK)
        receipt = {
            **running,
            "status": ("completed" if returncode == 0 else
                       "partial_failed" if core_completed else "failed"),
            "ended_at": ended.isoformat(timespec="seconds"),
            "elapsed_seconds": round((ended - started).total_seconds(), 3),
            "returncode": returncode,
            "timed_out": timed_out,
            "core_status": "completed" if core_completed else "failed",
            "index_daily": index_result,
        }
        _write_json_atomic(state_path, receipt)
        print(json.dumps(receipt, ensure_ascii=False, sort_keys=True))
        return returncode


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--python", type=Path, default=DEFAULT_PYTHON)
    parser.add_argument("--stocks-root", type=Path, default=DEFAULT_STOCKS_ROOT)
    parser.add_argument("--state-dir", type=Path, default=DEFAULT_STATE_DIR)
    args = parser.parse_args(argv)
    return run(
        python=args.python,
        stocks_root=args.stocks_root,
        state_dir=args.state_dir,
        execute=args.execute,
    )


if __name__ == "__main__":
    raise SystemExit(main())
