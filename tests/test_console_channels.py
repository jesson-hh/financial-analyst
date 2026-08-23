from __future__ import annotations

import asyncio
import json
import re
import threading

from fastapi import FastAPI
from fastapi.testclient import TestClient

from guanlan_v2.channels.runtime import ActionTicketStore
from guanlan_v2.console.api import build_console_router
from guanlan_v2.console.store import ConsoleStore


TOKEN = "123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZ_abcdefghi"
TOKEN_2 = "234567890:ZYXWVUTSRQPONMLKJIHGFEDCBA_abcdef"


class _IdleAgent:
    def __init__(self) -> None:
        self.messages = []
        self.calls = []

    async def run_turn(self, text, confirm_callback=None, allowed_tools=None):
        self.calls.append(text)
        yield type("Event", (), {"kind": "done", "payload": None})()


class _BusyAgent:
    def __init__(self) -> None:
        self.messages = []
        self.calls = []
        self.active = threading.Event()

    async def run_turn(self, text, confirm_callback=None, allowed_tools=None):
        self.calls.append(text)
        self.active.set()
        yield type("Event", (), {"kind": "delta", "payload": "working"})()
        await asyncio.Event().wait()


class _DraftRuntime:
    _token = re.compile(r"[0-9]{5,16}:[A-Za-z0-9_-]{20,}")

    def __init__(self, sid: str, draft_id: str) -> None:
        self.sid = sid
        self.draft_id = draft_id
        self.tickets = ActionTicketStore(ttl_seconds=120)
        self.submissions = []

    def looks_like_telegram_token(self, text: str) -> bool:
        return bool(self._token.search(text))

    def extract_telegram_tokens(self, text: str):
        return tuple(match.group(0) for match in self._token.finditer(text))

    def current_draft(self, sid: str):
        return type("Draft", (), {"id": self.draft_id})() if sid == self.sid else None

    def issue_action_ticket(self, sid: str, action: str, subject_id: str) -> str:
        return self.tickets.issue(sid, action, subject_id)

    def consume_action_ticket(self, ticket: str, sid: str, action: str, subject_id: str) -> bool:
        return self.tickets.consume(ticket, sid, action, subject_id)

    async def submit_token(self, sid: str, draft_id: str, token: str):
        assert sid == self.sid and draft_id == self.draft_id
        self.submissions.append(token)


class _NoDraftRuntime(_DraftRuntime):
    def current_draft(self, sid: str):
        return None


class _BrokenDetectorRuntime(_DraftRuntime):
    def extract_telegram_tokens(self, text: str):
        raise RuntimeError(f"unsafe detector input: {text}")


class _IllegalDetectorRuntime(_DraftRuntime):
    def extract_telegram_tokens(self, text: str):
        return "not-an-approved-token-sequence"


class _BlockingSubmitRuntime(_DraftRuntime):
    """Hold the remote verification window open for reservation race tests."""

    def __init__(self, sid: str, draft_id: str) -> None:
        super().__init__(sid, draft_id)
        self.started = threading.Event()
        self.release = threading.Event()

    async def submit_token(self, sid: str, draft_id: str, token: str, *, before_commit=None):
        assert sid == self.sid and draft_id == self.draft_id
        self.started.set()
        assert await asyncio.to_thread(self.release.wait, 3)
        if before_commit is not None:
            await before_commit()
        self.submissions.append(token)


class _ArchiveAfterCommitRuntime(_DraftRuntime):
    """Simulate an external archive immediately after a durable Token CAS."""

    def __init__(self, sid: str, draft_id: str, store: ConsoleStore) -> None:
        super().__init__(sid, draft_id)
        self.store = store

    async def submit_token(self, sid: str, draft_id: str, token: str, *, before_commit=None):
        assert sid == self.sid and draft_id == self.draft_id
        if before_commit is not None:
            await before_commit()
        self.submissions.append(token)
        assert self.store.set_archived(sid, True) is not None


