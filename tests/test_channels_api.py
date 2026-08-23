from __future__ import annotations

import asyncio
import json
import threading
import traceback
from datetime import datetime, timedelta, timezone

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from guanlan_v2.channels.api import build_channel_router
from guanlan_v2.channels import runtime as runtime_mod
from guanlan_v2.channels.models import BindingStatus, BotStatus, OutboundPart, OutboxState
from guanlan_v2.channels.runtime import (
    ActionTicketStore,
    ChannelRuntime,
    ChannelRuntimeError,
)
from guanlan_v2.channels.secrets import SecretUnavailable
from guanlan_v2.channels.store import ChannelConflict, ChannelStore
from guanlan_v2.channels.telegram import (
    TelegramBotIdentity,
    TelegramCredentialInvalid,
    TelegramPollConflict,
)
from guanlan_v2.console.store import ConsoleStore
from guanlan_v2.console.api import build_console_router


class _StatusRuntime:
    def __init__(self) -> None:
        self.tickets = ActionTicketStore(ttl_seconds=120)

    def status_for_session(self, sid: str):
        return {
            "available": True,
            "session_id": sid,
            "view_state": "none",
            "draft": None,
            "bot": None,
            "recipient": None,
            "pairing_requests": [],
            "counts": {"pending": 0, "retry_wait": 0, "uncertain": 0, "terminal": 0},
            "warnings": [],
            "recent_deliveries": [],
            "automations": [],
        }

    def issue_action_ticket(self, sid: str, action: str, subject_id: str) -> str:
        return self.tickets.issue(sid, action, subject_id)


class _TokenRuntime(_StatusRuntime):
    def __init__(self, sid: str = "cs_one", draft_id: str = "bot_draft") -> None:
        super().__init__()
        self.sid = sid
        self.draft_id = draft_id
        self.submissions: list[tuple[str, str, str]] = []
        self.placeholders: list[str] = []
        self.pause_calls: list[str] = []

    def current_draft(self, sid: str):
        if sid != self.sid:
            return None
        return type("Draft", (), {"id": self.draft_id})()

    def consume_action_ticket(self, ticket: str, sid: str, action: str, subject_id: str) -> bool:
        return self.tickets.consume(ticket, sid, action, subject_id)

    def extract_telegram_tokens(self, text: str):
        return ChannelRuntime.token_matches(text)

    async def submit_token(self, sid: str, draft_id: str, token: str):
        self.submissions.append((sid, draft_id, token))

    def record_secret_placeholder(self, sid: str):
        self.placeholders.append(sid)

    def pause(self, sid: str):
        self.pause_calls.append(sid)


def _app(runtime):
    app = FastAPI()
    app.include_router(build_channel_router(runtime))
    return app


TOKEN_A = "123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZ_abcdefghi"
TOKEN_B = "234567890:ZYXWVUTSRQPONMLKJIHGFEDCBA_abcdef"
TOKEN_C = "345678901:ABCDEFGHIJKLMNOPQRSTUVWX_abcdefghi"


class _MemorySecrets:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.put_calls: list[str] = []
        self.delete_calls: list[str] = []
        self.fail_delete_ids: set[str] = set()
        self.fail_get = False

    def put(self, value: str) -> str:
        self.put_calls.append(value)
        credential_id = f"cred_{len(self.values) + 1:032x}"
        self.values[credential_id] = value
        return credential_id

    def get(self, credential_id: str) -> str:
        if self.fail_get:
            raise SecretUnavailable()
        try:
            return self.values[credential_id]
        except KeyError:
            raise SecretUnavailable() from None

    def delete(self, credential_id: str) -> None:
        self.delete_calls.append(credential_id)
        if credential_id in self.fail_delete_ids:
            raise SecretUnavailable()
        self.values.pop(credential_id, None)


class _BlockingSecrets(_MemorySecrets):
    """Deterministic cancellation seam for DPAPI-style critical sections."""

    def __init__(self) -> None:
        super().__init__()
        self.block_put = False
        self.put_started = threading.Event()
        self.put_release = threading.Event()
        self.block_delete_id: str | None = None
        self.delete_started = threading.Event()
        self.delete_release = threading.Event()

    def put(self, value: str) -> str:
        credential_id = super().put(value)
        if self.block_put:
            self.put_started.set()
            assert self.put_release.wait(timeout=3)
        return credential_id

    def delete(self, credential_id: str) -> None:
        self.delete_calls.append(credential_id)
        if credential_id == self.block_delete_id:
            self.delete_started.set()
            assert self.delete_release.wait(timeout=3)
        if credential_id in self.fail_delete_ids:
            raise SecretUnavailable()
        self.values.pop(credential_id, None)


class _IdentityAdapter:
    def __init__(self) -> None:
        self.responses: dict[str, object] = {}
        self.calls: list[str] = []

    async def get_me(self, token: str):
        self.calls.append(token)
        response = self.responses[token]
        if isinstance(response, BaseException):
            raise response
        return response


class _BlockingIdentityAdapter(_IdentityAdapter):
    """Keep getMe in flight while exercising cross-router lifecycle guards."""

    def __init__(self) -> None:
        super().__init__()
        self.started = threading.Event()
        self.release = threading.Event()
        self.block_token: str | None = None

    async def get_me(self, token: str):
        self.calls.append(token)
        if token == self.block_token:
            self.started.set()
            assert await asyncio.to_thread(self.release.wait, 3)
        response = self.responses[token]
        if isinstance(response, BaseException):
            raise response
        return response


def _runtime_stack(tmp_path):
    console = ConsoleStore(root=tmp_path / "console")
    sid = console.create_session(title="channel test")["id"]
    store = ChannelStore(tmp_path / "channels.sqlite3")
    secrets = _MemorySecrets()
    adapter = _IdentityAdapter()
    runtime = ChannelRuntime(
        console_store=console,
        channel_store=store,
        secret_store=secrets,
        telegram_adapter=adapter,
    )
    draft = runtime.create_draft(sid, "Safe Bot")
    return runtime, console, store, secrets, adapter, sid, draft


@pytest.fixture
def anyio_backend():
    return "asyncio"


