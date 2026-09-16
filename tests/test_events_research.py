# -*- coding: utf-8 -*-
"""帷幄事件研究检索：PIT、过滤、provenance 与稳定 keyset 分页。"""
from __future__ import annotations

import base64
import json
import sqlite3

import pytest

from guanlan_v2.events import store
from guanlan_v2.events.research import (
    EventsSearchError,
    events_search,
    sanitize_outbound,
    search_events_page,
)


ASOF = "2026-08-30T12:00:00"
CATALOG = {
    "catalog_asof": "2026-08-01T00:00:00",
    "entities": [
        {"id": "SZ300750", "name": "宁德时代", "aliases": ["宁王"],
         "industry": "battery"},
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
            relation_id TEXT PRIMARY KEY,
            event_id TEXT NOT NULL,
            relation_type TEXT NOT NULL,
            target_id TEXT NOT NULL,
            target_name TEXT,
            relation_status TEXT NOT NULL,
            confidence REAL,
            evidence_json TEXT,
            relation_method TEXT,
            created_at TEXT NOT NULL,
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
        "source": "private_company",
        "source_ref": f"ref:{event_id}",
        "event_time": "2026-08-30T09:00:00",
        "available_at": "2026-08-30T10:00:00",
        "invalidated_at": None,
        "simhash": None,
        "line_id": event_id,
        "ingested_from": "company_events_json",
        "time_classification": "observed",
        "classification_reason": "source_time_not_future",
        "provenance_json": json.dumps(
            {"vendor": "private", "absolute_path": "C:/private/events.json", "api_key": "secret"},
            ensure_ascii=False,
        ),
        "payload_json": json.dumps({"private": "must-not-leak"}),
    }
    row.update(overrides)
    columns = list(row)
    conn.execute(
        f"INSERT INTO events ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})",
        [row[c] for c in columns],
    )
    conn.commit()


def _insert_relation(conn: sqlite3.Connection, relation_id: str, event_id: str,
                     **overrides) -> None:
    row = {
        "relation_id": relation_id,
        "event_id": event_id,
        "relation_type": "entity",
        "target_id": "SZ300750",
        "target_name": "宁德时代",
        "relation_status": "confirmed",
        "confidence": 0.8,
        "evidence_json": json.dumps({"ticket": relation_id}, ensure_ascii=False),
        "relation_method": "explicit_review",
        "created_at": "2026-08-30T09:00:00",
        "reviewed_by": "analyst-a",
        "reviewed_at": "2026-08-30T09:30:00",
    }
    row.update(overrides)
    columns = list(row)
    conn.execute(
        f"INSERT INTO event_relations ({','.join(columns)}) "
        f"VALUES ({','.join('?' for _ in columns)})",
        [row[c] for c in columns],
    )
    conn.commit()


def test_search_requires_asof_and_pit_hides_future_and_invalidated(tmp_path):
    conn = _conn(tmp_path)
    _insert(conn, "boundary", available_at=ASOF)
    _insert(conn, "future", available_at="2026-08-30T12:00:00.000001")
    _insert(conn, "invalidated", available_at="2026-08-30T08:00:00",
            invalidated_at="2026-08-30T11:00:00")

    with pytest.raises(EventsSearchError) as exc:
        search_events_page("", None, conn=conn)
    assert exc.value.code == "invalid_asof"
    result = search_events_page("", ASOF, conn=conn)
    assert [x["event_id"] for x in result["items"]] == ["boundary"]


def test_source_time_industry_and_entity_filters_compose(tmp_path):
    conn = _conn(tmp_path)
    _insert(conn, "match", title="宁德时代固态电池项目进展",
            event_time="2026-08-30T09:30:00")
    _insert(conn, "wrong-source", title="宁德时代固态电池项目进展",
            source="wire", event_time="2026-08-30T09:30:00",
            available_at="2026-08-30T09:59:00")
    _insert(conn, "wrong-time", title="宁德时代固态电池旧闻",
            event_time="2026-08-28T09:30:00", available_at="2026-08-30T09:58:00")

    result = search_events_page(
        "",
        ASOF,
        source="private_company",
        time_from="2026-08-30T09:00:00",
        time_to="2026-08-30T10:00:00",
        industry="battery",
        entity="SZ300750",
        conn=conn,
        catalog=CATALOG,
    )
    assert [x["event_id"] for x in result["items"]] == ["match"]
    assert result["filters"]["sources"] == ["private_company"]
    assert result["filters"]["industries"] == ["battery"]
    assert result["filters"]["entities"] == ["SZ300750"]


def test_search_payload_has_auditable_redacted_provenance_and_candidate_status(tmp_path):
    conn = _conn(tmp_path)
    _insert(conn, "ev-proof", title="宁德时代固态电池进展")

    item = search_events_page("", ASOF, conn=conn, catalog=CATALOG)["items"][0]
    proof = item["provenance"]
    assert proof["store"] == "events.db"
    assert proof["event_id"] == "ev-proof"
    assert proof["source_ref"] == "ref:ev-proof"
    assert proof["available_at"] == "2026-08-30T10:00:00"
    assert proof["retrieval_asof"]
    assert proof["vendor"] == "private"
    assert "absolute_path" not in proof and proof["api_key"] == "[REDACTED]"
    assert "payload_json" not in item and "must-not-leak" not in repr(item)
    assert item["relation_status"] == "candidate"
    assert item["issuer_fact"] is False
    assert item["evidence"]


def test_all_outbound_paths_and_secrets_are_redacted_including_relations(tmp_path):
    conn = _conn(tmp_path)
    _insert(
        conn,
        "ev-private",
        source_ref=r"C:\vault\source.json",
        provenance_json=json.dumps({
            "path": "/srv/private/raw.json",
            "nested": {"access_token": "token-value", "client_secret": "secret-value"},
        }),
    )
    _insert_relation(
        conn,
        "rel-private",
        "ev-private",
        evidence_json=json.dumps({
            "attachment": r"C:\vault\review.txt",
            "refresh_token": "review-token",
        }),
    )

    item = search_events_page("", ASOF, conn=conn, catalog={})["items"][0]
    rendered = json.dumps(item, ensure_ascii=False)
    assert item["source_ref"] == "[REDACTED_PATH]"
    assert item["provenance"]["source_ref"] == "[REDACTED_PATH]"
    assert item["provenance"]["path"] == "[REDACTED_PATH]"
    relation = next(r for r in item["relations"] if r["relation_status"] == "confirmed")
    assert relation["evidence"][0]["attachment"] == "[REDACTED_PATH]"
    assert relation["evidence"][0]["refresh_token"] == "[REDACTED]"
    for private_value in (
        r"C:\vault\source.json", "/srv/private/raw.json", r"C:\vault\review.txt",
        "token-value", "secret-value", "review-token",
    ):
        assert private_value not in rendered


def test_unified_outbound_sanitizer_handles_embedded_urls_paths_and_nested_secrets(tmp_path):
    conn = _conn(tmp_path)
    ordinary_url = "https://example.com/report?id=42&topic=token-economy&keynote=growth"
    source_url = (
        "https://analyst:password@example.com/report?id=42&access_token=url-token"
        "&signature=url-signature&lang=zh#access_token=fragment-token"
    )
    _insert(
        conn,
        "ev-embedded-private",
        title="Token economy、secret sauce 与 key growth drivers",
        summary=r"读取 C:\vault\raw.json 后继续分析，普通标题必须保留",
        source="wire token=source-secret",
        source_ref=source_url,
        provenance_json=json.dumps({
            "note": "read /srv/private/raw.json then api_key='nested-key'",
            "deep": [{"authorization": "Bearer nested-auth"}],
            "ordinary_url": ordinary_url,
            "file_note": "fallback file:///home/analyst/private.json",
            "custom_path_note": "copy /custom/private/blob.bin after review",
            "encoded": r'{\"token\":\"encoded-secret\",\"label\":\"keep\"}',
        }, ensure_ascii=False),
        payload_json=json.dumps({"token": "payload-secret"}),
    )
    _insert_relation(
        conn,
        "rel-embedded-private",
        "ev-embedded-private",
        evidence_json=json.dumps({
            "note": r"attachment=C:\vault\review.txt refresh_token=review-secret",
            "url": "https://example.com/evidence?sig=query-secret&topic=ai",
            "comment": "Token economy has key growth drivers and a signature move",
        }, ensure_ascii=False),
    )

    item = search_events_page("", ASOF, conn=conn, catalog={})["items"][0]
    assert item["title"] == "Token economy、secret sauce 与 key growth drivers"
    assert item["summary"] == "读取 [REDACTED_PATH] 后继续分析，普通标题必须保留"
    assert item["source"] == "wire token=[REDACTED]"
    assert item["source_ref"] == (
        "https://example.com/report?id=42&access_token=[REDACTED]"
        "&signature=[REDACTED]&lang=zh#access_token=[REDACTED]"
    )
    assert item["provenance"]["ordinary_url"] == ordinary_url
    assert item["provenance"]["note"] == (
        "read [REDACTED_PATH] then api_key='[REDACTED]'"
    )
    assert item["provenance"]["deep"][0]["authorization"] == "[REDACTED]"
    assert item["provenance"]["file_note"] == "fallback [REDACTED_PATH]"
    assert item["provenance"]["custom_path_note"] == (
        "copy [REDACTED_PATH] after review"
    )
    assert item["provenance"]["encoded"] == (
        r'{\"token\":\"[REDACTED]\",\"label\":\"keep\"}'
    )
    relation = next(
        row for row in item["relations"]
        if row["relation_id"] == "rel-embedded-private"
    )
    proof = relation["evidence"][0]
    assert proof["note"] == (
        "attachment=[REDACTED_PATH] refresh_token=[REDACTED]"
    )
    assert proof["url"] == (
        "https://example.com/evidence?sig=[REDACTED]&topic=ai"
    )
    assert proof["comment"] == (
        "Token economy has key growth drivers and a signature move"
    )
    rendered = json.dumps(item, ensure_ascii=False)
    assert "payload_json" not in item and "payload-secret" not in rendered
    for secret in (
        "url-token", "url-signature", "fragment-token", "source-secret",
        "nested-key", "nested-auth", "review-secret", "query-secret",
        "encoded-secret", "/custom/private/blob.bin",
        r"C:\vault\raw.json", r"C:\vault\review.txt", "/srv/private/raw.json",
        "file:///home/analyst/private.json", "analyst:password",
    ):
        assert secret not in rendered


def test_unified_outbound_sanitizer_preserves_benign_text_urls_and_routes():
    value = {
        "title": "Token economy、secret sauce 与 key growth drivers",
        "url": "https://example.com/report?id=42&topic=token-economy&keynote=growth#section-2",
        "relative_route": "gl-mcp/tools/list",
        "evidence": "authorization policy updated; signature move expected",
        "nested": [{"label": "api key management without an assigned credential"}],
    }
    assert sanitize_outbound(value) == value


def test_search_fails_closed_instead_of_crashing_on_overdeep_json(tmp_path):
    conn = _conn(tmp_path)
    nested = ("[" * 1_200) + '{"token":"deep-secret"}' + ("]" * 1_200)
    _insert(
        conn,
        "ev-overdeep-json",
        provenance_json='{"nested":' + nested + "}",
    )
    _insert_relation(
        conn,
        "rel-overdeep-json",
        "ev-overdeep-json",
        evidence_json=nested,
    )

    result = search_events_page("", ASOF, conn=conn, catalog={})
    assert result["items"][0]["event_id"] == "ev-overdeep-json"
    rendered = json.dumps(result, ensure_ascii=False)
    assert "deep-secret" not in rendered
    assert "internal_error" not in rendered


def test_unified_outbound_sanitizer_redacts_presigned_and_non_http_credentials():
    value = {
        "presigned": (
            "https://example.com/object?X-Amz-Algorithm=AWS4-HMAC-SHA256"
            "&X-Amz-Credential=credential-value&X-Amz-Signature=signature-value"
            "&response-content-type=text%2Fplain"
        ),
        "database": "postgresql://db-user:db-password@db.example/main?sslmode=require",
    }
    sanitized = sanitize_outbound(value)
    assert sanitized["presigned"] == (
        "https://example.com/object?X-Amz-Algorithm=AWS4-HMAC-SHA256"
        "&X-Amz-Credential=[REDACTED]&X-Amz-Signature=[REDACTED]"
        "&response-content-type=text%2Fplain"
    )
    assert sanitized["database"] == (
        "postgresql://db.example/main?sslmode=require"
    )
    assert sanitize_outbound(sanitized) == sanitized


def test_missing_fts_table_degrades_to_like_and_still_returns_hit(tmp_path):
    conn = _conn(tmp_path)  # deliberately no events_fts table
    _insert(conn, "hit", title="央行宣布降准")
    _insert(conn, "miss", title="海外市场收盘", available_at="2026-08-30T09:59:00")

    result = search_events_page("降准", ASOF, conn=conn)
    assert [x["event_id"] for x in result["items"]] == ["hit"]
    assert result["retrieval"]["mode"] == "like"
    assert result["retrieval"]["fts"]["status"] == "unavailable"
    assert result["retrieval"]["fts"]["reason"] == "events_fts_missing"


def test_partial_fts_missing_rows_use_same_token_or_semantics(tmp_path, monkeypatch):
    conn = _conn(tmp_path)
    conn.execute("CREATE VIRTUAL TABLE events_fts USING fts5(tokens,event_id UNINDEXED)")
    _insert(conn, "indexed-alpha", title="alpha headline")
    _insert(conn, "missing-beta", title="beta headline",
            available_at="2026-08-30T09:59:00")
    conn.execute(
        "INSERT INTO events_fts(tokens,event_id) VALUES (?,?)", ("alpha", "indexed-alpha"),
    )
    conn.commit()
    monkeypatch.setattr(store, "_jieba_tokens", lambda _query: "alpha beta")

    result = search_events_page("alpha beta", ASOF, conn=conn, catalog={})
    assert {item["event_id"] for item in result["items"]} == {
        "indexed-alpha", "missing-beta",
    }
    assert result["retrieval"]["mode"] == "fts+like_missing"
    assert result["retrieval"]["query_semantics"] == "token_or"


def test_watermark_and_keyset_give_three_stable_pages_across_insert(tmp_path):
    conn = _conn(tmp_path)
    # 同时刻依赖 event_id 作稳定 tie-break；6 行应恰好形成三页。
    for i in range(6):
        _insert(conn, f"e{i}", available_at="2026-08-30T10:00:00")

    p1 = search_events_page("", ASOF, limit=2, conn=conn)
    watermark = p1["snapshot_watermark"]
    assert p1["next_cursor"]
    # 分页期间晚到但 available_at 更靠前的行，rowid 超过首屏 watermark，不能挤动后页。
    _insert(conn, "e-new", available_at="2026-08-30T11:00:00")
    p2 = search_events_page("", ASOF, limit=2, cursor=p1["next_cursor"], conn=conn)
    p3 = search_events_page("", ASOF, limit=2, cursor=p2["next_cursor"], conn=conn)

    ids = [x["event_id"] for page in (p1, p2, p3) for x in page["items"]]
    assert ids == ["e5", "e4", "e3", "e2", "e1", "e0"]
    assert len(ids) == len(set(ids)) == 6
    assert p2["snapshot_watermark"] == p3["snapshot_watermark"] == watermark
    assert p3["next_cursor"] is None
    assert "e-new" not in ids


def test_bad_cursor_and_cross_filter_cursor_are_rejected(tmp_path):
    conn = _conn(tmp_path)
    for i in range(3):
        _insert(conn, f"e{i}")
    first = search_events_page("", ASOF, limit=1, source="private_company", conn=conn)

    with pytest.raises(EventsSearchError) as malformed:
        search_events_page("", ASOF, limit=1, cursor="not-a-json-cursor", conn=conn)
    assert malformed.value.code == "invalid_cursor"
    with pytest.raises(EventsSearchError) as mismatch:
        search_events_page("", ASOF, limit=1, source="wire",
                           cursor=first["next_cursor"], conn=conn)
    assert mismatch.value.code == "cursor_filter_mismatch"

    boundary = events_search("", ASOF, limit=1, cursor="bad", conn=conn)
    assert boundary["ok"] is False
    assert boundary["error"]["code"] == "invalid_cursor"
    assert boundary["items"] == [] and boundary["issuer_fact"] is False


def test_cursor_rejects_relation_catalog_and_fts_snapshot_changes(tmp_path, monkeypatch):
    conn = _conn(tmp_path)
    for i in range(4):
        _insert(conn, f"e{i}", title="alpha event")

    base = search_events_page("", ASOF, limit=1, conn=conn, catalog={})
    _insert_relation(conn, "rel-new", "e0")
    with pytest.raises(EventsSearchError) as relation_changed:
        search_events_page("", ASOF, limit=1, cursor=base["next_cursor"],
                           conn=conn, catalog={})
    assert relation_changed.value.code == "cursor_snapshot_changed"

    catalog_a = {"catalog_asof": "2026-08-01T00:00:00", "entities": []}
    catalog_b = {
        "catalog_asof": "2026-08-01T00:00:00",
        "entities": [{"id": "SZ300750", "name": "宁德时代"}],
    }
    base = search_events_page("", ASOF, limit=1, conn=conn, catalog=catalog_a)
    with pytest.raises(EventsSearchError) as catalog_changed:
        search_events_page("", ASOF, limit=1, cursor=base["next_cursor"],
                           conn=conn, catalog=catalog_b)
    assert catalog_changed.value.code == "cursor_snapshot_changed"

    conn.execute("CREATE VIRTUAL TABLE events_fts USING fts5(tokens,event_id UNINDEXED)")
    conn.commit()
    monkeypatch.setattr(store, "_jieba_tokens", lambda _query: "alpha")
    base = search_events_page("alpha", ASOF, limit=1, conn=conn, catalog={})
    conn.execute("INSERT INTO events_fts(tokens,event_id) VALUES ('alpha','e0')")
    conn.commit()
    with pytest.raises(EventsSearchError) as fts_changed:
        search_events_page("alpha", ASOF, limit=1, cursor=base["next_cursor"],
                           conn=conn, catalog={})
    assert fts_changed.value.code == "cursor_snapshot_changed"


def test_public_errors_use_fixed_non_disclosing_messages(tmp_path, monkeypatch):
    private_error = r"C:\vault\events.db: credential=top-secret"

    def fail_open(*_args, **_kwargs):
        raise sqlite3.OperationalError(private_error)

    monkeypatch.setattr(store, "connect_readonly", fail_open)
    unavailable = events_search("", ASOF, path=r"C:\vault\events.db")
    assert unavailable["error"] == {
        "code": "events_db_unavailable",
        "message": "events.db 只读打开失败",
    }
    assert private_error not in repr(unavailable)

    import guanlan_v2.events.research as research

    monkeypatch.setattr(
        research, "search_events_page",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError(private_error)),
    )
    internal = research.events_search("", ASOF)
    assert internal["error"] == {
        "code": "events_internal_error",
        "message": "事件检索内部错误",
    }
    assert private_error not in repr(internal)


