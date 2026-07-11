// 观澜 · 落子 — 数据内核 (纯 JS, 无 JSX)
// 生成 K 线 → 四席位策略落子 → 收益曲线/Sharpe/胜率/盈亏比 → 多标的舰队
// 全部 window 暴露, 供 chart / panels / fleet / app 使用.

// ───────── 确定性随机 ─────────
function mulberry32(a) {
  return function () {
    a |= 0; a = (a + 0x6D2B79F5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

// ───────── K 线生成 (分相位, 让各策略都有可咬合的形态) ─────────
// phases: [{len, drift(每日%), vol(波动%), volBias(量能倾向)}]
function genBars(seed, opts) {
  const rnd = mulberry32(seed);
  const o = opts || {};
  const phases = o.phases || [
    { len: 24, drift: -0.006, vol: 0.018, volBias: 0.9 },   // 阴跌 缩量
    { len: 30, drift: 0.009, vol: 0.020, volBias: 1.15 },   // 修复 上行
    { len: 16, drift: -0.005, vol: 0.022, volBias: 1.0 },   // 回调
    { len: 26, drift: 0.012, vol: 0.024, volBias: 1.35 },   // 主升 (含事件)
    { len: 24, drift: -0.003, vol: 0.026, volBias: 1.55 },  // 高位放量 派发
  ];
  const eventBar = o.eventBar != null ? o.eventBar : 72;    // 业绩超预期跳空
  let price = o.start || 200;
  const bars = [];
  let day = 0;
  const baseVol = o.baseVol || 1.0;
  const startDate = new Date(2025, 1, 10);  // 2025-02-10 起
  for (let p = 0; p < phases.length; p++) {
    const ph = phases[p];
    for (let k = 0; k < ph.len; k++) {
      const prev = price;
      let ret = ph.drift + (rnd() - 0.5) * 2 * ph.vol;
      if (day === eventBar) ret = 0.072 + rnd() * 0.02;       // 事件跳空大涨
      if (day === eventBar - 1) ret = -0.01 - rnd() * 0.01;   // 事件前缩量回踩
      price = Math.max(8, prev * (1 + ret));
      const o_ = prev * (1 + (rnd() - 0.5) * ph.vol * 0.6);
      const c_ = price;
      const hi = Math.max(o_, c_) * (1 + rnd() * ph.vol * 0.5);
      const lo = Math.min(o_, c_) * (1 - rnd() * ph.vol * 0.5);
      // 量能: 大涨大跌放量, 阴跌缩量
      let vol = baseVol * ph.volBias * (0.7 + rnd() * 0.6) * (1 + Math.abs(ret) * 14);
      if (day === eventBar) vol *= 2.4;
      const d = new Date(startDate); d.setDate(d.getDate() + Math.floor(day * 1.4));
      const mm = String(d.getMonth() + 1).padStart(2, '0');
      const dd = String(d.getDate()).padStart(2, '0');
      bars.push({
        i: day, date: d.getFullYear() + '-' + mm + '-' + dd,
        o: +o_.toFixed(2), c: +c_.toFixed(2), h: +hi.toFixed(2), l: +lo.toFixed(2),
        v: +vol.toFixed(2), event: day === eventBar,
      });
      day++;
    }
  }
  return bars;
}

// ───────── 指标 helper ─────────
function sma(bars, period, key, idx) {
  if (idx < period - 1) return null;
  let s = 0; for (let k = idx - period + 1; k <= idx; k++) s += bars[k][key];
  return s / period;
}
function ret5(bars, idx) {
  if (idx < 5) return 0;
  return bars[idx].c / bars[idx - 5].c - 1;
}
function volMA(bars, period, idx) {
  if (idx < period) return bars[idx].v;
  let s = 0; for (let k = idx - period + 1; k <= idx; k++) s += bars[k].v;
  return s / period;
}
function localMax(bars, win, idx) {
  let m = -Infinity; for (let k = Math.max(0, idx - win); k <= idx; k++) m = Math.max(m, bars[k].h);
  return m;
}

// ───────── 价格行为:方法论默认模板 + 价量几何特征(price_action.py 的 JS 镜像)─────────
// 契约:与 guanlan_v2/seats/price_action.py 同公式/枚举,改一边必同步另一边。
// PIT:只用 bars[0..idx](≤决策 bar);follow 仅 prev→current 向后看,不取未来。
window.LZ_PA_METHOD_DEFAULT =
  '价格行为读法(A股·做多为主):\n' +
  '1. 趋势 vs 区间:连续同向趋势棒(实体大、影线短、收于端部)= 趋势;互相重叠、影线长、收于中部 = 区间/震荡。趋势中顺势,区间中高抛低吸或观望。\n' +
  '2. 突破与回踩:放量突破前高(实体强、收于上沿)后,优先等第一次缩量回踩不破前高/均线企稳再进,胜率高于追突破当根。突破后迅速收回、留长上影 = 假突破,警惕。\n' +
  '3. 信号棒 + 跟随确认:孤立一根强棒不够,要看其后是否被同向棒跟随确认;无跟随、被反向吞没 = 信号失效。\n' +
  '4. 两腿回调:上升趋势中的回调常走两腿,第二腿缩量不破关键支撑后的转强棒,是较稳的右侧买点。\n' +
  '5. 位置感:同样形态在低位/超跌区比在高位/拥挤区可靠;高位放量滞涨、长上影、量价背离 = 退潮信号,降权或止盈。\n' +
  '6. A股 特有口径:T+1 当日买入次日才能卖,需为隔夜留余地;涨停封板≠可任意买卖(流动性骤降),涨停打开放量要警惕,跌停同理;ST 股 ±5% 幅度小、波动定义不同;不做空,只在做多方向取信号,看空时以「观望/减仓」表达。\n' +
  '几何特征是确定性事实(本席已附),本读法只是推理框架,不替代证据;证据不足时给「观望」。';

function _paRnd(x, p) { if (x == null || !isFinite(x)) return null; const m = Math.pow(10, p == null ? 3 : p); return Math.round(x * m) / m; }
function _paBoardLimit(code, name) {
  if (String(name || '').toUpperCase().replace(/\s/g, '').indexOf('ST') >= 0) return 0.05;
  const d = String(code || '').replace(/\D/g, '');
  if (d.slice(0, 3) === '688' || d.slice(0, 3) === '300') return 0.20;
  if ((d.slice(0, 1) === '8' || d.slice(0, 1) === '4') || String(code || '').toUpperCase().indexOf('BJ') === 0) return 0.30;
  return 0.10;
}
function _paBarType(o, h, l, c, ph, pl) {
  const rng = h - l;
  if (rng <= 0) return '平';
  if (ph != null && pl != null) {
    if (h <= ph && l >= pl) return '内含bar';
    if (h >= ph && l <= pl) return c >= o ? '外包阳' : '外包阴';
  }
  const body = Math.abs(c - o) / rng;
  if (body < 0.1) return '十字';
  if (body >= 0.5) return c > o ? '趋势阳' : '趋势阴';
  return c >= o ? '小阳' : '小阴';
}
function paFeatures(bars, idx, code, name) {
  if (!bars || idx == null || idx < 0 || idx >= bars.length) return {};
  // perf:每次重建 0..idx 切片;scanSeat 逐 bar 调 → O(n²),当前 bar 数(≤250 日/≤80 分钟)可忽略
  const o = [], h = [], l = [], c = [], v = [];
  for (let k = 0; k <= idx; k++) { const b = bars[k]; o.push(+b.o); h.push(+b.h); l.push(+b.l); c.push(+b.c); v.push(+b.v); }
  const n = c.length, i = n - 1;
  const rng = h[i] - l[i];
  const prevClose = i >= 1 ? c[i - 1] : null;
  const ph = i >= 1 ? h[i - 1] : null, pl = i >= 1 ? l[i - 1] : null;
  const body = rng > 0 ? _paRnd(Math.abs(c[i] - o[i]) / rng) : null;
  const upper = rng > 0 ? _paRnd((h[i] - Math.max(o[i], c[i])) / rng) : null;
  const lower = rng > 0 ? _paRnd((Math.min(o[i], c[i]) - l[i]) / rng) : null;
  const closePos = rng > 0 ? _paRnd((c[i] - l[i]) / rng) : null;
  let rangeAtr = null;
  if (n >= 15) {
    let s = 0; for (let k = n - 14; k < n; k++) s += Math.max(h[k] - l[k], Math.abs(h[k] - c[k - 1]), Math.abs(l[k] - c[k - 1]));
    const atr = s / 14; rangeAtr = atr > 0 ? _paRnd(rng / atr) : null;
  }
  let ema20Rel = null;
  if (n >= 20) {
    const kf = 2 / 21; let ema = c[0];
    for (let k = 1; k < n; k++) ema = c[k] * kf + ema * (1 - kf);
    ema20Rel = ema !== 0 ? _paRnd((c[i] - ema) / ema) : null;
  }
  const barType = _paBarType(o[i], h[i], l[i], c[i], ph, pl);
  let breakout = null;
  if (i >= 5) {
    const ph5 = Math.max.apply(null, h.slice(i - 5, i)), pl5 = Math.min.apply(null, l.slice(i - 5, i));
    breakout = h[i] > ph5 ? '突破前5高' : (l[i] < pl5 ? '跌破前5低' : '区间内');
  }
  let insideStreak = 0;
  for (let k = i; k >= 1; k--) { if (h[k] <= h[k - 1] && l[k] >= l[k - 1]) insideStreak++; else break; }
  let volRatio = null;
  if (i >= 5) { const base = (v[i - 5] + v[i - 4] + v[i - 3] + v[i - 2] + v[i - 1]) / 5; volRatio = base > 0 ? _paRnd(v[i] / base, 2) : null; }
  let limit = null, gap = null;
  // NaN 守卫(与后端 price_action.py 同口径):prev_close/今收均有限才判,否则诚实 null
  if (prevClose != null && prevClose === prevClose && prevClose !== 0 && c[i] === c[i]) {
    const L = _paBoardLimit(code, name), pct = (c[i] - prevClose) / prevClose;
    limit = pct >= L - 0.003 ? '涨停' : pct >= 0.7 * L ? '接近涨停' : pct <= -(L - 0.003) ? '跌停' : pct <= -0.7 * L ? '接近跌停' : '正常';
    if (o[i] === o[i]) gap = o[i] > prevClose * 1.002 ? '高开' : o[i] < prevClose * 0.998 ? '低开' : '无';
  }
  let follow = null;
  if (i >= 1) {
    const pph = i >= 2 ? h[i - 2] : null, ppl = i >= 2 ? l[i - 2] : null;
    const pbt = _paBarType(o[i - 1], h[i - 1], l[i - 1], c[i - 1], pph, ppl);
    if (pbt === '趋势阳' || pbt === '外包阳') { if (c[i] > c[i - 1] && c[i] > o[i]) follow = '已确认(多)'; else if (c[i] < l[i - 1]) follow = '转弱'; }
    else if (pbt === '趋势阴' || pbt === '外包阴') { if (c[i] < c[i - 1]) follow = '已确认(空)'; else if (c[i] > h[i - 1]) follow = '转弱'; }
  }
  const recent = [1, 2, 3].map(function (back) {
    const k = i - back;
    if (k < 0) return null;
    return _paBarType(o[k], h[k], l[k], c[k], k >= 1 ? h[k - 1] : null, k >= 1 ? l[k - 1] : null);
  });
  return { date: (bars[idx] && bars[idx].date) || null, bar_type: barType, body: body, upper_wick: upper, lower_wick: lower, close_pos: closePos, range_atr: rangeAtr, ema20_rel: ema20Rel, breakout: breakout, inside_streak: insideStreak, vol_ratio: volRatio, limit: limit, gap: gap, follow: follow, recent: recent };
}
function renderPaNote(feat) {
  if (!feat || !feat.bar_type) return '';
  const f = function (x) { return x == null ? '—' : x; };
  const bits = [feat.bar_type, '实体' + f(feat.body), '收盘位' + f(feat.close_pos)];
  if (feat.breakout && feat.breakout !== '区间内') bits.push(feat.breakout);
  if (feat.vol_ratio != null) bits.push('量比' + feat.vol_ratio + '×');
  if (feat.limit && feat.limit !== '正常') bits.push(feat.limit);
  if (feat.gap && feat.gap !== '无') bits.push(feat.gap);
  if (feat.follow) bits.push(feat.follow);
  return bits.join('·');
}
window.lzPaFeatures = paFeatures;
window.lzRenderPaNote = renderPaNote;

// ───────── 席位定义 ─────────
// 四席全量定义保留(第3期再泛化为用户自命名策略实例);为简洁当前只暴露动量席一席。
const SEATS_ALL = [
  { id: 'reversal', cn: '反转席', en: 'Reversal', color: 'var(--zhu)', glyph: '反',
    creed: '超跌缩量企稳即落子,搏短线反弹', card: '缩量反转' },
  { id: 'momentum', cn: '动量席', en: 'Momentum', color: 'var(--jin)', glyph: '动',
    creed: '突破均线、量价齐升则顺势加仓', card: '北向资金领先' },
  { id: 'event', cn: '事件驱动席', en: 'Event', color: '#3f6f8a', glyph: '事',
    creed: '业绩超预期后博 60 日漂移', card: '业绩漂移 PEAD' },
  { id: 'risk', cn: '风控席', en: 'Risk', color: 'var(--dai)', glyph: '险',
    creed: '高位放量滞涨即减仓止盈,守住回撤', card: '高位放量滞涨退潮' },
];
// 为简洁暂只留动量席;改这里(或加回其它 id)即可调整在场席位,全模块经 window.LZ_SEATS 自动跟随。
const LZ_KEPT_SEATS = ['momentum'];
const SEATS = SEATS_ALL.filter(s => LZ_KEPT_SEATS.indexOf(s.id) >= 0);

// ───────── 模板库(第3期:策略实例的信号引擎 = 模板进场规则 + 用户时钟出场)─────────
const LZ_TEMPLATES = {
  momentum: { cn: '动量突破', glyph: '动', color: 'var(--jin)', creed: '突破均线、量价齐升则顺势加仓', card: '北向资金领先',
    clock: { execTF: 'day', decisionFreq: 'hourly', maxHold: 30, stopLoss: 0.08, takeProfit: 0.18 } },
  reversal: { cn: '超跌反转', glyph: '反', color: 'var(--zhu)', creed: '超跌缩量企稳即落子,搏短线反弹', card: '缩量反转',
    clock: { execTF: 'day', decisionFreq: 'daily', maxHold: 13, stopLoss: 0.05, takeProfit: 0.11 } },
  event: { cn: '事件驱动', glyph: '事', color: '#3f6f8a', creed: '业绩超预期后博 60 日漂移', card: '业绩漂移 PEAD',
    clock: { execTF: 'day', decisionFreq: 'daily', maxHold: 22, stopLoss: 0.09, takeProfit: 0.26 } },
};
const LZ_TEMPLATE_IDS = ['momentum', 'reversal', 'event'];   // 风控本期不作独立模板(范围外)

// ───────── 策略实例(StrategyInstance)= GL type:'strategy' 实体 ─────────
function _normClock(c, tpl) {
  // 缺省随策略所属模板走(审计 M5): 此前恒取 momentum.clock, 反转/事件策略缺字段时
  // 会被动量的 止损0.08/持有30日/hourly 污染。tpl 不传时仍退 momentum(向后兼容)。
  const d = (tpl && tpl.clock) || LZ_TEMPLATES.momentum.clock;
  c = c || {};
  return {
    execTF: c.execTF === '5min' ? '5min' : 'day',
    decisionFreq: c.decisionFreq || d.decisionFreq || 'hourly',
    maxHold: (c.maxHold != null && isFinite(+c.maxHold)) ? +c.maxHold : d.maxHold,
    stopLoss: (c.stopLoss != null && isFinite(+c.stopLoss)) ? +c.stopLoss : d.stopLoss,
    takeProfit: (c.takeProfit != null && isFinite(+c.takeProfit)) ? +c.takeProfit : d.takeProfit,
  };
}
function strategyList() { return (window.GL ? window.GL.all('strategy') : []); }
function strategyGet(id) { return (window.GL ? window.GL.get(id) : null); }
function strategyForCode(code) {
  return strategyList().filter(s => !s.bind || s.bind.length === 0 || s.bind.indexOf(code) >= 0);
}
function strategyColor(id) {
  const s = strategyGet(id);
  if (s && s.color) return s.color;
  if (s && s.template && LZ_TEMPLATES[s.template]) return LZ_TEMPLATES[s.template].color;
  const seat = (SEATS_ALL || []).find(x => x.id === id);
  return seat ? seat.color : 'var(--ink-2)';
}
function strategySave(o) {
  if (!window.GL) return null;
  const tmpl = LZ_TEMPLATE_IDS.indexOf(o.template) >= 0 ? o.template : 'momentum';
  const td = LZ_TEMPLATES[tmpl];
  const obj = {
    id: o.id || ('strat_' + Date.now().toString(36) + Math.floor(Math.random() * 1e4).toString(36)),
    type: 'strategy', name: o.name || td.cn, template: tmpl,
    refs: Array.isArray(o.refs) ? o.refs : [],
    clock: _normClock(o.clock || td.clock, td),
    bind: Array.isArray(o.bind) ? o.bind.slice() : [],
    color: o.color || td.color, glyph: o.glyph || td.glyph,
    creed: (o.creed != null && String(o.creed).trim()) ? String(o.creed).trim() : (td.creed || ''),
    // P3:因子权重 w(0~1)。0=纯LLM;>0 时 /seats/decide 按 (1-w)·LLM分+w·vintage因子z分 混入决策方向。缺省 0(诚实退化)。
    w: (o.w != null && isFinite(+o.w)) ? Math.max(0, Math.min(1, +o.w)) : 0,
    // 价格行为:pa 开关(默认关)+ 可编辑方法论(空串=用默认模板)
    pa: o.pa === true,
    paMethod: (o.paMethod != null) ? String(o.paMethod).slice(0, 8000) : '',
  };
  window.GL.put(obj);
  return obj.id;
}
function strategyDelete(id) { if (window.GL) window.GL.remove(id); }
function seedDefaultStrategy() {
  if (!window.GL) return;
  if (strategyList().length > 0) return;
  // refs 留空:旧版配 card_north/fa_north 是 demo 假料(北向数据 2024-08 停披),假证据不进默认研判(互通审计 P0①)
  strategySave({ name: '动量 · 默认', template: 'momentum', bind: [], refs: [],
    clock: LZ_TEMPLATES.momentum.clock });
}
// 一次性迁移:旧 seed 给「动量 · 默认」塞的 card_north/fa_north(北向死数据)剥掉。
// 只动未改名的默认策略;用户自己改过名/自建的策略一概不碰(物料徽章+名称后缀会显形示例料)。
function _stripDeadDemoRecipe() {
  if (!window.GL) return;
  strategyList().forEach(s => {
    if (s.name !== '动量 · 默认' || s.template !== 'momentum') return;
    const refs = s.refs || [];
    const next = refs.filter(r => r !== 'card_north' && r !== 'fa_north');
    if (next.length !== refs.length) strategySave(Object.assign({}, s, { refs: next }));
  });
}
// 去重「动量 · 默认」自动默认:seedDefaultStrategy 在模块加载时**同步**建一个,而 guanlan-bus 的后端持久化
//   (P2-C /archive)经 setTimeout 异步回填补入上次会话存的同名默认 → 新鲜 profile(localStorage 空 + 后端有)下
//   两个无绑定同名默认并存(回测历史按 strategy_id 过滤会张冠李戴)。保留 **ts 最小**(最早=带回测历史的持久化那个),
//   删其余。仅针对未改名(name='动量 · 默认')未绑定(bind 空)的自动默认 → 用户改名/绑票的策略名或 bind 不同,不误删。
//   回填走 persist→emit 通知,故挂 GL.on 自愈;折叠后只剩 1 → 再 emit 不再删 → 无环。
function _dedupeDefaultStrategies() {
  if (!window.GL) return;
  const defs = strategyList().filter(s =>
    s && s.template === 'momentum' && s.name === '动量 · 默认' && (!s.bind || s.bind.length === 0));
  if (defs.length <= 1) return;
  defs.sort((a, b) => (a.ts || 0) - (b.ts || 0));   // 最早在前
  defs.slice(1).forEach(s => strategyDelete(s.id));  // 删较新副本,留最早(持久化·带历史)
}
// 把策略实例自己配的 refs 解析成 {cards,research,factors};区别于 seatCard/seatResearch(查老 seat_<id> 实体)。
// 配方因子(factors)仅供喂 LLM 研判参考,不参与任何确定性计算(红线:不冒充因子回测)。
function recipeForStrategy(stratId) {
  const s = strategyGet(stratId);
  const empty = { cards: [], research: [], factors: [] };
  if (!s || !window.GL) return empty;
  const cards = [], research = [], factors = [], seen = new Set();
  (s.refs || []).forEach(rid => {
    const a = window.GL.get(rid);
    if (!a || seen.has(rid)) return; seen.add(rid);
    if (a.demo) return;   // demo 假料退出真路径:不进配方、不喂 decide(用户红线「没接入的假东西删掉」)
    const dm = (x) => (x && x.demo ? '(示例)' : '');   // 保留标注函数(内层 research 若混入 demo 仍显形;现已 skip)
    if (a.type === 'card') {
      cards.push({ name: (a.title || a.id) + dm(a), insight: a.insight || a.verdict || '',
        verdict: a.verdict || null, conf: (a.conf != null ? a.conf : null), ic: (a.ic || null) });
      (a.refs || []).forEach(r2 => {                       // card 内层 research 同口径带上
        const b = window.GL.get(r2);
        if (b && b.type === 'research' && !seen.has(r2)) { seen.add(r2); research.push({ title: b.title + dm(b), from: b.from || '', path: b.path || null }); }
      });
    } else if (a.type === 'research') research.push({ title: a.title + dm(a), from: a.from || '', path: a.path || null });   // path:研报全文落点,P1⑤ 喂正文用
    else if (a.type === 'factor') factors.push({ id: a.id, name: (a.title || a.id) + dm(a), ic: (a.ic || ''), expr: (a.expr || '') });   // +id 供后端 P2 vintage IC resolve
  });
  return { cards, research, factors };
}

// ───────── 证据模板 (研报观点 / 经验卡 / regime) ─────────
const REGIME_BY_PHASE = ['震荡偏弱', '修复企稳', '震荡', '主升·情绪高涨', '高位派发'];
function regimeAt(bars, idx) {
  // 由相位粗分 (与 genBars phases 对齐: 24/30/16/26/24)
  const cuts = [24, 54, 70, 96, 120];
  for (let p = 0; p < cuts.length; p++) if (idx < cuts[p]) return REGIME_BY_PHASE[p];
  return REGIME_BY_PHASE[REGIME_BY_PHASE.length - 1];
}
const MAINLINE = ['新能源车', '锂电', '储能', '光伏', '消费电子'];

// ⚠ evidenceFor = 「示意 / legacy 合成证据」:combo/FM/LGB/v4 为拍脑袋公式、RESEARCH 写死研报句、card 模板文案、regime 按相位轮转 —— 非任何真模型/真卡/真大盘。
//   仅挂在 scanSeat 启发式决策上占位。**所有消费方在无真值时必须显「示意/未接入」徽章或降级为「—」,绝不冒充真值**:
//   触发因子无 signal→「—」(luozi-panels DecisionCard)、经验卡无真卡→「示意·未引用真卡」、市场状态实盘无真值→「—」+诚实小字。
//   真源:真因子 /watch/signal_pack | /seats/factors、真卡 lzSeatCard、真市况 /watch/market_status、真研判 /seats/decide。
function evidenceFor(seat, bars, idx, isPrimary) {
  const b = bars[idx];
  const regime = regimeAt(bars, idx);
  const ml = MAINLINE[idx % MAINLINE.length];
  const tmpl = seat.template || seat.id;
  // 量化因子 (镜像盯盘台字段)
  const factors = {
    combo: Math.round(40 + (ret5(bars, idx) + 0.1) * 280 + (tmpl === 'momentum' ? 18 : 0)),
    fmCluster: 'c' + (3 + (idx % 5)),
    fmPct: Math.round(35 + (idx % 60)),
    lgbRank: 4 + (idx % 40),
    v4: ['B', 'A-', 'A', 'A+'][Math.min(3, Math.floor((ret5(bars, idx) + 0.06) * 18))] || 'B',
  };
  factors.combo = Math.max(8, Math.min(98, factors.combo));
  const RESEARCH = {
    reversal: ['中信《锂电材料》:Q1 排产环比回暖,左侧布局窗口临近', '招商:估值已回落至历史 18% 分位,具备安全边际'],
    momentum: ['国君策略:北向连续 5 日净买入新能源,板块动量延续', '中金上调评级至「跑赢行业」,目标价上修 14%'],
    event: ['公司业绩预告:归母净利同比 +38%,超 wind 一致预期 11%', '海通:产能利用率回升驱动单季盈利拐点'],
    risk: ['卖方拥挤度报告:该股机构持仓已达 92% 分位,交易拥挤', '量价背离预警:股价新高而成交占比回落'],
  };
  const CARDHINT = {
    reversal: '超跌后缩量企稳,3 日内反转概率显著上升;震荡市最有效。',
    momentum: '北向连续净买入的板块,5 日后相对收益占优;择时+选股双用。',
    event: '业绩超预期后存在约 60 日漂移;事件驱动叠加基本面更稳。',
    risk: '龙头高位放量但涨幅收敛,常是退潮前兆,应降权止盈。',
  };
  return {
    regime, mainline: ml, factors,
    research: RESEARCH[tmpl] || RESEARCH.momentum,
    card: { name: seat.card || (LZ_TEMPLATES[tmpl] && LZ_TEMPLATES[tmpl].card) || tmpl, hint: CARDHINT[tmpl] || CARDHINT.momentum },
  };
}

// ───────── 单策略扫描 → 落子(template 进场 + clock 出场;buy/sell 成对)─────────
function scanSeat(bars, strat, meta) {
  meta = meta || {};
  const tmpl = strat.template || strat.id;                  // 兼容旧 SEATS(id 即模板)
  const sid = strat.id || tmpl;
  const td = LZ_TEMPLATES[tmpl] || LZ_TEMPLATES.momentum;
  const clk = _normClock(strat.clock || td.clock, td);
  const stopPct = clk.stopLoss, takePct = clk.takeProfit, maxHold = clk.maxHold;
  const ds = [];
  const n = bars.length;
  let holding = false, entryIdx = -1, entryPrice = 0;
  const push = (idx, side, conf, size, extra) => {
    const b = bars[idx];
    ds.push(Object.assign({
      seat: sid, idx, date: b.date, side, price: b.c, conf, size,
      stop: side === 'buy' ? +(b.c * (1 - stopPct)).toFixed(2) : null,
      take: side === 'buy' ? +(b.c * (1 + takePct)).toFixed(2) : null,
      ev: evidenceFor(strat, bars, idx, true),
    }, extra || {}));
  };
  // 通用出场:止损 / 止盈 / 最长持有(任一触发即平)
  const exitHit = (i) => bars[i].c <= entryPrice * (1 - stopPct) || bars[i].c >= entryPrice * (1 + takePct) || (i - entryIdx) >= maxHold;
  for (let i = 6; i < n; i++) {
    const ma5 = sma(bars, 5, 'c', i), ma20 = sma(bars, 20, 'c', i);
    const ma5p = sma(bars, 5, 'c', i - 1), ma20p = sma(bars, 20, 'c', i - 1);
    const vm = volMA(bars, 10, i), r5 = ret5(bars, i);
    const g = paFeatures(bars, i, meta.code, meta.name);
    if (tmpl === 'momentum') {
      const cross = ma5 && ma20 && ma5 > ma20 && ma5p <= ma20p;
      const dead = ma5 && ma20 && ma5 < ma20 && ma5p >= ma20p;
      const geoOk = (g.bar_type === '趋势阳' || g.breakout === '突破前5高')
        && (g.close_pos == null || g.close_pos >= 0.55)
        && (g.body == null || g.body >= 0.45)
        && (g.vol_ratio == null || g.vol_ratio >= 1.1)
        && g.limit !== '涨停';
      if (!holding && cross && bars[i].c > ma20 && bars[i].v > vm * 1.05 && geoOk) {
        const bump = Math.min(0.1, Math.max(0, (g.close_pos || 0.5) - 0.5) + Math.max(0, (g.body || 0.5) - 0.5) + Math.max(0, (g.vol_ratio || 1) - 1));
        push(i, 'buy', Math.min(1, 0.7 + Math.min(0.2, r5 * 2) + bump), 0.6, { note: 'MA5 上穿 MA20 · ' + renderPaNote(g) + ',顺势进场。', geo: g });
        holding = true; entryIdx = i; entryPrice = bars[i].c;
      } else if (holding && (dead || exitHit(i))) {
        push(i, 'sell', 0.66, 0, { note: dead ? 'MA5 下破 MA20,动量转弱,撤。' : '触止损/止盈/到期,离场。', geo: g });
        holding = false;
      }
    } else if (tmpl === 'reversal') {
      const turn = bars[i].c > bars[i - 1].c && bars[i - 1].c <= bars[i - 2].c;
      const belowTrend = ma20 && bars[i].c < ma20 * 0.96;
      const noGeoData = g.bar_type == null;   // 仅 paFeatures 返回 {} 才算「无数据」;rng=0 的平/停牌 bar 不当无数据
      const revGeo = (noGeoData
        || (g.lower_wick != null && g.lower_wick >= 0.3)
        || (g.close_pos != null && g.close_pos >= 0.6))
        && g.bar_type !== '趋势阴' && g.bar_type !== '平' && g.limit !== '跌停';
      if (!holding && r5 < -0.05 && belowTrend && bars[i].v < vm * 1.0 && turn && revGeo) {
        push(i, 'buy', 0.62 + Math.min(0.22, -r5), 0.5, { note: '五日超跌 ' + (r5 * 100).toFixed(1) + '% · ' + renderPaNote(g) + ',左侧企稳。', geo: g });
        holding = true; entryIdx = i; entryPrice = bars[i].c;
      } else if (holding && exitHit(i)) {
        const win = bars[i].c >= entryPrice;
        push(i, 'sell', 0.6, 0, { note: win ? '已达反弹目标/到期,落袋。' : '跌破止损/到期,纪律离场。', geo: g });
        holding = false;
      }
    } else if (tmpl === 'event') {
      if (!holding && bars[i].event && (g.gap === '高开' || g.bar_type === '趋势阳')) {
        push(i, 'buy', 0.82, 0.55, { note: '业绩超预期跳空 · ' + renderPaNote(g) + ',博 PEAD 漂移。', geo: g });
        holding = true; entryIdx = i; entryPrice = bars[i].c;
      } else if (holding && exitHit(i)) {
        push(i, 'sell', 0.6, 0, { note: (i - entryIdx) >= maxHold ? '漂移窗口结束,兑现。' : '止损/止盈离场。', geo: g });
        holding = false;
      }
    }
    // 风控 overlay 本期不作独立模板(范围外)
  }
  return ds;
}

// ───────── 收益曲线 / 指标 ─────────
function seatEquity(bars, decisions, seatId) {
  const ds = decisions.filter(d => d.seat === seatId && (d.side === 'buy' || (d.side === 'sell' && !d.warn))).sort((a, b) => a.idx - b.idx);
  const eq = new Array(bars.length);
  let val = 1, pos = 0, size = 0;
  const trades = [];
  let open = null;
  let di = 0;
  for (let k = 0; k < bars.length; k++) {
    if (pos && k > 0) val *= (1 + (bars[k].c / bars[k - 1].c - 1) * size);
    // 处理当日决策
    while (di < ds.length && ds[di].idx === k) {
      const d = ds[di];
      if (d.side === 'buy' && !pos) { pos = 1; size = d.size; open = { entry: bars[k].c, idx: k, conf: d.conf }; }
      else if (d.side === 'sell' && pos) {
        pos = 0; size = 0;
        if (open) { trades.push({ entry: open.entry, exit: bars[k].c, ret: bars[k].c / open.entry - 1, in: open.idx, out: k }); open = null; }
      }
      di++;
    }
    eq[k] = +val.toFixed(4);
  }
  if (open) trades.push({ entry: open.entry, exit: bars[bars.length - 1].c, ret: bars[bars.length - 1].c / open.entry - 1, in: open.idx, out: bars.length - 1, openEnd: true });
  return { eq, trades };
}

function metricsOf(eq, trades, freq) {
  const perDay = freq === '5min' ? 48 : 1;   // A股 4 小时/日 = 48 根 5min;日线 = 1
  const n = eq.length;
  const rets = [];
  for (let k = 1; k < n; k++) rets.push(eq[k] / eq[k - 1] - 1);
  const mean = rets.reduce((a, b) => a + b, 0) / (rets.length || 1);
  const sd = Math.sqrt(rets.reduce((a, b) => a + (b - mean) ** 2, 0) / (rets.length || 1)) || 1e-9;
  const sharpe = (mean / sd) * Math.sqrt(252 * perDay);
  const total = eq[n - 1] - 1;
  const years = ((n / perDay) * 1.4) / 365;  // 先折成交易日数,再按 1.4 自然日/交易日折年
  const annual = Math.pow(eq[n - 1], 1 / Math.max(0.3, years)) - 1;
  let peak = eq[0], mdd = 0;
  for (const v of eq) { peak = Math.max(peak, v); mdd = Math.min(mdd, v / peak - 1); }
  const wins = trades.filter(t => t.ret > 0), losses = trades.filter(t => t.ret <= 0);
  const winRate = trades.length ? wins.length / trades.length : 0;
  const avgWin = wins.length ? wins.reduce((a, b) => a + b.ret, 0) / wins.length : 0;
  const avgLoss = losses.length ? Math.abs(losses.reduce((a, b) => a + b.ret, 0) / losses.length) : 0;
  const plRatio = avgLoss ? avgWin / avgLoss : (avgWin ? 99 : 0);
  return {
    total, annual, sharpe, mdd, winRate, plRatio,
    nTrades: trades.length, nWin: wins.length,
  };
}

// 共识 = 启用席位收益曲线等权组合
function consensusEquity(bars, perSeatEq, activeIds) {
  const eq = new Array(bars.length).fill(0);
  const ids = activeIds.filter(id => perSeatEq[id]);
  if (!ids.length) return new Array(bars.length).fill(1);
  for (let k = 0; k < bars.length; k++) {
    let s = 0; for (const id of ids) s += perSeatEq[id].eq[k];
    eq[k] = +(s / ids.length).toFixed(4);
  }
  return eq;
}
// 已废弃:被 /seats/benchmark 真指数替代(fetchBenchmark+alignBench),无调用方。
// 旧实现 = mulberry32 种子随机「合成指数」,保留定义仅为最小 diff,绝不再画。
function benchmark(bars, seed) {
  const rnd = mulberry32((seed || 1) * 977 + 13);
  const eq = [1]; let v = 1;
  const drift = 0.0006;  // 基准温和上行
  for (let k = 1; k < bars.length; k++) {
    v *= (1 + drift + (rnd() - 0.5) * 0.014);
    eq.push(+v.toFixed(4));
  }
  return eq;
}

// ───────── 组装单标的 ─────────
// 核心:给定 bars(合成 OR 真日K)→ 扫席位 → 收益曲线/指标/基准。
// 席位策略(scanSeat)与指标(metricsOf)是真算法,喂真K即产出真回测;
// 证据链(evidenceFor 的因子/研报/卡/regime)仍 mock —— 见 ui/seats/README 开放项。
function buildSymbolFromBars(meta, bars, strategies, benchBars) {
  // 第3期:按"当前票在场策略"装配 perSeat(strategy.id 键);省略 strategies 时取该票在场策略,再退化旧 SEATS。
  const strats = (strategies && strategies.length) ? strategies
    : (strategyForCode(meta.code).length ? strategyForCode(meta.code) : SEATS);
  const decisions = [];
  const perSeat = {};
  strats.forEach(s => {
    const sid = s.id || s.template;
    const ds = scanSeat(bars, s, meta);
    decisions.push(...ds);
    perSeat[sid] = seatEquity(bars, ds, sid);
    perSeat[sid].metrics = metricsOf(perSeat[sid].eq, perSeat[sid].trades);
  });
  decisions.sort((a, b) => a.idx - b.idx || String(a.seat).localeCompare(String(b.seat)));
  decisions.forEach(d => { d.key = d.seat + '@' + d.idx; });
  // 第四参 benchBars = /seats/benchmark 真沪深300**原始行**(非对齐数组);没给 → bench=null,
  // 消费端隐藏基准线诚实降级(合成演示路径 buildSymbol 不传 → 不再画 mulberry32 假基准)。
  const bench = benchBars ? alignBench(bars, benchBars) : null;
  // 基准截至日(指数源末行 date):源滞后于本票末日时展示端标「· 截至MM-DD」——与「真指数未连接」
  // (bench 整体 null)是**两种可区分**的降级,语义不得混用。
  const benchAsof = (bench && benchBars && benchBars.length)
    ? String(benchBars[benchBars.length - 1].date).slice(0, 10) : null;
  return { meta, bars, decisions, perSeat, bench, benchAsof, stratIds: strats.map(s => s.id || s.template) };
}
function buildSymbol(meta) {
  return buildSymbolFromBars(meta, genBars(meta.seed, meta));
}

// ───────── 标的清单 ─────────
const SYMBOL_META = [
  { code: '300750', name: '宁德时代', industry: '锂电', seed: 71, start: 198, baseVol: 1.0, primary: true },
  { code: '600519', name: '贵州茅台', industry: '白酒', seed: 23, start: 1620, baseVol: 0.6,
    phases: [{ len: 24, drift: -0.004, vol: 0.013, volBias: 0.9 }, { len: 30, drift: 0.006, vol: 0.014, volBias: 1.0 }, { len: 16, drift: -0.003, vol: 0.015, volBias: 1.0 }, { len: 26, drift: 0.007, vol: 0.016, volBias: 1.1 }, { len: 24, drift: -0.002, vol: 0.017, volBias: 1.2 }], eventBar: 80 },
  { code: '002594', name: '比亚迪', industry: '新能源车', seed: 44, start: 245, baseVol: 0.9, eventBar: 66 },
  { code: '300308', name: '中际旭创', industry: '光模块', seed: 90, start: 132, baseVol: 1.2,
    phases: [{ len: 20, drift: 0.004, vol: 0.026, volBias: 1.1 }, { len: 28, drift: 0.016, vol: 0.030, volBias: 1.4 }, { len: 14, drift: -0.012, vol: 0.032, volBias: 1.2 }, { len: 30, drift: 0.014, vol: 0.030, volBias: 1.5 }, { len: 28, drift: -0.006, vol: 0.034, volBias: 1.6 }], eventBar: 60 },
  { code: '601012', name: '隆基绿能', industry: '光伏', seed: 12, start: 21, baseVol: 1.0,
    phases: [{ len: 30, drift: -0.009, vol: 0.022, volBias: 1.0 }, { len: 22, drift: 0.004, vol: 0.022, volBias: 1.0 }, { len: 20, drift: -0.006, vol: 0.024, volBias: 1.0 }, { len: 24, drift: 0.010, vol: 0.026, volBias: 1.3 }, { len: 24, drift: -0.004, vol: 0.026, volBias: 1.4 }], eventBar: 78 },
  { code: '600036', name: '招商银行', industry: '银行', seed: 5, start: 38, baseVol: 0.7,
    phases: [{ len: 24, drift: 0.002, vol: 0.011, volBias: 0.9 }, { len: 30, drift: 0.005, vol: 0.012, volBias: 1.0 }, { len: 16, drift: -0.002, vol: 0.013, volBias: 1.0 }, { len: 26, drift: 0.004, vol: 0.013, volBias: 1.05 }, { len: 24, drift: -0.001, vol: 0.014, volBias: 1.1 }], eventBar: 85 },
];

try { seedDefaultStrategy(); _dedupeDefaultStrategies(); _stripDeadDemoRecipe(); _pruneRetiredStrategies(); } catch (e) {}     // 必须在 SYMBOLS 构建前
// 后端持久化默认经 guanlan-bus 异步回填(setTimeout)→ 走 persist→emit;挂 GL.on 自愈去重(折叠后无环)。
// _pruneRetiredStrategies 同挂 GL.on:后端 /archive 异步回填可能复活烂尾策略,回填即再剪(幂等无环)。
try { if (window.GL && window.GL.on) window.GL.on(_dedupeDefaultStrategies); } catch (e) {}
try { if (window.GL && window.GL.on) window.GL.on(_pruneRetiredStrategies); } catch (e) {}
const SYMBOLS = {};
SYMBOL_META.forEach(m => { SYMBOLS[m.code] = buildSymbol(m); });
const PRIMARY_CODE = '300750';

// ───────── 盯盘池扩池(动态票池,localStorage 持久)─────────
// 固定 6 只是设计稿底座(带手调 phases 合成参数);动态票 = 用户/选股篮子加进来的任意 A 股,
// 真日K 由既有 per-code 拉取自动接管(luozi-app L189 effect 对任何 code 通用)。合成兜底仅
// file:// 离线时显形(带「样例」标,诚实降级),种子由代码哈希派生 —— 不冒充真实走势。
const POOL_LS_KEY = 'guanlan:lz:pool:v1';
function _poolLoad() { try { return JSON.parse(localStorage.getItem(POOL_LS_KEY)) || []; } catch (e) { return []; } }
function _poolSave(list) { try { localStorage.setItem(POOL_LS_KEY, JSON.stringify(list)); } catch (e) {} }
function _seedOf(code) { let h = 0; const s = String(code); for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) >>> 0; return h; }
function _dynMeta(e) {
  const h = _seedOf(e.code);
  return { code: e.code, name: e.name || e.code, industry: e.ind || e.industry || '—',
    seed: 17 + (h % 211), start: 40 + (h % 160), baseVol: 1.0, dynamic: true };
}
function poolAdd(e) {       // e={code(可带SH/SZ前缀),name,ind};幂等,返回 true=真新加
  const code = String((e && e.code) || '').replace(/^(SH|SZ|BJ)/i, '');
  if (!/^\d{6}$/.test(code)) return false;
  if (SYMBOL_META.some(m => m.code === code)) return false;
  const meta = _dynMeta(Object.assign({}, e, { code }));
  SYMBOL_META.push(meta);
  SYMBOLS[code] = buildSymbol(meta);
  const saved = _poolLoad().filter(x => x && x.code !== code);
  saved.push({ code: code, name: meta.name, ind: meta.industry });
  _poolSave(saved);
  return true;
}
function poolRemove(code) {  // 只许移除动态票;固定 6 只不可动
  const c = String(code || '').replace(/^(SH|SZ|BJ)/i, '');
  const i = SYMBOL_META.findIndex(m => m.code === c && m.dynamic);
  if (i < 0) return false;
  SYMBOL_META.splice(i, 1);
  delete SYMBOLS[c];
  _poolSave(_poolLoad().filter(x => x && x.code !== c));
  return true;
}
function poolIsDynamic(code) {
  const c = String(code || '').replace(/^(SH|SZ|BJ)/i, '');
  return SYMBOL_META.some(m => m.code === c && m.dynamic);
}
// ───────── 盯盘集判定(校场绑定驱动:有 agent 显式绑了该票 = 盯盘)─────────
// 全局默认(bind=[])不算盯盘;盯盘 = ∃ 策略 bind 非空且含该 code。
// owning agent = 第一个显式绑它的策略(单 agent 口径)。退役旧的 localStorage toggle,校场绑定为唯一真相。
function _monCode(code) { return String(code || '').replace(/^(SH|SZ|BJ)/i, ''); }
function monitoredCodes() {
  const out = {};
  strategyList().forEach(s => { if (Array.isArray(s.bind) && s.bind.length) s.bind.forEach(c => { out[_monCode(c)] = true; }); });
  return Object.keys(out);
}
function monitorAgentFor(code) {
  const c = _monCode(code);
  return strategyList().find(s => Array.isArray(s.bind) && s.bind.length && s.bind.map(_monCode).indexOf(c) >= 0) || null;
}
function poolIsMonitored(code) { return monitoredCodes().indexOf(_monCode(code)) >= 0; }
// 一键盯盘(2026-07-11 三页重排):开 = 把票绑进「本票当前策略」(无本票策略则绑全局默认「动量 · 默认」);
// 关 = 从所有策略 bind 移除。绑定仍是盯盘集唯一真相(与 ww_seats_bind / 后端 watcher 同源)。
function watchSet(code, on) {
  const c = _monCode(code);
  if (!c) return false;
  if (on) {
    if (poolIsMonitored(c)) return true;                       // 幂等
    const all = strategyList();
    // 优先全局默认「动量 · 默认」;没有则任一 bind 空的全局策略;再没有则第一个策略
    const tgt = all.find(s => s.name === '动量 · 默认' && (!s.bind || !s.bind.length))
      || all.find(s => !s.bind || !s.bind.length) || all[0];
    if (!tgt) return false;
    strategySave(Object.assign({}, tgt, { bind: (tgt.bind || []).concat([c]) }));
    return true;
  }
  let changed = false;
  strategyList().forEach(s => {
    if (!Array.isArray(s.bind) || !s.bind.length) return;
    const next = s.bind.filter(b => _monCode(b) !== c);
    if (next.length !== s.bind.length) { strategySave(Object.assign({}, s, { bind: next })); changed = true; }
  });
  return changed;
}
// 一次性迁移(2026-07-11 改造+料库整理):按 id 精确剪除退役实体,幂等,挂 GL.on 防后端回填复活。
//   ①烂尾实验策略×2(绑票不在池,盯盘集藏池外票隐形烧LLM);②guanlan-bus 种的示例物料(demo 假IC:
//   卡×6/研报×4/因子×4——P0①审计已从真路径剔除,料库里纯属噪音);③不在池股票的陈旧深度研报×2;
//   ④EV-014 联调测试卡(后端已打回 rejected,syncArchive 滤 rejected 不回流)。
//   research 类 GL.remove 经总线自动 /archive/remove(后端镜像同步删);卡/因子是 GL 本地种子,删即净。
function _pruneRetiredStrategies() {
  if (!window.GL) return;
  // 清单内联(勿提为模块级 const:本函数在模块顶部 L555 即被调,const 在其后声明会踩 TDZ 抛错被吞)
  const RETIRED = [
    'strat_mqae2q6f2th', 'strat_mqf7alg41jz',
    'card_reversal', 'card_north', 'card_pead', 'card_distrib', 'card_diverge', 'card_smallcap',
    'rs_reversal', 'rs_north', 'rs_distrib', 'rs_pead',
    'fa_reversal', 'fa_north', 'fa_pead', 'fa_distrib',
    'rs_report_SH605358_2026-06-10', 'rs_report_SZ000630_2026-06-15',
    'EV-014',
  ];
  RETIRED.forEach(id => {
    try { if (window.GL.get(id)) window.GL.remove(id); } catch (e) {}
  });
  // 配方里的死引用一并剥掉(如「动量 · 默认」旧 seed 挂的 card_reversal)
  try {
    strategyList().forEach(s => {
      const refs = s.refs || [];
      const next = refs.filter(r => RETIRED.indexOf(r) < 0);
      if (next.length !== refs.length) strategySave(Object.assign({}, s, { refs: next }));
    });
  } catch (e) {}
}
// 启动恢复持久池(SYMBOLS 构建后;坏条目跳过)
try {
  _poolLoad().forEach(e => {
    if (!e || !e.code || SYMBOL_META.some(m => m.code === e.code)) return;
    const meta = _dynMeta(e);
    SYMBOL_META.push(meta);
    SYMBOLS[e.code] = buildSymbol(meta);
  });
} catch (e) {}

// ───────── 时间尺度变换 (周/日聚合 + 日内合成) ─────────
function subdivideDay(bar, k, seed) {
  const rnd = mulberry32((seed * 131 + bar.i * 7919) >>> 0);
  const span = Math.max(0.02, bar.h - bar.l);
  const closes = [];
  for (let j = 0; j < k; j++) {
    const t = (j + 1) / k;
    closes.push(bar.o + (bar.c - bar.o) * t + (rnd() - 0.5) * span * 0.55);
  }
  closes[Math.floor(rnd() * k)] = bar.h;       // 触及当日高
  closes[Math.floor(rnd() * k)] = bar.l;       // 触及当日低
  closes[k - 1] = bar.c;                        // 收于日收盘
  const out = []; let prev = bar.o;
  for (let j = 0; j < k; j++) {
    const c = closes[j], o = prev;
    const h = Math.min(bar.h, Math.max(o, c) + rnd() * span * 0.12);
    const l = Math.max(bar.l, Math.min(o, c) - rnd() * span * 0.12);
    out.push({ o: +o.toFixed(2), c: +c.toFixed(2), h: +h.toFixed(2), l: +l.toFixed(2),
      v: +(bar.v / k * (0.6 + rnd() * 0.8)).toFixed(2), date: bar.date, day: bar.i, sub: j });
    prev = c;
  }
  return out;
}

// tf: 'W' | 'D' | '60' | '30' | '15'; cursor/reveal 为日线索引; reviewing=复盘已跑完
// liveEdge(实盘):日内窗口右端延伸到已拼接的今日 5min(仅 live 传 true,回测不传→行为不变)。
function frameData(symbol, tf, cursor, reveal, reviewing, liveEdge) {
  const bars = symbol.bars, decs = symbol.decisions, n = bars.length;
  if (tf === 'D') {
    return { fbars: bars, fdecs: decs, fcursor: cursor, freveal: reveal,
      viewEnd: reviewing ? n - 1 : cursor, label: '日线', mapDaily: (i) => i };
  }
  if (tf === 'W') {
    const g = 5, fbars = [];
    for (let s = 0; s < n; s += g) {
      const grp = bars.slice(s, s + g);
      fbars.push({ i: fbars.length, o: grp[0].o, c: grp[grp.length - 1].c,
        h: Math.max.apply(null, grp.map(b => b.h)), l: Math.min.apply(null, grp.map(b => b.l)),
        v: grp.reduce((a, b) => a + b.v, 0), date: grp[grp.length - 1].date });
    }
    const map = (i) => Math.floor(i / g);
    const fdecs = decs.map(d => Object.assign({}, d, { idx: map(d.idx) }));
    return { fbars, fdecs, fcursor: map(cursor), freveal: map(reveal),
      viewEnd: reviewing ? fbars.length - 1 : map(cursor), label: '周线', mapDaily: map };
  }
  // 日内: cursor 前 winDays 天窗口。优先用真 5min(symbol.bars5)聚合;无/越界则回退 subdivideDay 合成。
  const winDays = tf === '60' ? 12 : tf === '30' ? 8 : tf === '15' ? 5 : tf === '5' ? 3 : 2;   // 5min:近3日 · 1min:近2日
  const endDay = Math.min(n - 1, Math.max(0, reviewing ? n - 1 : cursor));   // 钳制:防 cursor 越界(切 symbol/TF 瞬态)致 bars[day] undefined
  const startDay = Math.max(0, endDay - winDays + 1);
  const lab = ({ '60': '60分', '30': '30分', '15': '15分', '5': '5分', '1': '1分' })[tf];

  // —— 真 5min 路径:把窗口内的真 5min 按日分组、按 perGroup 切块聚合成 60/30/15 分 ——
  const b5 = tf === '1' ? symbol.bars1 : symbol.bars5;   // 1min 用 bars1(实时),其余用 bars5
  if (b5 && b5.length) {
    const perGroup = tf === '60' ? 12 : tf === '30' ? 6 : tf === '15' ? 3 : 1;   // 每根日内 = 多少根真5min;5min/1min=1(原生直用)
    const sD = bars[startDay].date;
    let eD = bars[endDay].date;
    // ④-② 实盘:把窗口右端延伸到已拼接的今日 5min(b5 末根 day)→ 今日盘中 5min 入日内图;仅 live,回测不变。
    if (liveEdge && b5.length) { const m5 = b5[b5.length - 1].day; if (m5 > eD) eD = m5; }
    const win5 = b5.filter(b => b.day >= sD && b.day <= eD);
    if (win5.length >= perGroup) {
      const fbars = [], lastIdxOfDay = {}, byDay = {}, dayOrder = [];
      win5.forEach(b => { if (!byDay[b.day]) { byDay[b.day] = []; dayOrder.push(b.day); } byDay[b.day].push(b); });
      dayOrder.forEach(day => {
        const arr = byDay[day];
        for (let s = 0; s < arr.length; s += perGroup) {
          const grp = arr.slice(s, s + perGroup);
          fbars.push({ i: fbars.length, day, date: grp[grp.length - 1].date,
            o: grp[0].o, c: grp[grp.length - 1].c,
            h: Math.max.apply(null, grp.map(x => x.h)), l: Math.min.apply(null, grp.map(x => x.l)),
            v: +grp.reduce((a, x) => a + x.v, 0).toFixed(2) });
        }
        lastIdxOfDay[day] = fbars.length - 1;
      });
      const mapR = (di) => { const d = bars[di] && bars[di].date; return (d != null && lastIdxOfDay[d] != null) ? lastIdxOfDay[d] : -1; };
      const fdecs = decs.filter(d => bars[d.idx] && lastIdxOfDay[bars[d.idx].date] != null)
        .map(d => Object.assign({}, d, { idx: lastIdxOfDay[bars[d.idx].date] }));
      const freveal = reveal >= endDay ? fbars.length - 1 : mapR(reveal);
      return { fbars, fdecs, fcursor: fbars.length - 1, freveal,
        viewEnd: fbars.length - 1, label: lab, mapDaily: mapR, real5: true };
    }
  }

  // —— 回退: subdivideDay 合成(真5min 未加载 / 窗口越界 时)——
  const k = tf === '60' ? 4 : tf === '30' ? 8 : tf === '5' ? 48 : 16;
  const fbars = [], dayStart = {};
  for (let day = startDay; day <= endDay; day++) {
    const bd = bars[day];
    if (!bd) continue;                                   // 防越界(同上钳制兜底)
    dayStart[day] = fbars.length;
    subdivideDay(bd, k, symbol.meta.seed).forEach(sb => { sb.i = fbars.length; fbars.push(sb); });
  }
  const map = (i) => (i >= startDay && i <= endDay) ? dayStart[i] + k - 1 : (i < startDay ? -1 : fbars.length - 1);
  const fdecs = decs.filter(d => d.idx >= startDay && d.idx <= endDay).map(d => Object.assign({}, d, { idx: map(d.idx) }));
  const freveal = reveal >= endDay ? fbars.length - 1 : (reveal >= startDay ? dayStart[reveal] + k - 1 : -1);
  return { fbars, fdecs, fcursor: fbars.length - 1, freveal,
    viewEnd: fbars.length - 1, label: lab, mapDaily: map, real5: false };
}

// 全量 5min→30min 聚合(与 frameData perGroup=6 同口径,但不做视图窗口裁剪):
//   供 30 分钟 agent 真跑 + run 回放净值。无 bars5 → []。每根 {i,day,date:'YYYY-MM-DD HH:MM',o,c,h,l,v}。
function bars30(symbol) {
  const b5 = (symbol && symbol.bars5) || [];
  if (!b5.length) return [];
  const byDay = {}, dayOrder = [];
  b5.forEach(b => { if (!byDay[b.day]) { byDay[b.day] = []; dayOrder.push(b.day); } byDay[b.day].push(b); });
  const out = [];
  dayOrder.forEach(day => {
    const arr = byDay[day];
    for (let s = 0; s < arr.length; s += 6) {
      const grp = arr.slice(s, s + 6);
      out.push({ i: out.length, day, date: grp[grp.length - 1].date,
        o: grp[0].o, c: grp[grp.length - 1].c,
        h: Math.max.apply(null, grp.map(x => x.h)), l: Math.min.apply(null, grp.map(x => x.l)),
        v: +grp.reduce((a, x) => a + x.v, 0).toFixed(2) });
    }
  });
  return out;
}

// 把决策(realDecs/runDecs,带 asof 时间戳)按**时间戳容纳**映射到「当前显示帧」的 bar 下标。
//   决策的预存 idx 是按 bars30/symbol.bars 算的(供 runBacktest 模拟成交),与随 TF 变的 dispFrame.fbars 坐标系不通用;
//   图上落子必须按时间重新定位到当前帧,否则切 TF 即错位/消失(30min run 在任何视图都画不对的根因)。
//   日期口径(见 frameData/bars30):日线帧 bar.date='YYYY-MM-DD'(10);日内帧/bars30 bar.date='YYYY-MM-DD HH:MM'(16,收盘时刻)。
//   规则:① 日线帧 → 决策按 asof[:10] 落到该日 K(30min 的 10:00 买入 → 落到当日日 K,满足「切日线也显示」);
//         ② 日内帧 → 决策按 asof[:16] 精确落到对应 bar,不中则取同日「收盘≥决策时刻」的最近一根,再不中取当日末根;
//         ③ 日线决策落日内帧 → 取当日末根;
//         ④ 去噪:一根帧 bar 撞进多笔(粗于原生,如日线看 30min)→ 仅留买/卖、每向第一笔,丢观望;
//            原生分辨率(一帧一笔不碰撞)→ 原样全留(含观望)。窗外(idx<0)丢弃,图自身按可见窗再裁。
//   返回的标记 = 既有决策超集 + 重写 idx(key/side/direction/conf/rationale 等原样透传,选中描环按 key 仍对得上流水)。
function mapDecsToFrame(decs, fbars) {
  if (!decs || !decs.length || !fbars || !fbars.length) return [];
  const intradayFrame = (fbars[0].date || '').length > 10;
  const byFull = {}, byDayLast = {}, dayBars = {};
  fbars.forEach((b, i) => {
    const dt = b.date || '';
    byFull[dt] = i;
    const day = dt.slice(0, 10);
    byDayLast[day] = i;                                   // fbars 升序 → 同日末根最后写入
    (dayBars[day] || (dayBars[day] = [])).push({ i, dt });
  });
  const locate = (ts) => {
    if (!ts) return -1;
    const day = ts.slice(0, 10);
    if (!intradayFrame) return byFull[day] != null ? byFull[day] : -1;        // ① 日线帧:按日
    if (ts.length > 10) {                                                     // ② 日内决策 → 日内帧
      if (byFull[ts.slice(0, 16)] != null) return byFull[ts.slice(0, 16)];    //   精确同分辨率
      const arr = dayBars[day] || [];
      const hit = arr.find(x => x.dt >= ts);                                  //   容纳:收盘≥决策时刻的最近一根
      return hit ? hit.i : (byDayLast[day] != null ? byDayLast[day] : -1);
    }
    return byDayLast[day] != null ? byDayLast[day] : -1;                      // ③ 日线决策 → 当日末根
  };
  const byIdx = {};
  decs.forEach(d => {
    const idx = locate(String(d.asof || d.date || ''));
    if (idx < 0) return;
    (byIdx[idx] || (byIdx[idx] = [])).push(Object.assign({}, d, { idx }));
  });
  const out = [];
  Object.keys(byIdx).forEach(k => {
    const grp = byIdx[k];
    if (grp.length === 1) { out.push(grp[0]); return; }                       // ④ 无碰撞:原样(含观望)
    const acts = grp.filter(d => d.side === 'buy' || d.side === 'sell');      //   聚合:仅留买卖
    const seen = {};
    acts.forEach(d => { if (!seen[d.side]) { seen[d.side] = 1; out.push(d); } });   // 每向第一笔
  });
  return out;
}

// 拉新闻标记流(回测 PIT / 实时);无 GUANLAN_BACKEND 或失败 → null(调用方泳道静默空,诚实降级)。
async function fetchNews(code, asof, mode) {
  const API = (window.GUANLAN_BACKEND || '');
  if (!API) return null;
  const m = mode || 'pit';
  const q = '/seats/news?code=' + encodeURIComponent(code) + '&mode=' + m +
            (asof ? '&asof=' + encodeURIComponent(asof) : '') + '&window=250';
  try {
    const res = await fetch(API + q);
    if (!res.ok) return null;
    const j = await res.json();
    return (j && j.ok) ? j : null;
  } catch (e) { return null; }
}

// 把 PIT 新闻流按时间戳聚类到「当前显示帧」的 bar(镜像 mapDecsToFrame 的 locate 规则)。
//   pit_store ts 用 'T' 分隔(2026-06-01T09:31:00);日内帧 bar.date 用空格(YYYY-MM-DD HH:MM,收盘刻)→ 匹配前把 'T'→' '。
//   产出每 bar 一桶:{idx, count, hit(命中关键词), items}。keyword 空 → hit 恒 false(不高亮)。
function mapNewsToFrame(items, fbars, keyword) {
  if (!items || !items.length || !fbars || !fbars.length) return [];
  const intradayFrame = (fbars[0].date || '').length > 10;
  const byFull = {}, byDayLast = {}, dayBars = {};
  fbars.forEach((b, i) => {
    const dt = b.date || '';
    byFull[dt] = i;
    const day = dt.slice(0, 10);
    byDayLast[day] = i;
    (dayBars[day] || (dayBars[day] = [])).push({ i, dt });
  });
  const locate = (ts) => {
    if (!ts) return fbars.length - 1;                          // live 无 ts → 落最右 bar
    const day = ts.slice(0, 10);
    if (!intradayFrame) return byFull[day] != null ? byFull[day] : -1;
    if (ts.length > 10) {
      const norm = ts.replace('T', ' ');
      const key = norm.slice(0, 16);
      if (byFull[key] != null) return byFull[key];
      const arr = dayBars[day] || [];
      const hit = arr.find(x => x.dt >= norm);
      return hit ? hit.i : (byDayLast[day] != null ? byDayLast[day] : -1);
    }
    return byDayLast[day] != null ? byDayLast[day] : -1;
  };
  const kw = String(keyword || '').split('|').map(s => s.trim()).filter(Boolean);
  const matches = (t) => kw.length > 0 && kw.some(k => (t || '').indexOf(k) >= 0);
  const byIdx = {};
  items.forEach(it => {
    let idx = locate(String(it.ts || it.date || ''));
    // 周末/节假日 ts 无对应 bar(如周六快讯)→ 回退 pit_store 的 date(可见交易日,周末新闻滚进周一)。
    //   PIT 安全:date≥ts 日,新闻只会标在其发生**之后**的首个交易 bar,绝不前置。真机实测缺此回退丢 46% 条目。
    if (idx < 0) idx = locate(String(it.date || '').slice(0, 10));
    if (idx < 0) return;
    (byIdx[idx] || (byIdx[idx] = [])).push(it);
  });
  const out = [];
  Object.keys(byIdx).forEach(k => {
    const grp = byIdx[k];
    out.push({ idx: +k, count: grp.length, hit: grp.some(it => matches(it.title)), items: grp });
  });
  return out;
}

// ───────── 真日K接入(/seats/daily;失败回退合成,见 ui/seats/README 开放项)─────────
// 把 /seats/daily 的行 {date,open,high,low,close,vol,amount} 归一成本模块 bar 形状。
function normDailyBars(rows) {
  if (!rows || !rows.length) return null;
  const out = [];
  rows.forEach(r => {
    if (r.open == null || r.close == null) return;            // null OHLC(当日未收盘的占位行等)→ 丢弃,**别让 +null=0 漏成 0 价柱撑爆刻度**
    const o = +r.open, c = +r.close, h = +r.high, l = +r.low;
    if (!isFinite(o) || !isFinite(c)) return;                 // 坏 bar 丢弃
    out.push({
      i: out.length, date: (r.date || '').slice(0, 10),
      o: +o.toFixed(2), c: +c.toFixed(2),
      h: +(isFinite(h) ? h : Math.max(o, c)).toFixed(2),
      l: +(isFinite(l) ? l : Math.min(o, c)).toFixed(2),
      v: +(+r.vol || 0), event: false,                        // 真事件数据待上游 → 事件席暂不触发
    });
  });
  return out.length ? out : null;
}
// 拉真日K;无 GUANLAN_BACKEND(file://)或失败/404 → null(调用方回退合成,诚实降级)。
async function fetchDailyBars(code, n) {
  const API = (window.GUANLAN_BACKEND || '');
  if (!API) return null;
  try {
    const res = await fetch(API + '/seats/daily?code=' + encodeURIComponent(code) + '&n=' + (n || 250));
    if (!res.ok) return null;
    const j = await res.json();
    if (!j || !j.ok || !j.bars) return null;
    return normDailyBars(j.bars);
  } catch (e) { return null; }
}
// 拉真沪深300日收盘(/seats/benchmark,与 workflow 绩效同源 etf_index.parquet 399300.SZ)。
// 返回**原始行** [{date:'YYYY-MM-DD', close}]; 无后端/失败 → null(消费端隐藏基准线,绝不画假基准)。
async function fetchBenchmark(start, end, n) {
  const API = (window.GUANLAN_BACKEND || '');
  if (!API) return null;
  try {
    const q = start ? ('start=' + start + '&end=' + (end || '')) : ('n=' + (n || 250));
    const res = await fetch(API + '/seats/benchmark?' + q);
    if (!res.ok) return null;
    const j = await res.json();
    return (j && j.ok && j.bars && j.bars.length) ? j.bars : null;
  } catch (e) { return null; }
}
// 真指数对齐到本票 bars 的日期轴:起点归一为 1,缺日 ffill,起点前 null(画线/取末值须跳过 null)。
// ffill **不越过指数源末日**:末行 date 之后的 bars 一律 null(chart 对 null 抬笔 → 虚线提前收笔,
// 绝不画「指数源滞后期横盘」假轨迹;截至日由 buildSymbolFromBars 挂 benchAsof 供展示端诚实标注)。
function alignBench(bars, idxRows) {
  const byDate = {};
  let lastIdxDate = null;
  (idxRows || []).forEach(function(r) {
    const c = +r.close, d = String(r.date).slice(0, 10);
    if (Number.isFinite(c)) { byDate[d] = c; if (lastIdxDate == null || d > lastIdxDate) lastIdxDate = d; }
  });
  let base = null, last = null;
  const out = bars.map(function(b) {
    if (lastIdxDate != null && b.date > lastIdxDate) return null;   // 源末日之后:抬笔,不 ffill
    const c = byDate[b.date];
    if (c != null) { if (base == null) base = c; last = +((c / base)).toFixed(4); }
    return last;
  });
  return base != null ? out : null;
}

// ───────── ④-③ 实盘今日柱缓存(实时源):把白天记下的今日真实柱存住,盘后/隔夜保持图有今天,官方日K入库即让位 ─────────
//   诚实:缓存柱 = 今天**真实发生**的 OHLC(开/高/低/收当日已定盘),仅数据源为实时报价而非官方日K;
//   不写 G:/stocks,纯 localStorage;官方日K落库(末根真日K日 ≥ 缓存.date)即由调用方硬替换 + 清缓存。
const LZ_LIVEBAR_KEY = (code) => 'guanlan:lz:livebar:' + code;
function livebarLoad(code) {
  try {
    const b = JSON.parse(localStorage.getItem(LZ_LIVEBAR_KEY(code)) || 'null');
    if (b && b.date && isFinite(+b.o) && isFinite(+b.c)) return b;   // 必须有日期+有效开收(挡 null/坏价污染)
  } catch (e) {}
  return null;
}
function livebarSave(code, bar) {
  try { localStorage.setItem(LZ_LIVEBAR_KEY(code), JSON.stringify(bar)); } catch (e) {}
  return bar;
}
function livebarClear(code) {
  try { localStorage.removeItem(LZ_LIVEBAR_KEY(code)); } catch (e) {}
}
// 从实时报价提一根今日柱:asofDate 必须**晚于**末根真日K日(才算"今日新柱"),现价须有效;h/l 夹住 o/c 防过期盘口截顶。
//   失败 → null(不写),诚实降级。
function livebarFromQuote(quote, lastBarDate) {
  if (!quote || !quote.asofDate) return null;
  if (lastBarDate && quote.asofDate <= lastBarDate) return null;     // 不晚于已结算 → 不是今日新柱
  const c = +quote.price;
  if (!isFinite(c)) return null;
  const o = (quote.open != null && isFinite(+quote.open)) ? +quote.open : c;
  const hi = (quote.high != null && isFinite(+quote.high)) ? +quote.high : -Infinity;
  const lo = (quote.low != null && isFinite(+quote.low)) ? +quote.low : Infinity;
  return {
    date: quote.asofDate, o: +o.toFixed(2), c: +c.toFixed(2),
    h: +Math.max(hi, o, c).toFixed(2), l: +Math.min(lo, o, c).toFixed(2),
    v: (quote.volume != null && isFinite(+quote.volume)) ? +quote.volume : 0,
    fresh: !!quote.fresh, capturedAt: quote.asof || quote.asofDate,
  };
}

// 真 5min 行 → bar(date 含时分,day 用于按日聚合;供日内 TF 真聚合)。
function normBars5(rows) {
  if (!rows || !rows.length) return null;
  const out = [];
  rows.forEach(r => {
    if (r.open == null || r.close == null) return;            // null OHLC 占位行 → 丢弃(防 +null=0 漏成 0 价柱)
    const o = +r.open, c = +r.close, h = +r.high, l = +r.low;
    if (!isFinite(o) || !isFinite(c)) return;
    const date = (r.date || '').slice(0, 16);                 // 'YYYY-MM-DD HH:MM'
    out.push({ i: out.length, date, day: date.slice(0, 10),
      o: +o.toFixed(2), c: +c.toFixed(2),
      h: +(isFinite(h) ? h : Math.max(o, c)).toFixed(2),
      l: +(isFinite(l) ? l : Math.min(o, c)).toFixed(2),
      v: +(+r.vol || 0) });
  });
  return out.length ? out : null;
}
// 拉真历史 5min(默认 ~2400 根 ≈ 50 个交易日);无后端/失败 → null(日内回退合成)。
async function fetchBars5(code, n) {
  const API = (window.GUANLAN_BACKEND || '');
  if (!API) return null;
  try {
    const res = await fetch(API + '/seats/daily?freq=5min&code=' + encodeURIComponent(code) + '&n=' + (n || 2400));
    if (!res.ok) return null;
    const j = await res.json();
    if (!j || !j.ok || !j.bars) return null;
    return normBars5(j.bars);
  } catch (e) { return null; }
}

// ───────── 真K水合(校场演武 / 舰队 共用) ─────────
// REAL_BARS[code] = /seats/daily 真日线(normDailyBars 归一);REAL_BARS5 同理 5min。
// 红线:缺位 = 后端没给,调用方必须诚实剔除/显形,绝不回退 genBars 合成。
const REAL_BARS = {};        // code -> bars(day)
const REAL_BARS5 = {};       // code -> bars(5min)
const REAL_BARS_TS = {};     // (freq+code) -> 水合 epoch ms(10min 内不重拉)
async function _hydrate(store, fetcher, freq, codes, n) {
  const all = (codes && codes.length) ? codes : SYMBOL_META.map(m => m.code);
  const want = all.filter(c => !store[c] || (Date.now() - (REAL_BARS_TS[freq + c] || 0)) > 600000);
  await Promise.all(want.map(c => fetcher(c, n).then(bars => {
    if (bars && bars.length) { store[c] = bars; REAL_BARS_TS[freq + c] = Date.now(); }
  }).catch(() => {})));
  const cov = {};
  all.forEach(c => { cov[c] = !!store[c]; });
  return cov;                 // {code: true|false} 真数据覆盖表
}
// 真沪深300原始行缓存(/seats/benchmark;舰队 realSymbolOf 对标用)。只在 day 水合后拉一次;
// 失败保持 null → bench=null 消费端隐藏基准线(诚实降级,绝不回退 mulberry32 合成)。
let BENCH_CACHE = null;
async function hydrateRealBars(codes, n) {
  const cov = await _hydrate(REAL_BARS, fetchDailyBars, 'd', codes, n || 250);
  if (!BENCH_CACHE) {
    try { BENCH_CACHE = await fetchBenchmark(null, null, n || 250); } catch (e) {}
  }
  return cov;
}
function hydrateRealBars5(codes, n) { return _hydrate(REAL_BARS5, fetchBars5, '5', codes, n || 2400); }
// freq 口径 = clock.execTF('day'|'5min';兼容 frameData tf 的 '5'),拿错频率比缺数据更隐蔽
function realBarsOf(code, freq) { return ((freq === '5min' || freq === '5') ? REAL_BARS5[code] : REAL_BARS[code]) || null; }

// 真K → buildSymbolFromBars 装配缓存(舰队用;key 带末根日期+策略指纹,真K更新/策略增删/原地编辑自动重建)
// 指纹含 id|template|clock|bind(影响确定性扫描的字段);name/creed/refs/color 不入指纹(不影响扫描)。
const REAL_SYM_CACHE = {};
function realSymbolOf(code) {
  const meta = SYMBOL_META.find(function(m) { return m.code === code; });
  const bars = REAL_BARS[code];
  if (!meta || !bars || !bars.length) return null;
  const sids = (window.lzStrategyList ? window.lzStrategyList() : []).map(function(s) {
    return s.id + '|' + (s.template || '') + '|' + JSON.stringify(s.clock || null) + '|' + (s.bind || []).join('.');
  }).join(',');
  // key 带 bench 长度:水合先后可能让 BENCH_CACHE 晚到 → 到货后自动重建,防陈旧 bench=null 缓存
  const key = bars.length + ':' + bars[bars.length - 1].date + ':' + sids
    + ':bench' + (BENCH_CACHE ? BENCH_CACHE.length : 0);
  if (!REAL_SYM_CACHE[code] || REAL_SYM_CACHE[code].key !== key) {
    REAL_SYM_CACHE[code] = { key: key, sym: buildSymbolFromBars(meta, bars, undefined, BENCH_CACHE) };
  }
  return REAL_SYM_CACHE[code].sym;
}

// ④-② 实时 5min(引擎 /watch/bars,pytdx 实时口,最近 ~240-480 根、含今日盘中):拼今日日内图。
//   行 {open,high,low,close,vol,trade_date} → 映射到 normBars5 的 r.date 字段;无后端/失败 → null(诚实降级)。
async function fetchRealtimeBars5(code, n) {
  const API = (window.GUANLAN_BACKEND || '');
  if (!API || !code) return null;
  try {
    const res = await fetch(API + '/watch/bars?code=' + encodeURIComponent(code) + '&n=' + (n || 480));
    if (!res.ok) return null;
    const j = await res.json();
    if (!j || !j.ok || !j.bars) return null;
    const rows = j.bars.map(b => ({ date: b.trade_date, open: b.open, high: b.high, low: b.low, close: b.close, vol: b.vol }));
    return normBars5(rows);
  } catch (e) { return null; }
}

// ②+ 实时 1min(/seats/bars_live?freq=1min,引擎 pytdx 1min):盘中更细盯盘。1min 历史库个股为空,故只走实时。
//   返回行 {date,open,high,low,close,vol} 同 normBars5 形状(date 16 位 → day/o/c/h/l/v)。无后端/失败 → null。
async function fetchRealtimeBars1(code, n) {
  const API = (window.GUANLAN_BACKEND || '');
  if (!API || !code) return null;
  try {
    const res = await fetch(API + '/seats/bars_live?freq=1min&code=' + encodeURIComponent(code) + '&n=' + (n || 480));
    if (!res.ok) return null;
    const j = await res.json();
    if (!j || !j.ok || !j.bars) return null;
    return normBars5(j.bars);
  } catch (e) { return null; }
}

// ───────── 市场状态(今日快照,/watch/market_status):regime + 涨停/跌停 + 主线 ─────────
const REGIME_CN = { bull: '牛市·上行', bear: '熊市·下行', oscillating: '震荡', oscillate: '震荡', neutral: '中性', unknown: '—' };
async function fetchMarketStatus() {
  const API = (window.GUANLAN_BACKEND || '');
  if (!API) return null;
  try {
    const res = await fetch(API + '/watch/market_status');
    if (!res.ok) return null;
    const j = await res.json();
    if (!j || !j.ok) return null;
    const rg = j.regime || {}, lu = j.limit_ups || {}, ml = j.mainline || {};
    const raw = (rg.regime || rg.state || '').toString().toLowerCase();
    const top = (ml.top || []).map(t => t && (t.industry || t.name)).filter(Boolean);   // 端点字段是 industry(非 name)
    const pick = (o, ...ks) => { for (const k of ks) if (o[k] != null) return o[k]; return null; };
    return {
      date: j.date || null,
      regime: REGIME_CN[raw] || (rg.regime || rg.state || null),
      breadth: (rg.breadth_pct != null ? +rg.breadth_pct : null),
      limitUp: pick(lu, 'limit_up_total', 'total'),
      limitDn: pick(lu, 'limit_down'),
      upCount: pick(lu, 'up_count'), downCount: pick(lu, 'down_count'),
      mainline: top.length ? top.slice(0, 3).join(' · ') : null,
    };
  } catch (e) { return null; }
}

// ───────── ④ 实时盘口(/seats/quote):实盘盯盘真现价/涨跌/盘口(腾讯实时,同引擎 /quotes 源)─────────
//   盘中 fresh=true(报价日 > 最后日K日);盘后 fresh=false(最后收盘快照)。无后端/失败 → null(回退历史回放,诚实降级)。
async function fetchQuote(code) {
  const API = (window.GUANLAN_BACKEND || '');
  if (!API || !code) return null;
  try {
    const res = await fetch(API + '/seats/quote?code=' + encodeURIComponent(code));
    if (!res.ok) return null;
    const j = await res.json();
    if (!j || !j.ok) return null;
    return {
      code: j.code, name: j.name,
      price: j.price, prevClose: j.prevClose, open: j.open, high: j.high, low: j.low,
      change: j.change, changePercent: j.changePercent,
      volume: j.volume, amount: j.amount, turnoverRate: j.turnover_rate, volRatio: j.vol_ratio,
      asof: j.asof, asofDate: j.asofDate, lastBarDate: j.lastBarDate, lastClose: j.lastClose,
      fresh: j.fresh,
    };
  } catch (e) { return null; }
}

// ───────── ② 五档盘口 + 逐笔(/seats/orderbook, /seats/ticks):tdx 经统一 live_client ─────────
//   落子原无盘口挂单薄/逐笔成交面板;tdx 不可达/非交易时段 → 后端 ok:false,前端显 note 降级绝不塞假档。
async function fetchOrderbook(code) {
  const API = (window.GUANLAN_BACKEND || '');
  if (!API || !code) return null;
  try {
    const res = await fetch(API + '/seats/orderbook?code=' + encodeURIComponent(code));
    if (!res.ok) return null;
    return await res.json();                       // {ok, code, price, levels:[{level,bid,bid_vol,ask,ask_vol}], note}
  } catch (e) { return null; }
}
async function fetchTicks(code, limit) {
  const API = (window.GUANLAN_BACKEND || '');
  if (!API || !code) return null;
  try {
    const res = await fetch(API + '/seats/ticks?code=' + encodeURIComponent(code) + '&limit=' + (limit || 30));
    if (!res.ok) return null;
    return await res.json();                       // {ok, code, ticks:[{time,price,vol,side}], n, note}
  } catch (e) { return null; }
}

// ───────── 后端定时盯盘(2026-07-11 三页重排):状态/开关 + 研判时间线 ─────────
//   watcher 在服务端盘中按策略节拍自动研判绑定票(GUANLAN_SEATS_WATCH=1 才起);
//   老后端无此端点 → 404 → null → UI 显「未启用」诚实态,绝不冒充在盯。
async function fetchWatchStatus() {
  const API = (window.GUANLAN_BACKEND || '');
  if (!API) return null;
  try {
    const res = await fetch(API + '/seats/watch/status');
    if (!res.ok) return null;
    const j = await res.json();
    if (!j || !j.ok) return null;
    return { enabled: !!j.enabled, watching: j.watching || [], todayCount: (j.today_count != null ? +j.today_count : null),
      budget: (j.daily_budget != null ? +j.daily_budget : null), lastTick: j.last_tick || null, marketOpen: !!j.market_open };
  } catch (e) { return null; }
}
async function toggleWatch(on) {
  const API = (window.GUANLAN_BACKEND || '');
  if (!API) return null;
  try {
    const res = await fetch(API + '/seats/watch/toggle', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ on: !!on }) });
    if (!res.ok) return null;
    const j = await res.json();
    if (!j || !j.ok) return null;
    return { enabled: !!j.enabled, watching: j.watching || [], todayCount: (j.today_count != null ? +j.today_count : null),
      budget: (j.daily_budget != null ? +j.daily_budget : null), lastTick: j.last_tick || null, marketOpen: !!j.market_open };
  } catch (e) { return null; }
}
// 本票研判/条件单时间线(后端落盘全量,exclude_runs 剔批跑回放;研判卡与决策留痕共用)。
async function fetchDecisionsTimeline(code, limit) {
  const API = (window.GUANLAN_BACKEND || '');
  if (!API || !code) return null;
  try {
    const res = await fetch(API + '/seats/decisions?code=' + encodeURIComponent(code)
      + '&limit=' + (limit || 60) + '&exclude_runs=1');
    if (!res.ok) return null;
    const j = await res.json();
    if (!j || !j.ok || !Array.isArray(j.decisions)) return null;
    return j.decisions;
  } catch (e) { return null; }
}

