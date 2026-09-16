# -*- coding: utf-8 -*-
"""SQLite event store, versioned migrations, PIT reads, and FTS maintenance.

``event_time`` is the time asserted by a source.  ``available_at`` is the first
time the local system can prove the row was available and is the sole PIT
visibility clock.  Migrations are additive and advance ``PRAGMA user_version``
transactionally.  FTS5 is optional: its absence never disables the base table.
"""
from __future__ import annotations

import functools
import hashlib
import json
import math
import os
import re
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime, time
from pathlib import Path, PureWindowsPath
from typing import Any, Dict, List, Mapping, Optional, Sequence, Union
from zoneinfo import ZoneInfo

_ENV_DB_PATH = "GUANLAN_EVENTS_DB"
_REPO_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_DB_PATH = _REPO_ROOT / "var" / "events" / "events.db"

HONG_KONG = ZoneInfo("Asia/Hong_Kong")
LATEST_SCHEMA_VERSION = 3
TIME_CLASSIFICATIONS = frozenset({"observed", "scheduled", "time_conflict"})
RELATION_TYPES = frozenset({"entity", "industry"})
RELATION_STATUSES = frozenset(
    {"candidate", "source_asserted", "confirmed", "rejected"}
)
REVIEW_RELATION_STATUSES = frozenset({"confirmed", "rejected"})
PIT_PREDICATE = (
    "available_at <= :asof "
    "AND (invalidated_at IS NULL OR invalidated_at > :asof)"
)

_BASE_COLUMNS = (
    "event_id", "event_type", "tickers", "title", "summary", "source",
    "source_ref", "event_time", "available_at", "invalidated_at", "simhash",
    "line_id", "ingested_from",
)
_P0_COLUMNS = (
    "time_classification", "classification_reason", "payload_json",
    "provenance_json",
)
_NORMALIZED_COLUMNS = (
    "idempotency_key", "normalization_version", "source_id", "source_asof",
    "subject_kind", "issuer_ids", "context_type", "context_ids",
)
_COLUMNS = _BASE_COLUMNS + _P0_COLUMNS + _NORMALIZED_COLUMNS

_CREATE_EVENTS = """
CREATE TABLE IF NOT EXISTS events (
    event_id              TEXT PRIMARY KEY,
    event_type            TEXT NOT NULL,
    tickers               TEXT,
    title                 TEXT,
    summary               TEXT,
    source                TEXT,
    source_ref            TEXT,
    event_time            TEXT,
    available_at          TEXT NOT NULL,
    invalidated_at        TEXT,
    simhash               TEXT,
    line_id               TEXT,
    ingested_from         TEXT,
    time_classification   TEXT,
    classification_reason TEXT,
    payload_json          TEXT,
    provenance_json       TEXT,
    idempotency_key       TEXT,
    normalization_version TEXT,
    source_id             TEXT,
    source_asof           TEXT,
    subject_kind          TEXT,
    issuer_ids            TEXT,
    context_type          TEXT,
    context_ids           TEXT
)
"""

_CREATE_IDEMPOTENCY = """
CREATE TABLE IF NOT EXISTS event_idempotency (
    idempotency_key       TEXT PRIMARY KEY,
    event_id              TEXT NOT NULL UNIQUE,
    normalization_version TEXT NOT NULL,
    created_at            TEXT NOT NULL,
    FOREIGN KEY (event_id) REFERENCES events(event_id)
)
"""

_CREATE_INGEST_RUNS = """
CREATE TABLE IF NOT EXISTS event_ingest_runs (
    run_id            TEXT PRIMARY KEY,
    source_id         TEXT NOT NULL,
    requested_asof    TEXT,
    observed_at       TEXT NOT NULL,
    status            TEXT NOT NULL CHECK (
        status IN ('ok','partial','empty','failed','stale')
    ),
    fetched_count     INTEGER NOT NULL DEFAULT 0,
    inserted_count    INTEGER NOT NULL DEFAULT 0,
    error_kind        TEXT,
    note              TEXT,
    metadata_json     TEXT
)
"""

_CREATE_NORMALIZED_VIEW = """
CREATE VIEW IF NOT EXISTS normalized_events AS
SELECT
    event_id,
    COALESCE(idempotency_key, 'legacy-event-id:' || event_id) AS idempotency_key,
    COALESCE(normalization_version, 'legacy-compat-v1') AS normalization_version,
    event_type,
    COALESCE(source_id, NULLIF(ingested_from,''), NULLIF(source,''), 'legacy') AS source_id,
    source,
    source_ref,
    event_time,
    COALESCE(source_asof, available_at) AS source_asof,
    available_at AS pit_available_at,
    available_at,
    invalidated_at,
    COALESCE(
        subject_kind,
        CASE WHEN tickers IS NOT NULL AND tickers NOT IN ('','[]')
             THEN 'issuer' ELSE 'context' END
    ) AS subject_kind,
    COALESCE(issuer_ids, tickers, '[]') AS issuer_ids,
    COALESCE(context_type, 'unclassified') AS context_type,
    COALESCE(context_ids, '[]') AS context_ids,
    tickers,
    title,
    summary,
    simhash,
    line_id,
    ingested_from,
    time_classification,
    classification_reason,
    payload_json,
    provenance_json
FROM events
"""

_CREATE_RELATIONS = """
CREATE TABLE IF NOT EXISTS event_relations (
    relation_id       TEXT NOT NULL PRIMARY KEY,
    event_id          TEXT NOT NULL,
    relation_type     TEXT NOT NULL CHECK (relation_type IN ('entity', 'industry')),
    target_id         TEXT,
    target_name       TEXT,
    relation_status   TEXT NOT NULL CHECK (
        relation_status IN ('candidate', 'source_asserted', 'confirmed', 'rejected')
    ),
    confidence        REAL CHECK (confidence IS NULL OR (confidence >= 0 AND confidence <= 1)),
    evidence_json     TEXT,
    relation_method   TEXT,
    created_at        TEXT NOT NULL,
    reviewed_by       TEXT,
    reviewed_at       TEXT,
    FOREIGN KEY (event_id) REFERENCES events(event_id)
)
"""

