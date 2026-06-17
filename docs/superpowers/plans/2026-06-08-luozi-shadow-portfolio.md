# 第 2 期 · B1 影子组合(最小诚实版)Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`).

**Goal:** 把实盘的"假合议(复用历史回测)"换成**诚实的影子组合绩效**:用户点「开始跟踪」后,买入条件单触发即按信号价记一笔影子持仓,止损/止盈到价平仓,现价 mark-to-market,自上线日起算真实盘指标——彻底消灭"实盘=复盘"的名实不符。

**Architecture:** 当前票口径(与现有按票合议对齐)。纯函数影子内核放 `luozi-data.jsx`(load/save localStorage + addEntry + checkExits + computeShadowMetrics);`luozi-app.jsx` 持状态、接 onTrigger 进场、用 6s quote 轮询查出场、算指标、给「开始跟踪」按钮;`MetricsStrip` 实盘分支改吃影子指标 + 诚实标 + 空态。复盘分支**完全不动**。无后端改动。

**Tech Stack:** no-build React UMD;`localStorage` 持久;`new Date()` 取日期。

---

## 本仓特例(同第 1 期)
- 无前端测试框架 → 用 Chrome MCP 重载 + 0 console error + 行为观察验证;纯函数内核可在 console 直接调 `window.lzShadow*` 验。
- 非 git 仓 → 无 commit;"提交边界"= 改完 jsx 后 6 个 `?v=` 同步 +1。当前 `?v=20260608d16`,本期 `d17 → d18 → d19`。
- **验证用稳定的 9999**(agent 起的 9998 会被回收;9999 是用户长期跑的,同一份磁盘 jsx)。URL `http://127.0.0.1:9999/ui/seats/观澜 · 落子.html`。市场开则盘中验证,休市做可做部分,**不伪造成交**。
- 红线:系统只出信号、不代下单;诚实空态不编造;不动 symbol.bars/scan/复盘合议。

---

## 数据模型(localStorage)
键:`guanlan:lz:shadow:<code>`(每票一份)。值:
```
{ goLive: 'YYYY-MM-DD' | null,
  positions: [ { id, seat, side, entry, date, stop, take,
                 status: 'open' | 'closed', exit, exitDate, exitReason } ] }
```
- `side`:本期只处理 `'买入'`(含"买")进场;其它(观望/卖出)不记进场。
- `entry`/`exit`:价(数值);`stop`/`take` 来自触发的条件单(可空)。
- `exitReason`:`'止盈'` | `'止损'`。

---

## File Structure
- **Modify `ui/seats/luozi-data.jsx`**:加纯函数影子内核 + `Object.assign(window,{...})` 导出(lzShadowLoad/lzShadowSave/lzShadowAddEntry/lzShadowCheckExits/lzShadowMetrics)。
- **Modify `ui/seats/luozi-app.jsx`**:shadow 状态(按 code)+ onTrigger 进场 + quote 出场检查 + 开始跟踪按钮 + 算指标 + 传 MetricsStrip。
- **Modify `ui/seats/luozi-panels.jsx`**:`MetricsStrip` 实盘分支 → 影子指标卡 + 诚实标 + 空态;`OrderWatchPanel` 的 onTrigger 带上 stop/take。
- **Modify `ui/seats/观澜 · 落子.html`**:`?v` 推进。

---

## Task 1: 影子内核(纯函数,luozi-data.jsx)

**Files:** Modify `ui/seats/luozi-data.jsx`(在文件末尾 `Object.assign(window, {` 之前加函数,并在该导出对象里加 5 个键);Modify HTML(`?v`→`d17`)。

- [ ] **Step 1: 加纯函数(放在 luozi-data.jsx 末尾 `Object.assign(window, {` 之前)**

