# 观澜 · 帷幄 二期 Implementation Plan(U 对话UI移植 / A 研报并入 / D 导航收敛 / B 记忆 / C 体验)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 帷幄对话界面全盘移植 chat 页观感(数据源换 console 事件流);深度研报并入帷幄(后台 runner 不阻塞对话);顶栏收敛为「帷幄+落子」两门面(经验卡/图谱进右栏、ww_show_page 口头调界面);condenser+memory.md 让长对话不失忆。

**Architecture:** 前端 = 把 chat app.jsx 的成熟组件(UserBubble/AiSummary/markdown/ToolChain/ReportDrawer/ConfirmModal)按精确行号搬进 console-*,新增「事件流→消息模型」推导层;后端 = console 工具新增 5 件(report_run/show_page/cards_save/memory×2),api 增后台任务跑道(side_effect.background → asyncio 派生 + 进度轮询 + 完成回注)与 condenser(复用引擎 BuddyAgent.compact)。

**Tech Stack:** 同一期。事实依据 = 2026-06-12 三份读码报告(chat UI 行号/run_report 管线/嵌入面),本计划内全部 file:line 已核实。

**Spec:** `docs/superpowers/specs/2026-06-11-weiwo-console-design.md` §2 第五至七轮 + §5 二期表。

---

## ⚠ 仓库约定(同一期,执行前必读)

- **无 git**;检查点收尾(pytest / 重启 9999 / 浏览器由 controller 验)。
- pytest:`G:\financial-analyst\.venv\Scripts\python.exe -m pytest tests -q --ignore=tests/test_recipe_memory.py --ignore=tests/test_session_seed.py`,基线 **121 绿**。
- **GateGuard**:每文件首次 Write/Edit 被拦 → 重试消息写四事实后原样重试;首次 shell 同理;绝不删文件。
- 用户原话(四事实用):「现在帷幄的对话框太丑了 直接学对话研报的对话界面就行 全抄过来 只不过后端变化了」「顶栏可以只存在帷幄和落子」。
- **改 jsx 必 bump ?v=(用 Edit)**;测试不 import 引擎(懒导入/monkeypatch);改 python 重启 9999(杀监听 PID)。
- **移植红线**:从 chat app.jsx 搬组件 = 按本计划给定行号**逐字复制后只做本计划列出的适配改动**;chat 页本体一行不动(它退役但保留)。

## File Structure

```
ui/console/
  观澜 · 帷幄.html        # +CSS 动画/hover 类、+console-drawer.jsx 脚本、?v 全 bump
  console-data.jsx        # +wwDeriveItems 推导层、+bgTasks 折叠、WW_PAGES +cards/graph、TOOL_CN 补名
  console-thread.jsx      # 重写:chat 观感(移植组件)+ 事件流数据源
  console-drawer.jsx      # 新:ReportDrawer + ReportMarkdown 移植(props 化)
  console-rail.jsx        # +后台任务分区(进度条)
  console-app.jsx         # +drawer 状态、+拖宽、挂 WwDrawer
guanlan_v2/console/
  tools.py                # +report_run/show_page/cards_save/memory_read/memory_write impl+注册
  api.py                  # +background 跑道(_run_report_bg/进度轮询/入档)+condenser+memory 注入
guanlan_v2/console/store.py  # +merge_meta 公开方法
tests/
  test_console_store.py   # +merge_meta
  test_console_tools.py   # +6 impl 测试
  test_console_api.py     # +runner/condenser/memory 测试
ui/_shared/guanlan-nav.js # MODULES 收敛为 帷幄+落子
ui/cards/validation.jsx + 观澜 · 经验验证区.html   # embed 卫生 + refine 隐藏 + bump
ui/graph/graph.jsx + 观澜 · 研究图谱.html          # embed 卫生 + bump
```

新事件/字段契约(对一期协议的加法):
- `task_update` 可带 `kind:"report"`、`code`、`progress`(0-1 浮点)——后台任务进度;
- `tool_result` artifact 新 kind `report_md`(payload `{path, code, name}`,**不带 page** → 不进 bench,由中栏渲 ReportCard);artifact 新 kind `page_view`(带 page 不带 channel → bench 纯调出页面);
- 新事件 `condensation {summary}`(中栏渲压缩分隔线);
- `meta.bg`:`{<bg_id>: {kind, code, status, ts}}`(后台任务留档)。

---

### Task 1: 帷幄 html — CSS 随迁 + 新脚本位 + bump

**Files:** Modify `ui/console/观澜 · 帷幄.html`;Create `ui/console/console-drawer.jsx`(占位)

- [ ] **Step 1.1** `<style>` 块追加(放现有 `@keyframes blink` 之后;**一期已有 `pulse`(纯 opacity)与 `blink`,保留不动**;ToolRow 脉冲圈用新名 `pulseRing`):

```css
  @keyframes pulseRing { 0%,100% { transform: scale(1); opacity: 0.4; } 50% { transform: scale(1.4); opacity: 0; } }
  @keyframes fadeIn { from { opacity: 0; transform: translateY(4px); } to { opacity: 1; transform: translateY(0); } }
  @keyframes slideInRight { from { opacity: 0; transform: translateX(40px); } to { opacity: 1; transform: translateX(0); } }
  .hover-row:hover { background: rgba(28,24,20,0.04); }
  .hover-link:hover { color: var(--ink) !important; text-decoration: underline; text-underline-offset: 3px; }
  .hover-pill:hover { background: rgba(28,24,20,0.06); }
```

- [ ] **Step 1.2** 先建占位 `ui/console/console-drawer.jsx`(只含一行注释 `// console-drawer.jsx — Task 4 填充`,避免 404 中断脚本链);脚本表:`console-bench.jsx` 行后插 `<script type="text/babel" data-presets="env,react" src="console-drawer.jsx?v=20260613a"></script>`;六个 console-*.jsx 的 `?v=` 全改 `20260613a`。

- [ ] **Step 1.3** 检查点:页面强刷无 babel 报错、一期功能不回归。

---

### Task 2: console-data.jsx — 事件→消息模型推导层 + 注册表扩容

**Files:** Modify `ui/console/console-data.jsx`

- [ ] **Step 2.1** `WW_TOOL_CN` 补:`ww_report_run: '深度研报', ww_show_page: '调出界面', ww_cards_save: '沉淀经验卡', ww_memory_read: '读记忆', ww_memory_write: '记一笔',`

- [ ] **Step 2.2** `WW_PAGES` 加两条(bench 即插即用,读码报告 §5 已证零改动):

```jsx
  cards: { label: '经验卡', file: '../cards/观澜 · 经验验证区.html', channel: 'validation' },
  graph: { label: '图谱', file: '../graph/观澜 · 研究图谱.html', channel: null },
```

- [ ] **Step 2.3** `wwInitState` 加 `bgTasks: {}`;`wwApply` 在 task_update 分支后加:

```jsx
  if (ev.type === 'task_update' && ev.kind) {           // 后台任务(kind=report 等):按 task_id 聚合最新态
    n.bgTasks = { ...s.bgTasks, [ev.task_id]: { ...(s.bgTasks[ev.task_id] || {}), ...ev } };
  }
```
**注意**:一期 `task_update` 分支的 busy 翻转对带 `kind` 的后台事件**要跳过**(后台任务的 running/done 不该改对话 busy)——把一期那段改成 `if (ev.type === 'task_update' && !ev.kind) { …原 busy 逻辑… }`。

- [ ] **Step 2.4** 新增**推导层** `wwDeriveItems(events, busy)`(纯函数,挂 window.WW;chat 是 plan/idx 协议、console 是按名配对+delta 分片,推导规则:两条 user_msg 之间的 tool_call/result 折一个 chain;连续 agent_delta 合并 answer):

```jsx
// 事件流 → chat 形消息模型:[{kind:'user'|'chain'|'answer'|'report'|'condense'|'error', ...}]
function wwDeriveItems(events, busy) {
  const items = [];
  let chain = null;            // 当前折叠中的工具链 {kind:'chain', chain:[...]}
  let openIdx = {};            // tool 名 → chain 数组下标(就近配对)
  const flushChain = () => { chain = null; openIdx = {}; };
  events.forEach(ev => {
    if (ev.type === 'user_msg') {
      flushChain();
      items.push({ kind: 'user', id: 'u' + ev.id, text: ev.text });
    } else if (ev.type === 'tool_call') {
      if (!chain) { chain = { kind: 'chain', id: 'c' + ev.id, chain: [] }; items.push(chain); }
      openIdx[ev.tool] = chain.chain.length;
      chain.chain.push({ name: ev.tool, cn: (WW_TOOL_CN[ev.tool] || ev.tool),
        args: ev.args ? JSON.stringify(ev.args) : '{}', t: 0, status: 'running', _ts: ev.ts });
    } else if (ev.type === 'tool_result') {
      const at = chain ? openIdx[ev.tool] : null;
      if (chain && at != null && chain.chain[at] && chain.chain[at].status === 'running') {
        const row = chain.chain[at];
        row.status = ev.ok ? 'done' : 'fail';
        row.result = ev.summary || '';
        row.t = row._ts && ev.ts ? Math.max(0, (new Date(ev.ts) - new Date(row._ts)) / 1000) : 0;
        delete openIdx[ev.tool];
      }
      if (ev.artifact && ev.artifact.kind === 'report_md') {
        items.push({ kind: 'report', id: 'r' + ev.id, path: ev.artifact.payload.path,
          code: ev.artifact.payload.code, name: ev.artifact.payload.name || ev.artifact.payload.code });
      }
    } else if (ev.type === 'agent_delta' && ev.text) {
      flushChain();              // 文本到来 = 这一段工具链收束
      const last = items[items.length - 1];
      if (last && last.kind === 'answer') last.text += '\n\n' + ev.text;
      else items.push({ kind: 'answer', id: 'a' + ev.id, text: ev.text });
    } else if (ev.type === 'condensation') {
      items.push({ kind: 'condense', id: 'k' + ev.id, summary: ev.summary || '' });
    } else if (ev.type === 'task_update' && ev.status === 'error' && !ev.kind) {
      items.push({ kind: 'error', id: 'e' + ev.id, note: ev.note || '' });
    }
  });
  const last = items[items.length - 1];
  if (busy && last && last.kind === 'answer') last.streaming = true;   // 流式光标
  if (!busy) items.forEach(it => { if (it.kind === 'chain') it.chain.forEach(r => { if (r.status === 'running') r.status = 'fail'; }); });
  return items;
}
```
导出:`window.WW = { ..., deriveItems: wwDeriveItems }`。

- [ ] **Step 2.5** 检查点:浏览器 console 手测 `WW.deriveItems([{id:1,type:'user_msg',text:'x'},{id:2,type:'tool_call',tool:'ww_backtest',args:{},ts:'2026-06-13T10:00:00'},{id:3,type:'tool_result',tool:'ww_backtest',ok:true,summary:'s',ts:'2026-06-13T10:00:05'},{id:4,type:'agent_delta',text:'done'}], false)` → 3 项(user / chain[done,t=5] / answer)。

---

### Task 3: console-thread.jsx 重写 — chat 观感移植

**Files:** Rewrite `ui/console/console-thread.jsx`

移植源(全部 `ui/chat/app.jsx`,**逐字复制后只做列出的适配**;行号经 2026-06-12 读码报告核实):

| 组件 | 源行号 | 适配改动 |
|---|---|---|
| `renderInline` + `Cite` | 3234-3246, 2075-2084 | 零改(Cite 先粘,renderInline 引用它) |
| `renderChatMarkdown`(含 `_isTableRow/_isTableSep/_splitRow/_alignOf` 与 3252-3268 列表助手) | 3249-3349 | 零改 |
| `UserBubble` | 1641-1651 | 零改 |
| `AiAvatar` | 1669-1671 | 零改 |
| `ToolChain` | 1674-1725 | ① props 改 `({ msg })`,删 `backendUrl/dispatch`;② 删 planning 占位分支(1684-1696);③ 折叠头 `msg.kindLabel` 改固定 `'调用工具'`;④ 删 ToolRow 里的 DeepReportProgress 内嵌(1806-1814) |
| `ToolRow` | 1772-1818 | ① 删 onClick open_detail(1777)与 cursor;② 脉冲圈 `'pulse 1.6s…'` → `'pulseRing 1.6s…'`;③ **加 fail 态**:`const fail = item.status==='fail';` 序号方块 fail → `background:'var(--yin)'` 显 `✗`;状态文案 fail → `'✗ 失败'` 染 `var(--yin)`;结果行照常显 result(失败原因) |
| `ConfirmModal` | 2660-2729 | ① props 改 `({ confirm, onChoice })`;② resolve 体改 `onChoice(choice)`;③ **删 'a' 键/按钮**(console 后端无 autoApproved;键盘监听删 a 分支);④ 删 run_report 特判与 dispatch;⑤ label 用 `(window.WW.TOOL_CN[confirm.tool] || confirm.tool)`,detail 用 `JSON.stringify(confirm.args, null, 1)` |

- [ ] **Step 3.1** 新 `WwThread` 主体(完整骨架,移植组件粘其上方):

