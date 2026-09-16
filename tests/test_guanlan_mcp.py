import asyncio


def test_build_mcp_tools_derivation():
    import guanlan_v2.glmcp.tooltable as tt
    import guanlan_v2.console.tools as ct
    tools = tt.build_mcp_tools()
    names = {t["name"] for t in tools}
    ww = {t["name"] for t in ct.WW_TOOL_TABLE}
    assert "ww_plan_update" not in names and "ww_show_page" not in names and "ww_seats_bind" not in names      # 去除 console-UI-only & 前端信封
    assert (ww - {"ww_plan_update", "ww_show_page", "ww_seats_bind"}) <= names                  # 其余 ww_ 全在
    assert {"alpha_list", "alpha_compare", "alpha_forge", "factor_report"} <= names
    engine_names = set(tt._engine_registry())
    expected = (ww - tt._EXCLUDED) | {"guanlan_workspace_list"} | (
        set(tt._ALPHA_ZOO) & engine_names
    )
    assert names == expected
    # P10 Task 10 的四个新工具都不在 _EXCLUDED(非 console-UI-only / 非前端信封),故自动全进 MCP
    assert {"ww_orchestrate_start", "ww_orchestrate_status",
            "ww_orchestrate_runs", "ww_ta_ingest"} <= names
    assert {"ww_watch_alert_research", "ww_watch_alerts", "ww_watch_alert_manage"} <= names
    assert {"ww_events_search", "ww_market_clues"} <= names


def test_build_mcp_tools_annotations_and_gate():
    from guanlan_v2.glmcp.tooltable import build_mcp_tools
    by = {t["name"]: t for t in build_mcp_tools()}
    assert by["ww_model_delete"]["destructive"] and by["ww_model_delete"]["gated"]      # confirm=True
    assert by["ww_model_set_default"]["gated"]
    assert by["ww_screen_factors"]["read_only"] and not by["ww_screen_factors"]["gated"]  # 只读
    assert (not by["ww_memory_write"]["read_only"]) and (not by["ww_memory_write"]["gated"])  # 写但不锁
    assert by["alpha_forge"]["destructive"] and by["alpha_forge"]["gated"]              # 唯一 alpha 写
    assert not by["alpha_compare"]["read_only"] and not by["alpha_compare"]["gated"]
    assert not by["event_report"]["read_only"] and not by["event_report"]["gated"]
    for legacy in (
        "ww_factor_analyze", "ww_backtest", "ww_factor_compose",
        "ww_feature_build", "ww_factor_tsic",
    ):
        assert not by[legacy]["read_only"] and not by[legacy]["gated"]
    assert by["alpha_list"]["read_only"] and not by["alpha_list"]["gated"]
    assert not by["alpha_bench"]["read_only"] and not by["alpha_bench"]["gated"]
    assert not by["factor_report"]["read_only"] and not by["factor_report"]["gated"]
    assert not by["ww_screen_run"]["read_only"] and not by["ww_screen_run"]["gated"]
    assert by["ww_watch_alert_research"]["read_only"] and not by["ww_watch_alert_research"]["gated"]
    assert by["ww_watch_alerts"]["read_only"] and not by["ww_watch_alerts"]["gated"]
    assert by["ww_watch_alert_manage"]["destructive"] and by["ww_watch_alert_manage"]["gated"]
    for name in ("ww_events_search", "ww_market_clues"):
        assert by[name]["read_only"]
        assert not by[name]["destructive"] and not by[name]["gated"]
        assert by[name]["inputSchema"]["required"] == ["asof"]
    search_props = by["ww_events_search"]["inputSchema"]["properties"]
    clue_props = by["ww_market_clues"]["inputSchema"]["properties"]
    assert search_props["query"]["maxLength"] == 512
    assert search_props["cursor"]["maxLength"] == 4096
    assert search_props["sources"]["maxItems"] == 50
    assert clue_props["entities"]["maxItems"] == 50


def test_dispatch_readonly_wraps_impl(monkeypatch):
    import guanlan_v2.glmcp.server as ms

    async def fake_to_thread(fn, **kw):
        return {"ok": True, "content": "RESULT_X"}
    monkeypatch.setattr(ms.asyncio, "to_thread", fake_to_thread)
    res = asyncio.run(ms.dispatch_tool("ww_screen_factors", {}))
    assert res[0].text == "RESULT_X"


def test_dispatch_write_gate(monkeypatch):
    import guanlan_v2.glmcp.server as ms
    monkeypatch.delenv("GUANLAN_MCP_WRITE", raising=False)
    called = {"n": 0}

    async def fake_to_thread(fn, **kw):
        called["n"] += 1
        return {"ok": True, "content": "DID_WRITE"}
    monkeypatch.setattr(ms.asyncio, "to_thread", fake_to_thread)
    res = asyncio.run(ms.dispatch_tool("ww_model_set_default", {"id": "m_x"}))
    assert "写操作未启用" in res[0].text and called["n"] == 0          # 默认锁:impl 未被调
    monkeypatch.setenv("GUANLAN_MCP_WRITE", "1")
    res2 = asyncio.run(ms.dispatch_tool("ww_model_set_default", {"id": "m_x"}))
    assert called["n"] == 1 and res2[0].text == "DID_WRITE"           # 放行:impl 被调


def test_dispatch_unknown_tool():
    import guanlan_v2.glmcp.server as ms
    res = asyncio.run(ms.dispatch_tool("ww_nope", {}))
    assert "未知工具" in res[0].text


