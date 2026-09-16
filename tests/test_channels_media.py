from __future__ import annotations

import asyncio
import base64
import sqlite3
import threading
from dataclasses import dataclass

import httpx
import pytest

from guanlan_v2.channels.agent_tools import (
    validated_telegram_document_publish_args,
    validated_telegram_publish_args,
)
from guanlan_v2.channels.models import OutboundPart, OutboxState
from guanlan_v2.channels.media_policy import caption_contains_location, caption_sha256
from guanlan_v2.channels.outbox import OutboxWorker
from guanlan_v2.channels.projector import (
    MAX_TELEGRAM_PHOTO_CAPTION,
    ConsoleMessageProjector,
)
from guanlan_v2.channels.store import ChannelConflict, ChannelStore
from guanlan_v2.channels.telegram import (
    TelegramAdapter,
    TelegramRetryable,
    TelegramSendResult,
)
from guanlan_v2.console.media import MediaNotFound, MediaStore
from guanlan_v2.console.store import ConsoleStore


CREDENTIAL_ID = "cred_" + "a" * 32
TOKEN = "123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZ_abcdefghi"
MEDIA_ID = "mi_" + "b" * 32
DOCUMENT_ID = "do_" + "d" * 32
PNG = b"\x89PNG\r\n\x1a\nvalidated-image"
DOCUMENT = b"symbol,alpha,beta\n600667,0.10,1.20\n"
VALID_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _seed(tmp_path):
    console = ConsoleStore(root=tmp_path / "console")
    sid = console.create_session(title="图片镜像")["id"]
    channels = ChannelStore(tmp_path / "channels.sqlite3")
    bot_id = channels.create_bot("图片 Bot", draft_session_id=sid)
    channels.cas_bot_credential(
        bot_id,
        expected_revision=0,
        credential_id=CREDENTIAL_ID,
        telegram_bot_id="1001",
    )
    binding = channels.bind_for_test(
        sid,
        bot_id,
        user_id="7",
        chat_id="7",
        sync_from_event_id=0,
    )
    return console, channels, binding


def _artifact(
    *,
    media_id: str = MEDIA_ID,
    caption: str = "压力与支撑",
    permit_id: str = "pub_" + "c" * 32,
):
    return {
        "kind": "telegram_media_publish",
        "page": None,
        "channel": "telegram",
        "payload": {"media_id": media_id, "caption": caption},
        "ref": permit_id,
    }


def _document_artifact(
    *,
    document_id: str = DOCUMENT_ID,
    caption: str = "Alpha Beta 数据表",
    permit_id: str = "pub_" + "e" * 32,
):
    return {
        "kind": "telegram_document_publish",
        "page": None,
        "channel": "telegram",
        "payload": {"document_id": document_id, "caption": caption},
        "ref": permit_id,
    }


def _permit(channels, binding, *, media_id: str = MEDIA_ID, caption: str = "压力与支撑"):
    return channels.create_media_publish_permit(
        binding.session_id,
        media_id,
        caption_sha256(caption),
        expected_binding_id=binding.id,
        expected_paired_at=binding.paired_at,
    )


def _authorized_artifact(
    channels,
    binding,
    *,
    media_id: str = MEDIA_ID,
    caption: str = "压力与支撑",
):
    return _artifact(
        media_id=media_id,
        caption=caption,
        permit_id=_permit(channels, binding, media_id=media_id, caption=caption),
    )


def _authorized_document_artifact(
    channels,
    binding,
    *,
    document_id: str = DOCUMENT_ID,
    caption: str = "Alpha Beta 数据表",
):
    return _document_artifact(
        document_id=document_id,
        caption=caption,
        permit_id=_permit(
            channels, binding, media_id=document_id, caption=caption,
        ),
    )


def _append_publish(console, sid: str, *, caption: str = "压力与支撑", **fields):
    return console.append_event(
        sid,
        "tool_result",
        tool=fields.pop("tool", "ww_telegram_publish"),
        ok=fields.pop("ok", True),
        artifact=fields.pop("artifact", _artifact(caption=caption)),
        **fields,
    )


def _append_document_publish(
    console, sid: str, *, caption: str = "Alpha Beta 数据表", **fields,
):
    return console.append_event(
        sid,
        "tool_result",
        tool=fields.pop("tool", "ww_telegram_publish_document"),
        ok=fields.pop("ok", True),
        artifact=fields.pop("artifact", _document_artifact(caption=caption)),
        **fields,
    )


class Secrets:
    def get(self, credential_id: str):
        assert credential_id == CREDENTIAL_ID
        return TOKEN


class Media:
    def __init__(self, *, error: BaseException | None = None):
        self.error = error
        self.calls: list[tuple[str, str]] = []

    def load_for_session(self, media_id: str, session_id: str):
        self.calls.append((media_id, session_id))
        if self.error is not None:
            raise self.error
        return ({"schema_version": 1, "id": media_id, "mime": "image/png"}, PNG)


