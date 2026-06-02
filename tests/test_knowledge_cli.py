"""Tests for cli.py — typer CliRunner invokes build / search / stats."""
from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from financial_analyst.data.knowledge_index.cli import app as knowledge_app


def _seed_strategy(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "factor_insights.md").write_text(
        "## rev_20 反转\n反转因子在 A 股很强。\n\n"
        "## momentum 失效\n动量翻车。\n",
        encoding="utf-8",
    )


@pytest.fixture(autouse=True)
def _stub_default_embedder(monkeypatch):
    """Force the CLI to use StubEmbedder, not BGE — keeps tests offline."""
    from financial_analyst.data.knowledge_index import search as _search
    from financial_analyst.data.knowledge_index.embedder import StubEmbedder

    real_init = _search.KnowledgeIndex.__init__

    def _patched_init(self, strategy_root=None, index_root=None, embedder=None, **kw):
        if embedder is None:
            embedder = StubEmbedder(dim=8)
        return real_init(
            self,
            strategy_root=strategy_root,
            index_root=index_root,
            embedder=embedder,
            **kw,
        )

    monkeypatch.setattr(_search.KnowledgeIndex, "__init__", _patched_init)


def test_build_command_runs_to_completion(tmp_path: Path):
    strat = tmp_path / "strategy"
    idx_root = tmp_path / "_chroma"
    _seed_strategy(strat)

    runner = CliRunner()
    result = runner.invoke(
        knowledge_app,
        ["build", "--strategy-root", str(strat), "--index-root", str(idx_root)],
    )
    assert result.exit_code == 0, result.stdout
    assert "build complete" in result.stdout.lower() or "files_scanned" in result.stdout
    assert "embedded:" in result.stdout
    # Sanity: index files actually landed under idx_root.
    assert idx_root.exists()


def test_build_then_search_returns_a_hit(tmp_path: Path):
    strat = tmp_path / "strategy"
    idx_root = tmp_path / "_chroma"
    _seed_strategy(strat)
    runner = CliRunner()
    r1 = runner.invoke(
        knowledge_app,
        ["build", "--strategy-root", str(strat), "--index-root", str(idx_root)],
    )
    assert r1.exit_code == 0, r1.stdout

    r2 = runner.invoke(
        knowledge_app,
        [
            "search",
            "## rev_20 反转\n反转因子在 A 股很强。",
            "--k", "2",
            "--strategy-root", str(strat),
            "--index-root", str(idx_root),
        ],
    )
    assert r2.exit_code == 0, r2.stdout
    assert "score=" in r2.stdout
    assert "factor_insights" in r2.stdout


def test_search_on_empty_index_prints_friendly_message(tmp_path: Path):
    strat = tmp_path / "strategy"
    idx_root = tmp_path / "_chroma"
    strat.mkdir()
    runner = CliRunner()
    result = runner.invoke(
        knowledge_app,
        [
            "search",
            "anything",
            "--strategy-root", str(strat),
            "--index-root", str(idx_root),
        ],
    )
    assert result.exit_code == 0
    assert "no results" in result.stdout.lower()


def test_stats_command_shows_paths_and_count(tmp_path: Path):
    strat = tmp_path / "strategy"
    idx_root = tmp_path / "_chroma"
    _seed_strategy(strat)
    runner = CliRunner()
    runner.invoke(
        knowledge_app,
        ["build", "--strategy-root", str(strat), "--index-root", str(idx_root)],
    )
    result = runner.invoke(
        knowledge_app,
        ["stats", "--strategy-root", str(strat), "--index-root", str(idx_root)],
    )
    assert result.exit_code == 0, result.stdout
    assert "strategy_root" in result.stdout
    assert "n_chunks" in result.stdout
    # 2 H2 sections → 2 chunks
    assert ": 2" in result.stdout or "n_chunks: 2" in result.stdout


def test_build_force_flag(tmp_path: Path):
    strat = tmp_path / "strategy"
    idx_root = tmp_path / "_chroma"
    _seed_strategy(strat)
    runner = CliRunner()
    r1 = runner.invoke(
        knowledge_app,
        ["build", "--strategy-root", str(strat), "--index-root", str(idx_root)],
    )
    assert r1.exit_code == 0
    r2 = runner.invoke(
        knowledge_app,
        ["build", "--force", "--strategy-root", str(strat), "--index-root", str(idx_root)],
    )
    assert r2.exit_code == 0
    # On a forced rebuild, the second run should show 2 embedded again
    # (not all skipped).
    assert "embedded:      2" in r2.stdout or "embedded:      2\n" in r2.stdout


def test_help_text_lists_commands():
    runner = CliRunner()
    result = runner.invoke(knowledge_app, ["--help"])
    assert result.exit_code == 0
    assert "build" in result.stdout
    assert "search" in result.stdout
    assert "stats" in result.stdout
