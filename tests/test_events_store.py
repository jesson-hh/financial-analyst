# -*- coding: utf-8 -*-
"""events.store schema + 冻结 PIT 谓词 + FTS 检索单测(全 tmp db,绝不碰真 var/)。

分层:Task 1 = schema/WAL/幂等/PIT 三态;Task 4 = jieba 预分词 FTS5 + asof 必填 + LIKE 降级。
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta

import pytest

from guanlan_v2.events import store


def _open(tmp_path):
    return store.connect(path=tmp_path / "events" / "events.db")


# ───────────────────────── Task 1: schema / PIT ─────────────────────────

def test_db_path_param_and_env_injection(tmp_path, monkeypatch):
    p = tmp_path / "a" / "events.db"
    assert store.db_path(path=p) == p                      # 参数优先
    monkeypatch.setenv("GUANLAN_EVENTS_DB", str(tmp_path / "b" / "events.db"))
    assert store.db_path() == (tmp_path / "b" / "events.db")   # env 次之
    monkeypatch.delenv("GUANLAN_EVENTS_DB", raising=False)
    d = store.db_path()                                    # 默认落 repo var/events(不实际建)
    assert d.name == "events.db" and d.parent.name == "events" and d.parent.parent.name == "var"


def test_init_db_idempotent_and_wal(tmp_path):
    conn = _open(tmp_path)
    store.init_db(conn)
    store.init_db(conn)                                    # 重复建库不炸不重建
    assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
    cols = {r[1] for r in conn.execute("PRAGMA table_info(events)")}
    assert {"event_id", "event_type", "tickers", "title", "summary", "source",
            "source_ref", "event_time", "available_at", "invalidated_at",
            "simhash", "line_id", "ingested_from"} <= cols
    conn.close()


def test_available_at_not_null_constraint(tmp_path):
    conn = _open(tmp_path)
    store.init_db(conn)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO events(event_id, event_type, available_at) "
                     "VALUES('x', 't', NULL)")
    conn.close()


def test_init_db_preserves_existing_rows(tmp_path):
    conn = _open(tmp_path)
    store.init_db(conn)
    store.insert_event(conn, dict(event_id="e1", event_type="t",
                                  available_at="2026-07-18T09:00:00", title="hi"))
    conn.commit()
    store.init_db(conn)                                    # 幂等重入不清行
    assert conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 1
    conn.close()


def test_insert_event_idempotent_on_event_id(tmp_path):
    conn = _open(tmp_path)
    store.init_db(conn)
    row = dict(event_id="dup", event_type="t", available_at="2026-07-18T09:00:00", title="x")
    assert store.insert_event(conn, row) is True
    assert store.insert_event(conn, row) is False          # 同 event_id 重入不重复
    conn.commit()
    assert conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 1
    conn.close()


def test_pit_predicate_invalidated_tri_state(tmp_path):
    conn = _open(tmp_path)
    store.init_db(conn)
    asof = "2026-07-18T12:00:00"
    store.insert_event(conn, dict(event_id="a", event_type="t",
                                  available_at="2026-07-18T09:00:00",
                                  invalidated_at=None, title="A"))            # NULL → 可见
    store.insert_event(conn, dict(event_id="b", event_type="t",
                                  available_at="2026-07-18T09:00:00",
                                  invalidated_at="2026-07-18T10:00:00", title="B"))  # 早于 asof → 隐
    store.insert_event(conn, dict(event_id="c", event_type="t",
                                  available_at="2026-07-18T09:00:00",
                                  invalidated_at="2026-07-18T20:00:00", title="C"))  # 晚于 asof → 可见
    conn.commit()
    ids = {r["event_id"] for r in store.read_events(conn, asof=asof)}
    assert ids == {"a", "c"}
    conn.close()


def test_pit_available_at_horizon(tmp_path):
    conn = _open(tmp_path)
    store.init_db(conn)
    store.insert_event(conn, dict(event_id="past", event_type="t",
                                  available_at="2026-07-18T09:00:00", title="past"))
    store.insert_event(conn, dict(event_id="future", event_type="t",
                                  available_at="2026-07-18T15:00:00", title="future"))
    conn.commit()
    ids = {r["event_id"] for r in store.read_events(conn, asof="2026-07-18T12:00:00")}
    assert ids == {"past"}                                 # 晚于 asof 入库者不可见(无前视)
    conn.close()


def test_read_events_requires_asof(tmp_path):
    conn = _open(tmp_path)
    store.init_db(conn)
    with pytest.raises((ValueError, TypeError)):
        store.read_events(conn, asof=None)
    conn.close()


def _seed_relation_event(conn):
    store.insert_event(conn, {
        "event_id": "relation-event", "event_type": "news",
        "available_at": "2026-08-30T08:00:00", "title": "关联回放",
    })


def test_event_relation_reviews_append_and_replay_at_three_asofs(tmp_path):
    conn = _open(tmp_path)
    store.init_db(conn)
    _seed_relation_event(conn)
    candidate = {
        "relation_id": "candidate-1", "event_id": "relation-event",
        "relation_type": "entity", "target_id": "SH600000",
        "target_name": "浦发银行", "relation_status": "candidate",
        "confidence": 0.6, "created_at": "2026-08-30T09:00:00",
    }
    confirmed = {
        "relation_id": "confirmed-1", "event_id": "relation-event",
        "relation_type": "entity", "target_id": "SH600000",
        "target_name": "浦发银行", "relation_status": "confirmed",
        "confidence": 0.99, "created_at": "2026-08-30T09:00:00",
        "reviewed_by": "analyst-a", "reviewed_at": "2026-08-30T10:00:00",
    }
    rejected = {
        "relation_id": "rejected-1", "event_id": "relation-event",
        "relation_type": "entity", "target_id": "SH600000",
        "target_name": "浦发银行", "relation_status": "rejected",
        "confidence": 0.0, "created_at": "2026-08-30T09:00:00",
        "reviewed_by": "analyst-b", "reviewed_at": "2026-08-30T11:00:00",
    }
    assert store.insert_event_relation(conn, candidate) is True
    assert store.insert_event_relation(conn, candidate) is False
    with pytest.raises(ValueError, match="different immutable payload"):
        store.insert_event_relation(conn, {**candidate, "confidence": 0.7})
    assert store.insert_event_relation(conn, confirmed) is True
    assert store.insert_event_relation(conn, rejected) is True

    def status_at(asof):
        rows = store.read_event_relations(
            conn, asof, event_id="relation-event",
        )
        assert len(rows) == 1
        return rows[0]["relation_status"], rows[0]["effective_at"]

    assert status_at("2026-08-30T09:30:00") == (
        "candidate", "2026-08-30T09:00:00",
    )
    assert status_at("2026-08-30T10:30:00") == (
        "confirmed", "2026-08-30T10:00:00",
    )
    assert status_at("2026-08-30T11:30:00") == (
        "rejected", "2026-08-30T11:00:00",
    )
    assert len(store.read_event_relations(
        conn, "2026-08-30T11:30:00", event_id="relation-event", collapse=False,
    )) == 3

    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        conn.execute("UPDATE event_relations SET confidence=0.1 WHERE relation_id='candidate-1'")
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        conn.execute("DELETE FROM event_relations WHERE relation_id='candidate-1'")
    assert conn.execute("SELECT COUNT(*) FROM event_relations").fetchone()[0] == 3

    rootpage = conn.execute(
        "SELECT rootpage FROM sqlite_master WHERE type='table' AND name='event_relations'"
    ).fetchone()[0]
    trigger_sql = list(conn.execute(
        "SELECT name,sql FROM sqlite_master WHERE type='trigger' "
        "AND tbl_name='event_relations' ORDER BY name"
    ))
    relation_rows = list(conn.execute(
        "SELECT * FROM event_relations ORDER BY relation_id"
    ))
    before_changes = conn.total_changes
    store.init_db(conn)
    assert conn.execute(
        "SELECT rootpage FROM sqlite_master WHERE type='table' AND name='event_relations'"
    ).fetchone()[0] == rootpage
    assert list(conn.execute(
        "SELECT name,sql FROM sqlite_master WHERE type='trigger' "
        "AND tbl_name='event_relations' ORDER BY name"
    )) == trigger_sql
    assert list(conn.execute(
        "SELECT * FROM event_relations ORDER BY relation_id"
    )) == relation_rows
    assert conn.total_changes == before_changes
    conn.close()


def test_event_relation_rejects_ambiguous_future_and_invalid_review_times(tmp_path):
    conn = _open(tmp_path)
    store.init_db(conn)
    _seed_relation_event(conn)
    base = {
        "relation_id": "base", "event_id": "relation-event",
        "relation_type": "entity", "target_id": "SH600000",
        "relation_status": "candidate", "created_at": "2026-08-30T09:00:00",
    }
    assert store.insert_event_relation(conn, base) is True

    for index, ambiguous in enumerate(("2026-08-30", "2026-08-30Z",
                                       "2026-08-30+08:00")):
        with pytest.raises(ValueError, match="include a time"):
            store.insert_event_relation(conn, {
                **base, "relation_id": f"date-only-{index}",
                "created_at": ambiguous,
            })
    future = datetime.now(store.HONG_KONG) + timedelta(days=1)
    with pytest.raises(ValueError, match="future"):
        store.insert_event_relation(conn, {**base, "relation_id": "future",
                                            "created_at": future.isoformat()})
    review = {
        **base, "relation_id": "review", "relation_status": "confirmed",
        "reviewed_by": "analyst", "reviewed_at": "2026-08-30T10:00:00",
    }
    with pytest.raises(ValueError, match="reviewed_by"):
        store.insert_event_relation(conn, {**review, "relation_id": "no-reviewer",
                                            "reviewed_by": None})
    with pytest.raises(ValueError, match="reviewed_at"):
        store.insert_event_relation(conn, {**review, "relation_id": "no-time",
                                            "reviewed_at": None})
    with pytest.raises(ValueError, match="cannot precede"):
        store.insert_event_relation(conn, {**review, "relation_id": "backdated",
                                            "created_at": "2026-08-30T10:30:00"})
    with pytest.raises(ValueError, match="review metadata"):
        store.insert_event_relation(conn, {**base, "relation_id": "mutated-base",
                                            "reviewed_by": "analyst",
                                            "reviewed_at": "2026-08-30T10:00:00"})
    with pytest.raises(ValueError, match="existing target"):
        store.insert_event_relation(conn, {**review, "relation_id": "unknown-target",
                                            "target_id": "SZ000001"})
    review_without_created = {**review, "relation_id": "review-default-created"}
    review_without_created.pop("created_at")
    assert store.insert_event_relation(conn, review_without_created) is True
    stored_times = conn.execute(
        "SELECT created_at,reviewed_at FROM event_relations WHERE relation_id=?",
        ("review-default-created",),
    ).fetchone()
    assert tuple(stored_times) == (
        "2026-08-30T10:00:00", "2026-08-30T10:00:00",
    )
    conn.close()


def test_read_event_relations_also_obeys_parent_event_pit_window(tmp_path):
    conn = _open(tmp_path)
    store.init_db(conn)
    store.insert_event(conn, {
        "event_id": "pit-parent", "event_type": "news",
        "available_at": "2026-08-30T10:00:00",
        "invalidated_at": "2026-08-30T11:00:00",
    })
    store.insert_event_relation(conn, {
        "relation_id": "pit-relation", "event_id": "pit-parent",
        "relation_type": "industry", "target_id": "bank",
        "relation_status": "source_asserted", "created_at": "2026-08-30T09:00:00",
    })
    assert store.read_event_relations(
        conn, "2026-08-30T09:30:00", event_id="pit-parent",
    ) == []
    assert [row["relation_id"] for row in store.read_event_relations(
        conn, "2026-08-30T10:30:00", event_id="pit-parent",
    )] == ["pit-relation"]
    assert store.read_event_relations(
        conn, "2026-08-30T11:00:00", event_id="pit-parent",
    ) == []
    conn.close()


# ───────────────────────── Task 4: jieba 预分词 FTS5 检索 ─────────────────────────

def _seed_fts(conn):
    store.insert_event(conn, dict(event_id="a", event_type="kuaixun",
                                  available_at="2026-07-18T09:00:00",
                                  title="央行宣布全面降准", summary="释放流动性支持实体经济"))
    store.insert_event(conn, dict(event_id="b", event_type="kuaixun",
                                  available_at="2026-07-18T09:00:00",
                                  title="某公司发布人工智能芯片", summary="性能大幅提升"))
    conn.commit()


def test_backfill_fts_repairs_duplicate_and_orphan_balanced_gaps(tmp_path):
    conn = _open(tmp_path)
    store.init_db(conn)
    if not store._fts_exists(conn):
        pytest.skip("SQLite build has no FTS5")
    store.insert_event(conn, {
        "event_id": "fts-a", "event_type": "news",
        "available_at": "2026-08-30T09:00:00", "title": "甲事件",
    })
    store.insert_event(conn, {
        "event_id": "fts-b", "event_type": "news",
        "available_at": "2026-08-30T09:01:00", "title": "乙事件",
    })

    # Equal raw counts used to look healthy even though one base id was absent.
    conn.execute("DELETE FROM events_fts")
    conn.executemany(
        "INSERT INTO events_fts(tokens,event_id) VALUES(?,?)",
        (("甲", "fts-a"), ("重复甲", "fts-a")),
    )
    store.backfill_fts(conn)
    assert [tuple(row) for row in conn.execute(
        "SELECT event_id,count(*) FROM events_fts GROUP BY event_id ORDER BY event_id"
    )] == [("fts-a", 1), ("fts-b", 1)]

    # DISTINCT count alone is also insufficient when an orphan replaces a base id.
    conn.execute("DELETE FROM events_fts")
    conn.executemany(
        "INSERT INTO events_fts(tokens,event_id) VALUES(?,?)",
        (("甲", "fts-a"), ("幽灵", "ghost")),
    )
    store.backfill_fts(conn)
    assert [row[0] for row in conn.execute(
        "SELECT event_id FROM events_fts ORDER BY event_id"
    )] == ["fts-a", "fts-b"]
    conn.close()


def test_search_chinese_subword_hit(tmp_path):
    conn = _open(tmp_path)
    store.init_db(conn)
    _seed_fts(conn)
    out = store.search_events("降准", asof="2026-07-18T12:00:00", conn=conn)
    assert out["fts"] == "ok"
    assert {i["event_id"] for i in out["items"]} == {"a"}       # 分词后子词命中
    out2 = store.search_events("人工智能", asof="2026-07-18T12:00:00", conn=conn)
    assert {i["event_id"] for i in out2["items"]} == {"b"}
    conn.close()


def test_search_requires_asof(tmp_path):
    conn = _open(tmp_path)
    store.init_db(conn)
    with pytest.raises((ValueError, TypeError)):
        store.search_events("降准", asof=None, conn=conn)        # asof 缺则炸(前视温床)
    conn.close()


def test_jieba_query_first_use_does_not_create_temp_cache(monkeypatch):
    import jieba

    store._memory_jieba_tokenizer.cache_clear()

    def forbidden_mkstemp(*_args, **_kwargs):
        raise AssertionError("read-only query attempted a jieba temp cache write")

    monkeypatch.setattr(jieba.tempfile, "mkstemp", forbidden_mkstemp)
    assert "降准" in store._jieba_query("央行降准")


def test_search_pit_asof_filter(tmp_path):
    conn = _open(tmp_path)
    store.init_db(conn)
    store.insert_event(conn, dict(event_id="past", event_type="kuaixun",
                                  available_at="2026-07-18T09:00:00", title="央行降准", summary=""))
    store.insert_event(conn, dict(event_id="future", event_type="kuaixun",
                                  available_at="2026-07-18T15:00:00", title="央行降准", summary=""))
    conn.commit()
    out = store.search_events("降准", asof="2026-07-18T12:00:00", conn=conn)
    assert {i["event_id"] for i in out["items"]} == {"past"}    # 晚于 asof 入库不可见
    conn.close()


def test_search_like_degrade_when_jieba_broken(tmp_path, monkeypatch):
    conn = _open(tmp_path)
    store.init_db(conn)
    _seed_fts(conn)

    def _broken(_text):
        raise ImportError("jieba unavailable (forced)")

    monkeypatch.setattr(store, "_jieba_tokens", _broken)        # 强制降级(不靠卸载依赖)
    out = store.search_events("降准", asof="2026-07-18T12:00:00", conn=conn)
    assert out["fts"] == "unavailable"                          # 诚实标注,绝不静默装好
    assert {i["event_id"] for i in out["items"]} == {"a"}       # LIKE 仍命中
    conn.close()


def test_search_limit(tmp_path):
    conn = _open(tmp_path)
    store.init_db(conn)
    for i in range(5):
        store.insert_event(conn, dict(event_id=f"e{i}", event_type="kuaixun",
                                      available_at=f"2026-07-18T09:0{i}:00",
                                      title="央行宣布全面降准", summary="流动性"))
    conn.commit()
    out = store.search_events("降准", asof="2026-07-18T12:00:00", limit=2, conn=conn)
    assert out["fts"] == "ok" and len(out["items"]) == 2
    conn.close()


def test_search_tickers_decoded_in_payload(tmp_path):
    conn = _open(tmp_path)
    store.init_db(conn)
    store.insert_event(conn, dict(event_id="a", event_type="kuaixun",
                                  available_at="2026-07-18T09:00:00",
                                  title="央行降准", summary="", tickers=["SH600000"]))
    conn.commit()
    out = store.search_events("降准", asof="2026-07-18T12:00:00", conn=conn)
    assert out["items"][0]["tickers"] == ["SH600000"]           # payload tickers 反序列化为 list