```jsx
// ───────── B1 影子组合(最小诚实版,当前票口径;localStorage 持久)─────────
const LZ_SHADOW_KEY = (code) => 'guanlan:lz:shadow:' + code;
function shadowLoad(code) {
  try { const s = JSON.parse(localStorage.getItem(LZ_SHADOW_KEY(code)) || 'null');
    if (s && Array.isArray(s.positions)) return { goLive: s.goLive || null, positions: s.positions }; } catch (e) {}
  return { goLive: null, positions: [] };
}
function shadowSave(code, shadow) {
  try { localStorage.setItem(LZ_SHADOW_KEY(code), JSON.stringify(shadow)); } catch (e) {}
  return shadow;
}
// 进场:买入触发 → 记一笔 open 影子持仓(按信号价 fill)。非买入/无 goLive/重复 id → 不记。
function shadowAddEntry(shadow, ev) {
  if (!shadow.goLive) return shadow;
  const side = ev.side || '';
  if (!/买/.test(side)) return shadow;                       // 本期只进场买入
  const id = ev.id + '·' + ev.at;                            // 去重键(同一触发只记一次)
  if (shadow.positions.some(p => p.id === id)) return shadow;
  const entry = +ev.fill;
  if (!isFinite(entry)) return shadow;
  const pos = { id, seat: ev.seat, side: '买入', entry, date: String(ev.at).slice(0, 10),
    stop: (ev.stop != null && isFinite(+ev.stop)) ? +ev.stop : null,
    take: (ev.take != null && isFinite(+ev.take)) ? +ev.take : null,
    status: 'open', exit: null, exitDate: null, exitReason: null };
  return { goLive: shadow.goLive, positions: shadow.positions.concat([pos]) };
}
// 出场:对 open 持仓,按现价查 止盈(price≥take)/止损(price≤stop)。返回 {shadow, changed}。
function shadowCheckExits(shadow, price, asofDate) {
  if (!isFinite(+price)) return { shadow, changed: false };
  let changed = false;
  const positions = shadow.positions.map(p => {
    if (p.status !== 'open') return p;
    if (p.take != null && price >= p.take) { changed = true; return Object.assign({}, p, { status: 'closed', exit: p.take, exitDate: asofDate || null, exitReason: '止盈' }); }
    if (p.stop != null && price <= p.stop) { changed = true; return Object.assign({}, p, { status: 'closed', exit: p.stop, exitDate: asofDate || null, exitReason: '止损' }); }
    return p;
  });
  return { shadow: changed ? { goLive: shadow.goLive, positions } : shadow, changed };
}
// 指标:已平按 (exit-entry)/entry 复利成累计净值;未平按现价 mark-to-market 计浮动。
function shadowMetrics(shadow, price) {
  const closed = shadow.positions.filter(p => p.status === 'closed');
  const open = shadow.positions.filter(p => p.status === 'open');
  const rOf = (p, px) => (p.entry ? (px - p.entry) / p.entry : 0);     // 买入方向
  let eq = 1; closed.forEach(p => { eq *= (1 + rOf(p, p.exit)); });     // 已平复利
  const realized = eq - 1;
  const wins = closed.filter(p => rOf(p, p.exit) > 0);
  const losses = closed.filter(p => rOf(p, p.exit) <= 0);
  const avg = a => a.length ? a.reduce((x, y) => x + y, 0) / a.length : 0;
  const aw = avg(wins.map(p => rOf(p, p.exit)));
  const al = Math.abs(avg(losses.map(p => rOf(p, p.exit))));
  const unreal = (isFinite(+price) && open.length) ? avg(open.map(p => rOf(p, +price))) : 0;  // 未平均浮动
  const equityNow = eq * (1 + unreal) - 1;                              // 含浮动的当前累计
  return {
    goLive: shadow.goLive,
    nOpen: open.length, nClosed: closed.length,
    realized, equityNow, unreal,
    winRate: closed.length ? wins.length / closed.length : null,
    plRatio: al ? aw / al : (aw ? 99 : null),
  };
}
```

