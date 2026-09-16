"""Agent authoring contracts; HTTP stubs only touch real isolated local stores."""
import copy
import inspect
import json
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest

from guanlan_v2.console import tools as ct
from guanlan_v2.console import workflow_tools as wtools
from guanlan_v2.console import workspace_tools as wt
from guanlan_v2.console.store import ConsoleStore
from guanlan_v2.workflow import authoring
from guanlan_v2.workflow.run_store import WorkflowRunStore
from guanlan_v2.workflow.store import WorkflowStore


def graph():
    return {"nodes": [
        {"id": "src", "type": "source", "params": {"universe": "csi300_active",
            "start": "2025-01-01", "end": "2026-09-04"}},
        {"id": "f", "type": "formula", "params": {"expr": "rank(close)"}},
        {"id": "a", "type": "analysis", "params": {"context_source_id": "src"}},
    ], "edges": [{"from": ["f", "out"], "to": ["a", "factor"]}]}


def content(output):
    return json.loads(output["content"])


@pytest.fixture
def bound(tmp_path, monkeypatch):
    console = ConsoleStore(root=tmp_path / "console")
    first = console.workspace_store.create({"kind": "stock", "code": "SH600667", "name": "测试甲"})
    second = console.workspace_store.create({"kind": "stock", "code": "SZ000636", "name": "测试乙"})
    sid = console.create_session(workspace_id=first["id"])["id"]
    state = SimpleNamespace(console=console, workspace=first["id"], other=second["id"],
        sid=sid, workflows=WorkflowStore(tmp_path / "workflows"),
        runs=WorkflowRunStore(tmp_path / "runs"), calls=[], starts=[],
        generated={"ok": True, "graph": graph(), "source": "llm"})

    def get(url, **kwargs):
        state.calls.append(("GET", url, kwargs))
        parsed = urlsplit(url)
        query = parse_qs(parsed.query)
        assert query.get("workspace_id") == [state.workspace]
        assert "include_global" not in query
        wid = query["workspace_id"][0]
        if parsed.path == "/workflow/list":
            return {"ok": True, "items": state.workflows.list(workspace_id=wid)}
        if parsed.path.startswith("/workflow/get/"):
            item = state.workflows.get(parsed.path.rsplit("/", 1)[-1], workspace_id=wid)
            return {"ok": True, **item} if item else {"ok": False, "reason": "工作流不存在"}
        if parsed.path == "/workflow/run/list":
            return {"ok": True, "items": state.runs.list_runs(
                workspace_id=wid, limit=int(query.get("limit", [20])[0]))}
        if parsed.path.startswith("/workflow/run/status/"):
            item = state.runs.get(parsed.path.rsplit("/", 1)[-1], workspace_id=wid)
            return {"ok": True, **state.runs.public(item)} if item else {"ok": False, "reason": "运行记录不存在"}
        pytest.fail(f"unexpected GET: {url}")

    def post(path, body, **kwargs):
        state.calls.append(("POST", path, copy.deepcopy(body)))
        if path == "/workflow/generate":
            return copy.deepcopy(state.generated)
        assert body.get("workspace_id") == state.workspace
        if path == "/workflow/save":
            return {"ok": True, **state.workflows.save(
                body["name"], body["graph"], workspace_id=body["workspace_id"])}
        if path == "/workflow/run/start":
            previous = state.runs.get(body["run_id"], workspace_id=state.workspace)
            if previous is None:
                state.starts.append(body["run_id"])
            item = state.runs.create(run_id=body["run_id"], name=body["name"],
                graph=body["graph"], overrides={}, workspace_id=state.workspace)
            return {"ok": True, **state.runs.public(item)}
        pytest.fail(f"unexpected POST: {path}")

    monkeypatch.setattr(ct, "_self_get", get)
    monkeypatch.setattr(ct, "_self_post", post)
    tokens = (ct.CTX_STORE.set(console), ct.CTX_SID.set(sid),
              wt.CTX_WORKSPACE.set(wt.bind_workspace_context(console, sid)))
    try:
        yield state
    finally:
        wt.CTX_WORKSPACE.reset(tokens[2])
        ct.CTX_SID.reset(tokens[1])
        ct.CTX_STORE.reset(tokens[0])