// ───────── 研报(A+闭环):本股深度研报状态(/report-progress)+ 席位引用的 GL research ─────────
function prefixCode(code) {
  const c = String(code || '').replace(/^(SH|SZ|BJ)/i, '').trim();
  if (/^(6|9|5)/.test(c)) return 'SH' + c;
  if (/^(4|8)/.test(c)) return 'BJ' + c;
  return 'SZ' + c;                                // 0/2/3 → 深市
}
// 查某股深度研报是否已生成(对话·研报模块跑出,存 out/<CODE>_progress.json)。
async function fetchReportStatus(code) {
  const API = (window.GUANLAN_BACKEND || '');
  if (!API) return null;
  try {
    const res = await fetch(API + '/report-progress?code=' + encodeURIComponent(prefixCode(code)));
    if (!res.ok) return null;
    const j = await res.json();
    if (!j || !j.ok) return { exists: false };
    const total = +j.total || 0, done = +j.done || 0, running = +j.running || 0;
    return { exists: total > 0, done, total, running, asof: j.asof || null,
      complete: total > 0 && done >= total && running === 0 };
  } catch (e) { return null; }
}
// 闭环:某 LZ 席位(reversal/momentum/event/risk)在 GL 里引用的 research(经 seat→card→research)。
function seatResearch(lzSeatId) {
  if (!window.GL) return [];
  const seat = window.GL.get('seat_' + lzSeatId) || window.GL.all('seat').find(s => (s.id || '').indexOf(lzSeatId) >= 0);
  if (!seat) return [];
  const out = [], seen = new Set();
  const add = (id) => { const a = window.GL.get(id); if (a && a.type === 'research' && !seen.has(id)) { seen.add(id); out.push({ id, title: a.title, from: a.from, kind: a.kind }); } };
  (seat.refs || []).forEach(rid => {
    const a = window.GL.get(rid);
    if (!a) return;
    if (a.type === 'research') add(rid);
    else if (a.type === 'card') (a.refs || []).forEach(add);
  });
  return out;
}
// 某 LZ 席位引用的经验卡(seat.refs 里第一张 card)—— GL 共享档案,与校场/验证区同源(非 evidenceFor 的 mock)。
function seatCard(lzSeatId) {
  if (!window.GL) return null;
  const seat = window.GL.get('seat_' + lzSeatId) || window.GL.all('seat').find(s => (s.id || '').indexOf(lzSeatId) >= 0);
  if (!seat) return null;
  for (const rid of (seat.refs || [])) {
    const a = window.GL.get(rid);
    if (a && a.type === 'card') {
      return { id: a.id, name: a.title || a.id, hint: a.insight || a.verdict || '',
        conf: (a.conf != null ? a.conf : null), ic: (a.ic || null),
        verdict: (a.verdict || null), real: !!a.real };   // real=true 仅当经 syncArchive 自后端 /cards/list
    }
  }
  return null;
}

