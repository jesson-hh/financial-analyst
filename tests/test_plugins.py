"""Tests for plugin discovery loader."""
import pytest
from pathlib import Path
from financial_analyst.plugins import load_plugins


def test_load_plugins_no_config(tmp_path):
    """No config file → empty list, no error."""
    result = load_plugins(config_path=tmp_path / "nonexistent.yaml")
    assert result == []


def test_load_plugins_empty_list(tmp_path):
    cfg = tmp_path / "plugins.yaml"
    cfg.write_text("load_at_startup: []", encoding="utf-8")
    assert load_plugins(config_path=cfg) == []


def test_load_plugins_executes_user_file(tmp_path):
    """A plugin file that defines a global is exec'd correctly."""
    plugin = tmp_path / "my_plugin.py"
    plugin.write_text(
        "import builtins\n"
        "builtins.__FA_TEST_FLAG__ = 'loaded'\n",
        encoding="utf-8",
    )
    cfg = tmp_path / "plugins.yaml"
    cfg.write_text(f"load_at_startup:\n  - {plugin}\n", encoding="utf-8")
    import builtins
    if hasattr(builtins, "__FA_TEST_FLAG__"):
        del builtins.__FA_TEST_FLAG__
    loaded = load_plugins(config_path=cfg)
    assert len(loaded) == 1
    assert hasattr(builtins, "__FA_TEST_FLAG__")
    assert getattr(builtins, "__FA_TEST_FLAG__") == "loaded"
    del builtins.__FA_TEST_FLAG__


def test_load_plugins_skips_missing_file(tmp_path):
    cfg = tmp_path / "plugins.yaml"
    cfg.write_text("load_at_startup:\n  - /nonexistent/foo.py\n", encoding="utf-8")
    assert load_plugins(config_path=cfg) == []


def test_load_plugins_tolerates_broken_plugin(tmp_path):
    """A plugin that raises at import-time → warning logged, others still load."""
    bad = tmp_path / "bad.py"
    bad.write_text("raise RuntimeError('intentional')", encoding="utf-8")
    good = tmp_path / "good.py"
    good.write_text("x = 1", encoding="utf-8")
    cfg = tmp_path / "plugins.yaml"
    cfg.write_text(f"load_at_startup:\n  - {bad}\n  - {good}\n", encoding="utf-8")
    loaded = load_plugins(config_path=cfg)
    assert len(loaded) == 1
    assert str(good) in loaded
