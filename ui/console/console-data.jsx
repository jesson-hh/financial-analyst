// console-data.jsx — 帷幄事件客户端:EventSource(原生,自动重连)+ reducer + API。
// 前端 = 事件流的纯渲染器:状态全部由 wwApply 从事件推导,刷新/重连 = snapshot 重放。
const WW_API = window.GUANLAN_BACKEND || '';

// 研究空间的兜底身份。旧后端没有 /console/workspaces、旧会话没有
// workspace_id 时，前端仍把它们稳定归入「通用研究」，不退回自由文本分组。
const WW_GENERAL_WORKSPACE = {
  id: 'rw_general', name: '通用研究', kind: 'general', code: '', display_name: '通用研究',
  root_mode: 'managed', root_path: '', agent_mode: 'research', legacy: true,
};

const WW_TOOL_CN = {
  ww_plan_update: '任务计划', ww_factor_analyze: '因子分析', ww_backtest: '回测',
  ww_screen_run: '选股', ww_screen_factors: '因子目录', ww_seats_decide: '落子研判', ww_cards_query: '经验卡',
  ww_reports_query: '报告库', quote_lookup: '行情', realtime_quote: '实时行情',
  stock_brief: '个股速览', financials: '财务', news_query: '新闻',
  wisdom_search: '经验检索', quant_reports: '量化报告',
  ww_report_run: '深度研报', ww_show_page: '调出界面', ww_cards_save: '沉淀经验卡',
  ww_memory_read: '读记忆', ww_memory_write: '记一笔', ww_seats_history: '研判历史',
};

// 页面注册表:artifact.page → 嵌入目标(channel 与各页 take() 通道一致)
const WW_PAGES = {
  screen: { label: '选股', file: '../screen/观澜 · 选股.html', channel: 'screen' },
  factor: { label: '工作流', file: '../factor/观澜 · AI 工作流.html', channel: 'workflow' },
  cards: { label: '经验卡', file: '../cards/观澜 · 经验验证区.html', channel: 'validation' },
  graph: { label: '图谱', file: '../graph/观澜 · 研究图谱.html', channel: null },
  seats: { label: '落子', file: '../seats/观澜 · 落子.html', channel: 'cockpit' },
};

function wwInitState() {
  return { sid: null, meta: null, events: [], plan: [], busy: false,
           artifacts: [], activated: [], confirm: null, connected: false, benchClosed: false,
           bgTasks: {}, control: null, activeTurnId: null, streamGeneration: 0 };
}

// media_image 是后端托管的只读媒体句柄，不是文件路径。前端只接受窄字符集的
// opaque media_id，再把它编码成单一路径段；artifact 里的 path/url/src 一律不读。
function wwSafeMediaId(value) {
  const id = String(value || '').trim();
  return /^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$/.test(id) ? id : '';
}

function wwMediaUrl(mediaId) {
  const id = wwSafeMediaId(mediaId);
  return id ? (WW_API + '/console/media/' + encodeURIComponent(id)) : '';
}

function wwNormalizeMediaImage(payload) {
  const p = payload && typeof payload === 'object' ? payload : {};
  const mediaId = wwSafeMediaId(p.media_id);
  const mime = String(p.mime || '').trim().toLowerCase();
  if (!mediaId || (mime && !/^image\/[a-z0-9.+-]+$/.test(mime))) return null;
  const clean = (value, fallback, limit) => {
    const text = String(value || '').replace(/[\u0000-\u001f\u007f]/g, ' ').trim();
    return (text || fallback).slice(0, limit);
  };
  const dimension = (value) => {
    const n = Number(value);
    return Number.isInteger(n) && n > 0 && n <= 100000 ? n : null;
  };
  const name = clean(p.name, '图片产物', 160);
  return {
    mediaId, name, mime: mime || 'image/*',
    width: dimension(p.width), height: dimension(p.height),
    alt: clean(p.alt, name, 300),
    workspaceId: clean(p.workspace_id, '', 160),
  };
}

