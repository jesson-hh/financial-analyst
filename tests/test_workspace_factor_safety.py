from __future__ import annotations

from guanlan_v2.console import workspace_tools
from guanlan_v2.console.store import ConsoleStore


def _editable_workspace(tmp_path):
    store = ConsoleStore(root=tmp_path)
    workspace = store.workspace_store.create({
        "kind": "stock",
        "code": "SH600519",
        "name": "fixture",
        "display_name": "fixture",
        "root_mode": "managed",
        "agent_mode": "collaborative",
    })
    sid = store.create_session(workspace_id=workspace["id"])["id"]
    token = workspace_tools.CTX_WORKSPACE.set(
        workspace_tools.bind_workspace_context(store, sid)
    )
    return store, workspace, token


def test_workspace_factor_save_rejects_series_method_write(tmp_path):
    store, workspace, token = _editable_workspace(tmp_path)
    try:
        result = workspace_tools.workspace_factor_save_impl(
            name="unsafe",
            expr="close.to_csv('G:/should-never-exist.csv')",
        )
    finally:
        workspace_tools.CTX_WORKSPACE.reset(token)

    assert result["ok"] is False
    assert "属性/方法访问" in result["content"]
    assert workspace_tools.list_workspace_assets(
        store, workspace["id"], limit=20,
    ) == []


def test_workspace_factor_save_rejects_non_operator_call(tmp_path):
    store, workspace, token = _editable_workspace(tmp_path)
    try:
        result = workspace_tools.workspace_factor_save_impl(
            name="unsafe_call",
            expr="close()",
        )
    finally:
        workspace_tools.CTX_WORKSPACE.reset(token)

    assert result["ok"] is False
    assert "白名单 DSL 算子" in result["content"]
    assert workspace_tools.list_workspace_assets(
        store, workspace["id"], limit=20,
    ) == []


def test_workspace_factor_save_accepts_whitelisted_dsl_call(tmp_path):
    store, workspace, token = _editable_workspace(tmp_path)
    try:
        result = workspace_tools.workspace_factor_save_impl(
            name="safe_momentum",
            expr="rank(-delta(close, 5))",
        )
    finally:
        workspace_tools.CTX_WORKSPACE.reset(token)

    assert result["ok"] is True
    assets = workspace_tools.list_workspace_assets(
        store, workspace["id"], limit=20,
    )
    assert len(assets) == 1
    assert assets[0]["expr"] == "rank(-delta(close, 5))"
