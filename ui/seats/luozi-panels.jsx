// 观澜 · 落子 — 面板 (市况条 / 指标条 / 席位栏 / 决策流水 / 落子卡)

// 迷你折线
function MiniLine({ eq, color, rt, w = 88, h = 26 }) {
  const end = rt != null ? rt : eq.length - 1;
  const seg = eq.slice(0, end + 1);
  if (seg.length < 2) return <svg width={w} height={h} />;
  let lo = Math.min(...seg, 1), hi = Math.max(...seg, 1);
  const pad = (hi - lo) * 0.15 || 0.01; lo -= pad; hi += pad;
  const x = (i) => (i / (eq.length - 1)) * w;
  const y = (v) => h - (v - lo) / (hi - lo) * h;
  const d = seg.map((v, i) => (i ? 'L' : 'M') + x(i).toFixed(1) + ' ' + y(v).toFixed(1)).join(' ');
  const up = seg[seg.length - 1] >= 1;
  return (
    <svg width={w} height={h} style={{ display: 'block' }}>
      <line x1={0} x2={w} y1={y(1)} y2={y(1)} stroke="var(--line-soft)" strokeDasharray="2 2" />
      <path d={d + ' L' + x(end) + ' ' + h + ' L0 ' + h + ' Z'} fill={color} opacity="0.1" />
      <path d={d} fill="none" stroke={color} strokeWidth="1.3" />
    </svg>
  );
}

const pct = (x, d = 1) => (x >= 0 ? '+' : '') + (x * 100).toFixed(d) + '%';
const plFmt = (x) => x >= 99 ? '∞' : x.toFixed(2);

// 第3期:把落子的 seat(= strategy.id)解析成 {id,cn,color,glyph};兼容旧席位 id;永不返 undefined。
function lzSeatMeta(id) {
  const st = window.lzStrategyGet ? window.lzStrategyGet(id) : null;
  if (st) {
    const td = (window.LZ_TEMPLATES || {})[st.template] || {};
    return { id, cn: st.name, en: td.cn || st.template, creed: (st && st.creed) || td.creed || '', color: (window.lzStrategyColor ? window.lzStrategyColor(id) : st.color) || 'var(--ink-2)', glyph: st.glyph || '策' };
  }
  const seat = (window.LZ_SEATS || []).find(s => s.id === id);
  return seat || { id, cn: id, en: '', creed: '', color: 'var(--ink-2)', glyph: '策' };
}

// 研判触发原因 → 中文(研判循环流水用)
const LZ_REASON_CN = { manual: '手动研判', fill: '成交后研判', timer: '定时研判' };

