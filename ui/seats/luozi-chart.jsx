// 观澜 · 落子 — 图表组件 (蜡烛图 / 成交量 / 均线 / 落子标记 / 持仓底色 / 收益曲线)
const { useRef, useState, useLayoutEffect, useEffect } = React;

// 容器尺寸测量
function useSize() {
  const ref = useRef(null);
  const [sz, setSz] = useState({ w: 0, h: 0 });
  useLayoutEffect(() => {
    if (!ref.current) return;
    const el = ref.current;
    const ro = new ResizeObserver(() => setSz({ w: el.clientWidth, h: el.clientHeight }));
    ro.observe(el);
    setSz({ w: el.clientWidth, h: el.clientHeight });
    return () => ro.disconnect();
  }, []);
  return [ref, sz];
}

const css = (v) => getComputedStyle(document.documentElement).getPropertyValue(v).trim();
function seatColor(id) {
  if (window.lzStrategyColor) { const c = window.lzStrategyColor(id); if (c) return c; }   // 第3期:策略色
  const s = (window.LZ_SEATS || []).find(x => x.id === id);
  return s ? s.color : 'var(--ink-2)';
}

// 均线
function maSeries(bars, period, end) {
  const out = [];
  for (let i = 0; i <= end; i++) out.push(window.lzSma(bars, period, 'c', i));
  return out;
}

