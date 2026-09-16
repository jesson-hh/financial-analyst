# -*- coding: utf-8 -*-
"""Atomic ChannelStore fences used by Console archive and hard delete."""
from __future__ import annotations

import asyncio
import json
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from guanlan_v2.channels.models import BindingStatus, BotStatus, OutboundPart, OutboxState
from guanlan_v2.channels.runtime import ChannelRuntime, ChannelRuntimeError
from guanlan_v2.channels.secrets import SecretMetadata, SecretUnavailable
from guanlan_v2.channels.store import ChannelConflict, ChannelStore
from guanlan_v2.channels.telegram import (
    TelegramBotIdentity,
    TelegramRetryable,
    TelegramSendResult,
)
from guanlan_v2.console.api import build_console_router
from guanlan_v2.console.store import ConsoleStore


UTC = timezone.utc


class Clock:
    def __init__(self) -> None:
        self.value = datetime(2026, 8, 23, 1, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.value


def _store(tmp_path, *, clock: Clock | None = None) -> ChannelStore:
    return ChannelStore(tmp_path / "channels.sqlite3", clock=clock or Clock())


def _live(store: ChannelStore, sid: str, number: int):
    bot = store.create_bot(f"Bot {number}", draft_session_id=sid)
    store.cas_bot_credential(
        bot,
        expected_revision=0,
        credential_id="cred_" + f"{number:032x}"[-32:],
        telegram_bot_id=str(100000 + number),
    )
    binding = store.bind_for_test(
        sid,
        bot,
        user_id=str(200000 + number),
        chat_id=str(300000 + number),
        sync_from_event_id=0,
    )
    return bot, binding


def _project(store: ChannelStore, binding, event_id: int):
    return store.project_event_for_test(
        binding.id,
        source_event_id=event_id,
        parts=[OutboundPart(kind="text", payload_part=0, text=f"事件 {event_id}")],
    )[0]


def _automation(store: ChannelStore, sid: str, number: int):
    return store.create_automation(
        name=f"晨报 {number}",
        session_id=sid,
        schedule_kind="daily",
        local_time="08:30",
        prompt_text="生成已确认的晨报。",
        next_run_at=datetime(2026, 8, 24, 0, 30, tzinfo=UTC),
        skill_refs=(),
    )


def test_session_archive_freezes_all_sqlite_authority_in_one_decision(tmp_path):
    clock = Clock()
    store = _store(tmp_path, clock=clock)
    sid = "cs_archive"
    bot, binding = _live(store, sid, 1)
    pending = _project(store, binding, 1)
    retrying = _project(store, binding, 2)
    sending = _project(store, binding, 3)
    lease_until = clock() + timedelta(minutes=1)
    store.transition_outbox_for_test(
        retrying.id,
        OutboxState.SENDING,
        lease_owner="retry-lease",
        lease_until=lease_until,
    )
    store.transition_outbox_for_test(
        retrying.id,
        OutboxState.RETRY_WAIT,
        next_attempt_at=clock() + timedelta(minutes=5),
        error_class="telegram_429",
    )
    store.transition_outbox_for_test(
        sending.id,
        OutboxState.SENDING,
        lease_owner="send-lease",
        lease_until=lease_until,
    )
    projection_owner = store.acquire_projection_lease(binding.id, lease_seconds=30)
    assert projection_owner is not None
    automation = _automation(store, sid, 1)
    run = store.create_automation_run(
        automation.id, datetime(2026, 8, 24, 0, 30, tzinfo=UTC)
    )

    result = store.pause_session_for_archive(sid)

    assert result.safe_paused_sids == (sid,)
    assert result.paused_binding_count == 1
    assert result.paused_automation_count == 1
    assert result.cancelled_outbox_count == 2
    assert result.uncertain_outbox_count == 1
    assert result.cancelled_automation_run_count == 1
    assert store.binding(binding.id).status is BindingStatus.PAUSED
    assert store.binding(binding.id).pause_reason == "archive"
    assert store.bot(bot).status is BotStatus.PAUSED
    assert store.outbox(pending.id).state is OutboxState.CANCELLED
    assert store.outbox(retrying.id).state is OutboxState.CANCELLED
    assert store.outbox(sending.id).state is OutboxState.UNCERTAIN
    assert store.outbox(sending.id).last_error_class == "archive_inflight"
    assert store.acquire_projection_lease(binding.id) is None
    assert store.claim_next_outbox(
        binding.id,
        now=clock(),
        lease_until=clock() + timedelta(seconds=30),
    ) is None
    assert store.automation(automation.id).enabled is False
    assert store.automation(automation.id).pause_reason == "archive"
    assert store.automation_run(run.id).status.value == "cancelled"

    # A Console restore has deliberately no inverse here.  Reopening the same
    # durable store therefore cannot resume either delivery surface.
    reopened = ChannelStore(store.path, clock=clock)
    assert reopened.binding(binding.id).status is BindingStatus.PAUSED
    assert reopened.automation(automation.id).enabled is False


def test_workspace_pause_is_one_transaction_and_rolls_back_every_session(tmp_path):
    store = _store(tmp_path)
    sid_a, sid_b = "cs_workspace_a", "cs_workspace_b"
    _, binding_a = _live(store, sid_a, 11)
    _, binding_b = _live(store, sid_b, 12)
    auto_a = _automation(store, sid_a, 11)
    auto_b = _automation(store, sid_b, 12)
    with store.connect() as conn:
        conn.execute(
            "CREATE TRIGGER fail_second_workspace_pause BEFORE UPDATE ON channel_bindings "
            f"WHEN OLD.id = '{binding_b.id}' AND NEW.pause_reason = 'workspace_archived' "
            "BEGIN SELECT RAISE(ABORT, 'injected workspace failure'); END"
        )

    with pytest.raises(sqlite3.IntegrityError, match="injected workspace failure"):
        store.pause_workspace_sessions_for_archive((sid_a, sid_b))

    assert store.binding(binding_a.id).status is BindingStatus.ACTIVE
    assert store.binding(binding_b.id).status is BindingStatus.ACTIVE
    assert store.automation(auto_a.id).enabled is True
    assert store.automation(auto_b.id).enabled is True

    with store.connect() as conn:
        conn.execute("DROP TRIGGER fail_second_workspace_pause")
    result = store.pause_workspace_sessions_for_archive((sid_a, sid_b, sid_a))
    assert result.safe_paused_sids == (sid_a, sid_b)
    assert store.binding(binding_a.id).pause_reason == "workspace_archived"
    assert store.binding(binding_b.id).pause_reason == "workspace_archived"
    assert store.automation(auto_a.id).pause_reason == "workspace_archived"
    assert store.automation(auto_b.id).pause_reason == "workspace_archived"


def test_archive_freezes_unbound_pairing_poll_and_clears_pending_authority(tmp_path):
    clock = Clock()
    store = _store(tmp_path, clock=clock)
    sid = "cs_pairing_archive"
    bot = store.create_bot("待配对 Bot", draft_session_id=sid)
    store.cas_bot_credential(
        bot,
        expected_revision=0,
        credential_id="cred_" + "b" * 32,
        telegram_bot_id="771234",
    )
    assert store.acquire_poll_lease(
        bot, owner="pairing-poller", credential_revision=1
    ) is True
    request = store.create_pairing_request(
        bot_account_id=bot,
        session_id=sid,
        telegram_update_id=1,
        telegram_user_id="901",
        telegram_chat_id="901",
        code_salt=b"s" * 16,
        code_digest=b"d" * 32,
        code_hint="XY",
        expires_at=clock() + timedelta(hours=1),
    )
    store.prepare_token_replacement(sid, bot)

    result = store.pause_session_for_archive(sid)

    frozen = store.bot(bot)
    assert result.frozen_setup_bot_count == 1
    assert result.dismissed_pairing_request_count == 1
    assert result.cleared_replacement_intent_count == 1
    assert frozen.status is BotStatus.CREDENTIAL_UNAVAILABLE
    assert frozen.last_error_code == "session_archived"
    assert frozen.poll_owner is None
    assert frozen.poll_lease_until is None
    assert store.pairing_request(request.id).status.value == "dismissed"
    assert store.replacement_intent_for_session(sid) is None
    assert store.acquire_poll_lease(
        bot, owner="new-poller", credential_revision=1
    ) is False
    assert store.hard_delete_blockers(sid).setup_bot_count == 1


def test_archive_serializes_with_outbox_claim_and_never_leaves_sending(tmp_path):
    clock = Clock()
    store = _store(tmp_path, clock=clock)
    barrier = threading.Barrier(2)

    def claim(binding_id: str):
        local = ChannelStore(store.path, clock=clock)
        barrier.wait()
        return local.claim_next_outbox(
            binding_id,
            owner="race-worker",
            now=clock(),
            lease_until=clock() + timedelta(seconds=30),
        )

    def archive(sid: str):
        local = ChannelStore(store.path, clock=clock)
        barrier.wait()
        return local.pause_session_for_archive(sid)

    sid = "cs_claim_race"
    _, binding = _live(store, sid, 21)
    item = _project(store, binding, 1)
    with ThreadPoolExecutor(max_workers=2) as pool:
        claimed_future = pool.submit(claim, binding.id)
        archived_future = pool.submit(archive, sid)
        claimed = claimed_future.result()
        archived_future.result()

    final = store.outbox(item.id)
    assert final.state in {OutboxState.CANCELLED, OutboxState.UNCERTAIN}
    assert final.state is not OutboxState.SENDING
    if claimed is not None:
        assert final.state is OutboxState.UNCERTAIN
    assert store.binding(binding.id).status is BindingStatus.PAUSED
    assert store.claim_next_outbox(
        binding.id,
        now=clock(),
        lease_until=clock() + timedelta(seconds=30),
    ) is None


def test_archive_serializes_with_projector_claim_and_revokes_old_lease(tmp_path):
    store = _store(tmp_path)
    sid = "cs_projector_race"
    _, binding = _live(store, sid, 31)
    barrier = threading.Barrier(2)

    def acquire():
        local = ChannelStore(store.path, clock=store._clock)
        barrier.wait()
        return local.acquire_projection_lease(binding.id, lease_seconds=30)

    def archive():
        local = ChannelStore(store.path, clock=store._clock)
        barrier.wait()
        return local.pause_session_for_archive(sid)

    with ThreadPoolExecutor(max_workers=2) as pool:
        acquired_future = pool.submit(acquire)
        archived_future = pool.submit(archive)
        old_owner = acquired_future.result()
        archived_future.result()

    assert store.binding(binding.id).status is BindingStatus.PAUSED
    assert store.acquire_projection_lease(binding.id) is None
    if old_owner is not None:
        with pytest.raises(ChannelConflict, match="active binding"):
            store.project_event(
                binding.id,
                source_event_id=1,
                parts=[OutboundPart(kind="text", payload_part=0, text="不得投影")],
                projection_owner=old_owner,
            )


def test_hard_delete_blockers_cover_every_unresolved_delivery_state(tmp_path):
    clock = Clock()
    store = _store(tmp_path, clock=clock)
    sid = "cs_delete_blocked"
    bot, binding = _live(store, sid, 41)
    items = [_project(store, binding, index) for index in range(1, 6)]
    lease_until = clock() + timedelta(minutes=1)
    # pending, sending, retry_wait, uncertain, terminal
    for index in range(1, 5):
        store.transition_outbox_for_test(
            items[index].id,
            OutboxState.SENDING,
            lease_owner=f"lease-{index}",
            lease_until=lease_until,
        )
    store.transition_outbox_for_test(
        items[2].id,
        OutboxState.RETRY_WAIT,
        next_attempt_at=clock() + timedelta(minutes=1),
        error_class="retryable",
    )
    store.transition_outbox_for_test(
        items[3].id, OutboxState.UNCERTAIN, error_class="ambiguous"
    )
    store.transition_outbox_for_test(
        items[4].id, OutboxState.TERMINAL, error_class="terminal_error"
    )
    _automation(store, sid, 41)
    store.enqueue_credential_cleanup(
        "cred_" + "f" * 32,
        bot_account_id=bot,
        reason="delete_failed",
    )
    with store.connect() as conn:
        conn.execute(
            "UPDATE bot_accounts SET status = 'poll_conflict' WHERE id = ?", (bot,)
        )
        conn.execute(
            "UPDATE channel_bindings SET status = 'paused', pause_reason = 'poll_conflict' "
            "WHERE id = ?",
            (binding.id,),
        )

    blockers = store.hard_delete_blockers(sid)
    assert blockers.blocked is True
    assert blockers.reason_code == "channel_cleanup_required"
    assert blockers.live_binding_count == 1
    assert dict(blockers.unresolved_outbox_counts) == {
        "pending": 1,
        "sending": 1,
        "retry_wait": 1,
        "uncertain": 1,
        "terminal": 1,
    }
    assert blockers.automation_count == 1
    assert blockers.setup_bot_count == 1
    assert blockers.credential_cleanup_count == 1
    public = blockers.as_safe_dict()
    encoded = json.dumps(public, ensure_ascii=False)
    assert public["blocked"] is True
    assert {item["kind"] for item in public["blockers"]} == {
        "live_binding",
        "unresolved_outbox",
        "automation",
        "bot_setup",
        "credential_cleanup",
    }
    assert "cred_" not in encoded
    assert "telegram" not in encoded.lower()
    assert "事件" not in encoded


@pytest.mark.parametrize("status", ["draft", "awaiting_pairing", "poll_conflict"])
def test_hard_delete_setup_bot_statuses_are_all_blockers(tmp_path, status):
    store = _store(tmp_path)
    sid = f"cs_setup_{status}"
    bot = store.create_bot("配对中", draft_session_id=sid)
    if status != "draft":
        store.cas_bot_credential(
            bot,
            expected_revision=0,
            credential_id="cred_" + "a" * 32,
            telegram_bot_id="991234",
        )
    if status == "poll_conflict":
        store.transition_bot(bot, BotStatus.POLL_CONFLICT, error_code="poll_conflict")

    blockers = store.hard_delete_blockers(sid)
    assert blockers.setup_bot_count == 1
    assert blockers.blocked is True


def test_disconnected_historic_outbox_and_cleanup_queue_still_block_delete(tmp_path):
    clock = Clock()
    store = _store(tmp_path, clock=clock)
    sid = "cs_disconnected_history"
    _, binding = _live(store, sid, 51)
    terminal = _project(store, binding, 1)
    store.transition_outbox_for_test(
        terminal.id,
        OutboxState.SENDING,
        lease_owner="terminal-lease",
        lease_until=clock() + timedelta(minutes=1),
    )
    store.transition_outbox_for_test(
        terminal.id, OutboxState.TERMINAL, error_class="recipient_blocked"
    )
    store.disconnect_session(sid)

    blockers = store.hard_delete_blockers(sid)
    assert blockers.live_binding_count == 0
    assert dict(blockers.unresolved_outbox_counts) == {"terminal": 1}
    assert blockers.credential_cleanup_count == 1
    assert blockers.blocked is True


def test_clean_session_has_no_hard_delete_blockers(tmp_path):
    store = _store(tmp_path)
    blockers = store.hard_delete_blockers("cs_clean")
    assert blockers.blocked is False
    assert blockers.reason_code is None
    assert blockers.as_safe_dict() == {
        "blocked": False,
        "reason_code": None,
        "blockers": [],
    }


class _RuntimeSecrets:
    def __init__(self, values=None, metadata=()) -> None:
        self.values = dict(values or {})
        self.metadata = list(metadata)
        self.deleted: list[str] = []

    def get(self, credential_id: str) -> str:
        try:
            return self.values[credential_id]
        except KeyError:
            raise SecretUnavailable() from None

    def delete(self, credential_id: str) -> None:
        self.deleted.append(credential_id)
        self.values.pop(credential_id, None)

    def iter_metadata(self):
        return iter(tuple(self.metadata))


class _RuntimeAdapter:
    def __init__(self, outcome=None) -> None:
        self.outcome = outcome or TelegramBotIdentity(bot_id="100001", username="safe_bot")
        self.get_me_calls: list[str] = []
        self.close_calls = 0

    async def get_me(self, token: str):
        self.get_me_calls.append(token)
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        return self.outcome

    async def send_message(self, _token: str, *, chat_id: str, text: str):
        return TelegramSendResult(message_id="1")

    async def aclose(self) -> None:
        self.close_calls += 1


class _NoopPairing:
    def __init__(self) -> None:
        self.start_calls = 0
        self.stop_calls = 0
        self.signal_calls = 0

    async def start(self) -> None:
        self.start_calls += 1

    async def stop(self) -> None:
        self.stop_calls += 1

    def signal(self) -> None:
        self.signal_calls += 1


class _DoneEvent:
    kind = "done"
    payload = None


class _CountingConsoleAgent:
    def __init__(self) -> None:
        self.messages = []
        self.calls = 0

    async def run_turn(self, _text, confirm_callback=None, allowed_tools=None):
        self.calls += 1
        yield _DoneEvent()


class _BlockingLifecycleRuntime:
    """Block one lifecycle hook while ordinary Console requests race it."""

    def __init__(self, blocked_method: str) -> None:
        self.blocked_method = blocked_method
        self.entered = threading.Event()
        self.release = threading.Event()
        self.calls: list[tuple[str, tuple]] = []

    def _call(self, method: str, *args):
        self.calls.append((method, args))
        if method == self.blocked_method:
            self.entered.set()
            assert self.release.wait(timeout=5)
        if method == "prepare_session_archive":
            return {"ok": True, "safe_paused_sids": [args[0]]}
        if method == "prepare_workspace_archive":
            return {"ok": True, "safe_paused_sids": list(args[1])}
        if method == "on_session_restore":
            return {"ok": True, "channel_remains_paused": True}
        return {"ok": True, "blockers": []}

    def extract_telegram_tokens(self, _text):
        return ()

    def prepare_session_archive(self, sid):
        return self._call("prepare_session_archive", sid)

    def abort_session_archive(self, sid):
        return self._call("abort_session_archive", sid)

    def prepare_workspace_archive(self, workspace_id, session_ids):
        return self._call("prepare_workspace_archive", workspace_id, session_ids)

    def abort_workspace_archive(self, workspace_id, session_ids):
        return self._call("abort_workspace_archive", workspace_id, session_ids)

    def on_session_restore(self, sid):
        return self._call("on_session_restore", sid)

    def assert_session_delete_allowed(self, sid):
        return self._call("assert_session_delete_allowed", sid)

    def finish_session_delete(self, sid):
        return self._call("finish_session_delete", sid)


def _console_with_blocking_lifecycle(tmp_path, blocked_method: str):
    console = ConsoleStore(root=tmp_path / "console")
    runtime = _BlockingLifecycleRuntime(blocked_method)
    agent = _CountingConsoleAgent()
    app = FastAPI()
    app.include_router(build_console_router(
        store=console,
        agent_factory=lambda _sid: agent,
        channel_runtime=runtime,
    ))
    return TestClient(app), console, runtime, agent


def test_session_archive_reservation_rejects_send_while_channel_hook_blocks(tmp_path):
    client, console, runtime, agent = _console_with_blocking_lifecycle(
        tmp_path, "prepare_session_archive",
    )
    archive_result = {}
    with client:
        sid = client.post(
            "/console/sessions", json={"title": "归档竞态"},
        ).json()["meta"]["id"]

        def _archive():
            archive_result["response"] = client.post(
                f"/console/sessions/{sid}/archive"
            )

        thread = threading.Thread(target=_archive, daemon=True)
        thread.start()
        try:
            assert runtime.entered.wait(timeout=5)
            blocked = client.post(
                "/console/send", json={"sid": sid, "text": "不得穿过归档门禁"},
            )
            assert blocked.status_code == 409
            assert console.read_events(sid) == []
            assert agent.calls == 0
        finally:
            runtime.release.set()
            thread.join(timeout=5)

        assert not thread.is_alive()
        assert archive_result["response"].status_code == 200
        assert console.get_meta(sid)["archived"] is True


def test_session_delete_reservation_rejects_send_until_safe_delete_finishes(tmp_path):
    client, console, runtime, agent = _console_with_blocking_lifecycle(
        tmp_path, "assert_session_delete_allowed",
    )
    delete_result = {}
    with client:
        sid = client.post(
            "/console/sessions", json={"title": "删除竞态"},
        ).json()["meta"]["id"]

        def _delete():
            delete_result["response"] = client.delete(f"/console/sessions/{sid}")

        thread = threading.Thread(target=_delete, daemon=True)
        thread.start()
        try:
            assert runtime.entered.wait(timeout=5)
            blocked = client.post(
                "/console/send", json={"sid": sid, "text": "不得穿过删除门禁"},
            )
            assert blocked.status_code == 409
            assert console.read_events(sid) == []
            assert agent.calls == 0
        finally:
            runtime.release.set()
            thread.join(timeout=5)

        assert not thread.is_alive()
        assert delete_result["response"].status_code == 200
        assert delete_result["response"].json()["ok"] is True
        assert console.get_meta(sid) is None
        assert ("finish_session_delete", (sid,)) in runtime.calls


def test_workspace_archive_reservation_rejects_membership_and_send_races(tmp_path):
    client, console, runtime, agent = _console_with_blocking_lifecycle(
        tmp_path, "prepare_workspace_archive",
    )
    archive_result = {}
    with client:
        workspace = client.post("/console/workspaces", json={
            "name": "归档中的研究空间",
            "kind": "stock",
            "code": "600519",
        }).json()["workspace"]
        member_sid = client.post("/console/sessions", json={
            "title": "空间成员",
            "workspace_id": workspace["id"],
        }).json()["meta"]["id"]
        moving_sid = client.post("/console/sessions", json={
            "title": "待移动会话",
        }).json()["meta"]["id"]
        initial_session_ids = {
            meta["id"] for meta in console.list_sessions()
        }

        def _archive_workspace():
            archive_result["response"] = client.delete(
                f"/console/workspaces/{workspace['id']}"
            )

        thread = threading.Thread(target=_archive_workspace, daemon=True)
        thread.start()
        try:
            assert runtime.entered.wait(timeout=5)
            new_session = client.post("/console/sessions", json={
                "title": "不得新建",
                "workspace_id": workspace["id"],
            })
            moved = client.patch(f"/console/sessions/{moving_sid}", json={
                "workspace_id": workspace["id"],
            })
            implicit_session = client.post("/console/send", json={
                "workspace_id": workspace["id"],
                "text": "不得隐式创建会话",
            })
            member_send = client.post("/console/send", json={
                "sid": member_sid,
                "text": "不得写入空间成员会话",
            })

            assert new_session.status_code == 409
            assert moved.status_code == 409
            assert implicit_session.status_code == 409
            assert member_send.status_code == 409
            assert {
                meta["id"] for meta in console.list_sessions()
            } == initial_session_ids
            assert console.get_meta(moving_sid)["workspace_id"] == "rw_general"
            assert console.read_events(member_sid) == []
            assert agent.calls == 0
        finally:
            runtime.release.set()
            thread.join(timeout=5)

        assert not thread.is_alive()
        assert archive_result["response"].status_code == 200
        assert console.workspace_store.get(workspace["id"])["archived"] is True


def test_feature_off_archive_restore_is_idempotent_and_keeps_channel_paused(tmp_path):
    console = ConsoleStore(root=tmp_path / "console")
    sid = console.create_session("关闭通道后的归档恢复")["id"]
    channel_store = ChannelStore(Path(console.root) / "channels.sqlite3")
    _bot, binding = _live(channel_store, sid, 81)
    automation = _automation(channel_store, sid, 81)
    app = FastAPI()
    app.include_router(build_console_router(
        store=console,
        agent_factory=lambda _sid: _CountingConsoleAgent(),
        channel_runtime=None,
    ))

    with TestClient(app) as client:
        first_archive = client.post(f"/console/sessions/{sid}/archive")
        second_archive = client.post(f"/console/sessions/{sid}/archive")
        assert first_archive.status_code == second_archive.status_code == 200
        assert second_archive.json()["already_archived"] is True
        assert channel_store.binding(binding.id).status is BindingStatus.PAUSED
        assert channel_store.binding(binding.id).pause_reason == "archive"
        assert channel_store.automation(automation.id).enabled is False
        assert channel_store.automation(automation.id).pause_reason == "archive"

        first_restore = client.post(f"/console/sessions/{sid}/restore")
        second_restore = client.post(f"/console/sessions/{sid}/restore")
        assert first_restore.status_code == second_restore.status_code == 200
        assert first_restore.json()["channel_remains_paused"] is True
        assert second_restore.json()["already_active"] is True

    assert console.get_meta(sid)["archived"] is False
    assert channel_store.binding(binding.id).status is BindingStatus.PAUSED
    assert channel_store.binding(binding.id).pause_reason == "archive"
    assert channel_store.automation(automation.id).enabled is False
    assert channel_store.automation(automation.id).pause_reason == "archive"


def test_feature_off_missing_channel_database_with_history_marker_fails_lifecycle_closed(
    tmp_path,
):
    console = ConsoleStore(root=tmp_path / "console")
    delete_sid = console.create_session("数据库暂缺时不可删除")["id"]
    archive_sid = console.create_session("数据库暂缺时不可归档")["id"]
    restore_sid = console.create_session("数据库暂缺时不可恢复")["id"]
    assert console.set_archived(restore_sid, True) is not None
    workspace = console.workspace_store.create({
        "name": "数据库暂缺时不可归档空间",
        "kind": "stock",
        "code": "600519",
    })
    member_sid = console.create_session(
        "空间成员",
        workspace_id=workspace["id"],
    )["id"]

    channel_db = Path(console.root) / "channels.sqlite3"
    channel_store = ChannelStore(channel_db)
    history_marker = channel_store.history_marker_path
    missing_copy = channel_db.with_name("channels.temporarily-missing.sqlite3")
    channel_db.replace(missing_copy)
    assert not channel_db.exists()
    assert history_marker.is_file()

    app = FastAPI()
    app.include_router(build_console_router(
        store=console,
        agent_factory=lambda _sid: _CountingConsoleAgent(),
        channel_runtime=None,
    ))
    with TestClient(app) as client:
        responses = (
            client.delete(f"/console/sessions/{delete_sid}"),
            client.post(f"/console/sessions/{archive_sid}/archive"),
            client.post(f"/console/sessions/{restore_sid}/restore"),
            client.delete(f"/console/workspaces/{workspace['id']}"),
        )

    assert all(response.status_code == 503 for response in responses)
    assert all(
        response.json()["reason_code"] == "channel_lifecycle_unavailable"
        for response in responses
    )
    assert console.get_meta(delete_sid) is not None
    assert console.get_meta(archive_sid)["archived"] is False
    assert console.get_meta(restore_sid)["archived"] is True
    assert console.get_meta(member_sid)["workspace_id"] == workspace["id"]
    assert console.workspace_store.get(workspace["id"])["archived"] is False
    assert history_marker.is_file() and missing_copy.is_file()


def test_feature_off_never_initialized_channel_paths_keep_console_lifecycle_compatible(
    tmp_path,
):
    console = ConsoleStore(root=tmp_path / "console")
    delete_sid = console.create_session("从未启用通道仍可删除")["id"]
    archive_sid = console.create_session("从未启用通道仍可归档恢复")["id"]
    workspace = console.workspace_store.create({
        "name": "从未启用通道仍可归档空间",
        "kind": "stock",
        "code": "000001",
    })
    member_sid = console.create_session(
        "无通道空间成员",
        workspace_id=workspace["id"],
    )["id"]
    channel_db = Path(console.root) / "channels.sqlite3"
    history_marker = channel_db.with_name(channel_db.name + ".history-present")
    assert not channel_db.exists() and not history_marker.exists()
    assert all(
        console.read_events(sid) == []
        and console.get_meta(sid).get("channel_history_present") is not True
        for sid in (delete_sid, archive_sid, member_sid)
    )

    app = FastAPI()
    app.include_router(build_console_router(
        store=console,
        agent_factory=lambda _sid: _CountingConsoleAgent(),
        channel_runtime=None,
    ))
    with TestClient(app) as client:
        deleted = client.delete(f"/console/sessions/{delete_sid}")
        archived = client.post(f"/console/sessions/{archive_sid}/archive")
        restored = client.post(f"/console/sessions/{archive_sid}/restore")
        workspace_archived = client.delete(
            f"/console/workspaces/{workspace['id']}"
        )

    assert deleted.status_code == 200 and deleted.json()["ok"] is True
    assert archived.status_code == 200 and archived.json()["meta"]["archived"] is True
    assert restored.status_code == 200 and restored.json()["meta"]["archived"] is False
    assert workspace_archived.status_code == 200
    assert workspace_archived.json()["workspace"]["archived"] is True
    assert console.get_meta(delete_sid) is None
    assert console.get_meta(archive_sid)["archived"] is False
    assert console.get_meta(member_sid)["workspace_id"] == workspace["id"]
    assert not channel_db.exists() and not history_marker.exists()


@pytest.mark.parametrize("historic_state", [OutboxState.SENT, OutboxState.CANCELLED])
def test_hard_delete_blocks_disconnected_resolved_recipient_history(
    tmp_path, historic_state
):
    """Resolved delivery history may not outlive its deleted Console owner.

    A disconnect intentionally retains the immutable delivery/recipient audit
    trail.  Until ChannelStore owns an atomic purge/tombstone operation for
    that entire graph, hard-delete must fail closed even after the credential
    cleanup obligation has completed.
    """

    clock = Clock()
    console = ConsoleStore(root=tmp_path / "console")
    sid = console.create_session(f"删除保护 {historic_state.value}")["id"]
    store = _store(tmp_path, clock=clock)
    bot, binding = _live(store, sid, 61)
    item = _project(store, binding, 1)
    credential_id = store.bot(bot).credential_id
    assert credential_id is not None

    if historic_state is OutboxState.SENT:
        store.transition_outbox_for_test(
            item.id,
            OutboxState.SENDING,
            lease_owner="resolved-history",
            lease_until=clock() + timedelta(minutes=1),
        )
        store.transition_outbox_for_test(
            item.id,
            OutboxState.SENT,
            telegram_message_id="7001",
        )
    else:
        store.transition_outbox_for_test(item.id, OutboxState.CANCELLED)

    secrets = _RuntimeSecrets({credential_id: "123456:abcdefghijklmnopqrstuvwxyz_ABCD"})
    runtime = ChannelRuntime(
        console_store=console,
        channel_store=store,
        secret_store=secrets,
        telegram_adapter=object(),
    )
    disconnected = runtime.disconnect(sid)
    assert disconnected["view_state"] == "none"
    assert secrets.deleted == [credential_id]
    assert store.credential_cleanup_items() == []
    assert store.binding(binding.id).status is BindingStatus.DISCONNECTED
    assert store.bot(bot).status is BotStatus.DISCONNECTED
    assert store.outbox(item.id).state is historic_state

    app = FastAPI()
    app.include_router(
        build_console_router(
            store=console,
            agent_factory=lambda _sid: object(),
            channel_runtime=runtime,
        )
    )
    with TestClient(app) as client:
        response = client.delete(f"/console/sessions/{sid}")

    # Channel rows are deliberately retained by disconnect.  If the Console
    # owner vanished here, all four records would become an unowned graph.
    with store.connect() as connection:
        retained = {
            "binding": tuple(
                connection.execute(
                    "SELECT session_id, telegram_user_id, telegram_chat_id, status "
                    "FROM channel_bindings WHERE id = ?",
                    (binding.id,),
                ).fetchone()
            ),
            "bot": tuple(
                connection.execute(
                    "SELECT draft_session_id, status, credential_id "
                    "FROM bot_accounts WHERE id = ?",
                    (bot,),
                ).fetchone()
            ),
            "outbox": tuple(
                connection.execute(
                    "SELECT source_session_id, state FROM outbox WHERE id = ?",
                    (item.id,),
                ).fetchone()
            ),
        }
    assert retained == {
        "binding": (sid, "200061", "300061", "disconnected"),
        "bot": (sid, "disconnected", None),
        "outbox": (sid, historic_state.value),
    }
    assert response.status_code == 409, (
        "hard delete removed the Console owner while retaining disconnected "
        f"Channel recipient history: {retained}"
    )
    assert response.json()["reason_code"] == "channel_session_delete_blocked"
    assert console.get_meta(sid) is not None


@pytest.mark.parametrize("historic_state", [OutboxState.SENT, OutboxState.CANCELLED])
def test_feature_off_hard_delete_still_blocks_disconnected_channel_history(
    tmp_path, historic_state
):
    """Disabling Telegram must not disable deletion privacy/integrity fences."""

    clock = Clock()
    console = ConsoleStore(root=tmp_path / "console")
    sid = console.create_session(f"关闭开关删除保护 {historic_state.value}")["id"]
    store = ChannelStore(Path(console.root) / "channels.sqlite3", clock=clock)
    bot, binding = _live(store, sid, 62)
    item = _project(store, binding, 1)
    credential_id = store.bot(bot).credential_id
    assert credential_id is not None

    if historic_state is OutboxState.SENT:
        store.transition_outbox_for_test(
            item.id,
            OutboxState.SENDING,
            lease_owner="feature-off-history",
            lease_until=clock() + timedelta(minutes=1),
        )
        store.transition_outbox_for_test(
            item.id,
            OutboxState.SENT,
            telegram_message_id="7002",
        )
    else:
        store.transition_outbox_for_test(item.id, OutboxState.CANCELLED)

    _disconnected_bot, cleanup_id = store.disconnect_session(sid)
    assert cleanup_id == credential_id
    store.complete_credential_cleanup(credential_id)
    assert store.hard_delete_blockers(sid).blocked is True

    app = FastAPI()
    app.include_router(
        build_console_router(
            store=console,
            agent_factory=lambda _sid: object(),
            channel_runtime=None,
        )
    )
    with TestClient(app) as client:
        response = client.delete(f"/console/sessions/{sid}")

    assert response.status_code == 409
    assert response.json()["reason_code"] == "channel_session_delete_blocked"
    assert response.json()["blockers"] == [{"code": "channel_history"}]
    assert console.get_meta(sid) is not None
    assert store.binding(binding.id).status is BindingStatus.DISCONNECTED
    assert store.outbox(item.id).state is historic_state


def test_feature_off_hard_delete_allows_initialized_empty_channel_store(tmp_path):
    """An empty Channel database preserves the Console-only delete behavior."""

    console = ConsoleStore(root=tmp_path / "console")
    sid = console.create_session("关闭开关且无通道历史")["id"]
    ChannelStore(Path(console.root) / "channels.sqlite3")

    app = FastAPI()
    app.include_router(
        build_console_router(
            store=console,
            agent_factory=lambda _sid: object(),
            channel_runtime=None,
        )
    )
    with TestClient(app) as client:
        response = client.delete(f"/console/sessions/{sid}")

    assert response.status_code == 200
    assert response.json() == {"ok": True, "id": sid}
    assert console.get_meta(sid) is None


def _runtime_live(tmp_path, *, adapter_outcome=None):
    console = ConsoleStore(root=tmp_path / "console")
    sid = console.create_session("生命周期")['id']
    store = ChannelStore(tmp_path / "runtime-channels.sqlite3")
    credential_id = "cred_" + "1" * 32
    token = "123456:abcdefghijklmnopqrstuvwxyz_ABCD"
    bot = store.create_bot("Runtime Bot", draft_session_id=sid)
    store.cas_bot_credential(
        bot,
        expected_revision=0,
        credential_id=credential_id,
        telegram_bot_id="100001",
    )
    binding = store.bind_for_test(
        sid, bot, user_id="200001", chat_id="200001", sync_from_event_id=0
    )
    secrets = _RuntimeSecrets({credential_id: token})
    adapter = _RuntimeAdapter(adapter_outcome)
    runtime = ChannelRuntime(
        console_store=console,
        channel_store=store,
        secret_store=secrets,
        telegram_adapter=adapter,
        maintenance_interval_seconds=3600,
    )
    pairing = _NoopPairing()
    runtime.pairing = pairing
    return runtime, console, store, secrets, adapter, pairing, sid, bot, binding


def test_runtime_archive_decision_blocks_new_token_reservations_until_restore(tmp_path):
    runtime, _console, store, _secrets, _adapter, _pairing, sid, _bot, binding = (
        _runtime_live(tmp_path)
    )

    result = runtime.prepare_session_archive(sid)

    assert result["safe_paused_sids"] == [sid]
    assert store.binding(binding.id).status is BindingStatus.PAUSED
    assert runtime.reserve_secret_submission(sid) is False
    assert runtime.on_session_restore(sid)["channel_remains_paused"] is True
    assert runtime.reserve_secret_submission(sid) is True
    runtime.release_secret_submission(sid)


def test_runtime_archive_abort_releases_only_transient_freeze(tmp_path):
    runtime, _console, store, _secrets, _adapter, _pairing, sid, _bot, binding = (
        _runtime_live(tmp_path)
    )

    runtime.prepare_session_archive(sid)
    result = runtime.abort_session_archive(sid)

    assert result["channel_remains_paused"] is True
    assert store.binding(binding.id).status is BindingStatus.PAUSED
    assert runtime.reserve_secret_submission(sid) is True
    runtime.release_secret_submission(sid)


def test_clean_delete_snapshot_freezes_new_drafts_until_console_removal(tmp_path):
    console = ConsoleStore(root=tmp_path / "console")
    sid = console.create_session("删除门禁")['id']
    runtime = ChannelRuntime(
        console_store=console,
        channel_store=ChannelStore(tmp_path / "channels.sqlite3"),
        secret_store=_RuntimeSecrets(),
        telegram_adapter=_RuntimeAdapter(),
    )

    assert runtime.assert_session_delete_allowed(sid) == {
        "ok": True,
        "blockers": [],
    }
    with pytest.raises(ChannelRuntimeError) as frozen:
        runtime.create_draft(sid, "不得穿过删除门禁")
    assert frozen.value.reason_code == "channel_lifecycle_busy"
    assert console.delete_session(sid) is True
    assert runtime.finish_session_delete(sid) == {"ok": True}
    assert sid not in runtime._lifecycle_frozen_sessions


def test_runtime_start_fences_console_archive_before_any_network_or_claim(tmp_path):
    async def scenario():
        runtime, console, store, _secrets, adapter, _pairing, sid, _bot, binding = (
            _runtime_live(tmp_path)
        )
        blocked = runtime.assert_session_delete_allowed(sid)
        assert blocked["ok"] is False
        assert blocked["reason_code"] == "channel_session_delete_blocked"
        assert blocked["blockers"] == ["live_binding"]
        pending = _project(store, binding, 71)
        assert console.set_archived(sid, True) is not None

        await runtime.start()
        try:
            assert store.binding(binding.id).status is BindingStatus.PAUSED
            assert store.binding(binding.id).pause_reason == "archive"
            assert store.outbox(pending.id).state is OutboxState.CANCELLED
            assert adapter.get_me_calls == []
        finally:
            await runtime.stop()
        assert adapter.close_calls == 1

    asyncio.run(scenario())


def test_runtime_does_not_recover_archived_unbound_pairing_bot(tmp_path):
    async def scenario():
        console = ConsoleStore(root=tmp_path / "console")
        sid = console.create_session("配对归档")['id']
        store = ChannelStore(tmp_path / "channels.sqlite3")
        credential_id = "cred_" + "9" * 32
        bot = store.create_bot("Pairing Bot", draft_session_id=sid)
        store.cas_bot_credential(
            bot,
            expected_revision=0,
            credential_id=credential_id,
            telegram_bot_id="919191",
        )
        secrets = _RuntimeSecrets({credential_id: "123456:abcdefghijklmnopqrstuvwxyz_ABCD"})
        adapter = _RuntimeAdapter(
            TelegramBotIdentity(bot_id="919191", username="pairing_bot")
        )
        runtime = ChannelRuntime(
            console_store=console,
            channel_store=store,
            secret_store=secrets,
            telegram_adapter=adapter,
        )
        runtime.pairing = _NoopPairing()
        assert console.set_archived(sid, True) is not None

        await runtime.start()
        try:
            frozen = store.bot(bot)
            assert frozen.status is BotStatus.CREDENTIAL_UNAVAILABLE
            assert frozen.last_error_code == "session_archived"
            assert adapter.get_me_calls == []
        finally:
            await runtime.stop()

        # Restoring Console visibility is not delivery authority.  A fresh
        # process must still skip this lifecycle-faulted Bot until the user
        # explicitly asks for reverification.
        assert console.set_archived(sid, False) is not None
        adapter_two = _RuntimeAdapter(
            TelegramBotIdentity(bot_id="919191", username="pairing_bot")
        )
        runtime_two = ChannelRuntime(
            console_store=console,
            channel_store=store,
            secret_store=secrets,
            telegram_adapter=adapter_two,
        )
        runtime_two.pairing = _NoopPairing()
        await runtime_two.start()
        try:
            still_frozen = store.bot(bot)
            assert still_frozen.status is BotStatus.CREDENTIAL_UNAVAILABLE
            assert still_frozen.last_error_code == "session_archived"
            assert adapter_two.get_me_calls == []

            await runtime_two.reverify(sid)
            assert store.bot(bot).status is BotStatus.AWAITING_PAIRING
            assert len(adapter_two.get_me_calls) == 1
        finally:
            await runtime_two.stop()

    asyncio.run(scenario())


def test_inflight_reverify_and_archive_are_mutually_exclusive(tmp_path):
    class BlockingAdapter(_RuntimeAdapter):
        def __init__(self) -> None:
            super().__init__(
                TelegramBotIdentity(bot_id="717171", username="blocked_reverify")
            )
            self.entered = asyncio.Event()
            self.release = asyncio.Event()

        async def get_me(self, token: str):
            self.get_me_calls.append(token)
            self.entered.set()
            await self.release.wait()
            return self.outcome

    async def scenario():
        console = ConsoleStore(root=tmp_path / "console")
        sid = console.create_session("并发复验")['id']
        store = ChannelStore(tmp_path / "channels.sqlite3")
        credential_id = "cred_" + "7" * 32
        bot = store.create_bot("Reverify Bot", draft_session_id=sid)
        store.cas_bot_credential(
            bot,
            expected_revision=0,
            credential_id=credential_id,
            telegram_bot_id="717171",
        )
        adapter = BlockingAdapter()
        runtime = ChannelRuntime(
            console_store=console,
            channel_store=store,
            secret_store=_RuntimeSecrets(
                {credential_id: "123456:abcdefghijklmnopqrstuvwxyz_ABCD"}
            ),
            telegram_adapter=adapter,
        )
        runtime.pairing = _NoopPairing()

        verifying = asyncio.create_task(runtime.reverify(sid))
        await asyncio.wait_for(adapter.entered.wait(), timeout=2)
        with pytest.raises(ChannelRuntimeError) as busy:
            runtime.prepare_session_archive(sid)
        assert busy.value.reason_code == "channel_lifecycle_busy"
        assert console.get_meta(sid)["archived"] is False

        adapter.release.set()
        await verifying
        assert store.bot(bot).status is BotStatus.AWAITING_PAIRING

        # Once the verifier releases ownership, the complete pause+Console
        # archive decision can proceed and remains non-pollable.
        runtime.prepare_session_archive(sid)
        assert console.set_archived(sid, True) is not None
        frozen = store.bot(bot)
        assert frozen.status is BotStatus.CREDENTIAL_UNAVAILABLE
        assert frozen.last_error_code == "session_archived"

    asyncio.run(scenario())


def test_inflight_automation_upsert_cannot_cross_archive_fence(tmp_path):
    async def scenario():
        console = ConsoleStore(root=tmp_path / "console")
        sid = console.create_session("自动化并发")['id']
        store = ChannelStore(tmp_path / "channels.sqlite3")
        runtime = ChannelRuntime(
            console_store=console,
            channel_store=store,
            secret_store=_RuntimeSecrets(),
            telegram_adapter=_RuntimeAdapter(),
        )
        entered = threading.Event()
        release = threading.Event()
        original = runtime.automation_worker.upsert_automation

        def blocked_upsert(**kwargs):
            entered.set()
            assert release.wait(timeout=3)
            return original(**kwargs)

        runtime.automation_worker.upsert_automation = blocked_upsert
        spec = {
            "automation_id": "",
            "name": "晨报",
            "schedule_kind": "daily",
            "run_at": "",
            "local_time": "08:30",
            "timezone": "Asia/Hong_Kong",
            "prompt_text": "生成已确认晨报",
            "skill_refs": [],
            "media_policy": "none",
        }
        creating = asyncio.create_task(
            asyncio.to_thread(runtime.automation_upsert, sid, spec)
        )
        assert await asyncio.to_thread(entered.wait, 2)
        with pytest.raises(ChannelRuntimeError) as busy:
            runtime.prepare_session_archive(sid)
        assert busy.value.reason_code == "channel_lifecycle_busy"

        release.set()
        created = await creating
        assert created["ok"] is True
        runtime.prepare_session_archive(sid)
        assert store.automation(created["automation_id"]).enabled is False

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("outcome", "bot_status", "binding_status", "error_code"),
    [
        (TelegramBotIdentity(bot_id="100001", username="same"), BotStatus.ACTIVE, BindingStatus.ACTIVE, None),
        (TelegramBotIdentity(bot_id="999999", username="changed"), BotStatus.PAUSED, BindingStatus.PAUSED, "telegram_identity_changed"),
        (
            TelegramRetryable(),
            BotStatus.CREDENTIAL_UNAVAILABLE,
            BindingStatus.PAUSED,
            "credential_unavailable",
        ),
    ],
)
def test_runtime_start_reverifies_identity_without_deleting_on_network_failure(
    tmp_path, outcome, bot_status, binding_status, error_code
):
    async def scenario():
        runtime, _console, store, secrets, adapter, _pairing, _sid, bot, binding = (
            _runtime_live(tmp_path, adapter_outcome=outcome)
        )
        credential_id = store.bot(bot).credential_id
        await runtime.start()
        try:
            assert store.bot(bot).status is bot_status
            assert store.bot(bot).last_error_code == error_code
            assert store.binding(binding.id).status is binding_status
            assert store.bot(bot).credential_id == credential_id
            assert secrets.deleted == []
            assert len(adapter.get_me_calls) == 1
        finally:
            await runtime.stop()

    asyncio.run(scenario())


