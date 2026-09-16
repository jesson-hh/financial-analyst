"""Telegram read-only Channel end-to-end release gate.

These tests keep the production Console router, Channel router, Runtime,
projector, outbox, pairing supervisor, MediaStore, credential store and ASGI
lifespan in one graph.  Telegram's network and the nondeterministic LLM are
replaced by strict scripted fakes; Windows runs still use real CurrentUser
DPAPI for the credential ciphertext.
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import re
import sqlite3
import sys
import threading
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from guanlan_v2.channels import agent_tools as channel_agent_tools
from guanlan_v2.channels.api import build_channel_router
from guanlan_v2.channels.models import (
    BindingStatus,
    BotStatus,
    OutboxState,
)
from guanlan_v2.channels.runtime import ActionTicketStore, ChannelRuntime
from guanlan_v2.channels.secrets import MemoryProtector, SecretStore
from guanlan_v2.channels.store import ChannelStore
from guanlan_v2.channels.telegram import TelegramAdapter
from guanlan_v2.console.api import build_console_router
from guanlan_v2.console.media import MediaStore
from guanlan_v2.console.store import ConsoleStore
from guanlan_v2.console.workspaces import GENERAL_WORKSPACE_ID


# Build the value at runtime so the full credential is not itself checked into
# an ordinary source file.  It still has Telegram's real token shape and goes
# through the exact secret detector, URL builder and encrypted store.
RAW_TOKEN = "812345678" + ":" + "A1b2C3d4E5f6G7h8J9k0_LmNoPqRsTuV"
BOT_ID = "771234567"
BOT_USERNAME = "guanlan_readonly_test_bot"
PRIVATE_USER_ID = "880012345"
PRIVATE_USERNAME = "research_owner"
PAIRING_RE = re.compile(
    r"观澜 Telegram 配对码（1 小时内有效）：\n"
    r"([23456789ABCDEFGHJKLMNPQRSTUVWXYZ]{8})\n"
)
MEDIA_ID_RE = re.compile(r"mi_[0-9a-f]{32}")
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)
PROTECTOR_KEY = b"telegram-readonly-e2e-key-000001"


class _Event:
    def __init__(self, kind: str, payload: Any = None) -> None:
        self.kind = kind
        self.payload = payload


class ScriptedAgent:
    """Small deterministic Agent that still invokes the real Channel tools."""

    def __init__(self) -> None:
        self.messages: list[Any] = []
        self.calls: list[str] = []
        self.media_id: str | None = None

    @staticmethod
    def _tool_result(name: str, result: dict[str, Any]) -> _Event:
        side_effect: dict[str, Any] = {}
        if isinstance(result.get("artifact"), dict):
            side_effect["artifact"] = result["artifact"]
        return _Event(
            "tool_result",
            {
                "name": name,
                "content": str(result.get("content") or ""),
                "is_error": result.get("ok") is not True,
                "side_effect": side_effect,
            },
        )

    async def run_turn(
        self,
        text: str,
        confirm_callback: Callable[..., Any] | None = None,
        allowed_tools: Any = None,
    ):
        del allowed_tools
        self.calls.append(text)

        if "连接 Telegram" in text:
            args = {"display_name": "晨报研究 Bot"}
            yield _Event("tool_call", {"name": "ww_telegram_connect", "args": args})
            result = await asyncio.to_thread(
                channel_agent_tools.telegram_connect_impl, **args
            )
            yield self._tool_result("ww_telegram_connect", result)
            yield _Event("text", "连接草稿已经创建，请在安全卡提交 Token。")
            yield _Event("done")
            return

        code_match = re.search(
            r"(?<![0-9A-Z])[23456789ABCDEFGHJKLMNPQRSTUVWXYZ]{8}(?![0-9A-Z])",
            text,
        )
        if code_match is not None:
            code = code_match.group(0)
            args = {"code": code}
            yield _Event(
                "tool_call", {"name": "ww_telegram_pair_approve", "args": args}
            )
            approved = bool(
                confirm_callback
                and await confirm_callback("ww_telegram_pair_approve", args)
            )
            if approved:
                result = await asyncio.to_thread(
                    channel_agent_tools.telegram_pair_approve_impl, **args
                )
                yield self._tool_result("ww_telegram_pair_approve", result)
            # Deliberately no public final text: the confirmation/tool protocol
            # after binding must advance the cursor without becoming a message.
            yield _Event("done")
            return

        if "把刚才图片发到 Telegram" in text:
            assert self.media_id is not None
            args = {"media_id": self.media_id, "caption": "压力与支撑图"}
            yield _Event("tool_call", {"name": "ww_telegram_publish", "args": args})
            approved = bool(
                confirm_callback
                and await confirm_callback("ww_telegram_publish", args)
            )
            if approved:
                result = await asyncio.to_thread(
                    channel_agent_tools.telegram_publish_impl, **args
                )
                yield self._tool_result("ww_telegram_publish", result)
            yield _Event("done")
            return

        if "绑定前历史" in text:
            yield _Event("text", "这是绑定前的 Agent 历史答复。")
            yield _Event("done")
            return

        # One ordinary post-binding turn contains delta, tool, progress/error
        # and final surfaces.  Only the original user_msg and one committed
        # agent_final may cross the Channel projection boundary.
        yield _Event("delta", "仅供流式进度，不应单独发送")
        yield _Event(
            "tool_call", {"name": "ww_market_tape", "args": {"code": "600667"}}
        )
        yield _Event(
            "tool_result",
            {
                "name": "ww_market_tape",
                "content": "工具过程，不应发送",
                "is_error": False,
                "side_effect": {},
            },
        )
        yield _Event("error", "仅供错误进度，不是最终答复")
        yield _Event("text", "Agent 最终")
        yield _Event("text", "判断：保持观察。")
        yield _Event("done")


class IdleAgent:
    def __init__(self) -> None:
        self.messages: list[Any] = []
        self.calls: list[str] = []

    async def run_turn(self, text: str, confirm_callback=None, allowed_tools=None):
        del confirm_callback, allowed_tools
        self.calls.append(text)
        yield _Event("done")


class TelegramMock:
    """Stateful Bot API fake; request URLs/tokens are never retained."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._updates: list[dict[str, Any]] = []
        self._calls: list[dict[str, Any]] = []
        self._request_bodies: list[bytes] = []
        self.send_effect: str | None = None
        self._message_id = 100

    def queue_private_message(
        self,
        update_id: int,
        *,
        text: str,
        user_id: str = PRIVATE_USER_ID,
        username: str = PRIVATE_USERNAME,
    ) -> None:
        update = {
            "update_id": update_id,
            "message": {
                "message_id": update_id + 10,
                "text": text,
                "chat": {"id": int(user_id), "type": "private"},
                "from": {
                    "id": int(user_id),
                    "username": username,
                    "first_name": "Research",
                    "last_name": "Owner",
                },
            },
        }
        with self._lock:
            self._updates.append(update)

    @property
    def calls(self) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(item) for item in self._calls]

    @property
    def request_bodies(self) -> list[bytes]:
        with self._lock:
            return list(self._request_bodies)

    def pairing_code(self) -> str | None:
        for call in self.calls:
            if call.get("method") != "sendMessage":
                continue
            match = PAIRING_RE.search(str(call.get("text") or ""))
            if match is not None:
                return match.group(1)
        return None

    def delivery_calls(self) -> list[dict[str, Any]]:
        return [
            item
            for item in self.calls
            if item.get("method") in {"sendMessage", "sendPhoto"}
            and not (
                str(item.get("text") or "").startswith("观澜 Telegram 配对码")
            )
        ]

    @staticmethod
    def _multipart_value(body: bytes, field: str) -> str | None:
        pattern = re.compile(
            rb'name="' + re.escape(field.encode("ascii")) + rb'"\r\n\r\n(.*?)\r\n',
            re.DOTALL,
        )
        match = pattern.search(body)
        return match.group(1).decode("utf-8") if match is not None else None

    def _record(self, item: dict[str, Any], body: bytes) -> None:
        with self._lock:
            self._calls.append(item)
            self._request_bodies.append(bytes(body))

    def _send_failure(self, request: httpx.Request) -> httpx.Response | None:
        effect = self.send_effect
        if effect == "429":
            return httpx.Response(
                429,
                json={
                    "ok": False,
                    "error_code": 429,
                    "parameters": {"retry_after": 60},
                },
            )
        if effect == "401":
            return httpx.Response(
                401,
                json={"ok": False, "error_code": 401, "description": "Unauthorized"},
            )
        if effect == "403":
            return httpx.Response(
                403,
                json={"ok": False, "error_code": 403, "description": "Forbidden"},
            )
        if effect == "500":
            return httpx.Response(
                500,
                json={"ok": False, "error_code": 500, "description": "Internal"},
            )
        if effect == "connect_timeout":
            raise httpx.ConnectTimeout("mock connect timeout", request=request)
        if effect == "read_timeout":
            raise httpx.ReadTimeout("mock read timeout", request=request)
        return None

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        method = request.url.path.rsplit("/", 1)[-1]
        body = await request.aread()

        if method == "getMe":
            self._record({"method": method}, body)
            return httpx.Response(
                200,
                json={
                    "ok": True,
                    "result": {
                        "id": int(BOT_ID),
                        "username": BOT_USERNAME,
                        "first_name": "Guanlan",
                    },
                },
            )

        if method == "getUpdates":
            fields = parse_qs(body.decode("ascii"), keep_blank_values=True)
            offset = int(fields.get("offset", ["0"])[0])
            with self._lock:
                updates = [item for item in self._updates if item["update_id"] >= offset]
            self._record({"method": method, "offset": offset}, body)
            return httpx.Response(200, json={"ok": True, "result": updates})

        if method == "sendMessage":
            fields = parse_qs(body.decode("utf-8"), keep_blank_values=True)
            item = {
                "method": method,
                "chat_id": fields.get("chat_id", [None])[0],
                "text": fields.get("text", [None])[0],
            }
            self._record(item, body)
            failure = self._send_failure(request)
            if failure is not None:
                return failure
        elif method == "sendPhoto":
            item = {
                "method": method,
                "chat_id": self._multipart_value(body, "chat_id"),
                "caption": self._multipart_value(body, "caption"),
                "filename": (
                    re.search(rb'filename="([^"]+)"', body).group(1).decode("ascii")
                    if re.search(rb'filename="([^"]+)"', body)
                    else None
                ),
                "has_png": PNG in body,
            }
            self._record(item, body)
            failure = self._send_failure(request)
            if failure is not None:
                return failure
        else:  # pragma: no cover - fixed adapter methods make this unreachable
            self._record({"method": method}, body)
            return httpx.Response(404, json={"ok": False})

        with self._lock:
            self._message_id += 1
            message_id = self._message_id
        return httpx.Response(
            200, json={"ok": True, "result": {"message_id": message_id}}
        )


