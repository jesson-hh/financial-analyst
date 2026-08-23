"""ConsoleStore: 会话目录、事件追加(单调id)、计划、重读。纯文件级,不碰引擎。"""
import json

import pytest

from guanlan_v2.console.store import ConsoleStore
from guanlan_v2.console.workspaces import (
    GENERAL_WORKSPACE_ID,
    WorkspaceAmbiguous,
    WorkspaceError,
    normalize_security_code,
)


def test_create_and_list_sessions(tmp_path):
    st = ConsoleStore(root=tmp_path)
    meta = st.create_session(title="动量全流程")
    assert meta["id"].startswith("cs_") and meta["title"] == "动量全流程"
    assert meta["plan"] == [] and meta["status"] == "idle"
    assert meta["archived"] is False
    metas = st.list_sessions()
    assert [m["id"] for m in metas] == [meta["id"]]


def test_general_workspace_and_legacy_session_compatibility(tmp_path):
    st = ConsoleStore(root=tmp_path)
    general = st.workspace_store.get(GENERAL_WORKSPACE_ID)
    assert general["name"] == "通用研究" and general["kind"] == "general"
    assert general["root_mode"] == "managed"
    assert (tmp_path / "workspace_files" / GENERAL_WORKSPACE_ID).is_dir()

    sid = st.create_session()["id"]
    meta_path = tmp_path / "sessions" / sid / "meta.json"
    raw = json.loads(meta_path.read_text(encoding="utf-8"))
    raw.pop("workspace_id")  # simulate a pre-workspace conversation
    raw.pop("archived")      # simulate a pre-session-archive conversation
    meta_path.write_text(json.dumps(raw), encoding="utf-8")
    assert st.get_meta(sid)["workspace_id"] == GENERAL_WORKSPACE_ID
    assert st.get_meta(sid)["archived"] is False
    assert st.list_sessions()[0]["workspace_id"] == GENERAL_WORKSPACE_ID


def test_session_soft_archive_restore_preserves_history_and_private_state(tmp_path):
    st = ConsoleStore(root=tmp_path)
    sid = st.create_session(title="保留我")["id"]
    event = st.append_event(sid, "user_msg", text="完整历史")
    state = {
        "schema_version": "1",
        "checkpoint_event_id": event["id"],
        "messages": [{"role": "user", "content": "私有上下文"}],
    }
    st.save_buddy_state(sid, state)

    archived = st.set_archived(sid, True)
    assert archived["archived"] is True and archived["archived_at"]
    assert st.read_events(sid) == [event]
    assert st.load_buddy_state(sid) == state

    restored = st.set_archived(sid, False)
    assert restored["archived"] is False and "archived_at" not in restored
    assert st.read_events(sid) == [event]
    assert st.set_archived("plan_approvals", True) is None


def test_workspace_managed_root_binding_and_session_move(tmp_path):
    st = ConsoleStore(root=tmp_path)
    ws = st.workspace_store.create({
        "name": "贵州茅台研究", "kind": "stock", "code": "600519.SH",
    })
    assert ws["code"] == "SH600519" and ws["agent_mode"] == "collaborative"
    root = (tmp_path / "workspace_files" / ws["id"]).resolve()
    assert root.is_dir() and ws["root_path"] == str(root)
    meta = st.create_session("估值", workspace_id=ws["id"])
    assert meta["workspace_id"] == ws["id"]
    assert st.set_workspace(meta["id"], GENERAL_WORKSPACE_ID)["workspace_id"] \
        == GENERAL_WORKSPACE_ID


def test_security_workspace_lookup_is_canonical_unique_and_fail_closed(tmp_path):
    assert normalize_security_code("920001") == "BJ920001"
    st = ConsoleStore(root=tmp_path)
    first = st.workspace_store.create({
        "name": "浦发研究", "kind": "stock", "code": "600000.SH",
    })
    assert st.workspace_store.find_active_security_workspace("600000")["id"] == first["id"]
    assert st.workspace_store.find_active_security_workspace("SH600000")["id"] == first["id"]

    # Stock/ETF are UI labels; Seats owns one global assignment per code.
    with pytest.raises(WorkspaceError, match="已有一个未归档"):
        st.workspace_store.create({
            "name": "错误的重复类型", "kind": "etf", "code": "600000",
        })

    # Legacy/corrupt metadata may predate the creation guard.  Exact lookup
    # must fail closed rather than selecting whichever JSON file sorts first.
    second = st.workspace_store.create({
        "name": "ETF", "kind": "etf", "code": "510300",
    })
    second_path = tmp_path / "workspaces" / f"{second['id']}.json"
    second_raw = json.loads(second_path.read_text(encoding="utf-8"))
    second_raw["code"] = "SH600000"
    second_path.write_text(json.dumps(second_raw), encoding="utf-8")
    with pytest.raises(WorkspaceAmbiguous, match="拒绝歧义投影"):
        st.workspace_store.find_active_security_workspace("600000.SH")

    second_raw["archived"] = True
    second_path.write_text(json.dumps(second_raw), encoding="utf-8")
    assert st.workspace_store.find_active_security_workspace("600000")["id"] == first["id"]