class Attachments:
    def __init__(self):
        self.calls: list[tuple[str, str]] = []

    def load_attachment_for_session(self, attachment_id: str, session_id: str):
        self.calls.append((attachment_id, session_id))
        if attachment_id == MEDIA_ID:
            return (
                {"schema_version": 1, "id": attachment_id, "mime": "image/png"},
                PNG,
            )
        if attachment_id == DOCUMENT_ID:
            return (
                {
                    "schema_version": 2,
                    "kind": "document",
                    "id": attachment_id,
                    "name": "alpha-beta.csv",
                    "mime": "text/csv",
                },
                DOCUMENT,
            )
        raise LookupError("foreign attachment")


@dataclass
class Adapter:
    photo_outcome: object = TelegramSendResult("12")
    document_outcome: object = TelegramSendResult("13")

    def __post_init__(self):
        self.calls: list[tuple[str, dict]] = []

    async def send_message(self, _token: str, *, chat_id: str, text: str):
        self.calls.append(("text", {"chat_id": chat_id, "text": text}))
        return TelegramSendResult("11")

    async def send_photo(self, _token: str, **kwargs):
        self.calls.append(("photo", kwargs))
        if isinstance(self.photo_outcome, BaseException):
            raise self.photo_outcome
        return self.photo_outcome

    async def send_document(self, _token: str, **kwargs):
        self.calls.append(("document", kwargs))
        if isinstance(self.document_outcome, BaseException):
            raise self.document_outcome
        return self.document_outcome


def test_projector_only_accepts_exact_successful_publish_artifact(tmp_path):
    console, channels, binding = _seed(tmp_path)
    # A conversation image, a failed publish, another tool, and malformed
    # envelopes must all advance the cursor without creating delivery work.
    ordinary = _artifact()
    ordinary["kind"] = "media_image"
    _append_publish(console, binding.session_id, artifact=ordinary)
    _append_publish(console, binding.session_id, ok=False)
    _append_publish(console, binding.session_id, tool="ww_workspace_publish_image")
    malformed = _artifact()
    malformed["unexpected"] = "fail closed"
    _append_publish(console, binding.session_id, artifact=malformed)
    _append_publish(
        console,
        binding.session_id,
        artifact=_authorized_artifact(channels, binding),
    )

    assert ConsoleMessageProjector(console, channels).project_once(binding.id) == 5
    items = channels.outbox_for(binding.id)
    assert len(items) == 1
    item = items[0]
    assert (item.kind, item.media_id, item.caption) == (
        "media",
        MEDIA_ID,
        "压力与支撑",
    )
    assert item.media_authorization_version == 1
    assert item.text is None
    # The durable payload contains an opaque id/caption, never local bytes,
    # source paths, or a provider credential.
    rendered = repr(item)
    assert PNG.hex() not in rendered
    assert "C:\\" not in rendered and TOKEN not in rendered


def test_document_projector_requires_exact_tool_envelope_and_durable_permit(
    tmp_path,
):
    console, channels, binding = _seed(tmp_path)
    caption = "Alpha Beta 数据表"
    assert validated_telegram_document_publish_args({
        "document_id": DOCUMENT_ID,
        "caption": caption,
    }) == (DOCUMENT_ID, caption)

    # A conversation file card and a hand-written Telegram-shaped envelope are
    # both data, not authority.  Even a valid permit cannot be consumed by the
    # image tool name; only the exact document capability may project it.
    ordinary = _document_artifact(caption=caption)
    ordinary["kind"] = "file_document"
    ordinary["channel"] = "conversation"
    _append_document_publish(
        console,
        binding.session_id,
        tool="ww_workspace_publish_document",
        artifact=ordinary,
    )
    _append_document_publish(console, binding.session_id, caption=caption)
    authorized = _authorized_document_artifact(
        channels, binding, caption=caption,
    )
    _append_document_publish(
        console,
        binding.session_id,
        tool="ww_telegram_publish",
        artifact=authorized,
    )
    _append_document_publish(
        console, binding.session_id, artifact=authorized,
    )

    assert ConsoleMessageProjector(console, channels).project_once(binding.id) == 4
    items = channels.outbox_for(binding.id)
    assert len(items) == 1
    assert (items[0].kind, items[0].media_id, items[0].caption) == (
        "media",
        DOCUMENT_ID,
        caption,
    )
    assert items[0].media_authorization_version == 1
    with channels.connect() as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM media_publish_permits WHERE permit_id = ?",
            (authorized["ref"],),
        ).fetchone()[0] == 0


def test_document_permit_cannot_cross_session_and_wrong_attempt_burns_it(tmp_path):
    console, channels, owner_binding = _seed(tmp_path)
    foreign_sid = console.create_session(title="另一对话")["id"]
    foreign_bot = channels.create_bot("另一 Bot", draft_session_id=foreign_sid)
    channels.cas_bot_credential(
        foreign_bot,
        expected_revision=0,
        credential_id="cred_" + "f" * 32,
        telegram_bot_id="1002",
    )
    foreign_binding = channels.bind_for_test(
        foreign_sid,
        foreign_bot,
        user_id="8",
        chat_id="8",
        sync_from_event_id=0,
    )
    artifact = _authorized_document_artifact(channels, owner_binding)
    _append_document_publish(console, foreign_sid, artifact=artifact)

    assert ConsoleMessageProjector(console, channels).project_once(
        foreign_binding.id
    ) == 1
    assert channels.outbox_for(foreign_binding.id) == []
    with channels.connect() as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM media_publish_permits WHERE permit_id = ?",
            (artifact["ref"],),
        ).fetchone()[0] == 0