def test_build_server_name():
    from guanlan_v2.glmcp.server import build_server
    assert build_server().name == "guanlan"


def test_build_server_prewarms_decls():
    # 守护:build_server 必须急切预热工具表(_decls),把重导入挪到 stdio 读循环之前。
    # 否则首个 list_tools 触发冷导入会卡死 stdio(initialize 能回、tools/list 永不返回)。
    import guanlan_v2.glmcp.server as ms
    from guanlan_v2.glmcp.tooltable import build_mcp_tools
    ms._DECLS = None
    ms.build_server()
    assert ms._DECLS is not None
    assert {row["name"] for row in ms._DECLS} == {
        row["name"] for row in build_mcp_tools()
    }


def test_build_mcp_http_app_is_starlette():
    from guanlan_v2.glmcp.http import build_mcp_http_app
    from starlette.applications import Starlette
    assert isinstance(build_mcp_http_app(), Starlette)


def test_main_module_has_main():
    import importlib
    m = importlib.import_module("guanlan_v2.glmcp.__main__")
    assert callable(getattr(m, "main", None))


def test_server_mounts_gl_mcp_alongside_engine_mcp():
    import guanlan_v2.server as s
    mounts = [getattr(r, "path", None) for r in s.app.routes if r.__class__.__name__ == "Mount"]
    assert "/gl-mcp" in mounts          # 新 guanlan MCP
    assert "/gl-mcp-codex" in mounts    # Codex 专用、服务端空间绑定 MCP
    assert "/mcp" in mounts             # 引擎 MCP 仍在(不破)
    assert "/ui" in mounts              # 既有 UI 不破


def test_codex_scoped_dispatch_injects_workspace_and_denies_cross_space(
        tmp_path, monkeypatch):
    import guanlan_v2.glmcp.server as ms
    from guanlan_v2.console.store import ConsoleStore
    from guanlan_v2.console import workspace_tools as wt
    from guanlan_v2.glmcp.scope import (
        CTX_CODEX_MCP_SCOPE, register_codex_scope, resolve_codex_scope,
        revoke_codex_scope, workspace_root_sha256, workspace_subject_sha256,
    )

    store = ConsoleStore(tmp_path)
    general = store.workspace_store.get("rw_general")
    raw_scope_token = register_codex_scope(
        sid="cs_test", workspace_id="rw_general",
        workspace_code=str(general.get("code") or ""),
        workspace_subject_sha256=workspace_subject_sha256(general),
        workspace_root_sha256=workspace_root_sha256(
            wt._root_for(store, "rw_general"),
        ),
        allowed_tools=frozenset({"ww_workspace_trade_disclosure_calendar"}),
    )
    scope = resolve_codex_scope(raw_scope_token)
    assert scope is not None
    calls = []

    async def fake_to_thread(fn, *args, **kwargs):
        if fn is ms._run_workspace_impl:
            calls.append((args[2], args[4]))
            return {"content": "SCOPED_OK"}
        return fn(*args, **kwargs)

    monkeypatch.setattr(ms.asyncio, "to_thread", fake_to_thread)
    token = CTX_CODEX_MCP_SCOPE.set(scope)
    try:
        denied = asyncio.run(ms.dispatch_tool(
            "ww_workspace_trade_disclosure_calendar",
            {"workspace_id": "rw_123456789abc"},
            console_store=store, scope_required=True,
        ))
        assert denied.isError is True
        assert "越过当前研究空间" in denied.content[0].text

        result = asyncio.run(ms.dispatch_tool(
            "ww_workspace_trade_disclosure_calendar", {"kind": "trading"},
            console_store=store, scope_required=True,
        ))
        assert result[0].text == "SCOPED_OK"
        assert calls == [(
            {"kind": "trading", "workspace_id": "rw_general"}, "cs_test",
        )]

        changed = dict(general)
        changed["subject_since"] = "2099-01-01T00:00:00"
        store.workspace_store._write_json(
            store.workspace_store._path("rw_general"), changed,
        )
        stale = asyncio.run(ms.dispatch_tool(
            "ww_workspace_trade_disclosure_calendar", {"kind": "trading"},
            console_store=store, scope_required=True,
        ))
        assert stale.isError is True
        assert "标的或目录已变更" in stale.content[0].text

        destructive = asyncio.run(ms.dispatch_tool(
            "ww_model_delete", {"id": "not-real"},
            console_store=store, scope_required=True,
        ))
        assert destructive.isError is True
        assert "未授权工具" in destructive.content[0].text
    finally:
        CTX_CODEX_MCP_SCOPE.reset(token)
        revoke_codex_scope(raw_scope_token)


def test_codex_scoped_company_overrides_are_private_and_workspace_bound():
    from types import SimpleNamespace
    import guanlan_v2.glmcp.server as ms

    scope = SimpleNamespace(workspace_code="SH603986")
    calendar = {"kind": "both"}
    ms._apply_scoped_query_overrides(
        "ww_workspace_trade_disclosure_calendar", calendar, scope=scope,
    )
    assert calendar["stock_code"] == "SH603986"

    radar = {}
    ms._apply_scoped_query_overrides("ww_workspace_newsradar", radar, scope=scope)
    assert radar == {"_allow_refresh": False}

    prepare = {}
    ms._apply_scoped_query_overrides(
        "ww_workspace_company_research_prepare", prepare, scope=scope,
    )
    assert prepare == {
        "_global_event_write": False,
        "_newsradar_allow_refresh": False,
    }

    declaration = ms._by_name()["ww_workspace_trade_disclosure_calendar"]
    schema = ms._scoped_schema(declaration, scoped=True)
    assert "stock_code" not in schema["properties"]
    assert schema["additionalProperties"] is False


