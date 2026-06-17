# 落子 · 真数据收口(演武/舰队/信条/节拍/基准)实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把落子系统所有仍在合成数据上运行/降级不显形的环节接到后端真实数据:校场演武(日线+5min)、策略架排行、舰队卡、自定义信条、研判节拍显形、盯盘/舰队真沪深300基准。

**Architecture:** 在 `luozi-data.jsx` 增加一层模块级「真K水合缓存」(REAL_BARS / REAL_BARS5,按 code 缓存 `/seats/daily` 真数据),校场与舰队全部改读该缓存;**真数据缺位时诚实剔除/显形,绝不回退合成**。信条(creed)升级为策略实例自有字段,贯穿 `/seats/decide`、`/seats/order` 两条 LLM 通道。基准线由后端新端点 `/seats/benchmark`(真沪深300)替换 mulberry32 合成指数。

**Tech Stack:** React 18(浏览器内 Babel,无构建)、FastAPI(guanlan_v2/seats/api.py)、引擎 QlibBinaryLoader、pytest。

**关联既有计划:** `docs/superpowers/plans/2026-06-09-luozi-p1-roadmap.md` 的任务 2(decisionFreq 真节拍)**已落地**(luozi-panels.jsx:179-195,hourly/daily/10min 地板齐全)——本计划 Task 5 只补「下次研判」显形,不重做;其任务 3(自定义 creed)未落地,由本计划 Task 4 吸收(含其「落盘存 creed」要求);其任务 1(研判历史删/导出)不在本计划范围,留在原计划。

---

## 环境与纪律(执行前必读)

1. **本仓不是 git 仓库**(`Is a git repository: false`)。所有「Commit」步骤替换为「验证清单」收口;改坏即手动还原,改文件前先 Read 留底。
2. **后端 9999 无看门狗**:重启即挂须手动拉起。改 `guanlan_v2/seats/api.py` 后重启流程:
   - `netstat -ano | findstr :9999` 找 LISTENING 的 PID → `taskkill /PID <pid> /F`,等端口释放(防 10048);
   - 拉起:`G:/financial-analyst/.venv/Scripts/python.exe G:/guanlan-v2/guanlan_v2/server.py`(后台);
   - `Invoke-WebRequest http://127.0.0.1:9999/health` 等 200。
3. **浏览器按 ?v= 缓存 jsx**:每次浏览器验证前,用 **Edit(不是 sed)** bump `ui/seats/观澜 · 落子.html` 第 47-52 行对应文件的 `?v=`,再 reload。本计划统一 bump 到 `?v=20260610b`(data 已是 20260610a → 改 20260610b;其余从 20260609j → 20260610b)。
4. **React 点击坑**:JS `.click()` 对 React onClick 偶发不触发 → 浏览器验证时派发完整事件序列(pointerdown/mousedown/pointerup/mouseup/click)或用真坐标点击。
5. **pytest**:`G:/financial-analyst/.venv/Scripts/python.exe -m pytest tests/ -x -q`(工作目录 `G:\guanlan-v2`),现状全绿,收口时必须仍全绿。
6. **红线**:只填充现有组件,不重建/简化界面;降级必须显形(「样例」「未连接」标注),绝不让合成数据冒充真数据;只出信号不下单;不写 `G:/stocks`;LLM 失败不落盘。

## 任务依赖

```
Task 1(真K水合基础设施) ──▶ Task 2(演武接真·日线) ──▶ Task 3(5min execTF 演武)
        │
        └──▶ Task 6(舰队卡接真)
Task 4(自定义信条)      独立
Task 5(节拍显形+MarketBar) 独立
Task 7(真沪深300基准)    独立(后端+前端)
Task 8(bump ?v + 端到端浏览器验真) 最后
```

---

### Task 1: luozi-data 真K水合基础设施

**Files:**
- Modify: `G:\guanlan-v2\ui\seats\luozi-data.jsx`(在 `fetchBars5` 定义后、约 596 行处插入;导出表约 1091-1110 行追加)

背景:`fetchDailyBars(code, n)`(519-529)与 `fetchBars5(code, n)`(585-595)已存在且诚实(失败返 null),但只被盯盘单票路径使用。校场/舰队需要「一次水合整个名册」的共享缓存。

- [ ] **Step 1: 插入水合缓存与查询函数**

在 `fetchBars5` 函数结束(约 595 行 `}` )之后插入:

```js
// ───────── 真K水合(校场演武 / 舰队 共用) ─────────
// REAL_BARS[code] = /seats/daily 真日线(normDailyBars 归一);REAL_BARS5 同理 5min。
// 红线:缺位 = 后端没给,调用方必须诚实剔除/显形,绝不回退 genBars 合成。
const REAL_BARS = {};        // code -> bars(day)
const REAL_BARS5 = {};       // code -> bars(5min)
const REAL_BARS_TS = {};     // (freq+code) -> 水合 epoch ms(10min 内不重拉)
async function _hydrate(store, fetcher, freq, codes, n) {
  const all = (codes && codes.length) ? codes : SYMBOL_META.map(m => m.code);
  const want = all.filter(c => !store[c] || (Date.now() - (REAL_BARS_TS[freq + c] || 0)) > 600000);
  await Promise.all(want.map(c => fetcher(c, n).then(bars => {
    if (bars && bars.length) { store[c] = bars; REAL_BARS_TS[freq + c] = Date.now(); }
  }).catch(() => {})));
  const cov = {};
  all.forEach(c => { cov[c] = !!store[c]; });
  return cov;                 // {code: true|false} 真数据覆盖表
}
function hydrateRealBars(codes, n) { return _hydrate(REAL_BARS, fetchDailyBars, 'd', codes, n || 250); }
function hydrateRealBars5(codes, n) { return _hydrate(REAL_BARS5, fetchBars5, '5', codes, n || 2400); }
function realBarsOf(code, freq) { return (freq === '5min' ? REAL_BARS5[code] : REAL_BARS[code]) || null; }
```

- [ ] **Step 2: 导出到 window**

在导出块(1091 行起 `LZ_SEATS: SEATS, LZ_SYMBOLS: SYMBOLS, ...` 所在对象)中追加:

```js
  lzHydrateRealBars: hydrateRealBars, lzHydrateRealBars5: hydrateRealBars5,
  lzRealBarsOf: realBarsOf,
```

- [ ] **Step 3: 控制台验证(后端在跑时)**

浏览器打开落子页(本步无需 bump,Console 直接验函数;若浏览器缓存了旧 data jsx 则先 bump data 一次):

```js
window.lzHydrateRealBars().then(cov => console.log('coverage', cov,
  'bars300750', window.lzRealBarsOf('300750','day') && window.lzRealBarsOf('300750','day').length));
```

期望:`coverage {300750: true, 600519: true, ...}` 全 true,`bars300750 250`。再执行一次应秒回(缓存)。Network 面板首跑可见 6 条 `/seats/daily` 请求。

---

### Task 2: 校场演武接真日线(真进度 + 覆盖率显形)

**Files:**
- Modify: `G:\guanlan-v2\ui\seats\luozi-foundry.jsx`(strategyArena 27-54;runArena 78-81;Foundry mount 63 附近;RosterCard 94-121;结果面板 296-331)

- [ ] **Step 1: strategyArena 改读真K,缺位诚实剔除**

将 27-54 行 `strategyArena` 整体替换为(行为变化:bars 来源 `lzRealBarsOf`;新增返回字段 `freq/nCodes/nReal/missing/ready`):

```js
// 跨标的演武:聚合该策略实例在其绑票(或全局)上的回测成绩
// 真数据红线:bars 只来自 lzRealBarsOf(/seats/daily 水合缓存);缺位的票诚实剔除并报 missing,绝不用合成K顶上。
function strategyArena(strat) {
  const codes = (strat.bind && strat.bind.length) ? strat.bind : window.LZ_SYMBOL_META.map(m => m.code);
  const freq = (strat.clock && strat.clock.execTF) === '5min' ? '5min' : 'day';
  let tot = [], shp = [], trades = [], holds = [], eqs = [];
  const per = [], missing = [];
  codes.forEach(c => {
    const meta = window.LZ_SYMBOL_META.find(m => m.code === c);
    const bars = window.lzRealBarsOf ? window.lzRealBarsOf(c, freq) : null;
    if (!bars || !bars.length) { missing.push((meta && meta.name) || c); return; }
    const ds = window.lzScanSeat(bars, strat);
    const eqTr = window.lzSeatEquity(bars, ds, strat.id);
    const m = window.lzMetricsOf(eqTr.eq, eqTr.trades, freq);
    tot.push(m.total); shp.push(m.sharpe); eqs.push(eqTr.eq);
    (eqTr.trades || []).forEach(t => { trades.push(t); if (t.out != null && t.in != null) holds.push(t.out - t.in); });
    per.push({ code: c, name: (meta && meta.name) || c, total: m.total });
  });
  const avg = a => a.reduce((x, y) => x + y, 0) / (a.length || 1);
  const wins = trades.filter(t => t.ret > 0), losses = trades.filter(t => t.ret <= 0);
  const aw = wins.length ? avg(wins.map(t => t.ret)) : 0, al = losses.length ? Math.abs(avg(losses.map(t => t.ret))) : 0;
  let eq = [];
  if (eqs.length) { const L = Math.min(...eqs.map(e => e.length)); for (let k = 0; k < L; k++) { let s = 0; for (const e of eqs) s += e[k]; eq.push(+(s / eqs.length).toFixed(4)); } }
  return {
    avgTotal: avg(tot), avgSharpe: avg(shp),
    winRate: trades.length ? wins.length / trades.length : 0,
    plRatio: al ? aw / al : (aw ? 99 : 0),
    nTrades: trades.length, per, eq,
    avgHold: holds.length ? avg(holds) : null,
    recommend: per.length > 0 && avg(shp) >= 1 && avg(tot) > 0 && trades.length >= 3,
    freq, nCodes: codes.length, nReal: per.length, missing,
    ready: per.length > 0,            // false = 一只真K都没有 → 渲染端必须显形,不出成绩
  };
}
```