@pytest.mark.parametrize(
    "mutator",
    [
        lambda artifact: artifact["payload"].update(media_id="/tmp/chart.png"),
        lambda artifact: artifact["payload"].update(caption=None),
        lambda artifact: artifact.update(ref="mi_" + "c" * 32),
        lambda artifact: artifact.update(channel="conversation"),
        lambda artifact: artifact["payload"].update(extra="not allowed"),
    ],
)
def test_malformed_or_path_bearing_media_envelope_never_projects(tmp_path, mutator):
    console, channels, binding = _seed(tmp_path)
    artifact = _artifact()
    mutator(artifact)
    _append_publish(console, binding.session_id, artifact=artifact)

    assert ConsoleMessageProjector(console, channels).project_once(binding.id) == 1
    assert channels.outbox_for(binding.id) == []


def test_exact_hand_written_envelope_without_durable_permit_is_not_authority(tmp_path):
    console, channels, binding = _seed(tmp_path)
    _append_publish(console, binding.session_id, artifact=_artifact())

    assert ConsoleMessageProjector(console, channels).project_once(binding.id) == 1
    assert channels.outbox_for(binding.id) == []
    assert channels.binding(binding.id).projection_cursor_event_id == 1


def test_permit_survives_restart_is_consumed_atomically_and_cannot_replay(tmp_path):
    console, channels, binding = _seed(tmp_path)
    artifact = _authorized_artifact(channels, binding)
    _append_publish(console, binding.session_id, artifact=artifact)
    _append_publish(console, binding.session_id, artifact=artifact)

    # Re-opening the ChannelStore proves that authorization is durable rather
    # than an in-memory Runtime side effect.
    reopened = ChannelStore(channels.path)
    with reopened.connect() as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM media_publish_permits WHERE permit_id = ?",
            (artifact["ref"],),
        ).fetchone()[0] == 1
    assert ConsoleMessageProjector(console, reopened).project_once(binding.id) == 2
    items = reopened.outbox_for(binding.id)
    assert len(items) == 1 and items[0].media_id == MEDIA_ID
    assert reopened.binding(binding.id).projection_cursor_event_id == 2
    with reopened.connect() as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM media_publish_permits WHERE permit_id = ?",
            (artifact["ref"],),
        ).fetchone()[0] == 0


@pytest.mark.parametrize("failure", ["expired", "digest", "generation"])
def test_bad_permit_advances_cursor_without_media_outbox(tmp_path, failure):
    console, channels, binding = _seed(tmp_path)
    permit_id = _permit(channels, binding, caption="原说明")
    caption = "原说明"
    if failure == "expired":
        with channels.connect() as conn:
            conn.execute(
                "UPDATE media_publish_permits SET expires_at = ? WHERE permit_id = ?",
                ("2000-01-01T00:00:00.000000+00:00", permit_id),
            )
    elif failure == "digest":
        caption = "被替换的说明"
    else:
        with channels.connect() as conn:
            conn.execute(
                "UPDATE channel_bindings SET paired_at = ? WHERE id = ?",
                ("2030-01-01T00:00:00.000000+00:00", binding.id),
            )
    _append_publish(
        console,
        binding.session_id,
        artifact=_artifact(caption=caption, permit_id=permit_id),
    )

    assert ConsoleMessageProjector(console, channels).project_once(binding.id) == 1
    assert channels.outbox_for(binding.id) == []
    assert channels.binding(binding.id).projection_cursor_event_id == 1
    with channels.connect() as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM media_publish_permits WHERE permit_id = ?",
            (permit_id,),
        ).fetchone()[0] == 0


def test_permit_table_contains_digest_only_not_caption_path_or_secret(tmp_path):
    _console, channels, binding = _seed(tmp_path)
    caption = "普通中文分析 20/60/120"
    permit_id = _permit(channels, binding, caption=caption)
    with channels.connect() as conn:
        columns = {
            row["name"]
            for row in conn.execute(
                "PRAGMA table_info(media_publish_permits)"
            ).fetchall()
        }
        row = conn.execute(
            "SELECT * FROM media_publish_permits WHERE permit_id = ?",
            (permit_id,),
        ).fetchone()
    assert columns == {
        "permit_id",
        "session_id",
        "binding_id",
        "binding_paired_at",
        "media_id",
        "caption_sha256",
        "created_at",
        "expires_at",
    }
    assert row is not None and row["caption_sha256"] == caption_sha256(caption)
    assert caption not in repr(dict(row))
    assert TOKEN not in repr(dict(row))


