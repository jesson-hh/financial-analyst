# 落子右栏交互重构设计(实盘清晰化 + 舰队每股信号 + 自选/盯盘)

> 日期 2026-06-15 · 模块 ui/seats(观澜·落子)· 类型 前端交互重构(后端不动)

## 目标(一句话)

把「落子」实盘/舰队的右侧栏交互理清:右栏可滚动、实盘精简掉易误解的 scanSeat 信号队列改显真 LLM 研判流水、舰队按股列每只信号、新增「自选 vs 盯盘」区分;一切**诚实标注、不冒充真信号**。

## 背景与现状核实(读码已证,带 file:line)

本设计前用 5 路并行读码核实了用户的疑问,结论作为设计依据:

1. **信号队列不是凭空假数字,但也不是「真交易信号」。** 它是 `DecisionFlow`([luozi-panels.jsx:936](../../../ui/seats/luozi-panels.jsx))的实盘分支(`mode==='live' && !isRun`,标题/徽章在 panels.jsx:947-949),数据 = `symbol.decisions`(panels.jsx:941 按 `active` 席位过滤)= `scanSeat`([luozi-data.jsx:363](../../../ui/seats/luozi-data.jsx))确定性价量规则(MA5/MA20 交叉 + 量比 + PA 几何)在**联机时真日线**上扫出的结果。徽章「启发式·非LLM」是诚实自述。它**不是** agent 思考、**不是**实时撮合、**不是**真后端决策;与真 LLM 研判(`realDecs` app.jsx:59 / `/seats/decisions`)是**两套独立数据**。唯一合成料是卡上挂的 `evidenceFor`(luozi-data.jsx:325-360,已带「示意」徽章)。
2. **仓位台账有真后端,且是「一本共享组合账」跨所有股票。** 端点 `POST /seats/ledger`([api.py:687](../../../guanlan_v2/seats/api.py))、`GET /seats/ledger/state`(api.py:761,**无 code 入参**)、`_ledger_replay`(api.py:351)、落盘 `var/seats_ledger.jsonl`(append-only)。一个现金池 + 一条净值线 + 一份已实现盈亏/胜率;`positions:{code:…}` 横跨所有票但共用同一账。注释明示「实盘=一个组合,非按票」(api.py:332)。「重开账」= 追加一条 `open` 事件,重放只算最后 open 之后→归零(旧账留档)。「盘中自动研判落账」真在跑但**纯页面驱动、无后端 scheduler**:门控 `loopOn && mode==='live' && fresh`(panels.jsx:183-199),`runTimedDecide`(panels.jsx:99)真调 `/seats/decide`。「9:30 起随行情」(panels.jsx:662)是**纯文案**,代码无 9:30 锚,真实闸门是 `fresh`(盘中)。
3. **舰队切过去右栏整块消失。** 右栏 DOM 写死在 `view==='single'` 分支(app.jsx:633-707),`view==='fleet'` 只渲染 `<FleetGrid>`(app.jsx:708-712);点卡 `onPick` = `setCode(c); setView('single')`(app.jsx:710),即舰队只是选股入口。每股信号数据**已 per-code 可取**(scanSeat 每股算、`realDecs` 按 code 存),FleetGrid 卡片已各带「今日信号」徽章(luozi-fleet.jsx:60-66)。
4. **无「自选 vs 盯盘」区分。** 盯盘池 = 模块级数组 `SYMBOL_META`(luozi-data.jsx:542,6 固定底座 + 动态票),唯一维度是「固定 vs 动态(能否移出)」(`poolIsDynamic` :596),与盯不盯盘无关。全仓 grep `monitored/watch_only/自选` 零命中。自动研判现在只跑「当前聚焦的那只」(单 `OrderWatchPanel` 实例 app.jsx:682),是单聚焦架构副作用,不是盯盘范围控制。
5. **右栏滚不动根因**:右栏容器(app.jsx:674,`width:372` flex 列,有 `minHeight:0` 但缺 `overflowY`)内部面板全 `flexShrink:0` 堆叠(RunPicker/LedgerPanel/OrderWatchPanel/DecisionFlow 包裹),总高超出即被 `body{overflow-y:hidden}`(观澜 · 落子.html:13)裁掉,无滚动出口。

## 已锁定的设计决策(用户 2026-06-15 拍板)

| # | 决策 | 选择 |
|---|---|---|
| 1 | 实盘账本模型 | **维持一本组合账**(全局共享,不改 per-code) |
| 2 | 「盯盘」的含义/范围 | **标签 + 资格,维持单聚焦**(不做真·多股后台轮询研判) |
| 3 | 舰队右栏 | **网格 + 右栏每股信号列**(点行聚焦切单标) |
| 4 | 实盘 scanSeat 信号队列 | **实盘里精简掉**(改显真 LLM 研判流水) |

补充默认(设计时定,非用户逐项拍):新票及存量票一律默认 **自选(只看)**,opt-in 盯盘(与项目一贯 opt-in/存量不变风格一致)。

## 分节设计

