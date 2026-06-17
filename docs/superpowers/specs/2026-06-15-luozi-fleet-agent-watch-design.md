# 舰队多股 agent 盯盘设计(校场绑定驱动 · 页面驱动)

> 日期 2026-06-15 · 模块 ui/seats(观澜·落子)· 类型 前端特性(后端不动)

## 目标(一句话)

让用户在校场给 ~3 支票绑定 agent(策略)即把它们设为「盯盘」,舰队右栏对这些盯盘票展示其 agent 的**真实时研判**(观望/买入/卖出全显);其余票只作「自选」供点进去手动操作。盯盘的自动研判是**页面驱动**(开着页面 + 盘中才跑),诚实标注、不冒充。

## 背景与现状(读码已确认)

- **校场绑定模型**:每个策略(agent)有 `bind`(数组);`strategyForCode(code)`([luozi-data.jsx:227](../../../ui/seats/luozi-data.jsx))= `filter(s => !s.bind || s.bind.length === 0 || s.bind.indexOf(code) >= 0)`。即 `bind=[]`(不绑)=**全局**(对所有票生效);`bind=[codes]`=只对这些票。默认「动量·默认」`bind=[]` → 全局 → 现在每只票都挂着它。
- **真 LLM 研判存储**:`realDecs`(app.jsx:59)= `{code:[…]}`;`onLiveDecide`(app.jsx:305)把焦点票的定时研判写进 `realDecs[code]`(key `'true_'+seat+'@live'`)+ 已开账则落一笔 ledger decision。
- **现有单股研判循环**:`runTimedDecide`(panels.jsx:99)+ 其 effect(panels.jsx:183,门控 `loopOn && live && fresh`,按策略 clock 节流 + 10min 地板)只跑**当前聚焦的一只**。
- **`/seats/decide`** = 真 LLM 研判端点(`lzSeatDecide(payload)`)。
- **本设计取代 Task 4 的 monitored toggle**:2026-06-15 右栏重构里加的 `monitored` localStorage 映射 + `setMonitored` + 舰队 盯/自选 toggle —— 用户现确定 **校场绑定为唯一真相**,故这套 toggle 退役。

## 已锁定的设计决策(用户 2026-06-15 拍板)

| # | 决策 | 选择 |
|---|---|---|
| 1 | 盯盘集怎么定 | **校场里有 agent「显式绑定(bind 非空)」该票** = 盯盘;全局默认(bind=[])不算。退役舰队 盯/自选 toggle |
| 2 | 自动研判频率 | 沿用每支策略 clock 的 decisionFreq(hourly/daily)+ 10min 硬地板 |
| 3 | 舰队右栏布局 | 一个列表混排,徽章区分(盯盘显 agent 判断 / 自选只名字可点) |
| 4 | 驻留方式 | **页面驱动**(主开关 + 盘中 + 页面开才跑;关页面停;不动后端) |
| 5 | 自选行内容 | **只留名字/代码 + 自选徽章 + 可点**;删掉 scanSeat「非LLM」速览 |

## 分节设计

### ① 盯盘集判定(数据层 luozi-data.jsx)

- 新增纯函数:
  - `monitoredCodes()` → 所有策略「显式 bind(`Array.isArray(s.bind) && s.bind.length > 0`)」的 code 之并集(去重)。
  - `monitorAgentFor(code)` → 第一个显式绑定该 code 的策略对象(owning agent,单 agent 口径;无则 null)。
- **重写** `poolIsMonitored(code)` = `monitoredCodes().indexOf(归一code) >= 0`(即"∃ 策略显式绑了它");**不再读 localStorage**。
- **退役**:`MON_LS_KEY`/`_monLoad`/`_monSave`/`setMonitored` 删除;`lzSetMonitored` 从 window 导出移除;`lzPoolIsMonitored` 保留(重写实现)+ 新增 `lzMonitoredCodes`/`lzMonitorAgentFor` 导出。
- 归一沿用剥 `^(SH|SZ|BJ)` 前缀(与 bind 内 code 口径一致;bind 存的是 6 位纯码)。

