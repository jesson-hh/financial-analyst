"""``fa update`` — one-command upgrade from PyPI.

Two entry points:

  * ``fa update``                 — explicit "check + upgrade now"
  * background hint on ``fa start`` — read-only check, shows a banner if a
                                       newer version is on PyPI. Throttled to
                                       once per 24h via a cache file under
                                       ``~/.financial-analyst/.update_check.json``.

Editable installs (``pip install -e .`` for the dev) are detected and the
upgrade is refused — the dev should ``git pull`` instead.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional, Tuple

import typer
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm
from rich.text import Text

console = Console()


_PACKAGE = "financial-analyst"
_PYPI_JSON = f"https://pypi.org/pypi/{_PACKAGE}/json"
_CACHE_FILE = Path.home() / ".financial-analyst" / ".update_check.json"
_DEFAULT_TTL_HOURS = 24


# ──────────────────────── version helpers ────────────────────────


def _is_newer(a: str, b: str) -> bool:
    """Return True if version ``a`` is strictly newer than ``b`` (SemVer)."""
    try:
        from packaging.version import Version
        return Version(a) > Version(b)
    except Exception:
        # Naive fallback: 1.0.10 > 1.0.9 won't sort right with plain string compare,
        # but it's good enough for the common case 1.0.1 → 1.0.2.
        return a > b


def _is_editable_install() -> bool:
    """Detect ``pip install -e .`` (dev / source checkout).

    Approach: check if the package's installed Location is a directory that
    also contains ``pyproject.toml`` — that's the signature of an editable
    install via setuptools/hatchling .pth.
    """
    try:
        import importlib.util
        spec = importlib.util.find_spec("financial_analyst")
        if not spec or not spec.origin:
            return False
        pkg_dir = Path(spec.origin).resolve().parent
        # Walk up looking for pyproject.toml at the repo root
        for ancestor in [pkg_dir, pkg_dir.parent, pkg_dir.parent.parent]:
            if (ancestor / "pyproject.toml").exists() and (ancestor / "src").exists():
                return True
    except Exception:
        pass
    return False


def _get_latest_pypi_version(timeout: float = 2.0) -> Optional[str]:
    """Single-shot PyPI lookup. Returns None on any failure (no network, etc.)."""
    try:
        import httpx
        with httpx.Client(timeout=timeout, trust_env=True) as c:
            r = c.get(_PYPI_JSON)
        if 200 <= r.status_code < 300:
            return r.json()["info"]["version"]
    except Exception:
        return None
    return None


def _current_version() -> str:
    from financial_analyst import __version__
    return __version__


# ──────────────────────── cache (throttle background checks) ────────────────────────


def _read_cache() -> Optional[dict]:
    if not _CACHE_FILE.exists():
        return None
    try:
        return json.loads(_CACHE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return None


def _write_cache(latest: str) -> None:
    try:
        _CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        _CACHE_FILE.write_text(
            json.dumps({
                "checked_at": time.time(),
                "current": _current_version(),
                "latest": latest,
            }, indent=2),
            encoding="utf-8",
        )
    except Exception:
        pass


def check_for_updates(force: bool = False,
                      ttl_hours: int = _DEFAULT_TTL_HOURS,
                      ) -> Optional[Tuple[str, str]]:
    """Background-friendly update probe.

    Returns ``(current, latest)`` if a newer release exists on PyPI, else None.
    Honours a per-user cache (default 24h TTL) so we don't hit PyPI every
    ``fa start``. ``force=True`` bypasses the cache.
    """
    current = _current_version()
    cache = None if force else _read_cache()
    if cache:
        try:
            age = time.time() - float(cache.get("checked_at", 0))
            if age < ttl_hours * 3600:
                latest = cache.get("latest")
                if latest and _is_newer(latest, current):
                    return (current, latest)
                return None  # cache valid + no update
        except Exception:
            pass

    latest = _get_latest_pypi_version()
    if not latest:
        return None
    _write_cache(latest)
    if _is_newer(latest, current):
        return (current, latest)
    return None


# ──────────────────────── i18n ────────────────────────


def _lang() -> str:
    raw = os.environ.get("FA_LANG", "").strip().lower()
    if raw in ("zh", "en"):
        return raw
    env_file = Path.cwd() / ".env"
    if env_file.exists():
        try:
            for line in env_file.read_text(encoding="utf-8", errors="replace").splitlines():
                line = line.strip()
                if line.startswith("FA_LANG=") and "=" in line:
                    v = line.split("=", 1)[1].strip().lower()
                    if v in ("zh", "en"):
                        return v
        except Exception:
            pass
    return "zh"


# ──────────────────────── public banner (used by `fa start`) ────────────────────────


def maybe_render_update_banner(lang: Optional[str] = None) -> Optional[str]:
    """Return a short one-liner if an update is available, else None.

    Caller (e.g. the ``fa start`` ready panel) can append it. We deliberately
    keep this passive — never throws, never prints, never blocks > 2s.
    """
    try:
        result = check_for_updates(force=False)
    except Exception:
        return None
    if not result:
        return None
    current, latest = result
    lg = lang or _lang()
    if lg == "zh":
        return (f"[bold yellow]↗ 新版本可用[/bold yellow] "
                f"[dim]v{current} → [bold]v{latest}[/bold]  ·  "
                f"运行 [cyan]fa update[/cyan] 升级[/dim]")
    return (f"[bold yellow]↗ Update available[/bold yellow] "
            f"[dim]v{current} → [bold]v{latest}[/bold]  ·  "
            f"run [cyan]fa update[/cyan] to upgrade[/dim]")


# ──────────────────────── `fa update` command ────────────────────────


def update_cmd(
    yes: bool = typer.Option(False, "--yes", "-y",
                              help="不要交互确认, 直接 pip install -U / Skip confirmation."),
    check_only: bool = typer.Option(False, "--check",
                                     help="只检查不安装 / Check only, don't install."),
) -> None:
    """从 PyPI 检查并升级到最新版 financial-analyst.

    Examples:
      fa update              # 交互: 检查 → 看到新版 → y 确认 → 升级
      fa update --yes        # 非交互: 有新版直接升
      fa update --check      # 只查不升, 适合 cron / CI
    """
    lang = _lang()
    current = _current_version()

    # Reject editable installs — dev should `git pull` instead.
    if _is_editable_install():
        if lang == "zh":
            console.print(Panel(
                f"[bold]检测到 editable install[/bold] (`pip install -e .`).\n\n"
                f"你这装的是源码 dev 模式, [bold red]不能[/bold red]用 pip install -U 升级 "
                f"— 那样会装一份 PyPI 版盖掉你的本地代码.\n\n"
                f"[dim]要更新走 git:[/dim]\n"
                f"  [cyan]cd <repo> ^&^& git pull[/cyan]",
                title="[yellow]⚠ Editable install[/yellow]",
                border_style="yellow",
                padding=(0, 2),
            ))
        else:
            console.print(Panel(
                f"[bold]Editable install detected[/bold] (`pip install -e .`).\n\n"
                f"You're on a source checkout. [bold red]Do not[/bold red] use pip install -U here "
                f"— it would shadow your local code with the PyPI release.\n\n"
                f"[dim]Update via git instead:[/dim]\n"
                f"  [cyan]cd <repo> && git pull[/cyan]",
                title="[yellow]⚠ Editable install[/yellow]",
                border_style="yellow",
                padding=(0, 2),
            ))
        raise typer.Exit(1)

    # Force a fresh PyPI check
    if lang == "zh":
        console.print(f"[dim]检查 PyPI 最新版 ([cyan]{_PACKAGE}[/cyan])...[/dim]")
    else:
        console.print(f"[dim]Checking PyPI for [cyan]{_PACKAGE}[/cyan]...[/dim]")
    result = check_for_updates(force=True)

    if not result:
        # already latest, or PyPI unreachable
        latest = _get_latest_pypi_version()
        if latest:
            if lang == "zh":
                console.print(Panel.fit(
                    f"[bold green]✓ 已是最新版[/bold green]\n\n"
                    f"  当前: [bold]v{current}[/bold]\n"
                    f"  PyPI: v{latest}",
                    border_style="green", padding=(0, 2),
                ))
            else:
                console.print(Panel.fit(
                    f"[bold green]✓ Already up to date[/bold green]\n\n"
                    f"  installed: [bold]v{current}[/bold]\n"
                    f"  PyPI:      v{latest}",
                    border_style="green", padding=(0, 2),
                ))
        else:
            if lang == "zh":
                console.print("[yellow]⚠ 没拿到 PyPI 版本号 (没网?可设代理).[/yellow]")
            else:
                console.print("[yellow]⚠ Could not reach PyPI (no network? try a proxy).[/yellow]")
        raise typer.Exit(0)

    current, latest = result
    release_url = f"https://github.com/jesson-hh/{_PACKAGE}/releases/tag/v{latest}"
    pypi_url = f"https://pypi.org/project/{_PACKAGE}/{latest}/"

    if lang == "zh":
        body = (
            f"  当前:  [bold]v{current}[/bold]\n"
            f"  最新:  [bold cyan]v{latest}[/bold cyan]  [dim]← 升级到这版[/dim]\n\n"
            f"  Release notes:  [dim]{release_url}[/dim]\n"
            f"  PyPI:           [dim]{pypi_url}[/dim]"
        )
        title = "[bold]🎉 新版本可用[/bold]"
    else:
        body = (
            f"  installed:  [bold]v{current}[/bold]\n"
            f"  latest:     [bold cyan]v{latest}[/bold cyan]  [dim]← upgrade target[/dim]\n\n"
            f"  Release notes:  [dim]{release_url}[/dim]\n"
            f"  PyPI:           [dim]{pypi_url}[/dim]"
        )
        title = "[bold]🎉 Update available[/bold]"
    console.print(Panel.fit(
        body,
        title=title,
        border_style="cyan",
        padding=(1, 2),
    ))

    if check_only:
        return

    if not yes:
        prompt_msg = ("[bold]立即升级?[/bold]" if lang == "zh"
                      else "[bold]Upgrade now?[/bold]")
        if not Confirm.ask(prompt_msg, default=True):
            if lang == "zh":
                console.print("[dim]取消. 之后跑 [cyan]fa update[/cyan] 再升级.[/dim]")
            else:
                console.print("[dim]Skipped. Run [cyan]fa update[/cyan] later.[/dim]")
            raise typer.Exit(0)

    # Actually upgrade
    if lang == "zh":
        console.print(f"\n[dim]运行 pip install -U {_PACKAGE}=={latest}...[/dim]\n")
    else:
        console.print(f"\n[dim]Running pip install -U {_PACKAGE}=={latest}...[/dim]\n")

    cmd = [sys.executable, "-m", "pip", "install", "-U",
           f"{_PACKAGE}=={latest}"]
    try:
        # Stream pip output directly to the user's terminal
        result_code = subprocess.call(cmd)
    except Exception as e:
        console.print(f"[red]✗ pip 启动失败: {type(e).__name__}: {e}[/red]")
        raise typer.Exit(2)

    if result_code != 0:
        if lang == "zh":
            console.print(f"\n[red]✗ pip 退出码 {result_code}. 看上面错误.[/red]")
        else:
            console.print(f"\n[red]✗ pip exited with code {result_code}. See above.[/red]")
        raise typer.Exit(result_code)

    # Refresh cache (latest is now installed)
    _write_cache(latest)

    if lang == "zh":
        console.print(Panel.fit(
            f"[bold green]✓ 升级完成[/bold green]\n\n"
            f"  [bold]v{current}[/bold] → [bold cyan]v{latest}[/bold cyan]\n\n"
            f"[dim]下一步:[/dim] [cyan]fa start[/cyan]  [dim]重启工作台[/dim]",
            border_style="green",
            padding=(1, 2),
        ))
    else:
        console.print(Panel.fit(
            f"[bold green]✓ Upgrade complete[/bold green]\n\n"
            f"  [bold]v{current}[/bold] → [bold cyan]v{latest}[/bold cyan]\n\n"
            f"[dim]Next:[/dim] [cyan]fa start[/cyan]  [dim]to restart the workstation[/dim]",
            border_style="green",
            padding=(1, 2),
        ))
