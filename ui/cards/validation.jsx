// 观澜 · 经验总结 · 验证区
// 文本信息 → 提炼专属经验 → 数据验证 → 接入知识库 / 生成因子组合
// 四阶段：原 → 炼 → 验 → 用
const { useState, useRef, useEffect, useCallback } = React;

// 帷幄融合旗:EMBED=被帷幄嵌入(隐藏页头);LEGACY=找回页内 agent 窗口(refine 默认全局隐藏,spec §3.7)
const WW_EMBED = new URLSearchParams(location.search).get('embed') === '1';
const WW_LEGACY = new URLSearchParams(location.search).get('legacy') === '1';
// 帷幄会话工作台隔离:带 ?ws=<会话id>(嵌入或从工作台 ↗ 独立打开)→ handoff 信箱与卡记忆按会话各自存取;无 ws = 裸键如旧
const WW_WS = new URLSearchParams(location.search).get('ws') || '';

// 后端: 同源薄壳即真引擎 (window.GUANLAN_BACKEND 由 HTML 注入; 空/file:// → 相对路径)
const API = (typeof window !== 'undefined' && window.GUANLAN_BACKEND) || '';

// 单因子回测裁决: 按 rank_ic_mean(ic) 与 icir 阈值给 verdict。
//   compute_error / ic 算不出(null/NaN) → 驳回
//   |ic| ≥ 0.03 且 |icir| ≥ 0.3 → 通过
//   |ic| < 0.015 → 驳回
//   其余 → 存疑
function verdictFromIC(ic, icir) {
  const a = Math.abs(Number(ic));
  const b = Math.abs(Number(icir));
  if (!Number.isFinite(a)) return '驳回';
  if (a >= 0.03 && Number.isFinite(b) && b >= 0.3) return '通过';
  if (a < 0.015) return '驳回';
  return '存疑';
}

// 引擎 /cards 卡 → 右栏「经验知识库」条目形状
function cardToKb(c) {
  return {
    name: c.title, conf: c.conf, ic: c.ic, tags: (c.tags || []).slice(0, 3),
    verdict: c.verdict, src: c.src || '自定义', cat: c.cat || '其他',
  };
}

