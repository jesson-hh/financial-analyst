"""Trusted workspace image publication and Console delivery contracts."""
from __future__ import annotations

import base64
import json
import time
from contextlib import contextmanager

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from guanlan_v2.console import media
from guanlan_v2.console import workspace_tools as wt
from guanlan_v2.console.api import build_console_router
from guanlan_v2.console.media import MediaStore
from guanlan_v2.console.store import ConsoleStore


# Structurally valid, CRC-checked 1x1 PNG.  Keeping the fixture inline makes the
# tests hermetic and guarantees no test reads/writes the repository's real var/.
_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


def _workspace(store: ConsoleStore, code: str):
    ws = store.workspace_store.create({
        "name": code,
        "kind": "stock",
        "code": code,
        "agent_mode": "autonomous",
    })
    sid = store.create_session("图片研究", workspace_id=ws["id"])["id"]
    return ws, sid


@contextmanager
def _bound(store: ConsoleStore, sid: str):
    token = wt.CTX_WORKSPACE.set(wt.bind_workspace_context(store, sid))
    try:
        yield
    finally:
        wt.CTX_WORKSPACE.reset(token)


def _client(store: ConsoleStore, agent_factory=None) -> TestClient:
    app = FastAPI()
    app.include_router(build_console_router(
        store=store,
        agent_factory=agent_factory or (lambda _sid: _NoopAgent()),
    ))
    return TestClient(app)


class _NoopAgent:
    def __init__(self):
        self.messages = []

    async def run_turn(self, _text, confirm_callback=None, allowed_tools=None):
        yield _Event("done")


class _Event:
    def __init__(self, kind, payload=None):
        self.kind = kind
        self.payload = payload


def test_bound_tool_publishes_opaque_media_artifact_and_persistent_metadata(tmp_path):
    store = ConsoleStore(root=tmp_path / "console")
    ws, sid = _workspace(store, "600667")
    image = tmp_path / "console" / "workspace_files" / ws["id"] / "output" / "levels.png"
    image.parent.mkdir()
    image.write_bytes(_PNG)

    with _bound(store, sid):
        result = wt.workspace_publish_image_impl("output/levels.png", alt="太极实业压力支撑位")

    assert result["ok"] is True
    artifact = result["artifact"]
    assert artifact["kind"] == "media_image"
    assert artifact["channel"] == "conversation"
    payload = artifact["payload"]
    assert set(payload) == {
        "media_id", "name", "mime", "width", "height", "alt", "workspace_id",
    }
    assert payload == {
        "media_id": payload["media_id"],
        "name": "levels.png",
        "mime": "image/png",
        "width": 1,
        "height": 1,
        "alt": "太极实业压力支撑位",
        "workspace_id": ws["id"],
    }
    assert media._MEDIA_ID_RE.fullmatch(payload["media_id"])
    assert str(image) not in json.dumps(result, ensure_ascii=False)

    # A fresh MediaStore instance proves metadata/blob durability rather than
    # success coming from an in-memory registry.
    metadata, data = MediaStore(store).load(payload["media_id"])
    assert data == _PNG and metadata["workspace_id"] == ws["id"]
    assert "source_path" not in metadata and "root_path" not in metadata
    assert payload["media_id"] in result["content"]
    assert "ww_telegram_publish" in result["content"]


def test_load_for_session_requires_exact_durable_owner_but_allows_archive(tmp_path):
    store = ConsoleStore(root=tmp_path / "console")
    ws, owner_sid = _workspace(store, "600667")
    other_sid = store.create_session("其他对话", workspace_id=ws["id"])["id"]
    image = tmp_path / "console" / "workspace_files" / ws["id"] / "owned.png"
    image.write_bytes(_PNG)
    with _bound(store, owner_sid):
        media_id = wt.workspace_publish_image_impl("owned.png")["media"]["media_id"]

    media_store = MediaStore(store)
    metadata, data = media_store.load_for_session(media_id, owner_sid)
    assert metadata["session_id"] == owner_sid
    assert data == _PNG
    with pytest.raises(media.MediaNotFound):
        media_store.load_for_session(media_id, other_sid)
    with pytest.raises(media.MediaNotFound):
        media_store.load_for_session(media_id, "")

    # Archival blocks new Channel publication at Runtime's writable gate, but
    # must not strand an image that was already placed in the durable Outbox.
    store.set_archived(owner_sid, True)
    assert media_store.load_for_session(media_id, owner_sid)[1] == _PNG


