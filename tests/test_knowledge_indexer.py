"""Tests for indexer.py — build / mtime incremental / search hit / stale cleanup."""
from __future__ import annotations

import os
import time
from pathlib import Path

import numpy as np
import pytest

from financial_analyst.data.knowledge_index.embedder import StubEmbedder
from financial_analyst.data.knowledge_index.indexer import KnowledgeIndexer
from financial_analyst.data.knowledge_index.search import KnowledgeIndex
from financial_analyst.data.knowledge_index.store import ChromaStore


def _make_strategy_tree(root: Path) -> dict:
    """Two MD files spread across two canonical sub-dirs."""
    root.mkdir(parents=True, exist_ok=True)
    f1 = root / "factor_insights.md"
    f1.write_text(
        "# 因子经验\n\n"
        "## rev_20 反转\n"
        "rev_20 在 A 股很强, ICIR 0.12。\n\n"
        "## momentum 失效\n"
        "追涨在 A 股容易翻车。\n",
        encoding="utf-8",
    )
    research = root / "research"
    research.mkdir()
    f2 = research / "2026-05-30-sample.md"
    f2.write_text(
        "## 实验背景\n"
        "测试 PCA 残差。\n\n"
        "## 结果\n"
        "失败, ICIR 0.02。\n",
        encoding="utf-8",
    )
    return {"f1": f1, "f2": f2}


def test_discover_finds_all_md_in_canonical_dirs(tmp_path: Path):
    files = _make_strategy_tree(tmp_path)
    idx = KnowledgeIndexer(
        strategy_root=tmp_path,
        store=ChromaStore(root=tmp_path / "_chroma"),
        embedder=StubEmbedder(dim=8),
    )
    found = idx.discover()
    assert files["f1"].resolve() in found
    assert files["f2"].resolve() in found


def test_discover_on_missing_root_returns_empty(tmp_path: Path):
    idx = KnowledgeIndexer(
        strategy_root=tmp_path / "does_not_exist",
        store=ChromaStore(root=tmp_path / "_chroma"),
        embedder=StubEmbedder(dim=8),
    )
    assert idx.discover() == []


def test_build_first_time_embeds_everything(tmp_path: Path):
    _make_strategy_tree(tmp_path)
    store = ChromaStore(root=tmp_path / "_chroma")
    idx = KnowledgeIndexer(strategy_root=tmp_path, store=store, embedder=StubEmbedder(dim=8))
    stats = idx.build()
    assert stats.files_scanned == 2
    assert stats.chunks_seen == 4
    assert stats.chunks_embedded == 4
    assert stats.chunks_skipped_unchanged == 0
    assert stats.chunks_deleted_stale == 0
    assert store.count() == 4


def test_build_search_hits_correct_source_and_section(tmp_path: Path):
    _make_strategy_tree(tmp_path)
    idx = KnowledgeIndex(
        strategy_root=tmp_path,
        index_root=tmp_path / "_chroma",
        embedder=StubEmbedder(dim=8),
    )
    idx.build()

    # StubEmbedder maps "rev_20 反转" → the same vector as the chunk whose
    # text is exactly that. We query with the chunk text itself to guarantee
    # an exact-match top-1.
    chunk_text = (
        "## rev_20 反转\n"
        "rev_20 在 A 股很强, ICIR 0.12。"
    )
    results = idx.search(chunk_text, k=2)
    assert len(results) >= 1
    top = results[0]
    assert top.source.endswith("factor_insights.md")
    assert top.section == "rev_20 反转"
    assert "rev_20" in top.text


def test_build_incremental_skips_unchanged(tmp_path: Path):
    files = _make_strategy_tree(tmp_path)
    store = ChromaStore(root=tmp_path / "_chroma")
    idx = KnowledgeIndexer(strategy_root=tmp_path, store=store, embedder=StubEmbedder(dim=8))
    s1 = idx.build()
    assert s1.chunks_embedded == 4

    # Second build with no file changes → everything skipped.
    s2 = idx.build()
    assert s2.chunks_embedded == 0
    assert s2.chunks_skipped_unchanged == 4
    assert store.count() == 4


