import asyncio
import json

import pytest

from guanlan_v2.console.codex_backend import (
    CodexBackendAgent,
    CodexBackendError,
    _CODEX_ALLOWED_TOOLS,
    _CODEX_AUTO_QUERY_TOOLS,
    _CODEX_CLOSED_RISK_TOOLS,
    _CODEX_CONFIRMATION_REQUIRED_TOOLS,
    _CODEX_DISALLOWED_PRESENTATION_TOOLS,
    _CODEX_FIXED_PRESENTATION_TOOLS,
    _CODEX_WORKSPACE_SNAPSHOT_TOOLS,
    _CODEX_WORKFLOW_QUERY_TOOLS,
    _app_server_command,
    _child_environment,
    _console_tools_with_company_research_facade,
    _codex_tool_allowlist,
    _REQUIRED_DISABLED_EXPERIMENTAL_FEATURES,
    _subject_digest,
    codex_backend_diagnostics,
    find_codex_executable,
)
from guanlan_v2.console.store import ConsoleStore


def _agent(tmp_path):
    store = ConsoleStore(tmp_path)
    sid = store.create_session()["id"]
    return CodexBackendAgent(sid, store, "system")


def test_find_codex_executable_honours_explicit_real_file(tmp_path, monkeypatch):
    exe = tmp_path / "codex.exe"
    exe.write_bytes(b"probe")
    monkeypatch.setenv("GUANLAN_CODEX_EXE", str(exe))
    assert find_codex_executable() == exe.resolve()


def test_codex_notification_mapping_streams_delta_without_completed_duplicate(tmp_path):
    agent = _agent(tmp_path)
    seen = set()
    deltas = set()
    events, terminal = agent._map_notification({
        "method": "item/agentMessage/delta",
        "params": {"itemId": "msg-1", "delta": "通路正常"},
    }, seen, deltas)
    assert terminal is None
    assert [(event.kind, event.payload) for event in events] == [
        ("text", "通路正常")
    ]

    events, terminal = agent._map_notification({
        "method": "item/completed",
        "params": {"item": {
            "id": "msg-1", "type": "agentMessage", "text": "通路正常",
        }},
    }, seen, deltas)
    assert events == [] and terminal is None


def test_codex_notification_mapping_exposes_guanlan_mcp_call_and_result(tmp_path):
    agent = _agent(tmp_path)
    seen = set()
    deltas = set()
    started = {
        "id": "tool-1",
        "type": "mcpToolCall",
        "server": "guanlan",
        "tool": "ww_workspace_trade_disclosure_calendar",
        "arguments": {"workspace_id": "rw_general", "kind": "trade"},
        "status": "inProgress",
    }
    events, _ = agent._map_notification({
        "method": "item/started", "params": {"item": started},
    }, seen, deltas)
    assert events[0].kind == "tool_call"
    assert events[0].payload["name"] == (
        "guanlan.ww_workspace_trade_disclosure_calendar"
    )

    completed = {
        **started,
        "status": "completed",
        "result": {"content": [{"type": "text", "text": "asset_id=A-1"}]},
    }
    events, _ = agent._map_notification({
        "method": "item/completed", "params": {"item": completed},
    }, seen, deltas)
    assert [event.kind for event in events] == ["tool_result"]
    assert events[0].payload["is_error"] is False
    assert "asset_id=A-1" in events[0].payload["content"]


def test_codex_notification_mapping_restores_verified_structured_artifact(tmp_path):
    from guanlan_v2.console.artifact_bridge import build_artifact_envelope

    agent = _agent(tmp_path)
    state = agent._new_state(
        "019a1b2c-1234-7abc-8def-1234567890ab",
        "rw_general", "a" * 64, "b" * 64,
    )
    agent.store.save_codex_state(agent.sid, state)
    artifact = {
        "kind": "workspace_data", "page": None, "channel": "workspace",
        "payload": {
            "workspace_id": "rw_general",
            "asset_id": "wa_0123456789abcdef",
            "dataset_id": "research.test.live",
            "record_count": 3,
            "pulled_at": "2026-08-30T01:02:03Z",
            "path": "must-not-cross.json",
        },
        "ref": "wa_0123456789abcdef",
    }
    envelope = build_artifact_envelope(
        {"artifact": artifact},
        tool_name="ww_workspace_trade_disclosure_calendar",
        workspace_id="rw_general",
    )
    item = {
        "id": "tool-artifact", "type": "mcpToolCall",
        "server": "guanlan",
        "tool": "ww_workspace_trade_disclosure_calendar",
        "arguments": {}, "status": "completed",
        "result": {
            "content": [{"type": "text", "text": "已保存快照"}],
            "structuredContent": envelope,
        },
    }
    events, _ = agent._map_notification(
        {"method": "item/completed", "params": {"item": item}}, set(), set(),
    )
    restored = events[-1].payload["side_effect"]["artifact"]
    assert restored["kind"] == "workspace_data"
    assert restored["ref"] == "wa_0123456789abcdef"
    assert "path" not in restored["payload"]
    assert "structuredContent" not in events[-1].payload["content"]