_CREATE_LEGACY_IMPORTS = """
CREATE TABLE IF NOT EXISTS legacy_imports (
    import_id        TEXT PRIMARY KEY,
    record_digest    TEXT NOT NULL UNIQUE,
    event_id         TEXT NOT NULL,
    source_kind      TEXT NOT NULL,
    source_locator   TEXT,
    partition_date   TEXT,
    imported_at      TEXT NOT NULL,
    provenance_json  TEXT,
    FOREIGN KEY (event_id) REFERENCES events(event_id)
)
"""

_CREATE_FTS = (
    "CREATE VIRTUAL TABLE IF NOT EXISTS events_fts "
    "USING fts5(tokens, event_id UNINDEXED, tokenize='unicode61')"
)

_INDEX_DDL = (
    "CREATE INDEX IF NOT EXISTS idx_events_available_at ON events(available_at)",
    "CREATE INDEX IF NOT EXISTS idx_events_type_avail ON events(event_type, available_at)",
    "CREATE INDEX IF NOT EXISTS idx_events_line ON events(line_id)",
    "CREATE INDEX IF NOT EXISTS idx_event_relations_event ON event_relations(event_id)",
    "CREATE INDEX IF NOT EXISTS idx_event_relations_target "
    "ON event_relations(relation_type, target_id, relation_status)",
    "CREATE INDEX IF NOT EXISTS idx_legacy_imports_event ON legacy_imports(event_id)",
)

_RELATION_TRIGGER_DDL = (
    """
    CREATE TRIGGER IF NOT EXISTS trg_event_relations_no_update
    BEFORE UPDATE ON event_relations
    BEGIN
        SELECT RAISE(ABORT, 'event_relations is append-only');
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS trg_event_relations_no_delete
    BEFORE DELETE ON event_relations
    BEGIN
        SELECT RAISE(ABORT, 'event_relations is append-only');
    END
    """,
)

_NORMALIZATION_TRIGGER_DDL = (
    """
    CREATE TRIGGER IF NOT EXISTS trg_event_idempotency_no_update
    BEFORE UPDATE ON event_idempotency
    BEGIN
        SELECT RAISE(ABORT, 'event_idempotency is append-only');
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS trg_event_idempotency_no_delete
    BEFORE DELETE ON event_idempotency
    BEGIN
        SELECT RAISE(ABORT, 'event_idempotency is append-only');
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS trg_event_ingest_runs_no_update
    BEFORE UPDATE ON event_ingest_runs
    BEGIN
        SELECT RAISE(ABORT, 'event_ingest_runs is append-only');
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS trg_event_ingest_runs_no_delete
    BEFORE DELETE ON event_ingest_runs
    BEGIN
        SELECT RAISE(ABORT, 'event_ingest_runs is append-only');
    END
    """,
)

_SCHEDULE_CUES = (
    "calendar", "schedule", "scheduled", "upcoming", "meeting", "earnings_date",
    "dividend", "xdxr", "unlock", "预约", "日程", "股东大会", "业绩说明会",
    "披露日", "除权", "除息", "解禁", "召开", "拟于", "计划于",
)


def db_path(path: Optional[Union[str, Path]] = None) -> Path:
    """Resolve path by explicit argument, environment, then repo default."""
    if path is not None:
        return Path(path)
    env = os.environ.get(_ENV_DB_PATH)
    return Path(env) if env else _DEFAULT_DB_PATH


def connect(path: Optional[Union[str, Path]] = None) -> sqlite3.Connection:
    """Open a writable connection and create only its parent directory."""
    p = db_path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(p))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def connect_readonly(path: Optional[Union[str, Path]] = None) -> sqlite3.Connection:
    """Open an existing DB in ``mode=ro`` + ``query_only`` without creating it."""
    p = db_path(path).absolute()
    conn = sqlite3.connect(p.as_uri() + "?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA query_only=ON")
    return conn


def _parse_datetime(value: Any, *, date_end: bool = False) -> Optional[datetime]:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, date):
        dt = datetime.combine(value, time.max if date_end else time.min)
    else:
        raw = str(value).strip()
        if not raw:
            return None
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw):
            try:
                parsed_date = date.fromisoformat(raw)
            except ValueError:
                return None
            return datetime.combine(parsed_date, time.max if date_end else time.min)
        if raw.endswith(("Z", "z")):
            raw = raw[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(raw)
        except (TypeError, ValueError):
            return None
    if dt.tzinfo is not None:
        dt = dt.astimezone(HONG_KONG).replace(tzinfo=None)
    return dt


def normalize_timestamp(value: Any, *, date_end: bool = False) -> str:
    """Return Hong Kong-local naive ISO; offsets/Z are converted first."""
    dt = _parse_datetime(value, date_end=date_end)
    if dt is None:
        raise ValueError(f"invalid timestamp: {value!r}")
    return dt.isoformat(timespec="microseconds" if dt.microsecond else "seconds")


def normalize_asof(asof: Any) -> str:
    """Normalize mandatory PIT as-of; a date means Hong Kong local day-end."""
    if asof is None or asof == "":
        raise ValueError("events search requires an explicit asof(PIT)")
    raw = str(asof).strip() if not isinstance(asof, (date, datetime)) else asof
    date_only = isinstance(raw, date) and not isinstance(raw, datetime)
    if isinstance(raw, str):
        date_only = bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw))
    return normalize_timestamp(raw, date_end=date_only)


def _json_text(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError):
            return value
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
    )


def _is_local_absolute(value: Any) -> bool:
    return bool(
        isinstance(value, str) and value
        and (Path(value).is_absolute() or PureWindowsPath(value).is_absolute())
    )