def test_codex_scoped_dispatch_fails_closed_without_scope():
    import guanlan_v2.glmcp.server as ms
    result = asyncio.run(ms.dispatch_tool(
        "ww_news_search", {}, scope_required=True,
    ))
    assert result.isError is True
    assert "scope 缺失或已过期" in result.content[0].text


def test_codex_scoped_quant_chart_schema_and_dispatch_reject_extra_key(
        tmp_path, monkeypatch):
    import guanlan_v2.glmcp.server as ms
    from guanlan_v2.console.store import ConsoleStore
    from guanlan_v2.glmcp.scope import (
        CTX_CODEX_MCP_SCOPE, register_codex_scope, resolve_codex_scope,
        revoke_codex_scope, workspace_subject_sha256,
    )

    declaration = ms._by_name()["ww_workspace_quant_chart"]
    schema = ms._scoped_schema(declaration, scoped=True)
    assert schema["additionalProperties"] is False
    assert set(schema["properties"]) == {"benchmark", "asof"}
    assert "workspace_id" not in schema.get("required", [])

    store = ConsoleStore(tmp_path)
    workspace = store.workspace_store.get("rw_general")
    raw_scope_token = register_codex_scope(
        sid="cs_quant_extra", workspace_id="rw_general",
        workspace_code=str(workspace.get("code") or ""),
        workspace_subject_sha256=workspace_subject_sha256(workspace),
        allowed_tools=frozenset({"ww_workspace_quant_chart"}),
    )
    scope = resolve_codex_scope(raw_scope_token)
    executions = []

    async def fake_to_thread(*args, **kwargs):
        executions.append((args, kwargs))
        return {"content": "SHOULD_NOT_RUN"}

    monkeypatch.setattr(ms.asyncio, "to_thread", fake_to_thread)

    async def run():
        ctx = CTX_CODEX_MCP_SCOPE.set(scope)
        try:
            return await ms.dispatch_tool(
                "ww_workspace_quant_chart",
                {"benchmark": "csi300", "unexpected": "blocked"},
                console_store=store, scope_required=True,
            )
        finally:
            CTX_CODEX_MCP_SCOPE.reset(ctx)

    try:
        result = asyncio.run(run())
        assert result.isError is True
        assert "schema 未声明字段: unexpected" in result.content[0].text
        assert executions == []
    finally:
        revoke_codex_scope(raw_scope_token)


def test_codex_scoped_news_forces_shared_write_through_off(tmp_path, monkeypatch):
    import guanlan_v2.glmcp.server as ms
    from guanlan_v2.console.store import ConsoleStore
    from guanlan_v2.glmcp.scope import (
        CTX_CODEX_MCP_SCOPE, register_codex_scope, resolve_codex_scope,
        revoke_codex_scope, workspace_subject_sha256,
    )

    store = ConsoleStore(tmp_path)
    workspace = store.workspace_store.get("rw_general")
    raw_scope_token = register_codex_scope(
        sid="cs_news", workspace_id="rw_general",
        workspace_code=str(workspace.get("code") or ""),
        workspace_subject_sha256=workspace_subject_sha256(workspace),
        allowed_tools=frozenset({"ww_news_search"}),
    )
    scope = resolve_codex_scope(raw_scope_token)
    seen = []

    def impl(**kwargs):
        seen.append(kwargs)
        return {"content": "NEWS_OK"}

    monkeypatch.setattr(ms, "_resolve_impl", lambda _d, _name: impl)

    async def run():
        ctx = CTX_CODEX_MCP_SCOPE.set(scope)
        try:
            return await ms.dispatch_tool(
                "ww_news_search", {"scope": "market"},
                console_store=store, scope_required=True,
            )
        finally:
            CTX_CODEX_MCP_SCOPE.reset(ctx)

    try:
        result = asyncio.run(run())
        assert result[0].text == "NEWS_OK"
        assert seen == [{"scope": "market", "_write_through": False}]
    finally:
        revoke_codex_scope(raw_scope_token)


def test_codex_scoped_query_schemas_hide_persistence_and_path_selectors():
    import guanlan_v2.glmcp.server as ms

    alpha = ms._scoped_schema(ms._by_name()["alpha_bench"], scoped=True)
    factor = ms._scoped_schema(ms._by_name()["factor_report"], scoped=True)
    screen = ms._scoped_schema(ms._by_name()["ww_screen_run"], scoped=True)

    assert alpha["additionalProperties"] is False
    assert factor["additionalProperties"] is False
    assert screen["additionalProperties"] is False
    assert "save" not in alpha["properties"]
    assert {"archive", "note"}.isdisjoint(factor["properties"])
    assert {"model", "snapshot", "note"}.isdisjoint(screen["properties"])
    assert "../../private.txt" not in alpha["properties"]["universe"]["enum"]
    assert "../../private.txt" not in factor["properties"]["universe"]["enum"]
    assert alpha["properties"]["universe"]["enum"] == ["sample30"]
    assert alpha["properties"]["universe"]["default"] == "sample30"
    assert "alpha019" in factor["properties"]["expr_or_name"]["enum"]
    assert alpha["properties"]["top"]["maximum"] == 25
    assert screen["properties"]["factors"]["maxItems"] == 12


