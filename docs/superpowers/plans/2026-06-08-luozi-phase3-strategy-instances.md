# 落子第3期 · 自命名按票策略实例 — 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把固定席位泛化为用户在校场自命名、配方、设时钟、绑票的策略实例(StrategyInstance),桌面按票显示策略+合议,演武按时钟回测。

**Architecture:** 策略数据落 `window.GL`(共享档案库,localStorage 持久);确定性落子/演武 = `template`(动量/反转/事件 进场规则)+ `clock`(止损/止盈/最长持有出场)真算;配方(卡/因子/研报)只作依据展示 + 喂 LLM。前两期(影子/研判)按 `strategy.id` 接入,逻辑不返工。

**Tech Stack:** 无 build 前端 React 18 UMD + @babel/standalone(浏览器内编译);`ui/seats/*.jsx`(`Object.assign(window,…)` 导出)+ `ui/_shared/guanlan-bus.js`(`window.GL`)。

**Spec:** `docs/superpowers/specs/2026-06-08-luozi-phase3-strategy-instances-design.md`

---

## 本项目特例(执行者必读)

- **无测试框架**:本仓没有 pytest/jest。"测试" = ① 纯函数 → 浏览器 console `mcp__Claude_in_Chrome__javascript_tool` eval 喂合成数据断言(像 d26 验 `shadowAggregate` 那样);② UI → Chrome MCP `read_console_messages`(0 error)+ `screenshot`/`zoom`。页面由 9999 服务:`http://127.0.0.1:9999/ui/seats/%E8%A7%82%E6%BE%9C%20%C2%B7%20%E8%90%BD%E5%AD%90.html`。
- **无 git**(本仓非 git 仓)。"提交" = **bump `观澜 · 落子.html` 的 `?v`** + 浏览器实测。当前 `?v=20260608d27` → 本期递增到 `d28`(Task 7 统一 bump;但每 Task 必须 reload 后 console 0 error)。**改 `?v` 用 Edit 工具,绝不用 sed**(sed 会把中文 HTML 改成乱码——历史教训)。
- **babel 异步编译坑**:reload 后立即 eval `window.lz*` 可能 undefined;先等 ~2s 或轮询 `typeof window.lzStrategyList==='function' && document.getElementById('root').children.length>0` 再验。
- **GateGuard**:每个文件首次 Edit 前需陈述 4 事实(谁 import / 受影响函数 / 读写数据结构 / 用户原话)。
- **红线**:诚实不编造(无数据→`—`/空态);系统不出代下单;全程 `strategy.id` 不写死席位名;沿用现有布局扩展不重建;`guanlan-bus.js` 共享总线尽量不动。
- **验证用合成数据注入 localStorage 后必须精确还原**(快照→注入→验→还原),不污染用户真实 GL/影子数据。GL 键 `guanlan:store:v1`,影子键 `guanlan:lz:shadow:<code>`。

---

## 文件结构

| 文件 | 责任 | 本期改动 |
|---|---|---|
| `ui/seats/luozi-data.jsx` | 数据/算法内核 | 模板库 `LZ_TEMPLATES`、策略 CRUD 助手(封装 GL)、默认策略种子、`scanSeat` 泛化、`buildSymbolFromBars` 接策略参数、`evidenceFor` 泛化、导出 |
| `ui/seats/luozi-chart.jsx` | 图表 + `seatColor` | `seatColor` 泛化到策略色 |
| `ui/seats/luozi-app.jsx` | 主壳 | 当前 code 在场策略 state(订阅 GL)、`symbol` 按策略反应式重建、合议按 strategy.id、`equityLines` |
| `ui/seats/luozi-panels.jsx` | 面板 | `SeatRail` 渲染策略行、`SeatRow` 泛化、`OrderWatchPanel` 策略选择 + 传 template/name/clock |
| `ui/seats/luozi-foundry.jsx` | 校场 | 新建/命名/模板/配方/时钟/绑票 装配 UI、`strategyArena` 按 clock、排行/管理 |
| `观澜 · 落子.html` | 入口 | `?v` bump |

---

## Task 1: 模板库 + 策略数据层(GL CRUD + 默认种子)

**Files:**
- Modify: `ui/seats/luozi-data.jsx`(在 `SEATS` 定义之后插入模板库;助手定义在 `SYMBOLS` 构建之前;`Object.assign(window,…)` 增导出)

纯数据层新增,不改桌面行为(桌面仍走旧 `SEATS` 路径)。

- [ ] **Step 1: 加模板库 `LZ_TEMPLATES`**(插在 `const SEATS = SEATS_ALL.filter(...)` 之后)

