# 落子右栏交互重构 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把落子实盘/舰队右栏理清:右栏可滚动、实盘精简掉 scanSeat 信号队列改显真 LLM 研判流水、舰队按股列每只信号、新增「自选 vs 盯盘」开关 gate 自动研判/落账;一切诚实标注不冒充真信号。

**Architecture:** 纯前端(无构建 React UMD,window 挂载),不动后端、不重启 9999。新增一张独立 localStorage 映射存 monitored、两个新组件(`LiveDecideFlow` 真研判流水、`FleetSignalList` 舰队每股信号列),删一个旧组件(`DecisionFlow` 实盘信号队列),给右栏容器加滚动,给自动研判/落账加 monitored 闸门。验证走浏览器真机(每改 jsx 必 bump `?v`)。

**Tech Stack:** React 18 UMD + in-browser Babel(`type="text/babel"`),localStorage 持久,后端 FastAPI(本期只读不改)。

---

## 背景与契约(实施者必读)

- 设计依据见 spec:`docs/superpowers/specs/2026-06-15-luozi-right-panel-rework-design.md`(含 5 路读码核实结论)。
- **红线**:scanSeat / `evidenceFor` 是确定性启发式 / 合成示意料,任何消费方必须保留「非LLM / 示意」徽章,**绝不冒充真 agent / 真交易信号**;「真·LLM」徽章只能给 `realDecs`(真 `/seats/decide` 落盘)。台账 append-only 不就地改。
- **工程**:改 jsx 必 bump `?v=`(用 Edit 非 sed),在 `ui/seats/观澜 · 落子.html`。本期纯前端 → 无需重启 9999、无 pytest(零 Python 改动)。
- **当前 `?v`**(html 内):`luozi-data.jsx?v=20260614g`、`luozi-panels.jsx?v=20260615a`、`luozi-fleet.jsx?v=20260613e`、`luozi-app.jsx?v=20260615a`。每个任务改完把所改文件的 `?v` 升到该任务标注值。
- **验证环境**:9999 已由看门狗常驻;用 preview 浏览器导航到 `http://localhost:9999/ui/seats/观澜 · 落子.html`(或现有静态 preview server 导航过去),`?v` bump 后 reload。

## File Structure(本次改动的文件与职责)

- `ui/seats/luozi-data.jsx` — 数据层。**新增** monitored 映射(`poolIsMonitored`/`setMonitored` + localStorage `guanlan:lz:monitored:v1`)与 window 导出。
- `ui/seats/luozi-panels.jsx` — 面板层。**新增** `LiveDecideFlow`(真研判流水);**删** `DecisionFlow`(实盘信号队列);`runTimedDecide` 加 monitored 闸门;`LedgerPanel` 加「组合账」标注 + 改「9:30」文案。
- `ui/seats/luozi-app.jsx` — 主壳/布局。右栏容器加滚动;实盘右栏把 `DecisionFlow` 换 `LiveDecideFlow`;舰队分支加右栏 `FleetSignalList` + 传 `realDecs`/`onToggleMon`;条件单落账加 monitored 闸门。
- `ui/seats/luozi-fleet.jsx` — 舰队。**新增** `FleetSignalList`(每股信号列);`FleetCard`/`FleetGrid` 加盯/自选 徽章 + 开关。
- `ui/seats/README.md` — 收尾追加本次说明。

---

## Task 1: 右栏可滚动(纯修复)

**Files:**
- Modify: `ui/seats/luozi-app.jsx:674`(右栏容器加 `overflowY`)、`ui/seats/luozi-app.jsx:699-703`(末尾决策卡包裹防塌)
- Modify: `ui/seats/观澜 · 落子.html`(bump app.jsx `?v` → `20260615b`)

- [ ] **Step 1: 右栏容器加纵向滚动**

把 luozi-app.jsx:674 的右栏容器 div(`width: 372`)加 `overflowY: 'auto'`:

