// console-data.jsx — 帷幄事件客户端:EventSource(原生,自动重连)+ reducer + API。
// 前端 = 事件流的纯渲染器:状态全部由 wwApply 从事件推导,刷新/重连 = snapshot 重放。
const WW_API = window.GUANLAN_BACKEND || '';

// A persisted snapshot is not evidence that its upstream source was available.
// Read the server's structured projection; never infer health from a title,
// a human-readable summary, or status=saved.
function wwDataAvailability(asset) {
  if (!asset || asset.kind !== 'data' || !asset.dataset_id) return null;
  const raw = String(asset.upstream_status || '').toLowerCase();
  const isList = ['research.control_lists.live', 'diagnostic.control_lists'].includes(asset.dataset_id);
  const count = typeof asset.record_count === 'number' && Number.isFinite(asset.record_count)
    ? asset.record_count : null;
  if (['unavailable', 'error', 'failed', 'blocked', 'invalid'].includes(raw)) return {
    state: 'unavailable', label: isList ? '名单不可用' : '数据源不可用',
    note: isList ? '本快照未完成名单核验；0 条不代表未命中或没有风险。' : '保存的是失败回执，不能当作可用数据。',
  };
  if (['partial', 'partial_empty', 'stale', 'degraded', 'incomplete'].includes(raw)) return {
    state: 'degraded', label: raw === 'stale' ? '数据已过期' : '数据降级',
    note: isList ? '名单覆盖不完整，不能作为完整筛查结论。' : '只使用实际返回部分，并核对观测日期及缺口。',
  };
  if (raw === 'ok' || raw === 'empty') return {
    state: raw === 'empty' || count === 0 ? 'empty' : 'available', label: raw === 'empty' || count === 0 ? '查询无记录' : '上游返回正常',
    note: isList ? '仅反映本次查询范围；无记录不等于公司无风险。' : '返回正常不代表全部字段完整或数据为今日。',
  };
  return { state: 'unknown', label: '数据可用性未核验', note: '快照已保存；缺少可判定的上游状态，不按成功处理。' };
}

function wwSourceAvailability(asset) {
  if (!asset || !['research.control_lists.live', 'diagnostic.control_lists'].includes(asset.dataset_id) || !Array.isArray(asset.source_availability)) return [];
  const states = {ok: '已取回', error: '不可用', reference_only: '仅原文入口，未结构化', not_requested: '本次未查询', unknown: '未核验'};
  return asset.source_availability.slice(0, 16).filter(row => row && typeof row === 'object').map((row, i) => {
    const status = Object.prototype.hasOwnProperty.call(states, row.status) ? row.status : 'unknown';
    return {id: String(row.id || i).slice(0, 60), label: String(row.label || '名单源').slice(0, 80),
      status, labelStatus: states[status], reason: /^[A-Za-z0-9_]{1,48}$/.test(String(row.reason_code || '')) ? row.reason_code : ''};
  });
}

// Channel API 的路径和动作都来自闭集。尤其不能把 UI/Agent 提供的任意
// action 直接拼进 URL；所有会话、草稿和投递标识也必须先校验再编码。
const WW_CHANNEL_MANAGE_ACTIONS = Object.freeze({
  pause: 'pause', resume: 'resume', reverify: 'reverify',
  'replace-token': 'replace-token', disconnect: 'disconnect',
});
const WW_CHANNEL_RESOLUTION_ACTIONS = Object.freeze({
  assume_sent: 'assume_sent', resend: 'resend', drop: 'drop', retry: 'retry',
});
const WW_CHANNEL_TICKET_ACTIONS = Object.freeze({
  'telegram:submit-token': true,
  'telegram:pause': true,
  'telegram:resume': true,
  'telegram:reverify': true,
  'telegram:replace-token': true,
  'telegram:disconnect': true,
  'telegram:resolve:assume_sent': true,
  'telegram:resolve:resend': true,
  'telegram:resolve:drop': true,
  'telegram:resolve:retry': true,
});
const WW_CHANNEL_CONFIRM_TOOLS = Object.freeze({
  ww_telegram_pair_approve: true,
  ww_telegram_pair_dismiss: true,
  ww_telegram_pause: true,
  ww_telegram_resume: true,
  ww_telegram_disconnect: true,
  ww_telegram_publish: true,
  ww_telegram_publish_document: true,
  ww_automation_upsert: true,
  ww_automation_pause: true,
  ww_automation_resume: true,
  ww_automation_delete: true,
});
const WW_TELEGRAM_TOKEN_SHAPE_RE = /(?:bot)?[0-9]{5,16}:[A-Za-z0-9_-]{20,}/i;
const WW_TELEGRAM_TOKEN_EXACT_RE = /^[0-9]{5,16}:[A-Za-z0-9_-]{20,}$/;

function wwLooksLikeTelegramToken(text) {
  return typeof text === 'string' && WW_TELEGRAM_TOKEN_SHAPE_RE.test(text);
}

function wwChannelSafeId(value) {
  const id = typeof value === 'string' ? value : '';
  return /^[A-Za-z0-9_.:-]{1,160}$/.test(id) && !wwLooksLikeTelegramToken(id) ? id : '';
}

