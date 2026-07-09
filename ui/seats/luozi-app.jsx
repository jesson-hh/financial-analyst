// 观澜 · 落子 — 主壳 (复盘/实盘 · 单标/舰队 · 回放控制 · 当前策略·单 agent)
const { useMemo, useCallback: useCB } = React;

// 帷幄融合旗(对齐 screen-app):EMBED=被帷幄嵌入右栏(只隐顶栏身份区;哨兵 agent 窗口/席位/研判一概保留);
// WS=帷幄会话 id,handoff 信箱按会话取防串扰;独立打开 WS='' 裸键如旧。
const WW_EMBED = new URLSearchParams(location.search).get('embed') === '1';
const WW_WS = new URLSearchParams(location.search).get('ws') || '';

function strategyMetrics(symbol, sid) {
  // T5 单 agent:净值/指标 = 当前策略自身(多席等权合议退役,lzConsensusEquity 不再调用、函数本体保留在 data 层)。
  // perSeat 缺该键 → 平线 [1…](与旧 consensusEquity 空守卫同形)+ 零指标,诚实空。
  const ps = sid ? symbol.perSeat[sid] : null;
  if (!ps) {
    const eq = new Array(Math.max(1, symbol.bars.length)).fill(1);
    return { eq, metrics: window.lzMetricsOf(eq, []) };
  }
  return { eq: ps.eq, metrics: ps.metrics || window.lzMetricsOf(ps.eq, ps.trades || []) };
}

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

