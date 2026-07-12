# -*- coding: utf-8 -*-
"""起报接缝(Task 4,TDD):构包+env 注入+mainline 新鲜面板+market-scanner 证据捷径。

覆盖:
1. console `_call_buddy_report`:起跑前调 `_build_pack_safe(code)`,pack 有 path → env 含
   FA_EVIDENCE_PACK;桩返 None(或 ok:False/path 缺)→ env 无该键,spawn 仍照常进行。
2. glmcp `_spawn_background_detached`(kind=report):同款,同步上下文直调 `_build_pack_safe`。
3. server.py create_app():FA_MAINLINE_PANEL setdefault 指向仓内 vendor 产物(module 级
   `app = create_app()` 已在 import 时执行过——不重复起一次重量级 app)。
4. engine market_scanner.py `MarketScanner`:pack 存在且 sections.board_eco 非空 → 聚合字段
   直接返回(note 标"平台证据"、零触 loader);否则退回现状扫描(max_scan 默认收敛到 1500,
   其余行为逐字节不变)。
"""
from __future__ import annotations

import asyncio
import json
import os

import pytest


# ── ① console _call_buddy_report 构包接缝 ───────────────────────────────────

class _FakeProc:
    def __init__(self, returncode=0, stderr=""):
        self.returncode = returncode
        self.stdout = ""
        self.stderr = stderr


def test_console_call_buddy_report_injects_evidence_pack_env(tmp_path, monkeypatch):
    import guanlan_v2.console.api as capi

    calls = []

    def fake_build_pack_safe(code):
        calls.append(code)
        return {"ok": True, "path": str(tmp_path / "pack.json"), "sections_ok": [], "errors": {}}

    monkeypatch.setattr(capi, "_build_pack_safe", fake_build_pack_safe)
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["env"] = kwargs.get("env")
        return _FakeProc(returncode=0)

    monkeypatch.setattr("subprocess.run", fake_run)
    monkeypatch.setattr(capi, "_freshest_report_md",
                        lambda out_dir, code, t0: tmp_path / f"{code}_x.md")

    result = capi._call_buddy_report("SZ300750", None)

    assert calls == ["SZ300750"]
    assert captured["env"]["FA_EVIDENCE_PACK"] == str(tmp_path / "pack.json")
    assert result["ok"] is True


def test_console_call_buddy_report_no_pack_omits_env_key_spawn_proceeds(tmp_path, monkeypatch):
    import guanlan_v2.console.api as capi

    monkeypatch.setattr(capi, "_build_pack_safe", lambda code: None)
    captured = {}
    calls = {"n": 0}

    def fake_run(cmd, **kwargs):
        calls["n"] += 1
        captured["env"] = kwargs.get("env")
        return _FakeProc(returncode=0)

    monkeypatch.setattr("subprocess.run", fake_run)
    monkeypatch.setattr(capi, "_freshest_report_md",
                        lambda out_dir, code, t0: tmp_path / f"{code}_x.md")

    result = capi._call_buddy_report("SZ000001", None)

    assert calls["n"] == 1                                   # spawn 照常发生
    assert "FA_EVIDENCE_PACK" not in captured["env"]
    assert result["ok"] is True


def test_console_call_buddy_report_pack_not_ok_omits_env_key(tmp_path, monkeypatch):
    """build_evidence_pack 落盘失败(ok:False, path:None)同样不设 env——判 path 而非判 ok。"""
    import guanlan_v2.console.api as capi

    monkeypatch.setattr(capi, "_build_pack_safe",
                        lambda code: {"ok": False, "path": None, "sections_ok": [], "errors": {"_write": "boom"}})
    captured = {}

    def fake_run(cmd, **kw):
        captured["env"] = kw.get("env")
        return _FakeProc()

    monkeypatch.setattr("subprocess.run", fake_run)
    monkeypatch.setattr(capi, "_freshest_report_md",
                        lambda out_dir, code, t0: tmp_path / f"{code}_x.md")

    result = capi._call_buddy_report("SZ000001", None)
    assert "FA_EVIDENCE_PACK" not in captured["env"]
    assert result["ok"] is True