// 4 段式 body(## 经验 / ## 适用条件 / ## 操作建议 / ## 反例 / 边界)→ {标题: 正文}
function parseSections(md) {
  const out = {};
  (md || '').split(/\n(?=##\s)/).forEach(p => {
    const m = p.match(/^##\s+(.+?)\n([\s\S]*)$/);
    if (m) out[m[1].trim()] = m[2].trim();
  });
  return out;
}

// 量化"验"端未接 → 不编造回测指标(原按置信缩放的占位 IC/ICIR/年化/胜率已删),诚实留空「—」。
// demo:true 标注;recipeForStrategy 的 demo 守卫把它挡在 decide 信号路径外。要真指标须走 /factor/report2 真验证。
function synthVal(conf, verdict) {
  const c = conf || 60;
  return {
    ic: '—', icir: '—', ann: '—', win: '—', mono: '—', n: '—',
    conf: c, verdict: verdict || '未验证', demo: true,
  };
}

// 引擎 draft 卡(未验证·视频经验)→ 左栏 SourcePane 期望的「素材」形状
function cardToSource(c) {
  const sec = parseSections(c.insight);
  const order = ['经验', '适用条件', '操作建议', '反例 / 边界'];
  const seg = order.filter(t => sec[t]).map((t, i) => ({ t: `【${t}】 ${sec[t]}`, cite: i + 1 }));
  return {
    id: c.id, kind: '视频', title: c.title, from: c.src || 'B站', time: c.created || '',
    tag: (c.tags || []).slice(0, 2).join(' / '),
    seg: seg.length ? seg : [{ t: c.insight || c.title }],
    distill: {
      name: c.title, tags: (c.tags || []).slice(0, 4), cat: c.cat || '其他',
      insight: sec['经验'] || (c.insight || '').slice(0, 90),
      conds: [], scenes: (c.tags || []).slice(0, 3), expr: c.expr || '',
      val: synthVal(c.conf, c.verdict),
    },
    combos: [],
  };
}

// ───────── 来源类型配色 ─────────
const KIND = {
  研报: { c: 'var(--jin)' },
  热帖: { c: 'var(--zhu)' },
  复盘: { c: 'var(--dai)' },
  快讯: { c: 'var(--ink-2)' },
  视频: { c: 'var(--zhu)' },
};
const VERDICT = {
  通过: { c: 'var(--dai)', bg: 'rgba(74,107,92,0.10)', sym: '✓', note: '显著、稳健，建议纳入' },
  存疑: { c: 'var(--jin)', bg: 'rgba(138,111,63,0.12)', sym: '？', note: '方向正确但稳健性不足，建议留观' },
  驳回: { c: 'var(--yin)', bg: 'rgba(168,57,45,0.10)', sym: '✕', note: '统计不显著，暂不纳入' },
};

// ───────── 文本素材库 (mock) ─────────
// seg: 原文分句; cite 标记被 agent 抽为证据的句子
const SOURCES = [
  {
    id: 's1', kind: '研报', title: '缩量企稳后的反转效应', from: '中信证券 · 量化策略', time: '11-18', tag: '反转 / 缩量',
    seg: [
      { t: '近期市场情绪低迷，超跌个股普遍出现成交量快速萎缩。' },
      { t: '统计 2016 年以来，个股 5 日跌幅超过 8% 后，若次日量比降至 0.7 以下并收出企稳阳线，未来 3 个交易日反转概率达 63%。', cite: 1 },
      { t: '该效应在震荡市中尤为显著，单边下跌行情中基本失效。' },
      { t: '周频调仓下多空组合年化超额约 19%，但 IC 半衰期较短，约 4 个交易日。', cite: 2 },
    ],
    distill: {
      name: '缩量企稳反转', tags: ['反转', '缩量', '周频'], cat: '价量',
      insight: '超跌后缩量企稳，3 日内反转概率显著抬升；震荡市、周频最有效，但信号衰减快。',
      conds: [['5 日跌幅', '<', '−8%'], ['次日量比', '<', '0.7'], ['K 线形态', '=', '企稳阳线']],
      scenes: ['震荡市', '周频', '短线'],
      expr: '-rank(ts_sum(ret,5)) · (vol_ratio < 0.7)',
      val: { ic: '0.043', icir: '1.82', ann: '+19.2%', win: '58%', mono: '强', n: '4,820', conf: 76, verdict: '通过' },
    },
    combos: [
      { name: '缩量反转 × 业绩漂移', kind: '双引擎', members: ['缩量企稳反转', '业绩超预期漂移'], expr: '0.6·rank(rev) + 0.4·rank(eps_surprise)', ic: '0.061', corr: '0.12', note: '短期反转叠加中期业绩漂移，相关性低、信息互补，合成 IC 较单因子提升 ~40%。' },
      { name: '反转 · 北向门控', kind: '门控', members: ['缩量企稳反转', '北向资金领先'], expr: 'rank(rev) · sign(north_3d)', ic: '0.049', corr: '0.08', note: '用北向资金方向做门控，过滤单边下跌中的假反转，回撤显著收敛。' },
    ],
  },
  {
    id: 's2', kind: '热帖', title: '北向资金是不是聪明钱？', from: '雪球 · 量化老张', time: '11-15', tag: '资金流 / 择时',
    seg: [
      { t: '翻了下最近半年北向的持仓变化，挺有意思。' },
      { t: '连续 3 日净买入的板块，往后 5 个交易日相对沪深300 平均跑赢 1.8%。', cite: 1 },
      { t: '不过单看个股噪音很大，得做成板块层面的因子才稳。' },
      { t: '体感是这个信号在白马蓝筹上比小盘股管用得多。', cite: 2 },
    ],
    distill: {
      name: '北向资金领先', tags: ['北向', '资金流', '中频'], cat: '资金',
      insight: '北向连续净买入的板块 5 日后相对收益占优；蓝筹更显著，个股层面噪音大需聚合到板块。',
      conds: [['北向净买入', '≥', '连续 3 日'], ['聚合层级', '=', '板块'], ['标的偏好', '=', '大盘蓝筹']],
      scenes: ['资金流', '择时', '中频'],
      expr: 'rank(ts_sum(north_hold_chg, 3))',
      val: { ic: '0.031', icir: '1.21', ann: '+11.4%', win: '55%', mono: '中', n: '3,150', conf: 61, verdict: '存疑' },
    },
    combos: [
      { name: '北向 × 缩量反转', kind: '互补', members: ['北向资金领先', '缩量企稳反转'], expr: 'rank(north_3d) + rank(rev)', ic: '0.047', corr: '0.09', note: '资金面与价量面互补，板块层面择时 + 个股层面选股，分层使用更稳。' },
    ],
  },
  {
    id: 's3', kind: '复盘', title: '11-12 盘后复盘 · 龙头退潮', from: '我的复盘', time: '11-12', tag: '情绪 / 风控',
    seg: [
      { t: '今天指数还行，但几个高位票都不太对劲。' },
      { t: '中际旭创创 60 日新高后放出 2 倍量，但全天涨幅只有 1.2%，明显滞涨。', cite: 1 },
      { t: '这种高位放量不涨，过去经验看八成是要退潮的。' },
      { t: '下次遇到这种形态，应该提前降权止盈，别等破位。', cite: 2 },
    ],
    distill: {
      name: '高位放量滞涨退潮', tags: ['情绪', '风控', '顶部'], cat: '情绪',
      insight: '创新高后放量但涨幅收敛，常为退潮前兆；应提前降权止盈，而非追高。',
      conds: [['价格位置', '=', '创 60 日新高'], ['量比', '>', '2.0'], ['当日涨幅', '<', '2%']],
      scenes: ['情绪', '风控', '止盈'],
      expr: '-(near_60d_high & vol_ratio>2 & ret_1d<0.02)',
      val: { ic: '0.028', icir: '0.94', ann: '空头 +9.1%', win: '60%', mono: '中', n: '1,240', conf: 57, verdict: '存疑' },
    },
    combos: [
      { name: '退潮风控 · 持仓降权层', kind: '风控层', members: ['高位放量滞涨退潮'], expr: 'pos_weight · (1 − topout_signal)', ic: '—', corr: '—', note: '作为风控覆盖层而非选股因子：命中形态时按 0.5 降权，回测最大回撤由 −14% 收窄至 −9%。' },
    ],
  },
  {
    id: 's4', kind: '快讯', title: '多家公司 Q3 业绩超预期', from: '东方财富 · 快讯', time: '10-28', tag: '基本面 / 事件',
    seg: [
      { t: '三季报披露进入高峰期。' },
      { t: '多家公司实际净利润超出一致预期 10% 以上，公告次日平均跳空高开 2.3%。', cite: 1 },
      { t: '历史上业绩超预期后存在持续的价格漂移。' },
      { t: '学术上称为 PEAD，A 股漂移窗口约 40–60 个交易日。', cite: 2 },
    ],
    distill: {
      name: '业绩超预期漂移 PEAD', tags: ['基本面', '事件', '中频'], cat: '基本面',
      insight: '业绩超一致预期后存在约 40–60 日价格漂移；事件驱动叠加基本面因子更稳。',
      conds: [['eps_surprise', '>', '10%'], ['介入时点', '=', '公告 T+1'], ['持有周期', '=', '40–60 日']],
      scenes: ['基本面', '事件', '中频'],
      expr: 'rank(eps_surprise) · hold(60d)',
      val: { ic: '0.052', icir: '2.10', ann: '+24.6%', win: '62%', mono: '强', n: '2,680', conf: 84, verdict: '通过' },
    },
    combos: [
      { name: 'PEAD × 缩量反转', kind: '双引擎', members: ['业绩超预期漂移', '缩量企稳反转'], expr: '0.5·rank(eps_surprise) + 0.5·rank(rev)', ic: '0.061', corr: '0.12', note: '中期基本面漂移 + 短期价量反转，节奏错开、相关性低，是经典的双引擎组合。' },
      { name: 'PEAD · 北向增强', kind: '增强', members: ['业绩超预期漂移', '北向资金领先'], expr: 'rank(eps_surprise) · (1 + 0.3·north_3d)', ic: '0.058', corr: '0.10', note: '业绩超预期且获北向增持的标的，漂移更充分，胜率提升至 66%。' },
    ],
  },
];

// 知识库内容现从 guanlan 自有 /cards 后端实时拉取(见 cardToKb / refreshKb),
// 不再用硬编码 mock —— 初期库为空属正常(管线沉淀后自然填充)。

// ───────── 关键词驱动的简易提炼（粘贴自定义文本时） ─────────
function distillFromText(txt) {
  const t = txt || '';
  const has = (re) => re.test(t);
  let base = {
    name: '自定义经验', tags: ['待标注'], cat: '其他',
    insight: t.trim().slice(0, 60) + (t.length > 60 ? '…' : ''),
    conds: [['触发条件', '=', '待补充']], scenes: ['待标注'],
    expr: 'rank(custom_signal)',
    val: { ic: '0.029', icir: '1.08', ann: '+8.7%', win: '54%', mono: '弱', n: '1,900', conf: 52, verdict: '存疑' },
  };
  if (has(/反转|超跌|缩量/)) base = { ...SOURCES[0].distill };
  else if (has(/北向|资金|聪明钱/)) base = { ...SOURCES[1].distill };
  else if (has(/高位|放量|滞涨|退潮|止盈/)) base = { ...SOURCES[2].distill };
  else if (has(/业绩|超预期|pead|漂移|eps/i)) base = { ...SOURCES[3].distill };
  return base;
}

// ───────── 对话修改经验：prompt / 解析 / 本地兜底 ─────────
function buildRevisePrompt(d, chat, instr) {
  const condStr = d.conds.map(c => c.join(' ')).join('；');
  const hist = chat.filter(m => m.role === 'user').slice(-3).map(m => '- ' + m.text).join('\n');
  return `你是觀瀾，A股量化研究 agent。下面是一条从研究文本提炼出的"专属经验"，用户希望对它进行修改。

当前经验:
名称: ${d.name}
描述: ${d.insight}
触发条件: ${condStr}
适用场景: ${d.scenes.join('、')}
因子表达式: ${d.expr}
${hist ? '\n此前的修改记录:\n' + hist + '\n' : ''}
用户本次指令: "${instr}"

只修改用户要求的部分，其余原样保留；保持简洁、专业的中文研究员口吻。
严格只返回 JSON（不要 markdown 代码块、不要任何解释文字），格式如下:
{"reply":"一句话说明你做了什么修改","name":"","insight":"","conds":[["条件名","运算符","阈值"]],"scenes":[""],"expr":"因子表达式"}`;
}

function parseRevise(out) {
  let s = String(out || '').trim().replace(/^```(json)?/i, '').replace(/```$/, '').trim();
  const i = s.indexOf('{'), j = s.lastIndexOf('}');
  if (i >= 0 && j > i) s = s.slice(i, j + 1);
  const p = JSON.parse(s);
  return { reply: p.reply || '已更新经验。', patch: { name: p.name, insight: p.insight, conds: p.conds, scenes: p.scenes, expr: p.expr } };
}

// 本地兜底改写（后端 /cards/refine 不可用时的离线规则)
function mockRevise(instr, d) {
  const t = instr || '';
  const nd = { conds: d.conds.map(c => [...c]), scenes: [...d.scenes] };
  let reply = '已按你的要求调整。';
  const widen = /放宽|放松|宽松|降低|放大/.test(t), tighten = /收紧|严格|提高|缩小/.test(t);
  if (widen || tighten) {
    nd.conds = nd.conds.map(([k, op, v]) => {
      const m = String(v).match(/-?\d+(\.\d+)?/); if (!m) return [k, op, v];
      const num = parseFloat(m[0]);
      const f = widen ? (op.indexOf('<') >= 0 ? 1.2 : 0.82) : (op.indexOf('<') >= 0 ? 0.82 : 1.2);
      const dec = m[0].indexOf('.') >= 0 ? 1 : 0;
      const nv = (num * f).toFixed(dec);
      return [k, op, String(v).replace(m[0], nv)];
    });
    reply = widen ? '已放宽触发阈值——覆盖样本会增多，但信号纯度下降，记得重新验证。' : '已收紧阈值——信号更纯净，但样本数会减少。';
  } else if (/日频|日度|日线/.test(t)) {
    nd.scenes = nd.scenes.map(s => s === '周频' ? '日频' : s); if (nd.scenes.indexOf('日频') < 0) nd.scenes.push('日频');
    reply = '已改为日频；注意日频下换手率与交易成本会明显上升。';
  } else if (/月频/.test(t)) {
    nd.scenes = nd.scenes.map(s => /频/.test(s) ? '月频' : s); reply = '已改为月频。';
  } else if (/场景|适用|加.*标签/.test(t)) {
    const cand = ['成长股', '大盘蓝筹', '中小盘', '高波动', '低换手'].find(x => nd.scenes.indexOf(x) < 0) || '其他';
    nd.scenes = [...nd.scenes, cand]; reply = `已补充适用场景「${cand}」。`;
  } else if (/简化|精简/.test(t) && /表达式|因子|公式/.test(t)) {
    nd.expr = d.expr.split(/[·*+]/)[0].trim(); reply = '已简化因子表达式，仅保留主项。';
  } else {
    reply = '收到。本地预览仅支持阈值、频率、场景等快捷修改；接入大模型后可执行任意自然语言改写。';
  }
  return { patch: nd, reply };
}

// ───────── 小图表 ─────────
function ICBars({ data }) {
  // 内部归一化(真 rank-IC 序列幅度不定;最大 |v| 撑到 28px 半高),条距按点数自适应铺满
  const maxAbs = Math.max(...data.map(Math.abs), 1e-9);
  const step = data.length ? Math.min(290 / data.length, 16) : 12;
  const bw = Math.max(3, step * 0.66);
  return (
    <svg viewBox="0 0 300 64" style={{ width: '100%', height: 60 }}>
      <line x1="0" y1="32" x2="300" y2="32" stroke="var(--line)" />
      {data.map((v, i) => { const h = (Math.abs(v) / maxAbs) * 28; return v >= 0
        ? <rect key={i} x={6 + i * step} y={32 - h} width={bw} height={h} fill="var(--zhu)" opacity="0.9" />
        : <rect key={i} x={6 + i * step} y={32} width={bw} height={h} fill="var(--dai)" opacity="0.9" />; })}
    </svg>
  );
}
function DecileBars({ data }) {
  const max = Math.max(...data.map(Math.abs));
  return (
    <svg viewBox="0 0 300 64" style={{ width: '100%', height: 60 }}>
      <line x1="0" y1="60" x2="300" y2="60" stroke="var(--line)" />
      {data.map((v, i) => { const h = (Math.abs(v) / max) * 54; return (
        <rect key={i} x={6 + i * 29} y={60 - h} width="22" height={h}
          fill={i < 3 ? 'var(--dai)' : i < 7 ? 'var(--ink-3)' : 'var(--zhu)'} opacity="0.9" />
      ); })}
    </svg>
  );
}

// ───────── 主组件 ─────────
function ValidationApp() {
  const [active, setActive] = useState('s1');
  const [phase, setPhase] = useState('idle');   // idle | distilling | distilled
  const [draft, setDraft] = useState(null);     // 当前提炼出的经验
  const [kb, setKb] = useState([]);     // 从 /cards/list 拉真卡填充(见 refreshKb)
  const [sources, setSources] = useState(SOURCES);  // 左栏素材:从 /cards/list?status=draft 拉真·未验证卡,失败回退 mock
  const [srcStatus, setSrcStatus] = useState('demo'); // demo=拉取前示例 | live=后端真卡 | empty=后端空桶 | error=后端失败 — 非 live 必打「示例数据」标(审计 M2:兜底必显形)
  const [combos, setCombos] = useState([]);
  const [paste, setPaste] = useState('');
  const [toast, setToast] = useState(null);
  const [promoted, setPromoted] = useState(false);
  const [chat, setChat] = useState([]);
  const [thinking, setThinking] = useState(false);
  const [valRun, setValRun] = useState('idle');    // idle | running | done — 工作流回测结果回传
  const benchRef = useRef(null);
  const valRef = useRef(null);
  const applyRef = useRef(null);
  const timers = useRef([]);

  // 按卡 id 的「记忆」:切卡 / 刷新都不丢每张卡的提炼结果 + 对话 + 验证状态。
  // 初次从 localStorage 恢复(跨刷新),之后由下方 useEffect 持续快照落盘。
  const MEM_KEY = 'guanlan:cards:mem' + (WW_WS ? ':' + WW_WS : '');   // 帷幄会话各存各的卡记忆,独立页(无 ws)键不变
  const memRef = useRef(null);
  if (memRef.current === null) {
    try { memRef.current = JSON.parse(localStorage.getItem(MEM_KEY) || '{}') || {}; }
    catch (e) { memRef.current = {}; }
  }

  const src = sources.find(s => s.id === active);

  const flash = (msg) => { setToast(msg); clearTimeout(timers.current.t); timers.current.t = setTimeout(() => setToast(null), 3200); };
  const clearTimers = () => { (timers.current.list || []).forEach(clearTimeout); timers.current.list = []; };
  const resetAll = () => { setValRun('idle'); setPromoted(false); setCombos([]); };

  // 从 guanlan 自有 /cards 后端拉真经验卡填充右栏知识库(失败→空库, 绝不回退 mock)
  const refreshKb = useCallback(() => {
    fetch(API + '/cards/list')
      .then(r => (r.ok ? r.json() : Promise.reject(r.status)))
      .then(d => setKb((d.cards || []).map(cardToKb)))
      .catch(() => setKb([]));
  }, []);

  // 左栏「素材库·待提炼」← guanlan 自有「未验证」(draft)桶的真·视频经验卡
  const loadSources = useCallback(() => {
    fetch(API + '/cards/list?status=draft')
      .then(r => (r.ok ? r.json() : Promise.reject(r.status)))
      .then(d => {
        const arr = (d.cards || []).map(cardToSource);
        if (arr.length) {
          setSources(arr);
          setSrcStatus('live');
          setActive(a => (arr.some(s => s.id === a) ? a : arr[0].id));
        } else {
          setSrcStatus('empty');  // 后端健康但 draft 桶空 → 演示卡留着可玩, 但必须打标, 不冒充真素材
        }
      })
      .catch(() => setSrcStatus('error'));   // 失败 → 保留示例卡防白屏, 但顶部显形 + 可重试
  }, []);

  // 选中素材 → 有记忆则恢复(不重炼、不清对话);无记忆才首次提炼动画
  const selectSource = useCallback((id) => {
    clearTimers();
    const cached = memRef.current[id];
    if (cached && cached.draft) {
      // 恢复该卡的提炼结果 + 对话 + 验证状态,切回不丢、不重跑
      setActive(id);
      setPhase(cached.phase || 'distilled');
      setDraft(cached.draft);
      setChat(cached.chat || []);
      setValRun(cached.valRun || 'idle');
      setPromoted(!!cached.promoted);
      setCombos(cached.combos || []);
      return;
    }
    setActive(id); setPhase('distilling'); setDraft(null); setChat([]); resetAll();
    const s = sources.find(x => x.id === id);
    if (!s) return;
    timers.current.list = [setTimeout(() => {
      setDraft(s.distill); setPhase('distilled');
      setChat([{ role: 'asst', text: `已从「${s.from}」提炼出「${s.distill.name}」。可直接告诉我如何调整——阈值、频率、适用场景或因子表达式。` }]);
    }, 1150)];
  }, [sources]);

  // 粘贴文本 → 提炼
  const distillPaste = () => {
    if (!paste.trim()) return;
    clearTimers();
    setActive('paste'); setPhase('distilling'); setDraft(null); setChat([]); resetAll();
    const d = distillFromText(paste);
    timers.current.list = [setTimeout(() => {
      setDraft(d); setPhase('distilled');
      setChat([{ role: 'asst', text: `已提炼出「${d.name}」。可直接告诉我如何调整，或转入工作流做完整回测。` }]);
    }, 1150)];
  };

  // 对话修改经验（接后端 /cards/refine → 引擎大模型 deepseek，失败回退本地规则）
  const reviseDraft = async (instruction) => {
    const q = (instruction || '').trim();
    if (!q || !draft || thinking) return;
    setChat(c => [...c, { role: 'user', text: q }]);
    setThinking(true);
    let patch, reply;
    try {
      // 炼:送引擎大模型(deepseek)精炼,带基础 system prompt(后端 /cards/refine)
      const res = await fetch(API + '/cards/refine', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          draft: { name: draft.name, insight: draft.insight, conds: draft.conds, scenes: draft.scenes, expr: draft.expr },
          chat, instruction: q,
        }),
      });
      if (!res.ok) throw new Error('refine ' + res.status);
      const r = await res.json(); reply = r.reply; patch = r.patch || {};
    } catch (e) {
      // 引擎大模型不可用(无 key / 代理 / 离线)→ 本地规则兜底不卡流程, 但必须显形, 不冒充 LLM 回复(审计 M2)
      const r = mockRevise(q, draft); patch = r.patch;
      reply = '⚠ 大模型服务不可用,已降级为本地规则改写(仅支持阈值/频率等快捷修改,不理解复杂语义)。\n\n' + r.reply;
    }
    setDraft(d => ({
      ...d,
      name: patch.name || d.name,
      insight: patch.insight || d.insight,
      conds: Array.isArray(patch.conds) && patch.conds.length ? patch.conds : d.conds,
      scenes: Array.isArray(patch.scenes) && patch.scenes.length ? patch.scenes : d.scenes,
      expr: patch.expr || d.expr,
    }));
    setChat(c => [...c, { role: 'asst', text: reply }]);
    setThinking(false);
    if (valRun === 'done') { setValRun('idle'); setPromoted(false); }  // 经验已改，需重新验证
  };

  // 转入工作流验证：把经验表达式送入引擎 /factor/report 做真·单因子回测，结果回传至「数据验证」
  const runWorkflowValidate = () => {
    if (!draft || !draft.expr || !String(draft.expr).trim()) {
      flash('请先在「炼」里生成因子表达式（白名单 DSL），再来验证'); return;
    }
    clearTimers(); setValRun('running'); setPromoted(false);
    flash(`「${draft.name}」单因子回测中…`);
    setTimeout(() => valRef.current && valRef.current.scrollIntoView({ block: 'start' }), 60);
    fetch(API + '/factor/report2', {
      // 切 report2(壳内可配报告):比引擎 /factor/report 多返 ic.rank_ic_series + quantile.group_ann_return
      // → 「数据验证」两图喂真序列(审计 N2;M1 的守卫据此自动恢复渲染)
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ expr_or_name: draft.expr, universe: (draft.universe || 'csi_fast'), n_groups: 10 }),
    })
      .then(r => r.json())
      .then(j => {
        if (j && j.status === 'ok') {
          const ic = j.ic && j.ic.rank_ic_mean, icir = j.ic && j.ic.icir, pf = j.portfolio;
          setDraft(d => ({ ...d, val: {
            ic: Number(ic).toFixed(3), icir: Number(icir).toFixed(2),
            ann: (pf && pf.ann_return != null ? (pf.ann_return >= 0 ? '+' : '') + (pf.ann_return * 100).toFixed(1) + '%' : '—'),
            win: (pf && pf.win_rate != null ? (pf.win_rate * 100).toFixed(0) + '%' : '—'),
            mono: '—', n: String((j.meta && j.meta.n_codes) || '—'),
            sharpe: (pf && pf.sharpe != null ? Number(pf.sharpe).toFixed(2) : '—'),
            mdd: (pf && pf.max_drawdown != null ? (pf.max_drawdown * 100).toFixed(1) + '%' : '—'),
            conf: Math.max(0, Math.min(99, Math.round(Math.abs(Number(icir || 0)) * 50))),
            verdict: verdictFromIC(Number(ic), Number(icir)), real: true,
            // 真序列(report2):IC 时序 = 逐期 rank-IC ×100;十分位 = 各组年化 ×100;真换手
            ic_ts: (Array.isArray(j.ic && j.ic.rank_ic_series) ? j.ic.rank_ic_series : [])
              .map(p => +((Array.isArray(p) ? p[1] : p) * 100).toFixed(1)),
            decile_rets: (Array.isArray(j.quantile && j.quantile.group_ann_return) ? j.quantile.group_ann_return : [])
              .map(v => +(v * 100).toFixed(1)),
            turnover: (pf && pf.turnover != null) ? (pf.turnover * 100).toFixed(0) + '%' : null,
            // 真回测参数(report2 meta)→「回测参数」行不再显静态芯片
            meta: (j.meta && typeof j.meta === 'object') ? {
              universe: j.meta.universe, freq: j.meta.freq, start: j.meta.start, end: j.meta.end,
              n_codes: j.meta.n_codes, fwd_days: j.meta.fwd_days, n_groups: j.n_groups,
            } : null,
          } }));
          setValRun('done');
          setTimeout(() => applyRef.current && applyRef.current.scrollIntoView({ block: 'start' }), 90);
        } else {
          setDraft(d => ({ ...d, val: {
            ic: '—', icir: '—', ann: '—', win: '—', mono: '—', n: '—', conf: 0,
            verdict: '驳回', note: (j && (j.error || j.reason)) || '表达式无法编译/求值', real: true,
          } }));
          setValRun('done');
          setTimeout(() => applyRef.current && applyRef.current.scrollIntoView({ block: 'start' }), 90);
        }
      })
      .catch(() => { flash('验证后端不可用，请确认服务已起'); setValRun('idle'); });
  };

  // 打开真实工作流编辑器（携带经验快照）
  const openWorkflow = () => {
    if (window.GL) GL.handoff('workflow', { name: draft.name, expr: draft.expr, conds: draft.conds }, WW_WS);   // 产端带 ws,会话内交棒不走裸键(WW_WS 空=裸键,独立页如旧)
    // 工作流页在兄弟目录;裸文件名相对 /ui/cards/ 解析是 404(互通审计 P1⑧)。
    // 嵌入态透传 embed+ws:防 iframe 内导航跌回无 ws 独立态(画布/报告泄进全局键、本会话工作流 tab 看不到);独立打开两参皆空,URL 与旧版一致
    const q = (WW_EMBED ? '?embed=1' : '') + (WW_WS ? ((WW_EMBED ? '&' : '?') + 'ws=' + encodeURIComponent(WW_WS)) : '');
    window.location.href = '../factor/观澜 · AI 工作流.html' + q;
  };

  const promote = () => {
    if (!draft) return;
    setPromoted(true);
    // P2-A:有后端 id(EV-NNN 素材卡)→ 同 id 原位更新 + /status 真迁移(unlink 旧 draft 文件),
    // 不再 next_id 新建造双卡;GL 也用同一后端 id → seats syncArchive 不再二次合入(双 id 根因)。
    const hasBackendId = !!(src && src.id && /^EV-\d+$/.test(String(src.id)));
    const card = {
      ...(hasBackendId ? { id: src.id } : {}),
      title: draft.name, cat: draft.cat || '其他', tags: draft.tags.slice(0, 3),
      verdict: draft.val.verdict, conf: draft.val.conf, ic: draft.val.ic,
      expr: draft.expr, insight: draft.insight, src: src ? src.kind : '自定义',
      status: hasBackendId ? 'draft' : 'approved',   // 有 id:先原位更新 draft,再走 /status 迁移(纯 upsert 会留旧文件)
    };
    fetch(API + '/cards', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(card) })
      .then(r => (r.ok ? r.json() : Promise.reject(r.status)))
      .then(saved => {
        const cid = (saved && saved.id) || card.id;
        if (!hasBackendId || !cid) return cid;
        return fetch(API + '/cards/' + encodeURIComponent(cid) + '/status', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ status: 'approved', reviewed_by: 'validation-ui' }),
        }).then(() => cid);
      })
      .then(cid => {
        // 后端成功后才入共享档案,且用后端同一 id(EV-NNN)——图谱/落子与知识库一一对应
        if (window.GL && cid) {
          GL.put({ type: 'card', id: cid, title: draft.name, cat: draft.cat || '其他',
            tags: draft.tags.slice(0, 3), verdict: draft.val.verdict, conf: draft.val.conf, ic: draft.val.ic,
            insight: draft.insight, expr: draft.expr, status: 'validated', real: true, refs: [] });
        }
        refreshKb();
        try { loadSources(); } catch (e) {}   // draft 升级后从左栏素材库消失
        flash(`「${draft.name}」已沉淀入经验知识库${cid ? '(' + cid + ')' : ''} · 对话/研报 wisdom_search 可引用`);
      })
      .catch(() => { refreshKb(); flash(`「${draft.name}」后端写入失败(检查 /cards 服务);未写共享档案`); });
  };

  const genCombos = () => {
    const list = (src && src.combos) || SOURCES[0].combos;
    setCombos(list);
    flash(`已据「${draft ? draft.name : ''}」生成 ${list.length} 组候选因子组合`);
    if (benchRef.current) benchRef.current.scrollTo({ top: 0 });
  };

  useEffect(() => () => clearTimers(), []);

  // 首屏: 从后端加载真经验库(初期为空是正常的, 沉淀后自然出现真卡)
  useEffect(() => { refreshKb(); }, [refreshKb]);

  // 首屏: 从「未验证」桶加载真·视频经验到左栏素材库
  useEffect(() => { loadSources(); }, [loadSources]);

  // 把当前卡的状态(提炼结果 + 对话 + 验证/沉淀/组合)持续快照进「记忆」并落 localStorage。
  // 仅在 distilled 稳定态落盘,避免缓存到提炼动画中途的空态;切卡 / 刷新后可由 selectSource 恢复。
  useEffect(() => {
    if (phase !== 'distilled' || !active || !draft) return;
    memRef.current[active] = { phase, draft, chat, valRun, promoted, combos };
    try { localStorage.setItem(MEM_KEY, JSON.stringify(memRef.current)); } catch (e) {}
  }, [active, phase, draft, chat, valRun, promoted, combos]);

  // 反跳落地:从落子等模块带 focusCardName 进来 → 自动定位该经验的来源与验证
  useEffect(() => {
    // peek 非消费读取:左栏真卡是异步拉来的, 命中才 take 消费;未命中(首屏还是 mock)
    // 则留待 sources 加载后本 effect 重跑再匹配。依赖 [sources] 修掉旧的空依赖 stale-closure。
    const h = window.GL && GL.peek('validation', WW_WS);   // WW_WS=按帷幄会话读信箱
    if (!h || !h.focusCardName) return;
    const key = String(h.focusCardName);
    const hit = sources.find(s => {
      const n = s.distill.name;
      return n.includes(key) || key.includes(n)
        || (/反转|缩量/.test(key) && /反转/.test(n))
        || (/北向|资金/.test(key) && /北向/.test(n))
        || (/高位|退潮|放量|滞涨/.test(key) && /退潮/.test(n))
        || (/业绩|pead|漂移/i.test(key) && /PEAD/.test(n));
    });
    if (hit) {
      if (window.GL) GL.take('validation', WW_WS);   // 命中才消费(按会话键)
      const t = setTimeout(() => selectSource(hit.id), 140); timers.current.list = [...(timers.current.list || []), t]; flash(`已定位经验「${key}」的来源原文与验证`);
    }
  }, [sources, selectSource]);

  let phaseIdx = 0;
  if (phase === 'distilling' || phase === 'distilled') phaseIdx = 1;
  if (valRun !== 'idle') phaseIdx = 2;
  if (valRun === 'done') phaseIdx = 3;

  return (
    <div style={{ height: '100vh', display: 'grid', gridTemplateRows: WW_EMBED ? '1fr' : '52px 1fr' }} className="paper-bg">
      {!WW_EMBED && <Header kbCount={kb.length} pending={sources.length} />}
      <div style={{ display: 'grid', gridTemplateColumns: '296px 1fr 350px', minHeight: 0 }}>
        <SourcePane sources={sources} active={active} onPick={selectSource}
          paste={paste} setPaste={setPaste} onDistill={distillPaste}
          srcStatus={srcStatus} onRetry={loadSources} />
        <main ref={benchRef} style={{ overflowY: 'auto', borderLeft: '1px solid var(--line)', borderRight: '1px solid var(--line)', background: 'rgba(241,234,217,0.35)' }}>
          <PhaseRail idx={phaseIdx} />
          <div style={{ padding: '6px 30px 60px', maxWidth: 760, margin: '0 auto' }}>
            <StageSource src={active === 'paste' ? null : src} paste={active === 'paste' ? paste : null} />
            {phase !== 'idle' && (
              <StageDistill phase={phase} draft={draft} chat={chat} thinking={thinking}
                onSend={reviseDraft} onValidate={runWorkflowValidate} onOpenWorkflow={openWorkflow} valStarted={valRun !== 'idle'} />
            )}
            {valRun !== 'idle' && draft && (
              <div ref={valRef}>
                <StageValidate valRun={valRun} draft={draft} onOpenWorkflow={openWorkflow} />
              </div>
            )}
            {valRun === 'done' && draft && (
              <div ref={applyRef}>
                <StageApply draft={draft} promoted={promoted} onPromote={promote} onGen={genCombos} onOpenWorkflow={openWorkflow} />
              </div>
            )}
          </div>
        </main>
        <RightPane combos={combos} kb={kb} />
      </div>
      {toast && (
        <div style={{ position: 'fixed', bottom: 22, left: '50%', transform: 'translateX(-50%)', zIndex: 30, display: 'flex', alignItems: 'center', gap: 10, background: 'var(--paper)', border: '1px solid var(--dai-soft)', borderRadius: 11, padding: '11px 18px', boxShadow: '0 6px 26px rgba(28,24,20,0.18)', animation: 'fadeIn .3s ease' }}>
          <span className="seal" style={{ width: 22, height: 22, fontSize: 12, background: 'var(--dai)' }}>瀾</span>
          <span className="serif" style={{ fontSize: 13, color: 'var(--ink-1)' }}>{toast}</span>
        </div>
      )}
    </div>
  );
}