- [ ] **Step 2: 导出 5 函数**。在文件末尾 `Object.assign(window, {` 对象里加(与既有 `lzFrameData: frameData` 等同款):

```jsx
  lzShadowLoad: shadowLoad, lzShadowSave: shadowSave, lzShadowAddEntry: shadowAddEntry,
  lzShadowCheckExits: shadowCheckExits, lzShadowMetrics: shadowMetrics,
```

- [ ] **Step 3: `?v`→d17**。Run: `sed -i 's/?v=20260608d16/?v=20260608d17/g' "ui/seats/观澜 · 落子.html" && grep -c 20260608d17 "ui/seats/观澜 · 落子.html"` → Expected `6`.

- [ ] **Step 4: 验证(纯函数,console/preview_eval on 9999)**。重载页面后,在控制台跑:
```js
let s = { goLive: '2026-06-08', positions: [] };
s = window.lzShadowAddEntry(s, { id: 'x', at: '2026-06-08 14:00', side: '买入', fill: 100, stop: 95, take: 110, seat: 'momentum' });
let r1 = window.lzShadowCheckExits(s, 111, '2026-06-08'); // 应止盈 closed exit110
let m = window.lzShadowMetrics(r1.shadow, 111);
console.log(JSON.stringify({nClosed:m.nClosed, realized:m.realized, winRate:m.winRate}));
```
Expected: `nClosed:1, realized:0.1, winRate:1`(止盈到 110,(110-100)/100=0.1)。0 console error。

---

## Task 2: app 接线(luozi-app.jsx + OrderWatchPanel onTrigger 带 stop/take)

**Files:** Modify `ui/seats/luozi-app.jsx`;Modify `ui/seats/luozi-panels.jsx`(OrderWatchPanel 两处 onTrigger);Modify HTML(`?v`→`d18`)。

- [ ] **Step 1: shadow 状态 + 按 code 加载**。在 `const [quote, setQuote] = useState(null);`(约第 39 行)附近加:
```jsx
  const [shadow, setShadow] = useState({ goLive: null, positions: [] });
  useEffect(() => { setShadow(window.lzShadowLoad ? window.lzShadowLoad(code) : { goLive: null, positions: [] }); }, [code]);
```

- [ ] **Step 2: onTrigger 进场**。现有 `OrderWatchPanel` 挂载的 `onTrigger`(约第 293 行,即 `onTrigger={(t) => setOrderTriggers(...)}`)改成同时记影子进场(保留原 orderTriggers 标 K 线):
```jsx
onTrigger={(t) => {
  setOrderTriggers(ts => [...ts.filter(x => x.id !== t.id), t]);
  if (mode === 'live' && window.lzShadowAddEntry) setShadow(sh => { const ns = window.lzShadowAddEntry(sh, t); if (ns !== sh && window.lzShadowSave) window.lzShadowSave(code, ns); return ns; });
}}
```

- [ ] **Step 3: quote 出场检查**。新增一个跟随 quote 的 effect(放在 live quote 轮询 effect 附近):
```jsx
  useEffect(() => {
    if (mode !== 'live' || !quote || quote.price == null || !window.lzShadowCheckExits) return;
    setShadow(sh => { const r = window.lzShadowCheckExits(sh, +quote.price, quote.asofDate); if (r.changed && window.lzShadowSave) window.lzShadowSave(code, r.shadow); return r.changed ? r.shadow : sh; });
  }, [quote, mode, code]);
```

- [ ] **Step 4: 算指标 + 开始跟踪 + 传 MetricsStrip**。在 `const consensus = ...`(约第 66 行)附近加:
```jsx
  const shadowM = (mode === 'live' && window.lzShadowMetrics) ? window.lzShadowMetrics(shadow, quote && quote.price) : null;
  const startTracking = () => { const d = new Date().toISOString().slice(0, 10); setShadow(sh => { const ns = { goLive: d, positions: sh.positions }; if (window.lzShadowSave) window.lzShadowSave(code, ns); return ns; }); };
```
并把 `MetricsStrip` 挂载(约第 257 行)追加两个 prop:`shadowM={shadowM} onStartTracking={startTracking}`(其余 prop 原样)。