function wwChannelSafeTicket(value) {
  const ticket = typeof value === 'string' ? value : '';
  return /^[A-Za-z0-9_-]{20,512}$/.test(ticket) && !wwLooksLikeTelegramToken(ticket)
    ? ticket : '';
}

function wwChannelUnavailableStatus(sid) {
  return {
    ok: false, available: false, session_id: wwChannelSafeId(sid), view_state: 'none',
    draft: null, bot: null, recipient: null, pairing_requests: [],
    counts: { pending: 0, retry_wait: 0, uncertain: 0, terminal: 0 },
    warnings: [], recent_deliveries: [], automations: [],
  };
}

function wwChannelSafeFailure(response, data, fallback) {
  const source = data && typeof data === 'object' ? data : {};
  const rawCode = source.reason_code || source.error_code || '';
  const errorCode = /^[A-Za-z_][A-Za-z0-9_.:-]{0,127}$/.test(String(rawCode || ''))
    ? String(rawCode) : '';
  const rawReason = typeof source.reason === 'string' ? source.reason : '';
  const safeReason = rawReason && rawReason.length <= 240 && !wwLooksLikeTelegramToken(rawReason)
    ? rawReason.replace(/[\u0000-\u001f\u007f]/g, ' ').trim() : '';
  const status = response && Number.isInteger(response.status) ? response.status : 0;
  return {
    ok: false, http_status: status,
    reason_code: errorCode, error_code: errorCode,
    reason: safeReason || errorCode || fallback,
  };
}

async function wwChannelReadResponse(response, fallback) {
  let data = {};
  try { data = await response.json(); } catch (e) { data = {}; }
  if (!response.ok || (data && data.ok === false)) {
    return wwChannelSafeFailure(response, data, fallback);
  }
  return { ...(data && typeof data === 'object' ? data : {}), ok: true,
    http_status: response.status };
}

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
  ww_report_run: '深度研报', ww_show_page: '界面导航', ww_cards_save: '沉淀经验卡',
  ww_memory_read: '读记忆', ww_memory_write: '记一笔', ww_seats_history: '研判历史',
  ww_workspace_quant_chart: '量化位置图',
  ww_telegram_connect: '连接 Telegram', ww_telegram_status: 'Telegram 状态',
  ww_telegram_pair_status: 'Telegram 配对请求', ww_telegram_pair_approve: '永久绑定 Telegram',
  ww_telegram_pair_dismiss: '忽略 Telegram 配对', ww_telegram_pause: '暂停 Telegram',
  ww_telegram_resume: '恢复 Telegram', ww_telegram_disconnect: '解除 Telegram 绑定',
  ww_telegram_publish: '发送图片到 Telegram',
  ww_telegram_publish_document: '发送文件到 Telegram',
  ww_workspace_analysis_run: '通用数据分析',
  ww_workspace_publish_image: '发布图片产物',
  ww_workspace_publish_document: '发布文件产物',
  ww_workspace_trade_disclosure_calendar: '交易 / 披露日历',
  ww_workspace_tw_monthly_revenue: '台系月营收',
  ww_workspace_cn_commodity_futures: '国内商品期货',
  ww_workspace_control_lists: '管制 / 制裁 / 准入',
  ww_workspace_sec_filings_xbrl: 'SEC 申报 / XBRL',
  ww_workspace_finra_short_volume: 'FINRA 日短量',
  ww_workspace_us_treasury: '美国国债',
  ww_workspace_cftc_cot: 'CFTC 周度持仓',
  ww_workspace_overseas_earnings_calendar: '海外财报预期日历',
  ww_workspace_industry_review_list: '行业资讯候选',
  ww_workspace_industry_review: '确认行业资讯关联',
  ww_workspace_asset_get: '打开研究资产',
  ww_automation_skills: '定时任务能力',
  ww_automation_list: '定时任务列表', ww_automation_upsert: '保存定时任务',
  ww_automation_pause: '暂停定时任务', ww_automation_resume: '恢复定时任务',
  ww_automation_delete: '删除定时任务',
};

// 研究空间 Dock / 全宽 Stage 的页面注册表。运行版不再挂载旧 WwBench，
// 但 Stage 仍需要这份显式映射来打开现有全局研究工具。
const WW_PAGES = {
  screen: { label: '选股', file: '../screen/观澜 · 选股.html', channel: 'screen' },
  factor: { label: '工作流', file: '../factor/观澜 · AI 工作流.html', channel: 'workflow' },
  cards: { label: '经验卡', file: '../cards/观澜 · 经验验证区.html', channel: 'validation' },
  graph: { label: '图谱', file: '../graph/观澜 · 研究图谱.html', channel: null },
  seats: { label: '落子', file: '../seats/观澜 · 落子.html', channel: 'cockpit' },
};