### ② 多股盯盘循环(页面驱动 luozi-app.jsx)

- 新增 state:`const [fleetWatch, setFleetWatch] = useState(false)`(主开关,默认关,**不持久化**——刷新后需重新开,防意外后台烧 LLM)+ `const [monQuotes, setMonQuotes] = useState({})`(盯盘票实时报价 `{code: quote}`)。
- **盯盘实时报价轮询 effect(复用实时报价的落点)**:`fleetWatch` 开 → 每 ~7s 对 `monitoredCodes()` 每支 `lzFetchQuote(code)`(=现成 `/seats/quote`)→ 写 `monQuotes[code]`;关/卸载清理。**挂在 `fleetWatch` 上、与 mode 无关**——所以盯盘票各有自己的实时报价,不再依赖你正在看哪只票/哪个模式。
- 新增 `recordLiveDecide(code, agent, res)`:把一次 `/seats/decide` 成功结果写进 `realDecs[code]`(每条加 `ts: Date.now()` 供节流)+ 已开账则落 ledger decision(source `'timer'`)。`onLiveDecide`(焦点单股路径)改为调它(传焦点 code/agent),多股循环也调它。
- 新增盯盘循环 effect(依赖 `fleetWatch`、策略集变化、`monQuotes` 等):`fleetWatch` 开 → 每 60s tick → 遍历 `monitoredCodes()` 每支 code:
     - `agent = monitorAgentFor(code)`;无 → 跳过。
     - **盘中门控(逐股·真报价)**:`monQuotes[code] && monQuotes[code].fresh`(该股实时报价为今日 = 盘中);非盘中 → 跳过。**复用实时报价、与 mode 无关。**
     - **节流(跨循环去重)**:取 `realDecs[code]` 最新一条的 `ts`(**缺省 0**——复盘真跑写入的 realDecs 无 ts,视为"很久以前"、允许研判;只有本盯盘循环/单股定时研判写的才带 ts);`now - ts < 600000`(10min 地板)→ 跳过;再按 `agent.clock.decisionFreq` 判到点(`hourly`: `now - ts >= 3600000`;`daily`: `ts` 不在今天 / ts=0)→ 未到点跳过。(读 realDecs 的 ts 而非单独 ref,天然与单股 `runTimedDecide` 去重——谁先研判都更新 ts。)
     - 到点 → 用 agent 的信条/配方(`lzRecipeForStrategy(agent.id)`)/`pa`/`paMethod`/`w` 构 payload(镜像 runTimedDecide 的 payload),`lzSeatDecide(payload)` 真调;成功 → `recordLiveDecide(code, agent, res)`。串行或小并发(≤3 票,串行即可),失败该票跳过不写。
- 与现有单股 `runTimedDecide` 并存:都写 realDecs[code] 带 ts,共享 10min 地板 → 聚焦的盯盘票不会被两边重复研判。

### ③ 舰队右栏(混排徽章 luozi-fleet.jsx 的 FleetSignalList)

- 头部加**主开关**:`○ 开始盯盘` / `● 盯盘中 · N 支`(N=`monitoredCodes().length`);开启态副标「页面开着 + 盘中自动研判 · 关页面即停」(tooltip 说明页面驱动局限)。点击 = `onToggleWatch`(app 传入,翻 `fleetWatch`)。
- 列表每支池内票一行(`window.LZ_SYMBOL_META`):
  - **盯盘票**(`lzPoolIsMonitored(code)`):显 owning agent(`lzMonitorAgentFor(code)`)名 +「盯盘·真·LLM」徽章 + 该票 `realDecs[code]` 最新真研判(方向 观望/买入/卖出 + 置信 + 时间)。还没研判 → 「盯盘中 · 待研判」。
  - **自选票**:「自选」徽章 + 名/代码,**无 scanSeat、无判断**,整行可点。
  - 全行点击 → `onPick(code)`(`setCode + setView('single')`)进单标手动操作。
- **删**:FleetSignalList 里 scanSeat 速览(scan 取值 + 非LLM 徽章 + note)与 盯/自选 toggle chip。

