from __future__ import annotations
import asyncio
import sys
import typer
from pathlib import Path
from typing import Optional
from dotenv import load_dotenv
import pandas as pd

# On Windows zh-CN PowerShell, default stdout codec is GBK which chokes on ¥, emoji,
# and any rare CJK char. Force UTF-8 with replace-on-error so reports always render.
for stream in (sys.stdout, sys.stderr):
    try:
        stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

load_dotenv(override=True)  # populate os.environ from .env (overrides any existing shell vars)

# Load user plugins (user-private .py files that call ModelRegistry.register etc.)
# Errors here are non-fatal — a broken plugin won't prevent the CLI from working.
try:
    from financial_analyst.plugins import load_plugins
    load_plugins()
except Exception:
    pass

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


@app.command("models")
def models_cmd(
    action: str = typer.Argument("list", help="Subcommand: list"),
):
    """List registered quant models."""
    if action != "list":
        typer.echo(f"Unknown action: {action}. Try: list")
        raise typer.Exit(code=1)
    # Ensure built-in + user models registered
    from financial_analyst.models import ModelRegistry
    import financial_analyst.models  # triggers built-in registration via __init__

    names = ModelRegistry.names()
    if not names:
        typer.echo("(no models registered)")
        return
    typer.echo(f"{len(names)} registered model(s):")
    for n in names:
        try:
            inst = ModelRegistry.get_instance(n)
            meta = inst.metadata()
            typer.echo(f"  {n:<24} {meta}")
        except Exception as exc:
            typer.echo(f"  {n:<24} (metadata error: {exc})")


@app.command("loaders")
def loaders_cmd(
    action: str = typer.Argument("list", help="Subcommand: list"),
):
    """List configured data loaders + their type."""
    if action != "list":
        typer.echo(f"Unknown action: {action}. Try: list")
        raise typer.Exit(code=1)
    import yaml
    loaders_yaml = Path("config/loaders.yaml")
    if not loaders_yaml.exists():
        typer.echo(f"(no loaders config at {loaders_yaml})")
        return
    cfg = yaml.safe_load(loaders_yaml.read_text(encoding="utf-8"))
    default = cfg.get("default", "?")
    typer.echo(f"default loader: {default}")
    typer.echo("loaders configured:")
    for name, entry in (cfg.get("loaders") or {}).items():
        marker = "* " if name == default else "  "
        typer.echo(f"{marker}{name}: {entry}")


@app.command("agents")
def agents_cmd(
    action: str = typer.Argument("list", help="Subcommand: list"),
):
    """List registered sub-agents (15 built-in + any user-added)."""
    if action != "list":
        typer.echo(f"Unknown action: {action}. Try: list")
        raise typer.Exit(code=1)
    from financial_analyst.agent.registry import SubAgentRegistry
    from financial_analyst.tui import _ensure_registered
    _ensure_registered()
    names = SubAgentRegistry.names()
    typer.echo(f"{len(names)} registered sub-agent(s):")
    for n in names:
        if any(s in n for s in ["fetcher", "reader", "computer", "predictor"]):
            tier = "Tier 1"
        elif "analyst" in n:
            tier = "Tier 2"
        elif n == "introspector":
            tier = "Dream"
        elif "mainline" in n:
            tier = "Tier M"
        else:
            tier = "Tier 3"
        typer.echo(f"  {n:<24} {tier}")


@app.command("collectors")
def collectors_cmd(
    action: str = typer.Argument("list", help="Subcommand: list"),
):
    """List available news/F10 collector base classes + example implementations."""
    if action != "list":
        typer.echo(f"Unknown action: {action}. Try: list")
        raise typer.Exit(code=1)
    typer.echo("Available collector interfaces:")
    typer.echo("  BaseNewsCollector  (financial_analyst.data.collectors.news.base)")
    typer.echo("  BaseF10Collector   (financial_analyst.data.collectors.f10.base)")
    typer.echo("")
    typer.echo("Example implementations (under examples/):")
    examples_dir = Path("examples")
    if examples_dir.exists():
        for f in sorted(examples_dir.glob("custom_*collector*.py")):
            typer.echo(f"  {f}")
    typer.echo("")
    typer.echo("Define your own + register via config/plugins.yaml `load_at_startup`.")


@app.command(name="ask")
def ask_cmd(
    query: str = typer.Argument(..., help="Natural-language question"),
):
    """Ask the front-desk agent a question. Uses tools to search memory, read past reports, fetch quotes."""
    from financial_analyst.ask import ask
    from financial_analyst.tui import console
    from rich.markdown import Markdown

    output = asyncio.run(ask(query))
    console.print(Markdown(output.answer or "(no answer)"))
    if output.actions_taken:
        console.print(f"\n[dim]actions: {output.actions_taken}[/dim]")
    if output.references:
        console.print(f"[dim]references: {output.references}[/dim]")
    if output.needs_full_report and output.suggested_code:
        console.print(
            f"\n[yellow]This requires a full deep-dive. Run:[/yellow]\n"
            f"  financial-analyst report {output.suggested_code}"
        )


@app.command()
def mainline(
    asof: str = typer.Option(None, "--asof", help="As-of date YYYY-MM-DD (default: latest)"),
    panel: str = typer.Option(None, "--panel", help="Path to panel parquet"),
    out_dir: Path = typer.Option(Path("out"), "--out", help="Output directory"),
):
    """Generate monthly mainline radar brief (sector-level)."""
    asyncio.run(_run_mainline(asof=asof, panel=panel, out_dir=out_dir))


async def _run_mainline(asof: Optional[str], panel: Optional[str], out_dir: Path):
    from financial_analyst.agent.orchestrator import Orchestrator
    from financial_analyst.agent.registry import SubAgentRegistry
    from financial_analyst.swarm import load_preset
    from financial_analyst.tui import _ensure_registered

    _ensure_registered()
    nodes = load_preset("mainline-radar", memory_root=Path("memories"))
    asof = asof or pd.Timestamp.today().strftime("%Y-%m-%d")
    base_inputs = {"asof_date": asof, "out_dir": str(out_dir)}
    if panel:
        base_inputs["panel_path"] = panel

    orch = Orchestrator(nodes)
    typer.echo(f"Running mainline-radar for {asof}...")
    results = await orch.run(base_inputs)

    writer_result = results.get("mainline-writer")
    if writer_result and writer_result.ok:
        typer.echo(f"Brief: {writer_result.output.output_md_path}")
        typer.echo(f"Headline: {writer_result.output.headline}")
    else:
        for n, r in results.items():
            if not r.ok:
                typer.echo(f"[red]{n}: {r.error}[/red]")


@app.callback(invoke_without_command=True)
def _default(ctx: typer.Context):
    if ctx.invoked_subcommand is None:
        from financial_analyst.tui import run_tui
        asyncio.run(run_tui())


def main():
    app()


if __name__ == "__main__":
    main()
