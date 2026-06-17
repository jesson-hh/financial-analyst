# chat — 对话 · 研报(A1)

> **2026-06-13 退役注记**:本页已从导航摘除,对话与研报能力全部并入帷幄(spec §2 第五/七轮:研报后台跑道+对话 UI 整体移植);盯盘类(自选/提醒/行情轮询/雪球)不迁——落子哨兵地盘,引擎 /alerts 端点保留。直链仍可用,代码保留(?legacy 哲学)。

| 项 | 值 |
|----|----|
| 页面 | 观澜 · 交互原型.html |
| 入口组件 | `ObservatoryApp`(app.jsx,~196KB) |
| 配套 | agent-adapter.jsx(agent 抽象 + /run SSE 解析) |
| 后端 | /run /quotes /report /comments /concepts /upload |
| 闭环位置 | 产出 research(研报/素材),喂给 factor |

## 职责
自然语言 A 股研究助手:
- 流式回复 + **多步工具链可视化**("研究纸带"纵向展开,朱砂印章序号 + 计时)。
- 三档权限模式(default/safe/auto)+ Claude-Code 风格 y/n/a 确认。
- **深度研报抽屉**(`run_report` 后台进度 + 全文)。
- 多会话 + localStorage 持久化(`guanlan:state:v2`,reducer 状态机)。
- ⌘K 工具面板(工具按耗时分档)、斜杠命令(`/mode` `/llm` `/reset` …)。
- 后台盯盘触发 toast。
- Composer 三按钮:@引用 / ⌗板块(/concepts) / ⊟上传(/upload)。

## 后端端点
| 端点 | 用途 |
|------|------|
| `/run`(SSE) | agent 对话主流(plan/tool_start/tool_done/answer_progress/done),经 `agent-adapter.jsx` 的 `GuanlanAgent.run()`。 |
| `/quotes` | 实时行情(腾讯源)。 |
| `/report` | run_report 深度研报。 |
| `/comments` | 雪球评论/情绪(需登录 cookie)。 |
| `/concepts` | 板块/概念联想。 |
| `/upload` | 文件上传。 |

## 数据
引擎工具真数据:实时行情走腾讯,EOD/因子/新闻走 stock_data(经 `get_data_paths`)。

## 状态 / 开放项

**已接真引擎(chat 模块轮,2026-06-04)** — 页面默认连同源真后端,显示真数据,不再是 mock 设计稿。

- **接线方式**:`观澜 · 交互原型.html` 把 `window.GUANLAN_BACKEND` 默认设为 `location.origin`(http(s) 时)。本页由 `guanlan_v2/server.py` 在 9999 同源服务 → 自动连真引擎;`file://` 直开留 `null`,agent-adapter 回退内置 mock 预览。改过的 jsx 已 bump `?v=2`。
- **去 mock 兜底**:`agent-adapter.jsx` 的 `run()` 连了后端后,`_runBackend` 失败直接走 `onError`(状态栏「⚠ LLM 失败」),不再偷偷回退 mock 掩盖故障。
- **雪球未登录提醒**:`StockBriefCard` 的 `xqErr` 新增分支——`/comments` 返回风控/登录页(`_waf_`/`renderData`/`text/html`)时提示「雪球未登录或被风控拦截,需登录后重试」。
- 后端端点 + SSE 形状与引擎 `buddy/server.py` **逐一核对对齐**(/run /quotes /models /data/status /conversations /comments …),无需改前端解析,也未给引擎加能力。

**控制端验证证据**(preview_eval,引擎 `engine source: G:\fa-watch-wt\src`):
- `/health` ok(41 工具);自选墙真行情 宁德 415.56 / 茅台 1281.08 / 比亚迪 94.04(mock 是 325.10/1684/281 → 确证非 mock);指数条 上证 4071 / 深成 15665 / 创业 4088;盘口「交易中 10:15」;7 条历史会话从后端磁盘加载;状态栏 `backend · 127.0.0.1:9999`。
- `/run` SSE 真工具链:realtime_quote→416.71(live)· ths_fund_flow · news_query · chain_for;`deepseek-chat` 完整合成 610 字答案(tokens 2214)。

