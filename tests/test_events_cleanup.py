# -*- coding: utf-8 -*-
from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

from guanlan_v2.events import store
from guanlan_v2.events.cleanup import (
    CleanupPlanError,
    PLAN_SCHEMA,
    PLAN_SCHEMA_V1,
    audit_cleanup_plan,
    execute_cleanup_plan,
    load_plan,
    plan_digest,
    rows_digest,
)


_EVENT_CHILD_TABLES = (
    "events_fts", "event_idempotency", "event_relations", "legacy_imports",
)


def _file_hash(path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _fixture_db(tmp_path):
    path = (tmp_path / "events.db").resolve()
    conn = store.connect(path)
    store.init_db(conn)
    with conn:
        for event_id, title in (("polluted", "synthetic fixture"),
                                ("keep", "real event")):
            assert store.insert_event(conn, {
                "event_id": event_id,
                "event_type": "newsradar:semi",
                "title": title,
                "available_at": "2026-08-30T12:00:00",
                "source_id": "newsradar",
                "ingested_from": "newsradar",
                "idempotency_key": f"normalized:{event_id}",
                "normalization_version": "test-v1",
            })
        assert store.record_ingest_run(conn, {
            "run_id": "test-run", "source_id": "newsradar",
            "observed_at": "2026-08-30T12:00:01", "status": "ok",
            "fetched_count": 1, "inserted_count": 1,
        })
        assert store.insert_event_relation(conn, {
            "relation_id": "polluted-industry", "event_id": "polluted",
            "relation_type": "industry", "target_id": "semi",
            "target_name": "semiconductor", "relation_status": "candidate",
            "confidence": 0.8, "relation_method": "test",
            "created_at": "2026-08-30T12:00:02",
        })
    conn.close()
    return path


def _plan(path):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    event_rows = [dict(row) for row in conn.execute(
        "SELECT * FROM events WHERE event_id='polluted'"
    )]
    run_rows = [dict(row) for row in conn.execute(
        "SELECT * FROM event_ingest_runs WHERE run_id='test-run'"
    )]
    user_version = int(conn.execute("PRAGMA user_version").fetchone()[0])
    child_targets = {}
    for table in _EVENT_CHILD_TABLES:
        child_rows = [dict(row) for row in conn.execute(
            f'SELECT * FROM "{table}" WHERE event_id=?', ("polluted",),
        )]
        child_targets[table] = {
            "id_column": "event_id",
            "expected_count": len(child_rows),
            "rows_sha256": rows_digest(child_rows, id_column="event_id"),
        }
    definitions = []
    for name, table, sql in conn.execute(
        "SELECT name,tbl_name,sql FROM sqlite_master WHERE type='trigger' "
        "AND tbl_name IN (?,?,?,?,?,?) ORDER BY name",
        (*_EVENT_CHILD_TABLES, "events", "event_ingest_runs"),
    ):
        normalized = " ".join(str(sql or "").split())
        if " BEFORE DELETE ON " not in f" {normalized.upper()} ":
            continue
        definitions.append({
            "name": str(name),
            "table": str(table),
            "sql_sha256": hashlib.sha256(normalized.encode("utf-8")).hexdigest(),
        })
    definitions.sort(key=lambda item: item["name"])
    conn.close()
    plan = {
        "schema": PLAN_SCHEMA,
        "plan_id": "test-plan",
        "database": {"user_version": user_version},
        "targets": {
            "events": {
                "id_column": "event_id", "ids": ["polluted"],
                "expected_count": 1,
                "rows_sha256": rows_digest(event_rows, id_column="event_id"),
            },
            "event_ingest_runs": {
                "id_column": "run_id", "ids": ["test-run"],
                "expected_count": 1,
                "rows_sha256": rows_digest(run_rows, id_column="run_id"),
            },
        },
        "event_children": child_targets,
        "delete_triggers": {
            "expected_count": len(definitions),
            "definitions": definitions,
            "definitions_sha256": rows_digest(definitions, id_column="name"),
        },
    }
    plan["plan_sha256"] = plan_digest(plan)
    return plan


def test_dry_run_binds_full_rows_and_never_mutates_source(tmp_path):
    path = _fixture_db(tmp_path)
    plan = _plan(path)
    before = _file_hash(path)

    report = audit_cleanup_plan(path, plan)

    assert report["ok"] is True
    assert report["targets"]["events"]["matched_count"] == 1
    assert report["targets"]["event_ingest_runs"]["matched_count"] == 1
    assert report["event_children"]["events_fts"]["matched_count"] == 1
    assert report["event_children"]["event_idempotency"]["matched_count"] == 1
    assert report["event_children"]["event_relations"]["matched_count"] == 1
    assert report["event_children"]["legacy_imports"]["matched_count"] == 0
    assert report["delete_triggers"]["matched_count"] == 3
    assert report["execution_eligible"] is True
    assert report["production_mutated"] is False
    assert _file_hash(path) == before


def test_row_or_manifest_drift_fails_closed(tmp_path):
    path = _fixture_db(tmp_path)
    plan = _plan(path)
    conn = sqlite3.connect(path)
    conn.execute("UPDATE events SET title='drifted' WHERE event_id='polluted'")
    conn.commit()
    conn.close()

    with pytest.raises(CleanupPlanError, match="selected-row hash drifted"):
        audit_cleanup_plan(path, plan)

    broken = dict(plan)
    broken["plan_id"] = "tampered"
    with pytest.raises(CleanupPlanError, match="plan digest mismatch"):
        audit_cleanup_plan(path, broken)


def test_child_row_count_or_hash_drift_fails_closed(tmp_path):
    path = _fixture_db(tmp_path)
    plan = _plan(path)
    conn = sqlite3.connect(path)
    conn.execute(
        "UPDATE events_fts SET tokens='drifted child row' WHERE event_id='polluted'"
    )
    conn.commit()
    conn.close()

    with pytest.raises(CleanupPlanError, match="events_fts child-row hash drifted"):
        audit_cleanup_plan(path, plan)

    path = _fixture_db(tmp_path / "count-drift")
    plan = _plan(path)
    conn = sqlite3.connect(path)
    conn.execute(
        "INSERT INTO legacy_imports "
        "(import_id,record_digest,event_id,source_kind,imported_at) "
        "VALUES ('late','late','polluted','test','2026-08-30T12:01:00')"
    )
    conn.commit()
    conn.close()
    with pytest.raises(CleanupPlanError, match="legacy_imports child-row count drifted"):
        audit_cleanup_plan(path, plan)


def test_delete_trigger_definition_drift_fails_closed(tmp_path):
    path = _fixture_db(tmp_path)
    plan = _plan(path)
    conn = sqlite3.connect(path)
    conn.executescript("""
        DROP TRIGGER trg_event_relations_no_delete;
        CREATE TRIGGER trg_event_relations_no_delete
        BEFORE DELETE ON event_relations
        BEGIN
            SELECT RAISE(ABORT, 'changed append-only guard');
        END;
    """)
    conn.close()

    with pytest.raises(CleanupPlanError, match="delete-trigger definitions drifted"):
        audit_cleanup_plan(path, plan)


def test_v1_plan_remains_auditable_but_is_never_executable(tmp_path):
    path = _fixture_db(tmp_path)
    plan = _plan(path)
    plan["schema"] = PLAN_SCHEMA_V1
    plan.pop("event_children")
    plan.pop("delete_triggers")
    plan["plan_sha256"] = plan_digest(plan)

    report = audit_cleanup_plan(path, plan)
    assert report["execution_eligible"] is False
    assert "audit-only" in report["legacy_plan_limitation"]

    backup = (tmp_path / "legacy-backup.db").resolve()
    output = (tmp_path / "legacy-candidate.db").resolve()
    with pytest.raises(CleanupPlanError, match="immutable-audit-only"):
        execute_cleanup_plan(
            path, plan, backup_target=backup, output_target=output,
            confirmation=plan["plan_sha256"],
        )
    assert not backup.exists()
    assert not output.exists()


def test_execute_only_builds_backup_and_clean_candidate(tmp_path):
    path = _fixture_db(tmp_path)
    plan = _plan(path)
    source_before = _file_hash(path)
    backup = (tmp_path / "backup.db").resolve()
    output = (tmp_path / "candidate.db").resolve()

    report = execute_cleanup_plan(
        path, plan, backup_target=backup, output_target=output,
        confirmation=plan["plan_sha256"],
    )

    assert report["production_mutated"] is False
    assert report["deleted_from_candidate"]["events"] == 1
    assert report["deleted_from_candidate"]["event_ingest_runs"] == 1
    assert report["deleted_from_candidate"]["events_fts"] == 1
    assert report["deleted_from_candidate"]["event_idempotency"] == 1
    assert report["deleted_from_candidate"]["event_relations"] == 1
    assert report["deleted_from_candidate"]["legacy_imports"] == 0
    assert path.exists() and _file_hash(path) == source_before
    assert backup.exists() and _file_hash(backup) == source_before
    assert output.exists() and _file_hash(output) != source_before
    assert not type(output)(str(output) + "-wal").exists()
    assert not type(output)(str(output) + "-shm").exists()

    original = sqlite3.connect(path)
    untouched = sqlite3.connect(backup)
    candidate = sqlite3.connect(output)
    try:
        assert original.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 2
        assert untouched.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 2
        assert candidate.execute("SELECT event_id FROM events").fetchall() == [("keep",)]
        assert candidate.execute("SELECT COUNT(*) FROM event_ingest_runs").fetchone()[0] == 0
        assert candidate.execute("SELECT COUNT(*) FROM event_relations").fetchone()[0] == 0
        assert candidate.execute(
            "SELECT COUNT(*) FROM event_idempotency WHERE event_id='polluted'"
        ).fetchone()[0] == 0
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            candidate.execute("DELETE FROM event_idempotency WHERE event_id='keep'")
    finally:
        original.close()
        untouched.close()
        candidate.close()


def test_confirmation_paths_and_live_wal_are_mandatory(tmp_path):
    path = _fixture_db(tmp_path)
    plan = _plan(path)
    backup = (tmp_path / "backup.db").resolve()
    output = (tmp_path / "candidate.db").resolve()
    with pytest.raises(CleanupPlanError, match="confirmation"):
        execute_cleanup_plan(
            path, plan, backup_target=backup, output_target=output,
            confirmation="wrong",
        )
    with pytest.raises(CleanupPlanError, match="distinct"):
        execute_cleanup_plan(
            path, plan, backup_target=backup, output_target=backup,
            confirmation=plan["plan_sha256"],
        )

    wal = type(path)(str(path) + "-wal")
    wal.write_bytes(b"active")
    with pytest.raises(CleanupPlanError, match="WAL/SHM"):
        audit_cleanup_plan(path, plan)


def test_serialized_v1_and_v2_manifests_have_valid_self_digests(tmp_path):
    """发布包不携带本机清理目标；用隔离 fixture 守护文件加载与自摘要契约。"""
    database = _fixture_db(tmp_path)
    plan = _plan(database)
    manifest = (tmp_path / "reviewed-v2.json").resolve()
    manifest.write_text(
        json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    loaded = load_plan(manifest)
    assert loaded["schema"] == PLAN_SCHEMA
    assert loaded["plan_sha256"] == plan_digest(loaded) == plan["plan_sha256"]
    assert loaded["event_children"] == plan["event_children"]
    assert loaded["delete_triggers"] == plan["delete_triggers"]

    legacy = dict(plan)
    legacy["schema"] = PLAN_SCHEMA_V1
    legacy.pop("event_children")
    legacy.pop("delete_triggers")
    legacy["plan_sha256"] = plan_digest(legacy)
    legacy_manifest = (tmp_path / "reviewed-v1.json").resolve()
    legacy_manifest.write_text(
        json.dumps(legacy, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    loaded_legacy = load_plan(legacy_manifest)
    assert loaded_legacy["schema"] == PLAN_SCHEMA_V1
    assert loaded_legacy["plan_sha256"] == plan_digest(loaded_legacy)