// 单事件折叠进状态(snapshot 重放与直播共用)
function wwApply(s, ev) {
  const n = { ...s, events: s.events.concat([ev]) };
  if (ev.type === 'plan_update') n.plan = ev.todos || [];
  if (ev.type === 'task_update' && !ev.kind) {
    const status = String(ev.status || '').toLowerCase();
    const controlStatus = status === 'cancelling' ? 'stopping' : status;
    const controlKind = String(ev.control_kind || (s.control && s.control.kind) || '');
    if (status === 'running' || controlStatus === 'stopping' || status === 'steering') n.busy = true;
    if (status === 'running') {
      n.activeTurnId = ev.active_turn_id || ev.turn_id || ev.task_id || s.activeTurnId || null;
    }
    if (controlStatus === 'stopping' || status === 'steering') {
      n.control = { ...(s.control || {}), kind: controlKind || undefined, status: controlStatus,
        raw_status: status, note: ev.note || '', source: 'task_update' };
    }
    if (status === 'running' && s.control && s.control.status === 'queued') {
      n.control = null;
    } else if (status === 'running' && (ev.continuation_from || ev.continuation_turn_id || ev.control_kind === 'steer')
        && ((s.control && s.control.kind === 'steer') || controlKind === 'steer')) {
      n.control = { ...(s.control || {}), kind: 'steer', status: 'continued',
        source: 'task_update', note: ev.note || '已按新方向继续' };
    } else if (status === 'running' && s.control && s.control.source === 'local') {
      n.control = { ...s.control, source: 'task_update' };
    }
    const terminal = status === 'done' || status === 'cancelled'
      || (status === 'error' && ev.terminal !== false);
    if (status === 'error' && ev.terminal === false && s.control) {
      n.control = { ...s.control, source: 'task_update', warning: ev.note || '' };
    }
    if (terminal) {
      // steer 会先终止旧 turn、再启动替代 turn。旧 turn 的终态不能让界面
      // 短暂解锁成可再次 /send；保持 busy，等 steering/running 事件接管。
      const awaitingReplacement = ev.keep_busy === true || !!ev.continuation_turn_id
        || !!(s.control && s.control.kind === 'steer'
          && (s.control.status === 'stopping' || s.control.status === 'steering'));
      n.busy = awaitingReplacement;
      n.control = awaitingReplacement
        ? { ...(s.control || {}), kind: controlKind || 'steer', status: 'steering',
          source: 'task_update', note: ev.note || '当前任务已安全停止，正在按新方向继续' }
        : null;
      n.activeTurnId = awaitingReplacement
        ? (ev.continuation_turn_id || s.activeTurnId || null) : null;
      n.confirm = null;
    }
  }
  if (ev.type === 'task_update' && ev.kind) {           // 后台任务(kind=report 等):按 task_id 聚合最新态
    n.bgTasks = { ...s.bgTasks, [ev.task_id]: { ...(s.bgTasks[ev.task_id] || {}), ...ev } };
  }
  if (ev.type === 'confirm_request') {
    n.confirm = ev;
    n.busy = true;
    n.activeTurnId = ev.turn_id || s.activeTurnId || null;
  }
  if (ev.type === 'confirm_resolved') n.confirm = null;   // 修复#4:已决确认门即关(快照按序重放,不再复活)
  if (ev.type === 'control_event') {
    const status = String(ev.status || '').toLowerCase();
    const controlStatus = status === 'cancelling' ? 'stopping' : status;
    const controlKind = String(ev.control_kind || ev.kind || (s.control && s.control.kind) || '');
    if (controlStatus === 'stopping' || status === 'steering') {
      n.busy = true;
      n.control = { ...(s.control || {}), ...ev, kind: controlKind || undefined, status: controlStatus,
        raw_status: status, source: 'control_event' };
    } else if (status === 'continued' || status === 'running') {
      n.busy = true;
      n.activeTurnId = ev.active_turn_id || ev.turn_id || s.activeTurnId || null;
      n.control = { ...(s.control || {}), ...ev, kind: controlKind || 'steer',
        status: 'continued', source: 'control_event' };
    } else if (status === 'done' || status === 'error' || status === 'cancelled') {
      const awaitingReplacement = ev.keep_busy === true || !!ev.continuation_turn_id;
      n.busy = awaitingReplacement;
      n.activeTurnId = awaitingReplacement
        ? (ev.continuation_turn_id || s.activeTurnId || null) : null;
      n.control = awaitingReplacement
        ? { ...(s.control || {}), ...ev, kind: controlKind || 'steer', status: 'steering',
          source: 'control_event', note: ev.note || '当前任务已安全停止，正在按新方向继续' }
        : null;
      n.confirm = null;
    }
  }
  if (ev.type === 'tool_result' && ev.artifact && ev.artifact.page && WW_PAGES[ev.artifact.page]) {
    n.artifacts = s.artifacts.concat([{ ...ev.artifact, evId: ev.id, ts: ev.ts, tool: ev.tool }]);
    if (n.activated.indexOf(ev.artifact.page) < 0) n.activated = n.activated.concat([ev.artifact.page]);
    n.benchClosed = false;   // 新产物 → 工作台重新滑出
  }
  return n;
}

