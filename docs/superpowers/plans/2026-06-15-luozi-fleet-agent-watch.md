# 舰队多股 agent 盯盘 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 校场给票绑 agent 即设为「盯盘」;开主开关后,盯盘票各拉实时报价、盘中各按其 agent 的 clock 真调 `/seats/decide`,舰队右栏列出它们的实时 agent 研判;其余票只作「自选」可点。

**Architecture:** 纯前端(无构建 React 18 UMD)。盯盘集由校场绑定派生(无独立 toggle);多股研判循环在 app 层(页面驱动,逐股真报价判盘中,per-code clock 节流,标准 latest-ref 模式防陈旧闭包);舰队右栏重排为盯盘行(真研判)/ 自选行(只名字)。验证走浏览器真机(bump `?v`)。

**Tech Stack:** React 18 UMD + 浏览器内 Babel,localStorage,后端 FastAPI(本期只读不改)。

---

## 背景与契约(实施者必读)

- 设计依据 spec:`docs/superpowers/specs/2026-06-15-luozi-fleet-agent-watch-design.md`。
- **本计划取代上一轮(Task 4)的 monitored toggle**:`monitored` localStorage 映射 + `setMonitored` + 舰队 盯/自选 点击切换 —— 全部退役,改为「校场绑定派生」+ 只读徽章。
- **红线**:盯盘行只显真 `realDecs`(真 `/seats/decide` 落的),带「真·LLM」徽章;自选行不显任何信号;LLM 失败不写 realDecs / 不落账。
- **工程**:改 jsx 必 bump `?v`(用 Edit 非 sed,在 `ui/seats/观澜 · 落子.html`)。纯前端 → 无需重启 9999、无 pytest。
- **当前 `?v`**(以实际 html 为准,实施前先 Read):`luozi-data.jsx?v=20260615b`、`luozi-fleet.jsx?v=20260615d`、`luozi-app.jsx?v=20260615g`。每任务把所改文件 `?v` 升到标注值(或下一个可用字母)。
- **验证环境**:9999 看门狗常驻;preview 浏览器导航 `http://localhost:9999/ui/seats/观澜 · 落子.html`,`?v` bump 后 reload,**视口先 `preview_resize` 1440×900**(无头默认 0×0,所有 100vh 塌)。

## File Structure

- `ui/seats/luozi-data.jsx` — 盯盘集判定改绑定派生(`monitoredCodes`/`monitorAgentFor` + `poolIsMonitored` 重写;退役 `setMonitored`/localStorage)。
- `ui/seats/luozi-app.jsx` — `fleetWatch`/`monQuotes` state + 实时报价轮询 effect + `recordLiveDecide` 泛化 + onLiveDecide 改调它 + 盯盘循环 effect(latest-ref)+ 舰队分支接线。
- `ui/seats/luozi-fleet.jsx` — FleetSignalList 重排(主开关 + 盯盘行真研判 + 自选行只名字 + 删 scanSeat/toggle)+ FleetCard chip 只读 + 移除 onToggleMon。
- `ui/seats/README.md` — 收尾追加。

---

## Task 1: 数据层 — 盯盘集改校场绑定派生(luozi-data.jsx)

**Files:**
- Modify: `ui/seats/luozi-data.jsx:600-612`(替换 monitored 函数块)、`:1612`(导出)
- Modify: `ui/seats/观澜 · 落子.html`(bump data.jsx `?v` → `20260615h`)

- [ ] **Step 1: 替换 monitored 函数块为绑定派生**

把 luozi-data.jsx 第 600-612 行(从 `// ───────── 自选 vs 盯盘(monitored 标志...` 注释到 `setMonitored` 函数闭合 `}`)整块替换为:

```jsx
// ───────── 盯盘集判定(校场绑定驱动:有 agent 显式绑了该票 = 盯盘)─────────
// 全局默认(bind=[])不算盯盘;盯盘 = ∃ 策略 bind 非空且含该 code。
// owning agent = 第一个显式绑它的策略(单 agent 口径)。退役旧的 localStorage toggle,校场绑定为唯一真相。
function _monCode(code) { return String(code || '').replace(/^(SH|SZ|BJ)/i, ''); }
function monitoredCodes() {
  const out = {};
  strategyList().forEach(s => { if (Array.isArray(s.bind) && s.bind.length) s.bind.forEach(c => { out[_monCode(c)] = true; }); });
  return Object.keys(out);
}
function monitorAgentFor(code) {
  const c = _monCode(code);
  return strategyList().find(s => Array.isArray(s.bind) && s.bind.length && s.bind.map(_monCode).indexOf(c) >= 0) || null;
}
function poolIsMonitored(code) { return monitoredCodes().indexOf(_monCode(code)) >= 0; }
```

(`strategyList` 已在本文件定义。`setMonitored`/`MON_LS_KEY`/`_monLoad`/`_monSave` 一并删除,`_monCode` 保留复用。)

- [ ] **Step 2: 改导出**

把 luozi-data.jsx:1612 这行:
```jsx
  lzPoolIsMonitored: poolIsMonitored, lzSetMonitored: setMonitored,   // 自选 vs 盯盘
```
改成:
```jsx
  lzPoolIsMonitored: poolIsMonitored, lzMonitoredCodes: monitoredCodes, lzMonitorAgentFor: monitorAgentFor,   // 盯盘集(校场绑定派生)
```

- [ ] **Step 3: bump ?v**

html:`luozi-data.jsx?v=20260615b` → `20260615h`。

- [ ] **Step 4: 浏览器验证(preview_eval)**

resize 1440×900 + reload。`preview_eval`:

```js
(function(){
  var fns = { codes: typeof window.lzMonitoredCodes, agent: typeof window.lzMonitorAgentFor, isMon: typeof window.lzPoolIsMonitored, setMon: typeof window.lzSetMonitored };
  var before = window.lzMonitoredCodes();          // 默认无显式绑定 → [](默认策略 bind=[] 不算)
  return { fns: fns, monitoredNow: before };
})()
```

期望:`codes/agent/isMon` 均 `'function'`、`setMon` 为 `'undefined'`(已退役)、`monitoredNow` 为 `[]`(当前没有 agent 显式绑票;默认「动量·默认」bind=[] 不算)。

---

## Task 2: app.jsx — 盯盘循环 + 实时报价 + recordLiveDecide

**Files:**
- Modify: `ui/seats/luozi-app.jsx`(state ~:59 附近;onLiveDecide :305-324;新增 effect/ref)
- Modify: `ui/seats/观澜 · 落子.html`(bump app.jsx `?v` → `20260615h`)

- [ ] **Step 1: 加 state + 同步 ref**

在 luozi-app.jsx `realDecs` state 行(`const [realDecs, setRealDecs] = useState({});` 约 :59)之后插入:

```jsx
  const [fleetWatch, setFleetWatch] = useState(false);   // 舰队盯盘主开关(默认关,不持久;防意外后台烧 LLM)
  const [monQuotes, setMonQuotes] = useState({});         // 盯盘票实时报价 {code:quote}(复用 /seats/quote)
  const monQuotesRef = useRef({});                        // latest-ref:盯盘循环里读最新报价,避免 60s interval 陈旧闭包
  const realDecsRef = useRef({});                         // latest-ref:节流去重读最新 realDecs(与单股循环共享 ts 去重)
  const recordRef = useRef(null);                         // latest-ref:盯盘循环里调最新 recordLiveDecide
  useEffect(() => { monQuotesRef.current = monQuotes; }, [monQuotes]);
  useEffect(() => { realDecsRef.current = realDecs; }, [realDecs]);
```

- [ ] **Step 2: recordLiveDecide 泛化 + onLiveDecide 改调它**

把 luozi-app.jsx 现有 `onLiveDecide`(:305-324 整个 `const onLiveDecide = (rd) => { … };`)替换为:

```jsx
  // ⑤++ 真·研判落 realDecs(+已开账落台账 decision)。泛化:焦点单股(onLiveDecide)与多股盯盘循环共用。
  //   ts=Date.now() 供节流去重;idx 仅焦点票给末根(供图标记),非焦点票 null(只进舰队不上图)。
  const recordLiveDecide = (codeArg, nameArg, rd) => {
    if (!rd || !rd.direction) return;
    const side = /买/.test(rd.direction) ? 'buy' : (/卖/.test(rd.direction) ? 'sell' : 'watch');
    const key = 'true_' + (rd.seat || '') + '@live';
    const dec = { key: key, seat: rd.seat, idx: (codeArg === code ? n - 1 : null), date: rd.asof, side: side,
      direction: rd.direction, conf: (rd.conf != null ? rd.conf : null), rationale: rd.rationale || '', reasoning: rd.reasoning || '',
      asof: rd.asof, model_name: rd.model_name || '', ts: Date.now() };
    setRealDecs(prev => {
      const arr = (prev[codeArg] || []).filter(x => x.key !== key).concat([dec]);
      return Object.assign({}, prev, { [codeArg]: arr });
    });
    if (ledger && ledger.opened && window.lzLedgerPost) {
      window.lzLedgerPost({ kind: 'decision', date: String(rd.asof || '').slice(0, 10) || new Date().toISOString().slice(0, 10),
        code: codeArg, name: nameArg, direction: rd.direction, confidence: rd.conf == null ? null : +rd.conf,
        decision_id: rd.id || null, source: 'timer' }).then(() => refreshLedger());
    }
  };
  recordRef.current = recordLiveDecide;   // latest-ref:每次渲染刷新,盯盘循环调最新版
  const onLiveDecide = (rd) => recordLiveDecide(code, symbol.meta.name, rd);
```

- [ ] **Step 3: 盯盘票实时报价轮询 effect**

在现有"实盘盘口轮询" effect(`}, [mode, code]);` 约 :160)之后插入:

```jsx
  // 盯盘票实时报价轮询(复用 /seats/quote;挂在 fleetWatch 上、与 mode 无关)→ 盯盘循环用它判逐股盘中。
  useEffect(() => {
    if (!fleetWatch || !window.lzFetchQuote || !window.lzMonitoredCodes) { setMonQuotes({}); return; }
    let alive = true;
    const pull = () => {
      (window.lzMonitoredCodes() || []).forEach(c => {
        window.lzFetchQuote(c).then(q => { if (alive && q) setMonQuotes(prev => Object.assign({}, prev, { [c]: q })); });
      });
    };
    pull();
    const iv = setInterval(pull, 7000);
    return () => { alive = false; clearInterval(iv); };
  }, [fleetWatch]);
```

- [ ] **Step 4: 多股盯盘循环 effect(latest-ref,稳定 60s)**

紧接 Step 3 的 effect 之后插入:

