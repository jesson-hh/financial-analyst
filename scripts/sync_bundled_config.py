#!/usr/bin/env python
"""Regenerate bundled config subdirs (swarm presets + universes) from git.

``config/swarm/*.yaml`` and ``config/universes/*.txt`` are siblings of ``src/``
and are NOT included in the wheel. Mirror the git-tracked copies into
``src/financial_analyst/_resources/config/{swarm,universes}/`` so pip-installed
users get them (resolved through ``_config.find_config`` /
``bundled_config_dir``). The flat ``config/*.yaml`` defaults are maintained
separately and are deliberately left untouched here.

Run before cutting a release whenever swarm presets or universes change::

    python scripts/sync_bundled_config.py
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SRC_CONFIG = REPO / "config"
DEST_CONFIG = REPO / "src" / "financial_analyst" / "_resources" / "config"
SUBDIRS = ("swarm", "universes")


def _tracked(subdir: str) -> list[Path]:
    out = subprocess.run(
        ["git", "ls-files", f"config/{subdir}"],
        cwd=REPO, capture_output=True, text=True, check=True,
    ).stdout
    return [REPO / line for line in out.splitlines() if line.strip()]


def main() -> int:
    total = 0
    for sub in SUBDIRS:
        files = _tracked(sub)
        if not files:
            print(f"warning: no git-tracked files under config/{sub}", file=sys.stderr)
            continue
        target = DEST_CONFIG / sub
        if target.exists():
            shutil.rmtree(target)
        for src in files:
            rel = src.relative_to(SRC_CONFIG)
            dst = DEST_CONFIG / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            total += 1
    print(f"synced {total} files -> {DEST_CONFIG.relative_to(REPO)}/{{{','.join(SUBDIRS)}}}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