```jsx
            <div style={{ width: 372, flexShrink: 0, display: 'flex', flexDirection: 'column', minHeight: 0, overflowY: 'auto', background: 'var(--paper)' }}>
```

- [ ] **Step 2: 末尾决策卡包裹防塌**

把 luozi-app.jsx:699 的包裹由 `flex: 1, minHeight: 0` 改为 `flexShrink: 0`,并给决策卡区一个最小高度,避免在 auto 容器里塌成 0:

```jsx
              <div style={{ flexShrink: 0, minHeight: 320 }}>
                {selected && selected._isRun
                  ? <RunDecCard dec={selected} />
                  : <DecisionCard dec={selected} symbol={symbol} mode={mode === 'live' ? 'live' : 'backtest'} />}
              </div>
```

(`DecisionCard`/`RunDecCard` 内部已有 `height:100%, overflowY:auto`;包裹给 `minHeight:320` 后卡在右栏滚动容器里有确定高度,卡内自滚仍生效。)

- [ ] **Step 3: bump ?v**

在 `ui/seats/观澜 · 落子.html` 把 `luozi-app.jsx?v=20260615a` 改为 `luozi-app.jsx?v=20260615b`(用 Edit)。

- [ ] **Step 4: 浏览器验证**

reload 页面到实盘模式(顶栏「实盘」)。验证:
- 控制台无解析错(`preview_console_logs level=error` 为空)。
- 右栏内容(台账 + 信号区 + 条件单 + 详情卡)总高超过视口时,**右栏内部可纵向滚动**、信息不再被顶出裁掉。可用 `preview_eval` 读右栏容器 `getComputedStyle(...).overflowY === 'auto'` 与 `scrollHeight > clientHeight` 确认可滚。
- 中栏 K线 / 收益曲线高度不变(未受影响)。

---

## Task 2: monitored 数据模型(自选 vs 盯盘 的存储层)

**Files:**
- Modify: `ui/seats/luozi-data.jsx`(在盯盘池区块 `poolIsDynamic` 之后,约 :599 加函数;导出区 :1598 加 window 导出)
- Modify: `ui/seats/观澜 · 落子.html`(bump data.jsx `?v` → `20260615b`)

- [ ] **Step 1: 加 monitored 映射纯函数**

在 luozi-data.jsx `function poolIsDynamic(...)` 之后(约 :599,启动恢复持久池 `try {` 之前)插入:

```jsx
// ───────── 自选 vs 盯盘(monitored 标志,与池数组解耦,localStorage 一张映射)─────────
// monitored=true → 盯盘(可自动研判 + 自动落账);缺省/false → 自选(只看,不自动研判/落账)。
// 与 SYMBOL_META 解耦,固定 6 只底座与动态票统一处理;默认全自选(opt-in 盯盘)。
const MON_LS_KEY = 'guanlan:lz:monitored:v1';
function _monLoad() { try { return JSON.parse(localStorage.getItem(MON_LS_KEY)) || {}; } catch (e) { return {}; } }
function _monSave(map) { try { localStorage.setItem(MON_LS_KEY, JSON.stringify(map)); } catch (e) {} }
function _monCode(code) { return String(code || '').replace(/^(SH|SZ|BJ)/i, ''); }   // 与台账手调同口径
function poolIsMonitored(code) { return !!_monLoad()[_monCode(code)]; }
function setMonitored(code, on) {
  const m = _monLoad(); const c = _monCode(code);
  if (on) m[c] = true; else delete m[c];
  _monSave(m); return !!on;
}
```

- [ ] **Step 2: 挂 window 导出**

在 luozi-data.jsx 导出区(约 :1598,`lzPoolAdd: poolAdd, lzPoolRemove: poolRemove, lzPoolIsDynamic: poolIsDynamic,` 那行)后追加:

```jsx
  lzPoolIsMonitored: poolIsMonitored, lzSetMonitored: setMonitored,   // 自选 vs 盯盘
```

- [ ] **Step 3: bump ?v**

在 html 把 `luozi-data.jsx?v=20260614g` 改为 `luozi-data.jsx?v=20260615b`。