注意:`lzMetricsOf` 第三参 `freq` 在 Task 3 才生效,本任务先传(day 路径行为不变)。

- [ ] **Step 2: runArena 改真异步(水合即进度)**

替换 78-81 行:

```js
  const runArena = async (sid) => {
    const s = seats.find(x => x.id === sid); if (!s || running) return;
    const codes = (s.bind && s.bind.length) ? s.bind : window.LZ_SYMBOL_META.map(m => m.code);
    setRunning(sid);
    try {
      // 「推演中…」= 真实拉数:execTF 决定拉日线还是 5min(Task 3 接通 5min)
      if ((s.clock && s.clock.execTF) === '5min') { if (window.lzHydrateRealBars5) await window.lzHydrateRealBars5(codes); }
      else { if (window.lzHydrateRealBars) await window.lzHydrateRealBars(codes); }
    } finally {
      setRunning(null); setRan(r => ({ ...r, [sid]: true }));
    }
  };
```

- [ ] **Step 3: Foundry 挂载即水合(策略架排行用真数据)**

在 63 行 `useEffect(() => GL.on(...), [])` 之后新增:

```js
  const [hydrated, setHydrated] = useState(false);
  useEffect(() => {
    let alive = true;
    if (window.lzHydrateRealBars) window.lzHydrateRealBars().then(() => { if (alive) setHydrated(true); });
    else setHydrated(true);
    return () => { alive = false; };
  }, []);
```

- [ ] **Step 4: RosterCard 未就绪显形**

RosterCard(94-121)内消费 `a` 的四处(110 MiniSpark、113 pc(a.avgTotal)、114 Sharpe、115 胜率)外包一层守卫:`a.ready` 为 false 时该区域渲染:

```jsx
  <span className="mono" style={{ fontSize: 9.5, color: 'var(--ink-3)' }}>
    {hydrated ? '待演武 · 真K未达' : '取数中…'}
  </span>
```

(`a.ready === false` 时不渲染收益/Sharpe/胜率数字与 sparkline;「荐」徽章 `a.recommend` 已在 Step 1 加 `per.length > 0` 守卫。)

- [ ] **Step 5: 结果面板覆盖率显形 + 全缺位拒绝出成绩**

结果面板(299-331)开头加分支:`a.ready === false` 时整块替换为诚实空态:

```jsx
  <div className="mono" style={{ padding: '18px 16px', fontSize: 11, color: 'var(--zhu)', border: '1px dashed var(--zhu)', borderRadius: 13 }}>
    演武需要真实K线 · 后端未连接或所选标的全部无数据(已拒绝在合成样例上出成绩)。
    启动 9999 后端后点「重跑演武」。
  </div>
```

`a.ready === true` 时,在 hero 区(300-308)「跨标的收益」标签旁追加覆盖率徽章:

```jsx
  <span className="mono" style={{ fontSize: 9, color: 'var(--ink-3)', border: '1px solid var(--line)', borderRadius: 8, padding: '1px 7px' }}>
    真·{a.freq === '5min' ? '5min' : '日线'} {a.nReal}/{a.nCodes}
    {a.missing.length > 0 ? ' · 缺 ' + a.missing.join('/') + '(未纳入)' : ''}
  </span>
```

- [ ] **Step 6: 浏览器验证**

bump `观澜 · 落子.html` 47/51 行(data → `?v=20260610b`,foundry → `?v=20260610b`),reload,进校场:
1. Network:挂载即见 6 条 `/seats/daily?code=...` 请求(200);
2. 点「重跑演武」:结果数字应与改造前**不同**(真行情 ≠ 合成);hero 区出现「真·日线 6/6」徽章;
3. Console 0 error;
4. 反证:Console `window.GUANLAN_BACKEND=''` 后 reload → 校场显「取数中…」→「待演武 · 真K未达」,点重跑显「演武需要真实K线…」红框,**不出任何成绩数字**。恢复 reload。

---

### Task 3: 5min execTF 演武(metricsOf 频率折算)

**Files:**
- Modify: `G:\guanlan-v2\ui\seats\luozi-data.jsx`(metricsOf 309-340 附近)
- Modify: `G:\guanlan-v2\ui\seats\luozi-foundry.jsx`(结果面板窗口标注)

背景:后端 `/seats/daily?freq=5min` 已支持(api.py:196-198,n 上限 8000,数据 2018-01 起);`fetchBars5` 默认 2400 根 ≈ 50 个交易日;`normBars5` 的 bar 形状 `{i,date('YYYY-MM-DD HH:MM'),day,o,c,h,l,v}`,`scanSeat` 只用 o/c/h/l/v + 均线,对粒度无感,可直接复用(event 模板靠 `b.event`,5min bar 无该字段 → 事件策略 5min 演武无进场,属诚实空,不造假)。唯年化/夏普折算按日线写死,需加频率参数。

- [ ] **Step 1: metricsOf 加 freq 参数**

Read `luozi-data.jsx:309-340` 拿到当前函数体,做三处修改:

签名 `function metricsOf(eq, trades)` → `function metricsOf(eq, trades, freq)`,函数体开头加:

```js
  const perDay = freq === '5min' ? 48 : 1;   // A股 4 小时/日 = 48 根 5min;日线 = 1
```

原 317 行 `const years = (n * 1.4) / 365;` 改为:

```js
  const years = ((n / perDay) * 1.4) / 365;  // 先折成交易日数,再按 1.4 自然日/交易日折年
```

sharpe 行的 `Math.sqrt(252)` 改为 `Math.sqrt(252 * perDay)`(逐 bar 收益年化)。

其余调用方(`buildSymbolFromBars:370` 等)不传第三参 → `perDay=1`,行为逐位不变。

- [ ] **Step 2: 演武结果面板标注 5min 窗口**

`luozi-foundry.jsx` 结果面板覆盖率徽章(Task 2 Step 5)已含 `真·5min`;在 `a.freq === '5min'` 时于徽章后追加:

```jsx
  {a.freq === '5min' && <span className="mono" style={{ fontSize: 9, color: 'var(--ink-3)' }}>窗口 ~50 交易日(2400 根)</span>}
```

- [ ] **Step 3: 浏览器验证**

bump 两文件 ?v(若本次会话已是 20260610b 则跳过),reload:
1. 校场「编辑」现有策略,镜头 execTF 改 `5min`,钤印保存;
2. 点「重跑演武」:Network 见 6 条 `/seats/daily?freq=5min&...`(200);
3. 结果面板显「真·5min 6/6 · 窗口 ~50 交易日」;成交笔数/收益应与 day 模式明显不同;
4. 改回 `day` 重跑,数字回到 Task 2 的日线结果(缓存秒回);Console 0 error。

---

### Task 4: 自定义信条(creed)全链路

**Files:**
- Modify: `G:\guanlan-v2\ui\seats\luozi-data.jsx`(strategySave 135-149)
- Modify: `G:\guanlan-v2\ui\seats\luozi-foundry.jsx`(newDraft 83;表单 192-233;详情头 234-250 附近)
- Modify: `G:\guanlan-v2\ui\seats\luozi-panels.jsx`(lzSeatMeta 27-35)
- Modify: `G:\guanlan-v2\guanlan_v2\seats\api.py`(decide 落盘记录 549-556 加 creed)

背景(= P1 计划任务 3,吸收执行):**creed 管线已全通**——`runDecide` 已传 `creed: s.creed`(panels:901-917)、`runTimedDecide` 已传 `(meta && meta.creed) || ''`(panels:106-113)、`runJudge`→`lzSeatOrder` 已传 `extra.creed`(P1 计划核对为 panels:79,以当前 70-94 实际为准)、后端 decide(api.py:392/496-498)与 order(api.py:747「传入优先、空回退 `_CREEDS` 模板」)均已消费。**唯一缺口**:GL strategy 对象无 `creed` 字段,`lzSeatMeta`(panels:27-35)的 creed 恒取模板 `td.creed` → 加字段 + 一处回退 + 表单 + 落盘记录即可。`luozi-app.jsx:121-122` 已写 `(strat && strat.creed) || tmplCreed`,字段就位后自动生效。

- [ ] **Step 1: strategySave 持久化 creed**

`luozi-data.jsx:139-146` 的 obj 字面量中,`type:'strategy', name...` 行后加:

```js
    creed: (o.creed != null && String(o.creed).trim()) ? String(o.creed).trim() : (td.creed || ''),
```

- [ ] **Step 2: lzSeatMeta 解析策略自有信条**

Read `luozi-panels.jsx:24-40` 拿到 `lzSeatMeta` 当前实现。函数开头(若尚无策略对象解析)加:

```js
  const st = (window.lzStrategyGet && window.lzStrategyGet(id)) || null;
```

(`id` 为该函数的策略/席位 id 入参名,以实际签名为准。)把 `creed: td.creed || ''`(约 30-31 行)改为:

```js
    creed: (st && st.creed) || td.creed || '',
```

- [ ] **Step 3: 校场表单加「信条」输入**

`luozi-foundry.jsx:83` newDraft 加字段:

```js
  const newDraft = () => setEditing({ name: '', template: 'momentum', bind: [], clock: Object.assign({}, window.LZ_TEMPLATES.momentum.clock), refs: [], creed: window.LZ_TEMPLATES.momentum.creed });
```

模板分段选择器 onClick(199 行)同步换默认信条(用户已自定义则保留):

```js
  onClick={() => setEditing(s => ({ ...s, template: t,
    creed: (!s.creed || s.creed === window.LZ_TEMPLATES[s.template].creed) ? td.creed : s.creed,
    clock: Object.assign({}, td.clock, { execTF: s.clock.execTF }) }))}
```

207 行模板信条静态展示行**下方**新增可编辑信条(下划线输入风格与名称 input 一致):

