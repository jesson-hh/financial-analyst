#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Thin CLI drift-lint: fail (nonzero) if the mirror drifts from the single-source tree.

    python guanlan_v2/orchestration/skills/check.py [--root DIR] \
        [--materials-root DIR] [--catalog-golden PATH]

Checks byte identity + skill-v1 grammar of every mirror file and, when a catalog golden
is supplied, cross-checks each ``skill.<id>`` manifest digest against the tree. Exit 0 iff
:func:`guanlan_v2.orchestration.skilltree.lint_drift` returns ``()``; a tree load failure
exits 2. It is a thin wrapper over the pure core; the hyphenated filename means it is only
ever run via ``python`` / ``runpy`` / ``subprocess`` and is never imported as a module.
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
from guanlan_v2.orchestration.catalog import WorkerCatalogSnapshot  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="lint the skills tree ⇄ mirror for drift")
    ap.add_argument("--root", default=str(skilltree.DEFAULT_TREE_ROOT))
    ap.add_argument("--materials-root", default=str(skilltree.DEFAULT_MATERIALS_ROOT))
    ap.add_argument("--catalog-golden", default=None,
                    help="optional WorkerCatalogSnapshot JSON for the digest cross-check")
    args = ap.parse_args(argv)

    try:
        tree = skilltree.load_skill_tree(Path(args.root))
    except Exception as exc:  # noqa: BLE001 — surface any load failure as exit 2
        print(f"SKILL TREE LOAD ERROR: {exc}", file=sys.stderr)
        return 2

    catalog = None
    if args.catalog_golden:
        gp = Path(args.catalog_golden)
        if not gp.exists():
            print(f"catalog golden not found: {gp}", file=sys.stderr)
            return 2
        catalog = WorkerCatalogSnapshot.model_validate_json(gp.read_text(encoding="utf-8"))

    issues = skilltree.lint_drift(
        tree, materials_root=Path(args.materials_root), catalog=catalog)
    for it in issues:
        print(f"DRIFT[{it.code}] {it.skill_id}: {it.detail}", file=sys.stderr)
    if issues:
        print(f"{len(issues)} drift issue(s) — run sync-skills.py", file=sys.stderr)
        return 1
    print(f"OK: {len(tree)} skill(s), mirror clean")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
