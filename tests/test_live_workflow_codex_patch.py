"""Read-only, in-memory tests for the six-file live deployment patch.

Run with pytest. Executing this file directly writes only an audit hash manifest
beside the patch; neither mode writes live files or imports production apps.
"""
import ast
import copy
import hashlib
import json
import sys
import threading
import types
from contextlib import nullcontext
from contextvars import ContextVar
from pathlib import Path
from typing import Any, Dict, List

import pytest

ROOT = Path(__file__).resolve().parents[1]
PATCH = ROOT / "var/agent_workflow_20260905/live_backend.patch"
pytestmark = [
    pytest.mark.realdata,
    pytest.mark.skipif(not PATCH.is_file(), reason="private live patch fixture is not distributed"),
]
WORKFLOW_NAMES = frozenset("ww_workflow_" + x for x in (
    "catalog", "generate", "validate", "save", "get", "run", "runs"))
CONFIRM = {"ww_workflow_save", "ww_workflow_run"}


def patched_sources():
    """Apply only exact, unique hunks in memory, preserving original evidence."""
    sections, current, hunks = {}, None, None
    for line in PATCH.read_text(encoding="utf-8").splitlines():
        if line.startswith("*** Update File: "):
            current = line.removeprefix("*** Update File: ")
            sections[current] = []
        elif line == "@@":
            hunks = []
            sections[current].append(hunks)
        elif line.startswith("*** "):
            continue
        else:
            assert hunks is not None and line[:1] in {" ", "+", "-"}, line
            hunks.append(line)
    result, manifest = {}, {}
    for path, chunks in sections.items():
        raw = Path(path).read_bytes()
        source = raw.decode("utf-8").replace("\r\n", "\n")
        previous_end = 0
        for chunk in chunks:
            old = "\n".join(x[1:] for x in chunk if x[0] in " -") + "\n"
            new = "\n".join(x[1:] for x in chunk if x[0] in " +") + "\n"
            assert source.count(old) == 1, (path, "nonunique/missing exact context", old)
            position = source.index(old)
            assert position >= previous_end, (path, "hunks must be in source order")
            source = source.replace(old, new, 1)
            previous_end = position + len(new)
        ast.parse(source, filename=path)
        result[path] = source
        manifest[path] = {"old_sha256": hashlib.sha256(raw).hexdigest(),
                          "old_bytes": len(raw), "hunks": len(chunks),
                          "new_lf_sha256": hashlib.sha256(source.encode()).hexdigest()}
    return result, manifest


def source(suffix):
    return next(value for key, value in patched_sources()[0].items() if key.endswith(suffix))


def constants(text, prefix):
    scope = {"CODEX_COMPANY_RESEARCH_TOOL": "ww_workspace_company_research_run"}
    selected = [n for n in ast.parse(text).body if isinstance(n, ast.Assign)
                and all(isinstance(t, ast.Name) and t.id.startswith(prefix) for t in n.targets)]
    exec(compile(ast.Module(body=selected, type_ignores=[]), "constants-only", "exec"), scope)
    return scope


@pytest.fixture
def bridge(monkeypatch):
    # Use the real literal node catalog without importing app/router/store code.
    api = ast.parse((ROOT / "guanlan_v2/workflow/api.py").read_text(encoding="utf-8"))
    value = next(n.value for n in api.body if isinstance(n, ast.AnnAssign)
                 and isinstance(n.target, ast.Name) and n.target.id == "_CATALOG")
    node_catalog = ast.literal_eval(value)
    node_catalog["source"]["params"] += ["start", "end", "freq", "oos_frac", "wf_refit"]
    fake = types.ModuleType("guanlan_v2.workflow.authoring")
    fake.catalog = lambda: {"nodes": node_catalog}
    monkeypatch.setitem(sys.modules, "guanlan_v2.workflow.authoring", fake)
    mod = types.ModuleType("isolated_workflow_artifact_bridge")
    exec(compile(source("/artifact_bridge.py"), "patched_artifact_bridge", "exec"), mod.__dict__)
    return mod


