"""Tests for chunker.py — H2 splitting / no-H2 fallback / empty file."""
from __future__ import annotations

from pathlib import Path

import pytest

from financial_analyst.data.knowledge_index.chunker import Chunk, chunk_markdown


def test_three_h2_sections_make_three_chunks(tmp_path: Path):
    md = tmp_path / "doc.md"
    md.write_text(
        "# Title\n\nIntro paragraph that lives before any H2.\n\n"
        "## 第一节 反转因子\n"
        "rev_20 在 A 股一向是核心。\n\n"
        "## 第二节 动量陷阱\n"
        "追涨在 A 股容易翻车。\n\n"
        "## 第三节 小结\n"
        "用反转, 不用动量。\n",
        encoding="utf-8",
    )
    chunks = chunk_markdown(md)
    assert len(chunks) == 3
    assert [c.section_h2 for c in chunks] == [
        "第一节 反转因子",
        "第二节 动量陷阱",
        "第三节 小结",
    ]
    # H2 heading line is preserved inside the chunk text (lets the embedder
    # see the section title).
    assert chunks[0].text.startswith("## 第一节 反转因子")
    assert "rev_20" in chunks[0].text
    # No content from a later section bleeds into an earlier chunk.
    assert "动量陷阱" not in chunks[0].text
    assert "动量陷阱" in chunks[1].text
    # All chunks share the same mtime — they came from one file.
    assert len({c.mtime for c in chunks}) == 1
    # ids are unique, deterministic, 16 hex chars.
    ids = [c.id for c in chunks]
    assert len(set(ids)) == 3
    for cid in ids:
        assert len(cid) == 16
        int(cid, 16)  # hex-only — raises if not


def test_chunk_ids_are_stable_across_calls(tmp_path: Path):
    md = tmp_path / "doc.md"
    md.write_text("## A\nbody-a\n## B\nbody-b\n", encoding="utf-8")
    a = chunk_markdown(md)
    b = chunk_markdown(md)
    assert [c.id for c in a] == [c.id for c in b]


def test_no_h2_falls_back_to_single_root_chunk(tmp_path: Path):
    md = tmp_path / "no_h2.md"
    md.write_text(
        "# Just an H1\nSome prose without any H2 boundary.\n",
        encoding="utf-8",
    )
    chunks = chunk_markdown(md)
    assert len(chunks) == 1
    assert chunks[0].section_h2 == "(root)"
    assert "Just an H1" in chunks[0].text
    assert "Some prose" in chunks[0].text


def test_empty_file_returns_empty_list(tmp_path: Path):
    md = tmp_path / "empty.md"
    md.write_text("", encoding="utf-8")
    assert chunk_markdown(md) == []


def test_whitespace_only_file_returns_empty_list(tmp_path: Path):
    md = tmp_path / "blanks.md"
    md.write_text("\n\n   \n\t\n", encoding="utf-8")
    assert chunk_markdown(md) == []


def test_missing_file_raises(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        chunk_markdown(tmp_path / "nope.md")


def test_source_id_override_propagates_to_chunks(tmp_path: Path):
    md = tmp_path / "x.md"
    md.write_text("## A\nbody\n", encoding="utf-8")
    chunks = chunk_markdown(md, source_id="strategy/x.md")
    assert chunks[0].source_file == "strategy/x.md"


def test_lang_defaults_to_zh_and_is_overridable(tmp_path: Path):
    md = tmp_path / "y.md"
    md.write_text("## A\nbody\n", encoding="utf-8")
    chunks = chunk_markdown(md)
    assert chunks[0].lang == "zh"
    chunks_en = chunk_markdown(md, lang="en")
    assert chunks_en[0].lang == "en"


def test_h3_inside_h2_stays_with_parent(tmp_path: Path):
    """H3 is not a chunk boundary — it lives inside its H2 parent."""
    md = tmp_path / "nested.md"
    md.write_text(
        "## Parent\n"
        "intro\n\n"
        "### Child A\nchild-a-body\n\n"
        "### Child B\nchild-b-body\n",
        encoding="utf-8",
    )
    chunks = chunk_markdown(md)
    assert len(chunks) == 1
    assert "Child A" in chunks[0].text
    assert "Child B" in chunks[0].text
