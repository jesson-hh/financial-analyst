# 观澜 · 落子 第3期 —— 校场自命名按票策略实例(StrategyInstance)细化设计

- 日期:2026-06-08
- 上位设计:`docs/superpowers/specs/2026-06-08-luozi-live-trading-engine-design.md`(§2.2 / §2.3 / §4 第3期)
- 模块:`ui/seats/`(前端,无 build)+ `ui/_shared/guanlan-bus.js`(共享档案库)
- 状态:细化设计,待用户评审 → 出实施计划
- 前置:第1期研判循环 ✅、第2期 B1 影子组合 + 持仓感知研判 ✅、单席位化(`?v=d27`,固定4席→只留动量席)✅

---

## 1. 目标与背景

把固定席位泛化为**用户自命名、配方、设时钟、绑定具体股票的策略实例(StrategyInstance)**,校场按时钟周期演武回测验证。这是第3期、最大的结构改动。

第1/2期已按"泛化 id、不写死4席"设计(影子持仓的 `seat` 字段、`/seats/order` 的 `seat` 参数都是 id 口径),因此第3期**只替换"策略来源"**——把"4 个写死的席位"换成"GL 里的 strategy 实例集"——前两期逻辑不返工。

### 现状(本设计要改造的)
- 席位有**两套并行概念**,靠命名桥接:
  - **桌面席位** `LZ_SEATS`(luozi-data.jsx):携带 `scanSeat` 启发式 + 颜色 + `perSeat` 回测;驱动 K 线落子/合议。单席位化后只剩 `momentum` 一席(全量定义保留在 `SEATS_ALL`)。
  - **校场席位** = `window.GL` 的 `type:'seat'` 实体(`seat_momentum` 等,seed 于 [guanlan-bus.js:87](../../ui/_shared/guanlan-bus.js)),含 `refs`(指向 card/factor)。foundry 经 `SEAT_LZ` 映射回桌面 lzId 取回测。
- `scanSeat(bars, seat)`([luozi-data.jsx:141](../../ui/seats/luozi-data.jsx))**按 `seat.id` 硬分支**(momentum/reversal/event/risk),每支自带进场规则 + 写死出场阈值(如动量席 `entryPrice*0.92` 止损、`dead cross` 出场)。
- `window.GL`([guanlan-bus.js](../../ui/_shared/guanlan-bus.js)):共享档案库,持久化于 `localStorage['guanlan:store:v1']`,CRUD(`put/patch/get/all/remove/link`)+ 发布订阅 + 跨标签同步。实体 `{id, type, title, refs, ...}`,类型 research/factor/card/seat。

---

## 2. 核心设计决策

### D1. 信号源 = 模板启发式 + 用户时钟(诚实路径)
一个自命名策略的**确定性落子/演武回测**来自:
- **进场规则**:`template ∈ {momentum, reversal, event}` —— 沿用现有 `scanSeat` 三个分支的进场逻辑(MA 上穿/超跌缩量企稳/事件跳空)。`risk`(风控)不作独立模板,降为可选 overlay 标记(本期可不做)。
- **出场规则**:`clock.{stopLoss, takeProfit, maxHold}` 参数化 —— 替换分支里写死的止损/止盈/持有 bar 数阈值;模板原有的"形态出场"(如 momentum 的 MA 死叉)保留为附加出场条件。
- **扫描粒度**:`clock.execTF`(day / 5min)决定喂给 scanSeat 的 bar 序列。
- **配方(cards/factors/research)= 依据,不参与确定性信号**:在策略卡上展示 + 喂给 LLM 研判(`/seats/order` 的上下文),给用户"为什么这么配"的可解释性。**绝不**假装前端能跑任意因子配方回测(前端引擎只能跑这些确定性启发式)——延续诚实红线。

> 取舍理由:前端 no-build React 模块无法逐 bar 评估后端因子 DSL;LLM-only 信号每次不同、演武无可回测基准。模板+时钟是唯一既诚实又可演武的路径。"自命名/配方/绑票/时钟"提供用户要的定制,确定性引擎诚实地只跑它能跑的。

