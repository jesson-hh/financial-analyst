// agent-adapter.jsx
// 把"agent 跑一轮"抽象成一个统一的接口, 这样后续接真 backend (SSE / WebSocket /
// HTTP polling) 时, 只换 adapter 即可, UI 完全不用动.
//
//   const agent = new GuanlanAgent({ useRealLLM: true });
//   const handle = agent.run(query, sessionContext, {
//     onPlan({chain, intent, label})       — 研究链确定 (全 pending)
//     onContextUpdate({symbol, name, code}) — 本轮检测到的主体, 用于多轮上下文
//     onToolStart(idx)                     — 第 idx 个工具开始运行
//     onToolDone(idx, result)              — 第 idx 个工具完成
//     onBrief()                            — stock_brief 聚合卡可展示
//     onAnswerProgress(textSoFar)          — LLM 回复流式累积
//     onDone()                             — 整轮结束
//     onCancel()                           — 被外部取消
//     onError(err)
//   });
//   handle.cancel();   // 触发 onCancel
//
// 现在内置 MockAdapter (默认, 全本地), 以及 useRealLLM 开关 — 开启后, 工具仍 mock,
// 但最终的中文总结由 window.claude.complete 真实生成 (Haiku 4.5).
//
// 真接你 Python backend 时, 把 GuanlanAgent.run 的实现换成 fetch +
// EventSource / ReadableStream 即可, 接口保持一致.

const sleep = (ms) => new Promise(r => setTimeout(r, ms));

// ───────────────────────── 股票库 (mock) ─────────────────────────

const STOCK_DB = {
  '300750': { code: '300750', name: '宁德时代', market: '深主板', industry: '电力设备 / 电池', mc: '14,206 亿', price: 325.10, change: 7.04, deltaPct: 2.21, vol_ratio: 1.42, turn: '2.18%', amp: '3.84%', pe: '21.4', pb: '4.6', roe: '24.6%', main_in: 4.82, prev_main_in: 1.9, xq_bull: 68 },
  '600519': { code: '600519', name: '贵州茅台', market: '沪主板', industry: '食品饮料 / 白酒', mc: '21,180 亿', price: 1684.0, change: -7.10, deltaPct: -0.42, vol_ratio: 0.84, turn: '0.21%', amp: '1.10%', pe: '23.6', pb: '7.8', roe: '34.2%', main_in: -1.2, prev_main_in: -0.8, xq_bull: 54 },
  '002594': { code: '002594', name: '比亚迪',   market: '深主板', industry: '汽车 / 整车',     mc: '8,540 亿',  price: 281.40, change: 4.65, deltaPct: 1.68, vol_ratio: 1.18, turn: '1.42%', amp: '2.31%', pe: '18.2', pb: '3.8', roe: '21.1%', main_in: 2.31, prev_main_in: 0.6, xq_bull: 71 },
  '300308': { code: '300308', name: '中际旭创', market: '创业板', industry: '通信 / 光模块',   mc: '1,640 亿',  price: 142.55, change: 5.64, deltaPct: 4.12, vol_ratio: 2.18, turn: '4.62%', amp: '5.10%', pe: '34.2', pb: '6.1', roe: '19.4%', main_in: 8.12, prev_main_in: 3.4, xq_bull: 82 },
  '601012': { code: '601012', name: '隆基绿能', market: '沪主板', industry: '电力设备 / 光伏', mc: '1,352 亿',  price: 17.84,  change: -0.19, deltaPct: -1.06, vol_ratio: 0.92, turn: '1.18%', amp: '1.85%', pe: '—',    pb: '1.4', roe: '-2.1%', main_in: -1.8, prev_main_in: -0.4, xq_bull: 42 },
  '600036': { code: '600036', name: '招商银行', market: '沪主板', industry: '银行',           mc: '9,560 亿',  price: 37.92,  change: 0.12, deltaPct: 0.32, vol_ratio: 0.78, turn: '0.42%', amp: '0.61%', pe: '6.4',  pb: '0.9', roe: '15.8%', main_in: 1.40, prev_main_in: 0.8, xq_bull: 58 },
};

// 别名 -> code 映射, 用于自然语言匹配
const NAME_TO_CODE = {
  '宁德时代': '300750', '宁德': '300750', 'CATL': '300750', '300750': '300750',
  '贵州茅台': '600519', '茅台': '600519', '600519': '600519',
  '比亚迪':   '002594', 'BYD': '002594',  '002594': '002594',
  '中际旭创': '300308', '中际': '300308', '300308': '300308',
  '隆基绿能': '601012', '隆基': '601012', '601012': '601012',
  '招商银行': '600036', '招行': '600036', '600036': '600036',
};

