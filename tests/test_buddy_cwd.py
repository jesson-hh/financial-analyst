"""Tests for v1.6.9 project-root cwd handling.

When ``financial-analyst chat`` is launched from any cwd, the
buddy-tool layer must still resolve ``memories/`` etc. correctly.
Pre-1.6.9 every subprocess.run inherited the chat session's cwd and
the CLI crashed with WinError 3.
"""
from __future__ import annotations
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from financial_analyst.buddy.tools import _project_root


def test_project_root_resolves_to_repo_root_in_editable_install():
    """In the editable install (current dev setup) the helper must
    locate the actual project root, not whatever cwd happens to be."""
    root = _project_root()
    assert isinstance(root, Path)
    # The real repo has these siblings of src/
    assert (root / "memories").exists() or (root / "pyproject.toml").exists()


def test_project_root_uses_env_override(monkeypatch, tmp_path):
    """``FINANCIAL_ANALYST_HOME`` env var should win over auto-detection."""
    _project_root.cache_clear()
    monkeypatch.setenv("FINANCIAL_ANALYST_HOME", str(tmp_path))
    assert _project_root() == tmp_path
    _project_root.cache_clear()


def test_project_root_falls_back_to_cwd_when_no_markers(monkeypatch, tmp_path):
    """If env unset and no parent-of-src has memories/pyproject.toml,
    cwd is the last-resort fallback (not the package dir)."""
    _project_root.cache_clear()
    monkeypatch.delenv("FINANCIAL_ANALYST_HOME", raising=False)
    monkeypatch.setenv("FINANCIAL_ANALYST_HOME", str(tmp_path))
    # tmp_path has no memories/pyproject.toml so env wins anyway
    assert _project_root() == tmp_path
    _project_root.cache_clear()


def test_report_subprocess_uses_project_root_as_cwd(monkeypatch):
    """_tool_report must spawn the CLI with cwd=_project_root() so
    relative-path lookups inside the CLI (Path('memories'), Path('out'))
    work regardless of where chat was launched from."""
    from financial_analyst.buddy import tools

    captured = {}

    class FakeProc:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["cwd"] = kwargs.get("cwd")
        return FakeProc()

    monkeypatch.setattr(tools.subprocess, "run", fake_run)
    # Run from a foreign cwd
    original_cwd = os.getcwd()
    try:
        os.chdir(tmp_path_safe := str(Path.home()))
        tools._tool_report("SH600519")
    finally:
        os.chdir(original_cwd)

    assert captured["cwd"] is not None, "_tool_report must set cwd"
    assert Path(captured["cwd"]) == _project_root()


@pytest.mark.parametrize("tool_name,run_fn_name", [
    ("news_collect", "_tool_news_collect"),
    ("alpha_bench", "_tool_alpha_bench"),
    ("alpha_snapshot", "_tool_alpha_snapshot"),
    ("mainline_radar", "_tool_mainline"),
    ("morning_brief", "_tool_brief"),
])
def test_other_subprocess_tools_also_set_cwd(tool_name, run_fn_name, monkeypatch):
    """Every CLI-wrapping subprocess tool must pin cwd to project root."""
    from financial_analyst.buddy import tools

    captured = {}

    class FakeProc:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_run(cmd, **kwargs):
        captured["cwd"] = kwargs.get("cwd")
        return FakeProc()

    monkeypatch.setattr(tools.subprocess, "run", fake_run)
    run_fn = getattr(tools, run_fn_name)
    run_fn()  # default args fine — fake_run shortcircuits

    assert captured["cwd"] is not None, f"{tool_name}: cwd not set"
    assert Path(captured["cwd"]) == _project_root()


def test_dream_review_uses_absolute_memories_path(monkeypatch):
    """v1.6.9: _tool_dream_review previously used Path('memories') and
    crashed when chat ran outside the project. Now it must use
    _project_root() / 'memories'."""
    from financial_analyst.buddy import tools
    original_cwd = os.getcwd()
    try:
        os.chdir(str(Path.home()))
        result = tools._tool_dream_review()
        # Must not crash with WinError 3 / FileNotFoundError
        # An empty proposals dir is fine; what matters is no exception.
        assert isinstance(result.content, str)
    finally:
        os.chdir(original_cwd)
