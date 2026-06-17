# 第 2 期·补 · 持仓感知研判 + 研判平仓 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development. Steps use `- [ ]`.

**Goal:** 把"最小 B1"补成"完整 B1"的持仓管理半边:当前票+席位有未平影子持仓时,研判变**持仓感知**(LLM 判 继续持有/卖出),判卖出即按现价平掉影子持仓(`研判平`),与既有止损/止盈出场并存。

**Architecture:** 后端 `/seats/order` 加可选持仓上下文(hold_entry/hold_since/hold_days),有则换"管理持仓"提示、side 可为卖出。前端:`seatOrder` 带 hold;`OrderWatchPanel` 从传入的 `positions` 找本席位未平仓→研判带 hold、显持仓→研判出"卖出"则回调 `onClose`;`luozi-app` 把 `shadow.positions` 传下、`onClose` 用 `shadowClose` 按现价平仓+存。无新数据源。

**Tech Stack:** FastAPI 薄壳(`guanlan_v2/seats/api.py`,改后**重启 9998+9999**);no-build React UMD;localStorage。

---

## 本仓特例
- 无前端测试框架 → Chrome MCP 重载 + 0 console error + 行为/JS 验证。纯函数用 `javascript_tool` 调 `window.lzShadow*` 验。
- 非 git → 无 commit;"提交边界"= jsx 改完同步 6 个 `?v=`(当前 `d20`,本期 `d21 → d22`)。**后端改 .py 需重启 9998+9999**(见 README 重启法;带 `HTTP_PROXY/HTTPS_PROXY=http://127.0.0.1:7890`,否则 LLM 端点挂)。
- **服务器易掉**:agent 起的 9998/9999 会被回收;验证用当下活着的那个(curl 静态资源探活,如 `/ui/seats/luozi-data.jsx?v=...` 返 200)。市场开则盘中验,休市做可做部分,不伪造成交。
- 红线:系统只出信号、不代下单(平仓=出"该平"信号,影子按现价记;用户自己手机操作);诚实空态;复盘分支不动。

---

## Task 1: 后端 `/seats/order` 持仓感知分支(api.py)

**Files:** Modify `guanlan_v2/seats/api.py`(`/seats/order` 处理函数);重启 9998+9999。

- [ ] **Step 1: 读现有 `/seats/order` 处理函数**(grep `"/order"` 或 `def ` 附近;它现取 code/seat/tf,构 ctx,调 deepseek-chat 出条件单 `{side,triggers,stop,take,validity,note}`)。理解其 ctx 构建(day 用日线指标、5min 用 `_ctx5_sync`)与 prompt 拼装、JSON 解析。

- [ ] **Step 2: 加可选持仓上下文参数**。给该路由加三个**可选** query 参:`hold_entry: float | None = None`、`hold_since: str | None = None`、`hold_days: int | None = None`(FastAPI `Query(None)`)。在 ctx 构好、`price` 已知后计算:
```python
held = hold_entry is not None and hold_entry > 0
pnl_pct = ((price / hold_entry - 1.0) * 100.0) if held and price else None
```
(`price` 用该函数已有的现价变量名;若变量名不同,用现成的那个。)

- [ ] **Step 3: 持仓时换提示词 + 标注响应**。当 `held` 为真,把"拟新条件单"的用户提示替换为"管理持仓"提示(保留同样的 JSON 输出结构,便于复用解析):
  - 提示大意(中文,拼进现有 user 段):`你已持有该股:进场价 {hold_entry}、持有约 {hold_days} 日、当前价 {price}、浮动盈亏 {pnl_pct:.2f}%。请结合当前量价/指标判断该【继续持有】还是【了结卖出】。继续持有 → side 填"观望";了结 → side 填"卖出"并在 note 给理由。仍按原 JSON 结构输出(side/triggers/stop/take/validity/note);卖出时 triggers 可为空或给一个保护性触发。`
  - 在返回的顶层 dict 加 `"held": held, "pnlPct": pnl_pct`(其余字段不变)。
  - 未持仓(held=False)时**行为完全不变**(原拟新单逻辑、原 prompt)。

- [ ] **Step 4: 重启 9998+9999**(.py 改了)。按 README:两个进程 `python guanlan_v2/server.py`,9998 带 `GUANLAN_PORT=9998`,均带 `HTTP_PROXY/HTTPS_PROXY=http://127.0.0.1:7890`、`NO_PROXY=127.0.0.1,localhost`、`PYTHONPATH=G:\guanlan-v2`。