// ───────── 顶栏 ─────────
function Header({ kbCount, pending }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 14, padding: '0 20px', borderBottom: '1px solid var(--line)', background: 'rgba(241,234,217,0.75)' }}>
      <div style={{ display: 'flex', flexDirection: 'column', lineHeight: 1.15 }}>
        <span className="serif" style={{ fontSize: 15, fontWeight: 600, letterSpacing: '0.05em' }}>經驗總結 · 驗證區</span>
        <span className="mono" style={{ fontSize: 8.5, color: 'var(--ink-3)', letterSpacing: '0.2em', marginTop: 2 }}>EXPERIENCE DISTILLATION & VALIDATION</span>
      </div>
      <span className="mono" style={{ fontSize: 11, color: 'var(--ink-2)', marginLeft: 18 }}>
        文本信息 <span style={{ color: 'var(--ink-3)' }}>→</span> 提炼·对话修改 <span style={{ color: 'var(--ink-3)' }}>→</span> 转入工作流验证 <span style={{ color: 'var(--ink-3)' }}>→</span> 知识库·因子组合
      </span>
      <div style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: 12 }}>
        <span className="mono" style={{ fontSize: 11, color: 'var(--ink-2)' }}>知识库 <b style={{ color: 'var(--ink)' }}>{kbCount}</b> 条 · 待验证 <b style={{ color: 'var(--yin)' }}>{pending}</b></span>
      </div>
    </div>
  );
}

