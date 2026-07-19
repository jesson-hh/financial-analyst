#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Thin CLI: mirror the single-source skills tree into the derived materials mirror.

    python guanlan_v2/orchestration/skills/sync-skills.py [--root DIR] \
        [--materials-root DIR] [--dry-run]

The tree (``guanlan_v2/orchestration/skills/<worker_id>/SKILL.md``) is the only
human-edited location; this script writes ``config/orchestration/materials/skills/
<skill_id>.md`` verbatim. It is a thin wrapper over the pure core
:mod:`guanlan_v2.orchestration.skilltree`; the hyphenated filename means it is only ever
run via ``python`` / ``runpy`` / ``subprocess`` and is never imported as a module.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# make the repo root importable when run as a bare script (script dir != repo root)
_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from guanlan_v2.orchestration import skilltree  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="sync the skills tree to its derived mirror")
    ap.add_argument("--root", default=str(skilltree.DEFAULT_TREE_ROOT),
                    help="skills tree root (default: the in-repo tree)")
    ap.add_argument("--materials-root", default=str(skilltree.DEFAULT_MATERIALS_ROOT),
                    help="materials root; mirror is written under <root>/skills/")
    ap.add_argument("--dry-run", action="store_true",
                    help="print the plan but write nothing")
    args = ap.parse_args(argv)

    tree = skilltree.load_skill_tree(Path(args.root))
    actions = skilltree.plan_mirror(tree, materials_root=Path(args.materials_root))
    for a in actions:
        print(f"{a.op:6s} {a.target_label}  {a.source_digest[:12]}")
    if args.dry_run:
        print(f"dry-run: {len(tree)} skill(s), no files written")
        return 0
    skilltree.apply_mirror(actions, tree=tree, materials_root=Path(args.materials_root))
    changed = sum(1 for a in actions if a.op != "noop")
    print(f"applied: {changed} change(s) across {len(tree)} skill(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
