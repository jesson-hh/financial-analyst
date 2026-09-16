"""Phase-three memory self-awareness and recall telemetry."""
from __future__ import annotations

import json

from fastapi import FastAPI
from fastapi.testclient import TestClient

from guanlan_v2.console import tools as ct
from guanlan_v2.console.api import _memory_context, build_console_router
from guanlan_v2.console.memory_insight import MemoryInsight
from guanlan_v2.console.store import ConsoleStore


class _UnusedAgent:
    messages = []

    async def run_turn(self, *_args, **_kwargs):
        if False:
            yield None


def _client(tmp_path, monkeypatch):
    root = tmp_path / "console"
    memory = root / "memory.md"
    archive = root / "memory.archive.md"
    monkeypatch.setattr(ct, "_MEMORY_PATH", memory)
    monkeypatch.setattr(ct, "_ARCHIVE_PATH", archive)
    store = ConsoleStore(root=root)
    app = FastAPI()
    app.include_router(build_console_router(
        store=store, agent_factory=lambda _sid: _UnusedAgent(),
    ))
    return TestClient(app), store, memory, archive


def _stock_session(client, code: str, name: str):
    workspace = client.post("/console/workspaces", json={
        "kind": "stock", "code": code, "name": name,
    }).json()["workspace"]
    session = client.post("/console/sessions", json={
        "workspace_id": workspace["id"], "title": name,
    }).json()["meta"]
    return workspace, session


def _params(workspace, session):
    return {"workspace_id": workspace["id"], "session_id": session["id"]}


def test_audit_detects_conservative_issue_types_and_frontend_contract(tmp_path, monkeypatch):
    client, store, memory, _archive = _client(tmp_path, monkeypatch)
    workspace, session = _stock_session(client, "000636", "风华高科")
    memory.parent.mkdir(parents=True, exist_ok=True)
    memory.write_text(
        "- [2025-01-01] (capability-gap) 当前能力缺口：暂不支持图片发送\n"
        "- [2026-08-01] (same-key) 第一种口径\n"
        "- [2026-08-02] (same-key) 第二种口径\n"
        "- [2026-08-03] 完全重复的研究结论\n"
        "- [2026-08-04] (risk-rule) 全局风险口径\n",
        encoding="utf-8",
    )
    workspace_path = store.root / "workspace_memory" / workspace["id"] / "memory.md"
    workspace_path.parent.mkdir(parents=True)
    workspace_path.write_text(
        "- [2026-08-05] 完全重复的研究结论\n"
        "- [2026-08-06] (risk-rule) 风华高科空间风险口径\n",
        encoding="utf-8",
    )

    response = client.get("/console/memory-insights", params=_params(workspace, session))
    assert response.status_code == 200
    payload = response.json()
    assert payload["read_only_audit"] is True
    assert payload["summary"]["status"] == "risk"
    assert payload["summary"]["telemetry"]["status"] == "warming_up"
    assert not any(row["type"] == "low_use" for row in payload["insights"])
    by_type = {}
    for row in payload["insights"]:
        by_type.setdefault(row["type"], []).append(row)
        assert set(row) >= {
            "id", "severity", "type", "title", "description", "evidence",
            "impact", "memory_ids", "suggested_actions", "dismissed", "created_at",
        }
        assert isinstance(row["impact"], dict)
        assert row["impact"]["count"] == len(row["memory_ids"])
        assert all(action["kind"] in {"locate", "edit"}
                   and action.get("memory_id") for action in row["suggested_actions"])
    assert {"exact_duplicate", "key_conflict", "potential_stale"} <= set(by_type)
    assert any(row["severity"] == "critical" for row in by_type["key_conflict"])
    assert any(row["severity"] == "warning" for row in by_type["key_conflict"])
    counts = payload["summary"]["counts"]
    assert counts["critical"] >= 1 and counts["warning"] >= 1 and counts["info"] >= 1


