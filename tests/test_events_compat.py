# -*- coding: utf-8 -*-
"""Private company events.json / PIT events.jsonl compatibility import."""
from __future__ import annotations

import json

import pytest

from guanlan_v2.events import compat, store


def _db(tmp_path):
    conn = store.connect(tmp_path / "events.db")
    store.init_db(conn)
    return conn


def test_company_events_json_import_is_audited_idempotent_and_reclassifies_future(tmp_path):
    source = tmp_path / "private" / "events.json"
    source.parent.mkdir()
    source.write_text(json.dumps({
        "SZ300750": {
            "positive": [{
                "date": "2026-08-29", "title": "宁德时代签署合作协议",
                "available_at": "2026-08-29T18:00:00",
                "source": "private_company", "source_ref": "C:/private/raw/a.txt",
            }],
            "calendar": [{
                "date": "2026-09-10", "title": "计划于下月召开股东大会",
                "available_at": "2026-08-29T18:00:00",
            }],
            "negative": [{
                "date": "2026-09-11", "title": "无法证明的未来负面事件",
                "available_at": "2026-08-29T18:00:00",
            }],
        }
    }, ensure_ascii=False), encoding="utf-8")
    conn = _db(tmp_path)

    first = compat.import_legacy_events(conn, source)
    assert first["ok"] and first["files"] == 1 and first["scanned"] == 3
    assert first["inserted"] == 3 and first["relations"] == 3
    rows = {row["title"]: dict(row) for row in conn.execute("SELECT * FROM events")}
    assert rows["宁德时代签署合作协议"]["time_classification"] == "observed"
    assert rows["计划于下月召开股东大会"]["time_classification"] == "scheduled"
    assert rows["无法证明的未来负面事件"]["time_classification"] == "time_conflict"
    assert rows["宁德时代签署合作协议"]["event_time"] == "2026-08-29"  # raw source time kept
    assert rows["宁德时代签署合作协议"]["source_ref"] == "a.txt"  # local path redacted
    provenance = json.loads(rows["宁德时代签署合作协议"]["provenance_json"])
    assert provenance["source_locator"] == "events.json"
    assert provenance["record_digest"] and "private" not in provenance["source_locator"]
    assert conn.execute("SELECT COUNT(*) FROM legacy_imports").fetchone()[0] == 3
    assert {row[0] for row in conn.execute(
        "SELECT relation_status FROM event_relations")} == {"source_asserted"}

    second = compat.import_legacy_events(conn, source)
    assert second["inserted"] == 0 and second["skipped"] == 3
    assert conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 3
    assert conn.execute("SELECT COUNT(*) FROM event_relations").fetchone()[0] == 3


def test_pit_jsonl_uses_visible_ts_not_future_ann_date_or_event_date(tmp_path):
    day = tmp_path / "pit_store" / "2026-08-30"
    day.mkdir(parents=True)
    row = {
        "code": "SH600000", "type": "block_trade", "summary": "大宗交易",
        "ann_date": "2026-09-05",  # legacy future-looking carrier; never availability
        "fields": {
            "trade_date": "2026-08-28",
            "visible_ts": "2026-08-29T23:59:59",
            "visible_ts_reason": "legacy_trade_date_eod",
        },
        "source": "events/block_trades.parquet",
    }
    (day / "events.jsonl").write_text(json.dumps(row, ensure_ascii=False) + "\n",
                                        encoding="utf-8")
    conn = _db(tmp_path)

    report = compat.import_legacy_events(conn, tmp_path / "pit_store")
    assert report["inserted"] == 1
    got = dict(conn.execute("SELECT * FROM events").fetchone())
    assert got["available_at"] == "2026-08-29T23:59:59"
    assert got["event_time"] == "2026-08-28"
    proof = json.loads(got["provenance_json"])
    assert proof["availability_basis"] == "fields.visible_ts"
    assert proof["source_locator"] == "pit_store/2026-08-30/events.jsonl"


def test_invalid_jsonl_is_rejected_before_any_import_write(tmp_path):
    day = tmp_path / "pit_store" / "2026-08-30"
    day.mkdir(parents=True)
    (day / "events.jsonl").write_text(
        json.dumps({"code": "SH600000", "type": "risk", "summary": "x",
                    "ann_date": "2026-08-30"}) + "\n{broken\n",
        encoding="utf-8",
    )
    conn = _db(tmp_path)
    with pytest.raises(ValueError, match="line 2"):
        compat.import_legacy_events(conn, tmp_path / "pit_store")
    assert conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM legacy_imports").fetchone()[0] == 0


def test_document_available_at_wrapper_expands_children_and_inherits_visibility(tmp_path):
    source = tmp_path / "events.json"
    source.write_text(json.dumps({
        "available_at": "2026-08-29T18:30:00",
        "source": "legacy_snapshot",
        "events": [{
            "event_time": "2026-08-29",
            "title": "文档包装内的真实事件",
        }],
    }, ensure_ascii=False), encoding="utf-8")

    flattened = compat.load_legacy_file(source)
    assert len(flattened) == 1
    assert flattened[0]["title"] == "文档包装内的真实事件"
    assert flattened[0]["available_at"] == "2026-08-29T18:30:00"
    assert flattened[0]["source"] == "legacy_snapshot"
    assert "events" not in flattened[0]

    conn = _db(tmp_path)
    report = compat.import_legacy_events(conn, source)
    assert report["inserted"] == 1
    got = dict(conn.execute("SELECT * FROM events").fetchone())
    assert got["title"] == "文档包装内的真实事件"
    assert got["available_at"] == "2026-08-29T18:30:00"