// ───────────────────────── 检测 ─────────────────────────

// 从 query 抽取股票主体. 优先 6 位代码, 然后名称, 最后兜底用 fallback (session 上下文)
function detectSymbol(query, fallback) {
  const codeMatch = query.match(/\b\d{6}\b/);
  if (codeMatch && STOCK_DB[codeMatch[0]]) return STOCK_DB[codeMatch[0]];
  for (const name of Object.keys(NAME_TO_CODE)) {
    if (query.includes(name)) return STOCK_DB[NAME_TO_CODE[name]];
  }
  return fallback || null;
}

function detectIntent(query) {
  if (/资金|主力|龙虎|今天.*买/.test(query)) return 'fundflow';
  if (/为什么.*涨|为什么.*跌|催化|消息|为啥/.test(query)) return 'why_move';
  if (/对比|vs|比较|哪个/.test(query)) return 'compare';
  if (/技术|K线|均线|MA|压力|支撑/.test(query)) return 'technical';
  if (/盯|提醒|跌破|涨破/.test(query)) return 'alert';
  return 'brief';
}

const INTENT_LABEL = {
  brief: 'stock_brief',
  fundflow: '资金流扫描',
  why_move: '驱动归因',
  compare: '同业对比',
  technical: '技术面',
  alert: '盯盘规则',
};

// ───────────────────────── 工具链规划 ─────────────────────────

function planChain(intent, sym) {
  const arg = sym ? `{ symbol: "${sym.code}" }` : `{ ... }`;

  if (intent === 'fundflow') {
    return [
      { name: 'ths_fund_flow',  cn: '同花顺资金流榜', args: '{ target: "stock", limit: 20 }', t: 2.6, result: 'top1 中际旭创 +8.1 亿 · top2 寒武纪 +6.3 亿 · top3 宁德时代 +4.8 亿' },
      { name: 'iwencai_search', cn: '问财·量价齐升',  args: '{ q: "今日主力净流入>5亿 量比>2" }', t: 3.1, result: '23 只命中, 集中在 AI 算力 / CPO' },
      { name: 'mainline_radar', cn: '主线雷达',       args: '{ window: "1w" }', t: 1.8, result: '本周主线: AI 算力 / 储能 / 创新药' },
    ];
  }
  if (intent === 'why_move') {
    return [
      { name: 'realtime_quote', cn: '实时行情', args: arg, t: 0.4, result: sym ? `${sym.price.toFixed(2)} ${sym.deltaPct >= 0 ? '+' : ''}${sym.deltaPct}% · 量比 ${sym.vol_ratio}` : '查无主体' },
      { name: 'news_query',     cn: '本地新闻',   args: `{ keyword: "${sym?.name || ''}", days: 3 }`, t: 0.2, result: '7 条 · 含公告 1 · 研报 2 · 快讯 4' },
      { name: 'news_collect',   cn: '抓取最新',   args: '{ sources: ["eastmoney", "xueqiu"] }', t: 2.4, result: '新增 3 条快讯' },
      { name: 'ths_fund_flow',  cn: '主力资金',   args: arg, t: 2.5, result: sym ? `主力净 ${sym.main_in >= 0 ? '+' : ''}${sym.main_in} 亿` : '—' },
    ];
  }
  if (intent === 'compare') {
    return [
      { name: 'chain_for',      cn: '同行抓取',     args: arg, t: 0.1, result: '同行 5 家: 亿纬 / 国轩 / 中创新航 / 欣旺达 / 蔚蓝锂芯' },
      { name: 'quote_lookup',   cn: '批量估值',     args: '{ symbols: [...] }', t: 0.4, result: 'PE 中位 19.2 · ROE 中位 14.5%' },
      { name: 'iwencai_search', cn: '盈利能力对比', args: '{ q: "电池板块 毛利率 ROE 排名" }', t: 2.9, result: '宁德 28.4% > 亿纬 17.2% > 国轩 14.6% > 中创新航 11.8%' },
    ];
  }
  if (intent === 'technical') {
    return [
      { name: 'realtime_quote', cn: '实时盘口', args: arg, t: 0.4, result: sym ? `${sym.price.toFixed(2)} · 量比 ${sym.vol_ratio}` : '—' },
      { name: 'quote_lookup',   cn: '日线 + 均线', args: arg, t: 0.3, result: 'MA5 322.4 · MA10 318.6 · MA20 308.2 · BOLL 中轨 312.8' },
    ];
  }
  if (intent === 'alert') {
    return [
      { name: 'alert_add', cn: '添加盯盘', args: `{ symbol: "${sym?.code || '?'}", type: "price_below", value: 1200 }`, t: 0.1, result: '已添加规则 · alert_id=a4 · 后台 5 分钟轮询' },
      { name: 'alert_list', cn: '列出全部', args: '{}', t: 0.1, result: '当前 4 条活跃规则' },
    ];
  }
  // brief (default)
  return [
    { name: 'realtime_quote',  cn: '实时行情',         args: arg, t: 0.4, result: sym ? `${sym.price.toFixed(2)} ${sym.deltaPct >= 0 ? '+' : ''}${sym.deltaPct}% · 量比 ${sym.vol_ratio} · 换手 ${sym.turn}` : '查无主体' },
    { name: 'ths_fund_flow',   cn: '同花顺资金流',     args: `{ target: "stock", symbol: "${sym?.code || '?'}" }`, t: 2.8, result: sym ? `主力净流入 ${sym.main_in} 亿 · 大单 +${(sym.main_in * 0.65).toFixed(1)} 亿 · 中单 +${(sym.main_in * 0.35).toFixed(1)} 亿` : '—' },
    { name: 'news_query',      cn: '本地新闻全文检索', args: `{ keyword: "${sym?.name || ''}", days: 7 }`, t: 0.2, result: '14 条 · 含巨潮公告 2 · 雪球热门 5 · 东方财富快讯 7' },
    { name: 'chain_for',       cn: '产业链上下游',     args: arg, t: 0.1, result: '上游 6 / 同行 5 / 下游 8' },
    { name: 'stocks_show',     cn: '历史研究时间线',   args: `{ symbol: "${sym?.code || '?'}", limit: 3 }`, t: 0.3, result: '上次评级 ★★★★ (10-21) · 目标价 360 · 跑赢沪深300 +18%' },
  ];
}

