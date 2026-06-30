# guanlan MCP server Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 给 guanlan 一个真正的 MCP server——把帷幄 `ww_*`(去 2 个 console-UI-only)+ 7 个 alpha-zoo 工具暴露成 MCP 工具,双传输(HTTP `/gl-mcp` + stdio `python -m guanlan_v2.mcp`),复用现成 impl,写操作默认锁。

**Architecture:** 新包 `guanlan_v2/mcp/`:`tooltable.py`(从 `WW_TOOL_TABLE`+alpha-zoo 派生工具声明)→ `server.py`(`mcp.server.Server` + `dispatch_tool`:写门 + `asyncio.to_thread(impl)` + 结果转 `TextContent`)→ `http.py`(Streamable HTTP Starlette 子应用,镜像引擎 `mcp_http`)+ `__main__.py`(stdio)。`guanlan_v2/server.py` 把 `/gl-mcp` 挂上并**叠加** lifespan(引擎 `/mcp` 已占 `/mcp` 并设了 app lifespan,不能覆盖)。

**Tech Stack:** Python 3.13 / `mcp>=1.0`(`mcp.server.Server`、`mcp.types.{Tool,TextContent,ToolAnnotations}`、`mcp.server.stdio.stdio_server`、`mcp.server.streamable_http_manager.StreamableHTTPSessionManager`)/ Starlette / FastAPI。引擎 fork 在 `engine/`(测试 `python -m pytest`,`tests/conftest.py` 已 prepend engine)。

## Global Constraints

- **不动引擎 `/mcp`**:guanlan MCP 走独立 `/gl-mcp`;`server.py` 仅加挂载 + lifespan 叠加(wrap `app.router.lifespan_context` 先进原 lifespan 再进 guanlan MCP 的),不覆盖、不改既有路由。验证锚:`app.routes` 的 Mount 含 `/mcp`+`/ui`+`/gl-mcp`。
- **写默认锁**:`destructive=True` 工具(11 个 `confirm=True` ww_ + `alpha_forge`,共 12)调用时若 `os.environ.get("GUANLAN_MCP_WRITE") != "1"` → 诚实拒、不执行;`list_tools` 仍列出(带 destructiveHint)。其余只读 + `ww_memory_write` 不锁。
- **to_thread 铁律**:impl 内含同步自 HTTP `_self_post`,`call_tool` 协程里必须 `await asyncio.to_thread(impl, **args)`,否则堵事件循环→看门狗杀 9999。
- **诚实**:`ok:False`/异常/未知工具/写门拒 → 都回诚实文本(`TextContent`,SDK 无 isError 字段,error 进 text,同引擎);描述复用 `ww_`/引擎原文;不暴露不存在的能力(无真下单);去 console-UI-only(`ww_plan_update`/`ww_show_page`)。
- **派生防漂移**:工具集从 `WW_TOOL_TABLE`(去 2)+ alpha-zoo 名单派生,守护测试钉总数 35。
- 提交信息结尾:`Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`。

---

### Task 1: 工具表派生(tooltable)

**Files:**
- Create: `guanlan_v2/mcp/__init__.py`(空文件)
- Create: `guanlan_v2/mcp/tooltable.py`
- Test: `tests/test_guanlan_mcp.py`

**Interfaces:**
- Produces: `build_mcp_tools() -> list[dict]`,每条 dict 键:`name`、`description`、`inputSchema`、`read_only:bool`、`destructive:bool`、`gated:bool`、`engine:bool`。

- [ ] **Step 1: 写失败测试**

新建 `tests/test_guanlan_mcp.py`:

```python
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
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_guanlan_mcp.py -k "derivation or annotations" -q`
Expected: FAIL（`ModuleNotFoundError: guanlan_v2.mcp.tooltable`）

- [ ] **Step 3: 实现**

新建空 `guanlan_v2/mcp/__init__.py`。新建 `guanlan_v2/mcp/tooltable.py`:

```python
# -*- coding: utf-8 -*-
"""guanlan MCP 工具表:从 WW_TOOL_TABLE(去 console-UI-only)+ alpha-zoo 派生。"""
from __future__ import annotations

from typing import Any, Dict, List

# console-UI-only(改会话计划 / 往 console 右栏弹页面),MCP 无意义 → 不暴露
_EXCLUDED = {"ww_plan_update", "ww_show_page"}
# 引擎 alpha-zoo 研究线(已在 CONSOLE_ALLOWED 放行)
_ALPHA_ZOO = ["alpha_list", "alpha_show", "alpha_compare", "alpha_bench",
              "event_report", "alpha_forge", "factor_report"]
# alpha-zoo 里唯一会写盘的(save 写引擎因子库)→ 锁;其余贵但只读
_ALPHA_WRITERS = {"alpha_forge"}
# confirm=False 但写全局记忆 → 标非只读,但不锁(低危)
_MUTATES_UNGATED = {"ww_memory_write"}


def _engine_registry() -> Dict[str, Any]:
    from financial_analyst.buddy import tools as bt
    return {t.name: t for t in bt.TOOL_REGISTRY}


def build_mcp_tools() -> List[Dict[str, Any]]:
    """MCP 工具声明 list。每条:name/description/inputSchema/read_only/destructive/gated/engine。"""
    import guanlan_v2.console.tools as ct
    out: List[Dict[str, Any]] = []
    for t in ct.WW_TOOL_TABLE:
        name = t["name"]
        if name in _EXCLUDED:
            continue
        confirm = bool(t.get("confirm"))
        out.append({
            "name": name, "description": t["description"], "inputSchema": t["input_schema"],
            "read_only": (not confirm) and name not in _MUTATES_UNGATED,
            "destructive": confirm, "gated": confirm, "engine": False,
        })
    eng = _engine_registry()
    for name in _ALPHA_ZOO:
        tool = eng.get(name)
        if tool is None:
            continue
        writer = name in _ALPHA_WRITERS
        out.append({
            "name": name, "description": tool.description, "inputSchema": tool.input_schema,
            "read_only": not writer, "destructive": writer, "gated": writer, "engine": True,
        })
    return out
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_guanlan_mcp.py -k "derivation or annotations" -q`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add guanlan_v2/mcp/__init__.py guanlan_v2/mcp/tooltable.py tests/test_guanlan_mcp.py
git commit -m "feat(mcp): guanlan MCP 工具表派生(WW_TOOL_TABLE 去2 + alpha-zoo·注解·写门标记)"
```

---

### Task 2: server + 派发(写门 + to_thread + 结果转 TextContent)

**Files:**
- Create: `guanlan_v2/mcp/server.py`
- Test: `tests/test_guanlan_mcp.py`

**Interfaces:**
- Consumes: `tooltable.build_mcp_tools()`(Task 1)。
- Produces: `dispatch_tool(name: str, arguments: dict) -> list[TextContent]`(async,模块级·可单测);`build_server() -> mcp.server.Server`(Server("guanlan") + list_tools/call_tool)。

- [ ] **Step 1: 写失败测试**

追加到 `tests/test_guanlan_mcp.py`:

```python
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
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_guanlan_mcp.py -k "dispatch or build_server_name" -q`
Expected: FAIL（`ModuleNotFoundError: guanlan_v2.mcp.server`）

- [ ] **Step 3: 实现**

新建 `guanlan_v2/mcp/server.py`:

```python
# -*- coding: utf-8 -*-
"""guanlan MCP server(low-level mcp.server.Server)。镜像引擎 mcp_server,工具复用 ww_/引擎 impl。"""
from __future__ import annotations

import asyncio
import json
import os
from typing import Any, Dict, List

_DECLS: List[Dict[str, Any]] | None = None


def _decls() -> List[Dict[str, Any]]:
    global _DECLS
    if _DECLS is None:
        from guanlan_v2.mcp.tooltable import build_mcp_tools
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


async def dispatch_tool(name: str, arguments: Dict[str, Any]) -> List[Any]:
    """派发一次 MCP 工具调用 → list[TextContent]。写门 + to_thread + 诚实失败。"""
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
    return [TextContent(type="text", text=_to_text(result))]