// ───────── 阶段导航 原→炼→验→用 ─────────
function PhaseRail({ idx }) {
  const steps = [['原', '采集原文'], ['炼', '提炼·修改'], ['验', '工作流回测'], ['用', '决定去向']];
  return (
    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 0, padding: '16px 0 10px', borderBottom: '1px solid var(--line-soft)', position: 'sticky', top: 0, background: 'rgba(241,234,217,0.92)', backdropFilter: 'blur(3px)', zIndex: 5 }}>
      {steps.map(([z, l], i) => {
        const on = i <= idx; const cur = i === idx;
        return (
          <React.Fragment key={z}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 9 }}>
              <span className="serif" style={{ width: 30, height: 30, borderRadius: '50%', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 15, fontWeight: 600,
                background: on ? 'var(--yin)' : 'transparent', color: on ? 'var(--paper)' : 'var(--ink-3)',
                border: on ? 'none' : '1px solid var(--line)', boxShadow: cur ? '0 0 0 4px rgba(168,57,45,0.14)' : 'none', transition: 'all .4s ease' }}>{z}</span>
              <span className="mono" style={{ fontSize: 10, color: on ? 'var(--ink-1)' : 'var(--ink-3)', letterSpacing: '.04em', whiteSpace: 'nowrap' }}>{l}</span>
            </div>
            {i < steps.length - 1 && <span style={{ width: 40, height: 1, margin: '0 12px', background: i < idx ? 'var(--yin)' : 'var(--line)', transition: 'background .4s', flexShrink: 0 }} />}
          </React.Fragment>
        );
      })}
    </div>
  );
}