def test_send_and_steer_intercept_token_before_any_console_event_or_cancel(tmp_path):
    store = ConsoleStore(root=tmp_path)
    agent = _BusyAgent()
    app = FastAPI()
    runtime_holder = {}
    app.include_router(build_console_router(
        store=store,
        agent_factory=lambda _sid: agent,
        channel_runtime=lambda: runtime_holder["runtime"],
    ))

    with TestClient(app) as client:
        sid = client.post("/console/sessions", json={}).json()["meta"]["id"]
        runtime = _DraftRuntime(sid, "bot_draft")
        runtime_holder["runtime"] = runtime

        rejected = client.post("/console/send", json={"sid": sid, "text": TOKEN})
        assert rejected.json()["reason_code"] == "telegram_token_secure_submit_required"
        assert store.read_events(sid) == []

        ticket = runtime.issue_action_ticket(sid, "telegram:submit-token", "bot_draft")
        accepted = client.post("/console/send", json={
            "sid": sid, "text": "请用这个 token " + TOKEN,
            "draft_id": "bot_draft", "request_token": ticket,
        })
        assert accepted.status_code == 200 and accepted.json()["secret_submitted"] is True

        events = store.read_events(sid)
        assert events[0]["type"] == "user_msg"
        assert events[0]["text"] == "[Telegram Bot Token 已安全提交]"
        assert events[0]["channel_projection"] == "never"
        assert events[0]["secret_placeholder"] is True
        assert TOKEN not in str(events)
        assert runtime.submissions == [TOKEN]
        assert agent.active.wait(timeout=1)
        assert len(agent.calls) == 1
        assert agent.calls[0].endswith("[Telegram Bot Token 已安全提交]")
        assert TOKEN not in agent.calls[0]

        # Token through /steer must not append a direction or cancel the
        # genuinely active foreground turn.
        before = list(store.read_events(sid))
        steer_ticket = runtime.issue_action_ticket(sid, "telegram:submit-token", "bot_draft")
        steered = client.post("/console/steer", json={
            "sid": sid, "text": TOKEN, "draft_id": "bot_draft",
            "request_token": steer_ticket,
        })
        assert steered.status_code == 200 and steered.json()["secret_submitted"] is True
        assert store.read_events(sid) == before


def test_secret_interceptor_rejects_multiple_wrong_scope_no_draft_and_detector_fault(tmp_path):
    store = ConsoleStore(root=tmp_path)
    app = FastAPI()
    holder = {}
    app.include_router(build_console_router(
        store=store,
        agent_factory=lambda _sid: _IdleAgent(),
        channel_runtime=lambda: holder["runtime"],
    ))

    with TestClient(app) as client:
        sid = client.post("/console/sessions", json={}).json()["meta"]["id"]
        runtime = _DraftRuntime(sid, "bot_draft")
        holder["runtime"] = runtime
        multiple = client.post("/console/send", json={
            "sid": sid, "text": f"{TOKEN} and {TOKEN_2}",
        })
        wrong_ticket = runtime.issue_action_ticket("cs_other", "telegram:submit-token", "bot_draft")
        wrong_scope = client.post("/console/send", json={
            "sid": sid, "text": TOKEN, "draft_id": "bot_draft", "request_token": wrong_ticket,
        })

        holder["runtime"] = _NoDraftRuntime(sid, "bot_draft")
        no_draft = client.post("/console/send", json={"sid": sid, "text": TOKEN})

        holder["runtime"] = _BrokenDetectorRuntime(sid, "bot_draft")
        broken = client.post("/console/send", json={"sid": sid, "text": TOKEN})

    assert multiple.json()["reason_code"] == "telegram_token_secure_submit_required"
    assert wrong_scope.json()["reason_code"] == "telegram_token_secure_submit_required"
    assert no_draft.json()["reason_code"] == "telegram_token_needs_draft"
    assert broken.json()["reason_code"] == "channel_secret_inspection_unavailable"
    assert TOKEN not in broken.content.decode("utf-8")
    assert store.read_events(sid) == []
    assert runtime.submissions == []


