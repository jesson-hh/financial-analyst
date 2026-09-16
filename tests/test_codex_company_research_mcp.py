import asyncio

import pytest

from guanlan_v2.console import workspace_tools as wt
from guanlan_v2.console.store import ConsoleStore
from guanlan_v2.glmcp.company_research import (
    CODEX_COMPANY_RESEARCH_RECEIPT_SCHEMA,
    CODEX_COMPANY_RESEARCH_RECIPE,
    CODEX_COMPANY_RESEARCH_TOOL,
    artifact_for_finalized_company_asset,
    company_research_binding_sha256,
    find_finalized_company_asset,
    validate_finalized_company_asset,
    validate_codex_company_research_arguments,
)
from guanlan_v2.glmcp.scope import (
    CTX_CODEX_MCP_SCOPE,
    register_codex_scope,
    resolve_codex_scope,
    revoke_codex_scope,
    workspace_root_sha256,
    workspace_subject_sha256,
)


def _world(tmp_path):
    store = ConsoleStore(tmp_path)
    workspace = store.workspace_store.create({
        "kind": "stock",
        "code": "SH603986",
        "name": "兆易创新",
        "display_name": "兆易创新",
        "root_mode": "managed",
        "agent_mode": "research",
    })
    sid = store.create_session(workspace_id=workspace["id"])["id"]
    return store, workspace, sid


def _receipt(*, sid, workspace, asof):
    subject_digest = workspace_subject_sha256(workspace)
    return {
        "ok": True,
        "content": "公司研究 v1 已受理；当前 queued，不是完成回执。",
        "receipt": {
            "schema_version": CODEX_COMPANY_RESEARCH_RECEIPT_SCHEMA,
            "recipe_id": CODEX_COMPANY_RESEARCH_RECIPE,
            "job_id": "bg_0123456789",
            "status": "queued",
            "sid": sid,
            "workspace_id": workspace["id"],
            "code": workspace["code"],
            "binding_sha256": company_research_binding_sha256(
                sid=sid,
                workspace_id=workspace["id"],
                code=workspace["code"],
                subject_sha256=subject_digest,
                root_sha256=workspace_root_sha256(workspace["root_path"]),
            ),
            "asof": asof,
            "accepted_at": "2026-09-03T01:02:03+00:00",
        },
    }


async def _dispatch_with_scope(scope, store, arguments):
    import guanlan_v2.glmcp.server as mcp_server

    token = CTX_CODEX_MCP_SCOPE.set(scope)
    try:
        return await mcp_server.dispatch_tool(
            CODEX_COMPANY_RESEARCH_TOOL,
            arguments,
            console_store=store,
            scope_required=True,
        )
    finally:
        CTX_CODEX_MCP_SCOPE.reset(token)


def test_company_research_tool_is_codex_only_with_closed_arguments():
    import guanlan_v2.glmcp.server as mcp_server

    assert CODEX_COMPANY_RESEARCH_TOOL not in mcp_server._by_name()
    declaration = mcp_server._codex_by_name()[CODEX_COMPANY_RESEARCH_TOOL]
    assert set(declaration["inputSchema"]["properties"]) == {"asof"}
    assert declaration["inputSchema"]["additionalProperties"] is False
    assert declaration["gated"] is True
    assert declaration["destructive"] is True
    with pytest.raises(ValueError, match="不接受"):
        validate_codex_company_research_arguments({"workspace_id": "rw_other"})

    public = asyncio.run(mcp_server.dispatch_tool(
        CODEX_COMPANY_RESEARCH_TOOL, {}, scope_required=False,
    ))
    assert "未知工具" in public[0].text


