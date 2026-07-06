import asyncio


def test_build_mcp_tools_derivation():
    from guanlan_v2.glmcp.tooltable import build_mcp_tools
    import guanlan_v2.console.tools as ct
    tools = build_mcp_tools()
    names = {t["name"] for t in tools}
    ww = {t["name"] for t in ct.WW_TOOL_TABLE}
    assert "ww_plan_update" not in names and "ww_show_page" not in names and "ww_seats_bind" not in names      # 去除 console-UI-only & 前端信封
    assert (ww - {"ww_plan_update", "ww_show_page", "ww_seats_bind"}) <= names                  # 其余 ww_ 全在
    assert {"alpha_list", "alpha_compare", "alpha_forge", "factor_report"} <= names
    assert len(tools) == 54                                                    # 47 ww_(50−3 excluded) + 7 alpha-zoo


def test_build_mcp_tools_annotations_and_gate():
    from guanlan_v2.glmcp.tooltable import build_mcp_tools
    by = {t["name"]: t for t in build_mcp_tools()}
    assert by["ww_model_delete"]["destructive"] and by["ww_model_delete"]["gated"]      # confirm=True
    assert by["ww_model_set_default"]["gated"]
    assert by["ww_screen_factors"]["read_only"] and not by["ww_screen_factors"]["gated"]  # 只读
    assert (not by["ww_memory_write"]["read_only"]) and (not by["ww_memory_write"]["gated"])  # 写但不锁
    assert by["alpha_forge"]["destructive"] and by["alpha_forge"]["gated"]              # 唯一 alpha 写
    assert by["alpha_compare"]["read_only"] and not by["alpha_compare"]["gated"]        # 贵但只读=不锁
    assert by["alpha_list"]["read_only"] and not by["alpha_list"]["gated"]


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
    ms._DECLS = None
    ms.build_server()
    assert ms._DECLS is not None and len(ms._DECLS) == 54


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
    assert "/mcp" in mounts             # 引擎 MCP 仍在(不破)
    assert "/ui" in mounts              # 既有 UI 不破


def test_mcp_excludes_frontend_envelope_tools():
    """ww_seats_bind 靠前端 window.GL 落地,MCP 语境=空转假成功 → 排除(同 ww_show_page)。"""
    from guanlan_v2.glmcp.tooltable import build_mcp_tools
    names = {t["name"] for t in build_mcp_tools()}
    assert "ww_seats_bind" not in names
    assert "ww_report_run" in names          # 研报经 detached 子进程真跑,保留(gated)
    assert len(names) == 54


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
    """spawn 抛错 → dispatch 回错误显形,绝不假成功(红线)。"""
    import guanlan_v2.glmcp.server as ms
    async def fake_to_thread(fn, **kw):
        return {"ok": True, "content": "已受理", "background": {"kind": "report", "code": "X"}}
    monkeypatch.setattr(ms.asyncio, "to_thread", fake_to_thread)
    monkeypatch.setenv("GUANLAN_MCP_WRITE", "1")
    def boom(bg):
        raise RuntimeError("spawn炸了")
    monkeypatch.setattr(ms, "_spawn_background_detached", boom)
    res = asyncio.run(ms.dispatch_tool("ww_report_run", {"code": "X"}))
    assert "后台任务启动失败" in res[0].text and "spawn炸了" in res[0].text


def test_dispatch_background_success_appends_receipt(monkeypatch):
    import guanlan_v2.glmcp.server as ms
    async def fake_to_thread(fn, **kw):
        return {"ok": True, "content": "已受理研报", "background": {"kind": "report", "code": "X"}}
    monkeypatch.setattr(ms.asyncio, "to_thread", fake_to_thread)
    monkeypatch.setenv("GUANLAN_MCP_WRITE", "1")
    monkeypatch.setattr(ms, "_spawn_background_detached", lambda bg: "已真启动后台研报(job t)")
    res = asyncio.run(ms.dispatch_tool("ww_report_run", {"code": "X"}))
    assert "已受理研报" in res[0].text and "已真启动后台研报" in res[0].text
