# -*- coding: utf-8 -*-
"""Daily swarm subprocesses must execute the in-repo engine fork."""
from __future__ import annotations

import os
import subprocess
import sys
from datetime import date
from pathlib import Path
from types import SimpleNamespace

from financial_analyst.buddy import tools as bt


def test_daily_cached_swarm_passes_repo_engine_and_config_to_subprocess(
    tmp_path, monkeypatch,
):
    root = tmp_path / "repo"
    (root / "engine").mkdir(parents=True)
    (root / "config").mkdir()
    captured = {}

    def fake_run(cmd, **kwargs):
        captured.update(cmd=cmd, **kwargs)
        return SimpleNamespace(returncode=0, stdout="fresh output", stderr="")

    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "home"))
    monkeypatch.setattr(bt, "_project_root", lambda: root)
    monkeypatch.setattr(bt.subprocess, "run", fake_run)
    monkeypatch.setenv("PYTHONPATH", os.pathsep.join(["legacy-one", "legacy-two"]))
    monkeypatch.setenv("KEEP_ME", "preserved")

    result = bt._daily_cached_swarm(
        "sealed-test", ["financial-analyst", "mainline"], "主线测试", timeout=17,
    )

    assert result.is_error is False and result.content == "fresh output"
    assert captured["cwd"] == str(root)
    assert captured["timeout"] == 17
    env = captured["env"]
    assert env["KEEP_ME"] == "preserved"
    assert env["FA_CONFIG_DIR"] == str(root / "config")
    assert env["PYTHONIOENCODING"] == "utf-8"
    parts = env["PYTHONPATH"].split(os.pathsep)
    assert parts[:4] == [str(root / "engine"), str(root), "legacy-one", "legacy-two"]


def test_daily_swarm_env_imports_financial_analyst_from_repo_engine():
    root = Path(__file__).resolve().parents[1]
    env = bt._daily_swarm_env(root)

    proc = subprocess.run(
        [sys.executable, "-c", "import financial_analyst; print(financial_analyst.__file__)"],
        cwd=str(root),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=15,
    )

    assert proc.returncode == 0, proc.stderr
    imported = Path(proc.stdout.strip()).resolve()
    imported.relative_to((root / "engine").resolve())


def test_mainline_uses_versioned_cache_key_but_keeps_user_tool_command(monkeypatch):
    captured = {}

    def fake_cached(slug, cmd, cn, timeout=300):
        captured.update(slug=slug, cmd=cmd, cn=cn, timeout=timeout)
        return bt.ToolResult("ok")

    monkeypatch.setattr(bt, "_daily_cached_swarm", fake_cached)

    result = bt._tool_mainline()

    assert result.content == "ok"
    assert captured == {
        "slug": "mainline_research_v2",
        "cmd": ["financial-analyst", "mainline"],
        "cn": "主线雷达",
        "timeout": 180,
    }


def test_versioned_mainline_failure_never_falls_back_to_legacy_mainline_cache(
    tmp_path, monkeypatch,
):
    home = tmp_path / "home"
    cache_dir = home / ".financial-analyst" / "cache" / "daily_tools"
    cache_dir.mkdir(parents=True)
    (cache_dir / f"mainline_{date.today().isoformat()}.txt").write_text(
        "legacy mainline without report context", encoding="utf-8",
    )
    calls = []

    def failed_run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        return SimpleNamespace(returncode=1, stdout="", stderr="sealed failure")

    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.setattr(bt.subprocess, "run", failed_run)

    result = bt._tool_mainline()

    assert calls  # old unversioned cache did not short-circuit the new implementation
    assert result.is_error is True
    assert "legacy mainline without report context" not in result.content
    assert "sealed failure" in result.content


def test_mainline_tool_description_keeps_reports_subordinate_to_price_volume_ranking():
    tool = next(item for item in bt.TOOL_REGISTRY if item.name == "mainline_radar")

    assert "price/volume state first" in tool.description
    assert "latest attributed research-report views" in tool.description
    assert "1-3 month scenarios" in tool.description
    assert "never change candidate selection/ranking" in tool.description
