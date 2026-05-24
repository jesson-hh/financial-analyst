// 观澜 · A 股 AI 助手 — 真交互原型 v2
// 重构: 多会话 + localStorage 持久化 + 多轮上下文跟踪 + 可插拔 agent 适配器

const { useState, useEffect, useRef, useReducer, useCallback, useMemo } = React;

// ───────────────────────── 静态数据 ─────────────────────────

const WATCHLIST = [
  { name: '宁德时代', code: '300750', price: 325.10, delta: 2.21,  spark: [240,252,261,270,275,280,292,305,312,325] },
  { name: '贵州茅台', code: '600519', price: 1684.0, delta: -0.42, spark: [1720,1690,1700,1685,1690,1684] },
  { name: '比亚迪',   code: '002594', price: 281.40, delta: 1.68,  spark: [262,265,268,272,275,278,281] },
  { name: '中际旭创', code: '300308', price: 142.55, delta: 4.12,  spark: [125,128,135,140,142] },
  { name: '隆基绿能', code: '601012', price: 17.84,  delta: -1.06, spark: [19.5,19.1,18.6,18.2,18.0,17.84] },
  { name: '招商银行', code: '600036', price: 37.92,  delta: 0.32,  spark: [37.2,37.5,37.6,37.8,37.7,37.9,37.92] },
];

const ALERTS = [
  { id: 'a1', name: '贵州茅台', rule: '跌破 1,200', cur: '1,684',  pct: 28, far: '+40.3%' },
  { id: 'a2', name: '宁德时代', rule: '日涨 ≥ 5%',  cur: '+2.21%', pct: 44, far: '剩 2.79 pct', hot: true },
  { id: 'a3', name: '比亚迪',   rule: '涨破 300',   cur: '281.40', pct: 92, far: '剩 6.62%' },
];

const TOOLS_META = [
  { name: 'stock_brief',       cn: '速览',     cat: '行情', cost: 'seconds', desc: '一次返回行情+行业+产业链+新闻+情绪+资金流' },
  { name: 'quote_lookup',      cn: '估值',     cat: '行情', cost: 'instant', desc: '日线 EOD / PE / 市值' },
  { name: 'realtime_quote',    cn: '实时',     cat: '行情', cost: 'seconds', desc: '盘中实时价 / 盘口' },
  { name: 'alert_add',         cn: '加盯盘',   cat: '盯盘', cost: 'instant', desc: 'price/pct above|below 四种条件' },
  { name: 'alert_list',        cn: '列盯盘',   cat: '盯盘', cost: 'instant', desc: '查看已设规则' },
  { name: 'alert_remove',      cn: '撤盯盘',   cat: '盯盘', cost: 'instant', desc: '取消某规则' },
  { name: 'ths_fund_flow',     cn: '资金流',   cat: '主力', cost: 'seconds', desc: '同花顺 个股/概念/行业/大单' },
  { name: 'fund_flow_change',  cn: '加减仓',   cat: '主力', cost: 'instant', desc: '跨日主力对比' },
  { name: 'iwencai_search',    cn: '问财选股', cat: '主力', cost: 'seconds', desc: '自然语言筛选, 如 PE<20 且 ROE>15%' },
  { name: 'ths_concept_board', cn: '概念发布', cat: '主力', cost: 'seconds', desc: '最新概念板块' },
  { name: 'news_query',        cn: '查新闻',   cat: '新闻', cost: 'instant', desc: '本地 FTS5 全文检索' },
  { name: 'news_collect',      cn: '刷新闻',   cat: '新闻', cost: 'seconds', desc: '抓东方财富/新浪/雪球/同花顺' },
  { name: 'run_report',        cn: '跑研报',   cat: '研究', cost: 'minutes', desc: '深度研报 5-8 分钟, 星级评级+目标价' },
  { name: 'chain_for',         cn: '产业链',   cat: '研究', cost: 'instant', desc: '上下游 / 同行' },
  { name: 'stocks_show',       cn: '研究档案', cat: '研究', cost: 'instant', desc: '历史研究时间线' },
  { name: 'industry_show',     cn: '行业分类', cat: '研究', cost: 'instant', desc: '申万分类' },
  { name: 'mainline_radar',    cn: '主线雷达', cat: '研究', cost: 'seconds', desc: '月级板块轮动' },
  { name: 'morning_brief',     cn: '晨会',     cat: '研究', cost: 'seconds', desc: '盘前全市场扫描' },
  { name: 'dream_review',      cn: '反思',     cat: '研究', cost: 'instant', desc: '记忆提案' },
  { name: 'alpha_bench',       cn: '跑因子',   cat: '因子', cost: 'minutes', desc: '442 因子基准' },
  { name: 'alpha_snapshot',    cn: '因子快照', cat: '因子', cost: 'seconds', desc: 'top-N 因子' },
  { name: 'alpha_list',        cn: '因子库',   cat: '因子', cost: 'instant', desc: 'alpha101 / gtja191 / qlib158' },
  { name: 'alpha_show',        cn: '因子详情', cat: '因子', cost: 'instant', desc: '某因子公式+论文' },
  { name: 'factor_test',       cn: '测新因子', cat: '因子', cost: 'minutes', desc: '现搭自定义因子表达式 → 真算 RankIC/ICIR' },
  { name: 'alpha_compare',     cn: '比因子',   cat: '因子', cost: 'minutes', desc: '并排对比多个因子 IC/ICIR/健康分类' },
  { name: 'watchlist_show',    cn: '自选',     cat: '账户', cost: 'instant', desc: '雪球自选股' },
  { name: 'fund_snapshot',     cn: '基金总览', cat: '账户', cost: 'instant', desc: '蛋卷基金' },
  { name: 'fund_holdings',     cn: '基金持仓', cat: '账户', cost: 'instant', desc: '基金持仓明细' },
];

// ───────────────────────── 持久化 ─────────────────────────

const LS_KEY = 'guanlan:state:v2';

function loadPersisted() {
  try {
    const raw = localStorage.getItem(LS_KEY);
    if (!raw) return null;
    return JSON.parse(raw);
  } catch (e) {
    console.warn('载入会话失败:', e);
    return null;
  }
}

function savePersisted(state) {
  try {
    // 只保存可序列化部分
    const toSave = {
      mode: state.mode,
      model: state.model,
      backendModel: state.backendModel,
      watch: state.watch,
      autoApproved: state.autoApproved,
      useRealLLM: state.useRealLLM,
      theme: state.theme,
      watchlist: state.watchlist,
      sessions: state.sessions.map(s => ({
        id: s.id, title: s.title, createdAt: s.createdAt, updatedAt: s.updatedAt,
        context: s.context,
        // 只保存消息的核心字段, 不保存正在 stream 的临时态
        messages: s.messages.map(m => {
          if (m.kind === 'chain') {
            return { ...m, chain: m.chain.map(c => ({ ...c, status: c.status === 'running' ? 'cancelled' : c.status })) };
          }
          return m;
        }),
      })),
      currentSessionId: state.currentSessionId,
    };
    localStorage.setItem(LS_KEY, JSON.stringify(toSave));
  } catch (e) {
    console.warn('保存会话失败:', e);
  }
}

// ───────────────────────── reducer ─────────────────────────

const newBackendSid = () => 'sid_' + Date.now() + '_' + Math.random().toString(36).slice(2, 7);

const newSession = (title = '新对话') => {
  const id = 'sess_' + Date.now() + '_' + Math.random().toString(36).slice(2, 7);
  return {
    id, backendSid: id,    // 后端会话 key; /clear 时旋转它 → 后端起全新 agent
    title, createdAt: Date.now(), updatedAt: Date.now(),
    messages: [], context: null,
  };
};

function makeInitialState() {
  const loaded = loadPersisted();
  // 后端开关 — 由 index.html 注释/反注释那一行设置
  const backendUrl = (typeof window !== 'undefined' && window.GUANLAN_BACKEND) || null;
  const base = {
    backendUrl,
    // 服务端发现的 model 列表 + 当前选中的后端 model
    models: [],
    backendModel: loaded?.backendModel || null,
    // 后端 /alerts 拉到的规则; null = 未拉过 (此时用写死的 ALERTS)
    liveAlerts: null,
    // /quotes 拉到的实时价 — { CODE: { price, changePercent, ... } }
    liveQuotes: null,
    // /quotes 拉到的指数 — { SH000001: {...}, SZ399001: {...}, SZ399006: {...} }
    indices: null,
    // 最近一次真实触发的盯盘 (来自 /alerts/check 或 mock 假触发); null = 暂无
    lastFired: null,
    // 自选股清单 — 可增删, 持久化; 默认给一份起步清单
    watchlist: (loaded && Array.isArray(loaded.watchlist)) ? loaded.watchlist : WATCHLIST.map(w => ({ code: w.code, name: w.name })),
  };
  if (loaded && loaded.sessions && loaded.sessions.length > 0) {
    return {
      ...base,
      mode: loaded.mode || 'default',
      model: loaded.model || 'qwen3.5-plus',
      watch: loaded.watch || { on: true, interval: 5 },
      tokens: 0,
      autoApproved: loaded.autoApproved || ['news_query', 'quote_lookup'],
      useRealLLM: loaded.useRealLLM ?? false,
      theme: loaded.theme || 'light',
      sessions: loaded.sessions,
      currentSessionId: loaded.currentSessionId && loaded.sessions.find(s => s.id === loaded.currentSessionId)
        ? loaded.currentSessionId : loaded.sessions[0].id,
      status: 'idle',
      activeRound: null,
      queuedInput: '',
      confirm: null,
      toast: null,
      reportDrawer: null,
    };
  }
  const first = newSession();
  return {
    ...base,
    mode: 'default', model: 'qwen3.5-plus',
    watch: { on: true, interval: 5 }, tokens: 0,
    autoApproved: ['news_query', 'quote_lookup'],
    useRealLLM: false,
    theme: 'light',
    sessions: [first],
    currentSessionId: first.id,
    status: 'idle', activeRound: null, queuedInput: '',
    confirm: null, toast: null, reportDrawer: null,
  };
}

// 帮助函数: 操作当前 session 内的 messages
function updateCurrentSession(state, updater) {
  return updateSessionById(state, state.currentSessionId, updater);
}

// 按 sid 更新指定会话 — agent 事件用它路由到「发起 run 的会话」, 而不是当前查看的会话
// (修复: 后台研报 run 与其它会话的提问串台)
function updateSessionById(state, sid, updater) {
  if (!sid) return state;
  return {
    ...state,
    sessions: state.sessions.map(s =>
      s.id === sid
        ? { ...s, ...updater(s), updatedAt: Date.now() }
        : s
    ),
  };
}

function reducer(s, a) {
  switch (a.type) {
    case 'set_mode':         return { ...s, mode: a.mode };
    case 'set_use_llm':      return { ...s, useRealLLM: a.value };
    case 'set_watch':        return { ...s, watch: { ...s.watch, ...a.watch } };
    case 'rename_session':   return updateCurrentSession(s, () => ({ title: (a.title || '').trim() || '新对话' }));
    case 'toggle_theme':     return { ...s, theme: s.theme === 'dark' ? 'light' : 'dark' };
    case 'set_alerts':       return { ...s, liveAlerts: a.alerts };
    case 'set_live_quotes':  return { ...s, liveQuotes: a.quotes };
    // 自选股增删 (按 code 去重, 大小写/前缀归一化由调用方保证)
    case 'add_watch': {
      const have = new Set(s.watchlist.map(w => w.code));
      const fresh = (a.items || []).filter(it => it && it.code && !have.has(it.code));
      if (fresh.length === 0) return s;
      return { ...s, watchlist: [...s.watchlist, ...fresh] };
    }
    case 'remove_watch': return { ...s, watchlist: s.watchlist.filter(w => w.code !== a.code) };
    case 'clear_watch':  return { ...s, watchlist: [] };
    case 'set_indices':      return { ...s, indices: a.quotes };
    case 'set_tokens':       return { ...s, tokens: a.tokens };
    case 'set_models':       return { ...s, models: a.models, backendModel: s.backendModel || (a.models?.[0]?.id) || null };
    case 'set_backend_model':return { ...s, backendModel: a.model };
    case 'open_report':    return { ...s, reportDrawer: { sym: a.sym, status: a.text ? 'done' : 'running', step: REPORT_STEPS.length, text: a.text || '', startedAt: a.text ? null : (a.startedAt || Date.now()) } };
    case 'advance_report': return s.reportDrawer ? { ...s, reportDrawer: { ...s.reportDrawer, ...a.patch } } : s;
    case 'close_report':   return { ...s, reportDrawer: null };
    // 把生成好的研报存成 transcript 里的卡片 (持久化), 关了抽屉也能再点开
    case 'save_report': {
      return updateSessionById(s, a.sid, (sess) => {
        const exists = sess.messages.some(m => m.kind === 'report' && m.path === a.path);
        if (exists) {
          return { messages: sess.messages.map(m =>
            (m.kind === 'report' && m.path === a.path) ? { ...m, text: a.text, sym: a.sym } : m) };
        }
        return { messages: [...sess.messages, { id: 'rep_' + Date.now(), role: 'ai', kind: 'report', sym: a.sym, text: a.text, path: a.path }] };
      });
    }
    case 'remove_auto':    return { ...s, autoApproved: s.autoApproved.filter(x => x !== a.name) };

    case 'new_session': {
      const n = newSession();
      return { ...s, sessions: [n, ...s.sessions], currentSessionId: n.id, status: 'idle', activeRound: null, queuedInput: '' };
    }
    // /clear — 原地清空当前会话 + 旋转后端 sid (后端起全新 agent, 真重置上下文)
    case 'clear_session': {
      return {
        ...updateCurrentSession(s, () => ({
          messages: [], context: null, title: '新对话', backendSid: newBackendSid(),
        })),
        status: 'idle', activeRound: null, queuedInput: '',
      };
    }
    // /compact — 后端已把历史总结好, 前端转录替换成一条摘要
    case 'compact_session': {
      return updateCurrentSession(s, () => ({
        messages: [{ id: 'sum_' + Date.now(), role: 'ai', kind: 'answer',
                     text: `（前情摘要 · 已压缩会话以节省上下文）\n\n${a.summary}` }],
      }));
    }
    // 注入一条 AI 信息消息 (/help 等)
    case 'inject_message': {
      return updateCurrentSession(s, (sess) => ({ messages: [...sess.messages, a.message] }));
    }
    // 合并后端磁盘拉到的历史会话 (按 id 去重, updatedAt 大者优先)
    case 'merge_sessions': {
      const byId = {};
      for (const sess of s.sessions) byId[sess.id] = sess;
      for (const conv of (a.sessions || [])) {
        if (!conv || !conv.id) continue;
        const ex = byId[conv.id];
        if (!ex || (conv.updatedAt || 0) > (ex.updatedAt || 0)) {
          byId[conv.id] = {
            id: conv.id, backendSid: conv.backendSid || conv.id,
            title: conv.title || '新对话',
            createdAt: conv.createdAt || Date.now(), updatedAt: conv.updatedAt || Date.now(),
            context: conv.context || null, messages: conv.messages || [],
          };
        }
      }
      const merged = Object.values(byId).sort((x, y) => (y.updatedAt || 0) - (x.updatedAt || 0));
      const stillThere = merged.some(x => x.id === s.currentSessionId);
      return { ...s, sessions: merged, currentSessionId: stillThere ? s.currentSessionId : (merged[0] ? merged[0].id : s.currentSessionId) };
    }
    case 'switch_session': {
      if (a.id === s.currentSessionId) return s;
      return { ...s, currentSessionId: a.id, status: 'idle', activeRound: null, queuedInput: '' };
    }
    case 'delete_session': {
      const idx = s.sessions.findIndex(x => x.id === a.id);
      if (idx < 0) return s;
      const remain = s.sessions.filter(x => x.id !== a.id);
      let nextSessions = remain;
      let nextCurrent = s.currentSessionId;
      if (remain.length === 0) {
        const n = newSession();
        nextSessions = [n];
        nextCurrent = n.id;
      } else if (a.id === s.currentSessionId) {
        nextCurrent = remain[Math.min(idx, remain.length - 1)].id;
      }
      return { ...s, sessions: nextSessions, currentSessionId: nextCurrent };
    }

    // ─ 对话操作 (作用于当前 session) ─

    case 'send_user': {
      // sid/chainId/answerId 都由 startAgent 预生成传入 → 事件路由到发起会话, 不串台
      const sid = a.sid || s.currentSessionId;
      const uid = 'u_' + Date.now() + '_' + Math.random();
      const cid = a.chainId;
      const next = updateSessionById(s, sid, (sess) => ({
        // 自动标题: 仅当还是默认标题且无消息时 (用户改过名就不覆盖)
        title: (sess.messages.length === 0 && (!sess.title || sess.title === '新对话')) ? a.text.slice(0, 24) : sess.title,
        messages: [...sess.messages,
          { id: uid, role: 'user', text: a.text },
          { id: cid, role: 'ai', kind: 'chain', chain: [], kindLabel: '规划中…', kindKey: 'planning' },
        ],
      }));
      // 仅当「发起会话 == 当前查看会话」才动全局 status/activeRound (UI 指示器)
      if (sid === s.currentSessionId) {
        return { ...next, status: 'tool-running', activeRound: { chainId: cid, answerId: a.answerId, briefShown: false, sessionId: sid } };
      }
      return next;
    }

    case 'agent_plan': {
      return updateSessionById(s, a.sid, (sess) => ({
        messages: sess.messages.map(m => m.id === a.chainId
          ? { ...m, chain: a.chain.map(c => ({ ...c, status: 'pending' })), kindLabel: a.label, kindKey: a.intent }
          : m),
      }));
    }

    case 'agent_context': {
      return updateSessionById(s, a.sid, () => ({ context: a.context }));
    }

    case 'agent_tool_start': {
      const meta = a.meta || {};
      return updateSessionById(s, a.sid, (sess) => ({
        messages: sess.messages.map(m => {
          if (m.id !== a.chainId) return m;
          let chain = m.chain.slice();
          while (chain.length <= a.idx) {
            chain.push({ name: '?', cn: '?', args: '{}', t: 0, status: 'pending' });
          }
          chain[a.idx] = { ...chain[a.idx], ...meta, status: 'running' };
          return { ...m, chain };
        }),
      }));
    }

    case 'agent_tool_done': {
      return updateSessionById(s, a.sid, (sess) => ({
        messages: sess.messages.map(m => {
          if (m.id !== a.chainId) return m;
          let chain = m.chain.slice();
          while (chain.length <= a.idx) {
            chain.push({ name: '?', cn: '?', args: '{}', t: 0, status: 'pending' });
          }
          chain[a.idx] = { ...chain[a.idx], status: 'done', result: a.result };
          return { ...m, chain };
        }),
      }));
    }

    case 'agent_brief': {
      // 幂等: 该 run 的 brief 已插入就跳过
      const sess0 = s.sessions.find(x => x.id === a.sid);
      if (sess0 && sess0.messages.some(m => m.id === a.briefId)) return s;
      return updateSessionById(s, a.sid, (sess) => ({
        messages: [...sess.messages, { id: a.briefId, role: 'ai', kind: 'brief', sym: a.sym }],
      }));
    }

    case 'agent_answer_start': {
      const sess0 = s.sessions.find(x => x.id === a.sid);
      const exists = sess0 && sess0.messages.some(m => m.id === a.answerId);
      let next = s;
      if (!exists) {
        next = updateSessionById(s, a.sid, (sess) => ({
          messages: [...sess.messages, { id: a.answerId, role: 'ai', kind: 'answer', text: '' }],
        }));
      }
      if (a.sid === s.currentSessionId) {
        return { ...next, status: 'streaming', activeRound: { ...(next.activeRound || { chainId: a.chainId, sessionId: a.sid, briefShown: false }), answerId: a.answerId } };
      }
      return next;
    }

    case 'agent_answer_progress': {
      return updateSessionById(s, a.sid, (sess) => ({
        messages: sess.messages.map(m => m.id === a.answerId ? { ...m, text: a.text } : m),
      }));
    }

    case 'agent_done': {
      if (a.sid === s.currentSessionId) {
        return { ...s, status: 'idle', activeRound: null };
      }
      return s;
    }

    case 'agent_cancel': {
      const out = updateSessionById(s, a.sid, (sess) => ({
        messages: sess.messages.map(m => m.id === a.chainId
          ? { ...m, chain: m.chain.map(c => c.status === 'running' ? { ...c, status: 'cancelled' } : c) }
          : m),
      }));
      if (a.sid === s.currentSessionId) return { ...out, status: 'idle', activeRound: null };
      return out;
    }

    case 'cancel': {
      if (s.queuedInput) return { ...s, queuedInput: '' };
      if (s.confirm)     return { ...s, confirm: null, status: 'idle' };
      // 工具运行中的 cancel 由 agent.cancel() 触发 agent_cancel
      return s;
    }

    case 'queue':        return { ...s, queuedInput: a.text };
    case 'clear_queue':  return { ...s, queuedInput: '' };
    case 'prefill':      return { ...s, composerDraft: { text: a.text, nonce: (s.composerDraft?.nonce || 0) + 1 } };

    case 'request_confirm': return { ...s, status: 'confirming', confirm: a.confirm };
    case 'resolve_confirm': {
      const c = s.confirm;
      const out = { ...s, confirm: null, status: 'idle' };
      if (a.choice === 'a' && c && !s.autoApproved.includes(c.tool)) {
        out.autoApproved = [...s.autoApproved, c.tool];
      }
      // 同意跑 run_report 则开启报告抽屉 (真实触发在 ConfirmModal.resolve 里, 只触发一次)
      if ((a.choice === 'y' || a.choice === 'a') && c && c.tool === 'run_report') {
        const sym = c.sym || { code: '—', name: '深度研报', market: '', industry: '', mc: '' };
        out.reportDrawer = { sym, status: 'running', step: 0, text: '', startedAt: Date.now() };
      }
      return out;
    }

    case 'fire_toast':    return { ...s, toast: a.toast, lastFired: a.toast };
    case 'dismiss_toast': return { ...s, toast: null };

    default: return s;
  }
}

