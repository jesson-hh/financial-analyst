from __future__ import annotations

import asyncio
import json
import re
import threading
import time
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from guanlan_v2.channels.runtime import ActionTicketStore
from guanlan_v2.console.api import build_console_router
from guanlan_v2.console.store import ConsoleStore


CHANNEL_NAMES = {
    "ww_telegram_connect",
    "ww_telegram_status",
    "ww_telegram_pair_status",
    "ww_telegram_pair_approve",
    "ww_telegram_pair_dismiss",
    "ww_telegram_pause",
    "ww_telegram_resume",
    "ww_telegram_disconnect",
    "ww_telegram_publish",
    "ww_automation_skills",
    "ww_automation_list",
    "ww_automation_upsert",
    "ww_automation_pause",
    "ww_automation_resume",
    "ww_automation_delete",
}

CONFIRMED_NAMES = {
    "ww_telegram_pair_approve",
    "ww_telegram_pair_dismiss",
    "ww_telegram_pause",
    "ww_telegram_resume",
    "ww_telegram_disconnect",
    "ww_telegram_publish",
    "ww_automation_upsert",
    "ww_automation_pause",
    "ww_automation_resume",
    "ww_automation_delete",
}

READ_ONLY_NAMES = {
    "ww_telegram_status",
    "ww_telegram_pair_status",
    "ww_automation_skills",
    "ww_automation_list",
}

FORBIDDEN_SCHEMA_NAMES = {
    "token",
    "credential_id",
    "session_id",
    "bot_id",
    "chat_id",
    "user_id",
    "recipient",
    "path",
}


def _schema_names(value):
    if isinstance(value, dict):
        for key, child in value.items():
            yield str(key).lower()
            yield from _schema_names(child)
    elif isinstance(value, list):
        for child in value:
            yield from _schema_names(child)
    elif isinstance(value, str):
        yield value.lower()


