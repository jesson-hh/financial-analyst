# guanlan MCP server — 设计

> 日期:2026-06-30
> 目标:给 guanlan 一个**真正的 MCP server**,把帷幄的 `ww_*` 工具(+ 已放行的引擎 alpha-zoo 研究工具)暴露成 MCP 工具,让**外部 MCP 客户端**(别的 Claude / IDE 插件 / agent)也能驱动 guanlan。双传输(HTTP 挂 9999/mcp + stdio),复用现成 impl,写操作默认锁。
> 不做:真券商下单(guanlan 无此能力,绝不暴露不存在的能力);新业务逻辑(纯包现成 `ww_*`)。

---

## 0. 现状(为何从零建)

- **更正(实测 `app.routes` = `['/mcp','/ui']`)**:guanlan `create_app()` 调引擎 `build_app()`,而 `build_app()` **已把引擎 MCP 挂在 `/mcp`**(20 个引擎研究/dream 工具)+ 组合好其 lifespan。即 **9999/mcp 已是活的引擎 MCP**——但它**不含 guanlan 选股/落子/因子库/工作流/工坊**。
- 所以本设计 = 建 **guanlan 自己的 MCP**,挂在**独立路径 `/gl-mcp`**(不动引擎 `/mcp`),把 lifespan **组合**进已有的(`build_app` 已为引擎 `/mcp` 设了 lifespan,需 wrap `app.router.lifespan_context` 叠加,不是覆盖)。
- 引擎 MCP(`mcp_server.py` stdio + `mcp_http.py` HTTP,`buddy/server.py:376-393` 的挂载+lifespan 组合)是**成熟可镜像的蓝本**。
- 帷幄已有 30 个 `ww_*`(`WW_TOOL_TABLE`)+ 7 个引擎 alpha-zoo 工具在 `CONSOLE_ALLOWED` 里;impl 形 `{ok,content,artifact,raw}`,经 `_self_post/_self_get` 打 9999。
- 帷幄已有 30 个 `ww_*`(`WW_TOOL_TABLE`)+ 7 个引擎 alpha-zoo 工具(`_ALLOWED_ENGINE_TOOLS` 的 B 类)在 `CONSOLE_ALLOWED` 里;impl 形 `{ok,content,artifact,raw}`,经 `_self_post/_self_get` 打 9999。

---

## 1. 架构(镜像引擎 `mcp_server`+`mcp_http` 模式)

新包 `guanlan_v2/mcp/`:

- **`tooltable.py`** — `build_mcp_tools() -> list[dict]`:从 `WW_TOOL_TABLE`(派生)+ alpha-zoo 名单(引擎 `TOOL_REGISTRY` 取 description/schema)生成 MCP 工具声明。每条:`name`、`description`(复用 ww/引擎描述)、`inputSchema`(复用 ww `input_schema` / 引擎 `input_schema`)、`annotations`(见 §3)。
- **`server.py`** — `build_server() -> mcp.server.Server`:`Server("guanlan")` + `@server.list_tools()`(返回工具声明)+ `@server.call_tool()`(派发,见 §2)。
- **`http.py`** — `build_mcp_http_app() -> Starlette`:镜像引擎 `mcp_http.build_mcp_http_app`(`StreamableHTTPSessionManager(app=build_server())` + lifespan)。
- **`__main__.py`** — `python -m guanlan_v2.mcp` 跑 stdio(镜像引擎 `mcp_server.main`:`stdio_server()` + `server.run(...)`)。
- **挂载**:`guanlan_v2/server.py` `create_app()` 在 `return app` 前 `app.mount("/gl-mcp", _gl_mcp_app)`(独立路径,不动引擎 `/mcp`),并**叠加 lifespan**:`build_app()` 已给 app 设了引擎 MCP 的 lifespan,故 wrap `app.router.lifespan_context` = 先进原 lifespan 再进 guanlan MCP 的 `_gl_mcp_app.router.lifespan_context`(plan 期落代码,镜像 `buddy/server.py:379-393`)。
- **`.mcp.json` 示例 + `guanlan_v2/mcp/README.md`**:HTTP(`url: http://127.0.0.1:9999/gl-mcp`)与 stdio(`command: python -m guanlan_v2.mcp`)两种客户端配置。注明引擎 `/mcp`(20 引擎工具)与 guanlan `/gl-mcp`(35 工具)并存、各管各的。

---

## 2. 工具派发(`call_tool`)— 复用 impl + to_thread + 写门

`call_tool(name, arguments)`:
1. 查 name → 对应 impl(`ww_*` 用 `WW_TOOL_TABLE` 的 `impl`;alpha-zoo 用引擎 `TOOL_REGISTRY[name].run`)。未知名 → MCP error(诚实)。
2. **写门**:若该工具是写/销毁类(见 §3 判定)且 `os.environ.get("GUANLAN_MCP_WRITE") != "1"` → 直接返回诚实拒绝文本(`"写操作未启用:在 9999 启动环境设 GUANLAN_MCP_WRITE=1 后重启"`),不执行。
3. 执行:`result = await asyncio.to_thread(impl, **arguments)`(**铁律**:impl 内含同步自 HTTP `_self_post`,协程里必须 to_thread,否则堵事件循环→看门狗杀 9999)。
4. 转换:`ww_` impl 回 `{ok,content,artifact}` → MCP `TextContent(text=content)`;`ok==False` → 标 `isError=True`(诚实失败,不装成功)。引擎 `run()` 回 `ToolResult` → 取 `.content`,`is_error` → isError。
5. ContextVar:MCP 无 console 会话 → 不设 `CTX_SID/CTX_STORE`;依赖会话的 impl 自身已诚实降级(`ww_memory_write/read` session 作用域无 sid 时退全局/诚实空——见现状代码)。