def test_token_submission_is_deferred_until_send_session_authorization(tmp_path):
    store = ConsoleStore(root=tmp_path)
    app = FastAPI()
    holder = {}
    app.include_router(build_console_router(
        store=store,
        agent_factory=lambda _sid: _BusyAgent(),
        channel_runtime=lambda: holder["runtime"],
    ))

    with TestClient(app) as client:
        sid = client.post("/console/sessions", json={}).json()["meta"]["id"]
        runtime = _DraftRuntime(sid, "bot_draft")
        holder["runtime"] = runtime
        archived_ticket = runtime.issue_action_ticket(sid, "telegram:submit-token", "bot_draft")
        assert store.set_archived(sid, True) is not None
        archived = client.post("/console/send", json={
            "sid": sid, "text": TOKEN, "draft_id": "bot_draft", "request_token": archived_ticket,
        })
        assert archived.status_code == 409
        assert runtime.submissions == []
        assert store.read_events(sid) == []

        assert store.set_archived(sid, False) is not None
        ordinary = client.post("/console/send", json={"sid": sid, "text": "start busy turn"})
        assert ordinary.status_code == 200
        # The injected busy agent is created by the factory on first turn.
        # Poll the runtime surface rather than relying on a timing assumption.
        import time
        for _ in range(50):
            if any(event.get("type") == "agent_delta" for event in store.read_events(sid)):
                break
            time.sleep(0.01)
        busy_ticket = runtime.issue_action_ticket(sid, "telegram:submit-token", "bot_draft")
        busy = client.post("/console/send", json={
            "sid": sid, "text": TOKEN, "draft_id": "bot_draft", "request_token": busy_ticket,
        })

    assert busy.status_code == 200
    assert "正有任务" in busy.json()["reason"]
    assert runtime.submissions == []
    assert TOKEN not in str(store.read_events(sid))


def test_injected_runtime_resolution_or_detector_contract_failure_is_fail_closed(tmp_path):
    store = ConsoleStore(root=tmp_path)
    app = FastAPI()
    holder = {}
    app.include_router(build_console_router(
        store=store,
        agent_factory=lambda _sid: _IdleAgent(),
        channel_runtime=lambda: holder["runtime"],
    ))

    with TestClient(app) as client:
        sid = client.post("/console/sessions", json={}).json()["meta"]["id"]
        holder["runtime"] = _IllegalDetectorRuntime(sid, "bot_draft")
        illegal = client.post("/console/send", json={"sid": sid, "text": TOKEN})

        # A resolver exception is not feature-gate absence.  Once injected, a
        # broken resolver may not turn a raw secret into an ordinary user_msg.
        holder.clear()
        broken_resolver = client.post("/console/send", json={"sid": sid, "text": TOKEN})

    assert illegal.status_code == broken_resolver.status_code == 400
    assert illegal.json() == broken_resolver.json() == {
        "ok": False, "reason_code": "channel_secret_inspection_unavailable"
    }
    assert TOKEN not in illegal.content.decode("utf-8")
    assert TOKEN not in broken_resolver.content.decode("utf-8")
    assert store.read_events(sid) == []


def test_send_and_steer_manual_root_json_parsing_never_422_echoes_token(tmp_path):
    store = ConsoleStore(root=tmp_path)
    app = FastAPI()
    runtime_holder = {}
    app.include_router(build_console_router(
        store=store,
        agent_factory=lambda _sid: _IdleAgent(),
        channel_runtime=lambda: runtime_holder["runtime"],
    ))

    with TestClient(app) as client:
        sid = client.post("/console/sessions", json={}).json()["meta"]["id"]
        runtime_holder["runtime"] = _DraftRuntime(sid, "bot_draft")
        send = client.post(
            "/console/send", content=json.dumps(TOKEN), headers={"Content-Type": "application/json"}
        )
        steer = client.post(
            "/console/steer", content=json.dumps(TOKEN), headers={"Content-Type": "application/json"}
        )

    assert send.status_code == steer.status_code == 400
    assert send.json() == steer.json() == {
        "ok": False, "reason_code": "console_request_invalid"
    }
    assert TOKEN not in send.content.decode("utf-8")
    assert TOKEN not in steer.content.decode("utf-8")
    assert store.read_events(sid) == []


