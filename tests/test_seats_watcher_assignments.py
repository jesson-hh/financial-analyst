"""Single-owner Seats watcher assignments and evidence fail-closed contracts."""
from __future__ import annotations

import asyncio
import json
from datetime import datetime

import pytest

from guanlan_v2.seats import watch_assignments as WA
from guanlan_v2.seats import watcher as W


def _strategy(path, *, sid, name=None, bind=(), exec_tf="day", cadence="hourly"):
    row = {
        "id": sid,
        "type": "strategy",
        "name": name or sid,
        "bind": list(bind),
        "clock": {"execTF": exec_tf, "decisionFreq": cadence},
        "creed": "c",
        "refs": [],
    }
    path.write_text(json.dumps(row, ensure_ascii=False), encoding="utf-8")
    return row


def test_legacy_migration_is_single_owner_and_filename_deterministic(tmp_path):
    archive = tmp_path / "archive"
    archive.mkdir()
    _strategy(archive / "strat_b.json", sid="s_b", bind=["SZ300750"])
    _strategy(archive / "strat_a.json", sid="s_a", bind=["300750"])
    store = tmp_path / "assignments.json"

    rows = WA.migrate_legacy_assignments(path=store, archive_dir=archive)
    assert len(rows) == 1
    row = rows[0]
    assert row["code"] == "300750" and row["strategy_id"] == "s_a"
    assert row["source"] == "legacy_bind" and row["revision"] == 1
    assert row["migration_candidates"] == ["s_a", "s_b"]
    assert row["needs_review"] is True and row["status"] == "active"
    assert store.exists()

    # One-time migration: later bind edits cannot silently replace ownership.
    _strategy(archive / "strat_0.json", sid="s_0", bind=["300750"])
    again = WA.list_assignments(path=store, archive_dir=archive)
    assert again[0]["strategy_id"] == "s_a"


def test_legacy_5min_becomes_real_effective_evidence_without_day_fallback(tmp_path):
    archive = tmp_path / "archive"
    archive.mkdir()
    _strategy(archive / "strat_a.json", sid="s_a", bind=["600519"], exec_tf="5min")
    store = tmp_path / "assignments.json"
    row = WA.migrate_legacy_assignments(path=store, archive_dir=archive)[0]
    assert row["requested_evidence_tf"] == "5min"
    assert row["effective_evidence_tf"] == "5min"
    assert row["status"] == "active" and row["runnable"] is True
    assert WA.runtime_assignments(path=store, archive_dir=archive)[0]["assignment"][
        "effective_evidence_tf"] == "5min"


def test_upsert_revision_unique_owner_and_disable_does_not_edit_bind(tmp_path):
    archive = tmp_path / "archive"
    archive.mkdir()
    original = _strategy(archive / "strat_a.json", sid="s_a", bind=["600519"])
    _strategy(archive / "strat_b.json", sid="s_b", bind=[])
    store = tmp_path / "assignments.json"

    first = WA.upsert_assignment("SH600519", {
        "enabled": True, "strategy_id": "s_b", "evidence_tf": "30min",
        "decision_cadence": "30min", "thinking_mode": "deep",
    }, path=store, archive_dir=archive)
    assert first["strategy_id"] == "s_b" and first["revision"] == 2
    assert first["effective_evidence_tf"] == "30min" and first["status"] == "active"

    stopped = WA.upsert_assignment("600519", {
        "enabled": False, "strategy_id": "s_b", "evidence_tf": "30min",
        "decision_cadence": "30min", "thinking_mode": "deep", "revision": 2,
    }, path=store, archive_dir=archive)
    assert stopped["revision"] == 3 and stopped["status"] == "disabled"
    assert json.loads((archive / "strat_a.json").read_text(encoding="utf-8")) == original

    with pytest.raises(WA.RevisionConflict):
        WA.upsert_assignment("600519", {
            "enabled": True, "strategy_id": "s_a", "evidence_tf": "day",
            "decision_cadence": "daily", "revision": 1,
        }, path=store, archive_dir=archive)


def test_missing_strategy_and_corrupt_store_fail_closed(tmp_path, monkeypatch):
    archive = tmp_path / "archive"
    archive.mkdir()
    store = tmp_path / "assignments.json"
    with pytest.raises(WA.AssignmentError, match="strategy_id"):
        WA.upsert_assignment("300750", {
            "enabled": True, "strategy_id": "gone", "evidence_tf": "day",
            "decision_cadence": "daily",
        }, path=store, archive_dir=archive)

    store.write_text("{broken", encoding="utf-8")
    monkeypatch.setattr(W, "ASSIGNMENTS_PATH", store)
    monkeypatch.setattr(W, "ARCHIVE_DIR", archive)
    assert W.watching_codes() == []
    with pytest.raises(WA.AssignmentError):
        W.assignment_items()


