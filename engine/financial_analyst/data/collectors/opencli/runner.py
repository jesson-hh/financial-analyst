"""Subprocess wrapper around ``opencli``. Returns parsed JSON or raises.

Windows note: npm installs CLIs as ``.CMD`` shim batch files (e.g.
``opencli.CMD`` → ``node main.js %*``). Routing through ``cmd.exe`` via
``shell=True`` transcodes the node child's utf-8 stdout through the active
console code page (GBK / cp936 on a Chinese Windows), shredding non-ASCII
content even when Python is told to decode as utf-8.

The workaround: parse the .CMD shim to recover the underlying ``main.js``
path, then call ``node <main.js> ...`` directly with ``shell=False``. cmd.exe
is out of the loop and node's utf-8 bytes reach Python intact. Non-Windows
platforms invoke ``opencli`` directly as before.
"""
from __future__ import annotations
import json
import os
import re
import shutil
import subprocess
import sys
from typing import Any, Dict, List, Optional, Tuple, Union


def is_opencli_available() -> bool:
    """Check if opencli is installed and on PATH."""
    return shutil.which("opencli") is not None


def _is_windows_batch(path: str) -> bool:
    """True if path points to a Windows .cmd/.bat batch wrapper."""
    if sys.platform != "win32":
        return False
    p = path.lower()
    return p.endswith(".cmd") or p.endswith(".bat")


_NPM_SHIM_JS_RE = re.compile(
    r'"%_prog%"\s*"?([^"]+\.js)"?\s*%\*',
    re.IGNORECASE,
)


def _resolve_npm_shim(cmd_path: str) -> Optional[Tuple[str, str]]:
    """Read an npm-style .CMD shim and return (node_exe, main_js_path).

    Returns None if the file is not a recognizable npm shim. Resolves the
    ``%dp0%`` placeholder npm writes against the .CMD's own directory so the
    js path comes out absolute.
    """
    try:
        with open(cmd_path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
    except OSError:
        return None

    m = _NPM_SHIM_JS_RE.search(content)
    if not m:
        return None
    js_rel = m.group(1)
    # npm shim uses %dp0% (the .CMD's directory, with trailing backslash) as
    # the anchor. Substitute it before resolving.
    dp0 = os.path.dirname(cmd_path) + os.sep
    js_path = js_rel.replace("%dp0%\\", dp0).replace("%dp0%", dp0)
    js_path = os.path.normpath(js_path)
    if not os.path.exists(js_path):
        return None
    node_exe = shutil.which("node") or "node"
    return node_exe, js_path


def run_opencli(
    *args: str,
    timeout: int = 60,
    fmt: str = "json",
) -> Union[List[Dict[str, Any]], Dict[str, Any]]:
    """Run an opencli command and parse JSON output.

    Args:
        *args: command parts, e.g. ("eastmoney", "kuaixun") or
            ("eastmoney", "holders", "600519")
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

    cli_args = [*args, "--format", fmt]

    # On Windows, bypass cmd.exe by parsing the .CMD shim and calling node
    # directly. This is the only reliable way to keep utf-8 stdout intact
    # under a GBK console code page.
    invoke: List[str]
    if _is_windows_batch(exe):
        shim = _resolve_npm_shim(exe)
        if shim is not None:
            node_exe, js_path = shim
            invoke = [node_exe, js_path, *cli_args]
        else:
            # Unrecognized shim — fall back to shell=True. Mojibake risk but
            # at least we don't fail outright.
            invoke = [exe, *cli_args]
    else:
        invoke = [exe, *cli_args]

    # opencli (node/undici) fetches DOMESTIC sites (xueqiu/同花顺/东财) + talks to
    # the local Chrome bridge — none of which should go through the user's
    # international proxy (Clash). If the server inherited HTTP_PROXY/HTTPS_PROXY,
    # node's EnvHttpProxyAgent routes xueqiu through it and the fetch fails
    # (opencli exit 2/66). Strip proxy env for the subprocess so it connects直连.
    child_env = dict(os.environ)
    for _k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy",
               "ALL_PROXY", "all_proxy"):
        child_env.pop(_k, None)
    child_env["NO_PROXY"] = "*"
    child_env["no_proxy"] = "*"

    try:
        # capture_output=True with no text/encoding → raw bytes. Decode
        # ourselves so we never inherit the parent shell's code page.
        result = subprocess.run(
            invoke,
            capture_output=True,
            timeout=timeout,
            shell=False,
            env=child_env,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(
            f"opencli timeout after {timeout}s: {' '.join(map(str, invoke))}"
        ) from exc
    except FileNotFoundError as exc:
        raise RuntimeError(
            f"opencli could not be launched: {exc}. "
            f"Resolved path: {exe}. On Windows ensure npm's prefix is on PATH "
            f"and the .cmd shim points at an existing main.js."
        ) from exc

    stdout_bytes: bytes = result.stdout or b""
    stderr_bytes: bytes = result.stderr or b""
    stdout_str = stdout_bytes.decode("utf-8", errors="replace")
    stderr_str = stderr_bytes.decode("utf-8", errors="replace")

    if result.returncode != 0:
        stderr_tail = stderr_str[-500:]
        raise RuntimeError(f"opencli exit {result.returncode}: {stderr_tail}")

    # opencli sometimes prints undici warnings to stdout — strip non-JSON
    # prefix lines.
    stdout = stdout_str.strip()
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
        raise RuntimeError(
            f"opencli: JSON decode failed: {exc} -- output: {stdout[:500]}"
        ) from exc