```jsx
function WwReportCard({ item, onOpen }) {   // ReportCard 视觉(app.jsx:2049-2073 简化)
  return (
    <div style={{ display: 'flex', gap: 12 }}>
      <AiAvatar />
      <div style={{ border: '1px solid var(--yin)', padding: '10px 14px', fontSize: 13, fontFamily: 'var(--serif)', background: 'var(--paper-2)' }}>
        📄 {item.name} 深度研报已生成
        <span className="hover-link" onClick={() => onOpen(item)} style={{ marginLeft: 12, color: 'var(--yin)', cursor: 'pointer', fontSize: 12 }}>查看全文 ↗</span>
      </div>
    </div>
  );
}

function WwThread({ state, onSend, onConfirm, onOpenReport }) {
  const [draft, setDraft] = React.useState('');
  const scrollRef = React.useRef(null);
  const stickRef = React.useRef(true);
  const items = window.WW.deriveItems(state.events, state.busy);
  React.useEffect(() => {                                   // 粘底(app.jsx:1565-1577 语义)
    const el = scrollRef.current; if (!el) return;
    if (stickRef.current) el.scrollTop = el.scrollHeight;
  }, [state.events.length]);
  React.useEffect(() => { stickRef.current = true; }, [state.sid]);
  const onScroll = () => { const el = scrollRef.current; if (el) stickRef.current = (el.scrollHeight - el.scrollTop - el.clientHeight) < 80; };
  const send = () => { const t = draft.trim(); if (!t || state.busy) return; setDraft(''); onSend(t); };
  return (
    <div style={{ display: 'flex', flexDirection: 'column', minHeight: 0, minWidth: 0 }} className="paper-bg">
      <div ref={scrollRef} onScroll={onScroll} style={{ flex: 1, overflowY: 'auto', overflowX: 'hidden', padding: '24px 40px', display: 'flex', flexDirection: 'column', gap: 18, minHeight: 0 }}>
        {items.length === 0 && (
          <div style={{ textAlign: 'center', color: 'var(--ink-3)', fontSize: 13, marginTop: 80, fontFamily: 'var(--serif)', letterSpacing: 1 }}>
            对观澜下令——选股、回测、研报、研判、经验沉淀,一句话即可。
          </div>
        )}
        {items.map(m => {
          if (m.kind === 'user') return <UserBubble key={m.id} text={m.text} />;
          if (m.kind === 'chain') return <ToolChain key={m.id} msg={m} />;
          if (m.kind === 'answer') return (
            <div key={m.id} style={{ display: 'flex', gap: 12 }}>
              <AiAvatar />
              <div style={{ flex: 1, minWidth: 0, fontFamily: 'var(--serif)', fontSize: 14, color: 'var(--ink)', padding: '4px 0' }}>
                {renderChatMarkdown(m.text)}
                {m.streaming && <span style={{ display: 'inline-block', width: 6, height: 14, background: 'var(--ink)', marginLeft: 4, verticalAlign: -2, animation: 'blink 1s steps(2) infinite' }} />}
              </div>
            </div>);
          if (m.kind === 'report') return <WwReportCard key={m.id} item={m} onOpen={onOpenReport} />;
          if (m.kind === 'condense') return (
            <div key={m.id} title={m.summary} style={{ textAlign: 'center', fontSize: 10.5, color: 'var(--ink-3)', letterSpacing: 2, fontFamily: 'var(--serif)' }}>—— 前文已压缩入摘要 ——</div>);
          if (m.kind === 'error') return <div key={m.id} style={{ marginLeft: 40, color: 'var(--yin)', fontSize: 12 }}>✗ {m.note}</div>;
          return null;
        })}
        {state.busy && (!items.length || items[items.length - 1].kind !== 'answer') && (
          <div style={{ display: 'flex', gap: 12 }}><AiAvatar /><div style={{ fontSize: 12, color: 'var(--ink-3)', fontStyle: 'italic', padding: '6px 0' }}>⠋ 运筹中…</div></div>
        )}
      </div>
      {/* 输入坞:chat Composer 视觉(app.jsx:2284-2294),砍 slash/@/上传/模式 pill */}
      <div style={{ padding: '10px 40px 18px' }}>
        <div style={{ border: '1px solid var(--line)', borderRadius: 13, padding: '11px 13px 11px 17px', background: 'var(--paper)', display: 'flex', alignItems: 'flex-end', gap: 10 }}
          onMouseEnter={e => { e.currentTarget.style.borderColor = 'var(--ink-2)'; }}
          onMouseLeave={e => { e.currentTarget.style.borderColor = 'var(--line)'; }}>
          <textarea rows={1} value={draft} onChange={e => setDraft(e.target.value)}
            onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(); } }}
            placeholder={state.busy ? '执行中——完成后再下一令' : '对观澜下令…(Enter 发送,Shift+Enter 换行)'}
            style={{ flex: 1, border: 0, outline: 0, resize: 'none', background: 'transparent', color: 'var(--ink)', fontFamily: 'var(--serif)', fontSize: 14, lineHeight: 1.6, minHeight: 22, maxHeight: 120 }} />
          <div onClick={send} className="mono" style={{ width: 27, height: 23, border: '1px solid ' + (state.busy ? 'var(--line)' : 'var(--ink)'), color: state.busy ? 'var(--ink-3)' : 'var(--ink)', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 12, cursor: state.busy ? 'default' : 'pointer', borderRadius: 4 }}>↵</div>
        </div>
      </div>
      {state.confirm && <ConfirmModal confirm={state.confirm} onChoice={onConfirm} />}
    </div>
  );
}
window.WwThread = WwThread;
```

- [ ] **Step 3.2** 检查点(controller 浏览器):空态→发「分析 rank(roe)」→ 用户黑卡、ToolChain(运行中展开/完成自动收起显「已用 N 个工具 · Xs」)、markdown 回答(表格/粗体)、研判指令出确认模态 y/n 可用。

---

### Task 4: console-drawer.jsx — 研报抽屉移植

**Files:** Rewrite `ui/console/console-drawer.jsx`;Modify `ui/console/console-app.jsx`

移植源:`ReportMarkdown`(app.jsx:3153-3232,零改)+ `triggerDownload`(app.jsx:3397-3403,零改)+ `ReportDrawer`(app.jsx:2936-3150)适配为 `WwDrawer({ drawer, onClose })`:

- [ ] **Step 4.1** 适配清单:① 删 mock 步骤分支(3087-3115)与 REPORT_STEPS 依赖;② 删轻量详情分支(2980-3031);③ `dispatch({type:'close_report'})` → `onClose()`(两处:closeDrawer 2977 与 × 键 3060);④ 「加入研究档案」保留(`window.GL.put` 形状照 app.jsx:2951-2959;后端已自动入档,id 同日幂等不冲突);⑤ 保持 `position:'fixed', top:44, width:540` 与 `slideInRight`(帷幄也有 44px nav);⑥ running 态保留每秒计时器+走马灯(2940-2947, 3065-3086)。

- [ ] **Step 4.2** console-app.jsx 接线:
  - `const [drawer, setDrawer] = React.useState(null);`
  - ```jsx
    const openReport = async (item) => {
      setDrawer({ sym: { code: item.code, name: item.name }, status: 'running', text: '', path: item.path, startedAt: Date.now() });
      try {
        const r = await fetch(WW.API + '/report?path=' + encodeURIComponent(item.path)).then(x => x.json());
        setDrawer(d => d && { ...d, status: 'done', text: r.ok ? r.text : ('⚠ 读取失败: ' + (r.reason || '')) });
      } catch (e) { setDrawer(d => d && { ...d, status: 'done', text: '⚠ 读取失败: ' + e }); }
    };
    ```
  - WwThread 传 `onOpenReport={openReport}`;布局容器后挂 `{drawer && <WwDrawer drawer={drawer} onClose={() => setDrawer(null)} />}`。
  - **直播自动开抽屉**:dispatch 外包一层(reducer 保持纯):`const dispatchLive = (a) => { dispatch(a); if (a.type === 'ev' && a.ev.type === 'tool_result' && a.ev.artifact && a.ev.artifact.kind === 'report_md') { const p = a.ev.artifact.payload; setTimeout(() => openReport({ path: p.path, code: p.code, name: p.name || p.code }), 0); } };` `WW.connect(sid, dispatchLive)`(snapshot 重放不触发)。