```jsx
  <div style={{ marginTop: 10 }}>
    <div className="mono" style={{ fontSize: 9.5, color: 'var(--ink-3)', letterSpacing: '.18em' }}>信 条 · 喂给 agent 研判与条件单的交易哲学</div>
    <textarea value={editing.creed || ''} rows={2}
      onChange={e => setEditing(s => ({ ...s, creed: e.target.value }))}
      placeholder={window.LZ_TEMPLATES[editing.template].creed}
      style={{ width: '100%', background: 'transparent', border: 'none', borderBottom: '1px solid var(--line)', color: 'var(--ink)', fontSize: 12, fontFamily: 'inherit', resize: 'vertical', outline: 'none', padding: '4px 0' }} />
  </div>
```

保存 handler(228-229)的 `lzStrategySave({...})` 参数加 `creed: editing.creed,`;编辑入口(250 行 `setEditing(Object.assign({}, seat, ...))`)无需改(creed 随 seat 展开;老策略无 creed → textarea 空 + placeholder 模板文案,保存时 strategySave 兜底模板,即「留空=用模板」)。

- [ ] **Step 4: 书案详情头显示策略自有信条**

详情分支(234 起)显示模板 creed 的文案处(「动量突破 · 突破均线、量价齐升则顺势加仓」一行),Grep 本文件 `creed` 定位展示点,把数据源改为:

```jsx
  {(seat.creed || TCN[seat.template].creed)}
```

(布局逐字不动,只换数据源。)

- [ ] **Step 5: 后端 decide 落盘记录 creed(历史可溯源)**

`guanlan_v2/seats/api.py:549-556` decide 的 `_persist_decision("decide", {...})` 记录 dict 加一行:

```python
        "creed": creed[:120],
```

(order 落盘记录 894-901 若无 creed 字段,同样加 `"creed": creed[:120],`。)

- [ ] **Step 6: 重启 9999 + pytest**

按「环境与纪律 #2」重启 9999。`pytest tests/ -x -q` → 全绿(本改动只加字段,不应破坏既有用例)。

- [ ] **Step 7: 浏览器验证**

bump foundry/panels/data ?v,reload:
1. 校场新建策略:信条框预填模板文案,改为自定义(如「只做放量突破回踩,跌破 5 日线即走」),钤印保存;
2. Console:`window.lzStrategyGet(window.lzStrategyList().slice(-1)[0].id).creed` → 自定义文案;reload 后仍在(localStorage 持久);
3. 盯盘选该策略点「真·agent 研判」,成功后 `Get-Content G:\guanlan-v2\var\seats_decisions.jsonl -Tail 1` → 含 `"creed": "只做放量突破回踩…"`;
4. 「立单」:Network 查 `/seats/order` query 含自定义 creed;Console 0 error。

---

### Task 5: 研判节拍显形(下次研判 HH:mm)+ MarketBar 断连显形

**Files:**
- Modify: `G:\guanlan-v2\ui\seats\luozi-panels.jsx`(状态 51-54;runJudge :72;runTimedDecide :100;循环开关 220-226;MarketBar 341-344)

背景:decisionFreq 节流**已真实现**(panels:179-195:60s tick、10min 硬地板、hourly=距上次≥1h、daily=跨自然日仅一次;= P1 计划任务 2,已落地)。缺的只是显形:`lastJudgeRef` 是 ref,UI 看不到「下次研判」;另 MarketBar 断连显裸「—」(审计 not_fixed 项)。

- [ ] **Step 1: lastJudge 升级为可渲染 state**

在 :51-54 状态区加:

```js
  const [lastJudgeAt, setLastJudgeAt] = useState(0);   // 与 lastJudgeRef 同步,仅供「下次研判」显示
```

`runJudge`(:72 刷 `lastJudgeRef.current` 处)与 `runTimedDecide`(:100 同)紧跟 ref 赋值各加 `setLastJudgeAt(Date.now());`。切票重置 effect(:55)加 `setLastJudgeAt(0);`。

- [ ] **Step 2: 循环开关旁渲染「下次研判」**

:220-225 的 `{mode === 'live' && (...)}` 循环开关 span 之后(同一行容器内)追加:

```jsx
  {mode === 'live' && loopOn && (() => {
    const strat = (strategies || []).find(s => s.id === seat) || null;
    const fq = (strat && strat.clock && strat.clock.decisionFreq) || 'hourly';
    let label;
    if (!lastJudgeAt) label = '下次研判 · 即刻可触';
    else if (fq === 'daily') {
      label = new Date(lastJudgeAt).toDateString() === new Date().toDateString() ? '下次研判 · 次一交易日' : '下次研判 · 即刻可触';
    } else {
      const t = new Date(lastJudgeAt + 3600000);   // hourly 封顶;10min 地板已含于其中
      label = '下次研判 ~' + t.toTimeString().slice(0, 5);
    }
    return <span className="mono" title="按本策略「研判频率」节流:hourly=每小时封顶 · daily=每日一次 · 10min 硬地板" style={{ fontSize: 9, color: 'var(--jin)', marginLeft: 6 }}>{label}</span>;
  })()}
```

- [ ] **Step 3: MarketBar 断连显形**

:341-344 中 `{cell('行情', regime || '—', regColor)}` 后追加:

```jsx
  {!real && <span className="mono" style={{ fontSize: 8.5, color: 'var(--ink-3)' }}>(行情快照未连接)</span>}
```