function wwInitState() {
  return { sid: null, meta: null, events: [], plan: [], busy: false,
           confirm: null, connected: false, bgTasks: {}, control: null,
           activeTurnId: null, streamGeneration: 0, evidence: [] };
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

// 文件能力与图片能力刻意使用不同的 opaque id 形状。document_id 必须由
// 后端附件仓签发；路径、URL、media_id 或任意自由文本都不能被降级为文件能力。
function wwSafeDocumentId(value) {
  const id = String(value || '').trim();
  return /^do_[0-9a-f]{32}$/.test(id) ? id : '';
}

function wwDocumentUrl(documentId) {
  const id = wwSafeDocumentId(documentId);
  return id ? (WW_API + '/console/media/' + encodeURIComponent(id)) : '';
}

const WW_DOCUMENT_MIME_TYPES = Object.freeze({
  'application/pdf': true,
  'text/markdown': true,
  'text/plain': true,
  'text/csv': true,
  'text/tab-separated-values': true,
  'application/json': true,
  'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet': true,
  'application/vnd.openxmlformats-officedocument.wordprocessingml.document': true,
});
const WW_DOCUMENT_MIME_SUFFIXES = Object.freeze({
  'application/pdf': ['.pdf'],
  'text/markdown': ['.md', '.markdown'],
  'text/plain': ['.txt'],
  'text/csv': ['.csv'],
  'text/tab-separated-values': ['.tsv'],
  'application/json': ['.json'],
  'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet': ['.xlsx'],
  'application/vnd.openxmlformats-officedocument.wordprocessingml.document': ['.docx'],
});

function wwArtifactText(value, fallback, limit) {
  const text = String(value || '').replace(/[\u0000-\u001f\u007f]/g, ' ').trim();
  return (text || fallback || '').slice(0, limit);
}

function wwNormalizeWorkflowArtifact(payload) {
  const p = payload && typeof payload === 'object' ? payload : {};
  const safeId = (value, pattern) => pattern.test(String(value || '')) ? String(value) : '';
  const workspaceId = safeId(p.workspace_id, /^rw_[0-9A-Za-z_-]+$/);
  const sessionId = safeId(p.session_id, /^[0-9A-Za-z_-]{1,100}$/);
  const runId = safeId(p.run_id, /^wr_[0-9a-f]{12,40}$/);
  const workflowId = safeId(p.workflow_id, /^[0-9A-Za-z_-]{1,100}$/);
  const graph = p.graph && Array.isArray(p.graph.nodes) && Array.isArray(p.graph.edges) ? p.graph : null;
  if (!workspaceId || (!runId && !workflowId && !graph)) return null;
  return { workspaceId, sessionId, runId, workflowId, graph, name: String(p.name || 'Agent 工作流').slice(0, 160) };
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

function wwNormalizeFileDocument(payload) {
  const p = payload && typeof payload === 'object' ? payload : {};
  const documentId = wwSafeDocumentId(p.document_id);
  const mime = String(p.mime || '').trim().toLowerCase();
  const size = p.size;
  if (!documentId || !WW_DOCUMENT_MIME_TYPES[mime]
      || !Number.isInteger(size) || size <= 0 || size > 10 * 1024 * 1024) return null;
  const rawName = typeof p.name === 'string' ? p.name : '';
  if (!rawName || /[\u0000-\u001f\u007f\\/]/.test(rawName)) return null;
  const name = wwArtifactText(rawName, '', 160);
  const suffix = name.includes('.') ? name.slice(name.lastIndexOf('.')).toLowerCase() : '';
  if (!name || !WW_DOCUMENT_MIME_SUFFIXES[mime].includes(suffix)) return null;
  return {
    documentId,
    name,
    mime,
    size,
    title: wwArtifactText(p.title, name, 300),
    workspaceId: wwArtifactText(p.workspace_id, '', 160),
  };
}

function wwNormalizeAnalysisBundle(payload) {
  const p = payload && typeof payload === 'object' ? payload : {};
  const rawImages = Array.isArray(p.images) ? p.images.slice(0, 12) : [];
  const rawDocuments = Array.isArray(p.documents) ? p.documents.slice(0, 12) : [];
  const images = rawImages.map(wwNormalizeMediaImage).filter(Boolean);
  const documents = rawDocuments.map(wwNormalizeFileDocument).filter(Boolean);
  return {
    title: wwArtifactText(p.title, '分析产物', 160),
    images,
    documents,
    rejected: (rawImages.length - images.length) + (rawDocuments.length - documents.length),
  };
}

// 单事件折叠进状态(snapshot 重放与直播共用)
function wwApply(s, ev) {
  const n = { ...s, events: s.events.concat([ev]) };
  if (ev.type === 'plan_update') n.plan = ev.todos || [];
  if (ev.type === 'evidence_update' && Array.isArray(ev.items)) {
    const merged = new Map((s.evidence || []).map(row => [row.evidence_id, row]));
    ev.items.forEach(row => { if (row && row.evidence_id) merged.set(row.evidence_id, row); });
    n.evidence = Array.from(merged.values());
  }
  if (ev.type === 'task_update' && !ev.kind) {
    const status = String(ev.status || '').toLowerCase();
    const controlStatus = status === 'cancelling' ? 'stopping' : status;
    const controlKind = String(ev.control_kind || (s.control && s.control.kind) || '');
    if (status === 'running' || controlStatus === 'stopping' || status === 'steering') n.busy = true;
    if (status === 'running') n.activeTurnId = ev.active_turn_id || ev.turn_id || ev.task_id || s.activeTurnId || null;
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
      const awaitingReplacement = ev.keep_busy === true || !!ev.continuation_turn_id
        || !!(s.control && s.control.kind === 'steer'
          && (s.control.status === 'stopping' || s.control.status === 'steering'));
      n.busy = awaitingReplacement;
      n.control = awaitingReplacement
        ? { ...(s.control || {}), kind: controlKind || 'steer', status: 'steering',
          source: 'task_update', note: ev.note || '当前任务已安全停止，正在按新方向继续' }
        : null;
      n.activeTurnId = awaitingReplacement ? (ev.continuation_turn_id || s.activeTurnId || null) : null;
      const terminalTurnId = String(ev.turn_id || ev.task_id || '');
      if (!s.confirm || String(s.confirm.turn_id || '') === terminalTurnId) n.confirm = null;
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
  if (ev.type === 'confirm_resolved' && s.confirm) {
    const sameInstance = String(s.confirm.turn_id || '') !== ''
      && String(s.confirm.turn_id || '') === String(ev.turn_id || '')
      && String(s.confirm.confirm_id || '') !== ''
      && String(s.confirm.confirm_id || '') === String(ev.confirm_id || '')
      && String(s.confirm.tool || '') !== ''
      && String(s.confirm.tool || '') === String(ev.tool || '');
    if (sameInstance) n.confirm = null;
  }
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
      n.control = { ...(s.control || {}), ...ev, kind: controlKind || 'steer', status: 'continued', source: 'control_event' };
    } else if (status === 'done' || status === 'error' || status === 'cancelled') {
      const awaitingReplacement = ev.keep_busy === true || !!ev.continuation_turn_id;
      n.busy = awaitingReplacement;
      n.activeTurnId = awaitingReplacement ? (ev.continuation_turn_id || s.activeTurnId || null) : null;
      n.control = awaitingReplacement
        ? { ...(s.control || {}), ...ev, kind: controlKind || 'steer', status: 'steering', source: 'control_event' }
        : null;
      const terminalTurnId = String(ev.turn_id || ev.task_id || '');
      if (!s.confirm || String(s.confirm.turn_id || '') === terminalTurnId) n.confirm = null;
    }
  }
  return n;
}

// 事件流 → chat 形消息模型:[{kind:'user'|'memory'|'chain'|'answer'|'report'|'condense'|'error', ...}]
function wwDeriveItems(events, busy) {
  const items = [];
  let chain = null;            // 当前折叠中的工具链 {kind:'chain', chain:[...]}
  let openIdx = {};            // tool 名 → chain 数组下标(就近配对)
  let currentThinkingMode = 'standard';
  let currentTurnId = null;
  const flushChain = () => { chain = null; openIdx = {}; };
  // Codex MCP events prefix the trusted server; confirmation events do not.
  // Do not strip arbitrary namespaces or suffix-match an untrusted server.
  const toolKey = name => String(name || '').replace(/^guanlan\.(ww_[A-Za-z0-9_]+)$/, '$1');
  const findToolRow = (tool, turnId, predicate) => {
    for (let i = items.length - 1; i >= 0; i--) {
      const prior = items[i];
      if (prior.kind !== 'chain') continue;
      for (let j = prior.chain.length - 1; j >= 0; j--) {
        const row = prior.chain[j];
        if (toolKey(row.name) === toolKey(tool) && (!turnId || row.turnId === turnId) && predicate(row)) return row;
      }
    }
    return null;
  };
  const finishUnresolved = (row) => {
    if (row.status !== 'running' && row.status !== 'pending') return;
    if (row.awaitingApproval) {
      row.status = 'cancelled';
      row.notExecuted = true;
      row.result = '回合已结束，确认未完成；工具未执行';
    } else {
      row.status = 'incomplete';
      row.notExecuted = false;
      row.result = '回合已结束，未收到工具执行结果；是否执行尚未确认，请勿自动重试';
    }
  };
  const appendConversationArtifact = (artifact, eventId, tool, turnId) => {
    if (!artifact || typeof artifact !== 'object') return;
    if (artifact.kind === 'workflow') {
      const workflow = wwNormalizeWorkflowArtifact(artifact.payload);
      if (workflow) items.push({ kind: 'workflow', id: 'wf' + eventId, tool: tool || '', turnId, ...workflow });
      else items.push({ kind: 'warning', id: 'wfw' + eventId, note: '工作流产物缺少有效空间或图标识，未自动打开' });
      return;
    }
    if (artifact.kind === 'media_image') {
      const media = wwNormalizeMediaImage(artifact.payload);
      if (media) items.push({ kind: 'media_image', id: 'm' + eventId,
        tool: tool || '', turnId, ...media });
      else items.push({ kind: 'warning', id: 'mw' + eventId,
        note: '图片产物已拦截：缺少合法的媒体标识或图片类型' });
      return;
    }
    if (artifact.kind === 'file_document') {
      const document = wwNormalizeFileDocument(artifact.payload);
      if (document) items.push({ kind: 'file_document', id: 'd' + eventId,
        tool: tool || '', turnId, ...document });
      else items.push({ kind: 'warning', id: 'dw' + eventId,
        note: '文件产物已拦截：缺少合法的文件编号、类型或大小' });
      return;
    }
    if (artifact.kind === 'analysis_bundle') {
      const bundle = wwNormalizeAnalysisBundle(artifact.payload);
      items.push({ kind: 'analysis_bundle', id: 'b' + eventId, title: bundle.title,
        imageCount: bundle.images.length, documentCount: bundle.documents.length });
      bundle.images.forEach((media, index) => items.push({
        kind: 'media_image', id: 'bm' + eventId + '-' + index,
        tool: tool || '', turnId, bundleTitle: bundle.title, ...media,
      }));
      bundle.documents.forEach((document, index) => items.push({
        kind: 'file_document', id: 'bd' + eventId + '-' + index,
        tool: tool || '', turnId, bundleTitle: bundle.title, ...document,
      }));
      if (bundle.rejected) items.push({ kind: 'warning', id: 'bw' + eventId,
        note: `分析产物中有 ${bundle.rejected} 个附件未通过前端安全校验，已隐藏` });
    }
  };
  events.forEach(ev => {
    const eventTurnId = ev.turn_id || ev.task_id || null;
    if (ev.type === 'task_update' && !ev.kind && ev.status === 'running' && eventTurnId) currentTurnId = eventTurnId;
    const terminalStatus = String(ev.status || '').toLowerCase();
    const terminalTurn = ((ev.type === 'task_update' && !ev.kind) || ev.type === 'control_event')
      && (terminalStatus === 'done' || terminalStatus === 'cancelled'
        || (terminalStatus === 'error' && ev.terminal !== false));
    if (terminalTurn && eventTurnId) {
      items.forEach(it => {
        if (it.kind === 'chain') it.chain.forEach(row => {
          if (row.turnId === eventTurnId) finishUnresolved(row);
        });
      });
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
    } else if (ev.type === 'confirm_request') {
      // A confirmation instance is identified by the full server tuple. Never
      // resolve a newer request merely because it invokes the same tool.
      if (eventTurnId && ev.confirm_id && ev.tool) {
        const row = findToolRow(ev.tool, eventTurnId, r => r.status === 'running');
        if (row) {
          row.status = 'pending';
          row.confirmId = String(ev.confirm_id);
          row.awaitingApproval = true;
          row.result = '等待确认，尚未执行';
        }
      }
    } else if (ev.type === 'confirm_resolved') {
      const confirmId = String(ev.confirm_id || '');
      const row = eventTurnId && confirmId && ev.tool
        ? findToolRow(ev.tool, eventTurnId, r => r.confirmId === confirmId && !r.resultReceived)
        : null;
      if (row) {
        const choice = String(ev.choice || '').toLowerCase();
        if (['y', 'a', 'yes', 'always'].includes(choice)) {
          row.awaitingApproval = false;
          row.notExecuted = false;
          // A late approval event after a terminal turn cannot reopen a spinner.
          if (row.status === 'pending') row.status = 'running';
          else if (row.status === 'cancelled') row.status = 'incomplete';
          row.result = row.status === 'running' ? '已确认，等待工具执行结果' : '已确认，但回合已结束，执行结果未确认';
        } else {
          row.status = 'cancelled';
          row.awaitingApproval = false;
          row.notExecuted = true;
          row.result = ev.reason || (choice === 'timeout'
            ? '确认已超时，工具未执行' : '确认已取消或拒绝，工具未执行');
        }
      }
    } else if (ev.type === 'tool_result') {
      const at = chain ? openIdx[ev.tool] : null;
      const row = findToolRow(ev.tool, eventTurnId || currentTurnId, r => !r.resultReceived
        && ['running', 'pending', 'incomplete', 'cancelled'].includes(r.status));
      if (row) {
        // A real receipt is authoritative if it arrives after a terminal event.
        // A denied-call failure receipt preserves the known "not executed" state.
        const denied = row.status === 'cancelled' && row.notExecuted && !ev.ok;
        row.status = denied ? 'cancelled' : (ev.ok ? 'done' : 'fail');
        row.resultReceived = true;
        row.awaitingApproval = false;
        if (!denied) row.notExecuted = false;
        row.result = ev.summary || row.result || '';
        row.t = row._ts && ev.ts ? Math.max(0, (new Date(ev.ts) - new Date(row._ts)) / 1000) : 0;
        if (chain && at != null && chain.chain[at] === row) delete openIdx[ev.tool];
      }
      if (ev.artifact && ev.artifact.kind === 'report_md') {
        items.push({ kind: 'report', id: 'r' + ev.id, path: ev.artifact.payload.path,
          code: ev.artifact.payload.code, name: ev.artifact.payload.name || ev.artifact.payload.code });
      }
      if (ev.artifact && (ev.artifact.kind === 'workflow' || ev.artifact.kind === 'media_image'
          || ev.artifact.kind === 'file_document'
          || ev.artifact.kind === 'analysis_bundle')) {
        appendConversationArtifact(
          ev.artifact, ev.id, ev.tool, eventTurnId || currentTurnId,
        );
      }
    } else if (ev.type === 'artifact' && ev.artifact) {
      // 兼容仅投递 artifact 的 SSE 事件；所有附件仍走同一组 opaque-id 校验。
      appendConversationArtifact(
        ev.artifact, ev.id, ev.tool, eventTurnId || currentTurnId,
      );
    } else if (ev.type === 'agent_delta' && ev.text) {
      flushChain();              // 文本到来 = 这一段工具链收束
      const last = items[items.length - 1];
      const answerTurnId = eventTurnId || currentTurnId;
      // Deltas are transport fragments, not paragraphs. Preserve only the
      // whitespace supplied by the model, for both live SSE and snapshot replay.
      if (last && last.kind === 'answer' && last.turnId === answerTurnId) last.text += ev.text;
      else items.push({ kind: 'answer', id: 'a' + ev.id, text: ev.text,
        thinkingMode: currentThinkingMode, turnId: answerTurnId });
    } else if (ev.type === 'condensation') {
      items.push({ kind: 'condense', id: 'k' + ev.id, summary: ev.summary || '' });
    } else if (ev.type === 'memory_flush') {
      items.push({ kind: 'memory_flush', id: 'mf' + ev.id,
        note: ev.note || '关键前情已写入本会话长期笔记' });
    } else if (ev.type === 'memory_recall' && Number(ev.count || 0) > 0) {
      items.push({ kind: 'memory', id: 'mr' + ev.id, count: Number(ev.count || 0),
        hits: Array.isArray(ev.hits) ? ev.hits : [], turnId: eventTurnId || currentTurnId });
    } else if (ev.type === 'task_update' && ev.status === 'cancelled' && !ev.kind) {
      const cancelledTurnId = ev.task_id || ev.turn_id || null;
      if (cancelledTurnId) {
        for (let i = items.length - 1; i >= 0; i--) {
          if (items[i].kind === 'answer' && items[i].turnId === cancelledTurnId) { items[i].interrupted = true; break; }
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
  if (!busy) items.forEach(it => { if (it.kind === 'chain') it.chain.forEach(finishUnresolved); });
  return items;
}

function wwConnect(sid, dispatch, generation) {
  const streamGeneration = Number(generation || 0);
  const es = new EventSource(WW_API + '/console/stream/' + sid);
  es.addEventListener('snapshot', (m) => {
    try {
      const d = JSON.parse(m.data);
      dispatch({ type: 'snapshot', sid, generation: streamGeneration,
        meta: d.meta, events: d.events || [], runtime: d.runtime || null,
        evidence: Array.isArray(d.evidence) ? d.evidence : [] });
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
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body || {}),
    });
    let d = {};
    try { d = await r.json(); } catch (e) {}
    return { ...d, ok: r.ok && d.ok !== false, http_status: r.status,
      reason: d.reason || d.message || (r.ok ? '' : ('HTTP ' + r.status)) };
  } catch (e) { return { ok: false, http_status: 0, reason: fallbackReason }; }
}

async function wwChannelStatus(sid) {
  const safeSid = wwChannelSafeId(sid);
  if (!safeSid) return wwChannelUnavailableStatus('');
  try {
    const r = await fetch(WW_API + '/console/channels/session/' + encodeURIComponent(safeSid));
    if (r.status === 404) return wwChannelUnavailableStatus(safeSid);
    let data = {};
    try { data = await r.json(); } catch (e) { data = {}; }
    if (!r.ok || !data || data.available !== true) {
      if (r.ok && data && data.available === false) {
        return { ...wwChannelUnavailableStatus(safeSid), ...data,
          ok: false, available: false, http_status: r.status };
      }
      const failure = wwChannelSafeFailure(r, data, '通道状态服务暂不可达');
      const errorCode = failure.error_code || 'channel_status_unreachable';
      return { ...wwChannelUnavailableStatus(safeSid), ...failure,
        transient_status_error: true, status_error: errorCode,
        error_code: errorCode, reason_code: errorCode };
    }
    return { ...data, ok: data.ok !== false, available: true, http_status: r.status };
  } catch (e) {
    return { ...wwChannelUnavailableStatus(safeSid),
      http_status: 0, transient_status_error: true,
      status_error: 'channel_status_unreachable', error_code: 'channel_status_unreachable',
      reason_code: 'channel_status_unreachable', reason: '通道状态服务暂不可达' };
  }
}

async function wwChannelRequestToken(sid, action, subjectId) {
  const safeSid = wwChannelSafeId(sid);
  const safeAction = typeof action === 'string' && WW_CHANNEL_TICKET_ACTIONS[action]
    ? action : '';
  const subjectWasSupplied = subjectId !== undefined && subjectId !== null && subjectId !== '';
  const safeSubject = subjectWasSupplied ? wwChannelSafeId(subjectId) : '';
  const subjectRequired = safeAction === 'telegram:submit-token'
    || safeAction.indexOf('telegram:resolve:') === 0;
  if (!safeSid || !safeAction || (subjectRequired && !safeSubject)
      || (subjectWasSupplied && !safeSubject)) {
    return { ok: false, http_status: 0, reason_code: 'channel_ticket_request_invalid',
      error_code: 'channel_ticket_request_invalid', reason: '安全操作参数无效' };
  }
  try {
    const requestBody = { action: safeAction };
    if (safeSubject) requestBody.subject_id = safeSubject;
    const r = await fetch(WW_API + '/console/channels/session/'
      + encodeURIComponent(safeSid) + '/request-token', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(requestBody),
    });
    return await wwChannelReadResponse(r, '安全操作票据服务暂不可达');
  } catch (e) {
    return { ok: false, http_status: 0, reason_code: 'channel_ticket_unreachable',
      error_code: 'channel_ticket_unreachable', reason: '安全操作票据服务暂不可达' };
  }
}

async function wwTelegramSubmitToken(sid, draftId, token, requestToken) {
  const safeSid = wwChannelSafeId(sid);
  const safeDraftId = wwChannelSafeId(draftId);
  let secret = typeof token === 'string' ? token : '';
  let ticket = wwChannelSafeTicket(requestToken);
  token = '';
  requestToken = '';
  let requestBody = null;
  try {
    if (!safeSid || !safeDraftId || !ticket || !WW_TELEGRAM_TOKEN_EXACT_RE.test(secret)) {
      return { ok: false, http_status: 0, reason_code: 'telegram_token_submission_invalid',
        error_code: 'telegram_token_submission_invalid', reason: 'Telegram Bot Token 格式无效' };
    }
    requestBody = JSON.stringify({
      sid: safeSid, draft_id: safeDraftId, token: secret, request_token: ticket,
    });
    const r = await fetch(WW_API + '/console/channels/telegram/token', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: requestBody,
    });
    return await wwChannelReadResponse(r, 'Telegram Token 安全提交服务暂不可达');
  } catch (e) {
    return { ok: false, http_status: 0, reason_code: 'telegram_token_submission_unreachable',
      error_code: 'telegram_token_submission_unreachable', reason: 'Telegram Token 安全提交服务暂不可达' };
  } finally {
    secret = '';
    ticket = '';
    requestBody = null;
  }
}

async function wwChannelManage(sid, action, requestToken) {
  const safeSid = wwChannelSafeId(sid);
  const safeAction = typeof action === 'string' ? WW_CHANNEL_MANAGE_ACTIONS[action] : '';
  let ticket = wwChannelSafeTicket(requestToken);
  requestToken = '';
  try {
    if (!safeSid || !safeAction || !ticket) {
      return { ok: false, http_status: 0, reason_code: 'channel_action_invalid',
        error_code: 'channel_action_invalid', reason: '通道管理参数无效' };
    }
    const r = await fetch(WW_API + '/console/channels/session/'
      + encodeURIComponent(safeSid) + '/' + safeAction, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ request_token: ticket }),
    });
    return await wwChannelReadResponse(r, '通道管理服务暂不可达');
  } catch (e) {
    return { ok: false, http_status: 0, reason_code: 'channel_action_unreachable',
      error_code: 'channel_action_unreachable', reason: '通道管理服务暂不可达' };
  } finally {
    ticket = '';
  }
}

