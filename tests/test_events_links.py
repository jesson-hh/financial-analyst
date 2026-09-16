# -*- coding: utf-8 -*-
"""事件到实体/行业关系只能形成可审计候选，显式复核才可改变关系状态。"""
from __future__ import annotations

import json
import sqlite3

from guanlan_v2.events.relations import link_event_candidates, relation_matches


CATALOG = {
    "catalog_asof": "2026-08-01T00:00:00",
    "entities": [
        {
            "id": "SZ300750",
            "name": "宁德时代",
            "aliases": ["宁王"],
            "industry": "battery",
        },
        {
            "id": "SZ300014",
            "name": "亿纬锂能",
            "aliases": ["锂能龙头"],
            "industry": "battery",
        },
    ],
    "industries": [
        {"id": "battery", "name": "锂电池", "aliases": ["固态电池", "锂电"]},
    ],
}


def _event(**overrides):
    row = {
        "event_id": "ev-1",
        "event_type": "news",
        "tickers": [],
        "title": "",
        "summary": "",
        "source": "test",
        "available_at": "2026-08-30T09:00:00.000000",
    }
    row.update(overrides)
    return row


def _review_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
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
            created_at TEXT,
            reviewed_by TEXT,
            reviewed_at TEXT
        )
        """
    )
    return conn


def _review(conn: sqlite3.Connection, *, status: str, target_id: str = "SZ300750") -> None:
    conn.execute(
        "INSERT INTO event_relations VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (
            "ev-1",
            "entity",
            target_id,
            "宁德时代",
            status,
            0.98 if status == "confirmed" else 0.0,
            json.dumps([{"kind": "human_review", "ticket": "rv-7"}], ensure_ascii=False),
            "explicit_review",
            "2026-08-30T09:00:00",
            "analyst-a",
            "2026-08-30T10:00:00",
        ),
    )
    conn.commit()


def test_keyword_hits_remain_candidates_never_issuer_facts():
    linked = link_event_candidates(
        _event(title="宁德时代宣布固态电池新进展"),
        catalog=CATALOG,
    )

    entity = next(r for r in linked["relations"] if r["relation_type"] == "entity")
    industry = next(r for r in linked["relations"] if r["target_id"] == "battery"
                    and r["relation_method"] == "industry_alias_match")
    assert entity["relation_status"] == "candidate"
    assert industry["relation_status"] == "candidate"
    assert linked["relation_status"] == "candidate"
    assert linked["issuer_fact"] is False and entity["issuer_fact"] is False
    assert any(e.get("rule") == linked["relation_policy"] for e in linked["evidence"])


def test_no_match_is_preserved_as_explicit_unlinked_state():
    linked = link_event_candidates(_event(title="央行开展公开市场操作"), catalog=CATALOG)

    assert linked["relations"] == []
    assert linked["relation_status"] == "unlinked"
    assert linked["confidence"] == 0.0
    assert linked["evidence"] == []
    assert linked["unlinked"] is True and linked["issuer_fact"] is False
    assert relation_matches(linked) is True
    assert relation_matches(linked, entities=("SZ300750",)) is False


def test_structured_ticker_is_source_asserted_link_not_an_issuer_fact():
    linked = link_event_candidates(
        _event(title="供应链消息", tickers=["SZ300750"]),
        catalog=CATALOG,
    )

    relation = next(r for r in linked["relations"] if r["relation_type"] == "entity")
    assert relation["relation_status"] == "source_asserted"
    assert relation["relation_method"] == "structured_ticker_field"
    assert relation["evidence"][0]["claim_scope"] == "event_link_only"
    assert relation["issuer_fact"] is False and linked["issuer_fact"] is False


def test_explicit_review_is_the_only_path_to_confirmed_relation():
    conn = _review_conn()
    _review(conn, status="confirmed")

    linked = link_event_candidates(
        _event(title="宁德时代宣布固态电池新进展"),
        conn=conn,
        catalog=CATALOG,
    )

    reviewed = next(r for r in linked["relations"]
                    if r["target_id"] == "SZ300750"
                    and r["relation_status"] == "confirmed")
    assert linked["relation_status"] == "confirmed"
    assert reviewed["relation_method"] == "explicit_review"
    assert reviewed["review"] == {
        "reviewed_by": "analyst-a",
        "reviewed_at": "2026-08-30T10:00:00",
    }
    assert reviewed["evidence"][0]["ticket"] == "rv-7"
    # confirmed 只确认“事件与实体的关系”，不确认标题中的事件主张为真。
    assert reviewed["issuer_fact"] is False and linked["issuer_fact"] is False


def test_explicit_rejection_suppresses_same_target_lexical_candidate():
    conn = _review_conn()
    _review(conn, status="rejected")

    linked = link_event_candidates(
        _event(title="宁德时代宣布固态电池新进展"),
        conn=conn,
        # 不给实体行业，避免另一个独立行业候选影响 top-level unlinked 裁决。
        catalog={"entities": [{"id": "SZ300750", "name": "宁德时代"}]},
    )

    same_target = [r for r in linked["relations"] if r["target_id"] == "SZ300750"]
    assert [r["relation_status"] for r in same_target] == ["rejected"]
    assert linked["relation_status"] == "rejected"
    assert linked["unlinked"] is False
    assert relation_matches(linked, entities=("SZ300750",)) is False


def test_relation_filters_match_ids_and_names_but_not_rejected_rows():
    linked = link_event_candidates(
        _event(title="宁王的锂电供应链扩产"),
        catalog=CATALOG,
    )
    assert relation_matches(linked, entities=("SZ300750",)) is True
    assert relation_matches(linked, entities=("宁德时代",)) is True
    assert relation_matches(linked, industries=("battery",)) is True
    assert relation_matches(linked, industries=("锂电池",), entities=("宁王",)) is False


def test_future_review_does_not_leak_into_historical_asof():
    conn = _review_conn()
    _review(conn, status="confirmed")

    before = link_event_candidates(
        _event(title="宁德时代宣布固态电池新进展"), conn=conn,
        catalog=CATALOG, asof="2026-08-30T09:30:00",
    )
    after = link_event_candidates(
        _event(title="宁德时代宣布固态电池新进展"), conn=conn,
        catalog=CATALOG, asof="2026-08-30T10:30:00",
    )

    assert before["relation_status"] == "candidate"
    assert not any(r["relation_status"] == "confirmed" for r in before["relations"])
    assert after["relation_status"] == "confirmed"


def test_rejected_industry_cannot_be_resurrected_by_entity_bridge():
    conn = _review_conn()
    conn.execute(
        "INSERT INTO event_relations VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        ("ev-1", "industry", "battery", "锂电池", "rejected", 0.0,
         json.dumps([{"kind": "human_review"}], ensure_ascii=False),
         "explicit_review", "2026-08-30T09:00:00", "analyst-a",
         "2026-08-30T10:00:00"),
    )
    conn.commit()

    linked = link_event_candidates(
        _event(title="供应链消息", tickers=["SZ300750"]), conn=conn,
        catalog=CATALOG, asof="2026-08-30T11:00:00",
    )
    battery = [r for r in linked["relations"] if r["target_id"] == "battery"]
    assert [r["relation_status"] for r in battery] == ["rejected"]
    assert relation_matches(linked, industries=("battery",)) is False


def test_top_level_confidence_belongs_to_winning_status():
    conn = _review_conn()
    conn.execute(
        "INSERT INTO event_relations VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        ("ev-1", "entity", "SZ300750", "宁德时代", "confirmed", 0.4,
         "[]", "explicit_review", "2026-08-30T09:00:00", "analyst-a",
         "2026-08-30T10:00:00"),
    )
    conn.commit()

    linked = link_event_candidates(
        _event(tickers=["SZ300750", "SZ300014"]), conn=conn,
        catalog=CATALOG, asof="2026-08-30T11:00:00",
    )
    assert linked["relation_status"] == "confirmed"
    assert linked["confidence"] == 0.4


def test_same_time_rejection_wins_conservatively_during_relation_replay():
    conn = _review_conn()
    for status in ("confirmed", "rejected"):
        conn.execute(
            "INSERT INTO event_relations VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            ("ev-1", "entity", "SZ300750", "宁德时代", status,
             0.8 if status == "confirmed" else 0.0, "[]", "explicit_review",
             "2026-08-30T09:00:00", "analyst-a", "2026-08-30T10:00:00"),
        )
    conn.commit()

    linked = link_event_candidates(
        _event(title="宁德时代进展"), conn=conn, catalog=CATALOG,
        asof="2026-08-30T11:00:00",
    )
    same_target = [r for r in linked["relations"] if r["target_id"] == "SZ300750"]
    assert [r["relation_status"] for r in same_target] == ["rejected"]
    assert linked["relation_status"] == "rejected" and linked["unlinked"] is False


def test_name_only_human_review_is_retained_and_suppresses_derived_candidate():
    conn = _review_conn()
    for status in ("candidate", "confirmed"):
        conn.execute(
            "INSERT INTO event_relations VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                "ev-1", "entity", None, "宁德时代", status,
                0.6 if status == "candidate" else 0.99,
                json.dumps([{"kind": "human_review", "target_basis": "name"}],
                           ensure_ascii=False),
                "explicit_review", "2026-08-30T09:00:00",
                "analyst-a" if status == "confirmed" else None,
                "2026-08-30T10:00:00" if status == "confirmed" else None,
            ),
        )
    conn.commit()

    linked = link_event_candidates(
        _event(title="宁德时代进展"), conn=conn,
        catalog={"catalog_asof": "2026-08-01T00:00:00",
                 "entities": [{"id": "SZ300750", "name": "宁德时代"}]},
        asof="2026-08-30T11:00:00",
    )

    assert len(linked["relations"]) == 1
    reviewed = linked["relations"][0]
    assert reviewed["target_id"] == "" and reviewed["target_name"] == "宁德时代"
    assert reviewed["relation_status"] == "confirmed"
    assert linked["relation_status"] == "confirmed" and linked["unlinked"] is False
    assert relation_matches(linked, entities=("宁德时代",)) is True
