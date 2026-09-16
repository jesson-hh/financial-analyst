# -*- coding: utf-8 -*-
"""Fail-closed preparation of a cleaned ``events.db`` candidate.

The production database is never edited in place.  A reviewed v2 plan names
every event/run id and binds the complete selected rows, affected child rows,
and relevant delete-trigger definitions with SHA-256.  Execution first creates
an untouched backup, then produces a separate cleaned candidate.  An operator
must explicitly replace the live database in a later, independent maintenance
step.

This deliberately conservative shape makes test-pollution remediation
recoverable and prevents a stale plan from deleting rows after the database has
changed.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Sequence


PLAN_SCHEMA_V1 = "guanlan.events.cleanup-plan.v1"
PLAN_SCHEMA_V2 = "guanlan.events.cleanup-plan.v2"
# New plans must use v2.  V1 remains readable for immutable historical audits,
# but is deliberately not executable because it does not bind child rows or
# the append-only delete guards which must be removed temporarily in a copy.
PLAN_SCHEMA = PLAN_SCHEMA_V2
_SUPPORTED_PLAN_SCHEMAS = frozenset((PLAN_SCHEMA_V1, PLAN_SCHEMA_V2))
MAX_TARGET_IDS = 5_000
MAX_MANIFEST_BYTES = 1_000_000
_TARGETS = {
    "events": "event_id",
    "event_ingest_runs": "run_id",
}
_EVENT_CHILD_TARGETS = {
    "events_fts": "event_id",
    "event_idempotency": "event_id",
    "event_relations": "event_id",
    "legacy_imports": "event_id",
}
_EVENT_CHILD_TABLES = tuple(_EVENT_CHILD_TARGETS)
_DELETE_GUARD_TABLES = (*_EVENT_CHILD_TABLES, "events", "event_ingest_runs")
_MAX_DELETE_TRIGGERS = 100


class CleanupPlanError(ValueError):
    """A cleanup plan or database snapshot failed a safety check."""


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    )


def _jsonable(value: Any) -> Any:
    if isinstance(value, bytes):
        return {
            "bytes_length": len(value),
            "bytes_sha256": hashlib.sha256(value).hexdigest(),
        }
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


def plan_digest(plan: Mapping[str, Any]) -> str:
    """Return the confirmation digest, excluding its self-reference field."""
    payload = {str(key): value for key, value in plan.items()
               if str(key) != "plan_sha256"}
    return hashlib.sha256(_canonical_json(_jsonable(payload)).encode("utf-8")).hexdigest()


def rows_digest(rows: Iterable[Mapping[str, Any]], *, id_column: str) -> str:
    """Bind full selected rows, not only ids or mutable counts."""
    normalized = [_jsonable(dict(row)) for row in rows]
    # Child tables can legitimately contain more than one row per event_id.
    # Use the complete canonical row as a deterministic tie-breaker rather
    # than depending on SQLite's unspecified order for equal identifiers.
    normalized.sort(
        key=lambda row: (str(row.get(id_column) or ""), _canonical_json(row)),
    )
    return hashlib.sha256(_canonical_json(normalized).encode("utf-8")).hexdigest()


def _valid_sha256(value: Any) -> bool:
    candidate = str(value or "").lower()
    return (
        len(candidate) == 64
        and all(char in "0123456789abcdef" for char in candidate)
    )


def _normalize_trigger_sql(sql: str) -> str:
    """Canonicalize insignificant whitespace before fingerprinting DDL."""
    return " ".join(str(sql or "").split())


def _trigger_sql_digest(sql: str) -> str:
    return hashlib.sha256(_normalize_trigger_sql(sql).encode("utf-8")).hexdigest()


def _validate_path(path: Path, *, role: str, must_exist: bool) -> Path:
    if not isinstance(path, Path):
        path = Path(path)
    if not path.is_absolute():
        raise CleanupPlanError(f"{role} path must be absolute")
    resolved = path.resolve(strict=must_exist)
    if must_exist:
        if not resolved.is_file():
            raise CleanupPlanError(f"{role} must be an existing file")
    else:
        if resolved.exists():
            raise CleanupPlanError(f"{role} must not already exist")
        if not resolved.parent.is_dir():
            raise CleanupPlanError(f"{role} parent directory must already exist")
    if resolved.suffix.lower() not in {".db", ".sqlite", ".sqlite3"}:
        raise CleanupPlanError(f"{role} must be an explicit SQLite file")
    return resolved


def _wal_sidecars(path: Path) -> list[str]:
    found = []
    for suffix in ("-wal", "-shm"):
        candidate = Path(str(path) + suffix)
        if candidate.exists():
            found.append(candidate.name)
    return found


def connect_immutable(path: Path) -> sqlite3.Connection:
    """Open one stable main-file snapshot and reject a live WAL database."""
    source = _validate_path(path, role="database", must_exist=True)
    sidecars = _wal_sidecars(source)
    if sidecars:
        raise CleanupPlanError(
            "database has WAL/SHM sidecars; stop writers and checkpoint before audit"
        )
    conn = sqlite3.connect(
        source.as_uri() + "?mode=ro&immutable=1", uri=True, timeout=5,
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    return conn


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type IN ('table','view') AND name=?",
        (table,),
    ).fetchone() is not None


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in conn.execute(f'PRAGMA table_info("{table}")')}


def _selected_rows(
    conn: sqlite3.Connection, table: str, id_column: str, ids: Sequence[str],
) -> list[Dict[str, Any]]:
    rows: list[Dict[str, Any]] = []
    for offset in range(0, len(ids), 500):
        part = ids[offset:offset + 500]
        placeholders = ",".join("?" for _ in part)
        sql = (
            f'SELECT * FROM "{table}" '
            f'WHERE "{id_column}" IN ({placeholders})'
        )
        rows.extend(dict(row) for row in conn.execute(sql, tuple(part)).fetchall())
    rows.sort(key=lambda row: str(row.get(id_column) or ""))
    return rows


def _validate_v2_safety_bindings(value: Mapping[str, Any]) -> None:
    children = value.get("event_children")
    if not isinstance(children, Mapping) or set(children) != set(_EVENT_CHILD_TARGETS):
        raise CleanupPlanError(
            "v2 cleanup plan must bind every synthetic-event child table"
        )
    child_total = 0
    for table, id_column in _EVENT_CHILD_TARGETS.items():
        target = children.get(table)
        if not isinstance(target, Mapping):
            raise CleanupPlanError(f"invalid event child block: {table}")
        if target.get("id_column") != id_column:
            raise CleanupPlanError(f"invalid child id column for {table}")
        expected_count = target.get("expected_count")
        if type(expected_count) is not int or expected_count < 0:
            raise CleanupPlanError(
                f"{table} expected_count must be a non-negative integer"
            )
        if not _valid_sha256(target.get("rows_sha256")):
            raise CleanupPlanError(f"{table} rows_sha256 is invalid")
        child_total += expected_count
    if child_total > MAX_TARGET_IDS:
        raise CleanupPlanError("cleanup plan exceeds the child-row safety limit")

    delete_triggers = value.get("delete_triggers")
    if not isinstance(delete_triggers, Mapping):
        raise CleanupPlanError("v2 cleanup plan must bind delete-trigger definitions")
    expected_count = delete_triggers.get("expected_count")
    definitions = delete_triggers.get("definitions")
    if (
        type(expected_count) is not int
        or expected_count < 0
        or expected_count > _MAX_DELETE_TRIGGERS
        or not isinstance(definitions, list)
        or len(definitions) != expected_count
    ):
        raise CleanupPlanError("invalid delete-trigger expected_count/definitions")
    normalized: list[Dict[str, str]] = []
    seen: set[str] = set()
    for item in definitions:
        if not isinstance(item, Mapping) or set(item) != {
            "name", "table", "sql_sha256",
        }:
            raise CleanupPlanError("invalid delete-trigger definition block")
        name = str(item.get("name") or "")
        table = str(item.get("table") or "")
        sql_sha256 = str(item.get("sql_sha256") or "").lower()
        if not name or len(name) > 256 or name in seen:
            raise CleanupPlanError("delete-trigger names must be unique and non-empty")
        if table not in _DELETE_GUARD_TABLES:
            raise CleanupPlanError("delete-trigger table is outside cleanup scope")
        if not _valid_sha256(sql_sha256):
            raise CleanupPlanError("delete-trigger sql_sha256 is invalid")
        seen.add(name)
        normalized.append({
            "name": name, "table": table, "sql_sha256": sql_sha256,
        })
    normalized.sort(key=lambda item: item["name"])
    if definitions != normalized:
        raise CleanupPlanError("delete-trigger definitions must be sorted by name")
    definitions_sha256 = str(
        delete_triggers.get("definitions_sha256") or ""
    ).lower()
    if not _valid_sha256(definitions_sha256):
        raise CleanupPlanError("delete-trigger definitions_sha256 is invalid")
    if definitions_sha256 != rows_digest(normalized, id_column="name"):
        raise CleanupPlanError("delete-trigger definitions_sha256 mismatch")


def _validated_plan(plan: Mapping[str, Any]) -> Dict[str, Any]:
    if not isinstance(plan, Mapping):
        raise CleanupPlanError("cleanup plan must be a JSON object")
    value = dict(plan)
    schema = value.get("schema")
    if schema not in _SUPPORTED_PLAN_SCHEMAS:
        raise CleanupPlanError("unsupported cleanup plan schema")
    if not str(value.get("plan_id") or "").strip():
        raise CleanupPlanError("cleanup plan_id is required")
    supplied_digest = str(value.get("plan_sha256") or "").strip().lower()
    computed_digest = plan_digest(value)
    if supplied_digest != computed_digest:
        raise CleanupPlanError("cleanup plan digest mismatch")
    targets = value.get("targets")
    if not isinstance(targets, Mapping) or set(targets) != set(_TARGETS):
        raise CleanupPlanError("cleanup plan must bind events and event_ingest_runs")
    total = 0
    for table, id_column in _TARGETS.items():
        target = targets.get(table)
        if not isinstance(target, Mapping):
            raise CleanupPlanError(f"invalid target block: {table}")
        if target.get("id_column") != id_column:
            raise CleanupPlanError(f"invalid id column for {table}")
        ids = target.get("ids")
        if not isinstance(ids, list) or not ids:
            raise CleanupPlanError(f"{table} ids must be a non-empty list")
        if any(not isinstance(item, str) or not item or len(item) > 256 for item in ids):
            raise CleanupPlanError(f"{table} contains an invalid id")
        if len(set(ids)) != len(ids):
            raise CleanupPlanError(f"{table} contains duplicate ids")
        expected_count = target.get("expected_count")
        if type(expected_count) is not int or expected_count != len(ids):
            raise CleanupPlanError(f"{table} expected_count must equal id count")
        expected_hash = str(target.get("rows_sha256") or "").lower()
        if not _valid_sha256(expected_hash):
            raise CleanupPlanError(f"{table} rows_sha256 is invalid")
        total += len(ids)
    if total > MAX_TARGET_IDS:
        raise CleanupPlanError("cleanup plan exceeds the target safety limit")
    database = value.get("database")
    if not isinstance(database, Mapping) or type(database.get("user_version")) is not int:
        raise CleanupPlanError("cleanup plan must bind database.user_version")
    if schema == PLAN_SCHEMA_V2:
        _validate_v2_safety_bindings(value)
    return value


def load_plan(path: Path) -> Dict[str, Any]:
    manifest = Path(path)
    if not manifest.is_absolute():
        raise CleanupPlanError("manifest path must be absolute")
    try:
        manifest = manifest.resolve(strict=True)
        if not manifest.is_file() or manifest.stat().st_size > MAX_MANIFEST_BYTES:
            raise CleanupPlanError("cleanup manifest is missing or too large")
        raw = json.loads(manifest.read_text(encoding="utf-8"))
    except CleanupPlanError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise CleanupPlanError("cleanup manifest is unreadable or invalid JSON") from exc
    return _validated_plan(raw)


def validate_candidate_paths(
    database: Path, backup_target: Path, output_target: Path,
) -> tuple[Path, Path, Path]:
    """Validate the three explicit paths without creating or overwriting files."""
    source = _validate_path(database, role="database", must_exist=True)
    backup = _validate_path(backup_target, role="backup target", must_exist=False)
    output = _validate_path(output_target, role="output target", must_exist=False)
    if len({os.path.normcase(str(path)) for path in (source, backup, output)}) != 3:
        raise CleanupPlanError("database, backup, and output must be three distinct files")
    return source, backup, output


def _delete_guard_fingerprints(
    guards: Iterable[Mapping[str, str]],
) -> list[Dict[str, str]]:
    definitions = [{
        "name": str(guard["name"]),
        "table": str(guard["table"]),
        "sql_sha256": _trigger_sql_digest(str(guard["sql"])),
    } for guard in guards]
    definitions.sort(key=lambda item: item["name"])
    return definitions


def _audit_v2_safety_bindings(
    conn: sqlite3.Connection, plan: Mapping[str, Any],
) -> tuple[Dict[str, Any], Dict[str, Any]]:
    event_ids = list(plan["targets"]["events"]["ids"])
    children_report: Dict[str, Any] = {}
    for table, id_column in _EVENT_CHILD_TARGETS.items():
        if not _table_exists(conn, table) or id_column not in _table_columns(conn, table):
            raise CleanupPlanError(f"database is missing required child table: {table}")
        target = plan["event_children"][table]
        rows = _selected_rows(conn, table, id_column, event_ids)
        digest = rows_digest(rows, id_column=id_column)
        if len(rows) != int(target["expected_count"]):
            raise CleanupPlanError(
                f"{table} child-row count drifted from cleanup plan"
            )
        if digest != str(target["rows_sha256"]).lower():
            raise CleanupPlanError(
                f"{table} child-row hash drifted from cleanup plan"
            )
        children_report[table] = {
            "expected_count": int(target["expected_count"]),
            "matched_count": len(rows),
            "rows_sha256": digest,
        }

    guard_tables = [
        table for table in _DELETE_GUARD_TABLES if _table_exists(conn, table)
    ]
    guards = _delete_guards(conn, guard_tables)
    actual_definitions = _delete_guard_fingerprints(guards)
    expected = plan["delete_triggers"]
    actual_digest = rows_digest(actual_definitions, id_column="name")
    if len(actual_definitions) != int(expected["expected_count"]):
        raise CleanupPlanError(
            "delete-trigger definition count drifted from cleanup plan"
        )
    if actual_definitions != list(expected["definitions"]):
        raise CleanupPlanError(
            "delete-trigger definitions drifted from cleanup plan"
        )
    if actual_digest != str(expected["definitions_sha256"]).lower():
        raise CleanupPlanError(
            "delete-trigger definitions hash drifted from cleanup plan"
        )
    trigger_report = {
        "expected_count": int(expected["expected_count"]),
        "matched_count": len(actual_definitions),
        "definitions_sha256": actual_digest,
        "definitions": actual_definitions,
    }
    return children_report, trigger_report


def _audit_with_conn(conn: sqlite3.Connection, plan: Mapping[str, Any]) -> Dict[str, Any]:
    quick_check = str(conn.execute("PRAGMA quick_check").fetchone()[0])
    if quick_check != "ok":
        raise CleanupPlanError("database quick_check failed")
    user_version = int(conn.execute("PRAGMA user_version").fetchone()[0])
    if user_version != int(plan["database"]["user_version"]):
        raise CleanupPlanError("database user_version drifted from cleanup plan")

    report_targets: Dict[str, Any] = {}
    for table, id_column in _TARGETS.items():
        if not _table_exists(conn, table) or id_column not in _table_columns(conn, table):
            raise CleanupPlanError(f"database is missing required target table: {table}")
        target = plan["targets"][table]
        ids = list(target["ids"])
        rows = _selected_rows(conn, table, id_column, ids)
        found_ids = {str(row[id_column]) for row in rows}
        missing = sorted(set(ids) - found_ids)
        unexpected_count = len(rows) != int(target["expected_count"])
        digest = rows_digest(rows, id_column=id_column)
        if missing or unexpected_count:
            raise CleanupPlanError(f"{table} target ids/count drifted from cleanup plan")
        if digest != str(target["rows_sha256"]).lower():
            raise CleanupPlanError(f"{table} selected-row hash drifted from cleanup plan")
        report_targets[table] = {
            "expected_count": int(target["expected_count"]),
            "matched_count": len(rows),
            "rows_sha256": digest,
        }
    report = {
        "ok": True,
        "schema": plan["schema"],
        "plan_id": plan["plan_id"],
        "plan_sha256": plan["plan_sha256"],
        "database_user_version": user_version,
        "targets": report_targets,
        "execution_eligible": plan["schema"] == PLAN_SCHEMA_V2,
        "source_unchanged": True,
        "production_mutated": False,
    }
    if plan["schema"] == PLAN_SCHEMA_V2:
        children, triggers = _audit_v2_safety_bindings(conn, plan)
        report["event_children"] = children
        report["delete_triggers"] = triggers
    else:
        report["legacy_plan_limitation"] = (
            "v1 binds primary targets only and is immutable-audit-only; "
            "execution requires a reviewed v2 plan"
        )
    return report


def audit_cleanup_plan(database: Path, plan: Mapping[str, Any]) -> Dict[str, Any]:
    """Verify a plan through an immutable, query-only connection."""
    plan = _validated_plan(plan)
    source = _validate_path(database, role="database", must_exist=True)
    before = source.stat()
    conn = connect_immutable(source)
    try:
        report = _audit_with_conn(conn, plan)
    finally:
        conn.close()
    after = source.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise CleanupPlanError("database file changed during immutable audit")
    if _wal_sidecars(source):
        raise CleanupPlanError("database gained WAL/SHM sidecars during immutable audit")
    return report


def _copy_with_sha256(source: Path, destination: Path) -> str:
    digest = hashlib.sha256()
    created = False
    try:
        with source.open("rb") as src, destination.open("xb") as dst:
            created = True
            while True:
                chunk = src.read(1024 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
                dst.write(chunk)
            dst.flush()
            os.fsync(dst.fileno())
        shutil.copystat(source, destination)
    except Exception:
        if created:
            destination.unlink(missing_ok=True)
        raise
    return digest.hexdigest()


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _quoted(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _delete_guards(
    conn: sqlite3.Connection, tables: Sequence[str],
) -> list[Dict[str, str]]:
    if not tables:
        return []
    placeholders = ",".join("?" for _ in tables)
    rows = conn.execute(
        "SELECT name,tbl_name,sql FROM sqlite_master WHERE type='trigger' "
        f"AND tbl_name IN ({placeholders}) ORDER BY name",
        tuple(tables),
    ).fetchall()
    guards: list[Dict[str, str]] = []
    for name, table, sql in rows:
        normalized = " ".join(str(sql or "").upper().split())
        if " BEFORE DELETE ON " in f" {normalized} ":
            if not sql:
                raise CleanupPlanError(
                    f"delete-trigger definition is unavailable: {name}"
                )
            guards.append({
                "name": str(name), "table": str(table), "sql": str(sql),
            })
    return guards


def _delete_ids(
    conn: sqlite3.Connection, table: str, id_column: str, ids: Sequence[str],
) -> int:
    if not ids or not _table_exists(conn, table):
        return 0
    if id_column not in _table_columns(conn, table):
        raise CleanupPlanError(f"cleanup child table lacks {id_column}: {table}")
    deleted = 0
    for offset in range(0, len(ids), 500):
        part = ids[offset:offset + 500]
        placeholders = ",".join("?" for _ in part)
        cur = conn.execute(
            f"DELETE FROM {_quoted(table)} WHERE {_quoted(id_column)} "
            f"IN ({placeholders})",
            tuple(part),
        )
        deleted += max(0, int(cur.rowcount))
    return deleted


def _clean_candidate(candidate: Path, plan: Mapping[str, Any]) -> Dict[str, int]:
    conn = sqlite3.connect(str(candidate), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    event_ids = list(plan["targets"]["events"]["ids"])
    run_ids = list(plan["targets"]["event_ingest_runs"]["ids"])
    deleted: Dict[str, int] = {}
    try:
        conn.execute("BEGIN IMMEDIATE")
        _audit_with_conn(conn, plan)
        guard_tables = [
            table for table in _DELETE_GUARD_TABLES if _table_exists(conn, table)
        ]
        guards = _delete_guards(conn, guard_tables)
        for guard in guards:
            conn.execute(f"DROP TRIGGER {_quoted(guard['name'])}")
        for table in _EVENT_CHILD_TABLES:
            deleted[table] = _delete_ids(conn, table, "event_id", event_ids)
        deleted["events"] = _delete_ids(conn, "events", "event_id", event_ids)
        deleted["event_ingest_runs"] = _delete_ids(
            conn, "event_ingest_runs", "run_id", run_ids,
        )
        if deleted["events"] != len(event_ids):
            raise CleanupPlanError("candidate event deletion count mismatch")
        if deleted["event_ingest_runs"] != len(run_ids):
            raise CleanupPlanError("candidate run deletion count mismatch")
        for table in _EVENT_CHILD_TABLES:
            expected = int(plan["event_children"][table]["expected_count"])
            if deleted[table] != expected:
                raise CleanupPlanError(
                    f"candidate {table} deletion count mismatch"
                )
        for guard in guards:
            conn.execute(guard["sql"])
        remaining_events = _selected_rows(conn, "events", "event_id", event_ids)
        remaining_runs = _selected_rows(
            conn, "event_ingest_runs", "run_id", run_ids,
        )
        if remaining_events or remaining_runs:
            raise CleanupPlanError("target rows remain in cleaned candidate")
        for table, id_column in _EVENT_CHILD_TARGETS.items():
            if _selected_rows(conn, table, id_column, event_ids):
                raise CleanupPlanError(
                    f"target child rows remain in cleaned candidate: {table}"
                )
        restored_definitions = _delete_guard_fingerprints(
            _delete_guards(conn, guard_tables),
        )
        expected_triggers = plan["delete_triggers"]
        if (
            restored_definitions != list(expected_triggers["definitions"])
            or rows_digest(restored_definitions, id_column="name")
            != str(expected_triggers["definitions_sha256"]).lower()
        ):
            raise CleanupPlanError(
                "candidate delete-trigger definitions were not restored exactly"
            )
        foreign_errors = conn.execute("PRAGMA foreign_key_check").fetchall()
        if foreign_errors:
            raise CleanupPlanError("cleaned candidate failed foreign_key_check")
        conn.commit()
        if str(conn.execute("PRAGMA quick_check").fetchone()[0]) != "ok":
            raise CleanupPlanError("cleaned candidate failed quick_check")
        if str(conn.execute("PRAGMA journal_mode").fetchone()[0]).lower() == "wal":
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return deleted


def _remove_created_sqlite_artifact(path: Path) -> None:
    """Remove only one exact artifact created by this invocation and sidecars."""
    for candidate in (path, Path(str(path) + "-wal"), Path(str(path) + "-shm")):
        try:
            candidate.unlink(missing_ok=True)
        except OSError:
            pass


def execute_cleanup_plan(
    database: Path,
    plan: Mapping[str, Any],
    *,
    backup_target: Path,
    output_target: Path,
    confirmation: str,
) -> Dict[str, Any]:
    """Create an untouched backup and separate cleaned candidate.

    This function never replaces, renames, unlinks, or writes ``database``.
    Existing backup/output paths are rejected rather than overwritten.
    """
    plan = _validated_plan(plan)
    if plan["schema"] != PLAN_SCHEMA_V2:
        raise CleanupPlanError(
            "cleanup plan v1 is immutable-audit-only; execution requires v2 "
            "child-row and delete-trigger safety bindings"
        )
    if str(confirmation or "").strip().lower() != str(plan["plan_sha256"]).lower():
        raise CleanupPlanError("confirmation must exactly match plan_sha256")
    source, backup, output = validate_candidate_paths(
        database, backup_target, output_target,
    )

    before = source.stat()
    audit = audit_cleanup_plan(source, plan)
    created: list[Path] = []
    try:
        source_digest = _copy_with_sha256(source, backup)
        created.append(backup)
        backup_digest = _copy_with_sha256(backup, output)
        created.append(output)
        after_copy = source.stat()
        if ((before.st_size, before.st_mtime_ns)
                != (after_copy.st_size, after_copy.st_mtime_ns)):
            raise CleanupPlanError("database file changed while backup was created")
        if _wal_sidecars(source):
            raise CleanupPlanError("database gained WAL/SHM sidecars while backup was created")
        if source_digest != backup_digest:
            raise CleanupPlanError("backup/output copy digest mismatch")
        deleted = _clean_candidate(output, plan)
        candidate_digest = _file_sha256(output)
        audit.update({
            "mode": "prepared_clean_candidate",
            "backup_created": True,
            "candidate_created": True,
            "source_sha256": source_digest,
            "backup_sha256": backup_digest,
            "candidate_sha256": candidate_digest,
            "deleted_from_candidate": deleted,
            "production_mutated": False,
            "operator_action_required": (
                "review candidate, stop writers, and perform a separate approved swap"
            ),
        })
        return audit
    except Exception:
        # Only artifacts created by this invocation are removed.  The source is
        # never a cleanup target and existing files were rejected above.
        for path in reversed(created):
            _remove_created_sqlite_artifact(path)
        raise


__all__ = [
    "CleanupPlanError", "MAX_MANIFEST_BYTES", "MAX_TARGET_IDS", "PLAN_SCHEMA",
    "PLAN_SCHEMA_V1", "PLAN_SCHEMA_V2",
    "audit_cleanup_plan", "connect_immutable", "execute_cleanup_plan",
    "load_plan", "plan_digest", "rows_digest", "validate_candidate_paths",
]
