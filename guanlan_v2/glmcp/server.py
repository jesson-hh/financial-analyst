# -*- coding: utf-8 -*-
"""guanlan MCP server(low-level mcp.server.Server)。镜像引擎 mcp_server,工具复用 ww_/引擎 impl。"""
from __future__ import annotations

import asyncio
import json
import os
from typing import Any, Dict, List, Optional

_DECLS: List[Dict[str, Any]] | None = None


def _decls() -> List[Dict[str, Any]]:
    global _DECLS
    if _DECLS is None:
        from guanlan_v2.glmcp.tooltable import build_mcp_tools
        _DECLS = build_mcp_tools()
    return _DECLS


def _by_name() -> Dict[str, Dict[str, Any]]:
    return {d["name"]: d for d in _decls()}


def _is_write_enabled() -> bool:
    return os.environ.get("GUANLAN_MCP_WRITE") == "1"


def _to_text(result: Any) -> str:
    """impl/引擎结果 → 文本。ww_ impl 回 dict{content};引擎 ToolResult 有 .content。"""
    if isinstance(result, dict):
        return str(result.get("content") or json.dumps(result, ensure_ascii=False, default=str))
    content = getattr(result, "content", None)
    return str(content if content is not None else result)


def _resolve_impl(d: Dict[str, Any], name: str):
    if d["engine"]:
        from financial_analyst.buddy import tools as bt
        return {t.name: t for t in bt.TOOL_REGISTRY}[name].run
    import guanlan_v2.console.tools as ct
    return {t["name"]: t["impl"] for t in ct.WW_TOOL_TABLE}[name]


def _report_spawn_env() -> Dict[str, str]:
    """研报类后台子进程 env(与 console `_call_buddy_report` 逐项对齐):
    PYTHONIOENCODING 防 GBK UnicodeEncodeError + PYTHONPATH 挂仓内 engine/(崩溃根修——
    缺此键子进程吃 venv 里 pinned 旧引擎,缺 news-sentiment 注册即起跑即崩)+ FA_CONFIG_DIR
    钉死仓内 config/(find_config 显式注入赢过 pinned workspace,不靠父进程环境继承)。"""
    from pathlib import Path
    repo = Path(__file__).resolve().parents[2]
    return {**os.environ, "PYTHONIOENCODING": "utf-8",
            "PYTHONPATH": str(repo / "engine") + os.pathsep + os.environ.get("PYTHONPATH", ""),
            "FA_CONFIG_DIR": os.environ.get("FA_CONFIG_DIR", str(repo / "config"))}


def _build_pack_safe(code: str) -> Optional[Dict[str, Any]]:
    """研报起跑前构证据包(best-effort,与 console.api._build_pack_safe 逐项对齐)。整体
    try/except——任何异常(含 import 失败)绝不挡报,只 print 诊断日志返 None。glmcp 与
    9999 同进程,import guanlan_v2.reports.evidence 可行(不像子进程有包边界)。"""
    try:
        from guanlan_v2.reports.evidence import build_evidence_pack
        return build_evidence_pack(code)
    except Exception as e:  # noqa: BLE001 — 构包失败绝不挡报,诚实打印后放行
        print(f"[report] evidence pack build failed for {code}: {type(e).__name__}: {e}")
        return None