```js
// ───────── 模板库(第3期:策略实例的信号引擎 = 模板进场规则 + 用户时钟出场)─────────
// 默认 clock 近似各模板原 scanSeat 的写死阈值;用户在校场可改。
const LZ_TEMPLATES = {
  momentum: { cn: '动量突破', glyph: '动', color: 'var(--jin)', creed: '突破均线、量价齐升则顺势加仓', card: '北向资金领先',
    clock: { execTF: 'day', decisionFreq: 'hourly', maxHold: 30, stopLoss: 0.08, takeProfit: 0.18 } },
  reversal: { cn: '超跌反转', glyph: '反', color: 'var(--zhu)', creed: '超跌缩量企稳即落子,搏短线反弹', card: '缩量反转',
    clock: { execTF: 'day', decisionFreq: 'daily', maxHold: 13, stopLoss: 0.05, takeProfit: 0.11 } },
  event: { cn: '事件驱动', glyph: '事', color: '#3f6f8a', creed: '业绩超预期后博 60 日漂移', card: '业绩漂移 PEAD',
    clock: { execTF: 'day', decisionFreq: 'daily', maxHold: 22, stopLoss: 0.09, takeProfit: 0.26 } },
};
const LZ_TEMPLATE_IDS = ['momentum', 'reversal', 'event'];   // 风控本期不作独立模板(范围外)
```

- [ ] **Step 2: 加策略 CRUD 助手 + 默认种子**(插在 Step 1 之后、`SYMBOLS` 构建之前)

```js
// ───────── 策略实例(StrategyInstance)= GL type:'strategy' 实体 ─────────
function _normClock(c) {
  const d = LZ_TEMPLATES.momentum.clock;
  c = c || {};
  return {
    execTF: c.execTF === '5min' ? '5min' : 'day',
    decisionFreq: c.decisionFreq || 'hourly',
    maxHold: (c.maxHold != null && isFinite(+c.maxHold)) ? +c.maxHold : d.maxHold,
    stopLoss: (c.stopLoss != null && isFinite(+c.stopLoss)) ? +c.stopLoss : d.stopLoss,
    takeProfit: (c.takeProfit != null && isFinite(+c.takeProfit)) ? +c.takeProfit : d.takeProfit,
  };
}
function strategyList() { return (window.GL ? window.GL.all('strategy') : []); }
function strategyGet(id) { return (window.GL ? window.GL.get(id) : null); }
// 当前票在场策略 = 绑定该票 + 全局(bind 空)
function strategyForCode(code) {
  return strategyList().filter(s => !s.bind || s.bind.length === 0 || s.bind.indexOf(code) >= 0);
}
function strategyColor(id) {
  const s = strategyGet(id);
  if (s && s.color) return s.color;
  if (s && s.template && LZ_TEMPLATES[s.template]) return LZ_TEMPLATES[s.template].color;
  const seat = (SEATS_ALL || []).find(x => x.id === id);   // 兼容旧席位 id
  return seat ? seat.color : 'var(--ink-2)';
}
// 保存/新建(无 id 则生成);规整 template/clock/bind/color/glyph
function strategySave(o) {
  if (!window.GL) return null;
  const tmpl = LZ_TEMPLATE_IDS.indexOf(o.template) >= 0 ? o.template : 'momentum';
  const td = LZ_TEMPLATES[tmpl];
  const obj = {
    id: o.id || ('strat_' + Date.now().toString(36) + Math.floor(Math.random() * 1e4).toString(36)),
    type: 'strategy', name: o.name || td.cn, template: tmpl,
    refs: Array.isArray(o.refs) ? o.refs : [],
    clock: _normClock(o.clock || td.clock),
    bind: Array.isArray(o.bind) ? o.bind.slice() : [],
    color: o.color || td.color, glyph: o.glyph || td.glyph,
  };
  window.GL.put(obj);
  return obj.id;
}
function strategyDelete(id) { if (window.GL) window.GL.remove(id); }
// 默认策略种子:GL 无任何 strategy 时,种一个全局动量默认策略(承接单席位化后的现状)
function seedDefaultStrategy() {
  if (!window.GL) return;
  if (strategyList().length > 0) return;
  strategySave({ name: '动量 · 默认', template: 'momentum', bind: [], refs: ['card_north', 'fa_north'],
    clock: LZ_TEMPLATES.momentum.clock });
}
```

- [ ] **Step 3: `SYMBOLS` 构建前种默认策略**(在 `const SYMBOLS = {};` 之前一行)

```js
try { seedDefaultStrategy(); } catch (e) {}     // 必须在 SYMBOLS 构建前(buildSymbol 需默认策略)
```
（注:文件末原有的 `try { syncArchive(); } catch(e){}` 保留;此处是**额外**提前的一次 seed。)