```jsx
  // 多股盯盘循环(页面驱动):fleetWatch 开 → 每 60s 遍历盯盘集,逐股「真报价盘中 + per-code clock 节流」真调 /seats/decide。
  //   deps 只含 fleetWatch(稳定 60s);变量经 ref 取最新(防 interval 陈旧闭包)。失败该票跳过、不写 realDecs。
  useEffect(() => {
    if (!fleetWatch || !window.lzSeatDecide || !window.lzMonitoredCodes) return;
    let alive = true;
    const tick = async () => {
      const codes = window.lzMonitoredCodes() || [];
      for (let i = 0; i < codes.length; i++) {
        if (!alive) break;
        const c = codes[i];
        const q = monQuotesRef.current[c];
        if (!q || !q.fresh) continue;                                   // 逐股盘中门控:该股实时报价为今日
        const agent = window.lzMonitorAgentFor ? window.lzMonitorAgentFor(c) : null;
        if (!agent) continue;
        const rds = realDecsRef.current[c] || [];
        const lastTs = (rds.length && rds[rds.length - 1].ts) || 0;     // 复盘真跑写的无 ts → 视为很久以前
        const gap = Date.now() - lastTs;
        if (gap < 600000) continue;                                     // 10min 硬地板(与单股循环共享去重)
        const fq = (agent.clock && agent.clock.decisionFreq) || 'hourly';
        const due = fq === 'daily'
          ? (lastTs === 0 || new Date(lastTs).toDateString() !== new Date().toDateString())
          : (gap >= 3600000);
        if (!due) continue;
        const rcp = window.lzRecipeForStrategy ? window.lzRecipeForStrategy(agent.id) : { cards: [], research: [], factors: [] };
        const meta = (window.LZ_SYMBOLS[c] && window.LZ_SYMBOLS[c].meta) || { name: c };
        try {
          const res = await window.lzSeatDecide({
            code: c, name: meta.name, date: new Date().toISOString().slice(0, 10),
            seat_cn: agent.name, creed: agent.creed || '', mode: 'fast',
            strategy_id: agent.id, strategy_name: agent.name,
            card: rcp.cards[0] ? { name: rcp.cards[0].name, insight: rcp.cards[0].insight, verdict: rcp.cards[0].verdict, conf: rcp.cards[0].conf, ic: rcp.cards[0].ic } : null,
            cards: rcp.cards, recipe_factors: rcp.factors, industry: meta.industry || '', regime: null,
            pa: !!agent.pa, pa_method: agent.pa ? (agent.paMethod || window.LZ_PA_METHOD_DEFAULT || '') : '',
            w: agent.w || 0,
          });
          if (alive && res && res.ok && res.direction && recordRef.current) {
            recordRef.current(c, meta.name, { seat: agent.id, direction: res.direction, conf: res.confidence,
              rationale: res.rationale, reasoning: res.reasoning, asof: res.asof, model_name: res.model_name, id: res.id });
          }
        } catch (e) {}
      }
    };
    tick();
    const iv = setInterval(tick, 60000);
    return () => { alive = false; clearInterval(iv); };
  }, [fleetWatch]);
```

- [ ] **Step 5: bump ?v**

html:`luozi-app.jsx?v=20260615g` → `20260615h`。

- [ ] **Step 6: 浏览器验证**

resize 1440×900 + reload。
- 控制台无解析错(`preview_console_logs level=error` 空)。
- `preview_eval` 验 page mounted:`(function(){ return { mounted: (document.getElementById('root')||{}).childElementCount }; })()` → `mounted:1`。
- 盯盘循环真触发须盘中 + 有显式绑定的票,留待 Task 5 整体验。本任务只确认无语法错、globals 在线。

---

## Task 3: fleet.jsx — FleetSignalList 重排 + 主开关 + chip 只读

**Files:**
- Modify: `ui/seats/luozi-fleet.jsx`(FleetSignalList :137-182、FleetCard chip :96-103、FleetGrid :112/128、export :184)
- Modify: `ui/seats/观澜 · 落子.html`(bump fleet.jsx `?v` → `20260615h`)

- [ ] **Step 1: FleetSignalList 整体重排(主开关 + 盯盘行真研判 + 自选行只名字)**

把 luozi-fleet.jsx 的 `function FleetSignalList({ realDecs, onPick, onToggleMon, activeCode })`(:137)整个函数体替换为:

