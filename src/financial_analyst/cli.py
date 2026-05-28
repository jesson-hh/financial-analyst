from __future__ import annotations
import asyncio
import sys
import typer
from pathlib import Path
from typing import Optional
from financial_analyst.memory_paths import default_memory_root
from dotenv import load_dotenv
import pandas as pd

# On Windows zh-CN PowerShell, default stdout codec is GBK which chokes on ¥, emoji,
# and any rare CJK char. Force UTF-8 with replace-on-error so reports always render.
for stream in (sys.stdout, sys.stderr):
    try:
        stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# Three-pass .env loading (order = priority, last write wins via override=True):
#   1. cwd / parents — legacy behaviour, covers users running from a project dir
#      that has a local .env (e.g. devs in G:\financial-analyst).
#   2. canonical workspace location — where `fa init` actually writes for
#      pip-installed users (~/.financial-analyst/.env by default, or a pinned
#      D:\fa-workspace via the .workspace pointer file).
#   3. _project_root() / .env — the editable-install equivalent of #2.
#
# Bug fixed: without pass #2 / #3, a pip-installed user running `fa start`
# from C:\Users\<name> (default cmd cwd) would have load_dotenv() walk up
# without finding ~/.financial-analyst/.env. Result: os.environ missing
# DASHSCOPE_API_KEY / DEEPSEEK_API_KEY → /models returns 0 providers →
# the UI model picker hides itself and the user can't switch models.
load_dotenv(override=True)
try:
    from financial_analyst.workspace import get_workspace as _get_ws
    _ws_env = _get_ws() / ".env"
    if _ws_env.exists():
        load_dotenv(dotenv_path=_ws_env, override=True)
except Exception:
    pass
try:
    from financial_analyst.init_cli import _project_root as _pr
    _pr_env = _pr() / ".env"
    if _pr_env.exists():
        load_dotenv(dotenv_path=_pr_env, override=True)
except Exception:
    pass

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

# ``fa data <subcommand>`` — direct-connection incremental data update (pytdx main site + Tencent realtime)
from financial_analyst.data_cli import data_app
app.add_typer(data_app, name="data")

# ``fa init`` — first-launch wizard
from financial_analyst.init_cli import init_cmd
app.command(name="init", help="首次启动引导 — 配 LLM key + 选数据包 + 验证.")(init_cmd)

# ``fa start`` (primary) / ``fa launch`` (alias) — one-command workstation:
# detect existing services → fast-path browser, else wizard if first time + start backend + UI.
from financial_analyst.launch_cli import launch as _launch_cmd
app.command(name="start", help="一键启动工作台 — 第一次进引导, 之后直接开浏览器.")(_launch_cmd)
app.command(name="launch", help="`fa start` 的别名 (向后兼容).")(_launch_cmd)

# ``fa update`` — check PyPI for a newer release + pip install -U
from financial_analyst.update_cli import update_cmd as _update_cmd
app.command(name="update", help="一键从 PyPI 升级 financial-analyst 到最新版.")(_update_cmd)


@app.command()
def version():
    """Print version."""
    typer.echo(f"financial-analyst {__version__}")


@app.command()
def report(
    code: Optional[str] = typer.Argument(None, help="Stock code, e.g. SH600519 (or use -f for batch)"),
    asof: str = typer.Option(None, help="As-of date YYYY-MM-DD (default: today)"),
    out_dir: Path = typer.Option(Path("./out"), help="Output directory"),
    file: Optional[Path] = typer.Option(None, "--file", "-f", help="Read codes from file (one per line) for batch"),
    trace: bool = typer.Option(False, "--trace", help="Print per-agent timing table after run"),
    no_auto_aggregate: bool = typer.Option(
        False, "--no-auto-aggregate",
        help="Skip dream aggregate after batch finishes (default: auto-run when ≥5 new introspections)"),
):
    """Generate single-stock deep-dive report (one-shot).

    Batch mode (``-f file``): after all reports finish, automatically runs
    ``fa dream aggregate`` if ≥5 new introspections landed in
    ``memories/_pending_introspections/`` during this batch. Disable with
    ``--no-auto-aggregate``.
    """
    from financial_analyst.tui import run_report_oneshot

    codes: list[str] = []
    if code:
        codes.append(code)
    if file:
        text = file.read_text(encoding="utf-8")
        for line in text.splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                codes.append(line)
    if not codes:
        typer.echo("Error: provide a code as argument or use -f <file>")
        raise typer.Exit(code=1)

    # Snapshot the _pending_introspections/ count before the run (used to decide auto-aggregate)
    pending_dir = default_memory_root() / "_pending_introspections"
    n_before = len(list(pending_dir.glob("*.json"))) if pending_dir.exists() else 0

    completed = 0
    for c in codes:
        try:
            asyncio.run(run_report_oneshot(code=c, asof=asof, out_dir=out_dir, trace=trace))
            completed += 1
        except KeyboardInterrupt:
            typer.echo(f"\n[interrupted] cancelled report for {c}")
            break

    # Auto-aggregate hook: run when batch ≥ 5 completed + new introspections ≥ 5
    if not no_auto_aggregate and len(codes) >= 5 and completed >= 5:
        n_after = len(list(pending_dir.glob("*.json"))) if pending_dir.exists() else 0
        delta = n_after - n_before
        if delta >= 5:
            typer.echo(f"\n──────────────────────────────────────────────────")
            typer.echo(f"  本次 batch 新增 {delta} 份 introspection, "
                       f"自动跑 dream aggregate")
            typer.echo(f"──────────────────────────────────────────────────")
            try:
                from financial_analyst.dream.aggregator import aggregate_pending
                written, stats = aggregate_pending(
                    memory_root=default_memory_root(), min_count=3,
                    threshold=0.4, dry_run=False,
                )
                typer.echo(f"  扫 {stats.get('n_pending_files', 0)} 份 → "
                           f"{stats.get('clusters_promoted', 0)} 个 cluster 升级到 _proposed/")
                if stats.get("promoted_breakdown"):
                    for slug, info in stats["promoted_breakdown"].items():
                        typer.echo(f"    [{info['confidence']:>4}] {info['agent']}/{slug} "
                                   f"({info['n_cases']} cases)")
                if written:
                    typer.echo(f"\n  下一步: fa dream review  →  fa dream accept <agent>/<slug>")
            except Exception as e:
                typer.echo(f"  ⚠ auto-aggregate 失败 (不影响 batch 结果): "
                           f"{type(e).__name__}: {e}")
                typer.echo(f"  手动跑: fa dream aggregate")