def test_console_build_pack_safe_swallows_exceptions(monkeypatch, capsys):
    """真实 _build_pack_safe(非桩):内部函数炸了也绝不冒泡,诚实返 None(只打印诊断)。"""
    import guanlan_v2.console.api as capi
    import guanlan_v2.reports.evidence as evidence

    def _boom(code):
        raise RuntimeError("evidence 模块炸了")
    monkeypatch.setattr(evidence, "build_evidence_pack", _boom)

    result = capi._build_pack_safe("SZ000001")
    assert result is None
    out = capsys.readouterr().out
    assert "evidence pack build failed" in out


# ── ② glmcp _spawn_background_detached(kind=report)同款接缝 ────────────────

def test_glmcp_spawn_background_report_injects_evidence_pack_env(tmp_path, monkeypatch):
    import guanlan_v2.glmcp.server as gsrv

    calls = []

    def fake_build_pack_safe(code):
        calls.append(code)
        return {"ok": True, "path": str(tmp_path / "pack.json"), "sections_ok": [], "errors": {}}

    monkeypatch.setattr(gsrv, "_build_pack_safe", fake_build_pack_safe)
    captured = {}

    class _FakePopen:
        def __init__(self, cmd, **kw):
            captured["cmd"] = cmd
            captured["kw"] = kw

    monkeypatch.setattr("subprocess.Popen", _FakePopen)
    receipt = gsrv._spawn_background_detached({"kind": "report", "code": "SZ300750"})

    assert calls == ["SZ300750"]
    assert captured["kw"]["env"]["FA_EVIDENCE_PACK"] == str(tmp_path / "pack.json")
    assert "已真启动" in receipt
    _cleanup_empty_mcp_logs(gsrv)


def test_glmcp_spawn_background_report_no_pack_omits_env_key(tmp_path, monkeypatch):
    import guanlan_v2.glmcp.server as gsrv

    monkeypatch.setattr(gsrv, "_build_pack_safe", lambda code: None)
    captured = {}

    def fake_popen(cmd, **kw):
        captured["kw"] = kw
        return object()

    monkeypatch.setattr("subprocess.Popen", fake_popen)
    receipt = gsrv._spawn_background_detached({"kind": "report", "code": "SZ000001"})

    assert "FA_EVIDENCE_PACK" not in captured["kw"]["env"]
    assert "已真启动" in receipt
    _cleanup_empty_mcp_logs(gsrv)


def test_glmcp_spawn_background_etf_report_untouched_by_pack(tmp_path, monkeypatch):
    """ETF 研报线零回归:etf_report 分支不构证据包(_build_pack_safe 不应被调)。"""
    import guanlan_v2.glmcp.server as gsrv

    def _boom(code):
        raise AssertionError("etf_report 分支不应触发证据包构建")
    monkeypatch.setattr(gsrv, "_build_pack_safe", _boom)
    monkeypatch.setattr("subprocess.Popen", lambda cmd, **kw: object())

    receipt = gsrv._spawn_background_detached({"kind": "etf_report", "code": "510300", "asof": None})
    assert "已真启动" in receipt
    _cleanup_empty_mcp_logs(gsrv)


def _cleanup_empty_mcp_logs(gsrv_module):
    """本测新建的空 mcp_bg_*.log 清掉(同 test_guanlan_mcp.py 既有清理惯例)。"""
    from pathlib import Path
    for p in (Path(gsrv_module.__file__).resolve().parents[2] / "var").glob("mcp_bg_*.log"):
        if p.stat().st_size == 0:
            p.unlink()


