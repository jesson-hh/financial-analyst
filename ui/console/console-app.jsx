// console-app.jsx — 帷幄主壳。布局:无产物/✕收起 → [264px 1fr];有产物 → [264px 460px 1fr](工作台优先);
// ⇋ 对话优先 → [264px 1fr 560px]。新产物到来自动重新滑出(wwApply 置 benchClosed=false)。

// 盯盘 agent 模板默认(clock/glyph/color 与 ui/seats/luozi-data.jsx LZ_TEMPLATES 对齐,钉源防漂移)。
const SEATBIND_TPL = {
  momentum: { glyph: '动', color: 'var(--jin)', clock: { execTF: 'day', decisionFreq: 'hourly', maxHold: 30, stopLoss: 0.08, takeProfit: 0.18 } },
  reversal: { glyph: '反', color: 'var(--zhu)', clock: { execTF: 'day', decisionFreq: 'daily', maxHold: 13, stopLoss: 0.05, takeProfit: 0.11 } },
  event: { glyph: '事', color: '#3f6f8a', clock: { execTF: 'day', decisionFreq: 'daily', maxHold: 22, stopLoss: 0.09, takeProfit: 0.26 } },
};

const WW_THINKING_MODE_KEY = 'guanlan:ww:thinking-mode:v1';