def test_runtime_aborts_start_when_changed_identity_cannot_be_fault_paused(
    tmp_path, monkeypatch
):
    async def scenario():
        outcome = TelegramBotIdentity(bot_id="999999", username="changed")
        runtime, _console, store, _secrets, adapter, pairing, _sid, _bot, binding = (
            _runtime_live(tmp_path, adapter_outcome=outcome)
        )

        def unavailable(*_args, **_kwargs):
            raise sqlite3.OperationalError("private database detail")

        monkeypatch.setattr(store, "fault_pause_bot", unavailable)
        with pytest.raises(ChannelRuntimeError) as failed:
            await runtime.start()
        assert failed.value.reason_code == "channel_registry_unavailable"
        assert runtime._background_tasks == ()
        assert pairing.start_calls == 0
        assert store.binding(binding.id).status is BindingStatus.ACTIVE

        await runtime.stop()
        assert adapter.close_calls == 1

    asyncio.run(scenario())


def test_runtime_orphan_reconcile_never_decrypts_or_deletes_reference_or_grace(tmp_path):
    async def scenario():
        now = 2_000_000_000.0
        referenced = "cred_" + "a" * 32
        recent = "cred_" + "b" * 32
        orphan = "cred_" + "c" * 32
        console = ConsoleStore(root=tmp_path / "console")
        sid = console.create_session("密文对账")['id']
        store = ChannelStore(tmp_path / "channels.sqlite3")
        bot = store.create_bot("Secret Bot", draft_session_id=sid)
        store.cas_bot_credential(
            bot,
            expected_revision=0,
            credential_id=referenced,
            telegram_bot_id="888001",
        )
        secrets = _RuntimeSecrets(
            {referenced: "ref", recent: "recent", orphan: "old"},
            metadata=(
                SecretMetadata(referenced, now - 7200),
                SecretMetadata(recent, now - 30),
                SecretMetadata(orphan, now - 7200),
            ),
        )
        runtime = ChannelRuntime(
            console_store=console,
            channel_store=store,
            secret_store=secrets,
            telegram_adapter=_RuntimeAdapter(),
            wall_clock=lambda: now,
            credential_orphan_grace_seconds=3600,
        )

        assert await runtime.reconcile_credential_store() == 1
        assert secrets.deleted == [orphan]
        assert referenced in secrets.values
        assert recent in secrets.values

    asyncio.run(scenario())


