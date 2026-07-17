# -*- coding: utf-8 -*-
"""Phase 3 · Task 9 — read-only store adapter tests (explicit roots only).

Run: ``pytest tests/orchestration/memory/test_adapters.py -v``
"""
from __future__ import annotations

import pytest

from guanlan_v2.orchestration.memory.adapters import (
    AgentMemoryAdapter,
    ConsoleMemoryAdapter,
)
from guanlan_v2.orchestration.memory.models import MemoryContractError
from tests.orchestration.memory._env import write_agent_root, write_console_root


@pytest.fixture()
def agent_root(tmp_path):
    root = tmp_path / "memories"
    write_agent_root(root)
    return root


@pytest.fixture()
def console_root(tmp_path):
    root = tmp_path / "var" / "console"
    write_console_root(root)
    return root


# --------------------------------------------------------------------------- #
# agent adapter                                                                #
# --------------------------------------------------------------------------- #
def test_agent_adapter_covers_shared_and_every_own_dir_and_skips_staging(agent_root):
    units = AgentMemoryAdapter(agent_root).scan_units()
    locators = {u.locator for u in units}
    assert locators == {
        "_shared/market.md", "dec.pm/crit.md", "dec.pm/lesson.md", "mkt.macro/regime.md",
    }
    # staging/private paths never enter the scan.
    assert not any("_proposed" in loc or "_buddy" in loc for loc in locators)
    assert {u.source_id for u in units} == {"agent.shared", "agent.own"}


def test_agent_adapter_rows_carry_owner_scope_and_logical_identity(agent_root):
    rows = AgentMemoryAdapter(agent_root).rows()
    by_loc = {r.locator: r for r in rows}
    own = by_loc["dec.pm/lesson.md"]
    assert own.owner_id == "dec.pm" and own.scope == "agent_own"
    assert own.identity == {"store": "agent", "owner": "dec.pm",
                            "locator": "dec.pm/lesson.md"}
    shared = by_loc["_shared/market.md"]
    assert shared.owner_id == "shared" and shared.scope == "agent_shared"
    assert all(r.session_id is None for r in rows)


def test_always_include_targets_become_mandatory_hints(agent_root):
    rows = AgentMemoryAdapter(agent_root).rows()
    by_loc = {r.locator: r for r in rows}
    assert by_loc["dec.pm/crit.md"].mandatory_hint is True
    assert by_loc["dec.pm/lesson.md"].mandatory_hint is False


def test_always_include_rejects_absolute_and_traversal_targets(agent_root):
    (agent_root / "dec.pm" / "always_include.txt").write_text(
        "../escape.md\n", encoding="utf-8")
    with pytest.raises(MemoryContractError):
        AgentMemoryAdapter(agent_root).rows()
    (agent_root / "dec.pm" / "always_include.txt").write_text(
        "C:/abs.md\n", encoding="utf-8")
    with pytest.raises(MemoryContractError):
        AgentMemoryAdapter(agent_root).rows()


def test_agent_adapter_never_calls_the_seeding_default_root_helper(agent_root, monkeypatch):
    """The adapter takes an explicit root; the seeding helper must never run
    during a scan (it mutates the filesystem as a resolution side effect)."""
    from financial_analyst import memory_paths

    def _boom():  # pragma: no cover - the point is that it is never reached
        raise AssertionError("default_memory_root() was called during capture")

    monkeypatch.setattr(memory_paths, "default_memory_root", _boom)
    adapter = AgentMemoryAdapter(agent_root)
    assert adapter.scan_units() and adapter.rows()


def test_agent_adapter_is_read_only(agent_root):
    before = sorted(p.relative_to(agent_root).as_posix()
                    for p in agent_root.rglob("*") if p.is_file())
    adapter = AgentMemoryAdapter(agent_root)
    adapter.scan_units()
    adapter.rows()
    after = sorted(p.relative_to(agent_root).as_posix()
                   for p in agent_root.rglob("*") if p.is_file())
    assert after == before


# --------------------------------------------------------------------------- #
# console adapter                                                              #
# --------------------------------------------------------------------------- #
def test_console_adapter_units_cover_global_archive_tail_and_matching_session(console_root):
    units = ConsoleMemoryAdapter(
        console_root, session_id="cs.demo", archive_tail_chars=4000).scan_units()
    keys = {(u.source_id, u.locator) for u in units}
    assert keys == {
        ("console.global.keyed", "memory.md"),
        ("console.global.unkeyed", "memory.md"),
        ("console.global.archive", "memory.archive.md#tail"),
        ("console.session", "sessions/cs.demo/notes.md"),
    }