def sanitize_provenance(value: Any) -> Any:
    """Strip local absolute paths from a logical provenance document."""
    if isinstance(value, Mapping):
        out: Dict[str, Any] = {}
        for key, item in value.items():
            if _is_local_absolute(item):
                out[f"{key}_basename"] = PureWindowsPath(str(item)).name
            else:
                out[str(key)] = sanitize_provenance(item)
        return out
    if isinstance(value, (list, tuple)):
        return [sanitize_provenance(item) for item in value]
    if _is_local_absolute(value):
        return PureWindowsPath(str(value)).name
    return value


def classify_event_time(
    event_time: Any,
    available_at: Any,
    *,
    event_type: Any = None,
    title: Any = None,
    payload: Any = None,
    migration_version: int = 2,
) -> tuple[str, str]:
    """Classify source time while preserving it byte-for-byte in storage."""
    prefix = f"events-p0-v{migration_version}"
    event_dt = _parse_datetime(event_time)
    available_dt = _parse_datetime(available_at)
    if event_dt is None:
        return "observed", f"{prefix}: event_time_missing_or_unparseable"
    if available_dt is None:
        return "time_conflict", f"{prefix}: available_at_unparseable"
    if event_dt <= available_dt:
        return "observed", f"{prefix}: event_time_not_after_available_at"
    try:
        payload_text = _json_text(payload) or ""
    except (TypeError, ValueError):
        payload_text = str(payload or "")
    haystack = " ".join(
        (str(event_type or ""), str(title or ""), payload_text)
    ).lower()
    cue = next((cue for cue in _SCHEDULE_CUES if cue.lower() in haystack), None)
    if cue:
        return "scheduled", f"{prefix}: future_event_time_with_schedule_cue={cue}"
    return "time_conflict", f"{prefix}: future_event_time_without_schedule_evidence"


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})")}


def _fts_exists(conn: sqlite3.Connection) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='events_fts'"
    ).fetchone() is not None


def _ensure_fts(conn: sqlite3.Connection) -> tuple[bool, bool]:
    existed = _fts_exists(conn)
    try:
        conn.execute(_CREATE_FTS)
    except sqlite3.OperationalError:
        return False, False
    return True, not existed


def _add_p0_columns(conn: sqlite3.Connection) -> None:
    columns = _table_columns(conn, "events")
    for name in _P0_COLUMNS:
        if name not in columns:
            conn.execute(f"ALTER TABLE events ADD COLUMN {name} TEXT")


def _add_normalized_columns(conn: sqlite3.Connection) -> None:
    """Add v3 columns without rewriting/backfilling a large production table."""
    columns = _table_columns(conn, "events")
    for name in _NORMALIZED_COLUMNS:
        if name not in columns:
            conn.execute(f"ALTER TABLE events ADD COLUMN {name} TEXT")


def _migrate_relation_table(conn: sqlite3.Connection) -> None:
    """Create the v2 relation journal or transactionally rebuild an old shape."""
    exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='event_relations'"
    ).fetchone()
    if exists is None:
        conn.execute(_CREATE_RELATIONS)
        return

    info = {str(row[1]): row for row in conn.execute("PRAGMA table_info(event_relations)")}
    required = {
        "relation_id", "event_id", "relation_type", "target_id", "target_name",
        "relation_status", "confidence", "evidence_json", "relation_method",
        "created_at", "reviewed_by", "reviewed_at",
    }
    structurally_v2 = (
        required <= set(info)
        and int(info["relation_id"][5]) == 1
        and int(info["relation_id"][3]) == 1
        and int(info["event_id"][3]) == 1
        and int(info["relation_type"][3]) == 1
        and int(info["relation_status"][3]) == 1
        and int(info["created_at"][3]) == 1
    )
    if structurally_v2:
        return

    legacy_name = "event_relations_legacy_p0"
    if conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (legacy_name,)
    ).fetchone() is not None:
        raise RuntimeError(f"cannot migrate event_relations: {legacy_name} already exists")
    conn.execute(f"ALTER TABLE event_relations RENAME TO {legacy_name}")
    conn.execute(_CREATE_RELATIONS)

    event_available = {
        str(row["event_id"]): str(row["available_at"])
        for row in conn.execute("SELECT event_id,available_at FROM events")
    }
    legacy_rows = conn.execute(
        f"SELECT rowid AS _legacy_rowid,* FROM {legacy_name} ORDER BY rowid"
    ).fetchall()
    columns = (
        "relation_id", "event_id", "relation_type", "target_id", "target_name",
        "relation_status", "confidence", "evidence_json", "relation_method",
        "created_at", "reviewed_by", "reviewed_at",
    )
    for legacy_row in legacy_rows:
        item = dict(legacy_row)
        event_id = str(item.get("event_id") or "").strip()
        status = str(item.get("relation_status") or "").strip()
        reviewed_at = item.get("reviewed_at")
        if reviewed_at not in (None, ""):
            reviewed_at = _relation_timestamp(reviewed_at, field="reviewed_at")
        created_raw = item.get("created_at")
        if created_raw in (None, ""):
            created_raw = reviewed_at or event_available.get(event_id)
        created_at = _relation_timestamp(created_raw, field="created_at")
        if status in REVIEW_RELATION_STATUSES:
            if reviewed_at in (None, ""):
                reviewed_at = created_at
            if reviewed_at < created_at:
                raise ValueError(
                    "legacy event relation reviewed_at cannot precede created_at"
                )

        relation_id = str(item.get("relation_id") or "").strip()
        if not relation_id:
            identity = _json_text({
                key: item.get(key) for key in item if key != "_legacy_rowid"
            }) or "{}"
            relation_id = "legacy-" + hashlib.sha256(
                f"{item['_legacy_rowid']}\x1f{identity}".encode("utf-8")
            ).hexdigest()
        values = {
            "relation_id": relation_id,
            "event_id": event_id,
            "relation_type": item.get("relation_type"),
            "target_id": item.get("target_id"),
            "target_name": item.get("target_name"),
            "relation_status": status,
            "confidence": item.get("confidence"),
            "evidence_json": item.get("evidence_json"),
            "relation_method": item.get("relation_method"),
            "created_at": created_at,
            "reviewed_by": item.get("reviewed_by"),
            "reviewed_at": reviewed_at,
        }
        conn.execute(
            "INSERT INTO event_relations (" + ",".join(columns) + ") VALUES ("
            + ",".join("?" for _ in columns) + ")",
            tuple(values[column] for column in columns),
        )
    conn.execute(f"DROP TABLE {legacy_name}")