// ───────── 区块外壳 ─────────
function Block({ z, title, hint, accent, children, right }) {
  return (
    <section style={{ marginTop: 22, animation: 'fadeIn .45s ease' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 13 }}>
        <span className="serif" style={{ width: 24, height: 24, borderRadius: 6, display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 13, fontWeight: 600, background: accent || 'var(--ink)', color: 'var(--paper)' }}>{z}</span>
        <span className="serif" style={{ fontSize: 16, fontWeight: 600, color: 'var(--ink)', whiteSpace: 'nowrap' }}>{title}</span>
        {hint && <span className="mono" style={{ fontSize: 9.5, color: 'var(--ink-3)', letterSpacing: '.08em', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{hint}</span>}
        {right && <span style={{ marginLeft: 'auto' }}>{right}</span>}
      </div>
      {children}
    </section>
  );
}

// ───────── 一 · 原文 ─────────
function StageSource({ src, paste }) {
  return (
    <Block z="原" title="采集原文" hint={src ? `${src.from} · ${src.time}` : '自定义粘贴'} accent="var(--ink-1)">
      <div style={{ border: '1px solid var(--line)', borderRadius: 12, background: 'var(--paper)', padding: '18px 22px', boxShadow: '0 1px 5px rgba(28,24,20,0.05)' }}>
        {src ? (
          <>
            <div style={{ display: 'flex', alignItems: 'center', gap: 9, marginBottom: 12 }}>
              <span className="mono" style={{ fontSize: 9, color: 'var(--paper)', background: KIND[src.kind].c, borderRadius: 4, padding: '2px 7px' }}>{src.kind}</span>
              <span className="serif" style={{ fontSize: 15, fontWeight: 600, color: 'var(--ink)' }}>{src.title}</span>
            </div>
            <p className="serif" style={{ fontSize: 14, lineHeight: 2, color: 'var(--ink-1)', margin: 0, textWrap: 'pretty' }}>
              {src.seg.map((s, i) => s.cite ? (
                <span key={i} style={{ background: 'rgba(168,57,45,0.10)', boxShadow: 'inset 0 -1px 0 var(--zhu-soft)', padding: '1px 2px', borderRadius: 2 }}>
                  {s.t}<sup className="mono" style={{ fontSize: 8.5, color: 'var(--yin)', marginLeft: 2 }}>§{s.cite}</sup>
                </span>
              ) : <span key={i} style={{ color: 'var(--ink-2)' }}>{s.t}</span>)}
            </p>
            <div className="mono" style={{ fontSize: 9.5, color: 'var(--ink-3)', marginTop: 14, display: 'flex', gap: 7, alignItems: 'center' }}>
              <span style={{ width: 16, height: 1, background: 'var(--zhu-soft)' }} />观澜已标记 {src.seg.filter(s => s.cite).length} 处关键证据
            </div>
          </>
        ) : (
          <p className="serif" style={{ fontSize: 14, lineHeight: 2, color: 'var(--ink-1)', margin: 0, whiteSpace: 'pre-wrap', textWrap: 'pretty' }}>{paste}</p>
        )}
      </div>
    </Block>
  );
}

// ───────── 二 · 提炼经验（可对话修改） ─────────
const DISTILL_STEPS = ['通读原文', '抽取规律与阈值', '结构化为经验卡'];
const REFINE_CHIPS = ['放宽阈值', '收紧阈值', '改为日频', '补充适用场景', '简化因子表达式'];

function StageDistill({ phase, draft, chat, thinking, onSend, onValidate, onOpenWorkflow, valStarted }) {
  const distilling = phase === 'distilling';
  const edited = chat && chat.some(m => m.role === 'user');
  return (
    <Block z="炼" title="提炼专属经验" hint="文本 → 结构化经验 · 可对话修改" accent="var(--yin)">
      {distilling ? (
        <div style={{ border: '1px solid var(--zhu-soft)', borderRadius: 12, background: 'rgba(168,57,45,0.04)', padding: '20px 22px' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 14 }}>
            <span className="seal" style={{ width: 22, height: 22, fontSize: 12 }}>瀾</span>
            <span className="serif" style={{ fontSize: 13.5, color: 'var(--ink-1)' }}>正在炼制经验…</span>
          </div>
          {DISTILL_STEPS.map((s, i) => (
            <div key={s} style={{ display: 'flex', alignItems: 'center', gap: 11, padding: '6px 0', animation: `fadeIn .4s ease ${i * 0.34}s both` }}>
              <span style={{ width: 7, height: 7, borderRadius: '50%', background: 'var(--yin)', animation: 'pulse 1s infinite' }} />
              <span className="mono" style={{ fontSize: 11.5, color: 'var(--ink-2)' }}>{s}</span>
            </div>
          ))}
        </div>
      ) : draft ? (
        <>
          <ExpCardLarge draft={draft} edited={edited} />
          {WW_LEGACY && <ChatRefine chat={chat} thinking={thinking} onSend={onSend} />}
          <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginTop: 14, flexWrap: 'wrap' }}>
            <span onClick={onValidate} className="serif" style={{ display: 'inline-flex', alignItems: 'center', gap: 8, fontSize: 13.5, color: 'var(--paper)', background: 'var(--ink)', borderRadius: 9, padding: '10px 20px', cursor: 'pointer', boxShadow: '0 2px 10px rgba(28,24,20,0.14)' }}>
              {valStarted ? '↻ 重新送入工作流回测' : '瀾 转入工作流验证 →'}
            </span>
            <span onClick={onOpenWorkflow} className="mono" style={{ fontSize: 11, color: 'var(--ink-2)', borderBottom: '1px dashed var(--ink-3)', paddingBottom: 1, cursor: 'pointer' }}>打开工作流编辑器 ↗</span>
            <span className="mono" style={{ marginLeft: 'auto', fontSize: 9.5, color: 'var(--ink-3)' }}>回测在工作流中完成，结果回传至「数据验证」</span>
          </div>
        </>
      ) : null}
    </Block>
  );
}

// 把 **加粗** 渲染成 <b>;其余原样。返回 React 节点数组。
function mdBold(text, kp) {
  return String(text).split(/\*\*([^*]+)\*\*/g).map((p, i) => i % 2 === 1
    ? <b key={kp + '-' + i} style={{ color: 'var(--ink)', fontWeight: 600 }}>{p}</b>
    : <React.Fragment key={kp + '-' + i}>{p}</React.Fragment>);
}

// 提炼出的「洞察」排版:含「1. 2. 3.」编号则排成条件列表(编号徽章 + 加粗条件名),
// 否则按段落渲染;两种都支持 **加粗**。纯展示,不改 draft 数据。
function InsightBody({ insight }) {
  const text = String(insight || '').trim();
  if (!text) return null;
  const firstIdx = text.search(/\d+[.、]\s/);
  const isList = firstIdx >= 0 && (text.match(/\d+[.、]\s/g) || []).length >= 2;
  if (!isList) {
    return (
      <div style={{ margin: '0 0 16px' }}>
        {text.split(/\n+/).filter(Boolean).map((para, i) => (
          <p key={i} className="serif" style={{ fontSize: 13.5, lineHeight: 1.75, color: 'var(--ink-1)', margin: i ? '8px 0 0' : 0, textWrap: 'pretty' }}>{mdBold(para, 'p' + i)}</p>
        ))}
      </div>
    );
  }
  const intro = text.slice(0, firstIdx).trim();
  const items = text.slice(firstIdx).split(/(?=\d+[.、]\s)/).map(s => s.trim()).filter(Boolean);
  return (
    <div style={{ margin: '0 0 18px' }}>
      {intro && (
        <div style={{ display: 'flex', gap: 10, margin: '0 0 14px', alignItems: 'stretch' }}>
          <span style={{ flexShrink: 0, width: 3, borderRadius: 2, background: 'var(--zhu-soft)' }} />
          <p className="serif" style={{ fontSize: 13.5, lineHeight: 1.75, color: 'var(--ink-1)', margin: 0, textWrap: 'pretty' }}>{mdBold(intro, 'intro')}</p>
        </div>
      )}
      <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
        {items.map((it, i) => {
          const mm = it.match(/^(\d+)[.、]\s*([\s\S]*)$/);
          const num = mm ? mm[1] : String(i + 1);
          const body = mm ? mm[2] : it;
          const dm = body.match(/^([\s\S]*?)\s*[—－–]\s*([\s\S]*)$/);
          const name = dm ? dm[1] : body;
          const expl = dm ? dm[2] : '';
          return (
            <div key={i} style={{ display: 'flex', gap: 11, alignItems: 'flex-start' }}>
              <span className="mono" style={{ flexShrink: 0, width: 20, height: 20, borderRadius: '50%', display: 'inline-flex', alignItems: 'center', justifyContent: 'center', fontSize: 10, color: 'var(--yin)', background: 'rgba(168,57,45,0.07)', border: '1px solid var(--zhu-soft)', marginTop: 1 }}>{num}</span>
              <div style={{ minWidth: 0, flex: 1, paddingBottom: i < items.length - 1 ? 12 : 0, borderBottom: i < items.length - 1 ? '1px solid var(--line-soft)' : 'none' }}>
                <div className="serif" style={{ fontSize: 13.5, lineHeight: 1.55, color: 'var(--ink)', fontWeight: 600 }}>{mdBold(name, 'n' + i)}</div>
                {expl && <div className="serif" style={{ fontSize: 12, lineHeight: 1.65, color: 'var(--ink-2)', marginTop: 3, textWrap: 'pretty' }}>{mdBold(expl, 'e' + i)}</div>}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function ExpCardLarge({ draft, edited }) {
  return (
    <div style={{ border: '1px solid var(--ink)', borderRadius: 12, background: 'var(--paper)', padding: '18px 22px', boxShadow: '0 2px 12px rgba(28,24,20,0.08)' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 9, marginBottom: 10 }}>
        <span className="seal" style={{ width: 22, height: 22, fontSize: 12 }}>經</span>
        <span className="serif" style={{ fontSize: 17, fontWeight: 600, color: 'var(--ink)' }}>{draft.name}</span>
        {edited && <span className="mono" style={{ fontSize: 8, color: 'var(--paper)', background: 'var(--jin)', borderRadius: 4, padding: '2px 6px' }}>已修订</span>}
        <div style={{ marginLeft: 'auto', display: 'flex', gap: 5 }}>
          {draft.tags.map(t => <span key={t} className="mono" style={{ fontSize: 9, color: 'var(--ink-3)', border: '1px solid var(--line)', borderRadius: 5, padding: '2px 7px' }}>{t}</span>)}
        </div>
      </div>
      <InsightBody insight={draft.insight} />
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16 }}>
        <div>
          <div className="mono" style={{ fontSize: 9, letterSpacing: '.14em', color: 'var(--ink-3)', marginBottom: 9 }}>触发条件</div>
          {draft.conds.map(([k, op, v], i) => (
            <div key={i} style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '6px 0', borderBottom: i < draft.conds.length - 1 ? '1px solid var(--line-soft)' : 'none' }}>
              <span style={{ fontSize: 12, color: 'var(--ink-2)', flex: 1 }}>{k}</span>
              <span className="mono" style={{ fontSize: 11, color: 'var(--ink-3)' }}>{op}</span>
              <span className="mono" style={{ fontSize: 12, color: 'var(--ink)', fontWeight: 600 }}>{v}</span>
            </div>
          ))}
        </div>
        <div>
          <div className="mono" style={{ fontSize: 9, letterSpacing: '.14em', color: 'var(--ink-3)', marginBottom: 9 }}>适用场景</div>
          <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginBottom: 16 }}>
            {draft.scenes.map(s => <span key={s} className="mono" style={{ fontSize: 10, color: 'var(--dai)', border: '1px solid var(--dai-soft)', borderRadius: 5, padding: '3px 9px' }}>{s}</span>)}
          </div>
          <div className="mono" style={{ fontSize: 9, letterSpacing: '.14em', color: 'var(--ink-3)', marginBottom: 9 }}>因子表达式</div>
          <code className="mono" style={{ display: 'block', fontSize: 11, color: 'var(--ink)', background: 'rgba(28,24,20,0.04)', borderRadius: 7, padding: '10px 12px', lineHeight: 1.5, wordBreak: 'break-all' }}>{draft.expr}</code>
        </div>
      </div>
    </div>
  );
}

