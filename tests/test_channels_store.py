# -*- coding: utf-8 -*-
"""Channel SQLite fact store and domain-state-machine tests.

Every test uses an injected temporary database.  The production ``var`` tree is
never touched.
"""
from __future__ import annotations

import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pytest

from guanlan_v2.channels.models import (
    AutomationRunStatus,
    BindingStatus,
    BotStatus,
    OutboundPart,
    OutboxState,
    PairingStatus,
)
from guanlan_v2.channels.store import (
    ChannelConflict,
    ChannelStore,
    InvalidTransition,
    UnsafeAuditDetails,
)


UTC = timezone.utc


def _store(tmp_path) -> ChannelStore:
    return ChannelStore(tmp_path / "channels.sqlite3")


def _seed_bot(
    store: ChannelStore,
    *,
    name: str = "测试 Bot",
    bot_id: str = "101",
    draft_session_id: str | None = None,
) -> str:
    account_id = store.create_bot(name, draft_session_id=draft_session_id)
    store.activate_test_bot(account_id, telegram_bot_id=bot_id)
    return account_id


def _seed_binding(
    store: ChannelStore,
    *,
    session_id: str = "cs_a",
    bot_id: str = "101",
    cursor: int = 10,
):
    account_id = _seed_bot(store, bot_id=bot_id)
    return store.bind_for_test(
        session_id,
        account_id,
        user_id="1",
        chat_id="1",
        sync_from_event_id=cursor,
    )


def _pending_request(
    store: ChannelStore,
    *,
    bot_account_id: str,
    session_id: str,
    update_id: int,
    user_id: str,
    expires_at: datetime | None = None,
):
    now = datetime.now(UTC)
    return store.create_pairing_request(
        bot_account_id=bot_account_id,
        session_id=session_id,
        telegram_update_id=update_id,
        telegram_user_id=user_id,
        telegram_chat_id=user_id,
        code_salt=b"s" * 16,
        code_digest=(str(update_id).encode("ascii") * 32)[:32],
        code_hint="XY",
        expires_at=expires_at or now + timedelta(hours=1),
    )


def test_init_is_idempotent_and_configures_durable_sqlite(tmp_path):
    path = tmp_path / "nested" / "channels.sqlite3"
    first = ChannelStore(path)
    first.create_bot("保留我")
    first.init_db()
    first.init_db()
    ChannelStore(path)

    with first.connect() as conn:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
        assert conn.execute("PRAGMA synchronous").fetchone()[0] == 2
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 5000

        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        assert {
            "bot_accounts",
            "channel_bindings",
            "pairing_requests",
            "outbox",
            "automations",
            "automation_runs",
            "channel_audit",
            "credential_cleanup_queue",
        } <= tables
        indexes = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index'"
            )
        }
        assert {
            "uq_live_binding_session",
            "uq_live_binding_bot",
            "uq_live_telegram_bot",
            "uq_open_draft_session",
            "uq_outbox_source_part",
            "uq_automation_occurrence",
        } <= indexes
        assert conn.execute("SELECT COUNT(*) FROM bot_accounts").fetchone()[0] == 1


def test_foreign_keys_are_enforced_on_store_connections(tmp_path):
    store = _store(tmp_path)
    with store.connect() as conn, pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO channel_bindings "
            "(id, channel, session_id, bot_account_id, telegram_user_id, "
            "telegram_chat_id, status, paired_at, sync_from_event_id, "
            "projection_cursor_event_id, created_at, updated_at) "
            "VALUES (?, 'telegram', 'cs_x', 'missing', '1', '1', 'active', "
            "?, 0, 0, ?, ?)",
            ("bind_missing",) + (datetime.now(UTC).isoformat(),) * 3,
        )


def test_active_binding_is_one_to_one_in_both_directions(tmp_path):
    store = _store(tmp_path)
    bot_a = _seed_bot(store, name="甲", bot_id="101")
    bot_b = _seed_bot(store, name="乙", bot_id="102")
    store.bind_for_test("cs_a", bot_a, user_id="1", chat_id="1", sync_from_event_id=7)

    with pytest.raises(ChannelConflict):
        store.bind_for_test("cs_a", bot_b, user_id="2", chat_id="2", sync_from_event_id=8)
    with pytest.raises(ChannelConflict):
        store.bind_for_test("cs_b", bot_a, user_id="1", chat_id="1", sync_from_event_id=8)


def test_live_telegram_bot_and_open_draft_session_are_unique(tmp_path):
    store = _store(tmp_path)
    first = store.create_bot("甲", draft_session_id="cs_a")
    second = store.create_bot("乙", draft_session_id="cs_b")
    store.activate_test_bot(first, telegram_bot_id="101")

    with pytest.raises(ChannelConflict):
        store.activate_test_bot(second, telegram_bot_id="101")
    with pytest.raises(ChannelConflict):
        store.create_bot("另一个草稿", draft_session_id="cs_a")


