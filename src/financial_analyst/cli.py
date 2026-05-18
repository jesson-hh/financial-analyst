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


@app.command()
def ingest(
    source: str = typer.Option(..., "--source", help="Source name from config/data_sources.yaml"),
    config: Path = typer.Option(
        Path("config/data_sources.yaml"),
        "--config",
        help="Path to data_sources.yaml",
    ),
    dry_run: bool = typer.Option(False, "--dry-run", help="Only discover, no writes"),
):
    """Ingest a configured data source into the Qlib binary format."""
    import yaml
    from financial_analyst.data.ingest import CsvIngester, AkshareIngester, YfinanceIngester

    if not config.exists():
        typer.echo(f"Config not found: {config}")
        raise typer.Exit(code=1)

    cfg = yaml.safe_load(config.read_text(encoding="utf-8"))
    sources = {s["name"]: s for s in cfg.get("sources", [])}
    if source not in sources:
        typer.echo(f"Unknown source: {source}. Available: {list(sources)}")
        raise typer.Exit(code=1)

    entry = sources[source]
    t = entry["type"]
    if t == "csv":
        ingester = CsvIngester(
            path_glob=entry["path"],
            code_col=entry.get("code_col"),
            date_col=entry.get("date_col", "trade_date"),
            date_format=entry.get("date_format"),
            ohlcv_map=entry.get("ohlcv_map"),
            per_code_filenames=entry.get("per_code_filenames", False),
        )
    elif t == "akshare":
        ingester = AkshareIngester(
            **{k: v for k, v in entry.items() if k not in {"name", "type", "target"}}
        )
    elif t == "yfinance":
        ingester = YfinanceIngester(
            **{k: v for k, v in entry.items() if k not in {"name", "type", "target"}}
        )
    else:
        typer.echo(f"Unknown source type: {t}")
        raise typer.Exit(code=1)

    summary = ingester.discover()
    typer.echo(f"Discover summary for source '{source}':")
    for k, v in summary.items():
        typer.echo(f"  {k}: {v}")

    if dry_run:
        typer.echo("(--dry-run: no writes)")
        return

    target = Path(entry.get("target", f"~/.financial-analyst/data/{source}")).expanduser()
    result = ingester.convert(target_root=target)
    typer.echo(f"Done: {result!r}")
    typer.echo(
        f"\nNext: set config/loaders.yaml `qlib_binary.provider_uri.day: {target}` "
        f"then `financial-analyst report <code>`."
    )


@app.command()
def dream(
    since: int = typer.Option(30, "--since", help="Days lookback for reports"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Discover only, don't write proposals"),
    out_dir: Path = typer.Option(Path("out"), "--out", help="Report output dir"),
):
    """Run the dream loop: introspect past reports, propose memory updates."""
    asyncio.run(_run_dream(since=since, dry_run=dry_run, out_dir=out_dir))


async def _run_dream(since: int, dry_run: bool, out_dir: Path):
    """Body of the dream command — also called by TUI /dream."""
    from financial_analyst.data.loader_factory import get_default_loader
    from financial_analyst.dream import OutcomeTracker, Introspector, write_proposals

    loader = get_default_loader()
    tracker = OutcomeTracker(loader=loader, out_dir=out_dir)
    outcomes = tracker.collect(since_days=since)

    typer.echo(f"Found {len(outcomes)} reports in last {since} days")
    verdict_counts: dict = {}
    for o in outcomes:
        verdict_counts[o.verdict] = verdict_counts.get(o.verdict, 0) + 1
    typer.echo(f"  verdicts: {verdict_counts}")

    if not outcomes:
        typer.echo("No reports to introspect. Run some `financial-analyst report <code>` first.")
        return

    wrong_or_partial = [o for o in outcomes if o.verdict in ("wrong", "partial")]
    if not wrong_or_partial:
        typer.echo("All outcomes correct or pending — nothing to propose.")
        return

    typer.echo(f"Introspecting {len(wrong_or_partial)} wrong/partial cases (via LLM)...")
    agent = Introspector(memory_root=Path("memories"))
    result = await agent.run({"outcomes": [o.to_dict() for o in outcomes]})

    if not result.ok:
        typer.echo(f"introspector failed: {result.error}")
        return

    proposals = result.output.proposals
    typer.echo(f"Generated {len(proposals)} proposals:")
    for p in proposals:
        typer.echo(f"  [{p.confidence}] {p.target_agent}/{p.topic_slug}: {p.title}")

    if dry_run:
        typer.echo("(--dry-run: no files written)")
        return

    written = write_proposals(proposals)
    typer.echo(f"Wrote {len(written)} proposals to memories/_proposed/")
    typer.echo("Review with `/memory list-proposals` then `/memory accept` or `/memory reject`.")


@app.callback(invoke_without_command=True)
def _default(ctx: typer.Context):
    if ctx.invoked_subcommand is None:
        from financial_analyst.tui import run_tui
        asyncio.run(run_tui())


def main():
    app()


if __name__ == "__main__":
    main()
