# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import sqlite3

from guanlan_v2.events import store
from guanlan_v2.events.legacy_backfill import (
    BACKFILL_VERSION,
    backfill_legacy_events,
    plan_legacy_normalization,
)
from guanlan_v2.events.research import search_events_page


def _db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    store.init_db(conn)
    return conn


def _legacy(conn: sqlite3.Connection, **overrides) -> None:
    row = {
        "event_id": "legacy-news-1",
        "event_type": "newsradar:semiconductors",
        "tickers": "[]",
        "title": "先进封装行业更新",
        "summary": "供应链景气线索",
        "source": "publisher-name",
        "source_ref": "https://example.test/news/1",
        "event_time": "2026-08-20T09:00:00",
        "available_at": "2026-08-20T10:00:00",
        "line_id": "legacy-line-1",
        "ingested_from": "newsradar",
    }
    row.update(overrides)
    columns = tuple(row)
    conn.execute(
        "INSERT INTO events (" + ",".join(columns) + ") VALUES ("
        + ",".join("?" for _ in columns) + ")",
        tuple(row[name] for name in columns),
    )
    conn.commit()


def test_plan_never_promotes_publisher_source_or_guesses_unknown_context():
    plan = plan_legacy_normalization({
        "event_id": "unknown-1", "source": "publisher-only", "tickers": "[]",
        "available_at": "2026-08-20T10:00:00", "event_type": "news",
    })
    assert plan is not None
    assert plan.values["source_id"] is None
    assert plan.values["subject_kind"] is None
    assert plan.values["context_type"] is None
    assert plan.quality == "partial"
    assert "source_id_not_recoverable" in plan.issues
    assert "subject_kind_not_recoverable" in plan.issues
    assert "source_asof_from_local_available_at" in plan.issues


def test_dry_run_is_read_only_and_labels_available_at_fallback():
    conn = _db()
    _legacy(conn)
    before = tuple(conn.execute("SELECT * FROM events").fetchone())
    result = backfill_legacy_events(conn, apply=False)
    after = tuple(conn.execute("SELECT * FROM events").fetchone())
    assert before == after
    assert result["candidates"] == 1 and result["updated"] == 0
    assert result["quality"] == {"derived": 0, "partial": 1, "unrecoverable": 0}
    assert result["issues"] == {"source_asof_from_local_available_at": 1}


def test_apply_materializes_traceable_industry_relation_and_global_filter():
    conn = _db()
    _legacy(conn)
    result = backfill_legacy_events(conn, apply=True)
    assert result["updated"] == 1 and result["relations_inserted"] == 1

    row = dict(conn.execute("SELECT * FROM events").fetchone())
    assert row["source_id"] == "newsradar"
    assert row["source_asof"] == "2026-08-20T10:00:00"
    assert row["subject_kind"] == "context"
    assert json.loads(row["issuer_ids"]) == []
    assert row["context_type"] == "industry_news"
    assert json.loads(row["context_ids"]) == ["semiconductors"]
    assert row["normalization_version"] == BACKFILL_VERSION
    audit = json.loads(row["provenance_json"])["legacy_normalization"]
    assert audit["quality"] == "partial"
    assert audit["issues"] == ["source_asof_from_local_available_at"]

    relation = dict(conn.execute("SELECT * FROM event_relations").fetchone())
    assert relation["relation_type"] == "industry"
    assert relation["target_id"] == "semiconductors"
    assert relation["relation_status"] == "source_asserted"

    page = search_events_page(
        "先进封装", "2026-08-20T12:00:00",
        industries=["semiconductors"], conn=conn,
    )
    assert [item["event_id"] for item in page["items"]] == ["legacy-news-1"]
    linked = page["items"][0]
    assert any(item["target_id"] == "semiconductors"
               and item["relation_status"] == "source_asserted"
               for item in linked["relations"])


def test_kuaixun_structured_ticker_becomes_event_link_not_issuer_fact():
    conn = _db()
    _legacy(
        conn,
        event_id="legacy-kuaixun-1",
        event_type="kuaixun",
        tickers=json.dumps(["300750"]),
        title="公司快讯",
        ingested_from="kuaixun",
    )
    result = backfill_legacy_events(conn, apply=True)
    assert result["relations_inserted"] == 1
    row = dict(conn.execute("SELECT * FROM events").fetchone())
    assert row["subject_kind"] == "issuer"
    assert json.loads(row["issuer_ids"]) == ["SZ300750"]
    assert row["context_type"] == "company_news"
    assert json.loads(row["context_ids"]) == ["a_share"]

    page = search_events_page(
        "公司快讯", "2026-08-20T12:00:00", entities=["SZ300750"], conn=conn,
    )
    assert [item["event_id"] for item in page["items"]] == ["legacy-kuaixun-1"]
    assert page["items"][0]["issuer_fact"] is False


def test_apply_is_idempotent():
    conn = _db()
    _legacy(conn)
    first = backfill_legacy_events(conn, apply=True)
    second = backfill_legacy_events(conn, apply=True)
    assert first["updated"] == 1
    assert second["candidates"] == 0 and second["updated"] == 0
    assert conn.execute("SELECT count(*) FROM event_relations").fetchone()[0] == 1