def test_dismiss_restore_resolve_and_edit_invalidate_old_fingerprint(tmp_path, monkeypatch):
    client, store, memory, _archive = _client(tmp_path, monkeypatch)
    memory.write_text(
        "- [2025-01-01] (capability-gap) 当前能力缺口：旧图片发送状态\n",
        encoding="utf-8",
    )
    issue = next(row for row in client.get("/console/memory-insights").json()["insights"]
                 if row["type"] == "potential_stale")
    dismissed = client.post(
        f"/console/memory-insights/{issue['id']}/dismiss", json={"dismissed": True},
    )
    assert dismissed.status_code == 200 and dismissed.json()["insight"]["dismissed"] is True
    relisted = client.get("/console/memory-insights").json()
    closed = next(row for row in relisted["insights"] if row["id"] == issue["id"])
    assert closed["status"] == "dismissed" and closed["dismissed"] is True
    assert relisted["summary"]["counts"]["warning"] == 0
    assert relisted["summary"]["counts"]["dismissed"] == 1

    restored = client.post(
        f"/console/memory-insights/{issue['id']}/dismiss", json={"dismissed": False},
    ).json()["insight"]
    assert restored["status"] == "open" and restored["dismissed"] is False
    resolved = client.post(
        f"/console/memory-insights/{issue['id']}/status", json={"status": "resolved"},
    ).json()["insight"]
    assert resolved["resolved"] is True and resolved["dismissed"] is True

    target = client.get("/console/memories").json()["memories"][0]
    edited = client.patch(
        f"/console/memories/{target['id']}",
        json={"text": "当前能力缺口：新的图片发送状态", "etag": target["etag"]},
    )
    assert edited.status_code == 200
    current = client.get("/console/memory-insights").json()["insights"]
    replacement = next(row for row in current if row["type"] == "potential_stale")
    assert replacement["id"] != issue["id"]
    assert replacement["status"] == "open" and replacement["dismissed"] is False
    assert (store.root / "memory.insight.json").exists()
    assert (store.root / "memory.insight.json.bak").exists()


def test_scope_authorization_and_subject_mismatch_are_derived_from_session(tmp_path, monkeypatch):
    client, store, _memory, _archive = _client(tmp_path, monkeypatch)
    first_ws, first_session = _stock_session(client, "000636", "风华高科")
    second_ws, _second_session = _stock_session(client, "600667", "太极实业")
    workspace_path = store.root / "workspace_memory" / first_ws["id"] / "memory.md"
    workspace_path.parent.mkdir(parents=True)
    workspace_path.write_text(
        "- [2026-08-01] (risk) 风华高科风险结论\n", encoding="utf-8",
    )
    # Initial governance read freezes the subject binding.
    assert client.get("/console/memories", params=_params(first_ws, first_session)).status_code == 200
    changed = client.patch(
        f"/console/workspaces/{first_ws['id']}", json={"code": "603929"},
    )
    assert changed.status_code == 200
    report = client.get(
        "/console/memory-insights", params=_params(first_ws, first_session),
    ).json()
    mismatch = next(row for row in report["insights"] if row["type"] == "subject_mismatch")
    assert mismatch["severity"] == "warning"
    assert "SZ000636" in " ".join(mismatch["evidence"])

    assert client.get(
        "/console/memory-insights", params={"workspace_id": first_ws["id"]},
    ).status_code == 403
    assert client.get(
        "/console/memory-insights",
        params={"session_id": first_session["id"], "workspace_id": second_ws["id"]},
    ).status_code == 403


def test_actual_recall_usage_and_prompt_warning_are_best_effort(tmp_path, monkeypatch):
    client, store, memory, _archive = _client(tmp_path, monkeypatch)
    memory.write_text(
        "- [2025-01-01] (capability-gap) 当前能力缺口：图片发送仍未接通\n",
        encoding="utf-8",
    )
    session = client.post("/console/sessions", json={}).json()["meta"]
    insight = MemoryInsight(store)
    block, hits = _memory_context(
        session["id"], session["workspace_id"], "当前图片发送能力缺口是什么",
        store=store, insight=insight,
    )
    assert hits and hits[0]["requires_verification"] is True
    assert hits[0]["governance_warnings"][0]["type"] == "potential_stale"
    assert "[记忆治理警示·必须复核]" in block
    assert "可能过期" in block and "必须核验当前事实" in block

    usage = json.loads((store.root / "memory.recall.usage.json").read_text(encoding="utf-8"))
    assert usage["total_queries"] == 1 and usage["total_hits"] >= 1
    assert usage["memories"][hits[0]["id"]]["last_recalled_at"]


