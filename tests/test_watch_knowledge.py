"""Tests for watch/knowledge.py — load curated watch-agent 盘中守则.

多数用 tmp memory_root (不碰真实 default_memory_root / 不触发 seeding). 关键断言:
own-files-only 拼接 / 跳过 _shared / 缺目录→'' / 真 bundled seed 有验证知识锚点.
"""
from __future__ import annotations

from pathlib import Path

from financial_analyst.watch.knowledge import _read_agent_dir, load_watch_knowledge


def _seed(root: Path, name: str, text: str) -> None:
    d = root / "watch-agent"
    d.mkdir(parents=True, exist_ok=True)
    (d / name).write_text(text, encoding="utf-8")


def test_reads_own_agent_dir(tmp_path):
    _seed(tmp_path, "a.md", "反转是核心")
    assert "反转是核心" in load_watch_knowledge(memory_root=tmp_path)


def test_concatenates_multiple_md(tmp_path):
    _seed(tmp_path, "a.md", "块A")
    _seed(tmp_path, "b.md", "块B")
    out = load_watch_knowledge(memory_root=tmp_path)
    assert "块A" in out and "块B" in out


def test_missing_dir_returns_empty(tmp_path):
    assert load_watch_knowledge(memory_root=tmp_path) == ""   # no watch-agent subdir


def test_skips_shared(tmp_path):
    """_shared/*.md must NOT be pulled in (own-files-only, budget guard)."""
    (tmp_path / "_shared").mkdir(parents=True)
    (tmp_path / "_shared" / "big.md").write_text("不该出现", encoding="utf-8")
    _seed(tmp_path, "a.md", "应出现")
    out = load_watch_knowledge(memory_root=tmp_path)
    assert "应出现" in out
    assert "不该出现" not in out


def test_read_agent_dir_helper(tmp_path):
    _seed(tmp_path, "x.md", "hi")
    assert _read_agent_dir(tmp_path) == "hi"
    assert _read_agent_dir(tmp_path / "nope") == ""


def test_bundled_seed_has_validated_anchors():
    """真 bundled seed (_resources/memories_seed/watch-agent/intraday_playbook.md)."""
    from financial_analyst.memory_paths import bundled_seed_dir
    out = load_watch_knowledge(memory_root=bundled_seed_dir())
    assert out                       # non-empty (ships in the package)
    assert "反转" in out
    assert "super_distr" in out
    assert "游资博弈票" in out
