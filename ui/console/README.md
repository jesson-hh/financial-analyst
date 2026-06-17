# 观澜 · 帷幄 — 单核心 Agent 总控台(一期骨架)

> 交付:2026-06-12  
> 设计原文:`docs/superpowers/specs/2026-06-11-weiwo-console-design.md`  
> 实现计划:`docs/superpowers/plans/2026-06-11-weiwo-console-phase1.md`

## 模块定位

帷幄是观澜平台的**唯一统帅 agent 入口**。一个对话框操控全平台:选股、因子分析、回测、落子研判一句话下令,右栏自动滑出真实页面结果(不新建渲染器,直接嵌入既有选股/工作流页的同源 iframe)。

架构是**统帅—哨兵双层**:帷幄统帅负责跨模块编排,落子哨兵(`ui/seats/`)保留自己的专职 agent,两者不合并。其余各页散落的 agent 窗口全局隐藏(`?legacy=1` 找回)。

布局:三栏 grid(二级 masthead 已于 2026-06-11 裁掉——与顶部 nav 重复;⇋ 布局切换移入工作台头部,「● 运筹中」并入左栏脚注,`?v=20260613e`)  
- 右栏关闭时:`264px 1fr`(纯对话)  
- 右栏打开后工作台优先:`264px 460px 1fr`  
- ⇋ 对话优先:`264px 1fr 560px`

## 文件清单

