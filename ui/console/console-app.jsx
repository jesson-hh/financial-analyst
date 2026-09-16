// console-app.jsx — 帷幄主壳。运行版固定「空间树 + 对话」主结构，研究空间 Dock 按需停靠；
// 选股/因子研究通过全宽 Stage 打开，工具过程和结果继续留在对话流中。

// 盯盘 agent 模板默认(clock/glyph/color 与 ui/seats/luozi-data.jsx LZ_TEMPLATES 对齐,钉源防漂移)。
const SEATBIND_TPL = {
  momentum: { glyph: '动', color: 'var(--jin)', clock: { execTF: 'day', decisionFreq: 'hourly', maxHold: 30, stopLoss: 0.08, takeProfit: 0.18 } },
  reversal: { glyph: '反', color: 'var(--zhu)', clock: { execTF: 'day', decisionFreq: 'daily', maxHold: 13, stopLoss: 0.05, takeProfit: 0.11 } },
  event: { glyph: '事', color: '#3f6f8a', clock: { execTF: 'day', decisionFreq: 'daily', maxHold: 22, stopLoss: 0.09, takeProfit: 0.26 } },
};

const WW_THINKING_MODE_KEY = 'guanlan:ww:thinking-mode:v1';
const WW_SEAT_BIND_RECEIPT_PREFIX = 'guanlan:ww:seat-bind-applied:v1:';

// 帷幄 seat_bind 信封落地。新控制面由后端创建 authoritative assignment + strategy，
// 前端只把后端返回的同 id strategy 镜像进 GL；旧 artifact 才走历史兼容创建逻辑。
// 工具结果继续留在对话内，不自动打断并跳页。
function applySeatBind(payload) {
  try {
    const p = payload || {};
    const code = String(p.bareCode || p.code || '').replace(/^(SH|SZ|BJ)/i, '');
    const GL = window.GL;
    if (p.backendManaged === true) {
      const strategy = p.strategy && typeof p.strategy === 'object' ? p.strategy : null;
      if (strategy && strategy.id && GL && GL.put) {
        GL.put({ ...strategy, id: String(strategy.id), type: 'strategy' });
        return true;
      }
      return false;
    }
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
      return true;
    }
  } catch (e) { /* 落地失败不崩,对话内工具结果仍会如实显示 */ }
  return false;
}

// seat_bind 是需确认的持久副作用。若完成时断线，下次 snapshot 也要补做一次；
// 本地 receipt 避免用户后来手动删除策略后又被旧事件复活。
function applySeatBindEvent(sid, ev) {
  if (!ev || ev.type !== 'tool_result' || !ev.artifact || ev.artifact.kind !== 'seat_bind') return;
  const receipt = WW_SEAT_BIND_RECEIPT_PREFIX + String(sid || '') + ':' + String(ev.id || ev.ts || 'unknown');
  try { if (localStorage.getItem(receipt) === '1') return; } catch (e) {}
  if (applySeatBind(ev.artifact.payload || {})) {
    try { localStorage.setItem(receipt, '1'); } catch (e) {}
  }
}

