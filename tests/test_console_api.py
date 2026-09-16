"""console API:会话 CRUD / send→事件落盘(FakeAgent)/ snapshot 流 / 诚实失败。"""
import asyncio
import json
import time
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from guanlan_v2.console.store import ConsoleStore
from guanlan_v2.console.api import build_console_router


class _Evt:
    def __init__(self, kind, payload=None):
        self.kind, self.payload = kind, payload


class FakeAgent:
    """计划(side_effect.plan)→ 工具(artifact)→ 文本 → done。"""
    def __init__(self):
        self.messages = []

    async def run_turn(self, text, confirm_callback=None, allowed_tools=None):
        yield _Evt("tool_call", {"name": "ww_plan_update", "args": {"todos": [{"text": "回测", "status": "in_progress"}]}})
        yield _Evt("tool_result", {"name": "ww_plan_update", "content": "计划已更新,1 项", "is_error": False,
                                   "side_effect": {"plan": [{"id": "t1", "text": "回测", "status": "in_progress"}]}})
        yield _Evt("tool_call", {"name": "ww_backtest", "args": {"expr": "rank(roe)"}})
        yield _Evt("tool_result", {"name": "ww_backtest", "content": "回测完成: 净年化 12.4%", "is_error": False,
                                   "side_effect": {"artifact": {"kind": "backtest_report", "page": "factor",
                                                                "channel": "workflow", "payload": {"expr": "rank(roe)"}, "ref": None}}})
        yield _Evt("text", "回测结果已就绪。")
        yield _Evt("done", None)


def _client(tmp_path):
    app = FastAPI()
    store = ConsoleStore(root=tmp_path)
    app.include_router(build_console_router(store=store, agent_factory=lambda sid: FakeAgent()))
    return TestClient(app), store


def test_sessions_crud(tmp_path):
    c, _ = _client(tmp_path)
    r = c.post("/console/sessions", json={"title": "测试"}).json()
    assert r["ok"] and r["meta"]["title"] == "测试"
    sid = r["meta"]["id"]
    assert any(m["id"] == sid for m in c.get("/console/sessions").json()["sessions"])
    assert c.request("DELETE", f"/console/sessions/{sid}").json()["ok"]


def test_send_runs_turn_and_persists_events(tmp_path):
    c, store = _client(tmp_path)
    sid = c.post("/console/sessions", json={}).json()["meta"]["id"]
    r = c.post("/console/send", json={"sid": sid, "text": "回测 rank(roe)"}).json()
    assert r["ok"] and r["sid"] == sid
    evs = []
    for _ in range(50):                      # 后台任务最多等 5s
        evs = store.read_events(sid)
        if any(e["type"] == "task_update" and e.get("status") == "done" for e in evs):
            break
        time.sleep(0.1)
    types = [e["type"] for e in evs]
    assert "user_msg" in types and "plan_update" in types and "agent_delta" in types
    user = next(e for e in evs if e["type"] == "user_msg")
    assert user["thinking_mode"] == "standard"
    tr = [e for e in evs if e["type"] == "tool_result" and e.get("artifact")]
    assert tr and tr[0]["artifact"]["kind"] == "backtest_report"
    assert store.get_meta(sid)["plan"][0]["text"] == "回测"


def test_sessions_patch_rename_group(tmp_path):
    c, store = _client(tmp_path)
    sid = c.post("/console/sessions", json={"title": "原名"}).json()["meta"]["id"]
    r = c.patch(f"/console/sessions/{sid}", json={"title": "新名", "group": "研究"}).json()
    assert r["ok"] and r["meta"]["title"] == "新名" and r["meta"]["group"] == "研究"
    assert store.get_meta(sid)["group"] == "研究"
    # group 置空串 = 取消分组;空标题诚实拒绝;未知会话诚实拒绝
    assert c.patch(f"/console/sessions/{sid}", json={"group": ""}).json()["meta"]["group"] == ""
    assert c.patch(f"/console/sessions/{sid}", json={"title": "  "}).json()["ok"] is False
    assert c.patch("/console/sessions/cs_nope", json={"title": "x"}).json()["ok"] is False
    assert c.patch(f"/console/sessions/{sid}", json={}).json()["ok"] is False


def test_sessions_list_carries_running(tmp_path):
    c, _ = _client(tmp_path)
    sid = c.post("/console/sessions", json={}).json()["meta"]["id"]
    m = next(x for x in c.get("/console/sessions").json()["sessions"] if x["id"] == sid)
    assert m["running"] is False   # 空闲会话 running=False;真跑态由 send 路径覆盖(上方用例)


def test_send_unknown_session_fails_honest(tmp_path):
    c, _ = _client(tmp_path)
    r = c.post("/console/send", json={"sid": "cs_nope", "text": "x"}).json()
    assert r["ok"] is False and "会话" in r["reason"]


def test_send_rejects_path_alias_of_real_session(tmp_path):
    c, store = _client(tmp_path)
    sid = c.post("/console/sessions", json={}).json()["meta"]["id"]
    alias = f"../sessions/{sid}"

    r = c.post("/console/send", json={"sid": alias, "text": "绕过重入门"}).json()

    assert r["ok"] is False and "会话" in r["reason"]
    assert store.read_events(sid) == []


def test_reserved_plan_approval_feed_is_read_only_to_chat_mutations(tmp_path):
    import guanlan_v2.console.api as capi

    c, store = _client(tmp_path)
    sid = capi._PLAN_APPROVALS_SID
    capi._ensure_reserved_session(store, sid, "计划人审")
    before = store.get_meta(sid)

    responses = [
        c.post("/console/send", json={"sid": sid, "text": "污染审批镜像"}),
        c.post("/console/steer", json={"sid": sid, "text": "改向"}),
        c.post("/console/cancel", json={"sid": sid}),
        c.patch(f"/console/sessions/{sid}", json={"title": "普通聊天"}),
        c.request("DELETE", f"/console/sessions/{sid}"),
    ]

    assert all(response.status_code == 403 for response in responses)
    assert all(response.json()["ok"] is False for response in responses)
    assert store.get_meta(sid) == before
    assert store.read_events(sid) == []


def test_send_rejects_unknown_thinking_mode_without_writing_event(tmp_path):
    c, store = _client(tmp_path)
    sid = c.post("/console/sessions", json={}).json()["meta"]["id"]
    r = c.post("/console/send", json={
        "sid": sid, "text": "x", "thinking_mode": "turbo",
    })
    assert r.status_code == 400
    assert r.json()["ok"] is False and "standard/pro" in r.json()["reason"]
    assert store.read_events(sid) == []


def test_pro_mode_swaps_client_for_one_turn_and_restores(tmp_path, monkeypatch):
    """Pro 只影响本轮：V4-Pro 客户端覆盖 compact/run_turn，finally 后恢复
    standard 客户端；下一轮 standard 不得继承 Pro。"""
    import guanlan_v2.console.api as capi

    standard = SimpleNamespace(provider="deepseek", model="deepseek-v4-flash")
    pro = SimpleNamespace(provider="deepseek", model="deepseek-v4-pro")

    class ClientAwareAgent:
        def __init__(self):
            self.messages = []
            self._client = standard
            self.seen = []

        async def run_turn(self, text, confirm_callback=None, allowed_tools=None):
            self.seen.append(self._client)
            yield _Evt("text", "完成")
            yield _Evt("done", None)

    agent = ClientAwareAgent()
    monkeypatch.setattr(
        capi, "_client_for_thinking_mode",
        lambda mode: pro if mode == "pro" else None,
    )
    app = FastAPI()
    store = ConsoleStore(root=tmp_path)
    app.include_router(build_console_router(store=store, agent_factory=lambda sid: agent))

    with TestClient(app) as c:
        sid = c.post("/console/sessions", json={}).json()["meta"]["id"]
        r = c.post("/console/send", json={
            "sid": sid, "text": "深想一下", "thinking_mode": "pro",
        }).json()
        assert r["ok"] is True and r["thinking_mode"] == "pro"
        evs = _wait(store, sid, _done)
        assert agent.seen == [pro]
        assert agent._client is standard
        user = next(e for e in evs if e["type"] == "user_msg")
        running = next(e for e in evs
                       if e["type"] == "task_update" and e.get("status") == "running")
        assert user["thinking_mode"] == "pro"
        assert running["thinking_mode"] == "pro"
        assert running["model_name"] == "deepseek/deepseek-v4-pro"

        assert c.post("/console/send", json={
            "sid": sid, "text": "普通回答", "thinking_mode": "standard",
        }).json()["ok"] is True
        _wait(store, sid, lambda es: len([
            e for e in es if e["type"] == "task_update" and e.get("status") == "done"
        ]) >= 2)
        assert agent.seen == [pro, standard]
        assert agent._client is standard


def test_stream_snapshot_first_frame(tmp_path, monkeypatch):
    import guanlan_v2.console.api as _capi
    monkeypatch.setattr(_capi, "_SSE_LIFETIME", 0)
    c, store = _client(tmp_path)
    sid = c.post("/console/sessions", json={}).json()["meta"]["id"]
    store.append_event(sid, "user_msg", text="历史一条")
    stored = store.upsert_evidence(sid, [{
        "title": "AIDC 跟踪", "institution": "测试证券",
        "publish_ts": "2026-08-14", "source_ref": "https://example.invalid/a.pdf",
        "view": "私有观点不得进入 SSE",
    }], event_id=1)
    with c.stream("GET", f"/console/stream/{sid}") as resp:
        buf = ""
        for chunk in resp.iter_text():
            buf += chunk
            if "\n\n" in buf:
                break
    head = buf.split("\n\n")[0]
    assert head.startswith("event: snapshot")
    data = json.loads(head.split("data: ", 1)[1])
    assert data["meta"]["id"] == sid and data["events"][0]["text"] == "历史一条"
    assert data["evidence"] == stored
    assert "view" not in data["evidence"][0]