def test_action_ticket_is_bound_to_sid_action_subject_and_single_use():
    tickets = ActionTicketStore(ttl_seconds=120)
    issued = tickets.issue("cs_one", "telegram:submit-token", "bot_one")

    assert not tickets.consume(issued, "cs_two", "telegram:submit-token", "bot_one")
    assert not tickets.consume(issued, "cs_one", "pause", "bot_one")
    assert not tickets.consume(issued, "cs_one", "telegram:submit-token", "bot_two")
    assert tickets.consume(issued, "cs_one", "telegram:submit-token", "bot_one")
    assert not tickets.consume(issued, "cs_one", "telegram:submit-token", "bot_one")


def test_channel_status_is_stable_redacted_and_cross_site_requests_fail_closed():
    app = _app(_StatusRuntime())

    with TestClient(app) as client:
        response = client.get("/console/channels/session/cs_one")
        hostile = client.get(
            "http://testserver/console/channels/session/cs_one",
            headers={"Origin": "https://attacker.example", "Sec-Fetch-Site": "cross-site"},
        )

    assert response.status_code == 200
    dto = response.json()
    assert set(dto) == {
        "available", "session_id", "view_state", "draft", "bot", "recipient",
        "pairing_requests", "counts", "warnings", "recent_deliveries", "automations",
    }
    assert dto["session_id"] == "cs_one"
    assert dto["pairing_requests"] == []
    assert dto["recent_deliveries"] == []
    assert dto["automations"] == []
    assert hostile.status_code == 403


def test_channel_origin_boundary_allows_same_local_origin_and_rejects_null_cross_site():
    app = _app(_StatusRuntime())
    with TestClient(app, base_url="http://127.0.0.1") as client:
        same = client.get(
            "/console/channels/session/cs_one",
            headers={"Origin": "http://127.0.0.1", "Sec-Fetch-Site": "same-origin"},
        )
        null = client.get("/console/channels/session/cs_one", headers={"Origin": "null"})
        hostile = client.get(
            "/console/channels/session/cs_one",
            headers={"Origin": "https://attacker.example"},
        )
        fetch_cross_site = client.get(
            "/console/channels/session/cs_one", headers={"Sec-Fetch-Site": "cross-site"}
        )

    assert same.status_code == 200
    assert null.status_code == hostile.status_code == fetch_cross_site.status_code == 403
    assert all(response.json()["reason_code"] == "channel_origin_forbidden"
               for response in (null, hostile, fetch_cross_site))


def test_action_ticket_expires_without_retaining_raw_value():
    now = [10.0]
    tickets = ActionTicketStore(ttl_seconds=120, clock=lambda: now[0])
    ticket = tickets.issue("cs_one", "telegram:submit-token", "bot_one")
    now[0] += 120

    assert not tickets.consume(ticket, "cs_one", "telegram:submit-token", "bot_one")


def test_token_shaped_ids_are_rejected_without_status_or_ticket_echo():
    with pytest.raises(ValueError):
        ActionTicketStore().issue("cs_one", "telegram:pause", TOKEN_A)

    with TestClient(_app(_StatusRuntime())) as client:
        bad_status = client.get(f"/console/channels/session/{TOKEN_A}")
        bad_subject = client.post(
            "/console/channels/session/cs_one/request-token",
            json={"action": "telegram:pause", "subject_id": TOKEN_A},
        )

    assert bad_status.status_code == bad_subject.status_code == 400
    assert TOKEN_A.encode() not in bad_status.content
    assert TOKEN_A.encode() not in bad_subject.content


def test_token_endpoint_has_manual_bounded_zero_echo_failure_paths():
    runtime = _TokenRuntime()
    token = "123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZ_abcdefghi"
    with TestClient(_app(runtime)) as client:
        cases = [
            (b"{bad-json " + token.encode(), "application/json"),
            (json.dumps({"sid": runtime.sid, "draft_id": runtime.draft_id}).encode(), "application/json"),
            (json.dumps({"sid": runtime.sid, "draft_id": runtime.draft_id, "token": 7}).encode(), "application/json"),
            (("x" * (16 * 1024 + 1) + token).encode(), "application/json"),
        ]
        responses = [
            client.post("/console/channels/telegram/token", content=body, headers={"Content-Type": content_type})
            for body, content_type in cases
        ]

    assert all(response.status_code in {400, 413} for response in responses)
    assert all(response.json() == {"ok": False, "reason_code": "telegram_token_submission_invalid"}
               for response in responses)
    assert all(token.encode() not in response.content for response in responses)
    assert runtime.submissions == []


def test_token_endpoint_requires_bound_ticket_and_never_echoes_or_replays_token():
    runtime = _TokenRuntime()
    token = "123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZ_abcdefghi"
    with TestClient(_app(runtime)) as client:
        no_ticket = client.post("/console/channels/telegram/token", json={
            "sid": runtime.sid, "draft_id": runtime.draft_id, "token": token,
        })
        ticket_response = client.post(
            f"/console/channels/session/{runtime.sid}/request-token",
            json={"action": "telegram:submit-token", "subject_id": runtime.draft_id},
        )
        ticket = ticket_response.json()["request_token"]
        accepted = client.post("/console/channels/telegram/token", json={
            "sid": runtime.sid,
            "draft_id": runtime.draft_id,
            "token": token,
            "request_token": ticket,
        })
        replay = client.post("/console/channels/telegram/token", json={
            "sid": runtime.sid,
            "draft_id": runtime.draft_id,
            "token": token,
            "request_token": ticket,
        })
        wrong_draft = client.post("/console/channels/telegram/token", json={
            "sid": runtime.sid,
            "draft_id": "bot_other",
            "token": token,
            "request_token": runtime.issue_action_ticket(
                runtime.sid, "telegram:submit-token", runtime.draft_id
            ),
        })

    assert no_ticket.status_code == 403
    assert no_ticket.json()["reason_code"] == "telegram_token_secure_submit_required"
    assert ticket_response.status_code == 200
    assert accepted.status_code == 200 and accepted.json()["secret_submitted"] is True
    assert replay.status_code == 403
    assert replay.json()["reason_code"] == "telegram_token_secure_submit_required"
    assert wrong_draft.status_code == 409
    assert wrong_draft.json()["reason_code"] == "telegram_token_needs_draft"
    assert runtime.submissions == [(runtime.sid, runtime.draft_id, token)]
    assert runtime.placeholders == [runtime.sid]
    for response in (no_ticket, accepted, replay, wrong_draft):
        assert token.encode() not in response.content