async function wwChannelResolveDelivery(sid, itemId, action, requestToken) {
  const safeSid = wwChannelSafeId(sid);
  const safeItemId = wwChannelSafeId(itemId);
  const safeAction = typeof action === 'string' ? WW_CHANNEL_RESOLUTION_ACTIONS[action] : '';
  let ticket = wwChannelSafeTicket(requestToken);
  requestToken = '';
  try {
    if (!safeSid || !safeItemId || !safeAction || !ticket) {
      return { ok: false, http_status: 0, reason_code: 'channel_action_invalid',
        error_code: 'channel_action_invalid', reason: '投递处理参数无效' };
    }
    const r = await fetch(WW_API + '/console/channels/deliveries/'
      + encodeURIComponent(safeItemId) + '/resolve', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ sid: safeSid, action: safeAction, request_token: ticket }),
    });
    return await wwChannelReadResponse(r, '投递处理服务暂不可达');
  } catch (e) {
    return { ok: false, http_status: 0, reason_code: 'channel_delivery_unreachable',
      error_code: 'channel_delivery_unreachable', reason: '投递处理服务暂不可达' };
  } finally {
    ticket = '';
  }
}

function wwSteer(sid, text, thinkingMode) {
  return wwTurnControl('/console/steer', {
    sid, text, thinking_mode: thinkingMode === 'pro' ? 'pro' : 'standard',
  }, '转向服务不可达');
}