def test_evidence_ref_rewrite_and_terminal_plan_autoclose(tmp_path):
    import guanlan_v2.console.api as capi

    store = ConsoleStore(tmp_path)
    sid = store.create_session()["id"]
    public = store.upsert_evidence(sid, [{
        "citation_id": "R1", "title": "光模块跟踪", "institution": "测试证券",
        "publish_ts": "2026-08-14", "source_ref": "https://example.invalid/r.pdf",
    }], event_id=3)
    evidence_id = public[0]["evidence_id"]
    rewritten = capi._rewrite_evidence_refs(
        "观点 [R1]，来源 https://example.invalid/r.pdf", store.load_evidence(sid),
    )
    assert rewritten == f"观点 [{evidence_id}]，来源 [{evidence_id}]"
    guarded = capi._validate_agent_output(
        f"半导体 60d 超额 +2.80pp 尚未转正 [{evidence_id}] [R-000000000000]",
        store.load_evidence(sid),
    )
    assert "正号数值" in guarded and "未进入会话证据账本" in guarded

    plan = [
        {"id": "1", "text": "收集证据", "status": "done"},
        {"id": "2", "text": "收尾：更新计划并交付", "status": "in_progress"},
    ]
    store.set_plan(sid, plan)
    closed = capi._auto_close_terminal_plan(store, sid)
    assert closed and all(row["status"] == "done" for row in closed)


def test_stream_snapshot_keeps_meta_plan_beyond_the_event_tail(tmp_path, monkeypatch):
    import guanlan_v2.console.api as _capi
    monkeypatch.setattr(_capi, "_SSE_LIFETIME", 0)
    c, store = _client(tmp_path)
    sid = c.post("/console/sessions", json={}).json()["meta"]["id"]
    todos = [
        {"id": f"t{i}", "text": f"第 {i + 1} 步：" + ("长计划内容" * 12),
         "status": ("done" if i < 20 else "in_progress" if i == 20 else "pending")}
        for i in range(64)
    ]
    store.set_plan(sid, todos)
    store.append_event(sid, "plan_update", todos=todos)
    for i in range(501):
        store.append_event(sid, "agent_delta", text=f"尾部事件 {i}")

    with c.stream("GET", f"/console/stream/{sid}") as resp:
        buf = ""
        for chunk in resp.iter_text():
            buf += chunk
            if "\n\n" in buf:
                break
    data = json.loads(buf.split("\n\n")[0].split("data: ", 1)[1])
    assert len(data["events"]) == 500
    assert all(e["type"] != "plan_update" for e in data["events"])
    assert data["meta"]["plan"] == todos


def _wait(store, sid, cond, tries=50):
    """轮询事件日志直到 cond(events) 为真(后台任务最多等 5s)。"""
    evs = []
    for _ in range(tries):
        evs = store.read_events(sid)
        if cond(evs):
            return evs
        time.sleep(0.1)
    return evs


def _done(evs):
    return any(e["type"] == "task_update" and e.get("status") == "done" for e in evs)


def test_runtime_data_catalog_is_injected_and_acknowledged_per_session(
    tmp_path, monkeypatch,
):
    import guanlan_v2.console.api as capi

    ack = {
        "schema_version": "1",
        "catalog_digest": "a" * 64,
        "datasets": {"market.quote": "b" * 64},
        "sources": {"quote.primary": "c" * 64},
    }

    class CatalogAgent:
        def __init__(self):
            self.messages = []
            self.seen = []
            self._guanlan_runtime_catalog_preflight = True

        async def run_turn(self, text, confirm_callback=None, allowed_tools=None):
            self.seen.append(text)
            yield _Evt("text", "完成")
            yield _Evt("done", None)

    def fake_preflight(store, sid):
        previous = store.load_data_catalog_state(sid)
        return ("[CATALOG-FIRST]\n" if previous is None else "[CATALOG-SAME]\n", ack)

    monkeypatch.setattr(capi, "_runtime_data_catalog_preflight", fake_preflight)
    agent = CatalogAgent()
    app = FastAPI()
    store = ConsoleStore(root=tmp_path)
    app.include_router(build_console_router(store=store, agent_factory=lambda sid: agent))

    with TestClient(app) as client:
        sid = client.post("/console/sessions", json={}).json()["meta"]["id"]
        assert client.post(
            "/console/send", json={"sid": sid, "text": "第一轮"}
        ).json()["ok"]
        _wait(store, sid, _done)
        assert "[CATALOG-FIRST]" in agent.seen[0]
        assert store.load_data_catalog_state(sid) == ack

        assert client.post(
            "/console/send", json={"sid": sid, "text": "第二轮"}
        ).json()["ok"]
        _wait(store, sid, lambda events: len([
            event for event in events
            if event["type"] == "task_update" and event.get("status") == "done"
        ]) >= 2)
        assert "[CATALOG-SAME]" in agent.seen[1]

        # A different session has an independent acknowledgement baseline.
        sid2 = client.post("/console/sessions", json={}).json()["meta"]["id"]
        assert client.post(
            "/console/send", json={"sid": sid2, "text": "另一会话"}
        ).json()["ok"]
        _wait(store, sid2, _done)
        assert "[CATALOG-FIRST]" in agent.seen[2]


def test_runtime_catalog_ack_waits_for_durable_agent_checkpoint(
    tmp_path, monkeypatch,
):
    import guanlan_v2.console.api as capi

    ack = {
        "schema_version": "1",
        "catalog_digest": "a" * 64,
        "datasets": {"market.quote": "b" * 64},
        "sources": {"quote.primary": "c" * 64},
    }

    class CatalogAgent:
        def __init__(self):
            self.messages = []
            self.seen = []
            self._guanlan_runtime_catalog_preflight = True

        async def run_turn(self, text, confirm_callback=None, allowed_tools=None):
            self.seen.append(text)
            yield _Evt("text", "完成")
            yield _Evt("done", None)

    def fake_preflight(store, sid):
        marker = "[CATALOG-FIRST]" if store.load_data_catalog_state(sid) is None \
            else "[CATALOG-SAME]"
        return marker + "\n", ack

    monkeypatch.setattr(capi, "_runtime_data_catalog_preflight", fake_preflight)
    agent = CatalogAgent()
    app = FastAPI()
    store = ConsoleStore(root=tmp_path)
    app.include_router(build_console_router(store=store, agent_factory=lambda sid: agent))

    def fail_checkpoint(sid, state):
        raise OSError("simulated checkpoint failure")

    monkeypatch.setattr(store, "save_buddy_state", fail_checkpoint)
    with TestClient(app) as client:
        sid = client.post("/console/sessions", json={}).json()["meta"]["id"]
        assert client.post(
            "/console/send", json={"sid": sid, "text": "第一轮"}
        ).json()["ok"]
        _wait(store, sid, _done)
        assert store.load_data_catalog_state(sid) is None

        assert client.post(
            "/console/send", json={"sid": sid, "text": "第二轮"}
        ).json()["ok"]
        _wait(store, sid, lambda events: len([
            event for event in events
            if event["type"] == "task_update" and event.get("status") == "done"
        ]) >= 2)
        assert "[CATALOG-FIRST]" in agent.seen[0]
        assert "[CATALOG-FIRST]" in agent.seen[1]


def test_custom_agent_does_not_probe_runtime_catalog_without_opt_in(
    tmp_path, monkeypatch,
):
    import guanlan_v2.console.api as capi

    class PlainAgent:
        def __init__(self):
            self.messages = []

        async def run_turn(self, text, confirm_callback=None, allowed_tools=None):
            yield _Evt("done", None)

    def should_not_run(store, sid):
        raise AssertionError("custom agent unexpectedly touched external catalog")

    monkeypatch.setattr(capi, "_runtime_data_catalog_preflight", should_not_run)
    app = FastAPI()
    store = ConsoleStore(root=tmp_path)
    app.include_router(build_console_router(store=store, agent_factory=lambda sid: PlainAgent()))
    with TestClient(app) as client:
        sid = client.post("/console/sessions", json={}).json()["meta"]["id"]
        assert client.post(
            "/console/send", json={"sid": sid, "text": "保持 hermetic"}
        ).json()["ok"]
        assert _done(_wait(store, sid, _done))


def test_confirm_flow(tmp_path):
    class ConfirmAgent:
        def __init__(self):
            self.messages = []

        async def run_turn(self, text, confirm_callback=None, allowed_tools=None):
            ok = await confirm_callback("ww_seats_decide", {"code": "300750"})
            yield _Evt("text", "已准,执行研判。" if ok else "已拒,跳过研判。")
            yield _Evt("done", None)

    app = FastAPI()
    store = ConsoleStore(root=tmp_path)
    app.include_router(build_console_router(store=store, agent_factory=lambda sid: ConfirmAgent()))
    with TestClient(app) as c:        # 上下文管理器:portal 跨请求存活,后台任务不被回收
        def run_one(choice):
            sid = c.post("/console/sessions", json={}).json()["meta"]["id"]
            assert c.post("/console/send", json={"sid": sid, "text": "研判 300750"}).json()["ok"]
            evs = _wait(store, sid, lambda es: any(e["type"] == "confirm_request" for e in es))
            req = [e for e in evs if e["type"] == "confirm_request"]
            assert req and req[0]["tool"] == "ww_seats_decide"
            assert req[0]["confirm_id"].startswith("cf_")
            missing = c.post("/console/confirm", json={
                "turn_id": req[0]["turn_id"], "choice": choice,
            })
            assert missing.status_code == 409
            assert missing.json()["reason_code"] == "confirm_instance_required"
            wrong = c.post("/console/confirm", json={
                "turn_id": req[0]["turn_id"],
                "confirm_id": "cf_" + "0" * 32,
                "choice": choice,
            })
            assert wrong.status_code == 409
            assert wrong.json()["reason_code"] == "confirm_instance_required"
            r = c.post("/console/confirm", json={
                "turn_id": req[0]["turn_id"],
                "confirm_id": req[0]["confirm_id"],
                "choice": choice,
            }).json()
            assert r["ok"] is True
            evs2 = _wait(store, sid, lambda es: any(e["type"] == "confirm_resolved" for e in es))
            rs = [e for e in evs2 if e["type"] == "confirm_resolved"]
            assert rs and rs[0]["turn_id"] == req[0]["turn_id"] and rs[0]["choice"] == choice
            assert rs[0]["confirm_id"] == req[0]["confirm_id"]
            assert rs[0]["tool"] == req[0]["tool"]
            evs = _wait(store, sid, _done)
            return [e["text"] for e in evs if e["type"] == "agent_delta"]

        assert any("已准" in t for t in run_one("y"))
        assert any("已拒" in t for t in run_one("n"))
        r = c.post("/console/confirm", json={
            "turn_id": "cs_nope", "confirm_id": "cf_" + "0" * 32,
            "choice": "y",
        })
        assert r.status_code == 200
        assert r.json()["ok"] is False and "no pending confirm" in r.json()["reason"]