def test_catalog_has_actual_ports_complete_schema_and_does_not_mutate_catalog():
    from guanlan_v2.workflow.api import _CATALOG
    original = copy.deepcopy(_CATALOG)
    data = content(wtools.workflow_catalog_impl())
    assert data["schema"] == "workflow-authoring-v1"
    assert data["nodes"]["source"]["out"] == [["data", "series"]]
    assert {"start", "end", "freq", "oos_frac"} <= set(data["nodes"]["source"]["params"])
    assert "context_source_id" in data["nodes"]["analysis"]["params"]
    assert data["graph_schema"]["required"] == ["nodes", "edges"]
    assert data["graph_schema"]["properties"]["nodes"]["maxItems"] == 60
    assert data["graph_schema"]["properties"]["edges"]["maxItems"] == 160
    assert "csi300_active" in data["universes"] and "backtest" in data["terminal_types"]
    assert any("不是执行" in rule for rule in data["rules"])
    assert _CATALOG == original


def test_all_tool_schemas_forbid_forged_workspace_and_writes_require_confirmation():
    names = {tool["name"] for tool in ct.WW_TOOL_TABLE}
    for tool in wtools.TOOL_TABLE:
        assert tool["name"] in names
        assert tool["input_schema"]["additionalProperties"] is False
        assert "workspace_id" not in tool["input_schema"]["properties"]
        assert "workspace_id" not in inspect.signature(tool["impl"]).parameters
        if tool["name"] in {"ww_workflow_save", "ww_workflow_run"}:
            assert tool["confirm"] is True


@pytest.mark.parametrize("call", [
    lambda: wtools.workflow_get_impl(), lambda: wtools.workflow_runs_impl(),
    lambda: wtools.workflow_save_impl("private", graph()),
    lambda: wtools.workflow_generate_impl("研究趋势"),
    lambda: wtools.workflow_run_impl("w1234567890ab"),
])
def test_private_tools_without_bound_context_fail_before_any_http(monkeypatch, call):
    monkeypatch.setattr(ct, "_self_get", lambda *a, **k: pytest.fail("no unbound read"))
    monkeypatch.setattr(ct, "_self_post", lambda *a, **k: pytest.fail("no unbound write"))
    token = wt.CTX_WORKSPACE.set(None)
    try:
        out = call()
    finally:
        wt.CTX_WORKSPACE.reset(token)
    assert out["ok"] is False and "上下文" in out["reason"]


def test_workspace_binding_controls_reads_writes_artifacts_and_foreign_ids(bound):
    foreign = bound.workflows.save("私密乙", graph(), workspace_id=bound.other)
    global_record = bound.workflows.save("全局模板", graph())
    mine = wtools.workflow_save_impl("甲的图", graph())
    assert mine["ok"] is True
    artifact = mine["artifact"]
    assert artifact["payload"]["workspace_id"] == bound.workspace
    assert artifact["payload"]["session_id"] == bound.sid
    assert artifact["channel"] == "guanlan.workflow.import"
    assert content(wtools.workflow_get_impl())["items"][0]["id"] == mine["workflow_id"]
    for record in (foreign, global_record):
        out = wtools.workflow_get_impl(record["id"])
        assert out["ok"] is False and "artifact" not in out
        assert wtools.workflow_run_impl(record["id"])["ok"] is False
    before = len(bound.calls)
    assert wtools.workflow_get_impl(mine["workflow_id"] + "?workspace_id=" + bound.other)["ok"] is False
    assert len(bound.calls) == before
    assert bound.starts == []


def test_archived_bound_workspace_cannot_continue_writing(bound):
    bound.console.workspace_store.archive(bound.workspace)
    out = wtools.workflow_save_impl("归档后写入", graph())
    assert out["ok"] is False
    assert bound.calls == []