def test_permit_creation_cas_rejects_recipient_change_after_confirmation(tmp_path):
    _console, channels, binding = _seed(tmp_path)
    with channels.connect() as conn:
        conn.execute(
            "UPDATE channel_bindings SET paired_at = ? WHERE id = ?",
            ("2030-01-01T00:00:00.000000+00:00", binding.id),
        )
    with pytest.raises(ChannelConflict):
        channels.create_media_publish_permit(
            binding.session_id,
            MEDIA_ID,
            caption_sha256("图"),
            expected_binding_id=binding.id,
            expected_paired_at=binding.paired_at,
        )
    with channels.connect() as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM media_publish_permits"
        ).fetchone()[0] == 0


@pytest.mark.parametrize(
    "caption",
    [
        r"本图来自 C:\x.png",
        r"C:\private\chart.png",
        r"C:/private/chart.png",
        "/tmp/x",
        "/123",
        "file:///private/chart.png",
        "file:C",
        "https://example.invalid/chart.png",
        "https:example",
        "a" * 64 + ":opaque",
        "请看 output/chart.png",
        "请看 reports/secret",
        r"\\server\share\chart.png",
        "//server/share/chart.png",
    ],
)
@pytest.mark.anyio
async def test_caption_resource_locations_never_enter_outbox_or_send(tmp_path, caption):
    console, channels, binding = _seed(tmp_path)
    permit_id = _permit(channels, binding, caption=caption)
    _append_publish(
        console,
        binding.session_id,
        artifact=_artifact(caption=caption, permit_id=permit_id),
    )

    assert ConsoleMessageProjector(console, channels).project_once(binding.id) == 1
    assert channels.outbox_for(binding.id) == []
    adapter = Adapter()
    worker = OutboxWorker(
        channel_store=channels,
        secret_store=Secrets(),
        telegram_adapter=adapter,
        media_store=Media(),
    )
    assert await worker.run_once() == 0
    assert adapter.calls == []

    # The Store independently rejects a direct media part even if a caller
    # bypasses the event projector.
    with pytest.raises(ValueError):
        channels.project_event_for_test(
            binding.id,
            source_event_id=2,
            parts=[
                OutboundPart(
                    kind="media",
                    payload_part=0,
                    media_id=MEDIA_ID,
                    caption=caption,
                )
            ],
            media_permit_id=permit_id,
        )


def test_analytical_caption_is_not_mistaken_for_a_location(tmp_path):
    console, channels, binding = _seed(tmp_path)
    caption = "Alpha:0.3 · Beta:1.2 · 20/60/120\n中文研判：压力与支撑"
    _append_publish(
        console,
        binding.session_id,
        artifact=_authorized_artifact(channels, binding, caption=caption),
    )
    assert ConsoleMessageProjector(console, channels).project_once(binding.id) == 1
    items = channels.outbox_for(binding.id)
    assert len(items) == 1 and items[0].caption == caption


@pytest.mark.anyio
async def test_pairing_shaped_ordinary_caption_reaches_photo_delivery(tmp_path):
    console, channels, binding = _seed(tmp_path)
    caption = "BACKTEST · TELEGRAM · ABCDEFGH · 23456789"
    assert validated_telegram_publish_args(
        {"media_id": MEDIA_ID, "caption": caption}
    ) == (MEDIA_ID, caption)
    _append_publish(
        console,
        binding.session_id,
        artifact=_authorized_artifact(channels, binding, caption=caption),
    )

    assert ConsoleMessageProjector(console, channels).project_once(binding.id) == 1
    items = channels.outbox_for(binding.id)
    assert len(items) == 1 and items[0].caption == caption

    adapter = Adapter()
    worker = OutboxWorker(
        channel_store=channels,
        secret_store=Secrets(),
        telegram_adapter=adapter,
        media_store=Media(),
    )
    assert await worker.run_once() == 1
    assert adapter.calls == [
        (
            "photo",
            {
                "chat_id": binding.telegram_chat_id,
                "data": PNG,
                "filename": "image.png",
                "mime_type": "image/png",
                "caption": caption,
            },
        )
    ]


@pytest.mark.parametrize(
    "caption",
    [
        "Alpha:0.3",
        "Beta:1.2 · 20/60/120",
        "20/60/120",
        "普通中文研判：压力与支撑",
        "压力/支撑与行业/个股分析",
        "2026/08/23 收盘复盘",
    ],
)
def test_caption_location_policy_keeps_ordinary_analysis(caption):
    assert caption_contains_location(caption) is False