def test_codex_notification_mapping_rejects_cross_workspace_artifact(tmp_path):
    agent = _agent(tmp_path)
    state = agent._new_state(
        "019a1b2c-1234-7abc-8def-1234567890ab",
        "rw_general", "a" * 64, "b" * 64,
    )
    agent.store.save_codex_state(agent.sid, state)
    item = {
        "id": "tool-artifact", "type": "mcpToolCall",
        "server": "guanlan", "tool": "ww_workspace_asset_get",
        "arguments": {}, "status": "completed",
        "result": {
            "content": [{"type": "text", "text": "不可采信"}],
            "structuredContent": {
                "schema": "guanlan.weiwo-artifact.v1",
                "tool": "ww_workspace_asset_get",
                "workspace_id": "rw_123456789abc",
                "artifact": {
                    "kind": "workspace_data", "payload": {
                        "workspace_id": "rw_123456789abc",
                        "asset_id": "wa_0123456789abcdef",
                        "dataset_id": "research.test.live",
                        "record_count": 1,
                    },
                },
            },
        },
    }
    events, _ = agent._map_notification(
        {"method": "item/completed", "params": {"item": item}}, set(), set(),
    )
    assert events[-1].payload["side_effect"] == {}


def test_codex_notification_mapping_carries_quant_image_as_opaque_id_only(tmp_path):
    from guanlan_v2.console.artifact_bridge import build_artifact_envelope

    agent = _agent(tmp_path)
    agent.store.save_codex_state(agent.sid, agent._new_state(
        "019a1b2c-1234-7abc-8def-1234567890ab",
        "rw_general", "a" * 64, "b" * 64,
    ))
    envelope = build_artifact_envelope({"artifact": {
        "kind": "media_image", "page": None, "channel": "conversation",
        "payload": {
            "media_id": "mi_" + "a" * 32,
            "name": "quant.png", "mime": "image/png",
            "width": 1280, "height": 960, "alt": "量化图",
            "workspace_id": "rw_general",
            "path": "output/secret.png", "url": "file:///secret.png",
        },
    }}, tool_name="ww_workspace_quant_chart", workspace_id="rw_general")
    item = {
        "id": "quant-tool", "type": "mcpToolCall", "server": "guanlan",
        "tool": "ww_workspace_quant_chart", "arguments": {},
        "status": "completed", "result": {
            "content": [{"type": "text", "text": "图已生成"}],
            "structuredContent": envelope,
        },
    }
    events, _ = agent._map_notification(
        {"method": "item/completed", "params": {"item": item}}, set(), set(),
    )
    artifact = events[-1].payload["side_effect"]["artifact"]
    assert artifact["kind"] == "media_image"
    assert artifact["payload"]["media_id"] == "mi_" + "a" * 32
    assert "path" not in artifact["payload"] and "url" not in artifact["payload"]


def test_structured_analysis_bundle_round_trips_without_paths_or_provenance():
    from guanlan_v2.console.artifact_bridge import (
        MAX_ARTIFACT_ENVELOPE_BYTES,
        build_artifact_envelope,
        read_artifact_envelope,
    )

    artifact = {
        "kind": "analysis_bundle", "page": None, "channel": "conversation",
        "payload": {
            "run_id": "wr_0123456789abcdef",
            "asset_id": "wa_0123456789abcdef",
            "title": "公司研究准备包",
            "images": [{
                "media_id": "mi_" + "1" * 32,
                "name": "chart.png", "mime": "image/png",
                "width": 800, "height": 600, "alt": "量化图",
                "workspace_id": "rw_general",
                "path": "output/chart.png",
            }],
            "documents": [{
                "document_id": "do_" + "2" * 32,
                "name": "report.pdf", "mime": "application/pdf",
                "size": 1024, "title": "研究报告",
                "workspace_id": "rw_general",
                "url": "file:///private/report.pdf",
            }],
            "provenance": {"path": "C:/private/manifest.json"},
        },
    }
    envelope = build_artifact_envelope(
        {"artifact": artifact}, tool_name="ww_workspace_asset_get",
        workspace_id="rw_general",
    )
    assert envelope is not None
    encoded = json.dumps(envelope, ensure_ascii=False).encode("utf-8")
    assert len(encoded) <= MAX_ARTIFACT_ENVELOPE_BYTES
    clean = read_artifact_envelope(
        envelope, tool_name="ww_workspace_asset_get",
        workspace_id="rw_general",
    )
    assert clean["kind"] == "analysis_bundle"
    assert clean["payload"]["asset_id"] == "wa_0123456789abcdef"
    assert set(clean["payload"]) == {
        "run_id", "asset_id", "title", "images", "documents",
    }
    assert "path" not in clean["payload"]["images"][0]
    assert "url" not in clean["payload"]["documents"][0]