def _backfill_time_classification(conn: sqlite3.Connection) -> int:
    rows = conn.execute(
        "SELECT event_id,event_type,title,event_time,available_at,payload_json "
        "FROM events WHERE time_classification IS NULL "
        "OR time_classification NOT IN ('observed','scheduled','time_conflict') "
        "OR classification_reason IS NULL"
    ).fetchall()
    updates = []
    for row in rows:
        payload: Any = row["payload_json"]
        if payload:
            try:
                payload = json.loads(payload)
            except (TypeError, ValueError):
                pass
        classification, reason = classify_event_time(
            row["event_time"], row["available_at"], event_type=row["event_type"],
            title=row["title"], payload=payload,
        )
        updates.append((classification, reason, row["event_id"]))
    if updates:
        conn.executemany(
            "UPDATE events SET time_classification=?,classification_reason=? "
            "WHERE event_id=?", updates,
        )
    return len(updates)


@contextmanager
def _write_transaction(conn: sqlite3.Connection):
    """Explicit transaction/savepoint that also encloses pure SQLite DDL.

    ``sqlite3.Connection.__enter__`` does not necessarily issue ``BEGIN`` for a
    DDL-only sequence.  An explicit boundary is required for a real rollback of
    partially applied schema migrations.
    """
    nested = conn.in_transaction
    savepoint = "events_p0_write"
    if nested:
        conn.execute(f"SAVEPOINT {savepoint}")
    else:
        conn.execute("BEGIN IMMEDIATE")
    try:
        yield
    except BaseException:
        if nested:
            conn.execute(f"ROLLBACK TO {savepoint}")
            conn.execute(f"RELEASE {savepoint}")
        else:
            conn.rollback()
        raise
    else:
        if nested:
            conn.execute(f"RELEASE {savepoint}")
        else:
            conn.commit()


def init_db(conn: sqlite3.Connection) -> None:
    """Transactionally migrate to v3 without scanning a healthy v2 event table.

    The v3 migration is additive: eight nullable columns, two initially-empty
    audit tables, and a compatibility view.  Existing rows are normalized by
    the view at read time; a 372MB production DB is never mass-updated merely
    because the server restarted.
    """
    current = int(conn.execute("PRAGMA user_version").fetchone()[0])
    if current > LATEST_SCHEMA_VERSION:
        raise RuntimeError(
            f"events.db schema {current} is newer than supported {LATEST_SCHEMA_VERSION}"
        )
    conn.execute("PRAGMA foreign_keys=ON")
    with _write_transaction(conn):
        conn.execute(_CREATE_EVENTS)
        _add_p0_columns(conn)
        _add_normalized_columns(conn)
        _migrate_relation_table(conn)
        conn.execute(_CREATE_LEGACY_IMPORTS)
        conn.execute(_CREATE_IDEMPOTENCY)
        conn.execute(_CREATE_INGEST_RUNS)
        conn.execute(_CREATE_NORMALIZED_VIEW)
        for ddl in _INDEX_DDL:
            conn.execute(ddl)
        for ddl in _RELATION_TRIGGER_DDL:
            conn.execute(ddl)
        for ddl in _NORMALIZATION_TRIGGER_DDL:
            conn.execute(ddl)
        # v2 already proved this backfill.  Re-running the unindexed predicate
        # on every 30-minute tick turns a cheap init into a full-table scan.
        if current < 2:
            _backfill_time_classification(conn)
        fts_available, fts_created = _ensure_fts(conn)
        if fts_available and (current < 2 or fts_created):
            # Internal helper deliberately does not enter ``with conn``: the
            # schema, classification backfill, FTS repair, and user_version
            # advance remain one atomic migration transaction.
            _backfill_fts_in_transaction(conn)
        conn.execute(f"PRAGMA user_version={LATEST_SCHEMA_VERSION}")


@functools.lru_cache(maxsize=1)
def _memory_jieba_tokenizer():
    """Build jieba's prefix dictionary in memory without its temp cache write."""
    import jieba

    tokenizer = jieba.Tokenizer()
    # A regular file is deliberately not a writable cache directory.  More
    # importantly, populate the prefix dictionary directly and mark it ready so
    # ``Tokenizer.initialize`` (which calls tempfile.mkstemp) is never entered.
    tokenizer.tmp_dir = str(Path(__file__).resolve())
    tokenizer.FREQ, tokenizer.total = tokenizer.gen_pfdict(tokenizer.get_dict_file())
    tokenizer.initialized = True
    return tokenizer


def _jieba_tokens(text: str) -> str:
    tokenizer = _memory_jieba_tokenizer()
    return " ".join(word for word in tokenizer.cut(text or "") if word.strip())


def _fts_index(
    conn: sqlite3.Connection,
    event_id: str,
    title: Optional[str],
    summary: Optional[str],
) -> bool:
    text = ((title or "") + " " + (summary or "")).strip()
    try:
        tokens = _jieba_tokens(text)
        conn.execute(
            "INSERT INTO events_fts(tokens,event_id) VALUES (?,?)", (tokens, event_id)
        )
    except Exception:  # optional acceleration must never roll back the base row
        return False
    return True