// ───────── 蜡烛图 ─────────
function CandleChart({ bars, decisions, truedecs, activeSeats, selected, onSelect, revealTo, view, live, asOf, triggers }) {
  const [ref, { w, h }] = useSize();
  const [hover, setHover] = useState(null);
  const n = bars.length;
  let vEnd = view && view.end != null ? view.end : n - 1;
  let vStart = view && view.start != null ? view.start : 0;
  vEnd = Math.max(0, Math.min(vEnd, n - 1));            // 钳制:防 swap 瞬态越界 bars[i]
  vStart = Math.max(0, Math.min(vStart, vEnd));
  const vis = [];
  for (let i = vStart; i <= vEnd; i++) vis.push(i);
  const nv = vis.length;

  if (!w || !h) return <div ref={ref} style={{ width: '100%', height: '100%' }} />;

  const padR = 50, padT = 10, padB = 20, padL = 6;
  const plotW = w - padL - padR;
  const plotH = h - padT - padB;
  const priceH = plotH * 0.72, gap = plotH * 0.05, volH = plotH * 0.23;
  const priceTop = padT, volTop = padT + priceH + gap;

  let pHi = -Infinity, pLo = Infinity, vMax = 0;
  vis.forEach(i => { pHi = Math.max(pHi, bars[i].h); pLo = Math.min(pLo, bars[i].l); vMax = Math.max(vMax, bars[i].v); });
  const pPad = (pHi - pLo) * 0.06; pHi += pPad; pLo -= pPad;

  const cw = plotW / nv;
  const bw = Math.max(1.5, Math.min(cw * 0.64, 14));
  const xOf = (i) => padL + (vis.indexOf(i) + 0.5) * cw;
  const yP = (p) => priceTop + (pHi - p) / (pHi - pLo || 1) * priceH;
  const yV = (v) => volTop + volH - (v / (vMax || 1)) * volH;

  const ma5 = maSeries(bars, 5, vEnd), ma20 = maSeries(bars, 20, vEnd);
  const linePath = (arr) => vis.filter(i => arr[i] != null).map((i, k) => (k ? 'L' : 'M') + xOf(i).toFixed(1) + ' ' + yP(arr[i]).toFixed(1)).join(' ');

  // 持仓区间 (各启用席位 buy→sell, 取 size>0 的 P&L 席)
  const bands = [];
  activeSeats.forEach(sid => {
    if (sid === 'risk') return;
    const ds = decisions.filter(d => d.seat === sid && !d.warn).sort((a, b) => a.idx - b.idx);
    let entry = null;
    ds.forEach(d => {
      if (d.side === 'buy') entry = d.idx;
      else if (d.side === 'sell' && entry != null) { bands.push({ sid, a: entry, b: d.idx }); entry = null; }
    });
    if (entry != null) bands.push({ sid, a: entry, b: vEnd, open: true });
  });

  // 落子标记 (≤ revealTo)
  const rt = revealTo != null ? revealTo : vEnd;
  const marks = decisions.filter(d => activeSeats.includes(d.seat) && d.idx >= vStart && d.idx <= Math.min(vEnd, rt));
  // 真·思考标记(/seats/decide LLM,独立 truedecs;绝不与 scanSeat 决策/合议/净值 混)
  const truemarks = (truedecs || []).filter(d => activeSeats.includes(d.seat) && d.idx >= vStart && d.idx <= Math.min(vEnd, rt));
  const previewDim = truemarks.length ? 0.4 : 1;   // 有真·思考 → scanSeat 标记淡化为预览底图

  const gridYs = [0.0, 0.25, 0.5, 0.75, 1.0].map(t => pLo + (pHi - pLo) * t);

  return (
    <div ref={ref} style={{ width: '100%', height: '100%', position: 'relative' }}
      onMouseLeave={() => setHover(null)}>
      <svg width={w} height={h} style={{ display: 'block' }}
        onMouseMove={(e) => {
          const r = e.currentTarget.getBoundingClientRect();
          const x = e.clientX - r.left;
          const idx = vis[Math.max(0, Math.min(nv - 1, Math.floor((x - padL) / cw)))];
          setHover({ idx, my: e.clientY - r.top });
        }}>
        <defs>
          {/* 预警/触发 发光滤镜:模糊副本×2 垫在清晰图形下 → 柔光晕(色随图形 fill)*/}
          <filter id="lz-glow" x="-80%" y="-80%" width="260%" height="260%">
            <feGaussianBlur stdDeviation="2" result="b" />
            <feMerge><feMergeNode in="b" /><feMergeNode in="b" /><feMergeNode in="SourceGraphic" /></feMerge>
          </filter>
          {/* 落子徽章 钤印阴影 */}
          <filter id="lz-stamp" x="-50%" y="-50%" width="200%" height="200%">
            <feDropShadow dx="0" dy="0.6" stdDeviation="0.7" floodColor="rgba(28,24,20,0.4)" />
          </filter>
        </defs>
        {/* 持仓底色 */}
        {bands.map((bd, k) => {
          const x1 = padL + (vis.indexOf(bd.a)) * cw;
          const x2 = padL + (vis.indexOf(bd.b) + 1) * cw;
          if (vis.indexOf(bd.a) < 0) return null;
          return <rect key={'bd' + k} x={x1} y={priceTop} width={Math.max(0, x2 - x1)} height={priceH}
            fill={seatColor(bd.sid)} opacity={0.07} />;
        })}
        {/* 网格 + 价格轴 */}
        {gridYs.map((p, k) => (
          <g key={'g' + k}>
            <line x1={padL} x2={padL + plotW} y1={yP(p)} y2={yP(p)} stroke="var(--line-soft)" strokeDasharray="2 3" />
            <text x={w - padR + 6} y={yP(p) + 3} fontSize="9.5" fill="var(--ink-3)" fontFamily="var(--mono)">{p.toFixed(p > 100 ? 0 : 1)}</text>
          </g>
        ))}
        {/* 均线 */}
        <path d={linePath(ma20)} fill="none" stroke="var(--jin)" strokeWidth="1.1" opacity="0.85" />
        <path d={linePath(ma5)} fill="none" stroke="var(--ink-2)" strokeWidth="1" opacity="0.7" />
        {/* 蜡烛 + 量柱 */}
        {vis.map(i => {
          const b = bars[i]; const up = b.c >= b.o;
          const col = up ? 'var(--zhu)' : 'var(--dai)';
          const cx = xOf(i);
          const yo = yP(b.o), yc = yP(b.c), top = Math.min(yo, yc), bh = Math.max(1, Math.abs(yo - yc));
          const dim = (b.forming || b.cached) ? 1 : (i > rt ? 0.22 : 1);
          if (b.cached) {
            // ④-③ 缓存今日柱(实时源·待官方结算):**实心真柱**(相当于真实柱子)+ 金「今」标(区别已结算真日K)
            return (
              <g key={i} opacity={dim}>
                <line x1={cx} x2={cx} y1={yP(b.h)} y2={yP(b.l)} stroke={col} strokeWidth="1" />
                <rect x={cx - bw / 2} y={top} width={bw} height={bh} fill={col} />
                <rect x={cx - bw / 2} y={yV(b.v)} width={bw} height={volTop + volH - yV(b.v)} fill={col} opacity="0.5" />
                <text x={cx} y={yP(b.h) - 4} fontSize="8.5" textAnchor="middle" fill="var(--jin)" fontFamily="var(--mono)">今</text>
              </g>
            );
          }
          if (b.forming) {
            // ④-① 今日 forming 日K(实盘盘中·未收盘):空心虚线蜡烛 + 「今」标;纯展示,不入 scan/合议。
            return (
              <g key={i} opacity={dim}>
                <line x1={cx} x2={cx} y1={yP(b.h)} y2={yP(b.l)} stroke={col} strokeWidth="1" strokeDasharray="2 2" opacity="0.9" />
                <rect x={cx - bw / 2} y={top} width={bw} height={Math.max(3, bh)} fill="none" stroke={col} strokeWidth="1.1" strokeDasharray="2 1.5" />
                <rect x={cx - bw / 2} y={yV(b.v)} width={bw} height={volTop + volH - yV(b.v)} fill="none" stroke={col} strokeWidth="0.8" opacity="0.7" />
                <text x={cx} y={yP(b.h) - 4} fontSize="8.5" textAnchor="middle" fill="var(--yin)" fontFamily="var(--mono)">今</text>
              </g>
            );
          }
          if (b.h === b.l) {
            // 涨停/跌停一字封板:开=高=低=收、无振幅 → 渲染为**贯穿整格的实心横段**(相邻封板连成连续实线,
            //   不再像断续"缺数据"——封板期价格被锁死是最有信息量的 bar)。涨跌向取自较前一根收盘
            //   (封板内同价 → 沿用方向;首根对比开板前那根:涨停 prev 更低=朱、跌停 prev 更高=黛)。
            const prevC = i > 0 && bars[i - 1] ? bars[i - 1].c : b.o;
            const flatCol = b.c >= prevC ? 'var(--zhu)' : 'var(--dai)';
            const yf = yP(b.c);
            return (
              <g key={i} opacity={dim}>
                <rect x={cx - cw / 2} y={yf - 1.25} width={Math.max(cw, 2)} height={2.5} fill={flatCol} />
                <rect x={cx - bw / 2} y={yV(b.v)} width={bw} height={volTop + volH - yV(b.v)} fill={flatCol} opacity="0.5" />
              </g>
            );
          }
          return (
            <g key={i} opacity={dim}>
              <line x1={cx} x2={cx} y1={yP(b.h)} y2={yP(b.l)} stroke={col} strokeWidth="1" />
              <rect x={cx - bw / 2} y={top} width={bw} height={bh} fill={col} />
              <rect x={cx - bw / 2} y={yV(b.v)} width={bw} height={volTop + volH - yV(b.v)} fill={col} opacity="0.5" />
            </g>
          );
        })}
        {/* 启发式扫描标记(scanSeat·非 LLM)—— 有真·思考时淡化为预览底图 */}
        <g opacity={previewDim}>
        {marks.map((d, k) => {
          const cx = xOf(d.idx);
          const sel = selected && selected.key === d.key;
          const fresh = live && d.idx === rt;
          const col = seatColor(d.seat);
          const anim = fresh ? { animation: 'pop .5s cubic-bezier(.2,1.4,.4,1)' } : null;
          if (d.warn) {
            const y = yP(bars[d.idx].h) - 12;
            // 预警菱形:稍微发光(lz-glow);形/位辨识,不靠颜色
            return <g key={'m' + k} style={Object.assign({ cursor: 'pointer' }, anim)} onClick={() => onSelect && onSelect(d)}>
              {sel && <circle cx={cx} cy={y} r="9" fill="none" stroke="var(--ink-2)" strokeWidth="1.2" />}
              <text x={cx} y={y + 4} fontSize={fresh ? 14 : 12} textAnchor="middle" fill="var(--dai)" filter="url(#lz-glow)">◆</text>
            </g>;
          }
          const buy = d.side === 'buy';
          // 买卖标记:仿交易软件 —— 朱砂(B)/黛绿(S)圆角徽章 + 宣纸白字(秀气字重),钤印阴影;选中描环
          const bw2 = (sel || fresh) ? 16 : 14, bh2 = (sel || fresh) ? 15 : 13;
          const ry = buy ? yP(bars[d.idx].l) + 6 : yP(bars[d.idx].h) - 6 - bh2;
          return (
            <g key={'m' + k} style={Object.assign({ cursor: 'pointer' }, anim)} onClick={() => onSelect && onSelect(d)}>
              {sel && <rect x={cx - bw2 / 2 - 2} y={ry - 2} width={bw2 + 4} height={bh2 + 4} rx={4.5} fill="none" stroke="var(--ink-2)" strokeWidth="1" opacity="0.7" />}
              <rect x={cx - bw2 / 2} y={ry} width={bw2} height={bh2} rx={3} fill={buy ? 'var(--zhu)' : 'var(--dai)'} filter="url(#lz-stamp)" />
              <text x={cx} y={ry + bh2 / 2 + 3.2} textAnchor="middle" fill="var(--paper)" fontSize={(sel || fresh) ? 9.5 : 8.5} style={{ fontWeight: 600, fontFamily: 'var(--sans)', letterSpacing: '0.02em' }}>{buy ? 'B' : 'S'}</text>
            </g>
          );
        })}
        </g>
        {/* 真·思考标记(LLM /seats/decide·PIT)—— 金框 B/S,区别于启发式;仅展示,绝不入 scanSeat/合议/净值 */}
        {(truemarks || []).map((d, k) => {
          const cx = xOf(d.idx);
          const ttl = '真·思考 · ' + (d.direction || '') + (d.conf != null ? ' · 置信' + d.conf : '') + (d.rationale ? ' · ' + d.rationale : '');
          if (d.side === 'watch') {
            const cy = yP(bars[d.idx].h) - 11;
            return <g key={'tw' + k}><circle cx={cx} cy={cy} r="3.2" fill="none" stroke="var(--jin)" strokeWidth="1.5" /><title>{ttl}</title></g>;
          }
          const buy = d.side === 'buy';
          const bw2 = 14, bh2 = 13;
          const ry = buy ? yP(bars[d.idx].l) + 7 : yP(bars[d.idx].h) - 7 - bh2;
          return (
            <g key={'t' + k}>
              <rect x={cx - bw2 / 2 - 3} y={ry - 3} width={bw2 + 6} height={bh2 + 6} rx={5} fill="none" stroke="var(--jin)" strokeWidth="1.8" opacity="0.92" filter="url(#lz-glow)" />
              <rect x={cx - bw2 / 2} y={ry} width={bw2} height={bh2} rx={3} fill={buy ? 'var(--zhu)' : 'var(--dai)'} filter="url(#lz-stamp)" />
              <text x={cx} y={ry + bh2 / 2 + 3.2} textAnchor="middle" fill="var(--paper)" fontSize="8.5" style={{ fontWeight: 600, fontFamily: 'var(--sans)' }}>{buy ? 'B' : 'S'}</text>
              <title>{ttl}</title>
            </g>
          );
        })}
        {/* ④/2b 条件单触发落子:发光 B / S(glow = 触发/预警;到价那根 bar 上标点)*/}
        {(triggers || []).map((t, k) => {
          const day = (t.at || '').slice(0, 10);
          const intraday = bars.length > 0 && (bars[0].date || '').length > 10;
          let ti = -1;
          for (let i = 0; i < bars.length; i++) {
            const d = bars[i].date || '';
            if (d.slice(0, 10) !== day) continue;
            ti = i; if (!intraday || d >= t.at) break;
          }
          if (ti < 0 || ti < vStart || ti > vEnd) return null;
          const cx = xOf(ti);
          const buy = /买/.test(t.side) || t.side === 'buy';
          // 触发徽章:同款 B/S 徽章 + 细金环柔光(金晕,标识"发讯/触发",比原大金盘秀气)
          const tbw = 15, tbh = 14;
          const ty = buy ? yP(bars[ti].l) + 7 : yP(bars[ti].h) - 7 - tbh;
          return (
            <g key={'t' + k}>
              <rect x={cx - tbw / 2 - 2.5} y={ty - 2.5} width={tbw + 5} height={tbh + 5} rx={5} fill="none" stroke="var(--jin)" strokeWidth="1.3" opacity="0.9" filter="url(#lz-glow)" />
              <rect x={cx - tbw / 2} y={ty} width={tbw} height={tbh} rx={3} fill={buy ? 'var(--zhu)' : 'var(--dai)'} filter="url(#lz-stamp)" />
              <text x={cx} y={ty + tbh / 2 + 3.3} textAnchor="middle" fill="var(--paper)" fontSize={9.5} style={{ fontWeight: 600, fontFamily: 'var(--sans)' }}>{buy ? 'B' : 'S'}</text>
            </g>
          );
        })}
        {/* as-of 信息墙 (复盘回放:未来不可见) */}
        {asOf && asOf.on && (
          <g>
            <line x1={xOf(vEnd)} x2={xOf(vEnd)} y1={priceTop} y2={volTop + volH} stroke="var(--yin)" strokeWidth="1.2" opacity="0.8" />
            <rect x={Math.min(xOf(vEnd) + 4, w - padR - 150)} y={priceTop + 2} width="148" height="15" fill="var(--yin)" opacity="0.92" rx="2" />
            <text x={Math.min(xOf(vEnd) + 8, w - padR - 146)} y={priceTop + 13} fontSize="9" fill="var(--paper)" fontFamily="var(--mono)">as-of {asOf.date} · 无未来信息</text>
          </g>
        )}
        {/* 播放头 */}
        {live && rt >= vStart && rt <= vEnd && (
          <line x1={xOf(rt)} x2={xOf(rt)} y1={priceTop} y2={volTop + volH} stroke="var(--yin)" strokeWidth="1" strokeDasharray="3 2" opacity="0.7" />
        )}
        {/* 十字光标(竖=游标 K、横=光标价位) */}
        {hover != null && bars[hover.idx] && (
          <line x1={xOf(hover.idx)} x2={xOf(hover.idx)} y1={priceTop} y2={volTop + volH} stroke="var(--ink-3)" strokeWidth="0.6" opacity="0.5" />
        )}
        {hover != null && hover.my != null && (
          <line x1={padL} x2={padL + plotW} y1={Math.max(priceTop, Math.min(hover.my, volTop + volH))} y2={Math.max(priceTop, Math.min(hover.my, volTop + volH))} stroke="var(--ink-3)" strokeWidth="0.6" opacity="0.32" strokeDasharray="2 3" />
        )}
        {/* 量能标签 */}
        <text x={padL + 2} y={volTop - 3} fontSize="8.5" fill="var(--ink-3)" fontFamily="var(--mono)">VOL</text>
      </svg>
      {/* 浮窗 */}
      {hover != null && bars[hover.idx] && (() => {
        const b = bars[hover.idx]; const up = b.c >= b.o;
        // 浮窗贴在被悬停 K 线旁(光标右侧;近右边界→翻到左侧),纵向跟随光标 —— 不再钉在左/右上角
        const boxW = 142, boxH = 82, off = 14;
        const bx = xOf(hover.idx);
        let left = bx + off;
        if (left + boxW > w - 6) left = bx - off - boxW;
        if (left < 6) left = 6;
        let top = (hover.my != null ? hover.my : priceTop + 10) - boxH / 2;
        top = Math.max(4, Math.min(top, h - boxH - 4));
        return (
          <div style={{ position: 'absolute', top, left, width: 122, background: 'var(--paper)', border: '1px solid var(--line)', borderRadius: 7, padding: '6px 9px', pointerEvents: 'none', fontFamily: 'var(--mono)', fontSize: 10, color: 'var(--ink-2)', lineHeight: 1.7, whiteSpace: 'nowrap', boxShadow: '0 4px 14px rgba(28,24,20,0.16)' }}>
            <div style={{ color: 'var(--ink-3)' }}>{b.date}</div>
            <div>开 {b.o} 收 <b className={up ? 'up' : 'down'}>{b.c}</b></div>
            <div>高 {b.h} 低 {b.l}</div>
            <div>量 {b.v.toFixed(2)}</div>
            {b.forming && <div style={{ color: 'var(--yin)', fontSize: 9 }}>实时·盘中未收</div>}
            {b.cached && <div style={{ color: 'var(--jin)', fontSize: 9 }}>实时源·待官方结算</div>}
          </div>
        );
      })()}
      {/* 图例 */}
      <div style={{ position: 'absolute', top: 6, left: 8, display: 'flex', gap: 12, fontFamily: 'var(--mono)', fontSize: 9.5, color: 'var(--ink-3)', pointerEvents: 'none' }}>
        <span><span style={{ color: 'var(--ink-2)' }}>━</span> MA5</span>
        <span><span style={{ color: 'var(--jin)' }}>━</span> MA20</span>
        {truemarks.length > 0 && <span style={{ marginLeft: 4 }}><span style={{ color: 'var(--jin)' }}>▣</span> 真·思考(LLM)<span style={{ opacity: 0.5, marginLeft: 5 }}>· 淡=启发式预览</span></span>}
      </div>
    </div>
  );
}