def test_structured_artifact_unknown_shape_and_locator_text_fail_closed():
    from guanlan_v2.console.artifact_bridge import build_artifact_envelope

    assert build_artifact_envelope(
        {"artifact": {"kind": "future_unknown", "payload": {"path": "C:/x"}}},
        tool_name="ww_workspace_asset_get", workspace_id="rw_general",
    ) is None
    assert build_artifact_envelope(
        {"artifact": {"kind": "media_image", "payload": {
            "media_id": "mi_" + "1" * 32,
            "name": "C:/private/chart.png", "mime": "image/png",
            "width": 10, "height": 10, "alt": "chart",
            "workspace_id": "rw_general",
        }}},
        tool_name="ww_workspace_quant_chart", workspace_id="rw_general",
    ) is None
    assert build_artifact_envelope(
        {"artifact": {"kind": "ic_report", "payload": {
            "expr": "file:///private/factor.py", "name": "bad",
        }}},
        tool_name="ww_factor_analyze", workspace_id="rw_general",
    ) is None
    assert build_artifact_envelope(
        {"artifact": {"kind": "analysis_bundle", "payload": {
            "run_id": "wr_0123456789abcdef", "title": "https://example.test/x",
            "images": [], "documents": [],
        }}},
        tool_name="ww_workspace_asset_get", workspace_id="rw_general",
    ) is None
    assert build_artifact_envelope(
        {"artifact": {"kind": "media_image", "payload": {
            "media_id": "mi_" + "1" * 32,
            "name": "chart.png", "mime": "image/png",
            "width": 10, "height": 10, "alt": "../private/secret.png",
            "workspace_id": "rw_general",
        }}},
        tool_name="ww_workspace_quant_chart", workspace_id="rw_general",
    ) is None
    assert build_artifact_envelope(
        {"artifact": {"kind": "analysis_bundle", "payload": {
            "run_id": "wr_0123456789abcdef", "title": "bundle",
            "images": [{
                "media_id": "mi_" + "1" * 32,
                "name": "C:\\private\\chart.png", "mime": "image/png",
                "width": 10, "height": 10, "workspace_id": "rw_general",
            }],
            "documents": [],
        }}},
        tool_name="ww_workspace_asset_get", workspace_id="rw_general",
    ) is None


def test_structured_workspace_change_returns_receipt_not_file_path():
    from guanlan_v2.console.artifact_bridge import build_artifact_envelope

    envelope = build_artifact_envelope(
        {"artifact": {"kind": "workspace_change", "payload": {
            "workspace_id": "rw_general",
            "change_id": "wc_0123456789abcdef",
            "path": "research/private-note.md",
            "sha256": "a" * 64,
        }}},
        tool_name="ww_workspace_apply_patch", workspace_id="rw_general",
    )
    assert envelope is not None
    assert envelope["artifact"]["payload"] == {
        "workspace_id": "rw_general",
        "change_id": "wc_0123456789abcdef",
        "sha256": "a" * 64,
    }


def test_structured_envelope_rejects_oversize_or_extra_outer_fields():
    from guanlan_v2.console.artifact_bridge import (
        ARTIFACT_ENVELOPE_SCHEMA,
        MAX_ARTIFACT_ENVELOPE_BYTES,
        read_artifact_envelope,
    )

    base = {
        "schema": ARTIFACT_ENVELOPE_SCHEMA,
        "tool": "ww_workspace_asset_get",
        "workspace_id": "rw_general",
        "artifact": {
            "kind": "workspace_data", "payload": {
                "workspace_id": "rw_general",
                "asset_id": "wa_0123456789abcdef",
                "dataset_id": "research.test.live",
                "record_count": 1,
            },
        },
    }
    assert read_artifact_envelope(
        {**base, "extra": "not allowed"},
        tool_name="ww_workspace_asset_get", workspace_id="rw_general",
    ) is None
    assert read_artifact_envelope(
        {**base, "artifact": {**base["artifact"],
                               "padding": "x" * MAX_ARTIFACT_ENVELOPE_BYTES}},
        tool_name="ww_workspace_asset_get", workspace_id="rw_general",
    ) is None


def test_codex_terminal_failure_is_honest_and_still_closes_console_turn(tmp_path):
    agent = _agent(tmp_path)
    events, status = agent._map_notification({
        "method": "turn/completed",
        "params": {"turn": {
            "id": "turn-1", "status": "failed",
            "error": {"message": "upstream unavailable"},
        }},
    }, set(), set())
    assert status == "failed"
    assert [event.kind for event in events] == ["error", "done"]
    assert events[0].payload == "upstream unavailable"


