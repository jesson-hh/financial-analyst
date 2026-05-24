"""``fa launch`` — one-command launcher.

Detects config + data, runs init wizard if needed, starts buddy backend + UI
http.server, opens browser. Single Ctrl+C stops everything gracefully.

Flow:
  1. Check ``.env`` has at least one LLM key → if not, run ``fa init``
  2. Check ``~/.financial-analyst/data/`` has data → if not, prompt to pull HF
  3. Start buddy server on :9999 (subprocess, captured stdout)
  4. Start UI http.server on :5173 (subprocess, cwd = bundled ui/ dir)
  5. Poll /health + UI / until both ready (≤30s)
  6. ``webbrowser.open("http://localhost:5173")``
  7. Print status panel + wait. Ctrl+C → terminate both subprocesses cleanly.

Goal: zero-config experience matching ``pip install foo && foo`` → web UI.
"""
from __future__ import annotations

import os
import signal
import socket
import subprocess
import sys
import time
import webbrowser
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.panel import Panel

console = Console()


# ──────────────────────── helpers ────────────────────────


def _ui_dir() -> Path:
    """Locate the bundled UI directory.

    Priority:
      1. ``$FA_UI_DIR`` env override
      2. ``<package>/ui/`` (pip-installed wheel)
      3. ``<repo>/packaging/src-tauri/ui/`` (development checkout)
    """
    env = os.environ.get("FA_UI_DIR", "").strip()
    if env:
        p = Path(env)
        if (p / "index.html").exists():
            return p
    here = Path(__file__).resolve().parent
    bundled = here / "ui"
    if (bundled / "index.html").exists():
        return bundled
    dev = here.parent.parent / "packaging" / "src-tauri" / "ui"
    if (dev / "index.html").exists():
        return dev
    raise FileNotFoundError(
        "Could not locate UI directory (expected <package>/ui/ "
        "or packaging/src-tauri/ui/). Set FA_UI_DIR to override."
    )


def _env_has_llm_key() -> bool:
    """Check whether at least one supported LLM provider key is set."""
    for k in ("DASHSCOPE_API_KEY", "DEEPSEEK_API_KEY",
              "OPENAI_API_KEY", "ANTHROPIC_API_KEY"):
        if os.environ.get(k, "").strip():
            return True
    # also check .env in cwd
    env_file = Path.cwd() / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, val = line.split("=", 1)
            if key.strip() in {"DASHSCOPE_API_KEY", "DEEPSEEK_API_KEY",
                                "OPENAI_API_KEY", "ANTHROPIC_API_KEY"} \
                    and val.strip():
                return True
    return False


def _data_dir_ok() -> bool:
    """Check whether the data dir has at least a calendar + instruments."""
    candidates = [
        Path.home() / ".financial-analyst" / "data" / "cn_data",
        Path("G:/stocks/stock_data/cn_data"),
    ]
    for p in candidates:
        if (p / "calendars" / "day.txt").exists() \
                and (p / "instruments" / "all.txt").exists():
            return True
    return False


def _port_free(port: int, host: str = "127.0.0.1") -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        try:
            s.connect((host, port))
            return False
        except (ConnectionRefusedError, socket.timeout, OSError):
            return True


def _wait_url(url: str, timeout: float = 30.0,
               poll: float = 0.5) -> bool:
    """Poll a URL until 2xx response or timeout."""
    import httpx
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with httpx.Client(timeout=2.0, trust_env=False) as c:
                r = c.get(url)
            if 200 <= r.status_code < 300:
                return True
        except Exception:
            pass
        time.sleep(poll)
    return False


# ──────────────────────── main launch ────────────────────────


