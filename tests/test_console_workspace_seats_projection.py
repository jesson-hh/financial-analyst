"""Workspace-facing Seats projection contract.

These tests keep the Console Dock projection separate from the global Seats
control plane.  The endpoint is read-only, exact-subject scoped, and exposes a
strict allowlist rather than returning raw assignment/decision documents.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from guanlan_v2.console.api import build_console_router
from guanlan_v2.console.store import ConsoleStore


def _client(root: Path) -> tuple[TestClient, ConsoleStore]:
    app = FastAPI()
    store = ConsoleStore(root=root)
    app.include_router(
        build_console_router(store=store, agent_factory=lambda _sid: None)
    )
    return TestClient(app), store


def _seed_subject_projection(
    tmp_path: Path,
    monkeypatch: Any,
) -> dict[str, Any]:
    client, store = _client(tmp_path / "console")
    response = client.post("/console/workspaces", json={
        "name": "太极实业",
        "kind": "stock",
        "code": "600667.SH",
    })
    assert response.status_code == 200
    workspace = response.json()["workspace"]

    seats_root = tmp_path / "authoritative-seats"
    archive_dir = seats_root / "archive"
    archive_dir.mkdir(parents=True)
    assignment_path = seats_root / "seats_watch_assignments.json"
    decisions_path = seats_root / "seats_decisions.jsonl"
    realtime_path = seats_root / "realtime" / "seats_bars.sqlite3"

    (archive_dir / "strat_momentum.json").write_text(
        json.dumps({
            "id": "strat_momentum",
            "type": "strategy",
            "name": "动量 · 太极实业",
        }, ensure_ascii=False),
        encoding="utf-8",
    )
    assignment_path.write_text(
        json.dumps({
            "schema_version": 1,
            "migrated_at": workspace["created"],
            "assignments": {
                "600667": {
                    "code": "600667",
                    "enabled": True,
                    "strategy_id": "strat_momentum",
                    "strategy_name": "动量 · 太极实业",
                    "evidence_tf": "1min",
                    "decision_cadence": "10min",
                    "thinking_mode": "deep",
                    "revision": 3,
                    "source": "explicit",
                    "updated_at": workspace["created"],
                    "needs_review": False,
                    "migration_candidates": ["private-internal-detail"],
                },
            },
        }, ensure_ascii=False),
        encoding="utf-8",
    )

    event_ts = workspace["created"]
    rows = [
        {
            "id": "decide_1",
            "ts": event_ts,
            "kind": "decide",
            "code": "600667",
            "name": "太极实业",
            "source": "watcher",
            "strategy_id": "strat_momentum",
            "strategy_name": "动量 · 太极实业",
            "mode": "deep",
            "freq": "1min",
            "effective_evidence_tf": "1min",
            "decision_cadence": "10min",
            "assignment_revision": 3,
            "direction": "long",
            "confidence": 0.71,
            "rationale": "量价结构转强",
            "key_evidence": ["最近十分钟放量"],
            "data_asof": "2026-08-15 10:00",
            "evidence_source": "pytdx+realtime_store",
            "data_ref": "seats-realtime://SH600667/1min?n=120&sha256=abc",
            "data_digest": "a" * 64,
            "data_quality": {
                "n": 120,
                "complete_n": 119,
                "private_diagnostic": "never",
            },
            "reasoning": "模型私有思维链不得投影",
            "workspace_memory": "空间私有记忆正文不得投影",
            "workspace_history_items": [{"text": "私有历史正文"}],
            "workspace_history_ref": {"workspace_id": workspace["id"]},
            "research": ["私有研究正文"],
            "factors_std": [{"secret": 1}],
            "bars": [{"close": 24.29}],
        },
        {
            "id": "decide_replay",
            "ts": event_ts,
            "kind": "decide",
            "code": "SH600667",
            "run_id": "run_backtest",
            "direction": "short",
            "reasoning": "replay",
        },
        {
            "id": "other_security",
            "ts": event_ts,
            "kind": "decide",
            "code": "SH600000",
            "direction": "short",
        },
        {
            "id": "order_1",
            "ts": event_ts,
            "kind": "order",
            "code": "600667.SH",
            "side": "买入",
            "tf": "5min",
            "logic": "AND",
            "triggers": [{
                "kind": "price",
                "op": ">=",
                "value": 24.5,
                "secret": "drop",
            }],
            "note": "突破后观察",
            "reasoning": "also private",
        },
        {
            "id": "before_workspace",
            "ts": "2000-01-01T00:00:00",
            "kind": "decide",
            "code": "600667",
            "direction": "long",
        },
    ]
    decisions_path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows)
        + "\n{bad-json\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(
        "guanlan_v2.console.seats_projection.ASSIGNMENTS_PATH",
        assignment_path,
    )
    monkeypatch.setattr(
        "guanlan_v2.console.seats_projection.STRATEGY_ARCHIVE_DIR",
        archive_dir,
    )
    monkeypatch.setattr(
        "guanlan_v2.console.seats_projection.DECISIONS_LOG",
        decisions_path,
    )
    monkeypatch.setattr(
        "guanlan_v2.console.seats_projection.REALTIME_DB_PATH",
        realtime_path,
    )
    return {
        "client": client,
        "store": store,
        "workspace": workspace,
        "assignment_path": assignment_path,
        "decisions_path": decisions_path,
        "realtime_path": realtime_path,
    }


def test_workspace_seats_projection_is_exact_scoped_redacted_and_excludes_runs(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    world = _seed_subject_projection(tmp_path, monkeypatch)
    workspace = world["workspace"]
    response = world["client"].get(
        f"/console/workspaces/{workspace['id']}/seats",
        params={"limit": 10},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True and payload["available"] is True
    assert payload["workspace"] == {
        "id": workspace["id"],
        "display_name": "太极实业",
        "kind": "stock",
        "code": "SH600667",
        "archived": False,
    }
    assert payload["code"] == "SH600667"
    assert payload["exclude_runs"] is True
    assert payload["projection_scope"] == {
        "kind": "subject",
        "since": workspace["created"],
        "until": None,
    }
    assert payload["assignment"]["strategy_id"] == "strat_momentum"
    assert payload["assignment"]["effective_evidence_tf"] == "1min"
    assert "migration_candidates" not in payload["assignment"]
    assert [row["id"] for row in payload["decisions"]] == [
        "order_1",
        "decide_1",
    ]
    assert payload["decisions"][0]["triggers"] == [
        {"kind": "price", "op": ">=", "value": 24.5},
    ]
    assert payload["latest_snapshot"]["data_asof"] == "2026-08-15 10:00"

    serialized = json.dumps(payload, ensure_ascii=False)
    for private_value in (
        "reasoning",
        "workspace_memory",
        "workspace_history_items",
        "workspace_history_ref",
        "私有历史正文",
        "私有研究正文",
        "factors_std",
        "bars",
        "private_diagnostic",
        "private-internal-detail",
        "run_backtest",
        "other_security",
        "before_workspace",
    ):
        assert private_value not in serialized


def test_archived_workspace_seats_projection_is_read_only_and_pit_bounded(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    world = _seed_subject_projection(tmp_path, monkeypatch)
    client = world["client"]
    workspace = world["workspace"]

    archived_response = client.request(
        "DELETE", f"/console/workspaces/{workspace['id']}"
    )
    assert archived_response.status_code == 200

    assignment_doc = json.loads(
        world["assignment_path"].read_text(encoding="utf-8")
    )
    assignment_doc["assignments"]["600667"]["updated_at"] = (
        "2099-01-01T00:00:00"
    )
    assignment_doc["assignments"]["600667"]["revision"] = 4
    world["assignment_path"].write_text(
        json.dumps(assignment_doc, ensure_ascii=False),
        encoding="utf-8",
    )
    with world["decisions_path"].open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({
            "id": "after_archive",
            "ts": "2099-01-01T00:00:00",
            "kind": "decide",
            "code": "600667",
            "direction": "short",
            "data_asof": "2099-01-01 10:00",
        }, ensure_ascii=False) + "\n")

    # A current global cache may keep moving after the workspace is archived.
    # The archived projection must use only its bounded decision lineage.
    from guanlan_v2.seats.realtime_store import RealtimeBarStore

    realtime_store = RealtimeBarStore(world["realtime_path"])
    first_bar = datetime(2099, 1, 1, 9, 30)
    realtime_store.ingest("SH600667", "1min", [
        {
            "bar_time": (first_bar + timedelta(minutes=index)).isoformat(sep=" "),
            "open": 100 + index,
            "high": 100.2 + index,
            "low": 99.8 + index,
            "close": 100 + index,
            "volume": 1000 + index,
        }
        for index in range(3)
    ], source="pytdx", pulled_at="2099-01-01T02:00:00Z")

    response = client.get(f"/console/workspaces/{workspace['id']}/seats")
    assert response.status_code == 200
    payload = response.json()
    assert payload["available"] is True
    assert payload["archived"] is True and payload["read_only"] is True
    assert payload["assignment"] is None
    assert [row["id"] for row in payload["decisions"]] == [
        "order_1",
        "decide_1",
    ]
    assert payload["latest_snapshot"]["data_asof"] == "2026-08-15 10:00"
    assert payload["projection_scope"]["kind"] == "subject"
    assert payload["projection_scope"]["since"] == workspace["created"]
    assert payload["projection_scope"]["until"] is not None
    serialized = json.dumps(payload, ensure_ascii=False)
    assert "after_archive" not in serialized
    assert "2099-01-01 10:00" not in serialized


def test_workspace_seats_projection_fails_closed_for_unscoped_or_unsupported_subjects(
    tmp_path: Path,
) -> None:
    client, _store = _client(tmp_path)

    general = client.get("/console/workspaces/rw_general/seats")
    assert general.status_code == 200
    general_payload = general.json()
    assert general_payload["ok"] is True
    assert general_payload["available"] is False
    assert general_payload["reason"] == "workspace_has_no_security_code"
    assert general_payload["assignment"] is None
    assert general_payload["decisions"] == []

    hk_response = client.post("/console/workspaces", json={
        "name": "腾讯研究",
        "kind": "stock",
        "code": "00700",
    })
    assert hk_response.status_code == 200
    hk = hk_response.json()["workspace"]
    unsupported = client.get(f"/console/workspaces/{hk['id']}/seats")
    assert unsupported.status_code == 200
    unsupported_payload = unsupported.json()
    assert unsupported_payload["ok"] is True
    assert unsupported_payload["available"] is False
    assert unsupported_payload["reason"] == "seats_security_code_unsupported"
    assert unsupported_payload["assignment"] is None
    assert unsupported_payload["decisions"] == []


def test_workspace_seats_projection_rejects_ambiguous_active_security_owner(
    tmp_path: Path,
) -> None:
    client, store = _client(tmp_path)
    first = client.post("/console/workspaces", json={
        "name": "空间一",
        "kind": "stock",
        "code": "600000",
    }).json()["workspace"]
    second = client.post("/console/workspaces", json={
        "name": "空间二",
        "kind": "etf",
        "code": "510300",
    }).json()["workspace"]

    # Simulate a legacy/corrupt duplicate that predates today's uniqueness
    # checks.  File/list order must never choose an arbitrary owner.
    path = store.workspace_store.workspaces_dir / f"{second['id']}.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["code"] = "SH600000"
    path.write_text(json.dumps(raw), encoding="utf-8")

    response = client.get(f"/console/workspaces/{first['id']}/seats")
    assert response.status_code == 409
    assert response.json()["reason_code"] == "ambiguous_security_workspace"


def test_workspace_code_rebind_resets_subject_window_and_excludes_old_new_code_history(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    from guanlan_v2.console import workspaces as workspace_module

    clock = {"value": "2026-08-30T09:00:00"}
    monkeypatch.setattr(workspace_module, "_now", lambda: clock["value"])
    client, _store = _client(tmp_path / "console")

    clock["value"] = "2026-08-30T09:10:00"
    created = client.post("/console/workspaces", json={
        "name": "换绑研究",
        "kind": "stock",
        "code": "600000",
    })
    assert created.status_code == 200
    workspace = created.json()["workspace"]
    assert workspace["subject_since"] == "2026-08-30T09:10:00"

    seats_root = tmp_path / "authoritative-seats"
    archive_dir = seats_root / "archive"
    archive_dir.mkdir(parents=True)
    decisions_path = seats_root / "seats_decisions.jsonl"
    decisions_path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in [
            {
                "id": "new_code_before_rebind",
                "ts": "2026-08-30T09:30:00",
                "kind": "decide",
                "code": "SH600667",
                "direction": "long",
            },
            {
                "id": "new_code_after_rebind",
                "ts": "2026-08-30T10:01:00",
                "kind": "decide",
                "code": "600667.SH",
                "direction": "short",
            },
        ]) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "guanlan_v2.console.seats_projection.ASSIGNMENTS_PATH",
        seats_root / "missing-assignments.json",
    )
    monkeypatch.setattr(
        "guanlan_v2.console.seats_projection.STRATEGY_ARCHIVE_DIR",
        archive_dir,
    )
    monkeypatch.setattr(
        "guanlan_v2.console.seats_projection.DECISIONS_LOG",
        decisions_path,
    )
    monkeypatch.setattr(
        "guanlan_v2.console.seats_projection.REALTIME_DB_PATH",
        seats_root / "missing-realtime.sqlite3",
    )

    clock["value"] = "2026-08-30T10:00:00"
    rebound = client.patch(
        f"/console/workspaces/{workspace['id']}",
        json={"code": "600667.SH"},
    )
    assert rebound.status_code == 200
    updated = rebound.json()["workspace"]
    assert updated["code"] == "SH600667"
    assert updated["subject_since"] == "2026-08-30T10:00:00"
    assert updated["subject_since"] != workspace["subject_since"]
    assert updated["subject_until"] is None

    projection = client.get(
        f"/console/workspaces/{workspace['id']}/seats"
    ).json()
    assert projection["projection_scope"] == {
        "kind": "subject",
        "since": "2026-08-30T10:00:00",
        "until": None,
    }
    assert [row["id"] for row in projection["decisions"]] == [
        "new_code_after_rebind",
    ]
    assert "new_code_before_rebind" not in json.dumps(projection)


def test_workspace_code_normalizes_bj_920_and_is_unique_across_stock_and_etf(
    tmp_path: Path,
) -> None:
    client, _store = _client(tmp_path)
    first = client.post("/console/workspaces", json={
        "name": "北交所研究",
        "kind": "stock",
        "code": "920001",
    })
    assert first.status_code == 200
    assert first.json()["workspace"]["code"] == "BJ920001"

    duplicate = client.post("/console/workspaces", json={
        "name": "重复 ETF 标签",
        "kind": "etf",
        "code": "BJ920001",
    })
    assert duplicate.status_code == 400
    assert duplicate.json()["ok"] is False
    assert "已有一个未归档的研究空间" in duplicate.json()["reason"]


def test_workspace_watch_dock_is_wired_to_the_scoped_projection() -> None:
    root = Path(__file__).resolve().parents[1]
    data = (root / "ui" / "console" / "console-data.jsx").read_text(
        encoding="utf-8"
    )
    dock = (root / "ui" / "console" / "console-workspace-dock.jsx").read_text(
        encoding="utf-8"
    )

    assert "function wwWorkspaceSeats(wid, limit)" in data
    assert "'/seats?limit=' + encodeURIComponent(n)" in data
    assert "workspaceSeats: wwWorkspaceSeats" in data
    assert "window.WW.workspaceSeats(requestedWid, 50)" in dock
    assert "scopeMismatch" in dock
    assert "data.reason === 'seats_security_code_unsupported'" in dock
    assert "归档空间仅供查看" in dock