def test_codex_maps_mcp_protocol_error_to_failed_console_tool(tmp_path):
    agent = _agent(tmp_path)
    item = {
        "id": "tool-error", "type": "mcpToolCall", "server": "guanlan",
        "tool": "ww_workspace_trade_disclosure_calendar",
        "arguments": {}, "status": "completed",
        "result": {
            "content": [{"type": "text", "text": '{"error":"scope expired"}'}],
            "isError": True,
        },
    }
    events, _ = agent._map_notification(
        {"method": "item/completed", "params": {"item": item}}, set(), set(),
    )
    assert [event.kind for event in events] == ["tool_call", "tool_result"]
    assert events[-1].payload["is_error"] is True


def test_codex_cancel_receipt_and_checkpoint_update_private_state(tmp_path):
    agent = _agent(tmp_path)
    state = agent._new_state("019a1b2c-1234-7abc-8def-1234567890ab",
                             "rw_general", "b" * 64, "c" * 64)
    agent.store.save_codex_state(agent.sid, state)

    assert agent.append_cancel_receipt("已查询快照") is True
    assert agent.checkpoint(12) is True

    restored = agent.store.load_codex_state(agent.sid)
    assert restored["pending_receipts"] == ["已查询快照"]
    assert restored["checkpoint_event_id"] == 12


def test_codex_initialize_requires_chatgpt_subscription_login(tmp_path):
    agent = _agent(tmp_path)

    class Client:
        async def request(self, method, params, timeout=60):
            if method == "initialize":
                assert params["capabilities"] == {"experimentalApi": True}
                return {"serverInfo": {"name": "Codex"}}
            assert method == "account/read" and params == {"refreshToken": False}
            return {"account": {"type": "apiKey"}}

        async def send(self, payload):
            assert payload == {"method": "initialized", "params": {}}

    with pytest.raises(CodexBackendError, match="ChatGPT"):
        asyncio.run(agent._initialize(Client(), frozenset({"ww_news_search"})))


def test_codex_command_disables_shell_plugins_other_mcp_and_uses_allowlist(
        tmp_path, monkeypatch):
    home = tmp_path / "codex-home"
    home.mkdir()
    (home / "config.toml").write_text(
        '[mcp_servers.guanlan]\nurl="http://old"\n'
        '[mcp_servers.other]\ncommand="unsafe"\n'
        '[plugins."example@plugin"]\nenabled=true\n',
        encoding="utf-8",
    )
    skill_file = home / "skills" / "demo" / "SKILL.md"
    skill_file.parent.mkdir(parents=True)
    skill_file.write_text("# Demo", encoding="utf-8")
    monkeypatch.setenv("CODEX_HOME", str(home))
    monkeypatch.setenv("GUANLAN_MCP_BEARER_TOKEN", "long-lived-secret")
    command = _app_server_command(
        tmp_path / "codex.exe", tmp_path,
        frozenset({"ww_news_search"}),
    )
    joined = "\n".join(command)
    assert "features.shell_tool=false" in joined
    assert "features.unified_exec" not in joined
    assert "features.apps=false" in joined
    assert 'model_provider="openai"' in joined
    assert "skills.config=[" in joined
    assert str(skill_file.resolve()).replace("\\", "\\\\") in joined
    assert 'web_search="disabled"' in joined
    assert "apps._default.enabled=false" in joined
    assert "mcp_servers.other.enabled=false" in joined
    assert '"example@plugin"={enabled=false}' in joined
    assert 'mcp_servers.guanlan.enabled_tools=["ww_news_search"]' in joined
    assert "mcp_servers.guanlan.tool_timeout_sec=1200" in joined
    assert "destructive_enabled" not in joined
    assert (
        'mcp_servers.guanlan.bearer_token_env_var='
        '"GUANLAN_CODEX_SCOPE_TOKEN"'
    ) in joined
    assert "ww_model_delete" in joined
    assert "mcp_servers.guanlan.tools.ww_model_delete.enabled" not in joined
    child_env = _child_environment("short-scope")
    assert child_env["GUANLAN_CODEX_SCOPE_TOKEN"] == "short-scope"
    assert "127.0.0.1" in child_env["NO_PROXY"].split(",")
    assert "localhost" in child_env["NO_PROXY"].split(",")
    assert "GUANLAN_MCP_BEARER_TOKEN" not in child_env
    assert "long-lived-secret" not in joined