- [ ] **Step 4.3** 检查点:浏览器 console 手调 `openReport({path:'G:\\guanlan-v2\\out\\SZ300750_2026-06-11.md', code:'SZ300750', name:'宁德时代'})` → 侧滑抽屉、ReportMarkdown 渲真研报全文、「↧ 导出 markdown」可下载、× 关闭。

---

### Task 5: console/tools.py — 五件新工具

**Files:** Modify `guanlan_v2/console/tools.py`;Test `tests/test_console_tools.py`

事实依据:引擎 run_report Tool=tools.py:2096-2117(`_tool_report` 同步阻塞 subprocess,side_effect.md_path);`normalize_code`(tools.py:44);/cards POST upsert(cards/api.py:69-79,id 后端分配 EV-NNN);archive PUT 白名单含 research(archive/api.py:45-54)。

- [ ] **Step 5.1 失败测试**(test_console_tools.py 追加):

```python
def test_report_run_impl_returns_background_envelope():
    res = ct.report_run_impl(code="SZ300750", name="宁德时代")
    assert res["ok"] is True and "5-8" in res["content"]
    assert res["background"] == {"kind": "report", "code": "SZ300750", "name": "宁德时代", "asof": None}
    assert res["artifact"] is None


def test_report_run_impl_rejects_bad_code():
    assert ct.report_run_impl(code="茅台")["ok"] is False
    assert ct.report_run_impl(code="")["ok"] is False


def test_show_page_impl():
    res = ct.show_page_impl(page="cards")
    assert res["ok"] and res["artifact"]["kind"] == "page_view" and res["artifact"]["page"] == "cards"
    assert ct.show_page_impl(page="nope")["ok"] is False


def test_cards_save_impl(monkeypatch):
    sent = {}
    def fake_post(path, payload, timeout=120):
        sent["path"] = path; sent.update(payload)
        return {"id": "EV-015", "title": payload["title"], "status": payload["status"]}
    monkeypatch.setattr(ct, "_self_post", fake_post)
    res = ct.cards_save_impl(title="动量月频有效", insight="csi300 月频动量 RankIC 0.05", expr="rank(-delta(close,20))")
    assert sent["path"] == "/cards" and sent["status"] == "draft"
    assert res["ok"] and "EV-015" in res["content"]
    assert res["artifact"]["page"] == "cards" and res["artifact"]["payload"]["focusCardName"] == "动量月频有效"


def test_memory_write_and_read(tmp_path, monkeypatch):
    monkeypatch.setattr(ct, "_MEMORY_PATH", tmp_path / "memory.md")
    assert ct.memory_write_impl(text="用户偏好 csi300 月频")["ok"]
    assert "csi300" in ct.memory_read_impl()["content"]
    assert ct.memory_write_impl(text="")["ok"] is False


def test_wrap_passes_background(monkeypatch):
    import types
    fake = types.SimpleNamespace(ToolResult=lambda content, is_error=False, side_effect=None:
                                 types.SimpleNamespace(content=content, is_error=is_error, side_effect=side_effect))
    monkeypatch.setattr(ct, "_buddy_tools_mod", lambda: fake)
    out = ct._wrap(lambda **kw: {"ok": True, "content": "ok", "background": {"kind": "report"}})()
    assert out.side_effect["background"]["kind"] == "report"
```

- [ ] **Step 5.2 实现**(tools.py 追加;`_wrap` 的 side_effect 组装处加 `if out.get("background"): se["background"] = out["background"]`):

```python
import re as _re

_MEMORY_PATH = Path(__file__).resolve().parents[2] / "var" / "console" / "memory.md"
_CODE_RE = _re.compile(r"^(SH|SZ|BJ)\d{6}$")
_SHOW_PAGES = {"screen": "选股", "factor": "工作流", "cards": "经验卡", "graph": "研究图谱"}


def report_run_impl(code: str, name: str = "", asof: Optional[str] = None) -> Dict[str, Any]:
    """受理深度研报(不在工具线程跑 5-8 分钟子进程——返回 background 信封,由 api 后台跑道执行)。"""
    code = (code or "").strip().upper()
    if _re.match(r"^\d{6}$", code):          # 裸码 → 引擎规范化(懒导入)
        try:
            code = _buddy_tools_mod().normalize_code(code)
        except Exception:
            return {"ok": False, "content": f"无法规范化代码 {code}(需 SH/SZ/BJ 前缀)", "artifact": None}
    if not _CODE_RE.match(code):
        return {"ok": False, "content": f"代码格式非法: {code}(应为 SH600519 形)", "artifact": None}
    return {"ok": True,
            "content": f"研报已受理:{name or code} 后台生成中(真实约 5-8 分钟),完成后会在对话里通知并可直接翻阅。期间可以继续下其他指令。",
            "artifact": None,
            "background": {"kind": "report", "code": code, "name": name, "asof": (asof or None)}}


def show_page_impl(page: str = "") -> Dict[str, Any]:
    page = (page or "").strip()
    if page not in _SHOW_PAGES:
        return {"ok": False, "content": f"未知界面: {page}(可选 {'/'.join(_SHOW_PAGES)})", "artifact": None}
    return {"ok": True, "content": f"已调出「{_SHOW_PAGES[page]}」界面(右栏)。",
            "artifact": artifact("page_view", page=page, channel=None, payload={})}


def cards_save_impl(title: str, insight: str = "", expr: str = "", verdict: str = "存疑",
                    conf: int = 0, ic: str = "", cat: str = "其他",
                    status: str = "draft") -> Dict[str, Any]:
    title = (title or "").strip()
    if not title:
        return {"ok": False, "content": "缺少卡片标题 title", "artifact": None}
    if status not in {"draft", "approved"}:
        return {"ok": False, "content": f"status 非法: {status}(允许 draft/approved)", "artifact": None}
    try:
        r = _self_post("/cards", {"title": title, "insight": insight, "expr": expr,
                                  "verdict": verdict, "conf": int(conf or 0), "ic": str(ic or ""),
                                  "cat": cat, "status": status, "src": "帷幄 · ww_cards_save"})
    except Exception as e:
        return {"ok": False, "content": f"经验卡保存失败: {e}", "artifact": None}
    cid = r.get("id", "?")
    return {"ok": True, "content": f"经验卡已沉淀: {cid}「{title}」({status})",
            "artifact": artifact("card", page="cards", channel="validation",
                                 payload={"focusCardName": title})}


def memory_write_impl(text: str = "") -> Dict[str, Any]:
    text = (text or "").strip()
    if not text:
        return {"ok": False, "content": "空记忆不写", "artifact": None}
    try:
        _MEMORY_PATH.parent.mkdir(parents=True, exist_ok=True)
        with _MEMORY_PATH.open("a", encoding="utf-8") as f:
            f.write(f"- [{date.today().isoformat()}] {text}\n")
    except Exception as e:
        return {"ok": False, "content": f"记忆写入失败: {e}", "artifact": None}
    return {"ok": True, "content": "已记入帷幄记忆。", "artifact": None}


def memory_read_impl() -> Dict[str, Any]:
    try:
        body = _MEMORY_PATH.read_text(encoding="utf-8") if _MEMORY_PATH.exists() else ""
    except Exception as e:
        return {"ok": False, "content": f"记忆读取失败: {e}", "artifact": None}
    return {"ok": True, "content": ("帷幄记忆:\n" + body[-4000:]) if body.strip() else "记忆为空。", "artifact": None}
```