def test_dedicated_token_endpoint_records_one_projection_suppressed_placeholder_only(tmp_path):
    runtime, console, _store, _secrets, adapter, sid, draft = _runtime_stack(tmp_path)
    adapter.responses[TOKEN_A] = TelegramBotIdentity(bot_id="100", username="safe")
    with TestClient(_app(runtime)) as client:
        ticket = client.post(
            f"/console/channels/session/{sid}/request-token",
            json={"action": "telegram:submit-token", "subject_id": draft.id},
        ).json()["request_token"]
        accepted = client.post("/console/channels/telegram/token", json={
            "sid": sid, "draft_id": draft.id, "token": TOKEN_A, "request_token": ticket,
        })
        replay = client.post("/console/channels/telegram/token", json={
            "sid": sid, "draft_id": draft.id, "token": TOKEN_A, "request_token": ticket,
        })

    events = console.read_events(sid)
    assert accepted.status_code == 200 and accepted.json()["secret_submitted"] is True
    assert replay.status_code == 403
    assert [event["type"] for event in events] == ["user_msg"]
    assert events[0]["text"] == "[Telegram Bot Token 已安全提交]"
    assert events[0]["channel_projection"] == "never"
    assert events[0]["secret_placeholder"] is True
    assert TOKEN_A not in str(events)


def test_dedicated_token_reservation_blocks_console_archive_delete_and_second_submit(tmp_path):
    runtime, console, _store, _secrets, _adapter, sid, draft = _runtime_stack(tmp_path)
    adapter = _BlockingIdentityAdapter()
    adapter.block_token = TOKEN_A
    adapter.responses[TOKEN_A] = TelegramBotIdentity(bot_id="100", username="safe")
    runtime.telegram_adapter = adapter

    class _NoopAgent:
        messages: list[object] = []

        async def run_turn(self, *_args, **_kwargs):
            if False:
                yield None

    app = FastAPI()
    app.include_router(build_console_router(
        store=console, agent_factory=lambda _sid: _NoopAgent(), channel_runtime=runtime,
    ))
    app.include_router(build_channel_router(runtime))

    with TestClient(app) as client:
        first_ticket = client.post(
            f"/console/channels/session/{sid}/request-token",
            json={"action": "telegram:submit-token", "subject_id": draft.id},
        ).json()["request_token"]
        second_ticket = client.post(
            f"/console/channels/session/{sid}/request-token",
            json={"action": "telegram:submit-token", "subject_id": draft.id},
        ).json()["request_token"]
        result: dict[str, object] = {}

        def submit_first() -> None:
            with TestClient(app) as submit_client:
                result["response"] = submit_client.post("/console/channels/telegram/token", json={
                    "sid": sid,
                    "draft_id": draft.id,
                    "token": TOKEN_A,
                    "request_token": first_ticket,
                })

        worker = threading.Thread(target=submit_first)
        worker.start()
        assert adapter.started.wait(timeout=2)
        try:
            archived = client.post(f"/console/sessions/{sid}/archive")
            deleted = client.delete(f"/console/sessions/{sid}")
            ordinary_send = client.post("/console/send", json={"sid": sid, "text": "ordinary"})
            duplicate = client.post("/console/channels/telegram/token", json={
                "sid": sid,
                "draft_id": draft.id,
                "token": TOKEN_B,
                "request_token": second_ticket,
            })
            assert archived.status_code == 409
            assert deleted.status_code == 200 and deleted.json()["ok"] is False
            assert ordinary_send.status_code == 200 and ordinary_send.json()["ok"] is False
            assert duplicate.status_code == 409
            assert duplicate.json()["reason_code"] == "telegram_token_submission_busy"
            assert console.read_events(sid) == []
        finally:
            adapter.release.set()
            worker.join(timeout=3)

    accepted = result["response"]
    assert getattr(accepted, "status_code") == 200
    # The rejected second request never consumed its narrowly scoped ticket.
    assert runtime.consume_action_ticket(
        second_ticket, sid, "telegram:submit-token", draft.id
    )


def test_sensitive_channel_action_requires_ticket_bound_to_action_and_subject():
    runtime = _TokenRuntime()
    with TestClient(_app(runtime)) as client:
        no_ticket = client.post(
            f"/console/channels/session/{runtime.sid}/pause",
            json={"subject_id": runtime.draft_id},
        )
        ticket = client.post(
            f"/console/channels/session/{runtime.sid}/request-token",
            json={"action": "telegram:pause", "subject_id": runtime.draft_id},
        ).json()["request_token"]
        wrong_action = client.post(
            f"/console/channels/session/{runtime.sid}/resume",
            json={"subject_id": runtime.draft_id, "request_token": ticket},
        )
        accepted = client.post(
            f"/console/channels/session/{runtime.sid}/pause",
            json={"subject_id": runtime.draft_id, "request_token": ticket},
        )
        replay = client.post(
            f"/console/channels/session/{runtime.sid}/pause",
            json={"subject_id": runtime.draft_id, "request_token": ticket},
        )

    assert no_ticket.status_code == 403
    assert wrong_action.status_code == 403
    assert accepted.status_code == 200
    assert replay.status_code == 403
    assert runtime.pause_calls == [runtime.sid]


