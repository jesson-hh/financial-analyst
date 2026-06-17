"""Workspace — single root dir for all of financial-analyst's per-user state.

Why: stock data is large (155MB demo · 3GB lite · 14GB full + 5min + F10).
Many users on Windows can't put that on the system drive (C:). This module
lets them pin a workspace root (e.g. ``D:\\fa-workspace``) once via the
``fa init`` wizard, after which every subsequent ``fa`` invocation honours
it transparently.

Layout under a workspace root:

    <workspace>/
        .env                     ← LLM keys (also at $HOME by default, see below)
        config/loaders.yaml      ← data paths
        data/                    ← Qlib bin + Parquet (the heavy stuff)
            cn_data/
            cn_data_5min/
            parquet/
            news_data/
        out/                     ← per-run report markdown
        logs/                    ← launch / serve logs
        cache/                   ← model artefacts, zoo snapshots

Resolution order for ``get_workspace()``:

    1. Explicit ``override`` argument (e.g. ``--workspace D:\\xxx``)
    2. ``$FA_WORKSPACE`` env var
    3. ``~/.financial-analyst/.workspace`` pointer file (set by ``fa init``)
    4. ``~/.financial-analyst/`` (default — preserves pre-v1.0.3 behaviour)

The pointer file ALWAYS lives at ``~/.financial-analyst/.workspace`` even
when the workspace itself is somewhere else — that's the one place fa
knows to look from a fresh start.
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Optional


# ──────────────────────── constants ────────────────────────


_HOME_DIR = Path.home() / ".financial-analyst"
DEFAULT_WORKSPACE = _HOME_DIR
WORKSPACE_POINTER = _HOME_DIR / ".workspace"


# ──────────────────────── resolution ────────────────────────


def get_workspace(override: Optional[Path] = None) -> Path:
    """Return the active workspace root.

    Priority (first hit wins):
      1. ``override`` arg (CLI flag)
      2. ``$FA_WORKSPACE`` env var
      3. ``~/.financial-analyst/.workspace`` pointer file
      4. ``~/.financial-analyst/`` (legacy default)
    """
    if override:
        p = Path(override).expanduser().resolve()
        return p

    env = os.environ.get("FA_WORKSPACE", "").strip()
    if env:
        return Path(env).expanduser().resolve()

    if WORKSPACE_POINTER.exists():
        try:
            txt = WORKSPACE_POINTER.read_text(encoding="utf-8").strip()
            if txt:
                return Path(txt).expanduser().resolve()
        except Exception:
            pass

    return DEFAULT_WORKSPACE


def set_workspace(path: Path) -> Path:
    """Pin a workspace root persistently. Returns the resolved path.

    Writes a pointer file at ``~/.financial-analyst/.workspace`` so future
    ``fa`` invocations pick it up. Creates the workspace dir if missing.
    """
    target = Path(path).expanduser().resolve()
    target.mkdir(parents=True, exist_ok=True)
    WORKSPACE_POINTER.parent.mkdir(parents=True, exist_ok=True)
    WORKSPACE_POINTER.write_text(str(target), encoding="utf-8")
    return target


def clear_workspace() -> None:
    """Remove the pointer file, falling back to the default workspace."""
    try:
        if WORKSPACE_POINTER.exists():
            WORKSPACE_POINTER.unlink()
    except Exception:
        pass


# ──────────────────────── convenience accessors ────────────────────────


def data_dir(workspace: Optional[Path] = None) -> Path:
    """Bulk data directory (Qlib bin + Parquet). The big one."""
    return get_workspace(workspace) / "data"


def config_dir(workspace: Optional[Path] = None) -> Path:
    """Per-workspace config directory (``loaders.yaml`` etc.)."""
    return get_workspace(workspace) / "config"


def out_dir(workspace: Optional[Path] = None) -> Path:
    """Default report output directory."""
    return get_workspace(workspace) / "out"


def logs_dir(workspace: Optional[Path] = None) -> Path:
    """Launch / serve log directory."""
    return get_workspace(workspace) / "logs"


def cache_dir(workspace: Optional[Path] = None) -> Path:
    """Model / factor cache directory."""
    return get_workspace(workspace) / "cache"


# ──────────────────────── disk-space probe ────────────────────────


def disk_free_gb(path: Path) -> float:
    """Return free space in GB for the disk holding ``path``.

    Walks up to find an existing parent (so we can probe before creating
    the target dir). Returns 0.0 on any failure.
    """
    probe = Path(path).expanduser()
    while not probe.exists():
        parent = probe.parent
        if parent == probe:
            break  # reached root
        probe = parent
    try:
        usage = shutil.disk_usage(str(probe))
        return usage.free / 1e9
    except Exception:
        return 0.0


def is_writable(path: Path) -> bool:
    """Best-effort check that we can write under ``path``."""
    target = Path(path).expanduser()
    target_resolved = target if target.exists() else target.parent
    while not target_resolved.exists():
        next_p = target_resolved.parent
        if next_p == target_resolved:
            break
        target_resolved = next_p
    return os.access(str(target_resolved), os.W_OK)


__all__ = [
    "DEFAULT_WORKSPACE", "WORKSPACE_POINTER",
    "get_workspace", "set_workspace", "clear_workspace",
    "data_dir", "config_dir", "out_dir", "logs_dir", "cache_dir",
    "disk_free_gb", "is_writable",
]
