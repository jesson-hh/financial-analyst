"""``fa start`` / ``fa launch`` — one-command launcher.

Designed for the simplest possible end-user experience:

  $ pip install financial-analyst
  $ fa start            # or just `fa` (bare)

  ── first time:  welcome screen → wizard → backend + UI → browser opens
  ── returning:   detects services already running? just opens browser.
                  otherwise starts everything + opens browser.

Implementation notes:
  * Single Python process drives both subprocesses (backend on :9999,
    UI on :5173). Ctrl+C terminates them cleanly.
  * Internal ``_do_launch(...)`` holds the logic so it can be called
    cleanly from Python (e.g. from the bare-``fa`` default callback)
    without typer.Option sentinels leaking in.
  * Fast path: if both ports already serving healthy responses, we skip
    subprocess management and just open the browser.
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
from rich.align import Align
from rich.console import Console, Group
from rich.panel import Panel
from rich.rule import Rule
from rich.table import Table
from rich.text import Text

# Optional helper import used inside _data_staleness_banner — at module level
# import we just need to know the name resolves; the actual call is wrapped
# in a try/except so a missing module is non-fatal.
from typing import Optional  # noqa  (already imported above; safe shadow)

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


def _is_healthy(url: str, timeout: float = 1.5) -> bool:
    """Single-shot health probe — used by the fast path."""
    try:
        import httpx
        with httpx.Client(timeout=timeout, trust_env=False) as c:
            r = c.get(url)
        return 200 <= r.status_code < 300
    except Exception:
        return False


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


def _detect_lang() -> str:
    """Pick UI language for the welcome message.

    Honour FA_LANG env var or .env entry, else fall back to zh.
    """
    raw = os.environ.get("FA_LANG", "").strip().lower()
    if raw in ("zh", "en"):
        return raw
    env_file = Path.cwd() / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if line.startswith("FA_LANG=") and "=" in line:
                v = line.split("=", 1)[1].strip().lower()
                if v in ("zh", "en"):
                    return v
    return "zh"


# ──────────────────────── welcome screens ────────────────────────


def _show_first_time_welcome(lang: str) -> None:
    """Big friendly panel shown only on the very first launch."""
    title = Text("觀瀾 · financial-analyst", style="bold cyan", justify="center")
    tagline = (Text("个股深度研究 · 24 agent · 一键起", style="dim italic", justify="center")
               if lang == "zh"
               else Text("Stock deep-dive · 24 agents · one-command",
                         style="dim italic", justify="center"))

    if lang == "zh":
        body = Text.assemble(
            ("欢迎 ", "default"), ("🎉", ""), ("\n\n", ""),
            ("第一次启动需要一次性 ", "default"),
            ("3 分钟", "bold yellow"),
            (" 配置:\n\n", "default"),
            ("  ", ""), ("• ", "cyan"),
            ("填一个 LLM API key  ", "default"),
            ("(", "dim"),
            ("推荐 DashScope · 注册送 100w token", "dim italic"),
            (")\n", "dim"),
            ("  ", ""), ("• ", "cyan"),
            ("选一个数据包    ", "default"),
            ("(", "dim"),
            ("demo 155MB 足够上手", "dim italic"),
            (")\n\n", "dim"),
            ("配完之后会自动起后端 + UI + 开浏览器, ", "default"),
            ("以后再跑 ", "default"),
            ("fa start", "bold cyan"),
            (" 就直接开 ↗", "default"),
        )
    else:
        body = Text.assemble(
            ("Welcome ", "default"), ("🎉", ""), ("\n\n", ""),
            ("First launch needs a one-time ", "default"),
            ("3-minute ", "bold yellow"),
            ("setup:\n\n", "default"),
            ("  ", ""), ("• ", "cyan"),
            ("One LLM API key  ", "default"),
            ("(", "dim"),
            ("DashScope recommended · free 1M tokens on signup", "dim italic"),
            (")\n", "dim"),
            ("  ", ""), ("• ", "cyan"),
            ("One data package ", "default"),
            ("(", "dim"),
            ("demo 155MB is enough to start", "dim italic"),
            (")\n\n", "dim"),
            ("After setup, backend + UI + browser auto-start. ", "default"),
            ("Next time, just run ", "default"),
            ("fa start", "bold cyan"),
            (" ↗", "default"),
        )

    console.print()
    console.print(Panel(
        Align.center(Group(title, tagline, Text(""), body)),
        border_style="cyan",
        padding=(1, 4),
        width=72,
    ))


def _show_returning_welcome(lang: str) -> None:
    """Tiny banner shown on subsequent launches."""
    msg = (Text.assemble(
        ("✓ ", "green bold"),
        ("欢迎回来  ", "bold"),
        ("· 启动工作台中...", "dim"),
    ) if lang == "zh" else Text.assemble(
        ("✓ ", "green bold"),
        ("Welcome back  ", "bold"),
        ("· starting your workstation...", "dim"),
    ))
    console.print()
    console.print(msg)


def _show_already_running(url: str, lang: str) -> None:
    """Fast-path panel — services already serving, just open browser."""
    # Same passive update + staleness probes as the ready panel
    update_line = None
    try:
        from financial_analyst.update_cli import maybe_render_update_banner
        update_line = maybe_render_update_banner(lang=lang)
    except Exception:
        pass
    stale_line = _data_staleness_banner(lang)

    title = ("[bold green]🟢 已在运行[/bold green]" if lang == "zh"
             else "[bold green]🟢 Already running[/bold green]")
    body = (f"financial-analyst 已经跑着了, 直接开浏览器 ↗\n\n"
            f"[bold cyan]{url}[/bold cyan]"
            if lang == "zh"
            else f"financial-analyst is already running. Opening browser ↗\n\n"
                 f"[bold cyan]{url}[/bold cyan]")
    extra_lines = [s for s in (stale_line, update_line) if s]
    if extra_lines:
        body = body + "\n\n" + "\n".join(extra_lines)
    console.print()
    console.print(Panel.fit(
        body,
        title=title,
        border_style="green",
        padding=(1, 3),
    ))


def _data_staleness_banner(lang: str) -> Optional[str]:
    """Return a one-liner if day data hasn't been refreshed in > 24h."""
    try:
        from financial_analyst.data import last_update as _lu
        h = _lu.hours_since("day")
        if h is None:
            # Never updated — could be HF snapshot only; nudge once
            if lang == "zh":
                return ("[bold yellow]↻ 还没跑过增量更新[/bold yellow] "
                        "[dim]· 运行 [cyan]fa data refresh[/cyan] 拉最新行情[/dim]")
            return ("[bold yellow]↻ Data not refreshed yet[/bold yellow] "
                    "[dim]· run [cyan]fa data refresh[/cyan] to pull latest[/dim]")
        if h >= _lu.STALE_THRESHOLD_HOURS["day"]:
            age = _lu._format_age(h)
            if lang == "zh":
                return (f"[bold yellow]↻ 日线数据 {age}[/bold yellow] "
                        f"[dim]· 运行 [cyan]fa data refresh[/cyan] 更新[/dim]")
            return (f"[bold yellow]↻ Day data {age}[/bold yellow] "
                    f"[dim]· run [cyan]fa data refresh[/cyan] to update[/dim]")
    except Exception:
        pass
    return None