def test_confirm_instances_are_serialized_and_stale_id_cannot_approve_next(tmp_path):
    class ConcurrentConfirmAgent:
        def __init__(self):
            self.messages = []
            self.results = None

        async def run_turn(self, text, confirm_callback=None, allowed_tools=None):
            self.results = await asyncio.gather(
                confirm_callback("ww_model_delete", {"id": "candidate-old"}),
                confirm_callback("ww_model_train", {"name": "candidate"}),
            )
            yield _Evt("text", "确认完成")
            yield _Evt("done", None)

    agent = ConcurrentConfirmAgent()
    app = FastAPI()
    store = ConsoleStore(root=tmp_path)
    app.include_router(build_console_router(
        store=store, agent_factory=lambda sid: agent,
    ))
    with TestClient(app) as client:
        sid = client.post("/console/sessions", json={}).json()["meta"]["id"]
        assert client.post("/console/send", json={
            "sid": sid, "text": "依次确认两项操作",
        }).json()["ok"]

        first_events = _wait(store, sid, lambda events: len([
            event for event in events if event["type"] == "confirm_request"
        ]) >= 1)
        first_requests = [
            event for event in first_events if event["type"] == "confirm_request"
        ]
        assert len(first_requests) == 1
        first = first_requests[0]
        assert client.post("/console/confirm", json={
            "turn_id": first["turn_id"],
            "confirm_id": first["confirm_id"],
            "choice": "y",
        }).json()["ok"]

        second_events = _wait(store, sid, lambda events: len([
            event for event in events if event["type"] == "confirm_request"
        ]) == 2)
        requests = [
            event for event in second_events if event["type"] == "confirm_request"
        ]
        second = requests[1]
        assert second["confirm_id"] != first["confirm_id"]

        stale = client.post("/console/confirm", json={
            "turn_id": second["turn_id"],
            "confirm_id": first["confirm_id"],
            "choice": "y",
        })
        assert stale.status_code == 409
        assert stale.json()["reason_code"] == "confirm_instance_required"

        assert client.post("/console/confirm", json={
            "turn_id": second["turn_id"],
            "confirm_id": second["confirm_id"],
            "choice": "y",
        }).json()["ok"]
        assert _done(_wait(store, sid, _done))
        assert agent.results == [True, True]

        resolved = [
            event for event in store.read_events(sid)
            if event["type"] == "confirm_resolved"
        ]
        assert [event["confirm_id"] for event in resolved] == [
            first["confirm_id"], second["confirm_id"],
        ]
        assert [event["tool"] for event in resolved] == [
            first["tool"], second["tool"],
        ]


def test_send_reentrancy_guard(tmp_path):
    gate = asyncio.Event()

    class SlowAgent:
        def __init__(self):
            self.messages = []

        async def run_turn(self, text, confirm_callback=None, allowed_tools=None):
            await gate.wait()
            yield _Evt("text", "完")
            yield _Evt("done", None)

    app = FastAPI()
    store = ConsoleStore(root=tmp_path)
    app.include_router(build_console_router(store=store, agent_factory=lambda sid: SlowAgent()))
    with TestClient(app) as c:
        sid = c.post("/console/sessions", json={}).json()["meta"]["id"]
        assert c.post("/console/send", json={"sid": sid, "text": "第一轮"}).json()["ok"]
        r2 = c.post("/console/send", json={"sid": sid, "text": "第二轮"}).json()
        assert r2["ok"] is False and "在跑" in r2["reason"]
        c.portal.call(gate.set)       # 经 portal 在 loop 线程 set(跨线程直接 set 不可靠)
        evs = _wait(store, sid, _done)
        assert _done(evs)


def test_running_workspace_cannot_change_security_subject(tmp_path):
    gate = asyncio.Event()

    class SlowAgent:
        def __init__(self):
            self.messages = []

        async def run_turn(self, text, confirm_callback=None,
                           allowed_tools=None):
            await gate.wait()
            yield _Evt("done", None)

    app = FastAPI()
    store = ConsoleStore(root=tmp_path)
    app.include_router(build_console_router(
        store=store, agent_factory=lambda sid: SlowAgent(),
    ))
    with TestClient(app) as c:
        workspace = c.post("/console/workspaces", json={
            "kind": "stock", "code": "600000", "name": "浦发银行",
        }).json()["workspace"]
        sid = c.post("/console/sessions", json={
            "workspace_id": workspace["id"],
        }).json()["meta"]["id"]
        assert c.post("/console/send", json={
            "sid": sid, "text": "研究当前公司",
        }).json()["ok"]
        blocked = c.patch(
            f"/console/workspaces/{workspace['id']}",
            json={"code": "600519"},
        )
        assert blocked.status_code == 409
        assert "暂不能切换标的" in blocked.json()["reason"]
        assert store.workspace_store.get(workspace["id"])["code"] == "SH600000"
        c.portal.call(gate.set)
        assert _done(_wait(store, sid, _done))


def test_cancel_waits_for_agent_stream_security_boundary_before_done(tmp_path):
    gate = asyncio.Event()
    closed = []

    class ControlledStream:
        def __init__(self):
            self.index = 0

        def __aiter__(self):
            return self

        async def __anext__(self):
            self.index += 1
            if self.index == 1:
                return _Evt("tool_call", {"name": "probe", "args": {}})
            if self.index == 2:
                await gate.wait()
                return _Evt("tool_result", {
                    "name": "probe", "content": "ok", "is_error": False,
                    "side_effect": {},
                })
            raise StopAsyncIteration

        async def aclose(self):
            await asyncio.sleep(0)
            closed.append("security-boundary-complete")

    class Agent:
        def __init__(self):
            self.messages = []

        def run_turn(self, text, confirm_callback=None, allowed_tools=None):
            return ControlledStream()

    app = FastAPI()
    store = ConsoleStore(root=tmp_path)
    app.include_router(build_console_router(
        store=store, agent_factory=lambda sid: Agent(),
    ))
    with TestClient(app) as c:
        sid = c.post("/console/sessions", json={}).json()["meta"]["id"]
        assert c.post("/console/send", json={
            "sid": sid, "text": "probe",
        }).json()["ok"]
        _wait(store, sid, lambda events: any(
            event["type"] == "tool_call" for event in events
        ))
        response = c.post("/console/cancel", json={"sid": sid})
        assert response.status_code == 202
        assert response.json()["deferred_until_tool_returns"] is True
        c.portal.call(gate.set)
        assert _done(_wait(store, sid, _done))
        assert closed == ["security-boundary-complete"]


def test_deferred_cancel_stops_on_tool_error_boundary(tmp_path):
    gate = asyncio.Event()
    emitted_after_error = []

    class ErrorStream:
        def __init__(self):
            self.index = 0

        def __aiter__(self):
            return self

        async def __anext__(self):
            self.index += 1
            if self.index == 1:
                return _Evt("tool_call", {"name": "probe", "args": {}})
            if self.index == 2:
                await gate.wait()
                return _Evt("error", "tool failed")
            emitted_after_error.append(True)
            return _Evt("text", "must-not-continue")

        async def aclose(self):
            return None

    class Agent:
        def __init__(self):
            self.messages = []

        def run_turn(self, text, confirm_callback=None, allowed_tools=None):
            return ErrorStream()

    app = FastAPI()
    store = ConsoleStore(root=tmp_path)
    app.include_router(build_console_router(
        store=store, agent_factory=lambda sid: Agent(),
    ))
    with TestClient(app) as c:
        sid = c.post("/console/sessions", json={}).json()["meta"]["id"]
        assert c.post("/console/send", json={"sid": sid, "text": "probe"}).json()["ok"]
        _wait(store, sid, lambda events: any(
            event["type"] == "tool_call" for event in events
        ))
        assert c.post("/console/cancel", json={"sid": sid}).json()[
            "deferred_until_tool_returns"
        ] is True
        c.portal.call(gate.set)
        events = _wait(store, sid, _done)
        assert _done(events)
        assert emitted_after_error == []
        assert not any(
            event.get("type") == "agent_delta"
            and "must-not-continue" in str(event.get("text") or "")
            for event in events
        )