- [ ] **Step 5.3 注册**(register_console_tools 的 specs 追加 5 条;CONSOLE_ALLOWED 加 `ww_report_run/ww_show_page/ww_cards_save/ww_memory_write/ww_memory_read`):

```python
        ("ww_report_run",
         "生成单票深度研报(真引擎 16-agent,5-8 分钟,后台跑不阻塞;完成自动通知并可翻阅)。需要用户确认。Deep-dive stock research report.",
         {"type": "object", "properties": {"code": {"type": "string", "description": "股票代码,如 SZ300750 或 300750"},
          "name": {"type": "string"}, "asof": {"type": "string", "description": "YYYY-MM-DD,缺省今天"}},
          "required": ["code"]},
         _wrap(report_run_impl), "minutes", True),
        ("ww_show_page",
         "把平台某个界面调出到右栏给用户看(screen=选股/factor=工作流/cards=经验卡/graph=研究图谱)。用户说『调出/打开/看看XX界面』时用。",
         {"type": "object", "properties": {"page": {"type": "string", "enum": ["screen", "factor", "cards", "graph"]}},
          "required": ["page"]},
         _wrap(show_page_impl), "instant", False),
        ("ww_cards_save",
         "把验证过的结论沉淀为经验卡(默认 draft)。需要用户确认。",
         {"type": "object", "properties": {"title": {"type": "string"}, "insight": {"type": "string"},
          "expr": {"type": "string"}, "verdict": {"type": "string"}, "conf": {"type": "integer"},
          "ic": {"type": "string"}, "cat": {"type": "string"},
          "status": {"type": "string", "enum": ["draft", "approved"], "default": "draft"}},
          "required": ["title"]},
         _wrap(cards_save_impl), "instant", True),
        ("ww_memory_write", "往帷幄长期记忆追加一条(用户偏好/重要结论)。",
         {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]},
         _wrap(memory_write_impl), "instant", False),
        ("ww_memory_read", "读取帷幄长期记忆全文。",
         {"type": "object", "properties": {}},
         _wrap(memory_read_impl), "instant", False),
```

- [ ] **Step 5.4** 跑测试:目标文件 ≥23 passed;全量 ≥127 绿。

---

### Task 6: console/api.py + store.py — 后台跑道(研报)+ 完成回注 + 自动入档

**Files:** Modify `guanlan_v2/console/api.py`、`guanlan_v2/console/store.py`;Test `tests/test_console_api.py`、`tests/test_console_store.py`

事实依据:引擎 `_tool_report` 同步阻塞 900s(tools.py:339-375);进度文件 `out/{CODE}_progress.json`(`{total,done,fail,running,agents,ts}`,完成=done+fail==total,跨次运行残留);**run_report 子进程吃的是上游引擎**(venv .pth → G:\financial-analyst\src),要吃 fork 改动须 env 注入 `PYTHONPATH=G:\guanlan-v2\engine`;同 code 并发互踩 progress/md → in-flight 按 code 去重;`financial-analyst` 可执行解析自 venv Scripts 目录(看门狗用该 venv python 起 9999)。

- [ ] **Step 6.1 store.merge_meta**(store.py 加公开方法 + 测试):

```python
    def merge_meta(self, sid: str, **fields: Any) -> Optional[Dict[str, Any]]:
        """加锁合并写 meta 顶层键(后台任务留档等);缺会话返回 None。"""
        with self._lock:
            meta = self.get_meta(sid)
            if meta is None:
                return None
            meta.update(fields)
            meta["updated"] = _now()
            self._save_meta(meta)
            return meta
```
测试(test_console_store.py 追加):
```python
def test_merge_meta(tmp_path):
    st = ConsoleStore(root=tmp_path)
    sid = st.create_session()["id"]
    m = st.merge_meta(sid, bg={"bg_1": {"kind": "report", "status": "done"}})
    assert m["bg"]["bg_1"]["kind"] == "report"
    assert st.merge_meta("cs_nope", bg={}) is None
```

- [ ] **Step 6.2 失败测试**(test_console_api.py 追加):

```python
def test_background_report_lifecycle(tmp_path, monkeypatch):
    """se.background → 后台事件链:task_update(kind=report,running) → tool_result(report_md) → task_update(done)。"""
    import guanlan_v2.console.api as capi
    monkeypatch.setattr(capi, "_call_buddy_report",
                        lambda code, asof: {"ok": True, "content": "Report written. 评级4/10",
                                            "md_path": "G:\\guanlan-v2\\out\\SZ300750_2026-06-13.md"})
    monkeypatch.setattr(capi, "_archive_research", lambda **kw: True)
    monkeypatch.setattr(capi, "_BG_PROGRESS_POLL", 0.05)

    class BgAgent:
        messages = []
        async def run_turn(self, text, confirm_callback=None, allowed_tools=None):
            yield _Evt("tool_call", {"name": "ww_report_run", "args": {"code": "SZ300750"}})
            yield _Evt("tool_result", {"name": "ww_report_run", "content": "研报已受理", "is_error": False,
                                       "side_effect": {"background": {"kind": "report", "code": "SZ300750",
                                                                      "name": "宁德时代", "asof": None}}})
            yield _Evt("done", None)

    app = FastAPI(); store = ConsoleStore(root=tmp_path)
    app.include_router(build_console_router(store=store, agent_factory=lambda sid: BgAgent()))
    with TestClient(app) as c:
        sid = c.post("/console/sessions", json={}).json()["meta"]["id"]
        c.post("/console/send", json={"sid": sid, "text": "给宁德写研报"})
        evs = []
        for _ in range(80):
            evs = store.read_events(sid)
            if any(e["type"] == "task_update" and e.get("kind") == "report" and e.get("status") == "done" for e in evs):
                break
            time.sleep(0.1)
    kinds = [(e["type"], e.get("kind"), e.get("status")) for e in evs]
    assert ("task_update", "report", "running") in kinds
    art = [e for e in evs if (e.get("artifact") or {}).get("kind") == "report_md"][0]
    assert art["artifact"]["payload"]["code"] == "SZ300750" and art["artifact"]["payload"]["path"].endswith(".md")
    assert ("task_update", "report", "done") in kinds
    assert store.get_meta(sid).get("bg")


def test_background_report_failure_honest(tmp_path, monkeypatch):
    import guanlan_v2.console.api as capi
    monkeypatch.setattr(capi, "_call_buddy_report", lambda code, asof: {"ok": False, "content": "Report failed (exit 1)"})
    monkeypatch.setattr(capi, "_BG_PROGRESS_POLL", 0.05)

    class BgAgent:
        messages = []
        async def run_turn(self, text, confirm_callback=None, allowed_tools=None):
            yield _Evt("tool_result", {"name": "ww_report_run", "content": "x", "is_error": False,
                                       "side_effect": {"background": {"kind": "report", "code": "SZ000001", "name": "", "asof": None}}})
            yield _Evt("done", None)

    app = FastAPI(); store = ConsoleStore(root=tmp_path)
    app.include_router(build_console_router(store=store, agent_factory=lambda sid: BgAgent()))
    with TestClient(app) as c:
        sid = c.post("/console/sessions", json={}).json()["meta"]["id"]
        c.post("/console/send", json={"sid": sid, "text": "x"})
        evs = []
        for _ in range(80):
            evs = store.read_events(sid)
            if any(e["type"] == "task_update" and e.get("kind") == "report" and e.get("status") == "error" for e in evs):
                break
            time.sleep(0.1)
    errs = [e for e in evs if e["type"] == "task_update" and e.get("kind") == "report" and e.get("status") == "error"]
    assert errs and "failed" in errs[0]["note"]
```