def test_load_for_session_rejects_ownerless_legacy_metadata(tmp_path):
    store = ConsoleStore(root=tmp_path / "console")
    ws, sid = _workspace(store, "601318")
    image = tmp_path / "console" / "workspace_files" / ws["id"] / "legacy.png"
    image.write_bytes(_PNG)
    with _bound(store, sid):
        media_id = wt.workspace_publish_image_impl("legacy.png")["media"]["media_id"]

    media_store = MediaStore(store)
    metadata_path = media_store._metadata_path(media_id)
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    metadata["session_id"] = ""
    metadata_path.write_text(
        json.dumps(metadata, ensure_ascii=False), encoding="utf-8",
    )

    # The ordinary historic image endpoint remains compatible, while the
    # stronger outbound-channel ownership read fails closed.
    assert media_store.load(media_id)[1] == _PNG
    with pytest.raises(media.MediaNotFound):
        media_store.load_for_session(media_id, sid)


def test_cross_workspace_traversal_non_image_mismatch_and_oversize_are_rejected(
        tmp_path, monkeypatch):
    store = ConsoleStore(root=tmp_path / "console")
    first, first_sid = _workspace(store, "600519")
    second, _second_sid = _workspace(store, "300750")
    first_root = tmp_path / "console" / "workspace_files" / first["id"]
    second_root = tmp_path / "console" / "workspace_files" / second["id"]
    (first_root / "plain.txt").write_text("not an image", encoding="utf-8")
    (first_root / "wrong.jpg").write_bytes(_PNG)
    (first_root / "large.png").write_bytes(_PNG)
    (second_root / "other.png").write_bytes(_PNG)

    with _bound(store, first_sid):
        crossed = wt.workspace_publish_image_impl(
            f"../../workspace_files/{second['id']}/other.png",
        )
        absolute = wt.workspace_publish_image_impl(str(second_root / "other.png"))
        non_image = wt.workspace_publish_image_impl("plain.txt")
        mismatch = wt.workspace_publish_image_impl("wrong.jpg")
        monkeypatch.setattr(media, "MAX_MEDIA_BYTES", 32)
        oversize = wt.workspace_publish_image_impl("large.png")

    assert not crossed["ok"] and not absolute["ok"]
    assert not non_image["ok"] and "PNG、JPEG 或 WebP" in non_image["content"]
    assert not mismatch["ok"] and "扩展名" in mismatch["content"]
    assert not oversize["ok"] and "上限" in oversize["content"]


def test_symlink_image_is_rejected(tmp_path):
    store = ConsoleStore(root=tmp_path / "console")
    ws, sid = _workspace(store, "601318")
    root = tmp_path / "console" / "workspace_files" / ws["id"]
    outside = tmp_path / "outside.png"
    outside.write_bytes(_PNG)
    link = root / "linked.png"
    try:
        link.symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("当前 Windows 权限不允许创建符号链接")
    with _bound(store, sid):
        result = wt.workspace_publish_image_impl("linked.png")
    assert result["ok"] is False


