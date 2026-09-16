"""Phase-two Weiwo memory governance API."""
from __future__ import annotations

import json

from fastapi import FastAPI
from fastapi.testclient import TestClient

from guanlan_v2.console import tools as ct
from guanlan_v2.console.api import _memory_context, build_console_router
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


def test_list_searches_all_accessible_markdown_scopes_with_stable_ids(tmp_path, monkeypatch):
    client, store, memory, archive = _client(tmp_path, monkeypatch)
    workspace, session = _stock_session(client, "000636", "风华高科")
    memory.write_text(
        "- [2026-08-20] (risk-style) 全局偏好：风险优先\n",
        encoding="utf-8",
    )
    archive.write_text(
        "# 帷幄记忆归档\n- [2026-01-02] 旧版风华高科研究方法\n",
        encoding="utf-8",
    )
    workspace_path = (
        store.root / "workspace_memory" / workspace["id"] / "memory.md"
    )
    workspace_path.parent.mkdir(parents=True)
    workspace_path.write_text(
        "- [2026-08-21] (risk) 风华高科跌破五日线需复核\n",
        encoding="utf-8",
    )
    notes = store.sessions_dir / session["id"] / "notes.md"
    notes.write_text(
        "- [2026-08-22] 本会话继续验证五日线上升条件\n",
        encoding="utf-8",
    )

    response = client.get("/console/memories", params={
        **_params(workspace, session), "include_archived": 1,
    })
    assert response.status_code == 200
    payload = response.json()
    assert payload["counts"]["total"] == 4
    assert {row["scope"] for row in payload["memories"]} == {
        "global", "global_archive", "workspace", "session",
    }
    by_scope = {row["scope"]: row for row in payload["memories"]}
    assert by_scope["workspace"]["scope_ref"] == workspace["id"]
    assert by_scope["workspace"]["subject_code"] == "SZ000636"
    assert by_scope["workspace"]["etag"]
    assert by_scope["global_archive"]["read_only"] is True

    # The sidecar, not line number/content, is authoritative for management
    # ids.  Re-listing returns the exact same ids.
    second = client.get("/console/memories", params={
        **_params(workspace, session), "include_archived": 1,
    }).json()
    assert {r["scope"]: r["id"] for r in second["memories"]} == {
        r["scope"]: r["id"] for r in payload["memories"]
    }

    searched = client.get("/console/memories", params={
        **_params(workspace, session), "include_archived": 1, "q": "五日线",
    }).json()
    assert searched["counts"]["total"] == 2
    assert {row["scope"] for row in searched["memories"]} == {"workspace", "session"}


def test_patch_pin_edit_etag_and_delete_exact_row(tmp_path, monkeypatch):
    client, store, memory, _archive = _client(tmp_path, monkeypatch)
    workspace, session = _stock_session(client, "600667", "太极实业")
    memory.write_text(
        "- [2026-08-20] (style) 原偏好\n"
        "- [2026-08-21] 不应被编辑的相邻行\n",
        encoding="utf-8",
    )
    params = _params(workspace, session)
    listed = client.get("/console/memories", params=params).json()["memories"]
    target = next(row for row in listed if row["key"] == "style")

    stale = client.patch(
        f"/console/memories/{target['id']}", params=params,
        json={"text": "新偏好", "etag": "stale"},
    )
    assert stale.status_code == 409
    assert "原偏好" in memory.read_text(encoding="utf-8")

    updated = client.patch(
        f"/console/memories/{target['id']}", params=params,
        json={"text": "新偏好", "key": "style-v2", "pinned": True,
              "etag": target["etag"]},
    )
    assert updated.status_code == 200
    row = updated.json()["memory"]
    assert row["id"] == target["id"]
    assert row["text"] == "新偏好" and row["key"] == "style-v2"
    assert row["pinned"] is True and row["etag"] != target["etag"]
    assert "不应被编辑的相邻行" in memory.read_text(encoding="utf-8")
    relisted = client.get("/console/memories", params=params).json()["memories"]
    assert relisted[0]["id"] == target["id"]  # pinned rows sort first

    deleted = client.delete(f"/console/memories/{target['id']}", params=params)
    assert deleted.status_code == 200
    assert deleted.json()["recoverable"] is True
    body = memory.read_text(encoding="utf-8")
    assert "新偏好" not in body and "不应被编辑的相邻行" in body
    trash = (store.root / "memory.admin.trash.jsonl").read_text(encoding="utf-8")
    assert target["id"] in trash and "新偏好" in trash
    assert client.patch(
        f"/console/memories/{target['id']}", params=params, json={"pinned": False},
    ).status_code == 404