def _show_ready_panel(ui_url: str, backend_url: str, lang: str,
                      backend_log: Path, ui_log: Path) -> None:
    """The 'everything started' status panel.

    Also passively probes PyPI for a newer release (≤2s, 24h-cached)
    and appends an update banner if one is available; also nudges to
    refresh stale market data.
    """
    # Best-effort, non-blocking update hint — never throws, never blocks > 2s
    update_line = None
    try:
        from financial_analyst.update_cli import maybe_render_update_banner
        update_line = maybe_render_update_banner(lang=lang)
    except Exception:
        pass
    stale_line = _data_staleness_banner(lang)

    if lang == "zh":
        body = (
            f"[bold]Web UI:[/bold]   [cyan]{ui_url}[/cyan]\n"
            f"[bold]后端:[/bold]    [dim]{backend_url}[/dim]\n"
            f"[bold]日志:[/bold]    [dim]{backend_log.name}  ·  {ui_log.name}[/dim]\n\n"
            f"[dim]浏览器自动打开. 关闭工作台: 按 Ctrl+C[/dim]"
        )
        title = "[bold green]✓ 工作台就绪[/bold green]"
    else:
        body = (
            f"[bold]Web UI:[/bold]   [cyan]{ui_url}[/cyan]\n"
            f"[bold]Backend:[/bold]  [dim]{backend_url}[/dim]\n"
            f"[bold]Logs:[/bold]     [dim]{backend_log.name}  ·  {ui_log.name}[/dim]\n\n"
            f"[dim]Browser opening. Press Ctrl+C to stop the workstation.[/dim]"
        )
        title = "[bold green]✓ Workstation ready[/bold green]"
    if update_line:
        body = body + "\n\n" + update_line
    console.print()
    console.print(Panel.fit(
        body,
        title=title,
        border_style="green",
        padding=(1, 3),
    ))


# ──────────────────────── core: _do_launch ────────────────────────


