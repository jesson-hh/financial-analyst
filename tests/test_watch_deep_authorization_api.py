from __future__ import annotations

import json
from datetime import date, datetime

from fastapi import FastAPI
from fastapi.testclient import TestClient

from guanlan_v2.seats import api as seats_api
from guanlan_v2.seats import watch_assignments
from guanlan_v2.seats import watcher
from guanlan_v2.seats.watch_deep_authorization import (
    HKT,
    WatchDeepAuthorizationStore,
)


WEEKEND_NOW = datetime(2026, 8, 15, 11, 0, tzinfo=HKT)
MONDAY_RUN = datetime(2026, 8, 17, 9, 30, tzinfo=HKT)
PRESET = "pipeline.luozi_deep_decide"
DIGEST = "a" * 64


def _client(tmp_path, monkeypatch, *, evidence_tf: str = "day",
            thinking_mode: str = "deep"):
    archive = tmp_path / "archive"
    archive.mkdir()
    (archive / "strat_watch.json").write_text(json.dumps({
        "id": "strat-watch",
        "type": "strategy",
        "name": "测试盯盘",
        "bind": [],
    }, ensure_ascii=False), encoding="utf-8")
    assignments = tmp_path / "assignments.json"
    row = watch_assignments.upsert_assignment(
        "300750",
        {
            "enabled": True,
            "strategy_id": "strat-watch",
            "evidence_tf": evidence_tf,
            "decision_cadence": "30min",
            "thinking_mode": thinking_mode,
        },
        path=assignments,
        archive_dir=archive,
    )
    monkeypatch.setattr(watcher, "ASSIGNMENTS_PATH", assignments)
    monkeypatch.setattr(watcher, "ARCHIVE_DIR", archive)
    monkeypatch.setattr(watcher, "_calendar_dates", lambda: {
        date(2026, 8, 14), date(2026, 8, 17), date(2026, 8, 18),
    })
    journal = tmp_path / "deep-auth.jsonl"
    store = WatchDeepAuthorizationStore(
        journal, global_daily_max_runs=2, clock=lambda: WEEKEND_NOW)
    monkeypatch.setattr(seats_api, "_watch_deep_store", lambda: store)
    monkeypatch.setattr(seats_api, "_watch_deep_now", lambda: WEEKEND_NOW)
    monkeypatch.setattr(seats_api, "_watch_deep_operator_actor", lambda: "human:ops")
    binding = {
        "preset_id": PRESET,
        "preset_record_digest": DIGEST,
        "requested_llm_invocations": 8,
    }
    monkeypatch.setattr(seats_api, "_watch_deep_preset_binding", lambda: dict(binding))
    app = FastAPI()
    app.include_router(seats_api.build_seats_router())
    return TestClient(app), store, journal, row, binding


def test_get_is_read_only_and_pre_authorizes_the_next_trading_session(tmp_path, monkeypatch):
    client, _, journal, row, _ = _client(tmp_path, monkeypatch)
    response = client.get("/seats/watch/assignments/300750/deep-authorization")
    assert response.status_code == 200
    body = response.json()
    assert body["eligible"] is True
    assert body["assignment_revision"] == row["revision"]
    assert body["session_date"] == "2026-08-17"
    assert body["valid_from"] == "2026-08-17T09:25:00+08:00"
    assert body["valid_until"] == "2026-08-17T15:00:00+08:00"
    assert "下一交易日" in body["session_label"]
    assert body["required_confirm_text"] == "授权3007502026-08-17 10席深研".replace(" ", "")
    assert body["authorization"] is None
    assert body["default_max_llm_invocations"] == 20
    assert body["global_deep_budget"] == {
        "session_date": "2026-08-17", "max_runs": 2,
        "runs_used": 0, "runs_remaining": 2,
    }
    assert body["global_deep_concurrency"]["max_runs"] == 1
    assert body["coordination_scope"] == "single_process_threads"
    assert not journal.exists(), "GET and choosing deep must never auto-authorize"


def test_issue_requires_exact_confirmation_and_fixed_one_run_v2_envelope(
        tmp_path, monkeypatch):
    client, _, journal, row, _ = _client(tmp_path, monkeypatch)
    status = client.get(
        "/seats/watch/assignments/300750/deep-authorization").json()
    base = {
        "expected_revision": row["revision"],
        "session_date": status["session_date"],
        "confirm_text": status["required_confirm_text"],
    }

    wrong = client.post(
        "/seats/watch/assignments/300750/deep-authorization",
        json={**base, "confirm_text": status["required_confirm_text"] + " "},
    )
    assert wrong.status_code == 400
    assert wrong.json()["reason"] == "confirm_text_mismatch"
    assert not journal.exists()

    widened = client.post(
        "/seats/watch/assignments/300750/deep-authorization",
        json={**base, "max_runs": 2, "max_llm": 20},
    )
    assert widened.status_code == 400
    assert not journal.exists()

    issued = client.post(
        "/seats/watch/assignments/300750/deep-authorization", json=base)
    assert issued.status_code == 200
    auth = issued.json()["authorization"]
    assert auth["code"] == "300750" and auth["assignment_revision"] == row["revision"]
    assert auth["max_runs"] == 1 and auth["max_llm_invocations"] == 20
    assert auth["status"] == "not_yet_valid"
    assert journal.exists() and len(journal.read_text(encoding="utf-8").splitlines()) == 1