- [ ] **Step 6.3 实现**(api.py;模块级新增):

```python
_BG_PROGRESS_POLL = float(os.environ.get("CONSOLE_BG_POLL", "5"))   # 进度轮询秒
_OUT_DIR = Path(__file__).resolve().parents[2] / "out"
_ENGINE_DIR = Path(__file__).resolve().parents[2] / "engine"
_bg_inflight: set = set()                                            # "report:CODE" 全局去重


def _call_buddy_report(code: str, asof: Optional[str]) -> Dict[str, Any]:
    """同步阻塞跑引擎深度研报(在 executor 线程调)。env 注入 PYTHONPATH=engine/ 让子进程吃 fork 改动;
    其余行为对齐引擎 _tool_report(tools.py:339-375):cwd=仓根、timeout 900、glob 最新 md。"""
    import subprocess
    cmd = ["financial-analyst", "report", code]
    if asof:
        cmd += ["--asof", asof]
    root = Path(__file__).resolve().parents[2]
    env = dict(os.environ)
    env["PYTHONPATH"] = str(_ENGINE_DIR) + os.pathsep + env.get("PYTHONPATH", "")
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=900, cwd=str(root), env=env)
    except subprocess.TimeoutExpired:
        return {"ok": False, "content": "研报超时(15 分钟)"}
    except Exception as e:
        return {"ok": False, "content": f"研报子进程启动失败: {e}"}
    if proc.returncode != 0:
        return {"ok": False, "content": f"Report failed (exit {proc.returncode}): {(proc.stderr or '')[-400:]}"}
    md_files = sorted(_OUT_DIR.glob(f"{code}_*.md"))
    if not md_files:
        return {"ok": False, "content": f"研报跑完但未找到 {code} 的 markdown 输出"}
    return {"ok": True, "content": f"研报完成: {md_files[-1].name}", "md_path": str(md_files[-1])}


def _read_report_progress(code: str) -> Optional[Dict[str, Any]]:
    p = _OUT_DIR / f"{code}_progress.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def _archive_research(code: str, name: str, md_path: str) -> bool:
    """研报自动入 GL 研究档案(服务端影子库;前端 bus v3 启动拉取合并;失败不阻塞主流程)。"""
    import time as _t
    from guanlan_v2.console.tools import _self_post
    day = datetime.now().date().isoformat()
    try:
        _self_post("/archive/put", {"artifact": {
            "id": f"rs_report_{code}_{day}", "type": "research",
            "title": f"{name or code}({code}) 深度研报", "kind": "研报",
            "from": "帷幄 · ww_report_run", "status": "raw",
            "path": md_path, "date": day, "refs": [], "ts": int(_t.time() * 1000)}})
        return True
    except Exception:
        return False
```
(api.py 顶部补 `from datetime import datetime` 与 `from pathlib import Path`、`import os`——以现有 import 为准查缺补漏。)

`build_console_router` 内(与 `_run_turn` 同级)新增:

```python
    async def _spawn_bg(sid: str, spec: Dict[str, Any]):
        if (spec or {}).get("kind") == "report":
            await _run_report_bg(sid, spec)
        else:
            _emit(sid, "task_update", task_id="bg_" + uuid.uuid4().hex[:10],
                  status="error", note=f"未知后台任务类型: {(spec or {}).get('kind')}")

    async def _run_report_bg(sid: str, spec: Dict[str, Any]):
        code = spec.get("code", "")
        name = spec.get("name", "") or ""
        key = f"report:{code}"
        bg_id = "bg_" + uuid.uuid4().hex[:10]
        if key in _bg_inflight:
            _emit(sid, "task_update", task_id=bg_id, kind="report", code=code, status="error",
                  note=f"{code} 已有研报在跑,忽略重复请求")
            return
        _bg_inflight.add(key)
        final = "error"
        try:
            _emit(sid, "task_update", task_id=bg_id, kind="report", code=code, status="running",
                  progress=0.0, note=f"深度研报 {name or code} 后台生成中(约 5-8 分钟)")
            loop = asyncio.get_running_loop()
            fut = loop.run_in_executor(None, lambda: _call_buddy_report(code, spec.get("asof")))
            last_prog = -1.0
            while not fut.done():
                await asyncio.sleep(_BG_PROGRESS_POLL)
                pr = _read_report_progress(code)
                if pr and pr.get("total"):
                    prog = round((pr.get("done", 0) + pr.get("fail", 0)) / pr["total"], 2)
                    if prog != last_prog:
                        last_prog = prog
                        _emit(sid, "task_update", task_id=bg_id, kind="report", code=code,
                              status="running", progress=prog,
                              note=f"{pr.get('done', 0)}/{pr['total']} agents")
            r = fut.result()
            if r.get("ok"):
                md_path = r["md_path"]
                _archive_research(code=code, name=name, md_path=md_path)
                _emit(sid, "tool_result", tool="ww_report_run", ok=True,
                      summary=str(r.get("content", ""))[:500],
                      artifact={"kind": "report_md", "page": None, "channel": None,
                                "payload": {"path": md_path, "code": code, "name": name}, "ref": None})
                _emit(sid, "task_update", task_id=bg_id, kind="report", code=code,
                      status="done", ok=True, progress=1.0, note=f"研报完成: {name or code}")
                final = "done"
            else:
                _emit(sid, "tool_result", tool="ww_report_run", ok=False,
                      summary=str(r.get("content", ""))[:500], artifact=None)
                _emit(sid, "task_update", task_id=bg_id, kind="report", code=code,
                      status="error", note=str(r.get("content", ""))[:300])
        except Exception as e:
            _emit(sid, "task_update", task_id=bg_id, kind="report", code=code,
                  status="error", note=f"{type(e).__name__}: {e}"[:300])
        finally:
            _bg_inflight.discard(key)
            m = st.get_meta(sid)
            if m is not None:
                bg = m.get("bg") or {}
                bg[bg_id] = {"kind": "report", "code": code, "status": final,
                             "ts": datetime.now().isoformat(timespec="seconds")}
                st.merge_meta(sid, bg=bg)
```