def card():
    return {"kind": "workflow", "page": "factor", "channel": "guanlan.workflow.import",
            "payload": {"name": "量价趋势", "workspace_id": "rw_1234567890ab",
                "workflow_id": "w1234567890ab", "run_id": "wr_1234567890abcdef",
                "session_id": "private-session-not-to-be-exported",
                "graph": {"schema_version": 3, "nodes": [
                    {"id": "src", "type": "source", "params": {"universe": "csi_fast", "start": "2025-01-01"}},
                    {"id": "f", "type": "formula", "x": 200, "params": {"expr": "rank(close)"}},
                    {"id": "a", "type": "analysis", "params": {"context_source_id": "src"}},
                ], "edges": [{"from": ["f", "out"], "to": ["a", "factor"]}]}},
            "ref": "ignore-caller-ref"}


def build(bridge, value, tool="ww_workflow_get", wid="rw_1234567890ab"):
    return bridge.build_artifact_envelope({"artifact": value}, tool_name=tool, workspace_id=wid)


def test_six_files_exact_context_and_syntax_no_live_changes():
    _, hashes = patched_sources()
    assert len(hashes) == 6
    for path, row in hashes.items():
        assert hashlib.sha256(Path(path).read_bytes()).hexdigest() == row["old_sha256"]


def test_codex_only_seven_new_permissions_and_preserved_risk_partition():
    path = "G:/guanlan-v2/guanlan_v2/console/codex_backend.py"
    before = constants(Path(path).read_text(encoding="utf-8"), "_CODEX_")
    after = constants(source("/codex_backend.py"), "_CODEX_")
    assert after["_CODEX_ALLOWED_TOOLS"] - before["_CODEX_ALLOWED_TOOLS"] == WORKFLOW_NAMES
    assert after["_CODEX_CONFIRMATION_REQUIRED_TOOLS"] - before["_CODEX_CONFIRMATION_REQUIRED_TOOLS"] == CONFIRM
    assert after["_CODEX_WORKFLOW_QUERY_TOOLS"] == WORKFLOW_NAMES - CONFIRM
    for field in ("_CODEX_AUTO_QUERY_TOOLS", "_CODEX_WORKSPACE_SNAPSHOT_TOOLS",
                  "_CODEX_FIXED_PRESENTATION_TOOLS", "_CODEX_CLOSED_RISK_TOOLS",
                  "_CODEX_DISALLOWED_PRESENTATION_TOOLS"):
        assert after[field] == before[field]
    assert not after["_CODEX_ALLOWED_TOOLS"] & after["_CODEX_CLOSED_RISK_TOOLS"]


def test_registration_order_and_existing_live_enhancements_remain():
    s = source("/tools.py")
    assert s.index("WW_TOOL_TABLE.extend(_WORKFLOW_TOOL_TABLE)") < s.index("ALL_CONSOLE_TOOL_TABLE =")
    assert "[*WW_TOOL_TABLE, *CHANNEL_TOOL_TABLE]" in s
    assert s.index("WW_TOOL_TABLE.extend(_WORKFLOW_TOOL_TABLE)") < s.index("_WW_REACHABLE_ENDPOINTS =")
    before = Path("G:/guanlan-v2/guanlan_v2/console/workspace_tools.py").read_text(encoding="utf-8")
    after = source("/workspace_tools.py")
    # Extract permission constants only, rather than importing workspace state.
    names = {"READ_TOOLS", "DERIVED_TOOLS", "DATA_SNAPSHOT_TOOLS", "REVIEW_TOOLS",
             "MUTATING_TOOLS", "ALL_WORKSPACE_TOOLS", "AUTONOMOUS_SAFE_TOOLS", "WORKFLOW_CONTEXT_TOOLS"}
    def read_sets(text):
        scope = {}
        nodes = [n for n in ast.parse(text).body if isinstance(n, ast.Assign)
                 and all(isinstance(t, ast.Name) and t.id in names for t in n.targets)]
        exec(compile(ast.Module(body=nodes, type_ignores=[]), "workspace-sets", "exec"), scope)
        return scope
    old, new = read_sets(before), read_sets(after)
    assert new["ALL_WORKSPACE_TOOLS"] - old["ALL_WORKSPACE_TOOLS"] == WORKFLOW_NAMES
    assert new["MUTATING_TOOLS"] == old["MUTATING_TOOLS"]
    assert new["AUTONOMOUS_SAFE_TOOLS"] == old["AUTONOMOUS_SAFE_TOOLS"]
    assert not CONFIRM & new["AUTONOMOUS_SAFE_TOOLS"]