- [ ] **Step 4: 导出**(在末尾 `Object.assign(window, {...})` 块内加)

```js
  lzStrategyList: strategyList, lzStrategyGet: strategyGet, lzStrategyForCode: strategyForCode,
  lzStrategyColor: strategyColor, lzStrategySave: strategySave, lzStrategyDelete: strategyDelete,
  lzSeedDefaultStrategy: seedDefaultStrategy, LZ_TEMPLATES: LZ_TEMPLATES, LZ_TEMPLATE_IDS: LZ_TEMPLATE_IDS,
  lzScanSeat: scanSeat, lzSeatEquity: seatEquity, lzBuildSymbolFromBars: buildSymbolFromBars,
```
（`lzBuildSymbolFromBars` 若已导出则不重复;`lzScanSeat`/`lzSeatEquity` 为后续 Task 验证/演武需要。)

- [ ] **Step 5: 浏览器 console 验证(快照→注入→断言→还原)**

reload 9999,等就绪后 `javascript_tool` eval:

```js
const out = {};
out.tmpl = Object.keys(window.LZ_TEMPLATES);                 // ["momentum","reversal","event"]
out.defaultSeeded = window.lzStrategyList().length >= 1;     // true
const def = window.lzStrategyList().find(s => s.template==='momentum');
out.defaultGlobal = !!def && (!def.bind || def.bind.length===0); // true
out.forCode = window.lzStrategyForCode('300750').length >= 1;  // true(全局策略每票可见)
const tid = window.lzStrategySave({ name:'测试·只绑BYD', template:'reversal', bind:['002594'], clock:{stopLoss:0.06,takeProfit:0.1,maxHold:8} });
out.boundOnlyBYD = window.lzStrategyForCode('002594').some(s=>s.id===tid) && !window.lzStrategyForCode('600519').some(s=>s.id===tid);
out.clockKeys = Object.keys(window.lzStrategyGet(tid).clock).sort();
window.lzStrategyDelete(tid);                                 // 还原
out.cleaned = !window.lzStrategyGet(tid);
JSON.stringify(out);
```
Expected:`tmpl=["momentum","reversal","event"]`;`defaultSeeded/defaultGlobal/forCode/boundOnlyBYD/cleaned` 全 true;`clockKeys=["decisionFreq","execTF","maxHold","stopLoss","takeProfit"]`。`read_console_messages` 0 error。

- [ ] **Step 6: checkpoint** — console 全绿即过(桌面外观此 Task 不变)。

---

## Task 2: `scanSeat` 泛化(template 进场 + clock 出场)

**Files:**
- Modify: `ui/seats/luozi-data.jsx`(`evidenceFor` [luozi-data.jsx:108]、`scanSeat` [luozi-data.jsx:141])

向后兼容:旧 `SEATS` 元素(只有 `id`)仍能跑——`template=id`、`clock=模板默认`。

- [ ] **Step 1: `evidenceFor` 泛化**(把 `seat.id` 索引换成模板)

在 [luozi-data.jsx:108] `evidenceFor` 内:增 `const tmpl = seat.template || seat.id;`;`factors.combo` 的 `seat.id === 'momentum'` 改 `tmpl === 'momentum'`;`research: RESEARCH[seat.id]` 改 `research: RESEARCH[tmpl] || RESEARCH.momentum`;`card: { name: seat.card, hint: CARDHINT[seat.id] }` 改 `card: { name: seat.card || (LZ_TEMPLATES[tmpl] && LZ_TEMPLATES[tmpl].card) || tmpl, hint: CARDHINT[tmpl] || CARDHINT.momentum }`。RESEARCH/CARDHINT 字面量(键 momentum/reversal/event/risk)不变。

- [ ] **Step 2: 重写 `scanSeat`**(整体替换 [luozi-data.jsx:141-202])

