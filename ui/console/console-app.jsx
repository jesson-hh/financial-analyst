// console-app.jsx — 帷幄主壳。布局:无产物/✕收起 → [264px 1fr];有产物 → [264px 460px 1fr](工作台优先);
// ⇋ 对话优先 → [264px 1fr 560px]。新产物到来自动重新滑出(wwApply 置 benchClosed=false)。

// 盯盘 agent 模板默认(clock/glyph/color 与 ui/seats/luozi-data.jsx LZ_TEMPLATES 对齐,钉源防漂移)。
const SEATBIND_TPL = {
  momentum: { glyph: '动', color: 'var(--jin)', clock: { execTF: 'day', decisionFreq: 'hourly', maxHold: 30, stopLoss: 0.08, takeProfit: 0.18 } },
  reversal: { glyph: '反', color: 'var(--zhu)', clock: { execTF: 'day', decisionFreq: 'daily', maxHold: 13, stopLoss: 0.05, takeProfit: 0.11 } },
  event: { glyph: '事', color: '#3f6f8a', clock: { execTF: 'day', decisionFreq: 'daily', maxHold: 22, stopLoss: 0.09, takeProfit: 0.26 } },
};

// 帷幄 seat_bind 信封落地:在共享 window.GL 写一个 type:'strategy' 实体(bind=[该票]=盯盘);
// localStorage storage 事件实时同步到校场 iframe(luozi-app GL.on(refresh))重渲染出 owning agent。
// 去重守卫:已有策略 bind 含该票则不重复建。末了把校场调出右栏给用户看。
function applySeatBind(payload, openPage) {
  try {
    const p = payload || {};
    const code = String(p.bareCode || p.code || '').replace(/^(SH|SZ|BJ)/i, '');
    const GL = window.GL;
    if (code && GL && GL.all && GL.put) {
      const already = GL.all('strategy').some(function (s) {
        return Array.isArray(s.bind) && s.bind.some(function (c) {
          return String(c).replace(/^(SH|SZ|BJ)/i, '') === code;
        });
      });
      if (!already) {
        const tpl = ['momentum', 'reversal', 'event'].indexOf(p.template) >= 0 ? p.template : 'momentum';
        const t = SEATBIND_TPL[tpl];
        GL.put({
          id: 'strat_' + Date.now().toString(36) + Math.floor(Math.random() * 1e4).toString(36),
          type: 'strategy', name: (p.name || code) + ' · 盯盘',
          template: tpl, bind: [code], creed: p.creed || '', refs: [],
          clock: t.clock, w: 0, pa: false, glyph: t.glyph, color: t.color,
        });
      }
    }
  } catch (e) { /* 落地失败不崩,仍调出校场让用户手动核 */ }
  if (openPage) openPage('seats');
}