// 对话修改面板
function ChatRefine({ chat, thinking, onSend }) {
  const [q, setQ] = useState('');
  const threadRef = useRef(null);
  const send = (text) => { const t = text != null ? text : q; if (!t.trim() || thinking) return; onSend(t); setQ(''); };
  useEffect(() => { if (threadRef.current) threadRef.current.scrollTop = threadRef.current.scrollHeight; }, [chat, thinking]);
  return (
    <div style={{ marginTop: 12, border: '1px solid var(--zhu-soft)', borderRadius: 12, background: 'rgba(168,57,45,0.03)', overflow: 'hidden' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '10px 14px', borderBottom: '1px solid var(--line-soft)' }}>
        <span className="seal" style={{ width: 20, height: 20, fontSize: 11 }}>瀾</span>
        <span className="serif" style={{ fontSize: 13, fontWeight: 600, color: 'var(--ink-1)' }}>与观澜对话修改</span>
        <span className="mono" style={{ fontSize: 9, color: 'var(--ink-3)' }}>自然语言微调 · 卡片实时更新</span>
      </div>
      <div ref={threadRef} style={{ maxHeight: 172, overflowY: 'auto', padding: '12px 14px', display: 'flex', flexDirection: 'column', gap: 9 }}>
        {chat.map((m, i) => m.role === 'asst' ? (
          <div key={i} style={{ display: 'flex', gap: 8, alignItems: 'flex-start' }}>
            <span className="seal" style={{ width: 18, height: 18, fontSize: 9, flexShrink: 0, marginTop: 1 }}>瀾</span>
            <span className="serif" style={{ fontSize: 12.5, lineHeight: 1.6, color: 'var(--ink-1)', background: 'var(--paper)', border: '1px solid var(--line)', borderRadius: '3px 10px 10px 10px', padding: '7px 11px', maxWidth: '84%' }}>{m.text}</span>
          </div>
        ) : (
          <div key={i} style={{ display: 'flex', justifyContent: 'flex-end' }}>
            <span className="serif" style={{ fontSize: 12.5, lineHeight: 1.6, color: 'var(--paper)', background: 'var(--ink-1)', borderRadius: '10px 3px 10px 10px', padding: '7px 11px', maxWidth: '84%' }}>{m.text}</span>
          </div>
        ))}
        {thinking && (
          <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
            <span className="seal" style={{ width: 18, height: 18, fontSize: 9 }}>瀾</span>
            <span className="mono" style={{ fontSize: 11, color: 'var(--ink-3)' }}>观澜推敲中 <span style={{ animation: 'pulse 1s infinite' }}>●●●</span></span>
          </div>
        )}
      </div>
      <div style={{ padding: '2px 14px 8px', display: 'flex', gap: 6, flexWrap: 'wrap' }}>
        {REFINE_CHIPS.map(c => <span key={c} onClick={() => send(c)} className="mono" style={{ fontSize: 9.5, color: 'var(--yin)', border: '1px solid var(--zhu-soft)', borderRadius: 14, padding: '3px 10px', cursor: thinking ? 'default' : 'pointer', opacity: thinking ? 0.5 : 1 }}>{c}</span>)}
      </div>
      <div style={{ display: 'flex', gap: 8, padding: '8px 14px 12px', borderTop: '1px solid var(--line-soft)' }}>
        <input value={q} onChange={e => setQ(e.target.value)} onKeyDown={e => { if (e.key === 'Enter') send(); }} placeholder="例如：把量比阈值放宽到 0.8 / 改成日频 / 再补一个适用场景…"
          style={{ flex: 1, boxSizing: 'border-box', border: '1px solid var(--line)', borderRadius: 8, padding: '8px 11px', fontFamily: 'var(--serif)', fontSize: 12.5, color: 'var(--ink)', background: 'var(--paper)', outline: 'none' }} />
        <span onClick={() => send()} className="serif" style={{ fontSize: 12.5, color: 'var(--paper)', background: 'var(--yin)', borderRadius: 8, padding: '8px 16px', cursor: thinking ? 'default' : 'pointer', opacity: thinking ? 0.5 : 1, whiteSpace: 'nowrap' }}>发送</span>
      </div>
    </div>
  );
}

// ───────── 三 · 数据验证（工作流回测结果回传） ─────────
const WF_STEPS = [
  ['送入工作流编排', '因子链 · 6 节点'],
  ['向量化回测', '沪深300 · 49 期截面'],
  ['因子分析 · 显著性检验', 'Newey-West t 检验'],
  ['结果回传至验证区', ''],
];
function StageValidate({ valRun, draft, onOpenWorkflow }) {
  const v = draft.val;
  const ver = VERDICT[v.verdict];
  const freq = (draft.scenes || []).find(s => /频/.test(s)) || '周频';
  const real = !!v.real;   // 必须先于 params 声明:babel env 把 const 转 var 提升,声明后置时此处读到 undefined → 真态永显演示芯片
  // 真验证态用 report2 真 meta;演示态才显静态示例参数(原静态芯片在真态误导——显示沪深300/2016-2025 实跑 csi_fast/近1年)
  const params = (real && v.meta) ? [
    ['股票池', v.meta.universe || '—'], ['区间', (v.meta.start || '?') + ' ~ ' + (v.meta.end || '?')],
    ['频率', v.meta.freq || '—'], ['分组', String(v.meta.n_groups || 10)],
    ['前瞻', v.meta.fwd_days != null ? v.meta.fwd_days + 'd' : '—'], ['样本股', String(v.meta.n_codes || '—')],
  ] : [['股票池', '沪深300'], ['区间', '2016–2025'], ['频率', freq], ['分组', '10'], ['基准', '沪深300'], ['双边成本', '15bps'], ['中性化', '行业·市值'], ['预处理', '去极值·标准化']];

  if (valRun === 'running') {
    return (
      <Block z="验" title="数据验证" hint="工作流回测中…" accent="var(--dai)">
        <div style={{ border: '1px solid var(--dai-soft)', borderRadius: 12, background: 'rgba(74,107,92,0.04)', padding: '20px 22px' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 12 }}>
            <span className="seal" style={{ width: 22, height: 22, fontSize: 12, background: 'var(--dai)' }}>瀾</span>
            <span className="serif" style={{ fontSize: 13.5, color: 'var(--ink-1)' }}>已送入 AI 工作流执行回测…</span>
          </div>
          {WF_STEPS.map(([s, m], i) => (
            <div key={s} style={{ display: 'flex', alignItems: 'center', gap: 11, padding: '7px 0', animation: `fadeIn .4s ease ${i * 0.4}s both` }}>
              <span style={{ width: 7, height: 7, borderRadius: '50%', background: 'var(--dai)', animation: 'pulse 1s infinite' }} />
              <span className="mono" style={{ fontSize: 11.5, color: 'var(--ink-2)' }}>{s}</span>
              {m && <span className="mono" style={{ marginLeft: 'auto', fontSize: 9.5, color: 'var(--ink-3)' }}>{m}</span>}
            </div>
          ))}
        </div>
      </Block>
    );
  }
  // done — 结果回传(real 已在函数顶部声明)
  // 第三格: 真值用 Sharpe(val.sharpe), 旧演示态仍显 t 值(按 icir 编)
  const tval = (parseFloat(v.icir) * 2.1).toFixed(2);
  const thirdLabel = real ? 'Sharpe' : 't 值';
  const thirdVal = real ? (v.sharpe != null ? v.sharpe : '—') : tval;
  // 最大回撤: 真值用引擎回传 val.mdd; 旧演示态按 verdict 编
  const mdd = real ? (v.mdd != null ? v.mdd : '—') : (v.verdict === '通过' ? '−11.4%' : v.verdict === '存疑' ? '−16.8%' : '−23.0%');
  const annDown = typeof v.ann === 'string' && (v.ann.includes('−') || v.ann.includes('-'));
  const kpi = [
    ['RankIC', v.ic, 'up'], ['ICIR', v.icir, ''], [thirdLabel, thirdVal, ''], ['多空年化', v.ann, annDown ? 'down' : 'up'],
    ['胜率', v.win, ''], ['换手', real ? (v.turnover != null ? v.turnover : '—') : '38%', ''], ['最大回撤', mdd, 'down'], ['样本数', v.n, ''],
  ];
  return (
    <Block z="验" title="数据验证" hint={real ? '真·单因子回测 · /factor/report' : '演示占位 · 量化端未接(数值非真实回测)'} accent="var(--dai)"
      right={<span onClick={onOpenWorkflow} className="mono" style={{ fontSize: 10, color: 'var(--ink-2)', borderBottom: '1px dashed var(--ink-3)', cursor: 'pointer' }}>查看完整编排 ↗</span>}>
      <div style={{ border: '1px solid var(--line)', borderRadius: 12, background: 'var(--paper)', padding: '16px 22px 18px', boxShadow: '0 1px 5px rgba(28,24,20,0.05)' }}>
        {/* 回测参数（在工作流中配置，此处只读回传） */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 7, flexWrap: 'wrap', paddingBottom: 14, borderBottom: '1px solid var(--line-soft)' }}>
          <span className="mono" style={{ fontSize: 8.5, letterSpacing: '.12em', color: 'var(--ink-3)', marginRight: 2 }}>回测参数</span>
          {params.map(([k, val]) => (
            <span key={k} className="mono" style={{ fontSize: 9.5, color: 'var(--ink-2)', background: 'rgba(28,24,20,0.04)', borderRadius: 5, padding: '2px 8px' }}>{k} <b style={{ color: 'var(--ink)' }}>{val}</b></span>
          ))}
        </div>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4,1fr)', gap: '14px 12px', padding: '15px 0' }}>
          {kpi.map(([l, val, d], i) => (
            <div key={i}>
              <div className="mono" style={{ fontSize: 8.5, letterSpacing: '.1em', color: 'var(--ink-3)' }}>{l}</div>
              <div className={'mono ' + (d || '')} style={{ fontSize: 17, fontWeight: 500, color: d ? undefined : 'var(--ink)', marginTop: 3 }}>{val}</div>
            </div>
          ))}
        </div>
        {/* 真序列守卫: 仅当 /factor/report 真返 v.ic_ts / v.decile_rets 才渲染图 — 此前为硬编示意数组, 真验证态也显假图(审计 M1 拔除;后端补返序列字段后自动恢复) */}
        {((Array.isArray(v.ic_ts) && v.ic_ts.length > 0) || (Array.isArray(v.decile_rets) && v.decile_rets.length > 0)) && (
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 22, padding: '4px 0 14px', borderTop: '1px solid var(--line-soft)', marginTop: 2 }}>
            <div style={{ paddingTop: 12 }}>
              <div className="mono" style={{ fontSize: 9, letterSpacing: '.12em', color: 'var(--ink-3)', marginBottom: 7 }}>IC 时间序列</div>
              {Array.isArray(v.ic_ts) && v.ic_ts.length > 0 ? <ICBars data={v.ic_ts} /> : <span className="mono" style={{ fontSize: 9.5, color: 'var(--ink-3)' }}>无序列数据</span>}
            </div>
            <div style={{ paddingTop: 12 }}>
              <div className="mono" style={{ fontSize: 9, letterSpacing: '.12em', color: 'var(--ink-3)', marginBottom: 7 }}>十分位年化超额</div>
              {Array.isArray(v.decile_rets) && v.decile_rets.length > 0 ? <DecileBars data={v.decile_rets} /> : <span className="mono" style={{ fontSize: 9.5, color: 'var(--ink-3)' }}>无序列数据</span>}
            </div>
          </div>
        )}
        <ConfBar conf={v.conf} ver={ver} />
        <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginTop: 16, padding: '12px 15px', borderRadius: 10, background: ver.bg }}>
          <span className="serif" style={{ width: 30, height: 30, borderRadius: '50%', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 15, fontWeight: 600, background: ver.c, color: 'var(--paper)', flexShrink: 0 }}>{ver.sym}</span>
          <div>
            <span className="serif" style={{ fontSize: 14, fontWeight: 600, color: ver.c }}>验证结论 · {v.verdict}</span>
            <div className="serif" style={{ fontSize: 12, color: 'var(--ink-2)', marginTop: 2 }}>{v.note ? v.note : `${ver.note}。${v.verdict === '通过' ? '分位单调、IC 显著，可纳入知识库与因子合成。' : v.verdict === '存疑' ? 'IC 半衰期偏短或样本不足，建议回工作流改频或叠加门控后再验。' : '统计不显著，建议放弃或重新提炼。'}`}</div>
          </div>
        </div>
      </div>
    </Block>
  );
}