@pytest.mark.parametrize("unsafe_url", [
    "https://127.0.0.1:9999/gl-mcp-codex/",
    "http://localhost:9999/gl-mcp-codex/",
    "http://127.0.0.1:9998/gl-mcp-codex/",
    "http://user@127.0.0.1:9999/gl-mcp-codex/",
    "http://127.0.0.1:9999/gl-mcp-codex/?forward=1",
    "http://example.com:9999/gl-mcp-codex/",
])
def test_codex_command_rejects_non_exact_loopback_mcp_url(
        tmp_path, monkeypatch, unsafe_url):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "empty-codex-home"))
    monkeypatch.setenv("GUANLAN_CODEX_MCP_URL", unsafe_url)
    with pytest.raises(CodexBackendError, match="只允许本机"):
        _app_server_command(
            tmp_path / "codex.exe", tmp_path,
            frozenset({"ww_news_search"}),
        )


def test_codex_initialize_fails_closed_if_unsafe_feature_is_enabled(tmp_path):
    agent = _agent(tmp_path)

    class Client:
        async def request(self, method, params, timeout=60):
            if method == "initialize":
                return {"serverInfo": {"name": "Codex"}}
            if method == "account/read":
                return {"account": {"type": "chatgpt"}}
            if method == "experimentalFeature/list":
                return {"data": [
                    {"name": name, "enabled": name == "shell_tool"}
                    for name in _REQUIRED_DISABLED_EXPERIMENTAL_FEATURES
                ]}
            raise AssertionError(method)

        async def send(self, payload):
            assert payload == {"method": "initialized", "params": {}}

    with pytest.raises(CodexBackendError, match="shell_tool"):
        asyncio.run(agent._initialize(Client(), frozenset({"ww_news_search"})))


def test_codex_initialize_fails_closed_if_any_skill_remains_enabled(tmp_path):
    agent = _agent(tmp_path)

    class Client:
        async def request(self, method, params, timeout=60):
            if method == "initialize":
                return {"serverInfo": {"name": "Codex"}}
            if method == "account/read":
                return {"account": {"type": "chatgpt"}}
            if method == "experimentalFeature/list":
                return {"data": [
                    {"name": name, "enabled": False}
                    for name in _REQUIRED_DISABLED_EXPERIMENTAL_FEATURES
                ]}
            if method == "skills/list":
                assert params == {
                    "cwds": [str(tmp_path.resolve())], "forceReload": True,
                }
                return {"data": [{
                    "cwd": str(tmp_path), "errors": [],
                    "skills": [{"name": "skill-creator", "enabled": True}],
                }]}
            raise AssertionError(method)

        async def send(self, payload):
            return None

    with pytest.raises(CodexBackendError, match="skill-creator"):
        asyncio.run(agent._initialize(
            Client(), frozenset({"ww_news_search"}), tmp_path,
        ))


def test_codex_mcp_inventory_is_two_stage_and_thread_scoped(tmp_path):
    agent = _agent(tmp_path)
    expected = frozenset({"ww_news_search"})

    class Client:
        async def request(self, method, params, timeout=60):
            if method == "initialize":
                return {"serverInfo": {"name": "Codex"}}
            if method == "account/read":
                return {"account": {"type": "chatgpt"}}
            if method == "experimentalFeature/list":
                return {"data": [
                    {"name": name, "enabled": False}
                    for name in _REQUIRED_DISABLED_EXPERIMENTAL_FEATURES
                ]}
            if method == "skills/list":
                return {"data": [{"cwd": str(tmp_path), "skills": [], "errors": []}]}
            if method == "mcpServerStatus/list":
                assert params["limit"] == 100
                tools = ({"ww_news_search": {}}
                         if params.get("threadId") == "thread-1" else {})
                return {"data": [
                    {"name": "guanlan", "tools": tools},
                    {"name": "tdx", "tools": {}},
                ], "nextCursor": None}
            raise AssertionError(method)

        async def send(self, payload):
            return None

    client = Client()
    asyncio.run(agent._initialize(client, expected, tmp_path))
    asyncio.run(agent._verify_mcp_tools(
        client, expected, thread_id="thread-1", require_ready=True,
    ))


def test_codex_allowlist_keeps_audited_queries_snapshots_and_confirmed_writers():
    allowed = _codex_tool_allowlist({
        "ww_news_search",
        "ww_events_search",
        "ww_market_clues",
        "ww_workspace_trade_disclosure_calendar",
        "ww_workspace_company_research_prepare",
        "ww_workspace_apply_patch",
        "ww_workspace_undo",
        "ww_workspace_quant_chart",
        "ww_workspace_publish_image",
        "ww_model_delete",
        "ww_screen_run",
        "alpha_bench",
        "factor_report",
        "guanlan_workspace_list",
    })
    assert allowed == frozenset({
        "ww_news_search",
        "ww_events_search",
        "ww_market_clues",
        "ww_workspace_trade_disclosure_calendar",
        "ww_workspace_company_research_prepare",
        "ww_workspace_apply_patch",
        "ww_workspace_quant_chart",
        "ww_screen_run",
        "alpha_bench",
        "factor_report",
    })