// ───────────────────────── Mock 中文回答 ─────────────────────────

function mockAnswer(intent, sym, chain) {
  const name = sym?.name || '该主体';
  if (intent === 'fundflow') {
    return `今日主力净流入榜首是中际旭创 +8.1 亿[§1], 寒武纪 +6.3 亿, ${name} +${sym?.main_in || 4.8} 亿, 集中在 AI 算力 / CPO 与储能链. 问财扫描"量比>2 且主力净>5 亿"命中 23 只[§2], AI 算力占 12 只, 印证本周主线[§3]. 建议关注盘后龙虎榜, 看是否有机构席位接力.`;
  }
  if (intent === 'why_move') {
    return `${name}今日${sym?.deltaPct >= 0 ? '上涨' : '下跌'} ${Math.abs(sym?.deltaPct || 0)}%[§1], 触发要素: 近 3 日有 7 条相关新闻[§2], 新抓快讯 3 条[§3], 主力净${sym?.main_in >= 0 ? '流入' : '流出'} ${Math.abs(sym?.main_in || 0)} 亿[§4]. 短线驱动以${sym?.deltaPct >= 0 ? '资金面+消息面共振' : '获利回吐 + 板块轮动'}为主, 需关注明日量能是否延续.`;
  }
  if (intent === 'compare') {
    return `${name}所在板块同行 5 家[§1], 估值 PE 中位 19.2[§2]. 盈利能力排序: 宁德 28.4% > 亿纬 17.2% > 国轩 14.6% > 中创新航 11.8%[§3]. ${name === '宁德时代' ? '公司毛利率高出第二名 11 pct, 龙头溢价合理.' : '建议关注${name} 与龙头的差距.'}`;
  }
  if (intent === 'technical') {
    return `${name}现价 ${sym?.price.toFixed(2)}[§1], 短期均线全部多头排列 (MA5 > MA10 > MA20)[§2]. 量比 ${sym?.vol_ratio}, 走势偏强. 关键阻力位 ${(sym?.price * 1.045).toFixed(0)}, 支撑位 ${(sym?.price * 0.93).toFixed(0)}, 偏多操作建议突破阻力位后跟进.`;
  }
  if (intent === 'alert') {
    return `已为 ${name} 添加盯盘规则[§1], 后台每 5 分钟轮询交易时段价格. 当前活跃规则 ${4} 条[§2], 触发时会弹窗 + 桌面通知.`;
  }
  // brief
  if (!sym) {
    return `没有识别到具体股票主体. 试试: "看下宁德时代怎么样" / "300750 怎么样" / "茅台对比五粮液".`;
  }
  return `${name} Q3 利润同比 +25.9% 超预期, 毛利率回升至 28.4%[§1]. 今日主力净流入 +${sym.main_in} 亿, 连续 3 日加仓[§2]. 消息面: 19 GWh 海外储能订单 + 中信 360 目标价[§3]. 同行毛利率仍处第一梯队[§4]. 上次研报评级 ★★★★, 跑赢沪深300 +18%. 短线情绪偏多, 建议分批跟进, 关注 ${(sym.price * 1.015).toFixed(0)} 一线压力.`;
}

