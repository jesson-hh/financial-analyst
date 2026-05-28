#!/usr/bin/env python
"""Regenerate the bundled memories seed from git-tracked ``memories/``.

The wheel ships agent memories under
``src/financial_analyst/_resources/memories_seed/`` so pip users get the agent
rules/playbooks out of the box (the source-tree ``memories/`` is a sibling of
``src/`` and is NOT included in the wheel). That mirror is generated from the
*git-tracked* files under ``memories/`` — which by construction excludes the
gitignored runtime/private state (``_proposed/``, ``_pending_introspections/``,
``_shared/conversation_lessons.md``).

Run this before building a release whenever tracked memories change::

    python scripts/sync_memories_seed.py
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SRC_MEMORIES = REPO / "memories"
SEED = REPO / "src" / "financial_analyst" / "_resources" / "memories_seed"


def tracked_memory_files() -> list[Path]:
    out = subprocess.run(
        ["git", "ls-files", "memories"],
        cwd=REPO, capture_output=True, text=True, check=True,
    ).stdout
    return [REPO / line for line in out.splitlines() if line.strip()]


def main() -> int:
    files = tracked_memory_files()
    if not files:
        print("no git-tracked files under memories/ — nothing to seed", file=sys.stderr)
        return 1
    if SEED.exists():
        shutil.rmtree(SEED)
    SEED.mkdir(parents=True)
    n = 0
    for src in files:
        rel = src.relative_to(SRC_MEMORIES)
        dst = SEED / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        n += 1
    print(f"seeded {n} files -> {SEED.relative_to(REPO)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