@app.command()
def chat(
    legacy: bool = typer.Option(False, "--legacy",
                                 help="Use the old slash-command TUI."),
    simple: bool = typer.Option(False, "--simple",
                                 help="Use the v1.5 simple line-by-line REPL (no full-screen TUI)."),
):
    """Conversational chat — natural language drives the whole stack.

    Default (v1.6+) launches the full-TUI BuddyApp:
      - Persistent input box (type during agent thinking — queued)
      - Animated K-line spinner above the input
      - ESC cancels the running turn cleanly
      - Scrollable transcript

    Use --simple for the v1.5 line-by-line REPL (no full-screen).
    Use --legacy for the original slash-command TUI.

    Examples (input accepts Chinese natural language):
      ❯ what's the current price of SH600519 (Moutai)
      ❯ in csi300, which have PE<20 + dividend yield>3%
      ❯ how is the AI compute chain doing lately

    Slash commands: /help /reset /tools /save /quit.
    """
    if legacy:
        from financial_analyst.tui import run_tui
        asyncio.run(run_tui())
    elif simple:
        from financial_analyst.buddy import run_chat
        asyncio.run(run_chat())
    else:
        from financial_analyst.buddy.app import run_app
        asyncio.run(run_app())


@app.command()
def serve(
    host: str = typer.Option("127.0.0.1", "--host", help="Bind address."),
    port: int = typer.Option(9999, "--port", help="Port."),
):
    """Run the HTTP/SSE bridge for the desktop UI (觀瀾 Tauri app).

    Exposes the buddy agent over Server-Sent Events so the desktop
    front-end can drive the real tools instead of its mock.

    Endpoints: POST /run (SSE), POST /confirm, GET /health, GET /tools,
               GET /data/status, POST /data/refresh.
    """
    from financial_analyst.buddy.server import serve as _serve
    _serve(host=host, port=port)


@app.command()
def buddy():
    """Alias for `chat` — conversational front-end (full TUI)."""
    from financial_analyst.buddy.app import run_app
    asyncio.run(run_app())


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
    action: str = typer.Argument("run", help="One of: run | review | accept | reject"),
    target: Optional[str] = typer.Argument(None, help="For accept/reject: <agent>/<slug> (e.g. whale-analyst/dont-trust-VR-without-OBV)"),
    since: int = typer.Option(30, "--since", help="run: days lookback for reports"),
    dry_run: bool = typer.Option(False, "--dry-run", help="run: discover only, don't write proposals"),
    out_dir: Path = typer.Option(Path("out"), "--out", help="run: report output dir"),
):
    """Dream loop — introspect past reports + manage memory proposals.

    Subcommands:
      dream run                              — collect outcomes + propose memory updates (default)
      dream aggregate                        — cluster Tier-4 introspector pending proposals (>= 3 cases) → _proposed/
      dream review                           — list pending proposals under memories/_proposed/
      dream accept <agent>/<slug>            — promote a proposal into the agent's permanent memory
      dream reject <agent>/<slug>            — delete a proposal

    Examples:
      financial-analyst dream                                            # same as `dream run`
      financial-analyst dream aggregate                                  # cluster + promote _pending_introspections/
      financial-analyst dream aggregate --dry-run                        # preview only, no disk writes
      financial-analyst dream review
      financial-analyst dream accept whale-analyst/dont-trust-VR-without-OBV
      financial-analyst dream reject whale-analyst/dont-trust-VR-without-OBV
    """
    if action == "run":
        asyncio.run(_run_dream(since=since, dry_run=dry_run, out_dir=out_dir))
        return
    if action == "aggregate":
        _dream_aggregate(dry_run=dry_run)
        return
    if action == "review":
        _dream_review()
        return
    if action == "accept":
        if not target:
            typer.echo("dream accept requires <agent>/<slug>; run `dream review` to see options.")
            raise typer.Exit(1)
        _dream_promote(target, action="accept")
        return
    if action == "reject":
        if not target:
            typer.echo("dream reject requires <agent>/<slug>; run `dream review` to see options.")
            raise typer.Exit(1)
        _dream_promote(target, action="reject")
        return
    typer.echo(f"Unknown dream action {action!r}; "
               "use run | aggregate | review | accept | reject")
    raise typer.Exit(1)


def _dream_aggregate(dry_run: bool = False) -> None:
    """Tier-4 introspector pending → _proposed/ via Jaccard clustering.

    Scan memories/_pending_introspections/*.json, promote clusters with the
    same (target_agent + similar pattern) repeated >= 3 times to
    memories/_proposed/<agent>/<date>_<slug>.md.
    """
    from financial_analyst.dream.aggregator import aggregate_pending
    written, stats = aggregate_pending(
        memory_root=default_memory_root(), min_count=3, threshold=0.4,
        dry_run=dry_run,
    )
    typer.echo(f"扫 _pending_introspections/: "
               f"{stats.get('n_pending_files', 0)} 份, "
               f"{stats.get('n_proposals_total', 0)} 个 proposal")
    typer.echo(f"聚类: {stats.get('clusters_total', 0)} cluster 总数, "
               f"{stats.get('clusters_promoted', 0)} 升级 (>= 3 cases)")
    skipped = stats.get("skipped_unchanged", 0)
    if skipped:
        typer.echo(f"  跳过: {skipped} cluster (_proposed/ 已有同 slug + 同 cases)")
    if stats.get("promoted_breakdown"):
        typer.echo("升级 breakdown:")
        for slug, info in stats["promoted_breakdown"].items():
            typer.echo(f"  [{info['confidence']:>4}] {info['agent']}/{slug} "
                       f"({info['n_cases']} cases)")
    if dry_run:
        typer.echo("(dry-run, 未写盘)")
    else:
        typer.echo(f"\n写到 memories/_proposed/ ({len(written)} 份)")
        typer.echo("下一步: fa dream review  →  fa dream accept <agent>/<slug>")


def _dream_review() -> None:
    """List all proposals under memories/_proposed/ with frontmatter, supporting
    cases preview, and lesson_md head. Sorted by confidence high→med→low.
    """
    import yaml
    proposed_root = default_memory_root() / "_proposed"
    if not proposed_root.exists():
        typer.echo(f"No proposals directory at {proposed_root}. "
                   f"Run `dream run` 或 `dream aggregate` 先生成些 proposals.")
        return
    files = sorted(proposed_root.rglob("*.md"))
    if not files:
        typer.echo(f"{proposed_root} is empty. Run `dream run` or `dream aggregate` "
                   f"after accumulating a few reports.")
        return

    # Parse each one, collect (confidence_rank, agent, slug, fm, body_preview, path)
    parsed = []
    for f in files:
        try:
            text = f.read_text(encoding="utf-8")
            fm: dict = {}
            body = text
            if text.startswith("---\n"):
                end = text.find("\n---\n", 4)
                if end > 0:
                    fm = yaml.safe_load(text[4:end]) or {}
                    body = text[end + 5:].lstrip()
            agent = f.parent.name
            slug = f.stem.split("_", 1)[1] if "_" in f.stem else f.stem
            conf = fm.get("confidence", "low")
            # Sort weight: high=3, med=2, low=1, other=0
            conf_rank = {"high": 3, "med": 2, "low": 1}.get(conf, 0)
            # body preview: skip the # title line, take the first 3 lines of real content
            body_lines = [l for l in body.split("\n")
                          if l.strip() and not l.strip().startswith("#") and l.strip() != "---"]
            body_preview = "\n".join(body_lines[:3])[:280]
            parsed.append({
                "rank": conf_rank, "conf": conf, "agent": agent, "slug": slug,
                "fm": fm, "body_preview": body_preview, "path": f,
            })
        except Exception as e:
            typer.echo(f"  [err]  {f}: {e}")

    # Sort by confidence high→med→low
    parsed.sort(key=lambda x: (-x["rank"], x["agent"], x["slug"]))

    typer.echo(f"Pending proposals under {proposed_root} ({len(parsed)} total):\n")
    typer.echo(f"{'─' * 80}")

    for p in parsed:
        fm = p["fm"]
        title = fm.get("title", "(no title)")[:120]
        n_cases = len(fm.get("supporting_cases", []))
        # supporting cases preview (first 3)
        cases = fm.get("supporting_cases", [])[:3]
        cases_str = ", ".join(cases)
        if len(fm.get("supporting_cases", [])) > 3:
            cases_str += f", ... (+{len(fm.get('supporting_cases', [])) - 3} more)"

        typer.echo(f"  [{p['conf']:>4}] {p['agent']}/{p['slug']}  ({n_cases} cases)")
        typer.echo(f"         title:    {title}")
        if cases_str:
            typer.echo(f"         cases:    {cases_str}")
        if p["body_preview"]:
            for line in p["body_preview"].split("\n")[:3]:
                if line.strip():
                    typer.echo(f"         preview:  {line[:100]}")
        typer.echo(f"         file:     {p['path']}")
        typer.echo("")

    typer.echo(f"{'─' * 80}")
    typer.echo("To promote one:  financial-analyst dream accept <agent>/<slug>")
    typer.echo("To discard one:  financial-analyst dream reject <agent>/<slug>")