def test_codex_company_research_facade_requires_stock_and_real_console_lanes():
    from guanlan_v2.glmcp.company_research import (
        CODEX_COMPANY_RESEARCH_TOOL,
    )

    dependencies = {
        "ww_news_search",
        "ww_report_run",
        "ww_workspace_company_research_prepare",
    }
    stock = {"kind": "stock", "code": "SH600000"}
    derived = _console_tools_with_company_research_facade(dependencies, stock)
    assert CODEX_COMPANY_RESEARCH_TOOL in derived
    assert CODEX_COMPANY_RESEARCH_TOOL in _codex_tool_allowlist(derived)

    no_report = dependencies - {"ww_report_run"}
    assert CODEX_COMPANY_RESEARCH_TOOL not in (
        _console_tools_with_company_research_facade(no_report, stock)
    )
    assert CODEX_COMPANY_RESEARCH_TOOL not in (
        _console_tools_with_company_research_facade(
            dependencies, {"kind": "etf", "code": "SH510300"},
        )
    )


def test_codex_explicit_surface_has_complete_disjoint_audit_classes():
    from guanlan_v2.glmcp.tooltable import build_mcp_tools
    from guanlan_v2.glmcp.company_research import (
        CODEX_COMPANY_RESEARCH_TOOL,
    )

    declared = {row["name"] for row in build_mcp_tools()}
    assert len(_CODEX_AUTO_QUERY_TOOLS) == 50
    assert len(_CODEX_WORKSPACE_SNAPSHOT_TOOLS) == 12
    assert _CODEX_CONFIRMATION_REQUIRED_TOOLS == frozenset({
        "ww_workflow_save", "ww_workflow_run",
        "ww_workspace_apply_patch",
        CODEX_COMPANY_RESEARCH_TOOL,
        "ww_workspace_factor_save",
        "ww_workspace_industry_review",
    })
    assert _CODEX_FIXED_PRESENTATION_TOOLS == frozenset({
        "ww_workspace_quant_chart",
    })
    assert _CODEX_WORKFLOW_QUERY_TOOLS == frozenset({
        "ww_workflow_catalog", "ww_workflow_generate", "ww_workflow_validate",
        "ww_workflow_get", "ww_workflow_runs",
    })
    assert len(_CODEX_ALLOWED_TOOLS) == 74
    assert not (_CODEX_WORKFLOW_QUERY_TOOLS & (
        _CODEX_AUTO_QUERY_TOOLS | _CODEX_WORKSPACE_SNAPSHOT_TOOLS
        | _CODEX_CONFIRMATION_REQUIRED_TOOLS | _CODEX_FIXED_PRESENTATION_TOOLS
    ))
    assert _CODEX_ALLOWED_TOOLS <= declared | {CODEX_COMPANY_RESEARCH_TOOL}
    assert not (
        _CODEX_AUTO_QUERY_TOOLS & _CODEX_WORKSPACE_SNAPSHOT_TOOLS
        or _CODEX_AUTO_QUERY_TOOLS & _CODEX_CONFIRMATION_REQUIRED_TOOLS
        or _CODEX_WORKSPACE_SNAPSHOT_TOOLS
        & _CODEX_CONFIRMATION_REQUIRED_TOOLS
        or _CODEX_FIXED_PRESENTATION_TOOLS
        & (_CODEX_AUTO_QUERY_TOOLS | _CODEX_WORKSPACE_SNAPSHOT_TOOLS
           | _CODEX_CONFIRMATION_REQUIRED_TOOLS)
    )
    assert not (_CODEX_ALLOWED_TOOLS & _CODEX_DISALLOWED_PRESENTATION_TOOLS)
    assert _CODEX_CLOSED_RISK_TOOLS == frozenset({
        "alpha_compare", "alpha_forge", "event_report",
        "guanlan_workspace_list", "ww_cards_save",
        "ww_etf_report_run",
        "ww_backtest", "ww_factor_analyze", "ww_factor_compose",
        "ww_factor_promote", "ww_factor_tsic", "ww_factorlib_save",
        "ww_feature_build", "ww_memory_read",
        "ww_memory_write", "ww_model_delete", "ww_model_promote",
        "ww_model_set_default", "ww_model_train", "ww_model_validate",
        "ww_news_collect", "ww_orchestrate_start",
        "ww_regen", "ww_report_run", "ww_rerank_distill", "ww_rescore",
        "ww_research_loop", "ww_seats_decide",
        "ww_ta_ingest", "ww_update_data", "ww_watch_alert_manage",
        "ww_workspace_analysis_run", "ww_workspace_factor_publish",
        "ww_workspace_run", "ww_workspace_undo",
    })
    assert len(_CODEX_CLOSED_RISK_TOOLS) == 35
    # Four legacy capabilities are admitted only through server-enforced
    # scoped query adapters; all remaining explicit risk tools, including the
    # delete-capable workspace undo, stay closed.
    assert {"ww_news_search", "ww_screen_run", "alpha_bench", "factor_report"} \
        <= _CODEX_ALLOWED_TOOLS
    assert not (_CODEX_CLOSED_RISK_TOOLS & _CODEX_ALLOWED_TOOLS)
    closed = _CODEX_CLOSED_RISK_TOOLS | _CODEX_DISALLOWED_PRESENTATION_TOOLS
    assert len(closed) == 37
    assert declared == (
        _CODEX_ALLOWED_TOOLS - {CODEX_COMPANY_RESEARCH_TOOL}
    ) | closed


