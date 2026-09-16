"""ConsoleStore: 会话目录、事件追加(单调id)、计划、重读。纯文件级,不碰引擎。"""
import pytest

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


def test_evidence_ledger_roundtrip_is_private_bounded_and_survives_overwrite(tmp_path):
    st = ConsoleStore(tmp_path)
    sid = st.create_session()["id"]
    row = {
        "citation_id": "R1",
        "title": "AI 算力行业跟踪",
        "institution": "测试证券",
        "publish_ts": "2026-08-14T09:30:00+08:00",
        "source_ref": "https://example.invalid/report.pdf",
        "source_kind": "live_metadata_only",
        "view": "这是只留在私有账本中的有界观点",
    }
    public = st.upsert_evidence(sid, [row], event_id=7)
    assert len(public) == 1 and public[0]["evidence_id"].startswith("R-")
    assert "view" not in public[0]

    private = st.load_evidence(sid)
    assert private[0]["view"] == row["view"]
    assert private[0]["aliases"] == ["R1"]
    assert private[0]["first_event_id"] == private[0]["last_event_id"] == 7

    row["view"] = "更新后的同源摘要"
    st.upsert_evidence(sid, [row], event_id=12)
    restored = ConsoleStore(tmp_path).load_evidence(sid)
    assert len(restored) == 1
    assert restored[0]["view"] == "更新后的同源摘要"
    assert restored[0]["first_event_id"] == 7 and restored[0]["last_event_id"] == 12


def test_corrupt_evidence_ledger_fails_closed(tmp_path):
    st = ConsoleStore(tmp_path)
    sid = st.create_session()["id"]
    (tmp_path / "sessions" / sid / "evidence_ledger.json").write_text("{bad", encoding="utf-8")
    assert st.load_evidence(sid) == []


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


def test_all_session_paths_reject_aliases_of_a_real_session(tmp_path):
    st = ConsoleStore(root=tmp_path)
    sid = st.create_session()["id"]
    alias = f"../sessions/{sid}"
    state = _catalog_state()

    assert (tmp_path / "sessions" / alias).resolve() == (
        tmp_path / "sessions" / sid
    ).resolve()
    assert st.get_meta(alias) is None
    assert st.read_events(alias) == []
    assert st.load_buddy_state(alias) is None
    assert st.load_codex_state(alias) is None
    assert st.load_data_catalog_state(alias) is None
    with pytest.raises(KeyError):
        st.append_event(alias, "user_msg", text="must not alias")
    with pytest.raises(KeyError):
        st.save_data_catalog_state(alias, state)
    assert st.get_meta(sid) is not None


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


def _codex_state(thread_id="019a1b2c-1234-7abc-8def-1234567890ab"):
    return {
        "schema_version": "1",
        "backend": "codex",
        "thread_id": thread_id,
        "workspace_id": "rw_general",
        "workspace_root_sha256": "a" * 64,
        "workspace_subject_sha256": "b" * 64,
        "checkpoint_event_id": 3,
        "health": "ready",
        "created_at": "2026-08-30T00:00:00+00:00",
        "updated_at": "2026-08-30T00:01:00+00:00",
        "last_error": None,
        "pending_receipts": [],
        "tool_transport": "mcp:guanlan-scoped",
        "active_turn_id": None,
        "turn_nonce": None,
    }


def test_codex_state_roundtrip_is_private_token_free_and_atomic(tmp_path):
    st = ConsoleStore(root=tmp_path)
    sid = st.create_session()["id"]
    before = st.get_meta(sid)["next_event_id"]
    first = _codex_state()
    second = {**first, "checkpoint_event_id": 9,
              "pending_receipts": ["已完成查询，不要重复"]}

    st.save_codex_state(sid, first)
    st.save_codex_state(sid, second)

    assert st.load_codex_state(sid) == second
    assert st.get_meta(sid)["next_event_id"] == before
    assert st.read_events(sid) == []
    raw = (tmp_path / "sessions" / sid / "codex_state.json").read_text(
        encoding="utf-8"
    )
    assert "token" not in raw.lower() and "oauth" not in raw.lower()


def test_codex_state_corruption_and_unexpected_secret_fields_fail_closed(tmp_path):
    st = ConsoleStore(root=tmp_path)
    sid = st.create_session()["id"]
    path = tmp_path / "sessions" / sid / "codex_state.json"

    with pytest.raises(ValueError):
        st.save_codex_state(sid, {**_codex_state(), "access_token": "secret"})
    with pytest.raises(ValueError):
        st.save_codex_state(sid, {**_codex_state(), "health": "unknown"})
    path.write_text("{broken", encoding="utf-8")
    assert st.load_codex_state(sid) is None


def _catalog_state(seed="a"):
    return {
        "schema_version": "1",
        "catalog_digest": seed * 64,
        "datasets": {"market.quote": "b" * 64},
        "sources": {"tencent_realtime_quote": "c" * 64},
    }


def test_data_catalog_state_roundtrip_atomic_overwrite_and_delete(tmp_path):
    st = ConsoleStore(root=tmp_path)
    sid = st.create_session()["id"]
    before = st.get_meta(sid)["next_event_id"]
    first = _catalog_state("a")
    second = {
        "schema_version": "1",
        "catalog_digest": "d" * 64,
        "datasets": {"market.quote": "e" * 64, "market.bar": "f" * 64},
        "sources": {},
    }

    st.save_data_catalog_state(sid, first)
    st.save_data_catalog_state(sid, second)
    assert st.load_data_catalog_state(sid) == second
    assert st.get_meta(sid)["next_event_id"] == before
    assert st.read_events(sid) == []

    state_path = tmp_path / "sessions" / sid / "data_catalog_state.json"
    assert state_path.exists()
    assert st.delete_session(sid) is True
    assert not state_path.exists()


def test_data_catalog_state_strict_validation_and_corruption(tmp_path):
    st = ConsoleStore(root=tmp_path)
    sid = st.create_session()["id"]
    state_path = tmp_path / "sessions" / sid / "data_catalog_state.json"

    invalid_states = [
        {**_catalog_state(), "schema_version": "2"},
        {**_catalog_state(), "catalog_digest": "a" * 63},
        {**_catalog_state(), "datasets": {"market.quote": "NOT-A-DIGEST"}},
        {**_catalog_state(), "sources": {"": "c" * 64}},
        {**_catalog_state(), "unexpected": True},
    ]
    for invalid in invalid_states:
        with pytest.raises(ValueError):
            st.save_data_catalog_state(sid, invalid)

    with pytest.raises(TypeError):
        st.save_data_catalog_state(sid, [])
    with pytest.raises(KeyError):
        st.save_data_catalog_state("cs_000000000000", _catalog_state())

    state_path.write_text("{broken", encoding="utf-8")
    assert st.load_data_catalog_state(sid) is None
    state_path.write_text('{"schema_version":"1"}', encoding="utf-8")
    assert st.load_data_catalog_state(sid) is None