def _dream_promote(target: str, action: str) -> None:
    """Move (accept) or delete (reject) a proposal — delegates to ``memory_ops``.

    Every CLI accept/reject writes an audit entry with ``source="cli"`` to
    ``~/.financial-analyst/audit.jsonl``, alongside MCP accepts (source="mcp")
    and future UI accepts.
    """
    from financial_analyst.memory_ops import accept_proposal, reject_proposal

    if action == "accept":
        result = accept_proposal(target, source="cli", project_root=Path.cwd())
    elif action == "reject":
        result = reject_proposal(target, source="cli", project_root=Path.cwd())
    else:
        typer.echo(f"unknown action: {action!r}; expected 'accept' or 'reject'")
        raise typer.Exit(1)

    if "error" in result:
        typer.echo(result["error"])
        raise typer.Exit(1)

    if action == "accept":
        typer.echo(f"Accepted: {result['src']} → {result['dst']}")
        typer.echo(f"Audit id: {result['id']}")
        if not result.get("git_staged", False):
            git_err = result.get("git_error") or "(no detail)"
            typer.echo(f"(git stage skipped: {git_err})")
        agent = target.split("/", 1)[0]
        typer.echo(
            f"Next `financial-analyst report` call will use this rule in agent {agent!r}."
        )
    else:
        typer.echo(f"Rejected and deleted: {result['src']}")
        typer.echo(f"Audit id: {result['id']}")


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
    agent = Introspector(memory_root=default_memory_root())
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
    typer.echo("Next: `financial-analyst dream review` to list, then `dream accept <agent>/<slug>` or `dream reject <agent>/<slug>`.")


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
    query: Optional[str] = typer.Argument(None, help="Natural-language question (or use --file/stdin)"),
    file: Optional[Path] = typer.Option(None, "--file", "-f", help="Read query from file"),
):
    """Ask the front-desk agent a question. Uses tools to search memory, read past reports, fetch quotes."""
    from financial_analyst.ask import ask
    from financial_analyst.tui import console
    from rich.markdown import Markdown

    # Resolve query: argument > file > stdin
    if query is None and file is not None:
        query = file.read_text(encoding="utf-8").strip()
    if query is None and not sys.stdin.isatty():
        query = sys.stdin.read().strip()
    if not query:
        typer.echo("Error: provide a query as argument, with -f <file>, or via stdin")
        raise typer.Exit(code=1)

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
    nodes = load_preset("mainline-radar", memory_root=default_memory_root())
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


@app.command()
def brief(
    asof: str = typer.Option(None, "--asof", help="As-of date YYYY-MM-DD (default: today)"),
    universe: str = typer.Option("all", "--universe", help="Universe name (informational)"),
    universe_file: str = typer.Option(None, "--universe-file", help="Override instruments file path"),
    max_scan: int = typer.Option(5000, "--max-scan", help="Cap on stocks scanned"),
    out_dir: Path = typer.Option(Path("out"), "--out", help="Output directory"),
):
    """Generate daily A-share morning brief (market-wide anomaly scan)."""
    asyncio.run(_run_brief(asof=asof, universe=universe,
                            universe_file=universe_file, max_scan=max_scan, out_dir=out_dir))


async def _run_brief(asof, universe, universe_file, max_scan, out_dir):
    from financial_analyst.agent.orchestrator import Orchestrator
    from financial_analyst.agent.market.market_scanner import MarketScanner
    from financial_analyst.agent.market.morning_brief_writer import MorningBriefWriter
    from financial_analyst.agent.orchestrator import DAGNode
    from financial_analyst.tui import _ensure_registered

    _ensure_registered()
    # Build agents manually to pass universe_file + max_scan to scanner
    scanner = MarketScanner(memory_root=default_memory_root(),
                             universe_file=universe_file, max_scan=max_scan)
    writer = MorningBriefWriter(memory_root=default_memory_root())
    nodes = [
        DAGNode(agent=scanner, deps=[], input_keys=["asof_date", "universe"]),
        DAGNode(agent=writer, deps=["market-scanner"],
                input_keys=["market-scanner", "asof_date", "out_dir"]),
    ]
    asof = asof or pd.Timestamp.today().strftime("%Y-%m-%d")
    orch = Orchestrator(nodes)
    typer.echo(f"Scanning market for {asof}...")
    results = await orch.run({"asof_date": asof, "universe": universe, "out_dir": str(out_dir)})
    writer_result = results.get("morning-brief-writer")
    if writer_result and writer_result.ok:
        typer.echo(f"Brief: {writer_result.output.output_md_path}")
        typer.echo(f"Headline: {writer_result.output.headline}")
        if writer_result.output.watchlist_today:
            typer.echo(f"Watchlist: {writer_result.output.watchlist_today}")
    else:
        for n, r in results.items():
            if not r.ok:
                typer.echo(f"{n}: {r.error}")


@app.command(name="overseas-radar")
def overseas_radar(
    asof: str = typer.Option(None, "--asof", help="As-of date YYYY-MM-DD (default: today)"),
    out_dir: Path = typer.Option(Path("out"), "--out", help="Output directory"),
):
    """v1.9.7: International market + global news transmission radar.

    Pulls overnight US/HK indices via Tencent global, judges risk_tone,
    aggregates global news impact, fuses with today's A-share scanner,
    writes overseas_radar_<date>.md with actionable signals.
    """
    asyncio.run(_run_overseas_radar(asof=asof, out_dir=out_dir))