// 事件流 → chat 形消息模型:[{kind:'user'|'chain'|'answer'|'report'|'condense'|'error', ...}]
function wwDeriveItems(events, busy) {
  const items = [];
  let chain = null;            // 当前折叠中的工具链 {kind:'chain', chain:[...]}
  let openIdx = {};            // tool 名 → chain 数组下标(就近配对)
  let currentThinkingMode = 'standard';
  let currentTurnId = null;
  const flushChain = () => { chain = null; openIdx = {}; };
  events.forEach(ev => {
    const eventTurnId = ev.turn_id || ev.task_id || null;
    if (ev.type === 'task_update' && !ev.kind && ev.status === 'running' && eventTurnId) {
      currentTurnId = eventTurnId;
    }
    if (ev.type === 'user_msg') {
      flushChain();
      if (eventTurnId) currentTurnId = eventTurnId;
      currentThinkingMode = ev.thinking_mode === 'pro' ? 'pro' : 'standard';
      items.push({ kind: 'user', id: 'u' + ev.id, text: ev.text,
        thinkingMode: currentThinkingMode, turnId: eventTurnId || currentTurnId });
    } else if (ev.type === 'tool_call') {
      if (!chain) { chain = { kind: 'chain', id: 'c' + ev.id, chain: [] }; items.push(chain); }
      openIdx[ev.tool] = chain.chain.length;
      chain.chain.push({ name: ev.tool, cn: (WW_TOOL_CN[ev.tool] || ev.tool),
        args: ev.args ? JSON.stringify(ev.args) : '{}', t: 0, status: 'running', _ts: ev.ts,
        turnId: eventTurnId || currentTurnId });
    } else if (ev.type === 'tool_result') {
      const at = chain ? openIdx[ev.tool] : null;
      let row = (chain && at != null && chain.chain[at]
        && chain.chain[at].status === 'running'
        && (!eventTurnId || !chain.chain[at].turnId || chain.chain[at].turnId === eventTurnId))
        ? chain.chain[at] : null;
      // steer 在旧工具返回前会先写入新 user_msg，它会 flush 当前 chain。
      // 因此失配时向后回扫所有工具链，只匹配最近的同名 running 行。
      if (!row) {
        for (let i = items.length - 1; i >= 0 && !row; i--) {
          const prior = items[i];
          if (!prior || prior.kind !== 'chain') continue;
          for (let j = prior.chain.length - 1; j >= 0; j--) {
            const candidate = prior.chain[j];
            if (candidate.name === ev.tool && candidate.status === 'running'
                && (!eventTurnId || !candidate.turnId || candidate.turnId === eventTurnId)) {
              row = candidate;
              break;
            }
          }
        }
      }
      if (row) {
        row.status = ev.ok ? 'done' : 'fail';
        row.result = ev.summary || '';
        row.t = row._ts && ev.ts ? Math.max(0, (new Date(ev.ts) - new Date(row._ts)) / 1000) : 0;
        if (chain && at != null && chain.chain[at] === row) delete openIdx[ev.tool];
      }
      if (ev.artifact && ev.artifact.kind === 'report_md') {
        items.push({ kind: 'report', id: 'r' + ev.id, path: ev.artifact.payload.path,
          code: ev.artifact.payload.code, name: ev.artifact.payload.name || ev.artifact.payload.code });
      }
      if (ev.artifact && ev.artifact.kind === 'media_image') {
        const media = wwNormalizeMediaImage(ev.artifact.payload);
        if (media) items.push({ kind: 'media_image', id: 'm' + ev.id,
          tool: ev.tool || '', turnId: eventTurnId || currentTurnId, ...media });
        else items.push({ kind: 'warning', id: 'mw' + ev.id,
          note: '图片产物已拦截：缺少合法的媒体标识或图片类型' });
      }
    } else if (ev.type === 'artifact' && ev.artifact && ev.artifact.kind === 'media_image') {
      // 兼容仅投递 artifact 的 SSE 事件；仍执行与 tool_result 完全相同的句柄校验。
      const media = wwNormalizeMediaImage(ev.artifact.payload);
      if (media) items.push({ kind: 'media_image', id: 'm' + ev.id,
        tool: ev.tool || '', turnId: eventTurnId || currentTurnId, ...media });
      else items.push({ kind: 'warning', id: 'mw' + ev.id,
        note: '图片产物已拦截：缺少合法的媒体标识或图片类型' });
    } else if (ev.type === 'agent_delta' && ev.text) {
      flushChain();              // 文本到来 = 这一段工具链收束
      const last = items[items.length - 1];
      const answerTurnId = eventTurnId || currentTurnId;
      if (last && last.kind === 'answer' && last.turnId === answerTurnId) last.text += '\n\n' + ev.text;
      else items.push({ kind: 'answer', id: 'a' + ev.id, text: ev.text,
        thinkingMode: currentThinkingMode, turnId: answerTurnId });
    } else if (ev.type === 'condensation') {
      items.push({ kind: 'condense', id: 'k' + ev.id, summary: ev.summary || '' });
    } else if (ev.type === 'task_update' && ev.status === 'cancelled' && !ev.kind) {
      // 保留旧 turn 已经生成的部分内容，但明确标记「被打断」；
      // 只取消尚未完成的工具，不把已 done 的工具误标失败。
      const cancelledTurnId = ev.task_id || ev.turn_id || null;
      if (cancelledTurnId) {
        for (let i = items.length - 1; i >= 0; i--) {
          if (items[i].kind === 'answer' && items[i].turnId === cancelledTurnId) {
            items[i].interrupted = true; break;
          }
        }
        items.forEach(it => {
          if (it.kind === 'chain') it.chain.forEach(row => {
            if (row.status === 'running' && row.turnId === cancelledTurnId) row.status = 'cancelled';
          });
        });
      }
      flushChain();
    } else if (ev.type === 'task_update' && ev.status === 'error' && !ev.kind) {
      items.push({ kind: ev.terminal === false ? 'warning' : 'error',
        id: 'e' + ev.id, note: ev.note || '' });
    }
  });
  const last = items[items.length - 1];
  if (busy && last && last.kind === 'answer') last.streaming = true;   // 流式光标
  if (!busy) items.forEach(it => { if (it.kind === 'chain') it.chain.forEach(r => { if (r.status === 'running') r.status = 'fail'; }); });
  return items;
}