// ───────── 校场料接真:把真经验卡(/cards/list)+ 真因子(/factorlib/list)幂等 merge 进 GL ─────────
// 只增不改:已存在的 id(seed / 经验卡模块写入)一律跳过,绝不覆盖。Foundry 抽屉读 GL.all 自动显示真料。
async function syncArchive() {
  const API = (window.GUANLAN_BACKEND || '');
  if (!API || !window.GL) return { card: 0, factor: 0 };
  let nc = 0, nf = 0;
  try {
    const r = await fetch(API + '/cards/list?status=all');
    if (r.ok) {
      const j = await r.json();
      (j.cards || []).forEach(c => {
        if (!c || !c.id || c.status === 'rejected' || window.GL.get(c.id)) return;   // 驳回卡不进料库(P2 收口)
        window.GL.put({ id: c.id, type: 'card', title: c.title, cat: c.cat, tags: c.tags || [],
          verdict: c.verdict, conf: c.conf, ic: c.ic, expr: c.expr, insight: c.insight,
          src: c.src, status: c.status || 'approved', refs: c.refs || [], real: true });
        nc++;
      });
    }
  } catch (e) {}
  try {
    const r = await fetch(API + '/factorlib/list');
    if (r.ok) {
      const j = await r.json();
      (j.factors || []).forEach(f => {
        if (!f || !f.name) return;                              // f.name 形如 lib_turnover_cv20,天然唯一
        const icS = (f.ic != null ? String(f.ic) : '');         // P2-E:store 现已下发验证快照 RankIC
        const prev = window.GL.get(f.name);
        if (prev && prev.expr === f.expr && (prev.ic || '') === icS) return;   // 无变化不重写(防每载 39 次写盘)
        window.GL.put({ id: f.name, type: 'factor', title: f.name, expr: f.expr, ic: icS,
          family: f.family, source: f.source, status: (f.valid ? 'validated' : 'draft'),
          insight: f.description || '', real: true });
        nf++;
      });
    }
  } catch (e) {}
  return { card: nc, factor: nf };
}