def test_non_day_deep_assignment_is_visible_but_cannot_be_authorized(tmp_path, monkeypatch):
    client, _, journal, row, _ = _client(
        tmp_path, monkeypatch, evidence_tf="5min", thinking_mode="deep")
    status = client.get(
        "/seats/watch/assignments/300750/deep-authorization").json()
    assert status["eligible"] is False
    assert status["eligibility_reason"] == "unsupported_evidence_tf_for_10_agent"
    refused = client.post(
        "/seats/watch/assignments/300750/deep-authorization",
        json={
            "expected_revision": row["revision"],
            "confirm_text": status["required_confirm_text"],
        },
    )
    assert refused.status_code == 409
    assert not journal.exists()


def test_refrozen_preset_marks_old_authorization_stale(tmp_path, monkeypatch):
    client, _, _, row, binding = _client(tmp_path, monkeypatch)
    status = client.get(
        "/seats/watch/assignments/300750/deep-authorization").json()
    issued = client.post(
        "/seats/watch/assignments/300750/deep-authorization",
        json={
            "expected_revision": row["revision"],
            "confirm_text": status["required_confirm_text"],
        },
    )
    assert issued.status_code == 200
    binding["preset_record_digest"] = "b" * 64
    monkeypatch.setattr(seats_api, "_watch_deep_preset_binding", lambda: dict(binding))

    stale = client.get(
        "/seats/watch/assignments/300750/deep-authorization").json()
    assert stale["eligible"] is False
    assert stale["eligibility_reason"] == "authorization_preset_mismatch_requires_revoke"
    assert stale["authorization"]["status"] == "stale"
    assert stale["authorization"]["matches_current_preset"] is False


def test_canonical_revoke_and_runs_endpoint_are_auditable_only(tmp_path, monkeypatch):
    client, store, journal, row, _ = _client(tmp_path, monkeypatch)
    status = client.get(
        "/seats/watch/assignments/300750/deep-authorization").json()
    issued = client.post(
        "/seats/watch/assignments/300750/deep-authorization",
        json={
            "expected_revision": row["revision"],
            "confirm_text": status["required_confirm_text"],
        },
    ).json()
    auth_id = issued["authorization"]["authorization_id"]

    revoked = client.post(
        f"/seats/watch/deep-authorizations/{auth_id}/revoke",
        json={"reason": "test revoke"},
    )
    assert revoked.status_code == 200
    assert revoked.json()["authorization"]["status"] == "revoked"

    # A new standalone grant provides one run for the read-only audit endpoint.
    other_path = tmp_path / "runs.jsonl"
    run_store = WatchDeepAuthorizationStore(
        other_path, global_daily_max_runs=2, clock=lambda: MONDAY_RUN)
    run_store.issue(
        code="300750", assignment_revision=row["revision"], session_date="2026-08-17",
        preset_id=PRESET, preset_record_digest=DIGEST,
        valid_from=datetime(2026, 8, 17, 9, 25, tzinfo=HKT),
        valid_until=datetime(2026, 8, 17, 15, 0, tzinfo=HKT),
        max_runs=1, max_llm_invocations=8,
        issued_by="human:ops", reason="run audit test",
    )
    reservation = run_store.consume(
        run_id="deep-api-run", code="300750",
        assignment_revision=row["revision"], session_date="2026-08-17",
        preset_id=PRESET, preset_record_digest=DIGEST,
        requested_llm_invocations=8, now=MONDAY_RUN,
    )
    assert reservation is not None
    run_store.settle(
        reservation.reservation_id, actual_llm_invocations=7,
        outcome="completed", settled_at=MONDAY_RUN,
    )
    monkeypatch.setattr(seats_api, "_watch_deep_store", lambda: run_store)
    runs = client.get("/seats/watch/assignments/300750/deep-runs")
    assert runs.status_code == 200
    assert len(runs.json()["runs"]) == 1
    assert runs.json()["runs"][0]["run_id"] == "deep-api-run"
    assert len(other_path.read_text(encoding="utf-8").splitlines()) == 3
    assert len(journal.read_text(encoding="utf-8").splitlines()) == 2


def test_api_cannot_reissue_an_assignment_session_after_its_run_was_spent(
        tmp_path, monkeypatch):
    client, store, _, row, _ = _client(tmp_path, monkeypatch)
    status = client.get(
        "/seats/watch/assignments/300750/deep-authorization").json()
    request = {
        "expected_revision": row["revision"],
        "session_date": status["session_date"],
        "confirm_text": status["required_confirm_text"],
    }
    first = client.post(
        "/seats/watch/assignments/300750/deep-authorization", json=request)
    assert first.status_code == 200
    reservation = store.consume(
        run_id="spent-api-scope", code="300750",
        assignment_revision=row["revision"], session_date="2026-08-17",
        preset_id=PRESET, preset_record_digest=DIGEST,
        requested_llm_invocations=8, now=MONDAY_RUN,
    )
    assert reservation is not None
    store.settle(
        reservation.reservation_id,
        actual_llm_invocations=8,
        outcome="completed",
        settled_at=MONDAY_RUN,
    )

    second = client.post(
        "/seats/watch/assignments/300750/deep-authorization", json=request)
    assert second.status_code == 409
    assert second.json()["reason"] == "assignment_session_already_consumed"
    assert second.json()["scope_consumed"] is True
