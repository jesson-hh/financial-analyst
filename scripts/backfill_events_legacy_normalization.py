#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Prepare a normalized events.db candidate without editing the source DB."""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from pathlib import Path
from typing import Sequence

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from guanlan_v2.events import store  # noqa: E402
from guanlan_v2.events.legacy_backfill import backfill_legacy_events  # noqa: E402


class CandidateError(ValueError):
    pass


def _paths(source: Path, output: Path | None, *, execute: bool) -> tuple[Path, Path | None]:
    if not source.is_absolute():
        raise CandidateError("source must be an absolute SQLite path")
    source = source.resolve(strict=True)
    if not source.is_file() or source.suffix.lower() not in {".db", ".sqlite", ".sqlite3"}:
        raise CandidateError("source must be an existing SQLite file")
    if not execute:
        sidecars = [Path(str(source) + suffix).name for suffix in ("-wal", "-shm")
                    if Path(str(source) + suffix).exists()]
        if sidecars:
            raise CandidateError(
                "immutable dry-run requires no WAL/SHM sidecars; use a maintenance snapshot"
            )
        return source, None
    if output is None or not output.is_absolute():
        raise CandidateError("--execute requires an absolute --output path")
    output = output.resolve(strict=False)
    if output == source:
        raise CandidateError("output must differ from source; in-place mutation is forbidden")
    if output.exists():
        raise CandidateError("output must not already exist")
    if not output.parent.is_dir():
        raise CandidateError("output parent must already exist")
    if output.suffix.lower() not in {".db", ".sqlite", ".sqlite3"}:
        raise CandidateError("output must be an explicit SQLite file")
    return source, output


def _immutable(source: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(source.as_uri() + "?mode=ro&immutable=1", uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    return conn


def _online_backup(source: Path, output: Path) -> None:
    """Take a coherent SQLite snapshot even when a short-lived WAL exists."""
    source_conn = sqlite3.connect(source.as_uri() + "?mode=ro", uri=True, timeout=30)
    output_conn = sqlite3.connect(output, timeout=30)
    try:
        source_conn.execute("PRAGMA busy_timeout=30000")
        source_conn.backup(output_conn, pages=4096, sleep=0.05)
    finally:
        output_conn.close()
        source_conn.close()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _candidate_metrics(conn: sqlite3.Connection) -> dict[str, int | str]:
    event_count = int(conn.execute("SELECT count(*) FROM events").fetchone()[0])
    fts_count = int(conn.execute(
        "SELECT count(DISTINCT event_id) FROM events_fts"
    ).fetchone()[0])
    relation_count = int(conn.execute("SELECT count(*) FROM event_relations").fetchone()[0])
    missing = int(conn.execute(
        "SELECT count(*) FROM events WHERE source_id IS NULL OR trim(source_id)='' "
        "OR source_asof IS NULL OR trim(source_asof)='' "
        "OR subject_kind IS NULL OR trim(subject_kind)='' "
        "OR issuer_ids IS NULL OR trim(issuer_ids)='' "
        "OR context_type IS NULL OR trim(context_type)='' "
        "OR context_ids IS NULL OR trim(context_ids)=''"
    ).fetchone()[0])
    return {
        "quick_check": str(conn.execute("PRAGMA quick_check").fetchone()[0]),
        "event_count": event_count,
        "fts_distinct_event_count": fts_count,
        "relation_count": relation_count,
        "remaining_incomplete_normalization": missing,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=(
        "Audit legacy event normalization, or create and transform a separate candidate. "
        "The source DB is never edited."
    ))
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--limit", type=int)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    candidate: Path | None = None
    try:
        source, candidate = _paths(args.source, args.output, execute=args.execute)
        if not args.execute:
            conn = _immutable(source)
            try:
                result = backfill_legacy_events(conn, apply=False, limit=args.limit)
            finally:
                conn.close()
            result.update({"source": source.name, "source_mutated": False})
        else:
            assert candidate is not None
            _online_backup(source, candidate)
            conn = sqlite3.connect(candidate)
            conn.row_factory = sqlite3.Row
            try:
                store.init_db(conn)
                result = backfill_legacy_events(conn, apply=True, limit=args.limit)
                metrics = _candidate_metrics(conn)
                if metrics["quick_check"] != "ok":
                    raise CandidateError(
                        f"candidate quick_check failed: {metrics['quick_check']}"
                    )
            finally:
                conn.close()
            result.update({
                "source": source.name, "candidate": candidate.name,
                "source_mutated": False, "candidate_sha256": _sha256(candidate),
                **metrics,
            })
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0
    except (CandidateError, OSError, sqlite3.Error, ValueError) as exc:
        # A failed output is a disposable candidate, never an authoritative DB.
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False,
                         sort_keys=True), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