def test_status_projection_drops_credentials_bot_ids_and_free_text_from_runtime():
    token = "123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZ_abcdefghi"

    class _LeakyRuntime(_StatusRuntime):
        def status_for_session(self, sid: str):
            payload = super().status_for_session(sid)
            payload.update({
                "view_state": "active",
                "draft": {"draft_id": "bot_safe", "credential_id": "cred_deadbeef"},
                "bot": {"display_name": "safe", "username": "safe_bot", "telegram_bot_id": "99"},
                "recipient": {"user_id": "7", "username": "u", "display_name": "d"},
                "pairing_requests": [{
                    "request_id": "pair_safe", "user_id": "8", "code_hint": "…AB",
                    "token": token,
                }],
                "recent_deliveries": [{
                    "outbox_id": "out_safe", "kind": "text", "state": "sent",
                    "media_id": None, "created_at": "2026-01-01T00:00:00+00:00",
                    "sent_at": None, "attempts": 1, "error_class": None,
                    "text": token, "caption": token, "path": "C:/secret",
                }],
                "automations": [{
                    "automation_id": "auto_safe", "name": "safe", "schedule_kind": "daily",
                    "next_run_at": None, "last_run_at": None, "status": "enabled",
                    "pause_reason": None, "prompt_text": token,
                }],
            })
            return payload

    with TestClient(_app(_LeakyRuntime())) as client:
        response = client.get("/console/channels/session/cs_one")

    rendered = response.content.decode("utf-8")
    assert response.status_code == 200
    assert token not in rendered
    assert "credential_id" not in rendered
    assert "telegram_bot_id" not in rendered
    assert '"text":' not in rendered and '"caption":' not in rendered and '"path":' not in rendered


@pytest.mark.anyio
async def test_runtime_get_me_failure_writes_no_secret_and_suppresses_raw_exception_cause(tmp_path):
    runtime, _console, store, secrets, adapter, sid, draft = _runtime_stack(tmp_path)
    unsafe = RuntimeError(f"provider saw {TOKEN_A}")
    adapter.responses[TOKEN_A] = unsafe

    with pytest.raises(ChannelRuntimeError) as caught:
        await runtime.submit_token(sid, draft.id, TOKEN_A)

    rendered_traceback = "".join(traceback.format_exception(caught.value))
    assert caught.value.reason_code == "telegram_verification_failed"
    assert TOKEN_A not in repr(caught.value)
    assert TOKEN_A not in rendered_traceback
    assert secrets.put_calls == []
    assert store.bot(draft.id).credential_id is None


@pytest.mark.anyio
async def test_runtime_db_commit_failure_deletes_or_queues_new_credential_without_losing_old(tmp_path, monkeypatch):
    runtime, _console, store, secrets, adapter, sid, draft = _runtime_stack(tmp_path)
    adapter.responses[TOKEN_A] = TelegramBotIdentity(bot_id="100", username="safe")

    def fail_commit(*_args, **_kwargs):
        raise ChannelConflict(f"simulated {TOKEN_A}")

    monkeypatch.setattr(store, "cas_bot_credential", fail_commit)
    with pytest.raises(ChannelRuntimeError) as caught:
        await runtime.submit_token(sid, draft.id, TOKEN_A)

    assert caught.value.reason_code == "telegram_token_commit_failed"
    assert secrets.values == {}
    assert store.bot(draft.id).credential_id is None


@pytest.mark.anyio
async def test_runtime_db_failure_queues_orphan_when_compensating_delete_fails(tmp_path, monkeypatch):
    runtime, _console, store, secrets, adapter, sid, draft = _runtime_stack(tmp_path)
    adapter.responses[TOKEN_A] = TelegramBotIdentity(bot_id="100", username="safe")

    def fail_commit(*_args, **_kwargs):
        raise ChannelConflict("db commit failed")

    monkeypatch.setattr(store, "cas_bot_credential", fail_commit)
    # The first generated opaque ID is deterministic in this in-memory fake.
    secrets.fail_delete_ids.add("cred_00000000000000000000000000000001")
    with pytest.raises(ChannelRuntimeError) as caught:
        await runtime.submit_token(sid, draft.id, TOKEN_A)

    assert caught.value.reason_code == "telegram_token_commit_failed"
    assert len(secrets.values) == 1
    with store.connect() as conn:
        queued = conn.execute(
            "SELECT credential_id, bot_account_id, reason FROM credential_cleanup_queue"
        ).fetchall()
    assert [tuple(row) for row in queued] == [
        ("cred_00000000000000000000000000000001", draft.id, "db_commit_failed")
    ]


@pytest.mark.anyio
async def test_cancelled_secret_put_cas_and_old_cleanup_finish_before_cancellation_returns(tmp_path):
    runtime, _console, store, _secrets, adapter, sid, draft = _runtime_stack(tmp_path)
    secrets = _BlockingSecrets()
    runtime.secret_store = secrets
    adapter.responses[TOKEN_A] = TelegramBotIdentity(bot_id="100", username="safe")
    secrets.block_put = True
    install = asyncio.create_task(runtime.submit_token(sid, draft.id, TOKEN_A))
    assert await asyncio.to_thread(secrets.put_started.wait, 2)
    install.cancel()
    secrets.put_release.set()
    with pytest.raises(asyncio.CancelledError):
        await install

    installed = store.bot(draft.id)
    assert installed.credential_id in secrets.values

    adapter.responses[TOKEN_B] = TelegramBotIdentity(bot_id="100", username="safe")
    secrets.block_put = False
    secrets.block_delete_id = installed.credential_id
    replacement = asyncio.create_task(runtime.replace_token(sid, installed.id, TOKEN_B))
    assert await asyncio.to_thread(secrets.delete_started.wait, 2)
    replacement.cancel()
    secrets.delete_release.set()
    with pytest.raises(asyncio.CancelledError):
        await replacement

    current = store.bot(installed.id)
    assert current.credential_id in secrets.values
    assert installed.credential_id not in secrets.values
    assert store.credential_cleanup_for_bot(installed.id) == []


@pytest.mark.anyio
async def test_runtime_replace_failure_preserves_old_token_and_identity_change_fault_pauses_binding(tmp_path):
    runtime, _console, store, secrets, adapter, sid, draft = _runtime_stack(tmp_path)
    adapter.responses[TOKEN_A] = TelegramBotIdentity(bot_id="100", username="first_bot")
    initial = await runtime.submit_token(sid, draft.id, TOKEN_A)
    binding = store.bind_for_test(
        sid, initial.id, user_id="77", chat_id="77", sync_from_event_id=0
    )
    old_credential = initial.credential_id

    adapter.responses[TOKEN_B] = TelegramCredentialInvalid()
    with pytest.raises(ChannelRuntimeError) as failed:
        await runtime.replace_token(sid, initial.id, TOKEN_B)
    unchanged = store.bot(initial.id)
    assert failed.value.reason_code == "telegram_credential_invalid"
    assert unchanged.credential_id == old_credential
    assert unchanged.telegram_bot_id == "100"
    assert secrets.values[old_credential] == TOKEN_A

    adapter.responses[TOKEN_C] = TelegramBotIdentity(bot_id="200", username="second_bot")
    replaced = await runtime.replace_token(sid, initial.id, TOKEN_C)
    paused = store.binding(binding.id)
    status = runtime.status_for_session(sid)
    assert replaced.credential_id != old_credential
    assert old_credential not in secrets.values
    assert paused.status is BindingStatus.PAUSED
    assert paused.pause_reason == "bot_identity_changed"
    assert replaced.status is BotStatus.AWAITING_PAIRING
    assert status["view_state"] == "needs_reconnect"
    assert status["recipient"] is not None  # historical recipient is fault-paused, never active.


