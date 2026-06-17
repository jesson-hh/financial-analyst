# 第 1 期 · 研判循环 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让「落子」实盘按节拍自动"会动"——在现有条件单/LLM 上接齐研判三触发器(手动 + 条件单成交后 + 盘中每小时封顶定时),并用一条「研判流水」让用户看见循环在跑、每次为何而跑。

**Architecture:** 纯前端编排,全部落在 `OrderWatchPanel`(它已拥有 order 状态、`genOrder`、实时盯盘 8s 轮询)。新增:`loopOn` 开关 + `loopLog` 流水 + `lastJudgeRef` 节流;把 `genOrder` 包成 `runJudge(reason)`(记流水);成交(`liveFired`)后延时自动再判;`fresh` 时每小时封顶定时再判。复用 `lzSeatOrder` / `/seats/order`,不动后端、不动引擎。

**Tech Stack:** no-build React 18 UMD + 浏览器内 Babel(`useState/useEffect/useRef` 由 `luozi-chart.jsx` 全局 `const {…}=React` 暴露);`new Date()`/`Date.now()` 浏览器内可用。

---

## 本仓特例(务必先读)

- **无前端测试框架**:不写 pytest/jest。每个任务的"验证"= 用 Chrome MCP 重载页面 → 读 console 0 error → 观察行为(必要时模拟触发)。这是本模块既有纪律(见 `ui/seats/README.md` 全部条目均"浏览器实测")。
- **非 git 仓**:无 `git commit`。每个任务的"提交边界"= 改完 jsx 后**把 HTML 里 6 个 `?v=` 同步 +1** 再重载验证。当前版本 `?v=20260608d12`,本期依次用 `d13 → d14 → d15`。
- **服务**:9998(用户标签页)+ 9999 均 `python guanlan_v2/server.py`。本期**只改 jsx,无需重启后端**;若服务没起按 README 重启。Chrome 标签页 id 见会话(如失效用 `tabs_context_mcp` 重取)。
- **市场开闭**:定时/成交触发只在盘中(`fresh=true`)真跑。若验证时休市,按各任务的"休市替代验证"做(查调度/节流逻辑、模拟 `fresh`),不要伪造成交。
- **红线**:系统只出信号、不代下单;无数据诚实空态,绝不编造;不动 `symbol.bars`/scan/合议 的计算帧。

---

## File Structure

- **Modify `ui/seats/luozi-panels.jsx`**(唯一逻辑改动文件)
  - `OrderWatchPanel`:加循环状态、`runJudge`、成交后自动研判、定时研判、循环开关、研判流水 UI。
  - 模块级加一个 `LZ_REASON_CN` 常量(研判触发原因中文名)。
  - 组件签名加一个 `fresh` prop。
- **Modify `ui/seats/luozi-app.jsx`**:`OrderWatchPanel` 挂载处传入 `fresh`(由 live `quote.fresh` 推导)。
- **Modify `ui/seats/观澜 · 落子.html`**:6 个 `?v=` 依任务推进。

> 范围内只有 1 个组件 + 1 处挂载 + 版本号;后端、引擎、其余 jsx 不动。多票/多席循环、位置感知研判(继续持/平)属第 2、3 期,不在本期。

---

## Task 1: 研判状态 + `runJudge` 包装 + 手动接线 + 研判流水 UI

把现有手动 `genOrder` 收进统一的 `runJudge(reason)`(记一条流水),并在面板底部显示「研判流水」。本任务不引入自动触发,先把"研判=一次有原因、有记录的事件"这层立起来。

**Files:**
- Modify: `ui/seats/luozi-panels.jsx`(`OrderWatchPanel`,约 28-180;模块级常量)
- Modify: `ui/seats/观澜 · 落子.html`(`?v` → `d13`)

- [ ] **Step 1: 加模块级常量 `LZ_REASON_CN`**