def test_telegram_numeric_ids_round_trip_as_text(tmp_path):
    store = _store(tmp_path)
    bot_id = _seed_bot(store, bot_id="900719925474099312345")
    binding = store.bind_for_test(
        "cs_big",
        bot_id,
        user_id=900719925474099312346,
        chat_id=-900719925474099312347,
        sync_from_event_id=0,
    )
    assert binding.telegram_user_id == "900719925474099312346"
    assert binding.telegram_chat_id == "-900719925474099312347"
    assert store.bot(bot_id).telegram_bot_id == "900719925474099312345"


def test_draft_session_is_authoritative_and_survives_approval(tmp_path):
    store = _store(tmp_path)
    bot_id = store.create_bot("草稿", draft_session_id="cs_a")
    assert store.current_draft("cs_a").id == bot_id
    store.activate_test_bot(bot_id, telegram_bot_id="101")
    request = _pending_request(
        store,
        bot_account_id=bot_id,
        session_id="cs_a",
        update_id=7,
        user_id="8",
    )
    binding = store.approve_pairing_request(
        request.id, session_id="cs_a", sync_from_event_id=3
    )

    assert store.current_draft("cs_a") is None
    assert binding.session_id == "cs_a"
    assert store.bot(bot_id).draft_session_id == "cs_a"


def test_credential_switch_is_revision_cas_and_increments_once(tmp_path):
    store = _store(tmp_path)
    bot_id = store.create_bot("凭证", draft_session_id="cs_a")
    first_credential = "cred_" + "a" * 32
    second_credential = "cred_" + "b" * 32

    account = store.cas_bot_credential(
        bot_id,
        expected_revision=0,
        credential_id=first_credential,
        telegram_bot_id="101",
        telegram_username="first_bot",
    )
    assert account.credential_revision == 1
    assert account.credential_id == first_credential
    assert account.status == BotStatus.AWAITING_PAIRING
    assert store.referenced_credential_ids() == {first_credential}

    with pytest.raises(ChannelConflict, match="revision"):
        store.cas_bot_credential(
            bot_id,
            expected_revision=0,
            credential_id=second_credential,
            telegram_bot_id="102",
        )
    unchanged = store.bot(bot_id)
    assert unchanged.credential_revision == 1
    assert unchanged.credential_id == first_credential
    assert unchanged.telegram_bot_id == "101"


def test_approving_same_pairing_request_is_idempotent(tmp_path):
    store = _store(tmp_path)
    bot_id = _seed_bot(store, draft_session_id="cs_a")
    request = _pending_request(
        store,
        bot_account_id=bot_id,
        session_id="cs_a",
        update_id=11,
        user_id="31",
    )

    first = store.approve_pairing_request(
        request.id, session_id="cs_a", sync_from_event_id=7
    )
    second = store.approve_pairing_request(
        request.id, session_id="cs_a", sync_from_event_id=99
    )

    assert second.id == first.id
    assert second.sync_from_event_id == 7
    assert store.pairing_request(request.id).status == PairingStatus.APPROVED
    assert store.pairing_request(request.id).approved_binding_id == first.id


def test_expired_approval_is_rejected_and_expired_state_commits(tmp_path):
    store = _store(tmp_path)
    bot_id = _seed_bot(store, draft_session_id="cs_a")
    expiry = datetime(2026, 8, 23, 1, 0, tzinfo=UTC)
    request = _pending_request(
        store,
        bot_account_id=bot_id,
        session_id="cs_a",
        update_id=12,
        user_id="32",
        expires_at=expiry,
    )

    with pytest.raises(ChannelConflict, match="expired"):
        store.approve_pairing_request(
            request.id,
            session_id="cs_a",
            sync_from_event_id=7,
            now=expiry,
        )

    assert store.pairing_request(request.id).status == PairingStatus.EXPIRED
    assert store.live_bindings() == []


@pytest.mark.parametrize(
    ("request_session_id", "caller_session_id"),
    [
        ("cs_a", "cs_other"),
        ("cs_other", "cs_other"),
    ],
)
def test_pending_approval_requires_request_bot_and_caller_session_match(
    tmp_path, request_session_id, caller_session_id
):
    store = _store(tmp_path)
    bot_id = _seed_bot(store, draft_session_id="cs_a")
    request = _pending_request(
        store,
        bot_account_id=bot_id,
        session_id=request_session_id,
        update_id=13,
        user_id="33",
    )

    with pytest.raises(ChannelConflict, match="session"):
        store.approve_pairing_request(
            request.id,
            session_id=caller_session_id,
            sync_from_event_id=7,
        )

    assert store.pairing_request(request.id).status == PairingStatus.PENDING
    assert store.live_bindings() == []