function LuoziApp() {
  const [mode, setMode] = useState('backtest');     // backtest | live
  const [view, setView] = useState('single');        // single | fleet
  const [workspace, setWorkspace] = useState('desk'); // desk | foundry
  const [code, setCode] = useState(window.LZ_PRIMARY);
  const [strategies, setStrategies] = useState(() => window.lzStrategyForCode ? window.lzStrategyForCode(window.LZ_PRIMARY) : []);
  const [curStratId, setCurStratId] = useState(null); // T5 单 agent:当前策略 id(null/失效 → curSid 回退本票首个策略)
  const [selected, setSelected] = useState(null);
  const [cursor, setCursor] = useState(119);
  const [markerReveal, setMarkerReveal] = useState(119);
  const [thinking, setThinking] = useState(null);
  const [tf, setTf] = useState('D');
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState(1);
  const [showTweaks, setShowTweaks] = useState(false);
  const [dark, setDark] = useState(false);
  const [toast, setToast] = useState(null);
  const [, setPoolTick] = useState(0);          // 盯盘池增删后强制重渲染(池数组是模块级,React 看不见变更)
  const [realSyms, setRealSyms] = useState({});       // code → 用真日K装配的 symbol(覆盖合成)
  const [dataMode, setDataMode] = useState('mock');   // real | mock(真日K未到/后端未起时回退合成)
  const [zoom, setZoom] = useState(null);             // 可见 K 数(尺度);null = 适配全部
  const [panEnd, setPanEnd] = useState(null);         // 可见窗口右边缘 idx(滑动);null = 跟随最新
  const [market, setMarket] = useState(null);         // 今日市场状态(/watch/market_status:regime+涨跌停)
  const [quote, setQuote] = useState(null);           // ④ 实盘实时盘口(/seats/quote:腾讯实时价,仅 live 轮询)
  const [book, setBook] = useState(null);             // ② 五档盘口(/seats/orderbook:tdx,仅 live 轮询)
  const [ticks, setTicks] = useState(null);           // ② 逐笔成交(/seats/ticks:tdx,仅 live 轮询)
  const [newsKw, setNewsKw] = useState('');
  const [newsPayload, setNewsPayload] = useState(null);
  const [newsPanel, setNewsPanel] = useState(null);
  const [shadow, setShadow] = useState({ goLive: null, positions: [] });
  useEffect(() => { setShadow(window.lzShadowLoad ? window.lzShadowLoad(code) : { goLive: null, positions: [] }); }, [code]);
  const [liveBar, setLiveBar] = useState(null);       // ④-③ 实盘今日柱缓存(实时源;盘后/隔夜保持图有今天,官方日K入库即让位)
  useEffect(() => { setLiveBar(window.lzLivebarLoad ? window.lzLivebarLoad(code) : null); }, [code]);
  // T6 实盘仓位台账:后端持久(GET /seats/ledger/state);live 进入 / 落账(ledgerBump)即拉,
  //   非 live 不拉也**不清空**(回到 live 直接有缓存);失败 lzLedgerState 返 null = 「载入中」诚实降级。
  const [ledger, setLedger] = useState(null);
  const [ledgerBump, setLedgerBump] = useState(0);
  const refreshLedger = () => setLedgerBump(x => x + 1);
  useEffect(() => {
    if (mode !== 'live' || !window.lzLedgerState) return;
    let dead = false;
    window.lzLedgerState().then(st => { if (!dead) setLedger(st); });
    return () => { dead = true; };
  }, [mode, ledgerBump]);
  const [realDecs, setRealDecs] = useState({});       // ⑤++ 真·思考决策(/seats/decide LLM)独立存:realDecs[code]=[…];绝不入 symbol.decisions/合议/净值
  const [fleetWatch, setFleetWatch] = useState(false);   // 舰队盯盘主开关(默认关,不持久;防意外后台烧 LLM)
  const [monQuotes, setMonQuotes] = useState({});         // 盯盘票实时报价 {code:quote}(复用 /seats/quote)
  const monQuotesRef = useRef({});                        // latest-ref:盯盘循环里读最新报价,避免 60s interval 陈旧闭包
  const realDecsRef = useRef({});                         // latest-ref:节流去重读最新 realDecs(与单股循环共享 ts 去重)
  const recordRef = useRef(null);                         // latest-ref:盯盘循环里调最新 recordLiveDecide
  useEffect(() => { monQuotesRef.current = monQuotes; }, [monQuotes]);
  useEffect(() => { realDecsRef.current = realDecs; }, [realDecs]);
  const [realRun, setRealRun] = useState({ running: false, done: 0, total: 0, cur: null, seatName: '', errors: 0 });
  const [runsBump, setRunsBump] = useState(0);        // run 化:真跑注册 run 头后 +1(回测历史面板按此重拉,T4 消费)
  const [selRun, setSelRun] = useState(null);         // T4:回测历史选中的 run 头(null=实时 scanSeat 视图)
  const [runDecs, setRunDecs] = useState([]);         // T4:选中 run 的决策(asof 映射到 bar idx 后供 K线/流水/详情卡消费)
  const realStopRef = useRef(false);
  useEffect(() => { realStopRef.current = true; }, [code, mode]);   // 切票/切模式 → 停掉进行中的真跑
  // 第3期:当前票在场策略(订阅 GL,增删改 / 跨标签即刷新)
  useEffect(() => {
    setCurStratId(null);   // T5 切票:当前策略复位 → curSid 自动回退该票首个策略
    const refresh = () => setStrategies(window.lzStrategyForCode ? window.lzStrategyForCode(code) : []);
    refresh();
    const off = window.GL ? window.GL.on(refresh) : null;
    return () => { if (off) off(); };
  }, [code]);
  // P1⑨:接 cockpit 交棒(选股「据此落子」篮子 / 图谱跳转)。此前全仓无人收,payload 永久滞留。
  // 池内票 → 聚焦;池外票 → 诚实提示(盯盘池为固定 6 只,扩池另行规划,绝不静默吞)。
  useEffect(() => {
    // 提示条直插 DOM(不走 React state):本页初始 effect 风暴下 toast state 置上但提交不显形(fiber 实测),
    // 通知类一次性 UI 用原生节点最稳;样式照抄原 toast。聚焦(setCode)仍走 React。
    const notice = (text) => {
      try {
        const d = document.createElement('div');
        d.id = 'lz-handoff-notice';
        d.style.cssText = 'position:fixed;bottom:70px;left:50%;transform:translateX(-50%);z-index:9500;display:flex;align-items:center;gap:11px;background:var(--paper,#f1ead9);border:1px solid var(--dai-soft,#9db4a8);border-radius:11px;padding:11px 16px;box-shadow:0 6px 26px rgba(28,24,20,0.18);font-family:var(--serif,serif);font-size:12.5px;color:var(--ink-1,#3a332b);max-width:72vw;';
        d.innerHTML = '<span style="width:22px;height:22px;font-size:12px;background:var(--dai,#4a6b5c);color:var(--paper,#f1ead9);display:flex;align-items:center;justify-content:center;border-radius:6px;flex-shrink:0">瀾</span><span></span><span style="cursor:pointer;color:var(--ink-3,#999);font-size:14px;padding:0 2px;flex-shrink:0">×</span>';
        d.children[1].textContent = text;
        d.lastChild.onclick = () => { try { d.remove(); } catch (e) {} };
        document.body.appendChild(d);   // 常驻直到手点 ×:通知不再被任何时间窗吞掉
      } catch (e) {}
    };
    const tick = setTimeout(() => {
      const h = window.GL && window.GL.take ? window.GL.take('cockpit', WW_WS) : null;
      if (!h) return;
      const bare = (c) => String(c || '').replace(/^(SH|SZ|BJ)/i, '');
      if (h.fromScreen && Array.isArray(h.basket) && h.basket.length) {
        // 扩池:整篮入盯盘池(幂等去重;动态票顶栏可切、可「移出盯盘池」;真日K 由 per-code 拉取自动接管)
        let added = 0;
        h.basket.forEach(b => {
          if (b && window.lzPoolAdd && window.lzPoolAdd({ code: bare(b.code), name: b.name, ind: b.ind })) added++;
        });
        if (added) setPoolTick(t => t + 1);   // 池数组是模块级,补一拍让顶栏下拉/舰队重渲染
        const first = h.basket.find(b => b && window.LZ_SYMBOLS && window.LZ_SYMBOLS[bare(b.code)]);
        if (first) setCode(bare(first.code));
        const names = h.basket.slice(0, 5).map(b => (b.name || b.code)).join('、') + (h.basket.length > 5 ? '…' : '');
        notice('已接收选股篮子 ' + h.basket.length + ' 只(' + names + '):新入盯盘池 ' + added + ' 只'
          + (first ? ',已聚焦 ' + (first.name || first.code) : '') + ';顶栏可切换,动态票可「移出盯盘池」');
      } else if (h.code) {
        // 帷幄 ww_seats_decide 交棒(payload={code,name},无 fromScreen):入盯盘池;
        // poolAdd 成功即建 SYMBOLS[码] → 聚焦走现成 setCode 入口(池内已有则直接聚焦;坏码只提示不聚焦)。
        const c = bare(h.code);
        const added = !!(window.lzPoolAdd && window.lzPoolAdd({ code: c, name: h.name }));
        if (added) setPoolTick(t => t + 1);
        const focusable = !!(window.LZ_SYMBOLS && window.LZ_SYMBOLS[c]);
        if (focusable) setCode(c);
        notice('已接收帷幄研判交棒 ' + (h.name || c) + (added ? ':新入盯盘池' : '') + (focusable ? ',已聚焦' : ''));
      } else if (h.focusSeat || h.focusId) {
        notice('来自研究图谱的跳转已接收(' + (h.focusSeat || h.focusId) + ')');
      }
    }, 600);
    return () => clearTimeout(tick);
  }, []);
  const [orderTriggers, setOrderTriggers] = useState([]); // ②b 条件单触发落子(OrderWatchPanel 上报 → 标到 K 线)
  // 第3期:symbol 按 (bars, strategies) 反应式重建,perSeat 键为 strategy.id,策略变更即时反映。
  const baseBars = (realSyms[code] && realSyms[code].bars) || window.LZ_SYMBOLS[code].bars;
  const _meta = window.LZ_SYMBOLS[code].meta;
  const symbol = useMemo(() => {
    // 第四参 = 真沪深300原始行(随真K到达时一并拉取存 benchRows);合成路径无 → bench=null 隐藏基准线
    const s = window.lzBuildSymbolFromBars(_meta, baseBars, strategies,
      (realSyms[code] && realSyms[code].benchRows) || null);
    return (realSyms[code] && realSyms[code].bars5) ? Object.assign({}, s, { bars5: realSyms[code].bars5 }) : s;
  }, [baseBars, strategies, code, realSyms]);
  const stratIds = symbol.stratIds || [];
  // T5 单 agent:生效策略 = 显式选中且仍在场,否则回退首个(策略列表变化/切票自适应);
  // active 必须保持数组形状(LiveDecideFlow/CandleChart/播放/FleetCard/Tweaks/runRealThink active[0] 等 8 处依赖)= [curSid] 单元素。
  const curSid = (curStratId && strategies.some(s => s.id === curStratId)) ? curStratId : ((strategies[0] && strategies[0].id) || null);
  const curStrat = strategies.find(s => s.id === curSid) || null;
  const curName = (curStrat && curStrat.name) || '策略';
  const active = useMemo(() => curSid ? [curSid] : [], [curSid]);
  const n = symbol.bars.length;
  const cursorRef = useRef(cursor);
  const thinkingRef = useRef(false);
  useEffect(() => { cursorRef.current = cursor; }, [cursor]);

  useEffect(() => { document.body.classList.toggle('dark', dark); }, [dark]);

  // 今日市场状态(regime + 涨停/跌停),拉一次(快照,不随 code/cursor 变;失败保留 mock)。
  useEffect(() => {
    let alive = true;
    if (window.lzFetchMarketStatus) window.lzFetchMarketStatus().then(m => { if (alive && m) setMarket(m); });
    return () => { alive = false; };
  }, []);

  // ④ 实盘盘口:live 模式下轮询 /seats/quote 真实时价(~6s);切走 / 非 live 清空。失败保留上次(诚实降级,不回退假数据)。
  useEffect(() => {
    if (mode !== 'live' || !window.lzFetchQuote) { setQuote(null); return; }
    let alive = true;
    const pull = () => window.lzFetchQuote(code).then(q => { if (alive && q) setQuote(q); });
    pull();
    const iv = setInterval(pull, 6000);
    return () => { alive = false; clearInterval(iv); };
  }, [mode, code]);

  // ② 五档盘口 + 逐笔:live 模式下轮询 /seats/orderbook + /seats/ticks(~8s,tdx 较重故略慢于报价);
  //   切走/非 live 清空;失败保留上次(诚实降级)。纯展示,绝不喂研判/信号。
  useEffect(() => {
    if (mode !== 'live' || !window.lzFetchOrderbook) { setBook(null); setTicks(null); return; }
    let alive = true;
    const pull = () => {
      window.lzFetchOrderbook(code).then(b => { if (alive && b) setBook(b); });
      window.lzFetchTicks(code, 30).then(t => { if (alive && t) setTicks(t); });
    };
    pull();
    const iv = setInterval(pull, 8000);
    return () => { alive = false; clearInterval(iv); };
  }, [mode, code]);

  // 盯盘票实时报价轮询(复用 /seats/quote;挂在 fleetWatch 上、与 mode 无关)→ 盯盘循环用它判逐股盘中。
  useEffect(() => {
    if (!fleetWatch || !window.lzFetchQuote || !window.lzMonitoredCodes) { setMonQuotes({}); return; }
    let alive = true;
    const pull = () => {
      (window.lzMonitoredCodes() || []).forEach(c => {
        window.lzFetchQuote(c).then(q => { if (alive && q) setMonQuotes(prev => Object.assign({}, prev, { [c]: q })); });
      });
    };
    pull();
    const iv = setInterval(pull, 7000);
    return () => { alive = false; clearInterval(iv); };
  }, [fleetWatch]);

  // 多股盯盘循环(页面驱动):fleetWatch 开 → 每 60s 遍历盯盘集,逐股「真报价盘中 + per-code clock 节流」真调 /seats/decide。
  //   deps 只含 fleetWatch(稳定 60s);变量经 ref 取最新(防 interval 陈旧闭包)。失败该票跳过、不写 realDecs。
  useEffect(() => {
    if (!fleetWatch || !window.lzSeatDecide || !window.lzMonitoredCodes) return;
    let alive = true;
    const tick = async () => {
      const codes = window.lzMonitoredCodes() || [];
      for (let i = 0; i < codes.length; i++) {
        if (!alive) break;
        const c = codes[i];
        const q = monQuotesRef.current[c];
        if (!q || !q.fresh) continue;                                   // 逐股盘中门控:该股实时报价为今日
        const agent = window.lzMonitorAgentFor ? window.lzMonitorAgentFor(c) : null;
        if (!agent) continue;
        const rds = realDecsRef.current[c] || [];
        const lastTs = (rds.length && rds[rds.length - 1].ts) || 0;     // 复盘真跑写的无 ts → 视为很久以前
        const gap = Date.now() - lastTs;
        if (gap < 600000) continue;                                     // 10min 硬地板(与单股循环共享去重)
        const fq = (agent.clock && agent.clock.decisionFreq) || 'hourly';
        const due = fq === 'daily'
          ? (lastTs === 0 || new Date(lastTs).toDateString() !== new Date().toDateString())
          : (gap >= 3600000);
        if (!due) continue;
        const rcp = window.lzRecipeForStrategy ? window.lzRecipeForStrategy(agent.id) : { cards: [], research: [], factors: [] };
        const meta = (window.LZ_SYMBOLS[c] && window.LZ_SYMBOLS[c].meta) || { name: c };
        try {
          const res = await window.lzSeatDecide({
            code: c, name: meta.name, date: new Date().toISOString().slice(0, 10),
            seat_cn: agent.name, creed: agent.creed || '', mode: 'fast',
            strategy_id: agent.id, strategy_name: agent.name,
            card: rcp.cards[0] ? { name: rcp.cards[0].name, insight: rcp.cards[0].insight, verdict: rcp.cards[0].verdict, conf: rcp.cards[0].conf, ic: rcp.cards[0].ic } : null,
            cards: rcp.cards, recipe_factors: rcp.factors, industry: meta.industry || '', regime: null,
            pa: !!agent.pa, pa_method: agent.pa ? (agent.paMethod || window.LZ_PA_METHOD_DEFAULT || '') : '',
            w: agent.w || 0,
          });
          if (alive && res && res.ok && res.direction && recordRef.current) {
            recordRef.current(c, meta.name, { seat: agent.id, direction: res.direction, conf: res.confidence,
              rationale: res.rationale, reasoning: res.reasoning, asof: res.asof, model_name: res.model_name, id: res.id });
          }
        } catch (e) {}
      }
    };
    tick();
    const iv = setInterval(tick, 60000);
    return () => { alive = false; clearInterval(iv); };
  }, [fleetWatch]);

  useEffect(() => {
    if (mode !== 'live' || !quote || quote.price == null || !window.lzShadowCheckExits) return;
    setShadow(sh => { const r = window.lzShadowCheckExits(sh, +quote.price, quote.asofDate); if (r.changed && window.lzShadowSave) window.lzShadowSave(code, r.shadow); return r.changed ? r.shadow : sh; });
  }, [quote, mode, code]);

  // ④-③ 捕获今日柱:live 下报价**晚于**末根真日K日 → 提一根今日真实柱写缓存(盘中滚动刷新,收盘自然定格成完整 OHLC);失败不写,诚实。
  useEffect(() => {
    if (mode !== 'live' || !quote || !window.lzLivebarFromQuote) return;
    const last = symbol.bars[n - 1];
    const nb = window.lzLivebarFromQuote(quote, last ? last.date : null);
    if (nb) { setLiveBar(nb); if (window.lzLivebarSave) window.lzLivebarSave(code, nb); }
  }, [quote, mode, code, n]);

  const curPerf = useMemo(() => strategyMetrics(symbol, curSid), [symbol, curSid]);
  // ★ 诚实回测:复盘的净值/指标/买卖点**只由选中的真实 run 驱动**(从 run 真决策模拟成交);
  //   没选 run(含 0-run 票如立昂微)→ repPerf=null → 全部置空引导,绝不展示 scanSeat 启发式演示。
  //   scanSeat(curPerf)只服务校场演武,落子复盘页不再展示其虚假净值/买卖点。
  const runPerf = useMemo(() => {
    if (mode !== 'backtest' || !selRun || !window.lzRunBacktest) return null;
    const isMin = (selRun.tf === '30min');
    if (!isMin && tf !== 'D') return null;                // 日线 run 仅日线 TF 算净值
    const refBars = isMin ? (window.lzBars30 ? window.lzBars30(symbol) : []) : symbol.bars;
    return window.lzRunBacktest(runDecs, refBars, false);   // 纯 LLM:按 d.direction 派生 side
  }, [mode, selRun, tf, runDecs, symbol]);
  // P3 混合净值:同 runDecs/同 bars 跑第二遍,useHybrid=true → 按 d.hybrid_direction 派生 side。
  //   w=0 时后端 hybrid_direction==direction → 与 runPerf 数据逐位相同 → 两线在图上重合(诚实退化铁证)。
  const runPerfHybrid = useMemo(() => {
    if (mode !== 'backtest' || !selRun || !window.lzRunBacktest) return null;
    const isMin = (selRun.tf === '30min');
    if (!isMin && tf !== 'D') return null;
    const refBars = isMin ? (window.lzBars30 ? window.lzBars30(symbol) : []) : symbol.bars;
    return window.lzRunBacktest(runDecs, refBars, true);    // 混合:按 d.hybrid_direction 派生 side
  }, [mode, selRun, tf, runDecs, symbol]);
  const repPerf = mode === 'backtest' ? runPerf : null;          // 复盘展示口径(实盘走 ledger,不用此)
  const repPerfHybrid = mode === 'backtest' ? runPerfHybrid : null;
  // 是否有任一决策开启混合(w>0):有 → 画混合线 + 归因小字;全 w=0 → 仅注「未混入因子」(避免画重合冗余线但仍显形)
  const anyHybrid = (runDecs || []).some(d => (d && (+d.w || 0) > 0));
  const shadowM = (mode === 'live' && window.lzShadowMetrics) ? window.lzShadowMetrics(shadow, quote && quote.price) : null;
  // 跨票组合聚合(实盘):扫全部影子台账;当前票用 live 状态覆盖 localStorage(求最新),priceMap 仅含当前票现价
  //   —— 诚实:非当前票不编价,shadowAggregate 用 covered/nOpen 标覆盖率,已实现/胜率等仍跨票精确。
  const portfolioM = useMemo(() => {
    if (mode !== 'live' || !window.lzShadowListAll || !window.lzShadowAggregate) return null;
    const books = window.lzShadowListAll().filter(b => b.code !== code);
    if (shadow.goLive || (shadow.positions && shadow.positions.length)) books.push({ code, shadow });
    const priceMap = (quote && quote.price != null) ? { [code]: +quote.price } : {};
    return window.lzShadowAggregate(books, priceMap);
  }, [mode, code, quote, shadow]);
  const startTracking = () => { const d = new Date().toISOString().slice(0, 10); setShadow(sh => { const ns = { goLive: d, positions: sh.positions }; if (window.lzShadowSave) window.lzShadowSave(code, ns); return ns; }); };
  // ⑤++ 复盘「真跑」:从游标往前走,每根日K真调 /seats/decide(PIT·只用≤当日信息),金框标真思考决策。
  //   走到哪算到哪(运行中再点=停)、串行 await、**regime=null 防 look-ahead**;结果独立存 realDecs,绝不入合议/净值/scanSeat。
  //   决策成功即由后端 decide 落盘 → 自动进「研判历史」。fast 模式(秒级);LLM 失败该根跳过不落、不标。
  const runRealThink = async () => {
    if (realRun.running) { realStopRef.current = true; return; }       // 运行中再点 = 停
    if (mode !== 'backtest' || !window.lzSeatDecide) return;
    const isMin = (tf === '30');                    // 实验:仅放开 30 分钟
    if (tf !== 'D' && !isMin) return;               // 其余 TF(周/60/15/5/1)暂不真跑
    const sid = (active && active[0]) || (symbol.stratIds && symbol.stratIds[0]);
    if (!sid) return;
    const strat = window.lzStrategyGet ? window.lzStrategyGet(sid) : null;
    const tmpl = strat && strat.template;
    const seatName = (strat && strat.name) || sid;
    const tmplCreed = (window.LZ_TEMPLATES && tmpl && window.LZ_TEMPLATES[tmpl] && window.LZ_TEMPLATES[tmpl].creed) || '';
    const creed = (strat && strat.creed) || tmplCreed;
    const rcp = window.lzRecipeForStrategy ? window.lzRecipeForStrategy(sid) : { cards: [], research: [], factors: [] };
    const codeNow = code, meta = symbol.meta;
    const runBars = isMin ? (window.lzBars30 ? window.lzBars30(symbol) : []) : symbol.bars;
    if (!runBars.length) return;
    const total = runBars.length;
    let startIdx;
    if (isMin) {
      // 2 周窗口:取末 10 个交易日的 30min bar
      const seen = {}; const days = [];
      for (let i = total - 1; i >= 0; i--) { const d = runBars[i].day; if (!seen[d]) { seen[d] = 1; days.push(d); } if (days.length >= 10) break; }
      const firstDay = days[days.length - 1];
      startIdx = runBars.findIndex(b => b.day >= firstDay);
      if (startIdx < 0) startIdx = 0;
    } else {
      startIdx = Math.min(Math.max(6, cursorRef.current || 0), total - 1);
    }
    // run 化:本次真跑 = 一个 run;逐笔 decide 带 run_id 由后端落盘归组,跑完(含中途停)注册 run 头。
    const runId = window.lzRunId ? window.lzRunId() : ('run_' + Date.now());
    let nBuy = 0, nSell = 0, nWatch = 0, firstDate = null, lastDate = null, lastModel = '';
    realStopRef.current = false;
    setPlaying(false);
    setRealRun({ running: true, done: 0, total: Math.max(0, total - startIdx), cur: startIdx, seatName: seatName, errors: 0 });
    let done = 0, errors = 0;
    for (let idx = startIdx; idx < total; idx++) {
      if (realStopRef.current) break;
      const bar = runBars[idx];
      if (!bar) continue;
      setRealRun(s => Object.assign({}, s, { cur: idx }));
      if (!isMin) { cursorRef.current = idx; setCursor(idx); setMarkerReveal(idx); }   // 30 分钟不动日线游标防视图错乱
      let res = null;
      try {
        res = await window.lzSeatDecide({
          code: codeNow, name: meta.name, date: bar.date,
          seat_cn: seatName, creed: creed, mode: 'fast',
          strategy_id: sid, strategy_name: seatName,
          card: rcp.cards[0] ? { name: rcp.cards[0].name, insight: rcp.cards[0].insight, verdict: rcp.cards[0].verdict, conf: rcp.cards[0].conf, ic: rcp.cards[0].ic } : null,
          cards: rcp.cards, recipe_factors: rcp.factors,
          industry: meta.industry || '',                                // P1:后端按行业/票 PIT 浮出叙事卡(不再前端透传固定 research)
          regime: null,                                                 // ★ 复盘 PIT:不喂今天市况;后端用大盘日产物按 as-of 补(防 look-ahead)
          run_id: runId,                                                // run 化:后端 decide 落盘带 run_id 归组
          freq: isMin ? '30min' : 'day',                                // 30 分钟真跑:date 带时分 datetime + freq
          w: (strat && strat.w) || 0,                                   // P3:因子权重 w(策略级)。0=纯LLM;>0 后端按 (1-w)·LLM分+w·vintage因子z分混入方向
          pa: !!(strat && strat.pa),                                    // 价格行为:本席开关(默认关)。几何后端始终算并回 pa_features;仅 pa 开才把几何+方法论注入 LLM prompt
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
        const rd = { key: 'true_' + sid + '@' + idx, seat: sid, idx: idx, date: bar.date, side: side,
          direction: dir, conf: (res.confidence != null ? res.confidence : null),
          rationale: res.rationale || '', reasoning: res.reasoning || '', asof: res.asof || bar.date, model_name: res.model_name || '' };
        setRealDecs(prev => {
          const arr = (prev[codeNow] || []).filter(x => x.key !== rd.key).concat([rd]);
          return Object.assign({}, prev, { [codeNow]: arr });
        });
      } else { errors++; }
      setRealRun(s => Object.assign({}, s, { done: done, errors: errors }));
    }
    // run 头注册:≥1 笔成功决策才注册(全失败/立刻停 = 无 run);中途停也注册(已跑出的段落同样是一段历史)。
    if (firstDate) {
      try {
        await fetch((window.GUANLAN_BACKEND || '') + '/seats/runs', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ run_id: runId, code: codeNow, name: meta.name, strategy_id: sid, strategy_name: seatName,
            tf: isMin ? '30min' : 'D', start_date: firstDate, end_date: lastDate, n_buy: nBuy, n_sell: nSell, n_watch: nWatch,
            n_err: errors, model: lastModel || '' }),
        });
        setRunsBump(x => x + 1);   // 通知回测历史面板重拉(T4 消费)
      } catch (e) {}
    }
    setRealRun(s => Object.assign({}, s, { running: false, cur: null }));
  };
  // ⑤++ 真·研判落 realDecs(+已开账落台账 decision)。泛化:焦点单股(onLiveDecide)与多股盯盘循环共用。
  //   ts=Date.now() 供节流去重;idx 仅焦点票给末根(供图标记),非焦点票 null(只进舰队不上图)。
  const recordLiveDecide = (codeArg, nameArg, rd) => {
    if (!rd || !rd.direction) return;
    const side = /买/.test(rd.direction) ? 'buy' : (/卖/.test(rd.direction) ? 'sell' : 'watch');
    const key = 'true_' + (rd.seat || '') + '@live';
    const dec = { key: key, seat: rd.seat, idx: (codeArg === code ? n - 1 : null), date: rd.asof, side: side,
      direction: rd.direction, conf: (rd.conf != null ? rd.conf : null), rationale: rd.rationale || '', reasoning: rd.reasoning || '',
      asof: rd.asof, model_name: rd.model_name || '', ts: Date.now() };
    setRealDecs(prev => {
      const arr = (prev[codeArg] || []).filter(x => x.key !== key).concat([dec]);
      return Object.assign({}, prev, { [codeArg]: arr });
    });
    if (ledger && ledger.opened && window.lzLedgerPost) {
      window.lzLedgerPost({ kind: 'decision', date: String(rd.asof || '').slice(0, 10) || new Date().toISOString().slice(0, 10),
        code: codeArg, name: nameArg, direction: rd.direction, confidence: rd.conf == null ? null : +rd.conf,
        decision_id: rd.id || null, source: 'timer' }).then(() => refreshLedger());
    }
  };
  recordRef.current = recordLiveDecide;   // latest-ref:每次渲染刷新,盯盘循环调最新版
  const onLiveDecide = (rd) => recordLiveDecide(code, symbol.meta.name, rd);

  // 模式切换初始化
  useEffect(() => {
    setPlaying(false); thinkingRef.current = false; setThinking(null);
    setCursor(n - 1); setMarkerReveal(n - 1);
    setSelected(null); setOrderTriggers([]);
    setSelRun(null); setRunDecs([]);
  }, [mode, code]);

  // 切席位(当前策略)→ 清选中 run/决策:回测历史从属于席位,跨席残留会让图上标记张冠李戴
  useEffect(() => { setSelRun(null); setSelected(null); }, [curSid]);

  // T4:选中 run → 拉该 run 决策(时间正序),asof 按当前 bars 日期映射 idx;窗外(byDate 无)idx=-1 + offChart 标记。
  // 形状 = 既有 realDec 超集(chart truemarks 消费 {idx,side,direction,conf,rationale,key});_isRun 走 RunDecCard 详情,绝不入 dec.ev.* 旧路径。
  useEffect(() => {
    let dead = false;
    if (!selRun) { setRunDecs([]); return; }
    (async () => {
      const rows = window.lzRunDecisions ? await window.lzRunDecisions(selRun.run_id) : [];
      if (dead) return;
      const isMin = (selRun.tf === '30min');
      const refBars = isMin ? (window.lzBars30 ? window.lzBars30(symbol) : []) : (symbol.bars || []);
      const byKey = {}; refBars.forEach((b, i) => { byKey[b.date] = i; });   // 30分钟 b.date 含时分;日线纯日期
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
                 regime_asof: r.regime_asof || null,   // 当日大盘(PIT 日产物);叙事浮出经 r.research 标题显形,narratives_surfaced(id)审计用、落盘保留不在 UI 重复
                 recipe_factors_vintage: r.recipe_factors_vintage || [],   // P2:配方因子 vintage IC(as-of D·真OOS),后端 decide 落盘、/seats/decisions 回传
                 hybrid_direction: r.hybrid_direction || dir,   // P3:加权混合方向(w>0 按 (1-w)·LLM分+w·因子z分定;w=0 透传 direction → 与 direction 同 → 双线重合)
                 factor_score: (r.factor_score == null ? null : r.factor_score),   // 因子 z 分(无信号 → null)
                 hybrid_bias: (r.hybrid_bias == null ? null : r.hybrid_bias),       // 混合偏置 bias
                 w: r.w || 0,                                                       // 本条决策当时的 w(诚实回看)
                 offChart: byKey[key] == null, _isRun: true };
      }));
    })();
    return () => { dead = true; };
  }, [selRun, symbol]);

  // 真日K接入:当前 code 拉 /seats/daily → 用真 bar 装配并覆盖合成;失败/后端未起则保留合成。
  useEffect(() => {
    if (realSyms[code]) { setDataMode('real'); return; }
    if (!window.lzFetchDailyBars) { setDataMode('mock'); return; }
    let alive = true, tries = 0, timer = null;
    // 真日K拉取失败(后端未起 / 数据未到 / 日历曾损坏)**不再永久卡在合成 2025 样例**:退避重试
    // (3s→15s 封顶,~5min),后端恢复即自动切真,无需手动刷新。修「实盘 K线/信号流停在历史合成日期、
    // 与顶部实时报价(/seats/quote,独立成功)日期不一致」的复发 bug。
    const retry = () => { tries++; if (alive && tries <= 20) timer = setTimeout(attempt, Math.min(3000 * tries, 15000)); };
    const attempt = () => {
      if (!alive) return;
      window.lzFetchDailyBars(code, 250).then(async bars => {
        if (!alive) return;
        if (!bars) { setDataMode('mock'); retry(); return; }   // 诚实回退,不伪造;但安排重试
        // 真沪深300同窗原始行(/seats/benchmark);失败 null → bench 隐藏,基准失败不连坐 K线/合议
        const benchRows = window.lzFetchBenchmark ? await window.lzFetchBenchmark(bars[0].date, bars[bars.length - 1].date) : null;
        const built = window.lzBuildSymbolFromBars(window.LZ_SYMBOLS[code].meta, bars, undefined, benchRows);
        built.benchRows = benchRows;   // useMemo 按策略重建时透传同一原始行
        setRealSyms(s => Object.assign({}, s, { [code]: built }));
        setDataMode('real');
      }).catch(() => { if (alive) { setDataMode('mock'); retry(); } });
    };
    attempt();
    return () => { alive = false; if (timer) clearTimeout(timer); };
  }, [code]);

  // ④-③ 官方日K落库自动接管:live 下每 10min(+ mount 即查一次)有缓存才重拉 /seats/daily;
  //   经 normDailyBars(已丢 null 占位行)后,末根真日K日 ≥ 缓存.date → 官方真 OHLC 已到 → 用官方 bar 覆盖 realSyms + 清缓存。
  //   判据只认丢 null 后的真 bar,绝不信 quote.lastBarDate(占位行会让它提前变今日 → 误清缓存退回 6-8)。
  //   deps 不含 liveBar:否则 6s 捕获不断重建定时器 → 10min 永不到点;缓存从 localStorage 现读。
  useEffect(() => {
    if (mode !== 'live' || !window.lzFetchDailyBars) return;
    let alive = true;
    const poll = () => {
      const lb = window.lzLivebarLoad ? window.lzLivebarLoad(code) : null;
      if (!lb) return;                                   // 无待结算缓存 → 不必拉
      window.lzFetchDailyBars(code, 250).then(async bars => {
        if (!alive || !bars || !bars.length) return;
        const lastReal = bars[bars.length - 1].date;
        if (lastReal >= lb.date) {                       // 官方真 bar 已到 → 硬替换 + 清缓存
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
    const iv = setInterval(poll, 600000);                // 10min
    return () => { alive = false; clearInterval(iv); };
  }, [mode, code]);

  // bar 数变化(切标的 / 真日K到位覆盖合成)→ 复位回放游标到末尾(看完整回测)。
  useEffect(() => { setPlaying(false); setCursor(n - 1); setMarkerReveal(n - 1); }, [n]);

  // 切 TF / 标的 / 模式 → 导航条复位为「适配全部」。
  useEffect(() => { setZoom(null); setPanEnd(null); }, [tf, code, mode]);

  // 日内 TF 时懒加载真 5min,挂到当前真 symbol(供 frameData 聚合 60/30/15);失败保留合成。
  //   选中 30min run 时也加载:30min run 的净值(repPerf)依赖 bars30=bars5 聚合,否则日线视图选了 run 却显「未选回测」(净值缺)。
  useEffect(() => {
    const need = (tf === '60' || tf === '30' || tf === '15' || tf === '5') || !!(selRun && selRun.tf === '30min');
    if (!need) return;
    const sym = realSyms[code];
    if (!sym || sym.bars5 || !window.lzFetchBars5) return;     // 仅真 symbol、未拉过
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
  }, [tf, code, realSyms, selRun]);

  // ④-② 实盘日内:live + 日内 TF 时轮询 /watch/bars 真今日 5min,拼到历史 5min 末尾(去重),供日内图显今日盘中。
  //   仅在历史 5min 已到位后增量拼接;休市/已含 → 无新 → 不动(诚实)。30s 轮询,切走清理。
  useEffect(() => {
    if (mode !== 'live') return;
    if (tf !== '60' && tf !== '30' && tf !== '15' && tf !== '5') return;
    if (!window.lzFetchRealtimeBars5) return;
    let alive = true;
    const pull = () => window.lzFetchRealtimeBars5(code).then(live5 => {
      if (!alive || !live5 || !live5.length) return;
      setRealSyms(s => {
        const cur = s[code];
        if (!cur || !cur.bars5 || !cur.bars5.length) return s;   // 等历史 5min 先到再拼
        const seen = new Set(cur.bars5.map(b => b.date));
        const extra = live5.filter(b => !seen.has(b.date));
        if (!extra.length) return s;                             // 无新(休市/已含)→ 不改
        return Object.assign({}, s, { [code]: Object.assign({}, cur, { bars5: cur.bars5.concat(extra) }) });
      });
    });
    pull();
    const iv = setInterval(pull, 30000);
    return () => { alive = false; clearInterval(iv); };
    // deps 不含 realSyms:轮询读最新状态走 setRealSyms 函数式更新(s[code]),避免每次拼接后重建 30s 定时器→请求风暴。
  }, [mode, tf, code]);

  // ②+ 实盘 1min:live + 1分 TF 时轮询 /seats/bars_live?freq=1min(引擎 pytdx 1min),整批替换 bars1(1min 无历史,不拼接)。20s 轮询,切走清理。
  useEffect(() => {
    if (mode !== 'live' || tf !== '1' || !window.lzFetchRealtimeBars1) return;
    let alive = true;
    const pull = () => window.lzFetchRealtimeBars1(code, 480).then(b1 => {
      if (!alive || !b1 || !b1.length) return;
      setRealSyms(s => { const cur = s[code]; if (!cur) return s; return Object.assign({}, s, { [code]: Object.assign({}, cur, { bars1: b1 }) }); });
    });
    pull();
    const iv = setInterval(pull, 20000);
    return () => { alive = false; clearInterval(iv); };
  }, [mode, tf, code]);

  // 回放 (思考期暂停推进,实现“思考需时间 + 无未来信息”)
  useEffect(() => {
    if (!playing) return;
    const base = Math.round(620 / speed);
    const iv = setInterval(() => {
      if (thinkingRef.current) return;            // 思考中:暂停推进
      const c = cursorRef.current;
      if (c >= n - 1) { setPlaying(false); return; }
      const next = c + 1;
      cursorRef.current = next; setCursor(next);
      const dd = (mode === 'backtest') ? symbol.decisions.filter(d => active.includes(d.seat) && d.idx === next) : [];
      if (dd.length) {
        thinkingRef.current = true;
        setThinking({ bar: next, seats: [...new Set(dd.map(d => d.seat))], decs: dd });
        const thinkMs = Math.max(620, Math.round(1150 / Math.sqrt(speed)));
        setTimeout(() => { thinkingRef.current = false; setThinking(null); setMarkerReveal(next); }, thinkMs);
      } else {
        setMarkerReveal(next);
      }
    }, base);
    return () => clearInterval(iv);
  }, [playing, speed, n, mode, active, symbol]);

  // 实盘自动选中最新落子
  useEffect(() => {
    if (mode !== 'live' || !playing) return;
    const fresh = symbol.decisions.filter(d => active.includes(d.seat) && d.idx === cursor);
    if (fresh.length) { setSelected(fresh[fresh.length - 1]); setMarkerReveal(cursor); }
  }, [cursor, mode, playing]);

  const replay = () => {
    setSelected(null); setThinking(null); thinkingRef.current = false;
    const start = mode === 'live' ? Math.max(0, n - 42) : 0;
    cursorRef.current = start; setCursor(start); setMarkerReveal(mode === 'live' ? start : -1);
    setTimeout(() => setPlaying(true), 60);
  };
  const scrubTo = (v) => { setPlaying(false); thinkingRef.current = false; setThinking(null); cursorRef.current = v; setCursor(v); setMarkerReveal(v); };
  // 复盘回灌:把当前策略的复盘成绩提炼为新经验卡,写入共享档案库(T5:单 agent 口径,本次复盘 = 当前策略经验)
  const distillToCard = () => {
    if (!window.GL || !repPerf) return;       // 只提炼真实 run 回测,无 run 不提炼假数据
    const m = repPerf.metrics;
    const name = symbol.meta.name + ' · ' + curName + '复盘提炼';
    const id = GL.put({ type: 'card', title: name, cat: '实测', tags: ['复盘回灌', symbol.meta.industry],
      verdict: m.total > 0 ? '通过' : '存疑', conf: Math.max(40, Math.min(90, Math.round(52 + m.sharpe * 7))),
      // 删伪 IC(原 0.02+sharpe*0.008 是把夏普换算的好看数,非真截面/时序 IC)→ 复盘卡是草稿,要进信号须走验证区真验证
      insight: `${symbol.meta.name} 复盘(${frame.label}):${curName}累计 ${pct(m.total)}、Sharpe ${m.sharpe.toFixed(2)}、胜率 ${(m.winRate * 100).toFixed(0)}%。【复盘草稿·未经验证,不入信号】`,
      expr: '(实测落子序列)', status: 'draft', refs: [] });
    setToast({ id, name });
    setTimeout(() => setToast(null), 5200);
  };

  const reviewing = mode === 'backtest' && !playing && cursor >= n - 1;
  const frame = window.lzFrameData(symbol, tf, cursor, markerReveal, reviewing, mode === 'live');
  // 尺度真伪标识:日/周看 dataMode,日内看 frame.real5(真5min 聚合 vs 合成回退)。
  const intraday = tf === '60' || tf === '30' || tf === '15' || tf === '5' || tf === '1';
  const scaleReal = intraday ? !!frame.real5 : (dataMode === 'real');
  const scaleLabel = intraday
    ? (frame.real5 ? (tf === '1' ? '真·1min' : '真·5min') : (dataMode === 'real' ? '日内·合成' : '样例'))
    : (dataMode === 'real' ? '真·日线' : '样例');
  const scaleTitle = intraday
    ? (frame.real5 ? (tf === '1' ? '盘中实时 1min(/seats/bars_live?freq=1min,引擎 pytdx)' : '日内来自 stock_data 真 5min 聚合(/seats/daily?freq=5min)') : '真 5min/1min 未到 / 窗口越界 → subdivideDay 合成,仅形态示意')
    : (dataMode === 'real' ? '日K来自 stock_data 真数据(/seats/daily)' : '真日K未到 / 后端未起 → 回退合成样例');
  // ④-① 实盘今日日K(报价日 > 末根真日K日就补今日一根:**盘中=forming 实时跳动 / 盘后=今日已收盘快照**;
  //   数据全来自实时报价 quote(开/高/低/现价),标 today,**只入显示帧 dispFrame,绝不入 symbol.bars/scan/合议**;
  //   今晚 daily 入库后,末根真日K日==报价日 → 下方 dedup 命中 → 不再补,真 bar 接管。
  //   不再卡 quote.fresh:后端因 daily 留了今日 null 占位行而误判 fresh=false,会漏掉「实盘看不到今日」。)
  const dispFrame = (() => {
    if (mode !== 'live' || tf !== 'D') return frame;
    const last = symbol.bars[n - 1];
    if (!last) return frame;
    // ① 实时报价(最新,优先):报价日「晚于」末根真日K → forming 实时柱(盘中跳动 / 盘后收盘快照);捕获 effect 已同步写缓存
    if (quote && Number.isFinite(+quote.price) && quote.asofDate && quote.asofDate > last.date) {
      const fo = +(quote.open != null ? quote.open : quote.price), fc = +quote.price;   // 强制数值:防异源字符串/NaN 污染价标/tooltip
      const fb = { i: frame.fbars.length, date: quote.asofDate, o: fo, c: fc,
        h: Math.max(quote.high != null ? +quote.high : -Infinity, fo, fc),   // h/l 夹住 o/c:防过期 high<现价 截顶
        l: Math.min(quote.low != null ? +quote.low : Infinity, fo, fc),
        v: +(quote.volume != null ? quote.volume : 0), forming: !!quote.fresh, today: true, event: false };
      return Object.assign({}, frame, { fbars: frame.fbars.concat([fb]), viewEnd: frame.viewEnd + 1 });
    }
    // ② 缓存今日柱(实时源·待官方结算):报价撤了/退回(收盘后实时源退回上一结算日)但缓存仍晚于末根真日K → 渲染缓存柱,图永远有今天
    //   防幻影:缓存距今超 14 自然日仍未被官方替换(停牌/退市)→ 不渲染。官方日K入库后两条件皆 false,真 bar 接管(接管轮询清缓存)。
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
  const formingShown = dispFrame !== frame;
  // 落子标记按**时间戳**重映射到当前显示帧(dispFrame.fbars):决策预存 idx 属 bars30/日线坐标系,切 TF 必错位/消失
  //   → 按 asof 容纳定位,30min 买卖点在日线视图落到当日 K(满足「切日线也显示」)、在 30 分视图精确落到该 bar。
  //   源:选中 run → runDecs(原 idx 保留供 runPerf 模拟成交,互不干扰);否则 realDecs[code](复盘进行中真跑 / 实盘定时研判)。
  const chartMarks = (() => {
    const src = selRun ? runDecs : (realDecs[code] || []);
    return window.lzMapDecsToFrame ? window.lzMapDecsToFrame(src, dispFrame.fbars) : [];
  })();
  // 实盘右端是否为「今日实时」(forming 日K 或 已拼今日 5min)→ 抑制 as-of 墙(否则标「末日K日」与今日矛盾)
  const liveTodayEdge = formingShown || (mode === 'live' && frame.real5 && frame.fbars.length > 0
    && symbol.bars[n - 1] && frame.fbars[frame.fbars.length - 1].day > symbol.bars[n - 1].date);
  // 可见窗口:导航条 zoom(尺度)+ panEnd(滑动)驱动;未设时 复盘=全程、实盘=近端。不越过已揭示末尾(PIT)。
  const maxEnd = dispFrame.viewEnd;
  const autoWin = mode === 'live' ? (tf === 'D' ? 56 : tf === 'W' ? 22 : 60) : (maxEnd + 1);
  const vWin = Math.max(10, Math.min(zoom != null ? zoom : autoWin, maxEnd + 1));
  const vEnd = Math.max(vWin - 1, Math.min(panEnd != null ? panEnd : maxEnd, maxEnd));
  const chartView = { start: Math.max(0, vEnd - vWin + 1), end: vEnd };
  const pitOn = mode === 'live' || (mode === 'backtest' && (playing || cursor < n - 1));
  const asOfDate = (symbol.bars[cursor] || symbol.bars[n - 1]).date;
  // 新闻标记:回测态按 as-of 拉 PIT 流(后端只回 ≤as-of,前端图层再按 revealTo 拦),实时态拉 live。debounce 250ms。
  useEffect(() => {
    if (!code) { setNewsPayload(null); return; }
    let alive = true;
    const t = setTimeout(() => {
      const nmode = mode === 'live' ? 'live' : 'pit';
      const asof = mode === 'live' ? '' : asOfDate;
      window.lzFetchNews && window.lzFetchNews(code, asof, nmode).then(p => { if (alive) setNewsPayload(p); });
    }, 250);
    return () => { alive = false; clearTimeout(t); };
  }, [code, asOfDate, mode]);
  const newsMarkers = useMemo(
    () => (window.lzMapNewsToFrame && newsPayload)
      ? window.lzMapNewsToFrame(newsPayload.items || [], dispFrame.fbars, newsKw) : [],
    [newsPayload, dispFrame, newsKw]);

  const pnlActive = active.filter(s => symbol.perSeat[s]);
  // 基准末个**非 null** 值(alignBench 尾段在指数源末日后为 null,直读 [n-1] 会把「源滞后」
  // 误降级成「真指数未连接」——两种降级语义必须可区分,截至日标注在 MetricsStrip 内做)
  let benchTotal = null;
  if (symbol.bench) {
    for (let bi = Math.min(n, symbol.bench.length) - 1; bi >= 0; bi--) {
      if (symbol.bench[bi] != null) { benchTotal = symbol.bench[bi] - 1; break; }
    }
  }
  // 复盘净值线:**只画选中 run 的真实回测净值**(repPerf.eq);未选 run → 无净值线(诚实空),实盘走 ledger 不在此。
  //   30min run 的 eq 是 30 分粒度(长度=bars30),日线的 len=n/revealTo=cursor 会把它截成前段假平线 → 用 eq 自身长度与全揭示;
  //   且日线基准(symbol.bench,长度=n)无法与 30 分 x 轴同轴对齐 → 隐藏基准(诚实,不画错位线),只画策略净值。
  const eqMin = mode === 'backtest' && selRun && selRun.tf === '30min' && repPerf;
  const eqLen = eqMin ? repPerf.eq.length : n;
  const eqReveal = eqMin ? repPerf.eq.length - 1 : cursor;
  const equityLines = [
    ...(repPerf ? [{ eq: repPerf.eq, color: 'var(--yin)', width: 2, fill: true, name: curName + ' · 纯LLM' }] : []),
    // P3 混合净值线:仅当有决策开启 w>0 才叠加(全 w=0 时与纯LLM线逐位重合,画了也只是冗余 → 改注小字)。异色虚线区分。
    ...((repPerfHybrid && anyHybrid) ? [{ eq: repPerfHybrid.eq, color: 'var(--zhu)', width: 1.6, dash: '5 3', name: '混合(因子进信号)' }] : []),
    // bench=null(真指数未连接/样例)或 30min run(粒度不同无法同轴)→ 不画基准虚线,诚实降级;绝不回退合成假基准
    ...((symbol.bench && !eqMin) ? [{ eq: symbol.bench, color: 'var(--ink-3)', width: 1.2, dash: '4 3', dim: true, name: '基准' }] : []),
  ];
  // P3 归因:Δtotal =(混合.total − 纯LLM.total),正=因子进信号增厚收益。两套指标各取 metrics(metricsOf 已各算一遍)。
  const hybridDelta = (repPerf && repPerf.metrics && repPerfHybrid && repPerfHybrid.metrics)
    ? (repPerfHybrid.metrics.total - repPerf.metrics.total) : null;

  return (
    <div className="paper-bg" style={{ width: '100%', height: '100vh', display: 'flex', flexDirection: 'column', overflow: 'hidden', fontFamily: 'var(--sans)', color: 'var(--ink)' }}>
      <TopBar {...{ mode, setMode, view, setView, workspace, setWorkspace, code, setCode, symbol, cursor, showTweaks, setShowTweaks }} />
      {workspace === 'foundry' ? (
        <Foundry />
      ) : (
      <React.Fragment>
      <MarketBar symbol={symbol} revealTo={cursor} mode={mode === 'live' ? 'live' : 'backtest'} market={market} quote={mode === 'live' ? quote : null} />
      {view === 'single' ? (
        <>
          <MetricsStrip m={mode === 'live' ? curPerf.metrics : (repPerf ? repPerf.metrics : null)} benchTotal={benchTotal} label={(mode === 'live' ? '实盘 · ' : '回测 · ') + curName} symbol={symbol} rt={cursor} mode={mode} quote={mode === 'live' ? quote : null} ledger={mode === 'live' ? ledger : null} shadowM={shadowM} portfolioM={portfolioM} onStartTracking={startTracking} />
          <div style={{ flex: 1, display: 'flex', minHeight: 0, overflow: 'hidden' }}>
            <SeatRail strategy={curStrat} strategies={strategies} onPick={setCurStratId} ps={repPerf ? { eq: repPerf.eq, metrics: repPerf.metrics } : null} rt={cursor} />
            {/* 中栏: K线 + 收益曲线 */}
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
                {mode === 'backtest' && repPerf && <span onClick={distillToCard} className="mono" title="复盘回灌 · 提炼为新经验卡入共享库" style={{ fontSize: 10, color: 'var(--yin)', border: '1px solid var(--zhu-soft)', borderRadius: 6, padding: '3px 9px', cursor: 'pointer' }}>↺ 提炼为经验卡</span>}
                <span className="mono" style={{ fontSize: 9.5, color: 'var(--ink-3)' }}>朱砂涨 · 黛绿跌 · B 买 · S 卖 · ◆ 险(预警发光)</span>
              </div>
              <div style={{ flex: 1, minHeight: 0, position: 'relative' }}>
                <CandleChart bars={dispFrame.fbars} decisions={[]} truedecs={chartMarks} activeSeats={selRun ? Array.from(new Set(runDecs.map(d => d.seat))) : active} selected={selected} onSelect={setSelected} revealTo={dispFrame.freveal} view={chartView} live={pitOn} asOf={{ on: pitOn && !liveTodayEdge, date: asOfDate }} triggers={orderTriggers} newsMarkers={newsMarkers} onNewsClick={setNewsPanel} />
                <Deliberation thinking={thinking} symbol={symbol} />
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
              <div style={{ height: 150, borderTop: '1px solid var(--line)', flexShrink: 0, position: 'relative' }}>
                <div style={{ position: 'absolute', top: 5, left: 10, zIndex: 2, display: 'flex', gap: 12, fontFamily: 'var(--mono)', fontSize: 9.5, color: 'var(--ink-3)' }}>
                  <span className="serif" style={{ fontSize: 11, color: 'var(--ink-2)', fontWeight: 600 }}>收益曲线</span>
                  {repPerf && <span><span style={{ color: 'var(--yin)' }}>━</span> {curName} · 纯LLM(按 run 真决策模拟成交)</span>}
                  {/* P3 混合线图例 + 归因:有 w>0 决策才画混合线;Δtotal=(混合−纯LLM) */}
                  {(repPerf && repPerfHybrid && anyHybrid) && <span><span style={{ color: 'var(--zhu)' }}>┅</span> 混合(因子进信号){hybridDelta != null ? ' · Δtotal=' + (hybridDelta >= 0 ? '+' : '') + (hybridDelta * 100).toFixed(2) + '%' : ''}</span>}
                  {(repPerf && repPerfHybrid && !anyHybrid) && <span style={{ color: 'var(--ink-3)' }}>w=0 · 两线重合(未混入因子)</span>}
                  {(mode === 'backtest' && !repPerf) && <span style={{ color: 'var(--ink-3)' }}>未选回测 —— 右栏点开一次 run 看真实净值</span>}
                  {(mode === 'live') && <span style={{ color: 'var(--ink-3)' }}>实盘净值见右栏「仓位台账」</span>}
                  {(symbol.bench && !eqMin) && <span><span style={{ color: 'var(--ink-3)' }}>┄</span> 基准</span>}
                  {eqMin && <span style={{ color: 'var(--ink-3)' }}>30 分粒度 · 基准另案对齐</span>}
                </div>
                <EquityChart lines={equityLines} revealTo={eqReveal} len={eqLen} />
              </div>
            </div>
            {/* 右栏(复盘): 席位 → 回测历史 → 内嵌决策流水(从属层级,选中即图上换标记)
                右栏(实盘): 台账 + 信号队列 */}
            <div style={{ width: 372, flexShrink: 0, display: 'flex', flexDirection: 'column', minHeight: 0, overflowY: 'auto', overflowX: 'hidden', background: 'var(--paper)' }}>
              {mode !== 'live' && <RunPicker code={code} bump={runsBump} selRun={selRun} strategyId={curSid}
                strategyName={curName} runDecs={runDecs} selected={selected} onPickDec={setSelected}
                onSelect={(r) => { setSelRun(p => p && r && p.run_id === r.run_id ? null : r); setSelected(null); }} />}
              {mode === 'live' && <LedgerPanel ledger={ledger} onRefresh={refreshLedger} code={code} />}
              {mode === 'live' && <div style={{ height: 232, flexShrink: 0, borderBottom: '1px solid var(--line)', display: 'flex', flexDirection: 'column', minHeight: 0 }}>
                <LiveDecideFlow decs={realDecs[code]} openDate={(ledger && ledger.start_date) || null} />
              </div>}
              <OrderWatchPanel code={symbol.meta.code} name={symbol.meta.name} mode={mode === 'live' ? 'live' : 'backtest'} asOf={mode !== 'live' ? asOfDate : null} fresh={mode === 'live' && !!(quote && quote.fresh)} seatId={curSid} onRealDecide={onLiveDecide} onTrigger={(t) => {
  setOrderTriggers(ts => [...ts.filter(x => x.id !== t.id), t]);
  if (mode === 'live' && window.lzShadowAddEntry) setShadow(sh => { const ns = window.lzShadowAddEntry(sh, t); if (ns !== sh && window.lzShadowSave) window.lzShadowSave(code, ns); return ns; });
  // T6 台账买入:实盘已开账 + 买向触发 → 真落一笔 trade(可用现金 20% 取整百股;不足一手诚实跳过不入账)。
  //   影子写入保留(过渡期双轨,台账为准、影子只读展示待退役);复盘验触发(mode=backtest)绝不写真账。
  // 注:此处 code 为当前聚焦股(OrderWatchPanel 的 code prop = app code state;切票会重置该面板,旧 interval 经 cleanup 失活),故 monitored 闸门按聚焦股判定是安全的。
  if (mode === 'live' && ledger && ledger.opened && window.lzLedgerPost && /买/.test(t.side || '') && window.lzPoolIsMonitored && window.lzPoolIsMonitored(code)) {
    const today = new Date().toISOString().slice(0, 10);
    const avail = ledger.cash || 0; const px = +t.fill;
    const qty = px > 0 ? Math.floor((avail * 0.2) / px / 100) * 100 : 0;
    if (qty >= 100) window.lzLedgerPost({ kind: 'trade', date: String(t.at || '').slice(0, 10) || today, code, name: symbol.meta.name, side: 'buy', price: px, qty, reason: t.seat ? ('条件单·' + t.seat) : '条件单', source: 'order' }).then(() => refreshLedger());
    else console.warn('[ledger] 现金不足一手,跳过入账', { avail, px });
  }
}}
  positions={mode === 'live' ? shadow.positions : []}
  strategies={strategies}
  onClosePosition={(posId, price) => { if (!window.lzShadowClose) return; setShadow(sh => { const r = window.lzShadowClose(sh, posId, price, (quote && quote.asofDate) || null, '研判平'); if (r.changed && window.lzShadowSave) window.lzShadowSave(code, r.shadow); return r.changed ? r.shadow : sh; }); }}
/>
              {mode === 'live' && <OrderbookTicksPanel book={book} ticks={ticks} />}
              <div style={{ flexShrink: 0, minHeight: 320 }}>
                {selected && selected._isRun
                  ? <RunDecCard dec={selected} />
                  : <DecisionCard dec={selected} symbol={symbol} mode={mode === 'live' ? 'live' : 'backtest'} />}
              </div>
            </div>
          </div>
          <PlaybackBar {...{ mode, cursor, scrubTo, n, symbol, playing, setPlaying, speed, setSpeed, replay, thinking, realRun, onRealRun: runRealThink }} />
        </>
      ) : (
        <div style={{ flex: 1, minHeight: 0, display: 'flex' }}>
          <div style={{ flex: 1, minHeight: 0, display: 'flex', flexDirection: 'column' }}>
            <FleetGrid active={active} activeCode={code} onPick={(c) => { setCode(c); setView('single'); }} />
          </div>
          <FleetSignalList realDecs={realDecs} monQuotes={monQuotes} activeCode={code}
            watchOn={fleetWatch} onToggleWatch={() => setFleetWatch(v => !v)}
            onPick={(c) => { setCode(c); setView('single'); }} />
        </div>
      )}
      </React.Fragment>
      )}
      {showTweaks && <TweaksPanel {...{ dark, setDark, speed, setSpeed, curSid, setCurStratId, setShowTweaks, seats: stratIds.map(id => window.lzStrategyGet(id)).filter(Boolean) }} />}
      {toast && (
        <div style={{ position: 'fixed', bottom: 70, left: '50%', transform: 'translateX(-50%)', zIndex: 60, display: 'flex', alignItems: 'center', gap: 11, background: 'var(--paper)', border: '1px solid var(--dai-soft)', borderRadius: 11, padding: '11px 16px', boxShadow: '0 6px 26px rgba(28,24,20,0.18)', animation: 'fadeIn .3s ease' }}>
          <span className="seal" style={{ width: 22, height: 22, fontSize: 12, background: 'var(--dai)' }}>瀾</span>
          <span className="serif" style={{ fontSize: 12.5, color: 'var(--ink-1)' }}>{toast.kind === 'handoff' ? toast.text : <React.Fragment>复盘已提炼为草稿经验卡「{toast.name}」· 入共享档案库</React.Fragment>}</span>
          {toast.kind !== 'handoff' && <a href="../graph/观澜 · 研究图谱.html" className="mono" style={{ fontSize: 10.5, color: 'var(--yin)', textDecoration: 'none', borderBottom: '1px dashed var(--zhu-soft)' }}>看图谱 →</a>}
        </div>
      )}
    </div>
  );
}

// ───────── 顶栏 ─────────
function TopBar({ mode, setMode, view, setView, workspace, setWorkspace, code, setCode, symbol, showTweaks, setShowTweaks }) {
  const Seg = ({ val, set, opts, muted }) => (
    <div style={{ display: 'flex', border: '1px solid var(--line)', borderRadius: 8, overflow: 'hidden', opacity: muted ? 0.45 : 1 }}>
      {opts.map(([k, label]) => (
        <span key={k} onClick={() => set(k)} className="serif" style={{
          fontSize: 12, padding: '5px 13px', cursor: 'pointer',
          background: val === k ? 'var(--ink)' : 'transparent', color: val === k ? 'var(--paper)' : 'var(--ink-2)',
        }}>{label}</span>
      ))}
    </div>
  );
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 13, padding: '0 16px', height: 52, borderBottom: '1px solid var(--line)', background: 'rgba(241,234,217,0.6)', flexShrink: 0 }}>
      {!WW_EMBED && (<React.Fragment>
      <span className="serif" style={{ fontSize: 15, fontWeight: 600, letterSpacing: '.04em', whiteSpace: 'nowrap', flexShrink: 0 }}>落子</span>
      <span className="mono" style={{ fontSize: 9, color: 'var(--ink-3)', border: '1px solid var(--line)', borderRadius: 5, padding: '2px 6px', whiteSpace: 'nowrap', flexShrink: 0 }}>交易决策 agent</span>
      <span style={{ color: 'var(--line)' }}>|</span>
      </React.Fragment>)}
      <Seg val={workspace} set={setWorkspace} opts={[['desk', '盯盘'], ['foundry', '校场']]} />
      {workspace === 'desk' && (
        <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
          <Seg val={mode} set={setMode} opts={[['backtest', '复盘'], ['live', '实盘']]} muted={view === 'fleet'} />
          {view === 'fleet' && <span className="mono" title="舰队是多股总览;复盘/实盘只对单股生效。此处仅预设你点开某只股票后进入的模式,不会即时改变舰队。" style={{ fontSize: 8.5, color: 'var(--ink-3)', whiteSpace: 'nowrap' }}>· 点开后生效</span>}
        </div>
      )}
      {workspace === 'desk' && <Seg val={view} set={setView} opts={[['single', '单标'], ['fleet', '舰队']]} />}
      <div style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: 10 }}>
        <select value={code} onChange={e => setCode(e.target.value)} className="mono" style={{ fontSize: 11.5, padding: '5px 9px', border: '1px solid var(--line)', borderRadius: 7, background: 'var(--paper)', color: 'var(--ink-1)', cursor: 'pointer' }}>
          {window.LZ_SYMBOL_META.map(m => <option key={m.code} value={m.code}>{m.name} {m.code}{m.dynamic ? ' ·池' : ''}</option>)}
        </select>
        {window.lzPoolIsDynamic && window.lzPoolIsDynamic(code) && (
          <span onClick={() => { if (window.lzPoolRemove && window.lzPoolRemove(code)) setCode(window.LZ_PRIMARY); }}
            className="mono" title="把该票移出盯盘池(选股篮子/手动加入的动态票;固定 6 只底座不可移)"
            style={{ fontSize: 9.5, padding: '3px 8px', border: '1px solid var(--line)', borderRadius: 7, color: 'var(--ink-3)', cursor: 'pointer', whiteSpace: 'nowrap' }}>移出盯盘池 ×</span>
        )}
        <span onClick={() => setShowTweaks(s => !s)} className="mono" style={{ fontSize: 11, padding: '5px 11px', border: '1px solid ' + (showTweaks ? 'var(--yin)' : 'var(--line)'), borderRadius: 7, color: showTweaks ? 'var(--yin)' : 'var(--ink-2)', cursor: 'pointer' }}>⚙ Tweaks</span>
      </div>
    </div>
  );
}

