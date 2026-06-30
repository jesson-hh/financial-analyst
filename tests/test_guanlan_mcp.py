import asyncio


def test_build_mcp_tools_derivation():
    from guanlan_v2.mcp.tooltable import build_mcp_tools
    import guanlan_v2.console.tools as ct
    tools = build_mcp_tools()
    names = {t["name"] for t in tools}
    ww = {t["name"] for t in ct.WW_TOOL_TABLE}
    assert "ww_plan_update" not in names and "ww_show_page" not in names      # 去除 console-UI-only
    assert (ww - {"ww_plan_update", "ww_show_page"}) <= names                  # 其余 ww_ 全在
    assert {"alpha_list", "alpha_compare", "alpha_forge", "factor_report"} <= names
    assert len(tools) == 35                                                    # 28 ww_ + 7 alpha-zoo


def test_build_mcp_tools_annotations_and_gate():
    from guanlan_v2.mcp.tooltable import build_mcp_tools
    by = {t["name"]: t for t in build_mcp_tools()}
    assert by["ww_model_delete"]["destructive"] and by["ww_model_delete"]["gated"]      # confirm=True
    assert by["ww_model_set_default"]["gated"]
    assert by["ww_screen_factors"]["read_only"] and not by["ww_screen_factors"]["gated"]  # 只读
    assert (not by["ww_memory_write"]["read_only"]) and (not by["ww_memory_write"]["gated"])  # 写但不锁
    assert by["alpha_forge"]["destructive"] and by["alpha_forge"]["gated"]              # 唯一 alpha 写
    assert by["alpha_compare"]["read_only"] and not by["alpha_compare"]["gated"]        # 贵但只读=不锁
    assert by["alpha_list"]["read_only"] and not by["alpha_list"]["gated"]


def test_dispatch_readonly_wraps_impl(monkeypatch):
    import guanlan_v2.mcp.server as ms

    async def fake_to_thread(fn, **kw):
        return {"ok": True, "content": "RESULT_X"}
    monkeypatch.setattr(ms.asyncio, "to_thread", fake_to_thread)
    res = asyncio.run(ms.dispatch_tool("ww_screen_factors", {}))
    assert res[0].text == "RESULT_X"


def test_dispatch_write_gate(monkeypatch):
    import guanlan_v2.mcp.server as ms
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
    import guanlan_v2.mcp.server as ms
    res = asyncio.run(ms.dispatch_tool("ww_nope", {}))
    assert "未知工具" in res[0].text


def test_build_server_name():
    from guanlan_v2.mcp.server import build_server
    assert build_server().name == "guanlan"


def test_build_mcp_http_app_is_starlette():
    from guanlan_v2.mcp.http import build_mcp_http_app
    from starlette.applications import Starlette
    assert isinstance(build_mcp_http_app(), Starlette)


def test_main_module_has_main():
    import importlib
    m = importlib.import_module("guanlan_v2.mcp.__main__")
    assert callable(getattr(m, "main", None))


def test_server_mounts_gl_mcp_alongside_engine_mcp():
    import guanlan_v2.server as s
    mounts = [getattr(r, "path", None) for r in s.app.routes if r.__class__.__name__ == "Mount"]
    assert "/gl-mcp" in mounts          # 新 guanlan MCP
    assert "/mcp" in mounts             # 引擎 MCP 仍在(不破)
    assert "/ui" in mounts              # 既有 UI 不破