// ───────── 条件单 · agent 盯盘(2b:右侧栏,非全屏)─────────
//   agent(/seats/order)按席位信条 + 真实时上下文出一张到价/放量/指标触发单 → 触发引擎(真 5min 回放)检验。
function OrderWatchPanel({ code, name, onTrigger, mode, fresh, positions, onClosePosition, strategies, asOf, onRealDecide, seatId }) {
  // T5 单 agent:seat 受控于 app 的当前策略(seatId prop);未传/失效回退首个策略。内部下拉退役,左栏「当前策略」卡是唯一切换入口。
  const seat = (seatId && (strategies || []).some(s => s.id === seatId)) ? seatId : ((strategies && strategies[0] && strategies[0].id) || 'momentum');
  const [otf, setOtf] = useState('day');   // 交易单周期:day(日线波段) / 5min(日内短线)
  const [order, setOrder] = useState(null);     // {seat,seat_cn,asof,model_name,ctx,order:{...}}
  const [gen, setGen] = useState(false);
  const [trig, setTrig] = useState(null);
  const [checking, setChecking] = useState(false);
  const [liveFired, setLiveFired] = useState(null);   // ②搬实盘:盘中真实时触发结果
  const [liveCtx, setLiveCtx] = useState(null);       // 最近一次实时上下文(盯盘指示)
  const [loopOn, setLoopOn] = useState(false);     // 研判循环开关(live:成交后 + 每小时封顶定时)
  const [loopLog, setLoopLog] = useState([]);      // 研判流水 [{at,reason,dir}](最新在前,留 8 条)
  const lastJudgeRef = useRef(0);                  // 上次研判 epoch ms(定时节流用)
  const timedRef = useRef(false);                  // 定时真研判在途守卫(防慢响应叠跑)
  const [lastJudgeAt, setLastJudgeAt] = useState(0);   // 与 lastJudgeRef 同步,仅供「下次研判」显示
  const [wEditLive, setWEditLive] = useState(null);    // P3:实盘面板就地调因子权重 w(管循环研判 runTimedDecide;null=用策略持久值,拖动覆盖并回存,与校场/决策卡同源)
  useEffect(() => { setOrder(null); setTrig(null); setGen(false); setChecking(false); setLiveFired(null); setLiveCtx(null); setLoopLog([]); lastJudgeRef.current = 0; setLastJudgeAt(0); }, [code]);
  useEffect(() => { setWEditLive(null); }, [seat]);    // 切策略 → 清就地覆盖,回落该策略持久 w
  const SEATCN = { reversal: '反转席', momentum: '动量席', event: '事件席', risk: '风控席' };
  const seatName = ((strategies || []).find(s => s.id === seat) || {}).name || SEATCN[seat] || '策略';   // 第3期:当前选中策略名
  const myHold = (positions || []).find(p => p.status === 'open' && p.seat === seat) || null;
  const holdPnl = (myHold && liveCtx && liveCtx.price != null && myHold.entry) ? ((+liveCtx.price / myHold.entry - 1) * 100) : null;
  // 持有天数:进场日(myHold.date,'YYYY-MM-DD')→ 今日,按自然日真算(两端归零点防时分漂移);当日=0。
  const heldDays = myHold ? (() => {
    const a = new Date(String(myHold.date) + 'T00:00:00').getTime();
    if (!isFinite(a)) return null;
    const b = new Date(); b.setHours(0, 0, 0, 0);
    const d = Math.round((b.getTime() - a) / 86400000);
    return d >= 0 ? d : null;
  })() : null;
  const runJudge = (reason) => {
    if (!window.lzSeatOrder || gen) return;
    lastJudgeRef.current = Date.now();
    setLastJudgeAt(Date.now());
    setGen(true); setTrig(null); setOrder(null); setLiveFired(null); setLiveCtx(null);
    const hold = myHold ? { entry: myHold.entry, since: myHold.date, days: heldDays } : null;
    // 第3期:seat 是 strategy.id;传策略模板给后端取信条,影子/上报仍按 strategy.id(= seat)。
    const strat = (strategies || []).find(s => s.id === seat) || (strategies || [])[0] || null;
    const tmpl = strat ? strat.template : ((window.LZ_TEMPLATE_IDS && window.LZ_TEMPLATE_IDS.indexOf(seat) >= 0) ? seat : 'momentum');
    const meta = window.lzSeatMeta ? window.lzSeatMeta(seat) : null;      // creed 优先策略实例自有字段,回退模板(Task 4)
    const rcp = window.lzRecipeForStrategy ? window.lzRecipeForStrategy(seat) : { cards: [] };
    const extra = { creed: (meta && meta.creed) || '', note: (rcp.cards[0] && rcp.cards[0].insight) || '',
      strategy_id: seat, strategy_name: strat ? (strat.name || seat) : (meta ? meta.cn : seat),
      date: (mode === 'backtest' && asOf) ? asOf : '' };   // 复盘=按游标历史日 PIT 思考;实盘=空→后端走实时
    window.lzSeatOrder(code, tmpl, otf, hold, extra).then(o => {
      setOrder(o); setGen(false);
      const hhmm = (o && o.asof && /\d\d:\d\d/.test(String(o.asof))) ? String(o.asof).slice(11, 16) : new Date().toTimeString().slice(0, 5);   // 用市场/数据时间(o.asof 如 16:14),非浏览器墙钟
      const dir = o && o.order && o.order.side;
      setLoopLog(L => [{ at: hhmm, reason, dir: dir || '—' }, ...L].slice(0, 8));
      // 持仓感知:研判判卖出 → 按现价平掉该影子持仓(系统只发"该平"信号,用户手机自己操作)
      if (myHold && dir && /卖/.test(dir) && onClosePosition) {
        const px = (liveCtx && liveCtx.price != null) ? +liveCtx.price : (o && o.ctx && o.ctx.price != null ? +o.ctx.price : null);
        if (px != null) onClosePosition(myHold.id, px);
      }
    });
  };
  const genOrder = () => runJudge('manual');     // 手动「立单」走同一通道,记一条手动研判
  // ⑤++ 定时真研判:研判循环到「判别间隔」即真调 /seats/decide(真研判·买/卖/观望+理由+思维链,非条件单);
  //   结果上报 onRealDecide → realDecs+图标记+研判历史(后端 decide 成功即落盘),绝不入 symbol.decisions/合议/净值。失败不报不落。
  const runTimedDecide = (reason) => {
    if (!window.lzSeatDecide || timedRef.current) return;
    if (window.lzPoolIsMonitored && !window.lzPoolIsMonitored(code)) return;   // 自选只看:不自动研判(手动「立单」/卡内手动研判仍可)
    lastJudgeRef.current = Date.now();
    setLastJudgeAt(Date.now());
    timedRef.current = true;
    const strat = (strategies || []).find(s => s.id === seat) || null;
    const meta = window.lzSeatMeta ? window.lzSeatMeta(seat) : null;
    const rcp = window.lzRecipeForStrategy ? window.lzRecipeForStrategy(seat) : { cards: [], research: [], factors: [] };
    const today = new Date().toISOString().slice(0, 10);   // 实盘 PIT 上限=今天(就是今天,非 look-ahead)
    window.lzSeatDecide({
      code: code, name: name, date: today,
      seat_cn: (meta && meta.cn) || seatName, creed: (meta && meta.creed) || '', mode: 'fast',
      strategy_id: seat, strategy_name: (strat && strat.name) || seatName,
      card: rcp.cards[0] ? { name: rcp.cards[0].name, insight: rcp.cards[0].insight, verdict: rcp.cards[0].verdict, conf: rcp.cards[0].conf, ic: rcp.cards[0].ic } : null,
      cards: rcp.cards, recipe_factors: rcp.factors,
      industry: (meta && meta.industry) || '',   // P1:后端按行业/票 PIT 按日浮出叙事卡(与回测 runRealThink 同口径);不再前端透传固定 research(后端会 re-surface 覆盖)
      regime: null,
      pa: !!(strat && strat.pa),                  // 价格行为:本席开关(默认关)。与 runDecide/runRealThink 同口径;仅 pa 开才注入几何+方法论进 LLM
      pa_method: (strat && strat.pa) ? (strat.paMethod || window.LZ_PA_METHOD_DEFAULT || '') : '',
      w: (function () { var _s = (window.lzStrategyGet && window.lzStrategyGet(seat)) || strat; return (_s && isFinite(+_s.w)) ? Math.max(0, Math.min(1, +_s.w)) : 0; })(),   // P3:实盘定时研判按本策略因子权重 w 混合——fire 时从 GL 现取最新(滑块 onChange 已同步 GL.put,无 prop 回流 stale 窗口;校场/决策卡/本面板三处改动皆覆盖)。0=纯LLM;>0 后端 (1-w)·LLM分+w·因子z分
    }).then(d => {
      timedRef.current = false;
      if (d && d.ok && d.direction) {
        const hhmm = (d.asof && /\d\d:\d\d/.test(String(d.asof))) ? String(d.asof).slice(11, 16) : new Date().toTimeString().slice(0, 5);
        setLoopLog(L => [{ at: hhmm, reason: reason, dir: d.direction }, ...L].slice(0, 8));
        if (onRealDecide) onRealDecide({ seat: seat, direction: d.direction, conf: d.confidence, rationale: d.rationale, reasoning: d.reasoning, asof: d.asof || today, model_name: d.model_name });
      }
    }).catch(() => { timedRef.current = false; });
  };
  const clauseOf = (t) => {
    const v = t.value;
    if (t.kind === 'price') return { label: '价', val: t.op + ' ' + v };
    if (t.kind === 'volRatio') return { label: '量比', val: t.op + ' ' + v };
    if (t.kind === 'maDiff20') return (t.op === '>=' && v === 0) ? { label: '站上 20 日线', val: '✓' }
      : (t.op === '<=' && v === 0) ? { label: '跌破 20 日线', val: '✓' }
        : { label: '乖离 MA20', val: t.op + ' ' + v };
    if (t.kind === 'rsi14') return { label: 'RSI14', val: t.op + ' ' + v };
    return { label: t.kind, val: t.op + ' ' + v };
  };
  const check = () => {
    const o = order && order.order;
    if (!o || !(o.triggers || []).length || checking || !window.lzFetchBars5 || !window.lzRunTriggerReplay) return;
    setChecking(true); setTrig(null);
    window.lzFetchBars5(code, 480).then(bars5 => {
      if (!bars5 || !bars5.length) { setTrig({ error: '无 5min 数据(后端/窗口)' }); setChecking(false); return; }
      const win = bars5.slice(-240);
      const ord = { id: 'chk', seat: order.seat, side: o.side, triggers: o.triggers, logic: o.logic };
      const res = window.lzRunTriggerReplay(win, [ord], 20);
      const f = res.fired[0];
      setTrig(f || { none: true, range: [win[0].date, win[win.length - 1].date] });
      if (f && onTrigger) onTrigger({ id: code + '·' + seat, at: f.at, side: o.side, fill: f.fill, seat: seat, stop: o.stop, take: o.take });   // seat = strategy.id(影子按策略记)
      setChecking(false);
    });
  };
  // ②搬实盘:live 模式对已生成条件单,每 8s 拉真实时上下文(/seats/live_eval),用同一 evalTrigger 比对;
  //   价到 / 指标满足即**自动触发**(盘中,无需点检验)→ 弹实时触发 + 标 K 线。fresh=false(休市)只显示不判。
  useEffect(() => {
    const o2 = order && order.order;
    if (mode !== 'live' || !o2 || !(o2.triggers || []).length || liveFired
      || !window.lzFetchLiveEval || !window.lzEvalTrigger) return;
    let alive = true;
    const pull = () => window.lzFetchLiveEval(code, (order && order.tf) || 'day').then(ctx => {
      if (!alive || !ctx) return;
      setLiveCtx(ctx);
      if (!ctx.fresh) return;                        // 仅盘中(报价日 > 末日K)才判触发
      const r = window.lzEvalTrigger({ triggers: o2.triggers, logic: o2.logic, status: 'armed' }, ctx);
      if (r && r.triggered) {
        setLiveFired({ at: ctx.asof, fill: r.fill, ctx: ctx });
        if (onTrigger) onTrigger({ id: code + '·' + seat + '·live', at: ctx.asofDate || ctx.asof, side: o2.side, fill: r.fill, seat: seat, stop: o2.stop, take: o2.take });   // seat = strategy.id
      }
    });
    pull();
    const iv = setInterval(pull, 8000);
    return () => { alive = false; clearInterval(iv); };
  }, [mode, order, liveFired, code]);
  // 成交后研判:开循环 + live + 实时触发(成交)后,延时 1.5s 自动发起下一轮研判;
  //   runJudge 内 setLiveFired(null) 复位 → 上面 8s 盯盘 effect 在新 order 上重新武装,形成 成交→再判→再盯 链。
  useEffect(() => {
    if (!loopOn || mode !== 'live' || !liveFired) return;
    const t = setTimeout(() => runJudge('fill'), 1500);
    return () => clearTimeout(t);
  }, [liveFired, loopOn, mode, seat, otf]);   // 含 seat/otf:成交后 1.5s 内若改席位/周期,再判用最新选择(防陈旧闭包)
  // 定时真研判:开循环 + live + 盘中(fresh),每分钟查一次;**按该策略 clock.decisionFreq 真节流**
  //   (hourly=每小时 / daily=当日仅一次),10min 硬地板防刷爆 LLM。到点调 runTimedDecide(真研判·非条件单)。
  //   deps 含 seat/otf/code/strategies 取最新选择与频率;lastJudgeRef 为 ref 跨重建持续,节流不被重置。
  useEffect(() => {
    if (!loopOn || mode !== 'live' || !fresh) return;
    let alive = true;
    const strat = (strategies || []).find(s => s.id === seat) || null;
    const fq = (strat && strat.clock && strat.clock.decisionFreq) || 'hourly';
    const tick = () => {
      if (!alive) return;
      const gap = Date.now() - lastJudgeRef.current;
      if (gap < 600000) return;                                                   // 10min 硬地板
      const due = fq === 'daily'
        ? (new Date(lastJudgeRef.current).toDateString() !== new Date().toDateString())   // 当日仅一次
        : (gap >= 3600000);                                                       // hourly:每小时封顶
      if (due) runTimedDecide('timer');
    };
    const iv = setInterval(tick, 60000);
    return () => { alive = false; clearInterval(iv); };
  }, [loopOn, mode, fresh, seat, otf, code, strategies]);
  const o = order && order.order;
  const dir = o && o.side;
  const dirColor = dir && /买/.test(dir) ? 'var(--zhu)' : (dir && /卖/.test(dir) ? 'var(--dai)' : 'var(--ink-2)');
  const logicCN = o && (o.logic || 'AND').toUpperCase() === 'OR' ? '或' : '且';
  return (
    <div style={{ borderBottom: '1px solid var(--line)', flexShrink: 0, background: 'linear-gradient(180deg, rgba(168,57,45,0.035), transparent 64%)' }}>
      {/* 头:朱砂「令」+ 席位 + 立单 */}
      {/* 第一行:令 + 标题 + 立单(标题不挤换行) */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '9px 14px 5px' }}>
        <span className="serif" style={{ width: 19, height: 19, background: 'var(--yin)', color: 'var(--paper)', fontSize: 12, fontWeight: 600, display: 'flex', alignItems: 'center', justifyContent: 'center', borderRadius: 2, flexShrink: 0 }}>令</span>
        <span className="serif" style={{ fontSize: 13.5, fontWeight: 600, color: 'var(--ink)', letterSpacing: '0.06em', whiteSpace: 'nowrap' }}>条件单 · 盯盘</span>
        <span onClick={genOrder} className="serif" style={{ marginLeft: 'auto', flexShrink: 0, whiteSpace: 'nowrap', fontSize: 11, letterSpacing: '0.06em', color: gen ? 'var(--ink-3)' : 'var(--paper)', background: gen ? 'transparent' : 'var(--yin)', border: gen ? '1px solid var(--line)' : 'none', cursor: gen ? 'default' : 'pointer', borderRadius: 3, padding: '3px 11px' }}>
          {gen ? '拟单中…' : '⚡ 立 单'}
        </span>
      </div>
      {/* 第二行:席位 / 周期 / 研判循环 各自有位置,不再换行 */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '0 14px 8px' }}>
        <span className="serif" title="当前策略(单 agent:在左栏「当前策略」卡切换;下拉退役)" style={{ fontSize: 10.5, color: 'var(--ink-1)', borderBottom: '1px solid var(--line)', padding: '0 2px 1px', whiteSpace: 'nowrap' }}>{seatName}</span>
        <select value={otf} onChange={e => setOtf(e.target.value)} className="serif" title="交易单周期:日线波段 / 5min 日内短线" style={{ fontSize: 10.5, color: 'var(--jin)', border: 'none', borderBottom: '1px solid var(--line)', background: 'transparent', padding: '0 2px 1px', cursor: 'pointer', outline: 'none' }}>
          <option value="day">日线</option>
          <option value="5min">5min</option>
        </select>
        {mode === 'live' && (
          <span onClick={() => setLoopOn(v => !v)} className="mono" title="研判循环:开 = 成交后自动再判 + 盘中按本策略「判别频率」(每小时/每天)真调 agent 研判(/seats/decide·进研判历史;手动「立单」始终可用)"
            style={{ marginLeft: 'auto', flexShrink: 0, whiteSpace: 'nowrap', fontSize: 9, padding: '2px 8px', borderRadius: 10, cursor: 'pointer', border: '1px solid ' + (loopOn ? 'var(--yin)' : 'var(--line)'), color: loopOn ? 'var(--paper)' : 'var(--ink-3)', background: loopOn ? 'var(--yin)' : 'transparent' }}>
            {loopOn ? '● 循环中' : '○ 研判循环'}
          </span>
        )}
        {mode === 'live' && loopOn && (() => {
          const strat = (strategies || []).find(function(s) { return s.id === seat; }) || null;
          const fq = (strat && strat.clock && strat.clock.decisionFreq) || 'hourly';
          var label;
          if (!fresh) { label = '下次研判 · 开盘后'; }   // 休市时定时器不跑(节流 effect 有 fresh 门控),不显乐观时刻
          else if (!lastJudgeAt) { label = '下次研判 · 即刻可触'; }
          else if (fq === 'daily') {
            label = new Date(lastJudgeAt).toDateString() === new Date().toDateString() ? '下次研判 · 次一交易日' : '下次研判 · 即刻可触';
          } else {
            var t = new Date(lastJudgeAt + 3600000);
            label = '下次研判 ~' + t.toTimeString().slice(0, 5);
          }
          return <span className="mono" title="按本策略「研判频率」节流:hourly=每小时封顶 · daily=每日一次 · 10min 硬地板" style={{ fontSize: 9, color: 'var(--jin)', marginLeft: 6 }}>{label}</span>;
        })()}
      </div>
      {mode === 'live' && (function () {
        const wStratL = (strategies || []).find(function (s) { return s.id === seat; }) || null;
        if (!wStratL) return null;
        const wL = (wEditLive != null ? wEditLive : ((isFinite(+wStratL.w)) ? +wStratL.w : 0));
        return (
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '0 14px 9px', flexWrap: 'wrap' }}>
            <span className="mono" title="因子进信号的混合权重 w(管循环·定时研判 runTimedDecide):0=纯 LLM(方向只由 agent 研判定);>0 按 (1−w)·LLM分 + w·vintage 因子 z 分混入决策方向(as-of 真 OOS,不看未来,非确定性回测)。改这里即回存到本策略,与校场/决策卡同一个 w。"
              style={{ fontSize: 8.5, color: 'var(--ink-3)', letterSpacing: '.04em', cursor: 'help', whiteSpace: 'nowrap' }}>权 · 因子进信号</span>
            <input type="range" min="0" max="1" step="0.05" value={wL}
              onChange={function (e) { var nw = Math.max(0, Math.min(1, +e.target.value)); setWEditLive(nw); var st = window.lzStrategyGet && window.lzStrategyGet(seat); if (st && window.lzStrategySave) window.lzStrategySave(Object.assign({}, st, { w: nw })); }}
              style={{ width: 132, accentColor: 'var(--yin)', cursor: 'pointer' }} />
            <span className="mono" style={{ fontSize: 10.5, fontWeight: 700, color: (wL > 0 ? 'var(--yin)' : 'var(--ink-3)'), minWidth: 42 }}>w={(+wL).toFixed(2)}</span>
            <span className="mono" style={{ fontSize: 8, color: wL > 0 ? 'var(--yin)' : 'var(--ink-3)', whiteSpace: 'nowrap' }}>{wL > 0 ? '混合 · 因子进循环研判' : '纯 LLM'}</span>
          </div>
        );
      })()}
      {myHold && (
        <div className="mono" style={{ fontSize: 9.5, color: 'var(--ink-2)', padding: '0 14px 8px', display: 'flex', gap: 10 }}>
          <span style={{ color: 'var(--yin)' }}>持仓中</span>
          <span>进场 <b>{myHold.entry}</b></span>
          {heldDays != null && <span>持 <b>{heldDays}</b> 日</span>}
          {holdPnl != null && <span>浮动 <b style={{ color: holdPnl >= 0 ? 'var(--zhu)' : 'var(--dai)' }}>{(holdPnl >= 0 ? '+' : '') + holdPnl.toFixed(2) + '%'}</b></span>}
          <span style={{ color: 'var(--ink-3)' }}>研判将判 继续持/平</span>
          <span title="影子持仓 = 本地止盈损巡检(只读 · 待退役);真实仓位以后端「仓位台账」为准,此处不计入业绩" style={{ marginLeft: 'auto', fontSize: 8, padding: '0 5px', borderRadius: 4, border: '1px dashed var(--line)', color: 'var(--ink-3)', whiteSpace: 'nowrap', alignSelf: 'center' }}>影子 · 台账为准</span>
        </div>
      )}
      {!o && !gen && <div className="serif" style={{ fontSize: 10.5, color: 'var(--ink-3)', padding: '0 14px 11px', lineHeight: 1.7, textWrap: 'pretty' }}>令 <b style={{ color: 'var(--ink-2)', fontWeight: 600 }}>{seatName}</b> 依信条 + 真实时数据,拟一张 <b style={{ color: 'var(--jin)' }}>到价 · 放量 · 指标</b> 触发的条件单 —— 价到则发讯,你照讯落单。</div>}
      {gen && <div className="serif" style={{ fontSize: 10.5, color: 'var(--yin)', padding: '0 14px 11px' }}>{seatName} 拟单中…(deepseek 真调,需几秒)</div>}
      {o && (
        <div style={{ margin: '1px 12px 12px', border: '1px solid var(--line)', borderLeft: '2.5px solid ' + dirColor, borderRadius: '1px 7px 7px 1px', background: 'var(--paper)', padding: '9px 12px 10px', boxShadow: '0 1px 2px rgba(28,24,20,0.05)' }}>
          {/* 方向 · 席位 · 现价 */}
          <div style={{ display: 'flex', alignItems: 'baseline', gap: 8 }}>
            <span className="serif" style={{ fontSize: 17, fontWeight: 700, color: dirColor, letterSpacing: '0.12em' }}>{dir || '—'}</span>
            <span className="serif" style={{ fontSize: 10, color: 'var(--ink-3)' }}>{order.seat_cn || ''}</span>
            <span className="mono" style={{ fontSize: 8, color: 'var(--jin)', border: '1px solid var(--jin)', borderRadius: 3, padding: '0 4px' }}>{order.tf === '5min' ? '5min 单' : '日线单'}</span>
            <span className="mono" style={{ marginLeft: 'auto', fontSize: 9, color: 'var(--ink-3)' }}>{order.ctx && order.ctx.price != null ? '现价 ' + order.ctx.price : ''}</span>
          </div>
          {/* 触发条款 */}
          <div style={{ marginTop: 8 }}>
            <div className="mono" style={{ fontSize: 8, color: 'var(--ink-3)', letterSpacing: '0.24em' }}>触 发 条 款</div>
            <div style={{ marginTop: 3 }}>
              {(o.triggers || []).map((t, i) => {
                const cl = clauseOf(t);
                return (
                  <div key={i} style={{ display: 'flex', alignItems: 'baseline', gap: 7, padding: '2.5px 0', borderBottom: i < (o.triggers.length - 1) ? '1px dotted var(--line-soft)' : 'none' }}>
                    <span className="serif" style={{ fontSize: 10, color: i ? 'var(--jin)' : 'var(--ink-3)', width: 14, flexShrink: 0, textAlign: 'center' }}>{i ? logicCN : '若'}</span>
                    <span className="sans" style={{ fontSize: 11.5, color: 'var(--ink-1)' }}>{cl.label}</span>
                    <span className="mono" style={{ marginLeft: 'auto', fontSize: 11.5, color: 'var(--yin)', fontWeight: 600 }}>{cl.val}</span>
                  </div>
                );
              })}
              {!(o.triggers || []).length && <div className="serif" style={{ fontSize: 10, color: 'var(--ink-3)' }}>(无有效触发条件)</div>}
            </div>
          </div>
          {/* 守 · 标 · 效期 · 印 */}
          <div style={{ display: 'flex', alignItems: 'center', gap: 13, marginTop: 8, paddingTop: 7, borderTop: '1px solid var(--line-soft)' }}>
            <span className="mono" style={{ fontSize: 9.5, color: 'var(--ink-3)' }}>守 <b style={{ color: 'var(--dai)', fontSize: 11.5 }}>{o.stop == null ? '—' : o.stop}</b></span>
            <span className="mono" style={{ fontSize: 9.5, color: 'var(--ink-3)' }}>标 <b style={{ color: 'var(--zhu)', fontSize: 11.5 }}>{o.take == null ? '—' : o.take}</b></span>
            <span className="serif" style={{ fontSize: 9.5, color: 'var(--ink-3)' }}>{o.validity || ''}</span>
            <span className="mono" title={'真·' + (order.model_name || '') + (order.asof ? ' · ' + order.asof : '')} style={{ marginLeft: 'auto', fontSize: 8, color: 'var(--yin)', border: '1px solid var(--zhu-soft)', borderRadius: 3, padding: '0 4px' }}>真·{(order.model_name || 'agent').split('/').pop()}</span>
          </div>
          {/* 拟单批语 */}
          {o.note && <div className="serif" style={{ fontSize: 10.5, color: 'var(--ink-2)', marginTop: 8, lineHeight: 1.65, paddingLeft: 8, borderLeft: '2px solid var(--jin)', textWrap: 'pretty' }}>{o.note}</div>}
          {/* 验触发 */}
          <div onClick={check} className="serif" style={{ marginTop: 9, fontSize: 10.5, color: 'var(--yin)', cursor: checking ? 'default' : 'pointer', display: 'inline-block' }}>
            {checking ? '○ 回放推演中…' : '◷ 复盘验触发 →'}
          </div>
          {/* 触发结果:朱砂「触发」印 + 柔光 */}
          {trig && (trig.error
            ? <div className="serif" style={{ fontSize: 9.5, color: 'var(--ink-3)', marginTop: 7 }}>{trig.error}</div>
            : trig.none
              ? <div className="serif" style={{ fontSize: 9.5, color: 'var(--ink-3)', marginTop: 7, lineHeight: 1.6 }}>窗口 {trig.range && trig.range.join(' → ')} 内未触发 —— 条件未满足,诚实不发讯。</div>
              : <div style={{ marginTop: 8, display: 'flex', gap: 9, alignItems: 'stretch', padding: '8px 9px', border: '1px solid var(--yin)', borderRadius: 5, background: 'rgba(168,57,45,0.05)', animation: 'fadeIn .35s ease, lzGlow 2.6s ease-in-out .35s infinite' }}>
                  <span className="serif" style={{ width: 26, background: 'var(--yin)', color: 'var(--paper)', fontSize: 12.5, fontWeight: 600, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', borderRadius: 2, lineHeight: 1.12, flexShrink: 0, animation: 'lzSeal .55s cubic-bezier(.2,1.5,.4,1)' }}><span>触</span><span>发</span></span>
                  <div style={{ minWidth: 0, display: 'flex', flexDirection: 'column', justifyContent: 'center' }}>
                    <div className="mono" style={{ fontSize: 10.5, color: 'var(--ink-1)' }}>{trig.at} · 成交 <b style={{ color: 'var(--yin)', fontSize: 12 }}>{trig.fill}</b></div>
                    <div className="mono" style={{ fontSize: 8.5, color: 'var(--ink-3)', marginTop: 2, lineHeight: 1.55 }}>该 bar 高 {trig.ctx && trig.ctx.high} · 低 {trig.ctx && trig.ctx.low} · 量比 {trig.ctx && trig.ctx.volRatio} · 乖离 {trig.ctx && trig.ctx.maDiff20}<br />真历史 5min 回放 · 同一引擎 live 复用</div>
                  </div>
                </div>)}
          {/* ②搬实盘:盘中实时盯盘 + 自动触发(同一 evalTrigger,ctx 换成 /seats/live_eval 逐 poll)*/}
          {mode === 'live' && (liveFired
            ? <div style={{ marginTop: 8, display: 'flex', gap: 9, alignItems: 'stretch', padding: '8px 9px', border: '1px solid var(--yin)', borderRadius: 5, background: 'rgba(168,57,45,0.08)', animation: 'fadeIn .35s ease, lzGlow 1.7s ease-in-out infinite' }}>
                <span className="serif" style={{ width: 26, background: 'var(--yin)', color: 'var(--paper)', fontSize: 12.5, fontWeight: 600, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', borderRadius: 2, lineHeight: 1.12, flexShrink: 0, animation: 'lzSeal .55s cubic-bezier(.2,1.5,.4,1)' }}><span>触</span><span>发</span></span>
                <div style={{ minWidth: 0, display: 'flex', flexDirection: 'column', justifyContent: 'center' }}>
                  <div className="mono" style={{ fontSize: 10.5, color: 'var(--ink-1)' }}>实时触发 · {liveFired.at} · 价 <b style={{ color: 'var(--yin)', fontSize: 12 }}>{liveFired.fill}</b></div>
                  <div className="serif" style={{ fontSize: 9, color: 'var(--ink-2)', marginTop: 2 }}>盘中真到价 —— 照此讯在手机自行落单(系统不代下单)。</div>
                </div>
              </div>
            : <div className="mono" style={{ marginTop: 9, fontSize: 9.5, color: 'var(--dai)', display: 'flex', alignItems: 'center', gap: 6 }}>
                <span style={{ width: 6, height: 6, borderRadius: '50%', background: 'var(--dai)', animation: 'pulse 1.5s ease-in-out infinite' }} />实时盯盘中…(每 8s 比对真价/指标{liveCtx && liveCtx.price != null ? ' · 现价 ' + liveCtx.price : ''})
              </div>)}
        </div>
      )}
      {loopLog.length > 0 && (
        <div style={{ padding: '0 14px 11px' }}>
          <div className="mono" style={{ fontSize: 8, color: 'var(--ink-3)', letterSpacing: '0.24em', marginBottom: 4 }}>研 判 流 水</div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
            {loopLog.map((e, i) => (
              <div key={i} className="mono" style={{ display: 'flex', alignItems: 'baseline', gap: 8, fontSize: 9.5 }}>
                <span style={{ color: 'var(--ink-3)' }}>{e.at}</span>
                <span style={{ color: e.reason === 'manual' ? 'var(--ink-2)' : e.reason === 'fill' ? 'var(--yin)' : 'var(--jin)' }}>{LZ_REASON_CN[e.reason] || e.reason}</span>
                <span style={{ marginLeft: 'auto', color: 'var(--ink-2)' }}>{e.dir}</span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

// ───────── 实盘 · 仓位台账(T6)─────────
//   后端持久台账(/seats/ledger jsonl · append-only · 重放出快照):开账设初始资金,自起始日起
//   每日更新仓位;今日交易/研判置顶展开,往日折叠(accordion);9:30 起随实盘落账。
//   双轨过渡:影子组合(lzShadow*)止盈损巡检与展示照跑,**台账为准**,影子只读待退役。
function LedgerPanel({ ledger, onRefresh, code }) {
  const [cashIn, setCashIn] = useState('100000');
  const [dateIn, setDateIn] = useState(() => new Date().toISOString().slice(0, 10));
  const [importShadow, setImportShadow] = useState(true);
  const [opening, setOpening] = useState(false);
  const [openErr, setOpenErr] = useState(null);
  const [openDays, setOpenDays] = useState({});     // 往日 accordion 展开集 {date: true}
  const [reopen, setReopen] = useState(false);      // 已开账时显示重开表单(旧账留档,从新账起算)
  const [mtOpen, setMtOpen] = useState(false);      // 手动调仓表单开合
  const [mtSide, setMtSide] = useState('buy');
  const [mtCode, setMtCode] = useState('');
  const [mtPrice, setMtPrice] = useState('');
  const [mtQty, setMtQty] = useState('100');
  const [mtErr, setMtErr] = useState(null);
  const [mtBusy, setMtBusy] = useState(false);
  const [tca, setTca] = useState(null);              // 事后 TCA(/seats/tca);仅当成交/决策集真变化时重拉
  const [tcaOpen, setTcaOpen] = useState(false);     // 逐笔滑点展开
  // 台账每轮询刷新一次 ledger 引用,但 /seats/tca 每笔成交要同步取当日日线+5min(重)。
  // 故按「成交+决策事件计数 + 起账日」签名去重:内容没变(纯计时轮询)就不重打 TCA,只有
  // 新成交 / 新研判 / 重开账才触发。签名是基本类型字符串,React 用 Object.is 比较天然去重。
  // 不变量:依赖台账「append-only 事件流」契约(成交/决策只追加、不就地改删)→ 计数即足表征内容变化。
  // 若将来后端会就地订正某笔成交(改价/量而计数不变),须改 start_date 或加修订计数,否则 TCA 不会重拉。
  let _tcaSig = null;
  if (ledger && ledger.opened) {
    let _nt = 0, _nd = 0;
    (ledger.days || []).forEach(d => {
      _nt += (d.trades && d.trades.length) || 0;
      _nd += (d.decisions && d.decisions.length) || 0;
    });
    _tcaSig = (ledger.start_date || '') + '|' + _nt + '|' + _nd;
  }
  useEffect(() => {
    let dead = false;
    if (_tcaSig && window.lzSeatsTca) {
      window.lzSeatsTca().then(r => { if (!dead) setTca(r); });
    } else { setTca(null); }
    return () => { dead = true; };
  }, [_tcaSig]);
  const doManualTrade = async () => {
    if (mtBusy || !window.lzLedgerPost) return;
    const c = String(mtCode || code || '').replace(/^(SH|SZ|BJ)/i, '');
    const px = +mtPrice, q = Math.floor(+mtQty);
    if (!c) { setMtErr('缺代码'); return; }
    if (!(px > 0)) { setMtErr('价格须为正数'); return; }
    if (!(q > 0)) { setMtErr('数量须为正整数(股)'); return; }
    setMtBusy(true); setMtErr(null);
    const nm = (window.LZ_SYMBOLS && window.LZ_SYMBOLS[c] && window.LZ_SYMBOLS[c].meta && window.LZ_SYMBOLS[c].meta.name) || c;
    const r = await window.lzLedgerPost({ kind: 'trade', date: today, code: c, name: nm,
      side: mtSide, price: px, qty: q, reason: '手动调仓', source: 'manual' });
    setMtBusy(false);
    if (!r || !r.ok) { setMtErr((r && r.reason) || '落账失败(后端未起)'); return; }
    setMtPrice(''); setMtOpen(false);
    if (onRefresh) onRefresh();
  };
  const today = new Date().toISOString().slice(0, 10);
  const money = (x, d) => (x == null || !isFinite(+x)) ? '—'
    : (+x).toLocaleString('zh-CN', { minimumFractionDigits: d == null ? 0 : d, maximumFractionDigits: d == null ? 0 : d });
  // 影子迁移源:扫全部影子台账的 open 仓(开账表单计数 + 勾选导入用;name 从盯盘池 meta 回查)
  const shadowOpenList = () => {
    if (!window.lzShadowListAll) return [];
    const out = [];
    try {
      window.lzShadowListAll().forEach(b => {
        ((b.shadow && b.shadow.positions) || []).forEach(p => {
          if (p && p.status === 'open' && p.entry) out.push({
            code: b.code,
            name: (window.LZ_SYMBOLS && window.LZ_SYMBOLS[b.code] && window.LZ_SYMBOLS[b.code].meta && window.LZ_SYMBOLS[b.code].meta.name) || b.code,
            entry: +p.entry,
          });
        });
      });
    } catch (e) {}
    return out;
  };
  const doOpen = async () => {
    if (opening || !window.lzLedgerPost) return;
    const c = +cashIn;
    if (!(c > 0)) { setOpenErr('初始资金须为正数'); return; }
    setOpening(true); setOpenErr(null);
    const r = await window.lzLedgerPost({ kind: 'open', date: dateIn, cash: c });
    if (!r || !r.ok) { setOpening(false); setOpenErr((r && r.reason) || '开账失败(后端未起/参数非法)'); return; }
    if (importShadow) {
      // 影子 open 仓逐笔迁入(一手 100 股起步;串行落账,余额不足 422 → lzLedgerPost 返 null 静默跳过)
      const list = shadowOpenList();
      for (const e of list) {
        try {
          await window.lzLedgerPost({ kind: 'trade', date: today, code: e.code, name: e.name, side: 'buy', price: e.entry, qty: 100, reason: '影子迁移', source: 'manual' });
        } catch (err) {}
      }
    }
    setOpening(false); setReopen(false);
    if (onRefresh) onRefresh();
  };
  const head = (
    <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '9px 14px 5px' }}>
      <span className="serif" style={{ width: 19, height: 19, background: 'var(--dai)', color: 'var(--paper)', fontSize: 12, fontWeight: 600, display: 'flex', alignItems: 'center', justifyContent: 'center', borderRadius: 2, flexShrink: 0 }}>账</span>
      <span className="serif" style={{ fontSize: 13.5, fontWeight: 600, color: 'var(--ink)', letterSpacing: '0.06em', whiteSpace: 'nowrap' }}>实盘 · 仓位台账</span>
      <span className="mono" title="实盘=一个组合:所有盯盘股票共用一个现金池 / 一条净值线(非按票分账)" style={{ flexShrink: 0, fontSize: 8, padding: '1px 6px', borderRadius: 4, border: '1px solid var(--line)', color: 'var(--ink-3)' }}>组合账 · 跨股共享</span>
      {ledger && ledger.opened && <span onClick={() => setReopen(r => !r)} className="mono" title="重开账:旧账事件留档,从新一笔初始资金起算(重置清空入口)" style={{ marginLeft: 'auto', flexShrink: 0, fontSize: 9, color: reopen ? 'var(--zhu)' : 'var(--ink-3)', cursor: 'pointer', padding: '1px 7px', border: '1px solid ' + (reopen ? 'var(--zhu)' : 'var(--line)'), borderRadius: 5 }}>{reopen ? '取消重开' : '重开账'}</span>}
      {ledger && ledger.opened && <span onClick={onRefresh} className="mono" title="重拉台账(/seats/ledger/state)" style={{ marginLeft: reopen != null && ledger && ledger.opened ? 0 : 'auto', flexShrink: 0, fontSize: 11, color: 'var(--ink-3)', cursor: 'pointer', padding: '0 3px' }}>↻</span>}
    </div>
  );
  // ── 载入中 ──
  if (!ledger) return (
    <div style={{ borderBottom: '1px solid var(--line)', flexShrink: 0, background: 'linear-gradient(180deg, rgba(74,107,92,0.035), transparent 64%)' }}>
      {head}
      <div className="mono" style={{ padding: '0 14px 11px', fontSize: 9.5, color: 'var(--ink-3)' }}>台账载入中…</div>
    </div>
  );
  // ── 未开账(或已开账点了「重开账」):开账表单 ──
  if (!ledger.opened || reopen) {
    const nShadow = shadowOpenList().length;
    const uline = { fontSize: 11, color: 'var(--ink-1)', border: 'none', borderBottom: '1px solid var(--line)', background: 'transparent', padding: '0 2px 1px', outline: 'none' };
    return (
      <div style={{ borderBottom: '1px solid var(--line)', flexShrink: 0, background: 'linear-gradient(180deg, rgba(74,107,92,0.04), transparent 64%)' }}>
        {head}
        <div style={{ padding: '2px 14px 11px' }}>
          <div style={{ display: 'flex', alignItems: 'baseline', gap: 14, flexWrap: 'wrap' }}>
            <label className="mono" style={{ fontSize: 9.5, color: 'var(--ink-3)', display: 'flex', alignItems: 'baseline', gap: 5 }}>初始资金
              <input value={cashIn} onChange={e => setCashIn(e.target.value)} inputMode="numeric" className="mono" style={{ ...uline, width: 76 }} />
            </label>
            <label className="mono" style={{ fontSize: 9.5, color: 'var(--ink-3)', display: 'flex', alignItems: 'baseline', gap: 5 }}>起始日
              <input type="date" value={dateIn} onChange={e => setDateIn(e.target.value)} className="mono" style={{ ...uline, fontSize: 10.5 }} />
            </label>
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginTop: 9 }}>
            <label className="mono" style={{ fontSize: 9.5, color: 'var(--ink-2)', display: 'flex', alignItems: 'center', gap: 5, cursor: 'pointer' }}>
              <input type="checkbox" checked={importShadow} onChange={e => setImportShadow(e.target.checked)} style={{ accentColor: 'var(--yin)', margin: 0 }} />
              导入影子持仓<span style={{ color: 'var(--ink-3)' }}>({nShadow} 笔在档)</span>
            </label>
            <span onClick={doOpen} className="serif" style={{ marginLeft: 'auto', flexShrink: 0, whiteSpace: 'nowrap', fontSize: 11, letterSpacing: '0.06em', color: opening ? 'var(--ink-3)' : 'var(--paper)', background: opening ? 'transparent' : 'var(--yin)', border: opening ? '1px solid var(--line)' : 'none', cursor: opening ? 'default' : 'pointer', borderRadius: 3, padding: '3px 11px' }}>{opening ? '开账中…' : '✦ 开 账'}</span>
          </div>
          {openErr && <div className="mono" style={{ fontSize: 9, color: 'var(--dai)', marginTop: 6 }}>{openErr}</div>}
          <div className="serif" style={{ fontSize: 9.5, color: 'var(--ink-3)', marginTop: 8, lineHeight: 1.6, textWrap: 'pretty' }}>影子组合升级为仓位台账 · 后端持久 —— 设一笔初始资金,自起始日起每日更新仓位、记交易与研判。</div>
        </div>
      </div>
    );
  }
  // ── 已开账:账本视图 ──
  const eq = ledger.equity;
  const posVal = eq != null ? eq - ledger.cash : (() => {
    let s = 0, any = false;
    (ledger.positions || []).forEach(p => { if (p.mkt_value != null) { s += +p.mkt_value; any = true; } });
    return any ? s : null;
  })();
  const ret = (eq != null && ledger.init_cash > 0) ? (eq - ledger.init_cash) / ledger.init_cash : null;
  const days = ledger.days || [];
  const isToday = !!(days[0] && days[0].date === today);
  const todayDay = isToday ? days[0] : null;
  const restDays = isToday ? days.slice(1) : days;
  const num = (l, v, c, t) => (
    <div style={{ minWidth: 64 }} title={t || ''}>
      <div className="mono" style={{ fontSize: 8, color: 'var(--ink-3)', letterSpacing: '.12em' }}>{l}</div>
      <div className="mono" style={{ fontSize: 13, fontWeight: 600, color: c || 'var(--ink)', marginTop: 1, whiteSpace: 'nowrap' }}>{v}</div>
    </div>
  );
  const tRow = (t, i) => (
    <div key={'t' + i} className="mono" style={{ display: 'flex', alignItems: 'baseline', gap: 7, fontSize: 9.5, padding: '2px 0' }}>
      <span style={{ color: t.side === 'buy' ? 'var(--zhu)' : 'var(--dai)', fontWeight: 600, flexShrink: 0 }}>{t.side === 'buy' ? '买' : '卖'}</span>
      <span style={{ color: 'var(--ink-1)', flexShrink: 0 }}>{t.name || t.code}</span>
      <span style={{ color: 'var(--ink-2)', whiteSpace: 'nowrap' }}>{t.qty} 股 @ {t.price}</span>
      <span style={{ marginLeft: 'auto', color: 'var(--ink-3)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', maxWidth: 108 }} title={t.reason || ''}>{t.reason || t.source || ''}</span>
    </div>
  );
  const dRow = (d, i) => (
    <div key={'d' + i} className="mono" style={{ display: 'flex', alignItems: 'baseline', gap: 7, fontSize: 9.5, padding: '2px 0' }}>
      <span style={{ color: d.direction && /买/.test(d.direction) ? 'var(--zhu)' : (d.direction && /卖/.test(d.direction) ? 'var(--dai)' : 'var(--ink-2)'), fontWeight: 600, flexShrink: 0 }}>{d.direction || '—'}</span>
      <span style={{ color: 'var(--ink-1)', flexShrink: 0 }}>{d.name || d.code}</span>
      {d.confidence != null && <span style={{ color: 'var(--ink-3)', whiteSpace: 'nowrap' }}>置信 {d.confidence}</span>}
      <span style={{ marginLeft: 'auto', color: 'var(--ink-3)', whiteSpace: 'nowrap' }}>{String(d.ts || '').replace('T', ' ').slice(11, 16) || (LZ_REASON_CN[d.source] || d.source || '')}</span>
    </div>
  );
  return (
    <div style={{ borderBottom: '1px solid var(--line)', flexShrink: 0, background: 'linear-gradient(180deg, rgba(74,107,92,0.035), transparent 64%)' }}>
      {head}
      {/* 头部四数:净值 / 现金 / 持仓市值 / 收益(equity null = 估值缺价 诚实降级) */}
      <div style={{ display: 'flex', gap: 14, padding: '1px 14px 6px', flexWrap: 'wrap' }}>
        {num('净值', eq == null ? '估值缺价' : money(eq), eq == null ? 'var(--ink-3)' : (ret != null && ret < 0 ? 'var(--dai)' : 'var(--ink)'),
          eq == null ? ('持仓 ' + (ledger.covered || 0) + '/' + (ledger.n_positions || 0) + ' 票有现价 —— 缺价不估,诚实置空') : ('MTM 截至 ' + (ledger.equity_date || '—')))}
        {num('现金', money(ledger.cash))}
        {num('持仓市值', posVal == null ? '—' : money(posVal))}
        {num('收益', ret == null ? '—' : pct(ret), ret == null ? 'var(--ink-3)' : (ret >= 0 ? 'var(--zhu)' : 'var(--dai)'))}
      </div>
      <div className="mono" style={{ fontSize: 8.5, color: 'var(--ink-3)', padding: '0 14px 7px', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
        起始 {ledger.start_date} · 初始 {money(ledger.init_cash)} · 已实现 {money(ledger.realized, 0)} · 胜率 {ledger.win_rate == null ? '—' : (ledger.win_rate * 100).toFixed(0) + '%'}{ledger.n_closed ? '(' + ledger.n_closed + ' 笔)' : ''}
        <span onClick={() => { setMtOpen(o => !o); setMtErr(null); if (!mtCode) setMtCode(String(code || '').replace(/^(SH|SZ|BJ)/i, '')); }}
              style={{ marginLeft: 8, cursor: 'pointer', color: mtOpen ? 'var(--zhu)' : 'var(--yin)', borderBottom: '1px dotted currentColor' }}>{mtOpen ? '收起调仓' : '✎ 手动调仓'}</span>
      </div>
      {/* 手动调仓:买/卖 + 代码 + 价 + 量(后端校验现金/持仓,拒因显形) */}
      {mtOpen && (
        <div style={{ margin: '0 14px 8px', border: '1px solid var(--line)', borderRadius: 7, padding: '7px 10px', background: 'var(--paper)' }}>
          <div style={{ display: 'flex', alignItems: 'baseline', gap: 10, flexWrap: 'wrap' }}>
            <span className="mono" style={{ fontSize: 9.5, display: 'inline-flex', gap: 0, border: '1px solid var(--line)', borderRadius: 5, overflow: 'hidden' }}>
              {[['buy', '买入'], ['sell', '卖出']].map(([v, t]) => (
                <span key={v} onClick={() => setMtSide(v)} style={{ padding: '2px 9px', cursor: 'pointer',
                  color: mtSide === v ? '#fff' : 'var(--ink-2)',
                  background: mtSide === v ? (v === 'buy' ? 'var(--zhu)' : 'var(--dai)') : 'transparent' }}>{t}</span>
              ))}
            </span>
            <label className="mono" style={{ fontSize: 9.5, color: 'var(--ink-3)', display: 'flex', alignItems: 'baseline', gap: 4 }}>代码
              <input value={mtCode} onChange={e => setMtCode(e.target.value)} className="mono" placeholder="300750"
                     style={{ width: 56, fontSize: 10.5, color: 'var(--ink-1)', border: 'none', borderBottom: '1px solid var(--line)', background: 'transparent', outline: 'none', padding: '0 2px 1px' }} />
            </label>
            <label className="mono" style={{ fontSize: 9.5, color: 'var(--ink-3)', display: 'flex', alignItems: 'baseline', gap: 4 }}>价
              <input value={mtPrice} onChange={e => setMtPrice(e.target.value)} inputMode="decimal" className="mono" placeholder="0.00"
                     style={{ width: 52, fontSize: 10.5, color: 'var(--ink-1)', border: 'none', borderBottom: '1px solid var(--line)', background: 'transparent', outline: 'none', padding: '0 2px 1px' }} />
            </label>
            <label className="mono" style={{ fontSize: 9.5, color: 'var(--ink-3)', display: 'flex', alignItems: 'baseline', gap: 4 }}>量(股)
              <input value={mtQty} onChange={e => setMtQty(e.target.value)} inputMode="numeric" className="mono"
                     style={{ width: 48, fontSize: 10.5, color: 'var(--ink-1)', border: 'none', borderBottom: '1px solid var(--line)', background: 'transparent', outline: 'none', padding: '0 2px 1px' }} />
            </label>
            <span onClick={doManualTrade} className="serif" style={{ marginLeft: 'auto', fontSize: 10.5, letterSpacing: '0.06em', whiteSpace: 'nowrap',
              color: mtBusy ? 'var(--ink-3)' : 'var(--paper)', background: mtBusy ? 'transparent' : 'var(--yin)',
              border: mtBusy ? '1px solid var(--line)' : 'none', borderRadius: 3, padding: '2px 10px', cursor: mtBusy ? 'default' : 'pointer' }}>{mtBusy ? '落账中…' : '落 账'}</span>
          </div>
          {mtErr && <div className="mono" style={{ fontSize: 9, color: 'var(--dai)', marginTop: 5 }}>{mtErr}</div>}
        </div>
      )}
      <div style={{ padding: '0 14px 4px', maxHeight: 264, overflowY: 'auto' }}>
        {/* 今日区:置顶默认展开(days 逆序今日在前) */}
        {todayDay && (
          <div style={{ marginBottom: 6, border: '1px solid var(--line)', borderLeft: '2.5px solid var(--dai)', borderRadius: '1px 7px 7px 1px', background: 'var(--paper)', padding: '6px 10px 7px' }}>
            <div className="mono" style={{ fontSize: 8, color: 'var(--dai)', letterSpacing: '0.24em', marginBottom: 3 }}>今 日 · {todayDay.date}</div>
            {(todayDay.decisions || []).map(dRow)}
            {(todayDay.trades || []).map(tRow)}
            {!(todayDay.decisions || []).length && !(todayDay.trades || []).length && <div className="mono" style={{ fontSize: 9, color: 'var(--ink-3)' }}>今日暂无交易 / 研判记录</div>}
          </div>
        )}
        {/* 当前持仓表(每日更新仓位;null 安全显 —) */}
        {(ledger.positions || []).length > 0 && (
          <div style={{ marginBottom: 6 }}>
            <div className="mono" style={{ fontSize: 8, color: 'var(--ink-3)', letterSpacing: '0.24em', marginBottom: 2 }}>当 前 持 仓</div>
            {(ledger.positions || []).map((p, i) => {
              const uplPct = (p.upl != null && p.avg_cost > 0 && p.qty > 0) ? p.upl / (p.avg_cost * p.qty) : null;
              return (
                <div key={(p.code || '') + i} className="mono" style={{ display: 'flex', alignItems: 'baseline', gap: 6, fontSize: 9.5, padding: '2.5px 0', borderBottom: '1px dotted var(--line-soft)' }}>
                  <span style={{ color: 'var(--ink-1)', fontWeight: 600, flexShrink: 0 }}>{p.name || p.code}</span>
                  <span style={{ color: 'var(--ink-3)', flexShrink: 0 }}>{p.code}</span>
                  <span style={{ color: 'var(--ink-2)', whiteSpace: 'nowrap' }}>{p.qty} 股</span>
                  <span style={{ color: 'var(--ink-3)', whiteSpace: 'nowrap' }}>成本 {p.avg_cost != null ? (+p.avg_cost).toFixed(2) : '—'}</span>
                  <span style={{ color: 'var(--ink-3)', whiteSpace: 'nowrap' }}>现 {p.last_close != null ? (+p.last_close).toFixed(2) : '—'}</span>
                  <span style={{ marginLeft: 'auto', fontWeight: 600, whiteSpace: 'nowrap', color: uplPct == null ? 'var(--ink-3)' : (uplPct >= 0 ? 'var(--zhu)' : 'var(--dai)') }}>{uplPct == null ? '—' : pct(uplPct)}</span>
                </div>
              );
            })}
          </div>
        )}
        {/* 往日折叠:之前的买卖/研判记录 accordion(行头 日期 + N 笔交易 · M 次研判) */}
        {restDays.map((day) => {
          const opn = !!openDays[day.date];
          return (
            <div key={day.date} style={{ borderBottom: '1px solid var(--line-soft)' }}>
              <div onClick={() => setOpenDays(s => Object.assign({}, s, { [day.date]: !opn }))} className="hover-row mono" style={{ display: 'flex', alignItems: 'baseline', gap: 8, cursor: 'pointer', fontSize: 9.5, padding: '4px 2px' }}>
                <span style={{ color: 'var(--ink-2)' }}>{day.date}</span>
                <span style={{ color: 'var(--ink-3)' }}>{(day.trades || []).length} 笔交易 · {(day.decisions || []).length} 次研判</span>
                <span style={{ marginLeft: 'auto', color: 'var(--ink-3)' }}>{opn ? '▾' : '▸'}</span>
              </div>
              {opn && (
                <div style={{ padding: '0 2px 5px' }}>
                  {(day.decisions || []).map(dRow)}
                  {(day.trades || []).map(tRow)}
                  {!(day.decisions || []).length && !(day.trades || []).length && <div className="mono" style={{ fontSize: 9, color: 'var(--ink-3)' }}>无记录</div>}
                </div>
              )}
            </div>
          );
        })}
        {!todayDay && !restDays.length && <div className="mono" style={{ fontSize: 9, color: 'var(--ink-3)', padding: '2px 0 5px' }}>已开账,暂无交易 / 研判落账</div>}
      </div>
      {/* 事后 TCA(执行质量):成交价 vs 当日基准滑点 bps;只读台账 /seats/tca;缺基准诚实空,绝不画假成本 */}
      {tca && tca.opened && tca.n_trades > 0 && (() => {
        const t = tca.tca || {};
        const bpsFmt = (v) => (v == null ? '—' : (v >= 0 ? '+' : '') + (+v).toFixed(1) + ' bps');
        const bpsColor = (v) => (v == null ? 'var(--ink-3)' : (v > 0 ? 'var(--dai)' : 'var(--zhu)'));   // 正成本=吃亏=黛,负=占便宜=朱
        const cov = t.coverage || {};
        return (
          <div style={{ margin: '4px 14px 8px', border: '1px solid var(--line)', borderLeft: '2.5px solid var(--yin)', borderRadius: '1px 7px 7px 1px', padding: '6px 10px 7px', background: 'var(--paper)' }}>
            <div className="mono" style={{ fontSize: 8, color: 'var(--ink-3)', letterSpacing: '0.22em', marginBottom: 5, display: 'flex', alignItems: 'baseline', gap: 6 }}>
              <span>执 行 质 量 · T C A</span>
              <span style={{ letterSpacing: 0, color: 'var(--ink-3)' }}>{t.n_trades} 笔</span>
              <span onClick={() => setTcaOpen(o => !o)} style={{ marginLeft: 'auto', letterSpacing: 0, cursor: 'pointer', color: tcaOpen ? 'var(--zhu)' : 'var(--ink-3)' }}>{tcaOpen ? '收起逐笔' : '逐笔 ▸'}</span>
            </div>
            <div style={{ display: 'flex', gap: 14, flexWrap: 'wrap' }}>
              {[['vs VWAP', t.cost_vwap_bps], ['vs 开盘', t.cost_open_bps], ['vs 收盘', t.cost_close_bps], ['vs 到达价', t.cost_arrival_bps]].map(([l, v], i) => (
                <div key={i}>
                  <div className="mono" style={{ fontSize: 7.5, color: 'var(--ink-3)', letterSpacing: '.05em' }}>{l}</div>
                  <div className="mono" style={{ fontSize: 13, fontWeight: 600, color: bpsColor(v), marginTop: 1 }}>{bpsFmt(v)}</div>
                </div>
              ))}
            </div>
            {(t.by_strategy || []).length > 0 && (
              <div style={{ marginTop: 6, borderTop: '1px dotted var(--line-soft)', paddingTop: 4 }}>
                <div className="mono" style={{ fontSize: 7.5, color: 'var(--ink-3)', letterSpacing: '.06em', marginBottom: 2 }}>按 策 略(vs VWAP·成交额加权)</div>
                {(t.by_strategy || []).slice(0, 5).map((s, i) => (
                  <div key={i} className="mono" style={{ display: 'flex', alignItems: 'baseline', gap: 6, fontSize: 9, padding: '1.5px 0' }}>
                    <span style={{ color: 'var(--ink-2)', fontWeight: 600 }}>{s.strategy}</span>
                    <span style={{ color: 'var(--ink-3)' }}>{s.n_trades} 笔</span>
                    <span style={{ marginLeft: 'auto', fontWeight: 600, color: bpsColor(s.cost_vwap_bps) }}>{bpsFmt(s.cost_vwap_bps)}</span>
                  </div>
                ))}
              </div>
            )}
            {tcaOpen && (
              <div style={{ marginTop: 5, borderTop: '1px dotted var(--line-soft)', paddingTop: 4 }}>
                {(t.trades || []).slice(0, 40).map((r, i) => (
                  <div key={i} className="mono" style={{ display: 'flex', alignItems: 'baseline', gap: 5, fontSize: 8.5, padding: '1.5px 0', borderBottom: '1px dotted var(--line-soft)' }}>
                    <span style={{ color: r.side === 'buy' ? 'var(--zhu)' : 'var(--dai)', flexShrink: 0, fontWeight: 600 }}>{r.side === 'buy' ? '买' : '卖'}</span>
                    <span style={{ color: 'var(--ink-1)', fontWeight: 600 }}>{r.name || r.code}</span>
                    <span style={{ color: 'var(--ink-3)' }}>{String(r.date || '').slice(5)}</span>
                    <span style={{ color: 'var(--ink-3)' }}>{r.price != null ? (+r.price).toFixed(2) : '—'}</span>
                    <span style={{ marginLeft: 'auto', color: bpsColor(r.cost_vwap_bps), fontWeight: 600 }}>{bpsFmt(r.cost_vwap_bps)}</span>
                  </div>
                ))}
              </div>
            )}
            <div className="mono" style={{ fontSize: 7.5, color: 'var(--ink-3)', marginTop: 5, lineHeight: 1.5 }}>
              正=吃亏(买高/卖低于基准)· 负=占便宜 · 按成交额加权 · VWAP 覆盖 {cov.vwap || 0}/{t.n_trades}{(cov.arrival || 0) < t.n_trades ? ' · 到达价 ' + (cov.arrival || 0) + '/' + t.n_trades + '(仅决策链接成交)' : ''} · 日级影子盘口径,非 tick 级真实回报
            </div>
          </div>
        );
      })()}
      {/* 诚实边界:第一期前端驱动落账(页面在线时) */}
      <div className="mono" style={{ fontSize: 8, color: 'var(--ink-3)', padding: '0 14px 9px' }}>盘中自动研判 → 落账:仅「研判循环」开启 + 页面在线 + 盘中(有实时报价)时;无后端定时器,关页面即停。</div>
    </div>
  );
}

// ───────── 市况条 ─────────
function MarketBar({ symbol, revealTo, mode, market, quote }) {
  const rt = revealTo != null ? revealTo : symbol.bars.length - 1;
  const b = symbol.bars[rt] || symbol.bars[symbol.bars.length - 1];
  const real = !!(market && market.regime);     // 真 /watch/market_status(今日快照)
  const regime = real ? market.regime : null;    // 无真 regime 不编造(诚实 '—',不再回退启发式 mock)
  const regColor = real
    ? (/牛|上行|修复/.test(regime) ? 'var(--zhu)' : /熊|下行|派发|退潮/.test(regime) ? 'var(--dai)' : 'var(--ink-2)')
    : 'var(--ink-3)';
  const cell = (l, v, c) => (
    <span className="mono" style={{ fontSize: 11, color: 'var(--ink-3)', whiteSpace: 'nowrap' }}>{l} <b style={{ color: c || 'var(--ink-2)', fontSize: 12 }}>{v}</b></span>
  );
  const limitUp = (real && market.limitUp != null) ? market.limitUp : null;   // 无真数据不编造(诚实 '—')
  const limitDn = (real && market.limitDn != null) ? market.limitDn : null;
  // 日期:实盘=今日真交易日(quote.asofDate);复盘=游标 bar 日(b.date)。修实盘误显历史末根日(lastBarDate)。
  const rowDate = (mode === 'live' && quote && quote.asofDate) ? quote.asofDate : b.date;
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 16, padding: '5px 16px', borderBottom: '1px solid var(--line)', background: 'rgba(28,24,20,0.02)', whiteSpace: 'nowrap', overflowX: 'auto', flexShrink: 0 }}>
      <span className="mono" style={{ fontSize: 10, color: 'var(--ink-3)', flexShrink: 0 }}>市况</span>
      {real && <span className="mono" title={'行情 / 涨跌停来自 /watch/market_status 最新快照(可能滞后于今日)· ' + (market.date || '')} style={{ fontSize: 8.5, padding: '1px 6px', borderRadius: 4, border: '1px solid var(--zhu-soft)', color: 'var(--yin)', flexShrink: 0 }}>真·快照{market.date ? ' ' + String(market.date).slice(5) : ''}</span>}
      {cell('日期', rowDate)}
      {cell('行情', regime || '—', regColor)}
      {!real && <span className="mono" style={{ fontSize: 8.5, color: 'var(--ink-3)' }}>(行情快照未连接)</span>}
      {cell('涨停', limitUp == null ? '—' : limitUp, limitUp >= 50 ? 'var(--zhu)' : 'var(--ink-2)')}
      <span className="mono" style={{ fontSize: 10, color: 'var(--ink-3)' }}>跌停 <b style={{ color: limitDn >= 12 ? 'var(--dai)' : 'var(--ink-3)' }}>{limitDn == null ? '—' : limitDn}</b></span>
      {/* 主线已移除:数据为月级雷达(as_of 远早于今日),与"今日实盘"不符,按用户要求不显示 */}
      <span style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: 8, flexShrink: 0 }}>
        {mode === 'live'
          ? (quote && quote.price != null
            ? (
              <span className="mono" style={{ display: 'flex', alignItems: 'center', gap: 5, color: 'var(--yin)', fontSize: 10 }}>
                <span style={{ width: 7, height: 7, borderRadius: '50%', background: quote.fresh ? 'var(--zhu)' : 'var(--ink-3)', animation: quote.fresh ? 'pulse 1.4s ease-in-out infinite' : 'none' }} />{quote.fresh ? '实时盘中' : '最新收盘'}{quote.asof ? ' · ' + String(quote.asof).slice(5) : ''}
              </span>
            )
            : <span className="mono" style={{ fontSize: 10, color: 'var(--yin)', display: 'flex', alignItems: 'center', gap: 5 }}>
              <span style={{ width: 7, height: 7, borderRadius: '50%', background: 'var(--yin)', animation: 'pulse 1.6s ease-in-out infinite' }} />实时盯盘 · 等待报价…</span>)
          : cell('回测区间', symbol.bars[0].date + ' → ' + symbol.bars[symbol.bars.length - 1].date)}
      </span>
    </div>
  );
}

// ───────── 当前标的行情(顶栏放大版;live=真实时盘口 / 复盘=当前 bar)─────────
function StockHero({ symbol, rt, mode, quote }) {
  const idx = rt != null ? rt : symbol.bars.length - 1;
  const bar = symbol.bars[idx] || symbol.bars[symbol.bars.length - 1];
  const prev = symbol.bars[idx - 1];
  const live = mode === 'live' && quote && quote.price != null;
  const big = (x) => { x = +x; if (!isFinite(x)) return '—'; const a = Math.abs(x);
    return a >= 1e8 ? (x / 1e8).toFixed(2) + '亿' : a >= 1e4 ? (x / 1e4).toFixed(0) + '万' : x.toLocaleString(); };
  const px = (x) => (x == null || !isFinite(+x)) ? '—' : (+x).toFixed(2);

  let price, chgPct, chgAmt, open, high, low, extra, status, hasDot, tag, tagTitle;
  if (live) {
    price = +quote.price;
    chgPct = quote.changePercent != null ? +quote.changePercent : null;
    chgAmt = quote.change != null ? +quote.change : (quote.prevClose != null ? price - quote.prevClose : null);
    open = quote.open; high = quote.high; low = quote.low;
    extra = [
      { l: '量比', v: quote.volRatio != null ? (+quote.volRatio).toFixed(2) : '—' },
      { l: '换手', v: quote.turnoverRate != null ? (+quote.turnoverRate).toFixed(2) + '%' : '—' },
      { l: '额', v: quote.amount != null ? big(+quote.amount * 1e4) : '—' },   // 腾讯 amount 单位=万元(f[37])→ ×1e4 转元再格式化
    ];
    hasDot = true;
    status = (quote.fresh ? '实时盘中' : '最新收盘') + (quote.asof ? ' · ' + String(quote.asof).slice(5) : '');
    tag = '真·盘口'; tagTitle = '腾讯实时盘口 /seats/quote(同引擎 /quotes 源)';
  } else {
    price = bar.c;
    chgAmt = prev ? bar.c - prev.c : null;
    chgPct = prev ? (bar.c - prev.c) / prev.c * 100 : null;
    open = bar.o; high = bar.h; low = bar.l;
    extra = [{ l: '量', v: big(bar.v) }];
    hasDot = mode === 'live';                       // 实盘但报价未到:仍亮灯+「等待报价」,绝不误标「复盘」
    status = mode === 'live' ? '实时盯盘 · 等待报价…' : '复盘 · ' + bar.date;
    tag = mode === 'live' ? '待报价' : '当前 bar';
    tagTitle = mode === 'live' ? '实盘已开、报价未到(/seats/quote 轮询中)→ 暂显最后日K收盘' : '复盘游标所在 K 线 OHLC(' + bar.date + ')';
  }
  const up = (chgPct || 0) >= 0;
  const c = up ? 'var(--zhu)' : 'var(--dai)';
  const kv = (l, v, col) => (
    <span style={{ display: 'flex', gap: 4, alignItems: 'baseline', whiteSpace: 'nowrap' }}>
      <span style={{ fontSize: 9, color: 'var(--ink-3)' }}>{l}</span>
      <b className="mono" style={{ fontSize: 11.5, fontWeight: 500, color: col || 'var(--ink-2)' }}>{v}</b>
    </span>
  );
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 15, padding: '6px 18px', borderRight: '1px solid var(--line)',
      background: 'linear-gradient(90deg, rgba(168,57,45,0.06), rgba(168,57,45,0.012))', flexShrink: 0 }}>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 3, minWidth: 0 }}>
        <div style={{ display: 'flex', alignItems: 'baseline', gap: 6 }}>
          <span className="serif" style={{ fontSize: 16, fontWeight: 700, color: 'var(--ink)', whiteSpace: 'nowrap', letterSpacing: '.01em' }}>{symbol.meta.name}</span>
          <span className="mono" style={{ fontSize: 10, color: 'var(--ink-3)' }}>{symbol.meta.code}</span>
        </div>
        <span className="mono" style={{ display: 'flex', alignItems: 'center', gap: 5, fontSize: 9.5, color: hasDot ? 'var(--yin)' : 'var(--ink-3)', whiteSpace: 'nowrap' }}>
          {hasDot && <span style={{ width: 6, height: 6, borderRadius: '50%', background: (quote && quote.fresh) ? 'var(--zhu)' : 'var(--yin)', animation: 'pulse 1.5s ease-in-out infinite' }} />}
          {status}
        </span>
      </div>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 9 }}>
        <span className="mono" style={{ fontSize: 30, fontWeight: 600, color: c, lineHeight: 1, letterSpacing: '-.015em' }}>{px(price)}</span>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 2, lineHeight: 1.05 }}>
          <span className="mono" style={{ fontSize: 13, fontWeight: 600, color: c }}>{up ? '▲' : '▼'} {chgPct == null ? '—' : (up ? '+' : '') + chgPct.toFixed(2) + '%'}</span>
          <span className="mono" style={{ fontSize: 11, color: c }}>{chgAmt == null ? ' ' : (up ? '+' : '') + chgAmt.toFixed(2)}</span>
        </div>
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, auto)', columnGap: 14, rowGap: 2, alignContent: 'center' }}>
        {kv('开', px(open))}
        {kv('高', px(high), 'var(--zhu)')}
        {kv('低', px(low), 'var(--dai)')}
        {extra.map((e, i) => (
          <span key={i} style={{ display: 'flex', gap: 4, alignItems: 'baseline', whiteSpace: 'nowrap' }}>
            <span style={{ fontSize: 9, color: 'var(--ink-3)' }}>{e.l}</span>
            <b className="mono" style={{ fontSize: 11.5, fontWeight: 500, color: 'var(--ink-2)' }}>{e.v}</b>
          </span>
        ))}
      </div>
      <span title={tagTitle} className="mono" style={{ fontSize: 8.5, padding: '1px 6px', borderRadius: 4, whiteSpace: 'nowrap', border: '1px solid ' + (live ? 'var(--zhu-soft)' : 'var(--line)'), color: live ? 'var(--yin)' : 'var(--ink-3)', alignSelf: 'center' }}>{tag}</span>
    </div>
  );
}

