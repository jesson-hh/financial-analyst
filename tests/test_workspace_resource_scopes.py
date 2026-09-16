"""Workspace scoping for saved workflow instances and workflow reports."""
import json

from fastapi import FastAPI
from fastapi.testclient import TestClient

import guanlan_v2.reports.api as reports_api
import guanlan_v2.workflow.api as workflow_api
from guanlan_v2.console.store import ConsoleStore


WS_A = "rw_aaaaaaaaaaaa"
WS_B = "rw_bbbbbbbbbbbb"
GRAPH = {
    "nodes": [
        {"id": "factor", "type": "formula", "params": {"expr": "rank(close)"}},
        {"id": "analysis", "type": "analysis", "params": {}},
    ],
    "edges": [{"from": ["factor", "out"], "to": ["analysis", "factor"]}],
}


def _private_graph():
    """A distinct graph that still satisfies the current complete-graph gate."""
    return {
        "nodes": [
            {"id": "private", "type": "formula", "params": {"expr": "rank(open)"}},
            {"id": "analysis", "type": "analysis", "params": {}},
        ],
        "edges": [{"from": ["private", "out"], "to": ["analysis", "factor"]}],
    }


def _workflow_client(tmp_path, monkeypatch):
    monkeypatch.setenv("GUANLAN_WORKFLOW_ROOT", str(tmp_path / "workflows"))
    app = FastAPI()
    app.include_router(workflow_api.build_workflow_router())
    return TestClient(app)


def _reports_client(tmp_path, monkeypatch):
    monkeypatch.setattr(reports_api, "_STORE", tmp_path / "reports")
    app = FastAPI()
    app.include_router(reports_api.build_reports_router())
    return TestClient(app)


def test_workflow_global_legacy_and_workspace_namespaces(tmp_path, monkeypatch):
    c = _workflow_client(tmp_path, monkeypatch)
    global_item = c.post("/workflow/save", json={
        "name": "动量模板", "graph": GRAPH,
    }).json()
    private_a = c.post("/workflow/save", json={
        "name": "动量模板", "graph": GRAPH, "workspace_id": WS_A,
    }).json()
    private_b = c.post("/workflow/save", json={
        "name": "动量模板", "graph": GRAPH, "workspace_id": WS_B,
    }).json()
    assert global_item["scope"] == "global" and global_item["workspace_id"] is None
    assert private_a["scope"] == "workspace" and private_a["workspace_id"] == WS_A
    assert len({global_item["id"], private_a["id"], private_b["id"]}) == 3

    # Legacy list remains global-only.  Workspace list is private-only unless
    # include_global explicitly asks for read-only templates.
    legacy = c.get("/workflow/list").json()["items"]
    assert [item["id"] for item in legacy] == [global_item["id"]]
    only_a = c.get("/workflow/list", params={"workspace_id": WS_A}).json()["items"]
    assert [item["id"] for item in only_a] == [private_a["id"]]
    with_templates = c.get("/workflow/list", params={
        "workspace_id": WS_A, "include_global": "true",
    }).json()["items"]
    assert {item["id"] for item in with_templates} == {
        private_a["id"], global_item["id"],
    }
    template = next(item for item in with_templates if item["id"] == global_item["id"])
    assert template["read_only"] is True and template["is_template"] is True
    assert template["scope"] == "global" and template["workspace_id"] is None
    own = next(item for item in with_templates if item["id"] == private_a["id"])
    assert own["read_only"] is False and own["is_template"] is False