async def _run_overseas_radar(asof, out_dir):
    from financial_analyst.agent.orchestrator import Orchestrator
    from financial_analyst.swarm import load_preset
    from financial_analyst.tui import _ensure_registered

    _ensure_registered()
    nodes = load_preset("overseas-radar", memory_root=default_memory_root())
    asof = asof or pd.Timestamp.today().strftime("%Y-%m-%d")
    orch = Orchestrator(nodes)
    typer.echo(f"Running overseas-radar for {asof}...")
    results = await orch.run({"asof_date": asof, "out_dir": str(out_dir)})
    impact = results.get("macro-impact-analyzer")
    if impact and impact.ok:
        typer.echo(f"Report: {impact.output.output_md_path}")
        typer.echo(f"Headline: {impact.output.headline}")
        for s in impact.output.actionable_signals[:3]:
            typer.echo(f"  · {s.signal} ({s.confidence})")
    else:
        for n, r in results.items():
            if not r.ok:
                typer.echo(f"{n}: {r.error}")


@app.command()
def intraday(
    codes: str = typer.Option("", "--codes", help="Comma-separated codes (or empty to auto-detect from recent reports)"),
    asof: str = typer.Option(None, "--asof", help="As-of date YYYY-MM-DD (default: today)"),
    out_dir: Path = typer.Option(Path("out"), "--out", help="Output directory"),
):
    """Lunch-break intraday review: judge each stock OK / Caution / Exit."""
    asyncio.run(_run_intraday(codes=codes, asof=asof, out_dir=out_dir))


async def _run_intraday(codes: str, asof: Optional[str], out_dir: Path):
    from financial_analyst.agent.orchestrator import Orchestrator
    from financial_analyst.agent.market.intraday_reviewer import IntradayReviewer
    from financial_analyst.agent.orchestrator import DAGNode

    agent = IntradayReviewer(memory_root=default_memory_root())
    nodes = [DAGNode(agent=agent, deps=[], input_keys=["codes", "asof_date", "out_dir"])]
    asof = asof or pd.Timestamp.today().strftime("%Y-%m-%d")
    orch = Orchestrator(nodes)
    typer.echo(f"Reviewing intraday for {asof}...")
    results = await orch.run({"codes": codes, "asof_date": asof, "out_dir": str(out_dir)})
    rev_result = results.get("intraday-reviewer")
    if rev_result and rev_result.ok:
        typer.echo(f"Review: {rev_result.output.output_md_path}")
        typer.echo(f"Summary: {rev_result.output.summary}")
        typer.echo(f"N stocks: {rev_result.output.n_stocks}")
        for v in rev_result.output.verdicts[:10]:
            typer.echo(f"  [{v.verdict}] {v.code}: {v.reason}")
    else:
        for n, r in results.items():
            if not r.ok:
                typer.echo(f"{n}: {r.error}")


@app.command(name="news-collect")
def news_collect_cmd(
    sources: str = typer.Option(
        "kuaixun,longhu", "--sources",
        help="Comma list: kuaixun, longhu, holders, sinafinance, xueqiu-comments, xueqiu-hot, xueqiu-earnings, xueqiu-feed, xueqiu-hot-posts, xueqiu-watchlist, xueqiu-groups, xueqiu-fund, ths-hot, ths-fund-flow, ths-concept-fund, ths-industry-fund, ths-large-orders, ths-concept-board",
    ),
    code: str = typer.Option("", "--code", help="Filter to one stock (for holders/xueqiu-comments/xueqiu-earnings, required)"),
    limit: int = typer.Option(200, "--limit", help="Max items per source"),
):
    """Collect news/F10 from OpenCLI sources into ~/.financial-analyst/data/news.sqlite."""
    from financial_analyst.data.news_db import NewsDB
    from financial_analyst.data.collectors.opencli import (
        is_opencli_available, EastmoneyKuaixunCollector, EastmoneyLonghuCollector,
        EastmoneyHoldersCollector, SinafinanceNewsCollector,
        XueqiuCommentsCollector, XueqiuHotStockCollector, XueqiuEarningsCollector,
        XueqiuFeedCollector, XueqiuHotPostsCollector,
        XueqiuWatchlistCollector, XueqiuGroupsCollector,
        XueqiuFundSnapshotCollector, XueqiuFundHoldingsCollector,
        THSHotRankCollector,
        THSFundFlowCollector, THSConceptBoardCollector,
    )
    if not is_opencli_available():
        typer.echo("opencli not on PATH. Install: npm install -g @jackwener/opencli")
        raise typer.Exit(code=1)

    db = NewsDB()
    src_list = [s.strip() for s in sources.split(",") if s.strip()]
    totals = {}
    for src in src_list:
        try:
            if src == "kuaixun":
                items = EastmoneyKuaixunCollector().fetch(limit=limit)
                n = db.upsert_news(items, source="eastmoney_kuaixun")
                totals["kuaixun"] = n
            elif src == "longhu":
                items = EastmoneyLonghuCollector().fetch()
                n = db.upsert_lhb(items)
                totals["longhu"] = n
            elif src == "holders":
                if not code:
                    typer.echo("--code required for holders source")
                    continue
                items = EastmoneyHoldersCollector().fetch(code)
                n = db.upsert_holders(code, items)
                totals[f"holders_{code}"] = n
            elif src == "sinafinance":
                items = SinafinanceNewsCollector().fetch(limit=limit)
                n = db.upsert_news(items, source="sinafinance_news")
                totals["sinafinance"] = n
            elif src == "xueqiu-comments":
                if not code:
                    typer.echo("--code required for xueqiu-comments source")
                    continue
                items = XueqiuCommentsCollector().fetch(code, limit=limit)
                n = db.upsert_social_posts(code, items, source="xueqiu_comments")
                totals[f"xueqiu_comments_{code}"] = n
            elif src == "xueqiu-hot":
                items = XueqiuHotStockCollector().fetch(limit=limit)
                n = db.upsert_hot_stocks(items, source="xueqiu_hot_stock")
                totals["xueqiu_hot_stock"] = n
            elif src == "xueqiu-earnings":
                if not code:
                    typer.echo("--code required for xueqiu-earnings source")
                    continue
                items = XueqiuEarningsCollector().fetch(code)
                n = db.upsert_earnings_dates(items, source="xueqiu_earnings")
                totals[f"xueqiu_earnings_{code}"] = n
            elif src == "ths-hot":
                items = THSHotRankCollector().fetch(limit=limit)
                n = db.upsert_hot_stocks(items, source="ths_hot_rank")
                totals["ths_hot_rank"] = n
            elif src == "xueqiu-feed":
                items = XueqiuFeedCollector().fetch(limit=limit)
                n = db.upsert_news(items, source="xueqiu_feed")
                totals["xueqiu_feed"] = n
            elif src == "xueqiu-hot-posts":
                items = XueqiuHotPostsCollector().fetch(limit=limit)
                n = db.upsert_news(items, source="xueqiu_hot_posts")
                totals["xueqiu_hot_posts"] = n
            elif src == "xueqiu-watchlist":
                # Defaults to -1 (all groups). For a specific group pass
                # the pid in --code, e.g. `--code -5` for Shanghai+Shenzhen (A-share) only.
                pid = code if code and code.lstrip("-").isdigit() else "-1"
                items = XueqiuWatchlistCollector().fetch(pid=pid, limit=limit)
                n = db.upsert_watchlist(items, group_pid=pid)
                totals[f"xueqiu_watchlist_pid{pid}"] = n
            elif src == "xueqiu-groups":
                items = XueqiuGroupsCollector().fetch()
                n = db.upsert_groups(items)
                totals["xueqiu_groups"] = n
            elif src == "xueqiu-fund":
                # Combined: snapshot + holdings in one call. Both endpoints
                # need danjuan login; report the underlying error cleanly.
                try:
                    snap = XueqiuFundSnapshotCollector().fetch()
                    totals["xueqiu_fund_snapshot"] = db.upsert_fund_snapshot(snap)
                except Exception as exc:
                    typer.echo(f"[xueqiu-fund snapshot] {exc} (hint: log in to danjuanfunds.com)")
                try:
                    hold = XueqiuFundHoldingsCollector().fetch()
                    totals["xueqiu_fund_holdings"] = db.upsert_fund_holdings(hold)
                except Exception as exc:
                    typer.echo(f"[xueqiu-fund holdings] {exc} (hint: log in to danjuanfunds.com)")
            elif src == "ths-fund-flow":
                items = THSFundFlowCollector().fetch(target="gegu", limit=limit)
                totals["ths_fund_flow_gegu"] = db.upsert_ths_fund_flow(items)
            elif src == "ths-concept-fund":
                items = THSFundFlowCollector().fetch(target="gainian", limit=limit)
                totals["ths_fund_flow_gainian"] = db.upsert_ths_fund_flow(items)
            elif src == "ths-industry-fund":
                items = THSFundFlowCollector().fetch(target="hangye", limit=limit)
                totals["ths_fund_flow_hangye"] = db.upsert_ths_fund_flow(items)
            elif src == "ths-large-orders":
                items = THSFundFlowCollector().fetch(target="ddzz", limit=limit)
                totals["ths_fund_flow_ddzz"] = db.upsert_ths_fund_flow(items)
            elif src == "ths-concept-board":
                # Default mode=new (newly minted concepts). Pass --code "rank"
                # to switch to the ranked leaderboard (URL not fully verified).
                mode = code if code in ("new", "rank") else "new"
                items = THSConceptBoardCollector().fetch(mode=mode, limit=limit)
                totals[f"ths_concept_boards_{mode}"] = db.upsert_ths_concept_boards(items)
            else:
                typer.echo(f"Unknown source: {src} (try kuaixun/longhu/holders/sinafinance/xueqiu-comments/xueqiu-hot/xueqiu-earnings/xueqiu-feed/xueqiu-hot-posts/xueqiu-watchlist/xueqiu-groups/xueqiu-fund/ths-hot)")
        except Exception as exc:
            typer.echo(f"[{src}] failed: {exc}")
    db.close()
    typer.echo("Collected:")
    for k, v in totals.items():
        typer.echo(f"  {k}: {v} rows")


