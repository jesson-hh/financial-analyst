// 观澜 · 选股 — 数据内核 (股票池 + 因子元数据 + 选股引擎)
// 纯 JS / 无 React。先于 app 加载,挂到 window。
// 引擎是真的: 选因子→截面 z 打分→约束筛选→行业中性→TopN→定权重→组合统计。

(function () {
  // ───────── 确定性随机 (mulberry32) ─────────
  function mulberry32(a) {
    return function () {
      a |= 0; a = (a + 0x6D2B79F5) | 0;
      let t = Math.imul(a ^ (a >>> 15), 1 | a);
      t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
  }
  // 用代码做种子,保证每只票每次取值一致
  function seedOf(code) { let h = 2166136261; for (let i = 0; i < code.length; i++) { h ^= code.charCodeAt(i); h = Math.imul(h, 16777619); } return h >>> 0; }

  // ───────── 因子元数据 (与共享档案库 fa_* 对齐) ─────────
  // dir: +1 越高越好 / -1 作为惩罚(风控)
  const FACTORS = [
    { id: 'fa_reversal', short: '缩量反转', glyph: '反', cat: '价量',  ic: 0.043, dir: 1,  color: 'var(--zhu)', expr: '-rank(ts_sum(ret,5))·(vol_ratio<0.7)' },
    { id: 'fa_north',    short: '北向动量', glyph: '北', cat: '资金',  ic: 0.031, dir: 1,  color: 'var(--jin)', expr: 'rank(ts_sum(north_hold_chg,3))' },
    { id: 'fa_pead',     short: 'PEAD 漂移', glyph: '业', cat: '基本面', ic: 0.052, dir: 1,  color: 'var(--dai)', expr: 'rank(eps_surprise)·hold(60d)' },
    { id: 'fa_distrib',  short: '退潮风控', glyph: '险', cat: '风控',  ic: 0.028, dir: -1, color: 'var(--yin)', expr: '−(near_60d_high & vol_ratio>2 & ret_1d<2%)' },
  ];
  const FBYID = {}; FACTORS.forEach(f => FBYID[f.id] = f);

  // ───────── 股票池 (沪深主流, 跨 16 行业) ─────────
  // [code, name, industry, board] board: 主板/创/科
  const RAW = [
    ['600519', '贵州茅台', '食品饮料', '主'], ['000858', '五粮液', '食品饮料', '主'], ['000568', '泸州老窖', '食品饮料', '主'],
    ['600809', '山西汾酒', '食品饮料', '主'], ['600887', '伊利股份', '食品饮料', '主'], ['603288', '海天味业', '食品饮料', '主'],
    ['300750', '宁德时代', '电力设备', '创'], ['002594', '比亚迪', '汽车', '主'], ['300014', '亿纬锂能', '电力设备', '创'],
    ['300274', '阳光电源', '电力设备', '创'], ['601012', '隆基绿能', '电力设备', '主'], ['600438', '通威股份', '电力设备', '主'],
    ['688981', '中芯国际', '半导体', '科'], ['002371', '北方华创', '半导体', '主'], ['603501', '韦尔股份', '半导体', '主'],
    ['603986', '兆易创新', '半导体', '主'], ['688012', '中微公司', '半导体', '科'],
    ['600276', '恒瑞医药', '医药', '主'], ['603259', '药明康德', '医药', '主'], ['300760', '迈瑞医疗', '医药', '创'],
    ['300122', '智飞生物', '医药', '创'], ['300015', '爱尔眼科', '医药', '创'],
    ['600036', '招商银行', '银行', '主'], ['601166', '兴业银行', '银行', '主'], ['002142', '宁波银行', '银行', '主'], ['000001', '平安银行', '银行', '主'],
    ['300059', '东方财富', '非银', '创'], ['600030', '中信证券', '非银', '主'], ['601318', '中国平安', '非银', '主'],
    ['600406', '国电南瑞', '电力设备', '主'], ['600089', '特变电工', '电力设备', '主'],
    ['601899', '紫金矿业', '有色', '主'], ['600111', '北方稀土', '有色', '主'], ['002460', '赣锋锂业', '有色', '主'],
    ['601633', '长城汽车', '汽车', '主'], ['000625', '长安汽车', '汽车', '主'], ['600660', '福耀玻璃', '汽车', '主'],
    ['002230', '科大讯飞', '计算机', '主'], ['000977', '浪潮信息', '计算机', '主'], ['002415', '海康威视', '计算机', '主'], ['688111', '金山办公', '计算机', '科'],
    ['002027', '分众传媒', '传媒', '主'], ['002555', '三七互娱', '传媒', '主'], ['300413', '芒果超媒', '传媒', '创'],
    ['000333', '美的集团', '家电', '主'], ['000651', '格力电器', '家电', '主'], ['600690', '海尔智家', '家电', '主'],
    ['600031', '三一重工', '机械', '主'], ['300124', '汇川技术', '机械', '创'],
    ['600309', '万华化学', '化工', '主'], ['000792', '盐湖股份', '化工', '主'],
    ['000063', '中兴通讯', '通信', '主'], ['002475', '立讯精密', '通信', '主'],
    ['600585', '海螺水泥', '建材', '主'], ['002714', '牧原股份', '农业', '主'],
  ];

  // 行业对各因子的轻微 tilt (让打分有结构, 不是纯噪声)
  const TILT = {
    '食品饮料': { north: 0.55, pead: 0.15, reversal: -0.1, risk: -0.1 },
    '电力设备': { north: 0.1, pead: 0.2, reversal: 0.15, risk: 0.4 },
    '半导体':   { north: -0.1, pead: 0.5, reversal: 0.1, risk: 0.45 },
    '医药':     { north: 0.15, pead: 0.45, reversal: 0.0, risk: 0.1 },
    '银行':     { north: 0.6, pead: -0.1, reversal: 0.05, risk: -0.4 },
    '非银':     { north: 0.45, pead: 0.1, reversal: 0.2, risk: 0.2 },
    '有色':     { north: 0.0, pead: 0.25, reversal: 0.3, risk: 0.45 },
    '汽车':     { north: 0.1, pead: 0.3, reversal: 0.15, risk: 0.25 },
    '计算机':   { north: -0.05, pead: 0.1, reversal: 0.25, risk: 0.5 },
    '传媒':     { north: -0.1, pead: 0.0, reversal: 0.35, risk: 0.45 },
    '家电':     { north: 0.4, pead: 0.2, reversal: -0.05, risk: -0.15 },
    '机械':     { north: 0.05, pead: 0.3, reversal: 0.15, risk: 0.15 },
    '化工':     { north: 0.0, pead: 0.2, reversal: 0.3, risk: 0.3 },
    '通信':     { north: 0.1, pead: 0.15, reversal: 0.2, risk: 0.3 },
    '建材':     { north: 0.2, pead: 0.1, reversal: 0.1, risk: 0.0 },
    '农业':     { north: 0.0, pead: 0.35, reversal: 0.1, risk: 0.1 },
  };

  // 上一期持仓 (用于换手率对比) — 一份固定的先验组合
  const PREV_HOLD = ['600519', '300750', '600276', '600036', '601012', '002594', '600519', '603259', '300059', '601899', '000858', '600438', '002371', '600309', '601318', '000333'];


  // ───────── 生成股票静态属性 ─────────
  const UNIVERSE = RAW.map(([code, name, ind, board]) => {
    const rnd = mulberry32(seedOf(code));
    const t = TILT[ind] || {};
    const g = () => (rnd() + rnd() + rnd()) / 3 - 0.5; // ~钟形, 居中
    const raw = {
      reversal: g() + (t.reversal || 0) * 0.7,
      north:    g() + (t.north || 0) * 0.7,
      pead:     g() + (t.pead || 0) * 0.7,
      risk:     g() + (t.risk || 0) * 0.7,
    };
    // 市值: 蓝筹大, 创/科偏中小
    const baseCap = board === '主' ? 1400 : 700;
    const mktcap = Math.round(baseCap * (0.25 + rnd() * 2.6)); // 亿
    const price = +(8 + rnd() * 320).toFixed(2);
    const chg = +((g() * 2 + raw.reversal * 1.2) * 3.2).toFixed(2); // 当日 %
    const turn = +(0.4 + rnd() * 6.5).toFixed(2); // 换手 %
    const amt = +(mktcap * turn / 100 * (0.6 + rnd() * 0.8)).toFixed(1); // 成交额 亿
    const st = rnd() < 0.05;
    const halt = rnd() < 0.035;
    const newish = rnd() < 0.08;
    let limit = 0;
    if (chg > 9.6) limit = 1; else if (chg < -9.6) limit = -1;
    return { code, name, ind, board, raw, mktcap, price, chg, turn, amt, st, halt, newish, limit };
  });

  // ───────── 截面 z 分 (按因子标准化) ─────────
  const KEYS = ['reversal', 'north', 'pead', 'risk'];
  const STATS = {};
  KEYS.forEach(k => {
    const xs = UNIVERSE.map(s => s.raw[k]);
    const mean = xs.reduce((a, b) => a + b, 0) / xs.length;
    const sd = Math.sqrt(xs.reduce((a, b) => a + (b - mean) ** 2, 0) / xs.length) || 1;
    STATS[k] = { mean, sd };
  });
  UNIVERSE.forEach(s => {
    s.z = {};
    KEYS.forEach(k => { s.z[k] = (s.raw[k] - STATS[k].mean) / STATS[k].sd; });
  });
  // factor.id → z key
  const ZKEY = { fa_reversal: 'reversal', fa_north: 'north', fa_pead: 'pead', fa_distrib: 'risk' };

  // 分位 (0-100) 给 UI 展示
  function pctRank(arr, v) { let c = 0; arr.forEach(x => { if (x <= v) c++; }); return Math.round(c / arr.length * 100); }

  // ───────── 选股引擎 ─────────
  // cfg: { factors:[{id,w}], topN, industryNeutral, indCap(每行业占比上限), maxWeight,
  //        liqMin(成交额亿下限), exclST, exclHalt, exclLimit, exclNew, weighting:'equal'|'score'|'mktcap' }
  function build(cfg) {
    const sel = (cfg.factors || []).filter(f => FBYID[f.id]);
    const wsum = sel.reduce((a, f) => a + f.w, 0) || 1;
    // 复合分: Σ w_i · dir_i · z_i
    const scored = UNIVERSE.map(s => {
      let sc = 0;
      sel.forEach(f => {
        const meta = FBYID[f.id];
        sc += (f.w / wsum) * meta.dir * s.z[ZKEY[f.id]];
      });
      return { s, score: sc };
    });
    const allScores = scored.map(x => x.score);
    scored.forEach(x => { x.pct = pctRank(allScores, x.score); });

    // 排除规则 → reason
    const annotate = (x) => {
      const r = [];
      if (cfg.exclST && x.s.st) r.push('ST');
      if (cfg.exclHalt && x.s.halt) r.push('停牌');
      if (cfg.exclLimit && x.s.limit) r.push(x.s.limit > 0 ? '涨停' : '跌停');
      if (cfg.exclNew && x.s.newish) r.push('次新');
      if (x.s.amt < cfg.liqMin) r.push('流动性');
      return r;
    };
    scored.forEach(x => { x.excl = annotate(x); });

    const pool = scored.filter(x => x.excl.length === 0).sort((a, b) => b.score - a.score);

    // 行业中性: 每行业上限 = ceil(topN * indCap)
    const capPer = cfg.industryNeutral ? Math.max(1, Math.ceil(cfg.topN * cfg.indCap)) : Infinity;
    const indCount = {};
    const chosen = [];
    const benched = []; // 因行业限额被挤出
    for (const x of pool) {
      if (chosen.length >= cfg.topN) { benched.push(x); continue; }
      const c = indCount[x.s.ind] || 0;
      if (c >= capPer) { x.benchReason = '行业满额'; benched.push(x); continue; }
      indCount[x.s.ind] = c + 1; chosen.push(x);
    }

    // ── 选股分布统计 (无仓位概念,按只数计) ──
    const byInd = {};
    chosen.forEach(x => { byInd[x.s.ind] = (byInd[x.s.ind] || 0) + 1; });
    const nC = chosen.length || 1;
    const indDist = Object.entries(byInd).map(([k, v]) => ({ ind: k, n: v, frac: v / nC })).sort((a, b) => b.n - a.n);
    // 预期 RankIC: 因子 IC 按因子权重加权
    const combIC = sel.reduce((a, f) => a + (f.w / wsum) * FBYID[f.id].ic, 0);
    // 平均综合分位
    const avgPct = chosen.length ? chosen.reduce((a, x) => a + x.pct, 0) / chosen.length : 0;

    return {
      chosen, benched, pool, scored,
      stat: { n: chosen.length, indDist, combIC, avgPct },
    };
  }

  window.XG_FACTORS = FACTORS;
  window.XG_FBYID = FBYID;

  // ── 动态因子目录(选股页2.0):有后端时拉 /screen/factors(~56因子·11族·实测IC)覆盖静态4条 ──
  //    失败保持静态(诚实降级);FBYID 合并不清空 → 旧 id(fa_north 等)仍可显示不崩。
  const FAM_COLORS = {
    '动量反转': 'var(--zhu)', '技术': 'var(--dai)', '估值': 'var(--jin)', '财务质量': '#5d7a8a',
    '成长': '#8a5d44', '波动率': '#7a6f5d', '流动性': '#4a6b8a', '情绪': '#a8743d',
    '规模': '#6b5d7a', '共振': 'var(--yin)', '跟随': '#8a5d7a', '价量': 'var(--zhu)', '风控': 'var(--yin)',
  };
  window.xgLoadCatalog = async function (API) {
    const r = await fetch((API || '') + '/screen/factors');
    const j = await r.json();
    if (!j.ok || !j.factors || !j.factors.length) return 0;
    const rows = j.factors.filter(f => f.supported).map(f => ({
      id: f.id, short: f.short, cat: f.family || '其他', desc: f.desc || '',
      ic: (f.ic == null ? null : +f.ic), icir: (f.icir == null ? null : +f.icir),
      icReal: f.ic != null, icAsof: f.ic_asof || '', dir: f.dir, expr: f.expr || '',
      glyph: (f.family || '因')[0], color: FAM_COLORS[f.family] || 'var(--ink-2)',
    }));
    window.XG_FACTORS = rows;
    rows.forEach(f => { FBYID[f.id] = f; });
    window.XG_FAMILIES = j.families || [];
    window.XG_IC_NOTE = j.ic_note || '';
    return rows.length;
  };
  window.XG_UNIVERSE = UNIVERSE;
  window.XG_PREV_HOLD = PREV_HOLD;
  window.xgBuildLocal = build;                  // 本地合成引擎(file:// 直开预览兜底)
  window.xgBuild = build;                        // 默认=本地;有后端时 app 调 xgBuildBackend
  window.xgBuildBackend = async function (cfg, API) {  // 真后端 /screen/run(同源薄壳 9999)
    const r = await fetch((API || '') + '/screen/run', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(cfg) });
    const j = await r.json();
    if (!j.ok) throw new Error(j.reason || 'screen 失败');
    return j;
  };
})();