def test_workflow_cross_workspace_get_delete_are_fail_closed(tmp_path, monkeypatch):
    c = _workflow_client(tmp_path, monkeypatch)
    global_id = c.post("/workflow/save", json={"name": "全局", "graph": GRAPH}).json()["id"]
    private_id = c.post("/workflow/save", json={
        "name": "A 私有", "graph": GRAPH, "workspace_id": WS_A,
    }).json()["id"]

    assert c.get(f"/workflow/get/{private_id}", params={"workspace_id": WS_A}).json()["ok"]
    assert c.get(f"/workflow/get/{private_id}", params={"workspace_id": WS_B}).json()["ok"] is False
    assert c.get(f"/workflow/get/{private_id}").json()["ok"] is False
    assert c.get(f"/workflow/get/{global_id}", params={"workspace_id": WS_A}).json()["ok"] is False
    template = c.get(f"/workflow/get/{global_id}", params={
        "workspace_id": WS_A, "include_global": "true",
    }).json()
    assert template["ok"] and template["read_only"] and template["is_template"]

    assert c.request("DELETE", f"/workflow/delete/{private_id}", params={
        "workspace_id": WS_B,
    }).json()["ok"] is False
    assert c.post("/workflow/delete", json={
        "id": global_id, "workspace_id": WS_A,
    }).json()["ok"] is False
    assert c.get(f"/workflow/get/{private_id}", params={"workspace_id": WS_A}).json()["ok"]
    assert c.post("/workflow/delete", json={
        "id": private_id, "workspace_id": WS_A,
    }).json()["ok"] is True
    assert c.get(f"/workflow/get/{global_id}").json()["ok"] is True


def test_workflow_old_record_is_global_and_private_same_name_never_overwrites_it(
        tmp_path, monkeypatch):
    root = tmp_path / "workflows"
    root.mkdir()
    old = {"id": "wlegacy", "name": "旧模板", "ts": 1, "graph": GRAPH}
    (root / "wlegacy.json").write_text(json.dumps(old), encoding="utf-8")
    c = _workflow_client(tmp_path, monkeypatch)

    assert c.get("/workflow/get/wlegacy").json()["scope"] == "global"
    saved = c.post("/workflow/save", json={
        "name": "旧模板", "graph": _private_graph(),
        "workspace_id": WS_A,
    }).json()
    assert saved["id"] != "wlegacy"
    assert c.get("/workflow/get/wlegacy").json()["graph"] == GRAPH
    assert c.get(f"/workflow/get/{saved['id']}", params={"workspace_id": WS_A}).json()["ok"]


def test_report_lists_are_scope_exact_and_old_records_stay_global(tmp_path, monkeypatch):
    c = _reports_client(tmp_path, monkeypatch)
    global_report = c.post("/report/save", json={"name": "全局报告", "result": {"x": 1}}).json()
    report_a = c.post("/report/save", json={
        "name": "A 报告", "result": {"x": "a"}, "workspace_id": WS_A,
        "workflow_name": "同名工作流", "workflow_scope": "workspace",
    }).json()
    report_b = c.post("/report/save", json={
        "name": "B 报告", "result": {"x": "b"}, "workspace_id": WS_B,
    }).json()

    store = tmp_path / "reports"
    old = {
        "id": "legacy-report", "name": "旧报告", "ts": "2020-01-01T00:00:00",
        "result": {"legacy": True}, "graph": None, "kpi": {},
    }
    (store / "legacy-report.json").write_text(json.dumps(old), encoding="utf-8")

    global_list = c.get("/report/list").json()["reports"]
    assert {item["id"] for item in global_list} == {
        global_report["id"], "legacy-report",
    }
    assert all(item["scope"] == "global" and item["workspace_id"] is None
               for item in global_list)
    a_list = c.get("/report/list", params={"workspace_id": WS_A}).json()["reports"]
    assert [item["id"] for item in a_list] == [report_a["id"]]
    assert a_list[0]["scope"] == "workspace" and a_list[0]["workspace_id"] == WS_A
    assert a_list[0]["workflow_scope"] == "workspace"
    assert report_b["id"] not in {item["id"] for item in a_list}