def test_codex_scoped_alpha_and_factor_use_strict_query_adapters(
        tmp_path, monkeypatch):
    import guanlan_v2.glmcp.server as ms
    from guanlan_v2.console.store import ConsoleStore
    from guanlan_v2.glmcp.scope import (
        CTX_CODEX_MCP_SCOPE, register_codex_scope, resolve_codex_scope,
        revoke_codex_scope, workspace_subject_sha256,
    )

    store = ConsoleStore(tmp_path)
    workspace = store.workspace_store.get("rw_general")
    raw_scope_token = register_codex_scope(
        sid="cs_strict_research", workspace_id="rw_general",
        workspace_code=str(workspace.get("code") or ""),
        workspace_subject_sha256=workspace_subject_sha256(workspace),
        allowed_tools=frozenset({"alpha_bench", "factor_report"}),
    )
    scope = resolve_codex_scope(raw_scope_token)
    seen = []

    def alpha_impl(**kwargs):
        seen.append(("alpha_bench", kwargs))
        return {"content": "ALPHA_QUERY_OK"}

    def factor_impl(**kwargs):
        seen.append(("factor_report", kwargs))
        return {"content": "FACTOR_QUERY_OK"}

    monkeypatch.setattr(
        ms, "_scoped_query_impl",
        lambda name: alpha_impl if name == "alpha_bench" else factor_impl,
    )

    async def call(name, args):
        token = CTX_CODEX_MCP_SCOPE.set(scope)
        try:
            return await ms.dispatch_tool(
                name, args, console_store=store, scope_required=True,
            )
        finally:
            CTX_CODEX_MCP_SCOPE.reset(token)

    try:
        alpha = asyncio.run(call("alpha_bench", {
            "universe": "sample30", "since": "2025-01-01",
            "until": "2025-06-30", "top": 5,
        }))
        factor = asyncio.run(call("factor_report", {
            "expr_or_name": "alpha019", "universe": "sample30",
            "freq": "month", "start": "2025-01-01", "end": "2025-06-30",
        }))
        assert alpha[0].text == "ALPHA_QUERY_OK"
        assert factor[0].text == "FACTOR_QUERY_OK"
        assert seen == [
            ("alpha_bench", {
                "since": "2025-01-01", "top": 5, "universe": "sample30",
                "until": "2025-06-30", "save": False,
            }),
            ("factor_report", {
                "end": "2025-06-30", "expr_or_name": "alpha019",
                "freq": "month", "start": "2025-01-01",
                "universe": "sample30", "archive": False, "note": "",
            }),
        ]

        blocked = asyncio.run(call("factor_report", {
            "expr_or_name": "alpha019", "universe": "../../private.txt",
        }))
        hidden = asyncio.run(call("factor_report", {
            "expr_or_name": "alpha019", "archive": True,
        }))
        too_wide = asyncio.run(call("alpha_bench", {
            "universe": "sample30", "since": "2020-01-01",
            "until": "2025-01-01",
        }))
        unsafe_expr = asyncio.run(call("factor_report", {
            "expr_or_name": "close.to_csv('G:/must-not-write.csv')",
            "universe": "sample30",
        }))
        assert blocked.isError is True and "内置命名池" in blocked.content[0].text
        assert hidden.isError is True and "schema 未声明字段: archive" in hidden.content[0].text
        assert too_wide.isError is True and "查询窗口" in too_wide.content[0].text
        assert unsafe_expr.isError is True and "只允许内置" in unsafe_expr.content[0].text
        assert len(seen) == 2
    finally:
        revoke_codex_scope(raw_scope_token)


def test_codex_scoped_screen_forces_query_only_and_rejects_archive_selectors(
        tmp_path, monkeypatch):
    import guanlan_v2.glmcp.server as ms
    from guanlan_v2.console.store import ConsoleStore
    from guanlan_v2.glmcp.scope import (
        CTX_CODEX_MCP_SCOPE, register_codex_scope, resolve_codex_scope,
        revoke_codex_scope, workspace_subject_sha256,
    )

    store = ConsoleStore(tmp_path)
    workspace = store.workspace_store.get("rw_general")
    raw_scope_token = register_codex_scope(
        sid="cs_screen_preview", workspace_id="rw_general",
        workspace_code=str(workspace.get("code") or ""),
        workspace_subject_sha256=workspace_subject_sha256(workspace),
        allowed_tools=frozenset({"ww_screen_run"}),
    )
    scope = resolve_codex_scope(raw_scope_token)
    seen = []

    def impl(**kwargs):
        seen.append(kwargs)
        return {"content": "SCREEN_PREVIEW_OK"}

    monkeypatch.setattr(ms, "_resolve_impl", lambda _d, _name: impl)

    async def call(args):
        token = CTX_CODEX_MCP_SCOPE.set(scope)
        try:
            return await ms.dispatch_tool(
                "ww_screen_run", args, console_store=store, scope_required=True,
            )
        finally:
            CTX_CODEX_MCP_SCOPE.reset(token)

    try:
        result = asyncio.run(call({
            "pool": "csi300", "topN": 10,
            "factors": [{"id": "reversal", "w": 1.0}],
        }))
        assert result[0].text == "SCREEN_PREVIEW_OK"
        assert seen == [{
            "factors": [{"id": "reversal", "w": 1.0}],
            "pool": "csi300", "topN": 10, "_write_through": False,
        }]
        for unsafe in (
            {"snapshot": True}, {"note": "archive me"},
            {"model": "../../private"}, {"unexpected": 1},
        ):
            blocked = asyncio.run(call(unsafe))
            assert blocked.isError is True
        assert len(seen) == 1
    finally:
        revoke_codex_scope(raw_scope_token)