// ───────── 指标条 ─────────
function MetricCard({ label, value, sub, color }) {
  return (
    <div style={{ flex: 1, padding: '8px 14px', borderRight: '1px solid var(--line-soft)' }}>
      <div className="mono" style={{ fontSize: 9, color: 'var(--ink-3)', letterSpacing: '.08em' }}>{label}</div>
      <div className="mono" style={{ fontSize: 19, fontWeight: 600, color: color || 'var(--ink)', marginTop: 3, letterSpacing: '-.01em' }}>{value}</div>
      {sub && <div className="mono" style={{ fontSize: 9, color: 'var(--ink-3)', marginTop: 1 }}>{sub}</div>}
    </div>
  );
}
function MetricsStrip({ m, benchTotal, label, symbol, rt, mode, quote, ledger, shadowM, portfolioM, onStartTracking }) {
  // T6:实盘区切「仓位台账」口径(全局一本账,本票⇄组合 toggle 退役);复盘分支零改动。
  //   shadowM / portfolioM / onStartTracking 已 deprecated —— 签名保留(调用处仍传)但不再消费,
  //   影子组合仅在 OrderWatchPanel 过渡期双轨展示,台账为准。
  const upc = (x) => x >= 0 ? 'var(--zhu)' : 'var(--dai)';
  // 基准两种降级可区分:benchTotal=null → 真断连「基准 —」;源滞后(benchAsof < 本票末日)→
  // 「基准 +x% · 截至MM-DD」+ title 注明指数源更新滞后(值是真值,只是截至日早于本票)。
  const benchStale = benchTotal != null && symbol && symbol.benchAsof && symbol.bars && symbol.bars.length
    && symbol.benchAsof < symbol.bars[symbol.bars.length - 1].date;
  const money = (x) => (x == null || !isFinite(+x)) ? '—' : (+x).toLocaleString('zh-CN', { maximumFractionDigits: 0 });
  const lg = mode === 'live' ? ledger : null;
  const lgOn = !!(lg && lg.opened);
  return (
    <div style={{ display: 'flex', alignItems: 'stretch', borderBottom: '1px solid var(--line)', background: 'var(--paper)', flexShrink: 0, overflowX: 'auto' }}>
      {symbol && <StockHero symbol={symbol} rt={rt} mode={mode} quote={quote} />}
      <div style={{ display: 'flex', flexDirection: 'column', justifyContent: 'center', padding: '0 14px', borderRight: '1px solid var(--line)', background: 'rgba(168,57,45,0.04)', flexShrink: 0 }}>
        <span className="serif" style={{ fontSize: 12, fontWeight: 600, color: 'var(--yin)', whiteSpace: 'nowrap' }}>{label || '策略'}</span>
        {lgOn && <span className="mono" title="实盘仓位台账 · 后端持久(/seats/ledger 事件重放)· 全局一本账" style={{ fontSize: 7.5, color: 'var(--ink-3)', marginTop: 2, whiteSpace: 'nowrap', letterSpacing: '.02em' }}>{'台账 · ' + String(lg.start_date || '').slice(5) + ' 起'}</span>}
      </div>
      {mode === 'live'
        ? (lgOn
          ? (() => {
              // 台账口径六卡:净值 / 现金 / 持仓市值 / 今日决策 / 胜率 / 已实现(null 全部诚实显「—」)
              const eq = lg.equity;
              const posVal = eq != null ? eq - lg.cash : (() => {
                let s = 0, any = false;
                (lg.positions || []).forEach(p => { if (p.mkt_value != null) { s += +p.mkt_value; any = true; } });
                return any ? s : null;
              })();
              const ret = (eq != null && lg.init_cash > 0) ? (eq - lg.init_cash) / lg.init_cash : null;
              const today = new Date().toISOString().slice(0, 10);
              const d0 = (lg.days || [])[0];
              const isToday = !!(d0 && d0.date === today);
              const nDec = isToday ? (d0.decisions || []).length : 0;
              const nTrd = isToday ? (d0.trades || []).length : 0;
              return (<>
                <MetricCard label="净值" value={eq == null ? '估值缺价' : money(eq)} sub={ret == null ? ((lg.covered || 0) + '/' + (lg.n_positions || 0) + ' 票有价') : '收益 ' + pct(ret)} color={eq == null ? 'var(--ink-3)' : (ret != null ? upc(ret) : 'var(--ink)')} />
                <MetricCard label="现金" value={money(lg.cash)} sub={'自 ' + (lg.start_date || '')} />
                <MetricCard label="持仓市值" value={posVal == null ? '—' : money(posVal)} sub={(lg.n_positions || 0) + ' 持仓'} color={posVal == null ? 'var(--ink-3)' : 'var(--ink)'} />
                <MetricCard label="今日决策" value={String(nDec)} sub={nTrd + ' 笔成交'} />
                <MetricCard label="胜率" value={lg.win_rate == null ? '—' : (lg.win_rate * 100).toFixed(0) + '%'} sub={(lg.n_closed || 0) + ' 笔了结'} />
                <MetricCard label="已实现" value={money(lg.realized)} color={upc(lg.realized || 0)} />
              </>);
            })()
          : (<div style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '0 16px', flex: 1 }}>
              <span className="serif" style={{ fontSize: 12, color: 'var(--ink-3)' }}>{lg == null ? '台账载入中…' : '未开账 —— 右栏「实盘 · 仓位台账」开账后启用(不显历史回测数字)'}</span>
            </div>))
        : (m == null
          // 复盘未选 run → 无真实回测净值,诚实空态(不显 scanSeat 启发式假数字)
          ? (<div style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '0 16px', flex: 1 }}>
              <span className="serif" style={{ fontSize: 12, color: 'var(--ink-3)', lineHeight: 1.5 }}>未选回测 —— 右栏「回测历史」点开一次 run 看真实净值/指标,或「让 agent 真跑」生成第一次</span>
            </div>)
          : (<>
            <MetricCard label="累计收益" value={pct(m.total)} sub={benchTotal == null
              ? <span title="真指数未连接">基准 —</span>
              : (benchStale
                ? <span title="指数源更新滞后,非横盘">{'基准 ' + pct(benchTotal) + ' · 截至' + symbol.benchAsof.slice(5)}</span>
                : '基准 ' + pct(benchTotal))} color={upc(m.total)} />
            <MetricCard label="年化" value={pct(m.annual)} color={upc(m.annual)} />
            <MetricCard label="SHARPE" value={m.sharpe.toFixed(2)} color={m.sharpe >= 1 ? 'var(--ink)' : 'var(--dai)'} />
            <MetricCard label="最大回撤" value={pct(m.mdd)} color="var(--dai)" />
            <MetricCard label="胜率" value={(m.winRate * 100).toFixed(0) + '%'} sub={m.nWin + '/' + m.nTrades + ' 笔'} />
            <MetricCard label="盈亏比" value={plFmt(m.plRatio)} />
          </>))}
    </div>
  );
}