def _backfill_fts_in_transaction(conn: sqlite3.Connection) -> Dict[str, Any]:
    """Backfill helper that never commits or opens a nested context manager."""
    if not _fts_exists(conn):
        available, _ = _ensure_fts(conn)
        if not available:
            return {"fts": "unavailable", "indexed": 0, "already_indexed": 0}
    try:
        event_count = int(conn.execute("SELECT count(*) FROM events").fetchone()[0])
        fts_count, fts_distinct = conn.execute(
            "SELECT count(*),count(DISTINCT event_id) FROM events_fts"
        ).fetchone()
        missing = conn.execute(
            "SELECT event_id FROM events EXCEPT "
            "SELECT event_id FROM events_fts LIMIT 1"
        ).fetchone()
        if event_count == int(fts_distinct) == int(fts_count) and missing is None:
            return {"fts": "ok", "indexed": 0,
                    "already_indexed": int(fts_distinct)}
        rows = conn.execute("SELECT event_id,title,summary FROM events").fetchall()
        base_ids = {str(row["event_id"]) for row in rows}
        fts_rows = conn.execute("SELECT rowid,event_id FROM events_fts").fetchall()
    except sqlite3.OperationalError as exc:
        return {"fts": "unavailable", "indexed": 0, "already_indexed": 0,
                "reason": str(exc)}

    # A raw row-count can look healthy when one event is duplicated and another
    # is missing.  Keep one row per live base event, remove stale/orphan rows,
    # then fill the genuinely missing ids.  This also prevents duplicate search
    # hits without forcing a full jieba rebuild of a large production database.
    existing: set[str] = set()
    delete_rowids: list[int] = []
    for fts_row in fts_rows:
        event_id = str(fts_row["event_id"])
        if event_id not in base_ids or event_id in existing:
            delete_rowids.append(int(fts_row["rowid"]))
        else:
            existing.add(event_id)
    if delete_rowids:
        conn.executemany(
            "DELETE FROM events_fts WHERE rowid=?",
            ((rowid,) for rowid in delete_rowids),
        )
    indexed = 0
    for row in rows:
        event_id = str(row["event_id"])
        if event_id in existing:
            continue
        if not _fts_index(conn, event_id, row["title"], row["summary"]):
            return {"fts": "unavailable", "indexed": indexed,
                    "already_indexed": len(existing)}
        indexed += 1
    return {"fts": "ok", "indexed": indexed, "already_indexed": len(existing)}


def backfill_fts(conn: sqlite3.Connection) -> Dict[str, Any]:
    """Repeatably add rows missing from optional FTS, with honest status."""
    with _write_transaction(conn):
        return _backfill_fts_in_transaction(conn)


def rebuild_fts(conn: sqlite3.Connection) -> Dict[str, Any]:
    """Drop and rebuild optional FTS; safe to run repeatedly."""
    with _write_transaction(conn):
        try:
            conn.execute("DROP TABLE IF EXISTS events_fts")
            conn.execute(_CREATE_FTS)
        except sqlite3.OperationalError as exc:
            return {"fts": "unavailable", "indexed": 0, "reason": str(exc)}
        return _backfill_fts_in_transaction(conn)


def _encode_tickers(tickers: Any) -> Optional[str]:
    if tickers is None:
        return None
    if isinstance(tickers, (list, tuple, set)):
        return json.dumps([str(item) for item in tickers], ensure_ascii=False)
    return str(tickers)


def _encode_string_list(values: Any) -> str:
    if values is None:
        return "[]"
    if isinstance(values, str):
        try:
            parsed = json.loads(values)
        except (TypeError, ValueError):
            parsed = [values] if values else []
        values = parsed if isinstance(parsed, list) else [values]
    if not isinstance(values, (list, tuple, set)):
        values = [values]
    return json.dumps(
        sorted({str(value).strip() for value in values if str(value).strip()}),
        ensure_ascii=False,
    )


def insert_event(conn: sqlite3.Connection, row: Dict[str, Any]) -> bool:
    """Insert one authoritative event idempotently; caller owns transaction."""
    available_at = normalize_timestamp(row["available_at"])
    payload_value = row.get("payload_json", row.get("payload"))
    provenance_value = row.get("provenance_json", row.get("provenance"))
    classification = row.get("time_classification")
    reason = row.get("classification_reason")
    if classification not in TIME_CLASSIFICATIONS or not reason:
        classification, reason = classify_event_time(
            row.get("event_time"), available_at, event_type=row.get("event_type"),
            title=row.get("title"), payload=payload_value,
        )
    invalidated_at = row.get("invalidated_at")
    if invalidated_at:
        invalidated_at = normalize_timestamp(invalidated_at)
    encoded_tickers = _encode_tickers(row.get("tickers"))
    issuer_ids = row.get("issuer_ids", row.get("tickers"))
    encoded_issuers = _encode_string_list(issuer_ids)
    subject_kind = str(row.get("subject_kind") or "").strip().lower()
    if not subject_kind:
        subject_kind = "issuer" if encoded_issuers != "[]" else "context"
    if subject_kind not in {"issuer", "context"}:
        raise ValueError("subject_kind must be issuer or context")
    if subject_kind == "issuer" and encoded_issuers == "[]":
        raise ValueError("issuer event requires issuer_ids")
    source_id = str(
        row.get("source_id") or row.get("ingested_from") or row.get("source") or "legacy"
    ).strip().lower()
    source_asof = normalize_timestamp(row.get("source_asof") or available_at)
    idempotency_key = str(
        row.get("idempotency_key") or f"legacy-event-id:{row['event_id']}"
    ).strip()
    normalization_version = str(
        row.get("normalization_version") or "legacy-compat-v1"
    ).strip()

    existing_key = conn.execute(
        "SELECT event_id FROM event_idempotency WHERE idempotency_key=?",
        (idempotency_key,),
    ).fetchone()
    if existing_key is not None and str(existing_key["event_id"]) != str(row["event_id"]):
        raise ValueError("idempotency_key already belongs to a different event_id")

    values = {
        "event_id": row["event_id"],
        "event_type": row["event_type"],
        "tickers": encoded_tickers,
        "title": row.get("title"),
        "summary": row.get("summary"),
        "source": row.get("source"),
        "source_ref": row.get("source_ref"),
        "event_time": row.get("event_time"),
        "available_at": available_at,
        "invalidated_at": invalidated_at,
        "simhash": None if row.get("simhash") is None else str(row["simhash"]),
        "line_id": row.get("line_id") or row["event_id"],
        "ingested_from": row.get("ingested_from"),
        "time_classification": classification,
        "classification_reason": reason,
        "payload_json": _json_text(payload_value),
        "provenance_json": _json_text(sanitize_provenance(provenance_value)),
        "idempotency_key": idempotency_key,
        "normalization_version": normalization_version,
        "source_id": source_id,
        "source_asof": source_asof,
        "subject_kind": subject_kind,
        "issuer_ids": encoded_issuers,
        "context_type": str(row.get("context_type") or "unclassified").strip().lower(),
        "context_ids": _encode_string_list(row.get("context_ids")),
    }
    sql = (
        "INSERT OR IGNORE INTO events (" + ",".join(_COLUMNS) + ") VALUES ("
        + ",".join(":" + col for col in _COLUMNS) + ")"
    )
    cur = conn.execute(sql, values)
    inserted = cur.rowcount == 1
    if inserted:
        _fts_index(conn, values["event_id"], values["title"], values["summary"])
    conn.execute(
        "INSERT OR IGNORE INTO event_idempotency "
        "(idempotency_key,event_id,normalization_version,created_at) VALUES (?,?,?,?)",
        (idempotency_key, values["event_id"], normalization_version, available_at),
    )
    registered = conn.execute(
        "SELECT event_id FROM event_idempotency WHERE idempotency_key=?",
        (idempotency_key,),
    ).fetchone()
    if registered is None or str(registered["event_id"]) != str(values["event_id"]):
        raise ValueError("idempotency registry conflict")
    return inserted


