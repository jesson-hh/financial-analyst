import pytest
from pathlib import Path
from financial_analyst.agent.memory import AgentMemory
from financial_analyst.agent.memory_index import MemoryIndex


def test_load_empty_dir(tmp_path):
    (tmp_path / "agent_x").mkdir()
    mem = AgentMemory("agent_x", tmp_path)
    assert mem.load_all() == ""


def test_load_concatenates_markdown_sorted(tmp_path):
    d = tmp_path / "bear-advocate"
    d.mkdir()
    (d / "b_pitfalls.md").write_text("pitfall A")
    (d / "a_modes.md").write_text("mode B")
    mem = AgentMemory("bear-advocate", tmp_path)
    text = mem.load_all()
    assert "mode B" in text
    assert "pitfall A" in text
    assert text.index("mode B") < text.index("pitfall A")


def test_includes_shared(tmp_path):
    shared = tmp_path / "_shared"
    shared.mkdir()
    (shared / "playbook.md").write_text("V1-V10")
    own = tmp_path / "bull-advocate"
    own.mkdir()
    (own / "long.md").write_text("long bullets")
    mem = AgentMemory("bull-advocate", tmp_path)
    text = mem.load_all()
    assert "V1-V10" in text
    assert "long bullets" in text


def test_reload_picks_up_changes(tmp_path):
    d = tmp_path / "a"
    d.mkdir()
    (d / "x.md").write_text("v1")
    mem = AgentMemory("a", tmp_path)
    assert "v1" in mem.load_all()
    (d / "x.md").write_text("v2")
    mem.reload()
    assert "v2" in mem.load_all()
    assert "v1" not in mem.load_all()


def test_borrowed_memory(tmp_path):
    (tmp_path / "bear-advocate").mkdir()
    (tmp_path / "bear-advocate" / "pitfalls.md").write_text("bear pits")
    (tmp_path / "risk-officer").mkdir()
    (tmp_path / "risk-officer" / "rules.md").write_text("hard rules")
    mem = AgentMemory("risk-officer", tmp_path, borrows=["bear-advocate"])
    text = mem.load_all()
    assert "hard rules" in text
    assert "bear pits" in text


def test_load_relevant_requires_index(tmp_path):
    (tmp_path / "bear-advocate").mkdir()
    (tmp_path / "bear-advocate" / "pitfalls.md").write_text("pit", encoding="utf-8")
    mem = AgentMemory("bear-advocate", tmp_path)
    with pytest.raises(RuntimeError, match="MemoryIndex"):
        mem.load_relevant("anything")


def test_load_relevant_returns_top_k_snippets(tmp_path):
    (tmp_path / "_shared").mkdir()
    (tmp_path / "_shared" / "v10.md").write_text("V10 discipline always.", encoding="utf-8")
    (tmp_path / "bear-advocate").mkdir()
    (tmp_path / "bear-advocate" / "f8.md").write_text("F8 super_distr regime fwd_5d -4.20pp.", encoding="utf-8")
    (tmp_path / "bear-advocate" / "f9.md").write_text("F9 broken board seal_at_close=False.", encoding="utf-8")
    (tmp_path / "bear-advocate" / "f10.md").write_text("F10 tail_surge regime distribution.", encoding="utf-8")

    idx = MemoryIndex(memory_root=tmp_path, db_path=tmp_path / "idx.db")
    idx.rebuild()
    mem = AgentMemory("bear-advocate", tmp_path, index=idx)
    text = mem.load_relevant("super_distr", top_k=2)
    # F8 is most relevant
    assert "F8" in text
    # _shared always present
    assert "V10 discipline" in text


def test_load_relevant_includes_borrowed(tmp_path):
    (tmp_path / "_shared").mkdir()
    (tmp_path / "bear-advocate").mkdir()
    (tmp_path / "bear-advocate" / "pitfalls.md").write_text("pitfalls F8 super_distr.", encoding="utf-8")
    (tmp_path / "risk-officer").mkdir()
    (tmp_path / "risk-officer" / "hard.md").write_text("Hard rule super_distr veto.", encoding="utf-8")

    idx = MemoryIndex(memory_root=tmp_path, db_path=tmp_path / "idx.db")
    idx.rebuild()
    mem = AgentMemory("risk-officer", tmp_path, borrows=["bear-advocate"], index=idx)
    text = mem.load_relevant("super_distr", top_k=3)
    assert "Hard rule" in text
    assert "pitfalls" in text or "F8" in text  # borrowed bear-advocate content


def test_load_all_unchanged_with_index(tmp_path):
    """Backward compat: load_all still works when index is set."""
    (tmp_path / "bear-advocate").mkdir()
    (tmp_path / "bear-advocate" / "x.md").write_text("hello", encoding="utf-8")
    idx = MemoryIndex(memory_root=tmp_path, db_path=tmp_path / "idx.db")
    idx.rebuild()
    mem = AgentMemory("bear-advocate", tmp_path, index=idx)
    assert "hello" in mem.load_all()


def test_load_relevant_without_shared_when_disabled(tmp_path):
    (tmp_path / "_shared").mkdir()
    (tmp_path / "_shared" / "v10.md").write_text("V10 rule", encoding="utf-8")
    (tmp_path / "bear-advocate").mkdir()
    (tmp_path / "bear-advocate" / "p.md").write_text("pitfall content", encoding="utf-8")

    idx = MemoryIndex(memory_root=tmp_path, db_path=tmp_path / "idx.db")
    idx.rebuild()
    mem = AgentMemory("bear-advocate", tmp_path, index=idx)
    text = mem.load_relevant("pitfall", top_k=2, always_include_shared=False)
    assert "pitfall content" in text
    assert "V10 rule" not in text


def test_load_relevant_fallback_to_load_all_on_zero_hits(tmp_path):
    """When FTS5 returns 0 hits, load_relevant should fall back to load_all."""
    (tmp_path / "agent_x").mkdir()
    (tmp_path / "agent_x" / "rules.md").write_text("rule content xyzzy", encoding="utf-8")
    idx = MemoryIndex(memory_root=tmp_path, db_path=tmp_path / "idx.db")
    idx.rebuild()
    mem = AgentMemory("agent_x", tmp_path, index=idx)
    # query for nonsense — FTS5 won't match
    text = mem.load_relevant("absolutely_nonexistent_term_zzzzzz", top_k=3)
    assert "rule content xyzzy" in text  # fell back


def test_always_include_loaded_unconditionally(tmp_path):
    """Files listed in always_include.txt always load, regardless of query."""
    (tmp_path / "risk-officer").mkdir()
    (tmp_path / "risk-officer" / "hard_rules.md").write_text("CRITICAL hard rule", encoding="utf-8")
    (tmp_path / "risk-officer" / "other.md").write_text("other content matchable", encoding="utf-8")
    (tmp_path / "risk-officer" / "always_include.txt").write_text("hard_rules.md\n", encoding="utf-8")
    idx = MemoryIndex(memory_root=tmp_path, db_path=tmp_path / "idx.db")
    idx.rebuild()
    mem = AgentMemory("risk-officer", tmp_path, index=idx)
    text = mem.load_relevant("matchable", top_k=1)
    assert "CRITICAL hard rule" in text   # always_include should be there
    assert "other content matchable" in text  # also there via FTS5 hit
