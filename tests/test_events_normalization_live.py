# -*- coding: utf-8 -*-
from __future__ import annotations

import sqlite3
from datetime import datetime

import pytest

from guanlan_v2.events import ingest, store
from guanlan_v2.events.normalization import normalized_event


def _db(tmp_path):
    conn = store.connect(tmp_path / "events.db")
    store.init_db(conn)
    return conn


def test_v2_to_v3_is_additive_and_skips_large_table_backfills(tmp_path, monkeypatch):
    path = tmp_path / "events.db"
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE events (
            event_id TEXT PRIMARY KEY,event_type TEXT NOT NULL,tickers TEXT,
            title TEXT,summary TEXT,source TEXT,source_ref TEXT,event_time TEXT,
            available_at TEXT NOT NULL,invalidated_at TEXT,simhash TEXT,line_id TEXT,
            ingested_from TEXT,time_classification TEXT,classification_reason TEXT,
            payload_json TEXT,provenance_json TEXT
        );
        CREATE VIRTUAL TABLE events_fts
        USING fts5(tokens,event_id UNINDEXED,tokenize='unicode61');
        INSERT INTO events(
            event_id,event_type,tickers,title,source,available_at,line_id,
            ingested_from,time_classification,classification_reason
        ) VALUES(
            'legacy','news','[]','旧事件','旧发布者','2026-08-01T10:00:00',
            'legacy','newsradar','observed','events-p0-v2: existing'
        );
        INSERT INTO events_fts(tokens,event_id) VALUES('旧 事件','legacy');
        PRAGMA user_version=2;
        """
    )
    conn.commit()

    monkeypatch.setattr(
        store, "_backfill_time_classification",
        lambda _conn: (_ for _ in ()).throw(AssertionError("v2 table was rescanned")),
    )
    monkeypatch.setattr(
        store, "_backfill_fts_in_transaction",
        lambda _conn: (_ for _ in ()).throw(AssertionError("healthy FTS was rescanned")),
    )
    store.init_db(conn)

    assert conn.execute("PRAGMA user_version").fetchone()[0] == 3
    assert conn.execute("SELECT count(*) FROM events").fetchone()[0] == 1
    columns = {row[1] for row in conn.execute("PRAGMA table_info(events)")}
    assert {
        "idempotency_key", "source_id", "source_asof", "subject_kind",
        "issuer_ids", "context_type", "context_ids", "normalization_version",
    } <= columns
    assert conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='view' AND name='normalized_events'"
    ).fetchone()
    row = conn.execute("SELECT * FROM normalized_events").fetchone()
    assert row["source_id"] == "newsradar"
    assert row["idempotency_key"] == "legacy-event-id:legacy"
    assert row["pit_available_at"] == "2026-08-01T10:00:00"
    conn.close()


def test_normalized_event_separates_three_clocks_and_replay_is_idempotent(tmp_path):
    conn = _db(tmp_path)
    first = normalized_event(
        source_id="cninfo_announcements", event_type="announcement",
        external_id="ann-1", event_time="2026-08-01T00:00:00",
        source_asof="2026-08-02T09:00:00", available_at="2026-08-02T09:00:05",
        title="公告", issuer_ids=["600519"], subject_kind="issuer",
        context_type="company_disclosure", context_ids=["a_share_disclosure"],
        payload={"announcement_id": "ann-1"}, identity_payload={"id": "ann-1"},
    )
    replay = normalized_event(
        source_id="cninfo_announcements", event_type="announcement",
        external_id="ann-1", event_time="2026-08-01T00:00:00",
        source_asof="2026-08-03T09:00:00", available_at="2026-08-03T09:00:05",
        title="公告", issuer_ids=["SH600519"], subject_kind="issuer",
        context_type="company_disclosure", context_ids=["a_share_disclosure"],
        payload={"announcement_id": "ann-1", "retried": True},
        identity_payload={"id": "ann-1"},
    )
    with conn:
        assert store.insert_event(conn, first) is True
        assert store.insert_event(conn, replay) is False
    row = conn.execute("SELECT * FROM normalized_events").fetchone()
    assert row["event_time"] == "2026-08-01T00:00:00"
    assert row["source_asof"] == "2026-08-02T09:00:00"
    assert row["pit_available_at"] == "2026-08-02T09:00:05"
    assert conn.execute("SELECT count(*) FROM event_idempotency").fetchone()[0] == 1

    with pytest.raises(ValueError, match="different event_id"):
        with conn:
            store.insert_event(conn, {
                **first, "event_id": "another-event-id",
            })
    conn.close()


def _announcement_envelope(status="ok", items=None):
    return {
        "ok": True, "source": "cninfo_announcements", "code": "600519",
        "date": "", "status": status, "items": ([] if items is None else items),
        "provider": "cninfo", "actual_provider": "cninfo",
        "schema_version": "guanlan.live-source-result.v2",
        "fetched_ts": "2026-08-30T12:00:00", "pulled_at": "2026-08-30T11:59:59",
        "network_used": True, "cache_hit": False,
    }


def test_live_empty_and_failed_are_runs_not_pseudo_events(tmp_path):
    conn = _db(tmp_path)
    announcement = {
        "source_id": "cninfo_announcements", "provider": "cninfo",
        "source_type": "announcement", "title": "半年报",
        "publish_ts": "2026-08-15T00:00:00", "url": "https://example/1.pdf",
        "stock_codes": ["SH600519"], "industries": [], "text": "年度报告",
        "raw": {"announcementId": "ann-1", "announcementType": "annual"},
        "revision_id": "rev-1", "pit_certified": True,
    }
    empty = {
        "ok": True, "source": "ths_hot_reason", "date": "2026-08-28",
        "status": "empty", "items": [], "note": "normal empty",
    }
    failed = {
        "ok": True, "source": "ths_limit_up_pool", "date": "20260828",
        "status": "network_error", "items": [], "error_kind": "network_error",
        "note": "network down",
    }
    out = ingest.ingest_live_envelopes(
        conn, [_announcement_envelope(items=[announcement]), empty, failed],
        now=datetime(2026, 8, 30, 12, 0, 5),
    )
    assert out["inserted"] == 1
    assert conn.execute("SELECT count(*) FROM events").fetchone()[0] == 1
    statuses = {row["source_id"]: row["status"] for row in store.read_ingest_runs(conn)}
    assert statuses == {
        "cninfo_announcements": "ok", "ths_hot_reason": "empty",
        "ths_limit_up_pool": "failed",
    }
    row = conn.execute("SELECT * FROM normalized_events").fetchone()
    assert row["source_id"] == "cninfo_announcements"
    assert row["subject_kind"] == "issuer"
    assert row["source_asof"] == "2026-08-30T12:00:00"
    conn.close()


def test_historical_limit_pool_uses_requested_session_not_fetch_timestamp(tmp_path):
    conn = _db(tmp_path)
    em = {
        "source_id": "em_limit_up_pool", "provider": "eastmoney",
        "title": "甲公司", "text": "", "stock_codes": ["000001"],
        "industries": ["银行"], "publish_ts": "2026-08-30T22:00:00",
        "pit_certified": False,
        "raw": {"code": "000001", "name": "甲公司", "first_seal": "09:25:00",
                "limit_days": 2, "industry": "银行", "zt_stat": "2天2板"},
    }
    ths = {
        "source_id": "ths_limit_up_pool", "provider": "ths",
        "title": "乙公司", "text": "重组+机器人", "stock_codes": ["600001"],
        "industries": [], "publish_ts": "2026-08-30T22:00:00",
        "pit_certified": False,
        "raw": {"code": "600001", "name": "乙公司", "first_time": "10:31:02",
                "reason": "重组+机器人", "high_days": "首板"},
    }
    envelopes = [
        {"ok": True, "source": "em_limit_up_pool", "date": "20260828",
         "status": "ok", "items": [em], "fetched_ts": "2026-08-30T22:00:00"},
        {"ok": True, "source": "ths_limit_up_pool", "date": "20260828",
         "status": "ok", "items": [ths], "fetched_ts": "2026-08-30T22:00:00"},
    ]
    out = ingest.ingest_live_envelopes(
        conn, envelopes, now=datetime(2026, 8, 30, 22, 0, 1),
    )
    assert out["inserted"] == 2
    got = {row["source_id"]: dict(row) for row in conn.execute(
        "SELECT * FROM normalized_events ORDER BY source_id"
    )}
    assert got["em_limit_up_pool"]["event_time"] == "2026-08-28T09:25:00"
    assert got["ths_limit_up_pool"]["event_time"] == "2026-08-28T10:31:02"
    assert got["ths_limit_up_pool"]["event_type"] == "limit_up_reason"
    assert got["em_limit_up_pool"]["pit_available_at"] == "2026-08-30T22:00:01"
    conn.close()


def test_live_availability_never_predates_provider_fetch_receipt(tmp_path):
    conn = _db(tmp_path)
    item = {
        "title": "甲公司", "text": "热点原因", "stock_codes": ["000001"],
        "raw": {"id": "hot-1", "date": "2026-08-28", "code": "000001"},
    }
    ingest.ingest_live_envelopes(conn, [{
        "ok": True, "source": "ths_hot_reason", "date": "2026-08-28",
        "status": "ok", "items": [item],
        "fetched_ts": "2026-08-30T22:00:32",
    }], now=datetime(2026, 8, 30, 22, 0, 0))
    row = conn.execute("SELECT * FROM normalized_events").fetchone()
    assert row["source_asof"] == "2026-08-30T22:00:32"
    assert row["pit_available_at"] == "2026-08-30T22:00:32"
    conn.close()


def test_on_demand_announcements_are_bounded_to_requested_issuers(tmp_path):
    conn = _db(tmp_path)
    seen = []

    def probe_many(requests):
        seen.extend(requests)
        out = []
        for request in requests:
            bare = request["code"][-6:]
            out.append({
                **_announcement_envelope(items=[{
                    "title": f"公告-{bare}", "publish_ts": "2026-08-29T00:00:00",
                    "stock_codes": [request["code"]], "url": f"https://example/{bare}.pdf",
                    "raw": {"announcementId": f"ann-{bare}"},
                }]),
                "code": bare,
            })
        return out

    out = ingest.ingest_live_announcements(
        conn, ["600519", "SZ000001"], probe_many=probe_many,
        now=datetime(2026, 8, 30, 12, 0, 0),
    )
    assert [request["code"] for request in seen] == ["SH600519", "SZ000001"]
    assert all(request["source"] == "cninfo_announcements" for request in seen)
    assert out["inserted"] == 2
    assert set(out["sources"]) == {
        "cninfo_announcements:SH600519", "cninfo_announcements:SZ000001",
    }
    conn.close()


def test_default_tick_isolates_each_source_failure(tmp_path, monkeypatch):
    path = tmp_path / "events.db"
    called = []
    real_connect = store.connect
    monkeypatch.setattr(store, "connect", lambda: real_connect(path))
    monkeypatch.setattr(
        ingest, "ingest_kuaixun",
        lambda conn: (called.append("kuaixun"), (_ for _ in ()).throw(RuntimeError("down")))[1],
    )
    monkeypatch.setattr(
        ingest, "ingest_newsradar",
        lambda conn: called.append("newsradar") or {"inserted": 0},
    )
    monkeypatch.setattr(
        ingest, "ingest_live_market_events",
        lambda conn: called.append("market") or {"inserted": 0},
    )
    monkeypatch.delenv("GUANLAN_EVENTS_ISSUER_CODES", raising=False)

    result = ingest._default_tick_ingest(datetime(2026, 8, 30, 12, 0, 0))
    assert called == ["kuaixun", "newsradar", "market"]
    assert result["kuaixun"]["status"] == "failed"
    assert result["newsradar"]["status"] == "completed"
    assert result["live_market_events"]["status"] == "completed"
    assert result["live_announcements"]["status"] == "not_requested"