// ───────────────────────── Claude prompt builder ─────────────────────────
// 把所有上下文 + 工具结果喂给 Claude, 让它生成自然的中文总结

function buildClaudePrompt(query, sessionContext, intent, sym, chain) {
  const ctxText = sessionContext && sessionContext.code !== sym?.code
    ? `\n会话之前在讨论: ${sessionContext.name} (${sessionContext.code})\n` : '';
  const toolResults = chain.map((c, i) => `[§${i+1}] ${c.name}(${c.args}) → ${c.result}`).join('\n');

  return `你是觀瀾, 一个 A 股研究 agent. 用户问: "${query}"
${ctxText}
本轮主体: ${sym ? `${sym.name} (${sym.code}) · ${sym.industry} · 现价 ${sym.price} (${sym.deltaPct >= 0 ? '+' : ''}${sym.deltaPct}%)` : '未识别'}
意图分类: ${intent}

刚刚执行了 ${chain.length} 个工具, 结果:
${toolResults}

请用中文写一段 2-4 句的总结回复用户. 要求:
- 简洁, 像专业研究员说话, 不要套话
- 关键数据点后用 [§N] 标注引用 (N 对应工具序号), 不要在末尾集中列引用
- 不写 "总而言之" / "综上所述" 之类的废话
- 涨用红 (公司是中国市场), 但你只输出文字, 颜色由前端处理
- 不超过 200 字`;
}

// ───────────────────────── SSE parser ─────────────────────────
// 一个 SSE 块形如:
//   event: tool_start\n
//   data: {"idx":0,"name":"realtime_quote","args":{...}}\n
//   \n
// 块之间用空行分隔. 同一块里 data 可能多行 (协议允许), 拼接.
function parseSSEBlock(block) {
  const lines = block.split(/\r?\n/);
  let event = 'message';
  let dataLines = [];
  for (const ln of lines) {
    if (!ln || ln.startsWith(':')) continue;       // comment / heartbeat
    if (ln.startsWith('event:')) event = ln.slice(6).trim();
    else if (ln.startsWith('data:')) dataLines.push(ln.slice(5).replace(/^ /, ''));
  }
  if (dataLines.length === 0) return null;
  const raw = dataLines.join('\n');
  let data = {};
  try { data = raw ? JSON.parse(raw) : {}; }
  catch { data = { _raw: raw }; }
  return { event, data };
}

// ───────────────────────── GuanlanAgent ─────────────────────────

class GuanlanAgent {
  constructor({ useRealLLM = false, backendUrl = null } = {}) {
    this.useRealLLM = useRealLLM;
    this.backendUrl = backendUrl;            // 留空 = 走 mock
    this.cancelled = false;
    this.turnId = null;                       // 后端给的回合 id, 用于 /confirm
    this._abort = null;
  }

  // opts: { sessionId, mode }
  async run(query, sessionContext, cb, opts = {}) {
    if (this.backendUrl) {
      try {
        return await this._runBackend(query, sessionContext, cb, opts);
      } catch (e) {
        console.warn('[guanlan] 后端 SSE 失败, 回退 mock:', e);
        if (this.cancelled) return cb.onCancel();
        // 回退到 mock 让 UI 继续可用
      }
    }
    return this._runMock(query, sessionContext, cb);
  }

