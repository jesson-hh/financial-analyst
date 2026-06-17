// 观澜 · 落子 — 舰队网格 (多标的同时盯盘)
const { useState: useStateF, useEffect: useEffectF } = React;

// 轻量迷你蜡烛 (含落子标记)
function MiniCandles({ symbol, active, w, h }) {
  const bars = symbol.bars;
  const n = bars.length;
  const start = Math.max(0, n - 46);
  const vis = []; for (let i = start; i < n; i++) vis.push(i);
  const nv = vis.length;
  const padT = 6, padB = 4, padR = 2, padL = 2;
  const plotW = w - padL - padR, plotH = h - padT - padB;
  let hi = -Infinity, lo = Infinity;
  vis.forEach(i => { hi = Math.max(hi, bars[i].h); lo = Math.min(lo, bars[i].l); });
  const pad = (hi - lo) * 0.06; hi += pad; lo -= pad;
  const cw = plotW / nv, bw = Math.max(1, cw * 0.62);
  const x = (i) => padL + (vis.indexOf(i) + 0.5) * cw;
  const y = (p) => padT + (hi - p) / (hi - lo || 1) * plotH;
  const ma20 = []; for (let i = 0; i < n; i++) ma20.push(window.lzSma(bars, 20, 'c', i));
  const maPath = vis.filter(i => ma20[i] != null).map((i, k) => (k ? 'L' : 'M') + x(i).toFixed(1) + ' ' + y(ma20[i]).toFixed(1)).join(' ');
  const marks = symbol.decisions.filter(d => active.includes(d.seat) && d.idx >= start && !d.warn);
  return (
    <svg width={w} height={h} style={{ display: 'block' }}>
      <path d={maPath} fill="none" stroke="var(--jin)" strokeWidth="0.8" opacity="0.7" />
      {vis.map(i => {
        const b = bars[i]; const up = b.c >= b.o; const col = up ? 'var(--zhu)' : 'var(--dai)';
        const cx = x(i); const yo = y(b.o), yc = y(b.c);
        return <g key={i}>
          <line x1={cx} x2={cx} y1={y(b.h)} y2={y(b.l)} stroke={col} strokeWidth="0.7" />
          <rect x={cx - bw / 2} y={Math.min(yo, yc)} width={bw} height={Math.max(0.8, Math.abs(yo - yc))} fill={col} />
        </g>;
      })}
      {marks.map((d, k) => {
        const buy = d.side === 'buy';
        return <text key={k} x={x(d.idx)} y={buy ? y(bars[d.idx].l) + 9 : y(bars[d.idx].h) - 4} fontSize="8" textAnchor="middle" fill={window.seatColor(d.seat)} opacity="0.4" style={{ fontWeight: 700 }}>{buy ? '▲' : '▼'}</text>;
      })}
      {marks.length > 0 && <text x={padL} y={padT + 1} fontSize="7" textAnchor="start" fill="var(--ink-3)" opacity="0.75" style={{ fontWeight: 500 }}>非LLM</text>}
    </svg>
  );
}