```jsx
function FleetSignalList({ realDecs, monQuotes, onPick, activeCode, watchOn, onToggleWatch }) {
  const codes = window.LZ_SYMBOL_META.map(m => m.code);
  const today = new Date().toISOString().slice(0, 10);
  const nMon = (window.lzMonitoredCodes ? window.lzMonitoredCodes() : []).length;
  return (
    <div style={{ width: 344, flexShrink: 0, borderLeft: '1px solid var(--line)', display: 'flex', flexDirection: 'column', minHeight: 0, background: 'var(--paper)' }}>
      <div style={{ padding: '10px 14px', borderBottom: '1px solid var(--line)', flexShrink: 0 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <span className="serif" style={{ fontSize: 13, fontWeight: 600 }}>盯盘 · 自选</span>
          <span style={{ flex: 1 }} />
          <span onClick={() => onToggleWatch && onToggleWatch()}
            title="盯盘:盘中(有实时报价)按各 agent 的判别频率自动真研判盯盘票。页面驱动——关页面即停,无后端定时器。"
            className="mono" style={{ fontSize: 9, padding: '3px 9px', borderRadius: 10, cursor: 'pointer', whiteSpace: 'nowrap',
              border: '1px solid ' + (watchOn ? 'var(--yin)' : 'var(--line)'), color: watchOn ? 'var(--paper)' : 'var(--ink-3)', background: watchOn ? 'var(--yin)' : 'transparent' }}>
            {watchOn ? '● 盯盘中 · ' + nMon + ' 支' : '○ 开始盯盘'}
          </span>
        </div>
        <div className="mono" style={{ fontSize: 8, color: 'var(--ink-3)', marginTop: 4 }}>
          {watchOn ? '页面开着 + 盘中自动研判 · 关页面即停' : '在校场给票绑 agent = 盯盘;点「开始盯盘」启动'}
        </div>
      </div>
      <div style={{ flex: 1, overflowY: 'auto', minHeight: 0 }}>
        {codes.map(code => {
          const S = (window.lzRealSymbolOf && window.lzRealSymbolOf(code)) || window.LZ_SYMBOLS[code];
          const name = (S && S.meta && S.meta.name) || code;
          const mon = window.lzPoolIsMonitored && window.lzPoolIsMonitored(code);
          const agent = mon && window.lzMonitorAgentFor ? window.lzMonitorAgentFor(code) : null;
          const rds = (realDecs && realDecs[code]) || [];
          const rd = rds.length ? rds[rds.length - 1] : null;
          const dirCol = rd ? (rd.side === 'buy' ? 'var(--zhu)' : rd.side === 'sell' ? 'var(--dai)' : 'var(--ink-3)') : 'var(--ink-3)';
          const q = monQuotes && monQuotes[code];
          return (
            <div key={code} onClick={() => onPick(code)} className="hover-row" style={{ padding: '9px 14px', borderBottom: '1px solid var(--line-soft)', cursor: 'pointer', borderLeft: '2px solid ' + (code === activeCode ? 'var(--yin)' : 'transparent') }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 7 }}>
                <span className="serif" style={{ fontSize: 12, fontWeight: 600 }}>{name}</span>
                <span className="mono" style={{ fontSize: 9, color: 'var(--ink-3)' }}>{code}</span>
                <span style={{ flex: 1 }} />
                {mon
                  ? <span className="mono" title={agent ? ('盯盘 agent:' + agent.name) : '盯盘'} style={{ fontSize: 8, padding: '1px 6px', borderRadius: 8, border: '1px solid var(--yin)', color: 'var(--paper)', background: 'var(--yin)' }}>● 盯盘</span>
                  : <span className="mono" style={{ fontSize: 8, padding: '1px 6px', borderRadius: 8, border: '1px solid var(--line)', color: 'var(--ink-3)' }}>○ 自选</span>}
              </div>
              {mon && (
                <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginTop: 4 }}>
                  {rd
                    ? <React.Fragment>
                        <span className="serif" style={{ fontSize: 11.5, fontWeight: 600, color: dirCol }}>{rd.direction || (rd.side === 'buy' ? '买入' : rd.side === 'sell' ? '卖出' : '观望')}</span>
                        {rd.conf != null && <span className="mono" style={{ fontSize: 8.5, color: 'var(--ink-3)' }}>置信 {rd.conf}</span>}
                        <span className="mono" style={{ fontSize: 8, color: 'var(--ink-3)' }}>{String(rd.asof || rd.date || '').slice(5, 16)}</span>
                        <span className="mono" style={{ fontSize: 7.5, padding: '0 4px', borderRadius: 3, border: '1px solid var(--yin)', color: 'var(--yin)' }}>真·LLM</span>
                      </React.Fragment>
                    : <span className="mono" style={{ fontSize: 9, color: 'var(--ink-3)' }}>盯盘中 · 待研判</span>}
                  <span style={{ flex: 1 }} />
                  {agent && <span className="mono" style={{ fontSize: 8, color: 'var(--ink-3)', maxWidth: 90, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{agent.name}</span>}
                  {q && <span title={q.fresh ? '盘中(实时报价)' : '休市/无实时'} style={{ width: 6, height: 6, borderRadius: '50%', background: q.fresh ? 'var(--zhu)' : 'var(--line)', flexShrink: 0 }} />}
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
```

