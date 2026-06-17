"""Locate the agent ``memories/`` root, seeding bundled defaults for pip users.

This is the same problem (and shape) as :mod:`financial_analyst._config`, which
bundles ``config/`` under ``_resources/`` because the source ``config/`` dir is
not shipped in the wheel. The source-tree ``memories/`` directory is a sibling
of ``src/`` and is likewise NOT in the wheel, so a pip-installed user has no
``memories/`` relative to their working directory. Resolving it as the bare
relative ``Path("memories")`` therefore crashed the first time anything scanned
it::

    FileNotFoundError: [WinError 3] 系统找不到指定的路径。: 'memories'

(On Python 3.13 ``Path.iterdir()`` is implemented via ``os.scandir`` and raises
on a missing directory; earlier versions silently yielded nothing, which merely
hid the bug.)

Lookup order (first hit wins)::

    1. $FINANCIAL_ANALYST_HOME/memories   (explicit project-home override)
    2. <cwd>/memories                     (dev / source checkout — unchanged)
    3. ~/.financial-analyst/memories      (pip-user default; seeded once)

Locations 1 and 3 are created and, when empty, seeded from the bundled
``financial_analyst/_resources/memories_seed`` so reports always have the agent
rules/playbooks to work with.
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path

_PACKAGE_DIR = Path(__file__).resolve().parent
_BUNDLED_SEED_DIR = _PACKAGE_DIR / "_resources" / "memories_seed"


def bundled_seed_dir() -> Path:
    """Path to the package's bundled seed memories (shipped inside the wheel).

    Useful for ``init`` commands that want to copy defaults into the user's
    writable location.
    """
    return _BUNDLED_SEED_DIR


def _ensure_seeded(target: Path) -> Path:
    """Create ``target`` and, when it is empty/new, copy the bundled seed in.

    Never overwrites existing content: seeding only happens for a fresh
    (missing or empty) directory, so a user's accumulated/edited memories and
    dream-loop writes are left untouched.
    """
    populated = target.is_dir() and any(target.iterdir())
    if populated:
        return target
    if _BUNDLED_SEED_DIR.is_dir():
        shutil.copytree(_BUNDLED_SEED_DIR, target, dirs_exist_ok=True)
    else:
        # No bundled seed (e.g. an unusual build) — still hand back a real,
        # writable directory so callers don't crash on a missing path.
        target.mkdir(parents=True, exist_ok=True)
    return target


def default_memory_root() -> Path:
    """Resolve the writable ``memories/`` root (see module docstring for order)."""
    env = os.environ.get("FINANCIAL_ANALYST_HOME", "").strip()
    if env:
        return _ensure_seeded(Path(env).expanduser() / "memories")

    cwd_mem = Path.cwd() / "memories"
    if cwd_mem.is_dir():
        return cwd_mem

    return _ensure_seeded(Path.home() / ".financial-analyst" / "memories")