def test_build_incremental_only_reembeds_changed_file(tmp_path: Path):
    files = _make_strategy_tree(tmp_path)
    store = ChromaStore(root=tmp_path / "_chroma")
    idx = KnowledgeIndexer(strategy_root=tmp_path, store=store, embedder=StubEmbedder(dim=8))
    idx.build()
    assert store.count() == 4

    # Touch f1 so its mtime advances. We bump it ahead by 2s deterministically
    # to dodge filesystem mtime granularity surprises on Windows.
    f1_stat = files["f1"].stat()
    new_mtime = f1_stat.st_mtime + 2.0
    os.utime(files["f1"], (new_mtime, new_mtime))

    s2 = idx.build()
    # f1 has 2 chunks, f2 has 2 chunks. After touching f1, only its 2 should
    # be re-embedded; f2's 2 remain skipped.
    assert s2.chunks_embedded == 2, s2.as_dict()
    assert s2.chunks_skipped_unchanged == 2, s2.as_dict()
    assert store.count() == 4


def test_build_force_reembeds_everything(tmp_path: Path):
    _make_strategy_tree(tmp_path)
    store = ChromaStore(root=tmp_path / "_chroma")
    idx = KnowledgeIndexer(strategy_root=tmp_path, store=store, embedder=StubEmbedder(dim=8))
    idx.build()
    s2 = idx.build(force=True)
    assert s2.chunks_embedded == 4
    assert s2.chunks_skipped_unchanged == 0


def test_build_deletes_stale_chunks(tmp_path: Path):
    files = _make_strategy_tree(tmp_path)
    store = ChromaStore(root=tmp_path / "_chroma")
    idx = KnowledgeIndexer(strategy_root=tmp_path, store=store, embedder=StubEmbedder(dim=8))
    idx.build()
    assert store.count() == 4

    # Delete one file → its chunks should be removed from the store.
    files["f2"].unlink()
    s2 = idx.build()
    assert s2.chunks_deleted_stale == 2
    assert store.count() == 2


def test_build_handles_empty_file(tmp_path: Path):
    _make_strategy_tree(tmp_path)
    # Add an empty MD — should be skipped without errors.
    (tmp_path / "empty.md").write_text("", encoding="utf-8")
    store = ChromaStore(root=tmp_path / "_chroma")
    idx = KnowledgeIndexer(strategy_root=tmp_path, store=store, embedder=StubEmbedder(dim=8))
    stats = idx.build()
    assert stats.files_scanned == 3
    assert stats.files_skipped_empty == 1
    assert stats.chunks_embedded == 4
    assert store.count() == 4


def test_high_level_search_returns_chunk_results(tmp_path: Path):
    _make_strategy_tree(tmp_path)
    idx = KnowledgeIndex(
        strategy_root=tmp_path,
        index_root=tmp_path / "_chroma",
        embedder=StubEmbedder(dim=8),
    )
    idx.build()
    results = idx.search("rev_20 反转", k=3)
    assert len(results) >= 1
    for r in results:
        assert r.text
        assert r.source
        assert r.section
        assert isinstance(r.score, float)
        assert r.chunk_id


def test_search_empty_query_returns_empty(tmp_path: Path):
    _make_strategy_tree(tmp_path)
    idx = KnowledgeIndex(
        strategy_root=tmp_path,
        index_root=tmp_path / "_chroma",
        embedder=StubEmbedder(dim=8),
    )
    idx.build()
    assert idx.search("", k=5) == []
    assert idx.search("   ", k=5) == []


def test_search_on_empty_store_returns_empty(tmp_path: Path):
    idx = KnowledgeIndex(
        strategy_root=tmp_path,  # empty dir
        index_root=tmp_path / "_chroma",
        embedder=StubEmbedder(dim=8),
    )
    # don't build → store is empty
    assert idx.search("anything", k=5) == []


def test_stats_reports_counts_and_paths(tmp_path: Path):
    _make_strategy_tree(tmp_path)
    idx = KnowledgeIndex(
        strategy_root=tmp_path,
        index_root=tmp_path / "_chroma",
        embedder=StubEmbedder(dim=8),
    )
    idx.build()
    s = idx.stats()
    assert s["n_chunks"] == 4
    assert s["strategy_root"] == str(tmp_path)
    assert s["collection_name"] == "strategy_knowledge"
