# tests/test_glmcp_background.py
# glmcp background 信封真执行门禁:detached 子进程真起 + 诚实受理凭证(绝不谎称完成)。
# 新文件(不动并行 WIP 中的 test_guanlan_mcp.py)。
import asyncio
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
_ENGINE = _REPO / "engine"
if _ENGINE.is_dir() and "financial_analyst" not in sys.modules:
    sys.path.insert(0, str(_ENGINE))

import guanlan_v2.glmcp.server as G  # noqa: E402


class _FakeProc:
    pid = 12345


def test_spawn_detached_report_builds_cli(monkeypatch):
    calls = {}

    def fake_popen(cmd, **kw):
        calls["cmd"], calls["kw"] = cmd, kw
        return _FakeProc()

    monkeypatch.setattr("subprocess.Popen", fake_popen)
    # 证据包构建是独立关注点(见 test_report_seams.py),这里桩掉防止真触网/真落盘拖慢本测。
    monkeypatch.setattr(G, "_build_pack_safe", lambda code: None)
    txt = G._spawn_background_detached({"kind": "report", "code": "SH600000", "name": "x", "asof": None})
    assert "已真启动" in txt and "mcpbg_" in txt and "5-8" in txt
    assert "完成" not in txt                                   # 受理凭证,绝不谎称完成
    assert calls["cmd"][1:3] == ["report", "SH600000"]         # financial-analyst report <code>
    assert "--asof" not in calls["cmd"]
    assert calls["kw"]["creationflags"] & 0x00000008           # DETACHED_PROCESS
    assert str(calls["kw"]["cwd"]).lower().endswith("guanlan-v2")


def test_spawn_detached_report_with_asof(monkeypatch):
    calls = {}
    monkeypatch.setattr("subprocess.Popen", lambda cmd, **kw: calls.update(cmd=cmd) or _FakeProc())
    monkeypatch.setattr(G, "_build_pack_safe", lambda code: None)
    G._spawn_background_detached({"kind": "report", "code": "SZ000001", "asof": "2026-07-01"})
    assert calls["cmd"][-2:] == ["--asof", "2026-07-01"]


def test_spawn_detached_unknown_kind(monkeypatch):
    monkeypatch.setattr("subprocess.Popen",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("不应派生")))
    txt = G._spawn_background_detached({"kind": "review", "code": "x"})
    assert "暂不支持" in txt


def test_dispatch_appends_receipt_and_honest_failure(monkeypatch):
    import mcp.types  # noqa: F401 — 预热 SDK 导入链(其 win32 模块导入期用 subprocess.Popen[bytes] 下标,须在 patch Popen 前完成)
    decl = {"name": "ww_report_run", "gated": False, "engine": False,
            "description": "", "inputSchema": {}, "read_only": False, "destructive": False}
    monkeypatch.setattr(G, "_by_name", lambda: {"ww_report_run": decl})
    monkeypatch.setattr(G, "_resolve_impl", lambda d, n: (
        lambda **kw: {"ok": True, "content": "已受理",
                      "background": {"kind": "report", "code": "SH600000", "name": "", "asof": None}}))
    monkeypatch.setattr(G, "_build_pack_safe", lambda code: None)
    # ① Popen 成功 → content + 凭证
    monkeypatch.setattr("subprocess.Popen", lambda cmd, **kw: _FakeProc())
    out = asyncio.run(G.dispatch_tool("ww_report_run", {}))
    assert "已受理" in out[0].text and "已真启动" in out[0].text
    # ② Popen 失败 → 诚实报错(不吞、不假成功)
    def boom(cmd, **kw):
        raise OSError("exe 不在")
    monkeypatch.setattr("subprocess.Popen", boom)
    out2 = asyncio.run(G.dispatch_tool("ww_report_run", {}))
    assert "后台任务启动失败" in out2[0].text and "已真启动" not in out2[0].text