在 `function OrderWatchPanel` 定义**之前**(约第 27 行,紧邻该区起始处)加:

```jsx
// 研判触发原因 → 中文(研判循环流水用)
const LZ_REASON_CN = { manual: '手动研判', fill: '成交后研判', timer: '定时研判' };
```

- [ ] **Step 2: 加循环状态与节流 ref**

在 `OrderWatchPanel` 内,现有 `const [liveCtx, setLiveCtx] = useState(null);`(约第 36 行)**之后**加:

```jsx
  const [loopOn, setLoopOn] = useState(false);     // 研判循环开关(live:成交后 + 每小时封顶定时)
  const [loopLog, setLoopLog] = useState([]);      // 研判流水 [{at,reason,dir}](最新在前,留 8 条)
  const lastJudgeRef = useRef(0);                  // 上次研判 epoch ms(定时节流用)
```

- [ ] **Step 3: 把 `genOrder` 重构为 `runJudge(reason)`**

把现有(约第 39-43 行):

```jsx
  const genOrder = () => {
    if (!window.lzSeatOrder || gen) return;
    setGen(true); setTrig(null); setOrder(null); setLiveFired(null); setLiveCtx(null);
    window.lzSeatOrder(code, seat, otf).then(o => { setOrder(o); setGen(false); });
  };
```

整体替换为:

```jsx
  const runJudge = (reason) => {
    if (!window.lzSeatOrder || gen) return;
    lastJudgeRef.current = Date.now();
    setGen(true); setTrig(null); setOrder(null); setLiveFired(null); setLiveCtx(null);
    window.lzSeatOrder(code, seat, otf).then(o => {
      setOrder(o); setGen(false);
      const hhmm = new Date().toTimeString().slice(0, 5);   // 本地 HH:MM
      const dir = o && o.order && o.order.side;
      setLoopLog(L => [{ at: hhmm, reason, dir: dir || '—' }, ...L].slice(0, 8));
    });
  };
  const genOrder = () => runJudge('manual');     // 手动「立单」走同一通道,记一条手动研判
```

- [ ] **Step 4: 切 code 时清空研判流水**

把现有(约第 37 行)清理 effect:

```jsx
  useEffect(() => { setOrder(null); setTrig(null); setGen(false); setChecking(false); setLiveFired(null); setLiveCtx(null); }, [code]);
```

替换为(追加 `setLoopLog([])`,并复位节流):

```jsx
  useEffect(() => { setOrder(null); setTrig(null); setGen(false); setChecking(false); setLiveFired(null); setLiveCtx(null); setLoopLog([]); lastJudgeRef.current = 0; }, [code]);
```

- [ ] **Step 5: 在面板底部渲染「研判流水」**

在 `{o && ( … )}` 大块(以第 177 行的 `)}` 结束)**之后**、`OrderWatchPanel` 最外层 `</div>`(第 178 行)**之前**,插入:

```jsx
      {loopLog.length > 0 && (
        <div style={{ padding: '0 14px 11px' }}>
          <div className="mono" style={{ fontSize: 8, color: 'var(--ink-3)', letterSpacing: '0.24em', marginBottom: 4 }}>研 判 流 水</div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
            {loopLog.map((e, i) => (
              <div key={i} className="mono" style={{ display: 'flex', alignItems: 'baseline', gap: 8, fontSize: 9.5 }}>
                <span style={{ color: 'var(--ink-3)' }}>{e.at}</span>
                <span style={{ color: e.reason === 'manual' ? 'var(--ink-2)' : e.reason === 'fill' ? 'var(--yin)' : 'var(--jin)' }}>{LZ_REASON_CN[e.reason] || e.reason}</span>
                <span style={{ marginLeft: 'auto', color: 'var(--ink-2)' }}>{e.dir}</span>
              </div>
            ))}
          </div>
        </div>
      )}
```

- [ ] **Step 6: 同步版本号到 d13**