- [ ] **Step 4: 浏览器验证(preview_eval 纯函数)**

reload 后用 `preview_eval` 验证:

```js
(function(){
  var before = window.lzPoolIsMonitored('300750');
  window.lzSetMonitored('SH300750', true);            // 带前缀也归一
  var on = window.lzPoolIsMonitored('300750');
  window.lzSetMonitored('300750', false);
  var off = window.lzPoolIsMonitored('300750');
  var persisted = JSON.parse(localStorage.getItem('guanlan:lz:monitored:v1') || '{}');
  return { before: before, on: on, off: off, persistedKeys: Object.keys(persisted) };
})()
```

期望:`before=false`(默认自选)、`on=true`、`off=false`、归一生效(前缀剥除)。验证后清理:`window.lzSetMonitored('300750', false)`(保持默认全自选,不留测试态)。

---

## Task 3: 实盘单标右栏重排(删信号队列 + 真研判流水)

**Files:**
- Create(组件,写在 `ui/seats/luozi-panels.jsx`):`LiveDecideFlow`
- Delete: `ui/seats/luozi-panels.jsx:932-1002`(`sideGlyph`/`sideCN`/`DecisionFlow`,确认无外部引用后删)
- Modify: `ui/seats/luozi-app.jsx:679-681`(实盘信号区换组件)
- Modify: `ui/seats/luozi-panels.jsx:434-441`(LedgerPanel head 加「组合账」标注)
- Modify: `ui/seats/观澜 · 落子.html`(bump app.jsx → `20260615c`、panels.jsx → `20260615c`)

- [ ] **Step 1: 新增 LiveDecideFlow 组件**

在 luozi-panels.jsx 原 `// ───────── 决策流水 ─────────`(:932)位置,新增组件(替代即将删除的 DecisionFlow):

```jsx
// ───────── 实盘 · 真研判流水 ─────────
// 只显真 LLM agent 研判(realDecs[code]:定时/手动/哨兵/真跑),带「真·LLM」徽章;
// 替代已退役的 scanSeat「信号队列」(那是启发式扫描、非真信号)。按开账日起算(重开账=干净起步)。
function LiveDecideFlow({ decs, openDate }) {
  const [openKey, setOpenKey] = useState(null);
  const list = (decs || [])
    .filter(d => !openDate || String(d.asof || d.date || '').slice(0, 10) >= openDate)
    .slice().reverse();
  const dirCol = (d) => d.side === 'buy' ? 'var(--zhu)' : d.side === 'sell' ? 'var(--dai)' : 'var(--ink-3)';
  return (
    <div style={{ display: 'flex', flexDirection: 'column', minHeight: 0, height: '100%' }}>
      <div style={{ padding: '9px 13px', borderBottom: '1px solid var(--line)', flexShrink: 0, display: 'flex', alignItems: 'baseline', gap: 8 }}>
        <span className="serif" style={{ fontSize: 12.5, fontWeight: 600 }}>真 · 研判流水</span>
        <span className="mono" style={{ fontSize: 9, color: 'var(--ink-3)' }}>{list.length} 条</span>
        <span className="mono" title="只显真 LLM agent 研判(/seats/decide):定时/手动/哨兵/真跑;非 scanSeat 启发式扫描" style={{ fontSize: 8, padding: '1px 6px', borderRadius: 4, border: '1px solid var(--yin)', color: 'var(--yin)', flexShrink: 0 }}>真 · LLM</span>
      </div>
      <div style={{ flex: 1, overflowY: 'auto', minHeight: 0 }}>
        {list.length === 0 && <div style={{ padding: 14, fontSize: 11, color: 'var(--ink-3)', fontFamily: 'var(--mono)' }}>尚无真 LLM 研判 — 开「研判循环」盘中自动判,或卡内「席位 · agent 研判」手动判</div>}
        {list.map((d) => {
          const col = dirCol(d);
          const open = openKey === d.key;
          return (
            <div key={d.key} className="hover-row" style={{ borderBottom: '1px solid var(--line-soft)' }}>
              <div onClick={() => setOpenKey(open ? null : d.key)} style={{ padding: '8px 13px', cursor: 'pointer', borderLeft: '2px solid ' + (open ? col : 'transparent') }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                  <span className="serif" style={{ fontSize: 12, fontWeight: 600, color: col }}>{d.direction || (d.side === 'buy' ? '买入' : d.side === 'sell' ? '卖出' : '观望')}</span>
                  <span className="mono" style={{ fontSize: 9, color: 'var(--ink-3)' }}>{String(d.asof || d.date || '').slice(0, 16)}</span>
                  {d.conf != null && <span className="mono" style={{ fontSize: 9, color: 'var(--ink-3)' }}>置信 {d.conf}</span>}
                  <span style={{ flex: 1 }} />
                  {d.model_name && <span className="mono" style={{ fontSize: 8, color: 'var(--ink-3)' }}>{d.model_name}</span>}
                </div>
                <div className="mono" style={{ fontSize: 9, color: 'var(--ink-3)', marginTop: 3, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{d.rationale || ''}</div>
              </div>
              {open && d.reasoning && <div className="mono" style={{ padding: '0 13px 9px', fontSize: 9, color: 'var(--ink-2)', whiteSpace: 'pre-wrap', maxHeight: 220, overflowY: 'auto', lineHeight: 1.5 }}>{d.reasoning}</div>}
            </div>
          );
        })}
      </div>
    </div>
  );
}
```