// 帷幄 seat_bind 信封落地。新控制面由后端原子创建 assignment + strategy，
// 前端只把后端返回的同 id strategy 镜像进 GL；旧 artifact 才走历史兼容创建逻辑。
function applySeatBind(payload, openPage) {
  try {
    const p = payload || {};
    const code = String(p.bareCode || p.code || '').replace(/^(SH|SZ|BJ)/i, '');
    const GL = window.GL;
    if (p.backendManaged === true) {
      const strategy = p.strategy && typeof p.strategy === 'object' ? p.strategy : null;
      if (strategy && strategy.id && GL && GL.put) {
        GL.put({ ...strategy, id: String(strategy.id), type: 'strategy' });
      }
    } else if (code && GL && GL.all && GL.put) {
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
    if (a.type === 'sid') return { ...WW.initState(), sid: a.sid,
      streamGeneration: Number(a.generation || 0) };
    // EventSource.close() 之后浏览器仍可能把已排队的旧回调送达。sid + generation
    // 双守卫让旧 snapshot/ev/onerror 无法污染新会话（同 sid 重连也安全）。
    if (a.sid && s.sid && a.sid !== s.sid) return s;
    if (a.generation != null && Number(a.generation) !== Number(s.streamGeneration || 0)) return s;
    if (a.type === 'snapshot') {
      const meta = a.meta || null;
      let n = { ...WW.initState(), sid: s.sid, meta,
        plan: meta && Array.isArray(meta.plan) ? meta.plan : [],
        connected: true, control: s.control || null,
        streamGeneration: s.streamGeneration || 0 };
      (a.events || []).forEach(ev => { n = WW.apply(n, ev); });
      // 快照事件用于展示；runtime 是「现在是否在跑 / 是否待确认」的权威状态。
      // 必须在重放后覆盖，否则裁剪过的快照或旧 turn 终态会错误解锁输入区。
      const runtime = a.runtime;
      if (runtime && typeof runtime === 'object') {
        const running = !!runtime.running;
        const controlKind = String(runtime.control_kind || (n.control && n.control.kind) || '');
        const phaseRaw = String(runtime.phase || '').toLowerCase();
        const controlRaw = String(runtime.control_status || '').toLowerCase();
        let controlStatus = controlRaw === 'cancelling' ? 'stopping' : controlRaw;
        if (!controlStatus && runtime.cancel_requested) {
          controlStatus = controlKind === 'steer' ? 'steering' : 'stopping';
        }
        n.busy = running;
        n.activeTurnId = runtime.active_turn_id || null;
        if (!running) {
          n.control = null;
        } else if (controlStatus === 'stopping' || controlStatus === 'steering') {
          n.control = { ...(n.control || {}), kind: controlKind || undefined, status: controlStatus,
            raw_status: controlRaw || phaseRaw, source: 'snapshot_runtime' };
        } else if (runtime.continuation_from && n.control && n.control.kind === 'steer') {
          n.control = { ...n.control, status: 'continued', source: 'snapshot_runtime',
            note: n.control.note || '已按新方向继续' };
        } else if (n.control && (n.control.source === 'local' || n.control.kind === 'cancel'
            || ['queued', 'stopping', 'steering'].indexOf(n.control.status) >= 0)) {
          n.control = null;
        }

        const pending = runtime.pending_confirm;
        const pendingTurnId = runtime.pending_confirm_turn_id
          || (pending && pending.turn_id) || null;
        if (pending && typeof pending === 'object') {
          n.confirm = { ...pending, turn_id: pending.turn_id || pendingTurnId };
        } else if (pendingTurnId) {
          n.confirm = n.confirm && n.confirm.turn_id === pendingTurnId
            ? n.confirm
            : { type: 'confirm_request', turn_id: pendingTurnId,
              tool: '待确认工具', args: {}, restored: true };
        } else {
          n.confirm = null;
        }
        if (pendingTurnId) {
          n.busy = true;
          n.activeTurnId = runtime.active_turn_id || pendingTurnId;
        }
      } else if (s.control && ['queued', 'stopping', 'steering'].indexOf(s.control.status) >= 0) {
        // 兼容尚未提供 runtime 的后端：不让 HTTP 已接收、SSE running 尚未到达的窗口重新可发送。
        n.busy = s.busy;
        n.control = s.control;
        n.activeTurnId = s.activeTurnId || null;
      }
      return n;
    }
    if (a.type === 'ev') return WW.apply(s, a.ev);
    if (a.type === 'conn') return { ...s, connected: a.ok };
    if (a.type === 'benchClosed') return { ...s, benchClosed: a.v };
    if (a.type === 'control') return { ...s, control: a.control || null };
    if (a.type === 'controlFailure') {
      if (!s.control || s.control.source !== 'local'
          || s.control.requestToken !== a.requestToken) return s;
      return { ...s, control: { ...s.control, status: 'error', source: 'http',
        note: a.note || '操作失败，请重试' } };
    }
    if (a.type === 'optimisticTurn') return { ...s, busy: true,
      activeTurnId: a.turn_id || s.activeTurnId || null,
      control: { kind: 'send', status: 'queued', source: 'local',
        requestToken: a.requestToken,
        note: a.note || '指令已接收，正在启动…' } };
    if (a.type === 'sendFailed') {
      if (!s.control || s.control.source !== 'local'
          || s.control.requestToken !== a.requestToken) return s;
      return { ...s, busy: false, activeTurnId: null,
      control: { kind: 'send', status: 'error', source: 'http',
        note: a.note || '发送失败，请重试' } };
    }
    if (a.type === 'metaPatch') return { ...s, meta: { ...(s.meta || {}), ...a.fields } };  // 改名等即时回显,不等快照
    return s;
  }, WW.initState());
  const [sessions, setSessions] = React.useState([]);
  const [workspaces, setWorkspaces] = React.useState([WW.GENERAL_WORKSPACE]);
  const [workspacesStatus, setWorkspacesStatus] = React.useState({ ok: true, reason: '' });
  const [manual, setManual] = React.useState([]);        // 手动呼出的功能页:独立于事件流,SSE 快照重放不丢
  const [benchFocus, setBenchFocus] = React.useState(null); // {page,n} 手动呼出 → 工作台强制切到该 tab(覆盖钉住)
  const [chatWide, setChatWide] = React.useState(false);
  const [thinkingMode, setThinkingMode] = React.useState(() => {
    try { return localStorage.getItem(WW_THINKING_MODE_KEY) === 'pro' ? 'pro' : 'standard'; }
    catch (e) { return 'standard'; }
  });
  const [benchW, setBenchW] = React.useState(() => { try { return parseInt(localStorage.getItem('guanlan:ww:benchw')) || 0; } catch (e) { return 0; } });
  const [drawer, setDrawer] = React.useState(null);
  const [sentry, setSentry] = React.useState({ items: [], unread: 0 });  // 哨兵研判(平台级,跨会话)
  const [dockOpen, setDockOpen] = React.useState(() => window.innerWidth >= 1120);
  const [narrow, setNarrow] = React.useState(() => window.innerWidth < 1120);
  const [stage, setStage] = React.useState(null);          // {page,load?}; string 兼容旧调用
  const esRef = React.useRef(null);
  const streamGenRef = React.useRef(0);
  const streamSidRef = React.useRef(null);
  const requestSeqRef = React.useRef(0);

  const generalId = (WW.GENERAL_WORKSPACE && WW.GENERAL_WORKSPACE.id) || 'rw_general';
  const sessionById = (sid) => sessions.find(m => m.id === sid) || null;
  const activeSession = sessionById(state.sid) || state.meta || null;
  const activeSessionArchived = !!(activeSession && activeSession.archived);
  const activeWorkspaceId = ((state.meta && state.meta.workspace_id) || (sessionById(state.sid) && sessionById(state.sid).workspace_id) || generalId);
  const activeWorkspace = workspaces.find(w => w.id === activeWorkspaceId) || WW.GENERAL_WORKSPACE;
  const activeWorkspaceSessions = sessions.filter(m => !m.archived && (m.workspace_id || generalId) === activeWorkspaceId);
  const openStage = (target) => {
    const next = typeof target === 'string' ? { page: target } : { ...(target || {}) };
    if (next.page === 'seats') {
      const code = WW.canonicalSecurityCode(next.code || (activeWorkspace && activeWorkspace.code));
      const stageWid = next.workspaceId || activeWorkspaceId || generalId;
      if (code) {
        next.code = code;
        next.name = next.name || (activeWorkspace && (activeWorkspace.display_name || activeWorkspace.name)) || code;
        next.focus = next.focus || 'watch';
        // 先写信箱、再 mount iframe；Stage 内仍带稳定 code 深链，避免首帧竞态。
        if (window.GL && window.GL.handoff) window.GL.handoff('cockpit', {
          code, name: next.name, focus: next.focus, fromWorkspaceWatch: !next.global,
        }, stageWid);
      }
    }
    setStage(next);
  };
  const stageWorkspaceRef = React.useRef(activeWorkspaceId);
  React.useEffect(() => {
    if (stage && stageWorkspaceRef.current !== activeWorkspaceId) setStage(null);
    stageWorkspaceRef.current = activeWorkspaceId;
  }, [activeWorkspaceId, stage]);

  const openReport = async (item) => {
    const reportSid = streamSidRef.current;
    const reportGeneration = streamGenRef.current;
    setDrawer({ sym: { code: item.code, name: item.name }, status: 'running', text: '', path: item.path, startedAt: Date.now() });
    try {
      const r = await fetch(WW.API + '/report?path=' + encodeURIComponent(item.path)).then(x => x.json());
      if (!requestIsCurrent(reportSid, reportGeneration)) return;
      setDrawer(d => d && { ...d, status: 'done', text: r.ok ? r.text : ('⚠ 读取失败: ' + (r.reason || '')) });
    } catch (e) {
      if (requestIsCurrent(reportSid, reportGeneration)) {
        setDrawer(d => d && { ...d, status: 'done', text: '⚠ 读取失败: ' + e });
      }
    }
  };

  const dispatchLive = (a) => {
    if (Number(a.generation || 0) !== streamGenRef.current) return;
    if (a.sid && a.sid !== streamSidRef.current) return;
    dispatch(a);
    if (a.type === 'ev' && a.ev.type === 'tool_result' && a.ev.artifact && a.ev.artifact.kind === 'report_md') {
      const p = a.ev.artifact.payload;
      const eventSid = a.sid; const eventGeneration = Number(a.generation || 0);
      setTimeout(() => {
        if (requestIsCurrent(eventSid, eventGeneration)) {
          openReport({ path: p.path, code: p.code, name: p.name || p.code });
        }
      }, 0);
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

  // 全局哨兵可能属于任意股票，绝不能把它写进当前股票研究空间的 cockpit 信箱。
  // 统一进入 rw_general 的全宽落子 Stage；空间内投影由 Dock 自己按 workspace code 打开。
  const onSentryFocus = (it) => {
    if (!it || !it.code) return;
    openStage({ page: 'seats', code: it.code, name: it.name, focus: 'watch', workspaceId: generalId, global: true });
  };

  const refreshSessions = async () => {
    const rows = await WW.sessions();
    if (Array.isArray(rows)) setSessions(rows);
    return Array.isArray(rows) ? rows : [];
  };
  const refreshWorkspaces = () => WW.workspaces().then(d => {
    setWorkspacesStatus({ ok: !!d.ok, reason: d.reason || '' });
    if (Array.isArray(d.workspaces) && d.workspaces.length) setWorkspaces(d.workspaces);
  });
  const attach = (sid) => {
    if (esRef.current) esRef.current.close();
    const generation = ++streamGenRef.current;
    streamSidRef.current = sid;
    setManual([]); setBenchFocus(null);
    setDrawer(null);                                      // 切会话清研报抽屉,防上个会话的研报残留
    dispatch({ type: 'sid', sid, generation });
    esRef.current = WW.connect(sid, dispatchLive, generation);
    // sessionStorage=本 tab 会话(刷新回到本 tab 的会话);localStorage=新 tab 兜底
    try { sessionStorage.setItem('guanlan:ww:sid', sid); } catch (e) {}
    try { localStorage.setItem('guanlan:ww:sid', sid); } catch (e) {}
  };
  const requestIsCurrent = (sid, generation) => (
    streamSidRef.current === sid && streamGenRef.current === generation
  );

  React.useEffect(() => {
    if (!WW.API) return;
    refreshSessions(); refreshWorkspaces();
    const last = (() => { try { return sessionStorage.getItem('guanlan:ww:sid') || localStorage.getItem('guanlan:ww:sid'); } catch (e) { return null; } })();
    if (last) { attach(last); }
    else { WW.newSession(null, generalId).then(m => { if (m && m.id) attach(m.id); }); }
    return () => { if (esRef.current) esRef.current.close(); };
  }, []);

  const onSend = async (text) => {
    if (activeSessionArchived) {
      return { ok: false, reason: '该对话已归档；历史记录仅供查看，请先恢复' };
    }
    if (activeWorkspace && activeWorkspace.archived) {
      return { ok: false, reason: '该研究空间已归档；历史消息仅供查看' };
    }
    const requestSid = state.sid;
    const requestGeneration = streamGenRef.current;
    const requestToken = requestSid + ':' + requestGeneration + ':' + (++requestSeqRef.current);
    if (state.busy) {
      dispatch({ type: 'control', control: {
        kind: 'steer', status: 'stopping', text, source: 'local', requestToken,
        note: '正在安全停止当前任务…',
      } });
      const steered = await WW.steer(requestSid, text, thinkingMode);
      if (!requestIsCurrent(requestSid, requestGeneration)) return steered;
      if (!steered || !steered.ok) {
        dispatch({ type: 'controlFailure', sid: requestSid, generation: requestGeneration,
          requestToken, note: (steered && (steered.reason || steered.message)) || '转向失败，请重试' });
      }
      return steered;
    }
    dispatch({ type: 'optimisticTurn', sid: requestSid, generation: requestGeneration, requestToken });
    let sendSid = requestSid;
    let sendGeneration = requestGeneration;
    let r = await WW.send(sendSid, text, thinkingMode);
    if (!requestIsCurrent(sendSid, sendGeneration)) return r;
    if (!r.ok && r.reason && r.reason.indexOf('会话不存在') >= 0) {
      const m = await WW.newSession(text.slice(0, 18), activeWorkspaceId);
      if (!requestIsCurrent(sendSid, sendGeneration)) return r;
      if (m && m.id) {
        attach(m.id);
        sendSid = m.id;
        sendGeneration = streamGenRef.current;
        dispatch({ type: 'optimisticTurn', sid: sendSid, generation: sendGeneration, requestToken });
        r = await WW.send(sendSid, text, thinkingMode);
        if (!requestIsCurrent(sendSid, sendGeneration)) return r;
      }
    }
    if (!r || !r.ok) {
      dispatch({ type: 'sendFailed', sid: sendSid, generation: sendGeneration, requestToken,
        note: (r && (r.reason || r.message)) || '发送失败，请重试' });
    }
    if (requestIsCurrent(sendSid, sendGeneration)) refreshSessions();
    return r;
  };
  const onCancel = async () => {
    if (activeWorkspace && activeWorkspace.archived) {
      return { ok: false, reason: '归档空间不能执行停止操作' };
    }
    if (!state.busy) return { ok: true, message: '当前没有运行中的前台任务' };
    const requestSid = state.sid;
    const requestGeneration = streamGenRef.current;
    const requestToken = requestSid + ':' + requestGeneration + ':' + (++requestSeqRef.current);
    dispatch({ type: 'control', control: {
      kind: 'cancel', status: 'stopping', source: 'local', requestToken,
      note: '正在安全停止当前任务…',
    } });
    const cancelled = await WW.cancel(requestSid);
    if (!requestIsCurrent(requestSid, requestGeneration)) return cancelled;
    if (!cancelled || !cancelled.ok) {
      dispatch({ type: 'controlFailure', sid: requestSid, generation: requestGeneration,
        requestToken, note: (cancelled && (cancelled.reason || cancelled.message)) || '停止失败，请重试' });
    }
    return cancelled;
  };
  const onThinkingMode = (mode) => {
    const next = mode === 'pro' ? 'pro' : 'standard';
    setThinkingMode(next);
    try { localStorage.setItem(WW_THINKING_MODE_KEY, next); } catch (e) {}
  };
  const onNew = async (workspaceId) => { const m = await WW.newSession(null, workspaceId || activeWorkspaceId || generalId); if (m && m.id) attach(m.id); refreshSessions(); };
  const onNewWorkspace = async (fields) => {
    const d = await WW.newWorkspace(fields);
    if (d && d.ok && d.workspace) {
      await refreshWorkspaces();
      const m = await WW.newSession(null, d.workspace.id);
      if (m && m.id) attach(m.id);
      refreshSessions();
    }
    return d;
  };
  const onWorkspaceChanged = (workspace) => {
    if (!workspace) return;
    setWorkspaces(rows => rows.map(w => w.id === workspace.id ? workspace : w));
  };
  const onUpdateSession = async (sid, fields) => {
    const generation = streamGenRef.current;
    const d = await WW.updateSession(sid, fields);
    if (!requestIsCurrent(sid, generation)) return d;
    if (d && d.ok !== false) dispatch({ type: 'metaPatch', sid, generation, fields });   // 当前会话 → 顶部会话栏立即换名
    refreshSessions();
    return d;
  };
  const onArchiveSession = async (sid) => {
    const d = await WW.archiveSession(sid);
    if (!d || d.ok === false) return d;
    const rows = await refreshSessions();
    if (streamSidRef.current === sid) {
      const old = rows.find(m => m.id === sid) || sessionById(sid) || {};
      const wid = old.workspace_id || generalId;
      let next = rows.find(m => !m.archived && m.id !== sid && (m.workspace_id || generalId) === wid)
        || rows.find(m => !m.archived && m.id !== sid);
      if (!next) {
        const workspace = workspaces.find(w => w.id === wid);
        next = await WW.newSession(null, workspace && !workspace.archived ? wid : generalId);
        await refreshSessions();
      }
      if (next && next.id) attach(next.id);
    }
    return d;
  };
  const onRestoreSession = async (sid) => {
    const d = await WW.restoreSession(sid);
    if (!d || d.ok === false) return d;
    await refreshSessions();
    if (streamSidRef.current === sid) dispatch({ type: 'metaPatch', sid, generation: streamGenRef.current, fields: { archived: false } });
    return d;
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
    const iv = setInterval(() => { refreshSessions(); refreshWorkspaces(); pullSentry(); }, 8000);
    return () => clearInterval(iv);
  }, []);

  React.useEffect(() => {
    const resize = () => { const next = window.innerWidth < 1120; setNarrow(next); if (next) setDockOpen(false); };
    window.addEventListener('resize', resize); return () => window.removeEventListener('resize', resize);
  }, []);

  if (!WW.API) return <div style={{ padding: 40, fontFamily: 'var(--serif)', color: 'var(--ink-2)' }}>帷幄需经 9999 服务打开(SSE 与工具都在后端):http://127.0.0.1:9999/ui/console/观澜 · 帷幄.html</div>;

  const activated = state.activated.concat(manual.filter(p => state.activated.indexOf(p) < 0)); // 事件流激活 ∪ 手动呼出
  const benchOpen = activated.length > 0 && !state.benchClosed;
  const stageWorkspaceId = stage && (stage.workspaceId || activeWorkspaceId);
  const stageWorkspace = stageWorkspaceId
    ? (workspaces.find(w => w.id === stageWorkspaceId)
      || (stageWorkspaceId === activeWorkspaceId ? activeWorkspace : WW.GENERAL_WORKSPACE)) : null;
  const stageReadOnly = !!(stage && (stage.readOnly || (stageWorkspace && stageWorkspace.archived)));
  const railW = narrow ? 236 : 264;
  const dockCol = (!narrow && dockOpen && !stage) ? ' 340px' : '';
  const cols = stage ? (railW + 'px 1fr')
    : !benchOpen ? (railW + 'px 1fr' + dockCol)
    : chatWide ? (railW + 'px 1fr 560px' + dockCol)
    : (railW + 'px 460px 1fr' + dockCol);

  // 二级 masthead 已裁(与顶部 nav 重复):⇋ 布局切换移入工作台头部,运筹中状态并入左栏脚注
  return (
    <div style={{ display: 'grid', gridTemplateColumns: cols, height: '100vh', minWidth: 0, position: 'relative' }}>
      <WwRail state={state} sessions={sessions} workspaces={workspaces} workspacesStatus={workspacesStatus} activeWorkspaceId={activeWorkspaceId} onNew={onNew} onNewWorkspace={onNewWorkspace} onChooseFolder={WW.chooseResearchFolder} onSwitch={attach} onUpdate={onUpdateSession} onArchive={onArchiveSession} onRestore={onRestoreSession} />
      {stage ? <WwWorkspaceStage page={stage.page} load={stage.load} asset={stage.asset} code={stage.code} name={stage.name} focus={stage.focus} readOnly={stageReadOnly} workspaceId={stage.workspaceId || activeWorkspaceId} onBack={() => setStage(null)} /> : <React.Fragment>
      <WwThread key={state.sid || 'no-session'} state={state} workspace={activeWorkspace} sessionArchived={activeSessionArchived} onSend={onSend} onCancel={onCancel} onConfirm={(c) => WW.confirm(state.confirm.turn_id, c)} onOpenReport={openReport} onOpenPage={openPage} activatedPages={activated} onRename={activeSessionArchived ? null : (t) => onUpdateSession(state.sid, { title: t })} sentry={sentry} markSentrySeen={markSentrySeen} onSentryFocus={onSentryFocus} thinkingMode={thinkingMode} onThinkingMode={onThinkingMode} />
      {benchOpen && <WwBench key={activeWorkspaceId} state={{ ...state, activated }} workspaceId={activeWorkspaceId} focus={benchFocus} chatWide={chatWide} onToggleWide={() => setChatWide(w => !w)} onClose={() => dispatch({ type: 'benchClosed', v: true })} onOpenStage={openStage} onResize={(w) => { setBenchW(w); try { localStorage.setItem('guanlan:ww:benchw', String(w)); } catch (e) {} }} />}
      <WwWorkspaceDock workspace={activeWorkspace} sessions={activeWorkspaceSessions} open={dockOpen} narrow={narrow} onToggle={() => setDockOpen(true)} onClose={() => setDockOpen(false)} onOpenStage={openStage} onWorkspaceChanged={onWorkspaceChanged} />
      </React.Fragment>}
      {drawer && <WwDrawer drawer={drawer} onClose={() => setDrawer(null)} />}
    </div>
  );
}
window.WeiwoApp = WeiwoApp;
