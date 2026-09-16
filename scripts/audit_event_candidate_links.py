#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Immutable dry-run of event-to-industry/entity candidate linking."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from guanlan_v2.events.audit import audit_candidate_links  # noqa: E402
from guanlan_v2.events.cleanup import CleanupPlanError  # noqa: E402


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Preview source-supplied candidate links without DB writes.",
    )
    parser.add_argument("--db", required=True, type=Path,
                        help="explicit absolute events.db path")
    parser.add_argument("--asof", required=True,
                        help="explicit Hong Kong PIT timestamp/date")
    parser.add_argument("--source", default="newsradar")
    parser.add_argument("--sample-limit", type=int, default=500)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result = audit_candidate_links(
            args.db, asof=args.asof, source_id=args.source,
            sample_limit=args.sample_limit,
        )
        result["database"] = args.db.name
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0
    except (CleanupPlanError, ValueError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False,
                         sort_keys=True), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