---

## 3. 范围 + 注解 + 写门判定

**收录(35 工具)= 30 `ww_*` 去 2 个纯 console-UI 工具 + 7 alpha-zoo:**
- **去除(MCP 无意义,诚实不暴露)**:`ww_plan_update`(改 console 会话计划,硬依赖 CTX_SID/CTX_STORE)、`ww_show_page`(往 console UI 右栏弹页面,MCP 无 UI)。README 注明去除原因。
- **保留 28 `ww_*`**:含 `ww_memory_write/read`(session 作用域无 sid 时诚实降级全局)、`ww_capabilities/ww_endpoints`(只读自描述,助客户端发现能力)。
- **7 alpha-zoo**:`alpha_list/alpha_show/alpha_compare/alpha_bench/event_report/alpha_forge/factor_report`。

**MCP 工具注解(从 ww `confirm` 字段 + 一处特例派生)**:
- `destructiveHint = (confirm == True)` —— 写/销毁类。
- `readOnlyHint = (confirm == False) and name != "ww_memory_write"` —— `ww_memory_write` 是唯一 `confirm=False` 的写(改全局记忆),标非只读但**不锁**(低危,沿用其 confirm=False 风险定级)。
- alpha-zoo:引擎 `confirm_required` 是「**贵**(聊天里要确认)」不等于「写」。`alpha_list/alpha_show/alpha_compare/alpha_bench/event_report/factor_report` 全是**只读计算/报告、不落盘** → `readOnlyHint=True`、**不锁**;唯 **`alpha_forge`**(`save` 可写引擎因子库)→ `destructiveHint=True`、**锁**(整体锁,即便 save=false 也锁,安全默认)。

**写门(`GUANLAN_MCP_WRITE`)= 所有 `destructiveHint=True` 工具 = `confirm=True` 的 11 个 `ww_` + `alpha_forge`(共 12 个)**。默认锁;`list_tools` 仍列出(带 destructiveHint),只是默认调不动 → 诚实拒。其余(只读 + `ww_memory_write`)不受门。`ww_memory_write` 单独标 `readOnlyHint=False`(诚实:它写全局记忆)但**不锁**(低危,沿用 confirm=False)。

---

## 4. 安全 / 诚实红线

- **写默认锁**:外部客户端无帷幄确认弹窗 → `GUANLAN_MCP_WRITE=1` 才放行写/销毁;默认只读。
- **注解真实**:readOnly/destructive 据实标(`ww_memory_write` 不冒充只读)。
- **失败显形**:impl `ok:False` → MCP `isError`;未知工具/写门拒 → 诚实文本,绝不装成功。
- **不暴露不存在的能力**:无真券商下单 → 无下单工具;去除 console-UI-only 工具。
- **描述复用 ww_/引擎原文**(不另编)。

---

## 5. 测试

**单元(`tests/test_guanlan_mcp.py`)**
- 工具表派生:`build_mcp_tools()` 的 ww_ 名集 == `{t['name'] for t in WW_TOOL_TABLE} - {ww_plan_update, ww_show_page}`;含 7 alpha-zoo;总数 35(防漂移)。
- 注解:每个 `confirm=True` 工具 destructiveHint;`ww_memory_write` readOnlyHint=False(不冒充只读);只读工具 readOnlyHint=True。
- 写门:mock 一个写工具,`GUANLAN_MCP_WRITE` 未设 → `call_tool` 回诚实拒(不调 impl);设 `=1` → 调 impl(monkeypatch 验被调)。
- `call_tool` 包装:只读工具(mock impl 回 `{ok:True,content:"x"}`)→ MCP TextContent "x";`ok:False` → isError;经 `asyncio.to_thread`(不堵)。
- HTTP app 可构建:`build_mcp_http_app()` 返 Starlette,不抛。

**真机(9999 + stdio)**
- `9999/gl-mcp` MCP 握手 `list_tools` → 列全 35;`python -m guanlan_v2.mcp` stdio 同样列全 35;引擎 `/mcp` 仍在(并存不破)。
- 只读工具真跑回真数据(如 `ww_capabilities` 或 `ww_screen_factors`)。
- 写门:默认调写工具(如 `ww_model_set_default`)→ 诚实拒;以 `GUANLAN_MCP_WRITE=1` 重启后 → 放行(用 throwaway 验,non-destructive 还原)。
- 全量 pytest 绿;`server.py` 挂 `/gl-mcp` + lifespan 叠加后既有路由/契约/引擎 `/mcp` 不破(`app.routes` 仍含 `/mcp`+`/ui`+新 `/gl-mcp`)。

---

## 6. 红线汇总

- 纯包现成 `ww_*`/引擎 impl,**不写新业务逻辑、不动选股/落子/工坊算法**。
- 写默认锁(env flag),注解据实,失败显形,无真下单,去除不可用工具。
- 工具集从 `WW_TOOL_TABLE` 派生(守护测试防漂移),改帷幄工具自动同步 MCP。
- `server.py` 仅加 `/mcp` 挂载 + lifespan 组合,不动既有路由。