def _spawn_background_detached(bg: dict) -> str:
    """background 信封 → detached 子进程真跑(不随 MCP 客户端退出而死)→ 诚实受理凭证。
    console 事件循环外的 MCP 通道没有 _spawn_bg 跑道 —— 此处补齐真执行,修假成功红线。
    kind=report → console 同款 CLI `financial-analyst report`;etf_report → 引擎 run_etf_report。
    本函数内部同步跑 _build_pack_safe(证据包构建,含秒级网络调用)+ Popen——dispatch_tool
    经 asyncio.to_thread 派本函数到工作线程,绝不同步跑在 9999 事件循环线程上(堵 loop→
    健康检查失败→看门狗误杀 9999 的历史事故模式)。"""
    import shutil
    import subprocess
    import sys as _sys
    import uuid
    from pathlib import Path
    repo = Path(__file__).resolve().parents[2]
    kind = str((bg or {}).get("kind") or "")
    code = str((bg or {}).get("code") or "")
    asof = (bg or {}).get("asof")
    pack_path: Optional[str] = None
    if kind == "report":
        exe = shutil.which("financial-analyst") or r"G:\financial-analyst\.venv\Scripts\financial-analyst.exe"
        cmd = [exe, "report", code] + (["--asof", str(asof)] if asof else [])
        pack = _build_pack_safe(code)
        if pack and pack.get("path"):
            pack_path = pack["path"]
    elif kind == "etf_report":
        py = ("import sys; sys.path.insert(0, r'{eng}');"
              "import financial_analyst.buddy.tools as bt;"
              "t = bt.get_tool('run_etf_report');"
              "r = t.run(code={code!r}, asof={asof!r});"
              "print('[etf_report] is_error=', getattr(r, 'is_error', None), flush=True);"
              "print(str(getattr(r, 'content', ''))[:2000], flush=True);"
              "sys.exit(0 if not getattr(r, 'is_error', False) else 1)").format(
                  eng=str(repo / "engine"), code=code, asof=asof)
        cmd = [_sys.executable, "-c", py]
    else:
        return f"该后台任务类型 MCP 通道暂不支持:{kind or '(空)'}(请经帷幄 console 执行)"
    job = "mcpbg_" + uuid.uuid4().hex[:8]
    log = repo / "var" / f"mcp_bg_{job}.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    flags = 0x00000008 | 0x00000200   # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    env = _report_spawn_env()   # PYTHONIOENCODING + PYTHONPATH=engine/ + FA_CONFIG_DIR(与 console 逐项对齐)
    if pack_path:
        env["FA_EVIDENCE_PACK"] = pack_path
    with open(log, "ab") as lf:
        subprocess.Popen(cmd, cwd=str(repo), stdout=lf, stderr=subprocess.STDOUT,
                         creationflags=flags, env=env)
    return (f"已真启动后台研报(job {job} · {code} · 预计 5-8 分钟 · "
            f"产物落 reports store · 日志 {log})")


async def dispatch_tool(name: str, arguments: Dict[str, Any]) -> List[Any]:
    """派发一次 MCP 工具调用 → list[TextContent]。写门 + to_thread + 诚实失败。
    background 信封(研报类长任务)→ detached 子进程真执行 + 受理凭证(修假成功红线)。
    _spawn_background_detached 内部同步跑证据包构建(秒级网络调用)+ Popen,必须经
    asyncio.to_thread 派到工作线程——直调会把网络调用堵在 9999 事件循环线程上,
    触发看门狗误杀(仓级红线,见 Task 4 评审)。"""
    from mcp.types import TextContent
    d = _by_name().get(name)
    if d is None:
        return [TextContent(type="text", text=json.dumps({"error": f"未知工具: {name}"}, ensure_ascii=False))]
    if d["gated"] and not _is_write_enabled():
        return [TextContent(type="text",
                text=f"写操作未启用:{name} 是写/销毁类工具,需在 9999 启动环境设 GUANLAN_MCP_WRITE=1 后重启才放行。")]
    try:
        impl = _resolve_impl(d, name)
        result = await asyncio.to_thread(impl, **(arguments or {}))
    except Exception as e:  # noqa: BLE001 — 诚实失败显形
        return [TextContent(type="text", text=json.dumps({"error": f"{type(e).__name__}: {e}"}, ensure_ascii=False))]
    if isinstance(result, dict) and result.get("background"):
        try:
            receipt = await asyncio.to_thread(_spawn_background_detached, result["background"])
        except Exception as e:  # noqa: BLE001 — 诚实失败显形,绝不假成功
            return [TextContent(type="text", text=json.dumps(
                {"error": f"后台任务启动失败: {type(e).__name__}: {e}"}, ensure_ascii=False))]
        base = str(result.get("content") or "")
        return [TextContent(type="text", text=(base + "\n" + receipt).strip())]
    return [TextContent(type="text", text=_to_text(result))]


def build_server():
    from mcp.server import Server
    from mcp.types import Tool, ToolAnnotations
    # 急切预热:把 console.tools / 引擎 buddy.tools 这类重导入(litellm 等)在
    # 进入 stdio 读循环【之前】跑完。否则首个 list_tools 在请求处理中触发冷导入,
    # 会卡死 stdio 读循环(初始化能回、tools/list 永不返回)。镜像引擎模块级 TOOLS 的急切构建。
    _decls()
    server = Server("guanlan")

    @server.list_tools()
    async def list_tools() -> List[Tool]:
        return [Tool(name=d["name"], description=d["description"], inputSchema=d["inputSchema"],
                     annotations=ToolAnnotations(readOnlyHint=d["read_only"], destructiveHint=d["destructive"]))
                for d in _decls()]

    @server.call_tool()
    async def call_tool(name: str, arguments: Dict[str, Any]) -> List[Any]:
        return await dispatch_tool(name, arguments or {})

    return server