@pytest.mark.anyio
async def test_runtime_disconnect_clean_state_is_none_but_cleanup_failure_is_visible(tmp_path):
    runtime, _console, store, secrets, adapter, sid, draft = _runtime_stack(tmp_path)
    adapter.responses[TOKEN_A] = TelegramBotIdentity(bot_id="100", username="safe")
    installed = await runtime.submit_token(sid, draft.id, TOKEN_A)
    binding = store.bind_for_test(sid, installed.id, user_id="77", chat_id="77", sync_from_event_id=0)
    pending = store.project_event_for_test(
        binding.id, source_event_id=1,
        parts=[OutboundPart(kind="text", payload_part=0, text="safe delivery")],
    )[0]

    clean = runtime.disconnect(sid)
    assert clean["view_state"] == "none"
    assert clean["recipient"] is None
    assert clean["warnings"] == []
    assert store.bot(installed.id).status is BotStatus.DISCONNECTED
    assert store.outbox(pending.id).state.value == "cancelled"

    runtime, _console, store, secrets, adapter, sid, draft = _runtime_stack(tmp_path / "queued")
    adapter.responses[TOKEN_A] = TelegramBotIdentity(bot_id="100", username="safe")
    installed = await runtime.submit_token(sid, draft.id, TOKEN_A)
    store.bind_for_test(sid, installed.id, user_id="77", chat_id="77", sync_from_event_id=0)
    secrets.fail_delete_ids.add(installed.credential_id)

    queued = runtime.disconnect(sid)
    # Cleanup is an independent durable obligation: it must not overwrite the
    # Bot's disconnected business state or leave a live credential reference.
    assert store.bot(installed.id).status is BotStatus.DISCONNECTED
    assert store.bot(installed.id).credential_id is None
    assert queued["view_state"] == "none"
    assert queued["warnings"] == [{"error_code": "credential_cleanup_pending"}]
    with store.connect() as conn:
        row = conn.execute(
            "SELECT credential_id, reason FROM credential_cleanup_queue"
        ).fetchone()
    assert tuple(row) == (installed.credential_id, "disconnect_cleanup_failed")


@pytest.mark.anyio
async def test_runtime_reverify_faults_are_durable_and_status_needs_reconnect(tmp_path):
    runtime, _console, store, secrets, adapter, sid, draft = _runtime_stack(tmp_path)
    adapter.responses[TOKEN_A] = TelegramBotIdentity(bot_id="100", username="safe")
    installed = await runtime.submit_token(sid, draft.id, TOKEN_A)
    binding = store.bind_for_test(sid, installed.id, user_id="77", chat_id="77", sync_from_event_id=0)

    adapter.responses[TOKEN_A] = TelegramCredentialInvalid()
    with pytest.raises(ChannelRuntimeError) as invalid:
        await runtime.reverify(sid)
    assert invalid.value.reason_code == "telegram_credential_invalid"
    assert store.bot(installed.id).status is BotStatus.CREDENTIAL_INVALID
    assert store.binding(binding.id).status is BindingStatus.PAUSED
    assert runtime.status_for_session(sid)["view_state"] == "needs_reconnect"
    with TestClient(_app(runtime)) as client:
        assert client.get(f"/console/channels/session/{sid}").json()["view_state"] == "needs_reconnect"

    adapter.responses[TOKEN_A] = TelegramBotIdentity(bot_id="100", username="safe")
    recovered = await runtime.reverify(sid)
    assert store.bot(installed.id).status is BotStatus.ACTIVE
    assert store.binding(binding.id).status is BindingStatus.ACTIVE
    assert recovered["view_state"] == "active"

    runtime, _console, store, secrets, adapter, sid, draft = _runtime_stack(tmp_path / "identity")
    adapter.responses[TOKEN_A] = TelegramBotIdentity(bot_id="100", username="safe")
    installed = await runtime.submit_token(sid, draft.id, TOKEN_A)
    binding = store.bind_for_test(sid, installed.id, user_id="77", chat_id="77", sync_from_event_id=0)
    adapter.responses[TOKEN_A] = TelegramBotIdentity(bot_id="200", username="other")
    with pytest.raises(ChannelRuntimeError) as changed:
        await runtime.reverify(sid)
    assert changed.value.reason_code == "telegram_identity_changed"
    assert store.bot(installed.id).status is BotStatus.PAUSED
    assert store.bot(installed.id).last_error_code == "telegram_identity_changed"
    assert store.binding(binding.id).status is BindingStatus.PAUSED
    assert runtime.status_for_session(sid)["view_state"] == "needs_reconnect"

    runtime, _console, store, secrets, adapter, sid, draft = _runtime_stack(tmp_path / "poll")
    adapter.responses[TOKEN_A] = TelegramBotIdentity(bot_id="100", username="safe")
    installed = await runtime.submit_token(sid, draft.id, TOKEN_A)
    binding = store.bind_for_test(sid, installed.id, user_id="77", chat_id="77", sync_from_event_id=0)
    adapter.responses[TOKEN_A] = TelegramPollConflict()
    with pytest.raises(ChannelRuntimeError) as conflicted:
        await runtime.reverify(sid)
    assert conflicted.value.reason_code == "telegram_poll_conflict"
    assert store.bot(installed.id).status is BotStatus.POLL_CONFLICT
    assert store.binding(binding.id).status is BindingStatus.PAUSED

    adapter.responses[TOKEN_A] = TelegramBotIdentity(bot_id="100", username="safe")
    recovered = await runtime.reverify(sid)
    assert store.bot(installed.id).status is BotStatus.ACTIVE
    assert store.binding(binding.id).status is BindingStatus.ACTIVE
    assert recovered["view_state"] == "active"