def test_finalizing_turn_rejects_steer_and_does_not_become_cancelled(tmp_path):
    close_entered = asyncio.Event()
    close_gate = asyncio.Event()

    class FinalizingStream:
        def __init__(self):
            self.index = 0

        def __aiter__(self):
            return self

        async def __anext__(self):
            self.index += 1
            if self.index == 1:
                return _Evt("done", None)
            raise StopAsyncIteration

        async def aclose(self):
            close_entered.set()
            await close_gate.wait()

    class Agent:
        def __init__(self):
            self.messages = []

        def run_turn(self, text, confirm_callback=None, allowed_tools=None):
            return FinalizingStream()

    app = FastAPI()
    store = ConsoleStore(root=tmp_path)
    app.include_router(build_console_router(
        store=store, agent_factory=lambda sid: Agent(),
    ))
    with TestClient(app) as c:
        sid = c.post("/console/sessions", json={}).json()["meta"]["id"]
        assert c.post("/console/send", json={"sid": sid, "text": "probe"}).json()["ok"]
        c.portal.call(close_entered.wait)
        cancel = c.post("/console/cancel", json={"sid": sid})
        assert cancel.status_code == 200
        assert cancel.json()["already_completed"] is True
        steer = c.post("/console/steer", json={"sid": sid, "text": "late"})
        assert steer.status_code == 409
        c.portal.call(close_gate.set)
        events = _wait(store, sid, _done)
        terminal = [event for event in events
                    if event.get("type") == "task_update"
                    and event.get("status") == "done"][-1]
        assert terminal.get("cancelled") is False


def test_double_cancel_cannot_interrupt_slow_stream_close(tmp_path):
    close_entered = asyncio.Event()
    close_gate = asyncio.Event()

    class SlowCloseStream:
        def __init__(self):
            self.index = 0

        def __aiter__(self):
            return self

        async def __anext__(self):
            self.index += 1
            if self.index == 1:
                return _Evt("text", "started")
            await asyncio.Event().wait()

        async def aclose(self):
            close_entered.set()
            await close_gate.wait()

    class Agent:
        def __init__(self):
            self.messages = []

        def run_turn(self, text, confirm_callback=None, allowed_tools=None):
            return SlowCloseStream()

    app = FastAPI()
    store = ConsoleStore(root=tmp_path)
    app.include_router(build_console_router(
        store=store, agent_factory=lambda sid: Agent(),
    ))
    with TestClient(app) as c:
        sid = c.post("/console/sessions", json={}).json()["meta"]["id"]
        assert c.post("/console/send", json={"sid": sid, "text": "probe"}).json()["ok"]
        _wait(store, sid, lambda events: any(
            event.get("type") == "agent_delta" for event in events
        ))
        first = c.post("/console/cancel", json={"sid": sid})
        assert first.status_code == 202
        c.portal.call(close_entered.wait)
        second = c.post("/console/cancel", json={"sid": sid})
        assert second.status_code == 202
        assert second.json()["already_requested"] is True
        c.portal.call(close_gate.set)
        assert _done(_wait(store, sid, _done))
        session = next(
            row for row in c.get("/console/sessions").json()["sessions"]
            if row["id"] == sid
        )
        assert session["running"] is False


def test_codex_boundary_failure_blocks_steer_successor(tmp_path):
    tool_gate = asyncio.Event()
    calls = []

    class Stream:
        def __init__(self):
            self.index = 0

        def __aiter__(self):
            return self

        async def __anext__(self):
            self.index += 1
            if self.index == 1:
                return _Evt("tool_call", {"name": "probe", "args": {}})
            if self.index == 2:
                await tool_gate.wait()
                return _Evt("tool_result", {
                    "name": "probe", "content": "old company data",
                    "is_error": False, "side_effect": {},
                })
            raise StopAsyncIteration

        async def aclose(self):
            return None

    class PoisonedExternalAgent:
        manages_own_history = True
        retains_interrupted_turn = True

        def run_turn(self, text, confirm_callback=None, allowed_tools=None,
                     thinking_mode="standard"):
            calls.append(text)
            return Stream()

        def checkpoint(self, event_id):
            return False

    agent = PoisonedExternalAgent()
    app = FastAPI()
    store = ConsoleStore(root=tmp_path)
    app.include_router(build_console_router(
        store=store, agent_factory=lambda sid: agent,
    ))
    with TestClient(app) as c:
        sid = c.post("/console/sessions", json={}).json()["meta"]["id"]
        assert c.post("/console/send", json={"sid": sid, "text": "old"}).json()["ok"]
        _wait(store, sid, lambda events: any(
            event.get("type") == "tool_call" for event in events
        ))
        steer = c.post("/console/steer", json={"sid": sid, "text": "new"})
        assert steer.status_code == 202
        c.portal.call(tool_gate.set)
        events = _wait(store, sid, _done)
        assert len(calls) == 1
        assert any(
            event.get("status") == "error"
            and "中断边界未能验证" in str(event.get("note") or "")
            for event in events
        )
        terminal = [event for event in events
                    if event.get("type") == "task_update"
                    and event.get("status") == "done"][-1]
        assert terminal.get("keep_busy") is not True


def test_agent_raise_emits_error_and_recovers(tmp_path):
    class BoomAgent:
        def __init__(self):
            self.messages = []

        async def run_turn(self, text, confirm_callback=None, allowed_tools=None):
            raise RuntimeError("boom")
            yield  # pragma: no cover — 不可达,仅保持 async generator 形态

    app = FastAPI()
    store = ConsoleStore(root=tmp_path)
    app.include_router(build_console_router(store=store, agent_factory=lambda sid: BoomAgent()))
    with TestClient(app) as c:
        sid = c.post("/console/sessions", json={}).json()["meta"]["id"]
        assert c.post("/console/send", json={"sid": sid, "text": "x"}).json()["ok"]
        evs = _wait(store, sid, _done)
        errs = [e for e in evs if e["type"] == "task_update" and e.get("status") == "error"]
        assert errs and "boom" in errs[0]["note"]
        dones = [e for e in evs if e["type"] == "task_update" and e.get("status") == "done"]
        assert dones and dones[-1]["ok"] is False
        assert store.get_meta(sid)["status"] == "idle"
        # running 已清,可再次发令并跑完第二轮
        assert c.post("/console/send", json={"sid": sid, "text": "y"}).json()["ok"]
        evs = _wait(store, sid, lambda es: len(
            [e for e in es if e["type"] == "task_update" and e.get("status") == "done"]) >= 2)
        assert len([e for e in evs if e["type"] == "task_update" and e.get("status") == "done"]) >= 2


def test_background_report_lifecycle(tmp_path, monkeypatch):
    """se.background → 后台事件链:task_update(kind=report,running) → tool_result(report_md) → task_update(done)。"""
    import guanlan_v2.console.api as capi
    monkeypatch.setattr(capi, "_call_buddy_report",
                        lambda code, asof: {"ok": True, "content": "Report written. 评级4/10",
                                            "md_path": "G:\\guanlan-v2\\out\\SZ300750_2026-06-13.md"})
    monkeypatch.setattr(capi, "_archive_research", lambda **kw: True)
    monkeypatch.setattr(capi, "_BG_PROGRESS_POLL", 0.05)

    class BgAgent:
        def __init__(self):
            self.messages = []
        async def run_turn(self, text, confirm_callback=None, allowed_tools=None):
            yield _Evt("tool_call", {"name": "ww_report_run", "args": {"code": "SZ300750"}})
            yield _Evt("tool_result", {"name": "ww_report_run", "content": "研报已受理", "is_error": False,
                                       "side_effect": {"background": {"kind": "report", "code": "SZ300750",
                                                                      "name": "宁德时代", "asof": None}}})
            yield _Evt("done", None)

    app = FastAPI(); store = ConsoleStore(root=tmp_path)
    app.include_router(build_console_router(store=store, agent_factory=lambda sid: BgAgent()))
    with TestClient(app) as c:
        sid = c.post("/console/sessions", json={}).json()["meta"]["id"]
        c.post("/console/send", json={"sid": sid, "text": "给宁德写研报"})
        evs = []
        for _ in range(80):
            evs = store.read_events(sid)
            if any(e["type"] == "task_update" and e.get("kind") == "report" and e.get("status") == "done" for e in evs):
                break
            time.sleep(0.1)
    kinds = [(e["type"], e.get("kind"), e.get("status")) for e in evs]
    assert ("task_update", "report", "running") in kinds
    art = [e for e in evs if (e.get("artifact") or {}).get("kind") == "report_md"][0]
    assert art["artifact"]["payload"]["code"] == "SZ300750" and art["artifact"]["payload"]["path"].endswith(".md")
    assert ("task_update", "report", "done") in kinds
    # meta.bg 留档:起跑即写(started),finally 终态合并(status/ok/ended)——有界轮询等 finally 落盘
    rec = None
    for _ in range(50):
        bgs = (store.get_meta(sid) or {}).get("bg") or {}
        rec = next(iter(bgs.values()), None)
        if rec and rec.get("status") == "done":
            break
        time.sleep(0.1)
    assert rec and rec["status"] == "done" and rec["ok"] is True
    assert rec["kind"] == "report" and rec["code"] == "SZ300750"
    assert rec.get("started") and rec.get("ended")


def test_background_report_failure_honest(tmp_path, monkeypatch):
    import guanlan_v2.console.api as capi
    monkeypatch.setattr(capi, "_call_buddy_report", lambda code, asof: {"ok": False, "content": "Report failed (exit 1)"})
    monkeypatch.setattr(capi, "_BG_PROGRESS_POLL", 0.05)

    class BgAgent:
        def __init__(self):
            self.messages = []
        async def run_turn(self, text, confirm_callback=None, allowed_tools=None):
            yield _Evt("tool_result", {"name": "ww_report_run", "content": "x", "is_error": False,
                                       "side_effect": {"background": {"kind": "report", "code": "SZ000001", "name": "", "asof": None}}})
            yield _Evt("done", None)

    app = FastAPI(); store = ConsoleStore(root=tmp_path)
    app.include_router(build_console_router(store=store, agent_factory=lambda sid: BgAgent()))
    with TestClient(app) as c:
        sid = c.post("/console/sessions", json={}).json()["meta"]["id"]
        c.post("/console/send", json={"sid": sid, "text": "x"})
        evs = []
        for _ in range(80):
            evs = store.read_events(sid)
            if any(e["type"] == "task_update" and e.get("kind") == "report" and e.get("status") == "error" for e in evs):
                break
            time.sleep(0.1)
    errs = [e for e in evs if e["type"] == "task_update" and e.get("kind") == "report" and e.get("status") == "error"]
    assert errs and "failed" in errs[0]["note"]


