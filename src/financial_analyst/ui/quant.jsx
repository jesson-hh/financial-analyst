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

const POOLS = ['csi300', 'csi500', 'csi800', 'all'];
const POOL_DEFAULT = 'csi300_active';   // csi300 交互快档
const poolParam = (p) => (p === 'csi300' ? POOL_DEFAULT : p);

// ═════════════════════════ 三态小组件 ═════════════════════════
function Loading({ label = '加载中…' }) {
  return <div className="mono" style={{ padding: 24, fontSize: 12, color: 'var(--ink-3)', textAlign: 'center' }}>⏳ {label}</div>;
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
  const [pool, setPool] = useState('csi300');
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
  const [pool, setPool] = useState('csi300');
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
  const [pool, setPool] = useState('csi300');
  const [trainFrac, setTrainFrac] = useState(0.6);
  const comp = useAsync();
  useEffect(() => { getJSON('/factor/list').then(d => setList(d || { registered: [], user: [] })).catch(() => {}); }, []);
  const allNames = [...new Set([...(list.registered || []).map(r => r.name), ...(list.user || []).map(u => u.name)])];
  const addMember = (m) => { if (m && !members.includes(m)) setMembers([...members, m]); };
  const run = () => { if (members.length < 2) return; comp.run(() => postJSON('/factor/compose', { members, method, universe: poolParam(pool), train_frac: trainFrac })); };
  const res = comp.data;
  return (
    <div style={{ flex: 1, overflowY: 'auto', padding: 18, minWidth: 0 }}>
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
      </div>
    </div>
  );
}

window.QuantApp = QuantApp;