@pytest.mark.anyio
async def test_runtime_replace_same_identity_recovers_fault_but_cleanup_plus_identity_stays_visible(tmp_path):
    runtime, _console, store, secrets, adapter, sid, draft = _runtime_stack(tmp_path)
    adapter.responses[TOKEN_A] = TelegramBotIdentity(bot_id="100", username="safe")
    installed = await runtime.submit_token(sid, draft.id, TOKEN_A)
    binding = store.bind_for_test(sid, installed.id, user_id="77", chat_id="77", sync_from_event_id=0)
    adapter.responses[TOKEN_A] = TelegramCredentialInvalid()
    with pytest.raises(ChannelRuntimeError):
        await runtime.reverify(sid)

    adapter.responses[TOKEN_B] = TelegramBotIdentity(bot_id="100", username="safe")
    recovered = await runtime.replace_token(sid, installed.id, TOKEN_B)
    assert recovered.status is BotStatus.ACTIVE
    assert store.binding(binding.id).status is BindingStatus.ACTIVE
    assert runtime.status_for_session(sid)["view_state"] == "active"

    runtime, _console, store, secrets, adapter, sid, draft = _runtime_stack(tmp_path / "combined")
    adapter.responses[TOKEN_A] = TelegramBotIdentity(bot_id="100", username="first")
    installed = await runtime.submit_token(sid, draft.id, TOKEN_A)
    binding = store.bind_for_test(sid, installed.id, user_id="77", chat_id="77", sync_from_event_id=0)
    secrets.fail_delete_ids.add(installed.credential_id)
    adapter.responses[TOKEN_C] = TelegramBotIdentity(bot_id="200", username="second")
    await runtime.replace_token(sid, installed.id, TOKEN_C)

    combined = runtime.status_for_session(sid)
    # Identity re-pairing remains visible even if the old ciphertext's cleanup
    # was queued.  The queue-derived warning is intentionally orthogonal.
    assert store.bot(installed.id).status is BotStatus.AWAITING_PAIRING
    assert store.binding(binding.id).pause_reason == "bot_identity_changed"
    assert combined["view_state"] == "needs_reconnect"
    assert {warning["error_code"] for warning in combined["warnings"]} == {
        "telegram_identity_changed", "credential_cleanup_pending"
    }


@pytest.mark.anyio
async def test_runtime_uses_worker_threads_for_store_and_secret_boundaries(tmp_path, monkeypatch):
    runtime, _console, _store, _secrets, adapter, sid, draft = _runtime_stack(tmp_path)
    adapter.responses[TOKEN_A] = TelegramBotIdentity(bot_id="100", username="safe")
    original = runtime_mod.asyncio.to_thread
    seen: list[str] = []

    async def tracked(function, /, *args, **kwargs):
        seen.append(getattr(function, "__name__", type(function).__name__))
        return await original(function, *args, **kwargs)

    monkeypatch.setattr(runtime_mod.asyncio, "to_thread", tracked)
    await runtime.submit_token(sid, draft.id, TOKEN_A)

    assert "target" in seen
    assert "put" in seen
    assert "cas_bot_credential" in seen


def test_store_rejects_token_shaped_channel_free_text_before_sqlite_write(tmp_path):
    store = ChannelStore(tmp_path / "channels.sqlite3")
    bot = store.create_bot("safe")
    store.activate_test_bot(bot, telegram_bot_id="100")
    binding = store.bind_for_test("cs_safe", bot, user_id="1", chat_id="1", sync_from_event_id=0)

    with pytest.raises(ValueError):
        store.create_bot(TOKEN_A)
    with pytest.raises(ValueError):
        store.project_event_for_test(
            binding.id,
            source_event_id=1,
            parts=[OutboundPart(kind="text", payload_part=0, text=TOKEN_A)],
        )
    with pytest.raises(ValueError):
        store.project_event_for_test(
            binding.id,
            source_event_id=1,
            parts=[OutboundPart(kind="media", payload_part=0, media_id="media_safe", caption=TOKEN_A)],
        )
    with pytest.raises(ValueError):
        store.create_automation(
            name="safe", session_id="cs_safe", schedule_kind="daily", prompt_text=TOKEN_A
        )
    with pytest.raises(ValueError):
        store.transition_bot(
            bot, BotStatus.PAUSED, error_code=TOKEN_A
        )
    with pytest.raises(ValueError):
        store.transition_binding(
            binding.id, BindingStatus.PAUSED, pause_reason=TOKEN_A
        )
    item = store.project_event_for_test(
        binding.id,
        source_event_id=2,
        parts=[OutboundPart(kind="text", payload_part=0, text="safe")],
    )[0]
    with pytest.raises(ValueError):
        store.transition_outbox_for_test(
            item.id,
            OutboxState.SENDING,
            lease_owner=TOKEN_A,
        )
    with pytest.raises(ValueError):
        store.create_automation(
            name="safe",
            session_id="cs_safe",
            schedule_kind="daily",
            prompt_text="safe",
            created_by=TOKEN_A,
        )

    assert TOKEN_A.encode() not in (tmp_path / "channels.sqlite3").read_bytes()


def test_paused_or_disconnected_binding_cannot_project_or_claim_new_delivery(tmp_path):
    store = ChannelStore(tmp_path / "channels.sqlite3")
    bot_id = store.create_bot("safe", draft_session_id="cs_safe")
    store.activate_test_bot(bot_id, telegram_bot_id="100")
    binding = store.bind_for_test(
        "cs_safe", bot_id, user_id="1", chat_id="1", sync_from_event_id=0
    )
    store.project_event_for_test(
        binding.id,
        source_event_id=1,
        parts=[OutboundPart(kind="text", payload_part=0, text="safe")],
    )
    store.set_session_manual_pause("cs_safe", paused=True)

    with pytest.raises(ChannelConflict):
        store.project_event_for_test(
            binding.id,
            source_event_id=2,
            parts=[OutboundPart(kind="text", payload_part=0, text="must not queue")],
        )
    assert store.claim_next_outbox(
        binding.id,
        owner="worker",
        lease_until=datetime.now(timezone.utc) + timedelta(seconds=30),
    ) is None