@dataclass
class Stack:
    root: Path
    console: ConsoleStore
    channels: ChannelStore
    secrets: SecretStore
    media: MediaStore
    runtime: ChannelRuntime
    telegram: TelegramMock
    http_client: httpx.AsyncClient
    app: FastAPI
    agents: dict[str, Any]


def _build_stack(
    root: Path,
    telegram: TelegramMock,
    *,
    ticket_clock: Callable[[], float] | None = None,
    agent_factory: Callable[[str], Any] | None = None,
) -> Stack:
    console_root = root / "console"
    console = ConsoleStore(root=console_root)
    channels = ChannelStore(console_root / "channels.sqlite3")
    secrets_root = console_root / "channel_secrets"
    try:
        if sys.platform != "win32":
            raise ImportError
        import win32crypt  # noqa: F401  # type: ignore[import-not-found]
    except ImportError:
        # Portable CI still exercises the exact atomic/ciphertext boundary.
        # The production Windows gate below is covered whenever DPAPI exists.
        secrets = SecretStore(
            secrets_root,
            protector=MemoryProtector(PROTECTOR_KEY),
        )
    else:
        secrets = SecretStore(secrets_root)
    media = MediaStore(console)
    http_client = httpx.AsyncClient(transport=httpx.MockTransport(telegram))
    adapter = TelegramAdapter(client=http_client)
    tickets = ActionTicketStore(
        ttl_seconds=120,
        clock=ticket_clock,
    ) if ticket_clock is not None else ActionTicketStore(ttl_seconds=120)
    runtime = ChannelRuntime(
        console_store=console,
        channel_store=channels,
        secret_store=secrets,
        telegram_adapter=adapter,
        media_store=media,
        ticket_store=tickets,
        automation_calendar=set(),
        maintenance_interval_seconds=60,
    )
    runtime.projector.idle_seconds = 0.01
    runtime.outbox_worker.idle_seconds = 0.01
    runtime.pairing.supervisor.reconcile_seconds = 0.01

    agents: dict[str, Any] = {}

    def make_agent(sid: str) -> Any:
        if sid not in agents:
            agents[sid] = agent_factory(sid) if agent_factory else ScriptedAgent()
        return agents[sid]

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        await runtime.start()
        try:
            yield
        finally:
            try:
                await runtime.stop()
            finally:
                await http_client.aclose()

    app = FastAPI(lifespan=lifespan)
    app.include_router(build_channel_router(runtime))
    app.include_router(
        build_console_router(
            store=console,
            agent_factory=make_agent,
            channel_runtime=runtime,
        )
    )
    return Stack(
        root=root,
        console=console,
        channels=channels,
        secrets=secrets,
        media=media,
        runtime=runtime,
        telegram=telegram,
        http_client=http_client,
        app=app,
        agents=agents,
    )