@app.command(name="news-query")
def news_query_cmd(
    code: str = typer.Argument(..., help="Stock code (or 'all')"),
    days: int = typer.Option(7, "--days"),
    limit: int = typer.Option(20, "--limit"),
    fts: str = typer.Option("", "--fts", help="Full-text search query"),
):
    """Query the local news DB."""
    from financial_analyst.data.news_db import NewsDB
    db = NewsDB()
    if fts:
        rows = db.search_news(fts, limit=limit)
    elif code == "all":
        rows = db.query_news(since_days=days, limit=limit)
    else:
        rows = db.query_news(code=code, since_days=days, limit=limit)
    db.close()
    if not rows:
        typer.echo("(no results)")
        return
    for r in rows:
        typer.echo(f"\n[{r['source']}] {r['ts']}  {r['title']}")
        if r.get("content"):
            typer.echo((r["content"] or "")[:200])


@app.command(name="news-stats")
def news_stats_cmd():
    """Show NewsDB row counts."""
    from financial_analyst.data.news_db import NewsDB
    db = NewsDB()
    stats = db.stats()
    db.close()
    typer.echo("Local news DB stats:")
    for k, v in stats.items():
        typer.echo(f"  {k}: {v}")


@app.command()
def doctor():
    """Diagnose financial-analyst environment (OpenCLI / Chrome / paths)."""
    import shutil
    import os
    typer.echo("=== financial-analyst doctor ===\n")

    # Python
    typer.echo(f"Python: {sys.version.split()[0]}")
    typer.echo(f"financial-analyst: {__version__}\n")

    # Env vars
    typer.echo("Env vars:")
    for key in ("TUSHARE_TOKEN", "DASHSCOPE_API_KEY", "ANTHROPIC_API_KEY",
                "FA_CACHE_DIR", "FA_UNIVERSE_FILE", "FA_MAINLINE_PANEL"):
        val = os.environ.get(key, "")
        marker = "v" if val else "x"
        masked = (val[:6] + "..." if len(val) > 8 else val) if val else "(unset)"
        typer.echo(f"  {marker} {key}: {masked}")
    typer.echo("")

    # OpenCLI
    typer.echo("OpenCLI:")
    opencli_path = shutil.which("opencli")
    if opencli_path:
        typer.echo(f"  v opencli on PATH: {opencli_path}")
        # Try public command
        try:
            from financial_analyst.data.collectors.opencli import run_opencli
            test = run_opencli("eastmoney", "kuaixun", "--limit", "1", timeout=15)
            if isinstance(test, list) and test:
                typer.echo("  v opencli eastmoney kuaixun: working")
            else:
                typer.echo("  ~ opencli eastmoney kuaixun: returned empty (check network)")
        except Exception as exc:
            typer.echo(f"  x opencli public test failed: {exc}")
        # Try xueqiu (cookie-mode)
        try:
            from financial_analyst.data.collectors.opencli import XueqiuHotStockCollector
            test = XueqiuHotStockCollector().fetch(limit=1)
            if isinstance(test, list) and test:
                typer.echo("  v opencli xueqiu hot-stock: working (cookie OK)")
            else:
                typer.echo("  ~ opencli xueqiu hot-stock: returned empty (Chrome ext not installed / not logged in)")
        except Exception as exc:
            typer.echo(f"  ~ opencli xueqiu test failed: {exc}")
    else:
        typer.echo("  x opencli not on PATH. Install: npm install -g @jackwener/opencli")
    typer.echo("")

    # NewsDB
    typer.echo("NewsDB:")
    try:
        from financial_analyst.data.news_db import NewsDB, DEFAULT_DB_PATH
        db = NewsDB()
        stats = db.stats()
        db.close()
        typer.echo(f"  v DB path: {DEFAULT_DB_PATH}")
        for k, v in stats.items():
            typer.echo(f"    {k}: {v}")
    except Exception as exc:
        typer.echo(f"  x NewsDB error: {exc}")

    # Data loaders
    typer.echo("\nData loaders:")
    try:
        from financial_analyst.data.loader_factory import get_default_loader
        loader = get_default_loader()
        typer.echo(f"  v default loader: {type(loader).__name__}")
    except Exception as exc:
        typer.echo(f"  x default loader: {exc}")

    typer.echo("\n=== done ===")


