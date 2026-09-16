# -*- coding: utf-8 -*-
"""events.db research tools stay auditable and read-only across both transports."""
from __future__ import annotations

import asyncio
import json
import sqlite3
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Dict, Iterator, Tuple

import pytest

from guanlan_v2.events import relations, store


ASOF = "2026-08-30T12:00:00"
_PROVENANCE_FIELDS = {
    "fixture", "store", "event_id", "source", "source_ref", "ingested_from",
    "available_at", "event_time", "line_id", "retrieval_asof", "pit_predicate",
    "retrieval_mode",
}


@pytest.fixture
def sealed_events_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A real migrated store plus an explicitly empty relation catalog."""
    db = tmp_path / "events.db"
    catalog = tmp_path / "empty-relation-catalog.json"
    catalog.write_text(json.dumps({
        "catalog_asof": "2026-08-30T00:00:00",
        "entities": [],
        "industries": [],
    }, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setenv("GUANLAN_EVENTS_DB", str(db))
    monkeypatch.setenv("GUANLAN_EVENT_RELATION_CATALOG", str(catalog))

    relations._default_catalog.cache_clear()
    try:
        assert relations._default_catalog() == ({}, {})
        conn = store.connect(db)
        try:
            store.init_db(conn)
            assert store.insert_event(conn, {
                "event_id": "flow-limit-up",
                "event_type": "limit_up",
                "tickers": [],
                "title": "市场涨停原因：流动性改善",
                "summary": "全市场候选线索，未指向任何发行人",
                "source": "fixture-wire",
                "source_ref": (
                    "https://wire-user:wire-password@example.com/event?id=42"
                    "&access_token=wire-url-secret&signature=wire-signature&lang=zh"
                    "&q=sk-abcdefghijklmnop"
                    "&next=https%253A%252F%252Fnested.example%252Fcb"
                    "%253Ftoken%253Dnested-flow-secret#sk-ponmlkjihgfedcba"
                ),
                "event_time": "2026-08-30T09:00:00",
                "available_at": "2026-08-30T10:00:00",
                "line_id": "flow-line",
                "ingested_from": "e2e-fixture",
                "time_classification": "observed",
                "classification_reason": "e2e fixture observed before asof",
                "payload": {
                    "authorization": "Bearer payload-secret-must-not-leak",
                    "raw": "private upstream payload",
                },
                "provenance": {
                    "fixture": "events-research-flow",
                    "local_path": str(tmp_path / "private" / "raw.json"),
                    "token": "provenance-secret-must-not-leak",
                    "nested_path": str(tmp_path / "private" / "more.json"),
                    "nested": [{
                        "note": r"open C:\private\flow.json then secret=flow-note-secret",
                        "ordinary_url": "https://example.com/report?id=42&topic=ai",
                        "payload_json": {"token": "flow-deep-payload-secret"},
                    }],
                    "PayloadJSON": "flow-camel-payload-secret",
                },
            })
            conn.commit()
        finally:
            conn.close()
        yield db
    finally:
        relations._default_catalog.cache_clear()


def _db_snapshot(path: Path) -> Tuple[Any, ...]:
    """Compare schema and every logical row, including optional FTS shadow data."""
    conn = sqlite3.connect(path)
    try:
        schema = tuple(tuple(row) for row in conn.execute(
            "SELECT type,name,tbl_name,sql FROM sqlite_master "
            "ORDER BY type,name,tbl_name"
        ))
        contents = []
        for kind, name, _table_name, _sql in schema:
            if kind != "table" or str(name).startswith("sqlite_"):
                continue
            quoted = '"' + str(name).replace('"', '""') + '"'
            rows = tuple(sorted(
                (tuple(row) for row in conn.execute(f"SELECT * FROM {quoted}")),
                key=repr,
            ))
            contents.append((name, rows))
        return (
            int(conn.execute("PRAGMA user_version").fetchone()[0]),
            schema,
            tuple(contents),
        )
    finally:
        conn.close()


def _console_tools():
    import financial_analyst.buddy.tools as buddy_tools
    import guanlan_v2.console.tools as console_tools

    console_tools.register_console_tools()
    found = {
        name: buddy_tools.get_tool(name)
        for name in ("ww_events_search", "ww_market_clues")
    }
    assert all(found.values()), "events research tools must be reachable in Console"
    return found


def _mcp_call(name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
    import guanlan_v2.glmcp.server as mcp_server

    blocks = asyncio.run(mcp_server.dispatch_tool(name, arguments))
    assert len(blocks) == 1 and blocks[0].type == "text"
    return json.loads(blocks[0].text)


def _strings(value: Any) -> Iterator[str]:
    if isinstance(value, dict):
        for key, child in value.items():
            yield str(key)
            yield from _strings(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            yield from _strings(child)
    elif isinstance(value, str):
        yield value


def _assert_no_private_payload(value: Dict[str, Any]) -> None:
    normalised = {
        "".join(character for character in text.casefold() if character.isalnum())
        for text in _strings(value)
    }
    assert "payloadjson" not in normalised
    for text in _strings(value):
        assert "payload-secret-must-not-leak" not in text
        assert "provenance-secret-must-not-leak" not in text
        assert "wire-url-secret" not in text
        assert "wire-signature" not in text
        assert "wire-user:wire-password" not in text
        assert "flow-note-secret" not in text
        assert "nested-flow-secret" not in text
        assert "flow-deep-payload-secret" not in text
        assert "flow-camel-payload-secret" not in text
        assert "sk-abcdefghijklmnop" not in text
        assert "sk-ponmlkjihgfedcba" not in text
        assert not PureWindowsPath(text).is_absolute()
        assert not PurePosixPath(text).is_absolute()


def _assert_complete_unlinked(item: Dict[str, Any]) -> None:
    assert {"provenance", "relation_status", "confidence", "evidence", "unlinked"} <= set(item)
    assert item["relation_status"] == "unlinked"
    assert item["confidence"] == 0.0
    assert item["evidence"] == []
    assert item["unlinked"] is True
    provenance = item["provenance"]
    assert _PROVENANCE_FIELDS <= set(provenance)
    assert provenance["fixture"] == "events-research-flow"
    assert provenance["store"] == "events.db"
    assert provenance["event_id"] == "flow-limit-up"
    assert provenance["source_ref"] == (
        "https://example.com/event?id=42&access_token=[REDACTED]"
        "&signature=[REDACTED]&lang=zh&q=[REDACTED]&next=[REDACTED]#[REDACTED]"
    )
    assert provenance["retrieval_asof"] == ASOF
    assert provenance["pit_predicate"] == store.PIT_PREDICATE
    assert provenance["retrieval_mode"] in {"fts", "fts+like_missing", "like", "scan"}
    assert provenance["nested"][0]["note"] == (
        "open [REDACTED_PATH] then secret=[REDACTED]"
    )
    assert provenance["nested"][0]["ordinary_url"] == (
        "https://example.com/report?id=42&topic=ai"
    )


def test_console_and_gl_mcp_research_flow_is_identical_auditable_and_readonly(
    sealed_events_db: Path,
) -> None:
    tools = _console_tools()
    before = _db_snapshot(sealed_events_db)
    search_args = {"asof": ASOF, "query": "涨停", "limit": 10}
    clue_args = {"asof": ASOF, "window_days": 1, "limit": 10}

    console_search_result = tools["ww_events_search"].run(**search_args)
    console_clues_result = tools["ww_market_clues"].run(**clue_args)
    assert console_search_result.is_error is False
    assert console_clues_result.is_error is False
    console_search = json.loads(console_search_result.content)
    console_clues = json.loads(console_clues_result.content)
    mcp_search = _mcp_call("ww_events_search", search_args)
    mcp_clues = _mcp_call("ww_market_clues", clue_args)

    assert console_search == mcp_search
    assert console_clues == mcp_clues
    assert console_search["ok"] is True and console_search["asof"] == ASOF
    assert [item["event_id"] for item in console_search["items"]] == ["flow-limit-up"]
    _assert_complete_unlinked(console_search["items"][0])
    assert console_clues["ok"] is True and console_clues["asof"] == ASOF
    _assert_complete_unlinked(console_clues["event_lines"][0]["representative"])
    _assert_complete_unlinked(console_clues["limit_up_reasons"][0])
    _assert_no_private_payload(console_search)
    _assert_no_private_payload(console_clues)
    assert _db_snapshot(sealed_events_db) == before


def test_missing_asof_has_same_stable_error_in_console_and_gl_mcp(
    sealed_events_db: Path,
) -> None:
    tools = _console_tools()
    before = _db_snapshot(sealed_events_db)
    expected = {
        "ww_events_search": "events_search_asof_required",
        "ww_market_clues": "market_clues_asof_required",
    }
    for name, error_code in expected.items():
        console_result = tools[name].run()
        assert console_result.is_error is True
        console_body = json.loads(console_result.content)
        mcp_body = _mcp_call(name, {})
        assert console_body == mcp_body
        assert console_body["error"]["code"] == error_code
        assert "asof" in console_body["error"]["message"]
    assert _db_snapshot(sealed_events_db) == before


def test_input_bound_errors_match_across_console_and_gl_mcp(
    sealed_events_db: Path,
) -> None:
    tools = _console_tools()
    before = _db_snapshot(sealed_events_db)
    cases = [
        ("ww_events_search", {"asof": ASOF, "query": "q" * 513},
         "query_too_long"),
        ("ww_events_search", {"asof": ASOF, "cursor": "x" * 4097},
         "cursor_too_long"),
        ("ww_events_search", {
            "asof": ASOF,
            "sources": [f"source-{index}" for index in range(51)],
        }, "too_many_filter_values"),
        ("ww_market_clues", {
            "asof": ASOF,
            "entities": [f"entity-{index}" for index in range(51)],
        }, "too_many_filter_values"),
    ]
    for name, arguments, error_code in cases:
        console_result = tools[name].run(**arguments)
        assert console_result.is_error is True
        console_body = json.loads(console_result.content)
        mcp_body = _mcp_call(name, arguments)
        assert console_body == mcp_body
        assert console_body["error"]["code"] == error_code
    assert _db_snapshot(sealed_events_db) == before