(自选行只显名字/代码 + 「○ 自选」徽章,无第二行、无 scanSeat。盯盘行显真研判 + agent 名 + 盘中点。)

- [ ] **Step 2: FleetCard chip 改只读 + 去 onToggleMon**

luozi-fleet.jsx `FleetCard` 签名(:41)把 `onToggleMon` 去掉:`function FleetCard({ code, active, onPick, isActive }) {`。
把 :96-103 的盯/自选 chip(`<span onClick={(e) => { e.stopPropagation(); onToggleMon && onToggleMon(code); }} …>` 整段)替换为只读徽章:

```jsx
        {(window.lzPoolIsMonitored && window.lzPoolIsMonitored(code))
          ? <span className="mono" title="盯盘(校场已绑 agent)" style={{ fontSize: 8.5, padding: '2px 7px', borderRadius: 8, border: '1px solid var(--yin)', color: 'var(--paper)', background: 'var(--yin)' }}>● 盯盘</span>
          : <span className="mono" title="自选(校场未绑 agent;去校场绑 agent 即盯盘)" style={{ fontSize: 8.5, padding: '2px 7px', borderRadius: 8, border: '1px solid var(--line)', color: 'var(--ink-3)' }}>○ 自选</span>}
```

- [ ] **Step 3: FleetGrid 去 onToggleMon**

`function FleetGrid({ active, onPick, activeCode, onToggleMon })`(:112)→ 去掉 `onToggleMon`:`function FleetGrid({ active, onPick, activeCode }) {`。
卡渲染(:128)去掉 `onToggleMon={onToggleMon}`:`{codes.map(c => <FleetCard key={c} code={c} active={active} onPick={onPick} isActive={c === activeCode} />)}`。

- [ ] **Step 4: bump ?v**

html:`luozi-fleet.jsx?v=20260615d` → `20260615h`。

- [ ] **Step 5: 浏览器验证**

resize 1440×900 + reload → 切「舰队」。
- 控制台无解析错。
- `preview_eval`:`(function(){ var t=document.body.innerText; return { hasHeader: t.indexOf('盯盘 · 自选')>=0, hasStart: t.indexOf('开始盯盘')>=0, hasMon: /[●○]\s*(盯盘|自选)/.test(t), noNonLLM: t.indexOf('非LLM')<0 }; })()` → 期望 `hasHeader/hasStart/hasMon` 真、`noNonLLM` 真(舰队不再有 scanSeat「非LLM」)。
- 当前无显式绑定 → 所有行应是「○ 自选」只名字。

---

## Task 4: app.jsx 舰队分支接线

**Files:**
- Modify: `ui/seats/luozi-app.jsx:709-720`(舰队分支)
- Modify: `ui/seats/观澜 · 落子.html`(bump app.jsx `?v` → `20260615i`)

- [ ] **Step 1: 舰队分支传新 props、去 onToggleMon**

把 luozi-app.jsx:709-720 舰队分支替换为:

```jsx
      ) : (
        <div style={{ flex: 1, minHeight: 0, display: 'flex' }}>
          <div style={{ flex: 1, minHeight: 0, display: 'flex', flexDirection: 'column' }}>
            <FleetGrid active={active} activeCode={code} onPick={(c) => { setCode(c); setView('single'); }} />
          </div>
          <FleetSignalList realDecs={realDecs} monQuotes={monQuotes} activeCode={code}
            watchOn={fleetWatch} onToggleWatch={() => setFleetWatch(v => !v)}
            onPick={(c) => { setCode(c); setView('single'); }} />
        </div>
      )}
```

- [ ] **Step 2: bump ?v**

html:`luozi-app.jsx?v=20260615h` → `20260615i`。

- [ ] **Step 3: 浏览器验证**