// 任务芯片/面板已并入对话区顶部会话栏 WwSessBar(console-thread.jsx)——每个对话各一条,随会话切换。
function WeiwoApp() {
  const WW = window.WW;
  const [state, dispatch] = React.useReducer((s, a) => {
    if (a.type === 'snapshot') { let n = { ...WW.initState(), sid: s.sid, meta: a.meta, connected: true }; (a.events || []).forEach(ev => { n = WW.apply(n, ev); }); return n; }
    if (a.type === 'ev') return WW.apply(s, a.ev);
    if (a.type === 'conn') return { ...s, connected: a.ok };
    if (a.type === 'sid') return { ...WW.initState(), sid: a.sid };
    if (a.type === 'benchClosed') return { ...s, benchClosed: a.v };
    if (a.type === 'metaPatch') return { ...s, meta: { ...(s.meta || {}), ...a.fields } };  // 改名等即时回显,不等快照
    return s;
  }, WW.initState());
  const [sessions, setSessions] = React.useState([]);
  const [manual, setManual] = React.useState([]);        // 手动呼出的功能页:独立于事件流,SSE 快照重放不丢
  const [benchFocus, setBenchFocus] = React.useState(null); // {page,n} 手动呼出 → 工作台强制切到该 tab(覆盖钉住)
  const [chatWide, setChatWide] = React.useState(false);
  const [benchW, setBenchW] = React.useState(() => { try { return parseInt(localStorage.getItem('guanlan:ww:benchw')) || 0; } catch (e) { return 0; } });
  const [drawer, setDrawer] = React.useState(null);
  const [sentry, setSentry] = React.useState({ items: [], unread: 0 });  // 哨兵研判(平台级,跨会话)
  const esRef = React.useRef(null);

  const openReport = async (item) => {
    setDrawer({ sym: { code: item.code, name: item.name }, status: 'running', text: '', path: item.path, startedAt: Date.now() });
    try {
      const r = await fetch(WW.API + '/report?path=' + encodeURIComponent(item.path)).then(x => x.json());
      setDrawer(d => d && { ...d, status: 'done', text: r.ok ? r.text : ('⚠ 读取失败: ' + (r.reason || '')) });
    } catch (e) { setDrawer(d => d && { ...d, status: 'done', text: '⚠ 读取失败: ' + e }); }
  };

  const dispatchLive = (a) => {
    dispatch(a);
    if (a.type === 'ev' && a.ev.type === 'tool_result' && a.ev.artifact && a.ev.artifact.kind === 'report_md') {
      const p = a.ev.artifact.payload;
      setTimeout(() => openReport({ path: p.path, code: p.code, name: p.name || p.code }), 0);
    }
    if (a.type === 'ev' && a.ev.type === 'tool_result' && a.ev.artifact && a.ev.artifact.kind === 'seat_bind') {
      applySeatBind(a.ev.artifact.payload || {}, openPage);
    }
  };

  const openPage = (p) => {                               // 呼出器/对话均可手动调出功能页
    setManual(m => (m.indexOf(p) >= 0 ? m : m.concat([p])));
    setBenchFocus({ page: p, n: Date.now() });
    dispatch({ type: 'benchClosed', v: false });
  };

  // 哨兵条目点对点聚焦:先把 {code,name} 交棒进本会话 cockpit 信箱,再呼出落子页——
  // 落子页 iframe 加载时 take('cockpit', ws) 即吃到该票入池+聚焦(S1 新分支)。
  // 边界:seats iframe 已挂载时不强制 reload(避免打断盘面),本次 handoff 待下次重载
  // (首开/切会话/新产物驱动)生效;最常见的首开场景点对点聚焦成立。
  const onSentryFocus = (it) => {
    if (it && it.code && window.GL && window.GL.handoff) window.GL.handoff('cockpit', { code: it.code, name: it.name }, state.sid);
    openPage('seats');
  };

  const refreshSessions = () => WW.sessions().then(setSessions);
  const attach = (sid) => {
    if (esRef.current) esRef.current.close();
    setManual([]); setBenchFocus(null);
    setDrawer(null);                                      // 切会话清研报抽屉,防上个会话的研报残留
    dispatch({ type: 'sid', sid });
    esRef.current = WW.connect(sid, dispatchLive);
    // sessionStorage=本 tab 会话(刷新回到本 tab 的会话);localStorage=新 tab 兜底
    try { sessionStorage.setItem('guanlan:ww:sid', sid); } catch (e) {}
    try { localStorage.setItem('guanlan:ww:sid', sid); } catch (e) {}
  };

  React.useEffect(() => {
    if (!WW.API) return;
    refreshSessions();
    const last = (() => { try { return sessionStorage.getItem('guanlan:ww:sid') || localStorage.getItem('guanlan:ww:sid'); } catch (e) { return null; } })();
    if (last) { attach(last); }
    else { WW.newSession().then(m => attach(m.id)); }
    return () => { if (esRef.current) esRef.current.close(); };
  }, []);

  const onSend = async (text) => {
    const r = await WW.send(state.sid, text);
    if (!r.ok && r.reason && r.reason.indexOf('会话不存在') >= 0) {
      const m = await WW.newSession(text.slice(0, 18)); attach(m.id); await WW.send(m.id, text);
    }
    refreshSessions();
  };
  const onNew = async () => { const m = await WW.newSession(); attach(m.id); refreshSessions(); };
  const onUpdateSession = async (sid, fields) => {
    await WW.updateSession(sid, fields);
    if (sid === state.sid) dispatch({ type: 'metaPatch', fields });   // 当前会话 → 顶部会话栏立即换名
    refreshSessions();
  };

  // 会话 running 态是进程内实时值,轻轮询保鲜(小 JSON,8s 一次);哨兵研判(平台级)同节拍拉取。
  // 已读水位 = localStorage 存最新已读 ts(ISO 秒,字符串比较即时间序);unread = 比水位新的条数。
  const pullSentry = () => WW.seatsDecisions(20).then(items => {
    const seen = (() => { try { return localStorage.getItem('guanlan:ww:sentryseen') || ''; } catch (e) { return ''; } })();
    setSentry({ items, unread: items.filter(it => String(it.ts || '') > seen).length });
  });
  const markSentrySeen = () => setSentry(s => {
    const top = s.items.length ? String(s.items[0].ts || '') : '';
    if (top) { try { localStorage.setItem('guanlan:ww:sentryseen', top); } catch (e) {} }
    return { ...s, unread: 0 };
  });
  React.useEffect(() => {
    if (!WW.API) return;
    pullSentry();
    const iv = setInterval(() => { refreshSessions(); pullSentry(); }, 8000);
    return () => clearInterval(iv);
  }, []);

  if (!WW.API) return <div style={{ padding: 40, fontFamily: 'var(--serif)', color: 'var(--ink-2)' }}>帷幄需经 9999 服务打开(SSE 与工具都在后端):http://127.0.0.1:9999/ui/console/观澜 · 帷幄.html</div>;

  const activated = state.activated.concat(manual.filter(p => state.activated.indexOf(p) < 0)); // 事件流激活 ∪ 手动呼出
  const benchOpen = activated.length > 0 && !state.benchClosed;
  const cols = !benchOpen ? '264px 1fr'
    : chatWide ? '264px 1fr 560px'
    : (benchW ? ('264px 1fr ' + Math.max(480, Math.min(benchW, window.innerWidth - 700)) + 'px') : '264px 460px 1fr');

  // 二级 masthead 已裁(与顶部 nav 重复):⇋ 布局切换移入工作台头部,运筹中状态并入左栏脚注
  return (
    <div style={{ display: 'grid', gridTemplateColumns: cols, height: '100vh', minWidth: 1280 }}>
      <WwRail state={state} sessions={sessions} onNew={onNew} onSwitch={attach} onUpdate={onUpdateSession} />
      <WwThread state={state} onSend={onSend} onConfirm={(c) => WW.confirm(state.confirm.turn_id, c)} onOpenReport={openReport} onOpenPage={openPage} activatedPages={activated} onRename={(t) => onUpdateSession(state.sid, { title: t })} sentry={sentry} markSentrySeen={markSentrySeen} onSentryFocus={onSentryFocus} />
      {benchOpen && <WwBench state={{ ...state, activated }} focus={benchFocus} chatWide={chatWide} onToggleWide={() => setChatWide(w => !w)} onClose={() => dispatch({ type: 'benchClosed', v: true })} onResize={(w) => { setBenchW(w); try { localStorage.setItem('guanlan:ww:benchw', String(w)); } catch (e) {} }} />}
      {drawer && <WwDrawer drawer={drawer} onClose={() => setDrawer(null)} />}
    </div>
  );
}
window.WeiwoApp = WeiwoApp;
