#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Explicit events.db P0 migration and legacy-company-event import command."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence

# Allow the reviewed invocation ``python scripts/migrate_events_p0.py`` from a
# source checkout without requiring an editable installation.
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from guanlan_v2.events import compat, store  # noqa: E402


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Migrate events.db to the audited P0 schema and optionally import "
            "events.json or pit_store/<date>/events.jsonl."
        )
    )
    parser.add_argument(
        "--db", type=Path, default=None,
        help="events.db path (default: GUANLAN_EVENTS_DB or repo var/events/events.db)",
    )
    parser.add_argument(
        "--source", action="append", type=Path, default=[],
        help="legacy events.json, events.jsonl, or pit_store root; repeatable",
    )
    fts = parser.add_mutually_exclusive_group()
    fts.add_argument("--rebuild-fts", action="store_true", help="drop and rebuild FTS5")
    fts.add_argument("--backfill-fts", action="store_true", help="fill missing FTS5 rows")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    conn = store.connect(args.db)
    try:
        store.init_db(conn)
        imports = [compat.import_legacy_events(conn, source) for source in args.source]
        fts_report = None
        if args.rebuild_fts:
            fts_report = store.rebuild_fts(conn)
        elif args.backfill_fts:
            fts_report = store.backfill_fts(conn)
        result = {
            "ok": True,
            "schema_version": int(conn.execute("PRAGMA user_version").fetchone()[0]),
            "database": store.db_path(args.db).name,
            "imports": imports,
            "fts": fts_report,
        }
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