function wwCancel(sid) {
  return wwTurnControl('/console/cancel', { sid }, '停止服务不可达');
}

async function wwConfirm(confirm, choice) {
  const pending = confirm && typeof confirm === 'object'
    ? confirm : { turn_id: String(confirm || '') };
  const turnId = String(pending.turn_id || '');
  const confirmId = String(pending.confirm_id || '');
  const actionTicketRequired = pending.action_ticket_required === true;
  if (!turnId || !confirmId) return { ok: false, reason: '确认实例无效，请等待状态刷新' };

  const body = { turn_id: turnId, confirm_id: confirmId, choice };
  let issued = null;
  let requestToken = '';
  try {
    if (actionTicketRequired) {
      const affirmative = ['y', 'a', 'yes', 'always'].indexOf(String(choice || '').toLowerCase()) >= 0;
      if (affirmative) {
        issued = await wwTurnControl('/console/confirm/request-token', {
          turn_id: turnId, confirm_id: confirmId,
        }, '安全确认票据服务不可达');
        requestToken = issued && typeof issued.request_token === 'string'
          ? issued.request_token : '';
        if (!issued || issued.ok !== true || !requestToken) {
          return { ...(issued || {}), ok: false,
            reason: (issued && issued.reason) || '未取得安全确认票据，请重试' };
        }
        body.request_token = requestToken;
      }
    }
    return wwTurnControl('/console/confirm', body, '确认服务不可达');
  } finally {
    if (Object.prototype.hasOwnProperty.call(body, 'request_token')) delete body.request_token;
    requestToken = '';
    issued = null;
  }
}