def test_public_workspace_required_scoped_hidden_and_unknown_arguments_rejected():
    scope = {"Dict": Dict, "Any": Any, "List": List, "deepcopy": copy.deepcopy,
             "ALL_WORKSPACE_TOOLS": WORKFLOW_NAMES,
             "_workspace_tool_names": lambda: WORKFLOW_NAMES,
             "_WORKSPACE_ID_PROPERTY": {"type": "string"},
             "_CODEX_HIDDEN_INPUT_FIELDS": {},
             "_CODEX_CLOSED_INPUT_SCHEMAS": constants(source("/server.py"), "_CODEX_")["_CODEX_CLOSED_INPUT_SCHEMAS"]}
    texts = [Path("G:/guanlan-v2/guanlan_v2/glmcp/tooltable.py").read_text(encoding="utf-8"),
             source("/server.py")]
    wanted = {"_mcp_schema", "_scoped_schema", "_unexpected_scoped_arguments"}
    functions = [node for text in texts for node in ast.parse(text).body
                 if isinstance(node, ast.FunctionDef) and node.name in wanted]
    exec(compile(ast.Module(body=functions, type_ignores=[]), "schema-only", "exec"), scope)
    for name in WORKFLOW_NAMES:
        original = {"type": "object", "additionalProperties": False,
                    "properties": {"goal": {"type": "string"}}, "required": ["goal"]}
        public = scope["_mcp_schema"](name, original)
        assert "workspace_id" in public["required"] and "workspace_id" not in original["properties"]
        declaration = {"name": name, "inputSchema": public}
        scoped = scope["_scoped_schema"](declaration, scoped=True)
        assert "workspace_id" not in scoped["required"] and "workspace_id" not in scoped["properties"]
        assert scope["_unexpected_scoped_arguments"](declaration, {"goal": "trend", "workspace_id": "rw_general"}) == []
        assert scope["_unexpected_scoped_arguments"](declaration, {"goal": "trend", "root": "C:/secret", "shell": "cmd"}) == ["root", "shell"]


@pytest.fixture
def scoped_runner(monkeypatch):
    """Execute the actual patched binding function with thread-safe fake stores."""
    names = ["guanlan_v2", "guanlan_v2.console", "guanlan_v2.console.tools",
             "guanlan_v2.console.workspace_tools", "guanlan_v2.glmcp", "guanlan_v2.glmcp.scope"]
    mods = {name: types.ModuleType(name) for name in names}
    for name, mod in mods.items():
        mod.__path__ = []
        monkeypatch.setitem(sys.modules, name, mod)
        if "." in name:
            parent, child = name.rsplit(".", 1)
            setattr(mods[parent], child, mod)
    ct, wt, sc = (mods[n] for n in names[2:4] + names[5:])
    ct.CTX_STORE, ct.CTX_SID = ContextVar("test_store"), ContextVar("test_sid")
    wt.CTX_WORKSPACE = ContextVar("test_workspace")
    wt.WORKFLOW_CONTEXT_TOOLS = WORKFLOW_NAMES
    wt.WorkspaceToolError = RuntimeError
    wt.allowed_tools_for_workspace = lambda names, workspace: names
    wt._root_for = lambda store, wid: "opaque-test-root"
    sc.codex_scope_is_active = lambda scope: scope.active
    sc.workspace_root_sha256 = lambda root: "root-hash"
    sc.workspace_subject_sha256 = lambda workspace: "subject-hash"
    workspace = {"id": "rw_1234567890ab", "code": "SH600667"}
    class WorkspaceStore:
        _lock = threading.RLock()
        def require(self, wid, active_only=True):
            with self._lock:
                assert wid == workspace["id"] and active_only
                return dict(workspace)
    store = types.SimpleNamespace(workspace_store=WorkspaceStore())
    scope = types.SimpleNamespace(active=True, workspace_code="SH600667",
        workspace_subject_sha256="subject-hash", workspace_root_sha256="root-hash")
    fn = next(n for n in ast.parse(source("/server.py")).body
              if isinstance(n, ast.FunctionDef) and n.name == "_run_workspace_impl")
    namespace = {"Dict": Dict, "Any": Any, "nullcontext": nullcontext}
    exec(compile(ast.Module(body=[fn], type_ignores=[]), "actual-scoped-binding", "exec"), namespace)
    return namespace["_run_workspace_impl"], store, scope, ct, wt


