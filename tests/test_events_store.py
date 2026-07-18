# -*- coding: utf-8 -*-
"""events.store schema + 冻结 PIT 谓词 + FTS 检索单测(全 tmp db,绝不碰真 var/)。

分层:Task 1 = schema/WAL/幂等/PIT 三态;Task 4 = jieba 预分词 FTS5 + asof 必填 + LIKE 降级。
"""
from __future__ import annotations

import sqlite3

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


# ───────────────────────── Task 4: jieba 预分词 FTS5 检索 ─────────────────────────

def _seed_fts(conn):
    store.insert_event(conn, dict(event_id="a", event_type="kuaixun",
                                  available_at="2026-07-18T09:00:00",
                                  title="央行宣布全面降准", summary="释放流动性支持实体经济"))
    store.insert_event(conn, dict(event_id="b", event_type="kuaixun",
                                  available_at="2026-07-18T09:00:00",
                                  title="某公司发布人工智能芯片", summary="性能大幅提升"))
    conn.commit()


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