def test_send_holds_secret_reservation_through_durable_placeholder_and_launch(tmp_path):
    store = ConsoleStore(root=tmp_path)
    app = FastAPI()
    holder = {}
    app.include_router(build_console_router(
        store=store,
        agent_factory=lambda _sid: _IdleAgent(),
        channel_runtime=lambda: holder["runtime"],
    ))

    with TestClient(app) as client:
        sid = client.post("/console/sessions", json={}).json()["meta"]["id"]
        runtime = _BlockingSubmitRuntime(sid, "bot_draft")
        holder["runtime"] = runtime
        ticket = runtime.issue_action_ticket(sid, "telegram:submit-token", "bot_draft")
        result = {}

        def submit() -> None:
            # A second ASGI client gives the blocked request its own portal;
            # production requests are concurrent rather than TestClient's
            # same-portal serialization.
            with TestClient(app) as submit_client:
                result["response"] = submit_client.post("/console/send", json={
                    "sid": sid,
                    "text": TOKEN,
                    "draft_id": "bot_draft",
                    "request_token": ticket,
                })

        worker = threading.Thread(target=submit)
        worker.start()
        assert runtime.started.wait(timeout=2)

        try:
            # Both an archive (which would make the post-getMe commit unsafe)
            # and a second ordinary turn are blocked for the full secure
            # handoff.
            archived = client.post(f"/console/sessions/{sid}/archive")
            duplicate = client.post("/console/send", json={"sid": sid, "text": "ordinary"})
            assert archived.status_code == 409
            # Preserve the established Console busy envelope while proving the
            # second request did not append or launch a turn.
            assert duplicate.status_code == 200 and duplicate.json()["ok"] is False
            assert store.read_events(sid) == []
        finally:
            runtime.release.set()
            worker.join(timeout=3)

    accepted = result["response"]
    assert accepted.status_code == 200
    assert accepted.json()["secret_submitted"] is True
    events = store.read_events(sid)
    assert events[0]["text"] == "[Telegram Bot Token 已安全提交]"
    assert events[0]["secret_placeholder"] is True
    assert TOKEN not in str(events)


def test_steer_reports_post_commit_archive_as_partial_success_without_control_mutation(tmp_path):
    store = ConsoleStore(root=tmp_path)
    agent = _BusyAgent()
    app = FastAPI()
    holder = {}
    app.include_router(build_console_router(
        store=store,
        agent_factory=lambda _sid: agent,
        channel_runtime=lambda: holder["runtime"],
    ))

    with TestClient(app) as client:
        sid = client.post("/console/sessions", json={}).json()["meta"]["id"]
        runtime = _ArchiveAfterCommitRuntime(sid, "bot_draft", store)
        holder["runtime"] = runtime
        assert client.post("/console/send", json={"sid": sid, "text": "start"}).json()["ok"]
        assert agent.active.wait(timeout=2)
        ticket = runtime.issue_action_ticket(sid, "telegram:submit-token", "bot_draft")

        partial = client.post("/console/steer", json={
            "sid": sid,
            "text": TOKEN,
            "draft_id": "bot_draft",
            "request_token": ticket,
        })

    assert partial.status_code == 200
    assert partial.json() == {
        "ok": True,
        "sid": sid,
        "secret_submitted": True,
        "secret_placeholder_recorded": False,
    }
    assert runtime.submissions == [TOKEN]
    assert store.get_meta(sid)["archived"] is True
    assert not any(event.get("steer") for event in store.read_events(sid))
    assert TOKEN not in str(store.read_events(sid))