- [ ] **Step 2: 实盘右栏换组件**

在 luozi-app.jsx:679-681,把实盘信号区的 `<DecisionFlow .../>` 换成 `<LiveDecideFlow .../>`:

```jsx
              {mode === 'live' && <div style={{ height: 232, flexShrink: 0, borderBottom: '1px solid var(--line)', display: 'flex', flexDirection: 'column', minHeight: 0 }}>
                <LiveDecideFlow decs={realDecs[code]} openDate={(ledger && ledger.start_date) || null} />
              </div>}
```

- [ ] **Step 3: 删除已无引用的 DecisionFlow**

grep 确认 `sideGlyph`、`sideCN`、`DecisionFlow` 仅在 luozi-panels.jsx 的 932-1002 块内被定义/使用(`DecisionFlow` 唯一调用点 app.jsx:680 已在 Step 2 移除):

Run: `rg -n "sideGlyph|sideCN|DecisionFlow" ui/seats`
Expected: 仅 panels.jsx 932-1002 内部命中(无 app.jsx、无其他文件)。

确认后删除 luozi-panels.jsx 932-1002(从 `// ───────── 决策流水 ─────────` 注释到 `DecisionFlow` 函数闭合 `}`,含 `sideGlyph`/`sideCN` 两个 helper)。若 grep 发现 `sideGlyph`/`sideCN` 有外部引用,则只删 `DecisionFlow`(936-1002)、保留两 helper。

- [ ] **Step 4: LedgerPanel 标注「组合账」**

在 luozi-panels.jsx LedgerPanel 的 `head`(:434-441)里,把 :437 的标题 span(`实盘 · 仓位台账`)与 :438 的 `重开账` span 之间,插入一个小徽章:

```jsx
      <span className="mono" title="实盘=一个组合:所有盯盘股票共用一个现金池 / 一条净值线(非按票分账)" style={{ flexShrink: 0, fontSize: 8, padding: '1px 6px', borderRadius: 4, border: '1px solid var(--line)', color: 'var(--ink-3)' }}>组合账 · 跨股共享</span>
```

- [ ] **Step 5: bump ?v**

html:`luozi-app.jsx?v=20260615b`→`20260615c`、`luozi-panels.jsx?v=20260615a`→`20260615c`。

- [ ] **Step 6: 浏览器验证**