def test_save_preserves_same_title_user_graph_and_identical_retry_reuses_version(bound):
    old_graph = graph()
    old_graph["nodes"][1]["params"]["expr"] = "rank(-close)"
    old = bound.workflows.save("趋势研究", old_graph, workspace_id=bound.workspace)
    before = (bound.workflows.root / f"{old['id']}.json").read_bytes()

    first = wtools.workflow_save_impl("趋势研究", graph())
    second = wtools.workflow_save_impl("趋势研究", graph())

    assert first["ok"] is True and second["ok"] is True
    assert first["workflow_id"] == second["workflow_id"] != old["id"]
    assert content(first)["status"] == "saved_not_run"
    assert content(second)["reused"] is True
    assert len(bound.workflows.list(workspace_id=bound.workspace)) == 2
    assert (bound.workflows.root / f"{old['id']}.json").read_bytes() == before
    assert len([c for c in bound.calls if c[:2] == ("POST", "/workflow/save")]) == 1
    assert bound.starts == []


def test_modified_immutable_version_name_is_not_silently_overwritten(bound):
    initial = wtools.workflow_save_impl("趋势研究", graph())
    record = bound.workflows.get(initial["workflow_id"], workspace_id=bound.workspace)
    changed = graph()
    changed["nodes"][1]["params"]["expr"] = "rank(-close)"
    bound.workflows.save(record["name"], changed, workspace_id=bound.workspace)
    before = len(bound.calls)

    retry = wtools.workflow_save_impl("趋势研究", graph())

    assert retry["ok"] is False and "拒绝覆盖" in retry["reason"]
    assert all(method == "GET" for method, _, _ in bound.calls[before:])


def test_run_uses_fixed_id_until_new_run_is_explicit_boolean_true(bound):
    saved = wtools.workflow_save_impl("趋势研究", graph())
    first = wtools.workflow_run_impl(saved["workflow_id"])
    retry = wtools.workflow_run_impl(saved["workflow_id"])
    truthy_string = wtools.workflow_run_impl(saved["workflow_id"], new_run="true")
    assert first["ok"] and retry["ok"]
    assert first["run_id"] == retry["run_id"] == truthy_string["run_id"]
    assert bound.starts == [first["run_id"]]
    assert content(first)["status"] == "queued"
    assert "不代表完成" in content(first)["next"]
    fresh = wtools.workflow_run_impl(saved["workflow_id"], new_run=True)
    assert fresh["run_id"] != first["run_id"] and len(bound.starts) == 2


def test_private_run_discovery_cannot_read_foreign_run(bound):
    private = bound.runs.create(graph=graph(), overrides={}, workspace_id=bound.other)
    assert content(wtools.workflow_runs_impl())["items"] == []
    denied = wtools.workflow_runs_impl(private["id"])
    assert denied["ok"] is False and "artifact" not in denied


def test_runs_preserve_failed_measurement_despite_successful_status_read(bound):
    record = bound.runs.create(graph=graph(), overrides={}, workspace_id=bound.workspace)
    measured = {"ok": False, "reason": "数据覆盖不足", "node_errors": [{"nid": "a", "error": "无有效样本"}],
                "warnings": ["未执行回测"], "node_results": {"a": {"ok": False}}, "all_terminals": []}
    bound.runs.update(record["id"], status="failed", reason="数据覆盖不足", result=measured)
    out = wtools.workflow_runs_impl(record["id"])
    data = content(out)
    assert out["ok"] is True  # Read succeeded; measurement did not.
    assert data["status"] == "failed" and data["result"]["ok"] is False
    assert data["result"]["reason"] == "数据覆盖不足"
    assert data["result"]["node_errors"] == measured["node_errors"]
    assert data["result"]["warnings"] == measured["warnings"]
    assert data["recovery"]["can_resume"] is False