def test_validation_and_cursor_shape_errors_are_stable(tmp_path):
    conn = _conn(tmp_path)
    _insert(conn, "bad-time", event_time="not-a-time")

    cases = [
        ({"limit": True}, "invalid_limit"),
        ({"time_from": "   "}, "invalid_time"),
        ({"time_from": "not-a-time"}, "invalid_time"),
        ({"time_from": "2026-08-31", "time_to": "2026-08-30"},
         "invalid_time_range"),
    ]
    for kwargs, code in cases:
        with pytest.raises(EventsSearchError) as raised:
            search_events_page("", ASOF, conn=conn, **kwargs)
        assert raised.value.code == code

    # Date-only filters use Hong Kong day boundaries; an unparseable source
    # event_time is excluded instead of guessed into the interval.
    filtered = search_events_page(
        "", ASOF, conn=conn, time_from="2026-08-30", time_to="2026-08-30",
    )
    assert filtered["items"] == []
    invalid_asof = events_search("", r"C:\private\not-an-asof", conn=conn)
    assert invalid_asof["error"] == {
        "code": "invalid_asof", "message": "asof 必须是有效 ISO 日期或时间",
    }
    assert "private" not in repr(invalid_asof)

    def opaque(body):
        raw = json.dumps(body, separators=(",", ":")).encode("utf-8")
        return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")

    malformed_shapes = [
        {"v": 999},
        {"v": 2, "fp": "x", "wm": "bad", "a": "a", "e": "e", "s": {}},
        {"v": 2, "fp": "x", "wm": 1, "a": "", "e": "e", "s": {}},
        {"v": 2, "fp": "x", "wm": 1, "a": "a", "e": "e", "s": {}},
    ]
    from guanlan_v2.events.research import _decode_cursor, _fingerprint

    fingerprint = _fingerprint({})
    # Patch the fingerprint field after construction so validation reaches the
    # watermark/key/snapshot branches instead of failing at filter mismatch.
    malformed_shapes[1]["fp"] = fingerprint
    malformed_shapes[2]["fp"] = fingerprint
    malformed_shapes[3]["fp"] = fingerprint
    for body in malformed_shapes:
        with pytest.raises(EventsSearchError) as raised:
            _decode_cursor(opaque(body), fingerprint=fingerprint)
        assert raised.value.code == "invalid_cursor"