# ── ②b dispatch_tool 的 background spawn 必须离开 9999 事件循环线程 ──────────
# (Task 4 评审升格缺陷:_spawn_background_detached 内部同步跑 _build_pack_safe——
#  十 section 证据包构建,含 kuaixun/probe 等秒级网络调用,最坏 10-30s——旧代码里
#  dispatch_tool 直调而非 asyncio.to_thread,等于把该网络调用堵在 9999 事件循环
#  线程上,触发看门狗误杀 9999 的历史真实事故模式。console 路径无此问题,因其整体
#  跑在 executor 线程。)

def test_dispatch_tool_spawns_background_off_event_loop(monkeypatch):
    """回归锁:经真实 dispatch_tool 路径触发 report background spawn,桩 _build_pack_safe
    在函数体内探测当前是否在运行中的 event loop 线程——修复前(直调)应为 True/RED,
    修复后(asyncio.to_thread)应为 False/GREEN。"""
    import mcp.types  # noqa: F401 — 预热 SDK 导入链(win32 模块导入期用 subprocess.Popen[bytes] 下标,须在 patch Popen 前完成)
    import guanlan_v2.glmcp.server as gsrv

    seen = {}

    def _probe(code):
        try:
            asyncio.get_running_loop()
            seen["on_loop"] = True     # 在 loop 线程 = 缺陷回归
        except RuntimeError:
            seen["on_loop"] = False    # 在工作线程 = 正确
        return None

    monkeypatch.setattr(gsrv, "_build_pack_safe", _probe)
    monkeypatch.setattr("subprocess.Popen", lambda cmd, **kw: object())

    decl = {"name": "ww_report_run", "gated": False, "engine": False,
            "description": "", "inputSchema": {}, "read_only": False, "destructive": False}
    monkeypatch.setattr(gsrv, "_by_name", lambda: {"ww_report_run": decl})
    monkeypatch.setattr(gsrv, "_resolve_impl", lambda d, n: (
        lambda **kw: {"ok": True, "content": "已受理",
                      "background": {"kind": "report", "code": "SH600000", "name": "", "asof": None}}))

    out = asyncio.run(gsrv.dispatch_tool("ww_report_run", {}))

    assert "已真启动" in out[0].text
    assert seen["on_loop"] is False
    _cleanup_empty_mcp_logs(gsrv)


# ── ③ server.py create_app() FA_MAINLINE_PANEL setdefault ──────────────────

def test_server_fa_mainline_panel_path_points_at_vendor_artifact():
    import guanlan_v2.server as s
    assert s._MAINLINE_PANEL_PATH.parts[-5:] == (
        "guanlan_v2", "strategy", "vendor", "artifacts", "monthly_mainlines_panel.parquet")


def test_server_fa_mainline_panel_setdefault_applied():
    """module 级 `app = create_app()` 已在 import 时跑过(全套件必然发生,server.py:376)——
    不重复起一次重量级 create_app(),直接断言其 setdefault 的落地结果。"""
    import guanlan_v2.server as s
    assert os.environ.get("FA_MAINLINE_PANEL") == str(s._MAINLINE_PANEL_PATH)


# ── ④ engine market_scanner.py 证据包捷径 ───────────────────────────────────

def _write_pack(tmp_path, sections):
    p = tmp_path / "pack.json"
    p.write_text(json.dumps({"code": "ALL", "generated_at": "2026-07-12T09:30:00",
                             "sections": sections}, ensure_ascii=False), encoding="utf-8")
    return p


class _BoomLoader:
    """证据包命中路径绝不该碰 loader——任何调用即测试失败显形。"""
    def fetch_quote(self, *a, **k):
        raise AssertionError("evidence-pack 路径不应触碰 loader.fetch_quote")

    def fetch_daily_basic(self, *a, **k):
        raise AssertionError("evidence-pack 路径不应触碰 loader.fetch_daily_basic")


