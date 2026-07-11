// 观澜 · 落子 — 主壳(2026-07-11 三页重排:今日 | 复盘 | 策略)
//   三轴(盯盘·校场 × 复盘·实盘 × 单标·舰队)退役 → 单轴 page;逐bar推演/Deliberation/播放条/
//   影子组合/台账记账/前端盯盘循环 全部退役(盯盘归后端 watcher,留痕归 DecisionTrail)。
//   复盘真跑改「向导驱动」:票/策略/日期区间/粒度 → 一键跑,与游标彻底解耦(拔掉两个静默失败门)。
const { useMemo, useCallback: useCB } = React;

// 帷幄融合旗(对齐 screen-app):EMBED=被帷幄嵌入右栏(只隐顶栏身份区);WS=帷幄会话 id,handoff 信箱按会话取防串扰。
const WW_EMBED = new URLSearchParams(location.search).get('embed') === '1';
const WW_WS = new URLSearchParams(location.search).get('ws') || '';

// ② 五档盘口 + 逐笔成交面板(纯展示,数据来自 /seats/orderbook + /seats/ticks;tdx 经统一 live_client)。
//   null-safe:book/ticks 缺失或 ok:false → 显 note 降级,绝不塞 0 价假档。红线:只读展示,绝不喂研判/信号。
function OrderbookTicksPanel({ book, ticks }) {
  const asks = (book && book.ok ? (book.levels || []) : []).slice().reverse();   // 卖5..卖1 由上到下
  const bids = (book && book.ok) ? (book.levels || []) : [];
  const lc = book && book.last_close;
  const px = (p) => (p == null ? '—' : (+p).toFixed(2));
  const col = (p) => ((lc != null && p != null) ? (+p > +lc ? 'var(--zhu,#c0392b)' : (+p < +lc ? 'var(--fall,#1f9d55)' : 'var(--ink-2)')) : 'var(--ink-2)');
  const lvlRow = (lv, side) => {
    const p = side === '卖' ? lv.ask : lv.bid, vol = side === '卖' ? lv.ask_vol : lv.bid_vol;
    return (
      <div key={side + lv.level} style={{ display: 'flex', justifyContent: 'space-between', fontSize: 11, fontFamily: 'var(--font-mono)', padding: '1px 6px' }}>
        <span style={{ color: 'var(--ink-3)', width: 30 }}>{side}{lv.level}</span>
        <span style={{ color: col(p) }}>{px(p)}</span>
        <span style={{ color: 'var(--ink-2)', width: 64, textAlign: 'right' }}>{vol == null ? '—' : vol}</span>
      </div>
    );
  };
  return (
    <div style={{ flexShrink: 0, borderTop: '1px solid var(--line)', padding: '6px 4px' }}>
      <div style={{ fontSize: 11, color: 'var(--ink-2)', fontWeight: 600, marginBottom: 4, display: 'flex', justifyContent: 'space-between' }}>
        <span>五档盘口 · 逐笔</span><span style={{ fontSize: 9, color: 'var(--ink-4)' }} title="tdx 经统一 live_client 现拉">tdx 实时</span>
      </div>
      {(!book || !book.ok) ? (
        <div style={{ fontSize: 10, color: 'var(--ink-4)', padding: '4px 6px' }}>{(book && book.note) || '盘口加载中 / tdx 不可达(非交易时段无挂单)'}</div>
      ) : (
        <div>{asks.map(l => lvlRow(l, '卖'))}<div style={{ borderTop: '1px dashed var(--line)', margin: '2px 0' }} />{bids.map(l => lvlRow(l, '买'))}</div>
      )}
      <div style={{ fontSize: 9, color: 'var(--ink-4)', margin: '5px 6px 2px' }}>逐笔成交</div>
      {(!ticks || !ticks.ok || !(ticks.ticks || []).length) ? (
        <div style={{ fontSize: 10, color: 'var(--ink-4)', padding: '0 6px 4px' }}>{(ticks && ticks.note) || '无逐笔(非交易时段/tdx 不可达)'}</div>
      ) : (
        <div style={{ maxHeight: 96, overflowY: 'auto' }}>
          {(ticks.ticks || []).slice(0, 20).map((t, i) => (
            <div key={i} style={{ display: 'flex', justifyContent: 'space-between', fontSize: 10, fontFamily: 'var(--font-mono)', padding: '0 6px' }}>
              <span style={{ color: 'var(--ink-4)', width: 64 }}>{t.time || '—'}</span>
              <span style={{ color: t.side === 'buy' ? 'var(--zhu,#c0392b)' : (t.side === 'sell' ? 'var(--fall,#1f9d55)' : 'var(--ink-3)') }}>{t.price == null ? '—' : (+t.price).toFixed(2)}</span>
              <span style={{ color: 'var(--ink-3)', width: 52, textAlign: 'right' }}>{t.vol == null ? '—' : t.vol}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// 盯盘总闸(后端 watcher 状态条):服务未启用/盘外/预算 全显形;开关写 /seats/watch/toggle。
function WatchStrip({ watch, onToggle }) {
  if (watch === null) return (
    <div className="mono" style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '4px 18px', borderBottom: '1px solid var(--line-soft)', fontSize: 9.5, color: 'var(--ink-3)', flexShrink: 0 }}>
      ○ 后端盯盘服务未启用(重启 9999 前为旧后端,或未设 GUANLAN_SEATS_WATCH=1)· 手动研判/条件单不受影响
    </div>
  );
  const on = !!watch.enabled;
  return (
    <div className="mono" style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '4px 18px', borderBottom: '1px solid var(--line-soft)', fontSize: 9.5, flexShrink: 0, background: on ? 'rgba(168,57,45,0.035)' : 'transparent' }}>
      <span onClick={() => onToggle && onToggle(!on)} title={on ? '关闭服务端盯盘' : '开启服务端盯盘:交易日盘中按策略节拍自动研判绑定票,关页面也盯'}
        style={{ cursor: 'pointer', padding: '1px 9px', borderRadius: 9, border: '1px solid ' + (on ? 'var(--yin)' : 'var(--line)'), color: on ? 'var(--paper)' : 'var(--ink-3)', background: on ? 'var(--yin)' : 'transparent', whiteSpace: 'nowrap' }}>
        {on ? '● 后端盯盘中' : '○ 开后端盯盘'}
      </span>
      <span style={{ color: 'var(--ink-2)' }}>{(watch.watching || []).length} 支绑定</span>
      <span style={{ color: 'var(--ink-3)' }}>今日已判 {watch.todayCount != null ? watch.todayCount : '—'}{watch.budget != null ? ' / 预算 ' + watch.budget : ''}</span>
      <span style={{ color: 'var(--ink-3)' }}>{watch.marketOpen ? '盘中' : '盘外(到点自动跑)'}</span>
      {watch.lastTick && <span style={{ color: 'var(--ink-3)' }}>上次巡检 {String(watch.lastTick).replace('T', ' ').slice(5, 16)}</span>}
      <span style={{ marginLeft: 'auto', color: 'var(--ink-3)', fontSize: 8.5 }} title="服务端定时研判绑定票(策略 bind 派生),结果落研判历史;关页面照常盯">关页面也盯 · 结果进研判时间线</span>
    </div>
  );
}

function LuoziApp() {
  const [page, setPage] = useState('today');          // today | replay | strategy(单轴,三轴退役)
  const [code, setCode] = useState(window.LZ_PRIMARY);
  const [strategies, setStrategies] = useState(() => window.lzStrategyForCode ? window.lzStrategyForCode(window.LZ_PRIMARY) : []);
  const [curStratId, setCurStratId] = useState(null); // 当前策略 id(null/失效 → curSid 回退本票首个策略)
  const [selected, setSelected] = useState(null);     // 复盘:选中的 run 决策(RunDecCard)
  const [tf, setTf] = useState('D');
  const [dark, setDark] = useState(false);
  const [toast, setToast] = useState(null);
  const [, setPoolTick] = useState(0);                // 票池增删后强制重渲染(池数组是模块级)
  const [poolTickV, setPoolTickV] = useState(0);      // 传给 TicketList 触发其轮询重建
  const [realSyms, setRealSyms] = useState({});       // code → 用真日K装配的 symbol(覆盖合成)
  const [dataMode, setDataMode] = useState('mock');   // real | mock
  const [zoom, setZoom] = useState(null);
  const [panEnd, setPanEnd] = useState(null);
  const [market, setMarket] = useState(null);         // 今日市场状态(/watch/market_status)
  const [quote, setQuote] = useState(null);           // 实时盘口(仅今日页轮询)
  const [book, setBook] = useState(null);
  const [ticks, setTicks] = useState(null);
  const [newsKw, setNewsKw] = useState('');
  const [newsPayload, setNewsPayload] = useState(null);
  const [newsPanel, setNewsPanel] = useState(null);
  const [liveBar, setLiveBar] = useState(null);       // 今日柱缓存(盘后保图有今天)
  useEffect(() => { setLiveBar(window.lzLivebarLoad ? window.lzLivebarLoad(code) : null); }, [code]);
  const [watch, setWatch] = useState(null);           // 后端盯盘状态(null=端点不可达/旧后端)
  const [todayDecs, setTodayDecs] = useState([]);     // 今日页 K 线金框标记源(后端落盘时间线)
  const [realDecs, setRealDecs] = useState({});       // 复盘真跑「进行中」的即时标记(跑完由选中 run 接管)
  const [realRun, setRealRun] = useState({ running: false, done: 0, total: 0, cur: null, seatName: '', errors: 0 });
  const [runsBump, setRunsBump] = useState(0);
  const [selRun, setSelRun] = useState(null);
  const [runDecs, setRunDecs] = useState([]);
  const [orderTriggers, setOrderTriggers] = useState([]);   // 条件单触发(标 K 线;不再写影子/台账)
  // 复盘向导(与游标解耦的真跑参数;空 = 用派生默认:近 120 交易日 · 日线)
  const [wizStart, setWizStart] = useState('');
  const [wizEnd, setWizEnd] = useState('');
  const [wizFreq, setWizFreq] = useState('day');      // day | 30min
  const realStopRef = useRef(false);
  useEffect(() => { realStopRef.current = true; }, [code, page]);   // 切票/切页 → 停掉进行中的真跑

  useEffect(() => { document.body.classList.toggle('dark', dark); }, [dark]);

  // 当前票在场策略(订阅 GL,增删改 / 跨标签即刷新)
  useEffect(() => {
    setCurStratId(null);
    const refresh = () => setStrategies(window.lzStrategyForCode ? window.lzStrategyForCode(code) : []);
    refresh();
    const off = window.GL ? window.GL.on(refresh) : null;
    return () => { if (off) off(); };
  }, [code]);

  // P1⑨:接 cockpit 交棒(选股「据此落子」篮子 / 帷幄研判 / 图谱跳转)。通知条直插 DOM(effect 风暴下 React toast 不显形)。
  useEffect(() => {
    const notice = (text) => {
      try {
        const d = document.createElement('div');
        d.id = 'lz-handoff-notice';
        d.style.cssText = 'position:fixed;bottom:70px;left:50%;transform:translateX(-50%);z-index:9500;display:flex;align-items:center;gap:11px;background:var(--paper,#f1ead9);border:1px solid var(--dai-soft,#9db4a8);border-radius:11px;padding:11px 16px;box-shadow:0 6px 26px rgba(28,24,20,0.18);font-family:var(--serif,serif);font-size:12.5px;color:var(--ink-1,#3a332b);max-width:72vw;';
        d.innerHTML = '<span style="width:22px;height:22px;font-size:12px;background:var(--dai,#4a6b5c);color:var(--paper,#f1ead9);display:flex;align-items:center;justify-content:center;border-radius:6px;flex-shrink:0">瀾</span><span></span><span style="cursor:pointer;color:var(--ink-3,#999);font-size:14px;padding:0 2px;flex-shrink:0">×</span>';
        d.children[1].textContent = text;
        d.lastChild.onclick = () => { try { d.remove(); } catch (e) {} };
        document.body.appendChild(d);
      } catch (e) {}
    };
    const tick = setTimeout(() => {
      const h = window.GL && window.GL.take ? window.GL.take('cockpit', WW_WS) : null;
      if (!h) return;
      const bare = (c) => String(c || '').replace(/^(SH|SZ|BJ)/i, '');
      if (h.fromScreen && Array.isArray(h.basket) && h.basket.length) {
        let added = 0;
        h.basket.forEach(b => {
          if (b && window.lzPoolAdd && window.lzPoolAdd({ code: bare(b.code), name: b.name, ind: b.ind })) added++;
        });
        if (added) { setPoolTick(t => t + 1); setPoolTickV(t => t + 1); }
        const first = h.basket.find(b => b && window.LZ_SYMBOLS && window.LZ_SYMBOLS[bare(b.code)]);
        if (first) { setCode(bare(first.code)); setPage('today'); }
        const names = h.basket.slice(0, 5).map(b => (b.name || b.code)).join('、') + (h.basket.length > 5 ? '…' : '');
        notice('已接收选股篮子 ' + h.basket.length + ' 只(' + names + '):新入票池 ' + added + ' 只'
          + (first ? ',已聚焦 ' + (first.name || first.code) : '') + ';左栏可切换,动态票可「移出票池」');
      } else if (h.code) {
        const c = bare(h.code);
        const added = !!(window.lzPoolAdd && window.lzPoolAdd({ code: c, name: h.name }));
        if (added) { setPoolTick(t => t + 1); setPoolTickV(t => t + 1); }
        const focusable = !!(window.LZ_SYMBOLS && window.LZ_SYMBOLS[c]);
        if (focusable) { setCode(c); setPage('today'); }
        notice('已接收帷幄研判交棒 ' + (h.name || c) + (added ? ':新入票池' : '') + (focusable ? ',已聚焦' : ''));
      } else if (h.focusSeat || h.focusId) {
        notice('来自研究图谱的跳转已接收(' + (h.focusSeat || h.focusId) + ')');
      }
    }, 600);
    return () => clearTimeout(tick);
  }, []);

  // 今日市场状态(快照,拉一次)
  useEffect(() => {
    let alive = true;
    if (window.lzFetchMarketStatus) window.lzFetchMarketStatus().then(m => { if (alive && m) setMarket(m); });
    return () => { alive = false; };
  }, []);

  // 后端盯盘状态:今日页 30s 轮询(端点不可达 → null 诚实态)
  useEffect(() => {
    if (page !== 'today' || !window.lzFetchWatchStatus) { return; }
    let alive = true;
    const pull = () => window.lzFetchWatchStatus().then(w => { if (alive) setWatch(w); });
    pull();
    const iv = setInterval(pull, 30000);
    return () => { alive = false; clearInterval(iv); };
  }, [page]);
  const toggleWatch = (on) => { if (window.lzToggleWatch) window.lzToggleWatch(on).then(w => { if (w) setWatch(w); }); };

  // 今日页 K 线金框标记:后端落盘研判(45s;含 watcher/手动;条件单不上 K 线金框——触发另有金环)
  useEffect(() => {
    if (page !== 'today' || !window.lzFetchDecisionsTimeline) { setTodayDecs([]); return; }
    let alive = true;
    const pull = () => window.lzFetchDecisionsTimeline(code, 20).then(rows => {
      if (!alive || !rows) return;
      setTodayDecs(rows.filter(r => r.kind !== 'order').map(r => ({
        key: 'tl_' + r.id, seat: r.strategy_id || '', date: String(r.asof || r.ts || '').slice(0, 10),
        side: /买/.test(r.direction || '') ? 'buy' : (/卖/.test(r.direction || '') ? 'sell' : 'watch'),
        direction: r.direction || '', conf: (r.confidence != null ? r.confidence : null),
        rationale: r.rationale || '', asof: r.asof || r.ts, model_name: r.model_name || '',
      })));
    });
    pull();
    const iv = setInterval(pull, 45000);
    return () => { alive = false; clearInterval(iv); };
  }, [page, code]);

  // 实时盘口:今日页轮询 /seats/quote(~6s);切走清空,失败保留上次(诚实降级)。
  useEffect(() => {
    if (page !== 'today' || !window.lzFetchQuote) { setQuote(null); return; }
    let alive = true;
    const pull = () => window.lzFetchQuote(code).then(q => { if (alive && q) setQuote(q); });
    pull();
    const iv = setInterval(pull, 6000);
    return () => { alive = false; clearInterval(iv); };
  }, [page, code]);

  // 五档盘口 + 逐笔:今日页轮询(~8s)
  useEffect(() => {
    if (page !== 'today' || !window.lzFetchOrderbook) { setBook(null); setTicks(null); return; }
    let alive = true;
    const pull = () => {
      window.lzFetchOrderbook(code).then(b => { if (alive && b) setBook(b); });
      window.lzFetchTicks(code, 30).then(t => { if (alive && t) setTicks(t); });
    };
    pull();
    const iv = setInterval(pull, 8000);
    return () => { alive = false; clearInterval(iv); };
  }, [page, code]);

  // 第3期:symbol 按 (bars, strategies) 反应式重建
  const baseBars = (realSyms[code] && realSyms[code].bars) || window.LZ_SYMBOLS[code].bars;
  const _meta = window.LZ_SYMBOLS[code].meta;
  const symbol = useMemo(() => {
    const s = window.lzBuildSymbolFromBars(_meta, baseBars, strategies,
      (realSyms[code] && realSyms[code].benchRows) || null);
    return (realSyms[code] && realSyms[code].bars5) ? Object.assign({}, s, { bars5: realSyms[code].bars5 }) : s;
  }, [baseBars, strategies, code, realSyms]);
  const curSid = (curStratId && strategies.some(s => s.id === curStratId)) ? curStratId : ((strategies[0] && strategies[0].id) || null);
  const curStrat = strategies.find(s => s.id === curSid) || null;
  const curName = (curStrat && curStrat.name) || '策略';
  const active = useMemo(() => curSid ? [curSid] : [], [curSid]);
  const n = symbol.bars.length;
  const cursor = n - 1;                                // 游标退役:恒末根(推演/播放已删)

  // ④-③ 捕获今日柱:今日页下报价晚于末根真日K日 → 提一根今日真实柱写缓存
  useEffect(() => {
    if (page !== 'today' || !quote || !window.lzLivebarFromQuote) return;
    const last = symbol.bars[n - 1];
    const nb = window.lzLivebarFromQuote(quote, last ? last.date : null);
    if (nb) { setLiveBar(nb); if (window.lzLivebarSave) window.lzLivebarSave(code, nb); }
  }, [quote, page, code, n]);

  // 复盘净值:只由选中 run 驱动(run 真决策模拟成交);未选 run → null 引导。
  const runPerf = useMemo(() => {
    if (page !== 'replay' || !selRun || !window.lzRunBacktest) return null;
    const isMin = (selRun.tf === '30min');
    if (!isMin && tf !== 'D') return null;
    const refBars = isMin ? (window.lzBars30 ? window.lzBars30(symbol) : []) : symbol.bars;
    return window.lzRunBacktest(runDecs, refBars, false);
  }, [page, selRun, tf, runDecs, symbol]);
  const runPerfHybrid = useMemo(() => {
    if (page !== 'replay' || !selRun || !window.lzRunBacktest) return null;
    const isMin = (selRun.tf === '30min');
    if (!isMin && tf !== 'D') return null;
    const refBars = isMin ? (window.lzBars30 ? window.lzBars30(symbol) : []) : symbol.bars;
    return window.lzRunBacktest(runDecs, refBars, true);
  }, [page, selRun, tf, runDecs, symbol]);
  const repPerf = page === 'replay' ? runPerf : null;
  const repPerfHybrid = page === 'replay' ? runPerfHybrid : null;
  const anyHybrid = (runDecs || []).some(d => (d && (+d.w || 0) > 0));

  // ⑤++ 复盘「真跑」(向导驱动):按 [起, 止] 日期区间逐 bar 真调 /seats/decide(PIT),与游标解耦。
  //   运行中再点=停;串行 await;regime=null 防 look-ahead;跑完注册 run 头并自动选中。
  const runRealThink = async () => {
    if (realRun.running) { realStopRef.current = true; return; }
    if (page !== 'replay' || !window.lzSeatDecide) return;
    const isMin = (wizFreq === '30min');
    const sid = curSid;
    if (!sid) { setToast({ kind: 'handoff', text: '无策略可跑——先去「策略」页钤印一个' }); setTimeout(() => setToast(null), 4000); return; }
    const strat = window.lzStrategyGet ? window.lzStrategyGet(sid) : null;
    const tmpl = strat && strat.template;
    const seatName = (strat && strat.name) || sid;
    const tmplCreed = (window.LZ_TEMPLATES && tmpl && window.LZ_TEMPLATES[tmpl] && window.LZ_TEMPLATES[tmpl].creed) || '';
    const creed = (strat && strat.creed) || tmplCreed;
    const rcp = window.lzRecipeForStrategy ? window.lzRecipeForStrategy(sid) : { cards: [], research: [], factors: [] };
    const codeNow = code, meta = symbol.meta;
    // 区间默认:近 120 交易日(日线)/ 近 10 交易日(30min);向导有值用向导
    const dayBars = symbol.bars;
    const defStart = dayBars[Math.max(0, dayBars.length - (isMin ? 10 : 120))].date;
    const defEnd = dayBars[dayBars.length - 1].date;
    const d0 = wizStart || defStart, d1 = wizEnd || defEnd;
    let runBars;
    if (isMin) {
      let bars5 = symbol.bars5;
      if (!bars5 && window.lzFetchBars5) {
        bars5 = await window.lzFetchBars5(codeNow, 2400);
        if (bars5) setRealSyms(s => { const cur = s[codeNow]; return cur ? Object.assign({}, s, { [codeNow]: Object.assign({}, cur, { bars5 }) }) : s; });
      }
      if (!bars5 || !bars5.length) { setToast({ kind: 'handoff', text: '30 分钟粒度需要真 5min 数据,当前未到——换日线或稍后再试' }); setTimeout(() => setToast(null), 4200); return; }
      runBars = window.lzBars30 ? window.lzBars30(Object.assign({}, symbol, { bars5 })) : [];
      runBars = runBars.filter(b => b.day >= d0 && b.day <= d1);
    } else {
      runBars = dayBars.filter(b => b.date >= d0 && b.date <= d1);
    }
    if (!runBars.length) { setToast({ kind: 'handoff', text: '区间内无 K 线(' + d0 + ' → ' + d1 + ')' }); setTimeout(() => setToast(null), 4000); return; }
    // 因子窗口热身:起点前至少留 6 根日K(与旧 startIdx=max(6,·) 同口径)
    if (!isMin) {
      const i0 = dayBars.findIndex(b => b.date === runBars[0].date);
      if (i0 >= 0 && i0 < 6) runBars = runBars.slice(6 - i0);
      if (!runBars.length) { setToast({ kind: 'handoff', text: '区间太靠前,因子窗口不足' }); setTimeout(() => setToast(null), 4000); return; }
    }
    const total = runBars.length;
    const runId = window.lzRunId ? window.lzRunId() : ('run_' + Date.now());
    let nBuy = 0, nSell = 0, nWatch = 0, firstDate = null, lastDate = null, lastModel = '';
    realStopRef.current = false;
    setSelRun(null); setSelected(null);
    setRealDecs({});                                    // 进行中标记清零重来
    setRealRun({ running: true, done: 0, total: total, cur: 0, seatName: seatName, errors: 0 });
    let done = 0, errors = 0;
    for (let k = 0; k < total; k++) {
      if (realStopRef.current) break;
      const bar = runBars[k];
      if (!bar) continue;
      setRealRun(s => Object.assign({}, s, { cur: k }));
      let res = null;
      try {
        res = await window.lzSeatDecide({
          code: codeNow, name: meta.name, date: bar.date,
          seat_cn: seatName, creed: creed, mode: 'fast',
          strategy_id: sid, strategy_name: seatName,
          card: rcp.cards[0] ? { name: rcp.cards[0].name, insight: rcp.cards[0].insight, verdict: rcp.cards[0].verdict, conf: rcp.cards[0].conf, ic: rcp.cards[0].ic } : null,
          cards: rcp.cards, recipe_factors: rcp.factors,
          industry: meta.industry || '',
          regime: null,                                 // ★ 复盘 PIT:不喂今天市况;后端按 as-of 日产物补(防 look-ahead)
          run_id: runId,
          freq: isMin ? '30min' : 'day',
          w: (strat && strat.w) || 0,
          pa: !!(strat && strat.pa),
          pa_method: (strat && strat.pa) ? (strat.paMethod || window.LZ_PA_METHOD_DEFAULT || '') : '',
        });
      } catch (e) { res = null; }
      if (realStopRef.current) break;
      done++;
      if (res && res.ok && res.direction) {
        const dir = res.direction;
        const side = /买/.test(dir) ? 'buy' : (/卖/.test(dir) ? 'sell' : 'watch');
        if (side === 'buy') nBuy++; else if (side === 'sell') nSell++; else nWatch++;
        if (!firstDate) firstDate = bar.date;
        lastDate = bar.date;
        if (res.model_name) lastModel = res.model_name;
        const rd = { key: 'true_' + sid + '@' + bar.date, seat: sid, date: bar.date, side: side,
          direction: dir, conf: (res.confidence != null ? res.confidence : null),
          rationale: res.rationale || '', reasoning: res.reasoning || '', asof: res.asof || bar.date, model_name: res.model_name || '' };
        setRealDecs(prev => {
          const arr = (prev[codeNow] || []).filter(x => x.key !== rd.key).concat([rd]);
          return Object.assign({}, prev, { [codeNow]: arr });
        });
      } else { errors++; }
      setRealRun(s => Object.assign({}, s, { done: done, errors: errors }));
    }
    // run 头注册(≥1 笔成功;中途停也注册)→ 自动选中新 run(修「跑完不知道去哪看」)
    if (firstDate) {
      try {
        await fetch((window.GUANLAN_BACKEND || '') + '/seats/runs', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ run_id: runId, code: codeNow, name: meta.name, strategy_id: sid, strategy_name: seatName,
            tf: isMin ? '30min' : 'D', start_date: firstDate, end_date: lastDate, n_buy: nBuy, n_sell: nSell, n_watch: nWatch,
            n_err: errors, model: lastModel || '' }),
        });
        setRunsBump(x => x + 1);
        const list = window.lzRunsList ? await window.lzRunsList(codeNow) : null;
        const head = list && list.find(r => r.run_id === runId);
        if (head) { setSelRun(head); setRealDecs({}); }
      } catch (e) {}
    }
    setRealRun(s => Object.assign({}, s, { running: false, cur: null }));
  };

  // 切页/切票 → 清选中与触发标记
  useEffect(() => { setSelected(null); setOrderTriggers([]); setSelRun(null); setRunDecs([]); }, [page, code]);
  // 切策略 → 清选中 run(回测历史从属于策略)
  useEffect(() => { setSelRun(null); setSelected(null); }, [curSid]);

  // 选中 run → 拉该 run 决策(时间正序),asof 映射 idx;窗外 offChart。
  useEffect(() => {
    let dead = false;
    if (!selRun) { setRunDecs([]); return; }
    (async () => {
      const rows = window.lzRunDecisions ? await window.lzRunDecisions(selRun.run_id) : [];
      if (dead) return;
      const isMin = (selRun.tf === '30min');
      const refBars = isMin ? (window.lzBars30 ? window.lzBars30(symbol) : []) : (symbol.bars || []);
      const byKey = {}; refBars.forEach((b, i) => { byKey[b.date] = i; });
      setRunDecs(rows.map(r => {
        const key = isMin ? String(r.asof || '').slice(0, 16) : String(r.asof || '').slice(0, 10);
        const idx = byKey[key] != null ? byKey[key] : -1;
        const dir = String(r.direction || '');
        const side = /买/.test(dir) ? 'buy' : (/卖/.test(dir) ? 'sell' : 'watch');
        return { key: 'run_' + (r.id || key), seat: r.strategy_id || 'run', idx, date: key, side,
                 direction: dir, conf: (r.confidence == null ? null : +r.confidence),
                 rationale: r.rationale || '', reasoning: r.reasoning || null, asof: r.asof,
                 model_name: r.model_name || '', key_evidence: r.key_evidence || [],
                 recipe_factors: r.recipe_factors || [], card_names: r.card_names || [],
                 research: r.research || [], factors_std: r.factors_std || null,
                 regime_asof: r.regime_asof || null,
                 recipe_factors_vintage: r.recipe_factors_vintage || [],
                 hybrid_direction: r.hybrid_direction || dir,
                 factor_score: (r.factor_score == null ? null : r.factor_score),
                 hybrid_bias: (r.hybrid_bias == null ? null : r.hybrid_bias),
                 w: r.w || 0,
                 offChart: byKey[key] == null, _isRun: true };
      }));
    })();
    return () => { dead = true; };
  }, [selRun, symbol]);

  // 真日K接入(退避重试,后端恢复自动切真)
  useEffect(() => {
    if (realSyms[code]) { setDataMode('real'); return; }
    if (!window.lzFetchDailyBars) { setDataMode('mock'); return; }
    let alive = true, tries = 0, timer = null;
    const retry = () => { tries++; if (alive && tries <= 20) timer = setTimeout(attempt, Math.min(3000 * tries, 15000)); };
    const attempt = () => {
      if (!alive) return;
      window.lzFetchDailyBars(code, 250).then(async bars => {
        if (!alive) return;
        if (!bars) { setDataMode('mock'); retry(); return; }
        const benchRows = window.lzFetchBenchmark ? await window.lzFetchBenchmark(bars[0].date, bars[bars.length - 1].date) : null;
        const built = window.lzBuildSymbolFromBars(window.LZ_SYMBOLS[code].meta, bars, undefined, benchRows);
        built.benchRows = benchRows;
        setRealSyms(s => Object.assign({}, s, { [code]: built }));
        setDataMode('real');
      }).catch(() => { if (alive) { setDataMode('mock'); retry(); } });
    };
    attempt();
    return () => { alive = false; if (timer) clearTimeout(timer); };
  }, [code]);

  // ④-③ 官方日K落库自动接管(今日页,10min;有今日柱缓存才查)
  useEffect(() => {
    if (page !== 'today' || !window.lzFetchDailyBars) return;
    let alive = true;
    const poll = () => {
      const lb = window.lzLivebarLoad ? window.lzLivebarLoad(code) : null;
      if (!lb) return;
      window.lzFetchDailyBars(code, 250).then(async bars => {
        if (!alive || !bars || !bars.length) return;
        const lastReal = bars[bars.length - 1].date;
        if (lastReal >= lb.date) {
          const benchRows = window.lzFetchBenchmark ? await window.lzFetchBenchmark(bars[0].date, bars[bars.length - 1].date) : null;
          const built = window.lzBuildSymbolFromBars(window.LZ_SYMBOLS[code].meta, bars, undefined, benchRows);
          built.benchRows = benchRows;
          setRealSyms(s => Object.assign({}, s, { [code]: built }));
          if (window.lzLivebarClear) window.lzLivebarClear(code);
          setLiveBar(null);
        }
      }).catch(() => {});
    };
    poll();
    const iv = setInterval(poll, 600000);
    return () => { alive = false; clearInterval(iv); };
  }, [page, code]);

  // 切 TF / 标的 / 页 → 导航条复位
  useEffect(() => { setZoom(null); setPanEnd(null); }, [tf, code, page]);

  // 日内 TF / 30min run / 30min 向导 → 懒加载真 5min
  useEffect(() => {
    const need = (tf === '60' || tf === '30' || tf === '15' || tf === '5') || !!(selRun && selRun.tf === '30min') || (page === 'replay' && wizFreq === '30min');
    if (!need) return;
    const sym = realSyms[code];
    if (!sym || sym.bars5 || !window.lzFetchBars5) return;
    let alive = true;
    window.lzFetchBars5(code, 2400).then(bars5 => {
      if (!alive || !bars5) return;
      setRealSyms(s => {
        const cur = s[code];
        if (!cur || cur.bars5) return s;
        return Object.assign({}, s, { [code]: Object.assign({}, cur, { bars5 }) });
      });
    });
    return () => { alive = false; };
  }, [tf, code, realSyms, selRun, page, wizFreq]);

  // 今日页日内:轮询今日 5min 拼接(30s)
  useEffect(() => {
    if (page !== 'today') return;
    if (tf !== '60' && tf !== '30' && tf !== '15' && tf !== '5') return;
    if (!window.lzFetchRealtimeBars5) return;
    let alive = true;
    const pull = () => window.lzFetchRealtimeBars5(code).then(live5 => {
      if (!alive || !live5 || !live5.length) return;
      setRealSyms(s => {
        const cur = s[code];
        if (!cur || !cur.bars5 || !cur.bars5.length) return s;
        const seen = new Set(cur.bars5.map(b => b.date));
        const extra = live5.filter(b => !seen.has(b.date));
        if (!extra.length) return s;
        return Object.assign({}, s, { [code]: Object.assign({}, cur, { bars5: cur.bars5.concat(extra) }) });
      });
    });
    pull();
    const iv = setInterval(pull, 30000);
    return () => { alive = false; clearInterval(iv); };
  }, [page, tf, code]);

  // 今日页 1min:整批替换(20s)
  useEffect(() => {
    if (page !== 'today' || tf !== '1' || !window.lzFetchRealtimeBars1) return;
    let alive = true;
    const pull = () => window.lzFetchRealtimeBars1(code, 480).then(b1 => {
      if (!alive || !b1 || !b1.length) return;
      setRealSyms(s => { const cur = s[code]; if (!cur) return s; return Object.assign({}, s, { [code]: Object.assign({}, cur, { bars1: b1 }) }); });
    });
    pull();
    const iv = setInterval(pull, 20000);
    return () => { alive = false; clearInterval(iv); };
  }, [page, tf, code]);

  // 复盘回灌:当前 run 成绩提炼为草稿经验卡
  const distillToCard = () => {
    if (!window.GL || !repPerf) return;
    const m = repPerf.metrics;
    const name = symbol.meta.name + ' · ' + curName + '复盘提炼';
    const id = GL.put({ type: 'card', title: name, cat: '实测', tags: ['复盘回灌', symbol.meta.industry],
      verdict: m.total > 0 ? '通过' : '存疑', conf: Math.max(40, Math.min(90, Math.round(52 + m.sharpe * 7))),
      insight: `${symbol.meta.name} 复盘(${frame.label}):${curName}累计 ${pct(m.total)}、Sharpe ${m.sharpe.toFixed(2)}、胜率 ${(m.winRate * 100).toFixed(0)}%。【复盘草稿·未经验证,不入信号】`,
      expr: '(实测落子序列)', status: 'draft', refs: [] });
    setToast({ id, name });
    setTimeout(() => setToast(null), 5200);
  };

  const frame = window.lzFrameData(symbol, tf, cursor, cursor, true, page === 'today');
  const intraday = tf === '60' || tf === '30' || tf === '15' || tf === '5' || tf === '1';
  const scaleReal = intraday ? !!frame.real5 : (dataMode === 'real');
  const scaleLabel = intraday
    ? (frame.real5 ? (tf === '1' ? '真·1min' : '真·5min') : (dataMode === 'real' ? '日内·合成' : '样例'))
    : (dataMode === 'real' ? '真·日线' : '样例');
  const scaleTitle = intraday
    ? (frame.real5 ? (tf === '1' ? '盘中实时 1min(/seats/bars_live?freq=1min,引擎 pytdx)' : '日内来自 stock_data 真 5min 聚合(/seats/daily?freq=5min)') : '真 5min/1min 未到 / 窗口越界 → subdivideDay 合成,仅形态示意')
    : (dataMode === 'real' ? '日K来自 stock_data 真数据(/seats/daily)' : '真日K未到 / 后端未起 → 回退合成样例');
  // 今日页补今日柱(forming 实时 / 缓存快照;只入显示帧,绝不入 symbol.bars)
  const dispFrame = (() => {
    if (page !== 'today' || tf !== 'D') return frame;
    const last = symbol.bars[n - 1];
    if (!last) return frame;
    if (quote && Number.isFinite(+quote.price) && quote.asofDate && quote.asofDate > last.date) {
      const fo = +(quote.open != null ? quote.open : quote.price), fc = +quote.price;
      const fb = { i: frame.fbars.length, date: quote.asofDate, o: fo, c: fc,
        h: Math.max(quote.high != null ? +quote.high : -Infinity, fo, fc),
        l: Math.min(quote.low != null ? +quote.low : Infinity, fo, fc),
        v: +(quote.volume != null ? quote.volume : 0), forming: !!quote.fresh, today: true, event: false };
      return Object.assign({}, frame, { fbars: frame.fbars.concat([fb]), viewEnd: frame.viewEnd + 1 });
    }
    if (liveBar && liveBar.date > last.date) {
      const t = new Date(liveBar.date + 'T00:00:00').getTime();
      const today0 = new Date(); today0.setHours(0, 0, 0, 0);
      if (isFinite(t) && (today0.getTime() - t) <= 14 * 86400000) {
        const fb = { i: frame.fbars.length, date: liveBar.date, o: +liveBar.o, c: +liveBar.c,
          h: +liveBar.h, l: +liveBar.l, v: +liveBar.v || 0, forming: false, today: true, cached: true, event: false };
        return Object.assign({}, frame, { fbars: frame.fbars.concat([fb]), viewEnd: frame.viewEnd + 1 });
      }
    }
    return frame;
  })();
  // K线金框标记:复盘=选中 run(或进行中真跑);今日=后端落盘时间线
  const chartMarks = (() => {
    const src = page === 'replay' ? (selRun ? runDecs : (realDecs[code] || [])) : todayDecs;
    return window.lzMapDecsToFrame ? window.lzMapDecsToFrame(src, dispFrame.fbars) : [];
  })();
  // 可见窗口
  const maxEnd = dispFrame.viewEnd;
  const autoWin = page === 'today' ? (tf === 'D' ? 56 : tf === 'W' ? 22 : 60) : (maxEnd + 1);
  const vWin = Math.max(10, Math.min(zoom != null ? zoom : autoWin, maxEnd + 1));
  const vEnd = Math.max(vWin - 1, Math.min(panEnd != null ? panEnd : maxEnd, maxEnd));
  const chartView = { start: Math.max(0, vEnd - vWin + 1), end: vEnd };
  // 新闻:今日=live;复盘=PIT(asof=选中决策日 || run 终点 || 末根)
  const newsAsof = page === 'today' ? '' : ((selected && selected.date) || (selRun && (selRun.end_date || '')) || (symbol.bars[n - 1] || {}).date || '');
  useEffect(() => {
    if (!code) { setNewsPayload(null); return; }
    let alive = true;
    const t = setTimeout(() => {
      const nmode = page === 'today' ? 'live' : 'pit';
      window.lzFetchNews && window.lzFetchNews(code, page === 'today' ? '' : String(newsAsof).slice(0, 10), nmode).then(p => { if (alive) setNewsPayload(p); });
    }, 250);
    return () => { alive = false; clearTimeout(t); };
  }, [code, newsAsof, page]);
  const newsMarkers = useMemo(
    () => (window.lzMapNewsToFrame && newsPayload)
      ? window.lzMapNewsToFrame(newsPayload.items || [], dispFrame.fbars, newsKw) : [],
    [newsPayload, dispFrame, newsKw]);

  // 基准末个非 null 值
  let benchTotal = null;
  if (symbol.bench) {
    for (let bi = Math.min(n, symbol.bench.length) - 1; bi >= 0; bi--) {
      if (symbol.bench[bi] != null) { benchTotal = symbol.bench[bi] - 1; break; }
    }
  }
  const eqMin = page === 'replay' && selRun && selRun.tf === '30min' && repPerf;
  const eqLen = eqMin ? repPerf.eq.length : n;
  const eqReveal = eqMin ? repPerf.eq.length - 1 : cursor;
  const equityLines = [
    ...(repPerf ? [{ eq: repPerf.eq, color: 'var(--yin)', width: 2, fill: true, name: curName + ' · 纯LLM' }] : []),
    ...((repPerfHybrid && anyHybrid) ? [{ eq: repPerfHybrid.eq, color: 'var(--zhu)', width: 1.6, dash: '5 3', name: '混合(因子进信号)' }] : []),
    ...((symbol.bench && !eqMin) ? [{ eq: symbol.bench, color: 'var(--ink-3)', width: 1.2, dash: '4 3', dim: true, name: '基准' }] : []),
  ];
  const hybridDelta = (repPerf && repPerf.metrics && repPerfHybrid && repPerfHybrid.metrics)
    ? (repPerfHybrid.metrics.total - repPerf.metrics.total) : null;

  // ── 中栏(K线 + 头部;两页共用,差异经 props)──
  const chartCenter = (showEquity) => (
    <div style={{ flex: 1, display: 'flex', flexDirection: 'column', minHeight: 0, minWidth: 0, borderRight: '1px solid var(--line)' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '6px 14px', borderBottom: '1px solid var(--line-soft)', flexShrink: 0 }}>
        <span className="serif" style={{ fontSize: 13, fontWeight: 600 }}>{symbol.meta.name}</span>
        <span className="mono" style={{ fontSize: 10, color: 'var(--ink-3)' }}>{code}</span>
        <span className="mono" title={scaleTitle}
          style={{ fontSize: 8.5, padding: '1px 6px', borderRadius: 4, whiteSpace: 'nowrap', border: '1px solid ' + (scaleReal ? 'var(--zhu-soft)' : 'var(--line)'), color: scaleReal ? 'var(--yin)' : 'var(--ink-3)' }}>
          {scaleLabel}
        </span>
        <TfPicker tf={tf} setTf={setTf} />
        <input value={newsKw} onChange={e => setNewsKw(e.target.value)}
          placeholder="新闻关键词 加息|非农|本票名"
          style={{ font: '11px var(--mono)', padding: '2px 7px', border: '1px solid var(--line)', borderRadius: 5, background: 'var(--paper)', color: 'var(--ink)', width: 150 }} />
        {newsPayload && newsPayload.coverage && newsPayload.coverage.partial &&
          <span className="mono" style={{ fontSize: 9.5, color: 'var(--ink-3)' }}>覆盖不全&lt;{newsPayload.coverage.floor}</span>}
        <span style={{ flex: 1 }} />
        {page === 'replay' && repPerf && <span onClick={distillToCard} className="mono" title="复盘回灌 · 提炼为新经验卡入共享库" style={{ fontSize: 10, color: 'var(--yin)', border: '1px solid var(--zhu-soft)', borderRadius: 6, padding: '3px 9px', cursor: 'pointer' }}>↺ 提炼为经验卡</span>}
        <span className="mono" style={{ fontSize: 9.5, color: 'var(--ink-3)' }}>朱砂涨 · 黛绿跌 · B 买 · S 卖</span>
      </div>
      <div style={{ flex: 1, minHeight: 0, position: 'relative' }}>
        <CandleChart bars={dispFrame.fbars} decisions={[]} truedecs={chartMarks} activeSeats={selRun ? Array.from(new Set(runDecs.map(d => d.seat))) : active} selected={selected} onSelect={setSelected} revealTo={dispFrame.freveal} view={chartView} live={page === 'today'} asOf={{ on: false, date: '' }} triggers={orderTriggers} newsMarkers={newsMarkers} onNewsClick={setNewsPanel} />
        {newsPanel && (
          <div style={{ position: 'absolute', top: 8, right: 8, width: 300, maxHeight: '70%', overflow: 'auto', background: 'var(--paper)', border: '1px solid var(--line)', borderRadius: 8, boxShadow: '0 6px 20px rgba(28,24,20,0.18)', padding: '8px 10px', zIndex: 5 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 6 }}>
              <b className="serif" style={{ fontSize: 12, color: 'var(--ink)' }}>当日快讯 · {newsPanel.count} 条</b>
              <span onClick={() => setNewsPanel(null)} style={{ marginLeft: 'auto', cursor: 'pointer', color: 'var(--ink-3)', fontSize: 14 }}>×</span>
            </div>
            {(newsPanel.items || []).map((it, i) => {
              const hit = newsKw && newsKw.split('|').map(s => s.trim()).filter(Boolean).some(k => (it.title || '').indexOf(k) >= 0);
              return (
                <div key={i} style={{ display: 'flex', gap: 8, alignItems: 'baseline', padding: '5px 6px', borderRadius: 5, background: hit ? 'rgba(191,138,23,0.12)' : 'transparent' }}>
                  <span className="mono" style={{ fontSize: 10, color: 'var(--ink-3)', minWidth: 40 }}>{String(it.ts || '').slice(5, 16).replace('T', ' ')}</span>
                  <span className="mono" style={{ fontSize: 9.5, color: 'var(--ink-3)', border: '0.5px solid var(--line)', borderRadius: 4, padding: '0 5px' }}>{it.source || it.level}</span>
                  <span style={{ fontSize: 12, color: 'var(--ink)' }}>{it.title}</span>
                </div>
              );
            })}
          </div>
        )}
      </div>
      <ChartNav fbars={dispFrame.fbars} win={vWin} end={vEnd} maxEnd={maxEnd} setZoom={setZoom} setPanEnd={setPanEnd} />
      {showEquity && (
        <div style={{ height: 150, borderTop: '1px solid var(--line)', flexShrink: 0, position: 'relative' }}>
          <div style={{ position: 'absolute', top: 5, left: 10, zIndex: 2, display: 'flex', gap: 12, fontFamily: 'var(--mono)', fontSize: 9.5, color: 'var(--ink-3)' }}>
            <span className="serif" style={{ fontSize: 11, color: 'var(--ink-2)', fontWeight: 600 }}>收益曲线</span>
            {repPerf && <span><span style={{ color: 'var(--yin)' }}>━</span> {curName} · 纯LLM(按 run 真决策模拟成交)</span>}
            {(repPerfHybrid && anyHybrid) && <span><span style={{ color: 'var(--zhu)' }}>┅</span> 混合(因子进信号){(repPerf && hybridDelta != null) ? ' · Δtotal=' + (hybridDelta >= 0 ? '+' : '') + (hybridDelta * 100).toFixed(2) + '%' : (!repPerf ? ' · 纯LLM零成交,仅混合线' : '')}</span>}
            {(repPerf && repPerfHybrid && !anyHybrid) && <span style={{ color: 'var(--ink-3)' }}>w=0 · 两线重合(未混入因子)</span>}
            {(!selRun && !repPerf) && <span style={{ color: 'var(--ink-3)' }}>未选回测 —— 上方向导跑一次,或右栏点开历史 run</span>}
            {(selRun && !repPerf && !(repPerfHybrid && anyHybrid)) && <span style={{ color: 'var(--ink-3)' }}>本 run 纯LLM全观望 · 零成交(空仓避险,无净值可画)</span>}
            {(symbol.bench && !eqMin) && <span><span style={{ color: 'var(--ink-3)' }}>┄</span> 基准</span>}
            {eqMin && <span style={{ color: 'var(--ink-3)' }}>30 分粒度 · 基准另案对齐</span>}
          </div>
          <EquityChart lines={equityLines} revealTo={eqReveal} len={eqLen} />
        </div>
      )}
    </div>
  );

  // ── 复盘向导条 ──
  const dayBarsAll = symbol.bars;
  const defStartShown = dayBarsAll[Math.max(0, dayBarsAll.length - (wizFreq === '30min' ? 10 : 120))].date;
  const defEndShown = dayBarsAll[dayBarsAll.length - 1].date;
  const wizardBar = (
    <div style={{ display: 'flex', alignItems: 'center', gap: 9, padding: '7px 16px', borderBottom: '1px solid var(--line)', background: 'rgba(168,57,45,0.03)', flexShrink: 0, flexWrap: 'wrap' }}>
      <span className="serif" style={{ fontSize: 12.5, fontWeight: 600, color: 'var(--ink-1)' }}>新建回测</span>
      <select value={code} onChange={e => setCode(e.target.value)} className="mono" style={{ fontSize: 10.5, padding: '3px 7px', border: '1px solid var(--line)', borderRadius: 6, background: 'var(--paper)', color: 'var(--ink-1)', cursor: 'pointer' }}>
        {window.LZ_SYMBOL_META.map(m => <option key={m.code} value={m.code}>{m.name} {m.code}</option>)}
      </select>
      <select value={curSid || ''} onChange={e => setCurStratId(e.target.value)} className="mono" style={{ fontSize: 10.5, padding: '3px 7px', border: '1px solid var(--line)', borderRadius: 6, background: 'var(--paper)', color: 'var(--ink-1)', cursor: 'pointer' }}>
        {strategies.map(s => <option key={s.id} value={s.id}>{s.name}</option>)}
        {!strategies.length && <option value="">无策略 · 去「策略」页建</option>}
      </select>
      <input type="date" value={wizStart || defStartShown} onChange={e => setWizStart(e.target.value)} className="mono" title="起始日(默认近 120 交易日)" style={{ fontSize: 10.5, padding: '2px 6px', border: '1px solid var(--line)', borderRadius: 6, background: 'var(--paper)', color: 'var(--ink-1)' }} />
      <span className="mono" style={{ fontSize: 10, color: 'var(--ink-3)' }}>→</span>
      <input type="date" value={wizEnd || defEndShown} onChange={e => setWizEnd(e.target.value)} className="mono" title="结束日(默认最新)" style={{ fontSize: 10.5, padding: '2px 6px', border: '1px solid var(--line)', borderRadius: 6, background: 'var(--paper)', color: 'var(--ink-1)' }} />
      <select value={wizFreq} onChange={e => setWizFreq(e.target.value)} className="mono" title="研判粒度:日线=每交易日一判;30分钟=盘中逐 30min bar 判(需真 5min)" style={{ fontSize: 10.5, padding: '3px 7px', border: '1px solid var(--line)', borderRadius: 6, background: 'var(--paper)', color: 'var(--ink-1)', cursor: 'pointer' }}>
        <option value="day">日线</option>
        <option value="30min">30 分钟</option>
      </select>
      <span onClick={runRealThink} title="逐 bar 真调 LLM(PIT·只用≤当日信息);运行中再点=停;跑完自动选中该 run"
        className="serif" style={{ display: 'flex', alignItems: 'center', gap: 6, cursor: 'pointer', whiteSpace: 'nowrap', fontSize: 11.5, padding: '4px 13px', borderRadius: 7, flexShrink: 0, border: '1px solid ' + (realRun.running ? 'var(--dai)' : 'var(--zhu-soft)'), color: realRun.running ? 'var(--dai)' : 'var(--yin)', background: realRun.running ? 'rgba(74,107,92,0.07)' : 'rgba(168,57,45,0.05)' }}>
        {realRun.running
          ? <React.Fragment><span style={{ width: 6, height: 6, borderRadius: '50%', background: 'var(--dai)', animation: 'pulse 1s ease-in-out infinite' }} />停止 · {realRun.done}/{realRun.total}{realRun.errors ? ' · ' + realRun.errors + '失败' : ''}</React.Fragment>
          : '✦ 开始真跑'}
      </span>
      <span className="mono" style={{ fontSize: 8.5, color: 'var(--ink-3)' }}>每 bar 一次 LLM 调用(fast),120 日 ≈ 数分钟;可中途停,已跑段照样成 run</span>
    </div>
  );

  return (
    <div className="paper-bg" style={{ width: '100%', height: '100vh', display: 'flex', flexDirection: 'column', overflow: 'hidden', fontFamily: 'var(--sans)', color: 'var(--ink)' }}>
      <TopBar page={page} setPage={setPage} dark={dark} setDark={setDark} />
      {page === 'strategy' ? (
        <Foundry />
      ) : page === 'today' ? (
        <React.Fragment>
          <MarketBar symbol={symbol} revealTo={cursor} mode="live" market={market} quote={quote} />
          <WatchStrip watch={watch} onToggle={toggleWatch} />
          <div style={{ flex: 1, display: 'flex', minHeight: 0, overflow: 'hidden' }}>
            <TicketList code={code} onSelect={setCode} poolTick={poolTickV} />
            <div style={{ flex: 1, display: 'flex', flexDirection: 'column', minHeight: 0, minWidth: 0 }}>
              <div style={{ display: 'flex', alignItems: 'stretch', borderBottom: '1px solid var(--line)', flexShrink: 0, overflowX: 'auto' }}>
                <StockHero symbol={symbol} rt={cursor} mode="live" quote={quote} />
                <div style={{ display: 'flex', flexDirection: 'column', justifyContent: 'center', padding: '0 14px' }}>
                  <span className="serif" style={{ fontSize: 12, fontWeight: 600, color: 'var(--yin)', whiteSpace: 'nowrap' }}>{curName}</span>
                  <span className="mono" style={{ fontSize: 8, color: 'var(--ink-3)', marginTop: 2, whiteSpace: 'nowrap' }}>当前策略 · 绑票/配方去「策略」页</span>
                </div>
              </div>
              {chartCenter(false)}
            </div>
            <div style={{ width: 372, flexShrink: 0, display: 'flex', flexDirection: 'column', minHeight: 0, overflowY: 'auto', overflowX: 'hidden', background: 'var(--paper)' }}>
              <JudgeCard code={symbol.meta.code} name={symbol.meta.name} industry={symbol.meta.industry || ''} strat={curStrat} regime={market && market.regime} />
              <OrderWatchPanel code={symbol.meta.code} name={symbol.meta.name} mode="live" asOf={null} seatId={curSid} strategies={strategies}
                onTrigger={(t) => setOrderTriggers(ts => [...ts.filter(x => x.id !== t.id), t])} />
              <DecisionTrail code={symbol.meta.code} />
              <OrderbookTicksPanel book={book} ticks={ticks} />
            </div>
          </div>
        </React.Fragment>
      ) : (
        <React.Fragment>
          <MarketBar symbol={symbol} revealTo={cursor} mode="backtest" market={market} quote={null} />
          {wizardBar}
          <MetricsStrip m={repPerf ? repPerf.metrics : ((repPerfHybrid && anyHybrid) ? repPerfHybrid.metrics : null)} benchTotal={benchTotal} label={'回测 · ' + curName + ((!repPerf && repPerfHybrid && anyHybrid) ? ' · 混合线' : '')} symbol={symbol} rt={cursor} mode="backtest" quote={null} ledger={null} />
          <div style={{ flex: 1, display: 'flex', minHeight: 0, overflow: 'hidden' }}>
            {chartCenter(true)}
            <div style={{ width: 372, flexShrink: 0, display: 'flex', flexDirection: 'column', minHeight: 0, overflowY: 'auto', overflowX: 'hidden', background: 'var(--paper)' }}>
              <RunPicker code={code} bump={runsBump} selRun={selRun} strategyId={curSid}
                strategyName={curName} runDecs={runDecs} selected={selected} onPickDec={setSelected}
                onSelect={(r) => { setSelRun(p => p && r && p.run_id === r.run_id ? null : r); setSelected(null); }} />
              <div style={{ flexShrink: 0, minHeight: 320 }}>
                {selected && selected._isRun
                  ? <RunDecCard dec={selected} />
                  : <div style={{ padding: 20, color: 'var(--ink-3)', fontFamily: 'var(--serif)', fontSize: 12.5, lineHeight: 1.7, textWrap: 'pretty' }}>
                      点上方 run 内的任意一笔决策,或 K 线上的金框 <b style={{ color: 'var(--zhu)' }}>B</b>/<b style={{ color: 'var(--dai)' }}>S</b> —— 此处摊开完整证据链:vintage 因子 IC、当日浮出研报、当日大盘、价量形态与思维链。
                    </div>}
              </div>
            </div>
          </div>
        </React.Fragment>
      )}
      {toast && (
        <div style={{ position: 'fixed', bottom: 70, left: '50%', transform: 'translateX(-50%)', zIndex: 60, display: 'flex', alignItems: 'center', gap: 11, background: 'var(--paper)', border: '1px solid var(--dai-soft)', borderRadius: 11, padding: '11px 16px', boxShadow: '0 6px 26px rgba(28,24,20,0.18)', animation: 'fadeIn .3s ease' }}>
          <span className="seal" style={{ width: 22, height: 22, fontSize: 12, background: 'var(--dai)' }}>瀾</span>
          <span className="serif" style={{ fontSize: 12.5, color: 'var(--ink-1)' }}>{toast.kind === 'handoff' ? toast.text : <React.Fragment>复盘已提炼为草稿经验卡「{toast.name}」· 入共享档案库</React.Fragment>}</span>
          {toast.kind !== 'handoff' && toast.name && <a href="../graph/观澜 · 研究图谱.html" className="mono" style={{ fontSize: 10.5, color: 'var(--yin)', textDecoration: 'none', borderBottom: '1px dashed var(--zhu-soft)' }}>看图谱 →</a>}
        </div>
      )}
    </div>
  );
}