@pytest.mark.parametrize(
    ("arguments", "error_code"),
    [
        ({"query": "q" * 513}, "query_too_long"),
        ({"cursor": "x" * 4097}, "cursor_too_long"),
        ({"sources": [f"source-{index}" for index in range(51)]},
         "too_many_filter_values"),
        ({"industries": [f"industry-{index}" for index in range(51)]},
         "too_many_filter_values"),
        ({"entities": [f"entity-{index}" for index in range(51)]},
         "too_many_filter_values"),
        ({"source": "s" * 201}, "filter_value_too_long"),
        ({"industry": "i" * 201}, "filter_value_too_long"),
        ({"entity": "e" * 201}, "filter_value_too_long"),
        ({"time_from": "2" * 65}, "filter_value_too_long"),
    ],
)
def test_public_input_bounds_fail_before_cursor_decode_or_sql(
    monkeypatch, arguments, error_code,
):
    import guanlan_v2.events.research as research

    class NoSqlConnection:
        calls = 0

        def execute(self, *_args, **_kwargs):
            self.calls += 1
            raise AssertionError("validation must finish before SQL")

    decoded = {"calls": 0}

    def forbidden_decode(*_args, **_kwargs):
        decoded["calls"] += 1
        raise AssertionError("oversized input must not enter base64 decode")

    monkeypatch.setattr(research.base64, "urlsafe_b64decode", forbidden_decode)
    conn = NoSqlConnection()
    supplied = dict(arguments)
    query = supplied.pop("query", "")
    result = events_search(query, ASOF, conn=conn, catalog={}, **supplied)
    assert result["error"]["code"] == error_code
    assert conn.calls == 0
    assert decoded["calls"] == 0


def test_public_input_boundaries_remain_compatible(tmp_path):
    conn = _conn(tmp_path)
    sources = ["s" * 200, *(f"source-{index}" for index in range(49))]
    accepted = events_search(
        "q" * 512,
        ASOF,
        sources=sources,
        industries=["industry"],
        entities=["entity"],
        conn=conn,
        catalog={},
    )
    assert accepted["ok"] is True
    assert len(accepted["query"]) == 512
    assert len(accepted["filters"]["sources"]) == 50
    assert len(accepted["filters"]["sources"][0]) == 200

    # Exactly-at-limit cursors reach normal shape validation, still before SQL.
    class NoSqlConnection:
        calls = 0

        def execute(self, *_args, **_kwargs):
            self.calls += 1
            raise AssertionError("malformed cursor must fail before SQL")

    no_sql = NoSqlConnection()
    malformed = events_search("", ASOF, cursor="x" * 4096, conn=no_sql, catalog={})
    assert malformed["error"]["code"] == "invalid_cursor"
    assert no_sql.calls == 0