function FleetCard({ code, active, onPick, isActive }) {
  const realS = window.lzRealSymbolOf ? window.lzRealSymbolOf(code) : null;
  const S = realS || window.LZ_SYMBOLS[code];
  const isReal = !!realS;
  // 单 agent 口径:本票第一个策略的净值,无策略/无产物→诚实「—」(不造假数据)
  const strat = (window.lzStrategyForCode ? window.lzStrategyForCode(code) : [])[0] || null;
  const sid = strat && strat.id;
  const eq = (S.perSeat && sid && S.perSeat[sid]) ? S.perSeat[sid].eq : null;
  const cm = eq ? window.lzMetricsOf(eq, []) : null;
  // bench 取最后一个**非 null**(alignBench 头尾都可有 null 段:头=指数起点晚、尾=指数源滞后不 ffill);
  // 整体 null(真断连/样例)→「—」+「真指数未连接」;源滞后 → 真值 +「· 截至MM-DD」——两种降级可区分。
  let bench = null;
  if (S.bench) {
    for (let bi = S.bench.length - 1; bi >= 0; bi--) {
      if (S.bench[bi] != null) { bench = S.bench[bi] - 1; break; }
    }
  }
  const benchStale = bench != null && S.benchAsof && S.bars && S.bars.length
    && S.benchAsof < S.bars[S.bars.length - 1].date;
  // 今日信号: 最近 6 根内的落子(单 agent:只看本票策略)
  const recent = S.decisions.filter(d => d.seat === sid && d.idx >= S.bars.length - 6);
  const lastBuy = recent.filter(d => d.side === 'buy' && !d.warn).pop();
  const lastWarn = recent.filter(d => d.warn).pop();
  let sig = { t: '持有 · 观望', c: 'var(--ink-3)', bg: 'transparent' };
  if (lastWarn) sig = { t: '风控预警', c: 'var(--paper)', bg: 'var(--dai)' };
  else if (lastBuy) sig = { t: lastBuy.side === 'buy' ? '买入信号' : '卖出', c: 'var(--paper)', bg: 'var(--zhu)' };
  const last = S.bars[S.bars.length - 1];
  const chg = last.c / S.bars[S.bars.length - 2].c - 1;
  const [ref, sz] = window.useSize();
  return (
    <div onClick={() => onPick(code)} className="hover-row" style={{
      border: '1px solid ' + (isActive ? 'var(--yin)' : 'var(--line)'), borderRadius: 11, background: 'var(--paper)',
      padding: 12, cursor: 'pointer', display: 'flex', flexDirection: 'column', minHeight: 0,
      boxShadow: isActive ? '0 2px 14px rgba(168,57,45,0.12)' : 'none',
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        <div style={{ minWidth: 0, flex: 1 }}>
          <div className="serif" style={{ fontSize: 13.5, fontWeight: 600, color: 'var(--ink)', display: 'flex', alignItems: 'center', gap: 5 }}>
            {S.meta.name}
            <span className="mono" style={{ fontSize: 8.5, padding: '0 5px', borderRadius: 7,
              border: '1px solid ' + (isReal ? 'var(--yin)' : 'var(--line)'),
              color: isReal ? 'var(--yin)' : 'var(--ink-3)' }}>{isReal ? '真·日线' : '样例'}</span>
          </div>
          <div className="mono" style={{ fontSize: 9.5, color: 'var(--ink-3)' }}>{code} · {S.meta.industry}</div>
        </div>
        <div style={{ textAlign: 'right' }}>
          <div className="mono" style={{ fontSize: 13, fontWeight: 600, color: 'var(--ink)' }}>{last.c}</div>
          <div className="mono" style={{ fontSize: 10, color: chg >= 0 ? 'var(--zhu)' : 'var(--dai)' }}>{window.pct(chg)}</div>
        </div>
      </div>
      <div ref={ref} style={{ height: 64, margin: '9px 0' }}>
        {sz.w > 0 && <MiniCandles symbol={S} active={sid ? [sid] : []} w={sz.w} h={64} />}
      </div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        <span className="mono" style={{ fontSize: 9.5, padding: '2px 8px', borderRadius: 6, color: sig.c, background: sig.bg, border: sig.bg === 'transparent' ? '1px solid var(--line)' : 'none' }}>{sig.t}</span>
        <span className="mono" title="价量规则启发式(均线/量比/几何),非 LLM 研判;真 agent 研判见右栏「盯盘」列" style={{ fontSize: 8, padding: '1px 5px', borderRadius: 5, border: '1px dashed var(--line)', color: 'var(--ink-3)', whiteSpace: 'nowrap' }}>非LLM</span>
        {(window.lzPoolIsMonitored && window.lzPoolIsMonitored(code))
          ? <span className="mono" title="盯盘(校场已绑 agent)" style={{ fontSize: 8.5, padding: '2px 7px', borderRadius: 8, border: '1px solid var(--yin)', color: 'var(--paper)', background: 'var(--yin)' }}>● 盯盘</span>
          : <span className="mono" title="自选(校场未绑 agent;去校场绑 agent 即盯盘)" style={{ fontSize: 8.5, padding: '2px 7px', borderRadius: 8, border: '1px solid var(--line)', color: 'var(--ink-3)' }}>○ 自选</span>}
        <span style={{ flex: 1 }} />
        <span className="mono" title={cm ? 'scanSeat 价量启发式在真K线上的示意净值,非真实策略回测业绩;不可与基准并列对比' : undefined} style={{ fontSize: 10, color: 'var(--ink-3)' }}>{strat ? strat.name : '策略'} <b style={{ color: cm ? (cm.total >= 0 ? 'var(--zhu)' : 'var(--dai)') : 'var(--ink-3)' }}>{cm ? window.pct(cm.total) : '—'}</b>{cm && <span style={{ fontSize: 8, color: 'var(--ink-3)', opacity: 0.7, marginLeft: 3 }}>示意</span>}</span>
        <span className="mono" style={{ fontSize: 9, color: 'var(--ink-3)' }} title={bench == null ? '真指数未连接' : (benchStale ? '指数源更新滞后,非横盘' : undefined)}>基准 {bench == null ? '—' : window.pct(bench, 0) + (benchStale ? ' · 截至' + S.benchAsof.slice(5) : '')}</span>
      </div>
    </div>
  );
}

function FleetGrid({ active, onPick, activeCode }) {
  const [hydrated, setHydrated] = useStateF(false);
  useEffectF(function() {
    var alive = true;
    if (window.lzHydrateRealBars) window.lzHydrateRealBars().then(function() { if (alive) setHydrated(true); });
    else setHydrated(true);
    return function() { alive = false; };
  }, []);
  const codes = window.LZ_SYMBOL_META.map(m => m.code);
  return (
    <div style={{ height: '100%', overflowY: 'auto', minHeight: 0, padding: 16 }}>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 10, marginBottom: 12 }}>
        <span className="serif" style={{ fontSize: 14, fontWeight: 600 }}>舰队 · 多标的盯盘</span>
        <span className="mono" style={{ fontSize: 10, color: 'var(--ink-3)' }}>{codes.length} 只 · 一个 agent 巡店 · 点开深看</span>
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(280px, 1fr))', gap: 14 }}>
        {codes.map(c => <FleetCard key={c} code={c} active={active} onPick={onPick} isActive={c === activeCode} />)}
      </div>
    </div>
  );
}