def test_runtime_cleanup_queue_never_deletes_a_currently_referenced_credential(tmp_path):
    async def scenario():
        runtime, _console, store, secrets, _adapter, _pairing, _sid, bot, _binding = (
            _runtime_live(tmp_path)
        )
        current = store.bot(bot).credential_id
        assert current is not None
        store.enqueue_credential_cleanup(
            current,
            bot_account_id=bot,
            reason="stale_cleanup_record",
        )

        assert await runtime.retry_credential_cleanup() == 0
        assert secrets.deleted == []
        assert current in store.referenced_credential_ids()
        assert store.credential_cleanup_for_bot(bot) == [current]

    asyncio.run(scenario())


def test_runtime_stop_continues_after_scheduler_failure_and_closes_adapter(tmp_path):
    class FailingAutomation:
        def __init__(self) -> None:
            self.start_calls = 0
            self.stop_calls = 0

        def bind_hooks(self, **_hooks) -> None:
            return None

        async def start(self) -> None:
            self.start_calls += 1

        async def stop(self) -> None:
            self.stop_calls += 1
            if self.stop_calls == 1:
                raise RuntimeError("unsafe provider detail")

    async def scenario():
        console = ConsoleStore(root=tmp_path / "console")
        store = ChannelStore(tmp_path / "channels.sqlite3")
        adapter = _RuntimeAdapter()
        automation = FailingAutomation()
        runtime = ChannelRuntime(
            console_store=console,
            channel_store=store,
            secret_store=_RuntimeSecrets(),
            telegram_adapter=adapter,
            automation_worker=automation,
        )
        pairing = _NoopPairing()
        runtime.pairing = pairing
        runtime.bind_console_hooks(
            emit=lambda *_args, **_kwargs: None,
            is_busy=lambda _sid: False,
            submit_system_turn=lambda *_args, **_kwargs: None,
            on_turn_terminal=lambda *_args, **_kwargs: None,
        )
        await runtime.start()

        with pytest.raises(ChannelRuntimeError) as stopped:
            await runtime.stop()

        assert stopped.value.reason_code == "channel_shutdown_failed"
        assert automation.stop_calls == 1
        assert pairing.stop_calls == 1
        assert adapter.close_calls == 1
        assert runtime._background_tasks == ()
        assert runtime._maintenance_task is None
        assert runtime._automation_started is True

        # A failed component keeps its ownership flag, so a second safe stop
        # retries only that component without re-closing successful resources.
        await runtime.stop()
        assert automation.stop_calls == 2
        assert runtime._automation_started is False
        assert pairing.stop_calls == 1
        assert adapter.close_calls == 1

    asyncio.run(scenario())