reload 实盘模式。验证:
- 控制台无解析错。
- 实盘右栏**不再有 scanSeat「信号队列」**;出现「真 · 研判流水」(带「真·LLM」徽章)。无真研判时显诚实空文案。
- 台账标题旁出现「组合账 · 跨股共享」徽章。
- `preview_eval`:

```js
(function(){
  var t = document.body.innerText;
  return { hasReal: t.indexOf('真 · 研判流水') >= 0,
           noSignalQueue: t.indexOf('信号队列') < 0,
           hasPortfolioBadge: t.indexOf('组合账') >= 0 };
})()
```

期望:`hasReal=true`、`noSignalQueue=true`、`hasPortfolioBadge=true`。

---

## Task 4: 舰队右栏每股信号列 + 盯/自选 gate 接线

**Files:**
- Create(组件,写在 `ui/seats/luozi-fleet.jsx`):`FleetSignalList`
- Modify: `ui/seats/luozi-fleet.jsx`(`FleetCard`/`FleetGrid` 加盯/自选 徽章 + `onToggleMon`;导出 `FleetSignalList`)
- Modify: `ui/seats/luozi-app.jsx:708-712`(舰队分支加右栏 + 传 `realDecs`/`onToggleMon`)
- Modify: `ui/seats/luozi-app.jsx:687`(条件单落账加 monitored 闸门)
- Modify: `ui/seats/luozi-panels.jsx:99-100`(runTimedDecide 加 monitored 闸门)
- Modify: `ui/seats/观澜 · 落子.html`(bump app→`20260615d`、panels→`20260615d`、fleet→`20260615d`)

- [ ] **Step 1: runTimedDecide 加 monitored 闸门**

在 luozi-panels.jsx `runTimedDecide`(:99)首行守卫后加自选闸门:

```jsx
  const runTimedDecide = (reason) => {
    if (!window.lzSeatDecide || timedRef.current) return;
    if (window.lzPoolIsMonitored && !window.lzPoolIsMonitored(code)) return;   // 自选只看:不自动研判(手动「立单」/卡内手动研判仍可)
```

- [ ] **Step 2: 条件单落账加 monitored 闸门**

在 luozi-app.jsx:687 的台账买入条件里追加 monitored 判断(自选票条件单触发仍记 shadow/orderTriggers,但**不自动落进组合账**):

```jsx
  if (mode === 'live' && ledger && ledger.opened && window.lzLedgerPost && /买/.test(t.side || '') && window.lzPoolIsMonitored && window.lzPoolIsMonitored(code)) {
```

- [ ] **Step 3: FleetCard 加盯/自选 徽章 + 开关**

luozi-fleet.jsx `FleetCard` 签名加 `onToggleMon`:

```jsx
function FleetCard({ code, active, onPick, isActive, onToggleMon }) {
```

在 :95 信号徽章 `<span ...>{sig.t}</span>` 之后、`<span style={{ flex: 1 }} />`(:96)之前,插入盯/自选 chip:

```jsx
        <span onClick={(e) => { e.stopPropagation(); onToggleMon && onToggleMon(code); }}
          title={(window.lzPoolIsMonitored && window.lzPoolIsMonitored(code)) ? '盯盘中:自动研判 + 落账资格(点=转自选)' : '自选只看:不自动研判 / 落账(点=转盯盘)'}
          className="mono" style={{ fontSize: 8.5, padding: '2px 7px', borderRadius: 8, cursor: 'pointer',
            border: '1px solid ' + ((window.lzPoolIsMonitored && window.lzPoolIsMonitored(code)) ? 'var(--yin)' : 'var(--line)'),
            color: (window.lzPoolIsMonitored && window.lzPoolIsMonitored(code)) ? 'var(--paper)' : 'var(--ink-3)',
            background: (window.lzPoolIsMonitored && window.lzPoolIsMonitored(code)) ? 'var(--yin)' : 'transparent' }}>
          {(window.lzPoolIsMonitored && window.lzPoolIsMonitored(code)) ? '● 盯盘' : '○ 自选'}
        </span>
```