// ───────── 当前策略栏(T5 单 agent:多席位启停列表 + 编排官·合议块退役,一次只看一个策略)─────────
function SeatRail({ strategy, strategies, onPick, ps, rt }) {
  const meta = strategy ? lzSeatMeta(strategy.id) : null;
  const clk = (strategy && strategy.clock) || {};
  const pctOf = (x) => (x != null && isFinite(+x)) ? (+x * 100).toFixed(0) + '%' : '—';
  const m = ps && ps.metrics;
  const list = strategies || [];
  return (
    <div style={{ width: 244, flexShrink: 0, borderRight: '1px solid var(--line)', display: 'flex', flexDirection: 'column', minHeight: 0, background: 'var(--paper)' }}>
      <div style={{ padding: '10px 12px', borderBottom: '1px solid var(--line)', flexShrink: 0 }}>
        <div className="serif" style={{ fontSize: 13, fontWeight: 600 }}>当前策略</div>
        <div className="mono" style={{ fontSize: 9, color: 'var(--ink-3)', marginTop: 2 }}>单 agent · 一次只看一个</div>
      </div>
      {!strategy && (
        <div className="serif" style={{ padding: 14, fontSize: 11, color: 'var(--ink-3)', lineHeight: 1.7, textWrap: 'pretty' }}>本票暂无策略 — 去「校场」钤印一个再回来盯盘。</div>
      )}
      {strategy && (
        <div style={{ padding: '12px 12px 11px', borderBottom: '1px solid var(--line)', background: 'rgba(168,57,45,0.04)', flexShrink: 0 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
            <span style={{ width: 22, height: 22, flexShrink: 0, background: meta.color, color: 'var(--paper)', fontFamily: 'var(--serif)', fontSize: 12, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>{meta.glyph}</span>
            <div style={{ minWidth: 0, flex: 1 }}>
              <div className="serif" style={{ fontSize: 12.5, fontWeight: 600, color: 'var(--ink)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{meta.cn}</div>
              <div className="mono" style={{ fontSize: 8.5, color: 'var(--ink-3)', letterSpacing: '.06em', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{meta.en}</div>
            </div>
            <span style={{ width: 36, height: 18, flexShrink: 0 }}>
              {ps && <MiniLine eq={ps.eq} color="var(--yin)" rt={rt} w={36} h={18} />}
            </span>
          </div>
          <div className="serif" title={meta.creed} style={{ fontSize: 10.5, color: 'var(--ink-2)', margin: '2px 0 7px', lineHeight: 1.5, display: '-webkit-box', WebkitLineClamp: 2, WebkitBoxOrient: 'vertical', overflow: 'hidden' }}>{meta.creed}</div>
          <div className="mono" style={{ fontSize: 8.5, color: 'var(--ink-3)', marginBottom: 7, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
            止损 {pctOf(clk.stopLoss)} · 止盈 {pctOf(clk.takeProfit)} · 持有 ≤{clk.maxHold != null ? clk.maxHold + ' 日' : '—'} · {clk.decisionFreq === 'daily' ? '每日研判' : '每时研判'}
          </div>
          <div style={{ display: 'flex', gap: 10 }}>
            <span className="mono" style={{ fontSize: 10, color: 'var(--ink-3)' }}>收益 <b style={{ color: m ? (m.total >= 0 ? 'var(--zhu)' : 'var(--dai)') : 'var(--ink-3)' }}>{m ? pct(m.total) : '—'}</b></span>
            <span className="mono" style={{ fontSize: 10, color: 'var(--ink-3)' }}>Sharpe <b style={{ color: 'var(--ink)' }}>{m ? m.sharpe.toFixed(2) : '—'}</b></span>
            <span className="mono" style={{ fontSize: 10, color: 'var(--ink-3)' }}>{m ? m.nTrades + ' 笔' : ''}</span>
          </div>
          {!m && <div className="mono" style={{ fontSize: 8, color: 'var(--ink-3)', marginTop: 3, opacity: 0.85 }}>选「回测历史」看某次 run 的真实净值</div>}
          <div className="mono" style={{ fontSize: 8.5, color: 'var(--ink-3)', marginTop: 6 }}>配方 {(strategy.refs || []).length} 件 · 喂研判依据</div>
        </div>
      )}
      {list.length > 1 && (
        <div style={{ flex: 1, overflowY: 'auto', minHeight: 0 }}>
          <div className="mono" style={{ fontSize: 8.5, color: 'var(--ink-3)', letterSpacing: '.1em', padding: '8px 12px 3px' }}>本票策略 · 点击切换</div>
          {list.map(s => {
            const om = lzSeatMeta(s.id);
            const cur = strategy && s.id === strategy.id;
            return (
              <div key={s.id} onClick={() => { if (!cur && onPick) onPick(s.id); }} className="hover-row" style={{
                padding: '8px 12px', borderBottom: '1px solid var(--line-soft)', cursor: cur ? 'default' : 'pointer',
                display: 'flex', alignItems: 'center', gap: 8,
                borderLeft: '2px solid ' + (cur ? 'var(--yin)' : 'transparent'),
                background: cur ? 'rgba(168,57,45,0.05)' : 'transparent',
              }}>
                <span style={{ width: 18, height: 18, flexShrink: 0, background: cur ? om.color : 'transparent', border: '1px solid ' + om.color, color: cur ? 'var(--paper)' : om.color, fontFamily: 'var(--serif)', fontSize: 10, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>{om.glyph}</span>
                <span className="serif" style={{ fontSize: 11.5, fontWeight: cur ? 600 : 400, color: cur ? 'var(--ink)' : 'var(--ink-1)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', flex: 1 }}>{om.cn}</span>
                {cur && <span className="mono" style={{ fontSize: 8, color: 'var(--yin)', flexShrink: 0 }}>当前</span>}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

// ───────── 决策流水 helper(DecisionCard 等仍依赖)─────────
function sideGlyph(d) { return d.warn ? '◆' : d.side === 'buy' ? '▲' : '▼'; }
function sideCN(d) { return d.warn ? '预警' : d.side === 'buy' ? '买入' : '卖出'; }

// ───────── 实盘 · 真研判流水 ─────────
// 只显真 LLM agent 研判(realDecs[code]:定时/手动/哨兵/真跑),带「真·LLM」徽章;
// 替代已退役的 scanSeat「信号队列」(那是启发式扫描、非真信号)。按开账日起算(重开账=干净起步)。
function LiveDecideFlow({ decs, openDate }) {
  const [openKey, setOpenKey] = useState(null);
  const list = (decs || [])
    .filter(d => !openDate || String(d.asof || d.date || '').slice(0, 10) >= openDate)
    .slice().reverse();
  const dirCol = (d) => d.side === 'buy' ? 'var(--zhu)' : d.side === 'sell' ? 'var(--dai)' : 'var(--ink-3)';
  return (
    <div style={{ display: 'flex', flexDirection: 'column', minHeight: 0, height: '100%' }}>
      <div style={{ padding: '9px 13px', borderBottom: '1px solid var(--line)', flexShrink: 0, display: 'flex', alignItems: 'baseline', gap: 8 }}>
        <span className="serif" style={{ fontSize: 12.5, fontWeight: 600 }}>真 · 研判流水</span>
        <span className="mono" style={{ fontSize: 9, color: 'var(--ink-3)' }}>{list.length} 条</span>
        <span className="mono" title="只显真 LLM agent 研判(/seats/decide):定时/手动/哨兵/真跑;非 scanSeat 启发式扫描" style={{ fontSize: 8, padding: '1px 6px', borderRadius: 4, border: '1px solid var(--yin)', color: 'var(--yin)', flexShrink: 0 }}>真 · LLM</span>
      </div>
      <div style={{ flex: 1, overflowY: 'auto', minHeight: 0 }}>
        {list.length === 0 && <div style={{ padding: 14, fontSize: 11, color: 'var(--ink-3)', fontFamily: 'var(--mono)' }}>尚无真 LLM 研判 — 开「研判循环」盘中自动判,或卡内「席位 · agent 研判」手动判</div>}
        {list.map((d) => {
          const col = dirCol(d);
          const open = openKey === d.key;
          return (
            <div key={d.key} className="hover-row" style={{ borderBottom: '1px solid var(--line-soft)' }}>
              <div onClick={() => setOpenKey(open ? null : d.key)} style={{ padding: '8px 13px', cursor: 'pointer', borderLeft: '2px solid ' + (open ? col : 'transparent') }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                  <span className="serif" style={{ fontSize: 12, fontWeight: 600, color: col }}>{d.direction || (d.side === 'buy' ? '买入' : d.side === 'sell' ? '卖出' : '观望')}</span>
                  <span className="mono" style={{ fontSize: 9, color: 'var(--ink-3)' }}>{String(d.asof || d.date || '').slice(0, 16)}</span>
                  {(d.conf != null ? d.conf : d.confidence) != null && <span className="mono" style={{ fontSize: 9, color: 'var(--ink-3)' }}>置信 {d.conf != null ? d.conf : d.confidence}</span>}
                  <span style={{ flex: 1 }} />
                  {d.model_name && <span className="mono" style={{ fontSize: 8, color: 'var(--ink-3)' }}>{d.model_name}</span>}
                </div>
                <div className="mono" style={{ fontSize: 9, color: 'var(--ink-3)', marginTop: 3, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{d.rationale || ''}</div>
              </div>
              {open && d.reasoning && <div className="mono" style={{ padding: '0 13px 9px', fontSize: 9, color: 'var(--ink-2)', whiteSpace: 'pre-wrap', maxHeight: 220, overflowY: 'auto', lineHeight: 1.5 }}>{d.reasoning}</div>}
            </div>
          );
        })}
      </div>
    </div>
  );
}

// ───────── 回测历史(席位 → 历史 → 内嵌决策流水,从属层级)─────────
// RunPicker:复盘右栏第一位。只列**当前席位**(strategyId)的 run;点开一条 = 选中
// (图上 B/S 标记跟随)+ 该条目下内嵌展开它的决策流水;点流水行 → 详情卡(RunDecCard)。
function RunPicker({ code, bump, selRun, onSelect, strategyId, strategyName, runDecs, selected, onPickDec }) {
  const [runs, setRuns] = useState([]);
  const [busy, setBusy] = useState(false);
  const [localBump, setLocalBump] = useState(0);
  const [armClear, setArmClear] = useState(false);   // 两段式确认防误触
  useEffect(() => {
    let dead = false; setBusy(true);
    (window.lzRunsList ? window.lzRunsList(code) : Promise.resolve([])).then(rs => { if (!dead) { setRuns(rs || []); setBusy(false); } });
    return () => { dead = true; };
  }, [code, bump, localBump]);
  useEffect(() => {
    if (!armClear) return;
    const t = setTimeout(() => setArmClear(false), 3000);   // 3s 不二次点击自动撤防
    return () => clearTimeout(t);
  }, [armClear]);
  const doClear = async () => {
    if (!armClear) { setArmClear(true); return; }
    setArmClear(false);
    if (window.lzRunsClear) await window.lzRunsClear(code);  // 后端落水位标记,历史行留档不删
    onSelect(null); setLocalBump(x => x + 1);
  };
  // 从属于席位:只显当前策略的 run(旧 run 缺 strategy_id 的不混入别的席位)
  const mine = runs.filter(r => !strategyId || r.strategy_id === strategyId);
  const flow = (selRun ? (runDecs || []) : []).slice().reverse();   // 内嵌流水:最新在上
  return (
    <div style={{ flexShrink: 0, borderBottom: '1px solid var(--line)', display: 'flex', flexDirection: 'column' }}>
      <div style={{ padding: '9px 13px', borderBottom: '1px solid var(--line-soft)', flexShrink: 0, display: 'flex', alignItems: 'baseline', gap: 8 }}>
        <span className="serif" style={{ fontSize: 12.5, fontWeight: 600 }}>回测历史</span>
        {strategyName && <span className="mono" style={{ fontSize: 9, color: 'var(--yin)' }}>{strategyName}</span>}
        <span className="mono" style={{ fontSize: 9, color: 'var(--ink-3)' }}>{mine.length} 次真跑</span>
        <span style={{ flex: 1 }} />
        {mine.length > 0 && (
          <span onClick={doClear} className="mono" title="清空本票回测历史(落水位标记,后端记录留档不删)"
                style={{ fontSize: 9, cursor: 'pointer', padding: '1px 7px', borderRadius: 5,
                         color: armClear ? '#fff' : 'var(--ink-3)',
                         background: armClear ? 'var(--zhu)' : 'transparent',
                         border: '1px solid ' + (armClear ? 'var(--zhu)' : 'var(--line)') }}>
            {armClear ? '确认清空?' : '清空'}
          </span>
        )}
      </div>
      <div style={{ maxHeight: 332, overflowY: 'auto' }}>
        {busy && <div className="mono" style={{ padding: '10px 13px', fontSize: 10, color: 'var(--ink-3)' }}>载入…</div>}
        {!busy && mine.length === 0 && <div className="mono" style={{ padding: '10px 13px', fontSize: 10, color: 'var(--ink-3)' }}>{strategyName ? ('「' + strategyName + '」尚无回测历史 —— 点下方『让 agent 真跑』生成第一次') : '尚无回测历史 —— 点下方『让 agent 真跑』生成第一次'}</div>}
        {!busy && mine.map((r) => {
          const sel = selRun && selRun.run_id === r.run_id;
          return (
            <div key={r.run_id} style={{ borderBottom: '1px solid var(--line-soft)' }}>
              <div onClick={() => onSelect(r)} className="hover-row" style={{
                padding: '7px 13px', cursor: 'pointer',
                borderLeft: '2px solid ' + (sel ? 'var(--zhu)' : 'transparent'),
                background: sel ? 'rgba(168,57,45,0.07)' : 'transparent',
              }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 7 }}>
                  <span className="mono" style={{ fontSize: 9, color: sel ? 'var(--zhu)' : 'var(--ink-3)', flexShrink: 0, width: 10, textAlign: 'center' }}>{sel ? '▾' : '▸'}</span>
                  <span className="mono" style={{ fontSize: 8.5, color: 'var(--ink-3)', flexShrink: 0 }}>{String(r.ts || '').replace('T', ' ').slice(5, 16)}</span>
                  <span className="mono" style={{ fontSize: 8.5, color: 'var(--ink-3)' }}>{r.start_date}~{r.end_date}</span>
                  <span style={{ flex: 1 }} />
                  <span className="mono" style={{ fontSize: 8.5, color: 'var(--ink-2)', flexShrink: 0 }}>买{r.n_buy || 0} 卖{r.n_sell || 0} 观{r.n_watch || 0}</span>
                </div>
              </div>
              {/* 内嵌决策流水:选中的 run 在自己条目下展开;点行 → 详情卡 + 图上高亮 */}
              {sel && (
                <div style={{ borderLeft: '2px solid var(--zhu)', background: 'rgba(168,57,45,0.03)' }}>
                  {flow.length === 0 && <div className="mono" style={{ padding: '6px 13px 8px 30px', fontSize: 9.5, color: 'var(--ink-3)' }}>载入决策…(或该 run 无落盘决策)</div>}
                  {flow.map((d) => {
                    const on = selected && selected.key === d.key;
                    const col = d.side === 'buy' ? 'var(--zhu)' : d.side === 'sell' ? 'var(--dai)' : 'var(--ink-2)';
                    return (
                      <div key={d.key} onClick={() => onPickDec && onPickDec(d)} className="hover-row" style={{
                        cursor: 'pointer', padding: '4px 13px 5px 30px', borderTop: '1px dotted var(--line-soft)',
                        background: on ? 'rgba(28,24,20,0.05)' : 'transparent',
                      }}>
                        <div style={{ display: 'flex', alignItems: 'baseline', gap: 7 }}>
                          <span className="serif" style={{ fontSize: 10.5, fontWeight: 600, color: col, flexShrink: 0 }}>{d.direction || '—'}</span>
                          <span className="mono" style={{ fontSize: 8.5, color: 'var(--ink-3)', flexShrink: 0 }}>{d.date}</span>
                          {d.conf != null && <span className="mono" style={{ fontSize: 8.5, color: 'var(--ink-3)', flexShrink: 0 }}>{d.conf}%</span>}
                          <span className="mono" style={{ fontSize: 8.5, color: 'var(--ink-2)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', flex: 1 }} title={d.rationale || ''}>{d.rationale || ''}</span>
                          {(d.card_names || []).length > 0 && <span className="mono" title={'命中经验卡:' + (d.card_names || []).join('、')} style={{ fontSize: 8, color: 'var(--yin)', flexShrink: 0 }}>⌘{d.card_names.length}</span>}
                          {(d.research || []).length > 0 && <span className="mono" title={'引用研报:' + (d.research || []).map(r => typeof r === 'string' ? r : (r && r.title) || '').join('、')} style={{ fontSize: 8, color: 'var(--ink-3)', flexShrink: 0 }}>📄{d.research.length}</span>}
                          {d.offChart && <span className="mono" style={{ fontSize: 8, color: 'var(--ink-3)', flexShrink: 0 }}>窗外</span>}
                        </div>
                        {/* 证据第二行:买卖关键证据(PIT 因子真值);观望也带,点行看详情卡完整链 */}
                        {(d.key_evidence || []).length > 0 && (
                          <div className="mono" style={{ fontSize: 8, color: 'var(--ink-3)', lineHeight: 1.45, marginTop: 1.5, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={(d.key_evidence || []).join(' | ')}>
                            <span style={{ color: col, opacity: 0.7 }}>据 </span>{(d.key_evidence || []).slice(0, 2).join(' · ')}
                          </div>
                        )}
                      </div>
                    );
                  })}
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
// RunDecCard:run 决策详情卡(shape 无 ev/price/size,绝不走 DecisionCard 的 dec.ev.* 旧路径)。
function RunDecCard({ dec }) {
  if (!dec) return null;
  const head = dec.side === 'buy' ? 'var(--zhu)' : dec.side === 'sell' ? 'var(--dai)' : 'var(--ink-2)';
  return (
    <div style={{ height: '100%', overflowY: 'auto', minHeight: 0 }}>
      {dec.offChart && <div className="mono" style={{ fontSize: 9, color: 'var(--ink-3)', padding: '6px 15px', borderBottom: '1px solid var(--line-soft)', background: 'rgba(28,24,20,0.03)' }}>⚠ 该决策日不在当前K线窗口内</div>}
      {/* 头(样式照抄 DecisionCard 外框) */}
      <div style={{ padding: '13px 15px', borderBottom: '1px solid var(--line)', background: dec.side === 'buy' ? 'rgba(168,57,45,0.04)' : 'rgba(74,107,92,0.06)' }}>
        <div style={{ display: 'flex', alignItems: 'baseline', gap: 9 }}>
          <span className="serif" style={{ fontSize: 16, fontWeight: 600, color: head }}>{dec.direction || '—'}</span>
          {dec.conf != null && <span className="mono" style={{ fontSize: 10.5, color: 'var(--ink-2)' }}>置信 {dec.conf}%</span>}
        </div>
        <div className="mono" style={{ fontSize: 10, color: 'var(--ink-3)', marginTop: 3 }}>{dec.asof ? 'as-of ' + String(dec.asof).slice(0, 10) : ''}{dec.model_name ? ' · ' + dec.model_name : ''}</div>
      </div>
      <div style={{ padding: '13px 15px', display: 'flex', flexDirection: 'column', gap: 12 }}>
        <EvidenceFields dec={dec} />
      </div>
    </div>
  );
}

// ───────── 证据链渲染(2026-07-11 三页重排:RunDecCard / JudgeCard 时间线共用)─────────
//   容忍两种行形状:run 映射行(conf)与落盘原始行(confidence);字段缺席即不渲染,绝不编造。
function EvidenceFields({ dec }) {
  if (!dec) return null;
  return (
    <React.Fragment>
      {dec.rationale && <div className="serif" style={{ fontSize: 12.5, color: 'var(--ink-1)', lineHeight: 1.6, textWrap: 'pretty' }}>「{dec.rationale}」</div>}
      {(dec.key_evidence || []).length > 0 && (
        <Field label="关键证据">
          <div style={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
            {dec.key_evidence.map((k, i) => <div key={i} className="mono" style={{ fontSize: 9.5, color: 'var(--ink-2)', lineHeight: 1.5 }}>· {k}</div>)}
          </div>
        </Field>
      )}
      {(dec.recipe_factors_vintage || []).length > 0 && (
        <Field label="配方因子 vintage IC(as-of·真OOS·不进信号)">
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4 }}>
            {dec.recipe_factors_vintage.map((f, i) => (
              <span key={f.id || f.name || i} className="mono" style={{ fontSize: 8.5, color: f.ic == null ? 'var(--ink-3)' : 'var(--yin)', border: '1px solid var(--line)', borderRadius: 4, padding: '1px 5px' }}>
                {f.name}{f.ic == null ? ' · 样本不足' : ` · IC@${String(f.asof).slice(0, 10)}=${f.ic} · n${f.n} · ${f.kind === 'tsic' ? '本票' : '截面'}`}
              </span>
            ))}
          </div>
        </Field>
      )}
      {/* P3:加权混合(因子进信号)。w>0 显混合方向/bias/因子分;w=0/缺席 显纯 LLM。 */}
      {(dec.w || 0) > 0 ? (
        <Field label="混合决策(因子进信号)">
          <div className="mono" style={{ fontSize: 9, color: 'var(--ink-2)', lineHeight: 1.5 }}>
            {dec.hybrid_direction || dec.direction}
            {dec.hybrid_bias != null ? ` · bias=${dec.hybrid_bias}` : ''}
            {dec.factor_score != null ? ` · 因子分=${dec.factor_score}` : ' · 无因子信号'}
            {` · w=${dec.w}`}
          </div>
        </Field>
      ) : (
        <Field label="混合决策"><div className="mono" style={{ fontSize: 9, color: 'var(--ink-3)' }}>纯 LLM(w=0)</div></Field>
      )}
      {(dec.recipe_factors || []).length > 0 && !(dec.recipe_factors_vintage || []).length && (
        <Field label="配方因子(供参考·未回测)">
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4 }}>
            {dec.recipe_factors.map((f, i) => <span key={i} className="mono" style={{ fontSize: 8.5, color: 'var(--ink-2)', border: '1px solid var(--line)', borderRadius: 4, padding: '1px 5px' }}>{(f && f.name) || String(f)}</span>)}
          </div>
        </Field>
      )}
      {(dec.card_names || []).length > 0 && (
        <Field label="命中经验卡">
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4 }}>
            {dec.card_names.map((c, i) => <span key={i} className="mono" style={{ fontSize: 8.5, color: 'var(--yin)', border: '1px solid var(--line)', borderRadius: 4, padding: '1px 5px' }}>⌘ {String(c)}</span>)}
          </div>
        </Field>
      )}
      {(dec.research || []).length > 0 && (
        <Field label="当日浮出研报(PIT 按日)">
          <div style={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
            {dec.research.map((t, i) => <div key={i} className="mono" style={{ fontSize: 9, color: 'var(--ink-2)', lineHeight: 1.5 }}>📄 {typeof t === 'string' ? t : (t && t.title) || ''}</div>)}
          </div>
        </Field>
      )}
      {dec.regime_asof && (
        <Field label="当日大盘(PIT 日产物)">
          <div className="mono" style={{ fontSize: 9, color: 'var(--ink-2)', lineHeight: 1.5 }}>{dec.regime_asof}</div>
        </Field>
      )}
      {dec.factors_std && (
        <Field label="当时因子读数(PIT 真值)">
          <div className="mono" style={{ fontSize: 8.5, color: 'var(--ink-3)', lineHeight: 1.7 }}>
            {Object.entries(dec.factors_std).map(([k, v]) => k + '=' + (typeof v === 'number' ? v.toFixed(3) : v)).join(' · ')}
          </div>
        </Field>
      )}
      <ReasoningChain reasoning={dec.reasoning} />
    </React.Fragment>
  );
}

// ───────── 落子卡 (证据详情) ─────────
function Field({ label, children }) {
  return (
    <div style={{ marginBottom: 2 }}>
      <div className="mono" style={{ fontSize: 9, color: 'var(--ink-3)', letterSpacing: '.1em', marginBottom: 3 }}>{label}</div>
      {children}
    </div>
  );
}
// 思维链复用块:DecisionCard(实时)与 DecisionHistory(回看)共用。无 reasoning(快模式)→ null。
function ReasoningChain({ reasoning }) {
  if (!reasoning) return null;
  return (
    <details style={{ marginTop: 6 }}>
      <summary className="mono" style={{ fontSize: 9.5, color: 'var(--yin)', cursor: 'pointer' }}>思维链 ▾(reasoner 真逐步推理 · 点开)</summary>
      <div className="mono" style={{ fontSize: 9.5, color: 'var(--ink-2)', marginTop: 4, lineHeight: 1.65, whiteSpace: 'pre-wrap', maxHeight: 220, overflowY: 'auto', background: 'rgba(28,24,20,0.035)', borderRadius: 6, padding: '7px 9px' }}>{reasoning}</div>
    </details>
  );
}
// 研判历史抽屉:只读后端 /seats/decisions(落盘的真研判/条件单),逆时序时间线 + 点开思维链。不依赖前端 GL。
function DecisionHistory({ code, open, onClose }) {
  const [rows, setRows] = useState([]);
  const [loading, setLoading] = useState(false);
  const [scope, setScope] = useState('code');   // 'code' 本票 / 'all' 全部
  const [openId, setOpenId] = useState(null);
  useEffect(() => {
    if (!open) return;
    const API = (window.GUANLAN_BACKEND || '');
    if (!API) { setRows([]); return; }
    setLoading(true);
    // 不靠后端按 code 精确匹配(落盘 code 经 normalize_code 可能带不同前/后缀,与桌面 symbol.meta.code 格式不一);
    // 全量拉回,本票时按「数字核」客户端过滤(300750 ↔ 300750.SZ ↔ SZ300750 皆匹配)。
    fetch(API + '/seats/decisions?limit=80').then(r => r.json()).then(j => {
      let ds = (j && j.decisions) || [];
      if (scope === 'code' && code) {
        const dig = (x) => String(x || '').replace(/\D/g, '');
        const cd = dig(code);
        ds = ds.filter(d => dig(d.code) === cd);
      }
      setRows(ds); setLoading(false);
    }).catch(() => { setRows([]); setLoading(false); });
  }, [open, code, scope]);
  if (!open) return null;
  const dirColor = (d) => d && /买/.test(d) ? 'var(--zhu)' : (d && /卖/.test(d) ? 'var(--dai)' : 'var(--ink-2)');
  return (
    <div onClick={onClose} style={{ position: 'fixed', inset: 0, zIndex: 60, background: 'rgba(20,16,12,0.32)', display: 'flex', justifyContent: 'flex-end' }}>
      <div onClick={(e) => e.stopPropagation()} className="paper-bg" style={{ width: 460, maxWidth: '92vw', height: '100%', background: 'var(--paper)', borderLeft: '1px solid var(--line)', boxShadow: '-8px 0 30px rgba(20,16,12,0.18)', display: 'flex', flexDirection: 'column', animation: 'fadeIn 0.18s ease' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '14px 16px', borderBottom: '1px solid var(--line)' }}>
          <span className="serif" style={{ fontSize: 15, fontWeight: 600, color: 'var(--ink-1)' }}>研判历史</span>
          <div style={{ display: 'flex', gap: 4, marginLeft: 6 }}>
            {[['code', '本票'], ['all', '全部']].map(([k, label]) => (
              <span key={k} onClick={() => setScope(k)} className="mono" style={{ fontSize: 9.5, padding: '2px 9px', borderRadius: 5, cursor: 'pointer', border: '1px solid ' + (scope === k ? 'var(--zhu-soft)' : 'var(--line)'), background: scope === k ? 'rgba(168,57,45,0.07)' : 'transparent', color: scope === k ? 'var(--yin)' : 'var(--ink-3)' }}>{label}</span>
            ))}
          </div>
          <span onClick={onClose} style={{ marginLeft: 'auto', fontSize: 16, cursor: 'pointer', color: 'var(--ink-3)' }}>✕</span>
        </div>
        <div style={{ flex: 1, overflowY: 'auto', padding: '10px 14px' }}>
          {loading && <div className="mono" style={{ fontSize: 10, color: 'var(--ink-3)' }}>读取中…</div>}
          {!loading && rows.length === 0 && <div className="mono" style={{ fontSize: 10.5, color: 'var(--ink-3)', padding: '20px 0', textAlign: 'center' }}>暂无研判记录</div>}
          {!loading && rows.map((r) => {
            const isOpen = openId === r.id;
            const isOrder = r.kind === 'order';
            const dir = isOrder ? r.side : r.direction;
            return (
              <div key={r.id} style={{ borderBottom: '1px solid var(--line-soft)', padding: '9px 2px' }}>
                <div onClick={() => setOpenId(isOpen ? null : r.id)} className="hover-row" style={{ display: 'flex', alignItems: 'center', gap: 8, cursor: 'pointer' }}>
                  <span className="mono" style={{ fontSize: 8.5, color: 'var(--ink-3)', width: 96, flexShrink: 0 }}>{String(r.ts || '').replace('T', ' ').slice(5, 16)}</span>
                  <span className="serif" style={{ fontSize: 12.5, fontWeight: 600, color: dirColor(dir) }}>{dir || '—'}</span>
                  <span className="mono" style={{ fontSize: 9, color: 'var(--ink-2)' }}>{r.name || r.code}</span>
                  <span className="mono" style={{ fontSize: 8.5, color: 'var(--ink-3)' }}>{r.strategy_name || ''}</span>
                  {r.confidence != null && <span className="mono" style={{ fontSize: 8.5, color: 'var(--ink-3)' }}>置信{r.confidence}</span>}
                  <span className="mono" style={{ marginLeft: 'auto', fontSize: 8, padding: '1px 5px', borderRadius: 4, border: '1px solid var(--line)', color: 'var(--ink-3)' }}>{isOrder ? '条件单' : '研判'}</span>
                </div>
                {isOpen && (
                  <div style={{ marginTop: 6, paddingLeft: 4 }}>
                    {r.rationale && <div className="serif" style={{ fontSize: 11, color: 'var(--ink-1)', lineHeight: 1.6 }}>{r.rationale}</div>}
                    {isOrder && (r.triggers || []).length > 0 && <div className="mono" style={{ fontSize: 9, color: 'var(--ink-2)', marginTop: 4 }}>触发:{(r.triggers || []).map(t => t.kind + t.op + t.value).join(r.logic === 'OR' ? ' 或 ' : ' 且 ')}{r.stop != null ? ' · 止损' + r.stop : ''}{r.take != null ? ' · 止盈' + r.take : ''}</div>}
                    {(r.key_evidence || []).length > 0 && <div style={{ marginTop: 4, display: 'flex', flexWrap: 'wrap', gap: 4 }}>{r.key_evidence.map((k, i) => <span key={i} className="mono" style={{ fontSize: 8.5, color: 'var(--ink-2)', border: '1px solid var(--line)', borderRadius: 4, padding: '1px 5px' }}>{k}</span>)}</div>}
                    {(r.recipe_factors || []).length > 0 && <div className="mono" style={{ fontSize: 8.5, color: 'var(--ink-3)', marginTop: 4 }}>配方因子(供参考·未回测):{(r.recipe_factors || []).map(f => f.name).join('、')}</div>}
                    <ReasoningChain reasoning={r.reasoning} />
                    <div className="mono" style={{ fontSize: 8, color: 'var(--ink-3)', marginTop: 4 }}>{r.model_name || ''}{r.asof ? ' · as-of ' + r.asof : ''}</div>
                  </div>
                )}
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}
function DecisionCard({ dec, symbol, mode }) {
  const [report, setReport] = useState(null);     // 本股深度研报状态(/report-progress)
  const [signal, setSignal] = useState(null);     // 今日因子(/watch/signal_pack,按 code)
  const [market, setMarket] = useState(null);     // 今日市况(/watch/market_status,仅实盘)
  const [decide, setDecide] = useState(null);     // ⑤ 席位 agent 真研判(/seats/decide,on-demand)
  const [deciding, setDeciding] = useState(false);
  const [histOpen, setHistOpen] = useState(false);     // ⑤+ 研判历史抽屉(读 /seats/decisions 落盘记录)
  const [agentMode, setAgentMode] = useState('deep');  // ⑤ 研判模式:deep=reasoner有思维链 / fast=chat快无思维链(用户偏好,跨卡保持,故不在下方 useEffect 重置)
  const [wEdit, setWEdit] = useState(null);            // P3:本卡就地调因子权重 w(null=用策略持久值;拖动后覆盖并回存策略,实盘定时/手动研判同源)
  const code = symbol && symbol.meta && symbol.meta.code;
  useEffect(() => {
    let alive = true; setReport(null); setSignal(null); setMarket(null); setDecide(null); setDeciding(false);
    if (code && window.lzFetchReportStatus) window.lzFetchReportStatus(code).then(r => { if (alive) setReport(r); });
    // ③a 实盘:今日 signal_pack(按 code);③b 复盘:按 (code, dec.date) 取真历史因子(价量真算 + 模型旁路)
    if (code) {
      if (mode === 'live') {
        if (window.lzFetchSignalRow) window.lzFetchSignalRow(code).then(s => { if (alive) setSignal(s); });
      } else if (dec && dec.date && window.lzFetchSeatFactors) {
        window.lzFetchSeatFactors(code, dec.date).then(s => { if (alive) setSignal(s); });
      }
    }
    // 当时·市场状态:实盘取今日真 regime(/watch/market_status);复盘历史 regime 无 PIT 源 → 留 mock
    if (mode === 'live' && window.lzFetchMarketStatus) window.lzFetchMarketStatus().then(m => { if (alive) setMarket(m); });
    return () => { alive = false; };
  }, [code, dec && dec.date, mode]);
  useEffect(() => { setWEdit(null); }, [dec && dec.seat]);   // 切换决策(换策略/换票)→ 清就地覆盖,回落该策略持久 w
  if (!dec) return (
    <div style={{ padding: 20, color: 'var(--ink-3)', fontFamily: 'var(--serif)', fontSize: 12.5, lineHeight: 1.7, textWrap: 'pretty' }}>
      点选 K 线上的 <b style={{ color: 'var(--zhu)' }}>▲</b> 买点 / <b style={{ color: 'var(--dai)' }}>▼</b> 卖点,或右侧决策流水中的任意一次落子 —— 此处摊开它背后的<b>证据链</b>:触发的量化因子、引用的研报观点、命中的经验卡,以及当时的市场状态。
    </div>
  );
  const s = lzSeatMeta(dec.seat);
  const ev = dec.ev;
  // 启发式骨架(scanSeat 价量规则)vs 真 LLM/真 run 决策:前者方向/置信/因子均为规则估算,需整卡显形「非 LLM」。
  const isHeuristic = !!ev && !dec.model_name && !dec.reasoning && !dec._isRun;
  const head = dec.warn ? 'var(--dai)' : dec.side === 'buy' ? 'var(--zhu)' : 'var(--dai)';
  const sizePct = (dec.size * 100).toFixed(0);
  return (
    <div style={{ height: '100%', overflowY: 'auto', minHeight: 0 }}>
      {/* 头 */}
      <div style={{ padding: '13px 15px', borderBottom: '1px solid var(--line)', background: dec.warn ? 'rgba(74,107,92,0.06)' : 'rgba(168,57,45,0.04)' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 9 }}>
          <span style={{ width: 26, height: 26, background: head, color: 'var(--paper)', fontFamily: 'var(--serif)', fontSize: 15, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>{sideGlyph(dec)}</span>
          <div>
            <div className="serif" style={{ fontSize: 15, fontWeight: 600, color: 'var(--ink)' }}>{sideCN(dec)} · {s.cn}</div>
            <div className="mono" style={{ fontSize: 10, color: 'var(--ink-3)', marginTop: 1 }}>{dec.date} · 价 {dec.price} · {s.en}</div>
          </div>
        </div>
        <div className="serif" style={{ fontSize: 12.5, color: 'var(--ink-1)', marginTop: 9, lineHeight: 1.6, textWrap: 'pretty' }}>「{dec.note}」</div>
      </div>
      <div style={{ padding: '13px 15px', display: 'flex', flexDirection: 'column', gap: 13 }}>
        {isHeuristic && (
          <div className="mono" style={{ fontSize: 9, color: 'var(--ink-3)', background: 'rgba(28,24,20,0.04)', borderRadius: 7, padding: '6px 10px', lineHeight: 1.5 }}>
            启发式骨架 · 非 LLM —— 方向 / 置信 / 因子均为 scanSeat 价量规则估算,非大模型研判;真 agent 研判见下方「席位 · agent 研判(真)」
          </div>
        )}
        {/* 决策参数 */}
        {!dec.warn && (
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '10px 14px' }}>
            <Field label="方向 / 置信度">
              <span className="serif" style={{ fontSize: 13, fontWeight: 600, color: head }}>{sideCN(dec)}</span>
              <span className="mono" style={{ fontSize: 11, color: 'var(--ink-2)', marginLeft: 6 }}>{(dec.conf * 100).toFixed(0)}%</span>
              <div style={{ height: 4, background: 'rgba(28,24,20,0.08)', borderRadius: 2, marginTop: 4, overflow: 'hidden' }}>
                <div style={{ width: (dec.conf * 100) + '%', height: '100%', background: head }} />
              </div>
            </Field>
            <Field label="仓位建议"><span className="mono" style={{ fontSize: 14, fontWeight: 600, color: 'var(--ink)' }}>{dec.side === 'buy' ? sizePct + '%' : '清仓'}</span></Field>
            {dec.side === 'buy' && <Field label="止损"><span className="mono" style={{ fontSize: 13, color: 'var(--dai)' }}>{dec.stop} <span style={{ fontSize: 9, color: 'var(--ink-3)' }}>({pct(dec.stop / dec.price - 1)})</span></span></Field>}
            {dec.side === 'buy' && <Field label="止盈"><span className="mono" style={{ fontSize: 13, color: 'var(--zhu)' }}>{dec.take} <span style={{ fontSize: 9, color: 'var(--ink-3)' }}>({pct(dec.take / dec.price - 1)})</span></span></Field>}
          </div>
        )}
        {dec.side === 'buy' && (
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '7px 11px', background: 'rgba(28,24,20,0.03)', borderRadius: 8 }}>
            <span className="mono" style={{ fontSize: 9.5, color: 'var(--ink-3)' }}>风险敞口</span>
            <span className="mono" style={{ fontSize: 11, color: 'var(--ink-2)' }}>单笔最大回撤约 <b style={{ color: 'var(--dai)' }}>{pct(dec.size * (dec.stop / dec.price - 1))}</b> · 盈亏比 ≈ {(((dec.take / dec.price - 1)) / (1 - dec.stop / dec.price)).toFixed(1)}:1</span>
          </div>
        )}
        <hr className="ink-rule" style={{ borderColor: 'var(--line-soft)' }} />
        {/* 触发因子(③a:实盘用 signal_pack 今日真因子;复盘仍 mock,待 ③b 真算)*/}
        <Field label="触发 · 量化因子">
          {(() => {
            // ③a 实盘:signal_pack 今日模型因子;③b 复盘:/seats/factors 真历史因子(价量 34 项 PIT,end=D 含当日收盘)。
            //   live → 模型因子(combo/FM/LGB/v4);backtest → 价量代表 5 项(真算);模型历史真值(FM/combo)待 phase2 回填。
            const useReal = !!signal;
            let cells, realTag = null, realTitle = '';
            if (mode === 'live' && signal) {
              cells = [['combo 分位', signal.combo], ['FM 分位', signal.fmPct], ['FM 簇', signal.fmCluster], ['LGB 排名', signal.lgbRank != null ? '#' + signal.lgbRank : null], ['v4', signal.v4]];
              realTag = '真·今日'; realTitle = '/watch/signal_pack · 今日' + (signal.date ? ' ' + signal.date : '');
            } else if (mode === 'backtest' && signal) {
              const f = signal.factors || {};
              const fp = (v) => (v == null ? null : pct(v));
              const fn2 = (v) => (v == null ? null : Math.round(v * 100) / 100);
              const md = String(signal.date || '').slice(5);
              if (signal.modelAvailable) {
                // 历史模型因子真值(FM backfill 缓存)+ 两项价量;LGB/v4 历史未重算 → 不入 cells
                cells = [['combo 分位', signal.combo], ['FM 分位', signal.fmPct], ['FM 簇', signal.fmCluster != null ? 'c' + signal.fmCluster : null], ['反转20', fp(f.rev_20)], ['RSI14', f.rsi_14 != null ? Math.round(f.rsi_14) : null]];
                realTag = (signal.lookahead ? '⚠真·' : '真·') + md;
                realTitle = '/seats/factors?date=' + (signal.date || '') + ' · FM/combo ' + (signal.lookahead ? 'W11 模型 look-ahead(D≤训练截止)' : 'W11 OOS·PIT 重算') + ' + 价量 PIT';
              } else {
                cells = [['反转20', fp(f.rev_20)], ['动量60', fp(f.mom_60)], ['RSI14', f.rsi_14 != null ? Math.round(f.rsi_14) : null], ['乖离20', fp(f.ma_diff_20)], ['量比', fn2(f.turnover_20)]];
                realTag = '真·' + md; realTitle = '/seats/factors?date=' + (signal.date || '') + ' · 价量 ≤D 含当日收盘 PIT 真算';
              }
            } else {
              // 无真 signal(实盘 signal_pack 无此票/后端未连;复盘窗口越界)→ 不展示 evidenceFor 合成值,一律占位「—」+ 下方诚实小字。
              cells = [['combo 分位', null], ['FM 分位', null], ['FM 簇', null], ['LGB 排名', null], ['v4', null]];
            }
            return (
              <div>
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, alignItems: 'center' }}>
                  {useReal && <span className="mono" title={realTitle} style={{ fontSize: 8.5, padding: '1px 6px', borderRadius: 4, border: '1px solid var(--zhu-soft)', color: 'var(--yin)' }}>{realTag}</span>}
                  {cells.map(([k, v], i) => (
                    <span key={i} className="mono" style={{ fontSize: 10, color: 'var(--ink-2)', border: '1px solid var(--line)', borderRadius: 6, padding: '3px 8px' }}>{k} <b style={{ color: 'var(--ink)' }}>{v == null || v === '' ? '—' : v}</b></span>
                  ))}
                </div>
                {!signal && <div className="mono" style={{ fontSize: 8.5, color: 'var(--ink-3)', marginTop: 4 }}>{mode === 'live' ? '今日因子未取到真值(signal_pack 无此票 / 后端未连)· 非模型值' : '复盘历史因子暂无真值(后端无数据 / 窗口越界)'}</div>}
                {mode === 'backtest' && signal && !signal.modelAvailable && <div className="mono" style={{ fontSize: 8.5, color: 'var(--ink-3)', marginTop: 4 }}>价量因子已 PIT 真算;模型因子(FM/combo/LGB/v4)历史真值待回填</div>}
                {mode === 'backtest' && signal && signal.modelAvailable && signal.lookahead && <div className="mono" style={{ fontSize: 8.5, color: 'var(--ink-3)', marginTop: 4 }}>⚠ FM/combo 为 W11 模型 look-ahead(D≤2026-04-15 训练见过);LGB/v4 未重算</div>}
                {mode === 'backtest' && signal && signal.modelAvailable && !signal.lookahead && <div className="mono" style={{ fontSize: 8.5, color: 'var(--ink-3)', marginTop: 4 }}>FM/combo = W11 OOS·PIT 重算;LGB/v4 历史未重算</div>}
              </div>
            );
          })()}
        </Field>
        {/* 研报(A+闭环):本股真深度研报状态(/report-progress)+ 席位 GL 引用的 research 素材 */}
        <Field label="引用 · 研报观点">
          {(() => {
            const code = symbol.meta.code, name = symbol.meta.name;
            const goChat = (extra) => window.GL && window.GL.go('../chat/观澜 · 交互原型.html', 'chat', Object.assign({ code, name }, extra || {}));
            const refs = window.lzSeatResearch ? window.lzSeatResearch(dec.seat) : [];
            return (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 7 }}>
                {report && report.exists ? (
                  <div onClick={() => goChat({ intent: 'report' })} title="去对话·研报模块读全文"
                    style={{ display: 'flex', alignItems: 'center', gap: 7, border: '1px solid var(--jin)', borderRadius: 8, background: 'rgba(138,111,63,0.06)', padding: '7px 10px', cursor: 'pointer' }}>
                    <span style={{ color: 'var(--jin)' }}>📄</span>
                    <div style={{ minWidth: 0, flex: 1 }}>
                      <div className="serif" style={{ fontSize: 11.5, color: 'var(--ink-1)' }}>{name} 深度研报 · 已生成</div>
                      <div className="mono" style={{ fontSize: 9, color: 'var(--ink-3)' }}>{report.done}/{report.total} agents{report.asof ? ' · ' + report.asof : ''} · 看全文 →</div>
                    </div>
                  </div>
                ) : report && report.running ? (
                  <div className="mono" style={{ fontSize: 10, color: 'var(--ink-3)' }}>研报生成中 {report.done}/{report.total}…</div>
                ) : (
                  <div onClick={() => goChat({ intent: 'report' })} className="serif" style={{ fontSize: 10.5, color: 'var(--ink-3)', cursor: 'pointer' }}>
                    本股暂无深度研报 · <span style={{ color: 'var(--yin)', borderBottom: '1px dashed var(--zhu-soft)' }}>去对话生成 →</span>
                  </div>
                )}
                {refs.length ? refs.map((r, i) => (
                  <div key={i} onClick={() => goChat({ researchId: r.id, intent: 'research' })} title="去对话·研报看此素材"
                    style={{ display: 'flex', gap: 7, fontSize: 11.5, color: 'var(--ink-1)', fontFamily: 'var(--serif)', lineHeight: 1.5, cursor: 'pointer' }}>
                    <span style={{ color: 'var(--jin)', flexShrink: 0 }}>§</span>
                    <span style={{ textWrap: 'pretty' }}>{r.title}{r.from ? ' · ' + r.from : ''}</span>
                  </div>
                )) : <div className="mono" style={{ fontSize: 9.5, color: 'var(--ink-3)' }}>该席位未引用研报素材</div>}
              </div>
            );
          })()}
        </Field>
        {/* 价量形态 · 确定性:scanSeat 真算(dec.geo)/ decide 后端真算(pa_features);非 LLM,几何常显 */}
        {(() => {
          const g = (decide && decide.pa_features) || dec.geo;
          if (!g || !g.bar_type) return null;
          const fv = (x) => (x == null ? '—' : x);
          const cells = [
            ['K线型态', g.bar_type], ['实体比', fv(g.body)], ['收盘位', fv(g.close_pos)],
            ['上影/下影', fv(g.upper_wick) + ' / ' + fv(g.lower_wick)], ['振幅÷ATR', fv(g.range_atr)],
            ['距EMA20', fv(g.ema20_rel)], ['突破', fv(g.breakout)],
            ['量比', g.vol_ratio == null ? '—' : g.vol_ratio + '×'], ['涨跌停', fv(g.limit)], ['跳空', fv(g.gap)],
          ];
          if (g.inside_streak) cells.push(['连续内含', g.inside_streak + ' 根']);
          if (g.follow) cells.push(['跟随', g.follow]);
          const stratObj = (window.lzStrategyGet && window.lzStrategyGet(dec.seat)) || {};
          const stratPa = !!stratObj.pa;
          return (
            <Field label="价量形态 · 确定性">
              <span className="mono" title="价量几何特征由价量数据确定性算出(非 LLM);PIT≤决策bar" style={{ fontSize: 8, padding: '1px 6px', borderRadius: 4, border: '1px solid var(--line)', color: 'var(--ink-3)', marginBottom: 6, display: 'inline-block' }}>确定性 · 非 LLM</span>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '4px 12px' }}>
                {cells.map(([k, val]) => (
                  <div key={k} style={{ display: 'flex', justifyContent: 'space-between', gap: 8 }}>
                    <span className="mono" style={{ fontSize: 9.5, color: 'var(--ink-3)' }}>{k}</span>
                    <span className="mono" style={{ fontSize: 10.5, color: 'var(--ink-1)' }}>{val}</span>
                  </div>
                ))}
              </div>
              {(g.recent || []).filter(Boolean).length > 0 && <div className="mono" style={{ fontSize: 9, color: 'var(--ink-3)', marginTop: 5 }}>近3根:{(g.recent || []).map(r => r || '—').join(' / ')}</div>}
              {decide && decide.pa_features && stratPa && <div className="mono" style={{ fontSize: 9, color: 'var(--ink-3)', marginTop: 4 }}>已注入本席方法论(可在校场编辑)</div>}
            </Field>
          );
        })()}
        {/* 经验卡(席位 GL 真卡:seat.refs→card,与校场/验证区同源;无则回退 evidenceFor mock)*/}
        <Field label="命中 · 经验卡">
          {(() => {
            const sc = window.lzSeatCard ? window.lzSeatCard(dec.seat) : null;
            const card = sc || (ev && ev.card) || { name: '—', hint: '' };
            const meta = sc
              ? [sc.verdict && ('验证 ' + sc.verdict), (sc.conf != null) && ('conf ' + sc.conf), sc.ic && ('IC ' + sc.ic)].filter(Boolean).join(' · ')
              : '';
            return (
              <div onClick={() => window.GL && window.GL.go('观澜 · 经验验证区.html', 'validation', { focusCardName: card.name })}
                title="反跳验证区 · 看这条经验的来源与验证"
                style={{ border: '1px solid var(--zhu-soft)', borderRadius: 9, background: 'rgba(168,57,45,0.04)', padding: '9px 11px', cursor: 'pointer' }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                  <div className="serif" style={{ fontSize: 12.5, fontWeight: 600, color: 'var(--yin)' }}>◈ {card.name}</div>
                  {sc && sc.real && <span className="mono" style={{ fontSize: 8.5, padding: '1px 6px', borderRadius: 4, border: '1px solid var(--zhu-soft)', color: 'var(--yin)' }}>真</span>}
                  {!sc && <span className="mono" title="该席位未引用 GL 真经验卡,以下为模板示意文案(非验证结论)" style={{ fontSize: 8, padding: '1px 6px', borderRadius: 4, border: '1px solid var(--line)', color: 'var(--ink-3)' }}>示意 · 未引用真卡</span>}
                  <span className="mono" style={{ marginLeft: 'auto', fontSize: 9, color: 'var(--ink-3)' }}>看来历 →</span>
                </div>
                <div className="serif" style={{ fontSize: 11, color: 'var(--ink-2)', marginTop: 4, lineHeight: 1.55, textWrap: 'pretty' }}>{card.hint}</div>
                {meta ? <div className="mono" style={{ fontSize: 9, color: 'var(--ink-3)', marginTop: 4 }}>{meta}{sc && !sc.real ? ' · GL 知识库' : ''}</div>
                  : (!sc && <div className="mono" style={{ fontSize: 9, color: 'var(--ink-3)', marginTop: 4 }}>模板示意文案 · 该席位未引用 GL 真经验卡</div>)}
              </div>
            );
          })()}
        </Field>
        {/* 市场状态(实盘:今日真 regime /watch/market_status;复盘:历史 regime 无 PIT 源 → mock + 诚实标)*/}
        <Field label="当时 · 市场状态">
          {(() => {
            const liveReal = mode === 'live' && market && market.regime;
            const mlReal = liveReal && market.mainline;
            // 实盘缺真市况 → 不回退 evidenceFor 合成值(显「—」+ 诚实小字);复盘用 ev 但下方标「示例值」。
            const regime = liveReal ? market.regime : (mode === 'live' ? null : (ev && ev.regime));
            const mainline = mlReal ? market.mainline : (mode === 'live' ? null : (ev && ev.mainline));
            return (
              <div>
                <div style={{ display: 'flex', gap: 14, alignItems: 'center' }}>
                  {liveReal && <span className="mono" title={'/watch/market_status · 今日' + (market.date ? ' ' + market.date : '')} style={{ fontSize: 8.5, padding: '1px 6px', borderRadius: 4, border: '1px solid var(--zhu-soft)', color: 'var(--yin)' }}>真·今日</span>}
                  <span className="mono" style={{ fontSize: 10.5, color: 'var(--ink-3)' }}>regime <b style={{ color: 'var(--ink-2)' }}>{regime == null || regime === '' ? '—' : regime}</b></span>
                  <span className="mono" style={{ fontSize: 10.5, color: 'var(--ink-3)' }}>主线 <b style={{ color: 'var(--ink-2)' }}>{mainline == null || mainline === '' ? '—' : mainline}</b></span>
                </div>
                {mode === 'backtest' && <div className="mono" style={{ fontSize: 8.5, color: 'var(--ink-3)', marginTop: 4 }}>当时 regime 历史无 PIT 源 · 示例值(market_status 仅当日快照)</div>}
                {mode === 'live' && !liveReal && <div className="mono" style={{ fontSize: 8.5, color: 'var(--ink-3)', marginTop: 4 }}>今日市况未接入(/watch/market_status 未返回)· 不显示合成值</div>}
                {mode === 'live' && liveReal && !mlReal && <div className="mono" style={{ fontSize: 8.5, color: 'var(--ink-3)', marginTop: 4 }}>主线上游为空 · 不回退合成值</div>}
              </div>
            );
          })()}
        </Field>
        {/* ⑤ 席位 agent 真研判(on-demand 真调 LLM,综合 因子+卡+研报+市况;区别于 K 线 scanSeat 启发式)*/}
        <Field label="席位 · agent 研判(真)">
          {(() => {
            const runDecide = () => {
              if (!window.lzSeatDecide || deciding) return;
              setDeciding(true); setDecide(null);
              // 第3期:喂该策略实例「自己配的」经验卡/研报/因子(配方),而非旧席位卡。配方因子只作 LLM 参考、不冒充回测。
              const rcp = window.lzRecipeForStrategy ? window.lzRecipeForStrategy(dec.seat) : { cards: [], research: [], factors: [] };
              const regimeNow = (mode === 'live' && market && market.regime) ? market.regime : (ev && ev.regime);
              const st = (window.lzStrategyGet && window.lzStrategyGet(dec.seat)) || s;
              window.lzSeatDecide({
                code: symbol.meta.code, name: symbol.meta.name, date: dec.date,
                seat_cn: s.cn, creed: s.creed, mode: agentMode,
                strategy_id: dec.seat, strategy_name: s.cn,
                card: rcp.cards[0] ? { name: rcp.cards[0].name, insight: rcp.cards[0].insight, verdict: rcp.cards[0].verdict, conf: rcp.cards[0].conf, ic: rcp.cards[0].ic } : null,
                cards: rcp.cards,
                recipe_factors: rcp.factors,
                research: rcp.research.map(r => ({ title: r.title, from: r.from || '', path: r.path || null })),   // 带 path → 后端读研报正文摘录(P1⑤)
                regime: regimeNow,
                pa: !!(st && st.pa),
                pa_method: (st && st.pa) ? (st.paMethod || window.LZ_PA_METHOD_DEFAULT || '') : '',
                w: (st && isFinite(+st.w)) ? Math.max(0, Math.min(1, +st.w)) : 0,   // P3:手动研判按本策略 w 混合(下方滑块就地可调);w=0 严格纯 LLM
              }).then(d => { setDecide(d); setDeciding(false); });
            };
            // 快/深双模式(用户偏好,跨卡保持):快=deepseek-chat 秒级·无思维链;深=deepseek-reasoner 十几秒·带真思维链。
            const modeBtn = (m, label, tip) => (
              <span onClick={(e) => { e.stopPropagation(); if (!deciding) setAgentMode(m); }} title={tip}
                style={{ fontSize: 9, padding: '2px 7px', borderRadius: 5, cursor: deciding ? 'default' : 'pointer', fontFamily: 'var(--mono)', border: '1px solid ' + (agentMode === m ? 'var(--zhu-soft)' : 'var(--line)'), background: agentMode === m ? 'rgba(168,57,45,0.07)' : 'transparent', color: agentMode === m ? 'var(--yin)' : 'var(--ink-3)', fontWeight: agentMode === m ? 600 : 400, opacity: deciding ? 0.45 : 1 }}>{label}</span>
            );
            const modeRow = (
              <div style={{ display: 'flex', alignItems: 'center', gap: 5, marginBottom: 7 }}>
                <span className="mono" style={{ fontSize: 8.5, color: 'var(--ink-3)' }}>模式</span>
                {modeBtn('fast', '快 · chat', 'deepseek-chat:几秒出方向,无思维链')}
                {modeBtn('deep', '深 · reasoner', 'deepseek-reasoner:十几秒,带真思维链(逐步推理)')}
                <span className="mono" style={{ fontSize: 8, color: 'var(--ink-3)', marginLeft: 2 }}>{agentMode === 'deep' ? '慢 · 有思维链' : '快 · 无思维链'}</span>
                <span onClick={(e) => { e.stopPropagation(); setHistOpen(true); }} className="mono" title="查看历次 agent 研判 / 条件单的真思维链(落盘)"
                  style={{ marginLeft: 'auto', fontSize: 9, padding: '2px 8px', borderRadius: 5, cursor: 'pointer', border: '1px solid var(--line)', color: 'var(--ink-3)' }}>研判历史 ⏱</span>
              </div>
            );
            // P3:因子权重 w 滑块——就地可调,改即回存本策略(与校场同一个 w);实盘定时/手动研判都按它混合。
            const wStrat = window.lzStrategyGet ? window.lzStrategyGet(dec.seat) : null;
            const wShown = (wEdit != null ? wEdit : ((wStrat && isFinite(+wStrat.w)) ? +wStrat.w : 0));
            const wSlider = wStrat ? (
              <div style={{ display: 'flex', alignItems: 'center', gap: 9, margin: '1px 0 9px', flexWrap: 'wrap' }}>
                <span className="mono" title="因子进信号的混合权重 w:0=纯 LLM(方向只由 agent 研判定);>0 按 (1−w)·LLM分 + w·vintage 因子 z 分混入决策方向(因子用 as-of 真 OOS,不看未来,非确定性回测)。改这里即回存到本策略,实盘定时/手动研判同源生效。"
                  style={{ fontSize: 8.5, color: 'var(--ink-3)', letterSpacing: '.04em', cursor: 'help' }}>权 · 因子进信号</span>
                <input type="range" min="0" max="1" step="0.05" value={wShown}
                  onChange={e => { const nw = Math.max(0, Math.min(1, +e.target.value)); setWEdit(nw); const st = window.lzStrategyGet && window.lzStrategyGet(dec.seat); if (st && window.lzStrategySave) window.lzStrategySave(Object.assign({}, st, { w: nw })); }}
                  style={{ width: 148, accentColor: 'var(--yin)', cursor: 'pointer' }} />
                <span className="mono" style={{ fontSize: 11, fontWeight: 700, color: (wShown > 0 ? 'var(--yin)' : 'var(--ink-3)'), minWidth: 44 }}>w={(+wShown).toFixed(2)}</span>
                <span className="mono" style={{ fontSize: 8, color: wShown > 0 ? 'var(--yin)' : 'var(--ink-3)' }}>{wShown > 0 ? '混合 · 因子进信号' : '纯 LLM'}</span>
              </div>
            ) : null;
            let body;
            if (deciding) body = (
              <div className="mono" style={{ fontSize: 10.5, color: 'var(--yin)', display: 'flex', alignItems: 'center', gap: 6 }}>
                <span style={{ width: 6, height: 6, borderRadius: '50%', background: 'var(--yin)', animation: 'pulse 1s ease-in-out infinite' }} />{agentMode === 'deep' ? 'agent 研判中…(deepseek-reasoner 真推理,十几秒)' : 'agent 研判中…(deepseek-chat 快研判,几秒)'}
              </div>
            );
            else if (!decide) body = (
              <div onClick={runDecide} title="真调 LLM:综合 量化因子 + 经验卡 + 研报 + 市况(仅用 ≤当日信息)研判这一笔"
                style={{ fontSize: 10.5, color: 'var(--yin)', cursor: 'pointer', border: '1px dashed var(--zhu-soft)', borderRadius: 8, padding: '7px 10px', display: 'inline-block', fontFamily: 'var(--serif)' }}>
                ▶ 真·agent 研判(综合 因子 + 卡 + 研报 + 市况)
              </div>
            );
            else if (decide.error) body = (
              <div className="mono" style={{ fontSize: 9.5, color: 'var(--ink-3)' }}>agent 研判失败:{decide.error} · <span onClick={runDecide} style={{ color: 'var(--yin)', cursor: 'pointer' }}>重试 →</span></div>
            );
            else {
              const dirColor = decide.direction && /买/.test(decide.direction) ? 'var(--zhu)' : (decide.direction && /卖/.test(decide.direction) ? 'var(--dai)' : 'var(--ink-2)');
              body = (
                <div style={{ border: '1px solid var(--zhu-soft)', borderRadius: 9, background: 'rgba(138,111,63,0.05)', padding: '9px 11px' }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                    <span className="serif" style={{ fontSize: 13.5, fontWeight: 600, color: dirColor }}>{decide.direction || '—'}</span>
                    {decide.confidence != null && <span className="mono" style={{ fontSize: 10.5, color: 'var(--ink-2)' }}>置信 {decide.confidence}</span>}
                    <span className="mono" title={'真·LLM · ' + (decide.model_name || '') + (decide.asof ? ' · as-of ' + decide.asof : '')} style={{ marginLeft: 'auto', fontSize: 8.5, padding: '1px 6px', borderRadius: 4, border: '1px solid var(--zhu-soft)', color: 'var(--yin)' }}>真·{(decide.model_name || 'agent').split('/').pop()}</span>
                  </div>
                  {(decide.w || 0) > 0 && (
                    <div className="mono" style={{ fontSize: 9, color: 'var(--ink-2)', marginTop: 6, padding: '4px 8px', border: '1px solid var(--zhu-soft)', borderRadius: 6, background: 'rgba(168,57,45,0.045)' }}>
                      混合方向 <b style={{ color: 'var(--yin)' }}>{decide.hybrid_direction || decide.direction}</b>
                      {decide.factor_score != null ? ' · 因子分 ' + decide.factor_score : ' · 无因子信号'}
                      {decide.hybrid_bias != null ? ' · bias ' + decide.hybrid_bias : ''} · w={decide.w}
                      <span style={{ color: 'var(--ink-3)' }}> · (1−w)·LLM + w·因子z(as-of OOS·非确定性回测)</span>
                    </div>
                  )}
                  <div className="serif" style={{ fontSize: 11.5, color: 'var(--ink-1)', marginTop: 5, lineHeight: 1.6, textWrap: 'pretty' }}>{decide.rationale}</div>
                  {decide.key_evidence && decide.key_evidence.length > 0 && (
                    <div style={{ marginTop: 5, display: 'flex', flexWrap: 'wrap', gap: 5 }}>
                      {decide.key_evidence.map((k, i) => <span key={i} className="mono" style={{ fontSize: 9, color: 'var(--ink-2)', border: '1px solid var(--line)', borderRadius: 5, padding: '2px 6px' }}>{k}</span>)}
                    </div>
                  )}
                  <ReasoningChain reasoning={decide.reasoning} />
                  <div className="mono" style={{ fontSize: 8.5, color: 'var(--ink-3)', marginTop: 5 }}>独立真 agent 研判({(decide.model_name || '').split('/').pop()}),与 K 线 scanSeat 启发式标记可能不同{decide.reasoning ? ' —— 这才是真·思考' : ' · 快模式无思维链,切「深」看逐步推理'}。<span onClick={runDecide} style={{ color: 'var(--yin)', cursor: 'pointer' }}> 重研判 →</span></div>
                </div>
              );
            }
            return (<div>{modeRow}{wSlider}{body}</div>);
          })()}
        </Field>
        <DecisionHistory code={symbol && symbol.meta && symbol.meta.code} open={histOpen} onClose={() => setHistOpen(false)} />
      </div>
    </div>
  );
}

// ───────── 研判卡(2026-07-11 三页重排:LiveDecideFlow + DecisionCard「席位·agent 研判」区 合一)─────────
//   「▶ 研判一次」真调 /seats/decide + 研判时间线(后端落盘全量,修旧「流水只留最新1条」名实不符);
//   行点开 = EvidenceFields 完整证据链。industry 传真值(修旧 runTimedDecide 恒空 bug)。
function JudgeCard({ code, name, industry, strat, regime }) {
  const [decide, setDecide] = useState(null);
  const [deciding, setDeciding] = useState(false);
  const [agentMode, setAgentMode] = useState('deep');
  const [rows, setRows] = useState(null);
  const [openId, setOpenId] = useState(null);
  const [histOpen, setHistOpen] = useState(false);
  const [bump, setBump] = useState(0);
  useEffect(() => {
    let alive = true; setDecide(null); setOpenId(null);
    if (window.lzFetchDecisionsTimeline) window.lzFetchDecisionsTimeline(code, 40)
      .then(ds => { if (alive) setRows(ds ? ds.filter(d => d.kind !== 'order') : null); });
    else setRows(null);
    return () => { alive = false; };
  }, [code, bump]);
  const runDecide = () => {
    if (!window.lzSeatDecide || deciding || !strat) return;
    setDeciding(true); setDecide(null);
    const rcp = window.lzRecipeForStrategy ? window.lzRecipeForStrategy(strat.id) : { cards: [], research: [], factors: [] };
    window.lzSeatDecide({
      code, name, date: new Date().toISOString().slice(0, 10),
      seat_cn: strat.name, creed: strat.creed || '', mode: agentMode,
      strategy_id: strat.id, strategy_name: strat.name,
      industry: industry || '',
      card: rcp.cards[0] ? { name: rcp.cards[0].name, insight: rcp.cards[0].insight, verdict: rcp.cards[0].verdict, conf: rcp.cards[0].conf, ic: rcp.cards[0].ic } : null,
      cards: rcp.cards, recipe_factors: rcp.factors,
      research: rcp.research.map(r => ({ title: r.title, from: r.from || '', path: r.path || null })),
      regime: regime || null,
      pa: !!strat.pa,
      pa_method: strat.pa ? (strat.paMethod || window.LZ_PA_METHOD_DEFAULT || '') : '',
      w: isFinite(+strat.w) ? Math.max(0, Math.min(1, +strat.w)) : 0,
    }).then(d => { setDecide(d); setDeciding(false); setBump(b => b + 1); });
  };
  const dirColor = (d) => d && /买/.test(d) ? 'var(--zhu)' : (d && /卖/.test(d) ? 'var(--dai)' : 'var(--ink-2)');
  const modeBtn = (m, label, tip) => (
    <span onClick={() => { if (!deciding) setAgentMode(m); }} title={tip}
      style={{ fontSize: 9, padding: '2px 7px', borderRadius: 5, cursor: deciding ? 'default' : 'pointer', fontFamily: 'var(--mono)', border: '1px solid ' + (agentMode === m ? 'var(--zhu-soft)' : 'var(--line)'), background: agentMode === m ? 'rgba(168,57,45,0.07)' : 'transparent', color: agentMode === m ? 'var(--yin)' : 'var(--ink-3)', fontWeight: agentMode === m ? 600 : 400, opacity: deciding ? 0.45 : 1 }}>{label}</span>
  );
  return (
    <div style={{ borderBottom: '1px solid var(--line)' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 6, padding: '10px 13px 7px' }}>
        <span className="serif" style={{ fontSize: 13, fontWeight: 600, color: 'var(--ink-1)' }}>研判</span>
        <span className="mono" style={{ fontSize: 8.5, padding: '1px 6px', borderRadius: 4, border: '1px solid var(--zhu-soft)', color: 'var(--yin)' }}>真 · LLM</span>
        {modeBtn('fast', '快', 'deepseek-chat:几秒出方向,无思维链')}
        {modeBtn('deep', '深', 'deepseek-reasoner:十几秒,带真思维链')}
        <span onClick={() => setHistOpen(true)} className="mono" title="历次研判/条件单全量(落盘)"
          style={{ marginLeft: 'auto', fontSize: 9, padding: '2px 8px', borderRadius: 5, cursor: 'pointer', border: '1px solid var(--line)', color: 'var(--ink-3)' }}>⏱ 全部</span>
      </div>
      <div style={{ padding: '0 13px 10px' }}>
        {deciding ? (
          <div className="mono" style={{ fontSize: 10.5, color: 'var(--yin)', display: 'flex', alignItems: 'center', gap: 6, padding: '6px 0' }}>
            <span style={{ width: 6, height: 6, borderRadius: '50%', background: 'var(--yin)', animation: 'pulse 1s ease-in-out infinite' }} />
            {agentMode === 'deep' ? '研判中…(reasoner 真推理,十几秒)' : '研判中…(chat 快研判,几秒)'}
          </div>
        ) : (
          <div onClick={runDecide} title="真调 LLM:综合 配方卡/因子/研报 + 今日市况(仅 ≤当日信息)研判当下"
            style={{ fontSize: 10.5, color: strat ? 'var(--yin)' : 'var(--ink-3)', cursor: strat ? 'pointer' : 'default', border: '1px dashed var(--zhu-soft)', borderRadius: 8, padding: '6px 10px', display: 'inline-block', fontFamily: 'var(--serif)' }}>
            ▶ 研判一次{strat ? ' · ' + strat.name : '(无策略,去「策略」页建)'}
          </div>
        )}
        {decide && decide.error && <div className="mono" style={{ fontSize: 9.5, color: 'var(--ink-3)', marginTop: 6 }}>研判失败:{decide.error} · <span onClick={runDecide} style={{ color: 'var(--yin)', cursor: 'pointer' }}>重试 →</span></div>}
        {decide && !decide.error && (
          <div style={{ border: '1px solid var(--zhu-soft)', borderRadius: 9, background: 'rgba(138,111,63,0.05)', padding: '9px 11px', marginTop: 8 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <span className="serif" style={{ fontSize: 13.5, fontWeight: 600, color: dirColor(decide.direction) }}>{decide.direction || '—'}</span>
              {decide.confidence != null && <span className="mono" style={{ fontSize: 10.5, color: 'var(--ink-2)' }}>置信 {decide.confidence}</span>}
              <span className="mono" style={{ marginLeft: 'auto', fontSize: 8.5, padding: '1px 6px', borderRadius: 4, border: '1px solid var(--zhu-soft)', color: 'var(--yin)' }}>真·{(decide.model_name || 'agent').split('/').pop()}</span>
            </div>
            {(decide.w || 0) > 0 && (
              <div className="mono" style={{ fontSize: 9, color: 'var(--ink-2)', marginTop: 6 }}>
                混合方向 <b style={{ color: 'var(--yin)' }}>{decide.hybrid_direction || decide.direction}</b>
                {decide.factor_score != null ? ' · 因子分 ' + decide.factor_score : ' · 无因子信号'}
                {decide.hybrid_bias != null ? ' · bias ' + decide.hybrid_bias : ''} · w={decide.w}
              </div>
            )}
            <div className="serif" style={{ fontSize: 11.5, color: 'var(--ink-1)', marginTop: 5, lineHeight: 1.6, textWrap: 'pretty' }}>{decide.rationale}</div>
            <ReasoningChain reasoning={decide.reasoning} />
          </div>
        )}
        {/* 研判时间线(后端落盘,含 watcher/手动;条件单在下方「决策留痕」)*/}
        <div style={{ marginTop: 9 }}>
          {rows === null && <div className="mono" style={{ fontSize: 9, color: 'var(--ink-3)' }}>时间线读取中 / 后端未连…</div>}
          {rows && rows.length === 0 && <div className="mono" style={{ fontSize: 9.5, color: 'var(--ink-3)' }}>本票尚无研判 —— 点上方「▶ 研判一次」,或开顶部「盯盘」由服务端盘中自动判</div>}
          {rows && rows.slice(0, 8).map(r => {
            const isOpen = openId === r.id;
            return (
              <div key={r.id} style={{ borderTop: '1px solid var(--line-soft)', padding: '6px 0' }}>
                <div onClick={() => setOpenId(isOpen ? null : r.id)} className="hover-row" style={{ display: 'flex', alignItems: 'center', gap: 7, cursor: 'pointer' }}>
                  <span className="mono" style={{ fontSize: 8.5, color: 'var(--ink-3)', width: 84, flexShrink: 0 }}>{String(r.ts || '').replace('T', ' ').slice(5, 16)}</span>
                  <span className="serif" style={{ fontSize: 12, fontWeight: 600, color: dirColor(r.direction) }}>{r.direction || '—'}</span>
                  {r.confidence != null && <span className="mono" style={{ fontSize: 8.5, color: 'var(--ink-3)' }}>置信{r.confidence}</span>}
                  <span className="mono" style={{ fontSize: 8.5, color: 'var(--ink-3)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{r.strategy_name || ''}</span>
                  {r.source === 'watcher' && <span className="mono" title="服务端盯盘自动研判" style={{ fontSize: 8, padding: '1px 5px', borderRadius: 4, border: '1px solid var(--line)', color: 'var(--ink-3)' }}>盯</span>}
                  <span className="mono" style={{ marginLeft: 'auto', fontSize: 8, color: 'var(--ink-3)' }}>{isOpen ? '▴' : '▾'}</span>
                </div>
                {isOpen && <div style={{ padding: '6px 2px 4px', display: 'flex', flexDirection: 'column', gap: 10 }}><EvidenceFields dec={r} /></div>}
              </div>
            );
          })}
        </div>
      </div>
      <DecisionHistory code={code} open={histOpen} onClose={() => setHistOpen(false)} />
    </div>
  );
}

// ───────── 决策留痕(2026-07-11:台账记账半边退役,只留只读时间线)─────────
//   按日分组:研判/条件单混排(后端 seats_decisions 落盘全量);纯展示,不记现金/持仓。
function DecisionTrail({ code }) {
  const [rows, setRows] = useState(null);
  useEffect(() => {
    let alive = true; setRows(null);
    if (window.lzFetchDecisionsTimeline) window.lzFetchDecisionsTimeline(code, 60).then(ds => { if (alive) setRows(ds); });
    return () => { alive = false; };
  }, [code]);
  const days = {};
  (rows || []).forEach(r => { const d = String(r.ts || '').slice(0, 10) || '—'; (days[d] = days[d] || []).push(r); });
  const keys = Object.keys(days).sort().reverse();
  const dirColor = (d) => d && /买/.test(d) ? 'var(--zhu)' : (d && /卖/.test(d) ? 'var(--dai)' : 'var(--ink-2)');
  return (
    <div style={{ borderBottom: '1px solid var(--line)' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 6, padding: '10px 13px 6px' }}>
        <span className="serif" style={{ fontSize: 13, fontWeight: 600, color: 'var(--ink-1)' }}>决策留痕</span>
        <span className="mono" title="只读时间线(后端落盘);记账/持仓管理已退役——你在手机下单,系统只留研判与信号的痕" style={{ fontSize: 8, padding: '1px 6px', borderRadius: 4, border: '1px solid var(--line)', color: 'var(--ink-3)' }}>只读 · 不记账</span>
      </div>
      <div style={{ padding: '0 13px 10px', maxHeight: 200, overflowY: 'auto' }}>
        {rows === null && <div className="mono" style={{ fontSize: 9, color: 'var(--ink-3)' }}>读取中 / 后端未连…</div>}
        {rows && keys.length === 0 && <div className="mono" style={{ fontSize: 9.5, color: 'var(--ink-3)' }}>本票暂无留痕</div>}
        {keys.map(d => (
          <div key={d} style={{ marginBottom: 4 }}>
            <div className="mono" style={{ fontSize: 8.5, color: 'var(--ink-3)', letterSpacing: '.06em', padding: '4px 0 2px' }}>{d}</div>
            {days[d].map(r => (
              <div key={r.id} style={{ display: 'flex', alignItems: 'center', gap: 7, padding: '2px 0' }}>
                <span className="mono" style={{ fontSize: 8.5, color: 'var(--ink-3)', width: 38, flexShrink: 0 }}>{String(r.ts || '').slice(11, 16)}</span>
                <span className="mono" style={{ fontSize: 8, padding: '0 5px', borderRadius: 4, border: '1px solid var(--line)', color: 'var(--ink-3)', flexShrink: 0 }}>{r.kind === 'order' ? '条件单' : '研判'}</span>
                <span className="serif" style={{ fontSize: 11, fontWeight: 600, color: dirColor(r.kind === 'order' ? r.side : r.direction) }}>{(r.kind === 'order' ? r.side : r.direction) || '—'}</span>
                <span className="mono" style={{ fontSize: 8.5, color: 'var(--ink-3)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{r.strategy_name || ''}</span>
              </div>
            ))}
          </div>
        ))}
      </div>
    </div>
  );
}

Object.assign(window, { MiniLine, MarketBar, MetricsStrip, SeatRail, LiveDecideFlow, DecisionCard, DecisionHistory, ReasoningChain, LedgerPanel, pct, plFmt,
  EvidenceFields, JudgeCard, DecisionTrail });   // 2026-07-11 三页重排新组件
