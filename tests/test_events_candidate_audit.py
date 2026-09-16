# -*- coding: utf-8 -*-
from __future__ import annotations

import hashlib

import pytest

from guanlan_v2.events import store
from guanlan_v2.events.audit import audit_candidate_links
from guanlan_v2.events.cleanup import CleanupPlanError


def _hash(path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _db(tmp_path):
    path = (tmp_path / "events.db").resolve()
    conn = store.connect(path)
    store.init_db(conn)
    with conn:
        assert store.insert_event(conn, {
            "event_id": "semi", "event_type": "newsradar:semi",
            "title": "行业产能消息", "tickers": [],
            "available_at": "2026-08-30T09:00:00",
            "ingested_from": "newsradar", "source_id": None,
        })
        assert store.insert_event(conn, {
            "event_id": "future", "event_type": "newsradar:ai",
            "title": "未来才可见", "tickers": [],
            "available_at": "2026-08-31T09:00:00",
            "ingested_from": "newsradar", "source_id": "newsradar",
        })
    conn.close()
    return path


def test_newsradar_source_sector_is_context_only_and_audit_writes_nothing(tmp_path):
    path = _db(tmp_path)
    before = _hash(path)

    report = audit_candidate_links(
        path, asof="2026-08-30T23:59:00", source_id="newsradar",
        sample_limit=100,
    )

    assert report["visible_rows"] == 1
    assert report["structured_industry_linkable_rows"] == 1
    assert report["structured_industry_coverage"] == 1.0
    assert report["sample_industry_targets"] == {"semi": 1}
    assert report["sample_relation_statuses"] == {"source_asserted": 1}
    assert report["sample_relation_types"] == {"industry": 1}
    assert report["issuer_fact"] is False
    assert report["persisted_rows_written"] == 0
    assert report["persisted_relation_rows_before"] == 0
    assert report["persisted_relation_rows_after"] == 0
    assert report["production_mutated"] is False
    assert _hash(path) == before


def test_candidate_audit_requires_explicit_absolute_db_and_bounded_sample(tmp_path):
    path = _db(tmp_path)
    with pytest.raises(CleanupPlanError, match="absolute"):
        audit_candidate_links(
            type(path)("events.db"), asof="2026-08-30", sample_limit=10,
        )
    with pytest.raises(CleanupPlanError, match="sample_limit"):
        audit_candidate_links(path, asof="2026-08-30", sample_limit=1001)