- [ ] **Step 4: 新增 FleetSignalList 组件**

在 luozi-fleet.jsx `FleetGrid` 之后、`Object.assign(window, ...)`(:126)之前,新增:

```jsx
// ───────── 舰队右栏:每股信号列(网格旁) ─────────
// 每行 = 名/代码 + 最新 scanSeat 信号(徽章「非LLM」)+ 今日真 LLM 研判(若有,徽章「真·LLM」)+ 盯/自选 开关。
// 点行 = 聚焦该股切单标(onPick)。
function FleetSignalList({ realDecs, onPick, onToggleMon, activeCode }) {
  const codes = window.LZ_SYMBOL_META.map(m => m.code);
  const today = new Date().toISOString().slice(0, 10);
  return (
    <div style={{ width: 344, flexShrink: 0, borderLeft: '1px solid var(--line)', display: 'flex', flexDirection: 'column', minHeight: 0, background: 'var(--paper)' }}>
      <div style={{ padding: '10px 14px', borderBottom: '1px solid var(--line)', flexShrink: 0, display: 'flex', alignItems: 'baseline', gap: 8 }}>
        <span className="serif" style={{ fontSize: 13, fontWeight: 600 }}>每股信号</span>
        <span className="mono" style={{ fontSize: 9, color: 'var(--ink-3)' }}>{codes.length} 只 · 点行看单股</span>
      </div>
      <div style={{ flex: 1, overflowY: 'auto', minHeight: 0 }}>
        {codes.map(code => {
          const S = (window.lzRealSymbolOf && window.lzRealSymbolOf(code)) || window.LZ_SYMBOLS[code];
          const strat = (window.lzStrategyForCode ? window.lzStrategyForCode(code) : [])[0] || null;
          const sid = strat && strat.id;
          const scan = (sid && S && S.decisions) ? S.decisions.filter(d => d.seat === sid).slice(-1)[0] : null;
          const rds = (realDecs && realDecs[code]) || [];
          const rdToday = rds.filter(d => String(d.asof || d.date || '').slice(0, 10) === today).slice(-1)[0];
          const mon = window.lzPoolIsMonitored && window.lzPoolIsMonitored(code);
          const scanCol = scan ? (scan.side === 'buy' ? 'var(--zhu)' : scan.side === 'sell' ? 'var(--dai)' : 'var(--ink-3)') : 'var(--ink-3)';
          return (
            <div key={code} onClick={() => onPick(code)} className="hover-row" style={{ padding: '9px 14px', borderBottom: '1px solid var(--line-soft)', cursor: 'pointer', borderLeft: '2px solid ' + (code === activeCode ? 'var(--yin)' : 'transparent') }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 7 }}>
                <span className="serif" style={{ fontSize: 12, fontWeight: 600 }}>{(S && S.meta && S.meta.name) || code}</span>
                <span className="mono" style={{ fontSize: 9, color: 'var(--ink-3)' }}>{code}</span>
                <span style={{ flex: 1 }} />
                <span onClick={(e) => { e.stopPropagation(); onToggleMon && onToggleMon(code); }}
                  title={mon ? '盯盘中:自动研判 + 落账资格(点=转自选)' : '自选只看:不自动研判 / 落账(点=转盯盘)'}
                  className="mono" style={{ fontSize: 8, padding: '1px 6px', borderRadius: 8, cursor: 'pointer',
                    border: '1px solid ' + (mon ? 'var(--yin)' : 'var(--line)'), color: mon ? 'var(--paper)' : 'var(--ink-3)', background: mon ? 'var(--yin)' : 'transparent' }}>
                  {mon ? '● 盯盘' : '○ 自选'}
                </span>
              </div>
              <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginTop: 4 }}>
                <span className="mono" style={{ fontSize: 9, color: scanCol }}>{scan ? (scan.warn ? '预警' : scan.side === 'buy' ? '买入' : '卖出') : '持有·观望'}</span>
                <span className="mono" style={{ fontSize: 7.5, padding: '0 4px', borderRadius: 3, border: '1px solid var(--line)', color: 'var(--ink-3)' }}>非LLM</span>
                {rdToday && <span className="mono" style={{ fontSize: 8, padding: '0 5px', borderRadius: 3, border: '1px solid var(--yin)', color: 'var(--yin)' }}>真·LLM {rdToday.direction || ''}</span>}
                <span style={{ flex: 1 }} />
                {scan && <span className="mono" title={scan.note || ''} style={{ fontSize: 8.5, color: 'var(--ink-3)', maxWidth: 118, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{scan.note || ''}</span>}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
```