def test_scanner_evidence_pack_shortcut_returns_note_and_skips_loader(tmp_path):
    from financial_analyst.agent.market.market_scanner import MarketScanner

    board = {"as_of": "2026-07-12T09:25:00", "zt_count": 42, "zb_count": 5,
             "break_rate": 0.106, "promotion_rate": 0.33, "lhb": [], "north_net": -397.9}
    pack_path = _write_pack(tmp_path, {"board_eco": board})

    scanner = MarketScanner(memory_root=tmp_path, loader=_BoomLoader(), pack_path=str(pack_path))
    result = asyncio.run(scanner._execute({"asof_date": "2026-07-12", "universe": "all"}))

    assert "平台证据" in result["note"]
    assert result["n_scanned"] == 0
    assert result["n_flagged"] == 47                          # zt_count + zb_count
    assert result["index_snapshot"]["north_net"] == -397.9
    assert result["top_gainers"] == [] and result["top_losers"] == [] and result["volume_anomalies"] == []
    # Output schema 未改动:仍能被 MarketScannerOutput 消费(note 是额外键,校验时被忽略,不报错)
    from financial_analyst.agent.market.market_scanner import MarketScannerOutput
    out = MarketScannerOutput(**result)
    assert out.n_flagged == 47


def test_scanner_pack_without_board_eco_falls_back(tmp_path, monkeypatch):
    from financial_analyst.agent.market.market_scanner import MarketScanner

    pack_path = _write_pack(tmp_path, {"board_eco": None, "quote_live": {"price": 1}})
    missing_universe = tmp_path / "missing_all.txt"
    scanner = MarketScanner(memory_root=tmp_path, loader=_BoomLoader(),
                            pack_path=str(pack_path), universe_file=str(missing_universe))

    with pytest.raises(FileNotFoundError, match="No universe instruments found"):
        asyncio.run(scanner._execute({"asof_date": "2026-07-12", "universe": "all"}))


def test_scanner_no_pack_falls_back_and_max_scan_default_is_1500(tmp_path, monkeypatch):
    from financial_analyst.agent.market.market_scanner import MarketScanner

    monkeypatch.delenv("FA_EVIDENCE_PACK", raising=False)
    missing_universe = tmp_path / "missing_all.txt"
    scanner = MarketScanner(memory_root=tmp_path, universe_file=str(missing_universe))

    assert scanner._max_scan == 1500

    with pytest.raises(FileNotFoundError, match="No universe instruments found"):
        asyncio.run(scanner._execute({"asof_date": "2026-07-12", "universe": "all"}))


def test_scanner_read_evidence_pack_missing_file_returns_none(tmp_path):
    from financial_analyst.agent.market.market_scanner import MarketScanner
    scanner = MarketScanner(memory_root=tmp_path, pack_path=str(tmp_path / "nope.json"))
    assert scanner._read_evidence_pack() is None


def test_scanner_read_evidence_pack_malformed_json_returns_none(tmp_path):
    from financial_analyst.agent.market.market_scanner import MarketScanner
    bad = tmp_path / "bad.json"
    bad.write_text("{not valid json,,,", encoding="utf-8")
    scanner = MarketScanner(memory_root=tmp_path, pack_path=str(bad))
    assert scanner._read_evidence_pack() is None


def test_scanner_ctor_pack_path_wins_over_env(tmp_path, monkeypatch):
    """ctor > env 三层先例(与 evidence_loader/mainline_classifier 同款)。"""
    from financial_analyst.agent.market.market_scanner import MarketScanner

    env_pack = _write_pack(tmp_path, {"board_eco": {"zt_count": 1, "zb_count": 0}})
    ctor_dir = tmp_path / "ctor"
    ctor_dir.mkdir()
    ctor_pack_path = ctor_dir / "pack.json"
    ctor_pack_path.write_text(json.dumps({"sections": {"board_eco": {"zt_count": 99, "zb_count": 1}}}),
                              encoding="utf-8")
    monkeypatch.setenv("FA_EVIDENCE_PACK", str(env_pack))

    scanner = MarketScanner(memory_root=tmp_path, pack_path=str(ctor_pack_path))
    result = asyncio.run(scanner._execute({"asof_date": "2026-07-12", "universe": "all"}))
    assert result["n_flagged"] == 100   # 来自 ctor pack(99+1),非 env pack(1+0)