@pytest.mark.anyio
async def test_worker_fails_closed_for_legacy_path_caption(tmp_path):
    console, channels, binding = _seed(tmp_path)
    _append_publish(
        console,
        binding.session_id,
        artifact=_authorized_artifact(channels, binding),
    )
    ConsoleMessageProjector(console, channels).project_once(binding.id)
    item = channels.outbox_for(binding.id)[0]
    with channels.connect() as conn:
        conn.execute(
            "UPDATE outbox SET caption = ? WHERE id = ?",
            (r"legacy C:\private\chart.png", item.id),
        )
    adapter = Adapter()
    worker = OutboxWorker(
        channel_store=channels,
        secret_store=Secrets(),
        telegram_adapter=adapter,
        media_store=Media(),
    )

    assert await worker.run_once() == 1
    terminal = channels.outbox(item.id)
    assert terminal.state is OutboxState.TERMINAL
    assert terminal.last_error_class == "invalid_media_delivery"
    assert adapter.calls == []


@pytest.mark.anyio
async def test_upgrade_cancels_route_fenced_media_without_task8_authority(tmp_path):
    console, channels, binding = _seed(tmp_path)
    legacy_caption = "甲" * (MAX_TELEGRAM_PHOTO_CAPTION + 50)
    _append_publish(
        console,
        binding.session_id,
        artifact=_authorized_artifact(
            channels, binding, caption=legacy_caption
        ),
    )
    ConsoleMessageProjector(console, channels).project_once(binding.id)
    original_items = channels.outbox_for(binding.id)
    assert [entry.kind for entry in original_items] == ["text", "media"]
    item = original_items[-1]
    with channels.connect() as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM outbox_route_fences WHERE outbox_id = ?",
            (item.id,),
        ).fetchone()[0] == 1
    # Rebuild only the temp DB's outbox table in the exact Task7 shape.  The
    # recipient route fence remains present, but the Task8 authorization
    # marker column does not yet exist.
    legacy = sqlite3.connect(channels.path, isolation_level=None)
    try:
        legacy.executescript(
            """
            PRAGMA foreign_keys=OFF;
            PRAGMA legacy_alter_table=ON;
            BEGIN IMMEDIATE;
            CREATE TABLE outbox_legacy (
                id TEXT PRIMARY KEY,
                binding_id TEXT NOT NULL REFERENCES channel_bindings(id),
                source_session_id TEXT NOT NULL,
                source_event_id INTEGER NOT NULL CHECK (source_event_id >= 0),
                payload_part INTEGER NOT NULL CHECK (payload_part >= 0),
                batch_id TEXT NOT NULL,
                sequence INTEGER NOT NULL CHECK (sequence > 0),
                kind TEXT NOT NULL CHECK (kind IN ('text', 'media')),
                text TEXT,
                media_id TEXT,
                caption TEXT,
                state TEXT NOT NULL CHECK (state IN (
                    'pending', 'sending', 'sent', 'retry_wait', 'uncertain',
                    'terminal', 'cancelled'
                )),
                attempts INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),
                next_attempt_at TEXT,
                lease_owner TEXT,
                lease_until TEXT,
                telegram_message_id TEXT,
                last_error_class TEXT,
                last_error_at TEXT,
                created_at TEXT NOT NULL,
                sent_at TEXT,
                CHECK (
                    (kind = 'text' AND text IS NOT NULL AND media_id IS NULL
                        AND caption IS NULL)
                    OR (kind = 'media' AND text IS NULL AND media_id IS NOT NULL)
                )
            );
            INSERT INTO outbox_legacy (
                id, binding_id, source_session_id, source_event_id, payload_part,
                batch_id, sequence, kind, text, media_id, caption, state,
                attempts, next_attempt_at, lease_owner, lease_until,
                telegram_message_id, last_error_class, last_error_at, created_at,
                sent_at
            ) SELECT
                id, binding_id, source_session_id, source_event_id, payload_part,
                batch_id, sequence, kind, text, media_id, caption, state,
                attempts, next_attempt_at, lease_owner, lease_until,
                telegram_message_id, last_error_class, last_error_at, created_at,
                sent_at
            FROM outbox;
            DROP TABLE outbox;
            ALTER TABLE outbox_legacy RENAME TO outbox;
            CREATE UNIQUE INDEX uq_outbox_source_part
                ON outbox(binding_id, source_event_id, payload_part);
            CREATE UNIQUE INDEX uq_binding_sequence
                ON outbox(binding_id, sequence);
            CREATE INDEX idx_outbox_delivery
                ON outbox(binding_id, sequence, state, next_attempt_at);
            COMMIT;
            """
        )
    finally:
        legacy.close()

    reopened = ChannelStore(channels.path)
    with reopened.connect() as conn:
        columns = {
            row["name"] for row in conn.execute("PRAGMA table_info(outbox)")
        }
        assert "media_authorization_version" in columns
        assert conn.execute(
            "PRAGMA foreign_key_check"
        ).fetchall() == []
    migrated = reopened.outbox(item.id)
    assert migrated.state is OutboxState.CANCELLED
    assert migrated.last_error_class == "legacy_media_unauthorized"
    assert {
        entry.state for entry in reopened.outbox_for(binding.id)
    } == {OutboxState.CANCELLED}
    adapter = Adapter()
    worker = OutboxWorker(
        channel_store=reopened,
        secret_store=Secrets(),
        telegram_adapter=adapter,
        media_store=Media(),
    )
    assert await worker.run_once() == 0
    assert adapter.calls == []