- [ ] **Step 5: 导出 FleetSignalList + FleetGrid 透传 onToggleMon**

luozi-fleet.jsx 末尾导出(:126)加 `FleetSignalList`:

```jsx
Object.assign(window, { FleetGrid, MiniCandles, FleetSignalList });
```

`FleetGrid` 签名(:104)加 `onToggleMon` 并透传给卡(:120):

```jsx
function FleetGrid({ active, onPick, activeCode, onToggleMon }) {
```
```jsx
        {codes.map(c => <FleetCard key={c} code={c} active={active} onPick={onPick} isActive={c === activeCode} onToggleMon={onToggleMon} />)}
```

- [ ] **Step 6: 舰队分支加右栏**

把 luozi-app.jsx:708-712 舰队分支改为「网格 + FleetSignalList」横向布局:

```jsx
      ) : (
        <div style={{ flex: 1, minHeight: 0, display: 'flex' }}>
          <div style={{ flex: 1, minHeight: 0, display: 'flex', flexDirection: 'column' }}>
            <FleetGrid active={active} activeCode={code}
              onPick={(c) => { setCode(c); setView('single'); }}
              onToggleMon={(c) => { if (window.lzSetMonitored) window.lzSetMonitored(c, !(window.lzPoolIsMonitored && window.lzPoolIsMonitored(c))); setPoolTick(t => t + 1); }} />
          </div>
          <FleetSignalList realDecs={realDecs} activeCode={code}
            onPick={(c) => { setCode(c); setView('single'); }}
            onToggleMon={(c) => { if (window.lzSetMonitored) window.lzSetMonitored(c, !(window.lzPoolIsMonitored && window.lzPoolIsMonitored(c))); setPoolTick(t => t + 1); }} />
        </div>
      )}
```

- [ ] **Step 7: bump ?v**

html:app→`20260615d`、panels→`20260615d`、fleet `20260613e`→`20260615d`。

- [ ] **Step 8: 浏览器验证**

reload。验证:
- 控制台无解析错。
- 切「舰队」:网格仍在 + **右侧出现「每股信号」列**,每行有 名/代码、scanSeat 信号 +「非LLM」徽章、盯/自选 chip。
- 点某行 → 切回单标且 code 变为该股。
- 盯/自选 开关:点一只的「○ 自选」→ 变「● 盯盘」,localStorage `guanlan:lz:monitored:v1` 出现该 code;再点回自选。`preview_eval`(在舰队视图):

```js
(function(){
  return { hasList: document.body.innerText.indexOf('每股信号') >= 0,
           hasMon: /[●○]\s*(盯盘|自选)/.test(document.body.innerText) };
})()
```

期望 `hasList=true`、`hasMon=true`。验证后清理测试态:`localStorage.removeItem('guanlan:lz:monitored:v1')`。

- [ ] **Step 9: monitored 闸门静态核对**

`preview_eval`(不真跑 LLM):

```js
(function(){
  return { gateFn: typeof window.lzPoolIsMonitored, defaultMon: window.lzPoolIsMonitored('300750') };
})()
```

期望 `gateFn='function'`、`defaultMon=false`(默认自选 → runTimedDecide 早返、onTrigger 落账被 gate 拦)。盘中真触发端到端留待真盘人工确认(周末无 fresh 报价无法触发定时器,属预期)。

---

## Task 5: 文案诚实化