def test_approved_idempotency_path_never_returns_binding_cross_session(tmp_path):
    store = _store(tmp_path)
    bot_id = _seed_bot(store, draft_session_id="cs_a")
    request = _pending_request(
        store,
        bot_account_id=bot_id,
        session_id="cs_a",
        update_id=14,
        user_id="34",
    )
    approved = store.approve_pairing_request(
        request.id, session_id="cs_a", sync_from_event_id=7
    )

    with pytest.raises(ChannelConflict, match="session"):
        store.approve_pairing_request(
            request.id, session_id="cs_other", sync_from_event_id=99
        )

    assert store.binding(approved.id).session_id == "cs_a"
    assert len(store.live_bindings()) == 1


def test_generic_pairing_transition_cannot_approve(tmp_path):
    store = _store(tmp_path)
    bot_id = _seed_bot(store, draft_session_id="cs_a")
    request = _pending_request(
        store,
        bot_account_id=bot_id,
        session_id="cs_a",
        update_id=15,
        user_id="35",
    )

    with pytest.raises(InvalidTransition, match="approve_pairing_request"):
        store.transition_pairing_request(request.id, PairingStatus.APPROVED)

    assert store.pairing_request(request.id).status == PairingStatus.PENDING
    assert store.live_bindings() == []


def test_different_requests_concurrently_approving_one_bot_only_allow_one(tmp_path):
    path = tmp_path / "channels.sqlite3"
    store = ChannelStore(path)
    bot_id = _seed_bot(store, draft_session_id="cs_a")
    request_a = _pending_request(
        store,
        bot_account_id=bot_id,
        session_id="cs_a",
        update_id=21,
        user_id="41",
    )
    request_b = _pending_request(
        store,
        bot_account_id=bot_id,
        session_id="cs_a",
        update_id=22,
        user_id="42",
    )

    def approve(request_id: str):
        try:
            return ChannelStore(path).approve_pairing_request(
                request_id, session_id="cs_a", sync_from_event_id=1
            )
        except ChannelConflict as exc:
            return exc

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(approve, [request_a.id, request_b.id]))

    assert sum(not isinstance(item, Exception) for item in results) == 1
    assert sum(isinstance(item, ChannelConflict) for item in results) == 1
    assert len(store.live_bindings()) == 1


def test_project_event_has_idempotency_key_and_monotonic_binding_sequence(tmp_path):
    store = _store(tmp_path)
    binding = _seed_binding(store)
    parts = [
        OutboundPart(kind="text", payload_part=0, text="A"),
        OutboundPart(kind="text", payload_part=1, text="B"),
    ]

    store.project_event(binding.id, source_event_id=11, parts=parts)
    store.project_event(binding.id, source_event_id=11, parts=parts)
    store.project_event(
        binding.id,
        source_event_id=12,
        parts=[OutboundPart(kind="text", payload_part=0, text="C")],
    )

    items = store.outbox_for(binding.id)
    assert [item.sequence for item in items] == [1, 2, 3]
    assert [(item.source_event_id, item.payload_part) for item in items] == [
        (11, 0),
        (11, 1),
        (12, 0),
    ]
    assert store.binding(binding.id).projection_cursor_event_id == 12


def test_project_insert_and_cursor_advance_roll_back_together(tmp_path):
    store = _store(tmp_path)
    binding = _seed_binding(store)
    with store.connect() as conn:
        conn.execute(
            "CREATE TRIGGER fail_second_part BEFORE INSERT ON outbox "
            "WHEN NEW.payload_part = 1 BEGIN SELECT RAISE(ABORT, 'injected'); END"
        )

    with pytest.raises(sqlite3.IntegrityError, match="injected"):
        store.project_event(
            binding.id,
            source_event_id=11,
            parts=[
                OutboundPart(kind="text", payload_part=0, text="A"),
                OutboundPart(kind="text", payload_part=1, text="B"),
            ],
        )

    assert store.outbox_for(binding.id) == []
    assert store.binding(binding.id).projection_cursor_event_id == 10


def test_automation_run_occurrence_is_unique_and_idempotent(tmp_path):
    store = _store(tmp_path)
    automation = store.create_automation(
        name="晨报",
        session_id="cs_a",
        schedule_kind="daily",
        local_time="08:30",
        timezone_name="Asia/Hong_Kong",
        prompt_text="生成晨报",
        confirmed_at=datetime.now(UTC),
    )
    scheduled_for = datetime(2026, 8, 24, 0, 30, tzinfo=UTC)

    first = store.create_automation_run(automation.id, scheduled_for)
    second = store.create_automation_run(automation.id, scheduled_for)

    assert second.id == first.id
    assert second.status == AutomationRunStatus.QUEUED
    assert len(store.automation_runs_for(automation.id)) == 1