@pytest.mark.parametrize("name", ["ww_workflow_save", "ww_workflow_run"])
def test_workflow_self_http_handler_can_acquire_lock_before_response(scoped_runner, name):
    fn, store, scope, ct, wt = scoped_runner
    observed = []
    def impl():
        assert ct.CTX_SID.get() == "test-session"
        assert wt.CTX_WORKSPACE.get()["workspace_id"] == "rw_1234567890ab"
        def http_handler():
            acquired = store.workspace_store._lock.acquire(timeout=0.2)
            observed.append(acquired)
            if acquired:
                try:
                    store.workspace_store.require("rw_1234567890ab", active_only=True)
                finally:
                    store.workspace_store._lock.release()
        thread = threading.Thread(target=http_handler)
        thread.start()
        thread.join(timeout=0.5)
        assert not thread.is_alive()
        return "handler-completed"
    assert fn(impl, name, {"workspace_id": "rw_1234567890ab"}, store,
              "test-session", codex_scope=scope) == "handler-completed"
    assert observed == [True], "No timeout followed by delayed side effect"
    assert ct.CTX_SID.get(None) is None and wt.CTX_WORKSPACE.get(None) is None


def test_project_tool_still_holds_binding_lock(scoped_runner):
    fn, store, scope, _, _ = scoped_runner
    observed = []
    def impl():
        def other_thread():
            acquired = store.workspace_store._lock.acquire(timeout=0.03)
            observed.append(acquired)
            if acquired:
                store.workspace_store._lock.release()
        thread = threading.Thread(target=other_thread)
        thread.start()
        thread.join(timeout=0.2)
        assert not thread.is_alive()
    fn(impl, "ww_workspace_apply_patch", {"workspace_id": "rw_1234567890ab"},
       store, "test-session", codex_scope=scope)
    assert observed == [False], "Project path permissions must remain atomic"


def test_workflow_binding_still_rejects_revoked_or_changed_scope(scoped_runner):
    fn, store, scope, _, _ = scoped_runner
    impl = lambda: pytest.fail("must not execute")
    scope.active = False
    with pytest.raises(RuntimeError, match="撤销"):
        fn(impl, "ww_workflow_save", {"workspace_id": "rw_1234567890ab"}, store, codex_scope=scope)
    scope.active = True
    scope.workspace_code = "SZ000636"
    with pytest.raises(RuntimeError, match="变更"):
        fn(impl, "ww_workflow_run", {"workspace_id": "rw_1234567890ab"}, store, codex_scope=scope)


def test_actual_codex_developer_prompt_reaches_model_and_allows_only_private_fit():
    text = source("/codex_backend.py")
    assert "self.system_prompt\n" in text
    assert '"developerInstructions": developer_instructions' in text
    assert '"thread/resume", {"threadId": thread_id, **common}' in text
    assert "流程内部临时模型拟合" in text and "独立模型训练入库" in text
    assert "实盘放行或默认模型变更" in text