async function wwChannelConfirm(sid, turnId, toolName, choice) {
  const safeSid = wwChannelSafeId(sid);
  let pending = turnId && typeof turnId === 'object' ? { ...turnId } : null;
  const safeTool = typeof toolName === 'string' && WW_CHANNEL_CONFIRM_TOOLS[toolName]
    ? toolName : '';
  try {
    if (!safeSid || !pending || !safeTool
        || String(pending.turn_id || '') === '' || String(pending.confirm_id || '') === ''
        || (pending.tool && pending.tool !== safeTool)) {
      return { ok: false, http_status: 0, reason_code: 'channel_confirm_instance_required',
        error_code: 'channel_confirm_instance_required', reason: '安全确认实例已失效，请等待状态刷新' };
    }
    pending.tool = safeTool;
    pending.action_ticket_required = true;
    return await wwConfirm(pending, choice);
  } finally {
    pending = null;
    turnId = '';
    toolName = '';
    choice = '';
  }
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
async function wwWorkspaceIndustryReviews(wid, sid, assetId, limit) {
  const q = new URLSearchParams({
    session_id: String(sid || ''), snapshot_asset_id: String(assetId || ''),
    limit: String(Math.max(1, Math.min(100, Number(limit) || 100))),
  });
  try {
    const r = await fetch(WW_API + '/console/workspaces/' + encodeURIComponent(wid)
      + '/industry-reviews?' + q.toString());
    const d = await r.json().catch(() => ({}));
    return r.ok ? d : { ...d, ok: false, reason: d.reason || ('HTTP ' + r.status), http_status: r.status };
  } catch (e) { return { ok: false, reason: '行业资讯审核服务暂不可达' }; }
}
async function wwWorkspaceIndustryReviewSubmit(wid, payload) {
  try {
    const r = await fetch(WW_API + '/console/workspaces/' + encodeURIComponent(wid)
      + '/industry-reviews', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload || {}),
    });
    const d = await r.json().catch(() => ({}));
    return r.ok ? d : { ...d, ok: false, reason: d.reason || ('HTTP ' + r.status), http_status: r.status };
  } catch (e) { return { ok: false, reason: '行业资讯审核提交结果未知；请重新加载候选核对 revision' }; }
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