def test_codex_diagnostics_reports_scoped_confirmations_and_closed_undo(
        tmp_path, monkeypatch):
    executable = tmp_path / "codex.exe"
    executable.write_bytes(b"probe")
    monkeypatch.setenv("GUANLAN_CODEX_EXE", str(executable))

    diagnostics = codex_backend_diagnostics()

    assert diagnostics["approval_policy"] == (
        "Codex built-ins never; scoped workspace mutations use Guanlan "
        "per-call confirmation"
    )
    assert diagnostics["confirmation_required_tools"] == [
        "ww_workflow_run", "ww_workflow_save",
        "ww_workspace_apply_patch",
        "ww_workspace_company_research_run",
        "ww_workspace_factor_save",
        "ww_workspace_industry_review",
    ]
    assert diagnostics["workflow_query_tools"] == 5
    assert diagnostics["total_scoped_tools"] == 74
    assert len(diagnostics["closed_risk_tools"]) == 35
    assert "ww_workspace_undo" in diagnostics["closed_risk_tools"]


def test_codex_presentation_and_default_news_write_are_not_marked_read_only():
    from guanlan_v2.glmcp.tooltable import build_mcp_tools

    by_name = {row["name"]: row for row in build_mcp_tools()}
    for name in (_CODEX_DISALLOWED_PRESENTATION_TOOLS
                 | _CODEX_FIXED_PRESENTATION_TOOLS
                 | {"ww_news_search", "ww_screen_run", "alpha_bench",
                    "factor_report"}):
        assert by_name[name]["read_only"] is False
        assert by_name[name]["destructive"] is False


def test_news_search_private_keyword_switch_disables_shared_write_through(
        monkeypatch):
    import inspect
    import guanlan_v2.console.tools as tools

    parameter = inspect.signature(tools.news_search_impl).parameters[
        "_write_through"
    ]
    assert parameter.kind is inspect.Parameter.KEYWORD_ONLY
    writes = []
    monkeypatch.setattr(tools, "_sentiment_write_through", writes.append)
    monkeypatch.setattr(tools, "_run_news_sentiment", lambda codes, limit: {
        "ok": True,
        "as_of": "2026-08-30T00:00:00+00:00",
        "market_read": "中性",
        "market": [],
        "sentiment": {},
        "by_code": {},
    })

    result = tools.news_search_impl(scope="market", _write_through=False)
    assert result["ok"] is True
    assert writes == []
    tools.news_search_impl(scope="market")
    assert len(writes) == 1


def test_codex_developer_instructions_use_canonical_exec_mcp_names(tmp_path):
    agent = _agent(tmp_path)
    instructions = agent._developer_instructions(
        {"id": "rw_general", "code": ""},
        {"ww_news_search", "ww_workspace_trade_disclosure_calendar"},
    )
    assert "tools.mcp__guanlan__ww_news_search" in instructions
    assert "mcp__guanlan__ww_workspace_trade_disclosure_calendar" in instructions
    assert "tools.ww_news_search 或" in instructions
    assert "硬允许的 functions.exec 工具目录" in instructions

    quant_instructions = agent._developer_instructions(
        {"id": "rw_general", "code": "SH600519"},
        {"ww_workspace_quant_chart"},
    )
    assert "固定实现的 ww_workspace_quant_chart" in quant_instructions
    assert "不接受代码、命令、URL 或文件路径" in quant_instructions


def test_codex_allowlist_does_not_auto_grant_new_read_only_tools(monkeypatch):
    import guanlan_v2.glmcp.tooltable as tooltable

    monkeypatch.setattr(tooltable, "build_mcp_tools", lambda: [{
        "name": "ww_future_read_but_writes_audit", "read_only": True,
    }])
    assert _codex_tool_allowlist({
        "ww_future_read_but_writes_audit", "ww_screen_run",
    }) == frozenset()