// ───────── ③a 今日因子(/watch/signal_pack):按 code 取真今日因子向量,供实盘决策卡 ─────────
let _signalPack = null;                            // 缓存整包(今日全市场),只拉一次
function loadSignalPack() {
  if (_signalPack) return _signalPack;
  const API = (window.GUANLAN_BACKEND || '');
  if (!API) return Promise.resolve(null);
  _signalPack = fetch(API + '/watch/signal_pack')
    .then(r => r.ok ? r.json() : null)
    .then(j => {
      if (!j || !j.ok || !j.rows) return null;
      const map = {};
      j.rows.forEach(row => { if (row && row.code) map[String(row.code).toUpperCase()] = row; });
      return { date: j.date || null, map };
    })
    .catch(() => null);
  return _signalPack;
}
async function fetchSignalRow(code) {
  const pack = await loadSignalPack();
  if (!pack) return null;
  const row = pack.map[prefixCode(code).toUpperCase()];
  if (!row) return null;
  const r1 = (v) => (v == null ? null : Math.round(v));
  return { date: pack.date, code: row.code,
    combo: r1(row.combo_pct), fmPct: r1(row.fm_pct),
    fmCluster: (row.fm_cluster != null ? row.fm_cluster : null),
    lgbRank: (row.lgb_rank != null ? row.lgb_rank : null), v4: (row.v4_rating || null) };
}