resize 1440×900 + reload → 舰队。
- 控制台无解析错。
- 点头部「○ 开始盯盘」→ 变「● 盯盘中 · N 支」;再点关。`preview_eval` 验文案翻转(两次 eval,React 异步):先 eval 找含「开始盯盘」的 span `.click()`,再单独 eval 读 `document.body.innerText.indexOf('盯盘中')>=0`。

---

## Task 5: 收口(端到端 + 文档)

**Files:**
- Modify: `ui/seats/README.md`

- [ ] **Step 1: 端到端真机(校场绑定驱动盯盘)**

1. resize 1440×900,reload。
2. 进**校场**(顶栏「校场」)→ 编辑/新建一个 agent → 用「绑票」绑 1 支(如宁德 300750)→ 钤印保存。
3. 回**舰队** → 该票应变「● 盯盘」行(显 agent 名),未研判显「盯盘中 · 待研判」;其余票「○ 自选」只名字。`preview_eval` 验:`window.lzMonitoredCodes()` 含 `300750`、`window.lzMonitorAgentFor('300750').name` = 你绑的 agent 名。
4. FleetGrid 网格卡该票也显「● 盯盘」只读徽章。
5. 主开关 ○→● 翻转正常;控制台全程 0 报错。
6. (盘中真触发端到端:交易时段开主开关,等节流到点,验 `realDecs['300750']` 写入 + 舰队显真方向 + 已开账落 ledger decision。非盘中无法触发,属预期——记录"逻辑已接、待盘中实跑";可选注 stub 截 lzSeatDecide payload 验携 agent 信条/配方/pa/w。)
7. 验后把校场测试绑定解绑(回到无显式绑定)、主开关关、清测试态。

- [ ] **Step 2: README 追加**

`ui/seats/README.md` 在右栏重构那节后追加一段:本期「舰队多股 agent 盯盘」(校场绑定=盯盘 / `monitoredCodes`/`monitorAgentFor` 派生 / 退役 Task 4 toggle / `fleetWatch` 主开关页面驱动 / 逐股真报价盘中门控 + clock 节流 + recordLiveDecide / FleetSignalList 盯盘行真研判·自选行只名字;`?v=20260615h~i`),指向 spec/plan。

- [ ] **Step 3: 无后端改动确认**

`rg` 确认仅前端 jsx + html + 文档变更 → 无需重启 9999、pytest 不受影响。

---

## Self-Review(规划自查,已过)

- **Spec 覆盖**:① 盯盘集判定=Task 1;② 主开关/实时报价/recordLiveDecide/循环=Task 2;③ 舰队右栏重排=Task 3;④ FleetCard chip 只读 + 落账闸门(onTrigger 的 `lzPoolIsMonitored` 已在 Task 1 重写为绑定派生,自动生效,无需改 onTrigger)=Task 3 + Task 1;接线=Task 4;⑤=Task 5。全覆盖。
- **spec↔plan 偏差(已说明)**:spec ② 写的"monQuotes + 独立轮询",plan 保留 monQuotes(供盘中点显示)但盯盘循环用 **latest-ref**(monQuotesRef/realDecsRef/recordRef)读最新值,deps 只含 `fleetWatch` 使 60s interval 稳定——这是对"复用实时报价"同概念的正确 React 落地(防 interval 陈旧闭包)。
- **类型/命名一致**:`lzMonitoredCodes`/`lzMonitorAgentFor`/`lzPoolIsMonitored`(data)↔ 消费方一致;`FleetSignalList({realDecs, monQuotes, onPick, activeCode, watchOn, onToggleWatch})` ↔ app 传参一致;`recordLiveDecide(code,name,rd)` ↔ onLiveDecide / 循环调用一致;realDecs 元素新增 `ts` 字段(节流用)。
- **占位符**:无 TBD;改动均给完整代码或精确 old→new。
- **红线**:盯盘行只 realDecs 真研判 +「真·LLM」;自选行无信号;LLM 失败不写不落账;退役 toggle 后 onTrigger 闸门语义不变(自选=无显式绑定=不自动落账)。