def test_audit_accepts_only_structured_safe_details(tmp_path):
    store = _store(tmp_path)
    audit_id = store.audit(
        "binding_status_changed",
        session_id="cs_a",
        safe_details={
            "binding_id": "bind_abc",
            "previous_status": "active",
            "new_status": "paused",
            "changed_at": datetime.now(UTC),
            "error_class": "TelegramRetryable",
        },
    )
    with store.connect() as conn:
        raw = conn.execute(
            "SELECT safe_details_json FROM channel_audit WHERE id = ?", (audit_id,)
        ).fetchone()[0]
    details = json.loads(raw)
    assert details["binding_id"] == "bind_abc"
    assert "message" not in raw.lower()

    forbidden = [
        {"token": "123456:secret"},
        {"code": "ABCDEFGH"},
        {"text": "完整消息正文"},
        {"caption": "图片说明"},
        {"path": "C:/secret"},
        {"url": "https://example.test"},
        {"status": "这是一段消息全文"},
    ]
    for unsafe in forbidden:
        with pytest.raises(UnsafeAuditDetails):
            store.audit("unsafe", safe_details=unsafe)

    with store.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM channel_audit").fetchone()[0] == 1


@pytest.mark.parametrize(
    ("kind", "seed", "transition", "valid_target", "invalid_target"),
    [
        (
            "bot",
            lambda store: store.create_bot("状态机"),
            lambda store, item_id, target: store.transition_bot(item_id, target),
            BotStatus.AWAITING_PAIRING,
            BotStatus.PAUSED,
        ),
        (
            "binding",
            lambda store: _seed_binding(store).id,
            lambda store, item_id, target: store.transition_binding(item_id, target),
            BindingStatus.DISCONNECTED,
            BindingStatus.ACTIVE,
        ),
        (
            "pairing",
            lambda store: _pending_request(
                store,
                bot_account_id=_seed_bot(store),
                session_id="cs_state",
                update_id=91,
                user_id="91",
            ).id,
            lambda store, item_id, target: store.transition_pairing_request(
                item_id, target
            ),
            PairingStatus.DISMISSED,
            PairingStatus.APPROVED,
        ),
    ],
)
def test_domain_state_machines_reject_disallowed_transitions(
    tmp_path, kind, seed, transition, valid_target, invalid_target
):
    store = _store(tmp_path)
    item_id = seed(store)
    transition(store, item_id, valid_target)
    with pytest.raises(InvalidTransition, match=kind):
        transition(store, item_id, invalid_target)


def test_outbox_and_automation_run_state_machines(tmp_path):
    store = _store(tmp_path)
    binding = _seed_binding(store)
    item = store.project_event(
        binding.id,
        source_event_id=11,
        parts=[OutboundPart(kind="text", payload_part=0, text="A")],
    )[0]
    sending = store.transition_outbox(item.id, OutboxState.SENDING)
    assert sending.state == OutboxState.SENDING
    retrying = store.transition_outbox(item.id, OutboxState.RETRY_WAIT)
    assert retrying.state == OutboxState.RETRY_WAIT
    with pytest.raises(InvalidTransition, match="outbox"):
        store.transition_outbox(item.id, OutboxState.SENT)

    automation = store.create_automation(
        name="一次",
        session_id="cs_a",
        schedule_kind="once",
        run_at=datetime.now(UTC) + timedelta(hours=1),
        timezone_name="Asia/Hong_Kong",
        prompt_text="执行",
        confirmed_at=datetime.now(UTC),
    )
    run = store.create_automation_run(
        automation.id, datetime.now(UTC) + timedelta(hours=1)
    )
    assert store.transition_automation_run(run.id, AutomationRunStatus.RUNNING).status \
        == AutomationRunStatus.RUNNING
    assert store.transition_automation_run(run.id, AutomationRunStatus.DONE).status \
        == AutomationRunStatus.DONE
    with pytest.raises(InvalidTransition, match="automation_run"):
        store.transition_automation_run(run.id, AutomationRunStatus.RUNNING)


def test_all_persisted_times_are_aware_utc_iso_strings(tmp_path):
    fixed = datetime(2026, 8, 23, 9, 10, 11, tzinfo=timezone(timedelta(hours=8)))
    store = ChannelStore(tmp_path / "channels.sqlite3", clock=lambda: fixed)
    bot_id = store.create_bot("时区")
    account = store.bot(bot_id)
    parsed = datetime.fromisoformat(account.created_at)
    assert parsed.tzinfo is not None
    assert parsed.utcoffset() == timedelta(0)
    assert parsed.hour == 1

    with pytest.raises(ValueError, match="timezone-aware"):
        store.create_automation(
            name="坏时间",
            session_id="cs_a",
            schedule_kind="once",
            run_at=datetime(2026, 8, 24, 9, 0),
            timezone_name="Asia/Hong_Kong",
            prompt_text="执行",
            confirmed_at=fixed,
        )
