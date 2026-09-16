# -*- coding: utf-8 -*-
"""市场线索聚合保持候选语义，并隔离未来计划与旧数据时间冲突。"""
from __future__ import annotations

import json
import sqlite3

import pytest

from guanlan_v2.events.clues import market_clues


ASOF = "2026-08-30T12:00:00"
CATALOG = {
    "catalog_asof": "2026-08-01T00:00:00",
    "entities": [
        {"id": "SZ300750", "name": "宁德时代", "industry": "battery"},
    ],
    "industries": [
        {"id": "battery", "name": "锂电池", "aliases": ["固态电池", "锂电"]},
    ],
}


def _conn(tmp_path) -> sqlite3.Connection:
    conn = sqlite3.connect(tmp_path / "events.db")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
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
            ingested_from TEXT,
            time_classification TEXT,
            classification_reason TEXT,
            provenance_json TEXT,
            payload_json TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE event_relations (
            event_id TEXT NOT NULL,
            relation_type TEXT NOT NULL,
            target_id TEXT NOT NULL,
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
    conn.commit()
    return conn


def _insert(conn: sqlite3.Connection, event_id: str, **overrides) -> None:
    row = {
        "event_id": event_id,
        "event_type": "news",
        "tickers": "[]",
        "title": event_id,
        "summary": "",
        "source": "wire-a",
        "source_ref": f"ref:{event_id}",
        "event_time": "2026-08-30T09:00:00",
        "available_at": "2026-08-30T10:00:00",
        "invalidated_at": None,
        "simhash": None,
        "line_id": event_id,
        "ingested_from": "kuaixun",
        "time_classification": "observed",
        "classification_reason": "source_time_not_future",
        "provenance_json": json.dumps({"fixture": event_id}, ensure_ascii=False),
        "payload_json": None,
    }
    row.update(overrides)
    cols = list(row)
    conn.execute(
        f"INSERT INTO events ({','.join(cols)}) VALUES ({','.join('?' for _ in cols)})",
        [row[c] for c in cols],
    )
    conn.commit()


def _seed(conn: sqlite3.Connection) -> None:
    _insert(conn, "limit-a", event_type="limit_up", line_id="line-limit",
            title="宁德时代涨停原因：固态电池突破", source="wire-a",
            available_at="2026-08-30T10:05:00")
    _insert(conn, "limit-b", event_type="news", line_id="line-limit",
            title="转载：宁德时代因固态电池概念封板", source="wire-b",
            available_at="2026-08-30T10:04:00")
    _insert(conn, "risk", event_type="risk_warning", line_id="line-risk",
            title="宁德时代收到问询函并发布风险提示",
            available_at="2026-08-30T10:03:00")
    _insert(conn, "macro", line_id="line-macro", title="央行宣布降准",
            available_at="2026-08-30T10:02:00")
    _insert(conn, "conflict", event_type="limit_up_risk", line_id="line-conflict",
            title="宁德时代涨停同时被立案调查",
            time_classification="time_conflict",
            classification_reason="legacy_future_time_after_snapshot",
            available_at="2026-08-30T10:01:00")
    _insert(conn, "scheduled", line_id="line-scheduled",
            title="宁德时代将召开固态电池发布会",
            event_time="2026-09-10T09:00:00",
            time_classification="scheduled",
            classification_reason="explicit_calendar_semantics",
            available_at="2026-08-30T10:00:00")
    _insert(conn, "future-known", event_type="risk", title="未来才入库的风险",
            available_at="2026-08-30T12:00:00.000001")
    _insert(conn, "invalidated", event_type="risk", title="已撤回的立案消息",
            available_at="2026-08-30T09:00:00",
            invalidated_at="2026-08-30T11:00:00")


def test_market_clues_builds_all_four_auditable_candidate_views(tmp_path):
    conn = _conn(tmp_path)
    _seed(conn)

    out = market_clues(ASOF, conn=conn, catalog=CATALOG, window_days=7, limit=100)
    assert out["ok"] is True
    assert out["events_considered"] == 6  # future-known/invalidated 被 PIT 裁掉
    assert out["eligible_events"] == 4    # conflict/scheduled 从四类聚合隔离
    assert out["hotspots"] and out["hotspots"][0]["industry_id"] == "battery"
    assert out["hotspots"][0]["unlinked"] is False

    line = next(x for x in out["event_lines"] if x["line_id"] == "line-limit")
    assert line["event_count"] == 2 and line["is_reprint_line"] is True
    assert line["unlinked"] is False
    assert {x["event_id"] for x in line["evidence"]} == {"limit-a", "limit-b"}

    limit = out["limit_up_reasons"]
    risk = out["risk_clues"]
    assert len(limit) == 1 and limit[0]["line_id"] == "line-limit"
    assert len(risk) == 1 and risk[0]["event_id"] == "risk"
    for clue in [*limit, *risk]:
        assert clue["relation_status"] == "candidate"
        assert clue["confidence"] > 0
        assert clue["evidence"]
        assert clue["issuer_fact"] is False
        assert clue["provenance"]["event_id"] == clue["event_id"]
    assert "关键词命中不会自动升级" in out["honesty_note"]


def test_hotspot_score_deduplicates_reprints_but_keeps_source_breadth(tmp_path):
    conn = _conn(tmp_path)
    _insert(conn, "copy-a", line_id="same-line", title="固态电池取得突破",
            source="wire-a", available_at="2026-08-30T10:05:00")
    _insert(conn, "copy-b", line_id="same-line", title="转载：固态电池取得突破",
            source="wire-b", available_at="2026-08-30T10:04:00")

    out = market_clues(ASOF, conn=conn, catalog=CATALOG, limit=10)
    hotspot = out["hotspots"][0]
    assert hotspot["line_count"] == 1
    assert hotspot["event_count"] == 2
    assert hotspot["source_count"] == 2
    assert hotspot["score"] == 0.5  # 一条事件线只计一次，不因转载翻倍


def test_event_line_evidence_is_bounded_and_reports_truncation(tmp_path):
    conn = _conn(tmp_path)
    for index in range(25):
        _insert(conn, f"copy-{index:02d}", line_id="large-line",
                title="央行公开市场操作", source=f"wire-{index:02d}",
                available_at=f"2026-08-30T10:{index:02d}:00")

    out = market_clues(ASOF, conn=conn, catalog=CATALOG, limit=10)
    line = next(item for item in out["event_lines"] if item["line_id"] == "large-line")
    assert line["evidence_count"] == 25
    assert len(line["evidence"]) == 20
    assert line["evidence_truncated"] is True
    assert line["relation_status"] == "unlinked" and line["unlinked"] is True


def test_unlinked_macro_is_kept_in_event_lines_but_not_made_an_issuer_reason(tmp_path):
    conn = _conn(tmp_path)
    _seed(conn)

    out = market_clues(ASOF, conn=conn, catalog=CATALOG)
    macro = next(x for x in out["event_lines"] if x["line_id"] == "line-macro")
    assert macro["relation_status"] == "unlinked"
    assert macro["representative"]["unlinked"] is True
    assert all(x["event_id"] != "macro" for x in out["limit_up_reasons"])
    assert all(x["event_id"] != "macro" for x in out["risk_clues"])


def test_catalog_newer_than_asof_is_excluded_from_all_market_clue_relations(tmp_path):
    conn = _conn(tmp_path)
    _insert(
        conn, "future-catalog", title="固态电池风险提示",
        event_type="risk_warning", line_id="future-catalog-line",
        available_at="2026-08-30T10:00:00",
    )
    future_catalog = {
        "catalog_asof": "2026-09-01T00:00:00",
        "industries": [
            {"id": "battery", "name": "锂电池", "aliases": ["固态电池"]},
        ],
    }

    out = market_clues(ASOF, conn=conn, catalog=future_catalog, limit=10)

    assert out["hotspots"] == []
    line = out["event_lines"][0]
    assert line["relation_status"] == "unlinked" and line["unlinked"] is True
    risk = out["risk_clues"][0]
    assert risk["relation_status"] == "unlinked" and risk["unlinked"] is True
    assert risk["evidence"] == []


def test_time_conflict_is_quarantined_and_scheduled_is_separate(tmp_path):
    conn = _conn(tmp_path)
    _seed(conn)

    out = market_clues(ASOF, conn=conn, catalog=CATALOG)
    assert [x["event_id"] for x in out["quarantined"]] == ["conflict"]
    assert out["quarantined"][0]["quarantine_reason"] == "legacy_future_time_after_snapshot"
    assert [x["event_id"] for x in out["scheduled"]] == ["scheduled"]

    aggregated_ids = {
        *(x["event_id"] for h in out["hotspots"] for x in h["evidence"]),
        *(x["representative"]["event_id"] for x in out["event_lines"]),
        *(x["event_id"] for x in out["limit_up_reasons"]),
        *(x["event_id"] for x in out["risk_clues"]),
    }
    assert "conflict" not in aggregated_ids
    assert "scheduled" not in aggregated_ids


def test_market_clues_requires_asof_and_returns_stable_error_boundary(tmp_path):
    conn = _conn(tmp_path)
    _seed(conn)

    out = market_clues(None, conn=conn, catalog=CATALOG)
    assert out["ok"] is False
    assert out["error"]["code"] == "invalid_asof"
    assert out["hotspots"] == [] and out["event_lines"] == []
    assert out["limit_up_reasons"] == [] and out["risk_clues"] == []
    assert out["issuer_fact"] is False


@pytest.mark.parametrize(
    ("kwargs", "code"),
    [
        ({"window_days": True}, "invalid_window_days"),
        ({"window_days": "many"}, "invalid_window_days"),
        ({"window_days": 0}, "invalid_window_days"),
        ({"window_days": 366}, "invalid_window_days"),
        ({"limit": 501}, "invalid_limit"),
    ],
)
def test_market_clue_bounds_return_stable_errors(tmp_path, kwargs, code):
    conn = _conn(tmp_path)
    out = market_clues(ASOF, conn=conn, catalog=CATALOG, **kwargs)
    assert out["ok"] is False and out["error"]["code"] == code


@pytest.mark.parametrize(
    ("kwargs", "code"),
    [
        ({"industries": [f"industry-{index}" for index in range(51)]},
         "too_many_filter_values"),
        ({"entities": [f"entity-{index}" for index in range(51)]},
         "too_many_filter_values"),
        ({"industry": "i" * 201}, "filter_value_too_long"),
        ({"entity": "e" * 201}, "filter_value_too_long"),
    ],
)
def test_market_clue_filter_bounds_fail_before_sql(kwargs, code):
    class NoSqlConnection:
        calls = 0

        def execute(self, *_args, **_kwargs):
            self.calls += 1
            raise AssertionError("filter validation must finish before SQL")

    conn = NoSqlConnection()
    out = market_clues(ASOF, conn=conn, catalog=CATALOG, **kwargs)
    assert out["ok"] is False and out["error"]["code"] == code
    assert conn.calls == 0


def test_market_clues_readonly_open_failure_does_not_create_db(tmp_path):
    missing = tmp_path / "absent" / "events.db"
    out = market_clues(ASOF, path=missing, catalog=CATALOG)
    assert out["error"] == {
        "code": "events_db_unavailable", "message": "events.db 无法以只读方式打开",
    }
    assert not missing.exists()


def test_market_clues_internal_error_is_fixed_and_non_disclosing(monkeypatch):
    import guanlan_v2.events.clues as clues

    private_error = r"C:\private\events.db token=secret"
    monkeypatch.setattr(
        clues, "_collect",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError(private_error)),
    )
    out = clues.market_clues(ASOF)
    assert out["error"] == {
        "code": "market_clues_internal_error", "message": "市场线索聚合失败",
    }
    assert private_error not in repr(out)