def build_server():
    from mcp.server import Server
    from mcp.types import Tool, ToolAnnotations
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
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_guanlan_mcp.py -q`
Expected: PASS（Task 1 + Task 2 用例全绿）

- [ ] **Step 5: 提交**

```bash
git add guanlan_v2/mcp/server.py tests/test_guanlan_mcp.py
git commit -m "feat(mcp): guanlan MCP server(dispatch_tool 写门+to_thread+诚实失败 / build_server)"
```

---

### Task 3: HTTP 子应用 + stdio 入口

**Files:**
- Create: `guanlan_v2/mcp/http.py`
- Create: `guanlan_v2/mcp/__main__.py`
- Test: `tests/test_guanlan_mcp.py`

**Interfaces:**
- Consumes: `server.build_server()`(Task 2)。
- Produces: `http.build_mcp_http_app() -> Starlette`;`__main__.main()`(stdio)。

- [ ] **Step 1: 写失败测试**

追加到 `tests/test_guanlan_mcp.py`:

```python
def test_build_mcp_http_app_is_starlette():
    from guanlan_v2.mcp.http import build_mcp_http_app
    from starlette.applications import Starlette
    assert isinstance(build_mcp_http_app(), Starlette)


def test_main_module_has_main():
    import importlib
    m = importlib.import_module("guanlan_v2.mcp.__main__")
    assert callable(getattr(m, "main", None))
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_guanlan_mcp.py -k "http_app or main_module" -q`
Expected: FAIL（`ModuleNotFoundError: guanlan_v2.mcp.http`）

- [ ] **Step 3: 实现**

新建 `guanlan_v2/mcp/http.py`(镜像 `engine/financial_analyst/mcp_http.py`):

```python
# -*- coding: utf-8 -*-
"""guanlan MCP Streamable HTTP transport —— 供 server.py 挂在 /gl-mcp。镜像引擎 mcp_http。

返回的 Starlette 子应用自带 session-manager lifespan;Starlette 不会自动跑被挂子应用的
lifespan,故 guanlan_v2/server.py 需把它叠加进父 app 的 lifespan(见 Task 4)。
"""
from __future__ import annotations

import contextlib
from typing import AsyncIterator

from starlette.applications import Starlette
from starlette.routing import Mount


def build_mcp_http_app() -> Starlette:
    from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
    from guanlan_v2.mcp.server import build_server

    server = build_server()
    manager = StreamableHTTPSessionManager(app=server)

    async def handle_mcp(scope, receive, send) -> None:
        await manager.handle_request(scope, receive, send)

    @contextlib.asynccontextmanager
    async def lifespan(_app: Starlette) -> AsyncIterator[None]:
        async with manager.run():
            yield

    return Starlette(routes=[Mount("/", app=handle_mcp)], lifespan=lifespan)
```

新建 `guanlan_v2/mcp/__main__.py`(镜像 `mcp_server.main` 的 stdio):

```python
# -*- coding: utf-8 -*-
"""python -m guanlan_v2.mcp → stdio MCP(本地客户端用)。镜像引擎 mcp_server.main。"""
from __future__ import annotations

import asyncio


def main() -> None:
    from mcp.server.stdio import stdio_server
    from guanlan_v2.mcp.server import build_server

    async def _run():
        server = build_server()
        async with stdio_server() as (read_stream, write_stream):
            await server.run(read_stream, write_stream, server.create_initialization_options())

    asyncio.run(_run())


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_guanlan_mcp.py -q`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add guanlan_v2/mcp/http.py guanlan_v2/mcp/__main__.py tests/test_guanlan_mcp.py
git commit -m "feat(mcp): guanlan MCP 双传输(http.build_mcp_http_app 镜像引擎 + __main__ stdio 入口)"
```

---

### Task 4: 挂载 /gl-mcp + lifespan 叠加 + 客户端配置/README

**Files:**
- Modify: `guanlan_v2/server.py`（`create_app()` 内,`return app`(~234)之前)
- Create: `guanlan_v2/mcp/example.mcp.json`
- Create: `guanlan_v2/mcp/README.md`
- Test: `tests/test_guanlan_mcp.py`

**Interfaces:**
- Consumes: `http.build_mcp_http_app()`(Task 3)。
- Produces: `guanlan_v2.server.app` 的 Mount 集含 `/gl-mcp`(并存 `/mcp`+`/ui`)。

- [ ] **Step 1: 写失败测试**

追加到 `tests/test_guanlan_mcp.py`:

```python
def test_server_mounts_gl_mcp_alongside_engine_mcp():
    import guanlan_v2.server as s
    mounts = [getattr(r, "path", None) for r in s.app.routes if r.__class__.__name__ == "Mount"]
    assert "/gl-mcp" in mounts          # 新 guanlan MCP
    assert "/mcp" in mounts             # 引擎 MCP 仍在(不破)
    assert "/ui" in mounts              # 既有 UI 不破
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_guanlan_mcp.py -k "mounts_gl_mcp" -q`
Expected: FAIL（`/gl-mcp` 不在 mounts）