@app.command(name="chain")
def chain_cmd(
    action: str = typer.Argument(..., help="One of: list | show | for | import | stats"),
    target: Optional[str] = typer.Argument(None, help="For show: product_id. For `for`: stock code. For import: source dir."),
    overwrite: bool = typer.Option(False, "--overwrite", help="import: overwrite existing files"),
):
    """Industry-chain knowledge base — query product graph + stock memberships.

    Examples:
      financial-analyst chain list                              # all product node_ids
      financial-analyst chain show AI_chip_GPU                  # one product's full content
      financial-analyst chain for SH688256                      # which products this stock is in
      financial-analyst chain import G:/stocks/strategy/chain_kb/products
      financial-analyst chain stats
    """
    from financial_analyst.data.loaders.chain_kb import ChainKBLoader
    loader = ChainKBLoader()

    if action == "list":
        products = loader.list_products()
        if not products:
            typer.echo(f"No products found under {loader.root}.")
            typer.echo("Run `chain import <source-dir>` first.")
            return
        cats = loader.list_categories()
        typer.echo(f"{len(products)} products across {len(cats)} categories under {loader.root}:\n")
        for cat in cats:
            cat_products = [p for p in products if (loader.get(p) and loader.get(p).category == cat)]
            typer.echo(f"  [{cat}] ({len(cat_products)})")
            for pid in cat_products:
                p = loader.get(pid)
                typer.echo(f"    - {pid:35s}  {p.display_name}  ({len(p.related_codes)} codes, layer={p.layer})")
            typer.echo("")
        return

    if action == "show":
        if not target:
            typer.echo("chain show requires a product_id (e.g. AI_chip_GPU)")
            raise typer.Exit(1)
        prod = loader.get(target)
        if prod is None:
            typer.echo(f"Product {target!r} not found. Run `chain list` to see options.")
            raise typer.Exit(1)
        typer.echo(f"# {prod.node_id}  ({prod.display_name})\n")
        typer.echo(f"category: {prod.category}")
        typer.echo(f"layer:    {prod.layer}")
        typer.echo(f"summary:  {prod.summary}\n")
        typer.echo(f"upstream:   {prod.upstream_products}")
        typer.echo(f"downstream: {prod.downstream_products}\n")
        typer.echo(f"related_codes ({len(prod.related_codes)}):")
        for r in prod.related_codes:
            typer.echo(f"  {r.code:10s} {r.name:12s} {r.role:18s} weight={r.weight:+.2f}  {r.note}")
        if prod.body_md:
            typer.echo(f"\n--- body ---\n{prod.body_md[:1500]}")
        return

    if action == "for":
        if not target:
            typer.echo("chain for requires a stock code (e.g. SH688256)")
            raise typer.Exit(1)
        ctx = loader.chain_context(target)
        if ctx is None:
            typer.echo(f"No chain membership found for {target}.")
            raise typer.Exit(1)
        pp = ctx["primary_product"]
        typer.echo(f"Stock {target} → primary product: {pp['id']} ({pp['display_name']})")
        typer.echo(f"  Chain: {pp['category']}  layer={pp['layer']}")
        typer.echo(f"  Role: {pp['role_for_stock']} weight={pp['weight_for_stock']:+.2f}")
        typer.echo(f"  Summary: {pp['summary']}\n")
        if len(ctx["all_products"]) > 1:
            typer.echo(f"Also appears in:")
            for p in ctx["all_products"][1:]:
                typer.echo(f"  - {p['id']:30s} {p['display_name']:20s} ({p['role_for_stock']}, w={p['weight_for_stock']:+.2f})")
            typer.echo("")
        typer.echo(f"Upstream products:   {ctx['upstream_products']}")
        typer.echo(f"Downstream products: {ctx['downstream_products']}\n")
        typer.echo(f"Peer codes in {pp['id']} ({len(ctx['peer_codes'])}):")
        for r in ctx["peer_codes"]:
            typer.echo(f"  {r['code']:10s} {r['name']:12s} {r['role']:18s} weight={r['weight']:+.2f}")
        if ctx["catalyst_md"]:
            typer.echo(f"\n--- catalyst ---\n{ctx['catalyst_md'][:800]}")
        return

    if action == "import":
        if not target:
            typer.echo("chain import requires a source directory path.")
            raise typer.Exit(1)
        from pathlib import Path as _P
        try:
            n = loader.import_from(_P(target), overwrite=overwrite)
        except FileNotFoundError as e:
            typer.echo(str(e))
            raise typer.Exit(1)
        loader.reload()
        total = len(loader.list_products())
        typer.echo(f"Imported {n} new chain products from {target} → {loader.root}")
        typer.echo(f"Total now: {total} products across {len(loader.list_categories())} categories")
        if not overwrite:
            typer.echo("(use --overwrite to replace existing files)")
        return

    if action == "stats":
        s = loader.stats()
        for k, v in s.items():
            typer.echo(f"  {k}: {v}")
        return

    typer.echo(f"Unknown action {action!r}; use list / show / for / import / stats")
    raise typer.Exit(1)


@app.command(name="stocks")
def stocks_cmd(
    action: str = typer.Argument(..., help="One of: list | show | import | stats"),
    target: Optional[str] = typer.Argument(None, help="For show: stock code. For import: source directory."),
    overwrite: bool = typer.Option(False, "--overwrite", help="import: overwrite existing files"),
    tail: int = typer.Option(4000, "--tail", help="show: max chars to display (default 4000)"),
):
    """Manage per-stock research timeline files (~/.financial-analyst/memories/stocks/).

    Examples:
      financial-analyst stocks list
      financial-analyst stocks show SH600519
      financial-analyst stocks import G:/stocks/strategy/stocks
      financial-analyst stocks stats
    """
    from financial_analyst.data.loaders.stock_timeline import StockTimelineLoader
    loader = StockTimelineLoader()

    if action == "list":
        codes = loader.list_codes()
        if not codes:
            typer.echo(f"No timelines found under {loader.root}.")
            typer.echo("Run `stocks import <source-dir>` to bulk import per-stock markdown files.")
            return
        typer.echo(f"{len(codes)} stocks with timelines under {loader.root}:")
        for c in codes:
            size = loader.path_for(c).stat().st_size
            typer.echo(f"  {c}  ({size:,} bytes)")
        return

    if action == "show":
        if not target:
            typer.echo("stocks show requires a stock code (e.g. SH600519)")
            raise typer.Exit(1)
        text = loader.load_tail(target, max_chars=tail)
        if text is None:
            typer.echo(f"No timeline for {target} under {loader.root}.")
            raise typer.Exit(1)
        typer.echo(text)
        return

    if action == "import":
        if not target:
            typer.echo("stocks import requires a source directory path.")
            raise typer.Exit(1)
        from pathlib import Path as _P
        try:
            n = loader.import_from(_P(target), overwrite=overwrite)
        except FileNotFoundError as e:
            typer.echo(str(e))
            raise typer.Exit(1)
        existing = len(loader.list_codes())
        typer.echo(f"Imported {n} new stock timelines from {target} → {loader.root}")
        typer.echo(f"Total now: {existing} codes")
        if not overwrite:
            typer.echo("(use --overwrite to replace existing files)")
        return

    if action == "stats":
        s = loader.stats()
        for k, v in s.items():
            typer.echo(f"  {k}: {v}")
        return

    typer.echo(f"Unknown action {action!r}; use list / show / import / stats")
    raise typer.Exit(1)