// ───────── 顶栏(单轴三页)─────────
function TopBar({ page, setPage, dark, setDark }) {
  const tabs = [['today', '今日'], ['replay', '复盘'], ['strategy', '策略']];
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 13, padding: '0 16px', height: 52, borderBottom: '1px solid var(--line)', background: 'rgba(241,234,217,0.6)', flexShrink: 0 }}>
      {!WW_EMBED && (<React.Fragment>
      <span className="serif" style={{ fontSize: 15, fontWeight: 600, letterSpacing: '.04em', whiteSpace: 'nowrap', flexShrink: 0 }}>落子</span>
      <span className="mono" style={{ fontSize: 9, color: 'var(--ink-3)', border: '1px solid var(--line)', borderRadius: 5, padding: '2px 6px', whiteSpace: 'nowrap', flexShrink: 0 }}>交易决策 agent</span>
      <span style={{ color: 'var(--line)' }}>|</span>
      </React.Fragment>)}
      <div style={{ display: 'flex', border: '1px solid var(--line)', borderRadius: 8, overflow: 'hidden' }}>
        {tabs.map(([k, label]) => (
          <span key={k} onClick={() => setPage(k)} className="serif" style={{
            fontSize: 12.5, padding: '5px 16px', cursor: 'pointer',
            background: page === k ? 'var(--ink)' : 'transparent', color: page === k ? 'var(--paper)' : 'var(--ink-2)',
          }}>{label}</span>
        ))}
      </div>
      <span className="mono" style={{ fontSize: 8.5, color: 'var(--ink-3)', whiteSpace: 'nowrap' }}>
        {page === 'today' ? '看盘 · 研判 · 条件单 · 后端盯盘' : page === 'replay' ? '选区间一键真跑 · run 化历史' : '策略装配 · 配方 · 绑票'}
      </span>
      <span onClick={() => setDark(d => !d)} className="mono" title="宣纸 / 月夜"
        style={{ marginLeft: 'auto', fontSize: 11, padding: '4px 11px', border: '1px solid var(--line)', borderRadius: 7, color: 'var(--ink-2)', cursor: 'pointer', whiteSpace: 'nowrap' }}>
        {dark ? '◐ 月夜' : '◑ 宣纸'}
      </span>
    </div>
  );
}

