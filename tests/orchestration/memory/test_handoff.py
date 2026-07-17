# -*- coding: utf-8 -*-
"""Phase 3 · Task 9 — real-entry-point handoff gate (read-only inspection).

Inspects the REAL read/proposal/accept/reject/revert/archive/session entry
points the facade adapts: engine ``AgentMemory`` / ``MemoryIndex`` /
``memory_paths`` / ``memory_ops`` / ``dream.proposal_writer`` and the console
``store``/``tools``/``curator`` modules. Nothing here mutates a production
file — filesystem behavior is proven only against pytest tmp roots. If a fact
below drifts, the Task-9 plan must be updated from evidence, never hidden
behind a second writer.

Run: ``pytest tests/orchestration/memory/test_handoff.py -v``
"""
from __future__ import annotations

import inspect
from pathlib import Path

import pytest


# --------------------------------------------------------------------------- #
# engine AgentMemory — scan surface + the forbidden live path                   #
# --------------------------------------------------------------------------- #
def test_agent_memory_scans_only_shared_own_borrows_and_skips_staging(tmp_path):
    from financial_analyst.agent.memory import AgentMemory

    root = tmp_path / "memories"
    for d, name in (
        ("_shared", "s.md"), ("dec.pm", "own.md"), ("mkt.macro", "b.md"),
        ("_proposed/dec.pm", "p.md"), ("_pending_introspections", "i.md"),
        ("_buddy", "bd.md"),
    ):
        (root / d).mkdir(parents=True, exist_ok=True)
        (root / d / name).write_text("x", encoding="utf-8")
    mem = AgentMemory("dec.pm", root, borrows=["mkt.macro"])
    files = mem._collect_files()
    parents = {p.parent.name for p in files}
    assert parents == {"_shared", "dec.pm", "mkt.macro"}
    for staging in ("_proposed", "_pending_introspections", "_buddy"):
        assert staging not in parents


def test_agent_memory_live_relevance_path_has_the_forbidden_shape():
    """``load_relevant`` = FTS search -> top_k -> fallback-to-load_all. That path
    applies ORDER BY/LIMIT before any visibility allowlist, so orchestration
    reads must NOT call it (the adapter ranks the frozen visible tuple instead).
    This test pins the shape so a silent upstream change is caught."""
    from financial_analyst.agent.memory import AgentMemory

    sig = inspect.signature(AgentMemory.load_relevant)
    assert list(sig.parameters) == ["self", "query", "top_k", "always_include_shared"]
    src = inspect.getsource(AgentMemory.load_relevant)
    assert "load_all" in src  # the zero-hit fallback goes to the FULL store
    from financial_analyst.agent.memory_index import MemoryIndex

    search_src = inspect.getsource(MemoryIndex.search)
    assert "LIMIT" in search_src  # ORDER/LIMIT precede any visibility filter


def test_always_include_is_newline_separated_filenames(tmp_path):
    from financial_analyst.agent.memory import AgentMemory

    root = tmp_path / "memories"
    own = root / "dec.pm"
    own.mkdir(parents=True)
    (own / "always_include.txt").write_text("crit.md\n", encoding="utf-8")
    (own / "crit.md").write_text("critical", encoding="utf-8")
    mem = AgentMemory("dec.pm", root)
    assert "critical" in mem._load_always_include()


def test_default_memory_root_helper_seeds_the_filesystem(tmp_path, monkeypatch):
    """Why capture must receive explicit service-owned roots: the default-root
    helper CREATES + SEEDS a directory as a side effect of resolution."""
    from financial_analyst import memory_paths

    home = tmp_path / "fa-home"
    monkeypatch.setenv("FINANCIAL_ANALYST_HOME", str(home))
    root = memory_paths.default_memory_root()
    assert Path(root).exists()  # resolution mutated the filesystem
    assert Path(root) == home / "memories"


# --------------------------------------------------------------------------- #
# engine memory_ops + proposal_writer — the mutation owners                     #
# --------------------------------------------------------------------------- #
def test_memory_ops_exposes_the_legacy_lifecycle_with_caller_source_strings():
    from financial_analyst import memory_ops

    for fn_name in ("accept_proposal", "reject_proposal", "revert_proposal"):
        fn = getattr(memory_ops, fn_name)
        params = inspect.signature(fn).parameters
        assert "source" in params, f"{fn_name} lost its source kw"
        assert params["source"].kind is inspect.Parameter.KEYWORD_ONLY
    # `source="mcp"` is a caller string — never admin/review evidence.
    assert isinstance(memory_ops.AUDIT_PATH, Path)