```js
// ───────── 单策略扫描 → 落子(template 进场 + clock 出场;buy/sell 成对)─────────
function scanSeat(bars, strat) {
  const tmpl = strat.template || strat.id;                  // 兼容旧 SEATS(id 即模板)
  const sid = strat.id || tmpl;
  const td = LZ_TEMPLATES[tmpl] || LZ_TEMPLATES.momentum;
  const clk = _normClock(strat.clock || td.clock);
  const stopPct = clk.stopLoss, takePct = clk.takeProfit, maxHold = clk.maxHold;
  const ds = [];
  const n = bars.length;
  let holding = false, entryIdx = -1, entryPrice = 0;
  const push = (idx, side, conf, size, extra) => {
    const b = bars[idx];
    ds.push(Object.assign({
      seat: sid, idx, date: b.date, side, price: b.c, conf, size,
      stop: side === 'buy' ? +(b.c * (1 - stopPct)).toFixed(2) : null,
      take: side === 'buy' ? +(b.c * (1 + takePct)).toFixed(2) : null,
      ev: evidenceFor(strat, bars, idx, true),
    }, extra || {}));
  };
  // 通用出场:止损 / 止盈 / 最长持有(任一触发即平)
  const exitHit = (i) => bars[i].c <= entryPrice * (1 - stopPct) || bars[i].c >= entryPrice * (1 + takePct) || (i - entryIdx) >= maxHold;
  for (let i = 6; i < n; i++) {
    const ma5 = sma(bars, 5, 'c', i), ma20 = sma(bars, 20, 'c', i);
    const ma5p = sma(bars, 5, 'c', i - 1), ma20p = sma(bars, 20, 'c', i - 1);
    const vm = volMA(bars, 10, i), r5 = ret5(bars, i);
    if (tmpl === 'momentum') {
      const cross = ma5 && ma20 && ma5 > ma20 && ma5p <= ma20p;
      const dead = ma5 && ma20 && ma5 < ma20 && ma5p >= ma20p;
      if (!holding && cross && bars[i].c > ma20 && bars[i].v > vm * 1.05) {
        push(i, 'buy', 0.7 + Math.min(0.2, r5 * 2), 0.6, { note: 'MA5 上穿 MA20,量价齐升,顺势进场。' });
        holding = true; entryIdx = i; entryPrice = bars[i].c;
      } else if (holding && (dead || exitHit(i))) {
        push(i, 'sell', 0.66, 0, { note: dead ? 'MA5 下破 MA20,动量转弱,撤。' : '触止损/止盈/到期,离场。' });
        holding = false;
      }
    } else if (tmpl === 'reversal') {
      const turn = bars[i].c > bars[i - 1].c && bars[i - 1].c <= bars[i - 2].c;
      const belowTrend = ma20 && bars[i].c < ma20 * 0.96;
      if (!holding && r5 < -0.05 && belowTrend && bars[i].v < vm * 1.0 && turn) {
        push(i, 'buy', 0.62 + Math.min(0.22, -r5), 0.5, { note: '五日超跌 ' + (r5 * 100).toFixed(1) + '%,缩量收红转拐,左侧企稳。' });
        holding = true; entryIdx = i; entryPrice = bars[i].c;
      } else if (holding && exitHit(i)) {
        const win = bars[i].c >= entryPrice;
        push(i, 'sell', 0.6, 0, { note: win ? '已达反弹目标/到期,落袋。' : '跌破止损/到期,纪律离场。' });
        holding = false;
      }
    } else if (tmpl === 'event') {
      if (!holding && bars[i].event) {
        push(i, 'buy', 0.82, 0.55, { note: '业绩超预期跳空,博 PEAD 漂移。' });
        holding = true; entryIdx = i; entryPrice = bars[i].c;
      } else if (holding && exitHit(i)) {
        push(i, 'sell', 0.6, 0, { note: (i - entryIdx) >= maxHold ? '漂移窗口结束,兑现。' : '止损/止盈离场。' });
        holding = false;
      }
    }
    // 风控 overlay 本期不作独立模板(范围外)
  }
  return ds;
}
```

- [ ] **Step 3: console 验证 clock 真生效**(合成 bar,紧止盈应更早平)

```js
const bars = []; let p = 100;
for (let i=0;i<60;i++){ p *= (1 + (i<30? -0.005 : 0.02)); const c=+p.toFixed(2); bars.push({ i, date:'2026-01-'+String((i%28)+1).padStart(2,'0'), o:c, h:c*1.01, l:c*0.99, c, v:1000*(1+(i%5)) }); }
const holdAvg = ds => { let pairs=[],open=null; ds.forEach(d=>{ if(d.side==='buy')open=d.idx; else if(d.side==='sell'&&open!=null){pairs.push(d.idx-open);open=null;} }); return pairs.length? pairs.reduce((a,b)=>a+b,0)/pairs.length : null; };
const tight = window.lzScanSeat(bars, { id:'t1', template:'momentum', clock:{ stopLoss:0.05, takeProfit:0.06, maxHold:50 } });
const wide  = window.lzScanSeat(bars, { id:'t2', template:'momentum', clock:{ stopLoss:0.20, takeProfit:0.50, maxHold:50 } });
JSON.stringify({ tightHold: holdAvg(tight), wideHold: holdAvg(wide), tighterExitsSooner: (holdAvg(tight)||99) <= (holdAvg(wide)||99) });
```
Expected:`tighterExitsSooner: true`(紧止盈持有更短)。

- [ ] **Step 4: 验旧桌面不破**(reload,console)`window.LZ_SEATS=["momentum"]`;桌面动量席合议数字仍出现;`read_console_messages` 0 error。