def record_ingest_run(conn: sqlite3.Connection, run: Mapping[str, Any]) -> bool:
    """Append one source-attempt audit row; never materialize status as an event."""
    source_id = str(run.get("source_id") or "").strip().lower()
    status = str(run.get("status") or "").strip().lower()
    if not source_id:
        raise ValueError("ingest run source_id is required")
    if status not in {"ok", "partial", "empty", "failed", "stale"}:
        raise ValueError("invalid ingest run status")
    observed_at = normalize_timestamp(run.get("observed_at"))
    requested = run.get("requested_asof")
    requested_asof = (
        None if requested in (None, "")
        else normalize_timestamp(
            requested,
            date_end=isinstance(requested, str)
            and bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}", requested.strip())),
        )
    )
    metadata = sanitize_provenance(run.get("metadata"))
    identity = _json_text({
        "source_id": source_id,
        "requested_asof": requested_asof,
        "observed_at": observed_at,
        "status": status,
        "fetched_count": int(run.get("fetched_count") or 0),
        "inserted_count": int(run.get("inserted_count") or 0),
        "error_kind": str(run.get("error_kind") or ""),
        "note": str(run.get("note") or ""),
        "metadata": metadata,
    }) or "{}"
    run_id = str(run.get("run_id") or "").strip() or hashlib.sha256(
        identity.encode("utf-8")
    ).hexdigest()
    cur = conn.execute(
        "INSERT OR IGNORE INTO event_ingest_runs "
        "(run_id,source_id,requested_asof,observed_at,status,fetched_count,"
        "inserted_count,error_kind,note,metadata_json) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (
            run_id, source_id, requested_asof, observed_at, status,
            max(0, int(run.get("fetched_count") or 0)),
            max(0, int(run.get("inserted_count") or 0)),
            str(run.get("error_kind") or "") or None,
            str(run.get("note") or "")[:1000] or None,
            _json_text(metadata),
        ),
    )
    return cur.rowcount == 1


def read_ingest_runs(
    conn: sqlite3.Connection, *, source_id: Optional[str] = None, limit: int = 100,
) -> List[Dict[str, Any]]:
    bounded = max(1, min(int(limit), 1000))
    sql = "SELECT * FROM event_ingest_runs"
    params: list[Any] = []
    if source_id:
        sql += " WHERE source_id=?"
        params.append(str(source_id).strip().lower())
    sql += " ORDER BY observed_at DESC,run_id DESC LIMIT ?"
    params.append(bounded)
    rows = []
    for raw in conn.execute(sql, params).fetchall():
        item = dict(raw)
        try:
            item["metadata"] = json.loads(item.pop("metadata_json") or "null")
        except (TypeError, ValueError):
            item["metadata"] = None
        rows.append(item)
    return rows


def _relation_timestamp(value: Any, *, field: str) -> str:
    """Normalize one explicit, non-date-only relation audit timestamp."""
    if value is None or value == "":
        raise ValueError(f"{field} is required")
    if isinstance(value, date) and not isinstance(value, datetime):
        raise ValueError(f"{field} must include a time")
    if isinstance(value, str):
        raw = value.strip()
        if not re.match(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}", raw):
            raise ValueError(f"{field} must include a time")
    return normalize_timestamp(value)


def _relation_target_key(relation: Mapping[str, Any]) -> tuple[str, str]:
    target_id = str(relation.get("target_id") or "").strip()
    if target_id:
        return "id", target_id
    target_name = str(relation.get("target_name") or "").strip()
    if target_name:
        return "name", target_name
    raise ValueError("relation requires target_id or target_name")


def _relation_effective_at(relation: Mapping[str, Any]) -> str:
    status = str(relation.get("relation_status") or "").strip()
    field = "reviewed_at" if status in REVIEW_RELATION_STATUSES else "created_at"
    return _relation_timestamp(relation.get(field), field=field)


def _same_relation_payload(existing: sqlite3.Row, values: Mapping[str, Any]) -> bool:
    return all(existing[key] == values[key] for key in values)