@app.command(name="industry")
def industry_cmd(
    action: str = typer.Argument(..., help="One of: refresh | show | stats"),
    code: Optional[str] = typer.Argument(None, help="For show: stock code, e.g. SH600519"),
):
    """Manage the industry classifier cache.

    Examples:
      financial-analyst industry refresh        # pull from Tushare, write parquet
      financial-analyst industry show SH600519  # debug one code
      financial-analyst industry stats          # cache overview
    """
    from financial_analyst.data.loaders.industry import IndustryLoader
    loader = IndustryLoader()

    if action == "refresh":
        typer.echo("Refreshing industry classifier from Tushare stock_basic...")
        n = loader.refresh_from_tushare()
        typer.echo(f"Wrote {n} codes.")
        typer.echo(f"Stats: {loader.stats()}")
        return

    if action == "show":
        if not code:
            typer.echo("industry show requires a stock code (e.g. SH600519)")
            raise typer.Exit(1)
        typer.echo(f"{code}: {loader.get(code)}")
        return

    if action == "stats":
        typer.echo(f"Cache path: {loader._cache_path}")
        for k, v in loader.stats().items():
            typer.echo(f"  {k}: {v}")
        return

    typer.echo(f"Unknown action {action!r}; use refresh / show / stats")
    raise typer.Exit(1)