| 文件 | 职责 |
|------|------|
| `观澜 · 帷幄.html` | 模板:React UMD + `guanlan-bus.js?v=3` + `guanlan-nav.js` + `GUANLAN_BACKEND` 同源开关(file:// 直开诚实报错) |
| `console-data.jsx` | 事件流客户端:`EventSource`(原生,自动重连)+ `wwApply` reducer + 会话/计划/产物状态;导出 `window.WW`(API/TOOL_CN/PAGES/initState/apply/connect/send/confirm/sessions/newSession) |
| `console-rail.jsx` | 左栏:`WwRail` — 「新对话」按钮 + 任务计划(✓/▶/○ 三态,▶ 脉冲动画)+ 会话列表(当前朱砂竖线)+ 已连流/档案件数脚注 |
| `console-thread.jsx` | 中栏:`WwThread` — 事件流按序渲染(用户消息右对齐卡 / agent 消息「觀」印章头像 / 工具调用卡摘要行+展开摘要 / 确认门「准/驳回」面板);`WwToolCard` 工具卡子组件 |
| `console-bench.jsx` | 右栏:`WwBench` — tab(仅激活过的页)+ 同源 iframe 原样嵌入 + `?embed=1&_t=ts` 强制重载 + `scale(panelWidth/1280)` 自适应缩放 + ⌖钉住 + ↗独立页 + ✕收起 |
| `console-app.jsx` | 主壳:`WeiwoApp` — `useReducer` 管全局状态、布局三栏 grid、会话切换/新建、`onSend`/`onConfirm` |

所有文件当前 `?v=20260612a`。

## 后端依赖

`guanlan_v2/console/` 三个模块:

- **`store.py`** — `ConsoleStore`:每会话一个追加式 jsonl 事件日志(`var/console/sessions/<sid>/events.jsonl` + `meta.json`);`set_plan()` 写 meta.json 的 `plan` 字段。
- **`tools.py`** — 14 个工具 impl(`factor_analyze_impl`/`backtest_impl`/`screen_impl`/`seats_decide_impl`/`cards_query_impl`/`reports_query_impl`/`plan_update_impl` + 7 个 buddy 研究工具);`register_console_tools()` 幂等追加进引擎 `TOOL_REGISTRY`。工具结果统一包含 `{ok, content, artifact, raw}`。
- **`api.py`** — FastAPI 路由:`POST /console/send`、`GET /console/stream/{sid}`(SSE)、`GET/POST /console/sessions`、`POST /console/confirm`;buddy `BuddyAgent` 扩展 `profile='console'`,系统提示含纪律五条(计划先行/数字来自工具/zoo DSL/中文简洁/因子目录 id)。

### 事件七型协议

```jsonc
{"type": "user_msg",    "text": "..."}
{"type": "agent_delta", "msg_id": "m1", "delta": "..."}        // 流式文字
{"type": "tool_call",   "call_id": "c1", "tool": "ww_screen_run", "args": {}}
{"type": "tool_result", "call_id": "c1", "ok": true, "summary": "选股完成: 入选 20 只",
  "artifact": {"kind": "screen_result", "page": "screen", "channel": "screen", "payload": {}, "ref": null}}
{"type": "plan_update", "todos": [{"id": "t1", "text": "...", "status": "done"}]}
{"type": "task_update", "task_id": "bg1", "status": "done", "ok": true, "note": "..."}
{"type": "confirm_request", "turn_id": "...", "tool": "ww_seats_decide", "args": {}}
```

### SSE snapshot-then-live 协议

连接后先推一条 `event: snapshot`(含 `meta` + 全量 `events` 数组),再逐条推 `event: ev`。`EventSource` 内置自动重连:断线重连即重收 snapshot,前端 `wwApply` reducer 重放全量事件恢复到一致状态,刷新无损。SSE 每 15s 发 `: heartbeat` 注释心跳保活,分段直播总超时 300s(配合看门狗重启)。

## WW_PAGES 注册表与 handoff 驱动协议

`console-data.jsx` 中 `WW_PAGES` 登记当前可激活的两个工作台页:

| key | label | file | channel |
|-----|-------|------|---------|
| `screen` | 选股 | `../screen/观澜 · 选股.html` | `screen` |
| `factor` | 工作流 | `../factor/观澜 · AI 工作流.html` | `workflow` |

**handoff 驱动流程**:

1. agent 工具返回 `artifact.page`(如 `"screen"`)和 `artifact.payload`(如 `{cfg:{factors,pool,blend,topN}}`)
2. `WwBench` 收到新 artifact 后调 `GL.handoff(channel, payload)`,再把 iframe src 重置为 `<page.file>?embed=1&_t=<now>()`
3. iframe 页面 mount 时调 `GL.take(channel)` 取到 payload,按 cfg 真实重算
4. 选股页 `take('screen')` 收 `{cfg:{factors,pool,blend,topN}}` 后调 `refresh()` 真跑选股——cfg 不自动触发重算,需等下一个 tick 或用户确认

## 工具白名单 CONSOLE_ALLOWED(14 个)

`tools.py` 末尾 `CONSOLE_ALLOWED` 集合:

| 分组 | 工具名 |
|------|--------|
| 帷幄专属(7) | `ww_plan_update` `ww_factor_analyze` `ww_backtest` `ww_screen_run` `ww_seats_decide`(需确认) `ww_cards_query` `ww_reports_query` |
| buddy 研究工具(7) | `quote_lookup` `realtime_quote` `stock_brief` `financials` `news_query` `wisdom_search` `quant_reports` |

`ww_seats_decide` 在工具注册时 `confirm_required=True`,触发时前端弹确认门卡(`confirm_request` 事件),用户点「准」后才落盘 `var/seats_decisions.jsonl`。

## 已验收清单(2026-06-12,controller 浏览器真点击)

1. **空态两栏/SSE 已连流**:打开页面,左栏显示「暂无任务」+ 已连流状态,中栏显示空态引导文字
2. **一句话全链**:输入「验证动量因子 rank(-delta(close,20)),回测完选股」→ agent 自动拆 2 项计划 → `ww_factor_analyze` 返回真 RankIC → `ww_screen_run` 返回真 top10 → 计划全勾绿 → `task_update done ok=true`
3. **右栏自动滑出双 tab**:产物到来,选股/工作流 tab 按序出现,各显真数据
4. **刷新恢复**:页面刷新 → EventSource 重连 → snapshot 重放 → iframe 按 agent cfg 重算,出与汇报一致的 top10
5. **✕ 收起回纯对话**:右栏收起后布局变 `264px 1fr`,下次产物到来自动滑出
6. **三态 URL**:独立 URL(agent 窗口显示)/ `?legacy=1`(找回全部隐藏的 agent 控件)/ `?embed=1`(去 nav 去页头,iframe 嵌入态)
7. **cfg 驱动**:pool/blend/topN 参数通过 handoff payload 传入选股页,真生效于重算

## 二期(2026-06-13)

### U 批 — 对话 UI 全盘移植 chat 页

从 chat 页原样移植完整对话 UI,数据源换 console 事件流,盯盘件不迁。

**移植组件:**

| 组件 | 职责 |
|------|------|
| `UserBubble` | 用户消息黑卡,右对齐 |
| `觀` 头像 | agent 消息印章头像 |
| `renderChatMarkdown` | 全套 markdown 渲染(标题 H1–H6 / 表格 / 代码块 / 粗斜体),经 `window.WwMd` 跨文件共享渲染器 |
| `ToolChain` | 工具调用折叠组三态(running/done/fail) + 失败态 |
| `ConfirmModal` | y/n 确认门面板 |
| 圆角输入坞 | Shift+Enter 换行 / 印章「令」发送 |
| `WwDrawer` | 研报抽屉 540px 侧滑(读存量研报全文) |

**事件流推导层 `WW.deriveItems`**:两 `user_msg` 之间的 `tool_call`/`tool_result` 折成 `ToolChain`,连续 `agent_delta` 拼接成完整 `answer` 项;`window.WwMd` 跨 jsx 文件共享 markdown 渲染器(避免多次实例化)。

当前 `?v`:thread/drawer/app=`20260613b`→`20260613c`(D 批后 rail/bench/app=`20260613c`)。

### A 批 — 研报后台跑道

**工具 `ww_report_run`**:带确认门(`confirm_required=True`)→ 发往 `se.background` 信封 → API 后台跑道 `_run_report_bg`。

**跑道细节:**

- `executor` 线程跑子进程,`PYTHONPATH` 注入 engine fork(已实证优先加载)
- 进度轮询读 `out/{CODE}_progress.json`,经 `ts >= t0` 过滤陈旧残留(避免上次残留进度被误读)
- 同 code `_bg_inflight` 去重(防重复提交)
- 完成时 emit `tool_result`(artifact `report_md`) + `task_update done`,自动调 `/archive/put` 入档(经 `to_thread` 异步,避免协程内同步自 HTTP 堵死 event loop 触发看门狗杀进程)

**已修 Critical(审查阶段)**:入档曾是协程内同步自 HTTP 调用,会堵死 asyncio event loop,触发看门狗杀进程——现改为 `to_thread`。

### D 批 — 导航收敛两门面

- 顶栏导航只剩 **帷幄 + 席位·落子**,home 重定向帷幄
- 经验卡、工作流、选股、图谱从导航条摘除(代码/直链保留,`?legacy=1` 找回)
- 新工具 **`ww_show_page`**:agent 口头调出工作台右栏视图(artifact `page_view`)
- 新工具 **`ww_cards_save`**:带确认门,POST /cards 回写经验卡
- cards 页注入 `WW_EMBED` 旗(`?embed=1` 隐 Header/grid)+ `WW_LEGACY` 旗(`?legacy=1` 找回 ChatRefine,`?v=20260613a`)
- graph 页注入 WW_EMBED(`?embed=1` 隐顶栏,`?v=4`)
- `WW_PAGES` 注册表新增 `cards`/`graph` 两 channel(channel validation / null 守卫)

### B 批 — condenser + memory

- **condenser**:对话超 36 条/24000 字时 `agent.compact()` → 发 `condensation` 事件(带 `summary`);空摘要不发
- **memory.md**(`var/console/memory.md`):行式追加 `- [日期] 文本`;工具 `ww_memory_write`/`ww_memory_read`;每轮 `[帷幄记忆]` 块注入 agent system prompt
- `store.merge_meta`:合并更新 meta 字段而非全量覆盖
- `meta.bg`:后台任务信息留档 meta

### C 批 — 体验件

- **左栏后台任务分区**:按 `kind` 事件字段聚合 `bgTasks`,显示进度条
- **右栏左缘拖宽把手**:分隔条拖动,存 `localStorage` 键 `guanlan:ww:benchw`,clamp 范围 480 ~ (innerWidth - 700)

### 功能呼出器 ◫(2026-06-11 追加,?v=20260613d)

输入坞左侧 `◫` 按钮(`WwLauncher`,console-thread.jsx):点开向上弹菜单,列 `WW_PAGES` 四功能(选股/工作流/经验卡/图谱)+ 「● 已开/○」状态,点选手动呼出右栏对应页——与对 agent 说「调出选股」等价,但不占一轮对话。

**架构要点**:手动呼出走 app 级 `manual` useState(console-app.jsx `openPage`),**独立于事件流**——`activated` 渲染时取「事件流激活 ∪ 手动呼出」,SSE 快照重放(每 300s 分段重连)不会冲掉手动开的页;`benchFocus={page,n}` 协议传入 `WwBench` 强制切 tab(覆盖 ⌖钉住——钉住只挡 artifact 自动跟随,不挡用户显式点选);切会话时 manual 清空。手动呼出的页 iframe 走 `?embed=1` 无 payload 兜底(页面渲自身默认数据)。

### 顶栏任务芯片 + 会话管理(2026-06-11 追加,?v=20260613g)

- ~~顶栏「●任务」芯片 portal 进 nav~~ → **改为会话栏 `WwSessBar`(?v=20260613i/j,用户拍板:每个对话都有不同的顶栏)**:对话区(中栏)顶部 38px 一条,随会话切换各不相同——左:会话名 + ✎ 行内改名(Enter 提交,`metaPatch` action 即时回显不等快照);右:busy 时「●+当前计划项」摘要 + 「●任务」芯片(空闲灰、运行中朱砂带计数),点开下拉面板:当前对话状态 + 后台任务卡(进度条 / done 带「查看 ↗」→ 从事件流找回 report_md 开抽屉)。nav 上不再放全局芯片;后台任务从左栏迁来,左栏不再渲染。
- **会话改名/分组/状态**(console-rail.jsx):行悬停出 ✎改名 / ⊟分组,行内 input,Enter 提交 Escape 取消,经 `PATCH /console/sessions/{sid}`(白名单 title/group,group 空串=取消分组);左栏按 `meta.group` 归并渲组头(未分组平铺在前);行首状态点 = sessions 列表下发的 `running`(进程内实时态,8s 轻轮询保鲜)∪ 当前会话 busy。
- **用户消息靠左**(console-thread.jsx):UserBubble 黑卡从右对齐改 `flex-start`(用户拍板)。

### 会话级工作台隔离(2026-06-11,?v=20260613h / workflow.jsx?v=74)

每个帷幄会话一套独立工作台,显式隔离边界:

- **本来就隔离**:agent 对话史(每 sid 一实例)/事件日志(每会话 jsonl)/计划/后台任务/右栏激活页(快照重放推导)/顶栏任务面板(标题显示会话名);后端 workflow 端点无状态,多会话并行跑互不影响。
- **修掉的串台点**:工作流页「上次会话画布」(`guanlan:wf:last:v1`)与「报告缓存」(`guanlan:wf:reports:v1`)原是全局 localStorage——会话 B 空手呼出工作流会吃到 A(或独立页)的残留图。现 WwBench 给所有 iframe src 加 `ws=<sid>`,workflow.jsx 在 embed+ws 态把两个 key 加 `:<sid>` 后缀;独立打开(无 ws)行为不变。已实证:种全局残留图→会话内呼出工作流不吃残留、渲染本会话默认画布。
- **顺带修**:bench 挂载(=切会话重挂)时,每个激活页各驱本会话该页**最后一个**产物(原先只驱全局最后一个,切回会话其余 tab 是默认态)。
- **设计上共享(非污染)**:工作流保存库(wf:list)/报告库/经验卡/因子库/研究档案——全平台共用的「库」,与落子同源,刻意全局。
- handoff 信箱 `GL.take()` 读后即焚,不跨会话残留。

### 隔离加固 H1-H4(2026-06-12,体检 12 缺口收口;计划=docs/superpowers/plans/2026-06-11-weiwo-session-isolation-hardening.md)

- **H1 前端**:GL.handoff/take/peek 加可选 ws 参(键 `guanlan:handoff:<ch>[:<ws>]`,bus ?v=4 全 html 已 bump)——bench 产端带 sid、screen/workflow/cards 消费端带 WW_WS(不再依赖 embed),多 tab/切会话信箱互踩根治;cards 的 openWorkflow 交棒+导航透传 embed/ws(审查抓的 Important);attach 清研报抽屉;sid 改 sessionStorage 优先(每 tab 自己的会话)+ localStorage 兜底;cards MEM_KEY 按 ws 隔离;↗ 独立打开带 ws(进入本会话工作区)。
- **H2 记忆**:_reseed 用最后一条 condensation 摘要打底(重启不丢长程记忆,且回退收触发压缩那轮的提问);LRU 逐出跳过 running 会话;**记忆双层**——ww_memory_write/read 加 scope(global=偏好/session=本会话笔记落 sessions/<sid>/notes.md),每轮注入 `[帷幄记忆·全局]+[本会话笔记]` 两段,append 持锁;顺手堵 `DELETE /sessions/..` 路径穿越(_SID_RE)。
- **H3 后台韧性**:meta.bg 起跑即写(merge_meta_sub 锁内嵌套合并)+ 启动扫描把上次中断的 running 任务标 error 并补事件(逐会话逐条目容错);同票撞车改**搭车**——B 会话收 running「另一会话生成中」,完成后 A/B 各收 done+研报卡;删会话挡 in-flight 研报(发起者/搭车者均拒)。
- **H4 加固**:store 读路径持锁 + Windows replace/rmtree 重试(delete 失败返回诚实信封);引擎 profile_tool_names 全 profile 路径剔除 ww_*(堵已退役 chat 页 /run 直链越权;**边界**:显式传 tools 白名单仍可解析 ww_——localhost 单机信任缝,console 自身走 CONSOLE_ALLOWED 不受影响)。
- pytest 152 绿;挂账:meta.bg 终态条目裁剪、delete 与 _spawn_bg 起跑微竞窗、seats 进 _SHOW_PAGES(三期)、跨进程 pid 锁、_emit 吞吐。

## 事件契约新增(二期)

一期七型协议不变,二期扩展:

```jsonc
// task_update 可选字段扩展(kind 分类 / code 资产代码 / progress 0-1)
{"type": "task_update", "task_id": "bg1", "status": "running", "kind": "report", "code": "SH600519", "progress": 0.6, "note": "第3/5步"}

// artifact 新增两 kind
{"kind": "report_md", "path": "out/SH600519_2026-06-13.md", "code": "SH600519", "name": "宁德时代深度研报"}
// → 不进 bench 右栏;中栏渲 ReportCard + 抽屉

{"kind": "page_view", "page": "cards"}
// → 驱动右栏切到对应 WW_PAGES tab

// condensation 事件(新增)
{"type": "condensation", "replaces": [1, 36], "summary": "..."}

// busy 翻转:只认无 kind 的 task_update(带 kind 的是后台任务不翻 busy 标志)
```

## 工具白名单 CONSOLE_ALLOWED(19 个,二期新增 5)

一期 14 个基础上增加:

| 新增工具 | 批次 |
|----------|------|
| `ww_report_run` | A 批(确认门 + 后台跑道) |
| `ww_show_page` | D 批(口头调出右栏视图) |
| `ww_cards_save` | D 批(确认门 + 回写经验卡) |
| `ww_memory_write` | B 批 |
| `ww_memory_read` | B 批 |

## 已验收清单(2026-06-12 一期 + 2026-06-13 二期)

一期验收(见上方「已验收清单」)全部沿袭。二期新增:

8. **对话 UI 水准**:帷幄对话观感 = chat 页(黑卡/觀头像/markdown 表格+标题/工具链折叠/确认门)
9. **研报跑道**:「给宁德写份研报」→ 确认门 → 左栏进度条(A 批 bgTasks) → 期间可续聊 → 跑完通知 → WwDrawer 抽屉读全文 → 自动入 GL 档案
10. **导航两门面**:顶栏只剩帷幄 + 落子两按钮;经验卡/工作流/选股/图谱靠帷幄 `ww_show_page` 或直链访问
11. **长对话不失忆**:35 条后 condenser 压缩;memory.md 偏好跨会话持久
12. **右栏拖宽**:左缘把手拖动,宽度跨刷新保留

## 已知限制(I2 留档,三期待解)

- **后台研报孤儿进程**:9999 重启时,后台研报子进程变孤儿,事件流停在 `running` 状态不推进。三期补启动扫描:server 启动时扫描 `_bg_inflight`,对 orphan 任务注入 `task_update error`(方案已设计,待实现)。
- **CONSOLE_ALLOWED 19 工具**:随平台功能增长工具数会继续扩展,选择准确率需持续观测。
- **同名工具并发配对**:tool_call/result 按 `call_id` 配对已实现,历史遗留文档里描述的「按工具名就近配对」旧逻辑已不适用。

## 运维坑

- **帷幄必须经 9999 同源服务**:`GUANLAN_BACKEND` 设为 `location.origin`,`file://` 直开显「需经服务打开」提示;SSE 和工具端点都在后端,不可绕过
- **Chrome 扩展自动化卡 SSE**:帷幄页挂着 SSE 时,Chrome 扩展类自动化把页面视为「加载中」(等待 `document_idle`),截图/type 等操作会卡死。自动化验证前先 `window.stop()` 掐断 EventSource 流再截图;EventSource 会自动重连,无损
- **文本注入**:帷幄输入框是 React 受控组件,`element.value =` 后必须触发原生 `input` 事件才能更新 React state;用 `Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value').set.call(el, text); el.dispatchEvent(new Event('input', {bubbles:true}))`
- **重启 9999**:改后端 Python 须杀监听 PID(等看门狗 ~10s 拉新),改 JSX 只需 bump `?v=` 用 Edit 工具非 sed