def test_codex_inflight_state_is_durable_until_matching_boundary(tmp_path):
    agent = _agent(tmp_path)
    state = agent._new_state(
        "019a1b2c-1234-7abc-8def-1234567890ab", "rw_general",
        "a" * 64, "b" * 64,
    )
    agent.store.save_codex_state(agent.sid, state)
    nonce = agent._mark_inflight()
    inflight = agent.store.load_codex_state(agent.sid)
    assert inflight["health"] == "inflight" and inflight["turn_nonce"] == nonce
    turn_id = "019a1b2c-9876-7abc-8def-1234567890ab"
    agent._set_active_turn(nonce, turn_id)
    agent._mark_boundary_ready(turn_id)
    ready = agent.store.load_codex_state(agent.sid)
    assert ready["health"] == "ready"
    assert ready["active_turn_id"] is None and ready["turn_nonce"] is None


def test_codex_thread_refuses_same_workspace_after_subject_change(tmp_path):
    agent = _agent(tmp_path)
    workspace = agent.store.workspace_store.get("rw_general")
    original_subject = _subject_digest(workspace)
    state = agent._new_state(
        "019a1b2c-1234-7abc-8def-1234567890ab", "rw_general",
        "a" * 64, original_subject,
    )
    agent.store.save_codex_state(agent.sid, state)
    changed = {**workspace, "code": "SH600000", "subject_since": "new"}

    class Client:
        async def request(self, *args, **kwargs):
            raise AssertionError("subject mismatch must fail before resume")

    with pytest.raises(CodexBackendError, match="另一研究空间"):
        asyncio.run(agent._open_thread(
            Client(), changed, tmp_path, "a" * 64,
            _subject_digest(changed), "gpt-5.6-terra", "developer",
        ))


def test_codex_commits_terminal_boundary_before_yielding_done(
        tmp_path, monkeypatch):
    import guanlan_v2.console.codex_backend as cb

    agent = _agent(tmp_path)
    workspace = agent.store.workspace_store.get("rw_general")
    thread_id = "019a1b2c-1234-7abc-8def-1234567890ab"
    turn_id = "019a1b2c-9876-7abc-8def-1234567890ab"
    state = agent._new_state(
        thread_id, "rw_general", "a" * 64, _subject_digest(workspace),
    )
    agent.store.save_codex_state(agent.sid, state)
    monkeypatch.setattr(agent, "_workspace_binding", lambda: (
        workspace, tmp_path, "a" * 64, _subject_digest(workspace),
    ))
    monkeypatch.setattr(agent, "_harness_root", lambda: tmp_path)
    hard_allowed = frozenset({
        "ww_news_search", "ww_workspace_apply_patch",
    })
    monkeypatch.setattr(cb, "_codex_tool_allowlist",
                        lambda _tools: hard_allowed)

    import guanlan_v2.glmcp.scope as scope_module
    registered = {}
    revoked = []

    def fake_register(**kwargs):
        registered.update(kwargs)
        return "scope-token"

    monkeypatch.setattr(scope_module, "register_codex_scope", fake_register)
    monkeypatch.setattr(
        scope_module, "revoke_codex_scope", revoked.append,
    )

    class Client:
        async def request(self, method, params, timeout=60):
            if method == "mcpServerStatus/list":
                return {"data": [{
                    "name": "guanlan",
                    "tools": {name: {} for name in hard_allowed},
                }], "nextCursor": None}
            assert method == "turn/start"
            return {"turn": {"id": turn_id}}

        async def next_notification(self, timeout=900):
            return {"method": "turn/completed", "params": {
                "turn": {"id": turn_id, "status": "completed"},
            }}

        async def close(self):
            return None

    client = Client()

    async def fake_start(*args, **kwargs):
        return client

    async def fake_initialize(self, current, expected, cwd=None):
        return None

    async def fake_open(self, *args, **kwargs):
        return thread_id, self.store.load_codex_state(self.sid)

    monkeypatch.setattr(cb, "_start_app_server", fake_start)
    monkeypatch.setattr(CodexBackendAgent, "_initialize", fake_initialize)
    monkeypatch.setattr(CodexBackendAgent, "_open_thread", fake_open)

    async def confirm(name, args):
        return name == "ww_workspace_apply_patch"

    async def run():
        stream = agent.run_turn(
            "probe", confirm_callback=confirm,
            allowed_tools=hard_allowed,
        )
        event = await stream.__anext__()
        assert event.kind == "done"
        # Closing exactly at the public done event must not strand inflight.
        assert agent.store.load_codex_state(agent.sid)["health"] == "ready"
        await stream.aclose()

    asyncio.run(run())
    assert registered["workspace_root_sha256"] == "a" * 64
    assert registered["allowed_tools"] == hard_allowed
    assert registered["confirmation_required_tools"] == frozenset({
        "ww_workspace_apply_patch",
    })
    assert registered["confirm_callback"] is confirm
    assert revoked == ["scope-token"]