def test_report_cross_workspace_get_delete_are_fail_closed(tmp_path, monkeypatch):
    c = _reports_client(tmp_path, monkeypatch)
    global_id = c.post("/report/save", json={"name": "全局"}).json()["id"]
    private_id = c.post("/report/save", json={
        "name": "A", "workspace_id": WS_A, "result": {"secret": 1},
    }).json()["id"]

    own = c.get(f"/report/get/{private_id}", params={"workspace_id": WS_A}).json()
    assert own["ok"] and own["scope"] == "workspace" and own["result"]["secret"] == 1
    assert c.get(f"/report/get/{private_id}", params={"workspace_id": WS_B}).json()["reason"] == "not_found"
    assert c.get(f"/report/get/{private_id}").json()["reason"] == "not_found"
    assert c.get(f"/report/get/{global_id}", params={"workspace_id": WS_A}).json()["reason"] == "not_found"

    assert c.post("/report/delete", json={
        "id": private_id, "workspace_id": WS_B,
    }).json()["reason"] == "not_found"
    assert c.post("/report/delete", json={
        "id": global_id, "workspace_id": WS_A,
    }).json()["reason"] == "not_found"
    assert c.get(f"/report/get/{private_id}", params={"workspace_id": WS_A}).json()["ok"]
    assert c.post("/report/delete", json={
        "id": private_id, "workspace_id": WS_A,
    }).json()["ok"]
    assert c.get(f"/report/get/{global_id}").json()["ok"]


def test_scope_endpoints_reject_malformed_workspace_ids(tmp_path, monkeypatch):
    wc = _workflow_client(tmp_path, monkeypatch)
    assert wc.get("/workflow/list", params={"workspace_id": "../../x"}).json()["ok"] is False
    rc = _reports_client(tmp_path, monkeypatch)
    assert rc.get("/report/list", params={"workspace_id": "../../x"}).json()["ok"] is False


def test_archived_workspace_blocks_workflow_and_report_mutations_but_keeps_reads(
        tmp_path, monkeypatch):
    store = ConsoleStore(root=tmp_path / "console")
    workspace = store.workspace_store.create({
        "name": "历史票", "kind": "stock", "code": "600519",
    })
    monkeypatch.setenv("GUANLAN_WORKFLOW_ROOT", str(tmp_path / "workflows"))
    monkeypatch.setattr(reports_api, "_STORE", tmp_path / "reports")
    app = FastAPI()
    app.include_router(workflow_api.build_workflow_router(
        workspace_store=store.workspace_store,
    ))
    app.include_router(reports_api.build_reports_router(
        workspace_store=store.workspace_store,
    ))
    c = TestClient(app)

    workflow = c.post("/workflow/save", json={
        "name": "归档前工作流", "graph": GRAPH, "workspace_id": workspace["id"],
    }).json()
    report = c.post("/report/save", json={
        "name": "归档前报告", "result": {"x": 1}, "workspace_id": workspace["id"],
    }).json()
    assert workflow["ok"] is True and report["ok"] is True
    store.workspace_store.archive(workspace["id"])

    assert c.get(f"/workflow/get/{workflow['id']}", params={
        "workspace_id": workspace["id"],
    }).json()["ok"] is True
    assert c.get(f"/report/get/{report['id']}", params={
        "workspace_id": workspace["id"],
    }).json()["ok"] is True
    blocked_workflow = c.post("/workflow/save", json={
        "name": "不应保存", "graph": GRAPH, "workspace_id": workspace["id"],
    }).json()
    blocked_report = c.post("/report/save", json={
        "name": "不应保存", "result": {}, "workspace_id": workspace["id"],
    }).json()
    assert blocked_workflow["ok"] is False and "归档" in blocked_workflow["reason"]
    assert blocked_report["ok"] is False and "归档" in blocked_report["reason"]
    assert c.post("/workflow/delete", json={
        "id": workflow["id"], "workspace_id": workspace["id"],
    }).json()["ok"] is False
    assert c.post("/report/delete", json={
        "id": report["id"], "workspace_id": workspace["id"],
    }).json()["ok"] is False
