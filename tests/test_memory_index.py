import pytest
import time
from pathlib import Path
from financial_analyst.agent.memory_index import MemoryIndex


def _setup(tmp_path: Path):
    mem = tmp_path / "memories"
    mem.mkdir()
    (mem / "_shared").mkdir()
    (mem / "_shared" / "playbook.md").write_text("V1 industry tailwind. V10 execution discipline.", encoding="utf-8")
    (mem / "bear-advocate").mkdir()
    (mem / "bear-advocate" / "pitfalls.md").write_text("F2 游资(game-capital) tickers: mv<200亿 + pe>100 + ret60>50%.", encoding="utf-8")
    (mem / "bear-advocate" / "failure_modes.md").write_text("F8 super_distr regime. F9 broken board signal.", encoding="utf-8")
    (mem / "fundamental-analyst").mkdir()
    (mem / "fundamental-analyst" / "rating.md").write_text("Large cap >1000亿 forces factor score to 0.", encoding="utf-8")
    return mem


def test_rebuild_indexes_all_files(tmp_path):
    mem = _setup(tmp_path)
    db = tmp_path / "memory.fts5.db"
    idx = MemoryIndex(memory_root=mem, db_path=db)
    count = idx.rebuild()
    assert count == 4
    assert db.exists()


def test_search_finds_chinese_term(tmp_path):
    mem = _setup(tmp_path)
    idx = MemoryIndex(memory_root=mem, db_path=tmp_path / "memory.fts5.db")
    idx.rebuild()
    hits = idx.search("游资", top_k=5)
    assert len(hits) >= 1
    assert any("game-capital" in h["content"] or "pitfalls" in h["filename"] for h in hits)


def test_search_finds_v10(tmp_path):
    mem = _setup(tmp_path)
    idx = MemoryIndex(memory_root=mem, db_path=tmp_path / "memory.fts5.db")
    idx.rebuild()
    hits = idx.search("V10 discipline", top_k=3)
    assert any("playbook" in h["filename"] for h in hits)


def test_search_filter_by_agent(tmp_path):
    mem = _setup(tmp_path)
    idx = MemoryIndex(memory_root=mem, db_path=tmp_path / "memory.fts5.db")
    idx.rebuild()
    hits = idx.search("F8", agent="bear-advocate", top_k=5)
    assert all(h["agent"] == "bear-advocate" for h in hits)
    hits2 = idx.search("F8", agent="bull-advocate", top_k=5)
    assert len(hits2) == 0


def test_stats(tmp_path):
    mem = _setup(tmp_path)
    idx = MemoryIndex(memory_root=mem, db_path=tmp_path / "memory.fts5.db")
    idx.rebuild()
    s = idx.stats()
    assert s["total_files"] == 4
    assert s["per_agent"]["bear-advocate"] == 2
    assert s["per_agent"]["_shared"] == 1


def test_incremental_update(tmp_path):
    mem = _setup(tmp_path)
    idx = MemoryIndex(memory_root=mem, db_path=tmp_path / "memory.fts5.db")
    idx.rebuild()
    # add a new file
    (mem / "bull-advocate").mkdir()
    (mem / "bull-advocate" / "long.md").write_text("Long-side V4 立讯模式.", encoding="utf-8")
    updated = idx.update_changed()
    assert updated >= 1
    hits = idx.search("立讯", top_k=5)
    assert any("long.md" in h["filename"] for h in hits)


def test_update_after_file_edit(tmp_path):
    mem = _setup(tmp_path)
    idx = MemoryIndex(memory_root=mem, db_path=tmp_path / "memory.fts5.db")
    idx.rebuild()
    # modify existing file
    time.sleep(0.01)  # ensure mtime tick
    (mem / "bear-advocate" / "pitfalls.md").write_text("Brand new content with keyword Quokka123.", encoding="utf-8")
    idx.update_changed()
    hits = idx.search("Quokka123", top_k=5)
    assert len(hits) >= 1