def test_condenser_triggers_and_emits(tmp_path):
    class FatAgent:
        def __init__(self):
            self.messages = [type("M", (), {"role": "user", "content": "x" * 800})() for _ in range(40)]
            self.compacted = False
        async def compact(self):
            self.compacted = True
            self.messages = self.messages[:1]
            return "前文摘要:聊了很多动量因子"
        async def run_turn(self, text, confirm_callback=None, allowed_tools=None):
            yield _Evt("text", "ok")
            yield _Evt("done", None)
    agents = {}
    def factory(sid):
        agents[sid] = FatAgent(); return agents[sid]
    app = FastAPI(); store = ConsoleStore(root=tmp_path)
    app.include_router(build_console_router(store=store, agent_factory=factory))
    with TestClient(app) as c:
        sid = c.post("/console/sessions", json={}).json()["meta"]["id"]
        c.post("/console/send", json={"sid": sid, "text": "继续"})
        evs = []
        for _ in range(50):
            evs = store.read_events(sid)
            if any(e["type"] == "task_update" and e.get("status") == "done" for e in evs):
                break
            time.sleep(0.1)
    assert list(agents.values())[0].compacted is True
    assert any(e["type"] == "condensation" and "摘要" in e.get("summary", "") for e in evs)


def test_memory_injected_into_turn(tmp_path, monkeypatch):
    import guanlan_v2.console.tools as ct2
    monkeypatch.setattr(ct2, "_MEMORY_PATH", tmp_path / "memory.md")
    ct2.memory_write_impl(text="用户只看 csi300")
    seen = {}
    class EchoAgent:
        def __init__(self):
            self.messages = []
        async def run_turn(self, text, confirm_callback=None, allowed_tools=None):
            seen["text"] = text
            yield _Evt("done", None)
    app = FastAPI(); store = ConsoleStore(root=tmp_path)
    app.include_router(build_console_router(store=store, agent_factory=lambda sid: EchoAgent()))
    with TestClient(app) as c:
        sid = c.post("/console/sessions", json={}).json()["meta"]["id"]
        c.post("/console/send", json={"sid": sid, "text": "选股"})
        for _ in range(50):
            if "text" in seen:
                break
            time.sleep(0.1)
    assert "csi300" in seen["text"] and "[帷幄记忆·全局]" in seen["text"]


class _BareAgent:
    """只有 messages=[] 的轻量对象,给 _reseed 直测用(真实 Message 可导入的环境)。"""
    def __init__(self):
        self.messages = []


def test_reseed_condensation_summary_seeds_first():
    """事件序对齐真实压缩时机:user_msg(触发提问)→ condensation(轮开头)→ 本轮回答。"""
    from guanlan_v2.console.api import _reseed
    events = [
        {"type": "user_msg", "text": "更早问题"},
        {"type": "agent_delta", "text": "更早回答"},
        {"type": "user_msg", "text": "触发压缩的提问"},
        {"type": "condensation", "summary": "S摘要:聊过动量因子与csi300池"},
        {"type": "agent_delta", "text": "压缩轮的回答"},
    ]
    a = _BareAgent()
    _reseed(a, events)
    assert a.messages, "financial_analyst.buddy.agent.Message 可导入,reseed 应灌入消息"
    assert "前情摘要" in a.messages[0].content and "S摘要:聊过动量因子" in a.messages[0].content
    assert a.messages[0].role == "user"
    rest = [(m.role, m.content) for m in a.messages[1:]]
    # 摘要后第一条 = 触发压缩那轮的提问(不丢),其后是该轮回答
    assert rest == [("user", "触发压缩的提问"), ("assistant", "压缩轮的回答")]
    assert all("更早" not in m.content for m in a.messages[1:])   # 摘要前史不重灌


def test_reseed_without_condensation_keeps_old_behavior():
    from guanlan_v2.console.api import _reseed
    events = [{"type": "user_msg", "text": "问"}, {"type": "agent_delta", "text": "答"}]
    a = _BareAgent()
    _reseed(a, events)
    assert [m.content for m in a.messages] == ["问", "答"]
    assert [m.role for m in a.messages] == ["user", "assistant"]
    assert all("前情摘要" not in m.content for m in a.messages)


def test_router_restart_restores_complete_private_tool_history(tmp_path):
    """新 router/新 Agent 模拟进程重启：恢复 RC、tool_calls、tool_call_id 和完整结果。

    原始推理链只在 buddy_state sidecar，展示事件不得携带 reasoning_content。
    """
    from financial_analyst.buddy.agent import Message

    class ProtocolAgent:
        def __init__(self, produce=False):
            self.messages = []
            self.produce = produce
            self.seen_before = None

        async def run_turn(self, text, confirm_callback=None, allowed_tools=None):
            self.seen_before = [m.to_litellm() for m in self.messages]
            self.messages.append(Message(role="user", content=text))
            if self.produce:
                assistant_call = {
                    "role": "assistant", "content": "",
                    "reasoning_content": "先查询完整数据",
                    "tool_calls": [{
                        "id": "call_restart_1", "type": "function",
                        "function": {"name": "quote_lookup", "arguments": "{\"code\":\"SZ300750\"}"},
                    }],
                }
                tool = {"role": "tool", "tool_call_id": "call_restart_1",
                        "content": "完整工具结果：现价 251.20，成交额 42 亿"}
                final = {"role": "assistant", "content": "已核对。",
                         "reasoning_content": "综合行情后回答"}
                self.messages.extend([
                    Message(role="assistant", content="", raw=assistant_call),
                    Message(role="tool", content=tool["content"], raw=tool),
                    Message(role="assistant", content=final["content"], raw=final),
                ])
                yield _Evt("tool_call", {"name": "quote_lookup", "args": {"code": "SZ300750"}})
                yield _Evt("tool_result", {"name": "quote_lookup", "content": tool["content"],
                                           "is_error": False, "side_effect": None})
                yield _Evt("text", final["content"])
            yield _Evt("done", None)

    store = ConsoleStore(root=tmp_path)
    first = ProtocolAgent(produce=True)
    app1 = FastAPI()
    app1.include_router(build_console_router(store=store, agent_factory=lambda sid: first))
    with TestClient(app1) as c1:
        sid = c1.post("/console/sessions", json={}).json()["meta"]["id"]
        assert c1.post("/console/send", json={"sid": sid, "text": "查宁德"}).json()["ok"]
        assert _done(_wait(store, sid, _done))

    state = store.load_buddy_state(sid)
    assert state and state["messages"][1]["reasoning_content"] == "先查询完整数据"
    assert state["messages"][2]["tool_call_id"] == "call_restart_1"
    assert state["messages"][3]["reasoning_content"] == "综合行情后回答"
    assert all("reasoning_content" not in e for e in store.read_events(sid))

    second = ProtocolAgent(produce=False)
    app2 = FastAPI()
    app2.include_router(build_console_router(store=store, agent_factory=lambda sid: second))
    with TestClient(app2) as c2:
        assert c2.post("/console/send", json={"sid": sid, "text": "继续"}).json()["ok"]
        assert _done(_wait(store, sid, lambda es: len([
            e for e in es if e["type"] == "task_update" and e.get("status") == "done"
        ]) >= 2))

    restored = second.seen_before
    assert restored[1]["tool_calls"][0]["id"] == "call_restart_1"
    assert restored[1]["reasoning_content"] == "先查询完整数据"
    assert restored[2]["content"].startswith("完整工具结果")
    assert restored[3]["reasoning_content"] == "综合行情后回答"


def test_evict_lru_skips_running_sid():
    from collections import OrderedDict
    from guanlan_v2.console.api import _evict_lru
    agents = OrderedDict((f"s{i}", object()) for i in range(13))
    _evict_lru(agents, running={"s0"}, cap=12)   # 最旧 s0 在跑 → 逐出第二旧 s1
    assert len(agents) == 12
    assert "s0" in agents and "s1" not in agents


def test_select_memory_lines_keyed_always_present():
    """超大文件:全部常驻(keyed)行必现;易逝只取最近 N 条。"""
    from guanlan_v2.console.api import _select_memory_lines, _INJECT_N_UNKEYED
    text = "- [2026-06-01] (pool) 只看沪深300、月频\n"
    for i in range(40):
        text += f"- [2026-06-02] 临时笔记{i}\n"
    out = _select_memory_lines(text)
    assert "只看沪深300" in out
    assert "临时笔记39" in out
    assert "临时笔记0" not in out
    kept_unkeyed = [l for l in out.splitlines() if "临时笔记" in l]
    assert len(kept_unkeyed) == _INJECT_N_UNKEYED


def test_select_memory_lines_no_midline_cut():
    """整行截断:输出每行都以 '- ' 开头(无从行中间切出的半行)。"""
    from guanlan_v2.console.api import _select_memory_lines
    text = "".join(f"- [2026-06-02] 笔记{i} {'x'*200}\n" for i in range(40))
    out = _select_memory_lines(text)
    for ln in out.splitlines():
        assert ln.startswith("- "), ln


def test_select_memory_lines_keyed_budget_clamp():
    """常驻总量超预算才丢最旧常驻,并加诚实标注(罕见路径)。"""
    from guanlan_v2.console.api import _select_memory_lines, _INJECT_KEYED_MAX_CHARS
    text = "".join(f"- [2026-06-02] (k{i}) {'y'*270}\n" for i in range(40))
    out = _select_memory_lines(text)
    assert len(out) <= _INJECT_KEYED_MAX_CHARS + 200
    assert "超注入预算" in out
    assert "(k39)" in out and "(k0)" not in out