def test_codex_scoped_dispatch_returns_closed_structured_artifact(
        tmp_path, monkeypatch):
    import guanlan_v2.glmcp.server as ms
    from guanlan_v2.console import workspace_tools as wt
    from guanlan_v2.console.store import ConsoleStore
    from guanlan_v2.glmcp.scope import (
        CTX_CODEX_MCP_SCOPE, register_codex_scope, resolve_codex_scope,
        revoke_codex_scope, workspace_root_sha256, workspace_subject_sha256,
    )

    store = ConsoleStore(tmp_path)
    workspace = store.workspace_store.get("rw_general")
    raw_scope_token = register_codex_scope(
        sid="cs_artifact", workspace_id="rw_general",
        workspace_code=str(workspace.get("code") or ""),
        workspace_subject_sha256=workspace_subject_sha256(workspace),
        workspace_root_sha256=workspace_root_sha256(
            wt._root_for(store, "rw_general"),
        ),
        allowed_tools=frozenset({"ww_workspace_trade_disclosure_calendar"}),
    )
    scope = resolve_codex_scope(raw_scope_token)

    async def fake_to_thread(fn, *args, **kwargs):
        if fn is ms._run_workspace_impl:
            return {
                "ok": True,
                "content": "SNAPSHOT_OK",
                "artifact": {
                    "kind": "workspace_data",
                    "page": None,
                    "channel": "workspace",
                    "payload": {
                        "workspace_id": "rw_general",
                        "asset_id": "wa_0123456789abcdef",
                        "dataset_id": "research.calendar.live",
                        "record_count": 2,
                        "pulled_at": "2026-08-30T01:02:03Z",
                        "path": "private/payload.json",
                        "url": "file:///private/payload.json",
                    },
                    "ref": "wa_0123456789abcdef",
                },
            }
        return fn(*args, **kwargs)

    monkeypatch.setattr(ms.asyncio, "to_thread", fake_to_thread)

    async def run():
        ctx = CTX_CODEX_MCP_SCOPE.set(scope)
        try:
            return await ms.dispatch_tool(
                "ww_workspace_trade_disclosure_calendar", {},
                console_store=store, scope_required=True,
            )
        finally:
            CTX_CODEX_MCP_SCOPE.reset(ctx)

    try:
        result = asyncio.run(run())
        assert result.content[0].text == "SNAPSHOT_OK"
        structured = result.structuredContent
        assert structured["schema"] == "guanlan.weiwo-artifact.v1"
        assert structured["tool"] == "ww_workspace_trade_disclosure_calendar"
        assert structured["workspace_id"] == "rw_general"
        assert structured["artifact"]["ref"] == "wa_0123456789abcdef"
        assert "path" not in structured["artifact"]["payload"]
        assert "url" not in structured["artifact"]["payload"]
    finally:
        revoke_codex_scope(raw_scope_token)


def test_codex_scoped_dispatch_blocks_invalid_artifact_but_keeps_tool_text(
        tmp_path, monkeypatch):
    import guanlan_v2.glmcp.server as ms
    from guanlan_v2.console.store import ConsoleStore
    from guanlan_v2.glmcp.scope import (
        CTX_CODEX_MCP_SCOPE, register_codex_scope, resolve_codex_scope,
        revoke_codex_scope, workspace_subject_sha256,
    )

    store = ConsoleStore(tmp_path)
    workspace = store.workspace_store.get("rw_general")
    raw_scope_token = register_codex_scope(
        sid="cs_bad_artifact", workspace_id="rw_general",
        workspace_code=str(workspace.get("code") or ""),
        workspace_subject_sha256=workspace_subject_sha256(workspace),
        allowed_tools=frozenset({"ww_news_search"}),
    )
    scope = resolve_codex_scope(raw_scope_token)

    def impl(**_kwargs):
        return {
            "ok": True, "content": "QUERY_STILL_OK",
            "artifact": {
                "kind": "media_image", "payload": {
                    "media_id": "not-server-issued",
                    "workspace_id": "rw_general",
                    "path": "C:/secret.png",
                },
            },
        }

    monkeypatch.setattr(ms, "_resolve_impl", lambda _d, _name: impl)

    async def run():
        ctx = CTX_CODEX_MCP_SCOPE.set(scope)
        try:
            return await ms.dispatch_tool(
                "ww_news_search", {}, console_store=store, scope_required=True,
            )
        finally:
            CTX_CODEX_MCP_SCOPE.reset(ctx)

    try:
        result = asyncio.run(run())
        assert isinstance(result, list)
        assert "QUERY_STILL_OK" in result[0].text
        assert "产物回传已拦截" in result[0].text
        assert "secret.png" not in result[0].text
    finally:
        revoke_codex_scope(raw_scope_token)


