from __future__ import annotations
import asyncio
import sys
import typer
from pathlib import Path
from dotenv import load_dotenv

# On Windows zh-CN PowerShell, default stdout codec is GBK which chokes on ¥, emoji,
# and any rare CJK char. Force UTF-8 with replace-on-error so reports always render.
for stream in (sys.stdout, sys.stderr):
    try:
        stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

load_dotenv(override=True)  # populate os.environ from .env (overrides any existing shell vars)

from financial_analyst import __version__

app = typer.Typer(
    name="financial-analyst",
    help="A-share single-stock deep-dive multi-agent research workstation.",
    no_args_is_help=False,
)


@app.command()
def version():
    """Print version."""
    typer.echo(f"financial-analyst {__version__}")


@app.command()
def report(
    code: str = typer.Argument(..., help="Stock code, e.g. SH600519"),
    asof: str = typer.Option(None, help="As-of date YYYY-MM-DD (default: today)"),
    out_dir: Path = typer.Option(Path("./out"), help="Output directory"),
):
    """Generate single-stock deep-dive report (one-shot)."""
    from financial_analyst.tui import run_report_oneshot
    asyncio.run(run_report_oneshot(code=code, asof=asof, out_dir=out_dir))


@app.command()
def chat():
    """Drop into interactive TUI."""
    from financial_analyst.tui import run_tui
    asyncio.run(run_tui())


@app.callback(invoke_without_command=True)
def _default(ctx: typer.Context):
    if ctx.invoked_subcommand is None:
        from financial_analyst.tui import run_tui
        asyncio.run(run_tui())


def main():
    app()


if __name__ == "__main__":
    main()