def _wait_until(predicate: Callable[[], Any], timeout: float = 6.0) -> Any:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.01)
    return predicate()


def _wait_events(
    store: ConsoleStore,
    sid: str,
    predicate: Callable[[list[dict[str, Any]]], bool],
    timeout: float = 6.0,
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []

    def ready() -> bool:
        nonlocal events
        events = store.read_events(sid)
        return predicate(events)

    assert _wait_until(ready, timeout), json.dumps(events, ensure_ascii=False)
    return events


def _remember(response: httpx.Response, surfaces: list[str]) -> httpx.Response:
    surfaces.append(response.text)
    return response


def _latest_confirm(
    store: ConsoleStore, sid: str, *, tool: str
) -> dict[str, Any]:
    events = _wait_events(
        store,
        sid,
        lambda rows: any(
            row.get("type") == "confirm_request" and row.get("tool") == tool
            for row in rows
        ),
    )
    return [
        row
        for row in events
        if row.get("type") == "confirm_request" and row.get("tool") == tool
    ][-1]


def _sqlite_surface(store: ChannelStore) -> str:
    rendered: list[str] = []
    with store.connect() as connection:
        tables = [
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            ).fetchall()
            if isinstance(row[0], str) and not str(row[0]).startswith("sqlite_")
        ]
        for table in tables:
            safe_table = table.replace('"', '""')
            for row in connection.execute(f'SELECT * FROM "{safe_table}"').fetchall():
                rendered.extend(
                    value.hex() if isinstance(value, bytes) else str(value)
                    for value in tuple(row)
                    if value is not None
                )
    return "\n".join(rendered)