def test_codex_scoped_confirm_executes_frozen_args_once(tmp_path, monkeypatch):
    import guanlan_v2.glmcp.server as ms
    from guanlan_v2.console import workspace_tools as wt
    from guanlan_v2.console.store import ConsoleStore
    from guanlan_v2.glmcp.scope import (
        CTX_CODEX_MCP_SCOPE, register_codex_scope, resolve_codex_scope,
        revoke_codex_scope, workspace_root_sha256, workspace_subject_sha256,
    )

    store = ConsoleStore(tmp_path)
    workspace = store.workspace_store.update(
        "rw_general", {"agent_mode": "collaborative"},
    )
    confirmed = []
    executed = []

    async def confirm(name, args):
        confirmed.append((name, dict(args)))
        args["name"] = "MUTATED_BY_CALLBACK"
        return True

    raw_scope_token = register_codex_scope(
        sid="cs_confirm", workspace_id="rw_general",
        workspace_code=str(workspace.get("code") or ""),
        workspace_subject_sha256=workspace_subject_sha256(workspace),
        workspace_root_sha256=workspace_root_sha256(
            wt._root_for(store, "rw_general"),
        ),
        allowed_tools=frozenset({"ww_workspace_factor_save"}),
        confirmation_required_tools=frozenset({"ww_workspace_factor_save"}),
        confirm_callback=confirm,
    )
    scope = resolve_codex_scope(raw_scope_token)
    assert scope is not None

    async def fake_to_thread(fn, *args, **kwargs):
        if fn is ms._run_workspace_impl:
            executed.append(dict(args[2]))
            return {"content": "CONFIRMED_OK"}
        return fn(*args, **kwargs)

    monkeypatch.setattr(ms.asyncio, "to_thread", fake_to_thread)

    async def run():
        ctx = CTX_CODEX_MCP_SCOPE.set(scope)
        try:
            return await ms.dispatch_tool(
                "ww_workspace_factor_save",
                {"name": "private-factor", "expr": "rank(close)"},
                console_store=store, scope_required=True,
            )
        finally:
            CTX_CODEX_MCP_SCOPE.reset(ctx)

    try:
        result = asyncio.run(run())
        assert result[0].text == "CONFIRMED_OK"
        assert confirmed == [(
            "ww_workspace_factor_save",
            {"name": "private-factor", "expr": "rank(close)",
             "workspace_id": "rw_general"},
        )]
        assert executed == [{
            "name": "private-factor", "expr": "rank(close)",
            "workspace_id": "rw_general",
        }]
    finally:
        revoke_codex_scope(raw_scope_token)


def test_codex_scoped_confirm_rejects_without_execution(tmp_path, monkeypatch):
    import guanlan_v2.glmcp.server as ms
    from guanlan_v2.console import workspace_tools as wt
    from guanlan_v2.console.store import ConsoleStore
    from guanlan_v2.glmcp.scope import (
        CTX_CODEX_MCP_SCOPE, register_codex_scope, resolve_codex_scope,
        revoke_codex_scope, workspace_root_sha256, workspace_subject_sha256,
    )

    store = ConsoleStore(tmp_path)
    workspace = store.workspace_store.update(
        "rw_general", {"agent_mode": "collaborative"},
    )

    async def reject(_name, _args):
        return False

    raw_scope_token = register_codex_scope(
        sid="cs_reject", workspace_id="rw_general",
        workspace_code=str(workspace.get("code") or ""),
        workspace_subject_sha256=workspace_subject_sha256(workspace),
        workspace_root_sha256=workspace_root_sha256(
            wt._root_for(store, "rw_general"),
        ),
        allowed_tools=frozenset({"ww_workspace_factor_save"}),
        confirmation_required_tools=frozenset({"ww_workspace_factor_save"}),
        confirm_callback=reject,
    )
    scope = resolve_codex_scope(raw_scope_token)
    calls = []

    async def fake_to_thread(fn, *args, **kwargs):
        if fn is ms._run_workspace_impl:
            calls.append(args)
        return {"content": "SHOULD_NOT_RUN"}

    monkeypatch.setattr(ms.asyncio, "to_thread", fake_to_thread)

    async def run():
        ctx = CTX_CODEX_MCP_SCOPE.set(scope)
        try:
            return await ms.dispatch_tool(
                "ww_workspace_factor_save", {"name": "x", "expr": "close"},
                console_store=store, scope_required=True,
            )
        finally:
            CTX_CODEX_MCP_SCOPE.reset(ctx)

    try:
        result = asyncio.run(run())
        assert result.isError is True
        assert "未批准" in result.content[0].text
        assert calls == []
    finally:
        revoke_codex_scope(raw_scope_token)


def test_codex_scoped_confirm_revoke_while_waiting_never_executes(
        tmp_path, monkeypatch):
    import guanlan_v2.glmcp.server as ms
    from guanlan_v2.console import workspace_tools as wt
    from guanlan_v2.console.store import ConsoleStore
    from guanlan_v2.glmcp.scope import (
        CTX_CODEX_MCP_SCOPE, register_codex_scope, resolve_codex_scope,
        revoke_codex_scope, workspace_root_sha256, workspace_subject_sha256,
    )

    store = ConsoleStore(tmp_path)
    workspace = store.workspace_store.update(
        "rw_general", {"agent_mode": "collaborative"},
    )
    callback_started = asyncio.Event()
    callback_release = asyncio.Event()
    calls = []

    async def delayed_approve(_name, _args):
        callback_started.set()
        await callback_release.wait()
        return True

    raw_scope_token = register_codex_scope(
        sid="cs_revoke", workspace_id="rw_general",
        workspace_code=str(workspace.get("code") or ""),
        workspace_subject_sha256=workspace_subject_sha256(workspace),
        workspace_root_sha256=workspace_root_sha256(
            wt._root_for(store, "rw_general"),
        ),
        allowed_tools=frozenset({"ww_workspace_factor_save"}),
        confirmation_required_tools=frozenset({"ww_workspace_factor_save"}),
        confirm_callback=delayed_approve,
    )
    scope = resolve_codex_scope(raw_scope_token)

    async def fake_to_thread(fn, *args, **kwargs):
        if fn is ms._run_workspace_impl:
            calls.append(args)
        return {"content": "SHOULD_NOT_RUN"}

    monkeypatch.setattr(ms.asyncio, "to_thread", fake_to_thread)

    async def run():
        ctx = CTX_CODEX_MCP_SCOPE.set(scope)
        try:
            task = asyncio.create_task(ms.dispatch_tool(
                "ww_workspace_factor_save", {"name": "x", "expr": "close"},
                console_store=store, scope_required=True,
            ))
            await asyncio.wait_for(callback_started.wait(), timeout=1)
            revoke_codex_scope(raw_scope_token)
            callback_release.set()
            return await asyncio.wait_for(task, timeout=1)
        finally:
            CTX_CODEX_MCP_SCOPE.reset(ctx)

    result = asyncio.run(run())
    assert result.isError is True
    assert calls == []


