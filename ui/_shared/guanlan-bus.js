// 观澜 · 档案库总线 guanlan-bus.js
// 四模块共享的唯一事实源:对话/研报 · 经验卡 · 因子/工作流 · 席位/落子。
// localStorage 真持久化(刷新不丢)+ 跨标签同步 + 发布订阅 + 带上下文 handoff。
// 任意页面在自己的 app 脚本之前 <script src="guanlan-bus.js"> 即可用 window.GL。
(function () {
  const LS_KEY = 'guanlan:store:v1';
  const SEED_FLAG = 'seeded:v3';
  const HANDOFF = 'guanlan:handoff:';

  function load() { try { return JSON.parse(localStorage.getItem(LS_KEY)); } catch (e) { return null; } }
  let state = load() || { artifacts: {}, flags: {}, ts: 0 };
  const subs = [];

  function persist() {
    state.ts = Date.now();
    try { localStorage.setItem(LS_KEY, JSON.stringify(state)); } catch (e) {}
    emit();
  }
  function emit() { subs.slice().forEach(fn => { try { fn(state); } catch (e) {} }); }
  window.addEventListener('storage', e => { if (e.key === LS_KEY) { state = load() || state; emit(); } });

  let _n = 0;
  const genId = (t) => t + '_' + Date.now().toString(36) + (_n++).toString(36);

  const GL = {
    // ── 物料 CRUD ──
    all(type) { const a = Object.values(state.artifacts); return type ? a.filter(x => x.type === type) : a; },
    get(id) { return state.artifacts[id] || null; },
    byRef(id) { return Object.values(state.artifacts).filter(a => (a.refs || []).includes(id)); },
    put(a) {
      const id = a.id || genId(a.type || 'art');
      const prev = state.artifacts[id] || {};
      state.artifacts[id] = Object.assign({ refs: [], ts: Date.now() }, prev, a, { id });
      persist(); return id;
    },
    patch(id, fields) { if (state.artifacts[id]) { Object.assign(state.artifacts[id], fields); persist(); } },
    link(from, to) { const f = state.artifacts[from]; if (f) { f.refs = Array.from(new Set([...(f.refs || []), to])); persist(); } },
    remove(id) { delete state.artifacts[id]; persist(); },
    // ── 订阅 ──
    on(fn) { subs.push(fn); return () => { const i = subs.indexOf(fn); if (i >= 0) subs.splice(i, 1); }; },
    // ── 带上下文跳转(handoff)──
    // 可选 ws=帷幄会话 id → 键带 `:ws` 后缀,信箱按会话隔离;ws 空 = 裸键,独立页行为零变化。
    handoff(ch, payload, ws) { try { localStorage.setItem(HANDOFF + ch + (ws ? ':' + ws : ''), JSON.stringify({ payload, ts: Date.now() })); } catch (e) {} },
    take(ch, ws) { try { const k = HANDOFF + ch + (ws ? ':' + ws : ''); const v = localStorage.getItem(k); if (!v) return null; localStorage.removeItem(k); return JSON.parse(v).payload; } catch (e) { return null; } },
    peek(ch, ws) { try { const v = localStorage.getItem(HANDOFF + ch + (ws ? ':' + ws : '')); return v ? JSON.parse(v).payload : null; } catch (e) { return null; } },
    go(href, ch, payload) { if (ch) GL.handoff(ch, payload); window.location.href = href; },
    // ── 统计 ──
    stats() {
      const a = Object.values(state.artifacts);
      const by = (t) => a.filter(x => x.type === t).length;
      return { research: by('research'), card: by('card'), factor: by('factor'), seat: by('seat'), decision: by('decision'), strategy: by('strategy'), total: a.length };
    },
    reset() { state = { artifacts: {}, flags: {}, ts: 0 }; persist(); seed(true); },
  };

  // ───────── 初始物料图谱(与验证区 / 落子 现有数据一致)─────────
  function seed(force) {
    if (!force && state.flags && state.flags[SEED_FLAG]) return;
    const now = Date.now();
    // demo:true = 设计稿示例物料(非真实产出), 渲染端凭它打「示例」徽章与真物料区分(审计 M3)
    const A = (o) => { state.artifacts[o.id] = Object.assign({ ts: now, refs: [], demo: true }, o); };

    // 研报 / 原始素材(来源)
    A({ id: 'rs_reversal', type: 'research', title: '缩量企稳后的反转效应', kind: '研报', from: '中信证券 · 量化策略', status: 'raw' });
    A({ id: 'rs_north', type: 'research', title: '北向资金是不是聪明钱？', kind: '热帖', from: '雪球 · 量化老张', status: 'raw' });
    A({ id: 'rs_distrib', type: 'research', title: '11-12 盘后复盘 · 龙头退潮', kind: '复盘', from: '我的复盘', status: 'raw' });
    A({ id: 'rs_pead', type: 'research', title: '多家公司 Q3 业绩超预期', kind: '快讯', from: '东方财富 · 快讯', status: 'raw' });

    // 因子 / 工作流
    A({ id: 'fa_reversal', type: 'factor', title: '缩量反转因子', expr: '-rank(ts_sum(ret,5)) · (vol_ratio < 0.7)', ic: '0.043', status: 'validated' });
    A({ id: 'fa_north', type: 'factor', title: '北向动量因子', expr: 'rank(ts_sum(north_hold_chg,3))', ic: '0.031', status: 'validated' });
    A({ id: 'fa_pead', type: 'factor', title: 'PEAD 漂移因子', expr: 'rank(eps_surprise) · hold(60d)', ic: '0.052', status: 'validated' });
    A({ id: 'fa_distrib', type: 'factor', title: '退潮风控因子', expr: '-(near_60d_high & vol_ratio>2 & ret_1d<0.02)', ic: '0.028', status: 'validated' });

    // 经验卡(炼自研报,经工作流验证)
    A({ id: 'card_reversal', type: 'card', title: '缩量企稳反转', cat: '价量', tags: ['反转', '缩量', '周频'], verdict: '通过', conf: 76, ic: '0.043',
      insight: '超跌后缩量企稳,3 日内反转概率显著抬升;震荡市、周频最有效,但信号衰减快。', expr: '-rank(ts_sum(ret,5)) · (vol_ratio < 0.7)', status: 'validated', refs: ['rs_reversal', 'fa_reversal'] });
    A({ id: 'card_north', type: 'card', title: '北向资金领先', cat: '资金', tags: ['北向', '资金流', '中频'], verdict: '存疑', conf: 61, ic: '0.031',
      insight: '北向连续净买入的板块 5 日后相对收益占优;蓝筹更显著,个股层面噪音大需聚合到板块。', expr: 'rank(ts_sum(north_hold_chg,3))', status: 'validated', refs: ['rs_north', 'fa_north'] });
    A({ id: 'card_pead', type: 'card', title: '业绩超预期漂移 PEAD', cat: '基本面', tags: ['基本面', '事件', '中频'], verdict: '通过', conf: 84, ic: '0.052',
      insight: '业绩超一致预期后存在约 40–60 日价格漂移;事件驱动叠加基本面因子更稳。', expr: 'rank(eps_surprise) · hold(60d)', status: 'validated', refs: ['rs_pead', 'fa_pead'] });
    A({ id: 'card_distrib', type: 'card', title: '高位放量滞涨退潮', cat: '情绪', tags: ['情绪', '风控', '顶部'], verdict: '存疑', conf: 57, ic: '0.028',
      insight: '创新高后放量但涨幅收敛,常为退潮前兆;应提前降权止盈,而非追高。', expr: '-(near_60d_high & vol_ratio>2 & ret_1d<0.02)', status: 'validated', refs: ['rs_distrib', 'fa_distrib'] });
    // 知识库里已沉淀、暂未配席的卡(供校场装配挑选)
    A({ id: 'card_diverge', type: 'card', title: '量价背离顶钝化', cat: '价量', tags: ['技术', '风控'], verdict: '通过', conf: 71, ic: '0.036', insight: '价创新高而量能/动量背离,顶部钝化信号。', expr: 'rank(price)-rank(mom)', status: 'validated', refs: [] });
    A({ id: 'card_smallcap', type: 'card', title: '小市值动量切换', cat: '风格', tags: ['风格', '动量'], verdict: '通过', conf: 66, ic: '0.041', insight: '风格切换期小市值动量占优。', expr: 'rank(-mktcap)·rank(mom_20)', status: 'validated', refs: [] });

    // 席位(落子) — 各引用一张经验卡 + 因子
    A({ id: 'seat_reversal', type: 'seat', title: '反转席', glyph: '反', creed: '超跌缩量企稳即落子,搏短线反弹', status: 'deployed', refs: ['card_reversal', 'fa_reversal'] });
    A({ id: 'seat_momentum', type: 'seat', title: '动量席', glyph: '动', creed: '突破均线、量价齐升则顺势加仓', status: 'deployed', refs: ['card_north', 'fa_north'] });
    A({ id: 'seat_event', type: 'seat', title: '事件驱动席', glyph: '事', creed: '业绩超预期后博 60 日漂移', status: 'deployed', refs: ['card_pead', 'fa_pead'] });
    A({ id: 'seat_risk', type: 'seat', title: '风控席', glyph: '险', creed: '高位放量滞涨即减仓止盈,守住回撤', status: 'deployed', refs: ['card_distrib', 'fa_distrib'] });

    state.flags = state.flags || {};
    state.flags[SEED_FLAG] = true;
    persist();
  }
  seed(false);

  // 迁移: seed 只跑一次 → 老 localStorage 里的示例物料没有 demo 标, 按已知 seed id 补打。
  // 用户真物料 id 形如 card_user_* / strat_*, 不在清单内, 绝不误标。
  (function migrateDemoFlag() {
    const SEED_IDS = ['rs_reversal', 'rs_north', 'rs_distrib', 'rs_pead',
      'fa_reversal', 'fa_north', 'fa_pead', 'fa_distrib',
      'card_reversal', 'card_north', 'card_pead', 'card_distrib', 'card_diverge', 'card_smallcap',
      'seat_reversal', 'seat_momentum', 'seat_event', 'seat_risk'];
    let dirty = false;
    SEED_IDS.forEach((id) => { const a = state.artifacts[id]; if (a && a.demo !== true) { a.demo = true; dirty = true; } });
    if (dirty) persist();
  })();

  // ── 后端持久桥(互通审计 P2-C):strategy/research/decision 三类真物料镜像到 /archive ──
  // 总线本体仍是 localStorage(同步、零依赖、唯一事实源);后端只是防清缓存丢失的影子库。
  // 首拍:拉 /archive/list 合并本地缺失 id(本地优先,绝不覆盖)+ 把本地有/服务端无的上推(存量回填);
  // 此后 put/patch/remove 对三类型 fire-and-forget 上推。file:// 或后端不在 → 全静默跳过(行为同旧版)。
  const SYNC_TYPES = { strategy: 1, research: 1, decision: 1 };
  function _api() {
    try {
      return window.GUANLAN_BACKEND
        || ((location.protocol === 'http:' || location.protocol === 'https:') ? location.origin : null);
    } catch (e) { return null; }
  }
  function _push(a) {
    const api = _api();
    if (!api || !a || !SYNC_TYPES[a.type] || a.demo) return;
    try {
      fetch(api + '/archive/put', { method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ artifact: a }) }).catch(function () {});
    } catch (e) {}
  }
  function _pushRemove(id, type) {
    const api = _api();
    if (!api || !SYNC_TYPES[type || '']) return;
    try {
      fetch(api + '/archive/remove', { method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ id: id }) }).catch(function () {});
    } catch (e) {}
  }
  const _put0 = GL.put, _patch0 = GL.patch, _remove0 = GL.remove;
  GL.put = function (a) { const id = _put0(a); _push(state.artifacts[id]); return id; };
  GL.patch = function (id, fields) { _patch0(id, fields); if (state.artifacts[id]) _push(state.artifacts[id]); };
  GL.remove = function (id) { const t = (state.artifacts[id] || {}).type; _remove0(id); _pushRemove(id, t); };
  setTimeout(function () {            // 延后一拍:bus 先于页面内联脚本加载,GUANLAN_BACKEND 此时才就绪
    const api = _api();
    if (!api) return;
    try {
      fetch(api + '/archive/list').then(function (r) { return r.ok ? r.json() : null; }).then(function (j) {
        if (!j || !j.ok || !j.items) return;
        const have = {};
        let dirty = false;
        j.items.forEach(function (a) {
          if (!a || !a.id) return;
          have[a.id] = 1;
          if (!SYNC_TYPES[a.type] || state.artifacts[a.id]) return;   // 本地优先,只补缺
          state.artifacts[a.id] = a; dirty = true;
        });
        Object.values(state.artifacts).forEach(function (a) {          // 存量回填:本地有、影子库无 → 上推
          if (a && SYNC_TYPES[a.type] && !a.demo && !have[a.id]) _push(a);
        });
        if (dirty) persist();
      }).catch(function () {});
    } catch (e) {}
  }, 0);

  window.GL = GL;
  window.GuanlanBus = GL;
})();