Run: `sed -i 's/?v=20260608d12/?v=20260608d13/g' "ui/seats/观澜 · 落子.html"`
Expected: 命令后 `grep -c 20260608d13 "ui/seats/观澜 · 落子.html"` 输出 `6`。

- [ ] **Step 7: 浏览器验证(Chrome MCP)**

1. `navigate` 到 `http://127.0.0.1:9998/ui/seats/观澜 · 落子.html`(URL 编码同会话历史),等 6s。
2. 进任意模式,点条件单面板「⚡ 立单」生成一张单,等几秒。
3. `read_console_messages`(onlyErrors)→ 期望 **0 error**。
4. `screenshot` → 期望:生成单后面板底部出现「研判流水」,有一行 `HH:MM · 手动研判 · 买入/卖出`。

Expected: 0 console error;研判流水出现且首行为「手动研判」+ 方向正确。

休市替代验证:同上(手动研判与盘市无关,任何时候可点)。

---

## Task 2: 成交后自动研判 + 循环开关

加「研判循环」开关(仅 live 显示);开启后,实时触发(成交 `liveFired`)后延时 1.5s 自动发起下一轮 `runJudge('fill')`——`runJudge` 内会 `setLiveFired(null)` 复位,使现有 8s 盯盘 effect 在新 order 上重新武装,形成"成交→再判→再盯"的链。

**Files:**
- Modify: `ui/seats/luozi-panels.jsx`(`OrderWatchPanel` 头部 + 新 effect)
- Modify: `ui/seats/观澜 · 落子.html`(`?v` → `d14`)

- [ ] **Step 1: 头部加「研判循环」开关(仅 live)**

现有头部「立单」span(约第 107-109 行):

```jsx
        <span onClick={genOrder} className="serif" style={{ marginLeft: 'auto', fontSize: 11, letterSpacing: '0.06em', color: gen ? 'var(--ink-3)' : 'var(--paper)', background: gen ? 'transparent' : 'var(--yin)', border: gen ? '1px solid var(--line)' : 'none', cursor: gen ? 'default' : 'pointer', borderRadius: 3, padding: '3px 11px' }}>
          {gen ? '拟单中…' : '⚡ 立 单'}
        </span>
```

替换为(在其前插入开关,并把「立单」的 `marginLeft:'auto'` 改成 `marginLeft: 8`,使开关靠右、立单紧随):

```jsx
        {mode === 'live' && (
          <span onClick={() => setLoopOn(v => !v)} className="mono" title="研判循环:开 = 成交后自动再判 + 盘中每小时封顶定时研判(手动「立单」始终可用)"
            style={{ marginLeft: 'auto', fontSize: 9, padding: '2px 8px', borderRadius: 10, cursor: 'pointer', border: '1px solid ' + (loopOn ? 'var(--yin)' : 'var(--line)'), color: loopOn ? 'var(--paper)' : 'var(--ink-3)', background: loopOn ? 'var(--yin)' : 'transparent' }}>
            {loopOn ? '● 循环中' : '○ 研判循环'}
          </span>
        )}
        <span onClick={genOrder} className="serif" style={{ marginLeft: mode === 'live' ? 8 : 'auto', fontSize: 11, letterSpacing: '0.06em', color: gen ? 'var(--ink-3)' : 'var(--paper)', background: gen ? 'transparent' : 'var(--yin)', border: gen ? '1px solid var(--line)' : 'none', cursor: gen ? 'default' : 'pointer', borderRadius: 3, padding: '3px 11px' }}>
          {gen ? '拟单中…' : '⚡ 立 单'}
        </span>
```

- [ ] **Step 2: 加「成交后自动研判」effect**

在现有 8s 盯盘 effect(以第 89 行 `}, [mode, order, liveFired, code]);` 结束)**之后**加:

```jsx
  // 成交后研判:开循环 + live + 实时触发(成交)后,延时 1.5s 自动发起下一轮研判;
  //   runJudge 内 setLiveFired(null) 复位 → 上面 8s 盯盘 effect 在新 order 上重新武装,形成 成交→再判→再盯 链。
  useEffect(() => {
    if (!loopOn || mode !== 'live' || !liveFired) return;
    const t = setTimeout(() => runJudge('fill'), 1500);
    return () => clearTimeout(t);
  }, [liveFired, loopOn, mode]);
```

- [ ] **Step 3: 同步版本号到 d14**

Run: `sed -i 's/?v=20260608d13/?v=20260608d14/g' "ui/seats/观澜 · 落子.html"`
Expected: `grep -c 20260608d14 "ui/seats/观澜 · 落子.html"` 输出 `6`。

- [ ] **Step 4: 浏览器验证**

1. reload;切到 **实盘**。
2. 看头部出现「○ 研判循环」开关(复盘模式不应出现)。点它 → 变「● 循环中」(朱砂底)。
3. `read_console_messages`(onlyErrors)→ 0 error。
4. `screenshot` 确认开关态正确、复盘模式无此开关。

Expected: 开关仅 live 出现、可切换、0 error。

盘中真验证(若开市):开循环 + 生成一张"易触发"的单(如 价≥略低于现价),等其实时触发 → 观察「研判流水」在「实时触发」后约 1.5s 自动多出一行「成交后研判」,且盯盘指示对新单重新「实时盯盘中…」。
休市替代验证:`liveFired` 无法真产生;改为仅验证开关 UI + 0 error(**不伪造成交**);待开市补盘中验证并记入 README。

---

## Task 3: 定时研判(盘中每小时封顶)+ `fresh` 接线

加每分钟检查一次的心跳:`loopOn && live && fresh && 距上次研判 ≥ 1 小时` → `runJudge('timer')`。`fresh` 由 app 的 live `quote.fresh` 经新 prop 传入(使心跳不依赖"已有订单",可冷启动第一张单)。

**Files:**
- Modify: `ui/seats/luozi-panels.jsx`(签名加 `fresh`;新 effect)
- Modify: `ui/seats/luozi-app.jsx`(挂载处传 `fresh`)
- Modify: `ui/seats/观澜 · 落子.html`(`?v` → `d15`)

- [ ] **Step 1: 组件签名加 `fresh` prop**

把(第 28 行):

```jsx
function OrderWatchPanel({ code, name, onTrigger, mode }) {
```

改为:

```jsx
function OrderWatchPanel({ code, name, onTrigger, mode, fresh }) {
```

- [ ] **Step 2: 加「定时研判」effect**

在 Task 2 的「成交后研判」effect 之后加:

```jsx
  // 定时研判:开循环 + live + 盘中(fresh),每分钟查一次;距上次研判 ≥ 1 小时(每小时封顶)即发起一次。
  //   deps 含 seat/otf/code 使 runJudge 取到当前选择;lastJudgeRef 为 ref 跨重建持续,节流不被重置。
  useEffect(() => {
    if (!loopOn || mode !== 'live' || !fresh) return;
    let alive = true;
    const tick = () => { if (alive && Date.now() - lastJudgeRef.current >= 3600000) runJudge('timer'); };
    const iv = setInterval(tick, 60000);
    return () => { alive = false; clearInterval(iv); };
  }, [loopOn, mode, fresh, seat, otf, code]);
```

- [ ] **Step 3: app 挂载处传 `fresh`**

在 `ui/seats/luozi-app.jsx` 第 293 行,把:

```jsx
              <OrderWatchPanel code={symbol.meta.code} name={symbol.meta.name} mode={mode === 'live' ? 'live' : 'backtest'} onTrigger={(t) => setOrderTriggers(ts => [...ts.filter(x => x.id !== t.id), t])} />
```

改为(加 `fresh`):

