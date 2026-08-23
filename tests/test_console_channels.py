from __future__ import annotations

import asyncio
import base64
import json
import re
import threading
import time

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from guanlan_v2.channels.runtime import ActionTicketStore
from guanlan_v2.console import workspace_tools as wt
from guanlan_v2.console.api import build_console_router
from guanlan_v2.console.media import MediaStore
from guanlan_v2.console.store import ConsoleStore


TOKEN = "123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZ_abcdefghi"
TOKEN_2 = "234567890:ZYXWVUTSRQPONMLKJIHGFEDCBA_abcdef"
_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


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


class _HookRuntime:
    """Narrow duck-typed Runtime surface used by the Console hook tests."""

    def __init__(self, store: ConsoleStore) -> None:
        self.store = store
        self.bind_calls = 0
        self.hooks = {}
        self.terminal_calls = []
        self.media_confirmation_calls = []

    def bind_console_hooks(self, **hooks):
        self.bind_calls += 1
        self.hooks = dict(hooks)

    def extract_telegram_tokens(self, _text):
        return ()

    def automation_turn_terminal(
        self, automation_run_id, turn_id, ok, final_event_id, error_class,
    ):
        snapshots = {
            meta["id"]: self.store.read_events(meta["id"])
            for meta in self.store.list_sessions()
        }
        self.terminal_calls.append({
            "automation_run_id": automation_run_id,
            "turn_id": turn_id,
            "ok": ok,
            "final_event_id": final_event_id,
            "error_class": error_class,
            "snapshots": snapshots,
        })

    def status_for_session(self, _sid):
        return {"recipient": {"username": "private_user", "user_id": "10001"}}

    def media_publication_confirmation_preview(self, sid, media_id, caption=""):
        MediaStore(self.store).load_for_session(media_id, sid)
        return {
            "binding_id": "bind_generation",
            "binding_paired_at": "2026-08-23T09:30:00+08:00",
            "recipient": {"username": "private_user", "user_id": "10001"},
        }

    def issue_media_publication_confirmation(
        self, sid, media_id, caption, expected_binding_id, expected_paired_at,
    ):
        MediaStore(self.store).load_for_session(media_id, sid)
        assert expected_binding_id == "bind_generation"
        assert expected_paired_at == "2026-08-23T09:30:00+08:00"
        self.media_confirmation_calls.append((sid, media_id, caption))
        return "automation-media-proof"


class _TextAgent:
    def __init__(self) -> None:
        self.messages = []
        self.calls = []

    async def run_turn(self, text, confirm_callback=None, allowed_tools=None):
        self.calls.append(text)
        yield type("Event", (), {"kind": "text", "payload": "定时研究完成"})()
        yield type("Event", (), {"kind": "done", "payload": None})()


class _AutomationPolicyAgent:
    def __init__(self, created_artifact, old_media_id) -> None:
        self.messages = []
        self.created_artifact = created_artifact
        self.old_media_id = old_media_id
        self.decisions = []

    async def run_turn(self, text, confirm_callback=None, allowed_tools=None):
        workspace_write = await confirm_callback(
            "ww_workspace_apply_patch", {"patch": "update a local note"},
        )
        old_publish = await confirm_callback(
            "ww_telegram_publish", {"media_id": self.old_media_id, "caption": "old"},
        )
        yield type("Event", (), {
            "kind": "tool_result",
            "payload": {
                "name": "ww_workspace_publish_image",
                "content": "created image",
                "is_error": False,
                "side_effect": {"artifact": self.created_artifact},
            },
        })()
        media_id = self.created_artifact["payload"]["media_id"]
        new_publish = await confirm_callback(
            "ww_telegram_publish", {"media_id": media_id, "caption": "new"},
        )
        self.decisions.append({
            "workspace_write": workspace_write,
            "old_publish": old_publish,
            "new_publish": new_publish,
        })
        yield type("Event", (), {"kind": "text", "payload": "权限检查完成"})()
        yield type("Event", (), {"kind": "done", "payload": None})()


class _UnsafeAutomationAgent:
    def __init__(self) -> None:
        self.messages = []
        self.decisions = []

    @staticmethod
    def _spec(**changes):
        value = {
            "name": "Morning Brief",
            "schedule_kind": "daily",
            "local_time": "08:30",
            "timezone": "Asia/Hong_Kong",
            "prompt_text": "summarize the confirmed market data",
            "skill_refs": ["morning_brief_v1"],
            "media_policy": "none",
        }
        value.update(changes)
        return value

    async def run_turn(self, text, confirm_callback=None, allowed_tools=None):
        unsafe = self._spec(
            name="C:\\Users\\Alice\\morning",
            prompt_text=(
                "read C:\\Users\\Alice\\secret.csv and send to "
                "Telegram chat id=123456789"
            ),
        )
        yield type("Event", (), {
            "kind": "tool_call",
            "payload": {"name": "ww_automation_upsert", "args": unsafe},
        })()
        self.decisions.append(await confirm_callback("ww_automation_upsert", unsafe))
        self.decisions.append(await confirm_callback(
            "ww_automation_upsert",
            self._spec(name="Morning  Brief"),
        ))
        self.decisions.append(await confirm_callback(
            "ww_automation_upsert",
            self._spec(prompt_text="send Telegram user id=987654321"),
        ))
        self.decisions.append(await confirm_callback(
            "ww_automation_upsert",
            self._spec(prompt_text="hidden\x01control"),
        ))
        yield type("Event", (), {"kind": "done", "payload": None})()