// ───────────────────────── 选择器 ─────────────────────────

const useCurrentSession = (s) => s.sessions.find(x => x.id === s.currentSessionId);

// ───────────────────────── 主入口 ─────────────────────────

function ObservatoryApp() {
  const [s, dispatch] = useReducer(reducer, null, makeInitialState);
  const agentRef = useRef(null);

  // 持久化
  const persistTimeout = useRef(null);
  useEffect(() => {
    clearTimeout(persistTimeout.current);
    persistTimeout.current = setTimeout(() => savePersisted(s), 300);
  }, [s.sessions, s.currentSessionId, s.mode, s.model, s.autoApproved, s.useRealLLM, s.watch, s.theme, s.watchlist, s.backendModel]);

  // 主题 -> body class
  useEffect(() => {
    if (s.theme === 'dark') document.body.classList.add('dark');
    else document.body.classList.remove('dark');
  }, [s.theme]);

  // 研报抽屉进度推进
  useEffect(() => {
    if (!s.reportDrawer || s.reportDrawer.status !== 'running') return;
    // 真后端: 不放假步骤 (后端是黑盒一次性算完, 假步骤会秒到 8/8 然后卡住). 抽屉里改放真·已用时计时器.
    if (s.backendUrl) return;
    const STEPS = REPORT_STEPS;
    if (s.reportDrawer.step < STEPS.length) {
      const t = setTimeout(() => {
        dispatch({ type: 'advance_report', patch: { step: s.reportDrawer.step + 1 } });
      }, 900);
      return () => clearTimeout(t);
    }
    // mock 模式 (无后端): 流式打出占位报告
    const targetText = buildReportText(s.reportDrawer.sym);
    let i = s.reportDrawer.text.length;
    const t = setInterval(() => {
      i += 6;
      if (i >= targetText.length) {
        dispatch({ type: 'advance_report', patch: { text: targetText, status: 'done' } });
        clearInterval(t);
      } else {
        dispatch({ type: 'advance_report', patch: { text: targetText.slice(0, i) } });
      }
    }, 40);
    return () => clearInterval(t);
  }, [s.reportDrawer?.status, s.reportDrawer?.step, s.backendUrl]);

  const currentSession = useCurrentSession(s);
  const messages = currentSession?.messages || [];
  const context = currentSession?.context || null;

  // 启动一个 agent run
  const startAgent = useCallback((text) => {
    // 本 run 的归属会话 + 消息 id 全部在这里预生成, 随每个事件携带 →
    // 事件路由到「发起 run 的会话」, 即使用户切到别的会话/后台研报在跑也不串台
    const sid = s.currentSessionId;
    const nonce = Date.now() + '_' + Math.random().toString(36).slice(2, 7);
    const chainId = 'c_' + nonce;
    const answerId = 'a_' + nonce;
    const briefId = 'b_' + nonce;
    dispatch({ type: 'send_user', text, sid, chainId, answerId });

    const sessionCtx = currentSession?.context || null;
    const useReal = s.useRealLLM;
    const agent = new window.GuanlanAgent({
      useRealLLM: useReal,
      backendUrl: s.backendUrl,                         // 看 index.html 的 window.GUANLAN_BACKEND
    });
    agentRef.current = agent;

    agent.run(text, sessionCtx, {
      onPlan: ({ chain, intent, label }) => {
        dispatch({ type: 'agent_plan', sid, chainId, chain, intent, label });
      },
      onContextUpdate: (ctx) => {
        dispatch({ type: 'agent_context', sid, context: ctx });
      },
      onToolStart: (idx, meta) => {
        dispatch({ type: 'agent_tool_start', sid, chainId, idx, meta });
      },
      onToolDone: (idx, result, name) => {
        dispatch({ type: 'agent_tool_done', sid, chainId, idx, result });
        // ③ run_report 是 minutes 工具, 真全文在这条 SSE 上. advance_report reducer 会在抽屉为 null 时自动忽略.
        if (name === 'run_report' && result) {
          const text = typeof result === 'string' ? result : (result.text || JSON.stringify(result, null, 2));
          dispatch({ type: 'advance_report', patch: { status: 'done', text, step: REPORT_STEPS.length } });
        }
      },
      onBrief: (sym) => {
        dispatch({ type: 'agent_brief', sid, briefId, sym });
        dispatch({ type: 'agent_answer_start', sid, chainId, answerId });
      },
      onReport: (d) => {
        // run_report 把全文写进了 .md; 抓全文 → 填抽屉 + 存成 transcript 卡片 (可重开)
        if (!d || !d.path || !s.backendUrl) return;
        fetch(`${s.backendUrl}/report?path=${encodeURIComponent(d.path)}`)
          .then(r => r.json())
          .then(rd => {
            if (!rd || !rd.ok || !rd.text) return;
            dispatch({ type: 'advance_report', patch: { status: 'done', text: rd.text, step: REPORT_STEPS.length } });
            // 从首行 "# 名称 (CODE) — ..." 解析标的; 解析不到就用文件名里的 code
            const m = rd.text.match(/^#\s*(.+?)\s*[（(]([A-Za-z]{2}\d{6})[)）]/);
            const codeFromPath = (d.path.match(/([A-Za-z]{2}\d{6})/) || [])[1] || '';
            const repSym = m ? { code: m[2], name: m[1].trim() } : { code: codeFromPath, name: codeFromPath || '研报' };
            dispatch({ type: 'save_report', sid, sym: repSym, text: rd.text, path: d.path });
          })
          .catch(e => console.warn('[guanlan] /report 抓取失败:', e));
      },
      onAnswerProgress: (textSoFar) => {
        // brief 卡片如果还没插, 这里也启动 answer
        dispatch({ type: 'agent_answer_start', sid, chainId, answerId });
        dispatch({ type: 'agent_answer_progress', sid, answerId, text: textSoFar });
      },
      onConfirmRequest: (d) => {
        // ④ 后端发起的确认请求 — 弹出 modal 等 y/n/a
        const toolName = d.tool;
        const meta = TOOLS_META.find(t => t.name === toolName);
        const argsObj = typeof d.args === 'string' ? (() => { try { return JSON.parse(d.args); } catch { return {}; } })() : (d.args || {});
        const argsText = typeof d.args === 'string' ? d.args : JSON.stringify(argsObj, null, 2);
        // run_report: 从 args.code 还原标的, 抽屉标题才正确 (否则 resolve 会默认成宁德时代)
        let sym = null;
        if (toolName === 'run_report' && argsObj.code) {
          const code = String(argsObj.code);
          const bare = code.replace(/^(SH|SZ|BJ)/i, '');
          const ctx = currentSession?.context;   // 用户通常先看了速览, 上下文里有中文名
          const ctxName = (ctx && ctx.code && String(ctx.code).replace(/^(SH|SZ|BJ)/i, '') === bare) ? ctx.name : null;
          const known = window.STOCK_DB && window.STOCK_DB[bare];
          sym = known || { code, name: ctxName || code, market: '', industry: '', mc: '' };
        }
        dispatch({ type: 'request_confirm', confirm: {
          turn_id: d.turn_id,
          tool: toolName,
          label: d.label || `${toolName}${meta ? ` · ${meta.cn}` : ''}`,
          detail: d.detail || `准备调用后端工具 ${toolName}。\n\n参数:\n${argsText}`,
          sym,
          fromBackend: true,
        }});
      },
      onDone: (d) => {
        if (d && d.tokens != null) dispatch({ type: 'set_tokens', tokens: d.tokens });
        dispatch({ type: 'agent_done', sid });
        if (agentRef.current === agent) agentRef.current = null;   // 别清掉更晚启动的 run
      },
      onCancel: () => {
        dispatch({ type: 'agent_cancel', sid, chainId });
        if (agentRef.current === agent) agentRef.current = null;
      },
      onError: (err) => {
        console.error('Agent error:', err);
        dispatch({ type: 'agent_done', sid });
        if (agentRef.current === agent) agentRef.current = null;
      },
    }, {
      sessionId: currentSession?.backendSid || s.currentSessionId,  // /clear 旋转后端上下文
      mode: s.mode,                                       // default / safe / auto — 同步给后端
      model: s.backendModel,                              // ⑤ 后端 model
    });
  }, [currentSession, s.useRealLLM, s.backendUrl, s.currentSessionId, s.mode, s.backendModel]);

  // 全局 ESC: 优先取消队列, 然后取消 confirm, 然后 cancel agent
  useEffect(() => {
    const onKey = (e) => {
      if (e.key === 'Escape') {
        e.preventDefault();
        if (s.queuedInput) { dispatch({ type: 'clear_queue' }); return; }
        if (s.confirm)     { dispatch({ type: 'resolve_confirm', choice: 'n' }); return; }
        if (agentRef.current) {
          agentRef.current.cancel();
          return;
        }
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [s.queuedInput, s.confirm]);

  // 排队冲刷
  useEffect(() => {
    if (s.status === 'idle' && s.queuedInput) {
      const t = setTimeout(() => {
        const text = s.queuedInput;
        dispatch({ type: 'clear_queue' });
        startAgent(text);
      }, 250);
      return () => clearTimeout(t);
    }
  }, [s.status, s.queuedInput, startAgent]);

  // ② 盯盘提醒 — 后端轮询 vs mock 假触发
  useEffect(() => {
    if (s.backendUrl) {
      // 真后端: 拉规则列表 + 评估触发
      let cancelled = false;
      const tick = async () => {
        try {
          const ar = await fetch(`${s.backendUrl}/alerts`).then(r => r.json());
          if (cancelled) return;
          if (ar && Array.isArray(ar.alerts)) dispatch({ type: 'set_alerts', alerts: ar.alerts });
          const cr = await fetch(`${s.backendUrl}/alerts/check`).then(r => r.json());
          if (cancelled) return;
          for (const f of (cr.fired || [])) {
            dispatch({ type: 'fire_toast', toast: {
              name: f.name || f.code || '盯盘',
              code: f.code || '',
              rule: f.rule || f.desc || '',
              cur: f.changePercent != null ? `${f.changePercent >= 0 ? '+' : ''}${f.changePercent}%` : (f.cur || ''),
              price: f.price != null ? f.price : '',
              vol: f.vol_ratio || f.vol || '',
              time: new Date().toTimeString().slice(0,5),
              fromBackend: true,
            }});
          }
        } catch (e) {
          console.warn('[guanlan] /alerts 轮询失败:', e);
        }
      };
      tick();
      const id = setInterval(tick, 30000);
      return () => { cancelled = true; clearInterval(id); };
    }
    // mock: 保留原假触发
    const fire = () => dispatch({ type: 'fire_toast', toast: {
      name: '中际旭创', code: '300308', rule: 'pct_above 4',
      cur: '+4.12%', price: 142.55, vol: '2.1', time: new Date().toTimeString().slice(0,5)
    }});
    const first = setTimeout(fire, 14000);
    const loop = setInterval(fire, 45000);
    return () => { clearTimeout(first); clearInterval(loop); };
  }, [s.backendUrl]);

  // ⑤ 拉 /models 供状态栏 picker 选择
  useEffect(() => {
    if (!s.backendUrl) return;
    let cancelled = false;
    fetch(`${s.backendUrl}/models`)
      .then(r => r.json())
      .then(d => {
        if (cancelled) return;
        const ms = Array.isArray(d?.models) ? d.models
                 : Array.isArray(d) ? d
                 : [];
        const norm = ms.map(m => typeof m === 'string' ? { id: m, name: m } : m).filter(m => m && m.id);
        dispatch({ type: 'set_models', models: norm });
      })
      .catch(e => console.warn('[guanlan] /models 拉取失败:', e));
    return () => { cancelled = true; };
  }, [s.backendUrl]);

  // ⑥ 自选股监控墙 — 4s 轮询 /quotes (清单来自 s.watchlist, 可增删)
  const watchCodes = (s.watchlist || []).map(w => w.code).join(',');
  useEffect(() => {
    if (!s.backendUrl || !watchCodes) return;
    let cancelled = false;
    const tick = async () => {
      try {
        const r = await fetch(`${s.backendUrl}/quotes?codes=${encodeURIComponent(watchCodes)}`);
        const d = await r.json();
        if (cancelled) return;
        if (d && d.quotes) dispatch({ type: 'set_live_quotes', quotes: d.quotes });
      } catch (e) {
        console.warn('[guanlan] /quotes 轮询失败:', e);
      }
    };
    tick();
    const id = setInterval(tick, 4000);
    return () => { cancelled = true; clearInterval(id); };
  }, [s.backendUrl, watchCodes]);

  // ⑦ 大盘指数 — 4s 轮询 /quotes (上证/深成/创业板, 必须带 sh/sz 前缀)
  useEffect(() => {
    if (!s.backendUrl) return;
    let cancelled = false;
    const tick = async () => {
      try {
        const r = await fetch(`${s.backendUrl}/quotes?codes=sh000001,sz399001,sz399006`);
        const d = await r.json();
        if (cancelled) return;
        if (d && d.quotes) dispatch({ type: 'set_indices', quotes: d.quotes });
      } catch (e) {
        console.warn('[guanlan] /quotes 指数轮询失败:', e);
      }
    };
    tick();
    const id = setInterval(tick, 4000);
    return () => { cancelled = true; clearInterval(id); };
  }, [s.backendUrl]);

  // ⑧ 历史会话: 启动时从后端磁盘拉取并合并 (浏览器缓存被清也能恢复)
  useEffect(() => {
    if (!s.backendUrl) return;
    let cancelled = false;
    fetch(`${s.backendUrl}/conversations`)
      .then(r => r.json())
      .then(d => {
        if (cancelled || !d || !d.ok || !Array.isArray(d.conversations)) return;
        if (d.conversations.length) dispatch({ type: 'merge_sessions', sessions: d.conversations });
      })
      .catch(e => console.warn('[guanlan] 拉历史会话失败:', e));
    return () => { cancelled = true; };
  }, [s.backendUrl]);

  // ⑨ 历史会话: 当前会话变动后 debounce 2s 存盘到后端 (空会话不存)
  useEffect(() => {
    if (!s.backendUrl) return;
    const sess = s.sessions.find(x => x.id === s.currentSessionId);
    if (!sess || sess.messages.length === 0) return;
    const t = setTimeout(() => {
      const payload = {
        id: sess.id, backendSid: sess.backendSid || sess.id,
        title: sess.title, createdAt: sess.createdAt, updatedAt: sess.updatedAt,
        context: sess.context,
        messages: sess.messages.map(m => m.kind === 'chain'
          ? { ...m, chain: (m.chain || []).map(c => ({ ...c, status: c.status === 'running' ? 'cancelled' : c.status })) }
          : m),
      };
      fetch(`${s.backendUrl}/conversations`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      }).catch(e => console.warn('[guanlan] 会话存盘失败:', e));
    }, 2000);
    return () => clearTimeout(t);
  }, [s.backendUrl, s.currentSessionId, currentSession?.updatedAt]);

  // ⌘K / ⌘N
  const [showCmdK, setShowCmdK] = useState(false);
  useEffect(() => {
    const onKey = (e) => {
      if (e.key === 'Escape' && showCmdK) { e.preventDefault(); setShowCmdK(false); return; }
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') { e.preventDefault(); setShowCmdK(true); }
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'n') { e.preventDefault(); dispatch({ type: 'new_session' }); }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [showCmdK]);

  return (
    <div className="paper-bg" style={{
      width: '100%', height: '100vh', display: 'flex', overflow: 'hidden',
      fontFamily: 'var(--sans)', color: 'var(--ink)', background: 'var(--paper)', position: 'relative',
    }}>
      <LeftRail s={s} dispatch={dispatch} startAgent={startAgent} />
      <main style={{ flex: 1, display: 'flex', flexDirection: 'column', minWidth: 0, borderRight: '1px solid var(--line)' }}>
        <TopBar s={s} session={currentSession} dispatch={dispatch} />
        <Transcript s={s} messages={messages} dispatch={dispatch} startAgent={startAgent} />
        <Composer s={s} context={context} dispatch={dispatch} startAgent={startAgent} onCmdK={() => setShowCmdK(true)} />
        <StatusBar s={s} dispatch={dispatch} onCmdK={() => setShowCmdK(true)} />
      </main>
      <RightRail s={s} session={currentSession} dispatch={dispatch} startAgent={startAgent} />

      {s.confirm && <ConfirmModal s={s} dispatch={dispatch} agentRef={agentRef} startAgent={startAgent} />}
      {showCmdK && <CmdKPalette onClose={() => setShowCmdK(false)} startAgent={startAgent} dispatch={dispatch} />}
      {s.toast && <AlertToast toast={s.toast} dispatch={dispatch} startAgent={startAgent} />}
      {s.reportDrawer && <ReportDrawer drawer={s.reportDrawer} dispatch={dispatch} backendUrl={s.backendUrl} />}
    </div>
  );
}

// ───────────────────────── 左栏 ─────────────────────────

function LeftRail({ s, dispatch, startAgent }) {
  const [mgrOpen, setMgrOpen] = useState(false);
  const sortedSessions = useMemo(() =>
    [...s.sessions].sort((a, b) => b.updatedAt - a.updatedAt),
    [s.sessions]
  );

  // ⑥ 实时价在拼接: 后端返 { CODE: { price, changePercent, ... } } — 允许 code 加 前缀 SH/SZ
  const liveOf = useCallback((code) => {
    if (!s.liveQuotes) return null;
    return s.liveQuotes[code]
        || s.liveQuotes[`SH${code}`]
        || s.liveQuotes[`SZ${code}`]
        || s.liveQuotes[`sh${code}`]
        || s.liveQuotes[`sz${code}`]
        || null;
  }, [s.liveQuotes]);

  const watchlist = (s.watchlist || []).map(r => {
    const live = liveOf(r.code);
    return {
      code: r.code, name: r.name || r.code,
      price: live && live.price != null ? live.price : null,
      delta: live && live.changePercent != null ? live.changePercent : null,
      live: !!live,
    };
  });

  // ② 盯盘规则 — 连后端就用真规则 (空则空), 仅 mock 模式用写死的 ALERTS
  const alerts = s.backendUrl
    ? (s.liveAlerts || []).map((a, i) => ({
        id: a.id || `alert_${i}`,
        name: a.name || a.code || '规则',
        rule: a.rule || a.desc || `${a.type || ''} ${a.value ?? ''}`.trim(),
        cur: a.cur || (a.changePercent != null ? `${a.changePercent >= 0 ? '+' : ''}${a.changePercent}%` : (a.price != null ? String(a.price) : '—')),
        pct: a.pct ?? 50,
        far: a.far || '',
        hot: !!a.hot,
      }))
    : ALERTS;

  return (
    <aside style={{ width: 248, borderRight: '1px solid var(--line)', display: 'flex', flexDirection: 'column', flexShrink: 0, background: 'rgba(241,234,217,0.55)', overflow: 'hidden' }}>
      <div style={{ padding: '20px 20px 14px', flexShrink: 0 }}>
        <Brandmark subtitle="A 股 AI 助手" small />
      </div>
      <div style={{ padding: '0 20px 14px', flexShrink: 0 }}>
        <button onClick={() => dispatch({ type: 'new_session' })} style={{
          width: '100%', padding: '9px 12px', background: 'var(--ink)', color: 'var(--paper)',
          border: 'none', fontFamily: 'var(--serif)', fontSize: 13, letterSpacing: '0.06em', cursor: 'pointer',
          display: 'flex', alignItems: 'center', justifyContent: 'space-between'
        }}>
          <span>＋ 新对话</span>
          <span className="mono" style={{ fontSize: 10, opacity: 0.5 }}>⌘ N</span>
        </button>
      </div>

      <div style={{ flex: 1, overflowY: 'auto', minHeight: 0 }}>
        <RailSection label="自选" right={
          <span style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <span className="mono" style={{ fontSize: 10, color: 'var(--ink-3)' }}>{watchlist.length}{s.backendUrl ? ' · live' : ''}</span>
            <span onClick={() => setMgrOpen(true)} className="hover-link mono" title="增删自选"
              style={{ fontSize: 10, color: 'var(--ink-2)', cursor: 'pointer' }}>＋ 管理</span>
          </span>
        }>
          {watchlist.length === 0 && (
            <div className="serif" style={{ fontSize: 11, color: 'var(--ink-3)', padding: '6px 20px' }}>暂无自选 · 点「＋ 管理」添加</div>
          )}
          {watchlist.map((r) => (
            <div key={r.code}
              className="hover-row watch-row"
              style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '6px 20px', position: 'relative' }}>
              <div style={{ flex: 1, minWidth: 0 }}>
                <div className="serif" style={{ fontSize: 12, color: 'var(--ink-1)', display: 'flex', alignItems: 'center', gap: 5 }}>
                  {r.name}
                  {r.live && <span style={{ width: 4, height: 4, borderRadius: '50%', background: 'var(--zhu)' }} title="后端实时价" />}
                </div>
                <div className="mono" style={{ fontSize: 9, color: 'var(--ink-3)' }}>{r.code}</div>
              </div>
              <div style={{ flexShrink: 0, textAlign: 'right', width: 52 }}>
                <div className="mono" style={{ fontSize: 11, color: 'var(--ink)' }}>{r.price != null ? Number(r.price).toFixed(2) : '—'}</div>
                <div className={'mono ' + ((r.delta ?? 0) >= 0 ? 'up' : 'down')} style={{ fontSize: 9 }}>{r.delta != null ? `${r.delta >= 0 ? '+' : ''}${Number(r.delta).toFixed(2)}%` : '—'}</div>
              </div>
              <span onClick={() => dispatch({ type: 'remove_watch', code: r.code })}
                className="watch-del" title="移除自选"
                style={{ position: 'absolute', right: 4, top: '50%', transform: 'translateY(-50%)', width: 16, height: 16, display: 'none', alignItems: 'center', justifyContent: 'center', background: 'var(--paper-2)', color: 'var(--yin)', fontSize: 11, cursor: 'pointer', borderRadius: 2 }}>×</span>
            </div>
          ))}
        </RailSection>

        <RailSection label="盯盘" right={<span className="mono" style={{ fontSize: 10, color: 'var(--yin)' }}>● {alerts.length} 活跃</span>}>
          {alerts.length === 0 && (
            <div className="serif" style={{ fontSize: 11, color: 'var(--ink-3)', padding: '6px 20px' }}>
              暂无盯盘规则 · 说"X 跌破 Y 提醒我"添加
            </div>
          )}
          {alerts.map((a) => (
            <div key={a.id} className="alert-row"
                 style={{ padding: '7px 20px', display: 'flex', alignItems: 'center', gap: 8, position: 'relative' }}
                 onMouseEnter={(e) => { const x = e.currentTarget.querySelector('.alert-del'); if (x) x.style.opacity = 1; }}
                 onMouseLeave={(e) => { const x = e.currentTarget.querySelector('.alert-del'); if (x) x.style.opacity = 0; }}>
              <span style={{ width: 6, height: 6, background: a.hot ? 'var(--yin)' : 'var(--ink-3)', flexShrink: 0, borderRadius: a.hot ? 0 : '50%' }} />
              <div style={{ flex: 1, minWidth: 0 }}>
                <div className="serif" style={{ fontSize: 12, color: 'var(--ink-1)' }}>{a.name}</div>
                <div className="mono" style={{ fontSize: 9, color: 'var(--ink-3)' }}>{a.rule}</div>
              </div>
              <div className="mono" style={{ fontSize: 10, color: 'var(--ink-2)' }}>{a.cur}</div>
              {s.backendUrl && (a.id || a.code) && (
                <span className="alert-del"
                      title="删除此盯盘"
                      style={{ opacity: 0, transition: 'opacity 0.15s', cursor: 'pointer',
                               color: 'var(--ink-3)', fontSize: 14, marginLeft: 4, lineHeight: 1 }}
                      onClick={async (e) => {
                        e.stopPropagation();
                        if (!confirm(`删除盯盘 "${a.name || a.code}" ?`)) return;
                        const ruleId = a.id || a.code;
                        try {
                          const r = await fetch(`${s.backendUrl}/alerts/${encodeURIComponent(ruleId)}`, { method: 'DELETE' });
                          const j = await r.json();
                          if (j.ok) {
                            // 局部刷新: 拉新的 /alerts 列表
                            const ar = await fetch(`${s.backendUrl}/alerts`).then(r => r.json());
                            if (ar && Array.isArray(ar.alerts)) dispatch({ type: 'set_alerts', alerts: ar.alerts });
                          } else {
                            alert(`删除失败: ${j.reason || 'unknown'}`);
                          }
                        } catch (err) {
                          alert(`删除失败: ${err.message}`);
                        }
                      }}>×</span>
              )}
            </div>
          ))}
        </RailSection>

        <RailSection label={`会话 · ${sortedSessions.length}`}>
          {sortedSessions.map((sess) => {
            const active = sess.id === s.currentSessionId;
            const msgs = sess.messages.length;
            const ago = timeAgo(sess.updatedAt);
            return (
              <SessionRow key={sess.id}
                sess={sess} active={active} msgs={msgs} ago={ago}
                onClick={() => dispatch({ type: 'switch_session', id: sess.id })}
                onDelete={() => {
                  if (s.backendUrl) fetch(`${s.backendUrl}/conversations/${encodeURIComponent(sess.id)}`, { method: 'DELETE' }).catch(() => {});
                  dispatch({ type: 'delete_session', id: sess.id });
                }}
              />
            );
          })}
        </RailSection>

        {s.backendUrl && <TrashSection backendUrl={s.backendUrl} dispatch={dispatch} sessions={s.sessions} />}
      </div>
      {mgrOpen && <WatchlistManager s={s} dispatch={dispatch} onClose={() => setMgrOpen(false)} />}
    </aside>
  );
}

// 自选管理 — 批量添加 (代码/名称, 多个用空格/逗号/换行) + 逐条删除 + 清空
function WatchlistManager({ s, dispatch, onClose }) {
  const [val, setVal] = useState('');
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState('');
  const wl = s.watchlist || [];

  const doAdd = useCallback(async () => {
    const tokens = val.split(/[\s,，、;；\n]+/).map(t => t.trim()).filter(Boolean);
    if (tokens.length === 0) return;
    if (!s.backendUrl) { setMsg('需连后端才能解析代码/名称'); return; }
    setBusy(true); setMsg('解析中…');
    const added = []; const failed = [];
    for (const tok of tokens) {
      try {
        const r = await fetch(`${s.backendUrl}/resolve?q=${encodeURIComponent(tok)}`);
        const d = await r.json();
        if (d && d.ok && d.code) added.push({ code: d.code, name: d.name || d.code });
        else failed.push(tok);
      } catch { failed.push(tok); }
    }
    if (added.length) dispatch({ type: 'add_watch', items: added });
    setBusy(false); setVal('');
    setMsg(`已加 ${added.length} 只${failed.length ? ` · 失败: ${failed.join(', ')}` : ''}`);
  }, [val, s.backendUrl, dispatch]);

  return (
    <div onClick={onClose} style={{ position: 'fixed', inset: 0, background: 'rgba(28,24,20,0.55)', backdropFilter: 'blur(2px)', display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 100, animation: 'fadeIn 200ms ease-out' }}>
      <div onClick={(e) => e.stopPropagation()} style={{ width: 460, maxHeight: '80vh', background: 'var(--paper)', border: '2px solid var(--ink)', display: 'flex', flexDirection: 'column' }}>
        <div style={{ padding: '14px 20px 12px', borderBottom: '1px solid var(--line)', display: 'flex', alignItems: 'center' }}>
          <span className="serif" style={{ fontSize: 16, fontWeight: 500, color: 'var(--ink)' }}>自选管理</span>
          <span className="mono" style={{ fontSize: 10, color: 'var(--ink-3)', marginLeft: 8 }}>{wl.length} 只</span>
          <span style={{ flex: 1 }} />
          <span onClick={onClose} style={{ cursor: 'pointer', color: 'var(--ink-3)', fontSize: 18, lineHeight: 1 }}>×</span>
        </div>
        <div style={{ padding: '14px 20px' }}>
          <textarea value={val} onChange={(e) => setVal(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) { e.preventDefault(); doAdd(); } }}
            placeholder="输入代码或名称, 多个用空格/逗号/换行分隔&#10;如: 300750 600519 立讯精密 002475"
            rows={3}
            style={{ width: '100%', boxSizing: 'border-box', border: '1px solid var(--ink-2)', background: 'var(--paper)', padding: '8px 10px', fontFamily: 'var(--mono)', fontSize: 12, color: 'var(--ink)', resize: 'vertical', outline: 'none' }} />
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginTop: 8 }}>
            <button onClick={doAdd} disabled={busy}
              style={{ background: 'var(--ink)', color: 'var(--paper)', border: 'none', padding: '6px 16px', fontFamily: 'var(--serif)', fontSize: 13, cursor: busy ? 'wait' : 'pointer', opacity: busy ? 0.6 : 1 }}>
              {busy ? '解析中…' : '＋ 添加'}
            </button>
            <span className="mono" style={{ fontSize: 10, color: 'var(--ink-3)' }}>{msg || '⌘/Ctrl+Enter 快捷添加'}</span>
          </div>
        </div>
        <div style={{ borderTop: '1px solid var(--line)', overflowY: 'auto', flex: 1, minHeight: 60 }}>
          {wl.length === 0 && <div className="serif" style={{ fontSize: 12, color: 'var(--ink-3)', padding: '16px 20px', textAlign: 'center' }}>暂无自选</div>}
          {wl.map((w) => (
            <div key={w.code} className="hover-row" style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '8px 20px', borderBottom: '1px solid var(--line-soft)' }}>
              <span className="serif" style={{ fontSize: 13, color: 'var(--ink-1)', flex: 1 }}>{w.name}</span>
              <span className="mono" style={{ fontSize: 10, color: 'var(--ink-3)' }}>{w.code}</span>
              <span onClick={() => dispatch({ type: 'remove_watch', code: w.code })}
                className="hover-pill" title="移除"
                style={{ cursor: 'pointer', color: 'var(--yin)', fontSize: 13, padding: '0 6px', lineHeight: 1.4 }}>×</span>
            </div>
          ))}
        </div>
        <div style={{ padding: '10px 20px', borderTop: '1px solid var(--line)', display: 'flex', alignItems: 'center' }}>
          <span onClick={() => { if (wl.length) dispatch({ type: 'clear_watch' }); }}
            className="hover-link mono" style={{ fontSize: 11, color: 'var(--ink-3)', cursor: 'pointer' }}>清空全部</span>
          <span style={{ flex: 1 }} />
          <button onClick={onClose} style={{ background: 'transparent', border: '1px solid var(--line)', color: 'var(--ink-1)', padding: '6px 16px', fontFamily: 'var(--serif)', fontSize: 13, cursor: 'pointer' }}>完成</button>
        </div>
      </div>
    </div>
  );
}