// ───────── 回放控制条 ─────────
function PlaybackBar({ mode, cursor, scrubTo, n, symbol, playing, setPlaying, speed, setSpeed, replay, thinking, realRun, onRealRun }) {
  const b = symbol.bars[cursor] || symbol.bars[n - 1];
  const atEnd = cursor >= n - 1;
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 14, padding: '0 16px', height: 52, borderTop: '1px solid var(--line)', background: 'var(--paper)', flexShrink: 0 }}>
      <span onClick={() => atEnd && !playing ? replay() : setPlaying(p => !p)} style={{
        width: 32, height: 32, borderRadius: '50%', background: 'var(--yin)', color: 'var(--paper)', cursor: 'pointer',
        display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 13, flexShrink: 0,
      }}>{playing ? '❚❚' : atEnd ? '↻' : '▶'}</span>
      <span className="serif" style={{ fontSize: 12, color: 'var(--ink-2)', whiteSpace: 'nowrap' }}>
        {mode === 'live' ? '实时回放' : '逐 bar 推演'}
      </span>
      <span className="mono" style={{ fontSize: 10, color: 'var(--ink-3)', display: 'flex', gap: 4, alignItems: 'center' }}>
        {thinking
          ? <span style={{ color: 'var(--yin)', display: 'flex', alignItems: 'center', gap: 5, whiteSpace: 'nowrap' }}><span style={{ width: 6, height: 6, borderRadius: '50%', background: 'var(--yin)', animation: 'pulse 1s ease-in-out infinite' }} />启发式扫描中…</span>
          : [0.5, 1, 2, 4].map(s => (
            <span key={s} onClick={() => setSpeed(s)} style={{ cursor: 'pointer', padding: '2px 7px', borderRadius: 5, border: '1px solid ' + (speed === s ? 'var(--ink)' : 'var(--line)'), color: speed === s ? 'var(--ink)' : 'var(--ink-3)' }}>{s}×</span>
          ))}
      </span>
      {mode === 'backtest' && (
        <span onClick={() => onRealRun && onRealRun()} title="让 agent 从游标往前走,每根日K真调 LLM 研判(PIT·只用≤当日信息);走到哪算到哪,可随时停" style={{ display: 'flex', alignItems: 'center', gap: 6, cursor: 'pointer', whiteSpace: 'nowrap', fontSize: 10.5, padding: '4px 11px', borderRadius: 7, fontFamily: 'var(--serif)', flexShrink: 0, border: '1px solid ' + ((realRun && realRun.running) ? 'var(--dai)' : 'var(--zhu-soft)'), color: (realRun && realRun.running) ? 'var(--dai)' : 'var(--yin)', background: (realRun && realRun.running) ? 'rgba(74,107,92,0.07)' : 'rgba(168,57,45,0.05)' }}>
          {(realRun && realRun.running)
            ? <React.Fragment><span style={{ width: 6, height: 6, borderRadius: '50%', background: 'var(--dai)', animation: 'pulse 1s ease-in-out infinite' }} />停止真跑 · {realRun.done}/{realRun.total}{realRun.errors ? ' · ' + realRun.errors + '失败' : ''}</React.Fragment>
            : '✦ 让 agent 真跑'}
        </span>
      )}
      <input type="range" min={0} max={n - 1} value={cursor}
        onChange={e => scrubTo(+e.target.value)}
        style={{ flex: 1, accentColor: 'var(--yin)', cursor: 'pointer' }} />
      <span className="mono" style={{ fontSize: 11, color: 'var(--ink-1)', whiteSpace: 'nowrap', minWidth: 168, textAlign: 'right' }}>
        {b.date} · bar {cursor + 1}/{n}
      </span>
    </div>
  );
}