- [ ] **Step 4: 浏览器验证**

bump panels ?v,reload:
1. 实盘 tab 开「研判循环」:开启即显「下次研判 · 即刻可触」;手动研判一次后变「下次研判 ~HH:MM」(+1h);
2. daily 策略(反转/事件模板)研判后同位显「次一交易日」;
3. MarketBar:后端在跑显「真·快照」无小字;`window.GUANLAN_BACKEND=''` + reload → 「—(行情快照未连接)」。恢复;Console 0 error。

---

### Task 6: 舰队卡接真日线

**Files:**
- Modify: `G:\guanlan-v2\ui\seats\luozi-data.jsx`(真符号缓存 helper,Task 1 代码块之后)
- Modify: `G:\guanlan-v2\ui\seats\luozi-fleet.jsx`(FleetCard :41 取数 + 徽章;Fleet 主组件挂载水合)

背景:`luozi-fleet.jsx:41` `const S = window.LZ_SYMBOLS[code];` 直读合成装配产物(bars/perSeat/bench/decisions 全合成)。舰队是总览页非晋升依据,允许合成兜底但必须打「样例」标——与盯盘同一规范。

- [ ] **Step 1: luozi-data 加真符号缓存**

Task 1 代码块后追加:

```js
// 真K → buildSymbolFromBars 装配缓存(舰队用;key 带末根日期,真K更新自动重建)
const REAL_SYM_CACHE = {};
function realSymbolOf(code) {
  const meta = SYMBOL_META.find(m => m.code === code);
  const bars = REAL_BARS[code];
  if (!meta || !bars || !bars.length) return null;
  const key = bars.length + ':' + bars[bars.length - 1].date;
  if (!REAL_SYM_CACHE[code] || REAL_SYM_CACHE[code].key !== key) {
    REAL_SYM_CACHE[code] = { key, sym: buildSymbolFromBars(meta, bars) };
  }
  return REAL_SYM_CACHE[code].sym;
}
```

导出表加 `lzRealSymbolOf: realSymbolOf,`。

- [ ] **Step 2: Fleet 挂载水合 + FleetCard 换源**

Fleet 主组件(Read `luozi-fleet.jsx` 找顶层组件)加挂载水合(与 Task 2 Step 3 同式样 `useEffect` + `hydrated` state,水合完成 setState 触发重渲)。

FleetCard :41 改为:

```js
  const realS = window.lzRealSymbolOf ? window.lzRealSymbolOf(code) : null;
  const S = realS || window.LZ_SYMBOLS[code];
  const isReal = !!realS;
```

卡片头部(名称/代码行)追加徽章:

```jsx
  <span className="mono" style={{ fontSize: 8.5, padding: '0 5px', borderRadius: 7,
    border: '1px solid ' + (isReal ? 'var(--yin)' : 'var(--line)'),
    color: isReal ? 'var(--yin)' : 'var(--ink-3)' }}>{isReal ? '真·日线' : '样例'}</span>
```

- [ ] **Step 3: 浏览器验证**

bump fleet/data ?v,reload,切「舰队」:
1. 后端在跑:6 张卡全显「真·日线」徽章,K线/涨跌幅与盯盘页同票一致;
2. `window.GUANLAN_BACKEND=''` + reload:卡显「样例」徽章(诚实降级);恢复;
3. Console 0 error。

---

### Task 7: 真沪深300基准(后端端点 + 前端替换 mulberry32 合成指数)

**Files:**
- Modify: `G:\guanlan-v2\guanlan_v2\seats\api.py`(新端点 `/seats/benchmark`)
- Create: `G:\guanlan-v2\tests\test_seats_benchmark.py`
- Modify: `G:\guanlan-v2\ui\seats\luozi-data.jsx`(fetchBenchmark + alignBench;buildSymbolFromBars bench 换源)
- Modify: `G:\guanlan-v2\ui\seats\luozi-app.jsx`(真K路径同步拉基准)
- Modify: 基准消费端 null 守卫(收益曲线/MetricsStrip/FleetCard,Grep `\.bench` 定位)

背景:`buildSymbolFromBars:374` `bench = benchmark(bars, meta.seed||1)` 是 mulberry32 种子随机「指数」(344-353)——盯盘「基准 +29.8%」、收益曲线虚线、舰队卡 bench 全合成。仓内已有真沪深300日线(workflow 绩效报告「净值 vs 真沪深300」同源 etf_index.parquet)。

- [ ] **Step 1: 找到既有真指数读取路径**

```
rg -n "etf_index" G:\guanlan-v2\guanlan_v2 G:\guanlan-v2\engine --glob "*.py"
```

预期在 workflow/api.py(或 engine eval)找到读 etf_index.parquet 取沪深300收盘序列的既有 helper。下一步 `_load_csi300` 复用其数据源路径与列名落地(**不得另造数据源**)。

- [ ] **Step 2: 写失败测试**

Create `G:\guanlan-v2\tests\test_seats_benchmark.py`:

```python
from fastapi.testclient import TestClient
from guanlan_v2.server import build_app


def test_seats_benchmark_returns_real_csi300():
    client = TestClient(build_app())
    r = client.get("/seats/benchmark", params={"n": 250})
    assert r.status_code == 200
    j = r.json()
    assert j["ok"] is True
    bars = j["bars"]
    assert len(bars) > 100
    dates = [b["date"] for b in bars]
    assert dates == sorted(dates)                      # 升序
    assert all(b["close"] > 0 for b in bars)           # 真价
    assert bars[-1]["date"] >= "2026-01-01"            # 新鲜


def test_seats_benchmark_window():
    client = TestClient(build_app())
    r = client.get("/seats/benchmark", params={"start": "2025-06-01", "end": "2026-06-09"})
    j = r.json()
    assert j["ok"] is True
    assert j["bars"][0]["date"] >= "2025-06-01"
    assert j["bars"][-1]["date"] <= "2026-06-09"
```

(`build_app` 若与 `guanlan_v2/server.py` 实际工厂名不符,照 tests/ 既有用例的建 client 方式抄。)

- [ ] **Step 3: 跑测试确认失败**

`G:/financial-analyst/.venv/Scripts/python.exe -m pytest tests/test_seats_benchmark.py -x -q`
期望:FAIL(404,端点不存在)。

- [ ] **Step 4: 实现端点**

`guanlan_v2/seats/api.py` 追加(`_load_csi300` 按 Step 1 发现的真实源实现;返回行形如 `{"date": "YYYY-MM-DD", "close": <float>}` 升序):

```python
@router.get("/benchmark")
async def seats_benchmark(start: Optional[str] = None, end: Optional[str] = None, n: int = 250):
    """真·沪深300 日收盘(etf_index.parquet 同源),供盯盘/舰队净值对标。失败 ok:False,前端隐藏基准线诚实降级。"""
    try:
        rows = _load_csi300(start=start, end=end, n=max(10, min(int(n or 250), 1200)))
        return {"ok": True, "code": "csi300", "bars": rows}
    except Exception as e:  # noqa: BLE001 —— 诚实降级,不让基准把整页打挂
        return {"ok": False, "error": str(e)[:200]}
```

- [ ] **Step 5: 重启 9999,跑测试到绿**

重启流程见「环境与纪律 #2」。`pytest tests/test_seats_benchmark.py -x -q` → 2 passed;全量 `pytest tests/ -x -q` → 全绿。

- [ ] **Step 6: 前端取数与对齐**

`luozi-data.jsx` 在 fetchDailyBars 附近加:

```js
async function fetchBenchmark(start, end, n) {
  const API = (window.GUANLAN_BACKEND || '');
  if (!API) return null;
  try {
    const q = start ? ('start=' + start + '&end=' + (end || '')) : ('n=' + (n || 250));
    const res = await fetch(API + '/seats/benchmark?' + q);
    if (!res.ok) return null;
    const j = await res.json();
    return (j && j.ok && j.bars && j.bars.length) ? j.bars : null;
  } catch (e) { return null; }
}
// 真指数对齐到本票 bars 的日期轴:起点归一为 1,缺日 ffill,起点前 null
function alignBench(bars, idxRows) {
  const byDate = {};
  (idxRows || []).forEach(r => { const c = +r.close; if (Number.isFinite(c)) byDate[String(r.date).slice(0, 10)] = c; });
  let base = null, last = null;
  const out = bars.map(b => {
    const c = byDate[b.date];
    if (c != null) { if (base == null) base = c; last = +(c / base).toFixed(4); }
    return last;
  });
  return base != null ? out : null;
}
```

`buildSymbolFromBars(meta, bars, strategies)` 加第四参 `benchBars`,374 行 `const bench = benchmark(bars, meta.seed || 1);` 改为:

```js
  const bench = benchBars ? alignBench(bars, benchBars) : null;   // 真沪深300;未给 = null,渲染端隐藏基准(绝不再用 mulberry32 合成指数)
```

导出 `lzFetchBenchmark: fetchBenchmark,`。`buildSymbol`(377-379 合成演示路径)bench 自然为 null——样例模式不再画假基准。Task 6 的 `realSymbolOf` 同步补 bench:模块级 `let BENCH_CACHE = null;`,`hydrateRealBars` 成功后顺手 `fetchBenchmark()` 填一次,`realSymbolOf` 调 `buildSymbolFromBars(meta, bars, undefined, BENCH_CACHE)`。

- [ ] **Step 7: 接线与 null 守卫**

1. `luozi-app.jsx:196-203` 真K到达路径,fetch 真K成功后同步拉基准:

```js
      window.lzFetchDailyBars(code, 250).then(async bars => {
        if (!alive) return;
        if (!bars) { setDataMode('mock'); retry(); return; }
        const bench = window.lzFetchBenchmark ? await window.lzFetchBenchmark(bars[0].date, bars[bars.length - 1].date) : null;
        const built = window.lzBuildSymbolFromBars(window.LZ_SYMBOLS[code].meta, bars, undefined, bench);
        setRealSyms(s => Object.assign({}, s, { [code]: built }));
        setDataMode('real');
      }).catch(() => { if (alive) { setDataMode('mock'); retry(); } });
```