### D2. 数据模型 —— GL 新增 `type:'strategy'` 实体
```js
StrategyInstance = {
  id,            // 'strat_xxx'(GL.genId)
  type: 'strategy',
  name,          // 用户自命名,如「宁德·突破回踩」
  template,      // 'momentum' | 'reversal' | 'event'(信号引擎)
  refs: [ ... ], // 配方:card/factor/research 的 id(复用 GL.link / refs)
  clock: {
    execTF: 'day' | '5min',   // 决策 K 线粒度(≠ 看盘 TF)
    decisionFreq,             // 决策频率枚举(本期仅存,节拍仍每小时封顶)
    maxHold,                  // 最长持有(bar 数)→ 时间止盈
    stopLoss,                 // 止损 %(正数,如 0.08)
    takeProfit                // 止盈 %(正数,如 0.18)
  },
  bind: [code, ...],          // 绑定标的(空 = 未绑/全局可选)
  color, glyph,               // 显示(模板默认色/字,可改)
  ts
}
```
- **落点 `window.GL`**:复用其 CRUD + localStorage 持久 + 跨标签同步;新增类型不破坏现有 seed/card/factor。
- **旧 4 席 seed 退役为"模板库"**:`SEATS_ALL`(luozi-data)保留作 `scanSeat` 的 template 规则来源 + 模板默认色/字/creed;GL 里旧 `seat_*` 实体保留(向后兼容,不读为策略)。
- `decisionFreq` 本期**只存不驱动**(定时研判仍 every-hour 封顶,见范围外)。

### D3. 桌面集成(按票)
- 当前 `code` 的**在场策略** = `GL.all('strategy').filter(s => s.bind.includes(code))`(无则回退默认策略,见 D6)。
- `SeatRail` 列出这些策略(替代固定 `LZ_SEATS`);**合议 = 它们的等权收益组合**(`consensusEquity` 复用,id 换成 strategy.id)。
- `perSeat` 改由 `scanSeat(bars, strategy)` 按 **strategy.id** 装配(`buildSymbolFromBars` 遍历当前 code 的在场策略而非 SEATS);K 线落子按 strategy 上色。
- `seatColor(id)` 泛化:strategy 有 `color` 字段则用之,否则按 template 取模板色。

### D4. 演武(foundry,按 clock 回测)
- `seatArena` 泛化为 `strategyArena(strategy)`:用 `strategy.template + strategy.clock` 跑 `scanSeat`,在 `strategy.bind`(空则全部标的)上回测,聚合跨标的收益/Sharpe/胜率/盈亏比。
- **回显平均持有期**(成交 `out-in` 的均值 bar 数)校验 `clock.maxHold` 是否合理(用户定周期、数据验证它)。
- 装配 UI:新建/命名 + 模板选择 + 配方拖拽(沿用现有三栏料抽屉)+ 时钟控件(execTF / decisionFreq / maxHold / stopLoss / takeProfit)+ 绑票多选 → `GL.put`。基础管理:改名 / 删除 / 复制为新策略。

### D5. 研判循环 + 影子组合接入
- `OrderWatchPanel` 的席位选择换成**当前 code 的在场策略**列表;选中策略 → `/seats/order` 传:`seat=strategy.template`(后端 `_CREEDS[template]` 仍命中,creed 正确)、附带 `name`(显示用)、`clock.stopLoss/takeProfit`(影响止损止盈位)。**后端本期不改**(creed 走 template;自定义 creed 留后)。
- 影子持仓 `seat` 字段 = **strategy.id**(`shadowAddEntry(ev)` 已用 `ev.seat`),天然实现 spec §2.2 的 per-(策略,票)跟踪。研判平仓/持仓感知按 strategy.id 匹配 `myHold`。

### D6. 迁移 / 向后兼容
- **默认策略 seed**:首载若 GL 无任何 `strategy`,种一个 `{template:'momentum', name:'动量·默认', bind:[全部关注票 code], clock:模板默认值}`,使桌面非空、行为延续单席位化后的现状。
- **旧影子台账**(`guanlan:lz:shadow:<code>`,按 code)继续用;持仓里旧 `seat:'momentum'` 与默认策略的 template 一致,显示不断裂。
- `LZ_KEPT_SEATS`(单席位化引入)退役;`SEATS_ALL` 转为模板库。

