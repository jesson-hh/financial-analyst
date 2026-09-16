"""Real authoring/durable HTTP contracts with isolated stores and no real LLM/engine."""
import copy
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from guanlan_v2.console import tools as ct
from guanlan_v2.console import workflow_tools as wtools
from guanlan_v2.console import workspace_tools as wt
from guanlan_v2.console.store import ConsoleStore
from guanlan_v2.workflow import api as wapi
from guanlan_v2.workflow import executor as wex
from guanlan_v2.workflow.authoring import graph_digest
from guanlan_v2.workflow.run_store import WorkflowRunStore


RID = "wr_1234567890abcdef12345678"


def graph():
    return {"nodes": [
        {"id": "src", "type": "source", "params": {"universe": "csi300_active"}},
        {"id": "f", "type": "formula", "params": {"expr": "rank(close)"}},
        {"id": "a", "type": "analysis", "params": {"context_source_id": "src"}},
    ], "edges": [{"from": ["f", "out"], "to": ["a", "factor"]}]}


@pytest.fixture
def api(tmp_path, monkeypatch):
    wf_root = tmp_path / "workflows"
    monkeypatch.setenv("GUANLAN_WORKFLOW_ROOT", str(wf_root))
    console = ConsoleStore(root=tmp_path / "console")
    first = console.workspace_store.create({"kind": "stock", "code": "SH600667", "name": "甲"})
    second = console.workspace_store.create({"kind": "stock", "code": "SZ000636", "name": "乙"})
    runs = WorkflowRunStore(tmp_path / "runs")
    app = FastAPI()
    app.include_router(wapi.build_workflow_router(workspace_store=console.workspace_store, run_store=runs))

    async def no_llm(*args, **kwargs):
        pytest.fail("test must explicitly stub the LLM")

    monkeypatch.setattr(wapi, "_llm_complete", no_llm)
    monkeypatch.setattr(wex, "run_graph", lambda *a, **k: pytest.fail("no implicit market execution"))
    with TestClient(app) as client:
        yield SimpleNamespace(client=client, console=console, workspace=first["id"],
            other=second["id"], runs=runs, workflows_root=wf_root)


def wait_terminal(api, run_id):
    deadline = time.monotonic() + 4
    last = None
    while time.monotonic() < deadline:
        last = api.client.get(f"/workflow/run/status/{run_id}", params={"workspace_id": api.workspace}).json()
        if last.get("status") not in {"queued", "running"}:
            return last
        time.sleep(.01)
    raise AssertionError(f"run did not settle: {last}")


def test_catalog_and_validate_expose_complete_contract_without_running(api):
    catalog = api.client.get("/workflow/catalog").json()
    assert catalog["ok"] is True
    assert catalog["schema"] == "workflow-authoring-v1"
    assert catalog["graph_schema"]["required"] == ["nodes", "edges"]
    assert catalog["nodes"]["source"]["out"] == [["data", "series"]]
    assert "context_source_id" in catalog["nodes"]["analysis"]["params"]
    checked = api.client.post("/workflow/validate", json={"graph": graph()}).json()
    assert checked["ok"] is True and checked["graph_digest"] == graph_digest(graph())
    assert checked["validation_scope"] == "graph_structure_and_context_only_not_data_or_profitability"
    assert api.runs.list_runs(workspace_id=api.workspace) == []
    assert api.client.get("/workflow/list", params={"workspace_id": api.workspace}).json()["items"] == []


def test_validate_returns_node_errors_for_incomplete_graph(api):
    bad = graph()
    bad["edges"] = []
    response = api.client.post("/workflow/validate", json={"graph": bad})
    assert response.status_code == 200
    assert response.json()["ok"] is False
    assert any("a.factor" in item for item in response.json()["errors"])


def test_generate_returns_valid_graph_but_does_not_save_or_run(api, monkeypatch):
    calls = []

    async def generate(system, user):
        calls.append(user)
        return json.dumps(graph())

    monkeypatch.setattr(wapi, "_llm_complete", generate)
    response = api.client.post("/workflow/generate", json={"goal": "研究动量", "max_retries": 0}).json()
    assert response["ok"] is True and response["source"] == "llm"
    assert response["graph"] == graph()
    assert response["validation"]["ok"] is True and len(calls) == 1
    assert api.runs.list_runs() == []
    assert api.client.get("/workflow/list").json()["items"] == []


@pytest.mark.parametrize("failure", ["unavailable", "invalid_json", "invalid_graph"])
def test_generation_failures_never_fallback_to_success_template(api, monkeypatch, failure):
    calls = []

    async def generate(system, user):
        calls.append(user)
        if failure == "unavailable":
            raise RuntimeError("offline test")
        if failure == "invalid_json":
            return "no JSON"
        return json.dumps({"nodes": [{"id": "madeup", "type": "invented", "params": {}}], "edges": []})

    monkeypatch.setattr(wapi, "_llm_complete", generate)
    response = api.client.post("/workflow/generate", json={"goal": "研究趋势", "max_retries": 1})
    data = response.json()
    assert response.status_code == 200 and data["ok"] is False
    assert data["reason"] and "graph" not in data
    assert len(calls) == (1 if failure == "unavailable" else 2)
    assert api.runs.list_runs() == []