function wwConnect(sid, dispatch, generation) {
  const streamGeneration = Number(generation || 0);
  const es = new EventSource(WW_API + '/console/stream/' + sid);
  es.addEventListener('snapshot', (m) => {
    try {
      const d = JSON.parse(m.data);
      dispatch({ type: 'snapshot', sid, generation: streamGeneration,
        meta: d.meta, events: d.events || [], runtime: d.runtime || null });
    } catch (e) {}
  });
  es.addEventListener('ev', (m) => {
    try { dispatch({ type: 'ev', sid, generation: streamGeneration, ev: JSON.parse(m.data) }); } catch (e) {}
  });
  es.onopen = () => dispatch({ type: 'conn', sid, generation: streamGeneration, ok: true });
  es.onerror = () => dispatch({ type: 'conn', sid, generation: streamGeneration, ok: false }); // EventSource 自动重连,重连即重收 snapshot
  return es;
}

async function wwSend(sid, text, thinkingMode) {
  try {
    const r = await fetch(WW_API + '/console/send', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ sid, text, thinking_mode: thinkingMode === 'pro' ? 'pro' : 'standard' }),
    });
    return r.json();
  } catch (e) { return { ok: false, reason: '会话服务不可达' }; }
}

async function wwTurnControl(path, body, fallbackReason) {
  try {
    const r = await fetch(WW_API + path, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body || {}),
    });
    let d = {};
    try { d = await r.json(); } catch (e) {}
    return {
      ...d,
      ok: r.ok && d.ok !== false,
      http_status: r.status,
      reason: d.reason || d.message || (r.ok ? '' : ('HTTP ' + r.status)),
    };
  } catch (e) { return { ok: false, http_status: 0, reason: fallbackReason }; }
}

// 原子转向契约:后端负责取消当前 foreground、等待收尾、再启动唯一替代 turn；
// 前端收到成功后绝不补调 /send，避免两个 foreground turn 并发。
function wwSteer(sid, text, thinkingMode) {
  return wwTurnControl('/console/steer', {
    sid, text, thinking_mode: thinkingMode === 'pro' ? 'pro' : 'standard',
  }, '转向服务不可达');
}