// ───────── 收益曲线 ─────────
function EquityChart({ lines, revealTo, len }) {
  const [ref, { w, h }] = useSize();
  if (!w || !h) return <div ref={ref} style={{ width: '100%', height: '100%' }} />;
  const padR = 44, padT = 12, padB = 16, padL = 6;
  const plotW = w - padL - padR, plotH = h - padT - padB;
  const n = len;
  const rt = revealTo != null ? revealTo : n - 1;
  let lo = Infinity, hi = -Infinity;
  // 真基准对齐数组开头可有 null 段(指数起点晚于本票首日)→ 比例尺/路径一律跳过非有限值
  lines.forEach(L => L.eq.forEach((v, i) => { if (i <= rt && Number.isFinite(v)) { lo = Math.min(lo, v); hi = Math.max(hi, v); } }));
  lo = Math.min(lo, 1); hi = Math.max(hi, 1);
  const pad = (hi - lo) * 0.12 || 0.02; hi += pad; lo -= pad;
  const x = (i) => padL + (i / (n - 1)) * plotW;
  const y = (v) => padT + (hi - v) / (hi - lo || 1) * plotH;
  const path = (eq) => {
    let d = '', pen = false;
    eq.slice(0, rt + 1).forEach((v, i) => {
      if (!Number.isFinite(v)) { pen = false; return; }       // null 段:抬笔不画,绝不补假点
      d += (pen ? 'L' : 'M') + x(i).toFixed(1) + ' ' + y(v).toFixed(1) + ' ';
      pen = true;
    });
    return d.trim();
  };
  const ticks = [lo + (hi - lo) * 0.15, 1, lo + (hi - lo) * 0.85];

  return (
    <div ref={ref} style={{ width: '100%', height: '100%', position: 'relative' }}>
      <svg width={w} height={h} style={{ display: 'block' }}>
        <line x1={padL} x2={padL + plotW} y1={y(1)} y2={y(1)} stroke="var(--line)" strokeDasharray="2 3" />
        {[lo + (hi - lo) * 0.15, lo + (hi - lo) * 0.85].map((v, k) => (
          <text key={k} x={w - padR + 6} y={y(v) + 3} fontSize="9" fill="var(--ink-3)" fontFamily="var(--mono)">{((v - 1) * 100 >= 0 ? '+' : '') + ((v - 1) * 100).toFixed(0) + '%'}</text>
        ))}
        <text x={w - padR + 6} y={y(1) + 3} fontSize="9" fill="var(--ink-3)" fontFamily="var(--mono)">0</text>
        {lines.map((L, k) => (
          <g key={k}>
            {L.fill && <path d={path(L.eq) + ' L' + x(rt) + ' ' + y(lo) + ' L' + x(0) + ' ' + y(lo) + ' Z'} fill={L.color} opacity="0.08" />}
            <path d={path(L.eq)} fill="none" stroke={L.color} strokeWidth={L.width || 1.4} strokeDasharray={L.dash || 'none'} opacity={L.dim ? 0.5 : 1} />
            {!L.dim && rt >= 0 && Number.isFinite(L.eq[rt]) && Number.isFinite(y(L.eq[rt])) && <circle cx={x(rt)} cy={y(L.eq[rt])} r="2.6" fill={L.color} />}
          </g>
        ))}
      </svg>
    </div>
  );
}

Object.assign(window, { useSize, CandleChart, EquityChart, seatColor });