`_run_turn` 的 tool_result 分支(plan 分支旁)加:

```python
                    if "background" in se:
                        bt_ = asyncio.get_running_loop().create_task(_spawn_bg(sid, se["background"]))
                        _BG_TASKS.add(bt_); bt_.add_done_callback(_BG_TASKS.discard)
```

- [ ] **Step 6.4** 跑测试(api ≥9、store ≥6,全量 ≥130);重启 9999。

---

### Task 7: condenser + memory 注入(B 批后端)

**Files:** Modify `guanlan_v2/console/api.py`;Test `tests/test_console_api.py`

事实依据:引擎 `BuddyAgent.compact(transcript=None)`(agent.py:199)已实现 LLM 总结+替换 self.messages,直接复用。

- [ ] **Step 7.1 失败测试**:

```python
def test_condenser_triggers_and_emits(tmp_path):
    class FatAgent:
        def __init__(self):
            self.messages = [type("M", (), {"role": "user", "content": "x" * 800})() for _ in range(40)]
            self.compacted = False
        async def compact(self):
            self.compacted = True
            self.messages = self.messages[:1]
            return "前文摘要:聊了很多动量因子"
        async def run_turn(self, text, confirm_callback=None, allowed_tools=None):
            yield _Evt("text", "ok")
            yield _Evt("done", None)
    agents = {}
    def factory(sid):
        agents[sid] = FatAgent(); return agents[sid]
    app = FastAPI(); store = ConsoleStore(root=tmp_path)
    app.include_router(build_console_router(store=store, agent_factory=factory))
    with TestClient(app) as c:
        sid = c.post("/console/sessions", json={}).json()["meta"]["id"]
        c.post("/console/send", json={"sid": sid, "text": "继续"})
        evs = []
        for _ in range(50):
            evs = store.read_events(sid)
            if any(e["type"] == "task_update" and e.get("status") == "done" for e in evs):
                break
            time.sleep(0.1)
    assert list(agents.values())[0].compacted is True
    assert any(e["type"] == "condensation" and "摘要" in e.get("summary", "") for e in evs)


def test_memory_injected_into_turn(tmp_path, monkeypatch):
    import guanlan_v2.console.tools as ct2
    monkeypatch.setattr(ct2, "_MEMORY_PATH", tmp_path / "memory.md")
    ct2.memory_write_impl(text="用户只看 csi300")
    seen = {}
    class EchoAgent:
        messages = []
        async def run_turn(self, text, confirm_callback=None, allowed_tools=None):
            seen["text"] = text
            yield _Evt("done", None)
    app = FastAPI(); store = ConsoleStore(root=tmp_path)
    app.include_router(build_console_router(store=store, agent_factory=lambda sid: EchoAgent()))
    with TestClient(app) as c:
        sid = c.post("/console/sessions", json={}).json()["meta"]["id"]
        c.post("/console/send", json={"sid": sid, "text": "选股"})
        for _ in range(50):
            if "text" in seen:
                break
            time.sleep(0.1)
    assert "csi300" in seen["text"] and "[帷幄记忆]" in seen["text"]
```

- [ ] **Step 7.2 实现**(api.py):

```python
_CONDENSE_CHARS = int(os.environ.get("CONSOLE_CONDENSE_CHARS", "24000"))
_CONDENSE_MSGS = int(os.environ.get("CONSOLE_CONDENSE_MSGS", "36"))


def _memory_block() -> str:
    from guanlan_v2.console.tools import _MEMORY_PATH
    try:
        body = _MEMORY_PATH.read_text(encoding="utf-8") if _MEMORY_PATH.exists() else ""
    except Exception:
        body = ""
    body = body.strip()[-2000:]
    return f"[帷幄记忆]\n{body}\n\n" if body else ""
```

`_run_turn` 的 try 内、`agent = _agent_for(sid)` 之后:

```python
            # condenser:对话史超阈值 → 复用引擎 compact(LLM 摘要替换 messages),全量 jsonl 不丢
            msgs = getattr(agent, "messages", []) or []
            if (len(msgs) > _CONDENSE_MSGS or
                    sum(len(str(getattr(m, "content", ""))) for m in msgs) > _CONDENSE_CHARS):
                try:
                    summary = await agent.compact()
                    _emit(sid, "condensation", summary=str(summary)[:2000])
                except Exception:
                    pass   # 压缩失败不阻塞本轮(下轮再试)
```

turn_text 组装改:`turn_text = _memory_block() + _plan_block(st.get_meta(sid)) + text`。

`_SYSTEM_PROMPT` 工具清单补:`深度研报 ww_report_run(后台5-8分钟,需确认)、调界面 ww_show_page、沉淀经验卡 ww_cards_save(需确认)、长期记忆 ww_memory_write/read`;纪律加:`6. 用户的稳定偏好(池子/频率/风格)用 ww_memory_write 记下来;开新话题先想想记忆里有没有相关偏好。7. 用户说『调出/打开/看看XX界面』→ ww_show_page。`

- [ ] **Step 7.3** 跑测试 + 全量 ≥132 绿;重启 9999。

---

### Task 8: 导航收敛 + cards/graph 嵌入卫生(D 批前端)

**Files:** Modify `ui/_shared/guanlan-nav.js`、`ui/cards/validation.jsx`、`ui/cards/观澜 · 经验验证区.html`、`ui/graph/graph.jsx`、`ui/graph/观澜 · 研究图谱.html`

- [ ] **Step 8.1 nav 收敛**(guanlan-nav.js:6-14 的 MODULES 整体替换;用户拍板「顶栏只存在帷幄和落子」;图谱/对话·研报/经验卡/工作流/选股直链与代码保留——隐藏不删):

```js
  var MODULES = [
    { label: '帷幄', file: '../console/观澜 · 帷幄.html', home: true },
    { label: '席位 · 落子', file: '../seats/观澜 · 落子.html' },
  ];
```

- [ ] **Step 8.2 cards 页**(validation.jsx;行号经读码报告核实):
  - 顶部加 `WW_EMBED/WW_LEGACY` 两 const(同选股页模式);
  - `:560` `<Header kbCount={…} pending={…} />` 包 `{!WW_EMBED && …}`,`:559` 外层 grid `gridTemplateRows: '52px 1fr'` 改 `gridTemplateRows: WW_EMBED ? '1fr' : '52px 1fr'`;
  - `:708` `<ChatRefine chat={chat} thinking={thinking} onSend={onSend} />` 包 `{WW_LEGACY && …}`(§3.7:refine LLM 入口全局隐藏,?legacy=1 找回);
  - html `validation.jsx?v=20260610e` → `?v=20260613a`。