// ───────── ③b 复盘因子(/seats/factors):按 (code, 历史日 D) 取真因子 ─────────
//   价量/表达式因子 34 项 = PIT 真算(后端 end=D 含当日收盘);模型因子(fm/combo/lgb/v4)走
//   signal_pack 旁路,历史 D 多半无 → null(modelAvailable=false),UI 回退 mock + 诚实降级。
async function fetchSeatFactors(code, date) {
  const API = (window.GUANLAN_BACKEND || '');
  if (!API || !code || !date) return null;
  const d = String(date).slice(0, 10);
  try {
    const r = await fetch(API + '/seats/factors?code=' + encodeURIComponent(code) + '&date=' + encodeURIComponent(d));
    if (!r.ok) return null;
    const j = await r.json();
    if (!j || !j.ok) return null;
    const m = j.model || {};
    const r1 = (v) => (v == null ? null : Math.round(v));
    return {
      date: j.date || d, code: j.code || code,
      combo: r1(m.combo_pct), fmPct: r1(m.fm_pct),
      fmCluster: (m.fm_cluster != null ? Math.round(m.fm_cluster) : null),
      lgbRank: (m.lgb_rank != null ? Math.round(m.lgb_rank) : null),
      v4: (m.v4_rating || null),
      factors: (j.factors || null),         // 34 价量因子真值(PIT, end=D)
      modelAvailable: !!m.available,         // 模型因子命中?(历史 = FM backfill 缓存命中)
      lookahead: !!m.lookahead,              // FM/combo 为 W11 模型 look-ahead(D≤2026-04-15)
    };
  } catch (e) { return null; }
}
const _seatFactorsCache = {};               // (code@date)→Promise,避免重复拉(复盘只读、值不变)
function fetchSeatFactorsCached(code, date) {
  const key = (prefixCode(code) || code) + '@' + String(date).slice(0, 10);
  if (!_seatFactorsCache[key]) _seatFactorsCache[key] = fetchSeatFactors(code, date);
  return _seatFactorsCache[key];
}