function SessionRow({ sess, active, msgs, ago, onClick, onDelete }) {
  const [hover, setHover] = useState(false);
  return (
    <div
      onClick={onClick}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      className="hover-row"
      style={{
        padding: '7px 20px', cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 8,
        borderLeft: active ? '2px solid var(--yin)' : '2px solid transparent',
        background: active ? 'rgba(168,57,45,0.05)' : 'transparent',
      }}>
      <div style={{ flex: 1, minWidth: 0 }}>
        <div className="serif" style={{ fontSize: 12.5, color: active ? 'var(--ink)' : 'var(--ink-1)', fontWeight: active ? 500 : 400, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
          {sess.title}
        </div>
        <div className="mono" style={{ fontSize: 9, color: 'var(--ink-3)', marginTop: 2 }}>
          {msgs > 0 ? `${msgs} 条 · ${ago}` : '空对话'}
          {sess.context && <span style={{ color: 'var(--yin)', marginLeft: 6 }}>● {sess.context.name}</span>}
        </div>
      </div>
      {hover && (
        <button
          onClick={(e) => { e.stopPropagation(); onDelete(); }}
          title="删除会话"
          style={{ background: 'transparent', border: 'none', color: 'var(--ink-3)', cursor: 'pointer', padding: 4, fontSize: 12, lineHeight: 1 }}>
          ×
        </button>
      )}
    </div>
  );
}

function timeAgo(ts) {
  const diff = (Date.now() - ts) / 1000;
  if (diff < 60) return '刚刚';
  if (diff < 3600) return Math.floor(diff / 60) + ' 分钟前';
  if (diff < 86400) return Math.floor(diff / 3600) + ' 小时前';
  return Math.floor(diff / 86400) + ' 天前';
}


// "已删除会话" 折叠段 — 点开拉 /conversations/trash, 每条可 restore / 永久删
function TrashSection({ backendUrl, dispatch, sessions }) {
  const [open, setOpen] = useState(false);
  const [items, setItems] = useState(null);  // null = 未拉; [] = 拉过但空
  const [loading, setLoading] = useState(false);

  const fetchTrash = async () => {
    setLoading(true);
    try {
      const r = await fetch(`${backendUrl}/conversations/trash`).then(r => r.json());
      setItems(Array.isArray(r.conversations) ? r.conversations : []);
    } catch (e) {
      console.warn('[guanlan] /conversations/trash 失败:', e);
      setItems([]);
    } finally {
      setLoading(false);
    }
  };

  const toggle = () => {
    const next = !open;
    setOpen(next);
    if (next && items === null) fetchTrash();
  };

  const handleRestore = async (cid, trashFn) => {
    try {
      const r = await fetch(`${backendUrl}/conversations/${encodeURIComponent(cid)}/restore`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ trash_filename: trashFn }),
      }).then(r => r.json());
      if (r.ok) {
        // 拉回 live conv 数据 + dispatch merge
        const conv = await fetch(`${backendUrl}/conversations/${encodeURIComponent(cid)}`).then(r => r.json());
        if (conv && conv.ok) {
          // 当前 sessions 里没有这个 cid → 加进去
          const exists = sessions.find(x => x.id === cid);
          if (!exists) {
            dispatch({ type: 'merge_sessions', sessions: [conv.conversation] });
          }
        }
        fetchTrash();   // 刷新回收站
      } else {
        alert('恢复失败');
      }
    } catch (err) {
      alert(`恢复失败: ${err.message}`);
    }
  };

  const handlePermDelete = async (cid) => {
    if (!confirm('永久删除? 不可恢复.')) return;
    try {
      await fetch(`${backendUrl}/conversations/${encodeURIComponent(cid)}?permanent=1`, { method: 'DELETE' });
      fetchTrash();
    } catch (err) {
      alert(`删除失败: ${err.message}`);
    }
  };

  return (
    <div style={{ paddingTop: 6, paddingBottom: 8 }}>
      <div
        onClick={toggle}
        style={{ padding: '0 20px', cursor: 'pointer', display: 'flex', alignItems: 'center',
                 justifyContent: 'space-between', userSelect: 'none' }}>
        <div className="serif" style={{ fontSize: 10, color: 'var(--ink-3)', letterSpacing: 1 }}>
          {open ? '▾' : '▸'} 已删除会话 {items !== null && `· ${items.length}`}
        </div>
        {open && items !== null && items.length > 0 && (
          <span className="mono" style={{ fontSize: 9, color: 'var(--ink-3)' }}>30 天后自动清</span>
        )}
      </div>
      {open && (
        <div style={{ marginTop: 4 }}>
          {loading && <div className="serif" style={{ fontSize: 11, color: 'var(--ink-3)', padding: '6px 20px' }}>加载中...</div>}
          {!loading && items !== null && items.length === 0 && (
            <div className="serif" style={{ fontSize: 11, color: 'var(--ink-3)', padding: '6px 20px' }}>
              回收站是空的
            </div>
          )}
          {!loading && items && items.map((it) => (
            <div key={it._trash_filename}
                 className="hover-row"
                 style={{ padding: '6px 20px', display: 'flex', alignItems: 'center', gap: 6 }}>
              <div style={{ flex: 1, minWidth: 0 }}>
                <div className="serif" style={{ fontSize: 12, color: 'var(--ink-1)',
                                                  whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                  {it.title || '(无标题)'}
                </div>
                <div className="mono" style={{ fontSize: 9, color: 'var(--ink-3)' }}>
                  {(it.messages || []).length} 条 · 删于 {timeAgo(it.deletedAt || 0)}
                </div>
              </div>
              <button
                onClick={() => handleRestore(it.id, it._trash_filename)}
                title="恢复"
                style={{ background: 'transparent', border: 'none', color: 'var(--yin)',
                         cursor: 'pointer', padding: 4, fontSize: 11 }}>
                ↺
              </button>
              <button
                onClick={() => handlePermDelete(it.id)}
                title="永久删除"
                style={{ background: 'transparent', border: 'none', color: 'var(--ink-3)',
                         cursor: 'pointer', padding: 4, fontSize: 12, lineHeight: 1 }}>
                ×
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function RailSection({ label, right, children }) {
  return (
    <div style={{ paddingTop: 6, paddingBottom: 8 }}>
      <div style={{ display: 'flex', alignItems: 'center', padding: '4px 20px 6px' }}>
        <span className="mono" style={{ fontSize: 9, color: 'var(--ink-3)', letterSpacing: '0.2em', textTransform: 'uppercase', flex: 1 }}>{label}</span>
        {right}
      </div>
      {children}
    </div>
  );
}

// ───────────────────────── 顶栏 ─────────────────────────

// 真·盘面时钟: 根据本地时间算交易时段 + 距收盘倒计时
function marketClock(d) {
  const pad = n => String(n).padStart(2, '0');
  const hhmmss = `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
  const day = d.getDay();
  if (day === 0 || day === 6) return { label: '周末休市', time: hhmmss, count: null, open: false };
  const hm = d.getHours() * 60 + d.getMinutes();
  const sec = d.getSeconds();
  const OPEN1 = 9 * 60 + 30, CLOSE1 = 11 * 60 + 30, OPEN2 = 13 * 60, CLOSE2 = 15 * 60;
  const countdown = (targetMin) => {
    let rem = (targetMin - hm) * 60 - sec;
    if (rem < 0) rem = 0;
    return `${pad(Math.floor(rem / 3600))}:${pad(Math.floor((rem % 3600) / 60))}:${pad(rem % 60)}`;
  };
  if (hm < OPEN1)  return { label: '未开盘',  time: hhmmss, count: countdown(OPEN1), countLabel: '距开盘', open: false };
  if (hm < CLOSE1) return { label: '交易中',  time: hhmmss, count: countdown(CLOSE2), countLabel: '距收盘', open: true };
  if (hm < OPEN2)  return { label: '午间休市', time: hhmmss, count: countdown(OPEN2), countLabel: '距开盘', open: false };
  if (hm < CLOSE2) return { label: '交易中',  time: hhmmss, count: countdown(CLOSE2), countLabel: '距收盘', open: true };
  return { label: '已收盘', time: hhmmss, count: null, open: false };
}

function TopBar({ s, session, dispatch }) {
  // 每秒走字的真时钟
  const [now, setNow] = useState(() => new Date());
  useEffect(() => { const id = setInterval(() => setNow(new Date()), 1000); return () => clearInterval(id); }, []);
  const clk = marketClock(now);

  // 大盘指数 — 优先用 /quotes 拉到的真值, 没拉上显示 "—" (不再写死假数)
  const idxOf = (code) => {
    const q = s.indices;
    if (!q) return null;
    return q[code] || q[code.toUpperCase()] || q[code.toLowerCase()] || null;
  };
  const INDICES = [
    { n: '上证', code: 'sh000001' },
    { n: '深成', code: 'sz399001' },
    { n: '创业', code: 'sz399006' },
  ];

  // 标题重命名: 双击标题进入编辑, 回车/失焦保存, Esc 取消
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState('');
  const titleRef = useRef(null);
  const beginEdit = () => { setDraft(session?.title || ''); setEditing(true); };
  const commitEdit = () => { dispatch({ type: 'rename_session', title: draft }); setEditing(false); };
  useEffect(() => { if (editing && titleRef.current) { titleRef.current.focus(); titleRef.current.select(); } }, [editing]);

  const canExport = session && session.messages.length > 0;
  const r = s.activeRound;
  const chainMsg = r ? session?.messages.find(m => m.id === r.chainId) : null;
  const done = chainMsg ? chainMsg.chain.filter(c => c.status === 'done').length : 0;
  const total = chainMsg ? chainMsg.chain.length : 0;
  const running = chainMsg ? chainMsg.chain.find(c => c.status === 'running') : null;

  // /diag 探活面板 — 一键看 5 源 + LLM 健康度
  const [diag, setDiag] = useState(null);
  const [diagLoading, setDiagLoading] = useState(false);
  const runDiag = async () => {
    if (diagLoading || !s.backendUrl) return;
    setDiagLoading(true); setDiag(null);
    try {
      const r = await fetch(`${s.backendUrl}/diag?quick=1`);
      setDiag(await r.json());
    } catch (e) { setDiag({ ok: false, _err: String(e) }); }
    setDiagLoading(false);
  };

  let sub = '准备就绪 · 输入提问或按 ⌘K 选工具';
  if (s.status === 'tool-running') sub = <><span>正在执行 {done}/{total}</span><span style={{ color: 'var(--ink-3)', margin: '0 6px' }}>·</span><span style={{ color: 'var(--yin)' }}>● {running?.name || '规划中…'}</span></>;
  else if (s.status === 'streaming') sub = '流式输出中…';
  else if (s.status === 'confirming') sub = <span style={{ color: 'var(--yin)' }}>⚠ 等待工具确认</span>;
  else if (s.queuedInput) sub = <span style={{ color: 'var(--jin)' }}>⏳ 有排队任务</span>;
  else if (session?.messages.length === 0) sub = '准备就绪';
  else sub = `${session?.messages.length} 条消息 · 等待追问`;

  return (
    <header style={{ padding: '14px 32px 12px', borderBottom: '1px solid var(--line)', display: 'flex', alignItems: 'center', gap: 16, flexShrink: 0, background: 'rgba(241,234,217,0.4)' }}>
      <div style={{ flex: 1, minWidth: 0 }}>
        {editing ? (
          <input ref={titleRef} value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onBlur={commitEdit}
            onKeyDown={(e) => {
              if (e.key === 'Enter') { e.preventDefault(); commitEdit(); }
              else if (e.key === 'Escape') { e.preventDefault(); setEditing(false); }
            }}
            maxLength={48}
            className="serif"
            style={{ fontSize: 16, fontWeight: 500, color: 'var(--ink)', background: 'var(--paper)', border: '1px solid var(--yin)', outline: 'none', padding: '1px 6px', width: '100%', maxWidth: 420, fontFamily: 'var(--serif)' }} />
        ) : (
          <div onDoubleClick={beginEdit} title="双击重命名"
            style={{ display: 'flex', alignItems: 'center', gap: 6, cursor: 'text', minWidth: 0 }}>
            <span className="serif" style={{ fontSize: 16, color: 'var(--ink)', fontWeight: 500, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{session?.title || '新对话'}</span>
            <span onClick={(e) => { e.stopPropagation(); beginEdit(); }} className="hover-link"
              title="重命名"
              style={{ flexShrink: 0, fontSize: 11, color: 'var(--ink-3)', cursor: 'pointer', lineHeight: 1 }}>✎</span>
          </div>
        )}
        <div className="mono" style={{ fontSize: 10, color: 'var(--ink-3)', marginTop: 3 }}>{sub}</div>
      </div>
      <div style={{ display: 'flex', gap: 18, fontFamily: 'var(--mono)', fontSize: 11 }}>
        {INDICES.map((x, i) => {
          const q = idxOf(x.code);
          const v = q && q.price != null
            ? Number(q.price).toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
            : '—';
          const d = q && q.changePercent != null ? Number(q.changePercent) : null;
          return (
            <div key={i} style={{ display: 'flex', alignItems: 'baseline', gap: 5 }} title={q ? '腾讯行情实时' : (s.backendUrl ? '等待行情…' : '未连后端')}>
              <span style={{ color: 'var(--ink-3)', fontSize: 10 }}>{x.n}</span>
              <span style={{ color: 'var(--ink-1)' }}>{v}</span>
              {d != null
                ? <span className={d < 0 ? 'down' : 'up'}>{d >= 0 ? '+' : ''}{d.toFixed(2)}%</span>
                : <span style={{ color: 'var(--ink-3)' }}>—</span>}
            </div>
          );
        })}
      </div>
      <div style={{ width: 1, height: 22, background: 'var(--line)' }} />
      <div style={{ display: 'flex', alignItems: 'center', gap: 6 }} className="mono">
        {s.backendUrl ? (
          <>
            <span style={{ width: 5, height: 5, background: 'var(--zhu)', borderRadius: '50%' }} />
            <span style={{ fontSize: 10, color: 'var(--ink-2)' }}>backend · {s.backendUrl.replace(/^https?:\/\//, '')}</span>
          </>
        ) : (
          <>
            <span style={{ width: 5, height: 5, background: 'var(--ink-3)', borderRadius: '50%' }} />
            <span style={{ fontSize: 10, color: 'var(--ink-3)' }}>mock</span>
          </>
        )}
        <span style={{ fontSize: 10, color: 'var(--ink-3)' }}>·</span>
        <span style={{ width: 5, height: 5, background: clk.open ? 'var(--zhu)' : 'var(--ink-3)', borderRadius: '50%' }} />
        <span style={{ fontSize: 10, color: 'var(--ink-2)' }}>{clk.label} · {clk.time}</span>
        {clk.count && (
          <>
            <span style={{ fontSize: 10, color: 'var(--ink-3)' }}>·</span>
            <span style={{ fontSize: 10, color: 'var(--ink-3)' }}>{clk.countLabel} {clk.count}</span>
          </>
        )}
      </div>
      <div style={{ width: 1, height: 22, background: 'var(--line)' }} />
      <span onClick={runDiag} title="探活: xueqiu / 腾讯行情 / news_db / LLM / 各 opencli 源"
        className="hover-pill"
        style={{ cursor: (diagLoading || !s.backendUrl) ? 'default' : 'pointer', fontSize: 11, padding: '4px 10px', border: '1px solid var(--line)', color: 'var(--ink-2)', fontFamily: 'var(--mono)', opacity: s.backendUrl ? 1 : 0.5 }}>
        {diagLoading ? '⏳ 探活中…' : (diag ? (diag.ok ? '🩺 ✓' : '🩺 ✗') : '🩺 探活')}
      </span>
      {canExport && (
        <>
          <div style={{ width: 1, height: 22, background: 'var(--line)' }} />
          <button onClick={() => exportSessionToMd(session)}
            title="导出为 markdown"
            className="hover-pill"
            style={{ background: 'transparent', border: '1px solid var(--line)', color: 'var(--ink-2)', padding: '4px 10px', fontFamily: 'var(--mono)', fontSize: 11, cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 5 }}>
            <span>↧</span> <span>导出 .md</span>
          </button>
        </>
      )}
      {diag && (
        <div style={{ position: 'fixed', top: 56, right: 32, width: 380, background: 'var(--paper)', border: '1px solid var(--ink)', boxShadow: '0 4px 16px rgba(28,24,20,0.18)', zIndex: 50, padding: '12px 16px', fontFamily: 'var(--mono)', fontSize: 11, color: 'var(--ink-1)' }}>
          <div style={{ display: 'flex', alignItems: 'center', marginBottom: 8, gap: 6 }}>
            <span className="serif" style={{ fontSize: 13, color: 'var(--ink)', fontWeight: 500, flex: 1 }}>
              API 探活 {diag.ok ? '✓ 全绿' : '✗ 有故障'}
            </span>
            <span onClick={runDiag} className="hover-link" title="重新探活"
              style={{ cursor: 'pointer', fontSize: 11, color: 'var(--ink-3)' }}>↻</span>
            <span onClick={() => setDiag(null)}
              style={{ cursor: 'pointer', fontSize: 14, color: 'var(--ink-3)', lineHeight: 1, marginLeft: 4 }}>×</span>
          </div>
          {(diag.results || []).map((r, i) => (
            <div key={i} style={{ display: 'flex', alignItems: 'baseline', gap: 6, padding: '4px 0', borderTop: i ? '1px dashed var(--line-soft)' : 'none' }}>
              <span style={{ color: r.ok ? 'var(--zhu)' : 'var(--yin)', fontSize: 11, width: 12 }}>{r.ok ? '✓' : '✗'}</span>
              <span style={{ color: 'var(--ink-1)', flex: 1 }}>{r.name}</span>
              <span style={{ color: 'var(--ink-3)', fontSize: 10 }}>{r.latency_ms}ms</span>
            </div>
          ))}
          {!diag.results && diag._err && (
            <div style={{ color: 'var(--yin)' }}>探活失败: {diag._err}</div>
          )}
          {diag.rate_limit_stats && (
            <details style={{ marginTop: 8, fontSize: 10, color: 'var(--ink-3)' }}>
              <summary style={{ cursor: 'pointer' }}>限速 / 缓存累计 ({Object.keys(diag.rate_limit_stats).length} 个 source)</summary>
              <div style={{ marginTop: 4, fontSize: 9, maxHeight: 220, overflowY: 'auto', paddingRight: 6 }}>
                {Object.entries(diag.rate_limit_stats).map(([name, st], j) => (
                  <div key={j} style={{ marginBottom: 3 }}>
                    <span style={{ color: 'var(--ink-1)' }}>{name}</span>
                    <span style={{ marginLeft: 6, color: 'var(--ink-2)' }}>calls={st.calls} throttled={st.throttled} cache_hits={st.cache_hits}{st.last_error ? ' ⚠' : ''}</span>
                    {st.last_error && <div style={{ color: 'var(--yin)', fontSize: 9, marginLeft: 12 }}>{st.last_error}</div>}
                  </div>
                ))}
              </div>
            </details>
          )}
        </div>
      )}
    </header>
  );
}

// ───────────────────────── Transcript ─────────────────────────

function Transcript({ s, messages, dispatch, startAgent }) {
  const scrollerRef = useRef(null);
  // 自动滚底 = 仅当用户还粘在底部时才执行; 上滚浏览历史时关掉, 滚回底部 80px 内时恢复
  const stickRef = useRef(true);
  const onScroll = () => {
    const el = scrollerRef.current;
    if (!el) return;
    stickRef.current = (el.scrollHeight - el.scrollTop - el.clientHeight) < 80;
  };
  // 切换会话时重置粘底 (打开新对话默认看最新)
  useEffect(() => { stickRef.current = true; }, [s.currentSessionId]);
  useEffect(() => {
    const el = scrollerRef.current;
    if (el && stickRef.current) el.scrollTop = el.scrollHeight;
  });

  const empty = messages.length === 0;

  return (
    <div ref={scrollerRef} onScroll={onScroll} style={{ flex: 1, overflowY: 'auto', overflowX: 'hidden', padding: '24px 56px', display: 'flex', flexDirection: 'column', gap: 18, minHeight: 0 }}>
      {empty && <EmptyState startAgent={startAgent} />}
      {messages.map(m => {
        if (m.role === 'user') return <UserBubble key={m.id} text={m.text} />;
        if (m.kind === 'chain') return <ToolChain key={m.id} msg={m} backendUrl={s.backendUrl} />;
        if (m.kind === 'brief') return <StockBriefCard key={m.id} sym={m.sym} dispatch={dispatch} backendUrl={s.backendUrl} />;
        if (m.kind === 'report') return <ReportCard key={m.id} msg={m} dispatch={dispatch} />;
        if (m.kind === 'answer') return <AiSummary key={m.id} text={m.text} streaming={s.status === 'streaming' && s.activeRound?.answerId === m.id} />;
        return null;
      })}
      {s.queuedInput && <QueuedBar text={s.queuedInput} dispatch={dispatch} />}
    </div>
  );
}

function EmptyState({ startAgent }) {
  const samples = [
    '看下宁德时代怎么样',
    '今天主力在买什么',
    'CPO 板块还能不能追',
    '茅台跌破 1,200 提醒我',
  ];
  return (
    <div style={{ flex: 1, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', gap: 24, paddingTop: 60 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 14 }}>
        <span className="seal" style={{ width: 42, height: 42, fontSize: 24 }}>觀</span>
        <div>
          <div className="serif" style={{ fontSize: 24, color: 'var(--ink)', fontWeight: 500, letterSpacing: '0.08em' }}>觀瀾</div>
          <div className="mono" style={{ fontSize: 10, color: 'var(--ink-3)', letterSpacing: '0.2em', marginTop: 2 }}>A 股深度研究 · 26 工具 · 自然语言驱动</div>
        </div>
      </div>
      <div className="serif" style={{ fontSize: 14, color: 'var(--ink-2)', maxWidth: 480, textAlign: 'center', lineHeight: 1.8 }}>
        问一只股票, 一个板块, 或一个想法 —— agent 会自动调度工具, 把结果归纳成一段中文.
      </div>
      <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', justifyContent: 'center', maxWidth: 640 }}>
        {samples.map((q, i) => (
          <button key={i}
            onClick={() => startAgent(q)}
            className="hover-pill"
            style={{
              padding: '8px 14px', border: '1px solid var(--line)', background: 'var(--paper)',
              fontFamily: 'var(--serif)', fontSize: 13, color: 'var(--ink-1)', cursor: 'pointer'
            }}>
            ❯ {q}
          </button>
        ))}
      </div>
      <div className="mono" style={{ fontSize: 10, color: 'var(--ink-3)', display: 'flex', gap: 20, marginTop: 8 }}>
        <span>⌘K  工具面板</span>
        <span>⌘N  新对话</span>
        <span>/  斜杠命令</span>
        <span>ESC  取消当前轮</span>
      </div>
    </div>
  );
}

function UserBubble({ text }) {
  return (
    <div style={{ display: 'flex', justifyContent: 'flex-end' }}>
      <div style={{
        maxWidth: '72%', padding: '12px 16px', background: 'var(--ink)',
        color: 'var(--paper)', fontFamily: 'var(--serif)', fontSize: 14, lineHeight: 1.65,
        animation: 'fadeIn 200ms ease-out'
      }}>{text}</div>
    </div>
  );
}

function QueuedBar({ text, dispatch }) {
  return (
    <div style={{ display: 'flex', justifyContent: 'flex-end', animation: 'fadeIn 200ms ease-out' }}>
      <div style={{
        display: 'flex', alignItems: 'center', gap: 10,
        padding: '6px 12px', background: 'rgba(138,111,63,0.12)', border: '1px solid var(--jin)',
        fontFamily: 'var(--mono)', fontSize: 11, color: 'var(--jin)'
      }}>
        <span>⏳</span>
        <span>排队: <span className="serif" style={{ color: 'var(--ink-1)', fontSize: 12 }}>{text}</span></span>
        <button onClick={() => dispatch({ type: 'clear_queue' })} style={{ marginLeft: 8, border: 'none', background: 'transparent', color: 'var(--ink-2)', cursor: 'pointer', fontFamily: 'var(--mono)', fontSize: 10 }}>ESC ×</button>
      </div>
    </div>
  );
}

function AiAvatar() {
  return <div style={{ width: 28, height: 28, flex: '0 0 28px', background: 'var(--paper-2)', border: '1px solid var(--ink)', display: 'flex', alignItems: 'center', justifyContent: 'center', fontFamily: 'var(--serif)', fontSize: 13, color: 'var(--ink)' }}>觀</div>;
}

// ───── 工具链 ─────
function ToolChain({ msg, backendUrl }) {
  const done = msg.chain.filter(c => c.status === 'done').length;
  const total = msg.chain.length;
  const elapsed = msg.chain.filter(c => c.status === 'done').reduce((s, c) => s + (c.t || 0), 0);
  const hasRunning = msg.chain.some(c => c.status === 'running');
  const allDone = total > 0 && done === total;
  // 默认 collapsed; 有 running 时展开; 全 done 后自动 collapse 不占视觉
  const [userExpanded, setUserExpanded] = useState(null);  // null = 自动, true/false = 用户覆盖
  const expanded = userExpanded !== null ? userExpanded : (hasRunning && !allDone);

  if (total === 0 && msg.kindKey === 'planning') {
    return (
      <div style={{ display: 'flex', gap: 14, animation: 'fadeIn 200ms ease-out' }}>
        <AiAvatar />
        <div style={{ flex: 1, minWidth: 0, padding: '10px 16px' }}>
          <div className="serif" style={{ fontSize: 13, color: 'var(--ink-3)', fontStyle: 'italic' }}>
            <span className="mono" style={{ color: 'var(--yin)', marginRight: 6 }}>⠋</span>
            研究计划生成中…
          </div>
        </div>
      </div>
    );
  }

  return (
    <div style={{ display: 'flex', gap: 14, animation: 'fadeIn 200ms ease-out' }}>
      <AiAvatar />
      <div style={{ flex: 1, minWidth: 0 }}>
        {/* 透明背景, 无边框 — 跟用户聊天上下文融为一体 */}
        <div>
          <div onClick={() => setUserExpanded(!expanded)}
               style={{ padding: '4px 0', display: 'flex', alignItems: 'center', gap: 10, cursor: 'pointer',
                        color: 'var(--ink-3)', userSelect: 'none' }}>
            <span className="mono" style={{ fontSize: 10, color: allDone ? 'var(--ink-3)' : 'var(--yin)' }}>
              {expanded ? '▾' : '▸'}
            </span>
            <span className="mono" style={{ fontSize: 11, color: allDone ? 'var(--ink-3)' : 'var(--ink-2)' }}>
              {allDone ? `已用 ${total} 个工具 · ${elapsed.toFixed(1)}s` : `${msg.kindLabel} · ${done}/${total} · ${elapsed.toFixed(1)}s`}
            </span>
          </div>
          {expanded && (
            <div style={{ padding: '2px 0 4px', borderLeft: '1px dashed var(--line)', marginLeft: 6, paddingLeft: 10 }}>
              {msg.chain.map((tl, i) => (
                <ToolRow key={i} i={i + 1} {...tl} last={i === msg.chain.length - 1} backendUrl={backendUrl} />
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

// 深度研报实时进度: 当 run_report 工具正在跑时, 轮询 /report-progress?code=X
// 显示 14 个 agent 各自的 pending/running/done/fail 状态 + elapsed.
// 状态文件由 tui.run_report_oneshot 的 on_event 写在 out/<CODE>_progress.json.
function DeepReportProgress({ code, backendUrl }) {
  const [snap, setSnap] = useState(null);
  useEffect(() => {
    if (!code || !backendUrl) return;
    let cancelled = false;
    const tick = async () => {
      try {
        const r = await fetch(`${backendUrl}/report-progress?code=${encodeURIComponent(code)}`);
        const d = await r.json();
        if (!cancelled) setSnap(d);
      } catch {}
    };
    tick();
    const id = setInterval(tick, 2000);
    return () => { cancelled = true; clearInterval(id); };
  }, [code, backendUrl]);
  if (!snap || !snap.agents || Object.keys(snap.agents).length === 0) {
    return <div style={{ fontSize: 11, color: 'var(--ink-3)', marginTop: 6, fontStyle: 'italic' }}>等待 agent 启动…</div>;
  }
  const agents = Object.entries(snap.agents);
  return (
    <div style={{ marginTop: 8, padding: '8px 12px', background: 'rgba(255,255,255,0.45)', border: '1px dashed var(--line)', fontFamily: 'var(--mono)', fontSize: 10 }}>
      <div style={{ fontSize: 11, color: 'var(--ink-2)', marginBottom: 5, display: 'flex', alignItems: 'center', gap: 8 }}>
        <span>研判流水线</span>
        <span style={{ color: 'var(--ink-3)' }}>{snap.done}/{snap.total} 完成 · {snap.running} 思考中 · {snap.pending} 排队{snap.fail > 0 ? ` · ${snap.fail} 失败` : ''}</span>
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, 1fr)', gap: '3px 14px' }}>
        {agents.map(([name, st]) => {
          const color = st.state === 'done' ? 'var(--zhu)' : st.state === 'running' ? 'var(--jin)' : st.state === 'fail' ? 'var(--yin)' : 'var(--ink-3)';
          return (
            <div key={name} style={{ display: 'flex', alignItems: 'center', gap: 5 }}>
              <span style={{ width: 7, height: 7, borderRadius: '50%', background: color, flexShrink: 0, animation: st.state === 'running' ? 'pulse 1.2s ease-in-out infinite' : 'none' }} />
              <span style={{ color: 'var(--ink-1)', flex: 1, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{name}</span>
              {st.elapsed > 0 && <span style={{ color: 'var(--ink-3)', fontSize: 9 }}>{st.elapsed.toFixed(1)}s</span>}
            </div>
          );
        })}
      </div>
    </div>
  );
}

function ToolRow({ i, name, cn, args, t, status, result, last, backendUrl }) {
  const running = status === 'running';
  const pending = status === 'pending';
  const cancelled = status === 'cancelled';
  return (
    <div style={{ display: 'flex', gap: 14, padding: '8px 16px', alignItems: 'flex-start', position: 'relative', opacity: pending ? 0.4 : 1, transition: 'opacity 250ms' }}>
      <div style={{ position: 'relative', width: 22, flex: '0 0 22px' }}>
        <div style={{
          width: 22, height: 22,
          background: running ? 'var(--yin)' : (pending || cancelled) ? 'transparent' : 'var(--ink)',
          color: (pending || cancelled) ? 'var(--ink-3)' : 'var(--paper)',
          border: (pending || cancelled) ? '1px dashed var(--ink-3)' : 'none',
          display: 'flex', alignItems: 'center', justifyContent: 'center',
          fontFamily: 'var(--serif)', fontSize: 11, fontWeight: 500,
          transition: 'background 250ms'
        }}>{cancelled ? '×' : i}</div>
        {!last && <div style={{ position: 'absolute', top: 24, left: 10, bottom: -12, width: 2, background: 'var(--line)' }} />}
        {running && <div style={{ position: 'absolute', inset: -3, width: 28, height: 28, border: '1px solid var(--yin)', opacity: 0.5, animation: 'pulse 1.6s ease-in-out infinite' }} />}
      </div>
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ display: 'flex', alignItems: 'baseline', gap: 8 }}>
          <code style={{ fontFamily: 'var(--mono)', fontSize: 12, color: 'var(--ink)', fontWeight: 500 }}>{name}</code>
          <span className="serif" style={{ fontSize: 12, color: 'var(--ink-2)' }}>{cn}</span>
          <span className="mono" style={{ fontSize: 10, color: 'var(--ink-3)', whiteSpace: 'nowrap', marginLeft: 'auto' }}>
            {running ? '⠋ 运行中…' : pending ? '— 等待' : cancelled ? '× 已取消' : `✓ ${t}s`}
          </span>
        </div>
        <div className="mono" style={{ fontSize: 10.5, color: 'var(--ink-3)', marginTop: 2, wordBreak: 'break-all' }}>{args}</div>
        {!pending && !cancelled && (
          <div className="serif" style={{ fontSize: 12.5, color: running ? 'var(--ink-3)' : 'var(--ink-1)', marginTop: 5, fontStyle: running ? 'italic' : 'normal', display: 'flex', alignItems: 'center', gap: 6 }}>
            {!running && <span style={{ color: 'var(--ink-3)' }}>→</span>}
            <span style={{ flex: 1 }}>{running ? '正在抓取…' : result}</span>
          </div>
        )}
        {name === 'run_report' && running && (() => {
          // run_report 正在跑 → 显示 14-agent 实时进度. 解析 code 从 args (可能是 JSON 串或对象)
          let code = null;
          try {
            if (typeof args === 'object' && args && args.code) code = args.code;
            else if (typeof args === 'string') code = (JSON.parse(args) || {}).code;
          } catch {}
          return <DeepReportProgress code={code} backendUrl={backendUrl} />;
        })()}
      </div>
    </div>
  );
}

// ───── 速览卡 (使用真 sym 数据) ─────
function StockBriefCard({ sym, dispatch, backendUrl }) {
  if (!sym) sym = window.STOCK_DB['300750']; // fallback

  // 雪球评论: sym.comments 是本地已存 (秒出); "拉最新" 走 /comments?refresh=1 现拉 + 情绪
  const [xqComments, setXqComments] = useState(null);  // null = 用 sym.comments
  const [xqSenti, setXqSenti] = useState(null);
  const [xqLoading, setXqLoading] = useState(false);
  const [xqErr, setXqErr] = useState(null);
  const [sentiLoading, setSentiLoading] = useState(false);  // sentiment 第二阶段 (异步, 不阻塞按钮)
  const [added, setAdded] = useState(false);  // 「加入自选」点击反馈
  const comments = xqComments != null ? xqComments : (Array.isArray(sym.comments) ? sym.comments : []);
  const refreshComments = () => {
    if (!backendUrl || xqLoading) return;
    setXqLoading(true); setXqErr(null);
    // 第一阶段: 秒回评论 (sentiment=0, ~0.5s) — 按钮 loading 仅持续这段
    fetch(`${backendUrl}/comments?code=${encodeURIComponent(sym.code)}&refresh=1&sentiment=0&limit=10`)
      .then(r => r.json())
      .then(d => {
        if (d && Array.isArray(d.comments)) setXqComments(d.comments);
        if (d && d.ok === false) {
          const e = String(d.error || '');
          setXqErr(/Unexpected response|COMMAND_EXEC/.test(e)
            ? '雪球对该股返回异常页面（多为冷门/特殊股，讨论极少），暂拉不到'
            : (/timeout/i.test(e) ? '拉取超时，可重试' : '拉取失败，可重试'));
        } else if (d && d.ok && Array.isArray(d.comments) && d.comments.length === 0) {
          setXqErr('雪球对该股近期无讨论');
        }
        setXqLoading(false);
        // 第二阶段: 后台拉情绪 (~10-20s LLM 分类), 不阻塞按钮; 失败静默
        if (d && d.ok && Array.isArray(d.comments) && d.comments.length > 0) {
          setSentiLoading(true);
          fetch(`${backendUrl}/comments?code=${encodeURIComponent(sym.code)}&refresh=0&sentiment=1&limit=10`)
            .then(r => r.json())
            .then(d2 => { if (d2 && d2.sentiment) setXqSenti(d2.sentiment); })
            .catch(() => {})
            .finally(() => setSentiLoading(false));
        }
      })
      .catch(e => { console.warn('[guanlan] /comments 失败:', e); setXqErr('网络/后端异常，可重试'); setXqLoading(false); });
  };

  // 全字段 null 安全: 后端 brief_data 任意字段可能为 null (盘后/无 cookie/无数据)
  const num = (v) => (v == null || v === '' || (typeof v === 'number' && isNaN(v))) ? null : v;
  const fmt2 = (v) => { const n = Number(v); return isNaN(n) ? '—' : n.toFixed(2); };
  const price = num(sym.price), deltaPct = num(sym.deltaPct), change = num(sym.change);
  const up = (deltaPct ?? 0) >= 0;
  const fundIn = num(sym.main_in);
  const prevIn = num(sym.prev_main_in);
  const inflow = num(sym.inflow), outflow = num(sym.outflow);   // 真·流入/流出 (亿)
  const flowMax = Math.max(Math.abs(inflow ?? 0), Math.abs(outflow ?? 0), 1);
  const news = Array.isArray(sym.news) ? sym.news : [];
  const mkt = sym.market || '';

  return (
    <div style={{ display: 'flex', gap: 14, animation: 'fadeIn 400ms ease-out' }}>
      <AiAvatar />
      <div style={{ flex: 1, minWidth: 0, background: 'var(--paper)', border: '1px solid var(--ink)' }}>
        <div style={{ padding: '14px 18px 12px', display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', borderBottom: '1px solid var(--ink)' }}>
          <div>
            <div style={{ display: 'flex', alignItems: 'baseline', gap: 12 }}>
              <span className="serif" style={{ fontSize: 22, fontWeight: 500, color: 'var(--ink)' }}>{sym.name}</span>
              <span className="mono" style={{ fontSize: 12, color: 'var(--ink-3)' }}>{sym.code}{mkt ? ` · ${mkt}` : ''}</span>
              <span className="serif" style={{ fontSize: 11, padding: '1px 6px', background: 'var(--ink)', color: 'var(--paper)', letterSpacing: '0.08em' }}>速览</span>
            </div>
            <div className="mono" style={{ fontSize: 10, color: 'var(--ink-3)', marginTop: 4 }}>申万 · {sym.industry || '—'} · 总市值 {sym.mc || '—'}</div>
          </div>
          <div style={{ textAlign: 'right' }}>
            <div className={'mono ' + (up ? 'up' : 'down')} style={{ fontSize: 24, fontWeight: 500 }}>{price != null ? fmt2(price) : '—'}</div>
            <div className={'mono ' + (up ? 'up' : 'down')} style={{ fontSize: 12 }}>
              {change != null ? `${change >= 0 ? '+' : ''}${fmt2(change)} ` : ''}{deltaPct != null ? `(${deltaPct >= 0 ? '+' : ''}${fmt2(deltaPct)}%)` : ''}
            </div>
          </div>
        </div>

        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', borderBottom: '1px solid var(--line)' }}>
          <BriefRegion title="行情 / 估值" cite="1">
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 10 }}>
              {[
                { l: '量比', v: sym.vol_ratio }, { l: '换手', v: sym.turn }, { l: '振幅', v: sym.amp },
                { l: 'PE TTM', v: sym.pe }, { l: 'PB', v: sym.pb },
              ].map((m, i) => (
                <div key={i}>
                  <div style={{ fontSize: 10, color: 'var(--ink-3)' }}>{m.l}</div>
                  <div className="mono" style={{ fontSize: 13, color: 'var(--ink)' }}>{m.v == null || m.v === '' ? '—' : m.v}</div>
                </div>
              ))}
            </div>
          </BriefRegion>
          <BriefRegion title="主力资金 · 今日" cite="2" borderL>
            {fundIn != null ? (
              <>
                <div style={{ display: 'flex', alignItems: 'baseline', gap: 8, marginBottom: 8 }}>
                  <span className={'mono ' + (fundIn >= 0 ? 'up' : 'down')} style={{ fontSize: 18, fontWeight: 500 }}>{fundIn >= 0 ? '+' : ''}{fundIn}</span>
                  <span className="serif" style={{ fontSize: 11, color: 'var(--ink-2)' }}>亿 {fundIn >= 0 ? '净流入' : '净流出'}</span>
                  {prevIn != null && <span className="mono" style={{ fontSize: 10, color: 'var(--ink-3)', marginLeft: 'auto' }}>vs 昨 {prevIn >= 0 ? '+' : ''}{prevIn}</span>}
                </div>
                {(inflow != null || outflow != null) ? [
                  { l: '流入', v: inflow, up: true }, { l: '流出', v: outflow, up: false },
                ].map((b, i) => (
                  <div key={i} style={{ display: 'flex', alignItems: 'center', gap: 8, marginTop: 4 }}>
                    <span className="serif" style={{ fontSize: 11, color: 'var(--ink-2)', width: 38 }}>{b.l}</span>
                    <div style={{ flex: 1, height: 3, background: 'var(--paper-2)' }}>
                      <div style={{ width: `${Math.min(100, Math.abs(b.v ?? 0) / flowMax * 100)}%`, height: '100%', background: b.up ? 'var(--zhu)' : 'var(--dai)' }} />
                    </div>
                    <span className={'mono ' + (b.up ? 'up' : 'down')} style={{ fontSize: 11, width: 48, textAlign: 'right' }}>{b.v != null ? `${b.v}亿` : '—'}</span>
                  </div>
                )) : null}
              </>
            ) : (
              <div className="serif" style={{ fontSize: 12, color: 'var(--ink-3)', padding: '6px 0' }}>
                暂无主力资金数据
                <div className="mono" style={{ fontSize: 9, color: 'var(--ink-3)', marginTop: 2 }}>需同花顺资金流采集 (ths_fund_flow)</div>
              </div>
            )}
          </BriefRegion>
          <BriefRegion title={`最近 7 日新闻 · ${news.length} 条`} cite="3" borderT full>
            {news.length > 0 ? news.map((n, i) => (
              <div key={i} style={{ display: 'flex', alignItems: 'baseline', gap: 8, padding: '4px 0', borderTop: i ? '1px dashed var(--line-soft)' : 'none' }}>
                <span style={{ width: 4, height: 4, background: 'var(--zhu)', flexShrink: 0, marginTop: 6 }} />
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div className="serif" style={{ fontSize: 12, color: 'var(--ink-1)', lineHeight: 1.5 }}>{n.t}</div>
                  <div className="mono" style={{ fontSize: 9, color: 'var(--ink-3)' }}>{n.s}</div>
                </div>
              </div>
            )) : (
              <div className="serif" style={{ fontSize: 12, color: 'var(--ink-3)', padding: '6px 0' }}>
                本地暂无相关新闻
                <div className="mono" style={{ fontSize: 9, color: 'var(--ink-3)', marginTop: 2 }}>可让 agent 跑 news_collect 抓取</div>
              </div>
            )}
          </BriefRegion>
          <BriefRegion title={`雪球热议 · ${comments.length}`} cite="4" borderT full>
            {xqSenti && (
              <div style={{ marginBottom: 8 }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4 }}>
                  <span className="mono" style={{ fontSize: 10, color: 'var(--ink-3)' }}>散户情绪</span>
                  <div style={{ flex: 1, height: 6, display: 'flex', overflow: 'hidden', background: 'var(--paper-2)' }}>
                    <div style={{ width: `${xqSenti.bull}%`, background: 'var(--zhu)' }} title={`看多 ${xqSenti.bull}%`} />
                    <div style={{ width: `${xqSenti.neutral}%`, background: 'var(--ink-3)' }} title={`中性 ${xqSenti.neutral}%`} />
                    <div style={{ width: `${xqSenti.bear}%`, background: 'var(--dai)' }} title={`看空 ${xqSenti.bear}%`} />
                  </div>
                  <span className="mono" style={{ fontSize: 10 }}><span className="up">多{xqSenti.bull}</span> <span className="down">空{xqSenti.bear}</span></span>
                </div>
                {xqSenti.summary && <div className="serif" style={{ fontSize: 11.5, color: 'var(--ink-2)', lineHeight: 1.5 }}>{xqSenti.summary}</div>}
              </div>
            )}
            {comments.length > 0 ? comments.slice(0, 5).map((c, i) => (
              <div key={i} style={{ padding: '5px 0', borderTop: i ? '1px dashed var(--line-soft)' : 'none' }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                  <span className="serif" style={{ fontSize: 11, color: 'var(--ink-1)', fontWeight: 500 }}>{c.author || '雪球用户'}</span>
                  <span className="mono" style={{ fontSize: 9, color: 'var(--ink-3)' }}>♥{c.likes} 评{c.replies}{c.ts ? ` · ${c.ts}` : ''}</span>
                  {c.url && <a href={c.url} target="_blank" rel="noopener noreferrer" className="mono hover-link" style={{ fontSize: 9, color: 'var(--ink-3)', marginLeft: 'auto', textDecoration: 'none' }}>原帖 ↗</a>}
                </div>
                <div className="serif" style={{ fontSize: 11.5, color: 'var(--ink-1)', lineHeight: 1.55, marginTop: 2 }}>{c.text}</div>
              </div>
            )) : (
              <div className="serif" style={{ fontSize: 12, color: xqErr ? 'var(--yin)' : 'var(--ink-3)', padding: '6px 0' }}>
                {xqErr ? `⚠ ${xqErr}` : `本地暂无雪球评论${backendUrl ? '，点下面「拉最新」现拉' : '（未连后端）'}`}
              </div>
            )}
            <div style={{ marginTop: 8, display: 'flex', alignItems: 'center', gap: 8 }}>
              <span onClick={refreshComments} className="hover-pill"
                style={{ cursor: (xqLoading || !backendUrl) ? 'default' : 'pointer', fontSize: 10, color: xqLoading ? 'var(--ink-3)' : 'var(--yin)', border: '1px solid var(--line)', padding: '3px 8px', opacity: backendUrl ? 1 : 0.5 }}>
                {xqLoading ? '⏳ 拉取中 (~30-60s)…' : '↻ 拉最新评论 + 情绪'}
              </span>
              <span className="mono" style={{ fontSize: 9, color: 'var(--ink-3)' }}>雪球社区 · 直连{sentiLoading ? ' · 情绪分析中…' : ''}</span>
            </div>
          </BriefRegion>
        </div>

        <div style={{ padding: '8px 18px', display: 'flex', gap: 16, fontFamily: 'var(--mono)', fontSize: 10, color: 'var(--ink-2)', background: 'rgba(241,234,217,0.4)', alignItems: 'center', flexWrap: 'wrap' }}>
          <span onClick={() => exportBriefToMd(sym, comments)} className="hover-link" style={{ cursor: 'pointer' }}>↧ 导出 markdown</span>
          <span onClick={() => { dispatch({ type: 'add_watch', items: [{ code: sym.code, name: sym.name }] }); setAdded(true); }} className="hover-link" style={{ cursor: 'pointer', color: added ? 'var(--zhu)' : 'inherit' }}>{added ? '✓ 已加入自选' : '＋ 加入自选'}</span>
          <span onClick={() => dispatch({ type: 'prefill', text: `${sym.name}（${sym.code}）跌破 ` })} className="hover-link" style={{ cursor: 'pointer' }}>👁 添加盯盘…</span>
          <span
            onClick={() => dispatch({ type: 'request_confirm', confirm: {
              tool: 'run_report', label: `跑深度研报 · ${sym.name} (${sym.code})`,
              sym,
              detail: '将启动 run_report 工具, 预计用时 5-8 分钟. 输出: 星级评级 / 目标价 / 止损 / 多空论证 / 风控提示.'
            }})}
            className="hover-link"
            style={{ cursor: 'pointer', color: 'var(--yin)' }}>⊟ 跑深度研报…（约 6 分钟）</span>
          <span style={{ flex: 1 }} />
          <span style={{ color: 'var(--ink-3)' }}>引用 1·2·3·4</span>
        </div>
      </div>
    </div>
  );
}

function BriefRegion({ title, cite, children, borderL, borderT, full }) {
  return (
    <div style={{ padding: '12px 16px', gridColumn: full ? '1 / -1' : undefined, borderLeft: borderL ? '1px solid var(--line)' : 'none', borderTop: borderT ? '1px solid var(--line)' : 'none' }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 8 }}>
        <span className="serif" style={{ fontSize: 12, color: 'var(--ink-1)', fontWeight: 500 }}>{title}</span>
        {cite && <sup style={{ display: 'inline-flex', alignItems: 'center', justifyContent: 'center', width: 15, height: 15, background: 'var(--yin)', color: 'var(--paper)', fontFamily: 'var(--serif)', fontSize: 9, cursor: 'pointer' }}>{cite}</sup>}
      </div>
      {children}
    </div>
  );
}

// ───── LLM 总结 (流式) ─────
function AiSummary({ text, streaming }) {
  return (
    <div style={{ display: 'flex', gap: 14, animation: 'fadeIn 200ms ease-out' }}>
      <AiAvatar />
      <div style={{ flex: 1, minWidth: 0, fontFamily: 'var(--serif)', fontSize: 14, color: 'var(--ink)', padding: '4px 0' }}>
        {renderChatMarkdown(text)}
        {streaming && <span style={{ display: 'inline-block', width: 6, height: 14, background: 'var(--ink)', marginLeft: 4, verticalAlign: -2, animation: 'blink 1s steps(2) infinite' }} />}
      </div>
    </div>
  );
}

// 研报卡片 — 持久化在 transcript, 关了抽屉也能再点开看全文 / 下载
function ReportCard({ msg, dispatch }) {
  const sym = msg.sym || {};
  const onView = () => dispatch({ type: 'open_report', sym, text: msg.text });
  const onDownload = () => {
    const blob = new Blob([msg.text || ''], { type: 'text/markdown;charset=utf-8' });
    triggerDownload(blob, `深度研报-${sym.name || sym.code || 'report'}.md`);
  };
  return (
    <div style={{ display: 'flex', gap: 14, animation: 'fadeIn 300ms ease-out' }}>
      <AiAvatar />
      <div style={{ flex: 1, minWidth: 0, background: 'var(--paper)', border: '1px solid var(--yin)', padding: '12px 16px' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 6 }}>
          <span style={{ width: 22, height: 22, background: 'var(--yin)', color: 'var(--paper)', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 12, flexShrink: 0 }}>📄</span>
          <span className="serif" style={{ fontSize: 14, fontWeight: 500, color: 'var(--ink)' }}>深度研报 · {sym.name || sym.code}{sym.name && sym.code && sym.name !== sym.code ? ` (${sym.code})` : ''}</span>
          <span className="mono" style={{ fontSize: 10, color: 'var(--yin)' }}>已生成</span>
        </div>
        {msg.path && <div className="mono" style={{ fontSize: 9.5, color: 'var(--ink-3)', marginBottom: 10, wordBreak: 'break-all' }}>{msg.path}</div>}
        <div style={{ display: 'flex', gap: 8 }}>
          <button onClick={onView} className="hover-pill" style={{ background: 'var(--ink)', color: 'var(--paper)', border: 'none', padding: '6px 14px', fontFamily: 'var(--serif)', fontSize: 12, cursor: 'pointer' }}>查看全文 ↗</button>
          <button onClick={onDownload} className="hover-pill" style={{ background: 'transparent', color: 'var(--ink-1)', border: '1px solid var(--line)', padding: '6px 14px', fontFamily: 'var(--serif)', fontSize: 12, cursor: 'pointer' }}>↧ 下载 .md</button>
        </div>
      </div>
    </div>
  );
}

function Cite({ n }) {
  return (
    <sup style={{
      display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
      width: 14, height: 14, background: 'var(--yin)', color: 'var(--paper)',
      fontFamily: 'var(--serif)', fontSize: 8, fontWeight: 500,
      margin: '0 1px', verticalAlign: 2, cursor: 'pointer'
    }} title={`引用 ${n}`}>{n}</sup>
  );
}

// ───────────────────────── Composer (含上下文 chip) ─────────────────────────

const SLASH_CMDS = [
  { cmd: 'clear',   args: '',                    desc: '清空当前会话 (重置上下文)' },
  { cmd: 'compact', args: '',                    desc: '压缩会话为摘要 (省 context)' },
  { cmd: 'new',     args: '',                    desc: '开一个新会话' },
  { cmd: 'save',    args: '',                    desc: '导出当前对话为 markdown' },
  { cmd: 'mode',    args: '[default|safe|auto]', desc: '切换权限模式' },
  { cmd: 'model',   args: '[name]',              desc: '切换 LLM 模型' },
  { cmd: 'watch',   args: '[on 5m | off]',       desc: '后台盯盘开关' },
  { cmd: 'tools',   args: '',                    desc: '打开工具面板 (⌘K)' },
  { cmd: 'llm',     args: '[on|off]',            desc: '切换真 Claude 总结' },
  { cmd: 'lesson',  args: '<一句话经验>',         desc: '沉淀对话经验到 memories/_shared, 下次提问自动 prepend 系统 prompt' },
  { cmd: 'help',    args: '',                    desc: '查看全部命令' },
];

const SLASH_HELP = [
  '可用命令：',
  '/clear — 清空当前会话并重置上下文（后端起全新 agent）',
  '/compact — 把当前会话用 LLM 压成一段摘要，节省上下文',
  '/new — 开一个新会话（旧会话保留在左栏）',
  '/save — 把当前对话导出成 markdown 文件',
  '/mode [default|safe|auto] — 切换工具确认模式',
  '/model [名称] — 切换后端 LLM',
  '/watch [on 5m|off] — 后台盯盘开关与间隔',
  '/tools — 打开工具面板',
  '/llm [on|off] — 切换真 Claude 总结',
  '/lesson <text> — 沉淀一条对话经验, 立即 prepend 到 buddy SYSTEM_PROMPT (无需重启)',
  '',
  '历史对话自动保存（浏览器 + 后端磁盘），左栏可切换/删除。',
].join('\n');

function Composer({ s, context, dispatch, startAgent, onCmdK }) {
  const [val, setVal] = useState('');
  const inputRef = useRef(null);
  const showSlash = val.startsWith('/');

  useEffect(() => { inputRef.current?.focus(); }, [s.currentSessionId]);

  // 外部预填 (如个股卡「添加盯盘」) — 写入输入框并聚焦, 不自动发送, 等用户补全价格
  useEffect(() => {
    const d = s.composerDraft;
    if (d && d.text != null) { setVal(d.text); inputRef.current?.focus(); }
  }, [s.composerDraft?.nonce]);

  const send = useCallback(() => {
    const text = val.trim();
    if (!text) return;
    if (text.startsWith('/')) {
      const [cmd, ...args] = text.slice(1).split(/\s+/);
      const sess = s.sessions.find(x => x.id === s.currentSessionId);
      if (cmd === 'mode' && ['default','safe','auto'].includes(args[0])) {
        dispatch({ type: 'set_mode', mode: args[0] });
      } else if (cmd === 'clear') {
        dispatch({ type: 'clear_session' });
      } else if (cmd === 'new' || cmd === 'reset') {
        dispatch({ type: 'new_session' });
      } else if (cmd === 'compact') {
        if (!s.backendUrl) {
          dispatch({ type: 'inject_message', message: { id: 'sys_'+Date.now(), role: 'ai', kind: 'answer', text: '⚠ /compact 需要连后端 (mock 模式不可用)。' } });
        } else {
          const transcript = (sess?.messages || []).map(m =>
            m.role === 'user' ? `用户: ${m.text}`
            : m.kind === 'answer' ? `助手: ${m.text}`
            : m.kind === 'chain' ? `工具链: ${(m.chain||[]).map(c => c.name).join(', ')}`
            : m.kind === 'brief' ? `速览卡: ${m.sym?.name || ''}` : ''
          ).filter(Boolean).join('\n');
          dispatch({ type: 'inject_message', message: { id: 'sys_'+Date.now(), role: 'ai', kind: 'answer', text: '⏳ 正在压缩会话 (LLM 总结中)…' } });
          fetch(`${s.backendUrl}/compact`, {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ session_id: sess?.backendSid || s.currentSessionId, transcript }),
          }).then(r => r.json()).then(d => {
            if (d && d.ok && d.summary) dispatch({ type: 'compact_session', summary: d.summary });
            else dispatch({ type: 'inject_message', message: { id: 'sys_'+Date.now(), role: 'ai', kind: 'answer', text: '⚠ 压缩失败或无内容可压缩。' } });
          }).catch(e => dispatch({ type: 'inject_message', message: { id: 'sys_'+Date.now(), role: 'ai', kind: 'answer', text: '⚠ 压缩请求失败: ' + e } }));
        }
      } else if (cmd === 'save') {
        if (sess) exportSessionToMd(sess);
      } else if (cmd === 'model' && args[0]) {
        const m = (s.models || []).find(x => x.id === args[0] || x.name === args[0]);
        if (m) dispatch({ type: 'set_backend_model', model: m.id });
      } else if (cmd === 'watch') {
        if (args[0] === 'off') dispatch({ type: 'set_watch', watch: { on: false } });
        else { const mins = parseInt((args[1]||args[0]||'').replace(/\D/g,''),10); dispatch({ type: 'set_watch', watch: { on: true, ...(mins ? { interval: mins } : {}) } }); }
      } else if (cmd === 'tools') {
        onCmdK && onCmdK();
      } else if (cmd === 'llm') {
        dispatch({ type: 'set_use_llm', value: args[0] !== 'off' });
      } else if (cmd === 'lesson') {
        const txt = args.join(' ').trim();
        if (!txt) {
          dispatch({ type: 'inject_message', message: { id: 'sys_'+Date.now(), role: 'ai', kind: 'answer', text: '用法: /lesson <一句话经验>。会写进 memories/_shared/conversation_lessons.md, 下次提问 buddy 系统 prompt 自动载入。' } });
        } else if (!s.backendUrl) {
          dispatch({ type: 'inject_message', message: { id: 'sys_'+Date.now(), role: 'ai', kind: 'answer', text: '⚠ /lesson 需要连后端。' } });
        } else {
          fetch(`${s.backendUrl}/lesson`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ text: txt }) })
            .then(r => r.json()).then(d => {
              const msg = d && d.ok ? `✓ 经验已记录: ${d.appended}` : `⚠ 记录失败: ${d && d.error || '未知'}`;
              dispatch({ type: 'inject_message', message: { id: 'sys_'+Date.now(), role: 'ai', kind: 'answer', text: msg } });
            }).catch(e => dispatch({ type: 'inject_message', message: { id: 'sys_'+Date.now(), role: 'ai', kind: 'answer', text: `⚠ /lesson 请求失败: ${e}` } }));
        }
      } else if (cmd === 'help') {
        dispatch({ type: 'inject_message', message: { id: 'help_'+Date.now(), role: 'ai', kind: 'answer', text: SLASH_HELP } });
      }
      setVal('');
      return;
    }
    if (s.status === 'idle') startAgent(text);
    else dispatch({ type: 'queue', text });
    setVal('');
  }, [val, s.status, s.backendUrl, s.models, s.sessions, s.currentSessionId, dispatch, startAgent, onCmdK]);

  const onKey = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(); }
  };

  return (
    <div style={{ padding: '8px 56px 8px', flexShrink: 0, position: 'relative' }}>
      {context && (
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6, fontSize: 10 }} className="mono">
          <span style={{ color: 'var(--ink-3)' }}>上下文</span>
          <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6, padding: '2px 8px', background: 'rgba(168,57,45,0.08)', border: '1px solid var(--yin)', color: 'var(--ink)' }}>
            <span style={{ width: 5, height: 5, background: 'var(--yin)', borderRadius: '50%' }} />
            <span className="serif" style={{ fontSize: 11 }}>{context.name}</span>
            <span style={{ color: 'var(--ink-3)' }}>{context.code}</span>
          </span>
          <span style={{ color: 'var(--ink-3)' }}>追问无需重复股票名</span>
        </div>
      )}
      {showSlash && <SlashMenu val={val} onPick={(cmd) => { setVal('/' + cmd + ' '); inputRef.current?.focus(); }} />}
      <div style={{ border: '1px solid var(--ink-2)', background: 'var(--paper)' }}>
        <div style={{ padding: '6px 14px', borderBottom: '1px dashed var(--line)', display: 'flex', alignItems: 'center', gap: 10 }}>
          <span className="mono" style={{ fontSize: 10, color: 'var(--ink-3)' }}>提示</span>
          {['/ 命令', '@ 引用此股'].map((x, i) => (
            <span key={i} className="mono" style={{ fontSize: 10, color: 'var(--ink-2)' }}>{x}</span>
          ))}
          <span style={{ flex: 1 }} />
          {s.status !== 'idle' && (
            <span className="mono" style={{ fontSize: 10, color: 'var(--ink-3)' }}>
              {s.status === 'streaming' ? '生成中 · 回车进入排队'
                : s.status === 'tool-running' ? '工具运行中 · 回车进入排队'
                : '等待…'}
            </span>
          )}
        </div>
        <div style={{ padding: '10px 14px 8px', display: 'flex', alignItems: 'flex-start', gap: 8 }}>
          <span className="serif" style={{ fontSize: 14, color: 'var(--ink-2)', marginTop: 1 }}>❯</span>
          <textarea ref={inputRef} value={val}
            onChange={(e) => setVal(e.target.value)} onKeyDown={onKey}
            placeholder={s.status === 'idle' ? '问个问题, 或输入代码/名称…' : '继续追问 (回车排队), 或 ESC 取消'}
            rows={1}
            style={{ flex: 1, border: 'none', outline: 'none', resize: 'none', fontFamily: 'var(--serif)', fontSize: 14, color: 'var(--ink)', background: 'transparent', lineHeight: 1.5, minHeight: 22, maxHeight: 120 }} />
        </div>
        <div style={{ padding: '6px 14px 8px', display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
          <div style={{ display: 'flex', gap: 6 }} className="mono">
            {['⊟ 上传', '@ 引用', '⌗ 板块'].map((x, i) => (
              <span key={i} style={{ fontSize: 10, color: 'var(--ink-2)', padding: '3px 7px', border: '1px solid var(--line)', cursor: 'pointer' }}>{x}</span>
            ))}
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            {/* 仅 mock 模式才警示, 真模式 (useRealLLM=true) 不占位 */}
            {!s.useRealLLM && (
              <span className="mono" style={{ fontSize: 10, color: 'var(--ink-3)' }}>
                本地 mock · {s.model}
              </span>
            )}
            <button onClick={send} style={{ background: 'var(--ink)', color: 'var(--paper)', border: 'none', padding: '5px 14px', fontFamily: 'var(--serif)', fontSize: 12, letterSpacing: '0.1em', cursor: 'pointer' }}>
              {s.status === 'idle' ? '发送 ↵' : '排队 ↵'}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

function SlashMenu({ val, onPick }) {
  const q = val.slice(1).split(/\s+/)[0].toLowerCase();
  const filtered = SLASH_CMDS.filter(c => c.cmd.startsWith(q));
  if (filtered.length === 0) return null;
  return (
    <div style={{
      position: 'absolute', bottom: 'calc(100% - 6px)', left: 56, right: 56,
      background: 'var(--paper)', border: '1px solid var(--ink)',
      boxShadow: '0 4px 24px rgba(0,0,0,0.12)', zIndex: 20, maxHeight: 280, overflowY: 'auto'
    }}>
      <div style={{ padding: '6px 12px', borderBottom: '1px solid var(--line-soft)', fontFamily: 'var(--mono)', fontSize: 10, color: 'var(--ink-3)', letterSpacing: '0.15em' }}>斜杠命令</div>
      {filtered.map((c, i) => (
        <div key={c.cmd} onClick={() => onPick(c.cmd)} className="hover-row"
          style={{ padding: '8px 12px', display: 'flex', alignItems: 'baseline', gap: 10, cursor: 'pointer', borderBottom: i < filtered.length - 1 ? '1px solid var(--line-soft)' : 'none' }}>
          <code style={{ fontFamily: 'var(--mono)', fontSize: 12, color: 'var(--ink)' }}>/{c.cmd}</code>
          {c.args && <code style={{ fontFamily: 'var(--mono)', fontSize: 10, color: 'var(--ink-3)' }}>{c.args}</code>}
          <span className="serif" style={{ fontSize: 12, color: 'var(--ink-2)', marginLeft: 'auto' }}>{c.desc}</span>
        </div>
      ))}
    </div>
  );
}

// ───────────────────────── 状态行 ─────────────────────────

function StatusBar({ s, dispatch, onCmdK }) {
  const clk = marketClock(new Date());
  const modeColor = s.mode === 'safe' ? 'var(--jin)' : s.mode === 'auto' ? 'var(--yin)' : 'var(--dai)';
  const modeBg    = s.mode === 'safe' ? 'rgba(138,111,63,0.12)' : s.mode === 'auto' ? 'rgba(168,57,45,0.12)' : 'rgba(74,107,92,0.12)';
  return (
    <footer style={{ padding: '6px 32px 8px', borderTop: '1px solid var(--line-soft)', display: 'flex', alignItems: 'center', gap: 14, fontFamily: 'var(--mono)', fontSize: 10, color: 'var(--ink-2)', flexShrink: 0, background: 'rgba(241,234,217,0.4)' }}>
      <select value={s.mode} onChange={(e) => dispatch({ type: 'set_mode', mode: e.target.value })}
        style={{ padding: '2px 6px', background: modeBg, color: modeColor, border: 'none', fontFamily: 'var(--mono)', fontSize: 10, fontWeight: 500, letterSpacing: '0.05em', cursor: 'pointer' }}>
        <option value="default">🛡 default</option>
        <option value="safe">🚦 safe</option>
        <option value="auto">⚡ auto</option>
      </select>
      {s.backendUrl && s.models && s.models.length > 0 ? (
        <span style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
          <span style={{ color: 'var(--ink-3)' }}>模型</span>
          <select value={s.backendModel || ''} onChange={(e) => dispatch({ type: 'set_backend_model', model: e.target.value })}
            title="后端可用 model"
            style={{ padding: '1px 5px', background: 'transparent', color: 'var(--ink-1)', border: '1px solid var(--line)', fontFamily: 'var(--mono)', fontSize: 10, cursor: 'pointer', maxWidth: 180 }}>
            {s.models.map(m => (
              <option key={m.id} value={m.id} disabled={m.available === false}>
                {m.name || m.id}{m.available === false ? ' (不可用)' : ''}
              </option>
            ))}
          </select>
          <span style={{ width: 5, height: 5, borderRadius: '50%', background: 'var(--zhu)' }} title="后端在线" />
        </span>
      ) : (
        <span><span style={{ color: 'var(--ink-3)' }}>模型</span> <span style={{ color: 'var(--ink-1)' }}>{s.useRealLLM ? 'Claude (实时)' : s.model}</span></span>
      )}
      <span
        onClick={() => dispatch({ type: 'set_use_llm', value: !s.useRealLLM })}
        title="切换 mock 数据 / 真 Claude 生成回复"
        className="hover-pill"
        style={{ padding: '1px 5px', border: '1px solid ' + (s.useRealLLM ? 'var(--yin)' : 'var(--line)'), color: s.useRealLLM ? 'var(--yin)' : 'var(--ink-2)', cursor: 'pointer' }}>
        {s.useRealLLM ? '● 真 LLM' : '○ 切真 LLM'}
      </span>
      <Sep />
      <span><span style={{ color: 'var(--ink-3)' }}>token</span> <span style={{ color: 'var(--ink-1)' }}>{(s.tokens / 1000).toFixed(1)}k</span></span>
      <Sep />
      <span><span style={{ color: 'var(--ink-3)' }}>盯盘</span> <span style={{ color: 'var(--ink-1)' }}>{s.watch.interval} 分钟</span> <span style={{ color: clk.open ? 'var(--zhu)' : 'var(--ink-3)' }}>· {clk.label}</span></span>
      <Sep />
      <span style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
        <span style={{ color: 'var(--ink-3)' }}>已免确认</span>
        {s.autoApproved.length === 0 && <span style={{ color: 'var(--ink-3)' }}>(无)</span>}
        {s.autoApproved.map((t) => (
          <span key={t} onClick={() => dispatch({ type: 'remove_auto', name: t })}
            className="hover-pill"
            style={{ padding: '1px 5px', border: '1px solid var(--line)', color: 'var(--ink-1)', cursor: 'pointer' }}>
            {t} <span style={{ color: 'var(--ink-3)' }}>×</span>
          </span>
        ))}
      </span>
      <span style={{ flex: 1 }} />
      <span onClick={() => dispatch({ type: 'toggle_theme' })} className="hover-pill"
        title="切换 宣纸 / 月夜 主题"
        style={{ cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 4, padding: '1px 5px' }}>
        <span style={{ color: 'var(--ink-3)' }}>{s.theme === 'dark' ? '🌙' : '☀'}</span>
        <span>{s.theme === 'dark' ? '月夜' : '宣纸'}</span>
      </span>
      <Sep />
      <span onClick={onCmdK} className="hover-pill" style={{ cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 4, padding: '1px 5px' }}>
        <span style={{ color: 'var(--ink-3)' }}>⌘K</span> 工具面板
      </span>
      <span style={{ color: 'var(--ink-3)' }}>v2.1</span>
    </footer>
  );
}

const Sep = () => <span style={{ color: 'var(--ink-3)' }}>·</span>;

// ───────────────────────── 右栏 ─────────────────────────

function RightRail({ s, session, dispatch, startAgent }) {
  const r = s.activeRound;
  const chainMsg = r && session ? session.messages.find(m => m.id === r.chainId) : null;
  const refs = chainMsg
    ? chainMsg.chain.filter(c => c.status === 'done').map((c, i) => ({ n: i + 1, src: c.name, t: c.t ? `~${c.t}s` : '', d: c.result }))
    : [];

  // ② 盯盘规则 — 同 LeftRail: 连后端用真规则 (空则空), 仅 mock 模式用写死的 ALERTS
  const alerts = s.backendUrl
    ? (s.liveAlerts || []).map((a, i) => ({
        id: a.id || `alert_${i}`,
        name: a.name || a.code || '规则',
        rule: a.rule || a.desc || `${a.type || ''} ${a.value ?? ''}`.trim(),
        cur: a.cur || (a.changePercent != null ? `${a.changePercent >= 0 ? '+' : ''}${a.changePercent}%` : (a.price != null ? String(a.price) : '—')),
        pct: a.pct ?? 50,
        far: a.far || '',
      }))
    : ALERTS;

  return (
    <aside style={{ width: 312, display: 'flex', flexDirection: 'column', flexShrink: 0, background: 'rgba(241,234,217,0.4)', overflow: 'hidden' }}>
      <div style={{ padding: '14px 18px 10px', borderBottom: '1px solid var(--line)', display: 'flex', alignItems: 'center', gap: 10, flexShrink: 0 }}>
        <span className="serif" style={{ fontSize: 14, fontWeight: 500, color: 'var(--ink)' }}>盯盘</span>
        <span style={{ flex: 1 }} />
        <span className="mono" style={{ fontSize: 10, color: 'var(--ink-3)', cursor: 'pointer' }}>/watch ▾</span>
      </div>

      <div style={{ flex: 1, overflowY: 'auto', minHeight: 0 }}>
        <div style={{ padding: '12px 18px 8px' }}>
          <div className="mono" style={{ fontSize: 9, color: 'var(--ink-3)', letterSpacing: '0.2em', marginBottom: 8 }}>最近触发</div>
          {s.lastFired ? (() => {
            const f = s.lastFired;
            const up = !String(f.cur || '').startsWith('-');
            return (
              <div style={{ border: '1px solid var(--yin)', padding: '10px 12px', background: 'rgba(168,57,45,0.06)' }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
                  <span style={{ width: 18, height: 18, background: 'var(--yin)', color: 'var(--paper)', fontFamily: 'var(--serif)', fontSize: 11, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>🔔</span>
                  <span className="serif" style={{ fontSize: 13, color: 'var(--ink)', fontWeight: 500 }}>{f.name}</span>
                  {f.cur && <span className={'mono ' + (up ? 'up' : 'down')} style={{ fontSize: 11, fontWeight: 500, marginLeft: 'auto' }}>{f.cur}</span>}
                </div>
                <div className="serif" style={{ fontSize: 12, color: 'var(--ink-1)', lineHeight: 1.5 }}>
                  触发规则 <span className="mono">{f.rule || '—'}</span>
                </div>
                <div className="mono" style={{ fontSize: 10, color: 'var(--ink-3)', marginTop: 6 }}>
                  {[f.price !== '' && f.price != null ? f.price : null, f.time, f.vol ? `量比 ${f.vol}` : null].filter(Boolean).join(' · ')}
                </div>
                <div style={{ display: 'flex', gap: 6, marginTop: 8 }} className="mono">
                  <span onClick={() => startAgent(`${f.name}为什么${up ? '涨' : '跌'}`)}
                    className="hover-pill"
                    style={{ fontSize: 10, color: 'var(--ink-2)', padding: '2px 6px', border: '1px solid var(--line)', cursor: 'pointer' }}>追问 →</span>
                </div>
              </div>
            );
          })() : (
            <div style={{ border: '1px dashed var(--line)', padding: '12px', textAlign: 'center' }}>
              <div className="serif" style={{ fontSize: 12, color: 'var(--ink-3)' }}>暂无触发</div>
              <div className="mono" style={{ fontSize: 9, color: 'var(--ink-3)', marginTop: 4 }}>
                {s.backendUrl ? '盘中规则命中后显示' : '未连后端'}
              </div>
            </div>
          )}
        </div>

        <div style={{ padding: '8px 18px' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
            <span className="mono" style={{ fontSize: 9, color: 'var(--ink-3)', letterSpacing: '0.2em' }}>活跃规则 · {alerts.length}{s.backendUrl ? ' · live' : ' · mock'}</span>
            <span className="mono"
              title="添加一条盯盘规则"
              style={{ fontSize: 10, color: 'var(--yin)', cursor: 'pointer', fontWeight: 500 }}
              onClick={async () => {
                if (!s.backendUrl) { alert('未连后端 (mock 模式)'); return; }
                const codeRaw = prompt('股票代码 (e.g. SH600519 或 600519):');
                if (!codeRaw) return;
                const kindLabel = prompt('类型? 输 1=跌破价 / 2=涨破价 / 3=涨幅% / 4=跌幅%', '1');
                const kindMap = { '1': 'price_below', '2': 'price_above', '3': 'pct_above', '4': 'pct_below' };
                const kind = kindMap[kindLabel] || 'price_below';
                const thresholdStr = prompt(`阈值 (${kind} 的值):`);
                if (!thresholdStr) return;
                const threshold = parseFloat(thresholdStr);
                if (isNaN(threshold)) { alert('阈值不是数字'); return; }
                try {
                  const r = await fetch(`${s.backendUrl}/alerts`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ code: codeRaw, kind, threshold }),
                  });
                  const j = await r.json();
                  if (j.ok) {
                    const ar = await fetch(`${s.backendUrl}/alerts`).then(r => r.json());
                    if (ar && Array.isArray(ar.alerts)) dispatch({ type: 'set_alerts', alerts: ar.alerts });
                  } else {
                    alert(`添加失败: ${j.reason || 'unknown'}`);
                  }
                } catch (err) {
                  alert(`添加失败: ${err.message}`);
                }
              }}>+ 添加</span>
          </div>
          {alerts.length === 0 && (
            <div className="serif" style={{ fontSize: 11, color: 'var(--ink-3)', padding: '4px 0' }}>暂无盯盘规则</div>
          )}
          {alerts.map((a, i) => (
            <div key={a.id} className="alert-row-aside"
                 onMouseEnter={(e) => { const x = e.currentTarget.querySelector('.alert-del-aside'); if (x) x.style.opacity = 1; }}
                 onMouseLeave={(e) => { const x = e.currentTarget.querySelector('.alert-del-aside'); if (x) x.style.opacity = 0; }}
                 style={{ padding: '10px 0', borderTop: i ? '1px solid var(--line-soft)' : 'none', position: 'relative' }}>
              <div style={{ display: 'flex', alignItems: 'baseline', gap: 6, marginBottom: 4 }}>
                <span className="serif" style={{ fontSize: 12.5, color: 'var(--ink)' }}>{a.name}</span>
                <span className="mono" style={{ fontSize: 10, color: 'var(--ink-3)' }}>{a.rule}</span>
                <span style={{ flex: 1 }} />
                <span className="mono" style={{ fontSize: 11, color: 'var(--ink-1)' }}>{a.cur}</span>
                {s.backendUrl && (a.id || a.code) && (
                  <span className="alert-del-aside"
                        title="删除此盯盘"
                        style={{ opacity: 0, transition: 'opacity 0.15s', cursor: 'pointer',
                                 color: 'var(--ink-3)', fontSize: 13, marginLeft: 4, lineHeight: 1 }}
                        onClick={async (e) => {
                          e.stopPropagation();
                          if (!confirm(`删除盯盘 "${a.name || a.code}"?`)) return;
                          const ruleId = a.id || a.code;
                          try {
                            const r = await fetch(`${s.backendUrl}/alerts/${encodeURIComponent(ruleId)}`, { method: 'DELETE' });
                            const j = await r.json();
                            if (j.ok) {
                              const ar = await fetch(`${s.backendUrl}/alerts`).then(r => r.json());
                              if (ar && Array.isArray(ar.alerts)) dispatch({ type: 'set_alerts', alerts: ar.alerts });
                            } else {
                              alert(`删除失败: ${j.reason || 'unknown'}`);
                            }
                          } catch (err) {
                            alert(`删除失败: ${err.message}`);
                          }
                        }}>×</span>
                )}
              </div>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                <div style={{ flex: 1, height: 2, background: 'var(--paper-2)' }}>
                  <div style={{ width: `${a.pct}%`, height: '100%', background: 'var(--ink-1)' }} />
                </div>
                <span className="mono" style={{ fontSize: 9, color: 'var(--ink-3)' }}>{a.far}</span>
              </div>
            </div>
          ))}
        </div>

        <div style={{ padding: '14px 18px 10px', borderTop: '1px solid var(--line)' }}>
          <div className="mono" style={{ fontSize: 9, color: 'var(--ink-3)', letterSpacing: '0.2em', marginBottom: 10 }}>当前任务引用 · {refs.length}</div>
          {refs.length === 0 && <div className="serif" style={{ fontSize: 12, color: 'var(--ink-3)', padding: '4px 0' }}>等待 agent 调用工具…</div>}
          {refs.map((c, i) => (
            <div key={i} style={{ display: 'flex', gap: 10, padding: '8px 0', borderTop: i ? '1px solid var(--line-soft)' : 'none' }}>
              <span style={{ width: 18, height: 18, background: 'var(--yin)', color: 'var(--paper)', display: 'flex', alignItems: 'center', justifyContent: 'center', fontFamily: 'var(--serif)', fontSize: 10, flexShrink: 0 }}>{c.n}</span>
              <div style={{ flex: 1, minWidth: 0 }}>
                <code style={{ fontFamily: 'var(--mono)', fontSize: 10.5, color: 'var(--ink-1)' }}>{c.src}</code>
                <div className="serif" style={{ fontSize: 11.5, color: 'var(--ink-2)', marginTop: 1 }}>{c.d}</div>
                <div className="mono" style={{ fontSize: 9, color: 'var(--ink-3)', marginTop: 1 }}>{c.t}</div>
              </div>
            </div>
          ))}
        </div>
      </div>

    </aside>
  );
}

// ───────────────────────── Confirm Modal ─────────────────────────

function ConfirmModal({ s, dispatch, agentRef, startAgent }) {
  const c = s.confirm;
  // ④ y/n/a 回传后端 (如果是后端发起的) + 本地 dispatch
  const resolve = useCallback((choice) => {
    if (c?.fromBackend && c.turn_id && agentRef?.current?.resolveConfirm) {
      agentRef.current.resolveConfirm(c.turn_id, choice);
    }
    dispatch({ type: 'resolve_confirm', choice });   // 先开抽屉 / 关 confirm
    // 本地发起的 run_report 确认 (来自速览卡按钮): 连后端时**只在这里触发一次**真实研报
    if (!c?.fromBackend && (choice === 'y' || choice === 'a') && c?.tool === 'run_report' && c?.sym && s.backendUrl && startAgent) {
      startAgent(`跑 ${c.sym.name}(${c.sym.code}) 深度研报`);
    }
  }, [c, agentRef, dispatch, s.backendUrl, startAgent]);

  useEffect(() => {
    const k = (e) => {
      const key = e.key.toLowerCase();
      if (key === 'y') { e.preventDefault(); resolve('y'); }
      if (key === 'n') { e.preventDefault(); resolve('n'); }
      if (key === 'a') { e.preventDefault(); resolve('a'); }
    };
    window.addEventListener('keydown', k);
    return () => window.removeEventListener('keydown', k);
  }, [resolve]);

  return (
    <div onClick={() => resolve('n')} style={{
      position: 'fixed', inset: 0, background: 'rgba(28,24,20,0.55)', backdropFilter: 'blur(2px)',
      display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 100, animation: 'fadeIn 200ms ease-out'
    }}>
      <div onClick={(e) => e.stopPropagation()} style={{
        width: 520, background: 'var(--paper)', border: '2px solid var(--yin)',
        boxShadow: '0 24px 80px rgba(0,0,0,0.3)'
      }}>
        <div style={{ padding: '18px 24px 14px', borderBottom: '1px solid var(--line)', display: 'flex', alignItems: 'flex-start', gap: 14 }}>
          <div style={{ width: 40, height: 40, background: 'var(--yin)', color: 'var(--paper)', display: 'flex', alignItems: 'center', justifyContent: 'center', fontFamily: 'var(--serif)', fontSize: 22, fontWeight: 500, flexShrink: 0 }}>⚠</div>
          <div style={{ flex: 1 }}>
            <div className="mono" style={{ fontSize: 10, color: 'var(--yin)', letterSpacing: '0.2em', marginBottom: 4 }}>
              {c.fromBackend ? '后端等待确认' : '等待工具确认'}
            </div>
            <div className="serif" style={{ fontSize: 17, color: 'var(--ink)', fontWeight: 500 }}>{c.label}</div>
          </div>
        </div>
        <div style={{ padding: '14px 24px', fontFamily: 'var(--serif)', fontSize: 13.5, color: 'var(--ink-1)', lineHeight: 1.75, whiteSpace: 'pre-wrap' }}>
          {c.detail}
        </div>
        <div style={{ padding: '8px 24px 18px' }}>
          <div className="mono" style={{ fontSize: 10, color: 'var(--ink-3)', letterSpacing: '0.15em', marginBottom: 8 }}>选择操作</div>
          <div style={{ display: 'flex', gap: 10 }}>
            <button onClick={() => resolve('y')}
              style={{ flex: 1, padding: '10px', background: 'var(--ink)', color: 'var(--paper)', border: 'none', fontFamily: 'var(--serif)', fontSize: 13, cursor: 'pointer', display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 2 }}>
              <span>同意 · 一次</span>
              <span className="mono" style={{ fontSize: 9, opacity: 0.6 }}>Y</span>
            </button>
            <button onClick={() => resolve('a')}
              style={{ flex: 1, padding: '10px', background: 'transparent', color: 'var(--yin)', border: '1px solid var(--yin)', fontFamily: 'var(--serif)', fontSize: 13, cursor: 'pointer', display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 2 }}>
              <span>同意 · 本会话永久</span>
              <span className="mono" style={{ fontSize: 9, opacity: 0.7 }}>A</span>
            </button>
            <button onClick={() => resolve('n')}
              style={{ flex: 1, padding: '10px', background: 'transparent', color: 'var(--ink-2)', border: '1px solid var(--line)', fontFamily: 'var(--serif)', fontSize: 13, cursor: 'pointer', display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 2 }}>
              <span>拒绝</span>
              <span className="mono" style={{ fontSize: 9, opacity: 0.6 }}>N · ESC</span>
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

// ───────────────────────── ⌘K Tool Palette ─────────────────────────

function CmdKPalette({ onClose, startAgent, dispatch }) {
  const [q, setQ] = useState('');
  const inputRef = useRef(null);
  useEffect(() => { inputRef.current?.focus(); }, []);

  const groups = useMemo(() => {
    const ql = q.toLowerCase();
    const filtered = TOOLS_META.filter(t =>
      !q || t.name.toLowerCase().includes(ql) || t.cn.includes(q) || t.desc.toLowerCase().includes(ql) || t.cat.includes(q)
    );
    const byCat = {};
    filtered.forEach(t => { if (!byCat[t.cat]) byCat[t.cat] = []; byCat[t.cat].push(t); });
    return Object.entries(byCat);
  }, [q]);

  const costColor = (c) => c === 'instant' ? 'var(--dai)' : c === 'seconds' ? 'var(--jin)' : 'var(--yin)';
  const costLabel = (c) => c === 'instant' ? '即时' : c === 'seconds' ? '秒级' : '分钟级';

  return (
    <div onClick={onClose} style={{
      position: 'fixed', inset: 0, background: 'rgba(28,24,20,0.55)', backdropFilter: 'blur(2px)',
      display: 'flex', alignItems: 'flex-start', justifyContent: 'center', paddingTop: 80, zIndex: 90,
      animation: 'fadeIn 150ms ease-out'
    }}>
      <div onClick={(e) => e.stopPropagation()} style={{
        width: 640, maxHeight: 'calc(100vh - 140px)', background: 'var(--paper)', border: '1px solid var(--ink)',
        boxShadow: '0 24px 80px rgba(0,0,0,0.25)', display: 'flex', flexDirection: 'column'
      }}>
        <div style={{ padding: '14px 18px', borderBottom: '1px solid var(--line)', display: 'flex', alignItems: 'center', gap: 10 }}>
          <span className="seal" style={{ width: 24, height: 24, fontSize: 13 }}>⌘</span>
          <input ref={inputRef} value={q} onChange={(e) => setQ(e.target.value)}
            placeholder="搜索工具 — 名称 / 中文 / 用途 / 分类"
            style={{ flex: 1, border: 'none', outline: 'none', background: 'transparent', fontFamily: 'var(--serif)', fontSize: 15, color: 'var(--ink)' }} />
          <span className="mono" style={{ fontSize: 10, color: 'var(--ink-3)' }}>26 工具</span>
          <span className="mono" style={{ fontSize: 10, color: 'var(--ink-3)', padding: '1px 5px', border: '1px solid var(--line)' }}>ESC</span>
        </div>
        <div style={{ flex: 1, overflowY: 'auto' }}>
          {groups.map(([cat, items]) => (
            <div key={cat}>
              <div className="mono" style={{ padding: '10px 18px 6px', fontSize: 10, color: 'var(--ink-3)', letterSpacing: '0.2em' }}>{cat.toUpperCase()} · {items.length}</div>
              {items.map((t) => (
                <div key={t.name}
                  onClick={() => {
                    onClose();
                    if (t.cost === 'minutes') {
                      dispatch({ type: 'request_confirm', confirm: {
                        tool: t.name, label: `${t.name} · ${t.cn}`,
                        detail: `${t.desc}  预计耗时 5-8 分钟, 需手动确认.`
                      }});
                    } else {
                      startAgent(`用 ${t.name} 看一下`);
                    }
                  }}
                  className="hover-row"
                  style={{ padding: '8px 18px', display: 'flex', alignItems: 'center', gap: 10, cursor: 'pointer', borderBottom: '1px solid var(--line-soft)' }}>
                  <code style={{ fontFamily: 'var(--mono)', fontSize: 12, color: 'var(--ink)', width: 160 }}>{t.name}</code>
                  <span className="serif" style={{ fontSize: 13, color: 'var(--ink-1)', width: 70 }}>{t.cn}</span>
                  <span className="serif" style={{ fontSize: 12, color: 'var(--ink-2)', flex: 1 }}>{t.desc}</span>
                  <span className="mono" style={{ fontSize: 9, color: costColor(t.cost), padding: '1px 6px', border: `1px solid ${costColor(t.cost)}` }}>{costLabel(t.cost)}</span>
                </div>
              ))}
            </div>
          ))}
          {groups.length === 0 && <div style={{ padding: 24, textAlign: 'center', fontFamily: 'var(--serif)', color: 'var(--ink-3)' }}>没有匹配的工具</div>}
        </div>
      </div>
    </div>
  );
}

// ───────────────────────── Alert Toast ─────────────────────────

function AlertToast({ toast, dispatch, startAgent }) {
  useEffect(() => {
    const t = setTimeout(() => dispatch({ type: 'dismiss_toast' }), 10000);
    return () => clearTimeout(t);
  }, [toast, dispatch]);
  return (
    <div style={{
      position: 'fixed', right: 332, bottom: 80, width: 320,
      background: 'var(--paper)', border: '2px solid var(--yin)',
      boxShadow: '0 12px 40px rgba(168,57,45,0.25), 0 4px 12px rgba(0,0,0,0.15)',
      zIndex: 80, animation: 'slideInRight 350ms cubic-bezier(.2,.7,.3,1)'
    }}>
      <div style={{ padding: '12px 16px 10px', display: 'flex', alignItems: 'flex-start', gap: 12, borderBottom: '1px solid var(--line)' }}>
        <div style={{ width: 32, height: 32, background: 'var(--yin)', color: 'var(--paper)', display: 'flex', alignItems: 'center', justifyContent: 'center', fontFamily: 'var(--serif)', fontSize: 16, flexShrink: 0, animation: 'shake 600ms ease-in-out' }}>🔔</div>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div className="mono" style={{ fontSize: 9, color: 'var(--yin)', letterSpacing: '0.2em', marginBottom: 3 }}>盯盘触发 · {toast.time}</div>
          <div style={{ display: 'flex', alignItems: 'baseline', gap: 8 }}>
            <span className="serif" style={{ fontSize: 15, fontWeight: 500, color: 'var(--ink)' }}>{toast.name}</span>
            <span className="mono" style={{ fontSize: 10, color: 'var(--ink-3)' }}>{toast.code}</span>
            <span className="mono up" style={{ fontSize: 13, fontWeight: 600, marginLeft: 'auto' }}>{toast.cur}</span>
          </div>
        </div>
        <button onClick={() => dispatch({ type: 'dismiss_toast' })} style={{ background: 'transparent', border: 'none', color: 'var(--ink-3)', cursor: 'pointer', padding: 0, fontSize: 14, lineHeight: 1 }}>×</button>
      </div>
      <div style={{ padding: '10px 16px', fontFamily: 'var(--serif)', fontSize: 12.5, color: 'var(--ink-1)', lineHeight: 1.55 }}>
        触发规则 <span className="mono" style={{ color: 'var(--ink)' }}>{toast.rule}</span> · 现价 <span className="mono">{toast.price}</span> · 量比 <span className="mono">{toast.vol}</span>
      </div>
      <div style={{ padding: '6px 16px 12px', display: 'flex', gap: 8 }}>
        <button onClick={() => { dispatch({ type: 'dismiss_toast' }); startAgent(`${toast.name}为什么涨`); }}
          style={{ flex: 1, padding: '7px', background: 'var(--ink)', color: 'var(--paper)', border: 'none', fontFamily: 'var(--serif)', fontSize: 12, cursor: 'pointer' }}>
          ❯ 追问 agent
        </button>
        <button onClick={() => dispatch({ type: 'dismiss_toast' })}
          style={{ padding: '7px 12px', background: 'transparent', color: 'var(--ink-2)', border: '1px solid var(--line)', fontFamily: 'var(--serif)', fontSize: 12, cursor: 'pointer' }}>
          稍后
        </button>
      </div>
    </div>
  );
}

// ───────────────────────── 研报抽屉 ─────────────────────────

const REPORT_STEPS = [
  { phase: '数据收集', label: '拉取财报 · 近 8 季', t: 0.9 },
  { phase: '数据收集', label: '行业景气 + 同行排序', t: 0.9 },
  { phase: '数据收集', label: '近 30 日新闻 + 研报', t: 0.9 },
  { phase: '数据收集', label: '主力资金 + 龙虎榜', t: 0.9 },
  { phase: '深度分析', label: '估值横向对比 (PE / PEG)', t: 0.9 },
  { phase: '深度分析', label: '盈利质量 + 现金流', t: 0.9 },
  { phase: '深度分析', label: '多空论证 + 因子打分', t: 0.9 },
  { phase: '生成', label: '撰写结论 + 风险提示', t: 0.9 },
];

function buildReportText(sym) {
  if (!sym) sym = window.STOCK_DB['300750'];
  return `# ${sym.name} · 深度研报

**评级**　★★★★☆ (4.2 / 5)
**目标价**　${(sym.price * 1.08).toFixed(0)} - ${(sym.price * 1.18).toFixed(0)}
**止损位**　${(sym.price * 0.88).toFixed(0)}　|　**操作建议**　分批跟进, 中线持有

---

## 一、核心结论

${sym.name} 当前位于行业景气复苏中段, 公司基本面持续验证, 估值仍在合理区间. Q3 业绩超一致预期, 毛利率 ${sym.industry.includes('电池') ? '28.4%' : '行业领先水平'}, 经营性现金流改善明显. 短期看, 主力资金连续 3 日加仓, 雪球情绪偏多 ${sym.xq_bull}%; 中期看, 海外产能扩张 + 储能业务增量打开第二曲线.

## 二、多头逻辑 (★★★★☆)

1. **业绩超预期** — Q3 单季净利同比 +25.9%, 远超 markets 预期的 +18%
2. **毛利率拐点** — 上游碳酸锂价格企稳, 公司议价能力强, 毛利率连续 3 季度回升
3. **储能放量** — 储能业务占比首破 25%, 海外大单 (19 GWh) 已签订
4. **资金面共振** — 北向资金近月净流入超 38 亿, 机构席位活跃

## 三、空头逻辑 (★★☆☆☆)

1. **行业供给压力** — 二线厂商 2025 年产能扩张 +60%, 价格战风险待消化
2. **欧洲需求放缓** — 主要海外市场新能源车补贴退坡, 短期承压
3. **估值已不便宜** — PE TTM ${sym.pe} 处于近 3 年中位偏上

## 四、关键数据

| 指标 | 2024 Q3 | 2024 Q2 | YoY | 一致预期 |
| --- | --- | --- | --- | --- |
| 营业收入 (亿) | 922.8 | 870.0 | +12.4% | 870.2 |
| 归母净利 (亿) | 131.4 | 123.5 | +25.9% | 124.0 |
| 毛利率 | 28.4% | 26.6% | +6.0 pct | 26.8% |
| 经营现金流 (亿) | 274.6 | 203.8 | +38.2% | — |

## 五、风险提示

- 欧洲电动车需求继续放缓
- 海外建厂资本开支高峰
- 碳酸锂价格反弹挤压利润
- 二线厂商低价竞争

---

> 本研报由 觀瀾 · A 股 AI 助手 自动生成, 数据更新至 ${new Date().toLocaleString('zh-CN')}.
> 仅供参考, 不构成投资建议.`;
}

function ReportDrawer({ drawer, dispatch, backendUrl }) {
  const sym = drawer.sym;
  const totalSteps = REPORT_STEPS.length;
  // 真·已用时计时器 (每秒走字) — 真后端模式下这才是"真实速度/进度"
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    if (drawer.status !== 'running') return;
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, [drawer.status]);
  const elapsed = drawer.startedAt ? Math.max(0, Math.floor((now - drawer.startedAt) / 1000)) : 0;
  const mmss = (n) => `${Math.floor(n / 60)}:${String(n % 60).padStart(2, '0')}`;
  const phaseGroups = useMemo(() => {
    const out = [];
    let curPhase = null, curGroup = null;
    REPORT_STEPS.forEach((s, i) => {
      if (s.phase !== curPhase) {
        curPhase = s.phase;
        curGroup = { phase: s.phase, items: [] };
        out.push(curGroup);
      }
      curGroup.items.push({ ...s, idx: i });
    });
    return out;
  }, []);

  return (
    <div style={{
      position: 'fixed', top: 0, right: 0, bottom: 0, width: 560,
      background: 'var(--paper)', borderLeft: '2px solid var(--yin)',
      boxShadow: '-20px 0 60px rgba(0,0,0,0.18)',
      zIndex: 95, display: 'flex', flexDirection: 'column',
      animation: 'slideInRight 350ms cubic-bezier(.2,.7,.3,1)',
    }}>
      {/* 头 */}
      <div style={{ padding: '16px 22px 12px', borderBottom: '2px solid var(--ink)', display: 'flex', alignItems: 'flex-start', gap: 14, flexShrink: 0 }}>
        <div style={{ width: 38, height: 38, background: 'var(--yin)', color: 'var(--paper)', display: 'flex', alignItems: 'center', justifyContent: 'center', fontFamily: 'var(--serif)', fontSize: 18, flexShrink: 0 }}>
          {drawer.status === 'done' ? '✓' : '⏳'}
        </div>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div className="mono" style={{ fontSize: 10, color: 'var(--yin)', letterSpacing: '0.2em', marginBottom: 3 }}>
            {drawer.status === 'done' ? '深度研报 · 已完成' : '深度研报 · 后台运行'}
          </div>
          <div className="serif" style={{ fontSize: 17, color: 'var(--ink)', fontWeight: 500 }}>{sym.name}{sym.name !== sym.code ? ` · ${sym.code}` : ''}</div>
          <div className="mono" style={{ fontSize: 10, color: 'var(--ink-3)', marginTop: 3 }}>
            {drawer.status === 'done'
              ? (drawer.startedAt ? `已完成 · 用时 ${mmss(elapsed)}` : '已完成')
              : backendUrl
                ? `生成中 · 已用时 ${mmss(elapsed)} · 真实约 5-8 分钟`
                : `进度 ${drawer.step}/${totalSteps}`}
          </div>
        </div>
        <button onClick={() => dispatch({ type: 'close_report' })}
          style={{ background: 'transparent', border: 'none', color: 'var(--ink-3)', cursor: 'pointer', fontSize: 18, lineHeight: 1, padding: 0 }}>×</button>
      </div>

      {/* 进度: 真后端 → 真·已用时计时器 (后端黑盒一次算完, 无法逐阶段汇报); mock → 步骤动画 */}
      {drawer.status !== 'done' && backendUrl && (
        <div style={{ padding: '18px 22px', borderBottom: '1px solid var(--line)', flexShrink: 0 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 12 }}>
            <div style={{ position: 'relative', width: 16, height: 16, flexShrink: 0 }}>
              <div style={{ width: 10, height: 10, margin: 3, background: 'var(--yin)' }} />
              <div style={{ position: 'absolute', inset: 0, border: '1px solid var(--yin)', opacity: 0.4, animation: 'pulse 1.6s ease-in-out infinite' }} />
            </div>
            <span className="mono" style={{ fontSize: 22, color: 'var(--ink)', fontWeight: 500 }}>{mmss(elapsed)}</span>
            <span className="serif" style={{ fontSize: 12, color: 'var(--ink-2)' }}>已用时 · 真实约 5-8 分钟</span>
          </div>
          {/* 不确定进度条 (走马灯) */}
          <div style={{ height: 3, background: 'var(--paper-2)', overflow: 'hidden', position: 'relative', marginBottom: 12 }}>
            <div style={{ position: 'absolute', height: '100%', width: '35%', background: 'var(--yin)', animation: 'slideInRight 1.4s ease-in-out infinite alternate' }} />
          </div>
          <div className="serif" style={{ fontSize: 12, color: 'var(--ink-2)', lineHeight: 1.7 }}>
            后台正在<strong style={{ color: 'var(--ink)' }}>现场训练 LightGBM + Flow Matching</strong>、跑多空辩论与风控审查，一次性算完后整篇返回（不逐字流式）。本次将产出：
          </div>
          <div style={{ marginTop: 8, fontSize: 11.5, color: 'var(--ink-3)', fontFamily: 'var(--serif)', lineHeight: 1.8 }}>
            综合评级 · Variance Table · 基本面 · 技术与情绪 · 量化共识(LGB分位) · 多空辩论 · 风控审查 · 操作建议
          </div>
        </div>
      )}
      {drawer.status !== 'done' && !backendUrl && (
        <div style={{ padding: '16px 22px', borderBottom: '1px solid var(--line)', flexShrink: 0 }}>
          {phaseGroups.map((g, gi) => (
            <div key={gi} style={{ marginBottom: gi < phaseGroups.length - 1 ? 14 : 0 }}>
              <div className="mono" style={{ fontSize: 10, color: 'var(--ink-3)', letterSpacing: '0.2em', marginBottom: 6 }}>
                {g.phase.toUpperCase()}
              </div>
              {g.items.map((it) => {
                const done = drawer.step > it.idx;
                const running = drawer.step === it.idx;
                return (
                  <div key={it.idx} style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '4px 0' }}>
                    <div style={{ position: 'relative', width: 14, height: 14 }}>
                      <div style={{
                        width: 10, height: 10, margin: 2,
                        background: done ? 'var(--ink)' : running ? 'var(--yin)' : 'transparent',
                        border: done || running ? 'none' : '1px solid var(--ink-3)',
                      }} />
                      {running && <div style={{ position: 'absolute', inset: -2, border: '1px solid var(--yin)', opacity: 0.4, animation: 'pulse 1.6s ease-in-out infinite' }} />}
                    </div>
                    <span className="serif" style={{ fontSize: 12.5, color: done ? 'var(--ink)' : running ? 'var(--ink)' : 'var(--ink-3)', fontWeight: running ? 500 : 400, flex: 1 }}>{it.label}</span>
                    <span className="mono" style={{ fontSize: 9, color: 'var(--ink-3)' }}>{done ? '✓' : running ? '⠋' : '—'}</span>
                  </div>
                );
              })}
            </div>
          ))}
        </div>
      )}

      {/* 报告正文 */}
      <div style={{ flex: 1, overflowY: 'auto', padding: drawer.text ? '20px 28px 24px' : '20px 28px' }}>
        {drawer.text
          ? <ReportMarkdown text={drawer.text} streaming={drawer.status === 'running'} />
          : drawer.status !== 'done' && (
            <div className="serif" style={{ fontSize: 13, color: 'var(--ink-3)', textAlign: 'center', padding: '40px 0', fontStyle: 'italic' }}>
              {backendUrl ? '研报在后台计算，完成后整篇展示在此（可关掉抽屉，跑完会自动填充）…' : '报告将在工具链完成后流式生成…'}
            </div>
          )
        }
      </div>

      {/* 操作行 */}
      <div style={{ padding: '10px 22px', borderTop: '1px solid var(--line)', display: 'flex', alignItems: 'center', gap: 12, flexShrink: 0, background: 'rgba(241,234,217,0.4)' }}>
        <button
          disabled={drawer.status !== 'done'}
          onClick={() => {
            const blob = new Blob([drawer.text], { type: 'text/markdown;charset=utf-8' });
            triggerDownload(blob, `深度研报-${sym.name}-${sym.code}.md`);
          }}
          style={{ background: 'var(--ink)', color: 'var(--paper)', border: 'none', padding: '6px 14px', fontFamily: 'var(--serif)', fontSize: 12, cursor: drawer.status === 'done' ? 'pointer' : 'not-allowed', opacity: drawer.status === 'done' ? 1 : 0.4 }}>
          ↧ 导出 markdown
        </button>
        <button disabled={drawer.status !== 'done'}
          style={{ background: 'transparent', color: 'var(--ink-1)', border: '1px solid var(--line)', padding: '6px 14px', fontFamily: 'var(--serif)', fontSize: 12, cursor: 'pointer', opacity: drawer.status === 'done' ? 1 : 0.4 }}>
          加入研究档案
        </button>
        <span style={{ flex: 1 }} />
        <span className="mono" style={{ fontSize: 10, color: 'var(--ink-3)' }}>tool: run_report</span>
      </div>
    </div>
  );
}

// 极简 markdown 渲染 (够用)
function ReportMarkdown({ text, streaming }) {
  // 极简, 不引入 markdown 库
  const lines = text.split('\n');
  const out = [];
  let inTable = false, tableRows = [];

  const flushTable = (key) => {
    if (tableRows.length === 0) return;
    const [headerRow, , ...rows] = tableRows;
    out.push(
      <table key={key} style={{ borderCollapse: 'collapse', width: '100%', margin: '12px 0', fontFamily: 'var(--mono)', fontSize: 11 }}>
        <thead>
          <tr style={{ borderBottom: '1px solid var(--ink)' }}>
            {headerRow.map((c, i) => <th key={i} style={{ textAlign: i === 0 ? 'left' : 'right', padding: '6px 8px', fontFamily: 'var(--serif)', fontSize: 12, color: 'var(--ink-2)', fontWeight: 500 }}>{c}</th>)}
          </tr>
        </thead>
        <tbody>
          {rows.map((r, ri) => (
            <tr key={ri} style={{ borderBottom: '1px solid var(--line-soft)' }}>
              {r.map((c, ci) => <td key={ci} style={{ textAlign: ci === 0 ? 'left' : 'right', padding: '6px 8px', color: 'var(--ink-1)' }}>{c}</td>)}
            </tr>
          ))}
        </tbody>
      </table>
    );
    tableRows = [];
  };

  lines.forEach((line, i) => {
    if (line.startsWith('|')) {
      const cells = line.split('|').slice(1, -1).map(c => c.trim());
      tableRows.push(cells);
      inTable = true;
      return;
    } else if (inTable) {
      flushTable('t' + i);
      inTable = false;
    }

    if (line.startsWith('# ')) {
      out.push(<h1 key={i} className="serif" style={{ fontSize: 22, fontWeight: 600, color: 'var(--ink)', margin: '0 0 8px', letterSpacing: '-0.005em' }}>{line.slice(2)}</h1>);
    } else if (line.startsWith('## ')) {
      out.push(<h2 key={i} className="serif" style={{ fontSize: 16, fontWeight: 500, color: 'var(--ink)', margin: '20px 0 8px', display: 'flex', alignItems: 'baseline', gap: 8 }}>
        <span className="mono" style={{ fontSize: 10, color: 'var(--ink-3)' }}>{line.slice(3, 5)}</span>
        <span>{line.slice(5)}</span>
      </h2>);
    } else if (line.startsWith('### ')) {
      out.push(<h3 key={i} className="serif" style={{ fontSize: 13.5, fontWeight: 600, color: 'var(--ink-1)', margin: '12px 0 4px' }}>{renderInline(line.slice(4))}</h3>);
    } else if (line.startsWith('---')) {
      out.push(<hr key={i} style={{ border: 0, borderTop: '1px solid var(--line)', margin: '14px 0' }} />);
    } else if (line.startsWith('> ')) {
      out.push(<blockquote key={i} className="serif" style={{ margin: '8px 0', padding: '6px 12px', borderLeft: '2px solid var(--ink-3)', color: 'var(--ink-2)', fontSize: 11.5, fontStyle: 'italic' }}>{renderInline(line.slice(2))}</blockquote>);
    } else if (line.match(/^\s*[-*]\s+/)) {
      const bm = line.match(/^(\s*)[-*]\s+(.*)$/);
      const ind = Math.floor(((bm[1] || '').replace(/\t/g, '  ').length) / 2);
      out.push(<div key={i} style={{ display: 'flex', gap: 8, fontSize: 13, color: 'var(--ink)', lineHeight: 1.75, margin: '2px 0', paddingLeft: 4 + ind * 16 }}>
        <span style={{ flexShrink: 0, color: 'var(--yin)' }}>·</span>
        <span style={{ flex: 1, minWidth: 0 }}>{renderInline(bm[2])}</span>
      </div>);
    } else if (line.match(/^\d+\. /)) {
      out.push(<div key={i} className="serif" style={{ fontSize: 13, color: 'var(--ink)', lineHeight: 1.75, margin: '3px 0', paddingLeft: 4 }}>
        {renderInline(line)}
      </div>);
    } else if (line.startsWith('**')) {
      out.push(<div key={i} className="serif" style={{ fontSize: 13, color: 'var(--ink-1)', lineHeight: 1.85, margin: '2px 0' }}>{renderInline(line)}</div>);
    } else if (line.trim() === '') {
      out.push(<div key={i} style={{ height: 6 }} />);
    } else {
      out.push(<p key={i} className="serif" style={{ fontSize: 13, color: 'var(--ink)', lineHeight: 1.85, margin: '2px 0', textWrap: 'pretty' }}>{renderInline(line)}</p>);
    }
  });
  if (inTable) flushTable('t-end');

  return (
    <div>
      {out}
      {streaming && <span style={{ display: 'inline-block', width: 6, height: 14, background: 'var(--ink)', verticalAlign: -2, animation: 'blink 1s steps(2) infinite' }} />}
    </div>
  );
}

function renderInline(text) {
  // **bold**, `code`, *italic*, [§N] 引用 简易解析 (顺序: 粗体先于斜体, 避免吃掉 **)
  const parts = (text || '').split(/(\*\*[^*]+\*\*|`[^`]+`|\[§\d+\]|\*[^*\n]+\*)/g);
  return parts.map((p, i) => {
    if (!p) return null;
    let m;
    if (p.startsWith('**') && p.endsWith('**')) return <strong key={i} style={{ fontWeight: 600, color: 'var(--ink)' }}>{p.slice(2, -2)}</strong>;
    if (p.startsWith('`') && p.endsWith('`')) return <code key={i} style={{ fontFamily: 'var(--mono)', fontSize: '0.9em', background: 'var(--paper-2)', padding: '0 4px' }}>{p.slice(1, -1)}</code>;
    if ((m = p.match(/^\[§(\d+)\]$/))) return <Cite key={i} n={m[1]} />;
    if (p.length > 2 && p.startsWith('*') && p.endsWith('*')) return <em key={i} style={{ fontStyle: 'italic', color: 'var(--ink-1)' }}>{p.slice(1, -1)}</em>;
    return <span key={i}>{p}</span>;
  });
}

// 聊天回答的轻量 markdown 排版: 标题 / 有序无序列表 (含缩进) / 引用 / 分隔线 / 段落
function renderChatMarkdown(text) {
  const lines = (text || '').split('\n');
  const out = [];
  let listBuf = [], listType = null;

  const flushList = (key) => {
    if (!listBuf.length) return;
    const ordered = listType === 'ol';
    out.push(
      <div key={key} style={{ margin: '4px 0' }}>
        {listBuf.map((it, i) => (
          <div key={i} style={{ display: 'flex', gap: 8, fontSize: 14, color: 'var(--ink)', lineHeight: 1.7, margin: '3px 0', paddingLeft: 2 + it.indent * 16 }}>
            <span style={{ flexShrink: 0, color: 'var(--yin)', fontFamily: ordered ? 'var(--mono)' : 'inherit', fontSize: ordered ? 12 : 14, minWidth: ordered ? 16 : 8 }}>{ordered ? `${it.marker}.` : '·'}</span>
            <span style={{ flex: 1, minWidth: 0 }}>{renderInline(it.text)}</span>
          </div>
        ))}
      </div>
    );
    listBuf = []; listType = null;
  };

  lines.forEach((raw, i) => {
    const indent = (raw.match(/^\s*/)[0] || '').replace(/\t/g, '    ').length;
    const trimmed = raw.trim();
    const bulletM = trimmed.match(/^[*\-•·]\s+(.*)$/);
    const orderM = trimmed.match(/^(\d+)[.)]\s+(.*)$/);

    if (bulletM) {
      if (listType === 'ol') flushList('l' + i);
      listType = 'ul';
      listBuf.push({ text: bulletM[1], indent: Math.min(3, Math.floor(indent / 3)) });
      return;
    }
    if (orderM) {
      if (listType === 'ul') flushList('l' + i);
      listType = 'ol';
      listBuf.push({ text: orderM[2], marker: orderM[1], indent: Math.min(3, Math.floor(indent / 3)) });
      return;
    }
    flushList('l' + i);

    if (trimmed === '') { out.push(<div key={i} style={{ height: 8 }} />); return; }
    if (trimmed.startsWith('### ')) { out.push(<div key={i} className="serif" style={{ fontSize: 14, fontWeight: 600, color: 'var(--ink)', margin: '12px 0 4px' }}>{renderInline(trimmed.slice(4))}</div>); return; }
    if (trimmed.startsWith('## ')) { out.push(<div key={i} className="serif" style={{ fontSize: 15, fontWeight: 600, color: 'var(--ink)', margin: '14px 0 5px' }}>{renderInline(trimmed.slice(3))}</div>); return; }
    if (trimmed.startsWith('# ')) { out.push(<div key={i} className="serif" style={{ fontSize: 16, fontWeight: 600, color: 'var(--ink)', margin: '14px 0 6px' }}>{renderInline(trimmed.slice(2))}</div>); return; }
    if (trimmed.startsWith('> ')) { out.push(<blockquote key={i} className="serif" style={{ margin: '6px 0', padding: '4px 12px', borderLeft: '2px solid var(--ink-3)', color: 'var(--ink-2)', fontSize: 13, fontStyle: 'italic' }}>{renderInline(trimmed.slice(2))}</blockquote>); return; }
    if (/^([-—*]\s?){3,}$/.test(trimmed)) { out.push(<hr key={i} style={{ border: 0, borderTop: '1px solid var(--line)', margin: '10px 0' }} />); return; }
    out.push(<p key={i} className="serif" style={{ fontSize: 14, color: 'var(--ink)', lineHeight: 1.85, margin: '4px 0' }}>{renderInline(trimmed)}</p>);
  });
  flushList('l-end');
  return out;
}

// ───────────────────────── Markdown 导出 ─────────────────────────

function exportSessionToMd(session) {
  if (!session) return;
  const lines = [];
  lines.push(`# ${session.title}`);
  lines.push('');
  lines.push(`> 导出时间: ${new Date().toLocaleString('zh-CN')}　|　觀瀾 · A 股 AI 助手　|　模型: qwen3.5-plus`);
  if (session.context) lines.push(`> 上下文: ${session.context.name} (${session.context.code})`);
  lines.push('');

  session.messages.forEach((m) => {
    if (m.role === 'user') {
      lines.push(`---`);
      lines.push(`### 🧑 用户`);
      lines.push(m.text);
      lines.push('');
    } else if (m.kind === 'chain') {
      const done = m.chain.filter(c => c.status === 'done').length;
      lines.push(`### 🔧 研究链 · ${m.kindLabel} (${done}/${m.chain.length})`);
      m.chain.forEach((c, i) => {
        const icon = c.status === 'done' ? '✓' : c.status === 'cancelled' ? '×' : c.status === 'running' ? '⠋' : '—';
        lines.push(`${i + 1}. ${icon} **${c.name}** \`${c.args}\`  → ${c.result || '(未完成)'}`);
      });
      lines.push('');
    } else if (m.kind === 'brief' && m.sym) {
      const s = m.sym;
      lines.push(`### 📊 速览 · ${s.name} (${s.code})`);
      lines.push(`- 现价: **${s.price.toFixed(2)}** (${s.deltaPct >= 0 ? '+' : ''}${s.deltaPct.toFixed(2)}%)`);
      lines.push(`- 行业: ${s.industry}　市值: ${s.mc}`);
      lines.push(`- 估值: PE ${s.pe} · PB ${s.pb} · ROE ${s.roe}`);
      lines.push(`- 主力资金: ${s.main_in >= 0 ? '+' : ''}${s.main_in} 亿`);
      lines.push(`- 雪球情绪: ${s.xq_bull >= 50 ? '偏多' : '偏空'} ${s.xq_bull}%`);
      lines.push('');
    } else if (m.kind === 'answer') {
      lines.push(`### 🤖 AI 总结`);
      lines.push(m.text.replace(/\[§(\d+)\]/g, '[^$1]'));
      lines.push('');
    }
  });

  const blob = new Blob([lines.join('\n')], { type: 'text/markdown;charset=utf-8' });
  const filename = `观澜-${session.title.slice(0, 20).replace(/[\\/:*?"<>|]/g, '_')}-${Date.now().toString().slice(-6)}.md`;
  triggerDownload(blob, filename);
}

function triggerDownload(blob, filename) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url; a.download = filename;
  document.body.appendChild(a); a.click();
  setTimeout(() => { document.body.removeChild(a); URL.revokeObjectURL(url); }, 1000);
}

function exportBriefToMd(sym, comments) {
  if (!sym) return;
  const v = (x) => (x == null || x === '') ? '—' : x;
  const n2 = (x) => { const f = Number(x); return Number.isFinite(f) ? Math.round(f * 100) / 100 : v(x); };
  const L = [];
  L.push(`# ${sym.name || ''}（${sym.code || ''}）速览`);
  L.push('');
  L.push(`- 市场 / 行业: ${v(sym.market)} · ${v(sym.industry)} · 总市值 ${v(sym.mc)}`);
  L.push(`- 价格: ${v(sym.price)}　涨跌: ${sym.change != null ? sym.change : '—'} (${sym.deltaPct != null ? sym.deltaPct + '%' : '—'})`);
  L.push('');
  L.push('## 行情 / 估值');
  L.push('| 量比 | 换手 | 振幅 | PE TTM | PB |');
  L.push('|---|---|---|---|---|');
  L.push(`| ${v(sym.vol_ratio)} | ${v(sym.turn)} | ${v(sym.amp)} | ${n2(sym.pe)} | ${n2(sym.pb)} |`);
  if (sym.main_in != null) {
    L.push('');
    L.push('## 主力资金 · 今日');
    L.push(`- 净额 ${sym.main_in} 亿　流入 ${v(sym.inflow)} 亿　流出 ${v(sym.outflow)} 亿`);
  }
  const news = Array.isArray(sym.news) ? sym.news : [];
  if (news.length) {
    L.push('');
    L.push(`## 最近新闻 · ${news.length} 条`);
    news.forEach((n) => L.push(`- ${n.t || ''}${n.s ? `（${n.s}）` : ''}`));
  }
  const cm = Array.isArray(comments) ? comments : (Array.isArray(sym.comments) ? sym.comments : []);
  if (cm.length) {
    L.push('');
    L.push(`## 雪球热议 · ${cm.length} 条`);
    cm.slice(0, 10).forEach((c) => L.push(`- **${c.author || '雪球用户'}**${c.ts ? ` · ${c.ts}` : ''}: ${(c.text || '').trim()}`));
  }
  L.push('');
  L.push(`> 导出自 觀瀾 · ${new Date().toLocaleString('zh-CN')}`);
  const blob = new Blob([L.join('\n')], { type: 'text/markdown;charset=utf-8' });
  triggerDownload(blob, `速览-${(sym.name || sym.code || 'brief')}.md`);
}

window.ObservatoryApp = ObservatoryApp;
