# -*- coding: utf-8 -*-
"""events.db P0 migration, time reclassification and read-only permissions."""
from __future__ import annotations

import sqlite3

import pytest

from guanlan_v2.events import store


_V1_DDL = """
CREATE TABLE events (
    event_id TEXT PRIMARY KEY,
    event_type TEXT NOT NULL,
    tickers TEXT,
    title TEXT,
    summary TEXT,
    source TEXT,
    source_ref TEXT,
    event_time TEXT,
    available_at TEXT NOT NULL,
    invalidated_at TEXT,
    simhash TEXT,
    line_id TEXT,
    ingested_from TEXT
)
"""


def _legacy_conn(path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute(_V1_DDL)
    rows = [
        ("observed", "news", "普通已发生事件", "2026-08-29T09:00:00",
         "2026-08-29T10:00:00"),
        ("scheduled", "calendar", "计划于下月召开股东大会", "2026-09-10T09:00:00",
         "2026-08-29T10:00:00"),
        ("conflict", "news", "无法证明的未来事件", "2026-09-10T09:00:00",
         "2026-08-29T10:00:00"),
    ]
    conn.executemany(
        "INSERT INTO events(event_id,event_type,title,event_time,available_at,line_id) "
        "VALUES(?,?,?,?,?,?)",
        [(*row, row[0]) for row in rows],
    )
    # Simulate the historical production failure: FTS exists but only one of
    # three rows was indexed.  Migration must repair the gap repeatably.
    try:
        conn.execute("CREATE VIRTUAL TABLE events_fts USING fts5(tokens,event_id UNINDEXED)")
        conn.execute("INSERT INTO events_fts(tokens,event_id) VALUES('普通 事件','observed')")
    except sqlite3.OperationalError:
        pass
    conn.commit()
    return conn


def test_v1_to_p0_migration_preserves_rows_reclassifies_future_and_repairs_fts(tmp_path):
    conn = _legacy_conn(tmp_path / "events.db")

    store.init_db(conn)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == store.LATEST_SCHEMA_VERSION
    columns = {row[1] for row in conn.execute("PRAGMA table_info(events)")}
    assert {"time_classification", "classification_reason", "payload_json",
            "provenance_json"} <= columns
    tables = {row[0] for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"event_relations", "legacy_imports"} <= tables
    got = {row["event_id"]: row["time_classification"] for row in conn.execute(
        "SELECT event_id,time_classification FROM events")}
    assert got == {"observed": "observed", "scheduled": "scheduled",
                   "conflict": "time_conflict"}
    assert all("events-p0-v2" in row[0] for row in conn.execute(
        "SELECT classification_reason FROM events"))
    assert conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 3
    if "events_fts" in tables:
        assert conn.execute("SELECT COUNT(DISTINCT event_id) FROM events_fts").fetchone()[0] == 3

    before = conn.total_changes
    store.init_db(conn)
    assert conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 3
    assert conn.total_changes == before  # repeat migration/backfill is a no-op


def test_migration_rebuilds_old_relation_shape_and_preserves_pit_reviews(tmp_path):
    conn = _legacy_conn(tmp_path / "events.db")
    conn.execute(
        """
        CREATE TABLE event_relations (
            event_id TEXT NOT NULL,
            relation_type TEXT NOT NULL,
            target_id TEXT,
            target_name TEXT,
            relation_status TEXT NOT NULL,
            confidence REAL,
            evidence_json TEXT,
            relation_method TEXT,
            reviewed_by TEXT,
            reviewed_at TEXT
        )
        """
    )
    conn.executemany(
        "INSERT INTO event_relations VALUES(?,?,?,?,?,?,?,?,?,?)",
        (
            ("observed", "entity", "SH600000", "浦发银行", "candidate", 0.6,
             None, "legacy_keyword", None, None),
            ("observed", "entity", "SH600000", "浦发银行", "confirmed", 0.99,
             None, "legacy_review", "analyst", "2026-08-29T11:00:00"),
        ),
    )
    conn.commit()

    store.init_db(conn)
    info = {row[1]: row for row in conn.execute("PRAGMA table_info(event_relations)")}
    assert {"relation_id", "created_at", "reviewed_by", "reviewed_at"} <= set(info)
    assert info["relation_id"][5] == 1
    assert info["relation_id"][3] == 1
    assert info["created_at"][3] == 1
    assert conn.execute("SELECT COUNT(*) FROM event_relations").fetchone()[0] == 2
    assert all(row[0] for row in conn.execute(
        "SELECT relation_id FROM event_relations"
    ))
    before_review = store.read_event_relations(
        conn, "2026-08-29T10:30:00", event_id="observed",
    )
    after_review = store.read_event_relations(
        conn, "2026-08-29T11:30:00", event_id="observed",
    )
    assert [row["relation_status"] for row in before_review] == ["candidate"]
    assert [row["relation_status"] for row in after_review] == ["confirmed"]
    assert conn.execute("PRAGMA user_version").fetchone()[0] == store.LATEST_SCHEMA_VERSION


def test_migration_rolls_back_schema_and_version_on_midflight_failure(tmp_path, monkeypatch):
    conn = _legacy_conn(tmp_path / "events.db")

    def boom(_conn):
        raise RuntimeError("classification backfill failed")

    monkeypatch.setattr(store, "_backfill_time_classification", boom)
    with pytest.raises(RuntimeError, match="classification backfill failed"):
        store.init_db(conn)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 0
    columns = {row[1] for row in conn.execute("PRAGMA table_info(events)")}
    assert "time_classification" not in columns
    assert conn.execute(
        "SELECT 1 FROM sqlite_master WHERE name='event_relations'").fetchone() is None
    assert conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 3


def test_readonly_connection_never_creates_db_and_sqlite_denies_writes(tmp_path):
    missing = tmp_path / "missing" / "events.db"
    with pytest.raises(sqlite3.OperationalError):
        store.connect_readonly(missing)
    assert not missing.exists() and not missing.parent.exists()

    path = tmp_path / "events.db"
    writable = store.connect(path)
    store.init_db(writable)
    with writable:
        store.insert_event(writable, {
            "event_id": "e1", "event_type": "news", "title": "只读证据",
            "available_at": "2026-08-30T09:00:00",
        })
    writable.close()

    readonly = store.connect_readonly(path)
    assert readonly.execute("PRAGMA query_only").fetchone()[0] == 1
    assert readonly.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 1
    with pytest.raises(sqlite3.OperationalError):
        readonly.execute("INSERT INTO events(event_id,event_type,available_at) "
                         "VALUES('forbidden','news','2026-08-30T10:00:00')")
    readonly.close()


def test_asof_normalization_date_end_and_offset_conversion():
    assert store.normalize_asof("2026-08-30").startswith("2026-08-30T23:59:59.999999")
    assert store.normalize_asof("2026-08-30T04:00:00Z") == "2026-08-30T12:00:00"
    assert store.normalize_timestamp("2026-08-30T12:00:00+08:00") == "2026-08-30T12:00:00"
    with pytest.raises(ValueError):
        store.normalize_asof("")