@app.command(name="alpha")
def alpha_cmd(
    action: str = typer.Argument(..., help="One of: list | show | bench | snapshot"),
    target: Optional[str] = typer.Argument(None, help="For show: alpha name. For bench: family slug. For snapshot: 'top10' / 'auto' / comma-list."),
    universe: str = typer.Option("csi300", "--universe", "-u", help="Universe file or named universe (csi300/csi500/all)"),
    since: str = typer.Option("2024-01-01", "--since", help="Start date YYYY-MM-DD"),
    until: str = typer.Option("2024-12-31", "--until", help="End date YYYY-MM-DD"),
    fwd_days: int = typer.Option(5, "--fwd-days", "-n", help="Forward-return horizon for IC"),
    top: int = typer.Option(20, "--top", "-k", help="Show top-K rows by |rank_IR|"),
    save: bool = typer.Option(False, "--save", help="Bench only: persist CSV to cache for snapshot --auto"),
    top_n: int = typer.Option(20, "--top-n", help="Snapshot --auto: how many top alphas to pick from latest bench"),
):
    """Alpha zoo — list / inspect / bench formulaic alphas.

    Examples:
      financial-analyst alpha list
      financial-analyst alpha list gtja191
      financial-analyst alpha show alpha001
      financial-analyst alpha bench gtja191 --universe csi300 --since 2024-06-01
    """
    from financial_analyst.factors.zoo import list_alphas, get, families
    from rich.console import Console
    from rich.table import Table

    console = Console()

    if action == "list":
        fam = target  # may be None
        rows = list_alphas(family=fam)
        if not rows:
            typer.echo(f"No alphas registered for family={fam!r}. Known: {families()}")
            raise typer.Exit(1)
        table = Table(title=f"Alpha Zoo — {fam or 'all families'} ({len(rows)} alphas)")
        table.add_column("name", style="cyan", no_wrap=True)
        table.add_column("family", style="green")
        table.add_column("description")
        for a in rows:
            table.add_row(a.name, a.family, a.description)
        console.print(table)
        return

    if action == "show":
        if not target:
            typer.echo("alpha show requires an alpha name (e.g. alpha001)")
            raise typer.Exit(1)
        try:
            spec = get(target)
        except KeyError as e:
            typer.echo(str(e))
            raise typer.Exit(1)
        console.print(f"[bold cyan]{spec.name}[/]  [green]({spec.family})[/]")
        console.print(f"[dim]{spec.paper}[/]" if spec.paper else "")
        console.print(f"\n[bold]Description:[/] {spec.description}")
        console.print(f"\n[bold]Formula:[/]")
        console.print(f"  {spec.formula_text}")
        return

    if action == "snapshot":
        # Build a universe-wide snapshot of curated top alphas at one asof
        # date and cache it for fast per-stock lookup in reports.
        codes = _resolve_universe(universe)
        if not codes:
            typer.echo(f"Universe {universe!r} produced 0 codes — aborting.")
            raise typer.Exit(1)
        asof = until  # reuse --until as the snapshot anchor date
        typer.echo(f"Building snapshot: universe={universe!r} ({len(codes)} codes), asof={asof}")
        from financial_analyst.data.loader_factory import get_default_loader
        from financial_analyst.data.loaders.industry import IndustryLoader, industry_map_path
        from financial_analyst.factors.zoo.snapshot import (
            build_snapshot, snapshot_path, PRODUCTION_TOP10,
        )
        from financial_analyst.factors.zoo.selector import (
            load_latest_bench, select_top_alphas, alpha_metadata_from_bench,
        )
        loader = get_default_loader()
        ind_loader = IndustryLoader() if industry_map_path().exists() else None

        # Resolve alpha names. Priority: --auto > comma-list > top10 keyword > PRODUCTION_TOP10.
        names: Optional[list[str]] = None
        bench_metadata: dict = {}
        if target == "auto":
            bench_df = load_latest_bench(universe)
            if bench_df is None:
                typer.echo(f"No cached bench for universe={universe!r}. Run `alpha bench --universe {universe} --save` first.")
                raise typer.Exit(1)
            names = select_top_alphas(bench_df, n=top_n)
            bench_metadata = alpha_metadata_from_bench(bench_df, names)
            typer.echo(f"--auto: picked {len(names)} alphas from latest bench (top-{top_n} by |rank_IR|):")
            for nm in names:
                meta = bench_metadata.get(nm, {})
                ric = meta.get("bench_rank_ic")
                hr = meta.get("bench_hit_rate")
                typer.echo(f"  {nm:18s}  bench_rank_ic={ric:+.4f}  hit={hr:.1%}" if ric is not None else f"  {nm}")
        elif target and "," in target:
            names = [s.strip() for s in target.split(",") if s.strip()]
            typer.echo(f"Using custom alpha set ({len(names)} alphas): {', '.join(names)}")
        elif target in (None, "top10", "default"):
            names = None  # falls through to PRODUCTION_TOP10
            typer.echo(f"Using PRODUCTION_TOP10 ({len(PRODUCTION_TOP10)} alphas): {', '.join(PRODUCTION_TOP10)}")
        else:
            # Unrecognised single token — treat as a single alpha name
            names = [target]
            typer.echo(f"Using single alpha: {target}")

        df = build_snapshot(loader, codes, asof, names=names, industry_loader=ind_loader,
                            bench_metadata=bench_metadata)
        out_path = snapshot_path(universe, asof)
        df.to_parquet(out_path, index=False)
        typer.echo(f"Wrote {out_path} ({len(df)} rows)")
        # Quick summary: per-alpha n_obs
        if "alpha" in df.columns:
            summary = df.groupby("alpha")["n_obs"].max().sort_values(ascending=False)
            typer.echo("Per-alpha n_obs (max across universe):")
            for alpha, n in summary.items():
                typer.echo(f"  {alpha}: {n}")
        return

    if action == "bench":
        # Resolve universe: named ("csi300") or path to a text file with one code per line
        codes = _resolve_universe(universe)
        if not codes:
            typer.echo(f"Universe {universe!r} produced 0 codes — aborting.")
            raise typer.Exit(1)
        typer.echo(f"Loading {len(codes)} codes from universe {universe!r} ({since} → {until})...")
        from financial_analyst.data.loader_factory import get_default_loader
        from financial_analyst.factors.zoo import PanelData
        from financial_analyst.factors.zoo.bench_runner import run_bench
        loader = get_default_loader()
        # v1.4.0: auto-load IndustryLoader so IndNeutralize alphas work
        from financial_analyst.data.loaders.industry import IndustryLoader, industry_map_path
        ind_loader = IndustryLoader() if industry_map_path().exists() else None
        if ind_loader:
            typer.echo("Industry classifier cache found — IndNeutralize alphas will use it.")
        else:
            typer.echo("No industry cache (run `industry refresh`); IndNeutralize alphas will demean to 0.")
        # v1.4.6: auto-load BenchmarkLoader so gtja149 (downside beta) works
        from financial_analyst.data.loaders.benchmark import BenchmarkLoader
        try:
            bench_loader = BenchmarkLoader(loader=loader)  # csi300 default
            typer.echo(f"Benchmark loader: {bench_loader.benchmark_key} ({bench_loader.benchmark_code}) — gtja149 enabled.")
        except Exception as e:
            bench_loader = None
            typer.echo(f"BenchmarkLoader not available ({e}); gtja149 will return NaN.")
        panel = PanelData.from_loader(
            loader, codes, since, until, freq="day",
            industry_loader=ind_loader, benchmark_loader=bench_loader,
        )
        typer.echo(f"Panel ready: {panel}")
        # Previous bench (if any) → run_bench uses it to flag reversed (rank_ic sign flip)
        prev_bench = None
        try:
            from financial_analyst.factors.zoo.selector import bench_csv_path
            _pp = bench_csv_path(universe)
            if _pp.exists():
                prev_bench = pd.read_csv(_pp)
                typer.echo(f"Prev bench found ({_pp.name}) — will flag 反向(reversed) on rank_ic sign flips.")
        except Exception:
            prev_bench = None
        typer.echo(f"Benching family={target or '<all>'}, fwd_days={fwd_days}...")
        results = run_bench(panel, family=target, fwd_days=fwd_days, prev_bench=prev_bench)

        table = Table(title=f"Alpha Bench — {target or 'all'} / fwd_{fwd_days}d / {len(codes)} codes")
        for c in ["name", "family", "ic", "rank_ic", "ir", "rank_ir", "hit_rate", "n_dates", "state"]:
            table.add_column(c, justify="right" if c not in ("name", "family", "state") else "left",
                             style="cyan" if c == "name" else "green" if c == "family" else "")
        for _, row in results.head(top).iterrows():
            table.add_row(
                row["name"], row["family"],
                f"{row['ic']:+.4f}" if pd.notna(row["ic"]) else "—",
                f"{row['rank_ic']:+.4f}" if pd.notna(row["rank_ic"]) else "—",
                f"{row['ir']:+.3f}" if pd.notna(row["ir"]) else "—",
                f"{row['rank_ir']:+.3f}" if pd.notna(row["rank_ir"]) else "—",
                f"{row['hit_rate']:.1%}" if pd.notna(row["hit_rate"]) else "—",
                str(int(row["n_dates"])),
                str(row.get("state", "")),
            )
        console.print(table)

        errors = results[results["status"] != "ok"]
        if len(errors):
            console.print(f"\n[red]{len(errors)} alphas had errors:[/]")
            for _, row in errors.iterrows():
                console.print(f"  {row['name']} ({row['family']}): {row['error']}")

        # v1.4.2: --save persists CSV to canonical cache so `snapshot --auto`
        # can pick it up.
        if save:
            from financial_analyst.factors.zoo.selector import bench_csv_path
            cache_path = bench_csv_path(universe)
            results.to_csv(cache_path, index=False)
            console.print(f"\n[green]Saved bench CSV to {cache_path} for snapshot --auto[/]")
        return

    typer.echo(f"Unknown action {action!r}; use list / show / bench")
    raise typer.Exit(1)


def _resolve_universe(universe: str) -> list[str]:
    """Resolve universe string to a list of stock codes.

    Accepts:
    - A path to a text file (one code per line, ``#`` for comments)
    - A named universe: ``csi300``, ``csi500``, ``all`` — looked up under
      ``~/.financial-analyst/universes/<name>.txt`` then ``config/universes/<name>.txt``
    """
    from pathlib import Path
    p = Path(universe)
    if p.exists():
        candidate = p
    else:
        home_path = Path.home() / ".financial-analyst" / "universes" / f"{universe}.txt"
        repo_path = Path(__file__).parent.parent.parent / "config" / "universes" / f"{universe}.txt"
        for path in (home_path, repo_path):
            if path.exists():
                candidate = path
                break
        else:
            return []
    codes = []
    for line in candidate.read_text(encoding="utf-8").splitlines():
        line = line.strip().split("#", 1)[0].strip()
        if line:
            codes.append(line)
    return codes


@app.callback(invoke_without_command=True)
def _default(ctx: typer.Context,
              tui: bool = typer.Option(False, "--tui",
                                        help="Use the terminal TUI instead of the web UI launcher.")):
    """Default behaviour with no subcommand: one-command web launcher.

    Run ``financial-analyst`` (or ``fa``) bare → kicks off ``fa launch`` —
    detects config, runs init wizard if needed, starts backend + UI, opens
    browser. Drop into the terminal TUI with ``fa --tui`` instead.
    """
    if ctx.invoked_subcommand is None:
        if tui:
            from financial_analyst.tui import run_tui
            asyncio.run(run_tui())
        else:
            # Use run_default() not launch() — the latter has typer.Option sentinels
            # as defaults, which leak in when invoked outside the typer dispatcher.
            from financial_analyst.launch_cli import run_default
            run_default()


def main():
    app()


if __name__ == "__main__":
    main()