// ───────── Tweaks 面板 ─────────
function TweaksPanel({ dark, setDark, speed, setSpeed, curSid, setCurStratId, setShowTweaks, seats }) {
  const Row = ({ label, children }) => (
    <div style={{ marginBottom: 16 }}>
      <div className="mono" style={{ fontSize: 9.5, color: 'var(--ink-3)', letterSpacing: '.1em', marginBottom: 7 }}>{label}</div>
      {children}
    </div>
  );
  const Chip = ({ on, onClick, color, children }) => (
    <span onClick={onClick} className="mono" style={{ fontSize: 11, padding: '5px 11px', borderRadius: 7, cursor: 'pointer', border: '1px solid ' + (on ? (color || 'var(--ink)') : 'var(--line)'), background: on ? (color || 'var(--ink)') : 'transparent', color: on ? 'var(--paper)' : 'var(--ink-2)' }}>{children}</span>
  );
  return (
    <div style={{ position: 'absolute', top: 96, right: 16, width: 248, background: 'var(--paper)', border: '1px solid var(--line)', borderRadius: 13, boxShadow: '0 10px 40px rgba(28,24,20,0.18)', padding: '15px 17px', zIndex: 50, animation: 'fadeIn .25s ease' }}>
      <div style={{ display: 'flex', alignItems: 'center', marginBottom: 14 }}>
        <span className="serif" style={{ fontSize: 13.5, fontWeight: 600 }}>Tweaks</span>
        <span style={{ flex: 1 }} />
        <span onClick={() => setShowTweaks(false)} style={{ cursor: 'pointer', color: 'var(--ink-3)', fontSize: 15 }}>×</span>
      </div>
      <Row label="主题">
        <div style={{ display: 'flex', gap: 7 }}>
          <Chip on={!dark} onClick={() => setDark(false)}>宣纸</Chip>
          <Chip on={dark} onClick={() => setDark(true)}>月夜</Chip>
        </div>
      </Row>
      <Row label="回放速度">
        <div style={{ display: 'flex', gap: 7 }}>
          {[0.5, 1, 2, 4].map(s => <Chip key={s} on={speed === s} onClick={() => setSpeed(s)}>{s}×</Chip>)}
        </div>
      </Row>
      <Row label="当前策略">
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 7 }}>
          {(seats || []).map(s => (
            <Chip key={s.id} on={curSid === s.id} onClick={() => setCurStratId(s.id)} color={window.lzStrategyColor(s.id)}>{s.name}</Chip>
          ))}
        </div>
      </Row>
      <div className="mono" style={{ fontSize: 8.5, color: 'var(--ink-3)', lineHeight: 1.6, marginTop: 4 }}>单 agent 模式:每次只看一个策略,切换即重算收益曲线与指标。</div>
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