def _wait(store, sid, predicate, timeout=3.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        events = store.read_events(sid)
        if predicate(events):
            return events
        time.sleep(0.01)
    return store.read_events(sid)


def test_channel_tool_table_is_narrow_and_separate_from_core_table():
    from guanlan_v2.channels import agent_tools as at
    from guanlan_v2.console import tools as ct

    assert at.CHANNEL_TOOL_NAMES == CHANNEL_NAMES
    assert {row["name"] for row in at.CHANNEL_TOOL_TABLE} == CHANNEL_NAMES
    assert not (CHANNEL_NAMES & {row["name"] for row in ct.WW_TOOL_TABLE})
    assert {row["name"] for row in ct.ALL_CONSOLE_TOOL_TABLE} == (
        {row["name"] for row in ct.WW_TOOL_TABLE} | CHANNEL_NAMES
    )
    assert CHANNEL_NAMES <= ct.CONSOLE_ALLOWED

    by_name = {row["name"]: row for row in at.CHANNEL_TOOL_TABLE}
    assert {name for name, row in by_name.items() if row["confirm"]} == CONFIRMED_NAMES
    assert all(by_name[name]["confirm"] is False for name in READ_ONLY_NAMES)
    assert set(by_name["ww_telegram_connect"]["input_schema"]["properties"]) == {
        "display_name"
    }
    assert set(by_name["ww_telegram_pair_approve"]["input_schema"]["properties"]) == {
        "code"
    }
    assert set(by_name["ww_telegram_pair_dismiss"]["input_schema"]["properties"]) == {
        "pairing_request_id"
    }
    publish_schema = by_name["ww_telegram_publish"]["input_schema"]
    assert set(publish_schema["properties"]) == {"media_id", "caption"}
    assert publish_schema["required"] == ["media_id"]
    assert publish_schema["properties"]["media_id"]["pattern"] == (
        r"^mi_[0-9a-f]{32}$"
    )

    for row in at.CHANNEL_TOOL_TABLE:
        assert row["input_schema"].get("additionalProperties") is False
        schema_words = set(_schema_names(row["input_schema"]))
        assert not (schema_words & FORBIDDEN_SCHEMA_NAMES), row["name"]
        flat = json.dumps(row["input_schema"], ensure_ascii=False).lower()
        for forbidden in FORBIDDEN_SCHEMA_NAMES:
            assert f'"{forbidden}"' not in flat, row["name"]


def test_channel_writes_fail_honestly_without_session_or_runtime_context():
    from guanlan_v2.channels import agent_tools as at

    calls = {
        "ww_telegram_connect": {"display_name": "晨报助手"},
        "ww_telegram_pair_approve": {"code": "23456789"},
        "ww_telegram_pair_dismiss": {"pairing_request_id": "pair_one"},
        "ww_telegram_pause": {},
        "ww_telegram_resume": {},
        "ww_telegram_disconnect": {},
        "ww_telegram_publish": {"media_id": "mi_" + "1" * 32},
        "ww_automation_upsert": {
            "name": "每日晨报",
            "schedule_kind": "daily",
            "local_time": "08:30",
            "timezone": "Asia/Hong_Kong",
            "prompt_text": "生成晨报",
            "skill_refs": ["morning_brief_v1"],
            "media_policy": "none",
        },
        "ww_automation_pause": {"automation_id": "auto_one"},
        "ww_automation_resume": {"automation_id": "auto_one"},
        "ww_automation_delete": {"automation_id": "auto_one"},
    }
    by_name = {row["name"]: row for row in at.CHANNEL_TOOL_TABLE}
    for name, args in calls.items():
        result = by_name[name]["impl"](**args)
        assert result["ok"] is False
        assert result["reason_code"] == "channel_context_unavailable"


def test_channel_tool_context_uses_current_sid_and_owner_loop_for_async_pairing():
    from guanlan_v2.channels import agent_tools as at
    from guanlan_v2.console import tools as ct

    class Runtime:
        def __init__(self):
            self.calls = []

        def create_draft(self, sid, display_name):
            self.calls.append(("connect", sid, display_name))
            return object()

        def status_for_session(self, sid):
            self.calls.append(("status", sid))
            return {
                "available": True,
                "session_id": sid,
                "view_state": "awaiting_pairing",
                "draft": {"draft_id": "draft_one"},
                "bot": {"display_name": "晨报助手", "username": "morning_bot"},
                "recipient": None,
                "pairing_requests": [],
                "counts": {},
                "warnings": [],
                "recent_deliveries": [],
                "automations": [],
            }

        async def approve_pairing_code(self, sid, code):
            await asyncio.sleep(0)
            self.calls.append(("approve", sid, code))
            return object()

    async def exercise():
        runtime = Runtime()
        sid_token = ct.CTX_SID.set("cs_current")
        runtime_token = at.CTX_CHANNEL_RUNTIME.set(
            at.ChannelToolContext.capture(runtime)
        )
        try:
            connected = await asyncio.to_thread(
                at.telegram_connect_impl, display_name="晨报助手"
            )
            approved = await asyncio.to_thread(
                at.telegram_pair_approve_impl, code="23456789"
            )
        finally:
            at.CTX_CHANNEL_RUNTIME.reset(runtime_token)
            ct.CTX_SID.reset(sid_token)
        return runtime.calls, connected, approved

    calls, connected, approved = asyncio.run(exercise())
    assert connected["ok"] is True and approved["ok"] is True
    assert ("connect", "cs_current", "晨报助手") in calls
    assert ("approve", "cs_current", "23456789") in calls
    assert all("cs_other" not in call for call in calls)


def test_async_channel_tool_timeout_reports_unknown_not_failed(monkeypatch):
    from guanlan_v2.channels import agent_tools as at
    from guanlan_v2.console import tools as ct

    class Runtime:
        async def approve_pairing_code(self, sid, code):
            await asyncio.sleep(1)

    async def exercise():
        sid_token = ct.CTX_SID.set("cs_current")
        runtime_token = at.CTX_CHANNEL_RUNTIME.set(
            at.ChannelToolContext.capture(Runtime())
        )
        try:
            return await asyncio.to_thread(
                at.telegram_pair_approve_impl, code="23456789"
            )
        finally:
            at.CTX_CHANNEL_RUNTIME.reset(runtime_token)
            ct.CTX_SID.reset(sid_token)

    monkeypatch.setattr(at, "_ASYNC_TOOL_TIMEOUT_SECONDS", 0.01)
    result = asyncio.run(exercise())
    assert result["ok"] is False
    assert result["reason_code"] == "channel_operation_unknown"
    assert "先读取当前连接状态" in result["content"]
    assert "重试" in result["content"]


def test_not_yet_wired_publish_and_automation_are_honestly_unavailable():
    from guanlan_v2.channels import agent_tools as at
    from guanlan_v2.console import tools as ct

    class Runtime:
        def status_for_session(self, sid):
            return {"available": True, "session_id": sid, "view_state": "active"}

    sid_token = ct.CTX_SID.set("cs_current")
    runtime_token = at.CTX_CHANNEL_RUNTIME.set(at.ChannelToolContext.capture(Runtime()))
    try:
        publish = at.telegram_publish_impl(media_id="mi_" + "1" * 32, caption="图")
        skills = at.automation_skills_impl()
        listed = at.automation_list_impl()
        upserted = at.automation_upsert_impl(
            name="晨报",
            schedule_kind="daily",
            local_time="08:30",
            timezone="Asia/Hong_Kong",
            prompt_text="生成晨报",
            skill_refs=["morning_brief_v1"],
            media_policy="none",
        )
    finally:
        at.CTX_CHANNEL_RUNTIME.reset(runtime_token)
        ct.CTX_SID.reset(sid_token)

    for result in (publish, skills, listed, upserted):
        assert result == {
            "ok": False,
            "reason_code": "channel_capability_unavailable",
            "content": "该通道能力尚未接通，当前不能执行。",
        }


def test_publish_tool_accepts_only_exact_runtime_media_envelope_and_strips_extras():
    from guanlan_v2.channels import agent_tools as at
    from guanlan_v2.console import tools as ct

    media_id = "mi_" + "a" * 32

    class Runtime:
        def __init__(self):
            self.calls = []

        def publish_media(self, sid, supplied_media_id, caption, confirmation_ticket):
            self.calls.append((sid, supplied_media_id, caption, confirmation_ticket))
            return {
                "ok": True,
                "content": r"internal C:\\private\\chart.png",
                "artifact": {
                    "kind": "telegram_media_publish",
                    "page": None,
                    "channel": "telegram",
                    "payload": {"media_id": supplied_media_id, "caption": caption},
                    "ref": "pub_" + "1" * 32,
                },
                "internal_path": r"C:\\private\\chart.png",
            }

    runtime = Runtime()
    sid_token = ct.CTX_SID.set("cs_current")
    runtime_token = at.CTX_CHANNEL_RUNTIME.set(
        at.ChannelToolContext.capture(runtime)
    )
    permit_token = at.CTX_MEDIA_PUBLICATION_TICKETS.set({})
    try:
        assert at.stash_media_publication_ticket(
            media_id, "支撑压力位", "confirmation-proof"
        )
        result = at.telegram_publish_impl(media_id=media_id, caption="支撑压力位")
    finally:
        at.CTX_MEDIA_PUBLICATION_TICKETS.reset(permit_token)
        at.CTX_CHANNEL_RUNTIME.reset(runtime_token)
        ct.CTX_SID.reset(sid_token)

    assert runtime.calls == [
        ("cs_current", media_id, "支撑压力位", "confirmation-proof")
    ]
    assert set(result) == {"ok", "content", "artifact"}
    assert result["ok"] is True
    assert result["artifact"] == {
        "kind": "telegram_media_publish",
        "page": None,
        "channel": "telegram",
        "payload": {"media_id": media_id, "caption": "支撑压力位"},
        "ref": "pub_" + "1" * 32,
    }
    assert "private" not in json.dumps(result, ensure_ascii=False)


def test_publish_tool_rejects_paths_urls_tokens_and_malformed_runtime_envelope():
    from guanlan_v2.channels import agent_tools as at
    from guanlan_v2.console import tools as ct

    media_id = "mi_" + "b" * 32

    class Runtime:
        def __init__(self):
            self.calls = 0

        def publish_media(self, sid, supplied_media_id, caption, confirmation_ticket):
            self.calls += 1
            return {
                "ok": True,
                "artifact": {
                    "kind": "media_image",  # ordinary UI image is never outbound
                    "page": None,
                    "channel": "conversation",
                    "payload": {"media_id": supplied_media_id, "caption": caption},
                    "ref": "pub_" + "2" * 32,
                },
            }

    runtime = Runtime()
    sid_token = ct.CTX_SID.set("cs_current")
    runtime_token = at.CTX_CHANNEL_RUNTIME.set(
        at.ChannelToolContext.capture(runtime)
    )
    permit_token = at.CTX_MEDIA_PUBLICATION_TICKETS.set({})
    try:
        for invalid in (
            r"C:\\private\\chart.png",
            "output/chart.png",
            "https://example.invalid/chart.png",
            "file:///private/chart.png",
        ):
            denied = at.telegram_publish_impl(media_id=invalid, caption="图")
            assert denied["ok"] is False
            assert denied["reason_code"] == "telegram_media_invalid"
        secret_caption = at.telegram_publish_impl(
            media_id=media_id,
            caption="123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZ_abcdefghi",
        )
        assert secret_caption["ok"] is False
        for path_caption in (
            r"C:\x.png",
            "/tmp/x",
            "file:///tmp/x.png",
            "https://example.invalid/x.png",
            "output/chart.png",
        ):
            denied_caption = at.telegram_publish_impl(
                media_id=media_id,
                caption=path_caption,
            )
            assert denied_caption["ok"] is False
            assert denied_caption["reason_code"] == "telegram_caption_invalid"
        assert at.stash_media_publication_ticket(media_id, "图", "proof")
        malformed = at.telegram_publish_impl(media_id=media_id, caption="图")
    finally:
        at.CTX_MEDIA_PUBLICATION_TICKETS.reset(permit_token)
        at.CTX_CHANNEL_RUNTIME.reset(runtime_token)
        ct.CTX_SID.reset(sid_token)

    assert runtime.calls == 1
    assert malformed["ok"] is False
    assert malformed["reason_code"] == "telegram_media_publish_invalid"


def test_runtime_publish_media_requires_active_writable_owner_scope(tmp_path):
    from guanlan_v2.channels.runtime import ChannelRuntime, ChannelRuntimeError
    from guanlan_v2.channels.store import ChannelStore

    console = ConsoleStore(root=tmp_path / "console")
    sid = console.create_session("图片投递")['id']
    channels = ChannelStore(tmp_path / "channels.sqlite3")
    bot_id = channels.create_bot("图片 Bot", draft_session_id=sid)
    channels.cas_bot_credential(
        bot_id,
        expected_revision=0,
        credential_id="cred_" + "a" * 32,
        telegram_bot_id="1001",
    )
    channels.bind_for_test(
        sid,
        bot_id,
        user_id="7",
        chat_id="7",
        sync_from_event_id=0,
    )
    media_id = "mi_" + "c" * 32

    class SessionMedia:
        def __init__(self):
            self.calls = []

        def load_for_session(self, candidate, owner_sid):
            self.calls.append((candidate, owner_sid))
            if candidate != media_id or owner_sid != sid:
                raise LookupError("not owned")
            return ({"id": candidate, "mime": "image/png"}, b"image")

    session_media = SessionMedia()
    runtime = ChannelRuntime(
        console_store=console,
        channel_store=channels,
        secret_store=object(),
        telegram_adapter=object(),
        media_store=session_media,
    )

    def issue(candidate_media_id, candidate_caption):
        preview = runtime.media_publication_confirmation_preview(
            sid, candidate_media_id, candidate_caption
        )
        return runtime.issue_media_publication_confirmation(
            sid,
            candidate_media_id,
            candidate_caption,
            preview["binding_id"],
            preview["binding_paired_at"],
        )

    for path_caption in (
        r"C:\x.png",
        "/tmp/x",
        "file:///tmp/x.png",
        "https://example.invalid/x.png",
        "output/chart.png",
    ):
        with pytest.raises(ChannelRuntimeError) as location:
            runtime.issue_media_publication_confirmation(
                sid, media_id, path_caption
            )
        assert location.value.reason_code == "telegram_caption_rejected"
    assert isinstance(
        issue(
            media_id,
            "Alpha:0.3 · Beta:1.2 · 20/60/120\n中文分析",
        ),
        str,
    )

    with pytest.raises(ChannelRuntimeError) as unconfirmed:
        runtime.publish_media(sid, media_id, "压力与支撑")
    assert unconfirmed.value.reason_code == "telegram_publish_confirmation_required"
    assert session_media.calls == []

    confirmation = issue(media_id, "压力与支撑")
    published = runtime.publish_media(
        sid, media_id, "压力与支撑", confirmation
    )
    assert published["artifact"] == {
        "kind": "telegram_media_publish",
        "page": None,
        "channel": "telegram",
        "payload": {"media_id": media_id, "caption": "压力与支撑"},
        "ref": published["artifact"]["ref"],
    }
    assert re.fullmatch(r"pub_[0-9a-f]{32}", published["artifact"]["ref"])
    assert session_media.calls == [(media_id, sid)]

    with pytest.raises(ChannelRuntimeError) as replayed:
        runtime.publish_media(sid, media_id, "压力与支撑", confirmation)
    assert replayed.value.reason_code == "telegram_publish_confirmation_required"
    assert session_media.calls == [(media_id, sid)]

    card_generation = runtime.media_publication_confirmation_preview(
        sid, media_id, "确认卡代次"
    )
    with channels.connect() as conn:
        conn.execute(
            "UPDATE channel_bindings SET paired_at = ? WHERE session_id = ? "
            "AND status = 'active'",
            ("2030-01-01T00:00:00.000000+00:00", sid),
        )
    with pytest.raises(ChannelRuntimeError) as changed_before_approval:
        runtime.issue_media_publication_confirmation(
            sid,
            media_id,
            "确认卡代次",
            card_generation["binding_id"],
            card_generation["binding_paired_at"],
        )
    assert changed_before_approval.value.reason_code == "telegram_recipient_changed"

    old_generation_ticket = issue(media_id, "接收者代次")
    with channels.connect() as conn:
        conn.execute(
            "UPDATE channel_bindings SET paired_at = ? WHERE session_id = ? "
            "AND status = 'active'",
            ("2031-01-01T00:00:00.000000+00:00", sid),
        )
    with pytest.raises(ChannelRuntimeError) as generation_changed:
        runtime.publish_media(
            sid, media_id, "接收者代次", old_generation_ticket
        )
    assert (
        generation_changed.value.reason_code
        == "telegram_publish_confirmation_required"
    )
    assert session_media.calls == [(media_id, sid)]

    with pytest.raises(ChannelRuntimeError) as foreign:
        foreign_id = "mi_" + "d" * 32
        foreign_ticket = issue(foreign_id, "图")
        runtime.publish_media(sid, foreign_id, "图", foreign_ticket)
    assert foreign.value.reason_code == "telegram_media_not_found"

    console.set_archived(sid, True)
    with pytest.raises(ChannelRuntimeError) as archived:
        runtime.publish_media(sid, media_id, "图")
    assert archived.value.reason_code == "channel_session_read_only"
    # The archive gate runs before any media read, so a newly requested send
    # cannot become an implicit historic-media capability.
    assert session_media.calls == [(media_id, sid), ("mi_" + "d" * 32, sid)]


def test_rejected_publish_confirmation_emits_no_media_publish_result(tmp_path):
    media_id = "mi_" + "e" * 32

    class Runtime:
        def extract_telegram_tokens(self, _text):
            return ()

        def media_publication_confirmation_preview(self, sid, candidate, caption):
            assert candidate == media_id and caption == "压力与支撑"
            return {
                "binding_id": "bind_generation_a",
                "binding_paired_at": "2026-08-23T00:00:00.000000+00:00",
                "recipient": {
                    "user_id": "7",
                    "username": "owner",
                    "display_name": "",
                },
            }

        def status_for_session(self, sid):
            return {
                "available": True,
                "session_id": sid,
                "view_state": "active",
                "bot": {"display_name": "图片 Bot", "username": "image_bot"},
                "recipient": {"user_id": "7", "username": "owner"},
                "pairing_requests": [],
                "counts": {},
                "warnings": [],
                "recent_deliveries": [],
                "automations": [],
            }

    class Agent:
        def __init__(self):
            self.messages = []
            self.approved = None

        async def run_turn(self, _text, confirm_callback=None, allowed_tools=None):
            self.approved = await confirm_callback(
                "ww_telegram_publish",
                {"media_id": media_id, "caption": "压力与支撑"},
            )
            if self.approved:
                # This branch makes an accidental approval visible as a
                # projectable event instead of letting the test pass silently.
                yield type("Evt", (), {
                    "kind": "tool_result",
                    "payload": {
                        "name": "ww_telegram_publish",
                        "content": "published",
                        "is_error": False,
                        "side_effect": {"artifact": {
                            "kind": "telegram_media_publish",
                            "page": None,
                            "channel": "telegram",
                            "payload": {
                                "media_id": media_id,
                                "caption": "压力与支撑",
                            },
                            "ref": media_id,
                        }},
                    },
                })()
            yield type("Evt", (), {"kind": "done", "payload": None})()

    store = ConsoleStore(root=tmp_path)
    agent = Agent()
    app = FastAPI()
    app.include_router(build_console_router(
        store=store,
        agent_factory=lambda _sid: agent,
        channel_runtime=Runtime(),
    ))
    with TestClient(app) as client:
        sid = client.post("/console/sessions", json={}).json()["meta"]["id"]
        assert client.post(
            "/console/send", json={"sid": sid, "text": "把这张图发给我"}
        ).json()["ok"]
        events = _wait(store, sid, lambda items: any(
            item.get("type") == "confirm_request"
            and item.get("tool") == "ww_telegram_publish"
            for item in items
        ))
        request = next(
            item for item in events
            if item.get("type") == "confirm_request"
            and item.get("tool") == "ww_telegram_publish"
        )
        assert client.post("/console/confirm", json={
            "turn_id": request["turn_id"],
            "confirm_id": request["confirm_id"],
            "choice": "n",
        }).json()["ok"]
        events = _wait(store, sid, lambda items: any(
            item.get("type") == "task_update" and item.get("status") == "done"
            for item in items
        ))

    assert agent.approved is False
    assert not any(
        item.get("type") == "tool_result"
        and item.get("tool") == "ww_telegram_publish"
        for item in events
    )


def test_publish_confirmation_freezes_displayed_recipient_generation(tmp_path):
    media_id = "mi_" + "f" * 32
    caption = "第一行\n" + "甲" * 400

    class RecipientChanged(RuntimeError):
        reason_code = "telegram_recipient_changed"

    class Runtime:
        def __init__(self):
            self.tickets = ActionTicketStore(ttl_seconds=120)
            self.generation = {
                "binding_id": "bind_a",
                "binding_paired_at": "2026-08-23T00:00:00.000000+00:00",
                "recipient": {
                    "user_id": "7",
                    "username": "recipient_a",
                    "display_name": "A",
                },
            }
            self.issue_calls = []

        def issue_action_ticket(self, sid, action, subject_id):
            return self.tickets.issue(sid, action, subject_id)

        def consume_action_ticket(self, ticket, sid, action, subject_id):
            return self.tickets.consume(ticket, sid, action, subject_id)

        def extract_telegram_tokens(self, _text):
            return ()

        def status_for_session(self, sid):
            return {
                "available": True,
                "session_id": sid,
                "view_state": "active",
                "bot": {"display_name": "图片 Bot", "username": "image_bot"},
                "recipient": dict(self.generation["recipient"]),
                "pairing_requests": [],
                "counts": {},
                "warnings": [],
                "recent_deliveries": [],
                "automations": [],
            }

        def media_publication_confirmation_preview(self, sid, candidate, supplied):
            assert sid and candidate == media_id and supplied == caption
            return {
                "binding_id": self.generation["binding_id"],
                "binding_paired_at": self.generation["binding_paired_at"],
                "recipient": dict(self.generation["recipient"]),
            }

        def issue_media_publication_confirmation(
            self, sid, candidate, supplied, expected_binding_id, expected_paired_at
        ):
            self.issue_calls.append(
                (sid, candidate, supplied, expected_binding_id, expected_paired_at)
            )
            if (
                expected_binding_id != self.generation["binding_id"]
                or expected_paired_at != self.generation["binding_paired_at"]
            ):
                raise RecipientChanged("recipient changed")
            return "ticket"

    class Agent:
        def __init__(self):
            self.messages = []
            self.approved = None

        async def run_turn(self, _text, confirm_callback=None, allowed_tools=None):
            self.approved = await confirm_callback(
                "ww_telegram_publish",
                {"media_id": media_id, "caption": caption},
            )
            yield type("Evt", (), {"kind": "done", "payload": None})()

    runtime = Runtime()
    agent = Agent()
    store = ConsoleStore(root=tmp_path)
    app = FastAPI()
    app.include_router(
        build_console_router(
            store=store,
            agent_factory=lambda _sid: agent,
            channel_runtime=runtime,
        )
    )
    with TestClient(app) as client:
        sid = client.post("/console/sessions", json={}).json()["meta"]["id"]
        assert client.post(
            "/console/send", json={"sid": sid, "text": "发图"}
        ).json()["ok"]
        events = _wait(
            store,
            sid,
            lambda items: any(
                item.get("type") == "confirm_request"
                and item.get("tool") == "ww_telegram_publish"
                for item in items
            ),
        )
        request = next(
            item
            for item in events
            if item.get("type") == "confirm_request"
            and item.get("tool") == "ww_telegram_publish"
        )
        assert request["public_details"]["telegram_username"] == "recipient_a"
        assert request["public_details"]["caption"] == caption
        assert request["public_details"]["caption_length"] == len(caption)

        runtime.generation = {
            "binding_id": "bind_b",
            "binding_paired_at": "2026-08-23T00:01:00.000000+00:00",
            "recipient": {
                "user_id": "8",
                "username": "recipient_b",
                "display_name": "B",
            },
        }
        issued = client.post(
            "/console/confirm/request-token",
            json={
                "turn_id": request["turn_id"],
                "confirm_id": request["confirm_id"],
            },
        )
        assert issued.status_code == 200
        assert client.post(
            "/console/confirm",
            json={
                "turn_id": request["turn_id"],
                "confirm_id": request["confirm_id"],
                "choice": "y",
                "request_token": issued.json()["request_token"],
            },
        ).json()["ok"]
        events = _wait(
            store,
            sid,
            lambda items: any(
                item.get("type") == "task_update" and item.get("status") == "done"
                for item in items
            ),
        )

    assert agent.approved is False
    assert runtime.issue_calls[-1][3:] == (
        "bind_a",
        "2026-08-23T00:00:00.000000+00:00",
    )
    assert not any(
        item.get("type") == "tool_result"
        and item.get("tool") == "ww_telegram_publish"
        for item in events
    )


def test_invalid_publish_location_never_enters_console_public_events(tmp_path):
    media_id = "mi_" + "1" * 32
    unsafe_caption = r"C:\private\chart.png"

    class Runtime:
        def extract_telegram_tokens(self, _text):
            return ()

    class Agent:
        def __init__(self):
            self.messages = []
            self.approved = None

        async def run_turn(self, _text, confirm_callback=None, allowed_tools=None):
            args = {"media_id": media_id, "caption": unsafe_caption}
            self.approved = await confirm_callback("ww_telegram_publish", args)
            # A hostile/custom Agent may still emit its private call after the
            # guard rejects it.  The public event projection must remain empty.
            yield type(
                "Evt",
                (),
                {
                    "kind": "tool_call",
                    "payload": {"name": "ww_telegram_publish", "args": args},
                },
            )()
            yield type(
                "Evt",
                (),
                {
                    "kind": "tool_result",
                    "payload": {
                        "name": "ww_telegram_publish",
                        "content": f"pretend success {unsafe_caption}",
                        "is_error": False,
                        "side_effect": {
                            "artifact": {
                                "kind": "telegram_media_publish",
                                "page": None,
                                "channel": "telegram",
                                "payload": {
                                    "media_id": media_id,
                                    "caption": unsafe_caption,
                                },
                                "ref": "pub_" + "2" * 32,
                            }
                        },
                    },
                },
            )()
            yield type("Evt", (), {"kind": "done", "payload": None})()

    store = ConsoleStore(root=tmp_path)
    agent = Agent()
    app = FastAPI()
    app.include_router(
        build_console_router(
            store=store,
            agent_factory=lambda _sid: agent,
            channel_runtime=Runtime(),
        )
    )
    with TestClient(app) as client:
        sid = client.post("/console/sessions", json={}).json()["meta"]["id"]
        assert client.post(
            "/console/send", json={"sid": sid, "text": "尝试发图"}
        ).json()["ok"]
        events = _wait(
            store,
            sid,
            lambda items: any(
                item.get("type") == "task_update" and item.get("status") == "done"
                for item in items
            ),
        )

    assert agent.approved is False
    public_non_user = json.dumps(
        [item for item in events if item.get("type") != "user_msg"],
        ensure_ascii=False,
    )
    assert unsafe_caption not in public_non_user
    assert not any(item.get("type") == "confirm_request" for item in events)
    tool_call = next(item for item in events if item.get("type") == "tool_call")
    assert tool_call["args"] == {}
    tool_result = next(item for item in events if item.get("type") == "tool_result")
    assert tool_result["ok"] is False
    assert tool_result["artifact"] is None


def test_completed_mutation_stays_successful_when_status_refresh_fails():
    from guanlan_v2.channels import agent_tools as at
    from guanlan_v2.console import tools as ct

    class Runtime:
        def create_draft(self, sid, display_name):
            return object()

        def status_for_session(self, sid):
            raise RuntimeError("refresh unavailable")

    sid_token = ct.CTX_SID.set("cs_current")
    runtime_token = at.CTX_CHANNEL_RUNTIME.set(
        at.ChannelToolContext.capture(Runtime())
    )
    try:
        result = at.telegram_connect_impl(display_name="晨报助手")
    finally:
        at.CTX_CHANNEL_RUNTIME.reset(runtime_token)
        ct.CTX_SID.reset(sid_token)

    assert result["ok"] is True
    assert result["warning_code"] == "channel_status_unavailable"
    assert "不要盲目重试" in result["content"]


def test_pair_status_content_exposes_only_safe_request_handle_for_dismiss():
    from guanlan_v2.channels import agent_tools as at
    from guanlan_v2.console import tools as ct

    class Runtime:
        def status_for_session(self, sid):
            return {
                "available": True,
                "view_state": "pairing_pending",
                "pairing_requests": [{
                    "request_id": "pair_safe_one",
                    "username": "researcher",
                    "display_name": "研究员",
                    "expires_at": "2026-08-23T09:00:00+00:00",
                }],
            }

    sid_token = ct.CTX_SID.set("cs_current")
    runtime_token = at.CTX_CHANNEL_RUNTIME.set(
        at.ChannelToolContext.capture(Runtime())
    )
    try:
        result = at.telegram_pair_status_impl()
    finally:
        at.CTX_CHANNEL_RUNTIME.reset(runtime_token)
        ct.CTX_SID.reset(sid_token)

    assert result["ok"] is True
    assert "pair_safe_one" in result["content"]
    assert "@researcher" in result["content"]


def test_status_content_includes_safe_delivery_truth_for_agent():
    from guanlan_v2.channels import agent_tools as at

    content = at._status_content({
        "view_state": "uncertain",
        "bot": {"display_name": "晨报 Bot", "username": "morning_bot"},
        "recipient": {
            "user_id": "778899",
            "username": "researcher",
            "display_name": "研究员",
        },
        "counts": {"pending": 1, "retry_wait": 2, "uncertain": 3, "terminal": 4},
        "warnings": [{"error_code": "delivery_uncertain"}],
        "recent_deliveries": [{
            "outbox_id": "out_one",
            "kind": "text",
            "state": "uncertain",
            "attempts": 2,
            "error_class": "read_timeout",
        }],
    })

    assert "@researcher" in content
    assert "待发送 1" in content and "重试中 2" in content
    assert "状态未知 3" in content and "终止 4" in content
    assert "delivery_uncertain" in content
    assert "out_one" in content and "read_timeout" in content


def test_buddy_snapshot_redacts_assistant_and_rejects_pair_args_with_extras():
    from guanlan_v2.console.api import _agent_state_snapshot

    code = "23456789"

    class Message:
        def to_litellm(self):
            return {
                "role": "assistant",
                "content": f"不应保存 {code}",
                "reasoning_content": f"思考中也有 {code}",
                "tool_calls": [{
                    "id": "call_pair",
                    "type": "function",
                    "function": {
                        "name": "ww_telegram_pair_approve",
                        "arguments": json.dumps({
                            "code": code,
                            "token": "123456:ABCDEFGHIJKLMNOPQRSTUVWXYZabcd",
                        }),
                    },
                }],
            }

    agent = type("Agent", (), {"messages": [Message()]})()
    # No current-turn code list: a later turn can still recall a historical
    # private pair tool call, so assistant/reasoning sanitization is generic.
    state = _agent_state_snapshot(agent, 7, pairing_codes=set())
    assert state is not None
    assistant = state["messages"][0]
    assert code not in assistant["content"]
    assert code not in assistant["reasoning_content"]
    arguments = json.loads(assistant["tool_calls"][0]["function"]["arguments"])
    assert arguments == {}


def test_buddy_snapshot_keeps_only_exact_private_pair_tool_argument():
    from guanlan_v2.console.api import _agent_state_snapshot

    code = "23456789"

    class Message:
        def to_litellm(self):
            return {
                "role": "assistant",
                "content": "",
                "tool_calls": [{
                    "id": "call_pair_exact",
                    "type": "function",
                    "function": {
                        "name": "ww_telegram_pair_approve",
                        "arguments": json.dumps({"code": code}),
                    },
                }],
            }

    state = _agent_state_snapshot(
        type("Agent", (), {"messages": [Message()]})(), 8, pairing_codes=set()
    )
    assert state is not None
    arguments = json.loads(
        state["messages"][0]["tool_calls"][0]["function"]["arguments"]
    )
    assert arguments == {"code": code}


def test_cross_tool_secret_guard_matrix_and_public_redaction():
    from guanlan_v2.console.api import (
        _console_tool_argument_guard,
        _public_confirm_args,
        _public_tool_args,
    )

    code = "23456789"
    token = "123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZ_abcdefghi"
    assert _console_tool_argument_guard(
        "ww_telegram_pair_approve", {"code": code}
    ) is None
    assert _console_tool_argument_guard(
        "ww_telegram_pair_approve", {"code": code, "extra": "x"}
    )
    assert _console_tool_argument_guard(
        "ww_plan_update", {"todos": [{"text": f"use {code}"}]}
    )
    assert _console_tool_argument_guard(
        "ww_memory_write", {"content": token}
    )
    assert _console_tool_argument_guard(
        "quote_lookup", {code: "secret-in-key"}
    )

    for public in (
        _public_tool_args("ww_plan_update", {"todos": [{code: token}]}),
        _public_confirm_args("ww_cards_save", {"insight": code, token: "x"}),
    ):
        encoded = json.dumps(public, ensure_ascii=False)
        assert code not in encoded
        assert token not in encoded


def test_real_buddy_guard_blocks_cross_tool_execution_without_echo(monkeypatch):
    from financial_analyst.buddy import agent as buddy_agent_module
    from financial_analyst.buddy.tools import Tool, ToolResult
    from guanlan_v2.console.api import _console_tool_argument_guard

    assert "engine" in Path(buddy_agent_module.__file__).resolve().parts
    assert hasattr(buddy_agent_module, "CTX_TOOL_ARGUMENT_GUARD")
    code = "23456789"
    executions = []

    class Client:
        total_completion_tokens = 0

        def __init__(self):
            self.calls = 0

        async def chat(self, messages, tools=None, temperature=0.2):
            self.calls += 1
            if self.calls == 1:
                return {"choices": [{"message": {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [{
                        "id": "call_secret_plan",
                        "type": "function",
                        "function": {
                            "name": "ww_plan_update",
                            "arguments": json.dumps({
                                "todos": [{"text": f"保存 {code}"}]
                            }),
                        },
                    }],
                }}]}
            return {"choices": [{"message": {
                "role": "assistant", "content": "已安全停止错误调用",
            }}]}

    tool = Tool(
        name="ww_plan_update",
        description="test sentinel",
        input_schema={"type": "object"},
        run=lambda **kwargs: executions.append(kwargs) or ToolResult("ran"),
    )
    monkeypatch.setattr(
        buddy_agent_module,
        "get_tool",
        lambda name: tool if name == "ww_plan_update" else None,
    )
    agent = buddy_agent_module.BuddyAgent(system_prompt="test", max_tool_iters=2)
    agent._client = Client()

    async def scenario():
        token = buddy_agent_module.CTX_TOOL_ARGUMENT_GUARD.set(
            _console_tool_argument_guard
        )
        try:
            return [event async for event in agent.run_turn("执行计划")]
        finally:
            buddy_agent_module.CTX_TOOL_ARGUMENT_GUARD.reset(token)

    events = asyncio.run(scenario())
    assert executions == []
    attempted = next(event for event in events if event.kind == "tool_call")
    blocked = next(event for event in events if event.kind == "tool_result")
    assert attempted.payload == {"name": "ww_plan_update", "args": {}}
    assert blocked.payload["is_error"] is True
    assert code not in json.dumps(
        [event.payload for event in events], ensure_ascii=False
    )


def test_console_redacts_fake_tool_side_effects_from_events_and_plan(tmp_path):
    code = "23456789"
    token = "123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZ_abcdefghi"

    class FakeAgent:
        def __init__(self):
            self.messages = []

        async def run_turn(self, text, confirm_callback=None, allowed_tools=None):
            yield type("Evt", (), {
                "kind": "tool_call",
                "payload": {
                    "name": "ww_plan_update",
                    "args": {"todos": [{code: token}]},
                },
            })()
            yield type("Evt", (), {
                "kind": "tool_result",
                "payload": {
                    "name": "ww_plan_update",
                    "content": f"result {code} {token}",
                    "is_error": False,
                    "side_effect": {
                        "plan": [{"id": "p1", "text": code, code: token}],
                        "artifact": {"kind": "test", "payload": {code: token}},
                        "background": {"kind": "unknown", "code": code},
                    },
                },
            })()
            yield type("Evt", (), {"kind": "done", "payload": None})()

    store = ConsoleStore(root=tmp_path)
    app = FastAPI()
    app.include_router(build_console_router(
        store=store, agent_factory=lambda _sid: FakeAgent()
    ))
    with TestClient(app) as client:
        sid = client.post("/console/sessions", json={}).json()["meta"]["id"]
        assert client.post(
            "/console/send", json={"sid": sid, "text": "测试跨工具清洗"}
        ).json()["ok"]
        events = _wait(store, sid, lambda items: any(
            item.get("type") == "task_update" and item.get("status") == "done"
            for item in items
        ))

    for event in events:
        if event.get("type") != "user_msg":
            encoded = json.dumps(event, ensure_ascii=False)
            assert code not in encoded
            assert token not in encoded
    encoded_meta = json.dumps(store.get_meta(sid), ensure_ascii=False)
    assert code not in encoded_meta
    assert token not in encoded_meta


def test_no_runtime_fails_closed_before_send_or_steer_can_persist_token(tmp_path):
    token = "123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZ_abcdefghi"
    store = ConsoleStore(root=tmp_path)
    app = FastAPI()
    app.include_router(build_console_router(store=store))

    with TestClient(app) as client:
        sid = client.post("/console/sessions", json={}).json()["meta"]["id"]
        for path in ("/console/send", "/console/steer"):
            response = client.post(path, json={"sid": sid, "text": token})
            assert response.status_code == 400
            assert response.json() == {
                "ok": False,
                "reason_code": "channel_secret_inspection_unavailable",
            }
            assert token not in response.text

    encoded_events = json.dumps(store.read_events(sid), ensure_ascii=False)
    assert token not in encoded_events
    assert store.load_buddy_state(sid) is None


def test_channel_public_argument_whitelists_hide_secrets_and_bound_text():
    from guanlan_v2.channels import agent_tools as at
    from guanlan_v2.console.api import _console_tool_argument_guard

    secret = "123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZ_abcdefghi"
    assert at.public_tool_args(
        "ww_telegram_pair_approve", {"code": "23456789"}
    ) == {}
    assert at.public_tool_args(
        "ww_telegram_connect", {"display_name": secret, "extra": "never"}
    ) == {"display_name": "[内容已隐藏]"}
    public = at.public_tool_args("ww_automation_upsert", {
        "name": "晨报" * 100,
        "prompt_text": "不得进入事件 " + secret,
        "timezone": "Asia/Hong_Kong",
        "extra": "never",
    })
    assert set(public) == {"name", "timezone"}
    assert len(public["name"]) <= 128
    assert secret not in json.dumps(public, ensure_ascii=False)

    media_id = "mi_" + "a" * 32
    assert at.public_tool_args(
        "ww_telegram_publish", {"media_id": media_id, "caption": "正常分析"}
    ) == {"media_id": media_id}
    for unsafe in (
        {"media_id": r"C:\private\chart.png", "caption": ""},
        {"media_id": media_id, "caption": "https:example.invalid/chart"},
        {"media_id": media_id, "caption": "reports/secret"},
        {"media_id": media_id, "caption": "正常", "extra": "C:/hidden"},
    ):
        assert at.public_tool_args("ww_telegram_publish", unsafe) == {}
        assert _console_tool_argument_guard("ww_telegram_publish", unsafe)
        rendered = json.dumps(
            at.confirm_metadata("ww_telegram_publish", unsafe, {}),
            ensure_ascii=False,
        )
        assert "private" not in rendered
        assert "example.invalid" not in rendered
        assert "reports/secret" not in rendered
        assert "C:/hidden" not in rendered


def test_pair_confirm_event_redacts_code_and_requires_scoped_one_use_ticket(
    tmp_path, monkeypatch
):
    code = "23456789"
    clock = [100.0]
    monkeypatch.setattr("guanlan_v2.console.api._CONDENSE_MSGS", -1)

    class Runtime:
        def __init__(self):
            self.tickets = ActionTicketStore(
                ttl_seconds=120, clock=lambda: clock[0]
            )

        def issue_action_ticket(self, sid, action, subject_id):
            return self.tickets.issue(sid, action, subject_id)

        def consume_action_ticket(self, ticket, sid, action, subject_id):
            return self.tickets.consume(ticket, sid, action, subject_id)

        def extract_telegram_tokens(self, text):
            return ()

        def status_for_session(self, sid):
            return {
                "available": True,
                "session_id": sid,
                "view_state": "pairing_pending",
                "bot": {"display_name": "晨报助手", "username": "morning_bot"},
                "pairing_requests": [{
                    "request_id": "pair_wrong_same_hint",
                    "user_id": "111111",
                    "username": "wrong_user",
                    "display_name": "错误请求",
                    "code_hint": "89",
                    "created_at": "2026-08-23T08:00:00+00:00",
                    "expires_at": "2026-08-23T09:00:00+00:00",
                }],
            }

        async def resolve_pairing_code(self, sid, supplied_code):
            assert supplied_code == code
            return {
                "request_id": "pair_exact",
                "user_id": "778899",
                "username": "researcher",
                "display_name": "研究员",
                "code_hint": "89",
                "created_at": "2026-08-23T08:00:00+00:00",
                "expires_at": "2026-08-23T09:00:00+00:00",
            }

    class Agent:
        def __init__(self):
            self.messages = []
            self.executions = 0

        async def compact(self):
            return f"历史摘要里也不应公开 {code}"

        async def run_turn(self, text, confirm_callback=None, allowed_tools=None):
            # Split the code across chunks to exercise the streaming carry,
            # then cross a tool protocol boundary.
            yield type("Evt", (), {"kind": "text", "payload": f"正在处理 {code[:4]}"})()
            yield type("Evt", (), {"kind": "text", "payload": f"{code[4:]} 请稍候"})()
            yield type("Evt", (), {
                "kind": "tool_call",
                "payload": {"name": "ww_telegram_pair_approve", "args": {"code": code}},
            })()
            ok = await confirm_callback("ww_telegram_pair_approve", {"code": code})
            if ok:
                self.executions += 1
            yield type("Evt", (), {"kind": "text", "payload": "done"})()
            yield type("Evt", (), {"kind": "done", "payload": None})()

    runtime = Runtime()
    agent = Agent()
    store = ConsoleStore(root=tmp_path)
    app = FastAPI()
    app.include_router(build_console_router(
        store=store,
        agent_factory=lambda _sid: agent,
        channel_runtime=runtime,
    ))

    with TestClient(app) as client:
        sid = client.post("/console/sessions", json={}).json()["meta"]["id"]
        assert client.post("/console/send", json={"sid": sid, "text": f"配对码 {code}"}).json()["ok"]
        events = _wait(
            store, sid,
            lambda items: any(item.get("type") == "confirm_request" for item in items),
        )
        request = next(item for item in events if item.get("type") == "confirm_request")
        turn_id = request["turn_id"]
        confirm_id = request["confirm_id"]
        assert request["args"] == {}
        assert request["confirm_kind"] == "telegram_pairing"
        assert request["action_ticket_required"] is True
        assert request["title"] == "确认永久绑定 Telegram"
        assert request["approve_label"] == "确认永久绑定"
        assert request["public_details"]["code_hint"] == "89"
        assert request["public_details"]["pairing_request_id"] == "pair_exact"
        assert request["public_details"]["telegram_user_id"] == "778899"
        assert request["public_details"]["recipient_verified"] is True
        assert code not in json.dumps(request, ensure_ascii=False)
        tool_call = next(item for item in events if item.get("type") == "tool_call")
        assert tool_call["args"] == {}
        assert code not in json.dumps(tool_call, ensure_ascii=False)
        user_event = next(item for item in events if item.get("type") == "user_msg")
        assert user_event["channel_projection"] == "never"
        for item in events:
            if item.get("type") != "user_msg":
                assert code not in json.dumps(item, ensure_ascii=False)

        from guanlan_v2.console import api as console_api

        assert code not in console_api._build_review_snapshot(store, sid)

        client_chosen_scope = client.post("/console/confirm/request-token", json={
            "turn_id": turn_id,
            "confirm_id": confirm_id,
            "tool_name": "ww_telegram_pair_approve",
        })
        assert client_chosen_scope.status_code == 400
        unknown_turn = client.post("/console/confirm/request-token", json={
            "turn_id": "turn_not_pending",
            "confirm_id": "confirm_not_pending",
        })
        assert unknown_turn.status_code == 409

        missing = client.post("/console/confirm", json={
            "turn_id": turn_id, "confirm_id": confirm_id, "choice": "y",
        })
        assert missing.status_code == 403
        assert missing.json() == {
            "ok": False, "reason_code": "channel_confirm_ticket_required"
        }
        assert agent.executions == 0

        wrong_sid = runtime.issue_action_ticket(
            "cs_other", "confirm:ww_telegram_pair_approve",
            f"{turn_id}:{confirm_id}",
        )
        wrong_tool = runtime.issue_action_ticket(
            sid, "confirm:ww_telegram_disconnect", f"{turn_id}:{confirm_id}"
        )
        wrong_turn = runtime.issue_action_ticket(
            sid, "confirm:ww_telegram_pair_approve", "turn_other:confirm_other"
        )
        for ticket in (wrong_sid, wrong_tool, wrong_turn):
            denied = client.post("/console/confirm", json={
                "turn_id": turn_id, "confirm_id": confirm_id,
                "choice": "y", "request_token": ticket,
            })
            assert denied.status_code == 403
            assert denied.json()["reason_code"] == "channel_confirm_ticket_required"
            assert agent.executions == 0

        expired = runtime.issue_action_ticket(
            sid, "confirm:ww_telegram_pair_approve", f"{turn_id}:{confirm_id}"
        )
        clock[0] += 121
        denied = client.post("/console/confirm", json={
            "turn_id": turn_id, "confirm_id": confirm_id,
            "choice": "y", "request_token": expired,
        })
        assert denied.status_code == 403 and agent.executions == 0

        issued = client.post("/console/confirm/request-token", json={
            "turn_id": turn_id,
            "confirm_id": confirm_id,
        })
        assert issued.status_code == 200
        valid = issued.json()["request_token"]
        accepted = client.post("/console/confirm", json={
            "turn_id": turn_id, "confirm_id": confirm_id,
            "choice": "y", "request_token": valid,
        })
        assert accepted.status_code == 200 and accepted.json() == {"ok": True}
        _wait(store, sid, lambda items: any(
            item.get("type") == "task_update" and item.get("status") == "done"
            for item in items
        ))
        assert agent.executions == 1

        replay = client.post("/console/confirm", json={
            "turn_id": turn_id, "confirm_id": confirm_id,
            "choice": "y", "request_token": valid,
        })
        assert replay.status_code != 200 or replay.json().get("ok") is False
        assert agent.executions == 1
        after_done = client.post("/console/confirm/request-token", json={
            "turn_id": turn_id,
            "confirm_id": confirm_id,
        })
        assert after_done.status_code == 409


def test_channel_confirm_rejection_does_not_require_ticket(tmp_path):
    code = "23456789"

    class Runtime:
        def extract_telegram_tokens(self, text):
            return ()

        def status_for_session(self, sid):
            return {
                "available": True,
                "view_state": "pairing_pending",
                "bot": {"display_name": "晨报助手", "username": "morning_bot"},
            }

        async def resolve_pairing_code(self, sid, supplied_code):
            assert supplied_code == code
            return {
                "request_id": "pair_exact",
                "user_id": "778899",
                "username": "researcher",
                "display_name": "研究员",
                "code_hint": "89",
                "created_at": "2026-08-23T08:00:00+00:00",
                "expires_at": "2026-08-23T09:00:00+00:00",
            }

    class Agent:
        def __init__(self):
            self.messages = []
            self.result = None

        async def run_turn(self, text, confirm_callback=None, allowed_tools=None):
            self.result = await confirm_callback(
                "ww_telegram_pair_approve", {"code": code}
            )
            yield type("Evt", (), {"kind": "done", "payload": None})()

    agent = Agent()
    store = ConsoleStore(root=tmp_path)
    app = FastAPI()
    app.include_router(build_console_router(
        store=store,
        agent_factory=lambda _sid: agent,
        channel_runtime=Runtime(),
    ))
    with TestClient(app) as client:
        sid = client.post("/console/sessions", json={}).json()["meta"]["id"]
        assert client.post("/console/send", json={"sid": sid, "text": code}).json()["ok"]
        events = _wait(store, sid, lambda items: any(
            item.get("type") == "confirm_request" for item in items
        ))
        turn_id = next(
            item["turn_id"] for item in events if item.get("type") == "confirm_request"
        )
        confirm_id = next(
            item["confirm_id"]
            for item in events
            if item.get("type") == "confirm_request"
        )
        rejected = client.post("/console/confirm", json={
            "turn_id": turn_id, "confirm_id": confirm_id, "choice": "n",
        })
        assert rejected.status_code == 200 and rejected.json() == {"ok": True}
        _wait(store, sid, lambda items: any(
            item.get("type") == "task_update" and item.get("status") == "done"
            for item in items
        ))
    assert agent.result is False


def test_same_turn_same_tool_cannot_reuse_ticket_across_confirm_instances(tmp_path):
    codes = ("23456789", "ABCDEFGH")

    class Runtime:
        def __init__(self):
            self.tickets = ActionTicketStore(ttl_seconds=120)

        def issue_action_ticket(self, sid, action, subject_id):
            return self.tickets.issue(sid, action, subject_id)

        def consume_action_ticket(self, ticket, sid, action, subject_id):
            return self.tickets.consume(ticket, sid, action, subject_id)

        def extract_telegram_tokens(self, text):
            return ()

        def status_for_session(self, sid):
            return {
                "available": True,
                "view_state": "pairing_pending",
                "bot": {"display_name": "晨报助手", "username": "morning_bot"},
            }

        async def resolve_pairing_code(self, sid, code):
            index = codes.index(code)
            return {
                "request_id": f"pair_{index}",
                "user_id": str(700 + index),
                "username": f"user_{index}",
                "display_name": f"用户{index}",
                "code_hint": code[-2:],
                "created_at": "2026-08-23T08:00:00+00:00",
                "expires_at": "2026-08-23T09:00:00+00:00",
            }

    class Agent:
        def __init__(self):
            self.messages = []
            self.results = []

        async def run_turn(self, text, confirm_callback=None, allowed_tools=None):
            for code in codes:
                self.results.append(await confirm_callback(
                    "ww_telegram_pair_approve", {"code": code}
                ))
            yield type("Evt", (), {"kind": "done", "payload": None})()

    runtime = Runtime()
    agent = Agent()
    store = ConsoleStore(root=tmp_path)
    app = FastAPI()
    app.include_router(build_console_router(
        store=store,
        agent_factory=lambda _sid: agent,
        channel_runtime=runtime,
    ))
    with TestClient(app) as client:
        sid = client.post("/console/sessions", json={}).json()["meta"]["id"]
        assert client.post(
            "/console/send", json={"sid": sid, "text": " ".join(codes)}
        ).json()["ok"]
        events = _wait(store, sid, lambda items: sum(
            item.get("type") == "confirm_request" for item in items
        ) >= 1)
        first = [item for item in events if item.get("type") == "confirm_request"][0]
        stale_ticket = client.post("/console/confirm/request-token", json={
            "turn_id": first["turn_id"], "confirm_id": first["confirm_id"],
        }).json()["request_token"]
        assert client.post("/console/confirm", json={
            "turn_id": first["turn_id"], "confirm_id": first["confirm_id"],
            "choice": "n",
        }).json()["ok"]

        events = _wait(store, sid, lambda items: sum(
            item.get("type") == "confirm_request" for item in items
        ) >= 2)
        second = [item for item in events if item.get("type") == "confirm_request"][1]
        assert second["turn_id"] == first["turn_id"]
        assert second["confirm_id"] != first["confirm_id"]

        stale_issue = client.post("/console/confirm/request-token", json={
            "turn_id": first["turn_id"], "confirm_id": first["confirm_id"],
        })
        assert stale_issue.status_code == 409
        stale_use = client.post("/console/confirm", json={
            "turn_id": second["turn_id"], "confirm_id": second["confirm_id"],
            "choice": "y", "request_token": stale_ticket,
        })
        assert stale_use.status_code == 403

        fresh_ticket = client.post("/console/confirm/request-token", json={
            "turn_id": second["turn_id"], "confirm_id": second["confirm_id"],
        }).json()["request_token"]
        assert client.post("/console/confirm", json={
            "turn_id": second["turn_id"], "confirm_id": second["confirm_id"],
            "choice": "y", "request_token": fresh_ticket,
        }).json()["ok"]
        _wait(store, sid, lambda items: any(
            item.get("type") == "task_update" and item.get("status") == "done"
            for item in items
        ))
    assert agent.results == [False, True]


def test_channel_tools_are_explicitly_excluded_from_mcp():
    from guanlan_v2.channels.agent_tools import CHANNEL_TOOL_NAMES
    from guanlan_v2.glmcp import tooltable
    from guanlan_v2.glmcp import server

    assert CHANNEL_TOOL_NAMES <= tooltable._EXCLUDED
    exposed = {tool["name"] for tool in tooltable.build_mcp_tools()}
    assert not (CHANNEL_TOOL_NAMES & exposed)
    for name in CHANNEL_TOOL_NAMES:
        response = asyncio.run(server.dispatch_tool(name, {}))
        assert "未知工具" in response[0].text


def test_background_review_receives_no_channel_runtime_authority(
    tmp_path, monkeypatch
):
    from financial_analyst.buddy import agent as buddy_agent_module
    from guanlan_v2.channels import agent_tools as at

    observed = {}
    reviewed = threading.Event()

    class MainAgent:
        def __init__(self):
            self.messages = []

        async def run_turn(self, text, confirm_callback=None, allowed_tools=None):
            for _ in range(5):
                yield type("Evt", (), {
                    "kind": "tool_call",
                    "payload": {"name": "ww_factor_fields", "args": {}},
                })()
                yield type("Evt", (), {
                    "kind": "tool_result",
                    "payload": {
                        "name": "ww_factor_fields",
                        "content": "ok",
                        "is_error": False,
                        "side_effect": None,
                    },
                })()
            yield type("Evt", (), {"kind": "done", "payload": None})()

    class ReviewAgent:
        def __init__(self, *args, **kwargs):
            self.max_tool_iters = 0

        async def run_turn(self, text, confirm_callback=None, allowed_tools=None):
            observed["runtime"] = at.CTX_CHANNEL_RUNTIME.get(None)
            observed["loop"] = at.CTX_CHANNEL_OWNER_LOOP.get(None)
            observed["thread"] = at.CTX_CHANNEL_OWNER_THREAD.get(None)
            reviewed.set()
            yield type("Evt", (), {"kind": "done", "payload": None})()

    class Runtime:
        def extract_telegram_tokens(self, text):
            return ()

    monkeypatch.setenv("CONSOLE_REVIEW_MODE", "monitor")
    monkeypatch.setattr(buddy_agent_module, "BuddyAgent", ReviewAgent)
    store = ConsoleStore(root=tmp_path)
    app = FastAPI()
    app.include_router(build_console_router(
        store=store,
        agent_factory=lambda _sid: MainAgent(),
        channel_runtime=Runtime(),
    ))
    with TestClient(app) as client:
        sid = client.post("/console/sessions", json={}).json()["meta"]["id"]
        assert client.post("/console/send", json={"sid": sid, "text": "复盘"}).json()["ok"]
        assert reviewed.wait(timeout=3)

    assert observed == {"runtime": None, "loop": None, "thread": None}