def _all_file_bytes(root: Path) -> list[tuple[Path, bytes]]:
    result: list[tuple[Path, bytes]] = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        try:
            result.append((path, path.read_bytes()))
        except OSError:  # transient SQLite shared-memory handles are not evidence files
            continue
    return result


def _create_session(client: TestClient, surfaces: list[str], title: str) -> str:
    response = _remember(
        client.post("/console/sessions", json={"title": title}), surfaces
    )
    assert response.status_code == 200 and response.json()["ok"] is True
    return str(response.json()["meta"]["id"])


def _wait_turn_done(store: ConsoleStore, sid: str, turn_id: str) -> list[dict[str, Any]]:
    return _wait_events(
        store,
        sid,
        lambda rows: any(
            row.get("type") == "task_update"
            and row.get("task_id") == turn_id
            and row.get("status") == "done"
            for row in rows
        ),
    )


def test_full_readonly_flow_security_and_restart(tmp_path, caplog):
    caplog.set_level(logging.DEBUG)
    telegram = TelegramMock()
    ticket_time = [100.0]
    api_surfaces: list[str] = []
    first = _build_stack(tmp_path, telegram, ticket_clock=lambda: ticket_time[0])

    with TestClient(first.app) as client:
        sid = _create_session(client, api_surfaces, "Telegram E2E")
        other_sid = _create_session(client, api_surfaces, "Ticket scope")

        # 1. The Agent, not a browser-supplied bot/session id, creates the draft.
        connect = _remember(
            client.post(
                "/console/send",
                json={"sid": sid, "text": "连接 Telegram", "thinking_mode": "standard"},
            ),
            api_surfaces,
        )
        assert connect.status_code == 200 and connect.json()["ok"] is True
        _wait_turn_done(first.console, sid, connect.json()["turn_id"])
        draft = first.channels.current_draft(sid)
        assert draft is not None and draft.display_name == "晨报研究 Bot"

        # 2/3. The dedicated secure card endpoint consumes a one-use ticket,
        # verifies getMe and stores only encrypted credential bytes.
        ticket_response = _remember(
            client.post(
                f"/console/channels/session/{sid}/request-token",
                json={
                    "action": "telegram:submit-token",
                    "subject_id": draft.id,
                },
            ),
            api_surfaces,
        )
        assert ticket_response.status_code == 200
        secret_response = _remember(
            client.post(
                "/console/channels/telegram/token",
                json={
                    "sid": sid,
                    "draft_id": draft.id,
                    "token": RAW_TOKEN,
                    "request_token": ticket_response.json()["request_token"],
                },
            ),
            api_surfaces,
        )
        assert secret_response.status_code == 200
        assert secret_response.json()["secret_submitted"] is True
        installed = first.channels.bot(draft.id)
        assert installed.telegram_bot_id == BOT_ID
        assert installed.telegram_username == BOT_USERNAME
        assert installed.credential_id is not None
        secret_files = list((first.console.root / "channel_secrets").glob("*"))
        assert len(secret_files) == 1 and secret_files[0].suffix == ".bin"
        assert RAW_TOKEN.encode() not in secret_files[0].read_bytes()

        # A real private update generates a one-hour code but cannot invoke the
        # Agent.  Meanwhile, pre-binding conversation history remains local.
        telegram.queue_private_message(1, text="pair please")
        assert _wait_until(telegram.pairing_code)
        pairing_code = telegram.pairing_code()
        assert pairing_code is not None
        prehistory = _remember(
            client.post(
                "/console/send",
                json={"sid": sid, "text": "绑定前历史", "thinking_mode": "standard"},
            ),
            api_surfaces,
        )
        assert prehistory.status_code == 200
        _wait_turn_done(first.console, sid, prehistory.json()["turn_id"])
        assert first.channels.live_bindings() == []
        assert not any(
            call.get("text") in {"绑定前历史", "这是绑定前的 Agent 历史答复。"}
            for call in telegram.calls
        )

        # 4/5. Pairing is driven by the exact user-supplied code and a
        # sid+tool+turn+confirm bound ticket.  Wrong scope, expiration and
        # replay all fail before the permanent one-to-one commit.
        pair_turn = _remember(
            client.post(
                "/console/send",
                json={
                    "sid": sid,
                    "text": f"配对码 {pairing_code}",
                    "thinking_mode": "standard",
                },
            ),
            api_surfaces,
        )
        assert pair_turn.status_code == 200
        confirmation = _latest_confirm(
            first.console, sid, tool="ww_telegram_pair_approve"
        )
        assert pairing_code not in json.dumps(confirmation, ensure_ascii=False)
        subject = f"{confirmation['turn_id']}:{confirmation['confirm_id']}"
        confirm_body = {
            "turn_id": confirmation["turn_id"],
            "confirm_id": confirmation["confirm_id"],
            "choice": "y",
        }
        wrong_tickets = (
            first.runtime.issue_action_ticket(
                other_sid, "confirm:ww_telegram_pair_approve", subject
            ),
            first.runtime.issue_action_ticket(
                sid, "confirm:ww_telegram_disconnect", subject
            ),
            first.runtime.issue_action_ticket(
                sid,
                "confirm:ww_telegram_pair_approve",
                "turn_wrong:confirm_wrong",
            ),
        )
        for wrong_ticket in wrong_tickets:
            denied = _remember(
                client.post(
                    "/console/confirm",
                    json={**confirm_body, "request_token": wrong_ticket},
                ),
                api_surfaces,
            )
            assert denied.status_code == 403
            assert denied.json()["reason_code"] == "channel_confirm_ticket_required"

        expiring = _remember(
            client.post(
                "/console/confirm/request-token",
                json={
                    "turn_id": confirmation["turn_id"],
                    "confirm_id": confirmation["confirm_id"],
                },
            ),
            api_surfaces,
        )
        assert expiring.status_code == 200
        ticket_time[0] += 121
        expired = _remember(
            client.post(
                "/console/confirm",
                json={
                    **confirm_body,
                    "request_token": expiring.json()["request_token"],
                },
            ),
            api_surfaces,
        )
        assert expired.status_code == 403

        valid_ticket = _remember(
            client.post(
                "/console/confirm/request-token",
                json={
                    "turn_id": confirmation["turn_id"],
                    "confirm_id": confirmation["confirm_id"],
                },
            ),
            api_surfaces,
        )
        accepted = _remember(
            client.post(
                "/console/confirm",
                json={
                    **confirm_body,
                    "request_token": valid_ticket.json()["request_token"],
                },
            ),
            api_surfaces,
        )
        assert accepted.status_code == 200 and accepted.json() == {"ok": True}
        _wait_turn_done(first.console, sid, pair_turn.json()["turn_id"])
        replay = _remember(
            client.post(
                "/console/confirm",
                json={
                    **confirm_body,
                    "request_token": valid_ticket.json()["request_token"],
                },
            ),
            api_surfaces,
        )
        assert replay.json().get("ok") is not True

        bindings = first.channels.live_bindings()
        assert len(bindings) == 1
        binding = bindings[0]
        assert binding.session_id == sid
        assert binding.bot_account_id == draft.id
        assert binding.telegram_user_id == PRIVATE_USER_ID
        assert binding.telegram_chat_id == PRIVATE_USER_ID
        assert binding.status is BindingStatus.ACTIVE
        assert first.channels.bot(binding.bot_account_id).status is BotStatus.ACTIVE

        status = _remember(
            client.get(f"/console/channels/session/{sid}"), api_surfaces
        )
        assert status.status_code == 200
        assert status.json()["view_state"] == "active"
        assert status.json()["bot"] == {
            "display_name": "晨报研究 Bot",
            "username": BOT_USERNAME,
        }
        assert status.json()["recipient"]["user_id"] == PRIVATE_USER_ID
        assert pairing_code not in status.text

        # 6/7/8. After binding, only the committed user and final text are
        # delivered, in order.  Agent deltas, ordinary tool protocol, error
        # progress and confirmation records merely advance the cursor.
        normal_user = "盘中方向请给最终判断"
        normal_turn = _remember(
            client.post(
                "/console/send",
                json={"sid": sid, "text": normal_user, "thinking_mode": "standard"},
            ),
            api_surfaces,
        )
        assert normal_turn.status_code == 200
        normal_events = _wait_turn_done(
            first.console, sid, normal_turn.json()["turn_id"]
        )
        expected_final = "Agent 最终\n判断：保持观察。"
        assert any(
            row.get("type") == "agent_final" and row.get("text") == expected_final
            for row in normal_events
        )
        assert _wait_until(lambda: len(telegram.delivery_calls()) >= 2)
        delivered = telegram.delivery_calls()
        assert [item["method"] for item in delivered[:2]] == [
            "sendMessage",
            "sendMessage",
        ]
        assert [item["text"] for item in delivered[:2]] == [
            normal_user,
            expected_final,
        ]
        forbidden_projection_text = (
            "仅供流式进度",
            "工具过程，不应发送",
            "仅供错误进度",
            "连接草稿已经创建",
            "绑定前历史",
        )
        assert all(
            all(piece not in str(item.get("text") or "") for piece in forbidden_projection_text)
            for item in delivered
        )

        # 9. The Agent publishes one session-owned opaque media_id.  Telegram
        # receives immutable image bytes and a safe filename, never a path.
        workspace = first.console.workspace_store.require(GENERAL_WORKSPACE_ID)
        source_path = Path(workspace["root_path"]) / "readonly-e2e-chart.png"
        source_path.write_bytes(PNG)
        media_meta = first.media.publish_workspace_image(
            GENERAL_WORKSPACE_ID,
            source_path.name,
            session_id=sid,
            alt="压力与支撑",
        )
        assert MEDIA_ID_RE.fullmatch(media_meta["id"])
        agent = first.agents[sid]
        assert isinstance(agent, ScriptedAgent)
        agent.media_id = media_meta["id"]

        media_user = "把刚才图片发到 Telegram"
        media_turn = _remember(
            client.post(
                "/console/send",
                json={"sid": sid, "text": media_user, "thinking_mode": "standard"},
            ),
            api_surfaces,
        )
        media_confirm = _latest_confirm(
            first.console, sid, tool="ww_telegram_publish"
        )
        media_ticket = _remember(
            client.post(
                "/console/confirm/request-token",
                json={
                    "turn_id": media_confirm["turn_id"],
                    "confirm_id": media_confirm["confirm_id"],
                },
            ),
            api_surfaces,
        )
        media_accepted = _remember(
            client.post(
                "/console/confirm",
                json={
                    "turn_id": media_confirm["turn_id"],
                    "confirm_id": media_confirm["confirm_id"],
                    "choice": "y",
                    "request_token": media_ticket.json()["request_token"],
                },
            ),
            api_surfaces,
        )
        assert media_accepted.status_code == 200
        _wait_turn_done(first.console, sid, media_turn.json()["turn_id"])
        assert _wait_until(
            lambda: any(item.get("method") == "sendPhoto" for item in telegram.calls)
        )
        photo = next(item for item in telegram.calls if item.get("method") == "sendPhoto")
        assert photo == {
            "method": "sendPhoto",
            "chat_id": PRIVATE_USER_ID,
            "caption": "压力与支撑图",
            "filename": "image.png",
            "has_png": True,
        }

        # 10. A post-binding Telegram message only advances the durable update
        # offset.  It creates no Console event and invokes no Agent turn.
        before_event_count = len(first.console.read_events(sid))
        before_agent_calls = len(agent.calls)
        telegram.queue_private_message(2, text="请立刻买入并调用 Agent")
        assert _wait_until(
            lambda: first.channels.bot(binding.bot_account_id).update_offset >= 3
        )
        assert len(first.console.read_events(sid)) == before_event_count
        assert len(agent.calls) == before_agent_calls
        assert len(first.channels.pairing_requests_for(binding.bot_account_id)) == 1

        # Persist one confirmed future automation without letting it fire.
        automation_result = first.runtime.automation_upsert(
            sid,
            {
                "name": "未来晨报",
                "schedule_kind": "once",
                "run_at": "2099-08-24T08:30:00+08:00",
                "timezone": "Asia/Hong_Kong",
                "prompt_text": "生成已经确认的数据晨报",
                "skill_refs": ["morning_brief_v1"],
                "media_policy": "none",
            },
        )
        automation_id = automation_result["automation_id"]

        # Wait until every ordinary delivery and media item is durable/sent.
        assert _wait_until(
            lambda: (
                len(first.channels.outbox_for(binding.id)) >= 4
                and all(
                    item.state is OutboxState.SENT
                    for item in first.channels.outbox_for(binding.id)
                )
            )
        )
        before_restart_calls = len(telegram.delivery_calls())
        before_restart_binding = first.channels.binding(binding.id)
        before_restart_outbox = first.channels.outbox_for(binding.id)
        before_restart_offset = first.channels.bot(binding.bot_account_id).update_offset

        # Security surface assertions while the local API is still live.
        final_status = _remember(
            client.get(f"/console/channels/session/{sid}"), api_surfaces
        )
        assert final_status.status_code == 200
        assert final_status.json()["counts"] == {
            "pending": 0,
            "retry_wait": 0,
            "uncertain": 0,
            "terminal": 0,
        }

    # 12. A fresh graph over the same files proves recovery of the binding,
    # cursor, Outbox, inbound offset and automation without replaying history.
    restarted = _build_stack(
        tmp_path,
        telegram,
        ticket_clock=lambda: ticket_time[0],
        agent_factory=lambda _sid: IdleAgent(),
    )
    with TestClient(restarted.app) as client:
        assert _wait_until(
            lambda: restarted.channels.bot(binding.bot_account_id).last_verified_at
            is not None
        )
        time.sleep(0.08)
        rebound = restarted.channels.binding(binding.id)
        assert rebound.status is BindingStatus.ACTIVE
        assert rebound.projection_cursor_event_id == (
            before_restart_binding.projection_cursor_event_id
        )
        assert restarted.channels.bot(binding.bot_account_id).update_offset == (
            before_restart_offset
        )
        recovered_outbox = restarted.channels.outbox_for(binding.id)
        assert [item.id for item in recovered_outbox] == [
            item.id for item in before_restart_outbox
        ]
        assert all(item.state is OutboxState.SENT for item in recovered_outbox)
        assert len(telegram.delivery_calls()) == before_restart_calls
        recovered_automations = restarted.runtime.automation_list(sid)["automations"]
        assert [item["automation_id"] for item in recovered_automations] == [
            automation_id
        ]
        restart_status = _remember(
            client.get(f"/console/channels/session/{sid}"), api_surfaces
        )
        assert restart_status.status_code == 200
        assert restart_status.json()["view_state"] == "active"
        assert restart_status.json()["automations"][0]["automation_id"] == automation_id

    events = restarted.console.read_events(sid)
    buddy_state = restarted.console.load_buddy_state(sid)
    sqlite_surface = _sqlite_surface(restarted.channels)
    outbox_surface = json.dumps(
        [item.__dict__ for item in restarted.channels.outbox_for(binding.id)],
        ensure_ascii=False,
        default=str,
    )
    api_surface = "\n".join(api_surfaces)
    request_body_surface = b"\n".join(telegram.request_bodies)

    # Raw Token: zero in events, Buddy, all SQLite/outbox text, API responses,
    # logs and every ordinary/ciphertext file created by the test.
    assert RAW_TOKEN not in json.dumps(events, ensure_ascii=False)
    assert RAW_TOKEN not in json.dumps(buddy_state, ensure_ascii=False, default=str)
    assert RAW_TOKEN not in sqlite_surface
    assert RAW_TOKEN not in outbox_surface
    assert RAW_TOKEN not in api_surface
    assert RAW_TOKEN not in caplog.text
    assert RAW_TOKEN.encode() not in request_body_surface
    for path, data in _all_file_bytes(tmp_path):
        assert RAW_TOKEN.encode() not in data, path

    # Pairing code: only the originating user_msg (and the private Agent/Buddy
    # protocol if a concrete Agent stores it) may retain it on the Console side.
    code_events = [
        row
        for row in events
        if pairing_code in json.dumps(row, ensure_ascii=False)
    ]
    assert len(code_events) == 1
    assert code_events[0]["type"] == "user_msg"
    assert code_events[0]["channel_projection"] == "never"
    assert pairing_code not in sqlite_surface
    assert pairing_code not in outbox_surface
    assert pairing_code not in api_surface
    assert pairing_code not in caplog.text
    assert all(
        pairing_code not in json.dumps(row, ensure_ascii=False)
        for row in events
        if row.get("type") in {"confirm_request", "channel_audit"}
    )

    # Local media capability: no absolute path crosses Channel SQLite, a
    # status/API DTO, Telegram request bytes or logs.
    rendered_source = str(source_path)
    assert rendered_source not in sqlite_surface
    assert rendered_source not in api_surface
    assert rendered_source not in caplog.text
    assert rendered_source.encode() not in request_body_surface