- [ ] **Step 5: OrderWatchPanel 的 onTrigger 带上 stop/take**(`ui/seats/luozi-panels.jsx`)。`OrderWatchPanel` 内有两处 `if (... onTrigger) onTrigger({ id: ..., at: ..., side: ..., fill: ..., seat: order.seat })`:
  - check() 内(回放验触发,用 `o`):`onTrigger({ id: code + '·' + order.seat, at: f.at, side: o.side, fill: f.fill, seat: order.seat, stop: o.stop, take: o.take });`
  - live 监控内(用 `o2`):`onTrigger({ id: code + '·' + order.seat + '·live', at: ctx.asofDate || ctx.asof, side: o2.side, fill: r.fill, seat: order.seat, stop: o2.stop, take: o2.take });`
  各自只在尾部加 `stop`/`take` 两键,其余不动。

- [ ] **Step 6: `?v`→d18**。Run: `sed -i 's/?v=20260608d17/?v=20260608d18/g' "ui/seats/观澜 · 落子.html" && grep -c 20260608d18 "ui/seats/观澜 · 落子.html"` → `6`.

- [ ] **Step 7: 验证(9999)**。重载、切实盘、0 console error;console 查 `window.lzShadowLoad(<当前 code 如 'SZ300750'>)` 返回 `{goLive,positions}` 结构。(进出场真链路 Task 3 出 UI 后端到端验。)

---

## Task 3: MetricsStrip 实盘 → 影子指标 + 诚实标 + 空态 + 开始跟踪按钮

**Files:** Modify `ui/seats/luozi-panels.jsx`(`MetricsStrip`);Modify HTML(`?v`→`d19`)。

- [ ] **Step 1: `MetricsStrip` 签名加 prop**。`function MetricsStrip({ m, benchTotal, label, symbol, rt, mode, quote })` → 加 `shadowM, onStartTracking`。

- [ ] **Step 2: 6 张 MetricCard 按 mode 三分支**。把现有那 6 行 `<MetricCard label="累计收益".../> … <MetricCard label="盈亏比".../>` 整体替换为:
```jsx
{mode === 'live'
  ? (shadowM && shadowM.goLive
    ? (<>
        <MetricCard label="累计·影子" value={pct(shadowM.equityNow)} sub={'自 ' + shadowM.goLive} color={upc(shadowM.equityNow)} />
        <MetricCard label="已实现" value={pct(shadowM.realized)} color={upc(shadowM.realized)} />
        <MetricCard label="浮动" value={pct(shadowM.unreal)} color={upc(shadowM.unreal)} />
        <MetricCard label="持仓" value={String(shadowM.nOpen)} sub={shadowM.nClosed + ' 已平'} />
        <MetricCard label="胜率" value={shadowM.winRate == null ? '—' : (shadowM.winRate * 100).toFixed(0) + '%'} sub={shadowM.nClosed + ' 笔'} />
        <MetricCard label="盈亏比" value={shadowM.plRatio == null ? '—' : plFmt(shadowM.plRatio)} />
        <div style={{ display: 'flex', alignItems: 'center', padding: '0 14px', flexShrink: 0 }}>
          <span className="mono" title={'信号绩效·影子组合·自 ' + shadowM.goLive + ' 起·按信号价记账·未计真实成交/滑点'} style={{ fontSize: 8, color: 'var(--ink-3)', lineHeight: 1.4, maxWidth: 150, whiteSpace: 'normal' }}>信号绩效·影子组合·自 {shadowM.goLive} 起·未计真实成交</span>
        </div>
      </>)
    : (<div style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '0 16px', flex: 1 }}>
        <span className="serif" style={{ fontSize: 12, color: 'var(--ink-3)' }}>实盘绩效未开始跟踪(不显历史回测数字)</span>
        <span onClick={onStartTracking} className="serif" style={{ fontSize: 11, color: 'var(--paper)', background: 'var(--yin)', borderRadius: 6, padding: '4px 12px', cursor: 'pointer' }}>▶ 开始跟踪(自今日起记影子组合)</span>
      </div>))
  : (<>
      <MetricCard label="累计收益" value={pct(m.total)} sub={'基准 ' + pct(benchTotal)} color={upc(m.total)} />
      <MetricCard label="年化" value={pct(m.annual)} color={upc(m.annual)} />
      <MetricCard label="SHARPE" value={m.sharpe.toFixed(2)} color={m.sharpe >= 1 ? 'var(--ink)' : 'var(--dai)'} />
      <MetricCard label="最大回撤" value={pct(m.mdd)} color="var(--dai)" />
      <MetricCard label="胜率" value={(m.winRate * 100).toFixed(0) + '%'} sub={m.nWin + '/' + m.nTrades + ' 笔'} />
      <MetricCard label="盈亏比" value={plFmt(m.plRatio)} />
    </>)}
```
(`upc`/`pct`/`plFmt` 已在作用域内。复盘分支 = 原 6 卡,逐字保留。)