@pytest.mark.anyio
async def test_worker_requires_durable_media_authorization_marker(tmp_path):
    console, channels, binding = _seed(tmp_path)
    _append_publish(
        console,
        binding.session_id,
        artifact=_authorized_artifact(channels, binding),
    )
    ConsoleMessageProjector(console, channels).project_once(binding.id)
    item = channels.outbox_for(binding.id)[0]
    with channels.connect() as conn:
        conn.execute(
            "UPDATE outbox SET media_authorization_version = NULL WHERE id = ?",
            (item.id,),
        )
    adapter = Adapter()
    worker = OutboxWorker(
        channel_store=channels,
        secret_store=Secrets(),
        telegram_adapter=adapter,
        media_store=Media(),
    )

    assert await worker.run_once() == 1
    rejected = channels.outbox(item.id)
    assert rejected.state is OutboxState.TERMINAL
    assert rejected.last_error_class == "invalid_media_delivery"
    assert adapter.calls == []


def test_long_caption_is_lossless_durable_text_then_short_photo(tmp_path):
    console, channels, binding = _seed(tmp_path)
    caption = ("第一段\n" + "甲" * 4200 + "\n第二段\n" + "乙" * 1200)
    _append_publish(
        console,
        binding.session_id,
        artifact=_authorized_artifact(channels, binding, caption=caption),
    )

    ConsoleMessageProjector(console, channels).project_once(binding.id)
    items = channels.outbox_for(binding.id)
    assert items[-1].kind == "media"
    assert items[-1].media_id == MEDIA_ID
    assert 0 < len(items[-1].caption or "") <= MAX_TELEGRAM_PHOTO_CAPTION
    assert all(item.kind == "text" for item in items[:-1])
    assert all(0 < len(item.text or "") <= 4000 for item in items[:-1])
    assert "".join(item.text or "" for item in items[:-1]) + (
        items[-1].caption or ""
    ) == caption
    assert [item.payload_part for item in items] == list(range(len(items)))
    assert [item.sequence for item in items] == list(range(1, len(items) + 1))


@pytest.mark.anyio
async def test_worker_loads_session_owned_media_and_sends_controlled_multipart(tmp_path):
    console, channels, binding = _seed(tmp_path)
    _append_publish(
        console,
        binding.session_id,
        artifact=_authorized_artifact(channels, binding),
    )
    ConsoleMessageProjector(console, channels).project_once(binding.id)
    media = Media()
    adapter = Adapter()
    worker = OutboxWorker(
        channel_store=channels,
        secret_store=Secrets(),
        telegram_adapter=adapter,
        media_store=media,
    )

    assert await worker.run_once() == 1
    item = channels.outbox_for(binding.id)[0]
    assert item.state is OutboxState.SENT
    assert item.telegram_message_id == "12"
    assert media.calls == [(MEDIA_ID, binding.session_id)]
    assert adapter.calls == [(
        "photo",
        {
            "chat_id": "7",
            "data": PNG,
            "filename": "image.png",
            "mime_type": "image/png",
            "caption": "压力与支撑",
        },
    )]
    assert binding.session_id not in repr(adapter.calls)


@pytest.mark.anyio
async def test_outbox_routes_document_to_send_document_and_keeps_photo_dispatch(
    tmp_path,
):
    console, channels, binding = _seed(tmp_path)
    _append_publish(
        console,
        binding.session_id,
        artifact=_authorized_artifact(channels, binding, caption="位置图"),
    )
    _append_document_publish(
        console,
        binding.session_id,
        artifact=_authorized_document_artifact(
            channels, binding, caption="Alpha Beta 数据表",
        ),
    )
    assert ConsoleMessageProjector(console, channels).project_once(binding.id) == 2

    attachments = Attachments()
    adapter = Adapter()
    worker = OutboxWorker(
        channel_store=channels,
        secret_store=Secrets(),
        telegram_adapter=adapter,
        media_store=attachments,
    )
    assert await worker.run_once() == 1
    assert await worker.run_once() == 1

    items = channels.outbox_for(binding.id)
    assert [item.state for item in items] == [OutboxState.SENT, OutboxState.SENT]
    assert [item.telegram_message_id for item in items] == ["12", "13"]
    assert attachments.calls == [
        (MEDIA_ID, binding.session_id),
        (DOCUMENT_ID, binding.session_id),
    ]
    assert adapter.calls == [
        (
            "photo",
            {
                "chat_id": "7",
                "data": PNG,
                "filename": "image.png",
                "mime_type": "image/png",
                "caption": "位置图",
            },
        ),
        (
            "document",
            {
                "chat_id": "7",
                "data": DOCUMENT,
                "filename": "alpha-beta.csv",
                "mime_type": "text/csv",
                "caption": "Alpha Beta 数据表",
            },
        ),
    ]


