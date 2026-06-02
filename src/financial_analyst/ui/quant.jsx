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

// ═════════════════════════ TopBar (5 模式) ═════════════════════════
function TopBar({ mode, onMode }) {
  const tabs = [
    { k: 'lib', l: '因子库 & 详情' },
    { k: 'forge', l: '炼因子' },
    { k: 'compose', l: '多因子合成' },
    { k: 'archive', l: '研究档案' },
    { k: 'workflow', l: '工作流实验室' },
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

// ═════════════════════════ 工作流实验室 (第 5 模式) ═════════════════════════
// 设计 spec: docs/superpowers/specs/2026-06-02-workflow-lab-ui-design.md
// 后端 9 个 /workflow/* endpoints + SSE 流; 前端自写 AutoForm, 不引 rjsf.

// AutoForm — 读 JSON Schema 渲染 input. 支持: string / integer / number /
// boolean / array of string / enum (anyOf+const). object 嵌套**不支持** (节点
// params 都是平 dict, 撞到嵌套则 console.warn 提示).
// props: schema=JSON Schema (object), value=当前 dict, onChange(next dict).
function AutoForm({ schema, value, onChange }) {
  const props = (schema && schema.properties) || {};
  const required = new Set((schema && schema.required) || []);
  const keys = Object.keys(props);
  if (!keys.length) {
    return <div className="serif" style={{ fontSize: 12, color: 'var(--ink-3)', padding: '6px 0' }}>无参数</div>;
  }
  const setKey = (k, v) => onChange({ ...(value || {}), [k]: v });
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
      {keys.map(k => {
        const ps = props[k] || {};
        const cur = value ? value[k] : undefined;
        const lab = (
          <label key={k + '-lab'} className="mono" style={{ fontSize: 10, color: 'var(--ink-2)', letterSpacing: '0.12em', display: 'block' }}>
            {k}{required.has(k) ? ' *' : ''} <span style={{ color: 'var(--ink-3)' }}>· {ps.type || (ps.anyOf ? 'enum' : '?')}</span>
          </label>
        );
        // enum (anyOf with const) → select
        const enumVals = Array.isArray(ps.enum) ? ps.enum : (Array.isArray(ps.anyOf) ? ps.anyOf.map(x => x && x.const).filter(x => x !== undefined) : null);
        if (enumVals && enumVals.length) {
          return (
            <div key={k}>{lab}
              <select value={cur === undefined ? '' : String(cur)} onChange={e => setKey(k, e.target.value)}
                style={{ width: '100%', padding: '5px 8px', border: '1px solid var(--line)', fontFamily: 'var(--mono)', fontSize: 12, background: 'var(--paper)' }}>
                <option value="">— 选择 —</option>
                {enumVals.map(v => <option key={String(v)} value={String(v)}>{String(v)}</option>)}
              </select>
            </div>
          );
        }
        if (ps.type === 'boolean') {
          return (
            <div key={k} style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <input type="checkbox" checked={!!cur} onChange={e => setKey(k, e.target.checked)} style={{ cursor: 'pointer' }} />
              <span className="mono" style={{ fontSize: 11, color: 'var(--ink-2)' }}>{k}{required.has(k) ? ' *' : ''}</span>
            </div>
          );
        }
        if (ps.type === 'integer' || ps.type === 'number') {
          const step = ps.type === 'integer' ? 1 : 'any';
          return (
            <div key={k}>{lab}
              <input type="number" step={step} value={cur === undefined || cur === null ? '' : cur}
                onChange={e => {
                  const raw = e.target.value;
                  if (raw === '') { setKey(k, undefined); return; }
                  const num = ps.type === 'integer' ? parseInt(raw, 10) : parseFloat(raw);
                  setKey(k, isNaN(num) ? undefined : num);
                }}
                style={{ width: '100%', padding: '5px 8px', border: '1px solid var(--line)', fontFamily: 'var(--mono)', fontSize: 12, background: 'var(--paper)' }} />
            </div>
          );
        }
        if (ps.type === 'array' && ps.items && (ps.items.type === 'string' || !ps.items.type)) {
          // array<string> → textarea, 一行一个
          const arr = Array.isArray(cur) ? cur : [];
          return (
            <div key={k}>{lab}
              <textarea rows={Math.max(3, Math.min(8, arr.length + 1))} value={arr.join('\n')}
                onChange={e => setKey(k, e.target.value.split('\n').map(s => s.trim()).filter(s => s.length > 0))}
                placeholder="一行一个"
                style={{ width: '100%', padding: '6px 8px', border: '1px solid var(--line)', fontFamily: 'var(--mono)', fontSize: 11.5, background: 'var(--paper)', resize: 'vertical' }} />
              <div className="mono" style={{ fontSize: 9.5, color: 'var(--ink-3)', marginTop: 2 }}>当前 {arr.length} 项</div>
            </div>
          );
        }
        if (ps.type === 'object') {
          console.warn('[AutoForm] 嵌套 object 不支持 (key=' + k + '), 显示只读 JSON.');
          return (
            <div key={k}>{lab}
              <pre className="mono" style={{ fontSize: 10.5, padding: '6px 8px', border: '1px dashed var(--line)', background: 'rgba(28,24,20,0.03)', margin: 0, color: 'var(--ink-3)' }}>
                嵌套 object 暂不支持编辑 · {JSON.stringify(cur || ps.default || {})}
              </pre>
            </div>
          );
        }
        // string fallback
        return (
          <div key={k}>{lab}
            <input type="text" value={cur === undefined || cur === null ? '' : String(cur)}
              onChange={e => setKey(k, e.target.value)}
              placeholder={ps.description || ''}
              style={{ width: '100%', padding: '5px 8px', border: '1px solid var(--line)', fontFamily: 'var(--mono)', fontSize: 12, background: 'var(--paper)' }} />
            {ps.description && <div className="serif" style={{ fontSize: 10.5, color: 'var(--ink-3)', marginTop: 2 }}>{ps.description}</div>}
          </div>
        );
      })}
    </div>
  );
}

// InputsEditor — node.inputs 是 {input_name: "<prev_node_id>.output"} 形式.
// 不靠 schema (registry 没暴露 input 名), 让用户自己加 key 并从下拉选上游节点.
// 自动 edge: 加新节点时, 若已有 ≥1 节点, 默认 inputs={ <type 末段>: <prev>.output }.
function InputsEditor({ inputs, upstreamIds, onChange }) {
  const [newKey, setNewKey] = useState('');
  const entries = Object.entries(inputs || {});
  const setEntry = (k, v) => onChange({ ...(inputs || {}), [k]: v });
  const renameEntry = (oldK, newK) => {
    if (!newK || newK === oldK || (inputs && Object.prototype.hasOwnProperty.call(inputs, newK))) return;
    const next = {};
    Object.entries(inputs || {}).forEach(([k, v]) => { next[k === oldK ? newK : k] = v; });
    onChange(next);
  };
  const removeEntry = (k) => {
    const next = { ...(inputs || {}) };
    delete next[k];
    onChange(next);
  };
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
      {entries.length === 0 && (
        <div className="serif" style={{ fontSize: 12, color: 'var(--ink-3)', padding: '4px 0' }}>
          {upstreamIds.length ? '无上游连线 (若节点需要 inputs 请在此添加)' : '无上游节点'}
        </div>
      )}
      {entries.map(([k, v]) => (
        <div key={k} style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
          <input type="text" defaultValue={k} onBlur={e => renameEntry(k, e.target.value.trim())}
            placeholder="input_name" title="input key (失焦时改名)"
            style={{ flex: '0 0 110px', padding: '4px 6px', border: '1px solid var(--line)', fontFamily: 'var(--mono)', fontSize: 11.5, background: 'var(--paper)' }} />
          <span className="mono" style={{ fontSize: 11, color: 'var(--ink-3)' }}>=</span>
          <select value={v} onChange={e => setEntry(k, e.target.value)}
            style={{ flex: 1, padding: '4px 6px', border: '1px solid var(--line)', fontFamily: 'var(--mono)', fontSize: 11.5, background: 'var(--paper)' }}>
            {!upstreamIds.includes((v || '').split('.')[0]) && <option value={v}>{v}</option>}
            {upstreamIds.map(u => <option key={u} value={u + '.output'}>{u}.output</option>)}
          </select>
          <button onClick={() => removeEntry(k)} title="删除连线" className="hover-pill"
            style={{ padding: '2px 6px', border: '1px solid var(--line)', background: 'transparent', cursor: 'pointer', fontSize: 11, color: 'var(--yin)' }}>×</button>
        </div>
      ))}
      {upstreamIds.length > 0 && (
        <div style={{ display: 'flex', gap: 6, alignItems: 'center', marginTop: 4 }}>
          <input type="text" value={newKey} onChange={e => setNewKey(e.target.value)}
            onKeyDown={e => { if (e.key === 'Enter' && newKey.trim()) { setEntry(newKey.trim(), upstreamIds[upstreamIds.length - 1] + '.output'); setNewKey(''); } }}
            placeholder="+ 新 input key, 回车确认"
            style={{ flex: 1, padding: '4px 6px', border: '1px dashed var(--line)', fontFamily: 'var(--mono)', fontSize: 11.5, background: 'transparent' }} />
        </div>
      )}
    </div>
  );
}