- [ ] **Step 8.3 graph 页**(graph.jsx:38-44 顶栏身份区):
  - 顶部加 `WW_EMBED` const(graph 无 LLM 入口,不需要 LEGACY);
  - `:38-44` 整个顶栏 div 包 `{!WW_EMBED && ( … )}`(整行是身份+计数,无功能按钮);
  - html `graph.jsx?v=3` → `?v=4`。

- [ ] **Step 8.4 检查点**(controller 浏览器):① 任意页强刷:导航只剩「帷幄|席位·落子」;② `经验验证区.html?embed=1`:无 nav、无 Header、refine 没了;`?legacy=1` refine 回来;③ `研究图谱.html?embed=1`:无 nav、无顶栏;④ 五个隐藏页直链照常可用。

---

### Task 9: 左栏后台任务分区 + 右栏拖宽(C 批)

**Files:** Modify `ui/console/console-rail.jsx`、`ui/console/console-app.jsx`、`ui/console/console-bench.jsx`

- [ ] **Step 9.1 rail 后台任务**(「任务计划」节后插;数据 = `state.bgTasks`):

```jsx
      {Object.keys(state.bgTasks || {}).length > 0 && <h3 style={WW_RAIL_H3}>后台任务</h3>}
      {Object.entries(state.bgTasks || {}).map(([id, t]) => (
        <div key={id} style={{ margin: '0 10px 6px', padding: '8px 10px', border: '1px solid var(--line-soft)', background: 'var(--paper-2)', fontSize: 12, opacity: t.status === 'done' ? 0.65 : 1 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 7, color: 'var(--ink-1)' }}>
            <span style={{ width: 6, height: 6, borderRadius: '50%', background: t.status === 'running' ? 'var(--zhu)' : t.status === 'error' ? 'var(--yin)' : 'var(--dai)', animation: t.status === 'running' ? 'pulse 1.4s infinite' : 'none' }} />
            <span>深度研报 · {t.code}</span>
          </div>
          <div style={{ marginTop: 3, fontSize: 10.5, color: t.status === 'error' ? 'var(--yin)' : 'var(--ink-3)', paddingLeft: 13 }}>{t.note}</div>
          {t.status === 'running' && t.progress != null && (
            <div style={{ margin: '6px 0 1px 13px', height: 3, background: 'var(--line-soft)', position: 'relative' }}>
              <div style={{ position: 'absolute', left: 0, top: 0, bottom: 0, width: (t.progress * 100) + '%', background: 'var(--jin)' }} />
            </div>
          )}
        </div>
      ))}
```

- [ ] **Step 9.2 拖宽**(console-app.jsx + console-bench.jsx):
  - app:`const [benchW, setBenchW] = React.useState(() => { try { return parseInt(localStorage.getItem('guanlan:ww:benchw')) || 0; } catch (e) { return 0; } });` cols 计算:bench 开且非 chatWide 时 `benchW ? ('264px 1fr ' + Math.max(480, Math.min(benchW, window.innerWidth - 700)) + 'px') : '264px 460px 1fr'`(拖过=右栏定宽,没拖过=工作台优先原状);把 `setBenchW` 经 props 传给 WwBench;
  - bench:tabs 行前加左缘把手 `<div onPointerDown={startDrag} style={{ position: 'absolute', left: -4, top: 0, bottom: 0, width: 8, cursor: 'col-resize', zIndex: 5 }} />`(bench 根 div 加 `position:'relative'`);`startDrag` 起 window pointermove/pointerup 监听:move 时 `setBenchW(Math.round(window.innerWidth - e.clientX))`,up 时写 localStorage 并卸监听。

- [ ] **Step 9.3 检查点**:回放含后台任务的会话(Task 6 测试 tmp 不在生产 var——用 Task 10 真机研报看)+ 拖宽生效且刷新记忆。

---

### Task 10: 真机端到端 + 文档收口

- [ ] **Step 10.1** 全量 pytest ≥132 绿;重启 9999。
- [ ] **Step 10.2 浏览器全链(controller 亲验;SSE 页截图前先 window.stop())**:
  1. 新观感:发「分析 rank(-delta(close,20)) 再选 csi300 top5」→ 用户黑卡/工具链折叠组/markdown 回答/右栏滑出;
  2. 「把经验卡界面调出来」→ 右栏经验卡页(无页头无 refine);「调出图谱」同理;
  3. 「给 SZ300750 写份深度研报」→ 确认模态 Y → 左栏进度条爬升(期间再发一条普通指令证明不阻塞)→ 完成 ReportCard+抽屉自动滑开全文 → `var/archive/rs_report_SZ300750_*.json` 出现;
  4. 「记住:我只看 csi300」→ 新会话发「选股」→ agent 体现偏好;
  5. 临时 `CONSOLE_CONDENSE_MSGS=4` 重启触发 condensation 分隔线(验完恢复);
  6. 导航全站只剩两项;五个隐藏页直链可用。
- [ ] **Step 10.3 文档**:`ui/console/README.md` 大更新(新事件字段/五新工具/抽屉/推导层);`ui/chat/README.md` 顶部加「**2026-06-13 退役注记**:本页已摘出导航,研报能力并入帷幄(spec §2 第五轮);直链可用,代码保留」;`ui/cards/README.md`、`ui/graph/README.md`(若无则建)注记 embed 卫生;`ui/_shared/README.md` 注记 nav 收敛;spec 状态行改「二期已实现(2026-06-13)」;memory(weiwo-console-phase1.md 追加二期节)。

---

## Self-Review 结论(已自查)

- **Spec 覆盖**:U=T1-T4、A=T5(report_run)+T6(跑道/进度/入档)+T10.3(chat 退役注记)、D=T2.2(WW_PAGES)+T5(show_page/cards_save)+T8(nav/卫生)、B=T7、C=T9;C3 模型切换按 spec「可选」裁定**不做**。
- **占位扫描**:移植类步骤以「源行号+逐字复制+列明适配」表达(源码在仓内,行号经 2026-06-12 读码报告核实);无 TBD。
- **类型一致**:`se.background` T5(_wrap 透传)↔T6(api 分支);`task_update.kind/progress` T6(产生)↔T2.3(折叠,且 busy 翻转对 kind 事件跳过)↔T9.1(渲染);artifact `report_md{path,code,name}` T6↔T2.4↔T4;`_MEMORY_PATH` T5(定义)↔T7(_memory_block);`merge_meta` T6.1(store)↔T6.3(api 调用)。
- **已知风险注记**:① 同 code 并发研报互踩——_bg_inflight 去重已防;② glob 取最新 md 在传旧 asof 时可能取错(引擎已知弱点,沿袭);③ 9999 重启时跑一半的研报线程死亡,左栏停在 running(事件不撒谎,无 done)——三期补启动扫描 meta.bg 标中断;④ progress json 跨次运行残留——轮询只在子进程在跑时进行,且按变化才发事件,残留值最多造成首拍进度偏高,不误报完成。
