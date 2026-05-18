"""Subprocess wrapper around `opencli`. Returns parsed JSON or raises."""
from __future__ import annotations
import json
import shutil
import subprocess
from typing import Any, Dict, List, Optional, Union


def is_opencli_available() -> bool:
    """Check if opencli is installed and on PATH."""
    return shutil.which("opencli") is not None


def run_opencli(
    *args: str,
    timeout: int = 60,
    fmt: str = "json",
) -> Union[List[Dict[str, Any]], Dict[str, Any]]:
    """Run an opencli command and parse JSON output.

    Args:
        *args: command parts, e.g. ("eastmoney", "kuaixun") or ("eastmoney", "holders", "600519")
        timeout: seconds before kill (default 60)
        fmt: output format flag (default "json")

    Returns:
        Parsed JSON (list or dict). Raises on non-zero exit or parse failure.
    """
    if not is_opencli_available():
        raise RuntimeError(
            "opencli not found on PATH. Install: npm install -g @jackwener/opencli"
        )

    cmd = ["opencli", *args, "--format", fmt]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, encoding="utf-8",
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"opencli timeout after {timeout}s: {' '.join(cmd)}") from exc

    if result.returncode != 0:
        stderr_tail = (result.stderr or "")[-500:]
        raise RuntimeError(f"opencli exit {result.returncode}: {stderr_tail}")

    # opencli sometimes prints undici warnings to stdout — strip non-JSON prefix lines
    stdout = result.stdout.strip()
    if not stdout:
        return []

    # Find the first '[' or '{' (start of JSON)
    json_start = -1
    for i, ch in enumerate(stdout):
        if ch in "[{":
            json_start = i
            break
    if json_start < 0:
        raise RuntimeError(f"opencli: no JSON in output: {stdout[:200]}")

    try:
        return json.loads(stdout[json_start:])
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"opencli: JSON decode failed: {exc} -- output: {stdout[:500]}") from exc