// 幂等停止当前 foreground；后台研报等独立任务不属于此接口。
function wwCancel(sid) {
  return wwTurnControl('/console/cancel', { sid }, '停止服务不可达');
}

async function wwConfirm(turnId, choice) {
  try {
    const r = await fetch(WW_API + '/console/confirm', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ turn_id: turnId, choice }),
    });
    return r.json();
  } catch (e) { return { ok: false, reason: '确认服务不可达' }; }
}

async function wwSessions() {
  try {
    const r = await fetch(WW_API + '/console/sessions');
    if (!r.ok) return [];
    return (await r.json()).sessions || [];
  } catch (e) { return []; }
}

async function wwUpdateSession(sid, fields) {   // 改名/分组:{title?} / {group?}(空串=取消分组)
  try {
    const r = await fetch(WW_API + '/console/sessions/' + sid, {
      method: 'PATCH', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(fields),
    });
    const d = await r.json();
    return r.ok ? d : { ...d, ok: false, reason: d.reason || ('HTTP ' + r.status) };
  } catch (e) { return { ok: false, reason: '会话服务不可达' }; }
}

async function wwSessionLifecycle(sid, action) {
  try {
    const safeAction = action === 'restore' ? 'restore' : 'archive';
    const r = await fetch(WW_API + '/console/sessions/' + encodeURIComponent(sid) + '/' + safeAction, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}',
    });
    const d = await r.json().catch(() => ({}));
    return r.ok ? d : { ...d, ok: false, reason: d.reason || ('HTTP ' + r.status) };
  } catch (e) { return { ok: false, reason: '会话归档服务不可达' }; }
}
function wwArchiveSession(sid) { return wwSessionLifecycle(sid, 'archive'); }
function wwRestoreSession(sid) { return wwSessionLifecycle(sid, 'restore'); }

async function wwNewSession(title, workspaceId) {
  const post = (body) => fetch(WW_API + '/console/sessions', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
  });
  try {
    const body = { title: title || '新对话', workspace_id: workspaceId || WW_GENERAL_WORKSPACE.id };
    let r = await post(body);
    // 仅在旧服务以 schema 422 拒绝新增字段时重试旧协议。当前服务对
    // 未知/已归档 workspace 返回 400；绝不能把这种错误静默改建到通用空间。
    if (!r.ok && r.status === 422) r = await post({ title: body.title });
    if (!r.ok) return null;
    return (await r.json()).meta || null;
  } catch (e) { return null; }
}

async function wwWorkspaces() {
  try {
    const r = await fetch(WW_API + '/console/workspaces');
    if (!r.ok) return { ok: false, workspaces: [WW_GENERAL_WORKSPACE], reason: '研究空间服务暂不可用' };
    const d = await r.json();
    const rows = Array.isArray(d.workspaces) ? d.workspaces : [];
    return { ok: d.ok !== false, workspaces: rows.length ? rows : [WW_GENERAL_WORKSPACE], reason: d.reason || '' };
  } catch (e) {
    return { ok: false, workspaces: [WW_GENERAL_WORKSPACE], reason: '研究空间服务暂不可用，已进入兼容模式' };
  }
}

async function wwNewWorkspace(fields) {
  try {
    const r = await fetch(WW_API + '/console/workspaces', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(fields || {}),
    });
    const d = await r.json();
    return r.ok ? d : { ...d, ok: false, reason: d.reason || ('HTTP ' + r.status) };
  } catch (e) { return { ok: false, reason: '研究空间服务不可达' }; }
}

async function wwUpdateWorkspace(wid, fields) {
  try {
    const r = await fetch(WW_API + '/console/workspaces/' + encodeURIComponent(wid), {
      method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(fields || {}),
    });
    const d = await r.json();
    return r.ok ? d : { ...d, ok: false, reason: d.reason || ('HTTP ' + r.status) };
  } catch (e) { return { ok: false, reason: '研究空间服务不可达' }; }
}

async function wwWorkspaceRead(wid, suffix) {
  try {
    const r = await fetch(WW_API + '/console/workspaces/' + encodeURIComponent(wid) + suffix);
    if (!r.ok) return { ok: false, available: false, reason: '该能力尚未接入' };
    const d = await r.json();
    return { available: true, ...d };
  } catch (e) { return { ok: false, available: false, reason: '服务暂不可达' }; }
}

