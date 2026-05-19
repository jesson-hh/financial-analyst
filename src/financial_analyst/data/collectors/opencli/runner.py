"""Subprocess wrapper around `opencli`. Returns parsed JSON or raises.

Windows note: npm installs CLIs as ``.CMD`` batch wrappers. Python's
``subprocess`` with ``shell=False`` can't invoke them directly. We resolve the
full path via ``shutil.which`` and ``shell=True`` on Windows ``.cmd``/``.bat``.
"""
from __future__ import annotations
import json
import shutil
import subprocess
import sys
from typing import Any, Dict, List, Union


def is_opencli_available() -> bool:
    """Check if opencli is installed and on PATH."""
    return shutil.which("opencli") is not None


def _is_windows_batch(path: str) -> bool:
    """True if path points to a Windows .cmd/.bat batch wrapper."""
    if sys.platform != "win32":
        return False
    p = path.lower()
    return p.endswith(".cmd") or p.endswith(".bat")


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
    exe = shutil.which("opencli")
    if exe is None:
        raise RuntimeError(
            "opencli not found on PATH. Install: npm install -g @jackwener/opencli"
        )

    full_cmd = [exe, *args, "--format", fmt]

    try:
        if _is_windows_batch(exe):
            # Windows .cmd files need cmd.exe — pass as a string with quoting
            quoted = " ".join(
                f'"{a}"' if (" " in str(a) or any(c in str(a) for c in "&|<>^"))
                else str(a)
                for a in full_cmd
            )
            result = subprocess.run(
                quoted, shell=True, capture_output=True, text=True,
                timeout=timeout, encoding="utf-8", errors="replace",
            )
        else:
            result = subprocess.run(
                full_cmd, capture_output=True, text=True,
                timeout=timeout, encoding="utf-8", errors="replace",
            )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"opencli timeout after {timeout}s: {' '.join(map(str, full_cmd))}") from exc
    except FileNotFoundError as exc:
        raise RuntimeError(
            f"opencli could not be launched: {exc}. "
            f"Resolved path: {exe}. On Windows ensure npm's prefix is on PATH and the .cmd is intact."
        ) from exc

    if result.returncode != 0:
        stderr_tail = (result.stderr or "")[-500:]
        raise RuntimeError(f"opencli exit {result.returncode}: {stderr_tail}")

    # opencli sometimes prints undici warnings to stdout — strip non-JSON prefix lines
    stdout = (result.stdout or "").strip()
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