def test_copy_promotes_without_deleting_source_and_rejects_cross_stock(tmp_path, monkeypatch):
    client, store, memory, _archive = _client(tmp_path, monkeypatch)
    first_ws, first_session = _stock_session(client, "000636", "风华高科")
    second_ws, second_session = _stock_session(client, "603929", "亚翔集成")
    first_notes = store.sessions_dir / first_session["id"] / "notes.md"
    first_notes.write_text(
        "- [2026-08-22] (trend-rule) 五日线持续上升且回踩十日线\n",
        encoding="utf-8",
    )
    first_params = _params(first_ws, first_session)
    source = next(
        row for row in client.get("/console/memories", params=first_params).json()["memories"]
        if row["scope"] == "session"
    )

    promoted = client.post(
        f"/console/memories/{source['id']}/copy", params=first_params,
        json={"target_scope": "workspace", "workspace_id": first_ws["id"],
              "session_id": first_session["id"]},
    )
    assert promoted.status_code == 200
    receipt = promoted.json()
    assert receipt["source_retained"] is True
    assert first_notes.exists() and "trend-rule" in first_notes.read_text(encoding="utf-8")
    workspace_path = store.root / "workspace_memory" / first_ws["id"] / "memory.md"
    assert "trend-rule" in workspace_path.read_text(encoding="utf-8")

    # A caller cannot use a Fenghua-bound session to write the same memory to
    # Yaxiang's stock workspace, even when it supplies that workspace id twice.
    crossed = client.post(
        f"/console/memories/{source['id']}/copy", params=first_params,
        json={"target_scope": "workspace", "workspace_id": second_ws["id"],
              "session_id": first_session["id"]},
    )
    assert crossed.status_code == 403
    assert not (
        store.root / "workspace_memory" / second_ws["id"] / "memory.md"
    ).exists()

    # The source id is not addressable through another stock's session context.
    inaccessible = client.patch(
        f"/console/memories/{source['id']}",
        params=_params(second_ws, second_session), json={"text": "串写"},
    )
    assert inaccessible.status_code == 404
    assert not memory.exists()


def test_global_archive_is_read_only_but_can_be_copied_back_to_global(tmp_path, monkeypatch):
    client, _store, memory, archive = _client(tmp_path, monkeypatch)
    workspace, session = _stock_session(client, "600667", "太极实业")
    archive.write_text(
        "# 帷幄记忆归档\n- [2025-12-01] 更早的无键研究习惯\n",
        encoding="utf-8",
    )
    params = {**_params(workspace, session), "include_archived": 1}
    archived = next(
        row for row in client.get("/console/memories", params=params).json()["memories"]
        if row["scope"] == "global_archive"
    )
    patch = client.patch(
        f"/console/memories/{archived['id']}", params=_params(workspace, session),
        json={"text": "不应直接改"},
    )
    assert patch.status_code == 409 and "只读" in patch.json()["reason"]
    delete = client.delete(
        f"/console/memories/{archived['id']}", params=_params(workspace, session),
    )
    assert delete.status_code == 409
    wrong_target = client.post(
        f"/console/memories/{archived['id']}/copy", params=_params(workspace, session),
        json={"target_scope": "session"},
    )
    assert wrong_target.status_code == 409

    restored = client.post(
        f"/console/memories/{archived['id']}/copy", params=_params(workspace, session),
        json={"target_scope": "global"},
    )
    assert restored.status_code == 200
    assert restored.json()["source_retained"] is True
    assert "更早的无键研究习惯" in memory.read_text(encoding="utf-8")
    assert "更早的无键研究习惯" in archive.read_text(encoding="utf-8")


def test_workspace_access_is_derived_from_session_and_archived_context_is_read_only(
        tmp_path, monkeypatch):
    client, store, _memory, _archive = _client(tmp_path, monkeypatch)
    workspace, session = _stock_session(client, "000636", "风华高科")
    path = store.root / "workspace_memory" / workspace["id"] / "memory.md"
    path.parent.mkdir(parents=True)
    path.write_text("- [2026-08-22] 空间研究结论\n", encoding="utf-8")

    missing_session = client.get("/console/memories", params={
        "workspace_id": workspace["id"],
    })
    assert missing_session.status_code == 403
    mismatch = client.get("/console/memories", params={
        "workspace_id": "rw_general", "session_id": session["id"],
    })
    assert mismatch.status_code == 403

    params = _params(workspace, session)
    row = next(r for r in client.get("/console/memories", params=params).json()["memories"]
               if r["scope"] == "workspace")
    assert client.request("DELETE", f"/console/workspaces/{workspace['id']}").status_code == 200
    listed = client.get("/console/memories", params=params).json()["memories"]
    archived_row = next(r for r in listed if r["id"] == row["id"])
    assert archived_row["read_only"] is True
    blocked = client.patch(
        f"/console/memories/{row['id']}", params=params, json={"pinned": True},
    )
    assert blocked.status_code == 409