// ───────── ⑤ 席位真决策(/seats/decide):on-demand 调真 LLM(deepseek)综合 因子+卡+研报+市况 研判这一笔 ─────────
//   区别于 K 线上的 scanSeat 价量启发式标记(回放骨架);这里是真模型推理(秒级、要等)。
async function seatDecide(payload) {
  const API = (window.GUANLAN_BACKEND || '');
  if (!API || !payload || !payload.code || !payload.date) return null;
  try {
    const r = await fetch(API + '/seats/decide', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    if (!r.ok) return { error: 'HTTP ' + r.status };
    const j = await r.json();
    if (!j) return null;
    if (!j.ok) return { error: (j.reason || '后端拒绝') };
    return j;   // {ok, direction, confidence, rationale, key_evidence, model_name, asof, factors, model}
  } catch (e) { return { error: String(e) }; }
}

// ───────── ②b agent 条件单(/seats/order):LLM 按席位信条 + 真实时上下文出一张触发单 ─────────
//   返回 {ok, code, name, seat, seat_cn, asof, model_name, ctx, order:{side,triggers,logic,stop,take,note,validity}}。
//   triggers 已服务端校验(kind∈price/volRatio/maDiff20/rsi14),可直接喂 lzRunTriggerReplay / lzEvalTrigger。无后端/失败 → null。
async function seatOrder(code, seat, tf, hold, extra) {
  const API = (window.GUANLAN_BACKEND || '');
  if (!API || !code) return null;
  try {
    let url = API + '/seats/order?code=' + encodeURIComponent(code) + '&seat=' + encodeURIComponent(seat || 'momentum') + '&tf=' + encodeURIComponent(tf || 'day');
    if (hold && hold.entry != null) {   // 仅在有值时附参:空 hold_days= 会让后端 int 解析 422
      url += '&hold_entry=' + encodeURIComponent(hold.entry);
      if (hold.since) url += '&hold_since=' + encodeURIComponent(hold.since);
      if (hold.days != null) url += '&hold_days=' + encodeURIComponent(hold.days);
    }
    if (extra) {   // 第3期:策略实例的信条 + 配方首卡洞见 + 落盘标识(空值不附)
      if (extra.creed) url += '&creed=' + encodeURIComponent(extra.creed);
      if (extra.note) url += '&note=' + encodeURIComponent(extra.note);
      if (extra.strategy_id) url += '&strategy_id=' + encodeURIComponent(extra.strategy_id);
      if (extra.strategy_name) url += '&strategy_name=' + encodeURIComponent(extra.strategy_name);
      if (extra.date) url += '&date=' + encodeURIComponent(extra.date);   // 复盘 PIT:按游标历史日思考(实盘留空→走实时)
    }
    const r = await fetch(url);
    if (!r.ok) return null;
    const j = await r.json();
    if (!j || !j.ok) return null;
    return j;
  } catch (e) { return null; }
}

// ───────── run 化 + 实盘台账(2026-06-12 重排:回测历史/仓位台账 后端封装)─────────
//   一次「真跑」= 一个 run(run 头由前端跑完注册,逐笔决策由后端 decide 落盘时带 run_id)。
//   台账 = 实盘仓位事件流(后端 /seats/ledger 持久化)。全部诚实降级:无后端/失败 → []/null。
async function runsList(code) {
  const API = (window.GUANLAN_BACKEND || '');
  if (!API || !code) return [];
  try {
    const bare = String(code).replace(/^(SH|SZ|BJ)/i, '');
    const r = await fetch(API + '/seats/runs?code=' + encodeURIComponent(bare) + '&limit=30');
    if (!r.ok) return [];
    const j = await r.json();
    return (j && j.ok && Array.isArray(j.runs)) ? j.runs : [];
  } catch (e) { return []; }
}
async function runDecisions(runId) {
  const API = (window.GUANLAN_BACKEND || '');
  if (!API || !runId) return [];
  try {
    const r = await fetch(API + '/seats/decisions?run_id=' + encodeURIComponent(runId) + '&limit=300');
    if (!r.ok) return [];
    const j = await r.json();
    if (!j || !j.ok || !Array.isArray(j.decisions)) return [];
    return j.decisions.slice().reverse();   // 后端逆序(最新在前)→ 时间正序,方便上图
  } catch (e) { return []; }
}
async function ledgerState() {
  const API = (window.GUANLAN_BACKEND || '');
  if (!API) return null;
  try {
    const r = await fetch(API + '/seats/ledger/state');
    if (!r.ok) return null;
    const j = await r.json();
    return j || null;
  } catch (e) { return null; }
}
// 事后 TCA(执行质量):GET /seats/tca —— 重放台账逐笔成交 vs 当日基准(VWAP/开/收/到达价)成本 bps。
// 无后端/失败/404 → null(消费端隐藏 TCA 卡,诚实降级,绝不画假成本)。
async function seatsTca() {
  const API = (window.GUANLAN_BACKEND || '');
  if (!API) return null;
  try {
    const r = await fetch(API + '/seats/tca');
    if (!r.ok) return null;
    const j = await r.json();
    return j || null;
  } catch (e) { return null; }
}
async function ledgerPost(ev) {
  const API = (window.GUANLAN_BACKEND || '');
  if (!API || !ev) return null;
  try {
    const r = await fetch(API + '/seats/ledger', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(ev),
    });
    // 422 也返回 {ok:false,reason}(校验拒绝要显形,如「现金不足」),网络层失败才 null
    const j = await r.json();
    return j || null;
  } catch (e) { return null; }
}
async function runsClear(code) {
  // 「清空回测历史」:后端 append 水位标记(不删不改历史行);code 空 = 全局清空
  const API = (window.GUANLAN_BACKEND || '');
  if (!API) return null;
  try {
    const bare = String(code || '').replace(/^(SH|SZ|BJ)/i, '');
    const r = await fetch(API + '/seats/runs/clear', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ code: bare }),
    });
    const j = await r.json();
    return j || null;
  } catch (e) { return null; }
}
function runIdGen() { return 'run_' + Date.now().toString(36) + Math.random().toString(36).slice(2, 6); }

