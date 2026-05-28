"""Regression tests for the fresh-install ``memories/`` crash.

Background
----------
Every command resolved ``memories/`` as the *relative* path ``Path("memories")``,
which only existed in the dev's source checkout. A pip-installed user running
``fa report`` / the MCP ``run_report`` tool from any other directory hit::

    FileNotFoundError: [WinError 3] 系统找不到指定的路径。: 'memories'

(on Python 3.13 ``Path.iterdir()`` is implemented via ``os.scandir`` and raises
on a missing directory — earlier versions silently yielded nothing).

These tests pin down:
  * ``MemoryIndex`` must not crash when its root is missing (defense in depth)
  * ``default_memory_root()`` must resolve to a real, writable, seeded location
    regardless of the current working directory.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from financial_analyst.agent.memory_index import MemoryIndex


# ---------------------------------------------------------------------------
# Direct reproduction of the reported crash
# ---------------------------------------------------------------------------

def test_memory_index_missing_root_does_not_crash(tmp_path):
    """update_changed() over a non-existent memory_root must no-op, not raise.

    Pre-fix this raised FileNotFoundError [WinError 3] from os.scandir('memories').
    """
    missing = tmp_path / "does-not-exist"
    idx = MemoryIndex(memory_root=missing, db_path=tmp_path / "memory.fts5.db")
    assert idx.update_changed() == 0


def test_memory_index_rebuild_missing_root_does_not_crash(tmp_path):
    missing = tmp_path / "nope"
    idx = MemoryIndex(memory_root=missing, db_path=tmp_path / "memory.fts5.db")
    assert idx.rebuild() == 0


# ---------------------------------------------------------------------------
# default_memory_root() resolution chain
# ---------------------------------------------------------------------------

def test_resolver_falls_back_to_user_home_when_no_cwd_memories(tmp_path, monkeypatch):
    """A pip user (no ./memories, no env) gets ~/.financial-analyst/memories,
    and it actually exists afterwards — never a bare relative ./memories."""
    from financial_analyst.memory_paths import default_memory_root

    fake_home = tmp_path / "home"
    fake_home.mkdir()
    workdir = tmp_path / "workdir"   # deliberately has NO memories/ subdir
    workdir.mkdir()
    monkeypatch.delenv("FINANCIAL_ANALYST_HOME", raising=False)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))
    monkeypatch.chdir(workdir)

    root = default_memory_root()

    assert root == fake_home / ".financial-analyst" / "memories"
    assert root.is_dir()


def test_resolver_honours_env_override(tmp_path, monkeypatch):
    from financial_analyst.memory_paths import default_memory_root

    fa_home = tmp_path / "fa-home"
    fa_home.mkdir()
    monkeypatch.setenv("FINANCIAL_ANALYST_HOME", str(fa_home))

    root = default_memory_root()

    assert root == fa_home / "memories"
    assert root.is_dir()


def test_resolver_prefers_cwd_memories_in_dev_mode(tmp_path, monkeypatch):
    """If ./memories exists (source checkout / dev), it wins — preserves the
    pre-fix behavior so the maintainer's working tree is still used."""
    from financial_analyst.memory_paths import default_memory_root

    monkeypatch.delenv("FINANCIAL_ANALYST_HOME", raising=False)
    repo = tmp_path / "repo"
    (repo / "memories" / "_shared").mkdir(parents=True)
    monkeypatch.chdir(repo)

    root = default_memory_root()

    assert root == repo / "memories"


def test_fresh_user_root_is_seeded_from_bundle(tmp_path, monkeypatch):
    """A fresh ~/.financial-analyst/memories is populated from the bundled
    seed so reports actually have the agent rules/playbooks to work with."""
    from financial_analyst.memory_paths import default_memory_root, bundled_seed_dir

    if not bundled_seed_dir().is_dir():
        pytest.fail(
            f"bundled seed dir missing: {bundled_seed_dir()} — the wheel would "
            "ship without agent memories and reports would be degraded"
        )

    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.delenv("FINANCIAL_ANALYST_HOME", raising=False)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))
    monkeypatch.chdir(tmp_path)

    root = default_memory_root()

    # Seeding copied at least one agent-knowledge markdown across.
    assert any(root.rglob("*.md")), f"no seed .md copied into {root}"


def test_bundled_seed_excludes_private_runtime_files():
    """The shipped seed must NOT contain gitignored runtime/private state."""
    from financial_analyst.memory_paths import bundled_seed_dir

    seed = bundled_seed_dir()
    if not seed.is_dir():
        pytest.fail(f"bundled seed dir missing: {seed}")

    assert not (seed / "_proposed").exists(), "dream-loop proposals must not ship"
    assert not (seed / "_pending_introspections").exists(), "pending introspections must not ship"
    assert not (seed / "_shared" / "conversation_lessons.md").exists(), \
        "user conversation lessons must not ship"
