"""ConsoleStore: 会话目录、事件追加(单调id)、计划、重读。纯文件级,不碰引擎。"""
from guanlan_v2.console.store import ConsoleStore


def test_create_and_list_sessions(tmp_path):
    st = ConsoleStore(root=tmp_path)
    meta = st.create_session(title="动量全流程")
    assert meta["id"].startswith("cs_") and meta["title"] == "动量全流程"
    assert meta["plan"] == [] and meta["status"] == "idle"
    metas = st.list_sessions()
    assert [m["id"] for m in metas] == [meta["id"]]


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
