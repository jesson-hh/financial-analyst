// 观澜 · 量化研究工作台 — 独立页 (直连 /factor/* REST, 无 mock)
// 视觉沿用设计稿 (暖墨色 + 衬线/等宽), 但控件驱动: 对话/agent 留在主 app。

const { useState, useMemo, useRef, useEffect, useCallback } = React;

// ═════════════════════════ 直连 REST 数据层 ═════════════════════════
const API = window.GUANLAN_BACKEND || '';

async function q(path, opts) {
  const res = await fetch(API + path, opts);
  let body = null;
  try { body = await res.json(); } catch (e) { body = null; }
  if (!res.ok && (!body || body.error)) {
    throw new Error((body && body.error) || ('HTTP ' + res.status));
  }
  return body;
}
const getJSON = (path) => q(path);
const postJSON = (path, payload) =>
  q(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });

// useAsync: 手动触发的异步请求 → {data, loading, error, run, reset}
function useAsync() {
  const [state, setState] = useState({ data: null, loading: false, error: null });
  const run = useCallback(async (fn) => {
    setState({ data: null, loading: true, error: null });
    try { const data = await fn(); setState({ data, loading: false, error: null }); return data; }
    catch (e) { setState({ data: null, loading: false, error: e.message || String(e) }); }
  }, []);
  const reset = useCallback(() => setState({ data: null, loading: false, error: null }), []);
  return { ...state, run, reset };
}

// null/undefined/NaN → 「—」; 数字按位
const n2 = (v, d = 2) => (v === null || v === undefined || (typeof v === 'number' && isNaN(v))) ? '—' : (typeof v === 'number' ? v.toFixed(d) : v);
const pct = (v, d = 2) => (v === null || v === undefined || (typeof v === 'number' && isNaN(v))) ? '—' : (v * 100).toFixed(d) + '%';

const POOLS = ['快测', 'csi300', 'csi500', 'csi800', 'all'];
const POOL_DEFAULT = 'csi300_active';   // csi300 交互快档
const poolParam = (p) => (p === '快测' ? 'csi_fast' : (p === 'csi300' ? POOL_DEFAULT : p));

// ═════════════════════════ 三态小组件 ═════════════════════════
function Loading({ label = '加载中…' }) {
  const [secs, setSecs] = useState(0);
  useEffect(() => {
    const t0 = Date.now();
    const id = setInterval(() => setSecs(Math.floor((Date.now() - t0) / 1000)), 250);
    return () => clearInterval(id);
  }, []);
  return (
    <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 12, padding: 36, animation: 'fadeIn 0.3s ease' }}>
      <div style={{
        width: 26, height: 26, borderRadius: '50%', boxSizing: 'border-box',
        border: '2.5px solid var(--line)', borderTopColor: 'var(--ink-1)',
        animation: 'spin 0.8s linear infinite',
      }} />
      <div className="mono" style={{ fontSize: 12, color: 'var(--ink-3)', textAlign: 'center' }}>
        {label}{secs >= 1 ? ' · ' + secs + 's' : ''}
      </div>
    </div>
  );
}
function Empty({ label = '暂无数据' }) {
  return <div className="serif" style={{ padding: 24, fontSize: 13, color: 'var(--ink-3)', textAlign: 'center' }}>{label}</div>;
}
function ErrorBox({ error }) {
  return <div className="mono" style={{ padding: 16, fontSize: 12, color: 'var(--yin)', border: '1px solid var(--line)', background: 'rgba(28,24,20,0.03)' }}>✗ {error}</div>;
}

// ═════════════════════════ 复用基元 (port from draft) ═════════════════════════
function Pill({ label, children }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
      <span className="mono" style={{ fontSize: 9.5, color: 'var(--ink-3)', letterSpacing: '0.16em' }}>{label}</span>
      {children}
    </div>
  );
}

function Segmented({ value, onChange, options }) {
  return (
    <div style={{ display: 'inline-flex', border: '1px solid var(--line)' }}>
      {options.map((o, i) => (
        <button key={o.value} onClick={() => onChange(o.value)} style={{
          padding: '5px 11px', border: 'none',
          background: value === o.value ? 'var(--ink)' : 'transparent',
          color: value === o.value ? 'var(--paper)' : 'var(--ink-2)',
          fontFamily: 'var(--serif)', fontSize: 11.5, cursor: 'pointer',
          whiteSpace: 'nowrap',
          borderRight: i < options.length - 1 ? '1px solid var(--line)' : 'none',
        }}>{o.label}</button>
      ))}
    </div>
  );
}

function Kpi({ label, value, hint, dir, last }) {
  return (
    <div style={{ padding: '12px 14px', borderRight: last ? 'none' : '1px solid var(--line-soft)', background: 'rgba(255,255,255,0.4)' }}>
      <div className="mono" style={{ fontSize: 9, color: 'var(--ink-3)', letterSpacing: '0.16em' }}>{label}</div>
      <div className={'mono ' + (dir === 'up' ? 'up' : dir === 'down' ? 'down' : '')}
        style={{ fontSize: 17, fontWeight: 500, color: dir ? undefined : 'var(--ink)', marginTop: 4 }}>{value}</div>
      {hint && <div className="serif" style={{ fontSize: 10, color: 'var(--ink-2)', marginTop: 2 }}>{hint}</div>}
    </div>
  );
}

function Panel({ title, right, children }) {
  return (
    <div style={{ border: '1px solid var(--line)', background: 'var(--paper)' }}>
      <div style={{ padding: '10px 14px 9px', borderBottom: '1px solid var(--line-soft)', display: 'flex', alignItems: 'center', gap: 8 }}>
        <span className="serif" style={{ fontSize: 12.5, color: 'var(--ink)', fontWeight: 500 }}>{title}</span>
        <span style={{ flex: 1 }} />
        {right}
      </div>
      <div style={{ padding: '14px 16px 16px' }}>{children}</div>
    </div>
  );
}

// chart tooltip 浮层 (绝对定位 HTML overlay)
function ChartTip({ x, y, w, h, children, tipW = 168, tipH = 96 }) {
  let left = x + 14;
  let top = y - tipH - 8;
  if (left + tipW > w - 4) left = x - tipW - 14;
  if (left < 4) left = 4;
  if (top < 4) top = y + 14;
  if (top + tipH > h - 4) top = h - tipH - 4;
  return (
    <div style={{
      position: 'absolute', left, top, pointerEvents: 'none',
      background: 'var(--paper)', border: '1px solid var(--ink)',
      padding: '6px 9px', fontFamily: 'var(--sans)', fontSize: 11, color: 'var(--ink)',
      boxShadow: '3px 3px 0 -1px var(--paper-3)', zIndex: 8, minWidth: tipW - 24,
    }}>{children}</div>
  );
}

// ═════════════════════════ 图表 (port + 适配真实数据) ═════════════════════════

// IC 序列柱状图. series=IC 数值数组, dates=对应日期 (可空)。
function ICChart({ series, dates }) {
  const w = 360, h = 180, pad = { l: 30, r: 8, t: 14, b: 22 };
  const wrapperRef = useRef(null);
  const [hover, setHover] = useState(null);
  if (!series || !series.length) return <Empty label="无 IC 序列" />;
  const max = Math.max(...series.map(v => Math.abs(v)), 0.07);
  const mid = pad.t + (h - pad.t - pad.b) / 2;

  const onMove = (e) => {
    const rect = wrapperRef.current.getBoundingClientRect();
    const px = e.clientX - rect.left, py = e.clientY - rect.top;
    const svgX = (px / rect.width) * w;
    if (svgX < pad.l - 2 || svgX > w - pad.r + 2) { setHover(null); return; }
    const t = (svgX - pad.l) / (w - pad.l - pad.r);
    const idx = Math.max(0, Math.min(series.length - 1, Math.floor(t * series.length)));
    setHover({ idx, px, py, rectW: rect.width, rectH: rect.height });
  };
  const lbl = (i) => (dates && dates[i]) ? String(dates[i]).slice(2, 10) : ('#' + (i + 1));
  const tickIdxs = series.length ? [0, Math.floor(series.length / 2), series.length - 1] : [];

  return (
    <div ref={wrapperRef} style={{ position: 'relative' }} onMouseMove={onMove} onMouseLeave={() => setHover(null)}>
      <svg viewBox={`0 0 ${w} ${h}`} style={{ width: '100%', height: 190, display: 'block', cursor: 'crosshair' }}>
        <line x1={pad.l} x2={w - pad.r} y1={mid} y2={mid} stroke="var(--ink-3)" strokeWidth="1" />
        {[-0.06, -0.03, 0.03, 0.06].map((v) => {
          const y = mid - (v / max) * ((h - pad.t - pad.b) / 2);
          return (
            <g key={v}>
              <line x1={pad.l} x2={w - pad.r} y1={y} y2={y} stroke="var(--line-soft)" strokeDasharray="2 3" />
              <text x={pad.l - 4} y={y + 3} fontSize="9" textAnchor="end" fontFamily="var(--mono)" fill="var(--ink-3)">{(v * 100).toFixed(0) + '%'}</text>
            </g>
          );
        })}
        {series.map((v, i) => {
          const x = pad.l + i * ((w - pad.l - pad.r) / series.length);
          const bw = (w - pad.l - pad.r) / series.length - 1;
          const barH = (Math.abs(v) / max) * ((h - pad.t - pad.b) / 2);
          const y = v >= 0 ? mid - barH : mid;
          const sig = Math.abs(v) > 0.04;
          const isHover = hover && hover.idx === i;
          return (
            <rect key={i} x={x} y={y} width={bw} height={barH}
              fill={v >= 0 ? (sig ? 'var(--zhu)' : 'var(--zhu-soft)') : (sig ? 'var(--dai)' : 'var(--dai-soft)')}
              opacity={isHover ? 1 : (sig ? 0.95 : 0.55)}
              stroke={isHover ? 'var(--ink)' : 'none'} strokeWidth={isHover ? 0.8 : 0} />
          );
        })}
        {tickIdxs.map((di, k, arr) => {
          const x = pad.l + (w - pad.l - pad.r) * (k / (arr.length - 1));
          return <text key={di} x={x} y={h - 6} fontSize="9" fontFamily="var(--mono)" fill="var(--ink-3)" textAnchor="middle">{lbl(di)}</text>;
        })}
      </svg>
      {hover && (() => {
        const v = series[hover.idx];
        return (
          <ChartTip x={hover.px} y={hover.py} w={hover.rectW} h={hover.rectH} tipW={160} tipH={64}>
            <div className="mono" style={{ fontSize: 9, color: 'var(--ink-3)', letterSpacing: '0.16em', marginBottom: 4 }}>{lbl(hover.idx)} · 第 {hover.idx + 1}/{series.length} 期</div>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline' }}>
              <span className="serif" style={{ fontSize: 11, color: 'var(--ink-1)' }}>IC</span>
              <span className={'mono ' + (v >= 0 ? 'up' : 'down')} style={{ fontSize: 14, fontWeight: 500 }}>{v >= 0 ? '+' : ''}{(v * 100).toFixed(2)}%</span>
            </div>
          </ChartTip>
        );
      })()}
    </div>
  );
}