@pytest.mark.parametrize(
    ("effect", "expected_state", "expected_error", "expected_bot_status"),
    [
        ("429", OutboxState.RETRY_WAIT, "retryable", BotStatus.ACTIVE),
        ("401", OutboxState.TERMINAL, "credential_invalid", BotStatus.CREDENTIAL_INVALID),
        ("403", OutboxState.TERMINAL, "target_unavailable", BotStatus.ACTIVE),
        ("500", OutboxState.RETRY_WAIT, "retryable", BotStatus.ACTIVE),
        ("connect_timeout", OutboxState.RETRY_WAIT, "retryable", BotStatus.ACTIVE),
        ("read_timeout", OutboxState.UNCERTAIN, "uncertain", BotStatus.ACTIVE),
    ],
)
def test_provider_failures_reach_durable_safe_states(
    tmp_path,
    caplog,
    effect,
    expected_state,
    expected_error,
    expected_bot_status,
):
    """429/401/403/5xx/timeout never collapse into blind success/retry."""

    caplog.set_level(logging.DEBUG)
    telegram = TelegramMock()
    stack = _build_stack(tmp_path, telegram, agent_factory=lambda _sid: IdleAgent())
    sid = stack.console.create_session(title=f"failure {effect}")["id"]
    credential_id = stack.secrets.put(RAW_TOKEN)
    bot_id = stack.channels.create_bot("Failure Bot", draft_session_id=sid)
    stack.channels.cas_bot_credential(
        bot_id,
        expected_revision=0,
        credential_id=credential_id,
        telegram_bot_id=BOT_ID,
        telegram_username=BOT_USERNAME,
    )
    binding = stack.channels.bind_for_test(
        sid,
        bot_id,
        user_id=PRIVATE_USER_ID,
        chat_id=PRIVATE_USER_ID,
        sync_from_event_id=0,
    )

    api_surface: list[str] = []
    with TestClient(stack.app) as client:
        telegram.send_effect = effect
        stack.console.append_event(sid, "user_msg", text=f"fault-{effect}")

        def terminal_state():
            items = stack.channels.outbox_for(binding.id)
            return (
                items
                if items
                and items[0].state is expected_state
                and stack.channels.bot(bot_id).status is expected_bot_status
                else None
            )

        items = _wait_until(terminal_state)
        assert items
        item = items[0]
        assert item.state is expected_state
        assert item.last_error_class == expected_error
        assert stack.channels.bot(bot_id).status is expected_bot_status
        status = _remember(
            client.get(f"/console/channels/session/{sid}"), api_surface
        )
        assert status.status_code == 200
        count_key = {
            OutboxState.RETRY_WAIT: "retry_wait",
            OutboxState.UNCERTAIN: "uncertain",
            OutboxState.TERMINAL: "terminal",
        }[expected_state]
        assert status.json()["counts"][count_key] == 1
        assert RAW_TOKEN not in status.text

    sqlite_surface = _sqlite_surface(stack.channels)
    assert RAW_TOKEN not in sqlite_surface
    assert RAW_TOKEN not in "\n".join(api_surface)
    assert RAW_TOKEN not in caplog.text
    assert RAW_TOKEN.encode() not in b"\n".join(telegram.request_bodies)
    assert RAW_TOKEN.encode() not in (
        stack.console.root / "channel_secrets" / f"{credential_id}.bin"
    ).read_bytes()