function WorkflowLab() {
  const [nodes, setNodes] = useState([]);                      // 工具栏可用节点
  const [nodesErr, setNodesErr] = useState('');
  const [savedList, setSavedList] = useState([]);              // 历史 workflow 列表
  const [savedErr, setSavedErr] = useState('');
  const [runsList, setRunsList] = useState([]);                // 运行历史
  const [currentWorkflow, setCurrentWorkflow] = useState({ wf_id: null, name: '未命名工作流', nodes: [], edges: [], meta: {} });
  const [selectedNodeIdx, setSelectedNodeIdx] = useState(null);
  const [draftParams, setDraftParams] = useState({});          // 编辑中 params, Apply 才提交
  const [draftInputs, setDraftInputs] = useState({});
  const [runId, setRunId] = useState(null);
  const [runEvents, setRunEvents] = useState([]);              // SSE 事件累积
  const [runStatus, setRunStatus] = useState('idle');          // idle | running | done | error
  const [nodeRunStatus, setNodeRunStatus] = useState({});      // {node_id: 'running'|'success'|'failed'|'skipped'}
  const [saving, setSaving] = useState(false);
  const [saveErr, setSaveErr] = useState('');
  const [artifactView, setArtifactView] = useState(null);      // {node_id, kind, ...}
  const [artifactErr, setArtifactErr] = useState('');
  const esRef = useRef(null);
  const logEndRef = useRef(null);

  // 拉工具栏 + 历史 workflow + 运行历史
  const reloadAll = useCallback(() => {
    getJSON('/workflow/nodes').then(d => { setNodes((d && d.nodes) || []); setNodesErr(''); }).catch(e => setNodesErr(e.message || String(e)));
    getJSON('/workflow').then(d => { setSavedList((d && d.workflows) || []); setSavedErr(''); }).catch(e => setSavedErr(e.message || String(e)));
    getJSON('/workflow/runs?limit=20').then(d => setRunsList((d && d.runs) || [])).catch(() => {});
  }, []);
  useEffect(() => { reloadAll(); }, [reloadAll]);

  // 选中节点变了 → reset 草稿 (避免上一节点的草稿污染)
  useEffect(() => {
    if (selectedNodeIdx === null || !currentWorkflow.nodes[selectedNodeIdx]) {
      setDraftParams({}); setDraftInputs({}); return;
    }
    const node = currentWorkflow.nodes[selectedNodeIdx];
    setDraftParams(node.params ? { ...node.params } : {});
    setDraftInputs(node.inputs ? { ...node.inputs } : {});
  }, [selectedNodeIdx, currentWorkflow]);

  // 工具栏分组 (按 type 第一段: data / factor / eval)
  const groupedNodes = useMemo(() => {
    const g = {};
    (nodes || []).forEach(n => {
      const seg = (n.type || '').split('.')[0] || 'misc';
      if (!g[seg]) g[seg] = [];
      g[seg].push(n);
    });
    return g;
  }, [nodes]);

  const nodeByType = useMemo(() => {
    const m = {};
    (nodes || []).forEach(n => { m[n.type] = n; });
    return m;
  }, [nodes]);

  // 添加节点到 step list (自动 wire 第一个 inputs key → prev.output)
  const addNode = (nodeMeta) => {
    const seg = (nodeMeta.type || '').split('.')[1] || nodeMeta.type.replace(/\./g, '_') || 'node';
    // 唯一 id: 用 type 末段 + index, 撞名加后缀
    let baseId = seg, idx = 0;
    const existingIds = new Set(currentWorkflow.nodes.map(n => n.id));
    while (existingIds.has(baseId + (idx ? '_' + idx : ''))) idx++;
    const newId = baseId + (idx ? '_' + idx : '');
    // 自动 edge: 若已有节点, 默认把第一个 input 接到上一节点 output
    // 节点 inputs 的 key 名不在 schema 里 (registry 没暴露), 用 type 第一段作 placeholder
    // (用户必要时在右栏改). data.* 通常无 inputs, factor/eval 多需要.
    const inputs = {};
    if (currentWorkflow.nodes.length > 0 && seg !== 'constant_universe' && (nodeMeta.type || '').split('.')[0] !== 'data') {
      const prevId = currentWorkflow.nodes[currentWorkflow.nodes.length - 1].id;
      // 用 prev 节点 type 第一段作默认 input key (e.g. universe / frame)
      const prevType = currentWorkflow.nodes[currentWorkflow.nodes.length - 1].type || '';
      const prevSeg = prevType.split('.')[1] || prevType.split('.')[0];
      // 启发式: factor.* 收 universe, eval.* 收 frame
      const inputKey = (nodeMeta.type || '').startsWith('factor.') ? 'universe' : ((nodeMeta.type || '').startsWith('eval.') ? 'frame' : (prevSeg || 'input'));
      inputs[inputKey] = prevId + '.output';
    }
    const newNode = { id: newId, type: nodeMeta.type, params: {}, inputs };
    setCurrentWorkflow({ ...currentWorkflow, wf_id: null, nodes: [...currentWorkflow.nodes, newNode] });
    setSelectedNodeIdx(currentWorkflow.nodes.length);
  };

  // 上下移动
  const moveNode = (i, dir) => {
    const j = i + dir;
    if (j < 0 || j >= currentWorkflow.nodes.length) return;
    const ns = [...currentWorkflow.nodes];
    [ns[i], ns[j]] = [ns[j], ns[i]];
    setCurrentWorkflow({ ...currentWorkflow, wf_id: null, nodes: ns });
    if (selectedNodeIdx === i) setSelectedNodeIdx(j);
    else if (selectedNodeIdx === j) setSelectedNodeIdx(i);
  };

  // 删除节点 + 解绑所有引用它的 inputs
  const removeNode = (i) => {
    const removed = currentWorkflow.nodes[i];
    if (!removed) return;
    const ns = currentWorkflow.nodes.filter((_, k) => k !== i).map(n => {
      const newInputs = {};
      Object.entries(n.inputs || {}).forEach(([k, v]) => {
        if (!((v || '').startsWith(removed.id + '.'))) newInputs[k] = v;
      });
      return { ...n, inputs: newInputs };
    });
    setCurrentWorkflow({ ...currentWorkflow, wf_id: null, nodes: ns });
    if (selectedNodeIdx === i) setSelectedNodeIdx(null);
    else if (selectedNodeIdx !== null && selectedNodeIdx > i) setSelectedNodeIdx(selectedNodeIdx - 1);
  };

  // Apply 草稿 → currentWorkflow.nodes[i]
  const applyDraft = () => {
    if (selectedNodeIdx === null) return;
    const ns = [...currentWorkflow.nodes];
    ns[selectedNodeIdx] = { ...ns[selectedNodeIdx], params: draftParams, inputs: draftInputs };
    setCurrentWorkflow({ ...currentWorkflow, wf_id: null, nodes: ns });
  };

  // Save workflow → POST /workflow/create
  const saveWorkflow = async () => {
    if (!currentWorkflow.nodes.length) { setSaveErr('请先加节点'); return; }
    setSaving(true); setSaveErr('');
    try {
      const payload = {
        name: currentWorkflow.name || '未命名工作流',
        nodes: currentWorkflow.nodes,
        edges: currentWorkflow.edges || [],
        meta: currentWorkflow.meta || {},
      };
      const d = await postJSON('/workflow/create', payload);
      if (d && d.wf_id) {
        setCurrentWorkflow({ ...currentWorkflow, wf_id: d.wf_id });
        // 刷历史列表
        getJSON('/workflow').then(d2 => setSavedList((d2 && d2.workflows) || [])).catch(() => {});
      }
    } catch (e) {
      setSaveErr(e.message || String(e));
    } finally {
      setSaving(false);
    }
  };

  // 加载历史 workflow
  const loadWorkflow = async (wf_id) => {
    try {
      const wf = await getJSON('/workflow/' + encodeURIComponent(wf_id));
      setCurrentWorkflow({
        wf_id: wf.id || wf_id,
        name: wf.name || wf_id,
        nodes: wf.nodes || [],
        edges: wf.edges || [],
        meta: wf.meta || {},
      });
      setSelectedNodeIdx(null);
      setNodeRunStatus({});
      setRunEvents([]);
      setRunId(null);
      setRunStatus('idle');
      setArtifactView(null);
    } catch (e) {
      setSaveErr('加载失败: ' + (e.message || String(e)));
    }
  };

  // Run workflow → POST /workflow/{wf_id}/run, EventSource 订阅 stream
  const runWorkflow = async () => {
    if (!currentWorkflow.wf_id) { setSaveErr('请先 Save'); return; }
    // 关上一个 stream
    if (esRef.current) { try { esRef.current.close(); } catch (e) {} esRef.current = null; }
    setRunEvents([]); setNodeRunStatus({}); setRunStatus('running'); setArtifactView(null); setSaveErr('');
    try {
      const d = await postJSON('/workflow/' + encodeURIComponent(currentWorkflow.wf_id) + '/run', {});
      const newRunId = d && d.run_id;
      if (!newRunId) { setRunStatus('error'); setSaveErr('Run 启动失败: 无 run_id'); return; }
      setRunId(newRunId);
      const es = new EventSource(API + '/workflow/runs/' + encodeURIComponent(newRunId) + '/stream');
      esRef.current = es;
      es.addEventListener('node_start', (e) => {
        try {
          const data = JSON.parse(e.data);
          setRunEvents(evts => [...evts, { kind: 'node_start', ts: Date.now(), ...data }]);
          setNodeRunStatus(s => ({ ...s, [data.node_id]: 'running' }));
        } catch (err) { /* swallow */ }
      });
      es.addEventListener('node_done', (e) => {
        try {
          const data = JSON.parse(e.data);
          setRunEvents(evts => [...evts, { kind: 'node_done', ts: Date.now(), ...data }]);
          setNodeRunStatus(s => ({ ...s, [data.node_id]: data.status }));
        } catch (err) { /* swallow */ }
      });
      es.addEventListener('workflow_done', (e) => {
        try {
          const data = JSON.parse(e.data);
          setRunEvents(evts => [...evts, { kind: 'workflow_done', ts: Date.now(), ...data }]);
          setRunStatus(data.status === 'success' ? 'done' : (data.status || 'done'));
        } catch (err) { /* swallow */ }
        try { es.close(); } catch (err) {}
        esRef.current = null;
        // 刷运行历史
        getJSON('/workflow/runs?limit=20').then(d => setRunsList((d && d.runs) || [])).catch(() => {});
      });
      es.addEventListener('error', (e) => {
        let data = null;
        try { data = JSON.parse(e.data); } catch (err) {}
        setRunEvents(evts => [...evts, { kind: 'error', ts: Date.now(), message: (data && data.message) || 'SSE error' }]);
        setRunStatus('error');
        try { es.close(); } catch (err) {}
        esRef.current = null;
      });
    } catch (e) {
      setRunStatus('error'); setSaveErr('Run 失败: ' + (e.message || String(e)));
    }
  };

  // 清理 EventSource
  useEffect(() => () => { if (esRef.current) { try { esRef.current.close(); } catch (e) {} } }, []);

  // 日志区自动滚到底
  useEffect(() => {
    if (logEndRef.current) logEndRef.current.scrollIntoView({ behavior: 'smooth', block: 'end' });
  }, [runEvents.length]);

  // 看 artifact
  const viewArtifact = async (node_id) => {
    if (!runId) return;
    setArtifactErr(''); setArtifactView({ node_id, loading: true });
    try {
      const d = await getJSON('/workflow/runs/' + encodeURIComponent(runId) + '/artifacts/' + encodeURIComponent(node_id));
      setArtifactView({ node_id, ...d });
    } catch (e) {
      setArtifactErr(e.message || String(e)); setArtifactView({ node_id, error: true });
    }
  };

  const selectedNode = selectedNodeIdx !== null ? currentWorkflow.nodes[selectedNodeIdx] : null;
  const selectedNodeMeta = selectedNode ? nodeByType[selectedNode.type] : null;
  const upstreamIds = selectedNodeIdx !== null ? currentWorkflow.nodes.slice(0, selectedNodeIdx).map(n => n.id) : [];

  // node status 渲染色
  const statusColor = (s) => s === 'success' ? 'var(--dai)' : s === 'failed' ? 'var(--yin)' : s === 'running' ? 'var(--jin)' : s === 'skipped' ? 'var(--ink-3)' : 'var(--ink-3)';
  const statusIcon = (s) => s === 'success' ? '✓' : s === 'failed' ? '✗' : s === 'running' ? '▶' : s === 'skipped' ? '⊘' : '·';

  return (
    <div style={{ flex: 1, display: 'flex', flexDirection: 'column', minHeight: 0, minWidth: 0 }}>
      {/* 顶部工具条 — name + 历史选择 + reload */}
      <div style={{ padding: '10px 18px 8px', borderBottom: '1px solid var(--line-soft)', display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap', background: 'rgba(241,234,217,0.35)' }}>
        <span className="serif" style={{ fontSize: 13, color: 'var(--ink)', fontWeight: 500 }}>工作流</span>
        <input type="text" value={currentWorkflow.name} onChange={e => setCurrentWorkflow({ ...currentWorkflow, name: e.target.value, wf_id: null })}
          placeholder="工作流名"
          style={{ flex: '0 1 240px', padding: '4px 8px', border: '1px solid var(--line)', fontFamily: 'var(--serif)', fontSize: 12, background: 'var(--paper)' }} />
        {currentWorkflow.wf_id && <code className="mono" style={{ fontSize: 10, color: 'var(--ink-3)' }}>{currentWorkflow.wf_id}</code>}
        <span style={{ flex: 1 }} />
        <select value={currentWorkflow.wf_id || ''} onChange={e => { if (e.target.value) loadWorkflow(e.target.value); }}
          style={{ padding: '4px 8px', border: '1px solid var(--line)', fontFamily: 'var(--mono)', fontSize: 11, background: 'var(--paper)' }}>
          <option value="">— 选历史工作流 —</option>
          {savedList.map(w => <option key={w.wf_id} value={w.wf_id}>{w.name} · {w.node_count} 节点</option>)}
        </select>
        <button onClick={reloadAll} className="hover-pill" style={{ padding: '4px 10px', border: '1px solid var(--line)', background: 'transparent', cursor: 'pointer', fontFamily: 'var(--serif)', fontSize: 11 }}>刷新 ↻</button>
        <button onClick={() => { setCurrentWorkflow({ wf_id: null, name: '未命名工作流', nodes: [], edges: [], meta: {} }); setSelectedNodeIdx(null); setRunEvents([]); setNodeRunStatus({}); setRunId(null); setRunStatus('idle'); setArtifactView(null); }}
          className="hover-pill" style={{ padding: '4px 10px', border: '1px solid var(--line)', background: 'transparent', cursor: 'pointer', fontFamily: 'var(--serif)', fontSize: 11 }}>新建</button>
      </div>

      {/* 3 列 grid */}
      <div style={{ flex: 1, display: 'grid', gridTemplateColumns: '220px 1fr 320px', minHeight: 0 }}>
        {/* ── 列 1: 节点工具栏 ── */}
        <aside style={{ borderRight: '1px solid var(--line)', overflowY: 'auto', background: 'rgba(241,234,217,0.25)', minHeight: 0 }}>
          <div style={{ padding: '10px 12px 8px', borderBottom: '1px solid var(--line-soft)' }}>
            <div className="mono" style={{ fontSize: 9, color: 'var(--ink-3)', letterSpacing: '0.18em' }}>NODE TOOLBOX</div>
          </div>
          {nodesErr && <div style={{ padding: 10 }}><ErrorBox error={nodesErr} /></div>}
          {!nodesErr && nodes.length === 0 && <Empty label="加载中…" />}
          {Object.keys(groupedNodes).sort().map(grp => (
            <div key={grp} style={{ borderBottom: '1px solid var(--line-soft)' }}>
              <div className="mono" style={{ fontSize: 10, padding: '6px 12px 4px', color: 'var(--ink-2)', letterSpacing: '0.14em', background: 'rgba(28,24,20,0.04)' }}>{grp.toUpperCase()}</div>
              {groupedNodes[grp].map(n => (
                <div key={n.type} className="hover-row" onClick={() => addNode(n)}
                  title={n.description || '点击加到 step list'}
                  style={{ padding: '7px 12px', cursor: 'pointer', display: 'flex', flexDirection: 'column', gap: 2 }}>
                  <code className="mono" style={{ fontSize: 11.5, color: 'var(--ink)' }}>{(n.type.split('.')[1] || n.type)}</code>
                  <div className="serif" style={{ fontSize: 10, color: 'var(--ink-3)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{n.description || '—'}</div>
                </div>
              ))}
            </div>
          ))}
        </aside>

        {/* ── 列 2: Step List ── */}
        <main style={{ borderRight: '1px solid var(--line)', overflowY: 'auto', minHeight: 0, display: 'flex', flexDirection: 'column' }}>
          <div style={{ padding: '10px 14px 8px', borderBottom: '1px solid var(--line-soft)', display: 'flex', alignItems: 'center', gap: 10 }}>
            <div className="mono" style={{ fontSize: 9, color: 'var(--ink-3)', letterSpacing: '0.18em' }}>STEP LIST · {currentWorkflow.nodes.length} 节点</div>
            <span style={{ flex: 1 }} />
            <button onClick={saveWorkflow} disabled={saving || !currentWorkflow.nodes.length}
              className="hover-pill" style={{ padding: '5px 14px', border: 'none', background: currentWorkflow.nodes.length ? 'var(--ink)' : 'var(--line)', color: 'var(--paper)', fontFamily: 'var(--serif)', fontSize: 11, cursor: currentWorkflow.nodes.length ? 'pointer' : 'default' }}>
              {saving ? '保存中…' : (currentWorkflow.wf_id ? 'Save as new' : 'Save')}
            </button>
            <button onClick={runWorkflow} disabled={!currentWorkflow.wf_id || runStatus === 'running'}
              className="hover-pill" style={{ padding: '5px 14px', border: 'none', background: (currentWorkflow.wf_id && runStatus !== 'running') ? 'var(--yin)' : 'var(--line)', color: 'var(--paper)', fontFamily: 'var(--serif)', fontSize: 11, cursor: (currentWorkflow.wf_id && runStatus !== 'running') ? 'pointer' : 'default' }}>
              {runStatus === 'running' ? '运行中…' : 'Run ▶'}
            </button>
          </div>
          {saveErr && <div style={{ padding: '8px 12px' }}><ErrorBox error={saveErr} /></div>}
          <div style={{ flex: 1, overflowY: 'auto', padding: '8px 0', minHeight: 0 }}>
            {!currentWorkflow.nodes.length && <Empty label="左侧点击节点 → 加到此处" />}
            {currentWorkflow.nodes.map((n, i) => {
              const status = nodeRunStatus[n.id];
              const isSel = selectedNodeIdx === i;
              return (
                <div key={n.id + '_' + i} onClick={() => setSelectedNodeIdx(i)}
                  className="hover-row"
                  style={{ padding: '8px 14px', cursor: 'pointer', borderBottom: '1px solid var(--line-soft)', background: isSel ? 'rgba(28,24,20,0.06)' : 'transparent', borderLeft: isSel ? '2px solid var(--ink)' : '2px solid transparent' }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                    <span className="mono" style={{ fontSize: 14, color: statusColor(status), width: 16, flexShrink: 0, textAlign: 'center' }}>{statusIcon(status)}</span>
                    <span className="mono" style={{ fontSize: 10, color: 'var(--ink-3)', width: 22, flexShrink: 0 }}>{i + 1}.</span>
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <div style={{ display: 'flex', gap: 6, alignItems: 'baseline' }}>
                        <code className="mono" style={{ fontSize: 12, color: 'var(--ink)' }}>{n.id}</code>
                        <span className="serif" style={{ fontSize: 10.5, color: 'var(--ink-3)' }}>· {n.type}</span>
                      </div>
                      {Object.keys(n.inputs || {}).length > 0 && (
                        <div className="mono" style={{ fontSize: 9.5, color: 'var(--ink-3)', marginTop: 2 }}>
                          ← {Object.entries(n.inputs).map(([k, v]) => k + '=' + v).join(', ')}
                        </div>
                      )}
                    </div>
                    {status && status !== 'running' && (
                      <button onClick={e => { e.stopPropagation(); viewArtifact(n.id); }}
                        className="hover-pill" style={{ padding: '2px 7px', border: '1px solid var(--line)', background: 'transparent', cursor: 'pointer', fontSize: 10, color: 'var(--ink-2)' }}>artifact</button>
                    )}
                    <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
                      <button onClick={e => { e.stopPropagation(); moveNode(i, -1); }} disabled={i === 0}
                        title="上移" style={{ padding: 0, width: 18, height: 14, border: '1px solid var(--line)', background: 'transparent', cursor: i === 0 ? 'default' : 'pointer', fontSize: 9, lineHeight: 1, opacity: i === 0 ? 0.3 : 1 }}>↑</button>
                      <button onClick={e => { e.stopPropagation(); moveNode(i, +1); }} disabled={i === currentWorkflow.nodes.length - 1}
                        title="下移" style={{ padding: 0, width: 18, height: 14, border: '1px solid var(--line)', background: 'transparent', cursor: i === currentWorkflow.nodes.length - 1 ? 'default' : 'pointer', fontSize: 9, lineHeight: 1, opacity: i === currentWorkflow.nodes.length - 1 ? 0.3 : 1 }}>↓</button>
                    </div>
                    <button onClick={e => { e.stopPropagation(); removeNode(i); }} title="删除"
                      style={{ padding: '0 6px', height: 22, border: '1px solid var(--line)', background: 'transparent', cursor: 'pointer', color: 'var(--yin)', fontSize: 11 }}>×</button>
                  </div>
                </div>
              );
            })}
          </div>
        </main>

        {/* ── 列 3: 参数表单 ── */}
        <aside style={{ overflowY: 'auto', background: 'rgba(241,234,217,0.18)', minHeight: 0 }}>
          <div style={{ padding: '10px 14px 8px', borderBottom: '1px solid var(--line-soft)' }}>
            <div className="mono" style={{ fontSize: 9, color: 'var(--ink-3)', letterSpacing: '0.18em' }}>NODE INSPECTOR</div>
          </div>
          {!selectedNode && <Empty label="选中 Step List 节点编辑参数" />}
          {selectedNode && (
            <div style={{ padding: 14, display: 'flex', flexDirection: 'column', gap: 14 }}>
              <div>
                <div className="mono" style={{ fontSize: 10, color: 'var(--ink-3)', letterSpacing: '0.14em' }}>ID <span style={{ color: 'var(--ink-3)' }}>(失焦提交)</span></div>
                <input type="text" key={selectedNode.id + '_idinput'} defaultValue={selectedNode.id}
                  onBlur={e => {
                    const newId = (e.target.value || '').trim();
                    const oldId = selectedNode.id;
                    if (!newId || newId === oldId) { e.target.value = oldId; return; }
                    if (currentWorkflow.nodes.some(n => n.id === newId)) { e.target.value = oldId; return; }
                    const ns = [...currentWorkflow.nodes];
                    ns[selectedNodeIdx] = { ...ns[selectedNodeIdx], id: newId };
                    // 重写下游 inputs 中引用了 oldId 的地方
                    for (let k = selectedNodeIdx + 1; k < ns.length; k++) {
                      const newInputs = {};
                      Object.entries(ns[k].inputs || {}).forEach(([kk, vv]) => {
                        newInputs[kk] = (vv || '').startsWith(oldId + '.') ? newId + '.' + (vv.split('.').slice(1).join('.')) : vv;
                      });
                      ns[k] = { ...ns[k], inputs: newInputs };
                    }
                    setCurrentWorkflow({ ...currentWorkflow, wf_id: null, nodes: ns });
                  }}
                  style={{ width: '100%', padding: '4px 8px', border: '1px solid var(--line)', fontFamily: 'var(--mono)', fontSize: 12, background: 'var(--paper)', marginTop: 3 }} />
                <div className="serif" style={{ fontSize: 11, color: 'var(--ink-2)', marginTop: 6 }}>{selectedNode.type}</div>
                {selectedNodeMeta && selectedNodeMeta.description && (
                  <div className="serif" style={{ fontSize: 11, color: 'var(--ink-3)', marginTop: 4, lineHeight: 1.5 }}>{selectedNodeMeta.description}</div>
                )}
              </div>

              <div>
                <div className="mono" style={{ fontSize: 10, color: 'var(--ink-3)', letterSpacing: '0.14em', marginBottom: 8 }}>UPSTREAM INPUTS</div>
                <InputsEditor inputs={draftInputs} upstreamIds={upstreamIds} onChange={setDraftInputs} />
              </div>

              <div>
                <div className="mono" style={{ fontSize: 10, color: 'var(--ink-3)', letterSpacing: '0.14em', marginBottom: 8 }}>PARAMS</div>
                {selectedNodeMeta ? <AutoForm schema={selectedNodeMeta.params_schema || {}} value={draftParams} onChange={setDraftParams} /> : <Empty label="无 schema" />}
              </div>

              <button onClick={applyDraft} className="hover-pill"
                style={{ padding: '7px 14px', border: 'none', background: 'var(--ink)', color: 'var(--paper)', fontFamily: 'var(--serif)', fontSize: 12, cursor: 'pointer' }}>
                Apply 修改
              </button>
            </div>
          )}
        </aside>
      </div>

      {/* ── 底部 log panel ── */}
      <div style={{ borderTop: '1px solid var(--line)', height: 220, display: 'flex', flexDirection: 'column', background: 'rgba(28,24,20,0.025)' }}>
        <div style={{ padding: '8px 14px', borderBottom: '1px solid var(--line-soft)', display: 'flex', alignItems: 'center', gap: 12, flexShrink: 0 }}>
          <div className="mono" style={{ fontSize: 9, color: 'var(--ink-3)', letterSpacing: '0.18em' }}>RUN LOG</div>
          {runId && <code className="mono" style={{ fontSize: 10, color: 'var(--ink-2)' }}>run_id={runId}</code>}
          <span className="mono" style={{ fontSize: 10, color: statusColor(runStatus === 'done' ? 'success' : runStatus === 'error' ? 'failed' : runStatus === 'running' ? 'running' : null) }}>· {runStatus}</span>
          <span style={{ flex: 1 }} />
          {runsList.length > 0 && (
            <select value="" onChange={e => { if (e.target.value) { /* future: re-view past run */ } }}
              style={{ padding: '3px 6px', border: '1px solid var(--line)', fontFamily: 'var(--mono)', fontSize: 10, background: 'var(--paper)' }}>
              <option value="">— 历史 runs ({runsList.length}) —</option>
              {runsList.slice(0, 20).map(r => <option key={r.run_id} value={r.run_id}>{r.run_id}{r.wf_id ? ' · ' + r.wf_id : ''}</option>)}
            </select>
          )}
        </div>
        <div style={{ flex: 1, overflowY: 'auto', padding: '8px 14px', display: 'flex', flexDirection: 'column', gap: 3, fontFamily: 'var(--mono)', fontSize: 11, color: 'var(--ink-1)', minHeight: 0 }}>
          {runEvents.length === 0 && <span className="serif" style={{ color: 'var(--ink-3)', fontSize: 12 }}>未运行 · Save 后点 Run 启动</span>}
          {runEvents.map((evt, i) => {
            if (evt.kind === 'node_start') {
              return <div key={i}>▶ <span style={{ color: 'var(--ink)' }}>{evt.node_id}</span> ({evt.type}) <span style={{ color: 'var(--ink-3)' }}>started · {evt.idx + 1}/{evt.n}</span></div>;
            }
            if (evt.kind === 'node_done') {
              const color = statusColor(evt.status);
              return <div key={i} style={{ color }}>{statusIcon(evt.status)} <span style={{ color: 'var(--ink)' }}>{evt.node_id}</span> {evt.status} · {evt.duration_ms != null ? evt.duration_ms + 'ms' : '—'}{evt.artifact_uri ? ' · artifact' : ''}</div>;
            }
            if (evt.kind === 'workflow_done') {
              return <div key={i} style={{ color: evt.status === 'success' ? 'var(--dai)' : 'var(--yin)', fontWeight: 500, paddingTop: 4 }}>=== workflow_done: {evt.status} · {evt.n_success} success / {evt.n_failed} failed / {evt.n_skipped} skipped ===</div>;
            }
            if (evt.kind === 'error') {
              return <div key={i} style={{ color: 'var(--yin)' }}>✗ error · {evt.message}</div>;
            }
            return null;
          })}
          <div ref={logEndRef} />
        </div>
        {artifactView && (
          <div style={{ borderTop: '1px solid var(--line)', padding: '8px 14px', maxHeight: 180, overflowY: 'auto', background: 'rgba(255,255,255,0.4)' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 6 }}>
              <span className="mono" style={{ fontSize: 10, color: 'var(--ink-3)', letterSpacing: '0.14em' }}>ARTIFACT · {artifactView.node_id}</span>
              {artifactView.kind === 'dataframe' && <span className="mono" style={{ fontSize: 10, color: 'var(--ink-2)' }}>DataFrame {artifactView.shape && artifactView.shape.join('×')}</span>}
              {artifactView.kind === 'json' && <span className="mono" style={{ fontSize: 10, color: 'var(--ink-2)' }}>JSON</span>}
              <span style={{ flex: 1 }} />
              <button onClick={() => setArtifactView(null)} className="hover-pill" style={{ padding: '1px 7px', border: '1px solid var(--line)', background: 'transparent', cursor: 'pointer', fontSize: 10 }}>关</button>
            </div>
            {artifactView.loading && <Loading label="加载 artifact…" />}
            {artifactErr && <ErrorBox error={artifactErr} />}
            {artifactView.kind === 'json' && (
              <pre style={{ margin: 0, fontFamily: 'var(--mono)', fontSize: 11, color: 'var(--ink-1)', whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>{JSON.stringify(artifactView.value, null, 2)}</pre>
            )}
            {artifactView.kind === 'dataframe' && (
              <table style={{ width: '100%', fontSize: 11, borderCollapse: 'collapse', fontFamily: 'var(--mono)' }}>
                <thead><tr style={{ color: 'var(--ink-3)', borderBottom: '1px solid var(--line-soft)' }}>
                  {(artifactView.columns || []).map(c => <td key={c} style={{ padding: '3px 8px', textAlign: 'left' }}>{c}</td>)}
                </tr></thead>
                <tbody>
                  {(artifactView.records || []).slice(0, 100).map((row, ri) => (
                    <tr key={ri} style={{ borderBottom: '1px solid var(--line-soft)' }}>
                      {(artifactView.columns || []).map(c => <td key={c} style={{ padding: '3px 8px' }}>{row[c] === null || row[c] === undefined ? <span style={{ color: 'var(--ink-3)' }}>—</span> : String(row[c])}</td>)}
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
            {artifactView.kind === 'dataframe' && (artifactView.records || []).length > 100 && (
              <div className="mono" style={{ fontSize: 9.5, color: 'var(--ink-3)', marginTop: 4 }}>已截断 · 共 {artifactView.records.length} 行, 展示前 100</div>
            )}
          </div>
        )}
      </div>
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
        {mode === 'workflow' && <WorkflowLab />}
      </div>
    </div>
  );
}

window.QuantApp = QuantApp;