// 回测净值 = 把一次 run 的**真实买卖决策**模拟成交(口径:买入信号→满仓持有,卖出信号→清仓,
//   观望→维持;无杠杆无成本)。这是「真实回测」的诚实净值——区别于 scanSeat 启发式演示。
//   runDecs:[{idx(已映射到 bars),side:'buy'|'sell'}] ;bars:[{c:收盘}]。无买卖信号 → 返回 null
//   (纯观望 = 从未建仓 = 没有回测净值可言,诚实空)。决策日**之前**的 bar 净值留 NaN(未参与,不画)。
//   P3 双线:useHybrid=true 时按**混合方向**(d.hybrid_direction,后端 w>0 时 (1-w)·LLM分+w·因子z分定的方向)派生 side;
//   false(缺省)沿用既有 d.side(由 LLM direction 派生),逐字向后兼容。w=0 时后端 hybrid_direction==direction → 两线必重合。
function runBacktest(runDecs, bars, useHybrid) {
  if (!bars || !bars.length || !runDecs || !runDecs.length) return null;
  const sideByIdx = {};
  let firstSig = Infinity;
  runDecs.forEach(d => {
    if (!d || !(d.idx >= 0)) return;
    // 派生 side:混合线读 hybrid_direction(/买/→buy /卖/→sell 余 watch);纯LLM线用既有 d.side(行为不变)
    let side = d.side;
    if (useHybrid) { const hd = String(d.hybrid_direction || d.direction || ''); side = /买/.test(hd) ? 'buy' : (/卖/.test(hd) ? 'sell' : 'watch'); }
    if (side === 'buy' || side === 'sell') {
      sideByIdx[d.idx] = side; firstSig = Math.min(firstSig, d.idx);
    }
  });
  if (!isFinite(firstSig)) return null;                       // 全程观望 → 无回测净值
  const n = bars.length;
  const eq = new Array(n).fill(1);                             // 建仓前 = 持现金,平线 1.0(诚实未参与)
  const trades = [];
  let pos = 0, entryPx = 0, entryIdx = -1, cash = 1, shares = 0;
  for (let i = firstSig; i < n; i++) {
    const px = bars[i] && +bars[i].c;
    const sig = sideByIdx[i];
    if (sig === 'buy' && pos === 0 && px > 0) {
      pos = 1; entryPx = px; entryIdx = i; shares = cash / px; cash = 0;
    } else if (sig === 'sell' && pos === 1 && px > 0) {
      cash = shares * px;
      trades.push({ entry: entryPx, exit: px, ret: px / entryPx - 1, in: entryIdx, out: i });
      pos = 0; shares = 0;
    }
    eq[i] = (px > 0) ? (pos === 1 ? shares * px : cash) : (i > 0 ? eq[i - 1] : 1);
  }
  if (pos === 1 && entryIdx >= 0) {
    const px = +bars[n - 1].c;
    trades.push({ entry: entryPx, exit: px, ret: px / entryPx - 1, in: entryIdx, out: n - 1, openEnd: true });
  }
  const eqSeg = eq.slice(firstSig);                            // 指标只算建仓后段(空仓期不计入波动)
  if (eqSeg.length < 2) return null;
  return { eq, eqSeg, trades, firstSig, metrics: metricsOf(eqSeg, trades, 'day') };
}

// ②搬实盘 实时触发上下文(/seats/live_eval):盘中真现价+高低+MA/RSI/量比,喂同一 evalTrigger 做实时盯盘。无后端/失败 → null。
async function fetchLiveEval(code, tf) {
  const API = (window.GUANLAN_BACKEND || '');
  if (!API || !code) return null;
  try {
    const r = await fetch(API + '/seats/live_eval?code=' + encodeURIComponent(code) + '&tf=' + encodeURIComponent(tf || 'day'));
    if (!r.ok) return null;
    const j = await r.json();
    if (!j || !j.ok) return null;
    return j;
  } catch (e) { return null; }
}