@pytest.mark.anyio
async def test_worker_uses_real_media_store_session_capability(tmp_path):
    console = ConsoleStore(root=tmp_path / "console")
    workspace = console.workspace_store.create({
        "name": "太极实业",
        "kind": "stock",
        "code": "600667",
        "agent_mode": "autonomous",
    })
    sid = console.create_session(
        title="真实媒体",
        workspace_id=workspace["id"],
    )["id"]
    source = (
        tmp_path
        / "console"
        / "workspace_files"
        / workspace["id"]
        / "chart.png"
    )
    source.write_bytes(VALID_PNG)
    media_store = MediaStore(console)
    metadata = media_store.publish_workspace_image(
        workspace["id"],
        "chart.png",
        session_id=sid,
        alt="相关性",
    )

    channels = ChannelStore(tmp_path / "channels.sqlite3")
    bot_id = channels.create_bot("真实图片 Bot", draft_session_id=sid)
    channels.cas_bot_credential(
        bot_id,
        expected_revision=0,
        credential_id=CREDENTIAL_ID,
        telegram_bot_id="1002",
    )
    binding = channels.bind_for_test(
        sid, bot_id, user_id="8", chat_id="8", sync_from_event_id=0
    )
    console.append_event(
        sid,
        "tool_result",
        tool="ww_telegram_publish",
        ok=True,
        artifact=_authorized_artifact(
            channels,
            binding,
            media_id=metadata["id"],
            caption="相关性图",
        ),
    )
    ConsoleMessageProjector(console, channels).project_once(binding.id)
    adapter = Adapter()
    worker = OutboxWorker(
        channel_store=channels,
        secret_store=Secrets(),
        telegram_adapter=adapter,
        media_store=media_store,
    )

    assert await worker.run_once() == 1
    assert channels.outbox_for(binding.id)[0].state is OutboxState.SENT
    assert adapter.calls[0][1]["data"] == VALID_PNG
    assert adapter.calls[0][1]["filename"] == "image.png"
    assert str(source) not in repr(adapter.calls)


@pytest.mark.anyio
async def test_real_document_store_owner_blocks_cross_session_delivery_before_network(
    tmp_path,
):
    console = ConsoleStore(root=tmp_path / "console")
    workspace = console.workspace_store.create({
        "name": "文档边界",
        "kind": "stock",
        "code": "600667",
        "agent_mode": "autonomous",
    })
    owner_sid = console.create_session(
        title="文档所有者", workspace_id=workspace["id"],
    )["id"]
    foreign_sid = console.create_session(
        title="其他对话", workspace_id=workspace["id"],
    )["id"]
    source = (
        tmp_path
        / "console"
        / "workspace_files"
        / workspace["id"]
        / "alpha-beta.md"
    )
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("# Alpha Beta\n\n研究结果。\n", encoding="utf-8")
    media_store = MediaStore(console)
    metadata = media_store.publish_workspace_document(
        workspace["id"],
        source.name,
        session_id=owner_sid,
        title="Alpha Beta 研究结果",
    )
    assert media_store.load_document_for_session(metadata["id"], owner_sid)[1]
    with pytest.raises(MediaNotFound):
        media_store.load_document_for_session(metadata["id"], foreign_sid)

    channels = ChannelStore(tmp_path / "channels.sqlite3")
    bot_id = channels.create_bot("文档 Bot", draft_session_id=foreign_sid)
    channels.cas_bot_credential(
        bot_id,
        expected_revision=0,
        credential_id=CREDENTIAL_ID,
        telegram_bot_id="1003",
    )
    binding = channels.bind_for_test(
        foreign_sid, bot_id, user_id="9", chat_id="9", sync_from_event_id=0,
    )
    _append_document_publish(
        console,
        foreign_sid,
        artifact=_authorized_document_artifact(
            channels,
            binding,
            document_id=metadata["id"],
            caption="研究结果",
        ),
    )
    assert ConsoleMessageProjector(console, channels).project_once(binding.id) == 1
    adapter = Adapter()
    worker = OutboxWorker(
        channel_store=channels,
        secret_store=Secrets(),
        telegram_adapter=adapter,
        media_store=media_store,
    )

    assert await worker.run_once() == 1
    item = channels.outbox_for(binding.id)[0]
    assert item.state is OutboxState.TERMINAL
    assert item.last_error_class == "local_delivery_error"
    assert adapter.calls == []