def test_workspace_external_root_requires_one_time_desktop_grant(tmp_path):
    st = ConsoleStore(root=tmp_path / "console")
    chosen = tmp_path / "selected-research"
    chosen.mkdir()
    with pytest.raises(WorkspaceError, match="不能通过 HTTP"):
        st.workspace_store.create({
            "name": "ETF", "kind": "etf", "code": "159915",
            "root_mode": "external", "root_path": str(chosen),
        })

    grant = st.workspace_store.issue_folder_grant(chosen)
    ws = st.workspace_store.create({
        "name": "创业板 ETF", "kind": "etf", "code": "159915",
        "root_mode": "external", "folder_grant": grant["id"],
    })
    assert ws["root_path"] == str(chosen.resolve())
    with pytest.raises(WorkspaceError, match="不存在或已使用"):
        st.workspace_store.create({
            "name": "重复授权", "kind": "etf", "code": "159949",
            "root_mode": "external", "folder_grant": grant["id"],
        })


def test_external_workspace_rejects_control_repo_and_other_workspace_overlap(tmp_path):
    st = ConsoleStore(root=tmp_path / "console")
    with pytest.raises(WorkspaceError, match="控制数据目录"):
        st.workspace_store.issue_folder_grant(tmp_path / "console")

    first_root = tmp_path / "first"
    first_root.mkdir()
    first_grant = st.workspace_store.issue_folder_grant(first_root)
    st.workspace_store.create({
        "name": "第一空间", "kind": "stock", "code": "600000",
        "root_mode": "external", "folder_grant": first_grant["id"],
    })
    nested = first_root / "nested"
    nested.mkdir()
    with pytest.raises(WorkspaceError, match="其他研究空间"):
        st.workspace_store.issue_folder_grant(nested)

    repo_root = __import__("pathlib").Path(__file__).resolve().parents[1]
    with pytest.raises(WorkspaceError, match="程序目录"):
        st.workspace_store.issue_folder_grant(repo_root)


def test_workspace_archive_never_deletes_root(tmp_path):
    st = ConsoleStore(root=tmp_path)
    ws = st.workspace_store.create({"name": "宁德", "kind": "stock", "code": "300750"})
    root = tmp_path / "workspace_files" / ws["id"]
    sentinel = root / "research.md"
    sentinel.write_text("retain", encoding="utf-8")
    archived = st.workspace_store.archive(ws["id"])
    assert archived["archived"] is True
    assert sentinel.read_text(encoding="utf-8") == "retain"
    with pytest.raises(WorkspaceError):
        st.create_session(workspace_id=ws["id"])


def test_append_and_read_events_monotonic(tmp_path):
    st = ConsoleStore(root=tmp_path)
    sid = st.create_session()["id"]
    e1 = st.append_event(sid, "user_msg", text="你好")
    e2 = st.append_event(sid, "agent_delta", text="收到")
    assert (e1["id"], e2["id"]) == (1, 2)
    assert e1["type"] == "user_msg" and e1["ts"]
    evs = st.read_events(sid)
    assert [e["id"] for e in evs] == [1, 2]
    assert st.read_events(sid, after_id=1)[0]["text"] == "收到"


def test_read_events_forward_returns_oldest_page_after_cursor(tmp_path):
    st = ConsoleStore(root=tmp_path)
    sid = st.create_session()["id"]
    for i in range(2205):
        st.append_event(sid, "user_msg", text=str(i))

    page = st.read_events_forward(sid, after_id=0, limit=200)

    assert [event["id"] for event in page] == list(range(1, 201))
    # The existing reader is the SSE snapshot reader and must keep its tail semantics.
    assert st.read_events(sid, after_id=0, limit=200)[0]["id"] == 2006