def test_codex_scoped_confirm_revalidates_root_after_approval(
        tmp_path, monkeypatch):
    import guanlan_v2.glmcp.server as ms
    from guanlan_v2.console import workspace_tools as wt
    from guanlan_v2.console.store import ConsoleStore
    from guanlan_v2.glmcp.scope import (
        CTX_CODEX_MCP_SCOPE, register_codex_scope, resolve_codex_scope,
        revoke_codex_scope, workspace_root_sha256, workspace_subject_sha256,
    )

    store = ConsoleStore(tmp_path / "store")
    workspace = store.workspace_store.update(
        "rw_general", {"agent_mode": "collaborative"},
    )
    replacement = tmp_path / "replacement-root"
    replacement.mkdir()
    calls = []

    async def approve_after_rebind(_name, _args):
        with store.workspace_store._lock:
            changed = store.workspace_store.get("rw_general")
            changed["root_path"] = str(replacement)
            store.workspace_store._write_json(
                store.workspace_store._path("rw_general"), changed,
            )
        return True

    raw_scope_token = register_codex_scope(
        sid="cs_rebind", workspace_id="rw_general",
        workspace_code=str(workspace.get("code") or ""),
        workspace_subject_sha256=workspace_subject_sha256(workspace),
        workspace_root_sha256=workspace_root_sha256(
            wt._root_for(store, "rw_general"),
        ),
        allowed_tools=frozenset({"ww_workspace_factor_save"}),
        confirmation_required_tools=frozenset({"ww_workspace_factor_save"}),
        confirm_callback=approve_after_rebind,
    )
    scope = resolve_codex_scope(raw_scope_token)

    async def fake_to_thread(fn, *args, **kwargs):
        if fn is ms._run_workspace_impl:
            calls.append(args)
        return {"content": "SHOULD_NOT_RUN"}

    monkeypatch.setattr(ms.asyncio, "to_thread", fake_to_thread)

    async def run():
        ctx = CTX_CODEX_MCP_SCOPE.set(scope)
        try:
            return await ms.dispatch_tool(
                "ww_workspace_factor_save", {"name": "x", "expr": "close"},
                console_store=store, scope_required=True,
            )
        finally:
            CTX_CODEX_MCP_SCOPE.reset(ctx)

    try:
        result = asyncio.run(run())
        assert result.isError is True
        assert "标的或目录已变更" in result.content[0].text
        assert calls == []
    finally:
        revoke_codex_scope(raw_scope_token)


def test_codex_scoped_confirmations_are_serialized(tmp_path, monkeypatch):
    import guanlan_v2.glmcp.server as ms
    from guanlan_v2.console import workspace_tools as wt
    from guanlan_v2.console.store import ConsoleStore
    from guanlan_v2.glmcp.scope import (
        CTX_CODEX_MCP_SCOPE, register_codex_scope, resolve_codex_scope,
        revoke_codex_scope, workspace_root_sha256, workspace_subject_sha256,
    )

    store = ConsoleStore(tmp_path)
    workspace = store.workspace_store.update(
        "rw_general", {"agent_mode": "collaborative"},
    )
    active = 0
    max_active = 0
    executed = []

    async def approve(_name, _args):
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        await asyncio.sleep(0.03)
        active -= 1
        return True

    raw_scope_token = register_codex_scope(
        sid="cs_parallel", workspace_id="rw_general",
        workspace_code=str(workspace.get("code") or ""),
        workspace_subject_sha256=workspace_subject_sha256(workspace),
        workspace_root_sha256=workspace_root_sha256(
            wt._root_for(store, "rw_general"),
        ),
        allowed_tools=frozenset({"ww_workspace_factor_save"}),
        confirmation_required_tools=frozenset({"ww_workspace_factor_save"}),
        confirm_callback=approve,
    )
    scope = resolve_codex_scope(raw_scope_token)

    async def fake_to_thread(fn, *args, **kwargs):
        if fn is ms._run_workspace_impl:
            executed.append(args[2]["name"])
            return {"content": "OK"}
        return fn(*args, **kwargs)

    monkeypatch.setattr(ms.asyncio, "to_thread", fake_to_thread)

    async def run():
        ctx = CTX_CODEX_MCP_SCOPE.set(scope)
        try:
            return await asyncio.gather(*(
                ms.dispatch_tool(
                    "ww_workspace_factor_save",
                    {"name": name, "expr": "close"},
                    console_store=store, scope_required=True,
                )
                for name in ("one", "two")
            ))
        finally:
            CTX_CODEX_MCP_SCOPE.reset(ctx)

    try:
        results = asyncio.run(run())
        assert all(result[0].text == "OK" for result in results)
        assert max_active == 1
        assert sorted(executed) == ["one", "two"]
    finally:
        revoke_codex_scope(raw_scope_token)


