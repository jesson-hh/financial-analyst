from __future__ import annotations

import hashlib
from pathlib import Path

from guanlan_v2.seats.workspace_history import (
    MAX_ITEMS,
    MAX_TEXT_CHARS,
    build_workspace_history_brief,
)


class _WorkspaceStore:
    def __init__(self, rows):
        self.rows = list(rows)

    def list(self, *, include_archived=True):
        if include_archived:
            return [dict(row) for row in self.rows]
        return [dict(row) for row in self.rows if not row.get("archived")]


class _ConsoleStore:
    def __init__(self, root: Path, rows):
        self.root = root
        self.workspace_store = _WorkspaceStore(rows)


def _workspace(workspace_id="rw_111111111111", *, code="SH600519",
               kind="stock", archived=False):
    return {
        "id": workspace_id,
        "kind": kind,
        "code": code,
        "archived": archived,
    }


def _write_memory(root: Path, workspace_id: str, text: str) -> Path:
    path = root / "workspace_memory" / workspace_id / "memory.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_exact_workspace_returns_latest_per_key_and_bounded_newest_rows(tmp_path):
    row = _workspace()
    store = _ConsoleStore(tmp_path, [row])
    _write_memory(tmp_path, row["id"], "\n".join([
        "- [2026-08-01] (thesis) 旧主线，应被同 key 新行替代",
        "- [2026-08-10] 无 key 的较早观察",
        "- [2026-08-11] 第三条观察",
        "- [2026-08-12] (risk) 风险摘要",
        "- [2026-08-13] (thesis) 新主线摘要",
        "这是一条不带日期前缀的原文，必须忽略",
    ]))

    brief = build_workspace_history_brief(
        store, "600519", "2026-08-14 15:00", replay=False, freq="day",
    )

    assert [item["date"] for item in brief["items"]] == [
        "2026-08-13", "2026-08-12", "2026-08-11",
    ]
    assert [item["key"] for item in brief["items"]] == ["thesis", "risk", None]
    assert "新主线摘要" in brief["text"]
    assert "旧主线" not in brief["text"]
    assert "不带日期" not in brief["text"]
    assert len(brief["items"]) == MAX_ITEMS
    assert len(brief["text"]) <= MAX_TEXT_CHARS
    assert brief["ref"] == {
        "kind": "workspace_history",
        "workspace_id": row["id"],
        "code": "SH600519",
        "asof": "2026-08-14",
        "item_count": 3,
        "latest_date": "2026-08-13",
        "digest": hashlib.sha256(brief["text"].encode("utf-8")).hexdigest(),
        "reason": None,
    }


def test_replay_daily_and_intraday_are_strictly_before_asof_day(tmp_path):
    row = _workspace(code="SZ300750")
    store = _ConsoleStore(tmp_path, [row])
    _write_memory(tmp_path, row["id"], "\n".join([
        "- [2026-08-09] (thesis) 前一日可见",
        "- [2026-08-10] (risk) 同日时刻不明，不得回放",
        "- [2026-08-11] (catalyst) 未来信息不得回放",
    ]))

    daily = build_workspace_history_brief(
        store, "SZ300750", "2026-08-10", replay=True, freq="day",
    )
    assert daily["items"] == [
        {"date": "2026-08-09", "key": "thesis", "text": "前一日可见"},
    ]

    intraday = build_workspace_history_brief(
        store, "SZ300750", "2026-08-10 10:30", replay=True, freq="30min",
    )
    assert intraday["items"] == [
        {"date": "2026-08-09", "key": "thesis", "text": "前一日可见"},
    ]
    assert intraday["ref"]["reason"] is None


def test_live_includes_same_day_but_never_future_dated_memory(tmp_path):
    row = _workspace(code="SZ300750")
    store = _ConsoleStore(tmp_path, [row])
    _write_memory(tmp_path, row["id"], "\n".join([
        "- [2026-08-13] (thesis) 昨日结论",
        "- [2026-08-14] (risk) 今日已形成结论",
        "- [2026-08-15] (catalyst) 未来日期异常",
    ]))

    brief = build_workspace_history_brief(
        store, "300750", "2026-08-14 15:00", replay=False,
    )
    assert [item["text"] for item in brief["items"]] == [
        "今日已形成结论", "昨日结论",
    ]


def test_ambiguous_stock_and_etf_workspace_fails_closed(tmp_path):
    rows = [
        _workspace("rw_111111111111", code="SH600519", kind="stock"),
        _workspace("rw_222222222222", code="SH600519", kind="etf"),
    ]
    store = _ConsoleStore(tmp_path, rows)
    for row in rows:
        _write_memory(tmp_path, row["id"], "- [2026-08-01] (thesis) 不可择一读取")

    brief = build_workspace_history_brief(store, "SH600519", "2026-08-14")
    assert brief["items"] == []
    assert brief["ref"]["workspace_id"] is None
    assert brief["ref"]["reason"] == "workspace_ambiguous"


def test_archived_and_general_workspaces_are_not_read(tmp_path):
    archived = _workspace(archived=True)
    malformed_general = _workspace(
        "rw_general", code="SZ300750", kind="general", archived=False,
    )
    store = _ConsoleStore(tmp_path, [archived, malformed_general])
    _write_memory(tmp_path, archived["id"], "- [2026-08-01] (thesis) 已归档私有摘要")
    _write_memory(tmp_path, malformed_general["id"], "- [2026-08-01] (thesis) 通用空间摘要")

    old = build_workspace_history_brief(store, "SH600519", "2026-08-14")
    assert old["items"] == []
    assert old["ref"]["reason"] == "workspace_archived"

    general = build_workspace_history_brief(store, "SZ300750", "2026-08-14")
    assert general["items"] == []
    assert general["ref"]["reason"] == "workspace_not_found"


def test_budget_skips_oversized_row_without_truncating_smaller_rows(tmp_path):
    row = _workspace()
    store = _ConsoleStore(tmp_path, [row])
    huge = "超" * MAX_TEXT_CHARS
    small = "完整短摘要"
    _write_memory(tmp_path, row["id"], "\n".join([
        f"- [2026-08-13] (thesis) {huge}",
        f"- [2026-08-12] (risk) {small}",
        "- [2026-08-11] (catalyst) 第二条完整摘要",
    ]))

    brief = build_workspace_history_brief(store, "SH600519", "2026-08-14")
    assert len(brief["text"]) <= MAX_TEXT_CHARS
    assert huge not in brief["text"]
    assert brief["items"][0]["text"] == small
    assert all(item["text"] in brief["text"] for item in brief["items"])


def test_missing_memory_and_invalid_inputs_return_shape_stable_empty(tmp_path):
    row = _workspace()
    store = _ConsoleStore(tmp_path, [row])

    missing = build_workspace_history_brief(store, "SH600519", "2026-08-14")
    assert missing["items"] == [] and missing["text"] == ""
    assert missing["ref"]["workspace_id"] == row["id"]
    assert missing["ref"]["reason"] == "memory_empty"

    bad_code = build_workspace_history_brief(store, "not/a/code", "2026-08-14")
    assert bad_code["ref"]["reason"] == "invalid_code"

    bad_asof = build_workspace_history_brief(store, "SH600519", "not-a-date")
    assert bad_asof["ref"]["reason"] == "invalid_asof"