```jsx
              <OrderWatchPanel code={symbol.meta.code} name={symbol.meta.name} mode={mode === 'live' ? 'live' : 'backtest'} fresh={mode === 'live' && !!(quote && quote.fresh)} onTrigger={(t) => setOrderTriggers(ts => [...ts.filter(x => x.id !== t.id), t])} />
```

- [ ] **Step 4: 同步版本号到 d15**

Run: `sed -i 's/?v=20260608d14/?v=20260608d15/g' "ui/seats/观澜 · 落子.html"`
Expected: `grep -c 20260608d15 "ui/seats/观澜 · 落子.html"` 输出 `6`。

- [ ] **Step 5: 浏览器验证**

1. reload;切 **实盘**;开「研判循环」。
2. `read_console_messages`(onlyErrors)→ 0 error;`screenshot` 确认开关/流水正常。

Expected: 0 error;无定时器风暴(切 seat/模式不报错)。

盘中真验证(若开市,fresh=true):为缩短等待,**临时**把 Step 2 的 `3600000` 改 `120000`(2 分钟)验证定时研判按节拍多出「定时研判」行,确认后**改回 3600000** 再 d+1 重载。休市(fresh=false)替代验证:确认开循环时定时**不触发**(诚实:盘外不研判),仅 UI/0 error。

---

## Task 4: 端到端验证 + 文档

- [ ] **Step 1: 三触发穿插联跑(开市优先,休市做可做部分)**

实盘 + 循环开,确认三条流水都能产生且互不打架:
- 手动:点「立单」→ 出「手动研判」行。
- 成交后:易触发单实时触发 → 约 1.5s 后「成交后研判」行 + 新单重新盯盘。
- 定时:(临时缩短节流)到点出「定时研判」行;验毕改回 1 小时。
`read_console_messages`(onlyErrors)全程 0 error;`screenshot` 留证。

- [ ] **Step 2: 复盘模式回归**

切复盘:不应出现「研判循环」开关;手动「立单」「复盘验触发」仍正常;0 error。

- [ ] **Step 3: README 记一条**

在 `ui/seats/README.md` 现有条目区(与其它 `✅` 条目同格式)加一条,概述:研判循环三触发(手动/成交后/定时·每小时封顶)、循环开关仅 live、研判流水、`fresh` 由 app `quote.fresh` 传入、本期复用 `lzSeatOrder` 无后端改动、`?v=20260608d15`、盘中/休市各自验证结论。注明位置感知研判(继续持/平)与影子组合归第 2 期。

- [ ] **Step 4: 终版版本号自检**

Run: `grep -c 20260608d15 "ui/seats/观澜 · 落子.html"`
Expected: `6`。

---

## Self-Review(已对照 spec §2.4 / §4 第 1 期)

- **spec 覆盖**:三触发(手动/成交后/定时)= Task1 手动 + Task2 成交后 + Task3 定时;"每小时封顶" = Task3 的 3600000 节流;"席位周期取慢"在无真 seat-clock(第 3 期)前以"每小时封顶"为安全默认,Task3 注释与 README 注明。两层架构:研判层 = `runJudge→lzSeatOrder`,执行层 = 既有 8s 盯盘 + 触发引擎(未改)。"系统只出信号不代下单"沿用既有文案,未改。
- **占位符**:无 TODO;每步给出完整代码/命令/期望。
- **类型/命名一致**:`runJudge(reason)`、`loopOn`/`loopLog`/`lastJudgeRef`、`LZ_REASON_CN`、`fresh` prop 在 Task1-3 一致;`runJudge('manual'|'fill'|'timer')` 三处 reason 与 `LZ_REASON_CN` 键一致。
- **已知本期限制(非缺口,spec 已划归后续)**:成交后研判暂为"重新拟一张单"(位置感知的继续持/平依赖第 2 期影子组合的持仓台账);循环作用于当前(票,席),多票/多席循环属第 3 期。