// ───────── Agent 思考浮层 ─────────
function DotDot() {
  const [k, setK] = useState(1);
  useEffect(() => { const t = setInterval(() => setK(x => x % 3 + 1), 360); return () => clearInterval(t); }, []);
  return <span>{'·'.repeat(k)}</span>;
}
function Deliberation({ thinking, symbol }) {
  if (!thinking) return null;
  const seats = thinking.seats.map(id => { const st = window.lzStrategyGet ? window.lzStrategyGet(id) : null; return st ? { cn: st.name, color: window.lzStrategyColor(id), glyph: st.glyph || '策' } : null; }).filter(Boolean);
  const b = symbol.bars[thinking.bar];
  return (
    <div style={{ position: 'absolute', top: 14, left: '50%', transform: 'translateX(-50%)', zIndex: 8, display: 'flex', alignItems: 'center', gap: 11, background: 'var(--paper)', border: '1px solid var(--yin)', borderRadius: 11, padding: '9px 15px', boxShadow: '0 6px 24px rgba(28,24,20,0.16)', animation: 'fadeIn .2s ease', maxWidth: '92%' }}>
      <span style={{ display: 'flex' }}>
        {seats.map((s, i) => (
          <span key={i} style={{ width: 22, height: 22, marginLeft: i ? -5 : 0, background: s.color, color: 'var(--paper)', fontFamily: 'var(--serif)', fontSize: 11, display: 'flex', alignItems: 'center', justifyContent: 'center', borderRadius: 4, border: '1px solid var(--paper)', animation: 'pulse 1.1s ease-in-out infinite' }}>{s.glyph}</span>
        ))}
      </span>
      <div style={{ minWidth: 0 }}>
        <div className="serif" style={{ fontSize: 12.5, fontWeight: 600, color: 'var(--ink)' }}>{seats.map(s => s.cn).join(' · ')} 启发式扫描<DotDot /></div>
        <div className="mono" style={{ fontSize: 9.5, color: 'var(--ink-2)', marginTop: 2 }}>{b.date} 及之前行情 · MA / 量比价量规则(scanSeat · 非 LLM)· 真 agent 研判用「✦ 让 agent 真跑」</div>
      </div>
    </div>
  );
}

// ───────── 图表导航条 (尺度缩放 + 时间轴平移) ─────────
function ChartNav({ fbars, win, end, maxEnd, setZoom, setPanEnd }) {
  const total = maxEnd + 1;
  const canPan = win <= maxEnd;                       // 窗口比全集窄才能平移
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

window.LuoziApp = LuoziApp;