- [ ] **Step 5: 后端验证(curl)**。
  - 未持仓回归:`curl "http://127.0.0.1:9999/seats/order?code=300750&seat=momentum&tf=day"` → `ok:true`、`held` 缺或 false、side 多为买入/观望(原行为)。
  - 持仓感知:`curl "http://127.0.0.1:9999/seats/order?code=300750&seat=momentum&tf=day&hold_entry=300&hold_since=2026-05-01&hold_days=20"` → `ok:true`、`held:true`、`pnlPct` 为数值(现价 ~393、进场 300 → 约 +31%)、side ∈ {观望,卖出}、note 体现"持有/了结"语义。

---

## Task 2: data 内核 —— seatOrder 带 hold + shadowClose(luozi-data.jsx)

**Files:** Modify `ui/seats/luozi-data.jsx`;Modify HTML(`?v`→`d21`)。

- [ ] **Step 1: `seatOrder` 加可选 hold 参**。找到 `async function seatOrder(code, seat, tf)`(它 fetch `/seats/order?code=&seat=&tf=`)。改签名为 `async function seatOrder(code, seat, tf, hold)`;当 `hold && hold.entry != null` 时,URL 追加 `&hold_entry=<entry>&hold_since=<since>&hold_days=<days>`(用 `encodeURIComponent`)。无 hold 时 URL 不变(回归)。
```jsx
  let url = API + '/seats/order?code=' + encodeURIComponent(code) + '&seat=' + encodeURIComponent(seat) + '&tf=' + encodeURIComponent(tf || 'day');
  if (hold && hold.entry != null) url += '&hold_entry=' + encodeURIComponent(hold.entry) + '&hold_since=' + encodeURIComponent(hold.since || '') + '&hold_days=' + encodeURIComponent(hold.days != null ? hold.days : '');
```
(把现有构 URL 那行替换成上面两行;后续 fetch 用 `url`。)

- [ ] **Step 2: 加 `shadowClose` 纯函数**(放在 shadowMetrics 之后):
```jsx
// 研判平仓:按 id 把 open 持仓按现价平(reason 默认 '研判平')。返回 {shadow, changed}。
function shadowClose(shadow, posId, price, asofDate, reason) {
  if (!isFinite(+price)) return { shadow, changed: false };
  let changed = false;
  const positions = shadow.positions.map(p => {
    if (p.status === 'open' && p.id === posId) { changed = true; return Object.assign({}, p, { status: 'closed', exit: +price, exitDate: asofDate || null, exitReason: reason || '研判平' }); }
    return p;
  });
  return { shadow: changed ? { goLive: shadow.goLive, positions } : shadow, changed };
}
```
并在末尾 `Object.assign(window, {...})` 里加 `lzShadowClose: shadowClose,`。

- [ ] **Step 3: `?v`→d21**。`sed -i 's/?v=20260608d20/?v=20260608d21/g' "ui/seats/观澜 · 落子.html" && grep -c 20260608d21 "ui/seats/观澜 · 落子.html"` → `6`.

- [ ] **Step 4: 验证(javascript_tool,活着的端口)**。重载后:
```js
let sh={goLive:'2026-06-08',positions:[{id:'p1',seat:'momentum',side:'买入',entry:100,date:'2026-06-08',stop:95,take:110,status:'open',exit:null}]};
let r=window.lzShadowClose(sh,'p1',105,'2026-06-08','研判平');
JSON.stringify({changed:r.changed, exit:r.shadow.positions[0].exit, reason:r.shadow.positions[0].exitReason, status:r.shadow.positions[0].status});
```
Expected: `changed:true, exit:105, reason:'研判平', status:'closed'`。

---

## Task 3: app + 面板 —— 传持仓、持仓感知研判、研判平仓、显持仓(luozi-app.jsx + luozi-panels.jsx)

**Files:** Modify `ui/seats/luozi-app.jsx`、`ui/seats/luozi-panels.jsx`;Modify HTML(`?v`→`d22`)。

- [ ] **Step 1: app 传 `positions` + `onClosePosition` 给 OrderWatchPanel**。在 `<OrderWatchPanel ... />` 挂载追加(其余 prop 不动):
```jsx
  positions={mode === 'live' ? shadow.positions : []}
  onClosePosition={(posId, price) => { if (!window.lzShadowClose) return; setShadow(sh => { const r = window.lzShadowClose(sh, posId, price, (quote && quote.asofDate) || null, '研判平'); if (r.changed && window.lzShadowSave) window.lzShadowSave(code, r.shadow); return r.changed ? r.shadow : sh; }); }}
```

- [ ] **Step 2: OrderWatchPanel 签名 + 计算本席位持仓**。`function OrderWatchPanel({ code, name, onTrigger, mode, fresh })` → 加 `, positions, onClosePosition`。在组件内(seat/otf/liveCtx 已定义后)加:
```jsx
  const myHold = (positions || []).find(p => p.status === 'open' && p.seat === seat) || null;
  const holdPnl = (myHold && liveCtx && liveCtx.price != null && myHold.entry) ? ((+liveCtx.price / myHold.entry - 1) * 100) : null;
```