def test_media_endpoint_is_same_origin_nosniff_cached_and_archive_readable(tmp_path):
    store = ConsoleStore(root=tmp_path / "console")
    ws, sid = _workspace(store, "000858")
    image = tmp_path / "console" / "workspace_files" / ws["id"] / "chart.png"
    image.write_bytes(_PNG)
    with _bound(store, sid):
        media_id = wt.workspace_publish_image_impl("chart.png")["media"]["media_id"]
    client = _client(store)

    response = client.get(f"/console/media/{media_id}")
    assert response.status_code == 200 and response.content == _PNG
    assert response.headers["content-type"] == "image/png"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["cross-origin-resource-policy"] == "same-origin"
    assert response.headers["cache-control"] == "private, max-age=31536000, immutable"
    assert response.headers["content-disposition"].startswith("inline; filename*=UTF-8''")
    assert response.headers["etag"]

    cached = client.get(
        f"/console/media/{media_id}", headers={"If-None-Match": response.headers["etag"]},
    )
    assert cached.status_code == 304 and not cached.content
    assert client.get(
        f"/console/media/{media_id}", headers={"Origin": "https://attacker.example"},
    ).status_code == 403
    assert client.get(
        f"/console/media/{media_id}", headers={"Sec-Fetch-Site": "cross-site"},
    ).status_code == 403
    assert client.get("/console/media/../../secret").status_code == 404
    assert client.get("/console/media/mi_" + "0" * 32).status_code == 404

    store.workspace_store.archive(ws["id"])
    archived = client.get(f"/console/media/{media_id}")
    assert archived.status_code == 200 and archived.content == _PNG


def test_media_artifact_is_persisted_and_visible_in_sse_snapshot(tmp_path, monkeypatch):
    store = ConsoleStore(root=tmp_path / "console")
    ws, sid = _workspace(store, "600036")
    payload = {
        "media_id": "mi_" + "a" * 32,
        "name": "alpha.png",
        "mime": "image/png",
        "width": 900,
        "height": 600,
        "alt": "Alpha Beta 回归图",
        "workspace_id": ws["id"],
    }

    class MediaAgent:
        def __init__(self):
            self.messages = []

        async def run_turn(self, _text, confirm_callback=None, allowed_tools=None):
            yield _Event("tool_call", {
                "name": "ww_workspace_publish_image", "args": {"path": "alpha.png"},
            })
            yield _Event("tool_result", {
                "name": "ww_workspace_publish_image",
                "content": "图片已安全发布",
                "is_error": False,
                "side_effect": {"artifact": {
                    "kind": "media_image", "page": None, "channel": "conversation",
                    "payload": payload, "ref": payload["media_id"],
                }},
            })
            yield _Event("done")

    client = _client(store, agent_factory=lambda _sid: MediaAgent())
    accepted = client.post("/console/send", json={"sid": sid, "text": "给我图"})
    assert accepted.status_code == 200
    deadline = time.time() + 3
    events = []
    while time.time() < deadline:
        events = store.read_events(sid)
        if any(e.get("type") == "task_update" and e.get("terminal") for e in events):
            break
        time.sleep(0.02)
    result = next(e for e in events if e.get("type") == "tool_result")
    assert result["artifact"]["kind"] == "media_image"
    assert result["artifact"]["payload"] == payload

    import guanlan_v2.console.api as console_api
    monkeypatch.setattr(console_api, "_SSE_LIFETIME", 0)
    stream = client.get(f"/console/stream/{sid}")
    assert stream.status_code == 200
    assert '"kind": "media_image"' in stream.text


def test_publish_tool_is_registered_without_relaxing_execution_confirmation():
    from guanlan_v2.console import tools as ct

    spec = next(t for t in ct.WW_TOOL_TABLE if t["name"] == "ww_workspace_publish_image")
    run = next(t for t in ct.WW_TOOL_TABLE if t["name"] == "ww_workspace_run")
    assert spec["confirm"] is False
    assert spec["input_schema"]["required"] == ["path"]
    assert run["confirm"] is True

    # Sending an already-generated, validated private image is presentation,
    # not project mutation.  Read-only research spaces must be able to show it;
    # arbitrary project execution remains unavailable there.
    allowed = wt.allowed_tools_for_workspace(
        ct.CONSOLE_ALLOWED,
        {"id": "rw_0123456789ab", "agent_mode": "research"},
    )
    assert "ww_workspace_publish_image" in allowed
    assert "ww_workspace_run" not in allowed