def test_runtime_stop_finishes_all_cleanup_before_propagating_cancellation(tmp_path):
    class BlockingAutomation:
        def __init__(self) -> None:
            self.entered = asyncio.Event()
            self.release = asyncio.Event()

        def bind_hooks(self, **_hooks) -> None:
            return None

        async def start(self) -> None:
            return None

        async def stop(self) -> None:
            self.entered.set()
            await self.release.wait()

    async def scenario():
        console = ConsoleStore(root=tmp_path / "console")
        store = ChannelStore(tmp_path / "channels.sqlite3")
        adapter = _RuntimeAdapter()
        automation = BlockingAutomation()
        runtime = ChannelRuntime(
            console_store=console,
            channel_store=store,
            secret_store=_RuntimeSecrets(),
            telegram_adapter=adapter,
            automation_worker=automation,
        )
        pairing = _NoopPairing()
        runtime.pairing = pairing
        runtime.bind_console_hooks(
            emit=lambda *_args, **_kwargs: None,
            is_busy=lambda _sid: False,
            submit_system_turn=lambda *_args, **_kwargs: None,
            on_turn_terminal=lambda *_args, **_kwargs: None,
        )
        await runtime.start()

        stopping = asyncio.create_task(runtime.stop())
        await asyncio.wait_for(automation.entered.wait(), timeout=2)
        stopping.cancel()
        automation.release.set()
        with pytest.raises(asyncio.CancelledError):
            await stopping

        assert runtime._automation_started is False
        assert pairing.stop_calls == 1
        assert adapter.close_calls == 1
        assert runtime._background_tasks == ()

    asyncio.run(scenario())
