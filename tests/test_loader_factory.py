"""Tests for the loader factory (get_default_loader)."""
import struct

import numpy as np
import pandas as pd
import pytest

from financial_analyst.data.loader_factory import get_default_loader
from financial_analyst.data.loaders.tushare import TushareLoader
from financial_analyst.data.loaders.qlib_binary import QlibBinaryLoader


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_minimal_qlib_dir(root):
    """Minimal Qlib directory: calendar + no features (enough for loader init)."""
    (root / "calendars").mkdir(parents=True)
    (root / "calendars" / "day.txt").write_text("2026-05-01\n2026-05-05\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_factory_returns_tushare_by_default(tmp_path, monkeypatch):
    monkeypatch.setenv("TUSHARE_TOKEN", "fake")
    cfg = tmp_path / "loaders.yaml"
    cfg.write_text("default: tushare\nloaders:\n  tushare: {}\n", encoding="utf-8")
    loader = get_default_loader(config_path=cfg)
    assert isinstance(loader, TushareLoader)


def test_factory_tushare_cache_settings_applied(tmp_path, monkeypatch):
    monkeypatch.setenv("TUSHARE_TOKEN", "fake")
    cfg = tmp_path / "loaders.yaml"
    cfg.write_text(
        "default: tushare\nloaders:\n  tushare:\n    cache_enabled: false\n    cache_ttl_seconds: 3600\n",
        encoding="utf-8",
    )
    loader = get_default_loader(config_path=cfg)
    assert isinstance(loader, TushareLoader)
    assert loader.enable_cache is False


def test_factory_returns_qlib_binary(tmp_path):
    qlib_root = tmp_path / "qlib_data"
    _make_minimal_qlib_dir(qlib_root)
    cfg = tmp_path / "loaders.yaml"
    cfg.write_text(
        f"default: qlib_binary\nloaders:\n  qlib_binary:\n    provider_uri: {qlib_root}\n",
        encoding="utf-8",
    )
    loader = get_default_loader(config_path=cfg)
    assert isinstance(loader, QlibBinaryLoader)


def test_factory_qlib_binary_missing_uri_raises(tmp_path):
    cfg = tmp_path / "loaders.yaml"
    cfg.write_text(
        "default: qlib_binary\nloaders:\n  qlib_binary: {}\n", encoding="utf-8"
    )
    with pytest.raises(ValueError, match="provider_uri"):
        get_default_loader(config_path=cfg)


def test_factory_missing_config_falls_back_to_tushare(tmp_path, monkeypatch):
    monkeypatch.setenv("TUSHARE_TOKEN", "fake")
    loader = get_default_loader(config_path=tmp_path / "nonexistent.yaml")
    assert isinstance(loader, TushareLoader)


def test_factory_unknown_loader_name_falls_back_to_tushare(tmp_path, monkeypatch):
    monkeypatch.setenv("TUSHARE_TOKEN", "fake")
    cfg = tmp_path / "loaders.yaml"
    cfg.write_text("default: unknown_loader\nloaders: {}\n", encoding="utf-8")
    loader = get_default_loader(config_path=cfg)
    assert isinstance(loader, TushareLoader)
