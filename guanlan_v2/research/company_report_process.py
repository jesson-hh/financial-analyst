"""Durable subprocess owner for one confirmed company-research report.

The Console ASGI process must be restartable while a 5--15 minute legacy
report is running.  A thread that calls ``subprocess.run`` cannot provide that
property: after a Console restart the child may still be alive, but no process
owns a durable completion receipt.  This tiny helper is that owner.  It is
started with service-generated paths/ids, launches exactly one legacy report,
and atomically writes one closed result record before exiting.

It performs no confirmation and no workspace finalization.  Confirmation and
binding remain Console responsibilities; the strict finalizer is only resumed
by Console after this receipt is verified.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


RESULT_SCHEMA = "company-report-process-result@2"
_BG_ID_RE = re.compile(r"^bg_[0-9a-f]{10}$")
_CODE_RE = re.compile(r"^(?:SH|SZ|BJ)[0-9]{6}$")
_SHA_RE = re.compile(r"^[0-9a-f]{64}$")
_MAX_CAPTURE_CHARS = 16_000
_MTIME_SLOP_SECONDS = 1.0


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(
        value, ensure_ascii=False, sort_keys=True, indent=1, allow_nan=False,
    )
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(data, encoding="utf-8")
    tmp.replace(path)


def _freshest_report(out_dir: Path, code: str, after_epoch: float) -> Path | None:
    rows: list[tuple[float, Path]] = []
    for path in out_dir.glob(f"{code}_*.md"):
        try:
            if not path.is_file():
                continue
            mtime = path.stat().st_mtime
        except OSError:
            continue
        # NTFS/Python timestamps observed in production can round a just-written
        # file a fraction of a millisecond behind ``time.time()``.  The output
        # directory is private to this job, so a one-second granularity allowance
        # preserves the freshness gate without reintroducing cross-job guessing.
        if mtime >= after_epoch - _MTIME_SLOP_SECONDS:
            rows.append((mtime, path.resolve()))
    if len(rows) > 1:
        raise RuntimeError(
            "report process produced multiple fresh markdown artifacts; "
            "refusing to guess which one belongs to the job"
        )
    return rows[0][1] if rows else None


def run_report_process(
    *, job_id: str, code: str, asof: str | None, evidence_pack: Path,
    result_path: Path, out_dir: Path, request_sha256: str,
    timeout_seconds: int = 900,
) -> dict[str, Any]:
    """Run one report and persist a terminal, content-addressed receipt."""
    if _BG_ID_RE.fullmatch(job_id) is None:
        raise ValueError("invalid company report job id")
    if _CODE_RE.fullmatch(code) is None:
        raise ValueError("invalid A-share code")
    if _SHA_RE.fullmatch(request_sha256) is None:
        raise ValueError("invalid request digest")
    if not evidence_pack.is_file():
        raise ValueError("evidence pack is absent")
    evidence_sha256 = hashlib.sha256(evidence_pack.read_bytes()).hexdigest()
    resolved_out_dir = out_dir.resolve()
    resolved_out_dir.mkdir(parents=True, exist_ok=True)
    out_dir_sha256 = hashlib.sha256(
        os.path.normcase(str(resolved_out_dir)).encode("utf-8")
    ).hexdigest()
    started_epoch = time.time()
    started_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(started_epoch))
    base: dict[str, Any] = {
        "schema_version": RESULT_SCHEMA,
        "job_id": job_id,
        "code": code,
        "asof": asof,
        "request_sha256": request_sha256,
        "evidence_pack_sha256": evidence_sha256,
        "report_out_dir_sha256": out_dir_sha256,
        "started_epoch": started_epoch,
        "started_at": started_at,
    }
    env = dict(os.environ)
    env["FA_EVIDENCE_PACK"] = str(evidence_pack.resolve())
    command = [
        "financial-analyst", "report", code,
        "--out-dir", str(resolved_out_dir),
    ]
    if asof:
        command.extend(["--asof", asof])
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_seconds,
            cwd=os.getcwd(),
            env=env,
        )
        stderr_tail = (completed.stderr or "")[-_MAX_CAPTURE_CHARS:]
        stdout_tail = (completed.stdout or "")[-_MAX_CAPTURE_CHARS:]
        report = (
            _freshest_report(resolved_out_dir, code, started_epoch)
            if completed.returncode == 0 else None
        )
        if completed.returncode == 0 and report is not None:
            body = report.read_bytes()
            result = {
                **base,
                "ok": True,
                "returncode": 0,
                "report_path": str(report),
                "report_sha256": hashlib.sha256(body).hexdigest(),
                "report_bytes": len(body),
                "stdout_tail": stdout_tail,
                "stderr_tail": stderr_tail,
            }
        else:
            reason = (
                f"legacy report exited {completed.returncode}"
                if completed.returncode != 0
                else "legacy report exited 0 without a fresh markdown artifact"
            )
            result = {
                **base,
                "ok": False,
                "returncode": int(completed.returncode),
                "error": reason,
                "stdout_tail": stdout_tail,
                "stderr_tail": stderr_tail,
            }
    except subprocess.TimeoutExpired as exc:
        result = {
            **base,
            "ok": False,
            "returncode": None,
            "error": f"legacy report timed out after {timeout_seconds}s",
            "stdout_tail": str(exc.stdout or "")[-_MAX_CAPTURE_CHARS:],
            "stderr_tail": str(exc.stderr or "")[-_MAX_CAPTURE_CHARS:],
        }
    except Exception as exc:  # noqa: BLE001 - child must always close its receipt
        result = {
            **base,
            "ok": False,
            "returncode": None,
            "error": f"{type(exc).__name__}: {exc}"[:1000],
            "stdout_tail": "",
            "stderr_tail": "",
        }
    result["ended_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    _atomic_json(result_path, result)
    return result


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--job-id", required=True)
    parser.add_argument("--code", required=True)
    parser.add_argument("--asof", default="")
    parser.add_argument("--evidence-pack", required=True)
    parser.add_argument("--result-path", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--request-sha256", required=True)
    parser.add_argument("--timeout-seconds", type=int, default=900)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result = run_report_process(
            job_id=args.job_id,
            code=str(args.code).upper(),
            asof=str(args.asof or "") or None,
            evidence_pack=Path(args.evidence_pack),
            result_path=Path(args.result_path),
            out_dir=Path(args.out_dir),
            request_sha256=args.request_sha256,
            timeout_seconds=max(1, min(int(args.timeout_seconds), 3600)),
        )
        return 0 if result.get("ok") else 1
    except Exception as exc:  # Invalid bootstrap args may preclude a trusted receipt.
        print(f"company report process bootstrap failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":  # pragma: no cover - exercised as a subprocess
    raise SystemExit(main())


__all__ = ["RESULT_SCHEMA", "run_report_process"]