def test_codex_company_research_confirms_then_launches_once_with_frozen_args(
    tmp_path,
):
    store, workspace, sid = _world(tmp_path)
    confirmed = []
    launched = []

    async def confirm(name, arguments):
        confirmed.append((name, dict(arguments)))
        arguments["asof"] = "2001-01-01"
        return True

    async def launch(arguments):
        launched.append(dict(arguments))
        return _receipt(
            sid=sid, workspace=workspace, asof=arguments.get("asof"),
        )

    raw_token = register_codex_scope(
        sid=sid,
        workspace_id=workspace["id"],
        workspace_code=workspace["code"],
        workspace_subject_sha256=workspace_subject_sha256(workspace),
        workspace_root_sha256=workspace_root_sha256(
            wt._root_for(store, workspace["id"]),
        ),
        allowed_tools=frozenset({CODEX_COMPANY_RESEARCH_TOOL}),
        confirmation_required_tools=frozenset({CODEX_COMPANY_RESEARCH_TOOL}),
        confirm_callback=confirm,
        company_research_launch_callback=launch,
    )
    scope = resolve_codex_scope(raw_token)
    assert scope is not None
    try:
        result = asyncio.run(_dispatch_with_scope(
            scope, store, {"asof": "2026-08-30"},
        ))
        assert result[0].text.startswith("公司研究 v1 已受理")
        assert confirmed == [(
            CODEX_COMPANY_RESEARCH_TOOL, {"asof": "2026-08-30"},
        )]
        assert launched == [{"asof": "2026-08-30"}]

        injected = asyncio.run(_dispatch_with_scope(
            scope, store, {
                "asof": "2026-08-30", "workspace_id": workspace["id"],
            },
        ))
        assert injected.isError is True
        assert "schema 未声明字段" in injected.content[0].text
        assert len(confirmed) == 1 and len(launched) == 1
    finally:
        revoke_codex_scope(raw_token)


def test_codex_company_research_reject_and_post_confirm_drift_never_launch(
    tmp_path,
):
    store, workspace, sid = _world(tmp_path)
    launches = []

    async def launch(arguments):
        launches.append(dict(arguments))
        return _receipt(sid=sid, workspace=workspace, asof=None)

    async def run_one(confirm, token_suffix):
        raw_token = register_codex_scope(
            sid=sid,
            workspace_id=workspace["id"],
            workspace_code=workspace["code"],
            workspace_subject_sha256=workspace_subject_sha256(workspace),
            workspace_root_sha256=workspace_root_sha256(
                wt._root_for(store, workspace["id"]),
            ),
            allowed_tools=frozenset({CODEX_COMPANY_RESEARCH_TOOL}),
            confirmation_required_tools=frozenset({CODEX_COMPANY_RESEARCH_TOOL}),
            confirm_callback=confirm,
            company_research_launch_callback=launch,
        )
        scope = resolve_codex_scope(raw_token)
        try:
            return await _dispatch_with_scope(scope, store, {})
        finally:
            revoke_codex_scope(raw_token)

    async def reject(_name, _arguments):
        return False

    rejected = asyncio.run(run_one(reject, "reject"))
    assert rejected.isError is True
    assert "未批准或已取消" in rejected.content[0].text
    assert launches == []

    async def drift(_name, _arguments):
        store.workspace_store.update(workspace["id"], {"code": "SH600000"})
        return True

    drifted = asyncio.run(run_one(drift, "drift"))
    assert drifted.isError is True
    assert "标的或目录已变更" in drifted.content[0].text
    assert launches == []


def test_finalized_asset_adoption_is_exact_and_ambiguous_state_fails_closed(
    monkeypatch,
):
    from guanlan_v2.console import workspace_tools

    exact = {
        "id": "wa_0123456789abcdef",
        "workspace_id": "rw_0123456789ab",
        "source": "ww_workspace_company_research_run",
        "prepared_asset_id": "wa_aaaaaaaaaaaaaaaa",
        "report_sha256": "b" * 64,
        "run_id": "run_1",
        "name": "兆易创新 · 深度研报",
        "attachments": {"documents": [{"document_id": "doc_1"}]},
    }
    rows = [exact]
    monkeypatch.setattr(
        workspace_tools, "_list_workspace_assets_complete",
        lambda _store, _workspace_id, limit=500: list(rows),
    )
    found = find_finalized_company_asset(
        object(), workspace_id="rw_0123456789ab",
        prepared_asset_id="wa_aaaaaaaaaaaaaaaa", report_sha256="b" * 64,
    )
    assert found == exact
    artifact = artifact_for_finalized_company_asset(found)
    assert artifact["payload"]["asset_id"] == exact["id"]
    assert artifact["payload"]["documents"] == [{"document_id": "doc_1"}]
    with pytest.raises(RuntimeError, match="report_sha256"):
        validate_finalized_company_asset(
            {**exact, "report_sha256": "c" * 64},
            workspace_id="rw_0123456789ab",
            prepared_asset_id="wa_aaaaaaaaaaaaaaaa",
            report_sha256="b" * 64,
        )

    rows.append({**exact, "id": "wa_fedcba9876543210"})
    with pytest.raises(RuntimeError, match="多个最终资产"):
        find_finalized_company_asset(
            object(), workspace_id="rw_0123456789ab",
            prepared_asset_id="wa_aaaaaaaaaaaaaaaa",
            report_sha256="b" * 64,
        )