- [ ] **Step 3: `?v`→d19**。Run: `sed -i 's/?v=20260608d18/?v=20260608d19/g' "ui/seats/观澜 · 落子.html" && grep -c 20260608d19 "ui/seats/观澜 · 落子.html"` → `6`.

- [ ] **Step 4: 端到端验证(9999)**。
  1. 重载、切实盘 → 顶栏指标条显**空态**「实盘绩效未开始跟踪」+「▶ 开始跟踪」(**不再显 +5.9%/Sharpe0.88 回测数字**)。
  2. 点「开始跟踪」→ 影子卡:累计·影子 +0.0%、持仓 0、胜率 —、自 YYYY-MM-DD;诚实标可见。0 console error。
  3. 切复盘 → 6 张回测卡照旧(+5.9%/年化/SHARPE…),无影子、无空态。
  4. 重载后(实盘)→ goLive 仍在(localStorage 持久),仍显影子卡。

---

## Task 4: 端到端 + README
- [ ] **Step 1**: 复盘/实盘切换回归(实盘空态→开始跟踪→影子卡;复盘原样);localStorage 跨重载持久。0 error,截图留证。
- [ ] **Step 2**: README 加一条:第 2 期 B1 影子组合(最小诚实版)—— 当前票口径、进场买入触发按信号价、出场止损/止盈到价、现价 MTM、自上线日起、localStorage、替换实盘假合议为影子指标+诚实标+空态、复盘不动;注明 maxHold/卖信号/持仓研判/跨票聚合归后续。`?v=20260608d19`。

---

## Self-Review(对照 spec §2.6 B1)
- 覆盖:影子台账(localStorage)/进场(买入触发按信号价)/出场(止损止盈到价)/MTM(现价)/自上线日(开始跟踪按钮)/替换假合议(MetricsStrip 实盘分支)/诚实标+空态 —— 均有任务。
- 范围外(本期不做,spec/用户已划)= maxHold、卖信号出场、持仓时研判变继续持/平、跨票聚合;均在 README 注明。
- 命名一致:`lzShadowLoad/Save/AddEntry/CheckExits/Metrics`、`shadow`/`shadowM`/`onStartTracking`/`startTracking`、字段 `goLive/positions/entry/exit/stop/take/status/exitReason` 全程一致;`shadowCheckExits` 返回 `{shadow,changed}`(Task2 Step3 按 `r.changed`/`r.shadow` 解构,与 Task1 一致)。
- 诚实红线:空态绝不回退复盘数字;影子标「未计真实成交/滑点」;系统只出信号不代下单(沿用)。
