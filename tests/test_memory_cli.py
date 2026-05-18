"""Tests for /memory CLI subcommands (Task 4)."""
from __future__ import annotations

import pytest
from pathlib import Path
from unittest.mock import patch

from financial_analyst.tui import handle_memory_cmd, console


@pytest.fixture(autouse=True)
def _populate_memories_and_cache(tmp_path, monkeypatch):
    """Create temp memories/ + cache dir, chdir there."""
    mem = tmp_path / "memories"
    mem.mkdir()
    (mem / "_shared").mkdir()
    (mem / "_shared" / "playbook.md").write_text("V1 V10 discipline.", encoding="utf-8")
    (mem / "bear-advocate").mkdir()
    (mem / "bear-advocate" / "pitfalls.md").write_text("F2 game-capital ticker.", encoding="utf-8")
    cache = tmp_path / "cache"
    cache.mkdir()
    monkeypatch.setenv("FA_CACHE_DIR", str(cache))
    monkeypatch.chdir(tmp_path)
    yield


@pytest.mark.asyncio
async def test_memory_list():
    with console.capture() as cap:
        await handle_memory_cmd(["list", "bear-advocate"])
    out = cap.get()
    assert "pitfalls.md" in out


@pytest.mark.asyncio
async def test_memory_list_missing_dir():
    with console.capture() as cap:
        await handle_memory_cmd(["list", "nonexistent-agent"])
    out = cap.get()
    assert "no memory dir" in out.lower()


@pytest.mark.asyncio
async def test_memory_show():
    with console.capture() as cap:
        await handle_memory_cmd(["show", "bear-advocate/pitfalls.md"])
    out = cap.get()
    assert "game-capital" in out


@pytest.mark.asyncio
async def test_memory_show_missing():
    with console.capture() as cap:
        await handle_memory_cmd(["show", "bear-advocate/no_such_file.md"])
    out = cap.get()
    assert "not found" in out.lower()


@pytest.mark.asyncio
async def test_memory_search():
    with console.capture() as cap:
        await handle_memory_cmd(["search", "V10"])
    out = cap.get()
    assert "_shared" in out or "playbook" in out


@pytest.mark.asyncio
async def test_memory_search_no_match():
    with console.capture() as cap:
        await handle_memory_cmd(["search", "xyzzy_no_match_term"])
    out = cap.get()
    assert "no matches" in out.lower()


@pytest.mark.asyncio
async def test_memory_stats():
    with console.capture() as cap:
        await handle_memory_cmd(["stats"])
    out = cap.get()
    assert "bear-advocate" in out
    assert "_shared" in out


@pytest.mark.asyncio
async def test_memory_reindex():
    with console.capture() as cap:
        await handle_memory_cmd(["reindex"])
    out = cap.get()
    assert "reindex" in out.lower()


@pytest.mark.asyncio
async def test_memory_unknown_subcommand():
    with console.capture() as cap:
        await handle_memory_cmd(["bogus_sub"])
    out = cap.get()
    assert "unknown" in out.lower() or "usage" in out.lower()


@pytest.mark.asyncio
async def test_memory_edit_opens_editor():
    with patch("subprocess.Popen") as mock_popen:
        with console.capture() as cap:
            await handle_memory_cmd(["edit", "bear-advocate/pitfalls.md"])
        mock_popen.assert_called_once()
    out = cap.get()
    assert "opened" in out.lower() or "notepad" in out.lower() or "vi" in out.lower()


@pytest.mark.asyncio
async def test_memory_no_args_shows_usage():
    with console.capture() as cap:
        await handle_memory_cmd([])
    out = cap.get()
    assert "usage" in out.lower() or "/memory" in out


@pytest.mark.asyncio
async def test_memory_reload():
    with console.capture() as cap:
        await handle_memory_cmd(["reload"])
    out = cap.get()
    assert "reload" in out.lower() or "cache" in out.lower()