def test_watcher_payload_uses_assignment_evidence_and_provenance(monkeypatch):
    monkeypatch.setattr(W, "_hold_entry_for", lambda code: None)
    monkeypatch.setattr(W, "_resolve_refs", lambda refs: ([], []))
    monkeypatch.setattr(W, "_industry_for", lambda code: "电子")
    cw = {
        "code": "300750", "strategy_id": "s1", "name": "盘中策略",
        "creed": "c", "refs": [], "w": 0, "pa": False, "pa_method": "",
        "requested_evidence_tf": "30min", "effective_evidence_tf": "30min",
        "decision_cadence": "30min", "thinking_mode": "deep",
        "assignment_revision": 7,
    }
    payload = W._build_payload(cw, datetime(2026, 8, 14, 14, 32), {"name": "宁德"})
    assert payload["date"] == "2026-08-14T14:32"
    assert payload["freq"] == "30min" and payload["mode"] == "deep"
    assert payload["requested_evidence_tf"] == payload["effective_evidence_tf"] == "30min"
    assert payload["decision_cadence"] == "30min" and payload["assignment_revision"] == 7


def test_assignment_api_list_upsert_disable_and_status_compat(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from guanlan_v2.seats import api as seats_api

    archive = tmp_path / "archive"
    archive.mkdir()
    _strategy(archive / "strat_a.json", sid="s_a", bind=[])
    monkeypatch.setattr(W, "ASSIGNMENTS_PATH", tmp_path / "assignments.json")
    monkeypatch.setattr(W, "ARCHIVE_DIR", archive)
    monkeypatch.setattr(W, "STATE_PATH", tmp_path / "watch.json")
    monkeypatch.setattr(W, "_is_market_open", lambda now: False)

    app = FastAPI()
    app.include_router(seats_api.build_seats_router())
    client = TestClient(app)

    assert client.get("/seats/watch/assignments").json() == {
        "ok": True,
        "assignments": [],
        "evidence_tf_values": ["1min", "30min", "5min", "day"],
        "effective_evidence_tf_values": ["1min", "30min", "5min", "day"],
        "decision_cadence_values": ["daily", "60min", "30min", "10min"],
        "thinking_mode_values": ["fast", "deep"],
    }
    put = client.put("/seats/watch/assignments/SH300750", json={
        "enabled": True, "strategy_id": "s_a", "evidence_tf": "1min",
        "decision_cadence": "10min", "thinking_mode": "fast",
    }).json()
    assert put["ok"] is True
    assert put["assignment"]["requested_evidence_tf"] == "1min"
    assert put["assignment"]["effective_evidence_tf"] == "1min"
    assert put["assignment"]["status"] == "active"

    status = client.get("/seats/watch/status").json()
    assert status["ok"] is True and status["watching"] == ["300750"]
    assert status["assignment_items"][0]["strategy_id"] == "s_a"
    stopped = client.put("/seats/watch/assignments/300750", json={
        "enabled": False, "strategy_id": "s_a", "evidence_tf": "1min",
        "decision_cadence": "10min", "revision": 1,
    }).json()
    assert stopped["ok"] is True and stopped["assignment"]["status"] == "disabled"


def test_assignment_migration_route_is_explicit_and_idempotent(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from guanlan_v2.seats import api as seats_api

    archive = tmp_path / "archive"
    archive.mkdir()
    _strategy(archive / "strat_old.json", sid="strat_old", bind=["600519"])
    store = tmp_path / "assignments.json"
    monkeypatch.setattr(W, "ASSIGNMENTS_PATH", store)
    monkeypatch.setattr(W, "ARCHIVE_DIR", archive)
    app = FastAPI()
    app.include_router(seats_api.build_seats_router())
    client = TestClient(app)

    # Ordinary reads expose migration candidates but never freeze them.
    before = client.get("/seats/watch/assignments").json()
    assert before["assignments"][0]["strategy_id"] == "strat_old"
    assert not store.exists()
    migrated = client.post("/seats/watch/assignments/migrate").json()
    assert migrated["ok"] is True and store.exists()
    assert migrated["assignments"][0]["strategy_id"] == "strat_old"
    # Later bind edits cannot rewrite the now-authoritative assignment.
    _strategy(archive / "strat_new.json", sid="strat_new", bind=["600519"])
    again = client.post("/seats/watch/assignments/migrate").json()
    assert again["assignments"][0]["strategy_id"] == "strat_old"


def test_explicit_empty_migration_is_authoritative_and_never_reawakens_bind(
        tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from guanlan_v2.seats import api as seats_api

    archive = tmp_path / "archive"
    archive.mkdir()
    store = tmp_path / "assignments.json"
    monkeypatch.setattr(W, "ASSIGNMENTS_PATH", store)
    monkeypatch.setattr(W, "ARCHIVE_DIR", archive)
    app = FastAPI()
    app.include_router(seats_api.build_seats_router())
    client = TestClient(app)

    first = client.post("/seats/watch/assignments/migrate").json()
    assert first["ok"] is True and first["assignments"] == [] and store.exists()
    # Explicit empty migration is a user-visible migration decision.  A later
    # legacy bind must not silently turn watching back on.
    _strategy(archive / "strat_late.json", sid="strat_late", bind=["600519"])
    second = client.post("/seats/watch/assignments/migrate").json()
    assert second["ok"] is True and second["assignments"] == []
    assert WA.runtime_assignments(path=store, archive_dir=archive) == []


def test_decide_missing_minute_evidence_fails_closed_without_day_fallback(monkeypatch):
    from guanlan_v2.seats import api as seats_api
    from guanlan_v2.seats import minute_evidence
    monkeypatch.setattr(minute_evidence, "stored_snapshot", lambda *a, **k: None)
    result = asyncio.run(seats_api._decide_impl({
        "code": "300750", "date": "2020-08-14T14:30", "freq": "1min",
        "requested_evidence_tf": "1min", "effective_evidence_tf": "1min",
    }))
    assert result["ok"] is False and result["reason"] == "minute_evidence_missing"
    assert result["requested_evidence_tf"] == "1min"
    assert result["effective_evidence_tf"] == "1min"


def test_watcher_30min_uses_five_minute_freshness_boundary():
    from guanlan_v2.seats.api import _watcher_minute_evidence_fresh as fresh
    assert fresh("2026-08-14 14:20", "2026-08-14 14:30", "5min") is True
    assert fresh("2026-08-14 14:19", "2026-08-14 14:30", "5min") is False


def test_minute_freshness_gate_covers_open_lunch_outage_and_pit_boundary():
    from guanlan_v2.seats.api import _watcher_minute_evidence_fresh as fresh
    # 09:30 with only yesterday's bar is not realtime evidence.
    assert fresh("2026-08-13 15:00", "2026-08-14 09:30") is False
    # The exchange did not trade over lunch; 11:30 is honest at 13:05.
    assert fresh("2026-08-14 11:30", "2026-08-14 13:05") is True
    # Once trading resumed, an old morning bar becomes an outage.
    assert fresh("2026-08-14 11:30", "2026-08-14 13:20") is False
    assert fresh("2026-08-14 14:27", "2026-08-14 14:30", "1min") is True
    assert fresh("2026-08-14 14:26", "2026-08-14 14:30", "1min") is False
    assert fresh("2026-08-14 14:20", "2026-08-14 14:30", "5min") is True
    assert fresh("2026-08-14 14:19", "2026-08-14 14:30", "5min") is False
    # A future bar is also invalid (clock/source skew rather than freshness).
    assert fresh("2026-08-14 14:32", "2026-08-14 14:30") is False


def test_tail_only_throttles_same_watcher_assignment_revision(tmp_path, monkeypatch):
    log = tmp_path / "decisions.jsonl"
    rows = [
        {"kind": "decide", "code": "SZ300750", "ts": "2026-08-14T10:00:00",
         "strategy_id": "s1", "source": "manual", "assignment_revision": 3},
        {"kind": "decide", "code": "SZ300750", "ts": "2026-08-14T10:10:00",
         "strategy_id": "s1", "source": "watcher", "assignment_revision": 2},
        {"kind": "decide", "code": "SZ300750", "ts": "2026-08-14T10:20:00",
         "strategy_id": "s2", "source": "watcher", "assignment_revision": 3},
        {"kind": "decide", "code": "SZ300750", "ts": "2026-08-14T10:30:00",
         "strategy_id": "s1", "source": "watcher", "assignment_revision": 3},
    ]
    log.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    monkeypatch.setattr(W, "DECISIONS_LOG", log)
    assert W._decisions_tail_production(
        "300750", strategy_id="s1", assignment_revision=3) == "2026-08-14T10:30:00"
    # Editing the assignment increments revision and makes it immediately due.
    assert W._decisions_tail_production(
        "300750", strategy_id="s1", assignment_revision=4) is None


def test_archive_remove_is_server_first_and_guards_active_and_disabled_assignments(
        tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from guanlan_v2.archive import api as archive_api

    archive = tmp_path / "archive"
    archive.mkdir()
    p1 = archive / "strat_s_a.json"
    _strategy(p1, sid="strat_s_a", bind=[])
    _strategy(archive / "strat_s_b.json", sid="strat_s_b", bind=[])
    store = tmp_path / "assignments.json"
    monkeypatch.setattr(archive_api, "_DIR", archive)
    monkeypatch.setattr(WA, "ASSIGNMENTS_PATH", store)

    first = WA.upsert_assignment("600519", {
        "enabled": True, "strategy_id": "strat_s_a", "evidence_tf": "day",
        "decision_cadence": "daily",
    }, path=store, archive_dir=archive)
    app = FastAPI()
    app.include_router(archive_api.build_archive_router())
    client = TestClient(app)

    blocked = client.post("/archive/remove", json={"id": "strat_s_a"})
    assert blocked.status_code == 409
    assert blocked.json()["reason"] == "strategy_assigned"
    assert blocked.json()["codes"] == ["600519"] and p1.exists()

    disabled = WA.upsert_assignment("600519", {
        "enabled": False, "strategy_id": "strat_s_a", "evidence_tf": "day",
        "decision_cadence": "daily", "revision": first["revision"],
    }, path=store, archive_dir=archive)
    blocked_disabled = client.post("/archive/remove", json={"id": "strat_s_a"})
    assert blocked_disabled.status_code == 409 and p1.exists()

    WA.upsert_assignment("600519", {
        "enabled": True, "strategy_id": "strat_s_b", "evidence_tf": "day",
        "decision_cadence": "daily", "revision": disabled["revision"],
    }, path=store, archive_dir=archive)
    removed = client.post("/archive/remove", json={"id": "strat_s_a"})
    assert removed.status_code == 200 and removed.json()["ok"] is True
    assert not p1.exists()


def test_archive_remove_fails_closed_for_broken_strategy_guard_but_not_research(
        tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from guanlan_v2.archive import api as archive_api

    archive = tmp_path / "archive"
    archive.mkdir()
    strategy = archive / "strat_s_a.json"
    _strategy(strategy, sid="strat_s_a", bind=[])
    research = archive / "r_a.json"
    research.write_text(json.dumps({"id": "r_a", "type": "research"}), encoding="utf-8")
    store = tmp_path / "assignments.json"
    store.write_text("{broken", encoding="utf-8")
    monkeypatch.setattr(archive_api, "_DIR", archive)
    monkeypatch.setattr(WA, "ASSIGNMENTS_PATH", store)

    app = FastAPI()
    app.include_router(archive_api.build_archive_router())
    client = TestClient(app)
    failed = client.post("/archive/remove", json={"id": "strat_s_a"})
    assert failed.status_code == 503 and strategy.exists()
    assert failed.json()["reason"] == "assignment_guard_unavailable"
    ok = client.post("/archive/remove", json={"id": "r_a"})
    assert ok.status_code == 200 and not research.exists()


def test_empty_legacy_read_does_not_freeze_before_archive_hydrates(tmp_path):
    archive = tmp_path / "archive"
    archive.mkdir()
    store = tmp_path / "assignments.json"
    assert WA.list_assignments(path=store, archive_dir=archive) == []
    assert not store.exists()
    _strategy(archive / "strat_late.json", sid="s_late", bind=["600519"])
    rows = WA.migrate_legacy_assignments(path=store, archive_dir=archive)
    assert rows[0]["strategy_id"] == "s_late" and store.exists()


def test_unattended_watcher_waits_for_explicit_archive_migration(tmp_path):
    archive = tmp_path / "archive"
    archive.mkdir()
    strategy = archive / "strat_late.json"
    _strategy(strategy, sid="s_late", bind=["600519"])
    store = tmp_path / "assignments.json"
    assert WA.runtime_assignments(path=store, archive_dir=archive) == []
    assert not store.exists()
    WA.migrate_legacy_assignments(path=store, archive_dir=archive)
    rows = WA.runtime_assignments(path=store, archive_dir=archive)
    assert rows[0]["assignment"]["strategy_id"] == "s_late"
    assert store.exists()