---

## 3. 文件触点(预估)

| 文件 | 改动 |
|---|---|
| `ui/_shared/guanlan-bus.js` | `stats()` 加 strategy 计数;(可选)seed 默认策略的兜底——优先放前端 data.jsx 避免动共享总线 |
| `ui/seats/luozi-data.jsx` | `scanSeat(bars, strategy)` 泛化(template 进场 + clock 出场);`buildSymbolFromBars` 按"当前 code 在场策略"装配 perSeat;模板库 `SEATS_ALL`→template 元数据;默认策略 seed;`seatColor` 泛化;导出 strategy 助手(列举/CRUD 薄封装 GL) |
| `ui/seats/luozi-app.jsx` | `active`/`ALL_SEATS` 换成"当前 code 在场策略 id";`consensusMetrics` 用 strategy id;`equityLines`;`realSyms` 装配联动策略变化 |
| `ui/seats/luozi-panels.jsx` | `SeatRail` 渲染策略行;`OrderWatchPanel` 席位选择换成策略选择 + 传 template/name/clock;`SeatRow`/`SeatCN` 泛化 |
| `ui/seats/luozi-foundry.jsx` | 新建/命名/模板/时钟/绑票 装配 UI;`strategyArena` 按 clock 回测 + 平均持有回显;演武排行榜按策略;基础管理(改名/删/复制) |
| `观澜 · 落子.html` | `?v` bump |

> 红线:`guanlan-bus.js` 是四模块共享总线,**尽量不动**(只在必要时加 strategy 计数);策略 seed/助手优先放 `luozi-data.jsx`。

---

## 4. 范围

### 本期(MVP)
GL `strategy` 模型;foundry 新建/命名/模板/配方/时钟/绑票 → 保存;`scanSeat` 泛化(template+clock);桌面按 code 显示在场策略 + 合议;演武按 clock 回测 + 平均持有回显;研判/影子接 strategy.id;默认策略迁移;基础策略管理(改名/删除/复制)。

### 范围外(留后续)
- `decisionFreq` 真正驱动定时研判节拍(现仍每小时封顶 —— 第1期实现)。
- 后端 `/seats/order` 自定义 creed(本期 creed 走 template)。
- 策略级**独立**影子指标卡(本期影子仍按 code 聚合;跨票聚合「本票/组合」已在 d26)。
- 后端/跨设备持久(本期 localStorage)。
- `execTF='5min'` 的演武(日内回测;本期 execTF 存储 + day 路径,5min 演武留后)。
- 风控 overlay 作为可叠加层(本期模板只 momentum/reversal/event)。

---

## 5. 关键不变量 / 红线
- **诚实**:确定性落子/演武只跑模板+时钟能跑的;配方因子是依据/LLM 上下文,不冒充因子回测。无数据→`—`/空态,不编造。
- **系统不代下单**:沿用;研判只出信号。
- **泛化 id**:全程 strategy.id,绝不写死席位名。
- **布局**:沿用现有 foundry/SeatRail 布局扩展,不另起炉灶(填充扩展、非重建)。
- **共享总线最小改动**:策略数据虽落 GL,但 seed/助手逻辑放 data.jsx,`guanlan-bus.js` 只做必要的计数兜底。

---

## 6. 验收
- 可在校场新建命名策略:起名 + 选模板 + 拖配方 + 设时钟(止损/止盈/最长持有/执行TF/频率)+ 绑票 → 保存,刷新不丢(GL localStorage)。
- 桌面切到某绑定票:SeatRail 显示该票在场策略、合议 = 它们组合;K 线落子按策略;切不同 clock 的策略,演武/出场行为随之变。
- 演武:用策略 template+clock 跑出按周期的真回测 + 平均持有期回显;排行榜按策略。
- 研判:OrderWatchPanel 选该票策略,`/seats/order` 真出单(creed 走 template);影子持仓按 strategy.id 记;持仓感知研判/平仓按策略匹配。
- 默认策略迁移:首载桌面非空,旧影子不断裂。
- 第1/2期逻辑无返工接入;0 console error;Chrome MCP @9999 浏览器实测。