def insert_event_relation(conn: sqlite3.Connection, relation: Mapping[str, Any]) -> bool:
    """Append one immutable relation assertion; exact replays are idempotent.

    ``candidate`` and ``source_asserted`` assertions become effective at their
    explicit ``created_at``.  Human ``confirmed``/``rejected`` reviews must be
    separate rows and become effective at their explicit ``reviewed_at``.
    Effective timestamps cannot be date-only or later than the write-time Hong
    Kong clock, so a stored assertion cannot leak future knowledge into PIT
    replay.
    """
    relation_id = str(relation.get("relation_id") or "").strip()
    event_id = str(relation.get("event_id") or "").strip()
    relation_type = str(relation.get("relation_type") or "").strip()
    status = str(relation.get("relation_status") or "").strip()
    if not relation_id:
        raise ValueError("relation_id is required")
    if not event_id:
        raise ValueError("event_id is required")
    if relation_type not in RELATION_TYPES:
        raise ValueError("relation_type must be entity or industry")
    if status not in RELATION_STATUSES:
        raise ValueError("invalid relation_status")
    target_kind, target_value = _relation_target_key(relation)
    target_id = target_value if target_kind == "id" else None
    target_name = str(relation.get("target_name") or "").strip() or None

    confidence = relation.get("confidence")
    if confidence is not None:
        confidence = float(confidence)
        if not math.isfinite(confidence) or not 0 <= confidence <= 1:
            raise ValueError("confidence must be finite and within [0,1]")

    reviewed_by = str(relation.get("reviewed_by") or "").strip() or None
    reviewed_at_value = relation.get("reviewed_at")
    if status in REVIEW_RELATION_STATUSES:
        if reviewed_by is None:
            raise ValueError("confirmed/rejected relation requires reviewed_by")
        reviewed_at = _relation_timestamp(reviewed_at_value, field="reviewed_at")
        created_value = relation.get("created_at")
        created_at = _relation_timestamp(
            reviewed_at if created_value is None else created_value, field="created_at"
        )
        if reviewed_at < created_at:
            raise ValueError("reviewed_at cannot precede created_at")
    else:
        created_at = _relation_timestamp(relation.get("created_at"), field="created_at")
        if reviewed_by is not None or reviewed_at_value not in (None, ""):
            raise ValueError("review metadata must be appended as a confirmed/rejected row")
        reviewed_at = None

    effective_at = reviewed_at or created_at
    now_hk = datetime.now(HONG_KONG).replace(tzinfo=None)
    effective_dt = _parse_datetime(effective_at)
    if effective_dt is None or effective_dt > now_hk:
        raise ValueError("relation effective timestamp cannot be in the future")

    values: Dict[str, Any] = {
        "relation_id": relation_id,
        "event_id": event_id,
        "relation_type": relation_type,
        "target_id": target_id,
        "target_name": target_name,
        "relation_status": status,
        "confidence": confidence,
        "evidence_json": _json_text(sanitize_provenance(
            relation.get("evidence_json", relation.get("evidence"))
        )),
        "relation_method": relation.get("relation_method"),
        "created_at": created_at,
        "reviewed_by": reviewed_by,
        "reviewed_at": reviewed_at,
    }

    existing = conn.execute(
        "SELECT * FROM event_relations WHERE relation_id=?", (relation_id,)
    ).fetchone()
    if existing is not None:
        if _same_relation_payload(existing, values):
            return False
        raise ValueError("relation_id already exists with different immutable payload")

    if status in REVIEW_RELATION_STATUSES:
        prior_rows = conn.execute(
            "SELECT * FROM event_relations WHERE event_id=? AND relation_type=?",
            (event_id, relation_type),
        ).fetchall()
        prior_exists = False
        for prior in prior_rows:
            prior_item = dict(prior)
            try:
                same_target = _relation_target_key(prior_item) == (target_kind, target_value)
                prior_effective = _relation_effective_at(prior_item)
            except ValueError:
                continue
            if same_target and prior_effective <= effective_at:
                prior_exists = True
                break
        if not prior_exists:
            raise ValueError("confirmed/rejected relation must append to an existing target")

    columns = tuple(values)
    cur = conn.execute(
        "INSERT INTO event_relations (" + ",".join(columns) + ") VALUES ("
        + ",".join("?" for _ in columns) + ") "
        "ON CONFLICT(relation_id) DO NOTHING",
        tuple(values[column] for column in columns),
    )
    if cur.rowcount == 1:
        return True
    existing = conn.execute(
        "SELECT * FROM event_relations WHERE relation_id=?", (relation_id,)
    ).fetchone()
    if existing is not None and _same_relation_payload(existing, values):
        return False
    raise ValueError("relation insert conflicted with immutable audit state")