**本轮 UI 对齐修复(F1–F3,2026-06-04,自包含构建 + ?v=5 上 preview_eval 验证)**:
- **F1 去「本地 mock」误导标签**:连 `backendUrl` 时 composer 与 StatusBar 显示真后端 model(`deepseek-chat · 后端` / `● deepseek-chat`),不再显示与 mock 合成相关的「本地 mock / 切真 LLM」;无后端(file://)仍保留该开关。欢迎页「26 工具」→「40+ 工具」。
- **F2 ⌘K 面板拉引擎 `/tools`**:`CmdKPalette` 挂载时拉 `/tools`(41)与本地 `TOOLS_META`(28 精选)合并——引擎独有工具(如 `overseas_radar`/`run_etf_report`)归「其它」,精选 cn/cat 保留;面板计数动态(实测显 **41**)。
- **F3 接雪球 `/xueqiu/feed`**:侧栏新增「雪球动态」区(`XueqiuFeedSection`),拉 `/xueqiu/feed` 显示真帖(标题/作者/赞评/原文链接),含加载/空/错态(实测 6 条真帖)。**`/xueqiu/hot` 需登录(error 400016),空壳无意义,暂不接。**

**模块工具边界(引擎层硬隔离,2026-06-04)**:对话端 `/run` 传 `profile='research'`,agent **只见研究类工具(30)**,**因子炼制/评测(`alpha_forge`/`factor_test`…)被裁掉,归量化模块**;⌘K 面板拉 `/tools?profile=research`(无因子)。机制:引擎 `_tool_schemas` 按 profile 裁 + `run_turn` 执行 guard 兜底(`profile_tool_names` in `engine/.../buddy/tools.py`)。验证:点名 `alpha_forge` → `tool_done`「不在当前模块可用范围内」+ 正常 `done` + 答案优雅引导去量化模块(非报错);研究类(研报/速览/茅台)正常。

**渲染修复(2026-06-08,?v=7 上 Claude-in-Chrome 验证)**:
- **`renderChatMarkdown` 标题泛化到 `#{1,6}`**:旧实现只判 `#`/`##`/`###`,而 agent 速览答案常用 `####` 作小节标题(估值与行情/资金流向/消息面/产业链/综合倾向)→ 漏渲染成生 `#### xxx` 纯文本(看着「没框 / 丑」)。改成一次性正则 `^(#{1,6})\s+(.*)$` 匹配,1–3 级保持原视觉、4–6 级字号递减。验证:同一条恒盛能源(SH605580)答案重载后 `####` 全部成粗体小节标题,控制台零报错。
- **`renderChatMarkdown` 加 Markdown 表格(?v=9)**:LLM 答案常用 `| a | b |` + `|---|---|` 表格(如「你有哪些工具」列 工具/用途/耗时),旧实现漏渲染成生管道符、列不对齐(用户:「格式太丑 能对齐吗」)。内部 `forEach→for`(向前看分隔行),检测「表头行 + 分隔行」→ 整块渲成对齐 `<table>`(表头下划线 / 行分隔 / 按 `:---:` 列对齐 / 缺列补空)。验证:重载后 2 列(盯盘/经验)+ 3 列(深度研究 工具/用途/耗时)表全对齐,管道符消失,控制台零报错。
- 备注:文本答案 `AiSummary` 本就无边框(聊天流式排版);富速览卡 `StockBriefCard`(有框)仅在后端发 `brief` SSE 事件时渲染。

**财务基本面自动补全(2026-06-08,引擎 + ?v=8 端到端验证)** — 答用户「看 XX 怎么样,财务这些不自动补全吗?」:
- **根因**:对话端取财务原本靠因子层(`fetch_financials`/`rank(roe)`),被硬隔离裁出 research → 取不到;且 brief 速览 recipe(`recipes.py:_build_brief_recipe`)与合成提示词 `_BRIEF_SYNTH_SYSTEM` 都**写死「四维度(估值/资金流/消息面/产业链)」**,没有财务位 → 永远不出财务。
- **改(引擎 fork:`tools.py` 4 处 + `recipes.py` 2 处 + 前端 2 处,自包含 engine 内,红线豁免)**:① `tools.py` 新增 research 域 `financials` 工具(`_tool_financials`:ROE/ROA/净利率 · 营收·净利+同比 · EPS · 负债率 · 净资产 · 现金流 + 近 N 期趋势;读因子层同一份 PIT 防前视财报 parquet,取的是**原始财报口径、非因子 IC/回测**,故属研究域不破硬隔离);`stock_brief` 速览也带财务段;`profile_tool_names` factor 公共集加 `financials`(research 因「全部−因子」自动纳入)。② `recipes.py` brief recipe 加 `financials` 步 + 合成提示词「四维度 → 五维度(加财务基本面)」。③ 前端 `TOOLS_META` 加 `financials`(cn 财务 / cat 基本面)+ HTML bump `?v=8`。
- **验证(控制端 + 浏览器端到端)**:`/tools` 41→42、research 30→31 含 financials、factor 20 含 financials、`alpha_forge` 仍被挡(硬隔离没破);恒盛能源(SH605580)重跑「看看…怎么样」→ **5 个工具(quote+financials+资金流+新闻+产业链)**,答案五维度,财务真数据(ROE 4.99% / 营收 3.13亿 +39.75% / 净利 +11.29% / EPS 0.17 / 负债率 45.29%)+ 趋势,合成 LLM 据财务给「中性偏空·盈利能力下滑」判断;茅台 sanity ROE 10.39% / EPS 21.76;控制台零报错。
- **诚实边界**:资金流(仅同花顺前30榜,小盘不在榜)/ 新闻(DB 30 天内无该股)/ 产业链(未归类)对小盘股是**真数据缺口**,如实标「无数据」,非工具问题。

**市场状态(大盘/主线/regime)自包含 + 数据刷新(2026-06-08,引擎 + 控制端 + 浏览器端到端)** — 答用户「主线停在 6-02,是不是没接后端瞎说?后端全移植到 guanlan」:
- **诊断(铁证)**:`_tool_market_status` 读的 `market_status.json` 是 **6-03 生成的缓存**(锁住 regime 6-02 / 主线 5-26),而底层 EOD `day` bin 实际已到 **6-05**(直读二进制确认:SH600519 收盘逐日齐至 6-05),主线 panel 也到 6-05 → **不是没接/瞎说,是缓存没重生成**;原生成脚本 `export_market_status.py` 在 fa-watch-wt(qlib 版,本仓无 qlib)。
- **移植(自包含,去 fa-watch-wt/qlib)**:新建 `guanlan_v2/strategy/market_status.py` 原生生成器 —— 直读引擎 day 二进制(`QlibBinaryLoader`,复用 `compute.breadth.list_all_instruments`),单遍现算:涨停家数(ret 阈值 主板≥9.5%&<19.5% / 双创≥19.5% / 跌停≤-9.5% + 涨跌家数)+ 市场宽度(% close>MA20)+ 轻量 regime(breadth 阈值;bin 无指数 → breadth-only,`source` 标 `guanlan-lite` 诚实区分,DFM 待补);主线读 `monthly_mainlines_panel.parquet` top-N。**写仓内 `data/market_status.json`(不写 G:/stocks)**。
- **接线**:引擎 `watch/market_status.py:default_market_status_path()` 加 env `MARKET_STATUS_PATH` 覆盖(存在即读仓内,否则回退老 stocks parquet,向后兼容);`guanlan_v2/server.py:create_app()` setdefault 该 env → `data/market_status.json`。
- **验证**:生成器 40s 跑出 **as-of 6-05**(oscillating / breadth 16.8% / 涨停 115 主板102双创13 / 涨3231跌2156 / 主线 6-05 火力发电·通信设备·小金属·化工原料);控制端 `default_market_status_path()→仓内`、`_tool_market_status()` 返 6-05;浏览器重跑「分析大盘和主线」→ 答案 **as-of 2026-06-05**(原 6-02)、涨停 115、主线 6-05,合成 LLM 还指出 6-05 状态与今日 6-08 资金流背离。
- **鲜度上限 = EOD bin 日期**:as-of = 最近**完整收盘**交易日;当日 EOD 未入库 / ingest 半截时回退上一完整日(见下盘中守卫)。要到当天需先跑每日 EOD ingest(写 bin,另案,踩「不并行写 bin」红线)。
- **一键刷新端点(2026-06-09,`?v=10`)**:新建 `guanlan_v2/market/`(`POST /market_status/refresh` 后台线程跑生成器,幂等 + `GET /market_status/refresh_state` 轮询;读仍走引擎 `GET /watch/market_status`)。前端 `DataRefreshButton.onClick` 附带 fire-and-forget 触发 → 点状态栏「数据」按钮**一键连大盘状态一起重生成**,免命令行。**完整收盘日守卫**:`generate()` 从最新交易日往回取第一个**真·完整收盘日**,跳过两类不可用日 —— ① **今日未收盘**(本地 <15:00 → bar 是盘中实时价、涨停/宽度只半天累积;实测盘中 6-09 虽 `n5526` 但涨停仅 78,被时间守卫挡)② **覆盖不完整**(`n<4500`,ingest 写到一半;实测 6-08 `n3401` 被挡),落最近完整日 6-05(`n5523`);loader 缓存故迭代仍 ~40s。验证:控制端 POST→后台 regen→`last_date`/`generated_at` 更新、`GET` 读 6-05;浏览器 `fetch /market_status/refresh`→`started`、v=10 零报错。

**收盘后自动调度(完成,2026-06-09)**:`guanlan_v2/market/api.py:start_market_status_scheduler()` 进程内 daemon 线程,server `create_app()` 启动(幂等只起一次)—— ① 启动期按需刷(json 非今日生成才刷)② 每日本地 `MARKET_STATUS_REFRESH_HOUR`(默认 18:00,收盘+ingest 后)自动刷一次,免手动。验证:临时 HOUR=0/CHECK=60 重启 → startup 正确跳过(json 今日已生成)、~68s 后 `last_reason=scheduled` 触发并完成。**健壮性**:日历/数据读失败 → `_worker` try/except 记 `last_error`、**不崩服务、不覆盖旧 json**(实测日历损坏时 `last_error=DateParseError`、`GET` 仍返旧值、server healthy)。

**⚠️ 环境事故(2026-06-09 10:34,非本仓代码)**:外部 ingest 进程把 `G:/stocks/.../cn_data/calendars/day.txt` 交易日历整文件写花(每行乱码),致引擎 **day bin 读取全挂**(market_status/breadth/技术面)。`.bin` 行情数据未损(只日历文本索引坏)。干净同源 = `cn_data_etf/calendars/day.txt`(逐项一致、8797 行)。**由用户侧恢复 / 重跑 ingest**(红线「不改 stocks / 不并行写日历」,Claude 不擅动);恢复后一键 / 调度刷新即自动回正(完整收盘日守卫会落对日期)。

**删除「数据陈旧/刷新」状态栏按钮(2026-06-09,`?v=12`)** — 答用户「后端真接入了吗?怎么判断陈旧?直接删除」:
- **核实(真后端,非 mock)**:`/data/status`(引擎 `last_update.status_summary()`)读**每类数据文件最后更新时间** → 超阈值(日线 >24h)标 `stale`,`stale_count` 只数有更新器的陈旧类(f10/financials 无更新器不计,免红⚠永不消)。问题在**体验**:lockup/announcements 等非核心数据类堆个吓人的「N 类陈旧」+「更新中」是慢/易卡的 `/data/refresh` 子进程。
- **删除(`app.jsx`)**:移除 `DataRefreshButton` 组件 + 状态栏用法 + `/data/status` 轮询 useEffect + `dataStatus` state + `set_data_status`/`data_refreshing` reducer(死码清净)。服务端核对 `app.jsx?v=12` 已无 `function/<DataRefreshButton`、无 `/data/status` 轮询。引擎 `/data/status`、`/data/refresh` 端点**未删**(别处或用)。
- **副作用**:原挂数据按钮上的「大盘一键刷新」随之移除 → 大盘刷新改由**调度(默认 18:00)+ `POST /market_status/refresh`** 维护,自动刷新不受影响(仅少手动按钮)。

**对话上下文记忆修复(2026-06-09,引擎 `recipes.py`+`server.py` · 单测+控制端+浏览器三层验证 · 无前端改动)** — 答用户「对话根本没上下文记忆,越改越回去」:
- **诊断(代码 + 浏览器活体复现双锤)**:`/run` 有两条路径——**recipe 路径**(`run_recipe`,处理大多数股票类 query)只接 `agent._client`,**既不读也不写 `agent.messages`**;只有**自由循环** `agent.run_turn` 才维护历史。后端 `_agent_for(session_id)` 本按会话复用 `BuddyAgent`(设计上有记忆),但 recipe 路径把它绕过 → 对话历史**从不累积**。浏览器复现:「你帮我看看立昂微」(走 recipe,**没写历史**)→「给我出一份深度研报」(无代码 → `resolve_slots` 失败 → 落自由循环,但 `agent.messages` 空)→ 反问「哪只股票?」。两条断裂(recipe→recipe、recipe→自由循环)同源。
- **改(引擎 fork 内,自包含;**无前端改动 → 不 bump ?v=**)**:① `recipes.py` `run_recipe` 加可选 `agent=None`;新增纯函数 `recipe_history_messages(agent)`——取近 8 轮**干净** user/assistant 文本(剔 tool 噪声/空消息、抹内部 `<!--gate-->` 留痕、`max_msgs`/`max_chars` 双截断防膨胀),垫在综合 prompt 的 system 与当前 user 之间。② 产出前把**干净的 (query, answer)** 写回 `agent.messages`(只存对话、不存工具 pack 防膨胀;综合 LLM 失败已提前 return → 失败轮不落盘,守「失败不入库」)。③ `server.py:/run` 把 per-session `agent` 传进 `run_recipe(...)`。两路径自此**共享 `agent.messages`**,recipe↔自由循环记忆打通。
- **验证(三层)**:① 单测 `tests/test_recipe_memory.py` 3/3(综合读历史 / 写回历史 / 剔 tool 噪声);全套 **91 passed**(1 既存无关 `test_strategy_provenance` 数据漂移,与本改零重叠)。② 控制端 `/run`×2 同 `session_id` 真 LLM:第二轮**纯记忆探针**(无股票名、无 context)→ 答「你刚才让你看的是**立昂微 (SH605358)**,资产负债率 **57.16%**」(连第一轮财报数字都回忆起),`tools=[]` 确证走自由循环、纯靠历史。③ 浏览器原始失败序列:「给我出一份深度研报」→「**立昂微 (SH605358)** … 深度研报需 5-8 分钟,确认要跑吗?」(记得标的 + 回忆 PB6.85/净利-103%,**不再反问哪只**)。
- **边界(已被下条「抗重启根治」补完)**:本条让记忆在**单个进程实例内**贯通(recipe↔自由循环);但记忆仍只在后端内存 → 进程一重启即丢。跨重启持久化见下条。

**对话记忆抗重启根治(2026-06-09,前端 `app.jsx`+`agent-adapter.jsx` + 引擎 `server.py` · `?v=13` · 单测+控制端+浏览器三层验证含真重启)** — 答用户「还是没用…去原版看看怎么做的」:
- **真根因(三层证据钉死;上一条 recipe 修复是必要的第一步, 但不是全部)**:对话历史**只存后端进程内存**(`build_app` 闭包里的 `sessions: OrderedDict[session_id→BuddyAgent]`,server.py)。本环境 9999 被反复重启(实测 PID 45436→13920 自换),**一重启 `sessions` 全清空 → agent 变冷(`messages` 空)→ 下一句失忆**(浏览器「确认」→「请告诉我哪只股票」= `session_id=null` 的答案一字不差)。**前端 session_id 其实稳定**(fetch 钩子实证 T1=T2 同一 `sess_…`),**不是前端丢 SID**。**对照真原版 `G:\financial-analyst\src`:会话机制与 fork 完全相同(同 in-memory `sessions`+`_agent_for`,无 recipes.py),只因它是单进程桌面 app 不重启才没暴露** → 正解不是照搬原版,而是做得更稳。
- **改(标准「客户端持有对话」范式,抗重启/刷新/换实例)**:① 前端每轮带**近期对话** `history`(`app.jsx startAgent` 从本轮前的 `currentSession.messages` 取 user 问 + assistant 答、近 12 条;`agent-adapter.jsx _runBackend` 塞进 `/run` body)。② 后端 `server.py` `RunReq.history` + 模块级 `_seed_agent_history`:**仅当 agent 冷(`not agent.messages`)**才用客户端历史 seed 回(剔 tool/空噪声、`max_msgs`/`max_chars` 双截断);热 agent 不覆盖/不重复。两路径(recipe/自由循环)都读 `agent.messages` → 都受益。③ 顺手修前端两 bug:`savePersisted` 漏存 `backendSid`、`makeInitialState` 不回填 → 刷新后 session_id 断链。
- **验证(三层,含真重启)**:① 单测 `tests/test_session_seed.py` 4/4(冷 agent 被 seed / 剔噪声 / 有界 / 空 no-op)+ 全套 **96 passed**。② 控制端:**全新 session_id(=冷 agent)+ 客户端 history** → 答「你刚才让我看的是**立昂微 (SH605358)**,资产负债率 **57.16%**」;`history=null` 对照失忆。③ **浏览器真重启**:T1 立昂微 → T2「它负债率多少」答 57.16%(`history_len` 0→2)→ **kill 服务、起全新实例(内存清零)** → T3「我刚才问的哪只股票?净利同比?」答「**立昂微 (SH605358),净利同比 -103.46%**」(`history_len=4`,后端零内存全靠 seed 恢复)→ **记忆扛住重启**;`?v=13` 控制台零报错。
- **运维(更正上一条)**:9999 **无可靠看门狗**(杀后实测 60s 未自动拉起)——改引擎后须**手动重启**(杀监听 PID + `python -m guanlan_v2.server`;本会话用 Bash `run_in_background` 拉起更稳)。

**开放项**:
- **regime DFM 逐位一致**:现 `guanlan-lite`(breadth-only,bin 无指数 → 无趋势确认,保守默认震荡)。要与生产 DFM 口径一致需移植 `market_regime.py`(DFM 模型 + benchmark,工作量大、可能带模型文件)。
- **富速览卡触发**:「看 XX 怎么样」是否稳定出富速览卡(有框),取决于后端是否发 `brief` SSE 事件。小盘股(如恒盛能源 SH605580)实测只出文本 answer(无 `brief` 事件)→ 看着「没框」。待查后端 brief 事件触发条件(对比宁德时代等大盘股),决定是否前端兜底(intent=brief && sym 解析到 → 也插富卡)。
- **默认 model = `deepseek-chat`**(原 qwen3.5-plus token 过期 401);旧浏览器若持久化了 qwen pin,需 ⌘K 重选或清 `guanlan:state:v2`。根治在引擎/环境侧 `.env`。
- **雪球 cookie 未配**:`/comments?refresh=1` 与 `/xueqiu/hot` 需服务端登录 cookie(现 WAF 拦,已提示登录);`/xueqiu/feed` 无需登录、可用。cookie 配置在引擎侧采集器。

**2026-06-10 · mock 清零快赢批(app.jsx `?v=14`,审计 M4)**:
- `StockBriefCard` 识别失败不再回退写死的宁德时代(`STOCK_DB['300750']`)→ 显空态「未识别到标的,请补代码或全名」;`buildReportText`(仅 file:// mock 预览路径)无标的时返回明示「示例研报」、正文标题加「(示例 · mock 预览, 非真实数据)」。
- 欢迎页样本问题改跟用户**真自选清单**动态拼(无自选退通用问法),不再写死宁德/茅台。
- 浏览器验真 v=14 编译渲染零报错。

**2026-06-10 · 研报串台隔离 + 慢工具档1(app.jsx `?v=15` + 引擎 tools.py,审计 N3/N4)**:
- **N3 串台隔离**:`reportDrawer` 开启时记 `originChainId`(resolve_confirm 接受 run_report 处取 `activeRound.chainId`);`advance_report` reducer 守卫 —— 事件带 chainId 且与抽屉发起轮不符则忽略;`onToolDone`/`onReport` 的 advance dispatch 带当轮 chainId。A 会话研报跑动中 B 会话再跑一份,两抽屉互不覆盖;不带 chainId 的旧 dispatch(假进度路径)不受影响。
- **「加入研究档案」接线(`?v=18`,研报跨模块闭环)**:抽屉底部该按钮此前是**死按钮**(无 onClick),且 chat 页根本没载 `guanlan-bus.js`(`window.GL` 不存在)→ 生成的研报永远出不了对话模块。改:① html 补载 `guanlan-bus.js?v=2`(顺带顶栏档案库计数变真);② 按钮 onClick → `GL.put({type:'research', id:'rs_report_<code>_<date>'(幂等), title, kind:'研报', from:'觀瀾 · run_report', path, status:'raw'})`,成功变「✓ 已入档案库」;③ `open_report`/`onReport`/研报卡 onView 全链透传 `path`。**研报存储三层**:全文 `out/<code>_<date>.md`(经 `/report?path=`)、会话研报卡(localStorage+/conversations)、个股时间线 `~/.financial-analyst/memories/stocks/`。验真:点击后 GL 出现 `rs_report_SH605358_2026-06-10`(demo:false,带 path),图谱「对话·研报」支柱 4→5 显示该研报**无示例徽章**,落子料库同源自动可见。
- **自选墙走势线喂真(`?v=17`)**:左栏自选 6 票的 sparkline 此前一直是 `WATCHLIST` 设计稿假数组(宁德 240→325 恒上行),与真涨跌方向常打架(用户实锤:宁德 -2.72% 线却朝上)。`LeftRail` 新增 effect 按自选 codes 拉 `/seats/daily?freq=day&n=20` 真收盘喂 `Sparkline`;拿不到显空位不回退假线,file:// mock 预览才用旧 SPARK。验真:渲染 20 点折线与端点真序列首尾方向逐票一致(宁德 434→399 下行 ✓ 茅台 1344→1256 下行 ✓),K 线窗口 05-13~06-09。
- **抽屉被导航条压头修复(`?v=16`)**:全局导航 `#gl-nav` 是 `sticky top:0 z-index:9000`,而研报抽屉/轻量详情抽屉 `fixed top:0 zIndex:95` → 头部 44px(标题+关闭键)被盖(用户截图实证)。两处抽屉容器 `top:0→44` 让出导航高度;浏览器验真:抽屉头完整、✕ 可点、导航仍可用。
- **N4 慢工具档1**(`tools.py` 晨会/主线雷达/海外雷达,共用壳 `_daily_cached_swarm`):① **当日成功结果缓存** `~/.financial-analyst/cache/daily_tools/{slug}_{date}.txt`,同日再调**秒回**文首标「[缓存 · 今日 HH:MM 生成]」;② 超时 600s→主线 180s/晨会·海外 300s,失败带已跑时长;③ 实时失败时若有 ≤3 天旧缓存,附带返回并**明确标日期**「仅供参考」(不冒充今日)。进程内双路径验真:缓存命中 0.01s 带标 / 失败附昨日缓存标注。档2(子进程流式进度)未做,留后续。改 tools.py 已重启 9999。

**2026-06-11 · P2-D 接交棒 + 死链(`app.jsx?v=19`)**:① mount 消费 `take('chat')`(落子「问对话」{code,name,intent} → `agent_context` 设会话个股 + `prefill` 预填作曲器,不自动发送)与 `take('dialogue')`(图谱研报 {focusId} → GL 物料 path → /report 抓全文 → 直开研报抽屉)——此前两通道全仓无消费者,跳过来是白页;② :1468「📡 盯盘台」死链 cockpit.html(不存在)→ `../seats/观澜 · 落子.html`。验真:预填「宁德时代(300750)最近的落子研判怎么看?」+ 立昂微研报抽屉直开(综合评级节在屏)。另:buddy 新工具 `quant_reports`(P2-F)在 research profile 可用——对话能列工作流验证报告真 KPI。
