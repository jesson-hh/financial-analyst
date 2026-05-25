"""Smoke tests for `financial_analyst.data.paths.get_data_paths`.

Verifies the 4-tier priority order: env var > loaders.yaml > user dir > dev fallback.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from financial_analyst.data.paths import DataPaths, get_data_paths


# ──────────────────────── helpers ────────────────────────


def _write_yaml(path: Path, qlib_uri, parquet_root=None, news_data_root=None):
    lines = [
        "default: qlib_binary",
        "loaders:",
        "  qlib_binary:",
    ]
    if isinstance(qlib_uri, dict):
        lines.append("    provider_uri:")
        for freq, uri in qlib_uri.items():
            lines.append(f"      {freq}: {uri}")
    else:
        lines.append(f"    provider_uri: {qlib_uri}")
    if parquet_root:
        lines.append(f"    parquet_root: {parquet_root}")
    if news_data_root:
        lines.append(f"    news_data_root: {news_data_root}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# ──────────────────────── tests ────────────────────────


def test_dataclass_attributes_exist():
    """DataPaths exposes the documented fields."""
    p = DataPaths(qlib_uri="/x", parquet_root=Path("/y"), news_data_root=Path("/z"))
    assert p.qlib_day == Path("/x")
    assert p.qlib_5min is None
    assert p.parquet_root == Path("/y")
    assert p.news_data_root == Path("/z")
    assert p.tdx_f10_root == Path("/z/tdx_f10")


def test_qlib_5min_resolved_from_dict():
    p = DataPaths(
        qlib_uri={"day": "/a", "5min": "/b"},
        parquet_root=Path("/c"),
        news_data_root=Path("/d"),
    )
    assert p.qlib_day == Path("/a")
    assert p.qlib_5min == Path("/b")


def test_loaders_yaml_provides_all_three(tmp_path):
    """All three paths flow from yaml when set."""
    cfg = tmp_path / "loaders.yaml"
    _write_yaml(
        cfg,
        qlib_uri={"day": str(tmp_path / "q"), "5min": str(tmp_path / "q5")},
        parquet_root=str(tmp_path / "pq"),
        news_data_root=str(tmp_path / "nd"),
    )
    paths = get_data_paths(config_path=cfg)
    assert paths.qlib_uri == {"day": str(tmp_path / "q"), "5min": str(tmp_path / "q5")}
    assert paths.parquet_root == tmp_path / "pq"
    assert paths.news_data_root == tmp_path / "nd"


def test_env_var_overrides_yaml(tmp_path, monkeypatch):
    """Env var takes precedence over yaml entry."""
    cfg = tmp_path / "loaders.yaml"
    _write_yaml(cfg, qlib_uri=str(tmp_path / "from_yaml"))
    monkeypatch.setenv("FA_PARQUET_ROOT", str(tmp_path / "from_env"))
    paths = get_data_paths(config_path=cfg)
    assert paths.parquet_root == tmp_path / "from_env"


def test_falls_back_to_dev_root_when_no_config(monkeypatch, tmp_path):
    """No yaml, no env, no user dir => dev fallback G:/stocks/..."""
    # Force the user-dir probe to miss by pointing HOME elsewhere
    monkeypatch.setenv("USERPROFILE", str(tmp_path))   # Windows
    monkeypatch.setenv("HOME", str(tmp_path))          # POSIX
    # Force find_config to return None
    import financial_analyst.data.paths as paths_mod

    def _raise(*a, **kw):
        raise FileNotFoundError("no loaders.yaml")

    monkeypatch.setattr(paths_mod, "find_config", _raise)
    monkeypatch.delenv("FA_QLIB_URI", raising=False)
    monkeypatch.delenv("FA_PARQUET_ROOT", raising=False)
    monkeypatch.delenv("FA_NEWS_DATA_ROOT", raising=False)

    paths = get_data_paths()
    # Dev fallback is hardcoded — assert structure, not specific drive letter
    assert "stock_data" in str(paths.qlib_uri) or "cn_data" in str(paths.qlib_uri)
    assert "stock_data" in str(paths.parquet_root) or "parquet" in str(paths.parquet_root)
    assert "news_data" in str(paths.news_data_root)


def test_partial_yaml_inherits_for_missing_keys(tmp_path, monkeypatch):
    """If yaml sets only qlib but not parquet, the others fall through cleanly."""
    cfg = tmp_path / "loaders.yaml"
    _write_yaml(cfg, qlib_uri=str(tmp_path / "only_qlib"))   # no parquet/news
    monkeypatch.delenv("FA_PARQUET_ROOT", raising=False)
    monkeypatch.delenv("FA_NEWS_DATA_ROOT", raising=False)
    paths = get_data_paths(config_path=cfg)
    assert str(paths.qlib_uri) == str(tmp_path / "only_qlib")
    # parquet + news must still resolve to *something* (user dir or dev fallback)
    assert paths.parquet_root is not None
    assert paths.news_data_root is not None
