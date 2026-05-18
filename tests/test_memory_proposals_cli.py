"""Tests for /memory list-proposals / accept / reject TUI subcommands."""
import pytest
import yaml
from pathlib import Path
from financial_analyst.tui import handle_memory_cmd, console


@pytest.fixture(autouse=True)
def _chdir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "memories").mkdir()
    yield


@pytest.mark.asyncio
async def test_list_proposals_empty(capsys):
    with console.capture() as cap:
        await handle_memory_cmd(["list-proposals"])
    out = cap.get()
    assert "no proposals" in out.lower() or "yet" in out.lower()


@pytest.mark.asyncio
async def test_list_proposals_shows_files(tmp_path):
    proposed = tmp_path / "memories" / "_proposed" / "bear-advocate"
    proposed.mkdir(parents=True)
    fm = yaml.safe_dump({"topic": "t", "title": "T", "target_agent": "bear-advocate",
                          "confidence": "med", "generated_at": "2026-05-18",
                          "supporting_cases": [], "reasoning": ""}, allow_unicode=True)
    (proposed / "2026-05-18_test.md").write_text(f"---\n{fm}---\n\n# body", encoding="utf-8")
    with console.capture() as cap:
        await handle_memory_cmd(["list-proposals"])
    out = cap.get()
    assert "bear-advocate" in out
    assert "med" in out


@pytest.mark.asyncio
async def test_accept_moves_file(tmp_path):
    proposed = tmp_path / "memories" / "_proposed" / "bull-advocate"
    proposed.mkdir(parents=True)
    (proposed / "2026-05-18_x.md").write_text("content", encoding="utf-8")
    with console.capture() as cap:
        await handle_memory_cmd(["accept", "_proposed/bull-advocate/2026-05-18_x.md"])
    out = cap.get()
    assert "accepted" in out.lower()
    assert not (proposed / "2026-05-18_x.md").exists()
    assert (tmp_path / "memories" / "bull-advocate" / "2026-05-18_x.md").exists()


@pytest.mark.asyncio
async def test_reject_deletes_file(tmp_path):
    proposed = tmp_path / "memories" / "_proposed" / "risk-officer"
    proposed.mkdir(parents=True)
    target = proposed / "2026-05-18_bad.md"
    target.write_text("bad", encoding="utf-8")
    with console.capture() as cap:
        await handle_memory_cmd(["reject", "_proposed/risk-officer/2026-05-18_bad.md"])
    out = cap.get()
    assert "rejected" in out.lower() or "deleted" in out.lower()
    assert not target.exists()


@pytest.mark.asyncio
async def test_accept_rejects_outside_proposed(tmp_path):
    """Safety: accept must only operate on _proposed/ paths."""
    with console.capture() as cap:
        await handle_memory_cmd(["accept", "bull-advocate/x.md"])
    out = cap.get()
    assert "_proposed" in out.lower()


@pytest.mark.asyncio
async def test_accept_missing_file(tmp_path):
    with console.capture() as cap:
        await handle_memory_cmd(["accept", "_proposed/bull-advocate/missing.md"])
    out = cap.get()
    assert "not found" in out.lower()
