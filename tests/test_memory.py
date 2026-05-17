from pathlib import Path
from financial_analyst.agent.memory import AgentMemory


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
