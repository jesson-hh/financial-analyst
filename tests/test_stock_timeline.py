"""Tests for StockTimelineLoader (v1.4.4)."""
from __future__ import annotations
from pathlib import Path
import pytest

from financial_analyst.data.loaders.stock_timeline import StockTimelineLoader


def test_loader_default_path(monkeypatch, tmp_path):
    monkeypatch.delenv("FA_STOCK_TIMELINE_DIR", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))  # POSIX
    monkeypatch.setenv("USERPROFILE", str(tmp_path))  # Windows
    loader = StockTimelineLoader()
    # Path should land under ~/.financial-analyst/memories/stocks
    assert ".financial-analyst" in str(loader.root)
    assert loader.root.name == "stocks"


def test_loader_env_override(monkeypatch, tmp_path):
    monkeypatch.setenv("FA_STOCK_TIMELINE_DIR", str(tmp_path))
    loader = StockTimelineLoader()
    assert loader.root == tmp_path


def test_has_load_load_tail(tmp_path):
    (tmp_path / "SH600100.md").write_text(
        "# SH600100\n" + ("line\n" * 1000) + "# 最新\n2026-04-30 ★★★★☆\n",
        encoding="utf-8",
    )
    loader = StockTimelineLoader(root=tmp_path)
    assert loader.has("SH600100")
    assert not loader.has("SH999999")

    full = loader.load("SH600100")
    assert full is not None
    assert "# SH600100" in full

    tail = loader.load_tail("SH600100", max_chars=500)
    assert tail is not None
    assert len(tail) <= 600  # ~max_chars + truncation prefix
    assert "最新" in tail
    # Full file's header should NOT be in the tail (it's near the start)
    assert tail.count("# SH600100") <= 1


def test_load_tail_short_file_no_truncation(tmp_path):
    """Files shorter than max_chars should be returned in full, no prefix."""
    (tmp_path / "SH600519.md").write_text("short content", encoding="utf-8")
    loader = StockTimelineLoader(root=tmp_path)
    tail = loader.load_tail("SH600519", max_chars=4000)
    assert tail == "short content"
    assert "truncated" not in tail


def test_load_unknown_code_returns_none(tmp_path):
    loader = StockTimelineLoader(root=tmp_path)
    assert loader.load("SH999999") is None
    assert loader.load_tail("SH999999") is None


def test_list_codes_sorted(tmp_path):
    for c in ["SZ002594", "SH600519", "SH600100"]:
        (tmp_path / f"{c}.md").write_text(f"content {c}", encoding="utf-8")
    loader = StockTimelineLoader(root=tmp_path)
    assert loader.list_codes() == ["SH600100", "SH600519", "SZ002594"]


def test_import_from(tmp_path):
    """Import should copy SH/SZ/BJ codes, skip non-stock files."""
    src = tmp_path / "src"
    src.mkdir()
    (src / "SH600100.md").write_text("aaa", encoding="utf-8")
    (src / "SZ002594.md").write_text("bbb", encoding="utf-8")
    (src / "INDEX.md").write_text("not a stock", encoding="utf-8")
    (src / "missed_bulls.md").write_text("not a stock", encoding="utf-8")
    (src / "BJ430489.md").write_text("ccc", encoding="utf-8")

    dst = tmp_path / "dst"
    loader = StockTimelineLoader(root=dst)
    n = loader.import_from(src)
    assert n == 3  # SH600100, SZ002594, BJ430489 only
    assert loader.has("SH600100")
    assert loader.has("SZ002594")
    assert loader.has("BJ430489")
    assert not loader.has("INDEX")


def test_import_overwrite_flag(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "SH600100.md").write_text("new content", encoding="utf-8")
    dst = tmp_path / "dst"
    dst.mkdir()
    (dst / "SH600100.md").write_text("old content", encoding="utf-8")

    loader = StockTimelineLoader(root=dst)
    # First pass without overwrite: should skip
    n = loader.import_from(src, overwrite=False)
    assert n == 0
    assert loader.load("SH600100") == "old content"
    # With overwrite: should replace
    n = loader.import_from(src, overwrite=True)
    assert n == 1
    assert loader.load("SH600100") == "new content"


def test_import_missing_source_raises(tmp_path):
    loader = StockTimelineLoader(root=tmp_path)
    with pytest.raises(FileNotFoundError):
        loader.import_from(tmp_path / "does-not-exist")


def test_stats(tmp_path):
    loader = StockTimelineLoader(root=tmp_path)
    assert loader.stats()["n_codes"] == 0
    (tmp_path / "SH600100.md").write_text("a" * 1024, encoding="utf-8")
    (tmp_path / "SZ002594.md").write_text("b" * 2048, encoding="utf-8")
    s = loader.stats()
    assert s["n_codes"] == 2
    assert s["total_bytes"] == 1024 + 2048
    assert s["avg_kb"] == 1.5