// ───────── 时间尺度选择 ─────────
function TfPicker({ tf, setTf }) {
  const opts = [['W', '周'], ['D', '日'], ['60', '60分'], ['30', '30分'], ['15', '15分'], ['5', '5分'], ['1', '1分']];
  return (
    <div style={{ display: 'flex', gap: 3 }}>
      {opts.map(([k, l]) => (
        <span key={k} onClick={() => setTf(k)} className="mono" style={{ fontSize: 10, padding: '2px 7px', borderRadius: 5, cursor: 'pointer', border: '1px solid ' + (tf === k ? 'var(--ink)' : 'var(--line)'), background: tf === k ? 'var(--ink)' : 'transparent', color: tf === k ? 'var(--paper)' : 'var(--ink-3)' }}>{l}</span>
      ))}
    </div>
  );
}

// ───────── 图表导航条 (尺度缩放 + 时间轴平移) ─────────
function ChartNav({ fbars, win, end, maxEnd, setZoom, setPanEnd }) {
  const total = maxEnd + 1;
  const canPan = win <= maxEnd;
  const start = Math.max(0, end - win + 1);
  const dOf = (i) => { const b = fbars[Math.max(0, Math.min(i, fbars.length - 1))]; return b ? (b.date || '').slice(5) : ''; };
  const btn = { display: 'inline-flex', alignItems: 'center', justifyContent: 'center', width: 18, height: 16, fontSize: 11, cursor: 'pointer', border: '1px solid var(--line)', borderRadius: 4, color: 'var(--ink-2)', userSelect: 'none' };
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '4px 14px', borderTop: '1px solid var(--line-soft)', background: 'rgba(28,24,20,0.015)', flexShrink: 0 }}>
      <span className="mono" style={{ fontSize: 9, color: 'var(--ink-3)', whiteSpace: 'nowrap' }}>尺度</span>
      <span onClick={() => setZoom(Math.max(10, Math.round(win * 0.6)))} title="放大 · 更少 K" style={btn}>−</span>
      <input type="range" min={10} max={total} value={win}
        onChange={e => { const w = +e.target.value; setZoom(w >= total ? null : w); }}
        title="可见 K 线数(尺度)" style={{ width: 100, accentColor: 'var(--yin)', cursor: 'pointer' }} />
      <span onClick={() => setZoom(Math.min(total, Math.round(win * 1.6)))} title="缩小 · 更多 K" style={btn}>+</span>
      <span onClick={() => { setZoom(null); setPanEnd(null); }} title="复位 · 显示全部" style={{ ...btn, width: 'auto', padding: '0 6px', fontSize: 10 }}>全</span>
      <span style={{ color: 'var(--line)', fontSize: 11 }}>|</span>
      <span className="mono" style={{ fontSize: 9, color: 'var(--ink-3)', whiteSpace: 'nowrap' }}>滑动</span>
      <input type="range" min={Math.max(0, win - 1)} max={maxEnd} value={end} disabled={!canPan}
        onChange={e => { const v = +e.target.value; setPanEnd(v >= maxEnd ? null : v); }}
        title="沿时间轴平移" style={{ flex: 1, accentColor: 'var(--yin)', cursor: canPan ? 'pointer' : 'default', opacity: canPan ? 1 : 0.35 }} />
      <span className="mono" style={{ fontSize: 9.5, color: 'var(--ink-2)', whiteSpace: 'nowrap', minWidth: 124, textAlign: 'right' }}>
        {dOf(start)}–{dOf(end)} · {win}/{total} 根
      </span>
    </div>
  );
}
