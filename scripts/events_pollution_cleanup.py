#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Audit a reviewed events.db pollution plan or prepare a clean candidate."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from guanlan_v2.events.cleanup import (  # noqa: E402
    CleanupPlanError,
    audit_cleanup_plan,
    execute_cleanup_plan,
    load_plan,
    validate_candidate_paths,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Fail-closed events.db pollution remediation. Default is immutable "
            "dry-run; --execute requires a v2 child/trigger-bound plan, creates "
            "backup + clean candidate, and never edits DB."
        )
    )
    parser.add_argument("--db", required=True, type=Path,
                        help="explicit absolute source events.db path")
    parser.add_argument("--manifest", required=True, type=Path,
                        help="absolute reviewed cleanup-plan JSON path")
    parser.add_argument("--backup", required=True, type=Path,
                        help="absolute, non-existing untouched backup target")
    parser.add_argument("--output", required=True, type=Path,
                        help="absolute, non-existing cleaned candidate target")
    parser.add_argument("--confirm", required=True,
                        help="exact plan_sha256 from the reviewed manifest")
    parser.add_argument("--execute", action="store_true",
                        help=(
                            "with a v2 plan, create backup and candidate; source "
                            "DB is still never edited"
                        ))
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        plan = load_plan(args.manifest)
        if args.confirm.strip().lower() != str(plan["plan_sha256"]).lower():
            raise CleanupPlanError("confirmation must exactly match plan_sha256")
        if args.execute:
            result = execute_cleanup_plan(
                args.db, plan, backup_target=args.backup,
                output_target=args.output, confirmation=args.confirm,
            )
        else:
            # Backup/output are mandatory even in dry-run so the reviewed
            # invocation cannot silently become an in-place operation later.
            validate_candidate_paths(args.db, args.backup, args.output)
            result = audit_cleanup_plan(args.db, plan)
            result.update({
                "mode": "dry_run",
                "backup_created": False,
                "candidate_created": False,
                "production_mutated": False,
            })
        # Deliberately report basenames only; operational paths stay private.
        result["database"] = args.db.name
        result["backup_target"] = args.backup.name
        result["output_target"] = args.output.name
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0
    except CleanupPlanError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False,
                         sort_keys=True), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