- [ ] **Step 3: 实现**

在 `guanlan_v2/server.py` `create_app()` 里,找到 `if not _UI_DIR.is_dir():`(~220 行)那一行,在它**之前**插入:

```python
    # ── guanlan 自有 MCP(挂 /gl-mcp,与引擎 /mcp 并存)──────────────
    # build_app() 已挂引擎 MCP 于 /mcp 并为其设了 app lifespan。Starlette 不会自动
    # 跑被挂子应用的 lifespan,故这里把 guanlan MCP 的 session-manager lifespan
    # **叠加**进现有 lifespan(wrap app.router.lifespan_context:先进原,再进 guanlan MCP)。
    import contextlib as _ctxlib
    from guanlan_v2.mcp.http import build_mcp_http_app as _build_gl_mcp
    _gl_mcp_app = _build_gl_mcp()
    _prev_lifespan = app.router.lifespan_context

    @_ctxlib.asynccontextmanager
    async def _composed_lifespan(_app):
        async with _prev_lifespan(_app):
            async with _gl_mcp_app.router.lifespan_context(_gl_mcp_app):
                yield

    app.router.lifespan_context = _composed_lifespan
    app.mount("/gl-mcp", _gl_mcp_app)
```

新建 `guanlan_v2/mcp/example.mcp.json`:

```json
{
  "mcpServers": {
    "guanlan-http": {
      "url": "http://127.0.0.1:9999/gl-mcp"
    },
    "guanlan-stdio": {
      "command": "python",
      "args": ["-m", "guanlan_v2.mcp"]
    }
  }
}
```

新建 `guanlan_v2/mcp/README.md`:

```markdown
# guanlan MCP server

把帷幄的 `ww_*` 工具(去 2 个 console-UI-only)+ 7 个引擎 alpha-zoo 研究工具暴露成 MCP 工具(35 个),
供外部 MCP 客户端(别的 Claude / IDE 插件 / agent)驱动 guanlan。

## 两种传输(任选)
- **HTTP**:随 9999 后端一起跑,挂在 `http://127.0.0.1:9999/gl-mcp`。
- **stdio**:`python -m guanlan_v2.mcp`(本地客户端启动它)。

`example.mcp.json` 是两种的客户端配置样例。

## 与引擎 MCP 并存
9999 上 `/mcp` 是引擎自带 MCP(20 个引擎研究/dream 工具);本 server 是 `/gl-mcp`(35 个 guanlan 工具)。两者并存、各管各的。

## 写操作默认锁
写/销毁类工具(`ww_model_train/delete/set_default`、`ww_factorlib_save`、`ww_cards_save`、
`ww_seats_decide/bind`、`ww_update_data`、`ww_news_collect`、`ww_report_run`、`ww_etf_report_run`、
`alpha_forge`)默认**调不动**——外部客户端无帷幄确认弹窗,故需在 9999 启动环境设
`GUANLAN_MCP_WRITE=1` 后重启才放行。只读工具与 `ww_memory_write` 不受锁。
`list_tools` 始终列出全部并标注 `readOnlyHint`/`destructiveHint`。

## 无真下单
guanlan 是研究平台,无券商真实下单 → MCP 不暴露下单工具(诚实)。
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_guanlan_mcp.py -q`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add guanlan_v2/server.py guanlan_v2/mcp/example.mcp.json guanlan_v2/mcp/README.md tests/test_guanlan_mcp.py
git commit -m "feat(mcp): server.py 挂 /gl-mcp + lifespan 叠加(并存引擎 /mcp)+ 客户端配置/README"
```

---

### Task 5: 真机 e2e(双传输握手 + 写门)+ 全量回归 + 还原

**Files:** 无源改动(验证任务)

- [ ] **Step 1: 全量回归**

Run: `python -m pytest -q`
Expected: 全绿(含 `tests/test_guanlan_mcp.py` 与既有 564+)。若红定位修复。

- [ ] **Step 2: 重启 9999(默认锁,不带 write flag)**

Run（杀监听 PID;若看门狗已死则手动 `Start-Process` 起,见仓库运维笔记;轮询 `/openapi.json` 到 200）：
```bash
powershell -c "(Get-NetTCPConnection -LocalPort 9999 -State Listen).OwningProcess | Select-Object -Unique | ForEach-Object { Stop-Process -Id $_ -Force }"
```