def test_console_adapter_never_crosses_session_ids(console_root):
    rows = ConsoleMemoryAdapter(
        console_root, session_id="cs.demo", archive_tail_chars=4000).rows()
    texts = " ".join(r.text for r in rows)
    assert "FOREIGN session secret note" not in texts
    session_rows = [r for r in rows if r.scope == "console_session"]
    assert session_rows and all(r.session_id == "cs.demo" for r in session_rows)
    # no session at all -> no session unit/row.
    none_rows = ConsoleMemoryAdapter(
        console_root, session_id=None, archive_tail_chars=4000).rows()
    assert not any(r.scope == "console_session" for r in none_rows)


def test_console_keyed_vs_unkeyed_parsing_and_heading_skip(console_root):
    rows = ConsoleMemoryAdapter(
        console_root, session_id=None, archive_tail_chars=4000).rows()
    keyed = [r for r in rows if r.source_id == "console.global.keyed"]
    unkeyed = [r for r in rows if r.source_id == "console.global.unkeyed"]
    assert len(keyed) == 1 and keyed[0].identity["key"] == "pref"
    assert keyed[0].mandatory_hint is True  # 常驻 keyed line
    assert len(unkeyed) == 2
    archive = [r for r in rows if r.source_id == "console.global.archive"]
    # the "## 归档于 ..." heading is ignored as user memory.
    assert len(archive) == 1 and "old archived observation" in archive[0].text


def test_console_archive_tail_window_is_honored(console_root):
    tiny = ConsoleMemoryAdapter(console_root, session_id=None, archive_tail_chars=10)
    rows = [r for r in tiny.rows() if r.source_id == "console.global.archive"]
    assert rows == []  # the full archived line is outside the 10-char tail
    zero = ConsoleMemoryAdapter(console_root, session_id=None, archive_tail_chars=0)
    assert not any(r.source_id == "console.global.archive" for r in zero.rows())


def test_duplicate_unkeyed_lines_keep_distinct_occurrence_identity(console_root):
    (console_root / "memory.md").write_text(
        "- [2026-07-11] same ephemeral line\n"
        "- [2026-07-11] same ephemeral line\n",
        encoding="utf-8")
    rows = [r for r in ConsoleMemoryAdapter(
        console_root, session_id=None, archive_tail_chars=0).rows()
        if r.source_id == "console.global.unkeyed"]
    assert len(rows) == 2
    assert rows[0].identity["occurrence"] == 0
    assert rows[1].identity["occurrence"] == 1
    assert rows[0].identity["content"] == rows[1].identity["content"]


def test_archive_move_preserves_unkeyed_identity(console_root):
    """Moving the same logical record into the reviewed archive window keeps
    its identity (identity excludes the source view/locator)."""
    main = ConsoleMemoryAdapter(console_root, session_id=None, archive_tail_chars=4000)
    before = {r.text: r.identity for r in main.rows()
              if r.source_id == "console.global.unkeyed"}
    line = "- [2026-07-11] checked momentum factor yesterday"
    assert line in before
    # curator-style move: drop from main, append to archive.
    kept = [ln for ln in (console_root / "memory.md").read_text(encoding="utf-8")
            .splitlines() if ln != line]
    (console_root / "memory.md").write_text("\n".join(kept) + "\n", encoding="utf-8")
    with (console_root / "memory.archive.md").open("a", encoding="utf-8") as f:
        f.write(line + "\n")
    after_rows = ConsoleMemoryAdapter(
        console_root, session_id=None, archive_tail_chars=4000).rows()
    archived = next(r for r in after_rows
                    if r.source_id == "console.global.archive" and r.text == line)
    assert archived.identity == before[line]


def test_apply_marker_lines_are_metadata_not_user_memory(console_root):
    marker = ("<!-- guanlan-memory-apply-v1 marker_id=apply." + "a" * 64
              + " request_digest=" + "b" * 64 + " payload_digest=" + "c" * 64 + " -->")
    (console_root / "memory.md").write_text(
        marker + "\n- [2026-07-12] the applied record\n", encoding="utf-8")
    rows = ConsoleMemoryAdapter(
        console_root, session_id=None, archive_tail_chars=0).rows()
    assert not any(marker in r.text for r in rows)
    assert any("the applied record" in r.text for r in rows)


def test_adapters_require_explicit_roots(tmp_path):
    with pytest.raises(MemoryContractError):
        AgentMemoryAdapter("")
    with pytest.raises(MemoryContractError):
        ConsoleMemoryAdapter("", session_id=None, archive_tail_chars=0)
    with pytest.raises(MemoryContractError):
        ConsoleMemoryAdapter(tmp_path, session_id=None, archive_tail_chars=-1)