// 任务芯片/面板已并入对话区顶部会话栏 WwSessBar(console-thread.jsx)——每个对话各一条,随会话切换。
function WeiwoApp() {
  const WW = window.WW;
  const [state, dispatch] = React.useReducer((s, a) => {
    if (a.type === 'sid') return { ...WW.initState(), sid: a.sid, streamGeneration: Number(a.generation || 0) };
    if (a.sid && s.sid && a.sid !== s.sid) return s;
    if (a.generation != null && Number(a.generation) !== Number(s.streamGeneration || 0)) return s;
    if (a.type === 'snapshot') {
      const meta = a.meta || null;
      let n = { ...WW.initState(), sid: s.sid, meta,
        plan: meta && Array.isArray(meta.plan) ? meta.plan : [], connected: true,
        control: s.control || null, streamGeneration: s.streamGeneration || 0,
        evidence: Array.isArray(a.evidence) ? a.evidence : [] };
      (a.events || []).forEach(ev => { n = WW.apply(n, ev); });
      const runtime = a.runtime;
      if (runtime && typeof runtime === 'object') {
        const running = !!runtime.running;
        const controlKind = String(runtime.control_kind || (n.control && n.control.kind) || '');
        const phaseRaw = String(runtime.phase || '').toLowerCase();
        const controlRaw = String(runtime.control_status || '').toLowerCase();
        let controlStatus = controlRaw === 'cancelling' ? 'stopping' : controlRaw;
        if (!controlStatus && runtime.cancel_requested) controlStatus = controlKind === 'steer' ? 'steering' : 'stopping';
        n.busy = running;
        n.activeTurnId = runtime.active_turn_id || null;
        if (!running) n.control = null;
        else if (controlStatus === 'stopping' || controlStatus === 'steering') {
          n.control = { ...(n.control || {}), kind: controlKind || undefined, status: controlStatus,
            raw_status: controlRaw || phaseRaw, source: 'snapshot_runtime' };
        } else if (runtime.continuation_from && n.control && n.control.kind === 'steer') {
          n.control = { ...n.control, status: 'continued', source: 'snapshot_runtime', note: n.control.note || '已按新方向继续' };
        } else if (n.control && (n.control.source === 'local' || n.control.kind === 'cancel'
            || ['queued', 'stopping', 'steering'].indexOf(n.control.status) >= 0)) n.control = null;
        const pending = runtime.pending_confirm;
        const pendingTurnId = runtime.pending_confirm_turn_id || (pending && pending.turn_id) || null;
        if (pending && typeof pending === 'object') n.confirm = { ...pending, turn_id: pending.turn_id || pendingTurnId };
        else if (pendingTurnId) n.confirm = n.confirm && n.confirm.turn_id === pendingTurnId
          ? n.confirm : { type: 'confirm_request', turn_id: pendingTurnId, tool: '待确认工具', args: {}, restored: true };
        else n.confirm = null;
        if (pendingTurnId) { n.busy = true; n.activeTurnId = runtime.active_turn_id || pendingTurnId; }
      } else if (s.control && ['queued', 'stopping', 'steering'].indexOf(s.control.status) >= 0) {
        n.busy = s.busy; n.control = s.control; n.activeTurnId = s.activeTurnId || null;
      }
      return n;
    }
    if (a.type === 'ev') return WW.apply(s, a.ev);
    if (a.type === 'conn') return { ...s, connected: a.ok };
    if (a.type === 'control') return { ...s, control: a.control || null };
    if (a.type === 'controlFailure') {
      if (!s.control || s.control.source !== 'local' || s.control.requestToken !== a.requestToken) return s;
      return { ...s, control: { ...s.control, status: 'error', source: 'http', note: a.note || '操作失败，请重试' } };
    }
    if (a.type === 'optimisticTurn') return { ...s, busy: true,
      activeTurnId: a.turn_id || s.activeTurnId || null,
      control: { kind: 'send', status: 'queued', source: 'local', requestToken: a.requestToken,
        note: a.note || '指令已接收，正在启动…' } };
    if (a.type === 'sendFailed') {
      if (!s.control || s.control.source !== 'local' || s.control.requestToken !== a.requestToken) return s;
      return { ...s, busy: false, activeTurnId: null,
        control: { kind: 'send', status: 'error', source: 'http', note: a.note || '发送失败，请重试' } };
    }
    if (a.type === 'metaPatch') return { ...s, meta: { ...(s.meta || {}), ...a.fields } };  // 改名等即时回显,不等快照
    return s;
  }, WW.initState());
  const [sessions, setSessions] = React.useState([]);
  const [workspaces, setWorkspaces] = React.useState([WW.GENERAL_WORKSPACE]);
  const [workspacesStatus, setWorkspacesStatus] = React.useState({ ok: true, reason: '' });
  const [thinkingMode, setThinkingMode] = React.useState(() => {
    try { return localStorage.getItem(WW_THINKING_MODE_KEY) === 'pro' ? 'pro' : 'standard'; }
    catch (e) { return 'standard'; }
  });
  const [drawer, setDrawer] = React.useState(null);
  const [channelBySid, setChannelBySid] = React.useState({});
  const [channelDrawerOpen, setChannelDrawerOpen] = React.useState(false);
  const [sentry, setSentry] = React.useState({ items: [], unread: 0 });  // 哨兵研判(平台级,跨会话)
  const [dockOpen, setDockOpen] = React.useState(() => window.innerWidth >= 1120);
  const [narrow, setNarrow] = React.useState(() => window.innerWidth < 1120);
  const [stage, setStage] = React.useState(null);          // {page,load?}; string 兼容旧调用
  const esRef = React.useRef(null);
  const streamGenRef = React.useRef(0);
  const streamSidRef = React.useRef(null);
  const requestSeqRef = React.useRef(0);
  const channelRequestSeqRef = React.useRef(0);
  const requestIsCurrent = (sid, generation) => streamSidRef.current === sid && streamGenRef.current === generation;

  const generalId = (WW.GENERAL_WORKSPACE && WW.GENERAL_WORKSPACE.id) || 'rw_general';
  const sessionById = (sid) => sessions.find(m => m.id === sid) || null;
  const activeSession = sessionById(state.sid) || state.meta || null;
  const activeSessionArchived = !!(activeSession && activeSession.archived);
  const activeWorkspaceId = ((state.meta && state.meta.workspace_id) || (sessionById(state.sid) && sessionById(state.sid).workspace_id) || generalId);
  const activeWorkspace = workspaces.find(w => w.id === activeWorkspaceId) || WW.GENERAL_WORKSPACE;
  const activeWorkspaceSessions = sessions.filter(m => !m.archived && (m.workspace_id || generalId) === activeWorkspaceId);
  const sessionChannel = activeSession && activeSession.channel_summary
    && typeof activeSession.channel_summary === 'object' ? activeSession.channel_summary : null;
  const activeChannel = channelBySid[state.sid] || sessionChannel || {
    available: false, session_id: state.sid || '', view_state: 'none', recent_deliveries: [],
  };
  const openStage = (target) => {
    setChannelDrawerOpen(false);
    const next = typeof target === 'string' ? { page: target } : { ...(target || {}) };
    next.sessionId = next.sessionId || state.sid;
    setStage(next);
  };
  const openWorkflow = (item) => {
    if (!item || item.workspaceId !== activeWorkspaceId || (item.sessionId && item.sessionId !== state.sid)) return;
    const importToken = 'wi_' + Date.now().toString(36) + Math.random().toString(36).slice(2);
    openStage({ page: 'factor', workspaceId: item.workspaceId, sessionId: state.sid,
      run: item.runId || '', load: item.runId ? '' : item.workflowId,
      workflowImport: !item.runId && !item.workflowId ? { graph: item.graph, name: item.name,
        workspace_id: item.workspaceId, session_id: state.sid } : null, importToken });
  };
  const stageWorkspaceRef = React.useRef(activeWorkspaceId);
  React.useEffect(() => {
    if (stage && (stageWorkspaceRef.current !== activeWorkspaceId || (stage.sessionId && stage.sessionId !== state.sid))) setStage(null);
    stageWorkspaceRef.current = activeWorkspaceId;
  }, [activeWorkspaceId, state.sid, stage]);

  const openReport = async (item) => {
    const reportSid = streamSidRef.current;
    const reportGeneration = streamGenRef.current;
    setChannelDrawerOpen(false);
    setDrawer({ sym: { code: item.code, name: item.name }, status: 'running', text: '', path: item.path, startedAt: Date.now() });
    try {
      const r = await fetch(WW.API + '/report?path=' + encodeURIComponent(item.path)).then(x => x.json());
      if (!requestIsCurrent(reportSid, reportGeneration)) return;
      setDrawer(d => d && { ...d, status: 'done', text: r.ok ? r.text : ('⚠ 读取失败: ' + (r.reason || '')) });
    } catch (e) { if (requestIsCurrent(reportSid, reportGeneration)) setDrawer(d => d && { ...d, status: 'done', text: '⚠ 读取失败: ' + e }); }
  };

  const dispatchLive = (a) => {
    if (Number(a.generation || 0) !== streamGenRef.current) return;
    if (a.sid && a.sid !== streamSidRef.current) return;
    dispatch(a);
    if (a.type === 'ev' && a.ev.type === 'tool_result' && a.ev.artifact && a.ev.artifact.kind === 'report_md') {
      const p = a.ev.artifact.payload;
      const eventSid = a.sid; const eventGeneration = Number(a.generation || 0);
      setTimeout(() => { if (requestIsCurrent(eventSid, eventGeneration)) openReport({ path: p.path, code: p.code, name: p.name || p.code }); }, 0);
    }
    if (a.type === 'ev' && a.ev.type === 'tool_result') {
      const toolName = String(a.ev.tool || a.ev.name || '');
      if (/^ww_(telegram|automation)_/.test(toolName)) {
        pullChannel(a.sid || streamSidRef.current, Number(a.generation || streamGenRef.current));
      }
    }
  };

  React.useEffect(() => {
    (state.events || []).forEach(ev => applySeatBindEvent(state.sid, ev));
  }, [state.sid, state.events.length]);

  // 哨兵条目点击前把目标交给完整「席位 · 落子」页；WwSessBar 使用标准链接导航。
  // 完整页不带 ws 参数，因此写裸 cockpit 信箱，保证落地页 mount 时能够消费并聚焦。
  const onSentryFocus = (it) => {
    if (it && it.code && window.GL && window.GL.handoff) window.GL.handoff('cockpit', { code: it.code, name: it.name });
  };

  const refreshSessions = async () => {
    const rows = await WW.sessions();
    if (Array.isArray(rows)) {
      setSessions(rows);
      setChannelBySid(current => {
        let changed = false; const next = { ...current };
        rows.forEach(row => {
          const summary = row && row.channel_summary && typeof row.channel_summary === 'object'
            ? row.channel_summary : null;
          const prior = row && current[row.id];
          if (summary && !prior) {
            next[row.id] = summary; changed = true;
          } else if (summary && prior.transient_status_error === true
              && prior.available !== true) {
            next[row.id] = { ...summary, ok: false,
              transient_status_error: true,
              status_error: prior.status_error || 'channel_status_unreachable',
              reason_code: prior.reason_code || 'channel_status_unreachable',
              error_code: prior.error_code || 'channel_status_unreachable',
              http_status: prior.http_status || 0 };
            changed = true;
          }
        });
        return changed ? next : current;
      });
    }
    return Array.isArray(rows) ? rows : [];
  };
  const refreshWorkspaces = () => WW.workspaces().then(d => {
    setWorkspacesStatus({ ok: !!d.ok, reason: d.reason || '' });
    if (Array.isArray(d.workspaces) && d.workspaces.length) setWorkspaces(d.workspaces);
  });
  const pullChannel = async (sid, generation) => {
    if (!sid || !WW.channelStatus) return null;
    const sequence = ++channelRequestSeqRef.current;
    const result = await WW.channelStatus(sid);
    if (!requestIsCurrent(sid, generation) || sequence !== channelRequestSeqRef.current) return result;
    setChannelBySid(current => {
      const prior = current[sid] || (sid === state.sid ? sessionChannel : null);
      if (result && result.transient_status_error === true && prior && prior.available === true) {
        return { ...current, [sid]: { ...prior, ok: false,
          status_error: result.status_error || 'channel_status_unreachable',
          transient_status_error: true } };
      }
      return { ...current, [sid]: result };
    });
    return result;
  };
  const staleChannelResult = () => ({ ok: false,
    reason_code: 'channel_request_stale', error_code: 'channel_request_stale',
    reason: '当前对话已切换，请返回原对话确认真实状态' });
  const openChannel = () => {
    if (!state.sid) return;
    setDrawer(null);
    setChannelDrawerOpen(true);
    pullChannel(state.sid, streamGenRef.current);
  };
  const attach = (sid) => {
    if (esRef.current) esRef.current.close();
    const generation = ++streamGenRef.current;
    streamSidRef.current = sid;
    setDrawer(null);                                      // 切会话清研报抽屉,防上个会话的研报残留
    setChannelDrawerOpen(false);                          // 通道抽屉同样严格跟随当前会话
    dispatch({ type: 'sid', sid, generation });
    esRef.current = WW.connect(sid, dispatchLive, generation);
    pullChannel(sid, generation);
    // sessionStorage=本 tab 会话(刷新回到本 tab 的会话);localStorage=新 tab 兜底
    try { sessionStorage.setItem('guanlan:ww:sid', sid); } catch (e) {}
    try { localStorage.setItem('guanlan:ww:sid', sid); } catch (e) {}
  };

  React.useEffect(() => {
    if (!WW.API) return;
    refreshSessions(); refreshWorkspaces();
    const explicitSid = new URLSearchParams(location.search).get('session_id');
    const last = (explicitSid && /^[0-9A-Za-z_-]{1,100}$/.test(explicitSid) ? explicitSid : '') || (() => { try { return sessionStorage.getItem('guanlan:ww:sid') || localStorage.getItem('guanlan:ww:sid'); } catch (e) { return null; } })();
    if (last) { attach(last); }
    else { WW.newSession(null, generalId).then(m => { if (m && m.id) attach(m.id); }); }
    return () => { if (esRef.current) esRef.current.close(); };
  }, []);

  const onSend = async (text) => {
    const requestSid = state.sid;
    const requestGeneration = streamGenRef.current;
    // Bot Token 是秘密控制输入，不进入对话、乐观消息或转向事件。
    if (WW.looksLikeTelegramToken && WW.looksLikeTelegramToken(text)) {
      const draft = activeChannel && activeChannel.draft && typeof activeChannel.draft === 'object'
        ? activeChannel.draft : null;
      const draftId = draft && draft.draft_id;
      let secretResult = { ok: false, reason_code: 'channel_draft_required',
        error_code: 'channel_draft_required',
        reason: '先让 Agent 创建连接草稿，再提交 Bot Token' };
      try {
        if (draftId) {
          const issued = await WW.channelRequestToken(requestSid, 'telegram:submit-token', draftId);
          if (issued && issued.ok && !requestIsCurrent(requestSid, requestGeneration)) {
            secretResult = staleChannelResult();
          } else if (issued && issued.ok) {
            secretResult = await WW.telegramSubmitToken(
              requestSid, draftId, text, issued.request_token || issued.ticket
            );
            if (!requestIsCurrent(requestSid, requestGeneration)) {
              secretResult = staleChannelResult();
            } else if (secretResult && secretResult.ok) {
              await pullChannel(requestSid, requestGeneration);
              refreshSessions();
            }
          } else if (issued) {
            secretResult = issued;
          }
        }
      } catch (e) {
        secretResult = { ok: false, reason_code: 'channel_submit_failed',
          error_code: 'channel_submit_failed', reason: 'Token 安全提交失败，请重试' };
      } finally {
        text = '';
      }
      return { ...(secretResult || {}), secret_handled: true, clear_input: true };
    }
    if (activeSessionArchived) return { ok: false, reason: '该对话已归档；历史记录仅供查看，请先恢复' };
    if (activeWorkspace && activeWorkspace.archived) return { ok: false, reason: '该研究空间已归档；历史消息仅供查看' };
    const requestToken = requestSid + ':' + requestGeneration + ':' + (++requestSeqRef.current);
    if (state.busy) {
      dispatch({ type: 'control', control: { kind: 'steer', status: 'stopping', text,
        source: 'local', requestToken, note: '正在安全停止当前任务…' } });
      const steered = await WW.steer(requestSid, text, thinkingMode);
      if (!requestIsCurrent(requestSid, requestGeneration)) return steered;
      if (!steered || !steered.ok) dispatch({ type: 'controlFailure', sid: requestSid,
        generation: requestGeneration, requestToken,
        note: (steered && (steered.reason || steered.message)) || '转向失败，请重试' });
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
        attach(m.id); sendSid = m.id; sendGeneration = streamGenRef.current;
        dispatch({ type: 'optimisticTurn', sid: sendSid, generation: sendGeneration, requestToken });
        r = await WW.send(sendSid, text, thinkingMode);
        if (!requestIsCurrent(sendSid, sendGeneration)) return r;
      }
    }
    if (!r || !r.ok) dispatch({ type: 'sendFailed', sid: sendSid, generation: sendGeneration,
      requestToken, note: (r && (r.reason || r.message)) || '发送失败，请重试' });
    if (requestIsCurrent(sendSid, sendGeneration)) refreshSessions();
    return r;
  };
  const onChannelSubmitToken = async (draftId, tokenValue) => {
    const sid = state.sid; const generation = streamGenRef.current;
    if (activeSessionArchived || (activeWorkspace && activeWorkspace.archived)) {
      return { ok: false, reason: '归档对话只读，请先恢复后再连接 Telegram' };
    }
    try {
      const issued = await WW.channelRequestToken(sid, 'telegram:submit-token', draftId);
      if (!issued || !issued.ok) return issued;
      if (!requestIsCurrent(sid, generation)) return staleChannelResult();
      const result = await WW.telegramSubmitToken(
        sid, draftId, tokenValue, issued.request_token || issued.ticket
      );
      if (!requestIsCurrent(sid, generation)) return staleChannelResult();
      if (result && result.ok) {
        await pullChannel(sid, generation); refreshSessions();
      }
      return result;
    } catch (e) {
      return { ok: false, reason: 'Token 安全提交失败，请重试' };
    } finally {
      tokenValue = '';
    }
  };
  const onChannelManage = async (action) => {
    const sid = state.sid; const generation = streamGenRef.current;
    if (activeSessionArchived || (activeWorkspace && activeWorkspace.archived)) {
      return { ok: false, reason: '归档对话只读，请先恢复后再管理通道' };
    }
    const issued = await WW.channelRequestToken(sid, 'telegram:' + action, null);
    if (!issued || !issued.ok) return issued;
    if (!requestIsCurrent(sid, generation)) return staleChannelResult();
    const result = await WW.channelManage(sid, action, issued.request_token || issued.ticket);
    if (!requestIsCurrent(sid, generation)) return staleChannelResult();
    if (result && result.ok) {
      await pullChannel(sid, generation); refreshSessions();
    }
    return result;
  };
  const onChannelResolve = async (outboxId, action) => {
    const sid = state.sid; const generation = streamGenRef.current;
    if (activeSessionArchived || (activeWorkspace && activeWorkspace.archived)) {
      return { ok: false, reason: '归档对话只读，请先恢复后再处理投递' };
    }
    const issued = await WW.channelRequestToken(sid, 'telegram:resolve:' + action, outboxId);
    if (!issued || !issued.ok) return issued;
    if (!requestIsCurrent(sid, generation)) return staleChannelResult();
    const result = await WW.channelResolveDelivery(
      sid, outboxId, action, issued.request_token || issued.ticket
    );
    if (!requestIsCurrent(sid, generation)) return staleChannelResult();
    if (result && result.ok) {
      await pullChannel(sid, generation); refreshSessions();
    }
    return result;
  };
  const onConfirmChoice = async (choice) => {
    const pending = state.confirm;
    const sid = state.sid; const generation = streamGenRef.current;
    const result = pending && pending.action_ticket_required === true
      ? await WW.channelConfirm(sid, pending, pending.tool, choice)
      : await WW.confirm(pending, choice);
    if (!requestIsCurrent(sid, generation)) return staleChannelResult();
    if (result && result.ok) pullChannel(sid, generation);
    return result;
  };
  const onCancel = async () => {
    if (activeWorkspace && activeWorkspace.archived) return { ok: false, reason: '归档空间不能执行停止操作' };
    if (!state.busy) return { ok: true, message: '当前没有运行中的前台任务' };
    const requestSid = state.sid;
    const requestGeneration = streamGenRef.current;
    const requestToken = requestSid + ':' + requestGeneration + ':' + (++requestSeqRef.current);
    dispatch({ type: 'control', control: { kind: 'cancel', status: 'stopping', source: 'local', requestToken,
      note: '正在安全停止当前任务…' } });
    const cancelled = await WW.cancel(requestSid);
    if (!requestIsCurrent(requestSid, requestGeneration)) return cancelled;
    if (!cancelled || !cancelled.ok) dispatch({ type: 'controlFailure', sid: requestSid,
      generation: requestGeneration, requestToken,
      note: (cancelled && (cancelled.reason || cancelled.message)) || '停止失败，请重试' });
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
    const iv = setInterval(() => {
      refreshSessions(); refreshWorkspaces(); pullSentry();
      const sid = streamSidRef.current; const generation = streamGenRef.current;
      if (sid) pullChannel(sid, generation);
    }, 8000);
    return () => clearInterval(iv);
  }, []);

  React.useEffect(() => {
    const viewState = activeChannel && activeChannel.view_state;
    if (!state.sid || ['awaiting_pairing', 'pairing_pending'].indexOf(viewState) < 0) return undefined;
    const sid = state.sid; const generation = state.streamGeneration;
    const iv = setInterval(() => pullChannel(sid, generation), 2000);
    return () => clearInterval(iv);
  }, [state.sid, state.streamGeneration, activeChannel && activeChannel.view_state]);

  React.useEffect(() => {
    const resize = () => { const next = window.innerWidth < 1120; setNarrow(next); if (next) setDockOpen(false); };
    window.addEventListener('resize', resize); return () => window.removeEventListener('resize', resize);
  }, []);

  if (!WW.API) return <div style={{ padding: 40, fontFamily: 'var(--serif)', color: 'var(--ink-2)' }}>帷幄需经 9999 服务打开(SSE 与工具都在后端):http://127.0.0.1:9999/ui/console/观澜 · 帷幄.html</div>;

  const railW = narrow ? 236 : 264;
  const dockCol = (!narrow && dockOpen && !stage) ? ' 340px' : '';
  const cols = stage ? (railW + 'px 1fr') : (railW + 'px 1fr' + dockCol);

  // 二级 masthead 已裁(与顶部 nav 重复)；artifact 不再自动展开第三栏。
  return (
    <div style={{ display: 'grid', gridTemplateColumns: cols, height: '100vh', minWidth: 0, position: 'relative' }}>
      <WwRail state={state} sessions={sessions} workspaces={workspaces} workspacesStatus={workspacesStatus} activeWorkspaceId={activeWorkspaceId} onNew={onNew} onNewWorkspace={onNewWorkspace} onChooseFolder={WW.chooseResearchFolder} onSwitch={attach} onUpdate={onUpdateSession} onArchive={onArchiveSession} onRestore={onRestoreSession} channelBySid={channelBySid} />
      {stage ? <WwWorkspaceStage page={stage.page} load={stage.load} asset={stage.asset} run={stage.run} sessionId={stage.sessionId} workflowImport={stage.workflowImport} importToken={stage.importToken} readOnly={!!(activeWorkspace && activeWorkspace.archived)} workspaceId={activeWorkspaceId} onBack={() => setStage(null)} /> : <React.Fragment>
      <WwThread key={state.sid || 'no-session'} state={state} workspace={activeWorkspace} sessionArchived={activeSessionArchived} onSend={onSend} onCancel={onCancel} onConfirm={onConfirmChoice} onOpenReport={openReport} onOpenWorkflow={openWorkflow} onRename={activeSessionArchived ? null : (t) => onUpdateSession(state.sid, { title: t })} sentry={sentry} markSentrySeen={markSentrySeen} onSentryFocus={onSentryFocus} thinkingMode={thinkingMode} onThinkingMode={onThinkingMode} channelStatus={activeChannel} channelDrawerOpen={channelDrawerOpen} onOpenChannel={openChannel} onChannelSubmitToken={onChannelSubmitToken} onChannelRefresh={() => pullChannel(state.sid, streamGenRef.current)} />
      <WwWorkspaceDock workspace={activeWorkspace} sessions={activeWorkspaceSessions} sessionId={state.sid} open={dockOpen} narrow={narrow} onToggle={() => setDockOpen(true)} onClose={() => setDockOpen(false)} onOpenStage={openStage} onWorkspaceChanged={onWorkspaceChanged} />
      </React.Fragment>}
      {drawer && <WwDrawer drawer={drawer} onClose={() => setDrawer(null)} />}
      <WwChannelDrawer key={(state.sid || 'no-session') + ':' + state.streamGeneration} open={channelDrawerOpen} status={activeChannel} archived={activeSessionArchived || !!(activeWorkspace && activeWorkspace.archived)} onClose={() => setChannelDrawerOpen(false)} onManage={onChannelManage} onResolve={onChannelResolve} onSubmitToken={onChannelSubmitToken} onRefresh={() => pullChannel(state.sid, streamGenRef.current)} />
    </div>
  );
}
window.WeiwoApp = WeiwoApp;