def test_memory_block_large_file_recalls_keyed(tmp_path, monkeypatch):
    """端到端:memory.md 远超旧 2000 窗口时,_memory_block 仍注入老的常驻偏好。"""
    import guanlan_v2.console.tools as ct
    from guanlan_v2.console.api import _memory_block
    monkeypatch.setattr(ct, "_MEMORY_PATH", tmp_path / "memory.md")
    body = "- [2026-06-01] (pool) 只看沪深300、月频\n"
    for i in range(60):
        body += f"- [2026-06-02] 噪声笔记{i} {'z'*40}\n"
    (tmp_path / "memory.md").write_text(body, encoding="utf-8")
    blk = _memory_block("cs_none")
    assert "[帷幄记忆·全局]" in blk and "只看沪深300" in blk


def test_evict_lru_all_running_no_evict():
    from collections import OrderedDict
    from guanlan_v2.console.api import _evict_lru
    agents = OrderedDict((f"s{i}", object()) for i in range(13))
    _evict_lru(agents, running=set(agents), cap=12)
    assert len(agents) == 13                      # 全在跑:宁可超限不丢史


def test_background_report_dedup(tmp_path, monkeypatch):
    """_bg_inflight 去重:同会话同 code 已在跑 → 第二次直接 error 事件,不重复起跑。"""
    import guanlan_v2.console.api as capi

    def slow_report(code, asof):
        time.sleep(0.5)
        return {"ok": True, "content": "ok", "md_path": "G:\\guanlan-v2\\out\\SZ300750_x.md"}

    monkeypatch.setattr(capi, "_call_buddy_report", slow_report)
    monkeypatch.setattr(capi, "_archive_research", lambda **kw: True)
    monkeypatch.setattr(capi, "_BG_PROGRESS_POLL", 0.05)

    class BgAgent:
        def __init__(self):
            self.messages = []
        async def run_turn(self, text, confirm_callback=None, allowed_tools=None):
            yield _Evt("tool_result", {"name": "ww_report_run", "content": "研报已受理", "is_error": False,
                                       "side_effect": {"background": {"kind": "report", "code": "SZ300750",
                                                                      "name": "宁德时代", "asof": None}}})
            yield _Evt("done", None)

    app = FastAPI(); store = ConsoleStore(root=tmp_path)
    app.include_router(build_console_router(store=store, agent_factory=lambda sid: BgAgent()))
    with TestClient(app) as c:
        sid = c.post("/console/sessions", json={}).json()["meta"]["id"]
        # 预占:模拟「本会话」已有同 code 研报在跑(异会话撞车=搭车,另测)
        capi._bg_inflight["report:SZ300750"] = {"sid": sid, "watchers": set(), "bg_id": "bg_owner"}
        try:
            c.post("/console/send", json={"sid": sid, "text": "再写一份研报"})
            evs = []
            for _ in range(80):
                evs = store.read_events(sid)
                if any(e["type"] == "task_update" and e.get("kind") == "report"
                       and e.get("status") == "error" for e in evs):
                    break
                time.sleep(0.1)
            errs = [e for e in evs if e["type"] == "task_update" and e.get("kind") == "report"
                    and e.get("status") == "error"]
            assert errs and "已有研报在跑" in errs[0]["note"]
        finally:
            capi._bg_inflight.pop("report:SZ300750", None)


def test_background_progress_stale_filtered(tmp_path, monkeypatch):
    """陈旧进度文件(ts < 本次 t0)被过滤:不发 progress=1.0 的 running 假进度。"""
    import json as _json
    import guanlan_v2.console.api as capi
    monkeypatch.setattr(capi, "_OUT_DIR", tmp_path)
    (tmp_path / "SZ300750_progress.json").write_text(
        _json.dumps({"total": 16, "done": 16, "fail": 0, "ts": 1}), encoding="utf-8")

    def slow_report(code, asof):
        time.sleep(0.3)
        return {"ok": True, "content": "ok", "md_path": "G:\\guanlan-v2\\out\\SZ300750_x.md"}

    monkeypatch.setattr(capi, "_call_buddy_report", slow_report)
    monkeypatch.setattr(capi, "_archive_research", lambda **kw: True)
    monkeypatch.setattr(capi, "_BG_PROGRESS_POLL", 0.05)

    class BgAgent:
        def __init__(self):
            self.messages = []
        async def run_turn(self, text, confirm_callback=None, allowed_tools=None):
            yield _Evt("tool_result", {"name": "ww_report_run", "content": "研报已受理", "is_error": False,
                                       "side_effect": {"background": {"kind": "report", "code": "SZ300750",
                                                                      "name": "宁德时代", "asof": None}}})
            yield _Evt("done", None)

    app = FastAPI(); store = ConsoleStore(root=tmp_path)
    app.include_router(build_console_router(store=store, agent_factory=lambda sid: BgAgent()))
    with TestClient(app) as c:
        sid = c.post("/console/sessions", json={}).json()["meta"]["id"]
        c.post("/console/send", json={"sid": sid, "text": "写研报"})
        evs = []
        for _ in range(80):
            evs = store.read_events(sid)
            if any(e["type"] == "task_update" and e.get("kind") == "report"
                   and e.get("status") == "done" for e in evs):
                break
            time.sleep(0.1)
    assert any(e["type"] == "task_update" and e.get("kind") == "report"
               and e.get("status") == "done" for e in evs)
    stale = [e for e in evs if e["type"] == "task_update" and e.get("kind") == "report"
             and e.get("status") == "running" and e.get("progress") == 1.0]
    assert stale == []   # 陈旧快照被 ts>=t0 过滤,无假进度


def test_startup_scan_marks_interrupted_bg(tmp_path):
    """重建 router(=进程重启)→ meta.bg 里 running 的任务标 error+note 中断,并补 task_update 事件;done 条目不动。"""
    store = ConsoleStore(root=tmp_path)
    sid = store.create_session()["id"]
    store.merge_meta_sub(sid, "bg", "bg_x", {"kind": "report", "code": "SZ000001", "status": "running"})
    store.merge_meta_sub(sid, "bg", "bg_done", {"kind": "report", "code": "SH600519", "status": "done", "ok": True})
    app = FastAPI()
    app.include_router(build_console_router(store=store, agent_factory=lambda s: FakeAgent()))
    bg = store.get_meta(sid)["bg"]
    assert bg["bg_x"]["status"] == "error" and bg["bg_x"]["ok"] is False and "重启" in bg["bg_x"]["note"]
    assert bg["bg_done"]["status"] == "done" and "note" not in bg["bg_done"]   # 终态条目不被扫描误伤
    evs = store.read_events(sid)
    errs = [e for e in evs if e["type"] == "task_update" and e.get("status") == "error"]
    assert errs and errs[-1]["task_id"] == "bg_x" and "重启" in errs[-1]["note"]
    assert errs[-1]["kind"] == "report" and errs[-1]["code"] == "SZ000001" and errs[-1]["ok"] is False


def test_startup_scan_tolerates_bad_bg(tmp_path):
    """坏会话(meta.bg 非 dict)只跳过自己:扫描继续,其余会话的 running 条目仍被正确标中断。"""
    store = ConsoleStore(root=tmp_path)
    bad = store.create_session()["id"]
    store.merge_meta(bad, bg="坏")   # 脏数据:bg 不是 dict
    good = store.create_session()["id"]
    store.merge_meta_sub(good, "bg", "bg_y", {"kind": "report", "code": "SZ000002", "status": "running"})
    app = FastAPI()
    app.include_router(build_console_router(store=store, agent_factory=lambda s: FakeAgent()))
    b = store.get_meta(good)["bg"]["bg_y"]
    assert b["status"] == "error" and b["ok"] is False and "重启" in b["note"]
    errs = [e for e in store.read_events(good) if e["type"] == "task_update" and e.get("status") == "error"]
    assert errs and errs[-1]["task_id"] == "bg_y"
    assert store.get_meta(bad)["bg"] == "坏"           # 脏数据原样留着:不炸、不改
    assert store.read_events(bad) == []                # 坏会话不被误注事件