- [ ] **Step 5: checkpoint**。

---

## Task 3: `buildSymbolFromBars` 接策略参数 + `seatColor` 泛化

**Files:**
- Modify: `ui/seats/luozi-data.jsx`(`buildSymbolFromBars` [luozi-data.jsx:280]、`SYMBOLS` 构建 [luozi-data.jsx:312])
- Modify: `ui/seats/luozi-chart.jsx`(`seatColor` [luozi-chart.jsx:20])

- [ ] **Step 1: `buildSymbolFromBars` 接 `strategies` 参数**(替换 [luozi-data.jsx:280-293])

```js
function buildSymbolFromBars(meta, bars, strategies) {
  // strategies 省略时:用该票在场策略(默认策略全局可见);再退化到旧 SEATS 防早期空。
  const strats = (strategies && strategies.length) ? strategies
    : (strategyForCode(meta.code).length ? strategyForCode(meta.code) : SEATS);
  const decisions = [];
  const perSeat = {};
  strats.forEach(s => {
    const sid = s.id || s.template;
    const ds = scanSeat(bars, s);
    decisions.push(...ds);
    perSeat[sid] = seatEquity(bars, ds, sid);
    perSeat[sid].metrics = metricsOf(perSeat[sid].eq, perSeat[sid].trades);
  });
  decisions.sort((a, b) => a.idx - b.idx || String(a.seat).localeCompare(String(b.seat)));
  decisions.forEach(d => { d.key = d.seat + '@' + d.idx; });
  const bench = benchmark(bars, meta.seed || 1);
  return { meta, bars, decisions, perSeat, bench, stratIds: strats.map(s => s.id || s.template) };
}
```

- [ ] **Step 2: 确认 seed 在 `SYMBOLS` 构建前**(Task 1 Step 3 已加;此处确认 `const SYMBOLS={}` 之前已有 `seedDefaultStrategy()`)。`buildSymbol(meta)` 保持 `return buildSymbolFromBars(meta, genBars(meta.seed, meta));`(三参省略走默认 = `strategyForCode(meta.code)`)。

- [ ] **Step 3: `seatColor` 泛化**(替换 [luozi-chart.jsx:20-23])

```js
function seatColor(id) {
  if (window.lzStrategyColor) { const c = window.lzStrategyColor(id); if (c) return c; }
  const s = (window.LZ_SEATS || []).find(x => x.id === id);
  return s ? s.color : 'var(--ink-2)';
}
```

- [ ] **Step 4: console 验证**

```js
const meta = window.LZ_SYMBOL_META.find(m=>m.code==='300750');
const bars = window.LZ_SYMBOLS['300750'].bars;
const def = window.lzStrategyForCode('300750');
const built = window.lzBuildSymbolFromBars(meta, bars, def);
JSON.stringify({
  perSeatKeys: Object.keys(built.perSeat),       // 策略 id(如 ["strat_…"]),非 "momentum"
  stratIds: built.stratIds,
  colorOk: typeof window.seatColor(def[0].id) === 'string' && window.seatColor(def[0].id) !== 'var(--ink-2)',
});
```
Expected:`perSeatKeys` = 策略 id 列表;`colorOk: true`。0 console error。

- [ ] **Step 5: checkpoint**。

---

## Task 4: 桌面按票显示策略 + 合议(反应式重建)

**Files:**
- Modify: `ui/seats/luozi-app.jsx`(`consensusMetrics` [luozi-app.jsx:7]、`ALL_SEATS`/`active`、`symbol` 来源、`equityLines`、订阅 GL)
- Modify: `ui/seats/luozi-panels.jsx`(`SeatRail` [luozi-panels.jsx:480])

核心:`symbol` 按 `(bars, strategies)` 反应式 `useMemo` 重建,策略变更即时反映。

- [ ] **Step 1: app 持有策略 state 并订阅 GL**(在 `LuoziApp` 内 `code` state 之后)

```js
const [strategies, setStrategies] = useState(() => window.lzStrategyForCode ? window.lzStrategyForCode(code) : []);
useEffect(() => {
  const refresh = () => setStrategies(window.lzStrategyForCode ? window.lzStrategyForCode(code) : []);
  refresh();
  const off = window.GL ? window.GL.on(refresh) : null;   // 策略增删改 / 跨标签 → 刷新
  return () => { if (off) off(); };
}, [code]);
```

- [ ] **Step 2: `symbol` 按 (bars, strategies) 反应式重建**(替换 `const symbol = realSyms[code] || window.LZ_SYMBOLS[code];` [约 luozi-app.jsx:43])