@pytest.mark.anyio
async def test_slow_media_validation_renews_before_provider_call(tmp_path):
    console, channels, binding = _seed(tmp_path)
    _append_publish(
        console,
        binding.session_id,
        artifact=_authorized_artifact(channels, binding),
    )
    ConsoleMessageProjector(console, channels).project_once(binding.id)
    entered = threading.Event()
    release = threading.Event()

    class SlowMedia(Media):
        def load_for_session(self, media_id: str, session_id: str):
            self.calls.append((media_id, session_id))
            entered.set()
            assert release.wait(timeout=5)
            return (
                {"schema_version": 1, "id": media_id, "mime": "image/png"},
                PNG,
            )

    adapter = Adapter()
    worker = OutboxWorker(
        channel_store=channels,
        secret_store=Secrets(),
        telegram_adapter=adapter,
        media_store=SlowMedia(),
        lease_seconds=3,
    )
    task = asyncio.create_task(worker.run_once())
    try:
        assert await asyncio.to_thread(entered.wait, 2)
        initial_lease = channels.outbox_for(binding.id)[0].lease_until
        loop = asyncio.get_running_loop()
        deadline = loop.time() + 2.2
        renewed_lease = initial_lease
        while renewed_lease == initial_lease and loop.time() < deadline:
            await asyncio.sleep(0.05)
            renewed_lease = channels.outbox_for(binding.id)[0].lease_until
        assert initial_lease is not None and renewed_lease is not None
        assert renewed_lease > initial_lease
    finally:
        release.set()
    assert await asyncio.wait_for(task, timeout=3) == 1
    assert channels.outbox_for(binding.id)[0].state is OutboxState.SENT
    assert [kind for kind, _payload in adapter.calls] == ["photo"]


@pytest.mark.anyio
async def test_long_caption_delivery_order_is_text_before_photo(tmp_path):
    console, channels, binding = _seed(tmp_path)
    caption = "前" * 5000 + "尾" * 901
    _append_publish(
        console,
        binding.session_id,
        artifact=_authorized_artifact(channels, binding, caption=caption),
    )
    ConsoleMessageProjector(console, channels).project_once(binding.id)
    media = Media()
    adapter = Adapter()
    worker = OutboxWorker(
        channel_store=channels,
        secret_store=Secrets(),
        telegram_adapter=adapter,
        media_store=media,
    )

    item_count = len(channels.outbox_for(binding.id))
    for _ in range(item_count):
        assert await worker.run_once() == 1
    assert [kind for kind, _payload in adapter.calls][-1] == "photo"
    assert all(kind == "text" for kind, _payload in adapter.calls[:-1])
    rebuilt = "".join(
        payload["text"] for kind, payload in adapter.calls if kind == "text"
    ) + adapter.calls[-1][1]["caption"]
    assert rebuilt == caption


@pytest.mark.anyio
async def test_missing_or_foreign_media_is_terminal_without_provider_send(tmp_path):
    console, channels, binding = _seed(tmp_path)
    _append_publish(
        console,
        binding.session_id,
        artifact=_authorized_artifact(channels, binding),
    )
    ConsoleMessageProjector(console, channels).project_once(binding.id)
    adapter = Adapter()
    worker = OutboxWorker(
        channel_store=channels,
        secret_store=Secrets(),
        telegram_adapter=adapter,
        media_store=Media(error=LookupError("foreign session")),
    )

    assert await worker.run_once() == 1
    item = channels.outbox_for(binding.id)[0]
    assert item.state is OutboxState.TERMINAL
    assert item.last_error_class == "local_delivery_error"
    assert adapter.calls == []


@pytest.mark.anyio
async def test_photo_reuses_retry_state_machine(tmp_path):
    console, channels, binding = _seed(tmp_path)
    _append_publish(
        console,
        binding.session_id,
        artifact=_authorized_artifact(channels, binding),
    )
    ConsoleMessageProjector(console, channels).project_once(binding.id)
    worker = OutboxWorker(
        channel_store=channels,
        secret_store=Secrets(),
        telegram_adapter=Adapter(photo_outcome=TelegramRetryable(19)),
        media_store=Media(),
        random_uniform=lambda _low, _high: 0.0,
    )

    assert await worker.run_once() == 1
    item = channels.outbox_for(binding.id)[0]
    assert item.state is OutboxState.RETRY_WAIT
    assert item.last_error_class == "retryable"
    assert item.next_attempt_at is not None


@pytest.mark.anyio
async def test_cancelled_photo_send_becomes_uncertain(tmp_path):
    console, channels, binding = _seed(tmp_path)
    _append_publish(
        console,
        binding.session_id,
        artifact=_authorized_artifact(channels, binding),
    )
    ConsoleMessageProjector(console, channels).project_once(binding.id)
    entered = asyncio.Event()

    class Blocked(Adapter):
        async def send_photo(self, _token: str, **kwargs):
            entered.set()
            await asyncio.Event().wait()

    worker = OutboxWorker(
        channel_store=channels,
        secret_store=Secrets(),
        telegram_adapter=Blocked(),
        media_store=Media(),
    )
    task = asyncio.create_task(worker.run_once())
    await asyncio.wait_for(entered.wait(), timeout=2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert channels.outbox_for(binding.id)[0].state is OutboxState.UNCERTAIN


@pytest.mark.anyio
async def test_send_photo_http_200_ok_false_is_not_reported_sent():
    async def handler(_request):
        return httpx.Response(
            200,
            json={"ok": False, "error_code": 500, "description": "provider failed"},
        )

    adapter = TelegramAdapter(
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )
    try:
        with pytest.raises(TelegramRetryable):
            await adapter.send_photo(
                TOKEN,
                chat_id="7",
                data=PNG,
                filename="image.png",
                mime_type="image/png",
                caption="图",
            )
    finally:
        await adapter.aclose()