def test_low_use_requires_real_per_memory_observation_window(tmp_path, monkeypatch):
    client, store, memory, _archive = _client(tmp_path, monkeypatch)
    memory.write_text("- [2025-01-01] 一条无主题键的低频笔记\n", encoding="utf-8")
    session = client.post("/console/sessions", json={}).json()["meta"]
    insight = MemoryInsight(store)
    insight.record_recall(
        session_id=session["id"], workspace_id=session["workspace_id"],
        query="完全不相关的问题", hits=[],
    )
    warming = insight.audit(
        session_id=session["id"], workspace_id=session["workspace_id"],
    )
    assert warming["summary"]["telemetry"]["status"] == "warming_up"
    assert not any(row["type"] == "low_use" for row in warming["insights"])

    usage_path = store.root / "memory.recall.usage.json"
    usage = json.loads(usage_path.read_text(encoding="utf-8"))
    memory_id = next(iter(usage["memories"]))
    usage.update({
        "first_observed_at": "2026-06-01T00:00:00",
        "last_observed_at": "2026-08-01T00:00:00",
        "total_queries": 20,
    })
    usage["memories"][memory_id].update({
        "first_observed_at": "2026-06-01T00:00:00",
        "last_observed_at": "2026-08-01T00:00:00",
        "query_opportunities": 20,
        "hit_count": 0,
    })
    usage_path.write_text(json.dumps(usage, ensure_ascii=False), encoding="utf-8")
    ready = insight.audit(
        session_id=session["id"], workspace_id=session["workspace_id"],
    )
    assert ready["summary"]["telemetry"]["status"] == "ready"
    low_use = next(row for row in ready["insights"] if row["type"] == "low_use")
    assert low_use["memory_ids"] == [memory_id]
    assert "20" in " ".join(low_use["evidence"])


def test_insight_failure_never_blocks_deterministic_recall(tmp_path, monkeypatch):
    client, store, memory, _archive = _client(tmp_path, monkeypatch)
    memory.write_text(
        "- [2026-08-01] (stable-preference) 用户偏好月频回测\n",
        encoding="utf-8",
    )
    session = client.post("/console/sessions", json={}).json()["meta"]

    class _BrokenInsight:
        def annotate_hits(self, *_args, **_kwargs):
            raise OSError("governance unavailable")

        def record_recall(self, *_args, **_kwargs):
            raise OSError("telemetry unavailable")

    block, hits = _memory_context(
        session["id"], session["workspace_id"], "我的回测频率偏好",
        store=store, insight=_BrokenInsight(),
    )
    assert hits and hits[0]["key"] == "stable-preference"
    assert "用户偏好月频回测" in block


def test_usage_sidecar_falls_back_to_atomic_backup(tmp_path, monkeypatch):
    client, store, memory, _archive = _client(tmp_path, monkeypatch)
    memory.write_text("- [2026-08-01] 一条普通记忆\n", encoding="utf-8")
    session = client.post("/console/sessions", json={}).json()["meta"]
    insight = MemoryInsight(store)
    kwargs = {
        "session_id": session["id"],
        "workspace_id": session["workspace_id"],
        "query": "无关问题",
        "hits": [],
    }
    insight.record_recall(**kwargs)
    insight.record_recall(**kwargs)
    usage_path = store.root / "memory.recall.usage.json"
    backup_path = store.root / "memory.recall.usage.json.bak"
    assert backup_path.exists()
    usage_path.write_text("{broken", encoding="utf-8")

    report = insight.audit(
        session_id=session["id"], workspace_id=session["workspace_id"],
    )
    assert report["ok"] is True
    assert report["summary"]["telemetry"]["total_queries"] == 1