// 净值曲线. series=组合净值数组; benchmark=基准净值数组 (可空); dates 可空。
function EquityChart({ series, dates, benchmark }) {
  const w = 540, h = 180, pad = { l: 36, r: 12, t: 14, b: 22 };
  const wrapperRef = useRef(null);
  const [hover, setHover] = useState(null);
  if (!series || !series.length) return <Empty label="无净值序列" />;
  const bench = (benchmark && benchmark.length === series.length) ? benchmark : null;
  const all = bench ? series.concat(bench) : series;
  const min = Math.min(...all, 1) - 0.005;
  const max = Math.max(...all, 1) + 0.005;
  const xRange = (v) => pad.l + (w - pad.l - pad.r) * (v / (series.length - 1));
  const yRange = (v) => pad.t + (h - pad.t - pad.b) * (1 - (v - min) / (max - min));
  const path = series.map((v, i) => `${i === 0 ? 'M' : 'L'} ${xRange(i).toFixed(1)} ${yRange(v).toFixed(1)}`).join(' ');
  const fillPath = path + ` L ${xRange(series.length - 1).toFixed(1)} ${yRange(min).toFixed(1)} L ${xRange(0).toFixed(1)} ${yRange(min).toFixed(1)} Z`;
  const benchPath = bench ? bench.map((v, i) => `${i === 0 ? 'M' : 'L'} ${xRange(i).toFixed(1)} ${yRange(v).toFixed(1)}`).join(' ') : null;
  const yTicks = 4;
  const yLabels = Array.from({ length: yTicks + 1 }, (_, i) => min + (i * (max - min) / yTicks));
  const lbl = (i) => (dates && dates[i]) ? String(dates[i]).slice(2, 10) : ('#' + (i + 1));
  const tickIdxs = series.length ? [0, Math.floor(series.length / 2), series.length - 1] : [];

  const onMove = (e) => {
    const rect = wrapperRef.current.getBoundingClientRect();
    const px = e.clientX - rect.left, py = e.clientY - rect.top;
    const svgX = (px / rect.width) * w;
    if (svgX < pad.l - 4 || svgX > w - pad.r + 4) { setHover(null); return; }
    const t = (svgX - pad.l) / (w - pad.l - pad.r);
    const idx = Math.max(0, Math.min(series.length - 1, Math.round(t * (series.length - 1))));
    setHover({ idx, px, py, rectW: rect.width, rectH: rect.height });
  };

  return (
    <div ref={wrapperRef} style={{ position: 'relative' }} onMouseMove={onMove} onMouseLeave={() => setHover(null)}>
      <svg viewBox={`0 0 ${w} ${h}`} style={{ width: '100%', height: 190, display: 'block', cursor: 'crosshair' }}>
        {yLabels.map((v, i) => (
          <g key={i}>
            <line x1={pad.l} x2={w - pad.r} y1={yRange(v)} y2={yRange(v)} stroke="var(--line-soft)" strokeDasharray={i === 0 ? '0' : '2 3'} />
            <text x={pad.l - 6} y={yRange(v) + 3} textAnchor="end" fontSize="9" fontFamily="var(--mono)" fill="var(--ink-3)">{v.toFixed(2)}</text>
          </g>
        ))}
        <path d={fillPath} fill="var(--zhu)" opacity="0.07" />
        {benchPath && <path d={benchPath} stroke="var(--ink-3)" strokeWidth="1" fill="none" strokeDasharray="3 3" />}
        <path d={path} stroke="var(--ink)" strokeWidth="1.4" fill="none" />
        {tickIdxs.map((di, k, arr) => {
          const x = pad.l + (w - pad.l - pad.r) * (k / (arr.length - 1));
          return <text key={di} x={x} y={h - 6} fontSize="9" fontFamily="var(--mono)" fill="var(--ink-3)" textAnchor="middle">{lbl(di)}</text>;
        })}
        {hover && (
          <g pointerEvents="none">
            <line x1={xRange(hover.idx)} x2={xRange(hover.idx)} y1={pad.t} y2={h - pad.b} stroke="var(--ink)" strokeWidth="0.6" strokeDasharray="3 3" opacity="0.55" />
            <circle cx={xRange(hover.idx)} cy={yRange(series[hover.idx])} r="3.5" fill="var(--yin)" stroke="var(--paper)" strokeWidth="1.2" />
            {bench && <circle cx={xRange(hover.idx)} cy={yRange(bench[hover.idx])} r="2.5" fill="var(--ink-3)" stroke="var(--paper)" strokeWidth="1" />}
          </g>
        )}
      </svg>
      {hover && (() => {
        const v = series[hover.idx];
        const b = bench ? bench[hover.idx] : null;
        return (
          <ChartTip x={hover.px} y={hover.py} w={hover.rectW} h={hover.rectH} tipW={172} tipH={b !== null ? 88 : 56}>
            <div className="mono" style={{ fontSize: 9, color: 'var(--ink-3)', letterSpacing: '0.16em', marginBottom: 4 }}>{lbl(hover.idx)} · 第 {hover.idx + 1} 期</div>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', gap: 8 }}>
              <span className="serif" style={{ fontSize: 11, color: 'var(--ink-1)' }}>● 多空</span>
              <span className="mono" style={{ fontSize: 13, color: 'var(--ink)', fontWeight: 500 }}>{v.toFixed(4)}</span>
            </div>
            {b !== null && (
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', gap: 8 }}>
                <span className="serif" style={{ fontSize: 11, color: 'var(--ink-3)' }}>┄ 基准</span>
                <span className="mono" style={{ fontSize: 12, color: 'var(--ink-2)' }}>{b.toFixed(4)}</span>
              </div>
            )}
          </ChartTip>
        );
      })()}
    </div>
  );
}

// 十分位年化超额柱状. bars=每组年化(%)数组。显示型, 网格线按 max 派生。
function DecileChart({ bars }) {
  const w = 360, h = 180, pad = { l: 30, r: 8, t: 14, b: 22 };
  const wrapperRef = useRef(null);
  const [hover, setHover] = useState(null);
  if (!bars || !bars.length) return <Empty label="无十分位" />;
  const max = Math.max(...bars.map(v => Math.abs(v)), 1);
  const bw = (w - pad.l - pad.r) / bars.length - 4;
  const mid = pad.t + (h - pad.t - pad.b) / 2;
  const barX = (i) => pad.l + 2 + i * ((w - pad.l - pad.r) / bars.length);

  const onMove = (e) => {
    const rect = wrapperRef.current.getBoundingClientRect();
    const px = e.clientX - rect.left, py = e.clientY - rect.top;
    const svgX = (px / rect.width) * w;
    const slot = (w - pad.l - pad.r) / bars.length;
    const idx = Math.floor((svgX - pad.l) / slot);
    if (idx < 0 || idx >= bars.length) { setHover(null); return; }
    setHover({ idx, px, py, rectW: rect.width, rectH: rect.height });
  };

  return (
    <div ref={wrapperRef} style={{ position: 'relative' }} onMouseMove={onMove} onMouseLeave={() => setHover(null)}>
      <svg viewBox={`0 0 ${w} ${h}`} style={{ width: '100%', height: 190, display: 'block' }}>
        <line x1={pad.l} x2={w - pad.r} y1={mid} y2={mid} stroke="var(--ink-3)" strokeWidth="1" />
        {[-max, -max / 2, max / 2, max].map((v) => {
          const y = mid - (v / max) * ((h - pad.t - pad.b) / 2);
          return (
            <g key={v}>
              <line x1={pad.l} x2={w - pad.r} y1={y} y2={y} stroke="var(--line-soft)" strokeDasharray="2 3" />
              <text x={pad.l - 4} y={y + 3} fontSize="9" textAnchor="end" fontFamily="var(--mono)" fill="var(--ink-3)">{v.toFixed(0)}%</text>
            </g>
          );
        })}
        {bars.map((v, i) => {
          const x = barX(i);
          const barH = (Math.abs(v) / max) * ((h - pad.t - pad.b) / 2);
          const y = v >= 0 ? mid - barH : mid;
          const isHover = hover && hover.idx === i;
          return (
            <g key={i}>
              <rect x={x - 2} y={pad.t} width={bw + 4} height={h - pad.t - pad.b} fill="transparent" />
              <rect x={x} y={y} width={bw} height={barH} fill={v >= 0 ? 'var(--zhu)' : 'var(--dai)'} opacity={isHover ? 0.95 : 0.7} stroke={isHover ? 'var(--ink-2)' : 'none'} strokeWidth={isHover ? 0.6 : 0} />
              <text x={x + bw / 2} y={h - 6} fontSize="9" fontFamily="var(--mono)" fill="var(--ink-3)" textAnchor="middle">D{i + 1}</text>
            </g>
          );
        })}
      </svg>
      {hover && (() => {
        const v = bars[hover.idx];
        return (
          <ChartTip x={hover.px} y={hover.py} w={hover.rectW} h={hover.rectH} tipW={160} tipH={56}>
            <div className="mono" style={{ fontSize: 9, color: 'var(--ink-3)', letterSpacing: '0.16em', marginBottom: 4 }}>第 {hover.idx + 1} 分位 · D{hover.idx + 1}</div>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline' }}>
              <span className="serif" style={{ fontSize: 11, color: 'var(--ink-1)' }}>年化超额</span>
              <span className={'mono ' + (v >= 0 ? 'up' : 'down')} style={{ fontSize: 13, fontWeight: 500 }}>{v >= 0 ? '+' : ''}{v.toFixed(2)}%</span>
            </div>
          </ChartTip>
        );
      })()}
    </div>
  );
}

// ═════════════════════════ TopBar (4 模式) ═════════════════════════
function TopBar({ mode, onMode }) {
  const tabs = [
    { k: 'lib', l: '因子库 & 详情' },
    { k: 'forge', l: '炼因子' },
    { k: 'compose', l: '多因子合成' },
    { k: 'archive', l: '研究档案' },
    { k: 'backtest', l: 'Agent 回测' },   // ← P5 新增
    { k: 'watch', l: '实时盯盘' },
  ];
  return (
    <header style={{ padding: '12px 28px', borderBottom: '1px solid var(--line)', display: 'flex', alignItems: 'center', gap: 18, flexShrink: 0, background: 'rgba(241,234,217,0.5)', whiteSpace: 'nowrap' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
        <span style={{ width: 28, height: 28, fontSize: 15, background: 'var(--yin)', color: 'var(--paper)', fontFamily: 'var(--serif)', display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0 }}>觀</span>
        <div>
          <div className="serif" style={{ fontSize: 14, fontWeight: 500, color: 'var(--ink)', letterSpacing: '0.06em' }}>觀瀾 · 量化研究</div>
          <div className="mono" style={{ fontSize: 9, color: 'var(--ink-3)', letterSpacing: '0.18em', marginTop: 1 }}>QUANT WORKBENCH · ALPHA RESEARCH</div>
        </div>
      </div>
      <nav style={{ display: 'flex', alignItems: 'center', gap: 0, marginLeft: 28 }}>
        {tabs.map(t => (
          <button key={t.k} onClick={() => onMode(t.k)} className="hover-pill" style={{
            padding: '6px 12px', border: 'none', background: 'transparent',
            fontFamily: 'var(--serif)', fontSize: 12.5, whiteSpace: 'nowrap',
            color: mode === t.k ? 'var(--ink)' : 'var(--ink-2)',
            borderBottom: mode === t.k ? '2px solid var(--yin)' : '2px solid transparent',
            cursor: 'pointer',
          }}>{t.l}</button>
        ))}
      </nav>
      <div style={{ flex: 1 }} />
      <a href="index.html" className="mono hover-link" style={{ fontSize: 11, color: 'var(--ink-3)', textDecoration: 'none', flexShrink: 0 }}>← 返回对话</a>
    </header>
  );
}

// ═════════════════════════ 模式占位 (Tasks 7-10 填充) ═════════════════════════
// FactorReport 渲染 (C.2 详情 + C.4a composite 共用)。两档分离: IC 体检 / 组合回测。
function FactorReportView({ report }) {
  if (!report) return null;
  if (report.status && report.status !== 'ok') {
    return <ErrorBox error={`评测未完成 · ${report.status}${report.error ? ' · ' + report.error : ''}`} />;
  }
  const ic = report.ic || {}, qt = report.quantile || {}, pf = report.portfolio || {}, ch = report.characteristics || {};
  const icDates = (ic.ic_series || []).map(p => p[0]);
  const icVals = (ic.ic_series || []).map(p => p[1]);
  const navDates = (pf.nav_series || []).map(p => p[0]);
  const navVals = (pf.nav_series || []).map(p => p[1]);
  const benchVals = (pf.benchmark_nav || []).map(p => p[1]);
  const decile = (qt.group_ann_return || []).map(v => v * 100);
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
      {(report.warnings || []).length > 0 && (
        <div className="mono" style={{ fontSize: 10, color: 'var(--jin)' }}>⚠ {report.warnings.join(' · ')}</div>
      )}
      <Panel title={<span>IC 体检 <span className="mono" style={{ fontSize: 9, color: 'var(--ink-3)', marginLeft: 6 }}>秒级 · 截面相关</span></span>}>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4,1fr)', border: '1px solid var(--line-soft)' }}>
          <Kpi label="IC 均值" value={n2(ic.ic_mean, 4)} />
          <Kpi label="ICIR" value={n2(ic.icir, 2)} />
          <Kpi label="RankIC" value={n2(ic.rank_ic_mean, 4)} />
          <Kpi label="RankICIR" value={n2(ic.rank_icir, 2)} last />
          <Kpi label="t-stat" value={n2(ic.ic_tstat, 2)} />
          <Kpi label="IC 胜率" value={pct(ic.ic_win_rate)} />
          <Kpi label="覆盖度" value={pct(ch.coverage)} />
          <Kpi label="半衰期" value={ch.half_life >= 0 ? n2(ch.half_life, 0) : '—'} last />
        </div>
        <div style={{ marginTop: 10 }}><ICChart series={icVals} dates={icDates} /></div>
      </Panel>
      <Panel title={<span>组合回测 <span className="mono" style={{ fontSize: 9, color: 'var(--ink-3)', marginLeft: 6 }}>十分位等权多空 · 毛收益</span></span>}>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4,1fr)', border: '1px solid var(--line-soft)' }}>
          <Kpi label="年化" value={pct(pf.ann_return)} dir={pf.ann_return >= 0 ? 'up' : 'down'} />
          <Kpi label="Sharpe" value={n2(pf.sharpe, 2)} />
          <Kpi label="最大回撤" value={pct(pf.max_drawdown)} dir="down" />
          <Kpi label="Calmar" value={n2(pf.calmar, 2)} last />
          <Kpi label="波动率" value={pct(pf.volatility)} />
          <Kpi label="换手" value={pct(pf.turnover)} />
          <Kpi label="胜率" value={pct(pf.win_rate)} />
          <Kpi label="多空价差" value={pct(qt.long_short_spread)} last />
        </div>
        <div style={{ display: 'flex', gap: 14, marginTop: 10, flexWrap: 'wrap' }}>
          <div style={{ flex: '1 1 320px', minWidth: 0 }}><EquityChart series={navVals} dates={navDates} benchmark={benchVals} /></div>
          <div style={{ flex: '1 1 320px', minWidth: 0 }}><DecileChart bars={decile} /></div>
        </div>
      </Panel>
    </div>
  );
}