def test_legacy_proposal_writer_entry_point_is_preserved():
    from financial_analyst.dream import proposal_writer

    sig = inspect.signature(proposal_writer.write_proposals)
    assert list(sig.parameters) == ["proposals", "memory_root"]


def test_accept_proposal_refuses_to_overwrite_an_accepted_file(tmp_path):
    from financial_analyst import memory_ops

    root = tmp_path
    proposed = root / "memories" / "_proposed" / "dec.pm"
    proposed.mkdir(parents=True)
    (proposed / "lesson.md").write_text("body", encoding="utf-8")
    accepted = root / "memories" / "dec.pm"
    accepted.mkdir(parents=True)
    (accepted / "lesson.md").write_text("existing", encoding="utf-8")
    out = memory_ops.accept_proposal("dec.pm/lesson", source="cli", project_root=root)
    assert "error" in out and "overwrite" in out["error"]


# --------------------------------------------------------------------------- #
# console store / tools / curator — read-only interface facts                   #
# --------------------------------------------------------------------------- #
def test_console_store_journal_surface(tmp_path):
    from guanlan_v2.console.store import ConsoleStore

    store = ConsoleStore(root=tmp_path / "console")
    meta = store.create_session("t")
    ev = store.append_event(meta["id"], "note", body="x")
    assert ev["id"] == 1
    assert store.read_events(meta["id"])[0]["type"] == "note"
    assert store.delete_session(meta["id"]) is True
    # id monotonicity is meta-driven (crash yields holes, never duplicates).
    assert "next_event_id" in store.create_session("t2")


def test_console_memory_writer_and_curator_interfaces():
    import guanlan_v2.console.tools as tools
    from guanlan_v2.console import curator

    sig = inspect.signature(tools.memory_write_impl)
    assert list(sig.parameters) == ["text", "scope", "key"]
    assert callable(curator.is_keyed_line) and callable(curator.classify_lines)
    assert curator.is_keyed_line("- [2026-07-17] (pref) keep this")
    assert not curator.is_keyed_line("- [2026-07-17] ephemeral note")
    keyed, unkeyed = curator.classify_lines(
        "- [2026-07-17] (pref) keep\n- [2026-07-17] tmp\n")
    assert len(keyed) == 1 and len(unkeyed) == 1


def test_console_archive_tail_window_is_4000_chars():
    """The reviewed policy freezes the EXISTING archive-tail size; if the console
    behavior changes, the policy golden must be re-reviewed, not silently drift."""
    import guanlan_v2.console.tools as tools

    src = inspect.getsource(tools._archive_tail)
    assert "4000" in src
    from guanlan_v2.orchestration.memory.catalog import reviewed_capture_policy

    assert reviewed_capture_policy().archive_tail_chars == 4000


def test_consolidate_memory_never_archives_keyed_lines(tmp_path):
    from guanlan_v2.console.curator import consolidate_memory

    mem = tmp_path / "memory.md"
    arch = tmp_path / "memory.archive.md"
    keyed = [f"- [2026-07-17] (k{i}) keep {i}" for i in range(3)]
    unkeyed = [f"- [2026-07-17] tmp {i}" for i in range(5)]
    mem.write_text("\n".join(keyed + unkeyed) + "\n", encoding="utf-8")
    out = consolidate_memory(mem, arch, max_lines=4)
    assert out["ok"] and out["archived"] == 4
    kept = mem.read_text(encoding="utf-8")
    for line in keyed:
        assert line in kept  # keyed lines never leave main memory


def test_facade_never_imports_console_or_engine_writers_at_module_scope():
    """Stage-A memory modules adapt interfaces READ-ONLY and lazily; importing
    them must not import console tools (the concurrent session owns those files)
    or engine writer modules."""
    import sys

    preloaded = {m for m in sys.modules if m.startswith("guanlan_v2.console")}
    import guanlan_v2.orchestration.memory.models  # noqa: F401
    import guanlan_v2.orchestration.memory.schema_registry  # noqa: F401
    import guanlan_v2.orchestration.memory.catalog  # noqa: F401

    after = {m for m in sys.modules if m.startswith("guanlan_v2.console")}
    assert after == preloaded  # no console import as a side effect
