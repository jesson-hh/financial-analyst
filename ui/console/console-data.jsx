// console-data.jsx — 帷幄事件客户端:EventSource(原生,自动重连)+ reducer + API。
// 前端 = 事件流的纯渲染器:状态全部由 wwApply 从事件推导,刷新/重连 = snapshot 重放。
const WW_API = window.GUANLAN_BACKEND || '';

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
           bgTasks: {} };
}

// 单事件折叠进状态(snapshot 重放与直播共用)
function wwApply(s, ev) {
  const n = { ...s, events: s.events.concat([ev]) };
  if (ev.type === 'plan_update') n.plan = ev.todos || [];
  if (ev.type === 'task_update' && !ev.kind) {
    if (ev.status === 'running') n.busy = true;
    if (ev.status === 'done' || ev.status === 'error') { n.busy = false; n.confirm = null; }
  }
  if (ev.type === 'task_update' && ev.kind) {           // 后台任务(kind=report 等):按 task_id 聚合最新态
    n.bgTasks = { ...s.bgTasks, [ev.task_id]: { ...(s.bgTasks[ev.task_id] || {}), ...ev } };
  }
  if (ev.type === 'confirm_request') n.confirm = ev;
  if (ev.type === 'confirm_resolved') n.confirm = null;   // 修复#4:已决确认门即关(快照按序重放,不再复活)
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

function wwConnect(sid, dispatch) {
  const es = new EventSource(WW_API + '/console/stream/' + sid);
  es.addEventListener('snapshot', (m) => {
    try {
      const d = JSON.parse(m.data);
      dispatch({ type: 'snapshot', meta: d.meta, events: d.events || [] });
    } catch (e) {}
  });
  es.addEventListener('ev', (m) => {
    try { dispatch({ type: 'ev', ev: JSON.parse(m.data) }); } catch (e) {}
  });
  es.onopen = () => dispatch({ type: 'conn', ok: true });
  es.onerror = () => dispatch({ type: 'conn', ok: false }); // EventSource 自动重连,重连即重收 snapshot
  return es;
}

async function wwSend(sid, text) {
  const r = await fetch(WW_API + '/console/send', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ sid, text }),
  });
  return r.json();
}

async function wwConfirm(turnId, choice) {
  const r = await fetch(WW_API + '/console/confirm', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ turn_id: turnId, choice }),
  });
  return r.json();
}

async function wwSessions() {
  const r = await fetch(WW_API + '/console/sessions');
  return (await r.json()).sessions || [];
}

async function wwUpdateSession(sid, fields) {   // 改名/分组:{title?} / {group?}(空串=取消分组)
  const r = await fetch(WW_API + '/console/sessions/' + sid, {
    method: 'PATCH', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(fields),
  });
  return r.json();
}

async function wwNewSession(title) {
  const r = await fetch(WW_API + '/console/sessions', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ title: title || '新对话' }),
  });
  return (await r.json()).meta;
}

// 哨兵研判(平台级,不属于某个会话→不进会话事件流):读 /seats/decisions,最新在前;失败诚实返回 []
async function wwSeatsDecisions(limit) {
  try {
    const r = await fetch(WW_API + '/seats/decisions?limit=' + (limit || 20) + '&exclude_runs=1');
    return (await r.json()).decisions || [];
  } catch (e) { return []; }
}

window.WW = { API: WW_API, TOOL_CN: WW_TOOL_CN, PAGES: WW_PAGES,
              initState: wwInitState, apply: wwApply, connect: wwConnect,
              send: wwSend, confirm: wwConfirm, sessions: wwSessions, newSession: wwNewSession,
              updateSession: wwUpdateSession, deriveItems: wwDeriveItems,
              seatsDecisions: wwSeatsDecisions };