### ① 右栏可滚动(纯修复)

- **改**:右栏容器 app.jsx:674 加 `overflowY:'auto'`(`minHeight:0` 链路已通到顶层定高 flex 列,无需补)。
- **连带**:末尾决策卡包裹 app.jsx:699 现为 `flex:1, minHeight:0`;在 auto-overflow 容器里 `flex:1` 失去「填满剩余」语义,卡内 `height:100%`(panels.jsx:1112/1321)会塌成内容高 → 把卡内 `height:100%` 改为 `maxHeight`(如 `maxHeight:'min(640px,70vh)'`)+ 该包裹由 `flex:1` 改 `flexShrink:0`,卡按内容自然高、超高时卡内自滚仍生效;实测按真高微调。
- **边界**:只动右栏这一个 div;**不要**给中栏 K线区(app.jsx:639)或三栏行(app.jsx:636)加 overflow,否则破坏 K线/收益曲线定高。用 `overflowY:auto`(非 `overflow:auto`)避免误开横向滚动条。
- **验收**:实盘右栏(台账 + 研判流水 + 条件单 + 详情卡)同时展开、总高超视口时,右栏内纵向滚动,信息不再被顶出。

### ② 实盘单标右栏重排(精简信号队列 + 真研判流水)

- **删**:`DecisionFlow` 的实盘 scanSeat 信号队列分支(`mode==='live' && !isRun`,panels.jsx:947-998 的 live 卡片渲染)。**scanSeat 与 `symbol.decisions` 本体不删**——仍供 复盘决策流水(`DecisionFlow` 非 live 分支)、舰队信号列(③)、回测 marks(app.jsx:583-586)使用。
- **新右栏顺序(实盘单标,从上到下)**:
  1. **仓位台账** `LedgerPanel`(panels.jsx:341/513)——标题/小字明确「组合账·跨股共享(实盘=一个组合)」。
  2. **真·研判流水**(新)——读 `realDecs[code]`(app.jsx:59,真 LLM 研判:source `timer`/`manual`/`sentry`),每条带「真·LLM」徽章 + 方向/置信 + 思维链入口(复用既有 `ReasoningChain`/研判历史抽屉 `histOpen`)。**只读 realDecs,不读 scanSeat。**
  3. **条件单** `OrderWatchPanel`(panels.jsx:42/205)。
  4. **详情卡** `DecisionCard`。
- **重开账后流水重置**:真·研判流水按「最后一次开账日期」起算(只显示 `asof ≥ open.date` 的 realDecs[code]),给干净起步;**不销毁 realDecs 数据**(复盘真跑同用 realDecs[code],销毁会误伤),只做显示过滤。开账日期取台账 state 的最后 open 事件日期。
- **验收**:实盘单标右栏不再出现 scanSeat「信号队列」;真·研判流水只在有真 LLM 研判时显条目、否则诚实空;重开账后流水从新账日起算。

### ③ 舰队右栏:每股信号列

- **渲染门控改**:`view==='fleet'`(app.jsx:708-712)从「只有 FleetGrid」改为「FleetGrid(主区)+ 右侧每股信号列(新组件 `FleetSignalList`)」。三栏行(app.jsx:636)在舰队下 = 网格 + 右栏(左栏 SeatRail 在舰队是否保留沿用现状,不在本期改)。
- **`FleetSignalList`(新组件,放 luozi-fleet.jsx)**:遍历 `window.LZ_SYMBOL_META`(=盯盘池每股),每行 =
  - 股票名/代码;
  - **最新信号**:取该股 `lzRealSymbolOf(code)||LZ_SYMBOLS[code]` 的 `.decisions` 最近一笔 scanSeat(徽章「非LLM」);若该股今日(`realDecs[code]` 最新一条 asof=今日)有真 LLM 研判,**叠加「真·LLM」徽章**显其方向;
  - **盯盘/自选 徽章**(见④)。
- **交互**:点某行 = `setCode(code); setView('single')`(沿用 app.jsx:710 既有跳转)→ 切单标看单股右栏(=「点开单只右栏只显示单股」)。
- **数据**:全 per-code 现成,`realDecs` 整个对象(非 `realDecs[code]`)传进 `FleetSignalList`,scanSeat 信号每股 `buildSymbolFromBars` 已算好,无需新请求。**不动数据层。**
- **验收**:切舰队右栏出现每股信号列(网格仍在);每行信号带正确徽章;点行切到该股单标。

### ④ 自选 vs 盯盘(新特性)

- **数据模型(luozi-data.jsx)**:
  - monitored 状态用**一张独立 localStorage 映射** `guanlan:lz:monitored:v1` = `{ 归一code: true }`(只记盯盘的票,缺省=自选 false),**与 `SYMBOL_META` 池数组解耦**——这样固定 6 只底座(不在动态池数组里)与动态票统一处理,不必扩池条目结构、无向后兼容负担。code 归一沿用台账手动调仓口径(剥 SH/SZ/BJ 前缀,panels.jsx:381)。
  - 新增纯函数:`poolIsMonitored(code)`(归一后查映射,缺省 false=自选)、`setMonitored(code, bool)`(写映射 + 持久化)。挂 `window.lzPoolIsMonitored` / `window.lzSetMonitored`(luozi-data.jsx ~1598 导出区)。
  - React 看不见 localStorage 变更 → `setMonitored` 后须 `setPoolTick`(app.jsx:37 既有 hack)强制重渲染。