def test_channel_database_contains_no_plaintext_in_any_text_column(tmp_path):
    """Independent whole-schema scanner catches future accidental columns."""

    telegram = TelegramMock()
    stack = _build_stack(tmp_path, telegram, agent_factory=lambda _sid: IdleAgent())
    sid = stack.console.create_session(title="schema scanner")["id"]
    credential_id = stack.secrets.put(RAW_TOKEN)
    bot_id = stack.channels.create_bot("Scanner Bot", draft_session_id=sid)
    stack.channels.cas_bot_credential(
        bot_id,
        expected_revision=0,
        credential_id=credential_id,
        telegram_bot_id=BOT_ID,
    )

    surface = _sqlite_surface(stack.channels)
    assert RAW_TOKEN not in surface
    assert "cred_" in surface

    # Also scan raw database/WAL bytes: SQLite free pages must not preserve a
    # value that was never allowed through the registry boundary.
    for path in stack.console.root.glob("channels.sqlite3*"):
        if path.is_file():
            assert RAW_TOKEN.encode() not in path.read_bytes()

    with stack.channels.connect() as connection:
        text_columns = []
        for table_row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall():
            table = str(table_row[0])
            if table.startswith("sqlite_"):
                continue
            quoted = table.replace('"', '""')
            for column in connection.execute(f'PRAGMA table_info("{quoted}")').fetchall():
                declared = str(column[2] or "").upper()
                if "TEXT" in declared:
                    text_columns.append((table, str(column[1])))
        assert text_columns, "scanner must cover the actual durable text surface"