def test_credential_cas_durably_queues_the_superseded_reference(tmp_path):
    """A crash after CAS still leaves old ciphertext recoverable on restart."""
    store = ChannelStore(tmp_path / "channels.sqlite3")
    bot_id = store.create_bot("safe", draft_session_id="cs_safe")
    first = store.cas_bot_credential(
        bot_id,
        expected_revision=0,
        credential_id="cred_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        telegram_bot_id="100",
    )
    store.cas_bot_credential(
        bot_id,
        expected_revision=first.credential_revision,
        credential_id="cred_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
        telegram_bot_id="100",
    )
    assert store.credential_cleanup_for_bot(bot_id) == [
        "cred_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    ]


def test_active_replace_is_prepared_then_handed_off_only_to_secure_token_endpoint(tmp_path):
    """An active binding needs a durable, opaque replacement target.

    The management action never carries raw secret material.  It only creates
    the short-lived UI state that lets the dedicated endpoint bind its ticket
    to the real local Bot account.
    """
    runtime, _console, store, secrets, adapter, sid, draft = _runtime_stack(tmp_path)
    adapter.responses[TOKEN_A] = TelegramBotIdentity(bot_id="100", username="first")
    installed = asyncio.run(runtime.submit_token(sid, draft.id, TOKEN_A))
    binding = store.bind_for_test(
        sid, installed.id, user_id="77", chat_id="77", sync_from_event_id=0
    )
    adapter.responses[TOKEN_B] = TelegramBotIdentity(bot_id="100", username="first")

    with TestClient(_app(runtime)) as client:
        prepare_ticket = client.post(
            f"/console/channels/session/{sid}/request-token",
            json={"action": "telegram:replace-token", "subject_id": installed.id},
        ).json()["request_token"]
        prepared = client.post(
            f"/console/channels/session/{sid}/replace-token",
            json={"subject_id": installed.id, "request_token": prepare_ticket},
        )
        assert prepared.status_code == 200
        prepared_status = prepared.json()["status"]
        assert prepared_status["view_state"] == "awaiting_token"
        assert prepared_status["draft"] == {"draft_id": installed.id}

        submit_ticket = client.post(
            f"/console/channels/session/{sid}/request-token",
            json={"action": "telegram:submit-token", "subject_id": installed.id},
        ).json()["request_token"]
        submitted = client.post("/console/channels/telegram/token", json={
            "sid": sid,
            "draft_id": installed.id,
            "token": TOKEN_B,
            "request_token": submit_ticket,
        })

    assert submitted.status_code == 200
    assert store.binding(binding.id).status is BindingStatus.ACTIVE
    assert store.bot(installed.id).status is BotStatus.ACTIVE
    assert store.bot(installed.id).credential_id != installed.credential_id
    assert installed.credential_id not in secrets.values
    assert runtime.status_for_session(sid)["view_state"] == "active"


@pytest.mark.anyio
async def test_replacement_intent_expires_and_awaiting_pairing_credential_submit_rotates(tmp_path):
    runtime, _console, store, secrets, adapter, sid, draft = _runtime_stack(tmp_path)
    adapter.responses[TOKEN_A] = TelegramBotIdentity(bot_id="100", username="first")
    installed = await runtime.submit_token(sid, draft.id, TOKEN_A)
    binding = store.bind_for_test(
        sid, installed.id, user_id="77", chat_id="77", sync_from_event_id=0
    )
    runtime.prepare_replacement(sid)
    with store.connect() as conn:
        conn.execute(
            "UPDATE token_replacement_intents SET expires_at = '2000-01-01T00:00:00+00:00' "
            "WHERE session_id = ?",
            (sid,),
        )
    expired = runtime.status_for_session(sid)
    assert expired["view_state"] == "active"
    assert expired["draft"] is None
    assert store.binding(binding.id).status is BindingStatus.ACTIVE

    # An unbound awaiting-pairing Bot can already have an old credential (for
    # example after an identity-changing replacement).  A new Token is always
    # a rotation, never a blind fresh submit that leaks the old ciphertext.
    runtime, _console, store, secrets, adapter, sid, draft = _runtime_stack(tmp_path / "awaiting")
    adapter.responses[TOKEN_A] = TelegramBotIdentity(bot_id="100", username="first")
    initial = await runtime.submit_token(sid, draft.id, TOKEN_A)
    old_credential = initial.credential_id
    adapter.responses[TOKEN_B] = TelegramBotIdentity(bot_id="100", username="first")
    replacement = await runtime.submit_token(sid, initial.id, TOKEN_B)

    assert replacement.status is BotStatus.AWAITING_PAIRING
    assert replacement.credential_id != old_credential
    assert old_credential not in secrets.values
    assert runtime.status_for_session(sid)["view_state"] == "awaiting_pairing"


@pytest.mark.anyio
async def test_cleanup_queue_is_orthogonal_and_disconnect_clears_credential_reference(tmp_path):
    runtime, _console, store, secrets, adapter, sid, draft = _runtime_stack(tmp_path)
    adapter.responses[TOKEN_A] = TelegramBotIdentity(bot_id="100", username="first")
    installed = await runtime.submit_token(sid, draft.id, TOKEN_A)
    binding = store.bind_for_test(
        sid, installed.id, user_id="77", chat_id="77", sync_from_event_id=0
    )
    secrets.fail_delete_ids.add(installed.credential_id)
    adapter.responses[TOKEN_B] = TelegramBotIdentity(bot_id="100", username="first")
    await runtime.replace_token(sid, installed.id, TOKEN_B)

    # A failed *old* credential cleanup is an obligation, not a replacement
    # for the live Bot/binding's actual delivery state.
    assert store.bot(installed.id).status is BotStatus.ACTIVE
    assert store.binding(binding.id).status is BindingStatus.ACTIVE
    assert runtime.status_for_session(sid)["warnings"] == [
        {"error_code": "credential_cleanup_pending"}
    ]
    secrets.fail_delete_ids.clear()
    assert await runtime.retry_credential_cleanup() == 1
    with store.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM credential_cleanup_queue").fetchone()[0] == 0

    current = store.bot(installed.id)
    secrets.fail_delete_ids.add(current.credential_id)
    disconnected = runtime.disconnect(sid)
    detached = store.bot(installed.id)
    assert detached.status is BotStatus.DISCONNECTED
    assert detached.credential_id is None
    assert disconnected["view_state"] == "none"
    assert disconnected["warnings"] == [{"error_code": "credential_cleanup_pending"}]
    with store.connect() as conn:
        assert conn.execute("SELECT credential_id FROM credential_cleanup_queue").fetchone()[0] == current.credential_id


@pytest.mark.anyio
async def test_archived_session_or_workspace_rejects_channel_writes_before_ticket_or_network(tmp_path):
    runtime, console, _store, secrets, adapter, sid, draft = _runtime_stack(tmp_path)
    adapter.responses[TOKEN_A] = TelegramBotIdentity(bot_id="100", username="safe")
    assert console.set_archived(sid, True) is not None
    ticket = runtime.issue_action_ticket(sid, "telegram:submit-token", draft.id)

    with TestClient(_app(runtime)) as client:
        ticket_request = client.post(
            f"/console/channels/session/{sid}/request-token",
            json={"action": "telegram:submit-token", "subject_id": draft.id},
        )
        direct = client.post("/console/channels/telegram/token", json={
            "sid": sid, "draft_id": draft.id, "token": TOKEN_A, "request_token": ticket,
        })
        pause = client.post(
            f"/console/channels/session/{sid}/pause",
            json={"subject_id": draft.id, "request_token": runtime.issue_action_ticket(
                sid, "telegram:pause", draft.id
            )},
        )

    assert all(response.status_code == 409 for response in (ticket_request, direct, pause))
    assert secrets.put_calls == []
    assert adapter.calls == []
    # The caller's ticket was never consumed by the read-only rejection.
    assert runtime.consume_action_ticket(ticket, sid, "telegram:submit-token", draft.id)


@pytest.mark.anyio
async def test_resume_never_overrides_fault_pause_and_reverify_preserves_manual_pause(tmp_path):
    runtime, _console, store, _secrets, adapter, sid, draft = _runtime_stack(tmp_path)
    adapter.responses[TOKEN_A] = TelegramBotIdentity(bot_id="100", username="safe")
    installed = await runtime.submit_token(sid, draft.id, TOKEN_A)
    binding = store.bind_for_test(
        sid, installed.id, user_id="77", chat_id="77", sync_from_event_id=0
    )

    adapter.responses[TOKEN_A] = TelegramPollConflict()
    with pytest.raises(ChannelRuntimeError):
        await runtime.reverify(sid)
    with pytest.raises(ChannelRuntimeError) as blocked:
        runtime.resume(sid)
    assert blocked.value.reason_code == "channel_resume_not_allowed"
    assert store.binding(binding.id).pause_reason == "poll_conflict"

    # A deliberate manual pause remains deliberate through a transient fault
    # and its later successful verification; it is never auto-resumed.
    runtime, _console, store, _secrets, adapter, sid, draft = _runtime_stack(tmp_path / "manual")
    adapter.responses[TOKEN_A] = TelegramBotIdentity(bot_id="100", username="safe")
    installed = await runtime.submit_token(sid, draft.id, TOKEN_A)
    binding = store.bind_for_test(
        sid, installed.id, user_id="77", chat_id="77", sync_from_event_id=0
    )
    runtime.pause(sid)
    adapter.responses[TOKEN_A] = TelegramCredentialInvalid()
    with pytest.raises(ChannelRuntimeError):
        await runtime.reverify(sid)
    adapter.responses[TOKEN_A] = TelegramBotIdentity(bot_id="100", username="safe")
    recovered = await runtime.reverify(sid)
    assert recovered["view_state"] == "paused"
    assert store.bot(installed.id).status is BotStatus.PAUSED
    assert store.binding(binding.id).status is BindingStatus.PAUSED
    assert store.binding(binding.id).pause_reason == "manual"


@pytest.mark.anyio
async def test_ticket_subjects_are_server_resolved_and_delivery_tickets_require_sid_ownership(tmp_path):
    runtime, console, store, _secrets, adapter, sid_a, draft_a = _runtime_stack(tmp_path)
    sid_b = console.create_session(title="other") ["id"]
    draft_b = runtime.create_draft(sid_b, "Other Bot")
    adapter.responses[TOKEN_A] = TelegramBotIdentity(bot_id="100", username="first")
    adapter.responses[TOKEN_B] = TelegramBotIdentity(bot_id="200", username="second")
    bot_a = await runtime.submit_token(sid_a, draft_a.id, TOKEN_A)
    bot_b = await runtime.submit_token(sid_b, draft_b.id, TOKEN_B)
    binding_a = store.bind_for_test(sid_a, bot_a.id, user_id="77", chat_id="77", sync_from_event_id=0)
    binding_b = store.bind_for_test(sid_b, bot_b.id, user_id="88", chat_id="88", sync_from_event_id=0)
    foreign_item = store.project_event_for_test(
        binding_b.id, source_event_id=1,
        parts=[OutboundPart(kind="text", payload_part=0, text="safe")],
    )[0]

    with TestClient(_app(runtime)) as client:
        foreign_pause = client.post(
            f"/console/channels/session/{sid_a}/request-token",
            json={"action": "telegram:pause", "subject_id": bot_b.id},
        )
        foreign_delivery = client.post(
            f"/console/channels/session/{sid_a}/request-token",
            json={"action": "telegram:resolve:drop", "subject_id": foreign_item.id},
        )
        own_delivery_ticket = client.post(
            f"/console/channels/session/{sid_a}/request-token",
            json={"action": "telegram:resolve:drop", "subject_id": store.project_event_for_test(
                binding_a.id, source_event_id=1,
                parts=[OutboundPart(kind="text", payload_part=0, text="safe")],
            )[0].id},
        )

    assert foreign_pause.status_code == foreign_delivery.status_code == 409
    assert own_delivery_ticket.status_code == 200