@pytest.mark.parametrize("tool", sorted(WORKFLOW_NAMES - {"ww_workflow_catalog", "ww_workflow_validate"}))
def test_workflow_card_roundtrip_preserves_graph_and_binds_workspace(bridge, tool):
    original = card()
    envelope = build(bridge, original, tool)
    assert envelope is not None
    restored = bridge.read_artifact_envelope(envelope, tool_name=tool, workspace_id="rw_1234567890ab")
    assert restored["payload"]["graph"] == original["payload"]["graph"]
    assert restored["payload"]["workspace_id"] == "rw_1234567890ab"
    assert "session_id" not in restored["payload"]
    assert restored["ref"] == original["payload"]["run_id"]
    assert restored["payload"]["graph"] is not original["payload"]["graph"]
    assert bridge.read_artifact_envelope(envelope, tool_name=tool, workspace_id="rw_aaaaaaaaaaaa") is None


@pytest.mark.parametrize("mutation", [
    lambda c: c.update(channel="arbitrary.transport"),
    lambda c: c.update(page="settings"),
    lambda c: c["payload"].update(workspace_id="rw_aaaaaaaaaaaa"),
    lambda c: c["payload"].update(workflow_id="../secret"),
    lambda c: c["payload"].update(run_id="http://example.invalid"),
    lambda c: c["payload"].update(name="C:/Users/secret"),
    lambda c: c["payload"].update(url="https://example.invalid"),
    lambda c: c["payload"]["graph"].update(source_blob="secret"),
    lambda c: c["payload"]["graph"]["nodes"][0].update(code="print(1)"),
    lambda c: c["payload"]["graph"]["nodes"][0]["params"].update(source_blob="secret"),
    lambda c: c["payload"]["graph"]["nodes"][0]["params"].update(universe="C:/secret"),
    lambda c: c["payload"]["graph"]["nodes"][1]["params"].update(expr="close.to_csv('/tmp/leak')"),
    lambda c: c["payload"]["graph"]["nodes"][1].update(x=float("nan")),
    lambda c: c["payload"]["graph"]["nodes"][1].update(x=True),
    lambda c: c["payload"]["graph"]["nodes"][1].update(id="src"),
    lambda c: c["payload"]["graph"]["nodes"][1].update(type="arbitrary_code"),
    lambda c: c["payload"]["graph"]["edges"][0].update(to=["missing", "factor"]),
    lambda c: c["payload"]["graph"]["edges"][0].update(url="https://example.invalid"),
    lambda c: c["payload"]["graph"].update(schema_version=True),
    lambda c: c["payload"]["graph"].update(nodes=[]),
])
def test_workflow_shape_tampering_is_closed(bridge, mutation):
    value = card()
    mutation(value)
    assert build(bridge, value) is None


@pytest.mark.parametrize("tool", ["ww_quote", "ww_workspace_asset_get", "ww_workflow_catalog", "ww_workflow_validate"])
def test_only_actual_workflow_card_emitters_can_use_new_shape(bridge, tool):
    assert build(bridge, card(), tool) is None


def test_generated_preview_without_persistence_ids(bridge):
    value = card()
    value["payload"].update(workflow_id=None, run_id=None)
    value["payload"]["graph"].pop("schema_version")
    envelope = build(bridge, value, "ww_workflow_generate")
    assert envelope is not None and envelope["artifact"]["ref"] is None


def test_original_artifact_limits_and_shapes_stay_supported(bridge):
    assert bridge.MAX_ARTIFACT_ENVELOPE_BYTES == 65536
    report = {"kind": "ic_report", "payload": {"expr": "rank(close)", "name": "trend"}}
    assert build(bridge, report, "ww_factor_analyze")["artifact"]["kind"] == "ic_report"
    assert build(bridge, {"kind": "arbitrary", "payload": {}}, "ww_workflow_get") is None


if __name__ == "__main__":
    _, manifest = patched_sources()
    output = PATCH.with_name("live_backend_hashes.json")
    output.write_text(json.dumps({"patch_sha256": hashlib.sha256(PATCH.read_bytes()).hexdigest(),
        "files": manifest, "live_files_written": False,
        "required_co_deployment": ["console/workflow_tools.py", "workflow/authoring.py with strict DSL guard"],
        "verification": "exact unique contexts and AST syntax passed for all six files"},
        ensure_ascii=False, indent=2), encoding="utf-8")
    print(output)