function ConfBar({ conf, ver }) {
  return (
    <div style={{ marginTop: 4 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 6 }}>
        <span className="mono" style={{ fontSize: 9.5, letterSpacing: '.1em', color: 'var(--ink-3)' }}>置信度</span>
        <span className="mono" style={{ fontSize: 12, color: ver.c, fontWeight: 600 }}>{conf} / 100</span>
      </div>
      <div style={{ height: 7, borderRadius: 4, background: 'rgba(28,24,20,0.07)', overflow: 'hidden' }}>
        <div style={{ width: conf + '%', height: '100%', background: ver.c, borderRadius: 4, transition: 'width .8s ease' }} />
      </div>
    </div>
  );
}

// ───────── 四 · 决定去向 ─────────
function StageApply({ draft, promoted, onPromote, onGen, onOpenWorkflow }) {
  return (
    <Block z="用" title="决定去向" hint="回测已出结论 · 决定这条经验的后续" accent="var(--jin)">
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 14 }}>
        <ApplyCard
          title="沉淀入经验知识库"
          desc="将验证后的经验连同因子表达式、回测结论一并存档，供 agent 后续检索、复用与组合。"
          btn={promoted ? '✓ 已沉淀' : '⊕ 存入知识库'}
          done={promoted} onClick={promoted ? null : onPromote} accent="var(--ink)" />
        <ApplyCard
          title="生成新因子组合"
          desc="agent 在知识库中检索低相关经验，与本条经验合成多因子方案并预估增益。"
          btn="瀾 生成因子组合 →" onClick={onGen} accent="var(--yin)" />
      </div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginTop: 12, padding: '11px 15px', border: '1px dashed var(--line)', borderRadius: 10 }}>
        <span className="serif" style={{ fontSize: 12, color: 'var(--ink-2)' }}>结论存疑或想继续调参？</span>
        <span onClick={onOpenWorkflow} className="serif" style={{ fontSize: 12, color: 'var(--yin)', cursor: 'pointer', borderBottom: '1px dashed var(--zhu-soft)', paddingBottom: 1 }}>回工作流迭代因子链 ↗</span>
      </div>
    </Block>
  );
}
function ApplyCard({ title, desc, btn, onClick, done, accent }) {
  return (
    <div style={{ border: '1px solid ' + (done ? 'var(--dai-soft)' : 'var(--line)'), borderRadius: 12, background: done ? 'rgba(74,107,92,0.05)' : 'var(--paper)', padding: '16px 18px', display: 'flex', flexDirection: 'column' }}>
      <span className="serif" style={{ fontSize: 14, fontWeight: 600, color: 'var(--ink)', marginBottom: 7 }}>{title}</span>
      <p className="serif" style={{ fontSize: 12, lineHeight: 1.65, color: 'var(--ink-2)', margin: '0 0 14px', flex: 1, textWrap: 'pretty' }}>{desc}</p>
      <span onClick={onClick || undefined} className="serif" style={{ textAlign: 'center', fontSize: 12.5, color: done ? 'var(--dai)' : 'var(--paper)', background: done ? 'transparent' : accent, border: done ? '1px solid var(--dai-soft)' : 'none', borderRadius: 8, padding: '9px', cursor: done ? 'default' : 'pointer' }}>{btn}</span>
    </div>
  );
}