def test_read_events_forward_pages_across_event_id_gaps(tmp_path):
    st = ConsoleStore(root=tmp_path)
    sid = st.create_session()["id"]
    for i in range(8):
        st.append_event(sid, "user_msg", text=str(i))

    # append_event deliberately reserves ids before appending.  A crash can
    # therefore leave holes; emulate that durable shape without renumbering.
    path = tmp_path / "sessions" / sid / "events.jsonl"
    events = [event for event in st.read_events(sid) if event["id"] not in {3, 4, 7}]
    path.write_text(
        "".join(json.dumps(event, ensure_ascii=False) + "\n" for event in events),
        encoding="utf-8",
    )

    seen = []
    cursor = 0
    while True:
        page = st.read_events_forward(sid, after_id=cursor, limit=2)
        if not page:
            break
        seen.extend(event["id"] for event in page)
        cursor = page[-1]["id"]

    assert seen == [1, 2, 5, 6, 8]


def test_plan_roundtrip(tmp_path):
    st = ConsoleStore(root=tmp_path)
    sid = st.create_session()["id"]
    todos = [{"id": "t1", "text": "验证动量因子", "status": "in_progress"}]
    meta = st.set_plan(sid, todos)
    assert meta["plan"] == todos
    assert st.get_meta(sid)["plan"][0]["status"] == "in_progress"


def test_delete_session(tmp_path):
    st = ConsoleStore(root=tmp_path)
    sid = st.create_session()["id"]
    st.append_event(sid, "user_msg", text="x")
    assert st.delete_session(sid) is True
    assert st.list_sessions() == [] and st.get_meta(sid) is None


def test_append_concurrent_threads_unique_monotonic(tmp_path):
    import threading
    st = ConsoleStore(root=tmp_path)
    sid = st.create_session()["id"]
    ids = []
    def worker():
        for _ in range(50):
            ids.append(st.append_event(sid, "user_msg", text="x")["id"])
    ts = [threading.Thread(target=worker) for _ in range(2)]
    [t.start() for t in ts]; [t.join() for t in ts]
    assert sorted(ids) == list(range(1, 101))
    evs = st.read_events(sid)
    assert len(evs) == 100 and len({e["id"] for e in evs}) == 100


def test_merge_meta(tmp_path):
    st = ConsoleStore(root=tmp_path)
    sid = st.create_session()["id"]
    m = st.merge_meta(sid, bg={"bg_1": {"kind": "report", "status": "done"}})
    assert m["bg"]["bg_1"]["kind"] == "report"
    assert st.merge_meta("cs_nope", bg={}) is None


def test_merge_meta_sub_keeps_siblings_and_fields(tmp_path):
    """子键合并不丢兄弟条目;同子键再合并是字段级更新不抹旧字段;缺会话返回 None。"""
    st = ConsoleStore(root=tmp_path)
    sid = st.create_session()["id"]
    st.merge_meta_sub(sid, "bg", "a", {"kind": "report", "code": "SZ000001", "status": "running"})
    m = st.merge_meta_sub(sid, "bg", "b", {"kind": "report", "code": "SH600519", "status": "running"})
    assert set(m["bg"]) == {"a", "b"}                       # 写 b 不丢 a
    m = st.merge_meta_sub(sid, "bg", "a", {"status": "done", "ok": True})
    assert m["bg"]["a"]["status"] == "done" and m["bg"]["a"]["ok"] is True
    assert m["bg"]["a"]["code"] == "SZ000001"               # 旧字段保留
    assert m["bg"]["b"]["status"] == "running"              # 兄弟条目不受影响
    persisted = st.get_meta(sid)["bg"]                      # 真落盘
    assert persisted["a"]["status"] == "done" and persisted["b"]["code"] == "SH600519"
    assert st.merge_meta_sub("cs_nope", "bg", "x", {"status": "running"}) is None


def test_reads_under_concurrent_writes_no_tear(tmp_path):
    """H4-1 读持锁:两写线程各 50 次 append + 主线程并发 get_meta/read_events/list_sessions:
    不抛异常、终态恰 100 条、事件 id 严格单调 1..100。"""
    import threading
    st = ConsoleStore(root=tmp_path)
    sid = st.create_session()["id"]
    errs = []

    def writer():
        try:
            for _ in range(50):
                st.append_event(sid, "user_msg", text="x")
        except Exception as e:   # pragma: no cover — 失败取证
            errs.append(repr(e))

    ts = [threading.Thread(target=writer) for _ in range(2)]
    [t.start() for t in ts]
    for _ in range(50):                                # 主线程并发读:持锁不撕裂、不抛
        m = st.get_meta(sid)
        assert m is not None and m["id"] == sid
        st.read_events(sid)
        st.list_sessions()                             # 内部重入 get_meta(RLock)
    [t.join() for t in ts]
    assert errs == []
    evs = st.read_events(sid)
    assert [e["id"] for e in evs] == list(range(1, 101))


