"""Regression tests: swarm presets + universes must ship in the wheel.

Same bug class as the memories/ crash (v1.0.8): `config/swarm/` and
`config/universes/` are siblings of `src/` and were not in the wheel, yet the
swarm loader resolved presets via ``Path(__file__).parents[3] / "config" /
"swarm"`` — which on a wheel install lands at ``<python>/Lib/config/swarm`` and
crashed with FileNotFoundError on ``load_preset("stock-deep-dive")``.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from financial_analyst._config import bundled_config_dir, find_config

SWARM_PRESETS = [
    "stock-deep-dive",
    "mainline-radar",
    "morning-brief",
    "overseas-radar",
    "intraday-review",
]


def test_swarm_presets_bundled():
    """All swarm presets must be bundled so the wheel can run load_preset."""
    d = bundled_config_dir() / "swarm"
    if not d.is_dir():
        pytest.fail(f"bundled swarm dir missing: {d} — wheel crashes on load_preset")
    for name in SWARM_PRESETS:
        assert (d / f"{name}.yaml").is_file(), f"missing bundled preset {name}.yaml"


def test_universes_bundled():
    """Named universes must be bundled so `--universe csi300...` works on pip."""
    d = bundled_config_dir() / "universes"
    assert d.is_dir(), f"bundled universes dir missing: {d}"
    assert any(d.glob("*.txt")), "no bundled universe .txt files"


def test_find_config_resolves_swarm_from_bundle(tmp_path, monkeypatch):
    """A pip user (no ./config, no ~/.financial-analyst/config, no env) must
    still resolve a swarm preset — from the bundled _resources copy."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("FA_CONFIG_DIR", raising=False)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "home"))
    # find_config also probes get_workspace()/config; that resolver caches a
    # real workspace (the repo root) from earlier tests in the full suite, so
    # force it to an empty dir to truly simulate a fresh pip user.
    monkeypatch.setattr("financial_analyst.workspace.get_workspace", lambda: tmp_path / "ws")

    p = find_config("swarm/stock-deep-dive.yaml")

    assert p.is_file()
    assert "_resources" in str(p), f"expected bundled resolution, got {p}"


def test_load_preset_no_filenotfound_when_repo_config_absent(tmp_path, monkeypatch):
    """Reproduces the reported crash: on a wheel install the repo-root
    config/swarm/ does not exist (Path(__file__).parents[3] lands in
    <python>/Lib), so load_preset must fall back to the bundled preset instead
    of raising FileNotFoundError on the YAML read.

    We simulate the wheel layout by pointing the legacy repo-root PRESET_DIR at
    a nonexistent dir and clearing the cwd/home/env config locations. A later
    KeyError from an unpopulated agent registry is fine — path resolution (not
    DAG construction) is what this test pins down.
    """
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("FA_CONFIG_DIR", raising=False)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "home"))

    import financial_analyst.swarm.loader as loader_mod
    monkeypatch.setattr(loader_mod, "PRESET_DIR", tmp_path / "nope" / "swarm", raising=False)

    try:
        loader_mod.load_preset("stock-deep-dive", memory_root=tmp_path / "mem")
    except FileNotFoundError as exc:
        pytest.fail(f"preset YAML not found — the wheel-install crash: {exc}")
    except KeyError:
        pass  # agent registry not populated in this isolated test; resolution succeeded
