"""``fa knowledge`` typer sub-app — build / search / stats commands.

Wired into the main ``fa`` CLI via ``cli.py``::

    fa knowledge build              # incremental
    fa knowledge build --force      # full rebuild
    fa knowledge search "反转 失效" --k 5
    fa knowledge stats              # n_chunks + paths
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer

app = typer.Typer(
    name="knowledge",
    help="Semantic knowledge index over strategy/ markdown (chunk → BGE-zh → Chroma).",
    no_args_is_help=True,
)


def _build_index(strategy_root: Optional[Path], index_root: Optional[Path]):
    """Factory for the default ``KnowledgeIndex`` — uses BGE embedder."""
    from .search import KnowledgeIndex

    return KnowledgeIndex(strategy_root=strategy_root, index_root=index_root)


@app.command("build")
def build_cmd(
    force: bool = typer.Option(False, "--force", help="Re-embed every chunk, even unchanged ones."),
    strategy_root: Optional[Path] = typer.Option(
        None, "--strategy-root", help="Override strategy MD root (else DataPaths default)."
    ),
    index_root: Optional[Path] = typer.Option(
        None, "--index-root", help="Override chroma store root (else DataPaths default)."
    ),
):
    """Build / refresh the knowledge index.

    Default is incremental (mtime-based — unchanged chunks are skipped).
    Pass ``--force`` to re-embed everything (e.g. after switching embedder)."""
    idx = _build_index(strategy_root, index_root)
    stats = idx.build(force=force)
    d = stats.as_dict()
    typer.echo(f"Knowledge index build complete:")
    typer.echo(f"  strategy_root: {idx.strategy_root}")
    typer.echo(f"  index_root:    {idx.index_root}")
    typer.echo(f"  files_scanned: {d['files_scanned']}")
    typer.echo(f"  chunks_seen:   {d['chunks_seen']}")
    typer.echo(f"  embedded:      {d['chunks_embedded']}")
    typer.echo(f"  skipped:       {d['chunks_skipped_unchanged']} (unchanged)")
    typer.echo(f"  deleted:       {d['chunks_deleted_stale']} (stale)")
    typer.echo(f"  empty files:   {d['files_skipped_empty']}")
    if d.get("errors"):
        typer.echo(f"  errors:")
        for e in d["errors"]:
            typer.echo(f"    - {e}")


@app.command("search")
def search_cmd(
    query: str = typer.Argument(..., help="Natural-language query (e.g. '反转因子 失效场景')."),
    k: int = typer.Option(5, "--k", help="Number of top results to return."),
    strategy_root: Optional[Path] = typer.Option(
        None, "--strategy-root", help="Override strategy MD root."
    ),
    index_root: Optional[Path] = typer.Option(
        None, "--index-root", help="Override chroma store root."
    ),
    preview: int = typer.Option(240, "--preview", help="Chars of chunk text to show inline."),
):
    """Search the index. Returns top-K matching chunks with scores."""
    idx = _build_index(strategy_root, index_root)
    results = idx.search(query, k=k)
    if not results:
        typer.echo("(no results — try `fa knowledge build` first, or rephrase the query)")
        return
    typer.echo(f"Top {len(results)} results for {query!r}:\n")
    for i, r in enumerate(results, 1):
        body = r.text.strip().replace("\n", " ")
        if len(body) > preview:
            body = body[:preview] + "..."
        typer.echo(f"  {i:>2}. [score={r.score:.4f}]  {r.source}  §{r.section}")
        typer.echo(f"      {body}")
        typer.echo("")


@app.command("stats")
def stats_cmd(
    strategy_root: Optional[Path] = typer.Option(
        None, "--strategy-root", help="Override strategy MD root."
    ),
    index_root: Optional[Path] = typer.Option(
        None, "--index-root", help="Override chroma store root."
    ),
):
    """Show index size + configured paths."""
    idx = _build_index(strategy_root, index_root)
    s = idx.stats()
    typer.echo("Knowledge index stats:")
    for k, v in s.items():
        typer.echo(f"  {k}: {v}")


__all__ = ["app"]