def test_delete_session_with_subdirectory(tmp_path):
    """H4-1 delete 改 rmtree:会话目录含子目录(笔记/附件)也能整树删
    ——旧 iterdir+unlink 对子目录直接炸,这正是 rmtree 的修复点。"""
    st = ConsoleStore(root=tmp_path)
    sid = st.create_session()["id"]
    sub = tmp_path / "sessions" / sid / "sub"
    sub.mkdir()
    (sub / "x.txt").write_text("孤儿文件", encoding="utf-8")
    assert st.delete_session(sid) is True
    assert not (tmp_path / "sessions" / sid).exists()
    assert st.get_meta(sid) is None


def test_delete_session_rejects_path_traversal(tmp_path):
    """非法 sid(../ 等)一律拒:绝不把 sessions 上层目录(memory.md 所在)当会话删。"""
    st = ConsoleStore(root=tmp_path)
    sentinel = tmp_path / "memory.md"                  # store root 顶层哨兵
    sentinel.write_text("勿删", encoding="utf-8")
    for bad in ("..", "../..", "cs_nope", "cs_XYZ", "", "cs_" + "a" * 13):
        assert st.delete_session(bad) is False
    assert sentinel.exists() and sentinel.read_text(encoding="utf-8") == "勿删"
    # 合法 sid 仍正常删
    sid = st.create_session()["id"]
    assert st.delete_session(sid) is True


def test_buddy_state_roundtrip_preserves_private_tool_protocol(tmp_path):
    st = ConsoleStore(root=tmp_path)
    sid = st.create_session()["id"]
    before = st.get_meta(sid)["next_event_id"]
    state = {
        "schema_version": "1",
        "checkpoint_event_id": 0,
        "messages": [
            {"role": "user", "content": "查一下宁德时代"},
            {
                "role": "assistant",
                "content": "",
                "reasoning_content": "先核行情，再形成结论",
                "tool_calls": [{
                    "id": "call_中文_1",
                    "type": "function",
                    "function": {"name": "quote_lookup", "arguments": "{\"code\":\"SZ300750\"}"},
                }],
            },
            {"role": "tool", "tool_call_id": "call_中文_1", "content": "现价 251.20"},
        ],
    }
    st.save_buddy_state(sid, state)
    assert st.load_buddy_state(sid) == state
    # 私有快照不是事件：不推进 SSE 的 event id，也不写 events.jsonl。
    assert st.get_meta(sid)["next_event_id"] == before
    assert st.read_events(sid) == []


def test_buddy_state_atomic_overwrite_corruption_and_delete(tmp_path):
    st = ConsoleStore(root=tmp_path)
    sid = st.create_session()["id"]
    first = {"schema_version": "1", "checkpoint_event_id": 1,
             "messages": [{"role": "user", "content": "一"}]}
    second = {"schema_version": "1", "checkpoint_event_id": 2,
              "messages": [{"role": "assistant", "content": "二"}]}
    st.save_buddy_state(sid, first)
    st.save_buddy_state(sid, second)
    assert st.load_buddy_state(sid) == second
    state_path = tmp_path / "sessions" / sid / "buddy_state.json"
    state_path.write_text("{broken", encoding="utf-8")
    assert st.load_buddy_state(sid) is None
    assert st.delete_session(sid) is True
    assert not state_path.exists()


def test_buddy_state_rejects_dangling_or_mismatched_tool_protocol(tmp_path):
    st = ConsoleStore(root=tmp_path)
    sid = st.create_session()["id"]
    dangling = {
        "schema_version": "1", "checkpoint_event_id": 1,
        "messages": [
            {"role": "user", "content": "查行情"},
            {"role": "assistant", "content": "", "tool_calls": [{
                "id": "call_a", "type": "function",
                "function": {"name": "quote", "arguments": "{}"},
            }]},
        ],
    }
    with pytest.raises(ValueError, match="incomplete tool-call"):
        st.save_buddy_state(sid, dangling)

    mismatched = json.loads(json.dumps(dangling))
    mismatched["messages"].append({
        "role": "tool", "tool_call_id": "call_b", "content": "wrong id",
    })
    state_path = tmp_path / "sessions" / sid / "buddy_state.json"
    state_path.write_text(json.dumps(mismatched), encoding="utf-8")
    assert st.load_buddy_state(sid) is None