// ───────── 条件单 + 触发引擎(阶段2:纯函数,live 与回测共用同一套)─────────
//   条件单 = 触发规格:到价 / 放量 / 技术指标条件 → 满足即"落子"。引擎对单根 bar 的"触发上下文 ctx"判定;
//   ctx 由调用方构造:回测 = cursor 处真 K 算指标(buildTriggerCtx);live = /seats/live_eval 真实时(同字段)。
//   **PIT 安全**:只用该 bar 及之前数据;price 用 high/low 判 intrabar 触碰(条件单/止损单成交语义)。
//   条件单:{ id, code, seat, side:'buy'|'sell', triggers:[{kind,op,value}], logic:'AND'|'OR',
//            stop, take, note, createdIdx, expiryIdx, status:'armed'|'triggered'|'expired' }
//   kind: 'price'(到价·intrabar) | 'close'(收盘价) | 'volRatio'(量比=v/10日均量) |
//         'maDiff20'(收/MA20-1) | 'rsi14' | 'ma5' | 'ma20' | 'ret5';  op: '>=' '<=' '>' '<'
function rsiOf(bars, period, i, field) {
  field = field || 'c';
  if (i < period) return null;
  let up = 0, dn = 0;
  for (let k = i - period + 1; k <= i; k++) {
    const ch = bars[k][field] - bars[k - 1][field];
    if (ch >= 0) up += ch; else dn -= ch;
  }
  const au = up / period, ad = dn / period;
  if (ad === 0) return au === 0 ? 50 : 100;
  return +(100 - 100 / (1 + au / ad)).toFixed(2);
}
// 触发上下文:从任意 bar 序列(5min 或日线)第 i 根算出引擎要判的全部量(PIT,≤i)。
function buildTriggerCtx(bars, i) {
  if (!bars || i < 0 || i >= bars.length) return null;
  const b = bars[i];
  const ma5 = sma(bars, 5, 'c', i), ma20 = sma(bars, 20, 'c', i);
  const vm = volMA(bars, 10, i);
  return {
    idx: i, date: b.date,
    price: b.c, close: b.c, open: b.o, high: b.h, low: b.l, vol: b.v,
    ma5: ma5 != null ? +ma5.toFixed(3) : null,
    ma20: ma20 != null ? +ma20.toFixed(3) : null,
    maDiff20: (ma20 ? +(b.c / ma20 - 1).toFixed(4) : null),
    rsi14: rsiOf(bars, 14, i, 'c'),
    volRatio: (vm ? +(b.v / vm).toFixed(3) : null),
    ret5: ret5(bars, i),
  };
}
function _condMet(cond, ctx) {
  const op = cond.op || '>=';
  const cmp = (a, b) => op === '>=' ? a >= b : op === '<=' ? a <= b : op === '>' ? a > b : op === '<' ? a < b : false;
  if (cond.kind === 'price') {
    // 到价 intrabar 触碰:'>=' 看最高触(向上突破/止盈)、'<=' 看最低触(跌破/超跌/止损)
    if (op === '>=' || op === '>') return ctx.high != null && cmp(ctx.high, cond.value);
    return ctx.low != null && cmp(ctx.low, cond.value);
  }
  const v = ctx[cond.kind];
  return v != null && cmp(v, cond.value);
}
// 触发引擎(纯函数):一张 armed 条件单 + 一根 bar 的 ctx → 是否触发 + 成交价 + 命中明细。
function evalTrigger(order, ctx) {
  if (!order || !ctx || order.status !== 'armed') return { triggered: false };
  const triggers = order.triggers || [];
  if (!triggers.length) return { triggered: false };
  const met = triggers.map(c => ({ c: c, ok: _condMet(c, ctx) }));
  const logic = (order.logic || 'AND').toUpperCase();
  const triggered = logic === 'OR' ? met.some(m => m.ok) : met.every(m => m.ok);
  if (!triggered) return { triggered: false, met: met };
  const pc = triggers.find(c => c.kind === 'price');   // 到价成交用触发价,否则用现价
  const fill = pc ? pc.value : ctx.price;
  return { triggered: true, fill: +(+fill).toFixed(2), at: ctx.date, idx: ctx.idx, met: met };
}
// 回放验证 harness:一组条件单按 bar 顺序跑过真 K 序列,首次触发即 fire(之后置 triggered 不重复)。
//   live 端将用同一 evalTrigger,只是 ctx 来自 live_eval、逐 poll/tick 喂入 —— 同一套引擎。
function runTriggerReplay(bars, orders, fromIdx) {
  const start = Math.max(20, fromIdx || 20);            // 留指标预热
  const live = (orders || []).map(o => Object.assign({}, o, { status: o.status || 'armed' }));
  const fired = [];
  for (let i = start; i < bars.length; i++) {
    const ctx = buildTriggerCtx(bars, i);
    if (!ctx) continue;
    for (const o of live) {
      if (o.status !== 'armed') continue;
      if (o.expiryIdx != null && i > o.expiryIdx) { o.status = 'expired'; continue; }
      const r = evalTrigger(o, ctx);
      if (r.triggered) {
        o.status = 'triggered';
        fired.push({ id: o.id, seat: o.seat, side: o.side, fill: r.fill, at: r.at, idx: r.idx,
          ctx: { price: ctx.price, high: ctx.high, low: ctx.low, ma20: ctx.ma20, maDiff20: ctx.maDiff20, rsi14: ctx.rsi14, volRatio: ctx.volRatio } });
      }
    }
  }
  return { fired: fired, orders: live };
}

// ───────── B1 影子组合(最小诚实版,当前票口径;localStorage 持久)─────────
const LZ_SHADOW_KEY = (code) => 'guanlan:lz:shadow:' + code;
function shadowLoad(code) {
  try { const s = JSON.parse(localStorage.getItem(LZ_SHADOW_KEY(code)) || 'null');
    if (s && Array.isArray(s.positions)) return { goLive: s.goLive || null, positions: s.positions }; } catch (e) {}
  return { goLive: null, positions: [] };
}
function shadowSave(code, shadow) {
  try { localStorage.setItem(LZ_SHADOW_KEY(code), JSON.stringify(shadow)); } catch (e) {}
  return shadow;
}
// 进场:买入触发 → 记一笔 open 影子持仓(按信号价 fill)。非买入/无 goLive/重复 id → 不记。
function shadowAddEntry(shadow, ev) {
  if (!shadow.goLive) return shadow;
  const side = ev.side || '';
  if (!/买/.test(side)) return shadow;                       // 本期只进场买入
  const id = ev.id + '·' + ev.at;                            // 去重键(同一触发只记一次)
  if (shadow.positions.some(p => p.id === id)) return shadow;
  const entry = +ev.fill;
  if (!isFinite(entry)) return shadow;
  const pos = { id, seat: ev.seat, side: '买入', entry, date: String(ev.at).slice(0, 10),
    stop: (ev.stop != null && isFinite(+ev.stop)) ? +ev.stop : null,
    take: (ev.take != null && isFinite(+ev.take)) ? +ev.take : null,
    status: 'open', exit: null, exitDate: null, exitReason: null };
  return { goLive: shadow.goLive, positions: shadow.positions.concat([pos]) };
}
// 出场:对 open 持仓,按现价查 止盈(price≥take)/止损(price≤stop)。返回 {shadow, changed}。
function shadowCheckExits(shadow, price, asofDate) {
  if (price == null || !isFinite(+price)) return { shadow, changed: false };   // null 价不判出场(+null=0 会绕过 isFinite)
  let changed = false;
  const positions = shadow.positions.map(p => {
    if (p.status !== 'open') return p;
    if (p.take != null && price >= p.take) { changed = true; return Object.assign({}, p, { status: 'closed', exit: p.take, exitDate: asofDate || null, exitReason: '止盈' }); }
    if (p.stop != null && price <= p.stop) { changed = true; return Object.assign({}, p, { status: 'closed', exit: p.stop, exitDate: asofDate || null, exitReason: '止损' }); }
    return p;
  });
  return { shadow: changed ? { goLive: shadow.goLive, positions } : shadow, changed };
}
// 指标:已平按 (exit-entry)/entry 复利成累计净值;未平按现价 mark-to-market 计浮动。
function shadowMetrics(shadow, price) {
  const closed = shadow.positions.filter(p => p.status === 'closed');
  const open = shadow.positions.filter(p => p.status === 'open');
  const rOf = (p, px) => (p.entry ? (px - p.entry) / p.entry : 0);     // 买入方向
  let eq = 1; closed.forEach(p => { eq *= (1 + rOf(p, p.exit)); });     // 已平复利
  const realized = eq - 1;
  const wins = closed.filter(p => rOf(p, p.exit) > 0);
  const losses = closed.filter(p => rOf(p, p.exit) <= 0);
  const avg = a => a.length ? a.reduce((x, y) => x + y, 0) / a.length : 0;
  const aw = avg(wins.map(p => rOf(p, p.exit)));
  const al = Math.abs(avg(losses.map(p => rOf(p, p.exit))));
  const unreal = (price != null && isFinite(+price) && open.length) ? avg(open.map(p => rOf(p, +price))) : 0;  // 未平浮动(null 价不 MTM,+null=0 会绕过 isFinite)
  const equityNow = eq * (1 + unreal) - 1;                              // 含浮动的当前累计
  return {
    goLive: shadow.goLive,
    nOpen: open.length, nClosed: closed.length,
    realized, equityNow, unreal,
    winRate: closed.length ? wins.length / closed.length : null,
    plRatio: al ? aw / al : (aw ? 99 : null),
  };
}

// 研判平仓:按 id 把 open 持仓按现价平(reason 默认 '研判平')。返回 {shadow, changed}。
function shadowClose(shadow, posId, price, asofDate, reason) {
  if (price == null || !isFinite(+price)) return { shadow, changed: false };   // null 价不平(+null=0 会绕过 isFinite)
  let changed = false;
  const positions = shadow.positions.map(p => {
    if (p.status === 'open' && p.id === posId) { changed = true; return Object.assign({}, p, { status: 'closed', exit: +price, exitDate: asofDate || null, exitReason: reason || '研判平' }); }
    return p;
  });
  return { shadow: changed ? { goLive: shadow.goLive, positions } : shadow, changed };
}

// ── 跨票聚合(组合级影子绩效)──
// 扫 localStorage 全部影子台账(键前缀 guanlan:lz:shadow:),返回 [{code, shadow}](坏数据静默跳过)。
function shadowListAll() {
  const out = [];
  try {
    const prefix = 'guanlan:lz:shadow:';
    for (let i = 0; i < localStorage.length; i++) {
      const key = localStorage.key(i);
      if (!key || key.indexOf(prefix) !== 0) continue;
      const code = key.slice(prefix.length);
      if (!code) continue;
      try {
        const s = JSON.parse(localStorage.getItem(key) || 'null');
        if (s && Array.isArray(s.positions)) out.push({ code, shadow: { goLive: s.goLive || null, positions: s.positions } });
      } catch (e) {}
    }
  } catch (e) {}
  return out;
}
// 组合聚合:已实现/笔数/胜率/盈亏比 跨所有票精确(无需现价);浮动仅对 priceMap 里有真现价的持仓 MTM,
//   其余诚实不计入(返回 covered/nOpen 让 UI 标覆盖率)。priceMap = {code: 现价}。books = shadowListAll()。
function shadowAggregate(books, priceMap) {
  const pm = priceMap || {};
  const rOf = (p, px) => (p.entry ? (px - p.entry) / p.entry : 0);   // 买入方向
  const avg = a => a.length ? a.reduce((x, y) => x + y, 0) / a.length : 0;
  const closed = [], open = [];
  let nTracked = 0; let goLive = null;
  (books || []).forEach(b => {
    const sh = b.shadow || {};
    if (sh.goLive) { nTracked++; if (!goLive || sh.goLive < goLive) goLive = sh.goLive; }   // 最早上线日
    (sh.positions || []).forEach(p => {
      if (p.status === 'closed') closed.push(p);
      else if (p.status === 'open') open.push({ pos: p, code: b.code });
    });
  });
  const rClosed = closed.map(p => rOf(p, p.exit));
  const wins = rClosed.filter(r => r > 0);
  const losses = rClosed.filter(r => r <= 0);
  const aw = avg(wins), al = Math.abs(avg(losses));
  const covered = open.filter(o => { const px = pm[o.code]; return px != null && isFinite(+px); });
  const unrealReturns = covered.map(o => rOf(o.pos, +pm[o.code]));
  return {
    goLive,
    nTracked,                                   // 跟踪票数(有 goLive 的台账)
    nOpen: open.length, nClosed: closed.length,
    realized: avg(rClosed),                     // 已实现:跨票等权·每笔均收益(精确)
    unreal: covered.length ? avg(unrealReturns) : null,   // 浮动:仅有真现价的持仓均(无覆盖→null)
    covered: covered.length,                    // 已按现价 MTM 的持仓数
    winRate: closed.length ? wins.length / closed.length : null,
    plRatio: al ? aw / al : (aw ? 99 : null),
  };
}

Object.assign(window, {
  LZ_SEATS: SEATS, LZ_SYMBOLS: SYMBOLS, LZ_SYMBOL_META: SYMBOL_META, LZ_PRIMARY: PRIMARY_CODE,
  lzConsensusEquity: consensusEquity, lzMetricsOf: metricsOf, lzRegimeAt: regimeAt,
  lzSma: sma, lzFrameData: frameData,
  lzBuildSymbolFromBars: buildSymbolFromBars, lzFetchDailyBars: fetchDailyBars,
  lzFetchBenchmark: fetchBenchmark,
  lzFetchBars5: fetchBars5, lzFetchRealtimeBars5: fetchRealtimeBars5, lzFetchRealtimeBars1: fetchRealtimeBars1, lzFetchMarketStatus: fetchMarketStatus,
  lzFetchReportStatus: fetchReportStatus, lzSeatResearch: seatResearch, lzSeatCard: seatCard,
  lzSyncArchive: syncArchive, lzFetchSignalRow: fetchSignalRow,
  lzFetchSeatFactors: fetchSeatFactorsCached, lzSeatDecide: seatDecide,
  lzFetchQuote: fetchQuote, lzFetchOrderbook: fetchOrderbook, lzFetchTicks: fetchTicks,
  lzLivebarLoad: livebarLoad, lzLivebarSave: livebarSave, lzLivebarClear: livebarClear, lzLivebarFromQuote: livebarFromQuote,
  lzBuildTriggerCtx: buildTriggerCtx, lzEvalTrigger: evalTrigger,
  lzRunTriggerReplay: runTriggerReplay, lzRsiOf: rsiOf, lzSeatOrder: seatOrder, lzFetchLiveEval: fetchLiveEval,
  lzRunsList: runsList, lzRunDecisions: runDecisions, lzLedgerState: ledgerState, lzLedgerPost: ledgerPost, lzSeatsTca: seatsTca, lzRunId: runIdGen, lzRunsClear: runsClear, lzRunBacktest: runBacktest,
  lzBars30: bars30, lzMapDecsToFrame: mapDecsToFrame,
  lzFetchNews: fetchNews, lzMapNewsToFrame: mapNewsToFrame,
  lzShadowLoad: shadowLoad, lzShadowSave: shadowSave, lzShadowAddEntry: shadowAddEntry,
  lzShadowCheckExits: shadowCheckExits, lzShadowMetrics: shadowMetrics, lzShadowClose: shadowClose,
  lzShadowListAll: shadowListAll, lzShadowAggregate: shadowAggregate,
  lzStrategyList: strategyList, lzStrategyGet: strategyGet, lzStrategyForCode: strategyForCode,
  lzStrategyColor: strategyColor, lzStrategySave: strategySave, lzStrategyDelete: strategyDelete,
  lzRecipeForStrategy: recipeForStrategy,
  lzPoolAdd: poolAdd, lzPoolRemove: poolRemove, lzPoolIsDynamic: poolIsDynamic,   // 盯盘池扩池
  lzPoolIsMonitored: poolIsMonitored, lzMonitoredCodes: monitoredCodes, lzMonitorAgentFor: monitorAgentFor,   // 盯盘集(校场绑定派生)
  lzWatchSet: watchSet, lzFetchWatchStatus: fetchWatchStatus, lzToggleWatch: toggleWatch,   // 后端定时盯盘(2026-07-11)
  lzFetchDecisionsTimeline: fetchDecisionsTimeline,
  lzSeedDefaultStrategy: seedDefaultStrategy, LZ_TEMPLATES: LZ_TEMPLATES, LZ_TEMPLATE_IDS: LZ_TEMPLATE_IDS,
  lzScanSeat: scanSeat, lzSeatEquity: seatEquity,
  lzHydrateRealBars: hydrateRealBars, lzHydrateRealBars5: hydrateRealBars5,
  lzRealBarsOf: realBarsOf,
  lzRealSymbolOf: realSymbolOf,
});

// 启动即把真卡/真因子 merge 进 GL(异步、失败静默;校场抽屉随后显示真料)。
try { syncArchive(); } catch (e) {}