- **含义**:
  - **盯盘**:该票可被自动研判 + 自动落账(条件单触发成交进组合账)+ 打「盯盘」徽章。
  - **自选**:不自动研判、不自动落账,仅展示(打「自选·只看」徽章)。**手动研判 / 手动调仓 / 手动立单仍可**——不拦用户主动操作,只拦自动行为。
- **接线(前端 gate,后端台账不改)**:
  - 自动研判:`runTimedDecide` 触发前(panels.jsx:99 或 effect 门控 :183-195)加 `poolIsMonitored(code)` 判断,自选票即便聚焦 + 研判循环开,也不自动研判。
  - 自动落账:条件单触发落账 `onTrigger`(app.jsx:687)前加 `poolIsMonitored(code)` gate,自选票不自动落账。
  - 舰队:FleetCard(luozi-fleet.jsx:41)+ FleetSignalList 行加「盯盘/自选」徽章 + 切换开关(调 `setMonitored`)。
- **与「移出盯盘池」区分**:顶栏「移出盯盘池」(app.jsx:754)= 删条目;「盯/自选」开关 = 留条目改标志。二者语义独立,不复用。
- **验收**:默认全自选;打开某股「盯盘」后,它在研判循环+盘中才会自动研判/落账;自选股自动研判/落账被 gate 拦;徽章正确;持久化跨刷新。

### ⑤ 文案诚实化

- "9:30 起随行情"(panels.jsx:662)→ 改为与代码一致的描述(如「盘中 · 页面在线 · 研判循环开启后」),消除无 9:30 锚的误导。
- 舰队信号列「非LLM」徽章保留;真 LLM 用「真·LLM」徽章区分(③已含)。

## 实施顺序

1. **右栏滚动修复**(独立、最快,①)。
2. **monitored 数据模型**(④的 data 层:标志 + 函数 + 持久化 + 导出)。
3. **实盘单标右栏重排**(②:删 live 信号队列 + 真·研判流水组件 + 重开账日期过滤)。
4. **舰队右栏每股信号列**(③:`FleetSignalList` + 渲染门控)+ **盯/自选 gate 接线**(④的接线层:研判/落账 gate + 舰队徽章开关)。
5. **文案诚实化 + 真机验证**(⑤ + 浏览器端到端)。

## 不在本期范围(挂账)

- 真·多股后台轮询研判(用户选了单聚焦;monitored 标志已为将来留好「盯盘子集」遍历入口)。
- 台账改 per-code 分账(用户选维持一本组合账)。
- scanSeat / evidenceFor 合成料的彻底清理(另案,见 MEMORY luozi-fake-audit;本期只是「实盘不显示 + 舰队带非LLM徽章」)。
- 舰队左栏 SeatRail 在舰队视图的取舍(沿用现状)。

## 红线 / 契约(实施时不可破)

- scanSeat / `evidenceFor` 是确定性启发式 / 合成示意料,任何消费方(舰队信号列等)**必须保留「非LLM / 示意」徽章,绝不冒充真 agent / 真交易信号**(仓库反复强调的红线)。「真·LLM」徽章只能给 `realDecs`(真 `/seats/decide` 落盘)。
- 台账 append-only:重开账/重置靠 append 新 `open` + 重放只取最后 open 之后,**绝不就地删改历史行**;改这条会破坏 TCA 去重签名(panels.jsx:362)。
- PIT:沿用既有口径,不引入 > 决策时刻的数据。
- 工程:改 jsx 必 `bump ?v=`(用 Edit 非 sed);本期基本纯前端(monitored gate 也在前端),**无需重启 9999**;若意外触及后端再重启。
- `active`/`curSid` 是单元素数组 `[curSid]`(app.jsx:134/137),8 处依赖其形状——舰队多股各有各的首个策略(`lzStrategyForCode(code)[0]`),别把单股 active 套到多股。

## 验证计划

- **浏览器真机(preview/9999)**:① 实盘右栏内容撑高→栏内可滚、信息不被裁;② 实盘单标无 scanSeat 信号队列、真·研判流水正确(有真研判显条目、无则诚实空、重开账后从新账日起算);③ 切舰队右栏出现每股信号列、徽章正确、点行切单标;④ 默认全自选 → 开某股盯盘 → 研判循环+盘中下只该股自动研判/落账、自选股被 gate 拦、刷新后 monitored 持久。
- **无后端改动** → pytest 后端基线不受影响(若最终零 Python 改动则不跑;有改动则跑 `G:\financial-analyst\.venv\Scripts\python.exe -m pytest -q`)。
- 验证用到的台账/研判测试记录,验后清理(沿用既有纪律)。
