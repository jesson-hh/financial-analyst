# 观澜 · 帷幄 — 单核心 Agent 总控台设计

- 日期:2026-06-11
- 状态:一期+二期均已实现并验收(二期 2026-06-13,plan: docs/superpowers/plans/2026-06-12-weiwo-console-phase2.md);三期待启动(@引物料//快捷令/落子页嵌右栏/哨兵研判回流/多会话并行/启动扫描中断bg任务)
- 设计稿:`ui/_mockups/console-mockup.html`(静态,合成展示值)
- 调研依据:OpenHands(事件日志+condenser)、bolt.diy(产物面板跟随)、AG-UI(SSE 事件协议)、Claude Code(TodoWrite+记忆文件+压缩)、Letta(记忆块)

## 1. 背景与动机

现状:平台六个分页(图谱/对话/经验卡/工作流/选股/落子)各自为政,互通靠 GL 总线 + handoff 通道;chat 页虽有 buddy agent 但 profile 裁剪、记忆按页割裂。用户要的是 vibe coding 体验:**一个核心对话 agent 通过工具操控全平台**,任务记忆集中、左任务/中对话/右功能三栏布局。

## 2. 已拍板决策(用户 2026-06-11)

| 分叉 | 决定 |
|---|---|
| 页面策略 | **新建** `ui/console/观澜 · 帷幄.html` 作日常主入口;六个旧分页全保留为「专业视图」;chat 对话页暂留,稳定后再议退役 |
| Agent 大脑 | **扩展引擎 buddy agent**(新增 console profile),不另起 orchestrator |
| 命名 | **帷幄**(运筹帷幄,与「落子」执行层相对) |
| 右栏宽度(第二轮) | 默认太窄→**工作台优先布局**(右栏主面积)+ 拖宽 + ⇋ 对话/工作台优先切换 |
| UI 融合(第二轮) | 「应该融合 而不是一个全新的」→ 右栏 = **现有页同源 iframe 原样嵌入**,不做新渲染器;新写的只有壳+对话流+任务栏(见 3.4) |
| 去重复头(第三轮) | 「上面太重复了」→ embed=1 隐藏内页导航条+页头,只留帷幄一个顶栏(见 3.4 嵌入卫生) |
| 单一 agent 入口(第三轮) | agent 入口只留 帷幄+落子哨兵;其余各页 agent 窗口**全局隐藏**(?legacy=1 找回),手动调整控件保留(见 3.7) |
| 删看板/图谱 tab(第四轮) | 右栏开局**整个隐藏**,只有对话;agent 调工具产出后滑出对应页,tab 按激活出现;✕ 可收起;研报阅读=中栏 md 抽屉 |
| chat 并入帷幄(第五轮,2026-06-12) | 「对话·研报」页退役:研报全部能力(生成/进度/阅读/入档)并入帷幄;**盯盘类(自选/提醒/行情轮询/雪球)不迁**——落子哨兵地盘;引擎 /alerts 端点保留待落子三期接 |
| 导航收敛(第六轮) | **顶栏只剩 帷幄 + 席位·落子**;经验卡/工作流/选股/图谱全摘出导航(代码直链保留)——界面降级为「agent 召之即来的工作台视图」:新工具 `ww_show_page` 口头调出右栏;经验卡定位=给 agent 消费的知识库(query→回测闭环已通,补 ww_cards_save 回写) |
| 对话 UI 移植(第七轮) | 帷幄对话框「太丑」→ **全盘移植 chat 页对话界面**(消息卡/markdown/工具链 chips/研报抽屉/输入坞样式),数据源换 console 事件流;不迁 chat 的盯盘件与会话栏(帷幄左栏已有) |

## 3. 前端设计

布局:52px masthead + 三栏 grid `264px / 1fr / 432px`,min-width 1280,沿用 tokens.css 文人书案风。

### 3.1 顶栏 masthead
印章「帷」+ 标题 + 状态 chips:引擎在线 / 数据日期 / 体检 IC / 工具数 / 「分页 ▾」(跳旧页)。

### 3.2 左栏 · 任务栏(agent 记忆的可视化)
- **进行中任务**:两类合流渲染——① agent 计划项(plan_update 事件);② 后台长任务(task_update 事件,带进度条/排队态)。点击跳对应会话与消息位置。
- **今日已完成**:完成项沉底,半透明,点击回看产物。
- **会话列表**:多会话,当前高亮(左侧朱砂竖线);跨刷新可恢复。
- 脚注:记忆条数 / 档案件数(GL.stats)。

### 3.3 中栏 · 对话
- 用户消息右对齐卡;agent 消息带「觀」印章头像。
- **计划块**:agent 拆解任务的清单卡,✓(黛绿)/▶(朱砂跳动)/○ 三态,实时勾掉,与左栏同源(同一 plan 数据两处渲染)。
- **工具调用卡**:折叠一行 = ⚙ + 工具名(mono)+ 参数摘要 + 状态/耗时;展开 = 关键指标行 + 「右栏查看 ↗」。大 payload 永不内联。
- 流式文本 + 光标;运行中可打断/追加。
- **输入坞**:`/` 快捷令、`@` 引物料(经验卡/因子/研报喂上下文)、深度研判开关、印章「令」发送,Shift+Enter 换行。

### 3.4 右栏 · 工作台 = 现有页原样嵌入(用户反馈 2026-06-11 第二轮:「应该融合 而不是一个全新的」)

**右栏不做新渲染器**——既有六页 UI 是平台资产,原样进驻:

- **初始态:右栏不存在(用户第四轮)**。开局只有 左栏+对话 两栏,对话居中铺开;agent 首次调用某页工具产出 artifact 时,右栏**滑出**并直接打开对应页(bolt.diy 的 workbench 行为);「✕」可随时收起回纯对话,下个产物到来再自动滑出(钉住时不自动)。
- **布局(右栏打开后):工作台优先**。三栏 `264px / 460px / 1fr`,右栏是主面积(1568 屏上 ~840px);「⇋ 对话优先」一键互换(对话吃宽、工作台收 560px);左缘分隔条可拖(宽度存 localStorage)。同时回应「右栏太窄不好看盘」。
- **tabs = 本会话激活过的现有页**:候选 选股 / 工作流 / 落子 / 经验(~~看板/图谱~~ 已删,用户第四轮;图谱仍是独立页,顶栏「分页 ▾」可去)。每个 tab 是一个**同源 iframe**,装的就是 `/ui/<module>/观澜 · X.html` 本尊——选股 2.0、工作流画布、校场、验证区,一像素不重画;tab 只在 agent 首次产出该页 artifact 后出现。
- **同源融合是免费的**:六页全跑在 9999 同源,iframe 内 localStorage / GL 总线 / handoff 通道与宿主天然共享。
- **agent 驱动既有页**:工具结果的 artifact 信封改为 `{kind, page, channel, payload, ref}`——宿主收到后 `GL.handoff(channel, payload)` + 重载对应 iframe,页面 mount 时 `take(channel)` 自取并渲染。选股页 `take('screen')` 在 P1⑨ 已按「未来接口」预留,帷幄就是它的生产者;落子 cockpit、工作流 workflow、经验 validation、chat dialogue 通道全部现成。
- **右栏零新渲染**:看板已删——本会话产物 chips 移到右栏底部产物条,计划/后台任务概览本就在左栏;研报阅读 = 中栏对话流内 md 抽屉(GET /report 既有端点;chat 页不嵌入,因为它本身就是要收编的 agent 窗口,见 3.7)。
- **自动跟随**:agent 产出新 artifact——右栏关着则滑出,开着则切到对应页 tab;「⌖钉住」锁定;「↗」原独立页全宽打开;「✕」收起。
- **嵌入卫生(用户反馈第三轮:「上面太重复了」)**:`?embed=1` 时隐藏三样——① guanlan-nav 导航条(共享守卫一行)② 各页自己的页头 masthead(页标识/状态 chips 与帷幄顶栏重复)③ 各页 agent 窗口(清单见 3.7)。页头里的**功能按钮**(重新计算/据此落子等)不随页头消失,保留或上移至该页工具行。iframe 按面板宽 `scale(panelWidth/1280)` 自适应缩放(下限 0.6);**惰性挂载**:只挂当前 tab(+钉住的),省内存省 babel 编译。

### 3.5 文件结构(UMD + babel + ?v= 惯例)
```
ui/console/
  观澜 · 帷幄.html        # 模板:react UMD + bus?v=3 + nav + GUANLAN_BACKEND 同源开关
  console-data.jsx        # 事件流客户端(EventSource)+ reducer + 会话/计划/任务状态(纯逻辑)
  console-rail.jsx        # 左栏
  console-thread.jsx      # 中栏(消息/计划块/工具卡/输入坞)
  console-bench.jsx       # 右栏(tabs + renderer 注册表)
  console-app.jsx         # 主壳
```
红线:旧页改动**仅限授权清单**——① `guanlan-nav.js` embed=1 守卫 ② 各页页头/agent 窗口的隐藏(3.7 删除清单,用户已拍板);清单之外一行不动;新页改动 bump `?v=`(用 Edit);该 UI 风格组件密度对齐落子页。

### 3.6 与落子的关系:统帅—哨兵双层 agent(用户拍板 2026-06-11)

落子**不并入**帷幄,保留专页 + 它自己的专门 agent 控制:

| 层 | 角色 | 职责 |
|---|---|---|
| **帷幄(统帅)** | 全平台核心对话 agent | 拆任务、调工具、跨模块编排;对落子只下"军令":pool.add 入池、seats.decide 触发研判 |
| **落子(哨兵)** | 既有的专职盯盘 agent 体系 | TF 时钟、策略席位、研判三触发、影子组合——在自己的全宽专页里值守看盘,一行不动 |

回流:哨兵的研判落盘(`var/seats_decisions.jsonl`)已是事实事件源——三期把新研判作为通知事件回注帷幄会话(左栏「落子哨兵 · 新研判」卡),统帅可据此续编排。右栏「落子」tab = 落子页原样嵌入(哨兵体系自治运转,工作台优先布局下有 ~840px 可用);认真盯盘仍建议「↗」独立页全宽。

### 3.7 单一 agent 入口(用户拍板第三轮:「除了席位落子的agent…其他的agent窗口都可以删掉」)

全平台 agent 入口收敛为**两个**:帷幄对话(统帅)+ 落子席位研判 agent(哨兵,独立决策,**全保留**:研判三触发/agent 研判按钮/研判历史抽屉)。

**删除清单**(散落各页的 agent 窗口,由帷幄工具替代):

| 页 | 删除的 agent 窗口 | 帷幄替代 |
|---|---|---|
| 选股 | 顶栏「观 · 一句话调约束」LLM chip;左栏「LLM 选因子」框 | screen.run(blend/约束参数) |
| 工作流 | 「AI 搭图」文本生成入口、「AI 闭环 ✦」、critique | workflow.generate_run(LLM 搭图+critique 后端能力保留,只删页面入口) |
| 经验卡 | refine LLM 入口 | cards.refine 工具 |
| chat 对话页 | 整页即 agent 窗口 → 不进右栏 tabs,留存待退役 | 帷幄对话本体 |
| 图谱 | 无 agent 入口 | — |
| 落子 | **不删** | 哨兵自治 |

**保留的手动调整控件**(直接操作,非 agent):选股 条件滑杆/三开关/股票池切换/重新计算/据此落子/导出;工作流 画布节点编辑/运行/载入保存/导出报告/沉淀经验卡;经验 批准/状态迁移;落子 全部;帷幄自身 ⌖钉住/⇋切换/拖宽/↗。

**删除范围(用户已拍板)**:**全局隐藏**——无论嵌入还是独立打开,agent 窗口默认不显示;URL 加 `?legacy=1` 可临时找回(可回退,代码不删)。平台从此只有帷幄+落子哨兵两个 agent 入口。

## 4. 后端设计 `guanlan_v2/console/`

### 4.1 存储:每会话一个追加式事件日志
```
var/console/
  sessions/<sid>/meta.json      # {title, created, updated, status, plan: [...]}
  sessions/<sid>/events.jsonl   # 一行一事件,只追加
  memory.md                     # 帷幄全局记忆文件(agent 可读写)
```
事件 schema(7 类,id 单调递增):
```json
{"id": 1, "ts": "2026-06-11T14:00:00", "type": "user_msg",    "text": "..."}
{"id": 2, "ts": "...", "type": "agent_delta", "msg_id": "m1", "delta": "..."}
{"id": 3, "ts": "...", "type": "tool_call",   "call_id": "c1", "tool": "backtest.vector", "args": {}, "display": "回测 动量·csi300"}
{"id": 4, "ts": "...", "type": "tool_result", "call_id": "c1", "ok": true, "summary": "Sharpe 0.68",
          "artifact": {"kind": "backtest_report", "page": "factor", "channel": "workflow", "payload": {}, "ref": "report:<rid>"}}
{"id": 5, "ts": "...", "type": "plan_update", "todos": [{"id": "t1", "text": "...", "status": "done"}]}
{"id": 6, "ts": "...", "type": "task_update", "task_id": "bg1", "status": "running", "progress": 0.6, "note": "第3/5步"}
{"id": 7, "ts": "...", "type": "condensation", "replaces": [1, 30], "summary": "..."}
```
恢复 = 重放;前端是这条流的纯渲染器。

### 4.2 端点
- `POST /console/send` — 用户消息入队,触发 agent 轮
- `GET /console/stream/{sid}` — SSE;连上先发 `snapshot`(meta+全量/尾段事件),再续直播
- `GET /console/sessions` / `POST /console/sessions` / `DELETE …/{sid}` — 会话 CRUD
- `GET /console/tasks` — 后台任务总览(左栏轮询兜底)
- `POST /console/confirm` — 确认门应答(沿用 buddy confirm_required 语义)

### 4.3 Agent 集成(扩展 buddy)
- 复用 `BuddyAgent`(15 轮工具循环)+ `LLMClient.for_agent`(provider 路由)。
- 新增 `profile='console'`:**平台模块工具进程内直调**(guanlan_v2 各模块函数,不走 HTTP 自环——共享 FACTOR_DEFS 等单例,且 9999 单进程);工具薄壳放 `guanlan_v2/console/tools.py`。
- 每轮 prompt 组装:system + memory.md + 当前 plan + condensation 摘要 + 最近 N 事件。

### 4.4 工具表(一期 ~12 个常驻 + 元工具)
| 工具 | 映射 | 时长 |
|---|---|---|
| plan.update | meta.json plan 字段(TodoWrite 式) | 即时 |
| memory.write / memory.read | var/console/memory.md | 即时 |
| screen.run | screen.api 选股(pool/blend/topN) | 秒级 |
| workflow.generate_run | workflow LLM 搭图 + headless 真跑 | 秒-分 |
| factor.analyze | /factor/report2 截面 IC / tsic 单票 | 秒级 |
| backtest.vector | workflow 回测(定权/分腿成本) | 秒级 |
| seats.decide | 落子研判(配方喂 LLM) | 秒级 |
| pool.add / pool.list | 盯盘池(对接 lz pool 后端落点,见开放项) | 即时 |
| cards.save / cards.query | 经验卡 CRUD | 即时 |
| reports.query | reports/store + out/ 研报检索 | 即时 |
| report.run | 深度研报(subprocess)——**二期**随后台 runner 一起上,一期不常驻(避免阻塞对话) | 分钟级→后台 |
| archive.query | var/archive 物料检索 | 即时 |
| 元工具 list_tools/describe_tool | 长尾渐进披露(buddy 45 工具不全量常驻,防选择准确率下降) | 即时 |

### 4.5 记忆三层(「长对话记住任务」的答案)
1. **计划层**:plan 存 meta.json,每轮注入 + 左栏渲染——UI 即记忆,天然扛压缩;
2. **压缩层**:condenser——事件视野超阈值(估 token)即把最旧一段 LLM 总结成 condensation 事件;jsonl 全量不丢,只压模型视野;
3. **持久层**:memory.md(每轮新鲜注入)+ GL 档案/经验卡(已有后端持久化)作长期知识。
零向量库;全部文件级,可 git、可人读。

### 4.6 长任务
分钟级工具(report.run、未来 v4 regen)立即返回 `{task_id, status:"running"}`;后台 runner 线程追加 task_update 事件;agent 不阻塞,任务完成事件在下一轮回注。9999 看门狗重启 = SSE 断流,前端 EventSource 自动重连先收 snapshot 补齐。

## 5. 分期

| 期 | 内容 | 验收出口 |
|---|---|---|
| 一期·骨架 | console 后端(jsonl+SSE+plan 工具+核心工具)+ 前端(对话/计划块/工具卡 + 右栏初始隐藏/首产物滑出/✕收起 + 工作台优先布局/拖宽/⇋切换)+ 右栏宿主嵌**选股、工作流**两页(handoff 驱动)+ 中栏研报 md 抽屉 + embed=1 卫生(nav/页头/这两页的 agent 窗口,按 3.7 清单) | 一句话跑通「验证因子→回测→选股」全链,产物在嵌入的真页面里显形,刷新可恢复 |
| 二期·U 批(UI 移植) | 帷幄对话界面全盘移植 chat 页(消息卡/markdown 渲染/工具链 chips/**研报抽屉**/输入坞),数据源换 console 事件流 | 帷幄对话观感 = chat 页水准;抽屉能读存量 38+ 篇研报 |
| 二期·A 批(研报并入) | 后台长任务 runner + `ww_report_run`(确认门/进度事件/完成回注对话)+ 研报自动入 GL 档案 | 「给宁德写份研报」→ 左栏进度条 → 期间可续聊 → 跑完通知 → 抽屉读全文 → 落子料库可见 |
| 二期·D 批(导航收敛) | nav 只剩**帷幄+落子**;经验卡/图谱注册进 WW_PAGES + embed 卫生(经验卡页隐藏 refine 入口);`ww_show_page` 工具(口头调出工作台视图);`ww_cards_save`(确认门);chat 页摘出导航 | 平台两门面;「调出经验卡界面」口头可达;agent 闭环 = 读卡→回测→回写新卡 |
| 二期·B 批(记忆) | condenser(视野超阈值 LLM 压缩,jsonl 不丢)+ memory.md(agent 读写工具+每轮注入) | 小时级会话不失忆;偏好跨会话记住 |
| 二期·C 批(体验) | 左栏后台任务进度分区、右栏拖宽(localStorage)、顶栏模型切换(可选) | — |
| 三期·融合深化 | @引物料、/快捷令、落子页嵌右栏(落子保有独立入口不急)、哨兵研判回流帷幄(通知事件)、多会话并行任务、跨会话任务恢复 | — |

## 6. 红线与诚实口径(沿袭全仓既有)

- G:/stocks 只读;mock/兜底必打「示例」标显形;LLM 失败不落盘;配方因子只喂 LLM 不冒充确定性回测;
- 旧页一行不动;新页 jsx 改动必 bump ?v=;改 python 必重启 9999(杀监听 PID 等看门狗);
- 工具结果 artifact 必须真数据,右栏渲染不得静态装饰。

## 7. 风险与对策

- **no-build 体积**:三栏拆 5 个 jsx 文件,babel 逐文件转译,沿用落子页结构先例(6 文件)可行;
- **SSE 与看门狗**:重启断流→重连+snapshot 协议覆盖;
- **工具直调单例**:console/tools.py 必须在 9999 进程内 import 模块函数(同 screen↔factorlib 先例);
- **token 估算粗糙**:condenser 阈值保守(字符数/4 估),宁早勿晚;
- **iframe 重载延迟**:嵌入页 babel 编译 1-3s——handoff 驱动刷新时 tab 上出加载态;惰性挂载(只挂当前+钉住 tab)控内存;
- **缩放可用性**:scale(panelWidth/1280) 下小字可读性下降——拖宽/⇋ 切换/↗ 全宽三条退路;缩放系数下限 0.6,再窄改横向滚动;
- **iframe 内跳转逃逸**:嵌入页内部 `<a>` 跳别页会把 iframe 导走——embed=1 模式下 nav 隐藏已挡主要入口,页内跳转链接保持 iframe 内导航即可接受,实测再收口。

## 8. 开放项

- ~~agent 窗口删除范围~~ → 已拍板:全局隐藏 + `?legacy=1` 找回(见 3.7);
- 盯盘池目前是前端 localStorage(guanlan:lz:pool:v1)——pool.add 工具需要一个后端落点(var/ 池文件)+ 落子页读取合流,放一期实现细化;
- chat 页退役时机(三期后再议);
- 沪深300 基准补源(独立探讨项,不属本设计)。