function LibraryMode() {
  const [list, setList] = useState({ registered: [], user: [] });
  const [family, setFamily] = useState('');
  const [benchRows, setBenchRows] = useState([]);
  const [pool, setPool] = useState('快测');
  const [sel, setSel] = useState('');
  const [expr, setExpr] = useState('');
  const rpt = useAsync();
  const benchA = useAsync();

  useEffect(() => {
    getJSON('/factor/list').then(d => {
      setList(d || { registered: [], user: [] });
      const fams = [...new Set(((d && d.registered) || []).map(r => r.family))];
      setFamily(fams[0] || 'user');
    }).catch(() => {});
  }, []);

  const families = useMemo(() => {
    // 排除 'user' (入库后的 user 因子也在 registered 里, 会和下面的 我的 项撞 key);
    // user 因子统一在 我的 tab 经 list.user 展示。
    const fams = [...new Set((list.registered || []).map(r => r.family).filter(f => f && f !== 'user'))];
    return [...fams.map(f => ({ value: f, label: f })), { value: 'user', label: '我的' }];
  }, [list]);

  const factors = family === 'user' ? (list.user || []) : (list.registered || []).filter(r => r.family === family);
  const icByName = {};
  benchRows.forEach(r => { icByName[r.name] = r; });

  const loadBench = () => benchA.run(() =>
    getJSON(`/factor/bench?universe=${poolParam(pool)}&family=${family}`).then(b => { setBenchRows((b && b.rows) || []); return b; }));
  const runReport = (target) => {
    const t = target || expr.trim() || sel;
    if (!t) return;
    rpt.run(() => postJSON('/factor/report', { expr_or_name: t, universe: poolParam(pool) }));
  };

  return (
    <div style={{ flex: 1, display: 'flex', minHeight: 0 }}>
      <aside style={{ width: 300, flexShrink: 0, borderRight: '1px solid var(--line)', display: 'flex', flexDirection: 'column', minHeight: 0, background: 'rgba(241,234,217,0.25)' }}>
        <div style={{ padding: 12, borderBottom: '1px solid var(--line-soft)', display: 'flex', gap: 6, flexWrap: 'wrap', alignItems: 'center' }}>
          {families.length > 0 && <Segmented value={family} onChange={setFamily} options={families} />}
          <button onClick={loadBench} className="hover-pill" style={{ fontSize: 11, padding: '4px 8px', border: '1px solid var(--line)', background: 'transparent', cursor: 'pointer' }}>批量 IC ↻</button>
        </div>
        <div style={{ flex: 1, overflowY: 'auto' }}>
          {benchA.loading && <Loading label="批量 IC 计算中…" />}
          {benchA.error && <ErrorBox error={benchA.error} />}
          {factors.map(f => (
            <div key={f.name} className="hover-row" onClick={() => { setSel(f.name); setExpr(''); runReport(f.name); }}
              style={{ padding: '8px 12px', cursor: 'pointer', borderBottom: '1px solid var(--line-soft)', background: sel === f.name ? 'rgba(28,24,20,0.06)' : 'transparent' }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', gap: 6 }}>
                <code className="mono" style={{ fontSize: 12, color: 'var(--ink)' }}>{f.name}</code>
                {icByName[f.name] && <span className={'mono ' + (icByName[f.name].rank_ic >= 0 ? 'up' : 'down')} style={{ fontSize: 10 }}>{n2(icByName[f.name].rank_ic, 3)}</span>}
              </div>
              <div className="mono" style={{ fontSize: 9.5, color: 'var(--ink-3)', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{f.formula || f.expr || ''}</div>
            </div>
          ))}
          {!factors.length && <Empty label="暂无因子" />}
        </div>
      </aside>
      <div style={{ flex: 1, overflowY: 'auto', padding: 18, minWidth: 0 }}>
        <div style={{ display: 'flex', gap: 8, marginBottom: 14, alignItems: 'center', flexWrap: 'wrap' }}>
          <input value={expr} onChange={e => setExpr(e.target.value)} onKeyDown={e => { if (e.key === 'Enter') runReport(); }}
            placeholder="输入白名单表达式, 如 rank(-delta(close,5))"
            style={{ flex: '1 1 280px', padding: '6px 10px', border: '1px solid var(--line)', fontFamily: 'var(--mono)', fontSize: 12, background: 'var(--paper)' }} />
          <Segmented value={pool} onChange={setPool} options={POOLS.map(p => ({ value: p, label: p }))} />
          <button onClick={() => runReport()} disabled={rpt.loading} className="hover-pill"
            style={{ padding: '6px 14px', border: 'none', background: 'var(--ink)', color: 'var(--paper)', fontFamily: 'var(--serif)', fontSize: 12, cursor: 'pointer' }}>
            {rpt.loading ? '运行中…' : '运行评测'}
          </button>
        </div>
        {rpt.loading && <Loading label="组合回测中 (小池秒级 / 大池分钟级)…" />}
        {rpt.error && <ErrorBox error={rpt.error} />}
        {rpt.data && <FactorReportView report={rpt.data} />}
        {!rpt.data && !rpt.loading && !rpt.error && <Empty label="选左侧因子 / 输表达式 → 运行评测" />}
      </div>
    </div>
  );
}
// 炼因子卡 — 复用设计稿 AlchemyCard 的视觉外壳, 真 /factor/forge 契约。
function ForgeCard({ result, onSave, saved, saving }) {
  if (!result) return null;
  const { idea, expr, parsed, name, rationale, compile_ok, error, out_of_vocab, quick_ic } = result;
  return (
    <div style={{ background: 'var(--paper)', border: '1.5px solid var(--ink)', position: 'relative', boxShadow: '6px 6px 0 -2px var(--paper-3)', maxWidth: 640, width: '100%' }}>
      <div style={{ position: 'absolute', top: -1, right: -1, width: 30, height: 30, background: 'var(--yin)', color: 'var(--paper)', fontFamily: 'var(--serif)', fontSize: 14, fontWeight: 500, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>炼</div>
      <div style={{ padding: '10px 14px 8px', borderBottom: '1px solid var(--line-soft)', display: 'flex', alignItems: 'baseline', gap: 8, paddingRight: 38 }}>
        <span className="serif" style={{ fontSize: 13, fontWeight: 500 }}>经验 → 因子</span>
        <span className="mono" style={{ fontSize: 9, color: 'var(--ink-3)', letterSpacing: '0.18em' }}>α-FORGE</span>
      </div>
      <div style={{ padding: '12px 14px 10px' }}>
        <div className="mono" style={{ fontSize: 8.5, color: 'var(--ink-3)', letterSpacing: '0.18em', marginBottom: 6 }}>原话 · 想法</div>
        <div className="serif" style={{ fontSize: 13, color: 'var(--ink-1)', lineHeight: 1.72, fontStyle: 'italic', paddingLeft: 12, borderLeft: '2px solid var(--jin)' }}>{idea}</div>
      </div>
      {out_of_vocab && <div className="mono" style={{ padding: '0 14px 12px', fontSize: 11, color: 'var(--jin)' }}>当前只支持价量/估值/股息/规模/换手类因子；ROE/财报/事件暂不支持 (B.2)。</div>}
      {!compile_ok && !out_of_vocab && <div style={{ padding: '0 14px 12px' }}><ErrorBox error={error || '生成失败'} /></div>}
      {compile_ok && (
        <>
          {rationale && (
            <div style={{ borderTop: '1px dashed var(--line)', padding: '10px 14px' }}>
              <div className="mono" style={{ fontSize: 8.5, color: 'var(--ink-3)', letterSpacing: '0.18em', marginBottom: 6 }}>推理 · LLM</div>
              <div className="serif" style={{ fontSize: 12, color: 'var(--ink-1)', lineHeight: 1.6 }}>{rationale}</div>
            </div>
          )}
          <div style={{ borderTop: '1px dashed var(--line)', padding: '10px 14px', background: 'rgba(28,24,20,0.04)' }}>
            <div className="mono" style={{ fontSize: 8.5, color: 'var(--ink-3)', letterSpacing: '0.18em', marginBottom: 6 }}>因子公式 · {name || 'usr_factor'}</div>
            <pre style={{ margin: 0, fontFamily: 'var(--mono)', fontSize: 12, color: 'var(--ink)', lineHeight: 1.7, whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>{expr}</pre>
          </div>
          <div style={{ borderTop: '1px dashed var(--line)', padding: '10px 14px' }}>
            <div className="mono" style={{ fontSize: 8.5, color: 'var(--ink-3)', letterSpacing: '0.18em', marginBottom: 8 }}>速测 IC{quick_ic ? '' : ' · 跳过'}</div>
            {quick_ic ? (
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3,1fr)', border: '1px solid var(--line-soft)' }}>
                <Kpi label="RankIC" value={n2(quick_ic.rank_ic, 4)} />
                <Kpi label="RankICIR" value={n2(quick_ic.rank_ir, 2)} />
                <Kpi label="判定" value={quick_ic.state || '—'} last />
              </div>
            ) : <span className="mono" style={{ fontSize: 11, color: 'var(--ink-3)' }}>无速测结果</span>}
          </div>
          <div style={{ borderTop: '1px solid var(--line)', padding: '8px 12px' }}>
            <button onClick={() => onSave && onSave(result)} disabled={saved || saving} style={{
              width: '100%', padding: '7px 10px', background: saved ? 'transparent' : 'var(--ink)', color: saved ? 'var(--dai)' : 'var(--paper)',
              border: saved ? '1px solid var(--dai)' : 'none', fontFamily: 'var(--serif)', fontSize: 12, cursor: saved ? 'default' : 'pointer' }}>
              {saved ? '✓ 已入库 · 可在因子库引用' : saving ? '入库中…' : '存入因子库 ↗'}
            </button>
          </div>
        </>
      )}
    </div>
  );
}

function ForgeMode() {
  const [idea, setIdea] = useState('');
  const [pool, setPool] = useState('快测');
  const forge = useAsync();
  const [saved, setSaved] = useState(false);
  const [saving, setSaving] = useState(false);
  const [saveErr, setSaveErr] = useState('');
  const runForge = () => { if (!idea.trim()) return; setSaved(false); setSaveErr(''); forge.run(() => postJSON('/factor/forge', { idea, universe: poolParam(pool), quick_eval: true })); };
  const save = async (r) => {
    setSaving(true); setSaveErr('');
    try { await postJSON('/factor/save', { name: r.name, expr: r.expr, description: r.rationale || '', parsed: r.parsed || [], kpis: r.quick_ic || {} }); setSaved(true); }
    catch (e) { setSaveErr(e.message || String(e)); }
    finally { setSaving(false); }
  };
  return (
    <div style={{ flex: 1, overflowY: 'auto', padding: 24, display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 16 }}>
      <div style={{ width: '100%', maxWidth: 640, display: 'flex', flexDirection: 'column', gap: 8 }}>
        <textarea value={idea} onChange={e => setIdea(e.target.value)} rows={3}
          placeholder="用一句话描述你的因子想法, 如: 5 日反转 / 量价背离 / 低换手高股息"
          style={{ padding: '10px 12px', border: '1px solid var(--line)', fontFamily: 'var(--sans)', fontSize: 13, background: 'var(--paper)', resize: 'vertical' }} />
        <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          <Segmented value={pool} onChange={setPool} options={POOLS.map(p => ({ value: p, label: p }))} />
          <span style={{ flex: 1 }} />
          <button onClick={runForge} disabled={forge.loading} className="hover-pill"
            style={{ padding: '7px 16px', border: 'none', background: 'var(--ink)', color: 'var(--paper)', fontFamily: 'var(--serif)', fontSize: 12, cursor: 'pointer' }}>
            {forge.loading ? '炼制中…' : '炼因子 ⚗'}
          </button>
        </div>
      </div>
      {forge.loading && <Loading label="LLM 炼因子 + 速测中…" />}
      {forge.error && <ErrorBox error={forge.error} />}
      {forge.data && <ForgeCard result={forge.data} onSave={save} saved={saved} saving={saving} />}
      {saveErr && <ErrorBox error={'入库失败: ' + saveErr} />}
    </div>
  );
}
function ComposeMode() {
  const [list, setList] = useState({ registered: [], user: [] });
  const [members, setMembers] = useState([]);
  const [draft, setDraft] = useState('');
  const [method, setMethod] = useState('equal');
  const [pool, setPool] = useState('快测');
  const [trainFrac, setTrainFrac] = useState(0.6);
  const comp = useAsync();
  const [goal, setGoal] = useState('');
  const advise = useAsync();
  const [recipeNote, setRecipeNote] = useState('');
  const doAdvise = () => {
    if (!goal.trim()) return;
    advise.run(() => postJSON('/factor/compose/advise', { goal, universe: poolParam(pool) }).then(rec => {
      if (rec && rec.status === 'ok') {
        setMembers(rec.members || []);
        if (rec.method) setMethod(rec.method);
        if (rec.train_frac) setTrainFrac(rec.train_frac);
        setRecipeNote(rec.rationale || '');
      } else {
        setRecipeNote('配方生成失败: ' + ((rec && rec.error) || '未知'));
      }
      return rec;
    }));
  };
  useEffect(() => { getJSON('/factor/list').then(d => setList(d || { registered: [], user: [] })).catch(() => {}); }, []);
  const allNames = [...new Set([...(list.registered || []).map(r => r.name), ...(list.user || []).map(u => u.name)])];
  const addMember = (m) => { if (m && !members.includes(m)) setMembers([...members, m]); };
  const run = () => { if (members.length < 2) return; comp.run(() => postJSON('/factor/compose', { members, method, universe: poolParam(pool), train_frac: trainFrac, interpret: true })); };
  const res = comp.data;
  return (
    <div style={{ flex: 1, overflowY: 'auto', padding: 18, minWidth: 0 }}>
      <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginBottom: 10, flexWrap: 'wrap' }}>
        <input value={goal} onChange={e => setGoal(e.target.value)}
          onKeyDown={e => { if (e.key === 'Enter') doAdvise(); }}
          placeholder="🪄 一句话配方: 如 低回撤的动量+反转组合"
          style={{ flex: '1 1 320px', padding: '6px 10px', border: '1px solid var(--jin)', fontFamily: 'var(--sans)', fontSize: 13, background: 'var(--paper)' }} />
        <button onClick={doAdvise} disabled={advise.loading} className="hover-pill"
          style={{ padding: '6px 14px', border: 'none', background: 'var(--jin)', color: 'var(--paper)', fontFamily: 'var(--serif)', fontSize: 12, cursor: 'pointer' }}>
          {advise.loading ? '配方中…' : 'LLM 配方 🪄'}
        </button>
      </div>
      {advise.error && <div style={{ marginBottom: 10 }}><ErrorBox error={advise.error} /></div>}
      {recipeNote && <div className="serif" style={{ fontSize: 12, color: 'var(--ink-2)', marginBottom: 10, paddingLeft: 10, borderLeft: '2px solid var(--jin)' }}>{recipeNote}</div>}
      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center', marginBottom: 12 }}>
        <input list="members-dl" value={draft} onChange={e => setDraft(e.target.value)}
          onKeyDown={e => { if (e.key === 'Enter') { addMember(draft.trim()); setDraft(''); } }}
          placeholder="选/输因子名或表达式" style={{ flex: '1 1 240px', padding: '6px 10px', border: '1px solid var(--line)', fontFamily: 'var(--mono)', fontSize: 12, background: 'var(--paper)' }} />
        <datalist id="members-dl">{allNames.map(n => <option key={n} value={n} />)}</datalist>
        <button onClick={() => { addMember(draft.trim()); setDraft(''); }} className="hover-pill" style={{ padding: '6px 10px', border: '1px solid var(--line)', background: 'transparent', cursor: 'pointer', fontSize: 12 }}>+ 加成员</button>
        <Segmented value={method} onChange={setMethod} options={[{ value: 'equal', label: '等权' }, { value: 'ic_weighted', label: 'IC加权' }, { value: 'linear', label: '线性' }, { value: 'lgbm', label: 'LGBM' }]} />
        <Segmented value={pool} onChange={setPool} options={POOLS.map(p => ({ value: p, label: p }))} />
        <button onClick={run} disabled={comp.loading || members.length < 2} className="hover-pill"
          style={{ padding: '6px 14px', border: 'none', background: members.length < 2 ? 'var(--line)' : 'var(--ink)', color: 'var(--paper)', fontFamily: 'var(--serif)', fontSize: 12, cursor: members.length < 2 ? 'default' : 'pointer' }}>
          {comp.loading ? '合成中…' : '合成评测'}
        </button>
      </div>
      <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginBottom: 14, alignItems: 'center' }}>
        {members.map(m => (
          <span key={m} className="mono" style={{ fontSize: 11, padding: '3px 8px', border: '1px solid var(--line)', display: 'flex', gap: 6, alignItems: 'center' }}>
            {m}<span onClick={() => setMembers(members.filter(x => x !== m))} style={{ cursor: 'pointer', color: 'var(--yin)', opacity: 1, fontWeight: 600 }}>×</span>
          </span>
        ))}
        {members.length < 2 && <span className="serif" style={{ fontSize: 12, color: 'var(--ink-3)' }}>至少选 2 个成员</span>}
      </div>
      {comp.loading && <Loading label="OOS 训练/测试中…" />}
      {comp.error && <ErrorBox error={comp.error} />}
      {res && res.status && res.status !== 'ok' && <ErrorBox error={`合成未完成 · ${res.status}${res.error ? ' · ' + res.error : ''}`} />}
      {res && res.status === 'ok' && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
          <Panel title={<span>合成结论 <span className="mono" style={{ fontSize: 10, color: 'var(--ink-3)', marginLeft: 6 }}>{res.method} · train {res.n_train_dates} / test {res.n_test_dates}</span></span>}>
            <div className="serif" style={{ fontSize: 13, color: 'var(--ink)', lineHeight: 1.6, marginBottom: 10 }}>{res.verdict}</div>
            <div style={{ display: 'flex', gap: 20, flexWrap: 'wrap' }}>
              <div style={{ flex: '1 1 200px' }}>
                <div className="mono" style={{ fontSize: 9, color: 'var(--ink-3)', marginBottom: 4, letterSpacing: '0.16em' }}>权重</div>
                {Object.entries(res.weights || {}).map(([k, v]) => (
                  <div key={k} style={{ display: 'flex', justifyContent: 'space-between', fontSize: 11, padding: '2px 0' }}><code className="mono" style={{ color: 'var(--ink-1)' }}>{k}</code><span className="mono">{n2(v, 3)}</span></div>
                ))}
              </div>
              <div style={{ flex: '1 1 320px' }}>
                <div className="mono" style={{ fontSize: 9, color: 'var(--ink-3)', marginBottom: 4, letterSpacing: '0.16em' }}>成员 OOS 对比</div>
                <table style={{ width: '100%', fontSize: 11, borderCollapse: 'collapse' }}>
                  <thead><tr style={{ color: 'var(--ink-3)' }}><td style={{ textAlign: 'left' }}>成员</td><td style={{ textAlign: 'right' }}>RankIC</td><td style={{ textAlign: 'right' }}>Sharpe</td></tr></thead>
                  <tbody>{(res.member_oos || []).map(m => (
                    <tr key={m.name}><td><code className="mono" style={{ color: 'var(--ink-1)' }}>{m.name}</code></td><td className="mono" style={{ textAlign: 'right' }}>{n2(m.rank_ic, 3)}</td><td className="mono" style={{ textAlign: 'right' }}>{n2(m.sharpe, 2)}</td></tr>
                  ))}</tbody>
                </table>
              </div>
            </div>
          </Panel>
          <div>
            <div className="mono" style={{ fontSize: 10, color: 'var(--ink-3)', margin: '4px 0 8px', letterSpacing: '0.16em' }}>综合分 OOS 评测</div>
            <FactorReportView report={res.composite} />
          </div>
          {res.interpretation && (
            <Panel title={<span>LLM 研判 <span className="mono" style={{ fontSize: 9, color: 'var(--ink-3)', marginLeft: 6 }}>compose interpreter</span></span>}>
              <div className="serif" style={{ fontSize: 13, color: 'var(--ink-1)', lineHeight: 1.7, whiteSpace: 'pre-wrap' }}>{res.interpretation}</div>
            </Panel>
          )}
        </div>
      )}
    </div>
  );
}
function ArchiveMode() {
  const listA = useAsync();
  const [target, setTarget] = useState('');
  const [cmp, setCmp] = useState([]);
  const cmpA = useAsync();
  const load = (t) => listA.run(() => getJSON(t ? `/factor/archive?target=${encodeURIComponent(t)}` : '/factor/archive'));
  useEffect(() => { load(''); }, []);
  const rows = target ? ((listA.data && listA.data.history) || []) : ((listA.data && listA.data.runs) || []);
  const loadTarget = (t) => { setTarget(t); setCmp([]); cmpA.reset(); load(t); };
  const reset = () => { setTarget(''); setCmp([]); cmpA.reset(); load(''); };
  const toggleCmp = (id) => {
    const next = cmp.includes(id) ? cmp.filter(x => x !== id) : [...cmp, id].slice(-2);
    setCmp(next);
    if (next.length === 2) cmpA.run(() => getJSON(`/factor/archive?compare=${next[0]},${next[1]}`)); else cmpA.reset();
  };
  const diffs = cmpA.data && (cmpA.data.metric_diffs || cmpA.data);
  return (
    <div style={{ flex: 1, overflowY: 'auto', padding: 18, minWidth: 0 }}>
      <div style={{ display: 'flex', gap: 10, alignItems: 'center', marginBottom: 12, flexWrap: 'wrap' }}>
        <span className="serif" style={{ fontSize: 14, color: 'var(--ink)' }}>研究档案 {target && <span className="mono" style={{ fontSize: 11, color: 'var(--ink-3)' }}>· {target} 历史</span>}</span>
        {(target || cmp.length > 0) && <button onClick={reset} className="hover-pill" style={{ fontSize: 11, padding: '3px 8px', border: '1px solid var(--line)', background: 'transparent', cursor: 'pointer' }}>← 全部</button>}
        <span style={{ flex: 1 }} />
        <span className="mono" style={{ fontSize: 10, color: 'var(--ink-3)' }}>勾选 2 条对比 diff</span>
      </div>
      {listA.loading && <Loading />}
      {listA.error && <ErrorBox error={listA.error} />}
      {cmpA.loading && <Loading label="对比中…" />}
      {diffs && (
        <Panel title={`对比 diff · ${cmp.join(' → ')}`}>
          <table style={{ width: '100%', fontSize: 11, borderCollapse: 'collapse' }}>
            <tbody>{Object.entries(diffs).filter(([k, v]) => typeof v === 'number').map(([k, v]) => (
              <tr key={k}><td className="mono" style={{ color: 'var(--ink-3)' }}>{k}</td><td className={'mono ' + (v >= 0 ? 'up' : 'down')} style={{ textAlign: 'right' }}>{v >= 0 ? '+' : ''}{n2(v, 4)}</td></tr>
            ))}</tbody>
          </table>
        </Panel>
      )}
      {!listA.loading && !rows.length && <Empty label="研究档案为空 · 在因子库 / 合成里跑评测会自动归档" />}
      <div style={{ marginTop: 12 }}>
        {rows.map(r => (
          <div key={r.id} className="hover-row" style={{ display: 'flex', gap: 10, alignItems: 'center', padding: '8px 10px', borderBottom: '1px solid var(--line-soft)' }}>
            <input type="checkbox" checked={cmp.includes(r.id)} onChange={() => toggleCmp(r.id)} style={{ cursor: 'pointer' }} />
            <code className="mono" style={{ fontSize: 11, color: 'var(--ink-3)', width: 54, flexShrink: 0 }}>{r.id}</code>
            <span className="mono" style={{ fontSize: 9.5, padding: '1px 5px', background: r.kind === 'compose' ? 'var(--yin)' : 'var(--dai)', color: 'var(--paper)', flexShrink: 0 }}>{r.kind}</span>
            <code className="mono hover-link" onClick={() => loadTarget(r.target)} style={{ fontSize: 12, color: 'var(--ink)', cursor: 'pointer', maxWidth: 220, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{r.target}</code>
            <span className="mono" style={{ fontSize: 9.5, color: 'var(--ink-3)', flex: 1, minWidth: 0 }}>{r.timestamp} · {r.universe}/{r.freq}{r.note ? ' · ' + r.note : ''}</span>
            <span className="mono" style={{ fontSize: 9.5, color: 'var(--ink-2)', flexShrink: 0 }}>{Object.entries(r.metrics || {}).slice(0, 3).map(([k, v]) => `${k}=${n2(v, 3)}`).join('  ')}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

// ═════════════════════════ Agent 回测 (P5) ═════════════════════════
function BacktestMode() {
  const [start, setStart]   = useState('');
  const [end, setEnd]       = useState('');
  const [cash, setCash]     = useState(1000000);
  const [topn, setTopn]     = useState(20);
  const [mode, setMode]     = useState('mock');          // mock | real
  const run = useAsync();
  const [polling, setPolling] = useState(false);
  const timer = useRef(null);

  // 挂载: probe data_end (走 /data/status 的 day 时间戳 — 退而求其次用近 2 周),
  // 填默认窗口, 不硬编码任何固定日期。失败则留空 (后端 None → 自动近窗口)。
  useEffect(() => {
    getJSON('/data/status').then(d => {
      const today = new Date();
      const iso = (dt) => dt.toISOString().slice(0, 10);
      const past = new Date(today.getTime() - 14 * 864e5);
      setStart(iso(past)); setEnd(iso(today));
    }).catch(() => {});
    return () => { if (timer.current) clearInterval(timer.current); };
  }, []);

  const start_run = () => {
    if (timer.current) clearInterval(timer.current);
    setPolling(true);
    const maxPolls = mode === 'mock' ? 60 : 150;   // mock≈36s / real≈6min 兜底
    run.run(async () => {
      const r = await postJSON('/backtest/run', {
        start: start || null, end: end || null,
        init_cash: Number(cash), candidate_topn: Number(topn), mode,
        match_freq: 'day',
      });
      const rid = r.run_id;
      let polls = 0;
      return await new Promise((resolve, reject) => {
        timer.current = setInterval(async () => {
          polls += 1;
          if (polls > maxPolls) {
            clearInterval(timer.current); setPolling(false);
            reject(new Error('回测超时, 请重试或缩短窗口')); return;
          }
          try {
            const res = await getJSON('/backtest/result/' + rid);
            if (res.status === 'running') return;        // 继续轮询
            clearInterval(timer.current); setPolling(false);
            if (res.status === 'error') reject(new Error(res.error || '回测失败'));
            else resolve(res);
          } catch (e) { clearInterval(timer.current); setPolling(false); reject(e); }
        }, mode === 'mock' ? 600 : 2500);
      });
    });
  };

  const d = run.data;
  const navVals = d ? (d.nav && d.nav.series || []) : [];
  const navDates = d ? (d.nav && d.nav.dates || []) : [];
  const k = d ? (d.kpi || {}) : {};
  const navOk = navVals.length >= 2;                     // 单点 → EquityChart 除 0
  const dirOf = (v) => (v === null || v === undefined) ? undefined : (v >= 0 ? 'up' : 'down');
  // 理由摘要: fill.reason 恒空 → 查 decisions[date] 里同 code 的 reason
  const reasonFor = (t) => {
    if (t.reason) return t.reason;
    try {
      const day = d && d.decisions && d.decisions[t.date];
      const leg = day && (day.decisions || []).find(x => (x.code) === t.code);
      return (leg && leg.reason) || '—';
    } catch (e) { return '—'; }
  };

  return (
    <div style={{ flex: 1, overflowY: 'auto', padding: 18, minWidth: 0 }}>
      {/* 控件条 */}
      <div style={{ display: 'flex', gap: 12, alignItems: 'flex-end', flexWrap: 'wrap', marginBottom: 14 }}>
        <label className="mono" style={{ fontSize: 10, color: 'var(--ink-3)' }}>起始日
          <input value={start} onChange={e => setStart(e.target.value)} placeholder="YYYY-MM-DD"
            style={{ display: 'block', marginTop: 3, padding: '5px 8px', border: '1px solid var(--line)', fontFamily: 'var(--mono)', fontSize: 12 }} /></label>
        <label className="mono" style={{ fontSize: 10, color: 'var(--ink-3)' }}>结束日
          <input value={end} onChange={e => setEnd(e.target.value)} placeholder="留空=data_end"
            style={{ display: 'block', marginTop: 3, padding: '5px 8px', border: '1px solid var(--line)', fontFamily: 'var(--mono)', fontSize: 12 }} /></label>
        <label className="mono" style={{ fontSize: 10, color: 'var(--ink-3)' }}>初始资金
          <input type="number" value={cash} onChange={e => setCash(e.target.value)}
            style={{ display: 'block', marginTop: 3, padding: '5px 8px', width: 120, border: '1px solid var(--line)', fontFamily: 'var(--mono)', fontSize: 12 }} /></label>
        <label className="mono" style={{ fontSize: 10, color: 'var(--ink-3)' }}>候选 N
          <input type="number" value={topn} onChange={e => setTopn(e.target.value)}
            style={{ display: 'block', marginTop: 3, padding: '5px 8px', width: 70, border: '1px solid var(--line)', fontFamily: 'var(--mono)', fontSize: 12 }} /></label>
        <Segmented value={mode} onChange={setMode}
          options={[{ value: 'mock', label: 'Mock(秒级)' }, { value: 'real', label: '真 LLM(慢)' }]} />
        <button onClick={start_run} disabled={run.loading || polling} className="hover-pill"
          style={{ padding: '7px 16px', border: 'none', background: 'var(--ink)', color: 'var(--paper)', fontFamily: 'var(--serif)', fontSize: 12, cursor: 'pointer' }}>
          {(run.loading || polling) ? '回测中…' : '起回测 ▶'}
        </button>
      </div>

      {(run.loading || polling) && <Loading label={mode === 'mock' ? '跑确定性回测中…' : 'LLM 决策回测中(较慢)…'} />}
      {run.error && <ErrorBox error={run.error} />}

      {d && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
          {(d.warnings || []).length > 0 && <div className="mono" style={{ fontSize: 10, color: 'var(--jin)' }}>⚠ {d.warnings.join(' · ')}</div>}
          <Panel title={<span>组合表现 <span className="mono" style={{ fontSize: 9, color: 'var(--ink-3)', marginLeft: 6 }}>{d.mode}·{d.params && d.params.start}~{d.params && d.params.end}·LLM {k.n_llm_calls} 次</span></span>}>
            {/* 8 格, 对齐 FactorReportView 的组合回测格 */}
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4,1fr)', border: '1px solid var(--line-soft)' }}>
              <Kpi label="年化"     value={pct(k.ann_return)} dir={dirOf(k.ann_return)} />
              <Kpi label="Sharpe"   value={n2(k.sharpe, 2)} />
              <Kpi label="最大回撤"  value={pct(k.max_drawdown)} dir={k.max_drawdown ? 'down' : undefined} />
              <Kpi label="Calmar"   value={n2(k.calmar, 2)} last />
              <Kpi label="波动率"    value={pct(k.volatility)} />
              <Kpi label="换手"     value={pct(k.turnover)} />
              <Kpi label="胜率(日)"  value={pct(k.win_rate)} />
              <Kpi label="逐笔胜率"  value={pct(k.trade_win_rate)} last />
            </div>
            <div style={{ marginTop: 10 }}>
              {navOk ? <EquityChart series={navVals} dates={navDates} benchmark={d.benchmark || undefined} />
                     : <Empty label="窗口过短, 无足够净值点 (请选 ≥2 个交易日)" />}
            </div>
          </Panel>
          <Panel title={<span>交易记录 <span className="mono" style={{ fontSize: 9, color: 'var(--ink-3)', marginLeft: 6 }}>{(d.trades || []).length} 笔 · 逐笔胜率 {pct(k.trade_win_rate)}</span></span>}>
            {(!d.trades || !d.trades.length) ? <Empty label="窗口内无成交" /> : (
              <div>
                <div style={{ display: 'flex', gap: 10, padding: '6px 10px', borderBottom: '1px solid var(--line)' }}>
                  {['日期', '动作', '代码', '成交价', '数量', '盈亏', '理由'].map((h, i) => (
                    <span key={i} className="mono" style={{
                      fontSize: 9.5, color: 'var(--ink-3)', letterSpacing: '0.1em',
                      width: [78, 46, 84, 72, 64, 88, 0][i] || undefined, flex: i === 6 ? 1 : 'none', minWidth: i === 6 ? 0 : undefined
                    }}>{h}</span>
                  ))}
                </div>
                {d.trades.map((t, i) => (
                  <div key={i} className="hover-row" style={{ display: 'flex', gap: 10, alignItems: 'baseline', padding: '7px 10px', borderBottom: '1px solid var(--line-soft)' }}>
                    <span className="mono" style={{ fontSize: 11, color: 'var(--ink-2)', width: 78 }}>{t.date}</span>
                    <span className="mono" style={{ fontSize: 10, width: 46, color: t.action === 'buy' ? 'var(--zhu)' : 'var(--dai)' }}>{t.action}</span>
                    <code className="mono" style={{ fontSize: 11.5, color: 'var(--ink)', width: 84 }}>{t.code}</code>
                    <span className="mono" style={{ fontSize: 11, color: 'var(--ink-1)', width: 72 }}>{n2(t.price, 2)}</span>
                    <span className="mono" style={{ fontSize: 11, color: 'var(--ink-2)', width: 64 }}>{t.qty}</span>
                    <span className={'mono ' + (t.pnl > 0 ? 'up' : t.pnl < 0 ? 'down' : '')} style={{ fontSize: 11, width: 88 }}>{t.action === 'sell' ? n2(t.pnl, 1) : '—'}</span>
                    <span className="serif" style={{ fontSize: 11, color: 'var(--ink-2)', flex: 1, minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{reasonFor(t)}</span>
                  </div>
                ))}
              </div>
            )}
          </Panel>
        </div>
      )}
      {!d && !run.loading && !polling && !run.error && <Empty label="设定窗口 + Mock 模式, 点「起回测」秒级出净值 + 交易 (Mock=单只反转买入持有 N 日后了结, 演示数据通路, 非盈利策略)" />}
    </div>
  );
}

// ═════════════════════════ 实时盯盘 (Task 8) ═════════════════════════
//
// 三栏: 左 自选列表 (EventSource /watch/stream 收 quote_update 驱动实时价 + ⚡触发标记,
// 点选切换 sel); 中 <Candle code={sel}/> (最小 SVG 蜡烛 + MA); 右 推荐 feed
// (收 recommendation 事件 unshift, 每条 [确认][忽略] -> POST /watch/ack)。
// 顶部 开始/停止 -> POST /watch/start,/watch/stop; 初始 GET /watch/status。
//
// 后端契约 (buddy/server.py + watch/loop.py 已实现):
//   GET  /watch/status  -> {ok, running, n_items, items:[{code,avg_cost,stop_loss}], tick_count, llm_calls_made}
//   POST /watch/start   {items:[{code,avg_cost?,stop_loss?}], tick_seconds?} -> {ok, running, n_items}
//   POST /watch/stop    -> {ok, running:false}
//   GET  /watch/stream  (SSE) -> event: quote_update {code, ts, quote:{price,changePercent,high,low,...}}
//                                event: recommendation {code, ts, rec:{code,action,reason,trigger_kind,target_price,stop_loss,confidence}}
//   POST /watch/ack     {ts, code, user_action:'confirm'|'ignore'} -> {ok}

// 动作 -> 中文 + 涨跌色向 (与 backtest.decision.DecisionLeg 五档一致)
const ACTION_CN = { buy: '买入', add: '加仓', hold: '持有', reduce: '减仓', sell: '卖出' };
const ACTION_DIR = { buy: 'up', add: 'up', hold: '', reduce: 'down', sell: 'down' };

// 最小蜡烛图 — 静态渲染一段 bars (OHLC 或仅 close 点序列) + MA。空数据不白屏 (Empty)。
// bars: [{open,high,low,close,ts?}] 或 [{close,ts?}] 或 [number]。maWindow 默认 5。
function Candle({ code, bars, maWindow = 5 }) {
  const w = 540, h = 260, pad = { l: 40, r: 12, t: 16, b: 24 };
  // 归一化输入: 容忍纯数字 / 仅 close / 完整 OHLC。
  const norm = (bars || []).map((b) => {
    if (b === null || b === undefined) return null;
    if (typeof b === 'number') return { o: b, h: b, l: b, c: b, ts: null };
    const c = (b.close !== undefined ? b.close : b.c);
    if (c === null || c === undefined || (typeof c === 'number' && isNaN(c))) return null;
    const o = (b.open !== undefined ? b.open : (b.o !== undefined ? b.o : c));
    const hi = (b.high !== undefined ? b.high : (b.h !== undefined ? b.h : Math.max(o, c)));
    const lo = (b.low !== undefined ? b.low : (b.l !== undefined ? b.l : Math.min(o, c)));
    return { o, h: hi, l: lo, c, ts: (b.ts || b.trade_date || b.datetime || null) };
  }).filter(Boolean);

  if (!code) return <Empty label="← 选择左侧自选股查看分时蜡烛" />;
  if (!norm.length) return <Empty label="等待行情数据… (盯盘开始后实时累积)" />;

  const lo = Math.min(...norm.map(b => b.l));
  const hi = Math.max(...norm.map(b => b.h));
  const span = (hi - lo) || (Math.abs(hi) * 0.02 + 0.01);   // 防 0 高度 (一字/单点)
  const padV = span * 0.08;
  const yMin = lo - padV, yMax = hi + padV;
  const innerW = w - pad.l - pad.r, innerH = h - pad.t - pad.b;
  const n = norm.length;
  const slot = innerW / n;
  const bw = Math.max(1.5, Math.min(14, slot * 0.6));
  const xMid = (i) => pad.l + slot * (i + 0.5);
  const yPx = (v) => pad.t + innerH * (1 - (v - yMin) / (yMax - yMin));

  // MA(maWindow) over close
  const ma = norm.map((_, i) => {
    if (i < maWindow - 1) return null;
    let s = 0;
    for (let k = i - maWindow + 1; k <= i; k++) s += norm[k].c;
    return s / maWindow;
  });
  const maPath = ma
    .map((v, i) => (v === null ? null : `${i === 0 || ma[i - 1] === null ? 'M' : 'L'} ${xMid(i).toFixed(1)} ${yPx(v).toFixed(1)}`))
    .filter(Boolean).join(' ');

  const yTicks = 4;
  const yLabels = Array.from({ length: yTicks + 1 }, (_, i) => yMin + (i * (yMax - yMin) / yTicks));
  const lbl = (b, i) => (b.ts ? String(b.ts).slice(-8, -3) || String(b.ts).slice(0, 5) : '#' + (i + 1));
  const tickIdxs = n > 1 ? [0, Math.floor(n / 2), n - 1] : [0];
  const last = norm[n - 1];

  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 10, marginBottom: 6 }}>
        <code className="mono" style={{ fontSize: 13, color: 'var(--ink)' }}>{code}</code>
        <span className="mono" style={{ fontSize: 11, color: 'var(--ink-3)' }}>最新 {n2(last.c, 2)}</span>
        <span style={{ flex: 1 }} />
        <span className="mono" style={{ fontSize: 9.5, color: 'var(--ink-3)', letterSpacing: '0.14em' }}>MA{maWindow} · {n} 根</span>
      </div>
      <svg viewBox={`0 0 ${w} ${h}`} style={{ width: '100%', height: 270, display: 'block', border: '1px solid var(--line-soft)' }}>
        {yLabels.map((v, i) => (
          <g key={i}>
            <line x1={pad.l} x2={w - pad.r} y1={yPx(v)} y2={yPx(v)} stroke="var(--line-soft)" strokeDasharray={i === 0 ? '0' : '2 3'} />
            <text x={pad.l - 6} y={yPx(v) + 3} textAnchor="end" fontSize="9" fontFamily="var(--mono)" fill="var(--ink-3)">{v.toFixed(2)}</text>
          </g>
        ))}
        {norm.map((b, i) => {
          const up = b.c >= b.o;
          const col = up ? 'var(--zhu)' : 'var(--dai)';
          const x = xMid(i);
          const yO = yPx(b.o), yC = yPx(b.c);
          const top = Math.min(yO, yC);
          const bodyH = Math.max(1, Math.abs(yC - yO));
          return (
            <g key={i}>
              <line x1={x} x2={x} y1={yPx(b.h)} y2={yPx(b.l)} stroke={col} strokeWidth="1" />
              <rect x={x - bw / 2} y={top} width={bw} height={bodyH} fill={col} opacity={up ? 0.85 : 0.95} />
            </g>
          );
        })}
        {maPath && <path d={maPath} stroke="var(--yin)" strokeWidth="1.3" fill="none" />}
        {tickIdxs.map((di) => (
          <text key={di} x={xMid(di)} y={h - 7} fontSize="9" fontFamily="var(--mono)" fill="var(--ink-3)" textAnchor="middle">{lbl(norm[di], di)}</text>
        ))}
      </svg>
    </div>
  );
}

// 一条推荐卡 (右栏 feed)。rec = {action, reason, trigger_kind, target_price, stop_loss, confidence}。
function RecCard({ item, onAck }) {
  const { code, ts, rec, ack } = item;
  const r = rec || {};
  const dir = ACTION_DIR[r.action] || '';
  return (
    <div style={{ border: '1px solid var(--line)', background: 'var(--paper)', marginBottom: 10 }}>
      <div style={{ padding: '8px 11px', borderBottom: '1px solid var(--line-soft)', display: 'flex', alignItems: 'center', gap: 8 }}>
        <code className="mono" style={{ fontSize: 12, color: 'var(--ink)' }}>{code}</code>
        <span className={'mono ' + dir} style={{ fontSize: 12, fontWeight: 600 }}>{ACTION_CN[r.action] || r.action || '—'}</span>
        <span style={{ flex: 1 }} />
        <span className="mono" style={{ fontSize: 9, color: 'var(--ink-3)', padding: '1px 5px', border: '1px solid var(--line-soft)' }}>{r.trigger_kind || '—'}</span>
      </div>
      <div style={{ padding: '9px 11px' }}>
        <div className="serif" style={{ fontSize: 12.5, color: 'var(--ink-1)', lineHeight: 1.6 }}>{r.reason || (r.error ? '✗ ' + r.error : '无理由')}</div>
        <div style={{ display: 'flex', gap: 14, marginTop: 7, flexWrap: 'wrap' }}>
          {r.target_price > 0 && <span className="mono" style={{ fontSize: 10, color: 'var(--ink-2)' }}>目标 {n2(r.target_price, 2)}</span>}
          {r.stop_loss > 0 && <span className="mono" style={{ fontSize: 10, color: 'var(--ink-2)' }}>止损 {n2(r.stop_loss, 2)}</span>}
          {(r.confidence || r.confidence === 0) && <span className="mono" style={{ fontSize: 10, color: 'var(--ink-2)' }}>信心 {pct(r.confidence, 0)}</span>}
          <span className="mono" style={{ fontSize: 9.5, color: 'var(--ink-3)' }}>{ts}</span>
        </div>
      </div>
      <div style={{ borderTop: '1px solid var(--line-soft)', padding: '6px 9px', display: 'flex', gap: 8 }}>
        {ack ? (
          <span className="mono" style={{ fontSize: 11, color: ack === 'confirm' ? 'var(--zhu)' : 'var(--ink-3)' }}>
            {ack === 'confirm' ? '✓ 已确认' : '— 已忽略'}
          </span>
        ) : (
          <>
            <button onClick={() => onAck(item, 'confirm')} className="hover-pill"
              style={{ flex: 1, padding: '5px 8px', border: 'none', background: 'var(--ink)', color: 'var(--paper)', fontFamily: 'var(--serif)', fontSize: 11.5, cursor: 'pointer' }}>确认</button>
            <button onClick={() => onAck(item, 'ignore')} className="hover-pill"
              style={{ flex: 1, padding: '5px 8px', border: '1px solid var(--line)', background: 'transparent', color: 'var(--ink-2)', fontFamily: 'var(--serif)', fontSize: 11.5, cursor: 'pointer' }}>忽略</button>
          </>
        )}
      </div>
    </div>
  );
}

// 复盘页 (C 复盘闭环): 命中率看板 (overall + by_trigger + by_action) + 推荐历史
// (rec × outcome left-join, verdict/T+1/T+5)。盘后点「复盘回填」给到期推荐离线打分。
function WatchReview({ hitrate, rows, busy, err, onBackfill, onReload }) {
  const ov = (hitrate && hitrate.overall) ||
    { n: 0, correct: 0, partial: 0, wrong: 0, win_rate: 0, avg_return_t1: 0, avg_return_t5: 0 };
  const byTrig = (hitrate && hitrate.by_trigger) || {};
  const byAct = (hitrate && hitrate.by_action) || {};
  const pct = (x) => (x === undefined || x === null) ? '—' : (x * 100).toFixed(1) + '%';
  const retCell = (x) => {
    if (x === undefined || x === null) return <span className="mono" style={{ color: 'var(--ink-3)' }}>—</span>;
    const d = x > 0 ? 'up' : x < 0 ? 'down' : '';
    return <span className={'mono ' + d}>{(x > 0 ? '+' : '') + (x * 100).toFixed(2) + '%'}</span>;
  };
  const vColor = (v) => v === 'correct' ? 'var(--zhu)' : v === 'wrong' ? 'var(--dai)'
    : v === 'partial' ? 'var(--jin)' : 'var(--ink-3)';
  const vLabel = (v) => ({ correct: '✓ 命中', wrong: '✗ 错', partial: '~ 部分', pending: '… 待评' })[v] || v;
  const StatRow = ({ k, s }) => (
    <tr style={{ borderTop: '1px solid var(--line-soft)' }}>
      <td style={{ padding: '5px 8px' }}><code className="mono" style={{ fontSize: 11 }}>{k}</code></td>
      <td className="mono" style={{ padding: '5px 8px', textAlign: 'right' }}>{s.n}</td>
      <td className="mono" style={{ padding: '5px 8px', textAlign: 'right', color: s.win_rate >= 0.5 ? 'var(--zhu)' : 'var(--dai)' }}>{pct(s.win_rate)}</td>
      <td style={{ padding: '5px 8px', textAlign: 'right' }}>{retCell(s.avg_return_t5)}</td>
    </tr>
  );
  return (
    <div style={{ flex: 1, overflowY: 'auto', padding: 20, minWidth: 0 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 16 }}>
        <span className="serif" style={{ fontSize: 15, color: 'var(--ink)', fontWeight: 500 }}>推荐复盘</span>
        <span className="mono" style={{ fontSize: 10, color: 'var(--ink-3)' }}>盘后离线 · T+1/T+5 outcome 打分</span>
        <span style={{ flex: 1 }} />
        <button onClick={onReload} className="hover-pill" style={{ padding: '5px 11px', border: '1px solid var(--line)', background: 'transparent', cursor: 'pointer', fontSize: 11.5, fontFamily: 'var(--serif)' }}>↻ 刷新</button>
        <button onClick={onBackfill} disabled={busy} className="hover-pill" style={{ padding: '5px 13px', border: 'none', background: busy ? 'var(--ink-3)' : 'var(--ink)', color: 'var(--paper)', cursor: busy ? 'default' : 'pointer', fontSize: 11.5, fontFamily: 'var(--serif)' }}>{busy ? '回填中…' : '⟳ 复盘回填'}</button>
      </div>
      {err && <div style={{ marginBottom: 12 }}><ErrorBox error={err} /></div>}

      {/* overall 命中率 KPIs */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(5,1fr)', border: '1px solid var(--line-soft)', marginBottom: 18 }}>
        <Kpi label="已评样本" value={ov.n} />
        <Kpi label="命中率" value={pct(ov.win_rate)} dir={ov.win_rate >= 0.5 ? 'up' : 'down'} />
        <Kpi label="命中/部分/错" value={ov.correct + '/' + ov.partial + '/' + ov.wrong} />
        <Kpi label="均 T+1" value={pct(ov.avg_return_t1)} dir={ov.avg_return_t1 >= 0 ? 'up' : 'down'} />
        <Kpi label="均 T+5" value={pct(ov.avg_return_t5)} dir={ov.avg_return_t5 >= 0 ? 'up' : 'down'} last />
      </div>

      {/* by_trigger + by_action 两张表 */}
      <div style={{ display: 'flex', gap: 18, marginBottom: 18, flexWrap: 'wrap' }}>
        {[['按触发类型', byTrig], ['按操作', byAct]].map(([title, m]) => (
          <div key={title} style={{ flex: 1, minWidth: 280 }}>
            <Panel title={title}>
              <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 11.5 }}>
                <thead><tr style={{ color: 'var(--ink-3)', fontSize: 10 }}>
                  <td style={{ padding: '4px 8px' }}>类型</td>
                  <td style={{ padding: '4px 8px', textAlign: 'right' }}>样本</td>
                  <td style={{ padding: '4px 8px', textAlign: 'right' }}>命中率</td>
                  <td style={{ padding: '4px 8px', textAlign: 'right' }}>均 T+5</td>
                </tr></thead>
                <tbody>
                  {Object.keys(m).length === 0 && <tr><td colSpan={4} style={{ padding: '10px 8px', color: 'var(--ink-3)', textAlign: 'center' }}>暂无数据</td></tr>}
                  {Object.entries(m).map(([k, s]) => <StatRow key={k} k={k} s={s} />)}
                </tbody>
              </table>
            </Panel>
          </div>
        ))}
      </div>

      {/* 推荐历史 */}
      <Panel title={<span>推荐历史 <span className="mono" style={{ fontSize: 9, color: 'var(--ink-3)', marginLeft: 6 }}>{rows.length} 条 · 新→旧</span></span>}>
        {!rows.length && <Empty label="暂无推荐历史 · 盯盘产生推荐后, 盘后点「复盘回填」打分" />}
        {!!rows.length && (
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 11.5 }}>
            <thead><tr style={{ color: 'var(--ink-3)', fontSize: 10 }}>
              <td style={{ padding: '5px 8px' }}>时间</td>
              <td style={{ padding: '5px 8px' }}>代码</td>
              <td style={{ padding: '5px 8px' }}>操作</td>
              <td style={{ padding: '5px 8px' }}>触发</td>
              <td style={{ padding: '5px 8px', textAlign: 'right' }}>T+1</td>
              <td style={{ padding: '5px 8px', textAlign: 'right' }}>T+5</td>
              <td style={{ padding: '5px 8px', textAlign: 'center' }}>评定</td>
              <td style={{ padding: '5px 8px', textAlign: 'center' }}>人工</td>
            </tr></thead>
            <tbody>
              {rows.map((r, i) => (
                <tr key={(r.ts || '') + '|' + (r.code || '') + '|' + i} style={{ borderTop: '1px solid var(--line-soft)' }}>
                  <td className="mono" style={{ padding: '5px 8px', fontSize: 10, color: 'var(--ink-3)' }}>{String(r.ts || '').slice(5, 16)}</td>
                  <td style={{ padding: '5px 8px' }}><code className="mono" style={{ fontSize: 11 }}>{r.code}</code></td>
                  <td className="mono" style={{ padding: '5px 8px' }}>{r.action}</td>
                  <td className="mono" style={{ padding: '5px 8px', fontSize: 10, color: 'var(--ink-3)' }}>{r.trigger_kind}</td>
                  <td style={{ padding: '5px 8px', textAlign: 'right' }}>{retCell(r.return_t1)}</td>
                  <td style={{ padding: '5px 8px', textAlign: 'right' }}>{retCell(r.return_t5)}</td>
                  <td style={{ padding: '5px 8px', textAlign: 'center', color: vColor(r.verdict) }}>{vLabel(r.verdict)}</td>
                  <td className="mono" style={{ padding: '5px 8px', textAlign: 'center', fontSize: 10, color: r.user_action === 'confirm' ? 'var(--zhu)' : r.user_action === 'ignore' ? 'var(--dai)' : 'var(--ink-3)' }}>{r.user_action === 'confirm' ? '✓确认' : r.user_action === 'ignore' ? '✗忽略' : '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Panel>
    </div>
  );
}

function WatchMode() {
  const [running, setRunning] = useState(false);
  const [items, setItems] = useState([]);          // [{code, avg_cost, stop_loss}]
  const [quotes, setQuotes] = useState({});        // {code: {price, changePercent, ...}}
  const [series, setSeries] = useState({});        // {code: [{ts, close}]} — quote_update 累积的分时
  const [history, setHistory] = useState({});      // {code: [{open,high,low,close,vol,trade_date}]} — /watch/bars 历史 5min K
  const [fired, setFired] = useState({});          // {code: lastTriggerKind} ⚡ 标记
  const [recs, setRecs] = useState([]);            // 推荐 feed (新的在前)
  const [sel, setSel] = useState('');
  const [draft, setDraft] = useState('');          // 新增 code 输入
  const [conn, setConn] = useState('idle');        // idle | open | error
  const [err, setErr] = useState('');
  const [tickCount, setTickCount] = useState(0);
  const esRef = useRef(null);
  // 复盘 (C): 'live' 盯盘三栏 | 'review' 命中率看板+推荐历史
  const [view, setView] = useState('live');
  const [hitrate, setHitrate] = useState(null);    // {overall, by_trigger, by_action}
  const [histRows, setHistRows] = useState([]);    // /watch/history rows (rec×outcome)
  const [reviewBusy, setReviewBusy] = useState(false);
  const [reviewErr, setReviewErr] = useState('');

  // 初始 status
  useEffect(() => {
    getJSON('/watch/status').then(s => {
      if (!s) return;
      setRunning(!!s.running);
      setItems(s.items || []);
      setTickCount(s.tick_count || 0);
      if (!sel && (s.items || []).length) setSel(s.items[0].code);
    }).catch(() => {});
  }, []);

  // running 翻 true 时挂 SSE; 翻 false / 卸载时拆。
  useEffect(() => {
    if (!running) {
      if (esRef.current) { esRef.current.close(); esRef.current = null; }
      setConn('idle');
      return;
    }
    const url = (window.GUANLAN_BACKEND || '') + '/watch/stream';
    let es;
    try { es = new EventSource(url); }
    catch (e) { setConn('error'); setErr('EventSource 创建失败: ' + (e.message || e)); return; }
    esRef.current = es;
    es.onopen = () => { setConn('open'); setErr(''); };
    es.addEventListener('quote_update', (ev) => {
      let d; try { d = JSON.parse(ev.data); } catch (e) { return; }
      const code = d.code; const qd = d.quote || {};
      if (!code) return;
      setQuotes(prev => ({ ...prev, [code]: qd }));
      const px = (qd.price !== undefined ? qd.price : qd.now);
      if (px !== undefined && px !== null && !(typeof px === 'number' && isNaN(px))) {
        setSeries(prev => {
          const arr = (prev[code] || []).concat([{ ts: d.ts, close: px }]);
          if (arr.length > 240) arr.splice(0, arr.length - 240);   // 上限一日 5min 根数
          return { ...prev, [code]: arr };
        });
      }
    });
    es.addEventListener('recommendation', (ev) => {
      let d; try { d = JSON.parse(ev.data); } catch (e) { return; }
      if (!d.code) return;
      const key = (d.ts || '') + '|' + d.code;
      setRecs(prev => [{ key, code: d.code, ts: d.ts, rec: d.rec || {}, ack: null }, ...prev].slice(0, 100));
      setFired(prev => ({ ...prev, [d.code]: (d.rec && d.rec.trigger_kind) || 'signal' }));
    });
    es.onerror = () => { setConn('error'); };   // 浏览器会自动重连; 仅标红状态
    return () => { es.close(); if (esRef.current === es) esRef.current = null; };
  }, [running]);

  // 选中股 → 拉一次历史 5min K (真 OHLC 蜡烛打底, 无需盯盘运行)。
  useEffect(() => {
    if (!sel || history[sel]) return;
    let cancelled = false;
    getJSON('/watch/bars?code=' + encodeURIComponent(sel) + '&n=240')
      .then(r => { if (!cancelled && r && r.ok) setHistory(prev => ({ ...prev, [sel]: r.bars || [] })); })
      .catch(() => {});
    return () => { cancelled = true; };
  }, [sel]);

  // 盯盘开始 → 刷新当前选中股的历史 K (拉到最新已收的 5min 根)。
  useEffect(() => {
    if (!running || !sel) return;
    getJSON('/watch/bars?code=' + encodeURIComponent(sel) + '&n=240')
      .then(r => { if (r && r.ok) setHistory(prev => ({ ...prev, [sel]: r.bars || [] })); })
      .catch(() => {});
  }, [running]);

  const parseCodes = (raw) => String(raw || '').split(/[\s,，、]+/).map(s => s.trim()).filter(Boolean);

  const start = async () => {
    setErr('');
    const codes = items.length ? items.map(it => it.code) : parseCodes(draft);
    if (!codes.length) { setErr('请先添加至少一只股票'); return; }
    try {
      const body = { items: codes.map(c => ({ code: c })) };
      const r = await postJSON('/watch/start', body);
      if (r && r.ok) { setRunning(true); setDraft(''); }
      else setErr((r && r.reason) || '启动失败');
    } catch (e) { setErr(e.message || String(e)); }
  };
  const stop = async () => {
    setErr('');
    try { await postJSON('/watch/stop', {}); } catch (e) { setErr(e.message || String(e)); }
    setRunning(false);
  };

  const addCode = async () => {
    const codes = parseCodes(draft);
    if (!codes.length) return;
    setDraft('');
    if (running) {
      for (const c of codes) {
        try {
          const r = await postJSON('/watch/item', { op: 'add', code: c });
          if (r && r.ok) setItems(r.items || ((prev) => prev));
        } catch (e) { /* swallow per-code */ }
      }
      // status 回读权威清单
      getJSON('/watch/status').then(s => { if (s) setItems(s.items || []); }).catch(() => {});
    } else {
      setItems(prev => {
        const have = new Set(prev.map(it => it.code));
        const add = codes.filter(c => !have.has(c)).map(c => ({ code: c }));
        return [...prev, ...add];
      });
    }
    if (!sel && codes.length) setSel(codes[0]);
  };
  const removeCode = async (code) => {
    if (running) {
      try { await postJSON('/watch/item', { op: 'remove', code }); } catch (e) { /* ignore */ }
      getJSON('/watch/status').then(s => { if (s) setItems(s.items || []); }).catch(() => {});
    } else {
      setItems(prev => prev.filter(it => it.code !== code));
    }
    if (sel === code) setSel('');
  };

  const ack = async (item, action) => {
    try {
      await postJSON('/watch/ack', { ts: item.ts, code: item.code, user_action: action });
    } catch (e) { /* still mark locally — UI optimistic */ }
    setRecs(prev => prev.map(r => (r.key === item.key ? { ...r, ack: action } : r)));
  };

  // 复盘: 拉命中率 + 推荐历史 (rec×outcome left-join)。
  const loadReview = async () => {
    setReviewErr('');
    try {
      const [h, hist] = await Promise.all([
        getJSON('/watch/hitrate'),
        getJSON('/watch/history?n=200'),
      ]);
      setHitrate((h && h.ok) ? h : null);
      setHistRows((hist && hist.ok && hist.rows) ? hist.rows : []);
    } catch (e) { setReviewErr(e.message || String(e)); }
  };
  // 复盘回填: 盘后给到期推荐打 T+1/T+5 outcome 分, 再刷新看板。
  const backfill = async () => {
    setReviewBusy(true); setReviewErr('');
    try {
      const r = await postJSON('/watch/outcome/backfill', {});
      if (!(r && r.ok)) setReviewErr((r && r.reason) || '回填失败');
    } catch (e) { setReviewErr(e.message || String(e)); }
    await loadReview();
    setReviewBusy(false);
  };
  // 切到复盘页 → 首次自动拉一次。
  useEffect(() => {
    if (view === 'review' && hitrate === null) loadReview();
  }, [view]);

  const connDot = conn === 'open' ? 'var(--zhu)' : conn === 'error' ? 'var(--dai)' : 'var(--ink-3)';
  const connTxt = conn === 'open' ? 'SSE 已连' : conn === 'error' ? 'SSE 断开 (重连中)' : '未连接';

  // 中栏蜡烛: 历史 5min K (真 OHLC) 打底 + 实时分时 (close-only) 延伸到末根之后。
  const histBars = (sel && history[sel]) || [];
  const liveBars = (sel && series[sel]) || [];
  const lastHistTs = histBars.length
    ? String(histBars[histBars.length - 1].trade_date || histBars[histBars.length - 1].ts || '')
    : '';
  const liveTail = liveBars
    .filter(p => !lastHistTs || String(p.ts || '') > lastHistTs)
    .map(p => ({ close: p.close, ts: p.ts }));
  const chartBars = histBars.length ? histBars.concat(liveTail) : liveBars;

  return (
    <div style={{ flex: 1, display: 'flex', flexDirection: 'column', minHeight: 0 }}>
      {/* 顶部控制条 */}
      <div style={{ padding: '10px 16px', borderBottom: '1px solid var(--line)', display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap', background: 'rgba(241,234,217,0.4)' }}>
        {!running ? (
          <button onClick={start} className="hover-pill"
            style={{ padding: '6px 16px', border: 'none', background: 'var(--ink)', color: 'var(--paper)', fontFamily: 'var(--serif)', fontSize: 12.5, cursor: 'pointer' }}>▶ 开始盯盘</button>
        ) : (
          <button onClick={stop} className="hover-pill"
            style={{ padding: '6px 16px', border: '1px solid var(--dai)', background: 'transparent', color: 'var(--yin)', fontFamily: 'var(--serif)', fontSize: 12.5, cursor: 'pointer' }}>■ 停止</button>
        )}
        <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
          <span style={{ width: 7, height: 7, borderRadius: '50%', background: connDot }} />
          <span className="mono" style={{ fontSize: 10.5, color: 'var(--ink-3)' }}>{connTxt}</span>
        </span>
        <span className="mono" style={{ fontSize: 10.5, color: 'var(--ink-3)' }}>{items.length} 只 · tick {tickCount}</span>
        <span style={{ display: 'inline-flex', border: '1px solid var(--line)', overflow: 'hidden' }}>
          {[['live', '盯盘'], ['review', '复盘']].map(([v, lbl]) => (
            <button key={v} onClick={() => setView(v)}
              style={{ padding: '4px 13px', border: 'none', cursor: 'pointer', fontFamily: 'var(--serif)', fontSize: 11.5,
                background: view === v ? 'var(--ink)' : 'transparent', color: view === v ? 'var(--paper)' : 'var(--ink-3)' }}>{lbl}</button>
          ))}
        </span>
        <span style={{ flex: 1 }} />
        <input value={draft} onChange={e => setDraft(e.target.value)} onKeyDown={e => { if (e.key === 'Enter') addCode(); }}
          placeholder="加股票代码 (600519 / SH600519, 逗号分隔)"
          style={{ width: 240, padding: '5px 9px', border: '1px solid var(--line)', fontFamily: 'var(--mono)', fontSize: 11.5, background: 'var(--paper)' }} />
        <button onClick={addCode} className="hover-pill" style={{ padding: '5px 11px', border: '1px solid var(--line)', background: 'transparent', cursor: 'pointer', fontSize: 11.5, fontFamily: 'var(--serif)' }}>+ 添加</button>
      </div>
      {err && <div style={{ padding: '8px 16px' }}><ErrorBox error={err} /></div>}

      {/* 盯盘三栏 (view==='live') */}
      {view === 'live' && (
      <div style={{ flex: 1, display: 'flex', minHeight: 0 }}>
        {/* 左: 自选列表 */}
        <aside style={{ width: 260, flexShrink: 0, borderRight: '1px solid var(--line)', overflowY: 'auto', background: 'rgba(241,234,217,0.25)' }}>
          <div style={{ padding: '9px 12px', borderBottom: '1px solid var(--line-soft)' }}>
            <span className="serif" style={{ fontSize: 12.5, color: 'var(--ink)', fontWeight: 500 }}>盯盘列表</span>
          </div>
          {!items.length && <Empty label="空 · 在上方添加股票后开始盯盘" />}
          {items.map(it => {
            const code = it.code;
            const qd = quotes[code] || {};
            const chg = (qd.changePercent !== undefined ? qd.changePercent : qd.changepercent);
            const dir = (chg > 0) ? 'up' : (chg < 0) ? 'down' : '';
            const px = (qd.price !== undefined ? qd.price : qd.now);
            return (
              <div key={code} className="hover-row" onClick={() => setSel(code)}
                style={{ padding: '8px 12px', cursor: 'pointer', borderBottom: '1px solid var(--line-soft)', background: sel === code ? 'rgba(28,24,20,0.07)' : 'transparent', display: 'flex', alignItems: 'center', gap: 8 }}>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                    <code className="mono" style={{ fontSize: 12, color: 'var(--ink)' }}>{code}</code>
                    {fired[code] && <span title={fired[code]} style={{ fontSize: 11, color: 'var(--jin)' }}>⚡</span>}
                  </div>
                  <div className="mono" style={{ fontSize: 9.5, color: 'var(--ink-3)' }}>{fired[code] || ''}</div>
                </div>
                <div style={{ textAlign: 'right' }}>
                  <div className={'mono ' + dir} style={{ fontSize: 12 }}>{n2(px, 2)}</div>
                  <div className={'mono ' + dir} style={{ fontSize: 10 }}>{(chg === undefined || chg === null) ? '—' : (chg > 0 ? '+' : '') + n2(chg, 2) + '%'}</div>
                </div>
                <span onClick={(e) => { e.stopPropagation(); removeCode(code); }} title="移除"
                  style={{ cursor: 'pointer', color: 'var(--yin)', opacity: 1, fontWeight: 600, fontSize: 13, lineHeight: 1 }}>×</span>
              </div>
            );
          })}
        </aside>

        {/* 中: 蜡烛 */}
        <div style={{ flex: 1, overflowY: 'auto', padding: 18, minWidth: 0 }}>
          <Panel title={<span>5min 蜡烛 <span className="mono" style={{ fontSize: 9, color: 'var(--ink-3)', marginLeft: 6 }}>历史 K线 + 实时分时延伸</span></span>}>
            <Candle code={sel} bars={chartBars} maWindow={5} />
            {sel && quotes[sel] && (
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4,1fr)', border: '1px solid var(--line-soft)', marginTop: 12 }}>
                <Kpi label="现价" value={n2(quotes[sel].price !== undefined ? quotes[sel].price : quotes[sel].now, 2)} />
                <Kpi label="涨跌%" value={n2(quotes[sel].changePercent, 2)} dir={(quotes[sel].changePercent > 0) ? 'up' : (quotes[sel].changePercent < 0) ? 'down' : ''} />
                <Kpi label="最高" value={n2(quotes[sel].high, 2)} />
                <Kpi label="最低" value={n2(quotes[sel].low, 2)} last />
              </div>
            )}
          </Panel>
        </div>

        {/* 右: 推荐 feed */}
        <aside style={{ width: 320, flexShrink: 0, borderLeft: '1px solid var(--line)', overflowY: 'auto', padding: 14, background: 'rgba(241,234,217,0.18)' }}>
          <div style={{ display: 'flex', alignItems: 'baseline', gap: 8, marginBottom: 10 }}>
            <span className="serif" style={{ fontSize: 12.5, color: 'var(--ink)', fontWeight: 500 }}>实时推荐</span>
            <span className="mono" style={{ fontSize: 9.5, color: 'var(--ink-3)' }}>{recs.length}</span>
          </div>
          {!recs.length && <Empty label={running ? '盯盘中 · 触发关键点时推送' : '开始盯盘后, 触发信号会在此推送'} />}
          {recs.map(item => <RecCard key={item.key} item={item} onAck={ack} />)}
        </aside>
      </div>
      )}

      {view === 'review' && (
        <WatchReview hitrate={hitrate} rows={histRows} busy={reviewBusy}
                     err={reviewErr} onBackfill={backfill} onReload={loadReview} />
      )}
    </div>
  );
}

// ═════════════════════════ 入口 ═════════════════════════
function QuantApp() {
  const [mode, setMode] = useState('lib');
  return (
    <div className="paper-bg" style={{
      width: '100%', height: '100vh', display: 'flex', flexDirection: 'column', overflow: 'hidden',
      fontFamily: 'var(--sans)', color: 'var(--ink)', background: 'var(--paper)',
    }}>
      <TopBar mode={mode} onMode={setMode} />
      <div style={{ flex: 1, display: 'flex', minHeight: 0, overflow: 'hidden' }}>
        {mode === 'lib' && <LibraryMode />}
        {mode === 'forge' && <ForgeMode />}
        {mode === 'compose' && <ComposeMode />}
        {mode === 'archive' && <ArchiveMode />}
        {mode === 'backtest' && <BacktestMode />}   {/* ← P5 新增 */}
        {mode === 'watch' && <WatchMode />}
      </div>
    </div>
  );
}

window.QuantApp = QuantApp;
window.WatchMode = WatchMode;   // 供 cockpit.html 复用 (P1 交易盯盘台)