// console-channel.jsx 作为后续独立脚本加载；显式挂到 window，避免依赖
// Babel 对顶层 function 声明的实现细节。这里只暴露窄 API，不暴露任何状态。
Object.assign(window, {
  wwChannelStatus,
  wwChannelRequestToken,
  wwTelegramSubmitToken,
  wwChannelManage,
  wwChannelResolveDelivery,
  wwChannelConfirm,
  wwLooksLikeTelegramToken,
});

window.WW = { API: WW_API, TOOL_CN: WW_TOOL_CN, PAGES: WW_PAGES, GENERAL_WORKSPACE: WW_GENERAL_WORKSPACE,
              initState: wwInitState, apply: wwApply, connect: wwConnect,
              send: wwSend, steer: wwSteer, cancel: wwCancel,
              confirm: wwConfirm, sessions: wwSessions, newSession: wwNewSession,
              updateSession: wwUpdateSession, archiveSession: wwArchiveSession,
              restoreSession: wwRestoreSession, workspaces: wwWorkspaces, newWorkspace: wwNewWorkspace,
              updateWorkspace: wwUpdateWorkspace, workspaceFiles: wwWorkspaceFiles,
              workspaceFile: wwWorkspaceFile, workspaceAsset: wwWorkspaceAsset,
              workspaceAgentRuns: wwWorkspaceAgentRuns, workspaceAssets: wwWorkspaceAssets,
              workspaceIndustryReviews: wwWorkspaceIndustryReviews,
              workspaceIndustryReviewSubmit: wwWorkspaceIndustryReviewSubmit,
              workspaceSeats: wwWorkspaceSeats, canonicalSecurityCode: wwCanonicalSecurityCode,
              chooseResearchFolder: wwChooseResearchFolder, deriveItems: wwDeriveItems,
              dataAvailability: wwDataAvailability,
              sourceAvailability: wwSourceAvailability,
              mediaUrl: wwMediaUrl, documentUrl: wwDocumentUrl, normalizeWorkflowArtifact: wwNormalizeWorkflowArtifact,
               normalizeMediaImage: wwNormalizeMediaImage,
               normalizeFileDocument: wwNormalizeFileDocument,
               channelStatus: wwChannelStatus,
               channelRequestToken: wwChannelRequestToken,
              telegramSubmitToken: wwTelegramSubmitToken,
              channelManage: wwChannelManage,
              channelResolveDelivery: wwChannelResolveDelivery,
              channelConfirm: wwChannelConfirm,
              looksLikeTelegramToken: wwLooksLikeTelegramToken,
              seatsDecisions: wwSeatsDecisions };