def launch(
    skip_init: bool = typer.Option(False, "--skip-init",
                                    help="Skip the first-launch wizard even if config seems incomplete."),
    no_browser: bool = typer.Option(False, "--no-browser",
                                     help="Do not auto-open the browser."),
    backend_port: int = typer.Option(9999, "--backend-port",
                                      help="Backend (buddy SSE) port."),
    ui_port: int = typer.Option(5173, "--ui-port",
                                 help="UI http.server port."),
    backend_host: str = typer.Option("127.0.0.1", "--backend-host"),
    ui_host: str = typer.Option("127.0.0.1", "--ui-host"),
) -> None:
    """One-command launch: wizard if needed → backend + UI → browser.

    Equivalent to: ``fa init`` (if needed) + ``fa serve`` + ``python -m http.server`` (UI)
    + ``webbrowser.open``, all wrapped in a single Ctrl-C-aware process.
    """
    # 1. First-launch wizard (skippable)
    if not skip_init and not _env_has_llm_key():
        console.print("[yellow]No LLM API key detected. Running first-launch wizard...[/yellow]\n")
        from financial_analyst.init_cli import init as _init_cmd
        try:
            _init_cmd()
        except (typer.Exit, SystemExit):
            pass  # wizard returns Exit on normal completion; OK to continue
        if not _env_has_llm_key():
            console.print("[red]Still no LLM API key after wizard. Aborting.[/red]")
            console.print("[dim]Re-run `fa init` or set DASHSCOPE_API_KEY in .env manually.[/dim]")
            raise typer.Exit(2)

    if not _data_dir_ok():
        console.print(
            "[yellow]No data directory detected. Some features (e.g. fa report) need it.[/yellow]\n"
            "[dim]Run `fa init` to download a HuggingFace dataset bundle.[/dim]\n"
        )

    # 2. Locate UI files
    try:
        ui_path = _ui_dir()
    except FileNotFoundError as exc:
        console.print(f"[red]✗ {exc}[/red]")
        raise typer.Exit(3)
    console.print(f"[dim]UI directory: {ui_path}[/dim]")

    # 3. Port availability check
    if not _port_free(backend_port, backend_host):
        console.print(f"[red]✗ Backend port {backend_port} already in use.[/red]")
        console.print(f"[dim]Pass --backend-port <other> or kill the process holding it.[/dim]")
        raise typer.Exit(4)
    if not _port_free(ui_port, ui_host):
        console.print(f"[red]✗ UI port {ui_port} already in use.[/red]")
        raise typer.Exit(4)

    # 4. Start backend (buddy SSE)
    console.print(f"[dim]Starting backend on {backend_host}:{backend_port}...[/dim]")
    backend_log = Path.cwd() / ".fa-launch-backend.log"
    backend_proc = subprocess.Popen(
        [sys.executable, "-m", "financial_analyst.cli", "serve",
         "--host", backend_host, "--port", str(backend_port)],
        stdout=open(backend_log, "w", encoding="utf-8"),
        stderr=subprocess.STDOUT,
        creationflags=(subprocess.CREATE_NEW_PROCESS_GROUP
                        if sys.platform == "win32" else 0),
    )

    # 5. Start UI http.server
    console.print(f"[dim]Starting UI on {ui_host}:{ui_port}...[/dim]")
    ui_log = Path.cwd() / ".fa-launch-ui.log"
    ui_proc = subprocess.Popen(
        [sys.executable, "-m", "http.server", str(ui_port),
         "--bind", ui_host, "--directory", str(ui_path)],
        stdout=open(ui_log, "w", encoding="utf-8"),
        stderr=subprocess.STDOUT,
        creationflags=(subprocess.CREATE_NEW_PROCESS_GROUP
                        if sys.platform == "win32" else 0),
    )

    # 6. Wait both ready
    backend_url = f"http://{backend_host}:{backend_port}/health"
    ui_url = f"http://{ui_host}:{ui_port}/index.html"
    if not _wait_url(backend_url, timeout=30):
        console.print(f"[red]✗ Backend did not become ready in 30s. Log: {backend_log}[/red]")
        backend_proc.terminate(); ui_proc.terminate()
        raise typer.Exit(5)
    if not _wait_url(ui_url, timeout=10):
        console.print(f"[red]✗ UI did not become ready in 10s. Log: {ui_log}[/red]")
        backend_proc.terminate(); ui_proc.terminate()
        raise typer.Exit(5)

    # 7. Print ready panel + open browser
    open_url = f"http://localhost:{ui_port}/"
    console.print(Panel.fit(
        f"[bold green]✓ Ready[/bold green]\n\n"
        f"[bold]UI:[/bold]       {open_url}\n"
        f"[bold]Backend:[/bold]  http://{backend_host}:{backend_port}\n"
        f"[bold]Logs:[/bold]     {backend_log.name}, {ui_log.name}\n\n"
        f"[dim]Press Ctrl+C to stop both services.[/dim]",
        title="financial-analyst · one-command launcher",
        border_style="green",
    ))
    if not no_browser:
        try:
            webbrowser.open(open_url)
        except Exception:
            pass  # headless env

    # 8. Wait Ctrl+C, then terminate both
    def _terminate_all(*_):
        console.print("\n[yellow]Stopping services...[/yellow]")
        for p, name in [(backend_proc, "backend"), (ui_proc, "ui")]:
            if p.poll() is None:
                try:
                    if sys.platform == "win32":
                        p.send_signal(signal.CTRL_BREAK_EVENT)
                    else:
                        p.terminate()
                except Exception:
                    pass
        # final wait + force kill if still alive
        deadline = time.time() + 5
        for p in (backend_proc, ui_proc):
            while p.poll() is None and time.time() < deadline:
                time.sleep(0.2)
            if p.poll() is None:
                try: p.kill()
                except Exception: pass
        console.print("[green]✓ Stopped.[/green]")
        raise typer.Exit(0)

    if sys.platform != "win32":
        signal.signal(signal.SIGINT, _terminate_all)
        signal.signal(signal.SIGTERM, _terminate_all)

    try:
        while True:
            # detect either subprocess dying unexpectedly
            if backend_proc.poll() is not None:
                console.print(f"[red]✗ Backend exited (code {backend_proc.returncode}). Check {backend_log}.[/red]")
                ui_proc.terminate()
                raise typer.Exit(6)
            if ui_proc.poll() is not None:
                console.print(f"[red]✗ UI server exited (code {ui_proc.returncode}). Check {ui_log}.[/red]")
                backend_proc.terminate()
                raise typer.Exit(6)
            time.sleep(1)
    except KeyboardInterrupt:
        _terminate_all()