def test_background_report_carpool_and_delete_guard(tmp_path, monkeypatch):
    """异会话同 code 撞车 → 搭车:B 收 running 通知(非 error);完成后 A、B 各落 done+report_md;
    in-flight 期间 A(发起)与 B(搭车)都不可删;B 再发同 code 维持拒绝口径。"""
    import threading
    import guanlan_v2.console.api as capi

    gate = threading.Event()   # 卡门:executor 线程等 set,保证 in-flight 窗口可观测
    md = tmp_path / "SZ000001_x.md"
    md.write_text("# 研报", encoding="utf-8")

    def gated_report(code, asof):
        gate.wait(timeout=120)   # 超时仅兜底防卡死;快乐路径事件驱动,gate.set 即放行不多等
        return {"ok": True, "content": "研报完成: SZ000001_x.md", "md_path": str(md)}

    monkeypatch.setattr(capi, "_call_buddy_report", gated_report)
    monkeypatch.setattr(capi, "_archive_research", lambda **kw: True)
    monkeypatch.setattr(capi, "_BG_PROGRESS_POLL", 0.05)
    monkeypatch.setattr(capi, "_OUT_DIR", tmp_path)   # 进度轮询不读真 out/

    class BgAgent:
        def __init__(self):
            self.messages = []
        async def run_turn(self, text, confirm_callback=None, allowed_tools=None):
            yield _Evt("tool_result", {"name": "ww_report_run", "content": "研报已受理", "is_error": False,
                                       "side_effect": {"background": {"kind": "report", "code": "SZ000001",
                                                                      "name": "测试", "asof": None}}})
            yield _Evt("done", None)

    def _turn_done_count(es):
        return sum(1 for e in es if e["type"] == "task_update" and e.get("status") == "done" and not e.get("kind"))

    app = FastAPI(); store = ConsoleStore(root=tmp_path)
    app.include_router(build_console_router(store=store, agent_factory=lambda sid: BgAgent()))
    with TestClient(app) as c:
        sa = c.post("/console/sessions", json={}).json()["meta"]["id"]
        sb = c.post("/console/sessions", json={}).json()["meta"]["id"]
        try:
            c.post("/console/send", json={"sid": sa, "text": "给平安写研报"})
            for _ in range(100):                       # 等 A 真入 in-flight(executor 卡在 gate)
                if "report:SZ000001" in capi._bg_inflight:
                    break
                time.sleep(0.05)
            assert "report:SZ000001" in capi._bg_inflight
            # B 同 code 发起 → 搭车:running 通知非 error
            c.post("/console/send", json={"sid": sb, "text": "也给平安写研报"})
            evs_b = _wait(store, sb, lambda es: any(
                e["type"] == "task_update" and e.get("kind") == "report" for e in es), tries=100)
            ride = [e for e in evs_b if e["type"] == "task_update" and e.get("kind") == "report"]
            assert ride and ride[0]["status"] == "running" and "另一会话" in ride[0]["note"]
            assert not any(e.get("status") == "error" for e in ride)
            assert sb in capi._bg_inflight["report:SZ000001"]["watchers"]
            # B 搭车留档 running(左/顶栏一致)
            bg_b = (store.get_meta(sb) or {}).get("bg") or {}
            assert any(v.get("status") == "running" and v.get("note") == "搭车" for v in bg_b.values())
            # B 再发同 code → 已在 watchers,维持拒绝口径(先等 B 首轮收尾,避免 send 级重入挡路)
            _wait(store, sb, lambda es: _turn_done_count(es) >= 1, tries=100)
            c.post("/console/send", json={"sid": sb, "text": "再来一份"})
            evs_b = _wait(store, sb, lambda es: any(
                e["type"] == "task_update" and e.get("kind") == "report" and e.get("status") == "error"
                for e in es), tries=100)
            rej = [e for e in evs_b if e["type"] == "task_update" and e.get("kind") == "report"
                   and e.get("status") == "error"]
            assert rej and "已有研报在跑" in rej[-1]["note"]
            # 等两会话的轮都收尾(running 清空),再测 delete 只被 in-flight 研报挡
            _wait(store, sa, lambda es: _turn_done_count(es) >= 1, tries=100)
            _wait(store, sb, lambda es: _turn_done_count(es) >= 2, tries=100)
            ra = c.request("DELETE", f"/console/sessions/{sa}").json()
            rb = c.request("DELETE", f"/console/sessions/{sb}").json()
            assert ra["ok"] is False and "后台研报" in ra["reason"]
            assert rb["ok"] is False and "后台研报" in rb["reason"]
        finally:
            gate.set()   # 放行(断言失败也不留卡死的 executor 线程)
        # 完成:A、B 各自 jsonl 都有 done 事件 + report_md artifact,meta.bg 终态同步
        # 先看 B(watcher 事件在 finally 末尾发):B 的 done 可见 ⇒ A/B 的 meta 终态均已落盘
        for s in (sb, sa):
            evs = _wait(store, s, lambda es: any(
                e["type"] == "task_update" and e.get("kind") == "report" and e.get("status") == "done"
                for e in es), tries=100)
            assert any(e["type"] == "task_update" and e.get("kind") == "report"
                       and e.get("status") == "done" for e in evs)
            arts = [e for e in evs if (e.get("artifact") or {}).get("kind") == "report_md"]
            assert arts and arts[-1]["artifact"]["payload"]["code"] == "SZ000001"
            bgs = (store.get_meta(s) or {}).get("bg") or {}
            assert any(v.get("status") == "done" and v.get("ok") is True and v.get("ended")
                       for v in bgs.values())
        # in-flight 已清:两会话现在可删
        assert "report:SZ000001" not in capi._bg_inflight
        assert c.request("DELETE", f"/console/sessions/{sa}").json()["ok"] is True
        assert c.request("DELETE", f"/console/sessions/{sb}").json()["ok"] is True


def test_confirm_extras_seats_decide(monkeypatch):
    import asyncio
    from guanlan_v2.console import api as capi
    import guanlan_v2.console.tools as ct
    monkeypatch.setattr(ct, "_self_get", lambda path: {
        "ok": True, "price": 303.12, "asofDate": "2026-06-11",
        "rev20": 0.2170881, "mom60": -0.0313489, "rsi14": 22.79383,
        "maDiff20": -0.1907521, "turnover20": 8.8468891})
    ex = asyncio.run(capi._confirm_extras("ww_seats_decide",
                                          {"code": "SH688012", "creed": "动量最强(20日+20%)"}))
    assert any("下跌21.7%" in f for f in ex["facts"])
    assert any("方向矛盾" in p for p in ex["precheck"])


def test_confirm_extras_cards_save_and_fallback():
    import asyncio
    from guanlan_v2.console import api as capi
    ex = asyncio.run(capi._confirm_extras("ww_cards_save",
                                          {"title": "卡", "insight": "动量20日+20%", "ic": "RankIC 4.80%"}))
    assert ex.get("precheck") and "未注明出处" in ex["precheck"][0]
    assert asyncio.run(capi._confirm_extras("ww_plan_update", {})) == {}


def test_system_prompt_mentions_seats_bind():
    from guanlan_v2.console.api import _SYSTEM_PROMPT
    assert "ww_seats_bind" in _SYSTEM_PROMPT
    assert "7×24" in _SYSTEM_PROMPT          # 诚实口径钉死


def test_bg_kinds_includes_etf_report():
    """守护:_spawn_bg 能分发 etf_report(防回归只认 report)。"""
    import guanlan_v2.console.api as capi
    assert "etf_report" in capi._BG_KINDS and "report" in capi._BG_KINDS


def test_etf_report_inflight_blocks_session_delete(tmp_path):
    """守卫:_etf_inflight 里有本会话(ETF 研报在跑)→ 删除被拦,清空后可删。"""
    from guanlan_v2.console import api as capi
    app = FastAPI(); store = ConsoleStore(root=tmp_path)
    app.include_router(build_console_router(store=store, agent_factory=lambda sid: FakeAgent()))
    with TestClient(app) as c:
        sid = c.post("/console/sessions", json={}).json()["meta"]["id"]
        capi._etf_inflight["bg_etf_unit"] = sid     # 直接模拟一只 ETF 研报在跑
        try:
            r = c.request("DELETE", f"/console/sessions/{sid}").json()
            assert r["ok"] is False and "后台研报" in r["reason"]
        finally:
            capi._etf_inflight.pop("bg_etf_unit", None)
        # 清空后可删(同一 sid 的其它 ETF 任务也不会误锁——按 bg_id 各自登记)
        assert c.request("DELETE", f"/console/sessions/{sid}").json()["ok"] is True


def test_etf_report_inflight_guards_session_delete_lifecycle(tmp_path, monkeypatch):
    """端到端:ETF 研报跑动期间进 _etf_inflight 锁住删除;完成后清空、会话可删。"""
    import threading
    import financial_analyst.buddy.tools as bt
    import guanlan_v2.console.api as capi

    gate = threading.Event()   # 卡门:executor 线程等 set,保证 in-flight 窗口可观测

    class _Res:
        is_error = False
        content = "ETF 研报正文"

    class _Tool:
        def run(self, **kw):
            gate.wait(timeout=120)   # 超时仅兜底防卡死;快乐路径 gate.set 即放行
            return _Res()

    monkeypatch.setattr(bt, "get_tool", lambda n: _Tool() if n == "run_etf_report" else None)

    class EtfAgent:
        def __init__(self):
            self.messages = []
        async def run_turn(self, text, confirm_callback=None, allowed_tools=None):
            yield _Evt("tool_result", {"name": "ww_etf_report_run", "content": "ETF 研报已受理",
                                       "is_error": False,
                                       "side_effect": {"background": {"kind": "etf_report", "code": "SH510300",
                                                                      "name": "沪深300ETF", "asof": None}}})
            yield _Evt("done", None)

    app = FastAPI(); store = ConsoleStore(root=tmp_path)
    app.include_router(build_console_router(store=store, agent_factory=lambda sid: EtfAgent()))
    with TestClient(app) as c:
        sid = c.post("/console/sessions", json={}).json()["meta"]["id"]
        try:
            c.post("/console/send", json={"sid": sid, "text": "给沪深300ETF写研报"})
            for _ in range(100):                       # 等 ETF 任务真入 in-flight(executor 卡在 gate)
                if sid in capi._etf_inflight.values():
                    break
                time.sleep(0.05)
            assert sid in capi._etf_inflight.values()
            r = c.request("DELETE", f"/console/sessions/{sid}").json()
            assert r["ok"] is False and "后台研报" in r["reason"]
        finally:
            gate.set()   # 放行(断言失败也不留卡死的 executor 线程)
        evs = _wait(store, sid, lambda es: any(
            e["type"] == "task_update" and e.get("kind") == "etf_report" and e.get("status") == "done"
            for e in es), tries=100)
        assert any(e["type"] == "task_update" and e.get("kind") == "etf_report"
                   and e.get("status") == "done" for e in evs)
        # in-flight 已清:会话现在可删
        assert sid not in capi._etf_inflight.values()
        assert c.request("DELETE", f"/console/sessions/{sid}").json()["ok"] is True


# ── 阶段1:自学回路(受限后台复盘)──

def test_bg_kinds_includes_review():
    import guanlan_v2.console.api as capi
    assert "review" in capi._BG_KINDS