### ④ FleetCard(网格卡)与落账闸门

- FleetCard 的 盯/自选 chip 改为**只读徽章**(`lzPoolIsMonitored(code)` 派生:盯盘/自选),去掉 `onToggleMon` 点击切换(校场绑定为真相)。`FleetGrid`/`FleetCard` 的 `onToggleMon` 形参移除。
- `onTrigger` 条件单自动落账(app.jsx:687)的 monitored 闸门**保留**,其 `lzPoolIsMonitored` 已重写为绑定派生 → 语义不变(自选票不自动落账)、来源换。

### ⑤ 诚实 / 红线

- 「真·LLM」徽章只给 `realDecs` 真研判(`/seats/decide` 成功落的);自选行不显任何"信号"(避免误读)。
- **页面驱动局限明示**:主开关旁标「关页面即停 · 无后端定时器」。
- **成本**:盯盘票越多 LLM 调用越多(~3 支 + 按小时 ≈ 每小时 3 次);主开关默认关、opt-in,防意外烧。
- PIT 沿用既有 decide 口径;台账 append-only 不就地改。

## 实施顺序

1. 数据层:`monitoredCodes`/`monitorAgentFor` + `poolIsMonitored` 重写 + 退役 setMonitored/localStorage(luozi-data.jsx)。
2. app.jsx:`fleetWatch` state + `recordLiveDecide` 泛化 + onLiveDecide 改调它 + 盯盘循环 effect + 盘中时间门控。
3. fleet.jsx:FleetSignalList 重排(主开关 + 盯盘行 agent 判断 + 自选行只名字 + 删 scanSeat/toggle)+ FleetCard chip 只读化 + 移除 onToggleMon。
4. app.jsx 舰队分支:传 `fleetWatch`/`onToggleWatch` 给 FleetSignalList,移除 onToggleMon 传参。
5. 收口:浏览器真机(校场绑定 1-2 支 → 舰队显盯盘行 + 主开关 + 真研判;自选只名字可点)+ README + memory。

## 不在本期范围(挂账)

- 真后端 scheduler(无人值守关页面也跑)—— 用户选页面驱动,留挂账。
- 多 owning agent 合议(一票多绑时只显第一个 agent,单 agent 口径)。
- 盯盘实时报价轮询(复用 `/seats/quote`)= 盯盘票数 × 每 ~7s;~3 支可忽略,票多时轮询批量/频率另议。
- 盯盘研判结果的历史留存查看(realDecs 内存态,刷新清;后端 `/seats/decisions` 有持久历史但本期舰队不读)。

## 红线 / 契约(实施不可破)

- scanSeat / evidenceFor 是启发式 / 合成示意料;本期自选行**直接不显**这些(避免误读),盯盘行只显真 realDecs。「真·LLM」徽章绝不给非 realDecs。
- 校场绑定 = 盯盘唯一真相;不再有独立 toggle 真相源。
- LLM 失败不写 realDecs、不落账(诚实降级)。
- 改 jsx 必 bump `?v`;本期纯前端、无需重启 9999。

## 验证计划

- **浏览器真机(preview→9999,resize 1440×900)**:① 校场给某票显式绑一个 agent → 舰队该票变「盯盘」行(显 agent 名 + 盯盘徽章),未研判显「待研判」;未绑的票 = 「自选」只名字可点。② 主开关默认关;开「开始盯盘」→ 盘中时对盯盘票真调 `/seats/decide`(可注入 stub 验 payload 携 agent 信条/配方,或真跑 1 票验 realDecs[code] 写入 + 舰队显真方向),验后清测试态。③ 点自选行 → 切单标。④ 控制台 0 报错。⑤ 盘中门控:逐股按实时报价 fresh 判定(`monQuotes[code].fresh` 为假 = 非盘中,主开关开也不研判)。
- 盘中真触发端到端只能交易时段验;非盘中(报价非今日)用「门控早返 + payload 构造(stub 截获)」验。
- **无后端改动** → pytest 基线不受影响(零 Python)。