def _automation_test_app(store, runtime, agent_factory):
    app = FastAPI()
    app.include_router(build_console_router(
        store=store,
        agent_factory=agent_factory,
        channel_runtime=runtime,
    ))

    @app.post("/_test/automation-turn")
    async def trigger(request: Request):
        body = await request.json()
        return await runtime.hooks["submit_system_turn"](
            body.get("sid"),
            body.get("automation_run_id"),
            body.get("turn_text"),
            body.get("media_policy", "none"),
        )

    return app


def _wait_for(predicate, timeout=3.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return bool(predicate())


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


def test_console_binds_same_loop_automation_hooks_and_terminalizes_after_final_and_done(
    tmp_path,
):
    store = ConsoleStore(root=tmp_path)
    sid = store.create_session("晨报")["id"]
    runtime = _HookRuntime(store)
    agent = _TextAgent()
    app = _automation_test_app(store, runtime, lambda _sid: agent)

    assert runtime.bind_calls == 1
    assert set(runtime.hooks) == {
        "emit", "is_busy", "submit_system_turn", "on_turn_terminal",
    }

    with TestClient(app) as client:
        accepted = client.post("/_test/automation-turn", json={
            "sid": sid,
            "automation_run_id": "ar_morning_001",
            "turn_text": "生成今天的已确认晨报",
            "media_policy": "none",
        }).json()
        assert accepted["ok"] is True
        assert re.fullmatch(
            r"at_[0-9a-f]{6}(?:x[0-9a-f]{6}){3}x[0-9a-f]{4}",
            accepted["turn_id"],
        )
        assert _wait_for(lambda: len(runtime.terminal_calls) == 1)

    events = store.read_events(sid)
    trigger = next(event for event in events if event["type"] == "automation_trigger")
    final = next(event for event in events if event["type"] == "agent_final")
    done = next(
        event for event in events
        if event["type"] == "task_update" and event.get("status") == "done"
    )
    assert trigger["automation_run_id"] == "ar_morning_001"
    assert trigger["turn_id"] == accepted["turn_id"]
    assert trigger["source"] == "automation"
    assert trigger["media_policy"] == "none"
    assert trigger["channel_projection"] == "never"
    assert not any(event["type"] == "user_msg" for event in events)
    assert agent.calls and agent.calls[0].endswith("生成今天的已确认晨报")
    assert final["source"] == "automation"

    terminal = runtime.terminal_calls[0]
    assert terminal["automation_run_id"] == "ar_morning_001"
    assert terminal["turn_id"] == accepted["turn_id"]
    assert terminal["ok"] is True
    assert terminal["final_event_id"] == final["id"]
    assert terminal["error_class"] is None
    observed = terminal["snapshots"][sid]
    assert any(event.get("id") == final["id"] for event in observed)
    assert any(event.get("id") == done["id"] for event in observed)
    assert len(runtime.terminal_calls) == 1


def test_system_turn_admission_refuses_busy_archived_session_and_archived_workspace(
    tmp_path,
):
    store = ConsoleStore(root=tmp_path)
    busy_sid = store.create_session("busy")["id"]
    archived_sid = store.create_session("archived")["id"]
    assert store.set_archived(archived_sid, True) is not None
    workspace = store.workspace_store.create({
        "name": "archived workspace",
        "kind": "stock",
        "code": "600667",
    })
    workspace_sid = store.create_session(
        "workspace archived", workspace_id=workspace["id"],
    )["id"]
    store.workspace_store.archive(workspace["id"])
    runtime = _HookRuntime(store)
    busy_agent = _BusyAgent()
    app = _automation_test_app(store, runtime, lambda _sid: busy_agent)

    with TestClient(app) as client:
        first = client.post("/_test/automation-turn", json={
            "sid": busy_sid,
            "automation_run_id": "ar_busy_1",
            "turn_text": "hold this turn",
        }).json()
        assert first["ok"] is True and busy_agent.active.wait(timeout=2)
        duplicate = client.post("/_test/automation-turn", json={
            "sid": busy_sid,
            "automation_run_id": "ar_busy_2",
            "turn_text": "must queue instead",
        }).json()
        archived = client.post("/_test/automation-turn", json={
            "sid": archived_sid,
            "automation_run_id": "ar_archived",
            "turn_text": "must not run",
        }).json()
        workspace_archived = client.post("/_test/automation-turn", json={
            "sid": workspace_sid,
            "automation_run_id": "ar_ws_archived",
            "turn_text": "must not run",
        }).json()

    assert duplicate == {"ok": False, "reason_code": "busy"}
    assert archived == workspace_archived == {
        "ok": False, "reason_code": "session_read_only",
    }
    assert store.read_events(archived_sid) == []
    assert store.read_events(workspace_sid) == []


def test_automation_confirmation_precedes_workspace_auto_and_allows_only_same_turn_media(
    tmp_path,
):
    store = ConsoleStore(root=tmp_path)
    workspace = store.workspace_store.create({
        "name": "automation media",
        "kind": "stock",
        "code": "000636",
        "agent_mode": "autonomous",
    })
    sid = store.create_session("automation media", workspace_id=workspace["id"])["id"]
    workspace_root = tmp_path / "workspace_files" / workspace["id"]
    (workspace_root / "new.png").write_bytes(_PNG)
    (workspace_root / "old.png").write_bytes(_PNG)
    workspace_token = wt.CTX_WORKSPACE.set(wt.bind_workspace_context(store, sid))
    try:
        created_artifact = wt.workspace_publish_image_impl("new.png")["artifact"]
        old_media_id = wt.workspace_publish_image_impl("old.png")["media"]["media_id"]
    finally:
        wt.CTX_WORKSPACE.reset(workspace_token)

    runtime = _HookRuntime(store)
    agent = _AutomationPolicyAgent(created_artifact, old_media_id)
    app = _automation_test_app(store, runtime, lambda _sid: agent)

    with TestClient(app) as client:
        explicit = client.post("/_test/automation-turn", json={
            "sid": sid,
            "automation_run_id": "ar_explicit_media",
            "turn_text": "make and explicitly publish the new image",
            "media_policy": "explicit",
        }).json()
        assert explicit["ok"] is True
        assert _wait_for(lambda: len(agent.decisions) == 1)

        none = client.post("/_test/automation-turn", json={
            "sid": sid,
            "automation_run_id": "ar_no_media",
            "turn_text": "make an image without publication authority",
            "media_policy": "none",
        }).json()
        assert none["ok"] is True
        assert _wait_for(lambda: len(agent.decisions) == 2)

    assert agent.decisions == [
        {"workspace_write": False, "old_publish": False, "new_publish": True},
        {"workspace_write": False, "old_publish": False, "new_publish": False},
    ]
    assert runtime.media_confirmation_calls == [
        (sid, created_artifact["payload"]["media_id"], "new"),
    ]
    decisions = [
        event.get("choice") for event in store.read_events(sid)
        if event.get("type") == "confirm_resolved"
    ]
    assert "workspace_auto" not in decisions
    assert "automation_explicit_media" in decisions
    assert decisions.count("automation_policy_denied") >= 3


def test_automation_confirmation_rejects_sensitive_or_display_ambiguous_spec_before_history(
    tmp_path,
):
    store = ConsoleStore(root=tmp_path)
    sid = store.create_session("automation safety")["id"]
    runtime = _HookRuntime(store)
    agent = _UnsafeAutomationAgent()
    app = _automation_test_app(store, runtime, lambda _sid: agent)

    with TestClient(app) as client:
        accepted = client.post("/console/send", json={
            "sid": sid, "text": "create the schedule",
        }).json()
        assert accepted["ok"] is True
        assert _wait_for(lambda: len(agent.decisions) == 4)

    assert agent.decisions == [False, False, False, False]
    events = store.read_events(sid)
    assert not any(event.get("type") == "confirm_request" for event in events)
    tool_call = next(event for event in events if event.get("type") == "tool_call")
    assert tool_call["args"] == {}
    serialized = json.dumps(events, ensure_ascii=False)
    assert "C:\\Users\\Alice" not in serialized
    assert "123456789" not in serialized
    assert "987654321" not in serialized
    assert "\x01" not in serialized
    assert "Morning  Brief" not in serialized


def test_structural_ids_survive_pairing_redaction_while_standalone_codes_do_not(
    monkeypatch,
):
    import guanlan_v2.console.api as console_api

    automation_id = "auto_" + "a" * 6 + "22222222" + "b" * 18
    run_id = "arun_" + "c" * 6 + "23456789" + "d" * 18
    code = "23456789"
    cleaned = console_api._redact_snapshot_value({
        "automation_id": automation_id,
        "automation_run_id": run_id,
        "text": f"standalone {code}",
    }, set())
    assert cleaned["automation_id"] == automation_id
    assert cleaned["automation_run_id"] == run_id
    assert code not in cleaned["text"]
    assert console_api._console_tool_argument_guard(
        "ww_automation_pause", {"automation_id": automation_id},
    ) is None
    assert console_api._public_tool_args(
        "ww_automation_pause", {"automation_id": automation_id},
    ) == {"automation_id": automation_id}

    stream = console_api._PairingCodeStreamRedactor(set())
    rendered = (
        stream.feed(f"id={automation_id}; code 2345")
        + stream.feed("6789")
        + stream.finish()
    )
    assert automation_id in rendered
    assert code not in rendered

    fake_uuid = type("UUID", (), {"hex": "2" * 32})()
    monkeypatch.setattr(console_api.uuid, "uuid4", lambda: fake_uuid)
    generated = console_api._new_console_id()
    assert len(generated) == 32
    assert generated.count("x") == 4
    assert console_api._PAIRING_CODE_RE.search(generated) is None