- [ ] **Step 3: runJudge 带 hold + 研判平仓**。把 `runJudge(reason)` 内 `window.lzSeatOrder(code, seat, otf).then(o => {...})` 改为带 hold,并在结果为卖出且持仓时回调平仓:
```jsx
    const hold = myHold ? { entry: myHold.entry, since: myHold.date, days: null } : null;
    window.lzSeatOrder(code, seat, otf, hold).then(o => {
      setOrder(o); setGen(false);
      const hhmm = new Date().toTimeString().slice(0, 5);
      const dir = o && o.order && o.order.side;
      setLoopLog(L => [{ at: hhmm, reason, dir: dir || '—' }, ...L].slice(0, 8));
      // 持仓感知:研判判卖出 → 按现价平掉该影子持仓(系统只发"该平"信号,用户手机自己操作)
      if (myHold && dir && /卖/.test(dir) && onClosePosition) {
        const px = (liveCtx && liveCtx.price != null) ? +liveCtx.price : (o && o.ctx && o.ctx.price != null ? +o.ctx.price : null);
        if (px != null) onClosePosition(myHold.id, px);
      }
    });
```
(runJudge 内其余如 `lastJudgeRef.current=Date.now()`、`setGen(true)`、复位 不变。)

- [ ] **Step 4: 面板显持仓条**。在条件单卡头部(席位/otf 选择行之后、`{!o && !gen && ...}` 提示之前)加(仅 `myHold` 存在时):
```jsx
      {myHold && (
        <div className="mono" style={{ fontSize: 9.5, color: 'var(--ink-2)', padding: '0 14px 8px', display: 'flex', gap: 10 }}>
          <span style={{ color: 'var(--yin)' }}>持仓中</span>
          <span>进场 <b>{myHold.entry}</b></span>
          {holdPnl != null && <span>浮动 <b style={{ color: holdPnl >= 0 ? 'var(--zhu)' : 'var(--dai)' }}>{(holdPnl >= 0 ? '+' : '') + holdPnl.toFixed(2) + '%'}</b></span>}
          <span style={{ color: 'var(--ink-3)' }}>研判将判 继续持/平</span>
        </div>
      )}
```

- [ ] **Step 5: `?v`→d22**。`sed -i 's/?v=20260608d21/?v=20260608d22/g' "ui/seats/观澜 · 落子.html" && grep -c 20260608d22 "ui/seats/观澜 · 落子.html"` → `6`.

- [ ] **Step 6: 验证(活着端口)**。① 重载、切实盘、0 console error。② 用 JS 往当前 code 的 localStorage 注入一笔 open 持仓再重载,实盘下条件单面板应显「持仓中·进场X·浮动Y%」;点「立单」(研判)→ 若 LLM 判卖出则该持仓在 MetricsStrip 变"已平+1"(研判平)、研判流水记一行(LLM 判继续持则持仓不变,亦正常)。③ 复盘无影子、面板无持仓条。

---

## Task 4: e2e + README
- [ ] **Step 1**: 端到端(活着端口):有持仓→研判→(LLM 卖出则)研判平仓使 MetricsStrip 已平/胜率更新;无持仓→研判仍是开仓逻辑(回归);复盘不动;0 error;截图/JS 留证。后端 curl 持仓感知 held:true/pnlPct 复核。
- [ ] **Step 2**: README 加一条:第2期·补 持仓感知研判+研判平仓 —— /seats/order 加 hold_entry/since/days、持仓换"管理"提示 side 可卖出、held/pnlPct;前端 seatOrder 带 hold、shadowClose、面板持仓条、研判卖出按现价平;复盘不动;注明 maxHold/跨票/B2 仍归后续。`?v=20260608d22`。

---

## Self-Review
- 覆盖:持仓感知研判(后端 hold 分支 + 前端传 hold)/ 研判平仓(shadowClose + 卖出回调现价平)/ 持仓显示 / 复盘不动 —— 均有任务。
- 命名一致:`hold_entry/hold_since/hold_days`(后端)↔ `hold={entry,since,days}`(前端);`shadowClose`/`lzShadowClose`;`positions`/`onClosePosition`/`myHold`/`holdPnl`。
- 红线:平仓=信号(影子按现价记),系统不代下单;未持仓行为完全不变(回归);复盘分支不动;无数据诚实(pnl null 时不显)。
- 范围外(注明):maxHold(需第3期席位时钟)、跨票聚合、B2 回填真实成交。