def test_generate_and_validate_never_execute_or_claim_measured_success(bound):
    checked = wtools.workflow_validate_impl(graph())
    assert checked["ok"] is True
    assert checked["validation_scope"] == "graph_structure_and_context_only_not_data_or_profitability"
    assert bound.calls == []
    generated = wtools.workflow_generate_impl("研究低波动趋势，不要改股票池")
    assert generated["ok"] is True
    assert content(generated)["status"] == "generated_not_saved_not_run"
    assert bound.workflows.list(workspace_id=bound.workspace) == [] and bound.starts == []
    bound.generated = {"ok": False, "reason": "生成服务不可用"}
    failed = wtools.workflow_generate_impl("趋势研究")
    assert failed["ok"] is False and "artifact" not in failed


def test_summary_preserves_legacy_terminal_without_all_terminals():
    result = {"ok": True, "terminal": {"kind": "analysis", "node_id": "a", "payload": {
        "ok": True, "headline_ic": {"rank_ic": -.02}, "n_dates": 150}}}
    summary = wtools._result_summary(result)
    assert summary["terminals"] == [{"kind": "analysis", "node_id": "a", "ok": True,
                                      "headline_ic": {"rank_ic": -.02}, "n_dates": 150}]


def test_screen_summary_retains_bounded_verifiable_candidates_and_as_of():
    candidates = [{"code": f"SH{600000+i}", "name": f"候选{i}", "score": i / 100,
                   "reasons": ["MA5上行"]} for i in range(80)]
    result = {"ok": True, "all_terminals": [{"kind": "data_screen", "node_id": "screen", "payload": {
        "ok": True, "as_of": "2026-09-04", "run_id": "ds_test", "candidates": candidates,
        "n_loaded": 1000, "n_matched": 80, "reason": None}}]}
    summary = wtools._result_summary(result)
    screen = summary["terminals"][0]
    assert screen["as_of"] == "2026-09-04"
    assert screen["run_id"] == "ds_test"
    assert 1 <= len(screen["candidates"]) <= 50
    assert screen["candidates"][0]["code"] == candidates[0]["code"]
    assert screen["candidates"][0]["reasons"] == candidates[0]["reasons"]
    assert screen["n_matched"] == 80
    assert screen["n_loaded"] == 1000
    assert screen["candidate_rows_total"] == 80
    assert screen["candidate_rows_truncated"] is True


def test_authoring_rejects_isolated_factor_and_missing_source_context():
    isolated = graph()
    isolated["nodes"].append({"id": "forgotten", "type": "formula", "params": {"expr": "rank(volume)"}})
    checked = authoring.validate_authoring(isolated)
    assert checked["ok"] is False
    assert any("forgotten" in error and "下游" in error for error in checked["errors"])
    no_source = graph()
    no_source["nodes"][2]["params"] = {}
    checked = authoring.validate_authoring(no_source)
    assert checked["ok"] is False and any("数据源上下文" in error for error in checked["errors"])


def test_authoring_does_not_guess_first_of_multiple_source_ancestors():
    multi = graph()
    multi["nodes"] = multi["nodes"][:2] + [
        {"id": "src2", "type": "source", "params": {"universe": "csi_fast"}},
        {"id": "f2", "type": "formula", "params": {"expr": "rank(volume)", "context_source_id": "src2"}},
        {"id": "fe", "type": "feature", "params": {}},
        {"id": "bt", "type": "backtest", "params": {}},
    ]
    multi["edges"] = [
        {"from": ["src", "data"], "to": ["fe", "src"]},
        {"from": ["f", "out"], "to": ["fe", "feat"]},
        {"from": ["f2", "out"], "to": ["fe", "feat"]},
        {"from": ["fe", "fe"], "to": ["bt", "factor"]},
    ]
    checked = authoring.validate_authoring(multi)
    assert checked["ok"] is False
    assert any("多数据源" in error and "bt" in error for error in checked["errors"])


@pytest.mark.parametrize("start,end", [("2026-13-01", "2026-09-04"), ("2026-09-04", "2025-01-01")])
def test_authoring_rejects_bad_source_dates(start, end):
    value = graph()
    value["nodes"][0]["params"].update(start=start, end=end)
    assert authoring.validate_authoring(value)["ok"] is False