def test_key_collision_never_silently_overwrites_target(tmp_path, monkeypatch):
    client, store, memory, _archive = _client(tmp_path, monkeypatch)
    workspace, session = _stock_session(client, "000636", "风华高科")
    memory.write_text("- [2026-08-20] (risk) 全局旧纪律\n", encoding="utf-8")
    notes = store.sessions_dir / session["id"] / "notes.md"
    notes.write_text("- [2026-08-22] (risk) 会话新纪律\n", encoding="utf-8")
    params = _params(workspace, session)
    source = next(r for r in client.get("/console/memories", params=params).json()["memories"]
                  if r["scope"] == "session")
    conflict = client.post(
        f"/console/memories/{source['id']}/copy", params=params,
        json={"target_scope": "global"},
    )
    assert conflict.status_code == 409
    assert "全局旧纪律" in memory.read_text(encoding="utf-8")
    assert "会话新纪律" not in memory.read_text(encoding="utf-8")


def test_admin_sidecar_is_valid_json_and_audit_is_append_only(tmp_path, monkeypatch):
    client, store, memory, _archive = _client(tmp_path, monkeypatch)
    memory.write_text("- [2026-08-20] 一条全局记忆\n", encoding="utf-8")
    row = client.get("/console/memories").json()["memories"][0]
    client.patch(f"/console/memories/{row['id']}", json={"pinned": True})
    client.patch(f"/console/memories/{row['id']}", json={"pinned": False})
    index = json.loads((store.root / "memory.admin.json").read_text(encoding="utf-8"))
    assert index["schema_version"] == 1 and row["id"] in index["items"]
    audit = (store.root / "memory.admin.audit.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(audit) == 2 and all(json.loads(line)["action"] == "patch" for line in audit)


def test_recall_receipt_reuses_management_id_after_edit(tmp_path, monkeypatch):
    client, store, memory, _archive = _client(tmp_path, monkeypatch)
    workspace, session = _stock_session(client, "000636", "风华高科")
    memory.write_text(
        "- [2026-08-19] 无关前置行\n"
        "- [2026-08-20] (hold-000636-cost) 风华高科成本62.83元\n",
        encoding="utf-8",
    )
    params = _params(workspace, session)
    listed = client.get("/console/memories", params=params).json()["memories"]
    target = next(row for row in listed if row["key"] == "hold-000636-cost")
    _block, hits = _memory_context(
        session["id"], workspace["id"], "风华高科000636成本", store=store,
    )
    assert any(hit["id"] == target["id"] for hit in hits)

    edited = client.patch(
        f"/console/memories/{target['id']}", params=params,
        json={"text": "风华高科成本调整为63.10元", "etag": target["etag"]},
    ).json()["memory"]
    assert edited["id"] == target["id"]
    _block, hits = _memory_context(
        session["id"], workspace["id"], "风华高科000636成本63.10", store=store,
    )
    assert any(hit["id"] == target["id"] for hit in hits)


def test_subject_binding_filters_memory_after_workspace_code_change(tmp_path, monkeypatch):
    client, store, _memory, _archive = _client(tmp_path, monkeypatch)
    workspace, session = _stock_session(client, "000636", "风华高科")
    path = store.root / "workspace_memory" / workspace["id"] / "memory.md"
    path.parent.mkdir(parents=True)
    path.write_text(
        "- [2026-08-22] (risk) 风华高科跌破五日线需复核\n",
        encoding="utf-8",
    )
    # First management read freezes the row's subject_code in the sidecar.
    row = next(r for r in client.get(
        "/console/memories", params=_params(workspace, session),
    ).json()["memories"] if r["scope"] == "workspace")
    assert row["subject_code"] == "SZ000636"

    changed = client.patch(
        f"/console/workspaces/{workspace['id']}", json={"code": "603929"},
    )
    assert changed.status_code == 200
    _block, hits = _memory_context(
        session["id"], workspace["id"], "风华高科风险", store=store,
    )
    assert not any(hit.get("id") == row["id"] for hit in hits)
    relisted = next(r for r in client.get(
        "/console/memories",
        params={"workspace_id": workspace["id"], "session_id": session["id"]},
    ).json()["memories"] if r["id"] == row["id"])
    assert relisted["subject_mismatch"] is True and relisted["editable"] is False


def test_pinned_unkeyed_memory_is_resident_and_survives_auto_curator(tmp_path, monkeypatch):
    client, store, memory, archive = _client(tmp_path, monkeypatch)
    memory.write_text("- [2026-08-20] 手动置顶的无键记忆\n", encoding="utf-8")
    row = client.get("/console/memories").json()["memories"][0]
    pinned = client.patch(
        f"/console/memories/{row['id']}",
        json={"pinned": True, "etag": row["etag"]},
    ).json()["memory"]
    assert pinned["pinned"] is True and pinned["resident"] is True

    monkeypatch.setattr(ct, "_CURATOR_TRIGGER_LINES", 2)
    assert ct.memory_write_impl("较新的普通记忆1", scope="global")["ok"]
    assert ct.memory_write_impl("较新的普通记忆2", scope="global")["ok"]
    assert "手动置顶的无键记忆" in memory.read_text(encoding="utf-8")
    assert archive.exists()
    assert "手动置顶的无键记忆" not in archive.read_text(encoding="utf-8")


def test_management_id_survives_curator_move_to_archive(tmp_path, monkeypatch):
    client, _store, memory, archive = _client(tmp_path, monkeypatch)
    memory.write_text(
        "- [2026-08-01] 最旧易逝记忆\n"
        "- [2026-08-02] 中间易逝记忆\n"
        "- [2026-08-03] 最新易逝记忆\n",
        encoding="utf-8",
    )
    before = next(
        row for row in client.get("/console/memories").json()["memories"]
        if row["text"] == "最旧易逝记忆"
    )
    from guanlan_v2.console.curator import consolidate_memory
    assert consolidate_memory(memory, archive, max_lines=2)["archived"] == 1
    after = next(
        row for row in client.get(
            "/console/memories", params={"include_archived": 1},
        ).json()["memories"] if row["text"] == "最旧易逝记忆"
    )
    assert after["scope"] == "global_archive"
    assert after["id"] == before["id"]


def test_corrupt_sidecar_recovers_from_backup_without_touching_markdown(tmp_path, monkeypatch):
    client, store, memory, _archive = _client(tmp_path, monkeypatch)
    original = "- [2026-08-20] 治理元数据恢复测试\n"
    memory.write_text(original, encoding="utf-8")
    row = client.get("/console/memories").json()["memories"][0]
    pinned = client.patch(
        f"/console/memories/{row['id']}",
        json={"pinned": True, "etag": row["etag"]},
    ).json()["memory"]
    client.patch(
        f"/console/memories/{row['id']}",
        json={"pinned": False, "etag": pinned["etag"]},
    )
    index_path = store.root / "memory.admin.json"
    backup_path = store.root / "memory.admin.json.bak"
    assert backup_path.exists()
    index_path.write_text("{broken", encoding="utf-8")
    recovered = client.get("/console/memories").json()["memories"][0]
    assert recovered["id"] == row["id"]
    assert memory.read_text(encoding="utf-8") == original


def test_deleted_memory_id_is_tombstoned_and_never_reused(tmp_path, monkeypatch):
    client, store, memory, _archive = _client(tmp_path, monkeypatch)
    original = "- [2026-08-20] (same-line) 可被重新写入的同一行\n"
    memory.write_text(original, encoding="utf-8")
    first = client.get("/console/memories").json()["memories"][0]
    deleted = client.delete(f"/console/memories/{first['id']}")
    assert deleted.status_code == 200 and deleted.json()["recoverable"] is True

    # Simulate a later legacy writer recreating the byte-identical row at the
    # same position.  Its identity must not collide with durable trash/audit.
    memory.write_text(original, encoding="utf-8")
    second = client.get("/console/memories").json()["memories"][0]
    assert second["id"] != first["id"]
    trash = (store.root / "memory.admin.trash.jsonl").read_text(encoding="utf-8")
    assert first["id"] in trash


def test_legacy_keyed_replace_refreshes_management_updated_at(tmp_path, monkeypatch):
    client, store, memory, _archive = _client(tmp_path, monkeypatch)
    memory.write_text("- [2026-08-01] (capability) 旧能力状态\n", encoding="utf-8")
    first = client.get("/console/memories").json()["memories"][0]
    index_path = store.root / "memory.admin.json"
    index = json.loads(index_path.read_text(encoding="utf-8"))
    index["items"][first["id"]]["updated_at"] = "2020-01-01T00:00:00"
    index_path.write_text(json.dumps(index, ensure_ascii=False), encoding="utf-8")

    assert ct.memory_write_impl("已核验的新能力状态", scope="global", key="capability")["ok"]
    second = client.get("/console/memories").json()["memories"][0]
    assert second["id"] == first["id"]
    assert second["text"] == "已核验的新能力状态"
    assert second["updated_at"] != "2020-01-01T00:00:00"