```js
const baseBars = (realSyms[code] && realSyms[code].bars) || window.LZ_SYMBOLS[code].bars;
const _meta = window.LZ_SYMBOLS[code].meta;
const symbol = useMemo(() => {
  const s = window.lzBuildSymbolFromBars(_meta, baseBars, strategies);
  return (realSyms[code] && realSyms[code].bars5) ? Object.assign({}, s, { bars5: realSyms[code].bars5 }) : s;
}, [baseBars, strategies, code, realSyms]);
```

- [ ] **Step 3: `active`/`consensus`/`toggleSeat` 用策略 id**

```js
const stratIds = symbol.stratIds || [];
const [activeSet, setActiveSet] = useState(null);          // null = 全选
const active = activeSet ? stratIds.filter(id => activeSet.includes(id)) : stratIds;
const toggleSeat = (id) => setActiveSet(cur => {
  const base = cur || stratIds.slice();
  return base.includes(id) ? base.filter(x => x !== id) : [...base, id];
});
```
`consensusMetrics` [luozi-app.jsx:7] 改(去 `s!=='risk'` 过滤,按 perSeat 存在过滤):

```js
function consensusMetrics(symbol, activeSeats) {
  const pnl = activeSeats.filter(s => symbol.perSeat[s]);
  const eq = window.lzConsensusEquity(symbol.bars, symbol.perSeat, pnl);
  const trades = [];
  pnl.forEach(s => { (symbol.perSeat[s].trades || []).forEach(t => trades.push(t)); });
  return { eq, metrics: window.lzMetricsOf(eq, trades) };
}
```
删除原 `const [active, setActive]=useState(ALL_SEATS.slice())` 与原 `toggleSeat`/`ALL_SEATS`/`PNL_SEATS`(被上面取代)。`equityLines` 里 `pnlActive` 改 `const pnlActive = active.filter(s => symbol.perSeat[s]);`。原 `useEffect` 里 `setActive(...)` 调用全部删除(active 现为派生值)。

- [ ] **Step 4: `SeatRail` 渲染传入策略**(替换 [luozi-panels.jsx:501-503] 的 `window.LZ_SEATS.map`;`SeatRail` 加 `seats` prop)

```js
{(seats || []).map(s => (
  <SeatRow key={s.id}
    seat={{ id: s.id, cn: s.name, en: (window.LZ_TEMPLATES[s.template] || {}).cn || s.template, color: window.lzStrategyColor(s.id), glyph: s.glyph || '策' }}
    ps={symbol.perSeat[s.id]} active={active.includes(s.id)} onToggle={onToggle} rt={rt} />
))}
```
app 调用处加 `seats={symbol.stratIds.map(id => window.lzStrategyGet(id)).filter(Boolean)}`。
> `SeatRow` 若读 `ps.metrics`(perSeat 子项)——已满足;若 `ps` 可能 undefined(策略无成交)加 `ps={symbol.perSeat[s.id] || {eq:[1], trades:[], metrics:window.lzMetricsOf([1],[])}}` 防护。

- [ ] **Step 5: Chrome MCP 验证**

reload(默认 backtest)。`read_console_messages` 0 error。`javascript_tool`:

```js
JSON.stringify({ railHasDefault: /动量 · 默认|动量·默认/.test(document.body.innerText) });
```
Expected:`railHasDefault: true`。`screenshot`:盯盘席位栏「动量 · 默认」一行 + 编排官·合议;合议数字 == 该策略数字。

- [ ] **Step 6: checkpoint**。

---

## Task 5: 校场 — 策略装配 UI + 演武按 clock + 排行/管理

**Files:**
- Modify: `ui/seats/luozi-foundry.jsx`(`seatArena`→`strategyArena`、候选席来源、新建表单、时钟控件、绑票、排行、管理)

- [ ] **Step 1: `strategyArena(strategy)` 按 clock 回测 + 平均持有**(替换 `seatArena` [luozi-foundry.jsx:12-31])

```js
function strategyArena(strat) {
  const codes = (strat.bind && strat.bind.length) ? strat.bind : window.LZ_SYMBOL_META.map(m => m.code);
  let tot = [], shp = [], trades = [], holds = [];
  const per = [];
  codes.forEach(c => {
    const sym = window.LZ_SYMBOLS[c]; if (!sym) return;
    const ds = window.lzScanSeat(sym.bars, strat);
    const eqTr = window.lzSeatEquity(sym.bars, ds, strat.id);
    const m = window.lzMetricsOf(eqTr.eq, eqTr.trades);
    tot.push(m.total); shp.push(m.sharpe);
    (eqTr.trades || []).forEach(t => { trades.push(t); if (t.out != null && t.in != null) holds.push(t.out - t.in); });
    per.push({ code: c, name: sym.meta.name, total: m.total });
  });
  const avg = a => a.reduce((x, y) => x + y, 0) / (a.length || 1);
  const wins = trades.filter(t => t.ret > 0), losses = trades.filter(t => t.ret <= 0);
  const aw = wins.length ? avg(wins.map(t => t.ret)) : 0, al = losses.length ? Math.abs(avg(losses.map(t => t.ret))) : 0;
  return {
    avgTotal: avg(tot), avgSharpe: avg(shp),
    winRate: trades.length ? wins.length / trades.length : 0,
    plRatio: al ? aw / al : (aw ? 99 : 0),
    nTrades: trades.length, per,
    avgHold: holds.length ? avg(holds) : null,        // 实测平均持有 bar 数(校验 clock.maxHold)
    recommend: avg(shp) >= 1 && avg(tot) > 0 && trades.length >= 3,
  };
}
```