def test_review_mode_default_enforce(monkeypatch):
    import guanlan_v2.console.api as capi
    monkeypatch.delenv("CONSOLE_REVIEW_MODE", raising=False)
    assert capi._review_mode() == "enforce"
    # 非法值降级 off;monitor / enforce 透传
    monkeypatch.setenv("CONSOLE_REVIEW_MODE", "weird")
    assert capi._review_mode() == "off"
    monkeypatch.setenv("CONSOLE_REVIEW_MODE", "monitor")
    assert capi._review_mode() == "monitor"
    monkeypatch.setenv("CONSOLE_REVIEW_MODE", "ENFORCE")   # 大小写不敏感
    assert capi._review_mode() == "enforce"


def test_build_review_snapshot_shapes(tmp_path):
    import guanlan_v2.console.api as capi
    from guanlan_v2.console.store import ConsoleStore
    st = ConsoleStore(root=tmp_path)
    sid = st.create_session()["id"]
    st.append_event(sid, "user_msg", text="帮我分析动量因子")
    st.append_event(sid, "tool_call", tool="ww_factor_analyze")
    st.append_event(sid, "tool_result", tool="ww_factor_analyze", ok=False, summary="失败:字段名错")
    snap = capi._build_review_snapshot(st, sid)
    assert "动量因子" in snap and "ww_factor_analyze" in snap and "失败" in snap


class _NToolAgent:
    """产 n 个 tool_call/tool_result(全 ok)再 text+done 的 FakeAgent,用于自学回路触发门测试。
    all_ok=False 时收尾前 yield 一个 kind=error 事件 → _run_turn 把 turn_ok 置 False
    (= 触发门的 had_failure 信号;实现用现有 turn_ok 变量,只对 kind==error/异常翻转,
    单条 tool_result 的 is_error 不翻转 turn_ok —— 与 Task 1.3 实现口径一致)。"""
    def __init__(self, n, all_ok=True):
        self.messages = []
        self._n = n
        self._all_ok = all_ok

    async def run_turn(self, text, confirm_callback=None, allowed_tools=None):
        for _ in range(self._n):
            yield _Evt("tool_call", {"name": "ww_factor_fields", "args": {}})
            yield _Evt("tool_result", {"name": "ww_factor_fields", "content": "字段词表",
                                       "is_error": False, "side_effect": None})
        if not self._all_ok:
            yield _Evt("error", "某工具反复失败(模拟 had_failure)")
        yield _Evt("text", "完")
        yield _Evt("done", None)


def _review_evs(es):
    return [e for e in es if e["type"] == "task_update" and e.get("kind") == "review"]


def test_review_triggers_on_5_tools(tmp_path, monkeypatch):
    """CONSOLE_REVIEW_MODE=monitor + 本轮≥5 工具 → 触发后台复盘(出现 kind=review 的 task_update,
    至少 running);4 工具无失败不触发;off 即便 5+ 工具也不触发。

    复盘 fork 真构造 BuddyAgent,其 run_turn 的 LLM 调用在测试环境会失败,但 fail-closed 吞掉、
    且 running 的 task_update 在 LLM 调用之前 emit,故 kind=review 事件必现(本测只断言触发与否,
    不依赖复盘的 LLM 产物)。"""
    import guanlan_v2.console.api as capi

    def _run(n, all_ok, expect_review):
        app = FastAPI(); store = ConsoleStore(root=tmp_path / f"r{n}_{all_ok}_{expect_review}")
        app.include_router(build_console_router(
            store=store, agent_factory=lambda sid: _NToolAgent(n, all_ok)))
        with TestClient(app) as c:
            sid = c.post("/console/sessions", json={}).json()["meta"]["id"]
            c.post("/console/send", json={"sid": sid, "text": "跑一轮"})
            # 等主 turn 收尾(done)
            evs = _wait(store, sid, _done, tries=100)
            assert _done(evs)
            if expect_review:
                evs = _wait(store, sid, lambda es: bool(_review_evs(es)), tries=100)
                rv = _review_evs(evs)
                assert rv and rv[0]["status"] == "running", f"应触发复盘(n={n},all_ok={all_ok})"
            else:
                # 不应触发:主 turn done 后再宽限轮询一阵,确认始终无 review 事件
                for _ in range(8):
                    assert not _review_evs(store.read_events(sid)), \
                        f"不应触发复盘(n={n},all_ok={all_ok})"
                    time.sleep(0.05)

    # monitor:5 工具(全 ok)→ 触发;4 工具无失败 → 不触发;4 工具有失败 → 触发(had_failure)
    monkeypatch.setenv("CONSOLE_REVIEW_MODE", "monitor")
    _run(5, True, expect_review=True)
    _run(4, True, expect_review=False)
    _run(4, False, expect_review=True)
    # off:即便 5+ 工具也不触发
    monkeypatch.setenv("CONSOLE_REVIEW_MODE", "off")
    _run(5, True, expect_review=False)


def test_review_semaphore_bounded():
    """安全3:复盘并发受信号量限(默认 2),防多 turn/多会话无界堆积 LLM 调用拖慢主对话。
    只断言公共量(不碰 CPython 私有 _value,跨版本脆弱)。"""
    import asyncio as _asyncio
    import guanlan_v2.console.api as capi
    assert isinstance(capi._REVIEW_SEM, _asyncio.Semaphore)
    assert capi._REVIEW_MAX_CONCURRENCY == 2


def test_review_fork_blocks_nonwhitelisted_tool(monkeypatch):
    """spec Task 1.4 Step 2(最关键安全门·必须实测):真引擎 BuddyAgent + mock LLM 让它发起一个
    白名单外的 ww_screen_run tool_call,以 allowed_tools=ct.REVIEW_ALLOWED 驱动 run_turn,断言:
    ①该 tool_call 的 tool_result is_error=True(被 engine agent.py:455 的执行兜底门拦下,且回的是
      门的「不在当前模块可用范围内」而非工具真执行结果);
    ②screen_impl 没被真正调用(门在 tool.run 之前 continue,根本不进 _wrap)。

    LLM seam:engine run_turn 内发起调用的是 self._client.chat(async,返 OpenAI-compat 信封)。
    LLMClient.for_agent 构造时只读配置不联网,故构造真 BuddyAgent 安全;再 monkeypatch 实例的
    _client.chat 即可确定性地产出 tool_call → 终止文本两轮。"""
    import asyncio as _asyncio
    import guanlan_v2.console.api as capi
    import guanlan_v2.console.tools as ct

    # screen_impl 防真执行哨兵:若门没拦住而真跑了工具,flag 会被置 True。门在 tool.run 之前
    # continue(见 agent.py:455),根本不进 _wrap → flag 恒 False = 工具未执行的铁证。
    # (不改 TOOL_REGISTRY——门在任何 impl 闭包之前就拦,无须替换注册;register 幂等只确保已注册。)
    called = {"screen": False}

    def _sentinel_screen(*a, **k):
        called["screen"] = True
        return {"ok": True, "content": "不该被调用", "artifact": None}

    monkeypatch.setattr(ct, "screen_impl", _sentinel_screen)

    from financial_analyst.buddy.agent import BuddyAgent
    import financial_analyst.buddy.tools as bt
    ct.register_console_tools()                            # 幂等:确保 ww_screen_run 已在 TOOL_REGISTRY
    assert bt.get_tool("ww_screen_run") is not None        # 工具确实注册了(否则测的是「未注册」非「被门拦」)
    assert "ww_screen_run" not in ct.REVIEW_ALLOWED        # 前提:它确属白名单外

    ra = BuddyAgent(system_prompt=capi._REVIEW_SYSTEM_PROMPT)
    assert hasattr(ra, "max_tool_iters")                   # 钉死引擎属性名(防未来改名静默失效)

    calls = {"n": 0}

    async def _fake_chat(messages, tools=None, temperature=0.2, **kw):
        calls["n"] += 1
        if calls["n"] == 1:        # 第一轮:发起白名单外 ww_screen_run
            return {"choices": [{"message": {
                "role": "assistant", "content": "",
                "tool_calls": [{"id": "call_1", "type": "function",
                                "function": {"name": "ww_screen_run", "arguments": "{}"}}]}}]}
        return {"choices": [{"message": {"role": "assistant", "content": "好的,结束。"}}]}

    monkeypatch.setattr(ra._client, "chat", _fake_chat)

    async def _drive():
        out = []
        async for evt in ra.run_turn("复盘快照", confirm_callback=None,
                                     allowed_tools=ct.REVIEW_ALLOWED):
            out.append(evt)
        return out

    events = _asyncio.run(_drive())
    trs = [e for e in events if e.kind == "tool_result"
           and (e.payload or {}).get("name") == "ww_screen_run"]
    assert trs, "应有 ww_screen_run 的 tool_result(被门拦下后仍 emit is_error)"
    assert trs[0].payload["is_error"] is True               # ① 被 allowed_tools 门拦
    assert "不在当前模块可用范围内" in str(trs[0].payload.get("content"))  # 门的拦截语,非工具真结果
    assert called["screen"] is False                        # ② screen_impl 没被真正调用


def test_system_prompt_names_all_ww_tools():
    """#3 守护:每个 ww_ 工具名都必须在 _SYSTEM_PROMPT 出现(防提示词↔工具表漂移)。"""
    from guanlan_v2.console.api import _SYSTEM_PROMPT
    import guanlan_v2.console.tools as ct
    missing = [t["name"] for t in ct.WW_TOOL_TABLE if t["name"] not in _SYSTEM_PROMPT]
    assert missing == [], f"这些 ww_ 工具未在系统提示词具名: {missing}"


def test_system_prompt_routes_news_collect():
    """#4 守护:提示词把裸 news_collect 路由到 ww_news_collect 并警示别直接调。
    用纪律12 的独有短语断言(不能用『调不到』——它已在 :34 ww_endpoints 描述出现,会让守护空转)。"""
    from guanlan_v2.console.api import _SYSTEM_PROMPT
    assert "改用 ww_news_collect" in _SYSTEM_PROMPT
    assert "别直接调" in _SYSTEM_PROMPT