def test_generation_never_guesses_first_source_when_context_is_ambiguous(api, monkeypatch):
    multi = graph()
    multi["nodes"][2]["params"] = {}
    multi["nodes"].insert(1, {"id": "other", "type": "source", "params": {"universe": "csi800"}})
    original = copy.deepcopy(multi)
    wapi._autowire_source(multi)
    assert multi == original

    async def generate(system, user):
        return json.dumps(multi)

    monkeypatch.setattr(wapi, "_llm_complete", generate)
    response = api.client.post("/workflow/generate", json={"goal": "比较两个池", "max_retries": 0}).json()
    assert response["ok"] is False and "上下文" in response["reason"]
    assert "graph" not in response


def test_parallel_same_id_requests_start_exactly_one_executor(api, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    calls, calls_lock = [], threading.Lock()

    def execute(*args, **kwargs):
        with calls_lock:
            calls.append(1)
        entered.set()
        release.wait(timeout=3)
        return {"ok": True, "terminal": {"kind": "analysis", "payload": {"ok": True}}}

    monkeypatch.setattr(wex, "run_graph", execute)
    barrier = threading.Barrier(6)
    payload = {"run_id": RID, "graph": graph(), "workspace_id": api.workspace}

    def start():
        barrier.wait(timeout=3)
        return api.client.post("/workflow/run/start", json=payload).json()

    try:
        with ThreadPoolExecutor(max_workers=6) as pool:
            replies = list(pool.map(lambda _: start(), range(6)))
        assert all(reply["ok"] for reply in replies), replies
        assert sum(reply["started"] is True for reply in replies) == 1
        assert {reply["id"] for reply in replies} == {RID}
        assert entered.wait(timeout=1)
        assert calls == [1]
    finally:
        release.set()
        if entered.is_set():
            wait_terminal(api, RID)


def test_same_run_id_rejects_changed_graph_or_overrides(api, monkeypatch):
    calls = []

    def execute(*args, **kwargs):
        calls.append(1)
        return {"ok": True, "terminal": {"kind": "analysis", "payload": {"ok": True}}}

    monkeypatch.setattr(wex, "run_graph", execute)
    payload = {"run_id": RID, "graph": graph(), "workspace_id": api.workspace}
    assert api.client.post("/workflow/run/start", json=payload).json()["ok"] is True
    wait_terminal(api, RID)
    changed = copy.deepcopy(payload)
    changed["graph"]["nodes"][1]["params"]["expr"] = "rank(-close)"
    for attempt in (changed, {**payload, "oos_frac": .4}):
        rejected = api.client.post("/workflow/run/start", json=attempt).json()
        assert rejected["ok"] is False and "拒绝复用" in rejected["reason"]
    identical = api.client.post("/workflow/run/start", json=payload).json()
    assert identical["ok"] is True and identical["started"] is False
    assert calls == [1]


def test_run_list_and_status_strictly_isolate_workspace_and_global(api):
    own = api.runs.create(graph=graph(), overrides={}, workspace_id=api.workspace)
    foreign = api.runs.create(graph=graph(), overrides={}, workspace_id=api.other)
    global_run = api.runs.create(graph=graph(), overrides={})
    own_rows = api.client.get("/workflow/run/list", params={"workspace_id": api.workspace}).json()
    assert own_rows["ok"] is True and [row["id"] for row in own_rows["items"]] == [own["id"]]
    assert "graph" not in own_rows["items"][0] and "result" not in own_rows["items"][0]
    assert "worker_instance" not in own_rows["items"][0]
    assert own_rows["items"][0]["recovery"]["can_resume"] is False
    globals_ = api.client.get("/workflow/run/list").json()["items"]
    assert [row["id"] for row in globals_] == [global_run["id"]]
    for record in (foreign, global_run):
        assert api.client.get(f"/workflow/run/status/{record['id']}",
            params={"workspace_id": api.workspace}).json()["ok"] is False


def test_agent_save_metadata_digest_matches_real_store_and_retry_is_idempotent(api, monkeypatch):
    # The real save endpoint migrates, then stores only nodes/edges. Digest and
    # idempotency must use that same graph, not discarded transport metadata.
    sid = api.console.create_session(workspace_id=api.workspace)["id"]
    monkeypatch.setattr(ct, "_self_get", lambda path, **kwargs: api.client.get(path).json())
    monkeypatch.setattr(ct, "_self_post", lambda path, body, **kwargs: api.client.post(path, json=body).json())
    tokens = (ct.CTX_STORE.set(api.console), ct.CTX_SID.set(sid),
              wt.CTX_WORKSPACE.set(wt.bind_workspace_context(api.console, sid)))
    try:
        decorated = {**graph(), "schema_version": 3, "metadata": {"origin": "restored canvas"}}
        first = wtools.workflow_save_impl("保存口径", decorated)
        assert first["ok"] is True, first
        stored = api.client.get(f"/workflow/get/{first['workflow_id']}",
            params={"workspace_id": api.workspace}).json()
        assert stored["graph"] == graph()
        assert json.loads(first["content"])["graph_digest"] == graph_digest(stored["graph"])
        path = api.workflows_root / f"{first['workflow_id']}.json"
        initial_bytes = path.read_bytes()
        repeated = wtools.workflow_save_impl("保存口径", decorated)
        assert repeated["ok"] is True, repeated
        assert repeated["workflow_id"] == first["workflow_id"]
        assert json.loads(repeated["content"])["reused"] is True
        assert path.read_bytes() == initial_bytes
    finally:
        wt.CTX_WORKSPACE.reset(tokens[2])
        ct.CTX_SID.reset(tokens[1])
        ct.CTX_STORE.reset(tokens[0])