def test_mcp_excludes_frontend_envelope_tools():
    """ww_seats_bind 靠前端 window.GL 落地,MCP 语境=空转假成功 → 排除(同 ww_show_page)。"""
    from guanlan_v2.glmcp.tooltable import build_mcp_tools
    names = {t["name"] for t in build_mcp_tools()}
    assert "ww_seats_bind" not in names
    assert "ww_report_run" in names          # 研报经 detached 子进程真跑,保留(gated)


def test_spawn_background_report_branch(monkeypatch):
    import subprocess
    from pathlib import Path
    import guanlan_v2.glmcp.server as ms
    calls = {}
    class FakePopen:
        def __init__(self, cmd, **kw):
            calls["cmd"] = cmd
            calls["kw"] = kw
    monkeypatch.setattr(subprocess, "Popen", FakePopen)
    # 证据包构建是独立关注点(见 test_report_seams.py),这里桩掉防止真触网/真落盘拖慢本测。
    monkeypatch.setattr(ms, "_build_pack_safe", lambda code: None)
    receipt = ms._spawn_background_detached({"kind": "report", "code": "SZ000630"})
    assert "已真启动后台研报" in receipt
    assert calls["cmd"][1] == "report" and calls["cmd"][2] == "SZ000630"
    assert calls["kw"]["creationflags"] == (0x00000008 | 0x00000200)   # detached
    # 清理:FakePopen 未写内容 → 本测新建的空日志删掉
    for p in (Path(ms.__file__).resolve().parents[2] / "var").glob("mcp_bg_*.log"):
        if p.stat().st_size == 0:
            p.unlink()


def test_spawn_background_etf_branch(monkeypatch):
    import subprocess
    from pathlib import Path
    import guanlan_v2.glmcp.server as ms
    calls = {}
    class FakePopen:
        def __init__(self, cmd, **kw):
            calls["cmd"] = cmd
    monkeypatch.setattr(subprocess, "Popen", FakePopen)
    receipt = ms._spawn_background_detached({"kind": "etf_report", "code": "510300", "asof": None})
    assert "已真启动" in receipt and calls["cmd"][1] == "-c" and "run_etf_report" in calls["cmd"][2]
    for p in (Path(ms.__file__).resolve().parents[2] / "var").glob("mcp_bg_*.log"):
        if p.stat().st_size == 0:
            p.unlink()


def test_spawn_background_unknown_kind_refuses(monkeypatch):
    """未知 kind → 诚实拒绝文案,绝不 spawn。"""
    import subprocess
    import guanlan_v2.glmcp.server as ms
    def _boom(*a, **k):
        raise AssertionError("不应 spawn")
    monkeypatch.setattr(subprocess, "Popen", _boom)
    msg = ms._spawn_background_detached({"kind": "weird"})
    assert "暂不支持" in msg


def test_dispatch_background_spawn_failure_is_visible(monkeypatch):
    """spawn 抛错 → dispatch 回错误显形,绝不假成功(红线)。
    fake_to_thread 现须区分两次 asyncio.to_thread 调用:impl 调用(纯 kwargs)回桩 dict;
    spawn 调用(dispatch_tool 传 fn+位置参数 background,Task 4 评审后 spawn 也经 to_thread
    离开事件循环线程)→ 真调 fn(*args, **kw),让 monkeypatch 的 _spawn_background_detached
    真正生效(含抛错传播,同真实 asyncio.to_thread 行为)。"""
    import guanlan_v2.glmcp.server as ms
    async def fake_to_thread(fn, *args, **kw):
        if args:
            return fn(*args, **kw)
        return {"ok": True, "content": "已受理", "background": {"kind": "report", "code": "X"}}
    monkeypatch.setattr(ms.asyncio, "to_thread", fake_to_thread)
    monkeypatch.setenv("GUANLAN_MCP_WRITE", "1")
    def boom(bg):
        raise RuntimeError("spawn炸了")
    monkeypatch.setattr(ms, "_spawn_background_detached", boom)
    res = asyncio.run(ms.dispatch_tool("ww_report_run", {"code": "X"}))
    assert "后台任务启动失败" in res[0].text and "spawn炸了" in res[0].text


def test_dispatch_background_success_appends_receipt(monkeypatch):
    """同上:fake_to_thread 区分 impl 调用(纯 kwargs,回桩 dict)与 spawn 调用(位置参数,
    真调 fn 走 monkeypatch 的 _spawn_background_detached)。"""
    import guanlan_v2.glmcp.server as ms
    async def fake_to_thread(fn, *args, **kw):
        if args:
            return fn(*args, **kw)
        return {"ok": True, "content": "已受理研报", "background": {"kind": "report", "code": "X"}}
    monkeypatch.setattr(ms.asyncio, "to_thread", fake_to_thread)
    monkeypatch.setenv("GUANLAN_MCP_WRITE", "1")
    monkeypatch.setattr(ms, "_spawn_background_detached", lambda bg: "已真启动后台研报(job t)")
    res = asyncio.run(ms.dispatch_tool("ww_report_run", {"code": "X"}))
    assert "已受理研报" in res[0].text and "已真启动后台研报" in res[0].text