- [ ] **Step 2: 候选席来源换策略 + 新建态**(替换 [luozi-foundry.jsx:40] 起的 `GL.all('seat')` 块)

```js
const seats = window.lzStrategyList();                 // 候选 = 策略实例
const [editing, setEditing] = useState(null);          // null=不在编辑;{}=新建;{...strat}=改某策略
const curId = cur || (seats[0] && seats[0].id);
const seat = seats.find(s => s.id === curId) || null;
```
顶部候选席 tab 行末加「+ 新建策略」:`<span onClick={() => setEditing({ template:'momentum', bind:[], clock:{...window.LZ_TEMPLATES.momentum.clock} })}>+ 新建策略</span>`。

- [ ] **Step 3: 新建/编辑表单(命名 + 模板 + 时钟 + 绑票)**

在中栏配方区上方,`editing` 非 null 时渲染表单(受控 state `editing`):

```jsx
// name: <input value={editing.name||''} onChange={e=>setEditing(s=>({...s,name:e.target.value}))} />
// template: <select value={editing.template} onChange={e=>setEditing(s=>({...s,template:e.target.value, clock:{...window.LZ_TEMPLATES[e.target.value].clock}}))}>{window.LZ_TEMPLATE_IDS.map(t=>...)}</select>
// clock:
//   execTF <select day/5min> ; decisionFreq <select hourly/daily>
//   maxHold <input type=number step=1> ; stopLoss <input type=number step=0.01> ; takeProfit <input type=number step=0.01>
//   (改任一字段:setEditing(s=>({...s, clock:{...s.clock, [k]: v}})))
// bind: LZ_SYMBOL_META.map(m => chip, 点击 toggle code in editing.bind)
// 保存: const id = window.lzStrategySave({ id:editing.id, name:editing.name, template:editing.template, clock:editing.clock, bind:editing.bind, refs:(editing.id? (window.lzStrategyGet(editing.id)||{}).refs : []) }); setEditing(null); setCur(id);
// 取消: setEditing(null)
```
配方(refs)仍走现有料抽屉拖拽到当前 `seat`(`GL.link`/`patch`);新建保存后再在该策略上拖配方。

- [ ] **Step 4: 演武成绩 + 平均持有 + 管理**

成绩块(`runArena`/展示用 `strategyArena(seat)`)加一行:`平均持有 {a.avgHold==null?'—':a.avgHold.toFixed(1)} bar · 设定上限 {seat.clock.maxHold}`。当前策略头部加「改名/编辑」(`setEditing(seat)`)、「复制」(`lzStrategySave({...seat, id:undefined, name:seat.name+' 副本'})`)、「删除」(`lzStrategyDelete(seat.id); setCur(null)`)。

- [ ] **Step 5: 排行榜按策略**(`board = seats.map(s => ({ s, a: strategyArena(s) }))...`;展示策略名 + avgTotal/Sharpe/胜率 + 平均持有;原"上场/toggle"行为本期改为 `setCur(s.id)` 选中)。

- [ ] **Step 6: Chrome MCP 验证**

reload → 点「校场」。`screenshot`:候选席 = 策略列表(「动量 · 默认」)。`javascript_tool` 模拟新建并验演武:

```js
const id = window.lzStrategySave({ name:'演武测试·紧止盈', template:'momentum', bind:['300750'], clock:{stopLoss:0.05,takeProfit:0.06,maxHold:10} });
JSON.stringify({ created: !!window.lzStrategyGet(id), names: window.lzStrategyList().map(s=>s.name) });
```
（GL.on 触发 app+foundry 刷新。)`screenshot` 确认候选席含新策略、演武成绩 + 平均持有显示。**验后还原**:`window.lzStrategyDelete(id)` 并 reload。0 console error。

- [ ] **Step 7: checkpoint**。

---

## Task 6: 研判 / 影子组合接策略