def read_event_relations(
    conn: sqlite3.Connection,
    asof: Any,
    *,
    event_id: Optional[str] = None,
    event_ids: Optional[Sequence[str]] = None,
    relation_type: Optional[str] = None,
    collapse: bool = True,
) -> List[Dict[str, Any]]:
    """Replay relation assertions visible at ``asof`` without future reviews.

    By default the latest assertion for each ``(event, type, target)`` is
    returned.  Set ``collapse=False`` to obtain every eligible immutable audit
    row.  At least one event id is required so this helper cannot accidentally
    scan the entire relation journal.
    """
    normalized_asof = normalize_asof(asof)
    if event_id is not None and event_ids is not None:
        raise ValueError("pass event_id or event_ids, not both")
    selected_ids = [str(event_id)] if event_id is not None else [
        str(value) for value in (event_ids or ())
    ]
    selected_ids = list(dict.fromkeys(value for value in selected_ids if value))
    if not selected_ids:
        raise ValueError("read_event_relations requires event_id or event_ids")
    if len(selected_ids) > 1000:
        raise ValueError("event_ids is limited to 1000")
    if relation_type is not None and relation_type not in RELATION_TYPES:
        raise ValueError("relation_type must be entity or industry")

    placeholders = ",".join("?" for _ in selected_ids)
    sql = (
        "SELECT r.* FROM event_relations r JOIN events e ON e.event_id=r.event_id "
        f"WHERE r.event_id IN ({placeholders}) "
        "AND e.available_at<=? AND (e.invalidated_at IS NULL OR e.invalidated_at>?)"
    )
    params: List[Any] = [*selected_ids, normalized_asof, normalized_asof]
    if relation_type is not None:
        sql += " AND r.relation_type=?"
        params.append(relation_type)

    eligible: List[Dict[str, Any]] = []
    for row in conn.execute(sql, params).fetchall():
        item = dict(row)
        try:
            effective_at = _relation_effective_at(item)
            _relation_target_key(item)
        except ValueError:
            continue
        if effective_at <= normalized_asof:
            item["effective_at"] = effective_at
            eligible.append(item)

    status_priority = {
        "candidate": 0, "source_asserted": 1, "confirmed": 2, "rejected": 3,
    }
    eligible.sort(key=lambda item: (
        item["effective_at"],
        status_priority.get(str(item.get("relation_status")), -1),
        str(item.get("relation_id") or ""),
    ))
    if not collapse:
        return eligible

    state: Dict[tuple[str, str, str, str], Dict[str, Any]] = {}
    for item in eligible:
        target_kind, target_value = _relation_target_key(item)
        key = (
            str(item["event_id"]), str(item["relation_type"]),
            target_kind, target_value,
        )
        state[key] = item
    return sorted(state.values(), key=lambda item: (
        str(item["event_id"]), str(item["relation_type"]),
        _relation_target_key(item),
    ))


def _decode_tickers(raw: Any) -> List[str]:
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return []
    return [str(item) for item in parsed] if isinstance(parsed, list) else []


def _row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
    out = dict(row)
    out["tickers"] = _decode_tickers(out.get("tickers"))
    out["issuer_ids"] = _decode_tickers(out.get("issuer_ids"))
    out["context_ids"] = _decode_tickers(out.get("context_ids"))
    for field in ("payload_json", "provenance_json"):
        raw = out.get(field)
        if raw:
            try:
                out[field[:-5]] = json.loads(raw)
            except (TypeError, ValueError):
                out[field[:-5]] = raw
    return out


def read_events(
    conn: sqlite3.Connection,
    asof: str,
    *,
    event_type: Optional[str] = None,
    limit: int = 200,
) -> List[sqlite3.Row]:
    """PIT read, deterministically ordered by availability and event id."""
    normalized_asof = normalize_asof(asof)
    bounded_limit = max(1, min(int(limit), 1000))
    sql = f"SELECT * FROM events WHERE {PIT_PREDICATE}"
    params: Dict[str, Any] = {"asof": normalized_asof, "limit": bounded_limit}
    if event_type:
        sql += " AND event_type=:event_type"
        params["event_type"] = event_type
    sql += " ORDER BY available_at DESC,event_id DESC LIMIT :limit"
    return list(conn.execute(sql, params).fetchall())


class _FtsUnavailable(Exception):
    pass


def _jieba_query(query: str) -> str:
    try:
        tokens = [token for token in _jieba_tokens(query or "").split(" ") if token]
    except Exception as exc:
        raise _FtsUnavailable(str(exc)) from exc
    return " OR ".join('"' + token.replace('"', "") + '"' for token in tokens)


def _search_fts(
    conn: sqlite3.Connection, query: str, asof: str, limit: int
) -> List[Dict[str, Any]]:
    if not query:
        return []
    sql = (
        "SELECT DISTINCT e.* FROM events e JOIN events_fts f ON f.event_id=e.event_id "
        f"WHERE f.tokens MATCH :q AND {PIT_PREDICATE} "
        "ORDER BY e.available_at DESC,e.event_id DESC LIMIT :limit"
    )
    rows = conn.execute(sql, {"q": query, "asof": asof, "limit": limit}).fetchall()
    return [_row_to_dict(row) for row in rows]


def _search_like(
    conn: sqlite3.Connection, query: str, asof: str, limit: int
) -> List[Dict[str, Any]]:
    sql = (
        "SELECT * FROM events WHERE (title LIKE :like OR summary LIKE :like) "
        f"AND {PIT_PREDICATE} "
        "ORDER BY available_at DESC,event_id DESC LIMIT :limit"
    )
    rows = conn.execute(
        sql, {"like": f"%{query or ''}%", "asof": asof, "limit": limit}
    ).fetchall()
    return [_row_to_dict(row) for row in rows]


def search_events(
    query: str,
    asof: str,
    limit: int = 50,
    *,
    conn: Optional[sqlite3.Connection] = None,
    path: Optional[Union[str, Path]] = None,
) -> Dict[str, Any]:
    """PIT text search with LIKE fallback and a read-only default connection."""
    normalized_asof = normalize_asof(asof)
    bounded_limit = max(1, min(int(limit), 200))
    own = conn is None
    if conn is None:
        conn = connect_readonly(path)
    try:
        try:
            items = _search_fts(conn, _jieba_query(query), normalized_asof, bounded_limit)
            status = "ok"
        except (_FtsUnavailable, sqlite3.OperationalError):
            items = _search_like(conn, query, normalized_asof, bounded_limit)
            status = "unavailable"
        return {
            "items": items, "n": len(items), "fts": status,
            "asof": normalized_asof, "query": query,
        }
    finally:
        if own:
            conn.close()


__all__ = [
    "HONG_KONG", "LATEST_SCHEMA_VERSION", "PIT_PREDICATE",
    "RELATION_STATUSES", "RELATION_TYPES", "REVIEW_RELATION_STATUSES",
    "TIME_CLASSIFICATIONS", "backfill_fts", "classify_event_time", "connect",
    "connect_readonly", "db_path", "init_db", "insert_event",
    "insert_event_relation", "normalize_asof", "normalize_timestamp",
    "read_event_relations", "read_events", "read_ingest_runs", "rebuild_fts",
    "record_ingest_run", "sanitize_provenance", "search_events",
]