  // ─── 真后端 ───
  async _runBackend(query, sessionContext, cb, { sessionId, mode = 'auto', model = null } = {}) {
    this._abort = new AbortController();
    const resp = await fetch(`${this.backendUrl}/run`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        query,
        mode,
        context: sessionContext || null,
        session_id: sessionId || null,
        model: model || null,        // ⑤ 切后端模型 (修: 原先漏传)
      }),
      signal: this._abort.signal,
    });
    if (!resp.ok || !resp.body) throw new Error(`HTTP ${resp.status}`);

    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buf = '';
    let lastToolName = null;
    let lastToolMeta = null;
    let answerText = '';

    while (true) {
      if (this.cancelled) { try { reader.cancel(); } catch(_){} cb.onCancel(); return; }
      const { done, value } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });

      // 按空行分块
      let idx;
      while ((idx = buf.indexOf('\n\n')) >= 0 || (idx = buf.indexOf('\r\n\r\n')) >= 0) {
        const sep = buf.startsWith('\r\n', idx) ? 4 : 2;
        const block = buf.slice(0, idx);
        buf = buf.slice(idx + sep);
        const ev = parseSSEBlock(block);
        if (!ev) continue;
        const d = ev.data || {};

        switch (ev.event) {
          case 'plan': {
            this.turnId = d.turn_id || this.turnId;
            cb.onPlan({
              chain: Array.isArray(d.chain) ? d.chain.map(c => ({ ...c, status: 'pending' })) : [],
              intent: d.intent || 'brief',
              label: d.label || (INTENT_LABEL[d.intent] || 'stock_brief'),
            });
            if (d.context) cb.onContextUpdate(d.context);
            break;
          }
          case 'context': cb.onContextUpdate(d); break;
          case 'tool_start': {
            lastToolName = d.name;
            const argsStr = typeof d.args === 'string' ? d.args
              : (d.args ? JSON.stringify(d.args) : '{}');
            lastToolMeta = { name: d.name, cn: d.cn || d.name, args: argsStr, t: d.t || 0 };
            cb.onToolStart(d.idx, lastToolMeta);
            break;
          }
          case 'tool_done': {
            cb.onToolDone(d.idx, d.result, lastToolName);
            break;
          }
          case 'brief': cb.onBrief(d.sym || d); break;
          case 'report': cb.onReport && cb.onReport(d); break;
          case 'answer_progress': {
            // 后端可能给增量或全量, 兼容: 优先用 text, 退而用 delta 累积
            if (typeof d.text === 'string') answerText = d.text;
            else if (typeof d.delta === 'string') answerText += d.delta;
            cb.onAnswerProgress(answerText);
            break;
          }
          case 'confirm_request': {
            cb.onConfirmRequest && cb.onConfirmRequest({
              turn_id: d.turn_id || this.turnId,
              tool: d.tool,
              args: d.args,
              label: d.label,
              detail: d.detail,
            });
            break;
          }
          case 'error':
            cb.onError(new Error(d.message || d._raw || 'agent error'));
            return;
          case 'done':
            cb.onDone(d);
            return;
        }
      }
    }
    cb.onDone();
  }

  // POST /confirm  — 用户选了 y/n/a 后回传后端
  async resolveConfirm(turnId, choice) {
    if (!this.backendUrl) return;
    const tid = turnId || this.turnId;
    if (!tid) return;
    try {
      await fetch(`${this.backendUrl}/confirm`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ turn_id: tid, choice }),
      });
    } catch (e) {
      console.warn('[guanlan] /confirm 回传失败:', e);
    }
  }

  // ─── mock (开发预览) ───
  async _runMock(query, sessionContext, cb) {
    try {
      const sym = detectSymbol(query, sessionContext);
      const intent = detectIntent(query);
      const chain = planChain(intent, sym);
      const label = INTENT_LABEL[intent];

      cb.onPlan({ chain, intent, label });
      if (sym) cb.onContextUpdate({ symbol: sym.code, name: sym.name, code: sym.code, sym });

      for (let i = 0; i < chain.length; i++) {
        if (this.cancelled) return cb.onCancel();
        cb.onToolStart(i, { name: chain[i].name, cn: chain[i].cn, args: chain[i].args, t: chain[i].t });
        const ms = Math.max(380, Math.min(1500, chain[i].t * 380));
        await sleep(ms);
        if (this.cancelled) return cb.onCancel();
        cb.onToolDone(i, chain[i].result, chain[i].name);
      }

      if (intent === 'brief' && sym) cb.onBrief(sym);

      let answerText;
      if (this.useRealLLM && typeof window !== 'undefined' && window.claude) {
        try {
          const prompt = buildClaudePrompt(query, sessionContext, intent, sym, chain);
          answerText = await window.claude.complete(prompt);
        } catch (e) {
          console.warn('Claude 调用失败, 回退到 mock:', e);
          answerText = mockAnswer(intent, sym, chain);
        }
      } else {
        answerText = mockAnswer(intent, sym, chain);
      }

      for (let i = 0; i <= answerText.length; i += 2) {
        if (this.cancelled) return cb.onCancel();
        await sleep(28);
        cb.onAnswerProgress(answerText.slice(0, i));
      }
      cb.onAnswerProgress(answerText);
      cb.onDone();
    } catch (err) {
      cb.onError(err);
    }
  }

  cancel() {
    this.cancelled = true;
    try { this._abort && this._abort.abort(); } catch (_) {}
  }
}

// 全局导出
window.GuanlanAgent = GuanlanAgent;
window.STOCK_DB = STOCK_DB;
window.detectSymbol = detectSymbol;
window.detectIntent = detectIntent;
window.INTENT_LABEL = INTENT_LABEL;