// ───────── 舰队右栏:盯盘·自选列 ─────────
// 盯盘行 = 名/代码 + 最新真 LLM 研判 + agent 名 + 实时报价指示灯。
// 自选行 = 名/代码 + 只读徽章「○ 自选」,不展研判。
// 头部主开关「盯盘」控制 watchOn。点行 = 聚焦该股切单标(onPick)。
function FleetSignalList({ realDecs, monQuotes, onPick, activeCode, watchOn, onToggleWatch }) {
  const codes = window.LZ_SYMBOL_META.map(m => m.code);
  const today = new Date().toISOString().slice(0, 10);
  const nMon = (window.lzMonitoredCodes ? window.lzMonitoredCodes() : []).length;
  return (
    <div style={{ width: 344, flexShrink: 0, borderLeft: '1px solid var(--line)', display: 'flex', flexDirection: 'column', minHeight: 0, background: 'var(--paper)' }}>
      <div style={{ padding: '10px 14px', borderBottom: '1px solid var(--line)', flexShrink: 0 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <span className="serif" style={{ fontSize: 13, fontWeight: 600 }}>盯盘 · 自选</span>
          <span style={{ flex: 1 }} />
          <span onClick={() => onToggleWatch && onToggleWatch()}
            title="盯盘:盘中(有实时报价)按各 agent 的判别频率自动真研判盯盘票。页面驱动——关页面即停,无后端定时器。"
            className="mono" style={{ fontSize: 9, padding: '3px 9px', borderRadius: 10, cursor: 'pointer', whiteSpace: 'nowrap',
              border: '1px solid ' + (watchOn ? 'var(--yin)' : 'var(--line)'), color: watchOn ? 'var(--paper)' : 'var(--ink-3)', background: watchOn ? 'var(--yin)' : 'transparent' }}>
            {watchOn ? '● 盯盘中 · ' + nMon + ' 支' : '○ 开始盯盘'}
          </span>
        </div>
        <div className="mono" style={{ fontSize: 8, color: 'var(--ink-3)', marginTop: 4 }}>
          {watchOn ? '页面开着 + 盘中自动研判 · 关页面即停' : '在校场给票绑 agent = 盯盘;点「开始盯盘」启动'}
        </div>
      </div>
      <div style={{ flex: 1, overflowY: 'auto', minHeight: 0 }}>
        {codes.map(code => {
          const S = (window.lzRealSymbolOf && window.lzRealSymbolOf(code)) || window.LZ_SYMBOLS[code];
          const name = (S && S.meta && S.meta.name) || code;
          const mon = window.lzPoolIsMonitored && window.lzPoolIsMonitored(code);
          const agent = mon && window.lzMonitorAgentFor ? window.lzMonitorAgentFor(code) : null;
          const rds = (realDecs && realDecs[code]) || [];
          const rd = rds.length ? rds[rds.length - 1] : null;
          const dirCol = rd ? (rd.side === 'buy' ? 'var(--zhu)' : rd.side === 'sell' ? 'var(--dai)' : 'var(--ink-3)') : 'var(--ink-3)';
          const q = monQuotes && monQuotes[code];
          return (
            <div key={code} onClick={() => onPick(code)} className="hover-row" style={{ padding: '9px 14px', borderBottom: '1px solid var(--line-soft)', cursor: 'pointer', borderLeft: '2px solid ' + (code === activeCode ? 'var(--yin)' : 'transparent') }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 7 }}>
                <span className="serif" style={{ fontSize: 12, fontWeight: 600 }}>{name}</span>
                <span className="mono" style={{ fontSize: 9, color: 'var(--ink-3)' }}>{code}</span>
                <span style={{ flex: 1 }} />
                {mon
                  ? <span className="mono" title={agent ? ('盯盘 agent:' + agent.name) : '盯盘'} style={{ fontSize: 8, padding: '1px 6px', borderRadius: 8, border: '1px solid var(--yin)', color: 'var(--paper)', background: 'var(--yin)' }}>● 盯盘</span>
                  : <span className="mono" style={{ fontSize: 8, padding: '1px 6px', borderRadius: 8, border: '1px solid var(--line)', color: 'var(--ink-3)' }}>○ 自选</span>}
              </div>
              {mon && (
                <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginTop: 4 }}>
                  {rd
                    ? <React.Fragment>
                        <span className="serif" style={{ fontSize: 11.5, fontWeight: 600, color: dirCol }}>{rd.direction || (rd.side === 'buy' ? '买入' : rd.side === 'sell' ? '卖出' : '观望')}</span>
                        {rd.conf != null && <span className="mono" style={{ fontSize: 8.5, color: 'var(--ink-3)' }}>置信 {rd.conf}</span>}
                        <span className="mono" style={{ fontSize: 8, color: 'var(--ink-3)' }}>{String(rd.asof || rd.date || '').slice(5, 16)}</span>
                        <span className="mono" style={{ fontSize: 7.5, padding: '0 4px', borderRadius: 3, border: '1px solid var(--yin)', color: 'var(--yin)' }}>真·LLM</span>
                      </React.Fragment>
                    : <span className="mono" style={{ fontSize: 9, color: 'var(--ink-3)' }}>盯盘中 · 待研判</span>}
                  <span style={{ flex: 1 }} />
                  {agent && <span className="mono" style={{ fontSize: 8, color: 'var(--ink-3)', maxWidth: 90, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{agent.name}</span>}
                  {q && <span title={q.fresh ? '盘中(实时报价)' : '休市/无实时'} style={{ width: 6, height: 6, borderRadius: '50%', background: q.fresh ? 'var(--zhu)' : 'var(--line)', flexShrink: 0 }} />}
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

Object.assign(window, { FleetGrid, MiniCandles, FleetSignalList });