**Files:**
- Modify: `ui/seats/luozi-panels.jsx:662`(「9:30」文案)
- Modify: `ui/seats/观澜 · 落子.html`(bump panels→`20260615e`)

- [ ] **Step 1: 改「9:30 起随行情」为与代码一致**

把 luozi-panels.jsx:662 整行替换:

```jsx
      <div className="mono" style={{ fontSize: 8, color: 'var(--ink-3)', padding: '0 14px 9px' }}>盘中自动研判 → 落账:仅「研判循环」开启 + 页面在线 + 盘中(有实时报价)时;无后端定时器,关页面即停。</div>
```

- [ ] **Step 2: bump ?v**

html:`luozi-panels.jsx?v=20260615d`→`20260615e`。

- [ ] **Step 3: 浏览器验证**

reload 实盘。`preview_eval`:

```js
(function(){
  var t = document.body.innerText;
  return { has930: t.indexOf('9:30') >= 0, hasHonest: t.indexOf('无后端定时器') >= 0 };
})()
```

期望 `has930=false`、`hasHonest=true`。控制台无解析错。

---

## Task 6: 收口(全链路 e2e + 文档)

**Files:**
- Modify: `ui/seats/README.md`(追加本次说明)

- [ ] **Step 1: 全链路浏览器 e2e**

reload 干净页面,过一遍:
1. 实盘右栏可纵向滚动、内容不被裁(Task 1)。
2. 实盘右栏无「信号队列」、有「真 · 研判流水」+「真·LLM」徽章 + 台账「组合账」徽章(Task 3)。
3. 舰队网格 + 右栏「每股信号」列、每行非LLM 徽章 + 盯/自选 开关;点行切单标;切换盯/自选 持久(Task 4)。
4. 文案无「9:30」(Task 5)。
5. 控制台全程无解析错(`preview_console_logs level=error` 空)。
6. 截图存证(`preview_screenshot`):实盘右栏 + 舰队右栏各一张。

- [ ] **Step 2: 清理测试态**

确保 `localStorage 'guanlan:lz:monitored:v1'` 回到默认(无盯盘 / 全自选),不留验证残留。

- [ ] **Step 3: README 追加**

在 `ui/seats/README.md` 价格行为那节后,追加一段说明本次重构(右栏滚动 / 实盘真研判流水替信号队列 / 舰队每股信号列 / 自选vs盯盘 / 文案诚实化,`?v=20260615b~e`),并指向 spec/plan 路径。

- [ ] **Step 4: 无后端改动确认**

确认本次零 Python 改动(`rg` 仅前端 jsx + html + README + docs 变更)→ 无需重启 9999、pytest 后端基线不受影响。若意外触及 Python,跑 `G:\financial-analyst\.venv\Scripts\python.exe -m pytest -q` 并附结果。

---

## Self-Review(规划自查,已过)

- **Spec 覆盖**:① 右栏滚动=Task 1;② 实盘右栏重排(删信号队列+真研判流水+重开账日起算)=Task 3;③ 舰队每股信号列=Task 4;④ 自选vs盯盘(数据模型=Task 2,gate+UI=Task 4)；⑤ 文案=Task 5。全覆盖。
- **类型/命名一致**:`poolIsMonitored`/`setMonitored`(data)↔ `window.lzPoolIsMonitored`/`window.lzSetMonitored`(消费);`LiveDecideFlow(decs, openDate)`、`FleetSignalList(realDecs, onPick, onToggleMon, activeCode)`、`FleetGrid/FleetCard(... onToggleMon)` 各处签名一致;`realDecs[code]` 形状 `{key,seat,idx,date,side,direction,conf,rationale,reasoning,asof,model_name}`(app.jsx:310-311)与 LiveDecideFlow 字段一致。
- **占位符**:无 TBD/TODO;所有改动给了完整代码或精确 old→new。
- **红线**:scanSeat 信号在舰队列保留「非LLM」徽章;真研判才给「真·LLM」;台账不改后端、append-only 不破。
