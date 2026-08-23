from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor

from guanlan_v2.channels.models import BindingStatus
from guanlan_v2.channels.projector import ConsoleMessageProjector, split_outbound_text
from guanlan_v2.channels.store import ChannelStore
from guanlan_v2.console.store import ConsoleStore


def _seed(tmp_path, *, cursor: int = 0):
    console = ConsoleStore(root=tmp_path / "console")
    sid = console.create_session(title="镜像") ["id"]
    channels = ChannelStore(tmp_path / "channels.sqlite3")
    bot_id = channels.create_bot("镜像 Bot", draft_session_id=sid)
    channels.activate_test_bot(bot_id, telegram_bot_id="101")
    binding = channels.bind_for_test(
        sid,
        bot_id,
        user_id="7",
        chat_id="7",
        sync_from_event_id=cursor,
    )
    return console, channels, binding


def test_projector_whitelist_is_fail_closed_and_never_always_wins(tmp_path):
    console, channels, binding = _seed(tmp_path)
    console.append_event(binding.session_id, "user_msg", text="用户正文")
    for event_type in (
        "agent_delta",
        "tool_call",
        "tool_result",
        "task_update",
        "confirm_request",
        "reasoning",
        "error",
        "automation_trigger",
    ):
        console.append_event(binding.session_id, event_type, text="内部内容")
    console.append_event(
        binding.session_id,
        "agent_final",
        text="不应发送",
        channel_projection="never",
    )
    console.append_event(binding.session_id, "agent_final", text="最终回答", turn_id="t1")

    projector = ConsoleMessageProjector(console, channels)
    assert projector.project_once(binding.id) == 11
    assert [item.text for item in channels.outbox_for(binding.id)] == [
        "用户正文",
        "最终回答",
    ]
    assert channels.binding(binding.id).projection_cursor_event_id == 11


def test_projector_pages_oldest_first_beyond_two_thousand_events(tmp_path):
    console, channels, binding = _seed(tmp_path)
    events = [
        {"id": index, "ts": "2026-08-23T00:00:00+00:00", "type": "agent_delta",
         "text": str(index)}
        for index in range(1, 2002)
    ]
    events.append({
        "id": 2002,
        "ts": "2026-08-23T00:00:00+00:00",
        "type": "agent_final",
        "text": "积压后的最终结果",
    })
    events_path = console._sdir(binding.session_id) / "events.jsonl"
    events_path.write_text(
        "".join(json.dumps(event, ensure_ascii=False) + "\n" for event in events),
        encoding="utf-8",
    )
    console.merge_meta(binding.session_id, next_event_id=2003)

    projector = ConsoleMessageProjector(console, channels, page_size=200)
    pages = 0
    while projector.project_once(binding.id):
        pages += 1

    assert pages == 11
    assert [item.text for item in channels.outbox_for(binding.id)] == ["积压后的最终结果"]
    assert channels.binding(binding.id).projection_cursor_event_id == 2002


def test_projector_never_backfills_events_before_binding_boundary(tmp_path):
    console = ConsoleStore(root=tmp_path / "console")
    sid = console.create_session(title="边界")["id"]
    console.append_event(sid, "user_msg", text="绑定前用户消息")
    console.append_event(sid, "agent_final", text="绑定前最终答复")

    channels = ChannelStore(tmp_path / "channels.sqlite3")
    bot_id = channels.create_bot("边界 Bot", draft_session_id=sid)
    channels.activate_test_bot(bot_id, telegram_bot_id="102")
    binding = channels.bind_for_test(
        sid,
        bot_id,
        user_id="8",
        chat_id="8",
        sync_from_event_id=2,
    )
    console.append_event(sid, "user_msg", text="绑定后第一条")

    assert ConsoleMessageProjector(console, channels).project_once(binding.id) == 1
    assert [item.text for item in channels.outbox_for(binding.id)] == ["绑定后第一条"]
    assert channels.binding(binding.id).projection_cursor_event_id == 3


def test_pairing_shape_is_skipped_without_stranding_projection_cursor(tmp_path):
    console, channels, binding = _seed(tmp_path)
    console.append_event(binding.session_id, "user_msg", text="配对码 23456789")
    console.append_event(binding.session_id, "agent_final", text="继续正常镜像")

    projector = ConsoleMessageProjector(console, channels)
    assert projector.project_once(binding.id) == 2
    assert [item.text for item in channels.outbox_for(binding.id)] == ["继续正常镜像"]
    assert channels.binding(binding.id).projection_cursor_event_id == 2


def test_intentional_resume_resets_both_cursors_to_actual_tail(tmp_path):
    console, channels, binding = _seed(tmp_path)
    console.append_event(binding.session_id, "user_msg", text="暂停前")
    ConsoleMessageProjector(console, channels).project_once(binding.id)
    channels.set_session_manual_pause(binding.session_id, paused=True)
    console.append_event(binding.session_id, "user_msg", text="暂停期间一")
    console.append_event(binding.session_id, "agent_final", text="暂停期间二")

    channels.set_session_manual_pause(
        binding.session_id,
        paused=False,
        resume_from_event_id=3,
    )
    resumed = channels.binding(binding.id)
    assert resumed.status is BindingStatus.ACTIVE
    assert resumed.sync_from_event_id == resumed.projection_cursor_event_id == 3

    console.append_event(binding.session_id, "user_msg", text="恢复后")
    ConsoleMessageProjector(console, channels).project_once(binding.id)
    items = channels.outbox_for(binding.id)
    assert [item.text for item in items] == ["暂停前", "恢复后"]
    assert items[0].state.value == "cancelled"


def test_text_chunking_is_lossless_stable_and_bounded():
    cases = (
        " " * 4001,
        "a" * 4000,
        "a" * 4001,
        "段落一\r\n\r\n" + "乙" * 3995 + "\n末尾",
        "🙂" * 4001,
        ("e\u0301" * 2100),
    )
    for value in cases:
        first = split_outbound_text(value)
        second = split_outbound_text(value)
        assert first == second
        assert "".join(first) == value
        assert first
        assert all(0 < len(part) <= 4000 for part in first)


def test_two_projectors_cannot_advance_high_event_before_oldest_page(tmp_path):
    console, channels, binding = _seed(tmp_path)
    console.append_event(binding.session_id, "user_msg", text="低 ID")
    console.append_event(binding.session_id, "agent_final", text="高 ID")
    real_reader = console.read_events_forward
    entered = threading.Event()
    release = threading.Event()
    calls = 0
    calls_lock = threading.Lock()

    def interleaved_reader(*args, **kwargs):
        nonlocal calls
        with calls_lock:
            calls += 1
            call_number = calls
        if call_number == 1:
            entered.set()
            assert release.wait(timeout=5)
            return real_reader(*args, **kwargs)
        # Without a binding lease a second generation could observe/commit the
        # high event first while the low-page reader is paused here.
        return [real_reader(*args, **kwargs)[-1]]

    console.read_events_forward = interleaved_reader
    first = ConsoleMessageProjector(console, channels)
    second = ConsoleMessageProjector(console, channels)
    with ThreadPoolExecutor(max_workers=2) as pool:
        low_page = pool.submit(first.project_once, binding.id)
        assert entered.wait(timeout=5)
        competing = pool.submit(second.project_once, binding.id)
        assert competing.result(timeout=5) == 0
        release.set()
        assert low_page.result(timeout=5) == 2

    items = channels.outbox_for(binding.id)
    assert [(item.source_event_id, item.sequence, item.text) for item in items] == [
        (1, 1, "低 ID"),
        (2, 2, "高 ID"),
    ]
    assert calls == 1