// ───────── 左 · 文本素材库 ─────────
function SourcePane({ sources, active, onPick, paste, setPaste, onDistill, srcStatus, onRetry }) {
  return (
    <aside style={{ display: 'flex', flexDirection: 'column', minHeight: 0, background: 'rgba(241,234,217,0.5)' }}>
      <div style={{ padding: '14px 16px 10px', borderBottom: '1px solid var(--line-soft)' }}>
        <div className="mono" style={{ fontSize: 9, letterSpacing: '.14em', color: 'var(--ink-3)', marginBottom: 9 }}>粘贴文本 · 即时提炼</div>
        <textarea value={paste} onChange={e => setPaste(e.target.value)} rows={3} placeholder="粘贴研报段落 / 复盘笔记 / 热帖…&#10;观澜将自动提炼为可验证的经验"
          style={{ width: '100%', boxSizing: 'border-box', border: '1px solid var(--line)', borderRadius: 9, padding: '9px 11px', fontFamily: 'var(--serif)', fontSize: 12, lineHeight: 1.6, color: 'var(--ink)', background: 'var(--paper)', outline: 'none', resize: 'none' }} />
        <span onClick={onDistill} className="serif" style={{ display: 'block', textAlign: 'center', marginTop: 8, fontSize: 12, color: paste.trim() ? 'var(--paper)' : 'var(--ink-3)', background: paste.trim() ? 'var(--yin)' : 'rgba(28,24,20,0.05)', borderRadius: 8, padding: '8px', cursor: paste.trim() ? 'pointer' : 'default' }}>瀾 提炼为经验</span>
      </div>
      <div style={{ padding: '12px 16px 6px', display: 'flex', justifyContent: 'space-between' }}>
        <span className="mono" style={{ fontSize: 9, letterSpacing: '.14em', color: 'var(--ink-3)' }}>素材库 · 待提炼</span>
        <span className="mono" style={{ fontSize: 9, color: 'var(--ink-3)' }}>{sources.length}</span>
      </div>
      {(srcStatus === 'error' || srcStatus === 'empty') && (
        <div className="mono" style={{ margin: '0 12px 8px', padding: '6px 10px', borderRadius: 8, fontSize: 9.5, lineHeight: 1.7, background: 'rgba(168,57,45,0.07)', color: 'var(--yin)', border: '1px dashed var(--yin)' }}>
          {srcStatus === 'error' ? '⚠ 素材库加载失败 · 以下为示例数据' : '素材库暂无真素材 · 以下为示例数据(粘贴文本或从对话沉淀)'}
          {srcStatus === 'error' && (
            <span onClick={onRetry} style={{ marginLeft: 8, cursor: 'pointer', borderBottom: '1px solid var(--yin)' }}>重试</span>
          )}
        </div>
      )}
      <div style={{ flex: 1, overflowY: 'auto', padding: '0 12px 16px' }}>
        {sources.map(s => {
          const on = s.id === active;
          return (
            <div key={s.id} onClick={() => onPick(s.id)}
              style={{ border: '1px solid ' + (on ? 'var(--yin)' : 'var(--line)'), borderRadius: 10, background: on ? 'rgba(168,57,45,0.05)' : 'var(--paper)', padding: '11px 12px', marginBottom: 8, cursor: 'pointer', boxShadow: on ? '0 2px 10px rgba(28,24,20,0.08)' : '0 1px 3px rgba(28,24,20,0.04)', transition: 'all .2s' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 7, marginBottom: 6 }}>
                <span className="mono" style={{ fontSize: 8.5, color: 'var(--paper)', background: KIND[s.kind].c, borderRadius: 4, padding: '1px 6px' }}>{s.kind}</span>
                <span className="serif" style={{ fontSize: 13, fontWeight: 600, color: 'var(--ink)', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{s.title}</span>
              </div>
              <div className="serif" style={{ fontSize: 11, color: 'var(--ink-2)', lineHeight: 1.55, marginBottom: 8, display: '-webkit-box', WebkitLineClamp: 2, WebkitBoxOrient: 'vertical', overflow: 'hidden' }}>{s.seg.find(x => x.cite)?.t}</div>
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                <span className="mono" style={{ fontSize: 9, color: 'var(--ink-3)' }}>{s.from} · {s.time}</span>
                <span className="mono" style={{ fontSize: 9, color: on ? 'var(--yin)' : 'var(--ink-3)' }}>{on ? '提炼中 ▸' : s.tag}</span>
              </div>
            </div>
          );
        })}
      </div>
    </aside>
  );
}

// ───────── 右 · 因子组合 + 知识库（紧凑可扩展） ─────────
function RightPane({ combos, kb }) {
  const [open, setOpen] = useState(0);          // 展开的组合 index，-1 全收起
  const [filter, setFilter] = useState('全部');  // 知识库结论筛选
  const [tagF, setTagF] = useState(null);        // 类别/标签筛选
  const [q, setQ] = useState('');                // 关键词搜索（组合 + 知识库）
  const qq = q.trim();
  const mq = (s) => qq && s && s.toLowerCase().includes(qq.toLowerCase());

  const sortedCombos = [...combos]
    .filter(c => !qq || mq(c.name) || mq(c.kind) || (c.members || []).some(mq))
    .sort((a, b) => (parseFloat(b.ic) || -1) - (parseFloat(a.ic) || -1));
  const counts = { 全部: kb.length, 通过: kb.filter(e => e.verdict === '通过').length, 存疑: kb.filter(e => e.verdict === '存疑').length };
  const shownKb = [...kb]
    .filter(e => filter === '全部' || e.verdict === filter)
    .filter(e => !tagF || (e.tags || []).includes(tagF) || e.cat === tagF)
    .filter(e => !qq || mq(e.name) || mq(e.cat) || (e.tags || []).some(mq))
    .sort((a, b) => b.conf - a.conf);
  const CAT_ORDER = ['价量', '资金', '基本面', '风格', '情绪', '另类', '其他'];
  const groups = CAT_ORDER.map(c => [c, shownKb.filter(e => (e.cat || '其他') === c)]).filter(([, arr]) => arr.length);

  return (
    <aside style={{ display: 'flex', flexDirection: 'column', minHeight: 0, background: 'rgba(241,234,217,0.5)' }}>
      {/* 关键词搜索 — 同时过滤组合与知识库 */}
      <div style={{ padding: '12px 14px 10px', borderBottom: '1px solid var(--line-soft)' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, border: '1px solid var(--line)', borderRadius: 9, background: 'var(--paper)', padding: '7px 11px' }}>
          <span className="mono" style={{ fontSize: 11, color: 'var(--ink-3)', flexShrink: 0 }}>搜</span>
          <input value={q} onChange={e => setQ(e.target.value)} placeholder="按名称 / 标签 / 类别搜索…"
            style={{ flex: 1, minWidth: 0, border: 'none', outline: 'none', background: 'transparent', fontFamily: 'var(--sans)', fontSize: 12, color: 'var(--ink)' }} />
          {qq && <span onClick={() => setQ('')} style={{ fontSize: 13, color: 'var(--ink-3)', cursor: 'pointer', flexShrink: 0 }}>✕</span>}
        </div>
      </div>

      {/* 新因子组合 — 折叠列表 */}
      <div style={{ flex: '0 0 auto', maxHeight: '44%', display: 'flex', flexDirection: 'column', borderBottom: '1px solid var(--line)', minHeight: 0 }}>
        <div style={{ padding: '12px 16px 8px', display: 'flex', justifyContent: 'space-between', alignItems: 'baseline' }}>
          <span className="serif" style={{ fontSize: 13, fontWeight: 600, color: 'var(--ink)' }}>新因子组合</span>
          <span className="mono" style={{ fontSize: 8.5, color: 'var(--ink-3)' }}>{combos.length ? `agent 生成 · ${combos.length} · IC 高→低` : 'agent 生成'}</span>
        </div>
        <div style={{ flex: 1, overflowY: 'auto', padding: '0 12px 12px' }}>
          {sortedCombos.length === 0 ? (
            <div className="serif" style={{ fontSize: 11, color: 'var(--ink-3)', lineHeight: 1.7, border: '1px dashed var(--line)', borderRadius: 9, padding: '14px', textAlign: 'center' }}>{combos.length ? '无匹配组合' : '「用」阶段生成因子组合后，将在此按 IC 排序列出。'}</div>
          ) : sortedCombos.map((c, i) => {
            const isOpen = open === i;
            return (
              <div key={c.name + i} style={{ border: '1px solid ' + (isOpen ? 'var(--zhu-soft)' : 'var(--line)'), borderRadius: 9, background: 'var(--paper)', marginBottom: 6, overflow: 'hidden', animation: `fadeIn .35s ease ${i * 0.08}s both` }}>
                <div onClick={() => setOpen(isOpen ? -1 : i)} style={{ display: 'flex', alignItems: 'center', gap: 7, padding: '8px 11px', cursor: 'pointer' }}>
                  <span className="mono" style={{ fontSize: 9, color: 'var(--ink-3)', width: 8, flexShrink: 0, transform: isOpen ? 'rotate(90deg)' : 'none', transition: 'transform .2s' }}>▸</span>
                  <span className="serif" style={{ fontSize: 12, fontWeight: 600, color: 'var(--ink)', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis', flex: 1, minWidth: 0 }}>{c.name}</span>
                  {c.kind && <span className="mono" style={{ fontSize: 8, color: 'var(--zhu)', border: '1px solid var(--zhu-soft)', borderRadius: 4, padding: '1px 5px', flexShrink: 0 }}>{c.kind}</span>}
                  <span className="mono" style={{ fontSize: 8.5, color: 'var(--ink-3)', flexShrink: 0 }}>{c.members.length} 元</span>
                  <span className="mono" style={{ fontSize: 10, color: 'var(--zhu)', fontWeight: 600, flexShrink: 0 }}>IC {c.ic}</span>
                </div>
                {isOpen && (
                  <div style={{ padding: '0 11px 11px', animation: 'fadeIn .25s ease' }}>
                    <div style={{ display: 'flex', gap: 5, flexWrap: 'wrap', marginBottom: 8 }}>
                      {c.members.map(m => <span key={m} className="mono" style={{ fontSize: 8.5, color: 'var(--ink-2)', background: 'rgba(28,24,20,0.05)', borderRadius: 4, padding: '2px 6px' }}>{m}</span>)}
                      {c.corr !== '—' && <span className="mono" style={{ fontSize: 8.5, color: 'var(--ink-3)', border: '1px solid var(--line)', borderRadius: 4, padding: '2px 6px' }}>ρ {c.corr}</span>}
                    </div>
                    <code className="mono" style={{ display: 'block', fontSize: 9.5, color: 'var(--ink-1)', background: 'rgba(28,24,20,0.04)', borderRadius: 6, padding: '7px 9px', marginBottom: 8, lineHeight: 1.45, wordBreak: 'break-all' }}>{c.expr}</code>
                    <p className="serif" style={{ fontSize: 10.5, lineHeight: 1.6, color: 'var(--ink-2)', margin: '0 0 9px', textWrap: 'pretty' }}>{c.note}</p>
                    <a href="../factor/观澜 · AI 工作流.html" className="serif" style={{ display: 'block', textAlign: 'center', fontSize: 11, color: 'var(--paper)', background: 'var(--yin)', borderRadius: 6, padding: '6px', textDecoration: 'none' }}>瀾 据此搭建工作流 →</a>
                  </div>
                )}
              </div>
            );
          })}
        </div>
      </div>

      {/* 经验知识库 — 分类归纳 + 标签 */}
      <div style={{ flex: 1, display: 'flex', flexDirection: 'column', minHeight: 0 }}>
        <div style={{ padding: '12px 16px 8px' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginBottom: 8 }}>
            <span className="serif" style={{ fontSize: 13, fontWeight: 600, color: 'var(--ink)' }}>经验知识库</span>
            <span className="mono" style={{ fontSize: 8.5, color: 'var(--ink-3)' }}>按类别 · 置信度高→低</span>
          </div>
          <div style={{ display: 'flex', gap: 6 }}>
            {['全部', '通过', '存疑'].map(f => (
              <span key={f} onClick={() => setFilter(f)} className="mono" style={{ fontSize: 9.5, cursor: 'pointer', padding: '3px 9px', borderRadius: 13,
                color: filter === f ? 'var(--paper)' : 'var(--ink-2)', background: filter === f ? 'var(--ink-1)' : 'transparent', border: '1px solid ' + (filter === f ? 'var(--ink-1)' : 'var(--line)') }}>
                {f} {counts[f]}
              </span>
            ))}
            {tagF && (
              <span onClick={() => setTagF(null)} className="mono" style={{ marginLeft: 'auto', fontSize: 9.5, cursor: 'pointer', padding: '3px 9px', borderRadius: 13, color: 'var(--paper)', background: 'var(--yin)', display: 'inline-flex', alignItems: 'center', gap: 5 }}>
                # {tagF} <span style={{ fontSize: 11 }}>✕</span>
              </span>
            )}
          </div>
        </div>
        <div style={{ flex: 1, overflowY: 'auto', padding: '0 12px 16px' }}>
          {groups.length === 0 ? (
            <div className="serif" style={{ fontSize: 11, color: 'var(--ink-3)', textAlign: 'center', padding: '14px' }}>无匹配经验</div>
          ) : groups.map(([cat, arr]) => (
            <div key={cat} style={{ marginBottom: 6 }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 7, padding: '7px 4px 5px' }}>
                <span onClick={() => setTagF(tagF === cat ? null : cat)} className="mono" style={{ fontSize: 9, letterSpacing: '.1em', color: tagF === cat ? 'var(--yin)' : 'var(--ink-2)', cursor: 'pointer', fontWeight: 600 }}>{cat}</span>
                <span style={{ flex: 1, height: 1, background: 'var(--line-soft)' }} />
                <span className="mono" style={{ fontSize: 8.5, color: 'var(--ink-3)' }}>{arr.length}</span>
              </div>
              {arr.map((e, i) => {
                const ver = VERDICT[e.verdict];
                return (
                  <div key={e.name + i}
                    style={{ background: e.fresh ? 'rgba(74,107,92,0.06)' : 'var(--paper)', border: '1px solid ' + (e.fresh ? 'var(--dai-soft)' : 'var(--line)'), borderLeft: '2px solid ' + ver.c, borderRadius: '4px 8px 8px 4px', padding: '8px 11px', marginBottom: 6, animation: e.fresh ? 'fadeIn .5s ease' : 'none' }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 7 }}>
                      <span style={{ width: 6, height: 6, borderRadius: '50%', background: ver.c, flexShrink: 0 }} />
                      <span className="serif" style={{ fontSize: 12, fontWeight: 600, color: 'var(--ink)', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis', flex: 1, minWidth: 0 }}>{e.name}</span>
                      {e.fresh && <span className="mono" style={{ fontSize: 7, color: 'var(--paper)', background: 'var(--dai)', borderRadius: 3, padding: '1px 4px', flexShrink: 0 }}>NEW</span>}
                      <span className="mono" style={{ fontSize: 9.5, color: 'var(--ink-2)', flexShrink: 0 }}>IC {e.ic}</span>
                    </div>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 7, marginTop: 6 }}>
                      <div style={{ flex: 1, height: 3, borderRadius: 2, background: 'rgba(28,24,20,0.08)', overflow: 'hidden' }}>
                        <div style={{ width: e.conf + '%', height: '100%', background: ver.c }} />
                      </div>
                      <span className="mono" style={{ fontSize: 8.5, color: 'var(--ink-3)', flexShrink: 0 }}>{e.conf} · {e.src}</span>
                    </div>
                    <div style={{ display: 'flex', gap: 5, marginTop: 7, flexWrap: 'wrap' }}>
                      {(e.tags || []).map(t => (
                        <span key={t} onClick={() => setTagF(tagF === t ? null : t)} className="mono"
                          style={{ fontSize: 8, cursor: 'pointer', borderRadius: 4, padding: '1px 6px', color: tagF === t ? 'var(--paper)' : 'var(--ink-3)', background: tagF === t ? 'var(--yin)' : 'transparent', border: '1px solid ' + (tagF === t ? 'var(--yin)' : 'var(--line)') }}># {t}</span>
                      ))}
                    </div>
                  </div>
                );
              })}
            </div>
          ))}
        </div>
      </div>
    </aside>
  );
}

window.ValidationApp = ValidationApp;
