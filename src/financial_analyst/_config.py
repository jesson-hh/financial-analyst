"""Config file locator — finds bundled or user-overridden YAML configs.

When pip-installed, the source tree's ``config/`` directory is NOT
included in the wheel. To make pip-installed users work out of the box,
we bundle a copy under ``financial_analyst/_resources/config/`` AND
support a chain of override locations.

Lookup order (first hit wins)::

    1. Explicit ``path`` argument                              (caller override)
    2. ``$FA_CONFIG_DIR/<name>``                               (env var override)
    3. ``~/.financial-analyst/config/<name>``                  (user override)
    4. ``<cwd>/config/<name>``                                 (dev mode / repo root)
    5. Bundled ``financial_analyst/_resources/config/<name>``  (shipped defaults)

If none of these exist a ``FileNotFoundError`` is raised with the
checked paths in the message — so the user can see exactly which
locations were searched.
"""
from __future__ import annotations
import os
from pathlib import Path
from typing import List, Optional


_PACKAGE_DIR = Path(__file__).resolve().parent
_BUNDLED_CONFIG_DIR = _PACKAGE_DIR / "_resources" / "config"


def find_config(name: str, explicit: Optional[Path] = None) -> Path:
    """Return the first existing path for config file ``name`` (e.g. ``"llm.yaml"``).

    Raises FileNotFoundError if no candidate path exists, listing the
    locations checked.
    """
    candidates = config_candidates(name, explicit=explicit)
    for p in candidates:
        if p.is_file():
            return p
    msg = (
        f"Config file {name!r} not found. Searched:\n  "
        + "\n  ".join(str(p) for p in candidates)
    )
    raise FileNotFoundError(msg)


def config_candidates(name: str, explicit: Optional[Path] = None) -> List[Path]:
    """Return the list of candidate paths in lookup order, for diagnostics.

    v1.0.3+: also probes ``<workspace>/config/<name>`` so a workspace pinned
    to e.g. ``D:\\fa-data`` is honoured. Falls back to ``~/.financial-analyst/``
    for users on the legacy default.
    """
    candidates: List[Path] = []
    if explicit is not None:
        candidates.append(Path(explicit))
    env_dir = os.environ.get("FA_CONFIG_DIR", "").strip()
    if env_dir:
        candidates.append(Path(env_dir).expanduser() / name)
    # Workspace-aware lookup (honours user's pinned workspace pointer)
    try:
        from financial_analyst.workspace import get_workspace
        ws = get_workspace()
        candidates.append(ws / "config" / name)
    except Exception:
        pass
    legacy = Path.home() / ".financial-analyst" / "config" / name
    if legacy not in candidates:
        candidates.append(legacy)
    candidates.append(Path.cwd() / "config" / name)
    candidates.append(_BUNDLED_CONFIG_DIR / name)
    return candidates


def bundled_config_dir() -> Path:
    """Path to the package's bundled defaults — useful for `init` commands
    that copy them into the user's override location."""
    return _BUNDLED_CONFIG_DIR