**Files:**
- Modify: `ui/seats/luozi-panels.jsx`(`OrderWatchPanel` [luozi-panels.jsx:31])
- Modify: `ui/seats/luozi-app.jsx`(传 `strategies` 给 `OrderWatchPanel`)

- [ ] **Step 1: `OrderWatchPanel` 席位选择换策略选择**

`OrderWatchPanel` 函数签名加 `strategies`。`const [seat, setSeat] = useState('momentum')` 改 `const [seat, setSeat] = useState(() => (strategies && strategies[0] && strategies[0].id) || 'momentum')`;并加 effect:`useEffect(() => { if (strategies && strategies.length && !strategies.some(s=>s.id===seat)) setSeat(strategies[0].id); }, [strategies])`(策略变了重置选中)。下拉(替换 [luozi-panels.jsx:153]):

```jsx
{(strategies || []).map(s => <option key={s.id} value={s.id}>{s.name}</option>)}
```
`SEATCN` 不再用于下拉(保留供旧引用);`myHold = (positions||[]).find(p => p.status==='open' && p.seat===seat)` 不变(seat 现为 strategy.id)。

- [ ] **Step 2: `runJudge` 传 template + 上报 strategy.id**

`runJudge` 内:

```js
const strat = (strategies || []).find(s => s.id === seat) || (strategies || [])[0] || { id: seat, template: 'momentum' };
const tmpl = strat.template || 'momentum';
// ...
window.lzSeatOrder(code, tmpl, otf, hold).then(o => {
  // 研判流水 at/reason/dir 不变;但 onClosePosition 用 myHold.id(已是策略口径)
  // ...
});
```
`check()`/live 触发里 `onTrigger({ id: code+'·'+seat, ..., seat: strat.id, ... })`(把上报的 `seat` 由 `order.seat`/模板改为 `strat.id`,使影子按 strategy.id 记)。`genOrder`/三触发 effect 的依赖数组把 `seat` 保留(现为 strategy.id)。

- [ ] **Step 3: app 传 `strategies`**

`OrderWatchPanel` 调用处加 `strategies={strategies}`(app 的当前票策略 state)。

- [ ] **Step 4: Chrome MCP + console 验证**

reload → 切实盘(JS click '实盘')。`screenshot`:条件单·盯盘 策略下拉 = 当前票策略(「动量 · 默认」)。`javascript_tool` 验影子按 strategy.id 记:

```js
const def = window.lzStrategyForCode('300750')[0];
const sh = window.lzShadowAddEntry({ goLive:'2026-06-01', positions:[] }, { id:'x1', at:'2026-06-05', side:'买入', fill:100, seat:def.id, stop:92, take:120 });
JSON.stringify({ seatIsStrategyId: sh.positions[0] && sh.positions[0].seat === def.id });
```
Expected:`seatIsStrategyId: true`。0 console error。

- [ ] **Step 5: checkpoint**。

---

## Task 7: 整合 + `?v` bump + 全量浏览器实测 + 文档

**Files:**
- Modify: `观澜 · 落子.html`(`?v=20260608d27` → `d28`,Edit replace_all,**非 sed**)
- Modify: `ui/seats/README.md`、记忆 `luozi-live-trading-roadmap.md` + `MEMORY.md`

- [ ] **Step 1: bump `?v`**(Edit replace_all `?v=20260608d27`→`?v=20260608d28`)
- [ ] **Step 2: 全量实测(Chrome MCP @9999,reload d28)**
  - 复盘桌面:盯盘席位栏 = 当前票策略 + 合议;切票看在场策略随 bind 变。
  - 校场:新建策略(名/模板/时钟/绑票)→ 保存 → 候选席出现 → 演武出按 clock 回测 + 平均持有;改 clock 再演武,成绩随之变。
  - 实盘:条件单·盯盘 策略下拉 = 当前票策略。
  - `read_console_messages` 0 error;关键截图留证。
  - **清理**:删除验证期注入的所有测试策略/影子,reload 确认 GL 仅剩默认策略 + 用户原数据。
- [ ] **Step 3: 文档/记忆**:`README.md` 记第3期;`luozi-live-trading-roadmap.md` + `MEMORY.md` 标第3期完成 `?v=d28`。

---

## 验收(对齐 spec §6)
- 校场可新建命名策略(名/模板/配方/时钟/绑票)→ 保存,刷新不丢。
- 桌面按票显示在场策略 + 合议 = 它们组合;K 线落子按策略;不同 clock 的策略演武/出场不同。
- 演武按 template+clock 真回测 + 平均持有回显;排行按策略。
- 研判选当前票策略,`/seats/order` 走 template creed;影子按 strategy.id 记。
- 默认策略迁移:首载桌面非空,旧影子不断裂。
- 第1/2期逻辑无返工;0 console error;Chrome MCP 实测。