def _do_launch(
    skip_init: bool = False,
    no_browser: bool = False,
    backend_port: int = 9999,
    ui_port: int = 5173,
    backend_host: str = "127.0.0.1",
    ui_host: str = "127.0.0.1",
) -> None:
    """The real launch logic. Callable from Python.

    Keep this separate from the typer wrapper below so the bare-``fa``
    callback and ``fa start`` / ``fa launch`` can all invoke it cleanly
    without OptionInfo sentinels leaking in.
    """
    lang = _detect_lang()
    backend_url = f"http://{backend_host}:{backend_port}"
    backend_health = f"{backend_url}/health"
    ui_url = f"http://{ui_host}:{ui_port}/"

    # ─── 0. Fast path: both services already up → just open browser ───
    if _is_healthy(backend_health, timeout=1.0) and _is_healthy(ui_url, timeout=1.0):
        _show_already_running(ui_url, lang)
        if not no_browser:
            try:
                webbrowser.open(ui_url)
            except Exception:
                pass
        return

    # ─── 1. First-time welcome vs returning ───
    is_first_time = not _env_has_llm_key()
    if is_first_time:
        _show_first_time_welcome(lang)
        if not skip_init:
            from financial_analyst.init_cli import init_cmd as _init_cmd
            try:
                _init_cmd(yes=False, preset=None, target=None, lang=None)
            except (typer.Exit, SystemExit):
                pass
            if not _env_has_llm_key():
                if lang == "zh":
                    console.print("[red]✗ 配置后仍未检测到 LLM API key. 终止.[/red]")
                    console.print("[dim]  再跑一次 fa init, 或手动编辑 .env 加上 DASHSCOPE_API_KEY.[/dim]")
                else:
                    console.print("[red]✗ Still no LLM API key after wizard. Aborting.[/red]")
                    console.print("[dim]  Re-run fa init or set DASHSCOPE_API_KEY in .env manually.[/dim]")
                raise typer.Exit(2)
    else:
        _show_returning_welcome(lang)

    if not _data_dir_ok():
        warn = ("[yellow]⚠ 没找到数据目录 — fa report 等功能需要数据.[/yellow]\n"
                "[dim]  跑 fa init 拉一份 HuggingFace 数据包 (demo 155MB).[/dim]"
                if lang == "zh"
                else "[yellow]⚠ No data directory detected — some features (fa report) need it.[/yellow]\n"
                     "[dim]  Run fa init to download a HuggingFace dataset bundle (demo 155MB).[/dim]")
        console.print()
        console.print(warn)

    # ─── 2. Locate UI files ───
    try:
        ui_path = _ui_dir()
    except FileNotFoundError as exc:
        console.print(f"[red]✗ {exc}[/red]")
        raise typer.Exit(3)

    # ─── 3. Port availability check ───
    if not _port_free(backend_port, backend_host):
        msg = (f"[red]✗ 后端端口 {backend_port} 被占用. 加 --backend-port <其他>"
               f" 或者杀掉占用进程.[/red]" if lang == "zh"
               else f"[red]✗ Backend port {backend_port} already in use. Pass --backend-port <other>"
                    f" or kill the process holding it.[/red]")
        console.print(msg)
        raise typer.Exit(4)
    if not _port_free(ui_port, ui_host):
        msg = (f"[red]✗ UI 端口 {ui_port} 被占用.[/red]" if lang == "zh"
               else f"[red]✗ UI port {ui_port} already in use.[/red]")
        console.print(msg)
        raise typer.Exit(4)

    # ─── 4. Start subprocesses ───
    console.print()
    starting_msg = ("[dim]启动 backend (:9999) + UI (:5173)...[/dim]" if lang == "zh"
                    else "[dim]Starting backend (:9999) + UI (:5173)...[/dim]")
    console.print(starting_msg)

    backend_log = Path.cwd() / ".fa-launch-backend.log"
    backend_proc = subprocess.Popen(
        [sys.executable, "-m", "financial_analyst.cli", "serve",
         "--host", backend_host, "--port", str(backend_port)],
        stdout=open(backend_log, "w", encoding="utf-8"),
        stderr=subprocess.STDOUT,
        creationflags=(subprocess.CREATE_NEW_PROCESS_GROUP
                        if sys.platform == "win32" else 0),
    )

    ui_log = Path.cwd() / ".fa-launch-ui.log"
    ui_proc = subprocess.Popen(
        [sys.executable, "-m", "http.server", str(ui_port),
         "--bind", ui_host, "--directory", str(ui_path)],
        stdout=open(ui_log, "w", encoding="utf-8"),
        stderr=subprocess.STDOUT,
        creationflags=(subprocess.CREATE_NEW_PROCESS_GROUP
                        if sys.platform == "win32" else 0),
    )

    # ─── 5. Wait both ready (with friendly spinner) ───
    waiting_msg = ("[dim]等待服务就绪 (≤30s)...[/dim]" if lang == "zh"
                   else "[dim]Waiting for services (≤30s)...[/dim]")
    with console.status(waiting_msg, spinner="dots", spinner_style="cyan"):
        backend_ok = _wait_url(backend_health, timeout=30)
        ui_ok = _wait_url(f"{ui_url}index.html", timeout=10)

    if not backend_ok:
        fail_msg = (f"[red]✗ 后端 30s 内没起来. 看日志: {backend_log}[/red]" if lang == "zh"
                    else f"[red]✗ Backend did not become ready in 30s. Log: {backend_log}[/red]")
        console.print(fail_msg)
        backend_proc.terminate(); ui_proc.terminate()
        raise typer.Exit(5)
    if not ui_ok:
        fail_msg = (f"[red]✗ UI 10s 内没起来. 看日志: {ui_log}[/red]" if lang == "zh"
                    else f"[red]✗ UI did not become ready in 10s. Log: {ui_log}[/red]")
        console.print(fail_msg)
        backend_proc.terminate(); ui_proc.terminate()
        raise typer.Exit(5)

    # ─── 6. Ready panel + open browser ───
    _show_ready_panel(ui_url, backend_url, lang, backend_log, ui_log)
    if not no_browser:
        try:
            webbrowser.open(ui_url)
        except Exception:
            pass

    # ─── 7. Wait Ctrl+C, terminate cleanly ───
    def _terminate_all(*_):
        stop_msg = ("[yellow]停止中...[/yellow]" if lang == "zh"
                    else "[yellow]Stopping services...[/yellow]")
        console.print("\n" + str(stop_msg))
        for p, _name in [(backend_proc, "backend"), (ui_proc, "ui")]:
            if p.poll() is None:
                try:
                    if sys.platform == "win32":
                        p.send_signal(signal.CTRL_BREAK_EVENT)
                    else:
                        p.terminate()
                except Exception:
                    pass
        deadline = time.time() + 5
        for p in (backend_proc, ui_proc):
            while p.poll() is None and time.time() < deadline:
                time.sleep(0.2)
            if p.poll() is None:
                try: p.kill()
                except Exception: pass
        done_msg = ("[green]✓ 已停止.[/green]" if lang == "zh"
                    else "[green]✓ Stopped.[/green]")
        console.print(done_msg)
        raise typer.Exit(0)

    if sys.platform != "win32":
        signal.signal(signal.SIGINT, _terminate_all)
        signal.signal(signal.SIGTERM, _terminate_all)

    try:
        while True:
            if backend_proc.poll() is not None:
                msg = (f"[red]✗ 后端异常退出 (code {backend_proc.returncode}). "
                       f"看 {backend_log}.[/red]" if lang == "zh"
                       else f"[red]✗ Backend exited (code {backend_proc.returncode}). "
                            f"Check {backend_log}.[/red]")
                console.print(msg)
                ui_proc.terminate()
                raise typer.Exit(6)
            if ui_proc.poll() is not None:
                msg = (f"[red]✗ UI 异常退出 (code {ui_proc.returncode}). "
                       f"看 {ui_log}.[/red]" if lang == "zh"
                       else f"[red]✗ UI exited (code {ui_proc.returncode}). "
                            f"Check {ui_log}.[/red]")
                console.print(msg)
                backend_proc.terminate()
                raise typer.Exit(6)
            time.sleep(1)
    except KeyboardInterrupt:
        _terminate_all()