function wwWorkspaceFiles(wid, path, depth) {
  const q = '?path=' + encodeURIComponent(path || '') + '&depth=' + encodeURIComponent(depth == null ? 2 : depth);
  return wwWorkspaceRead(wid, '/files' + q);
}
function wwWorkspaceFile(wid, path) {
  return wwWorkspaceRead(wid, '/file?path=' + encodeURIComponent(path || ''));
}
function wwWorkspaceAgentRuns(wid) { return wwWorkspaceRead(wid, '/agent/runs'); }
function wwWorkspaceAssets(wid) { return wwWorkspaceRead(wid, '/assets'); }
function wwWorkspaceAsset(wid, assetId) {
  return wwWorkspaceRead(wid, '/assets/' + encodeURIComponent(assetId));
}
async function wwWorkspaceSeats(wid, limit) {
  const n = Math.max(1, Math.min(100, Number(limit) || 50));
  try {
    const r = await fetch(WW_API + '/console/workspaces/' + encodeURIComponent(wid)
      + '/seats?limit=' + encodeURIComponent(n));
    const d = await r.json().catch(() => ({}));
    if (r.ok) return { available: d.available !== false, ...d };
    const missingCapability = r.status === 404 || r.status === 405;
    return { ...d, ok: false, available: !missingCapability,
      reason: d.reason || (missingCapability ? '该能力尚未接入' : ('HTTP ' + r.status)) };
  } catch (e) { return { ok: false, available: false, reason: '盯盘投影服务暂不可达' }; }
}

// 股票 / ETF 深链统一用带交易所前缀的代码。后端通常已返回 canonical code，
// 这里仍兼容旧空间里仅保存六位数字的记录；非法自由文本不进入 handoff 信箱。
function wwCanonicalSecurityCode(value) {
  let code = String(value || '').trim().toUpperCase().replace(/\s+/g, '');
  const suffix = code.match(/^(\d{6})\.(SH|SZ|BJ)$/);
  if (suffix) code = suffix[2] + suffix[1];
  if (/^(SH|SZ|BJ)\d{6}$/.test(code)) return code;
  if (!/^\d{6}$/.test(code)) return '';
  if (/^(4|8|920)/.test(code)) return 'BJ' + code;
  if (/^(5|6|9)/.test(code)) return 'SH' + code;
  return 'SZ' + code;
}

// pywebview 的目录授权不可由普通网页伪造。浏览器里只返回诚实不可用态；
// 桌面端返回的 folder_grant 原样交给后端，绝不把用户输入路径冒充授权。
async function wwChooseResearchFolder() {
  try {
    const api = window.pywebview && window.pywebview.api;
    if (!api || typeof api.choose_research_folder !== 'function') {
      return { ok: false, reason: '请选择观澜桌面端中的文件夹；当前浏览器不具备目录授权能力' };
    }
    const d = await api.choose_research_folder();
    return d || { ok: false, reason: '目录选择未返回结果' };
  } catch (e) { return { ok: false, reason: '目录选择失败' }; }
}

// 哨兵研判(平台级,不属于某个会话→不进会话事件流):读 /seats/decisions,最新在前;失败诚实返回 []
async function wwSeatsDecisions(limit) {
  try {
    const r = await fetch(WW_API + '/seats/decisions?limit=' + (limit || 20) + '&exclude_runs=1');
    return (await r.json()).decisions || [];
  } catch (e) { return []; }
}

window.WW = { API: WW_API, TOOL_CN: WW_TOOL_CN, PAGES: WW_PAGES, GENERAL_WORKSPACE: WW_GENERAL_WORKSPACE,
              initState: wwInitState, apply: wwApply, connect: wwConnect,
              send: wwSend, steer: wwSteer, cancel: wwCancel,
              confirm: wwConfirm, sessions: wwSessions, newSession: wwNewSession,
              updateSession: wwUpdateSession, archiveSession: wwArchiveSession,
              restoreSession: wwRestoreSession, workspaces: wwWorkspaces, newWorkspace: wwNewWorkspace,
              updateWorkspace: wwUpdateWorkspace, workspaceFiles: wwWorkspaceFiles,
              workspaceFile: wwWorkspaceFile, workspaceAsset: wwWorkspaceAsset,
              workspaceAgentRuns: wwWorkspaceAgentRuns, workspaceAssets: wwWorkspaceAssets,
              workspaceSeats: wwWorkspaceSeats, canonicalSecurityCode: wwCanonicalSecurityCode,
              chooseResearchFolder: wwChooseResearchFolder, deriveItems: wwDeriveItems,
              mediaUrl: wwMediaUrl,
              seatsDecisions: wwSeatsDecisions };