(:219-227 的 10min 接管轮询同样补 bench 参数;:53-58 `useMemo` 的 `lzBuildSymbolFromBars(_meta, baseBars, strategies)` 改为透传 `(realSyms[code] && realSyms[code].bench) || null` 作第四参——注意此处 bench 已是对齐后的数组,需在 buildSymbolFromBars 内区分:第四参为「原始指数行」或「已对齐数组」二选一,统一约定**只传原始指数行**,app 的 useMemo 路径改存原始 idxRows 于 `realSyms[code].benchRows` 并透传。)
2. `Grep "\.bench" G:\guanlan-v2\ui\seats` 列出全部消费点(已知:luozi-fleet.jsx:41-53 bench 末值、收益曲线基准虚线、MetricsStrip「基准 +x%」),逐处加 null 守卫:bench 为 null → 基准虚线不画、「基准」数字显 `—` + title「真指数未连接」。
3. 影子组合/合议计算**不碰**(基准只是展示对标,不入台账——展示帧与计算帧分离红线)。

- [ ] **Step 8: 浏览器验证**

bump data/app/fleet ?v,reload 盯盘:
1. Network 见 `/seats/benchmark`(200);
2. 收益曲线基准虚线与「基准 +x%」变化(不再恒合成值),走势与同窗真沪深300吻合;
3. `window.GUANLAN_BACKEND=''` + reload:基准线消失、「基准 —」+ tooltip;恢复;
4. Console 0 error;舰队卡 bench 同验。

---

### Task 8: 统一 bump + 端到端验真 + 收口

**Files:**
- Modify: `G:\guanlan-v2\ui\seats\观澜 · 落子.html`(47-52 行)
- Modify: `C:\Users\<user>\.claude\projects\G--guanlan-v2\memory\luozi-live-trading-roadmap.md`

- [ ] **Step 1: 用 Edit 统一 bump**

47-52 行动过的文件全部 `?v=20260610b`(chart 未动可不 bump)。

- [ ] **Step 2: 全量 pytest**

`G:/financial-analyst/.venv/Scripts/python.exe -m pytest tests/ -q` → 全绿(含新增 test_seats_benchmark.py)。

- [ ] **Step 3: 浏览器端到端清单(Chrome MCP,逐项截图/读网络)**

| # | 操作 | 期望 |
|---|---|---|
| 1 | 打开落子页 | Console 仅 favicon 404 + Babel warn;真请求 200 |
| 2 | 校场挂载 | 6 条 `/seats/daily`;策略架显真成绩(或「取数中…」过渡) |
| 3 | 重跑演武(day) | 「真·日线 6/6」徽章;数字 ≠ 改造前合成值 |
| 4 | execTF=5min 重跑 | `/seats/daily?freq=5min`×6;「真·5min 6/6 · 窗口 ~50 交易日」 |
| 5 | 新建策略改信条→研判 | `var/seats_decisions.jsonl` 末条含自定义 creed |
| 6 | 实盘开循环 | 「下次研判 ~HH:MM」随研判刷新;daily 策略显「次一交易日」 |
| 7 | 舰队 | 6 卡「真·日线」徽章 |
| 8 | 盯盘基准 | `/seats/benchmark` 200;基准线为真沪深300 |
| 9 | 断后端反证(`GUANLAN_BACKEND=''`) | 演武拒出成绩红框;舰队「样例」;基准「—」;行情「(行情快照未连接)」 |

- [ ] **Step 4: 更新记忆**

更新 `luozi-live-trading-roadmap.md`:演武/舰队/基准接真完成、creed 自定义完成、节拍显形完成;残留待做项(如有)如实记录。

---

## Self-Review 结论(已执行)

- **规格覆盖**:用户钦定四缺口(演武真数据、decisionFreq 节拍、自定义 creed、5min execTF)→ Task 2/5/4/3;侦察新坐实的同模式缺口(舰队合成、合成基准、MarketBar 裸—)→ Task 6/7/5。研判循环节流本体已存在(panels:179-195,= P1 计划任务 2 已落地),Task 5 只补显形不重做;P1 任务 3(creed)由 Task 4 吸收;P1 任务 1(研判历史删/导出)不在本计划范围。
- **占位符扫描**:Task 4 Step 2/4、Task 6 Step 2、Task 7 Step 1/7 含「Read/Grep 后按实际落地」的发现步——jsx 无行号锚定的精确摘录,发现步均给出目标代码与判定标准,非空泛占位。
- **类型一致性**:`strategyArena` 新返回字段(freq/nCodes/nReal/missing/ready)与 Task 2 Step 4/5 消费一致;`metricsOf(eq, trades, freq)` 第三参与 Task 2 Step 1 调用一致;`buildSymbolFromBars` 第四参约定「原始指数行 benchBars」,Task 7 Step 6/7 与 Task 6 的 `realSymbolOf` 调用一致;导出名 `lzHydrateRealBars / lzHydrateRealBars5 / lzRealBarsOf / lzRealSymbolOf / lzFetchBenchmark` 前后一致。