# ──────────────────────── typer wrapper ────────────────────────


def launch(
    skip_init: bool = typer.Option(False, "--skip-init",
                                    help="Skip the first-launch wizard even if config is incomplete."),
    no_browser: bool = typer.Option(False, "--no-browser",
                                     help="Do not auto-open the browser."),
    backend_port: int = typer.Option(9999, "--backend-port",
                                      help="Backend (buddy SSE) port."),
    ui_port: int = typer.Option(5173, "--ui-port",
                                 help="Web UI http.server port."),
    backend_host: str = typer.Option("127.0.0.1", "--backend-host"),
    ui_host: str = typer.Option("127.0.0.1", "--ui-host"),
) -> None:
    """One-command launch: wizard if needed → backend + UI → browser.

    Registered under TWO names for friendliness:
      ``fa start``   — primary user-facing name
      ``fa launch``  — original / power-user name (alias)

    Bare ``fa`` (no subcommand) also calls this.

    Examples:
      fa start                              # first time: wizard + everything
      fa start                              # subsequent: opens browser
      fa start --no-browser                 # headless / remote
      fa start --backend-port 9000          # custom backend port
    """
    _do_launch(
        skip_init=skip_init,
        no_browser=no_browser,
        backend_port=backend_port,
        ui_port=ui_port,
        backend_host=backend_host,
        ui_host=ui_host,
    )


# Alias for the bare-``fa`` callback in cli.py and for any other Python
# caller that wants the launch behaviour without going through typer.
def run_default() -> None:
    """Plain-Python entry to start the workstation with all defaults."""
    _do_launch()