- [ ] **Step 3: 真机 — HTTP `/gl-mcp` 握手 + 列工具 + 只读真跑 + 写门拒**

Run（用 mcp 客户端 SDK 连 9999/gl-mcp）:
```bash
PYTHONPATH="G:/guanlan-v2/engine;G:/guanlan-v2" python - <<'PY'
import asyncio
from mcp.client.streamable_http import streamablehttp_client
from mcp import ClientSession
async def go():
    async with streamablehttp_client("http://127.0.0.1:9999/gl-mcp") as (r, w, _):
        async with ClientSession(r, w) as s:
            await s.initialize()
            tools = (await s.list_tools()).tools
            print("tool count:", len(tools))                      # 期望 35
            names = {t.name for t in tools}
            print("has ww_screen_factors:", "ww_screen_factors" in names, "has alpha_list:", "alpha_list" in names)
            ro = await s.call_tool("ww_capabilities", {})          # 只读真跑
            print("ww_capabilities ok, head:", str(ro.content[0].text)[:60])
            wr = await s.call_tool("ww_model_set_default", {"id": "prod"})  # 写门(未开 flag)
            print("write gated:", "写操作未启用" in str(wr.content[0].text))
asyncio.run(go())
PY
```
Expected: tool count 35;只读工具回真数据;写工具回「写操作未启用」。

- [ ] **Step 4: 真机 — stdio 列工具**

Run:
```bash
PYTHONPATH="G:/guanlan-v2/engine;G:/guanlan-v2" python - <<'PY'
import asyncio
from mcp.client.stdio import stdio_client, StdioServerParameters
from mcp import ClientSession
async def go():
    params = StdioServerParameters(command="python", args=["-m", "guanlan_v2.mcp"],
                                   env={"PYTHONPATH": "G:/guanlan-v2/engine;G:/guanlan-v2"})
    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            print("stdio tool count:", len((await s.list_tools()).tools))    # 期望 35
asyncio.run(go())
PY
```
Expected: stdio tool count 35。

- [ ] **Step 5: 真机 — 写门放行(带 flag 重启)+ 引擎 /mcp 并存核验**

1. 杀 9999,以 `GUANLAN_MCP_WRITE=1` 重启(`$env:GUANLAN_MCP_WRITE='1'` 后 `Start-Process ... python guanlan_v2\server.py`)。
2. 重跑 Step 3 的写工具调用(`ww_model_set_default {"id":"prod"}` 幂等清除)→ 期望**放行**(不再回「写操作未启用」)。
3. 引擎 `/mcp` 并存:`import guanlan_v2.server` 后 `[r.path for r in app.routes if Mount]` 含 `/mcp`+`/gl-mcp`+`/ui`(或对 9999/mcp 另起一个 streamablehttp client 握手列出 20 引擎工具)。
Expected: 写门随 flag 放行;引擎 `/mcp` 仍在、guanlan `/gl-mcp` 并存。

- [ ] **Step 6: 还原现场**

把 9999 还原成默认(不带 `GUANLAN_MCP_WRITE`,即只读)重启,确认无遗留写态;清掉测试中可能设的默认变体指针(`ww_model_set_default {"id":"prod"}` 或删 `_default.json`)。汇报:35 工具双传输均列出、写门默认拒↔开 flag 放行、引擎 /mcp 并存、全量绿。

- [ ] **Step 7: 提交(若 Step 1 有顺带修复)**

```bash
git add -A
git commit -m "test(mcp): guanlan MCP 双传输真机 e2e + 写门 + 引擎 /mcp 并存证据"
```

---

## 自查锚

- 工具总数 35 = `len(WW_TOOL_TABLE)`(30) − 2(`ww_plan_update`/`ww_show_page`)+ 7 alpha-zoo;守护测试 `tests/test_guanlan_mcp.py::test_build_mcp_tools_derivation`。
- 写门集 12 = 11 个 `confirm=True` ww_ + `alpha_forge`;`test_build_mcp_tools_annotations_and_gate` + `test_dispatch_write_gate`。
- 挂载并存锚:`tests/test_guanlan_mcp.py::test_server_mounts_gl_mcp_alongside_engine_mcp`(`/gl-mcp`+`/mcp`+`/ui`)。
- 红线:不动引擎 `/mcp`/不动 ww_ 算法/写默认锁/诚实失败/无真下单/to_thread 不堵。
